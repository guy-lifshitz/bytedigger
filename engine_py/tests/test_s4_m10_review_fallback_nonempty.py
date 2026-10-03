"""RED tests for S4/M10 - fail-closed check on the phase_6 review stdout-fallback path.

Spec (frozen draft r1): docs/decisions/2026-10-03-s4-m10-review-fallback-nonempty.md

AC -> test map
  AC1  test_ac1_empty_or_unstructured_fallback_fails_closed[*]
  AC2  test_ac2_recognised_verdicts_unchanged[*]                      (GUARD, green today)
  AC3  test_ac3_structured_empty_block_is_pass_no_event               (GUARD, green today)
  AC4  test_ac4_unrecognised_severity_fails_closed[*],
       test_ac4_mixed_and_lowercase_severity_keep_recognised_verdict[*]
  AC5  test_ac5_aggregator_suspect_still_ok                           (GUARD, green today)
  AC6  test_ac6_error_code_registered_with_description
  AC7  test_ac7_check_is_pure_function_of_content (behavioural: no LLM call, no env/flag dependence)

Expected RED today: AC1 (all cells), AC4 unrecognised cells, AC6, AC7.
GUARDs (green today on purpose): AC2, AC3, AC5, AC4 mixed/lowercase cells.

Singleton/timing: none (workflows.md 1i not applicable; nothing here races).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bytedigger_engine import error_codes, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.workflows import phase_6_review as p6

NEW_CODE = "E_REVIEW_EMPTY_FALLBACK"
NEW_EVENT = "review_empty_fallback"


# --- harness -----------------------------------------------------------------


class _Sink:
    """Real event-log sink: what telemetry_ctx.emit_safe appends to."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload))

    def named(self, name: str) -> list[dict]:
        return [p for n, p in self.events if n == name]

    def names(self) -> list[str]:
        return [n for n, _ in self.events]


@pytest.fixture()
def sink(monkeypatch):
    telemetry_ctx.clear_current_run()
    s = _Sink()
    telemetry_ctx.set_current_run(event_log=s, run_id="s4m10", step_name="write_review_artifact", phase="p6")
    # Backend identity is only reported in a sibling event; pin it so no config is read.
    monkeypatch.setattr(p6, "_resolve_backend", lambda *a, **kw: ("claude-in-session", "test"))
    yield s
    telemetry_ctx.clear_current_run()


def _structured(findings: list) -> str:
    """The real structured-block shape (findings_extractor._STRUCTURED_SECTION_RE)."""
    return "## Findings (structured)\n```json\n" + json.dumps(findings) + "\n```\n"


def _finding(sev, fid: str = "F1") -> dict:
    return {"id": fid, "severity": sev, "path": "src/x.py:1", "description": "d"}


def _fallback_prev(tmp_path: Path, raw: str) -> tuple[StepResult, Path]:
    doc_path = tmp_path / "scratch" / "reviews" / "build-review.md"
    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "spec_path": "/dev/null",
            "red_log_path": "/dev/null",
            "green_log_path": "/dev/null",
            # no aggregated_content -> stdout-fallback branch
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )
    return prev, doc_path


def _fix_doc(doc_path: Path) -> Path:
    return doc_path.parent / Path(p6.REVIEW_FIX_DOC_RELPATH).name


# --- AC1 ---------------------------------------------------------------------

_AC1_CELLS = [
    ("", "body_empty"),
    ("   \n", "body_empty"),
    ("Looks good to me.", "no_structured_block"),
    ("VERDICT: PASS", "no_structured_block"),
    ("## Findings\n[]", "no_structured_block"),
    ("```json\n[]\n```\n", "no_structured_block"),
]


@pytest.mark.parametrize("raw,reason", _AC1_CELLS, ids=["empty", "whitespace", "prose", "verdict_pass_only", "findings_plain_list", "json_fence_no_structured_header"])
def test_ac1_empty_or_unstructured_fallback_fails_closed(tmp_path, sink, raw, reason):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)

    assert result.status == "error", f"fallback review with no findings must fail closed, got {result.status!r} verdict={(result.data or {}).get('verdict')!r}"
    assert result.error_code == NEW_CODE
    assert result.recoverable is False
    assert result.step_name == "write_review_artifact"

    events = sink.named(NEW_EVENT)
    assert len(events) == 1, f"exactly one {NEW_EVENT} event expected, got {sink.names()}"
    assert events[0]["reason"] == reason
    assert events[0]["phase"] == 6
    assert isinstance(events[0]["bytes"], int)

    assert doc_path.is_file(), "review doc must still be persisted for diagnosis"
    assert not _fix_doc(doc_path).exists(), "fix feed must NOT be written on the fail-closed path"


# --- AC2 (guard) -------------------------------------------------------------

