"""Benchmark deterministic class saturation on a deep generated hierarchy.

The default fixture contains 100,000 named classes in one subclass chain.  It isolates the
iterative scheduler and duplicate-suppressed context hot path: parsing, ontology compilation,
property saturation, and dispatcher construction are outside the timed interval.  Output is
JSON evidence rather than a machine-dependent pass/fail threshold.

Run from the repository root, for example::

    PYTHONPATH=src python benchmarks/bench_saturation.py

Use ``--classes 10000 --repeats 1 --warmups 0`` for a quick smoke run.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import resource
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
    ExpressionId,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    PropertyOccurrence,
)
from pyelk.reasoning.properties import saturate_properties
from pyelk.reasoning.saturation import SaturationDiagnostics, SaturationEngine


@dataclass(frozen=True, slots=True)
class Measurements:
    """Stable summary of repeated scheduler measurements."""

    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float
    maximum_seconds: float


def _compiled_deep_hierarchy(class_count: int) -> CompiledOntology:
    if class_count < 2:
        raise ValueError("class_count must be at least two")
    class_entities = (
        EntityRecord(EntityKind.CLASS, OWL_NOTHING_IRI),
        EntityRecord(EntityKind.CLASS, OWL_THING_IRI),
        *(
            EntityRecord(EntityKind.CLASS, f"urn:pyelk:bench:C{index:08d}")
            for index in range(class_count)
        ),
    )
    entities = (
        *class_entities,
        EntityRecord(EntityKind.OBJECT_PROPERTY, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
        EntityRecord(EntityKind.OBJECT_PROPERTY, OWL_TOP_OBJECT_PROPERTY_IRI),
    )
    expressions = tuple(
        ExpressionRecord(ExpressionTag.CLASS, (entity,)) for entity in range(len(class_entities))
    )
    occurrences: list[ExpressionOccurrence] = [ExpressionOccurrence(0, 0) for _ in expressions]
    subclass_axioms = tuple(
        (ExpressionId(index), ExpressionId(index + 1)) for index in range(2, class_count + 1)
    )
    for sub, super_ in subclass_axioms:
        previous_sub = occurrences[sub]
        occurrences[sub] = ExpressionOccurrence(previous_sub.negative + 1, previous_sub.positive)
        previous_super = occurrences[super_]
        occurrences[super_] = ExpressionOccurrence(
            previous_super.negative,
            previous_super.positive + 1,
        )
    bottom_property = EntityId(len(class_entities))
    top_property = EntityId(len(class_entities) + 1)
    return CompiledOntology(
        entities=entities,
        expressions=expressions,
        expression_occurrences=tuple(occurrences),
        property_occurrences=(PropertyOccurrence(0, 0), PropertyOccurrence(0, 0)),
        property_chains=((bottom_property,), (top_property,)),
        subclass_axioms=subclass_axioms,
        equivalent_class_axioms=(),
        disjoint_groups=(),
        subproperty_axioms=(),
        property_ranges=(),
        feature_counts=(0,) * FEATURE_VECTOR_LENGTH,
        source_fingerprint=b"\0" * 32,
    )


def _assert_result(
    engine: SaturationEngine,
    diagnostics: SaturationDiagnostics,
    class_count: int,
) -> None:
    root = ExpressionId(2)
    context = engine.context(root)
    if context is None or not context.saturated:
        raise AssertionError("benchmark root did not reach saturation")
    if len(context.decomposed_subsumers) != class_count:
        raise AssertionError("deep hierarchy closure lost a named-class subsumer")
    expected_conclusions = 1 + 2 * class_count
    if diagnostics.conclusions_inserted != expected_conclusions:
        raise AssertionError(
            f"expected {expected_conclusions} conclusions, got {diagnostics.conclusions_inserted}"
        )
    if diagnostics.rule_dispatches != diagnostics.conclusions_inserted:
        raise AssertionError("a novel conclusion was skipped or processed more than once")
    if diagnostics.duplicate_insertions:
        raise AssertionError("scheduler attempted duplicate storage")


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def run(*, class_count: int, repeats: int, warmups: int) -> dict[str, object]:
    """Build the scale fixture once, measure fresh scheduler runs, and return JSON data."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    if warmups < 0:
        raise ValueError("warmups cannot be negative")
    setup_started = time.perf_counter()
    compiled = _compiled_deep_hierarchy(class_count)
    properties = saturate_properties(compiled)
    setup_seconds = time.perf_counter() - setup_started
    root = ExpressionId(2)

    for _ in range(warmups):
        engine = SaturationEngine(compiled, properties)
        engine.run((root,))
        _assert_result(engine, engine.diagnostics(), class_count)

    elapsed: list[float] = []
    final_diagnostics: SaturationDiagnostics | None = None
    for _ in range(repeats):
        gc.collect()
        engine = SaturationEngine(compiled, properties)
        started = time.perf_counter()
        engine.run((root,))
        elapsed.append(time.perf_counter() - started)
        final_diagnostics = engine.diagnostics()
        _assert_result(engine, final_diagnostics, class_count)

    if final_diagnostics is None:  # pragma: no cover - guarded above
        raise AssertionError("benchmark produced no diagnostics")
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
        "benchmark": "deep-class-saturation",
        "class_count": class_count,
        "conclusion_count": final_diagnostics.conclusions_inserted,
        "fixture_setup_seconds": setup_seconds,
        "measurements": asdict(measurements),
        "peak_rss_bytes": _peak_rss_bytes(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "rule_dispatch_count": final_diagnostics.rule_dispatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classes", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    arguments = parser.parse_args()
    report = run(
        class_count=arguments.classes,
        repeats=arguments.repeats,
        warmups=arguments.warmups,
    )
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
