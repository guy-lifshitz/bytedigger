"""GH531 RED — frozen-spec short-circuit of phase_1 + SIMPLE gate relax.

AC1-AC9 per spec SHARED/memory/Decisions/2026-07-10_GH531_frozen_skip_phase1_spec.md.

§1q: `frozen_short_circuit_enabled` and the new `check_frozen_spec_skip` step do
not exist yet — imported INSIDE test bodies so this file COLLECTS today and
fails at assert/import-in-body time, never at collection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.workflows.phase_1_discovery import (  # noqa: E402
    FEATURE_DOC_RELPATH,
    SIMPLE_DOC_RELPATH,
    phase_1_discovery_workflow,
)
from bytedigger_engine.skip_logic import should_skip_phase  # noqa: E402


FROZEN_TEXT = (
    "# Frozen Spec\n\n"
    "**Status:** FROZEN (§1-PREFLIGHT done)\n\n"
    "### §3 Acceptance Criteria\n"
    "| # | AC |\n|---|----|\n| AC1 | thing |\n"
)

NON_FROZEN_TEXT = (
    "# Draft Spec\n\n"
    "Some ordinary decision doc content with no markers.\n"
    "It is resolvable and non-empty but not frozen.\n"
)


class _TextBackend:
    """Returns a canned raw_response (mirrors test_skip_logic_05F83B1B.py idiom)."""

    def __init__(self, response_text: str) -> None:
        self._text = response_text

    def __call__(self, **kw):
        from bytedigger_engine.contracts import StepResult

        data: dict = {
            "raw_response": self._text,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "llm"),
            error=None,
            error_code=None,
            recoverable=True,
        )


def _register_text_stub(text: str) -> None:
    from bytedigger_engine.llm_subprocess import register_backend

    register_backend(
        "claude-subprocess",
        _TextBackend(text),
        manifest_source="harness_tool_record",
        overwrite=True,
    )


def make_ctx(scratchpad: Path, *, question: str = "Add foo", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _run_phase1(
    tmp_path, monkeypatch, *, doc_text: str | None, complexity: str, flag: str | None,
    stub_llm: bool = True,
):
    """Execute phase_1_discovery_workflow with a decision_doc fixture and return (result, events).

    stub_llm=False lets the caller pre-register its own backend (e.g. a burn-guard
    for AC8) without it being clobbered here.
    """
    if flag is None:
        monkeypatch.delenv("HAL_FROZEN_SHORT_CIRCUIT", raising=False)
    else:
        monkeypatch.setenv("HAL_FROZEN_SHORT_CIRCUIT", flag)

    if stub_llm:
        _register_text_stub("## Context\nstub body\n")

    monkeypatch.chdir(tmp_path)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True)

    ddoc = None
    if doc_text is not None:
        ddoc = tmp_path / "decision.md"
        ddoc.write_text(doc_text)

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("p1", phase_1_discovery_workflow())

    org_extra = {"complexity": complexity}
    if ddoc is not None:
        org_extra["decision_doc"] = str(ddoc)

    result, _ = eng.execute("p1", make_ctx(scratchpad, **org_extra), run_id="rid-gh531")
    events = log.read_all()
    return result, events, scratchpad


# ─── AC1: frozen + SIMPLE (flag default) → skip, no doc, phase_self_skipped ──


def test_ac1_frozen_simple_flag_default_skips_no_doc_written(tmp_path, monkeypatch):
    result, events, scratchpad = _run_phase1(
        tmp_path, monkeypatch, doc_text=FROZEN_TEXT, complexity="SIMPLE", flag=None,
    )
    assert result.status == "ok"
    assert not (scratchpad / SIMPLE_DOC_RELPATH).exists()
    skip_events = [e for e in events if e["event_type"] == "phase_self_skipped"]
    assert len(skip_events) >= 1
    assert skip_events[0]["payload"].get("step_name") == "check_frozen_spec_skip"


# ─── AC2: same as AC1 with FEATURE complexity ────────────────────────────────


def test_ac2_frozen_feature_flag_default_skips_no_doc_written(tmp_path, monkeypatch):
    result, events, scratchpad = _run_phase1(
        tmp_path, monkeypatch, doc_text=FROZEN_TEXT, complexity="FEATURE", flag=None,
    )
    assert result.status == "ok"
    assert not (scratchpad / FEATURE_DOC_RELPATH).exists()
    skip_events = [e for e in events if e["event_type"] == "phase_self_skipped"]
    assert len(skip_events) >= 1
    assert skip_events[0]["payload"].get("step_name") == "check_frozen_spec_skip"


# ─── AC3: kill-switch OFF → runs fully, doc written, no skip event ──────────


def test_ac3_kill_switch_off_runs_fully(tmp_path, monkeypatch):
    result, events, scratchpad = _run_phase1(
        tmp_path, monkeypatch, doc_text=FROZEN_TEXT, complexity="SIMPLE", flag="0",
    )
    assert result.status == "ok"
    doc = scratchpad / SIMPLE_DOC_RELPATH
    assert doc.exists()
    assert doc.stat().st_size > 0
    skip_events = [e for e in events if e["event_type"] == "phase_self_skipped"]
    assert len(skip_events) == 0


# ─── AC4: non-frozen doc + SIMPLE (flag default) → runs normally, doc written


def test_ac4_non_frozen_doc_simple_runs_normally(tmp_path, monkeypatch):
    result, events, scratchpad = _run_phase1(
        tmp_path, monkeypatch, doc_text=NON_FROZEN_TEXT, complexity="SIMPLE", flag=None,
    )
    assert result.status == "ok"
    doc = scratchpad / SIMPLE_DOC_RELPATH
    assert doc.exists()
    assert doc.stat().st_size > 0
    skip, resolved = should_skip_phase({
        "decision_doc": str(tmp_path / "decision.md"),
        "complexity": "SIMPLE",
    })
    assert skip is False
    assert resolved is None


# ─── AC5: should_skip_phase SIMPLE+frozen+flag ON → (True, path), no drift ──


def test_ac5_should_skip_phase_simple_frozen_flag_on_skips_no_drift(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HAL_FROZEN_SHORT_CIRCUIT", raising=False)
    doc = tmp_path / "decision.md"
    doc.write_text(FROZEN_TEXT)

    from bytedigger_engine import telemetry_ctx

    class _FakeLog:
        def __init__(self):
            self.events = []

        def append(self, event_type, payload, run_id):
            self.events.append({"event_type": event_type, "payload": payload})

    fake_log = _FakeLog()
    telemetry_ctx.set_current_run(
        event_log=fake_log,
        run_id="test-run-ac5",
        step_name="check_frozen_spec_skip",
        phase="phase_1_discovery",
    )
    try:
        skip, resolved = should_skip_phase({
            "decision_doc": str(doc),
            "complexity": "SIMPLE",
        })
    finally:
        telemetry_ctx.clear_current_run()

    assert skip is True
    assert resolved == str(doc)
    drift = [e for e in fake_log.events if e["event_type"] == "decision_doc_skip_complexity_drift"]
    assert len(drift) == 0


# ─── AC6: should_skip_phase SIMPLE+frozen+flag OFF → (False, None), drift ───


def test_ac6_should_skip_phase_simple_frozen_flag_off_drifts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HAL_FROZEN_SHORT_CIRCUIT", "0")
    doc = tmp_path / "decision.md"
    doc.write_text(FROZEN_TEXT)

    from bytedigger_engine import telemetry_ctx

    class _FakeLog:
        def __init__(self):
            self.events = []

        def append(self, event_type, payload, run_id):
            self.events.append({"event_type": event_type, "payload": payload})

    fake_log = _FakeLog()
    telemetry_ctx.set_current_run(
        event_log=fake_log,
        run_id="test-run-ac6",
        step_name="check_frozen_spec_skip",
        phase="phase_1_discovery",
    )
    try:
        skip, resolved = should_skip_phase({
            "decision_doc": str(doc),
            "complexity": "SIMPLE",
        })
    finally:
        telemetry_ctx.clear_current_run()

    assert skip is False
    assert resolved is None
    drift = [e for e in fake_log.events if e["event_type"] == "decision_doc_skip_complexity_drift"]
    assert len(drift) == 1


# ─── AC7: flags_catalog picks up HAL_FROZEN_SHORT_CIRCUIT in skip_logic.py ──


def test_ac7_flags_catalog_discovers_kill_switch_in_skip_logic():
    from bytedigger_engine import flags_catalog

    engine_py_root = HERE.parent
    results = flags_catalog.discover_flag_reads(engine_py_root)
    assert "HAL_FROZEN_SHORT_CIRCUIT" in results
    sites = results["HAL_FROZEN_SHORT_CIRCUIT"]
    assert any("skip_logic.py" in site for site in sites), sites


# ─── AC8: later steps no-op on skip — no LLM invocation ─────────────────────


def test_ac8_skip_active_llm_never_invoked(tmp_path, monkeypatch):
    from bytedigger_engine.llm_subprocess import reset_backends, register_backend
    from bytedigger_engine.contracts import StepResult

    monkeypatch.delenv("HAL_FROZEN_SHORT_CIRCUIT", raising=False)

    calls = []

    def _burn_guard(**kw):
        calls.append(kw)
        return StepResult(status="ok", data={"raw_response": "SHOULD NOT RUN"}, duration_ms=0)

    register_backend("claude-subprocess", _burn_guard, manifest_source="test", overwrite=True)
    try:
        result, events, scratchpad = _run_phase1(
            tmp_path, monkeypatch, doc_text=FROZEN_TEXT, complexity="SIMPLE", flag=None,
            stub_llm=False,
        )
        assert result.status == "ok"
        assert result.data.get("skipped") is True
        assert calls == [], f"LLM subprocess was invoked despite active skip: {calls}"
    finally:
        reset_backends()


# ─── AC9: unknown complexity + frozen doc + flag ON → (False, None), drift ──


def test_ac9_unknown_complexity_frozen_flag_on_no_skip_drifts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HAL_FROZEN_SHORT_CIRCUIT", raising=False)
    doc = tmp_path / "decision.md"
    doc.write_text(FROZEN_TEXT)

    from bytedigger_engine import telemetry_ctx

    class _FakeLog:
        def __init__(self):
            self.events = []

        def append(self, event_type, payload, run_id):
            self.events.append({"event_type": event_type, "payload": payload})

    fake_log = _FakeLog()
    telemetry_ctx.set_current_run(
        event_log=fake_log,
        run_id="test-run-ac9",
        step_name="check_frozen_spec_skip",
        phase="phase_1_discovery",
    )
    try:
        skip, resolved = should_skip_phase({
            "decision_doc": str(doc),
            "complexity": "WEIRD",
        })
    finally:
        telemetry_ctx.clear_current_run()

    assert skip is False
    assert resolved is None
    drift = [e for e in fake_log.events if e["event_type"] == "decision_doc_skip_complexity_drift"]
    assert len(drift) == 1


# ─── §1i autouse teardown ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_backends_gh531():
    from bytedigger_engine.llm_subprocess import reset_backends

    yield
    reset_backends()
