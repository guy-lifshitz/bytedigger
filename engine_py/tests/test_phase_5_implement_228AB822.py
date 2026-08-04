"""228AB822 RED tests — GREEN prompt rewrite contract for `_build_green_prompt`.

Closes agreement 228AB822: phase_5 GREEN step produced ~26K output tokens
(505s wall) on the last build because the GREEN prompt does not explicitly
forbid file-content echo / narrative summaries and sets no response-size
budget. Sonnet dumps full file contents and prose explanations on top of
the actual Edit tool calls.

Desired behavior (post-GREEN-rewrite):
1. Prompt explicitly bans dumping/echoing/quoting file contents back.
2. Prompt explicitly bans narrative/prose explanation of changes.
3. Prompt sets an explicit response-token budget (numeric ≤5000, or named).
4. Existing "Edit/Write IS the deliverable" constraint preserved.
5. Existing "GREEN COMPLETE" marker trailer preserved.

These tests FAIL against current `_build_green_prompt` (no echo/narrative
ban, no token budget). They PASS once GREEN rewrites the prompt.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


# ─── helpers (mirror test_phase_5_implement_B6247E87.py style) ───────────────


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_validation_pass(scratchpad: Path):
    """Synthetic prev StepResult shaped like gate_on_validation output.

    `_build_green_prompt` reads keys: spec_path, red_log_path,
    validation_doc_path, verdict.
    """
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    return StepResult(
        status="ok",
        data={
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "validation_doc_path": str(scratchpad / "reviews/build-opus-validation.md"),
            "verdict": "PASS",
        },
        duration_ms=0,
        step_name="gate_on_validation",
    )


def _build_prompt(tmp_path: Path) -> str:
    """Invoke `_build_green_prompt` with minimal valid inputs and return the
    resulting prompt body (stripped of role-template prefix that's just role
    boilerplate from injection files — the rules under test live in the
    function-owned body)."""
    from bytedigger_engine.workflows.phase_5_implement import _build_green_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad)
    prev = _prev_validation_pass(scratchpad)
    result = _build_green_prompt(ctx, prev)
    assert result.status == "ok", f"_build_green_prompt returned {result.status!r}: {getattr(result, 'error', None)!r}"
    assert isinstance(result.data, dict) and "prompt" in result.data, "prompt missing from result.data"
    return result.data["prompt"]


# ─── Test 1: explicit no-echo / no-dump-file-contents rule ───────────────────


def test_228ab822_prompt_explicitly_bans_file_content_echo(tmp_path):
    """Prompt must contain an explicit ban on dumping/echoing/quoting file
    contents. Without this, Sonnet pastes whole file bodies after Edit calls
    and inflates the response by 10-20K tokens.

    Match liberally for any of: 'do not echo', 'do not dump', 'no file echo',
    'no file dump', 'do not output file contents', 'do not paste',
    'no fenced code blocks of edited files', 'no code blocks of files you
    edited', or similar (case-insensitive).
    """
    prompt = _build_prompt(tmp_path).lower()

    candidates = [
        "do not echo",
        "do not dump",
        "no file echo",
        "no file dump",
        "do not output file contents",
        "do not paste",
        "no fenced code blocks of edited files",
        "no code blocks of files you edited",
        "do not quote file contents",
        "do not repeat file contents",
        "no file content echo",
        "do not include file contents",
    ]
    matched = [c for c in candidates if c in prompt]

    assert matched, (
        "GREEN prompt does not explicitly ban file-content echo/dump. "
        "Expected at least one of: " + ", ".join(repr(c) for c in candidates) + ". "
        "228AB822 root cause: without this ban, Sonnet emits ~26K output tokens "
        "of file dumps after Edit calls. Add explicit no-echo rule to prompt body."
    )


# ─── Test 2: explicit no-narrative / no-prose rule ───────────────────────────


def test_228ab822_prompt_explicitly_bans_narrative_explanation(tmp_path):
    """Prompt must contain an explicit ban on narrative/prose explanations
    of changes. Current prompt says 'not a narrative report about
    implementing' (in ROLE) and 'Do NOT include a narrative summary in place
    of the implementation' (in OUTPUT) — but both leave a loophole: a
    narrative ALONGSIDE the implementation is not banned.

    Test demands a distinct, unambiguous ban: 'no narrative', 'no prose',
    'no commentary', 'no preamble', 'no explanation of changes', or
    'do not explain' (case-insensitive). The phrase must appear as a
    rule line, not buried in a 'not X in place of Y' construction.
    """
    prompt = _build_prompt(tmp_path).lower()

    # Tight matchers: imperative bans that stand alone.
    strict_phrases = [
        "no narrative",
        "no prose",
        "no commentary",
        "no preamble",
        "no explanation of changes",
        "do not explain",
        "do not narrate",
        "no explanations",
    ]
    matched = [p for p in strict_phrases if p in prompt]

    assert matched, (
        "GREEN prompt lacks an unambiguous narrative/prose ban. "
        "Current prompt has 'not a narrative report' and 'Do NOT include a "
        "narrative summary in place of the implementation' — both allow "
        "narrative ALONGSIDE the implementation. Add a strict standalone "
        "rule from: " + ", ".join(repr(p) for p in strict_phrases) + "."
    )


# ─── Test 3: explicit response-size / token budget ───────────────────────────


def test_228ab822_prompt_sets_explicit_response_token_budget(tmp_path):
    """Prompt must declare an explicit response-size budget. Either:
      - Numeric: '2000 tokens', '2k tokens', '5000 tokens', '5k tokens',
        '<5K tokens', etc. — any number ≤ 5000.
      - Named: 'keep response under', 'response size budget',
        'response token budget', 'output token budget', 'response budget'.

    Without a stated budget, Sonnet has no internal pressure to compress —
    last build emitted ~26K output tokens. Target is ≤5K.
    """
    prompt = _build_prompt(tmp_path)
    prompt_lower = prompt.lower()

    named_phrases = [
        "keep response under",
        "response size budget",
        "response token budget",
        "output token budget",
        "response budget",
        "keep output under",
        "response must be under",
    ]
    named_match = [p for p in named_phrases if p in prompt_lower]

    # Numeric budget: digits + optional 'k' + 'tokens', or '<N' / '≤N' style.
    # Examples that should match: "2000 tokens", "2k tokens", "5K tokens",
    # "under 5000 tokens", "≤5000 tokens".
    numeric_pattern = re.compile(
        r"(?:under|≤|<=|<|max(?:imum)?|budget[:\s]+)?\s*(\d{1,5})\s*(k)?\s*tokens",
        re.IGNORECASE,
    )
    numeric_matches: list[int] = []
    for m in numeric_pattern.finditer(prompt_lower):
        n = int(m.group(1))
        if m.group(2):  # 'k' suffix → multiply
            n *= 1000
        numeric_matches.append(n)
    valid_numeric = [n for n in numeric_matches if 0 < n <= 5000]

    assert named_match or valid_numeric, (
        "GREEN prompt has no explicit response-size budget. "
        f"Named-phrase match: {named_match!r}; numeric matches: {numeric_matches!r} "
        f"(need at least one ≤5000). Add a budget line to the prompt — "
        "228AB822 root cause: without a budget, GREEN emits ~26K output tokens. "
        "Target ≤5K. Examples: 'Keep response under 2000 tokens.', "
        "'Response token budget: 2K tokens.'"
    )


# ─── Test 4: Edit/Write tool requirement preserved ───────────────────────────


def test_228ab822_prompt_preserves_edit_tool_requirement(tmp_path):
    """The existing constraint that file changes go via the Edit/Write tool
    must remain. GREEN must not strip it while adding the new rules.

    Asserts both: 'Edit' (tool name) appears, AND a phrase tying file
    changes to the tool (e.g. 'via Edit', 'using Edit', 'Edit tool',
    'Edit/Write').
    """
    prompt = _build_prompt(tmp_path)

    assert "Edit" in prompt, (
        "Prompt no longer mentions 'Edit' tool by name. "
        "228AB822 must NOT remove the Edit-tool-only requirement."
    )

    tying_phrases = [
        "via Edit",
        "using Edit",
        "Edit tool",
        "Edit/Write",
        "with Edit",
        "through Edit",
        "Edit calls",
        "Edit and Write",
    ]
    matched = [p for p in tying_phrases if p in prompt]
    assert matched, (
        "Prompt mentions Edit but no longer ties file changes to the tool. "
        "Expected one of: " + ", ".join(repr(p) for p in tying_phrases) + ". "
        "Original phrasing was 'written via Edit/Write' — keep that contract."
    )


# ─── Test 5: GREEN COMPLETE marker trailer preserved ─────────────────────────


def test_228ab822_prompt_preserves_green_complete_marker(tmp_path):
    """The 'GREEN COMPLETE' trailer is the harness contract — engine parses
    it to detect successful completion. GREEN rewrite must NOT drop it.

    Asserts:
      - Literal 'GREEN COMPLETE' appears.
      - The marker spec includes the test count placeholder ('[N]' or
        'N tests') and a files-modified hint.
    """
    prompt = _build_prompt(tmp_path)

    assert "GREEN COMPLETE" in prompt, (
        "GREEN COMPLETE marker missing from prompt. "
        "This is the engine's success-detection contract — must be preserved."
    )

    # Must spec the test-count placeholder.
    has_count_placeholder = (
        "[N] tests" in prompt
        or "[N]" in prompt
        or re.search(r"\bN tests passing\b", prompt) is not None
    )
    assert has_count_placeholder, (
        "GREEN COMPLETE marker spec must include test-count placeholder "
        "(e.g. '[N] tests passing' or 'N tests passing'). "
        "Engine relies on this shape."
    )

    # Must mention files-modified hint somewhere near the marker spec.
    assert "Files modified" in prompt or "files modified" in prompt.lower(), (
        "GREEN COMPLETE marker spec must mention 'Files modified: [...]'. "
        "Original line: 'GREEN COMPLETE — all [N] tests passing. "
        "Files modified: [path1, path2, ...]'."
    )


# ─── Test 6 (H): prompt body stays under 6KB ─────────────────────────────────


def test_228ab822_prompt_body_stays_under_6kb(tmp_path):
    """Prompt itself must stay compact. Current prompt is ~2KB (verified
    2026-04-30 — see comment below). 6KB ceiling leaves 3-4KB of headroom
    for the new no-echo / no-narrative / token-budget rules.

    Rationale: tests 1-3 demand new rule lines, but GREEN could "satisfy"
    them by pasting a 10KB rule sheet — which would inflate INPUT tokens
    and defeat the cost goal of 228AB822 (reduce GREEN spend). Cap the
    prompt to force concise rules.

    Current size at time of writing (RED): ~2000 bytes (preservation
    guard — PASSES today; would FAIL if GREEN bloats the prompt).
    """
    prompt = _build_prompt(tmp_path)
    # Flake-hardening: the prompt embeds the resolved scratchpad absolute path ~8×.
    # Under concurrent pytest load tmp dirs get longer, inflating the byte count over
    # the cap. This guard measures RULE-CONTENT bloat, not env path length — normalize
    # the env-dependent path to a short token before counting.
    scratch_abs = str((tmp_path / "scratch").resolve())
    # GH475: the prompt also embeds the resolved cwd (repo root) 2× — from a
    # long-path worktree/scratch checkout that alone crossed the cap (5881 bytes
    # + 2×Δcwd-len from ~/.claude baseline). Normalize both env-dependent paths.
    cwd_abs = str(Path.cwd().resolve())
    normalized = prompt.replace(scratch_abs, "/s").replace(cwd_abs, "/c")
    size = len(normalized.encode("utf-8"))
    assert size < 6000, (
        f"GREEN prompt is {size} bytes — exceeds 6KB ceiling. "
        f"228AB822 rewrite must add no-echo/no-narrative/token-budget rules "
        f"CONCISELY. Current pre-rewrite size was ~2KB; 6KB leaves 3-4KB of "
        f"slack. If you need more, the rules are too verbose — tighten them."
    )


# ─── Test 7 (M): echo/narrative bans appear BEFORE OUTPUT: section ───────────


def test_228ab822_echo_and_narrative_bans_appear_before_output_section(tmp_path):
    """The echo-ban and narrative-ban must appear in the rules region of the
    prompt (before the 'OUTPUT:' section), not buried inside or after the
    output-format spec. LLMs weight earlier instructions more strongly, and
    the OUTPUT: block is about the trailer marker — not the place for
    behavioral constraints.

    Will FAIL today: no bans exist yet, so the candidate-phrase searches
    return no matches.
    """
    prompt = _build_prompt(tmp_path)
    prompt_lower = prompt.lower()

    # OUTPUT: marker is required — fail loudly if missing (separate breakage).
    try:
        output_idx = prompt.index("OUTPUT:")
    except ValueError as e:
        raise AssertionError(
            "Prompt missing 'OUTPUT:' section header. This is a separate "
            "breakage from 228AB822 — the prompt structure changed. "
            "Cannot validate rule ordering without the OUTPUT: anchor."
        ) from e

    # Reuse echo-ban candidates from test 1.
    echo_candidates = [
        "do not echo",
        "do not dump",
        "no file echo",
        "no file dump",
        "do not output file contents",
        "do not paste",
        "no fenced code blocks of edited files",
        "no code blocks of files you edited",
        "do not quote file contents",
        "do not repeat file contents",
        "no file content echo",
        "do not include file contents",
    ]
    echo_indices = [prompt_lower.index(c) for c in echo_candidates if c in prompt_lower]
    assert echo_indices, (
        "No echo-ban phrase found in prompt — test 1 already covers this. "
        "Cannot validate ordering without a match."
    )
    assert min(echo_indices) < output_idx, (
        f"Echo-ban phrase appears at index {min(echo_indices)} but OUTPUT: "
        f"section starts at {output_idx}. Echo-ban must be in the RULES "
        f"region (before OUTPUT:), not inside/after the trailer spec."
    )

    # Reuse narrative-ban strict phrases from test 2.
    narrative_candidates = [
        "no narrative",
        "no prose",
        "no commentary",
        "no preamble",
        "no explanation of changes",
        "do not explain",
        "do not narrate",
        "no explanations",
    ]
    narrative_indices = [prompt_lower.index(c) for c in narrative_candidates if c in prompt_lower]
    assert narrative_indices, (
        "No narrative-ban phrase found in prompt — test 2 already covers this. "
        "Cannot validate ordering without a match."
    )
    assert min(narrative_indices) < output_idx, (
        f"Narrative-ban phrase appears at index {min(narrative_indices)} but "
        f"OUTPUT: section starts at {output_idx}. Narrative-ban must be in "
        f"the RULES region (before OUTPUT:), not inside/after the trailer spec."
    )
