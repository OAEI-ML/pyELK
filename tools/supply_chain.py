"""Validate native dependencies and emit deterministic artifact SBOMs."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the Python 3.10 lane
    import tomli as tomllib  # type: ignore[import-not-found, unused-ignore]

Variant = Literal["pure", "native"]
_LOCAL_MANIFESTS = {
    "pyelk-core": Path("rust/pyelk-core/Cargo.toml"),
    "pyelk-pyo3": Path("rust/pyelk-pyo3/Cargo.toml"),
}
_CORE_REQUIREMENT = "pyowl-core>=0.2,<0.3"
_CORE_COMPATIBILITY_SCHEMA = "pyelk.core-compatibility/3"
_TESTED_CORE_COMMIT = "d39fe9c9bb9513db8c14fe2bc6d4864377901ad1"
_TESTED_CORE_TREE = "d29bbcc65684c5a246b5d952a91d8a62e07e1b35"
_CORE_CONTRACT = {
    "package_version": "0.2.0",
    "api_version": [0, 2],
    "model_schema": 2,
    "wire_format": [1, 2],
    "adapter_protocol": 1,
}
_NATIVE_ONTOLOGY_REDESIGN_CONTRACT = {
    "commit": _TESTED_CORE_COMMIT,
    "tree": _TESTED_CORE_TREE,
    "classification": "model-schema-2-component-scoped-anonymous-redesign",
    "workpackages": [
        "WP14",
        "WP15",
        "WP16",
        "WP17",
        "WP18",
        "WP19",
        "WP20",
        "WP21",
        "WP22",
        "WP23",
    ],
}
_ENCODED_INGESTION_CONTRACT = {
    "schema_name": "pyowl-core/structural-columns",
    "schema_version": 2,
    "descriptor_sha256": "c51d0eb7ecf6f29ad3495fe7c40a2ea6741cf03a7cf194d51417bb810df90f51",
    "capability_state": "advertised",
    "required_ingestion_path": "encoded-native",
    "parity_contract": "wp14-encoded-public-dispatch-short",
}
_FORBIDDEN_COMPONENTS = {"deeponto", "jpype", "jpype1", "mowl", "owlapi", "robot"}
_NOTICE_INVENTORY_START = "<!-- pyelk-native-inventory:start -->"
_NOTICE_INVENTORY_END = "<!-- pyelk-native-inventory:end -->"
_ALLOWED_LICENSE_EXPRESSIONS = {
    "Apache-2.0",
    "Apache-2.0 OR MIT",
    "Apache-2.0 WITH LLVM-exception",
    "BSD-3-Clause",
    "MIT",
    "MIT OR Apache-2.0",
    "(MIT OR Apache-2.0) AND Unicode-3.0",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_SELECTED_LICENSES = {
    "Apache-2.0",
    "Apache-2.0 AND Unicode-3.0",
    "Apache-2.0 WITH LLVM-exception",
    "BSD-3-Clause",
    "MIT",
}
_BUILD_INPUT_PATHS = (
    ".gitattributes",
    ".github/workflows/release.yml",
    ".github/workflows/wheels.yml",
    "Cargo.lock",
    "Cargo.toml",
    "MANIFEST.in",
    "pyelk_build.py",
    "pyproject.toml",
    "release/core-compatibility.json",
    "release/owner-release-authorization-0.2.0.md",
    "rust-toolchain.toml",
    "rust/pyelk-core/Cargo.toml",
    "rust/pyelk-pyo3/Cargo.toml",
    "rust/pyelk-pyo3/build.rs",
    "setup.py",
    "tests/backends/test_rust_core.py",
    "tests/packaging/install_artifact.py",
    "tests/packaging/installed_smoke.py",
    "tests/packaging/run_installed_wp14_contract.py",
    "tests/packaging/run_installed_suite.py",
    "tools/check_artifact.py",
    "tools/release_manifest.py",
    "tools/supply_chain.py",
)
_INSTALLED_NATIVE_CONTRACT_COMMAND = (
    "python {project}/tests/packaging/run_installed_wp14_contract.py"
)
_INSTALLED_NATIVE_CORE_COMMANDS = (
    (
        "python {project}/tests/packaging/run_installed_suite.py "
        "--backend rust --core-backend native --expected-ingestion encoded-native"
    ),
    (
        "python {project}/tests/packaging/run_installed_suite.py "
        "--backend python --core-backend native --expected-ingestion scalar-python"
    ),
)
_INSTALLED_PURE_CORE_COMMANDS = (
    (
        "python {project}/tests/packaging/run_installed_suite.py "
        "--backend rust --core-backend python --expected-ingestion encoded-native"
    ),
    (
        "python {project}/tests/packaging/run_installed_suite.py "
        "--backend python --core-backend python --expected-ingestion scalar-python"
    ),
)
_INSTALLED_NATIVE_CONTRACTS = (
    "tests/backends/test_rust_core.py::test_native_handshake_and_defensive_decoder",
    "tests/backends/test_rust_core.py::test_advertised_direct_encoded_session_matches_scalar_wire",
    (
        "tests/backends/test_rust_core.py"
        "::test_public_facade_runs_entirely_from_advertised_encoded_native_session"
    ),
    (
        "tests/backends/test_rust_core.py"
        "::test_public_advertised_dispatch_covers_mmap_and_recursive_segments"
    ),
    (
        "tests/backends/test_rust_core.py"
        "::test_public_advertised_dispatch_fails_closed_before_scalar_compilation"
    ),
)


@dataclass(frozen=True, slots=True)
class LockedPackage:
    """One package resolved by Cargo.lock."""

    name: str
    version: str
    checksum: str | None
    dependencies: tuple[str, ...]
    source: str | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.version)

    @property
    def bom_ref(self) -> str:
        return f"pkg:cargo/{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class InventoryComponent:
    """One reviewed production dependency."""

    name: str
    version: str
    license_expression: str
    selected_license: str
    scope: str
    additional_license_file: str | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.version)


@dataclass(frozen=True, slots=True)
class Inventory:
    """The reviewed production closure and approval state."""

    schema: int
    lockfile: str
    root_crate: str
    legal_approval: bool
    components: tuple[InventoryComponent, ...]


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        loaded = tomllib.load(stream)
    if not isinstance(loaded, dict):  # pragma: no cover - tomllib contract
        raise ValueError(f"{path} did not contain a TOML table")
    return loaded


def _required_string(values: dict[str, Any], field: str, *, context: str) -> str:
    value = values.get(field)
    if type(value) is not str or not value:
        raise ValueError(f"{context} {field} must be a non-empty string")
    return value


def load_locked_packages(path: Path) -> tuple[LockedPackage, ...]:
    """Load the lockfile without invoking Cargo or accessing a registry."""

    raw_packages = _load_toml(path).get("package", [])
    if not isinstance(raw_packages, list):
        raise ValueError("Cargo.lock package field must be an array")
    packages: list[LockedPackage] = []
    for raw in raw_packages:
        if not isinstance(raw, dict):
            raise ValueError("Cargo.lock package entry must be a table")
        dependencies = raw.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(value, str) for value in dependencies
        ):
            raise ValueError("Cargo.lock dependencies must be strings")
        checksum = raw.get("checksum")
        source = raw.get("source")
        if checksum is not None and type(checksum) is not str:
            raise ValueError("Cargo.lock checksum must be a string")
        if source is not None and type(source) is not str:
            raise ValueError("Cargo.lock source must be a string")
        packages.append(
            LockedPackage(
                name=_required_string(raw, "name", context="Cargo.lock package"),
                version=_required_string(raw, "version", context="Cargo.lock package"),
                checksum=checksum,
                dependencies=tuple(dependencies),
                source=source,
            )
        )
    return tuple(packages)


def load_inventory(path: Path) -> Inventory:
    """Load the reviewed inventory with strict required fields."""

    raw = _load_toml(path)
    schema = raw.get("schema")
    legal_approval = raw.get("legal_approval")
    if type(schema) is not int:
        raise ValueError("inventory schema must be an integer")
    if type(legal_approval) is not bool:
        raise ValueError("inventory legal_approval must be a boolean")
    raw_components = raw.get("component", [])
    if not isinstance(raw_components, list):
        raise ValueError("inventory component field must be an array")
    components: list[InventoryComponent] = []
    for entry in raw_components:
        if not isinstance(entry, dict):
            raise ValueError("inventory component entry must be a table")
        additional = entry.get("additional_license_file")
        if additional is not None and type(additional) is not str:
            raise ValueError("inventory additional_license_file must be a string")
        components.append(
            InventoryComponent(
                name=_required_string(entry, "name", context="inventory component"),
                version=_required_string(entry, "version", context="inventory component"),
                license_expression=_required_string(
                    entry,
                    "license",
                    context="inventory component",
                ),
                selected_license=_required_string(
                    entry,
                    "selected_license",
                    context="inventory component",
                ),
                scope=_required_string(entry, "scope", context="inventory component"),
                additional_license_file=additional,
            )
        )
    return Inventory(
        schema=schema,
        lockfile=_required_string(raw, "lockfile", context="inventory"),
        root_crate=_required_string(raw, "root_crate", context="inventory"),
        legal_approval=legal_approval,
        components=tuple(components),
    )


def _dependency_package(
    dependency: str,
    packages: tuple[LockedPackage, ...],
) -> LockedPackage | None:
    fields = dependency.split()
    candidates = [package for package in packages if package.name == fields[0]]
    if len(candidates) == 1:
        return candidates[0]
    if len(fields) > 1:
        matching = [package for package in candidates if package.version == fields[1]]
        if len(matching) == 1:
            return matching[0]
    return None


def _local_dependency_names(root: Path, package: LockedPackage) -> tuple[str, ...]:
    relative = _LOCAL_MANIFESTS.get(package.name)
    if relative is None:
        raise ValueError(f"unreviewed local crate {package.name} {package.version}")
    manifest = _load_toml(root / relative)
    dependencies = manifest.get("dependencies", {})
    build_dependencies = manifest.get("build-dependencies", {})
    if not isinstance(dependencies, dict) or not isinstance(build_dependencies, dict):
        raise ValueError(f"{relative} dependency tables must be TOML tables")
    names = {
        *(str(name) for name in dependencies),
        *(str(name) for name in build_dependencies),
    }
    return tuple(sorted(names))


def native_locked_packages(
    root: Path,
    packages: tuple[LockedPackage, ...] | None = None,
) -> tuple[LockedPackage, ...]:
    """Return the exact non-development closure linked or built for the extension."""

    resolved = packages if packages is not None else load_locked_packages(root / "Cargo.lock")
    inventory = load_inventory(root / "THIRD_PARTY_LICENSES" / "inventory.toml")
    roots = [package for package in resolved if package.name == inventory.root_crate]
    if len(roots) != 1 or roots[0].source is not None:
        raise ValueError(f"expected one local root crate {inventory.root_crate}")
    seen: set[tuple[str, str]] = set()
    pending = [roots[0]]
    while pending:
        package = pending.pop()
        if package.key in seen:
            continue
        seen.add(package.key)
        dependencies = (
            _local_dependency_names(root, package)
            if package.source is None
            else package.dependencies
        )
        for dependency in dependencies:
            selected = _dependency_package(dependency, resolved)
            if selected is None:
                raise ValueError(
                    f"unresolved dependency {dependency!r} required by "
                    f"{package.name} {package.version}"
                )
            pending.append(selected)
    return tuple(
        sorted(
            (package for package in resolved if package.key in seen and package.source is not None),
            key=lambda package: package.key,
        )
    )


def _runtime_requirement(root: Path) -> str:
    project = _load_toml(root / "pyproject.toml").get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml has no project table")
    dependencies = project.get("dependencies")
    if dependencies != [_CORE_REQUIREMENT]:
        raise ValueError(
            f"runtime dependencies must be exactly [{_CORE_REQUIREMENT!r}], found {dependencies!r}"
        )
    return _CORE_REQUIREMENT


def _local_keys(root: Path) -> set[tuple[str, str]]:
    workspace = _load_toml(root / "Cargo.toml").get("workspace")
    if not isinstance(workspace, dict):
        raise ValueError("Cargo.toml has no workspace table")
    package = workspace.get("package")
    if not isinstance(package, dict):
        raise ValueError("Cargo.toml has no workspace.package table")
    version = str(package["version"])
    keys: set[tuple[str, str]] = set()
    for relative in _LOCAL_MANIFESTS.values():
        crate = _load_toml(root / relative).get("package")
        if not isinstance(crate, dict):
            raise ValueError(f"{relative} has no package table")
        keys.add((str(crate["name"]), version))
    return keys


def _notice_inventory_lines(inventory: Inventory) -> tuple[str, ...]:
    lines: list[str] = []
    for component in sorted(inventory.components, key=lambda item: item.key):
        line = (
            f"- {component.name} {component.version}: "
            f"{component.selected_license} [{component.scope}]"
        )
        if component.additional_license_file is not None:
            line += f"; additional terms: {component.additional_license_file}"
        lines.append(line)
    return tuple(lines)


def validate_notice(root: Path, inventory: Inventory) -> list[str]:
    """Require NOTICE's dependency block to exactly match the inventory."""

    try:
        lines = (root / "NOTICE.pyelk").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return [f"inventory: cannot load NOTICE.pyelk: {error}"]
    if lines.count(_NOTICE_INVENTORY_START) != 1 or lines.count(_NOTICE_INVENTORY_END) != 1:
        return ["inventory: NOTICE.pyelk must contain exactly one native component block"]
    start = lines.index(_NOTICE_INVENTORY_START)
    end = lines.index(_NOTICE_INVENTORY_END)
    if end <= start or tuple(lines[start + 1 : end]) != _notice_inventory_lines(inventory):
        return ["inventory: NOTICE.pyelk native component block does not match inventory.toml"]
    return []


