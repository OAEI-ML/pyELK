#!/usr/bin/env python3
"""Synchronize and verify the pinned ELK 0.6.0 non-incremental test corpus.

This tool performs no Java work by default.  It copies the exact upstream resources
and licence from a hash-verified source checkout into the Java-free frozen test tree.
Java oracle regeneration is an explicit, separate ``java`` subcommand introduced by
the package-local harness under ``tools/java-oracle``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "data" / "elk-v0.6.0"
UPSTREAM_SUBTREE = Path("elk-reasoner/src/test/resources/test_input")
REPOSITORY = "https://github.com/liveontologies/elk-reasoner"
TAG = "v0.6.0"
COMMIT = "b8ac5ce83db0704a7359d96aa382891e2f547863"
TREE = "9becd9e41eac6434a1e247c2a9b19644cdd9d27a"
LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"

_EXPECTED_COUNTS = {
    "files": 262,
    "ontology_inputs": 124,
    "golden_outputs": 138,
    "class_classification_inputs": 66,
    "object_property_classification_inputs": 11,
    "class_query_inputs": 26,
    "entailment_inputs": 16,
    "realization_inputs": 5,
}

_NOTICE = """# ELK 0.6.0 frozen test resources

The files below `upstream/` are unmodified copies of
`elk-reasoner/src/test/resources/test_input` from ELK 0.6.0, commit
`b8ac5ce83db0704a7359d96aa382891e2f547863`.

ELK is Copyright 2011-2024 the ELK contributors and is licensed under the
Apache License, Version 2.0. The exact upstream licence is retained as
`LICENSE.txt`. `manifest.json` records every source path and SHA-256 digest.

Repository: https://github.com/liveontologies/elk-reasoner
Release: https://github.com/liveontologies/elk-reasoner/releases/tag/v0.6.0
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(source: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"cannot inspect ELK source checkout: {error}") from error
    return completed.stdout.strip()


def _verify_source(source: Path) -> Path:
    commit = _git(source, "rev-parse", "HEAD")
    tree = _git(source, "rev-parse", "HEAD^{tree}")
    if commit != COMMIT or tree != TREE:
        raise RuntimeError(
            f"ELK checkout mismatch: expected {COMMIT}/{TREE}, got {commit}/{tree}"
        )
    resources = source / UPSTREAM_SUBTREE
    if not resources.is_dir():
        raise RuntimeError(f"ELK resource tree does not exist: {resources}")
    licence = source / "LICENSE.txt"
    if _sha256(licence) != LICENSE_SHA256:
        raise RuntimeError("ELK LICENSE.txt does not match the pinned digest")
    return resources


def _family(relative: Path) -> str:
    parts = relative.parts
    if parts[:2] == ("classification", "object_property"):
        return "object_property_classification"
    if parts[0] == "classification":
        return "class_classification"
    if parts[:2] == ("query", "class"):
        return "class_query"
    if parts[:2] == ("query", "entailment"):
        return "entailment"
    if parts[0] == "realization":
        return "realization"
    raise RuntimeError(f"unknown upstream test family: {relative}")


