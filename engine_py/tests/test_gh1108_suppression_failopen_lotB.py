"""RED tests for GH1108 lot B (revised) — engine_py suppression fail-open closure.

Spec: SHARED/memory/Decisions/2026-07-22_CC6D477B_suppression_failopen_lotB_spec.md
Covers AC1-AC16 (§3), one test fn per AC.

OFIs shipped this lot:
  1830db11 (P2/P3) — per-gate blocklist registry + forbidden_tokens_for()
  f9001011 (P4)    — indeterminate origin rejects (spec-authorized still passes)
  f624e3fb (P6)    — green_commit scans a git-diff-derived (test-retained) set
  0b95d45a (P7)    — apply_surgical_patches suppression + pragma-allow guard

Conventions (§1q / D1CF5FDF collectability guard, 81F97F3D no module-level
sys.path mutation): existing modules (phase_5_implement, telemetry_ctx,
lib.authored_boundary, contracts) import at module top — conftest.py's
import-time singleton puts engine_py root + workflows on sys.path. The
NOT-YET-EXISTING symbol ``forbidden_tokens_for`` is resolved via
getattr(...) INSIDE each test body; ``apply_surgical_patches`` (module exists,
new behaviour absent) is imported inside each P7 body — so this file COLLECTS
cleanly and each test FAILS at assert time, never at collection time.

Stub-passability (§1l/7AD3D393): the real ``authored_boundary`` module and the
real ``_commit_green_code`` / ``_verify_security_lint`` / ``apply_surgical_patches``
are called directly, never mocked. Only ``phase_5_implement._emit_safe`` is
patched for telemetry capture. Fixture repos resolve paths with
``os.path.realpath`` (§1j — macOS /var/folders symlinks break subprocess-cwd
equality).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.lib import authored_boundary as authored_boundary  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── git fixtures (real temp repo — no mocking of git) ──────────────────────


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
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return r.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _rev_count(repo: Path) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return int(r.stdout.strip())


def _head_name_only(repo: Path) -> list[str]:
    r = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _porcelain(repo: Path, *pathspec: str) -> list[str]:
    # --untracked-files=all so a fully-untracked dir is NOT collapsed to a
    # single `?? tests/` line; pathspec targets the file explicitly.
    cmd = ["git", "status", "--porcelain", "--untracked-files=all"]
    if pathspec:
        cmd += ["--", *pathspec]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=True)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _real_repo(tmp_path: Path, name: str = "repo") -> Path:
    """§1j — realpath the tmp dir so subprocess-cwd path comparisons hold on macOS."""
    return Path(os.path.realpath(tmp_path)) / name


# ─── commit-flow ctx/prev (MagicMock prev — matches test_gh373/test_gh436) ───


def _make_green_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Fix the thing", session_id="gh1108-lotB-green", persona="hal",
        framework=None, domain=None,
    )


def _make_green_prev(**extra) -> MagicMock:
    prev = MagicMock()
    prev.data = {"cycle": 1, **extra}
    return prev


# ─── security-lint ctx/prev (StepResult prev — matches test_gh640) ───────────


def _make_lint_ctx(git_cwd: Path, scratchpad_dir: Path | None = None) -> WorkflowContext:
    cfg = {"git_cwd": str(git_cwd)}
    cfg["scratchpad_dir"] = str(scratchpad_dir) if scratchpad_dir is not None else str(git_cwd.parent / "scratchpad")
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=cfg,
        question="q", session_id="gh1108-lotB-lint", persona="hal",
        framework=None, domain=None,
    )


def _make_lint_prev(**extra) -> StepResult:
    data: dict = {
        "green_log_path": "tests/build-green-output.log",
        "spec_path": "scratchpad/spec.md",
        "red_log_path": "tests/build-red-output.log",
        "validation_doc_path": "scratchpad/validation.md",
        "green_status": "PASS",
    }
    data.update(extra)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="verify_green_lint_rules")


def _capture_events(monkeypatch) -> list:
    events: list = []
    monkeypatch.setattr(phase_5_implement, "_emit_safe", lambda et, payload, **kw: events.append((et, payload)))
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# 1830db11 (P2/P3) — per-gate blocklist registry + forbidden_tokens_for()
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac01_registry_token_flows_into_scan_boundary_no_callsite_change(tmp_path, monkeypatch):
    """AC1: forbidden_tokens_for('green_commit') list + a registry-appended literal makes scan_boundary flag it, no call-site change."""
    fn = getattr(authored_boundary, "forbidden_tokens_for", None)
    assert fn is not None, "authored_boundary.forbidden_tokens_for not implemented yet (expected RED)"

    base = fn("green_commit")
    assert isinstance(base, list), f"forbidden_tokens_for must return a list, got {base!r}"

    token = "MYCUSTOMSUPPRESSGRAMMAR"
    monkeypatch.setitem(authored_boundary.BOUNDARIES["green_commit"], "extra_forbidden_tokens", list(base) + [token])

    repo = _real_repo(tmp_path)
    _init_repo(repo)
    base_sha = _commit_file(repo, "a.py", "x = 1\n", "base")
    _write_file(repo, "a.py", f"x = 1\n# {token} added here\n")

    result = authored_boundary.scan_boundary(
        "green_commit",
        base_sha=base_sha,
        paths=["a.py"],
        git_cwd=str(repo),
        is_test_path=lambda p: False,
    )
    assert result.suppression_hits, (
        f"registry-appended token must be scanned via scan_boundary (registry union), got {result.suppression_hits!r}"
    )


def test_ac02_forbidden_tokens_for_fails_closed_valueerror(tmp_path, monkeypatch):
    """AC2: forbidden_tokens_for raises ValueError for an unregistered boundary and for a registered entry missing the key (return captured OUTSIDE pytest.raises)."""
    fn = getattr(authored_boundary, "forbidden_tokens_for", None)
    assert fn is not None, "authored_boundary.forbidden_tokens_for not implemented yet (expected RED)"

    # leg 1 — unregistered boundary name → ValueError
    with pytest.raises(ValueError):
        fn("no_such_boundary")

    # leg 2 — registered entry lacking extra_forbidden_tokens → ValueError.
    # MAJOR-4 structure: capture the return OUTSIDE pytest.raises so a silent []
    # return fails the test (AssertionError-in-raises would falsely pass).
    monkeypatch.setitem(
        authored_boundary.BOUNDARIES,
        "green_commit",
        {"scan_suppression": True, "assert_tests_untouched": True},
    )
    caught = False
    got = None
    try:
        got = fn("green_commit")
    except ValueError:
        caught = True
    assert caught, f"forbidden_tokens_for must raise ValueError for an entry missing extra_forbidden_tokens, returned {got!r}"


def test_ac03_every_boundary_declares_extra_forbidden_tokens_list():
    """AC3: every BOUNDARIES entry declares extra_forbidden_tokens as a list."""
    for name, entry in authored_boundary.BOUNDARIES.items():
        assert "extra_forbidden_tokens" in entry, f"boundary {name!r} missing extra_forbidden_tokens key"
        assert isinstance(entry["extra_forbidden_tokens"], list), (
            f"boundary {name!r} extra_forbidden_tokens must be a list, got {type(entry['extra_forbidden_tokens'])}"
        )


def test_ac04_scan_boundary_without_kwarg_still_applies_registry_tokens(tmp_path, monkeypatch):
    """AC4: scan_boundary('green_commit', ...) called WITHOUT forbidden_new_tokens still applies the registry tokens (26 call sites must not break)."""
    fn = getattr(authored_boundary, "forbidden_tokens_for", None)
    assert fn is not None, "authored_boundary.forbidden_tokens_for not implemented yet (expected RED)"

    token = "REGISTRYONLYTOKEN"
    base = fn("green_commit")
    monkeypatch.setitem(authored_boundary.BOUNDARIES["green_commit"], "extra_forbidden_tokens", list(base) + [token])

    repo = _real_repo(tmp_path)
    _init_repo(repo)
    base_sha = _commit_file(repo, "a.py", "x = 1\n", "base")
    _write_file(repo, "a.py", f"x = 1\n# {token}\n")

    # No forbidden_new_tokens kwarg — signature unchanged, registry supplies the token.
    result = authored_boundary.scan_boundary(
        "green_commit",
        base_sha=base_sha,
        paths=["a.py"],
        git_cwd=str(repo),
        is_test_path=lambda p: False,
    )
    assert result.suppression_hits, (
        f"registry token must be applied even with no forbidden_new_tokens kwarg, got {result.suppression_hits!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# f9001011 (P4) — indeterminate origin rejects (spec-authorized still passes)
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac05_falsy_red_sha_unauthorized_pragma_rejects_indeterminate(tmp_path, monkeypatch):
    """AC5: _verify_security_lint with falsy red_sha + a pragma NOT in spec ⇒ error, E_SEC_LINT_SELF_EXEMPT, hit origin=='indeterminate'."""
    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "base")
    _write_file(repo, "a.py", "# security-lint: allow some-unauthorized-reason\nx = 2\n")

    _capture_events(monkeypatch)
    ctx = _make_lint_ctx(repo)
    prev = _make_lint_prev(red_commit_sha=None)
    result = phase_5_implement._verify_security_lint(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status}"
    assert result.error_code == "E_SEC_LINT_SELF_EXEMPT", f"expected E_SEC_LINT_SELF_EXEMPT, got {result.error_code!r}"
    self_exempt = (result.data or {}).get("security_lint_self_exempt") or []
    assert any(h.get("origin") == "indeterminate" for h in self_exempt), (
        f"rejected hit must carry origin=='indeterminate', got {self_exempt!r}"
    )


def test_ac06_falsy_red_sha_spec_authorized_pragma_passes(tmp_path, monkeypatch):
    """AC6: same as AC5 but path IS spec-authorized ⇒ ok, no security_lint_self_exempt, a security_lint_pragma_authorized reason=='spec_authorized' path=='a.py' emitted."""
    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "base")
    _write_file(repo, "a.py", "# security-lint: allow spec-authorized-reason\nx = 2\n")

    scratchpad = Path(os.path.realpath(tmp_path)) / "scratchpad"
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "build-spec.md").write_text("## §5 Files in scope\nsecurity-lint-pragma-allow: a.py\n")

    events = _capture_events(monkeypatch)
    ctx = _make_lint_ctx(repo, scratchpad_dir=scratchpad)
    prev = _make_lint_prev(red_commit_sha=None)
    result = phase_5_implement._verify_security_lint(ctx, prev)

    assert result.status == "ok", f"expected ok (spec-authorized), got {result.status} err={result.error_code!r}"
    assert not (result.data or {}).get("security_lint_self_exempt"), (
        f"expected no security_lint_self_exempt, got {(result.data or {}).get('security_lint_self_exempt')!r}"
    )
    authorized = [p for (et, p) in events if et == "security_lint_pragma_authorized"]
    assert any(p.get("reason") == "spec_authorized" and p.get("path") == "a.py" for p in authorized), (
        f"expected security_lint_pragma_authorized reason=spec_authorized for a.py, got {events!r}"
    )


def test_ac07_falsy_red_sha_rejection_emits_indeterminate_rejected_event(tmp_path, monkeypatch):
    """AC7: AC5's rejection emits security_lint_pragma_origin_indeterminate_rejected (path a.py); the bare indeterminate event is not the sole event."""
    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "base")
    _write_file(repo, "a.py", "# security-lint: allow some-unauthorized-reason\nx = 2\n")

    events = _capture_events(monkeypatch)
    ctx = _make_lint_ctx(repo)
    prev = _make_lint_prev(red_commit_sha=None)
    phase_5_implement._verify_security_lint(ctx, prev)

    rejected = [p for (et, p) in events if et == "security_lint_pragma_origin_indeterminate_rejected"]
    assert any(p.get("path") == "a.py" for p in rejected), (
        f"expected security_lint_pragma_origin_indeterminate_rejected for a.py, got {events!r}"
    )


