"""RED tests for bd#296: the verdict-gate lint is a permanent warning and
HAL_VERDICT_GATE_LINT_ENFORCE is retired.

Spec: docs/decisions/2026-10-13-bd296-verdict-gate-lint-enforce-retired.md

AC1  test_ac1_catalog_retires_enforce_keeps_kill_switch
AC2  test_ac2_unanchored_approved_doc_warns_and_continues          (fence)
AC3  test_ac3_enforce_env_does_not_stop_the_step                   (RED)
AC4  test_ac4_kill_switch_emits_no_warn                            (fence)
AC5  test_ac5_agreeing_doc_emits_no_warn                           (fence)
AC6  test_ac6_driver_error_is_warn_and_fail_open[unset|set]        (unset = fence, set = RED)
AC7  test_ac7_error_code_retired_and_registry_consistent
AC8  test_ac8_ledger_drops_flag_without_extending_others
AC9  test_ac9_engine_sources_carry_no_retired_strings
AC10 test_ac10_enforce_flag_is_not_read_anywhere

Harness: AC2-AC6 run the real `_gate_on_validation` step with a recording event
log in the telemetry slot (pre-staged and cleared, no timing, workflows.md 1i),
cwd = a tiny tmp tree so the lint can verify anchors. Only AC6 replaces a
collaborator (`_run_verdict_gate`), to force the driver-error branch. Units
under test are imported lazily inside bodies.
"""
from __future__ import annotations

import hashlib
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

LEDGER_KEYS_AFTER = {
    "HAL_SPEC_PREFLIGHT_BATCH",
    "HAL_SPEC_REENTRY_ENFORCE",
    "HAL_SPEC_HELPER_EXTRACTION_ENFORCE",
    "HAL_SPEC_LINT_BATCH_ENFORCE",
    "HAL_SPEC_CITE_PRELINT_ENFORCE",
    "HAL_BASELINE_DELTA_ENFORCE",
    "HAL_CORPUS_PARITY_ENFORCE",
    "HAL_KNOWN_REDS_KILL_BY_ENFORCE",
    "HAL_KNOWN_REDS_SCOPE_ENFORCE",
    "HAL_KNOWN_REDS_OWNER_ENFORCE",
    "HAL_KNOWN_REDS_RED_SHAPE_ENFORCE",
}

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


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _agreeing_doc_text(tmp: Path) -> str:
    """Anchored spec + RED files (sha256 match), checklist without FAIL rows,
    AC ids equal to the spec's: the lint returns GATE_PASS on this."""
    spec = tmp / "docs" / "decisions" / "frozen-spec.md"
    red = tmp / "tests" / "test_red.py"
    spec.parent.mkdir(parents=True, exist_ok=True)
    red.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "# Spec\n\n## Acceptance criteria\n\n"
        "| AC1 | first behaviour |\n| AC2 | second behaviour |\n",
        encoding="utf-8",
    )
    red.write_text("def test_x():\n    assert False\n", encoding="utf-8")
    return (
        "# gate r1\n\n"
        "## Checklist\n\n- AC1: PASS\n- AC2: PASS\n\n"
        "VERDICT: APPROVED\n\n"
        "<!-- verdict-anchor\n"
        f"spec: docs/decisions/frozen-spec.md sha256:{_sha(spec)}\n"
        f"red: tests/test_red.py sha256:{_sha(red)}\n"
        "-->\n"
    )


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

def test_ac2_unanchored_approved_doc_warns_and_continues(harness) -> None:
    """Fence: passes before the change (the warn branch already emits)."""
    res, prev = _run_step(harness, UNANCHORED_DOC)
    _assert_ok_single_warn(res, harness["log"], prev, result="UNANCHORED", rc=1)


def test_ac3_enforce_env_does_not_stop_the_step(harness, monkeypatch) -> None:
    """RED: today the env flag turns the warning into E_VERDICT_GATE_LINT."""
    monkeypatch.setenv(RETIRED_FLAG, "1")
    res, prev = _run_step(harness, UNANCHORED_DOC)
    _assert_ok_single_warn(res, harness["log"], prev, result="UNANCHORED", rc=1)


def test_ac4_kill_switch_emits_no_warn(harness, monkeypatch) -> None:
    """Fence: passes before the change."""
    monkeypatch.setenv("HAL_VERDICT_GATE_LINT", "0")
    res, _prev_ = _run_step(harness, UNANCHORED_DOC)
    assert res.status == "ok", (res.status, res.error_code)
    assert harness["log"].of(EVENT) == [], harness["log"].events


def test_ac5_agreeing_doc_emits_no_warn(harness) -> None:
    """Fence: passes before the change. Instrument check first: the doc really
    is GATE_PASS under the unchanged lint, so silence means agreement."""
    from bytedigger_engine.verdict_gate import run_gate

    raw = _agreeing_doc_text(harness["tmp"])
    probe = harness["tmp"] / "probe.md"
    probe.write_text(raw, encoding="utf-8")
    rc, report = run_gate(str(probe), str(harness["tmp"]), {})
    assert (rc, report.get("result")) == (0, "GATE_PASS"), (rc, report)

    res, _prev_ = _run_step(harness, raw)
    assert res.status == "ok", (res.status, res.error_code)
    assert harness["log"].of(EVENT) == [], harness["log"].events


@pytest.mark.parametrize("enforce", [False, True], ids=["unset", "set"])
def test_ac6_driver_error_is_warn_and_fail_open(harness, monkeypatch, enforce) -> None:
    """`unset` is a fence (passes before the change); `set` is RED."""
    from bytedigger_engine.workflows import phase_5_implement as p5

    def _boom(*_a, **_kw):
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(p5, "_run_verdict_gate", _boom)
    if enforce:
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
    assert bool(out.strip()) is True, "flip_horizon --check produced no output (did not run)"
    assert (RETIRED_FLAG in out) is False, "flip_horizon output names the retired flag"
    assert (RETIRED_FLAG in ledger) is False, "ledger still has the retired flag key"
    assert set(ledger) == LEDGER_KEYS_AFTER, sorted(set(ledger) ^ LEDGER_KEYS_AFTER)
    assert len(ledger) == 11
    moved = {k: v.get("until") for k, v in ledger.items() if v.get("until") != "2026-11-03"}
    assert moved == {}, f"ledger dates were extended: {moved}"


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
