"""RED tests for 34AEB235 — phase_5 GREEN mypy typecheck gate (shift-left).

14 ACs (AC1–AC13 + AC10b) — pre-GREEN expected: ALL FAIL.

The new symbols (_mypy_base_argv, _parse_mypy_output, _verify_green_typecheck)
do NOT exist yet in phase_5_implement.py, and "green_typecheck" is NOT yet in
_recoverable_policy._POLICY_MATRIX.  Every test references these symbols INSIDE
the test body (AttributeError at assert time, not ImportError at collect time —
§1q / D1CF5FDF collectability guard).

Import pattern: modules imported at top level (both collect fine today);
not-yet-existing attrs accessed inside test body.  Mirrors the conftest-path-
singleton pattern (conftest.py inserts engine_py root + workflows on sys.path
at import time) — no module-level sys.path manipulation here (§1q / 81F97F3D).
"""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# Both modules import cleanly today (conftest inserts sys.path).
# Not-yet-existing attrs (_mypy_base_argv, etc.) referenced inside test bodies.
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows import _recoverable_policy  # noqa: E402

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402

# ─── shared helpers ───────────────────────────────────────────────────────────

pytestmark = pytest.mark.usefixtures()


def _make_ctx(git_cwd: str, build_class: str = "SIMPLE") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd, "build_class": build_class},
        question="q",
        session_id="test-34ae235",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(
    git_cwd: str,
    red_commit_sha: str | None = None,
    cycle: int = 1,
    build_class: str = "SIMPLE",
    extra: dict | None = None,
) -> StepResult:
    data: dict[str, Any] = {
        "git_cwd": git_cwd,
        "build_class": build_class,
        "cycle": cycle,
    }
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    if extra:
        data.update(extra)
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="verify_green_passing",
    )


