"""Measure pyELK capture overhead over resident pyowl-core shared views.

The default fixture contains one million axioms split between source and target snapshots.
Fixture construction is programmatic: the benchmark never serializes an existing view to
Functional Syntax, RDF, or another intermediate representation.  Cold core fingerprint work is
reported separately from warm pyELK capture so the adapter's bounded-copy behavior is visible.

Run from the repository root, for example::

    PYTHONPATH=src:../pyOWLCore/src python benchmarks/bench_snapshot_ingestion.py

Use ``--axioms 1000`` for a quick smoke run.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import statistics
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TypeVar

import pyowl_core

from pyelk.inputs import InputCapture, capture_input

T = TypeVar("T")
_MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Phase:
    """Repeat summary for one benchmark phase."""

    repeats: int
    median_seconds: float
    minimum_seconds: float
    peak_traced_bytes: int


class _CountingProvider:
    """Exact-OM-shaped provider used to prove the one-call handshake."""

    __slots__ = ("calls", "view")

    def __init__(self, view: pyowl_core.OntologyView) -> None:
        self.view = view
        self.calls = 0

    def owl_snapshot(self) -> pyowl_core.OntologyView:
        self.calls += 1
        return self.view


def _measure(callback: Callable[[], T], *, repeats: int) -> tuple[T, Phase]:
    elapsed: list[float] = []
    peaks: list[int] = []
    result: T | None = None
    for _ in range(repeats):
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        candidate = callback()
        elapsed.append(time.perf_counter() - started)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
        result = candidate
    if result is None:  # pragma: no cover - guarded by CLI validation
        raise AssertionError("a measured phase requires at least one repeat")
    return result, Phase(
        repeats=repeats,
        median_seconds=statistics.median(elapsed),
        minimum_seconds=min(elapsed),
        peak_traced_bytes=max(peaks),
    )


def _time(callback: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    result = callback()
    return result, time.perf_counter() - started


def _programmatic_snapshot(
    namespace: str,
    *,
    axiom_count: int,
) -> pyowl_core.OntologySnapshot:
    """Build a deterministic resident snapshot without a serialized intermediary."""

    document_iri = pyowl_core.IRI(namespace)
    axioms: pyowl_core.CanonicalSet[pyowl_core.AxiomNode] = pyowl_core.CanonicalSet(
        pyowl_core.Declaration(pyowl_core.Class(pyowl_core.IRI(f"{namespace}#C{index}")))
        for index in range(axiom_count)
    )
    descriptor = f"pyelk-ingestion-benchmark:{namespace}:{axiom_count}".encode()
    source_digest = hashlib.sha256(descriptor).digest()
    provenance = pyowl_core.DocumentProvenance(
        source_sha256=source_digest,
        digest_kind=pyowl_core.DigestKind.EXACT_BYTES,
        byte_length=0,
        decoded_codepoint_length=0,
        document_iri=document_iri,
        acquisition_locator=None,
        format=pyowl_core.DocumentFormat.FUNCTIONAL,
        detection_basis=pyowl_core.DetectionBasis.EXPLICIT,
        parser="benchmarks.programmatic",
        backend="python",
    )
    document = pyowl_core.OntologyDocument(
        ontology_id=pyowl_core.OntologyID(document_iri),
        document_iri=document_iri,
        direct_imports=(),
        ontology_annotations=pyowl_core.CanonicalSet(),
        axioms=axioms,
        extension_components=pyowl_core.CanonicalSet(),
        provenance=provenance,
        origin_index=pyowl_core.OriginIndex(),
    )
    document_key = "benchmark:" + source_digest.hex()
    record = pyowl_core.DocumentRecord(
        document_key=document_key,
        ontology_id=document.ontology_id,
        document_iri=document_iri,
        source_sha256=source_digest,
        document_fingerprint=document.document_fingerprint,
        format=pyowl_core.DocumentFormat.FUNCTIONAL,
        status=pyowl_core.DocumentStatus.ROOT,
    )
    manifest = pyowl_core.ImportManifest(
        policy=pyowl_core.ImportPolicy.IGNORE,
        offline=True,
        resolver_configuration_fingerprint=hashlib.sha256(
            b"pyelk-ingestion-benchmark:no-resolver:v1"
        ).digest(),
        documents=(record,),
        edges=(),
    )
    options = pyowl_core.LoadOptions(
        imports=pyowl_core.ImportPolicy.IGNORE,
        backend=pyowl_core.BackendPreference.PYTHON,
        offline=True,
        collect_provenance=False,
    )
    return pyowl_core.OntologySnapshot(
        root=document,
        documents=(document,),
        import_manifest=manifest,
        root_document_key=document_key,
        load_options=options,
    )


def _assert_capture(captured: InputCapture, expected: pyowl_core.OntologyView) -> None:
    if captured.ontology.view is not expected:
        raise AssertionError("pyELK did not retain the supplied view by identity")


def _fingerprints(view: pyowl_core.OntologyView) -> tuple[pyowl_core.Fingerprint, ...]:
    return (
        view.structural_fingerprint,
        view.logical_fingerprint,
        view.signature_fingerprint,
    )


def run(*, axiom_count: int, repeats: int, adapter_peak_mib: float) -> dict[str, object]:
    """Run the benchmark and return one machine-readable report."""

    if axiom_count < 2:
        raise ValueError("axiom_count must be at least two")
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if adapter_peak_mib <= 0:
        raise ValueError("adapter_peak_mib must be positive")

    source_count = (axiom_count + 1) // 2
    target_count = axiom_count // 2
    source, source_setup_seconds = _time(
        lambda: _programmatic_snapshot("urn:pyelk:benchmark:source", axiom_count=source_count)
    )
    target, target_setup_seconds = _time(
        lambda: _programmatic_snapshot("urn:pyelk:benchmark:target", axiom_count=target_count)
    )

    provider = _CountingProvider(source)

    def capture_provider() -> InputCapture:
        before = provider.calls
        captured = capture_input(provider)
        if provider.calls != before + 1:
            raise AssertionError("each capture must invoke SnapshotProvider exactly once")
        _assert_capture(captured, source)
        return captured

    provider_capture, provider_phase = _measure(capture_provider, repeats=repeats)

    overlay_axiom = pyowl_core.Declaration(
        pyowl_core.Class(pyowl_core.IRI("urn:pyelk:benchmark:overlay#Added"))
    )
    overlay_delta = pyowl_core.OntologyDelta(add_axioms=pyowl_core.CanonicalSet((overlay_axiom,)))
    overlay, overlay_create_phase = _measure(
        lambda: pyowl_core.apply_delta(source, overlay_delta),
        repeats=repeats,
    )
    if overlay.base is not source or overlay.delta.entry_count != 1:
        raise AssertionError("overlay did not preserve its base plus O(k) delta")

    overlay_capture, overlay_cold_phase = _measure(
        lambda: capture_input(overlay),
        repeats=1,
    )
    _assert_capture(overlay_capture, overlay)
    overlay_warm_capture, overlay_warm_phase = _measure(
        lambda: capture_input(overlay),
        repeats=repeats,
    )
    _assert_capture(overlay_warm_capture, overlay)

    bridge = pyowl_core.SubClassOf(
        pyowl_core.Class(pyowl_core.IRI("urn:pyelk:benchmark:source#C0")),
        pyowl_core.Class(pyowl_core.IRI("urn:pyelk:benchmark:target#C0")),
    )
    bridge_delta = pyowl_core.OntologyDelta(add_axioms=pyowl_core.CanonicalSet((bridge,)))
    composite, composite_create_phase = _measure(
        lambda: pyowl_core.compose_views(
            source,
            target,
            delta=bridge_delta,
            roles=("source", "target"),
        ),
        repeats=repeats,
    )
    member_views = tuple(member.view for member in composite.members)
    if member_views != (source, target) or composite.delta.entry_count != 1:
        raise AssertionError("composite did not retain source/target plus bridge delta")

    composite_capture, composite_cold_phase = _measure(
        lambda: capture_input(composite),
        repeats=1,
    )
    _assert_capture(composite_capture, composite)
    composite_warm_capture, composite_warm_phase = _measure(
        lambda: capture_input(composite),
        repeats=repeats,
    )
    _assert_capture(composite_warm_capture, composite)

    adapter_limit = int(adapter_peak_mib * _MIB)
    bounded_phases = {
        "provider_capture": provider_phase,
        "overlay_create": overlay_create_phase,
        "overlay_warm_capture": overlay_warm_phase,
        "composite_create": composite_create_phase,
        "composite_warm_capture": composite_warm_phase,
    }
    exceeded = {
        name: phase.peak_traced_bytes
        for name, phase in bounded_phases.items()
        if phase.peak_traced_bytes > adapter_limit
    }
    if exceeded:
        raise AssertionError(
            f"bounded shared-layer memory ceiling exceeded ({adapter_limit} bytes): {exceeded}"
        )

    # Keep these observations live until all identity and memory assertions have completed.
    _ = (provider_capture, overlay_warm_capture, composite_warm_capture)
    phases = {
        **bounded_phases,
        "overlay_cold_core_fingerprints_and_capture": overlay_cold_phase,
        "composite_cold_core_fingerprints_and_capture": composite_cold_phase,
    }
    return {
        "schema": "pyelk.snapshot-ingestion-benchmark.v1",
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "pyowl_core": pyowl_core.__version__,
        },
        "fixture": {
            "axiom_count": axiom_count,
            "source_axioms": source_count,
            "target_axioms": target_count,
            "bridge_axioms": 1,
            "overlay_delta_entries": overlay.delta.entry_count,
            "source_setup_seconds": source_setup_seconds,
            "target_setup_seconds": target_setup_seconds,
        },
        "phases": {name: asdict(phase) for name, phase in phases.items()},
        "invariants": {
            "provider_call_count": provider.calls,
            "provider_one_call_per_capture": provider.calls == repeats,
            "source_retained_by_identity": provider_capture.ontology.view is source,
            "overlay_base_retained_by_identity": overlay.base is source,
            "overlay_retained_by_identity": overlay_capture.ontology.view is overlay,
            "composite_members_retained_by_identity": member_views == (source, target),
            "composite_retained_by_identity": composite_capture.ontology.view is composite,
            "serialized_intermediate_created": False,
            "adapter_peak_limit_bytes": adapter_limit,
            "bounded_phases_within_limit": True,
            "fingerprints_present": all(
                len(item.digest) == 32
                for view in (source, target, overlay, composite)
                for item in _fingerprints(view)
            ),
        },
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--axioms",
        type=int,
        default=1_000_000,
        help="total source+target axioms (default: 1,000,000)",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--adapter-peak-mib",
        type=float,
        default=16.0,
        help="fixed peak-memory ceiling for shared-layer construction and warm capture",
    )
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    report = run(
        axiom_count=arguments.axioms,
        repeats=arguments.repeats,
        adapter_peak_mib=arguments.adapter_peak_mib,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
