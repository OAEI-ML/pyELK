from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pyowl_core as owl
import pytest

import pyelk
from pyelk import Reasoner, ReasonerConfig

ROOT = Path(__file__).resolve().parents[2]
OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)
PAYLOAD = (
    b"Prefix(:=<urn:consumer#>) Ontology(<urn:consumer> "
    b"Declaration(Class(:A)) Declaration(Class(:B)) Declaration(Class(:C)) "
    b"SubClassOf(:A :B) SubClassOf(:B :C))"
)


class ExactStyleSource:
    """The only Exact-OM protocol surface pyELK is allowed to observe."""

    def __init__(self, view: owl.OntologyView) -> None:
        self.view = view
        self.calls = 0
        self.path_fallback_calls = 0

    def owl_snapshot(self) -> owl.OntologyView:
        self.calls += 1
        return self.view

    def __fspath__(self) -> str:
        self.path_fallback_calls += 1
        raise AssertionError("a snapshot provider must never be traversed as a path")


def _taxonomy_rows(reasoner: Reasoner) -> tuple[object, ...]:
    result = reasoner.classify()
    value = result.require_complete()
    nodes = tuple(
        sorted(
            (tuple(member.iri.value for member in node.members) for node in value.nodes),
            key=lambda row: tuple(map(str.encode, row)),
        )
    )
    edges = tuple(
        sorted(
            (
                tuple(member.iri.value for member in sub.members),
                tuple(member.iri.value for member in sup.members),
            )
            for sub, sup in value.direct_edges
        )
    )
    return nodes, edges


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(PAYLOAD, id="bytes"),
        pytest.param(bytearray(PAYLOAD), id="bytearray"),
        pytest.param(memoryview(PAYLOAD), id="memoryview"),
    ),
)
def test_standalone_memory_inputs_match_the_shared_snapshot(source: object) -> None:
    snapshot = owl.load_snapshot(PAYLOAD, options=OPTIONS)
    with (
        Reasoner(source, ReasonerConfig(backend="python"), load_options=OPTIONS) as standalone,
        Reasoner(snapshot, ReasonerConfig(backend="python")) as shared,
    ):
        assert _taxonomy_rows(standalone) == _taxonomy_rows(shared)


def test_path_and_caller_owned_streams_match_without_retry(tmp_path: Path) -> None:
    path = tmp_path / "consumer.ofn"
    path.write_bytes(PAYLOAD)
    binary = io.BytesIO(PAYLOAD)
    text = io.StringIO(PAYLOAD.decode("utf-8"))
    values: list[tuple[object, ...]] = []
    for source in (path, os.fspath(path)):
        with Reasoner(source, ReasonerConfig(backend="python"), load_options=OPTIONS) as reasoner:
            values.append(_taxonomy_rows(reasoner))
    for source in (binary, text):
        with Reasoner(
            source,
            ReasonerConfig(backend="python"),
            document_iri="urn:consumer:stream",
            load_options=OPTIONS,
        ) as reasoner:
            values.append(_taxonomy_rows(reasoner))
    assert all(value == values[0] for value in values)
    assert not binary.closed
    assert not text.closed


def test_exact_provider_and_composite_are_coerced_once_without_parse_or_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = owl.load_snapshot(
        b"Prefix(:=<urn:consumer#>) Ontology(SubClassOf(:A :B))",
        options=OPTIONS,
    )
    target = owl.load_snapshot(
        b"Prefix(:=<urn:consumer#>) Ontology(SubClassOf(:B :C))",
        options=OPTIONS,
    )
    composite = owl.compose_views(source, target, roles=("source", "target"))
    provider = ExactStyleSource(composite)
    coerce_calls = 0
    actual_coerce = owl.coerce_snapshot

    def counted_coerce(*args: object, **kwargs: object) -> owl.OntologyView:
        nonlocal coerce_calls
        coerce_calls += 1
        return actual_coerce(*args, **kwargs)  # type: ignore[arg-type]

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("shared consumer input must never enter the parser")

    monkeypatch.setattr(owl, "coerce_snapshot", counted_coerce)
    monkeypatch.setattr(owl, "load_snapshot", forbidden)
    monkeypatch.setattr(owl, "parse_document", forbidden)
    with Reasoner(provider, ReasonerConfig(backend="python")) as reasoner:
        assert reasoner.ontology is composite
        assert tuple(member.view for member in composite.members) == (source, target)
        taxonomy = reasoner.classify().require_complete()
        public_a = next(
            entity for entity in composite.signature() if entity.iri.value.endswith("#A")
        )
        assert taxonomy.node(public_a).members[0] is public_a  # type: ignore[union-attr]
    assert provider.calls == 1
    assert provider.path_fallback_calls == 0
    assert coerce_calls == 1
    assert composite.signature()


def test_oaei_wire_worker_path_uses_verified_mmap_and_zero_owl_parses(
    tmp_path: Path,
) -> None:
    snapshot = owl.load_snapshot(PAYLOAD, options=OPTIONS)
    wire_path = tmp_path / "consumer.pyocore"
    wire_path.write_bytes(owl.encode_snapshot(snapshot))
    source_import = Path(pyelk.__file__).resolve()
    source_checkout = ROOT in source_import.parents
    environment = os.environ.copy()
    environment["PATH"] = ""
    environment["PYELK_PURE_PYTHON"] = "1"
    if source_checkout:
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(ROOT / "src"), str(ROOT.parent / "pyOWLCore" / "src"))
        )
    else:
        environment.pop("PYTHONPATH", None)
    script = """
import json
import pyowl_core as owl
import pyowl_core.api as core_api
from pyelk import Reasoner, ReasonerConfig

snapshot = owl.open_snapshot(WIRE_PATH, mmap=True, verify=True)
def forbidden(*args, **kwargs):
    raise AssertionError("wire worker attempted an OWL parse")
owl.load_snapshot = forbidden
owl.parse_document = forbidden
core_api.load_snapshot = forbidden
core_api.parse_document = forbidden
try:
    with Reasoner(snapshot, ReasonerConfig(backend="python")) as reasoner:
        assert reasoner.ontology is snapshot
        taxonomy = reasoner.classify().require_complete()
        payload = {
            "backend": reasoner.backend.name,
            "bottom": sorted(member.iri.value for member in taxonomy.bottom.members),
            "logical_fingerprint": snapshot.logical_fingerprint.hex,
            "wire_verified": True,
        }
    assert snapshot.signature()
    print(json.dumps(payload, sort_keys=True))
finally:
    snapshot.close()
""".replace("WIRE_PATH", repr(os.fspath(wire_path)))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "backend": "python",
        "bottom": ["http://www.w3.org/2002/07/owl#Nothing"],
        "logical_fingerprint": snapshot.logical_fingerprint.hex,
        "wire_verified": True,
    }
