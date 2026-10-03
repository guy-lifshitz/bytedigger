# S3 (bd#231): MGR acceptance after the gate round cap

Gate r1: REJECTED, 3 MAJOR (closed in spec r2 + RED r2). Gate r2: REJECTED, 1 MAJOR (`2026-10-03-s3-gate-r2.md`, MAJOR 1: `ambiguous_pairing` was fail-open). The cap of 2 rounds was reached, so the decision went to the coordinator.

MGR decision 2026-10-03 (inbox, bd#231): no round 3; GREEN proceeds under MGR acceptance on these conditions:

1. spec r3: `ambiguous_pairing` is fail-closed (blocking finding, AC excluded from the ok-set, never clean).
2. A RED test for exactly the MAJOR case (`prose share < 0.25 over 12 real stubs`, `measured: 0.31` -> not clean).
3. This acceptance file in the PR, linking gate-r2.
4. One Sonnet review of the GREEN diff.
5. Inventory-lint tests (`test_bd94*`, `test_bd150*`, `test_bd206*`) run before push.

Also from MGR: no undercover bypass; Russian threshold words appear only as `\uXXXX` escapes in code, tests and docs.

Status of each condition is recorded in the PR description.
