"""RED tests for 1A07C325 — satisfaction subagent-return-discipline (FD2592D9 slice-6c).

Spec: SHARED/memory/Decisions/2026-06-12_1A07C325_satisfaction_return_discipline_spec.md
Parent SYSTEMATIC: FD2592D9 (subagent raw_response bloats in-session servicer context).

Problem: satisfaction evaluator returns its full evaluation doc as raw_response.
Engine writes doc_path.write_text(raw).  Fix: instruct it to WRITE the doc to
doc_path; engine reads body from the file (fail-open to raw_response).

Tests that FAIL today (genuine fail-today):
  AC1  — _invoke_satisfaction_llm single branch passes allowed_tools=["Read"] today; "Write" absent.
  AC3  — no unlink before dispatch today; pre-existing doc_path file survives.
  AC4  — prompt contains "your response IS the satisfaction file" today (old echo directive); no "Do NOT echo".
  AC7  — _resolve_satisfaction_source does not exist yet → callable() assertion fails.
  AC8  — channel-split: body written from raw today (not file); event never emitted.
  AC9  — fail-open event never emitted today.
  AC10 — satisfaction_bytes_written computed from raw today (len(raw)), not file body.

Tests that MAY PASS today (regression guards):
  AC2  — COMPLEX parallel evaluators already pass allowed_tools=["Read"] (no Write).
  AC5  — COMPLEX prompt still contains echo directive (unchanged today).
  AC6  — both prompts already contain ## Composite, SCORE: <0-100>, Set satisfied=true, ## satisfaction-output.
  AC11 — multi-eval path routes to _write_satisfaction_doc_multi and writes composite doc (existing behavior).

§1q extension (D1CF5FDF): _resolve_satisfaction_source does NOT exist at import time.
  Access via getattr INSIDE test bodies; never import at top level.
  File COLLECTS cleanly; tests FAIL at assert/runtime, never at collection.

§1i (workflows.md): no singleton/timing resources used here; all state pre-staged
  deterministically via tmp_path fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(ENGINE_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "lib"))
if str(ENGINE_ROOT / "workflows") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

# Import the MODULE only — _resolve_satisfaction_source doesn't exist yet.
# Accessing it at top-level would raise ImportError and hang the RED phase (D1CF5FDF).
import phase_6_review  # noqa: E402

from phase_6_review import (  # noqa: E402
    SATISFACTION_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    FIX_DOC_RELPATH,
    _build_satisfaction_prompt,
    _invoke_satisfaction_llm,
    _write_satisfaction_doc,
)
from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── fixture helpers ──────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, *, complexity: str | None = None) -> WorkflowContext:
    """Build a minimal WorkflowContext for direct step-function calls."""
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if complexity is not None:
        org["complexity"] = complexity
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session-1A07C325",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_build_sat_prev(scratchpad: Path) -> StepResult:
    """Build the prev StepResult that _build_satisfaction_prompt expects.

    Requires spec_path, review_doc_path, fix_doc_path in prev.data.
    """
    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo to bar\n", encoding="utf-8")

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: PASS\n", encoding="utf-8")

    fix_doc = scratchpad / FIX_DOC_RELPATH
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE — 0 findings.\n", encoding="utf-8")

    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH

    return StepResult(
        status="ok",
        data={
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "doc_path": str(sat_doc_path),
        },
        duration_ms=0,
        step_name="write_fix_artifact",
    )


def _make_invoke_sat_prev(
    scratchpad: Path,
    prompt: str = "test satisfaction prompt",
) -> StepResult:
    """Build the prev StepResult that _invoke_satisfaction_llm expects."""
    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n", encoding="utf-8")

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: PASS\n", encoding="utf-8")

    fix_doc = scratchpad / FIX_DOC_RELPATH
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE.\n", encoding="utf-8")

    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH
    sat_doc_path.parent.mkdir(parents=True, exist_ok=True)

    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(sat_doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "prompt_bytes": len(prompt.encode()),
        },
        duration_ms=0,
        step_name="build_satisfaction_prompt",
    )


def _valid_raw_response(score: int = 95) -> str:
    """Build a valid raw_response with the given SCORE, VERDICT: PASS, and structured block."""
    verdict = "PASS" if score >= 80 else "FAIL"
    satisfied_val = "true" if score >= 80 else "false"
    return (
        "# Satisfaction Evaluation\n\n"
        "## Per-Dimension Scores\n"
        "- Spec compliance: 95\n"
        "- Test quality: 95\n"
        "- Code quality: 95\n"
        "- Completeness: 95\n"
        "- Boy Scout: 95\n\n"
        "## Evidence Per Dimension\n"
        "- All checks passed.\n\n"
        "## Concerns\n"
        "none\n\n"
        "## Composite\n"
        f"SCORE: {score}\n\n"
        f"VERDICT: {verdict}\n\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        f'{{\"satisfied\": {satisfied_val}, \"fixes_required\": []}}\n'
        "```\n"
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Capture every _emit_safe call in phase_6_review. Mirrors sibling test pattern."""
    captured: list[dict] = []

    def _capture(event_type: str, payload: dict) -> None:
        captured.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(phase_6_review, "_emit_safe", _capture)
    return captured


