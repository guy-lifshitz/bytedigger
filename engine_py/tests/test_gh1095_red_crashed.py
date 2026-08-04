"""GH1095 RED tests — `E_RED_CRASHED`: a crashing RED run must not pass
`_verify_red_fails_mechanically`.

Covers AC1-AC16 from
SHARED/memory/Decisions/2026-07-20_GH1095_red_crashed_gate_spec.md
(amended spec: GATE-AMEND-1 narrows C2 to `returncode != 0` and adds C2b /
`_RED_ZERO_EXECUTED_MARKERS`; GATE-AMEND-2 freezes the call-site accessor as
attribute-safe `getattr(proc, "stderr", "") or ""`; GATE-AMEND-3 closes the
canonical pytest rc=5 / `no tests ran` hole).

§0 DESCOPE (Guy, 07-20): the bash `.test.sh` layer is out of this lot
entirely. This file therefore covers the crash part only — swift / pytest /
bun → E_RED_CRASHED. `parse_red_shape` has three parsers (pytest, bun,
xctest); `classify_red_run_shape(returncode, output)` takes no `kind`;
`parse_red_shape_with_source` and `red_group_reports_failures` do not exist;
the `passing_groups` condition at L2393 is untouched by this lot. Rule C3 is
its plain form: tuple returned, executed > 0, failures == 0, returncode != 0.

§1q / 81F97F3D: NO module-level `sys.path` manipulation and no
`spec_from_file_location`. The engine_py root + `workflows/` are already on
`sys.path` via the conftest-import-time singleton
(`tests/conftest.py`, lines 23-28), so a plain `import phase_5_implement`
resolves.

D1CF5FDF collectability: `classify_red_run_shape` and `parse_red_shape` do
NOT exist yet on `phase_5_implement`. They are fetched via
`getattr(_p5, name, None)` INSIDE each test body (never imported at module
top), so this file COLLECTS cleanly under `pytest --co` and fails at assert
time, never at collection time.

§1l: the step-level tests invoke the REAL production step
`_verify_red_fails_mechanically(ctx, prev)` and assert on the returned
`StepResult`. `classify_red_run_shape` is never mocked — only the
test-execution collaborators (`bounded_run`, `run_test_command`,
`_infer_test_command_for_paths`, `_resolve_git_cwd`) and the two
independent GH483/GH1034 resume/restore branches are stubbed so the run
shape is deterministic.

§1i: no singleton/time races — `gate_attempts` is always supplied
explicitly inside `prev.data`; nothing is inherited from ambient state and
no wall-clock/timing fixture is used.

Expected pre-GREEN outcome:
  FAIL (gate + helpers not built): AC1, AC2 (x3), AC3, AC6, AC6b, AC8,
    AC10 (crash twin), AC11 (x2), AC12 (stderr-only), AC13 (x2),
    AC14 (x4: policy x3 + multi-group), AC15 (zero-match), AC16 (concat
    order), and the four direct classify/parse unit tests.
  PASS (regression floors that must stay green after GREEN): AC4 (x3),
    AC5, AC7 (x2), AC10 (GH1034 `0 failed` twin), AC12 (stderr-less
    runner object — blind today, load-bearing after GREEN).

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement as _p5  # noqa: E402


ENGINE_ROOT = Path(__file__).parent.parent

# ─── fixture output shapes (spec §2.1 runner table) ────────────────────────

XCTEST_ZERO_EXECUTED = "Test Suite 'All tests' started\nExecuted 0 tests, with 0 failures (0 unexpected) in 0.000 seconds\n"
XCTEST_CRASH_SIGNAL = (
    "Test Suite 'All tests' started\n"
    "Abort trap: 6\n"
    "Executed 0 tests, with 0 failures (0 unexpected) in 0.000 seconds\n"
)
XCTEST_BUILD_ERROR = (
    "error: Build input file not found: /repo/Sources/Thing.swift\n"
    "** TEST FAILED **\n"
)
XCTEST_GENUINE_RED = (
    "Executed 15 tests, with 10 failures (0 unexpected) in 1.234 seconds\n"
    "** TEST FAILED **\n"
)
PYTEST_GENUINE_RED = "=========== 2 failed, 1 passed in 0.31s ===========\n"
PYTEST_ALL_PASSING = "=========== 3 passed in 0.12s ===========\n"
PYTEST_INTERNAL_CRASH = (
    "INTERNALERROR> Traceback (most recent call last):\n"
    "INTERNALERROR>   File \"_pytest/main.py\", line 1, in wrap_session\n"
    "=========== no tests ran in 0.02s ===========\n"
)
BUN_GENUINE_RED = "\n 1 pass\n 3 fail\nRan 4 tests across 1 files.\n"
BUN_ZERO_EXECUTED = "\n 0 pass\n 0 fail\nRan 0 tests across 0 files.\n"


# ─── generic helpers ───────────────────────────────────────────────────────


def _get_helper(name: str):
    """Deferred symbol fetch (D1CF5FDF) — resolved inside the test body."""
    fn = getattr(_p5, name, None)
    assert fn is not None, (
        f"GH1095 helper {name!r} not implemented yet on phase_5_implement "
        "(spec §2.1)"
    )
    return fn


def _make_ctx(git_cwd: str, complexity: str = "SIMPLE") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd, "complexity": complexity},
        question="gh1095 question",
        session_id="gh1095-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(extra: dict | None = None) -> StepResult:
    """§1i: gate_attempts supplied explicitly by every caller, never ambient."""
    data: dict = {
        "red_test_paths": ["Tests/GH1095Tests.swift"],
        "cycle": 1,
        "spec_path": None,
        "gate_attempts": {},
    }
    if extra:
        data.update(extra)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="commit_red_tests")


def _patch_emit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


def _install_runner(
    monkeypatch,
    git_cwd: str,
    *,
    kind: str,
    returncode: int,
    stdout: str,
    stderr: str = "",
    restore_result: StepResult | None = None,
) -> None:
    """Stub only the test-execution collaborators of the UUT (§1l).

    `_verify_red_fails_mechanically` itself and `classify_red_run_shape` are
    NEVER stubbed — the assertions ride on the real production return path.
    """
    argv_by_kind = {
        "py": ["python3", "-m", "pytest"],
        "ts": ["bun", "test"],
        "swift": ["swift", "test"],
    }
    argv = argv_by_kind[kind]

    monkeypatch.setattr(
        _p5,
        "bounded_run",
        lambda *a, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr),
    )
    monkeypatch.setattr(
        _p5,
        "run_test_command",
        lambda *a, **kw: SimpleNamespace(exit_code=returncode, n_passed=0, n_failed=0),
    )
    monkeypatch.setattr(
        _p5,
        "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [{"kind": kind, "argv": [*argv, *paths], "paths": list(paths)}]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev=None: git_cwd)
    # Independent GH483 / GH1034 branches (spec §3 "defer") pinned inert so the
    # all-passing path lands on its own legacy code, not on a sibling feature.
    monkeypatch.setattr(_p5, "_detect_green_complete_resume", lambda *a, **kw: [])
    monkeypatch.setattr(_p5, "_attempt_red_cycle_restore", lambda *a, **kw: restore_result)


# ═══ AC1 — signal-terminated run → E_RED_CRASHED ═══════════════════════════


def test_ac1_signal_terminated_run_returns_e_red_crashed(monkeypatch, tmp_path):
    """AC1 (spec §4, SHARED/memory/Decisions/2026-07-20_GH1095_red_crashed_gate_spec.md):
    a run killed by a signal (exit 133 > 128) with zero executed tests must
    return StepResult(status='error', error_code='E_RED_CRASHED'), NOT a
    valid RED. §1y: also asserts the `red_crashed` telemetry event fires.
    """
    git_cwd = str(tmp_path)
    captured = _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=133, stdout=XCTEST_CRASH_SIGNAL
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_CRASHED", (
        f"AC1: expected E_RED_CRASHED for signal-terminated RED, got "
        f"{result.error_code!r} (status={result.status!r})"
    )
    assert result.status == "error", f"AC1: expected status 'error', got {result.status!r}"
    events = [e for e in captured if e["type"] == "red_crashed"]
    assert events, (
        f"AC1/§2.4: expected a `red_crashed` telemetry event, got "
        f"{[e['type'] for e in captured]}"
    )
    assert events[0]["payload"].get("reason") == "signal", (
        f"AC1/§2.4: expected reason 'signal', got {events[0]['payload']!r}"
    )


# ═══ AC2 — zero executed tests → E_RED_CRASHED (all three runners, AC9) ════


def test_ac2_zero_executed_tests_returns_e_red_crashed_xctest(monkeypatch, tmp_path):
    """AC2 + AC9/swift (spec §4): exit 1 with `Executed 0 tests, with 0
    failures` — nothing ran, so the run cannot be a valid RED.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=1, stdout=XCTEST_ZERO_EXECUTED
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_CRASHED", (
        f"AC2: expected E_RED_CRASHED for 0-executed xctest run, got "
        f"{result.error_code!r} (status={result.status!r})"
    )


