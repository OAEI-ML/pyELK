from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

import pyelk
from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import CompiledOntology, EntityId, ExpressionId, ExpressionTag
from pyelk.reasoning.conclusions import Conclusion, SubClassInclusionDecomposed
from pyelk.reasoning.properties import saturate_properties
from pyelk.reasoning.rules import RuleDispatcher
from pyelk.reasoning.saturation import (
    SaturationBudgetExceeded,
    SaturationEngine,
    SaturationInterrupted,
)
from pyelk.reasoning.session import SaturationSession, Stage
from tests.unit.indexing._support import entity_id, load_functional

_ROOT = Path(__file__).resolve().parents[3]


def _compiled(body: str) -> CompiledOntology:
    return compile_ontology(load_functional(body, ontology_iri="urn:saturation"))


def _class(compiled: CompiledOntology, name: str) -> ExpressionId:
    iri = name if "://" in name else f"urn:test#{name}"
    class_entity = EntityId(entity_id(compiled, iri))
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is ExpressionTag.CLASS and record.arguments == (class_entity,)
        )
    )


def _expression(
    compiled: CompiledOntology,
    tag: ExpressionTag,
    arguments: tuple[int, ...],
) -> ExpressionId:
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)
            if record.tag is tag and record.arguments == arguments
        )
    )


def test_scheduler_reaches_one_stable_duplicate_suppressed_fixed_point() -> None:
    compiled = _compiled(
        "SubClassOf(:A :B) SubClassOf(:B :C) SubClassOf(:A :C) "
        "SubClassOf(:C :D) SubClassOf(:B :D)"
    )
    roots = tuple(_class(compiled, name) for name in "ABCD")
    engine = SaturationEngine(compiled)
    first = engine.run((roots[0],))
    context = first.contexts[roots[0]]
    assert set(context.decomposed_subsumers) == set(roots)
    assert context.initialized and context.saturated
    assert not context.queued and not context.todo
    assert len(first.property_subsumers) == engine.properties.chain_count
    assert len(first.property_ranges) == len(compiled.entities)

    diagnostics = engine.diagnostics()
    assert diagnostics.conclusions_inserted == sum(
        len(value.conclusions) for value in first.contexts.values()
    )
    assert diagnostics.rule_dispatches == diagnostics.conclusions_inserted
    assert diagnostics.duplicate_insertions == 0
    assert diagnostics.duplicate_candidates > 0

    second = engine.run((roots[0], roots[0]))
    assert second == first
    assert engine.diagnostics() == diagnostics
    with pytest.raises(TypeError):
        first.contexts[roots[0]] = context  # type: ignore[index]


def test_cross_context_writes_reactivate_sources_without_lost_work() -> None:
    compiled = _compiled(
        "SubClassOf(:A ObjectSomeValuesFrom(:p :B)) "
        "ObjectPropertyRange(:p :C) SubClassOf(:C owl:Nothing)"
    )
    class_a = _class(compiled, "A")
    class_b = _class(compiled, "B")
    class_c = _class(compiled, "C")
    snapshot = SaturationEngine(compiled).run((class_a,))
    assert set(snapshot.contexts) == {class_a, class_b}
    assert class_c in snapshot.contexts[class_b].decomposed_subsumers
    assert snapshot.contexts[class_b].inconsistent
    assert snapshot.contexts[class_a].inconsistent
    assert all(
        value.saturated and not value.queued and not value.todo
        for value in snapshot.contexts.values()
    )


def test_budget_and_monitor_interruptions_resume_to_the_same_snapshot() -> None:
    axioms = " ".join(
        f"SubClassOf(:C{index:03d} :C{index + 1:03d})" for index in range(80)
    )
    compiled = _compiled(axioms)
    root = _class(compiled, "C000")
    expected = SaturationEngine(compiled).run((root,))

    budgeted = SaturationEngine(compiled)
    with pytest.raises(SaturationBudgetExceeded):
        budgeted.run((root,), max_conclusions=7)
    pending_after_budget = budgeted.has_pending_work
    assert pending_after_budget
    interrupted_view = budgeted.context(root)
    assert interrupted_view is not None
    assert interrupted_view.queued and not interrupted_view.saturated
    assert budgeted.run() == expected
    pending_after_resume = budgeted.has_pending_work
    assert not pending_after_resume
    assert budgeted.diagnostics().interrupted_runs == 1

    checkpoints = 0

    def interrupt() -> bool:
        nonlocal checkpoints
        checkpoints += 1
        return checkpoints == 6

    monitored = SaturationEngine(compiled)
    with pytest.raises(SaturationInterrupted):
        monitored.run((root,), interrupt=interrupt)
    assert monitored.has_pending_work
    assert monitored.run() == expected
    assert monitored.diagnostics().interrupted_runs == 1


