#!/usr/bin/env python3
"""Install one local artifact offline and run the compiler/JRE-free smoke test."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _venv_executable(root: Path, name: str) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return root / scripts / f"{name}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--expected-backend", choices=("python", "rust"), required=True)
    parser.add_argument("--force-python", action="store_true")
    args = parser.parse_args()

    artifact = args.artifact.resolve()
    wheelhouse = args.wheelhouse.resolve()
    smoke = Path(__file__).with_name("installed_smoke.py").resolve()
    with tempfile.TemporaryDirectory(prefix="pyelk-installed-") as temporary:
        environment = Path(temporary) / "venv"
        subprocess.run([str(args.python), "-m", "venv", str(environment)], check=True)
        python = _venv_executable(environment, "python")
        pip = [str(python), "-m", "pip"]
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PATH"] = str(python.parent)
        assert all(
            shutil.which(command, path=env["PATH"]) is None
            for command in ("java", "cargo", "rustc", "cc", "gcc", "clang")
        )
        subprocess.run(
            [
                *pip,
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--no-cache-dir",
                str(artifact),
            ],
            check=True,
            cwd=temporary,
            env=env,
        )
        command = [str(python), str(smoke), "--expected-backend", args.expected_backend]
        if args.force_python:
            command.append("--force-python")
        subprocess.run(command, check=True, cwd=temporary, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
