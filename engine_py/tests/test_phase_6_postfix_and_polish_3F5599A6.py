"""RED tests for 3F5599A6 (GH279 slice 3) — A1 residue.

Two independent fixes in phase_6_review.py:

D1 (55802041) — post-fix-pytest report injection: _build_review_prompt must
load `<scratchpad>/reviews/post-fix-pytest.md` (via a new named helper
`_load_postfix_pytest_report`) and inject it into the prompt immediately after
the GREEN WORKER REPORT line, emitting `composite_postfix_pytest_consumed`.

D2-D5 (50C9E8EC) — _verify_finding_quote polish:
  D2: drive-letter-safe `_QUOTE_LINE_RE` (optional `[A-Za-z]:` prefix in group 1).
  D3: `_QUOTE_FILE_MAX_BYTES` size cap -> ("suspect-file-not-found", "FILE-TOO-LARGE").
  D4: relative-path containment check -> ("suspect-file-not-found", "PATH-ESCAPES-ROOT").
  D5: honest `file_cache: dict[str, list[str] | None]` annotation (drop stale
      `# type: ignore[assignment]`).

Spec: SHARED/memory/Decisions/2026-07-06_3F5599A6_gh279_slice3_spec.md

Expected pre-GREEN outcome: 10 FAIL / 2 PASS. AC2 and AC5 are regression locks
on EXISTING behavior (plain-path exact match; nonexistent-relative stays
OK-UNVERIFIABLE-RELATIVE) and MUST pass today. Every other AC targets behavior
or a named symbol that does not exist yet in current production code.

Not-yet-existing symbols (`_load_postfix_pytest_report`,
`_QUOTE_FILE_MAX_BYTES`, `_POSTFIX_PYTEST_REPORT_RELPATH`,
`_POSTFIX_REPORT_MAX_BYTES`) are NEVER imported at module top level (D1CF5FDF)
— each is looked up via `getattr(module, name, None)` inside the relevant test
body with an assert-presence-first line, so the file always COLLECTS cleanly
and every test FAILS at ASSERT time, never at collection time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest  # noqa: F401

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    _build_review_prompt,
    _verify_finding_quote,
)
from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402


# ─── shared fixtures ──────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="add feature X",
        session_id="test-3F5599A6",
        persona="hal",
        framework=None,
        domain=None,
    )


def _capture_emit(monkeypatch) -> list[tuple]:
    """Monkeypatch phase_6_review._emit_safe (infra, not a UUT) to capture calls."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        _p6,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p, kw)),
    )
    return captured


# ─── AC1-AC6: _verify_finding_quote direct-call tests (§1y: direct UUT call) ──


def test_ac1_drive_letter_path_verified_exact(tmp_path):
    """AC1: a file literally named 'C:\\repo\\mod.py' under base_dir, cited with
    that drive-letter path, must verify as ('verified-exact', 'OK') — proves
    group(1) of _QUOTE_LINE_RE spans the drive-letter prefix.

    Pre-GREEN: `[^:]+` stops at the first colon (right after 'C'); the digit
    group then fails to match '\\repo\\mod.py:1: ...' -> no regex match at all
    -> ('suspect-no-quote', 'MISSING-QUOTE'). FAILS.
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    fixture = base_dir / "C:\\repo\\mod.py"
    fixture.write_text("line one\nline two\n", encoding="utf-8")

    block = (
        "### SEVERITY: HIGH — DriveLetterFinding\n"
        "> C:\\repo\\mod.py:1: line one\n"
        "Confidence: HIGH\n"
        "Description: drive-letter cite test\n"
    )
    status, reason = _verify_finding_quote(block, {}, base_dir=base_dir)
    assert (status, reason) == ("verified-exact", "OK"), (
        f"AC1: expected ('verified-exact', 'OK') for a drive-letter-prefixed cite; "
        f"got ({status!r}, {reason!r})"
    )


def test_ac2_plain_path_verified_exact_regression_lock(tmp_path):
    """AC2 (regression lock): plain '> mod.py:2: <exact line 2>' under base_dir
    must verify as ('verified-exact', 'OK'). This is EXISTING behavior and MUST
    pass today — pins that D2's regex change does not break plain-path cites.
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    fixture = base_dir / "mod.py"
    fixture.write_text("line one\nline two\n", encoding="utf-8")

    block = (
        "### SEVERITY: MEDIUM — PlainPathFinding\n"
        "> mod.py:2: line two\n"
        "Confidence: HIGH\n"
        "Description: plain-path regression lock\n"
    )
    status, reason = _verify_finding_quote(block, {}, base_dir=base_dir)
    assert (status, reason) == ("verified-exact", "OK"), (
        f"AC2: plain-path cite regression lock broken; got ({status!r}, {reason!r})"
    )


