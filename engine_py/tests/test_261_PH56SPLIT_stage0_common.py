"""RED tests for #261 Stage 0 — extract 13 duplicated helpers + 2 constants into
`phase_workflows_common.py` (ship-id 2C61F0A0).

AC1–AC7 from spec §3.  pre-GREEN: all 7 tests FAIL.

CRITICAL — §1q / D1CF5FDF non-collectable-RED discipline:
`phase_workflows_common` does NOT exist yet.  Every import of it is deferred
INSIDE the test function body so this file COLLECTS cleanly and FAILS at
assert/runtime, never at collection time.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── path bootstrap (mirrors all sibling tests) ────────────────────────────────
HERE = Path(__file__).parent
ENGINE_PY = HERE.parent
WORKFLOWS = ENGINE_PY / "workflows"

if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

# ── module-level imports of EXISTING modules only ────────────────────────────
# phase_5_implement and phase_6_review exist today — safe to import here.
import phase_5_implement as p5  # noqa: E402
import phase_6_review as p6  # noqa: E402

# ── canonical name lists ──────────────────────────────────────────────────────
_HELPER_NAMES = [
    "_git_op_with_lock_retry",
    "_last_marker_wins",
    "_maybe_role_template",
    "_worktree_edit_boundary_block",
    "_read_engine_mode",
    "_resolve_model",
    "_maybe_emit_cross_tree_warning",
    "_emit_safe",
    "_filter_gitignored_paths",
    "_verify_no_cross_tree_edits",
    "_resolve_scratchpad",
    "_revert_cross_tree_modifications",
    "_read_first_block",
]
_CONST_NAMES = [
    "_CROSS_TREE_PROMPT_TEMPLATE",
    "_ENGINE_MODE_RE",
]
_ALL_NAMES = _HELPER_NAMES + _CONST_NAMES

_P5_SRC = Path(p5.__file__).read_text(encoding="utf-8")
_P6_SRC = Path(p6.__file__).read_text(encoding="utf-8")
_COMMON_PATH = WORKFLOWS / "phase_workflows_common.py"


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — single-source-of-truth: each def/const appears 0× in p5, 0× in p6,
#       1× in common (pre-GREEN: still defined in p5/p6, common absent → FAIL).
# ─────────────────────────────────────────────────────────────────────────────

def test_ac1_helpers_defined_only_in_common():
    """AC1: Every helper def appears exactly 0 times in phase_5, 0 in phase_6,
    1 time in phase_workflows_common. Pre-GREEN FAILS because phase_5/phase_6
    still carry the defs and common does not exist."""
    assert _COMMON_PATH.exists(), (
        f"phase_workflows_common.py does not exist at {_COMMON_PATH} — "
        "GREEN has not created it yet"
    )
    common_src = _COMMON_PATH.read_text(encoding="utf-8")

    failures = []
    for name in _HELPER_NAMES:
        pat = re.compile(rf"^(?:async )?def {re.escape(name)}\(", re.MULTILINE)
        n_p5 = len(pat.findall(_P5_SRC))
        n_p6 = len(pat.findall(_P6_SRC))
        n_common = len(pat.findall(common_src))
        if n_p5 != 0 or n_p6 != 0 or n_common != 1:
            failures.append(
                f"{name}: p5={n_p5} (want 0), p6={n_p6} (want 0), common={n_common} (want 1)"
            )
    for name in _CONST_NAMES:
        pat = re.compile(rf"^{re.escape(name)} =", re.MULTILINE)
        n_p5 = len(pat.findall(_P5_SRC))
        n_p6 = len(pat.findall(_P6_SRC))
        n_common = len(pat.findall(common_src))
        if n_p5 != 0 or n_p6 != 0 or n_common != 1:
            failures.append(
                f"{name}: p5={n_p5} (want 0), p6={n_p6} (want 0), common={n_common} (want 1)"
            )
    assert not failures, "Single-source violations:\n" + "\n".join(failures)


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — re-export identity: phase_5.X is common.X AND phase_6.X is common.X
#       (deferred import of phase_workflows_common — it doesn't exist yet).
# ─────────────────────────────────────────────────────────────────────────────

def test_ac2_reexport_identity_both_modules():
    """AC2: Importing all 13 helpers + 2 constants from both phase_5_implement
    and phase_6_review resolves to the SAME object as in phase_workflows_common.
    Deferred import inside body so collection succeeds pre-GREEN."""
    # Deferred import — will raise ImportError pre-GREEN (FAIL, not hang).
    import phase_workflows_common as pwc  # type: ignore  # noqa: F401

    failures = []
    for name in _ALL_NAMES:
        p5_val = getattr(p5, name, _MISSING := object())
        p6_val = getattr(p6, name, _MISSING)
        common_val = getattr(pwc, name, _MISSING)

        if p5_val is _MISSING:
            failures.append(f"phase_5_implement.{name} missing")
        elif p5_val is not common_val:
            failures.append(f"phase_5_implement.{name} is not phase_workflows_common.{name}")

        if p6_val is _MISSING:
            failures.append(f"phase_6_review.{name} missing")
        elif p6_val is not common_val:
            failures.append(f"phase_6_review.{name} is not phase_workflows_common.{name}")

    assert not failures, "Re-export identity failures:\n" + "\n".join(failures)


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — _emit_safe superset reaches phase_6: severity kwarg accepted
#       (pre-GREEN phase_6._emit_safe lacks the kwarg → TypeError → FAIL).
# ─────────────────────────────────────────────────────────────────────────────

def test_ac3_emit_safe_severity_kwarg_accepted_via_phase6(monkeypatch):
    """AC3: Calling phase_6_review._emit_safe(evt, {}, severity="error") is
    accepted without TypeError. Pre-GREEN phase_6's copy lacks the kwarg."""
    # Patch get_current_run to return None so _emit_safe short-circuits cleanly.
    monkeypatch.setattr(p6.telemetry_ctx, "get_current_run", lambda: None)
    # Must not raise TypeError — if phase_6 still has the old 2-param signature
    # this call raises TypeError: _emit_safe() got an unexpected keyword argument
    p6._emit_safe("test_event_261_ac3", {}, severity="error")


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — _verify_no_cross_tree_edits in phase_6 routes through git_port.git_read
#       (deferred patch on phase_workflows_common; also asserts source is clean).
# ─────────────────────────────────────────────────────────────────────────────

