"""Exhaustive tests for the pinned ELK completeness matrix."""

from __future__ import annotations

import hashlib
import importlib
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, overload

import pytest

from pyelk.indexing.ir import FEATURE_VECTOR_LENGTH, U64_MAX
from pyelk.reasoning.completeness import (
    GENERAL_INCOMPLETENESS_COMBINATIONS,
    GENERAL_INCOMPLETENESS_FEATURES,
    OBJECT_PROPERTY_INCOMPLETENESS_COMBINATIONS,
    OBJECT_PROPERTY_INCOMPLETENESS_FEATURES,
    QUERY_FEATURE_BY_AXIOM,
    QUERY_INCOMPLETENESS_FEATURES,
    Feature,
    issues_for,
)
from pyelk.reasoning.contracts import PolicyFeature, ReasoningTask

tomllib: Any = importlib.import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")

_PINNED_FEATURE_NAMES = (
    "ANONYMOUS_INDIVIDUAL",
    "ASYMMETRIC_OBJECT_PROPERTY",
    "BOTTOM_OBJECT_PROPERTY_POSITIVE",
    "DATA_ALL_VALUES_FROM",
    "DATA_EXACT_CARDINALITY",
    "DATA_HAS_VALUE",
    "DATA_MAX_CARDINALITY",
    "DATA_MIN_CARDINALITY",
    "DATA_PROPERTY",
    "DATA_PROPERTY_ASSERTION",
    "DATA_PROPERTY_DOMAIN",
    "DATA_PROPERTY_RANGE",
    "DATA_SOME_VALUES_FROM",
    "DATATYPE",
    "DATATYPE_DEFINITION",
    "DIFFERENT_INDIVIDUALS",
    "DISJOINT_CLASSES",
    "DISJOINT_DATA_PROPERTIES",
    "DISJOINT_OBJECT_PROPERTIES",
    "DISJOINT_UNION",
    "EQUIVALENT_DATA_PROPERTIES",
    "FUNCTIONAL_DATA_PROPERTY",
    "FUNCTIONAL_OBJECT_PROPERTY",
    "HAS_KEY",
    "INVERSE_FUNCTIONAL_OBJECT_PROPERTY",
    "INVERSE_OBJECT_PROPERTIES",
    "IRREFLEXIVE_OBJECT_PROPERTY",
    "NEGATIVE_DATA_PROPERTY_ASSERTION",
    "NEGATIVE_OBJECT_PROPERTY_ASSERTION",
    "OBJECT_ALL_VALUES_FROM",
    "OBJECT_COMPLEMENT_OF_NEGATIVE",
    "OBJECT_COMPLEMENT_OF_POSITIVE",
    "OBJECT_EXACT_CARDINALITY",
    "OBJECT_HAS_SELF_NEGATIVE",
    "OBJECT_HAS_VALUE_POSITIVE",
    "OBJECT_INVERSE_OF",
    "OBJECT_MAX_CARDINALITY",
    "OBJECT_MIN_CARDINALITY",
    "OBJECT_ONE_OF",
    "OBJECT_PROPERTY_ASSERTION",
    "OBJECT_PROPERTY_CHAIN",
    "OBJECT_PROPERTY_RANGE",
    "OBJECT_UNION_OF_POSITIVE",
    "OWL_NOTHING_POSITIVE",
    "REFLEXIVE_OBJECT_PROPERTY",
    "SUB_DATA_PROPERTY_OF",
    "SWRL_RULE",
    "SYMMETRIC_OBJECT_PROPERTY",
    "TOP_OBJECT_PROPERTY_NEGATIVE",
    "QUERY_ANNOTATION_ASSERTION_AXIOM",
    "QUERY_ANNOTATION_PROPERTY_DOMAIN_AXIOM",
    "QUERY_ANNOTATION_PROPERTY_RANGE_AXIOM",
    "QUERY_SUB_ANNOTATION_PROPERTY_OF_AXIOM",
    "QUERY_DATA_PROPERTY_ASSERTION_AXIOM",
    "QUERY_NEGATIVE_DATA_PROPERTY_ASSERTION_AXIOM",
    "QUERY_NEGATIVE_OBJECT_PROPERTY_ASSERTION_AXIOM",
    "QUERY_DISJOINT_UNION_AXIOM",
    "QUERY_DATA_PROPERTY_DOMAIN_AXIOM",
    "QUERY_DATA_PROPERTY_RANGE_AXIOM",
    "QUERY_DISJOINT_DATA_PROPERTIES_AXIOM",
    "QUERY_EQUIVALENT_DATA_PROPERTIES_AXIOM",
    "QUERY_FUNCTIONAL_DATA_PROPERTY_AXIOM",
    "QUERY_SUB_DATA_PROPERTY_OF_AXIOM",
    "QUERY_DATATYPE_DEFINITION_AXIOM",
    "QUERY_DECLARATION_AXIOM",
    "QUERY_HAS_KEY_AXIOM",
    "QUERY_ASYMMETRIC_OBJECT_PROPERTY_AXIOM",
    "QUERY_DISJOINT_OBJECT_PROPERTIES_AXIOM",
    "QUERY_EQUIVALENT_OBJECT_PROPERTIES_AXIOM",
    "QUERY_FUNCTIONAL_OBJECT_PROPERTY_AXIOM",
    "QUERY_INVERSE_FUNCTIONAL_OBJECT_PROPERTY_AXIOM",
    "QUERY_INVERSE_OBJECT_PROPERTIES_AXIOM",
    "QUERY_IRREFLEXIVE_OBJECT_PROPERTY_AXIOM",
    "QUERY_OBJECT_PROPERTY_RANGE_AXIOM",
    "QUERY_REFLEXIVE_OBJECT_PROPERTY_AXIOM",
    "QUERY_SUB_OBJECT_PROPERTY_OF_AXIOM",
    "QUERY_SYMMETRIC_OBJECT_PROPERTY_AXIOM",
    "QUERY_TRANSITIVE_OBJECT_PROPERTY_AXIOM",
    "QUERY_SWRL_RULE",
)

