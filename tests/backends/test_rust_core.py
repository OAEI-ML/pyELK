from __future__ import annotations

import gc
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import weakref
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from types import MappingProxyType, ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest

from pyelk import Reasoner, ReasonerConfig
from pyelk.indexing.codec import OntologySection
from pyelk.indexing.encoded import (
    ENCODED_BUFFER_WIDTHS,
    ENCODED_DESCRIPTOR_SHA256,
    ENCODED_SCHEMA_NAME,
    ENCODED_SCHEMA_VERSION,
)
from tests.integration.test_pure_reasoner import (
    _CLASS_CASES,
    _CLASS_QUERY_CASES,
    _ENTAILMENT_CASES,
    _EXPECTED,
    _PROPERTY_CASES,
    _REALIZATION_CASES,
    _UPSTREAM,
    _parse_axiom,
    _parse_expression,
    _payload,
    _query_snapshot,
    _snapshot,
)
from tests.unit.indexing.test_feature_corpus import (
    _ONTOLOGY_FIXTURES,
    _ontology_feature_view,
)

_FROZEN_ENCODED_ONTOLOGIES = tuple(
    sorted(_UPSTREAM.rglob("*.owl"), key=lambda path: path.relative_to(_UPSTREAM).as_posix())
)
_W3C_CORE_FIXTURES = tuple(
    sorted((Path(__file__).parents[1] / "data/native-structural/w3c-minimal").glob("minimal.*"))
)
_ENCODED_BUFFER_NAMES = (
    "root_kinds",
    "root_ids",
    "node_tags",
    "node_field_offsets",
    "field_kinds",
    "field_values",
    "field_lengths",
    "item_kinds",
    "item_values",
    "item_lengths",
    "scalar_bytes",
)
_SCALAR_ENCODED_COUNTERS: Mapping[str, int | bool] = MappingProxyType(
    {
        "encoded_buffer_bytes": 0,
        "encoded_buffer_count": 0,
        "encoded_compiler_gil_released": False,
        "encoded_detached_buffer_count": 0,
        "encoded_indexed_buffer_count": 0,
        "encoded_posting_bytes": 0,
        "encoded_private_ir_bytes": 0,
        "encoded_referenced_view_count": 0,
        "encoded_segment_count": 0,
        "encoded_staging_copy_bytes": 0,
        "encoded_zero_copy_buffers": 0,
    }
)


