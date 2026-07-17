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
  --output benchmarks/results/linux-x86_64-release.json
```

The full protocol uses two warm-ups and five measured samples. Reports include median, median
absolute deviation, minimum, platform/CPU/RAM, Python, pyELK and pyowl-core revisions, worker
count, semantic digests, context/conclusion counts where available, peak-memory observations,
and the manifest hash. `--enforce` applies the native 5x throughput and 5% boundary thresholds
and is rejected without `--native`.

A pinned Java/ELK timing JSON can be attached with `--java-report`. Generate it on the same
machine, with the same ontology bytes and operations, after Java warm-up; the integrated
report records its payload and SHA-256. Java is never discovered or launched by the Python
benchmark tool. Java-relative and prior-release comparisons must use the ratios in
`manifest.toml` on the labelled runner; results from unrelated hardware are not compared.

External biomedical corpora are not committed unless their licence permits redistribution.
Record the corpus name, source, licence, SHA-256, axiom count, and entity count beside the
machine result. A missing private corpus may prevent performance sign-off but cannot weaken a
semantic test or create a compatibility exception.

`results/` distinguishes smoke evidence from release baselines. Regenerate reports with the
commands recorded there; do not hand-edit timing values.
