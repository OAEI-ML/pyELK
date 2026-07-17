from __future__ import annotations

from pathlib import Path

import pyowl_core as owl

from tests.parity.minimize import minimize_document

OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


def _edge(axiom: owl.Axiom) -> tuple[str, str] | None:
    if not isinstance(axiom, owl.SubClassOf):
        return None
    if not isinstance(axiom.sub_class, owl.Class) or not isinstance(axiom.super_class, owl.Class):
        return None
    return axiom.sub_class.iri.value, axiom.super_class.iri.value


def test_semantic_minimizer_is_deterministic_and_one_minimal(tmp_path: Path) -> None:
    source = tmp_path / "source.ofn"
    source.write_bytes(
        b"Prefix(:=<urn:min#>) Ontology(<urn:min> "
        b"Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
        b"SubClassOf(:A :B) SubClassOf(:B :C) SubClassOf(:A :C))"
    )
    required = {("urn:min#A", "urn:min#B"), ("urn:min#B", "urn:min#C")}

    def predicate(path: Path) -> bool:
        snapshot = owl.load_snapshot(path, options=OPTIONS)
        return required.issubset(filter(None, (_edge(axiom) for axiom in snapshot.iter_axioms())))

    first = minimize_document(source, predicate)
    second = minimize_document(source, predicate)
    assert first == second
    assert first.source_axioms == 6
    assert first.minimized_axioms == 2
    minimized = tmp_path / "minimized.ofn"
    minimized.write_bytes(first.document)
    assert predicate(minimized)
    snapshot = owl.load_snapshot(minimized, options=OPTIONS)
    assert {_edge(axiom) for axiom in snapshot.iter_axioms()} == required
