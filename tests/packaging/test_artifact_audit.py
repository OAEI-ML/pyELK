from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]


def _load_auditor() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pyelk_check_artifact", ROOT / "tools/check_artifact.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AUDITOR = _load_auditor()
AuditError = AUDITOR.AuditError
pytestmark = pytest.mark.packaging

LICENSE = (ROOT / "LICENSE").read_bytes()
NOTICE = (ROOT / "NOTICE.pyelk").read_bytes()
METADATA = b"""Metadata-Version: 2.4
Name: pyelk-reasoner
Version: 0.1.0.dev0
Requires-Python: >=3.10
License-Expression: Apache-2.0
License-File: LICENSE
License-File: NOTICE.pyelk
Requires-Dist: pyowl-core<0.2,>=0.1

fixture
"""


def _record(files: dict[str, bytes], record_name: str) -> bytes:
    rows = []
    for name, value in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=").decode()
        rows.append(f"{name},sha256={digest},{len(value)}\n")
    rows.append(f"{record_name},,\n")
    return "".join(rows).encode()


def _wheel(
    tmp_path: Path,
    *,
    native: bool,
    metadata: bytes = METADATA,
    source: bytes = b"VALUE = 1\n",
    extra: dict[str, bytes] | None = None,
    dist_info: str = "pyelk_reasoner-0.1.0.dev0.dist-info",
    extra_tags: tuple[str, ...] = (),
    wheel_version: str = "1.0",
    root_is_pure: str | None = None,
    extra_wheel_headers: tuple[tuple[str, str], ...] = (),
    record_suffix: bytes = b"",
    tamper_after_record: dict[str, bytes] | None = None,
) -> Path:
    tag = "cp310-abi3-test_platform" if native else "py3-none-any"
    path = tmp_path / f"pyelk_reasoner-0.1.0.dev0-{tag}.whl"
    pure_value = root_is_pure or ("false" if native else "true")
    wheel = (
        f"Wheel-Version: {wheel_version}\n".encode()
        + b"Generator: packaging-test\n"
        + f"Root-Is-Purelib: {pure_value}\n".encode()
        + b"".join(f"{name}: {value}\n".encode() for name, value in extra_wheel_headers)
        + f"Tag: {tag}\n".encode()
        + b"".join(f"Tag: {extra_tag}\n".encode() for extra_tag in extra_tags)
        + b"\n"
    )
    files = {
        "pyelk/__init__.py": source,
        "pyelk/_native.pyi": b"def version() -> str: ...\n",
        "pyelk/py.typed": b"",
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": wheel,
        f"{dist_info}/licenses/LICENSE": LICENSE,
        f"{dist_info}/licenses/NOTICE.pyelk": NOTICE,
    }
    if native:
        files["pyelk/_native.abi3.so"] = b"native"
    files.update(extra or {})
    record_name = f"{dist_info}/RECORD"
    files[record_name] = _record(files, record_name) + record_suffix
    files.update(tamper_after_record or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return path


def _sdist(
    tmp_path: Path,
    *,
    extra: dict[str, bytes] | None = None,
    root: str = "pyelk_reasoner-0.1.0.dev0",
) -> Path:
    path = tmp_path / "pyelk_reasoner-0.1.0.dev0.tar.gz"
    files = {
        "PKG-INFO": METADATA,
        "Cargo.lock": b"version = 4\n",
        "Cargo.toml": b"[workspace]\n",
        "LICENSE": LICENSE,
        "NOTICE.pyelk": NOTICE,
        "pyelk_build.py": b"def build_reproducible_sdist(): ...\n",
        "pyproject.toml": b"[build-system]\n",
        "rust-toolchain.toml": b"[toolchain]\n",
        "setup.py": b"from setuptools import setup\nsetup()\n",
        "rust/pyelk-core/Cargo.toml": b"[package]\n",
        "rust/pyelk-pyo3/Cargo.toml": b"[package]\n",
        "src/pyelk/__init__.py": b"",
        "src/pyelk/_native.pyi": b"",
        "src/pyelk/py.typed": b"",
    }
    files.update(extra or {})
    with tarfile.open(path, "w:gz") as archive:
        for name, value in files.items():
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    return path


def test_valid_artifacts_and_equivalent_payloads_pass(tmp_path: Path) -> None:
    pure = _wheel(tmp_path, native=False)
    native = _wheel(tmp_path, native=True)
    sdist = _sdist(tmp_path)
    assert AUDITOR.inspect_artifact(pure).kind == "pure-wheel"
    assert AUDITOR.inspect_artifact(native).kind == "native-wheel"
    assert AUDITOR.inspect_artifact(sdist).kind == "sdist"
    assert AUDITOR.compare_wheels(pure, native)[1].tags == ("cp310-abi3-test_platform",)


def test_wheel_requires_exact_filename_and_dist_info_identity(tmp_path: Path) -> None:
    foreign_root = _wheel(
        tmp_path,
        native=False,
        dist_info="foreign_project-0.1.0.dev0.dist-info",
    )
    with pytest.raises(AuditError, match="identity roots differ"):
        AUDITOR.inspect_artifact(foreign_root)

    wheel = _wheel(tmp_path, native=False)
    foreign_filename = tmp_path / "foreign_project-0.1.0.dev0-py3-none-any.whl"
    wheel.rename(foreign_filename)
    with pytest.raises(AuditError, match="filename does not match"):
        AUDITOR.inspect_artifact(foreign_filename)


def test_sdist_requires_exact_filename_and_root_identity(tmp_path: Path) -> None:
    with pytest.raises(AuditError, match="sdist archive identity"):
        AUDITOR.inspect_artifact(
            _sdist(
                tmp_path,
                root="foreign_project-0.1.0.dev0",
            )
        )


def test_compressed_repaired_platform_tags_are_expanded(tmp_path: Path) -> None:
    original = _wheel(tmp_path, native=True)
    repaired = tmp_path / (
        "pyelk_reasoner-0.1.0.dev0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
    )
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(original) as source:
        for info in source.infolist():
            value = source.read(info)
            if info.filename.endswith(".dist-info/WHEEL"):
                value = value.replace(
                    b"Tag: cp310-abi3-test_platform\n",
                    b"Tag: cp310-abi3-manylinux_2_17_x86_64\n"
                    b"Tag: cp310-abi3-manylinux2014_x86_64\n",
                )
            files[info.filename] = value
    record_name = "pyelk_reasoner-0.1.0.dev0.dist-info/RECORD"
    files.pop(record_name)
    files[record_name] = _record(files, record_name)
    with zipfile.ZipFile(repaired, "w") as target:
        for name, value in files.items():
            target.writestr(name, value)
    assert AUDITOR.inspect_artifact(repaired).kind == "native-wheel"


def test_wheel_metadata_cannot_claim_tags_absent_from_filename(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path,
        native=True,
        extra_tags=("cp310-abi3-unrelated_platform",),
    )
    with pytest.raises(AuditError, match="filename and WHEEL metadata tags differ"):
        AUDITOR.inspect_artifact(wheel)


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"wheel_version": "2.0"}, "exactly Wheel-Version: 1.0"),
        ({"native": True, "root_is_pure": "true"}, "native wheel must set Root-Is-Purelib"),
        (
            {"extra_wheel_headers": (("Root-Is-Purelib", "false"),)},
            "exactly one Root-Is-Purelib",
        ),
    ],
)
def test_wheel_install_headers_are_exact(
    tmp_path: Path,
    arguments: dict[str, object],
    match: str,
) -> None:
    options = dict(arguments)
    native = bool(options.pop("native", False))
    with pytest.raises(AuditError, match=match):
        AUDITOR.inspect_artifact(
            _wheel(
                tmp_path,
                native=native,
                **options,
            )
        )


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"vendor/runtime.jar": b"x"}, "Java"),
        ({"pyelk/helper.so": b"x"}, "one extension"),
        ({"notes.txt": b"/home/runner/work/project/secret"}, "absolute build path"),
    ],
)
def test_archive_policy_rejects_forbidden_payloads(
    tmp_path: Path, extra: dict[str, bytes], match: str
) -> None:
    path = _wheel(tmp_path, native=True, extra=extra)
    with pytest.raises(AuditError, match=match):
        AUDITOR.inspect_artifact(path)


