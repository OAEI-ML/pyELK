from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import tools.supply_chain as SUPPLY_CHAIN
from tools.supply_chain import (
    build_cyclonedx,
    build_dependency_inventory,
    build_provenance,
    generate_evidence,
    load_locked_packages,
    main,
    native_locked_packages,
    validate_cyclonedx,
    validate_inventory,
)

ROOT = Path(__file__).resolve().parents[2]


def _copy_manifests(target: Path) -> None:
    shutil.copytree(ROOT / "THIRD_PARTY_LICENSES", target / "THIRD_PARTY_LICENSES")
    shutil.copy2(ROOT / "NOTICE.pyelk", target / "NOTICE.pyelk")
    shutil.copy2(ROOT / "Cargo.lock", target / "Cargo.lock")
    shutil.copy2(ROOT / "Cargo.toml", target / "Cargo.toml")
    shutil.copy2(ROOT / "pyproject.toml", target / "pyproject.toml")
    for relative in (
        Path("rust/pyelk-core/Cargo.toml"),
        Path("rust/pyelk-pyo3/Cargo.toml"),
    ):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)


def _copy_build_inputs(target: Path) -> None:
    for relative in build_provenance(ROOT)["inputs"]:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def test_reviewed_inventory_matches_the_production_lock_closure() -> None:
    assert validate_inventory(ROOT) == []
    all_locked = load_locked_packages(ROOT / "Cargo.lock")
    production = native_locked_packages(ROOT, all_locked)
    inventory = build_dependency_inventory(ROOT)

    assert len(production) == len(inventory["native_components"]) == 31
    assert "criterion" not in {package.name for package in production}
    assert inventory["python_runtime_dependencies"] == [
        {"name": "pyowl-core", "requirement": ">=0.2,<0.3"}
    ]
    assert inventory["java_components"] == []
    assert inventory["legal_approval"] is True
    assert inventory["release_blockers"] == []


def test_pure_and_native_sboms_are_variant_exact_and_deterministic() -> None:
    pure = build_cyclonedx(ROOT, "pure")
    native = build_cyclonedx(ROOT, "native")

    assert pure == build_cyclonedx(ROOT, "pure")
    assert native == build_cyclonedx(ROOT, "native")
    assert [component["name"] for component in pure["components"]] == ["pyowl-core"]
    assert len(native["components"]) == 32
    assert native["metadata"]["component"]["version"] == "0.2.0"
    assert native["dependencies"][0]["dependsOn"] == [
        "pkg:cargo/blake2@0.10.6",
        "pkg:cargo/pyo3@0.29.0",
        "pkg:cargo/rayon@1.12.0",
        "pkg:cargo/sha2@0.10.9",
        "pkg:pypi/pyowl-core?requirement=%3E%3D0.2%2C%3C0.3",
    ]
    cargo = [
        component
        for component in native["components"]
        if component["bom-ref"].startswith("pkg:cargo/")
    ]
    assert all(component["hashes"][0]["alg"] == "SHA-256" for component in cargo)
    assert validate_cyclonedx(pure, "pure") == []
    assert validate_cyclonedx(native, "native") == []


def test_sbom_validator_rejects_non_spdx_or_unbound_components() -> None:
    document = deepcopy(build_cyclonedx(ROOT, "native"))
    document["components"][1]["licenses"] = [{"expression": "MIT/Apache-2.0"}]
    document["dependencies"][0]["dependsOn"].append("pkg:cargo/unbound@9.9.9")

    assert validate_cyclonedx(document, "native") == [
        "sbom: component pkg:cargo/blake2@0.10.6 has an unreviewed SPDX license",
        "sbom: dependency row pkg:pypi/pyelk-reasoner@0.2.0?variant=native is not canonical",
        "sbom: dependency row pkg:pypi/pyelk-reasoner@0.2.0?variant=native "
        "names unknown components ['pkg:cargo/unbound@9.9.9']",
    ]


