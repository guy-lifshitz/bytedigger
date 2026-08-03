"""RED tests for GH923 Lot 4 (ported from hal) — receipts line generator.

Port of hal GH923 lot 4 (PR #964).

lib/receipts_line.py and generate_receipts.py do NOT exist yet (§1q /
D1CF5FDF): ALL references to these NEW symbols are deferred to inside test
bodies so this file COLLECTS cleanly and fails at assert/import time, never
at collection time.

§1l forcing function: every AC anchors on a real file read/append, a real
exit code, or a real subprocess invocation. generate_receipts / render_receipts
/ collect_terminal / load_incidents / main ARE the UUT and are never
mocked/patched here.
"""
from __future__ import annotations

import json
import os
import subprocess

from helpers.engine_subprocess import engine_env
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


def _ev(run_id, event_type, payload, ts="2026-07-17T00:00:00Z"):
    return {"ts": ts, "run_id": run_id, "event_type": event_type, "payload": payload}


def _write_jsonl(path: Path, rows):
    """rows: list of dicts (json.dumps'd) or raw strings (garbage lines)."""
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _incident_rec(run_id, phase, step_name, error_code="E_X", classification="unknown",
                   workaround=None, cycle=1, status="error"):
    return {
        "ts": "2026-07-17T00:00:00Z", "run_id": run_id, "phase": phase,
        "step_name": step_name, "cycle": cycle, "status": status,
        "error_code": error_code, "error": "boom", "wall_ms": 1, "tokens": None,
        "classification": classification, "workaround": workaround,
    }


def _rich_fixture(tmp_path):
    """Canonical rich fixture from spec §3 preamble. Returns (events_path, ledger_path)."""
    events_path = Path(tmp_path).resolve() / "events.jsonl"
    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"

    rollup_payload = {
        "run_id": "r1", "calls": 16, "tokens_in": 200000, "tokens_out": 105900,
        "cost_usd": 16.6499, "by_phase": {}, "by_cycle": {"1": {}, "2": {}},
    }
    events = [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r1", "step_finished", {"step_name": "s2", "status": "ok", "duration_ms": 1}),
        _ev("r1", "step_finished", {"step_name": "s3", "status": "error", "duration_ms": 1}),
        _ev("r1", "phase_retry_triggered", {"error_code": "E_X"}),
        _ev("r1", "phase_retry_triggered", {"error_code": "E_Y"}),
        _ev("r1", "workflow_cost_rollup", rollup_payload),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "error", "wall_ms": 1542000}),
        _ev("r2", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
    ]
    _write_jsonl(events_path, events)

    ledger = [
        _incident_rec("r1", "p5", "green", error_code="E_X", classification="gate-FP",
                       workaround="restart step"),
        _incident_rec("r2", "p1", "s1"),
    ]
    _write_jsonl(ledger_path, ledger)
    return events_path, ledger_path


# ─── AC1: load_incidents — filter, missing file, garbage-tolerant ───────────

def test_ac1_load_incidents_filters_run_id_in_file_order(tmp_path):
    from bytedigger_engine.lib.receipts_line import load_incidents  # noqa: PLC0415

    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"
    r1a = _incident_rec("r1", "p1", "s1")
    r2 = _incident_rec("r2", "p1", "s1")
    r1b = _incident_rec("r1", "p2", "s2")
    garbage = "not json{{{"
    _write_jsonl(ledger_path, [json.dumps(r1a), json.dumps(r2), garbage, json.dumps(r1b)])

    result = load_incidents(ledger_path, "r1")
    assert len(result) == 2, f"AC1 FAIL: expected 2 r1 rows, got {len(result)}"
    assert result[0]["step_name"] == "s1", "AC1 FAIL: file order not preserved (row 0)"
    assert result[1]["step_name"] == "s2", "AC1 FAIL: file order not preserved (row 1)"


def test_ac1_load_incidents_missing_file_returns_empty_list():
    from bytedigger_engine.lib.receipts_line import load_incidents  # noqa: PLC0415

    result = load_incidents(Path("/nonexistent/dir/incidents.jsonl"), "r1")
    assert result == [], f"AC1 FAIL: missing file must yield [], got {result!r}"


def test_ac1_load_incidents_binary_garbage_file_no_raise(tmp_path):
    from bytedigger_engine.lib.receipts_line import load_incidents  # noqa: PLC0415

    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"
    ledger_path.write_bytes(b"\x00\x01\xff\xfe garbage not utf8 at all \x00")

    result = load_incidents(ledger_path, "r1")  # must not raise
    assert result == [], f"AC1 FAIL: binary-garbage file must yield [], got {result!r}"


