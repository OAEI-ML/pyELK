from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pyowl_core

import pyelk.owl as elk_owl


def test_every_structural_export_is_the_exact_core_object() -> None:
    assert elk_owl.__all__ == pyowl_core.model.__all__
    for name in elk_owl.__all__:
        assert getattr(elk_owl, name) is getattr(pyowl_core.model, name), name


def test_pyelk_owl_defines_no_runtime_model_or_compatibility_values() -> None:
    local_types = {
        name
        for name, value in vars(elk_owl).items()
        if isinstance(value, type) and value.__module__.startswith("pyelk")
    }
    assert local_types == set()
    for obsolete in (
        "ElkCompatibilityKey",
        "StructuralKey",
        "UnsupportedAxiom",
        "UnsupportedExpression",
        "UnsupportedNode",
    ):
        assert not hasattr(elk_owl, obsolete)
    module_files = sorted(Path(elk_owl.__file__).parent.glob("*.py"))
    assert [path.name for path in module_files] == ["__init__.py"]


def test_core_literal_identity_and_language_canonicalization_are_unchanged() -> None:
    upper = elk_owl.Literal("value", elk_owl.RDF_PLAIN_LITERAL, "EN-gb")
    lower = pyowl_core.Literal("value", pyowl_core.RDF_PLAIN_LITERAL, "en-GB")
    assert type(upper) is pyowl_core.Literal
    assert upper.language == "en-gb"
    assert upper == lower
    assert hash(upper) == hash(lower)


def test_import_has_no_parser_native_java_network_or_backend_side_effects() -> None:
    script = """
import sys
import pyowl_core

def forbidden(*args, **kwargs):
    raise AssertionError("ontology acquisition was invoked during pyelk.owl import")

pyowl_core.parse_document = forbidden
pyowl_core.load_snapshot = forbidden
pyowl_core.coerce_snapshot = forbidden
before = set(sys.modules)
import pyelk.owl
added = set(sys.modules) - before
for name in (
    "jpype",
    "pyelk._native",
    "pyelk.backends",
    "requests",
):
    assert name not in added, name
"""
    environment = dict(os.environ)
    roots = [str(Path(pyowl_core.__file__).parents[1]), str(Path(__file__).parents[3] / "src")]
    environment["PYTHONPATH"] = os.pathsep.join(roots)
    subprocess.run([sys.executable, "-c", script], check=True, env=environment)
