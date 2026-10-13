"""RED tests for bd#297: the dead verdict-gate lint seam at the phase 5
acceptance step is deleted.

Spec: docs/decisions/2026-10-13-bd297-verdict-gate-seam-removed.md

AC1  test_ac1_phase_5_has_no_run_verdict_gate_attribute
AC2  test_ac2_no_seam_tokens_in_engine_sources
AC3  test_ac3_unanchored_approved_doc_emits_no_verdict_gate_event
AC4  test_ac4_catalog_description_has_no_seam_wording

Every test here fails on current code (seam still present). The fences
(AC5-AC7) live in test_bd297_verdict_seam_removed_fences.py.

Harness: the real `_gate_on_validation` step with a recording event log in the
telemetry slot (pre-staged and cleared, no timing, workflows.md 1i). No
collaborator is replaced.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[1] / "bytedigger_engine"
PHASE_5 = ENGINE_DIR / "workflows" / "phase_5_implement.py"

# VERDICT: APPROVED, no anchor block -> UNANCHORED under the unchanged lint.
UNANCHORED_DOC = "## Verdict\nPASS\n\nVERDICT: APPROVED\n"


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from bytedigger_engine import telemetry_ctx

    monkeypatch.delenv("HAL_VERDICT_GATE_LINT", raising=False)
    monkeypatch.chdir(tmp_path)

    telemetry_ctx.clear_current_run()
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="bd297", step_name="gate_on_validation", phase="5", cycle=1,
    )
    try:
        yield {"tmp": tmp_path, "log": log, "scratch": tmp_path / "scratch"}
    finally:
        telemetry_ctx.clear_current_run()


def _ctx(scratch: Path):
    from bytedigger_engine.contracts import WorkflowContext

    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="bd297", session_id="test-bd297", persona="hal", framework=None, domain=None,
    )


def _prev(scratch: Path, raw: str, cycle: int = 1):
    from bytedigger_engine.contracts import StepResult
    from bytedigger_engine.workflows import phase_5_implement as p5

    scratch.mkdir(parents=True, exist_ok=True)
    doc = scratch / "validation.md"
    doc.write_text(raw, encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "verdict": p5.VERDICT_PASS,
            "cycle": cycle,
            "validation_doc_path": str(doc),
            "validation_raw": raw,
            "spec_path": str(scratch / "build-spec.md"),
            "red_log_path": str(scratch / "build-red-output-cycle-1.log"),
        },
        duration_ms=0,
        step_name="run_validator",
    )


# ---------------------------------------------------------------------------
# AC1 - AC4
# ---------------------------------------------------------------------------

def test_ac1_phase_5_has_no_run_verdict_gate_attribute() -> None:
    from bytedigger_engine.workflows import phase_5_implement as p5

    assert not hasattr(p5, "_run_verdict_gate"), (
        "phase_5_implement still exposes the _run_verdict_gate alias (the dead seam)"
    )


def test_ac2_no_seam_tokens_in_engine_sources() -> None:
    offenders = []
    for path in sorted(ENGINE_DIR.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "verdict_gate_lint_warn" in text:
            offenders.append(f"{path.relative_to(ENGINE_DIR)}: verdict_gate_lint_warn")

    p5_text = PHASE_5.read_text(encoding="utf-8")
    for token in ("_run_verdict_gate", "HAL_VERDICT_GATE_LINT"):
        if token in p5_text:
            offenders.append(f"workflows/phase_5_implement.py: {token}")

    tree = ast.parse(p5_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[-1] == "verdict_gate" or any(
                a.name == "verdict_gate" for a in node.names
            ):
                offenders.append(f"workflows/phase_5_implement.py: import from {mod!r}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == "verdict_gate":
                    offenders.append(f"workflows/phase_5_implement.py: import {a.name}")

    assert offenders == [], offenders


def test_ac3_unanchored_approved_doc_emits_no_verdict_gate_event(harness, monkeypatch) -> None:
    from bytedigger_engine import verdict_gate
    from bytedigger_engine.workflows import phase_5_implement as p5

    called: list = []

    def _spy(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("run_gate must not be called from phase 5")

    # Patch the module attribute and the alias phase_5 binds at import time
    # (raising=False: the alias does not exist after the seam is removed).
    monkeypatch.setattr(verdict_gate, "run_gate", _spy)
    monkeypatch.setattr(p5, "_run_verdict_gate", _spy, raising=False)

    prev = _prev(harness["scratch"], UNANCHORED_DOC)
    res = p5._gate_on_validation(_ctx(harness["scratch"]), prev)

    assert res.status == "ok", (res.status, res.error_code, res.error)
    gate_events = [t for (t, _p, _r) in harness["log"].events if "verdict_gate" in t]
    assert gate_events == [], harness["log"].events
    assert called == [], f"run_gate was invoked from phase 5: {called}"


def test_ac4_catalog_description_has_no_seam_wording() -> None:
    from bytedigger_engine.flags_catalog import FLAGS

    entry = FLAGS["HAL_VERDICT_GATE_LINT"]
    assert entry["kind"] == "gate", entry
    assert entry["default"] == "1", entry
    desc = entry["description"]
    for forbidden in ("phase_5", "acceptance seam", "verdict_gate_lint_warn"):
        assert forbidden not in desc, f"{forbidden!r} still in description: {desc!r}"
    for required in ("=0", "run_gate"):
        assert required in desc, f"{required!r} missing from description: {desc!r}"
