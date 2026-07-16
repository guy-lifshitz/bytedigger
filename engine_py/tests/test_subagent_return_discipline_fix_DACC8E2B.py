"""RED tests for DACC8E2B — fix-phase subagent-return-discipline (FD2592D9 slice-6b).

Spec: SHARED/memory/Decisions/2026-06-12_DACC8E2B_fix_return_discipline_spec.md

Tests that FAIL today:
  AC1 (a-d): _resolve_fix_source does not exist yet.
  AC2: _build_fix_prompt prompt missing OUTPUT —, Do NOT echo, 200-token-cap directive.
  AC4: _invoke_fix_llm does not unlink log_path before dispatch.
  AC5: _write_fix_artifact overwrites log_path with raw (body←file split not implemented).
  AC6: fix_writer_return_source event not emitted today.
  AC7: telemetry reads raw (not resolved body), so files_modified line from doc file not seen.
  AC8: single-write restructure not yet present (regression guard — may partly hold).

Tests that may PASS today (regression guards):
  AC3: absence of bash / git log / run verification command — existing behavior.

See §1i (workflows.md): no singleton/timing resources used here.
D1CF5FDF: _resolve_fix_source does not yet exist — accessed via getattr inside test bodies
only (never at module top level) so the file COLLECTS cleanly.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

# Mirror the import pattern used by sibling tests — do NOT re-point sys.path
# at module level beyond what sibling tests already do (81F97F3D / §1q).
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(ENGINE_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "lib"))
if str(ENGINE_ROOT / "workflows") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

# Import the MODULE only — _resolve_fix_source doesn't exist yet;
# accessing it at top-level would raise ImportError and hang the RED phase.
import phase_6_review  # noqa: E402

from phase_6_review import (  # noqa: E402
    FIX_BLOCKED,
    FIX_COMPLETE,
    FIX_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    _build_fix_prompt,
    _invoke_fix_llm,
    _write_fix_artifact,
)
from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── fixture helpers ─────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, *, worktree: Path | None = None) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if worktree is not None:
        org["current_worktree_path"] = str(worktree)
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


def _make_fix_prev(scratchpad: Path, raw: str, *, log_path: Path | None = None) -> StepResult:
    """Build the prev StepResult that _write_fix_artifact expects.

    Mirrors _make_fix_prev from test_phase_6_fix_structured_verdict.py.
    """
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: FAIL\n")
    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n")
    resolved_log = log_path if log_path is not None else scratchpad / FIX_DOC_RELPATH
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "log_path": str(resolved_log),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "verdict": "FAIL",
            "prompt": "fix worker prompt stub",
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )


def _make_build_fix_prev(scratchpad: Path) -> StepResult:
    """Build the prev StepResult that _build_fix_prompt expects.

    Mirrors the shape used in test_phase_6_verified_only_gate_65695203.py::
    test_ac6_build_fix_prompt_references_fix_doc_path_not_review_doc_path.
    """
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: FAIL\n")
    spec_doc = scratchpad / SPEC_DOC_RELPATH
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("## US1\nAdd foo\n")
    return StepResult(
        status="ok",
        data={
            "spec_path": str(spec_doc),
            "review_doc_path": str(review_doc),
            # review_fix_doc_path not set → falls back to review_doc_path (current behavior)
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="write_review_artifact",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Capture every _emit_safe call in phase_6_review. Mirrors sibling tests."""
    captured: list[dict] = []

    def _capture(event_type: str, payload: dict) -> None:
        captured.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(phase_6_review, "_emit_safe", _capture)
    return captured


def _stub_disk_truth(monkeypatch) -> None:
    """Stub out _emit_fix_disk_truth_telemetry to avoid needing a real git repo."""
    monkeypatch.setattr(
        phase_6_review,
        "_emit_fix_disk_truth_telemetry",
        lambda ctx, prev, body: None,
        raising=False,
    )


# ─── AC1: _resolve_fix_source helper (4 branches) ────────────────────────────
# D1CF5FDF: accessed via getattr so the file collects even before GREEN ships it.