def validate_inventory(root: Path) -> list[str]:
    """Return deterministic dependency and license violations."""

    inventory_path = root / "THIRD_PARTY_LICENSES" / "inventory.toml"
    try:
        inventory = load_inventory(inventory_path)
    except (KeyError, OSError, TypeError, ValueError) as error:
        return [f"inventory: cannot load {inventory_path.relative_to(root).as_posix()}: {error}"]
    violations: list[str] = []
    violations.extend(validate_notice(root, inventory))
    if inventory.schema != 1:
        violations.append(f"inventory: unsupported schema {inventory.schema}")
    if inventory.lockfile != "Cargo.lock":
        violations.append(
            f"inventory: lockfile must be exactly Cargo.lock, got {inventory.lockfile!r}"
        )
    if inventory.root_crate != "pyelk-pyo3":
        violations.append(
            f"inventory: root crate must be exactly pyelk-pyo3, got {inventory.root_crate!r}"
        )
    try:
        _runtime_requirement(root)
        all_locked = load_locked_packages(root / "Cargo.lock")
        expected_local = _local_keys(root)
        native = native_locked_packages(root, all_locked)
    except (KeyError, OSError, TypeError, ValueError) as error:
        return [f"inventory: cannot resolve dependency manifests: {error}"]

    locked_keys = [package.key for package in all_locked]
    if len(set(locked_keys)) != len(locked_keys):
        violations.append("inventory: duplicate locked package identity")
    actual_local = {package.key for package in all_locked if package.source is None}
    if actual_local != expected_local:
        violations.append(
            "inventory: source-less lock packages differ from workspace members; "
            f"expected {sorted(expected_local)}, found {sorted(actual_local)}"
        )
    for package in all_locked:
        for dependency in package.dependencies:
            if _dependency_package(dependency, all_locked) is None:
                violations.append(
                    "inventory: unresolved locked dependency "
                    f"{dependency!r} required by {package.name} {package.version}"
                )
        if package.source is not None and package.checksum is None:
            violations.append(
                f"inventory: registry component lacks checksum {package.name} {package.version}"
            )
        if package.name.casefold() in _FORBIDDEN_COMPONENTS:
            violations.append(f"inventory: forbidden Java/JVM component {package.name}")

    native_keys = {package.key for package in native}
    inventory_keys = {component.key for component in inventory.components}
    if len(inventory_keys) != len(inventory.components):
        violations.append("inventory: duplicate component entry")
    for key in sorted(native_keys - inventory_keys):
        violations.append(f"inventory: unreviewed production component {key[0]} {key[1]}")
    for key in sorted(inventory_keys - native_keys):
        violations.append(f"inventory: component is not in production closure {key[0]} {key[1]}")
    expected_legal_files = {"README.md", "inventory.toml"}
    for component in inventory.components:
        if not component.license_expression.strip() or not component.selected_license.strip():
            violations.append(
                f"inventory: missing license selection {component.name} {component.version}"
            )
        if component.license_expression not in _ALLOWED_LICENSE_EXPRESSIONS:
            violations.append(
                "inventory: unreviewed SPDX expression "
                f"{component.license_expression!r} for {component.name}"
            )
        if component.selected_license not in _ALLOWED_SELECTED_LICENSES:
            violations.append(
                "inventory: unreviewed selected license "
                f"{component.selected_license!r} for {component.name}"
            )
        if component.scope not in {"native-build", "native-runtime"}:
            violations.append(f"inventory: invalid scope {component.scope!r} for {component.name}")
        if component.additional_license_file is not None:
            relative = PurePosixPath(component.additional_license_file)
            if (
                relative.is_absolute()
                or len(relative.parts) != 2
                or relative.parts[0] != "THIRD_PARTY_LICENSES"
                or relative.parts[1] in {"", ".", ".."}
            ):
                violations.append(
                    "inventory: unsafe additional license path "
                    f"{component.additional_license_file!r}"
                )
                continue
            expected_legal_files.add(relative.name)
            license_path = root / component.additional_license_file
            if not license_path.is_file() or not license_path.read_text(encoding="utf-8").strip():
                violations.append(
                    "inventory: missing additional license file "
                    f"{component.additional_license_file}"
                )
        elif component.selected_license != "Apache-2.0":
            violations.append(
                "inventory: non-Apache selection requires a packaged license file "
                f"for {component.name} {component.version}"
            )
    legal_root = root / "THIRD_PARTY_LICENSES"
    try:
        actual_legal_files = {path.name for path in legal_root.iterdir() if path.is_file()}
    except OSError as error:
        violations.append(f"inventory: cannot enumerate legal payloads: {error}")
    else:
        unexpected = sorted(actual_legal_files - expected_legal_files)
        if unexpected:
            violations.append(f"inventory: unreferenced legal payload files {unexpected}")
        if "README.md" not in actual_legal_files:
            violations.append("inventory: missing THIRD_PARTY_LICENSES/README.md")
    return sorted(violations)