# ─── AC2: collect_terminal — last workflow_finished, None cases ─────────────

def test_ac2_collect_terminal_returns_last_workflow_finished(tmp_path):
    from bytedigger_engine.lib.receipts_line import collect_terminal  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    rows = [
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "error", "wall_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 2}),
    ]
    _write_jsonl(events_path, rows)

    result = collect_terminal(events_path, "r1")
    assert result is not None, "AC2 FAIL: expected a terminal dict"
    assert result["status"] == "ok", f"AC2 FAIL: expected the LAST row (status=ok), got {result!r}"
    for key in ("ts", "workflow_name", "status", "wall_ms"):
        assert key in result, f"AC2 FAIL: missing key {key!r} in terminal dict: {result!r}"


def test_ac2_collect_terminal_no_matching_run_returns_none(tmp_path):
    from bytedigger_engine.lib.receipts_line import collect_terminal  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    rows = [_ev("r2", "workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 1})]
    _write_jsonl(events_path, rows)

    result = collect_terminal(events_path, "r1")
    assert result is None, f"AC2 FAIL: no matching run must yield None, got {result!r}"


def test_ac2_collect_terminal_missing_file_returns_none_no_raise():
    from bytedigger_engine.lib.receipts_line import collect_terminal  # noqa: PLC0415

    result = collect_terminal(Path("/nonexistent/dir/events.jsonl"), "r1")  # must not raise
    assert result is None, f"AC2 FAIL: missing file must yield None, got {result!r}"


# ─── AC3: generate_receipts on rich fixture — exact-format assertions ───────

def test_ac3_generate_receipts_rich_fixture_exact_format(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path, ledger_path = _rich_fixture(tmp_path)

    exit_code, stdout, stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path,
    )
    assert exit_code == 0, f"AC3 FAIL: expected exit 0, got {exit_code}, stderr={stderr!r}"
    assert stderr == "", f"AC3 FAIL: expected empty stderr, got {stderr!r}"

    h2 = stdout.split("\n")[0]
    assert "r1" in h2, f"AC3 FAIL: H2 must contain run_id, got {h2!r}"
    assert "(wf)" in h2, f"AC3 FAIL: H2 must contain workflow_name in parens, got {h2!r}"
    assert "✗ FAIL @ E_X" in h2, f"AC3 FAIL: H2 must show FAIL with last incident error_code, got {h2!r}"

    assert "wall ≈ 25.7 min" in stdout, f"AC3 FAIL: wall segment mismatch, got:\n{stdout}"
    assert "Steps: 2/3 ok · retries: 2" in stdout, f"AC3 FAIL: steps/retries line mismatch, got:\n{stdout}"
    assert "`p5/green`" in stdout, f"AC3 FAIL: incident bullet missing phase/step, got:\n{stdout}"
    assert "E_X" in stdout.split("Incidents:")[1], "AC3 FAIL: incident bullet missing error_code"
    assert "[gate-FP]" in stdout, f"AC3 FAIL: incident bullet missing classification, got:\n{stdout}"
    assert "workaround: restart step" in stdout, f"AC3 FAIL: workaround text missing, got:\n{stdout}"
    assert "**$16.65 / 16 calls / 105.9K tokens_out / 2 cycles**" in stdout, (
        f"AC3 FAIL: economics line mismatch, got:\n{stdout}"
    )
    assert stdout.endswith("\n") and not stdout.endswith("\n\n"), (
        f"AC3 FAIL: block must end with exactly one trailing newline, got tail={stdout[-5:]!r}"
    )


# ─── AC4: all-ok run, no incidents ───────────────────────────────────────────

def test_ac4_all_ok_run_no_incidents(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 60000}),
    ])
    _write_jsonl(ledger_path, [])

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path,
    )
    assert exit_code == 0, f"AC4 FAIL: expected exit 0, got {exit_code}"
    h2 = stdout.split("\n")[0]
    assert "✅ DONE" in h2, f"AC4 FAIL: expected DONE marker in H2, got {h2!r}"
    assert "- Incidents: none" in stdout, f"AC4 FAIL: expected 'Incidents: none' line, got:\n{stdout}"


# ─── AC5: escalate status, falsy error_code → bare status + no-code ─────────

