from __future__ import annotations

import gzip
import os
import runpy
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pyelk_build
import pytest

ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.packaging


class _RustExtension:
    def __init__(self, target: str, **kwargs: object) -> None:
        self.target = target
        self.kwargs = kwargs


class _Sdist:
    def make_archive(self, *args: object, **kwargs: object) -> str:
        del args, kwargs
        return "fallback.tar.gz"


def _evaluate(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
    *,
    script: Path = ROOT / "setup.py",
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    setuptools = ModuleType("setuptools")
    setuptools.setup = lambda **kwargs: captured.update(kwargs)  # type: ignore[attr-defined]
    setuptools_command = ModuleType("setuptools.command")
    setuptools_sdist = ModuleType("setuptools.command.sdist")
    setuptools_sdist.sdist = _Sdist  # type: ignore[attr-defined]
    setuptools_rust = ModuleType("setuptools_rust")
    setuptools_rust.Binding = SimpleNamespace(PyO3="pyo3")  # type: ignore[attr-defined]
    setuptools_rust.RustExtension = _RustExtension  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "setuptools", setuptools)
    monkeypatch.setitem(sys.modules, "setuptools.command", setuptools_command)
    monkeypatch.setitem(sys.modules, "setuptools.command.sdist", setuptools_sdist)
    monkeypatch.setitem(sys.modules, "setuptools_rust", setuptools_rust)
    for name in ("CIBUILDWHEEL", "PYELK_BUILD_PURE", "PYELK_REQUIRE_NATIVE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    runpy.run_path(str(script), run_name="__main__")
    return captured


def test_default_mode_declares_an_optional_locked_abi3_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _evaluate(monkeypatch, {})
    extension = captured["rust_extensions"][0]
    assert extension.target == "pyelk._native"
    assert extension.kwargs["binding"] == "pyo3"
    assert extension.kwargs["optional"] is True
    assert extension.kwargs["cargo_manifest_args"] == ("--locked",)
    assert extension.kwargs["env"]["PATH"] == os.environ["PATH"]
    assert captured["options"] == {"bdist_wheel": {"py_limited_api": "cp310"}}
    assert captured["cmdclass"]["sdist"].__name__ == "ReproducibleSdist"
    flags = extension.kwargs["env"]["CARGO_ENCODED_RUSTFLAGS"]
    assert "--remap-path-prefix=" in flags


@pytest.mark.parametrize(
    "values",
    [
        {"PYELK_REQUIRE_NATIVE": "1"},
        {"CIBUILDWHEEL": "1"},
    ],
)
def test_release_modes_make_native_failure_fatal(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
) -> None:
    captured = _evaluate(monkeypatch, values)
    assert captured["rust_extensions"][0].kwargs["optional"] is False


def test_pure_mode_declares_no_extension_or_abi3_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _evaluate(monkeypatch, {"PYELK_BUILD_PURE": "1"})
    assert captured["rust_extensions"] == []
    assert captured["options"] == {}


@pytest.mark.parametrize(
    "values",
    [
        {"PYELK_BUILD_PURE": "1", "PYELK_REQUIRE_NATIVE": "1"},
        {"PYELK_BUILD_PURE": "1", "CIBUILDWHEEL": "1"},
    ],
)
def test_conflicting_modes_fail_before_build(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError, match="conflicts"):
        _evaluate(monkeypatch, values)


@pytest.mark.parametrize("name", ["PYELK_BUILD_PURE", "PYELK_REQUIRE_NATIVE", "CIBUILDWHEEL"])
def test_build_flags_are_strict(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    with pytest.raises(RuntimeError, match="must be either"):
        _evaluate(monkeypatch, {name: "yes"})


def test_missing_manifest_falls_back_only_in_optional_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "setup.py"
    script.write_bytes((ROOT / "setup.py").read_bytes())
    captured = _evaluate(monkeypatch, {}, script=script)
    assert captured["rust_extensions"] == []
    with pytest.raises(RuntimeError, match="native build requested"):
        _evaluate(monkeypatch, {"PYELK_REQUIRE_NATIVE": "1"}, script=script)


def test_source_archive_normalizes_gzip_tar_metadata_and_order(tmp_path: Path) -> None:
    source = tmp_path / "pyelk_reasoner-0.2.0"
    nested = source / "package"
    nested.mkdir(parents=True)
    regular = nested / "module.py"
    executable = source / "build-tool"
    regular.write_text("VALUE = 1\n", encoding="utf-8")
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    epoch = 1_735_689_600

    first = Path(
        pyelk_build.build_reproducible_sdist(
            tmp_path / "first",
            source.name,
            epoch=epoch,
            root_dir=tmp_path,
        )
    )
    os.utime(source, (epoch + 100, epoch + 100))
    os.utime(regular, (epoch + 200, epoch + 200))
    second = Path(
        pyelk_build.build_reproducible_sdist(
            tmp_path / "second",
            source.name,
            epoch=epoch,
            root_dir=tmp_path,
        )
    )

    assert first.read_bytes() == second.read_bytes()
    assert int.from_bytes(first.read_bytes()[4:8], "little") == epoch
    with (
        gzip.open(first, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r:") as archive,
    ):
        members = archive.getmembers()
    assert all(member.mtime == epoch for member in members)
    assert all(
        (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "") for member in members
    )
    modes = {member.name: member.mode for member in members}
    assert modes[f"{source.name}/package/module.py"] == 0o644
    assert modes[f"{source.name}/build-tool"] == 0o755