def test_ac2_zero_executed_tests_returns_e_red_crashed_bun(monkeypatch, tmp_path):
    """AC2 + AC9/bun (spec §4): bun form `0 pass / 0 fail` → executed == 0 →
    E_RED_CRASHED (spec §2.1 rule C2).
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="ts", returncode=1, stdout=BUN_ZERO_EXECUTED
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"red_test_paths": ["tests/gh1095.test.ts"]})
    )

    assert result.error_code == "E_RED_CRASHED", (
        f"AC2: expected E_RED_CRASHED for 0-executed bun run, got "
        f"{result.error_code!r} (status={result.status!r})"
    )


def test_ac2_zero_executed_tests_returns_e_red_crashed_pytest(monkeypatch, tmp_path):
    """AC2 + AC9/pytest (spec §4): pytest `no tests ran` after an
    INTERNALERROR — no summary line parses and exit != 0, with both an
    `_RED_EXEC_ERROR_MARKERS` marker (`INTERNALERROR`) and a
    `_RED_ZERO_EXECUTED_MARKERS` marker (`no tests ran`) present. Post
    GATE-AMEND-3 this lands on rule C2b; pre-amendment it was the C4 path.
    Only the error code is asserted (not a slug), so the test is stable
    across that reclassification. The canonical rc=5 zero-collect form is
    pinned separately by AC11.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="py", returncode=1, stdout=PYTEST_INTERNAL_CRASH
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"red_test_paths": ["tests/test_gh1095.py"]})
    )

    assert result.error_code == "E_RED_CRASHED", (
        f"AC2: expected E_RED_CRASHED for pytest 'no tests ran' crash, got "
        f"{result.error_code!r} (status={result.status!r})"
    )


