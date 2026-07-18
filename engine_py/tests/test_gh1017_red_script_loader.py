"""RED tests for GH1017 — RED-writer script-UUT loader rule + runpy lint.

Frozen spec: SHARED/memory/Decisions/2026-07-18_GH1017_red_script_loader_spec.md

Not-yet-existing symbols (`_spec_wants_script_uut`, `_RE_1Q_EXEC_IMPORT`,
`RED_SCRIPT_LOADER_RULE`) are accessed via `getattr(module, "name", None)`
INSIDE test bodies (D1CF5FDF/§1q) so this file COLLECTS cleanly and fails at
assert time, never at collection time.

§1i: no singleton/time-dependent resource under test — all fixtures (spec
files, RED test files) are pre-staged deterministically before invoking the
UUT, per workflows.md §1i.
"""
from __future__ import annotations

import re as _re_module
from pathlib import Path
from unittest.mock import patch

from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers (mirror test_gh501_red_prompt_rules.py / test_gh595) ──


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh1017",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_spec(scratchpad: Path, spec_text: str) -> None:
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "build-spec.md").write_text(spec_text, encoding="utf-8")


def _build_red(tmp_path: Path, spec_text: str | None, **org_extra):
    from phase_5_implement import _build_red_prompt  # noqa: PLC0415

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


_RUNPY_VIOLATION = """\
import runpy

result = runpy.run_path("/tmp/foo-bar.py")


def test_something():
    assert result is not None
"""

_RUNPY_PRAGMA = """\
import runpy

result = runpy.run_path("/tmp/foo-bar.py")  # 1q: allow


def test_pragma():
    assert result is not None
"""

_SPEC_FROM_FILE_LOCATION_VIOLATION = """\
import importlib.util

spec = importlib.util.spec_from_file_location("mod", "/tmp/foo.py")


def test_something():
    assert spec is not None
"""


# ─── AC1/AC2: _spec_wants_script_uut helper ────────────────────────────────


def test_ac1_spec_wants_script_uut_true_on_hyphenated_py_token():
    import phase_5_implement as p5  # noqa: PLC0415

    fn = getattr(p5, "_spec_wants_script_uut", None)
    assert callable(fn), "_spec_wants_script_uut must exist as a module-level callable"
    result = fn("UUT: SYSTEM/cli/build/toolscan-baseline-delta.py")
    assert result is True


def test_ac2_spec_wants_script_uut_false_on_non_hyphenated_tokens():
    import phase_5_implement as p5  # noqa: PLC0415

    fn = getattr(p5, "_spec_wants_script_uut", None)
    assert callable(fn), "_spec_wants_script_uut must exist as a module-level callable"
    result = fn("UUT: engine_py/spec_cite.py run_phase.py")
    assert result is False


# ─── AC3/AC4: prompt scaffold contains/omits the RED_SCRIPT_LOADER_RULE ────


def test_ac3_prompt_contains_script_loader_rule_when_spec_has_hyphenated_uut(tmp_path):
    result = _build_red(
        tmp_path,
        "UUT: SYSTEM/cli/build/toolscan-baseline-delta.py — fix the baseline delta calc",
    )
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "RULES script-UUT loader (GH1017" in prompt, (
        "spec has a hyphenated .py UUT token — prompt must contain the loader rule block"
    )
    assert "mod.__dict__" in prompt
    assert "sys.modules" in prompt
    assert "runpy.run_path" in prompt


def test_ac4_prompt_omits_script_loader_rule_when_no_hyphenated_uut(tmp_path):
    result = _build_red(tmp_path, "Add a new Python helper function foo()")
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "RULES script-UUT loader (GH1017" not in prompt, (
        "spec has no hyphenated .py UUT token — loader rule block must NOT appear"
    )


# ─── AC5/AC6/AC7/AC8: _verify_red_lint_rules extended for runpy.run_path ──
# Under default config (HAL_RED_LINT_PREFLIGHT_BATCH ON) this routes through
# the batch collector (:2443), not the legacy per-file site (:2578).


def test_ac5_verify_red_lint_rules_rejects_runpy_run_path(tmp_path, monkeypatch):
    from phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    monkeypatch.setenv("HAL_RED_PREFLIGHT_DELTA_RETRY", "0")
    relpath = _write_test_file(tmp_path, "tests/test_1017_runpy.py", _RUNPY_VIOLATION)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_1Q_EXEC_IMPORT", (
        f"expected error_code='E_RED_1Q_EXEC_IMPORT' for runpy.run_path hit, "
        f"got {result.error_code!r} (status={result.status!r})"
    )
    assert result.status == "error"
    assert result.recoverable is False


