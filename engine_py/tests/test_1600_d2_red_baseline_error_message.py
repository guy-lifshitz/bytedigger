"""RED tests for hal#1600 (D2) — one error-message chokepoint + rename.

Spec (v2, supersedes v1 after gate round-1 REJECTED with 4 MAJOR findings):
SHARED/memory/Decisions/2026-08-09_1600_d2_error_name_and_hint_order_spec_v2.md
ACs 1-10 (spec "Acceptance criteria" — AC1/AC2/AC3/AC3b/AC7a/AC7b/AC8 replace
v1's weaker versions; AC4/AC5/AC6/AC9 carried over, AC5 tightened; AC10 is a
post-GREEN mutation-check invariant, not directly testable pre-GREEN here).

Conventions (§1q / D1CF5FDF collectability guard): ``phase_5_implement``,
``phase_6_review``, ``lib.authored_boundary``, ``contracts``, and
``error_codes`` already exist today, so they are imported at module top
level (collectable, mirrors test_gh373/test_gh639/test_gh886 import headers
— no sys.path mutation). The NEW symbol this ship adds —
``phase_5_implement._red_baseline_error_message`` — does NOT exist yet, so
it is probed via ``getattr(...)`` INSIDE each test body via ``_require_helper``.
The file COLLECTS and each test FAILS at assert time, never at collection
time.

MAJOR-1 fix (offset-0, not "contains within 120"): AC1/AC2/AC3 now assert
``error.index(<action>) == 0`` / ``error_text.startswith(<action>)``, and the
site A / site B2 fixtures carry >=3 offending paths at realistic depth
(``SYSTEM/cli/build/engine_py/tests/test_*.py``, ~56 chars each) so a
dump-first layout cannot pass by accident (AC3b).

MAJOR-3 fix: site B2 gets two separate fixtures/tests — AC7a (flag OFF,
begins with the refresh action) and AC7b (flag ON, still errors with the
renamed code AND the frozen manifest file is byte-identical before/after —
B2 must never re-freeze).

MAJOR-4 fix: AC8 now binds the identifier ``_TAMPER_REMEDIATION_HINT`` via a
word-boundary regex (immune to ``+``/``.join``/``%``/``.format`` spellings)
and asserts exactly two production references: its own assignment, and one
inside ``_red_baseline_error_message``'s body (located via
``inspect.getsourcelines``, not literal offset arithmetic).

Stub-passability (§1l/7AD3D393): sites A, B1, B2 are driven end-to-end
through the real production entry points (``_build_green_prompt`` /
``_commit_green_code``). Sites C1/C2 stub only ``authored_boundary.scan_boundary``
— the same technique test_gh886's AC5/AC14 use to reach a branch the real
``fix_commit`` boundary policy (``assert_tests_untouched=False``) can never
itself populate — and then call the real ``_autocommit_fix_tail`` /
``_commit_fix_code`` production functions. The helper under test itself is
never patched/mocked anywhere.
"""
from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine import error_codes  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.lib import authored_boundary as boundary_mod  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


ENGINE_ROOT = Path(phase_5_implement.__file__).resolve().parents[1]

# AC3b realistic-depth multi-path fixture (>=3 paths, ~50+ chars each) used by
# sites A and B2 so a dump-first layout cannot pass the offset-0 checks by
# accident (a single 14-char path was v1's MAJOR-1 hole).
_REALISTIC_PATHS = [
    "SYSTEM/cli/build/engine_py/tests/test_alpha_baseline.py",
    "SYSTEM/cli/build/engine_py/tests/test_bravo_baseline.py",
    "SYSTEM/cli/build/engine_py/tests/test_charlie_baseline.py",
]


# ─── shared git-repo helpers (mirrors test_gh373/test_gh639/test_gh886) ──────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str, force: bool = False) -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    add_cmd = ["git", "add"] + (["-f"] if force else []) + [relpath]
    subprocess.run(add_cmd, cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout.strip()


def _make_green_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Fix the thing", session_id="d2-green", persona="hal",
        framework=None, domain=None,
    )


def _make_fix_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Apply fix", session_id="d2-fix", persona="hal",
        framework=None, domain=None,
    )


def _make_prev(**data) -> StepResult:
    return StepResult(status="ok", data=dict(data), duration_ms=0, step_name="prev")


