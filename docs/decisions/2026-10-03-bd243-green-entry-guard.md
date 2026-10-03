# bd#243 — GREEN cannot start without an approving gate verdict on the current spec revision

**Status: r2 DRAFT (gate r1 REJECTED: 4 blocking + 8 advisory, folded below; see `2026-10-03-bd243-green-entry-guard-gate-r1.md`)** · **Tier:** 2 (engine prod `.py`: one new module + one call site; Option D: RED, Opus gate, GREEN) ·
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

New module (stdlib plus `bytedigger_engine.verdict_verify` for the anchor regexes; no third-party imports) `engine_py/bytedigger_engine/green_entry_guard.py`, public
`check(root: str, staged: list[str], env: Mapping[str, str]) -> list[str]` returning refusal lines
(empty = allowed). It makes no model call. `precommit_enforce.main` calls it once, after the
registry pre-pass and `repo_root()`, before `nothing_to_lint`; non-empty result -> print lines, return 1.

### 2.1 When it applies (GREEN entry)

Order inside `check`: (0) kill switch (2.5) first; then (a), (b), (c) below. Any failing condition
returns `[]` silently. `precommit_enforce.main` calls the guard BEFORE its `nothing_to_lint` early
return, so a commit staging only binary/unclassified source still reaches it.
- (a) the staged paths contain at least one **source** path: not a test file
  (`precommit_lints.is_test_file` / `is_ts_test_file`), not under a `docs/`, `tests/` or
  `__tests__/` directory at any depth, not `*.md`;
- (b) a base ref resolves: `origin/main` if `git rev-parse --verify` accepts it, else `main`, else
  none -> `[]`. A `git merge-base HEAD <base>` failure -> `[]` (no lot context, e.g. unrelated
  histories). HEAD equal to the base tip (committing on the base branch itself) yields no added
  spec, hence `[]`;
- (c) at least one **lot spec** exists.

**Lot spec** = a path `docs/decisions/<name>.md` reported as status `A` by
`git diff --cached --name-status --diff-filter=A --no-renames <merge-base>` (`--no-renames`, so a
`git mv`-ed spec still counts as added), minus excluded names. **Excluded** (not a spec):
`*-gate-r<N>.md`, `*-gate-verdict-r<N>.md`, `*-escalation.md`, `*-acceptance.md`, `*-inventory.md`,
`*-close-gate.md`, `*-post-mortem.md`, and any doc whose first 20 lines contain a line
`Gate-exempt: <non-blank reason>` (the exemption is appended to the bypass log as kind
`gate_exempt`). Paths are matched on the index content for the exemption line.

### 2.2 Per-spec decision

For each lot spec `S` (stem `<stem>`), in order:
1. Escalation marker: file `docs/decisions/<stem>-escalation.md` exists and contains a line
   matching `^ESCALATION:[ \t]*\S` (single-line match, no cross-line whitespace) -> `S` passes (reason recorded in the bypass log, 2.6).
2. Gate docs = the UNION of files matching exactly `docs/decisions/<stem>-gate-r<N>.md` and
   `docs/decisions/<key>-gate-r<N>.md`, where `<key>` is the first four dash-separated segments of
   `<stem>` (`2026-10-03-bd243-green-entry-guard` -> `2026-10-03-bd243`; the corpus names most gate
   docs `<date>-bdN-gate-rN.md`). `N` is a positive integer; the newest is the highest `N` across
   the union (numeric, not lexical: r10 > r9; on equal `N` the stem form wins). None -> refusal
   `E_GREEN_GATE_MISSING`. A stem of fewer than four segments uses the stem form only.
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
is true -> `E_GREEN_GATE_UNREADABLE` refusal (never silent allow). Unreadable means: a spec or gate file that cannot be read, a non-zero `git` exit while listing or
resolving after (a) is true (except the merge-base failure of 2.1(b)), or any exception.
The spec hash is computed from the file on disk, and so are gate docs; the lot-spec list comes
from the index. The only silent allows are the non-application cases in 2.1.

### 2.4 Error codes

