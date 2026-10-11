# bd#202 gate r2 (TIER2)

Spec r2 and RED re-audited against gate r1. Preflight: 19 fail at assert time, 31 siblings green.

## r1 resolution
1. AC8 relocation: ACCEPTED. The preflight requires every RED test to fail pre-GREEN, so a pure guard cannot live in the RED file. The spec now names the guard's home (the rewritten test_bd89_p3b1b_i_straggler_removed.py). Carry it as a GREEN obligation, see fix 1 below.
2. Positional/keyword-only: addressed (test_ac1_no_positional_slot_for_straggler_cfg, derived via inspect, fails pre-GREEN).
3. Whole-source AC5: addressed (`"straggler_cfg" not in` the full llm_subprocess.py source).
4. AC7 top section: addressed (first unreleased/release section; helpers.changelog exists with parse_sections, is_unreleased, is_release).

## Adversarial edges
none new beyond the above.

## Notes
- Recording wrappers delegate to the real UUT, so no vacuous mocks.
- The AC8 guard has no RED coverage by design. GREEN must add it and verify it before the sibling rewrite is accepted.

## Fixes
1. (GREEN obligation, not a RED defect) The rewritten test_bd89_p3b1b_i_straggler_removed.py must contain the AC8 assertion: "abort" in `_DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"]`.

VERDICT: PASS
