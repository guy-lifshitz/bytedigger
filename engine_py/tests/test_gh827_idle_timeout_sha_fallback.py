"""RED tests — GH827 (a) anthropic_api idle_timeout wiring + (b) phase_5
_verify_green_typecheck sha_fallback telemetry.

Spec: SHARED/memory/Decisions/2026-07-15_GH827_idle_timeout_sha_fallback_fakeclock_spec.md

Covers A1-A5 (anthropic_api_backend idle_timeout_sec -> urlopen timeout= wiring,
gate_label pass-through) and B1-B3 (_verify_green_typecheck green_typecheck_sha_fallback
telemetry key). C-ACs (test_llm_subprocess_23680DDA.py sleep(30) removal) are
orchestrator-owned, not covered here.

§1q: both UUT symbols (anthropic_api_backend, _verify_green_typecheck) already
exist today — plain top-level imports collect cleanly. Failures happen at
ASSERT time (captured kwargs / dict keys), never at collection time.

§1l stub-passability: neither anthropic_api_backend nor _verify_green_typecheck
is patched/mocked. Only seams are patched: urllib.request.urlopen (A-ACs) and
phase_5_implement.bounded_run (B-ACs) plus real git repos on disk (B-ACs).

Pre-GREEN predict: A1, A3, A4 (gate_label present branch), A5 FAIL because
idle_timeout_sec / gate_label are currently accepted-and-ignored (anthropic_api.py
L142/144, docstring L149-150; no `timeout=effective_timeout` logic, no
`gate_label` key in base_data). A2 (disabled cases) and A4's gate_label=None
branch PASS today (already timeout_sec / already absent) — correctness guards.
B1 and B3 FAIL because `_verify_green_typecheck` never sets
`green_typecheck_sha_fallback` on any success path today. B2 (key absent when
red_sha given) PASSES today (key was never added on any path) — correctness
guard for post-GREEN behavior.
"""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine.workflows import phase_5_implement
from bytedigger_engine.lib.reference_backends.anthropic_api import anthropic_api_backend  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers — anthropic_api_backend (A-ACs)
# ---------------------------------------------------------------------------

def _make_capturing_urlopen(response_body: dict) -> Any:
    """Fake urlopen that records (request, timeout) for every call and
    returns a successful 200 response with response_body as JSON."""
    encoded = json.dumps(response_body).encode("utf-8")
    captured: list[dict[str, Any]] = []

    @contextmanager
    def _fake_urlopen(request, timeout=None):
        captured.append({"request": request, "timeout": timeout})
        resp = MagicMock()
        resp.read.return_value = encoded
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        yield resp

    _fake_urlopen.captured = captured  # type: ignore[attr-defined]
    return _fake_urlopen


_OK_BODY = {"content": [{"type": "text", "text": "PONG"}]}


def _call_backend(monkeypatch, *, timeout_sec, idle_timeout_sec=None, gate_label=None,
                   extra_data=None, step_name="gh827-test"):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-gh827")
    fake_urlopen = _make_capturing_urlopen(_OK_BODY)
    with patch("urllib.request.urlopen", fake_urlopen):
        result = anthropic_api_backend(
            prompt="ping",
            model="haiku",
            timeout_sec=timeout_sec,
            step_name=step_name,
            idle_timeout_sec=idle_timeout_sec,
            gate_label=gate_label,
            extra_data=extra_data,
        )
    return result, fake_urlopen.captured


# ---------------------------------------------------------------------------
# A1 — idle wired: idle_timeout_sec=5 < timeout_sec=100 -> urlopen timeout=5
# ---------------------------------------------------------------------------

def test_a1_idle_timeout_wins_over_outer_timeout(monkeypatch):
    result, captured = _call_backend(monkeypatch, timeout_sec=100, idle_timeout_sec=5)
    assert result.status == "ok", f"A1 setup failed: {result.error_code!r}"
    assert len(captured) == 1, f"A1: expected exactly one urlopen call; got {captured!r}"
    assert captured[0]["timeout"] == 5, (
        f"A1: urlopen must be called with timeout=5 (idle_timeout_sec wired); "
        f"got timeout={captured[0]['timeout']!r}"
    )


