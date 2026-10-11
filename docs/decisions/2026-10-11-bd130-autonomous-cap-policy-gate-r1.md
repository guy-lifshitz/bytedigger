# bd#130 gate round 1 — AUTONOMOUS cap policy (spec + RED)

Auditor: hal-gate-agent (continuation after clear-gate). Branch lot-130.

## Checklist
- AC1 PASS, AC2 FAIL, AC3 PASS, AC4 PASS, AC5 PASS, AC6 PASS
- AC7 PASS, AC8 PASS, AC9 FAIL, AC10 PASS, AC11 PASS, AC12 FAIL

## Must-fix
- M1: AC9 (SUPERVISED stdout == legacy for all AC1-AC6 fixtures) contradicts AC2
  (extra row (a) on SUPERVISED pass_with_concerns). RED special-cases the pwc
  fixtures to hide the clash. Restrict AC9 to `plan_review: pass` fixtures, or
  define legacy text as a subset of default text.
- M2: pass_with_concerns + COMPLEX + AUTONOMOUS unspecified; section 2.2 sits only
  in the `pass` branch, so the mode_override check can be skipped. Add AC + RED.
- M3: AC12 / section 2.4 underspecified. Only a missing table is tested; empty or
  malformed table, and when the table is read (not on a plain pass), undefined.
  AC12 title says "malformed" but text covers a missing file.
- M4: unknown policy value ("no other new rule runs") has no stated plan_review
  verdict (legacy?). AC8 test asserts only the unknown-value row and ROW_A absent.

## Verified in this round
- yaml_get (scripts/build-gate.sh:159) returns the raw top-level value: strips one
  layer of quotes and whitespace only. No trailing-comment stripping and no list
  parsing, so `[P1, P2]` arrives as the literal string and `none # x` is not
  `none`. The spec must state how autonomous_pauses and concerns are parsed
  (inline list only, no comments) and RED must pin it.
- Deliverables are driven by scripts/phase-deliverables.tsv (field_eq rows for
  4.5/5/5.2, opus_validation=pass at 5 and 5.2); checkDeliverables
  (build-phase-gate.ts:377) mirrors check_deliverables (build-gate.sh:179).
  Step 4 is reachable through both, via subprocess.

## Minors
- AC10 byte-compare lacks AC4 and AC12 fixtures; real shadow runner untouched.
- Empty autonomous_pauses and multi-failure row order untested.
- Section 3 doc changes (phase docs, SKILL.md, CHANGELOG) have no pinning test.
- Backstop "table still holds opus rows": only dropped rows tested, not a row
  with value fail or commented out; both rows dropped at once (one or two (c)?).

## RED adequacy
50 fail / 0 error at assert time, subprocess on the real UUT, no mocks. AC5 and
AC7 are covered only by guards that already pass on main. A stub hard-coding the
fixture row strings would pass.

## Adversarial edges not in the AC table
- Trailing comment or quoted `none` in autonomous_pauses; `[none]`, `none, P1`.
- `[ ]`, `[""]`, `[[]]` for concerns; block-sequence concerns (documented fail).
- Case/whitespace variants of mode, plan_review, policy (` legacy`, `Legacy`).
- Pause-id lookalikes: P01, p1, P11, duplicates.
- pass_with_concerns at phase 4.5; mode missing -> `(got mode: <missing>)`.
- legacy with a missing pause table; simultaneous a+b+d failures (row order).

VERDICT: REJECT
