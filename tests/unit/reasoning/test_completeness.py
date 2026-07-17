"""Exhaustive tests for the pinned ELK completeness matrix."""

from __future__ import annotations

from collections.abc import Iterable

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


def _counts(*features: Feature) -> tuple[int, ...]:
    values = [0] * FEATURE_VECTOR_LENGTH
    for feature in features:
        values[feature] += 1
    return tuple(values)


def _feature_names(issues: Iterable[object]) -> set[tuple[str, ...]]:
    return {issue.features for issue in issues}  # type: ignore[attr-defined]


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
