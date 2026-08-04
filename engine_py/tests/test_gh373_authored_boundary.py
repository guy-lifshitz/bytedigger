"""RED tests for GH373 Part A — universal authored-diff boundary scanner.

Spec: SHARED/memory/Decisions/2026-07-07_GH373_authored_boundary_verdict_spec.md
ACs A1-A10 (spec §3, Part A). Pre-GREEN expected: ALL FAIL except A10 (parity —
``lib.directed_repair._scan_new_suppression_tokens`` already exists today and
already detects a newly-added ``gitleaks:allow`` token, independent of this
migration; that assertion documents a behavior that must survive the ship).

Conventions (§1q / D1CF5FDF collectability guard, 81F97F3D no module-level
sys.path mutation): ``lib/authored_boundary.py`` does not exist yet, so it is
imported INSIDE each test body via ``_load_authored_boundary()`` — the file
COLLECTS and each test FAILS at assert/import-inside-body time, never at
collection time. Modules that already exist today (``phase_5_implement``,
``phase_6_review``, ``telemetry_ctx``, ``contracts``, ``lib.directed_repair``)
are imported at module top level (collectable) — conftest.py's import-time
singleton already puts ``engine_py`` root + ``workflows`` on sys.path, and
``lib`` is a package under the engine_py root so ``lib.directed_repair``
resolves without any extra path manipulation.

Stub-passability (§1l/7AD3D393): UUT symbols ``scan_boundary``,
``scan_added_content``, ``_commit_green_code``, ``_commit_fix_code`` are
called directly, never patched/mocked. Only ``telemetry_ctx.emit_safe``,
``phase_5_implement._emit_safe``, and env vars (via monkeypatch) are stubbed.

Fixture repos resolve ``tmp_path`` with ``.resolve()`` (§1j — macOS
``/var/folders`` symlinks break subprocess-cwd equality).
"""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.lib import directed_repair as dr_mod  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


# ─── shared helpers ───────────────────────────────────────────────────────────


def _load_authored_boundary():
    """Import the GH373 Part A primitive INSIDE test bodies (collectability guard)."""
    try:
        return importlib.import_module("bytedigger_engine.lib.authored_boundary")
    except ImportError as exc:
        pytest.fail(
            "lib/authored_boundary.py absent — GH373 Part A scanner not yet "
            f"implemented (expected RED fail): {exc}"
        )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str) -> str:
    """Write, stage, commit a file. Returns the resulting HEAD SHA."""
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
    """Write a file to the working tree without staging/committing (uncommitted diff)."""
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
        question="Fix the thing", session_id="gh373-a-green", persona="hal",
        framework=None, domain=None,
    )


def _make_fix_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Apply fix", session_id="gh373-a-fix", persona="hal",
        framework=None, domain=None,
    )


def _make_prev(sha_key: str, sha_value: str, cycle: int = 1, **extra) -> MagicMock:
    prev = MagicMock()
    prev.data = {sha_key: sha_value, "cycle": cycle, **extra}
    return prev


# ═══════════════════════════════════════════════════════════════════════════════
# TestGH373AuthoredBoundaryPartA
# ═══════════════════════════════════════════════════════════════════════════════


