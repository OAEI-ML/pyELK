from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyowl_core as owl
import pytest

from pyelk import EntityNode, Reasoner, ReasonerConfig, Taxonomy
from pyelk.reasoning.contracts import CompletenessIssue
from tests.unit.reasoning.test_entailment import _parse_axiom
from tests.unit.reasoning.test_queries import _parse_expression

_DATA = Path(__file__).parents[1] / "data" / "elk-v0.6.0"
_EXPECTED = _DATA / "expected"
_UPSTREAM = _DATA / "upstream"
_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)
_CLASS_CASES = tuple(
    path.stem
    for path in sorted((_EXPECTED / "classification").glob("*.json"))
    if path.stem
    not in {
        # These pinned Java resources contain duplicate set positions rejected by the
        # shared canonical OWL model. Their normalized algorithm cases remain covered by
        # the WP8/WP9 frozen-IR suites; every core-representable fixture crosses this facade.
        "ConjunctionsComplex",
        "DisjointSelf",
        "DuplicateConjuncts",
        "DuplicateDisjuncts",
    }
)
_PROPERTY_CASES = tuple(
    path.stem for path in sorted((_EXPECTED / "classification" / "object_property").glob("*.json"))
)
_REALIZATION_CASES = tuple(path.stem for path in sorted((_EXPECTED / "realization").glob("*.json")))
_CLASS_QUERY_CASES = tuple(
    path.stem for path in sorted((_EXPECTED / "query" / "class").glob("*.json"))
)
_ENTAILMENT_CASES = tuple(
    path.stem for path in sorted((_EXPECTED / "query" / "entailment").glob("*.json"))
)


def _payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _snapshot(path: Path) -> owl.OntologySnapshot:
    return owl.load_snapshot(path, options=_OPTIONS)


def _node(node: EntityNode[owl.Entity]) -> list[str]:
    return sorted((member.iri.value for member in node.members), key=str.encode)


def _node_rows(nodes: tuple[EntityNode[owl.Entity], ...]) -> list[list[str]]:
    return sorted((_node(node) for node in nodes), key=lambda row: tuple(map(str.encode, row)))


def _sorted_expected_nodes(nodes: list[list[str]]) -> list[list[str]]:
    return sorted(nodes, key=lambda row: tuple(map(str.encode, row)))


def _taxonomy(value: Taxonomy[owl.Entity]) -> dict[str, object]:
    nodes = sorted(
        (_node(node) for node in value.nodes), key=lambda node: tuple(map(str.encode, node))
    )
    index = {tuple(node): position for position, node in enumerate(nodes)}
    edges = sorted(
        [index[tuple(_node(sub))], index[tuple(_node(sup))]] for sub, sup in value.direct_edges
    )
    return {
        "bottom": index[tuple(_node(value.bottom))],
        "direct_edges": edges,
        "nodes": nodes,
        "top": index[tuple(_node(value.top))],
    }


def _issues(values: tuple[CompletenessIssue, ...]) -> list[dict[str, object]]:
    return [
        {
            "constructors": list(issue.constructors),
            "features": list(issue.features),
            "polarities": list(issue.polarities),
            "task": issue.task.value,
        }
        for issue in values
    ]


def _query_snapshot(name: str) -> owl.OntologySnapshot:
    if name in {"DuplicateConjuncts", "DuplicateDisjuncts"}:
        return owl.load_snapshot(
            b"Prefix(:=<http://example.org/>) Ontology(SubClassOf(:A :B))",
            options=_OPTIONS,
        )
    if name == "ConjunctionsComplex":
        pairs = (
            ("B", "C", "BC"),
            ("B", "D", "BD"),
            ("C", "B", "CB"),
            ("C", "D", "CD"),
            ("D", "C", "DC"),
            ("D", "B", "DB"),
        )
        triples = ("BCD", "BDC", "CBD", "CDB", "DBC", "DCB")
        body = " ".join(
            (
                "SubClassOf(:A :B)",
                "SubClassOf(:A :C)",
                "SubClassOf(:A :D)",
                "SubClassOf(:B :BB)",
                "SubClassOf(:C :CC)",
                "SubClassOf(:D :DD)",
                *(f"SubClassOf(ObjectIntersectionOf(:{a} :{b}) :{c})" for a, b, c in pairs),
                *(f"SubClassOf(ObjectIntersectionOf(:B :C :D) :{target})" for target in triples),
            )
        )
        return owl.load_snapshot(
            f"Prefix(:=<http://example.org/>) Ontology({body})".encode(),
            options=_OPTIONS,
        )
    return _snapshot(_UPSTREAM / "query" / "class" / f"{name}.owl")


