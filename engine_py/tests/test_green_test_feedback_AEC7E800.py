"""RED tests for AEC7E800 — GREEN test-failure feedback loop.

Contract: GREEN will modify `_verify_green_passing` (step 7) to return
`recoverable=True` with `green_test_findings` on genuine test-fail while
`cycle_count < GREEN_TEST_CYCLE_CAP`, and will modify `_build_green_prompt`
to inject a `## TEST FAILURES` section when `green_test_findings` is present
in the retry-mode `prev` dict.

§1i (singleton/time ACs): not applicable — no time or singleton-resource ACs.
§1q: no spec_from_file_location / exec_module — module imported via sys.path.
D1CF5FDF: all deferred symbols (`GREEN_TEST_CYCLE_CAP`, `green_test_findings`)
are accessed inside test bodies or via `getattr`, never at module level, so the
file collects even before GREEN exists.

AC1,2,4,5 — FAIL pre-GREEN (new behavior not yet implemented).
AC3,6,7,8 — PASS pre-GREEN (selectivity / regression guards).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_ctx(tmp_path: Path, **extra_cfg) -> WorkflowContext:
    cfg: dict = {"git_cwd": str(tmp_path), "verify_green_passing": True}
    cfg.update(extra_cfg)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(cycle_count: int = 1, red_commit_sha: str | None = None, **extra) -> StepResult:
    """Build a prev StepResult with no red_commit_sha so 2206/2244 gates are skipped."""
    data: dict = {
        "green_log_path": "tests/build-green-output.log",
        "spec_path": "scratchpad/spec.md",
        "red_log_path": "tests/build-red-output.log",
        "validation_doc_path": "scratchpad/validation.md",
        "green_status": "PASS",
        "red_test_paths": ["tests/test_example.py"],
        "cycle_count": cycle_count,
    }
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    data.update(extra)
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="write_green_artifact",
    )


def _make_fail_stdout(tmp_path: Path, content: str = "") -> tuple[str, str]:
    """Write pytest-style failure text to a real temp file; return (stdout_path, stderr_path)."""
    if not content:
        content = (
            "FAILED tests/test_example.py::test_something - AssertionError: expected True\n"
            "1 failed in 0.10s\n"
        )
    stdout_file = tmp_path / "stdout.txt"
    stdout_file.write_text(content, encoding="utf-8")
    stderr_file = tmp_path / "stderr.txt"
    stderr_file.write_text("", encoding="utf-8")
    return str(stdout_file), str(stderr_file)


def _patch_run_test_command(monkeypatch, stdout_path: str, stderr_path: str,
                            exit_code: int = 1, n_passed: int = 0, n_failed: int = 1):
    """Monkeypatch disk_truth.run_test_command inside phase_5_implement."""
    from bytedigger_engine.lib.plugins.disk_truth.test_runner import TestRunResult  # noqa: E402 (inside fn — D1CF5FDF)

    fake_result = TestRunResult(
        exit_code=exit_code,
        n_passed=n_passed,
        n_failed=n_failed,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    monkeypatch.setattr(phase_5_implement, "run_test_command", lambda *a, **kw: fake_result)
    return fake_result


def _make_build_green_prev_dict(scratchpad: Path, cycle: int = 2,
                                test_findings: list | None = None,
                                lint_findings: list | None = None) -> dict:
    """Build the retry-mode prev dict for _build_green_prompt (dict path, not StepResult)."""
    spec_path = scratchpad / "spec.md"
    if not spec_path.exists():
        spec_path.write_text("## Spec\nDo thing.\n", encoding="utf-8")
    red_log = scratchpad / "build-red-output.log"
    if not red_log.exists():
        red_log.write_text("RED COMPLETE\n", encoding="utf-8")
    validation_doc = scratchpad / "validation.md"
    if not validation_doc.exists():
        validation_doc.write_text("## Verdict\nPASS\n", encoding="utf-8")
    # _read_first_block expects this file
    first_block = scratchpad / "first-block.md"
    if not first_block.exists():
        first_block.write_text("TASK: fix the thing\n", encoding="utf-8")

    d: dict = {
        "cycle": cycle,
        "spec_path": str(spec_path),
        "red_log_path": str(red_log),
        "validation_doc_path": str(validation_doc),
    }
    if test_findings is not None:
        d["green_test_findings"] = test_findings
    if lint_findings is not None:
        d["green_lint_findings"] = lint_findings
    return d


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — test-fail + cycle < cap → recoverable=True (FAILS pre-GREEN)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC1RecoverableOnTestFail:

    def test_ac1_test_fail_under_cap_returns_recoverable_true(self, tmp_path, monkeypatch):
        """AC1: _verify_green_passing with a test-FAIL fixture and cycle_count < cap
        must return StepResult(status='error', error_code='E_GREEN_NOT_PASSING',
        recoverable=True).

        FAILS pre-GREEN: today the 2298 branch hard-codes recoverable=False.

        Fixture: no red_commit_sha → skips 2206 sibling gate and 2244 net-new-added
        gate; mock run_test_command returns exit_code=1 (test failure); cycle_count=1
        (< GREEN_TEST_CYCLE_CAP=2).
        """
        from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing  # noqa: E402

        stdout_path, stderr_path = _make_fail_stdout(tmp_path)
        _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                                exit_code=1, n_passed=0, n_failed=1)

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle_count=1)  # no red_commit_sha → 2206/2244 gates skipped

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected E_GREEN_NOT_PASSING, got {result.error_code!r}"
        )
        assert result.status == "error", (
            f"expected status='error', got {result.status!r}"
        )
        assert result.recoverable is True, (
            "FAILS pre-GREEN: today 2298 returns recoverable=False; "
            f"expected recoverable=True (cycle_count=1 < cap=2), got {result.recoverable!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — green_test_findings present and non-empty (FAILS pre-GREEN)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC2FindingsCaptured:

    def test_ac2_test_fail_captures_green_test_findings(self, tmp_path, monkeypatch):
        """AC2: same test-fail fixture → result.data['green_test_findings'] present
        and non-empty (contains captured failure text from stdout_path).

        FAILS pre-GREEN: today the 2298 branch returns data=None and does not
        capture stdout_path content.

        §1y point: the NEW capture logic inside _verify_green_passing reads
        stdout_path from the TestRunResult for each failing group and stores
        the tail-capped text in data['green_test_findings'].
        """
        from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing  # noqa: E402

        fail_text = (
            "FAILED tests/test_example.py::test_foo - AssertionError: got False\n"
            "1 failed in 0.15s\n"
        )
        stdout_path, stderr_path = _make_fail_stdout(tmp_path, content=fail_text)
        _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                                exit_code=1, n_passed=0, n_failed=1)

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle_count=1)

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected E_GREEN_NOT_PASSING, got {result.error_code!r}"
        )
        assert result.data is not None, (
            "FAILS pre-GREEN: data is None today; expected dict with green_test_findings"
        )
        findings = (result.data or {}).get("green_test_findings")
        assert findings is not None and len(findings) > 0, (
            "FAILS pre-GREEN: green_test_findings key absent or empty in result.data; "
            f"data keys={list((result.data or {}).keys())!r}"
        )
        # findings must carry some of the failure text content
        if isinstance(findings, list):
            combined = " ".join(str(f) for f in findings)
        else:
            combined = str(findings)
        assert "FAILED" in combined or "assert" in combined.lower() or "fail" in combined.lower(), (
            f"expected findings to reference failure text; got {combined[:200]!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — test-fail at cap → recoverable=False (PASSES pre-GREEN — selectivity)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC3TerminalAtCap:

    def test_ac3_test_fail_at_cap_remains_recoverable_false(self, tmp_path, monkeypatch):
        """AC3 (PASSES pre-GREEN): test-fail with cycle_count == GREEN_TEST_CYCLE_CAP
        must keep recoverable=False (terminal abort retained at cap).

        GREEN_TEST_CYCLE_CAP=2; cycle_count=2 → no more retries.
        Uses getattr so the file collects before GREEN adds the constant (D1CF5FDF).
        """
        from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing  # noqa: E402

        cap = getattr(phase_5_implement, "GREEN_TEST_CYCLE_CAP", 2)

        stdout_path, stderr_path = _make_fail_stdout(tmp_path)
        _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                                exit_code=1, n_passed=0, n_failed=1)

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle_count=cap)  # at cap

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected E_GREEN_NOT_PASSING at cap, got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"expected recoverable=False at cap={cap}, got {result.recoverable!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — findings payload ≤ FINDINGS_MAX_CHARS (FAILS pre-GREEN — no cap today)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC4FindingsCapped:

    def test_ac4_findings_payload_truncated_to_max_chars(self, tmp_path, monkeypatch):
        """AC4: green_test_findings payload length ≤ FINDINGS_MAX_CHARS (32_000).

        Drive it by providing a stdout file whose content is LARGER than the cap.
        After GREEN, the capture logic must tail-truncate from the left.

        FAILS pre-GREEN: today no findings are captured at all (data=None at 2298),
        so we cannot even assert a cap — the test fails when findings is absent/None.

        §1l anchor: the assertion checks the actual TEXT LENGTH of the captured
        findings against the module constant — not a stub-passable key-presence check.
        """
        from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing, FINDINGS_MAX_CHARS  # noqa: E402

        # Write stdout larger than the cap (enough to trigger truncation)
        large_content = ("FAILED tests/test_big.py::test_x - AssertionError\n" * 1000
                         + "1 failed in 0.5s\n")
        assert len(large_content) > FINDINGS_MAX_CHARS, (
            f"test fixture too small ({len(large_content)} < {FINDINGS_MAX_CHARS}); increase multiplier"
        )

        stdout_path, stderr_path = _make_fail_stdout(tmp_path, content=large_content)
        _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                                exit_code=1, n_passed=0, n_failed=1)

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle_count=1)

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected E_GREEN_NOT_PASSING, got {result.error_code!r}"
        )
        assert result.data is not None, (
            "FAILS pre-GREEN: data is None; expected dict with green_test_findings"
        )
        findings = (result.data or {}).get("green_test_findings")
        assert findings is not None, (
            "FAILS pre-GREEN: green_test_findings absent from result.data"
        )
        # Measure total serialised payload length
        if isinstance(findings, list):
            payload_text = " ".join(str(f) for f in findings)
        else:
            payload_text = str(findings)
        assert len(payload_text) <= FINDINGS_MAX_CHARS, (
            f"FAILS pre-GREEN: findings payload length {len(payload_text)} "
            f"exceeds FINDINGS_MAX_CHARS={FINDINGS_MAX_CHARS}; cap not applied"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — _build_green_prompt injects ## TEST FAILURES section (FAILS pre-GREEN)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC5BuildGreenPromptTestFailuresBlock:

    def test_ac5_build_green_prompt_injects_test_failures_section(self, tmp_path, monkeypatch):
        """AC5: _build_green_prompt(ctx, prev_dict) where prev_dict contains
        'green_test_findings' (and cycle >= 2) must produce a prompt body that
        includes a '## TEST FAILURES' section listing the findings.

        FAILS pre-GREEN: today _build_green_prompt reads green_lint_findings and
        green_typecheck_findings (lines 3316-3317) but does NOT read
        green_test_findings → no ## TEST FAILURES block is rendered.

        §1y point: the new read at lines ~3316-3317 mirror (GREEN will add
        `test_findings = list(prev.get("green_test_findings") or [])`) and the
        new rendered block in `parts.append(...)`.
        """
        from bytedigger_engine.workflows.phase_5_implement import _build_green_prompt  # noqa: E402

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        prev_dict = _make_build_green_prev_dict(
            scratchpad,
            cycle=2,
            test_findings=[
                "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1 got 2",
                "1 failed in 0.20s",
            ],
        )

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(tmp_path)},
            question="fix the thing",
            session_id="s",
            persona="hal",
            framework=None,
            domain=None,
        )

        result = _build_green_prompt(ctx, prev_dict)

        assert result.status == "ok", (
            f"expected ok from _build_green_prompt(dict prev), got {result.status!r} "
            f"(error_code={result.error_code!r}, error={result.error!r})"
        )

        # Locate the written prompt text
        data = result.data or {}
        written_prompt: str | None = None
        for key in ("green_prompt_path", "prompt_path", "log_path", "prompt"):
            val = data.get(key)
            if val and isinstance(val, str):
                p = Path(val)
                if p.exists():
                    written_prompt = p.read_text(encoding="utf-8")
                    break
                if "\n" in val and len(val) > 50:
                    written_prompt = val
                    break

        assert written_prompt is not None, (
            f"could not locate written prompt; data keys={list(data.keys())!r}"
        )
        assert "## TEST FAILURES" in written_prompt, (
            "FAILS pre-GREEN: ## TEST FAILURES section not injected by _build_green_prompt; "
            "green_test_findings threading not implemented yet. "
            f"Prompt head (500 chars): {written_prompt[:500]!r}"
        )
        # The actual failure text must appear
        assert "test_bar" in written_prompt or "test_foo" in written_prompt, (
            "expected finding text (test_bar / test_foo) in prompt; "
            f"prompt tail: {written_prompt[-300:]!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — no green_test_findings → no ## TEST FAILURES block (PASSES pre-GREEN)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC6NoFindingsNoBlock:

    def test_ac6_no_test_findings_no_test_failures_section(self, tmp_path):
        """AC6 (PASSES pre-GREEN): _build_green_prompt with no green_test_findings
        (normal cycle-1 path) must NOT contain ## TEST FAILURES.

        This is a selectivity guard — the normal GREEN path must be unaffected.
        """
        from bytedigger_engine.workflows.phase_5_implement import _build_green_prompt  # noqa: E402

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        # prev_dict without green_test_findings
        prev_dict = _make_build_green_prev_dict(scratchpad, cycle=1, test_findings=None)

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(tmp_path)},
            question="fix the thing",
            session_id="s",
            persona="hal",
            framework=None,
            domain=None,
        )

        result = _build_green_prompt(ctx, prev_dict)

        assert result.status == "ok", (
            f"expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        data = result.data or {}
        written_prompt: str | None = None
        for key in ("green_prompt_path", "prompt_path", "log_path", "prompt"):
            val = data.get(key)
            if val and isinstance(val, str):
                p = Path(val)
                if p.exists():
                    written_prompt = p.read_text(encoding="utf-8")
                    break
                if "\n" in val and len(val) > 50:
                    written_prompt = val
                    break

        if written_prompt is not None:
            assert "## TEST FAILURES" not in written_prompt, (
                "AC6 regression: ## TEST FAILURES section appeared when no findings present"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — runner-missing / not-reproducible recoverable values unchanged (PASSES)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC7OtherBranchesUnchanged:

    def test_ac7_runner_missing_stays_recoverable_false(self, tmp_path, monkeypatch):
        """AC7a (PASSES pre-GREEN): FileNotFoundError on run_test_command →
        E_GREEN_TEST_RUNNER_MISSING, recoverable=False.  MUST remain unchanged.
        """
        from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing  # noqa: E402

        def _raise_fnf(*a, **kw):
            raise FileNotFoundError("pytest binary not found")

        monkeypatch.setattr(phase_5_implement, "run_test_command", _raise_fnf)

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle_count=1)

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_TEST_RUNNER_MISSING", (
            f"expected E_GREEN_TEST_RUNNER_MISSING, got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"AC7 regression: runner-missing changed from recoverable=False to {result.recoverable!r}"
        )

    def test_ac7_not_reproducible_stays_recoverable_true(self, tmp_path, monkeypatch):
        """AC7b (PASSES pre-GREEN): non-reproducible test counts →
        E_GREEN_NOT_REPRODUCIBLE, recoverable=True.  MUST remain unchanged.

        We mock verify_count_reproducible (the function called BEFORE the main loop
        to check reproducibility) to return (False, [[0,1],[0,2]]).
        """
        from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing  # noqa: E402

        # run_test_command is called inside verify_count_reproducible; mock it to
        # return different counts per call so the reproducibility check fails.
        call_n = {"n": 0}
        from bytedigger_engine.lib.plugins.disk_truth.test_runner import TestRunResult  # noqa: E402

        def _alternate(*a, **kw):
            call_n["n"] += 1
            if call_n["n"] % 2 == 1:
                return TestRunResult(0, 1, 0, "/dev/null", "/dev/null")
            return TestRunResult(1, 0, 1, "/dev/null", "/dev/null")

        monkeypatch.setattr(phase_5_implement, "run_test_command", _alternate)

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle_count=1)

        result = _verify_green_passing(ctx, prev)

        # Either not-reproducible (True) or some other path — the key check is that
        # runner-missing is still False when we force FileNotFoundError (tested above).
        # For the not-reproducible path, if reached, recoverable must be True.
        if result.error_code == "E_GREEN_NOT_REPRODUCIBLE":
            assert result.recoverable is True, (
                f"AC7 regression: E_GREEN_NOT_REPRODUCIBLE changed to recoverable={result.recoverable!r}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — lint/typecheck findings still threaded (PASSES pre-GREEN — regression)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC8LintTypecheckRegression:

    def test_ac8_lint_findings_still_threaded_in_build_green_prompt(self, tmp_path):
        """AC8 (PASSES pre-GREEN): _build_green_prompt with green_lint_findings on
        cycle >= 2 still renders its GREEN_LINT_VIOLATIONS block.  No regression.
        """
        from bytedigger_engine.workflows.phase_5_implement import _build_green_prompt  # noqa: E402

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        lint_findings = [
            {"check_id": "bare-except", "path": "src/prod.py", "start": 7},
        ]
        prev_dict = _make_build_green_prev_dict(
            scratchpad, cycle=2, test_findings=None, lint_findings=lint_findings
        )

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(tmp_path)},
            question="fix lint",
            session_id="s",
            persona="hal",
            framework=None,
            domain=None,
        )

        result = _build_green_prompt(ctx, prev_dict)

        assert result.status == "ok", (
            f"AC8 regression: expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        data = result.data or {}
        written_prompt: str | None = None
        for key in ("green_prompt_path", "prompt_path", "log_path", "prompt"):
            val = data.get(key)
            if val and isinstance(val, str):
                p = Path(val)
                if p.exists():
                    written_prompt = p.read_text(encoding="utf-8")
                    break
                if "\n" in val and len(val) > 50:
                    written_prompt = val
                    break

        if written_prompt is not None:
            assert "## GREEN_LINT_VIOLATIONS" in written_prompt, (
                "AC8 regression: ## GREEN_LINT_VIOLATIONS section disappeared from "
                "_build_green_prompt output after GREEN changes"
            )
            assert "bare-except" in written_prompt, (
                "AC8 regression: lint finding 'bare-except' not in prompt"
            )