# ═══ AC3 — test-binary error without any failure lines → E_RED_CRASHED ═════


def test_ac3_test_binary_error_without_failure_lines_returns_e_red_crashed(
    monkeypatch, tmp_path
):
    """AC3 + AC9/swift (spec §4): exit 1, `error: Build input file not found`
    plus `** TEST FAILED **`, and NO run summary at all → spec §2.1 rule C4 →
    E_RED_CRASHED. Today this silently counts as a valid RED.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=1, stdout=XCTEST_BUILD_ERROR
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_CRASHED", (
        f"AC3: expected E_RED_CRASHED for build-error-without-failures run, "
        f"got {result.error_code!r} (status={result.status!r})"
    )
    assert result.status == "error", f"AC3: expected status 'error', got {result.status!r}"


# ═══ AC4 — genuine RED still passes (regression floor, all three runners) ══


@pytest.mark.parametrize(
    "kind,stdout,paths",
    [
        ("py", PYTEST_GENUINE_RED, ["tests/test_gh1095.py"]),
        ("ts", BUN_GENUINE_RED, ["tests/gh1095.test.ts"]),
        ("swift", XCTEST_GENUINE_RED, ["Tests/GH1095Tests.swift"]),
    ],
    ids=["pytest", "bun", "xctest"],
)
def test_ac4_genuine_red_with_executed_tests_and_failures_stays_ok(
    monkeypatch, tmp_path, kind, stdout, paths
):
    """AC4 + AC9 (spec §4): >=1 executed test AND >=1 assertion failure is a
    legitimate RED shape (spec §2.1 rule C5) — status 'ok', error_code None.
    Regression floor: must be green both before and after GREEN.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(monkeypatch, git_cwd, kind=kind, returncode=1, stdout=stdout)

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"red_test_paths": paths})
    )

    assert result.status == "ok", (
        f"AC4[{kind}]: genuine RED must stay ok, got {result.status!r} "
        f"error={result.error!r} code={result.error_code!r}"
    )
    assert result.error_code is None, (
        f"AC4[{kind}]: expected error_code None, got {result.error_code!r}"
    )


# ═══ AC5 — all tests passed → still E_RED_NOT_FAILING (untouched) ══════════


def test_ac5_all_passing_run_still_returns_e_red_not_failing(monkeypatch, tmp_path):
    """AC5 (spec §4): exit 0 with `3 passed` is a legitimate *shape*
    (spec §2.1 rule C5) — the pre-existing fake-RED path must keep owning it:
    E_RED_NOT_FAILING with recoverable False, NOT E_RED_CRASHED.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="py", returncode=0, stdout=PYTEST_ALL_PASSING
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"red_test_paths": ["tests/test_gh1095.py"]})
    )

    assert result.error_code == "E_RED_NOT_FAILING", (
        f"AC5: all-passing RED must keep E_RED_NOT_FAILING, got "
        f"{result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC5: expected recoverable False, got {result.recoverable!r}"
    )


# ═══ AC6 — recoverable=True + crash cause relayed in error tail ════════════


def test_ac6_e_red_crashed_is_recoverable_and_relays_output_tail(monkeypatch, tmp_path):
    """AC6 (spec §4, §2.3): the AC1 scenario must come back recoverable with
    `retry_from_step == 0` (regenerate RED) and the crash marker from stdout
    present in `result.error` — that relay is the feedback that is missing
    today. §1i: `gate_attempts` passed explicitly as {} (fresh, attempts 0).
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=133, stdout=XCTEST_CRASH_SIGNAL
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"gate_attempts": {}})
    )

    assert result.error_code == "E_RED_CRASHED", (
        f"AC6: expected E_RED_CRASHED, got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC6: fresh crash must be recoverable, got {result.recoverable!r}"
    )
    assert isinstance(result.data, dict), f"AC6: expected dict data, got {result.data!r}"
    assert result.data.get("retry_from_step") == 0, (
        f"AC6: expected retry_from_step 0 (build_red_prompt), got {result.data!r}"
    )
    assert "Abort trap: 6" in (result.error or ""), (
        f"AC6: crash marker from stdout must be relayed in error tail, got "
        f"{result.error!r}"
    )
    assert result.data.get("red_crash_reason") == "signal", (
        f"AC6/§2.3: expected red_crash_reason 'signal' in data, got {result.data!r}"
    )


