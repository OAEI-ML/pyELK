from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.packaging


def test_distribution_workflow_enforces_reproducibility_and_external_audits() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")

    assert "cmp dist/*.tar.gz rebuilt/*.tar.gz" in workflow
    assert "cmp dist/*-py3-none-any.whl rebuilt/*-py3-none-any.whl" in workflow
    assert "abi3audit==0.0.26" in workflow
    assert "auditwheel==6.7.0" in workflow
    assert "delocate==0.13.0" in workflow
    assert "delvewheel==1.13.0" in workflow
    assert 'check "$wheel" --expect native-wheel --external' in workflow


def test_every_supported_later_cpython_exercises_glibc_musl_macos_and_windows() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")

    assert workflow.count('python: ["3.11", "3.12", "3.13", "3.14"]') == 2
    for lane in (
        "linux-x86_64",
        "linux-aarch64",
        "macos-x86_64",
        "macos-arm64",
        "windows-amd64",
    ):
        assert lane in workflow
    assert "PYTHON_IMAGE: python:${{ matrix.python }}-alpine" in workflow
