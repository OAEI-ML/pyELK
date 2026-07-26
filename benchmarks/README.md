# Integrated performance programme

`manifest.toml` is the immutable corpus and threshold contract. It pins the redistributable
ELK fixture manifest by SHA-256, describes every deterministic generated corpus, and defines
the metadata required for a licence-restricted biomedical corpus. Timing samples are accepted
only after each benchmark's semantic or structural assertions pass.

For a fast Java-free harness check:

```shell
python tools/benchmark.py --suite quick --workers 1
```

For release evidence on a dedicated, labelled native runner:

```shell
python tools/benchmark.py \
  --suite full \
  --native \
  --workers "$(getconf _NPROCESSORS_ONLN)" \
  --enforce \
  --machine-label linux-x86_64-release \
  --java-report /absolute/path/java-performance.json \
  --prior-release-report /absolute/path/prior-release.json \
  --biomedical-source /absolute/path/source.owl \
  --biomedical-source-sha256 SOURCE_SHA256 \
  --biomedical-source-axiom-count SOURCE_AXIOMS \
  --biomedical-source-entity-count SOURCE_ENTITIES_WITHOUT_BUILTINS \
  --biomedical-target /absolute/path/target.owl \
  --biomedical-target-sha256 TARGET_SHA256 \
  --biomedical-target-axiom-count TARGET_AXIOMS \
  --biomedical-target-entity-count TARGET_ENTITIES_WITHOUT_BUILTINS \
  --biomedical-alignment /absolute/path/train.tsv \
  --biomedical-alignment-sha256 ALIGNMENT_SHA256 \
  --biomedical-name CORPUS_NAME \
  --biomedical-origin CORPUS_SOURCE_URL_OR_CITATION \
  --biomedical-license CORPUS_LICENSE \
  --biomedical-expected-source-semantic-completeness-sha256 SOURCE_RESULT_SHA256 \
  --biomedical-expected-target-semantic-completeness-sha256 TARGET_RESULT_SHA256 \
  --biomedical-expected-composite-semantic-completeness-sha256 COMPOSITE_RESULT_SHA256 \
  --output benchmarks/results/linux-x86_64-release.json
```

The full protocol uses two warm-ups and five measured samples. Reports include median, median
absolute deviation, minimum, platform/CPU/RAM, Python, pyELK and pyowl-core revisions, worker
count, semantic digests, context/conclusion counts where available, peak-memory observations,
and the manifest hash. `--enforce` applies the native 5x throughput and 5% boundary thresholds
and requires `--suite full`, `--native`, a machine label, same-machine pinned Java and prior
enforced-release reports, the complete hash-pinned biomedical metadata above, and caller-approved
semantic/completeness digests. It rejects an otherwise successful run when the biomedical,
Java-relative, or release-regression report remains gate-ineligible; an integrated report can
therefore never claim `enforced: true` while carrying blocked evidence. Both the pyELK and sibling
pyowl-core revisions must resolve to exact commits with clean worktrees.

Native suites also run `bench_encoded_ingestion.py` from already-resident direct, overlay, and
composite views. It records raw phase samples and RSS observations for encoded-view acquisition,
native session creation, first taxonomy, and warm queries alongside the scalar compiler/private-
wire baseline. Compiler digests, section counts, and packed taxonomy bytes must agree exactly.
Native diagnostics split structural validation, compilation, session construction, and their
combined boundary duration. The 5% gate includes encoded-view acquisition, native validation,
and measured FFI overhead rather than treating validation as compiler work.
The generated structural workload uses many bounded eight-class hierarchy components, keeping
ontology size large while preventing identical quadratic transitive closure from swamping the
ingestion-path comparison; realistic reasoning pressure remains covered by the biomedical suite.
Ordinary development runs use the explicitly labelled scalar fallback producer while the core and
consumer capabilities remain unpublished; those reports are never gate-eligible. `--enforce`
forbids that fallback and requires public capability negotiation, zero parser/resolver/wire/
scalar-materialization/base-flattening deltas, a 2x geometric-mean view-to-result speedup, the 5%
boundary ceiling, and the 10% time/RSS guardrails from `manifest.toml`.

A pinned Java/ELK timing JSON can be attached with `--java-report` and is mandatory for
enforcement. The `pyelk.java-performance/1` object names the same machine label and worker
count, at least two warm-ups and five measured runs, the pinned ELK release/commit, the Java
version, the corpus name and three input hashes, and source/target/composite rows containing
the pinned semantic/completeness digest plus `warm_view_to_result_seconds`. Generate it on the
same machine, with the same ontology bytes and operations, after Java warm-up; the integrated
report records its payload, SHA-256, per-corpus native/Java median ratios, and geometric-mean
ratio. Java is never discovered or launched by the Python benchmark tool. Java-relative and
prior-release comparisons use the thresholds in `manifest.toml` on the labelled runner;
results from unrelated hardware are not compared.

`--prior-release-report` consumes an earlier enforced `pyelk.integrated-benchmark/1` record from
the same machine label, worker count, and performance-manifest hash. The earlier report must name a
different clean pyELK commit. The gate compares end-to-end, native-boundary, and all three
biomedical median times, plus encoded direct/mmap/overlay/composite median time and current-RSS
growth, while requiring identical fixture/compiler/result/semantic identities. Any time or RSS
regression above the manifest's 10% limit fails enforcement.

External biomedical corpora are not committed unless their licence permits redistribution.
`bench_biomedical.py` verifies all source, target, and alignment hashes before parsing, then
captures and rechecks the exact byte buffers it parses. It loads each ontology once, retains
source/target/composite identity, validates that every `SrcEntity` and `TgtEntity` TSV value
names a class in the corresponding ontology, maps those rows to an `EquivalentClasses` bridge,
and checks every backend against caller-pinned source, target, and composite
taxonomy/completeness digests. Only alignment-referenced class IRIs are retained during
membership validation. Expected axiom and non-built-in entity counts are also fail-closed
inputs. The standalone report schema is `pyelk.biomedical-benchmark/2`.
A source or target with imports is rejected because the three-file contract cannot pin those
additional bytes; use self-contained benchmark documents or extend the manifest first.
A missing private corpus prevents performance sign-off but cannot weaken a semantic test or
create a compatibility exception.

Normal benchmark timing does not enable `tracemalloc`, because it materially distorts large
ontology runs. The standalone biomedical runner offers `--trace-allocations` for diagnostic
allocation peaks and marks those wall timings as non-gating. `ru_maxrss` is a process-lifetime
high-water mark, so its before/after growth is labelled as order-dependent diagnostic evidence,
not per-phase peak RSS. Public session diagnostics expose the selected ingestion path and encoded
buffer/copy/segment counters; recursive composite staging counts resolved posting bytes and
64-byte anonymous-scope remap pairs, while scalar-wire private-IR byte length remains deliberately
private.

`results/` distinguishes smoke evidence from release baselines. Regenerate reports with the
commands recorded there; do not hand-edit timing values.