def test_ac6b_crash_cap_exhausted_is_not_recoverable(monkeypatch, tmp_path):
    """AC6 / §3a (spec §4): second attempt at the same gate — `gate_attempts`
    pre-staged as {'red_crashed': 1} (§1i: explicit, no ambient state) meets
    the recoverable_once cap → recoverable False, terminal E_RED_CRASHED.
    Proves the retry loop is bounded rather than infinite.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=133, stdout=XCTEST_CRASH_SIGNAL
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd),
        _make_prev({"cycle": 2, "gate_attempts": {"red_crashed": 1}}),
    )

    assert result.error_code == "E_RED_CRASHED", (
        f"AC6b: expected terminal E_RED_CRASHED, got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC6b: cap-exhausted crash must NOT be recoverable, got "
        f"{result.recoverable!r}"
    )


# ═══ AC7 — 124 timeouts keep their own codes (check sits AFTER rc==124) ════


def test_ac7_pytest_timeout_keeps_e_red_pytest_timeout(monkeypatch, tmp_path):
    """AC7 (spec §4, §2.2): exit 124 on a py group must still yield
    E_RED_PYTEST_TIMEOUT — the crash check is placed strictly AFTER the
    rc==124 early return. The stdout deliberately carries a crash shape
    (0 executed) so a mis-placed gate would visibly steal this path.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="py", returncode=124, stdout=XCTEST_ZERO_EXECUTED
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"red_test_paths": ["tests/test_gh1095.py"]})
    )

    assert result.error_code == "E_RED_PYTEST_TIMEOUT", (
        f"AC7: py-group timeout must keep E_RED_PYTEST_TIMEOUT, got "
        f"{result.error_code!r}"
    )
    assert result.error_code != "E_RED_CRASHED", "AC7: timeout must not be reclassified"


