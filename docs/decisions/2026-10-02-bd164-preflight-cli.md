# bd#164 — host-callable deterministic preflight CLI with a tree-state receipt

**Status: r2 (gate r1 REJECTED: 2 MAJOR + 10 MINOR applied, see `2026-10-02-bd164-gate-r1.md`)** · **Tier:** 3 (one new engine prod module + a 3-line dispatch in
`run.py`, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `preflight.run_preflight` — the one function that runs the fixed deterministic step
list and writes the receipt; `preflight.verify_receipt` — the one function a host calls to decide
whether a gate/reviewer may be spawned. The CLI and every host adapter call these and add only I/O.
**Side of the seam:** engine. HAL's replacement of `SYSTEM/cli/build/lot-preflight.ts` by a call to
this CLI is hal-v2 follow-up work (AC5), not this PR. `phases/` and `workflows/engine.py` are not
touched.
**Source:** bd#164; HAL reference `SYSTEM/cli/build/lib/lot-preflight.ts` (hal-v2#2258, #2264).

## §1 Problem (measured on `3b6048a`)

1. `run.py` `main()` dispatches only `doctor`, `verify` and workflow execution. `git grep state_hash`
   in `engine_py/`: 0 hits. A host that drives phases itself (Option-D / manual lots, the thin
   protocol after cutover) has no deterministic preflight to call and no receipt to check.
2. The building blocks exist and are used inside phases: `spec_cite.lint_spec` (+
   `spec_cite.BLOCKING_STATUSES`), `stub_passability.lint_red_file`, `tier_gate.lint_paths`,
   `facts_pack.collect` / `facts_pack.render`, `known_reds_ledger.parse_table_rows` /
   `known_reds_ledger.partition_by_kill_by` / `known_reds_ledger.red_match_tokens`,
   `check_ladder.prescreen`. The preflight composes them; it re-implements none.

## §2 Design

New module `engine_py/bytedigger_engine/preflight.py`: stdlib only, listed in `core_manifest.json`
`core_modules` and in `bytedigger_engine/mypy-strict-modules.txt`, no Cyrillic, no `HAL_*` env read,
no vendor/provider name or backend branch, `if __name__ == "__main__":` → `sys.exit(preflight_main())`.
`run.py` `main()` gains, next to `doctor`/`verify`:
`if sys.argv[1:2] == ["preflight"]: from bytedigger_engine.preflight import preflight_main; return preflight_main(sys.argv[2:])`.

### Symbols this spec INTRODUCES
- `parse_spec_fields`, `compute_state_hash`, `run_preflight`, `verify_receipt`, `receipt_path`,
  `preflight_main`, `PreflightResult`, `STEPS`, `STEP_CODES`, `RECEIPT_SCHEMA`, `RECEIPT_DIRNAME`

### Constants
- `STEPS = ("syntax", "tier", "cite", "stub", "scoped", "siblings", "facts", "prescreen")` — fixed order.
- `STEP_CODES = {step: "E_PREFLIGHT_<STEP upper>"}` written out as literals for all 8 steps.
- `RECEIPT_SCHEMA = 1`; `RECEIPT_DIRNAME = "bytedigger-preflight"`.

### op1 `parse_spec_fields(text) -> dict[str, list[str]]`
YAML-ish front-matter between a first line `---` and the next `---` line. Keys `red_tests`,
`sibling_tests`, `paths`, `red_pins`, `tier`; each value is a list (block list `- item`, inline
`[a, b]`, or a scalar → one-item list; surrounding quotes stripped). Missing key → `[]`. No
front-matter → all `[]`. Unknown keys ignored. Never raises on any str.

### op2 `compute_state_hash(toplevel) -> str`
`sha256` hex of the concatenation, each part followed by `\0`: `git rev-parse HEAD`;
`git status --porcelain=v1 --untracked-files=all`; `git diff --binary HEAD`;
`git diff --binary --cached HEAD`; then for every untracked non-ignored path
(`git ls-files -z --others --exclude-standard`, sorted bytewise) a line `<blob-sha> <path>\n` with
the blob sha from `git hash-object --stdin-paths`. Consequence: any edit to a tracked file (staged or
not), any staging change, any new/edited/deleted untracked non-ignored file, and any new commit
changes the hash; gitignored files do not. Deterministic: same tree → same hash.

### op3 `receipt_path(toplevel) -> Path`
`<git rev-parse --absolute-git-dir>/bytedigger-preflight/receipt.json` (per-worktree, outside the
work tree, so writing it never changes the state hash). `facts.md` lives in the same directory.

### op4 `run_preflight(spec, phase, *, cwd=".", base="origin/main", tier=None, classifier_cmd=None, classifier_timeout_s=check_ladder.DEFAULT_TIMEOUT_S, known_reds=None, test_timeout_s=600) -> PreflightResult`
`PreflightResult = {"exit_code": 0|1|2, "receipt": dict|None, "error_code": str|None, "error": str|None}`.
Order:
1. Resolve toplevel (`git rev-parse --show-toplevel` in `cwd`) and git dir; failure → exit 2
   `E_PREFLIGHT_GIT` (no receipt can exist that this run could find). Then **delete any existing
   `receipt.json` and `facts.md`** in the receipt dir: every later exit — 0, 1 or 2 — starts from
   no receipt, so an exit 2 never leaves an older green receipt behind.
2. `phase` not in `("red", "green")` → exit 2 `E_PREFLIGHT_USAGE`.
3. Resolve `HEAD` and `merge-base <base> HEAD`; failure → exit 2 `E_PREFLIGHT_GIT`.
4. Read the spec (`cwd`-relative or absolute); unreadable, or `red_tests` or `paths` empty → exit 2
   `E_PREFLIGHT_SPEC_FIELDS`. Exit 2 writes no receipt. Relative `red_tests`, `sibling_tests` and
   `paths` entries are resolved against toplevel (never the process cwd) wherever a step touches
   the filesystem; `tier_gate.lint_paths` receives the toplevel-joined absolute paths.
5. `state_hash = compute_state_hash(toplevel)` is computed **now, before any step runs**: the
   receipt binds the tree the steps actually saw, so an edit made while steps run makes the
   receipt stale immediately.
6. Run `STEPS` in order. Each step returns `(status, detail)`; it is timed (`ms`). After the first
   `red` step every later step is recorded `{"status": "skipped", "ms": 0, "detail": ""}` and not
   run. An exception inside steps 1–7 → `red` with detail `internal error: <msg>` (fail closed).
   `changed` = files differing from the merge-base (`git diff --name-only -z --diff-filter=d <mb>`)
   ∪ untracked non-ignored files, existing on disk only.
   - **syntax** — targets `changed ∪ red_tests` (existing). `.py` → builtin `compile(src, path,
     "exec")`; `.sh` → `bash -n`; `.json` → `json.loads`; `.ts/.tsx/.js` → `bun build --no-bundle`
     into a temp dir when `bun` is on `PATH`, else counted unchecked; other extensions ignored. Any
     failure → `red` `syntax error in <file>: <first line>; …`. Else `ok` `N file(s) checked[, M unchecked (no bun)]`.
   - **tier** — tier = `tier` arg, else spec `tier[0]`, upper-cased. `MICRO` →
     `tier_gate.lint_paths(paths, "MICRO")`; findings or `error` → `red` naming the paths. Other /
     none → `ok` `skip <tier|none>`.
   - **cite** — drop this toplevel's entry from spec_cite's per-process repo-index memo
     (`spec_cite._REPO_INDEX_CACHE.pop(str(toplevel.resolve()), None)`, so a host calling
     `run_preflight` twice in one process never lints against a stale index), then
     `spec_cite.lint_spec(spec_abs, toplevel)`; any finding with status in
     `spec_cite.BLOCKING_STATUSES` → `red` listing `<status> <file> <symbol>` (first 5). Else `ok`.
   - **stub** — each `.py` in `red_tests` (existing) → `stub_passability.lint_red_file(abs)`;
     any findings or `error` → `red`. No `.py` → `ok` `no py`.
   - **scoped** — run each `red_tests` file: `.py` → `[sys.executable, "-m", "pytest", "-q",
     "-p", "no:cacheprovider", "--junitxml", <tmp>, <file>]`; `*.test.ts|*.test.tsx|*.test.js` →
     `["bun", "test", <file>, "--reporter=junit", "--reporter-outfile", <tmp>]`; others skipped.
     cwd = toplevel, env adds `PYTHONDONTWRITEBYTECODE=1`, timeout `test_timeout_s`. Counts come
     from the JUnit XML only: `fail` = testcases with `<failure>` or `<error>` children plus
     top-level errors (collection errors count as failures), `pass` = the rest minus `<skipped>`.
     Report absent/unparseable, runner missing, or timeout → `red` `<file>: no test report`.
     `red` phase: `pass > 0` → `red` `<file>: N passing test(s) in red phase`; `fail == 0` →
     `red` `<file>: no failing test (vacuous RED)`. `green` phase: `fail > 0` → `red`
     `<file>: N failing (<first 3 names>)`. Else `ok` `<file>: P pass, F fail; …`.
   - **siblings** — each `sibling_tests` file run as in scoped. `fail == 0` → fine. `red` phase and
     the file is in `red_pins` → tolerated (`pinned: <file>`). Else every failed id
     `<file>::<testname>` must contain a token of an **active** known-reds row
     (ledger = `known_reds` arg or `<toplevel>/known-reds.md`; rows via
     `known_reds_ledger.parse_table_rows`, active via `partition_by_kill_by(rows,
     known_reds_ledger.resolve_today())`, tokens via `red_match_tokens`) → tolerated; otherwise
     `red` `<file>: N failing (…)`. No ledger file → no row tolerates anything. No siblings → `ok`
     `no siblings`.
   - **facts** — `facts_pack.render(facts_pack.collect(toplevel, spec_text), phase)` prefixed by a
     header (`phase`, `branch`, `HEAD`, `tier`, one line per earlier step), written to
     `<receipt dir>/facts.md`; `ok` with detail = that path; `facts_path` = that path.
   - **prescreen** (advisory, AC4) — never `red`, never changes `ok`, own `try/except` (the
     fail-closed rule above does not apply). `green` phase → `off` `skip green`.
     `classifier_cmd is None` → `off` `no classifier`. Else
     `check_ladder.prescreen(findings=[], gate_input=<facts.md text>, classifier_cmd=…,
     mode="shadow", timeout_s=classifier_timeout_s)`; classifier status `ok` → step `ok`
     (`label=<l> confidence=<c>`); `timeout` / `error` (missing binary, non-zero exit, bad JSON) →
     step `error` with detail = classifier status; any exception → step `error` `internal error: …`.
     Receipt field `prescreen` = `{"status", "label", "confidence", "ms"}` of the classifier, or
     `null` when the step did not call it.
7. Receipt (`state_hash` from point 5):
   `{"schema": 1, "phase", "ok", "head", "base", "state_hash", "spec", "steps":
   [{"name","status","ms","detail"} × 8], "facts_path", "prescreen", "ts"}`; `ok` = no step `red`.
   Written atomically (temp file + `os.replace`). Exit 0 if `ok`, else exit 1 with
   `error_code = STEP_CODES[<first red step>]`, `error` = first line of its detail.
Step status vocabulary: `ok | red | skipped | off | error` (`off`/`error` only on prescreen).

### op5 `verify_receipt(phase, toplevel) -> "fresh" | "stale" | "red" | "missing"`
All git calls in this module run as `git --no-optional-locks …` (verify never takes the index
lock). Checked in this order: receipt absent / unreadable / not JSON / `schema != 1` / `phase` mismatch →
`missing`; `head` ≠ current `HEAD` or `state_hash` ≠ `compute_state_hash(toplevel)` → `stale`; the hash computation failing (git error) → `stale`;
`ok` is not `True` → `red`; else `fresh`. Never raises (non-git dir → `missing`). Read-only.

### op6 CLI `preflight_main(argv) -> int`
`bytedigger-engine preflight --spec PATH --phase red|green [--json] [--base REF] [--tier T]
[--classifier-cmd JSON-argv] [--classifier-timeout-s F] [--known-reds PATH] [--test-timeout-s N]`
→ `run_preflight(...)`, returns its exit code. Human output: one line per step
`<name>: <status> <detail>` then `preflight: ok` or `preflight: red <E_CODE> <error>`. `--json`:
stdout is exactly one JSON document — the receipt (exit 0/1) or `{"ok": false, "error_code",
"error"}` (exit 2). The parser uses `prog="bytedigger-engine preflight"`. `--classifier-cmd` not a non-empty JSON list of strings → exit 2 `E_PREFLIGHT_USAGE`.
`bytedigger-engine preflight --verify --phase red|green [--cwd DIR]` → prints the
`verify_receipt` word; exit 0 iff `fresh`, else 1. argparse usage errors → exit 2 with the usage message on stderr and nothing on stdout (also
under `--json`; the one-JSON-document rule covers runs that reach `run_preflight`).

### Registries
`error_codes.ERROR_CODES` gains `E_PREFLIGHT_{SYNTAX,TIER,CITE,STUB,SCOPED,SIBLINGS,FACTS,PRESCREEN,
SPEC_FIELDS,GIT,USAGE}` (PRESCREEN is reserved: listed in `STEP_CODES` for uniformity, never emitted
because prescreen never reds); `engine_py/ERROR_CODES.md` regenerated. Receipt schema documented in
the module docstring.

## §3 Acceptance (→ RED `engine_py/tests/test_bd164_preflight.py`)

Fixture: a temp git repo (`git init`, a commit, `origin/main` created as a local ref with
`git update-ref refs/remotes/origin/main HEAD`) holding `calc.py` defining `add`, a spec whose
front-matter lists `red_tests: [tests/test_calc.py]`, `paths: [calc.py]` and whose body cites
`calc.py` `add`, and `tests/test_calc.py` with one failing test (red) / passing test (green).

- A1 `parse_spec_fields`: block list, inline list, scalar, quotes, no front-matter, missing keys.
- A2 green-path red phase: exit 0, receipt at `receipt_path`, 8 steps in `STEPS` order, `ok` true,
  `head`/`state_hash` match, `facts_path` exists; `verify_receipt("red", top) == "fresh"`.
- A3 fail-closed: `spec_cite.lint_spec` patched to raise → exit 1 `E_PREFLIGHT_CITE`, detail
  starts `internal error`, later steps `skipped`. Green phase, RED test sleeps 30 s,
  `test_timeout_s=1` → exit 1 `E_PREFLIGHT_SCOPED`, detail contains `no test report`.
  Relative engine-prod `paths` entry with process cwd ≠ toplevel and tier MICRO → `E_PREFLIGHT_TIER`.
- A3 each red step: syntax (broken `.py` in changed), cite (unresolved symbol), stub (stub-passable
  RED), scoped red-phase (passing test; vacuous file), scoped green-phase (failing test), siblings
  (failing sibling not in ledger) → exit 1, `error_code == STEP_CODES[step]`, later steps
  `skipped`, `verify_receipt == "red"`. Siblings tolerated by an active known-reds row and by
  `red_pins` (red phase) → ok. Tier MICRO with an engine_py prod path → `E_PREFLIGHT_TIER`.
- A4 staleness (AC3), each on a fresh receipt: edit a tracked file (unstaged); `git add` a change;
  stage then revert the work tree; create an untracked file; edit an untracked file present at
  receipt time; delete an untracked file; new commit → `stale`. A gitignored new file → still
  `fresh`. Receipt `phase` ≠ asked phase → `missing`; no receipt / corrupt JSON → `missing`.
- A5 prescreen advisory (AC4): no classifier → step `off`, ok true; classifier binary missing →
  `error`, ok and exit unchanged; classifier exits non-zero → `error`; classifier sleeps past
  `classifier_timeout_s` → `error`; classifier prints `{"label":"reject","confidence":0.99}` → step
  `ok`, preflight still ok (shadow never rejects); `check_ladder.prescreen` raising → `error`, ok.
  Same with a red earlier step: prescreen `skipped`.
- A6 exit 2: missing spec, empty `red_tests`, empty `paths`, bad phase, not a git repo, unknown
  `--base` → exit 2 with the code; for every case inside a repo a green receipt and `facts.md`
  are produced first and are gone afterwards.
- A4b: a tracked edit made by the RED test itself while it runs (the test writes to a tracked
  file) → the resulting receipt is `stale` (hash taken before steps).
- A7 provider-agnostic: the same fixture under `HAL_RUNNER_BACKEND=claude-subprocess` and
  `=anthropic-api` gives identical step names/statuses; `llm_subprocess.invoke_llm_subprocess`
  patched to raise is never reached. Module source has no `HAL_` literal and no
  `anthropic|claude|openai|jev` (case-insensitive) outside the docstring.
- A8 CLI: `run.py` `main()` with `["preflight", …]` dispatches; `--json` emits one parseable
  document; `--verify` prints the word and exits 0 only for fresh; bad `--classifier-cmd` (not JSON, not a list, `[]`) → 2; an argparse
  usage error under `--json` → exit 2, empty stdout.
- A9 registries: module in `core_manifest.json` `core_modules` and `mypy-strict-modules.txt`; every
  `STEP_CODES` value and the 3 input codes in `error_codes.ERROR_CODES`.
- Hygiene: an autouse fixture clears `HAL_KNOWN_REDS_TODAY` and every `GIT_*` env var.
- A10 one implementation: module source references `spec_cite.lint_spec`,
  `stub_passability.lint_red_file`, `tier_gate.lint_paths`, `facts_pack.collect`,
  `check_ladder.prescreen`, `known_reds_ledger.red_match_tokens`, and defines no function named
  like theirs.

## §4 Out of scope
HAL adapter / deletion of `lot-preflight.ts` (hal-v2 follow-up); calling preflight from inside the
engine's own phases; the HAL §1a sibling-audit prescreen section (host tool, stays in HAL).
