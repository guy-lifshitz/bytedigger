# bd#130 gate round 2: AUTONOMOUS cap policy (spec Rev 2 + revised RED)

Auditor: hal-gate-agent. Branch lot-130, HEAD 8390bdd. Read-only audit, no tests run (preflight: RED 270 fail / 0 pass, guards 87 pass).

## r1 must-fix status
- M1 RESOLVED. AC9 now covers only `plan_review: pass` states. Guard GUARD_AC9 holds 6 pass fixtures and compares default vs legacy stdout and rc. SUPERVISED pwc is under AC2, and the RED test asserts ROW_A is present and ROW_LEGACY_PWC is absent.
- M2 RESOLVED. §2.1 and §2.2 now apply the COMPLEX check to `pass_with_concerns` too, ordered after rows a-d, at phase 5 only. AC13 scenarios: no override, other override (`manual` is echoed), override passes, 4.5 has no COMPLEX row, and row order b,d,COMPLEX.
- M3 RESOLVED. §2.3 and §2.4 define when the table is read, unreadable vs malformed, and that comments/blank lines are ignored. AC12 RED tests: missing, empty, comments-only, 4-field row, ids `X9` and `p2`, COMPLEX path, and `<path>` taken from the copied table. A custom table limits which ids are accepted, so a hard-coded id set fails. The guards check the table is never read on a plain pass or under legacy (missing, empty and bad_row states).
- M4 RESOLVED. §2 says an unknown value emits the unknown row first and then the legacy verdict, without reading the table. AC14 has 5 values (`bogus`, `AUTO`, ` legacy`, `Legacy`, `legacy `) x {pass, pwc, COMPLEX pass, table removed}. The tests assert order and absence.

## 4-step audit
- Step 1 (token drift): I found 4 minor inconsistencies (N1-N4). None of them changes whether the gate passes or blocks.
- Step 1.5 (branch simulation): I simulated these branches in order: policy (legacy / unknown / autonomous), then plan_review (pass / pwc / other), then a-d, then §2.2 at phase 5 only. Every SC expectation matches the first branch that applies. The fixture `mode: Autonomous` yields row a and `PASS` yields the legacy row, which is consistent with exact compare.
- Step 2 (§2 vs §3): every soft-block branch has an AC. The "stop with resumable state" post-condition (state untouched) has no explicit assertion; see N5.
- Step 3 (RED adequacy): all tests run the real bash and TS backends through subprocess with no mocks, and bun missing makes them FAIL. Every interpolated `<v>`, `<id>`, `<path>` and policy value differs across scenarios, so a stub that hard-codes rows cannot pass. AC10 compares bash and TS byte for byte across all SC scenarios, and the guards add legacy and bogus policy parity. AC5, AC7 and AC9 are covered only by guards, by design: they hold on main and constrain GREEN afterwards. One example: a stub that ignores the env fails the AC7 guard once AC1 is green.
- Step 4 (reachability): unchanged from r1. Rows 4.5 and 5 of phase-deliverables.tsv go through check_deliverables (build-gate.sh) and checkDeliverables (build-phase-gate.ts), and the tests reach both through subprocess.

## RED-author ambiguities: rulings (none blocking)
1. `[p1]`: the §2.3 regex is normative and case-sensitive, so `[p1]` is a malformed value. Listing `p1` as an unknown id in §2.3 and AC15 is a spec erratum (N1). Not blocking: the RED test accepts either row, and both outcomes soft-block.
2. Block-sequence `autonomous_pauses`: yaml_get returns an empty value, so it falls in the "no value" bullet. That matches how §2.1b treats block-sequence concerns. Listing it under malformed is an erratum (N2). Not blocking: it is untested and blocks either way.
3. Quoted `"none"` / `""`: these are already decided by "one layer of quotes stripped": `"none"` is ok and `""` gives "no value". Not blocking: it is only untested (N3).

## Adversarial edges (not in the AC table)
- A malformed `autonomous_pauses` value and a missing/malformed table at the same time: which row wins, or do both appear?
- Unknown policy combined with `plan_review` missing: is the order unknown row, then `plan_review=pass (got: <missing>)`?
- `plan_review_concerns` as a block sequence (§2.1b says it reads as empty, so (b)): untested.
- A pause table with a duplicate id or a CRLF line ending.

## Checklist
- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS
- AC16: PASS
- AC17: PASS

## Findings
- N1 MINOR: in §2.3 and AC15, move `p1` from "unknown id" to "malformed". Add `("[p1]", "malformed")` to _PAUSE_CASES so AC10 parity pins it, and drop the either-or test.
- N2 MINOR: in §2.3, move "block sequence" from the malformed bullet to the "no value" bullet.
- N3 MINOR: add `'"none"'` (ok) and `'""'` (no value) to _PAUSE_CASES.
- N4 MINOR: four wording drifts. AC17 is defined only in §3; add it to the §4 list. AC14 says "-> pass" where it means "no further row" (the unknown row still soft-blocks, as the test expects). §2.2 "also requires §2.3" should say the pause row is emitted once (the test asserts count == 1). The (d) row text and the malformed/unknown row texts are not quoted in §2.1; they appear only in §2.3.
- N5 MINOR: add an assertion that build-state.yaml bytes are unchanged after a soft block (the resumable-state claim in §2.1).

No MAJOR findings. The minors can be folded into GREEN's spec touch-up or a follow-up. They do not block GREEN.

VERDICT: ACCEPT
