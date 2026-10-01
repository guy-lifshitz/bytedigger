# bd#141 items 1+2 — claim-vs-evidence check (VerificationGate TF) and LoopDetector move into the engine

**Status: FROZEN r2 (gate r1 REJECT → 5 blockers fixed; gate r2 PASS, see `2026-10-01-bd141-gate-r{1,2}.md`)** · **Tier:** 3 (two new engine prod `.py` modules, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `claim_evidence.evaluate_turn` — the one function that turns a transcript into a
TF verdict; the CLI and every host adapter call it and add nothing but I/O. Second chokepoint:
`loop_detector.observe` — the one place a tool call is folded into the window and an alert is
decided; `process_event` and the CLI only load/save around it.
**Side of the seam (decision 2026-07-26 §7.1 / §7.4):** engine. The host keeps only the
mechanism the engine cannot own: hook registration, the log-only/block policy, the journal
location, and host-language vocabulary (passed in as data). HAL's adapter and its
host-controls registry entry are hal-v2 work (pairs with hal-v2#2277), not this PR.
**Source:** bd#141 items 1–2; ports hal-v2 PR #2085 (`SYSTEM/cli/build/lib/verification-gate.ts`
class TF + `stop-gate-transcript.ts`) and PR #2088 (`SYSTEM/hooks/loop-detector.ts`).

## §1 Problem (measured on `f718084`)

1. BD has no check for a "done / tests pass" claim made while the same turn's runner output is
   red. The only implementation is HAL host TypeScript, so no other BD host gets it.
   HAL journal 24–48 h after landing (`SHARED/state/verification-gate.jsonl`, class TF):
   78 rows — `no-runner` 67, `clear` 10, `fire` 1.
2. BD has no tool-call loop detector. HAL's host hook logged 5 episodes in two days
   (`SHARED/state/loop-detector.jsonl`: 30.09 repeat 1 / thrash 2; 01.10 repeat 1 / thrash 1).
3. Both HAL modules hard-code Russian vocabulary and `HAL_*` env names; neither can enter the
   engine as-is (`cyrillic-prose-lint.py`, `core_manifest.json` `forbidden.env_prefixes: ["HAL_"]`).

## §2 Design

Both modules are stdlib-only, listed in `core_manifest.json` `core_modules` and in
`mypy-strict-modules.txt`, contain no Cyrillic, read no `HAL_*` env, and have a
`if __name__ == "__main__":` CLI. Vocabulary and runner patterns use Python `re` with
`re.IGNORECASE`; the red-line and bun-fail regexes below are case-SENSITIVE, exactly as in the TS
source (`fail`, `FAILED`, `[Ee]xit code`, `conclusion: failure` match only as written). "Word
boundary" below means `(?<!\w)(?:P)(?!\w)` (Python `\w` is Unicode-aware, matching the TS
`[\p{L}\p{N}_]` class).

### op1 — `engine_py/bytedigger_engine/claim_evidence.py`

**Vocabulary.** `@dataclass(frozen=True) class Vocabulary` with four `tuple[str, ...]` fields —
`claim`, `downgrade`, `negation`, `runner` — each a tuple of regex alternatives. Joined pattern
of a field = `"|".join(alternatives)`.

`DEFAULT_VOCABULARY` (English only):
- `claim`: `done`, `all green`, `all tests pass(?:ing|ed)?`, `tests pass(?:ing|ed)?`,
  `suite is green`, `everything passes`
- `downgrade`: `known[- ]red`, `except`, `red on main`, `pre-?existing`,
  `[1-9]\d*\s*(?:fail(?:s|ed|ures?)?)`, `(?:failing|failed) tests?`
- `negation`: `not`, `never`, `should`, `will`, `todo`, `going to`
- `runner`: `bun test`, `bun run test`, `pytest`, `python3? -m pytest`, `npm test`, `jest`,
  `vitest`, `go test`, `cargo test`, `gh pr checks`, `gh run (?:view|watch)`

`Vocabulary.extended(other) -> Vocabulary`: per field, `self` alternatives then `other`'s,
duplicates dropped (first occurrence kept).
`load_vocabulary(path) -> Vocabulary`: reads a JSON object whose keys are a subset of the four
field names, each a list of strings; returns `DEFAULT_VOCABULARY.extended(<that>)`. Raises
`ValueError` on: unreadable file, invalid JSON, non-object, unknown key, a value that is not a
list of strings, or an alternative that fails `re.compile`.