def _project_version(root: Path) -> str:
    project = _load_toml(root / "pyproject.toml").get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError("pyproject.toml has no literal project version")
    return str(project["version"])


def _core_component() -> tuple[str, dict[str, Any]]:
    requirement = _CORE_REQUIREMENT.removeprefix("pyowl-core")
    reference = f"pkg:pypi/pyowl-core?requirement={quote(requirement, safe='')}"
    return (
        reference,
        {
            "type": "library",
            "bom-ref": reference,
            "name": "pyowl-core",
            "purl": "pkg:pypi/pyowl-core",
            "licenses": [{"expression": "Apache-2.0"}],
            "properties": [{"name": "pyelk:requirement", "value": requirement}],
        },
    )


def _direct_native_dependencies(
    root: Path,
    packages: tuple[LockedPackage, ...],
) -> tuple[str, ...]:
    inventory = load_inventory(root / "THIRD_PARTY_LICENSES" / "inventory.toml")
    root_package = next(package for package in packages if package.name == inventory.root_crate)
    direct: set[str] = set()
    seen_local: set[tuple[str, str]] = set()
    pending = [root_package]
    while pending:
        package = pending.pop()
        if package.key in seen_local:
            continue
        seen_local.add(package.key)
        for dependency in _local_dependency_names(root, package):
            selected = _dependency_package(dependency, packages)
            if selected is None:
                raise ValueError(f"unresolved local dependency {dependency!r}")
            if selected.source is None:
                pending.append(selected)
            else:
                direct.add(selected.bom_ref)
    return tuple(sorted(direct))


