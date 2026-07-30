"""Benchmark sparse and dense taxonomy quotient/transitive reduction.

The sparse default is a 100,000-node hierarchy chain and verifies the non-recursive linear
shape required by WP08.  The dense default is the transitively closed total order on 1,000
nodes and exercises compact bitset reduction.  Results are JSON evidence, not portable wall-
clock pass/fail thresholds.

Run from the repository root::

    PYTHONPATH=src python benchmarks/bench_taxonomy.py

Use ``--sparse-nodes 10000 --dense-nodes 500 --repeats 1`` for a quick smoke run.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from pyelk.reasoning.reduction import ReducedGraph, quotient_and_reduce


@dataclass(frozen=True, slots=True)
class Measurements:
    repeats: int
    median_seconds: float
    median_absolute_deviation_seconds: float
    minimum_seconds: float
    maximum_seconds: float


def _measure(
    build: Callable[[], ReducedGraph],
    *,
    repeats: int,
) -> tuple[Measurements, ReducedGraph]:
    elapsed: list[float] = []
    result: ReducedGraph | None = None
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        result = build()
        elapsed.append(time.perf_counter() - started)
    if result is None:  # pragma: no cover - repeats validated by run
        raise AssertionError("benchmark produced no result")
    median = statistics.median(elapsed)
    return (
        Measurements(
            repeats=repeats,
            median_seconds=median,
            median_absolute_deviation_seconds=statistics.median(
                abs(value - median) for value in elapsed
            ),
            minimum_seconds=min(elapsed),
            maximum_seconds=max(elapsed),
        ),
        result,
    )


def _assert_total_order(result: ReducedGraph, size: int) -> None:
    if len(result.nodes) != size:
        raise AssertionError(f"expected {size} quotient nodes, got {len(result.nodes)}")
    expected_edges = tuple((node, node + 1) for node in range(size - 1))
    if result.direct_edges != expected_edges:
        raise AssertionError("total-order reduction did not produce its unique chain cover")


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def run(*, sparse_nodes: int, dense_nodes: int, repeats: int) -> dict[str, object]:
    if sparse_nodes < 2 or dense_nodes < 2:
        raise ValueError("sparse_nodes and dense_nodes must be at least two")
    if repeats < 1:
        raise ValueError("repeats must be positive")

    sparse_measurements, sparse_result = _measure(
        lambda: quotient_and_reduce(
            range(sparse_nodes),
            ((node, node + 1) for node in range(sparse_nodes - 1)),
        ),
        repeats=repeats,
    )
    _assert_total_order(sparse_result, sparse_nodes)
    dense_measurements, dense_result = _measure(
        lambda: quotient_and_reduce(
            range(dense_nodes),
            ((sub, super_) for sub in range(dense_nodes) for super_ in range(sub + 1, dense_nodes)),
        ),
        repeats=repeats,
    )
    _assert_total_order(dense_result, dense_nodes)
    return {
        "benchmark": "taxonomy-reduction",
        "dense": {
            "input_edge_count": dense_nodes * (dense_nodes - 1) // 2,
            "measurements": asdict(dense_measurements),
            "node_count": dense_nodes,
            "output_edge_count": len(dense_result.direct_edges),
        },
        "peak_rss_bytes": _peak_rss_bytes(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sparse": {
            "input_edge_count": sparse_nodes - 1,
            "measurements": asdict(sparse_measurements),
            "node_count": sparse_nodes,
            "output_edge_count": len(sparse_result.direct_edges),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sparse-nodes", type=int, default=100_000)
    parser.add_argument("--dense-nodes", type=int, default=1_000)
    parser.add_argument("--repeats", type=int, default=3)
    arguments = parser.parse_args()
    print(
        json.dumps(
            run(
                sparse_nodes=arguments.sparse_nodes,
                dense_nodes=arguments.dense_nodes,
                repeats=arguments.repeats,
            ),
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
