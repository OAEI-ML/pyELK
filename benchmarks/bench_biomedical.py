#!/usr/bin/env python3
"""Benchmark an external biomedical source/target/alignment without redistributing it.

The runner verifies every caller-supplied hash before parsing, loads each ontology exactly
once, turns the tab-separated reference alignment into an ``EquivalentClasses`` bridge, and
then reasons over the two snapshots and their zero-copy composite through the public facade.
It never serializes a resident view or reaches into private reasoner/session state.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType
from typing import Literal, TypeAlias, TypeVar, cast

import pyowl_core as owl

from pyelk import Reasoner, ReasonerConfig
from pyelk.result import ReasoningResult, Taxonomy

T = TypeVar("T")
BackendName: TypeAlias = Literal["python", "rust"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PhaseSample:
    wall_seconds: float
    peak_traced_bytes: int | None
    process_peak_rss_before_bytes: int | None
    process_peak_rss_after_bytes: int | None
    process_peak_rss_increase_bytes: int | None


@dataclass(frozen=True, slots=True)
class PhaseSummary:
    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float
    maximum_seconds: float
    maximum_peak_traced_bytes: int | None
    maximum_process_peak_rss_increase_bytes: int | None


class _CountingProvider:
    __slots__ = ("calls", "view")

    def __init__(self, view: owl.OntologyView) -> None:
        self.calls = 0
        self.view = view

    def owl_snapshot(self) -> owl.OntologyView:
        self.calls += 1
        return self.view


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _verified_file(path: Path, expected_sha256: str, label: str) -> tuple[Path, int]:
    if not isinstance(path, Path):
        raise TypeError(f"{label} path must be pathlib.Path")
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise ValueError(f"{label} SHA-256 must be 64 lowercase hexadecimal characters")
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} is not a file: {path}")
    actual, size = _sha256(resolved)
    if actual != expected_sha256:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected_sha256}, observed {actual}")
    return resolved, size


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _observe(callback: Callable[[], T], *, trace_allocations: bool) -> tuple[T, PhaseSample]:
    gc.collect()
    rss_before = _peak_rss_bytes()
    if trace_allocations:
        if tracemalloc.is_tracing():
            raise RuntimeError("allocation tracing is already active")
        tracemalloc.start()
    started = time.perf_counter()
    try:
        result = callback()
        wall_seconds = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] if trace_allocations else None
    finally:
        if trace_allocations:
            tracemalloc.stop()
    rss_after = _peak_rss_bytes()
    rss_increment = (
        None if rss_before is None or rss_after is None else max(0, rss_after - rss_before)
    )
    return result, PhaseSample(
        wall_seconds=wall_seconds,
        peak_traced_bytes=peak,
        process_peak_rss_before_bytes=rss_before,
        process_peak_rss_after_bytes=rss_after,
        process_peak_rss_increase_bytes=rss_increment,
    )


def _summarize(samples: list[PhaseSample]) -> PhaseSummary:
    if not samples:
        raise ValueError("at least one measured sample is required")
    values = [sample.wall_seconds for sample in samples]
    median = statistics.median(values)
    rss_values = [
        sample.process_peak_rss_increase_bytes
        for sample in samples
        if sample.process_peak_rss_increase_bytes is not None
    ]
    allocation_values = [
        sample.peak_traced_bytes for sample in samples if sample.peak_traced_bytes is not None
    ]
    return PhaseSummary(
        repeats=len(samples),
        median_seconds=median,
        median_absolute_deviation_seconds=statistics.median(
            abs(value - median) for value in values
        ),
        minimum_seconds=min(values),
        maximum_seconds=max(values),
        maximum_peak_traced_bytes=max(allocation_values) if allocation_values else None,
        maximum_process_peak_rss_increase_bytes=max(rss_values) if rss_values else None,
    )


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _require_count(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")


def _view_counts(view: owl.OntologyView) -> dict[str, int]:
    return {
        "axioms": sum(1 for _ in view.iter_axioms()),
        "entities_excluding_builtins": len(view.signature(include_builtins=False)),
    }


def _require_self_contained(snapshot: owl.OntologySnapshot, label: str) -> None:
    if snapshot.root.direct_imports or len(snapshot.documents) != 1:
        raise ValueError(
            f"{label} has imports; every parsed byte must be hash-pinned, so this runner "
            "accepts self-contained ontology documents only"
        )
    if not snapshot.is_complete:
        raise ValueError(f"{label} does not have a complete ontology view")


def _verify_counts(
    observed: dict[str, int], *, expected_axioms: int, expected_entities: int, label: str
) -> None:
    expected = {
        "axioms": expected_axioms,
        "entities_excluding_builtins": expected_entities,
    }
    if observed != expected:
        raise ValueError(f"{label} count mismatch: expected {expected}, observed {observed}")


def _bridge_axioms(path: Path) -> owl.CanonicalSet[owl.EquivalentClasses]:
    axioms: list[owl.EquivalentClasses] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        required = {"SrcEntity", "TgtEntity"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("alignment must contain SrcEntity and TgtEntity TSV columns")
        for row_number, row in enumerate(reader, start=2):
            source = (row.get("SrcEntity") or "").strip()
            target = (row.get("TgtEntity") or "").strip()
            if not source or not target:
                raise ValueError(f"alignment row {row_number} has an empty entity IRI")
            axioms.append(
                owl.EquivalentClasses(
                    owl.CanonicalSet(
                        (
                            owl.Class(owl.IRI(source)),
                            owl.Class(owl.IRI(target)),
                        )
                    )
                )
            )
    if not axioms:
        raise ValueError("alignment must contain at least one correspondence")
    return owl.CanonicalSet(axioms)


def _node_row(node: object) -> tuple[str, ...]:
    members = getattr(node, "members", None)
    if not isinstance(members, tuple):
        raise TypeError("taxonomy node members must be a tuple")
    return tuple(sorted(member.iri.value for member in members))


def _taxonomy_observation(
    result: ReasoningResult[Taxonomy[owl.Class]],
) -> dict[str, object]:
    taxonomy = result.value
    nodes = sorted(_node_row(node) for node in taxonomy.nodes)
    edges = sorted((_node_row(sub), _node_row(sup)) for sub, sup in taxonomy.direct_edges)
    top = _node_row(taxonomy.top)
    bottom = _node_row(taxonomy.bottom)
    reasons = [
        {
            "constructors": list(issue.constructors),
            "features": list(issue.features),
            "polarities": list(issue.polarities),
            "task": issue.task.value,
        }
        for issue in result.reasons
    ]
    canonical = json.dumps(
        {
            "complete": result.complete,
            "bottom": bottom,
            "edges": edges,
            "nodes": nodes,
            "reasons": reasons,
            "top": top,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "semantic_completeness_sha256": hashlib.sha256(canonical).hexdigest(),
        "complete": result.complete,
        "completeness_reason_count": len(reasons),
        "node_count": len(nodes),
        "direct_edge_count": len(edges),
        "top": list(top),
        "bottom": list(bottom),
    }


def _run_backend(
    *,
    backend: BackendName,
    views: dict[str, owl.OntologyView],
    workers: int,
    warmups: int,
    repeats: int,
    trace_allocations: bool,
) -> dict[str, object]:
    providers = {name: _CountingProvider(view) for name, view in views.items()}
    measured: dict[str, dict[str, list[PhaseSample]]] = {}
    expected: dict[str, dict[str, object]] = {}
    backend_info: dict[str, object] | None = None

    for iteration in range(warmups + repeats):
        recording = iteration >= warmups
        for name, view in views.items():
            provider = providers[name]
            before_calls = provider.calls
            reasoner, construction = _observe(
                lambda provider=provider: Reasoner(
                    provider,
                    ReasonerConfig(backend=backend, workers=workers),
                ),
                trace_allocations=trace_allocations,
            )
            if provider.calls != before_calls + 1:
                reasoner.close()
                raise AssertionError("provider was not called exactly once for one session")
            try:
                if reasoner.ontology is not view:
                    raise AssertionError("reasoner did not retain the supplied view by identity")
                info = reasoner.backend
                if info.name != backend:
                    raise AssertionError(f"requested backend {backend!r} selected {info.name!r}")
                current_info = {
                    "name": info.name,
                    "implementation_version": info.implementation_version,
                    "ir_version": [info.ir_major, info.ir_minor],
                    "requested_workers": info.requested_workers,
                    "effective_workers": info.effective_workers,
                }
                if backend_info is not None and current_info != backend_info:
                    raise AssertionError("backend diagnostics changed between sessions")
                backend_info = current_info
                result, classification = _observe(
                    reasoner.classify, trace_allocations=trace_allocations
                )
                observation = _taxonomy_observation(result)
            finally:
                reasoner.close()

            previous = expected.get(name)
            if previous is not None and observation != previous:
                raise AssertionError(f"{backend} {name} result changed between samples")
            expected[name] = observation
            if recording:
                row = measured.setdefault(
                    name,
                    {"construction_samples": [], "classification_samples": []},
                )
                row["construction_samples"].append(construction)
                row["classification_samples"].append(classification)
            # Do not retain the prior taxonomy or closed facade while measuring the next
            # construction.  The source views remain resident by design; per-run products do not.
            del result, reasoner

    if backend_info is None:
        raise AssertionError("backend benchmark produced no session")
    results: dict[str, object] = {}
    for name in views:
        row = measured[name]
        construction_samples = row["construction_samples"]
        classification_samples = row["classification_samples"]
        results[name] = {
            **expected[name],
            "identity_preserved": True,
            "provider_calls": providers[name].calls,
            "provider_calls_expected": warmups + repeats,
            "session_construction": {
                "summary": asdict(_summarize(construction_samples)),
                "samples": [asdict(sample) for sample in construction_samples],
            },
            "classification": {
                "summary": asdict(_summarize(classification_samples)),
                "samples": [asdict(sample) for sample in classification_samples],
            },
        }
    return {
        "backend": backend_info,
        "views": results,
        "native_transfer": {
            "boundary": "public Reasoner construction",
            "compiled_ir_bytes": None,
            "contiguous_copy_count": None,
            "observable": False,
            "reason": (
                "The public facade deliberately does not expose private compiled IR bytes "
                "or native copy counters; construction wall/process-peak-RSS evidence is "
                "recorded, with allocation tracing available only as an opt-in diagnostic."
            ),
        }
        if backend == "rust"
        else None,
    }


def _backend_digest(result: dict[str, object], name: str) -> str:
    views = result.get("views")
    if not isinstance(views, dict):
        raise TypeError("backend result views must be a mapping")
    row = views.get(name)
    if not isinstance(row, dict):
        raise TypeError(f"backend result for {name!r} must be a mapping")
    digest = row.get("semantic_completeness_sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise TypeError(f"backend result for {name!r} has an invalid semantic digest")
    return digest


@contextmanager
def _workspace_native(path: Path | None) -> Iterator[None]:
    if path is None:
        yield
        return
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"native library is not a file: {path}")
    previous = sys.modules.get("pyelk._native")
    with tempfile.TemporaryDirectory(prefix="pyelk-biomedical-native-") as temporary:
        destination = Path(temporary) / ("_native.pyd" if sys.platform == "win32" else "_native.so")
        shutil.copy2(resolved, destination)
        spec = importlib.util.spec_from_file_location("pyelk._native", destination)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load native library: {path}")
        module: ModuleType = importlib.util.module_from_spec(spec)
        sys.modules["pyelk._native"] = module
        try:
            spec.loader.exec_module(module)
            yield
        finally:
            if previous is None:
                sys.modules.pop("pyelk._native", None)
            else:
                sys.modules["pyelk._native"] = previous


def run(
    *,
    source_path: Path,
    source_sha256: str,
    source_axiom_count: int,
    source_entity_count: int,
    target_path: Path,
    target_sha256: str,
    target_axiom_count: int,
    target_entity_count: int,
    alignment_path: Path,
    alignment_sha256: str,
    corpus_name: str,
    corpus_source: str,
    corpus_license: str,
    backends: Sequence[str],
    workers: int,
    warmups: int,
    repeats: int,
    native_path: Path | None = None,
    trace_allocations: bool = False,
) -> dict[str, object]:
    """Run one hash-pinned external-corpus observation."""

    for label, value in (
        ("corpus_name", corpus_name),
        ("corpus_source", corpus_source),
        ("corpus_license", corpus_license),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be nonempty")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a nonnegative integer")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if not isinstance(trace_allocations, bool):
        raise TypeError("trace_allocations must be bool")
    _require_count(source_axiom_count, "source_axiom_count")
    _require_count(source_entity_count, "source_entity_count")
    _require_count(target_axiom_count, "target_axiom_count")
    _require_count(target_entity_count, "target_entity_count")
    raw_selected = tuple(backends)
    if (
        not raw_selected
        or len(set(raw_selected)) != len(raw_selected)
        or any(backend not in {"python", "rust"} for backend in raw_selected)
    ):
        raise ValueError("backends must be a unique nonempty sequence of python/rust")
    selected = cast(tuple[BackendName, ...], raw_selected)

    source_file, source_bytes = _verified_file(source_path, source_sha256, "source ontology")
    target_file, target_bytes = _verified_file(target_path, target_sha256, "target ontology")
    alignment_file, alignment_bytes = _verified_file(alignment_path, alignment_sha256, "alignment")

    options = owl.LoadOptions(
        imports=owl.ImportPolicy.RESOLVE_LOCAL,
        backend=owl.BackendPreference.PYTHON,
        offline=True,
    )
    load_calls = {"source": 0, "target": 0}

    def load(label: str, path: Path) -> owl.OntologySnapshot:
        load_calls[label] += 1
        return owl.load_snapshot(path, options=options)

    source, source_load = _observe(
        lambda: load("source", source_file), trace_allocations=trace_allocations
    )
    target, target_load = _observe(
        lambda: load("target", target_file), trace_allocations=trace_allocations
    )
    _require_self_contained(source, "source ontology")
    _require_self_contained(target, "target ontology")
    source_counts, source_counting = _observe(
        lambda: _view_counts(source), trace_allocations=trace_allocations
    )
    target_counts, target_counting = _observe(
        lambda: _view_counts(target), trace_allocations=trace_allocations
    )
    _verify_counts(
        source_counts,
        expected_axioms=source_axiom_count,
        expected_entities=source_entity_count,
        label="source ontology",
    )
    _verify_counts(
        target_counts,
        expected_axioms=target_axiom_count,
        expected_entities=target_entity_count,
        label="target ontology",
    )
    bridge_axioms, alignment_parse = _observe(
        lambda: _bridge_axioms(alignment_file), trace_allocations=trace_allocations
    )
    composite, composite_create = _observe(
        lambda: owl.compose_views(
            source,
            target,
            delta=owl.OntologyDelta(add_axioms=bridge_axioms),
            roles=("source", "target"),
        ),
        trace_allocations=trace_allocations,
    )
    if load_calls != {"source": 1, "target": 1}:
        raise AssertionError("each ontology must be loaded exactly once")
    if tuple(member.view for member in composite.members) != (source, target):
        raise AssertionError("composite did not retain source and target by identity")

    views: dict[str, owl.OntologyView] = {
        "source": source,
        "target": target,
        "composite": composite,
    }
    backend_results: dict[str, dict[str, object]] = {}
    pure_environment = os.environ.get("PYELK_PURE_PYTHON")
    if "rust" in selected:
        os.environ.pop("PYELK_PURE_PYTHON", None)
    try:
        with _workspace_native(native_path if "rust" in selected else None):
            for backend in selected:
                backend_results[backend] = _run_backend(
                    backend=backend,
                    views=views,
                    workers=workers,
                    warmups=warmups,
                    repeats=repeats,
                    trace_allocations=trace_allocations,
                )
    finally:
        if pure_environment is None:
            os.environ.pop("PYELK_PURE_PYTHON", None)
        else:
            os.environ["PYELK_PURE_PYTHON"] = pure_environment

    parity: dict[str, bool] = {}
    reference_backend = selected[0]
    reference = backend_results[reference_backend]
    reference_views = reference["views"]
    assert isinstance(reference_views, dict)
    for name in views:
        expected_row = reference_views[name]
        assert isinstance(expected_row, dict)
        expected = expected_row["semantic_completeness_sha256"]
        parity[name] = all(
            _backend_digest(result, name) == expected for result in backend_results.values()
        )
        if not parity[name]:
            raise AssertionError(f"backend semantic/completeness mismatch for {name}")

    return {
        "schema": "pyelk.biomedical-benchmark/1",
        "gate_eligible": False,
        "gate_blockers": [
            "owner review and a clean labelled dedicated-runner record are still required",
            "Java-relative and prior-release RSS thresholds are evaluated outside this runner",
            "exact private IR transfer bytes/copy count are not exposed by the public facade",
        ],
        "corpus": {
            "name": corpus_name.strip(),
            "source": corpus_source.strip(),
            "license": corpus_license.strip(),
            "redistributed": False,
            "alignment_semantics": "EquivalentClasses",
        },
        "inputs": {
            "source": {
                "filename": source_file.name,
                "sha256": source_sha256,
                "bytes": source_bytes,
                "expected_axiom_count": source_axiom_count,
                "expected_entity_count_excluding_builtins": source_entity_count,
                "document_count": len(source.documents),
                "direct_import_count": len(source.root.direct_imports),
            },
            "target": {
                "filename": target_file.name,
                "sha256": target_sha256,
                "bytes": target_bytes,
                "expected_axiom_count": target_axiom_count,
                "expected_entity_count_excluding_builtins": target_entity_count,
                "document_count": len(target.documents),
                "direct_import_count": len(target.root.direct_imports),
            },
            "alignment": {
                "filename": alignment_file.name,
                "sha256": alignment_sha256,
                "bytes": alignment_bytes,
                "unique_equivalence_axioms": len(bridge_axioms),
            },
        },
        "protocol": {
            "warmups": warmups,
            "measured_runs": repeats,
            "workers": workers,
            "requested_backends": list(selected),
            "allocation_tracing": trace_allocations,
            "timing_warning": (
                "tracemalloc was enabled and wall timings are diagnostic only"
                if trace_allocations
                else None
            ),
            "dedicated_protocol_sample_count_met": warmups >= 2 and repeats >= 5,
        },
        "environment": {
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pyelk": _package_version("pyelk-reasoner"),
            "pyowl_core": owl.__version__,
        },
        "load_and_compose": {
            "load_calls": load_calls,
            "source_load": asdict(source_load),
            "target_load": asdict(target_load),
            "source_counting": asdict(source_counting),
            "target_counting": asdict(target_counting),
            "source_counts": source_counts,
            "target_counts": target_counts,
            "alignment_parse": asdict(alignment_parse),
            "composite_create": asdict(composite_create),
            "source_retained_by_identity": True,
            "target_retained_by_identity": True,
            "composite_members_retained_by_identity": True,
            "serialized_intermediate_created": False,
            "fingerprints": {
                name: {
                    "structural": view.structural_fingerprint.hex,
                    "logical": view.logical_fingerprint.hex,
                    "signature": view.signature_fingerprint.hex,
                }
                for name, view in views.items()
            },
        },
        "backends": backend_results,
        "backend_parity": parity,
    }


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--source-axiom-count", type=int, required=True)
    parser.add_argument("--source-entity-count", type=int, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--target-sha256", required=True)
    parser.add_argument("--target-axiom-count", type=int, required=True)
    parser.add_argument("--target-entity-count", type=int, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--alignment-sha256", required=True)
    parser.add_argument("--corpus-name", required=True)
    parser.add_argument("--corpus-source", required=True)
    parser.add_argument("--corpus-license", required=True)
    parser.add_argument("--backends", choices=("python", "rust", "both"), default="python")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--native-path", type=Path)
    parser.add_argument(
        "--trace-allocations",
        action="store_true",
        help="collect tracemalloc peaks; makes wall timings diagnostic rather than gate evidence",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    selected = ("python", "rust") if args.backends == "both" else (args.backends,)
    payload = run(
        source_path=args.source,
        source_sha256=args.source_sha256,
        source_axiom_count=args.source_axiom_count,
        source_entity_count=args.source_entity_count,
        target_path=args.target,
        target_sha256=args.target_sha256,
        target_axiom_count=args.target_axiom_count,
        target_entity_count=args.target_entity_count,
        alignment_path=args.alignment,
        alignment_sha256=args.alignment_sha256,
        corpus_name=args.corpus_name,
        corpus_source=args.corpus_source,
        corpus_license=args.corpus_license,
        backends=selected,
        workers=args.workers,
        warmups=args.warmups,
        repeats=args.repeats,
        native_path=args.native_path,
        trace_allocations=args.trace_allocations,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
