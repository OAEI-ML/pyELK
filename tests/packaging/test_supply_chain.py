from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from tools.supply_chain import (
    build_cyclonedx,
    build_dependency_inventory,
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


def test_reviewed_inventory_matches_the_production_lock_closure() -> None:
    assert validate_inventory(ROOT) == []
    all_locked = load_locked_packages(ROOT / "Cargo.lock")
    production = native_locked_packages(ROOT, all_locked)
    inventory = build_dependency_inventory(ROOT)

    assert len(production) == len(inventory["native_components"]) == 31
    assert "criterion" not in {package.name for package in production}
    assert inventory["python_runtime_dependencies"] == [
        {"name": "pyowl-core", "requirement": ">=0.1,<0.2"}
    ]
    assert inventory["java_components"] == []
    assert inventory["legal_approval"] is False
    assert inventory["release_blockers"]


def test_pure_and_native_sboms_are_variant_exact_and_deterministic() -> None:
    pure = build_cyclonedx(ROOT, "pure")
    native = build_cyclonedx(ROOT, "native")

    assert pure == build_cyclonedx(ROOT, "pure")
    assert native == build_cyclonedx(ROOT, "native")
    assert [component["name"] for component in pure["components"]] == ["pyowl-core"]
    assert len(native["components"]) == 32
    assert native["metadata"]["component"]["version"] == "0.1.0.dev0"
    assert native["dependencies"][0]["dependsOn"] == [
        "pkg:cargo/blake2@0.10.6",
        "pkg:cargo/pyo3@0.29.0",
        "pkg:cargo/rayon@1.12.0",
        "pkg:cargo/sha2@0.10.9",
        "pkg:pypi/pyowl-core?requirement=%3E%3D0.1%2C%3C0.2",
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
        "sbom: dependency row pkg:pypi/pyelk-reasoner@0.1.0.dev0?variant=native is not canonical",
        "sbom: dependency row pkg:pypi/pyelk-reasoner@0.1.0.dev0?variant=native "
        "names unknown components ['pkg:cargo/unbound@9.9.9']",
    ]


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
        inventory.replace("legal_approval = false", 'legal_approval = "false"'),
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
) -> None:
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
