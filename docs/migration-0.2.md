# Migrating to pyELK 0.2

pyELK 0.2 requires `pyowl-core>=0.2,<0.3`. Upgrade both packages together:

```bash
python -m pip install --upgrade "pyelk-reasoner>=0.2,<0.3" "pyowl-core>=0.2,<0.3"
```

The `Reasoner`, `ReasonerConfig`, result, and query APIs are unchanged. The compatibility
boundary changes to pyowl-core API `(0, 2)`, model schema 2, wire `(1, >=2)`, adapter protocol 1,
and `pyowl-core/structural-columns` schema 2.

Model schema 2 introduces component-scoped anonymous-individual identity. Regenerate persisted
pyowl-core snapshots and any application compiler caches created with model schema 1 from their
authoritative ontology sources. pyELK and pyowl-core reject stale schema identities rather than
silently interpreting their bytes under the new model.

Custom `OntologyView` providers may continue to omit encoded structural views; pyELK then uses its
complete scalar compiler. A provider that advertises only schema 1 is also treated as lacking the
required schema and selects scalar compilation before any encoded data is acquired. A provider
that advertises schema 2 must publish its frozen descriptor: malformed or falsely advertised
schema-2 data fails closed without scalar fallback.

Use `pyelk.backend_report()` before constructing a reasoner when deployment diagnostics need to
show the selected package/API/model/wire/adapter versions and native fallback reason.