_GENERAL_NAMES = {
    "ANONYMOUS_INDIVIDUAL",
    "ASYMMETRIC_OBJECT_PROPERTY",
    "BOTTOM_OBJECT_PROPERTY_POSITIVE",
    "DATA_ALL_VALUES_FROM",
    "DATA_EXACT_CARDINALITY",
    "DATA_HAS_VALUE",
    "DATA_MAX_CARDINALITY",
    "DATA_MIN_CARDINALITY",
    "DATA_PROPERTY",
    "DATA_PROPERTY_ASSERTION",
    "DATA_PROPERTY_DOMAIN",
    "DATA_PROPERTY_RANGE",
    "DATA_SOME_VALUES_FROM",
    "DATATYPE",
    "DATATYPE_DEFINITION",
    "DISJOINT_DATA_PROPERTIES",
    "DISJOINT_OBJECT_PROPERTIES",
    "DISJOINT_UNION",
    "EQUIVALENT_DATA_PROPERTIES",
    "FUNCTIONAL_DATA_PROPERTY",
    "FUNCTIONAL_OBJECT_PROPERTY",
    "HAS_KEY",
    "INVERSE_FUNCTIONAL_OBJECT_PROPERTY",
    "INVERSE_OBJECT_PROPERTIES",
    "IRREFLEXIVE_OBJECT_PROPERTY",
    "NEGATIVE_DATA_PROPERTY_ASSERTION",
    "NEGATIVE_OBJECT_PROPERTY_ASSERTION",
    "OBJECT_ALL_VALUES_FROM",
    "OBJECT_COMPLEMENT_OF_NEGATIVE",
    "OBJECT_EXACT_CARDINALITY",
    "OBJECT_HAS_SELF_NEGATIVE",
    "OBJECT_INVERSE_OF",
    "OBJECT_MAX_CARDINALITY",
    "OBJECT_MIN_CARDINALITY",
    "OBJECT_ONE_OF",
    "OBJECT_UNION_OF_POSITIVE",
    "SUB_DATA_PROPERTY_OF",
    "SWRL_RULE",
    "SYMMETRIC_OBJECT_PROPERTY",
    "TOP_OBJECT_PROPERTY_NEGATIVE",
}

_ROOT = Path(__file__).resolve().parents[3]
_FEATURE_MANIFEST = _ROOT / "tests" / "data" / "manifests" / "features.toml"


def _counts(*features: Feature) -> tuple[int, ...]:
    values = [0] * FEATURE_VECTOR_LENGTH
    for feature in features:
        values[feature] += 1
    return tuple(values)


def _feature_names(issues: Iterable[object]) -> set[tuple[str, ...]]:
    return {issue.features for issue in issues}  # type: ignore[attr-defined]


class _CountingFeatureVector(Sequence[int]):
    """Single-pass instrumentation for the fixed-width evaluator boundary."""

    def __init__(self, value: int) -> None:
        self._values = (value,) * FEATURE_VECTOR_LENGTH
        self.reads = 0

    def __len__(self) -> int:
        return len(self._values)

    @overload
    def __getitem__(self, index: int) -> int: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[int, ...]: ...

    def __getitem__(self, index: int | slice) -> int | tuple[int, ...]:
        return self._values[index]

    def __iter__(self) -> Iterator[int]:
        for value in self._values:
            self.reads += 1
            yield value


