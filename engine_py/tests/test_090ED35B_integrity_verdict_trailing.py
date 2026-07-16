"""RED tests for 090ED35B — integrity verdict trailing-gloss fix.

Two coupled defects:
  Class A (parser): `last_standalone_line_verdict` fail-closes on glossed lines like
    `VERDICT: SPEC_CHANGE — all hunks legitimate spec-driven updates`
    because the strict `$` anchor rejects any trailing text.
  Class B (prompt): `_integrity_output_schema()` instruction says "no trailing
    punctuation" yet the example lines carry trailing glosses — contradiction.

§4 rule (D1CF5FDF): `allow_trailing` does not exist yet (GREEN adds it). Calling
`last_standalone_line_verdict(..., allow_trailing=True)` raises `TypeError` INSIDE
each test body — the file COLLECTS cleanly but the test FAILs on assertion/exception.

Import pattern mirrors test_EEFD480F_verdict_parse_lib.py (conftest singleton
already adds engine_root + workflows to sys.path; lib/ is added inside each test
body that needs verdict_parse — no module-level sys.path manipulation per §1q /
81F97F3D).

Agreement: 090ED35B · Phase: RED
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
_LIB_PATH = str(ENGINE_ROOT / "lib")


# ─── helpers ──────────────────────────────────────────────────────────────────


def _ensure_lib_path() -> None:
    """Insert lib/ into sys.path inside a test body (not at module level)."""
    import sys
    if _LIB_PATH not in sys.path:
        sys.path.insert(0, _LIB_PATH)


# Tokens used by both phases (order matches production call sites).
_TOKENS = ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES")


# ─── AC1: default (no allow_trailing) rejects trailing gloss ──────────────────


def test_ac1_strict_default_rejects_trailing_gloss():
    """AC1: `last_standalone_line_verdict` with NO `allow_trailing` kwarg rejects
    a glossed line like 'VERDICT: SPEC_CHANGE — all hunks legit' → 'UNKNOWN'.

    This pins the *default = strict* invariant (regression floor).
    Passes pre-GREEN and post-GREEN (default stays strict).
    """
    _ensure_lib_path()
    from verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "VERDICT: SPEC_CHANGE — all hunks legit\n"
    result = last_standalone_line_verdict(raw, _TOKENS, fallback="UNKNOWN")
    assert result == "UNKNOWN", (
        f"strict default must reject trailing gloss; got {result!r}"
    )


# ─── AC2: allow_trailing=True accepts glossed line ────────────────────────────


def test_ac2_allow_trailing_accepts_gloss():
    """AC2: same glossed input + `allow_trailing=True` → 'SPEC_CHANGE'.

    `allow_trailing` does not exist yet — TypeError INSIDE the body = clean FAIL.
    Passes after GREEN adds the kwarg.
    """
    _ensure_lib_path()
    from verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "VERDICT: SPEC_CHANGE — all hunks legit\n"
    result = last_standalone_line_verdict(raw, _TOKENS, fallback="UNKNOWN", allow_trailing=True)
    assert result == "SPEC_CHANGE", (
        f"allow_trailing=True must parse glossed SPEC_CHANGE; got {result!r}"
    )


# ─── AC3: word-boundary guard — SPEC_CHANGED must NOT match SPEC_CHANGE ───────


def test_ac3_no_prefix_bleed_spec_changed():
    """AC3: `allow_trailing=True`, 'VERDICT: SPEC_CHANGED extra' → 'UNKNOWN'.

    The char after the token is 'D' (a word char) so SPEC_CHANGE must NOT
    match inside SPEC_CHANGED.
    """
    _ensure_lib_path()
    from verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "VERDICT: SPEC_CHANGED extra\n"
    result = last_standalone_line_verdict(raw, _TOKENS, fallback="UNKNOWN", allow_trailing=True)
    assert result == "UNKNOWN", (
        f"SPEC_CHANGED must NOT match SPEC_CHANGE (word-boundary guard); got {result!r}"
    )


# ─── AC4: head anchor intact — mid-prose VERDICT: does not match ──────────────


def test_ac4_head_anchor_mid_prose_ignored():
    """AC4: `allow_trailing=True`, 'I think the VERDICT: SPEC_CHANGE is right' → 'UNKNOWN'.

    Only the *tail* is relaxed; the head anchor (line must start with VERDICT:
    after optional ws/bold) is UNCHANGED.

    This test may already PASS pre-GREEN (mid-prose never matched even strictly).
    It pins the invariant.
    """
    _ensure_lib_path()
    from verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "I think the VERDICT: SPEC_CHANGE is right\n"
    result = last_standalone_line_verdict(raw, _TOKENS, fallback="UNKNOWN", allow_trailing=True)
    assert result == "UNKNOWN", (
        f"mid-prose VERDICT: must be ignored (head anchor intact); got {result!r}"
    )


# ─── AC5: last-match-wins preserved with two glossed standalone lines ──────────


def test_ac5_last_match_wins_two_glossed_lines():
    """AC5: `allow_trailing=True`, two standalone glossed verdict lines → LAST wins.

    First line: VERDICT: SPEC_CHANGE — prose
    Last line:  VERDICT: ASSERTION_GAMING — prose
    Expected: ASSERTION_GAMING (last wins).
    """
    _ensure_lib_path()
    from verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = (
        "VERDICT: SPEC_CHANGE — first pass looks clean\n"
        "On reflection:\n"
        "VERDICT: ASSERTION_GAMING — hunk 3 clearly gamed\n"
    )
    result = last_standalone_line_verdict(raw, _TOKENS, fallback="UNKNOWN", allow_trailing=True)
    assert result == "ASSERTION_GAMING", (
        f"last glossed standalone line must win; got {result!r}"
    )


# ─── AC6: phase_5_integrity._parse_verdict handles ASSERTION_GAMING gloss ─────


def test_ac6_phase5_parse_verdict_assertion_gaming_with_gloss():
    """AC6: `phase_5_integrity._parse_verdict('VERDICT: ASSERTION_GAMING — hunk 2 gamed')`
    → 'ASSERTION_GAMING'.

    fail-CLOSED preserved: gate fires even when the LLM appends a gloss.
    Depends on `allow_trailing=True` in the caller — FAILS pre-GREEN.
    """
    from phase_5_integrity import _parse_verdict  # noqa: PLC0415

    raw = "VERDICT: ASSERTION_GAMING — hunk 2 gamed\n"
    result = _parse_verdict(raw)
    assert result == "ASSERTION_GAMING", (
        f"phase_5 _parse_verdict must return ASSERTION_GAMING on glossed line; got {result!r}"
    )


# ─── AC7: phase_5_integrity._parse_verdict handles SPEC_CHANGE gloss ──────────


def test_ac7_phase5_parse_verdict_spec_change_with_gloss():
    """AC7: `phase_5_integrity._parse_verdict('VERDICT: SPEC_CHANGE — all hunks
    legitimate spec-driven updates')` → 'SPEC_CHANGE' (NOT UNKNOWN).

    Fails pre-GREEN: strict parser returns UNKNOWN on the trailing gloss.
    Passes after GREEN passes allow_trailing=True.
    """
    from phase_5_integrity import _parse_verdict  # noqa: PLC0415

    raw = "VERDICT: SPEC_CHANGE — all hunks legitimate spec-driven updates\n"
    result = _parse_verdict(raw)
    assert result == "SPEC_CHANGE", (
        f"phase_5 _parse_verdict must parse glossed SPEC_CHANGE; got {result!r} "
        f"(strict parser returns UNKNOWN — allow_trailing=True not yet passed)"
    )


# ─── AC8: phase_6_fix_integrity._parse_verdict handles SPEC_CHANGE gloss ──────


def test_ac8_phase6_parse_verdict_spec_change_with_gloss():
    """AC8: `phase_6_fix_integrity._parse_verdict('VERDICT: SPEC_CHANGE — all hunks
    legitimate spec-driven updates')` → 'SPEC_CHANGE'.

    Mirror of AC7 for the phase_6 caller. Fails pre-GREEN.
    """
    from phase_6_fix_integrity import _parse_verdict  # noqa: PLC0415

    raw = "VERDICT: SPEC_CHANGE — all hunks legitimate spec-driven updates\n"
    result = _parse_verdict(raw)
    assert result == "SPEC_CHANGE", (
        f"phase_6 _parse_verdict must parse glossed SPEC_CHANGE; got {result!r} "
        f"(strict parser returns UNKNOWN — allow_trailing=True not yet passed)"
    )


# ─── AC9: KEYSTONE round-trip — schema example lines parse to their tokens ─────


def test_ac9_keystone_schema_example_lines_round_trip():
    """AC9 KEYSTONE: For each canonical glossed example line extracted verbatim from
    `phase_5_integrity._integrity_output_schema()`, `_parse_verdict(line)` must
    return the expected non-UNKNOWN token.

    Extraction: find lines matching 'VERDICT: <TOK> —' in the schema.
    Expected tokens: SPEC_CHANGE, LEGITIMATE_REFACTOR, ASSERTION_GAMING.

    FAILS pre-GREEN: strict parser returns UNKNOWN for each glossed line.
    Passes after GREEN: allow_trailing=True in _parse_verdict.

    §1y: Point = _parse_verdict (phase_5_integrity L402),
         Host = test body calling _integrity_output_schema() + _parse_verdict,
         Test-path = function call with extracted literal lines.
    """
    import re  # noqa: PLC0415
    from phase_5_integrity import _integrity_output_schema, _parse_verdict  # noqa: PLC0415

    schema = _integrity_output_schema()
    # Extract lines of the canonical form:  "  VERDICT: <TOK> — <prose>"
    # The leading spaces in the schema string are literal (indented example lines).
    glossed_pattern = re.compile(r"VERDICT:\s+([A-Z_]+)\s+—")
    found_tokens = []
    failures = []

    for line in schema.splitlines():
        m = glossed_pattern.search(line)
        if m:
            token = m.group(1)
            # Only care about the three decision tokens, not the WRONG-forms examples
            if token in ("SPEC_CHANGE", "LEGITIMATE_REFACTOR", "ASSERTION_GAMING"):
                found_tokens.append((line.strip(), token))
                parsed = _parse_verdict(line.strip())
                if parsed != token:
                    failures.append(
                        f"line={line.strip()!r}  expected={token!r}  got={parsed!r}"
                    )

    # Sanity: schema must contain all three canonical example lines
    found_token_names = {tok for _, tok in found_tokens}
    for expected_tok in ("SPEC_CHANGE", "LEGITIMATE_REFACTOR", "ASSERTION_GAMING"):
        assert expected_tok in found_token_names, (
            f"schema missing canonical example for {expected_tok!r}; "
            f"found tokens: {found_token_names}"
        )

    assert not failures, (
        "Round-trip FAILED for glossed schema example lines (strict parser → UNKNOWN):\n"
        + "\n".join(failures)
    )


# ─── AC10: _integrity_output_schema() no longer contains "no trailing punctuation" ──


def test_ac10_schema_drops_no_trailing_punctuation_instruction():
    """AC10: `_integrity_output_schema()` in BOTH phase_5 and phase_6 no longer
    contains the contradictory instruction 'no trailing punctuation'.

    FAILS pre-GREEN: both files currently contain that substring.
    Passes after GREEN rewrites the instruction block.
    """
    from phase_5_integrity import _integrity_output_schema as p5_schema  # noqa: PLC0415
    from phase_6_fix_integrity import _integrity_output_schema as p6_schema  # noqa: PLC0415

    p5 = p5_schema()
    p6 = p6_schema()

    assert "no trailing punctuation" not in p5, (
        "phase_5_integrity._integrity_output_schema() still contains "
        "'no trailing punctuation' — Class B instruction fix not applied"
    )
    assert "no trailing punctuation" not in p6, (
        "phase_6_fix_integrity._integrity_output_schema() still contains "
        "'no trailing punctuation' — Class B instruction fix not applied"
    )


# ─── AC11: passthrough_stub echo still yields result.status == "error" ────────


def test_ac11_passthrough_echo_still_blocked(tmp_path):
    """AC11: echo-defense preserved — a `passthrough_stub` echo of the full
    integrity prompt still yields `result.status == 'error'`.

    The head anchor allows leading whitespace (`^[ \t]*`), so the schema's
    INDENTED glossed example lines DO match the standalone-line rule. Pre-GREEN the
    strict tail-anchor rejects the glossed lines → UNKNOWN on all → the echo blocks
    via E_INTEGRITY_NO_MARKER. Post-GREEN, with `allow_trailing=True`, the glossed
    lines parse and last-match-wins lands on an ASSERTION_GAMING variant from the
    WRONG-forms block (the lowercase `Verdict: assertion_gaming`, matched
    case-insensitively; the `## VERDICT:` heading form is correctly rejected by the
    head anchor) → the echo blocks via E_INTEGRITY_ASSERTION_GAMING.

    Either way a non-deciding echo BLOCKS. We assert only the durable safety
    property — result.status == 'error' — so the test is robust to which specific
    block-code fires.

    §1i: no singleton-resource contention — tmp_path isolation, no port/file-lock.
    §1y: Point = _classify_diff_verdict (phase_5_integrity),
         Host  = WorkflowEngine.execute,
         Test-path = passthrough_stub echoes prompt → verdict step parses.
    """
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    # WorkflowEngine and helpers are on sys.path via conftest singleton.
    from engine import WorkflowEngine  # noqa: PLC0415
    from contracts import WorkflowContext  # noqa: PLC0415
    from phase_5_integrity import phase_5_integrity_workflow  # noqa: PLC0415

    def passthrough_stub() -> list[str]:
        return ["python3", "-c", "import sys; sys.stdout.write(sys.stdin.read())"]

    def init_repo(repo: Path) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)

    def commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
        p = repo / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        subprocess.run(["git", "add", relpath], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)

    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    commit_file(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 1\n", "red")
    commit_file(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 2\n", "green-gamed")

    scratchpad = tmp_path / "scratch"
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)}
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={**org, "llm_command": passthrough_stub()},
        question="Add foo feature",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute("p5i", ctx)

    assert result.status == "error", (
        f"passthrough echo of integrity prompt must be BLOCKED (fail-closed); "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )
