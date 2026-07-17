from __future__ import annotations

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import ExpressionTag
from pyelk.reasoning.consistency import (
    consistency_roots,
    occurring_individual_roots,
)
from pyelk.reasoning.session import SaturationSession
from tests.unit.indexing._support import load_functional


def _session(body: str) -> SaturationSession:
    compiled = compile_ontology(load_functional(body, ontology_iri="urn:consistency"))
    return SaturationSession(compiled)


def test_unsatisfiable_unused_named_class_does_not_make_ontology_inconsistent() -> None:
    session = _session("SubClassOf(:Dead owl:Nothing) Declaration(Class(:Live))")
    initial = session.ensure_consistency()
    assert not initial.inconsistent
    assert not initial.owl_thing_inconsistent
    assert not initial.inconsistent_individuals
    classified = session.ensure_classified()
    dead = next(
        root
        for root, context in classified.contexts.items()
        if session.compiled.expressions[root].tag is ExpressionTag.CLASS
        and session.compiled.entities[
            session.compiled.expressions[root].arguments[0]
        ].iri.endswith("#Dead")
        and context.inconsistent
    )
    assert classified.contexts[dead].inconsistent
    assert not classified.inconsistent_ontology
    assert not session.consistency.inconsistent


def test_inconsistent_top_context_makes_ontology_inconsistent() -> None:
    session = _session("SubClassOf(owl:Thing owl:Nothing)")
    state = session.ensure_consistency()
    assert state.owl_thing_inconsistent
    assert state.inconsistent
    assert session.snapshot().inconsistent_ontology


def test_asserted_inconsistent_individual_is_global_but_declaration_is_not_existence() -> None:
    declared = _session(
        "Declaration(NamedIndividual(:declared)) SubClassOf(:Dead owl:Nothing)"
    )
    assert occurring_individual_roots(declared.compiled) == ()
    roots = consistency_roots(declared.compiled)
    assert len(roots) == 1
    assert not declared.ensure_consistency().inconsistent

    asserted = _session("ClassAssertion(owl:Nothing :asserted)")
    state = asserted.ensure_consistency()
    assert len(state.individual_roots) == 1
    assert state.inconsistent_individuals == state.individual_roots
    assert state.inconsistent


def test_object_assertion_target_is_an_occurring_consistency_root() -> None:
    session = _session(
        "ObjectPropertyAssertion(:p :source :target) "
        "ObjectPropertyRange(:p owl:Nothing)"
    )
    state = session.ensure_consistency()
    assert len(state.individual_roots) == 2
    assert state.inconsistent
    assert state.inconsistent_individuals


def test_top_object_property_below_bottom_is_a_global_inconsistency_condition() -> None:
    session = _session(
        "SubObjectPropertyOf(owl:topObjectProperty owl:bottomObjectProperty)"
    )
    state = session.ensure_consistency()
    assert state.top_object_property_in_bottom
    assert state.inconsistent
    assert not state.owl_thing_inconsistent
    assert not state.inconsistent_individuals


def test_consistency_state_and_snapshot_are_stable_across_later_stages() -> None:
    session = _session("ClassAssertion(:A :a) SubClassOf(:A :B)")
    consistency = session.ensure_consistency()
    snapshot = session.snapshot()
    assert not consistency.inconsistent
    session.ensure_classified()
    session.ensure_realized()
    assert session.consistency == consistency
    assert not session.snapshot().inconsistent_ontology
    assert snapshot.inconsistent_ontology is False
