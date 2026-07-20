"""Public ontology and query compilation entry points.

Compilation consumes an already captured pyowl-core view directly in canonical closure
order.  It never parses, renders, materializes an overlay/composite, or builds a second
collection of public axioms.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast, runtime_checkable

import pyowl_core as owl
from pyowl_core.extensions.swrl import SWRLRule

from pyelk.core import CapturedOntology, capture_compatible_view
from pyelk.exceptions import UnsupportedFeatureError, UnsupportedQueryError
from pyelk.indexing.builder import IndexTransaction, OntologyBuilder, QueryBuilder
from pyelk.indexing.conversion import (
    FEATURE_INDEX,
    AxiomConverter,
    ExpressionConverter,
    UnsupportedConstruct,
    convert_entailment_obligations,
    entity_record,
    literal_compatibility_key,
    unsupported_query_feature,
)
from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    U32_RESERVED,
    CompiledOntology,
    CompiledQuery,
    EntityId,
    EntityRecord,
    QueryIRKind,
)
from pyelk.indexing.polarity import IndexPolarity
from pyelk.inputs import semantic_cache_record

COMPILER_SCHEMA_VERSION = 1
ELK_COMPATIBILITY_ID = "elk-0.6.0:b8ac5ce83db0704a7359d96aa382891e2f547863"
DEFAULT_MAX_NODES_PER_AXIOM = 1_000_000


@runtime_checkable
class SymbolTableView(Protocol):
    """Minimal internal entity lookup needed by query compilation."""

    @property
    def entity_count(self) -> int: ...

    def lookup_entity(self, entity: EntityRecord) -> EntityId | None: ...


class CompiledSymbolTable:
    """Immutable lookup over one compiled ontology's canonical entity table."""

    __slots__ = ("_lookup", "entities")

    def __init__(self, compiled: CompiledOntology) -> None:
        if not isinstance(compiled, CompiledOntology):
            raise TypeError("compiled must be CompiledOntology")
        self.entities = compiled.entities
        self._lookup: Mapping[EntityRecord, EntityId] = MappingProxyType(
            {entity: EntityId(index) for index, entity in enumerate(compiled.entities)}
        )

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def lookup_entity(self, entity: EntityRecord) -> EntityId | None:
        if not isinstance(entity, EntityRecord):
            raise TypeError("entity must be EntityRecord")
        return self._lookup.get(entity)


def symbol_table(compiled: CompiledOntology) -> CompiledSymbolTable:
    """Create the immutable query lookup for a compiled ontology."""

    return CompiledSymbolTable(compiled)


def compile_ontology(
    ontology: owl.OntologyView,
    *,
    unsupported: Literal["ignore", "error"] = "ignore",
    max_nodes_per_axiom: int = DEFAULT_MAX_NODES_PER_AXIOM,
) -> CompiledOntology:
    """Compile one compatible core view into deterministic backend-neutral IR."""

    compiled, _materialized_scalar_rows = _compile_ontology_with_materialization_count(
        ontology,
        unsupported=unsupported,
        max_nodes_per_axiom=max_nodes_per_axiom,
    )
    return compiled


def _compile_ontology_with_materialization_count(
    ontology: owl.OntologyView,
    *,
    unsupported: Literal["ignore", "error"] = "ignore",
    max_nodes_per_axiom: int = DEFAULT_MAX_NODES_PER_AXIOM,
) -> tuple[CompiledOntology, int]:
    """Compile a scalar view and count every public row yielded to the consumer."""

    _validate_unsupported(unsupported)
    _validate_node_limit(max_nodes_per_axiom)
    captured = capture_compatible_view(ontology)
    view = captured.view
    builder = OntologyBuilder()
    annotated_logical_keys: set[bytes] = set()
    materialized_scalar_rows = 0

    for axiom in view.iter_axioms(scope=owl.AxiomScope.CLOSURE):
        materialized_scalar_rows += 1
        if isinstance(axiom, owl.ANNOTATION_AXIOM_TYPES):
            continue
        if _duplicate_annotated_axiom(view, axiom, annotated_logical_keys):
            continue
        transaction = IndexTransaction()
        try:
            AxiomConverter(
                transaction,
                literal_key=literal_compatibility_key,
                node_limit=max_nodes_per_axiom,
            ).convert(axiom)
        except UnsupportedConstruct as error:
            transaction.rollback()
            builder.add_feature(error.index)
            if unsupported == "error":
                raise UnsupportedFeatureError(error.feature, axiom) from None
        else:
            transaction.commit_into(builder)

    for extension in view.iter_extensions(scope=owl.AxiomScope.CLOSURE):
        materialized_scalar_rows += 1
        if isinstance(extension, SWRLRule):
            builder.add_feature(FEATURE_INDEX["SWRL_RULE"])
            if unsupported == "error":
                raise UnsupportedFeatureError("SWRL_RULE", extension)

    fingerprint = _source_fingerprint(
        captured,
        unsupported=unsupported,
        compatibility_spelling_digest=builder.compatibility_digest(),
    )
    return builder.freeze(fingerprint), materialized_scalar_rows


