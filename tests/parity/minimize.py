#!/usr/bin/env python3
"""Minimize a Functional-Syntax regression while an external command keeps failing.

The source is parsed and deterministically re-rendered through pyowl-core before reduction.
Imports, ontology annotations, and extension components are retained; only root logical axioms
are candidates for removal. The command must contain a ``{ontology}`` path placeholder.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pyowl_core as owl


@dataclass(frozen=True, slots=True)
class MinimizationResult:
    """Deterministic reduced document and axiom counts."""

    source_axioms: int
    minimized_axioms: int
    document: bytes


def _document_with_axioms(document: owl.OntologyDocument, axioms: Sequence[owl.Axiom]) -> bytes:
    reduced = replace(
        document,
        axioms=owl.CanonicalSet(axioms),
        source_map=None,
        origin_index=None,
        rdf_mapping_report=None,
        diagnostics=(),
    )
    return owl.render_document(reduced, format=owl.DocumentFormat.FUNCTIONAL)


def _ddmin(
    axioms: tuple[owl.Axiom, ...],
    interesting: Callable[[tuple[owl.Axiom, ...]], bool],
) -> tuple[owl.Axiom, ...]:
    current = axioms
    granularity = 2
    while current:
        chunk_size = max(1, (len(current) + granularity - 1) // granularity)
        reduced = False
        for start in range(0, len(current), chunk_size):
            candidate = current[:start] + current[start + chunk_size :]
            if interesting(candidate):
                current = candidate
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        if granularity >= len(current):
            break
        granularity = min(len(current), granularity * 2)
    return current


def minimize_document(
    source: Path,
    predicate: Callable[[Path], bool],
) -> MinimizationResult:
    """Return a 1-minimal root-axiom subset preserving ``predicate``."""

    options = owl.LoadOptions(
        format=owl.DocumentFormat.FUNCTIONAL,
        imports=owl.ImportPolicy.IGNORE,
        backend=owl.BackendPreference.PYTHON,
    )
    snapshot = owl.load_snapshot(source, options=options)
    root = snapshot.root
    axioms = tuple(root.axioms)
    with tempfile.TemporaryDirectory(prefix="pyelk-minimize-") as temporary:
        candidate_path = Path(temporary) / "candidate.ofn"

        def interesting(candidate: tuple[owl.Axiom, ...]) -> bool:
            candidate_path.write_bytes(_document_with_axioms(root, candidate))
            return predicate(candidate_path)

        if not interesting(axioms):
            raise ValueError("the failure predicate does not hold for the canonical source")
        minimized = _ddmin(axioms, interesting)
    return MinimizationResult(
        source_axioms=len(axioms),
        minimized_axioms=len(minimized),
        document=_document_with_axioms(root, minimized),
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expect-exit", type=int, default=1)
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command containing {ontology}; prefix it with --",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    command = arguments.command
    if command and command[0] == "--":
        command = command[1:]
    if not command or not any("{ontology}" in token for token in command):
        raise SystemExit("the command after -- must contain a {ontology} placeholder")

    def predicate(path: Path) -> bool:
        concrete = [token.replace("{ontology}", str(path)) for token in command]
        completed = subprocess.run(
            concrete,
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.returncode == arguments.expect_exit

    result = minimize_document(arguments.source, predicate)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(result.document)
    print(
        json.dumps(
            {
                "schema": "pyelk.regression-minimizer/1",
                "source_axioms": result.source_axioms,
                "minimized_axioms": result.minimized_axioms,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
