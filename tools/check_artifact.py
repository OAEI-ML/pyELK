#!/usr/bin/env python3
"""Fail-closed audits for pyELK wheels and source distributions."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from email.message import Message
from email.parser import BytesParser
from functools import partial
from pathlib import Path, PurePosixPath

PROJECT_NAME = "pyelk-reasoner"
CORE_REQUIREMENT = frozenset({">=0.2", "<0.3"})
ROOT = Path(__file__).resolve().parents[1]
LICENSE_EXPRESSION = "Apache-2.0"
_LICENSE_PATHS = (
    "LICENSE",
    "NOTICE.pyelk",
    *(
        f"THIRD_PARTY_LICENSES/{path.name}"
        for path in sorted((ROOT / "THIRD_PARTY_LICENSES").iterdir())
        if path.is_file()
    ),
)
LICENSE_PAYLOADS = {name: (ROOT / name).read_bytes() for name in _LICENSE_PATHS}
MAX_MEMBER_SIZE = 256 * 1024 * 1024
MAX_ARCHIVE_SIZE = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
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
_EXTRA_MARKER = re.compile(
    r"""(?:extra\s*==\s*(?:"[^"\r\n]+"|'[^'\r\n]+')|"""
    r"""(?:"[^"\r\n]+"|'[^'\r\n]+')\s*==\s*extra)\Z"""
)
_MAX_MARKER_CHARACTERS = 8192
_MAX_MARKER_DEPTH = 64


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
    legal_payload_sha256: str
    archive_sha256: str


@dataclass(frozen=True)
class _ArtifactIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


def _normalise_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _safe_member(name: str) -> str:
    if "\\" in name:
        raise AuditError(f"archive member uses a backslash: {name!r}")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise AuditError(f"archive member uses a control character: {name!r}")
    raw_parts = name.split("/")
    if (
        not name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name) is not None
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise AuditError(f"unsafe archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or path.as_posix() != name:
        raise AuditError(f"unsafe archive member path: {name!r}")
    return path.as_posix()


def _read_archive(path: Path, payload: bytes) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    casefold_names: dict[str, str] = {}
    total = 0

    def add(
        name: str,
        size: int,
        read: Callable[[], bytes],
        *,
        mode: int = 0,
    ) -> None:
        nonlocal total
        safe_name = _safe_member(name)
        if safe_name.endswith("/"):
            return
        if stat.S_ISLNK(mode):
            raise AuditError(f"archive member is a symbolic link: {safe_name}")
        if size < 0 or size > MAX_MEMBER_SIZE:
            raise AuditError(f"archive member is too large: {safe_name}")
        total += size
        if total > MAX_ARCHIVE_SIZE:
            raise AuditError("archive expands beyond the audit size limit")
        if safe_name in members:
            raise AuditError(f"duplicate archive member: {safe_name}")
        folded = safe_name.casefold()
        previous = casefold_names.get(folded)
        if previous is not None:
            raise AuditError(
                f"archive members collide after case normalization: {previous!r}, {safe_name!r}"
            )
        casefold_names[folded] = safe_name
        data = read()
        if len(data) != size:
            raise AuditError(f"archive member size differs from its header: {safe_name}")
        members[safe_name] = data

    if path.suffix == ".whl":
        if not zipfile.is_zipfile(io.BytesIO(payload)):
            raise AuditError(f"wheel is not a valid ZIP archive: {path}")
        with zipfile.ZipFile(io.BytesIO(payload)) as zip_archive:
            zip_infos = zip_archive.infolist()
            if len(zip_infos) > MAX_ARCHIVE_MEMBERS:
                raise AuditError("archive contains too many members")
            for zip_info in zip_infos:
                mode = (zip_info.external_attr >> 16) & 0xFFFF
                if zip_info.is_dir():
                    continue
                add(
                    zip_info.filename,
                    zip_info.file_size,
                    partial(zip_archive.read, zip_info),
                    mode=mode,
                )
    elif path.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as tar_archive:
                tar_infos = tar_archive.getmembers()
                if len(tar_infos) > MAX_ARCHIVE_MEMBERS:
                    raise AuditError("archive contains too many members")
                for tar_info in tar_infos:
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
                        extracted.read,
                        mode=tar_info.mode,
                    )
        except tarfile.TarError as error:
            raise AuditError(f"invalid source archive: {path}") from error
    else:
        raise AuditError(f"unsupported artifact type: {path.name}")
    return members


