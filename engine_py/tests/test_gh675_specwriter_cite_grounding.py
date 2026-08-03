"""RED tests for GH675A — spec-writer citation grounding.

Layer 1: `_grounded_citation_contract()` gains bare-symbol grounding rules.
Layer 2: new deterministic in-phase pre-lint step `verify_spec_cite_prelint`
  (`_verify_spec_cite_prelint`, `_inphase_unresolved_symbols`) inserted
  between `write_spec_doc` and `verify_spec_completeness`.

`_grounded_citation_contract` and `phase_45_spec_workflow` already exist and
are imported at module top level. `_verify_spec_cite_prelint` and
`_inphase_unresolved_symbols` do NOT exist yet — looked up via `getattr`
INSIDE each test body so the file COLLECTS cleanly (§1q ext / D1CF5FDF).
Module-level `sys.path` setup is done by conftest.py — no path mutation here.

Do NOT implement any production change here — RED-only file.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bytedigger_engine.workflows import phase_45_spec
from bytedigger_engine.contracts import StepResult


def _ctx(org_config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(org_config=org_config or {})


def _prelint_fn():
    """Deferred lookup — raises AssertionError (not ImportError) until GREEN ships."""
    fn = getattr(phase_45_spec, "_verify_spec_cite_prelint", None)
    assert fn is not None, "_verify_spec_cite_prelint not yet implemented (GREEN pending)"
    return fn


def _unresolved_helper():
    fn = getattr(phase_45_spec, "_inphase_unresolved_symbols", None)
    assert fn is not None, "_inphase_unresolved_symbols not yet implemented (GREEN pending)"
    return fn


def _dirty_fixture(tmp_path):
    """A repo root + spec file with one resolvable symbol and one bogus symbol."""
    code_file = tmp_path / "helpers.py"
    code_file.write_text("def real_helper():\n    pass\n")
    spec_text = (
        "The function `real_helper` in `helpers.py` performs validation.\n"
        "The function `ZzzHallucinated_gh675` in `helpers.py` performs the lookup.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)
    return spec_path, tmp_path


def _clean_fixture(tmp_path):
    """A repo root + spec file with zero unresolved citations."""
    code_file = tmp_path / "helpers.py"
    code_file.write_text("def real_helper():\n    pass\n")
    spec_text = "The function `real_helper` in `helpers.py` performs validation.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)
    return spec_path, tmp_path


# ── AC1: contract hardened to cover bare-symbol citations ──────────────────


def test_ac1_contract_covers_bare_symbols():
    """AC1: _grounded_citation_contract() text constrains bare backtick symbol
    citations (existing today: only <path>:<line> is covered)."""
    text = phase_45_spec._grounded_citation_contract()
    lowered = text.lower()
    assert "symbol" in lowered and "read" in lowered, (
        "contract should mention 'symbol' at all (weak baseline check)"
    )
    # Forcing assertion: the contract must explicitly require that a symbol
    # cited as EXISTING was seen via Read this turn — the spec's Layer-1
    # directive. The current contract only ever discusses <path>:<line>.
    assert "this turn" in lowered and (
        "symbol" in lowered.split("this turn")[0][-400:]
        or "backtick" in lowered
    ), (
        "expected a directive tying cited SYMBOLS (not just path:line) to "
        "files Read this turn; contract text:\n" + text
    )


# ── AC2: _inphase_unresolved_symbols flags bogus symbol, excludes real one ──


def test_ac2_inphase_helper_flags_bogus_symbol(tmp_path):
    fn = _unresolved_helper()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    result = fn(spec_path, repo_root)

    assert "ZzzHallucinated_gh675" in result, (
        f"expected bogus symbol in unresolved list, got {result!r}"
    )
    assert "real_helper" not in result, (
        f"expected resolvable symbol excluded from unresolved list, got {result!r}"
    )


# ── AC3: emits spec_cite_prelint_result with correct count/symbols ─────────


def test_ac3_emits_result_event_with_count(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)))

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="write_spec_doc")

    fn(ctx, prev)

    events = [p for et, p in captured if et == "spec_cite_prelint_result"]
    assert len(events) == 1, f"expected 1 spec_cite_prelint_result, got: {captured!r}"
    payload = events[0]
    assert payload.get("unresolved_count") == 1, (
        f"expected unresolved_count==1, got {payload.get('unresolved_count')!r}; payload={payload!r}"
    )
    assert "ZzzHallucinated_gh675" in payload.get("symbols", []), (
        f"expected bogus symbol in symbols payload, got {payload.get('symbols')!r}"
    )


# ── AC4: also emits _warn + non-blocking ok + data key set ─────────────────


def test_ac4_nonblocking_advance_and_data_key(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)))

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="write_spec_doc")

    result = fn(ctx, prev)

    warn_events = [p for et, p in captured if et == "spec_cite_prelint_warn"]
    assert len(warn_events) == 1, f"expected 1 spec_cite_prelint_warn, got: {captured!r}"

    assert result.status == "ok", f"expected non-blocking status='ok', got {result.status!r}"
    unresolved = result.data.get("spec_cite_prelint_unresolved")
    assert unresolved == ["ZzzHallucinated_gh675"], (
        f"expected data['spec_cite_prelint_unresolved']==['ZzzHallucinated_gh675'], got {unresolved!r}"
    )


# ── AC5: frozen fast-path — skip, no lint/emit ──────────────────────────────


def test_ac5_frozen_skip_no_lint(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)))

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(
        status="ok",
        data={"spec_path": str(spec_path), "is_frozen": True},
        duration_ms=0,
        step_name="write_spec_doc",
    )

    result = fn(ctx, prev)

    assert result.status == "skip", f"expected status='skip' on frozen spec, got {result.status!r}"
    result_events = [et for et, p in captured if et == "spec_cite_prelint_result"]
    assert result_events == [], (
        f"frozen fast-path must NOT run lint/emit; captured events: {captured!r}"
    )


# ── AC6: kill-switch — skip, no lint/emit ───────────────────────────────────


def test_ac6_killswitch_skip(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _dirty_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)))

    ctx = _ctx({"hal_root": str(repo_root), "spec_cite_prelint_skip": True})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="write_spec_doc")

    result = fn(ctx, prev)

    assert result.status == "ok", f"expected status='ok' on kill-switch, got {result.status!r}"
    assert result.data.get("spec_cite_prelint_skipped") == "config_skip", (
        f"expected data['spec_cite_prelint_skipped']=='config_skip', got {result.data.get('spec_cite_prelint_skipped')!r}"
    )
    assert captured == [], f"kill-switch must NOT lint/emit; captured events: {captured!r}"


# ── AC7: clean spec → clean=True, unresolved_count==0; resume-idempotent ───


def test_ac7_clean_spec_and_resume_idempotent(tmp_path, monkeypatch):
    fn = _prelint_fn()
    spec_path, repo_root = _clean_fixture(tmp_path)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, p: captured.append((et, p)))

    ctx = _ctx({"hal_root": str(repo_root)})
    prev = StepResult(status="ok", data={"spec_path": str(spec_path)}, duration_ms=0, step_name="write_spec_doc")

    result1 = fn(ctx, prev)

    events1 = [p for et, p in captured if et == "spec_cite_prelint_result"]
    assert len(events1) == 1, f"expected 1 spec_cite_prelint_result, got: {captured!r}"
    assert events1[0].get("unresolved_count") == 0, (
        f"expected unresolved_count==0 for clean spec, got {events1[0].get('unresolved_count')!r}"
    )
    assert result1.status == "ok"
    assert result1.data.get("spec_cite_prelint_clean") is True, (
        f"expected data['spec_cite_prelint_clean'] is True, got {result1.data.get('spec_cite_prelint_clean')!r}"
    )

    # Re-entry (resume/replay) — same prev, same result, no double-terminal.
    captured.clear()
    result2 = fn(ctx, prev)

    events2 = [p for et, p in captured if et == "spec_cite_prelint_result"]
    assert len(events2) == 1, f"expected 1 spec_cite_prelint_result on re-entry, got: {captured!r}"
    assert events2[0].get("unresolved_count") == 0, (
        f"expected unresolved_count==0 on re-entry, got {events2[0].get('unresolved_count')!r}"
    )
    assert result2.status == "ok", f"expected status='ok' on re-entry, got {result2.status!r}"
    assert result2.status == result1.status
    assert result2.data.get("spec_cite_prelint_clean") is True


# ── AC8: step registered between write_spec_doc and verify_spec_completeness ─


def test_ac8_step_registered_before_completeness():
    workflow = phase_45_spec.phase_45_spec_workflow()
    names = [s.name for s in workflow.steps]

    assert "verify_spec_cite_prelint" in names, (
        f"expected 'verify_spec_cite_prelint' registered in workflow steps, got: {names!r}"
    )
    idx_write = names.index("write_spec_doc")
    idx_completeness = names.index("verify_spec_completeness")
    idx_new = names.index("verify_spec_cite_prelint")
    idx_citations = names.index("verify_spec_citations")

    assert idx_write < idx_completeness, (
        f"expected write_spec_doc({idx_write}) < verify_spec_completeness({idx_completeness}); "
        f"names={names!r}"
    )
    assert idx_completeness < idx_new < idx_citations, (
        f"expected verify_spec_completeness({idx_completeness}) < verify_spec_cite_prelint({idx_new}) "
        f"< verify_spec_citations({idx_citations}); names={names!r}"
    )