**Transcript helpers** (entries = list of dicts parsed from Claude Code transcript JSONL):
- Sidechain entries (`isSidechain is True`) are dropped before every step below.
- `turn_boundary_index(entries) -> int`: index (in the sidechain-filtered list) of the LAST
  `type == "user"` entry whose `message.content` is a string, or a list with at least one block
  whose `type != "tool_result"`; `-1` when none.
- `slice_current_turn(entries) -> list`: filtered list from that index on; `[]` when `-1`.
- `final_assistant_text(slice) -> str`: text blocks of the LAST `type == "assistant"` entry,
  joined with `"\n"` (string content counts as one text block); `""` when none.
- `strip_quoted_text(text) -> str`: drops fenced blocks (```` ``` ```` or `~~~`, closed only by the
  same marker; fence lines themselves dropped), lines whose first non-space char is `>`, then
  removes every inline `` `...` `` span (no newline inside).

**Claim.** `claim_phrase(stripped_text, vocab) -> str | None`:
1. If the word-bounded `downgrade` pattern matches anywhere → `None` (honest downgrade).
2. Else, for each word-bounded `claim` match in order: skip it if negated — the clause before
   it (text since the last of `. ; ! ? —` or newline, NOT the whole prior text) contains a
   word-bounded `negation` match; skip it if the next clause-boundary char at or after the end of
   the match is `?`; otherwise return the matched text (original
   case). No surviving match → `None`.

**Runner.** `runner_id(command) -> str | None`: split the command on `&&`, `||`, `;`, `|`,
newline; for each segment, strip, drop leading `NAME=value` assignments and one leading
`timeout <N…> `; if the segment starts with the `runner` pattern followed by a non-`\w` char or
end → return the match lowercased. First hit wins; none → `None`.

**Red.** gh log prefix `P = (?:[^\t\n]*\t){0,2}(?:\S+Z\s+)?`.
`bun_fail_identity(line) -> str | None`: match
`^P\s*\(fail\)\s+(.*?)(?:\s+\[[0-9.]+ms\])?\s*$` (the `[ms]` suffix is OPTIONAL) and return
group 1, else `None`. So `(fail) a > b [3.10ms]` → `a > b`, `(fail) x` → `x`.
`text_is_red(text) -> bool`: per line, skip lines containing `(pass)`; red if the line matches
any of: `"conclusion"\s*:\s*"failure"` or `conclusion:\s*failure`; a bun fail line;
`^P\s*[1-9]\d*\s+fail(?:s|ed|ures?)?\s*$`; `^=+ .*\b[1-9]\d* failed\b`; `^P\s*FAILED\b`;
`^\s*[Ee]xit code:?\s*[1-9]`; `^[^\t]+\tfail\t`.

**Verdict.** `evaluate_turn(entries, vocab=DEFAULT_VOCABULARY) -> dict` with exactly the keys
`outcome`, `phrase`, `runner`:
- no boundary or empty final text → `{"outcome": "no-turn", "phrase": None, "runner": None}`
- `claim_phrase(strip_quoted_text(final))` is `None` → `outcome "no-claim"`
- otherwise walk the slice: a `tool_use` block named `Bash` with a string `id`, a string
  `input.command`, `input.run_in_background is not True`, and a non-`None` `runner_id` registers
  `id → runner`; a `tool_result` block whose string `tool_use_id` is registered sets that
  runner's last result: red = (`is_error is True` and the runner does not start with `gh `) or
  `text_is_red(<content text>)` (string content, or list items that are strings / have a string
  `text`, joined with `"\n"`). A later result for the same runner replaces the earlier one and
  moves it to the end of the order.
- no registered result → `"no-runner"`; else the first runner (in that order) whose last
  result is red → `"fire"` with `runner`; else `"clear"`. `phrase` is set for every outcome
  from `no-runner` on.

`evaluate_transcript(path, vocab=DEFAULT_VOCABULARY) -> dict`: reads the file as UTF-8 JSONL,
skips blank lines and lines that are not a JSON object; unreadable/missing file → `no-turn`.

**CLI.** `python -m bytedigger_engine.claim_evidence --transcript PATH [--vocab PATH]`
prints ONE line, `json.dumps(verdict, sort_keys=True)`, exit 0. Bad `--vocab` (any
`load_vocabulary` `ValueError`) or missing `--transcript` → a message on stderr, exit 2, empty
stdout. The CLI never blocks, never writes files, never reads env: the log/block policy is the
host's.

### op2 — `engine_py/bytedigger_engine/loop_detector.py`

**Thresholds.** `@dataclass(frozen=True) class Thresholds` — `window=20`, `repeat=3`,
`thrash_span=8`, `thrash_calls=5`, `thrash_fails=3`, `cooldown=4`.
`thresholds_from_env(environ) -> Thresholds`: `BD_LOOP_WINDOW`, `BD_LOOP_REPEAT`,
`BD_LOOP_THRASH_SPAN`, `BD_LOOP_THRASH_CALLS`, `BD_LOOP_THRASH_FAILS`, `BD_LOOP_COOLDOWN`; a
value is used only if it is all digits after strip and `> 0`, else the default.

**Hash.** `call_hash(tool_input) -> str`: sha256 hex of
`json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
(key order does not change the hash). Signature of an entry = `f"{tool}:{hash}"`.

**State.** `{"v": 1, "n": int, "win": [{"t": str, "h": str, "f": bool}], "last_alert_n": int|None,
"live": {str: int}}`. `empty_state()`. `load_state(path)`: missing / unparseable / wrong shape
(any field of the wrong type, `v != 1`, a `win` item or `live` value of the wrong type; `bool`
is not an int here) → `empty_state()`. `save_state(path, state)`: makes parent dirs, writes
`<path>.<pid>.tmp` in the same dir, `os.replace` onto `path`; on failure removes the tmp file
and re-raises.

**Observe.** `observe(state, tool, tool_input, failed, thresholds) -> tuple[dict, dict | None]`
— pure: does not mutate its `state` argument. Steps, with `n = state.n + 1`:
1. append `{t, h, f}`; keep only the last `window` entries.
2. hits: **repeat** — the current signature occurs `>= repeat` times in the window, key
   `rep:<sig>`, detail `"<tool> x<count> identical calls in the last <window>"`;
   **oscillation** — window has ≥4 entries and the last four signatures are `x y x y` with
   `x != y`, key `osc:<min(x,y)>|<max(x,y)>`, detail `"<toolA>/<toolB> alternate a-b-a-b"`;
   **thrash** — of the last `thrash_span` entries, those with the current tool number
   `>= thrash_calls` and `>= thrash_fails` of them failed, key `thr:<tool>`, detail
   `"<tool> <calls> of the last <span>, <fails> failed"`.
3. expire: drop every `live` key with `n - live[key] > window`.
4. if any hit: if any hit key is in `live` → refresh every hit key to `n`, no alert (same
   episode); elif `last_alert_n is None` or `n - last_alert_n > cooldown` → `last_alert_n = n`,
   every hit key `= n`, alert = the highest-ranked hit (thrash > oscillation > repeat) as
   `{"kind", "key", "detail"}`; else (cooldown) no alert and `live` is NOT updated.

**Event.** `process_event(payload, state_dir, log_path=None, thresholds=Thresholds()) -> str | None`:
returns the advisory text or `None`; never raises (any exception → `None`).
- `None` without touching disk when: payload not a dict; `agent_id` is a non-empty string
  (subagent); `tool_name` not a non-empty string.
- session id = `session_id` with every char outside `[A-Za-z0-9_-]` removed, `"nosession"` if
  empty; state file `<state_dir>/<sid>.json`; `failed = hook_event_name == "PostToolUseFailure"`;
  `tool_input` missing → `None` is hashed.
- load → observe → `save_state` BEFORE returning; a save failure → `None`.
- on alert, if `log_path` is set, create its parent dirs and append one JSON line `{"ts", "session_id", "kind", "tool", "n",
  "key_sha256"}` (`session_id` = the sanitized sid; no raw input, key hashed); a journal failure does not suppress the alert.
- advisory text (one line, no newline inside): `"<system-reminder>[LOOP DETECTED] <kind>: <detail>. Advisory only: stop and
  change approach; repeating the same call will not give a different result.</system-reminder>"`.

**CLI.** `python -m bytedigger_engine.loop_detector --state-dir DIR [--log PATH]` reads the hook
payload JSON from stdin, prints the advisory + `"\n"` when there is one, else nothing; ALWAYS
exit 0 (invalid stdin included). `BD_LOOP_DETECTOR=0` → nothing read, nothing written. Thresholds
from `thresholds_from_env(os.environ)`.

### Declared divergences from the HAL TS source
English-only default vocabulary, host vocab as data; `BD_*` env names; runner list is vocab
data and gains `python3? -m pytest`, drops HAL's `canary.sh`; the `is_error` exemption covers
every runner starting with `gh ` (TS: `gh pr checks` / `gh run` — the only `gh` runners in the
default list); non-JSON transcript lines are skipped instead of failing open; the engine returns a
verdict only (no journal, no block mode, no fingerprint/reminder).

## §3 Non-goals / files NOT in scope (§1v)
- No BD `hooks/hooks.json` registration, no `Stop`/`PostToolUse` wiring in BD itself — the
  host registers the CLI. No journal/block mode in `claim_evidence`.
- No HAL change (adapter, vocabulary file with Russian tokens, host-controls registry) — hal-v2
  follow-up, seam with hal-v2#2277.
- No change to `run.py`, phase workflows, `verification_registry.py`, `ERROR_CODES.md`.
- Items 3–7 of bd#141.

## §4 Scope (files touched)
- NEW `engine_py/bytedigger_engine/claim_evidence.py`, `engine_py/bytedigger_engine/loop_detector.py`
- NEW `engine_py/tests/test_bd141_claim_evidence.py`, `engine_py/tests/test_bd141_loop_detector.py`
- `engine_py/core_manifest.json` (+2 `core_modules`), `engine_py/bytedigger_engine/mypy-strict-modules.txt` (+2)
- `CHANGELOG.md` (Unreleased / Added), this spec.

Sibling tests (§1a) to run: `test_core_boundary*`, `test_*manifest*`, `test_*mypy_strict*`,
packaging test that compares the packaged module list with `core_manifest.json`, plus
`python3 core-boundary-lint.py` and `python3 cyrillic-prose-lint.py`.

## §5 Acceptance criteria

claim_evidence (C):
| AC | Check |
|---|---|
| C1 | `claim_phrase("All tests pass.", D)` → `"All tests pass"`; `"Done"` alone → `"Done"` |
| C2 | negation: `"This is not done yet"`, `"I will make the tests pass"` → `None`; clause-scoped: `"Not yet. Done."` → `"Done"` |
| C3 | question: `"Is it done?"` → `None`; `"Done? No."` → `None` |
| C4 | downgrade: `"Done, except 1 failing test"`, `"all green (known red #12)"`, `"done; 2 failed on main"` → `None` |
| C5 | word boundary: `"abandoned"`, `"undone"` → `None` |
| C6 | `strip_quoted_text`: claim only inside a ``` fence, a `> quote`, or `` `done` `` → `claim_phrase(strip(...))` is `None` |
| C7 | `runner_id`: `"cd x && CI=1 timeout 60s bun test a.ts"` → `"bun test"`; `"echo pytest"` → `None`; `"python3 -m pytest -q \| tail"` → `"python3 -m pytest"`; `"bun testx"` → `None` |
| C8 | `text_is_red` true for: `" 3 fail"`, `"(fail) suite > case [1.2ms]"`, `"==== 2 failed, 5 passed in 1s ===="`, `"FAILED tests/x.py::t"`, `"Exit code 1"`, `"ci\tfail\t2m"`, `'"conclusion": "failure"'`; false for `" 0 fail"`, `"(pass) a (fail) b"`, `"12 pass"`, and (case-sensitive) `"3 FAIL"`, `"failed to fetch"`, `"EXIT CODE 1"`, `"Conclusion: Failure"` |
| C9 | `bun_fail_identity("(fail) a > b [3.10ms]") == "a > b"`; with gh prefix `"job\tstep\t2026-10-01T00:00:00Z (fail) x"` → `"x"`; `"(fail) x"` (no ms suffix) → `"x"` |
| C10 | `evaluate_turn`: claim + red `bun test` result in the turn → `{"outcome":"fire","phrase":…,"runner":"bun test"}` |
| C11 | red run followed by a green rerun of the same runner → `clear`; red `pytest` + green `bun test` → `fire` runner `pytest` |
| C12 | `run_in_background: true` runner, non-Bash tool, or result id with no matching use → `no-runner` |
| C13 | `gh pr checks` result with `is_error: true` and pending text → `clear`; `bun test` with `is_error: true` and empty text → `fire` |
| C14 | runner red only in a PREVIOUS turn (before the last real user message) → `no-runner`; a `tool_result`-only user entry does not start a new turn |
| C15 | sidechain entries (red runner + claim) ignored; no claim → `no-claim`; empty transcript → `no-turn`; keys are exactly `{outcome, phrase, runner}` |
| C16 | `Vocabulary.extended` keeps order and drops duplicates; `load_vocabulary` adds a host claim token (non-ASCII, e.g. a JSON `\u` escape) that then fires; ValueError for: bad JSON, unknown key, non-list value, uncompilable regex |
| C17 | CLI (real subprocess, `sys.executable -m bytedigger_engine.claim_evidence`) on a JSONL file written to `tmp_path`: stdout is one JSON line equal to `evaluate_transcript`, rc 0; missing file → `no-turn` rc 0; bad `--vocab` → rc 2, empty stdout, non-empty stderr; no `--transcript` → rc 2 |
| C18 | `evaluate_transcript` skips blank and non-JSON lines |

loop_detector (L):
| AC | Check |
|---|---|
| L1 | `call_hash({"a":1,"b":2}) == call_hash({"b":2,"a":1})`; differs for a different value |
| L2 | repeat: 3 identical calls → alert kind `repeat` on the 3rd, detail contains `x3` |
| L3 | oscillation: A,B,A,B (distinct inputs) → alert `oscillation` on the 4th; A,A,A,A does not yield `oscillation` |
| L4 | thrash: 5 calls of one tool with different inputs, 3 failed, in 8 → `thrash`; 2 failed → no alert |
| L5 | rank: a call that is both repeat and thrash → `thrash` |
| L6 | one alert per episode: continuing the same repeat for 10 more calls → no further alert |
| L7 | cooldown: a different hit within `cooldown` calls of the last alert → no alert and its key not in `live`; after `> cooldown` calls → alert |
| L8 | episode expiry: a key not refreshed for `> window` calls is dropped and can alert again |
| L9 | `observe` does not mutate its input state (deep-equal before/after); window length capped at `window` |
| L10 | `load_state`: missing, invalid JSON, `v: 2`, `n: true`, `win` item with `f: "x"` → `empty_state()` |
| L11 | `process_event` writes `<state_dir>/<sid>.json` on disk with `n` incremented per call, sid sanitized (`"a/b..c"` → `abc.json`), no `*.tmp` left |
| L12 | `process_event` returns `None` and creates no file for subagent payload, missing `tool_name`, non-dict |
| L13 | journal: on alert one JSONL row with keys exactly `{ts, session_id, kind, tool, n, key_sha256}`, no raw input value anywhere in the file; `log_path` in a not-yet-existing dir is created; unwritable `log_path` still returns the alert |
| L14 | state save failure (state_dir is a file) → `None`, no exception |
| L15 | `thresholds_from_env`: `BD_LOOP_REPEAT=2` honored; `"0"`, `"-1"`, `"x"` → default; `HAL_LOOP_REPEAT` ignored |
| L16 | CLI (real subprocess): 3 identical payloads via stdin with `--state-dir tmp` → third prints one line starting `<system-reminder>[LOOP DETECTED] repeat:`, rc 0; invalid stdin → empty stdout rc 0; `BD_LOOP_DETECTOR=0` → empty stdout, state dir not created |
| L17 | `PostToolUseFailure` marks `f: true` in the saved window |

Both (B):
| AC | Check |
|---|---|
| B1 | both modules appear in `core_manifest.json` `core_modules` and in `mypy-strict-modules.txt`; `core-boundary-lint.py --json` reports `ok: true` |
| B2 | neither module source contains a Cyrillic char or the substring `HAL_` |
