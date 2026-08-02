# Changelog

All notable changes to pyELK are documented here.

## 0.2.0 — 2026-08-02

- Migrate the shared ontology contract to `pyowl-core>=0.2,<0.3`, API `(0, 2)`, model
  schema 2, wire 1.2, and `pyowl-core/structural-columns` schema 2.
- Preserve fail-closed package, capability, descriptor, fingerprint, and native-envelope
  negotiation while retaining scalar fallback when encoded schema 2 is not advertised.
- Reject stale model-schema-1 snapshots, encoded views, and compiler-cache identities instead of
  interpreting them under component-scoped anonymous identity.

## 0.1.1 — 2026-07-30

- Publish the complete universal, source, and seven-platform native wheel set from current main.
- Include encoded structural ingestion and cross-platform release-gate corrections completed
  after the initial portable-only 0.1.0 upload.
- Publish through the environment-protected GitHub OIDC trusted-publishing workflow.

## 0.1.0 — 2026-07-30

- Publish the Java-free OWL 2 EL reasoner with pure-Python fallback and optional Rust acceleration.
- Consume the released `pyowl-core>=0.1,<0.2` structural model and encoded-view contract.
- Include deterministic source, universal-wheel, native-wheel, license, SBOM, and provenance controls.
- Provide taxonomy, realization, entailment, consistency, and class-expression query APIs.
