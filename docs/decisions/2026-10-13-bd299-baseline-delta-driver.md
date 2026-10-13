---
red_tests:
  - tests/test_bd299_baseline_delta_driver.py
  - engine_py/tests/test_gh1338_corpus_parity_gate.py::test_ac26_exit_code_ladder_delta_fail_beats_corpus_divergence
  - engine_py/tests/test_gh1338_corpus_parity_gate.py::test_ac38_exit_ladder_blocked_always_nonzero_delta_still_beats_it
sibling_tests:
  - engine_py/tests/test_gh1740_corpus_scope_derived.py
  - tests/test_bd299_wiring_shield.py
paths:
  - baseline_delta_gate.py
  - engine_py/bytedigger_engine/workflows/_baseline_delta.py
  - scripts/baseline_delta_replay.py
---
# Spec lot 299 (bd#299, Refs #89) — baseline-delta driver: one env name, visible failures, replay harness

Worktree: /Users/guy/.hal-worktrees/wt-n299 (branch lot-299). Deterministic only, no model calls, no network in tests.

## S1. One env name
`baseline_delta_gate.py` main() must read `HAL_BASELINE_DELTA_ENFORCE` (the name registered in
`engine_py/bytedigger_engine/flags_catalog.py`), NOT `HAL_BASELINE_DELTA_GATE_ENFORCE`. The old name is read nowhere
(no alias, no fallback). Exit ladder unchanged: delta FAIL + enforce=1 -> exit 4; BLOCKED precondition -> 5; else 0.
Existing tests that use the old name (`engine_py/tests/test_gh1338_corpus_parity_gate.py` lines ~176,1285,1357-1358,2279,2305,2318;
`engine_py/tests/test_gh1740_corpus_scope_derived.py` ~116) MUST be migrated to the new name by the RED author.
Add a test: with `HAL_BASELINE_DELTA_GATE_ENFORCE=1` only, a delta FAIL exits 0 (old name is dead); with
`HAL_BASELINE_DELTA_ENFORCE=1` it exits 4. Add a catalog-vs-driver parity test: every `HAL_*_ENFORCE` env name the
driver source reads via os.environ is a key in `flags_catalog.py` CATALOG (parse the driver with ast).

Repo scan: no tracked file other than this decision doc and test files contains the string `HAL_BASELINE_DELTA_GATE_ENFORCE`
(driver, comments, catalog, docs, scripts all clean). The catalog AST walker resolves module-level string constants and
covers `os.environ.get`, `os.environ[...]`, `os.getenv`.

## S2. A swallowed driver failure stays visible
In `engine_py/bytedigger_engine/workflows/_baseline_delta.py::run_baseline_delta_gate`:
- returncode 2 (already emits stderr) — keep; additionally the event carries `returncode` and `stdout_tail` (last 2000 chars).
- Any other nonzero returncode that is not a clean verdict (e.g. 1 = Python traceback) and an unparseable last stdout line
  currently return `skipped: parse_error` WITHOUT an event. Must emit `baseline_delta_gate_skipped` with
  `reason: "parse_error"`, `returncode`, `stderr` tail, `stdout_tail`. (Exit 4/5 with valid JSON stay verdict paths, unchanged.)
- `exec_error` event carries the exception text as `detail`.
- Precise ladder: rc==2 -> `driver_error`. rc==1, or any rc not in {0,4,5} and !=2 -> `parse_error` ALWAYS, even if the
  last stdout line is valid JSON. rc in {0,4,5} with an unparseable last line -> `parse_error`. rc in {0,4,5} with valid
  JSON emits NO `baseline_delta_gate_skipped` event (regression shield). rc 4/5 with an unparseable last line, and rc 0 with
  empty stdout, -> `parse_error`. A subprocess TimeoutExpired -> `exec_error` event with `detail`. `stderr` and `stdout_tail` are each the last 2000 chars.

