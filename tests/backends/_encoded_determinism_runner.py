"""Fresh-process encoded compiler determinism probe used by the native test suite."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pyowl_core as owl
from pyowl_core.backends import native_views

from pyelk.indexing.compiler import compile_ontology


def _load_native(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("pyelk._native", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load native extension from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    return module


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: _encoded_determinism_runner.py NATIVE ONTOLOGY")
    native = _load_native(Path(sys.argv[1]))
    snapshot = owl.load_snapshot(
        Path(sys.argv[2]),
        options=owl.LoadOptions(
            format=owl.DocumentFormat.FUNCTIONAL,
            imports=owl.ImportPolicy.IGNORE,
            backend=owl.BackendPreference.PYTHON,
        ),
    )
    encoded = native_views.produce_encoded_structural_view_v2(snapshot)
    compiled = compile_ontology(snapshot, unsupported="ignore")
    scalar = native.create_session(compiled.encode(), 1)
    try:
        expected = {
            "class_taxonomy": _sha256(scalar.class_taxonomy()),
            "compiler_digest": scalar.diagnostics()["compiler_digest"],
            "debug_snapshot": _sha256(scalar.debug_snapshot(realize=True)),
            "object_property_taxonomy": _sha256(scalar.object_property_taxonomy()),
            "realization": _sha256(scalar.realization()),
        }
        counts = {
            key: value
            for key, value in scalar.diagnostics().items()
            if key.startswith("compiler_") and key.endswith("_count")
        }
    finally:
        scalar.close()

    workers: dict[str, object] = {}
    for worker_count in (0, 1, 2, 4):
        direct = native.create_session_from_encoded(encoded, worker_count, "ignore")
        try:
            actual = {
                "class_taxonomy": _sha256(direct.class_taxonomy()),
                "compiler_digest": direct.diagnostics()["compiler_digest"],
                "debug_snapshot": _sha256(direct.debug_snapshot(realize=True)),
                "object_property_taxonomy": _sha256(direct.object_property_taxonomy()),
                "realization": _sha256(direct.realization()),
            }
            if actual != expected:
                raise AssertionError((worker_count, actual, expected))
            workers[str(worker_count)] = actual
        finally:
            direct.close()

    print(
        json.dumps(
            {"compiler_counts": counts, "expected": expected, "workers": workers},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