def test_ac7_non_py_timeout_keeps_e_red_test_runner_timeout(monkeypatch, tmp_path):
    """AC7 (spec §4, §2.2): exit 124 on a non-py group must still yield
    E_RED_TEST_RUNNER_TIMEOUT, not E_RED_CRASHED.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=124, stdout=XCTEST_ZERO_EXECUTED
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_TEST_RUNNER_TIMEOUT", (
        f"AC7: non-py timeout must keep E_RED_TEST_RUNNER_TIMEOUT, got "
        f"{result.error_code!r}"
    )
    assert result.error_code != "E_RED_CRASHED", "AC7: timeout must not be reclassified"


# ═══ AC8 — code registered in both catalogs ════════════════════════════════


def test_ac8_e_red_crashed_registered_in_error_codes_catalogs():
    """AC8 (spec §4, §2.5): `E_RED_CRASHED` present in error_codes.ERROR_CODES
    and as a line in ERROR_CODES.md.
    """
    from bytedigger_engine import error_codes  # noqa: PLC0415 — deferred per D1CF5FDF

    assert "E_RED_CRASHED" in error_codes.ERROR_CODES, (
        "AC8: E_RED_CRASHED missing from error_codes.ERROR_CODES"
    )
    md = (ENGINE_ROOT / "bytedigger_engine/ERROR_CODES.md").read_text(encoding="utf-8")
    assert "E_RED_CRASHED" in md, "AC8: E_RED_CRASHED missing from ERROR_CODES.md"


# ═══ Direct unit tests of the §2.1 helpers (in addition to step-level) ═════


def test_classify_returns_signal_for_negative_and_high_exit_codes():
    """AC1 (spec §2.1 rule C1): returncode < 0, > 128, or a `signal code N`
    marker in the output all classify as 'signal'.
    """
    classify = _get_helper("classify_red_run_shape")

    assert classify(-11, "") == "signal", "C1: negative returncode must be 'signal'"
    assert classify(139, "") == "signal", "C1: returncode 139 (>128) must be 'signal'"
    assert classify(1, "Thread 0 crashed with signal code 11\n") == "signal", (
        "C1: 'signal code N' marker in output must be 'signal'"
    )


def test_classify_returns_zero_executed_and_exec_error_slugs():
    """AC2/AC3 (spec §2.1 rules C2-C4): zero executed → 'zero_executed';
    executed>0 with 0 failures and rc!=0 → 'exec_error_no_failures';
    unparsable output + rc!=0 + marker → 'exec_error_no_failures'.
    """
    classify = _get_helper("classify_red_run_shape")

    assert classify(5, PYTEST_ALL_PASSING.replace("3 passed", "0 passed")) == (
        "zero_executed"
    ), "C2: pytest '0 passed' must be 'zero_executed'"
    assert classify(1, BUN_ZERO_EXECUTED) == "zero_executed", (
        "C2: bun '0 pass / 0 fail' must be 'zero_executed'"
    )
    assert classify(1, "Executed 3 tests, with 0 failures in 0.1 seconds\n") == (
        "exec_error_no_failures"
    ), "C3: executed>0 + failures==0 + rc!=0 must be 'exec_error_no_failures'"
    assert classify(1, XCTEST_BUILD_ERROR) == "exec_error_no_failures", (
        "C4: unparsable output + rc!=0 + error marker must be 'exec_error_no_failures'"
    )


def test_classify_returns_none_for_legitimate_and_silent_shapes():
    """AC4/AC5 (spec §2.1 rule C5): a genuine RED, an all-passing run, and a
    silent non-zero exit from an unrecognised runner (no summary, no marker)
    all classify as None so the pre-existing code paths keep owning them.
    """
    classify = _get_helper("classify_red_run_shape")

    assert classify(1, PYTEST_GENUINE_RED) is None, "C5: genuine RED must be None"
    assert classify(0, PYTEST_ALL_PASSING) is None, "C5: all-passing must be None"
    assert classify(1, "some quiet suite output\n") is None, (
        "C5: silent non-zero exit with no summary and no marker must be None"
    )


def test_parse_red_shape_covers_pytest_bun_and_xctest_summaries():
    """AC9 (spec §2.1 table): parse_red_shape returns (executed, failures)
    for pytest, bun and xctest summaries, and None for unknown shapes.
    """
    parse = _get_helper("parse_red_shape")

    assert parse(PYTEST_GENUINE_RED) == (3, 2), "pytest: 2 failed + 1 passed → (3, 2)"
    assert parse(BUN_GENUINE_RED) == (4, 3), "bun: 1 pass + 3 fail → (4, 3)"
    assert parse(XCTEST_GENUINE_RED) == (15, 10), "xctest: 15 executed, 10 failures"
    assert parse("some quiet suite output\n") is None, (
        "unknown runner shape must return None"
    )


# ═══ AC10 (GATE-AMEND-1/3) — rc==0 zero-executed vs the GH1034 shape ═══════


def test_ac10_rc_zero_with_zero_executed_marker_returns_e_red_crashed(
    monkeypatch, tmp_path
):
    """AC10 (spec §4, §2.1 rule C2b): exit 0 with `Executed 0 tests, with 0
    failures` is a crash, not a pass — the §2.2 branch sits before
    `passing_groups.append`, so it shadows E_RED_NOT_FAILING. C2b fires
    regardless of returncode via `_RED_ZERO_EXECUTED_MARKERS`.
    """
    git_cwd = str(tmp_path)
    captured = _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=0, stdout=XCTEST_ZERO_EXECUTED
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_CRASHED", (
        f"AC10: rc==0 with zero executed tests must be E_RED_CRASHED (not "
        f"E_RED_NOT_FAILING), got {result.error_code!r}"
    )
    events = [e for e in captured if e["type"] == "red_crashed"]
    assert events and events[0]["payload"].get("reason") == "zero_executed", (
        f"AC10: expected reason 'zero_executed', got "
        f"{[e['payload'] for e in events]!r}"
    )


def test_ac10_rc_zero_with_gh1034_partial_match_shape_is_not_a_crash(
    monkeypatch, tmp_path
):
    """AC10 negative twin (spec §4, GATE-AMEND-1): the sibling GH1034 fixture
    (tests/test_gh1034_red_cycle2_degeneracy.py:140) emits stdout `0 failed`
    at rc==0, which parses to (0, 0) but carries NO zero-executed marker.
    C2 is narrowed to `returncode != 0` and C2b needs a marker, so this shape
    must reach C5 → the pre-existing GH1034 restore branch still runs and its
    ok result survives. A crash gate that swallowed this would redden 9
    sibling tests that GREEN may not touch (§1s).
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    restore_ok = StepResult(
        status="ok",
        data={"red_rewrite_rejected": True},
        duration_ms=0,
        step_name="verify_red_fails_mechanically",
    )
    _install_runner(
        monkeypatch,
        git_cwd,
        kind="py",
        returncode=0,
        stdout="0 failed\n",
        restore_result=restore_ok,
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"red_test_paths": ["tests/test_gh1095.py"]})
    )

    assert result.error_code is None, (
        f"AC10: GH1034 `0 failed` @ rc==0 must NOT be classified as a crash, "
        f"got error_code={result.error_code!r}"
    )
    assert result.status == "ok", (
        f"AC10: expected the GH1034 restore branch to still own this shape, "
        f"got status={result.status!r} error={result.error!r}"
    )