def test_ac1a_resolve_fix_source_file_exists_returns_file_text(tmp_path):
    """AC1a: file exists + non-blank → (file_text, 'worker_file').

    FAILS today: _resolve_fix_source does not exist in phase_6_review.
    """
    fn = getattr(phase_6_review, "_resolve_fix_source", None)
    assert fn is not None, (
        "_resolve_fix_source not found on phase_6_review — GREEN must add this helper (§2.1)"
    )
    doc = tmp_path / "fix-report.md"
    doc.write_text("# My Fix Report\n\nDid the thing.\n", encoding="utf-8")
    body, source = fn(str(doc), "raw fallback text")
    assert source == "worker_file", f"expected source='worker_file', got {source!r}"
    assert body == doc.read_text(encoding="utf-8"), (
        f"expected body to equal file contents, got {body!r}"
    )


def test_ac1b_resolve_fix_source_file_absent_returns_raw(tmp_path):
    """AC1b: file absent → (raw, 'raw_response_fallback').

    FAILS today: _resolve_fix_source does not exist.
    """
    fn = getattr(phase_6_review, "_resolve_fix_source", None)
    assert fn is not None, "_resolve_fix_source not found on phase_6_review"
    missing = tmp_path / "nonexistent.md"
    raw = "FIX COMPLETE\n## fix-output (structured)\n```json\n{}\n```\n"
    body, source = fn(str(missing), raw)
    assert source == "raw_response_fallback", (
        f"expected source='raw_response_fallback' for absent file, got {source!r}"
    )
    assert body == raw, f"expected body==raw for absent file, got {body!r}"


def test_ac1c_resolve_fix_source_empty_file_returns_raw(tmp_path):
    """AC1c: file exists but content is whitespace-only → (raw, 'raw_response_fallback').

    FAILS today: _resolve_fix_source does not exist.
    """
    fn = getattr(phase_6_review, "_resolve_fix_source", None)
    assert fn is not None, "_resolve_fix_source not found on phase_6_review"
    doc = tmp_path / "empty.md"
    doc.write_text("   \n\t  \n", encoding="utf-8")
    raw = "FIX COMPLETE\n"
    body, source = fn(str(doc), raw)
    assert source == "raw_response_fallback", (
        f"expected source='raw_response_fallback' for whitespace-only file, got {source!r}"
    )
    assert body == raw


def test_ac1d_resolve_fix_source_unreadable_doc_path_returns_raw(tmp_path):
    """AC1d: doc_path unreadable / bad type (None/int) → (raw, 'raw_response_fallback').

    FAILS today: _resolve_fix_source does not exist.
    """
    fn = getattr(phase_6_review, "_resolve_fix_source", None)
    assert fn is not None, "_resolve_fix_source not found on phase_6_review"
    raw = "FIX BLOCKED\n"
    # None as doc_path
    body_none, source_none = fn(None, raw)
    assert source_none == "raw_response_fallback", (
        f"None doc_path: expected 'raw_response_fallback', got {source_none!r}"
    )
    assert body_none == raw
    # integer doc_path
    body_int, source_int = fn(42, raw)
    assert source_int == "raw_response_fallback", (
        f"int doc_path: expected 'raw_response_fallback', got {source_int!r}"
    )
    assert body_int == raw


# ─── AC2: _build_fix_prompt directive ────────────────────────────────────────


