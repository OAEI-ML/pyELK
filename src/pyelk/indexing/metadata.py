"""Bounded native facade metadata, separate from pyELK's private compiler IR."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from pyelk.exceptions import BackendProtocolError
from pyelk.indexing.codec import (
    _U64,
    _decode_container,
    _decode_entity_records,
    _encode_byte_csr,
    _encode_container,
    _EncodedSection,
    _pack_values,
    _unpack_fixed,
)
from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    FINGERPRINT_SIZE,
    U64_MAX,
    CompiledOntology,
    EntityRecord,
    ReadableBuffer,
)

COMPILER_METADATA_MAGIC = b"PYELKFAC"


class CompilerMetadataSection(IntEnum):
    """Required sections in the private native facade metadata envelope."""

    ENTITY_KINDS = 1
    ENTITY_IRI_OFFSETS = 2
    ENTITY_IRI_BYTES = 3
    FEATURE_COUNTS = 4
    SOURCE_FINGERPRINT = 5


_KNOWN_TAGS = frozenset(int(tag) for tag in CompilerMetadataSection)


@dataclass(frozen=True, slots=True)
class CompilerMetadata:
    """Entity symbols and fixed feature ledger required by the public facade."""

    entities: tuple[EntityRecord, ...]
    feature_counts: tuple[int, ...]
    source_fingerprint: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.entities, tuple) or any(
            not isinstance(record, EntityRecord) for record in self.entities
        ):
            raise ValueError("compiler metadata entities must be EntityRecord values")
        keys = tuple((int(record.kind), record.iri.encode("utf-8")) for record in self.entities)
        if any(keys[index - 1] >= keys[index] for index in range(1, len(keys))):
            raise ValueError("compiler metadata entities must be strictly canonical")
        if len(self.feature_counts) != FEATURE_VECTOR_LENGTH or any(
            isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= U64_MAX
            for count in self.feature_counts
        ):
            raise ValueError("compiler metadata feature counts must be the frozen u64 vector")
        if (
            not isinstance(self.source_fingerprint, bytes)
            or len(self.source_fingerprint) != FINGERPRINT_SIZE
        ):
            raise ValueError(
                f"compiler metadata source fingerprint must be {FINGERPRINT_SIZE} bytes"
            )


def metadata_from_compiled(compiled: CompiledOntology) -> CompilerMetadata:
    """Project scalar compiler output onto the same bounded facade contract."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be CompiledOntology")
    return CompilerMetadata(
        entities=compiled.entities,
        feature_counts=compiled.feature_counts,
        source_fingerprint=compiled.source_fingerprint,
    )


def encode_compiler_metadata(metadata: CompilerMetadata) -> bytes:
    """Encode metadata for differential tests and backend-neutral adapters."""

    if not isinstance(metadata, CompilerMetadata):
        raise TypeError("metadata must be CompilerMetadata")
    iri_offsets, iri_bytes = _encode_byte_csr(
        tuple(record.iri.encode("utf-8") for record in metadata.entities)
    )
    sections = (
        _EncodedSection(
            CompilerMetadataSection.ENTITY_KINDS,
            len(metadata.entities),
            bytes(int(record.kind) for record in metadata.entities),
        ),
        _EncodedSection(
            CompilerMetadataSection.ENTITY_IRI_OFFSETS,
            len(metadata.entities),
            iri_offsets,
        ),
        _EncodedSection(
            CompilerMetadataSection.ENTITY_IRI_BYTES,
            len(iri_bytes),
            iri_bytes,
        ),
        _EncodedSection(
            CompilerMetadataSection.FEATURE_COUNTS,
            len(metadata.feature_counts),
            _pack_values(metadata.feature_counts, _U64),
        ),
        _EncodedSection(
            CompilerMetadataSection.SOURCE_FINGERPRINT,
            1,
            metadata.source_fingerprint,
        ),
    )
    return _encode_container(COMPILER_METADATA_MAGIC, sections)


def decode_compiler_metadata(data: ReadableBuffer) -> CompilerMetadata:
    """Defensively decode one exact native facade metadata payload."""

    sections = _decode_container(
        data,
        magic=COMPILER_METADATA_MAGIC,
        required_tags=_KNOWN_TAGS,
        known_tags=_KNOWN_TAGS,
    )
    if frozenset(sections) != _KNOWN_TAGS:
        raise BackendProtocolError("exact compiler metadata sections", sorted(sections))
    source = sections[CompilerMetadataSection.SOURCE_FINGERPRINT]
    if source.count != 1 or len(source.payload) != FINGERPRINT_SIZE:
        raise BackendProtocolError(
            f"one {FINGERPRINT_SIZE}-byte compiler source fingerprint",
            {"count": source.count, "bytes": len(source.payload)},
        )
    try:
        return CompilerMetadata(
            entities=_decode_entity_records(
                sections[CompilerMetadataSection.ENTITY_KINDS],
                sections[CompilerMetadataSection.ENTITY_IRI_OFFSETS],
                sections[CompilerMetadataSection.ENTITY_IRI_BYTES],
            ),
            feature_counts=_unpack_fixed(
                sections[CompilerMetadataSection.FEATURE_COUNTS],
                _U64,
                "compiler metadata feature counts",
            ),
            source_fingerprint=source.payload,
        )
    except ValueError as error:
        raise BackendProtocolError("valid compiler facade metadata", str(error)) from error


__all__ = [
    "COMPILER_METADATA_MAGIC",
    "CompilerMetadata",
    "CompilerMetadataSection",
    "decode_compiler_metadata",
    "encode_compiler_metadata",
    "metadata_from_compiled",
]