# ═══ AC11 (GATE-AMEND-3) — canonical zero-collect + C1 precedence ══════════


def test_ac11_canonical_pytest_zero_collected_classifies_zero_executed():
    """AC11 (spec §4, §2.1 rule C2b): the canonical pytest zero-collect form
    (`rc=5`, `no tests ran`, NO INTERNALERROR) matches no §2.1 regex and no
    exec-error marker — pre-amendment it fell through to C5 and counted as a
    valid RED, which is exactly the bug GH1095 closes.
    """
    classify = _get_helper("classify_red_run_shape")

    assert classify(5, "=========== no tests ran in 0.02s ===========\n") == (
        "zero_executed"
    ), "AC11/C2b: canonical pytest rc=5 'no tests ran' must be 'zero_executed'"
    assert classify(1, "collected 0 items\n") == "zero_executed", (
        "AC11/C2b: 'collected 0 items' marker must be 'zero_executed'"
    )


def test_ac11_signal_rule_takes_precedence_over_valid_summary():
    """AC11 (spec §4, §2.1): rule ordering is load-bearing — a signal exit
    (139) wins over an otherwise perfectly valid RED summary, which alone
    would classify as C5/None.
    """
    classify = _get_helper("classify_red_run_shape")

    assert classify(139, XCTEST_GENUINE_RED) == "signal", (
        "AC11/C1: rc=139 must classify as 'signal' even with a valid "
        "'Executed 15 tests, with 10 failures' summary (C1 precedes C5)"
    )


# ═══ AC12 (GATE-AMEND-2) — attribute-safe stdout/stderr accessor ═══════════


def test_ac12_crash_signal_present_only_on_stderr_returns_e_red_crashed(
    monkeypatch, tmp_path
):
    """AC12 (spec §4, §2.2): the crash evidence lives ONLY on stderr
    (`stdout=""`, `stderr="Segmentation fault: 11"`). The §2.2 accessor
    concatenates stdout+stderr, so the gate must still fire — today L2392
    reads stdout alone and this run is silently accepted as a valid RED.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch,
        git_cwd,
        kind="swift",
        returncode=1,
        stdout="",
        stderr="Segmentation fault: 11\n",
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_CRASHED", (
        f"AC12: stderr-only crash evidence must still yield E_RED_CRASHED, "
        f"got {result.error_code!r} (status={result.status!r})"
    )


def test_ac12_runner_object_without_stderr_attribute_does_not_raise(
    monkeypatch, tmp_path
):
    """AC12 (spec §4, GATE-AMEND-2): three sibling files stub the runner as
    `SimpleNamespace(returncode=…, stdout=…)` with NO `.stderr`. A direct
    `proc.stderr` read would raise AttributeError before any assertion, so
    the accessor is frozen as `getattr(proc, "stderr", "") or ""`.

    This test deliberately BYPASSES `_install_runner` (which always supplies
    stderr, and would therefore be blind to the regression) and patches
    `bounded_run` directly with a stderr-less object.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    monkeypatch.setattr(
        _p5,
        "bounded_run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout=PYTEST_GENUINE_RED),
    )
    monkeypatch.setattr(
        _p5,
        "run_test_command",
        lambda *a, **kw: SimpleNamespace(exit_code=1, n_passed=1, n_failed=2),
    )
    monkeypatch.setattr(
        _p5,
        "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [
                {"kind": "py", "argv": ["python3", "-m", "pytest", *paths],
                 "paths": list(paths)}
            ]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev=None: git_cwd)
    monkeypatch.setattr(_p5, "_detect_green_complete_resume", lambda *a, **kw: [])
    monkeypatch.setattr(_p5, "_attempt_red_cycle_restore", lambda *a, **kw: None)

    # Must not raise AttributeError.
    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd), _make_prev({"red_test_paths": ["tests/test_gh1095.py"]})
    )

    assert result.status == "ok", (
        f"AC12: stderr-less runner object with a genuine RED summary must "
        f"stay ok, got {result.status!r} error={result.error!r}"
    )
    assert result.error_code is None, (
        f"AC12: expected error_code None, got {result.error_code!r}"
    )


# ═══ AC13 — bounded tail, exit code, full telemetry payload ════════════════


