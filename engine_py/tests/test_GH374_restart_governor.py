"""RED tests for GH374 Part A — restart-governor primitive + Q12 satisfaction-score parser.

Spec: SHARED/memory/Decisions/2026-07-07_GH374_restart_governor_spec.md (§3 A1-A13).
Class 3 SYSTEMATIC: unbounded restart without progress detection.

D1CF5FDF: `lib.restart_governor` does not exist yet — every import of it is
INSIDE the test body so this file COLLECTS cleanly (module genuinely absent,
verified via Glob pre-write: no `restart_governor.py` under lib/). Conftest
already puts ENGINE_ROOT + ENGINE_ROOT/workflows on sys.path (§1q singleton) —
no module-level sys.path manipulation here.

No UUT mocking (stub-passability lint, §1l): governor functions and
`_parse_satisfaction_score` are exercised directly against real state files /
real subprocess / real strings, never patched.
"""
from __future__ import annotations

import json
import subprocess

from helpers.engine_subprocess import engine_env
import sys
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
# A1-A9 — lib.restart_governor unit contract
# ═══════════════════════════════════════════════════════════════════════════


def test_a1_fresh_state_allows_and_record_start_creates_state_file(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_check, governor_record_start

    run_id, workflow = "run-a1", "echo"

    allow, deny_code = governor_check(tmp_path, run_id, workflow)
    assert allow is True, f"A1 FAIL: fresh state must allow, got deny_code={deny_code!r}"
    assert deny_code is None

    governor_record_start(tmp_path, run_id, workflow)
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    assert state_file.exists(), f"A1 FAIL: expected state file at {state_file}"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    # GH625: split-counter schema v2 — a single open start is recorded via
    # `pending`, not a flat "starts" counter (see test_gh625_restart_budget_split.py AC1).
    assert data.get("pending") is True, f"A1 FAIL: expected pending==True, got {data!r}"
    assert data.get("crash_starts", 0) == 0, f"A1 FAIL: expected crash_starts==0, got {data!r}"


def test_a2_cap_reached_denies_with_restart_cap_code(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_check, governor_record_start

    run_id, workflow = "run-a2", "echo"
    for _ in range(3):
        governor_record_start(tmp_path, run_id, workflow)

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is False, "A2 FAIL: 3 starts at cap=3 must deny"
    assert deny_code == "E_RESTART_CAP", f"A2 FAIL: expected E_RESTART_CAP, got {deny_code!r}"


def test_a3_repeated_identical_non_transient_code_short_circuits(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_check, governor_record_result

    run_id, workflow = "run-a3", "echo"
    governor_record_result(tmp_path, run_id, workflow, error_code="E_BOUNDARY_SUPPRESSION")
    governor_record_result(tmp_path, run_id, workflow, error_code="E_BOUNDARY_SUPPRESSION")

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is False, "A3 FAIL: two identical non-transient codes must short-circuit"
    assert deny_code == "E_RESTART_SHORT_CIRCUIT", (
        f"A3 FAIL: expected E_RESTART_SHORT_CIRCUIT, got {deny_code!r}"
    )


def test_a4_transient_codes_exempt_from_short_circuit(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_check, governor_record_result

    run_id, workflow = "run-a4", "echo"
    governor_record_result(tmp_path, run_id, workflow, error_code="E_LLM_TIMEOUT")
    governor_record_result(tmp_path, run_id, workflow, error_code="E_LLM_TIMEOUT")
    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is True, "A4 FAIL: repeated *_TIMEOUT code must NOT short-circuit"
    assert deny_code is None

    run_id2 = "run-a4b"
    governor_record_result(tmp_path, run_id2, workflow, error_code="E_CUSTOM")
    governor_record_result(tmp_path, run_id2, workflow, error_code="E_CUSTOM")
    allow2, deny_code2 = governor_check(
        tmp_path, run_id2, workflow, cap=3, extra_transient=frozenset({"E_CUSTOM"})
    )
    assert allow2 is True, "A4 FAIL: extra_transient-listed code must NOT short-circuit"
    assert deny_code2 is None


def test_a5_success_result_deletes_state_file_and_resets_count(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_check, governor_record_result, governor_record_start

    run_id, workflow = "run-a5", "echo"
    governor_record_start(tmp_path, run_id, workflow)
    governor_record_start(tmp_path, run_id, workflow)
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    assert state_file.exists()

    governor_record_result(tmp_path, run_id, workflow, error_code=None)
    assert not state_file.exists(), "A5 FAIL: ok/skip result must delete the state file"

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is True, "A5 FAIL: post-delete check must allow with a fresh count"
    assert deny_code is None


def test_a6_corrupt_state_file_self_heals_without_exception(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_check

    run_id, workflow = "run-a6", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_bytes(b"{not valid json at all")

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is True, "A6 FAIL: corrupt state must self-heal to allow, not raise"
    assert deny_code is None


def test_a7_e2e_subprocess_denies_at_cap_and_never_starts_workflow(tmp_path):
    """Real `python3 run.py` invocation (§1l anchor) — pre-staged deny state,
    no mocking. Seeds starts>=3 for (run_id, echo) BEFORE invoking (§1i:
    pre-stage deterministic state, never race)."""
    engine_root = Path(__file__).parent.parent
    run_py = engine_root / "bytedigger_engine" / "run.py"
    event_log_path = tmp_path / "events.jsonl"
    run_id, workflow = "gh374-a7-run", "echo"

    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps({"starts": 3, "error_codes": []}), encoding="utf-8"
    )

    proc = subprocess.run(
        [
            sys.executable, str(run_py),
            "--workflow", workflow,
            "--run-id", run_id,
            "--event-log", str(event_log_path),
            "--ctx-json", "{}",
        ], env=engine_env(),
        cwd=str(engine_root),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 1, (
        f"A7 FAIL: expected exit 1, got {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    stdout_lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    assert stdout_lines, f"A7 FAIL: expected JSON on stdout, got {proc.stdout!r}"
    payload = json.loads(stdout_lines[-1])
    assert payload.get("error_code") == "E_RESTART_CAP", (
        f"A7 FAIL: expected error_code E_RESTART_CAP, got {payload!r}"
    )

    assert event_log_path.exists(), "A7 FAIL: expected event log to be written on deny"
    events_text = event_log_path.read_text(encoding="utf-8")
    assert "restart_governor_denied" in events_text, (
        f"A7 FAIL: expected restart_governor_denied event, got {events_text!r}"
    )
    assert "workflow_started" not in events_text, (
        "A7 FAIL: workflow must NEVER be invoked once denied "
        f"(found workflow_started in {events_text!r})"
    )


def test_a8_governor_gate_fails_open_on_broken_state_dir(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_gate

    # dirname(event_log_path) resolves to a plain FILE, not a directory —
    # real, unmocked broken-filesystem condition (§1n fail-open).
    broken_dir = tmp_path / "not-a-real-dir"
    broken_dir.write_text("i am a file, not a directory", encoding="utf-8")
    event_log_path = str(broken_dir / "events.jsonl")

    result = governor_gate(event_log_path, "run-a8", "echo", None)
    assert result is None, f"A8 FAIL: broken state_dir must fail-open (None), got {result!r}"


def test_a9_governor_gate_disabled_via_org_config_writes_nothing(tmp_path):
    from bytedigger_engine.lib.restart_governor import governor_gate

    event_log_path = str(tmp_path / "events.jsonl")
    result = governor_gate(
        event_log_path, "run-a9", "echo", {"restart_governor": {"disable": True}}
    )
    assert result is None, f"A9 FAIL: disabled governor must return None, got {result!r}"
    assert list(tmp_path.glob("restart-governor-*.json")) == [], (
        "A9 FAIL: disabled governor must not write any state file"
    )


# ═══════════════════════════════════════════════════════════════════════════
# A10 — structural source-order assert on run.py wiring
# ═══════════════════════════════════════════════════════════════════════════


def test_a10_run_py_calls_governor_gate_before_execute_and_records_result():
    run_py_path = Path(__file__).parent.parent / "bytedigger_engine" / "run.py"
    source = run_py_path.read_text(encoding="utf-8")

    assert "governor_gate(" in source, "A10 FAIL: run.py must call governor_gate("
    assert "governor_record_result(" in source, (
        "A10 FAIL: run.py must call governor_record_result("
    )

    idx_run_id = source.index("_resolved_run_id")
    idx_gate = source.index("governor_gate(")
    idx_execute = source.index("execute_durable_workflow(")
    assert idx_run_id < idx_gate < idx_execute, (
        "A10 FAIL: governor_gate( must appear after run-id resolution and before "
        f"execute_durable_workflow( — indices run_id={idx_run_id} gate={idx_gate} "
        f"execute={idx_execute}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# A11-A13 — Q12 `_parse_satisfaction_score` last-line-anchored-match parser
# ═══════════════════════════════════════════════════════════════════════════


def test_a11_last_line_anchored_score_wins_over_earlier_one(tmp_path):
    from bytedigger_engine.workflows.phase_6_review import _parse_satisfaction_score

    raw = "SCORE: 90\nsome intervening evaluator prose\nSCORE: 62\n"
    result = _parse_satisfaction_score(raw)
    assert result == 62, (
        f"A11 FAIL: last line-anchored SCORE must win over an earlier one, got {result!r}"
    )


def test_a12_mid_line_and_prompt_echo_score_yield_none():
    from bytedigger_engine.workflows.phase_6_review import _parse_satisfaction_score

    assert _parse_satisfaction_score("the SCORE: 90 rubric") is None, (
        "A12 FAIL: mid-line (non-line-anchored) SCORE must NOT match"
    )
    assert _parse_satisfaction_score("  SCORE: <0-100>") is None, (
        "A12 FAIL: prompt-echo schema line alone must yield None"
    )


def test_a13_back_compat_score_cases_unchanged():
    from bytedigger_engine.workflows.phase_6_review import _parse_satisfaction_score

    assert _parse_satisfaction_score("SCORE: 87") == 87
    assert _parse_satisfaction_score("**SCORE: 92**") == 92
    assert _parse_satisfaction_score("score: 50") == 50  # case-insensitive
    assert _parse_satisfaction_score("SCORE:0") == 0
    assert _parse_satisfaction_score("SCORE: 100") == 100
    assert _parse_satisfaction_score("SCORE: 150") is None  # out of range
    assert _parse_satisfaction_score("no score here") is None  # missing
