# WP12 — Compiler-Free Distribution and Native Wheel Release Matrix

## Goal

Turn the integrated pure/Rust package into one sdist, one universal fallback wheel, and strict
ABI3 native wheels under the same name/version. Implement automatic wheel preference,
compiler-free source fallback, installed-artifact tests, ABI/dependency audits, and atomic
release automation.

## Read first

| Source | Sections |
|---|---|
| `specs/native-packaging.md` | §§4–11 |
| `specs/verification.md` | §§9–12 |
| `specs/contracts.md` | configuration/backend diagnostics |
| setuptools-rust optional/ABI3 and cibuildwheel docs linked by native spec | current pinned tool behaviour |

## Depends on

WP10 and WP11.

## Owned paths

```text
pyproject.toml                    # dependent update of WP0 scaffold
setup.py                          # dependent update of WP0 build modes
MANIFEST.in
Cargo.toml/Cargo.lock             # packaging-only metadata changes coordinated with WP11
.github/workflows/wheels.yml
.github/workflows/release.yml
tools/check_artifact.py
tests/packaging/**
```

## Forbidden paths

OWL/parser/indexing/reasoning semantics, public API behaviour, Rust algorithms/bindings,
completeness/oracle corpus, benchmark thresholds/results.

## Deliverables

1. `RustExtension` default optional, `PYELK_REQUIRE_NATIVE=1` mandatory, and
   `PYELK_BUILD_PURE=1` zero-extension modes.
2. PyO3 `abi3-py310`, pinned Rust/Python build toolchain, locked release build.
3. cibuildwheel tier-one matrix and native-hardware tests/audits.
4. Universal wheel and sdist builds; every native wheel bundles the full fallback.
5. Runtime metadata pins `pyowl-core>=0.2,<0.3`; installed tests cover compatible core
   pure/native variants and reject incompatible model/wire/adapter versions.
6. Local simple-index/TestPyPI preference tests with native and pure artifacts together.
7. Artifact content/metadata/Python-hash/JVM/shared-library/ABI audit tool.
8. Atomic release workflow that publishes nothing unless the whole staged matrix passes.
9. Documented reproducible local build commands and failure diagnostics.

## Acceptance criteria

1. Compiler/JRE-free sdist install succeeds and completes representative reasoning with
   `backend.name == "python"`.
2. `PYELK_BUILD_PURE=1` produces correctly tagged `py3-none-any`; hiding Cargo without this
   switch is not used for published artifacts.
3. Every tier-one native wheel is `cp310-abi3`, imports `_native`, selects Rust in auto, and
   passes the full installed suite under both forced backends.
4. A deliberately broken Rust build fails mandatory native CI but succeeds in ordinary
   optional source mode with a diagnostic fallback reason.
5. Local-index pip chooses native on compatible CPython and universal on an unsupported
   interpreter/platform simulation.
6. ABI3 and platform audits pass; archive/dependency scan finds no Java/JAR/class, JVM bridge
   (including JPype/JNI), and no unallowlisted shared library/absolute path.
7. Pure/native project metadata, Python sources, stubs, and type marker are identical.
8. Matrix includes Linux glibc/musl x86-64/AArch64, macOS x86-64/arm64, and Windows AMD64;
   an architecture without native-hardware validation publishes fallback only.
9. CPython 3.10 and 3.12 installed-artifact lanes pass for standalone and already-parsed
   snapshot inputs with zero Java available.