def _native_library() -> Path:
    root = Path(__file__).parents[2]
    installed = importlib.util.find_spec("pyelk._native")
    if installed is not None and installed.origin is not None:
        candidate = Path(installed.origin).resolve()
        if root not in candidate.parents and candidate.is_file():
            return candidate
    candidates = (
        root / "target" / "release" / "lib_native.dylib",
        root / "target" / "release" / "lib_native.so",
        root / "target" / "debug" / "lib_native.dylib",
        root / "target" / "debug" / "lib_native.so",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.skip("build the pyelk-pyo3 crate before running native-backend tests")


@pytest.fixture(scope="session", autouse=True)
def native_module(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ModuleType]:
    """Load the workspace extension without installing it into the source tree."""

    source = _native_library()
    root = Path(__file__).parents[2].resolve()
    if root in source.parents:
        destination = tmp_path_factory.mktemp("pyelk-native") / f"_native{EXTENSION_SUFFIXES[0]}"
        shutil.copy2(source, destination)
    else:
        destination = source
    spec = importlib.util.spec_from_file_location("pyelk._native", destination)
    if spec is None or spec.loader is None:
        pytest.fail(f"could not create an import spec for {destination}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        if sys.modules.get("pyelk._native") is module:
            del sys.modules["pyelk._native"]


def _reasoners(snapshot: object, *, workers: int = 1) -> tuple[Reasoner, Reasoner]:
    return (
        Reasoner(snapshot, ReasonerConfig(backend="python", workers=workers)),
        Reasoner(snapshot, ReasonerConfig(backend="rust", workers=workers)),
    )


def _direct_encoded_snapshot(source: bytes) -> tuple[Any, Any]:
    import pyowl_core as owl

    native_views = pytest.importorskip("pyowl_core.backends.native_views")
    produce = getattr(native_views, "produce_encoded_structural_view_v1", None)
    if not callable(produce):
        pytest.skip("installed pyowl-core does not provide structural-columns v1")
    options = owl.LoadOptions(backend=owl.BackendPreference.PYTHON)
    document = owl.parse_document(source, format=owl.DocumentFormat.FUNCTIONAL, options=options)
    snapshot = owl.load_snapshot(
        document,
        options=owl.LoadOptions(
            backend=owl.BackendPreference.PYTHON,
            imports=owl.ImportPolicy.IGNORE,
        ),
    )
    return snapshot, produce(snapshot)


def _encoded_wrapper(encoded: Any, **changes: object) -> SimpleNamespace:
    values = {
        name: getattr(encoded, name)
        for name in (
            "schema_name",
            "schema_version",
            "model_schema",
            "owner",
            "buffers",
            "descriptor",
            "structural_fingerprint",
            "segments",
            "scope",
            "document_key",
        )
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _shared_bytes_exporter_encoded(
    encoded: Any,
    *,
    owner_order: tuple[str, ...] = _ENCODED_BUFFER_NAMES,
) -> SimpleNamespace:
    from pyowl_core.backends import native_views

    assert set(owner_order) == set(_ENCODED_BUFFER_NAMES)
    payloads = {name: bytes(encoded.buffers[name]) for name in _ENCODED_BUFFER_NAMES}
    owner = b"".join(payloads[name] for name in owner_order)
    ranges: dict[str, tuple[int, int]] = {}
    cursor = 0
    for name in owner_order:
        end = cursor + len(payloads[name])
        ranges[name] = (cursor, end)
        cursor = end
    buffers = MappingProxyType(
        {name: memoryview(owner)[slice(*ranges[name])] for name in _ENCODED_BUFFER_NAMES}
    )
    return _encoded_wrapper(
        encoded,
        buffers=buffers,
        structural_fingerprint=native_views._fingerprint(buffers, encoded.segments),
    )


def _noop_overlay_encoded(source: Any, encoded_source: Any) -> tuple[Any, Any]:
    import pyowl_core as owl
    from pyowl_core.backends import native_views

    _empty_snapshot, empty = _direct_encoded_snapshot(b"Ontology(<urn:encoded-empty>)")
    overlay = owl.apply_delta(source, owl.OntologyDelta())
    segment = native_views.EncodedStructuralSegmentV1(
        2,
        encoded_source.owner,
        encoded_source,
        0,
        memoryview(b""),
        memoryview(b""),
        None,
        encoded_source,
    )
    segments = (segment,)
    candidate = SimpleNamespace(
        schema_name=empty.schema_name,
        schema_version=empty.schema_version,
        model_schema=empty.model_schema,
        owner=overlay,
        buffers=empty.buffers,
        descriptor=empty.descriptor,
        structural_fingerprint=native_views._fingerprint(empty.buffers, segments),
        segments=segments,
        scope=empty.scope,
        document_key=empty.document_key,
    )
    return overlay, native_views.validate_encoded_structural_view_v1(
        candidate,
        expected_owner=overlay,
        expected_scope=empty.scope,
        expected_document_key=empty.document_key,
    )


def _excluding_overlay_encoded(
    source: Any, encoded_source: Any, removed_axiom: Any
) -> tuple[Any, Any, int]:
    import pyowl_core as owl
    from pyowl_core.backends import native_views

    axioms = sorted(source.iter_axioms(), key=lambda axiom: axiom.canonical_bytes())
    ordinal = axioms.index(removed_axiom) + 1
    overlay = owl.apply_delta(
        source,
        owl.OntologyDelta(remove_axioms=(removed_axiom,)),
    )
    _empty_snapshot, empty = _direct_encoded_snapshot(b"Ontology(<urn:encoded-empty>)")
    root_ids = memoryview(ordinal.to_bytes(4, "little"))
    segment = native_views.EncodedStructuralSegmentV1(
        2,
        encoded_source.owner,
        encoded_source,
        2,
        root_ids,
        memoryview(b""),
        None,
        encoded_source,
    )
    segments = (segment,)
    candidate = SimpleNamespace(
        schema_name=empty.schema_name,
        schema_version=empty.schema_version,
        model_schema=empty.model_schema,
        owner=overlay,
        buffers=empty.buffers,
        descriptor=empty.descriptor,
        structural_fingerprint=native_views._fingerprint(empty.buffers, segments),
        segments=segments,
        scope=empty.scope,
        document_key=empty.document_key,
    )
    encoded = native_views.validate_encoded_structural_view_v1(
        candidate,
        expected_owner=overlay,
        expected_scope=empty.scope,
        expected_document_key=empty.document_key,
    )
    return overlay, encoded, ordinal


def _delta_overlay_encoded(
    source: Any,
    encoded_source: Any,
    added_axioms: tuple[Any, ...],
    encoded_delta: Any,
    *,
    remove_axioms: tuple[Any, ...] = (),
) -> tuple[Any, Any]:
    import pyowl_core as owl
    from pyowl_core.backends import native_views

    source_axioms = sorted(source.iter_axioms(), key=lambda axiom: axiom.canonical_bytes())
    removed_ordinals = tuple(source_axioms.index(axiom) + 1 for axiom in remove_axioms)
    overlay = owl.apply_delta(
        source,
        owl.OntologyDelta(
            add_axioms=added_axioms,
            remove_axioms=remove_axioms,
            policy=owl.DeltaPolicy.IDEMPOTENT,
        ),
    )
    posting_mode = 2 if removed_ordinals else 0
    base = native_views.EncodedStructuralSegmentV1(
        2,
        encoded_source.owner,
        encoded_source,
        posting_mode,
        memoryview(b"".join(ordinal.to_bytes(4, "little") for ordinal in removed_ordinals)),
        memoryview(b""),
        None,
        encoded_source,
    )
    delta = native_views.EncodedStructuralSegmentV1(
        3,
        overlay,
        None,
        0,
        memoryview(b""),
        memoryview(b""),
        None,
        encoded_delta,
    )
    segments = (base, delta)
    candidate = SimpleNamespace(
        schema_name=encoded_delta.schema_name,
        schema_version=encoded_delta.schema_version,
        model_schema=encoded_delta.model_schema,
        owner=overlay,
        buffers=encoded_delta.buffers,
        descriptor=encoded_delta.descriptor,
        structural_fingerprint=native_views._fingerprint(encoded_delta.buffers, segments),
        segments=segments,
        scope=encoded_delta.scope,
        document_key=encoded_delta.document_key,
    )
    return overlay, native_views.validate_encoded_structural_view_v1(
        candidate,
        expected_owner=overlay,
        expected_scope=encoded_delta.scope,
        expected_document_key=encoded_delta.document_key,
    )


def _composite_encoded(
    owner: Any,
    members: tuple[tuple[Any, int, tuple[int, ...], bytes], ...],
    *,
    bridge: Any | None = None,
    scope_maps: tuple[tuple[tuple[bytes, bytes], ...], ...] | None = None,
) -> Any:
    from pyowl_core.backends import native_views

    if bridge is None:
        _empty_snapshot, local = _direct_encoded_snapshot(b"Ontology(<urn:encoded-empty>)")
    else:
        local = bridge
    selected_scope_maps = scope_maps or tuple(() for _member in members)
    if len(selected_scope_maps) != len(members):
        raise ValueError("scope_maps must align with composite members")
    segments = tuple(
        native_views.EncodedStructuralSegmentV1(
            4,
            source.owner,
            source,
            posting_mode,
            memoryview(b"".join(root_id.to_bytes(4, "little") for root_id in root_ids)),
            memoryview(b"".join(source + target for source, target in scope_map)),
            member_token,
            source,
        )
        for (source, posting_mode, root_ids, member_token), scope_map in zip(
            members, selected_scope_maps, strict=True
        )
    )
    if bridge is not None:
        segments += (
            native_views.EncodedStructuralSegmentV1(
                5,
                owner,
                None,
                0,
                memoryview(b""),
                memoryview(b""),
                None,
                bridge,
            ),
        )
    candidate = SimpleNamespace(
        schema_name=local.schema_name,
        schema_version=local.schema_version,
        model_schema=local.model_schema,
        owner=owner,
        buffers=local.buffers,
        descriptor=local.descriptor,
        structural_fingerprint=native_views._fingerprint(local.buffers, segments),
        segments=segments,
        scope=local.scope,
        document_key=local.document_key,
    )
    return native_views.validate_encoded_structural_view_v1(
        candidate,
        expected_owner=owner,
        expected_scope=local.scope,
        expected_document_key=local.document_key,
    )


def test_native_handshake_and_defensive_decoder(native_module: ModuleType) -> None:
    assert native_module.abi_version() == "abi3-py310"
    assert native_module.implementation_version() == "0.1.0"
    assert native_module.ir_version() == (1, 0)
    assert native_module.self_check() is True
    assert issubclass(native_module.NativeUnsupportedFeatureError, ValueError)
    assert native_module.encoded_view_schemas() == {
        "pyowl-core/structural-columns": 1,
    }
    with pytest.raises(ValueError, match=r"IR|payload|header|magic"):
        native_module.create_session(b"not a compiled ontology", 1)


def test_advertised_direct_encoded_session_matches_scalar_wire(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology
    from pyelk.indexing.metadata import encode_compiler_metadata, metadata_from_compiled
    from pyelk.indexing.summary import compiler_digest, compiler_section_counts

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded#>) Ontology(<urn:encoded>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(Class(:D))
        Declaration(ObjectProperty(:p))
        Declaration(ObjectProperty(:q))
        Declaration(NamedIndividual(:i))
        Declaration(NamedIndividual(:j))
        SubClassOf(:A :B)
        SubClassOf(:B ObjectSomeValuesFrom(:p :C))
        SubClassOf(
          ObjectIntersectionOf(:A ObjectSomeValuesFrom(:p ObjectComplementOf(:B)) :C)
          ObjectUnionOf(ObjectSomeValuesFrom(:q :D) ObjectOneOf(:i :j))
        )
        ClassAssertion(:A :i)
        ObjectPropertyDomain(:p :A)
        ObjectPropertyRange(:p :C)
        )"""
    )
    compiled = compile_ontology(snapshot, unsupported="error")
    direct = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        diagnostics = direct.diagnostics()
        scalar_diagnostics = scalar.diagnostics()
        assert diagnostics["encoded_buffer_count"] == 11
        assert diagnostics["encoded_buffer_bytes"] == sum(
            value.nbytes for value in encoded.buffers.values()
        )
        assert diagnostics["encoded_zero_copy_buffers"] == 11
        assert diagnostics["encoded_detached_buffer_count"] == 11
        assert diagnostics["encoded_compiler_gil_released"] is True
        assert diagnostics["encoded_indexed_buffer_count"] == 0
        assert diagnostics["encoded_staging_copy_bytes"] == 0
        assert diagnostics["encoded_private_ir_bytes"] == 0
        timed_phases = (
            "encoded_validation_seconds",
            "encoded_compiler_seconds",
            "encoded_session_build_seconds",
        )
        assert all(isinstance(diagnostics[name], float) for name in timed_phases)
        assert all(diagnostics[name] >= 0.0 for name in timed_phases)
        assert diagnostics["encoded_native_boundary_seconds"] >= sum(
            diagnostics[name] for name in timed_phases
        )
        expected_digest = compiler_digest(compiled).hex()
        assert diagnostics["compiler_digest"] == expected_digest
        assert scalar_diagnostics["compiler_digest"] == expected_digest
        expected_metadata = encode_compiler_metadata(metadata_from_compiled(compiled))
        assert direct.compiler_metadata() == expected_metadata
        assert scalar.compiler_metadata() == expected_metadata
        for name, count in compiler_section_counts(compiled).items():
            key = f"compiler_{name}_count"
            assert diagnostics[key] == count
            assert scalar_diagnostics[key] == count
        for operation in (
            "is_inconsistent",
            "class_taxonomy",
            "object_property_taxonomy",
            "realization",
        ):
            assert getattr(direct, operation)() == getattr(scalar, operation)()
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        direct.close()
        scalar.close()


def test_public_scalar_fallback_diagnostics_are_backend_independent(
    native_module: ModuleType,
) -> None:
    snapshot, _encoded = _direct_encoded_snapshot(
        b"""Ontology(<urn:public-diagnostics>
        Declaration(Class(<urn:public-diagnostics:A>))
        Declaration(Class(<urn:public-diagnostics:B>))
        SubClassOf(<urn:public-diagnostics:A> <urn:public-diagnostics:B>)
        )"""
    )
    with patch.object(native_module, "encoded_view_schemas", return_value={}):
        python, rust = _reasoners(snapshot)
        try:
            python_diagnostics = python.diagnostics()
            rust_diagnostics = rust.diagnostics()
            assert python_diagnostics["ingestion_path"] == "scalar-python"
            assert rust_diagnostics["ingestion_path"] == "scalar-wire"
            assert rust_diagnostics["native_abi_version"] == "abi3-py310"
            assert {
                python_diagnostics["compiler_digest"],
                rust_diagnostics["compiler_digest"],
            } == {python_diagnostics["compiler_digest"]}
            assert python_diagnostics["compiler_cache_schema_version"] == 1
            assert rust_diagnostics["compiler_cache_schema_version"] == 1
            assert python_diagnostics["ir_schema_version"] == 1
            assert rust_diagnostics["ir_schema_version"] == 1
            assert python_diagnostics["materialized_scalar_rows"] == 3
            assert rust_diagnostics["materialized_scalar_rows"] == 3
            for diagnostics in (python_diagnostics, rust_diagnostics):
                assert diagnostics["consumer_compile_seconds"] >= 0.0
                for name, expected in _SCALAR_ENCODED_COUNTERS.items():
                    assert diagnostics[name] == expected
        finally:
            python.close()
            rust.close()


def test_public_scalar_fallback_does_not_request_unadvertised_core_columns(
    native_module: ModuleType,
) -> None:
    snapshot, _encoded = _direct_encoded_snapshot(
        b"""Ontology(<urn:public-core-fallback>
        Declaration(Class(<urn:public-core-fallback:A>))
        )"""
    )
    capabilities = replace(snapshot.capabilities, encoded_view_schemas={})
    with (
        patch.object(
            type(snapshot),
            "capabilities",
            new_callable=PropertyMock,
            return_value=capabilities,
        ),
        patch.object(
            type(snapshot),
            "view",
            side_effect=AssertionError("unadvertised encoded view was requested"),
        ) as request,
    ):
        reasoner = Reasoner(
            snapshot,
            ReasonerConfig(backend="rust", workers=1, unsupported="error"),
        )
    try:
        diagnostics = reasoner.diagnostics()
        assert diagnostics["ingestion_path"] == "scalar-wire"
        assert diagnostics["materialized_scalar_rows"] == 1
        for name, expected in _SCALAR_ENCODED_COUNTERS.items():
            assert diagnostics[name] == expected
        assert request.call_count == 0
    finally:
        reasoner.close()


def test_hidden_packed_bytes_exporter_detaches_only_in_frozen_column_order(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-packed#>) Ontology(<urn:encoded-packed>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(ObjectProperty(:p))
        SubClassOf(:A ObjectSomeValuesFrom(:p :B))
        )"""
    )
    compiled = compile_ontology(snapshot, unsupported="error")
    scalar = native_module.create_session(compiled.encode(), 1)
    packed = native_module.create_session_from_encoded(
        _shared_bytes_exporter_encoded(encoded),
        1,
        "error",
    )
    reordered = native_module.create_session_from_encoded(
        _shared_bytes_exporter_encoded(encoded, owner_order=_ENCODED_BUFFER_NAMES[::-1]),
        1,
        "error",
    )
    try:
        expected = scalar.debug_snapshot(realize=True)
        assert packed.debug_snapshot(realize=True) == expected
        assert reordered.debug_snapshot(realize=True) == expected

        packed_diagnostics = packed.diagnostics()
        assert packed_diagnostics["encoded_zero_copy_buffers"] == 11
        assert packed_diagnostics["encoded_detached_buffer_count"] == 11
        assert packed_diagnostics["encoded_indexed_buffer_count"] == 0
        assert packed_diagnostics["encoded_compiler_gil_released"] is True

        reordered_diagnostics = reordered.diagnostics()
        assert reordered_diagnostics["encoded_zero_copy_buffers"] == 11
        assert reordered_diagnostics["encoded_detached_buffer_count"] == 0
        assert reordered_diagnostics["encoded_indexed_buffer_count"] == 11
        assert reordered_diagnostics["encoded_compiler_gil_released"] is False
        assert (
            packed_diagnostics["compiler_digest"]
            == reordered_diagnostics["compiler_digest"]
            == scalar.diagnostics()["compiler_digest"]
        )
    finally:
        packed.close()
        reordered.close()
        scalar.close()


def test_public_facade_runs_entirely_from_advertised_encoded_native_session(
    native_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyowl_core as owl

    assert native_module.encoded_view_schemas() == {
        "pyowl-core/structural-columns": 1,
    }
    snapshot, _encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:facade#>) Ontology(<urn:facade>
        Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B)
        Declaration(ObjectProperty(:p)) Declaration(ObjectProperty(:q))
        SubObjectPropertyOf(:p :q)
        Declaration(NamedIndividual(:i)) ClassAssertion(:A :i)
        )"""
    )
    config = ReasonerConfig(backend="rust", workers=2, unsupported="error")
    expected = Reasoner(snapshot, ReasonerConfig(backend="python", unsupported="error"))
    public_entities = {
        (entity.kind, entity.iri.value): entity
        for entity in snapshot.signature(include_builtins=True)
    }
    public_a = public_entities[(owl.EntityKind.CLASS, "urn:facade#A")]
    public_p = public_entities[(owl.EntityKind.OBJECT_PROPERTY, "urn:facade#p")]
    public_i = public_entities[(owl.EntityKind.NAMED_INDIVIDUAL, "urn:facade#i")]

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("public encoded path reached scalar compilation")

    monkeypatch.setattr("pyelk.api._compile_ontology_with_materialization_count", forbidden)
    monkeypatch.setattr("pyelk.api.create_backend_session", forbidden)
    monkeypatch.setattr(type(snapshot), "signature", forbidden)
    actual: Reasoner | None = None
    try:
        actual = Reasoner(snapshot, config)
        assert actual.backend.name == "rust"
        assert actual.backend.compiler_handoff == {
            "buffer_widths": dict(ENCODED_BUFFER_WIDTHS),
            "descriptor_sha256": ENCODED_DESCRIPTOR_SHA256.hex(),
            "model_schema": 1,
            "schema_name": ENCODED_SCHEMA_NAME,
            "schema_version": ENCODED_SCHEMA_VERSION,
        }
        assert actual._session is not None
        assert actual._session.diagnostics()["ingestion_path"] == "encoded-native"
        diagnostics = actual.diagnostics()
        assert diagnostics["ingestion_path"] == "encoded-native"
        assert diagnostics["compiler_cache_schema_version"] == 1
        assert diagnostics["ir_schema_version"] == 1
        assert diagnostics["compiler_digest"] == expected.diagnostics()["compiler_digest"]
        assert isinstance(diagnostics["encoded_view_publication_seconds"], float)
        assert diagnostics["encoded_view_publication_seconds"] >= 0.0
        assert diagnostics["consumer_compile_seconds"] >= 0.0
        assert diagnostics["materialized_scalar_rows"] == 0
        assert diagnostics["encoded_buffer_count"] == 11
        assert diagnostics["encoded_zero_copy_buffers"] == 11
        assert diagnostics["encoded_staging_copy_bytes"] == 0
        assert diagnostics["encoded_private_ir_bytes"] == 0
        for name in (
            "base_flattening_bytes",
            "parser_calls",
            "per_row_ffi_calls",
            "resolver_calls",
            "scalar_axiom_materializations",
            "scalar_term_materializations",
            "structural_copy_bytes",
            "wire_decoder_calls",
            "wire_encoder_calls",
        ):
            assert diagnostics[name] == 0
        assert actual.is_consistent() == expected.is_consistent()
        assert actual.classify() == expected.classify()
        assert actual.classify_object_properties() == expected.classify_object_properties()
        assert actual.realize() == expected.realize()
        class_member = next(
            member
            for node in actual.classify().value.nodes
            for member in node.members
            if member == public_a
        )
        property_member = next(
            member
            for node in actual.classify_object_properties().value.nodes
            for member in node.members
            if member == public_p
        )
        individual_member = next(
            member
            for node in actual.realize().value.instances
            for member in node.members
            if member == public_i
        )
        assert class_member == public_a
        assert property_member == public_p
        assert individual_member == public_i
        assert next(entity for entity in actual.all_classes() if entity == public_a) == public_a
        assert (
            next(entity for entity in actual.all_object_properties() if entity == public_p)
            == public_p
        )
        assert (
            next(entity for entity in actual.all_named_individuals() if entity == public_i)
            == public_i
        )
        class_a = owl.Class(owl.IRI("urn:facade#A"))
        class_b = owl.Class(owl.IRI("urn:facade#B"))
        individual = owl.NamedIndividual(owl.IRI("urn:facade#i"))
        assert actual.superclasses(class_a) == expected.superclasses(class_a)
        assert actual.instances(class_b) == expected.instances(class_b)
        assert actual.types(individual) == expected.types(individual)
        assert actual.is_entailed(owl.SubClassOf(class_a, class_b)) == expected.is_entailed(
            owl.SubClassOf(class_a, class_b)
        )
    finally:
        if actual is not None:
            actual.close()
        expected.close()


def test_public_advertised_dispatch_covers_mmap_and_recursive_segments(
    native_module: ModuleType,
    tmp_path: Path,
) -> None:
    import pyowl_core as owl

    assert native_module.encoded_view_schemas() == {
        "pyowl-core/structural-columns": 1,
    }
    options = owl.LoadOptions(
        backend=owl.BackendPreference.PYTHON,
        imports=owl.ImportPolicy.IGNORE,
    )

    def load(source: bytes) -> Any:
        return owl.load_snapshot(source, options=options)

    left = load(
        b"""Prefix(:=<urn:public-segments#>) Ontology(<urn:public-segments>
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(:A :B)
        ClassAssertion(:A _:shared)
        )"""
    )
    right = load(
        b"""Prefix(:=<urn:public-segments#>) Ontology(<urn:public-segments>
        Declaration(Class(:B))
        Declaration(Class(:C))
        SubClassOf(:B :C)
        ClassAssertion(:B _:shared)
        )"""
    )
    removed = owl.Declaration(owl.Class(owl.IRI("urn:public-segments#A")))
    added = owl.Declaration(owl.Class(owl.IRI("urn:public-segments#D")))
    overlay = owl.apply_delta(
        left,
        owl.OntologyDelta(
            add_axioms=owl.CanonicalSet((added,)),
            remove_axioms=owl.CanonicalSet((removed,)),
        ),
    )
    composite = owl.compose_views(overlay, right, roles=("overlay", "right"))
    nested = owl.apply_delta(
        composite,
        owl.OntologyDelta(
            add_axioms=owl.CanonicalSet(
                (owl.Declaration(owl.Class(owl.IRI("urn:public-segments#E"))),)
            )
        ),
    )
    third = load(
        b"""Prefix(:=<urn:public-segments#>) Ontology(<urn:public-segments-third>
        Declaration(Class(:F))
        SubClassOf(:E :F)
        )"""
    )
    recursive = owl.compose_views(nested, third, roles=("nested", "third"))
    scoped_source = b"""Prefix(:=<urn:public-scope#>) Ontology(<urn:public-scope>
    Declaration(Class(:A))
    ClassAssertion(:A _:shared)
    )"""
    scoped = owl.compose_views(
        load(scoped_source),
        load(scoped_source),
        roles=("scope-left", "scope-right"),
    )

    wire_path = tmp_path / "public-segments.pyocore"
    wire_path.write_bytes(owl.encode_snapshot(left))
    mapped = owl.open_snapshot(wire_path, mmap=True, verify=True)
    candidates = (
        ("direct", left),
        ("mmap", mapped),
        ("overlay", overlay),
        ("composite", composite),
        ("recursive", recursive),
        ("scoped", scoped),
    )
    try:
        for name, candidate in candidates:
            encoded = candidate.view(
                owl.EncodedStructuralView,
                schema_version=1,
                scope=owl.AxiomScope.CLOSURE,
            )
            if name == "scoped":
                pending = [encoded]
                visited: set[int] = set()
                scope_map_bytes = 0
                while pending:
                    current = pending.pop()
                    if id(current) in visited:
                        continue
                    visited.add(id(current))
                    for segment in current.segments:
                        scope_map_bytes += segment.anonymous_scope_map.nbytes
                        if segment.source is not None:
                            pending.append(segment.source)
                assert scope_map_bytes >= 128
            expected = Reasoner(
                candidate,
                ReasonerConfig(backend="python", workers=1, unsupported="ignore"),
            )
            actual = Reasoner(
                candidate,
                ReasonerConfig(backend="rust", workers=1, unsupported="ignore"),
            )
            try:
                expected_diagnostics = expected.diagnostics()
                diagnostics = actual.diagnostics()
                assert diagnostics["ingestion_path"] == "encoded-native", name
                assert diagnostics["compiler_digest"] == expected_diagnostics["compiler_digest"]
                assert diagnostics["materialized_scalar_rows"] == 0
                assert diagnostics["encoded_private_ir_bytes"] == 0
                assert (
                    diagnostics["encoded_zero_copy_buffers"] == diagnostics["encoded_buffer_count"]
                )
                assert diagnostics["encoded_segment_count"] >= len(encoded.segments)
                assert actual.is_consistent() == expected.is_consistent()
                assert actual.classify() == expected.classify()
                assert actual.classify_object_properties() == expected.classify_object_properties()
                assert actual.realize() == expected.realize()
                if name == "mmap":
                    assert diagnostics["encoded_staging_copy_bytes"] == 0
                    assert diagnostics["encoded_detached_buffer_count"] == 0
                    assert diagnostics["encoded_indexed_buffer_count"] == 11
                if name == "recursive":
                    assert diagnostics["encoded_referenced_view_count"] >= 4
                    assert diagnostics["encoded_staging_copy_bytes"] >= 4
            finally:
                actual.close()
                expected.close()
                del encoded
    finally:
        gc.collect()
        mapped.close()


@pytest.mark.parametrize("failure_stage", ("adapter", "native"))
def test_public_advertised_dispatch_fails_closed_before_scalar_compilation(
    native_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    import pyowl_core as owl

    from pyelk.exceptions import BackendProtocolError

    assert native_module.encoded_view_schemas() == {
        "pyowl-core/structural-columns": 1,
    }
    snapshot, encoded = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:public-hostile:A>)))"
    )
    if failure_stage == "adapter":
        hostile = replace(encoded, descriptor=encoded.descriptor + b" ")
        expected_error: type[BaseException] = owl.BackendProtocolError
        match = "descriptor"
    else:
        buffers = dict(encoded.buffers)
        root_kinds = bytearray(buffers["root_kinds"])
        root_kinds[0] ^= 0x7F
        buffers["root_kinds"] = memoryview(bytes(root_kinds))
        hostile = replace(encoded, buffers=MappingProxyType(buffers))
        expected_error = BackendProtocolError
        match = r"valid encoded structural|root|fingerprint"

    scalar_calls = 0

    def forbidden(*args: object, **kwargs: object) -> object:
        nonlocal scalar_calls
        scalar_calls += 1
        raise AssertionError("advertised malformed input reached scalar compilation")

    with (
        patch.object(type(snapshot), "view", return_value=hostile) as request,
        monkeypatch.context() as context,
    ):
        context.setattr("pyelk.api._compile_ontology_with_materialization_count", forbidden)
        context.setattr("pyelk.api.create_backend_session", forbidden)
        with pytest.raises(expected_error, match=match):
            Reasoner(
                snapshot,
                ReasonerConfig(backend="rust", workers=1, unsupported="error"),
            )
    assert request.call_count == 1
    assert scalar_calls == 0


def test_hidden_noop_overlay_chain_reuses_direct_source_without_flattening(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology

    snapshot, direct_encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-overlay#>) Ontology(<urn:encoded-overlay>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(ObjectProperty(:p))
        SubClassOf(:A ObjectSomeValuesFrom(:p :B))
        )"""
    )
    first_overlay, first_encoded = _noop_overlay_encoded(snapshot, direct_encoded)
    second_overlay, second_encoded = _noop_overlay_encoded(first_overlay, first_encoded)
    native = native_module.create_session_from_encoded(second_encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(second_overlay, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_compiler_gil_released"] is True
        assert diagnostics["encoded_segment_count"] == 3
        assert diagnostics["encoded_referenced_view_count"] == 2
        assert diagnostics["encoded_buffer_bytes"] == sum(
            value.nbytes for value in direct_encoded.buffers.values()
        )
        assert diagnostics["encoded_staging_copy_bytes"] == 0
        assert diagnostics["encoded_private_ir_bytes"] == 0
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_overlay_exclusion_compiles_only_selected_direct_roots(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology

    snapshot, direct_encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-overlay-exclude#>)
        Ontology(<urn:encoded-overlay-exclude>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(ObjectProperty(:p))
        SubClassOf(:A :B)
        FunctionalObjectProperty(:p)
        )"""
    )
    removed = next(
        axiom
        for axiom in snapshot.iter_axioms()
        if type(axiom).__name__ == "FunctionalObjectProperty"
    )
    overlay, encoded, _ordinal = _excluding_overlay_encoded(snapshot, direct_encoded, removed)
    native = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(overlay, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 2
        assert diagnostics["encoded_referenced_view_count"] == 1
        assert diagnostics["encoded_posting_bytes"] == 4
        assert diagnostics["encoded_staging_copy_bytes"] == 0
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_overlay_delta_merges_source_and_local_columns_exactly(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology

    source, encoded_source = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-overlay-delta#>)
        Ontology(<urn:encoded-overlay-delta>
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(:A :B)
        )"""
    )
    delta_source, encoded_delta = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-overlay-delta#>)
        Ontology(<urn:encoded-overlay-delta-local>
        Declaration(Class(:C))
        SubClassOf(:C :A)
        )"""
    )
    removed = next(axiom for axiom in source.iter_axioms() if type(axiom).__name__ == "SubClassOf")
    added = tuple(delta_source.iter_axioms())
    overlay, encoded = _delta_overlay_encoded(
        source,
        encoded_source,
        added,
        encoded_delta,
        remove_axioms=(removed,),
    )
    native = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(overlay, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 3
        assert diagnostics["encoded_referenced_view_count"] == 1
        assert diagnostics["encoded_buffer_count"] == 22
        assert diagnostics["encoded_zero_copy_buffers"] == 22
        assert diagnostics["encoded_posting_bytes"] == 4
        assert diagnostics["encoded_buffer_bytes"] == sum(
            value.nbytes for value in encoded_source.buffers.values()
        ) + sum(value.nbytes for value in encoded_delta.buffers.values())
        assert diagnostics["encoded_staging_copy_bytes"] == 0
        assert diagnostics["encoded_private_ir_bytes"] == 0
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()

    from pyowl_core.backends import native_views

    base, delta = encoded.segments
    hostile_delta = SimpleNamespace(
        role=5,
        owner=delta.owner,
        source=delta.source,
        posting_mode=delta.posting_mode,
        root_ids=delta.root_ids,
        anonymous_scope_map=delta.anonymous_scope_map,
        member_token=delta.member_token,
    )
    hostile_segments = (base, hostile_delta)
    with pytest.raises(ValueError, match=r"overlay|delta|role"):
        native_module.create_session_from_encoded(
            _encoded_wrapper(
                encoded,
                segments=hostile_segments,
                structural_fingerprint=native_views._fingerprint(encoded.buffers, hostile_segments),
            ),
            1,
            "error",
        )


def test_hidden_overlay_delta_deduplicates_cross_table_annotation_variants(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology

    source, encoded_source = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-overlay-annotation#>)
        Ontology(<urn:encoded-overlay-annotation>
        Declaration(AnnotationProperty(:ap))
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(Annotation(:ap "one") :A :B)
        )"""
    )
    delta_source, encoded_delta = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-overlay-annotation#>)
        Ontology(<urn:encoded-overlay-annotation-local>
        Declaration(AnnotationProperty(:ap))
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(:A :B)
        SubClassOf(Annotation(:ap "two") :A :B)
        )"""
    )
    added = tuple(delta_source.iter_axioms())
    overlay, encoded = _delta_overlay_encoded(
        source,
        encoded_source,
        added,
        encoded_delta,
    )
    native = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(overlay, unsupported="error").encode(),
        1,
    )
    try:
        assert native.diagnostics()["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_overlay_delta_session_retains_every_segment_owner(
    native_module: ModuleType,
) -> None:
    source, encoded_source = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-overlay-retain:A>)))"
    )
    delta_source, encoded_delta = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-overlay-retain:B>)))"
    )
    overlay, encoded = _delta_overlay_encoded(
        source,
        encoded_source,
        tuple(delta_source.iter_axioms()),
        encoded_delta,
    )
    source_ref = weakref.ref(encoded_source)
    delta_ref = weakref.ref(encoded_delta)
    top_ref = weakref.ref(encoded)
    session = native_module.create_session_from_encoded(encoded, 1, "error")
    del source, encoded_source, delta_source, encoded_delta, overlay, encoded
    gc.collect()
    assert source_ref() is not None
    assert delta_ref() is not None
    assert top_ref() is not None
    session.close()
    gc.collect()
    assert source_ref() is None
    assert delta_ref() is None
    assert top_ref() is None


def test_hidden_composite_members_merge_without_flattening(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    from pyelk.indexing.compiler import compile_ontology

    left, encoded_left = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-composite#>)
        Ontology(<urn:encoded-composite-left>
        Declaration(AnnotationProperty(:ap))
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(Annotation(:ap "left") :A :B)
        )"""
    )
    right, encoded_right = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-composite#>)
        Ontology(<urn:encoded-composite-right>
        Declaration(AnnotationProperty(:ap))
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        SubClassOf(Annotation(:ap "right") :A :B)
        SubClassOf(:B :C)
        )"""
    )
    composite = owl.compose_views(left, right)
    encoded = _composite_encoded(
        composite,
        (
            (encoded_left, 0, (), b"a" * 32),
            (encoded_right, 0, (), b"b" * 32),
        ),
    )
    scalar = native_module.create_session(
        compile_ontology(composite, unsupported="error").encode(),
        1,
    )
    fingerprint_error = AssertionError(
        "segmented native compilation crossed the composite scalar fingerprint facade"
    )
    with (
        patch.object(
            type(composite),
            "logical_fingerprint",
            new_callable=PropertyMock,
            side_effect=fingerprint_error,
        ),
        patch.object(
            type(composite),
            "signature_fingerprint",
            new_callable=PropertyMock,
            side_effect=fingerprint_error,
        ),
    ):
        native = native_module.create_session_from_encoded(encoded, 1, "error")
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_compiler_gil_released"] is True
        assert diagnostics["encoded_segment_count"] == 4
        assert diagnostics["encoded_referenced_view_count"] == 2
        assert diagnostics["encoded_buffer_count"] == 22
        assert diagnostics["encoded_zero_copy_buffers"] == 22
        assert diagnostics["encoded_posting_bytes"] == 0
        assert diagnostics["encoded_buffer_bytes"] == sum(
            value.nbytes for value in encoded_left.buffers.values()
        ) + sum(value.nbytes for value in encoded_right.buffers.values())
        assert diagnostics["encoded_staging_copy_bytes"] == 0
        assert diagnostics["encoded_private_ir_bytes"] == 0
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_composite_selection_and_bridge_compile_source_locally(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    from pyelk.indexing.compiler import compile_ontology

    left, encoded_left = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-composite-bridge#>)
        Ontology(<urn:encoded-composite-bridge-left>
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(:A :B)
        )"""
    )
    right, encoded_right = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-composite-bridge#>)
        Ontology(<urn:encoded-composite-bridge-right>
        Declaration(Class(:C))
        SubClassOf(:B :C)
        )"""
    )
    bridge_source, encoded_bridge = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-composite-bridge#>)
        Ontology(<urn:encoded-composite-bridge-local>
        Declaration(Class(:D))
        SubClassOf(:C :D)
        )"""
    )
    removed = next(axiom for axiom in left.iter_axioms() if type(axiom).__name__ == "SubClassOf")
    left_axioms = sorted(left.iter_axioms(), key=lambda axiom: axiom.canonical_bytes())
    removed_ordinal = left_axioms.index(removed) + 1
    added = tuple(bridge_source.iter_axioms())
    composite = owl.compose_views(
        left,
        right,
        delta=owl.OntologyDelta(
            add_axioms=owl.CanonicalSet(added),
            remove_axioms=owl.CanonicalSet((removed,)),
            policy=owl.DeltaPolicy.IDEMPOTENT,
        ),
    )
    encoded = _composite_encoded(
        composite,
        (
            (encoded_left, 2, (removed_ordinal,), b"a" * 32),
            (encoded_right, 0, (), b"b" * 32),
        ),
        bridge=encoded_bridge,
    )
    native = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(composite, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 5
        assert diagnostics["encoded_referenced_view_count"] == 2
        assert diagnostics["encoded_buffer_count"] == 33
        assert diagnostics["encoded_zero_copy_buffers"] == 33
        assert diagnostics["encoded_posting_bytes"] == 4
        assert diagnostics["encoded_buffer_bytes"] == sum(
            value.nbytes for value in encoded_left.buffers.values()
        ) + sum(value.nbytes for value in encoded_right.buffers.values()) + sum(
            value.nbytes for value in encoded_bridge.buffers.values()
        )
        assert diagnostics["encoded_staging_copy_bytes"] == 4
        assert diagnostics["encoded_private_ir_bytes"] == 0
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()

    from pyowl_core.backends import native_views

    first, second, bridge = encoded.segments
    hostile = SimpleNamespace(
        role=first.role,
        owner=first.owner,
        source=first.source,
        posting_mode=first.posting_mode,
        root_ids=memoryview((999).to_bytes(4, "little")),
        anonymous_scope_map=first.anonymous_scope_map,
        member_token=first.member_token,
    )
    hostile_segments = (hostile, second, bridge)
    with pytest.raises(ValueError, match=r"sorted|unique|range|posting"):
        native_module.create_session_from_encoded(
            _encoded_wrapper(
                encoded,
                segments=hostile_segments,
                structural_fingerprint=native_views._fingerprint(encoded.buffers, hostile_segments),
            ),
            1,
            "error",
        )


def test_hidden_composite_session_retains_every_member_owner(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    left, encoded_left = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-composite-retain:A>)))"
    )
    right, encoded_right = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-composite-retain:B>)))"
    )
    composite = owl.compose_views(left, right)
    encoded = _composite_encoded(
        composite,
        (
            (encoded_left, 0, (), b"a" * 32),
            (encoded_right, 0, (), b"b" * 32),
        ),
    )
    left_ref = weakref.ref(encoded_left)
    right_ref = weakref.ref(encoded_right)
    top_ref = weakref.ref(encoded)
    session = native_module.create_session_from_encoded(encoded, 1, "error")
    del left, encoded_left, right, encoded_right, composite, encoded
    gc.collect()
    assert left_ref() is not None
    assert right_ref() is not None
    assert top_ref() is not None
    session.close()
    gc.collect()
    assert left_ref() is None
    assert right_ref() is None
    assert top_ref() is None


