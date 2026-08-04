"""A11B364A / GH1097 RED — phase_5 bash `.test.sh` RED semantics.

Spec: SHARED/memory/Decisions/2026-07-20_A11B364A_gh1097_bash_red_semantics_spec.md

`_verify_red_fails_mechanically` currently decides an `sh` runner group passed on
exit code alone. A HAL `.test.sh` suite whose exit guard is absent/miswired exits 0
while reporting `FAIL:` lines — the genuinely-failing RED is rejected with
E_RED_NOT_FAILING (false negative). The ship adds `_parse_sh_test_counts` +
`red_group_reports_failures` and consults them at the single `returncode == 0` site.

§1q/D1CF5FDF: this module must stay COLLECTABLE. The two not-yet-existing symbols are
resolved inside each test body via getattr(), never imported at module top level, so
every AC fails at ASSERT time, not at collect time.

81F97F3D: no module-level sys.path mutation — tests/conftest.py already exposes
engine_py root / workflows / lib on sys.path at conftest-import time.

§1l/7AD3D393: AC1, AC2, AC11, AC12 drive the REAL `_verify_red_fails_mechanically`
over a REAL on-disk `.test.sh` through a REAL `bash` subprocess. The UUT and the
helpers under test are never mocked; only ambient seams (git_cwd via org_config,
the disk_truth telemetry re-run, _emit_safe capture) are neutralised.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

from bytedigger_engine.workflows import phase_5_implement as _p5  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402

_PARSER = "_parse_sh_test_counts"
_HELPER = "red_group_reports_failures"
_OVERRIDE_EVENT = "red_sh_failure_evidence_override"


# ─── resolution of the not-yet-existing symbols (deferred into test bodies) ────


def _get_parser():
    fn = getattr(_p5, _PARSER, None)
    assert fn is not None, (
        f"phase_5_implement.{_PARSER} does not exist yet (spec §2.1) — "
        "GREEN must add the single canonical bash-output parser."
    )
    return fn


def _get_helper():
    fn = getattr(_p5, _HELPER, None)
    assert fn is not None, (
        f"phase_5_implement.{_HELPER} does not exist yet (spec §2.2) — "
        "GREEN must add the frozen (output, kind) -> bool helper."
    )
    return fn


# ─── ambient-seam fixtures ────────────────────────────────────────────────────


def _make_ctx(git_cwd: str) -> WorkflowContext:
    """git_cwd seam: resolve_git_cwd takes org_config['git_cwd'] first."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_paths: list[str]) -> StepResult:
    """No spec_path -> dirty-tree allowlist is None -> guard emits and falls through
    (deterministic, no git repo needed). cycle=1 -> GH1034 restore returns None.
    No red_commit_sha -> GH483 green-complete-resume returns []."""
    return StepResult(
        status="ok",
        data={"red_test_paths": list(red_paths), "cycle": 1},
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _patch_ambient(monkeypatch) -> list[dict]:
    """Capture _emit_safe and neutralise the disk_truth telemetry re-run.

    NEITHER _verify_red_fails_mechanically NOR the helpers under test are patched:
    bounded_run stays real, so `bash <fixture>.test.sh` really executes.
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    monkeypatch.setattr(
        _p5,
        "run_test_command",
        lambda *a, **kw: SimpleNamespace(exit_code=0, n_passed=0, n_failed=0),
    )
    return captured


def _write_sh(tmp_path: Path, name: str, body: str) -> str:
    """Write a chmod-independent bash fixture (invoked as `bash <name>`).

    §1m: no detection tokens in comments — the fixture carries none.
    §1j: caller pins git_cwd via os.path.realpath so the subprocess cwd matches.
    """
    (tmp_path / name).write_text(body)
    return name


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_sh_group_reporting_failure_at_exit_zero_is_a_real_red(tmp_path, monkeypatch):
    """AC1: real .test.sh printing 'FAIL: AC1 boom' and exiting 0, run through the real
    _verify_red_fails_mechanically via a real subprocess -> status ok, NOT E_RED_NOT_FAILING."""
    captured = _patch_ambient(monkeypatch)
    git_cwd = os.path.realpath(str(tmp_path))
    name = _write_sh(tmp_path, "ac1.test.sh", 'echo "FAIL: AC1 boom"\nexit 0\n')

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev([name]))

    assert result.error_code != "E_RED_NOT_FAILING", (
        "AC1: an sh group that printed 'FAIL: AC1 boom' must NOT be rejected as a fake "
        f"RED. got error_code={result.error_code!r}, error={result.error!r}, "
        f"events={[e['type'] for e in captured]}"
    )
    assert result.status == "ok", (
        f"AC1: expected status='ok', got {result.status!r} "
        f"(error_code={result.error_code!r})"
    )


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_ac2_regression_floor_all_passing_sh_group_still_fake_red(tmp_path, monkeypatch):
    """AC2 regression floor: real .test.sh printing only PASS: lines + a 0-failed summary
    and exiting 0 -> E_RED_NOT_FAILING still fires (the override must not over-fire)."""
    _patch_ambient(monkeypatch)
    git_cwd = os.path.realpath(str(tmp_path))
    name = _write_sh(
        tmp_path,
        "ac2.test.sh",
        'echo "PASS: alpha"\n'
        'echo "PASS: beta"\n'
        'echo "PASS: gamma"\n'
        'echo "RESULT: 3 passed, 0 failed"\n'
        "exit 0\n",
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev([name]))

    assert result.error_code == "E_RED_NOT_FAILING", (
        "AC2: an all-passing sh group must still be rejected as a fake RED. "
        f"got status={result.status!r}, error_code={result.error_code!r}"
    )


# ─── AC3 ──────────────────────────────────────────────────────────────────────


def test_ac3_summary_line_is_never_counted_as_a_per_test_pass(tmp_path):
    """AC3: _parse_sh_test_counts('PASS:12 FAIL:3') == (12, 3) — the summary line is
    consumed as a SUMMARY, never miscounted as one per-test PASS with FAIL:3 missed."""
    parse = _get_parser()
    assert parse("PASS:12 FAIL:3") == (12, 3), (
        f"AC3: expected (12, 3), got {parse('PASS:12 FAIL:3')!r}"
    )


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_ac4_result_summary_form_is_parsed(tmp_path):
    """AC4: _parse_sh_test_counts('RESULT: 31 passed, 0 failed') == (31, 0)."""
    parse = _get_parser()
    assert parse("RESULT: 31 passed, 0 failed") == (31, 0), (
        f"AC4: expected (31, 0), got {parse('RESULT: 31 passed, 0 failed')!r}"
    )


# ─── AC5 ──────────────────────────────────────────────────────────────────────


def test_ac5_replayed_fail_lines_are_deduplicated(tmp_path):
    """AC5: 2 distinct PASS: lines + the same 'FAIL: AC1 boom' line 3x, no summary
    -> (2, 1) — FAIL_DETAILS replay must not double-count failures."""
    parse = _get_parser()
    out = (
        "PASS: alpha\n"
        "PASS: beta\n"
        "FAIL: AC1 boom\n"
        "FAIL: AC1 boom\n"
        "FAIL: AC1 boom\n"
    )
    assert parse(out) == (2, 1), f"AC5: expected (2, 1), got {parse(out)!r}"


# ─── AC6 ──────────────────────────────────────────────────────────────────────


def test_ac6_mixed_per_test_lines_without_summary(tmp_path):
    """AC6: 2 PASS: lines + 2 DISTINCT FAIL: lines, no summary -> (2, 2)."""
    parse = _get_parser()
    out = "PASS: alpha\nPASS: beta\nFAIL: gamma\nFAIL: delta\n"
    assert parse(out) == (2, 2), f"AC6: expected (2, 2), got {parse(out)!r}"


# ─── AC7 ──────────────────────────────────────────────────────────────────────


def test_ac7_helper_signature_is_frozen_output_then_kind():
    """AC7: inspect.signature(red_group_reports_failures) has exactly the two required
    positional params ('output', 'kind') in that order (spec §2.2 freeze, #1095 MAJOR-E)."""
    helper = _get_helper()
    params = list(inspect.signature(helper).parameters.values())

    assert [p.name for p in params] == ["output", "kind"], (
        f"AC7: expected exactly params ('output', 'kind') in order, "
        f"got {[p.name for p in params]!r}"
    )
    for p in params:
        assert p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ), f"AC7: param {p.name!r} must be positional, got kind={p.kind!r}"
        assert p.default is inspect.Parameter.empty, (
            f"AC7: param {p.name!r} must be REQUIRED (no default), got {p.default!r}"
        )


# ─── AC8 ──────────────────────────────────────────────────────────────────────


def test_ac8_non_sh_kinds_keep_exit_code_only_semantics():
    """AC8: red_group_reports_failures('FAIL: x', 'py') is False — py/ts/swift/rust are
    deliberately out of scope (that is the #1095 crash half)."""
    helper = _get_helper()
    assert helper("FAIL: x", "py") is False, (
        f"AC8: expected False for kind='py', got {helper('FAIL: x', 'py')!r}"
    )


# ─── AC9 ──────────────────────────────────────────────────────────────────────


def test_ac9_sh_kind_reports_failures_only_when_failures_present():
    """AC9: red_group_reports_failures('FAIL: x', 'sh') is True and
    red_group_reports_failures('PASS: x', 'sh') is False."""
    helper = _get_helper()
    assert helper("FAIL: x", "sh") is True, (
        f"AC9: expected True for a failing sh output, got {helper('FAIL: x', 'sh')!r}"
    )
    assert helper("PASS: x", "sh") is False, (
        f"AC9: expected False for a passing sh output, got {helper('PASS: x', 'sh')!r}"
    )


# ─── AC10 ─────────────────────────────────────────────────────────────────────


def test_ac10_helper_reads_the_single_canonical_parser(monkeypatch):
    """AC10 (§1g): monkeypatching _parse_sh_test_counts to return (0, 7) makes
    red_group_reports_failures(<all-PASS text>, 'sh') return True — the helper reads the
    single canonical parser rather than carrying its own regex."""
    helper = _get_helper()
    _get_parser()  # must exist before it can be swapped
    monkeypatch.setattr(_p5, _PARSER, lambda output: (0, 7))

    all_pass = "PASS: alpha\nPASS: beta\nRESULT: 2 passed, 0 failed\n"
    assert helper(all_pass, "sh") is True, (
        "AC10: helper did not route through the patched _parse_sh_test_counts — "
        "it must not re-derive counts with its own regex (§1g single source)."
    )


# ─── AC11 ─────────────────────────────────────────────────────────────────────


def test_ac11_override_emits_failure_evidence_event(tmp_path, monkeypatch):
    """AC11: when the override fires (AC1 shape, real subprocess), a
    red_sh_failure_evidence_override event is emitted with group=='sh' and n_failed > 0."""
    captured = _patch_ambient(monkeypatch)
    git_cwd = os.path.realpath(str(tmp_path))
    name = _write_sh(tmp_path, "ac11.test.sh", 'echo "FAIL: AC1 boom"\nexit 0\n')

    _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev([name]))

    events = [e for e in captured if e["type"] == _OVERRIDE_EVENT]
    assert len(events) == 1, (
        f"AC11: expected exactly 1 {_OVERRIDE_EVENT!r} event, got {len(events)}. "
        f"All events: {[e['type'] for e in captured]}"
    )
    payload = events[0]["payload"]
    assert payload.get("group") == "sh", (
        f"AC11: expected group=='sh', got payload={payload!r}"
    )
    assert isinstance(payload.get("n_failed"), int) and payload["n_failed"] > 0, (
        f"AC11: expected n_failed > 0 (int), got payload={payload!r}"
    )