# ─── AC1: _invoke_satisfaction_llm non-COMPLEX passes "Write" in allowed_tools ─


def test_ac1_invoke_satisfaction_llm_single_branch_passes_write_in_allowed_tools(
    tmp_path, monkeypatch
):
    """AC1: non-COMPLEX branch calls invoke_llm_subprocess with allowed_tools
    containing 'Write'.

    FAILS today: allowed_tools=["Read"] (line 2741) — "Write" absent.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured_calls: list[dict] = []

    def _fake_invoke(**kwargs):
        captured_calls.append({"allowed_tools": kwargs.get("allowed_tools")})
        return StepResult(
            status="ok",
            data={
                "raw_response": _valid_raw_response(95),
                "doc_path": kwargs.get("extra_data", {}).get("doc_path", ""),
                "spec_path": kwargs.get("extra_data", {}).get("spec_path", ""),
                "review_doc_path": kwargs.get("extra_data", {}).get("review_doc_path", ""),
                "fix_doc_path": kwargs.get("extra_data", {}).get("fix_doc_path", ""),
            },
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)

    # No complexity key → single branch
    ctx = _make_ctx(scratchpad)
    prev = _make_invoke_sat_prev(scratchpad)

    _invoke_satisfaction_llm(ctx, prev)

    assert len(captured_calls) == 1, (
        f"expected exactly 1 invoke_llm_subprocess call (single branch), got {len(captured_calls)}"
    )
    actual_tools = captured_calls[0]["allowed_tools"]
    assert "Write" in actual_tools, (
        f"AC1: non-COMPLEX branch must pass 'Write' in allowed_tools. "
        f"Today it passes {actual_tools!r} — 'Write' is absent (§2.2)."
    )


# ─── AC2: COMPLEX parallel evaluators still pass allowed_tools=["Read"] ────────


def test_ac2_complex_evaluators_still_pass_read_only_allowed_tools(tmp_path, monkeypatch):
    """AC2: COMPLEX parallel evaluators must still pass allowed_tools=["Read"]
    (regression guard — multi eval unchanged per §2.2).

    MAY PASS today (regression guard: current code already uses ["Read"] for COMPLEX).
    Must keep passing post-GREEN.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured_calls: list[dict] = []

    def _fake_invoke(**kwargs):
        captured_calls.append({"allowed_tools": kwargs.get("allowed_tools")})
        return StepResult(
            status="ok",
            data={
                "raw_response": _valid_raw_response(95),
                "doc_path": "",
                "spec_path": "",
                "review_doc_path": "",
                "fix_doc_path": "",
            },
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)

    # complexity=COMPLEX → parallel evaluators path
    ctx = _make_ctx(scratchpad, complexity="COMPLEX")
    prev = _make_invoke_sat_prev(scratchpad)

    _invoke_satisfaction_llm(ctx, prev)

    # COMPLEX branch spawns 3 parallel evaluators
    assert len(captured_calls) >= 1, (
        "COMPLEX branch must call invoke_llm_subprocess at least once (via parallel evaluators)"
    )
    for i, call in enumerate(captured_calls):
        actual_tools = call["allowed_tools"]
        assert actual_tools == ["Read"], (
            f"AC2 regression guard: COMPLEX evaluator {i} must pass allowed_tools=['Read'] "
            f"(no Write — they have no Write tool, §2.2 preserved). Got {actual_tools!r}"
        )