def test_hidden_composite_scope_maps_preserve_anonymous_member_identity(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    from pyelk.indexing.compiler import compile_ontology

    source = b"""Ontology(<urn:encoded-anonymous-composite>
    ClassAssertion(<urn:encoded-anonymous-composite:A> _:x)
    )"""
    left, encoded_left = _direct_encoded_snapshot(source)
    right, encoded_right = _direct_encoded_snapshot(source)
    composite = owl.compose_views(left, right)
    tokens = composite._source_tokens()  # type: ignore[attr-defined]
    mappings = composite._scope_replacements()  # type: ignore[attr-defined]
    rows = sorted(
        zip(tokens, (encoded_left, encoded_right), mappings, strict=True),
        key=lambda row: row[0],
    )
    encoded = _composite_encoded(
        composite,
        tuple((source_view, 0, (), token) for token, source_view, _mapping in rows),
        scope_maps=tuple(tuple(sorted(mapping.items())) for _token, _source_view, mapping in rows),
    )
    native = native_module.create_session_from_encoded(encoded, 1, "ignore")
    scalar = native_module.create_session(
        compile_ontology(composite, unsupported="ignore").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 4
        assert diagnostics["encoded_referenced_view_count"] == 2
        assert diagnostics["encoded_buffer_count"] == 22
        assert diagnostics["encoded_zero_copy_buffers"] == 22
        assert diagnostics["encoded_staging_copy_bytes"] == 128
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()

    from pyowl_core.backends import native_views

    first, second = encoded.segments
    source_scope = next(iter(rows[0][2]))
    hostile = SimpleNamespace(
        role=first.role,
        owner=first.owner,
        source=first.source,
        posting_mode=first.posting_mode,
        root_ids=first.root_ids,
        anonymous_scope_map=memoryview(source_scope + source_scope),
        member_token=first.member_token,
    )
    hostile_segments = (hostile, second)
    with pytest.raises(ValueError, match=r"scope.map|nonidentity|sorted|unique"):
        native_module.create_session_from_encoded(
            _encoded_wrapper(
                encoded,
                segments=hostile_segments,
                structural_fingerprint=native_views._fingerprint(encoded.buffers, hostile_segments),
            ),
            1,
            "ignore",
        )


def test_hidden_nested_composite_scope_maps_compose_through_segmented_sources(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    from pyelk.indexing.compiler import compile_ontology

    anonymous_source = b"""Ontology(<urn:encoded-nested-anonymous>
    ClassAssertion(<urn:encoded-nested-anonymous:A> _:x)
    )"""
    left, encoded_left = _direct_encoded_snapshot(anonymous_source)
    right, encoded_right = _direct_encoded_snapshot(anonymous_source)
    bridge_source, encoded_bridge = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-nested-anonymous:Bridge>)))"
    )
    inner = owl.compose_views(
        left,
        right,
        delta=owl.OntologyDelta(add_axioms=owl.CanonicalSet(bridge_source.iter_axioms())),
    )
    inner_rows = sorted(
        zip(
            inner._source_tokens(),  # type: ignore[attr-defined]
            (encoded_left, encoded_right),
            inner._scope_replacements(),  # type: ignore[attr-defined]
            strict=True,
        ),
        key=lambda row: row[0],
    )
    encoded_inner = _composite_encoded(
        inner,
        tuple((source_view, 0, (), token) for token, source_view, _mapping in inner_rows),
        bridge=encoded_bridge,
        scope_maps=tuple(
            tuple(sorted(mapping.items())) for _token, _source_view, mapping in inner_rows
        ),
    )

    third, encoded_third = _direct_encoded_snapshot(anonymous_source)
    outer = owl.compose_views(inner, third)
    outer_rows = sorted(
        zip(
            outer._source_tokens(),  # type: ignore[attr-defined]
            (encoded_inner, encoded_third),
            outer._scope_replacements(),  # type: ignore[attr-defined]
            strict=True,
        ),
        key=lambda row: row[0],
    )
    encoded_outer = _composite_encoded(
        outer,
        tuple((source_view, 0, (), token) for token, source_view, _mapping in outer_rows),
        scope_maps=tuple(
            tuple(sorted(mapping.items())) for _token, _source_view, mapping in outer_rows
        ),
    )

    native = native_module.create_session_from_encoded(encoded_outer, 1, "ignore")
    scalar = native_module.create_session(
        compile_ontology(outer, unsupported="ignore").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 8
        assert diagnostics["encoded_referenced_view_count"] == 4
        assert diagnostics["encoded_buffer_count"] == 44
        assert diagnostics["encoded_zero_copy_buffers"] == 44
        assert diagnostics["encoded_posting_bytes"] == 0
        assert diagnostics["encoded_staging_copy_bytes"] == 192
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_composite_recursively_resolves_segmented_member_sources(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    from pyelk.indexing.compiler import compile_ontology

    base, encoded_base = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-recursive#>)
        Ontology(<urn:encoded-recursive-base>
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(:A :B)
        )"""
    )
    delta_source, encoded_delta = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-recursive#>)
        Ontology(<urn:encoded-recursive-delta>
        Declaration(Class(:C))
        SubClassOf(:B :C)
        )"""
    )
    overlay, encoded_overlay = _delta_overlay_encoded(
        base,
        encoded_base,
        tuple(delta_source.iter_axioms()),
        encoded_delta,
    )
    right, encoded_right = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-recursive#>)
        Ontology(<urn:encoded-recursive-right>
        Declaration(Class(:D))
        SubClassOf(:C :D)
        )"""
    )
    removed = next(
        axiom for axiom in delta_source.iter_axioms() if type(axiom).__name__ == "SubClassOf"
    )
    local_axioms = sorted(delta_source.iter_axioms(), key=lambda axiom: axiom.canonical_bytes())
    removed_ordinal = local_axioms.index(removed) + 1
    composite = owl.compose_views(
        overlay,
        right,
        delta=owl.OntologyDelta(
            remove_axioms=owl.CanonicalSet((removed,)),
            policy=owl.DeltaPolicy.IDEMPOTENT,
        ),
    )
    encoded = _composite_encoded(
        composite,
        (
            (encoded_overlay, 2, (removed_ordinal,), b"a" * 32),
            (encoded_right, 0, (), b"b" * 32),
        ),
    )
    native = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(composite, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 6
        assert diagnostics["encoded_referenced_view_count"] == 3
        assert diagnostics["encoded_buffer_count"] == 33
        assert diagnostics["encoded_zero_copy_buffers"] == 33
        assert diagnostics["encoded_posting_bytes"] == 4
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()

    include_composite = owl.compose_views(
        overlay,
        right,
        delta=owl.OntologyDelta(
            remove_axioms=owl.CanonicalSet(
                axiom for axiom in overlay.iter_axioms() if axiom != removed
            ),
            policy=owl.DeltaPolicy.IDEMPOTENT,
        ),
    )
    included = _composite_encoded(
        include_composite,
        (
            (encoded_overlay, 1, (removed_ordinal,), b"a" * 32),
            (encoded_right, 0, (), b"b" * 32),
        ),
    )
    native = native_module.create_session_from_encoded(included, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(include_composite, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 6
        assert diagnostics["encoded_referenced_view_count"] == 3
        assert diagnostics["encoded_buffer_count"] == 33
        assert diagnostics["encoded_posting_bytes"] == 4
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_nested_composite_members_share_one_recursive_merge(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    from pyelk.indexing.compiler import compile_ontology

    left, encoded_left = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-nested-composite#>)
        Ontology(<urn:encoded-nested-composite-left>
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(:A :B)
        )"""
    )
    right, encoded_right = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-nested-composite#>)
        Ontology(<urn:encoded-nested-composite-right>
        Declaration(Class(:C))
        SubClassOf(:B :C)
        )"""
    )
    inner = owl.compose_views(left, right)
    encoded_inner = _composite_encoded(
        inner,
        (
            (encoded_left, 0, (), b"a" * 32),
            (encoded_right, 0, (), b"b" * 32),
        ),
    )
    third, encoded_third = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-nested-composite#>)
        Ontology(<urn:encoded-nested-composite-third>
        Declaration(Class(:D))
        SubClassOf(:C :D)
        )"""
    )
    outer = owl.compose_views(inner, third)
    encoded_outer = _composite_encoded(
        outer,
        (
            (encoded_inner, 0, (), b"c" * 32),
            (encoded_third, 0, (), b"d" * 32),
        ),
    )
    native = native_module.create_session_from_encoded(encoded_outer, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(outer, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 7
        assert diagnostics["encoded_referenced_view_count"] == 4
        assert diagnostics["encoded_buffer_count"] == 33
        assert diagnostics["encoded_zero_copy_buffers"] == 33
        assert diagnostics["encoded_posting_bytes"] == 0
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_overlay_delta_recursively_resolves_composite_base(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl

    from pyelk.indexing.compiler import compile_ontology

    left, encoded_left = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-overlay-composite:A>)))"
    )
    right, encoded_right = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-overlay-composite:B>)))"
    )
    composite = owl.compose_views(left, right)
    encoded_composite = _composite_encoded(
        composite,
        (
            (encoded_left, 0, (), b"a" * 32),
            (encoded_right, 0, (), b"b" * 32),
        ),
    )
    delta_source, encoded_delta = _direct_encoded_snapshot(
        b"""Ontology(
        Declaration(Class(<urn:encoded-overlay-composite:C>))
        SubClassOf(
          <urn:encoded-overlay-composite:B>
          <urn:encoded-overlay-composite:C>
        )
        )"""
    )
    overlay, encoded_overlay = _delta_overlay_encoded(
        composite,
        encoded_composite,
        tuple(delta_source.iter_axioms()),
        encoded_delta,
    )
    native = native_module.create_session_from_encoded(encoded_overlay, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(overlay, unsupported="error").encode(),
        1,
    )
    try:
        diagnostics = native.diagnostics()
        assert diagnostics["encoded_segment_count"] == 6
        assert diagnostics["encoded_referenced_view_count"] == 3
        assert diagnostics["encoded_buffer_count"] == 33
        assert diagnostics["encoded_zero_copy_buffers"] == 33
        assert diagnostics["encoded_posting_bytes"] == 0
        assert diagnostics["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
        assert native.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        native.close()
        scalar.close()


def test_hidden_overlay_slice_rejects_malformed_selected_or_local_base_segments(
    native_module: ModuleType,
) -> None:
    from pyowl_core.backends import native_views

    snapshot, direct_encoded = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-overlay:A>)))"
    )
    _overlay, encoded = _noop_overlay_encoded(snapshot, direct_encoded)
    base = encoded.segments[0]
    selected = SimpleNamespace(
        role=base.role,
        owner=base.owner,
        source=base.source,
        posting_mode=2,
        root_ids=memoryview((999).to_bytes(4, "little")),
        anonymous_scope_map=base.anonymous_scope_map,
        member_token=base.member_token,
    )
    with pytest.raises(ValueError, match=r"sorted|unique|range|posting"):
        native_module.create_session_from_encoded(
            _encoded_wrapper(
                encoded,
                segments=(selected,),
                structural_fingerprint=native_views._fingerprint(encoded.buffers, (selected,)),
            ),
            1,
            "error",
        )

    with pytest.raises(ValueError, match=r"local roots"):
        native_module.create_session_from_encoded(
            _encoded_wrapper(
                encoded,
                buffers=direct_encoded.buffers,
                structural_fingerprint=native_views._fingerprint(
                    direct_encoded.buffers, encoded.segments
                ),
            ),
            1,
            "error",
        )