def test_build_provenance_binds_toolchain_auditors_and_build_inputs() -> None:
    provenance = build_provenance(ROOT)

    assert provenance["schema"] == "pyelk.build-provenance/1"
    assert provenance["source_date_epoch"] == {
        "strategy": "git-commit-timestamp",
        "command": "git show -s --format=%ct HEAD",
    }
    assert provenance["tested_runtime"] == {
        "pyowl_core": {
            "commit": "d39fe9c9bb9513db8c14fe2bc6d4864377901ad1",
            "repository": "https://github.com/OAEI-ML/pyOWLCore",
            "tree": "d29bbcc65684c5a246b5d952a91d8a62e07e1b35",
            "version": "0.2.0",
        }
    }
    assert provenance["core_contract"] == {
        "adapter_protocol": 1,
        "api_version": [0, 2],
        "model_schema": 2,
        "package_version": "0.2.0",
        "wire_format": [1, 2],
    }
    assert provenance["native_ontology_redesign"] == {
        "classification": "model-schema-2-component-scoped-anonymous-redesign",
        "commit": "d39fe9c9bb9513db8c14fe2bc6d4864377901ad1",
        "tree": "d29bbcc65684c5a246b5d952a91d8a62e07e1b35",
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
    assert provenance["encoded_ingestion_contract"] == {
        "capability_state": "advertised",
        "descriptor_sha256": ("c51d0eb7ecf6f29ad3495fe7c40a2ea6741cf03a7cf194d51417bb810df90f51"),
        "parity_contract": "wp14-encoded-public-dispatch-short",
        "required_ingestion_path": "encoded-native",
        "schema_name": "pyowl-core/structural-columns",
        "schema_version": 2,
    }
    assert provenance["installed_core_backend_contracts"] == {
        "approved_native_hosted_lanes": "native",
        "compiler_free_forced_any": "python",
        "manylinux2014_build_container": "python",
        "musllinux": "python",
        "native_wheel_universal_core_fallback": "python",
    }
    assert provenance["installed_native_contracts"] == [
        {
            "capability_state": "advertised",
            "command": "python {project}/tests/packaging/run_installed_wp14_contract.py",
            "id": "wp14-encoded-public-dispatch-short",
            "scope": "bounded-correctness-only",
            "tests": [
                "tests/backends/test_rust_core.py::test_native_handshake_and_defensive_decoder",
                (
                    "tests/backends/test_rust_core.py"
                    "::test_advertised_direct_encoded_session_matches_scalar_wire"
                ),
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
            ],
        }
    ]
    assert provenance["tools"] == {
        "rust_toolchain": "1.97.1",
        "cargo_manifest_rust_version": "1.85",
        "rustup": "1.28.2",
        "rustup_installer_sha256": [
            "20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c",
            "e3853c5a252fca15252d07cb23a1bdd9377a8c6f3efa01531109281ae47f841c",
        ],
        "musllinux_rust_package": "rust=1.87.0-r1",
        "musllinux_cargo_package": "cargo=1.87.0-r1",
        "musllinux_smoke_images": [
            "python:3.11-alpine@sha256:"
            "25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4",
            "python:3.12-alpine@sha256:"
            "6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df",
            "python:3.13-alpine@sha256:"
            "399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0",
            "python:3.14-alpine@sha256:"
            "26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92",
        ],
        "python_build_frontend": "build==1.5.0",
        "python_build_backend": "setuptools==83.0.0",
        "setuptools_rust": "setuptools-rust==1.13.0",
        "wheel_builder": "wheel==0.46.3",
        "cibuildwheel_action": ("pypa/cibuildwheel@a0a973acdc9e7b7f8b04ac5c80e6883a5a102615"),
        "abi3audit": "abi3audit==0.0.26",
        "auditwheel": "auditwheel==6.7.0",
        "delocate": "delocate==0.13.0",
        "delvewheel": "delvewheel==1.13.0",
    }
    lock = (ROOT / "Cargo.lock").read_bytes()
    assert provenance["inputs"]["Cargo.lock"] == {
        "bytes": len(lock),
        "sha256": hashlib.sha256(lock).hexdigest(),
    }
    release_manifest = (ROOT / "tools/release_manifest.py").read_bytes()
    assert provenance["inputs"]["tools/release_manifest.py"] == {
        "bytes": len(release_manifest),
        "sha256": hashlib.sha256(release_manifest).hexdigest(),
    }
    release_workflow = (ROOT / ".github/workflows/release.yml").read_bytes()
    assert provenance["inputs"][".github/workflows/release.yml"] == {
        "bytes": len(release_workflow),
        "sha256": hashlib.sha256(release_workflow).hexdigest(),
    }


def test_build_provenance_rejects_divergent_rust_toolchain_pins(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    selector = tmp_path / "rust-toolchain.toml"
    selector.write_text(
        selector.read_text(encoding="utf-8").replace('channel = "1.97.1"', 'channel = "1.96.0"'),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Rust toolchain pins differ"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_missing_installed_wp14_contract(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            "python {project}/tests/packaging/run_installed_wp14_contract.py && ",
            "",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bounded installed WP14 contract"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_pure_core_on_approved_native_platforms(
    tmp_path: Path,
) -> None:
    _copy_build_inputs(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    default, override = text.split("[[tool.cibuildwheel.overrides]]", maxsplit=1)
    pyproject.write_text(
        default.replace("--core-backend native", "--core-backend python")
        + "[[tool.cibuildwheel.overrides]]"
        + override,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="require the native pyowl-core backend"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_native_core_on_musllinux(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    default, manylinux, musllinux = text.split("[[tool.cibuildwheel.overrides]]")
    pyproject.write_text(
        default
        + "[[tool.cibuildwheel.overrides]]"
        + manylinux
        + "[[tool.cibuildwheel.overrides]]"
        + musllinux.replace("--core-backend python", "--core-backend native"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="old-glibc and musllinux builders must require pure"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_unforced_pure_core_wheelhouse(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    workflow = tmp_path / ".github/workflows/wheels.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            '--platform any "pyowl-core==0.2.0"',
            '"pyowl-core==0.2.0"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="force and require pure pyowl-core"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_unforced_musllinux_core_wheelhouse(
    tmp_path: Path,
) -> None:
    _copy_build_inputs(tmp_path)
    workflow = tmp_path / ".github/workflows/wheels.yml"
    text = workflow.read_text(encoding="utf-8")
    prefix, musllinux = text.split("  musllinux-supported-cpython:", maxsplit=1)
    workflow.write_text(
        prefix
        + "  musllinux-supported-cpython:"
        + musllinux.replace(
            '--platform any "pyowl-core==0.2.0"',
            '"pyowl-core==0.2.0"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="musllinux must force and require pure pyowl-core"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_unbound_core_implementation(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    compatibility = tmp_path / "release" / "core-compatibility.json"
    compatibility.write_text(
        compatibility.read_text(encoding="utf-8").replace(
            "d39fe9c9bb9513db8c14fe2bc6d4864377901ad1",
            "c3e7893b0609fcd7df390375267a00356f09cb22",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="core compatibility pin"):
        build_provenance(tmp_path)


@pytest.mark.parametrize(
    ("bound_value", "replacement"),
    [
        (
            "d29bbcc65684c5a246b5d952a91d8a62e07e1b35",
            "22cc4cbf9c99f1b45785cb29f4f059ec0f86a691",
        ),
        (
            "c51d0eb7ecf6f29ad3495fe7c40a2ea6741cf03a7cf194d51417bb810df90f51",
            "d51d0eb7ecf6f29ad3495fe7c40a2ea6741cf03a7cf194d51417bb810df90f51",
        ),
        ('"wire_format": [1, 2]', '"wire_format": [1, 1]'),
        (
            "model-schema-2-component-scoped-anonymous-redesign",
            "behavior-preserving-native-ontology-redesign",
        ),
        ('"WP23"', '"WP24"'),
        ("encoded-native", "scalar-wire"),
    ],
)
def test_build_provenance_rejects_divergent_core_release_contract(
    tmp_path: Path,
    bound_value: str,
    replacement: str,
) -> None:
    _copy_build_inputs(tmp_path)
    compatibility = tmp_path / "release" / "core-compatibility.json"
    compatibility.write_text(
        compatibility.read_text(encoding="utf-8").replace(bound_value, replacement),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="core compatibility pin"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_mutable_rustup_bootstrap(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    start = text.index('before-all = """')
    end = text.index('"""', start + len('before-all = """')) + len('"""')
    pyproject.write_text(
        text[:start]
        + 'before-all = "curl -sSf https://sh.rustup.rs | sh -s -- '
        + '-y --default-toolchain 1.97.1 --profile minimal"'
        + text[end:],
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"Linux bootstrap rustup_version|archive- and checksum-bound",
    ):
        build_provenance(tmp_path)


def test_build_provenance_rejects_mutable_musllinux_compiler_packages(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'apk add --no-cache "rust=$musllinux_rust" "cargo=$musllinux_cargo"',
            "apk add --no-cache rust cargo",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be exact apk pins"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_mutable_musllinux_smoke_images(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    workflow = tmp_path / ".github/workflows/wheels.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "python:3.12-alpine@sha256:"
            "6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df",
            "python:3.12-alpine",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="digest-pinned musllinux smoke images"):
        build_provenance(tmp_path)


def test_build_provenance_rejects_an_absent_control_input(tmp_path: Path) -> None:
    _copy_build_inputs(tmp_path)
    (tmp_path / "setup.py").unlink()

    with pytest.raises(ValueError, match=r"cannot hash input setup\.py"):
        build_provenance(tmp_path)


def test_build_provenance_parses_the_exact_hashed_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_build_inputs(tmp_path)
    workflow = tmp_path / ".github/workflows/wheels.yml"
    original = workflow.read_bytes()
    read_file_identity = SUPPLY_CHAIN._read_file_identity

    def replace_after_capture(path: Path) -> tuple[bytes, dict[str, object]]:
        payload, identity = read_file_identity(path)
        if path == workflow:
            workflow.write_text(
                payload.decode("utf-8").replace(
                    "rustup toolchain install 1.97.1",
                    "rustup toolchain install 1.96.0",
                ),
                encoding="utf-8",
            )
        return payload, identity

    monkeypatch.setattr(SUPPLY_CHAIN, "_read_file_identity", replace_after_capture)
    provenance = build_provenance(tmp_path)

    assert provenance["tools"]["rust_toolchain"] == "1.97.1"
    assert provenance["inputs"][".github/workflows/wheels.yml"] == {
        "bytes": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows prevents replacing an open file, which precludes this path-swap attack",
)
def test_build_provenance_rejects_path_replacement_during_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "input.toml"
    moved = tmp_path / "original.toml"
    path.write_bytes(b"original input")
    original_read = os.read
    replaced = False

    def replacing_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        if not replaced:
            path.replace(moved)
            path.write_bytes(b"replacement input")
            replaced = True
        return original_read(descriptor, size)

    monkeypatch.setattr(SUPPLY_CHAIN.os, "read", replacing_read)
    with pytest.raises(ValueError, match="changed while hashing"):
        SUPPLY_CHAIN._read_file_identity(path)


def test_generated_evidence_check_detects_drift(tmp_path: Path) -> None:
    assert generate_evidence(ROOT, tmp_path) == []
    assert generate_evidence(ROOT, tmp_path, check=True) == []
    path = tmp_path / "sbom-pure.cdx.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["version"] = 99
    path.write_text(json.dumps(document), encoding="utf-8")

    assert generate_evidence(ROOT, tmp_path, check=True) == [
        f"supply-chain: generated evidence drift {path}"
    ]


def test_inventory_rejects_an_omitted_production_component(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    inventory_path = tmp_path / "THIRD_PARTY_LICENSES" / "inventory.toml"
    inventory = inventory_path.read_text(encoding="utf-8")
    start = inventory.index('[[component]]\nname = "blake2"')
    end = inventory.index("[[component]]", start + 1)
    inventory_path.write_text(inventory[:start] + inventory[end:], encoding="utf-8")

    assert validate_inventory(tmp_path) == [
        "inventory: NOTICE.pyelk native component block does not match inventory.toml",
        "inventory: unreviewed production component blake2 0.10.6",
    ]


def test_inventory_scans_development_lock_rows_for_java_components(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    lock_path = tmp_path / "Cargo.lock"
    lock = lock_path.read_text(encoding="utf-8")
    lock += (
        "\n[[package]]\n"
        'name = "owlapi"\n'
        'version = "9.9.9"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        'checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
    )
    lock_path.write_text(lock, encoding="utf-8")

    assert validate_inventory(tmp_path) == ["inventory: forbidden Java/JVM component owlapi"]


def test_inventory_cannot_redirect_the_reviewed_lockfile(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    inventory_path = tmp_path / "THIRD_PARTY_LICENSES" / "inventory.toml"
    inventory = inventory_path.read_text(encoding="utf-8")
    inventory_path.write_text(
        inventory.replace('lockfile = "Cargo.lock"', 'lockfile = "../unreviewed/Cargo.lock"'),
        encoding="utf-8",
    )

    assert validate_inventory(tmp_path) == [
        "inventory: lockfile must be exactly Cargo.lock, got '../unreviewed/Cargo.lock'"
    ]


def test_inventory_requires_every_selected_additional_license(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    (tmp_path / "THIRD_PARTY_LICENSES" / "Unicode-3.0.txt").unlink()

    assert validate_inventory(tmp_path) == [
        "inventory: missing additional license file THIRD_PARTY_LICENSES/Unicode-3.0.txt"
    ]


def test_inventory_rejects_unsafe_or_unreferenced_legal_payloads(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    inventory_path = tmp_path / "THIRD_PARTY_LICENSES" / "inventory.toml"
    inventory = inventory_path.read_text(encoding="utf-8")
    inventory_path.write_text(
        inventory.replace(
            'additional_license_file = "THIRD_PARTY_LICENSES/LLVM-exception.txt"',
            'additional_license_file = "../outside.txt"',
        ),
        encoding="utf-8",
    )
    (tmp_path / "THIRD_PARTY_LICENSES" / "unreviewed.txt").write_text(
        "unreviewed",
        encoding="utf-8",
    )

    assert validate_inventory(tmp_path) == [
        "inventory: NOTICE.pyelk native component block does not match inventory.toml",
        "inventory: unreferenced legal payload files ['LLVM-exception.txt', 'unreviewed.txt']",
        "inventory: unsafe additional license path '../outside.txt'",
    ]


def test_inventory_rejects_unresolved_development_lock_edges(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    lock_path = tmp_path / "Cargo.lock"
    lock = lock_path.read_text(encoding="utf-8")
    start = lock.index('[[package]]\nname = "criterion"')
    end = lock.index("[[package]]", start + 1)
    criterion = lock[start:end].replace('"anes"', '"absent-crate"', 1)
    lock_path.write_text(
        lock[:start] + criterion + lock[end:],
        encoding="utf-8",
    )

    assert validate_inventory(tmp_path) == [
        "inventory: unresolved locked dependency 'absent-crate' required by criterion 0.5.1"
    ]


def test_inventory_rejects_notice_component_drift(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    notice_path = tmp_path / "NOTICE.pyelk"
    notice = notice_path.read_text(encoding="utf-8")
    notice_path.write_text(
        notice.replace("- pyo3 0.29.0: Apache-2.0 [native-runtime]\n", ""),
        encoding="utf-8",
    )

    assert validate_inventory(tmp_path) == [
        "inventory: NOTICE.pyelk native component block does not match inventory.toml"
    ]


def test_inventory_loader_does_not_coerce_approval_or_component_types(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    inventory_path = tmp_path / "THIRD_PARTY_LICENSES" / "inventory.toml"
    inventory = inventory_path.read_text(encoding="utf-8")
    inventory_path.write_text(
        inventory.replace("legal_approval = true", 'legal_approval = "true"'),
        encoding="utf-8",
    )

    assert validate_inventory(tmp_path) == [
        "inventory: cannot load THIRD_PARTY_LICENSES/inventory.toml: "
        "inventory legal_approval must be a boolean"
    ]


def test_inventory_rejects_non_spdx_license_expression(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    inventory_path = tmp_path / "THIRD_PARTY_LICENSES" / "inventory.toml"
    inventory = inventory_path.read_text(encoding="utf-8")
    inventory_path.write_text(
        inventory.replace(
            'license = "MIT OR Apache-2.0"\nselected_license = "Apache-2.0"\n'
            'scope = "native-build"\n',
            'license = "MIT/Apache-2.0"\nselected_license = "Apache-2.0"\nscope = "native-build"\n',
            1,
        ),
        encoding="utf-8",
    )

    assert validate_inventory(tmp_path) == [
        "inventory: unreviewed SPDX expression 'MIT/Apache-2.0' for heck"
    ]


def test_non_apache_selection_requires_packaged_terms(tmp_path: Path) -> None:
    _copy_manifests(tmp_path)
    inventory_path = tmp_path / "THIRD_PARTY_LICENSES" / "inventory.toml"
    inventory = inventory_path.read_text(encoding="utf-8")
    inventory_path.write_text(
        inventory.replace(
            'additional_license_file = "THIRD_PARTY_LICENSES/MIT-generic-array.txt"\n',
            "",
        ),
        encoding="utf-8",
    )

    assert validate_inventory(tmp_path) == [
        "inventory: NOTICE.pyelk native component block does not match inventory.toml",
        "inventory: non-Apache selection requires a packaged license file for generic-array 0.14.7",
        "inventory: unreferenced legal payload files ['MIT-generic-array.txt']",
    ]


def test_release_gate_fails_closed_until_legal_approval(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = SUPPLY_CHAIN.load_inventory(ROOT / "THIRD_PARTY_LICENSES" / "inventory.toml")
    monkeypatch.setattr(
        SUPPLY_CHAIN,
        "load_inventory",
        lambda path: replace(inventory, legal_approval=False),
    )
    assert generate_evidence(ROOT, tmp_path) == []
    assert (
        main(
            [
                "--root",
                str(ROOT),
                "--output-dir",
                str(tmp_path),
                "--check",
                "--require-approval",
            ]
        )
        == 1
    )
    assert "requires release-owner or counsel approval" in capsys.readouterr().out
