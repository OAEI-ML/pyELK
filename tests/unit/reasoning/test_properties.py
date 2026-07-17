from __future__ import annotations

import importlib
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pyowl_core as owl
import pytest

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import EntityId, EntityKind, ExpressionId, ExpressionTag, PropertyChainId
from pyelk.reasoning.properties import (
    PropertyRange,
    SubPropertyChain,
    property_range_inherited,
    saturate_properties,
    sub_property_chain_expanded_sub_object_property_of,
    sub_property_chain_tautology,
)
from tests.unit.indexing._support import entity_id, load_functional

tomllib: Any = importlib.import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _ROOT / "data" / "manifests" / "property-inferences.toml"
_UPSTREAM = _ROOT / "data" / "elk-v0.6.0" / "upstream" / "classification" / "object_property"
_EXPECTED = _ROOT / "data" / "elk-v0.6.0" / "expected" / "classification" / "object_property"
_LOAD_OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)


def _property(compiled: object, name: str) -> EntityId:
    return EntityId(entity_id(compiled, f"urn:test#{name}"))  # type: ignore[arg-type]


def _class_expression(compiled: object, name: str) -> ExpressionId:
    class_id = entity_id(compiled, f"urn:test#{name}")  # type: ignore[arg-type]
    return ExpressionId(
        next(
            index
            for index, record in enumerate(compiled.expressions)  # type: ignore[attr-defined]
            if record.tag is ExpressionTag.CLASS and record.arguments == (class_id,)
        )
    )


def test_sub_property_chain_tautology_inference() -> None:
    assert sub_property_chain_tautology(PropertyChainId(7)) == SubPropertyChain(
        PropertyChainId(7), PropertyChainId(7)
    )


def test_sub_property_chain_expansion_requires_both_premises() -> None:
    premise = SubPropertyChain(PropertyChainId(2), PropertyChainId(3))
    assert sub_property_chain_expanded_sub_object_property_of(
        PropertyChainId(1), PropertyChainId(2), premise
    ) == SubPropertyChain(PropertyChainId(1), PropertyChainId(3))
    assert (
        sub_property_chain_expanded_sub_object_property_of(
            PropertyChainId(1), PropertyChainId(2), None
        )
        is None
    )
    assert (
        sub_property_chain_expanded_sub_object_property_of(
            PropertyChainId(1),
            PropertyChainId(4),
            premise,
        )
        is None
    )


def test_property_range_inheritance_requires_both_premises() -> None:
    hierarchy = SubPropertyChain(PropertyChainId(1), PropertyChainId(2))
    explicit_range = PropertyRange(EntityId(20), ExpressionId(7))
    arguments = (
        EntityId(10),
        EntityId(20),
        PropertyChainId(1),
        PropertyChainId(2),
    )
    assert property_range_inherited(*arguments, hierarchy, explicit_range) == PropertyRange(
        EntityId(10), ExpressionId(7)
    )
    assert property_range_inherited(*arguments, None, explicit_range) is None
    assert property_range_inherited(*arguments, hierarchy, None) is None
    assert (
        property_range_inherited(
            *arguments,
            SubPropertyChain(PropertyChainId(1), PropertyChainId(3)),
            explicit_range,
        )
        is None
    )
    assert (
        property_range_inherited(
            *arguments,
            hierarchy,
            PropertyRange(EntityId(21), ExpressionId(7)),
        )
        is None
    )


