"""Conditional declaration of the private optional Rust extension.

The three supported build modes intentionally live here instead of being
inferred from compiler discovery.  In particular, published fallback wheels
must use ``PYELK_BUILD_PURE=1`` so setuptools never advertises a platform
extension and the resulting tag remains ``py3-none-any``.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.sdist import sdist

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from pyelk_build import build_reproducible_sdist  # noqa: E402


def _flag(name: str) -> bool:
    value = os.environ.get(name, "0")
    if value not in {"0", "1"}:
        raise RuntimeError(f"{name} must be either '0' or '1', got {value!r}")
    return value == "1"


pure_build = _flag("PYELK_BUILD_PURE")
require_native = _flag("PYELK_REQUIRE_NATIVE")
cibuildwheel = _flag("CIBUILDWHEEL")

# cibuildwheel is exclusively a native release-wheel lane in this project.
# It sets CIBUILDWHEEL=1 itself, so a broken/missing extension cannot silently
# degrade into a platform-tagged fallback artifact.
require_native = require_native or cibuildwheel

if pure_build and require_native:
    raise RuntimeError("PYELK_BUILD_PURE=1 conflicts with PYELK_REQUIRE_NATIVE=1")

rust_extensions = []
rust_manifest = root / "rust/pyelk-pyo3/Cargo.toml"


class ReproducibleSdist(sdist):
    """Honor ``SOURCE_DATE_EPOCH`` in both tar and gzip metadata."""

    def make_archive(
        self,
        base_name: str | os.PathLike[str],
        format: str,
        root_dir: str | os.PathLike[str] | bytes | os.PathLike[bytes] | None = None,
        base_dir: str | None = None,
        owner: str | None = None,
        group: str | None = None,
    ) -> str:
        raw_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        filesystem_root = None if root_dir is None else os.fspath(root_dir)
        if (
            format != "gztar"
            or raw_epoch is None
            or base_dir is None
            or isinstance(filesystem_root, bytes)
        ):
            return super().make_archive(
                base_name,
                format,
                root_dir,
                base_dir,
                owner,
                group,
            )
        try:
            epoch = int(raw_epoch)
        except ValueError as error:
            raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer") from error
        return build_reproducible_sdist(
            base_name,
            base_dir,
            epoch=epoch,
            root_dir=filesystem_root,
        )


if not pure_build and rust_manifest.is_file():
    from setuptools_rust import Binding, RustExtension

    encoded_flags = os.environ.get("CARGO_ENCODED_RUSTFLAGS")
    if encoded_flags:
        rust_flags = encoded_flags.split("\x1f")
    else:
        rust_flags = shlex.split(os.environ.get("RUSTFLAGS", ""))
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")).resolve()
    rust_flags.extend(
        (
            f"--remap-path-prefix={root}=pyelk-src",
            f"--remap-path-prefix={cargo_home}=cargo-home",
        )
    )
    rust_environment = os.environ.copy()
    rust_environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(rust_flags)

    rust_extensions.append(
        RustExtension(
            "pyelk._native",
            path=str(rust_manifest),
            binding=Binding.PyO3,
            optional=not require_native,
            cargo_manifest_args=("--locked",),
            env=rust_environment,
        )
    )
elif require_native:
    raise RuntimeError(f"native build requested but {rust_manifest} does not exist")

setup(
    rust_extensions=rust_extensions,
    cmdclass={"sdist": ReproducibleSdist},
    # This controls both the extension suffix and the wheel compatibility tag.
    # It is omitted in zero-extension mode so the fallback is truly universal.
    options={"bdist_wheel": {"py_limited_api": "cp310"}} if rust_extensions else {},
    zip_safe=False,
)
