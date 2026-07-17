# Quarantined ELK 0.6.0 reference oracle

This Maven application is development-only evidence for pyELK. It reads one JSON object
per line from standard input and writes one canonical response per line to standard output.
It is deliberately outside `src/`, is never imported by the Python package, and its build
products must never be committed or included in a wheel or source distribution.

The oracle pins ELK `0.6.0` (source tag `v0.6.0`, commit
`b8ac5ce83db0704a7359d96aa382891e2f547863`), OWLAPI `5.1.20`, one worker,
non-incremental reasoning, and fresh entities enabled. `tools/oracle.py regenerate` is the
only supported caller. It verifies the JDK and Maven identities before building in an
isolated Maven repository.

The request schema is fail-closed. Every line must use schema `1`, the exact fixed
configuration (`workers=1`, `incremental=false`, `allow_fresh_entities=true`, and
`unsupported=ignore`), an object-valued `arguments`, and no unknown top-level fields. The
oracle exposes identity, structural load, consistency, feature-count, class/object-property
taxonomy, realisation, class-query, entailment, and diagnostic saturation-count operations.
All maps, nodes, edges, relations, and query lists are canonicalised before JSON encoding.

Regenerate or check the committed evidence with the pinned local toolchains:

```shell
python tools/oracle.py regenerate \
  --java-home /private/tmp/exact-owl-toolchains/temurin17/jdk-17.0.19+10/Contents/Home \
  --maven /private/tmp/exact-owl-toolchains/maven/3.9.16/libexec/bin/mvn \
  --maven-repository /private/tmp/pyelk-oracle-m2
python tools/oracle.py regenerate --check
python tools/oracle.py verify
```

`regenerate` executes all 204 requests twice in clean oracle processes, refuses any byte
difference, cross-checks 138 original ELK goldens, and atomically installs 124 case plus 79
feature expectation files. `verify` consumes only committed files and needs no Java, Maven,
compiler, network, or executable on `PATH`.

The `tests` classifier is intentional: ELK's own frozen taxonomy and query loaders define
the semantics of its upstream golden files. The oracle uses those original loaders for a
bidirectional semantic comparison while independently serialising the actual reasoner value.

The pinned ELK object implementation has one investigated dispatch defect:
`ElkDatatypeDefinitionAxiomImpl.accept(ElkAxiomVisitor)` returns `null`. The oracle wraps
that parsed axiom with an otherwise transparent implementation whose generic visitor overload
dispatches correctly. This lets ELK's own converters and incompleteness monitors record
`DATATYPE_DEFINITION` and `QUERY_DATATYPE_DEFINITION_AXIOM`, as required by its pinned
`Feature` enum and converter implementations. The correction is oracle-only and is recorded
in the generated evidence report.

No Java toolchain is needed to install or test pyELK from committed frozen data.
