"""Pure decision procedure for the eight normalized entailment families.

The compiler has already reduced each supported axiom to one or more subsumption
obligations.  This module intentionally decides only those obligations: ``encoded=None``
is always false, including for an inconsistent ontology, while a valid encoded query uses
classical explosion after ontology inconsistency has been established.
"""

from __future__ import annotations

from dataclasses import replace

from pyelk.indexing.ir import QueryIR, QueryIRKind
from pyelk.reasoning.properties import saturate_properties
from pyelk.reasoning.queries import (
    QueryFeatureMetadata,
    _context_subsumers,
    _install_query,
    query_feature_metadata,
)
from pyelk.reasoning.saturation import SaturationEngine
from pyelk.reasoning.session import SaturationSession


class EntailmentEngine:
    """Cache normalized entailment decisions for one immutable session."""

    __slots__ = ("_cache", "session")

    def __init__(self, session: SaturationSession) -> None:
        if not isinstance(session, SaturationSession):
            raise TypeError("session must be SaturationSession")
        self.session = session
        self._cache: dict[bytes | None, bool] = {}

    @property
    def cached_query_count(self) -> int:
        """Number of distinct encoded/unindexed decisions retained by this engine."""

        return len(self._cache)

    def entails(self, encoded_axiom: bytes | None) -> bool:
        """Decide a canonical entailment mini-IR or its unsupported ``None`` marker."""

        if encoded_axiom is not None and not isinstance(encoded_axiom, bytes):
            raise TypeError("encoded_axiom must be bytes or None")
        cached = self._cache.get(encoded_axiom)
        if cached is not None:
            return cached
        if encoded_axiom is None:
            self._cache[None] = False
            return False

        query = QueryIR.decode(encoded_axiom)
        if query.kind is not QueryIRKind.ENTAILMENT:
            raise ValueError("entailment requires ENTAILMENT mini-IR")
        if self.session.ensure_consistency().inconsistent:
            self._cache[encoded_axiom] = True
            return True

        overlay, _ontology_ids, query_ids = _install_query(self.session.compiled, query)
        obligations = tuple(
            (query_ids[sub_expression], query_ids[super_expression])
            for sub_expression, super_expression in query.subsumption_obligations
        )
        if overlay.property_ranges:
            # Pinned ELK reports ontology range interactions as incomplete for entailment
            # and its non-incremental query path does not use those ranges to prove the
            # normalized obligation (the frozen AssertionRanges/HasValueRanges values are
            # false).  Keep the feature metadata on the original compiled/query records,
            # but omit the unsupported range rule from this decision overlay.
            overlay = replace(overlay, property_ranges=())
        properties = saturate_properties(overlay)
        roots = {sub_expression for sub_expression, _ in obligations}
        contexts = dict(SaturationEngine(overlay, properties).run(tuple(sorted(roots))).contexts)
        result = all(
            contexts[sub_expression].inconsistent
            or super_expression in _context_subsumers(contexts[sub_expression])
            for sub_expression, super_expression in obligations
        )
        self._cache[encoded_axiom] = result
        return result


def entails(
    session: SaturationSession,
    encoded_axiom: bytes | None,
    *,
    engine: EntailmentEngine | None = None,
) -> bool:
    """Functional decision entry point; pass ``engine`` to retain the result cache."""

    evaluator = engine or EntailmentEngine(session)
    if evaluator.session is not session:
        raise ValueError("entailment engine belongs to another saturation session")
    return evaluator.entails(encoded_axiom)


def unsupported_entailment(
    feature_counts: tuple[int, ...],
) -> tuple[bool, QueryFeatureMetadata]:
    """Return the frozen unsupported-query value together with its exact feature hook."""

    return False, query_feature_metadata(feature_counts)


__all__ = ["EntailmentEngine", "entails", "unsupported_entailment"]