def _require_helper():
    """Presence-gate for the not-yet-existing D2 chokepoint (D1CF5FDF)."""
    fn = getattr(phase_5_implement, "_red_baseline_error_message", None)
    assert fn is not None, (
        "phase_5_implement._red_baseline_error_message does not exist yet "
        "(D2 chokepoint not implemented) — expected RED fail"
    )
    return fn


def _extract(result) -> "tuple[str | None, str]":
    """Normalize a StepResult-or-dict return into (error_code, error_text)."""
    if isinstance(result, dict):
        return result.get("error_code"), result.get("error", "") or ""
    return getattr(result, "error_code", None), getattr(result, "error", "") or ""


# ─── site drivers: real production entry points per spec PREFLIGHT table ────


def _drive_site_a_refresh_available(tmp_path: Path, monkeypatch):
    """Site A, AC1 state: all mismatches head_moved (3 realistic paths), flag OFF."""
    repo = (tmp_path / "repo").resolve()
    scratchpad = tmp_path / "scratch"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    stale_manifest: dict[str, str] = {}
    for p in _REALISTIC_PATHS:
        _commit_file(repo, p, f"def test_x(): assert True  # strengthened {p}\n", f"RED: strengthen {p}")
        stale_manifest[p] = f"stale-digest-cycle-3-{p}"
    scratchpad.mkdir()
    phase_5_implement._persist_red_test_hashes(scratchpad, stale_manifest)
    monkeypatch.delenv("HAL_RED_BASELINE_REFRESH", raising=False)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", lambda *a, **kw: None)

    (scratchpad / "red-log.md").write_text("RED report\n")
    (scratchpad / "validation.md").write_text("VERDICT: APPROVED\n")
    spec_path = scratchpad / "minimal-spec.md"
    spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")

    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_prev(
        spec_path=str(spec_path), red_log_path=str(scratchpad / "red-log.md"),
        validation_doc_path=str(scratchpad / "validation.md"), red_test_paths=list(_REALISTIC_PATHS),
    )
    return phase_5_implement._build_green_prompt(ctx, prev), list(_REALISTIC_PATHS)


def _drive_site_a_non_refresh(tmp_path: Path, monkeypatch):
    """Site A, AC2/AC3/AC4 state: worktree_dirty (non-refresh), 3 realistic paths."""
    repo = (tmp_path / "repo").resolve()
    scratchpad = tmp_path / "scratch"
    _init_repo(repo)
    frozen: dict[str, str] = {}
    for p in _REALISTIC_PATHS:
        _commit_file(repo, p, "def test_x(): assert True\n", f"init {p}")
        frozen[p] = boundary_mod.compute_red_test_hashes([p], str(repo))[p]
    scratchpad.mkdir()
    phase_5_implement._persist_red_test_hashes(scratchpad, frozen)
    for p in _REALISTIC_PATHS:
        _write_file(repo, p, f"def test_x(): assert False  # dirty {p}\n")
    monkeypatch.delenv("HAL_RED_BASELINE_REFRESH", raising=False)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", lambda *a, **kw: None)

    (scratchpad / "red-log.md").write_text("RED report\n")
    (scratchpad / "validation.md").write_text("VERDICT: APPROVED\n")
    spec_path = scratchpad / "minimal-spec.md"
    spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")

    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_prev(
        spec_path=str(spec_path), red_log_path=str(scratchpad / "red-log.md"),
        validation_doc_path=str(scratchpad / "validation.md"), red_test_paths=list(_REALISTIC_PATHS),
    )
    return phase_5_implement._build_green_prompt(ctx, prev), list(_REALISTIC_PATHS)


def _drive_site_b1(tmp_path: Path, monkeypatch):
    """Site B1: authored-diff boundary tampered_tests leg (no frozen manifest)."""
    repo = (tmp_path / "repo").resolve()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "RED: add test_x")
    _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # tampered\n")
    _write_file(repo, "src/module.py", "def bar(): return 2\n")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    monkeypatch.setattr(phase_5_implement, "_emit_safe", lambda *a, **kw: None)
    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_prev(
        red_commit_sha=red_sha, cycle=1,
        worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
    )
    return phase_5_implement._commit_green_code(ctx, prev), ["tests/test_x.py"]


