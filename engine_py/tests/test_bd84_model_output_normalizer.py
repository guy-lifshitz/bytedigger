"""bd#84 — one model-output normalizer in front of the P1-P3 verdict/marker parsers;
a missing marker is re-rolled, not terminal; zero findings on a non-empty
review is suspicious, not clean.

The corpus under ``fixtures/model_output_corpus`` holds 60 real replies from
four models (see its ``manifest.json``). ``stated_verdict`` is the verdict the
reply actually states, read by hand — the parsers must recover it regardless
of the markdown dressing around it (bold, backticks, ``###`` headings).

UUTs are imported inside each test body (§1q).
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult

CORPUS = Path(__file__).resolve().parent / "fixtures" / "model_output_corpus"
_MANIFEST = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
_CASES = _MANIFEST["cases"]


def _cases(kind: str) -> list[dict]:
    return [c for c in _CASES if c["kind"] == kind]


def _text(case: dict) -> str:
    return (CORPUS / case["file"]).read_text(encoding="utf-8")


# ─── AC1: the normalizer ─────────────────────────────────────────────────────


def test_ac1_falsy_passthrough():
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    assert normalize_model_output("") == ""
    assert normalize_model_output(None) is None


def test_ac1_line_endings():
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    assert normalize_model_output("a\r\nb\rc") == "a\nb\nc"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("**VERDICT: FAIL**", "VERDICT: FAIL"),
        ("__VERDICT: FAIL__", "VERDICT: FAIL"),
        ("**VERDICT:** FAIL", "VERDICT: FAIL"),
        ("`VERDICT: FAIL`", "VERDICT: FAIL"),
        ("### Verdict", "Verdict"),
        ("## VERDICT: PASS", "VERDICT: PASS"),
        ("###### Verdict", "Verdict"),
        ("**`VERDICT: FAIL`**", "VERDICT: FAIL"),
        ("`## Verdict`", "Verdict"),
        ("***VERDICT: FAIL***", "*VERDICT: FAIL*"),
        ("call __init__ here", "call __init__ here"),
        ("## Verdict: PASS criteria", "## Verdict: PASS criteria"),
        ("## Status: Done so far", "## Status: Done so far"),
        ("    ## VERDICT: PASS", "    ## VERDICT: PASS"),
        ("`VERDICT: PASS` or `VERDICT: FAIL`", "`VERDICT: PASS` or `VERDICT: FAIL`"),
    ],
)
def test_ac1_strips_markup(raw, expected):
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    assert normalize_model_output(raw) == expected


def test_ac1_fenced_blocks_kept_verbatim():
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    raw = "audit\n```text\nVERDICT: PASS\n```\n~~~\nx\n~~~"
    out = normalize_model_output(raw)
    assert out == raw


def test_ac1_fence_content_is_not_unwrapped():
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    raw = "```python\n# VERDICT: PASS\nx = **y**\n```"
    out = normalize_model_output(raw)
    assert out == raw
    assert normalize_model_output(out) == out


@pytest.mark.parametrize(
    "raw",
    [
        "```\n# VERDICT: PASS\n",  # unclosed fence runs to the end
        "````\n```\n# VERDICT: PASS\n````",  # shorter inner fence does not close
        "```\n~~~\n# VERDICT: PASS\n```",  # other fence char does not close
        "  ```\n# VERDICT: PASS\n  ```",  # indented 1-3 spaces is still a fence
    ],
)
def test_ac1_fence_corner_cases(raw):
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    assert normalize_model_output(raw) == raw


def test_ac1_blockquote_preserved():
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    assert normalize_model_output("> VERDICT: FAIL").startswith(">")


def test_ac1_idempotent_on_corpus():
    from bytedigger_engine.lib.llm_output_normalize import normalize_model_output  # noqa: PLC0415

    for case in _CASES:
        once = normalize_model_output(_text(case))
        assert normalize_model_output(once) == once, case["file"]


# ─── AC2: real replies parse to the verdict they state ───────────────────────

_GATE_MARKERS = [("VERDICT: PASS", "PASS"), ("VERDICT: FAIL", "FAIL")]


@pytest.mark.parametrize("case", _cases("gate_verdict"), ids=lambda c: c["file"])
def test_ac2_corpus_marker_verdict(case):
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    got = last_line_anchored_marker(_text(case), _GATE_MARKERS, "UNKNOWN")
    assert got == case["stated_verdict"]


@pytest.mark.parametrize("case", _cases("gate_verdict"), ids=lambda c: c["file"])
def test_ac2_corpus_standalone_verdict(case):
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    got = last_standalone_line_verdict(_text(case), ("PASS", "FAIL"), fallback="UNKNOWN")
    assert got == case["stated_verdict"]


@pytest.mark.parametrize("case", _cases("spec_review"), ids=lambda c: c["file"])
def test_ac2_corpus_heading_verdict(case):
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    got = verdict_under_heading(
        _text(case),
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert got == case["stated_verdict"]


# ─── AC3: heading-verdict shapes; prose still rejected ───────────────────────

_SPEC_TOKENS = ("SHIP", "PASS", "APPROVED", "REVISE")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("### Verdict\nSHIP", "SHIP"),
        ("## Verdict\n\n**REVISE**\n\nbecause", "REVISE"),
        ("Verdict\nREVISE", "REVISE"),
        ("**Verdict:**\nSHIP", "SHIP"),
        ("## Verdict: REVISE", "REVISE"),
        ("Intro says REVISE.\n## Verdict\nSHIP", "SHIP"),
        ("The verdict is SHIP in my view.", "UNKNOWN"),
        # Echo of the prompt's schema literal is a choice list, not a verdict.
        ("## Verdict\nSHIP | REVISE", "UNKNOWN"),
        ("Verdict: SHIP | REVISE", "UNKNOWN"),
        ("    ## Verdict\n    SHIP | REVISE", "UNKNOWN"),
        ("## Verdict\nSHIP | REVISE\n\nreview...\n\n## Verdict\nREVISE", "REVISE"),
        # Conflicting Verdict sections fail closed.
        ("### Verdict\nSHIP\n\n## Verdict\nREVISE", "UNKNOWN"),
        ("## Verdict\nSHIP\n\n## Verdict\n**SHIP**", "SHIP"),
        ("## Verdict\nPASS\n\n## Verdict\nSHIP", "SHIP"),
        # A second token in prose is not a choice list.
        ("## Verdict\nREVISE — the rest can ship as-is", "REVISE"),
        ("## Verdict\nSHIP / REVISE", "UNKNOWN"),
        ("## Verdict\nSHIP or REVISE", "UNKNOWN"),
        # A long one-line heading is a section title (fail-closed, as before).
        ("## Verdict: SHIP — looks good", "UNKNOWN"),
    ],
)
def test_ac3_heading_shapes(raw, expected):
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    got = verdict_under_heading(raw, _SPEC_TOKENS, aliases={"PASS": "SHIP"}, fallback="UNKNOWN")
    assert got == expected


@pytest.mark.parametrize(
    "raw",
    [
        "The reviewer noted VERDICT: FAIL inline only.",
        "> VERDICT: FAIL",
        "Quoting the prompt: **VERDICT: FAIL** was an option.",
        # Choice lists (prompt echoes) are never a verdict.
        "`VERDICT: PASS` or `VERDICT: FAIL`",
        "**VERDICT: PASS** or **VERDICT: FAIL**",
        "VERDICT: PASS | VERDICT: FAIL",
        "VERDICT: PASS/FAIL",
        "VERDICT: PASS | FAIL",
        "VERDICT: PASS or FAIL",
        # Long headings are section titles, not verdicts.
        "## Verdict: PASS criteria",
    ],
)
def test_ac3_prose_and_quotes_still_rejected(raw):
    from bytedigger_engine.lib.verdict_parse import (  # noqa: PLC0415
        last_line_anchored_marker,
        last_standalone_line_verdict,
    )

    assert last_standalone_line_verdict(raw, ("PASS", "FAIL"), fallback="UNKNOWN") == "UNKNOWN"
    assert last_standalone_line_verdict(
        raw, ("PASS", "FAIL"), fallback="UNKNOWN", allow_trailing=True
    ) == "UNKNOWN"
    assert last_line_anchored_marker(raw, _GATE_MARKERS, "UNKNOWN") == "UNKNOWN"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("VERDICT: FAIL — tests pass but AC3 uncovered", "FAIL"),
        ("**VERDICT: FAIL** — AC4 is not covered, so PASS is impossible", "FAIL"),
        ("VERDICT: FAIL — the earlier VERDICT: PASS was wrong", "FAIL"),
    ],
)
def test_ac3_glossed_verdict_is_not_a_choice_list(raw, expected):
    from bytedigger_engine.lib.verdict_parse import (  # noqa: PLC0415
        last_line_anchored_marker,
        last_standalone_line_verdict,
    )

    assert last_line_anchored_marker(raw, _GATE_MARKERS, "UNKNOWN") == expected
    assert last_standalone_line_verdict(raw, ("PASS", "FAIL"), fallback="UNKNOWN", allow_trailing=True) == expected


def test_ac3_glossed_integrity_verdict_is_not_a_choice_list():
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    tokens = ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES")
    raw = "VERDICT: SPEC_CHANGE — no ASSERTION_GAMING found"
    assert last_standalone_line_verdict(raw, tokens, fallback="UNKNOWN", allow_trailing=True) == "SPEC_CHANGE"


def test_ac3_fenced_comment_does_not_override_real_verdict():
    from bytedigger_engine.lib.verdict_parse import (  # noqa: PLC0415
        last_line_anchored_marker,
        last_standalone_line_verdict,
    )

    raw = "Audit.\n\nVERDICT: FAIL\n\nSuggested fix:\n```python\n# VERDICT: PASS\nassert clamp(1, 0, 2) == 1\n```\n"
    assert last_line_anchored_marker(raw, _GATE_MARKERS, "UNKNOWN") == "FAIL"
    assert last_standalone_line_verdict(raw, ("PASS", "FAIL"), fallback="UNKNOWN") == "FAIL"


def test_ac3_status_choice_list_ignored_superset_marker_kept():
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    markers = [("STATUS: DONE_WITH_CONCERNS", "DWC"), ("STATUS: DONE", "DONE"), ("STATUS: BLOCKED", "BLOCKED")]
    assert last_line_anchored_marker("**STATUS: DONE** | **STATUS: BLOCKED**", markers, None) is None
    assert last_line_anchored_marker("## Status: Done so far", markers, None) is None
    assert last_line_anchored_marker("work\n**STATUS: DONE_WITH_CONCERNS**", markers, None) == "DWC"
    assert last_line_anchored_marker("work\n## STATUS: DONE", markers, None) == "DONE"


# ─── AC4: severity-header canonicalization ───────────────────────────────────


@pytest.mark.parametrize(
    "line, expected",
    [
        ("### HIGH — Off-by-one in last_n", "### SEVERITY: HIGH — Off-by-one in last_n"),
        ("## **MEDIUM** - Bare except", "### SEVERITY: MEDIUM — Bare except"),
        ("#### Severity: critical: data loss", "### SEVERITY: CRITICAL — data loss"),
        ("### SEVERITY: HIGH — already canonical", "### SEVERITY: HIGH — already canonical"),
    ],
)
def test_ac4_canonicalizes_finding_headers(line, expected):
    from bytedigger_engine.lib.llm_output_normalize import canonicalize_severity_headers  # noqa: PLC0415

    assert canonicalize_severity_headers(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "HIGH - this is prose, not a heading",
        "> util.py:15: return items[len(items) - n - 1:]",
        "### Summary",
        "Confidence: HIGH",
        "### High-level summary",
        "## Low-hanging fruit",
        "### Critical: none",
        "Severity: HIGH — body field under a real header",
        "# SEVERITY: HIGH — single hash stays invisible (GH970)",
        "##### SEVERITY: HIGH — five hashes stay invisible (GH970)",
        "### HIGH —",
        "### Critical — No critical issues found",
        "### HIGH — 0 findings",
        "### High — Summary of review",
        "## Low - priority items",
        "### Critical: SQL injection in login",
        "### Critical — none found",
        "### HIGH — n/a",
        "### MEDIUM — None.",
        "### LOW — none identified",
        "## SEVERITY: HIGH - already parses (GH970), kept byte-identical",
    ],
)
def test_ac4_leaves_other_lines_untouched(line):
    from bytedigger_engine.lib.llm_output_normalize import canonicalize_severity_headers  # noqa: PLC0415

    assert canonicalize_severity_headers(line) == line


def test_ac4_fenced_lines_untouched_rest_rewritten():
    from bytedigger_engine.lib.llm_output_normalize import canonicalize_severity_headers  # noqa: PLC0415

    text = "```md\n### HIGH — inside a fence\n```\n### LOW — outside\n> f.py:1: x"
    assert canonicalize_severity_headers(text) == (
        "```md\n### HIGH — inside a fence\n```\n### SEVERITY: LOW — outside\n> f.py:1: x"
    )


# ─── AC5 / AC6: aggregate review findings ────────────────────────────────────


def _aggregate(tmp_path: Path, roles: dict[str, str], monkeypatch, events: list | None = None) -> StepResult:
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    sink = events if events is not None else []
    monkeypatch.setattr(p6, "_emit_safe", lambda name, payload=None, *a, **k: sink.append((name, payload)))
    reviews = tmp_path / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    for slug, body in roles.items():
        (reviews / f"role-{slug}.md").write_text(body, encoding="utf-8")
    (tmp_path / "util.py").write_text((CORPUS / "util.py.txt").read_text(encoding="utf-8"), encoding="utf-8")
    ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path)}, question="q")
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")
    return p6._aggregate_review_findings(ctx, prev)


@pytest.mark.parametrize("case", _cases("review_findings"), ids=lambda c: c["file"])
def test_ac5_corpus_findings_are_counted(case, tmp_path, monkeypatch):
    res = _aggregate(tmp_path, {"code-reviewer": _text(case)}, monkeypatch)
    data = res.data or {}
    parsed = data["findings_count"] + data["filtered_count"]
    assert parsed == case["finding_headers"], data.get("findings_audit")
    assert data["findings_audit"]["parsed_blocks"] == case["finding_headers"]
    assert data["verdict"] != "PASS"


def test_ac5_hosted_b_findings_verify_and_fail(tmp_path, monkeypatch):
    """The replies that dropped the `SEVERITY:` word carry correct quotes; once
    canonicalized they verify against util.py and the HIGH finding fails the review."""
    case = next(c for c in _cases("review_findings") if c["model"] == "hosted_b")
    res = _aggregate(tmp_path, {"code-reviewer": _text(case)}, monkeypatch)
    assert res.data["verdict"] == "FAIL"
    assert res.data["severity_counts"]["HIGH"] >= 1


def test_ac5_clean_role_with_high_level_heading_stays_pass(tmp_path, monkeypatch):
    body = "# code-reviewer Review\n\n### High-level summary\nNothing to report.\n\nVERDICT: PASS\n"
    res = _aggregate(tmp_path, {"code-reviewer": body}, monkeypatch)
    assert res.data["verdict"] == "PASS"
    assert res.data["findings_audit"]["parsed_blocks"] == 0


_PROSE_FAIL = (
    "# code-reviewer Review\n\n"
    "I found an off-by-one in last_n (line 15) and mean() divides by zero on\n"
    "an empty list. Both are real bugs.\n\n"
    "VERDICT: FAIL\n"
)


def test_ac6_prose_findings_with_fail_verdict_is_suspect(tmp_path, monkeypatch):
    events: list = []
    res = _aggregate(tmp_path, {"code-reviewer": _PROSE_FAIL}, monkeypatch, events)
    assert res.data["verdict"] == "SUSPECT"
    assert res.data["suspect_findings"] == []
    assert "## Suspect Findings" not in res.data["aggregated_content"]
    assert any(name == "review_zero_findings_suspect" for name, _ in events)


def test_ac6_partial_without_count_is_suspect(tmp_path, monkeypatch):
    body = "# code-reviewer Review\n\nMinor issues noted in prose.\n\nVERDICT: PARTIAL\n"
    res = _aggregate(tmp_path, {"code-reviewer": body}, monkeypatch)
    assert res.data["verdict"] == "SUSPECT"


def test_ac6_nonzero_selfcount_with_zero_parsed_is_suspect(tmp_path, monkeypatch):
    body = _PROSE_FAIL + "<!-- role-findings-count: 3 -->\n"
    res = _aggregate(tmp_path, {"code-reviewer": body}, monkeypatch)
    assert res.data["verdict"] == "SUSPECT"


def test_ac6_backstop_never_downgrades_a_fail(tmp_path, monkeypatch):
    corpus_case = next(c for c in _cases("review_findings") if c["model"] == "hosted_a")
    roles = {"code-reviewer": _text(corpus_case), "silent-failure-hunter": _PROSE_FAIL}
    res = _aggregate(tmp_path, roles, monkeypatch)
    assert res.data["verdict"] == "FAIL"


def test_ac6_bold_fail_verdict_is_suspect(tmp_path, monkeypatch):
    body = "# code-reviewer Review\n\nSeveral issues, described above in prose.\n\n**VERDICT: FAIL**\n"
    res = _aggregate(tmp_path, {"code-reviewer": body}, monkeypatch)
    assert res.data["verdict"] == "SUSPECT"


def test_ac6_clean_review_stays_pass(tmp_path, monkeypatch):
    body = "# code-reviewer Review\n\nNo issues found.\n\nVERDICT: PASS\n"
    res = _aggregate(tmp_path, {"code-reviewer": body}, monkeypatch)
    assert res.data["verdict"] == "PASS"


def test_ac6_explicit_zero_selfcount_stays_pass(tmp_path, monkeypatch):
    body = "# code-reviewer Review\n\nVERDICT: PASS\n<!-- role-findings-count: 0 -->\n"
    res = _aggregate(tmp_path, {"code-reviewer": body}, monkeypatch)
    assert res.data["verdict"] == "PASS"


def test_ac6_one_suspicious_role_among_clean_ones(tmp_path, monkeypatch):
    roles = {
        "code-reviewer": "# code-reviewer Review\n\nNo issues.\n\nVERDICT: PASS\n",
        "silent-failure-hunter": "# silent-failure-hunter Review\n\nThe bare except hides errors.\n\nVERDICT: FAIL\n",
    }
    res = _aggregate(tmp_path, roles, monkeypatch)
    assert res.data["verdict"] == "SUSPECT"


# ─── AC7: fix-integrity missing marker → same-prompt re-roll ─────────────────


def _fix_ctx(tmp_path: Path, extra: dict | None = None):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    cfg = {"scratchpad_dir": str(tmp_path)}
    cfg.update(extra or {})
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=cfg, question="q",
        session_id="bd84", persona="p", framework=None, domain=None,
    )


def _fix_prev(tmp_path: Path) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "CLASSIFY THIS FIX DIFF.",
            "doc_path": str(tmp_path / "reviews" / "fix-integrity-review.md"),
            "diff_path": str(tmp_path / "integrity" / "fix-diff.patch"),
        },
        duration_ms=0,
        step_name="build_fix_integrity_prompt",
    )


def _fake_invoke(
    monkeypatch, responses: list[str] | None, *, error: bool = False, error_on_call: int | None = None
) -> list[dict]:
    from bytedigger_engine.workflows import phase_6_fix_integrity as fi  # noqa: PLC0415

    monkeypatch.delenv("HAL_INTEGRITY_VERDICT_RETRY_MAX", raising=False)
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if error or error_on_call == len(calls):
            return StepResult(status="error", data=None, duration_ms=0,
                              step_name="invoke_fix_integrity_llm", error="timeout", error_code="E_TIMEOUT")
        raw = responses[min(len(calls), len(responses)) - 1]
        extra = kwargs.get("extra_data") or {}
        return StepResult(status="ok", data={"raw_response": raw, **extra}, duration_ms=0,
                          step_name="invoke_fix_integrity_llm")

    monkeypatch.setattr(fi, "invoke_llm_subprocess", _fake)
    return calls


def test_ac7_missing_marker_rerolls_same_prompt(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    calls = _fake_invoke(monkeypatch, ["Looks like a legitimate spec change.", "VERDICT: SPEC_CHANGE"])
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert res.status == "ok"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert res.data["raw_response"] == "VERDICT: SPEC_CHANGE"
    assert res.data["verdict_completeness_retries"] == 1


@pytest.mark.parametrize("decoy", ["The VERDICT: is unclear here.", "> VERDICT: SPEC_CHANGE"])
def test_ac7_decoy_first_reply_is_rerolled(decoy, tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    calls = _fake_invoke(monkeypatch, [decoy, "VERDICT: LEGITIMATE_REFACTOR"])
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert len(calls) == 2
    assert res.data["raw_response"] == "VERDICT: LEGITIMATE_REFACTOR"


def test_ac7_error_on_retry_passes_through(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    calls = _fake_invoke(monkeypatch, ["prose only"], error_on_call=2)
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert len(calls) == 2
    assert res.status == "error" and res.error_code == "E_TIMEOUT"


def test_ac7_marked_reply_is_not_rerolled(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    calls = _fake_invoke(monkeypatch, ["**VERDICT: LEGITIMATE_REFACTOR**"])
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert len(calls) == 1
    assert res.data["verdict_completeness_retries"] == 0


def test_ac7_error_reply_passes_through(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    calls = _fake_invoke(monkeypatch, None, error=True)
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert res.status == "error" and res.error_code == "E_TIMEOUT"
    assert len(calls) == 1


def test_ac7_knob_zero_disables_reroll(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    calls = _fake_invoke(monkeypatch, ["prose only", "VERDICT: SPEC_CHANGE"])
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path, {"integrity_verdict_retry_max": 0}), _fix_prev(tmp_path))
    assert len(calls) == 1
    assert res.data["verdict_completeness_retries"] == 0


def test_ac7_exhausted_reroll_still_no_marker(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import (  # noqa: PLC0415
        _classify_fix_diff_verdict,
        _invoke_fix_integrity_llm,
    )

    calls = _fake_invoke(monkeypatch, ["prose only"])
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert len(calls) == 2
    out = _classify_fix_diff_verdict(None, res)
    assert out.status == "error"
    assert out.error_code == "E_FIX_INTEGRITY_NO_MARKER"


# ─── Review round: silent-failure findings ───────────────────────────────────

_INTEGRITY_TOKENS = ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES")


def test_rv_gloss_after_slash_is_not_a_choice_list():
    """A separator counts as a choice only when another token follows it."""
    from bytedigger_engine.lib.verdict_parse import (  # noqa: PLC0415
        last_line_anchored_marker,
        last_standalone_line_verdict,
    )

    raw = "a.py:\nVERDICT: LEGITIMATE_REFACTOR\nb.py:\nVERDICT: ASSERTION_GAMING / assertEqual removed"
    assert last_standalone_line_verdict(raw, _INTEGRITY_TOKENS, fallback="UNKNOWN", allow_trailing=True) == "ASSERTION_GAMING"
    status = [("STATUS: DONE_WITH_CONCERNS", "DWC"), ("STATUS: DONE", "DONE")]
    assert last_line_anchored_marker("STATUS: DONE_WITH_CONCERNS / flaky test in foo", status, None) == "DWC"
    raw = "VERDICT: PASS for module a\n...\nVERDICT: FAIL / 2 high findings"
    assert last_line_anchored_marker(raw, _GATE_MARKERS, "UNKNOWN") == "FAIL"
    raw = "VERDICT: PASS\n\nWait, re-running.\nVERDICT: FAIL or flaky"
    assert last_line_anchored_marker(raw, _GATE_MARKERS, "UNKNOWN") == "FAIL"


@pytest.mark.parametrize(
    "raw",
    [
        "Verdict: pass rate is 60%, too low.",
        "**Verdict:** Pass for sections 1-3; section 4 must be revised.",
        "Verdict: Approved with changes required below.",
        "### Verdict\nApproved-by-author assumptions are unverified; REVISE",
        "Verdict: pass on structure; see gaps.\n\n```python\ndef f():\n    pass\n\n## Verdict\n\nREVISE",
    ],
)
def test_rv_p3_prose_after_token_is_not_a_verdict(raw):
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    got = verdict_under_heading(raw, _SPEC_TOKENS, aliases={"PASS": "SHIP", "APPROVED": "SHIP"}, fallback="UNKNOWN")
    assert got != "SHIP"


def test_rv_quoted_code_span_does_not_override_plain_verdict():
    from bytedigger_engine.lib.verdict_parse import (  # noqa: PLC0415
        last_line_anchored_marker,
        last_standalone_line_verdict,
    )

    raw = "VERDICT: FAIL\n\nOnce fixed, the expected output is:\n`VERDICT: PASS`"
    assert last_line_anchored_marker(raw, _GATE_MARKERS, "UNKNOWN") == "FAIL"
    raw = "VERDICT: ASSERTION_GAMING\n\nIf the hunk in b.py is reverted this would be:\n`VERDICT: SPEC_CHANGE`"
    assert last_standalone_line_verdict(raw, _INTEGRITY_TOKENS, fallback="UNKNOWN", allow_trailing=True) == "ASSERTION_GAMING"


@pytest.mark.parametrize(
    "body",
    [
        "# r Review\n\n## VERDICT: FAIL — 2 high findings\n",
        "```markdown\n# r Review\n### HIGH — bug\n**VERDICT: FAIL**\n```\n",
        "# r Review\n\nVERDICT: FAIL\n<!-- role-findings-count: 0 -->\n",
        "# r Review\n\nVERDICT: PARTIAL\n<!-- role-findings-count: 0 -->\n",
        "# r Review\n\nThe first issue is in last_n where",
    ],
)
def test_rv_zero_findings_without_a_pass_is_suspect(body, tmp_path, monkeypatch):
    res = _aggregate(tmp_path, {"code-reviewer": body}, monkeypatch)
    assert res.data["verdict"] == "SUSPECT"


def test_rv_unreadable_role_file_is_suspect(tmp_path, monkeypatch):
    events: list = []
    (tmp_path / "reviews" / "role-broken.md").mkdir(parents=True)
    res = _aggregate(tmp_path, {"code-reviewer": "# ok\n\nVERDICT: PASS\n"}, monkeypatch, events)
    assert res.data["verdict"] == "SUSPECT"
    assert any(name == "review_zero_findings_suspect" for name, _ in events)


def test_rv_zero_findings_role_blocks_the_all_suspect_override(tmp_path, monkeypatch):
    """Role A declares FAIL in prose; role B has only an uncitable finding.
    The review must not look 'all findings suspect' to the satisfaction override."""
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    events: list = []
    roles = {
        "code-reviewer": _PROSE_FAIL,
        "silent-failure-hunter": (
            "# silent-failure-hunter Review\n\n### SEVERITY: HIGH — t\n"
            "> /nonexistent/bd84.py:1: x = 1\n\nVERDICT: PARTIAL\n"
        ),
    }
    res = _aggregate(tmp_path, roles, monkeypatch, events)
    assert res.data["verdict"] == "SUSPECT"
    assert any(name == "review_zero_findings_suspect" for name, _ in events)
    assert p6._review_all_findings_suspect("SUSPECT", res.data["aggregated_content"]) is False


def test_rv_reroll_keeps_discarded_replies(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    _fake_invoke(monkeypatch, ["ASSERTION_GAMING analysis without a marker", "VERDICT: SPEC_CHANGE"])
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert res.data["discarded_raw_responses"] == ["ASSERTION_GAMING analysis without a marker"]


def test_rv_error_on_retry_keeps_retry_count(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_6_fix_integrity import _invoke_fix_integrity_llm  # noqa: PLC0415

    _fake_invoke(monkeypatch, ["prose only"], error_on_call=2)
    res = _invoke_fix_integrity_llm(_fix_ctx(tmp_path), _fix_prev(tmp_path))
    assert res.status == "error"
    assert res.data["verdict_completeness_retries"] == 1
    assert res.data["discarded_raw_responses"] == ["prose only"]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("### HIGH — None of the error paths are tested", "### SEVERITY: HIGH — None of the error paths are tested"),
        ("### HIGH — N/A handling drops rows", "### SEVERITY: HIGH — N/A handling drops rows"),
        ("### CRITICAL: SQL injection in login", "### SEVERITY: CRITICAL — SQL injection in login"),
        ("**SEVERITY: LOW** — bold line", "### SEVERITY: LOW — bold line"),
        ("**HIGH — SQL injection**", "### SEVERITY: HIGH — SQL injection"),
    ],
)
def test_rv_real_findings_are_not_placeholders(line, expected):
    from bytedigger_engine.lib.llm_output_normalize import canonicalize_severity_headers  # noqa: PLC0415

    assert canonicalize_severity_headers(line) == expected
