#!/usr/bin/env python3
"""Run the integrated, semantic-checking pyELK benchmark corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "manifest.toml"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    if not isinstance(pages, int) or not isinstance(page_size, int):
        return None
    return pages * page_size


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
) -> dict[str, object]:
    if suite not in {"quick", "full"}:
        raise ValueError("suite must be 'quick' or 'full'")
    if not isinstance(native, bool) or not isinstance(enforce, bool):
        raise TypeError("native and enforce must be bool")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
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
    java: dict[str, object] | None = None
    if java_report is not None:
        data = java_report.read_bytes()
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Java report must contain a JSON object")
        java = {
            "path": os.fspath(java_report),
            "sha256": hashlib.sha256(data).hexdigest(),
            "payload": payload,
        }
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
        "revisions": {
            "pyelk": _git_state(ROOT),
            "pyowl_core": _git_state(ROOT.parent / "pyOWLCore"),
        },
        "manifest": {
            "path": MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        },
        "java_comparison": java,
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
    payload = run(
        suite=arguments.suite,
        native=arguments.native,
        workers=arguments.workers,
        enforce=arguments.enforce,
        java_report=arguments.java_report,
        machine_label=arguments.machine_label,
        native_path=arguments.native_path,
        biomedical=biomedical,
    )
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