def test_ac13_stdout_tail_is_bounded_to_2000_chars_with_exit_code(
    monkeypatch, tmp_path
):
    """AC13 (spec §4, §2.3): `data["stdout_tail"]` is the last 2000 chars of
    stdout+stderr (bounded, symmetric with the L2433 `stdout_tail[-2000:]`
    convention) and `data["red_crash_exit_code"]` carries the real exit code.
    """
    git_cwd = str(tmp_path)
    long_stdout = ("filler line for the gh1095 tail bound\n" * 200) + XCTEST_CRASH_SIGNAL
    combined = long_stdout  # stderr is empty for this fixture
    assert len(combined) > 2000, "fixture guard: output must exceed the 2000-char bound"
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=133, stdout=long_stdout
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_CRASHED", (
        f"AC13: expected E_RED_CRASHED, got {result.error_code!r}"
    )
    assert isinstance(result.data, dict), f"AC13: expected dict data, got {result.data!r}"
    tail = result.data.get("stdout_tail")
    assert isinstance(tail, str), f"AC13: expected str stdout_tail, got {tail!r}"
    assert len(tail) <= 2000, f"AC13: stdout_tail must be <= 2000 chars, got {len(tail)}"
    assert tail == combined[-2000:], (
        "AC13: stdout_tail must be exactly the last 2000 chars of stdout+stderr"
    )
    assert result.data.get("red_crash_exit_code") == 133, (
        f"AC13: expected red_crash_exit_code 133, got {result.data!r}"
    )


def test_ac13_red_crashed_telemetry_carries_all_five_payload_keys(
    monkeypatch, tmp_path
):
    """AC13 (spec §4, §2.4): the `red_crashed` event payload carries all five
    frozen keys — phase, step, group, reason, exit_code — with severity
    'warning'.
    """
    git_cwd = str(tmp_path)
    captured = _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=133, stdout=XCTEST_CRASH_SIGNAL
    )

    _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    events = [e for e in captured if e["type"] == "red_crashed"]
    assert events, (
        f"AC13/§2.4: expected a red_crashed event, got "
        f"{[e['type'] for e in captured]}"
    )
    payload = events[0]["payload"]
    for key in ("phase", "step", "group", "reason", "exit_code"):
        assert key in payload, f"AC13/§2.4: payload missing {key!r}: {payload!r}"
    assert payload["phase"] == 5, f"AC13/§2.4: {payload!r}"
    assert payload["step"] == "verify_red_fails_mechanically", f"AC13/§2.4: {payload!r}"
    assert payload["group"] == "swift", f"AC13/§2.4: {payload!r}"
    assert payload["reason"] == "signal", f"AC13/§2.4: {payload!r}"
    assert payload["exit_code"] == 133, f"AC13/§2.4: {payload!r}"


# ═══ AC14 — policy matrix rows + multi-group plans ═════════════════════════


@pytest.mark.parametrize("complexity", ["SIMPLE", "FEATURE", "COMPLEX"])
def test_ac14_red_crashed_policy_row_exists_for_every_build_class(
    monkeypatch, tmp_path, complexity
):
    """AC14 (spec §4, §2.3): `_POLICY_MATRIX` must carry an explicit
    `red_crashed` row (recoverable_once, cap 1) for SIMPLE, FEATURE and
    COMPLEX. §1i: gate_attempts passed explicitly as {} so attempts==0 is
    fixture state, never ambient — recoverable must be True for all three.
    A missing row masked by `_DEFAULT_POLICY` is what this pins.
    """
    git_cwd = str(tmp_path)
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=133, stdout=XCTEST_CRASH_SIGNAL
    )

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd, complexity=complexity), _make_prev({"gate_attempts": {}})
    )

    assert result.error_code == "E_RED_CRASHED", (
        f"AC14[{complexity}]: expected E_RED_CRASHED, got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC14[{complexity}]: first crash attempt must be recoverable "
        f"(explicit recoverable_once policy row), got {result.recoverable!r}"
    )