def test_hierarchy_cycles_complex_subchains_and_range_inheritance() -> None:
    compiled = compile_ontology(
        load_functional(
            "SubObjectPropertyOf(:p :q) "
            "SubObjectPropertyOf(:q :r) "
            "SubObjectPropertyOf(ObjectPropertyChain(:a :b) :p) "
            "ObjectPropertyRange(:r :C) "
            "ObjectPropertyRange(:q :D) "
            "EquivalentObjectProperties(:x :y)"
        )
    )
    saturated = saturate_properties(compiled)
    properties = {name: _property(compiled, name) for name in "abpqrxy"}
    singletons = {
        name: saturated.singleton_chain(property_id) for name, property_id in properties.items()
    }
    chain_ab = saturated.lookup_chain((properties["a"], properties["b"]))
    assert chain_ab is not None
    assert set(saturated.super_chains(chain_ab)) >= {
        singletons["p"],
        singletons["q"],
        singletons["r"],
    }
    assert set(saturated.super_chains(singletons["p"])) >= {
        singletons["p"],
        singletons["q"],
        singletons["r"],
    }
    assert set(saturated.super_chains(singletons["x"])) == {
        singletons["x"],
        singletons["y"],
    }
    assert set(saturated.super_chains(singletons["y"])) == {
        singletons["x"],
        singletons["y"],
    }
    class_c = _class_expression(compiled, "C")
    class_d = _class_expression(compiled, "D")
    assert set(saturated.ranges(properties["p"])) == {class_c, class_d}
    assert set(saturated.ranges(properties["q"])) == {class_c, class_d}
    assert saturated.ranges(properties["r"]) == (class_c,)
    assert saturated.ranges(properties["a"]) == ()
    assert all(conclusion.property != properties["a"] for conclusion in saturated.property_ranges)


def test_suffix_compositions_transitivity_reflexivity_and_immutability() -> None:
    compiled = compile_ontology(
        load_functional(
            "SubObjectPropertyOf(ObjectPropertyChain(:p :q :r) :s) "
            "TransitiveObjectProperty(:t) ReflexiveObjectProperty(:p)"
        )
    )
    saturated = saturate_properties(compiled)
    properties = {name: _property(compiled, name) for name in "pqrst"}
    singleton = {
        name: saturated.singleton_chain(property_id) for name, property_id in properties.items()
    }
    suffix = saturated.lookup_chain((properties["q"], properties["r"]))
    full = saturated.lookup_chain((properties["p"], properties["q"], properties["r"]))
    transitive = saturated.lookup_chain((properties["t"], properties["t"]))
    assert suffix is not None and full is not None and transitive is not None
    assert saturated.chain_properties(suffix) == (properties["q"], properties["r"])
    assert saturated.chain_properties(full) == (
        properties["p"],
        properties["q"],
        properties["r"],
    )
    assert full in saturated.compositions(properties["p"], suffix)
    assert suffix in saturated.compositions(properties["q"], singleton["r"])
    assert saturated.compositions_by_right(properties["p"])[suffix] == (full,)
    assert saturated.compositions_by_left(suffix)[properties["p"]] == (full,)
    assert transitive in saturated.compositions(properties["t"], singleton["t"])
    assert transitive not in saturated.compositions(properties["t"], transitive)
    assert saturated.reflexive_properties == (properties["p"],)
    assert saturated == saturate_properties(compiled)
    with pytest.raises(FrozenInstanceError):
        saturated.reflexive_properties = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        saturated.compositions_by_right(properties["p"])[suffix] = ()  # type: ignore[index]


def test_redundant_composition_split_matches_pinned_association_check() -> None:
    compiled = compile_ontology(
        load_functional(
            "SubObjectPropertyOf(:L :P) SubObjectPropertyOf(:L :A) "
            "SubObjectPropertyOf(:R :B) "
            "SubObjectPropertyOf(ObjectPropertyChain(:A :B) :P) "
            "SubObjectPropertyOf(:U :T) "
            "SubObjectPropertyOf(ObjectPropertyChain(:R :U) :T) "
            "SubObjectPropertyOf(ObjectPropertyChain(:P :T) :S)"
        )
    )
    saturated = saturate_properties(compiled)
    properties = {name: _property(compiled, name) for name in ("L", "P", "R", "U", "T")}
    right = saturated.lookup_chain((properties["R"], properties["U"]))
    result = saturated.lookup_chain((properties["P"], properties["T"]))
    assert right is not None and result is not None
    assert result in saturated.compositions(properties["L"], right, redundant=True)
    assert result not in saturated.compositions(properties["L"], right)


