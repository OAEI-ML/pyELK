"""Java-free integrity and discovery harness for the frozen ELK 0.6.0 corpus."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "data" / "elk-v0.6.0"
UPSTREAM = CORPUS / "upstream"
MANIFEST = CORPUS / "manifest.json"
EXPECTED = CORPUS / "expected"

PINNED_COMMIT = "b8ac5ce83db0704a7359d96aa382891e2f547863"
PINNED_TREE = "9becd9e41eac6434a1e247c2a9b19644cdd9d27a"
PINNED_LICENSE_SHA256 = (
    "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
)


@dataclass(frozen=True, slots=True)
class FrozenCase:
    """One upstream ontology and the retained golden resources associated with it."""

    case_id: str
    family: str
    ontology: Path
    upstream_goldens: tuple[Path, ...]
    expected: Path | None


class FrozenCaseEvaluator(Protocol):
    """WP13-compatible adapter used once canonical Java JSON is present."""

    def __call__(self, case: FrozenCase) -> Mapping[str, Any]: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
                expected=expected if expected.is_file() else None,
            )
        )
    return tuple(result)


def evaluate_frozen_cases(evaluator: FrozenCaseEvaluator) -> Iterator[tuple[str, bool]]:
    """Compare a supplied WP13 adapter with every available canonical expected file."""

    for case in frozen_cases():
        if case.expected is None:
            continue
        expected = json.loads(case.expected.read_text(encoding="utf-8"))
        actual = evaluator(case)
        yield case.case_id, actual == expected


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
        path.relative_to(UPSTREAM).as_posix()
        for path in UPSTREAM.rglob("*")
        if path.is_file()
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


def test_frozen_tree_contains_no_java_runtime_artifacts() -> None:
    forbidden = {".class", ".jar", ".java"}

    assert not [
        path for path in CORPUS.rglob("*") if path.is_file() and path.suffix in forbidden
    ]


def test_repository_verifier_accepts_committed_tree() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "oracle.py"), "verify"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_evaluator_hook_is_empty_until_canonical_java_results_are_added() -> None:
    calls: list[str] = []

    def evaluator(case: FrozenCase) -> Mapping[str, Any]:
        calls.append(case.case_id)
        return {}

    assert list(evaluate_frozen_cases(evaluator)) == []
    assert calls == []
