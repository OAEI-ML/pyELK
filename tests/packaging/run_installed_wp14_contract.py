#!/usr/bin/env python3
"""Run the bounded WP14 encoded compiler contract against an installed native wheel."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

CONTRACT_NODE_IDS = (
    "tests/backends/test_rust_core.py::test_native_handshake_and_defensive_decoder",
    "tests/backends/test_rust_core.py::test_hidden_direct_encoded_session_matches_scalar_wire",
    (
        "tests/backends/test_rust_core.py"
        "::test_hidden_public_facade_runs_entirely_from_encoded_native_session"
    ),
)


def main() -> int:
    source_root = Path(__file__).resolve().parents[2]
    os.environ["PATH"] = str(Path(sys.executable).resolve().parent)
    os.environ["PYELK_BACKEND"] = "rust"
    os.environ["PYELK_PURE_PYTHON"] = "0"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    assert all(
        shutil.which(command) is None
        for command in ("java", "javac", "cargo", "rustc", "cc", "gcc", "clang")
    )

    def deny_network(event: str, _arguments: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
            raise RuntimeError(f"installed WP14 contract attempted network access: {event}")

    sys.addaudithook(deny_network)

    import pytest

    import pyelk

    installed = Path(pyelk.__file__).resolve()
    assert source_root not in installed.parents, f"source checkout imported: {installed}"
    native_spec = importlib.util.find_spec("pyelk._native")
    assert native_spec is not None and native_spec.origin is not None
    assert installed.parent in Path(native_spec.origin).resolve().parents

    sys.path.insert(0, str(source_root))
    selected: list[str] = []
    for node_id in CONTRACT_NODE_IDS:
        relative, test_name = node_id.split("::", 1)
        selected.append(f"{source_root / relative}::{test_name}")
    return pytest.main(["-q", "-p", "no:cacheprovider", *selected])


if __name__ == "__main__":
    raise SystemExit(main())
