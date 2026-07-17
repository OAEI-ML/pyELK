#!/usr/bin/env python3
"""Run the integrated, semantic-checking pyELK benchmark corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "manifest.toml"


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
) -> dict[str, object]:
    if workers < 1:
        raise ValueError("workers must be positive")
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
    parser.add_argument("--output", type=Path, help="write the canonical JSON report here")
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    if arguments.enforce and not arguments.native:
        raise SystemExit("--enforce requires --native")
    payload = run(
        suite=arguments.suite,
        native=arguments.native,
        workers=arguments.workers,
        enforce=arguments.enforce,
        java_report=arguments.java_report,
        machine_label=arguments.machine_label,
        native_path=arguments.native_path,
    )
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