def test_metadata_rejects_jvm_bridge_dependency(tmp_path: Path) -> None:
    metadata = METADATA.replace(b"\n\nfixture", b"\nRequires-Dist: JPype1>=1\n\nfixture")
    with pytest.raises(AuditError, match="JVM dependency"):
        AUDITOR.inspect_artifact(_wheel(tmp_path, native=False, metadata=metadata))


def test_metadata_requires_exact_license_expression_and_files(tmp_path: Path) -> None:
    wrong_expression = METADATA.replace(b"License-Expression: Apache-2.0", b"License: Apache-2.0")
    with pytest.raises(AuditError, match="License-Expression"):
        AUDITOR.inspect_artifact(_wheel(tmp_path, native=False, metadata=wrong_expression))

    duplicate_file = METADATA.replace(
        b"License-File: NOTICE.pyelk",
        b"License-File: LICENSE\nLicense-File: NOTICE.pyelk",
    )
    with pytest.raises(AuditError, match="License-File headers"):
        AUDITOR.inspect_artifact(_wheel(tmp_path, native=False, metadata=duplicate_file))


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_license_payloads_must_match_repository_sources(tmp_path: Path, kind: str) -> None:
    if kind == "wheel":
        artifact = _wheel(
            tmp_path,
            native=False,
            extra={"pyelk_reasoner-0.1.0.dev0.dist-info/licenses/NOTICE.pyelk": b"changed\n"},
        )
    else:
        artifact = _sdist(tmp_path, extra={"NOTICE.pyelk": b"changed\n"})
    with pytest.raises(AuditError, match="license payload differs"):
        AUDITOR.inspect_artifact(artifact)