def _init_git_repo(repo: Path) -> None:
    """Create a minimal real git repo (§1j: realpath for macOS /var/folders)."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_repo_with_red_commit(tmp_path: Path) -> tuple[Path, str]:
    """Real git repo: baseline commit, then a RED commit; returns (repo, red_sha)."""
    repo = Path(os.path.realpath(str(tmp_path / "repo")))  # §1j macOS symlink fix
    _init_git_repo(repo)
    (repo / "placeholder.py").write_text("# baseline\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    # RED commit: a stub test file (simulates red test)
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_stub.py").write_text("def test_placeholder(): pass\n")
    subprocess.run(["git", "add", "tests/test_stub.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "RED commit"], cwd=repo, check=True)
    red_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return repo, red_sha


# ─── AC1: _mypy_base_argv uses uvx when available, else mypy ─────────────────


class TestAC1MypyBaseArgvBinary:
    """AC1 — _mypy_base_argv uses ['uvx','mypy'] when uvx on PATH, else ['mypy']."""

    def test_ac1_uses_uvx_mypy_when_uvx_present(self, tmp_path: Path, monkeypatch) -> None:
        """monkeypatch shutil.which inside phase_5_implement to simulate uvx present."""
        monkeypatch.setattr(phase_5_implement.shutil, "which",
                            lambda name: "/usr/bin/uvx" if name == "uvx" else None)
        fn = getattr(phase_5_implement, "_mypy_base_argv",
                     None)  # AttributeError if not yet added
        assert fn is not None, (
            "_mypy_base_argv not found in phase_5_implement — not yet implemented (expected RED fail)"
        )
        argv = fn(str(tmp_path))
        assert argv[:2] == ["uvx", "mypy"], (
            f"Expected argv[:2]==['uvx','mypy'] when uvx present; got {argv[:2]!r}"
        )

    def test_ac1_uses_mypy_when_uvx_absent(self, tmp_path: Path, monkeypatch) -> None:
        """monkeypatch shutil.which to return None for uvx → ['mypy'] prefix."""
        monkeypatch.setattr(phase_5_implement.shutil, "which", lambda name: None)
        fn = getattr(phase_5_implement, "_mypy_base_argv", None)
        assert fn is not None, (
            "_mypy_base_argv not found in phase_5_implement — not yet implemented (expected RED fail)"
        )
        argv = fn(str(tmp_path))
        assert argv[0] == "mypy", (
            f"Expected argv[0]=='mypy' when uvx absent; got {argv[0]!r}"
        )
        assert argv[:2] != ["uvx", "mypy"], (
            f"Should NOT use uvx when not on PATH; got {argv[:2]!r}"
        )


# ─── AC2: --ignore-missing-imports only when no config present ────────────────


class TestAC2IgnoreMissingImportsFlag:
    """AC2 — --ignore-missing-imports appended ONLY when no target-repo config."""

    def test_ac2_ignore_missing_imports_absent_when_mypy_ini_present(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Create a mypy.ini in git_cwd → flag must NOT appear."""
        (tmp_path / "mypy.ini").write_text("[mypy]\n")
        monkeypatch.setattr(phase_5_implement.shutil, "which", lambda name: None)
        fn = getattr(phase_5_implement, "_mypy_base_argv", None)
        assert fn is not None, (
            "_mypy_base_argv not found — not yet implemented (expected RED fail)"
        )
        argv = fn(str(tmp_path))
        assert "--ignore-missing-imports" not in argv, (
            f"--ignore-missing-imports must be absent when mypy.ini exists; argv={argv!r}"
        )

    def test_ac2_ignore_missing_imports_present_when_no_config(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """No mypy.ini/setup.cfg/pyproject.toml → --ignore-missing-imports must appear."""
        monkeypatch.setattr(phase_5_implement.shutil, "which", lambda name: None)
        fn = getattr(phase_5_implement, "_mypy_base_argv", None)
        assert fn is not None, (
            "_mypy_base_argv not found — not yet implemented (expected RED fail)"
        )
        argv = fn(str(tmp_path))
        assert "--ignore-missing-imports" in argv, (
            f"--ignore-missing-imports must be present when no config; argv={argv!r}"
        )


# ─── AC3: --follow-imports=silent always present ──────────────────────────────


class TestAC3FollowImportsSilent:
    """AC3 — --follow-imports=silent always in argv regardless of config presence."""

    def test_ac3_follow_imports_silent_with_config(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "mypy.ini").write_text("[mypy]\n")
        monkeypatch.setattr(phase_5_implement.shutil, "which", lambda name: None)
        fn = getattr(phase_5_implement, "_mypy_base_argv", None)
        assert fn is not None, (
            "_mypy_base_argv not found — not yet implemented (expected RED fail)"
        )
        argv = fn(str(tmp_path))
        assert "--follow-imports=silent" in argv, (
            f"--follow-imports=silent must always be present (with config); argv={argv!r}"
        )

    def test_ac3_follow_imports_silent_without_config(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(phase_5_implement.shutil, "which", lambda name: None)
        fn = getattr(phase_5_implement, "_mypy_base_argv", None)
        assert fn is not None, (
            "_mypy_base_argv not found — not yet implemented (expected RED fail)"
        )
        argv = fn(str(tmp_path))
        assert "--follow-imports=silent" in argv, (
            f"--follow-imports=silent must always be present (without config); argv={argv!r}"
        )


# ─── AC4: _parse_mypy_output extracts finding with [code] suffix ──────────────


class TestAC4ParseMypyOutputFinding:
    """AC4 — _parse_mypy_output extracts {check_id,severity,path,start} from error line."""

    def test_ac4_parse_error_line_with_code_suffix(self) -> None:
        """Feed a real mypy error line with [assignment] suffix."""
        stdout = "foo.py:12: error: Incompatible types  [assignment]\n"
        fn = getattr(phase_5_implement, "_parse_mypy_output", None)
        assert fn is not None, (
            "_parse_mypy_output not found — not yet implemented (expected RED fail)"
        )
        findings = fn(stdout)
        assert len(findings) == 1, f"Expected 1 finding; got {findings!r}"
        f = findings[0]
        assert f["check_id"] == "assignment", f"check_id wrong: {f!r}"
        assert f["severity"] == "error", f"severity wrong: {f!r}"
        assert f["path"] == "foo.py", f"path wrong: {f!r}"
        assert f["start"] == 12, f"start wrong: {f!r}"

    def test_ac4_check_id_defaults_to_mypy_when_no_code(self) -> None:
        """Line without [code] suffix → check_id defaults to 'mypy'."""
        stdout = "bar.py:5: error: Some untyped error\n"
        fn = getattr(phase_5_implement, "_parse_mypy_output", None)
        assert fn is not None, (
            "_parse_mypy_output not found — not yet implemented (expected RED fail)"
        )
        findings = fn(stdout)
        assert len(findings) == 1, f"Expected 1 finding; got {findings!r}"
        assert findings[0]["check_id"] == "mypy", (
            f"check_id should default to 'mypy' when no [code] suffix; got {findings[0]!r}"
        )


# ─── AC5: note: lines ignored, Success: → [] ──────────────────────────────────


class TestAC5ParseMypyOutputIgnoreNoteAndSuccess:
    """AC5 — note: lines ignored; 'Success:' output → []."""

    def test_ac5_note_lines_ignored(self) -> None:
        stdout = (
            "foo.py:3: note: See https://mypy.readthedocs.io\n"
            "foo.py:3: note: Revealed type is 'builtins.int'\n"
        )
        fn = getattr(phase_5_implement, "_parse_mypy_output", None)
        assert fn is not None, (
            "_parse_mypy_output not found — not yet implemented (expected RED fail)"
        )
        findings = fn(stdout)
        assert findings == [], (
            f"note: lines must be ignored (returns []); got {findings!r}"
        )

    def test_ac5_success_line_returns_empty(self) -> None:
        stdout = "Success: no issues found in 1 source file\n"
        fn = getattr(phase_5_implement, "_parse_mypy_output", None)
        assert fn is not None, (
            "_parse_mypy_output not found — not yet implemented (expected RED fail)"
        )
        findings = fn(stdout)
        assert findings == [], (
            f"'Success:' output must return []; got {findings!r}"
        )

    def test_ac5_mixed_note_and_success_returns_empty(self) -> None:
        stdout = (
            "foo.py:1: note: See docs\n"
            "Success: no issues found in 1 source file\n"
        )
        fn = getattr(phase_5_implement, "_parse_mypy_output", None)
        assert fn is not None, (
            "_parse_mypy_output not found — not yet implemented (expected RED fail)"
        )
        findings = fn(stdout)
        assert findings == [], (
            f"Mixed note+Success must return []; got {findings!r}"
        )


# ─── AC6: column-form path:line:col: parsed correctly ────────────────────────


class TestAC6ParseMypyOutputColumnForm:
    """AC6 — _parse_mypy_output handles path:line:col: error: form; start==line."""

    def test_ac6_column_form_parsed(self) -> None:
        stdout = "foo.py:3:5: error: Name 'x' is not defined  [name-defined]\n"
        fn = getattr(phase_5_implement, "_parse_mypy_output", None)
        assert fn is not None, (
            "_parse_mypy_output not found — not yet implemented (expected RED fail)"
        )
        findings = fn(stdout)
        assert len(findings) == 1, f"Expected 1 finding from column-form; got {findings!r}"
        assert findings[0]["start"] == 3, (
            f"start must be line number (3), not column; got {findings[0]!r}"
        )
        assert findings[0]["check_id"] == "name-defined", (
            f"check_id should be 'name-defined'; got {findings[0]!r}"
        )
        assert findings[0]["path"] == "foo.py", (
            f"path should be 'foo.py'; got {findings[0]!r}"
        )


# ─── AC7: gate returns recoverable retry when changed file has type error ──────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC7RecoverableRetryOnTypeError:
    """AC7 — [core, forcing-fn] _verify_green_typecheck returns recoverable retry
    with error_code='E_GREEN_TYPECHECK_RETRY' and green_typecheck_findings non-empty
    when a changed .py file has a type error (cycle 1, SIMPLE)."""

    def test_ac7_recoverable_retry_with_findings(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        # Write a changed .py with a mypy error (staged, not committed — simulates GREEN)
        changed = repo / "changed_module.py"
        changed.write_text("x: int = 'not an int'\n")
        subprocess.run(["git", "add", "changed_module.py"], cwd=repo, check=True)

        # Canned mypy stdout simulating a real type error
        canned_stdout = "changed_module.py:1: error: Incompatible types  [assignment]\n"
        canned_stderr = ""

        _captured_argv: list[list[str]] = []
        _real_run = subprocess.run  # capture before monkeypatch

        def _fake_subprocess_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)  # pass git_diff_files calls through
            _captured_argv.append(list(argv))
            assert "timeout" in kwargs and kwargs["timeout"] is not None, (
                "subprocess.run must be called with a finite timeout="
            )
            return subprocess.CompletedProcess(
                args=argv, returncode=1,
                stdout=canned_stdout, stderr=canned_stderr,
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_subprocess_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None, (
            "_verify_green_typecheck not found — not yet implemented (expected RED fail)"
        )

        ctx = _make_ctx(str(repo), build_class="SIMPLE")
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1, build_class="SIMPLE")

        result = fn(ctx, prev)

        assert result.error_code == "E_GREEN_TYPECHECK_RETRY", (
            f"Expected error_code='E_GREEN_TYPECHECK_RETRY'; got {result.error_code!r}"
        )
        assert result.recoverable is True, (
            f"Expected recoverable=True (cycle 1, cap 2 for SIMPLE); got {result.recoverable!r}"
        )
        assert isinstance(result.data, dict), "result.data must be a dict"
        findings = result.data.get("green_typecheck_findings") or []
        assert len(findings) > 0, (
            f"green_typecheck_findings must be non-empty on type error; got {findings!r}"
        )


# ─── AC8: status=ok with empty typecheck_findings when files are type-clean ───


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC8OkOnCleanFiles:
    """AC8 — _verify_green_typecheck returns status='ok' with empty typecheck_findings
    when changed files are type-clean (canned 'Success' stdout)."""

    def test_ac8_ok_on_clean_files(self, tmp_path: Path, monkeypatch) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        changed = repo / "clean_module.py"
        changed.write_text("x: int = 1\n")
        subprocess.run(["git", "add", "clean_module.py"], cwd=repo, check=True)

        canned_stdout = "Success: no issues found in 1 source file\n"

        def _fake_subprocess_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout=canned_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_subprocess_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None, (
            "_verify_green_typecheck not found — not yet implemented (expected RED fail)"
        )

        ctx = _make_ctx(str(repo), build_class="SIMPLE")
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        result = fn(ctx, prev)

        assert result.status == "ok", (
            f"Expected status='ok' on clean files; got {result.status!r}"
        )
        assert isinstance(result.data, dict), "result.data must be a dict"
        findings = result.data.get("typecheck_findings")
        assert findings == [], (
            f"typecheck_findings must be [] on clean output; got {findings!r}"
        )


# ─── AC9: diff-scoping — only changed files passed to mypy ───────────────────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC9DiffScopingChangedFilesOnly:
    """AC9 — mypy argv target list contains only files changed since red_sha,
    excluding dirty-but-unchanged legacy files."""

    def test_ac9_only_changed_files_in_argv(self, tmp_path: Path, monkeypatch) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        # "changed" file: new file added after red_sha (GREEN change)
        changed = repo / "new_module.py"
        changed.write_text("x: int = 1\n")
        subprocess.run(["git", "add", "new_module.py"], cwd=repo, check=True)

        # "legacy dirty" file: exists since baseline, NOT touched since red_sha
        # (placeholder.py was committed before red_sha — already staged, no new changes)
        # We do NOT change placeholder.py here — it's a pre-existing file.

        canned_stdout = "Success: no issues found in 1 source file\n"
        captured_argv: list[list[str]] = []
        _real_run = subprocess.run  # capture before monkeypatch

        def _fake_subprocess_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)  # pass git_diff_files calls through
            captured_argv.append(list(argv))
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout=canned_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_subprocess_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None, (
            "_verify_green_typecheck not found — not yet implemented (expected RED fail)"
        )

        ctx = _make_ctx(str(repo), build_class="SIMPLE")
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        fn(ctx, prev)

        # At least one subprocess.run call should have been made
        assert len(captured_argv) >= 1, (
            "subprocess.run should have been called for mypy; was not called"
        )

        mypy_call = captured_argv[-1]
        # The changed new_module.py should appear in the argv (resolved absolute path)
        changed_abs = str(changed.resolve())
        assert any(changed_abs in arg or "new_module.py" in arg for arg in mypy_call), (
            f"changed new_module.py must appear in mypy argv; argv={mypy_call!r}"
        )
        # placeholder.py is NOT changed since red_sha — must NOT appear
        assert not any("placeholder.py" in arg for arg in mypy_call), (
            f"placeholder.py (unchanged since red_sha) must NOT appear in mypy argv; "
            f"argv={mypy_call!r}"
        )


# ─── AC10: terminal error at cycle >= cap (SIMPLE cap=2) ─────────────────────


class TestAC10TerminalAtCap:
    """AC10 — At cycle >= cap the gate returns terminal error_code='E_GREEN_TYPECHECK_FAIL_CAP2',
    recoverable=False.  Drives gated_step_result with SIMPLE gate at cycle=2."""

    def test_ac10_terminal_at_cap_simple(self) -> None:
        """Drive gated_step_result directly; green_typecheck must be registered for cap=2."""
        from bytedigger_engine.lib.recoverable_gate import RecoverableGateMixin  # noqa: E402 (inside body)

        # If green_typecheck not registered, resolve_policy returns _DEFAULT_POLICY (cap=1)
        # which makes cycle=2 still look like cap — but the error_code would be wrong.
        # So we assert the terminal_error_code is the specific one for this gate.
        # GH625: cap decision now keyed on per-gate gate_attempts, not raw
        # cycle. Seed 2 prior attempts (== SIMPLE cap) so this stays a cap case.
        result = RecoverableGateMixin.gated_step_result(
            build_class="SIMPLE",
            gate="green_typecheck",
            cycle=2,
            retry_from_step_idx=1,
            error_code="E_GREEN_TYPECHECK_RETRY",
            error_msg="typecheck failed",
            step_name="verify_green_typecheck",
            forwarded_data={
                "green_typecheck_findings": [{"check_id": "assignment"}],
                "gate_attempts": {"green_typecheck": 2},
            },
            terminal_error_code="E_GREEN_TYPECHECK_FAIL_CAP2",
        )
        assert result.recoverable is False, (
            f"At cycle=2 (cap for SIMPLE), recoverable must be False; got {result.recoverable!r}"
        )
        assert result.error_code == "E_GREEN_TYPECHECK_FAIL_CAP2", (
            f"terminal error_code must be 'E_GREEN_TYPECHECK_FAIL_CAP2'; got {result.error_code!r}"
        )
        assert result.status == "error", (
            f"terminal status must be 'error'; got {result.status!r}"
        )

    def test_ac10_feature_terminal_at_cycle_1(self) -> None:
        """FEATURE/COMPLEX cap=1 → cycle=1 is terminal."""
        from bytedigger_engine.lib.recoverable_gate import RecoverableGateMixin  # noqa: E402

        # GH625: cap decision now keyed on per-gate gate_attempts (FEATURE cap=1).
        result = RecoverableGateMixin.gated_step_result(
            build_class="FEATURE",
            gate="green_typecheck",
            cycle=1,
            retry_from_step_idx=1,
            error_code="E_GREEN_TYPECHECK_RETRY",
            error_msg="typecheck failed",
            step_name="verify_green_typecheck",
            forwarded_data={
                "green_typecheck_findings": [],
                "gate_attempts": {"green_typecheck": 1},
            },
            terminal_error_code="E_GREEN_TYPECHECK_FAIL_CAP2",
        )
        assert result.recoverable is False, (
            f"FEATURE at cycle=1 (cap=1): recoverable must be False; got {result.recoverable!r}"
        )


# ─── AC10b: policy explicitly registered (not falling to _DEFAULT_POLICY) ─────


class TestAC10bPolicyRegistered:
    """AC10b — green_typecheck policy registered for all 3 build classes,
    NOT falling through to _DEFAULT_POLICY (cap=1 for all)."""

    def test_ac10b_simple_cap_is_2(self) -> None:
        """resolve_policy('SIMPLE','green_typecheck').cycle_cap == 2."""
        pol = _recoverable_policy.resolve_policy("SIMPLE", "green_typecheck")
        assert pol.cycle_cap == 2, (
            f"SIMPLE green_typecheck cap must be 2 (recoverable_twice); "
            f"got {pol.cycle_cap!r}. Falls to _DEFAULT_POLICY (cap=1) today — gate not registered."
        )

    def test_ac10b_feature_cap_is_1(self) -> None:
        """resolve_policy('FEATURE','green_typecheck').cycle_cap == 1."""
        pol = _recoverable_policy.resolve_policy("FEATURE", "green_typecheck")
        assert pol.cycle_cap == 1, (
            f"FEATURE green_typecheck cap must be 1 (recoverable_once); got {pol.cycle_cap!r}"
        )
        # Also assert slot matches parity with green_lint
        assert pol.slot == "recoverable_once", (
            f"FEATURE slot should be 'recoverable_once'; got {pol.slot!r}"
        )

    def test_ac10b_complex_cap_is_1(self) -> None:
        """resolve_policy('COMPLEX','green_typecheck').cycle_cap == 1."""
        pol = _recoverable_policy.resolve_policy("COMPLEX", "green_typecheck")
        assert pol.cycle_cap == 1, (
            f"COMPLEX green_typecheck cap must be 1 (recoverable_once); got {pol.cycle_cap!r}"
        )

    def test_ac10b_simple_not_default_policy(self) -> None:
        """SIMPLE must NOT fall to _DEFAULT_POLICY (which has cap=1 for all unregistered gates)."""
        default_cap = _recoverable_policy._DEFAULT_POLICY.cycle_cap  # 1
        pol = _recoverable_policy.resolve_policy("SIMPLE", "green_typecheck")
        # _DEFAULT_POLICY.cycle_cap == 1; SIMPLE green_typecheck must be 2
        assert pol.cycle_cap != default_cap or pol.slot != _recoverable_policy._DEFAULT_POLICY.slot, (
            f"SIMPLE green_typecheck must NOT fall to _DEFAULT_POLICY (cap={default_cap}); "
            f"got pol={pol!r}. Registration missing."
        )


# ─── AC11: TimeoutExpired → E_GREEN_TYPECHECK_TIMEOUT, recoverable=False ──────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC11TimeoutExpired:
    """AC11 — subprocess.TimeoutExpired → error_code='E_GREEN_TYPECHECK_TIMEOUT',
    recoverable=False; subprocess called with finite timeout=."""

    def test_ac11_timeout_expired_gives_timeout_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        changed = repo / "changed.py"
        changed.write_text("x: int = 1\n")
        subprocess.run(["git", "add", "changed.py"], cwd=repo, check=True)

        _real_run = subprocess.run  # capture before monkeypatch

        def _raise_timeout(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)  # pass git_diff_files calls through
            # Verify finite timeout was passed (binding to AC11 forcing-fn)
            assert "timeout" in kwargs and kwargs["timeout"] is not None, (
                "subprocess.run must pass a finite timeout= kwarg"
            )
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _raise_timeout)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None, (
            "_verify_green_typecheck not found — not yet implemented (expected RED fail)"
        )

        ctx = _make_ctx(str(repo))
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        result = fn(ctx, prev)

        assert result.error_code == "E_GREEN_TYPECHECK_TIMEOUT", (
            f"Expected 'E_GREEN_TYPECHECK_TIMEOUT'; got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"TimeoutExpired path must be non-recoverable; got {result.recoverable!r}"
        )
        assert result.status == "error", (
            f"TimeoutExpired must set status='error'; got {result.status!r}"
        )