def _drive_site_b2(tmp_path: Path, monkeypatch):
    """Site B2, AC3/AC4 state: frozen-hash leg, git-diff-blind (gitignored),
    3 realistic-depth paths, non-refresh (never-committed -> head_unreadable)."""
    repo = (tmp_path / "repo").resolve()
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    _commit_file(repo, ".gitignore", "tests/\n", "add gitignore")

    for p in _REALISTIC_PATHS:
        _write_file(repo, p, "def test_x(): assert True\n")
    red_sha = _head(repo)

    manifest = boundary_mod.compute_red_test_hashes(_REALISTIC_PATHS, str(repo))
    phase_5_implement._persist_red_test_hashes(scratchpad, manifest)
    for p in _REALISTIC_PATHS:
        _write_file(repo, p, f"def test_x(): assert False  # tampered {p}\n")

    spec_path = scratchpad / "spec.md"
    spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")
    _write_file(repo, "src/module.py", "def bar(): return 2\n")

    monkeypatch.setattr(phase_5_implement, "_emit_safe", lambda *a, **kw: None)
    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_prev(
        red_commit_sha=red_sha, cycle=1,
        worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
        spec_path=str(spec_path),
    )
    return phase_5_implement._commit_green_code(ctx, prev), list(_REALISTIC_PATHS)


def _build_site_b2_head_moved_fixture(tmp_path: Path, monkeypatch, *, flag_on: bool):
    """Site B2, AC7a/AC7b fixture: all mismatches head_moved (git-diff-blind,
    3 realistic paths). ``red_commit_sha`` is pinned to the post-strengthen
    HEAD so the B1 git-diff leg sees zero changes and only B2's frozen-hash
    leg fires. Returns (ctx, prev, manifest_path) WITHOUT invoking
    ``_commit_green_code`` — callers own the before/after manifest snapshot."""
    repo = (tmp_path / "repo").resolve()
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    _commit_file(repo, ".gitignore", "tests/\n", "add gitignore")

    for p in _REALISTIC_PATHS:
        _commit_file(repo, p, f"def test_x(): assert True  # v1 {p}\n", f"RED v1 {p}", force=True)
    manifest = boundary_mod.compute_red_test_hashes(_REALISTIC_PATHS, str(repo))
    phase_5_implement._persist_red_test_hashes(scratchpad, manifest)

    red_sha = None
    for p in _REALISTIC_PATHS:
        red_sha = _commit_file(
            repo, p, f"def test_x(): assert True  # v2 strengthened {p}\n",
            f"RED: strengthen {p}", force=True,
        )

    spec_path = scratchpad / "spec.md"
    spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")
    _write_file(repo, "src/module.py", "def bar(): return 2\n")

    if flag_on:
        monkeypatch.setenv("HAL_RED_BASELINE_REFRESH", "1")
    else:
        monkeypatch.delenv("HAL_RED_BASELINE_REFRESH", raising=False)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_prev(
        red_commit_sha=red_sha, cycle=1,
        worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
        spec_path=str(spec_path),
    )
    relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
    manifest_path = scratchpad / relpath
    return ctx, prev, manifest_path


def _drive_site_c1(tmp_path: Path, monkeypatch):
    """Site C1: fix-tail boundary scan, tampered_tests stubbed on scan_boundary
    (the fix_commit boundary's assert_tests_untouched=False policy means the
    real scan_boundary can never itself populate tampered_tests here — same
    technique as test_gh886 AC5/AC14 — the real _autocommit_fix_tail is still
    exercised end to end)."""
    repo = (tmp_path / "repo").resolve()
    _init_repo(repo)
    base_sha = _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    _write_file(repo, "src/dirty.py", "# dirty tail\n")

    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "1")
    fake_scan_result = MagicMock()
    fake_scan_result.tampered_tests = ["tests/test_foo.py"]
    fake_scan_result.suppression_hits = []
    monkeypatch.setattr(
        phase_6_review.authored_boundary, "scan_boundary", lambda *a, **kw: fake_scan_result
    )

    result = phase_6_review._autocommit_fix_tail(
        {}, str(repo), "cfg_git_cwd", 1, base_sha, "commit_fix_tests"
    )
    return result, ["tests/test_foo.py"]


