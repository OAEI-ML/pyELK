"""Typed public reasoner facade over one captured view and one backend session."""

from __future__ import annotations

from threading import RLock
from typing import TypeVar, cast

import pyowl_core as owl

from pyelk.backends import create_backend_session
from pyelk.config import ReasonerConfig
from pyelk.exceptions import BackendProtocolError, FreshEntityError, ReasonerClosedError
from pyelk.indexing.compiler import (
    compile_entailment_query,
    compile_ontology,
    compile_query_expression,
)
from pyelk.indexing.conversion import entity_record
from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    CompiledOntology,
    CompiledQuery,
    EntityRecord,
)
from pyelk.indexing.ir import (
    EntityKind as IREntityKind,
)
from pyelk.inputs import InputCapture, capture_input, load_snapshot
from pyelk.reasoning.completeness import issues_for
from pyelk.reasoning.contracts import (
    BackendInfo,
    BackendSession,
    PolicyFeature,
    QueryKind,
    QueryResultEntityId,
    RawQueryResult,
    RawRealization,
    RawTaxonomy,
    ReasoningTask,
)
from pyelk.reasoning.queries import named_object_property_query
from pyelk.reasoning.realization import types as raw_types
from pyelk.reasoning.taxonomy import validate_taxonomy
from pyelk.result import EntityNode, InstanceTaxonomy, ReasoningResult, Taxonomy

V = TypeVar("V")
_EMPTY_FEATURE_COUNTS = (0,) * FEATURE_VECTOR_LENGTH


