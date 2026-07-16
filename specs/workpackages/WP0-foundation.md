# WP0 — Foundation, IR Codec, and Backend Contracts

## Goal

Create the installable pure-Python project skeleton and freeze the cross-agent contracts:
compiled IR records/codec, backend/session raw-result protocol, exception categories, import
boundaries, and reusable test doubles. Implement no OWL parsing or reasoning semantics.

## Read first

| Source | Sections |
|---|---|
| `specs/SPEC.md` | all, especially §§6–11 |
| `specs/contracts.md` | all |
| `specs/indexing.md` | §§7–9 (occurrence fields and validation) |
| `specs/native-packaging.md` | §§4–8 |
| `specs/verification.md` | §§1, 3, 10–12 |

## Depends on

None.

## Owned paths

```text
pyproject.toml
setup.py                         # foundational conditional build-mode helper
src/pyelk/exceptions.py
src/pyelk/indexing/ir.py
src/pyelk/indexing/codec.py
src/pyelk/reasoning/contracts.py
src/pyelk/reasoning/wire.py
tests/helpers/
tests/unit/indexing/test_codec.py
tests/unit/reasoning/test_contracts.py
.github/workflows/ci.yml         # pure foundation lanes only
```

WP0 may create package directories and minimal empty `__init__.py` files. Those files contain
no speculative exports and become owned by their later WP.

## Forbidden paths

`src/pyelk/owl/**`, `parsing/**`, semantic indexing files, reasoning implementation files,
Rust crates, oracle data/tooling, benchmarks, and public facade implementation.

## Deliverables

1. Setuptools `src/` package supporting Python 3.10+, distribution name `pyelk-reasoner`,
   import namespace `pyelk`, `py.typed`, Apache licence metadata, and zero mandatory runtime
   dependencies.
2. Dev/test extras for pytest, Hypothesis, ruff, mypy, import-linter, and build tooling.
3. Import-linter contracts matching `SPEC.md` §7.
4. Frozen record/enums for `CompiledOntology`, `CompiledQuery`, 79-position feature vectors,
   query mini-IR/fresh-result IDs, raw
   taxonomy/realisation/query results, `ReasoningTask`, `PolicyFeature`,
   `CompletenessIssue`, backend diagnostics, `BackendConfig`, `BackendFactory`, and
   `BackendSession`.
5. Ontology/query IR v1.0 and raw-result v1.0 little-endian section codecs with checksums and
   exhaustive validation.
6. Exception hierarchy from `contracts.md` §6.
7. `FakeBackendSession`, tiny compiled-ontology builder, and raw-result invariant helpers.
8. Empty/one-record golden codec bytes and architecture/hash-seed determinism tests.
9. Pure CI lanes for Python 3.10 and latest supported Python; no Java/compiler assumption.

## Acceptance criteria

1. `python -m build --wheel` produces an installable `py3-none-any` foundation wheel with
   `PYELK_BUILD_PURE=1` and no Java/native file.
2. Empty and representative IR encode/decode round trips are byte-identical across two hash
   seeds; corrupt magic/version/checksum/offset/CSR/enum/UTF-8 cases fail with the specified
   exception, not raw `IndexError`/`MemoryError` from trusted-small fixtures.
3. A fake backend can return a valid class taxonomy through the frozen protocol without
   importing semantic modules.
4. `import-linter` proves `owl`, parsing/indexing values, and public result layers cannot
   import `_native` or test/oracle code.
5. `pytest`, ruff, mypy, and import-linter pass in an environment with Java and Cargo removed
   from `PATH`.
6. `git diff --name-only` contains only owned/foundation files.
