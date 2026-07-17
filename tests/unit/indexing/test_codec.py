"""Tests for the frozen ontology and query IR codecs."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from hashlib import blake2b
from pathlib import Path
from struct import Struct

import pytest

import pyelk
from pyelk.exceptions import BackendProtocolError
from pyelk.indexing.codec import (
    CHECKSUM_SIZE,
    IR_MAGIC,
    OPTIONAL_TAG_START,
    OntologySection,
    QuerySection,
    _decode_container,
    _DecodedSection,
    _encode_container,
    _EncodedSection,
    decode_ontology,
    decode_query_ir,
)
from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    CompiledQuery,
    EntityKind,
    EntityRecord,
    ExpressionId,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    QueryEntityRecord,
    QueryIR,
    QueryIRKind,
)
from tests.helpers import TinyCompiledOntologyBuilder

_HEADER = Struct("<8sHHI")
_DIRECTORY_ENTRY = Struct("<HQQQ")
_U64 = Struct("<Q")

# These checksums freeze every byte of the v1 foundation fixtures while keeping this source
# reviewable. A checksum change requires a deliberate protocol-version decision.
EMPTY_GOLDEN_BLAKE2B256 = "2c26514fc612dcb2b3a6096317dd7078ae0b256a0fda558f09bd83b68cfb33c8"
ONE_AXIOM_GOLDEN_BLAKE2B256 = "36d44161b6aaba5fc7ac66d04a15093359d06bc324e7c5b2e48a53134044c8bd"


def _empty_ontology_bytes() -> bytes:
    return TinyCompiledOntologyBuilder().build().encode()


def _one_axiom_bytes() -> bytes:
    return (
        TinyCompiledOntologyBuilder()
        .add_subclass("http://example.org/A", "http://www.w3.org/2002/07/owl#Thing")
        .build()
        .encode()
    )


def _entries(data: bytes | bytearray) -> list[tuple[int, int, int, int, int]]:
    _, _, _, count = _HEADER.unpack_from(data)
    result = []
    for index in range(count):
        entry_offset = _HEADER.size + index * _DIRECTORY_ENTRY.size
        tag, offset, length, item_count = _DIRECTORY_ENTRY.unpack_from(data, entry_offset)
        result.append((entry_offset, tag, offset, length, item_count))
    return result


def _section_location(data: bytes | bytearray, tag: int) -> tuple[int, int, int]:
    for _, actual_tag, offset, length, count in _entries(data):
        if actual_tag == tag:
            return offset, length, count
    raise AssertionError(f"missing section {tag}")


def _refresh_checksum(data: bytearray) -> None:
    _, _, _, count = _HEADER.unpack_from(data)
    payload_start = _HEADER.size + count * _DIRECTORY_ENTRY.size
    data[-CHECKSUM_SIZE:] = blake2b(
        data[payload_start:-CHECKSUM_SIZE], digest_size=CHECKSUM_SIZE
    ).digest()


def _with_extra_section(data: bytes, tag: int) -> bytes:
    sections = _decode_container(
        data,
        magic=IR_MAGIC,
        required_tags=frozenset(int(value) for value in OntologySection),
    )
    encoded = tuple(
        _EncodedSection(section_tag, section.count, section.payload)
        for section_tag, section in sections.items()
    )
    return _encode_container(IR_MAGIC, (*encoded, _EncodedSection(tag, 3, b"new")))


def test_empty_ontology_round_trip_is_canonical() -> None:
    encoded = _empty_ontology_bytes()

    assert decode_ontology(encoded).encode() == encoded
    assert decode_ontology(bytearray(encoded)).encode() == encoded
    assert decode_ontology(memoryview(encoded)).encode() == encoded
    assert blake2b(encoded, digest_size=32).hexdigest() == EMPTY_GOLDEN_BLAKE2B256


def test_representative_ontology_round_trip_is_canonical() -> None:
    encoded = _one_axiom_bytes()

    assert decode_ontology(encoded).encode() == encoded
    assert blake2b(encoded, digest_size=32).hexdigest() == ONE_AXIOM_GOLDEN_BLAKE2B256


def test_same_major_unknown_optional_section_is_skipped() -> None:
    original = _empty_ontology_bytes()
    extended = _with_extra_section(original, OPTIONAL_TAG_START)

    assert decode_ontology(extended) == decode_ontology(original)


def test_unknown_required_section_is_rejected() -> None:
    with pytest.raises(BackendProtocolError, match="known required"):
        decode_ontology(_with_extra_section(_empty_ontology_bytes(), 21))


@pytest.mark.parametrize("case", ["magic", "major", "checksum", "truncated"])
def test_container_corruption_is_rejected(case: str) -> None:
    encoded = bytearray(_empty_ontology_bytes())
    if case == "magic":
        encoded[0] ^= 0xFF
    elif case == "major":
        encoded[8:10] = (2).to_bytes(2, "little")
    elif case == "checksum":
        encoded[-1] ^= 0xFF
    else:
        del encoded[-10:]

    with pytest.raises(BackendProtocolError):
        decode_ontology(encoded)


def test_noncontiguous_section_offset_is_rejected() -> None:
    encoded = bytearray(_empty_ontology_bytes())
    first_entry = _HEADER.size
    offset_field = first_entry + 2
    current = int.from_bytes(encoded[offset_field : offset_field + 8], "little")
    encoded[offset_field : offset_field + 8] = (current + 1).to_bytes(8, "little")

    with pytest.raises(BackendProtocolError, match="contiguous section offset"):
        decode_ontology(encoded)


def test_oversized_count_is_rejected_before_unpacking() -> None:
    encoded = bytearray(_empty_ontology_bytes())
    first_entry = _HEADER.size
    count_field = first_entry + 2 + 8 + 8
    encoded[count_field : count_field + 8] = _U64.pack(2**63)

    with pytest.raises(BackendProtocolError, match="entity kinds length"):
        decode_ontology(encoded)


def test_invalid_entity_enum_is_rejected() -> None:
    encoded = bytearray(_empty_ontology_bytes())
    offset, _, _ = _section_location(encoded, OntologySection.ENTITY_KINDS)
    encoded[offset] = 0xFF
    _refresh_checksum(encoded)

    with pytest.raises(BackendProtocolError, match="EntityKind"):
        decode_ontology(encoded)


def test_invalid_expression_record_is_wrapped_as_protocol_error() -> None:
    encoded = bytearray(_empty_ontology_bytes())
    offset, _, _ = _section_location(encoded, OntologySection.EXPRESSION_ARGUMENTS)
    encoded[offset : offset + 4] = (0xFFFFFFFF).to_bytes(4, "little")
    _refresh_checksum(encoded)

    with pytest.raises(BackendProtocolError, match="valid expression record"):
        decode_ontology(encoded)


def test_invalid_utf8_is_rejected() -> None:
    encoded = bytearray(_empty_ontology_bytes())
    offset, length, _ = _section_location(encoded, OntologySection.ENTITY_IRI_BYTES)
    assert length
    encoded[offset] = 0xFF
    _refresh_checksum(encoded)

    with pytest.raises(BackendProtocolError, match="UTF-8"):
        decode_ontology(encoded)


def test_bad_csr_final_offset_is_rejected() -> None:
    encoded = bytearray(_empty_ontology_bytes())
    offset, _, count = _section_location(encoded, OntologySection.PROPERTY_CHAIN_OFFSETS)
    final_offset = offset + count * _U64.size
    encoded[final_offset : final_offset + _U64.size] = _U64.pack(999)
    _refresh_checksum(encoded)

    with pytest.raises(BackendProtocolError, match="final offset"):
        decode_ontology(encoded)


def test_query_ir_and_compiled_query_round_trip() -> None:
    entity = EntityRecord(EntityKind.CLASS, "http://example.org/A")
    query = QueryIR(
        kind=QueryIRKind.CLASS_EXPRESSION,
        entities=(QueryEntityRecord(entity, None),),
        expressions=(ExpressionRecord(ExpressionTag.CLASS, (0,)),),
        expression_occurrences=(ExpressionOccurrence(negative=1, positive=1),),
        property_occurrences=(),
        root_expression=ExpressionId(0),
        subsumption_obligations=(),
    )

    encoded = query.encode()
    assert decode_query_ir(encoded) == query
    compiled = CompiledQuery(
        encoded=encoded,
        feature_counts=(0,) * FEATURE_VECTOR_LENGTH,
        fresh_entities=(entity,),
    )
    assert compiled.encoded == encoded
    with pytest.raises(ValueError, match="fresh entities"):
        CompiledQuery(
            encoded=encoded,
            feature_counts=(0,) * FEATURE_VECTOR_LENGTH,
            fresh_entities=(),
        )


def test_compiled_ontology_rejects_noncanonical_expression_order() -> None:
    ontology = TinyCompiledOntologyBuilder().build()

    with pytest.raises(ValueError, match="topological key order"):
        replace(
            ontology,
            expressions=tuple(reversed(ontology.expressions)),
            expression_occurrences=tuple(reversed(ontology.expression_occurrences)),
        )


def test_data_has_value_requires_an_opaque_literal_key() -> None:
    data_property = EntityRecord(EntityKind.DATA_PROPERTY, "http://example.org/p")

    with pytest.raises(ValueError, match="literal structural key"):
        QueryIR(
            kind=QueryIRKind.CLASS_EXPRESSION,
            entities=(QueryEntityRecord(data_property, None),),
            expressions=(ExpressionRecord(ExpressionTag.DATA_HAS_VALUE, (0,)),),
            expression_occurrences=(ExpressionOccurrence(negative=0, positive=1),),
            property_occurrences=(),
            root_expression=ExpressionId(0),
            subsumption_obligations=(),
        )


def test_invalid_query_kind_is_rejected() -> None:
    query = QueryIR(
        kind=QueryIRKind.ENTAILMENT,
        entities=(),
        expressions=(),
        expression_occurrences=(),
        property_occurrences=(),
        root_expression=None,
        subsumption_obligations=(),
    )
    encoded = bytearray(query.encode())
    offset, _, _ = _section_location(encoded, QuerySection.KIND)
    encoded[offset] = 0xFF
    _refresh_checksum(encoded)

    with pytest.raises(BackendProtocolError, match="QueryIRKind"):
        decode_query_ir(encoded)


def test_codec_is_hash_seed_independent() -> None:
    repository = Path(__file__).resolve().parents[3]
    script = (
        "from hashlib import blake2b; "
        "from tests.helpers import TinyCompiledOntologyBuilder; "
        "data=TinyCompiledOntologyBuilder().add_subclass('http://example.org/A', "
        "'http://www.w3.org/2002/07/owl#Thing').build().encode(); "
        "print(blake2b(data,digest_size=32).hexdigest())"
    )
    outputs = []
    for seed in ("1", "987654"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        paths = [str(repository)]
        if repository in Path(pyelk.__file__).resolve().parents:
            paths.insert(0, str(repository / "src"))
        inherited = environment.get("PYTHONPATH")
        if inherited:
            paths.append(inherited)
        environment["PYTHONPATH"] = os.pathsep.join(paths)
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script],
                cwd=repository,
                env=environment,
                text=True,
            ).strip()
        )

    assert outputs[0] == outputs[1] == ONE_AXIOM_GOLDEN_BLAKE2B256


def test_decoder_private_section_value_type_is_stable() -> None:
    sections = _decode_container(
        _empty_ontology_bytes(),
        magic=IR_MAGIC,
        required_tags=frozenset(int(value) for value in OntologySection),
    )
    assert all(isinstance(section, _DecodedSection) for section in sections.values())
