"""RED tests for GH282 — `# red-mass-deletion: allow` pragma-escape.

Contract (per spec 2026-07-13_GH282_red_mass_deletion_pragma_escape_spec.md §2):
  - new named helper `_has_mass_deletion_allow_pragma(abs_path) -> bool` on
    `phase_5_implement`, fail-CLOSED (returns False on any read error).
  - `_commit_red_tests`'s existing mass-deletion gate partitions violations
    into `exempted` (pragma'd) vs `violations` (not pragma'd); the
    `red_mass_deletion_check` telemetry event gains `exempted` / `exempted_n`
    keys, and `violations_n` only counts non-exempted entries.
  - Negative guard: a violation WITHOUT the pragma must still hard-block
    exactly as before — the escape must not weaken the gate.

Per §1q-ext (D1CF5FDF): `_has_mass_deletion_allow_pragma` does not exist yet
at collect time, so it is resolved via getattr() presence-gate INSIDE each
test body (never imported at module top level) so this file COLLECTS
cleanly today and only FAILS at assertion time.

No module-level `sys.path` manipulation here (81F97F3D / §1q) — this
directory's `conftest.py` already registers the engine_py root, `lib/`, and
`workflows/` dirs on `sys.path` at conftest-import time (singleton pattern),
so plain top-level imports of already-shipped symbols resolve fine.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


def _get_pragma_fn():
    """§1q-ext presence gate: resolve the not-yet-existing helper at call
    time so the module COLLECTS cleanly before GREEN. Fails the calling
    assertion (not collection) when the helper is absent."""
    from bytedigger_engine.workflows import phase_5_implement

    fn = getattr(phase_5_implement, "_has_mass_deletion_allow_pragma", None)
    if fn is None:
        pytest.fail(
            "_has_mass_deletion_allow_pragma does not exist yet on "
            "phase_5_implement (GH282 pragma-escape GREEN not shipped)"
        )
    return fn


# ─── repo helpers (mirrored from test_gh282_red_mass_deletion.py) ──────────


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
# Direct helper tests — AC A, B, E
# ═══════════════════════════════════════════════════════════════════════════


class TestHasMassDeletionAllowPragma:
    def test_returns_true_when_file_contains_literal_pragma_token(
        self, tmp_path: Path
    ) -> None:
        """AC A: file content contains the literal `# red-mass-deletion: allow`
        token -> True."""
        fn = _get_pragma_fn()
        p = tmp_path / "wanted.py"
        p.write_text("def test_x(): pass\n# red-mass-deletion: allow\n")

        assert fn(str(p)) is True

    def test_returns_false_when_pragma_token_absent(self, tmp_path: Path) -> None:
        """AC A (converse): ordinary file content, no pragma token -> False."""
        fn = _get_pragma_fn()
        p = tmp_path / "wanted.py"
        p.write_text("def test_x(): pass\n")

        assert fn(str(p)) is False

    def test_returns_false_for_missing_path_fail_closed(self, tmp_path: Path) -> None:
        """AC B: nonexistent path -> False (fail-closed, not an exception)."""
        fn = _get_pragma_fn()
        missing = tmp_path / "does" / "not" / "exist.py"

        assert fn(str(missing)) is False

    def test_returns_false_for_unreadable_directory_path(self, tmp_path: Path) -> None:
        """AC B: passing a directory (unreadable as a file) -> False (fail-closed)."""
        fn = _get_pragma_fn()
        d = tmp_path / "a_directory"
        d.mkdir()

        assert fn(str(d)) is False

    def test_idempotent_across_successive_calls(self, tmp_path: Path) -> None:
        """AC E: two successive calls on the same file return the identical
        bool (stateless — safe on intra-phase retry/resume/replay)."""
        fn = _get_pragma_fn()
        p = tmp_path / "wanted.py"
        p.write_text("# red-mass-deletion: allow\n")

        first = fn(str(p))
        second = fn(str(p))

        assert first == second == True  # noqa: E712


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests — AC C, D, F
# ═══════════════════════════════════════════════════════════════════════════


class TestCommitRedTestsPragmaEscape:
    def _setup_mass_deletion_repo(self, tmp_path: Path, pragma: bool):
        """Committed 'tests/wanted.py' at 3100 lines, mass-deleted to 693 lines
        (deleted=2407 >= default max 120 -> violation). Optionally leaves the
        pragma token in the post-deletion file content."""
        repo = _make_repo(tmp_path)
        _commit_file(repo, "tests/wanted.py", _lines(3100), "base")
        content = _lines(693)
        if pragma:
            content += "# red-mass-deletion: allow\n"
        (repo / "tests" / "wanted.py").write_text(content)

        spec_file = tmp_path / "spec.md"
        _make_spec_with_files_section(spec_file, ["tests/wanted.py"])
        return repo, spec_file

    def test_enforced_pragma_present_does_not_block_and_reports_exempted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC C: enforce ON + over-threshold deletion + pragma present ->
        _commit_red_tests does NOT return E_RED_MASS_DELETION; emitted
        red_mass_deletion_check has exempted_n==1, violations_n==0."""
        from bytedigger_engine.workflows import phase_5_implement

        repo, spec_file = self._setup_mass_deletion_repo(tmp_path, pragma=True)

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

        assert result.error_code != "E_RED_MASS_DELETION"

        events = [(n, p) for (n, p) in captured if n == "red_mass_deletion_check"]
        assert len(events) == 1
        assert events[0][1]["exempted_n"] == 1
        assert events[0][1]["violations_n"] == 0

    def test_enforced_no_pragma_still_blocks_negative_guard(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC D (negative-guard): same over-threshold deletion, NO pragma ->
        still returns E_RED_MASS_DELETION, recoverable=False. Proves the
        escape did not weaken the gate for non-pragma'd deletions."""
        from bytedigger_engine.workflows import phase_5_implement

        repo, spec_file = self._setup_mass_deletion_repo(tmp_path, pragma=False)

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

    def test_check_event_payload_contains_exempted_keys(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC F: red_mass_deletion_check payload contains the keys `exempted`
        and `exempted_n` (telemetry-shape, consumed by TW-GH282-CHECK)."""
        from bytedigger_engine.workflows import phase_5_implement

        repo, spec_file = self._setup_mass_deletion_repo(tmp_path, pragma=True)

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        _commit_red_tests(ctx, prev)

        events = [(n, p) for (n, p) in captured if n == "red_mass_deletion_check"]
        assert len(events) == 1
        payload = events[0][1]
        assert "exempted" in payload
        assert "exempted_n" in payload