@pytest.mark.parametrize("name", _CLASS_CASES)
def test_all_core_representable_frozen_class_taxonomies_through_public_facade(
    name: str,
) -> None:
    expected = _payload(_EXPECTED / "classification" / f"{name}.json")["result"]
    with Reasoner(
        _snapshot(_UPSTREAM / "classification" / f"{name}.owl"),
        ReasonerConfig(backend="python", workers=1),
    ) as reasoner:
        actual = reasoner.classify()
    assert _taxonomy(actual.value) == expected["value"]
    assert actual.complete is expected["complete"]
    assert _issues(actual.reasons) == expected["issues"]


@pytest.mark.parametrize("name", _PROPERTY_CASES)
def test_all_frozen_object_property_taxonomies_through_public_facade(name: str) -> None:
    expected = _payload(_EXPECTED / "classification" / "object_property" / f"{name}.json")["result"]
    with Reasoner(
        _snapshot(_UPSTREAM / "classification" / "object_property" / f"{name}.owl"),
        ReasonerConfig(backend="python", workers=1),
    ) as reasoner:
        actual = reasoner.classify_object_properties()
    assert _taxonomy(actual.value) == expected["value"]
    assert actual.complete is expected["complete"]
    assert _issues(actual.reasons) == expected["issues"]


@pytest.mark.parametrize("name", _REALIZATION_CASES)
def test_all_frozen_realizations_through_public_facade(name: str) -> None:
    expected = _payload(_EXPECTED / "realization" / f"{name}.json")["result"]
    with Reasoner(
        _snapshot(_UPSTREAM / "realization" / f"{name}.owl"),
        ReasonerConfig(backend="python", workers=1),
    ) as reasoner:
        actual = reasoner.realize()
    value = _taxonomy(actual.value.class_taxonomy)
    instance_nodes = sorted(
        (_node(node) for node in actual.value.instances),
        key=lambda node: tuple(map(str.encode, node)),
    )
    instance_index = {tuple(node): position for position, node in enumerate(instance_nodes)}
    class_index = {
        tuple(node): position
        for position, node in enumerate(value["nodes"])  # type: ignore[arg-type]
    }
    value.update(
        {
            "direct_types": sorted(
                [instance_index[tuple(_node(instance))], class_index[tuple(_node(class_node))]]
                for instance, class_node in actual.value.direct_types
            ),
            "instance_nodes": instance_nodes,
        }
    )
    assert value == expected["value"]
    assert actual.complete is expected["complete"]
    assert _issues(actual.reasons) == expected["issues"]


@pytest.mark.parametrize("name", _CLASS_QUERY_CASES)
def test_all_frozen_class_query_groups_through_public_facade(name: str) -> None:
    expected_rows = _payload(_EXPECTED / "query" / "class" / f"{name}.json")["result"]["value"][
        "queries"
    ]
    with Reasoner(
        _query_snapshot(name),
        ReasonerConfig(backend="python", workers=1),
    ) as reasoner:
        for expected in expected_rows:
            expression = _parse_expression(name, expected["expression"])
            satisfiable = reasoner.is_satisfiable(expression)
            equivalent = reasoner.equivalent_classes(expression)
            subclasses = reasoner.subclasses(expression, direct=True)
            superclasses = reasoner.superclasses(expression, direct=True)
            instances = reasoner.instances(expression, direct=True)
            equivalent_value = [] if not equivalent.value else _node(equivalent.value[0])
            assert satisfiable.value is expected["satisfiable"]["value"]
            assert equivalent_value == expected["equivalent_classes"]["value"]
            assert _node_rows(subclasses.value) == _sorted_expected_nodes(
                expected["direct_subclasses"]["value"]
            )
            assert _node_rows(superclasses.value) == _sorted_expected_nodes(
                expected["direct_superclasses"]["value"]
            )
            assert _node_rows(instances.value) == _sorted_expected_nodes(
                expected["direct_instances"]["value"]
            )
            for actual, operation in (
                (satisfiable, "satisfiable"),
                (equivalent, "equivalent_classes"),
                (subclasses, "direct_subclasses"),
                (superclasses, "direct_superclasses"),
                (instances, "direct_instances"),
            ):
                assert actual.complete is expected[operation]["complete"]
                assert _issues(actual.reasons) == expected["issues"]


@pytest.mark.parametrize("name", _ENTAILMENT_CASES)
def test_all_frozen_entailment_groups_through_public_facade(name: str) -> None:
    expected_rows = _payload(_EXPECTED / "query" / "entailment" / f"{name}.json")["result"][
        "value"
    ]["queries"]
    with Reasoner(
        _snapshot(_UPSTREAM / "query" / "entailment" / f"{name}.owl"),
        ReasonerConfig(backend="python", workers=1),
    ) as reasoner:
        for expected in expected_rows:
            actual = reasoner.is_entailed(_parse_axiom(expected["axiom"]))
            assert actual.value is expected["entailed"]
            assert actual.complete is expected["complete"]
            assert _issues(actual.reasons) == expected["issues"]
