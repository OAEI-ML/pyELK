"""Tests for backend records, wire values, exceptions, and test doubles."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import blake2b
from struct import Struct
from typing import Any

import pytest

from pyelk.exceptions import (
    BackendProtocolError,
    ParseError,
    ReasonerClosedError,
    UnsupportedFeatureError,
)
from pyelk.indexing.codec import CHECKSUM_SIZE
from pyelk.indexing.ir import EntityId, ReadableBuffer
from pyelk.reasoning.contracts import (
    BackendInfo,
    BackendSession,
    CompletenessIssue,
    PolicyFeature,
    QueryKind,
    QueryResultEntityId,
    RawQueryResult,
    RawRealization,
    RawTaxonomy,
    ReasoningTask,
)
from pyelk.reasoning.wire import (
    WireSection,
    decode_raw_query_result,
    decode_raw_realization,
    decode_raw_result,
    decode_raw_taxonomy,
    encode_raw_query_result,
    encode_raw_realization,
    encode_raw_result,
    encode_raw_taxonomy,
)
from tests.helpers import FakeBackendSession, assert_realization_valid, assert_taxonomy_valid

_HEADER = Struct("<8sHHI")
_DIRECTORY_ENTRY = Struct("<HQQQ")


def _taxonomy() -> RawTaxonomy:
    return RawTaxonomy(
        nodes=((EntityId(0),), (EntityId(1),), (EntityId(2),)),
        direct_edges=((0, 1), (1, 2)),
        top=2,
        bottom=0,
    )


def _realization() -> RawRealization:
    return RawRealization(
        class_taxonomy=_taxonomy(),
        instance_nodes=((EntityId(3), EntityId(4)),),
        direct_types=((0, 1),),
    )


def _section_location(data: ReadableBuffer, tag: int) -> tuple[int, int]:
    _, _, _, count = _HEADER.unpack_from(data)
    for index in range(count):
        entry_offset = _HEADER.size + index * _DIRECTORY_ENTRY.size
        actual_tag, offset, length, _ = _DIRECTORY_ENTRY.unpack_from(data, entry_offset)
        if actual_tag == tag:
            return offset, length
    raise AssertionError(tag)


def _refresh_checksum(data: bytearray) -> None:
    _, _, _, count = _HEADER.unpack_from(data)
    payload_start = _HEADER.size + count * _DIRECTORY_ENTRY.size
    data[-CHECKSUM_SIZE:] = blake2b(
        data[payload_start:-CHECKSUM_SIZE], digest_size=CHECKSUM_SIZE
    ).digest()


def test_completeness_issue_and_policy_identity() -> None:
    issue = CompletenessIssue(
        task=ReasoningTask.CLASS_TAXONOMY,
        features=(PolicyFeature.IGNORED_IMPORT.value,),
        constructors=("Import",),
        polarities=("ANY",),
    )

    assert issue.features == ("PYELK_IGNORED_IMPORT",)
    with pytest.raises(ValueError, match="equal lengths"):
        CompletenessIssue(
            task=ReasoningTask.CONSISTENCY,
            features=("A",),
            constructors=(),
            polarities=("ANY",),
        )


def test_backend_info_recursively_freezes_encoded_compiler_handoff() -> None:
    widths = {"root_ids": 4, "scalar_bytes": 1}
    handoff: dict[str, object] = {
        "buffer_widths": widths,
        "descriptor_sha256": "ab" * 32,
        "model_schema": 2,
        "schema_name": "pyowl-core/structural-columns",
        "schema_version": 2,
    }
    encoded = BackendInfo(
        name="rust",
        implementation_version="test-native",
        ir_major=1,
        ir_minor=0,
        requested_workers=0,
        effective_workers=1,
        native_available=True,
        fallback_reason=None,
        _compiler_handoff=handoff,
    )
    scalar = BackendInfo(
        name="python",
        implementation_version="test-python",
        ir_major=1,
        ir_minor=0,
        requested_workers=0,
        effective_workers=1,
        native_available=False,
        fallback_reason=None,
    )
    handoff["schema_name"] = "mutated"
    widths["root_ids"] = 8

    assert getattr(scalar, "compiler_handoff", None) is None
    assert encoded.compiler_handoff == {
        "buffer_widths": {"root_ids": 4, "scalar_bytes": 1},
        "descriptor_sha256": "ab" * 32,
        "model_schema": 2,
        "schema_name": "pyowl-core/structural-columns",
        "schema_version": 2,
    }
    with pytest.raises(TypeError):
        encoded.compiler_handoff["schema_name"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        encoded.compiler_handoff["buffer_widths"]["root_ids"] = 8  # type: ignore[index]
    with pytest.raises(ValueError, match="available Rust backend"):
        BackendInfo(
            name="python",
            implementation_version="test-python",
            ir_major=1,
            ir_minor=0,
            requested_workers=0,
            effective_workers=1,
            native_available=False,
            fallback_reason=None,
            _compiler_handoff=encoded.compiler_handoff,
        )


def test_exception_categories_retain_structured_fields() -> None:
    parse_error = ParseError("fixture.ofn", 2, 7, ")", "unexpected token")
    unsupported = UnsupportedFeatureError("DATA_PROPERTY", object())

    assert (parse_error.line, parse_error.column, parse_error.token) == (2, 7, ")")
    assert unsupported.feature == "DATA_PROPERTY"


def test_raw_taxonomy_and_realization_invariants() -> None:
    taxonomy = _taxonomy()
    realization = _realization()

    assert_taxonomy_valid(taxonomy)
    assert_realization_valid(realization)
    with pytest.raises(ValueError, match="strictly sorted"):
        RawTaxonomy(
            nodes=((EntityId(1),), (EntityId(0),)),
            direct_edges=(),
            top=0,
            bottom=1,
        )


def test_graph_validator_rejects_redundant_direct_edge() -> None:
    taxonomy = RawTaxonomy(
        nodes=((EntityId(0),), (EntityId(1),), (EntityId(2),)),
        direct_edges=((0, 1), (0, 2), (1, 2)),
        top=2,
        bottom=0,
    )

    with pytest.raises(AssertionError, match="redundant"):
        assert_taxonomy_valid(taxonomy)


def test_raw_taxonomy_rejects_cycles_and_disconnected_nodes() -> None:
    nodes = tuple((EntityId(index),) for index in range(4))
    with pytest.raises(ValueError, match="acyclic"):
        RawTaxonomy(
            nodes=nodes,
            direct_edges=((0, 1), (1, 2), (2, 1), (2, 3)),
            top=3,
            bottom=0,
        )
    with pytest.raises(ValueError, match="reachable from bottom"):
        RawTaxonomy(
            nodes=nodes,
            direct_edges=((0, 1), (1, 3), (2, 3)),
            top=3,
            bottom=0,
        )


@pytest.mark.parametrize(
    ("value", "encoder", "decoder"),
    [
        (_taxonomy(), encode_raw_taxonomy, decode_raw_taxonomy),
        (_realization(), encode_raw_realization, decode_raw_realization),
        (
            RawQueryResult(QueryKind.SATISFIABLE, boolean=False),
            encode_raw_query_result,
            decode_raw_query_result,
        ),
        (
            RawQueryResult(QueryKind.SUBCLASSES, nodes=((QueryResultEntityId(0),),)),
            encode_raw_query_result,
            decode_raw_query_result,
        ),
    ],
)
def test_raw_wire_round_trip(
    value: RawTaxonomy | RawRealization | RawQueryResult,
    encoder: Callable[[Any], bytes],
    decoder: Callable[[ReadableBuffer], RawTaxonomy | RawRealization | RawQueryResult],
) -> None:
    encoded = encoder(value)
    assert decoder(encoded) == value
    assert decode_raw_result(encoded) == value
    assert encode_raw_result(value) == encoded


def test_wire_checksum_corruption_is_rejected() -> None:
    encoded = bytearray(encode_raw_taxonomy(_taxonomy()))
    encoded[-1] ^= 0xFF

    with pytest.raises(BackendProtocolError, match="checksum"):
        decode_raw_taxonomy(encoded)


def test_wire_invalid_query_boolean_is_rejected() -> None:
    encoded = bytearray(
        encode_raw_query_result(RawQueryResult(QueryKind.SATISFIABLE, boolean=True))
    )
    offset, length = _section_location(encoded, WireSection.QUERY_BOOLEAN)
    assert length == 1
    encoded[offset] = 2
    _refresh_checksum(encoded)

    with pytest.raises(BackendProtocolError, match="zero/one"):
        decode_raw_query_result(encoded)


def test_wire_rejects_multiple_query_booleans() -> None:
    encoded = bytearray(
        encode_raw_query_result(RawQueryResult(QueryKind.SATISFIABLE, boolean=True))
    )
    _, _, _, count = _HEADER.unpack_from(encoded)
    for index in range(count):
        entry_offset = _HEADER.size + index * _DIRECTORY_ENTRY.size
        tag, offset, length, item_count = _DIRECTORY_ENTRY.unpack_from(encoded, entry_offset)
        if tag == WireSection.QUERY_BOOLEAN:
            assert length == item_count == 1
            encoded.insert(-CHECKSUM_SIZE, 0)
            _DIRECTORY_ENTRY.pack_into(encoded, entry_offset, tag, offset, 2, 2)
            _refresh_checksum(encoded)
            break
    else:
        raise AssertionError("missing query boolean section")

    with pytest.raises(BackendProtocolError, match="at most one"):
        decode_raw_query_result(encoded)


def test_wire_wrong_result_decoder_is_rejected() -> None:
    with pytest.raises(BackendProtocolError, match="taxonomy wire result"):
        decode_raw_taxonomy(
            encode_raw_query_result(RawQueryResult(QueryKind.SATISFIABLE, boolean=True))
        )


def test_fake_backend_satisfies_protocol_and_closes() -> None:
    taxonomy = _taxonomy()
    fake = FakeBackendSession(class_taxonomy=taxonomy, realization=_realization())

    assert isinstance(fake, BackendSession)
    assert fake.class_taxonomy() == taxonomy
    assert fake.query_class_expression(None, QueryKind.SATISFIABLE, False).boolean is True
    fake.close()
    fake.close()
    with pytest.raises(ReasonerClosedError):
        fake.class_taxonomy()
