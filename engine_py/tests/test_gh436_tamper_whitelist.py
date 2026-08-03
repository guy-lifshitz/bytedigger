"""RED tests for GH436 — E_RED_TESTS_TAMPERED spec-§5 authorized-test-edits whitelist.

Spec: SHARED/memory/Decisions/2026-07-10_GH436_tamper_whitelist_spec.md
ACs 1-9 (spec §3).

Conventions (§1q / D1CF5FDF collectability guard): ``parse_authorized_test_edits``
and the ``authorized_test_edits`` kwarg on ``scan_boundary`` do NOT exist yet.
Both existing modules (``lib.run_allowlist``, ``lib.authored_boundary``,
``phase_5_implement``) are imported at module top level (they already exist
today), but the NEW symbol/kwarg is probed via ``getattr``/``inspect.signature``
INSIDE each test body so the file COLLECTS and fails at assert time, never at
collection time.

Stub-passability (§1l/7AD3D393): UUT symbols ``parse_authorized_test_edits``,
``scan_boundary``, ``_commit_green_code`` are called directly, never
patched/mocked. Only ``telemetry_ctx.emit_safe`` is stubbed for telemetry
capture (AC5), matching the sibling test_gh373 pattern.
"""
from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.lib import run_allowlist as run_allowlist_mod  # noqa: E402
from bytedigger_engine.lib import authored_boundary as boundary_mod  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


# ─── shared helpers (copied minimal skeleton from test_gh373_authored_boundary.py) ──


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str) -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _rev_count(repo: Path) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return int(r.stdout.strip())


def _make_green_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Fix the thing", session_id="gh436-green", persona="hal",
        framework=None, domain=None,
    )


def _make_prev(sha_key: str, sha_value: str, cycle: int = 1, **extra) -> MagicMock:
    prev = MagicMock()
    prev.data = {sha_key: sha_value, "cycle": cycle, **extra}
    return prev


def _require_parse_authorized_test_edits():
    """Presence-gate for the not-yet-existing symbol (D1CF5FDF)."""
    fn = getattr(run_allowlist_mod, "parse_authorized_test_edits", None)
    assert fn is not None, (
        "lib.run_allowlist.parse_authorized_test_edits does not exist yet "
        "(GH436 §2.1 not implemented) — expected RED fail"
    )
    return fn


