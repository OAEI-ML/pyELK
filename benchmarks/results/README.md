# Benchmark result records

JSON files in this directory are outputs of `tools/benchmark.py`.

- `wp13-local-python312-smoke.json` is a Java-free harness/semantic smoke observation from
  the WP13 development machine. It is not a release performance baseline and its timings are
  not enforced across machines.
- `wp13-local-native-python312-smoke.json` exercises the same quick corpus through the local
  release native library with four workers, including exact Python/native boundary equality.
  It is likewise diagnostic smoke evidence rather than a five-sample release baseline.
- Release records use a stable machine label, a clean commit, the full suite, five measured
  samples, native workers 1 and N, and the matching private-corpus/Java metadata where
  applicable.

The authoritative thresholds and corpus descriptors are in `../manifest.toml`. A performance
record never overrides semantic parity, completeness, packaging, or Java-free runtime gates.
