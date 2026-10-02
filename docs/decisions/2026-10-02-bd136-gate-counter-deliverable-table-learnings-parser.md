# bd#136 items 4–6 — per-phase gate counter, declarative deliverable table, one learnings parser

**Status: DRAFT Rev 1** · **Class:** SYSTEMATIC (duplication → one source of truth) ·
**Builds on:** main `7a4c7cd` (#191 closed items 1–3). Deterministic only, stdlib only, no model.

## §1 Problem (measured on `7a4c7cd`)

4. **Global counter.** `loop_prevention` (`scripts/build-gate.sh:357-382`) and
   `loopPreventionCLI` (`scripts/ts/build-phase-gate.ts:792-841`) keep one `gate_block_counter`
   for the whole build and never reset it. After 3 soft blocks anywhere (say in 4.5), every
   later soft block (5.1, 5.2, 7, …) is bypassed on its first hit. `gate_bypass*` lines are
   appended on every bypass, so they pile up. Bash also rewrites the counter non-atomically
   (`grep -v > tmp && mv`, then `>>` append).
5. **Hand-written deliverable checks, twice.** Soft per-phase checks (4.5, 5, 5.1, 5.2, 5.5, 7)
   exist in bash `gate_phase_*` and in TS `checkPhase*`, and the two copies have already drifted.
   TS 5.2 says `opus_validation=pass`; bash says `opus_validation=pass (got: x)`. TS 5.5 drops
   the trailing `"; "`. Shadow mode (`gate-dispatcher.sh`) logs these as mismatches.
6. **Two learnings parsers.** `scripts/learning-store.sh` (file backend, embedded python) and
   `scripts/learning-store-sqlite.sh` (embedded python) each carry the
   `- [category] --- lesson` regex, the skip rules (blank / `#` / code fence), category
   sanitising and parse-error counting. They differ on decoding: file uses `errors='replace'`,
   sqlite uses strict utf-8 and turns bad bytes into a failed extract.

7. **Found by the RED guard: bash 5.5 crashes.** `gate_phase_55` runs
   `grep "^assertion_gaming_detected:" … | sed …` under `set -euo pipefail` without `|| true`.
   When the key is absent (the normal case), the script exits 1 with empty stdout. Under
   SubagentStop that is a non-zero exit with no verdict, and `gate-dispatcher.sh` shadow mode turns
   it into a hard block. GREEN fixes it: an absent key means "not detected". It is pinned by
   `test_guard_satisfied_state_passes[5.5-bash]`, which is RED today.

## §2 Scope

In: items 4–6. Out: the phase-6 checks (bash soft "unfixed findings" vs TS hard "Boy Scout"
plus state writes). They are different logic, not table rows, and stay code. Hard checks
(5.3, 5.5 gaming, 6 skipped/post_review_gate, the global downgrade) stay code in both backends.

## §3 Item 4 — per-phase counter (both backends, same rules)

State lines: `gate_block_counter: <n>` and new `gate_block_phase: <phase>`.

| Row | Stored state when a soft block reaches loop prevention for phase P | New counter |
|---|---|---|
| C1 | `gate_block_phase` absent (legacy / in-flight build) | stored count + 1 (no reset; keeps current bats/bun fixtures valid) |
| C2 | `gate_block_phase` == P | stored count + 1 |
| C3 | `gate_block_phase` present and != P | **1** (reset) |
| C4 | counter missing or non-numeric | treated as 0 |

- P is the canonical phase (`45`→`4.5`, `51`→`5.1`, … same alias map as TS dispatch). Bash only
  ever sees canonical values, since its `case` has no aliases.
- One atomic rewrite (tmp + rename) drops every existing `gate_block_counter`,
  `gate_block_phase`, `gate_bypass` and `gate_bypass_phase` line and appends
  `gate_block_counter: N` and `gate_block_phase: P`. If N > 3 it also appends `gate_bypass: true`
  and `gate_bypass_phase: P`. Each key then appears at most once.
- Bypass threshold is unchanged: N > 3 → exit 0 (bypass), otherwise the soft block is emitted.
- Write failure is best-effort, as today: a warning on stderr, and the verdict is still emitted.

## §4 Item 5 — declarative table `scripts/phase-deliverables.tsv`

One file, read by both backends at runtime. Format: UTF-8, `#` comments and blank lines ignored,
fields TAB-separated. Columns: `phase  kind  arg  [arg2]`. Rows are evaluated in file order, and
each failing row adds one reason entry. Entries are joined as `<entry>; ` (trailing separator
kept, which is the existing bash `printf '%s; '` form).

| kind | args | fails when | reason entry (exact) |
|---|---|---|---|
| `field_eq` | field, expected | trimmed value != expected | `<field>=<expected> (got: <value or <missing>>)` |
| `field_set` | field | trimmed value empty | `<field> has no value` |
| `log_red` | file (relative to CWD) | missing or 0 bytes / no `FAIL\|ERROR\|FAILED\|not ok` | `missing artifact: <file>` / `<file> contains no failures (tests must be RED)` |
| `scratch_file` | path under `scratchpad_dir` | `scratchpad_dir` set and file missing or 0 bytes | `missing deliverable: <scratchpad_dir>/<path>` |

Initial content (it must reproduce today's **bash** reasons byte-for-byte):

```
4.5  field_eq      plan_review          pass
5    field_eq      plan_review          pass
5    field_eq      phase_5_implement    complete
5    field_eq      opus_validation      pass
5.1  log_red       build-red-output.log
5.2  field_eq      opus_validation      pass
5.2  field_eq      phase_52a_gherkin    complete
5.5  field_set     test_integrity_check
7    field_eq      review_complete      pass
7    scratch_file  reviews/learnings-raw.md
```

- **Bash**: `gate_phase_45/5/51/52` are deleted, and the dispatcher calls `check_deliverables P`
  (pure bash `while IFS=$'\t' read`, no python). `gate_phase_55` keeps its hard gaming check and
  then calls `check_deliverables 5.5`. `gate_phase_7` keeps the TRIVIAL skip and the
  learnings_extracted WARN, and replaces its two hand checks with `check_deliverables 7`.
- **TS**: `checkPhase45/5/51/52` collapse into one `checkDeliverables(cwd, phase)`. `checkPhase55`
  and `checkPhase7` keep their code-only parts (hard check, `disablePhase7`, TRIVIAL) and call it.
  TS reasons then equal bash reasons for all six phases. This deliberately changes TS 5.2 and 5.5
  strings, and the RED commit updates the bun pins.
- `log_red` on an unreadable log follows bash: the reason is "contains no failures" (the TS-only "unreadable" entry goes away).
- **Table unreadable / missing** (both backends): soft block with the single entry
  `deliverable table unreadable: <abs path>` (it then goes through loop prevention). An unknown
  `kind` is a soft-block entry `deliverable table: unknown kind '<kind>' (line <n>)`. A phase
  with no rows passes.
- **Single source.** No string `plan_review`, `phase_5_implement`, `phase_52a_gherkin`,
  `test_integrity_check`, `learnings-raw.md` or `contains no failures` remains in
  `build-gate.sh` / `build-phase-gate.ts` outside comments, except where a hard check or a
  state write still needs it (none of these names does today).
- The table ships with the plugin. It sits in `scripts/` next to the gates, so every existing
  copy/package path that carries `scripts/` carries it too.

## §5 Item 6 — one parser `scripts/learnings_parse.py`

- Stdlib module: `parse(path) -> (entries: list[tuple[category, lesson]], parse_errors: int)`.
  It holds the only copy of the regex `^-\s+\[([^\]]+)\]\s+(?:---?|—)\s+(.+)$`, the skip rules
  (blank, `#…`, ```` ``` ````), the sanitising (lowercase, `[^a-z0-9]+`→`-`, strip `-`), the
  empty-category-is-an-error rule, and the decoding `encoding='utf-8', errors='replace'`
  (degrade, don't crash, which is the file backend's current behavior).
- CLI: `python3 learnings_parse.py <raw_md>` prints line 1 = parse_errors, then one
  `<category>\x1f<lesson>` per entry. If the file cannot be opened: message on stderr, exit 1.
- `learning-store-sqlite.sh` calls the CLI in place of its heredoc (same output contract it
  already consumes). `learning-store.sh` file backend calls the same CLI and keeps its own
  storage python (tags, append, trim), which now reads entries from that output instead of
  parsing. Both locate the module as `$(dirname script)/learnings_parse.py`.
- Neither shell script contains the regex fragment `(?:---?|` or `re.sub(r'[^a-z0-9]+'` any more.
- Behavior change: sqlite on invalid utf-8 now stores the replaced text and no longer fails the
  extract. Everything else is unchanged, including `learnings_extracted`,
  `learnings_parse_errors` and the WARN line.

## §6 Acceptance criteria (RED pins, pytest, subprocess on real scripts; TS via `bun run`)

- **A1** (C3) For both backends, state `current_phase: 5.2`, `gate_block_counter: 3`,
  `gate_block_phase: 4.5`, phase 5.2 missing a field → exit 2 (block), `gate_block_counter: 1`,
  `gate_block_phase: 5.2`.
- **A2** (C2) Same, but with `gate_block_phase: 5.2` → exit 0 (bypass), counter 4, exactly one
  `gate_bypass: true`.
- **A3** (C1) Legacy state without `gate_block_phase`, counter 3 → bypass (current behavior kept).
- **A4** Two bypasses in a row → every one of the four keys appears exactly once in the file.
- **A5** Byte parity: for each phase in {4.5, 5, 5.1, 5.2, 5.5, 7} and a failing fixture, bash
  stdout == TS stdout, and the exit codes are equal (5.5: the empty `test_integrity_check` path).
- **A6** Table drives behavior: a temp copy of the plugin with a row removed (or added) from
  `phase-deliverables.tsv` changes both backends' verdicts the same way, with no code edit.
- **A7** Missing table → both backends exit 2 with reason `deliverable table unreadable: …`.
  Unknown kind → the documented entry.
- **A8** Single-source pins of §4 and §5 (the grep lists).
- **A9** Parser: one corpus fixture (valid `---`/`--`/`—`, heading, fence, blank, malformed,
  empty-after-sanitise category, invalid utf-8 byte) → `learnings_parse.py` output pinned
  exactly. The file backend's category files and the sqlite DB rows (if `sqlite3` is present,
  else skip) contain the same (category, lesson) set, and both write the same
  `learnings_parse_errors`.
- **A2** note: the fixture already carries `gate_block_phase`, so A2 passes today (a GUARD for C2).
- **A10** Regression guards: existing `tests/build-gate.bats`, `tests/learning-store*.bats` and
  `tests/test_worker_deliverables.py` stay green.

## §7 Limits

- The table covers soft "field / artifact present" checks only. Phase 6 and all hard checks stay
  code, because they carry side effects (state writes) or different severity.
- TAB-separated: an editor that turns tabs into spaces breaks the row. The backends then report
  an unknown kind or a missing field. It fails visibly, never silently.
