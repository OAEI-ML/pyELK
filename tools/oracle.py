#!/usr/bin/env python3
"""Synchronize and verify the pinned ELK 0.6.0 non-incremental test corpus.

This tool performs no Java work by default.  It copies the exact upstream resources
and licence from a hash-verified source checkout into the Java-free frozen test tree.
Java oracle regeneration is an explicit, separate ``java`` subcommand introduced by
the package-local harness under ``tools/java-oracle``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "data" / "elk-v0.6.0"
UPSTREAM_SUBTREE = Path("elk-reasoner/src/test/resources/test_input")
REPOSITORY = "https://github.com/liveontologies/elk-reasoner"
TAG = "v0.6.0"
COMMIT = "b8ac5ce83db0704a7359d96aa382891e2f547863"
TREE = "9becd9e41eac6434a1e247c2a9b19644cdd9d27a"
LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
FEATURE_MANIFEST = ROOT / "tests" / "data" / "manifests" / "features.toml"
FEATURE_FIXTURES = TARGET / "features"

_FEATURE_SOURCE = Path(
    "elk-reasoner/src/main/java/org/semanticweb/elk/reasoner/completeness/Feature.java"
)

_ONTOLOGY_FEATURE_AXIOMS = {
    "ANONYMOUS_INDIVIDUAL": "ClassAssertion(:A _:a)",
    "ASYMMETRIC_OBJECT_PROPERTY": "AsymmetricObjectProperty(:r)",
    "BOTTOM_OBJECT_PROPERTY_POSITIVE": (
        "SubClassOf(:A ObjectSomeValuesFrom(owl:bottomObjectProperty :B))"
    ),
    "DATA_ALL_VALUES_FROM": "SubClassOf(:A DataAllValuesFrom(:dp xsd:string))",
    "DATA_EXACT_CARDINALITY": ("SubClassOf(:A DataExactCardinality(1 :dp xsd:string))"),
    "DATA_HAS_VALUE": 'SubClassOf(:A DataHasValue(:dp "x"))',
    "DATA_MAX_CARDINALITY": "SubClassOf(:A DataMaxCardinality(1 :dp xsd:string))",
    "DATA_MIN_CARDINALITY": "SubClassOf(:A DataMinCardinality(1 :dp xsd:string))",
    "DATA_PROPERTY": "Declaration(DataProperty(:dp))",
    "DATA_PROPERTY_ASSERTION": 'DataPropertyAssertion(:dp :i "x")',
    "DATA_PROPERTY_DOMAIN": "DataPropertyDomain(:dp :A)",
    "DATA_PROPERTY_RANGE": "DataPropertyRange(:dp xsd:string)",
    "DATA_SOME_VALUES_FROM": "SubClassOf(:A DataSomeValuesFrom(:dp xsd:string))",
    "DATATYPE": "Declaration(Datatype(:D))",
    "DATATYPE_DEFINITION": "DatatypeDefinition(:D xsd:string)",
    "DIFFERENT_INDIVIDUALS": "DifferentIndividuals(:i :j)",
    "DISJOINT_CLASSES": "DisjointClasses(:A :B)",
    "DISJOINT_DATA_PROPERTIES": "DisjointDataProperties(:dp :dq)",
    "DISJOINT_OBJECT_PROPERTIES": "DisjointObjectProperties(:r :s)",
    "DISJOINT_UNION": "DisjointUnion(:A :B :C)",
    "EQUIVALENT_DATA_PROPERTIES": "EquivalentDataProperties(:dp :dq)",
    "FUNCTIONAL_DATA_PROPERTY": "FunctionalDataProperty(:dp)",
    "FUNCTIONAL_OBJECT_PROPERTY": "FunctionalObjectProperty(:r)",
    "HAS_KEY": "HasKey(:A (:r) (:dp))",
    "INVERSE_FUNCTIONAL_OBJECT_PROPERTY": "InverseFunctionalObjectProperty(:r)",
    "INVERSE_OBJECT_PROPERTIES": "InverseObjectProperties(:r :s)",
    "IRREFLEXIVE_OBJECT_PROPERTY": "IrreflexiveObjectProperty(:r)",
    "NEGATIVE_DATA_PROPERTY_ASSERTION": 'NegativeDataPropertyAssertion(:dp :i "x")',
    "NEGATIVE_OBJECT_PROPERTY_ASSERTION": "NegativeObjectPropertyAssertion(:r :i :j)",
    "OBJECT_ALL_VALUES_FROM": "SubClassOf(:A ObjectAllValuesFrom(:r :B))",
    "OBJECT_COMPLEMENT_OF_NEGATIVE": "SubClassOf(ObjectComplementOf(:A) :B)",
    "OBJECT_COMPLEMENT_OF_POSITIVE": "SubClassOf(:A ObjectComplementOf(:B))",
    "OBJECT_EXACT_CARDINALITY": ("SubClassOf(:A ObjectExactCardinality(1 :r :B))"),
    "OBJECT_HAS_SELF_NEGATIVE": "SubClassOf(ObjectHasSelf(:r) :A)",
    "OBJECT_HAS_VALUE_POSITIVE": "SubClassOf(:A ObjectHasValue(:r :i))",
    "OBJECT_INVERSE_OF": "SubClassOf(:A ObjectSomeValuesFrom(ObjectInverseOf(:r) :B))",
    "OBJECT_MAX_CARDINALITY": "SubClassOf(:A ObjectMaxCardinality(1 :r :B))",
    "OBJECT_MIN_CARDINALITY": "SubClassOf(:A ObjectMinCardinality(1 :r :B))",
    "OBJECT_ONE_OF": "SubClassOf(:A ObjectOneOf(:i))",
    "OBJECT_PROPERTY_ASSERTION": "ObjectPropertyAssertion(:r :i :j)",
    "OBJECT_PROPERTY_CHAIN": "SubObjectPropertyOf(ObjectPropertyChain(:r :s) :t)",
    "OBJECT_PROPERTY_RANGE": "ObjectPropertyRange(:r :A)",
    "OBJECT_UNION_OF_POSITIVE": "SubClassOf(:A ObjectUnionOf(:B :C))",
    "OWL_NOTHING_POSITIVE": "SubClassOf(:A owl:Nothing)",
    "REFLEXIVE_OBJECT_PROPERTY": "ReflexiveObjectProperty(:r)",
    "SUB_DATA_PROPERTY_OF": "SubDataPropertyOf(:dp :dq)",
    "SWRL_RULE": ("DLSafeRule(Body(ClassAtom(:A Variable(:x))) Head(ClassAtom(:B Variable(:x))))"),
    "SYMMETRIC_OBJECT_PROPERTY": "SymmetricObjectProperty(:r)",
    "TOP_OBJECT_PROPERTY_NEGATIVE": "SubObjectPropertyOf(owl:topObjectProperty :r)",
}

_QUERY_FEATURE_AXIOMS = {
    "QUERY_ANNOTATION_ASSERTION_AXIOM": 'AnnotationAssertion(rdfs:label :A "x")',
    "QUERY_ANNOTATION_PROPERTY_DOMAIN_AXIOM": "AnnotationPropertyDomain(:ap :A)",
    "QUERY_ANNOTATION_PROPERTY_RANGE_AXIOM": "AnnotationPropertyRange(:ap :A)",
    "QUERY_SUB_ANNOTATION_PROPERTY_OF_AXIOM": "SubAnnotationPropertyOf(:ap :aq)",
    "QUERY_DATA_PROPERTY_ASSERTION_AXIOM": 'DataPropertyAssertion(:dp :i "x")',
    "QUERY_NEGATIVE_DATA_PROPERTY_ASSERTION_AXIOM": ('NegativeDataPropertyAssertion(:dp :i "x")'),
    "QUERY_NEGATIVE_OBJECT_PROPERTY_ASSERTION_AXIOM": ("NegativeObjectPropertyAssertion(:r :i :j)"),
    "QUERY_DISJOINT_UNION_AXIOM": "DisjointUnion(:A :B :C)",
    "QUERY_DATA_PROPERTY_DOMAIN_AXIOM": "DataPropertyDomain(:dp :A)",
    "QUERY_DATA_PROPERTY_RANGE_AXIOM": "DataPropertyRange(:dp xsd:string)",
    "QUERY_DISJOINT_DATA_PROPERTIES_AXIOM": "DisjointDataProperties(:dp :dq)",
    "QUERY_EQUIVALENT_DATA_PROPERTIES_AXIOM": "EquivalentDataProperties(:dp :dq)",
    "QUERY_FUNCTIONAL_DATA_PROPERTY_AXIOM": "FunctionalDataProperty(:dp)",
    "QUERY_SUB_DATA_PROPERTY_OF_AXIOM": "SubDataPropertyOf(:dp :dq)",
    "QUERY_DATATYPE_DEFINITION_AXIOM": "DatatypeDefinition(:D xsd:string)",
    "QUERY_DECLARATION_AXIOM": "Declaration(Class(:A))",
    "QUERY_HAS_KEY_AXIOM": "HasKey(:A (:r) (:dp))",
    "QUERY_ASYMMETRIC_OBJECT_PROPERTY_AXIOM": "AsymmetricObjectProperty(:r)",
    "QUERY_DISJOINT_OBJECT_PROPERTIES_AXIOM": "DisjointObjectProperties(:r :s)",
    "QUERY_EQUIVALENT_OBJECT_PROPERTIES_AXIOM": "EquivalentObjectProperties(:r :s)",
    "QUERY_FUNCTIONAL_OBJECT_PROPERTY_AXIOM": "FunctionalObjectProperty(:r)",
    "QUERY_INVERSE_FUNCTIONAL_OBJECT_PROPERTY_AXIOM": ("InverseFunctionalObjectProperty(:r)"),
    "QUERY_INVERSE_OBJECT_PROPERTIES_AXIOM": "InverseObjectProperties(:r :s)",
    "QUERY_IRREFLEXIVE_OBJECT_PROPERTY_AXIOM": "IrreflexiveObjectProperty(:r)",
    "QUERY_OBJECT_PROPERTY_RANGE_AXIOM": "ObjectPropertyRange(:r :A)",
    "QUERY_REFLEXIVE_OBJECT_PROPERTY_AXIOM": "ReflexiveObjectProperty(:r)",
    "QUERY_SUB_OBJECT_PROPERTY_OF_AXIOM": "SubObjectPropertyOf(:r :s)",
    "QUERY_SYMMETRIC_OBJECT_PROPERTY_AXIOM": "SymmetricObjectProperty(:r)",
    "QUERY_TRANSITIVE_OBJECT_PROPERTY_AXIOM": "TransitiveObjectProperty(:r)",
    "QUERY_SWRL_RULE": (
        "DLSafeRule(Body(ClassAtom(:A Variable(:x))) Head(ClassAtom(:B Variable(:x))))"
    ),
}

_GENERAL_INCOMPLETE = {
    "ANONYMOUS_INDIVIDUAL",
    "ASYMMETRIC_OBJECT_PROPERTY",
    "BOTTOM_OBJECT_PROPERTY_POSITIVE",
    "DATA_ALL_VALUES_FROM",
    "DATA_EXACT_CARDINALITY",
    "DATA_HAS_VALUE",
    "DATA_MAX_CARDINALITY",
    "DATA_MIN_CARDINALITY",
    "DATA_PROPERTY",
    "DATA_PROPERTY_ASSERTION",
    "DATA_PROPERTY_DOMAIN",
    "DATA_PROPERTY_RANGE",
    "DATA_SOME_VALUES_FROM",
    "DATATYPE",
    "DATATYPE_DEFINITION",
    "DISJOINT_DATA_PROPERTIES",
    "DISJOINT_OBJECT_PROPERTIES",
    "DISJOINT_UNION",
    "EQUIVALENT_DATA_PROPERTIES",
    "FUNCTIONAL_DATA_PROPERTY",
    "FUNCTIONAL_OBJECT_PROPERTY",
    "HAS_KEY",
    "INVERSE_FUNCTIONAL_OBJECT_PROPERTY",
    "INVERSE_OBJECT_PROPERTIES",
    "IRREFLEXIVE_OBJECT_PROPERTY",
    "NEGATIVE_DATA_PROPERTY_ASSERTION",
    "NEGATIVE_OBJECT_PROPERTY_ASSERTION",
    "OBJECT_ALL_VALUES_FROM",
    "OBJECT_COMPLEMENT_OF_NEGATIVE",
    "OBJECT_EXACT_CARDINALITY",
    "OBJECT_HAS_SELF_NEGATIVE",
    "OBJECT_INVERSE_OF",
    "OBJECT_MAX_CARDINALITY",
    "OBJECT_MIN_CARDINALITY",
    "OBJECT_ONE_OF",
    "OBJECT_UNION_OF_POSITIVE",
    "SUB_DATA_PROPERTY_OF",
    "SWRL_RULE",
    "SYMMETRIC_OBJECT_PROPERTY",
    "TOP_OBJECT_PROPERTY_NEGATIVE",
}

_OBJECT_PROPERTY_INCOMPLETE = {
    "DISJOINT_CLASSES",
    "OBJECT_COMPLEMENT_OF_POSITIVE",
    "OWL_NOTHING_POSITIVE",
}

_IGNORED_FEATURES = {
    "ANONYMOUS_INDIVIDUAL",
    "ASYMMETRIC_OBJECT_PROPERTY",
    "DATA_ALL_VALUES_FROM",
    "DATA_EXACT_CARDINALITY",
    "DATA_MAX_CARDINALITY",
    "DATA_MIN_CARDINALITY",
    "DATA_PROPERTY",
    "DATA_PROPERTY_ASSERTION",
    "DATA_PROPERTY_DOMAIN",
    "DATA_PROPERTY_RANGE",
    "DATA_SOME_VALUES_FROM",
    "DATATYPE",
    "DATATYPE_DEFINITION",
    "DISJOINT_DATA_PROPERTIES",
    "DISJOINT_OBJECT_PROPERTIES",
    "EQUIVALENT_DATA_PROPERTIES",
    "FUNCTIONAL_DATA_PROPERTY",
    "FUNCTIONAL_OBJECT_PROPERTY",
    "HAS_KEY",
    "INVERSE_FUNCTIONAL_OBJECT_PROPERTY",
    "INVERSE_OBJECT_PROPERTIES",
    "IRREFLEXIVE_OBJECT_PROPERTY",
    "NEGATIVE_DATA_PROPERTY_ASSERTION",
    "NEGATIVE_OBJECT_PROPERTY_ASSERTION",
    "OBJECT_ALL_VALUES_FROM",
    "OBJECT_EXACT_CARDINALITY",
    "OBJECT_INVERSE_OF",
    "OBJECT_MAX_CARDINALITY",
    "OBJECT_MIN_CARDINALITY",
    "SUB_DATA_PROPERTY_OF",
    "SWRL_RULE",
    "SYMMETRIC_OBJECT_PROPERTY",
}

_PARTIAL_FEATURES = {
    "BOTTOM_OBJECT_PROPERTY_POSITIVE",
    "DATA_HAS_VALUE",
    "DISJOINT_UNION",
    "OBJECT_COMPLEMENT_OF_NEGATIVE",
    "OBJECT_HAS_SELF_NEGATIVE",
    "OBJECT_HAS_VALUE_POSITIVE",
    "OBJECT_ONE_OF",
    "OBJECT_PROPERTY_ASSERTION",
    "OBJECT_PROPERTY_RANGE",
    "OBJECT_UNION_OF_POSITIVE",
    "REFLEXIVE_OBJECT_PROPERTY",
    "TOP_OBJECT_PROPERTY_NEGATIVE",
}

_ALL_TASKS = [
    "consistency",
    "class_taxonomy",
    "object_property_taxonomy",
    "realization",
    "class_expression_query",
    "entailment_query",
]

_FEATURE_CONDITIONS = {
    "OBJECT_PROPERTY_ASSERTION": ["all_tasks:OBJECT_PROPERTY_RANGE"],
    "OBJECT_HAS_VALUE_POSITIVE": ["all_tasks:OBJECT_PROPERTY_RANGE"],
    "OBJECT_PROPERTY_RANGE": [
        "all_tasks:OBJECT_PROPERTY_ASSERTION",
        "all_tasks:OBJECT_HAS_VALUE_POSITIVE",
    ],
    "OBJECT_PROPERTY_CHAIN": ["object_property_taxonomy:REFLEXIVE_OBJECT_PROPERTY"],
    "REFLEXIVE_OBJECT_PROPERTY": ["object_property_taxonomy:OBJECT_PROPERTY_CHAIN"],
}

_FIXTURE_PREFIXES = """Prefix(:=<http://example.org/>)
Prefix(owl:=<http://www.w3.org/2002/07/owl#>)
Prefix(rdfs:=<http://www.w3.org/2000/01/rdf-schema#>)
Prefix(xsd:=<http://www.w3.org/2001/XMLSchema#>)
"""

_EXPECTED_COUNTS = {
    "files": 262,
    "ontology_inputs": 124,
    "golden_outputs": 138,
    "class_classification_inputs": 66,
    "object_property_classification_inputs": 11,
    "class_query_inputs": 26,
    "entailment_inputs": 16,
    "realization_inputs": 5,
}

_NOTICE = """# ELK 0.6.0 frozen test resources

The files below `upstream/` are unmodified copies of
`elk-reasoner/src/test/resources/test_input` from ELK 0.6.0, commit
`b8ac5ce83db0704a7359d96aa382891e2f547863`.

ELK is Copyright 2011-2024 the ELK contributors and is licensed under the
Apache License, Version 2.0. The exact upstream licence is retained as
`LICENSE.txt`. `manifest.json` records every source path and SHA-256 digest.

Repository: https://github.com/liveontologies/elk-reasoner
Release: https://github.com/liveontologies/elk-reasoner/releases/tag/v0.6.0
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(source: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"cannot inspect ELK source checkout: {error}") from error
    return completed.stdout.strip()


def _verify_source(source: Path) -> Path:
    commit = _git(source, "rev-parse", "HEAD")
    tree = _git(source, "rev-parse", "HEAD^{tree}")
    if commit != COMMIT or tree != TREE:
        raise RuntimeError(f"ELK checkout mismatch: expected {COMMIT}/{TREE}, got {commit}/{tree}")
    resources = source / UPSTREAM_SUBTREE
    if not resources.is_dir():
        raise RuntimeError(f"ELK resource tree does not exist: {resources}")
    licence = source / "LICENSE.txt"
    if _sha256(licence) != LICENSE_SHA256:
        raise RuntimeError("ELK LICENSE.txt does not match the pinned digest")
    return resources


def _family(relative: Path) -> str:
    parts = relative.parts
    if parts[:2] == ("classification", "object_property"):
        return "object_property_classification"
    if parts[0] == "classification":
        return "class_classification"
    if parts[:2] == ("query", "class"):
        return "class_query"
    if parts[:2] == ("query", "entailment"):
        return "entailment"
    if parts[0] == "realization":
        return "realization"
    raise RuntimeError(f"unknown upstream test family: {relative}")


def _entries(resources: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(item for item in resources.rglob("*") if item.is_file()):
        relative = path.relative_to(resources)
        role = "ontology" if path.suffix == ".owl" else "upstream_golden"
        result.append(
            {
                "bytes": path.stat().st_size,
                "family": _family(relative),
                "path": relative.as_posix(),
                "role": role,
                "sha256": _sha256(path),
                "upstream_path": (UPSTREAM_SUBTREE / relative).as_posix(),
            }
        )
    return result


def _summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    roles = Counter(entry["role"] for entry in entries)
    ontology_families = Counter(entry["family"] for entry in entries if entry["role"] == "ontology")
    return {
        "files": len(entries),
        "ontology_inputs": roles["ontology"],
        "golden_outputs": roles["upstream_golden"],
        "class_classification_inputs": ontology_families["class_classification"],
        "object_property_classification_inputs": ontology_families[
            "object_property_classification"
        ],
        "class_query_inputs": ontology_families["class_query"],
        "entailment_inputs": ontology_families["entailment"],
        "realization_inputs": ontology_families["realization"],
    }


def _manifest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _summary(entries)
    if summary != _EXPECTED_COUNTS:
        raise RuntimeError(
            f"pinned corpus inventory changed: expected {_EXPECTED_COUNTS}, got {summary}"
        )
    return {
        "schema": "pyelk.elk-frozen-corpus/1",
        "generated_by": "tools/oracle.py sync",
        "source": {
            "commit": COMMIT,
            "license": "Apache-2.0",
            "license_sha256": LICENSE_SHA256,
            "release": "0.6.0",
            "repository": REPOSITORY,
            "source_path": UPSTREAM_SUBTREE.as_posix(),
            "tag": TAG,
            "tree": TREE,
        },
        "summary": summary,
        "files": entries,
    }


def _render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _build_stage(source: Path, parent: Path) -> Path:
    resources = _verify_source(source)
    entries = _entries(resources)
    stage = parent / TARGET.name
    shutil.copytree(resources, stage / "upstream", copy_function=shutil.copyfile)
    shutil.copyfile(source / "LICENSE.txt", stage / "LICENSE.txt")
    (stage / "NOTICE.md").write_text(_NOTICE, encoding="utf-8")
    (stage / "manifest.json").write_text(_render(_manifest(entries)), encoding="utf-8")
    return stage


def _managed_tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    managed = [path / "LICENSE.txt", path / "NOTICE.md", path / "manifest.json"]
    managed.extend(value for value in (path / "upstream").rglob("*") if value.is_file())
    for item in sorted(managed):
        if not item.is_file():
            return "missing"
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def sync(source: Path, *, check: bool) -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="elk-corpus-", dir=TARGET.parent) as temporary:
        stage = _build_stage(source.resolve(), Path(temporary))
        if TARGET.exists() and _managed_tree_digest(TARGET) == _managed_tree_digest(stage):
            return
        if check:
            raise RuntimeError("frozen ELK corpus differs from the pinned source checkout")
        TARGET.mkdir(parents=True, exist_ok=True)
        upstream = TARGET / "upstream"
        backup = TARGET / ".upstream.backup"
        if backup.exists():
            shutil.rmtree(backup)
        if upstream.exists():
            os.replace(upstream, backup)
        try:
            os.replace(stage / "upstream", upstream)
            for name in ("LICENSE.txt", "NOTICE.md", "manifest.json"):
                os.replace(stage / name, TARGET / name)
        except BaseException:
            if backup.exists() and not upstream.exists():
                os.replace(backup, upstream)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def verify() -> None:
    manifest_path = TARGET / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read frozen corpus manifest: {error}") from error
    if payload.get("schema") != "pyelk.elk-frozen-corpus/1":
        raise RuntimeError("unknown frozen corpus manifest schema")
    if payload.get("source", {}).get("commit") != COMMIT:
        raise RuntimeError("frozen corpus manifest has the wrong ELK commit")
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise RuntimeError("frozen corpus manifest has no file inventory")
    if payload.get("summary") != _EXPECTED_COUNTS or _summary(entries) != _EXPECTED_COUNTS:
        raise RuntimeError("frozen corpus manifest counts do not match the pinned inventory")
    expected_paths: set[str] = set()
    for entry in entries:
        relative = entry["path"]
        if relative in expected_paths:
            raise RuntimeError(f"duplicate frozen corpus path: {relative}")
        expected_paths.add(relative)
        path = TARGET / "upstream" / relative
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise RuntimeError(f"missing or size-mismatched frozen resource: {relative}")
        if _sha256(path) != entry["sha256"]:
            raise RuntimeError(f"hash-mismatched frozen resource: {relative}")
    actual_paths = {
        path.relative_to(TARGET / "upstream").as_posix()
        for path in (TARGET / "upstream").rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise RuntimeError("frozen corpus contains unmanifested or missing resources")
    if _sha256(TARGET / "LICENSE.txt") != LICENSE_SHA256:
        raise RuntimeError("frozen ELK licence digest does not match")


def _parse_java_features(source: Path) -> list[tuple[str, str, str]]:
    feature_java = source / _FEATURE_SOURCE
    text = feature_java.read_text(encoding="utf-8")
    enum_start = text.index("public enum Feature")
    enum_end = text.index("public static enum Polarity", enum_start)
    declarations = text[enum_start:enum_end]
    pattern = re.compile(
        r"^\s*([A-Z][A-Z0-9_]+)\s*\(\s*\"([^\"]+)\"\s*"
        r"(?:,\s*Polarity\.(ANY|NEGATIVE|POSITIVE))?\s*\)\s*[,;]",
        re.MULTILINE,
    )
    result = [
        (name, constructor, polarity or "ANY")
        for name, constructor, polarity in pattern.findall(declarations)
    ]
    if len(result) != 79:
        raise RuntimeError(f"expected 79 pinned Java features, parsed {len(result)}")
    names = {name for name, _, _ in result}
    expected = set(_ONTOLOGY_FEATURE_AXIOMS) | set(_QUERY_FEATURE_AXIOMS)
    if names != expected:
        raise RuntimeError(
            "feature fixture inventory differs from Feature.java: "
            f"missing={sorted(names - expected)}, extra={sorted(expected - names)}"
        )
    return result


def _feature_fixture(name: str, axiom: str) -> str:
    ontology_iri = f"http://example.org/feature/{name}"
    return f"{_FIXTURE_PREFIXES}Ontology(<{ontology_iri}>\n  {axiom}\n)\n"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _feature_action(name: str) -> str:
    if name.startswith("QUERY_") or name in _IGNORED_FEATURES:
        return "ignore"
    if name in _PARTIAL_FEATURES:
        return "partial"
    return "complete"


def _affected_tasks(name: str) -> list[str]:
    if name.startswith("QUERY_"):
        return ["entailment_query"]
    if name in _GENERAL_INCOMPLETE:
        return _ALL_TASKS
    if name in _OBJECT_PROPERTY_INCOMPLETE:
        return ["object_property_taxonomy"]
    conditions = _FEATURE_CONDITIONS.get(name, [])
    if any(condition.startswith("all_tasks:") for condition in conditions):
        return _ALL_TASKS
    if conditions:
        return ["object_property_taxonomy"]
    return []


def _test_pointer(name: str) -> str:
    base = "tests/unit/reasoning/test_completeness.py::"
    if name.startswith("QUERY_"):
        return base + "test_every_unsupported_query_feature_is_reported"
    if name in _GENERAL_INCOMPLETE:
        return base + "test_every_general_single_feature_has_positive_and_negative_case"
    if name in _OBJECT_PROPERTY_INCOMPLETE:
        return base + "test_object_property_special_single_feature_is_task_local"
    return base + "test_unaffected_single_features_do_not_trigger_general_monitor"


def _render_feature_manifest(
    source: Path,
    features: list[tuple[str, str, str]],
    fixture_hashes: dict[str, str],
) -> str:
    java_path = source / _FEATURE_SOURCE
    lines = [
        'schema = "pyelk.elk-feature-manifest/1"',
        f'source_repository = "{REPOSITORY}"',
        f'source_tag = "{TAG}"',
        f'source_commit = "{COMMIT}"',
        f'source_tree = "{TREE}"',
        f'source_path = "{_FEATURE_SOURCE.as_posix()}"',
        f'feature_java_sha256 = "{_sha256(java_path)}"',
        "feature_count = 79",
        "ontology_feature_count = 49",
        "query_feature_count = 30",
        "",
    ]
    for index, (name, constructor, polarity) in enumerate(features):
        query = name.startswith("QUERY_")
        relative = f"features/{'query' if query else 'ontology'}/{name}.ofn"
        expected_issue = query or name in _GENERAL_INCOMPLETE or name in _OBJECT_PROPERTY_INCOMPLETE
        lines.extend(
            [
                "[[features]]",
                f"index = {index}",
                f"name = {_toml_string(name)}",
                f"constructor = {_toml_string(constructor)}",
                f"polarity = {_toml_string(polarity)}",
                f"scope = {_toml_string('query' if query else 'ontology')}",
                f"index_action = {_toml_string(_feature_action(name))}",
                f"affected_tasks = {_toml_array(_affected_tasks(name))}",
                f"conditions = {_toml_array(_FEATURE_CONDITIONS.get(name, []))}",
                f"fixture = {_toml_string('tests/data/elk-v0.6.0/' + relative)}",
                f"fixture_sha256 = {_toml_string(fixture_hashes[name])}",
                f"test = {_toml_string(_test_pointer(name))}",
                "expected_count = 1",
                f"expected_issue = {'true' if expected_issue else 'false'}",
            ]
        )
        if query:
            lines.append("expected_value = false")
        lines.append("")
    return "\n".join(lines)


def sync_features(source: Path, *, check: bool) -> None:
    source = source.resolve()
    _verify_source(source)
    features = _parse_java_features(source)
    FEATURE_FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    FEATURE_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="elk-features-", dir=FEATURE_FIXTURES.parent
    ) as temporary:
        stage = Path(temporary) / "features"
        fixture_hashes: dict[str, str] = {}
        for name, _, _ in features:
            query = name.startswith("QUERY_")
            axiom = _QUERY_FEATURE_AXIOMS[name] if query else _ONTOLOGY_FEATURE_AXIOMS[name]
            path = stage / ("query" if query else "ontology") / f"{name}.ofn"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_feature_fixture(name, axiom), encoding="utf-8")
            fixture_hashes[name] = _sha256(path)
        manifest_text = _render_feature_manifest(source, features, fixture_hashes)
        stage_manifest = Path(temporary) / "features.toml"
        stage_manifest.write_text(manifest_text, encoding="utf-8")
        fixtures_match = FEATURE_FIXTURES.is_dir() and _tree_digest_for_all_files(
            FEATURE_FIXTURES
        ) == _tree_digest_for_all_files(stage)
        manifest_matches = (
            FEATURE_MANIFEST.is_file()
            and FEATURE_MANIFEST.read_text(encoding="utf-8") == manifest_text
        )
        if fixtures_match and manifest_matches:
            return
        if check:
            raise RuntimeError("feature manifest or fixtures differ from pinned Feature.java")
        backup = FEATURE_FIXTURES.with_name(".features.backup")
        if backup.exists():
            shutil.rmtree(backup)
        if FEATURE_FIXTURES.exists():
            os.replace(FEATURE_FIXTURES, backup)
        try:
            os.replace(stage, FEATURE_FIXTURES)
            os.replace(stage_manifest, FEATURE_MANIFEST)
        except BaseException:
            if backup.exists() and not FEATURE_FIXTURES.exists():
                os.replace(backup, FEATURE_FIXTURES)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def _tree_digest_for_all_files(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync", help="copy the pinned upstream corpus")
    sync_parser.add_argument("--source", required=True, type=Path)
    sync_parser.add_argument("--check", action="store_true")
    feature_parser = subparsers.add_parser(
        "features", help="regenerate Feature.java manifest and minimal fixtures"
    )
    feature_parser.add_argument("--source", required=True, type=Path)
    feature_parser.add_argument("--check", action="store_true")
    subparsers.add_parser("verify", help="verify the committed Java-free corpus")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "sync":
            sync(arguments.source, check=arguments.check)
        elif arguments.command == "features":
            sync_features(arguments.source, check=arguments.check)
        else:
            verify()
    except RuntimeError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