def _stat_fields(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_artifact(path: Path) -> tuple[bytes, _ArtifactIdentity]:
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise AuditError(f"artifact is not a regular file: {path}")
    if before.st_size > MAX_ARCHIVE_SIZE:
        raise AuditError(f"artifact exceeds the audit size limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AuditError(f"artifact cannot be opened safely: {path}") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stat_fields(before) != _stat_fields(opened):
            raise AuditError(f"artifact changed while opening: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        completed = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = path.stat(follow_symlinks=False)
    identities = {
        _stat_fields(before),
        _stat_fields(opened),
        _stat_fields(completed),
        _stat_fields(after),
    }
    payload = b"".join(chunks)
    if len(identities) != 1 or not stat.S_ISREG(after.st_mode) or len(payload) != completed.st_size:
        raise AuditError(f"artifact changed while hashing: {path}")
    return payload, _ArtifactIdentity(*_stat_fields(after), hashlib.sha256(payload).hexdigest())


def _path_matches_identity(path: Path, identity: _ArtifactIdentity) -> bool:
    try:
        current = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(current.st_mode) and _stat_fields(current) == (
        identity.device,
        identity.inode,
        identity.size,
        identity.mtime_ns,
        identity.ctime_ns,
    )


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


def _metadata_sha256(raw: bytes) -> str:
    """Hash metadata after normalizing the platform-dependent line ending."""

    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


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


def _split_marker_expression(expression: str, operator: str) -> tuple[str, ...] | None:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    position = 0
    while position < len(expression):
        character = expression[position]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            position += 1
            continue
        if character in {"'", '"'}:
            quote = character
            position += 1
            continue
        if character == "(":
            depth += 1
            position += 1
            continue
        if character == ")":
            depth -= 1
            if depth < 0:
                return None
            position += 1
            continue
        end = position + len(operator)
        before = expression[position - 1] if position else " "
        after = expression[end] if end < len(expression) else " "
        if (
            depth == 0
            and expression[position:end].casefold() == operator
            and not (before.isalnum() or before == "_")
            and not (after.isalnum() or after == "_")
        ):
            part = expression[start:position].strip()
            if not part:
                return None
            parts.append(part)
            start = end
            position = end
            continue
        position += 1
    if quote is not None or depth != 0:
        return None
    final = expression[start:].strip()
    if not final:
        return None
    parts.append(final)
    return tuple(parts)


def _has_enclosing_parentheses(expression: str) -> bool:
    if not expression.startswith("(") or not expression.endswith(")"):
        return False
    depth = 0
    quote: str | None = None
    escaped = False
    for position, character in enumerate(expression):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0 and position != len(expression) - 1:
                return False
    return depth == 0 and quote is None


def _marker_requires_extra(expression: str, *, depth: int = 0) -> bool:
    expression = expression.strip()
    if not expression or len(expression) > _MAX_MARKER_CHARACTERS or depth >= _MAX_MARKER_DEPTH:
        return False
    if _has_enclosing_parentheses(expression):
        return _marker_requires_extra(expression[1:-1], depth=depth + 1)
    alternatives = _split_marker_expression(expression, "or")
    if alternatives is None:
        return False
    if len(alternatives) > 1:
        return all(_marker_requires_extra(part, depth=depth + 1) for part in alternatives)
    conjunctions = _split_marker_expression(expression, "and")
    if conjunctions is None:
        return False
    if len(conjunctions) > 1:
        return any(_marker_requires_extra(part, depth=depth + 1) for part in conjunctions)
    return _EXTRA_MARKER.fullmatch(expression) is not None


def _is_extra_only_requirement(requirement: str) -> bool:
    package, separator, marker = requirement.partition(";")
    return bool(package.strip() and separator and _marker_requires_extra(marker))


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
    license_expressions = message.get_all("License-Expression", [])
    if license_expressions != [LICENSE_EXPRESSION]:
        raise AuditError(
            f"metadata must contain exactly License-Expression: {LICENSE_EXPRESSION}; "
            f"found {license_expressions}"
        )
    license_files = message.get_all("License-File", [])
    if len(license_files) != len(set(license_files)) or set(license_files) != set(LICENSE_PAYLOADS):
        raise AuditError(
            "metadata License-File headers must identify the exact legal payload inventory; "
            f"found {license_files}"
        )

    core_requirements = []
    for requirement in message.get_all("Requires-Dist", []):
        dependency = _dependency_name(requirement)
        if any(part in dependency for part in FORBIDDEN_DEPENDENCY_PARTS):
            raise AuditError(f"forbidden Java/JVM dependency: {requirement}")
        extra_only = _is_extra_only_requirement(requirement)
        if dependency == "pyowl-core" and not extra_only:
            core_requirements.append(requirement)
        elif not extra_only:
            raise AuditError(f"unexpected runtime dependency: {requirement}")
    if len(core_requirements) != 1:
        raise AuditError("metadata must contain exactly one runtime pyowl-core requirement")
    actual = _requirement_specifiers(core_requirements[0])
    if actual != CORE_REQUIREMENT:
        raise AuditError(
            "pyowl-core requirement must be exactly pyowl-core>=0.2,<0.3; "
            f"found {core_requirements[0]!r}"
        )
    return name, version, requires_python


def _audit_license_payloads(members: dict[str, bytes], *, wheel: bool) -> None:
    for name, expected in LICENSE_PAYLOADS.items():
        suffix = f".dist-info/licenses/{name}" if wheel else f"/{name}"
        member_name, actual = _one_member(members, suffix)
        if actual != expected:
            raise AuditError(f"license payload differs from the repository source: {member_name}")


def _legal_payload_sha256(members: dict[str, bytes], *, wheel: bool) -> str:
    digest = hashlib.sha256(b"pyelk:legal-payload:v1\0")
    for name in sorted(LICENSE_PAYLOADS):
        suffix = f".dist-info/licenses/{name}" if wheel else f"/{name}"
        _, payload = _one_member(members, suffix)
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


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
    wheel_versions = wheel_message.get_all("Wheel-Version", [])
    if wheel_versions != ["1.0"]:
        raise AuditError(
            f"WHEEL metadata must contain exactly Wheel-Version: 1.0; found {wheel_versions}"
        )
    tags = tuple(wheel_message.get_all("Tag", []))
    if not tags:
        raise AuditError("WHEEL metadata has no Tag")
    if len(tags) != len(set(tags)):
        raise AuditError("WHEEL metadata contains duplicate Tag entries")
    if set(tags) != filename_tags:
        raise AuditError(
            "wheel filename and WHEEL metadata tags differ; "
            f"filename-only={sorted(filename_tags - set(tags))}, "
            f"metadata-only={sorted(set(tags) - filename_tags)}"
        )
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


def _audit_wheel_identity(path: Path, members: dict[str, bytes], version: str) -> None:
    expected_root = f"pyelk_reasoner-{version}.dist-info"
    filename_prefix = path.name[:-4].rsplit("-", 3)[0]
    if filename_prefix != f"pyelk_reasoner-{version}":
        raise AuditError("wheel filename does not match the project metadata identity")
    dist_info_roots = {
        PurePosixPath(name).parts[0]
        for name in members
        if PurePosixPath(name).parts[0].endswith(".dist-info")
    }
    if dist_info_roots != {expected_root}:
        raise AuditError(
            f"wheel .dist-info identity roots differ: expected {expected_root!r}, "
            f"found {sorted(dist_info_roots)}"
        )
    required = {
        "pyelk/__init__.py",
        f"{expected_root}/METADATA",
        f"{expected_root}/WHEEL",
        f"{expected_root}/RECORD",
        *(f"{expected_root}/licenses/{name}" for name in LICENSE_PAYLOADS),
    }
    missing = required - set(members)
    if missing:
        raise AuditError(f"wheel is missing exact identity members: {sorted(missing)}")


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


def _audit_wheel(
    path: Path,
    members: dict[str, bytes],
    expected: str,
    archive_sha256: str,
) -> ArtifactReport:
    message, metadata_raw = _metadata(members, wheel=True)
    name, version, requires_python = _audit_metadata(message)
    _audit_wheel_identity(path, members, version)
    _audit_record(members)
    tags = _wheel_tags(path, members)
    native_members = _audit_names_and_payloads(members)
    inferred = "native-wheel" if native_members else "pure-wheel"
    if expected != "auto" and expected != inferred:
        raise AuditError(f"expected {expected}, found {inferred}")

    _, wheel_raw = _one_member(members, ".dist-info/WHEEL")
    _audit_license_payloads(members, wheel=True)
    wheel_message = BytesParser().parsebytes(wheel_raw)
    root_is_pure_values = wheel_message.get_all("Root-Is-Purelib", [])
    if len(root_is_pure_values) != 1:
        raise AuditError(
            "WHEEL metadata must contain exactly one Root-Is-Purelib header; "
            f"found {root_is_pure_values}"
        )
    root_is_pure = root_is_pure_values[0].lower()
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
        metadata_sha256=_metadata_sha256(metadata_raw),
        legal_payload_sha256=_legal_payload_sha256(members, wheel=True),
        archive_sha256=archive_sha256,
    )


def _audit_sdist(
    path: Path,
    members: dict[str, bytes],
    expected: str,
    archive_sha256: str,
) -> ArtifactReport:
    if expected not in {"auto", "sdist"}:
        raise AuditError(f"expected {expected}, found sdist")
    message, metadata_raw = _metadata(members, wheel=False)
    name, version, requires_python = _audit_metadata(message)
    _audit_license_payloads(members, wheel=False)
    native_members = _audit_names_and_payloads(members)
    if native_members:
        raise AuditError(f"sdist contains compiled shared libraries: {native_members}")
    roots = {member.split("/", 1)[0] for member in members}
    if len(roots) != 1:
        raise AuditError(f"sdist must have one top-level directory, found {sorted(roots)}")
    root = next(iter(roots))
    expected_root = f"pyelk_reasoner-{version}"
    if root != expected_root or path.name != f"{expected_root}.tar.gz":
        raise AuditError(
            "sdist archive identity does not match project metadata; "
            f"expected root/file {expected_root!r}, found {root!r}/{path.name!r}"
        )
    logical = {name[len(root) + 1 :] for name in members if name.startswith(root + "/")}
    required = {
        "Cargo.lock",
        "Cargo.toml",
        *LICENSE_PAYLOADS,
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
        metadata_sha256=_metadata_sha256(metadata_raw),
        legal_payload_sha256=_legal_payload_sha256(members, wheel=False),
        archive_sha256=archive_sha256,
    )


def inspect_artifact(path: Path, expected: str = "auto") -> ArtifactReport:
    """Inspect one artifact and return its deterministic audit report."""

    if path.is_symlink():
        raise AuditError(f"artifact must not be a symbolic link: {path}")
    path = path.absolute()
    payload, identity = _read_artifact(path)
    members = _read_archive(path, payload)
    if path.suffix == ".whl":
        report = _audit_wheel(path, members, expected, identity.sha256)
    else:
        report = _audit_sdist(path, members, expected, identity.sha256)
    if not _path_matches_identity(path, identity) or report.archive_sha256 != identity.sha256:
        raise AuditError(f"artifact changed during inspection: {path}")
    return report


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
    if pure_report.legal_payload_sha256 != native_report.legal_payload_sha256:
        raise AuditError("pure/native legal payload differs")
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


def external_audit(
    path: Path,
    *,
    expected_archive_sha256: str | None = None,
) -> tuple[str, ...]:
    """Run external auditors against the exact internally inspected artifact."""

    path = path.absolute()
    report = inspect_artifact(path, "native-wheel")
    payload, identity = _read_artifact(path)
    captured_sha256 = hashlib.sha256(payload).hexdigest()
    if report.archive_sha256 != captured_sha256 or (
        expected_archive_sha256 is not None and expected_archive_sha256 != captured_sha256
    ):
        raise AuditError("artifact changed between internal and external audit")
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
        if not _path_matches_identity(path, identity):
            raise AuditError("artifact changed before external audit")
        if shutil.which(command[0]) is None:
            raise AuditError(f"required audit command is unavailable: {command[0]}")
        subprocess.run(command, check=True)
        completed_payload, completed_identity = _read_artifact(path)
        if (
            completed_identity != identity
            or hashlib.sha256(completed_payload).hexdigest() != captured_sha256
        ):
            raise AuditError("artifact changed during external audit")
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
                result["external_commands"] = external_audit(
                    args.artifact,
                    expected_archive_sha256=report.archive_sha256,
                )
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