def test_ac2_build_fix_prompt_contains_return_discipline_directive(tmp_path, monkeypatch):
    """AC2: built prompt must contain fix_doc_path as Write target, 'OUTPUT —' landmark,
    'Do NOT echo', '200' token-cap reference; AND still contain existing required
    parts: 'ROLE: You are a fix worker', 'FIX COMPLETE', '## fix-output (structured)'.

    FAILS today: _build_fix_prompt does not include OUTPUT — / Do NOT echo / 200 directive.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(phase_6_review, "_maybe_role_template", lambda ctx: None, raising=False)
    monkeypatch.setattr(phase_6_review, "_read_first_block", lambda s: "", raising=False)
    monkeypatch.setattr(
        phase_6_review, "_inline_inscope_test_files",
        lambda ctx, s: ("", 0, 0), raising=False,
    )
    ctx = _make_ctx(scratchpad)
    prev = _make_build_fix_prev(scratchpad)

    result = _build_fix_prompt(ctx, prev)
    assert result.status == "ok", (
        f"_build_fix_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    # Whitespace-normalize for multi-line wrap safety (OFI 895f59d7)
    norm = " ".join(prompt.split())

    # The fix_doc_path is computed inside as scratchpad / FIX_DOC_RELPATH
    expected_fix_doc_path = str((scratchpad / FIX_DOC_RELPATH).resolve())
    # Check for the path OR the non-resolved form
    fix_doc_str = str(scratchpad / FIX_DOC_RELPATH)
    assert (fix_doc_str in prompt) or (expected_fix_doc_path in prompt), (
        f"AC2: fix_doc_path ({fix_doc_str!r}) not found as Write target in prompt. "
        "GREEN must add the 'write to this file' directive."
    )

    # New directive landmarks
    assert "OUTPUT —" in norm, (
        "AC2: 'OUTPUT —' landmark missing from fix prompt. "
        "GREEN must add return-discipline directive."
    )
    assert "Do NOT echo" in norm, (
        "AC2: 'Do NOT echo' directive missing from fix prompt. "
        "GREEN must instruct worker not to echo fix report body."
    )
    assert "200" in norm, (
        "AC2: '200' token-cap reference missing from fix prompt. "
        "GREEN must add ≤200-token return instruction."
    )

    # Existing required parts (regression guards — must stay)
    assert "ROLE: You are a fix worker" in prompt, (
        "AC2 regression: 'ROLE: You are a fix worker' must remain in fix prompt."
    )
    assert "FIX COMPLETE" in prompt, (
        "AC2 regression: 'FIX COMPLETE' marker instruction must remain in fix prompt."
    )
    assert "## fix-output (structured)" in prompt, (
        "AC2 regression: '## fix-output (structured)' block instruction must remain."
    )


# ─── AC3: absence guards (regression-guard — may pass today) ─────────────────


def test_ac3_build_fix_prompt_has_no_forbidden_tokens(tmp_path, monkeypatch):
    """AC3: fix prompt must NOT contain 'git log' or 'run verification command'.
    These are regression-guard assertions; may pass today and MUST pass post-GREEN.

    NOTE: AC3 may PASS pre-GREEN (existing behavior already clean). Included as
    a regression lock so the GREEN directive doesn't inadvertently introduce them.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(phase_6_review, "_maybe_role_template", lambda ctx: None, raising=False)
    monkeypatch.setattr(phase_6_review, "_read_first_block", lambda s: "", raising=False)
    monkeypatch.setattr(
        phase_6_review, "_inline_inscope_test_files",
        lambda ctx, s: ("", 0, 0), raising=False,
    )
    ctx = _make_ctx(scratchpad)
    prev = _make_build_fix_prev(scratchpad)

    result = _build_fix_prompt(ctx, prev)
    assert result.status == "ok", f"_build_fix_prompt failed: {result.error!r}"
    prompt = result.data["prompt"]

    assert "git log" not in prompt, (
        "AC3: 'git log' must not appear in fix prompt (§2.2 forbidden-token guard)"
    )
    assert "run verification command" not in prompt, (
        "AC3: 'run verification command' must not appear in fix prompt"
    )


# ─── AC4: _invoke_fix_llm unlinks log_path before dispatch ───────────────────


