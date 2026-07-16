"""RED tests for AD14A3ED L1 — spec-scope allowlist + E_RED_SCOPE_VIOLATION gate.

Contract: _commit_red_tests must parse a ## Files section from the spec,
compare derived git paths against the allowlist, and return
E_RED_SCOPE_VIOLATION (enforce=True) or emit red_scope_check telemetry
(enforce=False) when out-of-scope paths are detected.

All tests MUST FAIL until GREEN implements _parse_spec_files_allowlist,
_path_matches_allowlist, and the scope gate in _commit_red_tests.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

# The last two imports do not exist yet — ImportError is the expected RED failure.
from phase_5_implement import (  # noqa: E402
    _commit_red_tests,
    _parse_spec_files_allowlist,
    _path_matches_allowlist,
)
from contracts import WorkflowContext  # noqa: E402


# ─── repo helpers (copied from test_phase_5_step1_disk_truth.py) ──────────────


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


# ═══════════════════════════════════════════════════════════════════════════════
# TestParseSpecFilesAllowlist — AC1, AC2, AC3, AC4
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseSpecFilesAllowlist:
    """Unit tests for _parse_spec_files_allowlist helper."""

    def test_parse_returns_none_when_spec_missing(self) -> None:
        """AC1: non-existent path → None."""
        result = _parse_spec_files_allowlist("/nonexistent/path.md")
        assert result is None

    def test_parse_returns_none_when_no_files_section(self, tmp_path: Path) -> None:
        """AC2: spec with no ## Files heading → None."""
        spec = tmp_path / "spec.md"
        spec.write_text("# Title\n\nBody only, no Files section.\n")
        result = _parse_spec_files_allowlist(str(spec))
        assert result is None

    def test_parse_extracts_exact_list(self, tmp_path: Path) -> None:
        """AC3: spec with ## Files section → exact ordered list."""
        spec = tmp_path / "spec.md"
        spec.write_text(
            "# Title\n\n## Files\n- tests/test_a.py\n- tests/test_b.py\n\n## Next Section\n"
        )
        result = _parse_spec_files_allowlist(str(spec))
        assert result == ["tests/test_a.py", "tests/test_b.py"]

    def test_parse_strips_create_modify_verbs(self, tmp_path: Path) -> None:
        """AC4: verb-prefixed entries → bare paths returned."""
        spec = tmp_path / "spec.md"
        spec.write_text(
            "# Title\n\n## Files\n- CREATE tests/foo.py\n- MODIFY src/bar.py\n\n## Done\n"
        )
        result = _parse_spec_files_allowlist(str(spec))
        assert result == ["tests/foo.py", "src/bar.py"]


# ═══════════════════════════════════════════════════════════════════════════════
# TestPathMatchesAllowlist — AC5, AC6
# ═══════════════════════════════════════════════════════════════════════════════


