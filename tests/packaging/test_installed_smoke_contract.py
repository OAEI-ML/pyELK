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
    assert pyproject.count("--core-backend python") == 2
    assert "--expected-ingestion scalar-wire" in pyproject
    assert "--expected-ingestion scalar-python" in pyproject
