"""RED tests for bd flip — HAL_RED_COLLECT_PROBE_ENFORCE defaults ON.

Spec: docs/decisions/2026-10-03-bd-flip-red-collect-probe.md (AC1..AC7, AC9;
AC8 is the sibling migration in test_gh542_collect_probe.py and
test_bd59_enforcement_map.py).

Every behavioural test drives the REAL `_verify_red_lint_rules` (batch path)
or, via HAL_RED_LINT_PREFLIGHT_BATCH=0, the REAL legacy path, on a
non-collectable fixture. Only the telemetry sink `_emit_safe` is wrapped.
The ENFORCE env var is never set to "1" here: the default is under test.

§1i: no singleton/timing resource; fixture files and env are pre-staged
deterministically before invoking the unit under test.

Regression shields (PASS at RED by design, still discriminate after GREEN):
AC3, AC5, AC6 alias test, AC10 ordinary-error test, AC11 warn-only variants.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAG = "HAL_RED_COLLECT_PROBE_ENFORCE"

ENV_TOKENS = (
    "HAL_RED_COLLECT_PROBE_GATE",
    "HAL_RED_COLLECT_PROBE_ENFORCE",
    "HAL_RED_COLLECT_PROBE_TIMEOUT_MS",
    "HAL_RED_LINT_PREFLIGHT_BATCH",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for tok in ENV_TOKENS:
        monkeypatch.delenv(tok, raising=False)
        suffix = tok[len("HAL_"):]
        for prefix in ("BD_", "BYTEDIGGER_"):
            monkeypatch.delenv(prefix + suffix, raising=False)


# ─── helpers (mirror test_gh542_collect_probe.py) ────────────────────────────

_NON_COLLECTABLE = """\
from nonexistent_gh542_module import missing


def test_dummy():
    assert missing is not None
"""

_COLLECTABLE = """\
def test_x():
    assert False
