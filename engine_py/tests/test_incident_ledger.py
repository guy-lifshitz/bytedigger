"""RED tests for GH923 Lot 1 (ported from hal) — incident ledger per run.

lib/incident_ledger.py and config_provider.incident_log_relpath() do NOT
exist yet (§1q / D1CF5FDF): ALL references to these NEW symbols are
deferred to inside test bodies so this file COLLECTS cleanly and fails at
assert/import time, never at collection time.

§1l forcing function: every AC anchors on a real JSONL append (os.write
inside emit_incident) or a real method-return literal — none stub-passable.
emit_incident IS the UUT and is never mocked/patched here.

Seam adaptations from the hal source (PORT_SPEC.md):
  - HAL_INCIDENT_LOG -> BD_INCIDENT_LOG (new-in-bd JSONL path seam)
  - foreign state dirname ".bytedigger" (bd convention, GH878 seam)
  - hal_config_provider-specific override test dropped (no hal-host
    provider in bd)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _no_ambient_git(tmp_path, monkeypatch):
    """Mirrors test_engine.py: engine unit tests must not read ambient cwd git state."""
    monkeypatch.chdir(Path(tmp_path).resolve())  # §1j: resolve() — macOS /var/folders symlink


def _make_ctx(scratchpad_dir: "str | None" = None) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": scratchpad_dir} if scratchpad_dir else {},
        question="q",
        session_id="s",
        persona="p",
        framework=None,
        domain=None,
    )


# ─── AC1: full schema, single-record append ──────────────────────────────────

def test_ac1_emit_incident_writes_one_valid_record_all_keys(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    emit_incident(
        run_id="r1", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error="boom", wall_ms=42, path=log_path,
    )

    assert log_path.exists(), "AC1 FAIL: emit_incident did not create the ledger file"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC1 FAIL: expected exactly 1 line, got {len(lines)}"

    record = json.loads(lines[0])
    for key in (
        "ts", "run_id", "phase", "step_name", "cycle", "status", "error_code",
        "error", "wall_ms", "tokens", "classification", "workaround",
    ):
        assert key in record, f"AC1 FAIL: schema key {key!r} missing from record: {record!r}"

    assert record["run_id"] == "r1"
    assert record["phase"] == "p"
    assert record["step_name"] == "s"
    assert record["cycle"] == 1
    assert record["status"] == "error"
    assert record["error_code"] == "E_X"
    assert record["error"] == "boom"
    assert record["wall_ms"] == 42
    assert record["classification"] == "unknown"
    assert record["workaround"] is None
    assert record["tokens"] is None, "AC1 FAIL: tokens must be None when no events_path given"
    assert record["ts"].endswith("Z"), f"AC1 FAIL: ts must end 'Z', got {record['ts']!r}"


# ─── AC2: run_id falsy → suppress write entirely ─────────────────────────────

def test_ac2_run_id_none_suppresses_write(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    emit_incident(
        run_id=None, phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error="boom", wall_ms=1, path=log_path,
    )
    assert not log_path.exists(), "AC2 FAIL: run_id=None must not create the ledger file"


def test_ac2_run_id_empty_string_suppresses_write(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    emit_incident(
        run_id="", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error="boom", wall_ms=1, path=log_path,
    )
    assert not log_path.exists(), "AC2 FAIL: run_id='' must not create the ledger file"


# ─── AC3: non-writable parent → swallow, no raise ────────────────────────────

def test_ac3_non_writable_parent_swallows_no_raise(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    blocker_file = Path(tmp_path).resolve() / "not_a_dir"
    blocker_file.write_text("i am a file, not a directory", encoding="utf-8")
    bad_path = blocker_file / "incidents.jsonl"  # parent is a FILE → os.makedirs fails

    # Must not raise.
    emit_incident(
        run_id="r1", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error="boom", wall_ms=1, path=bad_path,
    )
    assert not bad_path.exists(), "AC3 FAIL: no record should be written to an unwritable path"


# ─── AC4: byte-cap + error truncation ────────────────────────────────────────

def test_ac4_long_error_truncated_record_still_written(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    long_error = "x" * 100_000
    emit_incident(
        run_id="r1", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error=long_error, wall_ms=1, path=log_path,
    )
    assert log_path.exists(), "AC4 FAIL: record with long error must still be written"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert len(record["error"]) <= 500, (
        f"AC4 FAIL: error must be truncated to <=500 chars, got {len(record['error'])}"
    )
    line_bytes = (lines[0] + "\n").encode("utf-8")
    assert len(line_bytes) <= 4096, "AC4 FAIL: serialized line must stay <=4096 bytes"


def test_ac4_oversized_record_skipped_file_unchanged(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    huge_phase = "p" * 20_000  # forces total record > 4096 bytes even after error truncation
    emit_incident(
        run_id="r1", phase=huge_phase, step_name="s", cycle=1, status="error",
        error_code="E_X", error="boom", wall_ms=1, path=log_path,
    )
    assert not log_path.exists(), (
        "AC4 FAIL: an oversized record (>4096 bytes even truncated) must be "
        "skipped entirely, not written truncated-invalid"
    )


# ─── AC13: None error must not be dropped (null-guarded truncation) ─────────

def test_ac13_error_none_still_writes_record(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    emit_incident(
        run_id="r1", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error=None, wall_ms=1, path=log_path,
    )
    assert log_path.exists(), "AC13 FAIL: a None-error incident must still be written"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["error"] is None, f"AC13 FAIL: expected error is None, got {record['error']!r}"


# ─── AC5: env call-time seam (BD_INCIDENT_LOG) ───────────────────────────────

def test_ac5_resolve_incident_log_path_honors_env_at_call_time(tmp_path, monkeypatch):
    from bytedigger_engine.lib.incident_ledger import resolve_incident_log_path  # noqa: PLC0415

    seam_path = Path(tmp_path).resolve() / "seam" / "incidents.jsonl"
    # Set env AFTER import (call-time resolution, GH497-D1 parity).
    monkeypatch.setenv("BD_INCIDENT_LOG", str(seam_path))
    result = resolve_incident_log_path()
    assert result == seam_path, (
        f"AC5 FAIL: resolve_incident_log_path() must honor BD_INCIDENT_LOG at call "
        f"time, expected {seam_path!r}, got {result!r}"
    )


# ─── AC6: default_incident_log_path foreign-root vs HAL-root split ──────────

def test_ac6_default_path_foreign_cwd(tmp_path, monkeypatch):
    from bytedigger_engine.lib.incident_ledger import default_incident_log_path  # noqa: PLC0415
    from bytedigger_engine.config_provider import foreign_state_dirname  # noqa: PLC0415

    monkeypatch.chdir(Path(tmp_path).resolve())
    result = default_incident_log_path()
    expected = Path.cwd().resolve() / foreign_state_dirname() / "incidents.jsonl"
    assert result == expected, (
        f"AC6 FAIL: foreign cwd default path expected {expected!r}, got {result!r}"
    )


def test_ac6_default_path_hal_root_uses_incident_log_relpath(monkeypatch):
    from bytedigger_engine.lib.incident_ledger import default_incident_log_path  # noqa: PLC0415
    from bytedigger_engine.config_provider import hal_root, incident_log_relpath  # noqa: PLC0415

    try:
        root = hal_root()
    except (OSError, ValueError, RuntimeError):
        pytest.skip("hal_root() unresolvable in this env — cannot exercise HAL branch")

    monkeypatch.chdir(root)
    result = default_incident_log_path()
    expected = root / incident_log_relpath()
    assert result == expected, (
        f"AC6 FAIL: HAL-root cwd default path expected {expected!r}, got {result!r}"
    )


# ─── AC7: token rollup via events_path ───────────────────────────────────────

def test_ac7_tokens_rollup_from_events_path(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    rows = [
        {"event_type": "subprocess_exited", "run_id": "r1",
         "payload": {"phase": "p", "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.1}},
        {"event_type": "subprocess_exited", "run_id": "r1",
         "payload": {"phase": "p", "tokens_in": 20, "tokens_out": 15, "cost_usd": 0.2}},
        {"event_type": "subprocess_exited", "run_id": "r2",
         "payload": {"phase": "p", "tokens_in": 999, "tokens_out": 999, "cost_usd": 9.0}},
    ]
    with open(events_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    emit_incident(
        run_id="r1", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error="boom", wall_ms=1, path=log_path,
        events_path=events_path,
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    tokens = record["tokens"]
    assert tokens is not None, "AC7 FAIL: tokens must be populated from events_path"
    assert tokens["calls"] == 2, f"AC7 FAIL: expected calls==2, got {tokens.get('calls')!r}"
    assert tokens["tokens_in"] == 30, f"AC7 FAIL: expected tokens_in==30, got {tokens.get('tokens_in')!r}"
    assert tokens["cost_usd"] == pytest.approx(0.3), (
        f"AC7 FAIL: expected cost_usd approx 0.3, got {tokens.get('cost_usd')!r}"
    )


def test_ac7_malformed_events_path_tokens_none_record_still_written(tmp_path):
    from bytedigger_engine.lib.incident_ledger import emit_incident  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    events_path.write_text("{ not valid json at all", encoding="utf-8")

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    emit_incident(
        run_id="r1", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_X", error="boom", wall_ms=1, path=log_path,
        events_path=events_path,
    )
    assert log_path.exists(), "AC7 FAIL: record must still be written despite malformed events file"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["tokens"] is None, (
        f"AC7 FAIL: tokens must be None on malformed events file, got {record['tokens']!r}"
    )


# ─── AC8-AC10: engine wire point (real Engine.execute, real step) ───────────

def _err_step(name: str = "fail_step"):
    def _run(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error="boom", error_code="E_T", recoverable=False,
        )
    return StepContract(name=name, execute=_run)


def _ok_step(name: str = "ok_step"):
    def _run(_ctx, _prev):
        return StepResult(status="ok", data="fine", duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _escalate_step(name: str = "escalate_step"):
    def _run(_ctx, _prev):
        return StepResult(
            status="escalate", data=None, duration_ms=0, step_name=name,
            error="degraded", error_code="E_ESCALATE",
        )
    return StepContract(name=name, execute=_run)


def test_ac8_engine_error_step_writes_exactly_one_incident_record(tmp_path, monkeypatch):
    # §1l/§1y forcing function: emit_incident is the REAL wire point in engine.py
    # §2.4 (post step_finished). This test drives Engine.execute end-to-end; it
    # does not call emit_incident directly.
    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    monkeypatch.setenv("BD_INCIDENT_LOG", str(log_path))

    eng = WorkflowEngine()
    eng.register("wf_err", WorkflowDefinition(name="wf_err", steps=[_err_step("s_err")]))
    result, _ctx = eng.execute("wf_err", _make_ctx(), run_id="run-ac8")

    assert result.status == "error"
    assert log_path.exists(), (
        "AC8 FAIL: Engine.execute on a real failing step must have triggered "
        "engine.py's emit_incident wire point — ledger file was not created"
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC8 FAIL: expected exactly 1 incident record, got {len(lines)}"
    record = json.loads(lines[0])
    assert record["phase"] == "wf_err"
    assert record["step_name"] == "s_err"
    assert record["error_code"] == "E_T"
    assert record["wall_ms"] >= 0


def test_ac9_engine_ok_step_writes_zero_incident_records(tmp_path, monkeypatch):
    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    monkeypatch.setenv("BD_INCIDENT_LOG", str(log_path))

    eng = WorkflowEngine()
    eng.register("wf_ok", WorkflowDefinition(name="wf_ok", steps=[_ok_step("s_ok")]))
    result, _ctx = eng.execute("wf_ok", _make_ctx(), run_id="run-ac9")

    assert result.status == "ok"
    assert not log_path.exists(), (
        "AC9 FAIL: an ok-status step must not produce any incident record"
    )


def test_ac10_engine_escalate_step_writes_one_record_status_escalate(tmp_path, monkeypatch):
    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    monkeypatch.setenv("BD_INCIDENT_LOG", str(log_path))

    eng = WorkflowEngine()
    eng.register(
        "wf_escalate",
        WorkflowDefinition(name="wf_escalate", steps=[_escalate_step("s_escalate")]),
    )
    result, _ctx = eng.execute("wf_escalate", _make_ctx(), run_id="run-ac10")

    assert result.status == "escalate"
    assert log_path.exists(), "AC10 FAIL: an escalate-status step must produce an incident record"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC10 FAIL: expected exactly 1 record, got {len(lines)}"
    record = json.loads(lines[0])
    assert record["status"] == "escalate", (
        f"AC10 FAIL: expected status=='escalate', got {record['status']!r}"
    )


# ─── AC11: §1ab re-entry — sentinel-cached replay never re-emits an incident ─

def test_ac11_sentinel_replay_of_prior_ok_step_emits_zero_records(tmp_path, monkeypatch):
    """§1ab re-entry AC: same run_id re-entry where the step was already
    cached ok via the sentinel must emit 0 incident records on replay — the
    structural guarantee is that maybe_write_sentinel (step_sentinel.py
    L216: ``status != "ok" -> return``) NEVER persists a non-ok result, so a
    cached replay can only ever synthesize an ok StepResult and the engine's
    ``result.status in ("error", "escalate")`` incident-wire-point guard
    (engine.py §2.4) is never reached on replay.

    §1i singleton-resource: the scratchpad/sentinel dir is pre-staged
    (created + first run executed) BEFORE the second (racing) execute call —
    never a timing-based race.
    """
    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    monkeypatch.setenv("BD_INCIDENT_LOG", str(log_path))

    scratchpad_dir = Path(tmp_path).resolve() / "scratchpad"
    scratchpad_dir.mkdir(parents=True, exist_ok=True)

    calls = {"n": 0}

    def _run(_ctx, _prev):
        calls["n"] += 1
        return StepResult(status="ok", data={"v": calls["n"]}, duration_ms=0, step_name="cacheable")

    step = StepContract(name="cacheable", execute=_run, resume_sentinel=True)

    eng = WorkflowEngine()
    eng.register("wf_resume", WorkflowDefinition(name="wf_resume", steps=[step]))

    ctx = _make_ctx(scratchpad_dir=str(scratchpad_dir))

    # First execute — real run, writes the sentinel (pre-stage, §1i).
    r1, _ = eng.execute("wf_resume", ctx, run_id="run-ac11")
    assert r1.status == "ok"
    assert calls["n"] == 1

    # Second execute, SAME run_id — sentinel replay must short-circuit the
    # step body (calls stays 1) and must not touch the incident ledger.
    r2, _ = eng.execute("wf_resume", ctx, run_id="run-ac11")
    assert r2.status == "ok"
    assert calls["n"] == 1, (
        "AC11 FAIL: sentinel replay should have short-circuited re-execution "
        "of the step body"
    )
    assert not log_path.exists(), (
        "AC11 FAIL: sentinel-cached ok replay must emit 0 incident records"
    )


# ─── AC12: config_provider accessor default (neutral, .bytedigger seam) ──────

def test_ac12_config_provider_default_incident_log_relpath():
    from bytedigger_engine import config_provider  # noqa: PLC0415

    # §1i: the default factory may already have been overridden by an
    # earlier test in this module — reset to the neutral factory BEFORE
    # asserting, and restore afterwards.
    try:
        config_provider.reset_default_config_provider_factory()

        fn = getattr(config_provider, "incident_log_relpath", None)
        assert callable(fn), (
            "AC12 FAIL: config_provider.incident_log_relpath free function does not "
            "exist yet — GREEN must add it (§2.2)"
        )
        result = fn()
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == ".bytedigger/incidents.jsonl", (
        f"AC12 FAIL: config_provider.incident_log_relpath() neutral default "
        f"expected '.bytedigger/incidents.jsonl', got {result!r}"
    )


# not ported: test_ac12_hal_config_provider_override_incident_log_relpath —
# hal-host provider (hal_config_provider module) absent in bd (PORT_SPEC.md
# "Seam adaptations": drop hal_config_provider-specific override tests).
