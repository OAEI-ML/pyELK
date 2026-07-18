from __future__ import annotations

import re
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


def test_rust_workflow_pins_toolchains_and_enforces_quality_gates() -> None:
    workflow = (ROOT / ".github/workflows/rust.yml").read_text(encoding="utf-8")

    assert "  push:" in workflow
    assert "  pull_request:" in workflow
    assert "  schedule:" in workflow
    assert "  workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert 'RUST_TOOLCHAIN: "1.97.1"' in workflow
    assert 'FUZZ_TOOLCHAIN: "nightly-2026-07-14"' in workflow
    assert 'CARGO_FUZZ_VERSION: "0.12.0"' in workflow
    assert (
        workflow.count(
            'cargo +"$FUZZ_TOOLCHAIN" install cargo-fuzz --version "$CARGO_FUZZ_VERSION" --locked'
        )
        == 2
    )
    assert "cargo fmt --all -- --check" in workflow
    assert (
        "cargo clippy --locked --workspace --all-targets --all-features -- -D warnings" in workflow
    )
    assert "cargo test --locked --workspace --all-features" in workflow

    action_references = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflow, flags=re.MULTILINE)
    assert action_references
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in action_references)
    assert workflow.count("persist-credentials: false") == 3


def test_rust_workflow_fuzzes_both_decoders_with_bounded_pr_and_scheduled_runs() -> None:
    workflow = (ROOT / ".github/workflows/rust.yml").read_text(encoding="utf-8")

    assert "target: [ir_decoder, query_decoder]" in workflow
    assert workflow.count("target: [ir_decoder, query_decoder]") == 2
    assert "if: github.event_name != 'schedule'" in workflow
    assert "if: github.event_name == 'schedule'" in workflow
    assert workflow.count('cargo +"$FUZZ_TOOLCHAIN" fuzz run --fuzz-dir rust/fuzz') == 2
    assert "-runs=256" in workflow
    assert "-seed=424242" in workflow
    assert "-max_len=65536" in workflow
    assert "-max_total_time=900" in workflow
    assert "-timeout=10" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "rust/fuzz/artifacts/${{ matrix.target }}/" in workflow
