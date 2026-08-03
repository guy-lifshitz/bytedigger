"""RED tests for GH705 — wire the remaining 5 loop-heavy call-sites through
the already-shipped {prompt, stable_prefix} seam (v3, GH334 §2.0, frozen).

Spec: SHARED/memory/Decisions/2026-07-13_GH705_wire_5_callsites_spec.md

The seam itself (invoke_llm_subprocess, per-adapter split) is SHIPPED and
UNCHANGED (#703). This file tests ONLY the 5 builders + their paired
call-sites: each builder must emit `data["stable_prefix"]` (a call-invariant,
`len>500`, `count==1` substring of `data["prompt"]`), and each paired
`_invoke_<site>_llm` must pass `stable_prefix=prev.data.get("stable_prefix", ...)`
to `invoke_llm_subprocess`.

Not-yet-existing symbols (`_VALIDATION_STABLE_PREFIX`, `_SYNTHESIZER_STABLE_PREFIX`,
`_REVIEW_STABLE_PREFIX`, `_SPEC_STABLE_PREFIX`, `_integrity_stable_prefix`) are
NEVER imported at module top — `result.data.get("stable_prefix")` is read from
the returned StepResult only, and the integrity helper is imported/called
INSIDE a test body only, per §1q ext / D1CF5FDF (avoids the non-collectable
RED collection hang).

sys.path is NOT touched here — conftest.py already installs the
conftest-import-time singleton (engine_py root + workflows dir) per §1q/81F97F3D.

§1i: no singleton/time-dependent resource under test. The integrity site's
git dependency is mocked deterministically via `git_port.git_read` (never the
builder UUT itself) — pre-staged, not raced.

Stub-passability: only the DEPENDENCY `git_port.git_read` is patched (for
integrity). No `_build_*_prompt` / `_invoke_*_llm` UUT is ever mocked/patched.

AC-suite (full-suite delta, §1r) is NOT a unit test — verified at ship step 6;
a placeholder xfail notes that instead of faking a pass.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixture helpers (mirrors test_prompt_cache_seam_GH334.py /
# test_phase_5_b4d83b40_red_rubric.py / test_gh530_delta_revise_populate.py)
# ═══════════════════════════════════════════════════════════════════════════


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar") -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    # Pin worktree to a clean non-git dir so review's BUILD SCOPE resolution
    # is fast/deterministic (mirrors GH334's _make_ctx fake_worktree trick).
    fake_worktree = scratchpad.parent / "fake_worktree"
    fake_worktree.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "current_worktree_path": str(fake_worktree),
        },
        question=question,
        session_id="test-gh705",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_injection(scratchpad: Path) -> None:
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")


def _get_sp(result: StepResult) -> str:
    """Extract stable_prefix, forcing pre-GREEN failure (key absent -> None -> falsy)."""
    assert result.status == "ok", f"builder failed: {result.error!r}"
    sp = result.data.get("stable_prefix")
    assert sp, (
        f"stable_prefix must be a non-empty string on the returned StepResult; "
        f"data keys={list(result.data)!r}"
    )
    return sp


def _isolate_fn_body(module, fn_name: str) -> str:
    """Isolate a single top-level function's body from module source (avoids
    false hits from other functions in the same file — deterministic cite-verify,
    not a text-presence stand-in for behavior)."""
    src_path = Path(inspect.getsourcefile(module))
    src = src_path.read_text(encoding="utf-8")
    start = src.index(f"def {fn_name}(")
    end = src.index("\ndef ", start + 1)
    return src[start:end]


# ═══════════════════════════════════════════════════════════════════════════
# Site 1 — validation (phase_5_implement.py)
# ═══════════════════════════════════════════════════════════════════════════


def _build_validation(tmp_path: Path, *, question: str, cycle: int = 1):
    from bytedigger_engine.workflows.phase_5_implement import _build_validation_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad, question=question)
    prev = StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests" / "build-red-output.log"),
            "spec_path": str(scratchpad / "specs" / "build-spec.md"),
            "cycle": cycle,
            "red_test_paths": ["tests/test_foo.py"],
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )
    return _build_validation_prompt(ctx, prev)


def test_AC1v_validation_stable_prefix_call_invariant_long_and_unique_substring(tmp_path):
    r1 = _build_validation(tmp_path / "a", question="Add foo to bar")
    r2 = _build_validation(tmp_path / "b", question="A totally different feature request")
    sp1 = _get_sp(r1)
    sp2 = _get_sp(r2)
    assert sp1 == sp2, "AC1v: validation stable_prefix must be byte-identical across builds"
    assert len(sp1) > 500, f"AC1v: stable_prefix must be substantial (len>500); got {len(sp1)}"
    assert r1.data["prompt"].count(sp1) == 1, (
        f"AC1v: stable_prefix must occur exactly once in prev.data['prompt']; "
        f"got count={r1.data['prompt'].count(sp1)}"
    )


def test_AC2v_validation_prompt_stays_full_and_callsite_wired(tmp_path):
    r = _build_validation(tmp_path, question="Add foo to bar")
    sp = _get_sp(r)
    assert sp in r.data["prompt"], "AC2v: builder must not subtract stable_prefix from prompt"
    from bytedigger_engine.workflows import phase_5_implement as _p5  # noqa: PLC0415
    body = _isolate_fn_body(_p5, "_invoke_validation_llm")
    assert 'stable_prefix=prev.data.get("stable_prefix"' in body, (
        "AC2v: _invoke_validation_llm must pass "
        'stable_prefix=prev.data.get("stable_prefix", ...) to invoke_llm_subprocess'
    )


def test_AC3v_validation_retry_idempotent(tmp_path):
    r_cycle1 = _build_validation(tmp_path / "c1", question="Add foo to bar", cycle=1)
    r_cycle2 = _build_validation(tmp_path / "c2", question="Add foo to bar", cycle=2)
    sp1 = _get_sp(r_cycle1)
    sp2 = _get_sp(r_cycle2)
    assert sp1 == sp2, "AC3v: validation stable_prefix must be identical across cycle 1 vs cycle 2 (retry)"


# ═══════════════════════════════════════════════════════════════════════════
# Site 2 — synthesizer (phase_7_synthesize.py)
# ═══════════════════════════════════════════════════════════════════════════


def _build_synthesizer(tmp_path: Path, *, question: str):
    from bytedigger_engine.workflows.phase_7_synthesize import _build_synthesizer_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad, question=question)
    return _build_synthesizer_prompt(ctx, None)


def test_AC1s_synthesizer_stable_prefix_call_invariant_long_and_unique_substring(tmp_path):
    r1 = _build_synthesizer(tmp_path / "a", question="Add foo to bar")
    r2 = _build_synthesizer(tmp_path / "b", question="A totally different feature request")
    sp1 = _get_sp(r1)
    sp2 = _get_sp(r2)
    assert sp1 == sp2, "AC1s: synthesizer stable_prefix must be byte-identical across builds"
    assert len(sp1) > 500, f"AC1s: stable_prefix must be substantial (len>500); got {len(sp1)}"
    assert r1.data["prompt"].count(sp1) == 1, (
        f"AC1s: stable_prefix must occur exactly once in prev.data['prompt']; "
        f"got count={r1.data['prompt'].count(sp1)}"
    )


def test_AC2s_synthesizer_prompt_stays_full_and_callsite_wired(tmp_path):
    r = _build_synthesizer(tmp_path, question="Add foo to bar")
    sp = _get_sp(r)
    assert sp in r.data["prompt"], "AC2s: builder must not subtract stable_prefix from prompt"
    from bytedigger_engine.workflows import phase_7_synthesize as _p7  # noqa: PLC0415
    body = _isolate_fn_body(_p7, "_invoke_synthesizer_llm")
    assert 'stable_prefix=prev.data.get("stable_prefix"' in body, (
        "AC2s: _invoke_synthesizer_llm must pass "
        'stable_prefix=prev.data.get("stable_prefix", ...) to invoke_llm_subprocess'
    )


# ═══════════════════════════════════════════════════════════════════════════
# Site 3 — review (phase_6_review.py)
# ═══════════════════════════════════════════════════════════════════════════


def _seed_last_findings(scratchpad: Path) -> None:
    reviews = scratchpad / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "last_findings.json").write_text(
        json.dumps({
            "attempt": 2,
            "score": 50,
            "threshold": 80,
            "structured_findings": [
                {"id": "F1", "type": "gap", "evidence": "e", "required_action": "a"},
            ],
        }),
        encoding="utf-8",
    )


def _build_review(tmp_path: Path, *, question: str, with_prior_findings: bool = False):
    from bytedigger_engine.workflows.phase_6_review import _build_review_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad, question=question)
    if with_prior_findings:
        _seed_last_findings(scratchpad)
    return _build_review_prompt(ctx, None)


def test_AC1r_review_stable_prefix_call_invariant_long_and_unique_substring(tmp_path):
    r1 = _build_review(tmp_path / "a", question="Add foo to bar")
    r2 = _build_review(tmp_path / "b", question="A totally different feature request")
    sp1 = _get_sp(r1)
    sp2 = _get_sp(r2)
    assert sp1 == sp2, "AC1r: review stable_prefix must be byte-identical across builds"
    assert len(sp1) > 500, f"AC1r: stable_prefix must be substantial (len>500); got {len(sp1)}"
    assert r1.data["prompt"].count(sp1) == 1, (
        f"AC1r: stable_prefix must occur exactly once in prev.data['prompt']; "
        f"got count={r1.data['prompt'].count(sp1)}"
    )


def test_AC2r_review_prompt_stays_full_and_callsite_wired(tmp_path):
    r = _build_review(tmp_path, question="Add foo to bar")
    sp = _get_sp(r)
    assert sp in r.data["prompt"], "AC2r: builder must not subtract stable_prefix from prompt"
    from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: PLC0415
    body = _isolate_fn_body(_p6, "_invoke_review_llm")
    assert 'stable_prefix=prev.data.get("stable_prefix"' in body, (
        "AC2r: _invoke_review_llm must pass "
        'stable_prefix=prev.data.get("stable_prefix", ...) to invoke_llm_subprocess'
    )


def test_AC3r_review_retry_idempotent_with_prior_findings(tmp_path):
    r_fresh = _build_review(tmp_path / "fresh", question="Add foo to bar", with_prior_findings=False)
    r_reattempt = _build_review(tmp_path / "reattempt", question="Add foo to bar", with_prior_findings=True)
    sp_fresh = _get_sp(r_fresh)
    sp_reattempt = _get_sp(r_reattempt)
    assert sp_fresh == sp_reattempt, (
        "AC3r: review stable_prefix must be identical between a fresh review and a "
        "re-attempt with prior findings present (retry idempotence)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Site 4 — spec_writer (phase_45_spec.py)
# ═══════════════════════════════════════════════════════════════════════════


def _write_prev_spec(scratchpad: Path) -> None:
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH  # noqa: PLC0415

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## Context\nPrevious cycle-1 spec body.\n", encoding="utf-8")


def _build_spec_cycle1(tmp_path: Path, *, question: str, monkeypatch):
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: PLC0415

    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad, question=question)
    return _build_spec_prompt(ctx, None)


def test_AC1p_spec_writer_stable_prefix_call_invariant_long_and_unique_substring(tmp_path, monkeypatch):
    r1 = _build_spec_cycle1(tmp_path / "a", question="Add foo to bar", monkeypatch=monkeypatch)
    r2 = _build_spec_cycle1(tmp_path / "b", question="A totally different feature request", monkeypatch=monkeypatch)
    sp1 = _get_sp(r1)
    sp2 = _get_sp(r2)
    assert sp1 == sp2, "AC1p: spec_writer stable_prefix must be byte-identical across builds"
    assert len(sp1) > 500, f"AC1p: stable_prefix must be substantial (len>500); got {len(sp1)}"
    assert r1.data["prompt"].count(sp1) == 1, (
        f"AC1p: stable_prefix must occur exactly once in prev.data['prompt']; "
        f"got count={r1.data['prompt'].count(sp1)}"
    )


def test_AC2p_spec_writer_prompt_stays_full_and_callsite_wired(tmp_path, monkeypatch):
    r = _build_spec_cycle1(tmp_path, question="Add foo to bar", monkeypatch=monkeypatch)
    sp = _get_sp(r)
    assert sp in r.data["prompt"], "AC2p: builder must not subtract stable_prefix from prompt"
    from bytedigger_engine.workflows import phase_45_spec as _p45  # noqa: PLC0415
    body = _isolate_fn_body(_p45, "_invoke_spec_llm")
    assert 'stable_prefix=prev.data.get("stable_prefix"' in body, (
        "AC2p: _invoke_spec_llm must pass "
        'stable_prefix=prev.data.get("stable_prefix", ...) to invoke_llm_subprocess'
    )


def test_AC3p_spec_writer_delta_branches_leave_stable_prefix_unset(tmp_path, monkeypatch):
    """Both cycle>=2 early-return branches (surgical-revise, delta-retry) must
    leave stable_prefix UNSET (inert / back-compat)."""
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: PLC0415

    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    _write_prev_spec(scratchpad)
    ctx = _make_ctx(scratchpad, question="Add foo to bar")

    structured_findings = [
        {"id": "T1", "type": "gap", "evidence": "e1", "required_action": "do the thing"},
    ]

    # Branch A: surgical-revise (surgical_fallback not set -> surgical path taken)
    prev_surgical = {
        "cycle": 2,
        "findings": "some reviewer findings text",
        "structured_findings": structured_findings,
    }
    r_surgical = _build_spec_prompt(ctx, prev_surgical)
    assert isinstance(r_surgical.data, dict)
    assert r_surgical.data.get("delta_retry") is True, (
        "fixture precondition: surgical-revise branch must set delta_retry=True"
    )
    assert r_surgical.data.get("stable_prefix", "") == "", (
        "AC3p: surgical-revise early-return must leave stable_prefix UNSET; "
        f"got {r_surgical.data.get('stable_prefix')!r}"
    )

    # Branch B: delta-retry (force surgical_fallback=True to skip the surgical branch)
    prev_delta = {
        "cycle": 2,
        "findings": "some reviewer findings text",
        "structured_findings": structured_findings,
        "surgical_fallback": True,
    }
    r_delta = _build_spec_prompt(ctx, prev_delta)
    assert isinstance(r_delta.data, dict)
    assert r_delta.data.get("delta_retry") is True, (
        "fixture precondition: delta-retry branch must set delta_retry=True"
    )
    assert r_delta.data.get("stable_prefix", "") == "", (
        "AC3p: delta-retry early-return must leave stable_prefix UNSET; "
        f"got {r_delta.data.get('stable_prefix')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Site 5 — integrity (phase_5_integrity.py)
# ═══════════════════════════════════════════════════════════════════════════


def _build_integrity(tmp_path: Path, *, question: str, diff_text: str):
    from bytedigger_engine.workflows.phase_5_integrity import _build_integrity_prompt  # noqa: PLC0415
    from bytedigger_engine.lib.git_port import GitResult  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad, question=question)
    fake_result = GitResult(returncode=0, stdout=diff_text, stderr="", timed_out=False)
    # Mock the DEPENDENCY (git_port.git_read), never the builder UUT.
    with patch("bytedigger_engine.workflows.phase_5_integrity.git_port.git_read", return_value=fake_result):
        return _build_integrity_prompt(ctx, None)


_FAKE_DIFF_1 = (
    "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/tests/test_foo.py\n"
    "+++ b/tests/test_foo.py\n"
    "@@ -1,3 +1,3 @@\n"
    "-assert x == 1\n"
    "+assert x == 2\n"
)

_FAKE_DIFF_2 = (
    "diff --git a/tests/test_bar.py b/tests/test_bar.py\n"
    "index 3333333..4444444 100644\n"
    "--- a/tests/test_bar.py\n"
    "+++ b/tests/test_bar.py\n"
    "@@ -5,2 +5,2 @@\n"
    "-assert y == 5\n"
    "+assert y == 6\n"
)


def test_AC1i_integrity_helper_call_invariant_long_and_unique_substring(tmp_path):
    r1 = _build_integrity(tmp_path / "a", question="Add foo to bar", diff_text=_FAKE_DIFF_1)
    r2 = _build_integrity(tmp_path / "b", question="A totally different feature request", diff_text=_FAKE_DIFF_2)
    sp1 = _get_sp(r1)
    sp2 = _get_sp(r2)
    assert sp1 == sp2, "AC1i: integrity stable_prefix must be byte-identical across builds"
    assert len(sp1) > 500, f"AC1i: stable_prefix must be substantial (len>500); got {len(sp1)}"
    assert r1.data["prompt"].count(sp1) == 1, (
        f"AC1i: stable_prefix must occur exactly once in prev.data['prompt']; "
        f"got count={r1.data['prompt'].count(sp1)}"
    )
    from bytedigger_engine.workflows import phase_5_integrity as _p5i  # noqa: PLC0415
    assert callable(getattr(_p5i, "_integrity_stable_prefix", None)), (
        "AC1i: phase_5_integrity._integrity_stable_prefix must be a callable, zero-arg helper"
    )
    assert _p5i._integrity_stable_prefix() == sp1, (
        "AC1i: data['stable_prefix'] must equal _integrity_stable_prefix() output"
    )


def test_AC2i_integrity_prompt_stays_full_and_callsite_wired(tmp_path):
    r = _build_integrity(tmp_path, question="Add foo to bar", diff_text=_FAKE_DIFF_1)
    sp = _get_sp(r)
    assert sp in r.data["prompt"], "AC2i: builder must not subtract stable_prefix from prompt"
    from bytedigger_engine.workflows import phase_5_integrity as _p5i  # noqa: PLC0415
    body = _isolate_fn_body(_p5i, "_invoke_integrity_llm")
    assert 'stable_prefix=prev.data.get("stable_prefix"' in body, (
        "AC2i: _invoke_integrity_llm must pass "
        'stable_prefix=prev.data.get("stable_prefix", ...) to invoke_llm_subprocess'
    )


def test_AC4i_integrity_no_changes_leaves_stable_prefix_unset(tmp_path):
    from bytedigger_engine.workflows.phase_5_integrity import VERDICT_NO_CHANGES  # noqa: PLC0415

    r = _build_integrity(tmp_path, question="Add foo to bar", diff_text="")
    assert r.data.get("verdict_override") == VERDICT_NO_CHANGES, (
        "fixture precondition: empty diff must set verdict_override=NO_CHANGES"
    )
    assert r.data.get("prompt") is None, "fixture precondition: NO_CHANGES must leave prompt=None"
    assert r.data.get("stable_prefix", "") == "", (
        "AC4i: NO_CHANGES override must leave stable_prefix UNSET; "
        f"got {r.data.get('stable_prefix')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC-suite — full-suite delta is a ship-step-6 verification gate, not a unit test.
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.xfail(
    reason="AC-suite (zero new full-suite failures, §1r delta math) is verified at "
    "ship step 6 (full pytest + bun test), not as a unit test. Placeholder only.",
    strict=False,
)
def test_AC_suite_placeholder_full_suite_delta_verified_at_ship_step_6():
    pytest.skip("AC-suite is a full-suite verify-step gate — see ship checklist step 6.")