def test_hidden_encoded_session_rolls_back_ignored_axioms(native_module: ModuleType) -> None:
    from pyelk.indexing.compiler import compile_ontology
    from pyelk.indexing.conversion import FEATURE_INDEX

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-transaction#>) Ontology(<urn:encoded-transaction>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(Class(:D))
        Declaration(ObjectProperty(:p))
        Declaration(ObjectProperty(:q))
        Declaration(NamedIndividual(:i))
        SubClassOf(ObjectIntersectionOf(:A ObjectAllValuesFrom(:p :B)) :C)
        SubClassOf(:A ObjectOneOf(:i _:anonymous))
        EquivalentObjectProperties(:p ObjectInverseOf(:q))
        SubObjectPropertyOf(ObjectPropertyChain(:p ObjectInverseOf(:q)) :p)
        SubClassOf(:D :A)
        )"""
    )
    compiled = compile_ontology(snapshot, unsupported="ignore")
    assert compiled.feature_counts[FEATURE_INDEX["ANONYMOUS_INDIVIDUAL"]] == 1
    assert compiled.feature_counts[FEATURE_INDEX["OBJECT_ALL_VALUES_FROM"]] == 1
    assert compiled.feature_counts[FEATURE_INDEX["OBJECT_INVERSE_OF"]] == 2
    assert compiled.feature_counts[FEATURE_INDEX["OBJECT_ONE_OF"]] == 0

    direct = native_module.create_session_from_encoded(encoded, 1, "ignore")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        direct.close()
        scalar.close()

    with pytest.raises(native_module.NativeUnsupportedFeatureError):
        native_module.create_session_from_encoded(encoded, 1, "error")


@pytest.mark.parametrize(
    "feature_name",
    tuple(path.stem for path in sorted(_ONTOLOGY_FIXTURES.glob("*.ofn"))),
)
def test_advertised_encoded_feature_corpus_matches_scalar_compiler(
    native_module: ModuleType,
    feature_name: str,
) -> None:
    from pyowl_core.backends import native_views

    from pyelk.exceptions import UnsupportedFeatureError
    from pyelk.indexing.compiler import compile_ontology

    view = _ontology_feature_view(feature_name)
    encoded = native_views.produce_encoded_structural_view_v1(view)
    compiled = compile_ontology(view, unsupported="ignore")
    direct = native_module.create_session_from_encoded(encoded, 1, "ignore")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
        direct_diagnostics = direct.diagnostics()
        scalar_diagnostics = scalar.diagnostics()
        assert {
            key: value
            for key, value in direct_diagnostics.items()
            if key.startswith("compiler_") and key.endswith("_count")
        } == {
            key: value
            for key, value in scalar_diagnostics.items()
            if key.startswith("compiler_") and key.endswith("_count")
        }
        assert (
            direct_diagnostics["compiler_source_fingerprint"]
            == scalar_diagnostics["compiler_source_fingerprint"]
        )
        assert direct_diagnostics["compiler_digest"] == scalar_diagnostics["compiler_digest"]
    finally:
        direct.close()
        scalar.close()

    try:
        strict_compiled = compile_ontology(view, unsupported="error")
    except UnsupportedFeatureError as scalar_error:
        with pytest.raises(native_module.NativeUnsupportedFeatureError) as native_error:
            native_module.create_session_from_encoded(encoded, 1, "error")
        assert str(native_error.value) == scalar_error.feature
    else:
        strict_direct = native_module.create_session_from_encoded(encoded, 1, "error")
        strict_scalar = native_module.create_session(strict_compiled.encode(), 1)
        try:
            assert (
                strict_direct.diagnostics()["compiler_digest"]
                == strict_scalar.diagnostics()["compiler_digest"]
            )
        finally:
            strict_direct.close()
            strict_scalar.close()

    expected = Reasoner(
        view,
        ReasonerConfig(backend="python", workers=1, unsupported="ignore"),
    )
    actual = Reasoner(
        view,
        ReasonerConfig(backend="rust", workers=1, unsupported="ignore"),
    )
    try:
        expected_diagnostics = expected.diagnostics()
        diagnostics = actual.diagnostics()
        assert diagnostics["ingestion_path"] == "encoded-native"
        assert diagnostics["compiler_digest"] == expected_diagnostics["compiler_digest"]
        assert diagnostics["materialized_scalar_rows"] == 0
        assert {
            key: value
            for key, value in diagnostics.items()
            if key.startswith("compiler_") and key.endswith("_count")
        } == {
            key: value
            for key, value in direct_diagnostics.items()
            if key.startswith("compiler_") and key.endswith("_count")
        }
        assert actual.is_consistent() == expected.is_consistent()
        assert actual.classify() == expected.classify()
        assert actual.classify_object_properties() == expected.classify_object_properties()
        assert actual.realize() == expected.realize()
    finally:
        actual.close()
        expected.close()


@pytest.mark.parametrize(
    "ontology_path",
    _FROZEN_ENCODED_ONTOLOGIES,
    ids=lambda path: path.relative_to(_UPSTREAM).as_posix(),
)
def test_hidden_encoded_frozen_elk_corpus_matches_scalar_compiler(
    native_module: ModuleType,
    ontology_path: Path,
) -> None:
    from pyowl_core.backends import native_views

    from pyelk.indexing.compiler import compile_ontology

    snapshot = _snapshot(ontology_path)
    encoded = native_views.produce_encoded_structural_view_v1(snapshot)
    compiled = compile_ontology(snapshot, unsupported="ignore")
    direct = native_module.create_session_from_encoded(encoded, 1, "ignore")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
        direct_diagnostics = direct.diagnostics()
        scalar_diagnostics = scalar.diagnostics()
        assert {
            key: value
            for key, value in direct_diagnostics.items()
            if key.startswith("compiler_") and key.endswith("_count")
        } == {
            key: value
            for key, value in scalar_diagnostics.items()
            if key.startswith("compiler_") and key.endswith("_count")
        }
        assert (
            direct_diagnostics["compiler_source_fingerprint"]
            == scalar_diagnostics["compiler_source_fingerprint"]
        )
        assert direct_diagnostics["compiler_digest"] == scalar_diagnostics["compiler_digest"]
    finally:
        direct.close()
        scalar.close()


def test_hidden_encoded_w3c_cross_syntax_views_have_one_exact_compiler_result(
    native_module: ModuleType,
) -> None:
    import pyowl_core as owl
    from pyowl_core.backends import native_views

    from pyelk.indexing.compiler import compile_ontology

    assert {path.suffix for path in _W3C_CORE_FIXTURES} == {".ofn", ".owx", ".rdf", ".ttl"}
    expected: tuple[str, bytes] | None = None
    options = owl.LoadOptions(
        imports=owl.ImportPolicy.IGNORE,
        backend=owl.BackendPreference.PYTHON,
    )
    for path in _W3C_CORE_FIXTURES:
        snapshot = owl.load_snapshot(path, options=options)
        encoded = native_views.produce_encoded_structural_view_v1(snapshot)
        compiled = compile_ontology(snapshot, unsupported="error")
        direct = native_module.create_session_from_encoded(encoded, 1, "error")
        scalar = native_module.create_session(compiled.encode(), 1)
        try:
            actual = (
                direct.diagnostics()["compiler_digest"],
                direct.debug_snapshot(realize=True),
            )
            assert actual == (
                scalar.diagnostics()["compiler_digest"],
                scalar.debug_snapshot(realize=True),
            )
            if expected is None:
                expected = actual
            else:
                assert actual == expected
        finally:
            direct.close()
            scalar.close()


def test_hidden_encoded_mmap_view_matches_direct_and_retains_provider(
    native_module: ModuleType,
    tmp_path: Path,
) -> None:
    import pyowl_core as owl
    from pyowl_core.backends import native_views
    from pyowl_core.exceptions import SnapshotInUseError

    from pyelk.indexing.compiler import compile_ontology

    source = _W3C_CORE_FIXTURES[0]
    options = owl.LoadOptions(
        imports=owl.ImportPolicy.IGNORE,
        backend=owl.BackendPreference.PYTHON,
    )
    snapshot = owl.load_snapshot(source, options=options)
    wire_path = tmp_path / "w3c-minimal.pyocore"
    wire_path.write_bytes(owl.encode_snapshot(snapshot))
    mapped = owl.open_snapshot(wire_path, mmap=True, verify=True)
    direct_encoded = native_views.produce_encoded_structural_view_v1(snapshot)
    mapped_encoded = native_views.produce_encoded_structural_view_v1(mapped)
    compiled = compile_ontology(snapshot, unsupported="error")
    direct = native_module.create_session_from_encoded(direct_encoded, 1, "error")
    mmap_session = native_module.create_session_from_encoded(mapped_encoded, 1, "error")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        with pytest.raises(SnapshotInUseError):
            mapped.close()
        expected = scalar.debug_snapshot(realize=True)
        assert direct.debug_snapshot(realize=True) == expected
        assert mmap_session.debug_snapshot(realize=True) == expected
        assert (
            direct.diagnostics()["compiler_digest"]
            == mmap_session.diagnostics()["compiler_digest"]
            == scalar.diagnostics()["compiler_digest"]
        )
        direct_diagnostics = direct.diagnostics()
        mmap_diagnostics = mmap_session.diagnostics()
        for diagnostics in (direct_diagnostics, mmap_diagnostics):
            assert diagnostics["encoded_staging_copy_bytes"] == 0
            assert diagnostics["encoded_private_ir_bytes"] == 0
            assert diagnostics["encoded_zero_copy_buffers"] == 11
        assert direct_diagnostics["encoded_detached_buffer_count"] == 11
        assert direct_diagnostics["encoded_indexed_buffer_count"] == 0
        assert direct_diagnostics["encoded_compiler_gil_released"] is True
        # PyO3's buffer API entered the stable ABI in CPython 3.11, while the
        # single pyELK native wheel targets abi3-py310. The mmap memoryviews
        # therefore remain zero-copy but are indexed with the GIL held.
        assert mmap_diagnostics["encoded_detached_buffer_count"] == 0
        assert mmap_diagnostics["encoded_indexed_buffer_count"] == 11
        assert mmap_diagnostics["encoded_compiler_gil_released"] is False
    finally:
        direct.close()
        mmap_session.close()
        scalar.close()
        del mapped_encoded
        gc.collect()
        mapped.close()


def test_hidden_encoded_compiler_is_hash_seed_and_worker_deterministic(
    native_module: ModuleType,
) -> None:
    native_path = Path(native_module.__file__).resolve()
    runner = Path(__file__).with_name("_encoded_determinism_runner.py")
    ontology = _UPSTREAM / "classification" / "Existentials.owl"
    root = Path(__file__).parents[2]
    python_path = os.pathsep.join((str(root / "src"), str(root.parent / "pyOWLCore" / "src")))
    payloads: list[object] = []
    for seed in ("0", "1", "42", "random"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = python_path
        completed = subprocess.run(
            [sys.executable, os.fspath(runner), os.fspath(native_path), os.fspath(ontology)],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        payloads.append(json.loads(completed.stdout))
    assert payloads[1:] == payloads[:-1]


def test_hidden_direct_encoded_general_class_axioms_match_scalar(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-general#>) Ontology(<urn:encoded-general>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(Class(:D))
        Declaration(Class(:E))
        Declaration(ObjectProperty(:p))
        Declaration(NamedIndividual(:i))
        EquivalentClasses(
          ObjectIntersectionOf(:A :B)
          ObjectSomeValuesFrom(:p ObjectComplementOf(:C))
          :D
        )
        DisjointClasses(ObjectUnionOf(:A :B) ObjectSomeValuesFrom(:p :C))
        DisjointClasses(:A ObjectComplementOf(:B) ObjectSomeValuesFrom(:p :C))
        DisjointUnion(
          :E
          ObjectIntersectionOf(:A :B)
          ObjectSomeValuesFrom(:p :C)
        )
        ClassAssertion(ObjectUnionOf(:A ObjectHasSelf(:p)) :i)
        ObjectPropertyDomain(:p ObjectIntersectionOf(:A :B))
        ObjectPropertyRange(:p ObjectUnionOf(:C ObjectComplementOf(:D)))
        )"""
    )
    direct = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(
        compile_ontology(snapshot, unsupported="error").encode(),
        1,
    )
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
        assert direct.diagnostics()["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
    finally:
        direct.close()
        scalar.close()


def test_hidden_direct_encoded_data_values_match_scalar_literal_keys(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology
    from pyelk.indexing.conversion import FEATURE_INDEX

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-data#>)
        Prefix(xsd:=<http://www.w3.org/2001/XMLSchema#>)
        Ontology(<urn:encoded-data>
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        EquivalentClasses(:A DataHasValue(:dp "1"^^xsd:integer))
        EquivalentClasses(:B DataHasValue(:dp "1"^^xsd:integer))
        SubClassOf(DataHasValue(:dp "hello"@EN) :C)
        )"""
    )
    compiled = compile_ontology(snapshot, unsupported="error")
    assert compiled.feature_counts[FEATURE_INDEX["DATA_HAS_VALUE"]] == 3
    direct = native_module.create_session_from_encoded(encoded, 1, "error")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
    finally:
        direct.close()
        scalar.close()


def test_hidden_encoded_session_deduplicates_annotated_axioms(
    native_module: ModuleType,
) -> None:
    from pyelk.indexing.compiler import compile_ontology
    from pyelk.indexing.conversion import FEATURE_INDEX

    snapshot, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-annotations#>) Ontology(<urn:encoded-annotations>
        Declaration(AnnotationProperty(:ap))
        Declaration(Class(:A))
        Declaration(Class(:B))
        Declaration(Class(:C))
        Declaration(ObjectProperty(:p))
        SubClassOf(Annotation(:ap "one") :A :B)
        SubClassOf(Annotation(:ap "two") :A :B)
        SubClassOf(:A :B)
        EquivalentClasses(Annotation(:ap "one") :B :C)
        EquivalentClasses(Annotation(:ap "two") :B :C)
        FunctionalObjectProperty(Annotation(:ap "one") :p)
        FunctionalObjectProperty(Annotation(:ap "two") :p)
        )"""
    )
    compiled = compile_ontology(snapshot, unsupported="ignore")
    assert compiled.feature_counts[FEATURE_INDEX["FUNCTIONAL_OBJECT_PROPERTY"]] == 1
    direct = native_module.create_session_from_encoded(encoded, 1, "ignore")
    scalar = native_module.create_session(compiled.encode(), 1)
    try:
        assert direct.debug_snapshot(realize=True) == scalar.debug_snapshot(realize=True)
        assert direct.diagnostics()["compiler_digest"] == scalar.diagnostics()["compiler_digest"]
    finally:
        direct.close()
        scalar.close()


def test_hidden_encoded_session_rejects_hostile_envelopes_before_publication(
    native_module: ModuleType,
) -> None:
    _snapshot_value, encoded = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded:A>)))"
    )
    buffers = dict(encoded.buffers)
    buffers["root_kinds"] = memoryview(bytearray(buffers["root_kinds"]))
    altered = dict(encoded.buffers)
    altered_scalar = bytearray(altered["scalar_bytes"])
    altered_scalar[-1] ^= 1
    altered["scalar_bytes"] = memoryview(bytes(altered_scalar))
    cases = (
        _encoded_wrapper(encoded, descriptor=encoded.descriptor + b" "),
        _encoded_wrapper(encoded, segments=()),
        _encoded_wrapper(encoded, buffers=buffers),
        _encoded_wrapper(encoded, buffers=altered),
        _encoded_wrapper(
            encoded,
            structural_fingerprint=SimpleNamespace(algorithm="sha256", schema=1, digest=b"\0" * 32),
        ),
    )
    for candidate in cases:
        with pytest.raises(ValueError, match=r"encoded|descriptor|segment|buffer|fingerprint"):
            native_module.create_session_from_encoded(candidate, 1, "error")