# ─── AC12 ─────────────────────────────────────────────────────────────────────


def test_ac12_nonzero_exit_sh_group_does_not_consult_the_helper(tmp_path, monkeypatch):
    """AC12 (reverse direction): an sh group exiting 1 -> status ok AND no
    red_sh_failure_evidence_override emit (the returncode != 0 path is untouched)."""
    captured = _patch_ambient(monkeypatch)
    git_cwd = os.path.realpath(str(tmp_path))
    name = _write_sh(tmp_path, "ac12.test.sh", 'echo "FAIL: AC12 boom"\nexit 1\n')

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev([name]))

    assert result.status == "ok", (
        f"AC12: expected status='ok' for a group exiting 1, got {result.status!r} "
        f"(error_code={result.error_code!r})"
    )
    events = [e for e in captured if e["type"] == _OVERRIDE_EVENT]
    assert not events, (
        f"AC12: {_OVERRIDE_EVENT!r} must NOT be emitted when returncode != 0 "
        f"(helper not consulted), got {len(events)} event(s)"
    )


# ─── AC13 (AMENDMENT-1) ───────────────────────────────────────────────────────


def test_ac13_proc_double_without_stderr_attribute_does_not_raise(tmp_path, monkeypatch):
    """AC13 (AMENDMENT-1, §1a sibling-compat regression floor): the §2.3 wiring must read
    the proc stderr defensively.

    Three sibling phase_5 test files stub `bounded_run` with a
    SimpleNamespace(returncode=..., stdout=...) carrying NO stderr attribute
    (test_phase5_test_only_verify_gates.py:149, test_green_complete_resume_gh483.py,
    test_gh1034_red_cycle2_degeneracy.py). A bare `proc.stderr` read raises
    AttributeError on that shape and regressed 13 passing tests; `getattr(proc,
    "stderr", "")` is behaviour-preserving in production because a real
    subprocess.run(capture_output=True) result always carries stderr.

    Same ambient-patch idiom as the sibling: bounded_run is swapped for the stderr-less
    double; the UUT (_verify_red_fails_mechanically) and the helpers under test stay real.
    """
    _patch_ambient(monkeypatch)
    git_cwd = os.path.realpath(str(tmp_path))
    name = _write_sh(tmp_path, "ac13.test.sh", 'echo "PASS: alpha"\nexit 0\n')

    # Deliberately WITHOUT stderr — mirrors the sibling double shape exactly.
    stderrless_proc = SimpleNamespace(
        returncode=0,
        stdout='PASS: alpha\nPASS: beta\nRESULT: 2 passed, 0 failed\n',
    )
    assert not hasattr(stderrless_proc, "stderr"), (
        "AC13 fixture invariant: the double must NOT carry a stderr attribute"
    )
    monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: stderrless_proc)

    try:
        result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev([name]))
    except AttributeError as e:
        raise AssertionError(
            "AC13: the §2.3 wiring read proc.stderr unguarded and raised on a proc double "
            f"with no stderr attribute ({e!r}). Spec AMENDMENT-1 requires "
            'getattr(proc, "stderr", "") — this is the shape 3 sibling test files use.'
        ) from e

    assert result.error_code == "E_RED_NOT_FAILING", (
        "AC13: an all-passing sh group must still be rejected as a fake RED after the "
        f"getattr fix. got status={result.status!r}, error_code={result.error_code!r}"
    )


