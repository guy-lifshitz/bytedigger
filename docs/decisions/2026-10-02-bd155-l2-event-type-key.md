# bd#155: `check_bd_l2` reads the on-disk `event_type` key, through one shared resolver

**Status:** r1 (frozen for gate) · **Tier:** 2 (one new private `conformance/` module, two checker edits, one new test file; Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `conformance/_event_type.event_type_of`, the single place a
conformance checker turns an event dict into its event-type name. `check_bd_l2` and `check_bd_l3` both
route through it.
**Source:** bd#155, found by the bd#141 item 4 (c/d/e) gate r1 MAJOR 3
(`2026-10-02-bd141-p4-bd-l3-cli-gate-r1.md`). Sibling of the bd_l3 fix in #156.

## §1 Problem (measured on `2222a1f`)

1. `EventLog.append` (`event_log.py:123-128`) writes `{"ts","run_id","event_type","payload"}`. Every
   production producer of an R2.x event goes through it: `red_test_outcome` and
   `red_stub_passability_violation` (`workflows/phase_5_implement.py`), `gate_decision`
   (`workflows/phase_6_review.py`), `baseline_delta_gate_verdict` (`workflows/_baseline_delta.py:107`).
2. `check_bd_l2` (`conformance/bd_l2.py:261`) reads `event.get("type")` only — the shape built by
   `conformance/harness.py` and the tests. Fed a real `events.jsonl`: every R2.x verdict is
   `not-checked`, `violations == ()`, every ADV-3..6 is `not-executed`. `failed` is unreachable on
   production input.
3. #156 fixed `check_bd_l3` inline (`bd_l3.py:267`: `EVENT_TYPE not in (event.get("event_type"),
   event.get("type"))`). Two checkers now carry two hand-written readings of the same key. A third
   checker will copy whichever it finds first — that is how bd_l2 got it wrong.
4. The #156 reading is a membership test over both keys: an event with `event_type: X` and
   `type: Y` counts as **both** X and Y. For bd_l2, which dispatches one event to six requirements,
   that would let one event feed two requirements.

## §2 Design

**2.1 Resolver (new file `engine_py/bytedigger_engine/conformance/_event_type.py`).** One function:

```
def event_type_of(event: "Mapping[str, object]") -> "object":
```

- If `event.get("event_type")` is a non-empty `str`, return it (the on-disk key wins).
- Otherwise return `event.get("type")` (the harness shape; may be `None` or any value).
- Pure: no I/O, no imports beyond `typing.TYPE_CHECKING`; module-level code is the function only.
  Importing it does no work (same rule as `conformance/__init__.py` AC-C1).

**2.2 `check_bd_l2`.** Line 261 becomes `event_type = _event_type_of(event)`, imported as
`from ._event_type import event_type_of as _event_type_of` (underscore-bound, per the B-2 note at
`bd_l2.py:32`). Nothing else in the checks changes.

**2.3 `check_bd_l3`.** Line 267 becomes `if _event_type_of(event) != _attest.EVENT_TYPE: continue`,
same underscore-bound import. Docstring updated to name the resolver and the precedence.
Behaviour change, deliberate: an event with `event_type` set to a foreign name and `type ==
EVENT_TYPE` is no longer read (one event, one type). No production writer emits both keys.

**2.4 Out of scope (§1v).** No bd_l2 CLI. No change to `harness.py`, `event_log.py`, producers,
`_CHECKS`, verdict tokens, `__all__` of either checker. No change to the non-conformance
`get("type")` readers (`llm_subprocess.py`, `close_gate.py`, `claim_evidence.py`): those read Claude
transcript blocks, a different format.

## §3 Acceptance criteria (test file `engine_py/tests/test_bd155_l2_event_type_key.py`)

Every event log in AC1–AC3 is written by the real `EventLog.append` into `tmp_path` and read back by
parsing the file's JSONL lines (no hand-built production-shape dicts).

- **AC1 (side-effect, §1l).** A log with one `red_test_outcome` event, payload
  `{"group":"x","exit_code":5,"n_passed":0,"n_failed":0,"phase":5}`. `check_bd_l2` over the parsed
  lines: `labels["verdict:R2.1"] == "failed"`; exactly one violation, starting `"R2.1:"`; `labels["ADV-4"] == "executed"`.
- **AC2 (every requirement, both directions).** Parametrized over R2.1–R2.6: a log holding one
  violating event for that requirement → that requirement `failed`, with a violation prefixed
  `"<req>:"`. A second log holding one passing event for each of the six → every requirement
  `passed`, `violations == ()`, `report.passed is True`. Payloads: R2.1 `n_passed=1,n_failed=1`
  vs `0,0`; R2.2 `hits=[]` vs `["m"]`; R2.3 one criterion `binds_observable_effect: True` vs `False`;
  R2.4 `raised="E", outcome="failed"` vs `outcome="passed"`; R2.5 `rows=[{"issue":"#1","status":"active"}]`
  vs `[{"issue":"","status":"active"}]`; R2.6 `verdict="pass", baseline_source="main"` vs
  `baseline_source=""`.
- **AC3 (adversaries).** The passing log of AC2 → `ADV-3`, `ADV-4`, `ADV-5`, `ADV-6` all `"executed"`.
- **AC4 (legacy shape unchanged).** Hand-built `{"type": <name>, "payload": ...}` events (the
  harness shape) give the same verdicts as AC2 for R2.1 fail and R2.1 pass.
- **AC5 (foreign type filtered).** An `EventLog.append("some_other_event", <R2.1-violating payload>)`
  log → R2.1 `not-checked`, `violations == ()`.
- **AC6 (precedence, both checkers).** `{"event_type": "some_other_event", "type": "red_test_outcome",
  "payload": <R2.1-violating>}` → bd_l2 R2.1 `not-checked`. `{"event_type": "some_other_event",
  "type": attest.EVENT_TYPE, "payload": the R3.3-violating payload of `test_bd141_p4_bd_l3_cli._payload("sonnet","haiku")`, rebuilt inline}`
  → bd_l3 `violations == ()`. Resolver unit cases: `event_type` non-empty str wins over `type`;
  `event_type` absent / `""` / `None` / `5` → returns `type`; neither key → `None`.
- **AC7 (single chokepoint).** AST scan of `bd_l2.py` and `bd_l3.py`: no `Call` whose func is an
  attribute `get` with first argument the string constant `"type"` or `"event_type"`, and no
  `Subscript` with either string as its slice. Both modules bind a name to
  `_event_type.event_type_of` (`bd_l2._event_type_of is _event_type.event_type_of`, same for bd_l3).
  Turns red if either checker re-inlines its own key read.
- **AC8 (surfaces unchanged).** `set(bd_l2.__all__) == {"REQUIREMENTS","AWAITING_PRODUCER","ENFORCEMENT","check_bd_l2","validate_report"}`; bd_l3 per
  `test_bd141_p4_bd_l3_cli::test_ac9`; public names of each equal its `__all__`. Importing
  `conformance._event_type` in a fresh interpreter adds no module outside `sys.modules` baseline
  other than itself and `bytedigger_engine`/`bytedigger_engine.conformance`.

## §4 Verify

- Scoped: the new test file. §1a siblings (all tests importing bd_l2 / bd_l3 / harness, 13 files,
  162 passed on `2222a1f`) stay green with no edits.
- Full suite: CI on the PR (delta vs base is the gate).

## §5 Files in scope

- NEW `engine_py/bytedigger_engine/conformance/_event_type.py`
- EDIT `engine_py/bytedigger_engine/conformance/bd_l2.py` (import + line 261)
- EDIT `engine_py/bytedigger_engine/conformance/bd_l3.py` (import + line 267 + docstring)
- NEW `engine_py/tests/test_bd155_l2_event_type_key.py`
- EDIT `CHANGELOG.md` (one line)
