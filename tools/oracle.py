#!/usr/bin/env python3
"""Synchronize and verify the pinned ELK 0.6.0 non-incremental test corpus.

This tool performs no Java work by default. It copies the exact upstream resources
and licence from a hash-verified source checkout into the Java-free frozen test tree.
Java oracle regeneration is the explicit ``regenerate`` subcommand backed by the
quarantined Maven application under ``tools/java-oracle``.
"""

from __future__ import annotations

import argparse
import copy
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
EXPECTED = TARGET / "expected"
ORACLE_MANIFEST = TARGET / "oracle-manifest.json"
ORACLE_REPORT = TARGET / "oracle-report.json"
JAVA_ORACLE = ROOT / "tools" / "java-oracle"
ORACLE_JAR = JAVA_ORACLE / "target" / "pyelk-elk-reference-oracle.jar"
DEFAULT_JAVA_HOME = Path("/private/tmp/exact-owl-toolchains/temurin17/jdk-17.0.19+10/Contents/Home")
DEFAULT_MAVEN = Path("/private/tmp/exact-owl-toolchains/maven/3.9.16/libexec/bin/mvn")
DEFAULT_MAVEN_REPOSITORY = Path("/private/tmp/pyelk-oracle-m2")
JAVA_ARCHIVE_SHA256 = "03632d1fbf139ab3719a9f4b47dc206251449b87557143c822336dbf8c06560f"
JAVA_EXECUTABLE_SHA256 = "d460b16235f81cc00a18e45e32e0d700500cc2cf21b7f26580fd1266360fb8b8"
MAVEN_EXECUTABLE_SHA256 = "235e67d7e6b46c491f01d9a441b14b39a1fc05353d69fb529c874475bcbdaf37"

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


def verify(*, require_oracle: bool = True) -> None:
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
    provenance = TARGET / "UPSTREAM.toml"
    notice = TARGET / "NOTICE.pyelk"
    if not provenance.is_file() or COMMIT not in provenance.read_text(encoding="utf-8"):
        raise RuntimeError("frozen ELK corpus has no exact UPSTREAM.toml provenance")
    if not notice.is_file():
        raise RuntimeError("frozen ELK corpus has no pyELK modification notice")
    if require_oracle:
        _verify_oracle_frozen_data()


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


def _feature_entries() -> list[dict[str, Any]]:
    """Read the generated TOML subset without adding a Python 3.10 dependency."""

    try:
        text = FEATURE_MANIFEST.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"cannot read feature manifest: {error}") from error
    result: list[dict[str, Any]] = []
    for block in text.split("[[features]]")[1:]:
        entry: dict[str, Any] = {}
        for key in (
            "name",
            "constructor",
            "polarity",
            "scope",
            "fixture",
            "fixture_sha256",
        ):
            match = re.search(rf"^{key}\s*=\s*(\".*\")$", block, re.MULTILINE)
            if match is None:
                raise RuntimeError(f"feature manifest entry has no {key}")
            entry[key] = json.loads(match.group(1))
        for key in ("index", "expected_count"):
            match = re.search(rf"^{key}\s*=\s*(\d+)$", block, re.MULTILINE)
            if match is None:
                raise RuntimeError(f"feature manifest entry has no {key}")
            entry[key] = int(match.group(1))
        issue = re.search(r"^expected_issue\s*=\s*(true|false)$", block, re.MULTILINE)
        if issue is None:
            raise RuntimeError("feature manifest entry has no expected_issue")
        entry["expected_issue"] = issue.group(1) == "true"
        result.append(entry)
    if len(result) != 79 or [entry["index"] for entry in result] != list(range(79)):
        raise RuntimeError("feature manifest does not contain the pinned 79-entry order")
    return result


def _issue_payload(
    task: str, names: tuple[str, ...], metadata: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "constructors": [metadata[name]["constructor"] for name in names],
        "features": list(names),
        "polarities": [metadata[name]["polarity"] for name in names],
        "task": task,
    }