def test_hidden_encoded_session_retains_owner_until_close(native_module: ModuleType) -> None:
    snapshot, encoded = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded:retained>)))"
    )
    encoded_ref = weakref.ref(encoded)
    session = native_module.create_session_from_encoded(encoded, 1, "error")
    del snapshot, encoded
    gc.collect()
    assert encoded_ref() is not None
    session.close()
    gc.collect()
    assert encoded_ref() is None


def test_hidden_encoded_session_serializes_concurrent_native_calls(
    native_module: ModuleType,
) -> None:
    _snapshot_value, encoded = _direct_encoded_snapshot(
        b"""Prefix(:=<urn:encoded-threads#>) Ontology(<urn:encoded-threads>
        Declaration(Class(:A))
        Declaration(Class(:B))
        SubClassOf(:A :B)
        )"""
    )
    session = native_module.create_session_from_encoded(encoded, 2, "error")
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = tuple(executor.map(lambda _index: session.class_taxonomy(), range(12)))
        assert len(set(results)) == 1
    finally:
        session.close()


def test_hidden_direct_encoded_compiler_releases_gil(native_module: ModuleType) -> None:
    axioms = " ".join(
        f"SubClassOf(<urn:encoded-gil:C{index}> <urn:encoded-gil:C{index + 1}>)"
        for index in range(500)
    )
    _snapshot_value, encoded = _direct_encoded_snapshot(
        f"Ontology(<urn:encoded-gil> {axioms})".encode()
    )
    ready = threading.Event()
    run = threading.Event()
    stop = threading.Event()
    counter = [0]

    def spin() -> None:
        ready.set()
        assert run.wait(5)
        while not stop.is_set():
            counter[0] += 1

    worker = threading.Thread(target=spin)
    worker.start()
    assert ready.wait(5)
    previous_interval = sys.getswitchinterval()
    session: Any | None = None
    progress = 0
    try:
        sys.setswitchinterval(0.1)
        before = counter[0]
        run.set()
        session = native_module.create_session_from_encoded(encoded, 1, "error")
        progress = counter[0] - before
    finally:
        sys.setswitchinterval(previous_interval)
        stop.set()
        worker.join(5)
        if session is not None:
            session.close()
    assert not worker.is_alive()
    assert progress > 1_000


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_hidden_encoded_session_rejects_use_after_fork_without_locking(
    native_module: ModuleType,
) -> None:
    _snapshot_value, encoded = _direct_encoded_snapshot(
        b"Ontology(Declaration(Class(<urn:encoded-fork:A>)))"
    )
    session = native_module.create_session_from_encoded(encoded, 1, "error")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - assertions run in the parent
        os.close(read_fd)
        try:
            session.is_inconsistent()
        except BaseException as error:
            outcome = f"{type(error).__name__}:{error}".encode()
        else:
            outcome = b"NO_ERROR"
        os.write(write_fd, outcome)
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    try:
        outcome = os.read(read_fd, 4_096)
        _pid, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert b"RuntimeError" in outcome
        assert b"after fork" in outcome
        assert isinstance(session.is_inconsistent(), bool)
    finally:
        os.close(read_fd)
        session.close()


