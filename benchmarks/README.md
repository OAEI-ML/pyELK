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
and requires `--suite full`, `--native`, a machine label, the complete hash-pinned
biomedical metadata above, and caller-approved semantic/completeness digests. It rejects an
otherwise successful run when the biomedical report remains gate-ineligible; an integrated
report can therefore never claim `enforced: true` while carrying blocked evidence.

Native suites also run `bench_encoded_ingestion.py` from already-resident direct, overlay, and
composite views. It records raw phase samples and RSS observations for encoded-view acquisition,
native session creation, first taxonomy, and warm queries alongside the scalar compiler/private-
wire baseline. Compiler digests, section counts, and packed taxonomy bytes must agree exactly.
Ordinary development runs use the explicitly labelled scalar fallback producer while the core and
consumer capabilities remain unpublished; those reports are never gate-eligible. `--enforce`
forbids that fallback and requires public capability negotiation, zero parser/resolver/wire/
scalar-materialization/base-flattening deltas, a 2x geometric-mean view-to-result speedup, the 5%
boundary ceiling, and the 10% time/RSS guardrails from `manifest.toml`.

A pinned Java/ELK timing JSON can be attached with `--java-report`. Generate it on the same
machine, with the same ontology bytes and operations, after Java warm-up; the integrated
report records its payload and SHA-256. Java is never discovered or launched by the Python
benchmark tool. Java-relative and prior-release comparisons must use the ratios in
`manifest.toml` on the labelled runner; results from unrelated hardware are not compared.

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
not per-phase peak RSS. The public `Reasoner` facade does not expose private compiled-IR byte or
native-copy counters, so the report records that limit explicitly.

`results/` distinguishes smoke evidence from release baselines. Regenerate reports with the
commands recorded there; do not hand-edit timing values.
