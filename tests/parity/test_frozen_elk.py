"""Java-free integrity and discovery harness for the frozen ELK 0.6.0 corpus."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from pyelk.reasoning.completeness import Feature, issues_for
from pyelk.reasoning.contracts import CompletenessIssue, ReasoningTask

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "data" / "elk-v0.6.0"
UPSTREAM = CORPUS / "upstream"
MANIFEST = CORPUS / "manifest.json"
EXPECTED = CORPUS / "expected"
FEATURE_MANIFEST = ROOT / "tests" / "data" / "manifests" / "features.toml"
ORACLE_MANIFEST = CORPUS / "oracle-manifest.json"
ORACLE_REPORT = CORPUS / "oracle-report.json"

PINNED_COMMIT = "b8ac5ce83db0704a7359d96aa382891e2f547863"
PINNED_TREE = "9becd9e41eac6434a1e247c2a9b19644cdd9d27a"
PINNED_LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"

_QUERY_OPERATIONS = (
    "direct_instances",
    "direct_subclasses",
    "direct_superclasses",
    "equivalent_classes",
    "satisfiable",
)
_QUIET_TASKS = {
    ReasoningTask.CLASS_TAXONOMY,
    ReasoningTask.OBJECT_PROPERTY_TAXONOMY,
    ReasoningTask.REALIZATION,
    ReasoningTask.CLASS_EXPRESSION_QUERY,
}


@dataclass(frozen=True, slots=True)
class FrozenCase:
    """One upstream ontology and the retained golden resources associated with it."""

    case_id: str
    family: str
    ontology: Path
    upstream_goldens: tuple[Path, ...]
    expected: Path


class FrozenCaseEvaluator(Protocol):
    """WP13-compatible adapter used once canonical Java JSON is present."""

    def __call__(self, case: FrozenCase) -> Mapping[str, Any]: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _feature_vector(counts: Mapping[str, Any]) -> tuple[int, ...]:
    values = [0] * len(Feature)
    for name, count in counts.items():
        assert isinstance(name, str)
        assert isinstance(count, int) and not isinstance(count, bool) and count >= 0
        values[Feature[name]] = count
    return tuple(values)


def _issue_payload(issue: CompletenessIssue) -> dict[str, Any]:
    return {
        "constructors": list(issue.constructors),
        "features": list(issue.features),
        "polarities": list(issue.polarities),
        "task": issue.task.value,
    }


def _runtime_issues(
    task: ReasoningTask,
    ontology_counts: Mapping[str, Any],
    *,
    query_counts: Mapping[str, Any] | None = None,
    oracle_complete: bool,
) -> list[dict[str, Any]]:
    ontology = _feature_vector(ontology_counts)
    query = () if query_counts is None else _feature_vector(query_counts)
    issues = issues_for(task, ontology, query_feature_counts=query)
    if oracle_complete and issues and task in _QUIET_TASKS:
        issues = issues_for(
            task,
            ontology,
            query_feature_counts=query,
            inconsistent=True,
        )
    return [_issue_payload(issue) for issue in issues]


def _canonical_issue_union(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {json.dumps(value, sort_keys=True): value for value in values}
    return sorted(
        unique.values(),
        key=lambda issue: (
            issue["task"],
            tuple(issue["features"]),
            tuple(issue["constructors"]),
            tuple(issue["polarities"]),
        ),
    )


def _payload() -> dict[str, Any]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("frozen corpus manifest must be an object")
    return value


def frozen_cases() -> tuple[FrozenCase, ...]:
    """Discover all 124 cases without parsing OWL or upstream golden syntax."""

    payload = _payload()
    entries = payload["files"]
    goldens_by_stem: dict[tuple[str, str], list[Path]] = {}
    for entry in entries:
        relative = Path(entry["path"])
        if entry["role"] != "upstream_golden":
            continue
        key = (entry["family"], relative.with_suffix("").as_posix())
        goldens_by_stem.setdefault(key, []).append(UPSTREAM / relative)

    result: list[FrozenCase] = []
    for entry in entries:
        if entry["role"] != "ontology":
            continue
        relative = Path(entry["path"])
        key = (entry["family"], relative.with_suffix("").as_posix())
        goldens = tuple(sorted(goldens_by_stem.get(key, ())))
        expected = EXPECTED / relative.with_suffix(".json")
        result.append(
            FrozenCase(
                case_id=relative.with_suffix("").as_posix(),
                family=entry["family"],
                ontology=UPSTREAM / relative,
                upstream_goldens=goldens,
                expected=expected,
            )
        )
    return tuple(result)


def evaluate_frozen_cases(evaluator: FrozenCaseEvaluator) -> Iterator[tuple[str, bool]]:
    """Compare a supplied WP13 adapter with every canonical expected file."""

    for case in frozen_cases():
        expected = json.loads(case.expected.read_text(encoding="utf-8"))
        actual = evaluator(case)
        yield case.case_id, actual == expected


def _feature_entries() -> list[dict[str, Any]]:
    """Read the generated TOML subset without adding a Python 3.10 dependency."""

    text = FEATURE_MANIFEST.read_text(encoding="utf-8")
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
            assert match is not None
            entry[key] = json.loads(match.group(1))
        for key in ("index", "expected_count"):
            match = re.search(rf"^{key}\s*=\s*(\d+)$", block, re.MULTILINE)
            assert match is not None
            entry[key] = int(match.group(1))
        result.append(entry)
    return result


def _assert_canonical_taxonomy(value: Mapping[str, Any], *, realization: bool) -> None:
    nodes = value["nodes"]
    edges = value["direct_edges"]

    assert nodes == sorted(nodes, key=lambda node: tuple(member.encode() for member in node))
    assert all(node == sorted(node, key=str.encode) for node in nodes)
    assert edges == sorted(edges)
    assert all(
        isinstance(edge, list) and len(edge) == 2 and all(isinstance(index, int) for index in edge)
        for edge in edges
    )
    assert 0 <= value["top"] < len(nodes)
    assert 0 <= value["bottom"] < len(nodes)
    if realization:
        instance_nodes = value["instance_nodes"]
        direct_types = value["direct_types"]
        assert instance_nodes == sorted(
            instance_nodes,
            key=lambda node: tuple(member.encode() for member in node),
        )
        assert all(node == sorted(node, key=str.encode) for node in instance_nodes)
        assert direct_types == sorted(direct_types)
        assert all(
            isinstance(relation, list)
            and len(relation) == 2
            and 0 <= relation[0] < len(instance_nodes)
            and 0 <= relation[1] < len(nodes)
            for relation in direct_types
        )


def test_manifest_pins_exact_upstream_source_and_inventory() -> None:
    payload = _payload()

    assert payload["schema"] == "pyelk.elk-frozen-corpus/1"
    assert payload["source"]["commit"] == PINNED_COMMIT
    assert payload["source"]["tree"] == PINNED_TREE
    assert payload["source"]["tag"] == "v0.6.0"
    assert payload["source"]["license"] == "Apache-2.0"
    assert payload["source"]["license_sha256"] == PINNED_LICENSE_SHA256
    assert payload["summary"] == {
        "class_classification_inputs": 66,
        "class_query_inputs": 26,
        "entailment_inputs": 16,
        "files": 262,
        "golden_outputs": 138,
        "object_property_classification_inputs": 11,
        "ontology_inputs": 124,
        "realization_inputs": 5,
    }


def test_every_resource_matches_manifest_hash_size_and_source_path() -> None:
    payload = _payload()
    seen: set[str] = set()

    for entry in payload["files"]:
        relative = entry["path"]
        assert relative not in seen
        seen.add(relative)
        path = UPSTREAM / relative
        assert path.is_file()
        assert path.stat().st_size == entry["bytes"]
        assert _sha256(path) == entry["sha256"]
        assert entry["upstream_path"].endswith("/" + relative)

    actual = {
        path.relative_to(UPSTREAM).as_posix() for path in UPSTREAM.rglob("*") if path.is_file()
    }
    assert actual == seen
    assert _sha256(CORPUS / "LICENSE.txt") == PINNED_LICENSE_SHA256


def test_discovery_harness_exposes_every_ontology_and_golden() -> None:
    cases = frozen_cases()
    families = Counter(case.family for case in cases)
    goldens = {path for case in cases for path in case.upstream_goldens}

    assert len(cases) == 124
    assert families == {
        "class_classification": 66,
        "object_property_classification": 11,
        "class_query": 26,
        "entailment": 16,
        "realization": 5,
    }
    assert len(goldens) == 138
    assert all(case.upstream_goldens for case in cases)
    assert all(case.expected.is_file() for case in cases)


def test_every_corpus_case_has_canonical_expected_result_with_exact_provenance() -> None:
    operations = {
        "class_classification": "class_taxonomy",
        "object_property_classification": "object_property_taxonomy",
        "class_query": "class_queries",
        "entailment": "entailment",
        "realization": "realization",
    }

    for case in frozen_cases():
        payload = json.loads(case.expected.read_text(encoding="utf-8"))
        assert payload["schema"] == "pyelk.elk-oracle-case/1"
        assert payload["case_id"] == case.case_id
        assert payload["family"] == case.family
        assert payload["operation"] == operations[case.family]
        assert payload["configuration"] == {
            "allow_fresh_entities": True,
            "incremental": False,
            "unsupported": "ignore",
            "workers": 1,
        }
        assert payload["source"]["ontology"] == case.ontology.relative_to(UPSTREAM).as_posix()
        assert payload["source"]["ontology_sha256"] == _sha256(case.ontology)
        assert payload["source"]["upstream_goldens"] == [
            {
                "path": golden.relative_to(UPSTREAM).as_posix(),
                "sha256": _sha256(golden),
            }
            for golden in case.upstream_goldens
        ]
        assert isinstance(payload["result"]["complete"], bool)
        assert isinstance(payload["result"]["diagnostics"], list)
        assert isinstance(payload["result"]["features"], dict)
        assert isinstance(payload["result"]["issues"], list)
        assert "upstream_golden_match" not in json.dumps(payload, sort_keys=True)
        if case.family in {
            "class_classification",
            "object_property_classification",
            "realization",
        }:
            _assert_canonical_taxonomy(
                payload["result"]["value"],
                realization=case.family == "realization",
            )
        else:
            queries = payload["result"]["value"]["queries"]
            query_key = "expression" if case.family == "class_query" else "axiom"
            assert queries == sorted(queries, key=lambda query: query[query_key].encode())


def test_all_frozen_java_issues_match_the_runtime_completeness_evaluator() -> None:
    task_by_family = {
        "class_classification": ReasoningTask.CLASS_TAXONOMY,
        "object_property_classification": ReasoningTask.OBJECT_PROPERTY_TAXONOMY,
        "realization": ReasoningTask.REALIZATION,
    }

    for case in frozen_cases():
        payload = json.loads(case.expected.read_text(encoding="utf-8"))
        result = payload["result"]
        ontology_counts = result["features"]
        if case.family in task_by_family:
            actual = _runtime_issues(
                task_by_family[case.family],
                ontology_counts,
                oracle_complete=result["complete"],
            )
            assert actual == result["issues"], case.case_id
            continue

        task = (
            ReasoningTask.CLASS_EXPRESSION_QUERY
            if case.family == "class_query"
            else ReasoningTask.ENTAILMENT_QUERY
        )
        aggregate: list[dict[str, Any]] = []
        for query in result["value"]["queries"]:
            oracle_complete = (
                all(query[operation]["complete"] for operation in _QUERY_OPERATIONS)
                if task is ReasoningTask.CLASS_EXPRESSION_QUERY
                else query["complete"]
            )
            actual = _runtime_issues(
                task,
                ontology_counts,
                query_counts=query["query_features"],
                oracle_complete=oracle_complete,
            )
            assert actual == query["issues"], (case.case_id, query)
            aggregate.extend(actual)
        assert _canonical_issue_union(aggregate) == result["issues"], case.case_id


def test_all_79_feature_expectations_match_manifest_target_counts() -> None:
    entries = _feature_entries()

    assert len(entries) == 79
    assert [entry["index"] for entry in entries] == list(range(79))
    for entry in entries:
        expected = EXPECTED / "features" / entry["scope"] / f"{entry['name']}.json"
        payload = json.loads(expected.read_text(encoding="utf-8"))
        feature = payload["feature"]
        assert payload["schema"] == "pyelk.elk-oracle-feature/1"
        assert payload["case_id"] == f"feature:{entry['name']}"
        assert payload["configuration"] == {
            "allow_fresh_entities": True,
            "incremental": False,
            "unsupported": "ignore",
            "workers": 1,
        }
        assert feature["index"] == entry["index"]
        assert feature["name"] == entry["name"]
        assert feature["constructor"] == entry["constructor"]
        assert feature["polarity"] == entry["polarity"]
        assert feature["scope"] == entry["scope"]
        assert feature["expected_count"] == entry["expected_count"]
        assert feature["actual_counts"].get(entry["name"], 0) == entry["expected_count"]
        assert payload["source"] == {
            "fixture": entry["fixture"],
            "fixture_sha256": entry["fixture_sha256"],
        }
        assert _sha256(ROOT / entry["fixture"]) == entry["fixture_sha256"]


def test_oracle_evidence_records_determinism_goldens_and_investigated_correction() -> None:
    manifest = json.loads(ORACLE_MANIFEST.read_text(encoding="utf-8"))
    report = json.loads(ORACLE_REPORT.read_text(encoding="utf-8"))

    assert manifest["schema"] == "pyelk.elk-oracle-manifest/1"
    assert manifest["source"] == {
        "commit": PINNED_COMMIT,
        "elk_version": "0.6.0",
        "owlapi_version": "5.1.20",
        "repository": "https://github.com/liveontologies/elk-reasoner",
        "tag": "v0.6.0",
        "tree": PINNED_TREE,
    }
    assert manifest["summary"]["files"] == 203
    assert manifest["summary"]["case_files"] == 124
    assert manifest["summary"]["feature_files"] == 79
    assert manifest["summary"]["upstream_goldens_checked"] == 138
    assert len(manifest["files"]) == 203

    assert report["status"] == "pass"
    assert report["determinism"]["two_clean_runs_byte_identical"] is True
    assert (
        report["determinism"]["first_expected_tree_sha256"]
        == report["determinism"]["second_expected_tree_sha256"]
        == manifest["summary"]["expected_tree_sha256"]
    )
    assert report["semantic_diff"] == {
        "canonical_output_mismatches": 0,
        "feature_count_mismatches": 0,
        "upstream_golden_mismatches": 0,
    }
    assert report["investigated_generator_corrections"] == [
        {
            "id": "elk-0.6.0-datatype-definition-visitor-dispatch",
            "observation": ("ElkDatatypeDefinitionAxiomImpl.accept(ElkAxiomVisitor) returns null"),
            "resolution": "oracle-only transparent wrapper restores pinned converter dispatch",
            "source_path": (
                "elk-owl-parent/elk-owl-implementation/src/main/java/"
                "org/semanticweb/elk/owl/implementation/ElkDatatypeDefinitionAxiomImpl.java"
            ),
        }
    ]


def test_frozen_tree_contains_no_java_runtime_artifacts() -> None:
    forbidden = {".class", ".jar", ".java"}

    assert not [path for path in CORPUS.rglob("*") if path.is_file() and path.suffix in forbidden]


def test_runtime_and_release_data_are_isolated_from_java_oracle() -> None:
    release_roots = (ROOT / "src", CORPUS)
    binary_suffixes = {".class", ".jar"}

    assert all(not (root / "tools" / "java-oracle").exists() for root in release_roots)
    assert not [
        path
        for root in release_roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix in binary_suffixes
    ]
    assert 'package-dir = { "" = "src" }' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_repository_verifier_accepts_committed_tree() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "oracle.py"), "verify"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_repository_verifier_is_java_free_with_empty_path() -> None:
    environment = os.environ.copy()
    environment["PATH"] = ""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "oracle.py"), "verify"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr


def test_evaluator_hook_runs_all_canonical_cases() -> None:
    calls: list[str] = []

    def evaluator(case: FrozenCase) -> Mapping[str, Any]:
        calls.append(case.case_id)
        return cast(
            Mapping[str, Any],
            json.loads(case.expected.read_text(encoding="utf-8")),
        )

    results = list(evaluate_frozen_cases(evaluator))

    assert len(results) == 124
    assert all(matches for _, matches in results)
    assert calls == [case.case_id for case in frozen_cases()]
