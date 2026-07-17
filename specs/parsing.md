# pyowl-core ingestion and ELK structural adaptation

pyELK does not define an OWL object model, ontology document class, parser, import
resolver, canonical OWL writer, or general-purpose ontology index. Those responsibilities
belong to the Java-free distribution `pyowl-core` and import package `pyowl_core`.
pyELK 0.1.x requires `pyowl-core>=0.1,<0.2` and Python 3.10 or later.

The release integration baseline is pyOWLCore commit
`6df155e3ef83588352dbfd11bc4b15bdc0fa9c4e`, including canonical parser normalization for
duplicate-derived singleton intersections/unions and self-disjoint class lists. This is the
minimum source baseline used by the 124-input frozen parity gate; published dependency
resolution remains the compatible `>=0.1,<0.2` package range.

This document is the normative boundary between the shared structural layer and pyELK's
private ELK compiler. The OWL 2 Structural Specification remains the language authority;
the pinned ELK 0.6.0 implementation remains the reasoner-behaviour oracle.

## 1. Shared values are public values

pyELK MUST import and, where convenient, re-export the exact `pyowl_core` classes. It MUST
NOT subclass, proxy, copy, or recreate them. In particular, `IRI`, `Literal`, entities,
individuals, class/property expressions, annotations, axioms, `OntologyDocument`,
`OntologySnapshot`, `OntologyDelta`, and `OntologyOverlay` have the same class identity in
both packages. `OntologyView` is the read-only consumer protocol; `OntologySnapshot`,
`OntologyOverlay`, and `OntologyComposite` are sibling implementations.

This identity rule lets Exact-OM, pyELK, pyHermiT, and projectors exchange one parsed
ontology without object-by-object conversion. pyELK-specific support state, polarity,
numeric IDs, feature counts, and saturation data are not added to shared values; they live
in pyELK's private `CompiledOntology` and backend sessions.

The old planned `src/pyelk/owl/`, `src/pyelk/parsing/`, and `src/pyelk/ontology.py` model and
parser implementations are removed from scope. A small `pyelk/inputs.py` adapter and public
re-exports replace them.

## 2. Accepted ontology inputs

The public input union is conceptually:

```python
OntologyInput = (
    str
    | bytes
    | bytearray
    | memoryview
    | os.PathLike[str]
    | TextIO
    | BinaryIO
    | OntologyDocument
    | OntologyView
    | OntologySnapshot
    | OntologyOverlay
    | OntologyComposite
    | SnapshotProvider
)
```

`OntologyInput` and `DocumentInput` are public `pyowl_core` typing exports;
pyELK imports them and defines no new runtime values.

Every public standalone entry point delegates to the shared coercion operation:

```python
def load_snapshot(
    source: DocumentInput,
    *,
    options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
) -> OntologySnapshot: ...

class Reasoner:
    def __init__(
        self,
        ontology: OntologyInput,
        config: ReasonerConfig | None = None,
        *,
        document_iri: IRI | str | None = None,
        load_options: LoadOptions | None = None,
        resolver: ImportResolver | None = None,
    ) -> None: ...
```

`load_snapshot` is a thin pyELK convenience facade over `pyowl_core.load_snapshot` for
standalone acquisition and, like the core function, accepts acquisition/document input
only; core rejects view/provider input to `load_snapshot`, and pyELK never materializes a
view to satisfy the concrete snapshot return type. Shared callers pass a view to
`Reasoner`, whose first operation is
`coerce_snapshot`; it returns that view unchanged and establishes no second cache. Format
selection, byte decoding, parse limits,
document IRIs, resolver behavior, import policy, syntax support, and source ownership are
defined only by `pyowl_core.LoadOptions` and the core specification.

`document_iri` is forwarded unchanged to core. It is required for caller-owned text and
binary streams, and core rejects it for an already identified document, view, or provider.

A plain `str` is always a filesystem path, never inline ontology text or a URL. Inline text
uses an explicit `TextIO` plus core-required format/document IRI; URL acquisition belongs to
an explicitly configured `ImportResolver`.

`Reasoner` calls `pyowl_core.coerce_snapshot` exactly once. It MUST NOT pre-read a source to
guess its format and then ask core to read it again.
Options incompatible with an existing view propagate core `OptionConflictError`; pyELK never
reparses to satisfy them.

Before compilation it checks `view.capabilities`: adapter protocol/model compatibility and
the complete structural constructor families required by the ELK scanner are mandatory;
source-map spelling is optional with the explicit fallback in §7. Missing requirements raise
core `AdapterCompatibilityError` before any private IR is published.

## 3. Zero-reparse and adapter handshake

Input coercion obeys these rules:

1. A compatible `OntologyView` (`OntologySnapshot`, `OntologyOverlay`, or
   `OntologyComposite`) is returned by identity. No axiom,
   entity, literal, import graph, or index is copied merely to establish the session.
2. For `SnapshotProvider`, pyELK calls `owl_snapshot()` once per reasoner construction and
   then coerces the returned value. Exact-OM implements this protocol when it already owns
   a shared snapshot. pyELK MUST NOT import Exact-OM or inspect its private records.
