from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar, cast

import pyowl_core as owl
from pyowl_core.model.axioms import AxiomNode

from pyelk.indexing.ir import CompiledOntology, EntityRecord, ExpressionId, ExpressionRecord

A = TypeVar("A", bound=AxiomNode)
V = TypeVar("V")


def load_functional(body: str, *, ontology_iri: str = "urn:test") -> owl.OntologySnapshot:
    source = (
        "Prefix(:=<urn:test#>) "
        "Prefix(rdfs:=<http://www.w3.org/2000/01/rdf-schema#>) "
        f"Ontology(<{ontology_iri}> {body})"
    ).encode()
    return owl.load_snapshot(
        source,
        options=owl.LoadOptions(
            format=owl.DocumentFormat.FUNCTIONAL,
            imports=owl.ImportPolicy.IGNORE,
            backend=owl.BackendPreference.PYTHON,
        ),
    )


def entity_id(compiled: CompiledOntology, iri: str) -> int:
    return next(index for index, record in enumerate(compiled.entities) if record.iri == iri)


def expression_id(compiled: CompiledOntology, record: ExpressionRecord) -> int:
    return compiled.expressions.index(record)


def expression_for_entity(compiled: CompiledOntology, entity: EntityRecord) -> ExpressionId:
    entity_index = compiled.entities.index(entity)
    return ExpressionId(
        next(
            index
            for index, expression in enumerate(compiled.expressions)
            if expression.arguments == (entity_index,)
        )
    )


class ExtensionView:
    """Protocol-complete view wrapper used for an explicit extension component."""

    def __init__(
        self,
        base: owl.OntologyView,
        extension: owl.StructuralNode | None = None,
    ) -> None:
        self.base = base
        self.extension = extension

    @property
    def capabilities(self) -> owl.CoreCapabilities:
        return self.base.capabilities

    def iter_axioms(
        self,
        axiom_type: type[A] | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[AxiomNode | A]:
        yield from self.base.iter_axioms(axiom_type, scope=scope, document_key=document_key)

    def iter_extensions(
        self,
        namespace: str | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[owl.StructuralNode]:
        if self.extension is not None and namespace in {None, "swrl"}:
            yield self.extension

    def contains(
        self,
        axiom: AxiomNode,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> bool:
        return self.base.contains(axiom, scope=scope, document_key=document_key)

    def ontology_annotations(
        self,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> owl.CanonicalSet[owl.Annotation]:
        return self.base.ontology_annotations(scope=scope, document_key=document_key)

    def signature(
        self,
        kind: owl.EntityKind | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
        include_builtins: bool = True,
    ) -> tuple[owl.Entity, ...]:
        values = {*self.base.signature(kind, scope=scope, document_key=document_key)}
        if self.extension is not None:
            values.update(
                entity
                for entity in owl.signature(self.extension)
                if kind is None or entity.kind is kind
            )
        return tuple(sorted(values, key=owl.canonical_bytes))

    def view(self, view_type: type[V], /, **options: object) -> V:
        encoded_type = getattr(owl, "EncodedStructuralView", None)
        if isinstance(encoded_type, type) and view_type is encoded_type:
            from pyowl_core.backends.native_views import produce_encoded_structural_view_v1

            selected = dict(options)
            schema_version = selected.pop("schema_version", 1)
            if schema_version != 1:
                raise owl.AdapterCompatibilityError(
                    "test extension view only publishes structural-columns schema 1"
                )
            scope = selected.pop("scope", owl.AxiomScope.CLOSURE)
            document_key = selected.pop("document_key", None)
            limits = selected.pop("limits", None)
            materialize_segments = selected.pop("materialize_segments", False)
            if selected:
                raise TypeError(f"unsupported encoded-view options: {sorted(selected)!r}")
            return cast(
                V,
                produce_encoded_structural_view_v1(
                    cast(owl.OntologyView, self),
                    scope=cast(owl.AxiomScope, scope),
                    document_key=cast(str | None, document_key),
                    limits=cast(owl.ParseLimits | None, limits),
                    materialize_segments=cast(bool, materialize_segments),
                ),
            )
        return self.base.view(view_type, **options)

    @property
    def origin_index(self) -> owl.OriginIndex:
        return self.base.origin_index

    @property
    def is_complete(self) -> bool:
        return self.base.is_complete

    @property
    def structural_fingerprint(self) -> owl.Fingerprint:
        return self.base.structural_fingerprint

    @property
    def logical_fingerprint(self) -> owl.Fingerprint:
        return self.base.logical_fingerprint

    @property
    def signature_fingerprint(self) -> owl.Fingerprint:
        return self.base.signature_fingerprint

    @property
    def report(self) -> owl.LoadReport:
        return self.base.report


def as_view(value: ExtensionView) -> owl.OntologyView:
    return cast(owl.OntologyView, value)
