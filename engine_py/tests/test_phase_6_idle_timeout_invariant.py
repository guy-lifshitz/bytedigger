"""RED test for 6923B6AC D1 — callsite invariant.

Asserts that NO ``invoke_llm_subprocess(...)`` (or
``invoke_llm_subprocess_with_stdin(...)``) call site in
``phase_6_review.py`` passes ``idle_timeout_sec=`` with a non-None value.

Background:
  - 775D6752 wired ``idle_timeout_sec=60`` into the two fix_llm call sites
    (line 1451 primary, line 1557 retry). BB8BFEFE run
    ``forge-1778192708-e2e3e40b`` cycle 1 proved that 60s is too aggressive:
    legitimate tool-use sessions inside a 77s LLM run hit 60+s
    inter-event gaps and got SIGKILLed mid-stream despite active output
    (response_size_bytes=116020).
  - 6923B6AC RC fix: remove ``idle_timeout_sec=60`` from both call sites,
    aligning fix_llm with the other 11 LLM call sites in engine_py which
    use the default ``idle_timeout_sec=None`` (disabled). Outer
    ``timeout_sec`` continues to protect against genuine hangs.

This test pins the invariant via AST. Catches accidental re-introduction
by future RED/GREEN agents or refactors. Plain string match would not
detect e.g. ``idle_timeout_sec=int(60)`` or
``idle_timeout_sec=DEFAULT_IDLE`` — AST walks Call nodes and inspects the
keyword binding directly.

Pre-GREEN (current code): FAILS — ``idle_timeout_sec=60`` still present
at lines 1451 + 1557.
Post-GREEN: PASSES — both call sites either omit the kwarg OR pass
``idle_timeout_sec=None``.
"""
from __future__ import annotations

import ast
from pathlib import Path

PHASE_6_REVIEW = (
    Path(__file__).parent.parent / "workflows" / "phase_6_review.py"
)

# Function names whose call sites we audit. Both flavours are listed for
# forward-compat: the codebase currently only uses invoke_llm_subprocess,
# but invoke_llm_subprocess_with_stdin appears in spec language and may
# land later. Keeping both keeps the invariant narrow-but-resilient.
_TARGET_CALL_NAMES = {
    "invoke_llm_subprocess",
    "invoke_llm_subprocess_with_stdin",
}


def _is_none_literal(node: ast.AST) -> bool:
    """True iff node is the literal ``None``."""
    return isinstance(node, ast.Constant) and node.value is None


def _callable_name(call: ast.Call) -> str | None:
    """Return the bare attribute/identifier name being called.

    For ``invoke_llm_subprocess(...)`` returns ``"invoke_llm_subprocess"``.
    For ``mod.invoke_llm_subprocess(...)`` returns ``"invoke_llm_subprocess"``.
    Returns None for indirect calls (e.g. ``getattr(...)(...)`` ).
    """
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _find_offending_callsites() -> list[tuple[int, str]]:
    """Walk phase_6_review.py AST, return [(lineno, repr_of_value), ...]
    for every offending call site (target callable + non-None
    idle_timeout_sec).
    """
    src = PHASE_6_REVIEW.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(PHASE_6_REVIEW))
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callable_name(node)
        if name not in _TARGET_CALL_NAMES:
            continue
        for kw in node.keywords:
            if kw.arg != "idle_timeout_sec":
                continue
            # Treat literal None as compliant; everything else (including
            # numeric literals, identifiers, attribute accesses, function
            # calls that compute a value) is non-compliant.
            if _is_none_literal(kw.value):
                continue
            offenders.append((node.lineno, ast.unparse(kw.value)))
    return offenders


def test_no_phase_6_callsite_sets_idle_timeout_sec_to_non_none():
    """RED for 6923B6AC D1: phase_6_review.py must not pin idle_timeout_sec
    to a non-None value at any LLM call site.

    Pre-GREEN: 2 offenders at lines 1451 + 1557 (both ``idle_timeout_sec=60``).
    Post-GREEN: 0 offenders — both lines either delete the kwarg or set
    it to None.
    """
    assert PHASE_6_REVIEW.exists(), (
        "6923B6AC D1: cannot locate phase_6_review.py at expected path "
        + str(PHASE_6_REVIEW)
    )
    offenders = _find_offending_callsites()
    assert offenders == [], (
        "6923B6AC D1: phase_6_review.py LLM call sites must NOT pin "
        "idle_timeout_sec to a non-None value (RC fix per BB8BFEFE: "
        "60s threshold false-positives on legitimate tool-use sessions "
        "with 60+s inter-event gaps; outer timeout_sec is sufficient). "
        "Offending call sites (line, value): "
        + repr(offenders)
        + " — remove the kwarg OR set to None."
    )