# ─── AC3: non-COMPLEX _invoke_satisfaction_llm unlinks doc_path before dispatch ─


def test_ac3_invoke_satisfaction_llm_unlinks_doc_path_before_dispatch(tmp_path, monkeypatch):
    """AC3: _invoke_satisfaction_llm must unlink a pre-existing doc_path BEFORE
    calling invoke_llm_subprocess.

    FAILS today: no unlink in _invoke_satisfaction_llm; pre-existing file survives.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH
    sat_doc_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-create stale file (simulates a prior run's satisfaction doc).
    sat_doc_path.write_text("stale content from prior run\n", encoding="utf-8")
    assert sat_doc_path.exists(), "pre-condition: file must exist before invoke"

    doc_path_exists_at_dispatch: list[bool] = []

    def _fake_invoke(**kwargs):
        # Record whether doc_path exists AT DISPATCH TIME (must be absent).
        doc_str = kwargs.get("extra_data", {}).get("doc_path", "")
        doc_path_exists_at_dispatch.append(Path(doc_str).exists())
        return StepResult(
            status="ok",
            data={
                "raw_response": _valid_raw_response(95),
                "doc_path": doc_str,
                "spec_path": "",
                "review_doc_path": "",
                "fix_doc_path": "",
            },
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_ctx(scratchpad)
    prev = _make_invoke_sat_prev(scratchpad)

    _invoke_satisfaction_llm(ctx, prev)

    assert len(doc_path_exists_at_dispatch) >= 1, (
        "invoke_llm_subprocess was not called at all (test setup error)"
    )
    assert doc_path_exists_at_dispatch[0] is False, (
        "AC3: stale doc_path was still present at dispatch time — "
        "_invoke_satisfaction_llm must unlink it before calling invoke_llm_subprocess (§2.2)."
    )


# ─── AC4: non-COMPLEX prompt has Write directive, "Do NOT echo"; no echo directive ─


def test_ac4_single_prompt_has_write_directive_and_no_echo(tmp_path, monkeypatch):
    """AC4: non-COMPLEX prompt must contain:
      - 'OUTPUT —' landmark
      - Write-to-file directive: 'WRITE' + 'build-satisfaction.md'
      - 'Do NOT echo'
    AND must NOT contain:
      - 'your response IS the satisfaction file'
      - 'your response IS the file content'

    FAILS today: prompt contains the echo directive ("your response IS the satisfaction
    file itself") and lacks 'Do NOT echo' and the Write-to-file instruction.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(
        phase_6_review, "_maybe_role_template", lambda ctx: None, raising=False
    )
    monkeypatch.setattr(
        phase_6_review, "_read_first_block", lambda s: "", raising=False
    )

    ctx = _make_ctx(scratchpad)  # no complexity
    prev = _make_build_sat_prev(scratchpad)

    result = _build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"_build_satisfaction_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    # Whitespace-normalize for multi-line wrap safety (OFI 895f59d7).
    norm = " ".join(prompt.split())

    assert "OUTPUT —" in norm, (
        "AC4: 'OUTPUT —' landmark must be in non-COMPLEX satisfaction prompt."
    )
    assert "WRITE" in norm, (
        "AC4: non-COMPLEX prompt must contain 'WRITE' instruction (Write-to-file directive)."
    )
    assert "build-satisfaction.md" in prompt, (
        "AC4: non-COMPLEX prompt must name 'build-satisfaction.md' as the Write target."
    )
    assert "Do NOT echo" in norm, (
        "AC4: non-COMPLEX prompt must contain 'Do NOT echo' directive. "
        "Today it is absent — GREEN must add return-discipline reword (§2.1)."
    )
    # Must NOT have the old echo directive.
    assert "your response IS the satisfaction file" not in norm, (
        "AC4: non-COMPLEX prompt must NOT contain 'your response IS the satisfaction file' "
        "(old echo directive must be removed from single-eval branch, §2.1)."
    )
    assert "your response IS the file content" not in norm, (
        "AC4: non-COMPLEX prompt must NOT contain 'your response IS the file content'."
    )


