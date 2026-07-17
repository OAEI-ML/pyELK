from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks" / "manifest.toml"


def test_performance_manifest_pins_every_required_corpus_and_threshold() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    assert text.startswith('schema = "pyelk.performance-corpus/1"')
    assert text.count("[[corpora]]") == 6
    for value in (
        "elk-0.6.0-frozen",
        "generated-el-chain-medium",
        "generated-el-chain-large",
        "generated-property-chain-large",
        "generated-taxonomy-mixed",
        "shared-snapshot-million",
        "native_python_geometric_mean_speedup = 5.0",
        "native_boundary_fraction_max = 0.05",
        "release_regression_fraction_max = 0.10",
    ):
        assert value in text
    assert (
        hashlib.sha256(
            (ROOT / "tests" / "data" / "elk-v0.6.0" / "manifest.json").read_bytes()
        ).hexdigest()
        in text
    )


def test_quick_integrated_benchmark_is_java_free_and_semantic_checking() -> None:
    environment = os.environ.copy()
    environment["PATH"] = ""
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "benchmark.py"),
            "--suite",
            "quick",
            "--workers",
            "1",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "pyelk.integrated-benchmark/1"
    assert payload["native"] is False
    assert payload["java_comparison"] is None
    assert set(payload["results"]) == {
        "class-saturation",
        "end-to-end",
        "property-saturation",
        "snapshot-ingestion",
        "taxonomy",
    }
    assert payload["results"]["end-to-end"]["backend"] == "python"
    assert payload["results"]["end-to-end"]["result_sha256"]