def test_ac5_escalate_falsy_error_code_bare_status_and_no_code(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "error", "duration_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "escalate", "wall_ms": 1000}),
    ])
    _write_jsonl(ledger_path, [
        _incident_rec("r1", "p1", "s1", error_code=None, classification="unknown"),
    ])

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path,
    )
    assert exit_code == 0, f"AC5 FAIL: expected exit 0, got {exit_code}"
    h2 = stdout.split("\n")[0]
    assert "✗ FAIL @ escalate" in h2, f"AC5 FAIL: expected bare-status fallback in H2, got {h2!r}"
    assert "no-code" in stdout, f"AC5 FAIL: expected 'no-code' in incident bullet, got:\n{stdout}"


# ─── AC6: no terminal workflow_finished → exit 4, target not created ────────

def test_ac6_no_terminal_exits_4_empty_stdout_stderr_mentions_run_id(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    _write_jsonl(events_path, [_ev("r2", "step_finished", {"step_name": "s1", "status": "ok"})])

    exit_code, stdout, stderr = generate_receipts(run_id="r1", events_path=events_path)
    assert exit_code == 4, f"AC6 FAIL: expected exit 4, got {exit_code}"
    assert stdout == "", f"AC6 FAIL: expected empty stdout, got {stdout!r}"
    assert "r1" in stderr, f"AC6 FAIL: stderr must mention run_id, got {stderr!r}"


def test_ac6_no_terminal_with_append_path_target_not_created(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    _write_jsonl(events_path, [_ev("r2", "step_finished", {"step_name": "s1", "status": "ok"})])
    append_path = Path(tmp_path).resolve() / "target.md"

    exit_code, _stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, append_path=append_path,
    )
    assert exit_code == 4, f"AC6 FAIL: expected exit 4, got {exit_code}"
    assert not append_path.exists(), "AC6 FAIL: append target must NOT be created when terminal is missing"


# ─── AC7: append writer-behavior enumeration ─────────────────────────────────

def test_ac7_append_to_absent_file_creates_with_block_content(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path, ledger_path = _rich_fixture(tmp_path)
    append_path = Path(tmp_path).resolve() / "receipts.md"

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
    )
    assert exit_code == 0, f"AC7 FAIL: expected exit 0, got {exit_code}"
    assert append_path.exists(), "AC7 FAIL: append target must be created"
    assert append_path.read_text(encoding="utf-8") == stdout, (
        "AC7 FAIL: created file content must equal stdout block exactly"
    )


def test_ac7_append_to_single_newline_terminated_existing_file(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path, ledger_path = _rich_fixture(tmp_path)
    append_path = Path(tmp_path).resolve() / "receipts.md"
    old_text = "# Existing header\n\nsome text\n"
    append_path.write_text(old_text, encoding="utf-8")

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
    )
    assert exit_code == 0, f"AC7 FAIL: expected exit 0, got {exit_code}"
    new_text = append_path.read_text(encoding="utf-8")
    assert new_text == old_text + "\n" + stdout, (
        f"AC7 FAIL: expected exactly one blank line before H2, got tail={new_text[-200:]!r}"
    )


def test_ac7_append_to_no_trailing_newline_existing_file(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path, ledger_path = _rich_fixture(tmp_path)
    append_path = Path(tmp_path).resolve() / "receipts.md"
    old_text = "# Existing header\n\nsome text no newline"
    append_path.write_text(old_text, encoding="utf-8")

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
    )
    assert exit_code == 0, f"AC7 FAIL: expected exit 0, got {exit_code}"
    new_text = append_path.read_text(encoding="utf-8")
    assert new_text == old_text + "\n\n" + stdout, (
        f"AC7 FAIL: expected double newline separator, got tail={new_text[-200:]!r}"
    )


# ─── AC8: idempotent re-entry — same run appended twice ─────────────────────

def test_ac8_repeat_append_same_run_exits_6_file_unchanged(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path, ledger_path = _rich_fixture(tmp_path)
    append_path = Path(tmp_path).resolve() / "receipts.md"

    exit1, _s1, _e1 = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
    )
    assert exit1 == 0, f"AC8 FAIL: first run expected exit 0, got {exit1}"
    content_after_run1 = append_path.read_text(encoding="utf-8")

    exit2, stdout2, stderr2 = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
    )
    assert exit2 == 6, f"AC8 FAIL: second (idempotent) run expected exit 6, got {exit2}"
    assert stdout2 == "", f"AC8 FAIL: idempotent re-entry stdout must be empty, got {stdout2!r}"
    assert "r1" in stderr2, f"AC8 FAIL: stderr must mention run_id, got {stderr2!r}"

    content_after_run2 = append_path.read_text(encoding="utf-8")
    assert content_after_run1 == content_after_run2, (
        "AC8 FAIL: idempotent re-entry must leave the file byte-identical"
    )