3. An `OntologyDocument` is assembled into a snapshot by core without reparsing it.
4. Only a path, bytes value, or stream invokes core parsing. Imports are acquired and
   parsed at most once per core load/cache policy.
5. Duck-typed legacy ontology objects are not traversed implicitly. An explicit adapter or
   `SnapshotProvider` prevents accidental O(n) conversions and ambiguous semantics.

`pyowl_core.compose_views(*views, delta=None, roles=None)` builds a zero-copy
`OntologyComposite` for Exact-OM source + target + bridge/mapping axioms. pyELK compiles its
effective logical view while retaining component/provenance roles. It does not concatenate
the component axiom collections.

The handshake is view-based, not reasoner-to-reasoner. pyELK never consumes pyHermiT
clauses or Exact-OM records, and neither package consumes ELK indexes.

## 4. Imports closure and completeness

An `OntologyView` is an immutable read-only view. Snapshots retain resolved document
boundaries; overlays retain base+delta; composites retain their component roles. Every view
exposes effective closure provenance, ontology/import identities, source provenance, and
unresolved-import diagnostics. pyELK
iterates the logical axiom closure through `view.iter_axioms(...)`, obtains entity universes
through `view.signature(...)`, and requests core's lazily cached shared indexes/views where
appropriate; it MUST NOT flatten and
copy all imported axioms into a second public ontology.

For standalone loading, `LoadOptions.imports` is authoritative and uses core's secure
`RESOLVE_LOCAL`/offline default. Network access is never implicit. Callers may explicitly
choose core `ImportPolicy.IGNORE` or `RECORD_UNRESOLVED` only with pyELK's
incomplete-import guard. Missing, rejected, or deliberately ignored imports have these effects:

- a strict policy fails in `pyowl_core` before compilation;
- a snapshot explicitly marked as incomplete may be consumed only when pyELK configuration
  permits incomplete imports; and
- every affected reasoning result carries `PolicyFeature.IGNORED_IMPORT`, so it can never
  be reported complete accidentally.

For a composite, one incomplete member makes the effective view incomplete for this policy;
component roles do not hide or downgrade the issue.

The legacy `ReasonerConfig.ignore_imports` boolean is removed. Import acquisition is an
input-layer policy; pyELK configuration may contain only an explicit
`allow_incomplete_imports` guard for accepting a snapshot whose core provenance already
records that condition.

## 5. Fingerprints, revisions, and overlays

pyELK uses core-defined fingerprints without redefining their bytes:

- per-source byte SHA-256 is acquisition provenance only;
- `document_fingerprint` identifies a canonical complete document;
- `structural_fingerprint` identifies the resolved closure including annotations and the
  import graph;
- `logical_fingerprint` identifies the logical axiom closure; and
- `signature_fingerprint` identifies the visible declared/used signature.

ELK compilation and semantic caches key at least on `logical_fingerprint`,
`signature_fingerprint`, the pyowl-core public/wire compatibility versions, pyELK compiler
schema, pinned ELK compatibility version, and compiler options. A cache concerned with
annotations/import diagnostics additionally includes `structural_fingerprint`. Source
paths, timestamps, syntax, prefix spelling, Python hashes, and object addresses are never
semantic cache keys.

`OntologyOverlay` is an immutable `OntologyDelta` over an allowed base view and is a sibling
implementation, not an `OntologySnapshot` subclass. pyELK v1 is
not required to update saturation incrementally: it may compile a new private ELK IR for an
overlay. It MUST nevertheless traverse the overlay read-through view without materializing
or copying the unchanged base axiom closure. Core alone decides when an overlay chain is
compacted according to explicit depth/memory policy. A reasoner captures one exact
view revision; later overlays/composites do not mutate that session.

## 6. Ownership, lifetime, and copy budget

Core documents, ontology views, axioms, and indexes are immutable and caller-shareable.
A `Reasoner` retains a strong reference to its captured view until `close()` so
borrowed native buffers and public entity mappings cannot dangle. Closing a reasoner never
closes or invalidates the core object.

Source ownership is delegated to core: core closes streams it opens and never closes a
caller-provided stream. Resolver-owned resources follow the resolver contract.

Copy expectations are normative:

- view/provider ingestion: zero structural-model copies and zero reparses;
- Python compilation: one new private ELK IR plus bounded temporary tables, with iteration
  directly over core `iter_axioms`/`signature` values and indexes;
- native compilation/transfer: prefer borrowed buffers, `memoryview`, or an mmap-backed
  snapshot from `pyowl_core.open_snapshot`; otherwise at most one contiguous bulk copy per
  created session;
- no per-axiom Python/Rust callback in a hot reasoning loop; and
- no canonical Functional Syntax or RDF serialization as an intermediate representation.

Necessary ELK indexes, normalized expressions, feature occurrence vectors, and saturation
state are intentionally new reasoner-owned data, not a violation of the zero-copy input
contract.

## 7. ELK compiler compatibility keys

