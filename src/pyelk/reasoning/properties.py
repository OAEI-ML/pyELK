"""Deterministic pure-Python object-property saturation.

This module is a clean-room implementation of the observable non-incremental behaviour in
ELK 0.6.0's Apache-2.0 ``saturation/properties`` package.  It computes sub-property-chain
closure, inherited ranges, and the symmetric composition indices consumed by later class
saturation.  No proof objects, Java artifacts, or mutable state escape the build call.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, cast

from pyelk.indexing.ir import (
    U32_RESERVED,
    CompiledOntology,
    EntityId,
    EntityKind,
    ExpressionId,
    ExpressionTag,
    PropertyChainId,
)

_NO_SUFFIX: Final = -1


def _checked_id(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < U32_RESERVED:
        raise ValueError(f"{field_name} must be a u32 ID excluding 0xffffffff")
    return value


def _check_sorted_unique(values: tuple[object, ...], field_name: str) -> None:
    ordered = cast(tuple[Any, ...], values)
    if any(ordered[index - 1] >= ordered[index] for index in range(1, len(ordered))):
        raise ValueError(f"{field_name} must be strictly sorted and unique")


@dataclass(frozen=True, slots=True, order=True)
class PropertyChainRecord:
    """One compact right-linked property chain.

    A singleton property has ``suffix_chain=None``.  Complex records point to a shorter
    suffix whose ID precedes this record, reproducing ELK's right-built structural chains
    without materialising every flat suffix of a very long chain.
    """

    first_property: EntityId
    suffix_chain: PropertyChainId | None

    def __post_init__(self) -> None:
        _checked_id(self.first_property, "chain first property")
        if self.suffix_chain is not None:
            _checked_id(self.suffix_chain, "chain suffix")

    @property
    def is_singleton(self) -> bool:
        return self.suffix_chain is None


@dataclass(frozen=True, slots=True, order=True)
class SubPropertyChain:
    """A derived ``sub_chain SubPropertyOf super_chain`` conclusion."""

    sub_chain: PropertyChainId
    super_chain: PropertyChainId

    def __post_init__(self) -> None:
        _checked_id(self.sub_chain, "sub-property chain")
        _checked_id(self.super_chain, "super-property chain")


@dataclass(frozen=True, slots=True, order=True)
class PropertyRange:
    """A range inherited by one named object property."""

    property: EntityId
    range: ExpressionId

    def __post_init__(self) -> None:
        _checked_id(self.property, "range property")
        _checked_id(self.range, "range expression")


@dataclass(frozen=True, slots=True, order=True)
class PropertyComposition:
    """A legal left-property/right-chain composition yielding a complex chain."""

    left_property: EntityId
    right_chain: PropertyChainId
    result_chain: PropertyChainId

    def __post_init__(self) -> None:
        _checked_id(self.left_property, "composition left property")
        _checked_id(self.right_chain, "composition right chain")
        _checked_id(self.result_chain, "composition result chain")


def sub_property_chain_tautology(chain: PropertyChainId) -> SubPropertyChain:
    """Implement pinned ``SubPropertyChainTautology``."""

    _checked_id(chain, "tautology chain")
    return SubPropertyChain(chain, chain)


def sub_property_chain_expanded_sub_object_property_of(
    told_sub_chain: PropertyChainId,
    told_super_chain: PropertyChainId,
    derived_premise: SubPropertyChain | None,
) -> SubPropertyChain | None:
    """Join one told axiom with ``told_super_chain <= derived_super_chain``.

    ``None`` is returned when the required derived premise is absent or incompatible.  This
    explicit negative path keeps the rule independently testable without proof machinery.
    """

    _checked_id(told_sub_chain, "told sub-chain")
    _checked_id(told_super_chain, "told super-chain")
    if derived_premise is None:
        return None
    if not isinstance(derived_premise, SubPropertyChain):
        raise TypeError("derived_premise must be SubPropertyChain or None")
    if derived_premise.sub_chain != told_super_chain:
        return None
    return SubPropertyChain(told_sub_chain, derived_premise.super_chain)


def property_range_inherited(
    sub_property: EntityId,
    super_property: EntityId,
    sub_property_chain: PropertyChainId,
    super_property_chain: PropertyChainId,
    hierarchy_premise: SubPropertyChain | None,
    range_premise: PropertyRange | None,
) -> PropertyRange | None:
    """Implement pinned ``PropertyRangeInherited`` with both premise checks."""

    _checked_id(sub_property, "inherited-range sub-property")
    _checked_id(super_property, "inherited-range super-property")
    _checked_id(sub_property_chain, "inherited-range sub-chain")
    _checked_id(super_property_chain, "inherited-range super-chain")
    if hierarchy_premise is None or range_premise is None:
        return None
    if not isinstance(hierarchy_premise, SubPropertyChain):
        raise TypeError("hierarchy_premise must be SubPropertyChain or None")
    if not isinstance(range_premise, PropertyRange):
        raise TypeError("range_premise must be PropertyRange or None")
    if hierarchy_premise != SubPropertyChain(sub_property_chain, super_property_chain):
        return None
    if range_premise.property != super_property:
        return None
    return PropertyRange(sub_property, range_premise.range)


@dataclass(frozen=True, slots=True)
class PropertySaturation:
    """Immutable property closure and composition view.

    ``chains`` is the saturation-local compact chain table.  ``compiled_chain_ids`` maps
    each input ``CompiledOntology.property_chains`` position to this table; it is necessary
    because saturation derives suffix records that the v1 frozen IR need not list.
    """

    chains: tuple[PropertyChainRecord, ...]
    compiled_chain_ids: tuple[PropertyChainId, ...]
    subproperty_chains: tuple[SubPropertyChain, ...]
    property_ranges: tuple[PropertyRange, ...]
    non_redundant_compositions: tuple[PropertyComposition, ...]
    redundant_compositions: tuple[PropertyComposition, ...]
    reflexive_properties: tuple[EntityId, ...]
    _entity_count: int = field(repr=False, compare=False)
    _expression_count: int = field(repr=False, compare=False)
    _subchains_by_super: tuple[tuple[PropertyChainId, ...], ...] = field(repr=False, compare=False)
    _superchains_by_sub: tuple[tuple[PropertyChainId, ...], ...] = field(repr=False, compare=False)
    _ranges_by_property: Mapping[EntityId, tuple[ExpressionId, ...]] = field(
        repr=False, compare=False
    )
    _non_redundant_by_left: Mapping[
        EntityId, Mapping[PropertyChainId, tuple[PropertyChainId, ...]]
    ] = field(repr=False, compare=False)
    _redundant_by_left: Mapping[EntityId, Mapping[PropertyChainId, tuple[PropertyChainId, ...]]] = (
        field(repr=False, compare=False)
    )
    _non_redundant_by_right: Mapping[
        PropertyChainId, Mapping[EntityId, tuple[PropertyChainId, ...]]
    ] = field(repr=False, compare=False)
    _redundant_by_right: Mapping[
        PropertyChainId, Mapping[EntityId, tuple[PropertyChainId, ...]]
    ] = field(repr=False, compare=False)
    _singleton_chains: Mapping[EntityId, PropertyChainId] = field(repr=False, compare=False)
    _chain_ids_by_key: Mapping[tuple[EntityId, int], PropertyChainId] = field(
        repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.chains, tuple) or not self.chains:
            raise ValueError("property saturation requires a nonempty chain tuple")
        if len(self.chains) >= U32_RESERVED:
            raise ValueError("property saturation exceeds the u32 chain namespace")
        if not isinstance(self._entity_count, int) or not 0 <= self._entity_count < U32_RESERVED:
            raise ValueError("invalid property-saturation entity count")
        if (
            not isinstance(self._expression_count, int)
            or not 0 <= self._expression_count < U32_RESERVED
        ):
            raise ValueError("invalid property-saturation expression count")
        for chain_id, record in enumerate(self.chains):
            if not isinstance(record, PropertyChainRecord):
                raise TypeError("chains must contain PropertyChainRecord values")
            if record.first_property >= self._entity_count:
                raise ValueError("chain property is outside the entity table")
            if record.suffix_chain is not None and record.suffix_chain >= chain_id:
                raise ValueError("chain suffixes must precede their complex chain")
        self._validate_chain_ids(self.compiled_chain_ids, "compiled chain mapping")
        if len(self._subchains_by_super) != len(self.chains) or len(
            self._superchains_by_sub
        ) != len(self.chains):
            raise ValueError("property adjacency arrays must match the chain table")
        for field_name, rows in (
            ("subchains by super", self._subchains_by_super),
            ("superchains by sub", self._superchains_by_sub),
        ):
            for row in rows:
                self._validate_chain_ids(row, field_name)
        self._validate_conclusions()

        object.__setattr__(
            self,
            "_ranges_by_property",
            MappingProxyType(dict(self._ranges_by_property)),
        )
        object.__setattr__(
            self,
            "_non_redundant_by_left",
            _freeze_compositions_by_left(self._non_redundant_by_left),
        )
        object.__setattr__(
            self,
            "_redundant_by_left",
            _freeze_compositions_by_left(self._redundant_by_left),
        )
        object.__setattr__(
            self,
            "_non_redundant_by_right",
            _freeze_compositions_by_right(self._non_redundant_by_right),
        )
        object.__setattr__(
            self,
            "_redundant_by_right",
            _freeze_compositions_by_right(self._redundant_by_right),
        )
        object.__setattr__(
            self,
            "_singleton_chains",
            MappingProxyType(dict(self._singleton_chains)),
        )
        object.__setattr__(
            self,
            "_chain_ids_by_key",
            MappingProxyType(dict(self._chain_ids_by_key)),
        )

    def _validate_chain_ids(self, values: tuple[PropertyChainId, ...], field_name: str) -> None:
        if not isinstance(values, tuple):
            raise TypeError(f"{field_name} must be a tuple")
        if any(not 0 <= value < len(self.chains) for value in values):
            raise ValueError(f"{field_name} contains an out-of-range chain ID")

    def _validate_conclusions(self) -> None:
        for name, values in (
            ("sub-property conclusions", self.subproperty_chains),
            ("property ranges", self.property_ranges),
            ("non-redundant compositions", self.non_redundant_compositions),
            ("redundant compositions", self.redundant_compositions),
        ):
            if not isinstance(values, tuple):
                raise TypeError(f"{name} must be a tuple")
            _check_sorted_unique(values, name)
        for sub_conclusion in self.subproperty_chains:
            if not isinstance(sub_conclusion, SubPropertyChain):
                raise TypeError("invalid sub-property conclusion")
            if sub_conclusion.sub_chain >= len(self.chains) or sub_conclusion.super_chain >= len(
                self.chains
            ):
                raise ValueError("sub-property conclusion contains an out-of-range chain")
        identities = {
            conclusion.sub_chain
            for conclusion in self.subproperty_chains
            if conclusion.sub_chain == conclusion.super_chain
        }
        if len(identities) != len(self.chains):
            raise ValueError("every property chain requires its tautology conclusion")
        for range_conclusion in self.property_ranges:
            if not isinstance(range_conclusion, PropertyRange):
                raise TypeError("invalid property-range conclusion")
            if (
                range_conclusion.property >= self._entity_count
                or range_conclusion.range >= self._expression_count
            ):
                raise ValueError("property-range conclusion contains an out-of-range ID")
        for name, values in (
            ("non-redundant", self.non_redundant_compositions),
            ("redundant", self.redundant_compositions),
        ):
            for composition in values:
                if not isinstance(composition, PropertyComposition):
                    raise TypeError(f"invalid {name} composition")
                if composition.left_property >= self._entity_count:
                    raise ValueError("composition left property is outside the entity table")
                if composition.right_chain >= len(self.chains) or composition.result_chain >= len(
                    self.chains
                ):
                    raise ValueError("composition contains an out-of-range chain")
                if self.chains[composition.result_chain].is_singleton:
                    raise ValueError("a composition result must be a complex chain")
        if set(self.non_redundant_compositions) & set(self.redundant_compositions):
            raise ValueError("a composition cannot be both redundant and non-redundant")
        if not isinstance(self.reflexive_properties, tuple):
            raise TypeError("reflexive properties must be a tuple")
        _check_sorted_unique(self.reflexive_properties, "reflexive properties")
        if any(not 0 <= value < self._entity_count for value in self.reflexive_properties):
            raise ValueError("reflexive property is outside the entity table")

    @property
    def chain_count(self) -> int:
        return len(self.chains)

    def compiled_chain(self, chain: PropertyChainId) -> PropertyChainId:
        """Map one compiled-IR chain ID to the saturation-local chain table."""

        index = _checked_id(chain, "compiled property-chain ID")
        if index >= len(self.compiled_chain_ids):
            raise IndexError("compiled property-chain ID is out of range")
        return self.compiled_chain_ids[index]

    def singleton_chain(self, property_id: EntityId) -> PropertyChainId:
        """Return the singleton chain for one indexed object property."""

        _checked_id(property_id, "object-property ID")
        try:
            return self._singleton_chains[property_id]
        except KeyError as error:
            raise KeyError(f"entity {property_id} is not an indexed object property") from error

    def lookup_chain(self, properties: Iterable[EntityId]) -> PropertyChainId | None:
        """Look up a semantic left-to-right property sequence without recursion."""

        values = tuple(properties)
        if not values:
            raise ValueError("property chains must be nonempty")
        suffix = _NO_SUFFIX
        current: PropertyChainId | None = None
        for property_id in reversed(values):
            _checked_id(property_id, "property-chain member")
            current = self._chain_ids_by_key.get((property_id, suffix))
            if current is None:
                return None
            suffix = int(current)
        return current

    def chain_properties(self, chain: PropertyChainId) -> tuple[EntityId, ...]:
        """Materialise one chain on demand in semantic left-to-right order."""

        current = _checked_id(chain, "property-chain ID")
        if current >= len(self.chains):
            raise IndexError("property-chain ID is out of range")
        values: list[EntityId] = []
        while True:
            record = self.chains[current]
            values.append(record.first_property)
            if record.suffix_chain is None:
                return tuple(values)
            current = record.suffix_chain

    def sub_chains(self, super_chain: PropertyChainId) -> tuple[PropertyChainId, ...]:
        index = _checked_id(super_chain, "super property-chain ID")
        if index >= len(self.chains):
            raise IndexError("super property-chain ID is out of range")
        return self._subchains_by_super[index]

    def super_chains(self, sub_chain: PropertyChainId) -> tuple[PropertyChainId, ...]:
        index = _checked_id(sub_chain, "sub property-chain ID")
        if index >= len(self.chains):
            raise IndexError("sub property-chain ID is out of range")
        return self._superchains_by_sub[index]

    def sub_properties(self, super_chain: PropertyChainId) -> tuple[EntityId, ...]:
        """Return named subproperties of a chain, in entity-ID order."""

        return tuple(
            self.chains[chain].first_property
            for chain in self.sub_chains(super_chain)
            if self.chains[chain].is_singleton
        )

    def ranges(self, property_id: EntityId) -> tuple[ExpressionId, ...]:
        _checked_id(property_id, "range property ID")
        return self._ranges_by_property.get(property_id, ())

    def compositions(
        self,
        left_property: EntityId,
        right_chain: PropertyChainId,
        *,
        redundant: bool = False,
    ) -> tuple[PropertyChainId, ...]:
        """Return result chains for one compatible composition premise pair."""

        _checked_id(left_property, "composition left property")
        _checked_id(right_chain, "composition right chain")
        if right_chain >= len(self.chains):
            raise IndexError("composition right chain is out of range")
        index = self._redundant_by_left if redundant else self._non_redundant_by_left
        by_right = index.get(left_property)
        return () if by_right is None else by_right.get(right_chain, ())

    def compositions_by_right(
        self,
        left_property: EntityId,
        *,
        redundant: bool = False,
    ) -> Mapping[PropertyChainId, tuple[PropertyChainId, ...]]:
        """Return the composition index keyed by right chain for one left property."""

        _checked_id(left_property, "composition left property")
        index = self._redundant_by_left if redundant else self._non_redundant_by_left
        return index.get(left_property, MappingProxyType({}))

    def compositions_by_left(
        self,
        right_chain: PropertyChainId,
        *,
        redundant: bool = False,
    ) -> Mapping[EntityId, tuple[PropertyChainId, ...]]:
        """Return the composition index keyed by left property for one right chain."""

        _checked_id(right_chain, "composition right chain")
        if right_chain >= len(self.chains):
            raise IndexError("composition right chain is out of range")
        index = self._redundant_by_right if redundant else self._non_redundant_by_right
        return index.get(right_chain, MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class _ChainUniverse:
    records: tuple[PropertyChainRecord, ...]
    compiled_ids: tuple[PropertyChainId, ...]
    singleton_ids: dict[EntityId, PropertyChainId]
    ids_by_key: dict[tuple[EntityId, int], PropertyChainId]


class _PropertySaturationBuilder:
    """Mutable single-call builder hidden behind :func:`saturate_properties`."""

    __slots__ = (
        "chains",
        "compiled",
        "explicit_ranges",
        "subchains_by_super",
        "subproperty_conclusions",
        "superchains_by_sub",
        "told_subchains_by_super",
    )

    def __init__(self, compiled: CompiledOntology) -> None:
        self.compiled = compiled
        self.chains = self._build_chain_universe()
        count = len(self.chains.records)
        self.subchains_by_super: list[set[PropertyChainId]] = [set() for _ in range(count)]
        self.superchains_by_sub: list[set[PropertyChainId]] = [set() for _ in range(count)]
        self.subproperty_conclusions: set[SubPropertyChain] = set()
        self.told_subchains_by_super: dict[PropertyChainId, set[PropertyChainId]] = defaultdict(set)
        self.explicit_ranges = tuple(
            PropertyRange(property_id, range_expression)
            for property_id, range_expression in compiled.property_ranges
        )

    def _build_chain_universe(self) -> _ChainUniverse:
        temporary_records: list[tuple[EntityId, int | None]] = []
        temporary_depths: list[int] = []
        temporary_ids: dict[tuple[EntityId, int], int] = {}

        def intern(properties: Iterable[EntityId]) -> int:
            values = tuple(properties)
            if not values:
                raise ValueError("compiled property chains must be nonempty")
            suffix: int | None = None
            for property_id in reversed(values):
                key = (property_id, _NO_SUFFIX if suffix is None else suffix)
                current = temporary_ids.get(key)
                if current is None:
                    current = len(temporary_records)
                    if current >= U32_RESERVED:
                        raise OverflowError("derived property-chain table exceeds u32 capacity")
                    temporary_ids[key] = current
                    temporary_records.append((property_id, suffix))
                    temporary_depths.append(1 if suffix is None else temporary_depths[suffix] + 1)
                suffix = current
            if suffix is None:
                raise AssertionError("nonempty property chain produced no structural root")
            return suffix

        object_properties = tuple(
            EntityId(index)
            for index, entity in enumerate(self.compiled.entities)
            if entity.kind is EntityKind.OBJECT_PROPERTY
        )
        singleton_temporary = {
            property_id: intern((property_id,)) for property_id in object_properties
        }
        compiled_temporary = tuple(intern(chain) for chain in self.compiled.property_chains)

        by_depth: dict[int, list[int]] = defaultdict(list)
        for temporary_id, depth in enumerate(temporary_depths):
            by_depth[depth].append(temporary_id)
        final_ids: dict[int, PropertyChainId] = {}
        final_records: list[PropertyChainRecord] = []
        ids_by_key: dict[tuple[EntityId, int], PropertyChainId] = {}
        for depth in sorted(by_depth):
            ordered = sorted(
                by_depth[depth],
                key=lambda temporary_id: (
                    temporary_records[temporary_id][0],
                    _NO_SUFFIX
                    if temporary_records[temporary_id][1] is None
                    else int(final_ids[cast(int, temporary_records[temporary_id][1])]),
                ),
            )
            for temporary_id in ordered:
                first, temporary_suffix = temporary_records[temporary_id]
                suffix = None if temporary_suffix is None else final_ids[temporary_suffix]
                final_id = PropertyChainId(len(final_records))
                final_ids[temporary_id] = final_id
                final_records.append(PropertyChainRecord(first, suffix))
                ids_by_key[(first, _NO_SUFFIX if suffix is None else int(suffix))] = final_id

        return _ChainUniverse(
            records=tuple(final_records),
            compiled_ids=tuple(final_ids[value] for value in compiled_temporary),
            singleton_ids={
                property_id: final_ids[temporary_id]
                for property_id, temporary_id in singleton_temporary.items()
            },
            ids_by_key=ids_by_key,
        )

    def build(self) -> PropertySaturation:
        agenda: deque[SubPropertyChain] = deque()

        def add(conclusion: SubPropertyChain) -> None:
            if conclusion in self.subproperty_conclusions:
                return
            self.subproperty_conclusions.add(conclusion)
            self.subchains_by_super[conclusion.super_chain].add(conclusion.sub_chain)
            self.superchains_by_sub[conclusion.sub_chain].add(conclusion.super_chain)
            agenda.append(conclusion)

        for chain in range(len(self.chains.records)):
            add(sub_property_chain_tautology(PropertyChainId(chain)))
        for compiled_sub_chain, super_property in self.compiled.subproperty_axioms:
            sub_chain = self.chains.compiled_ids[compiled_sub_chain]
            super_chain = self.chains.singleton_ids[super_property]
            self.told_subchains_by_super[super_chain].add(sub_chain)
            add(SubPropertyChain(sub_chain, super_chain))

        while agenda:
            premise = agenda.popleft()
            for told_sub_chain in self.told_subchains_by_super.get(premise.sub_chain, ()):
                conclusion = sub_property_chain_expanded_sub_object_property_of(
                    told_sub_chain,
                    premise.sub_chain,
                    premise,
                )
                if conclusion is None:
                    raise AssertionError("compatible sub-property premises failed to join")
                add(conclusion)

        ranges = self._inherit_ranges()
        non_redundant, redundant = self._compute_compositions()
        non_redundant_indices = _composition_indices(non_redundant)
        redundant_indices = _composition_indices(redundant)
        reflexive = self._reflexive_properties()
        sub_by_super = tuple(
            tuple(PropertyChainId(value) for value in sorted(values))
            for values in self.subchains_by_super
        )
        super_by_sub = tuple(
            tuple(PropertyChainId(value) for value in sorted(values))
            for values in self.superchains_by_sub
        )
        return PropertySaturation(
            chains=self.chains.records,
            compiled_chain_ids=self.chains.compiled_ids,
            subproperty_chains=tuple(sorted(self.subproperty_conclusions)),
            property_ranges=tuple(sorted(ranges)),
            non_redundant_compositions=tuple(sorted(non_redundant)),
            redundant_compositions=tuple(sorted(redundant)),
            reflexive_properties=tuple(sorted(reflexive)),
            _entity_count=len(self.compiled.entities),
            _expression_count=len(self.compiled.expressions),
            _subchains_by_super=sub_by_super,
            _superchains_by_sub=super_by_sub,
            _ranges_by_property=_range_index(ranges),
            _non_redundant_by_left=non_redundant_indices[0],
            _redundant_by_left=redundant_indices[0],
            _non_redundant_by_right=non_redundant_indices[1],
            _redundant_by_right=redundant_indices[1],
            _singleton_chains=self.chains.singleton_ids,
            _chain_ids_by_key=self.chains.ids_by_key,
        )

    def _inherit_ranges(self) -> set[PropertyRange]:
        conclusions: set[PropertyRange] = set()
        for explicit in self.explicit_ranges:
            super_chain = self.chains.singleton_ids[explicit.property]
            for sub_chain in self.subchains_by_super[super_chain]:
                record = self.chains.records[sub_chain]
                if not record.is_singleton:
                    continue
                relation = SubPropertyChain(sub_chain, super_chain)
                conclusion = property_range_inherited(
                    record.first_property,
                    explicit.property,
                    sub_chain,
                    super_chain,
                    relation,
                    explicit,
                )
                if conclusion is None:
                    raise AssertionError("compatible property-range premises failed to join")
                conclusions.add(conclusion)
        return conclusions

    def _compute_compositions(
        self,
    ) -> tuple[set[PropertyComposition], set[PropertyComposition]]:
        records = self.chains.records
        named_subproperties_cache: dict[PropertyChainId, frozenset[EntityId]] = {}
        left_subcomposable_cache: dict[EntityId, Mapping[EntityId, frozenset[EntityId]]] = {}

        def named_subproperties(chain: PropertyChainId) -> frozenset[EntityId]:
            cached = named_subproperties_cache.get(chain)
            if cached is not None:
                return cached
            result = frozenset(
                records[sub_chain].first_property
                for sub_chain in self.subchains_by_super[chain]
                if records[sub_chain].is_singleton
            )
            named_subproperties_cache[chain] = result
            return result

        def left_subcomposable(
            property_id: EntityId,
        ) -> Mapping[EntityId, frozenset[EntityId]]:
            cached = left_subcomposable_cache.get(property_id)
            if cached is not None:
                return cached
            property_chain = self.chains.singleton_ids[property_id]
            property_subs = named_subproperties(property_chain)
            values: dict[EntityId, set[EntityId]] = defaultdict(set)
            for complex_sub_chain in self.subchains_by_super[property_chain]:
                complex_record = records[complex_sub_chain]
                if complex_record.suffix_chain is None:
                    continue
                shared_left = property_subs & named_subproperties(
                    self.chains.singleton_ids[complex_record.first_property]
                )
                if not shared_left:
                    continue
                for right_property in named_subproperties(complex_record.suffix_chain):
                    values[right_property].update(shared_left)
            result = MappingProxyType({key: frozenset(value) for key, value in values.items()})
            left_subcomposable_cache[property_id] = result
            return result

        non_redundant: set[PropertyComposition] = set()
        redundant: set[PropertyComposition] = set()
        for result_chain, result_record in enumerate(records):
            suffix = result_record.suffix_chain
            if suffix is None:
                continue
            first_property = result_record.first_property
            first_chain = self.chains.singleton_ids[first_property]
            left_candidates = named_subproperties(first_chain)
            right_candidates = self.subchains_by_super[suffix]
            for right_chain in right_candidates:
                if first_chain == suffix and right_chain == result_chain:
                    continue
                redundant_left: frozenset[EntityId] = frozenset()
                right_record = records[right_chain]
                if (
                    right_record.suffix_chain is not None
                    and right_record.suffix_chain in right_candidates
                ):
                    redundant_left = left_subcomposable(first_property).get(
                        right_record.first_property,
                        frozenset(),
                    )
                for left_property in left_candidates:
                    composition = PropertyComposition(
                        left_property,
                        PropertyChainId(right_chain),
                        PropertyChainId(result_chain),
                    )
                    if left_property in redundant_left:
                        redundant.add(composition)
                    else:
                        non_redundant.add(composition)
        non_redundant.difference_update(redundant)
        return non_redundant, redundant

    def _reflexive_properties(self) -> set[EntityId]:
        thing_entity = next(
            EntityId(index)
            for index, entity in enumerate(self.compiled.entities)
            if entity.kind is EntityKind.CLASS
            and entity.iri == "http://www.w3.org/2002/07/owl#Thing"
        )
        thing_expression = next(
            ExpressionId(index)
            for index, expression in enumerate(self.compiled.expressions)
            if expression.tag is ExpressionTag.CLASS and expression.arguments == (thing_entity,)
        )
        result: set[EntityId] = set()
        for sub_expression, super_expression in self.compiled.subclass_axioms:
            if sub_expression != thing_expression:
                continue
            expression = self.compiled.expressions[super_expression]
            if expression.tag is ExpressionTag.OBJECT_HAS_SELF:
                result.add(EntityId(expression.arguments[0]))
        return result


def _range_index(
    ranges: set[PropertyRange],
) -> Mapping[EntityId, tuple[ExpressionId, ...]]:
    values: dict[EntityId, list[ExpressionId]] = defaultdict(list)
    for conclusion in sorted(ranges):
        values[conclusion.property].append(conclusion.range)
    return {key: tuple(value) for key, value in values.items()}


def _freeze_compositions_by_left(
    values: Mapping[EntityId, Mapping[PropertyChainId, tuple[PropertyChainId, ...]]],
) -> Mapping[EntityId, Mapping[PropertyChainId, tuple[PropertyChainId, ...]]]:
    return MappingProxyType({left: MappingProxyType(dict(rows)) for left, rows in values.items()})


def _freeze_compositions_by_right(
    values: Mapping[PropertyChainId, Mapping[EntityId, tuple[PropertyChainId, ...]]],
) -> Mapping[PropertyChainId, Mapping[EntityId, tuple[PropertyChainId, ...]]]:
    return MappingProxyType({right: MappingProxyType(dict(rows)) for right, rows in values.items()})


def _composition_indices(
    compositions: set[PropertyComposition],
) -> tuple[
    Mapping[EntityId, Mapping[PropertyChainId, tuple[PropertyChainId, ...]]],
    Mapping[PropertyChainId, Mapping[EntityId, tuple[PropertyChainId, ...]]],
]:
    by_pair: dict[tuple[EntityId, PropertyChainId], list[PropertyChainId]] = defaultdict(list)
    for composition in sorted(compositions):
        pair = (composition.left_property, composition.right_chain)
        by_pair[pair].append(composition.result_chain)
    by_left: dict[EntityId, dict[PropertyChainId, tuple[PropertyChainId, ...]]] = defaultdict(dict)
    by_right: dict[PropertyChainId, dict[EntityId, tuple[PropertyChainId, ...]]] = defaultdict(dict)
    for (left, right), results in by_pair.items():
        frozen_results = tuple(results)
        by_left[left][right] = frozen_results
        by_right[right][left] = frozen_results
    return (
        dict(by_left),
        dict(by_right),
    )


def saturate_properties(compiled: CompiledOntology) -> PropertySaturation:
    """Compute the complete immutable property closure for one compiled ontology."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    return _PropertySaturationBuilder(compiled).build()


__all__ = [
    "PropertyChainRecord",
    "PropertyComposition",
    "PropertyRange",
    "PropertySaturation",
    "SubPropertyChain",
    "property_range_inherited",
    "saturate_properties",
    "sub_property_chain_expanded_sub_object_property_of",
    "sub_property_chain_tautology",
]