class TestGH373AuthoredBoundaryPartA:
    """GH373 Part A — universal authored-diff boundary scanner (ACs A1-A10)."""

    def test_ac1_suppression_patterns_migrated_and_shared_identity(self) -> None:
        """AC1: expected FAIL pre-GREEN (module absent).

        ``authored_boundary.SUPPRESSION_PATTERNS`` must expose exactly 4 entries
        and ``lib.directed_repair._SUPPRESSION_PATTERNS`` must become the SAME
        object (identity, not just equal value) after the migration.
        """
        mod = _load_authored_boundary()
        assert len(mod.SUPPRESSION_PATTERNS) == 4, (
            f"Expected 4 suppression patterns, got {len(mod.SUPPRESSION_PATTERNS)}"
        )
        assert dr_mod._SUPPRESSION_PATTERNS is mod.SUPPRESSION_PATTERNS, (
            "lib.directed_repair._SUPPRESSION_PATTERNS must be the SAME object as "
            "authored_boundary.SUPPRESSION_PATTERNS post-migration (single source of truth)"
        )

    def test_ac2_scan_added_content_parity(self) -> None:
        """AC2: expected FAIL pre-GREEN (module absent).

        ``scan_added_content`` must match a token in an ADDED line, must NOT
        match a token that already existed pre-diff (even if the diff adds an
        unrelated line elsewhere), and must layer caller-supplied
        ``forbidden_new_tokens`` case-insensitively on top of the built-ins.
        """
        mod = _load_authored_boundary()

        pre = "def f():\n    return 1\n"
        post_new_token = pre + "# security-lint: allow x\n"
        hits = mod.scan_added_content(pre, post_new_token)
        assert any("security-lint" in h.lower() for h in hits), (
            f"Expected a security-lint hit for newly-added line, got {hits!r}"
        )

        pre_has_token = "# nosemgrep\n" + pre
        post_unrelated_add = pre_has_token + "print('hi')\n"
        hits_existing = mod.scan_added_content(pre_has_token, post_unrelated_add)
        assert not any("nosem" in h.lower() for h in hits_existing), (
            f"Token present in PRE (not newly added) must not be reported; got {hits_existing!r}"
        )

        post_forbidden = pre + "CUSTOMsecret\n"
        hits_forbidden = mod.scan_added_content(pre, post_forbidden, forbidden_new_tokens=["customsecret"])
        assert any(h.lower() == "customsecret" for h in hits_forbidden), (
            f"forbidden_new_tokens must match case-insensitively; got {hits_forbidden!r}"
        )

    def test_ac3_scan_boundary_unknown_boundary_raises_valueerror(self, tmp_path: Path) -> None:
        """AC3: expected FAIL pre-GREEN (module absent).

        Registry fail-closed: an unregistered boundary name raises ValueError
        before any git work is attempted.
        """
        mod = _load_authored_boundary()
        repo = (tmp_path / "repo").resolve()
        with pytest.raises(ValueError):
            mod.scan_boundary(
                "nonexistent_boundary",
                base_sha="a" * 40,
                paths=[],
                git_cwd=str(repo),
                is_test_path=lambda p: False,
            )

    def test_ac4_commit_green_code_rejects_new_suppression_token(self, tmp_path: Path) -> None:
        """AC4: expected FAIL pre-GREEN (no boundary gate exists — commit succeeds today).

        RED commit tracks src/module.py; worktree ADDS a `security-lint: allow`
        suppression token to that file. ``_commit_green_code`` must refuse the
        commit with E_BOUNDARY_SUPPRESSION and leave the commit count unchanged.
        """
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
        _write_file(repo, "src/module.py", "def foo():\n    return 1\n# security-lint: allow x\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "red_commit_sha", red_sha, cycle=1,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
        )

        commits_before = _rev_count(repo)
        result = phase_5_implement._commit_green_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.status == "error", (
            f"Expected error for a suppression-token-adding GREEN diff, got {result.status!r} "
            f"(data={result.data!r})"
        )
        assert result.error_code == "E_BOUNDARY_SUPPRESSION", (
            f"Expected E_BOUNDARY_SUPPRESSION, got {result.error_code!r}"
        )
        assert commits_after == commits_before, (
            f"No GREEN commit should be created on a rejected diff; "
            f"before={commits_before} after={commits_after}"
        )

    def test_ac5_commit_green_code_rejects_tampered_red_test(self, tmp_path: Path) -> None:
        """AC5: expected FAIL pre-GREEN (no tamper gate exists — commit succeeds today).

        RED commit tracks tests/test_x.py; worktree modifies that committed
        test file (tamper) while also adding an untracked prod file to the
        manifest. ``_commit_green_code`` must refuse with E_RED_TESTS_TAMPERED
        and leave the commit count unchanged.
        """
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "RED: add test_x")
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # tampered\n")
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "red_commit_sha", red_sha, cycle=1,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
        )

        commits_before = _rev_count(repo)
        result = phase_5_implement._commit_green_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.status == "error", (
            f"Expected error when a RED-committed test file is tampered, got {result.status!r} "
            f"(data={result.data!r})"
        )
        assert result.error_code == "E_RED_TESTS_TAMPERED", (
            f"Expected E_RED_TESTS_TAMPERED, got {result.error_code!r}"
        )
        assert commits_after == commits_before, (
            f"No GREEN commit should be created on a tampered-test diff; "
            f"before={commits_before} after={commits_after}"
        )

    def test_ac6_commit_green_code_clean_diff_commits_and_emits_scan_telemetry(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC6: expected FAIL pre-GREEN (authored_boundary_scan event does not exist today).

        A clean prod diff (no suppression, no tamper) must still commit AND
        emit ``authored_boundary_scan`` via telemetry_ctx.emit_safe with
        boundary=green_commit, n_suppression_hits=0, n_tampered_tests=0.
        """
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED: add failing test")
        _write_file(repo, "src/module.py", "def foo(): return 42\n")

        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(telemetry_ctx, "emit_safe", lambda et, p: captured.append((et, p)))

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "red_commit_sha", red_sha, cycle=1,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
        )

        result = phase_5_implement._commit_green_code(ctx, prev)

        assert result.status == "ok", f"Expected ok for a clean diff, got {result.status!r}: {getattr(result, 'error', '')}"
        assert result.data is not None
        assert result.data.get("green_commit_sha") is not None, "green_commit_sha must be set on a clean commit"

        scan_events = [p for (et, p) in captured if et == "authored_boundary_scan"]
        assert len(scan_events) == 1, (
            f"Expected 1 authored_boundary_scan event via telemetry_ctx.emit_safe, "
            f"got {len(scan_events)}. All captured: {captured!r}"
        )
        payload = scan_events[0]
        assert payload.get("boundary") == "green_commit"
        assert payload.get("n_suppression_hits") == 0
        assert payload.get("n_tampered_tests") == 0

    def test_ac7_gate_disabled_env_kill_switch_lets_suppression_through(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC7: expected FAIL pre-GREEN (no gate/kill-switch exists — no gate_disabled event).

        With HAL_AUTHORED_BOUNDARY_GATE=0, a diff that WOULD trip suppression
        must still commit, and a gate_disabled event (gate=HAL_AUTHORED_BOUNDARY_GATE)
        must be emitted via the phase's telemetry wrapper.
        """
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
        _write_file(repo, "src/module.py", "def foo():\n    return 1\n# security-lint: allow x\n")

        monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "0")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
        )

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "red_commit_sha", red_sha, cycle=1,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
        )

        result = phase_5_implement._commit_green_code(ctx, prev)

        assert result.status == "ok", (
            f"Kill switch ON: commit must proceed despite suppression token, got {result.status!r}"
        )
        assert result.data is not None and result.data.get("green_commit_sha") is not None, (
            "green_commit_sha must be set when the gate is disabled"
        )

        gate_events = [e for e in captured if e["type"] == "gate_disabled" and e["payload"].get("gate") == "HAL_AUTHORED_BOUNDARY_GATE"]
        assert len(gate_events) == 1, (
            f"Expected 1 gate_disabled event for HAL_AUTHORED_BOUNDARY_GATE, got {len(gate_events)}. "
            f"All captured: {captured!r}"
        )

    def test_ac8_commit_fix_code_rejects_new_suppression_token(self, tmp_path: Path) -> None:
        """AC8: expected FAIL pre-GREEN (no boundary gate exists — commit succeeds today).

        Fix boundary: pre_fix_sha commit tracks src/fix_module.py; worktree adds
        a `nosemgrep` token. ``_commit_fix_code`` must refuse with
        E_BOUNDARY_SUPPRESSION, no commit created. A second sub-case (tampered
        test file only, no suppression token) documents fix_commit's
        assert_tests_untouched=False policy: it must NOT flag E_RED_TESTS_TAMPERED
        (this sub-case is a parity/non-regression guard, not the AC8 forcing
        assertion — it may already pass today since no tamper check exists at all).
        """
        # ── sub-case 1: suppression token → E_BOUNDARY_SUPPRESSION ──────────────
        repo = (tmp_path / "repo1").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        pre_fix_sha = _commit_file(repo, "src/fix_module.py", "def x():\n    return 1\n", "pre-fix baseline")
        _write_file(repo, "src/fix_module.py", "def x():\n    return 1\n# nosemgrep\n")

        scratchpad = tmp_path / "scratch1"
        scratchpad.mkdir()
        ctx = _make_fix_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "pre_fix_sha", pre_fix_sha, cycle=2,
            worker_written_paths=["src/fix_module.py"], manifest_source="harness_tool_record",
        )

        commits_before = _rev_count(repo)
        result = phase_6_review._commit_fix_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.status == "error", (
            f"Expected error for a suppression-token-adding FIX diff, got {result.status!r} "
            f"(data={result.data!r})"
        )
        assert result.error_code == "E_BOUNDARY_SUPPRESSION", (
            f"Expected E_BOUNDARY_SUPPRESSION, got {result.error_code!r}"
        )
        assert commits_after == commits_before, (
            f"No FIX commit should be created on a rejected diff; "
            f"before={commits_before} after={commits_after}"
        )

        # ── sub-case 2: tampered committed test, NO suppression token ───────────
        repo2 = (tmp_path / "repo2").resolve()
        _init_repo(repo2)
        _commit_file(repo2, "src/placeholder.py", "# placeholder\n", "init")
        pre_fix_sha2 = _commit_file(repo2, "tests/test_y.py", "def test_y(): assert True\n", "pre-fix baseline")
        _write_file(repo2, "tests/test_y.py", "def test_y(): assert False  # tampered\n")
        _write_file(repo2, "src/other_fix.py", "def y(): return 3\n")

        scratchpad2 = tmp_path / "scratch2"
        scratchpad2.mkdir()
        ctx2 = _make_fix_ctx(scratchpad2, str(repo2))
        prev2 = _make_prev(
            "pre_fix_sha", pre_fix_sha2, cycle=2,
            worker_written_paths=["src/other_fix.py"], manifest_source="harness_tool_record",
        )

        result2 = phase_6_review._commit_fix_code(ctx2, prev2)
        assert result2.error_code != "E_RED_TESTS_TAMPERED", (
            "fix_commit boundary has assert_tests_untouched=False — must never "
            f"flag E_RED_TESTS_TAMPERED; got error_code={result2.error_code!r}"
        )

    def test_ac9_scan_boundary_new_file_treats_whole_content_as_added(self, tmp_path: Path) -> None:
        """AC9: expected FAIL pre-GREEN (module absent).

        A path absent at base_sha (git show rc != 0, i.e. genuinely new file)
        must have its ENTIRE post content treated as added — a suppression
        token anywhere in a brand-new file must be flagged.
        """
        mod = _load_authored_boundary()
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        base_sha = _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _write_file(repo, "src/new_file.py", "# security-lint: allow y\n")

        result = mod.scan_boundary(
            "green_commit",
            base_sha=base_sha,
            paths=["src/new_file.py"],
            git_cwd=str(repo),
            is_test_path=lambda p: False,
        )

        assert result.suppression_hits, (
            f"Expected a suppression hit for a brand-new file containing a token; "
            f"got {result.suppression_hits!r}"
        )
        assert any(h.get("path") == "src/new_file.py" for h in result.suppression_hits), (
            f"Expected the hit to reference src/new_file.py; got {result.suppression_hits!r}"
        )

    def test_ac10_directed_repair_wrapper_still_detects_new_suppression_token(self) -> None:
        """AC10: parity coverage — expected to PASS pre-GREEN.

        ``lib.directed_repair._scan_new_suppression_tokens`` is the pre-existing
        primitive being migrated into authored_boundary; it already detects a
        newly-added `gitleaks:allow` token today (before the migration touches
        this call site), and must continue to after the wrapper delegates to
        ``authored_boundary.scan_added_content``. This documents the repair-loop
        parity contract (§3b: test_457DC7DC_directed_repair.py stays green).
        """
        pre = "line one\n"
        post = pre + "gitleaks:allow\n"
        hits = dr_mod._scan_new_suppression_tokens(pre, post)
        assert any("gitleaks" in h.lower() for h in hits), (
            f"Expected a gitleaks:allow hit, got {hits!r}"
        )

    def test_ac11_commit_green_code_fails_closed_on_scan_git_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC11: scan infrastructure failure must fail CLOSED (error, no commit),
        never degrade to a silent clean scan. Expected pre-GREEN: FAIL (module absent).

        Fixture is a clean prod diff (no suppression token, no tamper) — identical
        shape to a passing AC6 case — except the `git diff --name-only <base_sha>`
        invocation scan_boundary's tests-untouched leg performs is forced to fail
        (rc != 0). ``_commit_green_code`` must return status=error with
        error_code=E_BOUNDARY_SCAN_FAILED and create no commit — NOT silently
        treat the failed leg as tampered=[] (a clean scan).
        """
        mod = _load_authored_boundary()

        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
        _write_file(repo, "src/module.py", "def foo():\n    return 2\n")  # clean change, no token

        real_run = mod.subprocess.run

        def _flaky_run(cmd, *args, **kwargs):
            if "diff" in cmd and "--name-only" in cmd:
                fake = MagicMock()
                fake.returncode = 128
                fake.stdout = ""
                fake.stderr = "fatal: ambiguous argument (forced failure for AC11)"
                return fake
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(mod.subprocess, "run", _flaky_run)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            "red_commit_sha", red_sha, cycle=1,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
        )

        commits_before = _rev_count(repo)
        result = phase_5_implement._commit_green_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.status == "error", (
            f"Expected error when the tests-untouched leg's git invocation fails, "
            f"got {result.status!r} (data={result.data!r})"
        )
        assert result.error_code == "E_BOUNDARY_SCAN_FAILED", (
            f"Expected E_BOUNDARY_SCAN_FAILED (fail-closed), got {result.error_code!r}"
        )
        assert commits_after == commits_before, (
            f"No GREEN commit should be created when the scan infrastructure fails; "
            f"before={commits_before} after={commits_after}"
        )