# ─── AC14 (re-gate MAJOR-3) ───────────────────────────────────────────────────


def test_ac14_stderrless_proc_double_survives_the_override_branch(tmp_path, monkeypatch):
    """AC14 (re-gate MAJOR-3, §1g): the emit re-derive inside the override branch must
    read the SAME defensively-derived input surface as the decision call.

    AC13 pins only site 1 (the `red_group_reports_failures(...)` call): its fixture is
    all-passing by construction, so `_sh_reports_fail` is False and execution provably
    never enters the `if _sh_reports_fail:` body. A second, bare `proc.stderr` in the
    `_parse_sh_test_counts(...)` re-derive therefore stays invisible to AC13 — and
    because site 1 used to crash first, fixing site 1 newly EXPOSES site 2.

    This test uses the same stderr-less sibling double shape but with stdout carrying a
    per-test FAIL line, so `red_group_reports_failures` returns True and the override
    branch is entered, reaching the re-derive. Passes only once GREEN hoists the single
    `_sh_output` local (spec §2.3 AMENDMENT-1) consumed at BOTH sites.
    """
    captured = _patch_ambient(monkeypatch)
    git_cwd = os.path.realpath(str(tmp_path))
    name = _write_sh(tmp_path, "ac14.test.sh", 'echo "FAIL: AC14 boom"\nexit 0\n')

    # Deliberately WITHOUT stderr — identical shape to AC13, failing stdout.
    stderrless_proc = SimpleNamespace(
        returncode=0,
        stdout="PASS: alpha\nFAIL: AC14 boom\n",
    )
    assert not hasattr(stderrless_proc, "stderr"), (
        "AC14 fixture invariant: the double must NOT carry a stderr attribute"
    )
    monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: stderrless_proc)

    try:
        result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev([name]))
    except AttributeError as e:
        raise AssertionError(
            "AC14: the §2.3 override branch read proc.stderr unguarded and raised on a "
            f"proc double with no stderr attribute ({e!r}). Spec §2.3 requires ONE "
            'hoisted local `_sh_output = (proc.stdout or "") + (getattr(proc, "stderr", '
            '"") or "")` consumed by BOTH the decision call and the emit re-derive.'
        ) from e

    events = [e for e in captured if e["type"] == _OVERRIDE_EVENT]
    assert len(events) == 1, (
        f"AC14: expected exactly 1 {_OVERRIDE_EVENT!r} event once the override branch is "
        f"reached, got {len(events)}. All events: {[e['type'] for e in captured]}"
    )
    payload = events[0]["payload"]
    assert payload.get("group") == "sh", (
        f"AC14: expected group=='sh', got payload={payload!r}"
    )
    assert isinstance(payload.get("n_failed"), int) and payload["n_failed"] > 0, (
        f"AC14: expected n_failed > 0 (int) from the re-derive, got payload={payload!r}"
    )
    assert result.error_code != "E_RED_NOT_FAILING", (
        "AC14: an sh group reporting 'FAIL: AC14 boom' must NOT be rejected as a fake "
        f"RED. got status={result.status!r}, error_code={result.error_code!r}"
    )