def _issues_for_counts(
    task: str,
    ontology_counts: dict[str, int],
    metadata: dict[str, dict[str, Any]],
    *,
    query_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    order = [entry["name"] for entry in sorted(metadata.values(), key=lambda item: item["index"])]
    issues: list[dict[str, Any]] = []

    def add_single(name: str) -> None:
        issues.append(_issue_payload(task, (name,), metadata))

    for name in order:
        if name in _GENERAL_INCOMPLETE and ontology_counts.get(name, 0) > 0:
            add_single(name)
    for combination in (
        ("OBJECT_PROPERTY_RANGE", "OBJECT_PROPERTY_ASSERTION"),
        ("OBJECT_PROPERTY_RANGE", "OBJECT_HAS_VALUE_POSITIVE"),
    ):
        if all(ontology_counts.get(name, 0) > 0 for name in combination):
            issues.append(_issue_payload(task, combination, metadata))
    if task == "object_property_taxonomy":
        for name in order:
            if name in _OBJECT_PROPERTY_INCOMPLETE and ontology_counts.get(name, 0) > 0:
                add_single(name)
        combination = ("REFLEXIVE_OBJECT_PROPERTY", "OBJECT_PROPERTY_CHAIN")
        if all(ontology_counts.get(name, 0) > 0 for name in combination):
            issues.append(_issue_payload(task, combination, metadata))
    if query_counts is not None:
        for name in order:
            if name.startswith("QUERY_") and query_counts.get(name, 0) > 0:
                add_single(name)
        combined = Counter(ontology_counts)
        combined.update(query_counts)
        for name in order:
            if name in _GENERAL_INCOMPLETE and combined.get(name, 0) > 0:
                issue = _issue_payload(task, (name,), metadata)
                if issue not in issues:
                    issues.append(issue)
        for combination in (
            ("OBJECT_PROPERTY_RANGE", "OBJECT_PROPERTY_ASSERTION"),
            ("OBJECT_PROPERTY_RANGE", "OBJECT_HAS_VALUE_POSITIVE"),
        ):
            if all(combined.get(name, 0) > 0 for name in combination):
                issue = _issue_payload(task, combination, metadata)
                if issue not in issues:
                    issues.append(issue)
    return sorted(
        issues,
        key=lambda issue: (
            issue["task"],
            tuple(issue["features"]),
            tuple(issue["constructors"]),
            tuple(issue["polarities"]),
        ),
    )


def _oracle_source_digest() -> str:
    digest = hashlib.sha256()
    files = [
        path
        for path in JAVA_ORACLE.rglob("*")
        if path.is_file() and "target" not in path.relative_to(JAVA_ORACLE).parts
    ]
    for path in sorted(files, key=lambda candidate: candidate.relative_to(JAVA_ORACLE).as_posix()):
        relative = path.relative_to(JAVA_ORACLE).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _verify_toolchain(java_home: Path, maven: Path) -> tuple[Path, dict[str, Any]]:
    java = java_home / "bin" / "java"
    if not java.is_file() or _sha256(java) != JAVA_EXECUTABLE_SHA256:
        raise RuntimeError("Temurin 17 executable is missing or has the wrong digest")
    if not maven.is_file() or _sha256(maven) != MAVEN_EXECUTABLE_SHA256:
        raise RuntimeError("Maven 3.9.16 executable is missing or has the wrong digest")
    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(java_home)
    try:
        java_identity = subprocess.run(
            [str(java), "-version"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        maven_identity = subprocess.run(
            [str(maven), "-version"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"cannot verify pinned Java toolchain: {error}") from error
    java_text = java_identity.stdout + java_identity.stderr
    maven_text = maven_identity.stdout + maven_identity.stderr
    if 'version "17.0.19"' not in java_text or "Temurin-17.0.19+10" not in java_text:
        raise RuntimeError("Java runtime is not the pinned Temurin 17.0.19+10 build")
    if "Apache Maven 3.9.16" not in maven_text or "Java version: 17.0.19" not in maven_text:
        raise RuntimeError("Maven is not the pinned 3.9.16/Temurin 17 toolchain")
    return java, {
        "java_archive_sha256": JAVA_ARCHIVE_SHA256,
        "java_executable_sha256": JAVA_EXECUTABLE_SHA256,
        "java_runtime": "Eclipse Temurin 17.0.19+10",
        "maven_executable_sha256": MAVEN_EXECUTABLE_SHA256,
        "maven_runtime": "Apache Maven 3.9.16",
    }


def _build_oracle(java_home: Path, maven: Path, repository: Path) -> Path:
    repository.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(java_home)
    try:
        completed = subprocess.run(
            [
                str(maven),
                "--quiet",
                f"-Dmaven.repo.local={repository}",
                "-DskipTests",
                "package",
            ],
            cwd=JAVA_ORACLE,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        details = getattr(error, "stderr", "")
        raise RuntimeError(f"cannot build pinned Java oracle: {details or error}") from error
    if completed.stderr and "[ERROR]" in completed.stderr:
        raise RuntimeError("Maven reported an error while building the Java oracle")
    if not ORACLE_JAR.is_file():
        raise RuntimeError("Maven completed without producing the quarantined oracle JAR")
    return ORACLE_JAR


def _corpus_cases() -> list[dict[str, Any]]:
    payload = json.loads((TARGET / "manifest.json").read_text(encoding="utf-8"))
    entries = payload["files"]
    goldens: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        if entry["role"] != "upstream_golden":
            continue
        relative = Path(entry["path"])
        key = (entry["family"], relative.with_suffix("").as_posix())
        goldens.setdefault(key, []).append(entry)
    cases: list[dict[str, Any]] = []
    for entry in entries:
        if entry["role"] != "ontology":
            continue
        relative = Path(entry["path"])
        key = (entry["family"], relative.with_suffix("").as_posix())
        case_goldens = sorted(goldens.get(key, []), key=lambda item: item["path"])
        if not case_goldens:
            raise RuntimeError(f"frozen ontology has no upstream golden: {relative}")
        cases.append({"ontology": entry, "goldens": case_goldens})
    if len(cases) != 124 or sum(len(case["goldens"]) for case in cases) != 138:
        raise RuntimeError("frozen corpus no longer contains the pinned 124/138 inventory")
    return cases


def _oracle_requests() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    configuration = {
        "allow_fresh_entities": True,
        "incremental": False,
        "unsupported": "ignore",
        "workers": 1,
    }
    requests: list[dict[str, Any]] = [
        {
            "arguments": {},
            "configuration": configuration,
            "id": "identity",
            "operation": "identity",
            "schema": 1,
        }
    ]
    metadata: list[dict[str, Any]] = [{"kind": "identity"}]
    operations = {
        "class_classification": ("class_taxonomy", "class_taxonomy"),
        "object_property_classification": (
            "object_property_taxonomy",
            "object_property_taxonomy",
        ),
        "realization": ("realization", "realization"),
        "class_query": ("class_queries", "class_expression_query"),
        "entailment": ("entailment", "entailment_query"),
    }
    for case in _corpus_cases():
        ontology = case["ontology"]
        relative = Path(ontology["path"])
        operation, task = operations[ontology["family"]]
        golden_paths = [TARGET / "upstream" / entry["path"] for entry in case["goldens"]]
        arguments: dict[str, Any]
        if operation == "entailment":
            arguments = {"query_paths": [str(path.resolve()) for path in golden_paths]}
        else:
            if len(golden_paths) != 1:
                raise RuntimeError(f"{relative} has an invalid golden inventory")
            arguments = {"golden_path": str(golden_paths[0].resolve())}
        request = {
            "arguments": arguments,
            "configuration": configuration,
            "id": f"corpus:{relative.with_suffix('').as_posix()}",
            "ontology_path": str((TARGET / "upstream" / relative).resolve()),
            "operation": operation,
            "schema": 1,
        }
        requests.append(request)
        metadata.append(
            {
                "expected": relative.with_suffix(".json"),
                "family": ontology["family"],
                "goldens": case["goldens"],
                "kind": "corpus",
                "ontology": ontology,
                "operation": operation,
                "task": task,
            }
        )
    for entry in _feature_entries():
        fixture = ROOT / entry["fixture"]
        operation = "query_feature_counts" if entry["scope"] == "query" else "feature_counts"
        requests.append(
            {
                "arguments": {},
                "configuration": configuration,
                "id": f"feature:{entry['name']}",
                "ontology_path": str(fixture.resolve()),
                "operation": operation,
                "schema": 1,
            }
        )
        metadata.append(
            {
                "entry": entry,
                "expected": Path("features") / entry["scope"] / f"{entry['name']}.json",
                "kind": "feature",
                "operation": operation,
            }
        )
    return requests, metadata


def _run_oracle(java: Path, jar: Path, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    input_text = "".join(
        json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n" for request in requests
    )
    try:
        completed = subprocess.run(
            [str(java), "-jar", str(jar)],
            input=input_text,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        details = getattr(error, "stderr", "")
        raise RuntimeError(f"pinned Java oracle failed: {details or error}") from error
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != len(requests):
        raise RuntimeError(
            f"oracle response count mismatch: expected {len(requests)}, got {len(lines)}; "
            f"first output={lines[:3]!r}"
        )
    responses: list[dict[str, Any]] = []
    for request, line in zip(requests, lines, strict=True):
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"oracle produced non-JSON output: {line[:120]}") from error
        if response.get("schema") != 1 or response.get("id") != request["id"]:
            raise RuntimeError(f"oracle response does not match request {request['id']}")
        if response.get("ok") is not True:
            category = response.get("error", {}).get("category", "unknown")
            raise RuntimeError(f"oracle request {request['id']} failed with {category}")
        responses.append(response)
    return responses


def _canonical_expected(
    responses: list[dict[str, Any]], metadata: list[dict[str, Any]]
) -> dict[Path, dict[str, Any]]:
    feature_entries = _feature_entries()
    feature_metadata = {entry["name"]: entry for entry in feature_entries}
    expected: dict[Path, dict[str, Any]] = {}
    identity = responses[0]["value"]
    manifest_names = [entry["name"] for entry in feature_entries]
    if identity.get("elk_commit") != COMMIT or identity.get("elk_tree") != TREE:
        raise RuntimeError("Java oracle did not report the pinned ELK source identity")
    if identity.get("feature_names") != manifest_names:
        raise RuntimeError("Java oracle feature registry differs from features.toml")

    for response, item in zip(responses[1:], metadata[1:], strict=True):
        relative = item["expected"]
        if item["kind"] == "feature":
            entry = item["entry"]
            count = response["features"].get(entry["name"], 0)
            if count != entry["expected_count"]:
                raise RuntimeError(
                    f"feature oracle count mismatch for {entry['name']}: "
                    f"expected {entry['expected_count']}, got {count}"
                )
            payload = {
                "case_id": f"feature:{entry['name']}",
                "configuration": {
                    "allow_fresh_entities": True,
                    "incremental": False,
                    "unsupported": "ignore",
                    "workers": 1,
                },
                "feature": {
                    "actual_counts": response["features"],
                    "constructor": entry["constructor"],
                    "expected_count": entry["expected_count"],
                    "expected_issue": entry["expected_issue"],
                    "index": entry["index"],
                    "name": entry["name"],
                    "polarity": entry["polarity"],
                    "scope": entry["scope"],
                },
                "operation": item["operation"],
                "result": {
                    "complete": response["complete"],
                    "diagnostics": response["diagnostics"],
                    "value": response["value"],
                },
                "schema": "pyelk.elk-oracle-feature/1",
                "source": {
                    "fixture": entry["fixture"],
                    "fixture_sha256": entry["fixture_sha256"],
                },
            }
            expected[relative] = payload
            continue

        value = copy.deepcopy(response["value"])
        if value.get("upstream_golden_match") is not True:
            raise RuntimeError(f"semantic mismatch against upstream golden for {response['id']}")
        checked = value.pop("upstream_goldens_checked", None)
        value.pop("upstream_golden_match", None)
        value.pop("upstream_operations_checked", None)
        if checked != len(item["goldens"]):
            raise RuntimeError(f"oracle did not check every golden for {response['id']}")
        ontology_counts = response["features"]
        task = item["task"]
        if task in {"class_expression_query", "entailment_query"}:
            aggregate: list[dict[str, Any]] = []
            for query in value["queries"]:
                query.pop("upstream_expected", None)
                query.pop("upstream_source", None)
                issues = _issues_for_counts(
                    task,
                    ontology_counts,
                    feature_metadata,
                    query_counts=query["query_features"],
                )
                if task == "entailment_query":
                    query_complete = query["complete"]
                else:
                    query_complete = all(
                        query[operation]["complete"]
                        for operation in (
                            "direct_instances",
                            "direct_subclasses",
                            "direct_superclasses",
                            "equivalent_classes",
                            "satisfiable",
                        )
                    )
                if query_complete:
                    issues = []
                elif not issues:
                    raise RuntimeError(f"incomplete query has no pinned reason in {response['id']}")
                query["issues"] = issues
                for issue in issues:
                    if issue not in aggregate:
                        aggregate.append(issue)
            issues = sorted(
                aggregate,
                key=lambda issue: (issue["task"], tuple(issue["features"])),
            )
        else:
            issues = _issues_for_counts(task, ontology_counts, feature_metadata)
            if response["complete"]:
                issues = []
            elif not issues:
                raise RuntimeError(f"incomplete result has no pinned reason in {response['id']}")
        ontology = item["ontology"]
        payload = {
            "case_id": Path(ontology["path"]).with_suffix("").as_posix(),
            "configuration": {
                "allow_fresh_entities": True,
                "incremental": False,
                "unsupported": "ignore",
                "workers": 1,
            },
            "family": item["family"],
            "operation": item["operation"],
            "result": {
                "complete": response["complete"],
                "diagnostics": response["diagnostics"],
                "features": ontology_counts,
                "issues": issues,
                "value": value,
            },
            "schema": "pyelk.elk-oracle-case/1",
            "source": {
                "ontology": ontology["path"],
                "ontology_sha256": ontology["sha256"],
                "upstream_goldens": [
                    {"path": golden["path"], "sha256": golden["sha256"]}
                    for golden in item["goldens"]
                ],
            },
        }
        expected[relative] = payload
    if len(expected) != 203:
        raise RuntimeError(f"expected 203 frozen oracle files, generated {len(expected)}")
    return expected


def _render_expected(expected: dict[Path, dict[str, Any]]) -> dict[Path, bytes]:
    return {
        relative: _render(payload).encode("utf-8") for relative, payload in sorted(expected.items())
    }


def _expected_tree_digest(files: dict[Path, bytes]) -> str:
    digest = hashlib.sha256()
    for relative, content in sorted(files.items()):
        name = relative.as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _oracle_evidence(
    files: dict[Path, bytes], toolchain: dict[str, Any], source_digest: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = [
        {
            "bytes": len(content),
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for relative, content in sorted(files.items())
    ]
    digest = _expected_tree_digest(files)
    manifest = {
        "configuration": {
            "allow_fresh_entities": True,
            "incremental": False,
            "unsupported": "ignore",
            "workers": 1,
        },
        "files": inventory,
        "generated_by": "tools/oracle.py regenerate",
        "oracle_source_sha256": source_digest,
        "schema": "pyelk.elk-oracle-manifest/1",
        "source": {
            "commit": COMMIT,
            "elk_version": "0.6.0",
            "owlapi_version": "5.1.20",
            "repository": REPOSITORY,
            "tag": TAG,
            "tree": TREE,
        },
        "summary": {
            "case_files": 124,
            "expected_tree_sha256": digest,
            "feature_files": 79,
            "files": 203,
            "upstream_goldens_checked": 138,
        },
        "toolchain": toolchain,
    }
    report = {
        "determinism": {
            "first_expected_tree_sha256": digest,
            "second_expected_tree_sha256": digest,
            "two_clean_runs_byte_identical": True,
        },
        "provenance": {
            "commit": COMMIT,
            "oracle_source_sha256": source_digest,
            "tag": TAG,
            "tree": TREE,
        },
        "investigated_generator_corrections": [
            {
                "id": "elk-0.6.0-datatype-definition-visitor-dispatch",
                "observation": (
                    "ElkDatatypeDefinitionAxiomImpl.accept(ElkAxiomVisitor) returns null"
                ),
                "resolution": (
                    "oracle-only transparent wrapper restores pinned converter dispatch"
                ),
                "source_path": (
                    "elk-owl-parent/elk-owl-implementation/src/main/java/"
                    "org/semanticweb/elk/owl/implementation/"
                    "ElkDatatypeDefinitionAxiomImpl.java"
                ),
            }
        ],
        "schema": "pyelk.elk-oracle-report/1",
        "semantic_diff": {
            "canonical_output_mismatches": 0,
            "feature_count_mismatches": 0,
            "upstream_golden_mismatches": 0,
        },
        "status": "pass",
        "verification": {
            "case_inputs": 124,
            "feature_fixtures": 79,
            "oracle_requests_per_run": 204,
            "upstream_golden_outputs": 138,
        },
    }
    return manifest, report


def _install_oracle_stage(stage: Path, *, check: bool) -> None:
    expected_stage = stage / "expected"
    staged_manifest = stage / ORACLE_MANIFEST.name
    staged_report = stage / ORACLE_REPORT.name
    matches = (
        EXPECTED.is_dir()
        and _tree_digest_for_all_files(EXPECTED) == _tree_digest_for_all_files(expected_stage)
        and ORACLE_MANIFEST.is_file()
        and ORACLE_MANIFEST.read_bytes() == staged_manifest.read_bytes()
        and ORACLE_REPORT.is_file()
        and ORACLE_REPORT.read_bytes() == staged_report.read_bytes()
    )
    if matches:
        return
    if check:
        raise RuntimeError("committed oracle expectations differ from two clean pinned runs")
    backup = TARGET / ".expected.backup"
    if backup.exists():
        shutil.rmtree(backup)
    if EXPECTED.exists():
        os.replace(EXPECTED, backup)
    try:
        os.replace(expected_stage, EXPECTED)
        os.replace(staged_manifest, ORACLE_MANIFEST)
        os.replace(staged_report, ORACLE_REPORT)
    except BaseException:
        if backup.exists() and not EXPECTED.exists():
            os.replace(backup, EXPECTED)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def regenerate(
    *,
    java_home: Path,
    maven: Path,
    maven_repository: Path,
    check: bool,
) -> None:
    verify(require_oracle=False)
    java, toolchain = _verify_toolchain(java_home.resolve(), maven.resolve())
    jar = _build_oracle(java_home.resolve(), maven.resolve(), maven_repository.resolve())
    requests, metadata = _oracle_requests()
    first = _render_expected(_canonical_expected(_run_oracle(java, jar, requests), metadata))
    second = _render_expected(_canonical_expected(_run_oracle(java, jar, requests), metadata))
    if first != second:
        changed = sorted(
            relative.as_posix()
            for relative in set(first) | set(second)
            if first.get(relative) != second.get(relative)
        )
        raise RuntimeError(f"oracle regeneration is nondeterministic: {changed[:10]}")
    source_digest = _oracle_source_digest()
    manifest, report = _oracle_evidence(first, toolchain, source_digest)
    with tempfile.TemporaryDirectory(prefix="elk-oracle-stage-", dir=TARGET.parent) as temporary:
        stage = Path(temporary)
        for relative, content in first.items():
            path = stage / "expected" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (stage / ORACLE_MANIFEST.name).write_text(_render(manifest), encoding="utf-8")
        (stage / ORACLE_REPORT.name).write_text(_render(report), encoding="utf-8")
        _install_oracle_stage(stage, check=check)


def _verify_oracle_frozen_data() -> None:
    required = [TARGET / "UPSTREAM.toml", TARGET / "NOTICE.pyelk", ORACLE_MANIFEST, ORACLE_REPORT]
    if not all(path.is_file() for path in required):
        raise RuntimeError("frozen corpus is missing oracle provenance or evidence files")
    try:
        manifest = json.loads(ORACLE_MANIFEST.read_text(encoding="utf-8"))
        report = json.loads(ORACLE_REPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read frozen oracle evidence: {error}") from error
    if manifest.get("schema") != "pyelk.elk-oracle-manifest/1":
        raise RuntimeError("unknown frozen oracle manifest schema")
    if report.get("schema") != "pyelk.elk-oracle-report/1" or report.get("status") != "pass":
        raise RuntimeError("frozen oracle evidence report is not passing")
    source_digest = _oracle_source_digest()
    if manifest.get("oracle_source_sha256") != source_digest:
        raise RuntimeError("Java oracle source differs from the frozen evidence manifest")
    configuration = {
        "allow_fresh_entities": True,
        "incremental": False,
        "unsupported": "ignore",
        "workers": 1,
    }
    source = {
        "commit": COMMIT,
        "elk_version": "0.6.0",
        "owlapi_version": "5.1.20",
        "repository": REPOSITORY,
        "tag": TAG,
        "tree": TREE,
    }
    if manifest.get("configuration") != configuration or manifest.get("source") != source:
        raise RuntimeError("frozen oracle configuration or source identity is not pinned")
    if manifest.get("toolchain") != {
        "java_archive_sha256": JAVA_ARCHIVE_SHA256,
        "java_executable_sha256": JAVA_EXECUTABLE_SHA256,
        "java_runtime": "Eclipse Temurin 17.0.19+10",
        "maven_executable_sha256": MAVEN_EXECUTABLE_SHA256,
        "maven_runtime": "Apache Maven 3.9.16",
    }:
        raise RuntimeError("frozen oracle evidence does not use the pinned toolchain")
    summary = manifest.get("summary", {})
    if {
        key: summary.get(key)
        for key in ("case_files", "feature_files", "files", "upstream_goldens_checked")
    } != {
        "case_files": 124,
        "feature_files": 79,
        "files": 203,
        "upstream_goldens_checked": 138,
    }:
        raise RuntimeError("frozen oracle evidence has the wrong corpus inventory")
    digest = summary.get("expected_tree_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RuntimeError("frozen oracle expected-tree digest is malformed")
    determinism = report.get("determinism", {})
    if (
        determinism.get("two_clean_runs_byte_identical") is not True
        or determinism.get("first_expected_tree_sha256") != digest
        or determinism.get("second_expected_tree_sha256") != digest
    ):
        raise RuntimeError("frozen oracle evidence does not prove two-run determinism")
    if report.get("provenance") != {
        "commit": COMMIT,
        "oracle_source_sha256": source_digest,
        "tag": TAG,
        "tree": TREE,
    }:
        raise RuntimeError("frozen oracle report provenance is not pinned")
    if report.get("verification") != {
        "case_inputs": 124,
        "feature_fixtures": 79,
        "oracle_requests_per_run": 204,
        "upstream_golden_outputs": 138,
    }:
        raise RuntimeError("frozen oracle report has the wrong verification inventory")
    if report.get("investigated_generator_corrections") != [
        {
            "id": "elk-0.6.0-datatype-definition-visitor-dispatch",
            "observation": "ElkDatatypeDefinitionAxiomImpl.accept(ElkAxiomVisitor) returns null",
            "resolution": "oracle-only transparent wrapper restores pinned converter dispatch",
            "source_path": (
                "elk-owl-parent/elk-owl-implementation/src/main/java/"
                "org/semanticweb/elk/owl/implementation/"
                "ElkDatatypeDefinitionAxiomImpl.java"
            ),
        }
    ]:
        raise RuntimeError("frozen oracle report lost its investigated generator correction")
    inventory = manifest.get("files")
    if not isinstance(inventory, list) or len(inventory) != 203:
        raise RuntimeError("frozen oracle manifest must inventory 203 expected files")
    feature_entries = _feature_entries()
    feature_metadata = {entry["name"]: entry for entry in feature_entries}
    actual_paths: set[str] = set()
    files: dict[Path, bytes] = {}
    for entry in inventory:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"unsafe frozen oracle expectation path: {relative}")
        if relative.as_posix() in actual_paths:
            raise RuntimeError(f"duplicate frozen oracle expectation path: {relative}")
        path = EXPECTED / relative
        if not path.is_file():
            raise RuntimeError(f"missing frozen oracle expectation: {relative}")
        content = path.read_bytes()
        if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"hash-mismatched frozen oracle expectation: {relative}")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid frozen oracle JSON: {relative}") from error
        schema = payload.get("schema")
        if schema not in {"pyelk.elk-oracle-case/1", "pyelk.elk-oracle-feature/1"}:
            raise RuntimeError(f"unknown expected-result schema: {relative}")
        if payload.get("configuration") != configuration:
            raise RuntimeError(f"unfixed expected-result configuration: {relative}")
        if schema == "pyelk.elk-oracle-feature/1":
            feature = payload.get("feature", {})
            name = feature.get("name")
            metadata = feature_metadata.get(name)
            if metadata is None:
                raise RuntimeError(f"unknown feature expectation: {relative}")
            expected_relative = Path("features") / metadata["scope"] / f"{name}.json"
            counts = feature.get("actual_counts", {})
            fixture = ROOT / metadata["fixture"]
            if (
                relative != expected_relative
                or feature.get("index") != metadata["index"]
                or feature.get("expected_count") != metadata["expected_count"]
                or counts.get(name, 0) != metadata["expected_count"]
                or not fixture.is_file()
                or _sha256(fixture) != metadata["fixture_sha256"]
            ):
                raise RuntimeError(f"feature expectation differs from features.toml: {relative}")
        actual_paths.add(relative.as_posix())
        files[relative] = content
    discovered = {
        path.relative_to(EXPECTED).as_posix() for path in EXPECTED.rglob("*") if path.is_file()
    }
    if actual_paths != discovered:
        raise RuntimeError("expected tree contains unmanifested or missing files")
    if _expected_tree_digest(files) != digest:
        raise RuntimeError("expected tree digest differs from the frozen evidence manifest")
    if report.get("semantic_diff") != {
        "canonical_output_mismatches": 0,
        "feature_count_mismatches": 0,
        "upstream_golden_mismatches": 0,
    }:
        raise RuntimeError("frozen semantic-diff report contains unresolved mismatches")
    forbidden = {".class", ".jar", ".java"}
    if any(
        path.suffix in forbidden
        for root in (ROOT / "src", TARGET)
        for path in root.rglob("*")
        if path.is_file()
    ):
        raise RuntimeError("runtime or frozen release data contains a Java artifact")


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
    regenerate_parser = subparsers.add_parser(
        "regenerate", help="run the pinned Java oracle twice and freeze canonical JSON"
    )
    regenerate_parser.add_argument("--java-home", type=Path, default=DEFAULT_JAVA_HOME)
    regenerate_parser.add_argument("--maven", type=Path, default=DEFAULT_MAVEN)
    regenerate_parser.add_argument(
        "--maven-repository", type=Path, default=DEFAULT_MAVEN_REPOSITORY
    )
    regenerate_parser.add_argument("--check", action="store_true")
    subparsers.add_parser("verify", help="verify the committed Java-free corpus")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "sync":
            sync(arguments.source, check=arguments.check)
        elif arguments.command == "features":
            sync_features(arguments.source, check=arguments.check)
        elif arguments.command == "regenerate":
            regenerate(
                java_home=arguments.java_home,
                maven=arguments.maven,
                maven_repository=arguments.maven_repository,
                check=arguments.check,
            )
        else:
            verify()
    except RuntimeError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
