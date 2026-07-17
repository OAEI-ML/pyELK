"""Deterministic single-thread class saturation.

The engine in this module mirrors ELK's active-context/local-agenda state machine while
remaining deliberately small.  It owns scheduling only: conclusions are stored by
``ContextState`` and inference selection is delegated to ``RuleDispatcher``.

Rule products are buffered until one dispatch completes.  Together with checkpoints only
between agenda items, this gives interruption a precise recovery boundary: an interrupted
run can be resumed on the same engine without rolling back any monotone conclusion.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from pyelk.indexing.ir import (
    CompiledOntology,
    EntityId,
    ExpressionId,
    PropertyChainId,
)
from pyelk.reasoning.conclusions import Conclusion, ContextInitialization, conclusion_destination
from pyelk.reasoning.contexts import ContextState, FrozenContext
from pyelk.reasoning.properties import PropertySaturation, saturate_properties
from pyelk.reasoning.rules import RuleDispatcher


class SaturationInterrupted(RuntimeError):
    """Internal control-flow signal raised at a safe saturation boundary."""


class SaturationBudgetExceeded(SaturationInterrupted):
    """A test/diagnostic run exhausted its internal conclusion budget."""


class SaturationMonitor(Protocol):
    """Internal cooperative interruption hook, called only between agenda items."""

    def __call__(self) -> bool:
        """Return true to interrupt the run at the current recovery boundary."""


@dataclass(frozen=True, slots=True)
class SaturationDiagnostics:
    """Immutable counters for deterministic scheduler diagnosis."""

    context_count: int
    contexts_created: int
    context_claims: int
    context_saturations: int
    conclusion_candidates: int
    conclusions_enqueued: int
    duplicate_candidates: int
    conclusions_inserted: int
    duplicate_insertions: int
    rule_dispatches: int
    product_candidates: int
    interrupted_runs: int
    maximum_active_contexts: int

    def as_mapping(self) -> Mapping[str, int]:
        """Return a stable immutable scalar mapping."""

        return MappingProxyType(
            {
                "conclusion_candidates": self.conclusion_candidates,
                "conclusions_enqueued": self.conclusions_enqueued,
                "conclusions_inserted": self.conclusions_inserted,
                "context_claims": self.context_claims,
                "context_count": self.context_count,
                "context_saturations": self.context_saturations,
                "contexts_created": self.contexts_created,
                "duplicate_candidates": self.duplicate_candidates,
                "duplicate_insertions": self.duplicate_insertions,
                "interrupted_runs": self.interrupted_runs,
                "maximum_active_contexts": self.maximum_active_contexts,
                "product_candidates": self.product_candidates,
                "rule_dispatches": self.rule_dispatches,
            }
        )


@dataclass(frozen=True, slots=True)
class SaturationSnapshot:
    """Deterministic immutable debug view of property and class saturation."""

    property_subsumers: tuple[tuple[PropertyChainId, ...], ...]
    property_ranges: tuple[tuple[ExpressionId, ...], ...]
    contexts: Mapping[ExpressionId, FrozenContext]
    inconsistent_ontology: bool

    def __post_init__(self) -> None:
        if not isinstance(self.property_subsumers, tuple) or not all(
            isinstance(row, tuple) for row in self.property_subsumers
        ):
            raise TypeError("property_subsumers must be a tuple of tuples")
        if not isinstance(self.property_ranges, tuple) or not all(
            isinstance(row, tuple) for row in self.property_ranges
        ):
            raise TypeError("property_ranges must be a tuple of tuples")
        if not isinstance(self.contexts, Mapping):
            raise TypeError("contexts must be a mapping")
        if not isinstance(self.inconsistent_ontology, bool):
            raise TypeError("inconsistent_ontology must be a boolean")
        frozen = {
            ExpressionId(root): context for root, context in sorted(self.contexts.items())
        }
        if any(not isinstance(context, FrozenContext) for context in frozen.values()):
            raise TypeError("snapshot contexts must contain FrozenContext values")
        if any(root != context.root for root, context in frozen.items()):
            raise ValueError("snapshot context keys must equal their context roots")
        object.__setattr__(self, "contexts", MappingProxyType(frozen))


@dataclass(slots=True)
class _MutableDiagnostics:
    contexts_created: int = 0
    context_claims: int = 0
    context_saturations: int = 0
    conclusion_candidates: int = 0
    conclusions_enqueued: int = 0
    duplicate_candidates: int = 0
    conclusions_inserted: int = 0
    duplicate_insertions: int = 0
    rule_dispatches: int = 0
    product_candidates: int = 0
    interrupted_runs: int = 0
    maximum_active_contexts: int = 0


class _BufferedProducer:
    """Collect one premise's products before publishing any of them."""

    __slots__ = ("products",)

    def __init__(self) -> None:
        self.products: list[Conclusion] = []

    def produce(self, conclusion: Conclusion, /) -> None:
        self.products.append(conclusion)


