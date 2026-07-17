"""Duplicate-suppressing storage for one ELK saturation context."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TypeVar

from pyelk.indexing.ir import DisjointGroupId, EntityId, ExpressionId, PropertyChainId
from pyelk.reasoning.conclusions import (
    BackwardLink,
    ClassInconsistency,
    Conclusion,
    ConclusionKey,
    ContextInitialization,
    DisjointSubsumer,
    ForwardLink,
    Propagation,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
    SubContextInitialization,
    conclusion_destination,
    conclusion_key,
)


@dataclass(frozen=True, slots=True)
class FrozenContext:
    """Deterministic immutable debug view of one context."""

    root: ExpressionId
    initialized: bool
    saturated: bool
    inconsistent: bool
    composed_subsumers: tuple[ExpressionId, ...]
    decomposed_subsumers: tuple[ExpressionId, ...]
    forward_links: tuple[tuple[PropertyChainId, tuple[ExpressionId, ...]], ...]
    backward_links: tuple[tuple[EntityId, tuple[ExpressionId, ...]], ...]
    propagations: tuple[tuple[EntityId, tuple[ExpressionId, ...]], ...]
    disjoint_positions: tuple[tuple[DisjointGroupId, tuple[int, ...]], ...]
    initialized_subcontexts: tuple[EntityId, ...]
    conclusions: tuple[Conclusion, ...]
    todo: tuple[Conclusion, ...]
    queued: bool


@dataclass(slots=True)
class ContextState:
    """Mutable state owned exclusively by a future WP7 scheduler claim."""

    root: ExpressionId
    initialized: bool = False
    saturated: bool = False
    inconsistent: bool = False
    composed_subsumers: set[ExpressionId] = field(default_factory=set)
    decomposed_subsumers: set[ExpressionId] = field(default_factory=set)
    forward_links: dict[PropertyChainId, set[ExpressionId]] = field(default_factory=dict)
    backward_links: dict[EntityId, set[ExpressionId]] = field(default_factory=dict)
    propagations: dict[EntityId, set[ExpressionId]] = field(default_factory=dict)
    disjoint_positions: dict[DisjointGroupId, set[int]] = field(default_factory=dict)
    initialized_subcontexts: set[EntityId] = field(default_factory=set)
    todo: deque[Conclusion] = field(default_factory=deque)
    queued: bool = False
    _conclusions: dict[ConclusionKey, Conclusion] = field(default_factory=dict, repr=False)
    _pending: set[ConclusionKey] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        ContextInitialization(self.root)

    @property
    def conclusion_count(self) -> int:
        return len(self._conclusions)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def enqueue(self, conclusion: Conclusion) -> bool:
        """Queue a novel candidate once, without applying rules recursively."""

        self._check_destination(conclusion)
        key = conclusion_key(conclusion)
        if key in self._conclusions or key in self._pending:
            return False
        self.todo.append(conclusion)
        self._pending.add(key)
        self.saturated = False
        return True

    def pop_todo(self) -> Conclusion | None:
        """Pop one local candidate, returning ``None`` when the local agenda is empty."""

        if not self.todo:
            return None
        conclusion = self.todo.popleft()
        self._pending.discard(conclusion_key(conclusion))
        return conclusion

    def insert(self, conclusion: Conclusion) -> bool:
        """Store one conclusion and update its family index if its identity is novel."""

        self._check_destination(conclusion)
        key = conclusion_key(conclusion)
        self._pending.discard(key)
        if key in self._conclusions:
            return False
        self._conclusions[key] = conclusion
        if isinstance(conclusion, ContextInitialization):
            self.initialized = True
        elif isinstance(conclusion, SubContextInitialization):
            self.initialized_subcontexts.add(conclusion.sub_destination_property)
        elif isinstance(conclusion, SubClassInclusionDecomposed):
            self.decomposed_subsumers.add(conclusion.subsumer)
        elif isinstance(conclusion, SubClassInclusionComposed):
            self.composed_subsumers.add(conclusion.subsumer)
        elif isinstance(conclusion, ForwardLink):
            self.forward_links.setdefault(conclusion.chain, set()).add(conclusion.target)
        elif isinstance(conclusion, BackwardLink):
            self.backward_links.setdefault(conclusion.relation, set()).add(conclusion.source)
        elif isinstance(conclusion, Propagation):
            self.propagations.setdefault(conclusion.relation, set()).add(
                conclusion.carry_existential
            )
        elif isinstance(conclusion, DisjointSubsumer):
            self.disjoint_positions.setdefault(conclusion.disjoint_group, set()).add(
                conclusion.position
            )
        elif isinstance(conclusion, ClassInconsistency):
            self.inconsistent = True
        else:  # pragma: no cover - exhaustive union, retained for hostile runtime callers
            raise TypeError(f"unsupported conclusion type: {type(conclusion).__name__}")
        return True

    def contains(self, conclusion: Conclusion) -> bool:
        self._check_destination(conclusion)
        return conclusion_key(conclusion) in self._conclusions

    def conclusions(self) -> tuple[Conclusion, ...]:
        """Return stored conclusions in stable structural-key order."""

        return tuple(value for _key, value in sorted(self._conclusions.items()))

    def mark_queued(self) -> bool:
        """Set the scheduler queue flag once."""

        if self.queued:
            return False
        self.queued = True
        self.saturated = False
        return True

    def claim(self) -> None:
        """Record exclusive scheduler ownership of this context."""

        self.queued = False
        self.saturated = False

    def mark_saturated(self) -> None:
        """Mark the context idle only after its local agenda is empty."""

        if self.todo:
            raise RuntimeError("cannot saturate a context with pending conclusions")
        self.saturated = True

    def freeze(self) -> FrozenContext:
        """Create a deterministic immutable snapshot without mutating local state."""

        return FrozenContext(
            root=self.root,
            initialized=self.initialized,
            saturated=self.saturated,
            inconsistent=self.inconsistent,
            composed_subsumers=tuple(sorted(self.composed_subsumers)),
            decomposed_subsumers=tuple(sorted(self.decomposed_subsumers)),
            forward_links=_freeze_multimap(self.forward_links),
            backward_links=_freeze_multimap(self.backward_links),
            propagations=_freeze_multimap(self.propagations),
            disjoint_positions=_freeze_multimap(self.disjoint_positions),
            initialized_subcontexts=tuple(sorted(self.initialized_subcontexts)),
            conclusions=self.conclusions(),
            todo=tuple(sorted(self.todo, key=conclusion_key)),
            queued=self.queued,
        )

    def _check_destination(self, conclusion: Conclusion) -> None:
        if conclusion_destination(conclusion) != self.root:
            raise ValueError(
                f"conclusion belongs to context {conclusion_destination(conclusion)}, "
                f"not {self.root}"
            )


_Key = TypeVar("_Key", bound=int)
_Value = TypeVar("_Value", bound=int)


def _freeze_multimap(
    values: dict[_Key, set[_Value]],
) -> tuple[tuple[_Key, tuple[_Value, ...]], ...]:
    return tuple((key, tuple(sorted(members))) for key, members in sorted(values.items()))


__all__ = ["ContextState", "FrozenContext"]
