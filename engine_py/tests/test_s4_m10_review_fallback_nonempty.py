"""RED tests for S4/M10 - fail-closed check on the phase_6 review stdout-fallback path.

Spec (r2): docs/decisions/2026-10-03-s4-m10-review-fallback-nonempty.md
Gate r1:   docs/decisions/2026-10-03-s4-m10-gate-r1.md

AC -> test map
  AC1  test_ac1_findingless_fallback_fails_closed[*]            (RED)
  AC1b test_ac1b_disk_first_findingless_doc_moved_stale_fix_removed (RED)
  AC2  test_ac2_recognised_verdicts_unchanged[*]                (GUARD, incl. PARTIAL gap pin)
  AC2b test_ac2b_severity_blocks_without_json_stay_ok[*]        (GUARD)
  AC3  test_ac3_structured_empty_block_is_pass_no_event         (GUARD)
  AC4  test_ac4_unrecognised_severity_fails_closed[*]           (RED)
       test_ac4_mixed_and_lowercase_severity_keep_recognised_verdict[*] (GUARD)
       test_ac4_unrecognised_block_with_fail_marker_is_fail      (RED: today PASS)
  AC4b test_ac4b_non_object_or_malformed_structured_fails_closed[*] (RED)
       test_ac4b_prompt_template_echo_fails_closed              (RED)
  AC5  test_ac5_aggregator_suspect_still_ok                     (GUARD)
  AC6  test_ac6_error_code_registered_with_description          (RED)
       test_ac6_class_registry_entry_is_terminal                (RED)
  AC7  test_ac7_check_is_pure_function_of_content               (RED via outcome)

  r4 additions (rollout flag HAL_REVIEW_EMPTY_FALLBACK_ENFORCE, spec 1a):
  AC1/AC1b/AC4/AC4b/AC7/rejected-file cells run with the flag ON (`enforce` fixture).
  AC2/AC2b/AC3/AC4-keep/AC4-marker/AC5 guards run with the flag OFF and ON (`any_mode`).
  AC8  test_ac8_*  shadow (flag unset, date before expiry)       (RED)
  AC9  test_ac9_*  expiry via the `_utc_today` seam               (RED)
  AC10 test_ac10_* non-object list, GH1399 test_ac9 shape         (RED)
  AC6b test_ac6b_flag_registered_in_catalog                        (RED)

Singleton/timing: none (workflows.md 1i: no lock/port/throttle; the only time
dependence is the module helper `_utc_today`, pinned for EVERY test by an autouse
fixture, so the suite is date-independent). Flag state is pinned per test via
monkeypatch env (delenv / setenv "1"; `get_config().flag` is true iff env == "1").
"""
from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path

import pytest

from bytedigger_engine import error_codes, flags_catalog, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.lib.plugins.review_schema import STRUCTURED_FINDINGS_JSON_TEMPLATE
from bytedigger_engine.workflows import phase_6_review as p6
from bytedigger_engine.workflows import phase_7_synthesize as p7

NEW_CODE = "E_REVIEW_EMPTY_FALLBACK"
NEW_EVENT = "review_empty_fallback"
REJECTED_NAME = "build-review.rejected.md"
FLAG = "HAL_REVIEW_EMPTY_FALLBACK_ENFORCE"
EXPIRES = "2026-10-17"
PINNED_TODAY = dt.date(2026, 10, 3)  # inside the shadow window


def _pin_today(monkeypatch, day: dt.date, *, raising: bool = False) -> None:
    monkeypatch.setattr(p6, "_utc_today", lambda: day, raising=raising)


@pytest.fixture(autouse=True)
def _hermetic_flag_and_clock(monkeypatch):
    """Default state for every test: flag UNSET, date inside the shadow window."""
    monkeypatch.delenv(FLAG, raising=False)
    # raising=False: the seam does not exist until GREEN; tests that need it use raising=True.
    _pin_today(monkeypatch, PINNED_TODAY, raising=False)


