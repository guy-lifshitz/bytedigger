# bd261 gate r3 (narrow, delta only)

Delta: `sim.listdir_calls.clear()` inserted after `_guard_is_live(...)` in `test_ac3_listing_without_match_returns_path_sim`, plus the spec header change to FROZEN.

## Findings

1. Satisfiable by a correct GREEN. `_guard_is_live` lists `ctl-root` once, so the counter holds `[ctl-root]` after it. Before the fix, the final `== [root]` assertion could not hold. The clear resets the counter. The raced case then lists `root` exactly once (the lstat hit comes from `force_exists`, the listing finds no match, and the function returns the path). The counter ends as `[root]`, which a correct GREEN satisfies.
2. Still fails on base for the right reason. Base has no guard, so the positive control `_refused("r1", ctl)` does not raise `RunIdCaseCollision`. The test fails at assert time (`pytest.raises` reports "DID NOT RAISE"), not at collect time. Even without the control, base never calls listdir, so `[] != [root]`.
3. No assertion weakened. The return-value assertion and the `listdir_calls == [root]` assertion are unchanged. The clear sits after the control and before the raced setup, so it hides no call made inside the scenario under test. The test still catches a GREEN that skips the listing or lists twice.
4. Spec header change is metadata only. It does not touch any AC.

No MAJOR or MINOR findings. The r2 verdict is not reopened.

## Checklist (delta)
- AC3: PASS

VERDICT: APPROVE

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd261-run-id-case-collision.md sha256:7ef8913205df6f27cc36e5f3b879e2ac995f2c4d567e01f07b8a23c12f8c69cd
red: engine_py/tests/test_bd261_run_id_case_collision.py sha256:aefa0f62c2789f978da7e95d0265c47fdac7d55f7b1115528e4b05ffad736d7d
-->
