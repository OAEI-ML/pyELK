"""Packed v1 wire format for native-backend raw results."""

from __future__ import annotations

from enum import IntEnum
from typing import TypeAlias

from pyelk.exceptions import BackendProtocolError
from pyelk.indexing.codec import (
    _PAIR_U32,
    _U8,
    _decode_container,
    _decode_csr,
    _DecodedSection,
    _encode_container,
    _encode_csr,
    _EncodedSection,
    _pack_pairs,
    _unpack_fixed,
    _unpack_pairs,
)
from pyelk.indexing.ir import EntityId, ReadableBuffer
from pyelk.reasoning.contracts import (
    QueryKind,
    QueryResultEntityId,
    RawQueryResult,
    RawRealization,
    RawTaxonomy,
)

RAW_MAGIC = b"PYELKRAW"

RawResult: TypeAlias = RawTaxonomy | RawRealization | RawQueryResult


class WireResultKind(IntEnum):
    """Top-level raw value carried by one wire payload."""

    TAXONOMY = 0
    REALIZATION = 1
    QUERY = 2


class WireSection(IntEnum):
    """Known v1 raw-result sections."""

    RESULT_KIND = 1
    NODE_OFFSETS = 2
    NODE_MEMBERS = 3
    DIRECT_EDGES = 4
    TOP_BOTTOM = 5
    INSTANCE_NODE_OFFSETS = 6
    INSTANCE_NODE_MEMBERS = 7
    DIRECT_TYPES = 8
    QUERY_KIND = 9
    QUERY_BOOLEAN = 10


_KNOWN_TAGS = frozenset(int(tag) for tag in WireSection)
_TAXONOMY_TAGS = frozenset(
    {
        int(WireSection.RESULT_KIND),
        int(WireSection.NODE_OFFSETS),
        int(WireSection.NODE_MEMBERS),
        int(WireSection.DIRECT_EDGES),
        int(WireSection.TOP_BOTTOM),
    }
)
_REALIZATION_TAGS = frozenset(
    {
        *_TAXONOMY_TAGS,
        int(WireSection.INSTANCE_NODE_OFFSETS),
        int(WireSection.INSTANCE_NODE_MEMBERS),
        int(WireSection.DIRECT_TYPES),
    }
)
_QUERY_TAGS = frozenset(
    {
        int(WireSection.RESULT_KIND),
        int(WireSection.NODE_OFFSETS),
        int(WireSection.NODE_MEMBERS),
        int(WireSection.QUERY_KIND),
        int(WireSection.QUERY_BOOLEAN),
    }
)


def _kind_section(kind: WireResultKind) -> _EncodedSection:
    return _EncodedSection(WireSection.RESULT_KIND, 1, _U8.pack(int(kind)))


def _node_sections(nodes: tuple[tuple[int, ...], ...]) -> tuple[_EncodedSection, ...]:
    offsets, members, member_count = _encode_csr(
        tuple(tuple(int(member) for member in node) for node in nodes)
    )
    return (
        _EncodedSection(WireSection.NODE_OFFSETS, len(nodes), offsets),
        _EncodedSection(WireSection.NODE_MEMBERS, member_count, members),
    )


def encode_raw_taxonomy(taxonomy: RawTaxonomy) -> bytes:
    """Encode a canonical raw taxonomy."""

    if not isinstance(taxonomy, RawTaxonomy):
        raise TypeError("encode_raw_taxonomy expects RawTaxonomy")
    sections = (
        _kind_section(WireResultKind.TAXONOMY),
        *_node_sections(taxonomy.nodes),
        _EncodedSection(
            WireSection.DIRECT_EDGES,
            len(taxonomy.direct_edges),
            _pack_pairs(taxonomy.direct_edges),
        ),
        _EncodedSection(WireSection.TOP_BOTTOM, 1, _PAIR_U32.pack(taxonomy.top, taxonomy.bottom)),
    )
    return _encode_container(RAW_MAGIC, sections)