def _drive_site_c2(tmp_path: Path, monkeypatch):
    """Site C2: commit_fix_code boundary scan, tampered_tests stubbed on
    scan_boundary (same rationale as site C1 — the fix_commit boundary policy
    can never populate tampered_tests for real; the real _commit_fix_code
    production function is still exercised end to end). Per spec v2 MINOR:
    C1/C2 are dead emitters in production today (not live defects); they stay
    in scope for defensive routing-through-the-chokepoint consistency."""
    repo = (tmp_path / "repo").resolve()
    _init_repo(repo)
    pre_fix_sha = _commit_file(repo, "src/fix_module.py", "def x():\n    return 1\n", "pre-fix baseline")
    _write_file(repo, "src/fix_module.py", "def x():\n    return 2\n")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    fake_scan_result = MagicMock()
    fake_scan_result.tampered_tests = ["tests/test_y.py"]
    fake_scan_result.suppression_hits = []
    monkeypatch.setattr(
        phase_6_review.authored_boundary, "scan_boundary", lambda *a, **kw: fake_scan_result
    )

    ctx = _make_fix_ctx(scratchpad, str(repo))
    prev = _make_prev(
        pre_fix_sha=pre_fix_sha, cycle=2,
        worker_written_paths=["src/fix_module.py"], manifest_source="harness_tool_record",
    )
    result = phase_6_review._commit_fix_code(ctx, prev)
    return result, ["tests/test_y.py"]


_SITE_DRIVERS = {
    "A": _drive_site_a_non_refresh,
    "B1": _drive_site_b1,
    "B2": _drive_site_b2,
    "C1": _drive_site_c1,
    "C2": _drive_site_c2,
}


# ═══════════════════════════════════════════════════════════════════════════
# TestD2ErrorMessageOrder — AC1, AC2 (MAJOR-1: offset 0, not "contains")
# ═══════════════════════════════════════════════════════════════════════════


