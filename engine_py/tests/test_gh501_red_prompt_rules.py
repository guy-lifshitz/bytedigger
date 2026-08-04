"""RED tests for GH501 — embed §1i/§1p into phase_5 RED-writer prompt +
§1q literal lint.

Spec: SHARED/memory/Decisions/2026-07-10_GH501_red_prompt_1i_1p_spec.md

Not-yet-existing symbols (`_spec_wants_1p`, `_spec_wants_1i`, error code
`E_RED_1Q_EXEC_IMPORT`) are accessed via getattr/deferred-import INSIDE test
bodies only (D1CF5FDF/§1q) so this file COLLECTS cleanly and fails at assert
time, never at collection time.

§1i: no singleton/time-dependent resource under test here (the spec's §1i
block is about content injected into the PROMPT TEXT, not a real singleton) —
all fixtures (spec files, RED test files) are pre-staged deterministically
before invoking the UUT, per workflows.md §1i.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers (mirror test_phase_5_gh340_security_fragment.py / test_7AD3D393) ──


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh501",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_spec(scratchpad: Path, spec_text: str) -> None:
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "build-spec.md").write_text(spec_text, encoding="utf-8")


def _build_red(tmp_path: Path, spec_text: str | None, **org_extra):
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    if spec_text is not None:
        _write_spec(scratchpad, spec_text)
    ctx = _make_ctx(scratchpad, **org_extra)
    return _build_red_prompt(ctx, None)


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


_SUITE_SAFE_1Q_VIOLATION = """\
import importlib.util

spec = importlib.util.spec_from_file_location("mod", "/tmp/foo.py")


def test_something():
    assert spec is not None
"""

_SUITE_SAFE_1Q_EXEC_MODULE = """\
import importlib.util

spec = importlib.util.spec_from_file_location("mod", "/tmp/foo.py")
mod = importlib.util.module_from_spec(spec)


def test_exec():
    spec.loader.exec_module(mod)
    assert mod is not None
"""

_SUITE_SAFE_1Q_CLEAN = """\
def test_clean():
    assert 1 == 1
"""

_SUITE_SAFE_1Q_PRAGMA = """\
import importlib.util

spec = importlib.util.spec_from_file_location("mod", "/tmp/foo.py")  # 1q: allow


def test_pragma():
    assert spec is not None
