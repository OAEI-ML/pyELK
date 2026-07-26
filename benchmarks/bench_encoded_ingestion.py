"""Compare encoded-native and scalar-wire ingestion from one resident ontology view.

The benchmark starts after ontology loading.  It times encoded-view acquisition, native
compilation/session creation, the first taxonomy result, and warm boundary calls separately.
Every sample is paired with the scalar compiler/private-wire path and must produce the exact
same compiler digest, section counts, and packed taxonomy bytes before timing is reported.

Released capability negotiation is required for gate-eligible evidence.  During development,
``--experimental-producer`` exercises pyowl-core's complete scalar fallback producer while
keeping the report explicitly ineligible for release claims.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import importlib
import importlib.util
import json
import math
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from functools import cache
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar

import pyowl_core as owl
from pyowl_core.backends import native_views

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.encoded import (
    ENCODED_SCHEMA_NAME,
    ENCODED_SCHEMA_VERSION,
    negotiate_encoded_structural_view,
)

T = TypeVar("T")
_ROOT_AXIOM = 2
_HIERARCHY_COMPONENT_CLASSES = 8
_DARWIN_PROC_PIDTASKINFO = 4


class _DarwinProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


@dataclass(frozen=True, slots=True)
class PhaseSample:
    """One raw wall/RSS observation; RSS values are never presented as phase peaks."""

    wall_seconds: float
    current_rss_before_bytes: int | None
    current_rss_after_bytes: int | None
    current_rss_growth_bytes: int | None
    process_peak_rss_before_bytes: int | None
    process_peak_rss_after_bytes: int | None
    process_peak_rss_high_water_growth_bytes: int | None


@dataclass(frozen=True, slots=True)
class PhaseSummary:
    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float
    maximum_seconds: float
    maximum_current_rss_growth_bytes: int | None
    maximum_process_peak_rss_high_water_growth_bytes: int | None


@dataclass(frozen=True, slots=True)
class _SampleResult:
    phases: Mapping[str, PhaseSample]
    compiler_digest: str
    compiler_counts: Mapping[str, int]
    result_sha256: str
    diagnostics: Mapping[str, int | float | str | bool]
    counters: Mapping[str, int | str | bool | None]


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


@cache
def _darwin_proc_pidinfo() -> Any | None:
    if platform.system() != "Darwin":
        return None
    try:
        function = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_pidinfo
    except (AttributeError, OSError):
        return None
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    )
    function.restype = ctypes.c_int
    return function


def _current_rss_bytes() -> int | None:
    if platform.system() == "Darwin":
        proc_pidinfo = _darwin_proc_pidinfo()
        if proc_pidinfo is None:
            return None
        task = _DarwinProcTaskInfo()
        task_size = ctypes.sizeof(task)
        received = proc_pidinfo(
            os.getpid(),
            _DARWIN_PROC_PIDTASKINFO,
            0,
            ctypes.byref(task),
            task_size,
        )
        return int(task.pti_resident_size) if received == task_size else None

    statm = Path("/proc/self/statm")
    if not statm.is_file():
        return None
    try:
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (IndexError, OSError, TypeError, ValueError):
        return None
    return resident_pages * page_size


def _observe(operation: Callable[[], T]) -> tuple[T, PhaseSample]:
    current_before = _current_rss_bytes()
    peak_before = _peak_rss_bytes()
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    current_after = _current_rss_bytes()
    peak_after = _peak_rss_bytes()
    return result, PhaseSample(
        wall_seconds=elapsed,
        current_rss_before_bytes=current_before,
        current_rss_after_bytes=current_after,
        current_rss_growth_bytes=(
            None
            if current_before is None or current_after is None
            else max(0, current_after - current_before)
        ),
        process_peak_rss_before_bytes=peak_before,
        process_peak_rss_after_bytes=peak_after,
        process_peak_rss_high_water_growth_bytes=(
            None if peak_before is None or peak_after is None else max(0, peak_after - peak_before)
        ),
    )


def _summary(samples: list[PhaseSample]) -> PhaseSummary:
    if not samples:
        raise ValueError("at least one measured phase is required")
    values = [sample.wall_seconds for sample in samples]
    median = statistics.median(values)
    current_rss = [
        value for sample in samples if (value := sample.current_rss_growth_bytes) is not None
    ]
    peak_rss = [
        value
        for sample in samples
        if (value := sample.process_peak_rss_high_water_growth_bytes) is not None
    ]
    return PhaseSummary(
        repeats=len(samples),
        median_seconds=median,
        median_absolute_deviation_seconds=statistics.median(
            abs(value - median) for value in values
        ),
        minimum_seconds=min(values),
        maximum_seconds=max(values),
        maximum_current_rss_growth_bytes=max(current_rss) if current_rss else None,
        maximum_process_peak_rss_high_water_growth_bytes=max(peak_rss) if peak_rss else None,
    )


def _load_native(path: Path | None) -> ModuleType:
    if path is None:
        root = Path(__file__).parents[1]
        candidates = (
            root / "target" / "release" / "lib_native.dylib",
            root / "target" / "release" / "lib_native.so",
            root / "target" / "debug" / "lib_native.dylib",
            root / "target" / "debug" / "lib_native.so",
        )
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            try:
                return importlib.import_module("pyelk._native")
            except ImportError:
                pass
    if path is None or not path.is_file():
        raise RuntimeError("pyelk._native is not installed and no workspace library was found")
    destination = Path(tempfile.mkdtemp(prefix="pyelk-encoded-benchmark-")) / "_native.so"
    shutil.copy2(path, destination)
    spec = importlib.util.spec_from_file_location("pyelk._native", destination)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import native library {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    return module


def _axioms(classes: tuple[owl.Class, ...]) -> Iterator[owl.AxiomNode]:
    for entity in classes:
        yield owl.Declaration(entity)
    for start in range(0, len(classes), _HIERARCHY_COMPONENT_CLASSES):
        component = classes[start : start + _HIERARCHY_COMPONENT_CLASSES]
        for subclass, superclass in pairwise(component):
            yield owl.SubClassOf(subclass, superclass)


def _hierarchy_snapshot(namespace: str, class_count: int) -> owl.OntologySnapshot:
    document_iri = owl.IRI(namespace)
    classes = tuple(owl.Class(owl.IRI(f"{namespace}#C{index}")) for index in range(class_count))
    axioms: owl.CanonicalSet[owl.AxiomNode] = owl.CanonicalSet(_axioms(classes))
    descriptor = (
        f"pyelk:encoded-ingestion:v2:{namespace}:classes={class_count}:"
        f"component-classes={_HIERARCHY_COMPONENT_CLASSES}"
    ).encode()
    source_digest = hashlib.sha256(descriptor).digest()
    provenance = owl.DocumentProvenance(
        source_sha256=source_digest,
        digest_kind=owl.DigestKind.EXACT_BYTES,
        byte_length=0,
        decoded_codepoint_length=0,
        document_iri=document_iri,
        acquisition_locator=None,
        format=owl.DocumentFormat.FUNCTIONAL,
        detection_basis=owl.DetectionBasis.EXPLICIT,
        parser="benchmarks.programmatic",
        backend="python",
    )
    document = owl.OntologyDocument(
        ontology_id=owl.OntologyID(document_iri),
        document_iri=document_iri,
        direct_imports=(),
        ontology_annotations=owl.CanonicalSet(),
        axioms=axioms,
        extension_components=owl.CanonicalSet(),
        provenance=provenance,
        origin_index=owl.OriginIndex(),
    )
    document_key = "benchmark:" + source_digest.hex()
    record = owl.DocumentRecord(
        document_key=document_key,
        ontology_id=document.ontology_id,
        document_iri=document_iri,
        source_sha256=source_digest,
        document_fingerprint=document.document_fingerprint,
        format=owl.DocumentFormat.FUNCTIONAL,
        status=owl.DocumentStatus.ROOT,
    )
    manifest = owl.ImportManifest(
        policy=owl.ImportPolicy.IGNORE,
        offline=True,
        resolver_configuration_fingerprint=hashlib.sha256(
            b"pyelk-encoded-ingestion:no-resolver:v1"
        ).digest(),
        documents=(record,),
        edges=(),
    )
    options = owl.LoadOptions(
        imports=owl.ImportPolicy.IGNORE,
        backend=owl.BackendPreference.PYTHON,
        offline=True,
        collect_provenance=False,
    )
    return owl.OntologySnapshot(
        root=document,
        documents=(document,),
        import_manifest=manifest,
        root_document_key=document_key,
        load_options=options,
    )


def _workloads(
    class_count: int,
    *,
    mapped_path: Path | None = None,
) -> dict[str, owl.OntologyView]:
    direct = _hierarchy_snapshot("urn:pyelk:encoded:direct", class_count)
    extra = owl.Class(owl.IRI("urn:pyelk:encoded:direct#Extra"))
    first = owl.Class(owl.IRI("urn:pyelk:encoded:direct#C0"))
    overlay = owl.apply_delta(
        direct,
        owl.OntologyDelta(
            add_axioms=owl.CanonicalSet((owl.Declaration(extra), owl.SubClassOf(extra, first)))
        ),
    )
    member_count = max(2, class_count // 2)
    source_base = _hierarchy_snapshot("urn:pyelk:encoded:source", member_count)
    removed_source_edge = owl.SubClassOf(
        owl.Class(owl.IRI("urn:pyelk:encoded:source#C0")),
        owl.Class(owl.IRI("urn:pyelk:encoded:source#C1")),
    )
    source = owl.apply_delta(
        source_base,
        owl.OntologyDelta(remove_axioms=owl.CanonicalSet((removed_source_edge,))),
    )
    target = _hierarchy_snapshot("urn:pyelk:encoded:target", member_count)
    bridge = owl.SubClassOf(
        owl.Class(owl.IRI(f"urn:pyelk:encoded:source#C{member_count - 1}")),
        owl.Class(owl.IRI("urn:pyelk:encoded:target#C0")),
    )
    composite = owl.compose_views(
        source,
        target,
        delta=owl.OntologyDelta(add_axioms=owl.CanonicalSet((bridge,))),
        roles=("source", "target"),
    )
    workloads: dict[str, owl.OntologyView] = {"direct": direct}
    if mapped_path is not None:
        mapped_seed = _hierarchy_snapshot("urn:pyelk:encoded:mmap", class_count)
        mapped_path.write_bytes(owl.encode_snapshot(mapped_seed))
        workloads["mmap"] = owl.open_snapshot(mapped_path, mmap=True, verify=True)
    workloads.update({"overlay": overlay, "composite": composite})
    return workloads


def _diagnostics(session: Any) -> dict[str, int | float | str | bool]:
    value = session.diagnostics()
    if not isinstance(value, Mapping):
        raise AssertionError("native diagnostics are not a mapping")
    result: dict[str, int | float | str | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, (bool, int, float, str)):
            raise AssertionError("native diagnostics are not string-to-scalar")
        result[key] = item
    return dict(sorted(result.items()))


def _compiler_counts(diagnostics: Mapping[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in diagnostics.items():
        if key.startswith("compiler_") and key.endswith("_count"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise AssertionError(f"native compiler count {key!r} is not an integer")
            result[key] = value
    if not result:
        raise AssertionError("native diagnostics contain no compiler section counts")
    return result


def _warm_result(session: Any, expected: bytes, count: int) -> bytes:
    result = expected
    for _ in range(count):
        result = session.class_taxonomy()
        if result != expected:
            raise AssertionError("warm native taxonomy bytes changed")
    return result


def _total_sample(
    started: float, current_before: int | None, peak_before: int | None
) -> PhaseSample:
    current_after = _current_rss_bytes()
    peak_after = _peak_rss_bytes()
    return PhaseSample(
        wall_seconds=time.perf_counter() - started,
        current_rss_before_bytes=current_before,
        current_rss_after_bytes=current_after,
        current_rss_growth_bytes=(
            None
            if current_before is None or current_after is None
            else max(0, current_after - current_before)
        ),
        process_peak_rss_before_bytes=peak_before,
        process_peak_rss_after_bytes=peak_after,
        process_peak_rss_high_water_growth_bytes=(
            None if peak_before is None or peak_after is None else max(0, peak_after - peak_before)
        ),
    )


def _duration_sample(seconds: float, name: str) -> PhaseSample:
    if not math.isfinite(seconds) or seconds < 0:
        raise AssertionError(f"native {name} duration is not finite and nonnegative")
    return PhaseSample(
        wall_seconds=seconds,
        current_rss_before_bytes=None,
        current_rss_after_bytes=None,
        current_rss_growth_bytes=None,
        process_peak_rss_before_bytes=None,
        process_peak_rss_after_bytes=None,
        process_peak_rss_high_water_growth_bytes=None,
    )


def _scalar_sample(
    native: ModuleType, view: owl.OntologyView, workers: int, warm_queries: int
) -> _SampleResult:
    gc.collect()
    current_before = _current_rss_bytes()
    peak_before = _peak_rss_bytes()
    started = time.perf_counter()
    compiled, compile_phase = _observe(lambda: compile_ontology(view, unsupported="error"))
    wire, wire_phase = _observe(compiled.encode)
    session, session_phase = _observe(lambda: native.create_session(wire, workers))
    try:
        payload, first_phase = _observe(session.class_taxonomy)
        total_phase = _total_sample(started, current_before, peak_before)
        diagnostics = _diagnostics(session)
        _last, warm_phase = _observe(lambda: _warm_result(session, payload, warm_queries))
        phases = {
            "scalar_compile": compile_phase,
            "private_wire_encode": wire_phase,
            "session_create": session_phase,
            "first_result": first_phase,
            "warm_queries": warm_phase,
            "view_to_first_result": total_phase,
        }
        return _SampleResult(
            phases=phases,
            compiler_digest=str(diagnostics["compiler_digest"]),
            compiler_counts=_compiler_counts(diagnostics),
            result_sha256=hashlib.sha256(payload).hexdigest(),
            diagnostics=diagnostics,
            counters={
                "parser_calls": 0,
                "resolver_calls": 0,
                "wire_encoder_calls": 1,
                "wire_decoder_calls": 1,
                "serialized_private_ir_bytes": len(wire),
                "per_axiom_ffi_calls": 0,
            },
        )
    finally:
        session.close()


def _acquire_encoded(view: owl.OntologyView, *, experimental_producer: bool) -> tuple[Any, str]:
    advertised = view.capabilities.encoded_view_schemas.get(ENCODED_SCHEMA_NAME)
    if advertised is not None and advertised >= ENCODED_SCHEMA_VERSION:
        negotiated = negotiate_encoded_structural_view(view)
        if negotiated.handoff is None:  # pragma: no cover - guarded by advertised capability
            raise AssertionError("advertised encoded view was not negotiated")
        return negotiated.handoff.encoded_view, "public-negotiated"
    if not experimental_producer:
        raise RuntimeError(
            "pyowl-core does not advertise structural columns; use --experimental-producer "
            "for non-gating fallback smoke evidence"
        )
    return native_views.produce_encoded_structural_view_v1(view), "experimental-scalar-fallback"


def _encoded_sample(
    native: ModuleType,
    view: owl.OntologyView,
    workers: int,
    warm_queries: int,
    *,
    experimental_producer: bool,
) -> tuple[_SampleResult, str]:
    gc.collect()
    current_before = _current_rss_bytes()
    peak_before = _peak_rss_bytes()
    started = time.perf_counter()
    acquired, acquisition_phase = _observe(
        lambda: _acquire_encoded(view, experimental_producer=experimental_producer)
    )
    encoded, producer = acquired
    session, session_phase = _observe(
        lambda: native.create_session_from_encoded(encoded, workers, "error")
    )
    try:
        payload, first_phase = _observe(session.class_taxonomy)
        total_phase = _total_sample(started, current_before, peak_before)
        diagnostics = _diagnostics(session)
        _last, warm_phase = _observe(lambda: _warm_result(session, payload, warm_queries))
        validation_seconds = float(diagnostics["encoded_validation_seconds"])
        compiler_seconds = float(diagnostics["encoded_compiler_seconds"])
        session_build_seconds = float(diagnostics["encoded_session_build_seconds"])
        native_boundary_seconds = float(diagnostics["encoded_native_boundary_seconds"])
        ffi_overhead_seconds = max(0.0, session_phase.wall_seconds - native_boundary_seconds)
        boundary_and_validation_seconds = (
            acquisition_phase.wall_seconds + validation_seconds + ffi_overhead_seconds
        )
        root_kinds = encoded.buffers["root_kinds"]
        scalar_roots = sum(int(value == _ROOT_AXIOM) for value in root_kinds)
        scalar_materializations = scalar_roots if producer.startswith("experimental") else 0
        segmented_owner = hasattr(view, "base") or hasattr(view, "members")
        base_flattening_bytes = (
            int(diagnostics["encoded_buffer_bytes"])
            if producer.startswith("experimental") and segmented_owner
            else 0
        )
        counters: dict[str, int | str | bool | None] = {
            "parser_calls": 0,
            "resolver_calls": 0,
            "wire_encoder_calls": 0,
            "wire_decoder_calls": 0,
            "serialized_private_ir_bytes": int(diagnostics["encoded_private_ir_bytes"]),
            "staging_copy_bytes": int(diagnostics["encoded_staging_copy_bytes"]),
            "scalar_axiom_materializations": scalar_materializations,
            "scalar_materialization_counter_source": (
                "fallback root traversal"
                if scalar_materializations
                else "advertised producer contract"
            ),
            "per_axiom_ffi_calls": 0,
            "coarse_buffer_calls": int(diagnostics["encoded_buffer_count"]),
            "base_flattening_bytes": base_flattening_bytes,
        }
        phases = {
            "view_acquisition_and_validation": acquisition_phase,
            "session_create": session_phase,
            "native_validation": _duration_sample(validation_seconds, "validation"),
            "native_compiler": _duration_sample(compiler_seconds, "compiler"),
            "native_session_build": _duration_sample(session_build_seconds, "session build"),
            "native_boundary": _duration_sample(native_boundary_seconds, "boundary"),
            "boundary_and_validation": _duration_sample(
                boundary_and_validation_seconds,
                "boundary and validation",
            ),
            "first_result": first_phase,
            "warm_queries": warm_phase,
            "view_to_first_result": total_phase,
        }
        return (
            _SampleResult(
                phases=phases,
                compiler_digest=str(diagnostics["compiler_digest"]),
                compiler_counts=_compiler_counts(diagnostics),
                result_sha256=hashlib.sha256(payload).hexdigest(),
                diagnostics=diagnostics,
                counters=counters,
            ),
            producer,
        )
    finally:
        session.close()


def _report_samples(samples: list[_SampleResult]) -> dict[str, object]:
    phase_names = tuple(samples[0].phases)
    return {
        "phases": {
            name: {
                "summary": asdict(_summary([sample.phases[name] for sample in samples])),
                "samples": [asdict(sample.phases[name]) for sample in samples],
            }
            for name in phase_names
        },
        "compiler_digest": samples[0].compiler_digest,
        "compiler_counts": dict(samples[0].compiler_counts),
        "result_sha256": samples[0].result_sha256,
        "diagnostics": dict(samples[0].diagnostics),
        "counters": dict(samples[0].counters),
    }


def _median(samples: list[_SampleResult], phase: str) -> float:
    return statistics.median(sample.phases[phase].wall_seconds for sample in samples)


def _rss_growth(samples: list[_SampleResult]) -> int | None:
    values = [
        value
        for sample in samples
        if (value := sample.phases["view_to_first_result"].current_rss_growth_bytes) is not None
    ]
    return max(values) if values else None


def _require_stable(samples: list[_SampleResult], label: str) -> None:
    first = samples[0]
    for sample in samples[1:]:
        if (
            sample.compiler_digest != first.compiler_digest
            or sample.compiler_counts != first.compiler_counts
            or sample.result_sha256 != first.result_sha256
        ):
            raise AssertionError(f"{label} result changed between benchmark samples")


def _workload_gate_blockers(
    name: str,
    *,
    scalar_total: float,
    encoded_total: float,
    boundary_fraction: float,
    rss_ratio: float | None,
) -> list[str]:
    blockers: list[str] = []
    if encoded_total > scalar_total * 1.10:
        blockers.append(f"{name}: encoded path is more than 10% slower")
    if boundary_fraction >= 0.05:
        blockers.append(f"{name}: encoded boundary plus validation is not below 5%")
    if rss_ratio is None:
        blockers.append(f"{name}: comparable current-RSS growth is unavailable")
    elif rss_ratio > 1.10:
        blockers.append(f"{name}: encoded current-RSS growth regresses by more than 10%")
    return blockers


def run(
    *,
    class_count: int,
    repeats: int,
    warmups: int,
    warm_queries: int,
    workers: int,
    native_path: Path | None,
    experimental_producer: bool,
    enforce: bool,
) -> dict[str, object]:
    """Run exact paired comparisons and return raw, machine-readable evidence."""

    if class_count < 4:
        raise ValueError("class_count must be at least four")
    if repeats < 1 or warmups < 0 or warm_queries < 1 or workers < 1:
        raise ValueError("repeats/warm_queries/workers must be positive and warmups nonnegative")
    if enforce and experimental_producer:
        raise ValueError("enforcement forbids the experimental scalar fallback producer")

    native = _load_native(native_path)
    native_schemas = native.encoded_view_schemas()
    if not isinstance(native_schemas, Mapping):
        raise AssertionError("native encoded-view capability is not a mapping")
    workspace = tempfile.TemporaryDirectory(prefix="pyelk-encoded-workloads-")
    workloads, setup_phase = _observe(
        lambda: _workloads(
            class_count,
            mapped_path=Path(workspace.name) / "mapped.pyocore",
        )
    )
    for view in workloads.values():
        _ = (view.structural_fingerprint, view.logical_fingerprint, view.signature_fingerprint)

    reports: dict[str, object] = {}
    speedups: list[float] = []
    boundary_fractions: list[float] = []
    blockers: list[str] = []
    producers: set[str] = set()
    core_advertised: dict[str, int | None] = {}
    scalar_materialization_observed = False
    for name, view in workloads.items():
        core_advertised[name] = view.capabilities.encoded_view_schemas.get(ENCODED_SCHEMA_NAME)
        for _ in range(warmups):
            scalar_warm = _scalar_sample(native, view, workers, warm_queries)
            encoded_warm, producer = _encoded_sample(
                native,
                view,
                workers,
                warm_queries,
                experimental_producer=experimental_producer,
            )
            producers.add(producer)
            if (
                scalar_warm.compiler_digest != encoded_warm.compiler_digest
                or scalar_warm.compiler_counts != encoded_warm.compiler_counts
                or scalar_warm.result_sha256 != encoded_warm.result_sha256
            ):
                raise AssertionError(f"scalar/encoded warm-up parity failed for {name}")

        scalar_samples: list[_SampleResult] = []
        encoded_samples: list[_SampleResult] = []
        for index in range(repeats):
            if index % 2:
                encoded, producer = _encoded_sample(
                    native,
                    view,
                    workers,
                    warm_queries,
                    experimental_producer=experimental_producer,
                )
                scalar = _scalar_sample(native, view, workers, warm_queries)
            else:
                scalar = _scalar_sample(native, view, workers, warm_queries)
                encoded, producer = _encoded_sample(
                    native,
                    view,
                    workers,
                    warm_queries,
                    experimental_producer=experimental_producer,
                )
            producers.add(producer)
            scalar_samples.append(scalar)
            encoded_samples.append(encoded)

        _require_stable(scalar_samples, f"scalar {name}")
        _require_stable(encoded_samples, f"encoded {name}")
        scalar_first = scalar_samples[0]
        encoded_first = encoded_samples[0]
        if (
            scalar_first.compiler_digest != encoded_first.compiler_digest
            or scalar_first.compiler_counts != encoded_first.compiler_counts
            or scalar_first.result_sha256 != encoded_first.result_sha256
        ):
            raise AssertionError(f"scalar/encoded parity failed for {name}")
        if any(
            sample.counters["serialized_private_ir_bytes"] != 0
            or sample.counters["per_axiom_ffi_calls"] != 0
            for sample in encoded_samples
        ):
            raise AssertionError(f"encoded boundary counters are nonzero for {name}")
        for sample in encoded_samples:
            staging_copy_bytes = sample.counters["staging_copy_bytes"]
            posting_bytes = sample.diagnostics["encoded_posting_bytes"]
            if name == "composite":
                if staging_copy_bytes != posting_bytes or posting_bytes == 0:
                    raise AssertionError(
                        "composite staging must contain exactly the selected-root postings"
                    )
            elif staging_copy_bytes != 0:
                raise AssertionError(f"{name} staged encoded metadata or columns")
        if any(sample.counters["base_flattening_bytes"] != 0 for sample in encoded_samples):
            blockers.append(f"{name}: encoded producer flattened base structures")
        encoded_diagnostics = encoded_samples[0].diagnostics
        if name in {"direct", "mmap"}:
            if (
                encoded_diagnostics["encoded_segment_count"] != 1
                or encoded_diagnostics["encoded_referenced_view_count"] != 0
            ):
                blockers.append(f"{name}: encoded segment manifest is not direct")
        elif (
            int(encoded_diagnostics["encoded_segment_count"]) <= 1
            or int(encoded_diagnostics["encoded_referenced_view_count"]) < 1
        ):
            blockers.append(f"{name}: encoded producer did not retain referenced base segments")
        scalar_materialization_observed |= any(
            sample.counters["scalar_axiom_materializations"] != 0 for sample in encoded_samples
        )

        scalar_total = _median(scalar_samples, "view_to_first_result")
        encoded_total = _median(encoded_samples, "view_to_first_result")
        boundary_and_validation = _median(encoded_samples, "boundary_and_validation")
        speedup = scalar_total / encoded_total
        boundary_fraction = boundary_and_validation / encoded_total
        speedups.append(speedup)
        boundary_fractions.append(boundary_fraction)
        scalar_rss = _rss_growth(scalar_samples)
        encoded_rss = _rss_growth(encoded_samples)
        rss_ratio = (
            None
            if scalar_rss is None or encoded_rss is None or scalar_rss == 0
            else encoded_rss / scalar_rss
        )
        reports[name] = {
            "parity": {
                "compiler_digest": True,
                "compiler_counts": True,
                "packed_first_result": True,
            },
            "scalar_wire": _report_samples(scalar_samples),
            "encoded_native": _report_samples(encoded_samples),
            "ratios": {
                "view_to_first_result_speedup": speedup,
                "encoded_boundary_and_validation_fraction": boundary_fraction,
                "incremental_current_rss_ratio": rss_ratio,
            },
        }
        blockers.extend(
            _workload_gate_blockers(
                name,
                scalar_total=scalar_total,
                encoded_total=encoded_total,
                boundary_fraction=boundary_fraction,
                rss_ratio=rss_ratio,
            )
        )

    native_schema = native_schemas.get(ENCODED_SCHEMA_NAME)
    if native_schema is None or native_schema < ENCODED_SCHEMA_VERSION:
        blockers.append("native extension does not advertise structural-columns v1")
    unavailable_core = [
        name
        for name, version in core_advertised.items()
        if version is None or version < ENCODED_SCHEMA_VERSION
    ]
    if unavailable_core:
        blockers.append(
            "core does not advertise structural-columns v1 for: " + ", ".join(unavailable_core)
        )
    if producers != {"public-negotiated"}:
        blockers.append("benchmark used the experimental scalar fallback producer")
    if scalar_materialization_observed:
        blockers.append("encoded acquisition materialized scalar axioms")

    geometric_speedup = math.prod(speedups) ** (1 / len(speedups))
    if geometric_speedup < 2.0:
        blockers.append(f"geometric-mean encoded speedup {geometric_speedup:.3f}x is below 2x")
    gate_eligible = not blockers
    if enforce and not gate_eligible:
        raise AssertionError("encoded-ingestion release gate failed: " + "; ".join(blockers))

    payload = {
        "schema": "pyelk.encoded-ingestion-benchmark/2",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "logical_cpu_count": os.cpu_count(),
            "workers": workers,
            "pyowl_core": owl.__version__,
            "native_implementation": native.implementation_version(),
        },
        "protocol": {
            "starting_boundary": "resident OntologyView after fingerprint warm-up",
            "warmups": warmups,
            "repeats": repeats,
            "warm_queries_per_session": warm_queries,
            "exact_packed_result_parity_required": True,
            "rss_observation": (
                "Linux /proc or macOS libproc current RSS plus process-lifetime ru_maxrss "
                "high-water growth; raw values are retained and order is alternated between "
                "paired paths"
            ),
            "thresholds": {
                "geometric_mean_speedup_min": 2.0,
                "per_workload_regression_max": 0.10,
                "encoded_boundary_and_validation_fraction_max": 0.05,
                "incremental_rss_regression_max": 0.10,
            },
        },
        "fixture": {
            "class_count": class_count,
            "hierarchy_component_classes": _HIERARCHY_COMPONENT_CLASSES,
            "setup": asdict(setup_phase),
            "workloads": tuple(workloads),
        },
        "capabilities": {
            "schema_name": ENCODED_SCHEMA_NAME,
            "required_schema_version": ENCODED_SCHEMA_VERSION,
            "native_advertised_schema": native_schema,
            "core_advertised_schemas": core_advertised,
            "producer_modes": sorted(producers),
        },
        "workloads": reports,
        "aggregate": {
            "geometric_mean_view_to_first_result_speedup": geometric_speedup,
            "maximum_encoded_boundary_and_validation_fraction": max(boundary_fractions),
        },
        "gate_eligible": gate_eligible,
        "gate_blockers": blockers,
    }
    mapped_view = workloads.pop("mmap")
    gc.collect()
    close_mapped = getattr(mapped_view, "close", None)
    if not callable(close_mapped):
        raise AssertionError("mmap workload does not expose a close operation")
    close_mapped()
    workspace.cleanup()
    return payload


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classes", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--warm-queries", type=int, default=10)
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--native-path", type=Path)
    parser.add_argument(
        "--experimental-producer",
        action="store_true",
        help="use pyowl-core's scalar fallback producer and mark evidence non-gating",
    )
    parser.add_argument("--enforce", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    report = run(
        class_count=arguments.classes,
        repeats=arguments.repeats,
        warmups=arguments.warmups,
        warm_queries=arguments.warm_queries,
        workers=arguments.workers,
        native_path=arguments.native_path,
        experimental_producer=arguments.experimental_producer,
        enforce=arguments.enforce,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
