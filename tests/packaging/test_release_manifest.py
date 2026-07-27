from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import tools.release_manifest as RELEASE_MANIFEST
from tools.check_artifact import ArtifactReport
from tools.release_manifest import (
    ManifestError,
    bind_artifact,
    build_manifest,
    generate_manifest,
)

from tests.packaging.test_artifact_audit import _wheel

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.packaging

_MATRIX = {
    "pyelk_reasoner-0.1.0.dev0.tar.gz": ("sdist", ()),
    "pyelk_reasoner-0.1.0.dev0-py3-none-any.whl": ("pure-wheel", ("py3-none-any",)),
    "pyelk_reasoner-0.1.0.dev0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": (
        "native-wheel",
        (
            "cp310-abi3-manylinux_2_17_x86_64",
            "cp310-abi3-manylinux2014_x86_64",
        ),
    ),
    "pyelk_reasoner-0.1.0.dev0-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl": (
        "native-wheel",
        (
            "cp310-abi3-manylinux_2_17_aarch64",
            "cp310-abi3-manylinux2014_aarch64",
        ),
    ),
    "pyelk_reasoner-0.1.0.dev0-cp310-abi3-musllinux_1_2_x86_64.whl": (
        "native-wheel",
        ("cp310-abi3-musllinux_1_2_x86_64",),
    ),
    "pyelk_reasoner-0.1.0.dev0-cp310-abi3-musllinux_1_2_aarch64.whl": (
        "native-wheel",
        ("cp310-abi3-musllinux_1_2_aarch64",),
    ),
    "pyelk_reasoner-0.1.0.dev0-cp310-abi3-macosx_10_12_x86_64.whl": (
        "native-wheel",
        ("cp310-abi3-macosx_10_12_x86_64",),
    ),
    "pyelk_reasoner-0.1.0.dev0-cp310-abi3-macosx_11_0_arm64.whl": (
        "native-wheel",
        ("cp310-abi3-macosx_11_0_arm64",),
    ),
    "pyelk_reasoner-0.1.0.dev0-cp310-abi3-win_amd64.whl": (
        "native-wheel",
        ("cp310-abi3-win_amd64",),
    ),
}


