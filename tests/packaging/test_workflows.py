from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.packaging


def test_foundation_uses_compiler_free_auto_fallback_without_masking_backend_tests() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    global_configuration = workflow.split("jobs:", maxsplit=1)[0]

    assert 'PYELK_BUILD_PURE: "1"' in global_configuration
    assert "PYELK_PURE_PYTHON" not in global_configuration
    assert "Verify external toolchains are absent" in workflow
    assert "python -m pytest" in workflow
    assert ".wheel-venv/bin/python -m pip install dist/*.whl" in workflow
    assert "pip install --no-deps dist/*.whl" not in workflow


def test_distribution_workflow_enforces_reproducibility_and_external_audits() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "cmp dist/*.tar.gz rebuilt/*.tar.gz" in workflow
    assert "cmp dist/*-py3-none-any.whl rebuilt/*-py3-none-any.whl" in workflow
    assert "abi3audit==0.0.26" in workflow
    assert "auditwheel==6.7.0" in workflow
    assert "delocate==0.13.0" in workflow
    assert "delvewheel==1.13.0" in workflow
    assert 'check "$wheel" --expect native-wheel --external' in workflow
    assert "rustup_version=1.28.2" in pyproject
    assert pyproject.count("rustup_sha256=") == 2
    assert "musllinux_rust=1.87.0-r1" in pyproject
    assert "musllinux_cargo=1.87.0-r1" in pyproject
    assert 'apk add --no-cache "rust=$musllinux_rust" "cargo=$musllinux_cargo"' in pyproject
    assert "sha256sum -c" in pyproject
    assert "https://sh.rustup.rs" not in pyproject
    assert re.search(r"\|\s*(?:sh|bash)(?:\s|$)", pyproject) is None
    assert 'before-test = "python -m pip install' in pyproject
    assert "test-requires" not in pyproject
    assert "CIBW_TEST_SKIP: ${{ runner.os == 'Windows' && '*' || '' }}" in workflow
    assert "CIBW_TEST_SKIP_WINDOWS" not in workflow
    assert "Select CPython 3.10 for Windows installed-wheel tests" in workflow
    windows_test = workflow.split(
        "- name: Exercise Windows native wheel with Rust and forced Python",
        maxsplit=1,
    )[1].split("- name: Install pinned Linux", maxsplit=1)[0]
    assert "run_installed_wp14_contract.py" in windows_test
    assert windows_test.count("run_installed_suite.py") == 2
    assert "--backend rust" in windows_test
    assert "--backend python" in windows_test
    assert windows_test.count("--core-backend native") == 2
    assert "--core-backend python" not in windows_test
    compiler_free = workflow.split("compiler-free-installed:", maxsplit=1)[1].split(
        "native-wheels:",
        maxsplit=1,
    )[0]
    assert compiler_free.count("--force-python") == 2
    assert "--platform any" in compiler_free


def test_distribution_workflow_stages_revalidated_supply_chain_evidence() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")

    assert workflow.count("python -m tools.supply_chain --check") == 2
    assert workflow.count("PYTHONPATH: ${{ github.workspace }}") == 3
    assert "reports/release/0.1.1" in workflow
    assert "name: supply-chain-evidence" in workflow
    assert "path: supply-chain" in workflow
    assert "supply-chain/*.json" in workflow
    assert "name: release-bundle" in workflow
    assert "name: release-evidence" in workflow
    release_bundle = workflow.split("name: release-bundle", maxsplit=1)[1].split(
        "- uses:",
        maxsplit=1,
    )[0]
    assert "artifacts/*.whl" in release_bundle
    assert "artifacts/*.tar.gz" in release_bundle
    assert "supply-chain" not in release_bundle
    assert workflow.count('"pyowl-core==0.1.1"') == 4


def test_atomic_release_revalidates_evidence_and_keeps_publish_input_distribution_only() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert workflow.count("name: release-bundle") == 2
    assert "name: release-evidence" in workflow
    assert "path: artifacts" in workflow
    assert "path: supply-chain" in workflow
    assert (
        "python -m tools.supply_chain --check --require-approval --output-dir supply-chain"
        in workflow
    )
    assert "PYTHONPATH: ${{ github.workspace }}" in workflow
    assert (
        "python -m tools.release_manifest artifacts "
        '--source-revision "${GITHUB_SHA}" '
        "--output supply-chain/artifact-manifest.json --check"
    ) in workflow
    assert "assert all(path.is_file() for path in artifacts), artifacts" in workflow
    assert "packages-dir: artifacts" in workflow
    assert "group: release-${{ github.ref }}" in workflow
    assert "cancel-in-progress: false" in workflow
    publish = workflow.split("  publish:", maxsplit=1)[1]
    assert "startsWith(github.ref, 'refs/tags/v')" in publish
    assert "timeout-minutes: 10" in publish
    assert "permissions:\n      id-token: write" in publish
    assert "api-token" not in publish
    assert "skip-existing: false" in publish
    assert "skip-existing: true" not in workflow


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
    assert "PYTHON_IMAGE: ${{ matrix.image }}" in workflow
    assert workflow.count("-alpine@sha256:") == 4
    assert "PYTHON_IMAGE: python:${{ matrix.python }}-alpine" not in workflow


def test_rust_workflow_pins_toolchains_and_enforces_quality_gates() -> None:
    workflow = (ROOT / ".github/workflows/rust.yml").read_text(encoding="utf-8")
    toolchain = (ROOT / "rust-toolchain.toml").read_text(encoding="utf-8")

    assert "  push:" in workflow
    assert "  pull_request:" in workflow
    assert "  schedule:" in workflow
    assert "  workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert 'RUST_TOOLCHAIN: "1.97.1"' in workflow
    assert 'FUZZ_TOOLCHAIN: "nightly-2026-07-14"' in workflow
    assert 'CARGO_FUZZ_VERSION: "0.13.2"' in workflow
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
    assert 'channel = "1.97.1"' in toolchain
    assert 'profile = "minimal"' in toolchain
    assert "components" not in toolchain

    action_references = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflow, flags=re.MULTILINE)
    assert action_references
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in action_references)
    assert workflow.count("persist-credentials: false") == 3


def test_rust_workflow_fuzzes_all_native_decoders_with_bounded_pr_and_scheduled_runs() -> None:
    workflow = (ROOT / ".github/workflows/rust.yml").read_text(encoding="utf-8")

    targets = "target: [ir_decoder, query_decoder, encoded_compiler]"
    assert workflow.count(targets) == 2
    assert (ROOT / "rust/fuzz/fuzz_targets/encoded_compiler.rs").is_file()
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