# ---------------------------------------------------------------------------
# A2 — idle disabled: None / 0 / -1 / True -> timeout=timeout_sec
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("idle_timeout_sec", [None, 0, -1, True])
def test_a2_idle_disabled_falls_back_to_outer_timeout(monkeypatch, idle_timeout_sec):
    result, captured = _call_backend(monkeypatch, timeout_sec=10, idle_timeout_sec=idle_timeout_sec)
    assert result.status == "ok", f"A2 setup failed: {result.error_code!r}"
    assert len(captured) == 1, f"A2: expected exactly one urlopen call; got {captured!r}"
    assert captured[0]["timeout"] == 10, (
        f"A2: idle_timeout_sec={idle_timeout_sec!r} must be treated as disabled -> "
        f"timeout must equal outer timeout_sec=10; got timeout={captured[0]['timeout']!r}"
    )


# ---------------------------------------------------------------------------
# A3 — idle >= outer: timeout_sec=5, idle_timeout_sec=100 -> timeout=5
# ---------------------------------------------------------------------------

def test_a3_outer_timeout_wins_when_smaller(monkeypatch):
    result, captured = _call_backend(monkeypatch, timeout_sec=5, idle_timeout_sec=100)
    assert result.status == "ok", f"A3 setup failed: {result.error_code!r}"
    assert len(captured) == 1, f"A3: expected exactly one urlopen call; got {captured!r}"
    assert captured[0]["timeout"] == 5, (
        f"A3: min(outer, idle) must pick the smaller outer timeout_sec=5; "
        f"got timeout={captured[0]['timeout']!r}"
    )


# ---------------------------------------------------------------------------
# A4 — gate_label present -> data['gate_label']; None -> key absent
# ---------------------------------------------------------------------------

def test_a4_gate_label_present_in_success_data(monkeypatch):
    result, _ = _call_backend(monkeypatch, timeout_sec=10, gate_label="opus_validate")
    assert result.status == "ok", f"A4 setup failed: {result.error_code!r}"
    assert result.data is not None, "A4: data must not be None on ok result"
    assert result.data.get("gate_label") == "opus_validate", (
        f"A4: data['gate_label'] must be 'opus_validate' when gate_label kwarg is set; "
        f"got {result.data.get('gate_label')!r}, data keys={list(result.data.keys())!r}"
    )


def test_a4_gate_label_none_is_absent_from_success_data(monkeypatch):
    result, _ = _call_backend(monkeypatch, timeout_sec=10, gate_label=None)
    assert result.status == "ok", f"A4 setup failed: {result.error_code!r}"
    assert result.data is not None, "A4: data must not be None on ok result"
    assert "gate_label" not in result.data, (
        f"A4: 'gate_label' key must be ABSENT when gate_label kwarg is None; "
        f"got data keys={list(result.data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# A5 — extra_data wins on collision with gate_label
# ---------------------------------------------------------------------------

def test_a5_extra_data_wins_over_gate_label(monkeypatch):
    result, _ = _call_backend(
        monkeypatch, timeout_sec=10, gate_label="Y", extra_data={"gate_label": "X"},
    )
    assert result.status == "ok", f"A5 setup failed: {result.error_code!r}"
    assert result.data is not None, "A5: data must not be None on ok result"
    assert result.data.get("gate_label") == "X", (
        f"A5: extra_data must win merge-order collision on 'gate_label' "
        f"(extra_data={{'gate_label':'X'}} vs gate_label kwarg='Y'); "
        f"got {result.data.get('gate_label')!r}"
    )


# ---------------------------------------------------------------------------
# Shared helpers — _verify_green_typecheck (B-ACs)
# ---------------------------------------------------------------------------

def _make_ctx(git_cwd: str, build_class: str = "SIMPLE") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd, "build_class": build_class},
        question="q",
        session_id="test-gh827",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(git_cwd: str, red_commit_sha: str | None = None, cycle: int = 1,
                build_class: str = "SIMPLE") -> StepResult:
    data: dict[str, Any] = {"git_cwd": git_cwd, "build_class": build_class, "cycle": cycle}
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    return StepResult(status="ok", data=data, duration_ms=0, step_name="verify_green_passing")


