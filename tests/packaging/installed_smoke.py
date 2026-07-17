#!/usr/bin/env python3
"""Run representative reasoning from an installed pyELK artifact."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-backend", choices=("python", "rust"), required=True)
    parser.add_argument("--force-python", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.force_python:
        os.environ["PYELK_PURE_PYTHON"] = "1"
        os.environ.pop("PYELK_BACKEND", None)
    elif args.expected_backend == "rust":
        os.environ.pop("PYELK_PURE_PYTHON", None)
        os.environ["PYELK_BACKEND"] = "auto"

    # Python is already running; restrict child-process discovery before importing
    # either package to prove the smoke path cannot find Java or a compiler.
    os.environ["PATH"] = str(Path(sys.executable).resolve().parent)
    assert all(
        shutil.which(command) is None
        for command in ("java", "javac", "cargo", "rustc", "cc", "gcc", "clang")
    )

    import pyowl_core as owl

    import pyelk
    from pyelk.core import require_core_compatibility

    source_root = Path(__file__).resolve().parents[2]
    installed_root = Path(pyelk.__file__).resolve()
    assert source_root not in installed_root.parents, (
        f"smoke test imported source checkout instead of installed artifact: {installed_root}"
    )
    require_core_compatibility()
    report = pyelk.backend_report()
    assert report.core_package_version == owl.__version__
    assert report.core_api_version == owl.API_VERSION
    assert report.core_model_schema_version == owl.MODEL_SCHEMA_VERSION
    assert report.core_wire_format_version == owl.WIRE_FORMAT_VERSION
    assert report.core_adapter_protocol_version == owl.ADAPTER_PROTOCOL_VERSION
    assert report.selected == args.expected_backend, report
    if args.expected_backend == "python" and args.force_python:
        assert report.rust.available is None and report.rust.reason
    elif args.expected_backend == "python":
        assert report.rust.available is False and report.rust.reason
    else:
        assert report.rust.available is True and report.rust.reason is None

    payload = (
        b"Prefix(:=<urn:installed#>) "
        b"Ontology(<urn:installed> "
        b"Declaration(Class(:A)) Declaration(Class(:B)) "
        b"SubClassOf(:A :B))"
    )
    options = owl.LoadOptions(
        format=owl.DocumentFormat.FUNCTIONAL,
        imports=owl.ImportPolicy.IGNORE,
        backend=owl.BackendPreference.PYTHON,
    )
    snapshot = owl.load_snapshot(payload, options=options)

    with pyelk.Reasoner(payload, load_options=options) as standalone:
        assert standalone.backend.name == args.expected_backend
        standalone_result = standalone.classify()
    with pyelk.Reasoner(snapshot) as shared:
        assert shared.backend.name == args.expected_backend
        assert shared.ontology is snapshot
        shared_result = shared.classify()
    assert standalone_result == shared_result
    print(
        f"installed smoke passed: backend={args.expected_backend} "
        f"python={sys.version_info.major}.{sys.version_info.minor}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
