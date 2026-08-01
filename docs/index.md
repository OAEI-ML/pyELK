# pyELK documentation

pyELK provides ELK-compatible OWL reasoning through a typed Python API. It is
Java-free at installation and runtime, with a complete Python backend and an
optional Rust accelerator.

```bash
python -m pip install pyelk-reasoner
```

## Start here

- [Getting started](getting-started.md) covers installation, first
  classification, completeness, backend selection, and input handling.
- The [API reference](api-reference.md) describes the complete public surface:
  session lifecycle, configuration, queries, taxonomy and realization values,
  entailment, backend selection, diagnostics, and exceptions.
- The [architecture overview](architecture.md) explains the compile-then-reason
  pipeline: inputs, indexing, saturation, and taxonomy construction.
- The repository [README](../README.md) contains executable examples for shared
  snapshots, cross-process wire transport, and diagnostics.
- [Compatibility specification](../specs/compatibility.md) defines the complete
  supported reasoning fragment and partial-result behavior.
- [Verification specification](../specs/verification.md) describes semantic,
  packaging, and backend parity gates.
- [Benchmarks](../benchmarks/README.md) explains reproducible performance runs.

## Package names

| Purpose | Name |
|---|---|
| PyPI distribution | `pyelk-reasoner` |
| Python import | `pyelk` |
| Shared ontology dependency | `pyowl-core` / `pyowl_core` |

The `pyelk` distribution on PyPI is a different graph-layout project. Always
install `pyelk-reasoner`.
