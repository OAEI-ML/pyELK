"""Structural identities for the nine ELK class-conclusion families.

The values in this module deliberately contain no proof, rule, scheduler, or backend state.
Numeric identifiers are session-local and validated against the frozen IR's u32 namespace.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TypeAlias

from pyelk.indexing.ir import (
    U32_RESERVED,
    DisjointGroupId,
    EntityId,
    ExpressionId,
    PropertyChainId,
)


def _checked_id(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < U32_RESERVED:
        raise ValueError(f"{field_name} must be a u32 ID excluding 0xffffffff")
    return value


class ConclusionKind(IntEnum):
    """Stable discriminator used by compact context identity keys."""

    CONTEXT_INITIALIZATION = 0
    SUBCONTEXT_INITIALIZATION = 1
    SUBCLASS_INCLUSION_DECOMPOSED = 2
    SUBCLASS_INCLUSION_COMPOSED = 3
    FORWARD_LINK = 4
    BACKWARD_LINK = 5
    PROPAGATION = 6
    DISJOINT_SUBSUMER = 7
    CLASS_INCONSISTENCY = 8


@dataclass(frozen=True, slots=True, order=True)
class ContextInitialization:
    """Initialize the context owned by ``root``."""

    root: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.root, "context root")


@dataclass(frozen=True, slots=True, order=True)
class SubContextInitialization:
    """Initialize one property subcontext below ``destination``."""

    destination: ExpressionId
    sub_destination_property: EntityId

    def __post_init__(self) -> None:
        _checked_id(self.destination, "subcontext destination")
        _checked_id(self.sub_destination_property, "subcontext property")


@dataclass(frozen=True, slots=True, order=True)
class SubClassInclusionDecomposed:
    """A decomposed subsumer stored at one destination context."""

    destination: ExpressionId
    subsumer: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.destination, "decomposed destination")
        _checked_id(self.subsumer, "decomposed subsumer")


@dataclass(frozen=True, slots=True, order=True)
class SubClassInclusionComposed:
    """A composed subsumer stored at one destination context."""

    destination: ExpressionId
    subsumer: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.destination, "composed destination")
        _checked_id(self.subsumer, "composed subsumer")


@dataclass(frozen=True, slots=True, order=True)
class ForwardLink:
    """A path from ``destination`` to ``target`` over a saturated property chain."""

    destination: ExpressionId
    chain: PropertyChainId
    target: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.destination, "forward-link destination")
        _checked_id(self.chain, "forward-link chain")
        _checked_id(self.target, "forward-link target")


@dataclass(frozen=True, slots=True, order=True)
class BackwardLink:
    """The reverse view at ``destination`` of a named-property link from ``source``."""

    destination: ExpressionId
    relation: EntityId
    source: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.destination, "backward-link destination")
        _checked_id(self.relation, "backward-link relation")
        _checked_id(self.source, "backward-link source")


@dataclass(frozen=True, slots=True, order=True)
class Propagation:
    """An existential carry authorised for one named relation."""

    destination: ExpressionId
    relation: EntityId
    carry_existential: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.destination, "propagation destination")
        _checked_id(self.relation, "propagation relation")
        _checked_id(self.carry_existential, "propagation carry")


@dataclass(frozen=True, slots=True, order=True)
class DisjointSubsumer:
    """One exact member position of an indexed disjoint group."""

    destination: ExpressionId
    disjoint_group: DisjointGroupId
    position: int

    def __post_init__(self) -> None:
        _checked_id(self.destination, "disjoint-subsumer destination")
        _checked_id(self.disjoint_group, "disjoint group")
        _checked_id(self.position, "disjoint position")


@dataclass(frozen=True, slots=True, order=True)
class ClassInconsistency:
    """Contradiction local to one context."""

    destination: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.destination, "inconsistency destination")


Conclusion: TypeAlias = (
    ContextInitialization
    | SubContextInitialization
    | SubClassInclusionDecomposed
    | SubClassInclusionComposed
    | ForwardLink
    | BackwardLink
    | Propagation
    | DisjointSubsumer
    | ClassInconsistency
)
ConclusionKey: TypeAlias = tuple[int, ...]


def conclusion_destination(conclusion: Conclusion) -> ExpressionId:
    """Return the context that owns ``conclusion``."""

    if isinstance(conclusion, ContextInitialization):
        return conclusion.root
    if isinstance(
        conclusion,
        (
            SubContextInitialization,
            SubClassInclusionDecomposed,
            SubClassInclusionComposed,
            ForwardLink,
            BackwardLink,
            Propagation,
            DisjointSubsumer,
            ClassInconsistency,
        ),
    ):
        return conclusion.destination
    raise TypeError(f"unsupported conclusion type: {type(conclusion).__name__}")


def conclusion_key(conclusion: Conclusion) -> ConclusionKey:
    """Encode one structural identity as a compact, stable integer tuple."""

    if isinstance(conclusion, ContextInitialization):
        return (ConclusionKind.CONTEXT_INITIALIZATION, conclusion.root)
    if isinstance(conclusion, SubContextInitialization):
        return (
            ConclusionKind.SUBCONTEXT_INITIALIZATION,
            conclusion.destination,
            conclusion.sub_destination_property,
        )
    if isinstance(conclusion, SubClassInclusionDecomposed):
        return (
            ConclusionKind.SUBCLASS_INCLUSION_DECOMPOSED,
            conclusion.destination,
            conclusion.subsumer,
        )
    if isinstance(conclusion, SubClassInclusionComposed):
        return (
            ConclusionKind.SUBCLASS_INCLUSION_COMPOSED,
            conclusion.destination,
            conclusion.subsumer,
        )
    if isinstance(conclusion, ForwardLink):
        return (
            ConclusionKind.FORWARD_LINK,
            conclusion.destination,
            conclusion.chain,
            conclusion.target,
        )
    if isinstance(conclusion, BackwardLink):
        return (
            ConclusionKind.BACKWARD_LINK,
            conclusion.destination,
            conclusion.relation,
            conclusion.source,
        )
    if isinstance(conclusion, Propagation):
        return (
            ConclusionKind.PROPAGATION,
            conclusion.destination,
            conclusion.relation,
            conclusion.carry_existential,
        )
    if isinstance(conclusion, DisjointSubsumer):
        return (
            ConclusionKind.DISJOINT_SUBSUMER,
            conclusion.destination,
            conclusion.disjoint_group,
            conclusion.position,
        )
    if isinstance(conclusion, ClassInconsistency):
        return (ConclusionKind.CLASS_INCONSISTENCY, conclusion.destination)
    raise TypeError(f"unsupported conclusion type: {type(conclusion).__name__}")


__all__ = [
    "BackwardLink",
    "ClassInconsistency",
    "Conclusion",
    "ConclusionKey",
    "ConclusionKind",
    "ContextInitialization",
    "DisjointSubsumer",
    "ForwardLink",
    "Propagation",
    "SubClassInclusionComposed",
    "SubClassInclusionDecomposed",
    "SubContextInitialization",
    "conclusion_destination",
    "conclusion_key",
]