def _make_report(
    path: Path,
    kind: str,
    tags: tuple[str, ...],
    expected: str = "auto",
) -> ArtifactReport:
    if expected != "auto" and expected != kind:
        raise AssertionError(f"unexpected fixture audit request: {expected} != {kind}")
    return ArtifactReport(
        path=str(path.resolve()),
        kind=kind,
        name="pyelk-reasoner",
        version="0.1.0.dev0",
        requires_python=">=3.10",
        tags=tags,
        native_members=("pyelk/_native.abi3.so",) if kind == "native-wheel" else (),
        python_hashes={},
        metadata_sha256="1" * 64,
        legal_payload_sha256="2" * 64,
        archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _report(path: Path, expected: str = "auto") -> ArtifactReport:
    kind, tags = _MATRIX[path.name]
    return _make_report(path, kind, tags, expected)


def _artifacts(tmp_path: Path) -> Path:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for filename in reversed(tuple(_MATRIX)):
        (artifacts / filename).write_bytes(f"fixture:{filename}".encode())
    return artifacts


def test_one_artifact_binding_uses_exact_audited_file_identity(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, native=False)

    binding = bind_artifact(wheel)

    assert binding.filename == wheel.name
    assert binding.sha256 == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert binding.size == wheel.stat().st_size
    assert binding.variant == "pure-wheel"


def test_exact_matrix_manifest_is_canonical_and_verifiable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts(tmp_path)
    monkeypatch.setattr(RELEASE_MANIFEST, "inspect_artifact", _report)
    output = tmp_path / "evidence" / "artifact-manifest.json"

    first = build_manifest(artifacts)
    second = build_manifest(artifacts)
    generate_manifest(artifacts, output)
    generated = output.read_text(encoding="utf-8")
    generate_manifest(artifacts, output, check=True)

    assert first == second
    assert json.loads(generated) == first
    assert generated == json.dumps(first, indent=2, sort_keys=True) + "\n"
    assert [row["filename"] for row in first["artifacts"]] == sorted(_MATRIX)
    assert {row["variant"] for row in first["artifacts"]} == RELEASE_MANIFEST._EXPECTED_VARIANTS


def test_matrix_rejects_missing_or_foreign_direct_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts(tmp_path)
    monkeypatch.setattr(RELEASE_MANIFEST, "inspect_artifact", _report)
    (artifacts / next(iter(_MATRIX))).unlink()
    with pytest.raises(ManifestError, match="exactly nine"):
        build_manifest(artifacts)

    (artifacts / "foreign.txt").write_text("foreign", encoding="utf-8")
    with pytest.raises(ManifestError, match="unsupported entry"):
        build_manifest(artifacts)


def test_matrix_rejects_a_non_tier_one_native_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts(tmp_path)
    windows = artifacts / "pyelk_reasoner-0.1.0.dev0-cp310-abi3-win_amd64.whl"
    arm = artifacts / "pyelk_reasoner-0.1.0.dev0-cp310-abi3-win_arm64.whl"
    windows.rename(arm)

    def inspect_with_arm64(path: Path, expected: str = "auto") -> ArtifactReport:
        if path == arm:
            return _make_report(
                arm,
                "native-wheel",
                ("cp310-abi3-win_arm64",),
                expected,
            )
        return _report(path, expected)

    monkeypatch.setattr(RELEASE_MANIFEST, "inspect_artifact", inspect_with_arm64)
    with pytest.raises(ManifestError, match="no tier-one matrix variant"):
        build_manifest(artifacts)


def test_binding_rejects_an_artifact_changed_during_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "pyelk_reasoner-0.1.0.dev0-py3-none-any.whl"
    wheel.write_bytes(b"before")

    def mutate(path: Path, expected: str = "auto") -> ArtifactReport:
        report = _report(path, expected)
        path.write_bytes(b"after")
        return report

    monkeypatch.setattr(RELEASE_MANIFEST, "inspect_artifact", mutate)
    with pytest.raises(ManifestError, match="changed while binding"):
        bind_artifact(wheel)


def test_matrix_rejects_a_late_unbound_directory_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts(tmp_path)
    final_name = sorted(_MATRIX)[-1]
    added = False

    def inspect_and_add(path: Path, expected: str = "auto") -> ArtifactReport:
        nonlocal added
        report = _report(path, expected)
        if path.name == final_name and not added:
            (artifacts / "late-unbound.txt").write_text("unbound", encoding="utf-8")
            added = True
        return report

    monkeypatch.setattr(RELEASE_MANIFEST, "inspect_artifact", inspect_and_add)
    with pytest.raises(ManifestError, match="directory changed"):
        build_manifest(artifacts)


def test_verification_rejects_artifact_tampering_and_self_sweeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts(tmp_path)
    monkeypatch.setattr(RELEASE_MANIFEST, "inspect_artifact", _report)
    output = tmp_path / "artifact-manifest.json"
    generate_manifest(artifacts, output)

    pure = artifacts / "pyelk_reasoner-0.1.0.dev0-py3-none-any.whl"
    pure.write_bytes(b"replaced")
    with pytest.raises(ManifestError, match="does not match staged artifacts"):
        generate_manifest(artifacts, output, check=True)

    with pytest.raises(ManifestError, match="outside the artifact input"):
        generate_manifest(artifacts, artifacts / "artifact-manifest.json")


def test_distribution_workflow_generates_then_verifies_bound_release_manifest() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")
    command = (
        "python -m tools.release_manifest artifacts --output supply-chain/artifact-manifest.json"
    )

    assert workflow.count(command) == 2
    assert f"{command} --check" in workflow
    assert workflow.index("--require-approval") < workflow.index(command)
    assert workflow.index(command) < workflow.index("name: release-bundle")
