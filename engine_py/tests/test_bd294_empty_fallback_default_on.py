"""RED tests for bd#294 - the empty-fallback review check is ON by default.

Spec: docs/decisions/2026-10-13-bd294-empty-fallback-review-enforce-default.md

All cells in this file fail on the pre-GREEN production code (default is shadow, the event
flag has no `switched_off` key, the date seam attributes exist, the flip-horizon token is
still in the catalog). No guard cells live here.

AC -> test map
  AC1  test_bd294_ac1_default_enforces_with_nothing_set
  AC2  test_bd294_ac2_switch_zero_is_shadow_with_switched_off_marker
  AC4  test_bd294_ac4_alias_zero_switches_check_off[*]
       test_bd294_ac4_hal_name_wins_over_alias[*]
  AC6  test_bd294_ac6_flip_horizon_does_not_name_flag[*]
  AC7  test_bd294_ac7_no_date_seam_attributes[*]
  AC11 test_bd294_ac11_flag_one_enforces

No clock is pinned (the date seam is deleted by the spec). Flag state is pinned per test via
monkeypatch env. No singleton/timing resources (workflows.md 1i).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.workflows import phase_6_review as p6
from bytedigger_engine.workflows import phase_7_synthesize as p7

NEW_CODE = "E_REVIEW_EMPTY_FALLBACK"
NEW_EVENT = "review_empty_fallback"
REJECTED_NAME = "build-review.rejected.md"
FLAG = "HAL_REVIEW_EMPTY_FALLBACK_ENFORCE"
ALIAS_NAMES = ("BD_REVIEW_EMPTY_FALLBACK_ENFORCE", "BYTEDIGGER_REVIEW_EMPTY_FALLBACK_ENFORCE")
REPO_ROOT = Path(__file__).resolve().parents[2]
FLIP_HORIZON = REPO_ROOT / "scripts" / "flip_horizon.py"
FLIP_LEDGER = REPO_ROOT / "scripts" / "flip_horizon_ledger.json"


@pytest.fixture(autouse=True)
def _hermetic_flag(monkeypatch):
    """Default state for every test: the flag and both aliases UNSET. No clock."""
    monkeypatch.delenv(FLAG, raising=False)
    for alias in ALIAS_NAMES:
        monkeypatch.delenv(alias, raising=False)


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
    telemetry_ctx.set_current_run(event_log=s, run_id="bd294", step_name="write_review_artifact", phase="p6")
    monkeypatch.setattr(p6, "_resolve_backend", lambda *a, **kw: ("claude-in-session", "test"))
    yield s
    telemetry_ctx.clear_current_run()


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


def _rejected(doc_path: Path) -> Path:
    return doc_path.parent / REJECTED_NAME


def _assert_closed(result: StepResult, sink: _Sink, reason: str) -> dict:
    assert result.status == "error", (
        f"findingless fallback review must fail closed, got {result.status!r} "
        f"verdict={(result.data or {}).get('verdict')!r}"
    )
    assert result.error_code == NEW_CODE
    assert result.recoverable is False
    assert result.step_name == "write_review_artifact"
    events = sink.named(NEW_EVENT)
    assert len(events) == 1, f"exactly one {NEW_EVENT} event expected, got {sink.names()}"
    assert events[0]["reason"] == reason
    assert events[0]["phase"] == 6
    assert isinstance(events[0]["bytes"], int)
    assert events[0].get("mode") == "enforce", f"event must carry mode=enforce, got {events[0]!r}"
    flag = events[0].get("flag") or {}
    assert flag.get("name") == FLAG
    assert flag.get("switched_off") is False, f"enforcing run must not be marked switched_off: {flag!r}"
    return events[0]


def _assert_shadow(result: StepResult, sink: _Sink, doc_path: Path, raw: str, reason: str) -> dict:
    """Shadow mode: step continues (ok, doc + fix feed written) plus event + rejected copy."""
    assert result.status == "ok", f"shadow must not fail the step: {result.error_code}: {result.error}"
    events = sink.named(NEW_EVENT)
    assert len(events) == 1, f"exactly one {NEW_EVENT} event expected, got {sink.names()}"
    ev = events[0]
    assert ev["reason"] == reason
    assert ev["phase"] == 6
    assert ev["bytes"] == len(raw.encode("utf-8"))
    assert ev.get("mode") == "shadow"
    flag = ev.get("flag") or {}
    assert flag.get("name") == FLAG
    assert flag.get("switched_off") is True, f"kill-switch run must be marked switched_off: {flag!r}"
    assert doc_path.is_file(), "shadow: normalised build-review.md still persisted"
    assert _fix_doc(doc_path).is_file(), "shadow: fix feed still written"
    assert _rejected(doc_path).is_file(), "shadow: diagnosis copy written to build-review.rejected.md"
    assert _rejected(doc_path).read_bytes() == raw.encode("utf-8"), "pre-normalisation bytes"
    return ev


def _assert_evidence_not_impersonating(tmp_path: Path, doc_path: Path, rejected_bytes: bytes) -> None:
    assert not doc_path.exists(), "build-review.md must be ABSENT after the failure (phase 7 must see MISSING)"
    assert _rejected(doc_path).is_file(), "diagnosis bytes must be kept as build-review.rejected.md"
    assert _rejected(doc_path).read_bytes() == rejected_bytes
    assert not _fix_doc(doc_path).exists(), "fix feed must NOT be present on the fail-closed path"
    scratch = tmp_path / "scratch"
    assert not (scratch / p7.REVIEW_DOC_RELPATH).is_file()


# --- AC1: default enforces ----------------------------------------------------


def test_bd294_ac1_default_enforces_with_nothing_set(tmp_path, sink):
    raw = "Looks good to me."
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, "no_findings_parsed")
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


# --- AC2: "0" is the kill switch ----------------------------------------------


def test_bd294_ac2_switch_zero_is_shadow_with_switched_off_marker(tmp_path, sink, monkeypatch):
    monkeypatch.setenv(FLAG, "0")
    raw = "Looks good to me."
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_shadow(result, sink, doc_path, raw, "no_findings_parsed")


# --- AC11: explicit "1" enforces ----------------------------------------------


def test_bd294_ac11_flag_one_enforces(tmp_path, sink, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    raw = "Looks good to me."
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, "no_findings_parsed")
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


# --- AC4: BD_ / BYTEDIGGER_ aliases -------------------------------------------


@pytest.mark.parametrize("alias", ALIAS_NAMES)
def test_bd294_ac4_alias_zero_switches_check_off(tmp_path, sink, monkeypatch, alias):
    monkeypatch.setenv(alias, "0")  # HAL_ name stays unset
    raw = "Looks good to me."
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_shadow(result, sink, doc_path, raw, "no_findings_parsed")


@pytest.mark.parametrize("alias", ALIAS_NAMES)
def test_bd294_ac4_hal_name_wins_over_alias(tmp_path, sink, monkeypatch, alias):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(alias, "0")
    raw = "Looks good to me."
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, "no_findings_parsed")
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


# --- AC6: the dated horizon is gone -------------------------------------------


@pytest.mark.parametrize("today", ["2026-10-18", "2026-11-04"])
def test_bd294_ac6_flip_horizon_does_not_name_flag(today):
    proc = subprocess.run(
        [sys.executable, str(FLIP_HORIZON), "--check", "--today", today],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert FLAG not in out, f"flip_horizon --today {today} still names {FLAG}:\n{out}"
    # The ledger must not carry an entry for the flag either (folded in: bare ledger check alone passes today).
    assert FLAG not in FLIP_LEDGER.read_text(encoding="utf-8")


# --- AC7: no date seam --------------------------------------------------------


@pytest.mark.parametrize("attr", ["_utc_today", "_EMPTY_FALLBACK_FLAG_EXPIRES", "_EMPTY_FALLBACK_FLAG_OWNER"])
def test_bd294_ac7_no_date_seam_attributes(attr):
    assert not hasattr(p6, attr), f"phase_6_review.{attr} must be removed (no dated rollout)"