@pytest.fixture()
def enforce(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


@pytest.fixture(params=["flag_off", "flag_on"])
def any_mode(request, monkeypatch):
    """Guard cells must hold with the flag off (shadow) AND on (enforce)."""
    if request.param == "flag_on":
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    return request.param


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
    assert str(flag.get("owner", "")).strip()
    assert flag.get("expires") == EXPIRES
    return events[0]


def _assert_shadow(result: StepResult, sink: _Sink, doc_path: Path, raw: str, reason: str) -> dict:
    """Shadow mode: step continues as today (ok, doc + fix feed written) plus event + rejected copy."""
    assert result.status == "ok", f"shadow must not fail the step: {result.error_code}: {result.error}"
    assert result.data["verdict"] in (p6.VERDICT_SUSPECT, p6.VERDICT_PASS), "verdict unchanged vs today"
    events = sink.named(NEW_EVENT)
    assert len(events) == 1, f"exactly one {NEW_EVENT} event expected, got {sink.names()}"
    ev = events[0]
    assert ev["reason"] == reason
    assert ev["phase"] == 6
    assert ev["bytes"] == len(raw.encode("utf-8"))
    assert ev.get("mode") == "shadow"
    flag = ev.get("flag") or {}
    assert flag.get("name") == FLAG
    assert str(flag.get("owner", "")).strip(), "flag.owner must be non-empty"
    assert flag.get("expires") == EXPIRES
    assert flag.get("expired") is False
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
    # phase-7 style MISSING check: existence of REVIEW_DOC_RELPATH under the scratchpad.
    scratch = tmp_path / "scratch"
    assert not (scratch / p7.REVIEW_DOC_RELPATH).is_file()


# --- AC1 ---------------------------------------------------------------------

_AC1_CELLS = [
    ("", "body_empty"),
    ("   \n", "body_empty"),
    ("Looks good to me.", "no_findings_parsed"),
    ("VERDICT: PASS", "no_findings_parsed"),
    ("## Findings\n[]", "no_findings_parsed"),
    ("```json\n[]\n```\n", "no_findings_parsed"),
]


@pytest.mark.parametrize(
    "raw,reason", _AC1_CELLS,
    ids=["empty", "whitespace", "prose", "verdict_pass_only", "findings_plain_list", "json_fence_no_structured_header"],
)
def test_ac1_findingless_fallback_fails_closed(tmp_path, sink, enforce, raw, reason):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)

    event = _assert_closed(result, sink, reason)
    assert event["bytes"] == len(raw.encode("utf-8")), "bytes = pre-normalisation text, UTF-8"
    assert reason in (result.error or "") or REJECTED_NAME in (result.error or "")
    assert REJECTED_NAME in (result.error or ""), "error must name the rejected file"
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


# --- AC1b --------------------------------------------------------------------


def test_ac1b_disk_first_findingless_doc_moved_stale_fix_removed(tmp_path, sink, enforce):
    on_disk = "I reviewed the change and found nothing worth reporting.\n"
    prev, doc_path = _fallback_prev(tmp_path, "done")  # stdout is only a summary
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(on_disk, encoding="utf-8")  # real-LLM disk-first shape
    _fix_doc(doc_path).write_text("STALE fix doc from previous cycle\n", encoding="utf-8")

    result = p6._write_review_artifact(None, prev)

    event = _assert_closed(result, sink, "no_findings_parsed")
    assert event["bytes"] == len(on_disk.encode("utf-8")), "disk-first: bytes counts the on-disk text"
    _assert_evidence_not_impersonating(tmp_path, doc_path, on_disk.encode("utf-8"))


# --- AC2 (guard) -------------------------------------------------------------

_AC2_CELLS = [
    (_structured([_finding("MEDIUM")]), p6.VERDICT_PARTIAL),
    (_structured([_finding("CRITICAL")]), p6.VERDICT_FAIL),
    ("Found a real bug in foo, details in prose.\nVERDICT: FAIL\n", p6.VERDICT_FAIL),
    # Accepted gap pinned (spec 1): bare PARTIAL marker with no findings stays ok.
    ("Some concerns noted in prose only.\nVERDICT: PARTIAL\n", p6.VERDICT_PARTIAL),
]


@pytest.mark.parametrize("raw,verdict", _AC2_CELLS, ids=["medium", "critical", "prose_fail_marker", "prose_partial_marker_gap"])
def test_ac2_recognised_verdicts_unchanged(tmp_path, sink, any_mode, raw, verdict):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["verdict"] == verdict
    assert NEW_EVENT not in sink.names()
    assert doc_path.is_file()
    assert _fix_doc(doc_path).is_file()


# --- AC2b (guard) ------------------------------------------------------------

_SEV_BLOCKS = (
    "### SEVERITY: HIGH - Missing null check in handler\n"
    "> src/x.py:1: def handler(x):\n"
    "Description: x may be None.\n"
)

_AC2B_CELLS = [
    "## Aggregated Findings\n\n" + _SEV_BLOCKS,
    _SEV_BLOCKS,  # no header: normalised, blocks preserved
    "## Aggregated Findings\n\n" + _SEV_BLOCKS + "\nVERDICT: PASS\n",
    # blocks alongside a structured block with only unrecognised severities
    "## Aggregated Findings\n\n" + _SEV_BLOCKS + "\n" + _structured([_finding("INFO")]),
]