def test_ac3_oversized_file_rejected_file_too_large(tmp_path):
    """AC3: a cited file of _QUOTE_FILE_MAX_BYTES + 1 bytes must be rejected as
    ('suspect-file-not-found', 'FILE-TOO-LARGE') AND its cache key must be
    absent from file_cache (no read occurs — stat-only rejection).

    Pre-GREEN: _QUOTE_FILE_MAX_BYTES does not exist yet -> presence-check fails.
    """
    max_bytes = getattr(_p6, "_QUOTE_FILE_MAX_BYTES", None)
    assert max_bytes is not None, (
        "AC3: _QUOTE_FILE_MAX_BYTES constant does not exist yet in phase_6_review"
    )

    fixture = tmp_path / "huge.py"
    fixture.write_bytes(b"x" * (max_bytes + 1))

    block = (
        "### SEVERITY: HIGH — HugeFileFinding\n"
        f"> {fixture}:1: x\n"
        "Confidence: HIGH\n"
        "Description: oversized-file cap test\n"
    )
    file_cache: dict = {}
    status, reason = _verify_finding_quote(block, file_cache, base_dir=tmp_path)
    assert (status, reason) == ("suspect-file-not-found", "FILE-TOO-LARGE"), (
        f"AC3: file exceeding _QUOTE_FILE_MAX_BYTES must be FILE-TOO-LARGE; "
        f"got ({status!r}, {reason!r})"
    )
    assert str(fixture) not in file_cache, (
        f"AC3: cache key must be absent for an oversized file (no read); "
        f"file_cache keys={list(file_cache.keys())}"
    )


def test_ac4_relative_escape_to_existing_file_rejected(tmp_path):
    """AC4: '> ../outside.txt:1: x' where the file EXISTS at base_dir.parent must
    be rejected as ('suspect-file-not-found', 'PATH-ESCAPES-ROOT') AND its cache
    key must be absent.

    Pre-GREEN: no containment check -> resolves, exists, content matches ->
    ('verified-exact', 'OK'). FAILS.
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret line\n", encoding="utf-8")

    block = (
        "### SEVERITY: HIGH — EscapeFinding\n"
        "> ../outside.txt:1: secret line\n"
        "Confidence: HIGH\n"
        "Description: containment-escape test\n"
    )
    file_cache: dict = {}
    status, reason = _verify_finding_quote(block, file_cache, base_dir=base_dir)
    assert (status, reason) == ("suspect-file-not-found", "PATH-ESCAPES-ROOT"), (
        f"AC4: relative cite resolving outside base_dir must be PATH-ESCAPES-ROOT; "
        f"got ({status!r}, {reason!r})"
    )
    assert not file_cache, (
        f"AC4: cache key must be absent when path escapes root; "
        f"file_cache keys={list(file_cache.keys())}"
    )


def test_ac5_relative_nonexistent_unverifiable_regression_lock(tmp_path):
    """AC5 (regression lock): '> ../nope.txt:1: x' nonexistent must stay
    ('suspect-file-not-found', 'OK-UNVERIFIABLE-RELATIVE') — the exists-check-
    first order (D4 runs AFTER the exists() check) must not disturb this. This
    is EXISTING behavior and MUST pass today.
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()

    block = (
        "### SEVERITY: LOW — NopeFinding\n"
        "> ../nope.txt:1: x\n"
        "Confidence: LOW\n"
        "Description: nonexistent relative cite regression lock\n"
    )
    status, reason = _verify_finding_quote(block, {}, base_dir=base_dir)
    assert (status, reason) == ("suspect-file-not-found", "OK-UNVERIFIABLE-RELATIVE"), (
        f"AC5: nonexistent relative cite regression lock broken; "
        f"got ({status!r}, {reason!r})"
    )


