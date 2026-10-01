# bd#141 item 3 — check ladder: a pre-screen rung in front of the validation gate

**Status: r1 (draft, before gate)** · **Tier:** 3 (one new engine prod `.py` module + a shadow call in
`workflows/phase_5_implement.py`, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `check_ladder.prescreen` — the one function that turns (script findings, gate input
text, classifier config) into a pre-screen verdict. The CLI, the phase-5 shadow call and every host
adapter call it and add nothing but I/O.
**Side of the seam (decision 2026-07-26 §7.1 / §7.4):** engine. The host keeps: which classifier
binary to run (HAL: Jev / TypeSafe through `jev-shadow`), its credentials, the DPA, the budget, and
where the CLI's journal goes. HAL's thin adapter and its host-controls registry entry are hal-v2 work
(pairs with hal-v2#2277), not this PR.
**Source:** bd#141 item 3; hal-v2#2264 item 1, hal-v2#2180 (gate pre-screen), hal-v2#2165 (Jev A/B,
closed), hal-v2 PR #2279 (HAL shadow seam `HAL_PRESCREEN_JEV_BIN` in `lot-preflight`).

## §1 Problem (measured on `07824db`)

1. The validation gate (`phase_5_implement._invoke_validation_llm`) is one Opus call with
   `hard_gate=True`. Everything in front of it is deterministic prompt assembly
   (`_build_validation_prompt`, `facts_pack.spec_facts_block`, `_check_red_executable`). There is no
   cheap rung between "script" and "Opus": a spec/RED pair with a mechanical defect pays a full Opus
   round to be told so.
2. HAL numbers (hal-v2#2180, 24–30.09): `hal-gate-agent` 358 spawns, $575/week, 100 % Opus; a typical
   run is r1 REJECTED → r2 APPROVED, r1 blockers often mechanical. hal-v2#2165: Jev (typed
   classifier, ~$0.04, ~0.3 s) is not a model replacement — it is a rung between script and LLM.
3. HAL already has a shadow seam (PR #2279) but no engine primitive: the ladder rule ("a cheap rung
   may only send work back, never approve; the gate stays final") lives nowhere in code.
4. Grep on `07824db`: `prescreen`, `pre-screen`, `jev` — zero hits in the tree; "ladder"/"rung" only
   as exit-code / event-collection metaphors.

## §2 Design

`engine_py/bytedigger_engine/check_ladder.py`, stdlib-only, listed in `core_manifest.json`
`core_modules` and in `mypy-strict-modules.txt`, no Cyrillic, no `HAL_*` env, no vendor name in code
(the classifier is any command), with a `if __name__ == "__main__":` CLI.

### Constants
- `RUNGS = ("script", "classifier", "llm", "gate")` — cost order. Exported, a tuple.
- `OUTCOMES = ("reject", "escalate")` — the only outcomes `prescreen` can return. There is no
  approve outcome: the gate is the only rung that approves.
- `MODES = ("shadow", "enforce")`; default `"shadow"`.
- `DEFAULT_THRESHOLD = 0.9`, `DEFAULT_TIMEOUT_S = 30`.

### Finding
A dict `{"rule": str, "severity": "MAJOR" | "MINOR", "detail": str}`. Input validation in op2.

### op1 — `run_classifier(cmd, payload, timeout_s) -> ClassifierResult`
`cmd` is an argv list (`list[str]`, non-empty) or `None`.
`ClassifierResult = {"status", "label", "confidence", "reasons", "rc", "ms", "cost_usd"}`.
1. `cmd is None` → `status="off"`, all other fields `None` except `ms=0`. No process is spawned.
2. Spawn `cmd` with `payload` as one JSON document on stdin (UTF-8), `shell=False`, stdout/stderr
   captured, `timeout=timeout_s`.
3. Spawn failure (`OSError`, e.g. missing binary) → `status="error"`, `rc=None`.
4. Timeout → `status="timeout"`, `rc=None`; the child is killed (the `subprocess.run` timeout path).
5. Non-zero exit → `status="error"`, `rc=<code>`.
6. Exit 0: the first non-empty stdout line must parse as a JSON object with `label` in
   `{"reject", "pass"}` and `confidence` a real number (not bool) in `[0, 1]`. Optional `reasons`
   (list of str; anything else → `[]`), optional `cost_usd` (non-negative real, not bool; anything
   else → `None`). Valid → `status="ok"`. Anything else (no line, not JSON, not an object, unknown
   label — including `"approve"` — bad confidence) → `status="error"`, `label=None`.
7. `ms` is wall time of the spawn in integer milliseconds (≥ 0) for every non-`off` status.
8. Never raises.

### op2 — `prescreen(findings, gate_input, classifier_cmd=None, mode="shadow", threshold=0.9, timeout_s=30) -> Verdict`
`Verdict = {"outcome", "rung", "mode", "findings", "classifier"}`; `findings` is the list of MAJOR
findings that drove a script reject (else `[]`), `classifier` is the op1 result.

Argument validation (raises `ValueError`, the CLI maps it to rc 2): `mode` not in `MODES`;
`threshold` not a real in `[0, 1]`; `timeout_s` not a positive real; a finding that is not a dict,
or whose `rule`/`detail` is not a str, or whose `severity` is not `MAJOR`/`MINOR`.

Steps:
1. `majors` = findings with `severity == "MAJOR"`, in input order.
2. Classifier call decision: in `shadow` mode the classifier is **always** called (when configured) —
   shadow exists to measure recall over every gate input, so a script reject must not hide the
   classifier's answer. In `enforce` mode it is called only when `majors` is empty (a script reject
   already decides; the cheaper rung short-circuits).
3. Payload sent to the classifier: `{"gate_input": gate_input, "findings": findings}` (all findings,
   MAJOR and MINOR).
4. Outcome:
   - `majors` non-empty → `outcome="reject"`, `rung="script"`, `findings=majors` (both modes; the
     script rung is deterministic and never shadow).
   - else `mode == "enforce"` and classifier `status == "ok"`, `label == "reject"`,
     `confidence >= threshold` → `outcome="reject"`, `rung="classifier"`.
   - else → `outcome="escalate"`, `rung=None`.
   In `shadow` mode the classifier never changes the outcome.
5. Fail-open: any classifier status other than `ok` (off, error, timeout) → the outcome is decided by
   the script rung alone. `prescreen` never raises except the argument `ValueError`s above.

### op3 — CLI `python -m bytedigger_engine.check_ladder prescreen ...`
Flags: `--gate-input PATH` (required; file read as UTF-8 text), `--findings PATH` (optional; a JSON
array of findings; absent → `[]`), `--classifier-cmd JSON` (optional; a JSON array of strings, the
argv), `--mode shadow|enforce` (default shadow), `--threshold F` (default 0.9),
`--timeout-s F` (default 30), `--log PATH` (optional JSONL journal).
- Prints exactly one JSON line: the op2 verdict. Exit 0 on any verdict (reject included — the verdict
  is data; the host owns policy).
- Exit 2, message on stderr, nothing on stdout: unknown subcommand/flag, missing `--gate-input`,
  unreadable gate-input or findings file, findings not valid JSON / not a list, `--classifier-cmd`
  not a non-empty JSON array of strings, any op2 `ValueError`.
- `--log`: appends one line `{"ts": <UTC ISO-8601 with Z>, "verdict": <verdict>, "gate_input_sha256":
  <hex of the UTF-8 bytes>}` with a single `O_APPEND` write; parent dir created. The journal holds no
  gate-input text and no classifier `reasons` (they may quote spec text) — `classifier.reasons` is
  replaced by its length (`"reasons": <int>`) in the logged copy only. A journal I/O error is reported
  on stderr and does not change stdout or the exit code.

### op4 — phase-5 shadow call (`workflows/phase_5_implement.py`, `_invoke_validation_llm`)
Config: `ctx.org_config["prescreen"]` — absent / not a dict / no `classifier_cmd` → no-op (today's
behaviour, byte-for-byte: no event, `extra_data` unchanged). When present:
`{"classifier_cmd": [str, ...], "timeout_s": number?}`.
1. Before `invoke_llm_subprocess`, call `check_ladder.prescreen(findings=[], gate_input=prev.data["prompt"],
   classifier_cmd=..., mode="shadow", timeout_s=...)`.
2. Emit one event `prescreen_verdict` via `_emit_safe` with payload
   `{"outcome", "rung", "mode", "classifier_status", "classifier_label", "classifier_confidence",
   "classifier_ms", "cost_usd", "cycle", "phase": 5}`. `cost_usd` is the classifier's (token-ledger
   input; `derive_state` aggregation is out of scope).