def test_ac4_invoke_fix_llm_unlinks_stale_log_path_before_dispatch(tmp_path, monkeypatch):
    """AC4: _invoke_fix_llm must unlink log_path before calling invoke_llm_subprocess;
    and must pass allowed_tools == ['Read','Write','Edit','Grep','Glob'].

    FAILS today: _invoke_fix_llm has no unlink call before dispatch (current line 2161).
    The fake observes the stale file still present → assertion fails.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    log_path = scratchpad / FIX_DOC_RELPATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-create stale file
    log_path.write_text("stale content from prior run\n", encoding="utf-8")

    captured_calls: list[dict] = []

    def _fake_invoke(**kwargs):
        # Record whether log_path exists AT THE MOMENT OF DISPATCH
        captured_calls.append({
            "log_path_exists_at_dispatch": Path(kwargs.get("extra_data", {}).get("log_path", "")).exists(),
            "allowed_tools": kwargs.get("allowed_tools"),
        })
        return StepResult(
            status="ok",
            data={
                "raw_response": "FIX COMPLETE — done.\n",
                "log_path": kwargs.get("extra_data", {}).get("log_path", ""),
                "spec_path": "",
                "review_doc_path": "",
                "verdict": "FAIL",
                "prompt": "",
            },
            duration_ms=0,
            step_name="invoke_fix_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(
        phase_6_review, "_maybe_emit_cross_tree_warning",
        lambda result, worktree_root: result, raising=False,
    )

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\n")
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n")

    prev = StepResult(
        status="ok",
        data={
            "prompt": "stub prompt",
            "log_path": str(log_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="build_fix_prompt",
    )
    ctx = _make_ctx(scratchpad)

    _invoke_fix_llm(ctx, prev)

    assert len(captured_calls) == 1, (
        f"expected exactly 1 invoke_llm_subprocess call, got {len(captured_calls)}"
    )
    call = captured_calls[0]

    # AC4a: log_path must be GONE at dispatch time (unlinked before invoke)
    assert call["log_path_exists_at_dispatch"] is False, (
        "AC4: stale log_path was still present at dispatch time — "
        "_invoke_fix_llm must unlink it before calling invoke_llm_subprocess (§2.3)"
    )
    # AC4b: allowed_tools unchanged
    assert call["allowed_tools"] == ["Read", "Write", "Edit", "Grep", "Glob"], (
        f"AC4: allowed_tools changed unexpectedly: {call['allowed_tools']!r}"
    )


# ─── AC5: channel-split — body from file, CONTROL from raw ───────────────────


def test_ac5_channel_split_body_from_file_control_from_raw(tmp_path, monkeypatch):
    """AC5: doc file at log_path has body B (non-blank, B != raw);
    raw = FIX BLOCKED + ## fix-output JSON fix_complete=false.
    _write_fix_artifact must:
      - return error_code == 'E_FIX_BLOCKED' (CONTROL from raw)
      - NOT overwrite log_path with raw (body from file, stays == B)
      - emit fix_writer_return_source event with source == 'worker_file'.

    FAILS today: _write_fix_artifact eagerly writes raw to log_path (line 2238),
    overwriting B; fix_writer_return_source event is never emitted.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    log_path = scratchpad / FIX_DOC_RELPATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    body_B = "# Fix Report\n\nI fixed the file carefully.\nDetails here.\n"
    log_path.write_text(body_B, encoding="utf-8")

    raw = (
        "FIX BLOCKED — 0 of 1 findings fixed. Diagnosis: spec ambiguity.\n"
        "\n"
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": false, "remaining": [{"file": "x.py", "issue": "still broken"}]}\n'
        "```\n"
    )

    captured = _patch_emit(monkeypatch)
    _stub_disk_truth(monkeypatch)

    prev = _make_fix_prev(scratchpad, raw, log_path=log_path)
    ctx = _make_ctx(scratchpad)

    result = _write_fix_artifact(ctx, prev)

    # CONTROL: verdict from raw → E_FIX_BLOCKED
    assert result.error_code == "E_FIX_BLOCKED", (
        f"AC5: expected error_code='E_FIX_BLOCKED' (CONTROL←raw), got {result.error_code!r}"
    )

    # BODY: file NOT overwritten with raw
    on_disk = log_path.read_text(encoding="utf-8")
    assert on_disk == body_B, (
        f"AC5: log_path was overwritten with raw — body should remain as worker file B.\n"
        f"Expected: {body_B!r}\nGot: {on_disk!r}"
    )

    # Telemetry: fix_writer_return_source with source='worker_file'
    source_events = [e for e in captured if e["type"] == "fix_writer_return_source"]
    assert len(source_events) >= 1, (
        f"AC5: no 'fix_writer_return_source' event emitted — GREEN must add this (§2.4).\n"
        f"All captured events: {[e['type'] for e in captured]}"
    )
    sources = {e["payload"].get("source") for e in source_events}
    assert "worker_file" in sources, (
        f"AC5: fix_writer_return_source event has unexpected source(s): {sources!r}; "
        "expected 'worker_file' since doc file was pre-created with body B."
    )


# ─── AC6: fail-open back-compat — no doc file, raw used as body ───────────────


