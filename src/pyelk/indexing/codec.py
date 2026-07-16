"""Deterministic and defensive v1 codecs for compiled ontology and query IR.

Container layout is little-endian::

    magic[8] | major:u16 | minor:u16 | section_count:u32
    section_count * (tag:u16 | offset:u64 | length:u64 | count:u64)
    contiguous section payloads in strictly increasing tag order
    blake2b-256(section payload bytes)

Tags below ``0x8000`` are required protocol tags. A future same-major writer may append
optional tags at or above ``0x8000``; older readers validate and skip those sections.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from hashlib import blake2b
from struct import Struct
from typing import TypeVar

from pyelk.exceptions import BackendProtocolError
from pyelk.indexing.ir import (
    U32_RESERVED,
    CompiledOntology,
    EntityId,
    EntityKind,
    EntityRecord,
    ExpressionId,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    PropertyChainId,
    PropertyOccurrence,
    QueryEntityRecord,
    QueryIR,
    QueryIRKind,
    ReadableBuffer,
)

IR_MAGIC = b"PYELKIR\0"
QUERY_MAGIC = b"PYELKQ\0\0"
SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
OPTIONAL_TAG_START = 0x8000
CHECKSUM_SIZE = 32
MAX_SECTIONS = 256

_HEADER = Struct("<8sHHI")
_DIRECTORY_ENTRY = Struct("<HQQQ")
_U8 = Struct("<B")
_U32 = Struct("<I")
_U64 = Struct("<Q")
_PAIR_U32 = Struct("<II")
_OCCURRENCE = Struct("<QQ")


class OntologySection(IntEnum):
    """Required v1 ontology section tags."""

    ENTITY_KINDS = 1
    ENTITY_IRI_OFFSETS = 2
    ENTITY_IRI_BYTES = 3
    EXPRESSION_TAGS = 4
    EXPRESSION_ARGUMENT_OFFSETS = 5
    EXPRESSION_ARGUMENTS = 6
    EXPRESSION_PAYLOAD_OFFSETS = 7
    EXPRESSION_PAYLOAD_BYTES = 8
    EXPRESSION_OCCURRENCES = 9
    PROPERTY_OCCURRENCES = 10
    PROPERTY_CHAIN_OFFSETS = 11
    PROPERTY_CHAIN_VALUES = 12
    SUBCLASS_AXIOMS = 13
    EQUIVALENT_CLASS_AXIOMS = 14
    DISJOINT_GROUP_OFFSETS = 15
    DISJOINT_GROUP_VALUES = 16
    SUBPROPERTY_AXIOMS = 17
    PROPERTY_RANGES = 18
    FEATURE_COUNTS = 19
    SOURCE_FINGERPRINT = 20


class QuerySection(IntEnum):
    """Required v1 query mini-IR section tags."""

    KIND = 1
    ENTITY_KINDS = 2
    ENTITY_IRI_OFFSETS = 3
    ENTITY_IRI_BYTES = 4
    ENTITY_ONTOLOGY_IDS = 5
    EXPRESSION_TAGS = 6
    EXPRESSION_ARGUMENT_OFFSETS = 7
    EXPRESSION_ARGUMENTS = 8
    EXPRESSION_PAYLOAD_OFFSETS = 9
    EXPRESSION_PAYLOAD_BYTES = 10
    EXPRESSION_OCCURRENCES = 11
    PROPERTY_OCCURRENCES = 12
    ROOT_EXPRESSION = 13
    SUBSUMPTION_OBLIGATIONS = 14


@dataclass(frozen=True, slots=True)
class _EncodedSection:
    tag: int
    count: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class _DecodedSection:
    count: int
    payload: bytes


def _protocol_error(expected: str, actual: object) -> BackendProtocolError:
    return BackendProtocolError(expected, actual)


def _pack_values(values: tuple[int, ...], item: Struct) -> bytes:
    return b"".join(item.pack(value) for value in values)


def _pack_pairs(values: tuple[tuple[int, int], ...]) -> bytes:
    return b"".join(_PAIR_U32.pack(first, second) for first, second in values)


def _encode_csr(rows: tuple[tuple[int, ...], ...]) -> tuple[bytes, bytes, int]:
    offsets = [0]
    flattened: list[int] = []
    for row in rows:
        flattened.extend(row)
        offsets.append(len(flattened))
    return (
        _pack_values(tuple(offsets), _U64),
        _pack_values(tuple(flattened), _U32),
        len(flattened),
    )


def _encode_byte_csr(rows: tuple[bytes, ...]) -> tuple[bytes, bytes]:
    offsets = [0]
    payload = bytearray()
    for row in rows:
        payload.extend(row)
        offsets.append(len(payload))
    return _pack_values(tuple(offsets), _U64), bytes(payload)


def _encode_occurrences(
    values: tuple[ExpressionOccurrence, ...] | tuple[PropertyOccurrence, ...],
) -> bytes:
    return b"".join(_OCCURRENCE.pack(value.negative, value.positive) for value in values)


def _encode_container(magic: bytes, sections: tuple[_EncodedSection, ...]) -> bytes:
    ordered = tuple(sorted(sections, key=lambda section: section.tag))
    tags = tuple(section.tag for section in ordered)
    if len(tags) != len(set(tags)):
        raise ValueError("section tags must be unique")
    if len(ordered) > MAX_SECTIONS:
        raise ValueError("too many protocol sections")

    header_size = _HEADER.size + len(ordered) * _DIRECTORY_ENTRY.size
    offset = header_size
    directory = bytearray()
    payload = bytearray()
    for section in ordered:
        directory.extend(
            _DIRECTORY_ENTRY.pack(section.tag, offset, len(section.payload), section.count)
        )
        payload.extend(section.payload)
        offset += len(section.payload)

    checksum = blake2b(payload, digest_size=CHECKSUM_SIZE).digest()
    return b"".join(
        (
            _HEADER.pack(magic, SCHEMA_MAJOR, SCHEMA_MINOR, len(ordered)),
            bytes(directory),
            bytes(payload),
            checksum,
        )
    )


def _decode_container(
    data: ReadableBuffer,
    *,
    magic: bytes,
    required_tags: frozenset[int],
    known_tags: frozenset[int] | None = None,
) -> dict[int, _DecodedSection]:
    try:
        raw = bytes(data)
    except (TypeError, ValueError) as error:
        raise _protocol_error("a bytes-like protocol payload", type(data).__name__) from error

    minimum = _HEADER.size + CHECKSUM_SIZE
    if len(raw) < minimum:
        raise _protocol_error(f"at least {minimum} payload bytes", len(raw))

    actual_magic, major, _minor, section_count = _HEADER.unpack_from(raw)
    if actual_magic != magic:
        raise _protocol_error(f"magic {magic!r}", actual_magic)
    if major != SCHEMA_MAJOR:
        raise _protocol_error(f"schema major {SCHEMA_MAJOR}", major)
    if section_count > MAX_SECTIONS:
        raise _protocol_error(f"at most {MAX_SECTIONS} sections", section_count)

    directory_end = _HEADER.size + section_count * _DIRECTORY_ENTRY.size
    checksum_start = len(raw) - CHECKSUM_SIZE
    if directory_end > checksum_start:
        raise _protocol_error("a complete section directory", len(raw))
    actual_checksum = raw[checksum_start:]
    expected_checksum = blake2b(
        raw[directory_end:checksum_start], digest_size=CHECKSUM_SIZE
    ).digest()
    if actual_checksum != expected_checksum:
        raise _protocol_error("valid BLAKE2b-256 section checksum", actual_checksum.hex())

    recognized_tags = required_tags if known_tags is None else known_tags
    if not required_tags <= recognized_tags:
        raise ValueError("required protocol tags must also be known")
    sections: dict[int, _DecodedSection] = {}
    previous_tag = -1
    expected_offset = directory_end
    for index in range(section_count):
        entry_offset = _HEADER.size + index * _DIRECTORY_ENTRY.size
        tag, offset, length, count = _DIRECTORY_ENTRY.unpack_from(raw, entry_offset)
        if tag <= previous_tag:
            raise _protocol_error("strictly increasing unique section tags", tag)
        previous_tag = tag
        if offset != expected_offset:
            raise _protocol_error(f"contiguous section offset {expected_offset}", offset)
        if length > checksum_start - offset:
            raise _protocol_error("section length within payload", length)
        end = offset + length
        if end > checksum_start:
            raise _protocol_error("section ending before checksum", end)
        if tag not in recognized_tags and tag < OPTIONAL_TAG_START:
            raise _protocol_error("a known required or future optional section tag", tag)
        if tag in recognized_tags:
            sections[tag] = _DecodedSection(count=count, payload=raw[offset:end])
        expected_offset = end

    if expected_offset != checksum_start:
        raise _protocol_error("no gap or trailing section data", checksum_start - expected_offset)
    missing = required_tags - sections.keys()
    if missing:
        raise _protocol_error("all required sections", sorted(missing))
    return sections


def _unpack_fixed(section: _DecodedSection, item: Struct, name: str) -> tuple[int, ...]:
    expected_length = section.count * item.size
    if expected_length != len(section.payload):
        raise _protocol_error(f"{name} length {expected_length}", len(section.payload))
    return tuple(
        item.unpack_from(section.payload, index * item.size)[0] for index in range(section.count)
    )


def _unpack_pairs(section: _DecodedSection, name: str) -> tuple[tuple[int, int], ...]:
    expected_length = section.count * _PAIR_U32.size
    if expected_length != len(section.payload):
        raise _protocol_error(f"{name} length {expected_length}", len(section.payload))
    return tuple(
        _PAIR_U32.unpack_from(section.payload, index * _PAIR_U32.size)
        for index in range(section.count)
    )


def _unpack_offsets(section: _DecodedSection, name: str) -> tuple[int, ...]:
    expected_length = (section.count + 1) * _U64.size
    if expected_length != len(section.payload):
        raise _protocol_error(f"{name} offset length {expected_length}", len(section.payload))
    offsets = tuple(
        _U64.unpack_from(section.payload, index * _U64.size)[0]
        for index in range(section.count + 1)
    )
    if not offsets or offsets[0] != 0:
        raise _protocol_error(f"{name} offsets beginning at zero", offsets[:1])
    for index in range(1, len(offsets)):
        if offsets[index - 1] > offsets[index]:
            raise _protocol_error(
                f"monotone {name} offsets",
                (index - 1, offsets[index - 1], index, offsets[index]),
            )
    return offsets


def _decode_csr(
    offsets_section: _DecodedSection,
    values_section: _DecodedSection,
    name: str,
) -> tuple[tuple[int, ...], ...]:
    offsets = _unpack_offsets(offsets_section, name)
    values = _unpack_fixed(values_section, _U32, f"{name} values")
    if offsets[-1] != len(values):
        raise _protocol_error(f"{name} final offset {len(values)}", offsets[-1])
    return tuple(
        tuple(values[offsets[index] : offsets[index + 1]]) for index in range(len(offsets) - 1)
    )


def _decode_byte_csr(
    offsets_section: _DecodedSection,
    payload_section: _DecodedSection,
    name: str,
) -> tuple[bytes, ...]:
    offsets = _unpack_offsets(offsets_section, name)
    if payload_section.count != len(payload_section.payload):
        raise _protocol_error(
            f"{name} byte count {len(payload_section.payload)}", payload_section.count
        )
    if offsets[-1] != len(payload_section.payload):
        raise _protocol_error(f"{name} final offset {len(payload_section.payload)}", offsets[-1])
    return tuple(
        payload_section.payload[offsets[index] : offsets[index + 1]]
        for index in range(len(offsets) - 1)
    )


OccurrenceT = TypeVar("OccurrenceT", ExpressionOccurrence, PropertyOccurrence)


def _decode_occurrences(
    section: _DecodedSection, record_type: type[OccurrenceT], name: str
) -> tuple[OccurrenceT, ...]:
    expected_length = section.count * _OCCURRENCE.size
    if expected_length != len(section.payload):
        raise _protocol_error(f"{name} length {expected_length}", len(section.payload))
    return tuple(
        record_type(*_OCCURRENCE.unpack_from(section.payload, index * _OCCURRENCE.size))
        for index in range(section.count)
    )


def _decode_entity_records(
    kinds_section: _DecodedSection,
    offsets_section: _DecodedSection,
    bytes_section: _DecodedSection,
) -> tuple[EntityRecord, ...]:
    kinds = _unpack_fixed(kinds_section, _U8, "entity kinds")
    iri_bytes = _decode_byte_csr(offsets_section, bytes_section, "entity IRIs")
    if len(kinds) != len(iri_bytes):
        raise _protocol_error("equal entity kind and IRI counts", (len(kinds), len(iri_bytes)))
    records: list[EntityRecord] = []
    for index, (kind_value, encoded_iri) in enumerate(zip(kinds, iri_bytes, strict=True)):
        try:
            kind = EntityKind(kind_value)
        except ValueError as error:
            raise _protocol_error("a valid EntityKind", kind_value) from error
        try:
            iri = encoded_iri.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _protocol_error(
                f"valid UTF-8 for entity IRI {index}",
                {"byte_length": len(encoded_iri), "error_offset": error.start},
            ) from error
        try:
            records.append(EntityRecord(kind=kind, iri=iri))
        except ValueError as error:
            raise _protocol_error("a valid entity record", str(error)) from error
    return tuple(records)


def _decode_expression_records(
    tags_section: _DecodedSection,
    argument_offsets: _DecodedSection,
    arguments: _DecodedSection,
    payload_offsets: _DecodedSection,
    payload_bytes: _DecodedSection,
) -> tuple[ExpressionRecord, ...]:
    tags = _unpack_fixed(tags_section, _U8, "expression tags")
    argument_rows = _decode_csr(argument_offsets, arguments, "expression arguments")
    payload_rows = _decode_byte_csr(payload_offsets, payload_bytes, "expression payloads")
    if not (len(tags) == len(argument_rows) == len(payload_rows)):
        raise _protocol_error(
            "equal expression tag, argument, and payload counts",
            (len(tags), len(argument_rows), len(payload_rows)),
        )
    records: list[ExpressionRecord] = []
    for tag_value, row, payload in zip(tags, argument_rows, payload_rows, strict=True):
        try:
            tag = ExpressionTag(tag_value)
        except ValueError as error:
            raise _protocol_error("a valid ExpressionTag", tag_value) from error
        try:
            records.append(ExpressionRecord(tag=tag, arguments=row, payload=payload))
        except ValueError as error:
            raise _protocol_error("a valid expression record", str(error)) from error
    return tuple(records)


def encode_ontology(ontology: CompiledOntology) -> bytes:
    """Encode a validated ``CompiledOntology`` deterministically."""

    if not isinstance(ontology, CompiledOntology):
        raise TypeError("encode_ontology expects CompiledOntology")
    iri_offsets, iri_bytes = _encode_byte_csr(
        tuple(record.iri.encode("utf-8") for record in ontology.entities)
    )
    argument_offsets, arguments, argument_count = _encode_csr(
        tuple(record.arguments for record in ontology.expressions)
    )
    payload_offsets, payload_bytes = _encode_byte_csr(
        tuple(record.payload for record in ontology.expressions)
    )
    chain_offsets, chain_values, chain_value_count = _encode_csr(
        tuple(tuple(int(value) for value in row) for row in ontology.property_chains)
    )
    disjoint_offsets, disjoint_values, disjoint_value_count = _encode_csr(
        tuple(tuple(int(value) for value in row) for row in ontology.disjoint_groups)
    )
    sections = (
        _EncodedSection(
            OntologySection.ENTITY_KINDS,
            len(ontology.entities),
            _pack_values(tuple(int(record.kind) for record in ontology.entities), _U8),
        ),
        _EncodedSection(OntologySection.ENTITY_IRI_OFFSETS, len(ontology.entities), iri_offsets),
        _EncodedSection(OntologySection.ENTITY_IRI_BYTES, len(iri_bytes), iri_bytes),
        _EncodedSection(
            OntologySection.EXPRESSION_TAGS,
            len(ontology.expressions),
            _pack_values(tuple(int(record.tag) for record in ontology.expressions), _U8),
        ),
        _EncodedSection(
            OntologySection.EXPRESSION_ARGUMENT_OFFSETS,
            len(ontology.expressions),
            argument_offsets,
        ),
        _EncodedSection(OntologySection.EXPRESSION_ARGUMENTS, argument_count, arguments),
        _EncodedSection(
            OntologySection.EXPRESSION_PAYLOAD_OFFSETS,
            len(ontology.expressions),
            payload_offsets,
        ),
        _EncodedSection(
            OntologySection.EXPRESSION_PAYLOAD_BYTES, len(payload_bytes), payload_bytes
        ),
        _EncodedSection(
            OntologySection.EXPRESSION_OCCURRENCES,
            len(ontology.expression_occurrences),
            _encode_occurrences(ontology.expression_occurrences),
        ),
        _EncodedSection(
            OntologySection.PROPERTY_OCCURRENCES,
            len(ontology.property_occurrences),
            _encode_occurrences(ontology.property_occurrences),
        ),
        _EncodedSection(
            OntologySection.PROPERTY_CHAIN_OFFSETS,
            len(ontology.property_chains),
            chain_offsets,
        ),
        _EncodedSection(OntologySection.PROPERTY_CHAIN_VALUES, chain_value_count, chain_values),
        _EncodedSection(
            OntologySection.SUBCLASS_AXIOMS,
            len(ontology.subclass_axioms),
            _pack_pairs(
                tuple((int(first), int(second)) for first, second in ontology.subclass_axioms)
            ),
        ),
        _EncodedSection(
            OntologySection.EQUIVALENT_CLASS_AXIOMS,
            len(ontology.equivalent_class_axioms),
            _pack_pairs(
                tuple(
                    (int(first), int(second)) for first, second in ontology.equivalent_class_axioms
                )
            ),
        ),
        _EncodedSection(
            OntologySection.DISJOINT_GROUP_OFFSETS,
            len(ontology.disjoint_groups),
            disjoint_offsets,
        ),
        _EncodedSection(
            OntologySection.DISJOINT_GROUP_VALUES, disjoint_value_count, disjoint_values
        ),
        _EncodedSection(
            OntologySection.SUBPROPERTY_AXIOMS,
            len(ontology.subproperty_axioms),
            _pack_pairs(
                tuple((int(first), int(second)) for first, second in ontology.subproperty_axioms)
            ),
        ),
        _EncodedSection(
            OntologySection.PROPERTY_RANGES,
            len(ontology.property_ranges),
            _pack_pairs(
                tuple((int(first), int(second)) for first, second in ontology.property_ranges)
            ),
        ),
        _EncodedSection(
            OntologySection.FEATURE_COUNTS,
            len(ontology.feature_counts),
            _pack_values(ontology.feature_counts, _U64),
        ),
        _EncodedSection(OntologySection.SOURCE_FINGERPRINT, 1, ontology.source_fingerprint),
    )
    return _encode_container(IR_MAGIC, sections)


def decode_ontology(data: ReadableBuffer) -> CompiledOntology:
    """Decode a v1 ontology payload and reject every structural violation."""

    sections = _decode_container(
        data,
        magic=IR_MAGIC,
        required_tags=frozenset(int(tag) for tag in OntologySection),
    )
    entities = _decode_entity_records(
        sections[OntologySection.ENTITY_KINDS],
        sections[OntologySection.ENTITY_IRI_OFFSETS],
        sections[OntologySection.ENTITY_IRI_BYTES],
    )
    expressions = _decode_expression_records(
        sections[OntologySection.EXPRESSION_TAGS],
        sections[OntologySection.EXPRESSION_ARGUMENT_OFFSETS],
        sections[OntologySection.EXPRESSION_ARGUMENTS],
        sections[OntologySection.EXPRESSION_PAYLOAD_OFFSETS],
        sections[OntologySection.EXPRESSION_PAYLOAD_BYTES],
    )
    fingerprint_section = sections[OntologySection.SOURCE_FINGERPRINT]
    if fingerprint_section.count != 1:
        raise _protocol_error("one source fingerprint", fingerprint_section.count)
    try:
        return CompiledOntology(
            entities=entities,
            expressions=expressions,
            expression_occurrences=_decode_occurrences(
                sections[OntologySection.EXPRESSION_OCCURRENCES],
                ExpressionOccurrence,
                "expression occurrences",
            ),
            property_occurrences=_decode_occurrences(
                sections[OntologySection.PROPERTY_OCCURRENCES],
                PropertyOccurrence,
                "property occurrences",
            ),
            property_chains=tuple(
                tuple(EntityId(value) for value in row)
                for row in _decode_csr(
                    sections[OntologySection.PROPERTY_CHAIN_OFFSETS],
                    sections[OntologySection.PROPERTY_CHAIN_VALUES],
                    "property chains",
                )
            ),
            subclass_axioms=tuple(
                (ExpressionId(first), ExpressionId(second))
                for first, second in _unpack_pairs(
                    sections[OntologySection.SUBCLASS_AXIOMS], "subclass axioms"
                )
            ),
            equivalent_class_axioms=tuple(
                (ExpressionId(first), ExpressionId(second))
                for first, second in _unpack_pairs(
                    sections[OntologySection.EQUIVALENT_CLASS_AXIOMS],
                    "equivalent-class axioms",
                )
            ),
            disjoint_groups=tuple(
                tuple(ExpressionId(value) for value in row)
                for row in _decode_csr(
                    sections[OntologySection.DISJOINT_GROUP_OFFSETS],
                    sections[OntologySection.DISJOINT_GROUP_VALUES],
                    "disjoint groups",
                )
            ),
            subproperty_axioms=tuple(
                (PropertyChainId(first), EntityId(second))
                for first, second in _unpack_pairs(
                    sections[OntologySection.SUBPROPERTY_AXIOMS], "subproperty axioms"
                )
            ),
            property_ranges=tuple(
                (EntityId(first), ExpressionId(second))
                for first, second in _unpack_pairs(
                    sections[OntologySection.PROPERTY_RANGES], "property ranges"
                )
            ),
            feature_counts=_unpack_fixed(
                sections[OntologySection.FEATURE_COUNTS], _U64, "feature counts"
            ),
            source_fingerprint=fingerprint_section.payload,
        )
    except ValueError as error:
        raise _protocol_error("a valid CompiledOntology", str(error)) from error


def encode_query_ir(query: QueryIR) -> bytes:
    """Encode a validated query mini-IR deterministically."""

    if not isinstance(query, QueryIR):
        raise TypeError("encode_query_ir expects QueryIR")
    entity_records = tuple(record.entity for record in query.entities)
    iri_offsets, iri_bytes = _encode_byte_csr(
        tuple(record.iri.encode("utf-8") for record in entity_records)
    )
    argument_offsets, arguments, argument_count = _encode_csr(
        tuple(record.arguments for record in query.expressions)
    )
    payload_offsets, payload_bytes = _encode_byte_csr(
        tuple(record.payload for record in query.expressions)
    )
    ontology_ids = tuple(
        U32_RESERVED if record.ontology_id is None else int(record.ontology_id)
        for record in query.entities
    )
    root = U32_RESERVED if query.root_expression is None else int(query.root_expression)
    sections = (
        _EncodedSection(QuerySection.KIND, 1, _U8.pack(int(query.kind))),
        _EncodedSection(
            QuerySection.ENTITY_KINDS,
            len(entity_records),
            _pack_values(tuple(int(record.kind) for record in entity_records), _U8),
        ),
        _EncodedSection(QuerySection.ENTITY_IRI_OFFSETS, len(entity_records), iri_offsets),
        _EncodedSection(QuerySection.ENTITY_IRI_BYTES, len(iri_bytes), iri_bytes),
        _EncodedSection(
            QuerySection.ENTITY_ONTOLOGY_IDS,
            len(ontology_ids),
            _pack_values(ontology_ids, _U32),
        ),
        _EncodedSection(
            QuerySection.EXPRESSION_TAGS,
            len(query.expressions),
            _pack_values(tuple(int(record.tag) for record in query.expressions), _U8),
        ),
        _EncodedSection(
            QuerySection.EXPRESSION_ARGUMENT_OFFSETS,
            len(query.expressions),
            argument_offsets,
        ),
        _EncodedSection(QuerySection.EXPRESSION_ARGUMENTS, argument_count, arguments),
        _EncodedSection(
            QuerySection.EXPRESSION_PAYLOAD_OFFSETS,
            len(query.expressions),
            payload_offsets,
        ),
        _EncodedSection(QuerySection.EXPRESSION_PAYLOAD_BYTES, len(payload_bytes), payload_bytes),
        _EncodedSection(
            QuerySection.EXPRESSION_OCCURRENCES,
            len(query.expression_occurrences),
            _encode_occurrences(query.expression_occurrences),
        ),
        _EncodedSection(
            QuerySection.PROPERTY_OCCURRENCES,
            len(query.property_occurrences),
            _encode_occurrences(query.property_occurrences),
        ),
        _EncodedSection(QuerySection.ROOT_EXPRESSION, 1, _U32.pack(root)),
        _EncodedSection(
            QuerySection.SUBSUMPTION_OBLIGATIONS,
            len(query.subsumption_obligations),
            _pack_pairs(
                tuple((int(first), int(second)) for first, second in query.subsumption_obligations)
            ),
        ),
    )
    return _encode_container(QUERY_MAGIC, sections)


def decode_query_ir(data: ReadableBuffer) -> QueryIR:
    """Decode a v1 query mini-IR and reject every structural violation."""

    sections = _decode_container(
        data,
        magic=QUERY_MAGIC,
        required_tags=frozenset(int(tag) for tag in QuerySection),
    )
    kind_values = _unpack_fixed(sections[QuerySection.KIND], _U8, "query kind")
    if len(kind_values) != 1:
        raise _protocol_error("one query kind", len(kind_values))
    try:
        kind = QueryIRKind(kind_values[0])
    except ValueError as error:
        raise _protocol_error("a valid QueryIRKind", kind_values[0]) from error
    entity_records = _decode_entity_records(
        sections[QuerySection.ENTITY_KINDS],
        sections[QuerySection.ENTITY_IRI_OFFSETS],
        sections[QuerySection.ENTITY_IRI_BYTES],
    )
    ontology_ids = _unpack_fixed(
        sections[QuerySection.ENTITY_ONTOLOGY_IDS], _U32, "query ontology entity IDs"
    )
    if len(entity_records) != len(ontology_ids):
        raise _protocol_error(
            "equal query entity and ontology-ID counts", (len(entity_records), len(ontology_ids))
        )
    query_entities = tuple(
        QueryEntityRecord(
            entity=record,
            ontology_id=None if ontology_id == U32_RESERVED else EntityId(ontology_id),
        )
        for record, ontology_id in zip(entity_records, ontology_ids, strict=True)
    )
    expressions = _decode_expression_records(
        sections[QuerySection.EXPRESSION_TAGS],
        sections[QuerySection.EXPRESSION_ARGUMENT_OFFSETS],
        sections[QuerySection.EXPRESSION_ARGUMENTS],
        sections[QuerySection.EXPRESSION_PAYLOAD_OFFSETS],
        sections[QuerySection.EXPRESSION_PAYLOAD_BYTES],
    )
    root_values = _unpack_fixed(
        sections[QuerySection.ROOT_EXPRESSION], _U32, "query root expression"
    )
    if len(root_values) != 1:
        raise _protocol_error("one query root field", len(root_values))
    root_expression = None if root_values[0] == U32_RESERVED else ExpressionId(root_values[0])
    try:
        return QueryIR(
            kind=kind,
            entities=query_entities,
            expressions=expressions,
            expression_occurrences=_decode_occurrences(
                sections[QuerySection.EXPRESSION_OCCURRENCES],
                ExpressionOccurrence,
                "query expression occurrences",
            ),
            property_occurrences=_decode_occurrences(
                sections[QuerySection.PROPERTY_OCCURRENCES],
                PropertyOccurrence,
                "query property occurrences",
            ),
            root_expression=root_expression,
            subsumption_obligations=tuple(
                (ExpressionId(first), ExpressionId(second))
                for first, second in _unpack_pairs(
                    sections[QuerySection.SUBSUMPTION_OBLIGATIONS],
                    "query subsumption obligations",
                )
            ),
        )
    except ValueError as error:
        raise _protocol_error("a valid QueryIR", str(error)) from error


__all__ = [
    "CHECKSUM_SIZE",
    "IR_MAGIC",
    "MAX_SECTIONS",
    "OPTIONAL_TAG_START",
    "QUERY_MAGIC",
    "SCHEMA_MAJOR",
    "SCHEMA_MINOR",
    "OntologySection",
    "QuerySection",
    "decode_ontology",
    "decode_query_ir",
    "encode_ontology",
    "encode_query_ir",
]
