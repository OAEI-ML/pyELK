#!/usr/bin/env python3
"""Diagnose native root-isolated saturation with property-range-heavy fillers.

The generated ontology has many named roots that point through one object property to one
shared filler, plus a configurable number of ranges for that property.  Each root is
saturated independently by the native session, making repeated auxiliary-context work and
scheduler membership costs visible without loading a private biomedical corpus.

Parsing and Python compilation happen once outside all measurements.  Fresh native sessions
then report transfer/property setup, consistency, first classification, and cached taxonomy
times separately, together with the native fixed-point counters for each stage.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import TypeVar

import pyowl_core as owl

from pyelk.backends.python import PythonBackendSession
from pyelk.indexing.compiler import compile_ontology
from pyelk.reasoning.wire import encode_raw_taxonomy

T = TypeVar("T")
_COUNTERS = (
    "conclusion_candidates",
    "conclusions_inserted",
    "contexts_created",
    "duplicate_candidates",
    "product_candidates",
    "rule_dispatches",
)


@dataclass(frozen=True, slots=True)
class Summary:
    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float
    maximum_seconds: float


def _source(
    root_count: int,
    range_count: int,
    *,
    range_hierarchy: bool = False,
    filler_chain: bool = False,
) -> bytes:
    declarations = [
        "Declaration(ObjectProperty(:p))",
        "Declaration(Class(:Filler))",
        *(f"Declaration(Class(:Root{index}))" for index in range(root_count)),
        *(f"Declaration(Class(:Range{index}))" for index in range(range_count)),
    ]
    axioms = [
        *(
            "SubClassOf(:Root{index} ObjectSomeValuesFrom(:p :{filler}))".format(
                index=index,
                filler=(
                    f"Root{index + 1}" if filler_chain and index + 1 < root_count else "Filler"
                ),
            )
            for index in range(root_count)
        ),
        *(f"ObjectPropertyRange(:p :Range{index})" for index in range(range_count)),
        *(
            f"SubClassOf(:Range{index} :Range{index + 1})"
            for index in range(range_count - 1)
            if range_hierarchy
        ),
    ]
    body = " ".join((*declarations, *axioms))
    return f"Prefix(:=<urn:pyelk:range-isolation#>) Ontology({body})".encode()


def _native_candidate(path: Path | None) -> Path:
    if path is not None:
        resolved = path.expanduser().resolve()
        if resolved.is_file():
            return resolved
        raise ValueError(f"native library is not a file: {path}")
    root = Path(__file__).parents[1]
    candidates = (
        root / "target" / "release" / "lib_native.dylib",
        root / "target" / "release" / "lib_native.so",
        root / "target" / "debug" / "lib_native.dylib",
        root / "target" / "debug" / "lib_native.so",
    )
    candidate = next((item for item in candidates if item.is_file()), None)
    if candidate is None:
        raise RuntimeError("native extension is not installed and no workspace library was found")
    return candidate


@contextmanager
def _native_module(path: Path | None) -> Iterator[ModuleType]:
    candidate = _native_candidate(path)
    previous = sys.modules.get("pyelk._native")
    with tempfile.TemporaryDirectory(prefix="pyelk-range-isolation-") as temporary:
        suffix = ".pyd" if sys.platform == "win32" else ".so"
        destination = Path(temporary) / f"_native{suffix}"
        shutil.copy2(candidate, destination)
        spec = importlib.util.spec_from_file_location("pyelk._native", destination)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot import native library: {candidate}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["pyelk._native"] = module
        try:
            spec.loader.exec_module(module)
            yield module
        finally:
            if previous is None:
                sys.modules.pop("pyelk._native", None)
            else:
                sys.modules["pyelk._native"] = previous


def _measure(operation: Callable[[], T]) -> tuple[T, float]:
    gc.collect()
    started = time.perf_counter()
    value = operation()
    return value, time.perf_counter() - started


def _summary(values: list[float]) -> Summary:
    median = statistics.median(values)
    return Summary(
        repeats=len(values),
        median_seconds=median,
        median_absolute_deviation_seconds=statistics.median(
            abs(value - median) for value in values
        ),
        minimum_seconds=min(values),
        maximum_seconds=max(values),
    )


def _diagnostics(value: object) -> dict[str, int | float | str | bool]:
    if not isinstance(value, Mapping):
        raise TypeError("native diagnostics must be a mapping")
    result: dict[str, int | float | str | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, (int, float, str, bool)):
            raise TypeError("native diagnostics must contain string-to-scalar entries")
        result[key] = item
    return result


def _counter_delta(
    before: Mapping[str, int | float | str | bool],
    after: Mapping[str, int | float | str | bool],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in _COUNTERS:
        first, second = before[key], after[key]
        if isinstance(first, bool) or not isinstance(first, int):
            raise TypeError(f"native counter {key!r} is not an integer")
        if isinstance(second, bool) or not isinstance(second, int):
            raise TypeError(f"native counter {key!r} is not an integer")
        result[key] = second - first
    return result


def run(
    *,
    root_count: int,
    range_count: int,
    workers: int,
    repeats: int,
    native_path: Path | None,
    verify_python: bool,
    range_hierarchy: bool = False,
    filler_chain: bool = False,
) -> dict[str, object]:
    if root_count < 1 or range_count < 0:
        raise ValueError("roots must be positive and ranges nonnegative")
    if workers < 1 or repeats < 1:
        raise ValueError("workers and repeats must be positive")
    source = _source(
        root_count,
        range_count,
        range_hierarchy=range_hierarchy,
        filler_chain=filler_chain,
    )
    setup_started = time.perf_counter()
    snapshot = owl.load_snapshot(
        source,
        options=owl.LoadOptions(
            format=owl.DocumentFormat.FUNCTIONAL,
            imports=owl.ImportPolicy.IGNORE,
            backend=owl.BackendPreference.PYTHON,
            offline=True,
        ),
    )
    compiled = compile_ontology(snapshot)
    encoded = compiled.encode()
    python_setup_seconds = time.perf_counter() - setup_started

    timings: dict[str, list[float]] = {
        "native_create": [],
        "consistency": [],
        "classification": [],
        "cached_taxonomy": [],
    }
    expected_digest: str | None = None
    expected_diagnostics: dict[str, int | float | str | bool] | None = None
    consistency_delta: dict[str, int] | None = None
    classification_delta: dict[str, int] | None = None
    final_payload = b""
    with _native_module(native_path) as native:
        for _ in range(repeats):
            session, elapsed = _measure(lambda: native.create_session(encoded, workers))
            timings["native_create"].append(elapsed)
            try:
                initial = _diagnostics(session.diagnostics())
                inconsistent, elapsed = _measure(session.is_inconsistent)
                if inconsistent is not False:
                    raise AssertionError("generated range fixture unexpectedly became inconsistent")
                timings["consistency"].append(elapsed)
                after_consistency = _diagnostics(session.diagnostics())
                payload, elapsed = _measure(session.class_taxonomy)
                if not isinstance(payload, bytes):
                    raise TypeError("native class taxonomy must be packed bytes")
                timings["classification"].append(elapsed)
                after_classification = _diagnostics(session.diagnostics())
                cached, elapsed = _measure(session.class_taxonomy)
                if cached != payload:
                    raise AssertionError("cached native taxonomy bytes changed")
                timings["cached_taxonomy"].append(elapsed)
            finally:
                session.close()
            digest = hashlib.sha256(payload).hexdigest()
            if expected_digest is not None and digest != expected_digest:
                raise AssertionError("native taxonomy changed between fresh sessions")
            if expected_diagnostics is not None and after_classification != expected_diagnostics:
                raise AssertionError("native diagnostics changed between fresh sessions")
            expected_digest = digest
            expected_diagnostics = after_classification
            consistency_delta = _counter_delta(initial, after_consistency)
            classification_delta = _counter_delta(after_consistency, after_classification)
            final_payload = payload

    python_parity: bool | None = None
    if verify_python:
        python = PythonBackendSession(
            compiled,
            requested_workers=1,
            native_available=False,
            fallback_reason="range-isolation benchmark",
        )
        try:
            python_parity = encode_raw_taxonomy(python.class_taxonomy()) == final_payload
        finally:
            python.close()
        if not python_parity:
            raise AssertionError("native taxonomy differs from the pure-Python backend")

    if expected_diagnostics is None or consistency_delta is None or classification_delta is None:
        raise AssertionError("benchmark produced no sample")
    retained_contexts = expected_diagnostics["context_count"]
    contexts_created = expected_diagnostics["contexts_created"]
    if isinstance(retained_contexts, bool) or not isinstance(retained_contexts, int):
        raise TypeError("context_count diagnostic is not an integer")
    if isinstance(contexts_created, bool) or not isinstance(contexts_created, int):
        raise TypeError("contexts_created diagnostic is not an integer")
    return {
        "schema": "pyelk.range-isolation-benchmark/1",
        "fixture": {
            "root_count": root_count,
            "range_count": range_count,
            "range_hierarchy": range_hierarchy,
            "filler_chain": filler_chain,
            "compiled_entity_count": len(compiled.entities),
            "compiled_expression_count": len(compiled.expressions),
            "compiled_property_range_count": len(compiled.property_ranges),
            "encoded_bytes": len(encoded),
        },
        "protocol": {"workers": workers, "repeats": repeats},
        "python_setup_seconds": python_setup_seconds,
        "timings": {name: asdict(_summary(values)) for name, values in timings.items()},
        "diagnostics": expected_diagnostics,
        "consistency_counter_delta": consistency_delta,
        "classification_counter_delta": classification_delta,
        "context_creation_amplification": (
            contexts_created / retained_contexts if retained_contexts else None
        ),
        "taxonomy_sha256": expected_digest,
        "python_parity": python_parity,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", type=int, default=128)
    parser.add_argument("--ranges", type=int, default=128)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--native-path", type=Path)
    parser.add_argument("--verify-python", action="store_true")
    parser.add_argument(
        "--range-hierarchy",
        action="store_true",
        help="connect every range in a subclass chain to expose redundant range work",
    )
    parser.add_argument(
        "--filler-chain",
        action="store_true",
        help="point each root at the next root instead of one shared filler",
    )
    arguments = parser.parse_args(argv)
    report = run(
        root_count=arguments.roots,
        range_count=arguments.ranges,
        workers=arguments.workers,
        repeats=arguments.repeats,
        native_path=arguments.native_path,
        verify_python=arguments.verify_python,
        range_hierarchy=arguments.range_hierarchy,
        filler_chain=arguments.filler_chain,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
