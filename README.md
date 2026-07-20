# pyELK

pyELK is a Java-free implementation of the core reasoning behaviour of
[ELK Reasoner 0.6.0](https://github.com/liveontologies/elk-reasoner/releases/tag/v0.6.0).
It provides one typed Python API with a portable pure-Python backend and an optional
Rust/PyO3 accelerator. Both backends consume the same deterministic compiled ontology and
return the same canonical public values.

The distribution is `pyelk-reasoner`; the import package is `pyelk`:

```shell
python -m pip install pyelk-reasoner
```

The separate `pyelk` distribution on PyPI is an unrelated graph-layout project. A normal
install selects a compatible native wheel when one is published and otherwise installs the
universal Python wheel. Python 3.10 and 3.12 run the complete release suites; the native
module uses the CPython 3.10 limited ABI and is additionally exercised on every supported
CPython minor through 3.14.

## First classification

pyELK delegates OWL syntax, structural values, imports, and immutable snapshots to the
independent `pyowl-core` package. A path, bytes-like value, or caller-owned stream can be
passed directly. Streams require a stable `document_iri`; pyELK never closes them.

<!-- pyelk-readme-example -->
```python
from io import BytesIO

import pyowl_core as owl
from pyelk import Reasoner

document = (
    b"Prefix(:=<urn:readme#>) Ontology(<urn:readme> "
    b"Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B))"
)
options = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)

with Reasoner(document, load_options=options) as reasoner:
    byte_result = reasoner.classify()
    taxonomy = byte_result.require_complete()
    assert reasoner.backend.name in {"python", "rust"}
    assert taxonomy.node(owl.Class(owl.IRI("urn:readme#A"))) is not None

stream = BytesIO(document)
with Reasoner(
    stream,
    document_iri="urn:readme:stream",
    load_options=options,
) as reasoner:
    assert reasoner.classify() == byte_result
assert not stream.closed
```

`Reasoner` also accepts `str`/`PathLike` paths, `OntologyDocument`, every immutable
`OntologyView`, and `SnapshotProvider`. Import resolution is explicit through
`LoadOptions` and an optional pyowl-core `ImportResolver`; unresolved or intentionally
ignored imports are never silently presented as complete reasoning.

## Completeness is part of every answer

Each operation returns `ReasoningResult[T]`, containing `value`, `complete`, and canonical
`reasons`. `complete` means complete for the pinned ELK 0.6 procedure, not for arbitrary OWL.
Inspect the metadata when a partial value is useful, or call `require_complete()` before
using a value that must be authoritative.

<!-- pyelk-readme-example -->
```python
import pyowl_core as owl
from pyelk import Reasoner
from pyelk.exceptions import IncompleteReasoningError

document = (
    b"Prefix(:=<urn:readme#>) Ontology(<urn:readme> "
    b"SubClassOf(ObjectAllValuesFrom(:p :A) :B))"
)
options = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)
with Reasoner(document, load_options=options) as reasoner:
    result = reasoner.classify()
    assert not result.complete
    assert any("OBJECT_ALL_VALUES_FROM" in issue.features for issue in result.reasons)
    try:
        result.require_complete()
    except IncompleteReasoningError as error:
        assert error.reasons == result.reasons
    else:
        raise AssertionError("an incomplete result was accepted")
```

Set `ReasonerConfig(unsupported="error")` when unsupported ontology constructs should fail
during construction instead of producing an explicitly incomplete result. The exact
construct, polarity, combination, per-operation monitor, and entailment-query boundary are
listed in [`specs/compatibility.md`](specs/compatibility.md).

## Exact-OM and shared snapshots

When a caller already owns a pyowl-core view, pass it directly. pyELK retains that exact
object, does not parse it again, and never converts public entities into a second OWL model.
An Exact-OM-style owner can expose only `owl_snapshot()`; it is called once. Source, target,
and bridge views can be combined as an identity-preserving `OntologyComposite`.

<!-- pyelk-readme-example -->
```python
import pyowl_core as owl
from pyelk import Reasoner

options = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)
source = owl.load_snapshot(
    b"Prefix(:=<urn:shared#>) Ontology(SubClassOf(:A :B))",
    options=options,
)
target = owl.load_snapshot(
    b"Prefix(:=<urn:shared#>) Ontology(SubClassOf(:B :C))",
    options=options,
)
shared = owl.compose_views(source, target, roles=("source", "target"))


class ExactModel:
    def __init__(self, view: owl.OntologyView) -> None:
        self.view = view
        self.calls = 0

    def owl_snapshot(self) -> owl.OntologyView:
        self.calls += 1
        return self.view


model = ExactModel(shared)
with Reasoner(model) as reasoner:
    assert reasoner.ontology is shared
    taxonomy = reasoner.classify().require_complete()
    public_a = next(entity for entity in shared.signature() if entity.iri.value.endswith("#A"))
    assert taxonomy.node(public_a).members[0] is public_a
assert model.calls == 1
```

pyELK imports neither Exact-OM nor any consumer-private record type. The interoperability
boundary is only pyowl-core's `OntologyView`/`SnapshotProvider` contract.

## OAEI and process boundaries

For a worker process, persist the shared snapshot with pyowl-core's versioned wire format.
The receiving process should verify and memory-map it before constructing the reasoner. No
OWL parser runs on this path.

<!-- pyelk-readme-example -->
```python
from pathlib import Path
from tempfile import TemporaryDirectory

import pyowl_core as owl
from pyelk import Reasoner

options = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
    backend=owl.BackendPreference.PYTHON,
)
document = b"Prefix(:=<urn:wire#>) Ontology(SubClassOf(:A :B))"
original = owl.load_snapshot(document, options=options)

with TemporaryDirectory() as directory:
    path = Path(directory) / "ontology.pyocore"
    path.write_bytes(owl.encode_snapshot(original))
    mapped = owl.open_snapshot(path, mmap=True, verify=True)
    try:
        with Reasoner(mapped) as reasoner:
            assert reasoner.ontology is mapped
            assert reasoner.classify().require_complete().nodes
    finally:
        mapped.close()
```

This is the supported OAEI-Bio-ML-eval handoff for timeout-isolated workers. Do not serialize
private pyELK indexes or consumer records as an interchange format.

## Backend selection and diagnostics

The default `auto` mode uses a compatible native extension when its version/ABI/IR handshake
passes, and otherwise selects Python while retaining the fallback reason. Use
`ReasonerConfig(backend="python" | "rust")` for a session-specific choice, or these process
controls:

- `PYELK_BACKEND=auto|python|rust` selects the default request;
- `PYELK_PURE_PYTHON=1` forces Python and prevents even probing `_native`;
- an explicit Rust request fails with `BackendUnavailableError` rather than falling back.

<!-- pyelk-readme-example -->
```python
import pyowl_core as owl
from pyelk import backend_report

report = backend_report()
assert report.selection_error is None
assert report.selected in {"python", "rust"}
assert report.core_package_version == owl.__version__
assert report.core_api_version == owl.API_VERSION
assert report.core_model_schema_version == owl.MODEL_SCHEMA_VERSION
assert report.core_wire_format_version == owl.WIRE_FORMAT_VERSION
assert report.core_adapter_protocol_version == owl.ADAPTER_PROTOCOL_VERSION

if report.selected == "python" and report.rust.reason:
    print(f"native backend not selected: {report.rust.reason}")
```

`reasoner.backend` reports the immutable choice, effective worker count, native availability,
and fallback reason for a live session. `reasoner.diagnostics()` returns an immutable scalar
mapping. Every session reports its canonical `compiler_digest`, compiler-cache and private-IR
schema versions, implementation version, total consumer compile time, scalar-row materialization
count, and an exact encoded buffer/copy/segment ledger. `ingestion_path` is `scalar-python`,
`scalar-wire`, or `encoded-native`; scalar sessions report contractual zero/false encoded
counters. Encoded sessions additionally report view-publication time, native validation,
compilation, session-build, and total native-boundary durations. Recursive composite sessions
count each temporary resolved posting byte and anonymous-scope mapping pair in
`encoded_staging_copy_bytes`; they never label those bounded metadata copies as zero-copy.
`pyelk.core.require_core_compatibility()` performs the full pyowl-core package/API/model/wire/
adapter guard explicitly.

## Supported reasoning surface

The public facade provides consistency, class and object-property classification,
realization, class-expression satisfiability, equivalent/sub/super-class queries,
instances/types, object-property views, entity enumeration, and the pinned supported
entailment families. It follows OWL 2 Direct Semantics and ELK's quiet inconsistent-ontology
values.

The complete fragment is deliberately “ELK 0.6 compatible,” not “all OWL 2 EL.” Named
classes/properties, intersections, existential restrictions, the core class axioms,
individual equality/difference, supported assertions, domains/ranges, and named property
hierarchies/chains are handled according to the pinned converter. Several data constructs,
cardinalities, universals, inverses, keys, and other features are ignored or partial with
exact completeness reasons. Annotation axioms remain in the shared snapshot but are
non-logical to pyELK.

## Runtime and current limits

Installed pyELK never launches or embeds Java. Java is used only by opt-in development tools
that regenerate or compare frozen ELK 0.6 oracle data; JARs and class files are rejected from
release artifacts. The Python fallback also needs no Rust/C/C++ compiler at installation or
runtime.

Version 0.1 sessions are immutable. Incremental reasoning, proofs/explanations/tracing,
method-for-method OWL API compatibility, datatype reasoning, a CLI, and a Protégé plugin are
outside scope. Input syntaxes and import retrieval are exactly those supplied by the installed
compatible pyowl-core. Native availability is platform-wheel dependent; Python remains the
semantic reference and fallback.

## Development verification

The default suite is Java-free:

```shell
PYELK_BUILD_PURE=1 PYELK_PURE_PYTHON=1 python -m pytest
ruff format --check .
ruff check .
mypy
lint-imports
```

The unified frozen-corpus runner covers all 124 upstream ontologies and 138 goldens:

```shell
python tests/parity/runner.py --backend python --workers 1
python tests/parity/runner.py --backend rust --workers 1 --workers 0
```

Integrated benchmark metadata and commands live in [`benchmarks/manifest.toml`](benchmarks/manifest.toml)
and `tools/benchmark.py`. Java oracle and performance gates are opt-in and pinned; ordinary
tests and installed artifacts have no Java dependency. See [`specs/verification.md`](specs/verification.md)
for the release gates and [`specs/traceability.md`](specs/traceability.md) for the implemented
source-to-test map.

pyELK is an independent Apache-2.0 reimplementation informed by the pinned ELK source and
tests. Attribution and modification details are in [`NOTICE.pyelk`](NOTICE.pyelk).
