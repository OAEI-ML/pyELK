"""ELK 0.6.0 feature identities and backend-independent completeness evaluation.

The integer values in :class:`Feature` are part of the v1 compiled-ontology wire
contract.  They reproduce ``Feature.java`` at the commit pinned in
``specs/baseline.toml`` and must therefore never be reordered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import IntEnum
from types import MappingProxyType

from pyelk.indexing.ir import FEATURE_VECTOR_LENGTH, U64_MAX
from pyelk.reasoning.contracts import (
    CompletenessIssue,
    Polarity,
    PolicyFeature,
    ReasoningTask,
)


class Feature(IntEnum):
    """Pinned ``org.semanticweb.elk.reasoner.completeness.Feature`` order."""

    ANONYMOUS_INDIVIDUAL = 0
    ASYMMETRIC_OBJECT_PROPERTY = 1
    BOTTOM_OBJECT_PROPERTY_POSITIVE = 2
    DATA_ALL_VALUES_FROM = 3
    DATA_EXACT_CARDINALITY = 4
    DATA_HAS_VALUE = 5
    DATA_MAX_CARDINALITY = 6
    DATA_MIN_CARDINALITY = 7
    DATA_PROPERTY = 8
    DATA_PROPERTY_ASSERTION = 9
    DATA_PROPERTY_DOMAIN = 10
    DATA_PROPERTY_RANGE = 11
    DATA_SOME_VALUES_FROM = 12
    DATATYPE = 13
    DATATYPE_DEFINITION = 14
    DIFFERENT_INDIVIDUALS = 15
    DISJOINT_CLASSES = 16
    DISJOINT_DATA_PROPERTIES = 17
    DISJOINT_OBJECT_PROPERTIES = 18
    DISJOINT_UNION = 19
    EQUIVALENT_DATA_PROPERTIES = 20
    FUNCTIONAL_DATA_PROPERTY = 21
    FUNCTIONAL_OBJECT_PROPERTY = 22
    HAS_KEY = 23
    INVERSE_FUNCTIONAL_OBJECT_PROPERTY = 24
    INVERSE_OBJECT_PROPERTIES = 25
    IRREFLEXIVE_OBJECT_PROPERTY = 26
    NEGATIVE_DATA_PROPERTY_ASSERTION = 27
    NEGATIVE_OBJECT_PROPERTY_ASSERTION = 28
    OBJECT_ALL_VALUES_FROM = 29
    OBJECT_COMPLEMENT_OF_NEGATIVE = 30
    OBJECT_COMPLEMENT_OF_POSITIVE = 31
    OBJECT_EXACT_CARDINALITY = 32
    OBJECT_HAS_SELF_NEGATIVE = 33
    OBJECT_HAS_VALUE_POSITIVE = 34
    OBJECT_INVERSE_OF = 35
    OBJECT_MAX_CARDINALITY = 36
    OBJECT_MIN_CARDINALITY = 37
    OBJECT_ONE_OF = 38
    OBJECT_PROPERTY_ASSERTION = 39
    OBJECT_PROPERTY_CHAIN = 40
    OBJECT_PROPERTY_RANGE = 41
    OBJECT_UNION_OF_POSITIVE = 42
    OWL_NOTHING_POSITIVE = 43
    REFLEXIVE_OBJECT_PROPERTY = 44
    SUB_DATA_PROPERTY_OF = 45
    SWRL_RULE = 46
    SYMMETRIC_OBJECT_PROPERTY = 47
    TOP_OBJECT_PROPERTY_NEGATIVE = 48
    QUERY_ANNOTATION_ASSERTION_AXIOM = 49
    QUERY_ANNOTATION_PROPERTY_DOMAIN_AXIOM = 50
    QUERY_ANNOTATION_PROPERTY_RANGE_AXIOM = 51
    QUERY_SUB_ANNOTATION_PROPERTY_OF_AXIOM = 52
    QUERY_DATA_PROPERTY_ASSERTION_AXIOM = 53
    QUERY_NEGATIVE_DATA_PROPERTY_ASSERTION_AXIOM = 54
    QUERY_NEGATIVE_OBJECT_PROPERTY_ASSERTION_AXIOM = 55
    QUERY_DISJOINT_UNION_AXIOM = 56
    QUERY_DATA_PROPERTY_DOMAIN_AXIOM = 57
    QUERY_DATA_PROPERTY_RANGE_AXIOM = 58
    QUERY_DISJOINT_DATA_PROPERTIES_AXIOM = 59
    QUERY_EQUIVALENT_DATA_PROPERTIES_AXIOM = 60
    QUERY_FUNCTIONAL_DATA_PROPERTY_AXIOM = 61
    QUERY_SUB_DATA_PROPERTY_OF_AXIOM = 62
    QUERY_DATATYPE_DEFINITION_AXIOM = 63
    QUERY_DECLARATION_AXIOM = 64
    QUERY_HAS_KEY_AXIOM = 65
    QUERY_ASYMMETRIC_OBJECT_PROPERTY_AXIOM = 66
    QUERY_DISJOINT_OBJECT_PROPERTIES_AXIOM = 67
    QUERY_EQUIVALENT_OBJECT_PROPERTIES_AXIOM = 68
    QUERY_FUNCTIONAL_OBJECT_PROPERTY_AXIOM = 69
    QUERY_INVERSE_FUNCTIONAL_OBJECT_PROPERTY_AXIOM = 70
    QUERY_INVERSE_OBJECT_PROPERTIES_AXIOM = 71
    QUERY_IRREFLEXIVE_OBJECT_PROPERTY_AXIOM = 72
    QUERY_OBJECT_PROPERTY_RANGE_AXIOM = 73
    QUERY_REFLEXIVE_OBJECT_PROPERTY_AXIOM = 74
    QUERY_SUB_OBJECT_PROPERTY_OF_AXIOM = 75
    QUERY_SYMMETRIC_OBJECT_PROPERTY_AXIOM = 76
    QUERY_TRANSITIVE_OBJECT_PROPERTY_AXIOM = 77
    QUERY_SWRL_RULE = 78

    @property
    def constructor(self) -> str:
        """Return the exact pinned Java constructor label."""

        return _FEATURE_METADATA[int(self)][0]

    @property
    def polarity(self) -> Polarity:
        """Return the exact pinned Java polarity."""

        return _FEATURE_METADATA[int(self)][1]


_FEATURE_METADATA: tuple[tuple[str, Polarity], ...] = (
    ("AnonymousIndividual", "ANY"),
    ("AsymmetricObjectProperty", "ANY"),
    ("owl:bottomObjectProperty", "POSITIVE"),
    ("DataAllValuesFrom", "ANY"),
    ("DataExactCardinality", "ANY"),
    ("DataHasValue", "ANY"),
    ("DataMaxCardinality", "ANY"),
    ("DataMinCardinality", "ANY"),
    ("DataProperty", "ANY"),
    ("DataPropertyAssertion", "ANY"),
    ("DataPropertyDomain", "ANY"),
    ("DataPropertyRange", "ANY"),
    ("DataSomeValuesFrom", "ANY"),
    ("Datatype", "ANY"),
    ("DatatypeDefinition", "ANY"),
    ("DifferentIndividuals", "ANY"),
    ("DisjointClasses", "ANY"),
    ("DisjointDataProperties", "ANY"),
    ("DisjointObjectProperties", "ANY"),
    ("DisjointUnion", "ANY"),
    ("EquivalentDataProperties", "ANY"),
    ("FunctionalDataProperty", "ANY"),
    ("FunctionalObjectProperty", "ANY"),
    ("HasKey", "ANY"),
    ("InverseFunctionalObjectProperty", "ANY"),
    ("InverseObjectProperties", "ANY"),
    ("IrreflexiveObjectProperty", "ANY"),
    ("NegativeDataPropertyAssertion", "ANY"),
    ("NegativeObjectPropertyAssertion", "ANY"),
    ("ObjectAllValuesFrom", "ANY"),
    ("ObjectComplementOf", "NEGATIVE"),
    ("ObjectComplementOf", "POSITIVE"),
    ("ObjectExactCardinality", "ANY"),
    ("ObjectHasSelf", "NEGATIVE"),
    ("ObjectHasValue", "POSITIVE"),
    ("ObjectInverseOf", "ANY"),
    ("ObjectMaxCardinality", "ANY"),
    ("ObjectMinCardinality", "ANY"),
    ("ObjectOneOf", "ANY"),
    ("ObjectPropertyAssertion", "ANY"),
    ("ObjectPropertyChain", "ANY"),
    ("ObjectPropertyRange", "ANY"),
    ("ObjectUnionOf", "POSITIVE"),
    ("owl:Nothing", "POSITIVE"),
    ("ReflexiveObjectProperty", "ANY"),
    ("SubDataPropertyOf", "ANY"),
    ("SWRLRule", "ANY"),
    ("SymmetricObjectProperty", "ANY"),
    ("owl:topObjectProperty", "NEGATIVE"),
    ("AnnotationAssertionAxiom", "ANY"),
    ("AnnotationPropertyDomainAxiom", "ANY"),
    ("AnnotationPropertyRangeAxiom", "ANY"),
    ("SubAnnotationPropertyOfAxiom", "ANY"),
    ("DataPropertyAssertionAxiom", "ANY"),
    ("NegativeDataPropertyAssertionAxiom", "ANY"),
    ("NegativeObjectPropertyAssertionAxiom", "ANY"),
    ("DisjointUnionAxiom", "ANY"),
    ("DataPropertyDomainAxiom", "ANY"),
    ("DataPropertyRangeAxiom", "ANY"),
    ("DisjointDataPropertiesAxiom", "ANY"),
    ("EquivalentDataPropertiesAxiom", "ANY"),
    ("FunctionalDataPropertyAxiom", "ANY"),
    ("SubDataPropertyOfAxiom", "ANY"),
    ("DatatypeDefinitionAxiom", "ANY"),
    ("DeclarationAxiom", "ANY"),
    ("HasKeyAxiom", "ANY"),
    ("AsymmetricObjectPropertyAxiom", "ANY"),
    ("DisjointObjectPropertiesAxiom", "ANY"),
    ("EquivalentObjectPropertiesAxiom", "ANY"),
    ("FunctionalObjectPropertyAxiom", "ANY"),
    ("InverseFunctionalObjectPropertyAxiom", "ANY"),
    ("InverseObjectPropertiesAxiom", "ANY"),
    ("IrreflexiveObjectPropertyAxiom", "ANY"),
    ("ObjectPropertyRangeAxiom", "ANY"),
    ("ReflexiveObjectPropertyAxiom", "ANY"),
    ("SubObjectPropertyOfAxiom", "ANY"),
    ("SymmetricObjectPropertyAxiom", "ANY"),
    ("TransitiveObjectPropertyAxiom", "ANY"),
    ("SWRLRule", "ANY"),
)

if len(Feature) != FEATURE_VECTOR_LENGTH or len(_FEATURE_METADATA) != FEATURE_VECTOR_LENGTH:
    raise RuntimeError("pinned ELK feature metadata does not match the v1 IR width")


GENERAL_INCOMPLETENESS_FEATURES: tuple[Feature, ...] = (
    Feature.ANONYMOUS_INDIVIDUAL,
    Feature.ASYMMETRIC_OBJECT_PROPERTY,
    Feature.BOTTOM_OBJECT_PROPERTY_POSITIVE,
    Feature.DATA_ALL_VALUES_FROM,
    Feature.DATA_EXACT_CARDINALITY,
    Feature.DATA_HAS_VALUE,
    Feature.DATA_MAX_CARDINALITY,
    Feature.DATA_MIN_CARDINALITY,
    Feature.DATA_PROPERTY,
    Feature.DATA_PROPERTY_ASSERTION,
    Feature.DATA_PROPERTY_DOMAIN,
    Feature.DATA_PROPERTY_RANGE,
    Feature.DATA_SOME_VALUES_FROM,
    Feature.DATATYPE,
    Feature.DATATYPE_DEFINITION,
    Feature.DISJOINT_DATA_PROPERTIES,
    Feature.DISJOINT_OBJECT_PROPERTIES,
    Feature.DISJOINT_UNION,
    Feature.EQUIVALENT_DATA_PROPERTIES,
    Feature.FUNCTIONAL_DATA_PROPERTY,
    Feature.FUNCTIONAL_OBJECT_PROPERTY,
    Feature.HAS_KEY,
    Feature.INVERSE_FUNCTIONAL_OBJECT_PROPERTY,
    Feature.INVERSE_OBJECT_PROPERTIES,
    Feature.IRREFLEXIVE_OBJECT_PROPERTY,
    Feature.NEGATIVE_DATA_PROPERTY_ASSERTION,
    Feature.NEGATIVE_OBJECT_PROPERTY_ASSERTION,
    Feature.OBJECT_ALL_VALUES_FROM,
    Feature.OBJECT_COMPLEMENT_OF_NEGATIVE,
    Feature.OBJECT_EXACT_CARDINALITY,
    Feature.OBJECT_HAS_SELF_NEGATIVE,
    Feature.OBJECT_INVERSE_OF,
    Feature.OBJECT_MAX_CARDINALITY,
    Feature.OBJECT_MIN_CARDINALITY,
    Feature.OBJECT_ONE_OF,
    Feature.OBJECT_UNION_OF_POSITIVE,
    Feature.SUB_DATA_PROPERTY_OF,
    Feature.SWRL_RULE,
    Feature.SYMMETRIC_OBJECT_PROPERTY,
    Feature.TOP_OBJECT_PROPERTY_NEGATIVE,
)

GENERAL_INCOMPLETENESS_COMBINATIONS: tuple[tuple[Feature, ...], ...] = (
    (Feature.OBJECT_PROPERTY_RANGE, Feature.OBJECT_PROPERTY_ASSERTION),
    (Feature.OBJECT_PROPERTY_RANGE, Feature.OBJECT_HAS_VALUE_POSITIVE),
)

OBJECT_PROPERTY_INCOMPLETENESS_FEATURES: tuple[Feature, ...] = (
    Feature.OWL_NOTHING_POSITIVE,
    Feature.DISJOINT_CLASSES,
    Feature.OBJECT_COMPLEMENT_OF_POSITIVE,
)

OBJECT_PROPERTY_INCOMPLETENESS_COMBINATIONS: tuple[tuple[Feature, ...], ...] = (
    (Feature.REFLEXIVE_OBJECT_PROPERTY, Feature.OBJECT_PROPERTY_CHAIN),
)

QUERY_INCOMPLETENESS_FEATURES: tuple[Feature, ...] = tuple(
    feature for feature in Feature if feature.name.startswith("QUERY_")
)

QUERY_FEATURE_BY_AXIOM: Mapping[str, Feature] = MappingProxyType(
    {
        "AnnotationAssertion": Feature.QUERY_ANNOTATION_ASSERTION_AXIOM,
        "AnnotationPropertyDomain": Feature.QUERY_ANNOTATION_PROPERTY_DOMAIN_AXIOM,
        "AnnotationPropertyRange": Feature.QUERY_ANNOTATION_PROPERTY_RANGE_AXIOM,
        "SubAnnotationPropertyOf": Feature.QUERY_SUB_ANNOTATION_PROPERTY_OF_AXIOM,
        "DataPropertyAssertion": Feature.QUERY_DATA_PROPERTY_ASSERTION_AXIOM,
        "NegativeDataPropertyAssertion": Feature.QUERY_NEGATIVE_DATA_PROPERTY_ASSERTION_AXIOM,
        "NegativeObjectPropertyAssertion": Feature.QUERY_NEGATIVE_OBJECT_PROPERTY_ASSERTION_AXIOM,
        "DisjointUnion": Feature.QUERY_DISJOINT_UNION_AXIOM,
        "DataPropertyDomain": Feature.QUERY_DATA_PROPERTY_DOMAIN_AXIOM,
        "DataPropertyRange": Feature.QUERY_DATA_PROPERTY_RANGE_AXIOM,
        "DisjointDataProperties": Feature.QUERY_DISJOINT_DATA_PROPERTIES_AXIOM,
        "EquivalentDataProperties": Feature.QUERY_EQUIVALENT_DATA_PROPERTIES_AXIOM,
        "FunctionalDataProperty": Feature.QUERY_FUNCTIONAL_DATA_PROPERTY_AXIOM,
        "SubDataPropertyOf": Feature.QUERY_SUB_DATA_PROPERTY_OF_AXIOM,
        "DatatypeDefinition": Feature.QUERY_DATATYPE_DEFINITION_AXIOM,
        "Declaration": Feature.QUERY_DECLARATION_AXIOM,
        "HasKey": Feature.QUERY_HAS_KEY_AXIOM,
        "AsymmetricObjectProperty": Feature.QUERY_ASYMMETRIC_OBJECT_PROPERTY_AXIOM,
        "DisjointObjectProperties": Feature.QUERY_DISJOINT_OBJECT_PROPERTIES_AXIOM,
        "EquivalentObjectProperties": Feature.QUERY_EQUIVALENT_OBJECT_PROPERTIES_AXIOM,
        "FunctionalObjectProperty": Feature.QUERY_FUNCTIONAL_OBJECT_PROPERTY_AXIOM,
        "InverseFunctionalObjectProperty": Feature.QUERY_INVERSE_FUNCTIONAL_OBJECT_PROPERTY_AXIOM,
        "InverseObjectProperties": Feature.QUERY_INVERSE_OBJECT_PROPERTIES_AXIOM,
        "IrreflexiveObjectProperty": Feature.QUERY_IRREFLEXIVE_OBJECT_PROPERTY_AXIOM,
        "ObjectPropertyRange": Feature.QUERY_OBJECT_PROPERTY_RANGE_AXIOM,
        "ReflexiveObjectProperty": Feature.QUERY_REFLEXIVE_OBJECT_PROPERTY_AXIOM,
        "SubObjectPropertyOf": Feature.QUERY_SUB_OBJECT_PROPERTY_OF_AXIOM,
        "SymmetricObjectProperty": Feature.QUERY_SYMMETRIC_OBJECT_PROPERTY_AXIOM,
        "TransitiveObjectProperty": Feature.QUERY_TRANSITIVE_OBJECT_PROPERTY_AXIOM,
        "SWRL": Feature.QUERY_SWRL_RULE,
    }
)

_QUERY_TASKS = frozenset({ReasoningTask.CLASS_EXPRESSION_QUERY, ReasoningTask.ENTAILMENT_QUERY})
_QUIET_COLLAPSE_TASKS = frozenset(
    {
        ReasoningTask.CLASS_TAXONOMY,
        ReasoningTask.OBJECT_PROPERTY_TAXONOMY,
        ReasoningTask.REALIZATION,
        ReasoningTask.CLASS_EXPRESSION_QUERY,
    }
)


def _validate_counts(values: Sequence[int], field: str) -> tuple[int, ...]:
    if isinstance(values, (str, bytes, bytearray)) or len(values) != FEATURE_VECTOR_LENGTH:
        raise ValueError(f"{field} must contain exactly {FEATURE_VECTOR_LENGTH} feature counts")
    result = tuple(values)
    if any(
        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= U64_MAX
        for value in result
    ):
        raise ValueError(f"{field} must contain unsigned 64-bit integers")
    return result


def _issue(task: ReasoningTask, features: tuple[Feature, ...]) -> CompletenessIssue:
    return CompletenessIssue(
        task=task,
        features=tuple(feature.name for feature in features),
        constructors=tuple(feature.constructor for feature in features),
        polarities=tuple(feature.polarity for feature in features),
    )


def _matching_issues(
    task: ReasoningTask,
    counts: tuple[int, ...],
    single_features: tuple[Feature, ...],
    combinations: tuple[tuple[Feature, ...], ...],
) -> tuple[CompletenessIssue, ...]:
    singles = (_issue(task, (feature,)) for feature in single_features if counts[feature] > 0)
    combined = (
        _issue(task, combination)
        for combination in combinations
        if all(counts[feature] > 0 for feature in combination)
    )
    return tuple((*singles, *combined))


def _general_issues(task: ReasoningTask, counts: tuple[int, ...]) -> tuple[CompletenessIssue, ...]:
    return _matching_issues(
        task,
        counts,
        GENERAL_INCOMPLETENESS_FEATURES,
        GENERAL_INCOMPLETENESS_COMBINATIONS,
    )


def _policy_issues(
    task: ReasoningTask, policy_features: Sequence[PolicyFeature]
) -> tuple[CompletenessIssue, ...]:
    issues: list[CompletenessIssue] = []
    seen: set[PolicyFeature] = set()
    for feature in policy_features:
        if not isinstance(feature, PolicyFeature):
            raise ValueError("policy_features must contain PolicyFeature values")
        if feature in seen:
            continue
        seen.add(feature)
        if feature is PolicyFeature.IGNORED_IMPORT:
            issues.append(
                CompletenessIssue(
                    task=task,
                    features=(feature.value,),
                    constructors=("Import",),
                    polarities=("ANY",),
                )
            )
    return tuple(issues)


def issues_for(
    task: ReasoningTask,
    feature_counts: Sequence[int],
    *,
    query_feature_counts: Sequence[int] = (),
    policy_features: Sequence[PolicyFeature] = (),
    inconsistent: bool = False,
) -> tuple[CompletenessIssue, ...]:
    """Return canonical ELK completeness reasons for one task.

    Query tasks compose the ontology monitor, unsupported-query monitor, and the
    top monitor over combined ontology/query occurrences exactly as pinned ELK does.
    Policy issues are deliberately outside the feature vectors and survive quiet
    inconsistent-ontology fallbacks.
    """

    if not isinstance(task, ReasoningTask):
        raise ValueError("task must be a ReasoningTask")
    if not isinstance(inconsistent, bool):
        raise ValueError("inconsistent must be a boolean")
    ontology_counts = _validate_counts(feature_counts, "feature_counts")

    if task in _QUERY_TASKS:
        query_counts = _validate_counts(query_feature_counts, "query_feature_counts")
    else:
        if len(query_feature_counts) != 0:
            raise ValueError("query_feature_counts are valid only for query tasks")
        query_counts = ()

    issues: list[CompletenessIssue] = list(_policy_issues(task, policy_features))
    if inconsistent and task in _QUIET_COLLAPSE_TASKS:
        return tuple(sorted(set(issues), key=_issue_sort_key))

    issues.extend(_general_issues(task, ontology_counts))

    if task is ReasoningTask.OBJECT_PROPERTY_TAXONOMY:
        issues.extend(
            _matching_issues(
                task,
                ontology_counts,
                OBJECT_PROPERTY_INCOMPLETENESS_FEATURES,
                OBJECT_PROPERTY_INCOMPLETENESS_COMBINATIONS,
            )
        )

    if task in _QUERY_TASKS:
        combined_counts = tuple(
            ontology_count + query_count
            for ontology_count, query_count in zip(ontology_counts, query_counts, strict=True)
        )
        issues.extend(_matching_issues(task, query_counts, QUERY_INCOMPLETENESS_FEATURES, ()))
        issues.extend(_general_issues(task, combined_counts))

    return tuple(sorted(set(issues), key=_issue_sort_key))


def _issue_sort_key(
    issue: CompletenessIssue,
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[Polarity, ...]]:
    return issue.task.value, issue.features, issue.constructors, issue.polarities


__all__ = [
    "GENERAL_INCOMPLETENESS_COMBINATIONS",
    "GENERAL_INCOMPLETENESS_FEATURES",
    "OBJECT_PROPERTY_INCOMPLETENESS_COMBINATIONS",
    "OBJECT_PROPERTY_INCOMPLETENESS_FEATURES",
    "QUERY_FEATURE_BY_AXIOM",
    "QUERY_INCOMPLETENESS_FEATURES",
    "Feature",
    "issues_for",
]