class TestD2ErrorMessageOrder:
    def test_ac1_site_a_refresh_available_action_at_offset_zero(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require_helper()
        result, _paths = _drive_site_a_refresh_available(tmp_path, monkeypatch)
        _code, error_text = _extract(result)
        assert error_text, f"expected an error string, got {result!r}"
        assert "set HAL_RED_BASELINE_REFRESH=1" in error_text, (
            f"expected the refresh operator action present at all, got error={error_text!r}"
        )
        assert error_text.index("set HAL_RED_BASELINE_REFRESH=1") == 0, (
            f"expected the refresh operator action to BEGIN the message, "
            f"got error[:60]={error_text[:60]!r}"
        )

    def test_ac2_site_a_non_refresh_action_at_offset_zero(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require_helper()
        result, _paths = _drive_site_a_non_refresh(tmp_path, monkeypatch)
        _code, error_text = _extract(result)
        assert error_text, f"expected an error string, got {result!r}"
        assert "add the path under" in error_text, (
            f"expected the non-refresh operator action present at all, got error={error_text!r}"
        )
        assert error_text.index("add the path under") == 0, (
            f"expected the non-refresh operator action to BEGIN the message, "
            f"got error[:60]={error_text[:60]!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestD2AllFiveSites — AC3, AC3b, AC4, AC7a, AC7b
# ═══════════════════════════════════════════════════════════════════════════


class TestD2AllFiveSites:
    @pytest.mark.parametrize("site", ["A", "B1", "B2", "C1", "C2"])
    def test_ac3_every_site_begins_with_an_operator_action_and_renamed_code(
        self, site: str, tmp_path: Path, monkeypatch
    ) -> None:
        _require_helper()
        result, _paths = _SITE_DRIVERS[site](tmp_path, monkeypatch)
        error_code, error_text = _extract(result)
        assert error_text, f"[{site}] expected an error string, got {result!r}"
        assert (
            error_text.startswith("set HAL_RED_BASELINE_REFRESH=1")
            or error_text.startswith("add the path under")
        ), (
            f"[{site}] expected the message to BEGIN with an operator action "
            f"(offset 0), got error[:60]={error_text[:60]!r}"
        )
        renamed_code = "E_RED_BASELINE" + "_FILE_MODIFIED"
        assert error_code == renamed_code, (
            f"[{site}] expected error_code == {renamed_code!r}, got {error_code!r}"
        )

    @pytest.mark.parametrize("site", ["A", "B1", "B2", "C1", "C2"])
    def test_ac4_every_site_path_dump_survives_in_full_message(
        self, site: str, tmp_path: Path, monkeypatch
    ) -> None:
        result, offending_paths = _SITE_DRIVERS[site](tmp_path, monkeypatch)
        _code, error_text = _extract(result)
        for p in offending_paths:
            assert p in error_text, (
                f"[{site}] expected offending path {p!r} to survive in the full "
                f"(untruncated) message, got {error_text!r}"
            )

    def test_ac12_realistic_paths_fixture_shape_is_load_bearing(self) -> None:
        """AC12 (round-2 MINOR): ``_REALISTIC_PATHS`` is load-bearing for
        AC1/AC2/AC3 — a later shrink of it would silently return those ACs to
        v1's defeatable "single 14-char path" strength with nothing going
        red. Pin the shape directly."""
        assert len(_REALISTIC_PATHS) >= 3, (
            f"expected >=3 realistic paths, got {len(_REALISTIC_PATHS)}: {_REALISTIC_PATHS!r}"
        )
        for p in _REALISTIC_PATHS:
            assert len(p) >= 50, f"expected each path >=50 chars, got {len(p)}: {p!r}"

    def test_ac7a_site_b2_flag_off_all_head_moved_begins_with_refresh_action(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require_helper()
        ctx, prev, _manifest_path = _build_site_b2_head_moved_fixture(
            tmp_path, monkeypatch, flag_on=False
        )
        result = phase_5_implement._commit_green_code(ctx, prev)
        _code, error_text = _extract(result)
        assert error_text, f"expected an error string, got {result!r}"
        assert error_text.startswith("set HAL_RED_BASELINE_REFRESH=1"), (
            f"site B2 flag OFF in an all-head_moved state must begin with the same "
            f"refresh action site A offers, got error[:60]={error_text[:60]!r}"
        )

    def test_ac7b_site_b2_flag_on_still_errors_and_never_refreezes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require_helper()
        ctx, prev, manifest_path = _build_site_b2_head_moved_fixture(
            tmp_path, monkeypatch, flag_on=True
        )
        before_bytes = manifest_path.read_bytes()
        result = phase_5_implement._commit_green_code(ctx, prev)
        after_bytes = manifest_path.read_bytes()
        error_code, error_text = _extract(result)
        assert error_text, (
            f"site B2 must still error with the flag ON (never re-freezes post-GREEN), "
            f"got {result!r}"
        )
        renamed_code = "E_RED_BASELINE" + "_FILE_MODIFIED"
        assert error_code == renamed_code, (
            f"expected error_code == {renamed_code!r} with flag ON, got {error_code!r}"
        )
        assert after_bytes == before_bytes, (
            "B2 must NEVER re-freeze the frozen manifest, even with the flag ON — "
            "that would bless whatever GREEN just wrote to the frozen test files"
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestD2Rename — AC5 (tightened), AC6
# ═══════════════════════════════════════════════════════════════════════════


class TestD2Rename:
    def test_ac5_retired_token_absent_from_engine_py_tree(self) -> None:
        old_token = "E_RED_TESTS" + "_TAMPERED"
        scanned = 0
        hits = []
        for path in ENGINE_ROOT.rglob("*"):
            if path.suffix not in (".py", ".md"):
                continue
            if "__pycache__" in path.parts:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="ignore")
            if old_token in text:
                hits.append(str(path.relative_to(ENGINE_ROOT)))
        assert scanned >= 100, (
            f"expected a non-empty corpus (>=100 .py/.md files) under {ENGINE_ROOT}, "
            f"scanned {scanned} — a wrong root must not pass as zero hits"
        )
        assert not hits, (
            f"retired token {old_token!r} must not appear anywhere under "
            f"{ENGINE_ROOT}; still present in: {hits!r}"
        )

    def test_ac6_error_codes_registry_carries_only_the_renamed_key(self) -> None:
        new_code = "E_RED_BASELINE" + "_FILE_MODIFIED"
        old_code = "E_RED_TESTS" + "_TAMPERED"
        assert new_code in error_codes.ERROR_CODES, (
            f"expected {new_code!r} registered in error_codes.ERROR_CODES, "
            f"got keys: {sorted(error_codes.ERROR_CODES)!r}"
        )
        description = error_codes.ERROR_CODES.get(new_code, "")
        assert "modified" in description.lower(), (
            f"expected the new code's description to say 'modified', got {description!r}"
        )
        assert "tampered" not in description.lower(), (
            f"expected the new code's description to drop 'tampered', got {description!r}"
        )
        assert old_code not in error_codes.ERROR_CODES, (
            f"expected {old_code!r} removed from error_codes.ERROR_CODES"
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestD2Chokepoint — AC8 (MAJOR-4: bind the identifier), AC9
# ═══════════════════════════════════════════════════════════════════════════


class TestD2Chokepoint:
    def test_ac8_hint_identifier_referenced_in_exactly_two_places(self) -> None:
        helper = _require_helper()
        ident = "_TAMPER_REMEDIATION_HINT"
        pattern = re.compile(r"(?<!\w)" + ident + r"(?!\w)")

        occurrences: "list[tuple[Path, int]]" = []
        for path in ENGINE_ROOT.rglob("*.py"):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                occurrences.append((path, line_no))

        assert len(occurrences) == 2, (
            f"expected the identifier {ident!r} referenced in exactly 2 production "
            f"places (its own assignment + the helper body), found "
            f"{len(occurrences)}: {occurrences!r}"
        )

        helper_source_path = inspect.getsourcefile(helper)
        assert helper_source_path is not None, "expected a resolvable source file for the helper"
        helper_file = Path(helper_source_path).resolve()
        src_lines, start_line = inspect.getsourcelines(helper)
        end_line = start_line + len(src_lines) - 1

        in_helper = [
            (p, ln) for (p, ln) in occurrences
            if p.resolve() == helper_file and start_line <= ln <= end_line
        ]
        assert len(in_helper) == 1, (
            f"expected exactly one of the two references inside "
            f"_red_baseline_error_message's own body (lines {start_line}-{end_line}), "
            f"found {len(in_helper)} there: {occurrences!r}"
        )

        assignment_pattern = re.compile(re.escape(ident) + r"\s*=")
        other = [oc for oc in occurrences if oc not in in_helper]
        assert len(other) == 1, f"expected exactly one non-helper reference, got {other!r}"
        other_path, other_line = other[0]
        other_lines = other_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        assert assignment_pattern.search(other_lines[other_line - 1]), (
            f"expected the non-helper reference to be the identifier's own assignment, "
            f"got line {other_line} of {other_path}: {other_lines[other_line - 1]!r}"
        )

    def test_ac11_hint_text_actually_reaches_the_message_at_site_a(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC11 (round-2 MINOR): AC8 only proves the identifier is referenced
        inside the helper body — a helper could reference it in a never-taken
        branch while emitting a hint-free message. Prove the actual HINT TEXT
        reaches the full message at a real site."""
        _require_helper()
        result, _paths = _drive_site_a_non_refresh(tmp_path, monkeypatch)
        _code, error_text = _extract(result)
        hint_text = phase_5_implement._TAMPER_REMEDIATION_HINT
        assert hint_text in error_text, (
            f"expected the actual _TAMPER_REMEDIATION_HINT text to appear in the "
            f"full message, got error={error_text!r} hint={hint_text!r}"
        )

    def test_ac9_sanctioned_refresh_state_still_returns_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """test_gh921_red_baseline_refresh.py's AC4 sanctioned-refresh case
        (flag ON, all head_moved) must still return ok/None — the reorder must
        not make a suppressed message reappear."""
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_x.py", "def test_x(): assert True  # strengthened\n", "RED: strengthen")
        scratchpad.mkdir()
        phase_5_implement._persist_red_test_hashes(scratchpad, {"tests/test_x.py": "stale-digest-cycle-3"})
        monkeypatch.setenv("HAL_RED_BASELINE_REFRESH", "1")
        monkeypatch.setattr(phase_5_implement, "_emit_safe", lambda *a, **kw: None)

        (scratchpad / "red-log.md").write_text("RED report\n")
        (scratchpad / "validation.md").write_text("VERDICT: APPROVED\n")
        spec_path = scratchpad / "minimal-spec.md"
        spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")

        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            spec_path=str(spec_path), red_log_path=str(scratchpad / "red-log.md"),
            validation_doc_path=str(scratchpad / "validation.md"), red_test_paths=["tests/test_x.py"],
        )
        result = phase_5_implement._build_green_prompt(ctx, prev)
        assert result.status == "ok", (
            f"sanctioned refresh must still return ok (suppressed message, no error), "
            f"got {result.status!r}: {getattr(result, 'error', '')}"
        )
