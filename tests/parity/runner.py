#!/usr/bin/env python3
"""Run the frozen ELK corpus through one public pyELK backend configuration.

The runner deliberately lives with the retained fixtures.  It can be invoked from a source
checkout or by an interpreter containing an installed wheel; adding the repository root to
``sys.path`` makes test helpers importable without making the ``src`` package importable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pyowl_core as owl  # noqa: E402

from pyelk import EntityNode, Reasoner, ReasonerConfig, Taxonomy  # noqa: E402
from pyelk.reasoning.contracts import CompletenessIssue  # noqa: E402
from tests.unit.reasoning.test_entailment import _parse_axiom  # noqa: E402
from tests.unit.reasoning.test_queries import _parse_expression  # noqa: E402

DATA = ROOT / "tests" / "data" / "elk-v0.6.0"
EXPECTED = DATA / "expected"
UPSTREAM = DATA / "upstream"
OPTIONS = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)

BackendName = Literal["auto", "python", "rust"]


@dataclass(frozen=True, slots=True)
class FixtureFailure:
    """One stable case identifier and its exact assertion/error text."""

    case_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class FixtureReport:
    """Machine-readable result for one backend/worker configuration."""

    schema: str
    backend: str
    effective_backend: str | None
    workers: int
    expected_cases: int
    evaluated_cases: int
    passed_cases: int
    elapsed_seconds: float
    failures: tuple[FixtureFailure, ...]

    @property
    def passed(self) -> bool:
        return not self.failures and self.evaluated_cases == self.expected_cases


def _payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected an object in {path}")
    return value


def _snapshot(path: Path) -> owl.OntologySnapshot:
    return owl.load_snapshot(path, options=OPTIONS)


def _node(node: EntityNode[owl.Entity]) -> list[str]:
    return sorted((member.iri.value for member in node.members), key=str.encode)


def _node_rows(nodes: tuple[EntityNode[owl.Entity], ...]) -> list[list[str]]:
    return sorted((_node(node) for node in nodes), key=lambda row: tuple(map(str.encode, row)))


def _taxonomy(value: Taxonomy[owl.Entity]) -> dict[str, object]:
    nodes = sorted(
        (_node(node) for node in value.nodes),
        key=lambda node: tuple(map(str.encode, node)),
    )
    index = {tuple(node): position for position, node in enumerate(nodes)}
    return {
        "bottom": index[tuple(_node(value.bottom))],
        "direct_edges": sorted(
            [index[tuple(_node(sub))], index[tuple(_node(sup))]] for sub, sup in value.direct_edges
        ),
        "nodes": nodes,
        "top": index[tuple(_node(value.top))],
    }


def _issues(values: tuple[CompletenessIssue, ...]) -> list[dict[str, object]]:
    return [
        {
            "constructors": list(issue.constructors),
            "features": list(issue.features),
            "polarities": list(issue.polarities),
            "task": issue.task.value,
        }
        for issue in values
    ]


def _query_snapshot(name: str) -> owl.OntologySnapshot:
    return _snapshot(UPSTREAM / "query" / "class" / f"{name}.owl")


def _assert_result_metadata(actual: Any, expected: dict[str, Any]) -> None:
    assert actual.complete is expected["complete"]
    assert _issues(actual.reasons) == expected["issues"]


def _run_class_case(name: str, config: ReasonerConfig) -> str:
    case_id = f"classification/{name}"
    expected = _payload(EXPECTED / "classification" / f"{name}.json")["result"]
    with Reasoner(_snapshot(UPSTREAM / f"{case_id}.owl"), config) as reasoner:
        actual = reasoner.classify()
        assert _taxonomy(actual.value) == expected["value"]
        _assert_result_metadata(actual, expected)
        assert reasoner.is_consistent().value is (
            expected["value"]["top"] != expected["value"]["bottom"]
        )
        return reasoner.backend.name


def _run_property_case(name: str, config: ReasonerConfig) -> str:
    case_id = f"classification/object_property/{name}"
    expected = _payload(EXPECTED / f"{case_id}.json")["result"]
    with Reasoner(_snapshot(UPSTREAM / f"{case_id}.owl"), config) as reasoner:
        actual = reasoner.classify_object_properties()
        assert _taxonomy(actual.value) == expected["value"]
        _assert_result_metadata(actual, expected)
        return reasoner.backend.name


def _run_realization_case(name: str, config: ReasonerConfig) -> str:
    case_id = f"realization/{name}"
    expected = _payload(EXPECTED / f"{case_id}.json")["result"]
    with Reasoner(_snapshot(UPSTREAM / f"{case_id}.owl"), config) as reasoner:
        actual = reasoner.realize()
        value = _taxonomy(actual.value.class_taxonomy)
        instance_nodes = sorted(
            (_node(node) for node in actual.value.instances),
            key=lambda node: tuple(map(str.encode, node)),
        )
        instance_index = {tuple(node): position for position, node in enumerate(instance_nodes)}
        class_index = {
            tuple(node): position
            for position, node in enumerate(value["nodes"])  # type: ignore[arg-type]
        }
        value.update(
            {
                "direct_types": sorted(
                    [
                        instance_index[tuple(_node(instance))],
                        class_index[tuple(_node(class_node))],
                    ]
                    for instance, class_node in actual.value.direct_types
                ),
                "instance_nodes": instance_nodes,
            }
        )
        assert value == expected["value"]
        _assert_result_metadata(actual, expected)
        return reasoner.backend.name


def _run_class_query_case(name: str, config: ReasonerConfig) -> str:
    case_id = f"query/class/{name}"
    expected_result = _payload(EXPECTED / f"{case_id}.json")["result"]
    expected_rows = expected_result["value"]["queries"]
    with Reasoner(_query_snapshot(name), config) as reasoner:
        for expected in expected_rows:
            expression = _parse_expression(name, expected["expression"])
            operations = {
                "satisfiable": reasoner.is_satisfiable(expression),
                "equivalent_classes": reasoner.equivalent_classes(expression),
                "direct_subclasses": reasoner.subclasses(expression, direct=True),
                "direct_superclasses": reasoner.superclasses(expression, direct=True),
                "direct_instances": reasoner.instances(expression, direct=True),
            }
            equivalent = operations["equivalent_classes"]
            equivalent_value = [] if not equivalent.value else _node(equivalent.value[0])
            values = {
                "satisfiable": operations["satisfiable"].value,
                "equivalent_classes": equivalent_value,
                "direct_subclasses": _node_rows(operations["direct_subclasses"].value),
                "direct_superclasses": _node_rows(operations["direct_superclasses"].value),
                "direct_instances": _node_rows(operations["direct_instances"].value),
            }
            for operation, actual in operations.items():
                assert values[operation] == expected[operation]["value"]
                assert actual.complete is expected[operation]["complete"]
                assert _issues(actual.reasons) == expected_result["issues"]
        return reasoner.backend.name


def _run_entailment_case(name: str, config: ReasonerConfig) -> str:
    case_id = f"query/entailment/{name}"
    expected_result = _payload(EXPECTED / f"{case_id}.json")["result"]
    with Reasoner(_snapshot(UPSTREAM / f"{case_id}.owl"), config) as reasoner:
        for expected in expected_result["value"]["queries"]:
            actual = reasoner.is_entailed(_parse_axiom(expected["axiom"]))
            assert actual.value is expected["entailed"]
            assert actual.complete is expected["complete"]
            assert _issues(actual.reasons) == expected["issues"]
        return reasoner.backend.name


def case_ids() -> tuple[str, ...]:
    """Return all 124 ontology case identifiers in canonical byte order."""

    values = [
        *(f"classification/{path.stem}" for path in (EXPECTED / "classification").glob("*.json")),
        *(
            f"classification/object_property/{path.stem}"
            for path in (EXPECTED / "classification" / "object_property").glob("*.json")
        ),
        *(f"realization/{path.stem}" for path in (EXPECTED / "realization").glob("*.json")),
        *(f"query/class/{path.stem}" for path in (EXPECTED / "query" / "class").glob("*.json")),
        *(
            f"query/entailment/{path.stem}"
            for path in (EXPECTED / "query" / "entailment").glob("*.json")
        ),
    ]
    return tuple(sorted(values, key=str.encode))


def _run_case(case_id: str, config: ReasonerConfig) -> str:
    family, name = case_id.rsplit("/", 1)
    if family == "classification":
        return _run_class_case(name, config)
    if family == "classification/object_property":
        return _run_property_case(name, config)
    if family == "realization":
        return _run_realization_case(name, config)
    if family == "query/class":
        return _run_class_query_case(name, config)
    if family == "query/entailment":
        return _run_entailment_case(name, config)
    raise ValueError(f"unknown frozen case {case_id!r}")


def run_frozen_suite(
    *,
    backend: BackendName,
    workers: int,
    selected_cases: tuple[str, ...] | None = None,
) -> FixtureReport:
    """Compare selected public results with their immutable Java JSON expectations."""

    available = case_ids()
    cases = available if selected_cases is None else selected_cases
    unknown = sorted(set(cases) - set(available), key=str.encode)
    if unknown:
        raise ValueError(f"unknown frozen case(s): {', '.join(unknown)}")
    config = ReasonerConfig(backend=backend, workers=workers)
    failures: list[FixtureFailure] = []
    effective_backends: set[str] = set()
    started = time.perf_counter()
    for case_id in cases:
        try:
            effective_backends.add(_run_case(case_id, config))
        except Exception as error:  # runner must classify every diff before returning
            detail = str(error).strip()
            failures.append(
                FixtureFailure(
                    case_id,
                    type(error).__name__ if not detail else f"{type(error).__name__}: {detail}",
                )
            )
    effective = next(iter(effective_backends)) if len(effective_backends) == 1 else None
    return FixtureReport(
        schema="pyelk.frozen-run/1",
        backend=backend,
        effective_backend=effective,
        workers=workers,
        expected_cases=len(cases),
        evaluated_cases=len(cases),
        passed_cases=len(cases) - len(failures),
        elapsed_seconds=time.perf_counter() - started,
        failures=tuple(failures),
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        action="append",
        choices=("auto", "python", "rust"),
        dest="backends",
        help="backend to exercise; repeat for multiple configurations (default: python)",
    )
    parser.add_argument(
        "--workers",
        action="append",
        type=int,
        dest="worker_counts",
        help="worker count to exercise; repeat for multiple configurations (default: 1)",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="exact case identifier to run; repeat to select multiple cases",
    )
    parser.add_argument(
        "--native-path",
        type=Path,
        help="load a workspace native library before running (installed wheels need no path)",
    )
    return parser.parse_args()


def _load_workspace_native(path: Path, directory: Path) -> ModuleType:
    if not path.is_file():
        raise FileNotFoundError(path)
    destination = directory / "_native.so"
    shutil.copy2(path, destination)
    spec = importlib.util.spec_from_file_location("pyelk._native", destination)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot create an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    arguments = _arguments()
    backends = arguments.backends or ["python"]
    worker_counts = arguments.worker_counts or [1]
    previous_native = sys.modules.get("pyelk._native")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        if arguments.native_path is not None:
            temporary = tempfile.TemporaryDirectory(prefix="pyelk-parity-native-")
            _load_workspace_native(arguments.native_path, Path(temporary.name))
        reports = [
            run_frozen_suite(
                backend=backend,
                workers=workers,
                selected_cases=None if arguments.cases is None else tuple(arguments.cases),
            )
            for backend in backends
            for workers in worker_counts
        ]
    finally:
        if arguments.native_path is not None:
            if previous_native is None:
                sys.modules.pop("pyelk._native", None)
            else:
                sys.modules["pyelk._native"] = previous_native
        if temporary is not None:
            temporary.cleanup()
    payload = {
        "schema": "pyelk.frozen-run-set/1",
        "passed": all(report.passed for report in reports),
        "reports": [asdict(report) for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
