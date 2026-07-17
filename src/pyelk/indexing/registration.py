"""Frozen ELK 0.6.0 rule-registration metadata.

The indexer does not execute inference rules.  It exposes this immutable, string-only
manifest so the Python and Rust engines can build identical dispatch tables from frozen IR
occurrences and conversion rows.  ``tests/data/manifests/registration.toml`` is the external
golden representation of the same rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final


class RegistrationSource(str, Enum):
    """Frozen-IR table that supplies a registration directive."""

    DISJOINT_GROUP = "disjoint_group"
    EQUIVALENT_CLASS_AXIOM = "equivalent_class_axiom"
    EXPRESSION = "expression"
    PROPERTY_CHAIN = "property_chain"
    PROPERTY_RANGE = "property_range"
    STATIC = "static"
    SUBCLASS_AXIOM = "subclass_axiom"
    SUBPROPERTY_AXIOM = "subproperty_axiom"


class OccurrenceTrigger(str, Enum):
    """Occurrence condition under which a directive is installed."""

    ALWAYS = "always"
    ANY = "any"
    NEGATIVE = "negative"
    POSITIVE = "positive"


@dataclass(frozen=True, slots=True)
class RuleRegistration:
    """One backend-neutral dispatch-registration directive."""

    key: str
    source: RegistrationSource
    occurrence: OccurrenceTrigger
    expression_tag: str
    anchor: str
    condition: str
    rule: str
    java_class: str
    java_path: str

    def __post_init__(self) -> None:
        for field_name in (
            "key",
            "expression_tag",
            "anchor",
            "condition",
            "rule",
            "java_class",
            "java_path",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a nonempty string")
        if not isinstance(self.source, RegistrationSource):
            raise TypeError("source must be RegistrationSource")
        if not isinstance(self.occurrence, OccurrenceTrigger):
            raise TypeError("occurrence must be OccurrenceTrigger")


_RULE_ROOT = "elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/saturation/rules"
_INDEX_ROOT = "elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/indexing/classes"


def _rule(
    key: str,
    source: RegistrationSource,
    occurrence: OccurrenceTrigger,
    expression_tag: str,
    anchor: str,
    condition: str,
    rule: str,
    package: str,
    *,
    java_class: str | None = None,
    indexing_class: bool = False,
) -> RuleRegistration:
    selected_class = java_class or rule
    root = _INDEX_ROOT if indexing_class else _RULE_ROOT
    return RuleRegistration(
        key=key,
        source=source,
        occurrence=occurrence,
        expression_tag=expression_tag,
        anchor=anchor,
        condition=condition,
        rule=rule,
        java_class=selected_class,
        java_path=(
            f"{root}/{package}/{selected_class}.java"
            if package
            else f"{root}/{selected_class}.java"
        ),
    )


RULE_REGISTRATIONS: Final = (
    _rule(
        "root-context-initialization",
        RegistrationSource.STATIC,
        OccurrenceTrigger.ALWAYS,
        "none",
        "global",
        "always",
        "RootContextInitializationRule",
        "contextinit",
    ),
    _rule(
        "owl-thing-context-initialization",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.NEGATIVE,
        "CLASS",
        "expression",
        "owl_thing",
        "OwlThingContextInitRule",
        "contextinit",
    ),
    _rule(
        "owl-nothing-decomposition",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.POSITIVE,
        "CLASS",
        "expression",
        "owl_nothing",
        "OwlNothingDecompositionRule",
        "subsumers",
    ),
    _rule(
        "intersection-from-first-conjunct",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.NEGATIVE,
        "OBJECT_INTERSECTION_OF",
        "argument_0",
        "always",
        "ObjectIntersectionFromFirstConjunctRule",
        "subsumers",
    ),
    _rule(
        "intersection-from-second-conjunct",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.NEGATIVE,
        "OBJECT_INTERSECTION_OF",
        "argument_1",
        "always",
        "ObjectIntersectionFromSecondConjunctRule",
        "subsumers",
    ),
    _rule(
        "intersection-decomposition",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.POSITIVE,
        "OBJECT_INTERSECTION_OF",
        "expression",
        "always",
        "IndexedObjectIntersectionOfDecomposition",
        "subsumers",
    ),
    _rule(
        "existential-filler-propagation",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.NEGATIVE,
        "OBJECT_SOME_VALUES_FROM",
        "argument_1",
        "always",
        "PropagationFromExistentialFillerRule",
        "subsumers",
    ),
    _rule(
        "existential-decomposition",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.POSITIVE,
        "OBJECT_SOME_VALUES_FROM",
        "expression",
        "always",
        "IndexedObjectSomeValuesFromDecomposition",
        "subsumers",
    ),
    _rule(
        "self-decomposition",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.POSITIVE,
        "OBJECT_HAS_SELF",
        "expression",
        "always",
        "IndexedObjectHasSelfDecomposition",
        "subsumers",
    ),
    _rule(
        "complement-decomposition",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.POSITIVE,
        "OBJECT_COMPLEMENT_OF",
        "expression",
        "always",
        "IndexedObjectComplementOfDecomposition",
        "subsumers",
    ),
    _rule(
        "complement-contradiction",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.POSITIVE,
        "OBJECT_COMPLEMENT_OF",
        "argument_0",
        "always",
        "ContradictionFromNegationRule",
        "subsumers",
    ),
    _rule(
        "union-from-disjunct",
        RegistrationSource.EXPRESSION,
        OccurrenceTrigger.NEGATIVE,
        "OBJECT_UNION_OF",
        "each_argument",
        "always",
        "ObjectUnionFromDisjunctRule",
        "subsumers",
    ),
    _rule(
        "superclass-from-subclass",
        RegistrationSource.SUBCLASS_AXIOM,
        OccurrenceTrigger.ALWAYS,
        "none",
        "sub_expression",
        "always",
        "SuperClassFromSubClassRule",
        "subsumers",
    ),
    _rule(
        "defined-class-decomposition",
        RegistrationSource.EQUIVALENT_CLASS_AXIOM,
        OccurrenceTrigger.ALWAYS,
        "none",
        "first_expression",
        "named_definition",
        "IndexedClassDecompositionRule",
        "subsumers",
    ),
    _rule(
        "class-from-definition",
        RegistrationSource.EQUIVALENT_CLASS_AXIOM,
        OccurrenceTrigger.ALWAYS,
        "none",
        "second_expression",
        "named_definition",
        "IndexedClassFromDefinitionRule",
        "subsumers",
    ),
    _rule(
        "equivalent-first-from-second",
        RegistrationSource.EQUIVALENT_CLASS_AXIOM,
        OccurrenceTrigger.ALWAYS,
        "none",
        "second_expression",
        "general_equivalence",
        "EquivalentClassFirstFromSecondRule",
        "subsumers",
    ),
    _rule(
        "equivalent-second-from-first",
        RegistrationSource.EQUIVALENT_CLASS_AXIOM,
        OccurrenceTrigger.ALWAYS,
        "none",
        "first_expression",
        "general_equivalence",
        "EquivalentClassSecondFromFirstRule",
        "subsumers",
    ),
    _rule(
        "disjoint-subsumer-from-member",
        RegistrationSource.DISJOINT_GROUP,
        OccurrenceTrigger.ALWAYS,
        "none",
        "each_member_position",
        "always",
        "DisjointSubsumerFromMemberRule",
        "subsumers",
    ),
    _rule(
        "object-property-told-range",
        RegistrationSource.PROPERTY_RANGE,
        OccurrenceTrigger.ALWAYS,
        "none",
        "property",
        "always",
        "ToldObjectPropertyRange",
        "",
        java_class="ModifiableIndexedObjectPropertyRangeAxiomImpl",
        indexing_class=True,
    ),
    _rule(
        "chain-told-super-property",
        RegistrationSource.SUBPROPERTY_AXIOM,
        OccurrenceTrigger.ALWAYS,
        "none",
        "sub_chain",
        "always",
        "ToldSuperObjectProperty",
        "",
        java_class="ModifiableIndexedSubObjectPropertyOfAxiomImpl",
        indexing_class=True,
    ),
    _rule(
        "property-told-sub-chain",
        RegistrationSource.SUBPROPERTY_AXIOM,
        OccurrenceTrigger.ALWAYS,
        "none",
        "super_property",
        "always",
        "ToldSubPropertyChain",
        "",
        java_class="ModifiableIndexedSubObjectPropertyOfAxiomImpl",
        indexing_class=True,
    ),
    _rule(
        "complex-chain-left-link",
        RegistrationSource.PROPERTY_CHAIN,
        OccurrenceTrigger.ANY,
        "none",
        "first_property",
        "complex_chain",
        "LeftChainLink",
        "",
        java_class="StructuralIndexedComplexPropertyChainEntryImpl",
        indexing_class=True,
    ),
    _rule(
        "complex-chain-right-link",
        RegistrationSource.PROPERTY_CHAIN,
        OccurrenceTrigger.ANY,
        "none",
        "suffix_chain",
        "complex_chain",
        "RightChainLink",
        "",
        java_class="StructuralIndexedComplexPropertyChainEntryImpl",
        indexing_class=True,
    ),
)

if tuple(sorted(row.key for row in RULE_REGISTRATIONS)) != tuple(
    sorted({row.key for row in RULE_REGISTRATIONS})
):
    raise RuntimeError("rule-registration keys must be unique")

REGISTRATION_BY_KEY: Final = MappingProxyType({row.key: row for row in RULE_REGISTRATIONS})


def registrations_for(
    source: RegistrationSource,
    *,
    expression_tag: str | None = None,
) -> tuple[RuleRegistration, ...]:
    """Select immutable rows for one IR table and optional expression tag."""

    if not isinstance(source, RegistrationSource):
        raise TypeError("source must be RegistrationSource")
    if expression_tag is not None and (not isinstance(expression_tag, str) or not expression_tag):
        raise ValueError("expression_tag must be a nonempty string or None")
    return tuple(
        row
        for row in RULE_REGISTRATIONS
        if row.source is source and (expression_tag is None or row.expression_tag == expression_tag)
    )


__all__ = [
    "REGISTRATION_BY_KEY",
    "RULE_REGISTRATIONS",
    "OccurrenceTrigger",
    "RegistrationSource",
    "RuleRegistration",
    "registrations_for",
]
