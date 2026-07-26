#!/usr/bin/env python3
"""Fail-closed audits for pyELK wheels and source distributions."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

PROJECT_NAME = "pyelk-reasoner"
CORE_REQUIREMENT = frozenset({">=0.1", "<0.2"})
MAX_MEMBER_SIZE = 256 * 1024 * 1024
MAX_ARCHIVE_SIZE = 1024 * 1024 * 1024
NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")
PYTHON_SUFFIXES = (".py", ".pyi")
FORBIDDEN_ARCHIVE_SUFFIXES = (".class", ".ear", ".jar", ".war")
FORBIDDEN_DEPENDENCY_PARTS = ("java", "jni", "jpype", "jnius", "jvm", "py4j")
ABSOLUTE_PATH_MARKERS = (
    b"/Users/",
    b"/home/runner/work/",
    b"/github/workspace/",
    b"\\Users\\",
)


class AuditError(RuntimeError):
    """An artifact violates the release policy."""


@dataclass(frozen=True)
class ArtifactReport:
    path: str
    kind: str
    name: str
    version: str
    requires_python: str
    tags: tuple[str, ...]
    native_members: tuple[str, ...]
    python_hashes: dict[str, str]
    metadata_sha256: str
    archive_sha256: str


def _normalise_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _safe_member(name: str) -> str:
    if "\\" in name:
        raise AuditError(f"archive member uses a backslash: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise AuditError(f"unsafe archive member path: {name!r}")
    return path.as_posix()


def _read_archive(path: Path) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    total = 0

    def add(name: str, size: int, data: bytes, *, mode: int = 0) -> None:
        nonlocal total
        safe_name = _safe_member(name)
        if safe_name.endswith("/"):
            return
        if stat.S_ISLNK(mode):
            raise AuditError(f"archive member is a symbolic link: {safe_name}")
        if size > MAX_MEMBER_SIZE:
            raise AuditError(f"archive member is too large: {safe_name}")
        total += size
        if total > MAX_ARCHIVE_SIZE:
            raise AuditError("archive expands beyond the audit size limit")
        if safe_name in members:
            raise AuditError(f"duplicate archive member: {safe_name}")
        members[safe_name] = data

    if path.suffix == ".whl":
        if not zipfile.is_zipfile(path):
            raise AuditError(f"wheel is not a valid ZIP archive: {path}")
        with zipfile.ZipFile(path) as zip_archive:
            for zip_info in zip_archive.infolist():
                mode = (zip_info.external_attr >> 16) & 0xFFFF
                if zip_info.is_dir():
                    continue
                add(
                    zip_info.filename,
                    zip_info.file_size,
                    zip_archive.read(zip_info),
                    mode=mode,
                )
    elif path.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        try:
            with tarfile.open(path, mode="r:*") as tar_archive:
                for tar_info in tar_archive.getmembers():
                    if tar_info.isdir():
                        continue
                    if not tar_info.isfile():
                        raise AuditError(
                            f"source archive has a non-regular member: {tar_info.name}"
                        )
                    extracted = tar_archive.extractfile(tar_info)
                    if extracted is None:
                        raise AuditError(f"cannot read source member: {tar_info.name}")
                    add(
                        tar_info.name,
                        tar_info.size,
                        extracted.read(),
                        mode=tar_info.mode,
                    )
        except tarfile.TarError as error:
            raise AuditError(f"invalid source archive: {path}") from error
    else:
        raise AuditError(f"unsupported artifact type: {path.name}")
    return members


def _one_member(members: dict[str, bytes], suffix: str) -> tuple[str, bytes]:
    matches = [(name, data) for name, data in members.items() if name.endswith(suffix)]
    if len(matches) != 1:
        raise AuditError(f"expected exactly one {suffix} member, found {len(matches)}")
    return matches[0]


def _metadata(members: dict[str, bytes], *, wheel: bool) -> tuple[Message, bytes]:
    if wheel:
        _, raw = _one_member(members, ".dist-info/METADATA")
    else:
        matches = [
            data
            for name, data in members.items()
            if name.endswith("/PKG-INFO") and name.count("/") == 1
        ]
        if len(matches) != 1:
            raise AuditError(
                f"expected exactly one top-level PKG-INFO member, found {len(matches)}"
            )
        raw = matches[0]
    return BytesParser().parsebytes(raw), raw


def _dependency_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if match is None:
        raise AuditError(f"malformed Requires-Dist value: {requirement!r}")
    return _normalise_name(match.group(1))


def _requirement_specifiers(requirement: str) -> frozenset[str]:
    head = requirement.split(";", 1)[0]
    match = re.match(r"\s*[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^]]+\])?\s*(.*)", head)
    if match is None:
        return frozenset()
    value = match.group(1).strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    return frozenset(part.strip().replace(" ", "") for part in value.split(",") if part.strip())


def _audit_metadata(message: Message) -> tuple[str, str, str]:
    name = message.get("Name", "")
    version = message.get("Version", "")
    requires_python = message.get("Requires-Python", "")
    if _normalise_name(name) != PROJECT_NAME:
        raise AuditError(f"unexpected project name: {name!r}")
    if not version:
        raise AuditError("artifact metadata has no Version")
    if requires_python.replace(" ", "") != ">=3.10":
        raise AuditError(f"unexpected Requires-Python: {requires_python!r}")

    core_requirements = []
    for requirement in message.get_all("Requires-Dist", []):
        dependency = _dependency_name(requirement)
        if any(part in dependency for part in FORBIDDEN_DEPENDENCY_PARTS):
            raise AuditError(f"forbidden Java/JVM dependency: {requirement}")
        if dependency == "pyowl-core" and "extra ==" not in requirement:
            core_requirements.append(requirement)
    if len(core_requirements) != 1:
        raise AuditError("metadata must contain exactly one runtime pyowl-core requirement")
    actual = _requirement_specifiers(core_requirements[0])
    if actual != CORE_REQUIREMENT:
        raise AuditError(
            "pyowl-core requirement must be exactly pyowl-core>=0.1,<0.2; "
            f"found {core_requirements[0]!r}"
        )
    return name, version, requires_python


def _wheel_tags(path: Path, members: dict[str, bytes]) -> tuple[str, ...]:
    stem_parts = path.name[:-4].rsplit("-", 3)
    if len(stem_parts) != 4:
        raise AuditError(f"malformed wheel filename: {path.name}")
    python_tag, abi_tag, platform_tag = stem_parts[-3:]
    filename_tags = {
        f"{python}-{abi}-{platform}"
        for python in python_tag.split(".")
        for abi in abi_tag.split(".")
        for platform in platform_tag.split(".")
    }
    _, wheel_raw = _one_member(members, ".dist-info/WHEEL")
    wheel_message = BytesParser().parsebytes(wheel_raw)
    tags = tuple(wheel_message.get_all("Tag", []))
    if not tags:
        raise AuditError("WHEEL metadata has no Tag")
    missing = filename_tags - set(tags)
    if missing:
        raise AuditError(f"wheel filename tags are absent from WHEEL metadata: {sorted(missing)}")
    return tags


def _audit_record(members: dict[str, bytes]) -> None:
    record_name, raw = _one_member(members, ".dist-info/RECORD")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AuditError("wheel RECORD is not valid UTF-8") from error
    rows: dict[str, tuple[str, str]] = {}
    try:
        parsed = csv.reader(io.StringIO(text, newline=""))
        for index, row in enumerate(parsed, start=1):
            if len(row) != 3 or not row[0]:
                raise AuditError(f"wheel RECORD row {index} is malformed")
            name = _safe_member(row[0])
            if name in rows:
                raise AuditError(f"wheel RECORD contains duplicate member: {name}")
            if name not in members:
                raise AuditError(f"wheel RECORD names an absent member: {name}")
            rows[name] = (row[1], row[2])
    except csv.Error as error:
        raise AuditError(f"wheel RECORD CSV is malformed: {error}") from error

    missing = set(members) - set(rows)
    if missing:
        raise AuditError(f"wheel RECORD omits archive members: {sorted(missing)}")
    for name, data in members.items():
        digest, size = rows[name]
        if name == record_name:
            if digest or size:
                raise AuditError("wheel RECORD must leave its own hash and size empty")
            continue
        expected_digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        if digest != f"sha256={expected_digest.decode('ascii')}":
            raise AuditError(f"wheel RECORD hash mismatch: {name}")
        if size != str(len(data)):
            raise AuditError(f"wheel RECORD size mismatch: {name}")


def _audit_names_and_payloads(members: dict[str, bytes]) -> tuple[str, ...]:
    native_members = []
    current_root = str(Path.cwd().resolve()).encode()
    for name, data in members.items():
        lower = name.lower()
        path = PurePosixPath(lower)
        if lower.endswith(FORBIDDEN_ARCHIVE_SUFFIXES):
            raise AuditError(f"forbidden Java archive/class member: {name}")
        if any(part in {"java", "jni", "jre", "jvm"} for part in path.parts):
            raise AuditError(f"forbidden Java/JVM archive path: {name}")
        if path.name in {"java", "java.exe", "javaw.exe"}:
            raise AuditError(f"forbidden JVM launcher: {name}")
        if lower.endswith(NATIVE_SUFFIXES):
            native_members.append(name)
        for marker in (*ABSOLUTE_PATH_MARKERS, current_root):
            if marker and marker in data:
                raise AuditError(f"absolute build path marker {marker!r} in {name}")
    return tuple(sorted(native_members))


def _python_hashes(members: dict[str, bytes], *, sdist: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, data in members.items():
        logical = name.split("/", 1)[1] if sdist and "/" in name else name
        if not logical.startswith("pyelk/") and not logical.startswith("src/pyelk/"):
            continue
        if not logical.endswith((*PYTHON_SUFFIXES, "py.typed")):
            continue
        if logical.startswith("src/"):
            logical = logical[4:]
        result[logical] = hashlib.sha256(data).hexdigest()
    return dict(sorted(result.items()))


def _audit_wheel(path: Path, members: dict[str, bytes], expected: str) -> ArtifactReport:
    _audit_record(members)
    message, metadata_raw = _metadata(members, wheel=True)
    name, version, requires_python = _audit_metadata(message)
    tags = _wheel_tags(path, members)
    native_members = _audit_names_and_payloads(members)
    inferred = "native-wheel" if native_members else "pure-wheel"
    if expected != "auto" and expected != inferred:
        raise AuditError(f"expected {expected}, found {inferred}")

    _, wheel_raw = _one_member(members, ".dist-info/WHEEL")
    _one_member(members, ".dist-info/licenses/LICENSE")
    _one_member(members, ".dist-info/licenses/NOTICE.pyelk")
    wheel_message = BytesParser().parsebytes(wheel_raw)
    root_is_pure = wheel_message.get("Root-Is-Purelib", "").lower()
    if inferred == "pure-wheel":
        if set(tags) != {"py3-none-any"} or not path.name.endswith("-py3-none-any.whl"):
            raise AuditError(f"fallback wheel is not exactly py3-none-any: {tags}")
        if root_is_pure != "true":
            raise AuditError("fallback wheel must set Root-Is-Purelib: true")
    else:
        if not all(tag.startswith("cp310-abi3-") for tag in tags):
            raise AuditError(f"native wheel is not cp310-abi3: {tags}")
        if root_is_pure != "false":
            raise AuditError("native wheel must set Root-Is-Purelib: false")
        if len(native_members) != 1:
            raise AuditError(f"native wheel must contain one extension, found {native_members}")
        extension = native_members[0].lower()
        if not extension.startswith("pyelk/_native"):
            raise AuditError(f"unallowlisted shared library: {native_members[0]}")
        if extension.endswith(".so") and not extension.endswith(".abi3.so"):
            raise AuditError(f"non-ABI3 extension suffix: {native_members[0]}")

    hashes = _python_hashes(members, sdist=False)
    for required in ("pyelk/_native.pyi", "pyelk/py.typed"):
        if required not in hashes:
            raise AuditError(f"wheel is missing {required}")
    return ArtifactReport(
        path=str(path),
        kind=inferred,
        name=name,
        version=version,
        requires_python=requires_python,
        tags=tags,
        native_members=native_members,
        python_hashes=hashes,
        metadata_sha256=hashlib.sha256(metadata_raw).hexdigest(),
        archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _audit_sdist(path: Path, members: dict[str, bytes], expected: str) -> ArtifactReport:
    if expected not in {"auto", "sdist"}:
        raise AuditError(f"expected {expected}, found sdist")
    message, metadata_raw = _metadata(members, wheel=False)
    name, version, requires_python = _audit_metadata(message)
    native_members = _audit_names_and_payloads(members)
    if native_members:
        raise AuditError(f"sdist contains compiled shared libraries: {native_members}")
    roots = {member.split("/", 1)[0] for member in members}
    if len(roots) != 1:
        raise AuditError(f"sdist must have one top-level directory, found {sorted(roots)}")
    root = next(iter(roots))
    logical = {name[len(root) + 1 :] for name in members if name.startswith(root + "/")}
    required = {
        "Cargo.lock",
        "Cargo.toml",
        "LICENSE",
        "NOTICE.pyelk",
        "pyelk_build.py",
        "pyproject.toml",
        "rust-toolchain.toml",
        "rust/pyelk-core/Cargo.toml",
        "rust/pyelk-pyo3/Cargo.toml",
        "setup.py",
        "src/pyelk/__init__.py",
        "src/pyelk/_native.pyi",
        "src/pyelk/py.typed",
    }
    missing = required - logical
    if missing:
        raise AuditError(f"sdist is missing required files: {sorted(missing)}")
    forbidden_prefixes = ("benchmarks/", "rust/fuzz/", "target/", "tests/", "tools/")
    forbidden = sorted(name for name in logical if name.startswith(forbidden_prefixes))
    if forbidden:
        raise AuditError(f"sdist contains release-excluded files: {forbidden[:5]}")
    return ArtifactReport(
        path=str(path),
        kind="sdist",
        name=name,
        version=version,
        requires_python=requires_python,
        tags=(),
        native_members=(),
        python_hashes=_python_hashes(members, sdist=True),
        metadata_sha256=hashlib.sha256(metadata_raw).hexdigest(),
        archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def inspect_artifact(path: Path, expected: str = "auto") -> ArtifactReport:
    """Inspect one artifact and return its deterministic audit report."""

    path = path.resolve()
    if not path.is_file():
        raise AuditError(f"artifact does not exist: {path}")
    members = _read_archive(path)
    if path.suffix == ".whl":
        return _audit_wheel(path, members, expected)
    return _audit_sdist(path, members, expected)


def compare_wheels(pure: Path, native: Path) -> tuple[ArtifactReport, ArtifactReport]:
    """Require pure/native wheels to share metadata and all Python payloads."""

    pure_report = inspect_artifact(pure, "pure-wheel")
    native_report = inspect_artifact(native, "native-wheel")
    identity = (pure_report.name, pure_report.version, pure_report.requires_python)
    native_identity = (native_report.name, native_report.version, native_report.requires_python)
    if identity != native_identity:
        raise AuditError(
            f"pure/native project identity differs: {identity!r} != {native_identity!r}"
        )
    if pure_report.metadata_sha256 != native_report.metadata_sha256:
        raise AuditError("pure/native METADATA bytes differ")
    if pure_report.python_hashes != native_report.python_hashes:
        pure_keys = set(pure_report.python_hashes)
        native_keys = set(native_report.python_hashes)
        changed = sorted(
            key
            for key in pure_keys & native_keys
            if pure_report.python_hashes[key] != native_report.python_hashes[key]
        )
        raise AuditError(
            "pure/native Python payload differs; "
            f"pure-only={sorted(pure_keys - native_keys)}, "
            f"native-only={sorted(native_keys - pure_keys)}, changed={changed}"
        )
    return pure_report, native_report


def external_audit(path: Path) -> tuple[str, ...]:
    """Run ABI3 plus the host platform's dependency-inspection command."""

    report = inspect_artifact(path, "native-wheel")
    commands: list[list[str]] = [["abi3audit", str(path)]]
    platform = report.tags[0].split("-", 2)[-1]
    if "manylinux" in platform or "musllinux" in platform:
        commands.append(["auditwheel", "show", str(path)])
    elif "macosx" in platform:
        commands.append(["delocate-listdeps", "--all", str(path)])
    elif platform.startswith("win"):
        commands.append(["delvewheel", "show", str(path)])
    else:
        raise AuditError(f"no external dependency auditor for platform tag: {platform}")
    rendered = []
    for command in commands:
        if shutil.which(command[0]) is None:
            raise AuditError(f"required audit command is unavailable: {command[0]}")
        subprocess.run(command, check=True)
        rendered.append(" ".join(command))
    return tuple(rendered)


def _write_json(value: object, destination: Path | None) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if destination is None:
        sys.stdout.write(payload)
    else:
        destination.write_text(payload, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="audit one wheel or sdist")
    check.add_argument("artifact", type=Path)
    check.add_argument(
        "--expect", choices=("auto", "pure-wheel", "native-wheel", "sdist"), default="auto"
    )
    check.add_argument("--external", action="store_true", help="also run ABI/platform tools")
    check.add_argument("--output-json", type=Path)
    compare = subparsers.add_parser("compare", help="compare fallback/native wheel payloads")
    compare.add_argument("pure_wheel", type=Path)
    compare.add_argument("native_wheel", type=Path)
    compare.add_argument("--output-json", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            report = inspect_artifact(args.artifact, args.expect)
            result: dict[str, object] = asdict(report)
            if args.external:
                result["external_commands"] = external_audit(args.artifact)
            _write_json(result, args.output_json)
        else:
            pure, native = compare_wheels(args.pure_wheel, args.native_wheel)
            _write_json(
                {"pure": asdict(pure), "native": asdict(native), "equivalent": True},
                args.output_json,
            )
    except (AuditError, OSError, subprocess.CalledProcessError) as error:
        print(f"artifact audit failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
