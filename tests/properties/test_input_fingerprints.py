from __future__ import annotations

from pathlib import Path

import pyowl_core
from hypothesis import given, settings
from hypothesis import strategies as st

from pyelk.inputs import (
    SemanticCacheRecord,
    capture_input,
    semantic_cache_record,
    structural_cache_record,
)
from tests.unit.inputs._support import functional, load_options

_AXIOMS = (
    "Declaration(Class(:A))",
    "Declaration(Class(:B))",
    "SubClassOf(:A :B)",
)


def _semantic(value: bytes | str | Path) -> SemanticCacheRecord:
    captured = capture_input(value, options=load_options()).ontology
    return semantic_cache_record(
        captured,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    )


@settings(max_examples=24, deadline=None)
@given(
    order=st.permutations(_AXIOMS),
    whitespace=st.sampled_from((" ", "\n", "\t", "\r\n")),
)
def test_axiom_order_and_whitespace_do_not_change_cache_identity(
    order: tuple[str, ...],
    whitespace: str,
) -> None:
    baseline = capture_input(
        functional("urn:properties", body=_AXIOMS),
        options=load_options(),
    )
    permuted = capture_input(
        functional(
            "urn:properties",
            body=order,
            whitespace=whitespace,
        ),
        options=load_options(),
    )
    assert permuted.ontology.logical_fingerprint == baseline.ontology.logical_fingerprint
    assert permuted.ontology.signature_fingerprint == baseline.ontology.signature_fingerprint
    assert semantic_cache_record(
        permuted.ontology,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    ) == semantic_cache_record(
        baseline.ontology,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    )


@settings(max_examples=12, deadline=None)
@given(order=st.permutations(("urn:left", "urn:right", "urn:leaf")))
def test_import_declaration_order_preserves_effective_fingerprints(
    order: tuple[str, ...],
) -> None:
    selected = tuple(value for value in order if value != "urn:leaf")
    root = functional("urn:root", imports=selected)
    mapping = pyowl_core.MappingResolver(
        {
            "urn:left": functional("urn:left", imports=("urn:leaf",)),
            "urn:right": functional("urn:right", imports=("urn:leaf",)),
            "urn:leaf": functional(
                "urn:leaf",
                body=("Declaration(Class(:Leaf))",),
            ),
        }
    )
    captured = capture_input(
        root,
        options=load_options(pyowl_core.ImportPolicy.RESOLVE_LOCAL),
        resolver=mapping,
    )
    canonical = capture_input(
        functional("urn:root", imports=("urn:left", "urn:right")),
        options=load_options(pyowl_core.ImportPolicy.RESOLVE_LOCAL),
        resolver=mapping,
    )
    assert captured.ontology.logical_fingerprint == canonical.ontology.logical_fingerprint
    assert captured.ontology.signature_fingerprint == canonical.ontology.signature_fingerprint
    assert semantic_cache_record(
        captured.ontology,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    ) == semantic_cache_record(
        canonical.ontology,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    )


def test_path_prefix_and_source_spelling_are_absent_from_semantic_record(
    tmp_path: Path,
) -> None:
    canonical = functional("urn:location", body=_AXIOMS)
    renamed_prefix = (
        b"Prefix(named:=<urn:test#>) "
        b"Ontology(<urn:location> "
        b"Declaration(Class(named:A)) "
        b"Declaration(Class(named:B)) "
        b"SubClassOf(named:A named:B))"
    )
    path = tmp_path / "renamed-prefix.ofn"
    path.write_bytes(renamed_prefix)
    assert _semantic(canonical) == _semantic(path)


def test_annotation_only_change_partitions_structural_not_semantic_cache() -> None:
    plain = capture_input(
        functional(
            "urn:annotations",
            body=(
                "Declaration(AnnotationProperty(:label))",
                "Declaration(Class(:A))",
            ),
        ),
        options=load_options(),
    ).ontology
    annotated = capture_input(
        functional(
            "urn:annotations",
            body=(
                "Annotation(<urn:test#label> <urn:test#A>)",
                "Declaration(AnnotationProperty(:label))",
                "Declaration(Class(:A))",
            ),
        ),
        options=load_options(),
    ).ontology
    plain_semantic = semantic_cache_record(
        plain,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    )
    annotated_semantic = semantic_cache_record(
        annotated,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    )
    assert plain.logical_fingerprint == annotated.logical_fingerprint
    assert plain.signature_fingerprint == annotated.signature_fingerprint
    assert plain.structural_fingerprint != annotated.structural_fingerprint
    assert plain_semantic == annotated_semantic
    assert structural_cache_record(
        plain,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    ) != structural_cache_record(
        annotated,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    )


def test_noop_overlay_changes_storage_shape_not_effective_cache_identity() -> None:
    snapshot = capture_input(
        functional("urn:overlay", body=_AXIOMS),
        options=load_options(),
    )
    overlay = capture_input(
        pyowl_core.apply_delta(snapshot.ontology.view, pyowl_core.OntologyDelta())
    )
    assert overlay.ontology.logical_fingerprint == snapshot.ontology.logical_fingerprint
    assert overlay.ontology.signature_fingerprint == snapshot.ontology.signature_fingerprint
    assert overlay.ontology.structural_fingerprint == snapshot.ontology.structural_fingerprint
    assert overlay.revision.kind.value == "overlay"
    assert snapshot.revision.kind.value == "snapshot"
    assert semantic_cache_record(
        overlay.ontology,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    ) == semantic_cache_record(
        snapshot.ontology,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
    )


def test_semantic_options_have_an_explicit_cache_partition() -> None:
    captured = capture_input(
        functional("urn:options", body=_AXIOMS),
        options=load_options(),
    ).ontology
    first = semantic_cache_record(
        captured,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
        semantic_options_fingerprint=b"a" * 32,
    )
    second = semantic_cache_record(
        captured,
        compiler_schema_version=1,
        compatibility_id="elk-b8ac5ce-v1",
        semantic_options_fingerprint=b"b" * 32,
    )
    assert first != second