def build_cyclonedx(root: Path, variant: Variant) -> dict[str, Any]:
    """Build one deterministic CycloneDX 1.5 document."""

    violations = validate_inventory(root)
    if violations:
        raise ValueError("; ".join(violations))
    version = _project_version(root)
    inventory = load_inventory(root / "THIRD_PARTY_LICENSES" / "inventory.toml")
    all_packages = load_locked_packages(root / inventory.lockfile)
    native_packages = native_locked_packages(root, all_packages) if variant == "native" else ()
    reviewed = {component.key: component for component in inventory.components}
    root_ref = f"pkg:pypi/pyelk-reasoner@{version}?variant={variant}"
    core_ref, core = _core_component()

    components = [core]
    for package in native_packages:
        item = reviewed[package.key]
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": package.bom_ref,
            "name": package.name,
            "version": package.version,
            "purl": package.bom_ref,
            "licenses": [{"expression": item.license_expression}],
            "properties": [
                {"name": "pyelk:selected-license", "value": item.selected_license},
                {"name": "pyelk:scope", "value": item.scope},
            ],
        }
        if package.checksum is not None:
            component["hashes"] = [{"alg": "SHA-256", "content": package.checksum}]
        components.append(component)

    root_dependencies = [core_ref]
    dependency_rows = [{"ref": core_ref, "dependsOn": []}]
    if variant == "native":
        root_dependencies.extend(_direct_native_dependencies(root, all_packages))
        native_keys = {package.key for package in native_packages}
        for package in native_packages:
            dependencies = []
            for raw in package.dependencies:
                selected = _dependency_package(raw, all_packages)
                if selected is not None and selected.key in native_keys:
                    dependencies.append(selected.bom_ref)
            dependency_rows.append({"ref": package.bom_ref, "dependsOn": sorted(set(dependencies))})
    dependency_rows.insert(0, {"ref": root_ref, "dependsOn": sorted(root_dependencies)})

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, root_ref)}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "library",
                "bom-ref": root_ref,
                "name": "pyelk-reasoner",
                "version": version,
                "purl": f"pkg:pypi/pyelk-reasoner@{version}",
                "licenses": [{"expression": "Apache-2.0"}],
                "properties": [{"name": "pyelk:artifact-variant", "value": variant}],
            }
        },
        "components": components,
        "dependencies": dependency_rows,
    }
    errors = validate_cyclonedx(document, variant)
    if errors:  # pragma: no cover - protects future generator edits
        raise ValueError("; ".join(errors))
    return document