# ─── AC5: COMPLEX prompt still has echo directive, no "Do NOT echo" ─────────────


def test_ac5_complex_prompt_still_has_echo_directive_no_do_not_echo(tmp_path, monkeypatch):
    """AC5: COMPLEX prompt (cfg complexity=COMPLEX) must STILL contain 'your response IS'
    (echo directive kept for multi-eval evaluators that have no Write tool)
    AND must NOT contain 'Do NOT echo'.

    MAY PASS today (regression guard: COMPLEX prompt is currently the same as single
    prompt — both have the echo directive; the change is to the single path only).
    Must pass post-GREEN to verify COMPLEX path is preserved unchanged.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(
        phase_6_review, "_maybe_role_template", lambda ctx: None, raising=False
    )
    monkeypatch.setattr(
        phase_6_review, "_read_first_block", lambda s: "", raising=False
    )

    ctx = _make_ctx(scratchpad, complexity="COMPLEX")
    prev = _make_build_sat_prev(scratchpad)

    result = _build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"_build_satisfaction_prompt (COMPLEX) returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    norm = " ".join(prompt.split())

    assert "your response IS" in norm, (
        "AC5 regression guard: COMPLEX prompt must STILL contain 'your response IS' "
        "(echo directive preserved for Read-only parallel evaluators, §2.1)."
    )
    assert "Do NOT echo" not in norm, (
        "AC5 regression guard: COMPLEX prompt must NOT contain 'Do NOT echo' "
        "(only the single-eval branch gets that directive, §2.1)."
    )


# ─── AC6: both branches preserve control literals ────────────────────────────


def test_ac6_both_prompts_preserve_control_literals(tmp_path, monkeypatch):
    """AC6: both non-COMPLEX and COMPLEX prompts must contain:
      - '## Composite'
      - 'SCORE: <0-100>'
      - 'Set satisfied=true iff VERDICT=PASS'
      - '## satisfaction-output'

    MAY PASS today (regression guard: these literals already present in current prompt).
    Guards the 1C24581F binding-test contract across the GREEN reword.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(
        phase_6_review, "_maybe_role_template", lambda ctx: None, raising=False
    )
    monkeypatch.setattr(
        phase_6_review, "_read_first_block", lambda s: "", raising=False
    )

    required_literals = [
        "## Composite",
        "SCORE: <0-100>",
        "Set satisfied=true iff VERDICT=PASS",
        "## satisfaction-output",
    ]

    for label, complexity in [("non-COMPLEX", None), ("COMPLEX", "COMPLEX")]:
        ctx = _make_ctx(scratchpad, complexity=complexity)
        prev = _make_build_sat_prev(scratchpad)
        result = _build_satisfaction_prompt(ctx, prev)
        assert result.status == "ok", (
            f"_build_satisfaction_prompt ({label}) returned error: {result.error!r}"
        )
        norm = " ".join(result.data["prompt"].split())

        for lit in required_literals:
            assert lit in norm, (
                f"AC6 ({label}): required literal {lit!r} missing from satisfaction prompt. "
                "GREEN must preserve all control literals (1C24581F binding-test contract)."
            )


# ─── AC7: _resolve_satisfaction_source helper exists and works ────────────────


def test_ac7_resolve_satisfaction_source_file_present(tmp_path):
    """AC7a: _resolve_satisfaction_source with a non-blank file returns
    (file_text, 'worker_file').

    FAILS today: _resolve_satisfaction_source does not exist in phase_6_review.
    callable() assertion fires (getattr returns None).
    D1CF5FDF: accessed via getattr INSIDE test body — file collects cleanly.
    """
    fn = getattr(phase_6_review, "_resolve_satisfaction_source", None)
    assert callable(fn), (
        "phase_6_review._resolve_satisfaction_source does not exist or is not callable. "
        "1A07C325 §2.3 requires this named helper to be added (GREEN must add it)."
    )

    doc = tmp_path / "build-satisfaction.md"
    doc.write_text("# Satisfaction Evaluation\n\nSCORE: 90\n", encoding="utf-8")

    body, source = fn(str(doc), "raw fallback text")
    assert source == "worker_file", (
        f"_resolve_satisfaction_source with existing file: expected source='worker_file', "
        f"got {source!r}"
    )
    assert body == doc.read_text(encoding="utf-8"), (
        f"_resolve_satisfaction_source must return file contents for existing file. Got {body!r}"
    )


