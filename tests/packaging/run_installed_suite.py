#!/usr/bin/env python3
"""Run the installed suite under one explicitly selected backend."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("python", "rust"), required=True)
    parser.add_argument(
        "--core-backend",
        choices=("python", "native"),
        required=True,
    )
    parser.add_argument(
        "--expected-ingestion",
        choices=("scalar-python", "scalar-wire", "encoded-native"),
        required=True,
    )
    args = parser.parse_args()
    if args.backend == "python":
        os.environ["PYELK_PURE_PYTHON"] = "1"
        os.environ.pop("PYELK_BACKEND", None)
    else:
        os.environ["PYELK_PURE_PYTHON"] = "0"
        os.environ["PYELK_BACKEND"] = "rust"
    os.environ["PATH"] = str(Path(sys.executable).resolve().parent)
    assert all(
        shutil.which(command) is None
        for command in ("java", "javac", "cargo", "rustc", "cc", "gcc", "clang")
    )

    import pytest

    import pyelk

    source_root = Path(__file__).resolve().parents[2]
    installed = Path(pyelk.__file__).resolve()
    assert source_root not in installed.parents, f"source checkout imported: {installed}"
    native_spec = importlib.util.find_spec("pyelk._native")
    installed_native = (
        native_spec is not None
        and native_spec.origin is not None
        and installed.parent in Path(native_spec.origin).resolve().parents
    )
    smoke = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("installed_smoke.py")),
            "--expected-backend",
            args.backend,
            "--expected-core-backend",
            args.core_backend,
            "--expected-ingestion",
            args.expected_ingestion,
            *(["--force-python"] if args.backend == "python" else []),
        ],
        check=False,
    )
    if smoke.returncode:
        return smoke.returncode
    parity_command = [
        sys.executable,
        str(source_root / "tests" / "parity" / "runner.py"),
        "--backend",
        args.backend,
        "--workers",
        "1",
    ]
    if args.backend == "rust":
        parity_command.extend(("--workers", "0"))
    parity = subprocess.run(parity_command, check=False)
    if parity.returncode:
        return parity.returncode
    # The installed smoke and unified runner above prove the requested artifact backend.
    # Restore neutral selection for tests that deliberately exercise environment policy.
    os.environ.pop("PYELK_PURE_PYTHON", None)
    os.environ.pop("PYELK_BACKEND", None)
    sys.path.insert(0, str(source_root))
    pytest_arguments = [
        str(source_root / "tests"),
        "-m",
        "not java_oracle and not packaging and not performance",
        "--strict-config",
        "--strict-markers",
        "-q",
    ]
    if not installed_native:
        pytest_arguments[1:1] = (
            "--ignore",
            str(source_root / "tests" / "backends"),
        )
    return pytest.main(pytest_arguments)


if __name__ == "__main__":
    raise SystemExit(main())