def _require_authorized_test_edits_kwarg():
    """Presence-gate for the new scan_boundary kwarg (D1CF5FDF)."""
    sig = inspect.signature(boundary_mod.scan_boundary)
    assert "authorized_test_edits" in sig.parameters, (
        "scan_boundary is missing the authorized_test_edits kwarg "
        "(GH436 §2.2 not implemented) — expected RED fail"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TestGH436TamperWhitelist
# ═══════════════════════════════════════════════════════════════════════════════


class TestGH436TamperWhitelist:
    """GH436 — spec-§5 authorized-test-edits whitelist (ACs 1-9)."""

    def test_ac1_parse_authorized_test_edits_no_marker_returns_empty(self, tmp_path: Path) -> None:
        """AC1: spec WITHOUT the marker -> []. Expected FAIL pre-GREEN (fn absent)."""
        fn = _require_parse_authorized_test_edits()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text("## Files\n\n- MODIFY `lib/foo.py`\n")
        assert fn(str(spec_path)) == []

    def test_ac2_parse_authorized_test_edits_parses_bullets_until_terminator(
        self, tmp_path: Path
    ) -> None:
        """AC2: marker + bullets -> list of paths; block terminates at first
        non-bullet non-blank line. Expected FAIL pre-GREEN (fn absent)."""
        fn = _require_parse_authorized_test_edits()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text(
            "## Files\n\n- MODIFY `lib/foo.py`\n\n"
            "authorized-test-edits:\n"
            "- `tests/test_x.py`\n"
            "\n"
            "- `tests/test_y.py`\n"
            "## Out of Role\n"
            "- `tests/test_z.py`\n"
        )
        result = fn(str(spec_path))
        assert result == ["tests/test_x.py", "tests/test_y.py"], (
            f"Expected block to include blank-line-separated bullets but stop "
            f"at the ## heading, got {result!r}"
        )

    def test_ac3_parse_authorized_test_edits_fail_closed(self, tmp_path: Path) -> None:
        """AC3: marker present with zero bullets -> []; unreadable path -> [].
        Expected FAIL pre-GREEN (fn absent)."""
        fn = _require_parse_authorized_test_edits()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text("authorized-test-edits:\n\n## Next Section\n- foo\n")
        assert fn(str(spec_path)) == []

        missing_path = tmp_path / "does_not_exist.md"
        assert fn(str(missing_path)) == []

    def test_ac4_scan_boundary_default_none_byte_identical(self, tmp_path: Path) -> None:
        """AC4: scan_boundary(..., authorized_test_edits=None) on a changed
        existing test -> tampered_tests contains it (today's default behavior).
        This AC may already PASS today (kwarg-absent call path is unaffected)."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "add test_x")
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False\n")

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/test_x.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: p.startswith("tests/"),
        )
        assert "tests/test_x.py" in result.tampered_tests

    def test_ac5_scan_boundary_whitelisted_test_excluded_and_telemetry_counted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC5: authorized_test_edits=["tests/test_x.py"] -> tampered_tests==[];
        telemetry field n_authorized_test_edits==1. Expected FAIL pre-GREEN
        (kwarg absent -> TypeError probed via presence-gate)."""
        _require_authorized_test_edits_kwarg()

        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "add test_x")
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False\n")

        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(telemetry_ctx, "emit_safe", lambda et, p: captured.append((et, p)))

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/test_x.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: p.startswith("tests/"),
            authorized_test_edits=["tests/test_x.py"],
        )
        assert result.tampered_tests == []

        scan_events = [p for (et, p) in captured if et == "authored_boundary_scan"]
        assert len(scan_events) == 1
        assert scan_events[0].get("n_authorized_test_edits") == 1

    def test_ac6_scan_boundary_glob_entry_does_not_whitelist(self, tmp_path: Path) -> None:
        """AC6: authorized_test_edits=["tests/*.py"] (glob) -> still tampered
        (exact-match only, fail-closed). Expected FAIL pre-GREEN (kwarg absent)."""
        _require_authorized_test_edits_kwarg()

        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "add test_x")
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False\n")

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/test_x.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: p.startswith("tests/"),
            authorized_test_edits=["tests/*.py"],
        )
        assert "tests/test_x.py" in result.tampered_tests, (
            "A glob entry must NOT whitelist via fnmatch — exact string match only"
        )

    def test_ac7_whitelist_does_not_bypass_suppression_leg(self, tmp_path: Path) -> None:
        """AC7: whitelisted test path with a NEW nosemgrep token in paths ->
        suppression_hits non-empty. Expected FAIL pre-GREEN (kwarg absent)."""
        _require_authorized_test_edits_kwarg()

        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "add test_x")
        _write_file(
            repo, "tests/test_x.py",
            "def test_x(): assert True\n# nosemgrep\n",
        )

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/test_x.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: p.startswith("tests/"),
            authorized_test_edits=["tests/test_x.py"],
        )
        assert result.suppression_hits, (
            "Whitelist covers the tamper leg only; the suppression leg must "
            "still scan the whitelisted path when it is in `paths`"
        )

    def test_ac8_commit_green_code_e2e_whitelisted_test_edit_succeeds(
        self, tmp_path: Path
    ) -> None:
        """AC8: e2e commit_green_code — spec carries the marker for
        tests/test_x.py; green-phase edits that committed RED test ->
        status=='ok', green commit created, no E_RED_TESTS_TAMPERED.
        Expected FAIL pre-GREEN (call site not wired to pass authorized_test_edits)."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "RED: add test_x")
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # inverted contract\n")
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        spec_path = scratchpad / "spec.md"
        spec_path.write_text(
            "## Files\n\n- MODIFY `src/module.py`\n\n"
            "authorized-test-edits:\n"
            "- `tests/test_x.py`\n"
        )
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "red_commit_sha", red_sha, cycle=1,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
            spec_path=str(spec_path),
        )

        commits_before = _rev_count(repo)
        result = phase_5_implement._commit_green_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.status == "ok", (
            f"Whitelisted test edit must succeed, got {result.status!r} "
            f"error_code={getattr(result, 'error_code', None)!r} data={result.data!r}"
        )
        assert commits_after == commits_before + 1
        assert getattr(result, "error_code", None) != "E_RED_TESTS_TAMPERED"

    def test_ac9_commit_green_code_e2e_unlisted_test_edit_still_rejected(
        self, tmp_path: Path
    ) -> None:
        """AC9: same shape as AC8 but the edited test is NOT listed in the
        marker -> E_RED_TESTS_TAMPERED unchanged (regression pin). This AC may
        already PASS today — today's code always rejects any test edit
        regardless of a whitelist (no whitelist mechanism exists yet)."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "RED: add test_x")
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # tampered\n")
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        spec_path = scratchpad / "spec.md"
        spec_path.write_text(
            "## Files\n\n- MODIFY `src/module.py`\n\n"
            "authorized-test-edits:\n"
            "- `tests/test_y.py`\n"
        )
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "red_commit_sha", red_sha, cycle=1,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
            spec_path=str(spec_path),
        )

        commits_before = _rev_count(repo)
        result = phase_5_implement._commit_green_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.status == "error"
        assert result.error_code == "E_RED_TESTS_TAMPERED"
        assert commits_after == commits_before