_AC2_CELLS = [
    (_structured([_finding("MEDIUM")]), p6.VERDICT_PARTIAL),
    (_structured([_finding("CRITICAL")]), p6.VERDICT_FAIL),
    ("Found a real bug in foo, details in prose.\nVERDICT: FAIL\n", p6.VERDICT_FAIL),
]


@pytest.mark.parametrize("raw,verdict", _AC2_CELLS, ids=["medium", "critical", "prose_fail_marker"])
def test_ac2_recognised_verdicts_unchanged(tmp_path, sink, raw, verdict):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["verdict"] == verdict
    assert NEW_EVENT not in sink.names()
    assert _fix_doc(doc_path).is_file()


# --- AC3 (guard) -------------------------------------------------------------


def test_ac3_structured_empty_block_is_pass_no_event(tmp_path, sink):
    prev, _ = _fallback_prev(tmp_path, _structured([]))
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["verdict"] == p6.VERDICT_PASS
    assert NEW_EVENT not in sink.names()


# --- AC4 ---------------------------------------------------------------------

_AC4_BAD = [
    [_finding("INFO")],
    [{"id": "F1", "path": "src/x.py:1", "description": "d"}],  # severity missing
    [_finding(5)],  # non-string severity
]


@pytest.mark.parametrize("findings", _AC4_BAD, ids=["info", "missing", "non_string"])
def test_ac4_unrecognised_severity_fails_closed(tmp_path, sink, findings):
    prev, doc_path = _fallback_prev(tmp_path, _structured(findings))
    result = p6._write_review_artifact(None, prev)

    assert result.status == "error", f"all-unrecognised severities must not read as PASS, got {result.status!r} verdict={(result.data or {}).get('verdict')!r}"
    assert result.error_code == NEW_CODE
    events = sink.named(NEW_EVENT)
    assert len(events) == 1
    assert events[0]["reason"] == "unrecognised_severity"
    assert doc_path.is_file()
    assert not _fix_doc(doc_path).exists()


_AC4_KEEP = [
    ([_finding("INFO", "F1"), _finding("LOW", "F2")], p6.VERDICT_PARTIAL),
    ([_finding(" high ")], p6.VERDICT_FAIL),
]


@pytest.mark.parametrize("findings,verdict", _AC4_KEEP, ids=["info_plus_low", "lowercase_padded_high"])
def test_ac4_mixed_and_lowercase_severity_keep_recognised_verdict(tmp_path, sink, findings, verdict):
    prev, _ = _fallback_prev(tmp_path, _structured(findings))
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["verdict"] == verdict
    assert NEW_EVENT not in sink.names()


# --- AC5 (guard) -------------------------------------------------------------


def test_ac5_aggregator_suspect_still_ok(tmp_path, sink):
    doc_path = tmp_path / "scratch" / "reviews" / "build-review.md"
    suspect_block = "### SEVERITY: HIGH - unverified claim\n> src/x.py:1: def f():\nDescription: could not verify.\n"
    prev = StepResult(
        status="ok",
        data={
            "aggregated_content": (
                "# Composite Review\n\n## Aggregated Findings\n\n(no findings)\n\n"
                "## Suspect Findings (LOW CONFIDENCE)\n\n" + suspect_block + "\nVERDICT: FAIL\n"
            ),
            "verdict": p6.VERDICT_SUSPECT,
            "verified_findings": [],
            "suspect_findings": [{"block": suspect_block}],
            "doc_path": str(doc_path),
            "spec_path": "/dev/null",
            "red_log_path": "/dev/null",
            "green_log_path": "/dev/null",
        },
        duration_ms=0,
        step_name="aggregate_review_findings",
    )
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["verdict"] == p6.VERDICT_SUSPECT
    assert NEW_EVENT not in sink.names()
    assert _fix_doc(doc_path).is_file()


# --- AC6 ---------------------------------------------------------------------


def test_ac6_error_code_registered_with_description():
    desc = error_codes.ERROR_CODES.get(NEW_CODE)
    assert isinstance(desc, str) and desc.strip(), f"{NEW_CODE} must be registered with a non-empty description"


# --- AC7 (behavioural form of the static AC) ---------------------------------


def test_ac7_check_is_pure_function_of_content(tmp_path, sink, monkeypatch):
    """No LLM call and no environment/flag dependence: with the LLM entry point
    booby-trapped and a hostile environment, the empty-body verdict is the same."""
    calls: list = []

    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("fail-closed check must not call an LLM")

    monkeypatch.setattr(p6, "invoke_llm_subprocess", _boom)
    monkeypatch.setenv("BYTEDIGGER_FLAGS", "{}")

    prev, _ = _fallback_prev(tmp_path, "")
    result = p6._write_review_artifact(None, prev)

    assert not calls
    assert result.status == "error"
    assert result.error_code == NEW_CODE
