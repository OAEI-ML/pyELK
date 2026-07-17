#!/usr/bin/env python3
"""Verify pip chooses native when compatible and universal when unsupported."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def _download(
    index: Path,
    destination: Path,
    version: str,
    *,
    platform: str | None = None,
) -> Path:
    destination.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-index",
        "--find-links",
        str(index),
        "--only-binary=:all:",
        "--no-deps",
        "--dest",
        str(destination),
    ]
    if platform is not None:
        command.extend(
            [
                "--platform",
                platform,
                "--implementation",
                "cp",
                "--python-version",
                "310",
                "--abi",
                "cp310",
            ]
        )
    command.append(f"pyelk-reasoner=={version}")
    subprocess.run(command, check=True)
    wheels = tuple(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one selected wheel, found {wheels}")
    return wheels[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--version")
    args = parser.parse_args()
    version = args.version
    if version is None:
        pure_wheels = tuple(args.index.glob("pyelk_reasoner-*-py3-none-any.whl"))
        if len(pure_wheels) != 1:
            raise RuntimeError(f"cannot infer version from fallback wheels: {pure_wheels}")
        version = (
            pure_wheels[0].name.removeprefix("pyelk_reasoner-").removesuffix("-py3-none-any.whl")
        )
    with tempfile.TemporaryDirectory(prefix="pyelk-index-") as temporary:
        root = Path(temporary)
        compatible = _download(args.index, root / "compatible", version)
        unsupported = _download(
            args.index,
            root / "unsupported",
            version,
            platform="win_arm64",
        )
    if "-cp310-abi3-" not in compatible.name:
        raise RuntimeError(f"compatible pip simulation did not prefer native: {compatible.name}")
    if not unsupported.name.endswith("-py3-none-any.whl"):
        raise RuntimeError(f"unsupported simulation did not select fallback: {unsupported.name}")
    print(f"pip preference passed: native={compatible.name}, fallback={unsupported.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