# ─── AC12: _build_green_prompt injects GREEN_TYPECHECK_VIOLATIONS on cycle>=2 ─


class TestAC12GreenPromptViolationsBlock:
    """AC12 — _build_green_prompt injects ## GREEN_TYPECHECK_VIOLATIONS block
    on cycle>=2 with green_typecheck_findings, omits it on cycle=1."""

    def _make_prompt_prev_dict(
        self, cycle: int, findings: list[dict] | None = None
    ) -> dict:
        """Build the dict-shaped prev that _build_green_prompt reads (retry path)."""
        d: dict[str, Any] = {
            "cycle": cycle,
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/red.log",
            "validation_doc_path": "/tmp/validation.md",
            "green_lint_findings": [],
        }
        if findings is not None:
            d["green_typecheck_findings"] = findings
        return d

    def test_ac12_violations_block_present_on_cycle_2(self, monkeypatch) -> None:
        """cycle=2 + green_typecheck_findings → ## GREEN_TYPECHECK_VIOLATIONS present."""
        findings = [{"check_id": "assignment", "severity": "error", "path": "foo.py", "start": 1}]
        prev_dict = self._make_prompt_prev_dict(cycle=2, findings=findings)

        # Patch file-reading helpers to avoid touching the filesystem
        monkeypatch.setattr(phase_5_implement, "_read_first_block", lambda _: "SPEC BLOCK")
        monkeypatch.setattr(phase_5_implement, "_maybe_role_template", lambda _: None)
        monkeypatch.setattr(phase_5_implement, "_resolve_scratchpad",
                            lambda _: Path("/tmp/scratchpad"))
        monkeypatch.setattr(phase_5_implement, "_get_producer_anti_fab_prompt", lambda: "")
        monkeypatch.setattr(phase_5_implement, "_resolve_worktree_root",
                            lambda ctx, sp: Path("/tmp"))
        monkeypatch.setattr(phase_5_implement, "_worktree_edit_boundary_block", lambda _: "")

        ctx = _make_ctx("/tmp")

        result = phase_5_implement._build_green_prompt(ctx, prev_dict)  # type: ignore[arg-type]

        # Result may be StepResult(error) if paths are missing — that's OK, check if
        # we can get the prompt string. Instead call the inner logic via the returned data
        # or check if the function itself checks for GREEN_TYPECHECK_VIOLATIONS presence.
        # Primary path: if it returns an error due to missing files, the block injection
        # is still not present yet → fail is still a RED fail.
        if result.status == "error" and result.error_code in (
            "E_RETRY_FORWARDED_DATA_MISSING", "E_MISSING_PREV_DATA"
        ):
            # Can't reach injection site — function errors before assembling prompt.
            # This is still a RED fail for AC12: the block does not exist.
            pytest.fail(
                "AC12 RED: _build_green_prompt returned early error before prompt assembly; "
                "## GREEN_TYPECHECK_VIOLATIONS block cannot be verified. "
                f"error_code={result.error_code!r}. Not yet implemented."
            )

        # Function returns status="ok", data={"prompt": ..., ...}
        assert isinstance(result.data, dict), (
            f"_build_green_prompt must return data dict; got {type(result.data)!r}"
        )
        prompt = result.data.get("prompt", "")
        assert "## GREEN_TYPECHECK_VIOLATIONS" in prompt, (
            f"## GREEN_TYPECHECK_VIOLATIONS must appear in prompt at cycle>=2 with findings; "
            f"not found in prompt (len={len(prompt)}). Not yet implemented — expected RED fail."
        )

    def test_ac12_violations_block_absent_on_cycle_1(self, monkeypatch) -> None:
        """cycle=1 → ## GREEN_TYPECHECK_VIOLATIONS must NOT appear."""
        prev_dict = self._make_prompt_prev_dict(cycle=1, findings=None)

        monkeypatch.setattr(phase_5_implement, "_read_first_block", lambda _: "SPEC BLOCK")
        monkeypatch.setattr(phase_5_implement, "_maybe_role_template", lambda _: None)
        monkeypatch.setattr(phase_5_implement, "_resolve_scratchpad",
                            lambda _: Path("/tmp/scratchpad"))
        monkeypatch.setattr(phase_5_implement, "_get_producer_anti_fab_prompt", lambda: "")
        monkeypatch.setattr(phase_5_implement, "_resolve_worktree_root",
                            lambda ctx, sp: Path("/tmp"))
        monkeypatch.setattr(phase_5_implement, "_worktree_edit_boundary_block", lambda _: "")

        ctx = _make_ctx("/tmp")

        result = phase_5_implement._build_green_prompt(ctx, prev_dict)  # type: ignore[arg-type]

        if result.status == "error":
            # Error before prompt assembly: block definitely not present → absence confirmed
            return

        assert isinstance(result.data, dict), (
            f"_build_green_prompt must return data dict; got {type(result.data)!r}"
        )
        prompt = result.data.get("prompt", "")
        assert "## GREEN_TYPECHECK_VIOLATIONS" not in prompt, (
            f"## GREEN_TYPECHECK_VIOLATIONS must NOT appear at cycle=1; found in prompt"
        )