def test_ac8_different_run_id_appends_fine(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path, ledger_path = _rich_fixture(tmp_path)
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r1", "step_finished", {"step_name": "s2", "status": "ok", "duration_ms": 1}),
        _ev("r1", "step_finished", {"step_name": "s3", "status": "error", "duration_ms": 1}),
        _ev("r1", "phase_retry_triggered", {"error_code": "E_X"}),
        _ev("r1", "phase_retry_triggered", {"error_code": "E_Y"}),
        _ev("r1", "workflow_cost_rollup", {
            "run_id": "r1", "calls": 16, "tokens_in": 200000, "tokens_out": 105900,
            "cost_usd": 16.6499, "by_phase": {}, "by_cycle": {"1": {}, "2": {}},
        }),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "error", "wall_ms": 1542000}),
        _ev("r2", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r2", "workflow_finished", {"workflow_name": "wf2", "status": "ok", "wall_ms": 60000}),
    ])
    append_path = Path(tmp_path).resolve() / "receipts.md"

    exit1, _s1, _e1 = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
    )
    assert exit1 == 0, f"AC8 FAIL: first run (r1) expected exit 0, got {exit1}"

    exit2, _s2, _e2 = generate_receipts(
        run_id="r2", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
    )
    assert exit2 == 0, f"AC8 FAIL: different run_id (r2) expected exit 0, got {exit2}"


# ─── AC9: unwritable append target → exit 5, no raise ───────────────────────

def test_ac9_readonly_parent_exits_5_no_raise(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("AC9: chmod read-only has no effect when running as root")

    events_path, ledger_path = _rich_fixture(tmp_path)
    ro_dir = Path(tmp_path).resolve() / "ro"
    ro_dir.mkdir()
    append_path = ro_dir / "receipts.md"

    os.chmod(ro_dir, 0o500)
    try:
        exit_code, _stdout, stderr = generate_receipts(
            run_id="r1", events_path=events_path, ledger_path=ledger_path, append_path=append_path,
        )
        assert exit_code == 5, f"AC9 FAIL: expected exit 5 on unwritable target, got {exit_code}"
        assert stderr != "", "AC9 FAIL: stderr must explain the append failure"
        assert not append_path.exists(), "AC9 FAIL: append target must not have been created"
    finally:
        os.chmod(ro_dir, 0o700)


def test_ac9_stdout_mode_unaffected_by_failed_append_fixture(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path, ledger_path = _rich_fixture(tmp_path)

    exit_code, stdout, stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path,
    )
    assert exit_code == 0, f"AC9 FAIL: stdout-mode (no append_path) expected exit 0, got {exit_code}"
    assert stdout != "", "AC9 FAIL: stdout-mode must still produce a block"
    assert stderr == "", f"AC9 FAIL: stdout-mode must have empty stderr, got {stderr!r}"


# ─── AC10: §1f entry point shape + subprocess smoke + argparse ──────────────

def test_ac10_entry_point_imports_main_no_local_defs():
    entry_path = HERE.parent / "bytedigger_engine" / "generate_receipts.py"
    assert entry_path.exists(), f"AC10 FAIL: entry point does not exist yet: {entry_path}"
    source = entry_path.read_text(encoding="utf-8")
    assert "from bytedigger_engine.lib.receipts_line import main" in source, (
        "AC10 FAIL: generate_receipts.py must import main from lib.receipts_line"
    )
    assert "def " not in source, (
        "AC10 FAIL: generate_receipts.py must contain no helper bodies (§1f) — found 'def '"
    )


def test_ac10_entry_point_subprocess_smoke(tmp_path):
    entry_path = HERE.parent / "bytedigger_engine" / "generate_receipts.py"
    events_path, ledger_path = _rich_fixture(tmp_path)

    result = subprocess.run(
        [sys.executable, str(entry_path), "--run", "r1",
         "--events", str(events_path), "--ledger", str(ledger_path)], env=engine_env(),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"AC10 FAIL: subprocess exit expected 0, got {result.returncode}, stderr={result.stderr!r}"
    )
    assert "r1" in result.stdout.split("\n")[0], (
        f"AC10 FAIL: expected H2 header with run_id on stdout, got {result.stdout!r}"
    )


def test_ac10_main_missing_required_args_exits_2():
    from bytedigger_engine.lib.receipts_line import main  # noqa: PLC0415

    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2, f"AC10 FAIL: expected SystemExit 2, got {excinfo.value.code}"


# ─── AC11: env seam — BD_INCIDENT_LOG honored when --ledger omitted ────────

def test_ac11_main_honors_env_seam_when_ledger_omitted(tmp_path, monkeypatch):
    from bytedigger_engine.lib.receipts_line import main  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 1000}),
    ])

    seam_path = Path(tmp_path).resolve() / "seam" / "incidents.jsonl"
    seam_path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(seam_path, [_incident_rec("r1", "p9", "seam-step", error_code="E_SEAM")])

    # Set env AFTER import (call-time resolution, mirrors test_incident_classify AC8).
    monkeypatch.setenv("BD_INCIDENT_LOG", str(seam_path))

    exit_code = main(["--run", "r1", "--events", str(events_path)])
    assert exit_code == 0, f"AC11 FAIL: expected exit 0, got {exit_code}"


