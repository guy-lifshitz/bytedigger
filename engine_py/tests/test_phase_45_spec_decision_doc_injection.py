"""RED tests for HAL agreement 8A9C0F24 Subtask C — decision_doc text
injection into the spec-writer prompt.

Subtask C is the LAST reason-axis fix in the phase_45_spec writer reliability
umbrella. Subtask A (verify_spec_completeness gate) and Subtask B (REVISION
block bullet reorder + no-changelog rule) have already shipped.

Bug being fixed
---------------
In ``_build_spec_prompt`` (workflows/phase_45_spec.py), when
``cfg["decision_doc"]`` is provided via ``--decision-doc <path>`` (see
``SYSTEM/cli/build/build-cli.ts:24`` and ``engine_py/bytedigger_engine/skip_logic.py``), its
**text** is never threaded into the spec-writer prompt body. The current code
only references ``architecture.md`` by path. When ``ctx.question`` is sparse,
the LLM writer (Opus or Sonnet) fabricates content from the architecture path
string instead of the actual decision doc — root cause of CE66FA4D
(Opus fabricated bare-filename citations on full-spec run forge-1777986051).

GREEN will:
    (a) read ``cfg["decision_doc"]`` text via ``_resolve_decision_doc_path``,
    (b) inline it into the prompt body under a recognizable section heading
        (``DECISION DOC`` / ``DECISION DOCUMENT`` / ``## DECISION DOC``),
    (c) keep the resolved path reference too (so the writer can re-Read for
        full context, especially after truncation),
    (d) handle oversize files (>80,000 B) via head/tail truncation with a
        ``truncated`` marker,
    (e) handle missing-file gracefully — no exception, no DECISION DOC section.

Tests are mechanical string asserts — no LLM, no network, no engine execution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


# ─── helpers (mirrors test_phase_45_spec_A813CA08.py boilerplate) ─────────────


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session-8A9C0F24-C",
        persona="hal",
        framework=None,
        domain=None,
    )


def get_spec_prompt(scratchpad: Path, *, _prev=None, **org_extra) -> str:
    """Drive _build_spec_prompt and pull the prompt string out."""
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    ctx = make_ctx(scratchpad, **org_extra)
    result = _build_spec_prompt(ctx, _prev)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"]


def _has_decision_doc_heading(prompt: str) -> bool:
    """Accept any of the three documented heading forms (literal characters)."""
    return (
        "DECISION DOC" in prompt
        or "DECISION DOCUMENT" in prompt
        or "## DECISION DOC" in prompt
    )


# ─── tests ────────────────────────────────────────────────────────────────────


def test_decision_doc_text_inlined_into_prompt(tmp_path, monkeypatch):
    """When cfg['decision_doc'] points to an existing file, the prompt body
    MUST contain the file's raw text (a unique marker we wrote into it).

    Today the code only inlines architecture.md by path — fails RED.
    """
    scratchpad = tmp_path / "scratch"
    decision_doc = tmp_path / "decision.md"
    decision_doc.write_text(
        "# Architecture decision\n\nSome rationale.\n"
        "UNIQUE_MARKER_DECISION_DOC_42\n"
        "More body text below.\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    prompt = get_spec_prompt(scratchpad, decision_doc=str(decision_doc))

    assert "UNIQUE_MARKER_DECISION_DOC_42" in prompt, (
        "decision_doc text not inlined — prompt does not contain "
        "UNIQUE_MARKER_DECISION_DOC_42 (the unique marker written into "
        f"{decision_doc})"
    )


def test_decision_doc_section_heading_present(tmp_path, monkeypatch):
    """When decision_doc is set + exists, the prompt MUST contain a
    recognizable section heading: any of 'DECISION DOC', 'DECISION DOCUMENT',
    or '## DECISION DOC' (literal characters, case-sensitive).

    Today: no such heading is emitted — fails RED.
    """
    scratchpad = tmp_path / "scratch"
    decision_doc = tmp_path / "decision.md"
    decision_doc.write_text("# Decision body\n\nfoo bar baz\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    prompt = get_spec_prompt(scratchpad, decision_doc=str(decision_doc))

    assert _has_decision_doc_heading(prompt), (
        "spec prompt missing 'DECISION DOC' / 'DECISION DOCUMENT' / "
        "'## DECISION DOC' section heading when decision_doc is set"
    )


def test_decision_doc_path_still_referenced_after_inline(tmp_path, monkeypatch):
    """Even after inlining, the prompt MUST also reference the resolved
    decision_doc path so the writer can re-Read for full context (especially
    after truncation).

    Today the path is never referenced (only architecture.md is) — fails RED.
    """
    scratchpad = tmp_path / "scratch"
    decision_doc = tmp_path / "decision.md"
    decision_doc.write_text("decision body line 1\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    prompt = get_spec_prompt(scratchpad, decision_doc=str(decision_doc))
    resolved = decision_doc.resolve()

    assert str(resolved) in prompt, (
        f"resolved decision_doc path {resolved} not referenced in prompt — "
        "writer cannot re-Read for full context after inline"
    )


def test_no_decision_doc_in_cfg_does_not_break(tmp_path):
    """Regression guard — when cfg has no 'decision_doc' key, the prompt
    builds normally and contains no DECISION DOC heading.

    Passes today (current code ignores decision_doc entirely); MUST still pass
    after GREEN.
    """
    scratchpad = tmp_path / "scratch"

    # No decision_doc passed at all.
    prompt = get_spec_prompt(scratchpad)

    assert not _has_decision_doc_heading(prompt), (
        "no decision_doc was set, but prompt still contains a DECISION DOC "
        "heading — false-positive injection"
    )
    # Sanity — the rest of the prompt still builds.
    assert "OUTPUT — " in prompt
    assert "FEATURE REQUEST:" in prompt

    # And empty string also yields no injection.
    prompt_empty = get_spec_prompt(scratchpad, decision_doc="")
    assert not _has_decision_doc_heading(prompt_empty), (
        "empty decision_doc string produced a DECISION DOC heading — "
        "should be treated as absent"
    )


def test_decision_doc_missing_file_graceful(tmp_path):
    """When cfg['decision_doc'] points to a non-existent path,
    _build_spec_prompt MUST NOT raise. The resulting prompt should not
    contain a DECISION DOC heading (text could not be read), but the rest of
    the prompt builds normally.

    Today the field is ignored entirely so this passes vacuously — regression
    guard for graceful degradation when GREEN adds the file-read path.
    """
    scratchpad = tmp_path / "scratch"
    missing = tmp_path / "does_not_exist_decision.md"
    assert not missing.exists(), "test setup invariant: file must NOT exist"

    # Must not raise.
    prompt = get_spec_prompt(scratchpad, decision_doc=str(missing))

    # No DECISION DOC heading because text could not be read.
    assert not _has_decision_doc_heading(prompt), (
        "decision_doc file missing, but prompt still emitted a DECISION DOC "
        "heading — should degrade gracefully (no heading without text)"
    )
    # Rest of the prompt still builds.
    assert "OUTPUT — " in prompt
    assert "FEATURE REQUEST:" in prompt


def test_oversize_decision_doc_truncated_with_marker(tmp_path, monkeypatch):
    """When decision_doc file is larger than 80,000 bytes, the inlined
    content in the prompt MUST be truncated and contain a truncation marker
    string (any of: 'truncated', 'TRUNCATED', '... (truncated', case-insensitive).
    The full file path MUST still be referenced.

    Today the code does not inline at all — fails RED on the truncation
    marker assert.
    """
    scratchpad = tmp_path / "scratch"
    big = tmp_path / "big_decision.md"
    big.write_text("x" * 95000 + "UNIQUE_TAIL_MARKER_99" + "x" * 5000, encoding="utf-8")
    assert big.stat().st_size > 80_000, "test setup invariant: file must be >80kB"

    monkeypatch.chdir(tmp_path)
    prompt = get_spec_prompt(scratchpad, decision_doc=str(big))
    lower = prompt.lower()

    # Truncation marker present.
    assert "truncated" in lower, (
        "oversize decision_doc inlined without a 'truncated' marker — "
        "prompt may exceed token budget without warning the writer"
    )

    # Full file path still referenced (so writer can re-Read).
    resolved = big.resolve()
    assert str(resolved) in prompt, (
        f"oversize decision_doc path {resolved} not referenced — writer "
        "cannot recover the full body via Read after truncation"
    )

    # Sanity: the prompt must NOT inline the full 100kB body verbatim (would
    # blow the token budget). This is a soft check via byte size — full
    # content body would push the prompt past 100kB.
    assert len(prompt.encode("utf-8")) < 100_000, (
        f"prompt is {len(prompt.encode('utf-8'))} bytes — full oversize body "
        "appears to have been inlined without truncation"
    )


def test_decision_doc_complexity_independent(tmp_path, monkeypatch):
    """decision_doc inlining should fire regardless of cfg['complexity'].
    The skip-logic in skip_logic.py only governs phase_2/3 self-skip — but
    spec-writer (phase_4.5) ALWAYS runs, and ALWAYS benefits from seeing the
    decision_doc text.

    Test with complexity = 'COMPLEX'. Fails RED today.
    """
    scratchpad = tmp_path / "scratch"
    decision_doc = tmp_path / "decision_complex.md"
    decision_doc.write_text(
        "# COMPLEX architecture\n\nUNIQUE_MARKER_DECISION_DOC_42\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    prompt = get_spec_prompt(
        scratchpad,
        decision_doc=str(decision_doc),
        complexity="COMPLEX",
    )

    assert "UNIQUE_MARKER_DECISION_DOC_42" in prompt, (
        "decision_doc text not inlined when complexity=COMPLEX — inlining "
        "must be complexity-independent (skip_logic only covers phase_2/3)"
    )
    assert _has_decision_doc_heading(prompt), (
        "DECISION DOC heading missing under complexity=COMPLEX"
    )


def test_multibyte_decision_doc_no_duplication(tmp_path, monkeypatch):
    """Multi-byte (Cyrillic) content where utf-8 byte size > 80kB cap but
    char count fits within head_chars + tail_chars (60_000 + 15_000 = 75_000).

    Today's truncation branch slices ``text[:60_000]`` (returns the WHOLE text
    when len < 60_000), then concatenates a 'truncated' marker, then re-slices
    ``text[-15_000:]`` of the SAME text — output contains duplicated content
    with a misleading marker. Fails RED.

    After F2: when len(text) <= head+tail, body MUST be inlined as-is, with no
    truncation marker.
    """
    scratchpad = tmp_path / "scratch"
    decision_doc = tmp_path / "cyrillic_decision.md"
    # "Привет миру " is 12 chars, ~22 bytes utf-8. * 4000 = 48_000 chars,
    # ~88_000 bytes — over the 80kB cap, but well under 75_000 char head+tail.
    # Wrap with unique head/tail sentinels so we can detect duplication
    # without false positives from the repeating Cyrillic pattern.
    head_sentinel = "DECISION_DOC_HEAD_SENTINEL_42_UNIQUE"
    tail_sentinel = "DECISION_DOC_TAIL_SENTINEL_99_UNIQUE"
    cyrillic_block = (
        head_sentinel + "\n" + ("Привет миру " * 4000) + "\n" + tail_sentinel
    )
    decision_doc.write_text(cyrillic_block, encoding="utf-8")
    nbytes = decision_doc.stat().st_size
    nchars = len(cyrillic_block)
    assert nbytes > 80_000, (
        f"test setup invariant: file must exceed 80kB cap (got {nbytes} bytes)"
    )
    assert nchars < 75_000, (
        f"test setup invariant: char count must fit head+tail (got {nchars} chars)"
    )

    monkeypatch.chdir(tmp_path)
    prompt = get_spec_prompt(scratchpad, decision_doc=str(decision_doc))

    # Unique substring is present.
    assert "Привет миру " in prompt, (
        "Cyrillic decision_doc text not inlined into prompt"
    )

    # No truncation marker because char count fits without overlap.
    assert "truncated" not in prompt.lower(), (
        "multi-byte decision_doc with byte-size > cap but char-count < head+tail "
        "incorrectly emitted a 'truncated' marker — head and tail would overlap "
        "producing duplicate-with-marker corruption"
    )

    # Duplication guard: head sentinel must appear exactly once. If the
    # pre-fix truncation branch fires, head=text[:60_000] returns the WHOLE
    # text including head_sentinel, then tail=text[-15_000:] would NOT include
    # head_sentinel — but the buggy concatenation duplicates large parts of
    # the body. The cleanest invariant: head_sentinel appears exactly once
    # and tail_sentinel appears exactly once.
    head_count = prompt.count(head_sentinel)
    tail_count = prompt.count(tail_sentinel)
    assert head_count == 1, (
        f"head sentinel appears {head_count}× in prompt — expected 1 "
        "(duplication implies head/tail overlap re-slice)"
    )
    assert tail_count == 1, (
        f"tail sentinel appears {tail_count}× in prompt — expected 1 "
        "(duplication implies head/tail overlap re-slice)"
    )


def test_binary_decision_doc_does_not_raise(tmp_path):
    """When --decision-doc accidentally points at a binary file, the
    UTF-8 decode raises UnicodeDecodeError. Today's ``except OSError:`` does
    NOT catch it (UnicodeDecodeError is a ValueError subclass, not OSError),
    so _build_spec_prompt crashes. Fails RED with UnicodeDecodeError.

    After F3: widen except to (OSError, UnicodeDecodeError). The decode failure
    is silently absorbed — no DECISION DOC heading, no exception.
    """
    scratchpad = tmp_path / "scratch"
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\x80\x81\x82\xff" * 1000)
    assert binary.exists()

    # Must NOT raise.
    prompt = get_spec_prompt(scratchpad, decision_doc=str(binary))

    # Failed read → no DECISION DOC heading.
    assert not _has_decision_doc_heading(prompt), (
        "binary decision_doc could not be decoded, but prompt still emitted "
        "a DECISION DOC heading — should degrade silently"
    )
    # Rest of the prompt still builds.
    assert "OUTPUT — " in prompt
    assert "FEATURE REQUEST:" in prompt


# ─── C1E92360 — logger.debug at silent-skip sites ─────────────────────────────


def _import_read_block():
    """Import _read_decision_doc_block directly (not via _build_spec_prompt)."""
    from bytedigger_engine.workflows.phase_45_spec import _read_decision_doc_block  # noqa: PLC0415

    return _read_decision_doc_block


def test_no_cfg_logs_debug_reason(caplog):
    """Sites 1a (None) and 1b ({}) emit DEBUG with reason='no_cfg'."""
    import logging

    _read_decision_doc_block = _import_read_block()
    with caplog.at_level(logging.DEBUG):
        r1 = _read_decision_doc_block(None)
        r2 = _read_decision_doc_block({})

    assert r1 == ""
    assert r2 == ""
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("no_cfg" in m for m in debug_msgs), (
        f"Expected DEBUG message with 'no_cfg' but got: {debug_msgs}"
    )


def test_unset_logs_debug_reason(caplog):
    """Sites 2a (empty string) and 2b (missing key) emit DEBUG with reason='unset'."""
    import logging

    _read_decision_doc_block = _import_read_block()
    with caplog.at_level(logging.DEBUG):
        r1 = _read_decision_doc_block({"decision_doc": ""})
        r2 = _read_decision_doc_block({"other_key": "val"})

    assert r1 == ""
    assert r2 == ""
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("unset" in m for m in debug_msgs), (
        f"Expected DEBUG message with 'unset' but got: {debug_msgs}"
    )


def test_unresolved_logs_debug_reason(tmp_path, monkeypatch, caplog):
    """Path rejected by _resolve_decision_doc_path emits DEBUG with reason='unresolved'."""
    import logging

    _read_decision_doc_block = _import_read_block()
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.DEBUG):
        result = _read_decision_doc_block({"decision_doc": "../../../etc/passwd"})

    assert result == ""
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("unresolved" in m for m in debug_msgs), (
        f"Expected DEBUG message with 'unresolved' but got: {debug_msgs}"
    )


def test_read_error_logs_debug_reason(tmp_path, monkeypatch, caplog):
    """OSError on read emits DEBUG with reason='read_error'."""
    import logging
    from unittest.mock import patch

    _read_decision_doc_block = _import_read_block()
    doc = tmp_path / "locked.md"
    doc.write_text("content", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    original_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if self == doc.resolve():
            raise OSError("simulated permission denied")
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", mock_read_text):
        with caplog.at_level(logging.DEBUG):
            result = _read_decision_doc_block({"decision_doc": str(doc)})

    assert result == ""
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("read_error" in m for m in debug_msgs), (
        f"Expected DEBUG message with 'read_error' but got: {debug_msgs}"
    )


def test_decode_error_logs_debug_reason(tmp_path, monkeypatch, caplog):
    """UnicodeDecodeError on read emits DEBUG with reason='decode_error'."""
    import logging

    _read_decision_doc_block = _import_read_block()
    binary = tmp_path / "binary.bin"
    # Bytes that are invalid utf-8 even with utf-8-sig stripping
    binary.write_bytes(b"\x80\x81\x82\xff" * 1000)
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.DEBUG):
        result = _read_decision_doc_block({"decision_doc": str(binary)})

    assert result == ""
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("decode_error" in m for m in debug_msgs), (
        f"Expected DEBUG message with 'decode_error' but got: {debug_msgs}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