3. Add `"prescreen": <verdict without classifier.reasons>` to `extra_data`.
4. The Opus call is made exactly as before: same prompt, same model, `hard_gate=True`. The phase-5
   call is **shadow-only in this PR**; `org_config["prescreen"]["mode"]` is ignored here (enforce on
   the gate path comes after the shadow recall measurement, hal-v2#2264: recall ≥ 90 % on the
   mechanical class).
5. Any exception from the pre-screen path (bad config shape, `ValueError`) is swallowed after one
   `prescreen_verdict` event with `classifier_status="config-error"`; the Opus call still runs.

## §3 Acceptance criteria

- **L1** `RUNGS == ("script","classifier","llm","gate")`, `OUTCOMES == ("reject","escalate")`,
  `MODES == ("shadow","enforce")`.
- **L2** No path returns an approve: over every combination of {no findings, MINOR only, MAJOR} ×
  {off, ok-pass, ok-reject high/low conf, error, timeout} × {shadow, enforce}, `outcome ∈ OUTCOMES`.
- **L3** MAJOR finding → `reject`/`script`, `findings` = only the MAJORs in order, both modes.
- **L4** MINOR-only, classifier off → `escalate`, `rung None`.
- **L5** enforce, no MAJOR, classifier ok `reject` conf ≥ threshold → `reject`/`classifier`;
  conf == threshold counts; conf < threshold → `escalate`.
