"""RED tests for GH960 -- downgrade pre-existing-file tamper flags to warning.

Spec: SHARED/memory/Decisions/2026-07-17_GH960_preexisting_tamper_downgrade_spec.md
ACs 1-9 (spec section 3).

Collectability (D1CF5FDF / section 1q): the ``red_freeze_paths`` kwarg on
``scan_boundary`` does NOT exist yet. All modules imported at top level exist
today; the new kwarg is passed directly in each call, so a TypeError raised
by the not-yet-updated signature surfaces at assert/call time inside the
test body, never at collection time.

Stub-passability (section 1l/7AD3D393): ``scan_boundary`` and
``_commit_green_code`` are called directly, never patched. Only
``telemetry_ctx.emit_safe`` is monkeypatched for telemetry capture, matching
the sibling test_gh436_tamper_whitelist.py pattern.
"""
from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import phase_5_implement  # noqa: E402
import lib.authored_boundary as boundary_mod  # noqa: E402
import telemetry_ctx  # noqa: E402


# ─── shared helpers (mirrors test_gh436_tamper_whitelist.py) ───────────────


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


def _delete_file(repo: Path, relpath: str) -> None:
    p = repo / relpath
    p.unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)


class TestGH960PreexistingTamperDowngrade:
    """GH960 -- pre-existing test-path change downgrade (ACs 1-9)."""

    def test_ac1_preexisting_file_deleted_downgraded_to_warning(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC1: pre-existing test path (in base, not in red_freeze_paths)
        deleted after base -> tampered_tests == [], one
        red_tamper_preexisting_downgraded event with cls=='deleted'."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/__init__.py", "\n", "add preexisting init"
        )
        _delete_file(repo, "tests/__init__.py")

        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            telemetry_ctx, "emit_safe", lambda et, p: captured.append((et, p))
        )

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/__init__.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=["tests/test_red_owned.py"],
        )
        assert result.tampered_tests == [], (
            f"pre-existing deletion outside red_freeze_paths must be downgraded, "
            f"got tampered_tests={result.tampered_tests!r}"
        )

        downgrade_events = [
            p for (et, p) in captured if et == "red_tamper_preexisting_downgraded"
        ]
        assert len(downgrade_events) == 1
        assert downgrade_events[0].get("path") == "tests/__init__.py"
        assert downgrade_events[0].get("cls") == "deleted"
        assert downgrade_events[0].get("boundary") == "green_commit"

    def test_ac2_preexisting_file_edited_downgraded_with_cls_edited(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC2: same shape but file EDITED (still present) -> downgraded,
        cls=='edited'."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/__init__.py", "VALUE_A = 1\n", "add preexisting init"
        )
        _write_file(repo, "tests/__init__.py", "VALUE_A = 2\n")

        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            telemetry_ctx, "emit_safe", lambda et, p: captured.append((et, p))
        )

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/__init__.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=["tests/test_red_owned.py"],
        )
        assert result.tampered_tests == []

        downgrade_events = [
            p for (et, p) in captured if et == "red_tamper_preexisting_downgraded"
        ]
        assert len(downgrade_events) == 1
        assert downgrade_events[0].get("cls") == "edited"

    def test_ac3_frozen_path_edited_stays_tampered(self, tmp_path: Path) -> None:
        """AC3: path IN red_freeze_paths, edited -> still in tampered_tests
        (regression pin -- RED-owned files never downgraded)."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/test_red_owned.py", "def test_x(): assert True\n", "RED add"
        )
        _write_file(
            repo, "tests/test_red_owned.py", "def test_x(): assert False\n"
        )

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/test_red_owned.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=["tests/test_red_owned.py"],
        )
        assert "tests/test_red_owned.py" in result.tampered_tests

    def test_ac4_new_file_not_in_base_stays_tampered(self, tmp_path: Path) -> None:
        """AC4: test path not in manifest AND not in base (created after
        base) -> still tampered (git show base:p fails -> fail-closed)."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        base_sha = _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _write_file(repo, "tests/test_new.py", "def test_new(): assert True\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/test_new.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=["tests/test_red_owned.py"],
        )
        assert "tests/test_new.py" in result.tampered_tests

    def test_ac5_no_freeze_manifest_stays_tampered_legacy(self, tmp_path: Path) -> None:
        """AC5: red_freeze_paths=None (no manifest -- legacy runs), a
        pre-existing deletion still stays tampered (fail-closed legacy)."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/__init__.py", "\n", "add preexisting init"
        )
        _delete_file(repo, "tests/__init__.py")

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/__init__.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=None,
        )
        assert "tests/__init__.py" in result.tampered_tests

    def test_ac6_telemetry_payload_counts(self, tmp_path: Path, monkeypatch) -> None:
        """AC6: authored_boundary_scan payload carries
        n_preexisting_downgraded==1 and n_tampered_tests==0 in the AC1
        scenario; n_preexisting_downgraded==0 when red_freeze_paths is None."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/__init__.py", "\n", "add preexisting init"
        )
        _delete_file(repo, "tests/__init__.py")

        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            telemetry_ctx, "emit_safe", lambda et, p: captured.append((et, p))
        )

        boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/__init__.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=["tests/test_red_owned.py"],
        )
        scan_events = [p for (et, p) in captured if et == "authored_boundary_scan"]
        assert len(scan_events) == 1
        assert scan_events[0].get("n_preexisting_downgraded") == 1
        assert scan_events[0].get("n_tampered_tests") == 0

        captured.clear()
        boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/__init__.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=None,
        )
        scan_events2 = [p for (et, p) in captured if et == "authored_boundary_scan"]
        assert len(scan_events2) == 1
        assert scan_events2[0].get("n_preexisting_downgraded") == 0

    def test_ac7_authorized_edit_excluded_before_downgrade_logic(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC7: path in authorized_test_edits (exact match) excluded BEFORE
        downgrade logic -- not tampered AND no downgrade event for it."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/test_authorized.py", "def test_x(): assert True\n", "add"
        )
        _write_file(
            repo, "tests/test_authorized.py", "def test_x(): assert False\n"
        )

        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            telemetry_ctx, "emit_safe", lambda et, p: captured.append((et, p))
        )

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/test_authorized.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            authorized_test_edits=["tests/test_authorized.py"],
            red_freeze_paths=["tests/test_red_owned.py"],
        )
        assert result.tampered_tests == []
        downgrade_events = [
            p for (et, p) in captured if et == "red_tamper_preexisting_downgraded"
            and p.get("path") == "tests/test_authorized.py"
        ]
        assert downgrade_events == [], (
            "authorized_test_edits exclusion happens before downgrade logic -- "
            "no downgrade event should fire for an already-whitelisted path"
        )

    def test_ac8_commit_green_code_wires_red_freeze_paths(self) -> None:
        """AC8 (cycle-2 strengthened pin, anti-vacuous): in the
        commit_green_code gate-block source, (a) the literal expression
        ``red_freeze_paths=list(_frozen.keys()) if _frozen else None``
        appears in the scan_boundary( call, (b) exactly ONE
        _read_red_test_hashes( occurrence exists in the gate block, and
        (c) that occurrence precedes the scan_boundary( call textually.
        A red_freeze_paths=None stub call fails assertion (a)."""
        source = Path(phase_5_implement.__file__).read_text()
        gate_start = source.index("GH373 Part A: authored-diff boundary scan")
        gate_end = source.index("GH639", gate_start)
        gate_block = source[gate_start:gate_end]

        expected_literal = (
            "red_freeze_paths=list(_frozen.keys()) if _frozen else None"
        )
        assert expected_literal in gate_block, (
            "commit_green_code's scan_boundary( call must pass the exact "
            f"literal {expected_literal!r} (GH960 section 2.2 not wired, "
            "or wired with a gameable stub such as red_freeze_paths=None)"
        )

        occurrences = gate_block.count("_read_red_test_hashes(")
        assert occurrences == 1, (
            "gate block must contain exactly ONE _read_red_test_hashes( call "
            f"(hoisted, reused by the GH639 leg); found {occurrences}"
        )

        read_idx = gate_block.index("_read_red_test_hashes(")
        scan_idx = gate_block.index("scan_boundary(")
        assert read_idx < scan_idx, (
            "_read_red_test_hashes( must be hoisted BEFORE the scan_boundary( "
            "call in the gate block"
        )

    def test_ac10_empty_freeze_manifest_still_downgrades(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC10: red_freeze_paths=[] (manifest present but empty) -- a
        pre-existing deletion IS downgraded. Pins the sentinel check as
        `is None`, not falsy/truthy-empty-list confusion."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/__init__.py", "\n", "add preexisting init"
        )
        _delete_file(repo, "tests/__init__.py")

        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            telemetry_ctx, "emit_safe", lambda et, p: captured.append((et, p))
        )

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/__init__.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=[],
        )
        assert result.tampered_tests == [], (
            "red_freeze_paths=[] must still downgrade a pre-existing deletion "
            "-- sentinel check must be `is None`, not a falsy-empty-list check"
        )
        downgrade_events = [
            p for (et, p) in captured if et == "red_tamper_preexisting_downgraded"
        ]
        assert len(downgrade_events) == 1

    def test_ac9_emit_seam_never_raises_on_emit_failure(self, tmp_path: Path, monkeypatch) -> None:
        """AC9: downgrade event emitted via telemetry_ctx.emit_safe
        (patchable); when emit_safe raises, scan_boundary still returns a
        result (never propagates the telemetry failure)."""
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        base_sha = _commit_file(
            repo, "tests/__init__.py", "\n", "add preexisting init"
        )
        _delete_file(repo, "tests/__init__.py")

        def _raising_emit(et, p):
            raise RuntimeError("telemetry backend down")

        monkeypatch.setattr(telemetry_ctx, "emit_safe", _raising_emit)

        result = boundary_mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["tests/__init__.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: "/tests/" in p or p.startswith("tests/"),
            red_freeze_paths=["tests/test_red_owned.py"],
        )
        assert result is not None
        assert result.tampered_tests == []