def test_property_inference_manifest_is_complete_and_resolves_python_symbols() -> None:
    with _MANIFEST.open("rb") as source:
        payload = tomllib.load(source)
    assert payload["schema"] == 1
    assert payload["elk_version"] == "0.6.0"
    assert payload["elk_commit"] == "b8ac5ce83db0704a7359d96aa382891e2f547863"
    concrete = {row["java_class"] for row in payload["inference"]}
    assert concrete == {
        "PropertyRangeInherited",
        "SubPropertyChainExpandedSubObjectPropertyOf",
        "SubPropertyChainTautology",
    }
    ignored = {row["java_class"] for row in payload["ignored"]}
    assert ignored == {
        "AbstractObjectPropertyInference",
        "AbstractPropertyRangeInference",
        "AbstractSubPropertyChainInference",
        "DummyObjectPropertyInferenceVisitor",
        "ObjectPropertyInference",
        "ObjectPropertyInferenceConclusionVisitor",
        "PropertyRangeInference",
        "SubPropertyChainInference",
        "SubPropertyChainInferenceConclusionVisitor",
    }
    assert concrete.isdisjoint(ignored)
    for row in payload["inference"]:
        assert row["status"] == "implemented"
        assert row["java_path"].endswith(f"/{row['java_class']}.java")
        module_name, symbol_name = row["python_rule"].rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module_name), symbol_name))
        test_path, test_name = row["unit_test"].split("::", 1)
        assert (_ROOT.parent / test_path).is_file()
        assert f"def {test_name}(" in (_ROOT.parent / test_path).read_text()


@pytest.mark.parametrize(
    "case_name",
    (
        "Ancestors",
        "Bottom",
        "ChainWithReflexive",
        "Cycle",
        "Equivalent",
        "Inconsistent",
        "InconsistentPropertyByDisjointClasses",
        "InconsistentPropertyByObjectComplementOf",
        "InconsistentPropertyByOwlNothing",
        "Top",
        "TopSubproperties",
    ),
)
def test_all_upstream_object_property_inputs_reach_a_valid_closure(case_name: str) -> None:
    view = owl.load_snapshot((_UPSTREAM / f"{case_name}.owl").read_bytes(), options=_LOAD_OPTIONS)
    compiled = compile_ontology(view)
    saturated = saturate_properties(compiled)
    assert len(saturated.compiled_chain_ids) == len(compiled.property_chains)
    assert (
        len(
            {
                conclusion.sub_chain
                for conclusion in saturated.subproperty_chains
                if conclusion.sub_chain == conclusion.super_chain
            }
        )
        == saturated.chain_count
    )


@pytest.mark.parametrize("case_name", ("Ancestors", "Cycle", "Equivalent"))
def test_named_hierarchy_reachability_matches_frozen_java_taxonomy(case_name: str) -> None:
    view = owl.load_snapshot((_UPSTREAM / f"{case_name}.owl").read_bytes(), options=_LOAD_OPTIONS)
    compiled = compile_ontology(view)
    saturated = saturate_properties(compiled)
    expected = json.loads((_EXPECTED / f"{case_name}.json").read_text())["result"]["value"]
    nodes: list[list[str]] = expected["nodes"]
    reachability = {(index, index) for index in range(len(nodes))}
    reachability.update(tuple(edge) for edge in expected["direct_edges"])
    changed = True
    while changed:
        changed = False
        additions = {
            (first, fourth)
            for first, second in reachability
            for third, fourth in reachability
            if second == third and (first, fourth) not in reachability
        }
        if additions:
            reachability.update(additions)
            changed = True

    iri_to_entity = {
        record.iri: EntityId(index)
        for index, record in enumerate(compiled.entities)
        if record.kind is EntityKind.OBJECT_PROPERTY
    }
    named_nodes = {
        iri: node_index
        for node_index, node in enumerate(nodes)
        for iri in node
        if iri.startswith("http://example.org/")
    }
    for sub_iri, sub_node in named_nodes.items():
        sub_chain = saturated.singleton_chain(iri_to_entity[sub_iri])
        actual_supers = set(saturated.super_chains(sub_chain))
        for super_iri, super_node in named_nodes.items():
            super_chain = saturated.singleton_chain(iri_to_entity[super_iri])
            assert (super_chain in actual_supers) is ((sub_node, super_node) in reachability)