def compile_query_expression(
    expression: owl.ClassExpression,
    symbols: SymbolTableView | CompiledOntology,
    *,
    unsupported: Literal["ignore", "error"] = "ignore",
    max_nodes: int = DEFAULT_MAX_NODES_PER_AXIOM,
) -> CompiledQuery:
    """Compile one class expression as an isolated dual-polarity mini-IR."""

    _validate_unsupported(unsupported)
    _validate_node_limit(max_nodes)
    table = _coerce_symbol_table(symbols)
    records, ontology_ids, fresh = _query_entities(expression, table)
    transaction = IndexTransaction()
    transaction.entities.update(records)
    try:
        root = ExpressionConverter(
            transaction,
            literal_key=literal_compatibility_key,
            node_limit=max_nodes,
        ).convert(expression, IndexPolarity.DUAL)
    except UnsupportedConstruct as error:
        transaction.rollback()
        if unsupported == "error":
            raise UnsupportedQueryError(error.feature, expression) from None
        return CompiledQuery(None, _single_feature_vector(error.index), fresh)

    ontology_ids, fresh = _complete_query_entity_mapping(transaction, table, ontology_ids, fresh)
    builder = QueryBuilder()
    mapping = transaction.commit_into(builder)
    query = builder.freeze_query_ir(
        kind=QueryIRKind.CLASS_EXPRESSION,
        ontology_ids=ontology_ids,
        root_expression=mapping[root],
        obligations=set(),
    )
    return CompiledQuery(query.encode(), tuple(builder.feature_counts), fresh)


def compile_entailment_query(
    axiom: object,
    symbols: SymbolTableView | CompiledOntology,
    *,
    unsupported: Literal["ignore", "error"] = "ignore",
    max_nodes: int = DEFAULT_MAX_NODES_PER_AXIOM,
) -> CompiledQuery:
    """Compile one supported entailment axiom into normalized obligations."""

    _validate_unsupported(unsupported)
    _validate_node_limit(max_nodes)
    table = _coerce_symbol_table(symbols)
    records, ontology_ids, fresh = _query_entities(axiom, table)
    family_feature = unsupported_query_feature(axiom)
    if family_feature is not None:
        if unsupported == "error":
            raise UnsupportedQueryError(family_feature, axiom)
        return CompiledQuery(
            None,
            _single_feature_vector(FEATURE_INDEX[family_feature]),
            fresh,
        )

    transaction = IndexTransaction()
    transaction.entities.update(records)
    try:
        obligations = convert_entailment_obligations(
            transaction,
            axiom,
            literal_key=literal_compatibility_key,
            node_limit=max_nodes,
        )
    except UnsupportedConstruct as error:
        transaction.rollback()
        if unsupported == "error":
            raise UnsupportedQueryError(error.feature, axiom) from None
        return CompiledQuery(None, _single_feature_vector(error.index), fresh)

    ontology_ids, fresh = _complete_query_entity_mapping(transaction, table, ontology_ids, fresh)
    builder = QueryBuilder()
    mapping = transaction.commit_into(builder)
    query = builder.freeze_query_ir(
        kind=QueryIRKind.ENTAILMENT,
        ontology_ids=ontology_ids,
        root_expression=None,
        obligations={(mapping[first], mapping[second]) for first, second in obligations},
    )
    return CompiledQuery(query.encode(), tuple(builder.feature_counts), fresh)


def _query_entities(
    value: object,
    symbols: SymbolTableView,
) -> tuple[tuple[EntityRecord, ...], dict[EntityRecord, EntityId], tuple[EntityRecord, ...]]:
    if not isinstance(value, owl.StructuralNode):
        raise TypeError("query must be a pyowl-core structural value")
    records = tuple(
        sorted(
            (entity_record(entity) for entity in owl.signature(_without_annotations(value))),
            key=lambda item: (int(item.kind), item.iri.encode("utf-8")),
        )
    )
    ontology_ids: dict[EntityRecord, EntityId] = {}
    observed_ids: set[EntityId] = set()
    fresh: list[EntityRecord] = []
    for record in records:
        ontology_id = _lookup_entity(symbols, record)
        if ontology_id is None:
            fresh.append(record)
        else:
            if ontology_id in observed_ids:
                raise ValueError("symbol table maps distinct entities to one ontology ID")
            observed_ids.add(ontology_id)
            ontology_ids[record] = ontology_id
    return records, ontology_ids, tuple(fresh)


def _coerce_symbol_table(
    value: SymbolTableView | CompiledOntology,
) -> SymbolTableView:
    if isinstance(value, CompiledOntology):
        return CompiledSymbolTable(value)
    if not isinstance(value, SymbolTableView):
        raise TypeError("symbols must implement SymbolTableView or be CompiledOntology")
    count = value.entity_count
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("symbol-table entity_count must be an integer")
    if not 0 <= count <= U32_RESERVED:
        raise ValueError("symbol-table entity_count exceeds the frozen u32 namespace")
    return value


