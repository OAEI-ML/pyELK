# Third-party license inventory

`inventory.toml` records every locked crate reachable from the production
`pyelk-pyo3` extension. Development-only benchmark, test, and fuzz
dependencies remain in `Cargo.lock` but are deliberately excluded from the
artifact SBOM.

For dependencies offered under `MIT OR Apache-2.0`, release artifacts select
the Apache-2.0 option; its complete text is the repository-root `LICENSE`.
`generic-array` is MIT-only and `subtle` is BSD-3-Clause, so their exact
notices are retained here. `target-lexicon` additionally carries the LLVM
exception, while `unicode-ident` also requires the Unicode License v3 text.

This checked inventory is engineering evidence, not legal approval. A release
owner or counsel must approve the selected licenses and any source, notice, or
relinking obligations before publication. No Java, OWLAPI, ROBOT, JPype,
DeepOnto, or mOWL component is linked or bundled by pyELK.