def test_hidden_encoded_session_survives_interpreter_shutdown(
    native_module: ModuleType,
) -> None:
    native_path = Path(native_module.__file__).resolve()
    runner = Path(__file__).with_name("_encoded_shutdown_runner.py")
    root = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(root / "src"), str(root.parent / "pyOWLCore" / "src"))
    )
    completed = subprocess.run(
        [sys.executable, os.fspath(runner), os.fspath(native_path)],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "encoded native session ready for interpreter shutdown\n"
    assert completed.stderr == ""


def test_native_session_close_is_idempotent_and_terminal(native_module: ModuleType) -> None:
    from tests.helpers.contracts import TinyCompiledOntologyBuilder

    session = native_module.create_session(TinyCompiledOntologyBuilder().build().encode(), 1)
    session.close()
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.class_taxonomy()


def test_native_query_decoder_rejects_corruption(native_module: ModuleType) -> None:
    from tests.helpers.contracts import TinyCompiledOntologyBuilder

    session = native_module.create_session(TinyCompiledOntologyBuilder().build().encode(), 1)
    try:
        for payload in (b"", b"not a query IR", b"PYELKQ\0\0"):
            with pytest.raises(ValueError):
                session.query_class_expression(payload, 0, False)
            with pytest.raises(ValueError):
                session.entails(payload)
        with pytest.raises(ValueError, match="query kind"):
            session.query_class_expression(None, 255, False)
    finally:
        session.close()


def test_none_query_fallbacks_match_python(native_module: ModuleType) -> None:
    from pyelk.backends.python import PythonBackendSession
    from pyelk.reasoning.contracts import QueryKind
    from pyelk.reasoning.wire import decode_raw_query_result
    from tests.helpers.contracts import TinyCompiledOntologyBuilder

    compiled = TinyCompiledOntologyBuilder().add_class("urn:fallback:A").build()
    native = native_module.create_session(compiled.encode(), 1)
    python = PythonBackendSession(
        compiled,
        requested_workers=1,
        native_available=True,
        fallback_reason=None,
    )
    try:
        for kind in QueryKind:
            for direct in (False, True):
                actual = decode_raw_query_result(
                    native.query_class_expression(None, int(kind), direct)
                )
                assert actual == python.query_class_expression(None, kind, direct)
        assert native.entails(None) is python.entails(None) is False
    finally:
        native.close()
        python.close()


@pytest.mark.parametrize(
    "case",
    (
        "magic",
        "major",
        "checksum",
        "truncated",
        "noncontiguous",
        "oversized-count",
        "entity-enum",
        "expression-id",
        "utf8",
        "csr-offset",
        "unknown-required-section",
    ),
)
def test_native_decoder_rejects_frozen_corrupt_input_families(
    native_module: ModuleType, case: str
) -> None:
    from tests.unit.indexing.test_codec import (
        _HEADER,
        _U64,
        _empty_ontology_bytes,
        _refresh_checksum,
        _section_location,
        _with_extra_section,
    )

    encoded = bytearray(_empty_ontology_bytes())
    if case == "magic":
        encoded[0] ^= 0xFF
    elif case == "major":
        encoded[8:10] = (2).to_bytes(2, "little")
    elif case == "checksum":
        encoded[-1] ^= 0xFF
    elif case == "truncated":
        del encoded[-10:]
    elif case == "noncontiguous":
        offset_field = _HEADER.size + 2
        current = int.from_bytes(encoded[offset_field : offset_field + 8], "little")
        encoded[offset_field : offset_field + 8] = (current + 1).to_bytes(8, "little")
    elif case == "oversized-count":
        count_field = _HEADER.size + 2 + 8 + 8
        encoded[count_field : count_field + 8] = _U64.pack(2**63)
    elif case == "entity-enum":
        offset, _, _ = _section_location(encoded, OntologySection.ENTITY_KINDS)
        encoded[offset] = 0xFF
        _refresh_checksum(encoded)
    elif case == "expression-id":
        offset, _, _ = _section_location(encoded, OntologySection.EXPRESSION_ARGUMENTS)
        encoded[offset : offset + 4] = (0xFFFF_FFFF).to_bytes(4, "little")
        _refresh_checksum(encoded)
    elif case == "utf8":
        offset, _, _ = _section_location(encoded, OntologySection.ENTITY_IRI_BYTES)
        encoded[offset] = 0xFF
        _refresh_checksum(encoded)
    elif case == "csr-offset":
        offset, _, count = _section_location(encoded, OntologySection.PROPERTY_CHAIN_OFFSETS)
        final_offset = offset + count * _U64.size
        encoded[final_offset : final_offset + _U64.size] = _U64.pack(999)
        _refresh_checksum(encoded)
    else:
        encoded = bytearray(_with_extra_section(bytes(encoded), 21))
    with pytest.raises(ValueError):
        native_module.create_session(bytes(encoded), 1)


@pytest.mark.parametrize("name", _CLASS_CASES)
def test_frozen_class_taxonomy_matches_python(name: str) -> None:
    snapshot = _snapshot(_UPSTREAM / "classification" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        assert rust.classify() == python.classify()
        assert rust.is_consistent() == python.is_consistent()


@pytest.mark.parametrize("name", _PROPERTY_CASES)
def test_frozen_object_property_taxonomy_matches_python(name: str) -> None:
    snapshot = _snapshot(_UPSTREAM / "classification" / "object_property" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        assert rust.classify_object_properties() == python.classify_object_properties()


@pytest.mark.parametrize("name", _REALIZATION_CASES)
def test_frozen_realization_matches_python(name: str) -> None:
    snapshot = _snapshot(_UPSTREAM / "realization" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        assert rust.realize() == python.realize()


@pytest.mark.parametrize("name", _CLASS_QUERY_CASES)
def test_frozen_class_queries_match_python_for_direct_and_transitive(name: str) -> None:
    expected_rows = _payload(_EXPECTED / "query" / "class" / f"{name}.json")["result"]["value"][
        "queries"
    ]
    python, rust = _reasoners(_query_snapshot(name))
    with python, rust:
        for row in expected_rows:
            expression = _parse_expression(name, row["expression"])
            assert rust.is_satisfiable(expression) == python.is_satisfiable(expression)
            assert rust.equivalent_classes(expression) == python.equivalent_classes(expression)
            for direct in (False, True):
                assert rust.subclasses(expression, direct=direct) == python.subclasses(
                    expression, direct=direct
                )
                assert rust.superclasses(expression, direct=direct) == python.superclasses(
                    expression, direct=direct
                )
                assert rust.instances(expression, direct=direct) == python.instances(
                    expression, direct=direct
                )


@pytest.mark.parametrize("name", _ENTAILMENT_CASES)
def test_frozen_entailment_queries_match_python(name: str) -> None:
    expected_rows = _payload(_EXPECTED / "query" / "entailment" / f"{name}.json")["result"][
        "value"
    ]["queries"]
    snapshot = _snapshot(_UPSTREAM / "query" / "entailment" / f"{name}.owl")
    python, rust = _reasoners(snapshot)
    with python, rust:
        for row in expected_rows:
            axiom = _parse_axiom(row["axiom"])
            assert rust.is_entailed(axiom) == python.is_entailed(axiom)


@pytest.mark.parametrize("workers", (0, 1, 2, 4))
def test_worker_count_does_not_change_results(workers: int) -> None:
    snapshot = _snapshot(_UPSTREAM / "classification" / "Existentials.owl")
    baseline = Reasoner(snapshot, ReasonerConfig(backend="python", workers=1))
    candidate = Reasoner(snapshot, ReasonerConfig(backend="rust", workers=workers))
    with baseline, candidate:
        assert candidate.classify() == baseline.classify()
        assert candidate.classify_object_properties() == baseline.classify_object_properties()
        assert candidate.realize() == baseline.realize()


def test_reused_worker_workspace_drops_root_local_range_facts() -> None:
    import pyowl_core as owl

    from tests.unit.indexing._support import load_functional

    snapshot = load_functional(
        "SubClassOf(:A ObjectSomeValuesFrom(:p :X)) "
        "ObjectPropertyRange(:p :R) "
        "SubClassOf(:B ObjectSomeValuesFrom(:q :X)) "
        "SubClassOf(ObjectSomeValuesFrom(:q ObjectIntersectionOf(:X :R)) :Leaked)",
        ontology_iri="urn:worker-workspace-isolation",
    )
    python, rust = _reasoners(snapshot, workers=1)
    with python, rust:
        assert rust.classify() == python.classify()
        leaked = owl.Class(owl.IRI("urn:test#Leaked"))
        assert all(
            leaked not in node.members
            for node in rust.superclasses(owl.Class(owl.IRI("urn:test#B"))).value
        )