@pytest.mark.parametrize("raw", _AC2B_CELLS, ids=["with_header", "no_header", "verdict_pass_marker", "blocks_plus_unrecognised_json"])
def test_ac2b_severity_blocks_without_json_stay_ok(tmp_path, sink, any_mode, raw):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert NEW_EVENT not in sink.names()
    assert doc_path.is_file()
    fix = _fix_doc(doc_path)
    assert fix.is_file()
    assert "Missing null check in handler" in fix.read_text(encoding="utf-8")


# --- AC3 (guard) -------------------------------------------------------------


def test_ac3_structured_empty_block_is_pass_no_event(tmp_path, sink, any_mode):
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
def test_ac4_unrecognised_severity_fails_closed(tmp_path, sink, enforce, findings):
    raw = _structured(findings)
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)

    _assert_closed(result, sink, "unrecognised_severity")
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


_AC4_KEEP = [
    ([_finding("INFO", "F1"), _finding("LOW", "F2")], p6.VERDICT_PARTIAL),
    ([_finding(" high ")], p6.VERDICT_FAIL),
]


@pytest.mark.parametrize("findings,verdict", _AC4_KEEP, ids=["info_plus_low", "lowercase_padded_high"])
def test_ac4_mixed_and_lowercase_severity_keep_recognised_verdict(tmp_path, sink, any_mode, findings, verdict):
    prev, _ = _fallback_prev(tmp_path, _structured(findings))
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["verdict"] == verdict
    assert NEW_EVENT not in sink.names()


def test_ac4_unrecognised_block_with_fail_marker_is_fail(tmp_path, sink, any_mode):
    """Explicit VERDICT: FAIL alongside an INFO-only block: marker wins -> FAIL, not an error."""
    raw = _structured([_finding("INFO")]) + "\nVERDICT: FAIL\n"
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["verdict"] == p6.VERDICT_FAIL
    assert NEW_EVENT not in sink.names()
    assert _fix_doc(doc_path).is_file()


# --- AC4b --------------------------------------------------------------------

_AC4B_CELLS = [
    (_structured(["CRITICAL: sql injection"]), "no_findings_parsed"),  # non-object entries only
    ("## Findings (structured)\n```json\n{not valid json\n```\n", "no_findings_parsed"),  # malformed
    ("## Findings (structured)\n```json\n" + json.dumps({"findings": []}) + "\n```\n", "no_findings_parsed"),  # dict root
]


@pytest.mark.parametrize("raw,reason", _AC4B_CELLS, ids=["non_object_entries", "malformed_json", "dict_root"])
def test_ac4b_non_object_or_malformed_structured_fails_closed(tmp_path, sink, enforce, raw, reason):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, reason)
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


def test_ac4b_prompt_template_echo_fails_closed(tmp_path, sink, enforce):
    """Reviewer echoing the prompt's JSON template: severity 'CRITICAL|HIGH|MEDIUM|LOW' is not recognised."""
    raw = STRUCTURED_FINDINGS_JSON_TEMPLATE
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, "unrecognised_severity")
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


# --- AC5 (guard) -------------------------------------------------------------


def test_ac5_aggregator_suspect_still_ok(tmp_path, sink, any_mode):
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


def _load_class_registry() -> dict:
    """Read _CLASS_REGISTRY from the GH1399 test file by AST (no import of a test module)."""
    src_path = Path(__file__).parent / "test_GH1399_advisory_format_terminal.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "_CLASS_REGISTRY":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_CLASS_REGISTRY" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("_CLASS_REGISTRY not found in test_GH1399_advisory_format_terminal.py")


def test_ac6_class_registry_entry_is_terminal():
    entry = _load_class_registry().get(NEW_CODE)
    assert isinstance(entry, dict), f"{NEW_CODE} must have a _CLASS_REGISTRY entry"
    assert entry.get("remedy") == "terminal"
    assert str(entry.get("why", "")).strip()
    assert str(entry.get("missing", "")).strip()


# --- AC7 (behavioural form of the static AC) ---------------------------------


def test_ac7_check_is_pure_function_of_content(tmp_path, sink, enforce, monkeypatch):
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


# --- AC6b: rollout flag registered -------------------------------------------


def test_ac6b_flag_registered_in_catalog():
    entry = flags_catalog.FLAGS.get(FLAG)
    assert isinstance(entry, dict), f"{FLAG} must be registered in flags_catalog.FLAGS"
    assert entry.get("kind") == "flag"
    assert entry.get("default") == "0"
    desc = str(entry.get("description", ""))
    assert "s4-bytedigger" in desc, "description must name the owner"
    assert EXPIRES in desc, "description must carry the flip-by date"


# --- AC8: shadow (flag unset, before expiry) ---------------------------------

