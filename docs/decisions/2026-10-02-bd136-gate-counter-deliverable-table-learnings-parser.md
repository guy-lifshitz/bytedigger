# bd#136 items 4–6 — per-phase gate counter, declarative deliverable table, one learnings parser

**Status: FROZEN Rev 3** (gate r1 REJECT M1–M4 + m1–m10 → §R2; gate r2 REJECT M-r2-1 + minors → §R3) · **Class:** SYSTEMATIC (duplication → one source of truth) ·
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

In: items 4–6 and item 7 (the bash 5.5 crash). Out: the phase-6 checks (bash soft "unfixed findings" vs TS hard "Boy Scout"
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
  strings. No bun test pins the old TS 5.1-unreadable, 5.2 or 5.5 strings.
- `log_red` on an unreadable log follows bash: the reason is "contains no failures" (the TS-only "unreadable" entry goes away).
- **Table unreadable / missing** (both backends): soft block with the single entry
  `deliverable table unreadable: <abs path>` (it then goes through loop prevention). An unknown
  `kind` is a soft-block entry `deliverable table: unknown kind '<kind>' (line <n>)`. A phase
  with no rows passes.
- **Single source.** No string `plan_review`, `phase_5_implement`, `phase_52a_gherkin`,
  `test_integrity_check` or `learnings-raw.md` remains in
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
- **A2-note**: the fixture already carries `gate_block_phase`, so A2 passes today (a GUARD for C2).
- **A10** Regression guards: existing `tests/build-gate.bats`, `tests/learning-store*.bats` and
  `tests/test_worker_deliverables.py` stay green.

## §7 Limits

- The table covers soft "field / artifact present" checks only. Phase 6 and all hard checks stay
  code, because they carry side effects (state writes) or different severity.
- TAB-separated: an editor that turns tabs into spaces breaks the row. The backends then report
  an unknown kind or a missing field. It fails visibly, never silently.

## §R2 Rev 2 (gate r1: REJECT, findings M1–M4 and m1–m10, all folded in)

- **M1** The literal `contains no failures` is the `log_red` kind template, so it may stay in code.
  It is removed from the §4/A8 forbidden list. A8 forbids data only (field names, file paths).
  New pin: the literal appears exactly once per backend.
- **M2: the whole table is validated on every load,** before rows are filtered by phase.
  Any violation adds an entry to **every** phase that reads the table (deliverable phases
  4.5, 5, 5.1, 5.2, 5.5, 7), in physical file order. A line is valid only if all of these hold:
  - it is exactly the TAB-split fields for its kind (`field_eq` 4, `field_set` 3, `log_red` 3,
    `scratch_file` 3);
  - no field is empty;
  - the phase matches `^[0-9]+(\.[0-9]+)?$`;
  - the kind is known.

  Entries (line = 1-based physical line, comments and blanks counted):
  - `deliverable table: unknown kind '<kind>' (line <n>)`
  - `deliverable table: malformed row (line <n>)`, which covers a wrong field count, an empty
    field, a bad phase, or tabs turned into spaces.

  Before splitting, a trailing `\r` is stripped, a last line without a newline is still read, and
  consecutive TABs give an empty field, which makes the row malformed. bash cannot use the
  `IFS=$'\t' read` field-merging and must split it by hand. Add `.gitattributes`:
  `*.tsv text eol=lf`. RED: a row with spaces instead of tabs, a row missing an argument, and an
  unknown kind on another phase's row. bash and TS output must be byte-identical.
- **M3 A9b (behavioral single parser).** Build a temp `scripts/` with both store scripts and a
  stub `learnings_parse.py` that prints fixed output (for example `2` / `stubcat\x1fstub lesson`).
  Both backends' `extract` must store exactly the stub entries and write
  `learnings_parse_errors: 2`. Stub exits 1 → file backend writes `learnings_extracted: 0` and
  no `learnings_parse_errors`; sqlite keeps its error-slug path. The file backend passes the CLI
  output to its storage python on stdin (or as a temp file). It never re-parses.
- **M4 A11 CI wiring.** The `manifests` job gets a step "gate counter / deliverable table /
  learnings parser (bd#136)". The step has `BD_REQUIRE_SQLITE: "1"`, installs sqlite3 if it is
  missing and installs bun the same pinned and checksummed way as the `pytest` job (same
  `BUN_VERSION`/sha256). It then runs `python -m pytest tests/test_bd136_gate_counter_table_parser.py -q`.
  A test pins that step (yaml load, the env value, and `bun` installed before the pytest run).
  The RED docstring is corrected.
- **m1 Lookup.** The table is found relative to the script only.
  - bash: `$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/phase-deliverables.tsv`
  - TS: `fileURLToPath(new URL("../phase-deliverables.tsv", import.meta.url))`
  - Never from cwd or `CLAUDE_PLUGIN_ROOT`. TS reads the table lazily, inside the check, and never
    at module load.
  - RED: a plugin copy under a path that contains a space.
- **m2** "Trimmed value" means bash `yaml_get` / TS `readStateField(...).trim()`. With values
  that contain inner spaces, `(got: …)` keeps the spaces, which differs from old bash `tr -d ' '`
  and is accepted. A `scratch_file` path keeps its spaces.
- **m4 Counter.**
  - "Numeric" means `^[0-9]+$`. Anything else counts as 0 in both backends.
  - An empty `gate_block_phase:` value counts as absent (C1).
  - The stored phase is canonicalised before the comparison.
- **m5** TS canonicalises the alias (`52`→`5.2` …) before `checkDeliverables` and before loop
  prevention. RED: TS with `current_phase: 52` gives the same verdict as `5.2`, and C2 holds
  across `52`/`5.2`.
- **m6** The rewrite uses a unique temp name (`build-state.yaml.<pid>.<rand>.tmp`) and then a
  rename. Documented limit: shadow mode runs both backends, so the counter grows by 2 per gate
  call.
- **m8** The parser CLI writes UTF-8 to stdout whatever the locale
  (`sys.stdout.reconfigure(encoding="utf-8")`). RED runs it without `PYTHONUTF8`, with
  `LC_ALL=C`.
- **m9 RED.**
  - Table present but unreadable (chmod 000, skipped as root) gives the same entry as missing.
  - A phase with no rows (an added `8` row is absent) passes.
  - State not writable: the soft block is still emitted, plus a WARN on stderr.
- **m10** bash `block()` JSON-escapes `\` and `"` in the reason, so bash and TS stay
  byte-identical for an unknown kind that contains a quote. RED: a kind `a"b`.
- **m7** A10 list adds `tests/test_bd89_p2a_phases_1_4_dropped.py`, `tests/gate-dispatcher.bats`,
  and the bun suites `build-phase-gate*.test.ts`, `worker-deliverables.test.ts` and
  `post-review-gate.test.ts`. GREEN keeps `loopPreventionCLI(statePath, phase)` exported.

## §R3 Rev 3 (gate r2: REJECT on M-r2-1, minors folded in; §R3 wins over §R2 and earlier sections)

- **M-r2-1 The TS module loads from a path that contains spaces.** `loadSemanticSkipPhrases`
  builds its default path with `fileURLToPath(new URL("./lib/semantic-skip-phrases.json", import.meta.url))`
  in place of `new URL(...).pathname`, which keeps `%20`. The `BYTEDIGGER_PHRASES_PATH` override
  stays as it is. `config-reader.ts`'s fallback gets the same fix. This is the producing path for
  `test_m1_plugin_copy_under_path_with_space_finds_table[ts]`.
- **m-r2-1b** The A8 data list adds `opus_validation` and `review_complete`. Neither may remain in
  `build-gate.sh` / `build-phase-gate.ts` outside comments. The TS `disablePhase7` keeps working: it
  skips the phase-7 `field_eq` rows, recognised by phase and kind, not by field name.
- **m-r2-3 Order and precedence.**
  - Validation runs line by line in physical order:
    1. a line with fewer than 2 TAB fields, an empty field, or a bad phase is `malformed row`;
    2. otherwise an unknown kind is `unknown kind`;
    3. otherwise a wrong arity for that kind is `malformed row`.
  - Each line gives at most one validation entry.
  - All validation entries come first. The failing entries of the phase's valid rows follow, in file
    order.
- **m-r2-4 Definitions.** After `\r` is stripped, a *blank* line is empty or whitespace only, and a
  *comment* is a line whose first non-whitespace character is `#`. Both backends apply the same rule.
- **m-r2-5 RED.** A decoy test sets `CLAUDE_PLUGIN_ROOT` to a dir whose `scripts/phase-deliverables.tsv`
  differs from the script-adjacent one. The verdict must follow the script-adjacent table.
- **m-r2-6 RED.** Bash canonicalises a stored phase alias too (`gate_block_phase: 52`, P=5.2 → C2).
  Bash and TS share the alias map `45 51 52 53 55` → `4.5 5.1 5.2 5.3 5.5`.
- **m-r2-8 RED.** If `build-red-output.log` exists but cannot be read (chmod 000, skipped as root),
  both backends emit `build-red-output.log contains no failures (tests must be RED)`.
- **m-r2-2** The m6 unique temp name is accepted as **spec-only**. A test cannot provoke the race
  reliably, and a grep pin would catch only one spelling. `test_m6_guard_*` stays as a weak guard
  that no `build-state.yaml.tmp` is left behind. GREEN still builds the name from pid and random,
  per §R2 m6.
- **m-r2-7 Drift.**
  - §7's "unknown kind or a missing field" now reads `malformed row` (§R2 M2).
  - The §R2 m7 path is `engine_py/tests/test_bd89_p2a_phases_1_4_dropped.py`.
  - The §4 table block is shown aligned with spaces for reading only. The shipped file uses single TABs.
- **GREEN constraints, from the gate:**
  - the manifests CI step has no `working-directory:` (`test_version_parity.py` AC10);
  - bash stays 3.2-safe (no `${x,,}`, no associative arrays, no `mapfile`), because test_bd89 AC10
    runs `/bin/bash`.
