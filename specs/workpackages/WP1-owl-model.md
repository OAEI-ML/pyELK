# WP1 — pyowl-core Contract Adoption and Public OWL Re-exports

## Goal

Adopt `pyowl-core>=0.2,<0.3` as pyELK's only public OWL structural model. Replace the
planned duplicate OWL values/keys with exact core re-exports, enforce API/adapter/wire
compatibility, and establish the typed snapshot input boundary. Implement no parser and no
reasoning semantics.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/parsing.md` | all |
| `specs/contracts.md` | §§1–4, 7 |
| `specs/compatibility.md` | §§1–4 |
| pyowl-core 0.2 specification | public model, snapshots, versions, adapters, ownership |
| pinned ELK OWL model | compatibility observations only; never a public model template |

## Depends on

WP0 and a released or workspace-pinned pyowl-core 0.2 contract.

## Owned paths

```text
src/pyelk/core.py                 # version/adapter guard and typed aliases
src/pyelk/owl/__init__.py         # re-exports only; no classes
pyproject.toml                    # add only runtime dependency/import-boundary amendments
tests/unit/core/**
tests/unit/owl/test_reexports.py
```

The implementer deletes obsolete pyELK OWL class/key modules after proving there are no
remaining imports. Deletion is part of this WP, not migration to another private model.

## Forbidden paths

Parser implementation, `pyowl_core` source, private compiled indexing/codec, reasoning,
backend adapters, public facade, Rust engine, oracle data, and build machinery beyond the
owned dependency/import-linter amendments.

## Deliverables

1. Typed imports/re-exports for core `IRI`, `Literal`, every required OWL 2 structural type,
   document/view/snapshot/delta/overlay/composite values, load options, policies, and provider protocol.
2. Identity-preserving `OntologyInput` typing and compatibility guard for core package SemVer/`API_VERSION`,
   `MODEL_SCHEMA_VERSION`, `WIRE_FORMAT_VERSION`, and `ADAPTER_PROTOCOL_VERSION`.
3. Identity-preserving propagation/re-export of core `AdapterCompatibilityError` and
   `OptionConflictError` for incompatible providers/views before compilation.
4. Import boundaries proving core/public OWL types cannot depend on pyELK backends.
5. Tests proving `pyelk` re-exports are the exact core class objects, not subclasses or
   field-compatible copies.
6. Removal plan/tests for the prior duplicate value classes, generic unsupported nodes,
   structural keys, and ELK-specific literal identity from the public model.
7. Project metadata requires `pyowl-core>=0.2,<0.3` and import-linter reflects the revised
   `SPEC.md` dependency direction.

## Acceptance criteria

1. Every construct in `compatibility.md` is received as a real pyowl-core structural value;
   no pyELK OWL runtime class remains.
2. `pyelk.Class is pyowl_core.Class` (and equivalent assertions for all re-exports) holds on
   Python 3.10 and 3.12.
3. Core values remain immutable/shareable, and import of `pyelk.owl` performs no parsing,
   native probing, Java/network/filesystem work, or adapter discovery.
4. Compatible 0.2 views/providers pass; incompatible API/model/wire/adapter contracts
   fail with expected/actual structured diagnostics.
5. Core standards-canonical literal identity is unchanged by pyELK. Pinned-ELK quirks are
   absent here and are tested later as compiler compatibility keys in WP4.
6. Forced-pure checks pass and only owned paths change.