"""


def _write_test_file(tmp_path: Path, relpath: str, content: str) -> str:
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return relpath


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(scratchpad)}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-bd-flip-collect-probe",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths: list) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": "scratchpad/spec.md",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _run(tmp_path: Path, content: str):
    from bytedigger_engine.workflows import phase_5_implement as p5

    relpath = _write_test_file(tmp_path, "tests/test_flip_probe_fixture.py", content)
    captured: list[tuple[str, dict]] = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, dict(payload)))

    with patch.object(p5, "_emit_safe", side_effect=_capture):
        result = p5._verify_red_lint_rules(_make_ctx(tmp_path), _make_prev([relpath]))
    return result, captured


def _probe_events(captured):
    return [p for (n, p) in captured if n == "red_collect_probe_check"]


# ═══ AC1 — catalog entry ═════════════════════════════════════════════════════


def test_ac1_catalog_entry_is_default_on_gate_with_kill_switch_description() -> None:
    from bytedigger_engine import flags_catalog

    entry = flags_catalog.FLAGS[FLAG]
    assert entry["kind"] == "gate"
    assert entry["default"] == "1"
    desc = entry["description"]
    assert "=0" in desc, f"description must name the =0 kill-switch: {desc!r}"
    for token in ("flip-by", "kill-by", "retire-by"):
        assert token not in desc, f"description must carry no horizon token {token!r}: {desc!r}"


# ═══ AC2 — production side effect: default hard-blocks ═══════════════════════


def test_ac2_default_blocks_non_collectable_red_batch_path(tmp_path: Path) -> None:
    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert result.status == "error"
    assert result.error_code == "E_RED_COLLECT_PROBE"
    assert result.recoverable is True
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is True
    assert events[0]["violations_n"] >= 1


def test_ac2_default_blocks_non_collectable_red_legacy_path(tmp_path: Path, monkeypatch) -> None:
    # Legacy path is reached only via the batch master kill-switch.
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert any(
        n == "gate_disabled" and p.get("gate") == "HAL_RED_LINT_PREFLIGHT_BATCH"
        for (n, p) in captured
    ), "legacy path must actually have been taken"
    assert result.status == "error"
    assert result.error_code == "E_RED_COLLECT_PROBE"
    assert result.recoverable is True
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is True
    assert events[0]["violations_n"] >= 1


# ═══ AC3 — =0 kill-switch restores warn-only ═════════════════════════════════


def test_ac3_enforce_zero_restores_warn_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "0")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert result.error_code != "E_RED_COLLECT_PROBE"
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is False
    assert events[0]["violations_n"] >= 1


# ═══ AC4 — collectable RED not blocked, enforcement visible ══════════════════


def test_ac4_default_collectable_red_not_blocked(tmp_path: Path) -> None:
    result, captured = _run(tmp_path, _COLLECTABLE)

    assert result.error_code != "E_RED_COLLECT_PROBE"
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is True
    assert events[0]["violations_n"] == 0


# ═══ AC5 — GATE=0 turns the whole gate off ═══════════════════════════════════


def test_ac5_gate_zero_turns_gate_fully_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAL_RED_COLLECT_PROBE_GATE", "0")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert result.error_code != "E_RED_COLLECT_PROBE"
    assert _probe_events(captured) == []
    assert any(
        n == "gate_disabled" and p.get("gate") == "HAL_RED_COLLECT_PROBE_GATE"
        for (n, p) in captured
    )


# ═══ AC6 — only exactly "0" disables; BD_ alias works ════════════════════════


def test_ac6_enforce_false_string_still_enforces(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "false")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert result.status == "error"
    assert result.error_code == "E_RED_COLLECT_PROBE"
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is True


def test_ac6_bd_alias_enforce_zero_restores_warn_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BD_RED_COLLECT_PROBE_ENFORCE", "0")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert result.error_code != "E_RED_COLLECT_PROBE"
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is False
    assert events[0]["violations_n"] >= 1


# ═══ AC10 — pytest-unavailable probe is a skip, not a violation (gate r1 F3) ═

class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _probe_with(proc: _FakeProc, tmp_path: Path):
    from bytedigger_engine.workflows import phase_5_implement as p5

    relpath = _write_test_file(tmp_path, "tests/test_flip_probe_fixture.py", _COLLECTABLE)
    with patch.object(p5, "bounded_run", return_value=proc):
        return p5._red_collect_probe([str(tmp_path / relpath)], str(tmp_path))


def test_ac10_no_module_named_pytest_is_skip_not_violation(tmp_path: Path) -> None:
    proc = _FakeProc(1, stderr="/usr/bin/python3: No module named pytest")

    assert _probe_with(proc, tmp_path) == ([], "pytest_unavailable")


def test_ac10_ordinary_collection_error_still_a_violation(tmp_path: Path) -> None:
    # Regression shield: passes at RED, pins that the skip is not over-broad.
    proc = _FakeProc(2, stdout="ImportError: cannot import name 'missing'")

    violations, skip_reason = _probe_with(proc, tmp_path)

    assert len(violations) == 1
    assert skip_reason == ""


# ═══ AC11 — legacy path honours the same kill-switch contract (gate r1 F7) ═══
# Regression shields (pass at RED, still discriminate post-GREEN): AC3, AC5,
# AC6 alias, and the AC11 warn-only variants below. "false" enforcing fails at RED.


def test_ac11_legacy_enforce_zero_warn_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    monkeypatch.setenv(FLAG, "0")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert any(
        n == "gate_disabled" and p.get("gate") == "HAL_RED_LINT_PREFLIGHT_BATCH"
        for (n, p) in captured
    ), "legacy path must actually have been taken"
    assert result.error_code != "E_RED_COLLECT_PROBE"
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is False
    assert events[0]["violations_n"] >= 1


def test_ac11_legacy_enforce_false_string_still_enforces(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    monkeypatch.setenv(FLAG, "false")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert any(
        n == "gate_disabled" and p.get("gate") == "HAL_RED_LINT_PREFLIGHT_BATCH"
        for (n, p) in captured
    ), "legacy path must actually have been taken"
    assert result.status == "error"
    assert result.error_code == "E_RED_COLLECT_PROBE"
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is True


def test_ac11_legacy_bd_alias_enforce_zero_warn_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    monkeypatch.setenv("BD_RED_COLLECT_PROBE_ENFORCE", "0")

    result, captured = _run(tmp_path, _NON_COLLECTABLE)

    assert any(
        n == "gate_disabled" and p.get("gate") == "HAL_RED_LINT_PREFLIGHT_BATCH"
        for (n, p) in captured
    ), "legacy path must actually have been taken"
    assert result.error_code != "E_RED_COLLECT_PROBE"
    events = _probe_events(captured)
    assert len(events) == 1
    assert events[0]["enforced"] is False
    assert events[0]["violations_n"] >= 1


# ═══ AC7 — horizon guard stays green ═════════════════════════════════════════


def test_ac7_horizon_token_and_ledger_entry_removed_together() -> None:
    import datetime
    import importlib.util
    import inspect
    import json

    from bytedigger_engine import flags_catalog
    from bytedigger_engine.workflows import phase_5_implement

    spec = importlib.util.spec_from_file_location(
        "flip_horizon_under_test", REPO_ROOT / "scripts" / "flip_horizon.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ledger = json.loads((REPO_ROOT / "scripts" / "flip_horizon_ledger.json").read_text())
    problems = mod.check(flags_catalog.FLAGS, ledger, datetime.date(2026, 10, 3))

    # (a) no guard problem line names the flag
    assert not [p for p in problems if FLAG in p], problems
    # (b) the ledger key is gone
    assert FLAG not in ledger, "ledger entry must be deleted together with the catalog token"
    # (c) the legacy collect-probe block carries no flip-by token. Sliced to this
    # flag's block only: the same function also holds the unrelated, legitimate
    # GH535 sibling-audit `flip-by` comment, so whole-function absence is wrong.
    lines = inspect.getsource(phase_5_implement._verify_red_lint_rules_legacy).splitlines()
    start = next(i for i, ln in enumerate(lines) if "HAL_RED_COLLECT_PROBE_GATE" in ln)
    end = next(i for i, ln in enumerate(lines) if i >= start and "E_RED_COLLECT_PROBE" in ln)
    block = "\n".join(lines[start : end + 1])
    assert FLAG in block
    assert "flip-by" not in block


# ═══ AC9 — enforcement map agrees with the catalog ═══════════════════════════


def test_ac9_enforcement_map_r21_true_and_agrees_with_catalog() -> None:
    from bytedigger_engine import flags_catalog
    from bytedigger_engine.conformance import bd_l2

    assert bd_l2.ENFORCEMENT["R2.1"]["enforced_by_default"] is True
    entry = flags_catalog.FLAGS[FLAG]
    assert entry["kind"] == "gate" and entry["default"] == "1"
    assert entry["default"] != "0"
