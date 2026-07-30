# pyELK 0.1.1 owner release authorization

Recorded: 2026-07-30

The repository owner explicitly authorized promotion and publication of the complete
current-main pyELK 0.1.1 patch release after verifying the remaining release gates. This
authorization closes the release-owner approval required by
`THIRD_PARTY_LICENSES/inventory.toml`, including the reviewed third-party license selections
represented by that inventory.

This record is a release decision, not replacement test or benchmark evidence. Historical
0.1.0 evidence remains unchanged under `reports/release/0.1.0`. The 0.1.1 evidence is generated
independently under `reports/release/0.1.1`.

The 0.1.1 release publishes the source distribution, universal fallback wheel, and all seven
tier-one native wheels atomically through the environment-protected trusted-publishing workflow.

The production release is bound to pyowl-core 0.1.1 commit
`b0d8fd27537b2f177cfe9a5e0fd41f33b9f18f19` and tree
`e72fc93248cd363a5c67dac9efffb367a71c2b1d`.
