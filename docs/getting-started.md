# Getting started with pyELK

## Install

pyELK requires Python 3.10 or newer:

```bash
python -m pip install --upgrade pip
python -m pip install pyelk-reasoner
```

A compatible native wheel is selected automatically when available. Otherwise,
pip installs the complete compiler-free Python implementation. Neither path
requires Java.

## Classify an ontology

`Reasoner` accepts a path, bytes, a caller-owned text or binary stream (pass a
stable `document_iri` with it), an `OntologyDocument`, an existing
`pyowl_core.OntologyView`, or a `SnapshotProvider`:

```python
import pyowl_core as owl
from pyelk import Reasoner

ontology = (
    b"Prefix(:=<urn:example#>) Ontology(<urn:example> "
    b"Declaration(Class(:A)) Declaration(Class(:B)) SubClassOf(:A :B))"
)
options = owl.LoadOptions(
    format=owl.DocumentFormat.FUNCTIONAL,
    imports=owl.ImportPolicy.IGNORE,
)

with Reasoner(ontology, load_options=options) as reasoner:
    result = reasoner.classify()
    taxonomy = result.require_complete()
    assert taxonomy.node(owl.Class(owl.IRI("urn:example#A"))) is not None
```

Use a context manager so native and Python session resources are released
deterministically.

## Handle completeness

Every reasoning operation returns `ReasoningResult[T]`. Check
`result.complete` and `result.reasons` when partial ELK-fragment answers are
useful, or call `result.require_complete()` when a partial result must fail.
Set `ReasonerConfig(unsupported="error")` to reject unsupported constructs when
the reasoner is created.

## Select a backend

The default `auto` mode uses a compatible native extension and otherwise keeps
the Python backend:

```python
from pyelk import ReasonerConfig, backend_report

print(backend_report())
python_only = ReasonerConfig(backend="python")
native_required = ReasonerConfig(backend="rust")
```

An explicit `rust` request fails if acceleration is unavailable; it never
silently falls back. `PYELK_PURE_PYTHON=1` prevents native probing for
compiler-free or diagnostic deployments.

## Load once, reason many times

Applications with multiple ontology consumers should load through `pyowl-core`
once and pass the resulting view directly:

```python
import pyowl_core as owl
from pyelk import Reasoner

view = owl.load_snapshot("ontology.owl")
with Reasoner(view) as reasoner:
    assert reasoner.ontology is view
```

Configure import resolution while loading the view. Ignored or unresolved
imports are never silently described as complete reasoning.

## Next steps

The [API reference](api-reference.md) covers every public operation, the result
and taxonomy values, diagnostics, and the exception hierarchy. The
[architecture overview](architecture.md) explains how inputs are compiled and
saturated before answers are produced. Upgrading from 0.1? Read the
[0.2 migration guide](migration-0.2.md) before reusing persisted snapshots or
caches.