def test_ac6_inroot_symlink_escaping_root_rejected(tmp_path):
    """AC6: an in-root symlink base_dir/link.txt pointing OUTSIDE root, cited
    as '> link.txt:1: x', must be rejected as
    ('suspect-file-not-found', 'PATH-ESCAPES-ROOT') — resolve() follows the
    symlink and the real target is outside root.

    Pre-GREEN: no containment check -> symlink resolves fine, content matches
    -> ('verified-exact', 'OK'). FAILS.
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside_target = tmp_path / "outside_target.txt"
    outside_target.write_text("linked line\n", encoding="utf-8")
    link = base_dir / "link.txt"
    link.symlink_to(outside_target)

    block = (
        "### SEVERITY: HIGH — SymlinkEscapeFinding\n"
        "> link.txt:1: linked line\n"
        "Confidence: HIGH\n"
        "Description: symlink-escape test\n"
    )
    file_cache: dict = {}
    status, reason = _verify_finding_quote(block, file_cache, base_dir=base_dir)
    assert (status, reason) == ("suspect-file-not-found", "PATH-ESCAPES-ROOT"), (
        f"AC6: in-root symlink resolving outside root must be PATH-ESCAPES-ROOT; "
        f"got ({status!r}, {reason!r})"
    )


# ─── AC7: source-text assert on cache annotation honesty ──────────────────────


def test_ac7_file_cache_annotation_honest_no_stale_type_ignore():
    """AC7: source must declare 'file_cache: dict[str, list[str] | None]' at BOTH
    the _verify_finding_quote signature and the aggregator init (>= 2x), AND the
    OSError cache-fill line must carry NO 'type: ignore[assignment]' comment.

    Pre-GREEN: annotation is 'dict[str, list[str]]' (no '| None') at both sites,
    and the stale type:ignore comment is still present. FAILS.
    """
    source = Path(_p6.__file__).read_text(encoding="utf-8")
    honest_count = source.count("file_cache: dict[str, list[str] | None]")
    assert honest_count >= 2, (
        f"AC7: 'file_cache: dict[str, list[str] | None]' must appear >= 2x "
        f"(signature + aggregator init); found {honest_count}x"
    )
    assert "type: ignore[assignment]" not in source, (
        "AC7: stale '# type: ignore[assignment]' comment must be removed now "
        "that the annotation is honest"
    )


# ─── AC8-AC10: _build_review_prompt + scratchpad fixture ──────────────────────


def test_ac8_build_review_prompt_injects_postfix_report(tmp_path, monkeypatch):
    """AC8: with a small `reviews/post-fix-pytest.md` present, the prompt must
    contain '## POST-FIX PYTEST REPORT' + the report body, and exactly one
    captured `composite_postfix_pytest_consumed` event with report_bytes > 0
    and truncated is False.

    Pre-GREEN: _load_postfix_pytest_report does not exist -> presence-check fails.
    """
    loader = getattr(_p6, "_load_postfix_pytest_report", None)
    assert loader is not None, (
        "AC8: _load_postfix_pytest_report helper does not exist yet in phase_6_review"
    )
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    reviews_dir = (tmp_path / "scratch") / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    report_body = "REPORT-BODY-MARKER-8F3A\nFAILED tests/foo.py::test_bar\n"
    (reviews_dir / "post-fix-pytest.md").write_text(report_body, encoding="utf-8")

    result = _build_review_prompt(ctx, None)
    assert result.status == "ok", (
        f"AC8: unexpected error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert "## POST-FIX PYTEST REPORT" in prompt, (
        "AC8: prompt must contain the '## POST-FIX PYTEST REPORT' header"
    )
    assert "REPORT-BODY-MARKER-8F3A" in prompt, (
        "AC8: prompt must contain the post-fix report body"
    )

    postfix_events = [e for e in captured if e[0] == "composite_postfix_pytest_consumed"]
    assert len(postfix_events) == 1, (
        f"AC8: expected exactly 1 'composite_postfix_pytest_consumed' event; "
        f"got {len(postfix_events)}: {captured}"
    )
    payload = postfix_events[0][1]
    assert payload.get("report_bytes", 0) > 0, (
        f"AC8: report_bytes must be > 0; got payload={payload}"
    )
    assert payload.get("truncated") is False, (
        f"AC8: truncated must be False for a small report; got payload={payload}"
    )


def test_ac9_build_review_prompt_no_injection_when_report_absent(tmp_path, monkeypatch):
    """AC9: with NO `reviews/post-fix-pytest.md`, the prompt must lack the header
    AND no `composite_postfix_pytest_consumed` event must be captured.

    Pre-GREEN: _load_postfix_pytest_report does not exist -> presence-check
    fails (forces RED even though the absence-behavior already coincidentally
    matches today, per D1CF5FDF assert-presence-first).
    """
    loader = getattr(_p6, "_load_postfix_pytest_report", None)
    assert loader is not None, (
        "AC9: _load_postfix_pytest_report helper does not exist yet in phase_6_review"
    )
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    # No reviews/post-fix-pytest.md written.

    result = _build_review_prompt(ctx, None)
    assert result.status == "ok", (
        f"AC9: unexpected error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert "## POST-FIX PYTEST REPORT" not in prompt, (
        "AC9: prompt must NOT contain the post-fix report header when absent"
    )
    assert not any(e[0] == "composite_postfix_pytest_consumed" for e in captured), (
        "AC9: no 'composite_postfix_pytest_consumed' event when report file is absent"
    )


def test_ac10_build_review_prompt_truncates_oversized_report(tmp_path, monkeypatch):
    """AC10: a report of _POSTFIX_REPORT_MAX_BYTES + 2048 bytes with a unique
    tail marker must appear (tail-truncated) in the prompt, the head marker
    must NOT appear, and the event must carry truncated is True.

    Pre-GREEN: neither helper nor constant exist -> presence-check fails.
    """
    loader = getattr(_p6, "_load_postfix_pytest_report", None)
    assert loader is not None, (
        "AC10: _load_postfix_pytest_report helper does not exist yet in phase_6_review"
    )
    max_bytes = getattr(_p6, "_POSTFIX_REPORT_MAX_BYTES", None)
    assert max_bytes is not None, (
        "AC10: _POSTFIX_REPORT_MAX_BYTES constant does not exist yet in phase_6_review"
    )
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    reviews_dir = (tmp_path / "scratch") / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    head_marker = "HEAD-MARKER-DO-NOT-SHOW-9Q2Z"
    tail_marker = "TAIL-MARKER-MUST-SURVIVE-4X7K"
    filler_len = (max_bytes + 2048) - len(head_marker) - len(tail_marker)
    report_body = head_marker + ("y" * filler_len) + tail_marker
    (reviews_dir / "post-fix-pytest.md").write_text(report_body, encoding="utf-8")

    result = _build_review_prompt(ctx, None)
    assert result.status == "ok", (
        f"AC10: unexpected error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert tail_marker in prompt, (
        "AC10: prompt must contain the TAIL marker of an oversized report"
    )
    assert head_marker not in prompt, (
        "AC10: prompt must NOT contain the HEAD marker (truncated to tail)"
    )

    postfix_events = [e for e in captured if e[0] == "composite_postfix_pytest_consumed"]
    assert len(postfix_events) == 1, (
        f"AC10: expected exactly 1 'composite_postfix_pytest_consumed' event; "
        f"got {len(postfix_events)}"
    )
    assert postfix_events[0][1].get("truncated") is True, (
        f"AC10: truncated must be True for an oversized report; "
        f"payload={postfix_events[0][1]}"
    )


# ─── AC11: source-text single-source-of-truth assert ──────────────────────────


def test_ac11_postfix_relpath_single_source_of_truth():
    """AC11: literal 'post-fix-pytest.md' must appear EXACTLY once in source (in
    the `_POSTFIX_PYTEST_REPORT_RELPATH` definition); both
    `_load_postfix_pytest_report` and `_run_pytest_post_fix` must reference the
    constant (§1g single source of truth).

    Pre-GREEN: _POSTFIX_PYTEST_REPORT_RELPATH does not exist -> presence-check
    fails.
    """
    relpath_const = getattr(_p6, "_POSTFIX_PYTEST_REPORT_RELPATH", None)
    assert relpath_const is not None, (
        "AC11: _POSTFIX_PYTEST_REPORT_RELPATH constant does not exist yet in phase_6_review"
    )
    source = Path(_p6.__file__).read_text(encoding="utf-8")
    literal_count = source.count("post-fix-pytest.md")
    assert literal_count == 1, (
        f"AC11: literal 'post-fix-pytest.md' must appear EXACTLY once (in the "
        f"constant definition); found {literal_count}x"
    )
    ref_count = source.count("_POSTFIX_PYTEST_REPORT_RELPATH")
    assert ref_count >= 3, (
        f"AC11: both _load_postfix_pytest_report and _run_pytest_post_fix must "
        f"reference the constant (definition + >=2 usages); found {ref_count}x"
    )


# ─── AC12: _load_postfix_pytest_report direct-call test ───────────────────────


def test_ac12_load_postfix_pytest_report_direct_call(tmp_path):
    """AC12 (§1aa named-helper forcing function): direct call to
    _load_postfix_pytest_report(scratchpad) must return None when the file is
    missing, and a (str, bool) tuple when present.

    Pre-GREEN: helper does not exist -> presence-check fails.
    """
    loader = getattr(_p6, "_load_postfix_pytest_report", None)
    assert loader is not None, (
        "AC12: _load_postfix_pytest_report helper does not exist yet in phase_6_review"
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)

    result_missing = loader(scratch)
    assert result_missing is None, (
        f"AC12: missing post-fix-pytest.md must return None; got {result_missing!r}"
    )

    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    (reviews_dir / "post-fix-pytest.md").write_text(
        "small report body\n", encoding="utf-8"
    )
    result_present = loader(scratch)
    assert isinstance(result_present, tuple) and len(result_present) == 2, (
        f"AC12: present post-fix-pytest.md must return a (str, bool) tuple; "
        f"got {result_present!r}"
    )
    text, truncated = result_present
    assert isinstance(text, str) and "small report body" in text, (
        f"AC12: returned text must contain the report body; got text={text!r}"
    )
    assert truncated is False, (
        f"AC12: truncated must be False for a small report; got {truncated!r}"
    )