class SaturationEngine:
    """Duplicate-suppressing, non-recursive fixed-point scheduler.

    ``max_conclusions`` and ``interrupt`` are internal verification controls rather than
    public reasoner timeouts.  Either may stop a run only before an agenda item is removed;
    the engine restores its active-context flags before propagating the exception.
    """

    __slots__ = (
        "_active",
        "_claimed_root",
        "_contexts",
        "_diagnostics",
        "_query_roots",
        "_retry",
        "compiled",
        "dispatcher",
        "properties",
    )

    def __init__(
        self,
        compiled: CompiledOntology,
        properties: PropertySaturation | None = None,
    ) -> None:
        if not isinstance(compiled, CompiledOntology):
            raise TypeError("compiled must be CompiledOntology")
        if properties is not None and not isinstance(properties, PropertySaturation):
            raise TypeError("properties must be PropertySaturation or None")
        self.compiled = compiled
        self.properties = saturate_properties(compiled) if properties is None else properties
        self.dispatcher = RuleDispatcher(compiled, self.properties)
        self._contexts: dict[ExpressionId, ContextState] = {}
        self._active: deque[ExpressionId] = deque()
        self._claimed_root: ExpressionId | None = None
        self._retry: dict[ExpressionId, deque[Conclusion]] = {}
        self._query_roots: dict[bytes, ExpressionId] = {}
        self._diagnostics = _MutableDiagnostics()

    @property
    def has_pending_work(self) -> bool:
        """Whether a subsequent run still has work to process."""

        return bool(self._active or self._claimed_root is not None)

    @property
    def roots(self) -> tuple[ExpressionId, ...]:
        """Allocated context roots in deterministic ID order."""

        return tuple(sorted(self._contexts))

    def context_state(self, root: ExpressionId) -> ContextState | None:
        """Return the internal mutable context for downstream internal stages."""

        checked = self._checked_root(root)
        return self._contexts.get(checked)

    def context(self, root: ExpressionId) -> FrozenContext | None:
        """Return an immutable view of one allocated context."""

        state = self.context_state(root)
        return None if state is None else state.freeze()

    def demand(self, root: ExpressionId) -> ContextState:
        """Allocate and seed a context exactly once."""

        return self._ensure_context(self._checked_root(root))

    def enqueue(self, conclusion: Conclusion) -> bool:
        """Route one candidate to its owning local or cross-context agenda."""

        destination = self._checked_root(conclusion_destination(conclusion))
        state = self._ensure_context(destination)
        return self._enqueue_existing(state, conclusion)

    def run(
        self,
        roots: Iterable[ExpressionId] = (),
        *,
        max_conclusions: int | None = None,
        interrupt: SaturationMonitor | Callable[[], bool] | None = None,
    ) -> SaturationSnapshot:
        """Saturate demanded roots and every transitively activated context.

        A finite ``max_conclusions`` and ``interrupt`` exist for recovery verification and
        internal resource control.  Stopping preserves all completed monotone work and
        requeues the claimed context exactly once.
        """

        checked_roots = tuple(sorted({self._checked_root(root) for root in roots}))
        if max_conclusions is not None and (
            isinstance(max_conclusions, bool)
            or not isinstance(max_conclusions, int)
            or max_conclusions < 0
        ):
            raise ValueError("max_conclusions must be a nonnegative integer or None")
        if interrupt is not None and not callable(interrupt):
            raise TypeError("interrupt must be callable or None")
        for root in checked_roots:
            self._ensure_context(root)

        completed = 0
        try:
            while self._active:
                root = self._active.popleft()
                state = self._contexts[root]
                state.claim()
                self._claimed_root = root
                self._diagnostics.context_claims += 1
                try:
                    while self._has_local_work(root, state):
                        if max_conclusions is not None and completed >= max_conclusions:
                            raise SaturationBudgetExceeded(
                                f"saturation conclusion budget exhausted after {completed} items"
                            )
                        if interrupt is not None and interrupt():
                            raise SaturationInterrupted("saturation interrupted at safe boundary")
                        premise, inserted = self._next_premise(root, state)
                        if inserted:
                            completed += 1
                        self._dispatch(root, state, premise)
                    state.mark_saturated()
                    self._diagnostics.context_saturations += 1
                finally:
                    self._claimed_root = None
                    if self._has_local_work(root, state) and not state.queued:
                        state.mark_queued()
                        self._active.appendleft(root)
                        self._track_active_high_watermark()
        except BaseException:
            self._diagnostics.interrupted_runs += 1
            raise
        return self.snapshot()

    def saturate(
        self,
        roots: Iterable[ExpressionId] = (),
        *,
        max_conclusions: int | None = None,
        interrupt: SaturationMonitor | Callable[[], bool] | None = None,
    ) -> SaturationSnapshot:
        """Alias for :meth:`run`, matching saturation-stage terminology."""

        return self.run(
            roots,
            max_conclusions=max_conclusions,
            interrupt=interrupt,
        )

    def saturate_query_root(
        self,
        canonical_key: bytes,
        root: ExpressionId,
    ) -> FrozenContext:
        """Cache and saturate one already-installed canonical complex-query root.

        WP9 owns query mini-IR installation.  This method owns only the scheduler-side
        identity guarantee: one canonical key cannot silently change roots, and query roots
        never alter ontology entity or taxonomy enumeration.
        """

        if not isinstance(canonical_key, bytes) or not canonical_key:
            raise ValueError("canonical query key must be nonempty bytes")
        checked = self._checked_root(root)
        previous = self._query_roots.get(canonical_key)
        if previous is not None and previous != checked:
            raise ValueError("canonical query key is already bound to another root")
        self._query_roots[canonical_key] = checked
        self.run((checked,))
        context = self._contexts[checked]
        return context.freeze()

    def diagnostics(self) -> SaturationDiagnostics:
        """Freeze the current scheduler counters."""

        values = self._diagnostics
        return SaturationDiagnostics(
            context_count=len(self._contexts),
            contexts_created=values.contexts_created,
            context_claims=values.context_claims,
            context_saturations=values.context_saturations,
            conclusion_candidates=values.conclusion_candidates,
            conclusions_enqueued=values.conclusions_enqueued,
            duplicate_candidates=values.duplicate_candidates,
            conclusions_inserted=values.conclusions_inserted,
            duplicate_insertions=values.duplicate_insertions,
            rule_dispatches=values.rule_dispatches,
            product_candidates=values.product_candidates,
            interrupted_runs=values.interrupted_runs,
            maximum_active_contexts=values.maximum_active_contexts,
        )

    def snapshot(self, *, inconsistent_ontology: bool = False) -> SaturationSnapshot:
        """Freeze all allocated contexts and the completed property closure."""

        if not isinstance(inconsistent_ontology, bool):
            raise TypeError("inconsistent_ontology must be a boolean")
        property_subsumers = tuple(
            self.properties.super_chains(PropertyChainId(chain))
            for chain in range(self.properties.chain_count)
        )
        property_ranges = tuple(
            self.properties.ranges(EntityId(entity))
            for entity in range(len(self.compiled.entities))
        )
        contexts = {
            root: self._contexts[root].freeze() for root in sorted(self._contexts)
        }
        return SaturationSnapshot(
            property_subsumers=property_subsumers,
            property_ranges=property_ranges,
            contexts=contexts,
            inconsistent_ontology=inconsistent_ontology,
        )

    def _checked_root(self, root: object) -> ExpressionId:
        if (
            isinstance(root, bool)
            or not isinstance(root, int)
            or not 0 <= root < len(self.compiled.expressions)
        ):
            raise ValueError("context root must be an expression ID in the compiled ontology")
        return ExpressionId(root)

    def _ensure_context(self, root: ExpressionId) -> ContextState:
        existing = self._contexts.get(root)
        if existing is not None:
            return existing
        state = ContextState(root)
        self._contexts[root] = state
        self._retry[root] = deque()
        self._diagnostics.contexts_created += 1
        self._enqueue_existing(state, ContextInitialization(root))
        return state

    def _enqueue_existing(self, state: ContextState, conclusion: Conclusion) -> bool:
        self._diagnostics.conclusion_candidates += 1
        if not state.enqueue(conclusion):
            self._diagnostics.duplicate_candidates += 1
            return False
        self._diagnostics.conclusions_enqueued += 1
        if state.root != self._claimed_root and state.mark_queued():
            self._active.append(state.root)
            self._track_active_high_watermark()
        return True

    def _track_active_high_watermark(self) -> None:
        self._diagnostics.maximum_active_contexts = max(
            self._diagnostics.maximum_active_contexts,
            len(self._active),
        )

    def _has_local_work(self, root: ExpressionId, state: ContextState) -> bool:
        return bool(self._retry[root] or state.todo)

    def _next_premise(
        self,
        root: ExpressionId,
        state: ContextState,
    ) -> tuple[Conclusion, bool]:
        retries = self._retry[root]
        if retries:
            return retries.popleft(), False
        premise = state.pop_todo()
        if premise is None:  # pragma: no cover - guarded by _has_local_work
            raise AssertionError("local work disappeared while context was claimed")
        if state.insert(premise):
            self._diagnostics.conclusions_inserted += 1
            return premise, True
        self._diagnostics.duplicate_insertions += 1
        return premise, False

    def _dispatch(
        self,
        root: ExpressionId,
        state: ContextState,
        premise: Conclusion,
    ) -> None:
        producer = _BufferedProducer()
        try:
            self.dispatcher.dispatch(state, premise, producer)
            self._diagnostics.rule_dispatches += 1
            self._diagnostics.product_candidates += len(producer.products)
            for product in producer.products:
                self.enqueue(product)
        except BaseException:
            self._retry[root].appendleft(premise)
            raise


__all__ = [
    "SaturationBudgetExceeded",
    "SaturationDiagnostics",
    "SaturationEngine",
    "SaturationInterrupted",
    "SaturationMonitor",
    "SaturationSnapshot",
]
