"""Measure native transfer, core, packed-result boundary, and worker scaling.

The benchmark starts from one already compiled generated hierarchy so parser/compiler time is
not charged to either backend.  It verifies exact raw taxonomy equality before reporting
timings.  Cached native calls measure the packed-result boundary independently of saturation;
fresh sessions measure transfer plus classification.  Results are JSON evidence suitable for
the machine-specific performance record required by the native specification.

Run from the repository root after building the extension::

    PYTHONPATH=src python benchmarks/bench_native_boundary.py

Use ``--classes 250 --repeats 1 --warmups 0`` for a quick smoke run.  ``--enforce`` applies
the initial 5x throughput and 5% boundary targets; ordinary cross-machine runs only report.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import importlib.util
import json
import math
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import TypeVar

from bench_saturation import _compiled_deep_hierarchy

from pyelk.backends.python import PythonBackendSession
from pyelk.indexing.ir import CompiledOntology
from pyelk.reasoning.wire import decode_raw_taxonomy

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Measurements:
    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float
    maximum_seconds: float


def _summarize(values: list[float]) -> Measurements:
    median = statistics.median(values)
    return Measurements(
        repeats=len(values),
        median_seconds=median,
        median_absolute_deviation_seconds=statistics.median(
            abs(value - median) for value in values
        ),
        minimum_seconds=min(values),
        maximum_seconds=max(values),
    )


def _measure(operation: Callable[[], T], *, repeats: int) -> tuple[Measurements, T]:
    values: list[float] = []
    result: T | None = None
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        result = operation()
        values.append(time.perf_counter() - started)
    if result is None:  # pragma: no cover - validated by run
        raise AssertionError("benchmark produced no result")
    return _summarize(values), result


def _load_native(path: Path | None) -> ModuleType:
    if path is None:
        try:
            return importlib.import_module("pyelk._native")
        except ImportError:
            root = Path(__file__).parents[1]
            candidates = (
                root / "target" / "release" / "lib_native.dylib",
                root / "target" / "release" / "lib_native.so",
                root / "target" / "debug" / "lib_native.dylib",
                root / "target" / "debug" / "lib_native.so",
            )
            path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None or not path.is_file():
        raise RuntimeError("pyelk._native is not installed and no workspace library was found")
    temporary = Path(tempfile.mkdtemp(prefix="pyelk-native-benchmark-")) / "_native.so"
    shutil.copy2(path, temporary)
    spec = importlib.util.spec_from_file_location("pyelk._native", temporary)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import native library {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    return module


def _pure_taxonomy(compiled: CompiledOntology) -> object:
    session = PythonBackendSession(
        compiled,
        requested_workers=1,
        native_available=False,
        fallback_reason="native benchmark",
    )
    try:
        return session.class_taxonomy()
    finally:
        session.close()


def _native_taxonomy(native: ModuleType, encoded: bytes, workers: int) -> object:
    session = native.create_session(encoded, workers)
    try:
        return decode_raw_taxonomy(session.class_taxonomy())
    finally:
        session.close()


def run(
    *,
    class_count: int,
    repeats: int,
    warmups: int,
    workers: int,
    native_path: Path | None,
    enforce: bool,
) -> dict[str, object]:
    if class_count < 2:
        raise ValueError("class_count must be at least two")
    if repeats < 1 or warmups < 0 or workers < 1:
        raise ValueError("repeats/workers must be positive and warmups nonnegative")
    native = _load_native(native_path)
    compiled = _compiled_deep_hierarchy(class_count)
    encoded = compiled.encode()

    expected = _pure_taxonomy(compiled)
    actual = _native_taxonomy(native, encoded, 1)
    if actual != expected:
        raise AssertionError("native and pure taxonomy results differ")
    for _ in range(warmups):
        _pure_taxonomy(compiled)
        _native_taxonomy(native, encoded, workers)

    pure, _ = _measure(lambda: _pure_taxonomy(compiled), repeats=repeats)
    native_one, _ = _measure(lambda: _native_taxonomy(native, encoded, 1), repeats=repeats)
    native_many, _ = _measure(lambda: _native_taxonomy(native, encoded, workers), repeats=repeats)

    create, session = _measure(lambda: native.create_session(encoded, workers), repeats=repeats)
    try:
        session.class_taxonomy()
        cached_samples = max(25, repeats * 10)
        cached, payload = _measure(session.class_taxonomy, repeats=cached_samples)
        if decode_raw_taxonomy(payload) != expected:
            raise AssertionError("cached native result differs from the pure taxonomy")
    finally:
        session.close()

    speedup = pure.median_seconds / native_many.median_seconds
    scaling = native_one.median_seconds / native_many.median_seconds
    boundary_fraction = cached.median_seconds / native_many.median_seconds
    if enforce and speedup < 5.0:
        raise AssertionError(f"native speedup {speedup:.2f}x is below the 5x target")
    if enforce and boundary_fraction >= 0.05:
        raise AssertionError(f"native boundary fraction {boundary_fraction:.2%} is not below 5%")

    medians = (pure.median_seconds, native_one.median_seconds, native_many.median_seconds)
    return {
        "benchmark": "native-boundary-and-scaling",
        "class_count": class_count,
        "compiled_bytes": len(encoded),
        "cpu_count": os.cpu_count(),
        "geometric_mean_seconds": math.prod(medians) ** (1 / len(medians)),
        "native": {
            "abi": native.abi_version(),
            "boundary_cached": asdict(cached),
            "boundary_fraction_of_fresh_parallel": boundary_fraction,
            "create_session": asdict(create),
            "implementation": native.implementation_version(),
            "ir": native.ir_version(),
            "workers_1": asdict(native_one),
            "workers_parallel": asdict(native_many),
        },
        "native_speedup_over_pure": speedup,
        "platform": platform.platform(),
        "pure": asdict(pure),
        "python": platform.python_version(),
        "worker_scaling": scaling,
        "workers": workers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classes", type=int, default=2_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--workers", type=int, default=max(2, os.cpu_count() or 2))
    parser.add_argument("--native-path", type=Path)
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            run(
                class_count=arguments.classes,
                repeats=arguments.repeats,
                warmups=arguments.warmups,
                workers=arguments.workers,
                native_path=arguments.native_path,
                enforce=arguments.enforce,
            ),
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
