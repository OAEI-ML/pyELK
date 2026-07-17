from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

tomllib: Any = importlib.import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")

_TESTS = Path(__file__).resolve().parents[3]
_REPOSITORY = _TESTS.parent
_MANIFEST = _TESTS / "data" / "manifests" / "inferences.toml"

_CONCRETE = {
    "BackwardLinkComposition",
    "BackwardLinkOfObjectHasSelf",
    "BackwardLinkOfObjectSomeValuesFrom",
    "BackwardLinkReversedExpanded",
    "ClassInconsistencyOfDisjointSubsumers",
    "ClassInconsistencyOfObjectComplementOf",
    "ClassInconsistencyOfOwlNothing",
    "ClassInconsistencyPropagated",
    "ContextInitializationNoPremises",
    "DisjointSubsumerFromSubsumer",
    "ForwardLinkComposition",
    "ForwardLinkOfObjectHasSelf",
    "ForwardLinkOfObjectSomeValuesFrom",
    "PropagationGenerated",
    "SubClassInclusionComposedDefinedClass",
    "SubClassInclusionComposedObjectIntersectionOf",
    "SubClassInclusionComposedObjectSomeValuesFrom",
    "SubClassInclusionComposedObjectUnionOf",
    "SubClassInclusionComposedOfDecomposed",
    "SubClassInclusionDecomposedFirstConjunct",
    "SubClassInclusionDecomposedSecondConjunct",
    "SubClassInclusionExpandedDefinition",
    "SubClassInclusionExpandedFirstEquivalentClass",
    "SubClassInclusionExpandedSecondEquivalentClass",
    "SubClassInclusionExpandedSubClassOf",
    "SubClassInclusionObjectHasSelfPropertyRange",
    "SubClassInclusionOwlThing",
    "SubClassInclusionRange",
    "SubClassInclusionTautology",
    "SubContextInitializationNoPremises",
}

_IGNORED = {
    "AbstractBackwardLinkInference",
    "AbstractClassInconsistencyInference",
    "AbstractClassInconsistencyOfInconsistentSubsumerInference",
    "AbstractClassInference",
    "AbstractContextInitializationInference",
    "AbstractDisjointSubsumerInference",
    "AbstractForwardLinkInference",
    "AbstractPropagationInference",
    "AbstractSubClassInclusionComposedInference",
    "AbstractSubClassInclusionDecomposedInference",
    "AbstractSubClassInclusionExpansionInference",
    "AbstractSubClassInclusionInference",
    "AbstractSubClassInference",
    "AbstractSubContextInitializationInference",
    "BackwardLinkInference",
    "ClassInconsistencyInference",
    "ClassInference",
    "ClassInferenceConclusionVisitor",
    "ComposedClassInferenceVisitor",
    "ContextInitializationInference",
    "DisjointSubsumerInference",
    "DummyClassInferenceVisitor",
    "DummySaturationInferenceVisitor",
    "ForwardLinkInference",
    "InitializationInference",
    "LinkComposition",
    "PropagationInference",
    "SaturationInference",
    "SubClassInclusionComposedInference",
    "SubClassInclusionDecomposedConjunct",
    "SubClassInclusionDecomposedInference",
    "SubClassInclusionInference",
    "SubClassInference",
    "SubContextInitializationInference",
}


def test_class_inference_manifest_is_complete_and_resolves_symbols_and_tests() -> None:
    with _MANIFEST.open("rb") as source:
        payload = tomllib.load(source)
    assert payload["schema"] == 1
    assert payload["elk_version"] == "0.6.0"
    assert payload["elk_commit"] == "b8ac5ce83db0704a7359d96aa382891e2f547863"
    implemented = {row["java_class"] for row in payload["inference"]}
    ignored = {row["java_class"] for row in payload["ignored"]}
    assert implemented == _CONCRETE
    assert ignored == _IGNORED
    assert implemented.isdisjoint(ignored)
    assert len(implemented | ignored) == 64
    for row in payload["inference"]:
        assert row["status"] == "implemented"
        assert row["java_path"].endswith(f"/{row['java_class']}.java")
        module_name, symbol_name = row["python_rule"].rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module_name), symbol_name))
        test_path, test_name = row["unit_test"].split("::", 1)
        source_path = _REPOSITORY / test_path
        assert source_path.is_file()
        assert f"def {test_name}(" in source_path.read_text()
    assert all(row["reason"] for row in payload["ignored"])