def test_ac08_valid_red_sha_baseline_ok_added_pragma_rejects(tmp_path, monkeypatch):
    """AC8: regression w/ valid red_sha — baseline pragma (non-pragma line changed) ⇒ ok; an added unauthorized pragma ⇒ E_SEC_LINT_SELF_EXEMPT."""
    # sub-case 1: baseline pragma present at red_sha, only a non-pragma line changed → ok
    repo = _real_repo(tmp_path, "repo_baseline")
    _init_repo(repo)
    base_sha = _commit_file(repo, "a.py", "# security-lint: allow legacy-baseline-reason\nx = 1\n", "base")
    _write_file(repo, "a.py", "# security-lint: allow legacy-baseline-reason\nx = 2\n")

    _capture_events(monkeypatch)
    ctx = _make_lint_ctx(repo)
    prev = _make_lint_prev(red_commit_sha=base_sha)
    result_baseline = phase_5_implement._verify_security_lint(ctx, prev)
    assert result_baseline.error_code != "E_SEC_LINT_SELF_EXEMPT", (
        f"baseline pragma must NOT be rejected, got error_code={result_baseline.error_code!r}"
    )

    # sub-case 2: pragma newly added since red_sha, unauthorized → reject
    repo2 = _real_repo(tmp_path, "repo_added")
    _init_repo(repo2)
    base_sha2 = _commit_file(repo2, "a.py", "x = 1\n", "base")
    _write_file(repo2, "a.py", "# security-lint: allow newly-added-reason\nx = 2\n")

    _capture_events(monkeypatch)
    ctx2 = _make_lint_ctx(repo2)
    prev2 = _make_lint_prev(red_commit_sha=base_sha2)
    result_added = phase_5_implement._verify_security_lint(ctx2, prev2)
    assert result_added.error_code == "E_SEC_LINT_SELF_EXEMPT", (
        f"added unauthorized pragma must be rejected, got error_code={result_added.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# f624e3fb (P6) — green_commit scans a git-diff-derived (test-retained) set
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac09_commit_green_code_scans_test_file_in_manifest(tmp_path):
    """AC9: manifest [src/module.py, tests/test_x.py] where the NEW test file carries gitleaks:allow ⇒ E_BOUNDARY_SUPPRESSION (today: passes)."""
    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
    _write_file(repo, "src/module.py", "def foo():\n    return 2\n")  # clean prod change
    _write_file(repo, "tests/test_x.py", "SECRET='x'  # gitleaks:allow\n")  # new test, suppression token

    scratchpad = Path(os.path.realpath(tmp_path)) / "scratch"
    scratchpad.mkdir()
    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_green_prev(
        red_commit_sha=red_sha,
        worker_written_paths=["src/module.py", "tests/test_x.py"],
        manifest_source="harness_tool_record",
    )

    commits_before = _rev_count(repo)
    result = phase_5_implement._commit_green_code(ctx, prev)
    commits_after = _rev_count(repo)

    assert result.status == "error", f"expected error, got {result.status!r} (data={result.data!r})"
    assert result.error_code == "E_BOUNDARY_SUPPRESSION", f"expected E_BOUNDARY_SUPPRESSION, got {result.error_code!r}"
    assert commits_after == commits_before, f"no commit on rejection; before={commits_before} after={commits_after}"


def test_ac10_clean_test_file_commits_only_prod_paths_test_untracked(tmp_path):
    """AC10: regression — clean test file ⇒ ok, exactly one new commit, HEAD carries src/module.py but NOT the test file, test file stays '??' (git add not widened)."""
    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
    _write_file(repo, "src/module.py", "def foo():\n    return 2\n")
    _write_file(repo, "tests/test_x.py", "def test_x():\n    assert True\n")  # clean, no token

    scratchpad = Path(os.path.realpath(tmp_path)) / "scratch"
    scratchpad.mkdir()
    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_green_prev(
        red_commit_sha=red_sha,
        worker_written_paths=["src/module.py", "tests/test_x.py"],
        manifest_source="harness_tool_record",
    )

    commits_before = _rev_count(repo)
    result = phase_5_implement._commit_green_code(ctx, prev)
    commits_after = _rev_count(repo)

    assert result.status == "ok", f"expected ok, got {result.status!r} err={getattr(result, 'error_code', None)!r}"
    assert commits_after == commits_before + 1, f"expected exactly one new commit; {commits_before}->{commits_after}"
    committed = _head_name_only(repo)
    assert "src/module.py" in committed, f"HEAD must carry src/module.py, got {committed!r}"
    assert "tests/test_x.py" not in committed, f"HEAD must NOT carry the test file (git add not widened), got {committed!r}"
    assert any(ln.startswith("??") and ln.endswith("tests/test_x.py") for ln in _porcelain(repo, "tests/test_x.py")), (
        f"test file must remain untracked (??), porcelain={_porcelain(repo, 'tests/test_x.py')!r}"
    )


def test_ac11_empty_prod_paths_manifest_still_scans_test_file(tmp_path):
    """AC11: manifest of ONLY test paths (prod_paths empty) where a test file carries a new gitleaks:allow ⇒ E_BOUNDARY_SUPPRESSION, NOT the empty-manifest early-ok."""
    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
    _write_file(repo, "tests/test_x.py", "SECRET='x'  # gitleaks:allow\n")  # only test path changed

    scratchpad = Path(os.path.realpath(tmp_path)) / "scratch"
    scratchpad.mkdir()
    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_green_prev(
        red_commit_sha=red_sha,
        worker_written_paths=["tests/test_x.py"],  # prod_paths strips to empty
        manifest_source="harness_tool_record",
    )

    result = phase_5_implement._commit_green_code(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status!r} (data={result.data!r})"
    assert result.error_code == "E_BOUNDARY_SUPPRESSION", (
        f"expected E_BOUNDARY_SUPPRESSION (not empty-manifest early-ok), got {result.error_code!r}"
    )


def test_ac12_green_complete_resume_scans_git_diff_not_stripped_manifest(tmp_path):
    """AC12: green_complete_resume entry (green_resume_paths=[src/module.py], test-stripped) but worktree also has a new test file w/ gitleaks:allow ⇒ E_BOUNDARY_SUPPRESSION."""
    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
    _write_file(repo, "src/module.py", "def foo():\n    return 2\n")  # clean prod change
    _write_file(repo, "tests/test_x.py", "SECRET='x'  # gitleaks:allow\n")  # test file NOT in resume manifest

    scratchpad = Path(os.path.realpath(tmp_path)) / "scratch"
    scratchpad.mkdir()
    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_green_prev(
        red_commit_sha=red_sha,
        green_complete_resume=True,
        green_resume_paths=["src/module.py"],  # test-stripped upstream
    )

    result = phase_5_implement._commit_green_code(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status!r} (data={result.data!r})"
    assert result.error_code == "E_BOUNDARY_SUPPRESSION", (
        f"boundary_scan_paths must be git-diff-derived, not the stripped resume manifest; got {result.error_code!r}"
    )


def test_ac13_ac9_runs_real_unmocked_boundary_module_identity(tmp_path):
    """AC13 (§1l): AC9 flow runs the real unmocked _commit_green_code + real authored_boundary (module identity asserted); commit count unchanged on rejection."""
    assert phase_5_implement.authored_boundary is authored_boundary, (
        "phase_5_implement must use the real lib.authored_boundary module (no mock)"
    )

    repo = _real_repo(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "src/module.py", "def foo():\n    return 1\n", "RED: add module")
    _write_file(repo, "src/module.py", "def foo():\n    return 2\n")
    _write_file(repo, "tests/test_x.py", "SECRET='x'  # gitleaks:allow\n")

    scratchpad = Path(os.path.realpath(tmp_path)) / "scratch"
    scratchpad.mkdir()
    ctx = _make_green_ctx(scratchpad, str(repo))
    prev = _make_green_prev(
        red_commit_sha=red_sha,
        worker_written_paths=["src/module.py", "tests/test_x.py"],
        manifest_source="harness_tool_record",
    )

    commits_before = _rev_count(repo)
    result = phase_5_implement._commit_green_code(ctx, prev)
    commits_after = _rev_count(repo)

    assert result.error_code == "E_BOUNDARY_SUPPRESSION", f"expected E_BOUNDARY_SUPPRESSION, got {result.error_code!r}"
    assert commits_after == commits_before, f"commit count must be unchanged on rejection; {commits_before}->{commits_after}"


# ═══════════════════════════════════════════════════════════════════════════════
# 0b95d45a (P7) — apply_surgical_patches suppression + pragma-allow guard
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac14_apply_surgical_patches_rejects_new_suppression_token():
    """AC14: apply_surgical_patches whose `new` introduces gitleaks:allow ⇒ (None, meta) reason=='suppression_token_added', non-empty tokens, finding_id echoed."""
    from bytedigger_engine.lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    base = "AAA\nOLD_FRAG\nCCC\n"
    patches = [{"finding_id": "F1", "old": "OLD_FRAG", "new": "OLD_FRAG  # gitleaks:allow"}]
    patched, meta = apply_surgical_patches(base, patches)

    assert patched is None, f"suppression-token-adding patch must fail closed, got patched={patched!r}"
    assert meta.get("reason") == "suppression_token_added", f"expected reason=suppression_token_added, got {meta!r}"
    assert meta.get("tokens"), f"expected non-empty tokens, got {meta!r}"
    assert meta.get("finding_id") == "F1", f"expected finding_id echoed, got {meta!r}"


def test_ac15_apply_surgical_patches_rejects_new_pragma_allow_line():
    """AC15: apply_surgical_patches whose `new` introduces a security-lint-pragma-allow line ⇒ same fail-closed shape, non-empty tokens (self-authorization loop)."""
    from bytedigger_engine.lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    base = "AAA\nOLD_FRAG\nCCC\n"
    patches = [{"finding_id": "F2", "old": "OLD_FRAG", "new": "OLD_FRAG\nsecurity-lint-pragma-allow: src/x.py"}]
    patched, meta = apply_surgical_patches(base, patches)

    assert patched is None, f"pragma-allow-adding patch must fail closed, got patched={patched!r}"
    assert meta.get("reason") == "suppression_token_added", f"expected reason=suppression_token_added, got {meta!r}"
    assert meta.get("tokens"), f"expected non-empty tokens, got {meta!r}"
    assert meta.get("finding_id") == "F2", f"expected finding_id echoed, got {meta!r}"


def test_ac16_apply_surgical_patches_clean_patch_unchanged():
    """AC16: regression — a clean patch still returns (patched_text, {'n_patches': N}) exact dict, untouched bytes byte-identical."""
    from bytedigger_engine.lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    base = "AAA one\nBBB two\nCCC three\n"
    patches = [{"finding_id": "F1", "old": "BBB two", "new": "BBB TWO"}]
    patched, meta = apply_surgical_patches(base, patches)

    assert patched == "AAA one\nBBB TWO\nCCC three\n", f"clean patch must apply exactly, got {patched!r}"
    assert meta == {"n_patches": 1}, f"expected exact meta {{'n_patches': 1}}, got {meta!r}"
