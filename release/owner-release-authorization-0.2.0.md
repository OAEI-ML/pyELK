# pyELK 0.2.0 owner release authorization

Recorded: 2026-08-02

The repository owner explicitly authorized promotion and publication of the production
pyELK 0.2.0 release after verifying and closing the remaining release gates. This authorization
also carries forward the reviewed third-party selections and legal approval recorded in
`THIRD_PARTY_LICENSES/inventory.toml`.

This record is a release decision, not replacement test or benchmark evidence. Historical 0.1.x
evidence remains unchanged under its existing `reports/release/` directories. The 0.2.0 evidence
is generated independently under `reports/release/0.2.0`.

The 0.2.0 release publishes the source distribution, universal fallback wheel, and all seven
tier-one native wheels atomically through the environment-protected trusted-publishing workflow.
It migrates the shared ontology boundary to pyowl-core 0.2.0, API `(0, 2)`, model schema 2,
wire `(1, 2)`, adapter protocol 1, and `pyowl-core/structural-columns` schema 2. The exact tested
pyowl-core commit and tree are the values in `release/core-compatibility.json`; generated build
provenance must agree with that ledger before publication.
