"""Immutable canonical public reasoning values."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

import pyowl_core as owl

from pyelk.exceptions import IncompleteReasoningError
from pyelk.reasoning.contracts import CompletenessIssue, PolicyFeature, ReasoningTask

T = TypeVar("T")
E = TypeVar("E", bound=owl.Entity)


def _entity_key(entity: owl.Entity) -> bytes:
    if not isinstance(entity, owl.Entity):
        raise TypeError("node members must be pyowl-core Entity values")
    return owl.canonical_bytes(entity)


def _node_key(node: EntityNode[E]) -> tuple[bytes, ...]:
    return tuple(_entity_key(member) for member in node.members)


@dataclass(frozen=True, slots=True)
class ReasoningResult(Generic[T]):
    """One value plus its exact ELK-compatible completeness metadata."""

    value: T
    complete: bool
    reasons: tuple[CompletenessIssue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.complete, bool):
            raise TypeError("complete must be a boolean")
        if not isinstance(self.reasons, tuple):
            raise TypeError("reasons must be a tuple")
        if any(not isinstance(reason, CompletenessIssue) for reason in self.reasons):
            raise TypeError("reasons must contain CompletenessIssue values")
        canonical = tuple(sorted(set(self.reasons)))
        object.__setattr__(self, "reasons", canonical)
        if self.complete != (not canonical):
            raise ValueError("complete must be true exactly when reasons is empty")

    def require_complete(self) -> T:
        """Return the value, or reject use of a potentially incomplete result."""

        if self.reasons:
            raise IncompleteReasoningError(self.reasons)
        return self.value


@dataclass(frozen=True, slots=True)
class EntityNode(Generic[E]):
    """One nonempty canonical equivalence node."""

    members: tuple[E, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.members, tuple):
            raise TypeError("node members must be a tuple")
        if not self.members:
            raise ValueError("an entity node cannot be empty")
        keyed: dict[bytes, E] = {}
        for member in self.members:
            key = _entity_key(member)
            previous = keyed.get(key)
            if previous is not None and previous != member:
                raise ValueError("distinct entities have the same canonical key")
            keyed[key] = member
        object.__setattr__(self, "members", tuple(keyed[key] for key in sorted(keyed)))

    @property
    def canonical_member(self) -> E:
        """Return the first member under pyowl-core canonical ordering."""

        return self.members[0]


@dataclass(frozen=True, slots=True)
class Taxonomy(Generic[E]):
    """A canonical equivalence quotient with direct sub-to-super edges."""

    nodes: tuple[EntityNode[E], ...]
    direct_edges: tuple[tuple[EntityNode[E], EntityNode[E]], ...]
    top: EntityNode[E]
    bottom: EntityNode[E]

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple):
            raise TypeError("taxonomy nodes must be a tuple")
        if not self.nodes:
            raise ValueError("taxonomy must contain at least one node")
        if any(not isinstance(node, EntityNode) for node in self.nodes):
            raise TypeError("taxonomy nodes must contain EntityNode values")
        keyed_nodes: dict[tuple[bytes, ...], EntityNode[E]] = {}
        member_keys: set[bytes] = set()
        member_kinds: set[owl.EntityKind] = set()
        for node in self.nodes:
            key = _node_key(node)
            if key in keyed_nodes:
                continue
            overlap = member_keys.intersection(key)
            if overlap:
                raise ValueError("an entity may occur in only one taxonomy node")
            member_keys.update(key)
            member_kinds.update(member.kind for member in node.members)
            keyed_nodes[key] = node
        if len(member_kinds) != 1:
            raise ValueError("a taxonomy must contain one entity kind")
        nodes = tuple(keyed_nodes[key] for key in sorted(keyed_nodes))
        canonical_by_key = {_node_key(node): node for node in nodes}

        if not isinstance(self.top, EntityNode) or not isinstance(self.bottom, EntityNode):
            raise TypeError("taxonomy bounds must be EntityNode values")
        try:
            top = canonical_by_key[_node_key(self.top)]
            bottom = canonical_by_key[_node_key(self.bottom)]
        except KeyError as error:
            raise ValueError("taxonomy bounds must occur in nodes") from error

        if not isinstance(self.direct_edges, tuple):
            raise TypeError("taxonomy direct_edges must be a tuple")
        edge_keys: set[tuple[tuple[bytes, ...], tuple[bytes, ...]]] = set()
        for edge in self.direct_edges:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise TypeError("taxonomy direct_edges must contain node pairs")
            sub, sup = edge
            if not isinstance(sub, EntityNode) or not isinstance(sup, EntityNode):
                raise TypeError("taxonomy edges must reference EntityNode values")
            sub_key, sup_key = _node_key(sub), _node_key(sup)
            if sub_key not in canonical_by_key or sup_key not in canonical_by_key:
                raise ValueError("taxonomy edges must reference taxonomy nodes")
            if sub_key == sup_key:
                raise ValueError("taxonomy direct edges cannot be self edges")
            edge_keys.add((sub_key, sup_key))
        edges = tuple(
            (canonical_by_key[sub_key], canonical_by_key[sup_key])
            for sub_key, sup_key in sorted(edge_keys)
        )
        _validate_graph(nodes, edges, top, bottom)
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "direct_edges", edges)
        object.__setattr__(self, "top", top)
        object.__setattr__(self, "bottom", bottom)

    def node(self, entity: E) -> EntityNode[E] | None:
        """Return the equivalence node containing ``entity`` if it is present."""

        key = _entity_key(entity)
        for node in self.nodes:
            if any(_entity_key(member) == key for member in node.members):
                return node
        return None

    def subs(self, entity: E, *, direct: bool = False) -> tuple[EntityNode[E], ...]:
        """Return strict direct or transitive subnodes of an entity's node."""

        return self._relatives(entity, direct=direct, supers=False)

    def supers(self, entity: E, *, direct: bool = False) -> tuple[EntityNode[E], ...]:
        """Return strict direct or transitive supernodes of an entity's node."""

        return self._relatives(entity, direct=direct, supers=True)

    def _relatives(self, entity: E, *, direct: bool, supers: bool) -> tuple[EntityNode[E], ...]:
        if not isinstance(direct, bool):
            raise TypeError("direct must be a boolean")
        start = self.node(entity)
        if start is None:
            return ()
        adjacency: dict[EntityNode[E], list[EntityNode[E]]] = {node: [] for node in self.nodes}
        for sub, sup in self.direct_edges:
            source, target = (sub, sup) if supers else (sup, sub)
            adjacency[source].append(target)
        selected = set(adjacency[start])
        if not direct:
            pending = list(selected)
            while pending:
                node = pending.pop()
                for target in adjacency[node]:
                    if target not in selected:
                        selected.add(target)
                        pending.append(target)
        return tuple(sorted(selected, key=_node_key))


