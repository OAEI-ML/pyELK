# Reproducible pyELK distributions

pyELK publishes one project/version as a source distribution, a universal Python fallback,
and one `cp310-abi3` wheel for each tier-one native platform. Every native wheel includes
the same Python sources, `_native.pyi`, and `py.typed` as the fallback wheel. Unsupported
platforms and interpreters therefore resolve the universal wheel without an extra or a
failed native source build.

## Local prerequisites

- CPython 3.10 or later.
- `build==1.5.0`, `setuptools==83.0.0`, `setuptools-rust==1.13.0`, and `wheel==0.46.3`.
- Platform release audits use `abi3audit==0.0.26` plus `auditwheel==6.7.0`,
  `delocate==0.13.0`, or `delvewheel==1.13.0` as appropriate.
- Rust 1.97.1 only for a native build. The committed lockfile is always enforced.
- No Java runtime, Java bridge, JAR, ontology download, or code generator is used.

Set `SOURCE_DATE_EPOCH` to the source commit timestamp before release builds. Start from a
clean checkout, or run `python setup.py clean --all`, so a previous native extension cannot
remain in setuptools' local build cache.

The source and fallback release lane builds both artifacts twice and requires byte identity.
The custom sdist command normalizes gzip/tar timestamps, ownership, modes, and entry order.

## Build modes

Universal fallback (the only supported way to create the published `py3-none-any` wheel):

```bash
PYELK_BUILD_PURE=1 PYELK_REQUIRE_NATIVE=0 \
  python -m build --wheel --no-isolation --outdir dist
python tools/check_artifact.py check dist/*-py3-none-any.whl --expect pure-wheel
```

Mandatory native ABI3 wheel (a compiler, link, or import failure is fatal):

```bash
PYELK_BUILD_PURE=0 PYELK_REQUIRE_NATIVE=1 \
  python -m build --wheel --no-isolation --outdir dist
python tools/check_artifact.py check dist/*-cp310-abi3-*.whl --expect native-wheel
```

Ordinary source install (Rust is attempted when available and is optional):

```bash
python -m build --sdist --no-isolation --outdir dist
python -m pip install dist/*.tar.gz
```

Do not hide Cargo to manufacture a universal wheel. The default optional extension is for
source installs only; `PYELK_BUILD_PURE=1` is what guarantees a zero-extension universal
artifact. `CIBUILDWHEEL=1` makes the extension mandatory, matching
`PYELK_REQUIRE_NATIVE=1`.

## Audit and installed checks

Audit and compare a local paired build:

```bash
python tools/check_artifact.py check dist/*.tar.gz --expect sdist
python tools/check_artifact.py check dist/*-py3-none-any.whl --expect pure-wheel
python tools/check_artifact.py check dist/*-cp310-abi3-*.whl --expect native-wheel
python tools/check_artifact.py compare \
  dist/*-py3-none-any.whl dist/*-cp310-abi3-*.whl
```

`check_artifact.py` fails closed on incorrect tags, unsafe archive paths, Java/JVM payloads or
dependencies, unallowlisted shared libraries, absolute build paths, incompatible
`pyowl-core` metadata, or divergent Python payloads. CI additionally lets cibuildwheel run
ABI3 and platform repair/dependency audits with pinned external tools.

For an offline compiler/JRE-free test, first populate a wheelhouse while online. It must
contain `pyowl-core` and, for a source install, every `[build-system]` requirement:

```bash
python -m pip download --dest wheelhouse --only-binary=:all: \
  'pyowl-core>=0.1,<0.2' 'setuptools>=77,<84' \
  setuptools-rust==1.13.0 'wheel>=0.45,<0.47'
python tests/packaging/install_artifact.py dist/*.tar.gz \
  --python "$(command -v python)" --wheelhouse wheelhouse \
  --expected-backend python --expected-core-backend python \
  --expected-ingestion scalar-python
```

The helper creates a new environment, disables network access for installation, removes
Java/Cargo/C/C++ tools from `PATH`, imports only the installed artifact, and compares
standalone bytes with the exact already-parsed snapshot path.

Place the fallback and a compatible native wheel together in one directory to verify pip's
preference rules:

```bash
python tests/packaging/check_index_preference.py --index dist
```

## Failure diagnostics

| Symptom | Meaning and action |
|---|---|
| `PYELK_BUILD_PURE=1 conflicts ...` | Pure and mandatory-native modes were combined; choose one. |
| `can't find Rust compiler` in mandatory mode | Install the pinned Rust toolchain; release CI must fail. |
| The same message in default source mode | The optional extension is skipped and backend diagnostics record the Python fallback. |
| Native filename is not `cp310-abi3` | The limited-API wheel option was bypassed; do not publish it. |
| More than one `_native` library | Clean the local setuptools build directory and rebuild. |
| Absolute source/Cargo path in a wheel | Path remapping was lost or stale objects were reused; rebuild cleanly. |
| Pure/native hash mismatch | Artifacts were not built from the same source tree and version. |
| `pyowl-core` compatibility error | Install the supported `>=0.1,<0.2` line with matching model/wire/adapter versions. |

## Release policy

`.github/workflows/wheels.yml` builds and tests the source/fallback pair and seven native
wheels on native x86-64/AArch64 hardware. CPython 3.10 runs the installed native suite under
both backends; CPython 3.11 through 3.14 repeat standalone/shared smoke checks, including
musllinux in native Alpine containers. The final job requires exactly nine files, compares every native
wheel with the fallback, and stages one `release-bundle` artifact.

`.github/workflows/release.yml` reruns that complete matrix. A tag push only stages and
audits. Publication requires a manual run from a matching `v<version>` tag with `publish`
explicitly enabled; only the last environment-protected trusted-publishing job receives an
OIDC token. No job publishes a partial artifact set or uses `skip-existing`.