# ─── AC13: verify_green_typecheck registered between verify_green_passing
#           and commit_green_code ─────────────────────────────────────────────


class TestAC13StepRegistration:
    """AC13 — verify_green_typecheck is registered in the phase_5 workflow step list
    between verify_green_passing and commit_green_code."""

    def _get_step_names(self) -> list[str]:
        """Introspect the phase_5 workflow definition's step name list."""
        # Canonical accessor (mirroring test_phase_5_step3_verify_green.py:296 + 6 siblings):
        from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415
        wf = phase_5_implement_workflow()

        if wf is None:
            pytest.fail(
                "AC13 RED: phase_5_implement_workflow() returned None; cannot introspect steps."
            )

        steps = getattr(wf, "steps", None)
        if steps is None:
            pytest.fail(
                f"AC13 RED: workflow definition has no .steps attribute; wf type={type(wf)!r}"
            )
        return [getattr(s, "name", None) or s for s in steps]

    def test_ac13_verify_green_typecheck_registered(self) -> None:
        """verify_green_typecheck must appear in the step list."""
        names = self._get_step_names()
        assert "verify_green_typecheck" in names, (
            f"verify_green_typecheck not in step list; steps={names!r}. "
            "Not yet registered — expected RED fail."
        )

    def test_ac13_ordering_between_passing_and_commit(self) -> None:
        """verify_green_typecheck must be between verify_green_passing and commit_green_code."""
        names = self._get_step_names()
        assert "verify_green_typecheck" in names, (
            f"verify_green_typecheck not in step list; steps={names!r}"
        )
        idx_tc = names.index("verify_green_typecheck")
        assert "verify_green_passing" in names, (
            f"verify_green_passing not in step list; steps={names!r}"
        )
        idx_passing = names.index("verify_green_passing")
        assert "commit_green_code" in names, (
            f"commit_green_code not in step list; steps={names!r}"
        )
        idx_commit = names.index("commit_green_code")
        assert idx_passing < idx_tc < idx_commit, (
            f"verify_green_typecheck (idx={idx_tc}) must be between "
            f"verify_green_passing (idx={idx_passing}) and commit_green_code (idx={idx_commit}); "
            f"steps={names!r}"
        )