def encode_raw_realization(realization: RawRealization) -> bytes:
    """Encode a canonical raw realization."""

    if not isinstance(realization, RawRealization):
        raise TypeError("encode_raw_realization expects RawRealization")
    instance_offsets, instance_members, member_count = _encode_csr(
        tuple(tuple(int(member) for member in node) for node in realization.instance_nodes)
    )
    sections = (
        _kind_section(WireResultKind.REALIZATION),
        *_node_sections(realization.class_taxonomy.nodes),
        _EncodedSection(
            WireSection.DIRECT_EDGES,
            len(realization.class_taxonomy.direct_edges),
            _pack_pairs(realization.class_taxonomy.direct_edges),
        ),
        _EncodedSection(
            WireSection.TOP_BOTTOM,
            1,
            _PAIR_U32.pack(realization.class_taxonomy.top, realization.class_taxonomy.bottom),
        ),
        _EncodedSection(
            WireSection.INSTANCE_NODE_OFFSETS,
            len(realization.instance_nodes),
            instance_offsets,
        ),
        _EncodedSection(WireSection.INSTANCE_NODE_MEMBERS, member_count, instance_members),
        _EncodedSection(
            WireSection.DIRECT_TYPES,
            len(realization.direct_types),
            _pack_pairs(realization.direct_types),
        ),
    )
    return _encode_container(RAW_MAGIC, sections)


def encode_raw_query_result(result: RawQueryResult) -> bytes:
    """Encode a canonical raw class-expression query result."""

    if not isinstance(result, RawQueryResult):
        raise TypeError("encode_raw_query_result expects RawQueryResult")
    boolean_payload = b"" if result.boolean is None else _U8.pack(int(result.boolean))
    boolean_count = 0 if result.boolean is None else 1
    sections = (
        _kind_section(WireResultKind.QUERY),
        *_node_sections(result.nodes),
        _EncodedSection(WireSection.QUERY_KIND, 1, _U8.pack(int(result.kind))),
        _EncodedSection(WireSection.QUERY_BOOLEAN, boolean_count, boolean_payload),
    )
    return _encode_container(RAW_MAGIC, sections)


def encode_raw_result(result: RawResult) -> bytes:
    """Encode any frozen raw result value."""

    if isinstance(result, RawTaxonomy):
        return encode_raw_taxonomy(result)
    if isinstance(result, RawRealization):
        return encode_raw_realization(result)
    if isinstance(result, RawQueryResult):
        return encode_raw_query_result(result)
    raise TypeError(f"unsupported raw result type: {type(result).__name__}")


def _decode_sections(data: ReadableBuffer) -> tuple[WireResultKind, dict[int, _DecodedSection]]:
    sections = _decode_container(
        data,
        magic=RAW_MAGIC,
        required_tags=frozenset({int(WireSection.RESULT_KIND)}),
        known_tags=_KNOWN_TAGS,
    )
    values = _unpack_fixed(sections[WireSection.RESULT_KIND], _U8, "wire result kind")
    if len(values) != 1:
        raise BackendProtocolError("one wire result kind", len(values))
    try:
        return WireResultKind(values[0]), sections
    except ValueError as error:
        raise BackendProtocolError("a valid WireResultKind", values[0]) from error


def _require_exact_sections(
    sections: dict[int, _DecodedSection], required: frozenset[int], name: str
) -> None:
    actual = frozenset(sections)
    if actual != required:
        raise BackendProtocolError(f"exact {name} sections {sorted(required)}", sorted(actual))


def _decode_nodes(
    sections: dict[int, _DecodedSection],
    offsets_tag: WireSection,
    members_tag: WireSection,
    name: str,
) -> tuple[tuple[EntityId, ...], ...]:
    return tuple(
        tuple(EntityId(member) for member in row)
        for row in _decode_csr(sections[offsets_tag], sections[members_tag], name)
    )


def _decode_query_nodes(
    sections: dict[int, _DecodedSection],
    offsets_tag: WireSection,
    members_tag: WireSection,
    name: str,
) -> tuple[tuple[QueryResultEntityId, ...], ...]:
    return tuple(
        tuple(QueryResultEntityId(member) for member in row)
        for row in _decode_csr(sections[offsets_tag], sections[members_tag], name)
    )