def test_feature_enum_exact_java_order_and_metadata() -> None:
    assert tuple(feature.name for feature in Feature) == _PINNED_FEATURE_NAMES
    assert tuple(int(feature) for feature in Feature) == tuple(range(FEATURE_VECTOR_LENGTH))
    assert len(GENERAL_INCOMPLETENESS_FEATURES) == 40
    assert {feature.name for feature in GENERAL_INCOMPLETENESS_FEATURES} == _GENERAL_NAMES
    assert Feature.OBJECT_COMPLEMENT_OF_NEGATIVE.constructor == "ObjectComplementOf"
    assert Feature.OBJECT_COMPLEMENT_OF_NEGATIVE.polarity == "NEGATIVE"
    assert Feature.OWL_NOTHING_POSITIVE.constructor == "owl:Nothing"
    assert Feature.OWL_NOTHING_POSITIVE.polarity == "POSITIVE"
    assert Feature.QUERY_SWRL_RULE.constructor == "SWRLRule"
    assert all(feature.polarity == "ANY" for feature in QUERY_INCOMPLETENESS_FEATURES)


def test_feature_manifest_covers_every_enum_fixture_and_test_pointer() -> None:
    with _FEATURE_MANIFEST.open("rb") as handle:
        payload = tomllib.load(handle)
    entries = payload["features"]

    assert payload["schema"] == "pyelk.elk-feature-manifest/1"
    assert payload["source_commit"] == "b8ac5ce83db0704a7359d96aa382891e2f547863"
    assert payload["source_tree"] == "9becd9e41eac6434a1e247c2a9b19644cdd9d27a"
    assert payload["feature_count"] == FEATURE_VECTOR_LENGTH
    assert payload["ontology_feature_count"] == 49
    assert payload["query_feature_count"] == 30
    assert len(entries) == FEATURE_VECTOR_LENGTH

    for feature, entry in zip(Feature, entries, strict=True):
        assert entry["index"] == int(feature)
        assert entry["name"] == feature.name
        assert entry["constructor"] == feature.constructor
        assert entry["polarity"] == feature.polarity
        assert entry["expected_count"] == 1
        assert entry["scope"] == ("query" if feature.name.startswith("QUERY_") else "ontology")
        assert entry["index_action"] in {"complete", "partial", "ignore", "nonlogical"}
        assert set(entry["affected_tasks"]) <= {task.value for task in ReasoningTask}
        assert all(":" in condition for condition in entry["conditions"])

        fixture = _ROOT / entry["fixture"]
        assert fixture.is_file()
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == entry["fixture_sha256"]
        test_path, test_name = entry["test"].split("::", 1)
        test_source = (_ROOT / test_path).read_text(encoding="utf-8")
        assert f"def {test_name}(" in test_source
        if feature.name.startswith("QUERY_"):
            assert entry["expected_value"] is False


def test_feature_manifest_conditions_match_the_production_evaluator() -> None:
    with _FEATURE_MANIFEST.open("rb") as handle:
        entries = tomllib.load(handle)["features"]
    query_tasks = {
        ReasoningTask.CLASS_EXPRESSION_QUERY,
        ReasoningTask.ENTAILMENT_QUERY,
    }

    for entry in entries:
        feature = Feature(entry["index"])
        if entry["scope"] == "query":
            issues = issues_for(
                ReasoningTask.ENTAILMENT_QUERY,
                _counts(),
                query_feature_counts=_counts(feature),
            )
            assert _feature_names(issues) == {(feature.name,)}
            assert entry["affected_tasks"] == [ReasoningTask.ENTAILMENT_QUERY.value]
            continue

        actual_tasks: set[str] = set()
        for task in ReasoningTask:
            issues = issues_for(
                task,
                _counts(feature),
                query_feature_counts=_counts() if task in query_tasks else (),
            )
            if any(feature.name in issue.features for issue in issues):
                actual_tasks.add(task.value)
        if entry["expected_issue"]:
            assert actual_tasks == set(entry["affected_tasks"])
        else:
            assert actual_tasks == set()

        conditional_tasks: set[str] = set()
        for condition in entry["conditions"]:
            scope, partner_name = condition.split(":", 1)
            partner = Feature[partner_name]
            selected_tasks = (
                tuple(ReasoningTask) if scope == "all_tasks" else (ReasoningTask(scope),)
            )
            conditional_tasks.update(task.value for task in selected_tasks)
            for task in selected_tasks:
                issues = issues_for(
                    task,
                    _counts(feature, partner),
                    query_feature_counts=_counts() if task in query_tasks else (),
                )
                assert (feature.name, partner.name) in _feature_names(issues) or (
                    partner.name,
                    feature.name,
                ) in _feature_names(issues)
        if entry["conditions"]:
            assert conditional_tasks == set(entry["affected_tasks"])


