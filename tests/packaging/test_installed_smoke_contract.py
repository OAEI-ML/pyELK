from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.packaging


def _load_script(name: str) -> ModuleType:
    path = ROOT / "tests" / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_pyelk_{name}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installed_smoke_requires_exact_consumer_core_and_ingestion_paths() -> None:
    smoke = _load_script("installed_smoke")
    args = smoke._parser().parse_args(
        [
            "--expected-backend",
            "rust",
            "--expected-core-backend",
            "native",
            "--expected-ingestion",
            "encoded-native",
        ]
    )

    assert args.expected_backend == "rust"
    assert args.expected_core_backend == "native"
    assert args.expected_ingestion == "encoded-native"
    smoke._validate_expectations(
        backend=args.expected_backend,
        ingestion=args.expected_ingestion,
        force_python=args.force_python,
    )


@pytest.mark.parametrize(
    ("backend", "ingestion", "force_python"),
    [
        ("python", "scalar-wire", True),
        ("python", "encoded-native", True),
        ("rust", "scalar-python", False),
        ("rust", "scalar-wire", True),
    ],
)
def test_installed_smoke_rejects_contradictory_path_expectations(
    backend: str,
    ingestion: str,
    force_python: bool,
) -> None:
    smoke = _load_script("installed_smoke")

    with pytest.raises(ValueError):
        smoke._validate_expectations(
            backend=backend,
            ingestion=ingestion,
            force_python=force_python,
        )


def test_every_packaging_lane_names_the_expected_core_and_ingestion_paths() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    offsets = sorted(
        index
        for index in range(len(workflow))
        if workflow.startswith(("installed_smoke.py", "install_artifact.py"), index)
    )
    assert offsets
    for offset in offsets:
        boundary = workflow.find("\n\n      -", offset)
        invocation = workflow[offset:] if boundary < 0 else workflow[offset:boundary]
        assert "--expected-core-backend" in invocation
        assert "--expected-ingestion" in invocation
    default_cibuildwheel, manylinux_override, musllinux_override = pyproject.split(
        "[[tool.cibuildwheel.overrides]]"
    )
    assert default_cibuildwheel.count("--core-backend native") == 2
    assert "--core-backend python" not in default_cibuildwheel
    assert 'select = "*-manylinux*"' in manylinux_override
    assert manylinux_override.count("--core-backend python") == 2
    assert "--core-backend native" not in manylinux_override
    assert 'select = "*-musllinux*"' in musllinux_override
    assert musllinux_override.count("--core-backend python") == 2
    assert "--core-backend native" not in musllinux_override
    assert "--expected-ingestion encoded-native" in pyproject
    assert "--expected-ingestion scalar-python" in pyproject


def test_released_core_abi3_lanes_require_native_core_and_encoded_ingestion() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")
    abi3 = workflow.split("  abi3-supported-cpython:", maxsplit=1)[1].split(
        "  musllinux-supported-cpython:",
        maxsplit=1,
    )[0]

    assert "--expected-ingestion scalar-wire" not in workflow
    assert abi3.count("--expected-core-backend native") == 2
    assert abi3.count("--expected-core-backend python") == 2
    assert "--platform any" in abi3
    assert "--expected-ingestion encoded-native" in abi3


def test_forced_any_and_musllinux_lanes_require_pure_core() -> None:
    workflow = (ROOT / ".github/workflows/wheels.yml").read_text(encoding="utf-8")
    compiler_free = workflow.split("  compiler-free-installed:", maxsplit=1)[1].split(
        "  native-wheels:",
        maxsplit=1,
    )[0]
    native_wheels = workflow.split("  native-wheels:", maxsplit=1)[1].split(
        "  abi3-supported-cpython:",
        maxsplit=1,
    )[0]
    musllinux = workflow.split("  musllinux-supported-cpython:", maxsplit=1)[1].split(
        "  artifact-consistency:",
        maxsplit=1,
    )[0]

    assert "--platform any" in compiler_free
    assert compiler_free.count("--expected-core-backend python") == 2
    assert native_wheels.count("--core-backend native") == 2
    assert "--core-backend python" not in native_wheels
    assert musllinux.count("--expected-core-backend python") == 2
    assert "--expected-core-backend native" not in musllinux


def test_native_wheel_runs_bounded_wp14_encoded_public_dispatch_contract() -> None:
    command = "python {project}/tests/packaging/run_installed_wp14_contract.py"
    nodes = (
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
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runner = _load_script("run_installed_wp14_contract")
    contract = (ROOT / "tests/backends/test_rust_core.py").read_text(encoding="utf-8")
    provenance = (ROOT / "tools/supply_chain.py").read_text(encoding="utf-8")

    assert metadata.count(command) == 3
    lanes = metadata.split("[[tool.cibuildwheel.overrides]]")
    assert len(lanes) == 3
    for lane in lanes:
        assert lane.count(command) == 1
        assert lane.index(command) < lane.index("run_installed_suite.py --backend rust")
    assert nodes == runner.CONTRACT_NODE_IDS
    runner_source = (ROOT / "tests/packaging/run_installed_wp14_contract.py").read_text(
        encoding="utf-8"
    )
    assert "source checkout imported" in runner_source
    assert "installed WP14 contract attempted network access" in runner_source
    assert '"pyowl-core/structural-columns": 1' in contract
    assert 'diagnostics["ingestion_path"] == "encoded-native"' in contract
    for relative in (
        '"tests/backends/test_rust_core.py"',
        '"tests/packaging/run_installed_wp14_contract.py"',
    ):
        assert relative in provenance
