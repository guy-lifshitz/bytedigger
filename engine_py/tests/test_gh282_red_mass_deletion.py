"""RED tests for GH282 — RED mass-deletion guard in `_commit_red_tests`.

Contract: `_red_mass_deletion_violations(red_paths, base_sha, git_cwd, max_deleted_lines)`
must detect files whose deleted-line count crosses BOTH an absolute floor
(max_deleted_lines) AND a >=50% base-file ratio, and `_commit_red_tests` must
gate on it (kill-switch HAL_RED_MASS_DELETION_GATE, warn-only default,
hard-block via HAL_RED_MASS_DELETION_ENFORCE, override via
HAL_RED_MASS_DELETION_MAX_LINES).

Per spec 2026-07-10_GH282_red_mass_deletion_guard_spec.md and §1q-ext
(D1CF5FDF): `_red_mass_deletion_violations` does not exist yet at collect
time, so it is resolved via getattr() presence-gate INSIDE each test body
(never imported at module top level) so this file COLLECTS cleanly today
and only FAILS at assertion time.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

# These already exist — safe to import at module top level.
from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


def _get_violations_fn():
    """§1q-ext presence gate: resolve the not-yet-existing helper at call
    time so the module COLLECTS cleanly before GREEN. Fails the calling
    assertion (not collection) when the helper is absent."""
    from bytedigger_engine.workflows import phase_5_implement

    fn = getattr(phase_5_implement, "_red_mass_deletion_violations", None)
    if fn is None:
        pytest.fail(
            "_red_mass_deletion_violations does not exist yet on "
            "phase_5_implement (GH282 GREEN not shipped)"
        )
    return fn


# ─── repo helpers (copied from test_phase_5_AD14A3ED_red_scope.py) ────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one committed file so HEAD is valid."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _add_untracked(repo: Path, relpath: str, body: str = "# test\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_ctx(scratchpad: Path, git_cwd: str, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths, cycle: int = 1, spec_path: str | None = None):
    """Minimal prev StepResult-like object with data dict."""
    prev = MagicMock()
    prev.data = {
        "red_test_paths": red_test_paths,
        "cycle": cycle,
    }
    if spec_path is not None:
        prev.data["spec_path"] = spec_path
    return prev


def _make_spec_with_files_section(path: Path, wanted: list[str]) -> None:
    lines = ["# Spec\n\n## Files\n"]
    for p in wanted:
        lines.append(f"- {p}\n")
    lines.append("\n## End\n")
    path.write_text("".join(lines))


def _lines(n: int, prefix: str = "L") -> str:
    return "".join(f"{prefix}{i}\n" for i in range(n))


def _head_sha(repo: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


ENV_TOKENS = (
    "HAL_RED_MASS_DELETION_GATE",
    "HAL_RED_MASS_DELETION_ENFORCE",
    "HAL_RED_MASS_DELETION_MAX_LINES",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure ambient env can't leak into any test (spec §4 env isolation)."""
    for tok in ENV_TOKENS:
        monkeypatch.delenv(tok, raising=False)


# ═══════════════════════════════════════════════════════════════════════════
# Direct helper tests — AC1 through AC5, AC12
# ═══════════════════════════════════════════════════════════════════════════