@pytest.mark.parametrize("feature", GENERAL_INCOMPLETENESS_FEATURES)
def test_every_general_single_feature_has_positive_and_negative_case(feature: Feature) -> None:
    issue = issues_for(ReasoningTask.CLASS_TAXONOMY, _counts(feature))

    assert len(issue) == 1
    assert issue[0].features == (feature.name,)
    assert issue[0].constructors == (feature.constructor,)
    assert issue[0].polarities == (feature.polarity,)
    assert issues_for(ReasoningTask.CLASS_TAXONOMY, _counts()) == ()


@pytest.mark.parametrize("combination", GENERAL_INCOMPLETENESS_COMBINATIONS)
def test_every_general_combination_requires_all_members(
    combination: tuple[Feature, ...],
) -> None:
    expected = tuple(feature.name for feature in combination)

    assert expected in _feature_names(issues_for(ReasoningTask.CONSISTENCY, _counts(*combination)))
    for omitted in combination:
        partial = tuple(feature for feature in combination if feature is not omitted)
        assert expected not in _feature_names(
            issues_for(ReasoningTask.CONSISTENCY, _counts(*partial))
        )


@pytest.mark.parametrize("feature", OBJECT_PROPERTY_INCOMPLETENESS_FEATURES)
def test_object_property_special_single_feature_is_task_local(feature: Feature) -> None:
    property_issues = issues_for(ReasoningTask.OBJECT_PROPERTY_TAXONOMY, _counts(feature))

    assert _feature_names(property_issues) == {(feature.name,)}
    assert issues_for(ReasoningTask.CLASS_TAXONOMY, _counts(feature)) == ()


@pytest.mark.parametrize("combination", OBJECT_PROPERTY_INCOMPLETENESS_COMBINATIONS)
def test_object_property_special_combination_requires_all_members(
    combination: tuple[Feature, ...],
) -> None:
    expected = tuple(feature.name for feature in combination)

    assert expected in _feature_names(
        issues_for(ReasoningTask.OBJECT_PROPERTY_TAXONOMY, _counts(*combination))
    )
    assert expected not in _feature_names(
        issues_for(ReasoningTask.CLASS_TAXONOMY, _counts(*combination))
    )
    for omitted in combination:
        partial = tuple(feature for feature in combination if feature is not omitted)
        assert expected not in _feature_names(
            issues_for(ReasoningTask.OBJECT_PROPERTY_TAXONOMY, _counts(*partial))
        )


@pytest.mark.parametrize("feature", QUERY_INCOMPLETENESS_FEATURES)
def test_every_unsupported_query_feature_is_reported(feature: Feature) -> None:
    issues = issues_for(
        ReasoningTask.ENTAILMENT_QUERY,
        _counts(),
        query_feature_counts=_counts(feature),
    )

    assert _feature_names(issues) == {(feature.name,)}


def test_query_feature_mapping_is_exhaustive_and_immutable() -> None:
    assert len(QUERY_FEATURE_BY_AXIOM) == 30
    assert set(QUERY_FEATURE_BY_AXIOM.values()) == set(QUERY_INCOMPLETENESS_FEATURES)
    with pytest.raises(TypeError):
        QUERY_FEATURE_BY_AXIOM["Other"] = Feature.QUERY_SWRL_RULE  # type: ignore[index]


def test_query_top_monitor_combines_ontology_and_query_occurrences() -> None:
    issues = issues_for(
        ReasoningTask.ENTAILMENT_QUERY,
        _counts(Feature.OBJECT_PROPERTY_RANGE),
        query_feature_counts=_counts(Feature.OBJECT_PROPERTY_ASSERTION),
    )

    assert _feature_names(issues) == {
        ("OBJECT_PROPERTY_RANGE", "OBJECT_PROPERTY_ASSERTION"),
    }


def test_query_monitor_deduplicates_general_ontology_reasons() -> None:
    issues = issues_for(
        ReasoningTask.CLASS_EXPRESSION_QUERY,
        _counts(Feature.DATA_PROPERTY),
        query_feature_counts=_counts(),
    )

    assert len(issues) == 1
    assert issues[0].features == ("DATA_PROPERTY",)


