"""Leave an encoded native session open while the interpreter shuts down."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: _encoded_shutdown_runner.py NATIVE")

    native_path = Path(sys.argv[1]).resolve()
    spec = importlib.util.spec_from_file_location("pyelk._native", native_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load native extension from {native_path}")
    native = importlib.util.module_from_spec(spec)
    sys.modules["pyelk._native"] = native
    spec.loader.exec_module(native)

    import pyowl_core as owl
    from pyowl_core.backends import native_views

    source = b"""Prefix(:=<urn:encoded-shutdown#>) Ontology(<urn:encoded-shutdown>
    Declaration(Class(:A))
    Declaration(Class(:B))
    SubClassOf(:A :B)
    )"""
    options = owl.LoadOptions(backend=owl.BackendPreference.PYTHON)
    document = owl.parse_document(source, format=owl.DocumentFormat.FUNCTIONAL, options=options)
    snapshot = owl.load_snapshot(
        document,
        options=owl.LoadOptions(
            backend=owl.BackendPreference.PYTHON,
            imports=owl.ImportPolicy.IGNORE,
        ),
    )
    encoded = native_views.produce_encoded_structural_view_v2(snapshot)
    session = native.create_session_from_encoded(encoded, 2, "error")
    assert session.diagnostics()["encoded_zero_copy_buffers"] == 11
    assert session.class_taxonomy()
    print("encoded native session ready for interpreter shutdown", flush=True)

    # Keep the provider, encoded owner, and native session live. Normal interpreter teardown must
    # release them without an explicit close, deadlock, unraisable exception, or process abort.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