class TestPathMatchesAllowlist:
    """Unit tests for _path_matches_allowlist helper."""

    def test_path_matches_exact(self) -> None:
        """AC5: exact string match True; different path False."""
        assert _path_matches_allowlist("tests/test_a.py", ["tests/test_a.py"]) is True
        assert _path_matches_allowlist("tests/test_x.py", ["tests/test_a.py"]) is False

    def test_path_matches_glob(self) -> None:
        """AC6: fnmatch glob pattern → True when glob matches."""
        assert (
            _path_matches_allowlist(
                "tests/test_phase_5_foo.py", ["tests/test_phase_5_*.py"]
            )
            is True
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestCommitRedTestsScopeGate — AC7 through AC12
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommitRedTestsScopeGate:
    """Integration tests for the scope gate inside _commit_red_tests.

    GH569 spec-cited edits (AC11): the out-of-scope fixture path is
    `other/tests/sneaky.py` — test-shaped (so git enumeration still surfaces
    it) but outside the ≤2-level derivation reach of the `tests/wanted.py`
    entry; the old `tests/sneaky.py` is now legitimately admitted via the
    derived test-scope rule. `red_scope_check` also gained
    `authorized_test_edits_n` / `derived_matched_n` fields.
    """

    def _make_spec_with_files_section(self, path: Path, wanted: list[str]) -> None:
        """Write a spec file with a ## Files section listing the given paths."""
        lines = ["# Spec\n\n## Files\n"]
        for p in wanted:
            lines.append(f"- {p}\n")
        lines.append("\n## End\n")
        path.write_text("".join(lines))

    def test_commit_red_tests_violates_enforce_true_returns_e_red_scope_violation(
        self, tmp_path: Path
    ) -> None:
        """AC7: out-of-scope file + enforce=True → E_RED_SCOPE_VIOLATION."""
        repo = _make_repo(tmp_path)
        spec_file = tmp_path / "spec.md"
        self._make_spec_with_files_section(spec_file, ["tests/wanted.py"])

        _add_untracked(repo, "tests/wanted.py", "def test_ok(): assert True\n")
        _add_untracked(repo, "other/tests/sneaky.py", "def test_bad(): assert False\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(
            scratchpad,
            str(repo),
            enforce_red_scope_allowlist=True,
        )
        prev = _make_prev(
            red_test_paths="<missing>",
            spec_path=str(spec_file),
        )

        result = _commit_red_tests(ctx, prev)

        assert result.status == "error"
        assert result.error_code == "E_RED_SCOPE_VIOLATION"
        assert "other/tests/sneaky.py" in result.error

    def test_commit_red_tests_violates_enforce_false_pass_through_with_telemetry(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC8: out-of-scope + enforce=False → status ok + red_scope_check event violations_n=1."""
        import phase_5_implement

        repo = _make_repo(tmp_path)
        spec_file = tmp_path / "spec.md"
        self._make_spec_with_files_section(spec_file, ["tests/wanted.py"])

        _add_untracked(repo, "tests/wanted.py", "def test_ok(): assert True\n")
        _add_untracked(repo, "other/tests/sneaky.py", "def test_bad(): assert False\n")

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(
            scratchpad,
            str(repo),
            enforce_red_scope_allowlist=False,
        )
        prev = _make_prev(
            red_test_paths="<missing>",
            spec_path=str(spec_file),
        )

        result = _commit_red_tests(ctx, prev)

        assert result.status == "ok"

        scope_events = [(n, p) for (n, p) in captured if n == "red_scope_check"]
        assert len(scope_events) == 1
        payload = scope_events[0][1]
        assert payload["violations_n"] == 1
        assert payload["enforced"] is False

    def test_no_files_section_with_enforce_true_pass_through(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC9: spec lacking ## Files + enforce=True → status ok; no_allowlist_found=True."""
        import phase_5_implement

        repo = _make_repo(tmp_path)
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Spec\n\nNo files section here.\n")

        _add_untracked(repo, "tests/anything.py", "def test_x(): assert True\n")

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(
            scratchpad,
            str(repo),
            enforce_red_scope_allowlist=True,
        )
        prev = _make_prev(
            red_test_paths="<missing>",
            spec_path=str(spec_file),
        )

        result = _commit_red_tests(ctx, prev)

        assert result.status == "ok"

        scope_events = [(n, p) for (n, p) in captured if n == "red_scope_check"]
        assert len(scope_events) == 1
        payload = scope_events[0][1]
        assert payload.get("no_allowlist_found") is True
        assert payload["allowlist_size"] == 0

    def test_scope_violation_fails_fast_before_git_subprocess(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC10: violation + enforce=True → no git add/commit subprocess called."""
        import phase_5_implement
        import subprocess as _subp

        repo = _make_repo(tmp_path)
        spec_file = tmp_path / "spec.md"
        self._make_spec_with_files_section(spec_file, ["tests/wanted.py"])

        _add_untracked(repo, "tests/wanted.py", "def test_ok(): assert True\n")
        _add_untracked(repo, "other/tests/sneaky.py", "def test_bad(): assert False\n")

        real_run = _subp.run
        recorded_argvs: list[list[str]] = []

        def recording_run(argv, *args, **kwargs):
            recorded_argvs.append(
                list(argv) if not isinstance(argv, str) else [argv]
            )
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(phase_5_implement.subprocess, "run", recording_run)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(
            scratchpad,
            str(repo),
            enforce_red_scope_allowlist=True,
        )
        prev = _make_prev(
            red_test_paths="<missing>",
            spec_path=str(spec_file),
        )

        _commit_red_tests(ctx, prev)

        assert not any(
            a[:2] == ["git", "add"] for a in recorded_argvs
        ), f"unexpected git add call; recorded: {recorded_argvs}"
        assert not any(
            a[:2] == ["git", "commit"] for a in recorded_argvs
        ), f"unexpected git commit call; recorded: {recorded_argvs}"

    def test_e_red_no_marker_fires_before_scope_check(
        self, tmp_path: Path
    ) -> None:
        """AC11: empty git → E_RED_NO_MARKER not E_RED_SCOPE_VIOLATION (§1n ordering)."""
        repo = _make_repo(tmp_path)
        # No untracked test files — git_paths will be empty → E_RED_NO_MARKER fires
        spec_file = tmp_path / "spec.md"
        self._make_spec_with_files_section(spec_file, ["tests/foo.py"])

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(
            scratchpad,
            str(repo),
            enforce_red_scope_allowlist=True,
        )
        prev = _make_prev(
            red_test_paths="<missing>",
            spec_path=str(spec_file),
        )

        result = _commit_red_tests(ctx, prev)

        assert result.error_code == "E_RED_NO_MARKER", (
            f"expected E_RED_NO_MARKER but got {result.error_code!r}; "
            "scope gate must not run before the no-marker guard"
        )

    def test_red_scope_check_payload_schema(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC12: red_scope_check event has exact key set; extra no_allowlist_found when absent."""
        import phase_5_implement

        # ── scenario A: violation, enforce=False (from AC8) ──────────────────
        repo_a = _make_repo(tmp_path / "repo_a")
        spec_a = tmp_path / "spec_a.md"
        self._make_spec_with_files_section(spec_a, ["tests/wanted.py"])
        _add_untracked(repo_a, "tests/wanted.py", "def test_ok(): assert True\n")
        _add_untracked(repo_a, "other/tests/sneaky.py", "def test_bad(): assert False\n")

        captured_a: list[tuple[str, dict]] = []

        def fake_emit_a(event_name, payload, severity=None):
            captured_a.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit_a)

        scratch_a = tmp_path / "scratch_a"
        scratch_a.mkdir()
        ctx_a = _make_ctx(
            scratch_a,
            str(repo_a),
            enforce_red_scope_allowlist=False,
        )
        prev_a = _make_prev(red_test_paths="<missing>", spec_path=str(spec_a))
        _commit_red_tests(ctx_a, prev_a)

        scope_a = [p for (n, p) in captured_a if n == "red_scope_check"]
        assert len(scope_a) == 1
        payload_a = scope_a[0]
        expected_base_keys = {
            "phase",
            "step",
            "allowlist_size",
            "red_paths_n",
            "violations_n",
            "enforced",
        }
        # GH569 spec §Gate change: two new fields on the allowlist-present path
        expected_base_keys |= {"authorized_test_edits_n", "derived_matched_n"}
        assert set(payload_a.keys()) == expected_base_keys, (
            f"AC12 scenario A: key mismatch. Got {set(payload_a.keys())}"
        )
        assert payload_a["phase"] == 5
        assert payload_a["step"] == "commit_red_tests"

        # ── scenario B: no allowlist (AC9-like), enforce=True ─────────────────
        repo_b = _make_repo(tmp_path / "repo_b")
        spec_b = tmp_path / "spec_b.md"
        spec_b.write_text("# Spec\n\nNo files section here.\n")
        _add_untracked(repo_b, "tests/anything.py", "def test_x(): assert True\n")

        captured_b: list[tuple[str, dict]] = []

        def fake_emit_b(event_name, payload, severity=None):
            captured_b.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit_b)

        scratch_b = tmp_path / "scratch_b"
        scratch_b.mkdir()
        ctx_b = _make_ctx(
            scratch_b,
            str(repo_b),
            enforce_red_scope_allowlist=True,
        )
        prev_b = _make_prev(red_test_paths="<missing>", spec_path=str(spec_b))
        _commit_red_tests(ctx_b, prev_b)

        scope_b = [p for (n, p) in captured_b if n == "red_scope_check"]
        assert len(scope_b) == 1
        payload_b = scope_b[0]
        # no-allowlist branch is unchanged by GH569 — no new counter fields
        expected_extended_keys = (
            expected_base_keys
            - {"authorized_test_edits_n", "derived_matched_n"}
        ) | {"no_allowlist_found"}
        assert set(payload_b.keys()) == expected_extended_keys, (
            f"AC12 scenario B: key mismatch. Got {set(payload_b.keys())}"
        )
        assert payload_b["no_allowlist_found"] is True
