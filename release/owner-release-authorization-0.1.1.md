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
`0aab7b137b5a6eef173b8ec000aa84ff8d41e196` and tree
`ca01ade1c99f804b7be550ac245a94fbf7411149`.
