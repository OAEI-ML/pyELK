from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar, cast

import pyowl_core as owl
import pytest
from pyowl_core.model.axioms import AxiomNode

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import ExpressionTag

from ._support import ExtensionView, as_view, load_functional

A = TypeVar("A", bound=AxiomNode)


def test_semantically_equal_source_orders_freeze_byte_identically() -> None:
    first = load_functional(
        "Declaration(Class(:C)) SubClassOf(:A :B) SubClassOf(ObjectSomeValuesFrom(:p :B) :C)",
        ontology_iri="urn:deterministic",
    )
    second = load_functional(
        "SubClassOf(ObjectSomeValuesFrom(:p :B) :C) SubClassOf(:A :B) Declaration(Class(:C))",
        ontology_iri="urn:deterministic",
    )
    first_bytes = compile_ontology(first).encode()
    assert compile_ontology(first).encode() == first_bytes
    assert compile_ontology(second).encode() == first_bytes


def test_semantic_compiler_mode_is_part_of_source_fingerprint() -> None:
    view = load_functional("SubClassOf(:A :B)")
    default = compile_ontology(view)
    strict = compile_ontology(view, unsupported="error")
    assert default.entities == strict.entities
    assert default.expressions == strict.expressions
    assert default.subclass_axioms == strict.subclass_axioms
    assert default.source_fingerprint != strict.source_fingerprint


def test_hash_seeds_and_empty_path_produce_identical_no_java_output() -> None:
    source = (
        b"Prefix(:=<urn:seed#>) Ontology(<urn:seed> "
        b"EquivalentClasses(:A ObjectIntersectionOf(:B :C)) "
        b"SubObjectPropertyOf(ObjectPropertyChain(:p :q) :r))"
    )
    encoded = base64.b64encode(source).decode("ascii")
    script = (
        "import base64,hashlib,pyowl_core as o;"
        "from pyelk.indexing.compiler import compile_ontology;"
        f"s=base64.b64decode('{encoded}');"
        "v=o.load_snapshot(s,options=o.LoadOptions("
        "format=o.DocumentFormat.FUNCTIONAL,imports=o.ImportPolicy.IGNORE,"
        "backend=o.BackendPreference.PYTHON));"
        "print(hashlib.sha256(compile_ontology(v).encode()).hexdigest())"
    )
    repository = Path(__file__).resolve().parents[3]
    pyowl_source = repository.parent / "pyOWLCore" / "src"
    outputs: set[str] = set()
    for seed in ("0", "1", "37", "random"):
        environment = os.environ.copy()
        environment["PATH"] = ""
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = os.pathsep.join(
            (
                str(repository / "src"),
                str(pyowl_source),
                environment.get("PYTHONPATH", ""),
            )
        )
        outputs.add(
            subprocess.check_output(
                [sys.executable, "-c", script],
                cwd=repository,
                env=environment,
                text=True,
            ).strip()
        )
    assert outputs == {hashlib.sha256(compile_ontology(_seed_view(source)).encode()).hexdigest()}


def _seed_view(source: bytes) -> owl.OntologySnapshot:
    return owl.load_snapshot(
        source,
        options=owl.LoadOptions(
            format=owl.DocumentFormat.FUNCTIONAL,
            imports=owl.ImportPolicy.IGNORE,
            backend=owl.BackendPreference.PYTHON,
        ),
    )


class _RepeatedAxiomView(ExtensionView):
    def __init__(self, count: int) -> None:
        super().__init__(load_functional("", ontology_iri="urn:stream"))
        self.count = count
        self.iteration_calls = 0
        self.yielded = 0
        self.axiom = owl.SubClassOf(
            owl.Class(owl.IRI("urn:stream#A")),
            owl.Class(owl.IRI("urn:stream#B")),
        )

    def iter_axioms(
        self,
        axiom_type: type[A] | None = None,
        *,
        scope: owl.AxiomScope = owl.AxiomScope.CLOSURE,
        document_key: str | None = None,
    ) -> Iterator[AxiomNode | A]:
        assert scope is owl.AxiomScope.CLOSURE
        assert document_key is None
        self.iteration_calls += 1
        if self.iteration_calls != 1:
            raise AssertionError("compiler attempted to materialize or reiterate the closure")
        if axiom_type is not None and axiom_type is not owl.SubClassOf:
            return
        for _ in range(self.count):
            self.yielded += 1
            yield cast(AxiomNode | A, self.axiom)


@pytest.mark.performance
def test_large_streaming_view_is_consumed_once_with_bounded_structural_state() -> None:
    view = _RepeatedAxiomView(25_000)
    started = time.perf_counter()
    compiled = compile_ontology(as_view(view))
    elapsed = time.perf_counter() - started
    assert view.iteration_calls == 1
    assert view.yielded == view.count
    assert len(compiled.subclass_axioms) == 1
    assert len(compiled.expressions) == 4
    class_occurrences = {
        compiled.entities[record.arguments[0]].iri: compiled.expression_occurrences[index]
        for index, record in enumerate(compiled.expressions)
        if record.tag is ExpressionTag.CLASS
    }
    assert class_occurrences["urn:stream#A"].negative == view.count
    assert class_occurrences["urn:stream#B"].positive == view.count
    assert elapsed < 10.0
