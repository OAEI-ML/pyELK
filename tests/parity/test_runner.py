from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.parity.runner import case_ids, run_frozen_suite

ROOT = Path(__file__).resolve().parents[2]


def test_runner_inventory_is_the_exact_frozen_ontology_set() -> None:
    identifiers = case_ids()
    assert len(identifiers) == 124
    assert len(set(identifiers)) == 124
    assert identifiers == tuple(sorted(identifiers, key=str.encode))


def test_runner_compares_public_result_with_java_json() -> None:
    report = run_frozen_suite(
        backend="python",
        workers=1,
        selected_cases=(
            "classification/Existentials",
            "classification/object_property/Equivalent",
            "realization/BasicABox",
            "query/class/Conjunctions",
            "query/entailment/EmptyOntology",
        ),
    )
    assert report.passed
    assert report.effective_backend == "python"
    assert report.passed_cases == report.expected_cases == 5


def test_runner_cli_is_java_free_and_hash_seed_stable() -> None:
    command = [
        sys.executable,
        str(ROOT / "tests" / "parity" / "runner.py"),
        "--backend",
        "python",
        "--workers",
        "1",
        "--case",
        "classification/Existentials",
    ]
    reports: list[dict[str, object]] = []
    for seed in ("0", "37"):
        environment = os.environ.copy()
        environment["PATH"] = ""
        environment["PYTHONHASHSEED"] = seed
        environment["PYELK_PURE_PYTHON"] = "1"
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["passed"] is True
        report = payload["reports"][0]
        assert report["failures"] == []
        report.pop("elapsed_seconds")
        reports.append(report)
    assert reports[0] == reports[1]