@dataclass(frozen=True, slots=True)
class InstanceTaxonomy:
    """Canonical individual equivalence nodes and their minimal named types."""

    class_taxonomy: Taxonomy[owl.Class]
    instances: tuple[EntityNode[owl.NamedIndividual], ...]
    direct_types: tuple[tuple[EntityNode[owl.NamedIndividual], EntityNode[owl.Class]], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.class_taxonomy, Taxonomy):
            raise TypeError("class_taxonomy must be a Taxonomy")
        if not isinstance(self.instances, tuple):
            raise TypeError("instances must be a tuple")
        if any(not isinstance(node, EntityNode) for node in self.instances):
            raise TypeError("instances must contain EntityNode values")
        keyed_instances = {_node_key(node): node for node in self.instances}
        instances = tuple(keyed_instances[key] for key in sorted(keyed_instances))
        instance_by_key = {_node_key(node): node for node in instances}
        class_by_key = {_node_key(node): node for node in self.class_taxonomy.nodes}
        if any(
            not isinstance(member, owl.Class)
            for node in self.class_taxonomy.nodes
            for member in node.members
        ):
            raise TypeError("class_taxonomy must contain Class values")
        observed_individuals: set[bytes] = set()
        for node in instances:
            if any(not isinstance(member, owl.NamedIndividual) for member in node.members):
                raise TypeError("instance nodes must contain NamedIndividual values")
            keys = {_entity_key(member) for member in node.members}
            if observed_individuals.intersection(keys):
                raise ValueError("an individual may occur in only one instance node")
            observed_individuals.update(keys)

        if not isinstance(self.direct_types, tuple):
            raise TypeError("direct_types must be a tuple")
        edge_keys: set[tuple[tuple[bytes, ...], tuple[bytes, ...]]] = set()
        for edge in self.direct_types:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise TypeError("direct_types must contain node pairs")
            instance, class_node = edge
            if not isinstance(instance, EntityNode) or not isinstance(class_node, EntityNode):
                raise TypeError("direct_types must reference EntityNode values")
            instance_key, class_key = _node_key(instance), _node_key(class_node)
            if instance_key not in instance_by_key or class_key not in class_by_key:
                raise ValueError("direct_types must reference instance and class taxonomy nodes")
            edge_keys.add((instance_key, class_key))
        typed = {instance_key for instance_key, _ in edge_keys}
        if typed != set(instance_by_key):
            raise ValueError("every instance node must have at least one direct type")
        direct_types = tuple(
            (instance_by_key[instance_key], class_by_key[class_key])
            for instance_key, class_key in sorted(edge_keys)
        )
        object.__setattr__(self, "instances", instances)
        object.__setattr__(self, "direct_types", direct_types)


def _validate_graph(
    nodes: tuple[EntityNode[E], ...],
    edges: tuple[tuple[EntityNode[E], EntityNode[E]], ...],
    top: EntityNode[E],
    bottom: EntityNode[E],
) -> None:
    outgoing: dict[EntityNode[E], list[EntityNode[E]]] = {node: [] for node in nodes}
    incoming: dict[EntityNode[E], list[EntityNode[E]]] = {node: [] for node in nodes}
    for sub, sup in edges:
        outgoing[sub].append(sup)
        incoming[sup].append(sub)
    if outgoing[top]:
        raise ValueError("taxonomy top cannot have a strict superclass")
    if incoming[bottom]:
        raise ValueError("taxonomy bottom cannot have a strict subclass")
    indegrees = {node: len(incoming[node]) for node in nodes}
    ready = deque(node for node in nodes if indegrees[node] == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for successor in outgoing[node]:
            indegrees[successor] -= 1
            if indegrees[successor] == 0:
                ready.append(successor)
    if visited != len(nodes):
        raise ValueError("taxonomy direct edges must be acyclic")


__all__ = [
    "CompletenessIssue",
    "EntityNode",
    "InstanceTaxonomy",
    "PolicyFeature",
    "ReasoningResult",
    "ReasoningTask",
    "Taxonomy",
]