def validate_cyclonedx(document: dict[str, Any], variant: Variant) -> list[str]:
    """Validate the strict CycloneDX subset emitted for release artifacts."""

    errors: list[str] = []
    if set(document) != {
        "bomFormat",
        "components",
        "dependencies",
        "metadata",
        "serialNumber",
        "specVersion",
        "version",
    }:
        errors.append("sbom: unexpected top-level fields")
    if document.get("bomFormat") != "CycloneDX":
        errors.append("sbom: bomFormat must be CycloneDX")
    if document.get("specVersion") != "1.5" or document.get("version") != 1:
        errors.append("sbom: expected CycloneDX 1.5 document version 1")
    serial = document.get("serialNumber")
    try:
        parsed_serial = uuid.UUID(str(serial).removeprefix("urn:uuid:"))
    except ValueError:
        errors.append("sbom: serialNumber must be a UUID URN")
    else:
        if not isinstance(serial, str) or not serial.startswith("urn:uuid:"):
            errors.append("sbom: serialNumber must be a UUID URN")
        elif parsed_serial.version != 5:
            errors.append("sbom: serialNumber must be deterministic UUIDv5")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != {"component"}:
        errors.append("sbom: metadata must contain exactly the root component")
        return sorted(errors)
    root = metadata.get("component")
    if not isinstance(root, dict):
        errors.append("sbom: root component must be an object")
        return sorted(errors)
    root_ref = root.get("bom-ref")
    expected_root_fields = {
        "bom-ref",
        "licenses",
        "name",
        "properties",
        "purl",
        "type",
        "version",
    }
    if set(root) != expected_root_fields:
        errors.append("sbom: root component fields differ from the release schema")
    if (
        root.get("type") != "library"
        or root.get("name") != "pyelk-reasoner"
        or not isinstance(root_ref, str)
    ):
        errors.append("sbom: invalid root component identity")
    if root.get("properties") != [{"name": "pyelk:artifact-variant", "value": variant}]:
        errors.append("sbom: root component variant differs")
    if root.get("licenses") != [{"expression": "Apache-2.0"}]:
        errors.append("sbom: root component license differs")

    components = document.get("components")
    if not isinstance(components, list):
        errors.append("sbom: components must be an array")
        return sorted(errors)
    expected_component_count = 1 if variant == "pure" else 32
    if len(components) != expected_component_count:
        errors.append(
            f"sbom: {variant} component count is {len(components)}, "
            f"expected {expected_component_count}"
        )
    component_refs: set[str] = set()
    cargo_count = 0
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            errors.append(f"sbom: component {index} must be an object")
            continue
        reference = component.get("bom-ref")
        if not isinstance(reference, str) or not reference:
            errors.append(f"sbom: component {index} has no bom-ref")
            continue
        if reference in component_refs:
            errors.append(f"sbom: duplicate component bom-ref {reference}")
        component_refs.add(reference)
        licenses = component.get("licenses")
        if (
            not isinstance(licenses, list)
            or len(licenses) != 1
            or not isinstance(licenses[0], dict)
            or licenses[0].get("expression") not in _ALLOWED_LICENSE_EXPRESSIONS
        ):
            errors.append(f"sbom: component {reference} has an unreviewed SPDX license")
        if reference.startswith("pkg:cargo/"):
            cargo_count += 1
            hashes = component.get("hashes")
            if (
                not isinstance(hashes, list)
                or len(hashes) != 1
                or not isinstance(hashes[0], dict)
                or hashes[0].get("alg") != "SHA-256"
                or not isinstance(hashes[0].get("content"), str)
                or _SHA256.fullmatch(hashes[0]["content"]) is None
            ):
                errors.append(f"sbom: component {reference} lacks its locked SHA-256")
    expected_cargo_count = 0 if variant == "pure" else 31
    if cargo_count != expected_cargo_count:
        errors.append(
            f"sbom: {variant} Cargo component count is {cargo_count}, "
            f"expected {expected_cargo_count}"
        )

    dependency_rows = document.get("dependencies")
    if not isinstance(dependency_rows, list):
        errors.append("sbom: dependencies must be an array")
        return sorted(errors)
    row_refs: set[str] = set()
    for index, row in enumerate(dependency_rows):
        if not isinstance(row, dict) or set(row) != {"dependsOn", "ref"}:
            errors.append(f"sbom: dependency row {index} has invalid fields")
            continue
        reference = row.get("ref")
        depends_on = row.get("dependsOn")
        if (
            not isinstance(reference, str)
            or not isinstance(depends_on, list)
            or not all(isinstance(value, str) for value in depends_on)
        ):
            errors.append(f"sbom: dependency row {index} has invalid types")
            continue
        if reference in row_refs:
            errors.append(f"sbom: duplicate dependency row {reference}")
        row_refs.add(reference)
        if depends_on != sorted(set(depends_on)):
            errors.append(f"sbom: dependency row {reference} is not canonical")
        unknown = set(depends_on) - component_refs
        if unknown:
            errors.append(
                f"sbom: dependency row {reference} names unknown components {sorted(unknown)}"
            )
    expected_row_refs = component_refs | ({root_ref} if isinstance(root_ref, str) else set())
    if row_refs != expected_row_refs:
        errors.append(
            "sbom: dependency rows do not cover root and components; "
            f"missing={sorted(expected_row_refs - row_refs)}, "
            f"extra={sorted(row_refs - expected_row_refs)}"
        )
    return sorted(errors)


