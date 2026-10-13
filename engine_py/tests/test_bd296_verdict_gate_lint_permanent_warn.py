"""RED tests for bd#296: tests that fail until HAL_VERDICT_GATE_LINT_ENFORCE is
retired and the verdict-gate lint is a permanent warning. Every test here
fails today. The fences (behaviour that must not change) live in
test_bd296_verdict_gate_lint_fences.py.

Spec: docs/decisions/2026-10-13-bd296-verdict-gate-lint-enforce-retired.md

AC1  test_ac1_catalog_retires_enforce_keeps_kill_switch
AC3  test_ac3_enforce_env_does_not_stop_the_step
AC6  test_ac6_driver_error_is_warn_and_fail_open   (enforce flag set)
AC7  test_ac7_error_code_retired_and_registry_consistent
AC8  test_ac8_ledger_drops_flag_without_extending_others
AC9  test_ac9_engine_sources_carry_no_retired_strings
AC10 test_ac10_enforce_flag_is_not_read_anywhere

Harness: AC3 and AC6 run the real `_gate_on_validation` step with a recording
event log in the telemetry slot (pre-staged and cleared, no timing,
workflows.md 1i), cwd = a tiny tmp tree so the lint can verify anchors. Only
AC6 replaces a collaborator (`_run_verdict_gate`), to force the driver-error
branch. Units under test are imported lazily inside bodies.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_PY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PKG_ROOT = ENGINE_PY_ROOT / "bytedigger_engine"
REPO_ROOT = ENGINE_PY_ROOT.parent

RETIRED_FLAG = "HAL_VERDICT_GATE_LINT_ENFORCE"
RETIRED_CODE = "E_VERDICT_GATE_LINT"
STALE_FLIP_BY = "flip-by:2026-08-01"
EVENT = "verdict_gate_lint_warn"

UNANCHORED_DOC = "## Verdict\nPASS\n\nVERDICT: APPROVED\n"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def of(self, event_type: str) -> list:
        return [p for (t, p, _) in self.events if t == event_type]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from bytedigger_engine import telemetry_ctx

    monkeypatch.delenv("HAL_VERDICT_GATE_LINT", raising=False)
    monkeypatch.delenv(RETIRED_FLAG, raising=False)
    monkeypatch.chdir(tmp_path)

    telemetry_ctx.clear_current_run()
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="bd296", step_name="gate_on_validation", phase="5", cycle=1,
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
        question="bd296", session_id="test-bd296", persona="hal", framework=None, domain=None,
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


def _run_step(h: dict, raw: str, cycle: int = 1):
    from bytedigger_engine.workflows import phase_5_implement as p5

    prev = _prev(h["scratch"], raw, cycle)
    return p5._gate_on_validation(_ctx(h["scratch"]), prev), prev


def _assert_ok_single_warn(res, log, prev, *, result: str, rc: int) -> None:
    assert res.status == "ok", (
        f"status={res.status!r} error_code={res.error_code!r} error={res.error!r}"
    )
    assert res.error_code is None, res.error_code
    warns = log.of(EVENT)
    assert len(warns) == 1, f"expected exactly one {EVENT}, got {log.events!r}"
    w = warns[0]
    assert w["result"] == result, w
    assert w["rc"] == rc, w
    assert w["doc"] == prev.data["validation_doc_path"], w
    assert w["phase"] == 5, w
    assert w["cycle"] == prev.data["cycle"], w


# ---------------------------------------------------------------------------
# AC1
# ---------------------------------------------------------------------------

def test_ac1_catalog_retires_enforce_keeps_kill_switch() -> None:
    from bytedigger_engine.flags_catalog import FLAGS

    assert RETIRED_FLAG not in FLAGS, "retired flag still in the catalog"
    entry = FLAGS["HAL_VERDICT_GATE_LINT"]
    assert entry["kind"] == "gate"
    assert entry["default"] == "1"
    desc = entry["description"]
    for needle in (RETIRED_CODE, RETIRED_FLAG, STALE_FLIP_BY):
        assert needle not in desc, f"description still contains {needle!r}"


# ---------------------------------------------------------------------------
# AC2 - AC6 (real step)
# ---------------------------------------------------------------------------

def test_ac3_enforce_env_does_not_stop_the_step(harness, monkeypatch) -> None:
    """RED: today the env flag turns the warning into E_VERDICT_GATE_LINT."""
    monkeypatch.setenv(RETIRED_FLAG, "1")
    res, prev = _run_step(harness, UNANCHORED_DOC)
    _assert_ok_single_warn(res, harness["log"], prev, result="UNANCHORED", rc=1)


def test_ac6_driver_error_is_warn_and_fail_open(harness, monkeypatch) -> None:
    """RED: with the retired env flag set, a driver error must still warn and
    fail open. (The unset case is a fence in
    test_bd296_verdict_gate_lint_fences.py.)"""
    from bytedigger_engine.workflows import phase_5_implement as p5

    def _boom(*_a, **_kw):
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(p5, "_run_verdict_gate", _boom)
    monkeypatch.setenv(RETIRED_FLAG, "1")
    res, prev = _run_step(harness, UNANCHORED_DOC)
    _assert_ok_single_warn(res, harness["log"], prev, result="DRIVER_ERROR", rc=2)


# ---------------------------------------------------------------------------
# AC7
# ---------------------------------------------------------------------------

def test_ac7_error_code_retired_and_registry_consistent() -> None:
    from bytedigger_engine import error_codes

    assert RETIRED_CODE not in error_codes.ERROR_CODES, f"{RETIRED_CODE} still registered"
    for md in (ENGINE_PY_ROOT / "ERROR_CODES.md", ENGINE_PKG_ROOT / "ERROR_CODES.md"):
        assert RETIRED_CODE not in md.read_text(encoding="utf-8"), f"{RETIRED_CODE} listed in {md}"

    unregistered, dead = error_codes.check(ENGINE_PKG_ROOT)
    assert unregistered == set(), f"unregistered codes: {sorted(unregistered)}"
    assert dead == set(), f"dead codes: {sorted(dead)}"

    rendered = error_codes.render_markdown().encode()
    for md in (ENGINE_PY_ROOT / "ERROR_CODES.md", ENGINE_PKG_ROOT / "ERROR_CODES.md"):
        raw = md.read_bytes()
        has_heading = any(
            line.strip() == "## E_VERDICT" for line in raw.decode("utf-8").splitlines()
        )
        in_sync = raw == rendered
        assert has_heading is False, f"{md} still has a '## E_VERDICT' heading"
        assert in_sync is True, f"{md} differs from error_codes.render_markdown()"


# ---------------------------------------------------------------------------
# AC8
# ---------------------------------------------------------------------------

def test_ac8_ledger_drops_flag_without_extending_others() -> None:
    script = REPO_ROOT / "scripts" / "flip_horizon.py"
    ledger = json.loads(
        (REPO_ROOT / "scripts" / "flip_horizon_ledger.json").read_text(encoding="utf-8")
    )
    r = subprocess.run(
        [sys.executable, str(script), "--check", "--today", "2026-11-04"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60,
    )
    out = r.stdout + r.stderr
    names_flag = RETIRED_FLAG in out
    rc_ok = r.returncode in (0, 1)
    key_present = RETIRED_FLAG in ledger
    late = sorted(
        k for k, v in ledger.items() if str(v.get("until", "")) > "2026-11-03"
    )
    assert key_present is False, "ledger still has the retired flag key"
    assert rc_ok is True, f"flip_horizon --check returncode={r.returncode}"
    assert names_flag is False, "flip_horizon output names the retired flag"
    assert late == [], f"ledger entries with until > 2026-11-03: {late}"


# ---------------------------------------------------------------------------
# AC9 / AC10
# ---------------------------------------------------------------------------

def _engine_py_files() -> list:
    files = sorted(ENGINE_PKG_ROOT.rglob("*.py"))
    assert files, f"no .py files under {ENGINE_PKG_ROOT}"
    return files


def test_ac9_engine_sources_carry_no_retired_strings() -> None:
    """The stale-deadline check is scoped to the lint's own text: an unrelated
    `flip-by:2026-08-01` (RED scope allowlist, refs #569) lives in
    phase_5_implement.py and is not this lot's to touch."""
    hits: list = []
    for p in _engine_py_files():
        rel = p.relative_to(ENGINE_PKG_ROOT)
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if RETIRED_CODE in line or RETIRED_FLAG in line:
                hits.append(f"{rel}:{n}: retired name")
            if STALE_FLIP_BY in line and ("34E0B77B" in line or "VERDICT_GATE" in line.upper()):
                hits.append(f"{rel}:{n}: stale verdict-gate flip-by")
    assert hits == [], hits


def test_ac10_enforce_flag_is_not_read_anywhere() -> None:
    # Any mention of the name (flag()/gate_enabled()/os.environ/literal) counts as a read site.
    hits = [
        str(p.relative_to(ENGINE_PKG_ROOT))
        for p in _engine_py_files()
        if RETIRED_FLAG in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == [], f"{RETIRED_FLAG} still referenced in: {hits}"