@pytest.mark.parametrize(
    "task",
    [
        ReasoningTask.CLASS_TAXONOMY,
        ReasoningTask.OBJECT_PROPERTY_TAXONOMY,
        ReasoningTask.REALIZATION,
        ReasoningTask.CLASS_EXPRESSION_QUERY,
    ],
)
def test_inconsistent_quiet_tasks_suppress_upstream_monitors(task: ReasoningTask) -> None:
    if task is ReasoningTask.CLASS_EXPRESSION_QUERY:
        issues = issues_for(
            task,
            _counts(Feature.DATA_PROPERTY, Feature.OWL_NOTHING_POSITIVE),
            query_feature_counts=_counts(),
            inconsistent=True,
        )
    else:
        issues = issues_for(
            task,
            _counts(Feature.DATA_PROPERTY, Feature.OWL_NOTHING_POSITIVE),
            inconsistent=True,
        )

    assert issues == ()


@pytest.mark.parametrize(
    "task",
    [ReasoningTask.CONSISTENCY, ReasoningTask.ENTAILMENT_QUERY],
)
def test_inconsistent_nonquiet_tasks_retain_monitors(task: ReasoningTask) -> None:
    if task is ReasoningTask.ENTAILMENT_QUERY:
        issues = issues_for(
            task,
            _counts(Feature.DATA_PROPERTY),
            query_feature_counts=_counts(),
            inconsistent=True,
        )
    else:
        issues = issues_for(
            task,
            _counts(Feature.DATA_PROPERTY),
            inconsistent=True,
        )

    assert _feature_names(issues) == {("DATA_PROPERTY",)}


def test_policy_issue_survives_quiet_short_circuit_and_is_deduplicated() -> None:
    issues = issues_for(
        ReasoningTask.CLASS_TAXONOMY,
        _counts(Feature.DATA_PROPERTY),
        policy_features=(PolicyFeature.IGNORED_IMPORT, PolicyFeature.IGNORED_IMPORT),
        inconsistent=True,
    )

    assert len(issues) == 1
    assert issues[0].features == ("PYELK_IGNORED_IMPORT",)
    assert issues[0].constructors == ("Import",)
    assert issues[0].polarities == ("ANY",)


@pytest.mark.parametrize(
    "feature",
    [
        Feature.DIFFERENT_INDIVIDUALS,
        Feature.DISJOINT_CLASSES,
        Feature.OBJECT_PROPERTY_ASSERTION,
        Feature.OBJECT_PROPERTY_RANGE,
        Feature.OBJECT_HAS_VALUE_POSITIVE,
        Feature.REFLEXIVE_OBJECT_PROPERTY,
        Feature.OBJECT_PROPERTY_CHAIN,
    ],
)
def test_unaffected_single_features_do_not_trigger_general_monitor(feature: Feature) -> None:
    assert issues_for(ReasoningTask.CONSISTENCY, _counts(feature)) == ()


def test_count_and_argument_contract_validation() -> None:
    with pytest.raises(ValueError, match="exactly 79"):
        issues_for(ReasoningTask.CONSISTENCY, ())
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        issues_for(
            ReasoningTask.CONSISTENCY,
            (0,) * (FEATURE_VECTOR_LENGTH - 1) + (-1,),
        )
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        issues_for(
            ReasoningTask.CONSISTENCY,
            (0,) * (FEATURE_VECTOR_LENGTH - 1) + (U64_MAX + 1,),
        )
    with pytest.raises(ValueError, match="query_feature_counts"):
        issues_for(ReasoningTask.ENTAILMENT_QUERY, _counts())
    with pytest.raises(ValueError, match="only for query tasks"):
        issues_for(
            ReasoningTask.REALIZATION,
            _counts(),
            query_feature_counts=_counts(),
        )
    with pytest.raises(ValueError, match="PolicyFeature"):
        issues_for(
            ReasoningTask.CONSISTENCY,
            _counts(),
            policy_features=("PYELK_IGNORED_IMPORT",),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="boolean"):
        issues_for(ReasoningTask.CONSISTENCY, _counts(), inconsistent=1)  # type: ignore[arg-type]


def test_maximum_count_vectors_are_consumed_once_with_bounded_output() -> None:
    ontology = _CountingFeatureVector(U64_MAX)
    query = _CountingFeatureVector(U64_MAX)

    issues = issues_for(
        ReasoningTask.ENTAILMENT_QUERY,
        ontology,
        query_feature_counts=query,
    )

    assert ontology.reads == FEATURE_VECTOR_LENGTH
    assert query.reads == FEATURE_VECTOR_LENGTH
    assert len(issues) == 72
    assert len(set(issues)) == len(issues)
    assert sum(len(issue.features) for issue in issues) == 74
