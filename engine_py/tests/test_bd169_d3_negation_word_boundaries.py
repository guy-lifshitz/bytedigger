"""RED tests for bd#169 — the D3 prohibition gate must read negations on word
boundaries.

Spec: docs/decisions/2026-10-02-bd169-d3-negation-word-boundaries.md

UUTs (never mocked): `workflows/phase_45_spec._prohibited_paths` and
`_gate_on_review` (end to end, real spec file under tmp_path).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine.contracts import StepResult


# ─── helpers (copied from test_gh1600_d3_prohibition_gate.py) ──────────────


def _ctx(org_config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(org_config=org_config or {})


def _ship_prev(
    spec_path: str,
    review_path: str = "review.md",
    cycle: int = 1,
    gate_attempts: dict | None = None,
) -> StepResult:
    data = {
        "verdict": "SHIP",
        "review_path": review_path,
        "spec_path": spec_path,
        "cycle": cycle,
        "review_raw": "",
    }
    if gate_attempts is not None:
        data["gate_attempts"] = gate_attempts
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_review_doc")


def _write_spec(tmp_path: Path, name: str, files_body: str) -> str:
    spec_path = tmp_path / name
    spec_path.write_text(
        f"# Test Spec\n\n## Files\n{files_body}\n\n## §3 Acceptance Criteria\n",
        encoding="utf-8",
    )
    return str(spec_path)


# ─── AC1 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task_description",
    [
        "Whenever you edit `src/app.py`, run the tests.",
        "The casino edit touches `src/a.py`.",
        "A piano edit to `src/b.py`.",
        "nobody will edit `src/c.py`",
        "A notable change to `src/d.py`.",
        "nevertheless edit `src/e.py`",
        "do nothing but edit `src/f.py`",
    ],
)
def test_ac1_look_alike_words_are_not_negations(task_description: str) -> None:
    """AC1: look-alike words prohibit nothing. nobody/notable pin today's behaviour."""
    from bytedigger_engine.workflows import phase_45_spec

    result = phase_45_spec._prohibited_paths(task_description)

    assert result == {}, (
        f"AC1 FAIL: {task_description!r} contains no real negation, "
        f"but _prohibited_paths returned {result!r}"
    )


# ─── AC2 ───────────────────────────────────────────────────────────────────


def test_ac2_no_op_stays_unprohibited() -> None:
    """AC2: 'no-op' is not a negation; pins today's behaviour."""
    from bytedigger_engine.workflows import phase_45_spec

    result = phase_45_spec._prohibited_paths("Make a no-op edit to `src/x.py`.")

    assert result == {}, f"AC2 FAIL: 'no-op' must not prohibit, got {result!r}"


# ─── AC3 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task_description, path",
    [
        ("do not touch `src/x.py`", "src/x.py"),
        ("never edit `src/y.py`", "src/y.py"),
        ("Don't modify `src/w.py`.", "src/w.py"),
        ("You must not change `src/v.py`.", "src/v.py"),
        ("DO NOT modify `src/u.py`", "src/u.py"),
    ],
)
def test_ac3_real_negations_still_prohibit(task_description: str, path: str) -> None:
    """AC3: real negations still prohibit; pins today's behaviour."""
    from bytedigger_engine.workflows import phase_45_spec

    result = phase_45_spec._prohibited_paths(task_description)

    assert path in result, (
        f"AC3 FAIL: {task_description!r} must prohibit {path!r}, got {result!r}"
    )


# ─── AC3b ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task_description, path",
    [
        ("no changes to `src/z.py`", "src/z.py"),
        ("no edits to `src/q.py`", "src/q.py"),
        ("no modifications to `src/r.py`", "src/r.py"),
        ("no modification to `src/s.py`", "src/s.py"),
    ],
)
def test_ac3b_noun_forms_prohibit(task_description: str, path: str) -> None:
    """AC3b: noun forms (changes/edits/modification(s)) inside a negation window prohibit; FAILS today."""
    from bytedigger_engine.workflows import phase_45_spec

    result = phase_45_spec._prohibited_paths(task_description)

    assert path in result, (
        f"AC3b FAIL: {task_description!r} must prohibit {path!r}, got {result!r}"
    )


@pytest.mark.parametrize(
    "task_description",
    [
        "do not open the editor for `src/t.py`",
        "never read the changelog `CHANGELOG.md`",
        "do not use the address in `src/addr.py`",
    ],
)
def test_ac3b_whole_word_controls_prohibit_nothing(task_description: str) -> None:
    """AC3b controls: editor/changelog/address are not mutation verbs; pins today's behaviour."""
    from bytedigger_engine.workflows import phase_45_spec

    result = phase_45_spec._prohibited_paths(task_description)

    assert result == {}, (
        f"AC3b FAIL: {task_description!r} has no whole-word mutation verb, got {result!r}"
    )


# ─── AC3c ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task_description",
    [
        "the cache is never written to `src/cache.py`",
        "`total` is never modified in `src/calc.py`",
        "`parse()` never writes the header in `src/p.py`",
        "no tests were added for `src/x.py`",
        "it was never edited in `src/e2.py`",
    ],
)
def test_ac3c_descriptive_negation_stays_unprohibited(task_description: str) -> None:
    """AC3c: descriptive (non-imperative) negation prohibits nothing; pins today's behaviour."""
    from bytedigger_engine.workflows import phase_45_spec

    result = phase_45_spec._prohibited_paths(task_description)

    assert result == {}, (
        f"AC3c FAIL: {task_description!r} is descriptive, not a prohibition, got {result!r}"
    )


# ─── AC4 ───────────────────────────────────────────────────────────────────


def test_ac4_whenever_ship_stands_without_llm_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC4: end to end, 'Whenever ...' + MODIFY src/app.py + SHIP -> ok; LLM seam never hit."""
    from bytedigger_engine.workflows import phase_45_spec

    def _boom(*args, **kwargs):
        raise AssertionError("AC4 FAIL: invoke_llm_subprocess must not be reached by the D3 gate")

    # The name phase_45_spec imports (:82); raising=True fails loud if it disappears.
    monkeypatch.setattr(phase_45_spec, "invoke_llm_subprocess", _boom, raising=True)

    spec_path = _write_spec(tmp_path, "spec_ac4.md", "- MODIFY src/app.py")
    ctx = _ctx({
        "task_description": "Whenever you edit `src/app.py`, run the tests.",
        "complexity": "SIMPLE",
    })
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC4 FAIL: 'Whenever' is not a negation, SHIP must stand; got "
        f"status={result.status!r}, error_code={result.error_code!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ─── AC5 ───────────────────────────────────────────────────────────────────


def test_ac5_real_prohibition_still_revises(tmp_path: Path) -> None:
    """AC5: end to end, 'never edit' + MODIFY src/y.py + SHIP -> REVISE; pins today's behaviour."""
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec_ac5.md", "- MODIFY src/y.py")
    ctx = _ctx({"task_description": "never edit `src/y.py`", "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", f"AC5 FAIL: got status={result.status!r}"
    assert result.error_code == "E_VALIDATION_RETRY", (
        f"AC5 FAIL: got error_code={result.error_code!r}"
    )
