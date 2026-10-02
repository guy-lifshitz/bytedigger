# bd#165 — port the seven-channel §1a sibling-coupling detector into the engine

**Status: r1 (pre-gate)** · **Tier:** 3 (one new engine prod module, a 3-line dispatch in `run.py`,
a rewrite of one warn-only helper in `workflows/phase_5_implement.py`; Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `sibling_coupling.audit()` — the one function that derives keys from production files,
runs the seven channels, reconciles against a spec and returns rows. The CLI and the phase-5 helper
call it and add only I/O.
**Side of the seam:** engine. **Not touched:** `phases/`, `bytedigger_engine/engine.py`,
`workflows/phase_45_spec.py` (bd#89 P3 owns those; see §6).
**Source:** bd#165; HAL reference `SYSTEM/cli/build/lib/sibling_coupling.py` (1255 lines) +
the scope-mode dispatch of `SYSTEM/cli/build/sibling-test-audit.sh` (lines 96-282, 704-824);
HAL spec `SHARED/memory/Decisions/2026-07-25_7C0C4B92_sibling_coupling_channels_spec.md` (v2.1);
HAL oracle `SYSTEM/cli/build/__tests__/sibling-test-audit-scope-coupling.test.ts` + fixtures
`SYSTEM/cli/build/__tests__/fixtures/gh1200/` (all read at HAL `main`, 2026-10-02).

## §1 Problem (measured on `30e57bd`)

1. `workflows/phase_5_implement.py:3522` `_sibling_audit_warn` resolves
   `Path(__file__).resolve().parents[3] / "sibling-test-audit.sh"` (override `HAL_SIBLING_AUDIT_BIN`).
   `git ls-files | grep sibling-test-audit.sh` → 0. On every bd install the pass emits
   `red_sibling_audit_skipped reason=script_missing` and returns: bd has had no working §1a audit.
2. Even when the script is present it runs the legacy `--substrings` mode over the first 8 symbols
   the RED tests import (`_red_import_symbols`) — keys authored by the same intuition being audited,
   not the scope-file inversion of rules-full §1a / 7C0C4B92.
3. `git grep -n "value-literal\|exec-invocation\|scope-file" engine_py/bytedigger_engine` → 0.
4. Sibling inventory (`git grep` for `_sibling_audit_warn|red_sibling_audit|sibling-test-audit|
   HAL_SIBLING_AUDIT_BIN|_red_import_symbols`): `phase_5_implement.py` (helper + 2 call sites
   3934, 4145), `flags_catalog.py:369-380` (`HAL_SIBLING_AUDIT_GATE`, `HAL_SIBLING_AUDIT_BIN`),
   `tests/test_engine_path_closure.py:50` (`ESCAPE_ALLOWLIST["sibling-test-audit.sh"]`).
   `lib/directed_repair.py:267`, `scripts/lib/sibling_test_verifier.py:41,200` and
   `phase_45_spec.py:1855` only name the audit in prose/marker regexes — unaffected.

## §2 Design

### §2.1 New module `engine_py/bytedigger_engine/sibling_coupling.py`

A port of HAL `lib/sibling_coupling.py` **plus** the scope-mode dispatch that HAL keeps in bash.
Constraints:
- stdlib only; `from __future__ import annotations`; runs on Python 3.9 (no `match`, no 3.10+ APIs);
  no Cyrillic; listed in `engine_py/core_manifest.json` `core_modules`; NOT added to
  `mypy-strict-modules.txt` (port fidelity first; typing is a follow-up).
- **No environment reads** (no `os.environ`/`os.getenv`, so no `flags_catalog` entries). Every HAL
  env seam becomes an explicit parameter / CLI flag (table in §2.3).
- **No subprocess except one optional `git rev-parse --show-toplevel`** (default corpus root). The
  HAL `grep -a -F -n -H -f` batch is replaced by an in-Python fixed-string scan with identical
  semantics: file read as bytes, decoded `utf-8` with `errors="replace"`, split on `"\n"` only
  (a `"\r"` stays in the line content), 1-based line numbers, a trailing empty piece after a final
  newline is not a line; a row is produced for every (line, key) with `key in line`. The trace line
  `grep <channel> keys=<n> files=<m>` is still written once per channel call, unchanged in form.
  A file that cannot be read contributes no hits and is named once on stderr as
  `W_CORPUS_UNREADABLE <path>` (replaces HAL `W_GREP_SPAWN_FAILED`).
- **No host path.** HAL `_default_root` falls back to `lib/../../..`; the port falls back to
  `os.getcwd()` (after `W_GIT_ROOT_UNRESOLVED`). HAL `_graph_path` defaults to
  `<lib dir>/../graphify-out/graph.json`; the port has **no default graph** (§2.4).
- No LLM, no provider, no backend: nothing in the module imports from `bytedigger_engine`
  (`llm_subprocess`, `lib.*`, `workflows.*`) or names a vendor/model/backend.

Detector bodies (channel matrix, key selection, stop list, extension matrix, `_dedup_longest`,
`_apply_key_file_cap`, `_KeySel`, constants `DEFAULT_MIN_LITERAL_LEN=6`, `DEFAULT_MAX_KEYS=200`,
`DEFAULT_MAX_CORPUS=5000`, `DEFAULT_MAX_KEY_FILES=25`, `CHANNELS`, `PATH_FAMILY`,
`SPAWN_PRIMITIVES`, `READ_PRIMITIVES`, `STOP_LIST`, corpus name pattern + prunes, spec-token regex,
every `W_*`/`E_*` stderr text) are ported **verbatim**; the HAL spec v2.1 §1.2-§1.7 is normative
for them. HAL prune `_PRUNE_SUBPATHS` (`.claude/worktrees`, `SHARED/memory-backups`) is kept as-is.

#### Symbols this spec INTRODUCES
The eleven HAL public names (HAL spec §1.9), same signatures except where §2.3/§2.4 replace an env
read by a parameter: `extract_constant_values`, `extract_data_cells`, `extract_defined_symbols`,
`detect_source_read`, `detect_imports`, `detect_call_sites`, `reconcile_with_spec`,
`collect_test_corpus`, `classify_path_family`, `is_distinctive_key`, `grep_keys_batched`.
New: `audit`, `AuditResult`, `SiblingAuditError`, `scope_files_from_spec`, `main`, `JSON_SCHEMA`.

### §2.2 `audit(...) -> AuditResult`

```
audit(scope_files, *, test_globs=None, corpus_root=None, channels=None, spec_path=None,
      min_literal_len=6, max_keys=200, max_corpus=5000, max_key_files=25,
      graph_path=None, exclude=None, trace_path=None) -> AuditResult
```
- Validates exactly like the HAL shell (§1.7 rows 1-2): an unknown channel → `SiblingAuditError`
  with `code="E_UNKNOWN_CHANNEL"` and a message listing all seven channel names; an unreadable scope
  file or spec → `code="E_SCOPE_FILE_UNREADABLE"`; empty corpus → `code="E_NO_TEST_CORPUS"`
  (message as HAL). `scope_files` empty → `code="E_NO_SCOPE_FILES"`.
- `exclude`: absolute paths removed from the corpus (HAL `collect_test_corpus(exclude=)`).
- Returns `AuditResult` (frozen dataclass): `rows: tuple[Row, ...]` where `Row` is a 6-tuple
  `(file, line:int, func, token, channel, verdict)` with verdict `cited|MISSING` (or `-` when no
  spec, as HAL); `warnings: tuple[str, ...]` (every `W_*` line, in emission order — also written to
  stderr by the CLI only, never by `audit`); `gate_warn: bool` (any of `W_UNSUPPORTED_SCOPE_EXT`,
  `W_KEY_CAP`, `W_CORPUS_CAP` — HAL lib exit 4); `partial_channels: bool`;
  `call_site: dict` (`{"source": "graph+grep"|"grep", "reason": None|"no_graph"|"graph_unreadable"|
  "channel_off"}`); `missing: int`.
- Row order: the HAL `LC_ALL=C sort -t\t -k1,1 -k2,2n -k6,6n` — bytewise by file, numeric by line,
  numeric by the internal sort index — then the sort index is dropped.
- Never writes to stdout/stderr (diagnostics go into `warnings`); `trace_path` (if given) receives
  the trace lines (HAL `HAL_SIBLING_COUPLING_TRACE`).

### §2.3 CLI `python -m bytedigger_engine.sibling_coupling` and `bytedigger-engine sibling-audit`

`run.py` `main()` gains, next to `preflight`:
`if sys.argv[1:2] == ["sibling-audit"]: from bytedigger_engine.sibling_coupling import main as _sa_main; return _sa_main(sys.argv[2:])`.
`if __name__ == "__main__": sys.exit(main())` in the module.

| flag | HAL equivalent |
|---|---|
| `--scope-file PATH` (repeatable, required) | same |
| `--test-glob PAT` (repeatable) | same (expanded in-process) |
| `--channels LIST` | same |
| `--spec PATH` | same |
| `--min-literal-len N`, `--max-keys N`, `--max-corpus N`, `--max-key-files N` | same (non-negative int, else exit 2) |
| `--require-clean` | same |
| `--corpus-root DIR` | env `HAL_SIBLING_CORPUS_ROOT` |
| `--graph PATH` | env `HAL_SIBLING_GRAPH` (+ `HAL_SIBLING_GRAPH_WALK`; no flag ⇒ no graph) |
| `--trace PATH` | env `HAL_SIBLING_COUPLING_TRACE` |
| `--json` | new |
| `-h/--help` | same (exit 0; names every flag above and all seven channels) |

Legacy HAL modes (`--substrings`, `--substring-classes`, `--callers-of`, `--detect-contract-flips`,
`--glob`, `--phase`) are **not** ported; passing one is an unknown argument → exit 2.

Output: TSV, one row per line, 6 fields `file line func token channel verdict` (HAL §1.2).
`--json`: one JSON object on stdout, `{"schema": 1, "rows": [{"file","line","func","token",
"channel","verdict"}...], "warnings": [...], "call_site": {...}, "missing": n, "exit": n}` where
`exit` equals the process exit code. Warnings go to stderr in both modes.

Exit codes (HAL §1.7/§1.8, shell + lib combined; exit-2 cases win over exit 1):
0 ok; 1 `--require-clean` and `missing > 0`; 2 any `SiblingAuditError` (code on stderr), any
usage error, or `--require-clean` with `partial_channels` (`E_PARTIAL_CHANNELS_GATE: --require-clean
needs the full channel set (...)` on stderr, checked first) or with `gate_warn`. Without
`--require-clean`, warnings never change the exit code.

### §2.4 Graph channel optional (issue AC4)

`call-site` is always the grep half; the graph half is added only when `graph_path` is given, is a
file, and parses as JSON. `graph_path=None` → `call_site.source="grep", reason="no_graph"`, no
warning. Given but unreadable / not JSON → `source="grep", reason="graph_unreadable"`, warning
`W_GRAPH_UNREADABLE <path>`, never an error, never a gate escalation. Readable → `"graph+grep"`
(a zero-caller-edge graph must not suppress grep — HAL AC36). `call-site` not in the active
channels → `reason="channel_off"`.

### §2.5 `scope_files_from_spec(paths, root) -> list[str]`

Uses `lib.run_allowlist.parse_spec_files_allowlist` **in the phase-5 helper, not in the module**
(the module stays import-free; this function takes the already-parsed list):
`scope_files_from_spec(paths: list[str], root: str) -> list[str]` — each entry resolved against
`root`, kept iff it is a file inside `root` and its basename does NOT match the corpus test pattern;
sorted, de-duplicated, absolute.

### §2.6 Phase-5 rewiring (issue AC2) — `_sibling_audit_warn(resolved_paths, git_cwd, spec_path=None)`

Still warn-only; never alters the `StepResult`; never raises. The two call sites pass
`spec_path=prev.data.get("spec_path")` (that is the only change at 3934 and 4145).
1. `spec_path` falsy or `parse_spec_files_allowlist(spec_path)` is `None` →
   `red_sibling_audit_skipped {"phase": 5, "reason": "no_spec"}`.
2. `scope_files_from_spec(...)` empty → `reason: "no_scope_files"`.
3. `audit(scope_files, corpus_root=git_cwd, spec_path=spec_path, exclude=resolved_paths,
   graph_path=<git_cwd>/graphify-out/graph.json if that file exists else None)`.
   `SiblingAuditError` → `reason: "<code>"`; any other exception → `reason: "detector_error"`,
   `"error": <exception class name>`.
4. MISSING rows → `red_sibling_audit_warn` (severity warn) `{"phase": 5, "count": n,
   "hits": [first 20 rows as tab-joined 6-field lines, file relative to git_cwd],
   "scope_files": [relative], "call_site": {...}, "warnings": [first 10]}`.
   No MISSING rows → `red_sibling_audit_clean {"phase": 5, "scope_files_n", "rows_n",
   "call_site", "warnings": [first 10]}` — a clean pass is visible, not silent.
The helper reaches the detector as a module attribute (`from bytedigger_engine import sibling_coupling`;
`sibling_coupling.audit(...)`), so patching `bytedigger_engine.sibling_coupling.audit` takes effect.
Removed: the script lookup, `HAL_SIBLING_AUDIT_BIN`, the `subprocess.run(["bash", ...])`,
`_red_import_symbols`, `_GH535_SKIP_MODULES`, reasons `script_missing|exec_error|rc_N|no_symbols`.
`HAL_SIBLING_AUDIT_GATE` kill-switch unchanged (default ON).

## §3 Files
- `engine_py/bytedigger_engine/sibling_coupling.py` — new
- `engine_py/bytedigger_engine/run.py` — `sibling-audit` dispatch (3 lines)
- `engine_py/bytedigger_engine/workflows/phase_5_implement.py` — §2.6 (helper 3493-3586 + call sites 3934, 4145 only)
- `engine_py/bytedigger_engine/flags_catalog.py` — delete the `HAL_SIBLING_AUDIT_BIN` entry; `HAL_SIBLING_AUDIT_GATE` description: "...disables the §1a sibling-coupling warn pass (bd#165, in-package detector, warn-only)."
- `engine_py/core_manifest.json` — add `"sibling_coupling.py"` to `core_modules`
- `engine_py/tests/test_bd165_sibling_coupling.py` — new (RED)
- `engine_py/tests/fixtures/sibling_coupling/` — new: verbatim copy of HAL `__tests__/fixtures/gh1200/` minus `lib_fail.py` and `golden/` (shell-only), plus `conftest.py` with `collect_ignore_glob = ["*"]` so pytest never collects the fixture `test_*.py` files
- authorized-test-edits:
  - `engine_py/tests/test_engine_path_closure.py` — drop the `ESCAPE_ALLOWLIST["sibling-test-audit.sh"]` row (the reference is gone)

## §4 Acceptance (RED = `engine_py/tests/test_bd165_sibling_coupling.py`)

**P — parity (issue AC5).** The HAL TS ACs below are the oracle: each is translated 1:1 into a
pytest that drives the CLI as a subprocess (`[sys.executable, "-m", "bytedigger_engine.sibling_coupling", ...]`,
cwd = `engine_py/`, env with `PYTHONPATH` = `engine_py/`) over `tests/fixtures/sibling_coupling/`,
with the HAL assertions kept (same exit codes, same channel/token/verdict/stderr-code checks).
Argv mapping: `bash SCRIPT` → the module; `HAL_SIBLING_GRAPH_WALK=0` → (nothing; no graph is the
default); `HAL_SIBLING_GRAPH=X` → `--graph X`; `HAL_SIBLING_CORPUS_ROOT=X` → `--corpus-root X`;
`HAL_SIBLING_COUPLING_TRACE=X` → `--trace X`.
Ported: AC1-AC13, AC15 (help text: the flags of §2.3 and all seven channels), AC16, AC18-AC20,
AC22-AC24, AC27, AC28, AC30, AC31, AC32, AC33, AC35 (import the module, eleven names callable),
AC36, AC37-AC40, AC42, AC43.
AC21 → **P21**: `--scope-file engine_py/bytedigger_engine/sibling_coupling.py` with
`--corpus-root engine_py/tests` finds `test_bd165_sibling_coupling.py` (dogfood).
Not ported (shell/legacy-only, stated so the gap is visible): AC14, AC17, AC29, AC34 (legacy
`--substrings`), AC25, AC26 (`HAL_SIBLING_COUPLING_LIB` lib-path dispatch), AC41 (`--phase`).

**B — bd-specific.**
- B1 (issue AC1/AC2, installed layout): copy only the `bytedigger_engine/` package dir into a fresh
  `tmp_path/site/`; with `PYTHONPATH=tmp_path/site`, cwd = `tmp_path` (outside any git repo),
  `python -m bytedigger_engine.sibling_coupling --scope-file <fixture value_const.py> --test-glob
  <fixture tests glob>` exits 0 with ≥1 `value-literal` row; and `python -m bytedigger_engine.run
  sibling-audit --help` exits 0.
- B2 (`--json`): the AC2 run with `--json` → stdout parses as one JSON object, `schema == 1`, rows
  equal (as 6-tuples, in order) the TSV run's rows, `exit == 0`; with `--spec spec_incomplete.md
  --require-clean --json` → process exit 1 and `exit == 1`, `missing ≥ 1`.
- B3 (issue AC4): no `--graph` → `call_site == {"source": "grep", "reason": "no_graph"}`, exit 0;
  `--graph /nonexistent.json` → `{"source": "grep", "reason": "graph_unreadable"}`, stderr has
  `W_GRAPH_UNREADABLE`, exit 0, and still exit 0 with `--require-clean` + a clean scope file;
  `--graph graph_zero_edges.json` → `source == "graph+grep"` and the AC5 caller rows still present.
- B4 (no host binary): the AC2 invocation with `PATH` set to an empty tmp dir (interpreter by
  absolute path) produces rows identical to the normal run.
- B5 (phase-5 helper, in-process, `_emit_safe` captured via monkeypatch): a staged tmp git repo
  with `pkg/mod.py` (a distinctive constant), `tests/test_pinner.py` pinning it, a RED file
  `tests/test_new.py`, and a spec whose `## Files` lists `pkg/mod.py` and `tests/test_new.py`:
  (a) spec not citing `test_pinner.py` → one `red_sibling_audit_warn` whose hits name
  `tests/test_pinner.py` with verdict `MISSING` and no hit names `tests/test_new.py`;
  (b) spec also citing `tests/test_pinner.py` → `red_sibling_audit_clean`, no warn;
  (c) `spec_path=None` → `skipped reason=no_spec`; (d) `## Files` listing only test files →
  `reason=no_scope_files`; (e) `audit` monkeypatched to raise `RuntimeError` → `reason=detector_error`,
  `error=RuntimeError`, the helper returns normally; (f) in every case the helper returns `None`
  and never emits `reason=script_missing`.
- B6 (static): `phase_5_implement.py` source contains none of `sibling-test-audit.sh`,
  `HAL_SIBLING_AUDIT_BIN`, `_red_import_symbols`; both call sites pass `spec_path=`;
  `flags_catalog.FLAGS` has no `HAL_SIBLING_AUDIT_BIN`; `sibling_coupling.py` AST imports only
  stdlib modules (`sys.stdlib_module_names` on 3.10+, a fixed allow-set otherwise), contains no
  `os.environ`/`os.getenv`, no `subprocess` call other than the one `git rev-parse`, and no token
  matching `(?i)anthropic|claude|openai|jev|pydantic|sonnet|opus|haiku`.
- B7: `"sibling_coupling.py" in core_manifest["core_modules"]`;
  `test_engine_path_closure.ESCAPE_ALLOWLIST` has no `sibling-test-audit.sh` key.

## §5 Design constraints (issue) — how each is met
- Deterministic first: the whole check is `audit()`; no LLM rung exists in this PR. A classifier
  rung (check_ladder) is out of scope.
- Provider-agnostic / provider-down: the module imports no engine or provider code (B6), so a
  provider or classifier being down cannot reach it; the phase-5 helper degrades every failure to a
  logged `red_sibling_audit_skipped` (B5e) and never crashes the step.
- Subscription vs API-key backends: the check runs before/independent of any backend call and takes
  no backend input; B5 exercises it with no backend configured at all, which covers both.

## §6 Out of scope (follow-up PR, after bd#89 merges)
- Issue AC3: phase 4.5 rejecting a spec whose in-scope files have uncovered coupled tests
  (shadow → enforce flag + kill-switch, flip criteria). Lives in `workflows/phase_45_spec.py`,
  which bd#89 P3 is rewriting now.
- Wiring `audit()` into `preflight.py` `step_prescreen` / `step_siblings` (bd#164 receipt).
- mypy-strict typing of `sibling_coupling.py`.
- HAL switching `sibling-test-audit.sh` scope mode to call this module (hal-v2 side).