class _FailOnceDispatcher:
    """Inject a failure after a product to exercise transactional dispatch recovery."""

    def __init__(self, delegate: RuleDispatcher) -> None:
        self.delegate = delegate
        self.failed = False

    def dispatch(self, state: Any, premise: Conclusion, producer: Any) -> None:
        if not self.failed:
            self.failed = True
            producer.produce(SubClassInclusionDecomposed(state.root, state.root))
            raise RuntimeError("injected dispatch failure")
        self.delegate.dispatch(state, premise, producer)


def test_rule_failure_retries_the_stored_premise_transactionally() -> None:
    compiled = _compiled("SubClassOf(:A :B) SubClassOf(:B :C)")
    root = _class(compiled, "A")
    expected = SaturationEngine(compiled).run((root,))
    engine = SaturationEngine(compiled)
    engine.dispatcher = cast(Any, _FailOnceDispatcher(engine.dispatcher))
    with pytest.raises(RuntimeError, match="injected dispatch failure"):
        engine.run((root,))
    assert engine.has_pending_work
    assert engine.run() == expected
    diagnostics = engine.diagnostics()
    assert diagnostics.interrupted_runs == 1
    assert diagnostics.rule_dispatches == diagnostics.conclusions_inserted


def test_seed_order_and_long_cycles_do_not_change_results_or_recurse() -> None:
    size = 600
    axioms = " ".join(
        f"SubClassOf(:C{index:04d} :C{(index + 1) % size:04d})" for index in range(size)
    )
    compiled = _compiled(axioms)
    roots = tuple(_class(compiled, f"C{index:04d}") for index in (0, 200, 400))
    forward = SaturationEngine(compiled)
    reverse = SaturationEngine(compiled)
    forward_snapshot = forward.run(roots)
    reverse_snapshot = reverse.run(reversed(roots))
    assert forward_snapshot == reverse_snapshot
    assert all(
        len(forward_snapshot.contexts[root].decomposed_subsumers) == size for root in roots
    )
    assert forward.diagnostics() == reverse.diagnostics()


def test_session_stages_are_monotone_idempotent_and_query_roots_are_cache_safe() -> None:
    compiled = _compiled(
        "SubClassOf(ObjectIntersectionOf(:A :B) :C) "
        "Declaration(NamedIndividual(:unused))"
    )
    session = SaturationSession(compiled)
    assert int(session.stage) == int(Stage.COMPILED)
    session.ensure_properties()
    assert int(session.stage) == int(Stage.PROPERTIES)
    session.ensure_consistency()
    assert int(session.stage) == int(Stage.CONSISTENCY)
    classified = session.ensure_classified()
    assert int(session.stage) == int(Stage.CLASSIFIED)
    realized = session.ensure_realized()
    assert int(session.stage) == int(Stage.REALIZED)
    diagnostics = session.diagnostics()
    assert session.ensure_realized() == realized
    assert session.diagnostics() == diagnostics
    assert set(classified.contexts) < set(realized.contexts)

    class_a = _class(compiled, "A")
    class_b = _class(compiled, "B")
    query_root = _expression(
        compiled,
        ExpressionTag.OBJECT_INTERSECTION_OF,
        (class_a, class_b),
    )
    before_stage = session.stage
    first = session.saturate_query_root(b"intersection:A:B", query_root)
    first_diagnostics = session.diagnostics()
    second = session.saturate_query_root(b"intersection:A:B", query_root)
    assert second == first
    assert session.diagnostics() == first_diagnostics
    assert session.stage is before_stage
    with pytest.raises(ValueError, match="another root"):
        session.saturate_query_root(b"intersection:A:B", class_a)


def test_supplied_property_closure_is_reused_by_identity() -> None:
    compiled = _compiled("Declaration(Class(:A))")
    properties = saturate_properties(compiled)
    engine = SaturationEngine(compiled, properties)
    assert engine.properties is properties


def test_snapshot_and_diagnostics_are_identical_across_hash_seeds() -> None:
    script = """
from pyelk.indexing.compiler import compile_ontology
from pyelk.reasoning.session import SaturationSession
from tests.unit.indexing._support import load_functional

compiled = compile_ontology(load_functional(
    "SubClassOf(:A :B) SubClassOf(:A ObjectSomeValuesFrom(:p :C)) "
    "ObjectPropertyRange(:p :D) DisjointClasses(:B :D)",
    ontology_iri="urn:hash-seed",
))
session = SaturationSession(compiled)
snapshot = session.ensure_realized()
print(repr((
    snapshot.property_subsumers,
    snapshot.property_ranges,
    tuple(snapshot.contexts.items()),
    session.diagnostics(),
)))
"""
    outputs: list[str] = []
    pyowl_source = _ROOT.parent / "pyOWLCore" / "src"
    for seed in ("1", "777"):
        environment = dict(os.environ)
        paths = [str(_ROOT)]
        if _ROOT in Path(pyelk.__file__).resolve().parents:
            paths.insert(0, str(_ROOT / "src"))
            if pyowl_source.is_dir():
                paths.append(str(pyowl_source))
        inherited = environment.get("PYTHONPATH")
        if inherited:
            paths.append(inherited)
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = os.pathsep.join(paths)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
