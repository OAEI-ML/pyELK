"""Lazy monotone stages over the pure-Python saturation engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import IntEnum
from types import MappingProxyType

from pyelk.indexing.ir import CompiledOntology, ExpressionId, ExpressionTag
from pyelk.reasoning.consistency import (
    ConsistencyState,
    consistency_roots,
    evaluate_consistency,
)
from pyelk.reasoning.contexts import FrozenContext
from pyelk.reasoning.properties import PropertySaturation, saturate_properties
from pyelk.reasoning.saturation import SaturationDiagnostics, SaturationEngine, SaturationSnapshot


class Stage(IntEnum):
    """Internal monotone reasoner stage."""

    COMPILED = 0
    PROPERTIES = 1
    CONSISTENCY = 2
    CLASSIFIED = 3
    REALIZED = 4


class SaturationSession:
    """Reusable internal stage manager consumed by later taxonomy/query work packages."""

    __slots__ = ("_consistency", "_engine", "_properties", "_stage", "compiled")

    def __init__(self, compiled: CompiledOntology) -> None:
        if not isinstance(compiled, CompiledOntology):
            raise TypeError("compiled must be CompiledOntology")
        self.compiled = compiled
        self._stage = Stage.COMPILED
        self._properties: PropertySaturation | None = None
        self._engine: SaturationEngine | None = None
        self._consistency: ConsistencyState | None = None

    @property
    def stage(self) -> Stage:
        return self._stage

    @property
    def properties(self) -> PropertySaturation:
        """Build and return the immutable property closure once."""

        return self.ensure_properties()

    @property
    def engine(self) -> SaturationEngine:
        """Return the class engine after establishing its property prerequisite."""

        self.ensure_properties()
        if self._engine is None:
            if self._properties is None:  # pragma: no cover - ensured above
                raise AssertionError("property stage completed without a closure")
            self._engine = SaturationEngine(self.compiled, self._properties)
        return self._engine

    @property
    def consistency(self) -> ConsistencyState:
        """Return the cached ontology consistency state, computing it when needed."""

        return self.ensure_consistency()

    def ensure_properties(self) -> PropertySaturation:
        """Advance monotonically to the property stage."""

        if self._properties is None:
            self._properties = saturate_properties(self.compiled)
        if self._stage < Stage.PROPERTIES:
            self._stage = Stage.PROPERTIES
        return self._properties

    def ensure_consistency(self) -> ConsistencyState:
        """Saturate only top/occurring-individual roots and evaluate consistency."""

        if self._stage >= Stage.CONSISTENCY and self._consistency is not None:
            return self._consistency
        engine = self.engine
        engine.run(consistency_roots(self.compiled))
        self._consistency = self._evaluate_consistency()
        self._stage = Stage.CONSISTENCY
        return self._consistency

    def ensure_classified(self) -> SaturationSnapshot:
        """Add every committed named-class root without repeating completed work."""

        self.ensure_consistency()
        if self._stage < Stage.CLASSIFIED:
            self.engine.run(self._roots(ExpressionTag.CLASS))
            self._consistency = self._evaluate_consistency()
            self._stage = Stage.CLASSIFIED
        return self.snapshot()

    def ensure_realized(self) -> SaturationSnapshot:
        """Add every committed named-individual root after classification."""

        self.ensure_classified()
        if self._stage < Stage.REALIZED:
            self.engine.run(self._roots(ExpressionTag.INDIVIDUAL))
            self._consistency = self._evaluate_consistency()
            self._stage = Stage.REALIZED
        return self.snapshot()

    def saturate_roots(self, roots: Iterable[ExpressionId]) -> SaturationSnapshot:
        """Saturate extra installed roots without advancing ontology enumeration stages."""

        self.ensure_consistency()
        self.engine.run(roots)
        self._consistency = self._evaluate_consistency()
        return self.snapshot()

    def saturate_query_root(
        self,
        canonical_key: bytes,
        root: ExpressionId,
    ) -> FrozenContext:
        """Saturate one cached installed query root while leaving the stage unchanged."""

        self.ensure_consistency()
        context = self.engine.saturate_query_root(canonical_key, root)
        self._consistency = self._evaluate_consistency()
        return context

    def snapshot(self) -> SaturationSnapshot:
        """Return the current immutable debug snapshot."""

        inconsistent = False if self._consistency is None else self._consistency.inconsistent
        return self.engine.snapshot(inconsistent_ontology=inconsistent)

    def diagnostics(self) -> Mapping[str, int | str | bool]:
        """Return deterministic stage and scheduler diagnostics."""

        counters: SaturationDiagnostics
        if self._engine is None:
            counters = SaturationDiagnostics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        else:
            counters = self._engine.diagnostics()
        values: dict[str, int | str | bool] = dict(counters.as_mapping())
        values["inconsistent_ontology"] = bool(
            self._consistency is not None and self._consistency.inconsistent
        )
        values["stage"] = self._stage.name.lower()
        values["stage_id"] = int(self._stage)
        return MappingProxyType(dict(sorted(values.items())))

    def _evaluate_consistency(self) -> ConsistencyState:
        properties = self.ensure_properties()
        contexts = {
            root: state
            for root in self.engine.roots
            if (state := self.engine.context_state(root)) is not None
        }
        return evaluate_consistency(self.compiled, properties, contexts)

    def _roots(self, tag: ExpressionTag) -> tuple[ExpressionId, ...]:
        return tuple(
            ExpressionId(index)
            for index, record in enumerate(self.compiled.expressions)
            if record.tag is tag
        )


__all__ = ["SaturationSession", "Stage"]