## S3. Replay harness `scripts/baseline_delta_replay.py` (stdlib only)
CLI: `python3 scripts/baseline_delta_replay.py --cases <cases.json> [--out table.md] [--json out.json]`.
`cases.json` = list of `{"id": str, "results": <path to pytest output text>, "base_fails": [<node ids>] (optional),
"expect": "PASS"|"FAIL"|"BLOCKED" (optional), "seeded": bool (optional)}`.
For each case it runs `baseline_delta_gate.py --results <results> --suite pytest --require-corpus-parity
--baseline <tmp file with base_fails>` (exit codes 0/4/5 are verdicts; 2 or no JSON = "ERROR") with cwd = the repo
root, parses the last stdout JSON line, and records verdict, delta_verdict, new_fails, blocked_by, corpus_scope_reason.
A case is a **false block** when `expect` is "PASS" and the verdict is not PASS (FAIL or BLOCKED); a seeded case is
**caught** when verdict == its expect. Output: a markdown table (id, verdict, expect, false_block yes/no, caught) and a
summary line `PASS=<n> FAIL=<n> BLOCKED=<n> ERROR=<n> false_blocks=<n> seeded_caught=<k>/<m>`; exit 0 always unless
cases.json is unreadable (exit 2). `--json` writes `{"rows":[...],"summary":{...}}`; row keys: id, verdict, delta_verdict, new_fails, blocked_by,
corpus_scope_reason, expect, seeded, false_block, caught. Missing results file, driver rc 2, or unparseable stdout ->
row verdict `ERROR` (process exit stays 0). `base_fails` containing the failing id -> verdict PASS. Table header is exactly
`| id | verdict | expect | false_block | caught |`; tests parse rows by the id column. Summary `seeded_caught=k/m` is exact.
Offline determinism: the replay always passes `--base-ref <ref>` to the driver; default `HEAD` (a non-remote ref, so no
`git ls-remote`, no E_REMOTE_UNREACHABLE/E_STALE_REMOTE_REF), overridable with the CLI option `--base-ref`. Tests must pass
with no network (assert the driver argv contains `--base-ref HEAD` by default, e.g. via a stubbed/monkeypatched subprocess
or a fake `git` on PATH that fails on `ls-remote`).
Pure function `summarize(rows)` importable (module has `main(argv)` and
`if __name__ == "__main__"`).

## RED tests (new file `tests/test_bd299_baseline_delta_driver.py`, plus the migrated old tests)
1. S1 old-name-dead / new-name-exit-4 (subprocess on the driver with a synthetic pytest output file that has one
   `FAILED x/y.py::t - boom` line and a `1 failed, 2 passed in 0.1s` summary; empty baseline file via `--baseline`).
2. S1 catalog parity (ast).
3. S2 via calling `run_baseline_delta_gate` with a stub script (`HAL_BASELINE_DELTA_BIN` through a cfg stub having
   `gate_enabled`, `flag`, `path`) that: exits 1 with a traceback on stderr -> event `parse_error` with returncode 1 and
   stderr tail; exits 0 printing garbage -> `parse_error` event; exits 2 -> `driver_error` event with returncode.
4. S3 seeded regression: results has a FAILED id absent from base_fails, expect FAIL -> verdict FAIL, new_fails lists it,
   seeded caught. Seeded parity mismatch: results output of a run whose summary is absent/incoherent so the driver gives
   BLOCKED under --require-corpus-parity (use the driver's own BLOCKED trigger: a results file with no summary line ->
   E_RESULTS_UNPARSEABLE or E_EMPTY_RUN; read `lib/corpus_parity.py` for the exact code), expect BLOCKED -> caught.
   Clean case (all passed, summary present) expect PASS -> verdict PASS or, if the corpus precondition blocks in the
   test sandbox, record exactly what the driver returns and assert the table marks it a false block (false_block yes).
5. S3 `summarize()` arithmetic and the summary-line format.
Tests must FAIL now for the right reason (old env name / missing events / missing script) and not by import error of
unrelated modules. Do not edit non-test files.