_AC4_SHADOW_CELLS = [
    (_structured([_finding("INFO")]), "unrecognised_severity"),
    (_structured([{"id": "F1", "path": "src/x.py:1", "description": "d"}]), "unrecognised_severity"),
    (_structured([_finding(5)]), "unrecognised_severity"),
]


@pytest.mark.parametrize(
    "raw,reason", _AC1_CELLS,
    ids=["empty", "whitespace", "prose", "verdict_pass_only", "findings_plain_list", "json_fence_no_structured_header"],
)
def test_ac8_shadow_ac1_cells_continue_with_event_and_rejected_copy(tmp_path, sink, raw, reason):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    assert result.data["verdict"] == p6.VERDICT_SUSPECT, "AC1 cells are SUSPECT today and stay so in shadow"
    _assert_shadow(result, sink, doc_path, raw, reason)


@pytest.mark.parametrize("raw,reason", _AC4_SHADOW_CELLS + _AC4B_CELLS + [(STRUCTURED_FINDINGS_JSON_TEMPLATE, "unrecognised_severity")],
                         ids=["info", "missing", "non_string", "non_object_entries", "malformed_json", "dict_root", "template_echo"])
def test_ac8_shadow_ac4_ac4b_cells_continue_with_event_and_rejected_copy(tmp_path, sink, raw, reason):
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_shadow(result, sink, doc_path, raw, reason)


def test_ac8_shadow_does_not_overwrite_existing_rejected_file(tmp_path, sink):
    prev, doc_path = _fallback_prev(tmp_path, "Looks good to me.")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    _rejected(doc_path).write_text("OLDER EVIDENCE\n", encoding="utf-8")
    result = p6._write_review_artifact(None, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    ev = sink.named(NEW_EVENT)
    assert len(ev) == 1 and ev[0].get("mode") == "shadow"
    assert _rejected(doc_path).read_text(encoding="utf-8") == "OLDER EVIDENCE\n"


# --- AC9: expiry via the _utc_today seam -------------------------------------


def test_ac9_on_expiry_day_flag_unset_is_still_shadow(tmp_path, sink, monkeypatch):
    _pin_today(monkeypatch, dt.date(2026, 10, 17), raising=True)
    raw = "Looks good to me."
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_shadow(result, sink, doc_path, raw, "no_findings_parsed")  # also pins expired is False


def test_ac9_after_expiry_flag_unset_enforces_and_marks_expired(tmp_path, sink, monkeypatch):
    _pin_today(monkeypatch, dt.date(2026, 10, 18), raising=True)
    raw = "Looks good to me."
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    ev = _assert_closed(result, sink, "no_findings_parsed")
    assert ev["flag"].get("expired") is True
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


def test_ac9_after_expiry_flag_set_enforces(tmp_path, sink, monkeypatch):
    _pin_today(monkeypatch, dt.date(2026, 10, 18), raising=True)
    monkeypatch.setenv(FLAG, "1")
    raw = ""
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, "body_empty")
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


def test_ac9_flag_set_before_expiry_enforces_not_expired(tmp_path, sink, enforce):
    prev, doc_path = _fallback_prev(tmp_path, "Looks good to me.")
    result = p6._write_review_artifact(None, prev)
    ev = _assert_closed(result, sink, "no_findings_parsed")
    assert ev["flag"].get("expired") is False


# --- AC10: r2 MAJOR cells (enforce) ------------------------------------------


@pytest.mark.parametrize(
    "findings", [["a", "b"], [1, None], ["CRITICAL: x", {"note": "no severity"}][:1]],
    ids=["strings", "non_objects", "single_string"],
)
def test_ac10_non_empty_list_with_zero_object_entries_fails_closed(tmp_path, sink, enforce, findings):
    raw = _structured(findings)
    prev, doc_path = _fallback_prev(tmp_path, raw)
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, "no_findings_parsed")
    _assert_evidence_not_impersonating(tmp_path, doc_path, raw.encode("utf-8"))


def test_ac10_gh1399_ac9_shape_disk_doc_findingless_doc_path_absent(tmp_path, sink, enforce):
    """GH1399 test_ac9 shape: no marker/JSON/severity blocks, reviewer wrote doc_path itself."""
    body = "SENTINEL-GH1399-BODY-VERBATIM\nno header on first attempt"
    prev, doc_path = _fallback_prev(tmp_path, "still no header on retry")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(body, encoding="utf-8")
    result = p6._write_review_artifact(None, prev)
    _assert_closed(result, sink, "no_findings_parsed")
    assert not doc_path.exists(), "doc_path must be absent (GH1399 test_ac9 rewrite target)"
    assert _rejected(doc_path).read_text(encoding="utf-8") == body
