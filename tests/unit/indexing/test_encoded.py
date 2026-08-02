from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from typing import Any, TypeVar, cast

import pyowl_core as owl
import pytest
from pyowl_core.backends.native_views import ENCODED_STRUCTURAL_DESCRIPTOR_V2

from pyelk.indexing.encoded import (
    ENCODED_BUFFER_WIDTHS,
    ENCODED_DESCRIPTOR_SHA256,
    ENCODED_MODEL_SCHEMA,
    ENCODED_SCHEMA_NAME,
    ENCODED_SCHEMA_VERSION,
    negotiate_encoded_structural_view,
)

V = TypeVar("V")
_FEATURES = frozenset(
    {
        "document-boundaries",
        "document-scoped-anonymous",
        "import-manifest",
        "owl2-structural",
    }
)


class _EncodedStructuralView:
    def __init__(self, owner: _View) -> None:
        self.schema_name = ENCODED_SCHEMA_NAME
        self.schema_version = ENCODED_SCHEMA_VERSION
        self.model_schema = ENCODED_MODEL_SCHEMA
        self.owner = owner
        self.scope = owl.AxiomScope.CLOSURE
        self.descriptor = ENCODED_STRUCTURAL_DESCRIPTOR_V2
        self.descriptor_digest = sha256(self.descriptor).digest()
        self.buffers = {
            name: memoryview(b"\x00" * (8 if name == "node_field_offsets" else 0))
            for name in ENCODED_BUFFER_WIDTHS
        }
        self.segments: tuple[object, ...] = ()
        self.structural_fingerprint = owl.Fingerprint("sha256", 2, b"e" * 32)


class _View:
    def __init__(self, *, advertise: bool = True) -> None:
        self.capabilities = owl.CoreCapabilities(
            adapter_protocol=1,
            model_schema=2,
            wire_format=(1, 2),
            features=_FEATURES,
            encoded_view_schemas=(
                {ENCODED_SCHEMA_NAME: ENCODED_SCHEMA_VERSION} if advertise else {}
            ),
        )
        self.structural_fingerprint = owl.Fingerprint("sha256", 2, b"s" * 32)
        self.logical_fingerprint = owl.Fingerprint("sha256", 2, b"l" * 32)
        self.signature_fingerprint = owl.Fingerprint("sha256", 2, b"g" * 32)
        self.report = object()
        self.origin_index = owl.OriginIndex()
        self.is_complete = True
        self.encoded = _EncodedStructuralView(self)
        self.requests: list[tuple[type[object], dict[str, object]]] = []

    def iter_axioms(
        self,
        axiom_type: type[owl.AxiomNode] | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[owl.AxiomNode]:
        return iter(())

    def iter_extensions(
        self,
        namespace: str | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[owl.StructuralNode]:
        return iter(())

    def contains(
        self,
        axiom: owl.AxiomNode,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> bool:
        return False

    def ontology_annotations(
        self,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> owl.CanonicalSet[owl.Annotation]:
        return owl.CanonicalSet()

    def signature(
        self,
        kind: owl.EntityKind | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
        include_builtins: bool = True,
    ) -> tuple[owl.Entity, ...]:
        return ()

    def view(self, view_type: type[V], /, **options: object) -> V:
        self.requests.append((cast(type[object], view_type), dict(options)))
        return cast(V, self.encoded)


def _as_view(value: _View) -> owl.OntologyView:
    return cast(owl.OntologyView, value)


def test_capability_absence_is_a_scalar_fallback_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(owl, "EncodedStructuralView", raising=False)
    view = _View(advertise=False)
    result = negotiate_encoded_structural_view(_as_view(view))
    assert result.available is False
    assert result.handoff is None
    assert result.advertised_schema is None
    assert "does not advertise" in (result.reason or "")
    assert view.requests == []


def test_older_advertised_schema_selects_scalar_before_acquisition() -> None:
    view = _View()
    view.capabilities = owl.CoreCapabilities(
        adapter_protocol=1,
        model_schema=2,
        wire_format=(1, 2),
        features=_FEATURES,
        encoded_view_schemas={ENCODED_SCHEMA_NAME: 1},
    )

    result = negotiate_encoded_structural_view(_as_view(view))

    assert result.available is False
    assert result.handoff is None
    assert result.advertised_schema == 1
    assert "requires 2" in (result.reason or "")
    assert view.requests == []


def test_false_advertising_fails_closed_before_scalar_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(owl, "EncodedStructuralView", raising=False)
    view = _View()
    with pytest.raises(owl.AdapterCompatibilityError, match="exports no"):
        negotiate_encoded_structural_view(_as_view(view))
    assert view.requests == []


def test_valid_handoff_retains_exact_owner_and_read_only_buffers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)
    view = _View()
    result = negotiate_encoded_structural_view(_as_view(view))
    assert result.available is True
    handoff = result.handoff
    assert handoff is not None
    assert cast(object, handoff.owner) is view
    assert handoff.encoded_view is view.encoded
    assert handoff.structural_fingerprint is view.encoded.structural_fingerprint
    assert handoff.structural_fingerprint != view.structural_fingerprint
    assert handoff.buffer_count == 11
    assert handoff.buffer_bytes == 8
    assert tuple(handoff.buffers) == tuple(sorted(ENCODED_BUFFER_WIDTHS))
    assert all(buffer.readonly for buffer in handoff.buffers.values())
    assert handoff.descriptor_digest == ENCODED_DESCRIPTOR_SHA256
    assert view.requests == [
        (
            _EncodedStructuralView,
            {
                "schema_version": ENCODED_SCHEMA_VERSION,
                "scope": owl.AxiomScope.CLOSURE,
            },
        )
    ]


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_name", "wrong/schema"),
        ("schema_version", 1),
        ("model_schema", 1),
        ("structural_fingerprint", owl.Fingerprint("sha256", 1, b"e" * 32)),
        ("descriptor", b""),
        ("buffers", {}),
        ("buffers", {"writable": bytearray(b"bad")}),
        ("scope", owl.AxiomScope.ROOT),
    ],
)
def test_malformed_advertised_envelope_never_silently_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    invalid: object,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)
    view = _View()
    setattr(view.encoded, field, invalid)
    with pytest.raises(owl.BackendProtocolError, match=field):
        negotiate_encoded_structural_view(_as_view(view))
    assert len(view.requests) == 1