pyowl-core provides the standards-correct, source-preserving OWL structural model. Pinned
ELK quirks MUST NOT alter that public model. The pyELK compiler derives private compatibility
keys only where ELK 0.6.0 observable behavior needs them.

For literals, core preserves lexical form, canonicalizes language tags to lowercase public
identity, and may retain the original tag token in an optional `SourceMap`. Public `Literal`
equality and writing follow core. When the pinned ELK oracle distinguishes source tag case or
maps plain literals to its historical `(lexical + "@tag", rdf:PlainLiteral)` representation,
indexing creates an `ElkCompatibilityKey` in the compiled IR. If source spelling is
available, the key uses it. If a programmatic/wire/shared view has no source spelling, it uses
the canonical core tag, records that fallback in compiler diagnostics, and MUST NOT reparse a
path to recover trivia. A digest of any source-spelling compatibility inputs participates in
the pyELK compiler cache key. The private key is not exported, written back into the core
literal, or used by another consumer. Tests prove standards-correct shared identity, pinned
ELK source compatibility, and deterministic canonical fallback.

Likewise, ELK-supported/unsupported constructors are determined by an exhaustive pyELK
adapter table over core axiom/expression types. Core never creates generic
`UnsupportedExpression` or `UnsupportedAxiom` placeholders for pyELK. A structurally valid
OWL construct remains a real core value; pyELK's transactional converter records the exact
ELK `Feature` and ignores/partially indexes it according to `compatibility.md`.

## 8. Version and wire compatibility

- Packaging requires `pyowl-core>=0.1,<0.2`; dependency resolution, not a best-effort duck
  type, enforces the initial API line.
- Compatibility reads core `API_VERSION` and parses package SemVer; it never compares
  `__version__` strings lexically.
- A provider/view with missing capabilities or incompatible model/adapter versions fails
  before compilation with core `AdapterCompatibilityError`, reporting expected and actual
  package/API/model/wire/adapter values.
- Core's independent wire format begins with `PYOCORE\0`. Major mismatch is a hard error;
  readers may skip unknown optional sections within a compatible major; corrupt or unknown
  required sections fail closed.
- Shared persistence uses only `pyowl_core.encode_snapshot`, `decode_snapshot`, and
  `open_snapshot(path, mmap=True, verify=True)`; pyELK does not invent aliases or inspect
  private core buffers.
- Persistent shared-model caches are discarded and rebuilt on incompatible core API/wire
  or fingerprint schema. They are never interpreted optimistically.
- pyELK's private `PYELKIR` schema remains separate. It may change without changing the
  pyowl-core format and MUST NOT be presented as a shared ontology serialization.

Adapters declare the exact pyowl-core API range they implement. There is no global adapter
registry import at package import time; optional adapters are loaded only on explicit use.

## 9. Public construction helpers

pyELK may re-export selected `pyowl_core` factories such as class/entity constructors for
ergonomics, but the objects returned are exact core instances. Convenience functions such
as `intersection` are aliases/delegates to core behavior and MUST NOT introduce ELK-only
simplifications into public OWL values. ELK simplification belongs in indexing.

Standalone examples use ordinary paths/streams:

```python
from pyelk import Reasoner

with Reasoner("large.ofn") as reasoner:
    taxonomy = reasoner.classify()
```

Shared in-process examples pass an existing snapshot:

```python
snapshot = exact_om_run.owl_snapshot()
with Reasoner(snapshot) as reasoner:  # same snapshot object; no parse
    result = reasoner.is_consistent()
```

## 10. Acceptance requirements

1. Identity tests show every re-exported OWL/core class is the same object as its
   `pyowl_core` counterpart and no `pyelk.owl` wrapper class exists.
2. Path, bytes, text/binary stream, document, snapshot, overlay, composite, and provider inputs all
   produce the same compiled semantic result.
3. A counting provider proves one `owl_snapshot()` call; parser instrumentation proves zero
   parser calls for document/snapshot/overlay/composite/provider inputs.
4. A million-axiom snapshot compiles without a second materialized OWL axiom collection;
   an overlay changing O(k) axioms consumes O(k) core overlay memory before private ELK
   compilation, and a source/target/bridge composite concatenates no component collection.
5. Cyclic/multi-document imports are traversed once in deterministic closure order and
   ignored/missing imports never receive a complete result.
6. Fingerprint/cache property tests vary syntax, paths, prefix spelling, import order,
   Python hash seed, and backend while preserving the correct semantic keys.
7. Literal tests separate core structural/standards identity from private pinned-ELK keys,
   including language-tag case and plain-literal regressions.
8. Supported and unsupported core constructor coverage is exhaustive and tied to the ELK
   feature manifest; no valid OWL construct is lost during parsing.
9. Core 0.1 compatibility, wire-major rejection, optional-section skipping, corrupt-cache
   rebuilding, stream ownership, overlay lifetime, and close behavior are tested.
10. All tests run on CPython 3.10 and 3.12 without Java; Java is used only by an explicitly
    selected development-oracle lane.
