"""RED tests for GH #640 — security-lint gate: reject self-exempt pragmas
added in the fix diff.

Contracts under test (phase_5_implement.py):
  _verify_security_lint(ctx, prev) -> StepResult          (EXISTS today)
  _classify_pragma_origin(rel, abs_, red_sha, git_cwd)     (NOT YET IMPLEMENTED)
  _parse_authorized_pragma_paths(spec_text)                (NOT YET IMPLEMENTED)

D1CF5FDF / §1q: the two not-yet-existing helpers are resolved via
getattr(module, name, None) INSIDE each test body — never imported at module
top level — so the file COLLECTS cleanly and each test FAILS at assert time,
never at collection time.

Spec: SHARED/memory/Decisions/2026-07-12_GH640_security_lint_self_exempt_spec.md
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402  (module exists; new helpers under test do not, yet)


# ─── shared helpers ──────────────────────────────────────────────────────────


def _make_ctx(git_cwd: Path, scratchpad_dir: Path | None = None, **org_extra) -> WorkflowContext:
    cfg = {"git_cwd": str(git_cwd)}
    if scratchpad_dir is not None:
        cfg["scratchpad_dir"] = str(scratchpad_dir)
    else:
        # MAJOR-2 fix: _resolve_scratchpad(ctx) raises ValueError when
        # org_config.scratchpad_dir is falsy — every test must supply one,
        # even a non-existent dummy path (the helper does not check
        # existence, only that the value is truthy).
        cfg["scratchpad_dir"] = str(git_cwd.parent / "scratchpad")
    cfg.update(org_extra)
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


def _make_prev(**extra) -> StepResult:
    data: dict = {
        "green_log_path": "tests/build-green-output.log",
        "spec_path": "scratchpad/spec.md",
        "red_log_path": "tests/build-red-output.log",
        "validation_doc_path": "scratchpad/validation.md",
        "green_status": "PASS",
    }
    data.update(extra)
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="verify_green_lint_rules",
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _git_init(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.email", "red@test")
    _git(path, "config", "user.name", "red")


def _git_commit_all(path: Path, msg: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-m", msg)
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _capture_events(monkeypatch) -> list:
    events: list = []
    monkeypatch.setattr(
        phase_5_implement, "_emit_safe", lambda et, payload: events.append((et, payload))
    )
    return events


# ─── A1/A2: self-exempt pragma added in diff, not authorized → reject ───────


def test_a1_pragma_added_in_diff_unauthorized_rejects(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "a.py").write_text("x = 1\n")
    base_sha = _git_commit_all(repo, "base")
    (repo / "a.py").write_text("# security-lint: allow suppressing-a-real-finding\nx = 2\n")

    _capture_events(monkeypatch)
    ctx = _make_ctx(repo)
    prev = _make_prev(red_commit_sha=base_sha)
    result = phase_5_implement._verify_security_lint(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status}"
    assert result.error_code == "E_SEC_LINT_SELF_EXEMPT", (
        f"expected E_SEC_LINT_SELF_EXEMPT, got {result.error_code!r}"
    )
    assert result.recoverable is False, f"expected recoverable False, got {result.recoverable!r}"


def test_a2_self_exempt_rejection_emits_event_with_path_reason_token(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "a.py").write_text("x = 1\n")
    base_sha = _git_commit_all(repo, "base")
    (repo / "a.py").write_text("# security-lint: allow suppressing-a-real-finding\nx = 2\n")

    events = _capture_events(monkeypatch)
    ctx = _make_ctx(repo)
    prev = _make_prev(red_commit_sha=base_sha)
    phase_5_implement._verify_security_lint(ctx, prev)

    rejects = [p for (et, p) in events if et == "security_lint_self_exempt_rejected"]
    assert len(rejects) == 1, f"expected exactly 1 security_lint_self_exempt_rejected event, got {events}"
    payload = rejects[0]
    assert payload.get("path") == "a.py", f"expected path=a.py, got {payload}"
    assert payload.get("reason"), f"expected non-empty reason, got {payload}"
    assert payload.get("token") == "security-lint: allow", f"expected token, got {payload}"


# ─── A3: pragma already present at red_sha (baseline) → not rejected ────────


def test_a3_baseline_pragma_not_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "a.py").write_text("# security-lint: allow legacy-baseline-reason\nx = 1\n")
    base_sha = _git_commit_all(repo, "base")
    # a.py itself must appear in the diff for its pragma to be classified —
    # change a NON-pragma line while the pragma line stays unchanged (baseline).
    (repo / "a.py").write_text("# security-lint: allow legacy-baseline-reason\nx = 2\n")

    events = _capture_events(monkeypatch)
    ctx = _make_ctx(repo)
    prev = _make_prev(red_commit_sha=base_sha)
    result = phase_5_implement._verify_security_lint(ctx, prev)

    assert result.error_code != "E_SEC_LINT_SELF_EXEMPT", (
        f"expected NOT rejected, got error_code={result.error_code!r}, status={result.status}"
    )
    authorized = [p for (et, p) in events if et == "security_lint_pragma_authorized"]
    assert any(p.get("reason") == "baseline" and p.get("path") == "a.py" for p in authorized), (
        f"expected security_lint_pragma_authorized reason=baseline for a.py, got {events}"
    )


# ─── A4: pragma added in diff BUT spec-authorized → not rejected ────────────


def test_a4_pragma_added_in_diff_spec_authorized_not_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "a.py").write_text("x = 1\n")
    base_sha = _git_commit_all(repo, "base")
    (repo / "a.py").write_text("# security-lint: allow spec-authorized-reason\nx = 2\n")

    scratchpad = tmp_path / "scratchpad"
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "build-spec.md").write_text(
        "## §5 Files in scope\nsecurity-lint-pragma-allow: a.py\n"
    )

    events = _capture_events(monkeypatch)
    ctx = _make_ctx(repo, scratchpad_dir=scratchpad)
    prev = _make_prev(red_commit_sha=base_sha)
    result = phase_5_implement._verify_security_lint(ctx, prev)

    assert result.error_code != "E_SEC_LINT_SELF_EXEMPT", (
        f"expected NOT rejected (spec-authorized), got error_code={result.error_code!r}, status={result.status}"
    )
    authorized = [p for (et, p) in events if et == "security_lint_pragma_authorized"]
    assert any(p.get("reason") == "spec_authorized" and p.get("path") == "a.py" for p in authorized), (
        f"expected security_lint_pragma_authorized reason=spec_authorized for a.py, got {events}"
    )


# ─── A5: _parse_authorized_pragma_paths is EXACT-match ──────────────────────


def test_a5_parse_authorized_pragma_paths_exact_match_no_glob():
    fn = getattr(phase_5_implement, "_parse_authorized_pragma_paths", None)
    assert fn is not None, "not implemented yet"

    spec_text = (
        "## §5 Files in scope\n"
        "security-lint-pragma-allow: foo/bar.py\n"
        "some other line\n"
    )
    authorized = fn(spec_text)

    assert "foo/bar.py" in authorized, f"expected foo/bar.py authorized, got {authorized}"
    assert "foo/bar_other.py" not in authorized, (
        f"expected foo/bar_other.py NOT authorized (exact-match only), got {authorized}"
    )
    assert "foo/" not in authorized and "foo/*" not in authorized, (
        f"expected no glob-style entries, got {authorized}"
    )


# ─── A6: red_sha falsy → indeterminate, gate REJECTS (GH1108 f9001011) ──────


def test_a6_classify_pragma_origin_falsy_red_sha_returns_indeterminate():
    fn = getattr(phase_5_implement, "_classify_pragma_origin", None)
    assert fn is not None, "not implemented yet"

    origin = fn("a.py", "/nonexistent/a.py", "", "/nonexistent")
    assert origin == "indeterminate", f"expected indeterminate, got {origin!r}"


def test_a6_gate_rejects_when_red_sha_falsy(tmp_path, monkeypatch):
    # GH1108 f9001011 (spec CC6D477B §1.2): a falsy red_sha yields an
    # `indeterminate` origin, which is now treated exactly like `added_in_diff`
    # — an unauthorized pragma REJECTS (fail-closed), no longer defers.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "a.py").write_text("x = 1\n")
    _git_commit_all(repo, "base")
    (repo / "a.py").write_text("# security-lint: allow some-reason\nx = 2\n")

    events = _capture_events(monkeypatch)
    ctx = _make_ctx(repo)
    prev = _make_prev(red_commit_sha=None)
    result = phase_5_implement._verify_security_lint(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status}"
    assert result.error_code == "E_SEC_LINT_SELF_EXEMPT", (
        f"expected REJECT when red_sha falsy + unauthorized pragma, got error_code={result.error_code!r}"
    )
    self_exempt = (result.data or {}).get("security_lint_self_exempt") or []
    assert any(h.get("origin") == "indeterminate" for h in self_exempt), (
        f"rejected hit must carry origin=='indeterminate', got {self_exempt!r}"
    )
    # The bare telemetry-only event may still be emitted alongside (§1.2
    # backward-compat), but must no longer be the sole outcome.
    indeterminate = [p for (et, p) in events if et == "security_lint_pragma_origin_indeterminate"]
    assert any(p.get("path") == "a.py" for p in indeterminate), (
        f"expected security_lint_pragma_origin_indeterminate for a.py, got {events}"
    )


# ─── A7: new file (absent at red_sha) carrying pragma → added_in_diff, reject


def test_a7_new_file_with_pragma_absent_at_red_sha_rejects(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "existing.py").write_text("x = 1\n")
    base_sha = _git_commit_all(repo, "base")
    # new, untracked file at HEAD carrying the pragma from birth
    (repo / "new_file.py").write_text("# security-lint: allow brand-new-file-reason\ny = 1\n")

    events = _capture_events(monkeypatch)
    ctx = _make_ctx(repo)
    prev = _make_prev(red_commit_sha=base_sha)
    result = phase_5_implement._verify_security_lint(ctx, prev)

    assert result.status == "error", f"expected error, got {result.status}"
    assert result.error_code == "E_SEC_LINT_SELF_EXEMPT", (
        f"expected E_SEC_LINT_SELF_EXEMPT, got {result.error_code!r}"
    )
    rejects = [p for (et, p) in events if et == "security_lint_self_exempt_rejected"]
    assert any(p.get("path") == "new_file.py" for p in rejects), (
        f"expected reject event for new_file.py, got {events}"
    )


# ─── A9 (§1ab idempotency): two identical calls → identical error_code and no
#      event-count accumulation across calls ──────────────────────────────────


def test_a9_repeated_calls_are_idempotent_no_event_accumulation(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "a.py").write_text("x = 1\n")
    base_sha = _git_commit_all(repo, "base")
    (repo / "a.py").write_text("# security-lint: allow suppressing-a-real-finding\nx = 2\n")

    ctx = _make_ctx(repo)

    events_1 = _capture_events(monkeypatch)
    prev = _make_prev(red_commit_sha=base_sha)
    result_1 = phase_5_implement._verify_security_lint(ctx, prev)
    rejects_1 = [p for (et, p) in events_1 if et == "security_lint_self_exempt_rejected"]

    events_2 = _capture_events(monkeypatch)
    prev2 = _make_prev(red_commit_sha=base_sha)
    result_2 = phase_5_implement._verify_security_lint(ctx, prev2)
    rejects_2 = [p for (et, p) in events_2 if et == "security_lint_self_exempt_rejected"]

    assert result_1.error_code == "E_SEC_LINT_SELF_EXEMPT", (
        f"expected E_SEC_LINT_SELF_EXEMPT on first call, got {result_1.error_code!r}"
    )
    assert result_2.error_code == result_1.error_code, (
        f"expected identical error_code across calls, got {result_1.error_code!r} vs {result_2.error_code!r}"
    )
    assert len(rejects_1) == len(rejects_2) == 1, (
        f"expected exactly 1 reject event per call (no accumulation), got "
        f"{len(rejects_1)} then {len(rejects_2)}"
    )
