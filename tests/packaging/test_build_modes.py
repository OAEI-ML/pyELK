from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.packaging


class _RustExtension:
    def __init__(self, target: str, **kwargs: object) -> None:
        self.target = target
        self.kwargs = kwargs


def _evaluate(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
    *,
    script: Path = ROOT / "setup.py",
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    setuptools = ModuleType("setuptools")
    setuptools.setup = lambda **kwargs: captured.update(kwargs)  # type: ignore[attr-defined]
    setuptools_rust = ModuleType("setuptools_rust")
    setuptools_rust.Binding = SimpleNamespace(PyO3="pyo3")  # type: ignore[attr-defined]
    setuptools_rust.RustExtension = _RustExtension  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "setuptools", setuptools)
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
