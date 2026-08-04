"""GH483 RED tests — GREEN-complete resume seam for E_RED_NOT_FAILING.

Covers all 12 ACs from
SHARED/memory/Decisions/2026-07-10_GH483_green_complete_resume_spec.md.

D1CF5FDF collectability: `_detect_green_complete_resume`,
`_persist_green_complete_resume`, `_read_green_complete_resume`, and
`GREEN_COMPLETE_RESUME_RELPATH` do NOT exist yet on `phase_5_implement`.
They are fetched via `getattr(_p5, "name", None)` INSIDE each test body
(never imported at module top) so the file always COLLECTS cleanly and
fails at assert time, never at collection time.

Expected pre-GREEN outcome (spec §4):
  PASS (regression floors): AC6, AC10, AC12
  FAIL (not yet implemented): AC1, AC2, AC3, AC4, AC5, AC7, AC8, AC9, AC11

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement as _p5  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _parse_green_status  # noqa: E402  (already-shipped symbol)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _get_helper(name: str):
    fn = getattr(_p5, name, None)
    assert fn is not None, f"GH483 helper {name!r} not implemented yet on phase_5_implement"
    return fn


def _make_ctx(scratchpad_dir: str, extra_cfg: dict | None = None) -> WorkflowContext:
    org_config = {"scratchpad_dir": scratchpad_dir}
    if extra_cfg:
        org_config.update(extra_cfg)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question="test question",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(data: dict, step_name: str = "verify_red_fails_mechanically") -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name=step_name)


def _patch_emit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


def _write_spec_with_files(tmp_path: Path, files: list[str]) -> str:
    spec_path = tmp_path / "spec.md"
    body = "\n".join(f"- `{f}`" for f in files)
    spec_path.write_text(f"## Files\n{body}\n", encoding="utf-8")
    return str(spec_path)


RED_SHA = "a" * 40


# ─── AC1: gate off → _detect_green_complete_resume returns [] ────────────────


def test_ac1_detect_returns_empty_when_gate_off(monkeypatch, tmp_path):
    fn = _get_helper("_detect_green_complete_resume")
    monkeypatch.setenv("HAL_GREEN_COMPLETE_RESUME_GATE", "0")
    spec_path = _write_spec_with_files(tmp_path, ["foo/bar.py"])
    monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["foo/bar.py"])

    result = fn({"spec_path": spec_path, "red_commit_sha": RED_SHA}, str(tmp_path))

    assert result == [], f"AC1: expected [] with gate off, got {result!r}"


# ─── AC2: missing allowlist / missing red_commit_sha → [] ────────────────────


def test_ac2_detect_returns_empty_when_allowlist_missing(monkeypatch, tmp_path):
    fn = _get_helper("_detect_green_complete_resume")
    monkeypatch.delenv("HAL_GREEN_COMPLETE_RESUME_GATE", raising=False)
    monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["foo/bar.py"])

    # No spec_path at all → parse_spec_files_allowlist → None
    result = fn({"red_commit_sha": RED_SHA}, str(tmp_path))
    assert result == [], f"AC2a: expected [] with no spec_path, got {result!r}"


def test_ac2_detect_returns_empty_when_red_commit_sha_missing(monkeypatch, tmp_path):
    fn = _get_helper("_detect_green_complete_resume")
    monkeypatch.delenv("HAL_GREEN_COMPLETE_RESUME_GATE", raising=False)
    spec_path = _write_spec_with_files(tmp_path, ["foo/bar.py"])
    monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["foo/bar.py"])

    result = fn({"spec_path": spec_path}, str(tmp_path))
    assert result == [], f"AC2b: expected [] with missing red_commit_sha, got {result!r}"


# ─── AC3: exact sorted allowlist-matching non-test changed paths ─────────────


def test_ac3_detect_returns_exact_sorted_matching_non_test_paths(monkeypatch, tmp_path):
    fn = _get_helper("_detect_green_complete_resume")
    monkeypatch.delenv("HAL_GREEN_COMPLETE_RESUME_GATE", raising=False)
    spec_path = _write_spec_with_files(tmp_path, ["src/z_match.py", "src/a_match.py"])
    monkeypatch.setattr(
        _p5,
        "git_diff_files",
        lambda *a, **kw: [
            "src/z_match.py",       # matching prod
            "src/a_match.py",       # matching prod
            "src/nonmatch.py",      # non-matching prod
            "tests/src/z_match.py",  # under tests/ segment — excluded
            "test_something.py",   # test_*.py basename — excluded
        ],
    )

    result = fn({"spec_path": spec_path, "red_commit_sha": RED_SHA}, str(tmp_path))

    assert result == ["src/a_match.py", "src/z_match.py"], (
        f"AC3: expected sorted matching non-test paths, got {result!r}"
    )


# ─── AC4: git_diff_files raises → [] (no propagation) ────────────────────────


def test_ac4_detect_returns_empty_when_git_diff_raises(monkeypatch, tmp_path):
    fn = _get_helper("_detect_green_complete_resume")
    monkeypatch.delenv("HAL_GREEN_COMPLETE_RESUME_GATE", raising=False)
    spec_path = _write_spec_with_files(tmp_path, ["src/a.py"])

    def _raise(*a, **kw):
        raise RuntimeError("git blew up")

    monkeypatch.setattr(_p5, "git_diff_files", _raise)

    result = fn({"spec_path": spec_path, "red_commit_sha": RED_SHA}, str(tmp_path))
    assert result == [], f"AC4: expected [] on git_diff_files raise, got {result!r}"


# ─── AC5: _verify_red_fails_mechanically resume path ─────────────────────────


def test_ac5_verify_red_fails_mechanically_resumes_on_detection(monkeypatch, tmp_path):
    captured = _patch_emit(monkeypatch)
    monkeypatch.delenv("HAL_GREEN_COMPLETE_RESUME_GATE", raising=False)

    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir()
    spec_path = _write_spec_with_files(tmp_path, ["src/a.py"])

    fake_proc = SimpleNamespace(returncode=0, stdout="1 passed")
    monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: fake_proc)
    fake_dt = SimpleNamespace(exit_code=0, n_passed=1, n_failed=0)
    monkeypatch.setattr(_p5, "run_test_command", lambda *a, **kw: fake_dt)
    monkeypatch.setattr(
        _p5,
        "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [{"kind": "py", "argv": ["pytest", "t_x.py"], "paths": ["t_x.py"]}]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))
    monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["src/a.py"])

    ctx = _make_ctx(str(scratchpad))
    prev = _make_prev({
        "red_test_paths": ["t_x.py"],
        "spec_path": spec_path,
        "red_commit_sha": RED_SHA,
    })

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.status == "ok", (
        f"AC5: expected status='ok' (resume path), got {result.status!r}, "
        f"error_code={result.error_code!r}"
    )
    assert result.data.get("green_complete_resume") is True, (
        f"AC5: expected green_complete_resume=True in data, got {result.data!r}"
    )
    assert result.data.get("green_resume_paths") == ["src/a.py"], (
        f"AC5: expected green_resume_paths==['src/a.py'], got {result.data.get('green_resume_paths')!r}"
    )

    marker_path = scratchpad / "resume" / "green-complete-resume.json"
    assert marker_path.exists(), f"AC5: expected marker file at {marker_path}"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker.get("red_commit_sha") == RED_SHA, f"AC5: marker sha mismatch: {marker!r}"
    assert marker.get("paths") == ["src/a.py"], f"AC5: marker paths mismatch: {marker!r}"

    detected_events = [e for e in captured if e["type"] == "green_complete_resume_detected"]
    assert detected_events, (
        f"AC5: expected 'green_complete_resume_detected' event, got types "
        f"{[e['type'] for e in captured]}"
    )


# ─── AC6 (regression floor): all passing + no detection → E_RED_NOT_FAILING ──


def test_ac6_regression_floor_all_passing_no_resume_gives_error(monkeypatch, tmp_path):
    _patch_emit(monkeypatch)

    fake_proc = SimpleNamespace(returncode=0, stdout="1 passed")
    monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: fake_proc)
    fake_dt = SimpleNamespace(exit_code=0, n_passed=1, n_failed=0)
    monkeypatch.setattr(_p5, "run_test_command", lambda *a, **kw: fake_dt)
    monkeypatch.setattr(
        _p5,
        "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [{"kind": "py", "argv": ["pytest", "t_x.py"], "paths": ["t_x.py"]}]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx(str(tmp_path / "scratchpad"))
    # NO spec_path / no red_commit_sha → detection is [] regardless of GH483 impl.
    prev = _make_prev({"red_test_paths": ["t_x.py"]})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", (
        f"AC6: expected E_RED_NOT_FAILING (regression floor), got "
        f"status={result.status!r}, error_code={result.error_code!r}"
    )
    assert "all passed" in (result.error or "") and "RED is fake" in (result.error or ""), (
        f"AC6: expected unchanged error message wording, got {result.error!r}"
    )


# ─── AC7: persist raises OSError → fail-closed to E_RED_NOT_FAILING ──────────


def test_ac7_persist_oserror_falls_through_to_legacy_error(monkeypatch, tmp_path):
    _patch_emit(monkeypatch)
    monkeypatch.delenv("HAL_GREEN_COMPLETE_RESUME_GATE", raising=False)

    spec_path = _write_spec_with_files(tmp_path, ["src/a.py"])

    fake_proc = SimpleNamespace(returncode=0, stdout="1 passed")
    monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: fake_proc)
    fake_dt = SimpleNamespace(exit_code=0, n_passed=1, n_failed=0)
    monkeypatch.setattr(_p5, "run_test_command", lambda *a, **kw: fake_dt)
    monkeypatch.setattr(
        _p5,
        "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [{"kind": "py", "argv": ["pytest", "t_x.py"], "paths": ["t_x.py"]}]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))
    monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["src/a.py"])

    persist_fn = getattr(_p5, "_persist_green_complete_resume", None)
    assert persist_fn is not None, "GH483 helper _persist_green_complete_resume not implemented yet"

    def _raise_oserror(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(_p5, "_persist_green_complete_resume", _raise_oserror)

    ctx = _make_ctx(str(tmp_path / "scratchpad"))
    prev = _make_prev({
        "red_test_paths": ["t_x.py"],
        "spec_path": spec_path,
        "red_commit_sha": RED_SHA,
    })

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", (
        f"AC7: expected fail-closed E_RED_NOT_FAILING on persist OSError, got "
        f"status={result.status!r}, error_code={result.error_code!r}"
    )


# ─── AC8: _read_green_complete_resume 4 sub-cases ────────────────────────────


def test_ac8_read_returns_paths_on_matching_sha(tmp_path):
    fn = _get_helper("_read_green_complete_resume")
    relpath_fn_marker = getattr(_p5, "GREEN_COMPLETE_RESUME_RELPATH", None)
    assert relpath_fn_marker is not None, "GH483 constant GREEN_COMPLETE_RESUME_RELPATH not implemented yet"

    marker_file = tmp_path / relpath_fn_marker
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(json.dumps({"red_commit_sha": RED_SHA, "paths": ["a.py", "b.py"]}), encoding="utf-8")

    result = fn(str(tmp_path), RED_SHA)
    assert result == ["a.py", "b.py"], f"AC8a: expected paths list, got {result!r}"


def test_ac8_read_returns_none_when_file_missing(tmp_path):
    fn = _get_helper("_read_green_complete_resume")
    result = fn(str(tmp_path), RED_SHA)
    assert result is None, f"AC8b: expected None when marker file missing, got {result!r}"


def test_ac8_read_returns_none_on_malformed_json(tmp_path):
    fn = _get_helper("_read_green_complete_resume")
    relpath = getattr(_p5, "GREEN_COMPLETE_RESUME_RELPATH", None)
    assert relpath is not None, "GH483 constant GREEN_COMPLETE_RESUME_RELPATH not implemented yet"
    marker_file = tmp_path / relpath
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text("{not valid json", encoding="utf-8")

    result = fn(str(tmp_path), RED_SHA)
    assert result is None, f"AC8c: expected None on malformed JSON, got {result!r}"


def test_ac8_read_returns_none_on_sha_mismatch(tmp_path):
    fn = _get_helper("_read_green_complete_resume")
    relpath = getattr(_p5, "GREEN_COMPLETE_RESUME_RELPATH", None)
    assert relpath is not None, "GH483 constant GREEN_COMPLETE_RESUME_RELPATH not implemented yet"
    marker_file = tmp_path / relpath
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(json.dumps({"red_commit_sha": "b" * 40, "paths": ["a.py"]}), encoding="utf-8")

    result = fn(str(tmp_path), RED_SHA)
    assert result is None, f"AC8d: expected None on sha mismatch, got {result!r}"


def test_ac8_read_returns_none_when_paths_not_list(tmp_path):
    fn = _get_helper("_read_green_complete_resume")
    relpath = getattr(_p5, "GREEN_COMPLETE_RESUME_RELPATH", None)
    assert relpath is not None, "GH483 constant GREEN_COMPLETE_RESUME_RELPATH not implemented yet"
    marker_file = tmp_path / relpath
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(json.dumps({"red_commit_sha": RED_SHA, "paths": "not-a-list"}), encoding="utf-8")

    result = fn(str(tmp_path), RED_SHA)
    assert result is None, f"AC8e: expected None when paths is not a list, got {result!r}"


# ─── AC9: _invoke_green_llm skips subprocess when marker present ────────────


def _make_green_prev_data(extra: dict | None = None) -> dict:
    data = {
        "prompt": "do the thing",
        "log_path": "/tmp/green.log",
        "spec_path": "/tmp/spec.md",
        "red_log_path": "/tmp/red.log",
        "validation_doc_path": "/tmp/validation.md",
        "verdict": "APPROVED",
        "red_commit_sha": RED_SHA,
        "red_test_paths": ["t_x.py"],
        "cycle_count": 1,
    }
    if extra:
        data.update(extra)
    return data


def test_ac9_invoke_green_llm_skips_subprocess_when_marker_present(monkeypatch, tmp_path):
    captured = _patch_emit(monkeypatch)
    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir()

    def _raise_if_called(*a, **kw):
        raise AssertionError("invoke_llm_subprocess must NOT be called when marker present")

    monkeypatch.setattr(_p5, "invoke_llm_subprocess", _raise_if_called)
    monkeypatch.setattr(_p5, "_resolve_scratchpad", lambda ctx: scratchpad)
    monkeypatch.setattr(_p5, "_resolve_worktree_root", lambda ctx, sp: sp)
    monkeypatch.setattr(_p5, "_maybe_emit_cross_tree_warning", lambda result, root: result)

    read_fn = getattr(_p5, "_read_green_complete_resume", None)
    assert read_fn is not None, "GH483 helper _read_green_complete_resume not implemented yet"
    monkeypatch.setattr(_p5, "_read_green_complete_resume", lambda sp, sha: ["src/a.py"])

    ctx = _make_ctx(str(scratchpad))
    prev = _make_prev(_make_green_prev_data(), step_name="build_green_prompt")

    result = _p5._invoke_green_llm(ctx, prev)

    assert result.status == "ok", f"AC9: expected status=ok, got {result.status!r}"
    assert "GREEN COMPLETE" in result.data.get("raw_response", ""), (
        f"AC9: expected 'GREEN COMPLETE' marker in raw_response, got {result.data!r}"
    )
    assert result.data.get("tokens_out") == 0, f"AC9: expected tokens_out==0, got {result.data!r}"
    assert result.data.get("green_complete_resume") is True, (
        f"AC9: expected green_complete_resume True, got {result.data!r}"
    )
    assert result.data.get("green_resume_paths") == ["src/a.py"], (
        f"AC9: expected green_resume_paths passthrough, got {result.data!r}"
    )
    skip_events = [e for e in captured if e["type"] == "invoke_green_llm_skipped_green_complete"]
    assert skip_events, (
        f"AC9: expected 'invoke_green_llm_skipped_green_complete' event, got "
        f"{[e['type'] for e in captured]}"
    )


# ─── AC10 (regression floor): no marker → invoke_llm_subprocess called once ──


def test_ac10_regression_floor_invoke_green_llm_calls_subprocess_when_no_marker(monkeypatch, tmp_path):
    _patch_emit(monkeypatch)
    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir()

    calls = []

    def _canned_ok(*a, **kw):
        calls.append((a, kw))
        return StepResult(
            status="ok",
            data={"raw_response": "GREEN COMPLETE", "tokens_out": 10, "log_path": "/tmp/green.log",
                  "spec_path": "/tmp/spec.md", "red_log_path": "/tmp/red.log",
                  "validation_doc_path": "/tmp/validation.md"},
            duration_ms=0,
            step_name="invoke_green_llm",
        )

    monkeypatch.setattr(_p5, "invoke_llm_subprocess", _canned_ok)
    monkeypatch.setattr(_p5, "_resolve_scratchpad", lambda ctx: scratchpad)
    monkeypatch.setattr(_p5, "_resolve_worktree_root", lambda ctx, sp: sp)
    monkeypatch.setattr(_p5, "_maybe_emit_cross_tree_warning", lambda result, root: result)

    ctx = _make_ctx(str(scratchpad))
    prev = _make_prev(_make_green_prev_data(), step_name="build_green_prompt")

    result = _p5._invoke_green_llm(ctx, prev)

    assert result.status == "ok", f"AC10: expected status=ok, got {result.status!r}"
    assert len(calls) == 1, f"AC10: expected invoke_llm_subprocess called exactly once, got {len(calls)}"


# ─── AC11: _commit_green_code resume path bypasses manifest_from_result ─────


def test_ac11_commit_green_code_uses_resume_paths_and_deletes_marker(monkeypatch, tmp_path):
    captured = _patch_emit(monkeypatch)
    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir()

    relpath = getattr(_p5, "GREEN_COMPLETE_RESUME_RELPATH", None)
    assert relpath is not None, "GH483 constant GREEN_COMPLETE_RESUME_RELPATH not implemented yet"
    marker_file = scratchpad / relpath
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(json.dumps({"red_commit_sha": RED_SHA, "paths": ["src/a.py"]}), encoding="utf-8")

    def _raise_if_called(*a, **kw):
        raise AssertionError("manifest_from_result must NOT be called on resume path")

    monkeypatch.setattr(_p5, "manifest_from_result", _raise_if_called)
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))
    monkeypatch.setattr(_p5, "_filter_gitignored_paths", lambda paths, cwd: list(paths))
    monkeypatch.setattr(_p5, "get_config", lambda: SimpleNamespace(gate_enabled=lambda name: False))
    monkeypatch.setattr(
        _p5, "_git_op_with_lock_retry",
        lambda argv, cwd=None, timeout=30: (SimpleNamespace(returncode=0, stdout="", stderr=""), "ok"),
    )
    monkeypatch.setattr(_p5, "_paths_have_staged_changes", lambda cwd, paths: True)
    monkeypatch.setattr(
        _p5.git_port, "git_read",
        lambda argv, cwd=None, timeout=30: SimpleNamespace(returncode=0, stdout="b" * 40, stderr=""),
    )

    ctx = _make_ctx(str(scratchpad))
    prev = _make_prev(
        {
            "red_commit_sha": RED_SHA,
            "cycle": 1,
            "green_complete_resume": True,
            "green_resume_paths": ["src/a.py"],
        },
        step_name="invoke_green_llm",
    )

    result = _p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"AC11: expected status=ok on resume commit path, got {result.status!r}, "
        f"error_code={result.error_code!r}, error={result.error!r}"
    )
    manifest_events = [e for e in captured if e["type"] == "commit_manifest_resolved"]
    for e in manifest_events:
        assert e["payload"].get("n_manifest") == 1, (
            f"AC11: expected resume-path manifest count of 1, got {e['payload']!r}"
        )
    assert not marker_file.exists(), (
        "AC11: expected green-complete-resume marker file to be deleted after commit"
    )


# ─── AC12: _parse_green_status on the §2.3 synthetic response ───────────────


def test_ac12_parse_green_status_recognizes_synthetic_resume_response():
    synthetic = (
        "GREEN COMPLETE\n\n"
        "(green_complete_resume gh483: implementation pre-existing in working "
        "tree; GREEN LLM skipped)"
    )
    result = _parse_green_status(synthetic)
    assert result == _p5.GREEN_COMPLETE, (
        f"AC12: expected GREEN_COMPLETE marker recognized, got {result!r}"
    )
