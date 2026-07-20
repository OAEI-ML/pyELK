from __future__ import annotations

import pytest

from pyelk.exceptions import BackendProtocolError
from pyelk.indexing.ir import EntityKind, EntityRecord
from pyelk.indexing.metadata import (
    COMPILER_METADATA_MAGIC,
    CompilerMetadata,
    decode_compiler_metadata,
    encode_compiler_metadata,
    metadata_from_compiled,
)
from tests.helpers.contracts import TinyCompiledOntologyBuilder


def _metadata() -> CompilerMetadata:
    builder = TinyCompiledOntologyBuilder()
    builder.add_subclass("urn:metadata:A", "urn:metadata:B")
    builder.add_object_property("urn:metadata:p")
    return metadata_from_compiled(builder.build())


def test_compiler_metadata_round_trips_without_private_ir_sections() -> None:
    metadata = _metadata()
    encoded = encode_compiler_metadata(metadata)

    assert encoded.startswith(COMPILER_METADATA_MAGIC)
    assert b"PYELKIR\0" not in encoded
    assert decode_compiler_metadata(encoded) == metadata


@pytest.mark.parametrize("case", ("magic", "checksum", "truncated"))
def test_compiler_metadata_corruption_fails_closed(case: str) -> None:
    encoded = bytearray(encode_compiler_metadata(_metadata()))
    if case == "magic":
        encoded[0] ^= 0xFF
    elif case == "checksum":
        encoded[-1] ^= 0xFF
    else:
        del encoded[-1]

    with pytest.raises(BackendProtocolError):
        decode_compiler_metadata(encoded)


def test_compiler_metadata_requires_canonical_entities_and_fixed_features() -> None:
    metadata = _metadata()
    with pytest.raises(ValueError, match="strictly canonical"):
        CompilerMetadata(
            entities=tuple(reversed(metadata.entities)),
            feature_counts=metadata.feature_counts,
            source_fingerprint=metadata.source_fingerprint,
        )
    with pytest.raises(ValueError, match="frozen u64 vector"):
        CompilerMetadata(
            entities=(EntityRecord(EntityKind.CLASS, "urn:metadata:A"),),
            feature_counts=metadata.feature_counts[:-1],
            source_fingerprint=metadata.source_fingerprint,
        )
