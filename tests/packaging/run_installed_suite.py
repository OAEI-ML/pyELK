#!/usr/bin/env python3
"""Run the installed suite under one explicitly selected backend."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("python", "rust"), required=True)
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
    smoke_result = os.spawnv(
        os.P_WAIT,
        sys.executable,
        [
            sys.executable,
            str(Path(__file__).with_name("installed_smoke.py")),
            "--expected-backend",
            args.backend,
            *(["--force-python"] if args.backend == "python" else []),
        ],
    )
    if smoke_result:
        return smoke_result
    return pytest.main(
        [
            str(source_root / "tests"),
            "-m",
            "not java_oracle and not packaging and not performance",
            "--strict-config",
            "--strict-markers",
            "-q",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
