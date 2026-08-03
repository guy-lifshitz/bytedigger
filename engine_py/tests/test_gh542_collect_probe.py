"""RED tests for GH542 — §1q collect-probe: `pytest --co -q` over RED files as
a deterministic gate in phase_5, ahead of the semgrep check.

Spec: SHARED/memory/Decisions/2026-07-10_GH542_red_collect_probe_spec.md

Not-yet-existing symbol `_red_collect_probe` and error code
`E_RED_COLLECT_PROBE` are accessed via deferred import INSIDE test bodies
only (D1CF5FDF/§1q) so this file COLLECTS cleanly and fails at assert time,
never at collection time.

§1i: no singleton/time-dependent resource under test — the contested
resource (the fixture RED files, env vars) is pre-staged deterministically
before invoking the UUT, per workflows.md §1i.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers (mirror test_gh501_red_prompt_rules.py) ──────────────────


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh542",
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


def _write_test_file(tmp_path: Path, relpath: str, content: str) -> str:
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return relpath


_NON_COLLECTABLE = """\
from nonexistent_gh542_module import missing


def test_dummy():
    assert missing is not None
"""

_COLLECTABLE = """\
def test_x():
    assert False
"""


# ─── AC1: non-collectable fixture -> violations_n >= 1 ───────────────────────


def test_ac1_non_collectable_fixture_returns_violation(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _red_collect_probe  # noqa: PLC0415

    relpath = _write_test_file(tmp_path, "tests/test_1q_noncollect.py", _NON_COLLECTABLE)
    full_path = str(tmp_path / relpath)

    violations, skip_reason = _red_collect_probe([full_path], str(tmp_path))

    assert len(violations) >= 1, (
        f"expected >=1 violation for a non-collectable RED file, got {violations!r} "
        f"(skip_reason={skip_reason!r})"
    )


# ─── AC2: collectable fixture -> ([], "") ─────────────────────────────────────


def test_ac2_collectable_fixture_returns_no_violations(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _red_collect_probe  # noqa: PLC0415

    relpath = _write_test_file(tmp_path, "tests/test_1q_collect.py", _COLLECTABLE)
    full_path = str(tmp_path / relpath)

    violations, skip_reason = _red_collect_probe([full_path], str(tmp_path))

    assert violations == [], f"expected no violations for collectable fixture, got {violations!r}"
    assert skip_reason == "", f"expected empty skip_reason on ok probe, got {skip_reason!r}"


# ─── AC3: non-.py paths -> ([], "no_python_red_files") ───────────────────────


def test_ac3_non_python_paths_skip_with_reason(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _red_collect_probe  # noqa: PLC0415

    non_py = _write_test_file(tmp_path, "tests/notes.txt", "not python\n")
    full_path = str(tmp_path / non_py)

    violations, skip_reason = _red_collect_probe([full_path], str(tmp_path))

    assert violations == []
    assert skip_reason == "no_python_red_files", (
        f"expected skip_reason='no_python_red_files' for non-.py paths, got {skip_reason!r}"
    )


# ─── AC4: enforce=1 on non-collectable fixture -> error/E_RED_COLLECT_PROBE ──


def test_ac4_enforce_on_non_collectable_fixture_returns_error(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    monkeypatch.setenv("HAL_RED_COLLECT_PROBE_ENFORCE", "1")

    relpath = _write_test_file(tmp_path, "tests/test_1q_noncollect_enforce.py", _NON_COLLECTABLE)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", (
        f"expected status='error' when HAL_RED_COLLECT_PROBE_ENFORCE=1 on a non-collectable "
        f"RED file, got status={result.status!r}"
    )
    assert result.error_code == "E_RED_COLLECT_PROBE", (
        f"expected error_code='E_RED_COLLECT_PROBE', got {result.error_code!r}"
    )
    assert result.recoverable is True, "collect-probe violation must be recoverable=True"


# ─── AC5: warn-only (enforce unset) -> no reject + red_collect_probe_check event ──


def test_ac5_warn_only_emits_event_without_reject(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.delenv("HAL_RED_COLLECT_PROBE_ENFORCE", raising=False)

    relpath = _write_test_file(tmp_path, "tests/test_1q_noncollect_warn.py", _NON_COLLECTABLE)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    captured_events: list[tuple] = []

    def _capture_emit(event_type, payload, severity="info"):
        captured_events.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture_emit):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.error_code != "E_RED_COLLECT_PROBE", (
        "warn-only rollout (enforce unset) must NOT reject even with violations present"
    )

    matching = [e for e in captured_events if e[0] == "red_collect_probe_check"]
    assert matching, (
        f"expected a 'red_collect_probe_check' event; got events: "
        f"{[e[0] for e in captured_events]!r}"
    )
    payload = matching[0][1]
    assert payload.get("violations_n", 0) >= 1, (
        f"expected violations_n>=1 in red_collect_probe_check payload, got {payload!r}"
    )
    assert payload.get("enforced") is False, (
        f"expected enforced=False in red_collect_probe_check payload, got {payload!r}"
    )


# ─── AC6: kill-switch HAL_RED_COLLECT_PROBE_GATE=0 -> no reject + gate_disabled event ──


def test_ac6_kill_switch_disabled_emits_gate_disabled_event(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setenv("HAL_RED_COLLECT_PROBE_GATE", "0")

    relpath = _write_test_file(tmp_path, "tests/test_1q_noncollect_killswitch.py", _NON_COLLECTABLE)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    captured_events: list[tuple] = []

    def _capture_emit(event_type, payload, severity="info"):
        captured_events.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture_emit):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.error_code != "E_RED_COLLECT_PROBE", (
        "kill-switch HAL_RED_COLLECT_PROBE_GATE=0 must bypass the collect-probe gate entirely"
    )

    matching = [e for e in captured_events if e[0] == "gate_disabled" and e[1].get("gate") == "HAL_RED_COLLECT_PROBE_GATE"]
    assert matching, (
        f"expected a 'gate_disabled' event with gate='HAL_RED_COLLECT_PROBE_GATE'; got events: "
        f"{[(e[0], e[1].get('gate')) for e in captured_events]!r}"
    )