class Reasoner:
    """ELK-compatible reasoning over one immutable pyowl-core view revision."""

    __slots__ = (
        "_capture",
        "_class_taxonomy_value",
        "_closed",
        "_compiled",
        "_config",
        "_entity_by_record",
        "_lock",
        "_object_taxonomy_value",
        "_policy_features",
        "_raw_class_taxonomy_value",
        "_raw_object_taxonomy_value",
        "_raw_realization_value",
        "_realization_value",
        "_session",
    )

    def __init__(
        self,
        ontology: owl.OntologyInput,
        config: ReasonerConfig | None = None,
        *,
        document_iri: owl.IRI | str | None = None,
        load_options: owl.LoadOptions | None = None,
        resolver: owl.ImportResolver | None = None,
    ) -> None:
        if config is not None and not isinstance(config, ReasonerConfig):
            raise TypeError("config must be ReasonerConfig or None")
        supplied = config or ReasonerConfig()
        self._config = ReasonerConfig(
            backend=supplied.backend,
            workers=supplied.workers,
            allow_fresh_entities=supplied.allow_fresh_entities,
            unsupported=supplied.unsupported,
            allow_incomplete_imports=supplied.allow_incomplete_imports,
        )
        self._lock = RLock()
        self._closed = False
        capture = capture_input(
            ontology,
            document_iri=document_iri,
            options=load_options,
            resolver=resolver,
        )
        if (
            capture.imports.requires_incomplete_imports
            and not self._config.allow_incomplete_imports
        ):
            raise owl.UnresolvedImportError(
                "the ontology view has an incomplete import closure; "
                "set allow_incomplete_imports=True to reason over the available closure"
            )
        self._capture: InputCapture | None = capture
        self._policy_features = (
            (PolicyFeature.IGNORED_IMPORT,) if capture.imports.requires_incomplete_imports else ()
        )
        self._compiled: CompiledOntology | None = compile_ontology(
            capture.ontology.view,
            unsupported=self._config.unsupported,
        )
        self._entity_by_record = self._capture_entities(capture, self._compiled)
        self._session: BackendSession | None = create_backend_session(
            self._compiled,
            self._config,
        )
        self._raw_class_taxonomy_value: RawTaxonomy | None = None
        self._raw_object_taxonomy_value: RawTaxonomy | None = None
        self._raw_realization_value: RawRealization | None = None
        self._class_taxonomy_value: Taxonomy[owl.Class] | None = None
        self._object_taxonomy_value: Taxonomy[owl.ObjectProperty] | None = None
        self._realization_value: InstanceTaxonomy | None = None

    def close(self) -> None:
        """Release backend-owned state exactly once."""

        with self._lock:
            if self._closed:
                return
            try:
                if self._session is not None:
                    self._session.close()
            finally:
                self._closed = True
                self._session = None
                self._compiled = None
                self._capture = None
                self._entity_by_record = {}
                self._raw_class_taxonomy_value = None
                self._raw_object_taxonomy_value = None
                self._raw_realization_value = None
                self._class_taxonomy_value = None
                self._object_taxonomy_value = None
                self._realization_value = None

    def __enter__(self) -> Reasoner:
        with self._lock:
            self._ensure_open()
            return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def backend(self) -> BackendInfo:
        with self._lock:
            session = self._require_session()
            info = session.info
            if not isinstance(info, BackendInfo):
                raise BackendProtocolError("BackendInfo from backend session", info)
            try:
                return BackendInfo(
                    name=info.name,
                    implementation_version=info.implementation_version,
                    ir_major=info.ir_major,
                    ir_minor=info.ir_minor,
                    requested_workers=info.requested_workers,
                    effective_workers=info.effective_workers,
                    native_available=info.native_available,
                    fallback_reason=info.fallback_reason,
                )
            except (TypeError, ValueError) as error:
                raise BackendProtocolError("a valid BackendInfo", str(error)) from error

    @property
    def ontology(self) -> owl.OntologyView:
        with self._lock:
            self._ensure_open()
            if self._capture is None:  # pragma: no cover - open invariant
                raise ReasonerClosedError
            return self._capture.ontology.view

    def is_consistent(self) -> ReasoningResult[bool]:
        with self._lock:
            inconsistent = self._inconsistent()
            return self._result(
                ReasoningTask.CONSISTENCY,
                not inconsistent,
                inconsistent=inconsistent,
            )

    def is_inconsistent(self) -> ReasoningResult[bool]:
        with self._lock:
            inconsistent = self._inconsistent()
            return self._result(
                ReasoningTask.CONSISTENCY,
                inconsistent,
                inconsistent=inconsistent,
            )

    def classify(self) -> ReasoningResult[Taxonomy[owl.Class]]:
        with self._lock:
            inconsistent = self._inconsistent()
            return self._result(
                ReasoningTask.CLASS_TAXONOMY,
                self._class_taxonomy(),
                inconsistent=inconsistent,
            )

    def classify_object_properties(
        self,
    ) -> ReasoningResult[Taxonomy[owl.ObjectProperty]]:
        with self._lock:
            inconsistent = self._inconsistent()
            return self._result(
                ReasoningTask.OBJECT_PROPERTY_TAXONOMY,
                self._object_taxonomy(),
                inconsistent=inconsistent,
            )

    def realize(self) -> ReasoningResult[InstanceTaxonomy]:
        with self._lock:
            inconsistent = self._inconsistent()
            return self._result(
                ReasoningTask.REALIZATION,
                self._realization(),
                inconsistent=inconsistent,
            )

    def is_satisfiable(self, expression: owl.ClassExpression) -> ReasoningResult[bool]:
        with self._lock:
            query, inconsistent = self._prepare_class_query(expression)
            raw = self._query(query, QueryKind.SATISFIABLE, False)
            if not isinstance(raw.boolean, bool):
                raise BackendProtocolError("a satisfiability boolean", raw)
            return self._query_result(raw.boolean, query, inconsistent)

    def equivalent_classes(
        self, expression: owl.ClassExpression
    ) -> ReasoningResult[tuple[EntityNode[owl.Class], ...]]:
        with self._lock:
            query, inconsistent = self._prepare_class_query(expression)
            raw = self._query(query, QueryKind.EQUIVALENT_CLASSES, False)
            nodes = self._query_nodes(raw, query, expression, IREntityKind.CLASS)
            if len(nodes) > 1:
                raise BackendProtocolError("at most one equivalent-class node", raw.nodes)
            return self._query_result(
                cast(tuple[EntityNode[owl.Class], ...], nodes), query, inconsistent
            )

    def subclasses(
        self, expression: owl.ClassExpression, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[owl.Class], ...]]:
        with self._lock:
            self._require_bool(direct, "direct")
            query, inconsistent = self._prepare_class_query(expression)
            raw = self._query(query, QueryKind.SUBCLASSES, direct)
            nodes = self._query_nodes(raw, query, expression, IREntityKind.CLASS)
            return self._query_result(
                cast(tuple[EntityNode[owl.Class], ...], nodes), query, inconsistent
            )

    def superclasses(
        self, expression: owl.ClassExpression, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[owl.Class], ...]]:
        with self._lock:
            self._require_bool(direct, "direct")
            query, inconsistent = self._prepare_class_query(expression)
            raw = self._query(query, QueryKind.SUPERCLASSES, direct)
            nodes = self._query_nodes(raw, query, expression, IREntityKind.CLASS)
            return self._query_result(
                cast(tuple[EntityNode[owl.Class], ...], nodes), query, inconsistent
            )

    def instances(
        self, expression: owl.ClassExpression, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[owl.NamedIndividual], ...]]:
        with self._lock:
            self._require_bool(direct, "direct")
            query, inconsistent = self._prepare_class_query(expression)
            raw = self._query(query, QueryKind.INSTANCES, direct)
            nodes = self._query_nodes(
                raw,
                query,
                expression,
                IREntityKind.NAMED_INDIVIDUAL,
            )
            return self._query_result(
                cast(tuple[EntityNode[owl.NamedIndividual], ...], nodes),
                query,
                inconsistent,
            )

    def types(
        self, individual: owl.NamedIndividual, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[owl.Class], ...]]:
        with self._lock:
            if not isinstance(individual, owl.NamedIndividual):
                raise TypeError("individual must be a pyowl-core NamedIndividual")
            self._require_bool(direct, "direct")
            inconsistent = self._inconsistent()
            compiled = self._require_compiled()
            record = EntityRecord(IREntityKind.NAMED_INDIVIDUAL, individual.iri.value)
            entity_id = self._entity_id(record)
            fresh_id: int | None = None
            if entity_id is None:
                fresh_id = len(compiled.entities)
                entity_id = fresh_id
                if not inconsistent and not self._config.allow_fresh_entities:
                    raise FreshEntityError((individual,))
            raw_nodes = raw_types(
                self._raw_realization(),
                entity_id,
                direct=direct,
                fresh_id=fresh_id,
            )
            nodes = self._ordinary_nodes(raw_nodes, IREntityKind.CLASS)
            return self._result(
                ReasoningTask.REALIZATION,
                cast(tuple[EntityNode[owl.Class], ...], nodes),
                inconsistent=inconsistent,
            )

    def sub_object_properties(
        self, prop: owl.ObjectProperty, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[owl.ObjectProperty], ...]]:
        return self._object_property_query(prop, QueryKind.SUBCLASSES, direct)

    def super_object_properties(
        self, prop: owl.ObjectProperty, *, direct: bool = False
    ) -> ReasoningResult[tuple[EntityNode[owl.ObjectProperty], ...]]:
        return self._object_property_query(prop, QueryKind.SUPERCLASSES, direct)

    def equivalent_object_properties(
        self, prop: owl.ObjectProperty
    ) -> ReasoningResult[EntityNode[owl.ObjectProperty]]:
        with self._lock:
            result = self._object_property_query(prop, QueryKind.EQUIVALENT_CLASSES, False)
            if len(result.value) != 1:  # pragma: no cover - named query contract
                raise BackendProtocolError("one equivalent object-property node", result.value)
            return ReasoningResult(result.value[0], result.complete, result.reasons)

    def is_entailed(self, axiom: owl.Axiom) -> ReasoningResult[bool]:
        with self._lock:
            self._ensure_open()
            if not isinstance(axiom, owl.AXIOM_TYPES):
                raise TypeError("axiom must be a pyowl-core Axiom")
            compiled = self._require_compiled()
            query = compile_entailment_query(
                axiom,
                compiled,
                unsupported=self._config.unsupported,
            )
            self._validate_fresh(query, axiom)
            value = self._require_session().entails(query.encoded)
            if not isinstance(value, bool):
                raise BackendProtocolError("an entailment boolean", value)
            inconsistent = self._inconsistent()
            return self._result(
                ReasoningTask.ENTAILMENT_QUERY,
                value,
                query_feature_counts=query.feature_counts,
                inconsistent=inconsistent,
            )

    def all_classes(self) -> tuple[owl.Class, ...]:
        with self._lock:
            return cast(
                tuple[owl.Class, ...],
                self._entities(IREntityKind.CLASS),
            )

    def all_named_individuals(self) -> tuple[owl.NamedIndividual, ...]:
        with self._lock:
            return cast(
                tuple[owl.NamedIndividual, ...],
                self._entities(IREntityKind.NAMED_INDIVIDUAL),
            )

    def all_object_properties(self) -> tuple[owl.ObjectProperty, ...]:
        with self._lock:
            return cast(
                tuple[owl.ObjectProperty, ...],
                self._entities(IREntityKind.OBJECT_PROPERTY),
            )

    def _object_property_query(
        self,
        prop: owl.ObjectProperty,
        kind: QueryKind,
        direct: bool,
    ) -> ReasoningResult[tuple[EntityNode[owl.ObjectProperty], ...]]:
        with self._lock:
            if not isinstance(prop, owl.ObjectProperty):
                raise TypeError("prop must be a pyowl-core ObjectProperty")
            self._require_bool(direct, "direct")
            inconsistent = self._inconsistent()
            compiled = self._require_compiled()
            record = EntityRecord(IREntityKind.OBJECT_PROPERTY, prop.iri.value)
            entity_id = self._entity_id(record)
            fresh_id: int | None = None
            taxonomy = self._raw_object_taxonomy()
            if inconsistent:
                raw_node_rows = (
                    (tuple(QueryResultEntityId(member) for member in taxonomy.nodes[taxonomy.top]),)
                    if kind is QueryKind.EQUIVALENT_CLASSES
                    else ()
                )
                raw = RawQueryResult(kind=kind, nodes=raw_node_rows)
            else:
                if entity_id is None:
                    fresh_id = len(compiled.entities)
                    entity_id = fresh_id
                    if not self._config.allow_fresh_entities:
                        raise FreshEntityError((prop,))
                raw = named_object_property_query(
                    taxonomy,
                    entity_id,
                    kind,
                    direct=direct,
                    fresh_id=fresh_id,
                )
            query = CompiledQuery(
                encoded=None,
                feature_counts=_EMPTY_FEATURE_COUNTS,
                fresh_entities=(record,) if fresh_id is not None else (),
            )
            public_nodes = self._query_nodes(raw, query, prop, IREntityKind.OBJECT_PROPERTY)
            return self._result(
                ReasoningTask.OBJECT_PROPERTY_TAXONOMY,
                cast(tuple[EntityNode[owl.ObjectProperty], ...], public_nodes),
                inconsistent=inconsistent,
            )

    def _prepare_class_query(self, expression: owl.ClassExpression) -> tuple[CompiledQuery, bool]:
        self._ensure_open()
        if not isinstance(expression, owl.CLASS_EXPRESSION_TYPES):
            raise TypeError("expression must be a pyowl-core ClassExpression")
        query = compile_query_expression(
            expression,
            self._require_compiled(),
            unsupported=self._config.unsupported,
        )
        inconsistent = self._inconsistent()
        if not inconsistent:
            self._validate_fresh(query, expression)
        return query, inconsistent

    def _query(self, query: CompiledQuery, kind: QueryKind, direct: bool) -> RawQueryResult:
        raw = self._require_session().query_class_expression(query.encoded, kind, direct)
        if not isinstance(raw, RawQueryResult):
            raise BackendProtocolError("RawQueryResult from backend", raw)
        try:
            validated = RawQueryResult(kind=raw.kind, boolean=raw.boolean, nodes=raw.nodes)
        except (TypeError, ValueError) as error:
            raise BackendProtocolError("a valid canonical RawQueryResult", str(error)) from error
        if validated.kind is not kind:
            raise BackendProtocolError(f"query kind {kind.name}", validated.kind)
        return validated

    def _query_result(
        self,
        value: V,
        query: CompiledQuery,
        inconsistent: bool,
    ) -> ReasoningResult[V]:
        return self._result(
            ReasoningTask.CLASS_EXPRESSION_QUERY,
            value,
            query_feature_counts=query.feature_counts,
            inconsistent=inconsistent,
        )

    def _result(
        self,
        task: ReasoningTask,
        value: V,
        *,
        query_feature_counts: tuple[int, ...] = (),
        inconsistent: bool = False,
    ) -> ReasoningResult[V]:
        compiled = self._require_compiled()
        reasons = issues_for(
            task,
            compiled.feature_counts,
            query_feature_counts=query_feature_counts,
            policy_features=self._policy_features,
            inconsistent=inconsistent,
        )
        return ReasoningResult(value, not reasons, reasons)

    def _inconsistent(self) -> bool:
        value = self._require_session().is_inconsistent()
        if not isinstance(value, bool):
            raise BackendProtocolError("an inconsistency boolean", value)
        return value

    def _raw_class_taxonomy(self) -> RawTaxonomy:
        if self._raw_class_taxonomy_value is None:
            value = self._require_session().class_taxonomy()
            self._raw_class_taxonomy_value = self._validate_taxonomy(value, IREntityKind.CLASS)
        return self._raw_class_taxonomy_value

    def _raw_object_taxonomy(self) -> RawTaxonomy:
        if self._raw_object_taxonomy_value is None:
            value = self._require_session().object_property_taxonomy()
            self._raw_object_taxonomy_value = self._validate_taxonomy(
                value,
                IREntityKind.OBJECT_PROPERTY,
            )
        return self._raw_object_taxonomy_value

    def _raw_realization(self) -> RawRealization:
        if self._raw_realization_value is None:
            value = self._require_session().realization()
            if not isinstance(value, RawRealization):
                raise BackendProtocolError("RawRealization from backend", value)
            try:
                value = RawRealization(
                    class_taxonomy=value.class_taxonomy,
                    instance_nodes=value.instance_nodes,
                    direct_types=value.direct_types,
                )
                self._validate_taxonomy(value.class_taxonomy, IREntityKind.CLASS)
                if value.class_taxonomy != self._raw_class_taxonomy():
                    raise ValueError("realization class taxonomy differs from classification")
                self._validate_realization(value)
            except (TypeError, ValueError, IndexError, KeyError) as error:
                raise BackendProtocolError("a valid complete RawRealization", str(error)) from error
            self._raw_realization_value = value
        return self._raw_realization_value

    def _class_taxonomy(self) -> Taxonomy[owl.Class]:
        if self._class_taxonomy_value is None:
            self._class_taxonomy_value = cast(
                Taxonomy[owl.Class],
                self._public_taxonomy(self._raw_class_taxonomy(), IREntityKind.CLASS),
            )
        return self._class_taxonomy_value

    def _object_taxonomy(self) -> Taxonomy[owl.ObjectProperty]:
        if self._object_taxonomy_value is None:
            self._object_taxonomy_value = cast(
                Taxonomy[owl.ObjectProperty],
                self._public_taxonomy(
                    self._raw_object_taxonomy(),
                    IREntityKind.OBJECT_PROPERTY,
                ),
            )
        return self._object_taxonomy_value

    def _realization(self) -> InstanceTaxonomy:
        if self._realization_value is None:
            raw = self._raw_realization()
            class_taxonomy = self._class_taxonomy()
            instances = self._ordinary_nodes(
                raw.instance_nodes,
                IREntityKind.NAMED_INDIVIDUAL,
            )
            instance_by_members = {
                frozenset(member.iri.value for member in node.members): node for node in instances
            }
            class_by_members = {
                frozenset(member.iri.value for member in node.members): node
                for node in class_taxonomy.nodes
            }
            direct_types = tuple(
                (
                    instance_by_members[
                        frozenset(
                            self._entity_for_id(member).iri.value
                            for member in raw.instance_nodes[instance_index]
                        )
                    ],
                    class_by_members[
                        frozenset(
                            self._entity_for_id(member).iri.value
                            for member in raw.class_taxonomy.nodes[class_index]
                        )
                    ],
                )
                for instance_index, class_index in raw.direct_types
            )
            self._realization_value = InstanceTaxonomy(
                class_taxonomy=class_taxonomy,
                instances=cast(tuple[EntityNode[owl.NamedIndividual], ...], instances),
                direct_types=cast(
                    tuple[tuple[EntityNode[owl.NamedIndividual], EntityNode[owl.Class]], ...],
                    direct_types,
                ),
            )
        return self._realization_value

    def _public_taxonomy(self, raw: RawTaxonomy, kind: IREntityKind) -> Taxonomy[owl.Entity]:
        try:
            raw_nodes = tuple(
                EntityNode(tuple(self._entity_for_id(member, kind) for member in node))
                for node in raw.nodes
            )
            edges = tuple((raw_nodes[sub], raw_nodes[sup]) for sub, sup in raw.direct_edges)
            return Taxonomy(raw_nodes, edges, raw_nodes[raw.top], raw_nodes[raw.bottom])
        except (TypeError, ValueError, IndexError, KeyError) as error:
            raise BackendProtocolError(f"canonical {kind.name} taxonomy", str(error)) from error

    def _ordinary_nodes(
        self,
        raw_nodes: tuple[tuple[int, ...], ...],
        kind: IREntityKind,
    ) -> tuple[EntityNode[owl.Entity], ...]:
        try:
            result = tuple(
                EntityNode(tuple(self._entity_for_id(member, kind) for member in node))
                for node in raw_nodes
            )
            return tuple(
                sorted(set(result), key=lambda node: owl.canonical_bytes(node.canonical_member))
            )
        except (TypeError, ValueError, IndexError, KeyError) as error:
            raise BackendProtocolError(f"canonical {kind.name} entity nodes", str(error)) from error

    def _query_nodes(
        self,
        raw: RawQueryResult,
        query: CompiledQuery,
        source: owl.StructuralNode,
        kind: IREntityKind,
    ) -> tuple[EntityNode[owl.Entity], ...]:
        compiled = self._require_compiled()
        source_entities = {entity_record(entity): entity for entity in owl.signature(source)}
        ontology_count = len(compiled.entities)

        def resolve(value: int) -> owl.Entity:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("query result entity IDs must be integers")
            if 0 <= value < ontology_count:
                return self._entity_for_id(value, kind)
            rank = value - ontology_count
            if not 0 <= rank < len(query.fresh_entities):
                raise ValueError("query result contains an out-of-range ephemeral entity ID")
            record = query.fresh_entities[rank]
            if record.kind is not kind:
                raise ValueError("query result contains an entity of the wrong kind")
            return source_entities.get(record, self._entity_from_record(record))

        try:
            nodes = tuple(
                EntityNode(tuple(resolve(member) for member in node)) for node in raw.nodes
            )
            return tuple(
                sorted(set(nodes), key=lambda node: owl.canonical_bytes(node.canonical_member))
            )
        except (TypeError, ValueError, IndexError, KeyError) as error:
            raise BackendProtocolError(f"canonical {kind.name} query nodes", str(error)) from error

    def _validate_taxonomy(self, value: object, kind: IREntityKind) -> RawTaxonomy:
        if not isinstance(value, RawTaxonomy):
            raise BackendProtocolError("RawTaxonomy from backend", value)
        try:
            canonical = RawTaxonomy(
                nodes=value.nodes,
                direct_edges=value.direct_edges,
                top=value.top,
                bottom=value.bottom,
            )
            return validate_taxonomy(self._require_compiled(), canonical, kind)
        except (TypeError, ValueError, IndexError, KeyError) as error:
            raise BackendProtocolError(
                f"a valid complete {kind.name} taxonomy", str(error)
            ) from error

    def _validate_realization(self, value: RawRealization) -> None:
        compiled = self._require_compiled()
        expected = tuple(
            index
            for index, record in enumerate(compiled.entities)
            if record.kind is IREntityKind.NAMED_INDIVIDUAL
        )
        actual = tuple(sorted(member for node in value.instance_nodes for member in node))
        if actual != expected:
            raise ValueError("realization individual coverage does not match compiled entities")
        direct_by_instance: dict[int, set[int]] = {
            index: set() for index in range(len(value.instance_nodes))
        }
        for instance, class_node in value.direct_types:
            direct_by_instance[instance].add(class_node)
        strict_supers = self._strict_super_closure(value.class_taxonomy)
        for types in direct_by_instance.values():
            if any(
                super_type in strict_supers[sub_type]
                for sub_type in types
                for super_type in types
                if sub_type != super_type
            ):
                raise ValueError("realization direct types are not minimal")

    @staticmethod
    def _strict_super_closure(taxonomy: RawTaxonomy) -> tuple[frozenset[int], ...]:
        outgoing: list[list[int]] = [[] for _ in taxonomy.nodes]
        for sub, sup in taxonomy.direct_edges:
            outgoing[sub].append(sup)
        closures: list[frozenset[int]] = []
        for start in range(len(taxonomy.nodes)):
            reached: set[int] = set()
            pending = list(outgoing[start])
            while pending:
                node = pending.pop()
                if node in reached:
                    continue
                reached.add(node)
                pending.extend(outgoing[node])
            closures.append(frozenset(reached))
        return tuple(closures)

    def _validate_fresh(self, query: CompiledQuery, source: owl.StructuralNode) -> None:
        if self._config.allow_fresh_entities or not query.fresh_entities:
            return
        source_entities: dict[EntityRecord, owl.Entity] = {
            entity_record(entity): entity for entity in owl.signature(source)
        }
        entities: tuple[owl.Entity, ...] = tuple(
            source_entities.get(record, self._entity_from_record(record))
            for record in query.fresh_entities
        )
        by_key = {owl.canonical_bytes(entity): entity for entity in entities}
        raise FreshEntityError(tuple(by_key[key] for key in sorted(by_key)))

    def _entities(self, kind: IREntityKind) -> tuple[owl.Entity, ...]:
        self._ensure_open()
        compiled = self._require_compiled()
        return tuple(
            self._entity_for_id(index, kind)
            for index, record in enumerate(compiled.entities)
            if record.kind is kind
        )

    def _entity_id(self, record: EntityRecord) -> int | None:
        compiled = self._require_compiled()
        for index, candidate in enumerate(compiled.entities):
            if candidate == record:
                return index
        return None

    def _entity_for_id(self, value: int, expected_kind: IREntityKind | None = None) -> owl.Entity:
        compiled = self._require_compiled()
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value < len(compiled.entities)
        ):
            raise ValueError("backend entity ID is out of range")
        record = compiled.entities[value]
        if expected_kind is not None and record.kind is not expected_kind:
            raise ValueError("backend entity ID has the wrong kind")
        return self._entity_by_record[record]

    @staticmethod
    def _capture_entities(
        capture: InputCapture,
        compiled: CompiledOntology,
    ) -> dict[EntityRecord, owl.Entity]:
        candidates = capture.ontology.view.signature(include_builtins=True)
        by_record = {entity_record(entity): entity for entity in candidates}
        result: dict[EntityRecord, owl.Entity] = {}
        for record in compiled.entities:
            if record.kind in {
                IREntityKind.CLASS,
                IREntityKind.NAMED_INDIVIDUAL,
                IREntityKind.OBJECT_PROPERTY,
                IREntityKind.DATA_PROPERTY,
                IREntityKind.DATATYPE,
                IREntityKind.ANNOTATION_PROPERTY,
            }:
                result[record] = by_record.get(record, Reasoner._entity_from_record(record))
        return result

    @staticmethod
    def _entity_from_record(record: EntityRecord) -> owl.Entity:
        iri = owl.IRI(record.iri)
        constructors = {
            IREntityKind.CLASS: owl.Class,
            IREntityKind.NAMED_INDIVIDUAL: owl.NamedIndividual,
            IREntityKind.OBJECT_PROPERTY: owl.ObjectProperty,
            IREntityKind.DATA_PROPERTY: owl.DataProperty,
            IREntityKind.DATATYPE: owl.Datatype,
            IREntityKind.ANNOTATION_PROPERTY: owl.AnnotationProperty,
        }
        try:
            constructor = constructors[record.kind]
        except KeyError as error:  # pragma: no cover - exhaustive frozen entity enum
            raise ValueError(f"unsupported entity kind {record.kind!r}") from error
        return constructor(iri)

    @staticmethod
    def _require_bool(value: object, field: str) -> None:
        if not isinstance(value, bool):
            raise TypeError(f"{field} must be a boolean")

    def _ensure_open(self) -> None:
        if self._closed:
            raise ReasonerClosedError

    def _require_session(self) -> BackendSession:
        self._ensure_open()
        if self._session is None:  # pragma: no cover - close invariant
            raise ReasonerClosedError
        return self._session

    def _require_compiled(self) -> CompiledOntology:
        self._ensure_open()
        if self._compiled is None:  # pragma: no cover - close invariant
            raise ReasonerClosedError
        return self._compiled


__all__ = ["Reasoner", "load_snapshot"]
