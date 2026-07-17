# pyELK

pyELK is a pure-Python and Rust/PyO3 reimplementation in progress of the core reasoning
behaviour of [ELK Reasoner 0.6.0](https://github.com/liveontologies/elk-reasoner/releases/tag/v0.6.0),
without a Java runtime dependency. The pure-Python backend is the complete portable
fallback; supported wheels also contain the high-performance Rust backend behind the same
public API.

The revised implementation specification uses the separate Java-free distribution
`pyowl-core` (import `pyowl_core`) as its canonical OWL structural/parser/view layer.
pyELK will accept paths and streams standalone or reuse an Exact-OM/core view without
reparsing, while keeping ELK-specific indexes and saturation state private.

The WP0 foundation is implemented: packaging, typed IR/backend contracts, deterministic
binary codecs, pure-build controls, test doubles, and foundation CI are in place. The
pyowl-core dependency/input migration, reasoning semantics, public facade, and Rust
accelerator remain assigned to later work packages, so this is not yet a usable OWL
reasoner release.

The distribution is named `pyelk-reasoner` and the Python import namespace is `pyelk`:

```shell
python -m pip install pyelk-reasoner
```

The separate [`pyelk` name on PyPI](https://pypi.org/project/pyelk/) is already used by an
unrelated graph-layout project; keeping the import namespace preserves the intended API
without colliding at installation.

## Implementation specifications

- [Master specification](https://github.com/OAEI-ML/pyELK/blob/main/specs/SPEC.md): scope,
  invariants, architecture, and definition of done.
- [Pinned baseline](https://github.com/OAEI-ML/pyELK/blob/main/specs/baseline.toml): immutable
  ELK release, commit, and oracle settings.
- [Compatibility contract](https://github.com/OAEI-ML/pyELK/blob/main/specs/compatibility.md):
  exact ELK fragment, partial features, completeness, and supported operations.
- [Component contracts](https://github.com/OAEI-ML/pyELK/blob/main/specs/contracts.md): public
  API, compiled IR, backend protocol, and wire values.
- Subsystem specifications:
  [pyowl-core ingestion](https://github.com/OAEI-ML/pyELK/blob/main/specs/parsing.md),
  [indexing](https://github.com/OAEI-ML/pyELK/blob/main/specs/indexing.md),
  [saturation](https://github.com/OAEI-ML/pyELK/blob/main/specs/saturation.md),
  [taxonomy and queries](https://github.com/OAEI-ML/pyELK/blob/main/specs/taxonomy-queries.md),
  and
  [native packaging](https://github.com/OAEI-ML/pyELK/blob/main/specs/native-packaging.md).
- [Verification plan](https://github.com/OAEI-ML/pyELK/blob/main/specs/verification.md):
  pinned Java oracle, frozen ELK corpus, differential tests, packaging tests, and performance
  gates.
- [ELK traceability](https://github.com/OAEI-ML/pyELK/blob/main/specs/traceability.md):
  upstream package-to-specification and package-to-owner mapping.
- [Parallel work packages](https://github.com/OAEI-ML/pyELK/blob/main/specs/workpackages/README.md):
  WP0–WP13, dependency waves, exclusive file ownership, deliverables, and exact acceptance
  gates.

Start with the master specification, then assign agents only from the work-package index.
Java is permitted in opt-in fixture regeneration and differential testing, but never in an
installed pyELK artifact or at runtime.