def test_ac7_resolve_satisfaction_source_file_absent(tmp_path):
    """AC7b: _resolve_satisfaction_source with absent file returns
    (raw, 'raw_response_fallback').

    FAILS today: _resolve_satisfaction_source does not exist.
    """
    fn = getattr(phase_6_review, "_resolve_satisfaction_source", None)
    assert callable(fn), (
        "phase_6_review._resolve_satisfaction_source does not exist."
    )

    missing = tmp_path / "nonexistent.md"
    raw = _valid_raw_response(90)

    body, source = fn(str(missing), raw)
    assert source == "raw_response_fallback", (
        f"_resolve_satisfaction_source (absent file) must return 'raw_response_fallback'. "
        f"Got {source!r}"
    )
    assert body == raw, (
        f"_resolve_satisfaction_source (absent file) must return raw. Got {body!r}"
    )


# ─── AC8: channel split — body from file, control from raw ───────────────────


def test_ac8_channel_split_body_from_file_control_from_raw(tmp_path, monkeypatch):
    """AC8 KEY forcing function: single-eval prev where:
      - doc_path FILE contains SCORE: 30 (below threshold 80)
      - raw carries SCORE: 95 + VERDICT: PASS + valid structured satisfied=true

    After _write_satisfaction_doc:
      - result.status == 'ok'    (gate reads raw=95 >= 80 → PASS)
      - disk doc == file body    (SCORE: 30 — file wins, resolver did not overwrite)
      - 'satisfaction_writer_return_source' event emitted with source='worker_file'

    FAILS today on multiple assertions:
      1. status == 'error' today (raw=30 < threshold 80, gate reads raw → FAIL)
         Wait — raw has SCORE: 95. But doc_path.write_text(raw) overwrites the file with raw.
         So disk doc will be raw (SCORE: 95), not the file body (SCORE: 30).
      2. disk doc != file body (raw overwrites it today)
      3. no 'satisfaction_writer_return_source' event emitted today
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH
    sat_doc_path.parent.mkdir(parents=True, exist_ok=True)

    # File body: has SCORE: 30 (below threshold) — file wins body-channel.
    file_body = (
        "# Satisfaction Evaluation\n\n"
        "## Per-Dimension Scores\n"
        "- Spec compliance: 30\n\n"
        "## Evidence Per Dimension\n"
        "- Limited evidence.\n\n"
        "## Concerns\n"
        "- Many issues found.\n\n"
        "## Composite\n"
        "SCORE: 30\n\n"
        "VERDICT: FAIL\n\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        '{"satisfied": false, "fixes_required": [{"file": "x.py", "issue": "broken"}]}\n'
        "```\n"
    )
    sat_doc_path.write_text(file_body, encoding="utf-8")

    # raw_response: control channel — SCORE: 95, VERDICT: PASS, satisfied=true
    raw = _valid_raw_response(95)
    # Confirm: raw has SCORE: 95, file_body has SCORE: 30, they differ
    assert "SCORE: 30" in file_body
    assert "SCORE: 95" in raw

    captured = _patch_emit(monkeypatch)

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n", encoding="utf-8")

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: PASS\n", encoding="utf-8")

    fix_doc = scratchpad / FIX_DOC_RELPATH
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE.\n", encoding="utf-8")

    # org_config with explicit threshold 80
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "satisfaction_threshold": "80",
        },
        question="Add foo to bar",
        session_id="test-session-1A07C325-ac8",
        persona="hal",
        framework=None,
        domain=None,
    )

    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(sat_doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )

    result = _write_satisfaction_doc(ctx, prev)

    # Gate reads CONTROL from raw (SCORE: 95 >= 80 → PASS → status="ok").
    assert result.status == "ok", (
        f"AC8: expected status='ok' (gate reads raw SCORE=95 >= threshold 80). "
        f"Got status={result.status!r}, error={result.error!r}, error_code={result.error_code!r}. "
        f"GREEN must split channels: gate reads raw, body reads file."
    )

    # Disk doc must equal the FILE BODY (SCORE: 30), not raw (SCORE: 95).
    on_disk = sat_doc_path.read_text(encoding="utf-8")
    assert on_disk == file_body, (
        f"AC8: disk doc must equal the pre-written file body (SCORE: 30 — file wins). "
        f"Today _write_satisfaction_doc overwrites with raw (SCORE: 95). "
        f"Disk doc head: {on_disk[:100]!r}"
    )

    # Event: satisfaction_writer_return_source with source='worker_file'.
    source_events = [e for e in captured if e["type"] == "satisfaction_writer_return_source"]
    assert len(source_events) >= 1, (
        f"AC8: 'satisfaction_writer_return_source' event must be emitted. "
        f"Today it is never emitted (§2.4). "
        f"All captured event types: {[e['type'] for e in captured]}"
    )
    sources = {e["payload"].get("source") for e in source_events}
    assert "worker_file" in sources, (
        f"AC8: satisfaction_writer_return_source must have source='worker_file' "
        f"(file was pre-written by worker). Got {sources!r}"
    )


# ─── AC9: fail-open — file absent, raw used ────────────────────────────────────


def test_ac9_fail_open_file_absent_raw_used_as_body(tmp_path, monkeypatch):
    """AC9: file ABSENT; raw carries full valid body + SCORE: 90 + structured satisfied=true.
    After _write_satisfaction_doc:
      - disk doc == raw
      - 'satisfaction_writer_return_source' event with source='raw_response_fallback'

    FAILS today: 'satisfaction_writer_return_source' event is never emitted today.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH
    sat_doc_path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately NOT writing the file (simulates worker did not write).
    assert not sat_doc_path.exists(), "pre-condition: file must not exist before call"

    raw = _valid_raw_response(90)

    captured = _patch_emit(monkeypatch)

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n", encoding="utf-8")

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: PASS\n", encoding="utf-8")

    fix_doc = scratchpad / FIX_DOC_RELPATH
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE.\n", encoding="utf-8")

    ctx = _make_ctx(scratchpad)

    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(sat_doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC9: expected status='ok' (raw SCORE=90 >= threshold 80). "
        f"Got status={result.status!r}, error={result.error!r}"
    )

    on_disk = sat_doc_path.read_text(encoding="utf-8")
    assert on_disk == raw, (
        f"AC9: disk doc must equal raw (fail-open: no file → use raw). "
        f"Disk doc: {on_disk[:100]!r}"
    )

    source_events = [e for e in captured if e["type"] == "satisfaction_writer_return_source"]
    assert len(source_events) >= 1, (
        f"AC9: 'satisfaction_writer_return_source' event must be emitted. "
        f"Today it is never emitted (§2.4). "
        f"All captured event types: {[e['type'] for e in captured]}"
    )
    sources = {e["payload"].get("source") for e in source_events}
    assert "raw_response_fallback" in sources, (
        f"AC9: source must be 'raw_response_fallback' (no file → fall-open to raw). "
        f"Got {sources!r}"
    )