def _decode_taxonomy_sections(sections: dict[int, _DecodedSection]) -> RawTaxonomy:
    top_bottom = _unpack_pairs(sections[WireSection.TOP_BOTTOM], "taxonomy top/bottom")
    if len(top_bottom) != 1:
        raise BackendProtocolError("one taxonomy top/bottom pair", len(top_bottom))
    try:
        return RawTaxonomy(
            nodes=_decode_nodes(
                sections,
                WireSection.NODE_OFFSETS,
                WireSection.NODE_MEMBERS,
                "taxonomy nodes",
            ),
            direct_edges=_unpack_pairs(sections[WireSection.DIRECT_EDGES], "taxonomy direct edges"),
            top=top_bottom[0][0],
            bottom=top_bottom[0][1],
        )
    except ValueError as error:
        raise BackendProtocolError("a valid RawTaxonomy", str(error)) from error


def decode_raw_taxonomy(data: ReadableBuffer) -> RawTaxonomy:
    """Decode a taxonomy wire payload."""

    kind, sections = _decode_sections(data)
    if kind is not WireResultKind.TAXONOMY:
        raise BackendProtocolError("a taxonomy wire result", kind.name)
    _require_exact_sections(sections, _TAXONOMY_TAGS, "taxonomy")
    return _decode_taxonomy_sections(sections)


def decode_raw_realization(data: ReadableBuffer) -> RawRealization:
    """Decode a realization wire payload."""

    kind, sections = _decode_sections(data)
    if kind is not WireResultKind.REALIZATION:
        raise BackendProtocolError("a realization wire result", kind.name)
    _require_exact_sections(sections, _REALIZATION_TAGS, "realization")
    taxonomy = _decode_taxonomy_sections(sections)
    try:
        return RawRealization(
            class_taxonomy=taxonomy,
            instance_nodes=_decode_nodes(
                sections,
                WireSection.INSTANCE_NODE_OFFSETS,
                WireSection.INSTANCE_NODE_MEMBERS,
                "instance nodes",
            ),
            direct_types=_unpack_pairs(
                sections[WireSection.DIRECT_TYPES], "realization direct types"
            ),
        )
    except ValueError as error:
        raise BackendProtocolError("a valid RawRealization", str(error)) from error


def decode_raw_query_result(data: ReadableBuffer) -> RawQueryResult:
    """Decode a class-expression query wire payload."""

    kind, sections = _decode_sections(data)
    if kind is not WireResultKind.QUERY:
        raise BackendProtocolError("a query wire result", kind.name)
    _require_exact_sections(sections, _QUERY_TAGS, "query")
    query_kind_values = _unpack_fixed(sections[WireSection.QUERY_KIND], _U8, "query kind")
    if len(query_kind_values) != 1:
        raise BackendProtocolError("one query kind", len(query_kind_values))
    try:
        query_kind = QueryKind(query_kind_values[0])
    except ValueError as error:
        raise BackendProtocolError("a valid QueryKind", query_kind_values[0]) from error
    boolean_values = _unpack_fixed(sections[WireSection.QUERY_BOOLEAN], _U8, "query boolean")
    if len(boolean_values) > 1:
        raise BackendProtocolError("at most one query boolean", len(boolean_values))
    if any(value not in {0, 1} for value in boolean_values):
        raise BackendProtocolError("a zero/one query boolean", boolean_values)
    boolean = None if not boolean_values else bool(boolean_values[0])
    try:
        return RawQueryResult(
            kind=query_kind,
            boolean=boolean,
            nodes=_decode_query_nodes(
                sections,
                WireSection.NODE_OFFSETS,
                WireSection.NODE_MEMBERS,
                "query nodes",
            ),
        )
    except ValueError as error:
        raise BackendProtocolError("a valid RawQueryResult", str(error)) from error


def decode_raw_result(data: ReadableBuffer) -> RawResult:
    """Decode any frozen raw-result payload."""

    kind, _ = _decode_sections(data)
    if kind is WireResultKind.TAXONOMY:
        return decode_raw_taxonomy(data)
    if kind is WireResultKind.REALIZATION:
        return decode_raw_realization(data)
    return decode_raw_query_result(data)


__all__ = [
    "RAW_MAGIC",
    "RawResult",
    "WireResultKind",
    "WireSection",
    "decode_raw_query_result",
    "decode_raw_realization",
    "decode_raw_result",
    "decode_raw_taxonomy",
    "encode_raw_query_result",
    "encode_raw_realization",
    "encode_raw_result",
    "encode_raw_taxonomy",
]