def _entries(resources: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(item for item in resources.rglob("*") if item.is_file()):
        relative = path.relative_to(resources)
        role = "ontology" if path.suffix == ".owl" else "upstream_golden"
        result.append(
            {
                "bytes": path.stat().st_size,
                "family": _family(relative),
                "path": relative.as_posix(),
                "role": role,
                "sha256": _sha256(path),
                "upstream_path": (UPSTREAM_SUBTREE / relative).as_posix(),
            }
        )
    return result


def _summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    roles = Counter(entry["role"] for entry in entries)
    ontology_families = Counter(
        entry["family"] for entry in entries if entry["role"] == "ontology"
    )
    return {
        "files": len(entries),
        "ontology_inputs": roles["ontology"],
        "golden_outputs": roles["upstream_golden"],
        "class_classification_inputs": ontology_families["class_classification"],
        "object_property_classification_inputs": ontology_families[
            "object_property_classification"
        ],
        "class_query_inputs": ontology_families["class_query"],
        "entailment_inputs": ontology_families["entailment"],
        "realization_inputs": ontology_families["realization"],
    }


def _manifest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _summary(entries)
    if summary != _EXPECTED_COUNTS:
        raise RuntimeError(
            f"pinned corpus inventory changed: expected {_EXPECTED_COUNTS}, got {summary}"
        )
    return {
        "schema": "pyelk.elk-frozen-corpus/1",
        "generated_by": "tools/oracle.py sync",
        "source": {
            "commit": COMMIT,
            "license": "Apache-2.0",
            "license_sha256": LICENSE_SHA256,
            "release": "0.6.0",
            "repository": REPOSITORY,
            "source_path": UPSTREAM_SUBTREE.as_posix(),
            "tag": TAG,
            "tree": TREE,
        },
        "summary": summary,
        "files": entries,
    }


def _render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _build_stage(source: Path, parent: Path) -> Path:
    resources = _verify_source(source)
    entries = _entries(resources)
    stage = parent / TARGET.name
    shutil.copytree(resources, stage / "upstream", copy_function=shutil.copyfile)
    shutil.copyfile(source / "LICENSE.txt", stage / "LICENSE.txt")
    (stage / "NOTICE.md").write_text(_NOTICE, encoding="utf-8")
    (stage / "manifest.json").write_text(_render(_manifest(entries)), encoding="utf-8")
    return stage


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def sync(source: Path, *, check: bool) -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="elk-corpus-", dir=TARGET.parent) as temporary:
        stage = _build_stage(source.resolve(), Path(temporary))
        if TARGET.exists() and _tree_digest(TARGET) == _tree_digest(stage):
            return
        if check:
            raise RuntimeError("frozen ELK corpus differs from the pinned source checkout")
        backup = TARGET.with_name(f".{TARGET.name}.backup")
        if backup.exists():
            shutil.rmtree(backup)
        if TARGET.exists():
            os.replace(TARGET, backup)
        try:
            os.replace(stage, TARGET)
        except BaseException:
            if backup.exists() and not TARGET.exists():
                os.replace(backup, TARGET)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def verify() -> None:
    manifest_path = TARGET / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read frozen corpus manifest: {error}") from error
    if payload.get("schema") != "pyelk.elk-frozen-corpus/1":
        raise RuntimeError("unknown frozen corpus manifest schema")
    if payload.get("source", {}).get("commit") != COMMIT:
        raise RuntimeError("frozen corpus manifest has the wrong ELK commit")
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise RuntimeError("frozen corpus manifest has no file inventory")
    if payload.get("summary") != _EXPECTED_COUNTS or _summary(entries) != _EXPECTED_COUNTS:
        raise RuntimeError("frozen corpus manifest counts do not match the pinned inventory")
    expected_paths: set[str] = set()
    for entry in entries:
        relative = entry["path"]
        if relative in expected_paths:
            raise RuntimeError(f"duplicate frozen corpus path: {relative}")
        expected_paths.add(relative)
        path = TARGET / "upstream" / relative
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise RuntimeError(f"missing or size-mismatched frozen resource: {relative}")
        if _sha256(path) != entry["sha256"]:
            raise RuntimeError(f"hash-mismatched frozen resource: {relative}")
    actual_paths = {
        path.relative_to(TARGET / "upstream").as_posix()
        for path in (TARGET / "upstream").rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise RuntimeError("frozen corpus contains unmanifested or missing resources")
    if _sha256(TARGET / "LICENSE.txt") != LICENSE_SHA256:
        raise RuntimeError("frozen ELK licence digest does not match")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync", help="copy the pinned upstream corpus")
    sync_parser.add_argument("--source", required=True, type=Path)
    sync_parser.add_argument("--check", action="store_true")
    subparsers.add_parser("verify", help="verify the committed Java-free corpus")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "sync":
            sync(arguments.source, check=arguments.check)
        else:
            verify()
    except RuntimeError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
