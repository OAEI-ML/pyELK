# Rust Backend and Packaging

pyELK uses Rust/PyO3 for native acceleration and setuptools/setuptools-rust for one canonical
source tree. The choice does not claim Rust instructions are inherently faster than C;
performance comes from owning the whole graph algorithm natively with coarse FFI. Rust is
chosen for memory-safe compact graph state, safer parallelism, and maintainable wheels.

Primary packaging references:

- [PyO3 stable ABI features](https://pyo3.rs/main/features#abi3)
- [PyO3 parallelism and detaching Python](https://pyo3.rs/main/parallelism)
- [setuptools-rust `RustExtension`, including `optional`](https://setuptools-rust.readthedocs.io/en/latest/reference.html#rustextension)
- [setuptools-rust ABI3 wheels](https://setuptools-rust.readthedocs.io/en/latest/building_wheels.html#building-for-abi3)
- [cibuildwheel Rust guidance](https://cibuildwheel.pypa.io/en/stable/faq/#building-rust-wheels)
- [Python platform-tag preference](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/#use)

## 1. Native architecture

```text
Cargo.toml                         # workspace, locked dependency policy
Cargo.lock
rust-toolchain.toml               # pinned stable release profile
rust/
├── pyelk-core/                    # no Python/PyO3 types
│   └── src/
│       ├── ir.rs                  # validated decoder
│       ├── properties.rs
│       ├── context.rs
│       ├── rules.rs
│       ├── saturation.rs
│       ├── taxonomy.rs
│       ├── realization.rs
│       └── query.rs
└── pyelk-pyo3/                    # cdylib named pyelk._native
    └── src/lib.rs                 # thin binding/session wrapper only
```

`pyelk-core` has no dependency on PyO3, Python objects, Python allocation, or public API
types. It accepts validated IR bytes/slices and returns Rust-owned raw result records. This
separation enables `cargo test`, fuzzing, Criterion benchmarks, sanitizers, and reuse without
starting Python.

`pyelk-pyo3` exposes only:

```text
ir_version() -> (major, minor)
implementation_version() -> str
create_session(ir: bytes, workers: int) -> NativeSession
NativeSession.is_inconsistent() -> bool
NativeSession.class_taxonomy() -> packed bytes
NativeSession.object_property_taxonomy() -> packed bytes
NativeSession.realization() -> packed bytes
NativeSession.query_class_expression(query_ir_or_none, kind, direct) -> packed bytes
NativeSession.entails(query_ir_or_none) -> bool
NativeSession.diagnostics() -> dict[str, scalar]
NativeSession.close()
```

Public classes and exceptions remain Python-owned. No Rust type other than the private
session handle appears in a public signature.

## 2. FFI and threading rules

- Create one native session per Python `Reasoner`; capture the compatible core view without
  parsing, compile one private ELK IR, and transfer that IR once.
- Calls cross the boundary by complete ontology, complete query, or complete result, never by
  axiom, rule, edge, or set operation.
- Validate magic, version, checksum, lengths, offsets, enum tags, IDs, CSR invariants, and
  UTF-8 before building graph allocations.
- Do not retain borrowed Python buffers or any `PyObject` in `pyelk-core`.
- Use `Python::detach` around Rust-only compile/saturate/taxonomy/query work. Reattach only to
  create the final Python bytes/scalars.
- Native computation may use Rayon/crossbeam. `workers=1` avoids the pool; `workers=0` uses
  logical CPU count; positive values cap the session pool.
- Never call Python, logging handlers, signal handlers, or progress callbacks from a worker.
- Check Python interruption only at coarse safe stage boundaries if implemented; exact Java
  interruption timing is out of scope.
- Catch panics at every exported method. A poisoned/failed session becomes closed and raises
  `InternalReasonerError`; it cannot be reused.

Binding overhead is measured separately and MUST remain below 5% of native classification
time on medium/large corpora.

## 3. Native data structures

The core uses contiguous IDs and adjacency-oriented structures:

- `u32` IDs, `usize` only for checked local indexing;
- `Vec`/boxed slices for immutable IR tables;
- CSR for static expression operands, axiom groups, and property maps;
- dense bitsets for high-density subsumer domains, adaptive sorted small vectors or hash sets
  for sparse domains, chosen from measured profiles;
- sharded context storage and a duplicate-free active-context scheduler;
- hashers with deterministic semantics and denial-of-service-safe handling for untrusted
  input; hash iteration order never affects output;
- sorted canonical output before encoding to Python.

`unsafe` is forbidden by default. Any necessary local `unsafe` optimisation requires:

1. a safety comment stating preconditions;
2. a safe debug assertion of those preconditions;
3. Miri coverage where applicable;
4. fuzz/property tests comparing the safe Python backend;
5. a benchmark showing at least 5% end-to-end benefit on a release corpus.

## 4. ABI policy

Build the private extension with PyO3 `abi3-py310` and the extension-module feature. One
native wheel per OS/architecture supports ordinary GIL-enabled CPython 3.10 and later.

Stable-ABI restrictions are acceptable because the binding exchanges bytes and scalars, not
hot-loop Python collections. CI compares `abi3` with version-specific experimental builds.
Switching the release matrix to per-minor wheels requires evidence of at least 5% geometric-
mean end-to-end improvement on the representative corpus; microbenchmark-only gains do not
qualify.

Free-threaded CPython and PyPy use the pure wheel initially. Native support may be added only
after dedicated correctness/thread-safety certification and correct wheel tags.

## 5. One-source build modes

`setup.py` declares the `RustExtension("pyelk._native", ..., binding=Binding.PyO3)` according
to these environment variables:

| Mode | Rust extension declaration | Purpose |
|---|---|---|
| default | `optional=True` | Local/sdist install tries Rust if available; build failure still installs full Python fallback. |
| `PYELK_REQUIRE_NATIVE=1` | `optional=False` | Native wheel CI; any Cargo/compiler/link/import failure is fatal. |
| `PYELK_BUILD_PURE=1` | no Rust extension declared | Published `py3-none-any` wheel; no Cargo probe and correct universal tag. |

Do not create the universal wheel by hiding Cargo and relying on `optional=True` failure.
setuptools-rust advertises an extension before compilation, which can create an unnecessarily
platform-specific fallback wheel and poison installer caches. Explicit pure mode is required.

The sdist includes Python sources, Cargo workspace, lockfile, licence, and build metadata.
Without Cargo, its optional build succeeds as a complete Python install. With Cargo, it may
build the native extension. Project metadata requires `pyowl-core>=0.1,<0.2` in every
artifact. No build step downloads Java, a JVM bridge/JAR, or ontology assets.

## 6. Published artifacts

For one version `V`, publish together:

```text
pyelk_reasoner-V.tar.gz
pyelk_reasoner-V-py3-none-any.whl
pyelk_reasoner-V-cp310-abi3-<tier-one-platform>.whl
```

Artifact filenames use the normalized form `pyelk_reasoner`; installers request the project
as `pyelk-reasoner`, and users import it as `pyelk`.

Every native wheel also contains the identical Python fallback, type hints, and `py.typed`.
Compatible installers prefer the more specific native tag; unsupported platforms receive
the universal wheel. Both wheels have identical project metadata and Python-file hashes; only
the extension, wheel metadata/tags, and generated RECORD may differ.

Do not split `pyelk-reasoner-native` into a required extra. Extras cannot express “install
only when a compatible wheel exists” reliably and would turn missing native artifacts into
failed source builds instead of automatic fallback.

## 7. Native wheel matrix

Tier one, built and tested before release:

| OS | Tag / architecture |
|---|---|
| Linux glibc | `manylinux_2_17_x86_64`, `manylinux_2_17_aarch64` |
| Linux musl | `musllinux_1_2_x86_64`, `musllinux_1_2_aarch64` |
| macOS | x86-64 with minimum 10.12; arm64 with minimum 11.0 |
| Windows | AMD64 |

Windows ARM64 is tier two until native-hardware wheel tests are available. The universal
wheel covers it meanwhile. The universal wheel also covers 32-bit systems, alternative
architectures/interpreters, and unsupported operating systems.

Native architecture policy:

- never compile release wheels with `-C target-cpu=native`;
- use the portable target baseline required by the wheel tag;
- optional SIMD uses runtime feature detection and a scalar implementation with differential
  tests;
- bundle no non-system shared libraries; prefer pure Rust dependencies;
- build with a pinned stable Rust toolchain, committed `Cargo.lock`, `--locked --release`.

## 8. Dispatcher

`src/pyelk/backends/__init__.py` resolves once per `Reasoner` creation. It validates
`PYELK_BACKEND` as `auto|python|rust` and `PYELK_PURE_PYTHON` as `0|1`, then:

1. choose the request from a non-`auto` `ReasonerConfig.backend`, otherwise
   `PYELK_BACKEND`, otherwise `auto`;
2. if pure mode is `1`, reject request `rust` as a conflict and replace `auto`/`python` with
   `python` without importing `_native`;
3. resolve the remaining `auto|python|rust` request below.

- `auto`: import `_native`, compare Python/native implementation and IR major versions, run a
  constant-time self-check, then choose Rust; on absence/failure choose Python and retain the
  reason in `BackendInfo`.
- explicit `python`: never import `_native`.
- explicit `rust`: any absence, handshake mismatch, or self-check failure raises
  `BackendUnavailableError`.
- `PYELK_PURE_PYTHON=1`: forces Python unless the effective request is explicitly Rust, in
  which case configuration validation fails clearly.
- A session never changes backend after creation.

`backend_report()` is side-effect-light and returns installed/selected availability,
extension version, ABI/IR version, fallback reason, and captured pyowl-core package,
`MODEL_SCHEMA_VERSION`, `WIRE_FORMAT_VERSION`, and `ADAPTER_PROTOCOL_VERSION`. It does not
start a reasoner session.
Under `PYELK_PURE_PYTHON=1` it does not import/probe `_native`; Rust availability is `None`
with a pure-mode reason rather than a misleading installed/not-installed answer.

## 9. CI build and audit

Use cibuildwheel with pinned action/tool versions. For every artifact:

1. build in an isolated environment;
2. inspect tag and contents;
3. install the wheel into a fresh environment, never test the source tree;
4. assert the expected backend is selected;
5. run unit, parity-fixture, and backend-forcing smoke suites;
6. run `abi3audit` on native extensions;
7. audit/repair Linux wheels with auditwheel, macOS with delocate, and Windows DLL
   dependencies with delvewheel-equivalent inspection;
8. scan archive contents for `.jar`, `.class`, JVM launchers, absolute build paths, and
   unapproved shared libraries, and inspect dependency metadata for JPype/JNI/Java packages;
9. compare pure/native metadata and Python file hashes;
10. stage all artifacts and publish only if the complete matrix passes.

Exercise each ABI3 wheel on CPython 3.10 and every currently supported later CPython minor.
Architecture wheels require native-hardware tests; emulated build-only success is not enough
to publish a tier-one artifact.

## 10. Packaging acceptance gates

1. `pip install pyelk-reasoner==V --only-binary=:all:` works without Java, Cargo, a C
   compiler, or network access beyond the package index on every supported environment.
2. Tier-one CPython selects a native ABI3 wheel; an unsupported platform/interpreter selects
   `py3-none-any` automatically.
3. Installing the sdist with Java/Cargo/C/C++ absent succeeds, selects Python, and passes the
   no-Java core smoke suite.
4. Every native wheel runs the entire suite once with Rust and once with forced Python.
5. Native-required CI fails if `_native` is absent; optional mode never masks release build
   breakage.
6. `abi3audit` and platform dependency audits pass.
7. Results are exact across OS, architecture, Python minor, hash seed, and worker count.
8. No JVM or unallowlisted dynamic-library dependency exists.
9. Metadata contains the compatible pyowl-core requirement, and installed tests cover the
   pyELK Python/Rust × pyowl-core Python/native matrix where artifacts exist.

## 11. Performance gates

Correctness gates always take precedence. Initial native release targets:

- at least 5x the pure backend's geometric-mean classification throughput on medium/large
  corpora;
- native boundary overhead below 5% on those corpora;
- no more than 2x ELK 0.6.0 Java wall time on any primary corpus and a geometric mean no more
  than 1.25x Java after warm-up;
- no release-to-release regression above 10% in time or peak RSS without an approved,
  documented trade-off;
- results and counts identical for workers 1 and N, with multi-core speedup reported rather
  than asserted on machines with at least four physical cores.

Benchmarks compare standalone core loading and shared-view capture separately, then
compilation, property saturation, class saturation, taxonomy,
total time, peak RSS, and produced conclusion counts separately. `native` vs C is reconsidered
only if profiling shows PyO3/Rust-specific overhead of at least 10% end-to-end after
algorithm/data-layout optimisation.