def build_dependency_inventory(root: Path) -> dict[str, Any]:
    """Render a machine-readable dependency and approval ledger."""

    violations = validate_inventory(root)
    if violations:
        raise ValueError("; ".join(violations))
    inventory = load_inventory(root / "THIRD_PARTY_LICENSES" / "inventory.toml")
    locked = {
        package.key: package
        for package in native_locked_packages(
            root,
            load_locked_packages(root / inventory.lockfile),
        )
    }
    components = []
    for item in sorted(inventory.components, key=lambda component: component.key):
        package = locked[item.key]
        components.append(
            {
                "name": item.name,
                "version": item.version,
                "checksum_sha256": package.checksum,
                "license": item.license_expression,
                "selected_license": item.selected_license,
                "scope": item.scope,
                "source": package.source,
            }
        )
    return {
        "schema": 1,
        "distribution": "pyelk-reasoner",
        "version": _project_version(root),
        "python_runtime_dependencies": [
            {"name": "pyowl-core", "requirement": _CORE_REQUIREMENT.removeprefix("pyowl-core")}
        ],
        "native_components": components,
        "java_components": [],
        "legal_approval": inventory.legal_approval,
        "release_blockers": (
            []
            if inventory.legal_approval
            else ["third-party license review requires release-owner or counsel approval"]
        ),
    }


def _workflow_pin(text: str, pattern: str, label: str) -> str:
    values: set[str] = set(re.findall(pattern, text))
    if len(values) != 1:
        raise ValueError(f"build provenance: expected one unique {label}, got {sorted(values)!r}")
    return values.pop()


def _shell_assignment(script: str, name: str) -> str:
    return _workflow_pin(
        script,
        rf"(?m)^{re.escape(name)}=([A-Za-z0-9_.-]+)$",
        f"Linux bootstrap {name}",
    )