def test_ac4_verify_no_cross_tree_edits_routes_via_git_port(tmp_path):
    """AC4: phase_6_review._verify_no_cross_tree_edits routes through
    git_port.git_read (not raw bounded_run).  Deferred import of
    phase_workflows_common inside body."""
    import phase_workflows_common as pwc  # type: ignore

    # Fake git_port.git_read: returns rc=0, stdout with a worktree entry
    # pointing to tmp_path itself (so is_relative_to fires and returns early —
    # the important thing is the mock gets called, not the full detection path).
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = f"worktree {tmp_path}\nHEAD abc\n"
    fake_result.stderr = ""

    call_log: list = []

    def recording_git_read(args, *, cwd, timeout=10):
        call_log.append(args)
        return fake_result

    with patch.object(pwc.git_port, "git_read", side_effect=recording_git_read):
        p6._verify_no_cross_tree_edits(tmp_path)

    assert call_log, (
        "git_port.git_read was never called — phase_6._verify_no_cross_tree_edits "
        "is still using raw bounded_run instead of routing through git_port"
    )

    # Also assert the common helper's source segment no longer references bounded_run.
    assert _COMMON_PATH.exists(), "phase_workflows_common.py does not exist yet"
    common_src = _COMMON_PATH.read_text(encoding="utf-8")
    # Find the function body of _verify_no_cross_tree_edits in common.
    fn_match = re.search(
        r"(def _verify_no_cross_tree_edits\(.*?)(?=\ndef |\Z)",
        common_src,
        re.DOTALL,
    )
    assert fn_match, "_verify_no_cross_tree_edits not found in phase_workflows_common.py"
    fn_body = fn_match.group(1)
    assert "bounded_run" not in fn_body, (
        "bounded_run token found in _verify_no_cross_tree_edits in phase_workflows_common — "
        "function should use git_port.git_read only"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — _filter_gitignored_paths routes through git_port.git_read
# ─────────────────────────────────────────────────────────────────────────────

def test_ac5_filter_gitignored_paths_routes_via_git_port(tmp_path):
    """AC5: phase_6_review._filter_gitignored_paths routes through
    git_port.git_read (not raw bounded_run).  Deferred import of
    phase_workflows_common inside body."""
    import phase_workflows_common as pwc  # type: ignore

    fake_result = MagicMock()
    fake_result.returncode = 1  # rc=1 means nothing was ignored → passthrough
    fake_result.stdout = ""
    fake_result.stderr = ""

    call_log: list = []

    def recording_git_read(args, *, cwd, timeout=30):
        call_log.append(args)
        return fake_result

    with patch.object(pwc.git_port, "git_read", side_effect=recording_git_read):
        result = p6._filter_gitignored_paths(["a.py"], str(tmp_path))

    assert call_log, (
        "git_port.git_read was never called — phase_6._filter_gitignored_paths "
        "is still using raw bounded_run instead of routing through git_port"
    )
    assert result == ["a.py"], f"Expected ['a.py'] passthrough on rc=1, got {result}"


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — _resolve_command + _read_engine_mode behavior preserved via BOTH
#       module namespaces (no deferred import needed — symbols exist in p5/p6).
# ─────────────────────────────────────────────────────────────────────────────

def test_ac6_resolve_command_and_read_engine_mode_behavior_both_namespaces(tmp_path):
    """AC6: Table-driven checks for _resolve_command precedence and
    _read_engine_mode marker parse via BOTH phase_5_implement and
    phase_6_review namespaces.

    25e75663 (Class C): _resolve_command is now an alias for _resolve_model.
    It reads override_key → "model" → default (no longer reads "llm_command").
    """
    # _resolve_command table
    for mod, label in [(p5, "phase_5_implement"), (p6, "phase_6_review")]:
        fn = mod._resolve_command

        # override key wins
        assert fn({"k": "sonnet"}, "k") == "sonnet", f"{label}: override key not respected"

        # global "model" fallback (25e75663: was "llm_command", now "model")
        assert fn({"model": "haiku"}, "k") == "haiku", f"{label}: model fallback broken"

        # explicit default fallback
        assert fn({}, "k", "opus") == "opus", f"{label}: explicit default fallback broken"

    # _read_engine_mode table
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("<!-- engine-mode: test_only -->\n# Spec\n", encoding="utf-8")

    for mod, label in [(p5, "phase_5_implement"), (p6, "phase_6_review")]:
        result = mod._read_engine_mode(str(spec_file))
        assert result == "test_only", (
            f"{label}._read_engine_mode returned {result!r}, expected 'test_only'"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC7 — constants are the common objects (deferred import of phase_workflows_common).
# ─────────────────────────────────────────────────────────────────────────────

def test_ac7_constants_are_common_objects():
    """AC7: _CROSS_TREE_PROMPT_TEMPLATE and _ENGINE_MODE_RE in both phase_5 and
    phase_6 namespaces are the SAME object as in phase_workflows_common.
    Deferred import inside body."""
    import phase_workflows_common as pwc  # type: ignore

    for name in _CONST_NAMES:
        p5_val = getattr(p5, name, None)
        p6_val = getattr(p6, name, None)
        common_val = getattr(pwc, name, None)

        assert common_val is not None, f"phase_workflows_common.{name} missing"
        assert p5_val is common_val, (
            f"phase_5_implement.{name} is not phase_workflows_common.{name}"
        )
        assert p6_val is common_val, (
            f"phase_6_review.{name} is not phase_workflows_common.{name}"
        )
