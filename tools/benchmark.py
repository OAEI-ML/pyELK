#!/usr/bin/env python3
"""Run the integrated, semantic-checking pyELK benchmark corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "manifest.toml"
BASELINE = ROOT / "specs" / "baseline.toml"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BIOMEDICAL_VIEWS = ("source", "target", "composite")
_JAVA_REPORT_SCHEMA = "pyelk.java-performance/1"


@dataclass(frozen=True, slots=True)
class BiomedicalInputs:
    source: Path
    source_sha256: str
    source_axiom_count: int
    source_entity_count: int
    target: Path
    target_sha256: str
    target_axiom_count: int
    target_entity_count: int
    alignment: Path
    alignment_sha256: str
    name: str
    origin: str
    license: str
    expected_source_semantic_completeness_sha256: str | None = None
    expected_target_semantic_completeness_sha256: str | None = None
    expected_composite_semantic_completeness_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("source", "target", "alignment"):
            if not isinstance(getattr(self, name), Path):
                raise TypeError(f"biomedical {name} must be a path")
        for name in ("source_sha256", "target_sha256", "alignment_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(f"biomedical {name} must be 64 lowercase hexadecimal characters")
        for name in (
            "source_axiom_count",
            "source_entity_count",
            "target_axiom_count",
            "target_entity_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"biomedical {name} must be a nonnegative integer")
        for name in ("name", "origin", "license"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"biomedical {name} must be nonempty")
        semantic_names = (
            "expected_source_semantic_completeness_sha256",
            "expected_target_semantic_completeness_sha256",
            "expected_composite_semantic_completeness_sha256",
        )
        semantic_values = {name: getattr(self, name) for name in semantic_names}
        if any(value is not None for value in semantic_values.values()):
            missing = [name for name, value in semantic_values.items() if value is None]
            if missing:
                raise ValueError(
                    "biomedical semantic expectations are all-or-nothing; missing: "
                    + ", ".join(missing)
                )
            for name, value in semantic_values.items():
                if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                    raise ValueError(
                        f"biomedical {name} must be 64 lowercase hexadecimal characters"
                    )

    @property
    def has_semantic_expectations(self) -> bool:
        return self.expected_source_semantic_completeness_sha256 is not None

    def command_arguments(self) -> list[str]:
        arguments = [
            "--source",
            str(self.source),
            "--source-sha256",
            self.source_sha256,
            "--source-axiom-count",
            str(self.source_axiom_count),
            "--source-entity-count",
            str(self.source_entity_count),
            "--target",
            str(self.target),
            "--target-sha256",
            self.target_sha256,
            "--target-axiom-count",
            str(self.target_axiom_count),
            "--target-entity-count",
            str(self.target_entity_count),
            "--alignment",
            str(self.alignment),
            "--alignment-sha256",
            self.alignment_sha256,
            "--corpus-name",
            self.name,
            "--corpus-source",
            self.origin,
            "--corpus-license",
            self.license,
        ]
        if self.has_semantic_expectations:
            expectations = (
                (
                    "--expected-source-semantic-completeness-sha256",
                    self.expected_source_semantic_completeness_sha256,
                ),
                (
                    "--expected-target-semantic-completeness-sha256",
                    self.expected_target_semantic_completeness_sha256,
                ),
                (
                    "--expected-composite-semantic-completeness-sha256",
                    self.expected_composite_semantic_completeness_sha256,
                ),
            )
            for flag, value in expectations:
                if value is None:  # pragma: no cover - guarded by post-init
                    raise AssertionError("semantic expectation unexpectedly missing")
                arguments.extend((flag, value))
        return arguments


@dataclass(frozen=True, slots=True)
class _ReleaseMetric:
    median_seconds: float
    identity: tuple[object, ...]
    current_rss_growth_bytes: int | None = None


def _git_state(path: Path) -> dict[str, object]:
    git = shutil.which("git")
    if git is None or not (path / ".git").exists():
        return {"commit": None, "dirty": None}
    commit = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        [git, "status", "--porcelain"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status.stdout) if status.returncode == 0 else None,
    }


def _physical_memory_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    return pages * page_size


def _toml_table(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^\[{re.escape(name)}\]\s*$\n(.*?)(?=^\[|\Z)",
        text,
    )
    if match is None:
        raise RuntimeError(f"{path.relative_to(ROOT)} is missing [{name}]")
    return match.group(1)


def _toml_number(path: Path, table: str, name: str) -> float:
    body = _toml_table(path, table)
    matches = re.findall(
        rf"(?m)^{re.escape(name)}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        body,
    )
    if len(matches) != 1:
        raise RuntimeError(f"{path.relative_to(ROOT)} must define exactly one {table}.{name}")
    value = float(matches[0])
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{path.relative_to(ROOT)} has invalid {table}.{name}")
    return value


def _toml_string(path: Path, table: str, name: str) -> str:
    body = _toml_table(path, table)
    matches = re.findall(rf'(?m)^{re.escape(name)}\s*=\s*"([^"]+)"\s*$', body)
    if len(matches) != 1:
        raise RuntimeError(f"{path.relative_to(ROOT)} must define exactly one {table}.{name}")
    value = matches[0]
    if not isinstance(value, str):
        raise RuntimeError(f"{path.relative_to(ROOT)} has invalid {table}.{name}")
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _positive_samples(value: object, label: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    samples: list[float] = []
    for index, raw in enumerate(value):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{label}[{index}] must be a number")
        sample = float(raw)
        if not math.isfinite(sample) or sample <= 0:
            raise ValueError(f"{label}[{index}] must be finite and positive")
        samples.append(sample)
    return tuple(samples)


def _validate_java_performance_report(
    payload: Mapping[str, object],
    *,
    machine_label: str,
    workers: int,
    biomedical: BiomedicalInputs,
) -> dict[str, tuple[float, ...]]:
    if payload.get("schema") != _JAVA_REPORT_SCHEMA:
        raise ValueError(f"Java report schema must be {_JAVA_REPORT_SCHEMA!r}")
    if payload.get("machine_label") != machine_label:
        raise ValueError("Java report machine_label does not match the integrated runner")
    if payload.get("workers") != workers:
        raise ValueError("Java report workers do not match the integrated runner")

    protocol = _mapping(payload.get("protocol"), "Java report protocol")
    warmups = protocol.get("warmups")
    measured_runs = protocol.get("measured_runs")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 2:
        raise ValueError("Java report protocol requires at least two warm-ups")
    if isinstance(measured_runs, bool) or not isinstance(measured_runs, int) or measured_runs < 5:
        raise ValueError("Java report protocol requires at least five measured runs")

    elk = _mapping(payload.get("elk"), "Java report elk")
    expected_release = _toml_string(BASELINE, "elk", "release")
    expected_commit = _toml_string(BASELINE, "elk", "commit")
    if elk.get("release") != expected_release or elk.get("commit") != expected_commit:
        raise ValueError("Java report does not identify the pinned ELK release and commit")

    environment = _mapping(payload.get("environment"), "Java report environment")
    java_version = environment.get("java")
    if not isinstance(java_version, str) or not java_version.strip():
        raise ValueError("Java report environment.java must be nonempty")

    inputs = _mapping(payload.get("inputs"), "Java report inputs")
    expected_inputs = {
        "corpus_name": biomedical.name,
        "source_sha256": biomedical.source_sha256,
        "target_sha256": biomedical.target_sha256,
        "alignment_sha256": biomedical.alignment_sha256,
    }
    for name, expected in expected_inputs.items():
        if inputs.get(name) != expected:
            raise ValueError(f"Java report inputs.{name} does not match the biomedical corpus")

    expected_digests = {
        "source": biomedical.expected_source_semantic_completeness_sha256,
        "target": biomedical.expected_target_semantic_completeness_sha256,
        "composite": biomedical.expected_composite_semantic_completeness_sha256,
    }
    corpora = _mapping(payload.get("corpora"), "Java report corpora")
    result: dict[str, tuple[float, ...]] = {}
    for name in _BIOMEDICAL_VIEWS:
        row = _mapping(corpora.get(name), f"Java report corpora.{name}")
        if row.get("semantic_completeness_sha256") != expected_digests[name]:
            raise ValueError(
                f"Java report corpora.{name} semantic/completeness digest does not match"
            )
        samples = _positive_samples(
            row.get("warm_view_to_result_seconds"),
            f"Java report corpora.{name}.warm_view_to_result_seconds",
        )
        if len(samples) != measured_runs:
            raise ValueError(
                f"Java report corpora.{name} sample count does not match protocol.measured_runs"
            )
        result[name] = samples
    return result


def _phase_wall_samples(row: Mapping[str, object], phase: str, label: str) -> tuple[float, ...]:
    phase_row = _mapping(row.get(phase), f"{label}.{phase}")
    raw_samples = phase_row.get("samples")
    if not isinstance(raw_samples, list):
        raise RuntimeError(f"{label}.{phase}.samples must be a JSON array")
    samples: list[float] = []
    for index, raw in enumerate(raw_samples):
        sample = _mapping(raw, f"{label}.{phase}.samples[{index}]")
        wall = sample.get("wall_seconds")
        if isinstance(wall, bool) or not isinstance(wall, (int, float)):
            raise RuntimeError(f"{label}.{phase}.samples[{index}].wall_seconds must be numeric")
        value = float(wall)
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError(
                f"{label}.{phase}.samples[{index}].wall_seconds must be finite and positive"
            )
        samples.append(value)
    return tuple(samples)


def _native_view_to_result_samples(
    biomedical_result: Mapping[str, object],
) -> dict[str, tuple[float, ...]]:
    backends = _mapping(biomedical_result.get("backends"), "biomedical backends")
    rust = _mapping(backends.get("rust"), "biomedical Rust backend")
    views = _mapping(rust.get("views"), "biomedical Rust views")
    result: dict[str, tuple[float, ...]] = {}
    for name in _BIOMEDICAL_VIEWS:
        row = _mapping(views.get(name), f"biomedical Rust views.{name}")
        construction = _phase_wall_samples(row, "session_construction", name)
        classification = _phase_wall_samples(row, "classification", name)
        if len(construction) < 5 or len(construction) != len(classification):
            raise RuntimeError(
                f"biomedical Rust {name} requires at least five paired construction/"
                "classification samples"
            )
        result[name] = tuple(
            construction_sample + classification_sample
            for construction_sample, classification_sample in zip(
                construction, classification, strict=True
            )
        )
    return result


def _java_relative_comparison(
    biomedical_result: Mapping[str, object],
    java_samples: Mapping[str, Sequence[float]],
) -> dict[str, object]:
    native_samples = _native_view_to_result_samples(biomedical_result)
    per_corpus_max = _toml_number(
        MANIFEST,
        "thresholds",
        "native_java_per_corpus_ratio_max",
    )
    geometric_mean_max = _toml_number(
        MANIFEST,
        "thresholds",
        "native_java_geometric_mean_ratio_max",
    )
    rows: dict[str, dict[str, float]] = {}
    ratios: list[float] = []
    blockers: list[str] = []
    for name in _BIOMEDICAL_VIEWS:
        java = tuple(java_samples[name])
        if len(java) < 5:
            raise ValueError(f"Java report {name} requires at least five measured samples")
        native_median = statistics.median(native_samples[name])
        java_median = statistics.median(java)
        ratio = native_median / java_median
        ratios.append(ratio)
        rows[name] = {
            "native_median_seconds": native_median,
            "java_median_seconds": java_median,
            "native_to_java_ratio": ratio,
        }
        if ratio > per_corpus_max:
            blockers.append(
                f"{name}: native/Java median ratio {ratio:.6g} exceeds {per_corpus_max:.6g}"
            )
    geometric_mean_ratio = math.exp(statistics.fmean(math.log(ratio) for ratio in ratios))
    if geometric_mean_ratio > geometric_mean_max:
        blockers.append(
            "native/Java geometric-mean ratio "
            f"{geometric_mean_ratio:.6g} exceeds {geometric_mean_max:.6g}"
        )
    return {
        "gate_eligible": not blockers,
        "gate_blockers": blockers,
        "thresholds": {
            "per_corpus_ratio_max": per_corpus_max,
            "geometric_mean_ratio_max": geometric_mean_max,
        },
        "corpora": rows,
        "native_to_java_geometric_mean_ratio": geometric_mean_ratio,
    }


def _release_revision_blockers(
    revisions: Mapping[str, Mapping[str, object]],
) -> list[str]:
    blockers: list[str] = []
    for name in ("pyelk", "pyowl_core"):
        state = revisions.get(name)
        if state is None:
            blockers.append(f"{name}: revision state is missing")
            continue
        commit = state.get("commit")
        if (
            not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit) is None
        ):
            blockers.append(f"{name}: exact Git commit is unavailable")
        if state.get("dirty") is not False:
            blockers.append(f"{name}: release evidence requires a clean worktree")
    return blockers


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return result


def _identity_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be nonempty text")
    return value


def _current_rss_growth(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _release_metrics(results: Mapping[str, object]) -> dict[str, _ReleaseMetric]:
    metrics: dict[str, _ReleaseMetric] = {}

    end_to_end = _mapping(results.get("end-to-end"), "release results.end-to-end")
    standalone = _mapping(end_to_end.get("standalone"), "release end-to-end.standalone")
    fixture = _mapping(end_to_end.get("fixture"), "release end-to-end.fixture")
    metrics["end-to-end"] = _ReleaseMetric(
        median_seconds=_positive_number(
            standalone.get("median_seconds"),
            "release end-to-end standalone median_seconds",
        ),
        identity=(
            _identity_text(end_to_end.get("result_sha256"), "release end-to-end result_sha256"),
            _identity_text(fixture.get("source_sha256"), "release end-to-end source_sha256"),
        ),
    )

    boundary = _mapping(results.get("native-boundary"), "release results.native-boundary")
    native = _mapping(boundary.get("native"), "release native-boundary.native")
    parallel = _mapping(
        native.get("workers_parallel"),
        "release native-boundary.native.workers_parallel",
    )
    class_count = boundary.get("class_count")
    compiled_bytes = boundary.get("compiled_bytes")
    if (
        isinstance(class_count, bool)
        or not isinstance(class_count, int)
        or class_count < 2
        or isinstance(compiled_bytes, bool)
        or not isinstance(compiled_bytes, int)
        or compiled_bytes < 1
    ):
        raise ValueError("release native-boundary fixture identity is invalid")
    metrics["native-boundary"] = _ReleaseMetric(
        median_seconds=_positive_number(
            parallel.get("median_seconds"),
            "release native-boundary parallel median_seconds",
        ),
        identity=(class_count, compiled_bytes),
    )

    biomedical = _mapping(results.get("biomedical"), "release results.biomedical")
    biomedical_samples = _native_view_to_result_samples(biomedical)
    biomedical_backends = _mapping(biomedical.get("backends"), "release biomedical.backends")
    biomedical_rust = _mapping(
        biomedical_backends.get("rust"),
        "release biomedical.backends.rust",
    )
    biomedical_views = _mapping(
        biomedical_rust.get("views"),
        "release biomedical.backends.rust.views",
    )
    for name in _BIOMEDICAL_VIEWS:
        row = _mapping(biomedical_views.get(name), f"release biomedical Rust views.{name}")
        metrics[f"biomedical/{name}"] = _ReleaseMetric(
            median_seconds=statistics.median(biomedical_samples[name]),
            identity=(
                _identity_text(
                    row.get("semantic_completeness_sha256"),
                    f"release biomedical {name} semantic_completeness_sha256",
                ),
            ),
        )

    encoded = _mapping(
        results.get("encoded-ingestion"),
        "release results.encoded-ingestion",
    )
    workloads = _mapping(
        encoded.get("workloads"),
        "release encoded-ingestion.workloads",
    )
    for name in ("direct", "mmap", "overlay", "composite"):
        workload = _mapping(workloads.get(name), f"release encoded workload {name}")
        native_row = _mapping(
            workload.get("encoded_native"),
            f"release encoded workload {name}.encoded_native",
        )
        phases = _mapping(
            native_row.get("phases"),
            f"release encoded workload {name}.phases",
        )
        total = _mapping(
            phases.get("view_to_first_result"),
            f"release encoded workload {name}.view_to_first_result",
        )
        summary = _mapping(
            total.get("summary"),
            f"release encoded workload {name}.view_to_first_result.summary",
        )
        metrics[f"encoded/{name}"] = _ReleaseMetric(
            median_seconds=_positive_number(
                summary.get("median_seconds"),
                f"release encoded workload {name} median_seconds",
            ),
            identity=(
                _identity_text(
                    native_row.get("compiler_digest"),
                    f"release encoded workload {name} compiler_digest",
                ),
                _identity_text(
                    native_row.get("result_sha256"),
                    f"release encoded workload {name} result_sha256",
                ),
            ),
            current_rss_growth_bytes=_current_rss_growth(
                summary.get("maximum_current_rss_growth_bytes"),
                f"release encoded workload {name} maximum_current_rss_growth_bytes",
            ),
        )
    return metrics


def _validate_prior_release_report(
    payload: Mapping[str, object],
    *,
    machine_label: str,
    workers: int,
) -> dict[str, _ReleaseMetric]:
    if payload.get("schema") != "pyelk.integrated-benchmark/1":
        raise ValueError("prior-release report must use pyelk.integrated-benchmark/1")
    if (
        payload.get("suite") != "full"
        or payload.get("native") is not True
        or payload.get("enforced") is not True
    ):
        raise ValueError("prior-release report must be an enforced full native run")
    if payload.get("workers") != workers:
        raise ValueError("prior-release report workers do not match the current run")
    environment = _mapping(payload.get("environment"), "prior-release environment")
    if environment.get("machine_label") != machine_label:
        raise ValueError("prior-release report machine_label does not match the current run")
    manifest = _mapping(payload.get("manifest"), "prior-release manifest")
    current_manifest_sha256 = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    if (
        manifest.get("path") != MANIFEST.relative_to(ROOT).as_posix()
        or manifest.get("sha256") != current_manifest_sha256
    ):
        raise ValueError("prior-release report does not use the current performance manifest")
    revisions = _mapping(payload.get("revisions"), "prior-release revisions")
    revision_rows = {
        name: _mapping(revisions.get(name), f"prior-release revisions.{name}")
        for name in ("pyelk", "pyowl_core")
    }
    revision_blockers = _release_revision_blockers(revision_rows)
    if revision_blockers:
        raise ValueError(f"prior-release revision evidence is invalid: {revision_blockers}")
    results = _mapping(payload.get("results"), "prior-release results")
    return _release_metrics(results)


def _release_regression_comparison(
    current: Mapping[str, _ReleaseMetric],
    baseline: Mapping[str, _ReleaseMetric],
) -> dict[str, object]:
    if set(current) != set(baseline):
        raise ValueError("current and prior-release metric inventories differ")
    regression_max = _toml_number(
        MANIFEST,
        "thresholds",
        "release_regression_fraction_max",
    )
    ratio_max = 1.0 + regression_max
    rows: dict[str, dict[str, object]] = {}
    blockers: list[str] = []
    for name in sorted(current):
        current_row = current[name]
        baseline_row = baseline[name]
        if current_row.identity != baseline_row.identity:
            blockers.append(f"{name}: fixture or semantic identity differs from prior release")
        time_ratio = current_row.median_seconds / baseline_row.median_seconds
        if time_ratio > ratio_max:
            blockers.append(f"{name}: median time ratio {time_ratio:.6g} exceeds {ratio_max:.6g}")
        rss_ratio: float | None = None
        if current_row.current_rss_growth_bytes is not None:
            baseline_rss = baseline_row.current_rss_growth_bytes
            if baseline_rss is None:
                blockers.append(f"{name}: prior-release current-RSS evidence is missing")
            elif baseline_rss == 0:
                rss_ratio = 1.0 if current_row.current_rss_growth_bytes == 0 else math.inf
            else:
                rss_ratio = current_row.current_rss_growth_bytes / baseline_rss
            if rss_ratio is not None and rss_ratio > ratio_max:
                blockers.append(
                    f"{name}: current-RSS ratio {rss_ratio:.6g} exceeds {ratio_max:.6g}"
                )
        rows[name] = {
            "current_median_seconds": current_row.median_seconds,
            "prior_median_seconds": baseline_row.median_seconds,
            "time_ratio": time_ratio,
            "current_rss_growth_bytes": current_row.current_rss_growth_bytes,
            "prior_rss_growth_bytes": baseline_row.current_rss_growth_bytes,
            "rss_ratio": rss_ratio,
            "identity_matched": current_row.identity == baseline_row.identity,
        }
    return {
        "gate_eligible": not blockers,
        "gate_blockers": blockers,
        "regression_fraction_max": regression_max,
        "ratio_max": ratio_max,
        "metrics": rows,
    }


def _command(script: str, *arguments: str) -> list[str]:
    return [sys.executable, str(ROOT / "benchmarks" / script), *arguments]


def _suite(
    name: str,
    *,
    native: bool,
    workers: int,
    enforce: bool,
    native_path: Path | None,
    biomedical: BiomedicalInputs | None,
) -> list[tuple[str, list[str]]]:
    if name == "quick":
        repeats, warmups = "1", "0"
        commands = [
            (
                "snapshot-ingestion",
                _command(
                    "bench_snapshot_ingestion.py",
                    "--axioms",
                    "1000",
                    "--repeats",
                    repeats,
                ),
            ),
            (
                "property-saturation",
                _command(
                    "bench_properties.py",
                    "--properties",
                    "1000",
                    "--repeats",
                    repeats,
                    "--warmups",
                    warmups,
                ),
            ),
            (
                "class-saturation",
                _command(
                    "bench_saturation.py",
                    "--classes",
                    "1000",
                    "--repeats",
                    repeats,
                    "--warmups",
                    warmups,
                ),
            ),
            (
                "taxonomy",
                _command(
                    "bench_taxonomy.py",
                    "--sparse-nodes",
                    "1000",
                    "--dense-nodes",
                    "100",
                    "--repeats",
                    repeats,
                ),
            ),
        ]
        classes = "100"
    else:
        repeats, warmups = "5", "2"
        commands = [
            (
                "snapshot-ingestion",
                _command(
                    "bench_snapshot_ingestion.py",
                    "--axioms",
                    "1000000",
                    "--repeats",
                    repeats,
                ),
            ),
            (
                "property-saturation",
                _command(
                    "bench_properties.py",
                    "--properties",
                    "100000",
                    "--repeats",
                    repeats,
                    "--warmups",
                    warmups,
                ),
            ),
            (
                "class-saturation",
                _command(
                    "bench_saturation.py",
                    "--classes",
                    "100000",
                    "--repeats",
                    repeats,
                    "--warmups",
                    warmups,
                ),
            ),
            (
                "taxonomy",
                _command(
                    "bench_taxonomy.py",
                    "--sparse-nodes",
                    "100000",
                    "--dense-nodes",
                    "1000",
                    "--repeats",
                    repeats,
                ),
            ),
        ]
        classes = "10000"
    backend = "rust" if native else "python"
    end_to_end = _command(
        "bench_end_to_end.py",
        "--classes",
        classes,
        "--repeats",
        repeats,
        "--warmups",
        warmups,
        "--backend",
        backend,
        "--workers",
        str(workers),
    )
    if native_path is not None:
        end_to_end.extend(("--native-path", str(native_path)))
    commands.append(("end-to-end", end_to_end))
    if native:
        boundary = _command(
            "bench_native_boundary.py",
            "--classes",
            classes,
            "--repeats",
            repeats,
            "--warmups",
            warmups,
            "--workers",
            str(workers),
        )
        if enforce:
            boundary.append("--enforce")
        if native_path is not None:
            boundary.extend(("--native-path", str(native_path)))
        commands.append(("native-boundary", boundary))
        encoded_ingestion = _command(
            "bench_encoded_ingestion.py",
            "--classes",
            classes,
            "--repeats",
            repeats,
            "--warmups",
            warmups,
            "--workers",
            str(workers),
        )
        if enforce:
            encoded_ingestion.append("--enforce")
        else:
            encoded_ingestion.append("--experimental-producer")
        if native_path is not None:
            encoded_ingestion.extend(("--native-path", str(native_path)))
        commands.append(("encoded-ingestion", encoded_ingestion))
    if biomedical is not None:
        biomedical_command = _command(
            "bench_biomedical.py",
            *biomedical.command_arguments(),
            "--backends",
            "both" if native else "python",
            "--workers",
            str(workers),
            "--warmups",
            warmups,
            "--repeats",
            repeats,
        )
        if native and native_path is not None:
            biomedical_command.extend(("--native-path", str(native_path)))
        commands.append(("biomedical", biomedical_command))
    return commands


def _run(command: list[str], environment: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(
            f"benchmark failed ({' '.join(command)}):\n{completed.stderr or completed.stdout}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError(f"benchmark did not return a JSON object: {' '.join(command)}")
    return value


def run(
    *,
    suite: str,
    native: bool,
    workers: int,
    enforce: bool,
    java_report: Path | None,
    machine_label: str | None,
    native_path: Path | None,
    biomedical: BiomedicalInputs | None = None,
    prior_release_report: Path | None = None,
) -> dict[str, object]:
    if suite not in {"quick", "full"}:
        raise ValueError("suite must be 'quick' or 'full'")
    if not isinstance(native, bool) or not isinstance(enforce, bool):
        raise TypeError("native and enforce must be bool")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if machine_label is not None and not isinstance(machine_label, str):
        raise TypeError("machine_label must be str or None")
    if biomedical is not None and not isinstance(biomedical, BiomedicalInputs):
        raise TypeError("biomedical must be BiomedicalInputs or None")
    if enforce and not native:
        raise ValueError("enforce requires native")
    if enforce and suite != "full":
        raise ValueError("enforce requires the full suite")
    if enforce and not machine_label:
        raise ValueError("enforce requires a machine label")
    if enforce and biomedical is None:
        raise ValueError("enforce requires hash-pinned biomedical inputs")
    if enforce and biomedical is not None and not biomedical.has_semantic_expectations:
        raise ValueError("enforce requires caller-pinned biomedical semantic digests")
    if enforce and java_report is None:
        raise ValueError("enforce requires a same-machine pinned Java performance report")
    if enforce and prior_release_report is None:
        raise ValueError("enforce requires a same-machine prior-release performance report")
    if java_report is not None and not isinstance(java_report, Path):
        raise TypeError("java_report must be pathlib.Path or None")
    if prior_release_report is not None and not isinstance(prior_release_report, Path):
        raise TypeError("prior_release_report must be pathlib.Path or None")

    java: dict[str, object] | None = None
    java_samples: dict[str, tuple[float, ...]] | None = None
    if java_report is not None:
        data = java_report.read_bytes()
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Java report must contain a JSON object")
        if enforce:
            if machine_label is None or biomedical is None:  # pragma: no cover - guarded above
                raise AssertionError("enforcement inputs unexpectedly missing")
            java_samples = _validate_java_performance_report(
                payload,
                machine_label=machine_label,
                workers=workers,
                biomedical=biomedical,
            )
        java = {
            "path": os.fspath(java_report),
            "sha256": hashlib.sha256(data).hexdigest(),
            "payload": payload,
            "validated_for_enforcement": enforce,
            "comparison": None,
        }

    prior_release: dict[str, object] | None = None
    prior_release_metrics: dict[str, _ReleaseMetric] | None = None
    prior_payload: dict[str, object] | None = None
    if prior_release_report is not None:
        prior_data = prior_release_report.read_bytes()
        decoded_prior = json.loads(prior_data)
        if not isinstance(decoded_prior, dict):
            raise ValueError("prior-release report must contain a JSON object")
        prior_payload = decoded_prior
        if enforce:
            if machine_label is None:  # pragma: no cover - guarded above
                raise AssertionError("enforcement machine label unexpectedly missing")
            prior_release_metrics = _validate_prior_release_report(
                prior_payload,
                machine_label=machine_label,
                workers=workers,
            )
        prior_release = {
            "path": os.fspath(prior_release_report),
            "sha256": hashlib.sha256(prior_data).hexdigest(),
            "source": {
                "schema": prior_payload.get("schema"),
                "workers": prior_payload.get("workers"),
                "environment": prior_payload.get("environment"),
                "revisions": prior_payload.get("revisions"),
                "manifest": prior_payload.get("manifest"),
            },
            "validated_for_enforcement": enforce,
            "comparison": None,
        }

    initial_revisions: dict[str, dict[str, object]] | None = None
    if enforce:
        initial_revisions = {
            "pyelk": _git_state(ROOT),
            "pyowl_core": _git_state(ROOT.parent / "pyOWLCore"),
        }
        revision_blockers = _release_revision_blockers(initial_revisions)
        if revision_blockers:
            raise RuntimeError(
                f"release revision evidence is not gate-eligible: {revision_blockers}"
            )
        if prior_release is None or prior_payload is None:
            raise AssertionError("validated prior-release evidence unexpectedly missing")
        prior_revisions = _mapping(prior_payload.get("revisions"), "prior-release revisions")
        prior_pyelk = _mapping(
            prior_revisions.get("pyelk"),
            "prior-release revisions.pyelk",
        )
        if prior_pyelk.get("commit") == initial_revisions["pyelk"].get("commit"):
            raise ValueError("prior-release report must identify an earlier pyELK commit")

    environment = os.environ.copy()
    source_paths = [str(ROOT / "src")]
    sibling_core = ROOT.parent / "pyOWLCore" / "src"
    if sibling_core.is_dir():
        source_paths.append(str(sibling_core))
    existing = environment.get("PYTHONPATH")
    if existing:
        source_paths.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(source_paths)
    if native:
        environment.pop("PYELK_PURE_PYTHON", None)
    else:
        environment["PYELK_PURE_PYTHON"] = "1"
    started = time.perf_counter()
    results = {
        name: _run(command, environment)
        for name, command in _suite(
            suite,
            native=native,
            workers=workers,
            enforce=enforce,
            native_path=native_path,
            biomedical=biomedical,
        )
    }
    if enforce:
        biomedical_result = results.get("biomedical")
        if not isinstance(biomedical_result, dict):
            raise RuntimeError("enforcement did not produce a biomedical result")
        if biomedical_result.get("gate_eligible") is not True:
            blockers = biomedical_result.get("gate_blockers")
            raise RuntimeError(
                "biomedical benchmark evidence is not gate-eligible"
                + (f": {blockers}" if blockers else "")
            )
        encoded_result = results.get("encoded-ingestion")
        if not isinstance(encoded_result, dict):
            raise RuntimeError("enforcement did not produce an encoded-ingestion result")
        if encoded_result.get("gate_eligible") is not True:
            blockers = encoded_result.get("gate_blockers")
            raise RuntimeError(
                "encoded-ingestion benchmark evidence is not gate-eligible"
                + (f": {blockers}" if blockers else "")
            )
        if java is None or java_samples is None:  # pragma: no cover - guarded above
            raise AssertionError("validated Java evidence unexpectedly missing")
        comparison = _java_relative_comparison(biomedical_result, java_samples)
        java["comparison"] = comparison
        if comparison["gate_eligible"] is not True:
            raise RuntimeError(
                "Java-relative performance evidence is not gate-eligible: "
                f"{comparison['gate_blockers']}"
            )
        if prior_release is None or prior_release_metrics is None:  # pragma: no cover
            raise AssertionError("validated prior-release evidence unexpectedly missing")
        current_release_metrics = _release_metrics(results)
        release_comparison = _release_regression_comparison(
            current_release_metrics,
            prior_release_metrics,
        )
        prior_release["comparison"] = release_comparison
        if release_comparison["gate_eligible"] is not True:
            raise RuntimeError(
                "prior-release performance evidence is not gate-eligible: "
                f"{release_comparison['gate_blockers']}"
            )
    revisions = {
        "pyelk": _git_state(ROOT),
        "pyowl_core": _git_state(ROOT.parent / "pyOWLCore"),
    }
    if enforce:
        revision_blockers = _release_revision_blockers(revisions)
        if initial_revisions != revisions:
            revision_blockers.append("repository revisions changed during the benchmark run")
        if revision_blockers:
            raise RuntimeError(
                f"release revision evidence is not gate-eligible: {revision_blockers}"
            )
    return {
        "schema": "pyelk.integrated-benchmark/1",
        "suite": suite,
        "native": native,
        "workers": workers,
        "enforced": enforce,
        "elapsed_seconds": time.perf_counter() - started,
        "environment": {
            "cpu": platform.processor() or platform.machine(),
            "logical_cpu_count": os.cpu_count(),
            "machine_label": machine_label,
            "physical_memory_bytes": _physical_memory_bytes(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "revisions": revisions,
        "manifest": {
            "path": MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        },
        "java_comparison": java,
        "prior_release_comparison": prior_release,
        "results": results,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("quick", "full"), default="quick")
    parser.add_argument("--native", action="store_true", help="include Rust/native gates")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="enforce native performance thresholds (requires --native)",
    )
    parser.add_argument("--java-report", type=Path, help="attach a pinned external Java report")
    parser.add_argument(
        "--prior-release-report",
        type=Path,
        help="compare against an enforced report from the prior release",
    )
    parser.add_argument("--machine-label", help="stable label for a dedicated performance runner")
    parser.add_argument("--native-path", type=Path, help="workspace native library to benchmark")
    parser.add_argument("--biomedical-source", type=Path)
    parser.add_argument("--biomedical-source-sha256")
    parser.add_argument("--biomedical-source-axiom-count", type=int)
    parser.add_argument("--biomedical-source-entity-count", type=int)
    parser.add_argument("--biomedical-target", type=Path)
    parser.add_argument("--biomedical-target-sha256")
    parser.add_argument("--biomedical-target-axiom-count", type=int)
    parser.add_argument("--biomedical-target-entity-count", type=int)
    parser.add_argument("--biomedical-alignment", type=Path)
    parser.add_argument("--biomedical-alignment-sha256")
    parser.add_argument("--biomedical-name")
    parser.add_argument("--biomedical-origin")
    parser.add_argument("--biomedical-license")
    parser.add_argument("--biomedical-expected-source-semantic-completeness-sha256")
    parser.add_argument("--biomedical-expected-target-semantic-completeness-sha256")
    parser.add_argument("--biomedical-expected-composite-semantic-completeness-sha256")
    parser.add_argument("--output", type=Path, help="write the canonical JSON report here")
    return parser.parse_args()


def _biomedical_options(arguments: argparse.Namespace) -> BiomedicalInputs | None:
    names = (
        "source",
        "source_sha256",
        "source_axiom_count",
        "source_entity_count",
        "target",
        "target_sha256",
        "target_axiom_count",
        "target_entity_count",
        "alignment",
        "alignment_sha256",
        "name",
        "origin",
        "license",
    )
    values = {name: getattr(arguments, f"biomedical_{name}") for name in names}
    semantic_values = {
        "expected_source_semantic_completeness_sha256": (
            arguments.biomedical_expected_source_semantic_completeness_sha256
        ),
        "expected_target_semantic_completeness_sha256": (
            arguments.biomedical_expected_target_semantic_completeness_sha256
        ),
        "expected_composite_semantic_completeness_sha256": (
            arguments.biomedical_expected_composite_semantic_completeness_sha256
        ),
    }
    if all(value is None for value in values.values()) and all(
        value is None for value in semantic_values.values()
    ):
        return None
    missing = [name for name, value in values.items() if value is None]
    if missing:
        raise ValueError(
            "biomedical corpus options are all-or-nothing; missing: " + ", ".join(missing)
        )
    return BiomedicalInputs(
        **values,
        **semantic_values,
    )


def main() -> int:
    arguments = _arguments()
    if arguments.enforce and not arguments.native:
        raise SystemExit("--enforce requires --native")
    if arguments.enforce and arguments.suite != "full":
        raise SystemExit("--enforce requires --suite full")
    if arguments.enforce and not arguments.machine_label:
        raise SystemExit("--enforce requires --machine-label")
    try:
        biomedical = _biomedical_options(arguments)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if arguments.enforce and biomedical is None:
        raise SystemExit("--enforce requires the hash-pinned biomedical corpus options")
    if arguments.enforce and biomedical is not None and not biomedical.has_semantic_expectations:
        raise SystemExit("--enforce requires caller-pinned biomedical semantic digests")
    if arguments.enforce and arguments.java_report is None:
        raise SystemExit("--enforce requires --java-report from the same labelled machine")
    if arguments.enforce and arguments.prior_release_report is None:
        raise SystemExit("--enforce requires --prior-release-report from the same labelled machine")
    payload = run(
        suite=arguments.suite,
        native=arguments.native,
        workers=arguments.workers,
        enforce=arguments.enforce,
        java_report=arguments.java_report,
        machine_label=arguments.machine_label,
        native_path=arguments.native_path,
        biomedical=biomedical,
        prior_release_report=arguments.prior_release_report,
    )
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