def test_ac6_fail_open_no_doc_file_raw_used_as_body(tmp_path, monkeypatch):
    """AC6: no doc file at log_path; raw = full body + FIX COMPLETE + JSON complete.
    _write_fix_artifact must:
      - return status='ok'
      - write raw to log_path
      - emit fix_writer_return_source with source='raw_response_fallback'
      - fix_bytes_written == len(raw.encode('utf-8'))

    FAILS today: fix_writer_return_source event is never emitted.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    raw = (
        "I carefully fixed all issues.\n"
        "FIX COMPLETE — 2 of 2 findings fixed. Files: [a.py, b.py]\n"
        "\n"
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": true, "remaining": []}\n'
        "```\n"
    )

    captured = _patch_emit(monkeypatch)
    _stub_disk_truth(monkeypatch)

    # log_path does NOT exist before call
    log_path = scratchpad / FIX_DOC_RELPATH
    assert not log_path.exists(), "test setup: log_path must not exist before call"

    prev = _make_fix_prev(scratchpad, raw, log_path=log_path)
    ctx = _make_ctx(scratchpad)

    result = _write_fix_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC6: expected status='ok', got {result.status!r} ({result.error_code!r})"
    )

    on_disk = log_path.read_text(encoding="utf-8")
    assert on_disk == raw, (
        f"AC6: log_path content should equal raw (fail-open path).\n"
        f"Expected: {raw!r}\nGot: {on_disk!r}"
    )

    # fix_writer_return_source with source='raw_response_fallback'
    source_events = [e for e in captured if e["type"] == "fix_writer_return_source"]
    assert len(source_events) >= 1, (
        f"AC6: no 'fix_writer_return_source' event — GREEN must emit it (§2.4).\n"
        f"All captured: {[e['type'] for e in captured]}"
    )
    sources = {e["payload"].get("source") for e in source_events}
    assert "raw_response_fallback" in sources, (
        f"AC6: expected source='raw_response_fallback' (no doc file), got {sources!r}"
    )

    # fix_bytes_written
    assert isinstance(result.data, dict)
    expected_bytes = len(raw.encode("utf-8"))
    assert result.data.get("fix_bytes_written") == expected_bytes, (
        f"AC6: fix_bytes_written expected {expected_bytes}, got {result.data.get('fix_bytes_written')!r}"
    )


# ─── AC7: telemetry reads resolved body, not raw ─────────────────────────────


def test_ac7_telemetry_reads_resolved_body_not_raw(tmp_path, monkeypatch):
    """AC7: doc file at log_path contains 'files_modified: a.py, b.py';
    raw = FIX COMPLETE marker + JSON only (NO 'files_modified:' line).
    _emit_fix_disk_truth_telemetry must see the resolved body (from file),
    so fix_files_drift event is emitted with llm_claimed == ['a.py', 'b.py'].

    FAILS today: _write_fix_artifact passes raw to _emit_fix_disk_truth_telemetry (line 2379);
    raw lacks 'files_modified:' → no drift event emitted.

    Mirrors stub pattern from test_phase_6_step7_w1_disk_truth.py::
    test_fix_files_drift_event_emitted_when_llm_claims_files_modified.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Pre-create doc file whose body has files_modified line
    log_path = scratchpad / FIX_DOC_RELPATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    body_with_files = (
        "# Fix Report\n\nI edited a.py and b.py.\n"
        "files_modified: a.py, b.py\n"
        "Details of my changes.\n"
    )
    log_path.write_text(body_with_files, encoding="utf-8")

    # raw = marker + JSON ONLY — no files_modified line
    raw = (
        "FIX COMPLETE — 2 of 2 findings fixed.\n"
        "\n"
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": true, "remaining": []}\n'
        "```\n"
    )
    assert "files_modified" not in raw, "test setup: raw must not contain files_modified"

    # Need a review doc with structured findings so fix_disk_truth_coverage doesn't
    # swallow the telemetry path. Seed a minimal one:
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text(
        "# Review\n\n"
        "## Findings (structured)\n"
        "```json\n"
        '[{"id":"1","severity":"HIGH","path":"a.py","description":"x"}]\n'
        "```\n\n"
        "VERDICT: FAIL\n"
    )

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n")

    captured = _patch_emit(monkeypatch)

    # Stub git helpers so disk-truth telemetry doesn't need a real repo
    monkeypatch.setattr(
        phase_6_review, "git_diff_files",
        lambda *a, **kw: ["a.py", "b.py"],
        raising=False,
    )
    monkeypatch.setattr(
        phase_6_review, "resolve_pre_phase_sha",
        lambda *a, **kw: "",
        raising=False,
    )

    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "log_path": str(log_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "verdict": "FAIL",
            "prompt": "stub",
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )
    ctx = _make_ctx(scratchpad)

    _write_fix_artifact(ctx, prev)

    drift_events = [e for e in captured if e["type"] == "fix_files_drift"]
    assert len(drift_events) >= 1, (
        f"AC7: no 'fix_files_drift' event emitted.\n"
        f"Expected: telemetry to receive resolved body (with files_modified: a.py, b.py).\n"
        f"Got: telemetry received raw (no files_modified line → no drift event).\n"
        f"All captured event types: {[e['type'] for e in captured]}\n"
        f"GREEN must pass resolved body to _emit_fix_disk_truth_telemetry (§2.4 step 6)."
    )
    payload = drift_events[0]["payload"]
    assert set(payload.get("llm_claimed", [])) == {"a.py", "b.py"}, (
        f"AC7: fix_files_drift llm_claimed expected {{'a.py','b.py'}}, got {payload.get('llm_claimed')!r}"
    )


