"""Frozen public-completeness and private backend contracts."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Literal, NewType, Protocol, TypeAlias, runtime_checkable

from pyelk.indexing.ir import U32_MAX, CompiledOntology, EntityId

Polarity: TypeAlias = Literal["ANY", "NEGATIVE", "POSITIVE"]
DiagnosticScalar: TypeAlias = int | float | str | bool
QueryResultEntityId = NewType("QueryResultEntityId", int)


class ReasoningTask(str, Enum):
    """Task families with independently evaluated completeness."""

    CONSISTENCY = "consistency"
    CLASS_TAXONOMY = "class_taxonomy"
    OBJECT_PROPERTY_TAXONOMY = "object_property_taxonomy"
    REALIZATION = "realization"
    CLASS_EXPRESSION_QUERY = "class_expression_query"
    ENTAILMENT_QUERY = "entailment_query"


class PolicyFeature(str, Enum):
    """pyELK ingestion-policy reasons outside the pinned ELK feature enum."""

    IGNORED_IMPORT = "PYELK_IGNORED_IMPORT"


@dataclass(frozen=True, slots=True, order=True)
class CompletenessIssue:
    """One canonical reason that a reasoning result may be incomplete."""

    task: ReasoningTask
    features: tuple[str, ...]
    constructors: tuple[str, ...]
    polarities: tuple[Polarity, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.task, ReasoningTask):
            raise ValueError("completeness issue task must be a ReasoningTask")
        if not all(
            isinstance(values, tuple)
            for values in (self.features, self.constructors, self.polarities)
        ):
            raise ValueError("completeness issue arrays must be tuples")
        if not self.features or any(
            not isinstance(value, str) or not value for value in self.features
        ):
            raise ValueError("completeness issue features must be nonempty strings")
        if len(self.constructors) != len(self.features) or len(self.polarities) != len(
            self.features
        ):
            raise ValueError("feature, constructor, and polarity arrays must have equal lengths")
        if any(not isinstance(value, str) or not value for value in self.constructors):
            raise ValueError("completeness issue constructors must be nonempty strings")
        if any(value not in {"ANY", "NEGATIVE", "POSITIVE"} for value in self.polarities):
            raise ValueError("completeness issue contains an invalid polarity")


@dataclass(frozen=True, slots=True)
class BackendInfo:
    """Backend selected for one live reasoner session."""

    name: Literal["python", "rust"]
    implementation_version: str
    ir_major: int
    ir_minor: int
    requested_workers: int
    effective_workers: int
    native_available: bool
    fallback_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in {"python", "rust"}:
            raise ValueError("backend name must be 'python' or 'rust'")
        if not isinstance(self.implementation_version, str) or not self.implementation_version:
            raise ValueError("backend implementation version must be nonempty")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (self.ir_major, self.ir_minor)
        ):
            raise ValueError("backend IR version components must be nonnegative")
        if (
            not isinstance(self.requested_workers, int)
            or isinstance(self.requested_workers, bool)
            or self.requested_workers < 0
            or not isinstance(self.effective_workers, int)
            or isinstance(self.effective_workers, bool)
            or self.effective_workers < 1
        ):
            raise ValueError("backend worker counts are invalid")
        if not isinstance(self.native_available, bool):
            raise ValueError("backend native_available must be a boolean")
        if self.fallback_reason is not None and not isinstance(self.fallback_reason, str):
            raise ValueError("backend fallback_reason must be text or None")


@dataclass(frozen=True, slots=True)
class BackendAvailability:
    """Side-effect-light availability information for one backend."""

    name: Literal["python", "rust"]
    available: bool | None
    implementation_version: str | None
    ir_major: int | None
    ir_minor: int | None
    abi: str | None
    reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in {"python", "rust"}:
            raise ValueError("backend availability name must be 'python' or 'rust'")
        if self.available is not None and not isinstance(self.available, bool):
            raise ValueError("backend availability must be a boolean or None")
        if self.implementation_version is not None and not isinstance(
            self.implementation_version, str
        ):
            raise ValueError("backend implementation version must be text or None")
        if self.available is True and self.implementation_version is None:
            raise ValueError("an available backend must report its implementation version")
        if (self.ir_major is None) != (self.ir_minor is None):
            raise ValueError("backend IR major and minor versions must be reported together")
        if any(
            value is not None
            and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
            for value in (self.ir_major, self.ir_minor)
        ):
            raise ValueError("backend IR version components must be nonnegative")
        if self.abi is not None and not isinstance(self.abi, str):
            raise ValueError("backend ABI must be text or None")
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError("backend availability reason must be text or None")


@dataclass(frozen=True, slots=True)
class BackendReport:
    """Installed-backend report without creating a reasoning session."""

    requested: str
    selected: Literal["python", "rust"] | None
    python: BackendAvailability
    rust: BackendAvailability
    selection_error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.requested, str):
            raise ValueError("backend report request must be text")
        if self.selected is not None and (
            not isinstance(self.selected, str) or self.selected not in {"python", "rust"}
        ):
            raise ValueError("backend report selection is invalid")
        if not isinstance(self.python, BackendAvailability) or not isinstance(
            self.rust, BackendAvailability
        ):
            raise ValueError("backend report entries must be BackendAvailability values")
        if self.python.name != "python" or self.rust.name != "rust":
            raise ValueError("backend report availability entries are mislabeled")
        if self.selection_error is not None and not isinstance(self.selection_error, str):
            raise ValueError("backend report selection_error must be text or None")


class QueryKind(IntEnum):
    """Class-expression operation encoded by a raw query result."""

    SATISFIABLE = 0
    EQUIVALENT_CLASSES = 1
    SUBCLASSES = 2
    SUPERCLASSES = 3
    INSTANCES = 4


def _valid_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= U32_MAX


def _validate_nodes(nodes: tuple[tuple[int, ...], ...], field: str) -> None:
    if not isinstance(nodes, tuple):
        raise ValueError(f"{field} must be a tuple")
    previous: tuple[int, ...] | None = None
    seen_members: set[int] = set()
    for node in nodes:
        if not isinstance(node, tuple) or not node:
            raise ValueError(f"{field} must contain nonempty member tuples")
        if any(not _valid_id(member) for member in node):
            raise ValueError(f"{field} contains an invalid entity ID")
        if any(node[index - 1] >= node[index] for index in range(1, len(node))):
            raise ValueError(f"{field} node members must be strictly sorted and unique")
        if previous is not None and previous >= node:
            raise ValueError(f"{field} nodes must be strictly sorted and unique")
        for member in node:
            if int(member) in seen_members:
                raise ValueError(f"{field} entity IDs may occur in only one node")
            seen_members.add(int(member))
        previous = node


def _validate_index_pairs(
    values: tuple[tuple[int, int], ...],
    field: str,
    first_limit: int,
    second_limit: int,
    *,
    distinct: bool,
) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{field} must be a tuple")
    previous: tuple[int, int] | None = None
    for value in values:
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValueError(f"{field} must contain index pairs")
        first, second = value
        if (
            not isinstance(first, int)
            or isinstance(first, bool)
            or not isinstance(second, int)
            or isinstance(second, bool)
            or not 0 <= first < first_limit
            or not 0 <= second < second_limit
        ):
            raise ValueError(f"{field} contains an out-of-range index")
        if distinct and first == second:
            raise ValueError(f"{field} cannot contain self edges")
        if previous is not None and previous >= value:
            raise ValueError(f"{field} must be strictly sorted and unique")
        previous = value


def _validate_taxonomy_graph(
    node_count: int,
    direct_edges: tuple[tuple[int, int], ...],
    top: int,
    bottom: int,
) -> None:
    outgoing: list[list[int]] = [[] for _ in range(node_count)]
    incoming: list[list[int]] = [[] for _ in range(node_count)]
    for sub_node, super_node in direct_edges:
        outgoing[sub_node].append(super_node)
        incoming[super_node].append(sub_node)
    if outgoing[top]:
        raise ValueError("taxonomy top node cannot have a strict superclass")
    if incoming[bottom]:
        raise ValueError("taxonomy bottom node cannot have a strict subclass")

    indegrees = [len(values) for values in incoming]
    ready = deque(index for index, indegree in enumerate(indegrees) if indegree == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for successor in outgoing[node]:
            indegrees[successor] -= 1
            if indegrees[successor] == 0:
                ready.append(successor)
    if visited != node_count:
        raise ValueError("taxonomy direct-edge graph must be acyclic")

    if _reachable_nodes(bottom, outgoing) != set(range(node_count)):
        raise ValueError("every taxonomy node must be reachable from bottom")
    if _reachable_nodes(top, incoming) != set(range(node_count)):
        raise ValueError("every taxonomy node must reach top")


def _reachable_nodes(start: int, edges: list[list[int]]) -> set[int]:
    reached = {start}
    pending = [start]
    while pending:
        node = pending.pop()
        for successor in edges[node]:
            if successor not in reached:
                reached.add(successor)
                pending.append(successor)
    return reached


@dataclass(frozen=True, slots=True)
class RawTaxonomy:
    """Backend-neutral taxonomy using entity IDs and canonical node indices."""

    nodes: tuple[tuple[EntityId, ...], ...]
    direct_edges: tuple[tuple[int, int], ...]
    top: int
    bottom: int

    def __post_init__(self) -> None:
        _validate_nodes(self.nodes, "taxonomy nodes")
        if not self.nodes:
            raise ValueError("taxonomy must contain at least one node")
        if (
            not isinstance(self.top, int)
            or isinstance(self.top, bool)
            or not 0 <= self.top < len(self.nodes)
            or not isinstance(self.bottom, int)
            or isinstance(self.bottom, bool)
            or not 0 <= self.bottom < len(self.nodes)
        ):
            raise ValueError("taxonomy top and bottom indices must reference nodes")
        _validate_index_pairs(
            self.direct_edges,
            "taxonomy direct edges",
            len(self.nodes),
            len(self.nodes),
            distinct=True,
        )
        _validate_taxonomy_graph(len(self.nodes), self.direct_edges, self.top, self.bottom)


@dataclass(frozen=True, slots=True)
class RawRealization:
    """Backend-neutral class taxonomy plus individual nodes and direct types."""

    class_taxonomy: RawTaxonomy
    instance_nodes: tuple[tuple[EntityId, ...], ...]
    direct_types: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.class_taxonomy, RawTaxonomy):
            raise ValueError("realization class_taxonomy must be a RawTaxonomy")
        _validate_nodes(self.instance_nodes, "instance nodes")
        _validate_index_pairs(
            self.direct_types,
            "realization direct types",
            len(self.instance_nodes),
            len(self.class_taxonomy.nodes),
            distinct=False,
        )
        typed_instances = {instance_index for instance_index, _ in self.direct_types}
        if typed_instances != set(range(len(self.instance_nodes))):
            raise ValueError("every realization instance node must have a direct type")


@dataclass(frozen=True, slots=True)
class RawQueryResult:
    """Backend-neutral scalar or canonical node collection for one query kind.

    Query-result IDs share a deterministic session namespace. Ontology entities retain their
    compiled IDs. A query's fresh entities use ``ontology_entity_count + fresh_rank``, where
    rank is their position in ``CompiledQuery.fresh_entities``.
    """

    kind: QueryKind
    boolean: bool | None = None
    nodes: tuple[tuple[QueryResultEntityId, ...], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, QueryKind):
            raise ValueError("raw query kind must be a QueryKind")
        _validate_nodes(self.nodes, "query nodes")
        if self.kind is QueryKind.SATISFIABLE:
            if not isinstance(self.boolean, bool) or self.nodes:
                raise ValueError("satisfiability result requires a boolean and no nodes")
        elif self.boolean is not None:
            raise ValueError("node-valued query result cannot contain a boolean")


@runtime_checkable
class BackendConfig(Protocol):
    """Minimal configuration view needed when creating a backend session."""

    workers: int


@runtime_checkable
class BackendSession(Protocol):
    """Coarse-grained immutable-session interface implemented by both backends."""

    @property
    def info(self) -> BackendInfo: ...

    def close(self) -> None: ...

    def is_inconsistent(self) -> bool: ...

    def class_taxonomy(self) -> RawTaxonomy: ...

    def object_property_taxonomy(self) -> RawTaxonomy: ...

    def realization(self) -> RawRealization: ...

    def query_class_expression(
        self, encoded_expression: bytes | None, kind: QueryKind, direct: bool
    ) -> RawQueryResult: ...

    def entails(self, encoded_axiom: bytes | None) -> bool: ...

    def diagnostics(self) -> Mapping[str, DiagnosticScalar]: ...


@runtime_checkable
class BackendFactory(Protocol):
    """Factory interface used by the future dispatcher."""

    def create_session(
        self, compiled: CompiledOntology, config: BackendConfig
    ) -> BackendSession: ...


__all__ = [
    "BackendAvailability",
    "BackendConfig",
    "BackendFactory",
    "BackendInfo",
    "BackendReport",
    "BackendSession",
    "CompletenessIssue",
    "DiagnosticScalar",
    "Polarity",
    "PolicyFeature",
    "QueryKind",
    "QueryResultEntityId",
    "RawQueryResult",
    "RawRealization",
    "RawTaxonomy",
    "ReasoningTask",
]