# ─── AC10: satisfaction_bytes_written == len(body.encode()) not len(raw) ───────


def test_ac10_satisfaction_bytes_written_equals_file_body_bytes(tmp_path, monkeypatch):
    """AC10: in the AC8 channel-split scenario (file body != raw),
    result.data['satisfaction_bytes_written'] == len(file_body.encode('utf-8'))
    (disk-truth = body, not raw).

    FAILS today: satisfaction_bytes_written = len(raw.encode('utf-8')) (line 2859).
    When file_body != raw this assertion fires.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH
    sat_doc_path.parent.mkdir(parents=True, exist_ok=True)

    # File body is distinct from raw (different length).
    file_body = (
        "# Satisfaction Evaluation\n\n"
        "## Per-Dimension Scores\n"
        "- Spec compliance: 95\n\n"
        "## Evidence Per Dimension\n"
        "- All checks passed. Evidence in test files.\n\n"
        "## Concerns\n"
        "none\n\n"
        "## Composite\n"
        "SCORE: 95\n\n"
        "VERDICT: PASS\n\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        '{"satisfied": true, "fixes_required": []}\n'
        "```\n"
        "EXTRA FILE BODY CONTENT THAT IS NOT IN RAW\n"
    )
    sat_doc_path.write_text(file_body, encoding="utf-8")

    # raw is shorter — the short control tail the worker would return.
    raw = (
        "SCORE: 95\n"
        "VERDICT: PASS\n\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        '{"satisfied": true, "fixes_required": []}\n'
        "```\n"
    )

    # Lengths must differ so the assertion is non-trivial.
    assert len(file_body.encode("utf-8")) != len(raw.encode("utf-8")), (
        "test setup: file_body and raw must have different byte lengths for AC10"
    )

    captured = _patch_emit(monkeypatch)

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n", encoding="utf-8")

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: PASS\n", encoding="utf-8")

    fix_doc = scratchpad / FIX_DOC_RELPATH
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE.\n", encoding="utf-8")

    ctx = _make_ctx(scratchpad)

    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(sat_doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )

    result = _write_satisfaction_doc(ctx, prev)

    assert result.data is not None, "result.data must not be None"
    expected_bytes = len(file_body.encode("utf-8"))
    actual_bytes = result.data.get("satisfaction_bytes_written")
    assert actual_bytes == expected_bytes, (
        f"AC10: satisfaction_bytes_written must equal len(body.encode()) = {expected_bytes} "
        f"(disk-truth = file body, not raw). "
        f"Today it equals len(raw.encode()) = {len(raw.encode('utf-8'))} (line 2859). "
        f"Got {actual_bytes!r}."
    )


# ─── AC11: multi-eval COMPLEX end-to-end still routes to _write_satisfaction_doc_multi ─


def test_ac11_multi_eval_complex_routes_to_composite_path(tmp_path, monkeypatch):
    """AC11: a 3-evaluator is_multi_evaluator prev still routes to
    _write_satisfaction_doc_multi and writes a composite doc.

    Regression guard: this tests CURRENT behavior that must be preserved post-GREEN.
    Multi-eval path is UNCHANGED (deferred to slice-6d, §4).
    MAY PASS today AND must pass post-GREEN (regression guard — asserts current behavior).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH
    sat_doc_path.parent.mkdir(parents=True, exist_ok=True)

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n", encoding="utf-8")

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: PASS\n", encoding="utf-8")

    fix_doc = scratchpad / FIX_DOC_RELPATH
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX COMPLETE.\n", encoding="utf-8")

    captured = _patch_emit(monkeypatch)

    # Stub _persist_satisfaction_last_findings to avoid needing real review structured data.
    monkeypatch.setattr(
        phase_6_review,
        "_persist_satisfaction_last_findings",
        lambda ctx, prev, score, threshold: None,
        raising=False,
    )

    eval_raw = _valid_raw_response(90)

    # Build a multi-evaluator prev (3 evaluators all passing).
    prev = StepResult(
        status="ok",
        data={
            "raw_response": eval_raw,
            "doc_path": str(sat_doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "is_multi_evaluator": True,
            "evaluator_responses": [
                {"index": 0, "status": "ok", "raw_response": eval_raw, "error_code": None},
                {"index": 1, "status": "ok", "raw_response": eval_raw, "error_code": None},
                {"index": 2, "status": "ok", "raw_response": eval_raw, "error_code": None},
            ],
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )

    ctx = _make_ctx(scratchpad)
    result = _write_satisfaction_doc(ctx, prev)

    # Composite doc must be written.
    assert sat_doc_path.exists(), (
        "AC11 regression guard: _write_satisfaction_doc must write a composite doc "
        "for multi-evaluator prev (routes to _write_satisfaction_doc_multi)."
    )
    composite = sat_doc_path.read_text(encoding="utf-8")
    assert "Multi-Evaluator Satisfaction Review" in composite, (
        f"AC11 regression guard: composite doc must contain 'Multi-Evaluator Satisfaction Review'. "
        f"Composite head: {composite[:200]!r}"
    )

    # Result must be ok (3 valid evaluators all passing → majority_passed=True).
    assert result.status == "ok", (
        f"AC11 regression guard: 3 passing evaluators must yield status='ok'. "
        f"Got status={result.status!r}, error={result.error!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    _sys.exit(pytest.main([__file__, "--tb=short"]))