def test_ac11_env_seam_incident_visible_in_stdout(tmp_path, monkeypatch, capsys):
    from bytedigger_engine.lib.receipts_line import main  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 1000}),
    ])

    seam_path = Path(tmp_path).resolve() / "seam" / "incidents.jsonl"
    seam_path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(seam_path, [_incident_rec("r1", "p9", "seam-step", error_code="E_SEAM")])

    monkeypatch.setenv("BD_INCIDENT_LOG", str(seam_path))

    main(["--run", "r1", "--events", str(events_path)])
    captured = capsys.readouterr()
    assert "p9/seam-step" in captured.out, (
        f"AC11 FAIL: env-pointed ledger incident must be visible on stdout, got {captured.out!r}"
    )
    assert "E_SEAM" in captured.out, (
        f"AC11 FAIL: env-pointed ledger incident error_code must be visible, got {captured.out!r}"
    )


# ─── AC12: rollup absent → n/a economics; non-numeric wall_ms → no wall segment ──

def test_ac12_rollup_absent_renders_na_economics(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 60000}),
    ])
    _write_jsonl(ledger_path, [])

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path,
    )
    assert exit_code == 0, f"AC12 FAIL: expected exit 0, got {exit_code}"
    assert "**n/a (no rollup)**" in stdout, f"AC12 FAIL: expected n/a economics, got:\n{stdout}"


def test_ac12_non_numeric_wall_ms_omits_wall_segment_no_raise(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": "not-a-number"}),
    ])
    _write_jsonl(ledger_path, [])

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path,
    )
    assert exit_code == 0, f"AC12 FAIL: expected exit 0, got {exit_code}"
    date_line = [ln for ln in stdout.split("\n") if ln.startswith("- Date:")][0]
    assert "wall ≈" not in date_line, (
        f"AC12 FAIL: wall segment must be omitted for non-numeric wall_ms, got {date_line!r}"
    )


# ─── AC13: null error_code/workaround, sub-1000 tokens_out, missing by_cycle ──

def test_ac13_null_error_code_and_workaround_render_unknown_no_workaround_text(tmp_path):
    from bytedigger_engine.lib.receipts_line import generate_receipts  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    ledger_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_jsonl(events_path, [
        _ev("r1", "step_finished", {"step_name": "s1", "status": "error", "duration_ms": 1}),
        _ev("r1", "workflow_finished", {"workflow_name": "wf", "status": "error", "wall_ms": 1000}),
        _ev("r1", "workflow_cost_rollup", {
            "run_id": "r1", "calls": 1, "tokens_in": 100, "tokens_out": 500, "cost_usd": 0.01,
            "by_phase": {},
        }),
    ])
    _write_jsonl(ledger_path, [
        _incident_rec("r1", "p1", "s1", error_code=None, classification="unknown", workaround=None),
    ])

    exit_code, stdout, _stderr = generate_receipts(
        run_id="r1", events_path=events_path, ledger_path=ledger_path,
    )
    assert exit_code == 0, f"AC13 FAIL: expected exit 0, got {exit_code}"
    assert "[unknown]" in stdout, f"AC13 FAIL: expected [unknown] classification, got:\n{stdout}"
    assert "workaround:" not in stdout, (
        f"AC13 FAIL: no workaround text expected when workaround is null, got:\n{stdout}"
    )
    assert "500 tokens_out" in stdout, f"AC13 FAIL: expected '500 tokens_out' (no K), got:\n{stdout}"
    econ_line = [ln for ln in stdout.split("\n") if "Economics:" in ln][0]
    assert "cycles" not in econ_line, (
        f"AC13 FAIL: expected no cycles segment when by_cycle is missing, got {econ_line!r}"
    )