`E_GREEN_GATE_MISSING`, `E_GREEN_GATE_REJECTED`, `E_GREEN_GATE_STALE`, `E_GREEN_GATE_UNREADABLE`,
`E_GREEN_GATE_BYPASS_NO_REASON`: each added to `error_codes.py` and both `ERROR_CODES.md` copies
(`engine_py/ERROR_CODES.md`, `engine_py/bytedigger_engine/ERROR_CODES.md`), in the existing format.

### 2.5 Kill switch

`HAL_GREEN_GATE_GUARD=0` skips the guard only when `HAL_GREEN_GATE_BYPASS_REASON` is non-blank; with
a blank/missing reason the guard refuses with `E_GREEN_GATE_BYPASS_NO_REASON` (it does not run the
checks either). Both vars are added to `flags_catalog.py`; `HAL_GREEN_GATE_GUARD` has `kind` `gate` (so the owner lint covers it) and carries `owner` and
`provenance: "introduced: bd#243 ..."`. The switch is evaluated first (2.1 step 0): with it on and a
reason present, EVERY commit skips the checks and one `kill_switch` line (`"spec": null`) is
logged; with it on and no reason, every commit is refused with `E_GREEN_GATE_BYPASS_NO_REASON`.
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

### 2.7 Touchpoints that must change with the guard (gate r1 blockers 1-2)

- `engine_py/tests/test_bd66_precommit_enforcement.py::_materialize_package_copy` additionally copies
  `green_entry_guard.py` and `verdict_verify.py` into the temp package, so bd66 AC6c/AC11 keep
  testing the real layer. `precommit_enforce` imports the guard at module level; the import is NOT
  wrapped (fail closed).
- `conformance/tree_scan_inventory.json`: new keys for each tree-scan call site in the guard
  (class `not-a-gate` is wrong for a gate input, so the guard avoids glob/listdir/walk entirely:
  gate docs are listed with `git ls-files` + `git diff --cached --name-only`, and that call site is
  registered with class `filters` only if the lint demands it; the implementer runs
  `test_bd94_engine_owned_paths.py::test_ac6_real_tree_passes_tree_scan_lint` and registers exactly
  what it reports, class `not-a-gate` with a note citing this section where the class definition
  allows it).

## 3b. Added acceptance criteria (gate r1 blocker 3 and advisories)

| AC | Fixture | Expected |
|---|---|---|
| AC18 | spec file unreadable (mode 000 or a directory in its place) | `E_GREEN_GATE_UNREADABLE` |
| AC19 | bypass log path made unwritable (a regular file where the `bytedigger` dir must go) on an escalation pass | refusal `E_GREEN_GATE_UNREADABLE`, no allow |
| AC20 | no `origin/main` and no `main` | `[]` |
| AC21 | both exist and differ (stale `origin/main`) | the lot specs are computed against `origin/main` |
| AC22 | staged on the base branch itself | `[]` |
| AC23 | gate docs named `<date>-bdN-gate-rN.md` for a spec `<date>-bdN-<slug>.md` | key form is honoured: REJECTED newest -> refused, APPROVED+anchor -> `[]` |
| AC24 | added decoys `-inventory.md`, `-acceptance.md`, `-close-gate.md`, `-gate-verdict-r1.md`, and a doc with `Gate-exempt: reason` | none is treated as a spec; exemption logged as `gate_exempt` |
| AC25 | spec `git mv`-ed (rename) on the branch | counted as added -> checked |
| AC26 | `docs/x/`, `tests/`, `__tests__/` directory paths with non-test file names staged on a REJECTED lot | `[]` |
| AC27 | kill switch on with a reason, docs-only commit | `[]` and one `kill_switch` line; with the switch on and no reason, the same docs-only commit gets `E_GREEN_GATE_BYPASS_NO_REASON` |
| AC28 | only binary/unclassified source staged (`nothing_to_lint` true) on a REJECTED lot, via the real hook | refused |
| AC29 | `ESCALATION:` line followed by text only on the NEXT line | not a marker |
| AC30 | bd66 sibling suite `test_bd66_precommit_enforcement.py` and `test_bd94_engine_owned_paths.py` | stay green |

## 4. Out of scope

- Writing the anchor block automatically in the gate agent prompts (host side); until they emit it, a
  lot must paste the block into the gate doc or record an escalation marker.
- CI-side re-check of PR history; only the commit-time chokepoint is added here.
- The engine-internal Phase 5 ordering (already enforced by `gate_on_validation`).