class TestRedMassDeletionViolationsHelper:
    def test_helper_exists_and_is_callable(self) -> None:
        """AC1: _red_mass_deletion_violations exists module-level, importable."""
        fn = _get_violations_fn()
        assert callable(fn)

    def test_mass_deletion_over_threshold_flags_violation(self, tmp_path: Path) -> None:
        """AC2: 2407 of 3100 lines deleted, max=120 -> 1 violation dict."""
        fn = _get_violations_fn()
        repo = _make_repo(tmp_path)
        _commit_file(repo, "tests/wanted.py", _lines(3100), "base")
        base_sha = _head_sha(repo)
        (repo / "tests" / "wanted.py").write_text(_lines(693))  # deletes 2407 lines

        violations, skip = fn(["tests/wanted.py"], base_sha, str(repo), 120)

        assert skip is None
        assert violations == [{"path": "tests/wanted.py", "deleted": 2407, "base_lines": 3100}]

    def test_below_absolute_floor_no_violation_despite_high_ratio(self, tmp_path: Path) -> None:
        """AC3a: deleted=119 (<120 max) even though ratio is 79% -> no violation."""
        fn = _get_violations_fn()
        repo = _make_repo(tmp_path)
        _commit_file(repo, "tests/wanted.py", _lines(150), "base")
        base_sha = _head_sha(repo)
        (repo / "tests" / "wanted.py").write_text(_lines(31))  # deletes 119 lines

        violations, skip = fn(["tests/wanted.py"], base_sha, str(repo), 120)

        assert skip is None
        assert violations == []

    def test_over_floor_but_below_ratio_no_violation(self, tmp_path: Path) -> None:
        """AC3b: deleted=100 of 1000 base (10%, <50% ratio) -> no violation (AND semantics)."""
        fn = _get_violations_fn()
        repo = _make_repo(tmp_path)
        _commit_file(repo, "tests/wanted.py", _lines(1000), "base")
        base_sha = _head_sha(repo)
        (repo / "tests" / "wanted.py").write_text(_lines(900))  # deletes 100 lines

        violations, skip = fn(["tests/wanted.py"], base_sha, str(repo), 50)

        assert skip is None
        assert violations == []

    def test_new_file_absent_at_base_no_violation(self, tmp_path: Path) -> None:
        """AC4: file that did not exist at base_sha -> skipped, no violation."""
        fn = _get_violations_fn()
        repo = _make_repo(tmp_path)
        base_sha = _head_sha(repo)  # only src/placeholder.py exists at this sha
        _commit_file(repo, "tests/newfile.py", _lines(50), "add new")

        violations, skip = fn(["tests/newfile.py"], base_sha, str(repo), 1)

        assert skip is None
        assert violations == []

    def test_empty_base_sha_returns_no_base_sha_skip(self, tmp_path: Path) -> None:
        """AC5a: base_sha='' -> ([], 'no_base_sha')."""
        fn = _get_violations_fn()
        repo = _make_repo(tmp_path)

        violations, skip = fn(["tests/wanted.py"], "", str(repo), 120)

        assert violations == []
        assert skip == "no_base_sha"

    def test_bad_base_sha_returns_numstat_failed_skip(self, tmp_path: Path) -> None:
        """AC5b: nonexistent base sha -> numstat git_read rc!=0 -> ([], 'numstat_failed')."""
        fn = _get_violations_fn()
        repo = _make_repo(tmp_path)

        bad_sha = "0" * 40
        violations, skip = fn(["tests/wanted.py"], bad_sha, str(repo), 120)

        assert violations == []
        assert skip == "numstat_failed"

    def test_threshold_override_max_lines_suppresses_violation(self, tmp_path: Path) -> None:
        """AC12: explicit max_deleted_lines=2000, deleted=1500 (<2000) -> no violation
        even at 90% ratio."""
        fn = _get_violations_fn()
        repo = _make_repo(tmp_path)
        _commit_file(repo, "tests/wanted.py", _lines(1666), "base")
        base_sha = _head_sha(repo)
        (repo / "tests" / "wanted.py").write_text(_lines(166))  # deletes 1500 lines

        violations, skip = fn(["tests/wanted.py"], base_sha, str(repo), 2000)

        assert skip is None
        assert violations == []


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests — AC6 through AC10
# ═══════════════════════════════════════════════════════════════════════════