def test_metadata_rejects_optional_marker_with_runtime_escape(tmp_path: Path) -> None:
    metadata = METADATA.replace(
        b"\n\nfixture",
        b'\nRequires-Dist: requests; extra == "dev" or python_version >= "3.10"\n\nfixture',
    )
    with pytest.raises(AuditError, match="unexpected runtime dependency"):
        AUDITOR.inspect_artifact(_wheel(tmp_path, native=False, metadata=metadata))


def test_metadata_accepts_dependency_only_when_every_branch_requires_an_extra(
    tmp_path: Path,
) -> None:
    metadata = METADATA.replace(
        b"\n\nfixture",
        (
            b'\nRequires-Dist: tomli>=2; python_version < "3.11" and extra == "dev"\n'
            b'Requires-Dist: pytest>=8; (extra == "test" and python_version >= "3.10") '
            b'or extra == "dev"\n\nfixture'
        ),
    )
    assert (
        AUDITOR.inspect_artifact(_wheel(tmp_path, native=False, metadata=metadata)).kind
        == "pure-wheel"
    )


def test_compare_rejects_changed_python_source(tmp_path: Path) -> None:
    pure = _wheel(tmp_path, native=False)
    native = _wheel(tmp_path, native=True, source=b"VALUE = 2\n")
    with pytest.raises(AuditError, match="Python payload differs"):
        AUDITOR.compare_wheels(pure, native)


def test_wheel_record_rejects_payload_changed_after_hashing(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path,
        native=False,
        tamper_after_record={"pyelk/__init__.py": b"VALUE = 2\n"},
    )
    with pytest.raises(AuditError, match="RECORD hash mismatch"):
        AUDITOR.inspect_artifact(wheel)


@pytest.mark.parametrize(
    ("suffix", "match"),
    [
        (b"pyelk/__init__.py,,\n", "duplicate member"),
        (b"malformed,row\n", "row .* malformed"),
    ],
)
def test_wheel_record_rejects_ambiguous_rows(
    tmp_path: Path,
    suffix: bytes,
    match: str,
) -> None:
    with pytest.raises(AuditError, match=match):
        AUDITOR.inspect_artifact(
            _wheel(
                tmp_path,
                native=False,
                record_suffix=suffix,
            )
        )


def test_sdist_rejects_release_excluded_tree(tmp_path: Path) -> None:
    sdist = _sdist(tmp_path, extra={"tools/java-oracle/tool.txt": b"x"})
    with pytest.raises(AuditError, match="release-excluded"):
        AUDITOR.inspect_artifact(sdist)


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, native=False)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("../escape", b"x")
    with pytest.raises(AuditError, match="unsafe archive"):
        AUDITOR.inspect_artifact(wheel)


@pytest.mark.parametrize(
    "name",
    [
        "pyelk/./alias.py",
        "pyelk//alias.py",
        "C:/escape.py",
        "pyelk/control\x01.py",
    ],
)
def test_noncanonical_archive_paths_are_rejected(tmp_path: Path, name: str) -> None:
    wheel = _wheel(tmp_path, native=False)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(name, b"x")
    with pytest.raises(AuditError, match="archive member"):
        AUDITOR.inspect_artifact(wheel)


def test_casefold_colliding_archive_paths_are_rejected(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, native=False)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("PYELK/__init__.py", b"x")
    with pytest.raises(AuditError, match="collide after case normalization"):
        AUDITOR.inspect_artifact(wheel)


def test_artifact_path_must_be_a_regular_file_not_a_symlink(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, native=False)
    link = tmp_path / "candidate.whl"
    link.symlink_to(wheel)
    with pytest.raises(AuditError, match="symbolic link"):
        AUDITOR.inspect_artifact(link)


def test_artifact_cannot_change_during_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _wheel(tmp_path, native=False)
    original = AUDITOR._read_archive

    def mutate_after_read(path: Path) -> dict[str, bytes]:
        members = original(path)
        path.write_bytes(path.read_bytes() + b"changed-after-archive-read")
        return members

    monkeypatch.setattr(AUDITOR, "_read_archive", mutate_after_read)
    with pytest.raises(AuditError, match="changed during inspection"):
        AUDITOR.inspect_artifact(wheel)


def test_member_size_limit_is_checked_before_zip_decompression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _wheel(tmp_path, native=False)
    monkeypatch.setattr(AUDITOR, "MAX_MEMBER_SIZE", 1)

    def unexpected_read(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("oversized member was decompressed")

    monkeypatch.setattr(AUDITOR.zipfile.ZipFile, "read", unexpected_read)
    with pytest.raises(AuditError, match="member is too large"):
        AUDITOR.inspect_artifact(wheel)