- **L6** shadow, no MAJOR, classifier ok `reject` conf 1.0 → `escalate` (classifier recorded in
  `classifier`, outcome unchanged).
- **L7** shadow + MAJOR → classifier **is** spawned (proved by a side-effect file the fake classifier
  writes); enforce + MAJOR → classifier **not** spawned (side-effect file absent).
- **L8** classifier receives `{"gate_input", "findings"}` on stdin exactly (fake writes stdin to a file;
  test parses it).
- **L9** classifier statuses: missing binary → `error`/`rc None`; exit 3 → `error`/`rc 3`;
  sleep past `timeout_s` → `timeout` within `timeout_s + 5` s; non-JSON line → `error`;
  `label "approve"` → `error`; confidence `1.5` / `true` / `"0.9"` → `error`; valid → `ok`;
  `cost_usd` passed through when valid, `None` when negative/bool/string; `reasons` non-list → `[]`.
- **L10** classifier error/timeout in enforce with no MAJOR → `escalate` (fail-open).
- **L11** `prescreen` `ValueError` on: bad mode, threshold 1.5 / `True`, timeout 0, finding missing
  `rule`, severity `"BLOCKER"`.
- **L12** CLI happy path prints exactly one JSON line equal to the op2 verdict for the same inputs;
  rc 0 for both `reject` and `escalate`.
- **L13** CLI rc 2 + empty stdout for: no subcommand, unknown flag, missing `--gate-input`, missing
  gate-input file, findings not JSON, findings an object, `--classifier-cmd '[]'`,
  `--classifier-cmd '"x"'`, `--mode block`.
- **L14** CLI `--log` into a non-existent subdir: dir created, one line appended per run (two runs →
  two lines), line has `ts` ending `Z`, `gate_input_sha256` equal to sha256 of the file bytes,
  `verdict.classifier.reasons` an int, and the gate-input text does not appear in the journal.
- **L15** CLI `--log` pointing at a path whose parent is a regular file → stdout verdict unchanged,
  rc 0, stderr non-empty.
- **L16** phase-5, no `prescreen` config: `_invoke_validation_llm` calls `invoke_llm_subprocess` with
  the same kwargs as before (no `prescreen` key in `extra_data`), no `prescreen_verdict` event.
- **L17** phase-5 with `prescreen.classifier_cmd` = a real fake classifier answering `reject` conf 1.0:
  one `prescreen_verdict` event with `outcome "escalate"`, `classifier_status "ok"`; the LLM call
  still happens with the identical prompt and `hard_gate=True`; `extra_data["prescreen"]` present
  with no `reasons` list. Even with `"mode": "enforce"` in config, the outcome is `escalate` and the
  gate runs.
- **L18** phase-5 with `prescreen = {"classifier_cmd": "not-a-list"}` → one event with
  `classifier_status "config-error"`, gate still runs, no exception.
- **L19** Module hygiene: listed in `core_manifest.json` `core_modules` and
  `mypy-strict-modules.txt`; `core-boundary-lint --json` clean; no Cyrillic; imports stdlib only.

## §4 Declared limits

- The classifier contract is binary (`reject`/`pass`); HAL's Jev `Choice` maps onto it in the
  adapter. Multi-label findings from the classifier are out of scope.
- Script-rung findings come from the caller. In phase 5 this PR passes `[]` (the existing
  deterministic checks already run as their own steps before the gate); wiring them as script-rung
  findings is a follow-up.
- No enforce on the gate path, no `derive_state` cost aggregation, no Haiku/Sonnet "llm" rung call
  (the rung is named so hosts and later items share the order).
- `gate_input` is sent to the classifier as-is; redaction is the host's job (HAL's `jev-shadow`
  already redacts).

## §5 Scope

Paths: `engine_py/bytedigger_engine/check_ladder.py` (new),
`engine_py/bytedigger_engine/workflows/phase_5_implement.py` (op4 only),
`engine_py/core_manifest.json`, `engine_py/bytedigger_engine/mypy-strict-modules.txt`, `CHANGELOG.md`.
RED: `engine_py/tests/test_bd141_check_ladder.py`.
Siblings (§1a): core-boundary tests, gh292 strict ramp, package namespace, contracts, the phase-5
validation tests that drive `_invoke_validation_llm` (names found by the RED agent and listed in the
RED file header).
NOT in scope: `llm_subprocess.py`, `derive_state.py`, any HAL file.
