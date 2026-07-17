from __future__ import annotations

import pyowl_core as owl
import pytest

from pyelk.indexing.builder import IndexTransaction
from pyelk.indexing.conversion import (
    ExpressionConverter,
    LiteralCompatibilityMode,
    literal_compatibility_key,
)
from pyelk.indexing.ir import ExpressionTag
from pyelk.indexing.polarity import IndexPolarity


def _stored_lexical(payload: bytes) -> str:
    prefix = b"pyelk:elk-literal-key:v1\x00"
    assert payload.startswith(prefix)
    offset = len(prefix)
    length = int.from_bytes(payload[offset : offset + 8], "big")
    offset += 8
    return payload[offset : offset + length].decode()


def test_plain_literal_uses_pinned_elk_trailing_at_compatibility_key() -> None:
    literal = owl.Literal("hello", owl.RDF_PLAIN_LITERAL)
    key = literal_compatibility_key(literal)
    assert _stored_lexical(key.payload) == "hello@"
    assert key.mode is LiteralCompatibilityMode.CANONICAL_FALLBACK
    assert literal.lexical_form == "hello"
    assert literal.language is None


def test_language_source_spelling_is_private_validated_and_cache_observed() -> None:
    literal = owl.Literal("hello", owl.RDF_PLAIN_LITERAL, "en")
    canonical = literal_compatibility_key(literal)
    source = literal_compatibility_key(literal, source_language="EN")
    assert _stored_lexical(canonical.payload) == "hello@en"
    assert _stored_lexical(source.payload) == "hello@EN"
    assert source.mode is LiteralCompatibilityMode.SOURCE_MAP
    assert source.payload != canonical.payload
    assert source.observation != canonical.observation
    with pytest.raises(ValueError, match="not equivalent"):
        literal_compatibility_key(literal, source_language="fr")
    with pytest.raises(ValueError, match="requires a language literal"):
        literal_compatibility_key(owl.Literal("hello", owl.RDF_PLAIN_LITERAL), source_language="en")


def test_typed_literal_retains_lexical_and_datatype_without_plain_literal_suffix() -> None:
    literal = owl.Literal("hello", owl.XSD_STRING)
    key = literal_compatibility_key(literal)
    assert _stored_lexical(key.payload) == "hello"
    assert owl.XSD_STRING.iri.value.encode() in key.payload


def test_data_has_value_uses_only_flat_private_payload_and_observation() -> None:
    transaction = IndexTransaction()
    expression = owl.DataHasValue(
        owl.DataProperty(owl.IRI("urn:data")),
        owl.Literal("value", owl.RDF_PLAIN_LITERAL, "en"),
    )
    handle = ExpressionConverter(transaction).convert(expression, IndexPolarity.DUAL)
    record = transaction.expressions[handle]
    assert record.tag is ExpressionTag.DATA_HAS_VALUE
    assert record.payload
    assert _stored_lexical(record.payload) == "value@en"
    assert len(transaction.compatibility_observations) == 1
    assert transaction.expression_occurrences[handle] == [1, 1]