def test_owner_fingerprint_and_descriptor_digest_are_bound_to_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)

    owner_mismatch = _View()
    owner_mismatch.encoded.owner = _View()
    with pytest.raises(owl.BackendProtocolError, match="owner"):
        negotiate_encoded_structural_view(_as_view(owner_mismatch))

    invalid_fingerprint = _View()
    cast(Any, invalid_fingerprint.encoded).structural_fingerprint = object()
    with pytest.raises(owl.BackendProtocolError, match="structural_fingerprint"):
        negotiate_encoded_structural_view(_as_view(invalid_fingerprint))

    digest_mismatch = _View()
    digest_mismatch.encoded.descriptor_digest = b"x" * 32
    with pytest.raises(owl.BackendProtocolError, match="descriptor_digest"):
        negotiate_encoded_structural_view(_as_view(digest_mismatch))

    descriptor_drift = _View()
    descriptor_drift.encoded.descriptor += b" "
    descriptor_drift.encoded.descriptor_digest = sha256(
        descriptor_drift.encoded.descriptor
    ).digest()
    with pytest.raises(owl.BackendProtocolError, match="frozen"):
        negotiate_encoded_structural_view(_as_view(descriptor_drift))


@pytest.mark.parametrize(
    "buffers",
    [
        {name: memoryview(b"") for name in ENCODED_BUFFER_WIDTHS if name != "root_ids"},
        {
            **{name: memoryview(b"") for name in ENCODED_BUFFER_WIDTHS},
            "private_layout": memoryview(b""),
        },
        {
            **{name: memoryview(b"") for name in ENCODED_BUFFER_WIDTHS},
            "root_ids": memoryview(b"\x00"),
        },
    ],
)
def test_buffer_ledger_and_scalar_widths_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    buffers: dict[str, memoryview],
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)
    view = _View()
    view.encoded.buffers = buffers
    with pytest.raises(owl.BackendProtocolError, match="buffers"):
        negotiate_encoded_structural_view(_as_view(view))


def test_advertised_acquisition_failure_is_a_compatibility_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owl, "EncodedStructuralView", _EncodedStructuralView, raising=False)
    view = _View()

    def fail(view_type: type[V], /, **options: object) -> V:
        raise RuntimeError("provider failed")

    view.view = fail  # type: ignore[method-assign]
    with pytest.raises(owl.AdapterCompatibilityError, match="provider failed"):
        negotiate_encoded_structural_view(_as_view(view))