def _lookup_entity(
    symbols: SymbolTableView,
    record: EntityRecord,
) -> EntityId | None:
    # Treat protocol output as untrusted at this boundary despite its static annotation.
    value = cast(object, symbols.lookup_entity(record))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("symbol-table lookup must return EntityId or None")
    if not 0 <= value < symbols.entity_count:
        raise ValueError("symbol-table lookup returned an out-of-range ontology ID")
    return EntityId(value)


def _complete_query_entity_mapping(
    transaction: IndexTransaction,
    symbols: SymbolTableView,
    ontology_ids: dict[EntityRecord, EntityId],
    fresh: tuple[EntityRecord, ...],
) -> tuple[dict[EntityRecord, EntityId], tuple[EntityRecord, ...]]:
    """Resolve entities introduced by query normalization, such as top and bottom."""

    fresh_values = set(fresh)
    observed_ids = set(ontology_ids.values())
    for record in transaction.entities:
        if record in ontology_ids or record in fresh_values:
            continue
        ontology_id = _lookup_entity(symbols, record)
        if ontology_id is None:
            fresh_values.add(record)
        else:
            if ontology_id in observed_ids:
                raise ValueError("symbol table maps distinct entities to one ontology ID")
            observed_ids.add(ontology_id)
            ontology_ids[record] = ontology_id
    return ontology_ids, tuple(
        sorted(fresh_values, key=lambda item: (int(item.kind), item.iri.encode("utf-8")))
    )


def _duplicate_annotated_axiom(
    ontology: owl.OntologyView,
    axiom: owl.AxiomNode,
    observed: set[bytes],
) -> bool:
    annotations = getattr(axiom, "annotations", None)
    if not annotations:
        return False
    stripped = cast(owl.AxiomNode, _without_annotations(axiom))
    if ontology.contains(stripped, scope=owl.AxiomScope.CLOSURE):
        return True
    key = owl.structural_digest(stripped)
    if key in observed:
        return True
    observed.add(key)
    return False


def _without_annotations(value: owl.StructuralNode) -> owl.StructuralNode:
    annotations = getattr(value, "annotations", None)
    if not annotations:
        return value
    return cast(
        owl.StructuralNode,
        replace(cast(Any, value), annotations=owl.CanonicalSet()),
    )


def _single_feature_vector(index: int) -> tuple[int, ...]:
    values = [0] * FEATURE_VECTOR_LENGTH
    values[index] = 1
    return tuple(values)


def _validate_unsupported(value: str) -> None:
    if value not in {"ignore", "error"}:
        raise ValueError("unsupported must be 'ignore' or 'error'")


def _validate_node_limit(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("node safety ceiling must be a positive integer")


def _source_fingerprint(
    captured: CapturedOntology,
    *,
    unsupported: Literal["ignore", "error"],
    compatibility_spelling_digest: bytes,
) -> bytes:
    options = hashlib.sha256(
        b"pyelk:compiler-semantic-options:v1\x00" + unsupported.encode("ascii")
    ).digest()
    record = semantic_cache_record(
        captured,
        compiler_schema_version=COMPILER_SCHEMA_VERSION,
        compatibility_id=ELK_COMPATIBILITY_ID,
        semantic_options_fingerprint=options,
    )
    digest = hashlib.blake2b(digest_size=32)
    digest.update(b"pyelk:compiled-ontology-source:v1\x00")
    for value in (
        record.logical_fingerprint.algorithm.encode("ascii"),
        record.logical_fingerprint.schema.to_bytes(8, "little"),
        record.logical_fingerprint.digest,
        record.signature_fingerprint.algorithm.encode("ascii"),
        record.signature_fingerprint.schema.to_bytes(8, "little"),
        record.signature_fingerprint.digest,
        record.core_package_version.encode("utf-8"),
        b".".join(str(item).encode("ascii") for item in record.core_api_version),
        record.core_model_schema_version.to_bytes(8, "little"),
        b".".join(str(item).encode("ascii") for item in record.core_wire_format_version),
        record.core_adapter_protocol_version.to_bytes(8, "little"),
        record.compiler_schema_version.to_bytes(8, "little"),
        record.compatibility_id.encode("ascii"),
        record.semantic_options_fingerprint,
        compatibility_spelling_digest,
    ):
        digest.update(len(value).to_bytes(8, "little"))
        digest.update(value)
    return digest.digest()


__all__ = [
    "COMPILER_SCHEMA_VERSION",
    "DEFAULT_MAX_NODES_PER_AXIOM",
    "ELK_COMPATIBILITY_ID",
    "CompiledSymbolTable",
    "SymbolTableView",
    "compile_entailment_query",
    "compile_ontology",
    "compile_query_expression",
    "symbol_table",
]
