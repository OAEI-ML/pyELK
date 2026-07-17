from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from pyelk.indexing.registration import (
    REGISTRATION_BY_KEY,
    RULE_REGISTRATIONS,
    OccurrenceTrigger,
    RegistrationSource,
    registrations_for,
)

tomllib: Any = importlib.import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")

_MANIFEST = Path(__file__).resolve().parents[2] / "data" / "manifests" / "registration.toml"


def test_generated_registration_rows_equal_the_cross_backend_golden_manifest() -> None:
    with _MANIFEST.open("rb") as source:
        payload = tomllib.load(source)
    assert payload["schema"] == 1
    assert payload["elk_version"] == "0.6.0"
    assert payload["elk_commit"] == "b8ac5ce83db0704a7359d96aa382891e2f547863"
    expected = tuple(
        (
            row["key"],
            row["source"],
            row["occurrence"],
            row["expression_tag"],
            row["anchor"],
            row["condition"],
            row["rule"],
        )
        for row in payload["registration"]
    )
    actual = tuple(
        (
            row.key,
            row.source.value,
            row.occurrence.value,
            row.expression_tag,
            row.anchor,
            row.condition,
            row.rule,
        )
        for row in RULE_REGISTRATIONS
    )
    assert actual == expected
    assert len(REGISTRATION_BY_KEY) == len(RULE_REGISTRATIONS)


def test_manifest_covers_every_required_occurrence_linked_rule_family() -> None:
    rules = {row.rule for row in RULE_REGISTRATIONS}
    assert {
        "ObjectIntersectionFromFirstConjunctRule",
        "ObjectIntersectionFromSecondConjunctRule",
        "IndexedObjectIntersectionOfDecomposition",
        "PropagationFromExistentialFillerRule",
        "IndexedObjectSomeValuesFromDecomposition",
        "IndexedObjectHasSelfDecomposition",
        "IndexedObjectComplementOfDecomposition",
        "ContradictionFromNegationRule",
        "ObjectUnionFromDisjunctRule",
        "SuperClassFromSubClassRule",
        "IndexedClassFromDefinitionRule",
        "IndexedClassDecompositionRule",
        "DisjointSubsumerFromMemberRule",
        "ToldObjectPropertyRange",
        "LeftChainLink",
        "RightChainLink",
    } <= rules
    assert all(row.java_class and row.java_path.endswith(".java") for row in RULE_REGISTRATIONS)
    assert all(
        row.java_path.startswith("elk-reasoner/src/main/java/") for row in RULE_REGISTRATIONS
    )


def test_registration_lookup_is_immutable_and_filtered() -> None:
    intersections = registrations_for(
        RegistrationSource.EXPRESSION,
        expression_tag="OBJECT_INTERSECTION_OF",
    )
    assert {row.occurrence for row in intersections} == {
        OccurrenceTrigger.NEGATIVE,
        OccurrenceTrigger.POSITIVE,
    }
    assert REGISTRATION_BY_KEY["intersection-decomposition"] in intersections
    try:
        REGISTRATION_BY_KEY["new"] = intersections[0]  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("registration mapping must be immutable")
