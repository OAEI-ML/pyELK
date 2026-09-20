# pyELK 0.2.1 owner release authorization

Recorded: 2026-09-20

The repository owner explicitly requested committing, pushing, building, verifying CI,
and publishing the four optimized native packages to PyPI. This authorizes the
pyELK 0.2.1 release after its configured checks pass. Existing reviewed dependency
license selections in `THIRD_PARTY_LICENSES/inventory.toml` are unchanged.

This authorization does not assert that unrun checks have passed. Fresh release
evidence is generated under `reports/release/0.2.1`; historical evidence is retained.
The source distribution, universal wheel and seven native wheels must pass the
existing atomic artifact-set gates before trusted publication. The tested pyowl-core
0.2.1 source is bound in `release/core-compatibility.json`.
