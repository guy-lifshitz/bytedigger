# bd#243 — GREEN cannot start without an approving gate verdict on the current spec revision

**Status: r1 DRAFT** · **Tier:** 2 (engine prod `.py`: one new module + one call site; Option D: RED, Opus gate, GREEN) ·
**Class:** process-order enforcement (SYSTEMATIC) ·
**Chokepoint:** the pre-commit enforcement layer (`precommit_enforce.main`), the one place every
commit of a lot passes through, before a non-test source file can enter history.
**Source:** issue #243 (observed in #242 / bd#212: gate rejected r1 and r2, spec revised to v3,
implementation written with no verdict on v3).

## 1. Problem, measured on `2132ba0`

1. The order "gate APPROVES the current spec revision, then GREEN starts" is convention only.
   `grep -n "gate-r" engine_py/bytedigger_engine/precommit_enforce.py engine_py/bytedigger_engine/precommit_lints.py hooks/ githooks/` -> 0 hits.
2. The engine's own Phase 5 already orders `gate_on_validation` before GREEN inside one engine
   run; the hole is the lot flow outside it (spec file + `docs/decisions/<stem>-gate-rN.md` written by
   a gate agent, GREEN written by a different agent).
3. Premises of the issue that were checked live:
   - `docs/decisions/*-gate-r*.md` exists and is the real naming (hundreds of files). Verdict is the last
     line, spelled both `VERDICT: APPROVE` / `VERDICT: APPROVED` and `REJECT` / `REJECTED`.
   - Existing gate docs carry NO spec-revision binding (they say "spec r4" in prose). A revision
     label in prose cannot be compared deterministically, so this lot binds by content hash using the
     existing `<!-- verdict-anchor spec: <path> sha256:<hex> -->` block format
     (`verdict_verify.ANCHOR_RE` / `ANCHOR_LINE_RE`).
   - There is no "bypass log" in the repo today; this lot creates one (section 2.6).

## 2. Design

New stdlib-only module `engine_py/bytedigger_engine/green_entry_guard.py`, public
`check(root: str, staged: list[str], env: Mapping[str, str]) -> list[str]` returning refusal lines
(empty = allowed). It makes no model call. `precommit_enforce.main` calls it once, after the
registry pre-pass and `repo_root()`, before the lint plan; non-empty result -> print lines, return 1.

### 2.1 When it applies (GREEN entry)

The guard applies only when BOTH hold, else returns `[]`:
- (a) the staged paths contain at least one **source** path: not a test file
  (`precommit_lints.is_test_file` / `is_ts_test_file`), not under `docs/` or any `tests/` or
  `__tests__/` directory, not `*.md`;
- (b) the lot has at least one **lot spec**.

**Lot spec** = a path `docs/decisions/<stem>.md` that is ADDED on this branch relative to the
merge-base with the base ref (`origin/main`, else `main`; neither resolvable -> no lot spec ->
`[]`), i.e. `git diff --cached --name-status --diff-filter=A <merge-base>` lists it. Names ending
`-gate-rN.md` or `-escalation.md` are not specs.

### 2.2 Per-spec decision

For each lot spec `S` (stem `<stem>`), in order:
1. Escalation marker: file `docs/decisions/<stem>-escalation.md` exists and contains a line
   matching `^ESCALATION:\s*\S` -> `S` passes (reason recorded in the bypass log, 2.6).
2. Gate docs = files matching exactly `docs/decisions/<stem>-gate-r<N>.md`, `N` a positive integer;
   the newest is the highest `N` (numeric, not lexical: r10 > r9). None -> refusal `E_GREEN_GATE_MISSING`.
3. Verdict of the newest gate doc = the LAST line matching `^VERDICT:\s*(APPROVED?|REJECT(ED)?)\s*$`
   (case-insensitive, CRLF tolerated). No such line -> `E_GREEN_GATE_UNREADABLE` (fail closed).
   `REJECT*` -> `E_GREEN_GATE_REJECTED`.
4. Binding: the gate doc must carry a verdict-anchor line `spec: docs/decisions/<stem>.md sha256:<hex>`
   whose hash equals the sha256 of the spec's bytes on disk now. No anchor, or a different hash ->
   `E_GREEN_GATE_STALE` (a stale verdict counts as no verdict). Check order: 3 before 4 (a REJECTED
   newest verdict reports REJECTED even when stale).
5. APPROVE* + matching anchor -> `S` passes.

A refusal line is `<CODE>: spec=<S> gate=<newest gate doc or -> detail=<one line>`; one line per
failing spec; every failing spec is reported (no early exit).

### 2.3 Fail-closed rules

