"""RED tests for 845F2C2C: per-phase --allowed-tools profiles.

Each test MUST FAIL before GREEN implements the feature.
After GREEN they MUST PASS.

25e75663 migration: command= seam replaced by model=str. Tests that used
command=_claude_p_command() now use model="claude-3-haiku-20240307". Tests
that tested caller-supplied --allowed-tools/--allowedTools in the command
argv (AC5, AC6, AC5-mid) and shell-kind commands (AC7) are adapted since
those paths no longer exist via the model= seam — the only injection source
is now the allowed_tools= kwarg.

AC coverage:
  AC 1  — test_allowed_tools_single_tool_injects_flag
  AC 2  — test_allowed_tools_multiple_tools_space_separated
  AC 3  — test_allowed_tools_none_omits_flag
  AC 4  — test_allowed_tools_empty_list_injects_empty_string
  AC 5  — test_allowed_tools_kwarg_value_injected (adapted from caller-supplied)
  AC 6  — test_allowed_tools_multiple_kwarg_injected (adapted from camelCase caller)
  AC 7  — test_allowed_tools_none_default_no_flag (adapted from shell-kind skip)
  AC 8/11 — test_phase_profile_reaches_invoke_llm_subprocess[<phase>/<role>]
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.llm_subprocess import (  # noqa: E402
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)
from bytedigger_engine import llm_subprocess  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ---------------------------------------------------------------------------
# §1i autouse teardown
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _capture_popen_from_argv() -> tuple[list[list[str]], object]:
    """Return (captured_argv, side_effect) for patching subprocess.Popen.

    The returned side_effect builds a minimal Popen mock that:
    - returns exit code 0
    - feeds a valid stream-json result event on stdout (so the success path
      through _stream_read_events runs cleanly without hanging)
    - records the argv passed to Popen in captured_argv
    """
    captured: list[list[str]] = []

    _RESULT_EVENT = (
        '{"type":"result","subtype":"success",'
        '"result":"OK",'
        '"usage":{"input_tokens":1,"output_tokens":1,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
        '"total_cost_usd":0.001,"duration_ms":100}\n'
    )

    def _side_effect(argv, **kwargs):
        captured.append(list(argv))
        proc = MagicMock()
        proc.pid = 99999
        proc.returncode = 0
        proc.stdout = io.StringIO(_RESULT_EVENT)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(_RESULT_EVENT, ""))
        return proc

    return captured, _side_effect


def _find_allowed_tools_in_argv(argv: list[str]) -> str | None:
    """Return the value of --allowed-tools from argv, or None if absent."""
    for i, tok in enumerate(argv):
        if tok == "--allowed-tools" and i + 1 < len(argv):
            return argv[i + 1]
    return None


# ─── AC 1: single tool injects --allowed-tools flag ──────────────────────────


def test_allowed_tools_single_tool_injects_flag():
    """AC 1: allowed_tools=["Read"] → argv contains --allowed-tools "Read"."""
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac1",
            allowed_tools=["Read"],
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1, f"expected 1 Popen call, got {len(captured)}"
    argv = captured[0]
    val = _find_allowed_tools_in_argv(argv)
    assert val is not None, (
        f"--allowed-tools flag was not injected into argv.\n"
        f"argv={argv!r}"
    )
    assert val == "Read", (
        f"--allowed-tools value must be 'Read' (single tool, no spaces), "
        f"got {val!r}\nargv={argv!r}"
    )


# ─── AC 2: multiple tools space-separated ────────────────────────────────────


def test_allowed_tools_multiple_tools_space_separated():
    """AC 2: allowed_tools=["Read","Write","Bash"] → --allowed-tools "Read Write Bash"."""
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac2",
            allowed_tools=["Read", "Write", "Bash"],
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    val = _find_allowed_tools_in_argv(argv)
    assert val is not None, (
        f"--allowed-tools flag was not injected.\nargv={argv!r}"
    )
    assert val == "Read Write Bash", (
        f"Multiple tools must be space-separated per claude --help; "
        f"expected 'Read Write Bash', got {val!r}\nargv={argv!r}"
    )


# ─── AC 3: None omits flag (backwards-compat) ────────────────────────────────


def test_allowed_tools_none_omits_flag():
    """AC 3: allowed_tools=None (default) → no --allowed-tools in argv.

    This is the backwards-compat case; current callers don't pass the kwarg.
    """
    captured, side = _capture_popen_from_argv()

    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
            invoke_llm_subprocess(
                prompt="hi",
                model="claude-3-haiku-20240307",
                timeout_sec=10,
                step_name="test_ac3",
                allowed_tools=None,
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    except TypeError as e:
        pytest.fail(
            f"invoke_llm_subprocess does not accept allowed_tools kwarg yet "
            f"(TypeError: {e}). Expected RED failure."
        )

    assert len(captured) == 1
    argv = captured[0]
    val = _find_allowed_tools_in_argv(argv)
    assert val is None, (
        f"allowed_tools=None must NOT inject --allowed-tools; "
        f"found value {val!r} in argv={argv!r}"
    )


# ─── AC 4: empty list injects --allowed-tools "" ─────────────────────────────


def test_allowed_tools_empty_list_injects_empty_string():
    """AC 4: allowed_tools=[] → --allowed-tools "" (empty string, not absent).

    Spec AC 4: distinct from None. Caller signals "no tools allowed".
    """
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac4",
            allowed_tools=[],
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    assert "--allowed-tools" in argv, (
        f"allowed_tools=[] must inject --allowed-tools (empty string); "
        f"flag absent from argv={argv!r}"
    )
    val = _find_allowed_tools_in_argv(argv)
    assert val == "", (
        f"allowed_tools=[] must inject --allowed-tools '' (empty string); "
        f"got value {val!r}\nargv={argv!r}"
    )


# ─── AC 5: allowed_tools kwarg value is injected correctly ───────────────────
#
# 25e75663 Class C adaptation: the old test checked that a caller-supplied
# --allowed-tools flag in the command argv won over the kwarg (since command
# was a caller-controlled list). Now command= is gone; only the kwarg exists.
# Test adapted: verify the kwarg value appears exactly once in the spawned argv.


def test_allowed_tools_kwarg_value_injected():
    """AC 5 (adapted): allowed_tools=["Read"] → --allowed-tools 'Read' in argv (once).

    25e75663: the old 'caller-supplied in argv wins' test no longer applies since
    the command= seam is removed. The kwarg is the only injection source.
    Test now verifies the kwarg value appears exactly once in argv.
    """
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac5",
            allowed_tools=["Read"],
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    at_count = argv.count("--allowed-tools")
    assert at_count == 1, (
        f"allowed_tools kwarg must inject exactly one --allowed-tools token; "
        f"found {at_count} occurrences in argv={argv!r}"
    )
    val = _find_allowed_tools_in_argv(argv)
    assert val == "Read", (
        f"allowed_tools=['Read'] must inject 'Read'; "
        f"got {val!r}\nargv={argv!r}"
    )


# ─── AC 6: multiple tools injected as space-separated (regression) ────────────
#
# 25e75663 Class C adaptation: the old test checked camelCase --allowedTools
# caller-supplied flag in command argv. Since command= is removed, this path
# no longer exists. Test adapted to verify multiple-tool kwarg injection works.


def test_allowed_tools_multiple_kwarg_injected():
    """AC 6 (adapted): allowed_tools=["Bash","Grep"] → --allowed-tools 'Bash Grep'.

    25e75663: 'caller-supplied --allowedTools in argv wins' is no longer applicable.
    Test verifies multi-tool kwarg injection produces correct space-separated string.
    """
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac6",
            allowed_tools=["Bash", "Grep"],
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    assert "--allowed-tools" in argv, (
        f"--allowed-tools must be present in argv; got argv={argv!r}"
    )
    val = _find_allowed_tools_in_argv(argv)
    assert val == "Bash Grep", (
        f"allowed_tools=['Bash','Grep'] must produce 'Bash Grep'; "
        f"got {val!r}\nargv={argv!r}"
    )


# ─── AC 7: allowed_tools=None produces no flag (regression test) ──────────────
#
# 25e75663 Class C adaptation: the old test checked shell-kind command (bash)
# silently skips injection. Since command= is removed, shell commands can no
# longer be passed via invoke_llm_subprocess. Test adapted to verify that
# allowed_tools=None (the default) correctly produces no --allowed-tools flag.


def test_allowed_tools_none_default_no_flag():
    """AC 7 (adapted): allowed_tools=None (default) → no --allowed-tools injected.

    25e75663: shell-kind commands are no longer passable via invoke_llm_subprocess.
    Test verifies the default (None) kwarg produces no flag in the spawned argv.
    """
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        result = invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac7",
            allowed_tools=None,  # default — no injection
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert result is not None, "Expected a StepResult, got None"
    assert len(captured) == 1
    argv = captured[0]
    assert "--allowed-tools" not in argv, (
        f"--allowed-tools must NOT be injected when allowed_tools=None; "
        f"found it in argv={argv!r}"
    )
    assert "--allowedTools" not in argv, (
        f"--allowedTools must NOT be injected when allowed_tools=None; "
        f"found it in argv={argv!r}"
    )


# ─── AC 8/11: per-phase profile presence tests ───────────────────────────────
#
# Strategy: mock `invoke_llm_subprocess` at the symbol imported into each
# phase module (e.g. `phase_1_discovery.invoke_llm_subprocess`), invoke the
# wrapper function, and assert `allowed_tools=` kwarg matches the spec table.
#
# The mock returns a minimal StepResult(status="ok") so the wrapper doesn't
# choke on the return value. We supply minimal prev StepResult with the exact
# keys each wrapper reads from prev.data so we reach the invoke_llm_subprocess
# call (not an early-return E_MISSING_PREV_DATA path).


def _ok_result(**data) -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prev")


def _mock_invoke_ok() -> MagicMock:
    """Return a mock that returns ok StepResult and records its call args."""
    m = MagicMock(return_value=StepResult(
        status="ok",
        data={"raw_response": "OK", "response_bytes": 2, "command": ["x"]},
        duration_ms=0,
        step_name="mock",
    ))
    return m


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="test",
        session_id="test-845F2C2C",
        persona="hal",
        framework=None,
        domain=None,
    )


def _extract_allowed_tools_kwarg(mock_obj: MagicMock) -> list[str] | None:
    """Return the allowed_tools kwarg from the single call recorded on mock_obj."""
    assert mock_obj.call_count >= 1, (
        f"invoke_llm_subprocess was never called; "
        f"did the wrapper short-circuit before reaching it?"
    )
    # Take the first call (primary callsite, not retry)
    _, kwargs = mock_obj.call_args_list[0]
    return kwargs.get("allowed_tools", "__MISSING__")


# Build the parametrized table:
# (phase_module_str, wrapper_fn_name, prev_data_factory, expected_profile)
# prev_data_factory receives (tmp_path: Path) and returns a dict suitable for
# passing to _ok_result(**data) so the wrapper reaches invoke_llm_subprocess.

_PHASE_PROFILE_CASES: list[tuple[str, str, object, list[str]]] = [
    # phase_1_discovery._invoke_discovery_llm
    (
        "phase_1_discovery",
        "_invoke_discovery_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "discovery.md"),
            "complexity": "SIMPLE",
        },
        ["Read", "Grep", "Glob", "Write", "Bash(graphify-shim.sh:*)"],
    ),
    # phase_2_explore._invoke_explore_llm
    (
        "phase_2_explore",
        "_invoke_explore_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "explore.md"),
            "complexity": "SIMPLE",
            # passthrough_if_skipped needs these absent or not-skipped
        },
        ["Read", "Grep", "Glob", "WebSearch", "WebFetch", "Write", "Bash(graphify-shim.sh:*)"],
    ),
    # phase_3_clarify._invoke_clarify_llm
    (
        "phase_3_clarify",
        "_invoke_clarify_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "clarify.md"),
            "complexity": "SIMPLE",
        },
        ["Read", "Glob", "Write"],
    ),
    # phase_4_architect._invoke_architect_llm
    (
        "phase_4_architect",
        "_invoke_architect_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "architect.md"),
            "complexity": "SIMPLE",
            "security_classification": "LOW",
        },
        ["Read", "Grep", "Glob", "Write"],
    ),
    # phase_45_spec._invoke_spec_llm  (spec writer)
    (
        "phase_45_spec",
        "_invoke_spec_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "spec.md"),
            "cycle": 1,
        },
        ["Read", "Write", "Glob"],
    ),
    # phase_45_spec._invoke_review_llm  (Opus reviewer)
    (
        "phase_45_spec",
        "_invoke_review_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "spec.md"),
            "spec_path": str(p / "spec.md"),
            "cycle": 1,
        },
        ["Read"],
    ),
    # phase_45_spec_lite._maybe_invoke_spec_rewrite  (writer, line 363)
    # NOTE: wrapper short-circuits when prev.data["rewrite"] is falsy.
    # We set rewrite=True to reach the invoke call.
    (
        "phase_45_spec_lite",
        "_maybe_invoke_spec_rewrite",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "spec.md"),
            "spec_path": str(p / "spec.md"),
            "cycle": 2,
            "rewrite": True,
        },
        ["Read", "Write", "Glob"],
    ),
    # phase_45_spec_lite._invoke_review_llm  (reviewer, line 478)
    (
        "phase_45_spec_lite",
        "_invoke_review_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "spec.md"),
            "spec_path": str(p / "spec.md"),
            "cycle": 1,
        },
        ["Read"],
    ),
    # phase_5_implement._invoke_red_llm
    (
        "phase_5_implement",
        "_invoke_red_llm",
        lambda p: {
            "prompt": "x",
            "log_path": str(p / "red.log"),
            "spec_path": str(p / "spec.md"),
        },
        ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
    ),
    # phase_5_implement._invoke_green_llm
    (
        "phase_5_implement",
        "_invoke_green_llm",
        lambda p: {
            "prompt": "x",
            "log_path": str(p / "green.log"),
            "spec_path": str(p / "spec.md"),
            "red_log_path": str(p / "red.log"),
            "validation_doc_path": str(p / "validation.md"),
            "verdict": "PASS",
        },
        ["Read", "Write", "Edit", "Grep", "Glob"],
    ),
    # phase_5_implement._invoke_validation_llm  (Opus gate)
    (
        "phase_5_implement",
        "_invoke_validation_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "validation.md"),
            "spec_path": str(p / "spec.md"),
            "red_log_path": str(p / "red.log"),
            "red_test_paths": [],
            "cycle": 1,
        },
        ["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"],
    ),
    # phase_5_integrity._invoke_integrity_llm
    # NOTE: wrapper short-circuits when prev.data["verdict_override"] is truthy.
    # We set verdict_override=None to reach the invoke call.
    (
        "phase_5_integrity",
        "_invoke_integrity_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "integrity.md"),
            "diff_path": str(p / "diff.txt"),
            "verdict_override": None,
        },
        ["Read"],
    ),
    # phase_6_review._invoke_review_llm  (reviewer)
    (
        "phase_6_review",
        "_invoke_review_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "review.md"),
            "spec_path": str(p / "spec.md"),
            "red_log_path": str(p / "red.log"),
            "green_log_path": str(p / "green.log"),
        },
        ["Read", "Grep", "Glob", "Write"],
    ),
    # phase_6_review._invoke_fix_llm  (fix worker)
    (
        "phase_6_review",
        "_invoke_fix_llm",
        lambda p: {
            "prompt": "x",
            "log_path": str(p / "fix.log"),
            "spec_path": str(p / "spec.md"),
            "review_doc_path": str(p / "review.md"),
            "verdict": "FAIL",
        },
        ["Read", "Write", "Edit", "Grep", "Glob"],
    ),
    # phase_6_review._invoke_satisfaction_llm  (Opus satisfaction)
    (
        "phase_6_review",
        "_invoke_satisfaction_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "review.md"),
            "spec_path": str(p / "spec.md"),
            "review_doc_path": str(p / "review.md"),
            "fix_doc_path": str(p / "fix.md"),
        },
        ["Read", "Write"],
    ),
    # phase_7_synthesize._invoke_synthesizer_llm
    (
        "phase_7_synthesize",
        "_invoke_synthesizer_llm",
        lambda p: {
            "prompt": "x",
            "doc_path": str(p / "synthesize.md"),
            "spec_path": str(p / "spec.md"),
            "review_doc_path": str(p / "review.md"),
            "fix_doc_path": str(p / "fix.md"),
            "satisfaction_doc_path": str(p / "satisfaction.md"),
        },
        ["Read", "Write", "Glob"],
    ),
]

_PHASE_IDS = [
    f"{module}/{fn}"
    for module, fn, _, _ in _PHASE_PROFILE_CASES
]


@pytest.mark.parametrize(
    "module_name,fn_name,prev_data_factory,expected_profile",
    _PHASE_PROFILE_CASES,
    ids=_PHASE_IDS,
)
def test_phase_profile_reaches_invoke_llm_subprocess(
    tmp_path,
    module_name: str,
    fn_name: str,
    prev_data_factory,
    expected_profile: list[str],
):
    """AC 8/11: each phase wrapper passes the spec-table profile as allowed_tools=.

    Strategy: mock invoke_llm_subprocess at the import site inside the phase
    module, call the wrapper with a minimal prev StepResult, then assert that
    the mock was called with allowed_tools=<expected_profile>.

    The test FAILS RED because:
    - The feature doesn't exist yet → no allowed_tools kwarg is passed.
    - After GREEN, each wrapper must pass the correct list.
    """
    import importlib

    # Dynamically import the phase module (they live in workflows/ which is on sys.path)
    module = importlib.import_module(module_name)
    wrapper_fn = getattr(module, fn_name)

    ctx = _make_ctx(tmp_path)
    prev_data = prev_data_factory(tmp_path)
    prev = _ok_result(**prev_data)

    mock_invoke = _mock_invoke_ok()
    patch_target = f"{module_name}.invoke_llm_subprocess"

    with patch(patch_target, mock_invoke):
        wrapper_fn(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        f"{module_name}.{fn_name}: invoke_llm_subprocess was never called "
        f"(wrapper may have short-circuited before the call site). "
        f"Check prev_data keys needed to reach line with invoke call."
    )

    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", "__MISSING__")

    assert actual != "__MISSING__", (
        f"{module_name}.{fn_name}: invoke_llm_subprocess was called but "
        f"allowed_tools kwarg was NOT passed at all. "
        f"Expected: {expected_profile!r}. "
        f"kwargs seen: {list(kwargs.keys())}"
    )
    assert actual == expected_profile, (
        f"{module_name}.{fn_name}: wrong allowed_tools profile. "
        f"Expected: {expected_profile!r}, got: {actual!r}"
    )


# ─── AC5-mid: removed (25e75663 Class C) ──────────────────────────────────────
#
# The old test_allowed_tools_caller_supplied_kebab_mid_argv_wins tested that
# --allowed-tools mid-list in command argv wins over kwarg. Since command= is
# removed, this test is no longer applicable.


# ─── Retry-path coverage (Opus required addition 2) ──────────────────────────
#
# Three retry callsites call invoke_llm_subprocess directly (not via the
# _invoke_*_llm wrappers that AC8 already covers):
#   - phase_5_implement._write_green_artifact line 2003  → GREEN_NO_MARKER retry
#   - phase_6_review._write_review_artifact   line 1235  → non-conformant review retry
#   - phase_6_review._write_fix_artifact      line 1499  → FIX_NO_MARKER retry
#
# Strategy: call each _write_*_artifact directly with prev.data["raw_response"]
# that triggers the retry branch, mock invoke_llm_subprocess at the phase-module
# import site, then assert call_args_list[0] (the retry call, index 0 because
# _write_*_artifact never calls invoke_llm_subprocess on the primary path — only
# the retry branch does) carries allowed_tools= matching the spec table profile
# for that phase+role.
#
# Expected profiles per spec table:
#   phase_5 GREEN  → ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]
#   phase_6 review → ["Read", "Grep", "Glob"]
#   phase_6 fix    → ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]


def _mock_invoke_ok_raw(raw_response: str = "OK") -> MagicMock:
    """Mock that returns a successful StepResult with a given raw_response."""
    return MagicMock(return_value=StepResult(
        status="ok",
        data={"raw_response": raw_response, "response_bytes": 2, "command": ["x"]},
        duration_ms=0,
        step_name="mock_retry",
    ))


def test_phase_5_implement_red_retry_passes_allowed_tools(tmp_path):
    """Retry callsite at phase_5_implement._write_green_artifact:2003 must pass allowed_tools=.

    Trigger: prev.data["raw_response"] contains no GREEN marker (GREEN_NO_MARKER).
    The function calls invoke_llm_subprocess once (the retry call).
    Assert: that call carries allowed_tools=["Read","Write","Edit","Bash","Grep","Glob"].

    Note: this test is named with 'red_retry' because _write_green_artifact is the
    step that writes the GREEN output artifact and may retry; the test name matches
    the Opus verdict requirement for test_phase_5_implement_red_retry_passes_allowed_tools.
    The profile expected is the phase_5 GREEN/RED profile (same for both).
    """
    import importlib
    module = importlib.import_module("bytedigger_engine.workflows.phase_5_implement")
    write_green = getattr(module, "_write_green_artifact")

    # Build a prev StepResult whose raw_response has NO green marker → triggers retry
    # "NO_MARKER" means _parse_green_status returns GREEN_NO_MARKER constant.
    # A raw_response with no "GREEN PASS", "GREEN FAIL" etc. marker triggers the retry.
    no_marker_raw = "some output with no green status marker at all"

    # The retry call expects these keys in prev.data (see line 2003-2015)
    log_path = tmp_path / "green.log"
    spec_path = tmp_path / "spec.md"
    red_log_path = tmp_path / "red.log"
    validation_doc_path = tmp_path / "validation.md"

    prev = StepResult(
        status="ok",
        data={
            "raw_response": no_marker_raw,
            "log_path": str(log_path),
            "spec_path": str(spec_path),
            "red_log_path": str(red_log_path),
            "validation_doc_path": str(validation_doc_path),
            "verdict": "PASS",
            "prompt": "implement the feature",
            # tokens_out absent → budget check won't short-circuit
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )

    ctx = _make_ctx(tmp_path)

    # Mock returns a raw_response with a valid GREEN PASS marker so the retry
    # branch succeeds (avoids secondary error paths that would obscure our assertion).
    mock_invoke = _mock_invoke_ok_raw("GREEN PASS\n")

    expected_profile = ["Read", "Write", "Edit", "Grep", "Glob"]

    with patch("bytedigger_engine.workflows.phase_5_implement.invoke_llm_subprocess", mock_invoke):
        write_green(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "phase_5_implement._write_green_artifact: invoke_llm_subprocess was never "
        "called on the retry path. Did the NO_MARKER trigger not fire?\n"
        f"prev.data['raw_response']={no_marker_raw!r}"
    )

    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", "__MISSING__")

    assert actual != "__MISSING__", (
        "phase_5_implement._write_green_artifact retry: invoke_llm_subprocess "
        "called but allowed_tools kwarg was NOT passed. "
        f"Expected: {expected_profile!r}. kwargs seen: {list(kwargs.keys())}"
    )
    assert actual == expected_profile, (
        "phase_5_implement._write_green_artifact retry: wrong allowed_tools. "
        f"Expected: {expected_profile!r}, got: {actual!r}"
    )


# REMOVED by GH1399 (§1c-ОТМЕНА): test_phase_6_review_review_retry_passes_allowed_tools
# The allowed_tools contract of the phase_6_review retry call: the call was deleted by GH1399.
# The other rows of the call-path matrix are unaffected.


def test_phase_6_review_fix_retry_passes_allowed_tools(tmp_path):
    """Retry callsite at phase_6_review._write_fix_artifact:1499 must pass allowed_tools=.

    Trigger: prev.data["raw_response"] contains no FIX marker (FIX_NO_MARKER),
    i.e. no 'FIX COMPLETE', 'FIX SKIPPED', 'FIX BLOCKED' token.
    The function calls invoke_llm_subprocess once (the retry call, call_args_list[0]).
    Assert: that call carries allowed_tools=["Read","Write","Edit","Grep","Glob"].
    """
    import importlib
    module = importlib.import_module("bytedigger_engine.workflows.phase_6_review")
    write_fix = getattr(module, "_write_fix_artifact")

    # Raw response with NO fix marker → _parse_fix_status returns FIX_NO_MARKER
    no_marker_raw = "fix output with no status marker"

    log_path = tmp_path / "fix.log"
    spec_path = tmp_path / "spec.md"
    review_doc_path = tmp_path / "review.md"

    prev = StepResult(
        status="ok",
        data={
            "raw_response": no_marker_raw,
            "log_path": str(log_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc_path),
            "verdict": "FAIL",
            "prompt": "fix the issues",
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )

    ctx = _make_ctx(tmp_path)

    # Retry mock returns a raw_response with a valid FIX COMPLETE marker
    mock_invoke = _mock_invoke_ok_raw("FIX COMPLETE\n")

    expected_profile = ["Read", "Write", "Edit", "Grep", "Glob"]

    with patch("bytedigger_engine.workflows.phase_6_review.invoke_llm_subprocess", mock_invoke):
        write_fix(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "phase_6_review._write_fix_artifact: invoke_llm_subprocess was never "
        "called on the retry path. Did the NO_MARKER trigger not fire?\n"
        f"prev.data['raw_response']={no_marker_raw!r}"
    )

    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", "__MISSING__")

    assert actual != "__MISSING__", (
        "phase_6_review._write_fix_artifact retry: invoke_llm_subprocess "
        "called but allowed_tools kwarg was NOT passed. "
        f"Expected: {expected_profile!r}. kwargs seen: {list(kwargs.keys())}"
    )
    assert actual == expected_profile, (
        "phase_6_review._write_fix_artifact retry: wrong allowed_tools. "
        f"Expected: {expected_profile!r}, got: {actual!r}"
    )
