#!/usr/bin/env python3
"""Measure standalone and shared-view public reasoning with exact result equality."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import platform
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

import pyowl_core as owl

from pyelk import Reasoner, ReasonerConfig


@dataclass(frozen=True, slots=True)
class Measurements:
    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float


def _load_workspace_native(path: Path, directory: Path) -> ModuleType:
    destination = directory / "_native.so"
    shutil.copy2(path, destination)
    spec = importlib.util.spec_from_file_location("pyelk._native", destination)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot create an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    return module


def _source(class_count: int) -> bytes:
    if class_count < 2:
        raise ValueError("class_count must be at least two")
    declarations = " ".join(f"Declaration(Class(:C{index}))" for index in range(class_count))
    hierarchy = " ".join(f"SubClassOf(:C{index} :C{index + 1})" for index in range(class_count - 1))
    return f"Prefix(:=<urn:pyelk:bench#>) Ontology({declarations} {hierarchy})".encode()


def _summary(values: list[float]) -> Measurements:
    median = statistics.median(values)
    return Measurements(
        repeats=len(values),
        median_seconds=median,
        median_absolute_deviation_seconds=statistics.median(
            abs(value - median) for value in values
        ),
        minimum_seconds=min(values),
    )


def _taxonomy_digest(reasoner: Reasoner) -> str:
    result = reasoner.classify()
    value = result.value
    rows = sorted(
        (tuple(member.iri.value for member in node.members) for node in value.nodes),
        key=lambda row: tuple(map(str.encode, row)),
    )
    edges = sorted(
        (
            tuple(member.iri.value for member in sub.members),
            tuple(member.iri.value for member in sup.members),
        )
        for sub, sup in value.direct_edges
    )
    canonical = json.dumps(
        {
            "complete": result.complete,
            "edges": edges,
            "nodes": rows,
            "reasons": [
                {
                    "constructors": issue.constructors,
                    "features": issue.features,
                    "polarities": issue.polarities,
                    "task": issue.task.value,
                }
                for issue in result.reasons
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def run(
    *,
    class_count: int,
    repeats: int,
    warmups: int,
    backend: str,
    workers: int,
) -> dict[str, object]:
    if repeats < 1 or warmups < 0:
        raise ValueError("repeats must be positive and warmups nonnegative")
    source = _source(class_count)
    options = owl.LoadOptions(
        format=owl.DocumentFormat.FUNCTIONAL,
        imports=owl.ImportPolicy.IGNORE,
        backend=owl.BackendPreference.PYTHON,
    )
    config = ReasonerConfig(backend=backend, workers=workers)  # type: ignore[arg-type]
    load_started = time.perf_counter()
    snapshot = owl.load_snapshot(source, options=options)
    load_seconds = time.perf_counter() - load_started

    def standalone() -> tuple[str, str]:
        with Reasoner(source, config, load_options=options) as reasoner:
            return reasoner.backend.name, _taxonomy_digest(reasoner)

    def shared() -> tuple[str, str]:
        with Reasoner(snapshot, config) as reasoner:
            if reasoner.ontology is not snapshot:
                raise AssertionError("shared benchmark lost snapshot identity")
            return reasoner.backend.name, _taxonomy_digest(reasoner)

    for _ in range(warmups):
        if standalone() != shared():
            raise AssertionError("standalone/shared result mismatch during warm-up")

    standalone_elapsed: list[float] = []
    shared_elapsed: list[float] = []
    final: tuple[str, str] | None = None
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        standalone_value = standalone()
        standalone_elapsed.append(time.perf_counter() - started)
        gc.collect()
        started = time.perf_counter()
        shared_value = shared()
        shared_elapsed.append(time.perf_counter() - started)
        if standalone_value != shared_value:
            raise AssertionError("standalone/shared result mismatch")
        final = shared_value
    if final is None:  # pragma: no cover - repeats validated above
        raise AssertionError("benchmark produced no result")
    return {
        "schema": "pyelk.end-to-end-benchmark/1",
        "backend": final[0],
        "environment": {
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "pyowl_core": owl.__version__,
            "python": platform.python_version(),
        },
        "fixture": {
            "axiom_count": class_count * 2 - 1,
            "class_count": class_count,
            "source_sha256": hashlib.sha256(source).hexdigest(),
        },
        "load_seconds": load_seconds,
        "result_sha256": final[1],
        "shared": asdict(_summary(shared_elapsed)),
        "standalone": asdict(_summary(standalone_elapsed)),
        "workers": workers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classes", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--backend", choices=("auto", "python", "rust"), default="python")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--native-path", type=Path)
    arguments = parser.parse_args()
    previous_native = sys.modules.get("pyelk._native")
    with tempfile.TemporaryDirectory(prefix="pyelk-end-to-end-native-") as temporary:
        try:
            if arguments.native_path is not None:
                _load_workspace_native(arguments.native_path, Path(temporary))
            payload = run(
                class_count=arguments.classes,
                repeats=arguments.repeats,
                warmups=arguments.warmups,
                backend=arguments.backend,
                workers=arguments.workers,
            )
        finally:
            if arguments.native_path is not None:
                if previous_native is None:
                    sys.modules.pop("pyelk._native", None)
                else:
                    sys.modules["pyelk._native"] = previous_native
    print(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