# ─── AC8: retry feeds single write ───────────────────────────────────────────


def test_ac8_retry_feeds_single_write_final_artifact_from_retry_raw(tmp_path, monkeypatch):
    """AC8: call-1 returns FIX_NO_MARKER (triggers retry); call-2 returns FIX COMPLETE + JSON.
    Final log_path content must be derived from call-2 (retry) raw, not call-1 raw.
    Returns status='ok'. log_path content contains 'SECOND' and not 'FIRST'.

    Today this may partly hold (retry overwrite at line 2295) but the single-write
    restructure (§2.4) changes the code path. Asserted strictly: the test is a
    regression lock verifying the restructure preserves the correct final artifact.
    If current code already passes (likely), this is a regression guard — noted below.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    raw_call1 = "garbage FIRST no marker — worker truncated\n"
    raw_call2 = (
        "FIX COMPLETE — 1 of 1 findings fixed. Files: [z.py]\n"
        "SECOND\n"
        "\n"
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": true, "remaining": []}\n'
        "```\n"
    )

    log_path = scratchpad / FIX_DOC_RELPATH
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _fake_invoke(**kwargs):
        # The retry is the ONLY fake call (_write_fix_artifact reads raw_call1 from
        # prev.data["raw_response"] directly — it does not go through invoke_llm_subprocess).
        # So this fake always returns raw_call2 (the success response).
        return StepResult(
            status="ok",
            data={
                "raw_response": raw_call2,
                "log_path": kwargs.get("extra_data", {}).get("log_path", str(log_path)),
                "spec_path": kwargs.get("extra_data", {}).get("spec_path", ""),
                "review_doc_path": kwargs.get("extra_data", {}).get("review_doc_path", ""),
                "verdict": "FAIL",
                "prompt": "",
            },
            duration_ms=0,
            step_name="invoke_fix_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)
    captured = _patch_emit(monkeypatch)
    _stub_disk_truth(monkeypatch)

    # No doc file written by fake (simulates worker-didn't-write case)
    assert not log_path.exists(), "test setup: log_path must not exist before call"

    prev = _make_fix_prev(scratchpad, raw_call1, log_path=log_path)
    ctx = _make_ctx(scratchpad)

    result = _write_fix_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC8: expected status='ok' after successful retry, got {result.status!r} ({result.error_code!r})"
    )

    on_disk = log_path.read_text(encoding="utf-8")
    assert "SECOND" in on_disk, (
        f"AC8: final artifact must derive from retry raw (call-2) containing 'SECOND'.\n"
        f"Got log_path content: {on_disk!r}"
    )
    assert "FIRST" not in on_disk, (
        f"AC8: final artifact must NOT contain 'FIRST' (call-1 raw must not persist).\n"
        f"Got log_path content: {on_disk!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    _sys.exit(pytest.main([__file__, "--tb=short"]))
