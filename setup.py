"""Conditional declaration of the private optional Rust extension."""

from __future__ import annotations

import os
from pathlib import Path

from setuptools import setup


def _flag(name: str) -> bool:
    value = os.environ.get(name, "0")
    if value not in {"0", "1"}:
        raise RuntimeError(f"{name} must be either '0' or '1', got {value!r}")
    return value == "1"


pure_build = _flag("PYELK_BUILD_PURE")
require_native = _flag("PYELK_REQUIRE_NATIVE")

if pure_build and require_native:
    raise RuntimeError("PYELK_BUILD_PURE=1 conflicts with PYELK_REQUIRE_NATIVE=1")

rust_extensions = []
rust_manifest = Path("rust/pyelk-pyo3/Cargo.toml")

if not pure_build and rust_manifest.is_file():
    from setuptools_rust import Binding, RustExtension

    rust_extensions.append(
        RustExtension(
            "pyelk._native",
            path=str(rust_manifest),
            binding=Binding.PyO3,
            optional=not require_native,
            cargo_manifest_args=("--locked",),
        )
    )
elif require_native:
    raise RuntimeError(f"native build requested but {rust_manifest} does not exist")

setup(rust_extensions=rust_extensions)
