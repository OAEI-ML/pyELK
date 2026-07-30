"""Benchmark sparse object-property saturation without parser or compiler noise.

The default case contains one 100,000-property chain plus the required singleton chains.
It exercises suffix derivation, hierarchy closure, and composition indexing while keeping
the semantic closure sparse. Output is JSON so local and CI runs can retain measurements
without treating cross-machine timing as a regression gate.

Run from the repository root, for example::

    PYTHONPATH=src python benchmarks/bench_properties.py

Use ``--properties 1000 --repeats 1`` for a quick smoke run.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass

from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    OWL_BOTTOM_OBJECT_PROPERTY_IRI,
    OWL_NOTHING_IRI,
    OWL_THING_IRI,
    OWL_TOP_OBJECT_PROPERTY_IRI,
    CompiledOntology,
    EntityId,
    EntityKind,
    EntityRecord,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    PropertyChainId,
    PropertyOccurrence,
)
from pyelk.reasoning.properties import PropertySaturation, saturate_properties


@dataclass(frozen=True, slots=True)
class Measurements:
    """Stable summary of repeated saturation measurements."""

    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float
    maximum_seconds: float


def _compiled_sparse_chain(property_count: int) -> CompiledOntology:
    if property_count < 2:
        raise ValueError("property_count must be at least two")

    entities = (
        EntityRecord(EntityKind.CLASS, OWL_NOTHING_IRI),
        EntityRecord(EntityKind.CLASS, OWL_THING_IRI),
        EntityRecord(EntityKind.OBJECT_PROPERTY, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
        EntityRecord(EntityKind.OBJECT_PROPERTY, OWL_TOP_OBJECT_PROPERTY_IRI),
        *(
            EntityRecord(EntityKind.OBJECT_PROPERTY, f"urn:pyelk:bench:p{index:08d}")
            for index in range(property_count)
        ),
    )
    generated = tuple(EntityId(index) for index in range(4, property_count + 4))
    singleton_chains = tuple((EntityId(index),) for index in range(2, property_count + 4))
    property_chains = tuple(sorted((*singleton_chains, generated)))
    generated_chain_id = PropertyChainId(property_chains.index(generated))
    expressions = (
        ExpressionRecord(ExpressionTag.CLASS, (0,)),
        ExpressionRecord(ExpressionTag.CLASS, (1,)),
    )
    return CompiledOntology(
        entities=entities,
        expressions=expressions,
        expression_occurrences=(ExpressionOccurrence(0, 0),) * len(expressions),
        property_occurrences=(PropertyOccurrence(0, 0),) * (property_count + 2),
        property_chains=property_chains,
        subclass_axioms=(),
        equivalent_class_axioms=(),
        disjoint_groups=(),
        subproperty_axioms=((generated_chain_id, EntityId(3)),),
        property_ranges=(),
        feature_counts=(0,) * FEATURE_VECTOR_LENGTH,
        source_fingerprint=b"\0" * 32,
    )


def _assert_sparse_result(result: PropertySaturation, property_count: int) -> None:
    expected_chain_count = (property_count + 2) + (property_count - 1)
    if result.chain_count != expected_chain_count:
        raise AssertionError(
            f"expected {expected_chain_count} compact chains, got {result.chain_count}"
        )
    if len(result.subproperty_chains) != expected_chain_count + 1:
        raise AssertionError("sparse hierarchy should contain identities plus one told conclusion")
    if len(result.non_redundant_compositions) != property_count - 1:
        raise AssertionError("every complex suffix should have one non-redundant composition")
    if result.redundant_compositions or result.property_ranges or result.reflexive_properties:
        raise AssertionError("sparse fixture unexpectedly derived optional property metadata")


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def run(*, property_count: int, repeats: int, warmups: int) -> dict[str, object]:
    """Build the fixture once, measure saturation, and return a JSON-safe report."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    if warmups < 0:
        raise ValueError("warmups cannot be negative")

    setup_started = time.perf_counter()
    compiled = _compiled_sparse_chain(property_count)
    setup_seconds = time.perf_counter() - setup_started

    for _ in range(warmups):
        result = saturate_properties(compiled)
        _assert_sparse_result(result, property_count)
        del result

    elapsed: list[float] = []
    final_result: PropertySaturation | None = None
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        final_result = saturate_properties(compiled)
        elapsed.append(time.perf_counter() - started)
        _assert_sparse_result(final_result, property_count)

    if final_result is None:  # pragma: no cover - guarded by CLI validation
        raise AssertionError("benchmark produced no result")
    median = statistics.median(elapsed)
    measurements = Measurements(
        repeats=repeats,
        median_seconds=median,
        median_absolute_deviation_seconds=statistics.median(
            abs(value - median) for value in elapsed
        ),
        minimum_seconds=min(elapsed),
        maximum_seconds=max(elapsed),
    )
    return {
        "benchmark": "sparse-property-chain-saturation",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "property_count": property_count,
        "compiled_chain_count": len(compiled.property_chains),
        "saturated_chain_count": final_result.chain_count,
        "subproperty_conclusion_count": len(final_result.subproperty_chains),
        "composition_count": len(final_result.non_redundant_compositions),
        "fixture_setup_seconds": setup_seconds,
        "peak_rss_bytes": _peak_rss_bytes(),
        "measurements": asdict(measurements),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--properties", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    arguments = parser.parse_args()
    report = run(
        property_count=arguments.properties,
        repeats=arguments.repeats,
        warmups=arguments.warmups,
    )
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