def _init_git_repo(repo: Path) -> None:
    """Minimal real git repo (§1j: realpath for macOS /var/folders)."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_clean_committed_repo(tmp_path: Path) -> Path:
    """Repo with one committed baseline file and a clean working tree —
    no red_sha given => no_production_files skip path (green_paths==[])."""
    repo = Path(os.path.realpath(str(tmp_path / "repo")))
    _init_git_repo(repo)
    (repo / "placeholder.py").write_text("x: int = 1\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    return repo


def _make_repo_with_red_sha_no_further_changes(tmp_path: Path) -> tuple[Path, str]:
    """Repo where red_sha == HEAD and nothing changed since -> green_paths==[]
    on the red_sha (non-fallback) branch."""
    repo = Path(os.path.realpath(str(tmp_path / "repo")))
    _init_git_repo(repo)
    (repo / "placeholder.py").write_text("x: int = 1\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "red commit"], cwd=repo, check=True)
    red_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return repo, red_sha


def _make_repo_with_dirty_py_file(tmp_path: Path) -> Path:
    """Repo with a committed baseline, plus one dirty untracked .py file so
    the no-red_sha fallback path finds a non-empty green_paths list."""
    repo = Path(os.path.realpath(str(tmp_path / "repo")))
    _init_git_repo(repo)
    (repo / "placeholder.py").write_text("x: int = 1\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    (repo / "changed_module.py").write_text("y: int = 2\n")
    return repo


def _fake_clean_mypy_run(*_args, **_kwargs):
    return subprocess.CompletedProcess(
        args=["mypy"], returncode=0, stdout="Success: no issues found in 1 source file\n", stderr="",
    )


_FINDING_STDOUT = "changed_module.py:1: error: Incompatible types  [assignment]\n"


def _fake_findings_mypy_run(*_args, **_kwargs):
    return subprocess.CompletedProcess(
        args=["mypy"], returncode=1, stdout=_FINDING_STDOUT, stderr="",
    )


# ---------------------------------------------------------------------------
# B1 — no red_sha (fallback), no_production_files skip path -> key True
# ---------------------------------------------------------------------------

@pytest.mark.skipif(subprocess.run(["which", "git"], capture_output=True).returncode != 0,
                     reason="git not available")
def test_b1_sha_fallback_true_on_no_production_files_skip(tmp_path: Path) -> None:
    repo = _make_clean_committed_repo(tmp_path)
    ctx = _make_ctx(str(repo))
    prev = _make_prev(str(repo), red_commit_sha=None, cycle=1)

    result = phase_5_implement._verify_green_typecheck(ctx, prev)

    assert result.status == "ok", f"B1 setup failed: {result.error_code!r}"
    assert isinstance(result.data, dict), "B1: result.data must be a dict"
    assert result.data.get("green_typecheck_sha_fallback") is True, (
        f"B1: no red_sha -> fallback path must set green_typecheck_sha_fallback=True; "
        f"got data keys={list(result.data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# B2 — red_sha present -> key ABSENT
# ---------------------------------------------------------------------------

@pytest.mark.skipif(subprocess.run(["which", "git"], capture_output=True).returncode != 0,
                     reason="git not available")
def test_b2_sha_fallback_key_absent_when_red_sha_given(tmp_path: Path) -> None:
    repo, red_sha = _make_repo_with_red_sha_no_further_changes(tmp_path)
    ctx = _make_ctx(str(repo))
    prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

    result = phase_5_implement._verify_green_typecheck(ctx, prev)

    assert result.status == "ok", f"B2 setup failed: {result.error_code!r}"
    assert isinstance(result.data, dict), "B2: result.data must be a dict"
    assert "green_typecheck_sha_fallback" not in result.data, (
        f"B2: red_sha given (no fallback) -> key must be ABSENT; "
        f"got data keys={list(result.data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# B3 — no-findings ok return carries the key when fallback fired
# ---------------------------------------------------------------------------

@pytest.mark.skipif(subprocess.run(["which", "git"], capture_output=True).returncode != 0,
                     reason="git not available")
def test_b3_sha_fallback_true_on_no_findings_ok_return(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo_with_dirty_py_file(tmp_path)
    monkeypatch.setattr(phase_5_implement, "bounded_run", _fake_clean_mypy_run)

    ctx = _make_ctx(str(repo))
    prev = _make_prev(str(repo), red_commit_sha=None, cycle=1)

    result = phase_5_implement._verify_green_typecheck(ctx, prev)

    assert result.status == "ok", f"B3 setup failed: {result.error_code!r}"
    assert isinstance(result.data, dict), "B3: result.data must be a dict"
    assert result.data.get("typecheck_findings") == [], (
        f"B3 setup: expected clean typecheck_findings==[]; got {result.data.get('typecheck_findings')!r}"
    )
    assert result.data.get("green_typecheck_sha_fallback") is True, (
        f"B3: no-findings ok return with fallback active must carry "
        f"green_typecheck_sha_fallback=True; got data keys={list(result.data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# B4 — pre-existing-warnings ok return (findings present, would_block=False)
#      carries the key when fallback fired
# ---------------------------------------------------------------------------

@pytest.mark.skipif(subprocess.run(["which", "git"], capture_output=True).returncode != 0,
                     reason="git not available")
def test_b4_sha_fallback_true_on_preexisting_warnings_ok_return(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = _make_repo_with_dirty_py_file(tmp_path)
    monkeypatch.setattr(phase_5_implement, "bounded_run", _fake_findings_mypy_run)
    # baseline_count == current_count (1 finding parsed from _FINDING_STDOUT) ->
    # classify() == "preexisting_only" -> is_regression=False -> would_block=False
    # -> hits the pre-existing-warnings ok return (~L4104), NOT the recoverable gate.
    monkeypatch.setattr(phase_5_implement, "_compute_baseline_typecheck_count", lambda *a, **k: 1)

    ctx = _make_ctx(str(repo))
    prev = _make_prev(str(repo), red_commit_sha=None, cycle=1)

    result = phase_5_implement._verify_green_typecheck(ctx, prev)

    assert result.status == "ok", f"B4 setup failed: {result.error_code!r}"
    assert isinstance(result.data, dict), "B4: result.data must be a dict"
    assert result.data.get("green_typecheck_warnings"), (
        f"B4 setup: expected non-empty green_typecheck_warnings (preexisting-only path); "
        f"got {result.data.get('green_typecheck_warnings')!r}"
    )
    assert result.data.get("green_typecheck_sha_fallback") is True, (
        f"B4: pre-existing-warnings ok return with fallback active must carry "
        f"green_typecheck_sha_fallback=True; got data keys={list(result.data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# B5 — blocking recoverable-gate path (would_block=True) carries the key
#      in the StepResult.data forwarded via gated_step_result
# ---------------------------------------------------------------------------

@pytest.mark.skipif(subprocess.run(["which", "git"], capture_output=True).returncode != 0,
                     reason="git not available")
def test_b5_sha_fallback_true_on_blocking_recoverable_gate_result(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = _make_repo_with_dirty_py_file(tmp_path)
    monkeypatch.setattr(phase_5_implement, "bounded_run", _fake_findings_mypy_run)
    # baseline_count=0 < current_count=1 -> classify()=="net_new_regression" ->
    # is_regression=True, enforce defaults True -> would_block=True -> falls
    # through to RecoverableGateMixin.gated_step_result(forwarded_data=fwd).
    monkeypatch.setattr(phase_5_implement, "_compute_baseline_typecheck_count", lambda *a, **k: 0)

    ctx = _make_ctx(str(repo))
    prev = _make_prev(str(repo), red_commit_sha=None, cycle=1)

    result = phase_5_implement._verify_green_typecheck(ctx, prev)

    assert result.error_code == "E_GREEN_TYPECHECK_RETRY", (
        f"B5 setup: expected recoverable-retry path; got status={result.status!r} "
        f"error_code={result.error_code!r}"
    )
    assert isinstance(result.data, dict), "B5: result.data must be a dict"
    assert result.data.get("green_typecheck_findings"), (
        f"B5 setup: expected non-empty green_typecheck_findings on blocking path; "
        f"got {result.data.get('green_typecheck_findings')!r}"
    )
    assert result.data.get("green_typecheck_sha_fallback") is True, (
        f"B5: blocking recoverable-gate StepResult.data (forwarded via fwd dict) must "
        f"carry green_typecheck_sha_fallback=True; got data keys={list(result.data.keys())!r}"
    )