"""


# ─── AC1/AC2: §1p block presence/absence, gated on `.sh` token ────────────────


def test_ac1_spec_with_sh_token_prompt_contains_1p_and_grep_rule(tmp_path):
    result = _build_red(tmp_path, "Fix bug in scripts/foo.sh discovered by CI")
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "§1p" in prompt, "spec mentions .sh — prompt must contain §1p block"
    assert "grep -E/-i" in prompt, "§1p block must include the grep -E/-i directive"


def test_ac2_spec_without_sh_token_prompt_omits_1p_block(tmp_path):
    result = _build_red(tmp_path, "Add a new Python helper function foo()")
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "§1p" not in prompt, "spec has no .sh token — §1p block must NOT appear"


# ─── AC3/AC4: §1i block presence/absence, gated on singleton/time tokens ──────


def test_ac3_spec_with_singleton_token_prompt_contains_1i_and_prestage(tmp_path):
    result = _build_red(tmp_path, "Implement a singleton lock file guard for the port bind")
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "§1i" in prompt, "spec mentions singleton — prompt must contain §1i block"
    assert "pre-stage" in prompt, "§1i block must include the pre-stage directive"


def test_ac4_spec_without_1i_tokens_prompt_omits_1i_block(tmp_path):
    result = _build_red(tmp_path, "Add a new Python helper function foo()")
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "§1i" not in prompt, "spec has no §1i tokens — §1i block must NOT appear"


# ─── AC5: missing spec_path → prompt assembles without either block, no exception ──


def test_ac5_missing_spec_path_assembles_without_exception_or_new_blocks(tmp_path):
    result = _build_red(tmp_path, spec_text=None)
    assert result.status == "ok", (
        f"_build_red_prompt must not raise/error when spec_path is absent; "
        f"got status={result.status!r} error={getattr(result, 'error', None)!r}"
    )
    prompt = result.data["prompt"]
    assert "§1p" not in prompt
    assert "§1i" not in prompt


# ─── AC6: module-level detect helpers exist + return bool on bare strings ─────


def test_ac6_spec_wants_1p_and_1i_are_module_level_callables_returning_bool():
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    fn_1p = getattr(p5, "_spec_wants_1p", None)
    fn_1i = getattr(p5, "_spec_wants_1i", None)
    assert callable(fn_1p), "_spec_wants_1p must exist as a module-level callable in phase_5_implement"
    assert callable(fn_1i), "_spec_wants_1i must exist as a module-level callable in phase_5_implement"

    r1 = fn_1p("touch scripts/build.sh and run it")
    r2 = fn_1p("plain python change, no shell")
    r3 = fn_1i("guard the singleton lock before sleep(2)")
    r4 = fn_1i("plain python change, nothing time-related")

    assert isinstance(r1, bool) and r1 is True
    assert isinstance(r2, bool) and r2 is False
    assert isinstance(r3, bool) and r3 is True
    assert isinstance(r4, bool) and r4 is False


# ─── AC7/AC8: §1q literal lint rejects spec_from_file_location / exec_module ──


def test_ac7_verify_red_lint_rules_rejects_spec_from_file_location(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    # GH602: delta-retry pinned off — asserts the terminal path
    monkeypatch.setenv("HAL_RED_PREFLIGHT_DELTA_RETRY", "0")
    relpath = _write_test_file(tmp_path, "tests/test_1q_violation.py", _SUITE_SAFE_1Q_VIOLATION)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_1Q_EXEC_IMPORT", (
        f"expected error_code='E_RED_1Q_EXEC_IMPORT' for spec_from_file_location hit, "
        f"got {result.error_code!r} (status={result.status!r})"
    )
    assert result.status == "error"
    assert result.recoverable is False, "§1q violation must be recoverable=False"


def test_ac8_verify_red_lint_rules_rejects_exec_module(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    # GH602: delta-retry pinned off — asserts the terminal path
    monkeypatch.setenv("HAL_RED_PREFLIGHT_DELTA_RETRY", "0")
    relpath = _write_test_file(tmp_path, "tests/test_1q_exec_module.py", _SUITE_SAFE_1Q_EXEC_MODULE)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_1Q_EXEC_IMPORT", (
        f"expected error_code='E_RED_1Q_EXEC_IMPORT' for exec_module hit, "
        f"got {result.error_code!r} (status={result.status!r})"
    )
    assert result.status == "error"
    assert result.recoverable is False


# ─── AC9: escape pragma suppresses the reject ─────────────────────────────────


def test_ac9_pragma_1q_allow_suppresses_reject(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    relpath = _write_test_file(tmp_path, "tests/test_1q_pragma.py", _SUITE_SAFE_1Q_PRAGMA)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code != "E_RED_1Q_EXEC_IMPORT", (
        f"pragma '# 1q: allow' must suppress the reject; got error_code={result.error_code!r}"
    )


# ─── AC10: kill-switch disabled → PASS + gate_disabled event ─────────────────


def test_ac10_kill_switch_disabled_emits_gate_disabled_event(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setenv("HAL_RED_1Q_GATE", "0")

    relpath = _write_test_file(tmp_path, "tests/test_1q_gated.py", _SUITE_SAFE_1Q_VIOLATION)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    captured_events: list[tuple] = []

    def _capture_emit(event_type, payload, severity="info"):
        captured_events.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture_emit):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.error_code != "E_RED_1Q_EXEC_IMPORT", (
        "kill-switch HAL_RED_1Q_GATE=0 must bypass the §1q lint even with a violation present"
    )
    emitted_names = [e[0] for e in captured_events]
    assert "gate_disabled" in emitted_names, (
        f"expected 'gate_disabled' event when HAL_RED_1Q_GATE=0; got events: {emitted_names!r}"
    )


# ─── AC11: violation emits red_1q_exec_import_violation event ────────────────


def test_ac11_violation_emits_red_1q_exec_import_violation_event(tmp_path):
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    relpath = _write_test_file(tmp_path, "tests/test_1q_event.py", _SUITE_SAFE_1Q_VIOLATION)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    captured_events: list[tuple] = []

    def _capture_emit(event_type, payload, severity="info"):
        captured_events.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture_emit):
        result = p5._verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_1Q_EXEC_IMPORT"
    emitted_names = [e[0] for e in captured_events]
    assert "red_1q_exec_import_violation" in emitted_names, (
        f"expected 'red_1q_exec_import_violation' event on violation; got events: {emitted_names!r}"
    )
