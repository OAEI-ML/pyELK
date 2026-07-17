"""ELK 0.6.0 structural and axiom conversion over exact pyowl-core values.

The implementation is a clean-room translation of the observable conversion behavior in
ELK's Apache-2.0 ``ElkPolarityExpressionConverterImpl``, ``ElkEntityConverterImpl``,
``ElkAxiomConverterImpl``, and ``EntailmentQueryConverter``.  It keeps conversion iterative
and transaction-local; no Java code or Java artifact is used at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

import pyowl_core as owl
from pyowl_core.extensions.swrl import SWRLRule

from pyelk.indexing.builder import IndexTransaction
from pyelk.indexing.ir import EntityKind, EntityRecord, ExpressionTag
from pyelk.indexing.polarity import IndexPolarity

ONTOLOGY_FEATURE_NAMES: Final = (
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
)

QUERY_FEATURE_NAMES: Final = (
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

FEATURE_INDEX: Final = {
    name: index for index, name in enumerate((*ONTOLOGY_FEATURE_NAMES, *QUERY_FEATURE_NAMES))
}

_CORE_TO_IR_KIND: Final = {
    owl.EntityKind.CLASS: EntityKind.CLASS,
    owl.EntityKind.NAMED_INDIVIDUAL: EntityKind.NAMED_INDIVIDUAL,
    owl.EntityKind.OBJECT_PROPERTY: EntityKind.OBJECT_PROPERTY,
    owl.EntityKind.DATA_PROPERTY: EntityKind.DATA_PROPERTY,
    owl.EntityKind.DATATYPE: EntityKind.DATATYPE,
    owl.EntityKind.ANNOTATION_PROPERTY: EntityKind.ANNOTATION_PROPERTY,
}

_UNSUPPORTED_EXPRESSION_FEATURE: Final = {
    owl.ObjectAllValuesFrom: "OBJECT_ALL_VALUES_FROM",
    owl.ObjectExactCardinality: "OBJECT_EXACT_CARDINALITY",
    owl.ObjectMaxCardinality: "OBJECT_MAX_CARDINALITY",
    owl.ObjectMinCardinality: "OBJECT_MIN_CARDINALITY",
    owl.DataAllValuesFrom: "DATA_ALL_VALUES_FROM",
    owl.DataExactCardinality: "DATA_EXACT_CARDINALITY",
    owl.DataMaxCardinality: "DATA_MAX_CARDINALITY",
    owl.DataMinCardinality: "DATA_MIN_CARDINALITY",
    owl.DataSomeValuesFrom: "DATA_SOME_VALUES_FROM",
}

_UNSUPPORTED_AXIOM_FEATURE: Final = {
    owl.AsymmetricObjectProperty: "ASYMMETRIC_OBJECT_PROPERTY",
    owl.DataPropertyAssertion: "DATA_PROPERTY_ASSERTION",
    owl.DataPropertyDomain: "DATA_PROPERTY_DOMAIN",
    owl.DataPropertyRange: "DATA_PROPERTY_RANGE",
    owl.DatatypeDefinition: "DATATYPE_DEFINITION",
    owl.DisjointDataProperties: "DISJOINT_DATA_PROPERTIES",
    owl.DisjointObjectProperties: "DISJOINT_OBJECT_PROPERTIES",
    owl.EquivalentDataProperties: "EQUIVALENT_DATA_PROPERTIES",
    owl.FunctionalDataProperty: "FUNCTIONAL_DATA_PROPERTY",
    owl.FunctionalObjectProperty: "FUNCTIONAL_OBJECT_PROPERTY",
    owl.HasKey: "HAS_KEY",
    owl.InverseFunctionalObjectProperty: "INVERSE_FUNCTIONAL_OBJECT_PROPERTY",
    owl.InverseObjectProperties: "INVERSE_OBJECT_PROPERTIES",
    owl.IrreflexiveObjectProperty: "IRREFLEXIVE_OBJECT_PROPERTY",
    owl.NegativeDataPropertyAssertion: "NEGATIVE_DATA_PROPERTY_ASSERTION",
    owl.NegativeObjectPropertyAssertion: "NEGATIVE_OBJECT_PROPERTY_ASSERTION",
    owl.SubDataPropertyOf: "SUB_DATA_PROPERTY_OF",
    owl.SymmetricObjectProperty: "SYMMETRIC_OBJECT_PROPERTY",
}

_UNSUPPORTED_QUERY_FEATURE: Final = {
    owl.AnnotationAssertion: "QUERY_ANNOTATION_ASSERTION_AXIOM",
    owl.AnnotationPropertyDomain: "QUERY_ANNOTATION_PROPERTY_DOMAIN_AXIOM",
    owl.AnnotationPropertyRange: "QUERY_ANNOTATION_PROPERTY_RANGE_AXIOM",
    owl.SubAnnotationPropertyOf: "QUERY_SUB_ANNOTATION_PROPERTY_OF_AXIOM",
    owl.DataPropertyAssertion: "QUERY_DATA_PROPERTY_ASSERTION_AXIOM",
    owl.NegativeDataPropertyAssertion: "QUERY_NEGATIVE_DATA_PROPERTY_ASSERTION_AXIOM",
    owl.NegativeObjectPropertyAssertion: "QUERY_NEGATIVE_OBJECT_PROPERTY_ASSERTION_AXIOM",
    owl.DisjointUnion: "QUERY_DISJOINT_UNION_AXIOM",
    owl.DataPropertyDomain: "QUERY_DATA_PROPERTY_DOMAIN_AXIOM",
    owl.DataPropertyRange: "QUERY_DATA_PROPERTY_RANGE_AXIOM",
    owl.DisjointDataProperties: "QUERY_DISJOINT_DATA_PROPERTIES_AXIOM",
    owl.EquivalentDataProperties: "QUERY_EQUIVALENT_DATA_PROPERTIES_AXIOM",
    owl.FunctionalDataProperty: "QUERY_FUNCTIONAL_DATA_PROPERTY_AXIOM",
    owl.SubDataPropertyOf: "QUERY_SUB_DATA_PROPERTY_OF_AXIOM",
    owl.DatatypeDefinition: "QUERY_DATATYPE_DEFINITION_AXIOM",
    owl.Declaration: "QUERY_DECLARATION_AXIOM",
    owl.HasKey: "QUERY_HAS_KEY_AXIOM",
    owl.AsymmetricObjectProperty: "QUERY_ASYMMETRIC_OBJECT_PROPERTY_AXIOM",
    owl.DisjointObjectProperties: "QUERY_DISJOINT_OBJECT_PROPERTIES_AXIOM",
    owl.EquivalentObjectProperties: "QUERY_EQUIVALENT_OBJECT_PROPERTIES_AXIOM",
    owl.FunctionalObjectProperty: "QUERY_FUNCTIONAL_OBJECT_PROPERTY_AXIOM",
    owl.InverseFunctionalObjectProperty: "QUERY_INVERSE_FUNCTIONAL_OBJECT_PROPERTY_AXIOM",
    owl.InverseObjectProperties: "QUERY_INVERSE_OBJECT_PROPERTIES_AXIOM",
    owl.IrreflexiveObjectProperty: "QUERY_IRREFLEXIVE_OBJECT_PROPERTY_AXIOM",
    owl.ObjectPropertyRange: "QUERY_OBJECT_PROPERTY_RANGE_AXIOM",
    owl.ReflexiveObjectProperty: "QUERY_REFLEXIVE_OBJECT_PROPERTY_AXIOM",
    owl.SubObjectPropertyOf: "QUERY_SUB_OBJECT_PROPERTY_OF_AXIOM",
    owl.SymmetricObjectProperty: "QUERY_SYMMETRIC_OBJECT_PROPERTY_AXIOM",
    owl.TransitiveObjectProperty: "QUERY_TRANSITIVE_OBJECT_PROPERTY_AXIOM",
}


class UnsupportedConstruct(Exception):
    """Internal transactional signal carrying one exact pinned feature."""

    def __init__(self, feature: str) -> None:
        self.feature = feature
        self.index = FEATURE_INDEX[feature]
        super().__init__(feature)


class LiteralCompatibilityMode(str, Enum):
    """How the private pinned-ELK language spelling was obtained."""

    CANONICAL_FALLBACK = "canonical-fallback"
    SOURCE_MAP = "source-map"


@dataclass(frozen=True, slots=True)
class ElkCompatibilityKey:
    """Private flat key reproducing ELK literal structural equality."""

    payload: bytes
    observation: bytes
    mode: LiteralCompatibilityMode

    def __post_init__(self) -> None:
        if not self.payload or not self.observation:
            raise ValueError("ELK compatibility keys must be nonempty")


def literal_compatibility_key(
    literal: owl.Literal,
    *,
    source_language: str | None = None,
) -> ElkCompatibilityKey:
    """Derive ELK's stored lexical/datatype pair without mutating the core literal.

    ELK's Functional Syntax parser stores an untyped literal as ``lexical + '@'`` and a
    language literal as ``lexical + '@' + tag`` with ``rdf:PlainLiteral``.  pyowl-core
    correctly keeps lexical text and language separately, so this key is compiler-private.
    """

    if not isinstance(literal, owl.Literal):
        raise TypeError("literal must be pyowl_core.Literal")
    language = literal.language
    selected_language: str | None
    if source_language is not None:
        if language is None:
            raise ValueError("source language spelling requires a language literal")
        if source_language.lower() != language:
            raise ValueError("source language spelling is not equivalent to the core language")
        selected_language = source_language
        mode = LiteralCompatibilityMode.SOURCE_MAP
    else:
        selected_language = language
        mode = LiteralCompatibilityMode.CANONICAL_FALLBACK
    lexical = literal.lexical_form
    if literal.datatype.iri.value == owl.RDF_PLAIN_LITERAL_IRI:
        lexical = lexical + "@" + (selected_language or "")
    lexical_bytes = lexical.encode("utf-8")
    datatype_bytes = literal.datatype.iri.value.encode("utf-8")
    payload = (
        b"pyelk:elk-literal-key:v1\x00"
        + len(lexical_bytes).to_bytes(8, "big")
        + lexical_bytes
        + len(datatype_bytes).to_bytes(8, "big")
        + datatype_bytes
    )
    mode_bytes = mode.value.encode("ascii")
    observation = (
        b"pyelk:elk-literal-spelling:v1\x00"
        + len(mode_bytes).to_bytes(8, "big")
        + mode_bytes
        + len(payload).to_bytes(8, "big")
        + payload
    )
    return ElkCompatibilityKey(payload, observation, mode)


LiteralKeyProvider = Callable[[owl.Literal], ElkCompatibilityKey]


@dataclass(frozen=True, slots=True)
class _Visit:
    value: object
    polarity: IndexPolarity


@dataclass(frozen=True, slots=True)
class _Finish:
    operation: str
    polarity: IndexPolarity
    child_count: int
    prop: EntityRecord | None = None


class ExpressionConverter:
    """Iterative positive/negative/dual converter with exact occurrence updates."""

    __slots__ = ("literal_key", "node_limit", "transaction")

    def __init__(
        self,
        transaction: IndexTransaction,
        *,
        literal_key: LiteralKeyProvider = literal_compatibility_key,
        node_limit: int = 1_000_000,
    ) -> None:
        if isinstance(node_limit, bool) or not isinstance(node_limit, int) or node_limit < 1:
            raise ValueError("node_limit must be a positive integer")
        self.transaction = transaction
        self.literal_key = literal_key
        self.node_limit = node_limit

    def convert(self, expression: object, polarity: IndexPolarity) -> int:
        tasks: list[_Visit | _Finish] = [_Visit(expression, polarity)]
        results: list[int] = []
        observed = 0
        while tasks:
            task = tasks.pop()
            if isinstance(task, _Finish):
                children = results[-task.child_count :] if task.child_count else []
                if task.child_count:
                    del results[-task.child_count :]
                results.append(self._finish(task, children))
                continue
            observed += 1
            if observed > self.node_limit:
                raise ValueError(
                    f"class expression exceeds the {self.node_limit} node safety ceiling"
                )
            value = task.value
            current = task.polarity
            if isinstance(value, owl.Class):
                results.append(self._class(value, current))
            elif isinstance(value, owl.NamedIndividual):
                results.append(self._individual(value, current))
            elif isinstance(value, owl.AnonymousIndividual):
                raise UnsupportedConstruct("ANONYMOUS_INDIVIDUAL")
            elif isinstance(value, owl.ObjectIntersectionOf):
                intersection_children = tuple(value.operands)
                tasks.append(_Finish("intersection", current, len(intersection_children)))
                tasks.extend(_Visit(child, current) for child in reversed(intersection_children))
            elif isinstance(value, owl.ObjectUnionOf):
                union_children = tuple(value.operands)
                tasks.append(_Finish("union", current, len(union_children)))
                tasks.extend(_Visit(child, current) for child in reversed(union_children))
            elif isinstance(value, owl.ObjectComplementOf):
                tasks.append(_Finish("complement", current, 1))
                tasks.append(_Visit(value.operand, current.complementary()))
            elif isinstance(value, owl.ObjectOneOf):
                one_of_children = tuple(value.individuals)
                if one_of_children:
                    self.transaction.add_feature(FEATURE_INDEX["OBJECT_ONE_OF"])
                tasks.append(_Finish("one_of", current, len(one_of_children)))
                tasks.extend(_Visit(child, current) for child in reversed(one_of_children))
            elif isinstance(value, owl.ObjectSomeValuesFrom):
                prop = self.object_property(value.property, current)
                tasks.append(_Finish("some", current, 1, prop))
                tasks.append(_Visit(value.filler, current))
            elif isinstance(value, owl.ObjectHasValue):
                prop = self.object_property(value.property, current)
                tasks.append(_Finish("some", current, 1, prop))
                tasks.append(_Visit(value.value, current))
            elif isinstance(value, owl.ObjectHasSelf):
                prop = self.object_property(value.property, current)
                handle = self.transaction.intern_expression(
                    ExpressionTag.OBJECT_HAS_SELF,
                    entities=(prop,),
                    polarity=current,
                )
                if current.negative:
                    self.transaction.add_feature(FEATURE_INDEX["OBJECT_HAS_SELF_NEGATIVE"])
                results.append(handle)
            elif isinstance(value, owl.DataHasValue):
                prop = entity_record(value.property)
                key = self.literal_key(value.value)
                self.transaction.add_entity(prop)
                self.transaction.add_feature(FEATURE_INDEX["DATA_HAS_VALUE"])
                self.transaction.observe_compatibility_spelling(key.observation)
                results.append(
                    self.transaction.intern_expression(
                        ExpressionTag.DATA_HAS_VALUE,
                        entities=(prop,),
                        payload=key.payload,
                        polarity=current,
                    )
                )
            else:
                feature = _UNSUPPORTED_EXPRESSION_FEATURE.get(type(value))
                if feature is not None:
                    raise UnsupportedConstruct(feature)
                raise TypeError(
                    f"unsupported pyowl-core class expression type: {type(value).__name__}"
                )
        if len(results) != 1:
            raise AssertionError("expression conversion did not produce exactly one root")
        return results[0]

    def _finish(self, task: _Finish, children: list[int]) -> int:
        if task.operation == "intersection":
            if not children:
                return self._class(owl.OWL_THING, task.polarity)
            result = children[0]
            for child in children[1:]:
                result = self.transaction.intern_expression(
                    ExpressionTag.OBJECT_INTERSECTION_OF,
                    expressions=(result, child),
                    polarity=task.polarity,
                )
            return result
        if task.operation in {"union", "one_of"}:
            if not children:
                return self._class(owl.OWL_NOTHING, task.polarity)
            if len(children) == 1:
                return children[0]
            result = self.transaction.intern_expression(
                ExpressionTag.OBJECT_UNION_OF,
                expressions=tuple(children),
                polarity=task.polarity,
            )
            if task.polarity.positive:
                self.transaction.add_feature(FEATURE_INDEX["OBJECT_UNION_OF_POSITIVE"])
            return result
        if task.operation == "complement":
            result = self.transaction.intern_expression(
                ExpressionTag.OBJECT_COMPLEMENT_OF,
                expressions=(children[0],),
                polarity=task.polarity,
            )
            if task.polarity.negative:
                self.transaction.add_feature(FEATURE_INDEX["OBJECT_COMPLEMENT_OF_NEGATIVE"])
            if task.polarity.positive:
                self.transaction.add_feature(FEATURE_INDEX["OBJECT_COMPLEMENT_OF_POSITIVE"])
            return result
        if task.operation == "some":
            if task.prop is None:
                raise AssertionError("existential conversion lost its property")
            result = self.transaction.intern_expression(
                ExpressionTag.OBJECT_SOME_VALUES_FROM,
                entities=(task.prop,),
                expressions=(children[0],),
                polarity=task.polarity,
            )
            if (
                task.polarity.positive
                and self.transaction.expression_tag(children[0]) is ExpressionTag.INDIVIDUAL
            ):
                self.transaction.add_feature(FEATURE_INDEX["OBJECT_HAS_VALUE_POSITIVE"])
            return result
        raise AssertionError(f"unknown expression finish operation: {task.operation}")

    def _class(self, value: owl.Class, polarity: IndexPolarity) -> int:
        record = entity_record(value)
        handle = self.transaction.intern_expression(
            ExpressionTag.CLASS,
            entities=(record,),
            polarity=polarity,
        )
        if value == owl.OWL_NOTHING and polarity.positive:
            self.transaction.add_feature(FEATURE_INDEX["OWL_NOTHING_POSITIVE"])
        return handle

    def _individual(self, value: owl.NamedIndividual, polarity: IndexPolarity) -> int:
        return self.transaction.intern_expression(
            ExpressionTag.INDIVIDUAL,
            entities=(entity_record(value),),
            polarity=polarity,
        )

    def object_property(
        self,
        value: object,
        polarity: IndexPolarity,
    ) -> EntityRecord:
        if isinstance(value, owl.ObjectInverseOf):
            raise UnsupportedConstruct("OBJECT_INVERSE_OF")
        if not isinstance(value, owl.ObjectProperty):
            raise TypeError(f"expected object property, got {type(value).__name__}")
        record = entity_record(value)
        self.transaction.record_object_property(record, polarity)
        if value == owl.OWL_BOTTOM_OBJECT_PROPERTY and polarity.positive:
            self.transaction.add_feature(FEATURE_INDEX["BOTTOM_OBJECT_PROPERTY_POSITIVE"])
        if value == owl.OWL_TOP_OBJECT_PROPERTY and polarity.negative:
            self.transaction.add_feature(FEATURE_INDEX["TOP_OBJECT_PROPERTY_NEGATIVE"])
        return record


def entity_record(value: owl.Entity) -> EntityRecord:
    """Convert an exact core entity into its frozen IR identity."""

    if not isinstance(value, owl.Entity):
        raise TypeError("value must be a pyowl_core Entity")
    return EntityRecord(_CORE_TO_IR_KIND[value.kind], value.iri.value)


class AxiomConverter:
    """Transactional ontology axiom conversion into normalized IR tables."""

    __slots__ = ("expressions", "transaction")

    def __init__(
        self,
        transaction: IndexTransaction,
        *,
        literal_key: LiteralKeyProvider = literal_compatibility_key,
        node_limit: int = 1_000_000,
    ) -> None:
        self.transaction = transaction
        self.expressions = ExpressionConverter(
            transaction,
            literal_key=literal_key,
            node_limit=node_limit,
        )

    def convert(self, axiom: object) -> None:
        unsupported = _UNSUPPORTED_AXIOM_FEATURE.get(type(axiom))
        if unsupported is not None:
            raise UnsupportedConstruct(unsupported)
        if isinstance(axiom, owl.Declaration):
            self._declaration(axiom)
        elif isinstance(axiom, owl.SubClassOf):
            self.transaction.add_subclass(
                self.expressions.convert(axiom.sub_class, IndexPolarity.NEGATIVE),
                self.expressions.convert(axiom.super_class, IndexPolarity.POSITIVE),
            )
        elif isinstance(axiom, owl.EquivalentClasses):
            self._equivalent_classes(tuple(axiom.expressions))
        elif isinstance(axiom, owl.DisjointClasses):
            self._disjoint(tuple(axiom.expressions))
            self.transaction.add_feature(FEATURE_INDEX["DISJOINT_CLASSES"])
        elif isinstance(axiom, owl.DisjointUnion):
            self._disjoint_union(axiom)
        elif isinstance(axiom, owl.ClassAssertion):
            self.transaction.add_subclass(
                self.expressions.convert(axiom.individual, IndexPolarity.NEGATIVE),
                self.expressions.convert(axiom.class_expression, IndexPolarity.POSITIVE),
            )
        elif isinstance(axiom, owl.SameIndividual):
            self._same_individual(tuple(axiom.individuals))
        elif isinstance(axiom, owl.DifferentIndividuals):
            self._disjoint(tuple(axiom.individuals))
            self.transaction.add_feature(FEATURE_INDEX["DIFFERENT_INDIVIDUALS"])
        elif isinstance(axiom, owl.ObjectPropertyAssertion):
            self._object_property_assertion(axiom)
        elif isinstance(axiom, owl.EquivalentObjectProperties):
            self._equivalent_object_properties(tuple(axiom.properties))
        elif isinstance(axiom, owl.SubObjectPropertyOf):
            sub_chain = self._property_chain(axiom.sub_property)
            super_property = self.expressions.object_property(
                axiom.super_property, IndexPolarity.POSITIVE
            )
            self.transaction.add_subproperty(sub_chain, super_property)
        elif isinstance(axiom, owl.ObjectPropertyDomain):
            prop = self.expressions.object_property(axiom.property, IndexPolarity.NEGATIVE)
            thing = self.expressions.convert(owl.OWL_THING, IndexPolarity.NEGATIVE)
            existential = self.transaction.intern_expression(
                ExpressionTag.OBJECT_SOME_VALUES_FROM,
                entities=(prop,),
                expressions=(thing,),
                polarity=IndexPolarity.NEGATIVE,
            )
            domain = self.expressions.convert(axiom.domain, IndexPolarity.POSITIVE)
            self.transaction.add_subclass(existential, domain)
        elif isinstance(axiom, owl.ObjectPropertyRange):
            prop = self.expressions.object_property(axiom.property, IndexPolarity.NEGATIVE)
            range_expression = self.expressions.convert(axiom.range, IndexPolarity.POSITIVE)
            self.transaction.add_property_range(prop, range_expression)
            self.transaction.add_feature(FEATURE_INDEX["OBJECT_PROPERTY_RANGE"])
        elif isinstance(axiom, owl.ReflexiveObjectProperty):
            self._reflexive(axiom)
        elif isinstance(axiom, owl.TransitiveObjectProperty):
            self._transitive(axiom)
        elif isinstance(axiom, owl.ANNOTATION_AXIOM_TYPES):
            return
        else:
            raise TypeError(f"unsupported pyowl-core axiom type: {type(axiom).__name__}")

    def _declaration(self, axiom: owl.Declaration) -> None:
        entity = axiom.entity
        if isinstance(entity, (owl.Class, owl.NamedIndividual, owl.ObjectProperty)):
            record = entity_record(entity)
            self.transaction.add_entity(record)
            if isinstance(entity, owl.ObjectProperty):
                self.transaction.add_property_chain((record,))
            return
        if isinstance(entity, owl.AnnotationProperty):
            return
        if isinstance(entity, owl.DataProperty):
            raise UnsupportedConstruct("DATA_PROPERTY")
        if isinstance(entity, owl.Datatype):
            raise UnsupportedConstruct("DATATYPE")
        raise TypeError(f"unsupported declaration entity: {type(entity).__name__}")

    def _equivalent_classes(self, members: tuple[object, ...]) -> None:
        first: int | None = None
        first_is_class = False
        for member in members:
            converted = self.expressions.convert(member, IndexPolarity.DUAL)
            if first is None:
                first = converted
                first_is_class = self.transaction.expression_tag(converted) is ExpressionTag.CLASS
            elif (
                not first_is_class
                and self.transaction.expression_tag(converted) is ExpressionTag.CLASS
            ):
                self.transaction.add_equivalent_class(converted, first)
            else:
                self.transaction.add_equivalent_class(first, converted)

    def _disjoint(self, members: tuple[object, ...]) -> None:
        if len(members) > 2:
            converted = tuple(
                self.expressions.convert(member, IndexPolarity.NEGATIVE) for member in members
            )
            self.transaction.add_disjoint_group(converted)
            return
        bottom = self.expressions.convert(owl.OWL_NOTHING, IndexPolarity.POSITIVE)
        for first_position, first in enumerate(members):
            first_expression = self.expressions.convert(first, IndexPolarity.NEGATIVE)
            for second in members[first_position + 1 :]:
                second_expression = self.expressions.convert(second, IndexPolarity.NEGATIVE)
                conjunction = self.transaction.intern_expression(
                    ExpressionTag.OBJECT_INTERSECTION_OF,
                    expressions=(first_expression, second_expression),
                    polarity=IndexPolarity.NEGATIVE,
                )
                self.transaction.add_subclass(conjunction, bottom)

    def _disjoint_union(self, axiom: owl.DisjointUnion) -> None:
        members = tuple(axiom.expressions)
        self._disjoint(members)
        if not members:
            defined = self.expressions.convert(axiom.defined_class, IndexPolarity.POSITIVE)
            bottom = self.expressions.convert(owl.OWL_NOTHING, IndexPolarity.POSITIVE)
            self.transaction.add_equivalent_class(defined, bottom)
        elif len(members) == 1:
            defined = self.expressions.convert(axiom.defined_class, IndexPolarity.DUAL)
            singleton = self.expressions.convert(members[0], IndexPolarity.DUAL)
            self.transaction.add_equivalent_class(defined, singleton)
        else:
            self.transaction.add_feature(FEATURE_INDEX["DISJOINT_UNION"])
            defined = self.expressions.convert(axiom.defined_class, IndexPolarity.POSITIVE)
            for member in members:
                converted = self.expressions.convert(member, IndexPolarity.NEGATIVE)
                self.transaction.add_subclass(converted, defined)

    def _same_individual(self, members: tuple[object, ...]) -> None:
        first: int | None = None
        for member in members:
            converted = self.expressions.convert(member, IndexPolarity.DUAL)
            if first is None:
                first = converted
            else:
                self.transaction.add_subclass(first, converted)
                self.transaction.add_subclass(converted, first)

    def _object_property_assertion(self, axiom: owl.ObjectPropertyAssertion) -> None:
        self.transaction.add_feature(FEATURE_INDEX["OBJECT_PROPERTY_ASSERTION"])
        source = self.expressions.convert(axiom.source, IndexPolarity.NEGATIVE)
        prop = self.expressions.object_property(axiom.property, IndexPolarity.POSITIVE)
        target = self.expressions.convert(axiom.target, IndexPolarity.POSITIVE)
        existential = self.transaction.intern_expression(
            ExpressionTag.OBJECT_SOME_VALUES_FROM,
            entities=(prop,),
            expressions=(target,),
            polarity=IndexPolarity.POSITIVE,
        )
        self.transaction.add_feature(FEATURE_INDEX["OBJECT_HAS_VALUE_POSITIVE"])
        self.transaction.add_subclass(source, existential)

    def _equivalent_object_properties(self, members: tuple[object, ...]) -> None:
        first: EntityRecord | None = None
        for member in members:
            converted = self.expressions.object_property(member, IndexPolarity.DUAL)
            if first is None:
                first = converted
            else:
                self.transaction.add_subproperty((first,), converted)
                self.transaction.add_subproperty((converted,), first)

    def _property_chain(self, value: object) -> tuple[EntityRecord, ...]:
        if isinstance(value, owl.ObjectProperty):
            prop = self.expressions.object_property(value, IndexPolarity.NEGATIVE)
            return (prop,)
        if isinstance(value, owl.ObjectInverseOf):
            raise UnsupportedConstruct("OBJECT_INVERSE_OF")
        if not isinstance(value, owl.ObjectPropertyChain):
            raise TypeError(f"unsupported sub-property expression: {type(value).__name__}")
        reversed_properties = tuple(
            self.expressions.object_property(prop, IndexPolarity.NEGATIVE)
            for prop in reversed(value.properties)
        )
        if len(reversed_properties) < 2:
            raise ValueError("object property chains must be nonempty and complex")
        self.transaction.add_feature(
            FEATURE_INDEX["OBJECT_PROPERTY_CHAIN"], len(reversed_properties) - 1
        )
        return tuple(reversed(reversed_properties))

    def _reflexive(self, axiom: owl.ReflexiveObjectProperty) -> None:
        self.transaction.add_feature(FEATURE_INDEX["REFLEXIVE_OBJECT_PROPERTY"])
        thing = self.expressions.convert(owl.OWL_THING, IndexPolarity.NEGATIVE)
        prop = self.expressions.object_property(axiom.property, IndexPolarity.POSITIVE)
        has_self = self.transaction.intern_expression(
            ExpressionTag.OBJECT_HAS_SELF,
            entities=(prop,),
            polarity=IndexPolarity.POSITIVE,
        )
        self.transaction.add_subclass(thing, has_self)

    def _transitive(self, axiom: owl.TransitiveObjectProperty) -> None:
        prop = self.expressions.object_property(axiom.property, IndexPolarity.DUAL)
        self.transaction.add_feature(FEATURE_INDEX["OBJECT_PROPERTY_CHAIN"])
        self.transaction.add_subproperty((prop, prop), prop)


def convert_entailment_obligations(
    transaction: IndexTransaction,
    axiom: object,
    *,
    literal_key: LiteralKeyProvider = literal_compatibility_key,
    node_limit: int = 1_000_000,
) -> set[tuple[int, int]]:
    """Convert one supported entailment query into canonical subsumption obligations."""

    unsupported = _UNSUPPORTED_QUERY_FEATURE.get(type(axiom))
    if unsupported is not None:
        raise UnsupportedConstruct(unsupported)
    if isinstance(axiom, SWRLRule):
        raise UnsupportedConstruct("QUERY_SWRL_RULE")
    converter = ExpressionConverter(
        transaction,
        literal_key=literal_key,
        node_limit=node_limit,
    )
    obligations: set[tuple[int, int]] = set()

    def subsumption(sub: object, sup: object) -> None:
        obligations.add(
            (
                converter.convert(sub, IndexPolarity.POSITIVE),
                converter.convert(sup, IndexPolarity.NEGATIVE),
            )
        )

    def nominal(individual: object, polarity: IndexPolarity) -> int:
        transaction.add_feature(FEATURE_INDEX["OBJECT_ONE_OF"])
        return converter.convert(individual, polarity)

    if isinstance(axiom, owl.SubClassOf):
        subsumption(axiom.sub_class, axiom.super_class)
    elif isinstance(axiom, owl.ClassAssertion):
        obligations.add(
            (
                converter.convert(axiom.individual, IndexPolarity.POSITIVE),
                converter.convert(axiom.class_expression, IndexPolarity.NEGATIVE),
            )
        )
    elif isinstance(axiom, owl.ObjectPropertyAssertion):
        source = converter.convert(axiom.source, IndexPolarity.POSITIVE)
        prop = converter.object_property(axiom.property, IndexPolarity.NEGATIVE)
        target = nominal(axiom.target, IndexPolarity.NEGATIVE)
        existential = transaction.intern_expression(
            ExpressionTag.OBJECT_SOME_VALUES_FROM,
            entities=(prop,),
            expressions=(target,),
            polarity=IndexPolarity.NEGATIVE,
        )
        obligations.add((source, existential))
    elif isinstance(axiom, owl.ObjectPropertyDomain):
        prop = converter.object_property(axiom.property, IndexPolarity.POSITIVE)
        thing = converter.convert(owl.OWL_THING, IndexPolarity.POSITIVE)
        existential = transaction.intern_expression(
            ExpressionTag.OBJECT_SOME_VALUES_FROM,
            entities=(prop,),
            expressions=(thing,),
            polarity=IndexPolarity.POSITIVE,
        )
        domain = converter.convert(axiom.domain, IndexPolarity.NEGATIVE)
        obligations.add((existential, domain))
    elif isinstance(axiom, owl.DisjointClasses):
        class_members = tuple(axiom.expressions)
        for first_position, first in enumerate(class_members[:-1]):
            for second in class_members[first_position + 1 :]:
                first_expression = converter.convert(first, IndexPolarity.POSITIVE)
                second_expression = converter.convert(second, IndexPolarity.POSITIVE)
                conjunction = transaction.intern_expression(
                    ExpressionTag.OBJECT_INTERSECTION_OF,
                    expressions=(first_expression, second_expression),
                    polarity=IndexPolarity.POSITIVE,
                )
                bottom = converter.convert(owl.OWL_NOTHING, IndexPolarity.NEGATIVE)
                obligations.add((conjunction, bottom))
    elif isinstance(axiom, owl.DifferentIndividuals):
        different_members = tuple(axiom.individuals)
        for first_position, individual_first in enumerate(different_members[:-1]):
            for individual_second in different_members[first_position + 1 :]:
                first_expression = nominal(individual_first, IndexPolarity.POSITIVE)
                second_expression = nominal(individual_second, IndexPolarity.POSITIVE)
                conjunction = transaction.intern_expression(
                    ExpressionTag.OBJECT_INTERSECTION_OF,
                    expressions=(first_expression, second_expression),
                    polarity=IndexPolarity.POSITIVE,
                )
                bottom = converter.convert(owl.OWL_NOTHING, IndexPolarity.NEGATIVE)
                obligations.add((conjunction, bottom))
    elif isinstance(axiom, owl.EquivalentClasses):
        equivalent_members = tuple(axiom.expressions)
        if not equivalent_members:
            raise ValueError("EquivalentClasses entailment requires at least one member")
        previous = equivalent_members[-1]
        for current in equivalent_members:
            subsumption(previous, current)
            previous = current
    elif isinstance(axiom, owl.SameIndividual):
        same_members = tuple(axiom.individuals)
        if not same_members:
            raise ValueError("SameIndividual entailment requires at least one member")
        previous_individual = same_members[-1]
        for current_individual in same_members:
            obligations.add(
                (
                    nominal(previous_individual, IndexPolarity.POSITIVE),
                    nominal(current_individual, IndexPolarity.NEGATIVE),
                )
            )
            previous_individual = current_individual
    else:
        raise TypeError(f"unsupported pyowl-core entailment query type: {type(axiom).__name__}")
    return obligations


def unsupported_query_feature(value: object) -> str | None:
    """Return the exact unsupported-family feature without converting nested values."""

    if isinstance(value, SWRLRule):
        return "QUERY_SWRL_RULE"
    return _UNSUPPORTED_QUERY_FEATURE.get(type(value))


__all__ = [
    "FEATURE_INDEX",
    "ONTOLOGY_FEATURE_NAMES",
    "QUERY_FEATURE_NAMES",
    "AxiomConverter",
    "ElkCompatibilityKey",
    "ExpressionConverter",
    "LiteralCompatibilityMode",
    "LiteralKeyProvider",
    "UnsupportedConstruct",
    "convert_entailment_obligations",
    "entity_record",
    "literal_compatibility_key",
    "unsupported_query_feature",
]