Unreadable spec/gate file, `git` failure while listing, or any exception inside the guard after (a)
is true -> `E_GREEN_GATE_UNREADABLE` refusal (never silent allow). The only silent allows are the
documented non-application cases in 2.1 (no source path; no resolvable base; no lot spec).

### 2.4 Error codes

`E_GREEN_GATE_MISSING`, `E_GREEN_GATE_REJECTED`, `E_GREEN_GATE_STALE`, `E_GREEN_GATE_UNREADABLE`,
`E_GREEN_GATE_BYPASS_NO_REASON`: each added to `error_codes.py` and both `ERROR_CODES.md` copies
(`engine_py/ERROR_CODES.md`, `engine_py/bytedigger_engine/ERROR_CODES.md`), in the existing format.

### 2.5 Kill switch

`HAL_GREEN_GATE_GUARD=0` skips the guard only when `HAL_GREEN_GATE_BYPASS_REASON` is non-blank; with
a blank/missing reason the guard refuses with `E_GREEN_GATE_BYPASS_NO_REASON` (it does not run the
checks either). Both vars are added to `flags_catalog.py`; `HAL_GREEN_GATE_GUARD` carries `owner`
and `provenance: "introduced: bd#243 ..."` (passes `scripts/flag_owner_lint.py`).
Reverting the lot removes the check entirely.

### 2.6 Bypass log

Every escalation-marker pass and every kill-switch skip appends ONE JSON line
`{"ts": <iso utc>, "kind": "escalation"|"kill_switch", "spec": <S or null>, "reason": <text>}` to
`<git-common-dir>/bytedigger/bypass.log` (directory created on demand; untracked). A failure to
write the log refuses with `E_GREEN_GATE_UNREADABLE` (a bypass that cannot be recorded is not allowed).

## 3. Acceptance criteria

| AC | Fixture (temp git repo, base branch `main`, lot branch, stage a source file) | Expected |
|---|---|---|
| AC1 | spec added; `gate-r1` REJECTED + anchor of an OLD spec; `gate-r2` REJECTED; spec then edited | `E_GREEN_GATE_REJECTED` (newest is r2) |
| AC2 | spec; newest gate `APPROVED` with anchor == current spec sha | `[]` |
| AC3 | spec; `gate-r1` APPROVED with anchor of the spec BEFORE an edit | `E_GREEN_GATE_STALE` |
| AC4 | spec; newest gate APPROVED but no anchor block | `E_GREEN_GATE_STALE` |
| AC5 | spec edited after REJECTED r2, plus `<stem>-escalation.md` with `ESCALATION: owner accepted risk` | `[]`; bypass log gains one `escalation` line |
| AC6 | escalation file exists but marker line is blank (`ESCALATION:`) | falls through to the gate-doc rules (-> REJECTED/STALE/MISSING as applicable) |
| AC7 | spec, no gate doc at all | `E_GREEN_GATE_MISSING` |
| AC8 | gate docs `r2` APPROVED+current anchor and `r10` REJECTED | `E_GREEN_GATE_REJECTED` (r10 is newest) |
| AC9 | newest gate doc has no `VERDICT:` line | `E_GREEN_GATE_UNREADABLE` |
| AC10 | only test files / docs staged with a REJECTED lot | `[]` (not GREEN entry) |
| AC11 | lot with no added spec doc (only an edit of an old spec) | `[]` |
| AC12 | `HAL_GREEN_GATE_GUARD=0` + non-blank reason, REJECTED lot | `[]`; bypass log gains one `kill_switch` line |
| AC13 | `HAL_GREEN_GATE_GUARD=0`, reason blank or missing | `E_GREEN_GATE_BYPASS_NO_REASON` |
| AC14 | two lot specs, one approved, one stale | exactly one refusal line, naming the stale one |
| AC15 | end to end: real `git commit` with the installed hook in a fixture repo, REJECTED lot, staged source | commit refused (non-zero), no new commit; same repo with APPROVED+anchor -> commit succeeds |
| AC16 | CRLF verdict line `VERDICT: APPROVE\r` with a matching anchor | `[]` |
| AC17 | catalog/doc parity: the five codes exist in `error_codes.py` and both `ERROR_CODES.md`; `flags_catalog.py` has both vars and `scripts/flag_owner_lint.py` exits 0 | pass |

## 4. Out of scope

- Writing the anchor block automatically in the gate agent prompts (host side); until they emit it, a
  lot must paste the block into the gate doc or record an escalation marker.
- CI-side re-check of PR history; only the commit-time chokepoint is added here.
- The engine-internal Phase 5 ordering (already enforced by `gate_on_validation`).
