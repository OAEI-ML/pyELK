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

The production release remains bound to pyowl-core 0.1.0 commit
`d3e7893b0609fcd7df390375267a00356f09cb22` and tree
`32cc4cbf9c99f1b45785cb29f4f059ec0f86a691`.
