"""Bind the complete pyELK release matrix to deterministic artifact identities."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.check_artifact import ArtifactReport, AuditError, inspect_artifact

_NATIVE_VARIANTS = {
    frozenset(
        {
            "cp310-abi3-manylinux_2_17_x86_64",
            "cp310-abi3-manylinux2014_x86_64",
        }
    ): "native-manylinux-x86_64",
    frozenset(
        {
            "cp310-abi3-manylinux_2_17_aarch64",
            "cp310-abi3-manylinux2014_aarch64",
        }
    ): "native-manylinux-aarch64",
    frozenset({"cp310-abi3-musllinux_1_2_x86_64"}): "native-musllinux-x86_64",
    frozenset({"cp310-abi3-musllinux_1_2_aarch64"}): "native-musllinux-aarch64",
    frozenset({"cp310-abi3-macosx_10_12_x86_64"}): "native-macos-x86_64",
    frozenset({"cp310-abi3-macosx_11_0_arm64"}): "native-macos-arm64",
    frozenset({"cp310-abi3-win_amd64"}): "native-windows-amd64",
}
_EXPECTED_VARIANTS = frozenset(
    {
        "sdist",
        "pure-wheel",
        *_NATIVE_VARIANTS.values(),
    }
)
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ManifestError(RuntimeError):
    """The staged release set or its persisted manifest is invalid."""


@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    """Stable identity and release-matrix variant for one artifact."""

    filename: str
    sha256: str
    size: int
    variant: str


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _file_identity(path: Path, *, description: str) -> _FileIdentity:
    details = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(details.st_mode):
        raise ManifestError(f"{description} is not a regular file: {path}")
    return _FileIdentity(
        device=details.st_dev,
        inode=details.st_ino,
        size=details.st_size,
        mtime_ns=details.st_mtime_ns,
        ctime_ns=details.st_ctime_ns,
    )


def _directory_identity(path: Path) -> _DirectoryIdentity:
    details = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(details.st_mode):
        raise ManifestError(f"artifact input is not a directory: {path}")
    return _DirectoryIdentity(
        device=details.st_dev,
        inode=details.st_ino,
        size=details.st_size,
        mtime_ns=details.st_mtime_ns,
        ctime_ns=details.st_ctime_ns,
    )


def _direct_entries(path: Path) -> tuple[Path, ...]:
    return tuple(sorted(path.iterdir(), key=lambda candidate: candidate.name))


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        raise ManifestError(f"cannot resolve release checkout context: {detail}")
    return completed.stdout.strip()


def _checkout_context(root: Path, source_revision: str) -> dict[str, object]:
    if _HEX40.fullmatch(source_revision) is None:
        raise ManifestError("source revision must be an exact lowercase 40-character Git SHA")
    commit = _git_output(root, "rev-parse", "--verify", "HEAD")
    tree = _git_output(root, "rev-parse", "--verify", "HEAD^{tree}")
    status = _git_output(root, "status", "--porcelain=v1", "--untracked-files=no")
    if commit != source_revision:
        raise ManifestError(
            f"source revision {source_revision} does not match release checkout HEAD {commit}"
        )
    if _HEX40.fullmatch(tree) is None:
        raise ManifestError(f"release checkout tree is not an exact Git object ID: {tree!r}")
    if status:
        raise ManifestError("release checkout has tracked worktree or index changes")
    return {
        "commit": commit,
        "tree": tree,
        "tracked_worktree_clean": True,
    }


def _variant(report: ArtifactReport) -> str:
    if report.kind == "sdist":
        return "sdist"
    if report.kind == "pure-wheel":
        return "pure-wheel"
    if report.kind != "native-wheel":
        raise ManifestError(f"unsupported audited artifact kind: {report.kind!r}")
    tags = frozenset(report.tags)
    try:
        return _NATIVE_VARIANTS[tags]
    except KeyError as error:
        raise ManifestError(
            f"native wheel has no tier-one matrix variant: {sorted(tags)}"
        ) from error


def _inspect_bound(path: Path) -> tuple[ArtifactBinding, ArtifactReport]:
    before = _file_identity(path, description="artifact")
    report = inspect_artifact(path)
    after = _file_identity(path, description="artifact")
    if before != after:
        raise ManifestError(f"artifact changed while binding its release identity: {path}")
    return (
        ArtifactBinding(
            filename=path.name,
            sha256=report.archive_sha256,
            size=after.size,
            variant=_variant(report),
        ),
        report,
    )


def bind_artifact(path: Path) -> ArtifactBinding:
    """Audit and bind one stable, regular release artifact."""

    binding, _ = _inspect_bound(path)
    return binding


def build_manifest(
    artifacts_dir: Path,
    *,
    checkout_context: dict[str, object],
) -> dict[str, object]:
    """Audit and bind the exact nine-slot tier-one artifact matrix."""

    if checkout_context != {
        "commit": checkout_context.get("commit"),
        "tree": checkout_context.get("tree"),
        "tracked_worktree_clean": True,
    }:
        raise ManifestError("release checkout context is incomplete or not clean")
    if any(
        _HEX40.fullmatch(value) is None
        for value in (
            checkout_context.get("commit"),
            checkout_context.get("tree"),
        )
        if isinstance(value, str)
    ) or not all(isinstance(checkout_context.get(name), str) for name in ("commit", "tree")):
        raise ManifestError("release checkout context has invalid Git object IDs")
    directory_identity = _directory_identity(artifacts_dir)
    paths = _direct_entries(artifacts_dir)
    if len(paths) != len(_EXPECTED_VARIANTS):
        raise ManifestError(
            f"release matrix must contain exactly nine direct artifact files; found {len(paths)}"
        )

    bindings: list[ArtifactBinding] = []
    reports: list[ArtifactReport] = []
    for path in paths:
        if path.suffix != ".whl" and not path.name.endswith(".tar.gz"):
            raise ManifestError(f"unsupported entry in artifact input: {path.name}")
        binding, report = _inspect_bound(path)
        bindings.append(binding)
        reports.append(report)

    counts = Counter(binding.variant for binding in bindings)
    expected_counts = Counter({variant: 1 for variant in _EXPECTED_VARIANTS})
    if counts != expected_counts:
        missing = sorted((expected_counts - counts).elements())
        duplicate_or_foreign = sorted((counts - expected_counts).elements())
        raise ManifestError(
            "release matrix variants differ; "
            f"missing={missing}, duplicate-or-foreign={duplicate_or_foreign}"
        )

    identities = {(report.name, report.version) for report in reports}
    if len(identities) != 1:
        raise ManifestError(
            f"release artifacts do not share one distribution identity: {sorted(identities)}"
        )
    distribution, version = next(iter(identities))
    if (
        _directory_identity(artifacts_dir) != directory_identity
        or _direct_entries(artifacts_dir) != paths
    ):
        raise ManifestError("artifact input directory changed while building the manifest")
    return {
        "schema": 2,
        "checkout_context": checkout_context,
        "distribution": distribution,
        "version": version,
        "artifacts": [asdict(binding) for binding in bindings],
    }


def _canonical_json(manifest: dict[str, object]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _assert_manifest_outside_artifacts(artifacts_dir: Path, manifest_path: Path) -> None:
    artifact_root = artifacts_dir.resolve()
    resolved_manifest = manifest_path.resolve()
    if resolved_manifest == artifact_root or resolved_manifest.is_relative_to(artifact_root):
        raise ManifestError(
            f"release manifest must be outside the artifact input directory: {manifest_path}"
        )


def generate_manifest(
    artifacts_dir: Path,
    manifest_path: Path,
    *,
    checkout_context: dict[str, object],
    check: bool = False,
) -> None:
    """Write or verify the canonical manifest for an exact release matrix."""

    _assert_manifest_outside_artifacts(artifacts_dir, manifest_path)
    rendered = _canonical_json(
        build_manifest(
            artifacts_dir,
            checkout_context=checkout_context,
        )
    )
    if check:
        before = _file_identity(manifest_path, description="release manifest")
        actual = manifest_path.read_text(encoding="utf-8")
        after = _file_identity(manifest_path, description="release manifest")
        if before != after:
            raise ManifestError(f"release manifest changed while verifying it: {manifest_path}")
        if actual != rendered:
            raise ManifestError(
                f"release manifest does not match staged artifacts: {manifest_path}"
            )
        return

    if manifest_path.is_symlink():
        raise ManifestError(f"release manifest must not be a symbolic link: {manifest_path}")
    if manifest_path.exists() and not manifest_path.is_file():
        raise ManifestError(f"release manifest path is not a regular file: {manifest_path}")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        generate_manifest(
            args.artifacts_dir,
            args.output,
            checkout_context=_checkout_context(args.root.resolve(), args.source_revision),
            check=args.check,
        )
    except (AuditError, ManifestError, OSError, UnicodeError) as error:
        print(f"release manifest failed: {error}", file=sys.stderr)
        return 1
    action = "verified" if args.check else "generated"
    print(f"release manifest {action}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