def test_ac6_pragma_1q_allow_suppresses_runpy_reject(tmp_path):
    from phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    relpath = _write_test_file(tmp_path, "tests/test_1017_runpy_pragma.py", _RUNPY_PRAGMA)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code != "E_RED_1Q_EXEC_IMPORT", (
        f"pragma '# 1q: allow' must suppress the runpy reject; got error_code={result.error_code!r}"
    )


def test_ac7_env_gate_disabled_bypasses_runpy_reject(tmp_path, monkeypatch):
    from phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    monkeypatch.setenv("HAL_RED_1Q_GATE", "0")
    relpath = _write_test_file(tmp_path, "tests/test_1017_runpy_gated.py", _RUNPY_VIOLATION)
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code != "E_RED_1Q_EXEC_IMPORT", (
        "kill-switch HAL_RED_1Q_GATE=0 must bypass the runpy.run_path lint too"
    )


def test_ac8_regression_spec_from_file_location_still_rejected(tmp_path, monkeypatch):
    """Pure regression pin — passes today and must still pass post-GREEN."""
    from phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

    monkeypatch.setenv("HAL_RED_PREFLIGHT_DELTA_RETRY", "0")
    relpath = _write_test_file(
        tmp_path, "tests/test_1017_spec_from_file.py", _SPEC_FROM_FILE_LOCATION_VIOLATION
    )
    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_1Q_EXEC_IMPORT"
    assert result.status == "error"
    assert result.recoverable is False


# ─── AC9: single-source-of-truth — exactly ONE module-level re.compile ────


def test_ac9_single_source_exactly_one_module_level_recompile_for_1q():
    import phase_5_implement as p5  # noqa: PLC0415

    source_path = Path(p5.__file__)
    source = source_path.read_text(encoding="utf-8")

    # Find every re.compile(...) call whose literal pattern argument mentions
    # spec_from_file_location, anywhere in the module source.
    matches = _re_module.findall(
        r're\.compile\(\s*r?["\'][^"\']*spec_from_file_location[^"\']*["\']',
        source,
    )
    assert len(matches) == 1, (
        f"expected exactly ONE re.compile(...) pattern mentioning "
        f"spec_from_file_location (single-source §1g), found {len(matches)}: {matches!r}"
    )

    const = getattr(p5, "_RE_1Q_EXEC_IMPORT", None)
    assert const is not None, "_RE_1Q_EXEC_IMPORT module-level constant must exist"
    assert const.search("runpy.run_path('/tmp/x.py')"), (
        "_RE_1Q_EXEC_IMPORT must also match runpy.run_path per §2.2"
    )


# ─── AC10: preflight-batch (:2443 site) also flags runpy.run_path ─────────


def test_ac10_preflight_batch_flags_runpy_run_path(tmp_path, monkeypatch):
    import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setenv("HAL_RED_PREFLIGHT_DELTA_RETRY", "0")
    collect_fn = getattr(p5, "_collect_red_lint_findings", None)
    assert callable(collect_fn), (
        "_collect_red_lint_findings must exist as a module-level callable "
        "(shared batch-collection helper, sibling of the per-file verify path)"
    )

    relpath = _write_test_file(tmp_path, "tests/test_1017_batch_runpy.py", _RUNPY_VIOLATION)
    ctx = _make_ctx(tmp_path)
    cfg = None
    abs_path = str(tmp_path / relpath)
    try:
        findings = collect_fn([abs_path], str(tmp_path), ctx, cfg)
    except TypeError:
        # tolerate a slightly different signature; fall back to the full
        # verify path which shares the batch dispatcher per §2.2
        from phase_5_implement import _verify_red_lint_rules  # noqa: PLC0415

        prev = _make_prev([relpath])
        result = _verify_red_lint_rules(ctx, prev)
        error_codes = result.data.get("preflight_error_codes", []) if result.data else []
        assert "E_RED_1Q_EXEC_IMPORT" in error_codes or result.error_code == "E_RED_1Q_EXEC_IMPORT", (
            f"preflight-batch path must flag runpy.run_path with E_RED_1Q_EXEC_IMPORT; "
            f"got error_codes={error_codes!r} result.error_code={result.error_code!r}"
        )
        return

    error_codes = [f.get("error_code") for f in findings]
    assert "E_RED_1Q_EXEC_IMPORT" in error_codes, (
        f"preflight-batch collector must flag runpy.run_path with E_RED_1Q_EXEC_IMPORT; "
        f"got {error_codes!r}"
    )