def test_ac14_crash_in_second_group_returns_e_red_crashed_naming_that_group(
    monkeypatch, tmp_path
):
    """AC14 (spec §4, §2.2): the check lives INSIDE the per-group loop — a
    two-group plan whose first group fails legitimately (py, 2 failed) and
    whose second group crashes (swift, exit 133) must return E_RED_CRASHED,
    with the telemetry `group` field naming the crashed group, not the first.
    """
    git_cwd = str(tmp_path)
    captured = _patch_emit(monkeypatch)
    calls = {"n": 0}
    # Deterministic per-group sequence (§1i: index-driven, no timing).
    shapes = [
        (1, PYTEST_GENUINE_RED),      # group 1: legitimate RED
        (133, XCTEST_CRASH_SIGNAL),   # group 2: crash
    ]

    def _bounded_run(*_a, **_kw):
        idx = min(calls["n"], len(shapes) - 1)
        calls["n"] += 1
        rc, out = shapes[idx]
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    monkeypatch.setattr(_p5, "bounded_run", _bounded_run)
    monkeypatch.setattr(
        _p5,
        "run_test_command",
        lambda *a, **kw: SimpleNamespace(exit_code=1, n_passed=1, n_failed=2),
    )
    monkeypatch.setattr(
        _p5,
        "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [
                {"kind": "py", "argv": ["python3", "-m", "pytest"],
                 "paths": ["tests/test_gh1095.py"]},
                {"kind": "swift", "argv": ["swift", "test"],
                 "paths": ["Tests/GH1095Tests.swift"]},
            ]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev=None: git_cwd)
    monkeypatch.setattr(_p5, "_detect_green_complete_resume", lambda *a, **kw: [])
    monkeypatch.setattr(_p5, "_attempt_red_cycle_restore", lambda *a, **kw: None)

    result = _p5._verify_red_fails_mechanically(
        _make_ctx(git_cwd),
        _make_prev({"red_test_paths": ["tests/test_gh1095.py", "Tests/GH1095Tests.swift"]}),
    )

    assert result.error_code == "E_RED_CRASHED", (
        f"AC14: crash in the SECOND group must still yield E_RED_CRASHED, got "
        f"{result.error_code!r} (status={result.status!r})"
    )
    events = [e for e in captured if e["type"] == "red_crashed"]
    assert events, (
        f"AC14: expected a red_crashed event, got {[e['type'] for e in captured]}"
    )
    assert events[0]["payload"].get("group") == "swift", (
        f"AC14: telemetry must name the crashed group 'swift', got "
        f"{events[0]['payload']!r}"
    )


# ═══ AC15 — zero-match output returns None, not a (0, 0) tuple ════════════


def test_ac15_zero_match_output_returns_none_not_a_zero_tuple():
    """AC15 (spec §4, §2.1 zero-match freeze): when NO line matches any of the
    three parsers, `parse_red_shape` returns None, NOT (0, 0). Otherwise C4
    would be dead code and C2 would fire on every silent non-zero exit
    (breaking test_phase_5_implement_C76F6F3C.py:63, `rc=1, stdout=""`).
    """
    parse = _get_helper("parse_red_shape")
    classify = _get_helper("classify_red_run_shape")

    assert parse("") is None, f"AC15: empty output must be None, got {parse('')!r}"
    # Hoisted out of the f-string: a backslash inside an f-string EXPRESSION is a
    # SyntaxError before Python 3.12 (PEP 701), and bytedigger's clean room runs 3.11.
    quiet = parse("some quiet suite output\n")
    assert quiet is None, f"AC15: unmatched output must be None, got {quiet!r}"
    assert classify(1, "") is None, (
        f"AC15: rc=1 with empty output must classify as None (no crash), got "
        f"{classify(1, '')!r}"
    )


# ═══ AC16 — concatenation order is stdout + stderr, not the reverse ════════


def test_ac16_stdout_tail_concatenation_order_is_stdout_then_stderr(
    monkeypatch, tmp_path
):
    """AC16 (spec §4, §2.2): with BOTH streams non-empty, `data["stdout_tail"]`
    must be `(stdout + stderr)[-2000:]`. AC13's fixture has an empty stderr and
    AC12's an empty stdout, so a GREEN that concatenated stderr+stdout would
    satisfy both — this test is the one that reddens on a flipped order.
    """
    git_cwd = str(tmp_path)
    stdout = "STDOUT-HEAD\n" + ("stdout filler line for the gh1095 order pin\n" * 60)
    stderr = "Abort trap: 6\nSTDERR-TAIL-SENTINEL\n"
    combined = stdout + stderr
    assert len(combined) > 2000, "fixture guard: combined output must exceed 2000 chars"
    _patch_emit(monkeypatch)
    _install_runner(
        monkeypatch, git_cwd, kind="swift", returncode=133, stdout=stdout, stderr=stderr
    )

    result = _p5._verify_red_fails_mechanically(_make_ctx(git_cwd), _make_prev())

    assert result.error_code == "E_RED_CRASHED", f"AC16: {result.error_code!r}"
    tail = result.data.get("stdout_tail")
    assert isinstance(tail, str), f"AC16: expected str stdout_tail, got {tail!r}"
    assert tail == combined[-2000:], (
        "AC16: stdout_tail must be (stdout + stderr)[-2000:] — a reversed "
        "concatenation would end with the stdout filler instead. Got tail "
        f"ending {tail[-40:]!r}"
    )
    assert tail.endswith("STDERR-TAIL-SENTINEL\n"), (
        "AC16: the tail must end with the stderr sentinel (stderr concatenated "
        f"LAST), got {tail[-40:]!r}"
    )