class TestCommitRedTestsMassDeletionGate:
    def _setup_mass_deletion_repo(self, tmp_path: Path):
        """Committed 'tests/wanted.py' at 300 lines, mass-deleted to 100 lines
        (deleted=200 >= default max 120, ratio 200*2=400 >= base 300 -> violation)."""
        repo = _make_repo(tmp_path)
        _commit_file(repo, "tests/wanted.py", _lines(300), "base")
        (repo / "tests" / "wanted.py").write_text(_lines(100))  # deletes 200 lines

        spec_file = tmp_path / "spec.md"
        _make_spec_with_files_section(spec_file, ["tests/wanted.py"])
        return repo, spec_file

    def test_warn_only_mass_deletion_commits_and_emits_check_event(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC6: enforce unset (warn-only default) -> status ok, commit created,
        red_mass_deletion_check event violations_n=1 enforced=False."""
        from bytedigger_engine.workflows import phase_5_implement

        repo, spec_file = self._setup_mass_deletion_repo(tmp_path)
        pre_head = _head_sha(repo)

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        result = _commit_red_tests(ctx, prev)

        assert result.status == "ok"
        post_head = _head_sha(repo)
        assert post_head != pre_head, "expected a new commit to have been created"

        events = [(n, p) for (n, p) in captured if n == "red_mass_deletion_check"]
        assert len(events) == 1
        assert events[0][1]["violations_n"] == 1
        assert events[0][1]["enforced"] is False

    def test_enforced_mass_deletion_blocks_with_error_code_and_no_commit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC7: HAL_RED_MASS_DELETION_ENFORCE=1 -> status error,
        error_code E_RED_MASS_DELETION, recoverable False, HEAD unchanged."""
        from bytedigger_engine.workflows import phase_5_implement

        repo, spec_file = self._setup_mass_deletion_repo(tmp_path)
        pre_head = _head_sha(repo)

        monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "1")

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        result = _commit_red_tests(ctx, prev)

        assert result.status == "error"
        assert result.error_code == "E_RED_MASS_DELETION"
        assert result.recoverable is False
        post_head = _head_sha(repo)
        assert post_head == pre_head, "no commit should have been created when blocked"

    def test_enforced_mass_deletion_emits_blocked_sentinel(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC8: enforced violation also emits red_mass_deletion_blocked."""
        from bytedigger_engine.workflows import phase_5_implement

        repo, spec_file = self._setup_mass_deletion_repo(tmp_path)

        monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "1")

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        _commit_red_tests(ctx, prev)

        blocked = [n for (n, p) in captured if n == "red_mass_deletion_blocked"]
        assert len(blocked) == 1

    def test_kill_switch_disables_detection_and_telemetry(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC9: HAL_RED_MASS_DELETION_GATE=0 -> no red_mass_deletion_check event,
        commit proceeds ok even though enforce=1 (gate short-circuits entirely)."""
        from bytedigger_engine.workflows import phase_5_implement

        assert getattr(phase_5_implement, "_red_mass_deletion_violations", None) is not None, (
            "GH282 helper not implemented yet"
        )

        repo, spec_file = self._setup_mass_deletion_repo(tmp_path)

        monkeypatch.setenv("HAL_RED_MASS_DELETION_GATE", "0")
        monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "1")

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        result = _commit_red_tests(ctx, prev)

        assert result.status == "ok"
        events = [n for (n, p) in captured if n == "red_mass_deletion_check"]
        assert events == []

    def test_clean_red_no_deletions_reports_zero_violations(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC10: new test file only, no deletions -> ok, check event violations_n=0."""
        from bytedigger_engine.workflows import phase_5_implement

        repo = _make_repo(tmp_path)
        _add_untracked(repo, "tests/newclean.py", "def test_ok(): assert True\n")

        spec_file = tmp_path / "spec.md"
        _make_spec_with_files_section(spec_file, ["tests/newclean.py"])

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        result = _commit_red_tests(ctx, prev)

        assert result.status == "ok"
        events = [(n, p) for (n, p) in captured if n == "red_mass_deletion_check"]
        assert len(events) == 1
        assert events[0][1]["violations_n"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Flags catalog — AC11
# ═══════════════════════════════════════════════════════════════════════════


class TestFlagsCatalogRegistration:
    def test_all_three_flags_registered_with_correct_kind(self) -> None:
        """AC11: flags_catalog.FLAGS has all 3 HAL_RED_MASS_DELETION_* tokens
        with kind gate/flag/int respectively."""
        from bytedigger_engine import flags_catalog

        expected = {
            "HAL_RED_MASS_DELETION_GATE": "gate",
            "HAL_RED_MASS_DELETION_ENFORCE": "flag",
            "HAL_RED_MASS_DELETION_MAX_LINES": "int",
        }
        for token, kind in expected.items():
            assert token in flags_catalog.FLAGS, f"{token} missing from flags_catalog.FLAGS"
            entry = flags_catalog.FLAGS[token]
            actual_kind = entry.get("kind") if isinstance(entry, dict) else getattr(entry, "kind", None)
            assert actual_kind == kind, f"{token} expected kind={kind!r}, got {actual_kind!r}"