def _literal_string_tuple(script: str, name: str) -> tuple[str, ...]:
    try:
        module = ast.parse(script)
    except SyntaxError as error:
        raise ValueError(f"build provenance: {name} source is not valid Python") from error
    matches = [
        statement.value
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == name
    ]
    if len(matches) != 1:
        raise ValueError(f"build provenance: expected one literal {name} assignment")
    try:
        value = ast.literal_eval(matches[0])
    except (TypeError, ValueError) as error:
        raise ValueError(f"build provenance: {name} must be a literal string tuple") from error
    if (
        not isinstance(value, tuple)
        or not value
        or not all(isinstance(item, str) for item in value)
    ):
        raise ValueError(f"build provenance: {name} must be a nonempty literal string tuple")
    return value


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_mode,
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_file_identity(path: Path) -> tuple[bytes, dict[str, Any]]:
    before = path.stat(follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode):
        raise ValueError(f"build provenance: input must not be a symlink: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"build provenance: input must be a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stat_identity(before) != _stat_identity(opened):
            raise ValueError(f"build provenance: input changed while opening: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        completed = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = path.stat(follow_symlinks=False)
    payload = b"".join(chunks)
    if (
        len(
            {
                _stat_identity(before),
                _stat_identity(opened),
                _stat_identity(completed),
                _stat_identity(after),
            }
        )
        != 1
        or not stat.S_ISREG(after.st_mode)
        or len(payload) != completed.st_size
    ):
        raise ValueError(f"build provenance: input changed while hashing: {path}")
    return (
        payload,
        {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )


def build_provenance(root: Path) -> dict[str, Any]:
    """Bind release tool pins to every deterministic build-control input."""

    payloads: dict[str, bytes] = {}
    inputs: dict[str, dict[str, Any]] = {}
    for relative in _BUILD_INPUT_PATHS:
        path = root / relative
        try:
            payload, identity = _read_file_identity(path)
        except OSError as error:
            raise ValueError(f"build provenance: cannot hash input {relative}: {error}") from error
        payloads[relative] = payload
        inputs[relative] = identity

    def bound_text(relative: str) -> str:
        try:
            return payloads[relative].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"build provenance: input {relative} is not valid UTF-8") from error

    wheels = bound_text(".github/workflows/wheels.yml")
    selector = tomllib.loads(bound_text("rust-toolchain.toml")).get("toolchain")
    workspace = tomllib.loads(bound_text("Cargo.toml")).get("workspace")
    if not isinstance(selector, dict):
        raise ValueError("build provenance: rust-toolchain.toml has no toolchain table")
    if not isinstance(workspace, dict) or not isinstance(workspace.get("package"), dict):
        raise ValueError("build provenance: Cargo.toml has no workspace.package table")
    selector_channel = selector.get("channel")
    rust_msrv = workspace["package"].get("rust-version")
    if type(selector_channel) is not str or type(rust_msrv) is not str:
        raise ValueError("build provenance: Rust selector and MSRV must be literal strings")
    workflow_toolchain = _workflow_pin(
        wheels,
        r"rustup toolchain install ([0-9]+\.[0-9]+\.[0-9]+)",
        "Rust release toolchain",
    )
    pyproject = bound_text("pyproject.toml")
    pyproject_document = tomllib.loads(pyproject)
    project = pyproject_document.get("project")
    if not isinstance(project, dict) or type(project.get("version")) is not str:
        raise ValueError("build provenance: pyproject.toml has no literal project version")
    project_version = project["version"]
    tool = pyproject_document.get("tool")
    cibuildwheel = tool.get("cibuildwheel") if isinstance(tool, dict) else None
    test_command = cibuildwheel.get("test-command") if isinstance(cibuildwheel, dict) else None
    if not isinstance(test_command, str):
        raise ValueError("build provenance: cibuildwheel test-command must be a literal string")
    commands = tuple(command.strip() for command in test_command.split("&&"))
    if commands != (_INSTALLED_NATIVE_CONTRACT_COMMAND, *_INSTALLED_NATIVE_CORE_COMMANDS):
        raise ValueError(
            "build provenance: native wheels must run the bounded installed WP14 contract "
            "first and require the native pyowl-core backend"
        )
    overrides = cibuildwheel.get("overrides") if isinstance(cibuildwheel, dict) else None
    if not isinstance(overrides, list) or len(overrides) != 2:
        raise ValueError(
            "build provenance: expected manylinux and musllinux cibuildwheel overrides"
        )
    override_commands: dict[str, tuple[str, ...]] = {}
    for override in overrides:
        selector = override.get("select") if isinstance(override, dict) else None
        override_test_command = override.get("test-command") if isinstance(override, dict) else None
        if not isinstance(selector, str) or not isinstance(override_test_command, str):
            raise ValueError(
                "build provenance: cibuildwheel core-backend overrides must be literal"
            )
        override_commands[selector] = tuple(
            command.strip() for command in override_test_command.split("&&")
        )
    if set(override_commands) != {"*-manylinux*", "*-musllinux*"} or any(
        value != (_INSTALLED_NATIVE_CONTRACT_COMMAND, *_INSTALLED_PURE_CORE_COMMANDS)
        for value in override_commands.values()
    ):
        raise ValueError(
            "build provenance: old-glibc and musllinux builders must require pure pyowl-core"
        )
    try:
        compiler_free = wheels.split("  compiler-free-installed:", maxsplit=1)[1].split(
            "  native-wheels:",
            maxsplit=1,
        )[0]
        native_wheels = wheels.split("  native-wheels:", maxsplit=1)[1].split(
            "  abi3-supported-cpython:",
            maxsplit=1,
        )[0]
        abi3 = wheels.split("  abi3-supported-cpython:", maxsplit=1)[1].split(
            "  musllinux-supported-cpython:",
            maxsplit=1,
        )[0]
        musllinux = wheels.split("  musllinux-supported-cpython:", maxsplit=1)[1].split(
            "  artifact-consistency:",
            maxsplit=1,
        )[0]
    except (IndexError, ValueError) as error:
        raise ValueError("build provenance: installed-wheel job boundaries are invalid") from error
    if (
        "--platform any" not in compiler_free
        or compiler_free.count("--expected-core-backend python") != 2
        or "--expected-core-backend native" in compiler_free
    ):
        raise ValueError(
            "build provenance: compiler-free wheels must force and require pure pyowl-core"
        )
    if (
        native_wheels.count("--core-backend native") != 2
        or "--core-backend python" in native_wheels
        or abi3.count("--expected-core-backend native") != 2
        or abi3.count("--expected-core-backend python") != 2
        or "--platform any" not in abi3
    ):
        raise ValueError(
            "build provenance: approved native platforms must require native pyowl-core "
            "and exercise the universal-core fallback"
        )
    if (
        "--platform any" not in musllinux
        or musllinux.count("--expected-core-backend python") != 2
        or "--expected-core-backend native" in musllinux
    ):
        raise ValueError("build provenance: musllinux must force and require pure pyowl-core")
    contract_script = bound_text("tests/packaging/run_installed_wp14_contract.py")
    if _literal_string_tuple(contract_script, "CONTRACT_NODE_IDS") != _INSTALLED_NATIVE_CONTRACTS:
        raise ValueError(
            "build provenance: installed WP14 contract does not select the exact reviewed tests"
        )
    try:
        core_compatibility = json.loads(bound_text("release/core-compatibility.json"))
    except (TypeError, ValueError) as error:
        raise ValueError("build provenance: core compatibility pin is not valid JSON") from error
    if not isinstance(core_compatibility, dict):
        raise ValueError("build provenance: core compatibility pin is not an object")
    tested_core = core_compatibility.get("tested_source")
    core_contract = core_compatibility.get("core_contract")
    redesign = core_compatibility.get("native_ontology_redesign")
    encoded_ingestion = core_compatibility.get("encoded_ingestion")
    if (
        core_compatibility.get("schema") != _CORE_COMPATIBILITY_SCHEMA
        or core_compatibility.get("dependency_constraint") != _CORE_REQUIREMENT
        or core_contract != _CORE_CONTRACT
        or not isinstance(tested_core, dict)
        or tested_core.get("repository") != "https://github.com/OAEI-ML/pyOWLCore"
        or tested_core.get("version") != "0.2.0"
        or tested_core.get("commit") != _TESTED_CORE_COMMIT
        or tested_core.get("tree") != _TESTED_CORE_TREE
        or redesign != _NATIVE_ONTOLOGY_REDESIGN_CONTRACT
        or encoded_ingestion != _ENCODED_INGESTION_CONTRACT
    ):
        raise ValueError("build provenance: core compatibility pin is invalid")
    linux = cibuildwheel.get("linux") if isinstance(cibuildwheel, dict) else None
    bootstrap = linux.get("before-all") if isinstance(linux, dict) else None
    if not isinstance(bootstrap, str):
        raise ValueError("build provenance: Linux Rust bootstrap must be a literal string")
    rustup_version = _shell_assignment(bootstrap, "rustup_version")
    container_toolchain = _shell_assignment(bootstrap, "rust_toolchain")
    musllinux_rust = _shell_assignment(bootstrap, "musllinux_rust")
    musllinux_cargo = _shell_assignment(bootstrap, "musllinux_cargo")
    rustup_installer_sha256 = sorted(
        set(re.findall(r"(?m)^\s*rustup_sha256=([0-9a-f]{64})$", bootstrap))
    )
    if len(rustup_installer_sha256) != 2:
        raise ValueError(
            "build provenance: Linux Rust bootstrap must bind two glibc installer checksums"
        )
    apk_package = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-r[0-9]+$")
    if (
        apk_package.fullmatch(musllinux_rust) is None
        or apk_package.fullmatch(musllinux_cargo) is None
        or 'apk add --no-cache "rust=$musllinux_rust" "cargo=$musllinux_cargo"' not in bootstrap
    ):
        raise ValueError(
            "build provenance: musllinux Rust and Cargo packages must be exact apk pins"
        )
    if (
        "https://static.rust-lang.org/rustup/archive/$rustup_version/$rustup_host/rustup-init"
        not in bootstrap
        or "sha256sum -c" not in bootstrap
        or "https://sh.rustup.rs" in bootstrap
        or re.search(r"\|\s*(?:sh|bash)(?:\s|$)", bootstrap) is not None
    ):
        raise ValueError(
            "build provenance: Linux Rust bootstrap is not archive- and checksum-bound"
        )
    if {selector_channel, workflow_toolchain, container_toolchain} != {selector_channel}:
        raise ValueError(
            "build provenance: Rust toolchain pins differ: "
            f"selector={selector_channel!r}, workflow={workflow_toolchain!r}, "
            f"container={container_toolchain!r}"
        )
    epoch_command = _workflow_pin(
        wheels,
        r"SOURCE_DATE_EPOCH=\$\((git show -s --format=%ct HEAD)\)",
        "SOURCE_DATE_EPOCH derivation",
    )
    versions = {
        "python_build_frontend": (
            "build==" + _workflow_pin(wheels, r"\bbuild==([0-9][^\s]+)", "build frontend")
        ),
        "python_build_backend": (
            "setuptools=="
            + _workflow_pin(wheels, r"\bsetuptools==([0-9][^\s]+)", "setuptools backend")
        ),
        "setuptools_rust": (
            "setuptools-rust=="
            + _workflow_pin(
                wheels,
                r"\bsetuptools-rust==([0-9][^\s]+)",
                "setuptools-rust builder",
            )
        ),
        "wheel_builder": (
            "wheel==" + _workflow_pin(wheels, r"\bwheel==([0-9][^\s]+)", "wheel builder")
        ),
        "cibuildwheel_action": (
            "pypa/cibuildwheel@"
            + _workflow_pin(
                wheels,
                r"pypa/cibuildwheel@([0-9a-f]{40})",
                "cibuildwheel action revision",
            )
        ),
        "abi3audit": (
            "abi3audit==" + _workflow_pin(wheels, r"\babi3audit==([0-9][^\s]+)", "ABI3 auditor")
        ),
        "auditwheel": (
            "auditwheel==" + _workflow_pin(wheels, r"\bauditwheel==([0-9][^\s]+)", "Linux auditor")
        ),
        "delocate": (
            "delocate==" + _workflow_pin(wheels, r"\bdelocate==([0-9][^\s]+)", "macOS auditor")
        ),
        "delvewheel": (
            "delvewheel=="
            + _workflow_pin(wheels, r"\bdelvewheel==([0-9][^\s]+)", "Windows auditor")
        ),
    }
    musllinux_smoke_images = sorted(
        set(
            re.findall(
                r"\bimage:\s+(python:3\.(?:11|12|13|14)-alpine@sha256:[0-9a-f]{64})",
                wheels,
            )
        )
    )
    if len(musllinux_smoke_images) != 4:
        raise ValueError(
            "build provenance: expected four digest-pinned musllinux smoke images, "
            f"got {musllinux_smoke_images!r}"
        )
    return {
        "schema": "pyelk.build-provenance/1",
        "distribution": "pyelk-reasoner",
        "version": project_version,
        "source_date_epoch": {
            "strategy": "git-commit-timestamp",
            "command": epoch_command,
        },
        "tested_runtime": {
            "pyowl_core": dict(sorted(tested_core.items())),
        },
        "core_contract": dict(sorted(core_contract.items())),
        "native_ontology_redesign": dict(sorted(redesign.items())),
        "encoded_ingestion_contract": dict(sorted(encoded_ingestion.items())),
        "installed_core_backend_contracts": {
            "approved_native_hosted_lanes": "native",
            "compiler_free_forced_any": "python",
            "manylinux2014_build_container": "python",
            "musllinux": "python",
            "native_wheel_universal_core_fallback": "python",
        },
        "installed_native_contracts": [
            {
                "capability_state": "advertised",
                "command": _INSTALLED_NATIVE_CONTRACT_COMMAND,
                "id": "wp14-encoded-public-dispatch-short",
                "scope": "bounded-correctness-only",
                "tests": list(_INSTALLED_NATIVE_CONTRACTS),
            }
        ],
        "tools": {
            "rust_toolchain": selector_channel,
            "cargo_manifest_rust_version": rust_msrv,
            "rustup": rustup_version,
            "rustup_installer_sha256": rustup_installer_sha256,
            "musllinux_rust_package": f"rust={musllinux_rust}",
            "musllinux_cargo_package": f"cargo={musllinux_cargo}",
            "musllinux_smoke_images": musllinux_smoke_images,
            **versions,
        },
        "inputs": inputs,
    }


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generate_evidence(root: Path, output_dir: Path, *, check: bool = False) -> list[str]:
    """Write or verify deterministic inventory and pure/native SBOM files."""

    documents = {
        "build-provenance.json": build_provenance(root),
        "dependency-inventory.json": build_dependency_inventory(root),
        "sbom-native.cdx.json": build_cyclonedx(root, "native"),
        "sbom-pure.cdx.json": build_cyclonedx(root, "pure"),
    }
    drift: list[str] = []
    if not check:
        output_dir.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        path = output_dir / name
        rendered = _canonical_json(document)
        if check:
            try:
                actual = path.read_text(encoding="utf-8")
            except OSError:
                drift.append(f"supply-chain: missing generated evidence {path}")
            else:
                if actual != rendered:
                    drift.append(f"supply-chain: generated evidence drift {path}")
        else:
            path.write_text(rendered, encoding="utf-8")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="fail unless the reviewed inventory records release-owner legal approval",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else root / "reports" / "release" / _project_version(root)
    )
    violations = validate_inventory(root)
    if not violations:
        violations.extend(generate_evidence(root, output_dir, check=args.check))
    if not violations and args.require_approval:
        inventory = load_inventory(root / "THIRD_PARTY_LICENSES" / "inventory.toml")
        if not inventory.legal_approval:
            violations.append(
                "release: third-party license review requires release-owner or counsel approval"
            )
    for violation in violations:
        print(violation)
    if violations:
        return 1
    action = "verified" if args.check else "generated"
    print(f"supply-chain evidence {action}: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
