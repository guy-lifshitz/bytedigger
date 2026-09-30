"""RED tests for GH1674 — missing injection files masquerade as a substantive REVISE.

Spec (frozen 2026-08-16, §1V [2026-08-19] is the FINAL, NARROWED form):
    SHARED/memory/Decisions/2026-08-16_gh1674_injection_missing_masquerades_as_verdict.md
Agreement: BA2DA8B1-62AF-459D-BCA8-FD2FD0275FAC

Class K-INFRA-AS-VERDICT: an INFRASTRUCTURE failure (no input files) leaves the
engine wearing the SUBJECT's error code (`E_REVIEW_FAILED` — "the reviewer
disagrees with the spec"), so the two are indistinguishable by error code.

§1V DECISION (2026-08-19): this lot is NARROWED to chokepoint (B) —
CLASSIFICATION only. Chokepoint (A) — DETECTION, the guard placed inside the
23 dispatch pairs that call `invoke_llm_subprocess`, so a run is never spent
at all on missing inputs — is OUT OF SCOPE here and moves to hal#1701,
whose spec is §1O..§1U of this same decision file. Measured collateral for
(A) at the time of the split: 0 existing tests reference both a
`_gate_on_review`/`_gate_on_validation` call AND a block-shaped worker
answer, so narrowing to (B) changes the verdict of nothing already asserted
here (§1V-1). Everything below that belonged to chokepoint (A) — the
per-builder guard-call form, the 23-pair dispatch census, the dominance
lint, the no-dispatch-branch fences, the retry/entry-point census — is
DELETED FROM THIS FILE, not weakened or skipped; #1701 recovers it from this
branch's git history.

**KNOWN LIMIT, stated here and in the spec's §6/§1V-4 (do not "fix" this in
GREEN for this lot):** chokepoint (B) restores the DIAGNOSIS but does not
prevent the SPEND — the worker is still invoked, the prompt is still built,
a run is still spent on missing inputs; only the error code surfacing the
failure changes from `E_REVIEW_FAILED`/`E_VALIDATION_FAILED` to
`E_INJECTION_MISSING`. Closing that spend is chokepoint (A), #1701.

Chokepoint (B) — classification, asserted on all THREE gate tiers:
  * FEATURE/COMPLEX — `_gate_on_review` (workflows/phase_45_spec.py:4440+),
    AC1-AC4, AC6, AC11, AC12, AC21.
  * SIMPLE — its own `_gate_on_review` twin (workflows/phase_45_spec_lite.py
    :974), AC18.
  * VALIDATION — `_gate_on_validation` (workflows/phase_5_implement.py:7626),
    AC24.
Each tier gets a block-recognition branch placed BESIDE its existing
non-verdict terminal precedent (`E_REVIEW_UNPARSEABLE` on the two review
tiers, phase_45_spec.py:4466-4491), which AC4 fences as untouched.

§1W (gate verdict 2026-08-19, REJECTED with six MAJOR — all inside scope)
adds, on top of the ten ACs above:
  * §1W-1 AC1 is the DIRECT `require_injection_files` contract, not an
    engine-level run (the "worker never invoked" arm is chokepoint (A), #1701);
  * §1W-2 BELOW-cap arms on SIMPLE (AC18 arm E) and VALIDATION (AC24 arm E) —
    at `cycle < cap` both gates return status="ok" and RE-ENTER, spending a
    second run on the same absent inputs;
  * §1W-3 a PASS fence on the VALIDATION tier (AC21's missing twin);
  * §1W-4 a ctx-shape fence — 10 modules / 29 sites call these gates as
    `_gate_on_*(None, …)`;
  * §1W-6 the SECOND CONJUNCT's predicate: directory PRESENT, `hal-memory.md`
    whitespace-only, worker declared the block ⇒ still E_INJECTION_MISSING.
    Without it `not injection_dir(sp).exists()` greens every other row while
    leaving the measured incident shape uncovered;
  * §1W-7 `recoverable is False` pinned on the infra-block terminals.

Gate round 18 (2026-08-21, FAIL on one MAJOR) adds the MIRROR of §1W-6 — the
FIRST conjunct's predicate. Every text decoy stood beside a COMPLETE injection
dir, where a conjunctive classifier never evaluates the text at all, so three
weak GREENs (`"block" in raw`, `len(raw) < 60`, `verdict == UNKNOWN`) passed
all thirteen tests. `test_r18_first_conjunct_needs_a_declared_block_not_words_
that_look_like_one` feeds substantive answers WITH the inputs absent and
requires the ordinary terminal on all three tiers. Its MINORs: the strip
contract now has arms on SIMPLE and VALIDATION too (AC18/AC24 arm F), FEATURE
gets its below-cap arm (AC3 arm C), and `recoverable` is pinned on AC18/AC24
arm A.

§1q (assert-time RED, no collection-time break): every symbol that does NOT
exist yet — `injection_dir`, `require_injection_files`, `E_INJECTION_MISSING`
— is reached through a guarded `getattr` / a subprocess / a filesystem probe
inside the test body. Nothing new is imported at module scope, so this file
COLLECTS today and FAILS at assert time.

§1q / 81F97F3D: no module-level `sys.path` mutation and no `from conftest
import`. `tests/conftest.py` already puts engine_py, engine_py/bytedigger_engine/workflows and
engine_py/bytedigger_engine/lib on `sys.path` at conftest-import time (conftest.py:23-28).

§1l / §1y production side-effect anchors: after §1W-1 the engine-level host
left this lot together with chokepoint (A) — the "worker was never invoked"
arm is un-greenable inside §1V-2's scope (satisfying it needs a pre-dispatch
guard in `phase_5_integrity.py`, a module this lot does not touch), so AC1 is
now the DIRECT contract assert on `require_injection_files` that §3/AC1 pins.
Every other AC still runs a REAL, unmocked unit under test:
AC3/AC4/AC11/AC12/AC18/AC21 the REAL `_gate_on_review` (FEATURE/COMPLEX and
SIMPLE tiers); AC24, AC24-PASS and the ctx-shape fence the REAL
`_gate_on_validation` (`phase_5_implement.py`), chokepoint (B)'s third
instance. AC11 anchors a real production side-effect — the durable REVISE
counter on disk. Nothing is mocked anywhere in this file; no LLM transport is
registered at all, because no gate invokes a worker.

§1i (pre-staged, never raced): every fixture below pre-stages the contested
state — the injection directory, its files, the git repo — deterministically
on disk BEFORE the unit under test is invoked. No timing, no sleeps, no
singleton contention.

§1s: this file adds no production code and edits no other test file.

Interface pinned by this RED (the spec fixes the behaviour, not the spelling;
GREEN must match these names):
  * `phase_workflows_common.injection_dir(scratchpad: Path) -> Path`
  * `phase_workflows_common.require_injection_files(scratchpad: Path) -> str | None`
        None when every file is present and non-empty; otherwise a message
        naming the absolute path (missing dir) or the offending file name.
        Empty is defined as empty AFTER `strip()` (§4.1/§1H) — a
        whitespace-only file counts as missing too (AC2).
  * error code string `E_INJECTION_MISSING`, registered in `error_codes.py`
    with `ERROR_CODES.md` regenerated (AC6).

The `K-INFRA-AS-VERDICT` framing above and the instrument-vs-subject
discipline in each docstring below (fence vs subject-red, decoy vs real
finding) carry over unchanged from the wider lot; only the chokepoint-(A)
material (dispatchers, dominance, the lint, the 23-pair census, no-dispatch
branches) has been removed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ENGINE_PY_ROOT = HERE.parent
ERROR_CODES_MD = ENGINE_PY_ROOT / "bytedigger_engine/ERROR_CODES.md"
ERROR_CODES_PY = ENGINE_PY_ROOT / "bytedigger_engine/error_codes.py"

CODE = "E_INJECTION_MISSING"

# The five READ_FIRST files named by `_read_first_block`
# (phase_workflows_common.py:463-474).
INJECTION_FILES = (
    "hal-memory.md",
    "constitution.md",
    "quality-gate.md",
    "producer-rules.md",
    "active-work.md",
)

# The block a READ_FIRST worker emits when the injection files are absent —
# verbatim from the prompt prose at phase_workflows_common.py:472-473. It is
# NON-EMPTY, which is exactly why the `E_REVIEW_UNPARSEABLE` branch cannot
# catch it and it falls through to the verdict axis (spec §1).
BLOCKED_WORKER_OUTPUT = (
    "STATUS=block\n"
    "SUMMARY: injection files missing\n"
)

# A genuine, substantive REVISE — the second arm of AC3. Nothing about this
# response is infrastructural; it must keep its old terminal code.
GENUINE_REVISE_OUTPUT = (
    "## Verdict\n"
    "REVISE\n"
    "## Findings\n"
    "1. AC3 has no forcing function — the spec asserts prose, not behaviour.\n"
    "2. The chokepoint named in §2 is not the one the fix edits.\n"
)

# §1C-2 / AC12 arm (a) — the TEXT decoy. This sentence is verbatim prompt prose
# (phase_workflows_common.py:472-473), so it appears in EVERY READ_FIRST prompt
# a worker is handed; a reviewer who quotes it while genuinely disagreeing is
# the ordinary case, not a contrived one. A classifier keying on the substring
# alone reads this substantive answer as an infrastructure block.
QUOTING_BUT_SUBSTANTIVE_OUTPUT = (
    "## Verdict\n"
    "REVISE\n"
    "## Findings\n"
    "1. The prompt instructs the worker: \"If any file is missing or empty: "
    "orchestrator Phase 0.5 failed — STATUS=block with SUMMARY 'injection files "
    "missing'.\" That instruction is prose only; nothing in the engine checks it,\n"
    "   so §4 of this spec cannot claim the rule is enforced today.\n"
    "2. AC2's forcing function is absent: the fixture never exercises the empty-file\n"
    "   path, so the acceptance criterion is unasserted.\n"
)


# ─── fixtures (all pre-staged, §1i) ──────────────────────────────────────────


def _seed_injection(scratchpad: Path, *, empty: str | None = None) -> Path:
    """Pre-stage a COMPLETE injection dir. `empty` names one file to truncate
    to zero length (AC2's "present but empty counts as missing")."""
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in INJECTION_FILES:
        body = "" if name == empty else f"# {name}\nreal content for {name}\n"
        (inj / name).write_text(body, encoding="utf-8")
    # security-rules.md is a sixth file some carriers list; seed it too so no
    # carrier's own list can be the reason a test reddens.
    (inj / "security-rules.md").write_text("# security-rules\ncontent\n", encoding="utf-8")
    return inj


def _make_ctx(scratchpad: Path, **org_extra: Any) -> Any:
    from bytedigger_engine.contracts import WorkflowContext

    org: dict[str, Any] = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-gh1674",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_injection_whitespace(scratchpad: Path, *, name: str = "hal-memory.md") -> Path:
    """§1aa named helper — a COMPLETE injection dir whose `name` holds ONLY
    whitespace. `st_size > 0`, so any size-based check waves it through; §4.1
    defines empty as empty AFTER `strip()`. This is the state CLOSEST TO THE
    MEASURED INCIDENT (§1W-6): the directory survived, one file did not."""
    inj = _seed_injection(scratchpad)
    (inj / name).write_text("   \n\t \n  \n", encoding="utf-8")
    assert (inj / name).stat().st_size > 0, (
        f"{name} must be NON-ZERO length here, otherwise this fixture collapses "
        f"into the plain zero-length one and proves nothing about strip()"
    )
    return inj


def _require_injection_files() -> Any:
    """§1q guarded reach: the symbol does not exist today."""
    from bytedigger_engine.workflows import phase_workflows_common as pwc

    fn = getattr(pwc, "require_injection_files", None)
    assert callable(fn), (
        "require_injection_files() is not implemented in "
        "workflows/phase_workflows_common.py (spec §4.1)"
    )
    return fn


def _injection_dir_helper() -> Any:
    from bytedigger_engine.workflows import phase_workflows_common as pwc

    fn = getattr(pwc, "injection_dir", None)
    assert callable(fn), (
        "injection_dir() is not implemented in "
        "workflows/phase_workflows_common.py (spec §4.1)"
    )
    return fn


# r18 MAJOR-1, candidate (2) — the "too little text ⇒ infrastructure block"
# classifier. A reviewer whose whole finding fits on one line is ordinary; the
# two answers below are SHORTER than BLOCKED_WORKER_OUTPUT, so a length
# predicate fires on them, and they carry no block declaration whatsoever.
TERSE_REVISE_OUTPUT = (
    "## Verdict\n"
    "REVISE\n"
    "AC3 is unforced.\n"
)

TERSE_VALIDATION_FAIL_OUTPUT = (
    "## Verdict\n"
    "FAIL\n"
    "AC3 is unforced.\n"
)

# A genuine, substantive validation FAIL — the VALIDATION tier's counterpart of
# GENUINE_REVISE_OUTPUT. Declares no block of any kind.
GENUINE_VALIDATION_FAIL_OUTPUT = (
    "## Verdict\nFAIL\n## Gaps\n"
    "1. AC3 has no forcing function — the spec asserts prose, not behaviour.\n"
)

# A LEGITIMATE, terse PASS. Short on purpose: §1W-3's named failure is a GREEN
# of the shape "too little text ⇒ infrastructure block" sitting at the top of
# `_gate_on_validation`, which would abort a build that PASSED.
GENUINE_VALIDATION_PASS_OUTPUT = "## Verdict\nPASS\n"


def _validation_prev(scratchpad: Path, *, raw: str, cycle: int = 2,
                     verdict: str | None = None) -> Any:
    """§1aa named helper — prev StepResult for `_gate_on_validation`.

    `cycle` defaults to 2 == MAX_VALIDATION_CYCLES, where the terminal branch
    fires (phase_5_implement.py:7792); `cycle=1` reaches the BELOW-cap re-entry
    at :7763. `verdict` defaults to UNKNOWN (the gate treats it as FAIL); pass
    `VERDICT_PASS` to reach the PASS path at :7806."""
    from bytedigger_engine.workflows import phase_5_implement as p5
    from bytedigger_engine.contracts import StepResult

    scratchpad.mkdir(parents=True, exist_ok=True)
    doc = scratchpad / "validation.md"
    doc.write_text(raw, encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "verdict": verdict if verdict is not None else p5.VERDICT_UNKNOWN,
            "cycle": cycle,
            "validation_doc_path": str(doc),
            "validation_raw": raw,
            "spec_path": str(scratchpad / "build-spec.md"),
            "red_log_path": str(scratchpad / "build-red-output-cycle-1.log"),
        },
        duration_ms=0,
        step_name="run_validator",
    )


def _make_gate_prev(tmp_path: Path, *, raw_review: str, cycle: int = 2,
                    verdict: str | None = None, at_cap: bool = True) -> Any:
    """prev StepResult for `_gate_on_review`. `at_cap=True` pins
    gate_attempts.spec_retry high enough that the REVISE branch TERMINATES
    (deterministic — no reliance on cycle bookkeeping, §1i)."""
    from bytedigger_engine.contracts import StepResult
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_UNKNOWN

    review_path = tmp_path / "build-plan-review.md"
    review_path.write_text(raw_review, encoding="utf-8")
    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text("## Context\nstub\n", encoding="utf-8")
    data: dict[str, Any] = {
        "verdict": verdict if verdict is not None else VERDICT_UNKNOWN,
        "cycle": cycle,
        "review_path": str(review_path),
        "spec_path": str(spec_path),
        "review_raw": raw_review,
    }
    if at_cap:
        data["gate_attempts"] = {"spec_retry": 99, "spec_review": 99}  # bytedigger: bd#85 spends the REVISE branch under "spec_review"
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_review_doc")


# ─── AC1 — the precondition catches a MISSING directory and names the
#          ABSOLUTE path it expected (§1W-1: the DIRECT helper contract; the
#          "worker was never invoked" arm belongs to chokepoint (A), #1701).


def test_ac1_missing_injection_dir_is_reported_with_the_absolute_path(tmp_path):
    """AC1 (as narrowed by §1W-1): `injection/` absent ⇒
    `require_injection_files(scratchpad)` returns a NON-EMPTY message carrying
    the ABSOLUTE path of the directory it expected.

    Why the absolute path and not just "missing": the whole class this lot
    closes is a diagnosis that cannot be acted on. A message saying "injection
    files missing" repeats exactly what the worker already said in the measured
    incident (§1); a message carrying `/…/scratch/injection` tells the operator
    WHICH scratchpad lost its inputs, which is the fact the two lost ppba runs
    needed and did not get.

    FAILS TODAY: `require_injection_files` does not exist at all in
    `workflows/phase_workflows_common.py` — the rule lives only as prompt prose
    (phase_workflows_common.py:472-473).

    Instrument (non-inert): the same helper must return None on a COMPLETE dir,
    otherwise "non-empty message" proves nothing about which condition fired.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    expected_inj = scratchpad / "injection"
    assert not expected_inj.exists(), "AC1 precondition: injection/ must be absent"
    assert expected_inj.is_absolute(), "AC1 precondition: tmp_path is absolute"

    msg = _require_injection_files()(scratchpad)

    assert msg is not None, (
        "AC1 FAIL: a missing injection/ directory must be reported, got None"
    )
    assert isinstance(msg, str) and msg.strip(), (
        f"AC1 FAIL: the report must be a NON-EMPTY message, got {msg!r}"
    )
    assert str(expected_inj) in msg, (
        f"AC1 FAIL: the message must name the ABSOLUTE expected path "
        f"{str(expected_inj)!r} — a bare 'injection files missing' repeats what the "
        f"worker already said and is not actionable; got {msg!r}"
    )
    # §1g: the path the message names is the one the shared helper hands out.
    assert _injection_dir_helper()(scratchpad) == expected_inj, (
        "AC1 FAIL: injection_dir() must resolve to the very path the message names"
    )
    # Non-inert instrument.
    clean = tmp_path / "clean"
    _seed_injection(clean)
    assert _require_injection_files()(clean) is None, (
        "AC1 FAIL (instrument): a complete, non-empty injection dir must return None — "
        "a helper that always reports would satisfy the assertions above vacuously"
    )


# ─── AC2 — an EMPTY file counts as missing, and the message names THAT file.


def test_ac2_empty_injection_file_is_treated_as_missing_and_named(tmp_path):
    """AC2: directory present, all five files present, ONE of them zero-length
    ⇒ the same block, and the message names exactly that file. The prose
    already says "missing OR empty"; the code must say the same.

    FAILS TODAY: `require_injection_files` does not exist at all.
    """
    scratchpad = tmp_path / "scratch"
    inj = _seed_injection(scratchpad, empty="constitution.md")
    assert (inj / "constitution.md").stat().st_size == 0, "AC2 precondition: file must be empty"
    for name in INJECTION_FILES:
        if name != "constitution.md":
            assert (inj / name).stat().st_size > 0, "AC2 precondition: siblings must be non-empty"

    msg = _require_injection_files()(scratchpad)

    assert msg is not None, (
        "AC2 FAIL: a zero-length injection file must be reported as missing, got None"
    )
    assert "constitution.md" in msg, (
        f"AC2 FAIL: the message must name the offending file 'constitution.md', got {msg!r}"
    )
    # Non-inert instrument: the same helper must return None on a clean dir,
    # otherwise "not None" proves nothing about WHICH condition fired.
    clean = tmp_path / "clean"
    _seed_injection(clean)
    assert _require_injection_files()(clean) is None, (
        "AC2 FAIL: a complete, non-empty injection dir must return None"
    )
    # §1g: the path the precondition guards is the one the helper hands out.
    assert _injection_dir_helper()(scratchpad) == inj

    # §1H whitespace arm: §4.1 now defines empty as "empty AFTER strip()". A
    # file of ONLY whitespace has st_size > 0, so a naive `st_size > 0` check
    # would pass it through — the same class of "proza requires, code does
    # not" bug the whole lot exists to close. It must be treated as missing,
    # exactly like the zero-length arm above, and named the same way.
    ws_scratchpad = tmp_path / "scratch_ws"
    _seed_injection_whitespace(ws_scratchpad)  # asserts st_size > 0 itself
    msg_ws = _require_injection_files()(ws_scratchpad)
    assert msg_ws is not None, (
        "AC2 FAIL: a whitespace-only injection file must be treated as missing "
        "(§4.1: empty AFTER strip()), got None"
    )
    assert "hal-memory.md" in msg_ws, (
        f"AC2 FAIL: the message must name the offending file 'hal-memory.md', got {msg_ws!r}"
    )


# ─── AC3 — the SUBJECT AC (§1l): the code is distinguishable from a
#          substantive REVISE. BOTH arms mandatory: without the second, a
#          rename-only "fix" passes.


def test_ac3_infra_block_and_genuine_revise_get_different_terminal_codes(tmp_path):
    """AC3 arm A: a run whose `injection/` is absent, whose worker therefore
    answered `STATUS=block / SUMMARY: injection files missing`, must terminate
    with E_INJECTION_MISSING — NOT E_REVIEW_FAILED.

    AC3 arm B: the SAME gate, with `injection/` complete and a GENUINE REVISE
    at cap, must still terminate with E_REVIEW_FAILED.

    AC3 arm C (r18 MINOR-2): the same infrastructure block BELOW cap must
    terminate too, not re-enter — the FEATURE counterpart of AC18/AC24 arm E.
    Arm C FAILS TODAY as E_VALIDATION_RETRY / recoverable=True.

    Arm A FAILS TODAY (the non-empty block answer escapes the
    E_REVIEW_UNPARSEABLE branch, falls into "everything else" and surfaces as
    E_REVIEW_FAILED — spec §1). Arm B passes today and is the fence that stops
    GREEN from simply renaming the terminal.
    """
    from bytedigger_engine.workflows.phase_45_spec import _gate_on_review

    # ── arm A: infrastructure block, injection/ absent ──
    sp_a = tmp_path / "scratch_a"
    sp_a.mkdir()
    assert not (sp_a / "injection").exists(), "AC3 arm A precondition: injection/ absent"
    res_a = _gate_on_review(
        _make_ctx(sp_a, complexity="FEATURE"),
        _make_gate_prev(sp_a, raw_review=BLOCKED_WORKER_OUTPUT),
    )
    assert res_a.error_code == CODE, (
        f"AC3 arm A FAIL: an infrastructure block must terminate as {CODE!r}, got "
        f"{res_a.error_code!r} — indistinguishable from reviewer disagreement "
        f"(error={res_a.error!r})"
    )
    # §1W-7: recoverable=True would send the engine back to step 0 and spend a
    # SECOND run on the same absent inputs — the exact two-run cost measured in
    # §1. The classification is worthless if the terminal is not terminal.
    assert res_a.recoverable is False, (
        f"AC3 arm A FAIL: an infrastructure block is TERMINAL — with "
        f"recoverable={res_a.recoverable!r} the engine retries from step 0 and "
        f"spends another run on inputs that are still absent"
    )

    # ── arm B: genuine REVISE at cap, injection/ complete ──
    sp_b = tmp_path / "scratch_b"
    _seed_injection(sp_b)
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_REVISE

    res_b = _gate_on_review(
        _make_ctx(sp_b, complexity="FEATURE"),
        _make_gate_prev(sp_b, raw_review=GENUINE_REVISE_OUTPUT, verdict=VERDICT_REVISE),
    )
    assert res_b.error_code == "E_REVIEW_FAILED", (
        f"AC3 arm B FAIL: a genuine REVISE at cap must STILL be E_REVIEW_FAILED "
        f"(a rename-only fix is not a fix), got {res_b.error_code!r} "
        f"(error={res_b.error!r})"
    )

    # ── arm C (r18 MINOR-2): BELOW cap on the FEATURE tier ──
    # AC18/AC24 got their below-cap arms in §1W-2; FEATURE did not. AC11 forces
    # the new branch ABOVE phase_45_spec.py:4563, which makes re-entry unlikely
    # structurally — but nothing ASSERTS it, so a GREEN that conditions the
    # branch on `attempts >= pol.cycle_cap` (:4654) passes AC3, AC11 and §1W-6
    # and still re-enters, spending a second run on the same absent inputs.
    sp_c = tmp_path / "scratch_c"
    sp_c.mkdir()
    assert not (sp_c / "injection").exists(), "AC3 arm C precondition: injection/ absent"
    prev_c = _make_gate_prev(sp_c, raw_review=BLOCKED_WORKER_OUTPUT, cycle=1, at_cap=False)
    assert "gate_attempts" not in prev_c.data and prev_c.data["cycle"] == 1, (
        "AC3 arm C precondition: NO prior gate spend and cycle 1 — otherwise the "
        "fixture sits at cap and duplicates arm A"
    )
    res_c = _gate_on_review(_make_ctx(sp_c, complexity="FEATURE"), prev_c)
    assert res_c.error_code == CODE, (
        f"AC3 arm C FAIL: an infrastructure block on the FIRST cycle must TERMINATE "
        f"as {CODE!r}; today the gate answers E_VALIDATION_RETRY and the engine "
        f"re-enters from step 0. Got {res_c.error_code!r} (status={res_c.status!r} "
        f"error={res_c.error!r})"
    )
    assert res_c.recoverable is False, (
        f"AC3 arm C FAIL: below-cap infra block must be TERMINAL, got "
        f"recoverable={res_c.recoverable!r}"
    )


# ─── AC2×GATE (§1W-6) — the SECOND CONJUNCT's predicate, on the form CLOSEST
#     TO THE MEASURED INCIDENT. Without this the gate and the helper are two
#     unrelated pieces of work: `injection_dir(sp).exists()` satisfies every
#     other row of this file while leaving the incident shape uncovered.


def test_ac2_gate_second_conjunct_uses_the_strip_contract_not_dir_existence(tmp_path):
    """§1W-6: the gate's "inputs are genuinely absent" conjunct must be the
    SAME predicate `require_injection_files` implements (§4.1 — empty AFTER
    `strip()`), not "the directory is missing".

    Arm 1 (SUBJECT, red today): the directory EXISTS, all six files are
    present, `hal-memory.md` holds ONLY whitespace, and the worker DECLARED the
    block (`BLOCKED_WORKER_OUTPUT`). Both conjuncts are true, so the terminal
    must be E_INJECTION_MISSING.

    Why this arm and not another decoy: every other fixture in this file uses
    one of two extremes — no directory at all, or `_seed_injection` put
    everything in place. A GREEN written as
    `if _declares_block(raw) and not injection_dir(sp).exists():` passes all of
    them, uses `injection_dir` (so §1g LOOKS satisfied), and still surfaces the
    measured incident shape as E_REVIEW_FAILED. AC2 already pins the strip()
    contract on the helper; nothing until now made the GATE use it.

    FAILS TODAY by subject: `_gate_on_review` has no injection awareness at
    all, so the non-empty block answer falls through to the verdict axis and
    exits as `E_REVIEW_FAILED: FEATURE/COMPLEX spec REVISE verdict on cycle 2
    (cap reached)` (phase_45_spec.py:4673-4693).

    Arm 2 (fence, green today): the SAME broken directory with a SUBSTANTIVE
    REVISE that declares no block stays E_REVIEW_FAILED — widening the disk
    conjunct must not weaken the conjunction into a disk-only classifier.
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_REVISE, _gate_on_review

    # ── arm 1: dir present, one file whitespace-only, worker declared block ──
    sp_a = tmp_path / "strip_block"
    inj_a = _seed_injection_whitespace(sp_a)
    assert inj_a.is_dir(), (
        "§1W-6 arm 1 precondition: the DIRECTORY must exist — a dir-existence "
        "check must NOT be able to satisfy this arm"
    )
    assert all((inj_a / n).exists() for n in INJECTION_FILES), (
        "§1W-6 arm 1 precondition: every named file must be PRESENT on disk"
    )
    assert not (inj_a / "hal-memory.md").read_text(encoding="utf-8").strip(), (
        "§1W-6 arm 1 precondition: hal-memory.md must be empty AFTER strip()"
    )

    res_a = _gate_on_review(
        _make_ctx(sp_a, complexity="FEATURE"),
        _make_gate_prev(sp_a, raw_review=BLOCKED_WORKER_OUTPUT),
    )
    assert res_a.error_code == CODE, (
        f"§1W-6 FAIL: the injection directory EXISTS but `hal-memory.md` is "
        f"whitespace-only and the worker DECLARED the block — this is the form "
        f"closest to the measured incident and must terminate as {CODE!r}; got "
        f"{res_a.error_code!r} (error={res_a.error!r}). A gate whose disk conjunct "
        f"is `injection_dir(sp).exists()` reports reviewer disagreement here, which "
        f"is exactly the masquerade this lot closes."
    )
    assert res_a.recoverable is False, (
        f"§1W-6 FAIL: terminal, got recoverable={res_a.recoverable!r}"
    )

    # ── arm 2 (fence): same broken dir, SUBSTANTIVE answer ⇒ unchanged ──
    sp_b = tmp_path / "strip_substantive"
    _seed_injection_whitespace(sp_b)
    res_b = _gate_on_review(
        _make_ctx(sp_b, complexity="FEATURE"),
        _make_gate_prev(sp_b, raw_review=GENUINE_REVISE_OUTPUT, verdict=VERDICT_REVISE),
    )
    assert res_b.error_code == "E_REVIEW_FAILED", (
        f"§1W-6 FENCE FAIL: broadening the disk conjunct from 'dir missing' to "
        f"'files missing or empty after strip()' must not make it SUFFICIENT — a "
        f"substantive REVISE declaring no block stays E_REVIEW_FAILED; got "
        f"{res_b.error_code!r} (error={res_b.error!r})"
    )


# ─── r18 MAJOR-1 — the FIRST conjunct's predicate ("the worker DECLARED a
#     block"), asserted where it is actually EVALUATED: with the inputs
#     genuinely absent. Mirror image of §1W-6, and found for the same reason —
#     a decoy standing in a state where the OTHER conjunct already decides the
#     outcome discriminates nothing.


def test_r18_first_conjunct_needs_a_declared_block_not_words_that_look_like_one(tmp_path):
    """Every text decoy in this file until now (AC12 arm a, AC18 arm C, AC24
    arm C) is fed alongside a COMPLETE injection dir. Under a conjunction the
    disk conjunct is already false there, so the TEXT predicate is never
    evaluated at all: those arms kill a non-conjunctive GREEN and nothing else.
    Meanwhile the only substantive answers fed with inputs ABSENT
    (GENUINE_REVISE_OUTPUT, GENUINE_VALIDATION_FAIL_OUTPUT) are long and
    scrubbed of every block-ish word — `test_ac12` even asserts the absence of
    "block" outright. The corpus therefore GUARANTEED the text predicate would
    never run against a substantive answer.

    Three GREENs passed all thirteen tests before this one existed:
      1. `msg and "block" in raw.lower()` — in this tree "blocker"/"blocking"
         is ordinary review prose, and `phase_8_post_deploy.py:122` removes
         `injection/` as routine housekeeping, so real dissent gets buried
         under an infrastructure diagnosis: the masquerade INVERTED;
      2. `msg and len(raw.strip()) < 60` — the "too little text" predicate.
         AC21 and AC24-PASS constrain where such a branch may SIT, never what
         it may TEST;
      3. `msg and verdict == VERDICT_UNKNOWN` — every substantive arm on the
         review tiers passes VERDICT_REVISE explicitly, so this died only
         incidentally, on `_validation_prev`'s default.

    Each arm below feeds a SUBSTANTIVE answer with `injection/` ABSENT — the
    one state where the text predicate must carry the decision — and requires
    the ordinary terminal. Arm (a) carries `block`, `STATUS` and `missing` as a
    QUOTATION; arm (b) is shorter than BLOCKED_WORKER_OUTPUT; both leave the
    verdict at UNKNOWN, so all three candidates die on each tier.

    GREENABLE, and the instrument proves it rather than asserting it: in
    BLOCKED_WORKER_OUTPUT `STATUS=block` opens a LINE, in the decoy it sits
    mid-sentence behind a quote mark, so `^STATUS=block` (MULTILINE)
    distinguishes a declaration from a citation.

    PASSES TODAY (no gate has a classifier at all) — a correctness guard on
    the GREEN, in the same role as AC4, AC12 and AC21.
    """
    from bytedigger_engine.workflows.phase_45_spec import _gate_on_review
    from bytedigger_engine.workflows.phase_45_spec_lite import _gate_on_review as _gate_lite
    from bytedigger_engine.workflows import phase_5_implement as p5

    declared = re.compile(r"^STATUS=block", re.MULTILINE)

    # ── instruments: the decoys must actually exercise the three predicates ──
    for word in ("block", "status", "missing"):
        assert word in QUOTING_BUT_SUBSTANTIVE_OUTPUT.lower(), (
            f"r18 instrument: the quoting decoy must carry {word!r}, otherwise a "
            f"free-text predicate is never exercised by it"
        )
    assert declared.search(BLOCKED_WORKER_OUTPUT), (
        "r18 instrument: the real block DECLARES itself at the start of a line — "
        "that is what makes these arms greenable"
    )
    assert not declared.search(QUOTING_BUT_SUBSTANTIVE_OUTPUT), (
        "r18 instrument: the decoy must CITE the sentence mid-line, never declare "
        "it — otherwise this test demands the impossible of GREEN"
    )
    for terse in (TERSE_REVISE_OUTPUT, TERSE_VALIDATION_FAIL_OUTPUT):
        assert len(terse.strip()) < len(BLOCKED_WORKER_OUTPUT.strip()), (
            f"r18 instrument: {terse!r} must be SHORTER than the block answer "
            f"({len(BLOCKED_WORKER_OUTPUT.strip())} chars), otherwise it does not "
            f"exercise a 'too little text' classifier"
        )
        assert not declared.search(terse) and "status" not in terse.lower(), (
            f"r18 instrument: the terse answer must declare no block at all, got {terse!r}"
        )

    def _absent(name: str) -> Path:
        """§1aa — a scratchpad with NO injection dir, so the disk conjunct is
        TRUE and the text predicate is the only thing left to decide."""
        sp = tmp_path / name
        sp.mkdir()
        assert not (sp / "injection").exists(), (
            f"r18 precondition ({name}): injection/ must be ABSENT — with the "
            f"directory present the first conjunct is never evaluated"
        )
        return sp

    # ── FEATURE tier ──
    for label, raw in (("quoting", QUOTING_BUT_SUBSTANTIVE_OUTPUT),
                       ("terse", TERSE_REVISE_OUTPUT)):
        sp = _absent(f"r18_feature_{label}")
        res = _gate_on_review(
            _make_ctx(sp, complexity="FEATURE"),
            # verdict left at UNKNOWN on purpose — candidate (3).
            _make_gate_prev(sp, raw_review=raw),
        )
        assert res.error_code == "E_REVIEW_FAILED", (
            f"r18 MAJOR-1 FAIL (FEATURE, {label} answer, injection/ ABSENT): a "
            f"SUBSTANTIVE reviewer answer that never DECLARES a block must keep the "
            f"substantive terminal E_REVIEW_FAILED; got {res.error_code!r} "
            f"(error={res.error!r}). Reporting {CODE!r} here buries real dissent "
            f"under an infrastructure diagnosis — the masquerade inverted."
        )

    # ── SIMPLE tier ──
    for label, raw in (("quoting", QUOTING_BUT_SUBSTANTIVE_OUTPUT),
                       ("terse", TERSE_REVISE_OUTPUT)):
        sp = _absent(f"r18_simple_{label}")
        res_lite = _gate_lite(
            _make_ctx(sp, complexity="SIMPLE"),
            _make_gate_prev(sp, raw_review=raw),
        )
        assert res_lite.error_code == "E_REVIEW_FAILED", (
            f"r18 MAJOR-1 FAIL (SIMPLE, {label} answer, injection/ ABSENT): expected "
            f"E_REVIEW_FAILED, got {res_lite.error_code!r} (error={res_lite.error!r})"
        )

    # ── VALIDATION tier ──
    for label, raw in (("quoting", QUOTING_BUT_SUBSTANTIVE_OUTPUT),
                       ("terse", TERSE_VALIDATION_FAIL_OUTPUT)):
        sp = _absent(f"r18_validation_{label}")
        res_val = p5._gate_on_validation(_make_ctx(sp), _validation_prev(sp, raw=raw))
        assert res_val.error_code == "E_VALIDATION_FAILED", (
            f"r18 MAJOR-1 FAIL (VALIDATION, {label} answer, injection/ ABSENT): "
            f"expected E_VALIDATION_FAILED, got {res_val.error_code!r} "
            f"(error={res_val.error!r})"
        )


# ─── AC4 — the regression FENCE on the E_REVIEW_UNPARSEABLE precedent
#          (§1A / §1v). Round-2 §1B-6: the old arm 1 duplicated AC3 arm A and
#          is dropped; the fence is the half that carries value.


def test_ac4_unparseable_branch_keeps_its_own_terminal_code(tmp_path):
    """AC4 (fence): an EMPTY worker answer with VERDICT_UNKNOWN must still
    yield its OLD code, E_REVIEW_UNPARSEABLE (phase_45_spec.py:4466-4491).

    That branch is the §1A precedent the new classification branch is modelled
    on and is explicitly out of scope (§1v): a GREEN that swallows it into the
    new E_INJECTION_MISSING branch — the cheapest way to make AC3 arm A green —
    is a regression, and this is the assertion that catches it.

    Passes today by construction; it protects post-GREEN behaviour.
    """
    from bytedigger_engine.workflows.phase_45_spec import _gate_on_review

    sp2 = tmp_path / "scratch2"
    _seed_injection(sp2)
    empty = _gate_on_review(
        _make_ctx(sp2, complexity="FEATURE"),
        _make_gate_prev(sp2, raw_review="   \n\n"),
    )
    assert empty.error_code == "E_REVIEW_UNPARSEABLE", (
        f"AC4 FENCE FAIL: empty reviewer output must keep its OLD terminal code "
        f"E_REVIEW_UNPARSEABLE (§1v), got {empty.error_code!r}"
    )
    assert empty.recoverable is False


# ─── AC6 — the code is REGISTERED and the derived catalogue regenerated.
#          (Exactly the pair missed in #1686; it cost five reds.)


def test_ac6_error_code_registered_and_markdown_regenerated():
    """AC6: `E_INJECTION_MISSING` is in `error_codes.py`, `error_codes.py
    --check` exits 0, AND the COMMITTED `ERROR_CODES.md` is byte-identical to
    `render_markdown()`. All three, because the third is the one that gets
    forgotten and reddens `test_error_codes.py::test_ac4` for everyone.

    FAILS TODAY: the code does not exist in the registry.
    """
    from bytedigger_engine import error_codes as m

    assert CODE in m.ERROR_CODES, (
        f"AC6 FAIL: {CODE!r} is not registered in error_codes.py"
    )
    doc = m.ERROR_CODES[CODE]
    assert isinstance(doc, str) and len(doc) >= 10 and doc != CODE, (
        f"AC6 FAIL: {CODE!r} needs a meaningful docstring, got {doc!r}"
    )

    proc = subprocess.run(
        [sys.executable, str(ERROR_CODES_PY), "--check"],
        cwd=str(ENGINE_PY_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"AC6 FAIL: error_codes.py --check exited {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )

    assert ERROR_CODES_MD.read_bytes() == m.render_markdown().encode(), (
        "AC6 FAIL: committed ERROR_CODES.md is stale — regenerate it "
        "(python3 -c \"import error_codes as m; ...\") after registering the code"
    )


# ─── AC11 — an infrastructure block must NOT consume the review-cycle budget
#           (§1B-5). phase_45_spec.py:4557-4566 keeps a DURABLE, cross-
#           invocation REVISE counter; the injection-missing return has to
#           happen BEFORE `bump_revise_count`. Otherwise missing inputs burn
#           the budget and the NEXT run hits the hard cap "legitimately" —
#           the masquerade coming back through another door.


RUN_ID = "gh1674run"


def _revise_counter_file(scratchpad: Path, run_id: str) -> Path:
    """Same location revise_counter.bump_revise_count writes to
    (lib/revise_counter.py:44-45)."""
    return scratchpad / "resume" / f"spec_revise_count_r{run_id}.json"


def _stage_revise_counter(scratchpad: Path, *, count: int, counted: list[int]) -> Path:
    """§1i: the contested durable resource is PRE-STAGED on disk before the unit
    under test runs — never raced, never derived from a prior test's leftovers."""
    path = _revise_counter_file(scratchpad, RUN_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": count, "counted_cycles": counted}), encoding="utf-8")
    return path


def _read_revise_count(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return int(data.get("count", -1))


def test_ac11_infrastructure_block_does_not_consume_the_revise_budget(tmp_path):
    """AC11: with `injection/` absent and the worker answering
    `STATUS=block / SUMMARY: injection files missing`, `_gate_on_review` must
    return BEFORE `bump_revise_count` (phase_45_spec.py:4564-4566) — the
    durable counter is unchanged, and the code is E_INJECTION_MISSING.

    FAILS TODAY: the non-empty block answer falls through the ALREADY_DONE and
    upstream branches straight into the durable-counter block, so the counter
    goes 3 → 4 and a run whose only fault was a missing input has spent one of
    six review cycles for every future invocation with this run_id.

    Second arm (instrument, passes today and must keep passing): a GENUINE
    REVISE with a complete injection dir still DOES bump the counter — without
    it, "unchanged" would also be satisfied by a counter that never fires
    (e.g. a missing `scratchpad_dir`, the §4557-4563 skip path).
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_REVISE, _gate_on_review

    # ── arm 1: infrastructure block must not spend budget ──
    sp = tmp_path / "scratch_block"
    sp.mkdir()
    assert not (sp / "injection").exists(), "AC11 precondition: injection/ absent"
    counter = _stage_revise_counter(sp, count=3, counted=[1])
    assert _read_revise_count(counter) == 3, "AC11 precondition: counter pre-staged at 3"

    res = _gate_on_review(
        _make_ctx(sp, complexity="FEATURE", run_id=RUN_ID),
        _make_gate_prev(sp, raw_review=BLOCKED_WORKER_OUTPUT, cycle=2),
    )

    after = _read_revise_count(counter)
    assert after == 3, (
        f"AC11 FAIL: an infrastructure block spent the durable review-cycle budget "
        f"(counter 3 → {after}). The injection-missing return must happen BEFORE "
        f"bump_revise_count (phase_45_spec.py:4564-4566), otherwise missing inputs burn "
        f"the cap and the next run aborts 'legitimately'."
    )
    assert res.error_code == CODE, (
        f"AC11 FAIL: the block must terminate as {CODE!r}, got {res.error_code!r} "
        f"(error={res.error!r})"
    )

    # ── arm 2 (instrument): a genuine REVISE still spends budget ──
    sp2 = tmp_path / "scratch_revise"
    _seed_injection(sp2)
    counter2 = _stage_revise_counter(sp2, count=3, counted=[1])
    _gate_on_review(
        _make_ctx(sp2, complexity="FEATURE", run_id=RUN_ID),
        _make_gate_prev(sp2, raw_review=GENUINE_REVISE_OUTPUT, cycle=2,
                        verdict=VERDICT_REVISE),
    )
    assert _read_revise_count(counter2) == 4, (
        "AC11 FAIL (instrument): a GENUINE REVISE must still bump the durable counter "
        "3 → 4; if it does not, arm 1's 'unchanged' proves nothing about ordering."
    )


# ─── AC12 — the classifier is CONJUNCTIVE (§1C-2). The block is recognised only
#           when the worker DECLARED it AND the inputs are genuinely absent.
#           Two plausible naive GREENs pass every other row of this table; each
#           is killed by exactly one arm below.


def test_ac12_classifier_is_conjunctive_not_substring_or_disk_state_alone(tmp_path):
    """AC12 arm (a) — TEXT decoy, kills the substring-only classifier.
    The inputs are PRESENT, and the reviewer's answer is substantive but QUOTES
    `STATUS=block with SUMMARY 'injection files missing'`. That sentence is
    verbatim prompt prose from `phase_workflows_common.py`/
    `lib/spec_retry_cycle.py:107` — the shared block this arm's own path
    (`_gate_on_review` / `spec_retry_cycle.py:118`) actually emits. It is NOT
    shared by every phase's inline copy: `phase_2_explore.py:176` and four
    siblings emit their own `STATUS: BLOCKED with reason '...'` wording
    instead (§1H-M4). A reviewer quoting the sentence THIS path emits while
    genuinely disagreeing is the ordinary case. The verdict must stay
    SUBSTANTIVE — E_REVIEW_FAILED at cap — and must NOT become
    E_INJECTION_MISSING.

    AC12 arm (b) — DISK decoy, kills the disk-state-only classifier.
    `injection/` is ABSENT (phase_8_post_deploy.py:122 deletes it as routine
    housekeeping, so this state is normal, not exotic), and the reviewer's
    answer is a substantive REVISE declaring no block whatsoever. The verdict
    must stay SUBSTANTIVE.

    Neither arm may be softened into "not None": each pins the exact terminal,
    because a classifier that mislabels either direction reintroduces the
    masquerade — arm (a) as a FALSE infrastructure diagnosis that hides real
    reviewer dissent, arm (b) as the same in reverse.

    Both arms pass today (today's gate has no classifier at all, so everything
    is E_REVIEW_FAILED) and are correctness guards on the GREEN: they are the
    only assertions in this file that a substring-match or an os.path.exists
    one-liner cannot satisfy alongside AC3 arm A and AC11 arm 1.
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_REVISE, _gate_on_review

    # ── arm (a): inputs PRESENT, answer QUOTES the block sentence ──
    sp_a = tmp_path / "scratch_text_decoy"
    inj = _seed_injection(sp_a)
    assert inj.is_dir() and all((inj / n).stat().st_size > 0 for n in INJECTION_FILES), (
        "AC12 arm a precondition: every injection file present and non-empty"
    )
    assert "STATUS=block with SUMMARY 'injection files missing'" in QUOTING_BUT_SUBSTANTIVE_OUTPUT, (
        "AC12 arm a instrument: the decoy must contain the prompt sentence VERBATIM, "
        "otherwise it does not exercise a substring classifier at all"
    )

    res_a = _gate_on_review(
        _make_ctx(sp_a, complexity="FEATURE"),
        _make_gate_prev(sp_a, raw_review=QUOTING_BUT_SUBSTANTIVE_OUTPUT,
                        verdict=VERDICT_REVISE),
    )
    assert res_a.error_code == "E_REVIEW_FAILED", (
        f"AC12 arm a FAIL: a SUBSTANTIVE reviewer answer that merely QUOTES the prompt's "
        f"block sentence, with all inputs present on disk, must keep the substantive "
        f"terminal E_REVIEW_FAILED; got {res_a.error_code!r} (error={res_a.error!r}). "
        f"A substring-only classifier reports E_INJECTION_MISSING here and buries real "
        f"reviewer dissent under an infrastructure diagnosis — the masquerade inverted."
    )
    assert res_a.error_code != CODE

    # ── arm (b): inputs ABSENT, answer declares NO block ──
    sp_b = tmp_path / "scratch_disk_decoy"
    sp_b.mkdir()
    assert not (sp_b / "injection").exists(), (
        "AC12 arm b precondition: injection/ absent (the routine post-deploy state)"
    )
    assert "block" not in GENUINE_REVISE_OUTPUT.lower(), (
        "AC12 arm b instrument: the substantive answer must declare no block at all, "
        "otherwise the arm cannot isolate the disk signal"
    )

    res_b = _gate_on_review(
        _make_ctx(sp_b, complexity="FEATURE"),
        _make_gate_prev(sp_b, raw_review=GENUINE_REVISE_OUTPUT, verdict=VERDICT_REVISE),
    )
    assert res_b.error_code == "E_REVIEW_FAILED", (
        f"AC12 arm b FAIL: a substantive REVISE that declares NO block must keep the "
        f"substantive terminal even though `injection/` is gone (phase_8_post_deploy.py:122 "
        f"deletes it routinely); got {res_b.error_code!r} (error={res_b.error!r}). "
        f"A disk-state-only classifier reports E_INJECTION_MISSING here."
    )
    assert res_b.error_code != CODE, (
        "AC12 arm b FAIL: disk state ALONE must never be sufficient — the block is "
        "recognised only when the worker DECLARED it AND the inputs are absent (§1C-2)."
    )


# ─── AC18 — chokepoint (B)'s SECOND instance, on the SIMPLE tier. §1J-3:
#     `phase_45_spec_lite.py:974` is its OWN `_gate_on_review`, with its OWN
#     terminal `E_REVIEW_UNPARSEABLE` (:1080) and its OWN `E_REVIEW_FAILED:
#     SIMPLE spec REVISE verdict on cycle N (cap reached)` (:1168). §1E named
#     this "the same masquerade one tier down"; §2 justifies chokepoint (A)
#     existing at all on the ground that (A) alone still leaves the
#     masquerade on ANY other infrastructure block — this arm applies that
#     argument literally, one tier down. Same shape as AC3 + AC12.


def test_ac18_simple_tier_gate_classifies_infra_block_same_as_feature_tier(tmp_path):
    """AC18 (§1J-3): the SIMPLE-tier gate must classify the SAME four rows
    AC3/AC12 already pin for the FEATURE/COMPLEX tier:
      * arm A — infrastructure block, injection/ absent ⇒ E_INJECTION_MISSING;
      * arm B — genuine REVISE at cap, injection/ complete ⇒ still
        E_REVIEW_FAILED (a rename-only fix is not a fix);
      * arm C (§1C-2 text decoy) — inputs present, answer QUOTES the block
        sentence ⇒ stays E_REVIEW_FAILED, not E_INJECTION_MISSING;
      * arm D (§1C-2 disk decoy) — injection/ absent, answer is a genuine
        REVISE declaring no block ⇒ stays E_REVIEW_FAILED;
      * arm E (§1W-2 below-cap) — infra block on cycle 1 ⇒ TERMINATES as
        E_INJECTION_MISSING instead of returning status="ok" and re-entering.

    FAILS TODAY (arms A and E): the SIMPLE-tier gate has no classifier at all —
    every branch below VERDICT_SHIP falls straight to the durable-cycle axis,
    so at cap the non-empty block answer surfaces as `E_REVIEW_FAILED: SIMPLE
    spec REVISE verdict on cycle 2 (cap reached)`
    (phase_45_spec_lite.py:1158-1170), and below cap it returns status="ok"
    with cycle+1 (:1086-1115) and spends a second run.
    Arms B/C/D pass today (the gate has no classifier, so everything not
    SHIP/UNPARSEABLE is already E_REVIEW_FAILED) and are the fence + decoys
    that stop GREEN from over-firing the new branch on this tier too.
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import VERDICT_REVISE
    from bytedigger_engine.workflows.phase_45_spec_lite import _gate_on_review as _gate_lite

    # ── arm A: infra block, injection/ absent ──
    sp_a = tmp_path / "lite_scratch_a"
    sp_a.mkdir()
    assert not (sp_a / "injection").exists(), "AC18 arm A precondition: injection/ absent"
    res_a = _gate_lite(
        _make_ctx(sp_a, complexity="SIMPLE"),
        _make_gate_prev(sp_a, raw_review=BLOCKED_WORKER_OUTPUT),
    )
    assert res_a.error_code == CODE, (
        f"AC18 arm A FAIL: the SIMPLE-tier gate must terminate an infrastructure block as "
        f"{CODE!r}, got {res_a.error_code!r} (error={res_a.error!r})"
    )
    assert res_a.recoverable is False, (
        f"AC18 arm A FAIL: terminal — with recoverable={res_a.recoverable!r} the engine "
        f"retries from step 0 and spends another run on inputs that are still absent"
    )

    # ── arm B: genuine REVISE at cap, injection/ complete ──
    sp_b = tmp_path / "lite_scratch_b"
    _seed_injection(sp_b)
    res_b = _gate_lite(
        _make_ctx(sp_b, complexity="SIMPLE"),
        _make_gate_prev(sp_b, raw_review=GENUINE_REVISE_OUTPUT, verdict=VERDICT_REVISE),
    )
    assert res_b.error_code == "E_REVIEW_FAILED", (
        f"AC18 arm B FAIL: a genuine SIMPLE-tier REVISE at cap must STILL be "
        f"E_REVIEW_FAILED, got {res_b.error_code!r} (error={res_b.error!r})"
    )

    # ── arm C: text decoy — inputs present, answer QUOTES the block sentence ──
    sp_c = tmp_path / "lite_scratch_c"
    _seed_injection(sp_c)
    res_c = _gate_lite(
        _make_ctx(sp_c, complexity="SIMPLE"),
        _make_gate_prev(sp_c, raw_review=QUOTING_BUT_SUBSTANTIVE_OUTPUT, verdict=VERDICT_REVISE),
    )
    assert res_c.error_code == "E_REVIEW_FAILED", (
        f"AC18 arm C FAIL: a substantive answer that merely quotes the block sentence, "
        f"with inputs present, must stay E_REVIEW_FAILED; got {res_c.error_code!r} "
        f"(error={res_c.error!r})"
    )

    # ── arm D: disk decoy — injection/ absent, answer is a genuine REVISE ──
    sp_d = tmp_path / "lite_scratch_d"
    sp_d.mkdir()
    assert not (sp_d / "injection").exists(), "AC18 arm D precondition: injection/ absent"
    res_d = _gate_lite(
        _make_ctx(sp_d, complexity="SIMPLE"),
        _make_gate_prev(sp_d, raw_review=GENUINE_REVISE_OUTPUT, verdict=VERDICT_REVISE),
    )
    assert res_d.error_code == "E_REVIEW_FAILED", (
        f"AC18 arm D FAIL: disk state alone must never be sufficient — a substantive "
        f"REVISE declaring no block must stay E_REVIEW_FAILED even with injection/ "
        f"absent; got {res_d.error_code!r} (error={res_d.error!r})"
    )

    # ── arm E (§1W-2): BELOW cap. Arms A–D all sit at cycle == MAX_REVIEW_CYCLES,
    # so a GREEN that only patches the terminal tail passes every one of them —
    # while an infra block on cycle 1 takes phase_45_spec_lite.py:1086, returns
    # status="ok" with cycle+1 and RE-ENTERS the loop, spending a SECOND run on
    # the same absent inputs. That is literally the two-run cost measured in §1.
    sp_e = tmp_path / "lite_scratch_e"
    sp_e.mkdir()
    assert not (sp_e / "injection").exists(), "AC18 arm E precondition: injection/ absent"
    prev_e = _make_gate_prev(sp_e, raw_review=BLOCKED_WORKER_OUTPUT, cycle=1, at_cap=False)
    assert prev_e.data["cycle"] < 2, (
        "AC18 arm E precondition: the fixture must sit BELOW MAX_REVIEW_CYCLES=2, "
        "otherwise it duplicates arm A and pins nothing new"
    )
    res_e = _gate_lite(_make_ctx(sp_e, complexity="SIMPLE"), prev_e)
    assert res_e.error_code == CODE, (
        f"AC18 arm E FAIL: an infrastructure block on cycle 1 must TERMINATE as "
        f"{CODE!r}, not re-enter the review loop; got status={res_e.status!r} "
        f"error_code={res_e.error_code!r} (error={res_e.error!r}). Re-entering spends "
        f"a second run on inputs that have not reappeared."
    )
    assert res_e.status == "error" and res_e.recoverable is False, (
        f"AC18 arm E FAIL: below-cap infra block must be a TERMINAL error, got "
        f"status={res_e.status!r} recoverable={res_e.recoverable!r}"
    )

    # ── arm F (r18 MINOR-1): the §1W-6 strip-contract arm, one tier down. The
    # directory EXISTS and only `hal-memory.md` is whitespace, so a SIMPLE-tier
    # GREEN left on `injection_dir(sp).exists()` still reads the incident shape
    # as reviewer disagreement.
    sp_f = tmp_path / "lite_scratch_f"
    inj_f = _seed_injection_whitespace(sp_f)
    assert inj_f.is_dir() and not (inj_f / "hal-memory.md").read_text(encoding="utf-8").strip(), (
        "AC18 arm F precondition: directory PRESENT, hal-memory.md empty after strip()"
    )
    res_f = _gate_lite(
        _make_ctx(sp_f, complexity="SIMPLE"),
        _make_gate_prev(sp_f, raw_review=BLOCKED_WORKER_OUTPUT),
    )
    assert res_f.error_code == CODE, (
        f"AC18 arm F FAIL: on the SIMPLE tier too the disk conjunct must be the "
        f"strip() contract, not directory existence — got {res_f.error_code!r} "
        f"(error={res_f.error!r})"
    )


def test_ac21_ship_verdict_still_ships_when_injection_is_absent(tmp_path):
    """AC21 (§1G F3/F4, re-raised round 8 M3, never closed until now — the
    SHIP shield). Nothing in this file asserts that a LEGITIMATE
    `VERDICT_SHIP` review still SHIPS when `injection/` is absent.
    `phase_8_post_deploy.py:122` routinely deletes `injection/` as ordinary
    housekeeping (AC12 arm b already relies on this being the routine
    state, not an exotic one). A GREEN that places the conjunctive infra
    check (chokepoint B) ABOVE the pre-existing SHIP branch of
    `_gate_on_review` (phase_45_spec.py:4458) converts a legitimate ship
    into `E_INJECTION_MISSING`, and no other test in this file would
    notice — AC3/AC4/AC12/AC18 all exercise REVISE-shaped inputs, never
    SHIP.

    This is a REGRESSION FENCE, not a hole in current behaviour, and its
    docstring says so explicitly per the assignment: nothing blocks ship
    today (there is no classifier at all yet), so BOTH arms below PASS
    today. It exists so a later GREEN that gets the branch order wrong is
    caught immediately rather than shipping silently — the same role AC4's
    fence plays for the `E_REVIEW_UNPARSEABLE` branch.

    FEATURE/COMPLEX tier (`phase_45_spec._gate_on_review`) and SIMPLE tier
    (`phase_45_spec_lite._gate_on_review`) are BOTH asserted: AC18 already
    established the SIMPLE tier gets the identical chokepoint-(B) treatment,
    so the shield must hold on both gates the same way.
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_SHIP, _gate_on_review

    sp = tmp_path / "ac21_ship_scratch_feature"
    sp.mkdir()
    assert not (sp / "injection").exists(), (
        "AC21 precondition: injection/ absent (phase_8_post_deploy.py:122's routine state)"
    )
    res = _gate_on_review(
        _make_ctx(sp, complexity="FEATURE"),
        _make_gate_prev(sp, raw_review="## Verdict\nSHIP\nLGTM, ship it.\n", verdict=VERDICT_SHIP),
    )
    assert res.status == "ok", (
        f"AC21 FAIL: a legitimate SHIP verdict with injection/ absent must still ship "
        f"(status='ok'); got status={res.status!r} error_code={res.error_code!r} "
        f"error={res.error!r} — the infra check has been placed ABOVE the ship branch"
    )
    assert res.error_code != CODE, (
        f"AC21 FAIL: SHIP must never be converted into {CODE!r} by the absence of "
        f"injection/ — that absence is routine post-deploy housekeeping, not a reason "
        f"to reject a verdict that already shipped"
    )

    # SIMPLE tier: phase_45_spec_lite._gate_on_review gets the identical shield (AC18's
    # own precedent — the SIMPLE gate mirrors the FEATURE/COMPLEX one row for row).
    from bytedigger_engine.workflows.phase_45_spec_lite import VERDICT_SHIP as VERDICT_SHIP_LITE
    from bytedigger_engine.workflows.phase_45_spec_lite import _gate_on_review as _gate_lite

    sp2 = tmp_path / "ac21_ship_scratch_simple"
    sp2.mkdir()
    assert not (sp2 / "injection").exists(), (
        "AC21 SIMPLE precondition: injection/ absent"
    )
    res2 = _gate_lite(
        _make_ctx(sp2, complexity="SIMPLE"),
        _make_gate_prev(sp2, raw_review="## Verdict\nSHIP\nLGTM, ship it.\n", verdict=VERDICT_SHIP_LITE),
    )
    assert res2.status == "ok", (
        f"AC21 SIMPLE FAIL: a legitimate SHIP verdict with injection/ absent must still "
        f"ship on the SIMPLE tier too; got status={res2.status!r} "
        f"error_code={res2.error_code!r} error={res2.error!r}"
    )
    assert res2.error_code != CODE, (
        f"AC21 SIMPLE FAIL: SHIP must never be converted into {CODE!r} on the SIMPLE "
        f"tier either"
    )


# ─── AC24 — chokepoint (B)'s THIRD instance, on the VALIDATION tier (§1N-2).
#     `phase_5_implement._gate_on_validation` (:7626, wired as the step
#     `gate_on_validation` at :8549 inside `LoopRunner`'s body at :8594) is
#     covered by NEITHER chokepoint (A) nor (B): the only thing that ever
#     covered this loop-body entry point was AC5 arm 2 (the per-builder
#     `_build_validation_prompt` carrier), retired this same round (§1M-5/
#     MAJOR-3) as a remnant of the snapped form. Deleting AC5 therefore OPENS
#     this window unless this AC closes it — same shape as AC3/AC12/AC18, one
#     tier down.


def test_ac24_validation_tier_gate_gets_the_identical_chokepoint_b_treatment(tmp_path):
    """AC24 (§1N-2): the same four rows AC3/AC12/AC18 already pin for the two
    `_gate_on_review`s, now on `_gate_on_validation`:
      * arm A — infra block: the worker's raw validation output declares the
        block and `injection/` is genuinely absent ⇒ E_INJECTION_MISSING;
      * arm B — a genuine validation FAIL at cap, injection/ complete ⇒ still
        E_VALIDATION_FAILED (a rename-only fix is not a fix);
      * arm C (§1C-2 text decoy) — inputs present, the raw output QUOTES the
        block sentence while genuinely failing ⇒ stays E_VALIDATION_FAILED;
      * arm D (§1C-2 disk decoy) — injection/ absent, raw output is a genuine
        FAIL declaring no block ⇒ stays E_VALIDATION_FAILED;
      * arm E (§1W-2 below-cap) — infra block on cycle 1 ⇒ TERMINATES as
        E_INJECTION_MISSING instead of re-entering the loop.

    FAILS TODAY (arms A and E): `_gate_on_validation` has no injection
    awareness at all; at cap every non-PASS verdict falls straight to the
    terminal E_VALIDATION_FAILED branch (phase_5_implement.py:7792-7805), and
    below cap it returns status="ok" and re-iterates (:7763) — so an infra
    block is indistinguishable from a genuine validation gap AND costs a second
    run. Arms B/C/D pass today (there is no classifier yet, so every
    non-PASS-at-cap case is already E_VALIDATION_FAILED) and are the fence +
    decoys that stop GREEN from over-firing the new branch on this tier too.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5

    genuine_fail = GENUINE_VALIDATION_FAIL_OUTPUT

    # ── arm A: infra block, injection/ absent ──
    sp_a = tmp_path / "ac24_scratch_a"
    sp_a.mkdir()
    assert not (sp_a / "injection").exists(), "AC24 arm A precondition: injection/ absent"
    res_a = p5._gate_on_validation(_make_ctx(sp_a), _validation_prev(sp_a, raw=BLOCKED_WORKER_OUTPUT))
    assert res_a.error_code == CODE, (
        f"AC24 arm A FAIL: an infrastructure block on the VALIDATION tier must "
        f"terminate as {CODE!r}, got {res_a.error_code!r} (error={res_a.error!r}) — "
        f"indistinguishable from a genuine validation gap"
    )
    assert res_a.recoverable is False, (
        f"AC24 arm A FAIL: terminal — with recoverable={res_a.recoverable!r} the engine "
        f"retries from step 0 and spends another run on inputs that are still absent"
    )

    # ── arm B: genuine validation FAIL at cap, injection/ complete ──
    sp_b = tmp_path / "ac24_scratch_b"
    _seed_injection(sp_b)
    res_b = p5._gate_on_validation(_make_ctx(sp_b), _validation_prev(sp_b, raw=genuine_fail))
    assert res_b.error_code == "E_VALIDATION_FAILED", (
        f"AC24 arm B FAIL: a genuine validation FAIL at cap must STILL be "
        f"E_VALIDATION_FAILED, got {res_b.error_code!r} (error={res_b.error!r})"
    )

    # ── arm C: text decoy — inputs present, raw output QUOTES the block sentence ──
    sp_c = tmp_path / "ac24_scratch_c"
    _seed_injection(sp_c)
    assert "STATUS=block with SUMMARY 'injection files missing'" in QUOTING_BUT_SUBSTANTIVE_OUTPUT, (
        "AC24 arm C instrument: the decoy must contain the prompt sentence VERBATIM"
    )
    res_c = p5._gate_on_validation(
        _make_ctx(sp_c), _validation_prev(sp_c, raw=QUOTING_BUT_SUBSTANTIVE_OUTPUT)
    )
    assert res_c.error_code == "E_VALIDATION_FAILED", (
        f"AC24 arm C FAIL: a substantive FAIL that merely quotes the block sentence, "
        f"with inputs present, must stay E_VALIDATION_FAILED; got "
        f"{res_c.error_code!r} (error={res_c.error!r})"
    )
    assert res_c.error_code != CODE

    # ── arm D: disk decoy — injection/ absent, raw output is a genuine FAIL ──
    sp_d = tmp_path / "ac24_scratch_d"
    sp_d.mkdir()
    assert not (sp_d / "injection").exists(), "AC24 arm D precondition: injection/ absent"
    res_d = p5._gate_on_validation(_make_ctx(sp_d), _validation_prev(sp_d, raw=genuine_fail))
    assert res_d.error_code == "E_VALIDATION_FAILED", (
        f"AC24 arm D FAIL: disk state alone must never be sufficient — a genuine "
        f"FAIL must stay E_VALIDATION_FAILED even with injection/ absent; got "
        f"{res_d.error_code!r} (error={res_d.error!r})"
    )
    assert res_d.error_code != CODE

    # ── arm E (§1W-2): BELOW cap. Arms A–D sit at cycle == cap, so a GREEN that
    # only patches the terminal tail passes them all — while an infra block on
    # cycle 1 takes phase_5_implement.py:7763, returns status="ok" with cycle+1
    # and re-enters the RED→validation loop, spending a SECOND run on the same
    # absent inputs. Exactly the two-run cost measured in §1.
    sp_e = tmp_path / "ac24_scratch_e"
    sp_e.mkdir()
    assert not (sp_e / "injection").exists(), "AC24 arm E precondition: injection/ absent"
    prev_e = _validation_prev(sp_e, raw=BLOCKED_WORKER_OUTPUT, cycle=1)
    assert prev_e.data["cycle"] < 2, (
        "AC24 arm E precondition: the fixture must sit BELOW MAX_VALIDATION_CYCLES=2, "
        "otherwise it duplicates arm A and pins nothing new"
    )
    res_e = p5._gate_on_validation(_make_ctx(sp_e), prev_e)
    assert res_e.error_code == CODE, (
        f"AC24 arm E FAIL: an infrastructure block on cycle 1 must TERMINATE as "
        f"{CODE!r}, not re-enter the validation loop; got status={res_e.status!r} "
        f"error_code={res_e.error_code!r} (error={res_e.error!r})"
    )
    assert res_e.status == "error" and res_e.recoverable is False, (
        f"AC24 arm E FAIL: below-cap infra block must be a TERMINAL error — with "
        f"recoverable={res_e.recoverable!r} the engine retries from step 0 and spends "
        f"another run; got status={res_e.status!r}"
    )

    # ── arm F (r18 MINOR-1): the §1W-6 strip-contract arm on the VALIDATION
    # tier — directory PRESENT, `hal-memory.md` whitespace-only, block declared.
    sp_f = tmp_path / "ac24_scratch_f"
    inj_f = _seed_injection_whitespace(sp_f)
    assert inj_f.is_dir() and not (inj_f / "hal-memory.md").read_text(encoding="utf-8").strip(), (
        "AC24 arm F precondition: directory PRESENT, hal-memory.md empty after strip()"
    )
    res_f = p5._gate_on_validation(_make_ctx(sp_f), _validation_prev(sp_f, raw=BLOCKED_WORKER_OUTPUT))
    assert res_f.error_code == CODE, (
        f"AC24 arm F FAIL: on the VALIDATION tier too the disk conjunct must be the "
        f"strip() contract, not directory existence — got {res_f.error_code!r} "
        f"(error={res_f.error!r})"
    )


# ─── AC24-PASS (§1W-3) — the SHIP shield's missing VALIDATION twin.


def test_ac24_pass_validation_still_passes_when_injection_is_absent(tmp_path, monkeypatch):
    """§1W-3 (FENCE): a legitimate `PASS` must still pass when `injection/` is
    absent. AC21 shields the SHIP branch on the two REVIEW gates and stops
    there; its own justification — "every other arm feeds REVISE-shaped input,
    never SHIP" — applies verbatim to `_gate_on_validation`, whose PASS path
    (phase_5_implement.py:7806-7859) no assertion in this file touches.

    The concrete GREEN this catches: an infra check of the shape "too little
    text ⇒ infrastructure block" placed at the TOP of `_gate_on_validation`
    passes all five AC24 arms and converts a terse, legitimate
    `## Verdict\\nPASS` into E_INJECTION_MISSING every time `injection/` has been
    routinely cleaned up (phase_8_post_deploy.py:122) — aborting a build that
    PASSED. That is strictly worse than the bug being fixed.

    PASSES TODAY (there is no classifier at all yet); it is a regression fence
    on post-GREEN behaviour, exactly like AC4 and AC21.

    HAL_VERDICT_GATE_LINT is pinned off so the fence measures the GATE's branch
    order and not the verdict-gate lint subprocess (which is fail-open anyway,
    but would make this arm depend on the surrounding tree).
    """
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.setenv("HAL_VERDICT_GATE_LINT", "0")

    sp = tmp_path / "ac24_pass_scratch"
    sp.mkdir()
    assert not (sp / "injection").exists(), (
        "§1W-3 precondition: injection/ absent (phase_8_post_deploy.py:122's routine state)"
    )
    assert len(GENUINE_VALIDATION_PASS_OUTPUT) < len(BLOCKED_WORKER_OUTPUT), (
        "§1W-3 instrument: the legitimate PASS must be SHORTER than the block answer, "
        "otherwise it does not exercise a 'too little text' classifier at all"
    )

    res = p5._gate_on_validation(
        _make_ctx(sp),
        _validation_prev(sp, raw=GENUINE_VALIDATION_PASS_OUTPUT, verdict=p5.VERDICT_PASS),
    )
    assert res.status == "ok", (
        f"§1W-3 FENCE FAIL: a legitimate PASS with injection/ absent must still pass "
        f"(status='ok'); got status={res.status!r} error_code={res.error_code!r} "
        f"error={res.error!r} — the infra check sits ABOVE the PASS path and is "
        f"aborting a build that already succeeded"
    )
    assert res.error_code != CODE, (
        f"§1W-3 FENCE FAIL: PASS must never be converted into {CODE!r} by the absence "
        f"of injection/ — that absence is routine post-deploy housekeeping"
    )


# ─── ctx-shape fence (§1W-4) — 10 modules / 29 call sites invoke these gates as
#     `_gate_on_*(None, …)`, and more pass an org_config with no
#     `scratchpad_dir`. A GREEN that opens a gate with
#     `require_injection_files(_resolve_scratchpad(_ctx))` raises before any
#     verdict logic and reddens all of them. Production already carries the
#     discipline this fence pins: phase_45_spec.py:4561-4563 — "no
#     scratchpad_dir ⇒ skip the counter entirely, behaviour unchanged".


def test_gates_tolerate_a_ctx_with_no_scratchpad(tmp_path):
    """§1W-4 (FENCE): with `_ctx=None`, and with an `org_config` that has no
    `scratchpad_dir`, ALL THREE gates must still return their pre-GREEN result
    for a substantive REVISE/FAIL at cap — never raise, never change code.

    A gate cannot evaluate the disk conjunct without a scratchpad; the correct
    behaviour is therefore to leave the verdict axis alone, not to fail. The
    answers below declare no block, so no conjunctive classifier should fire
    even if a scratchpad were available — which keeps this fence about the CTX
    SHAPE and nothing else.

    PASSES TODAY on all six calls (no gate reads a scratchpad on these paths).
    It is the fence that keeps GREEN from reddening the 29 measured call sites.
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_REVISE, _gate_on_review
    from bytedigger_engine.workflows.phase_45_spec_lite import VERDICT_REVISE as VERDICT_REVISE_LITE
    from bytedigger_engine.workflows.phase_45_spec_lite import _gate_on_review as _gate_lite
    from bytedigger_engine.workflows import phase_5_implement as p5

    # `_make_gate_prev`/`_validation_prev` need a directory to write docs into;
    # that directory is deliberately NOT handed to any gate below.
    docs = tmp_path / "docs_only"
    docs.mkdir()

    def _ctx_without_scratchpad(**org_extra: Any) -> Any:
        from bytedigger_engine.contracts import WorkflowContext

        return WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config=dict(org_extra),  # NO scratchpad_dir key at all
            question="Add foo to bar", session_id="test-gh1674",
            persona="hal", framework=None, domain=None,
        )

    assert "scratchpad_dir" not in (_ctx_without_scratchpad().org_config or {}), (
        "§1W-4 instrument: the ctx must genuinely lack scratchpad_dir"
    )

    for label, ctx in (
        ("_ctx=None", None),
        ("org_config without scratchpad_dir", _ctx_without_scratchpad(complexity="FEATURE")),
    ):
        res = _gate_on_review(
            ctx, _make_gate_prev(docs, raw_review=GENUINE_REVISE_OUTPUT, verdict=VERDICT_REVISE)
        )
        assert res.error_code == "E_REVIEW_FAILED", (
            f"§1W-4 FENCE FAIL (FEATURE tier, {label}): the gate must keep its "
            f"pre-GREEN terminal E_REVIEW_FAILED, got {res.error_code!r} "
            f"(status={res.status!r} error={res.error!r}). 29 production call sites "
            f"invoke the gates in exactly this shape."
        )

    for label, ctx in (
        ("_ctx=None", None),
        ("org_config without scratchpad_dir", _ctx_without_scratchpad(complexity="SIMPLE")),
    ):
        res_lite = _gate_lite(
            ctx, _make_gate_prev(docs, raw_review=GENUINE_REVISE_OUTPUT,
                                 verdict=VERDICT_REVISE_LITE)
        )
        assert res_lite.error_code == "E_REVIEW_FAILED", (
            f"§1W-4 FENCE FAIL (SIMPLE tier, {label}): expected the pre-GREEN "
            f"E_REVIEW_FAILED, got {res_lite.error_code!r} (status={res_lite.status!r} "
            f"error={res_lite.error!r})"
        )

    for label, ctx in (
        ("_ctx=None", None),
        ("org_config without scratchpad_dir", _ctx_without_scratchpad()),
    ):
        res_val = p5._gate_on_validation(
            ctx, _validation_prev(docs, raw=GENUINE_VALIDATION_FAIL_OUTPUT)
        )
        assert res_val.error_code == "E_VALIDATION_FAILED", (
            f"§1W-4 FENCE FAIL (VALIDATION tier, {label}): expected the pre-GREEN "
            f"E_VALIDATION_FAILED, got {res_val.error_code!r} "
            f"(status={res_val.status!r} error={res_val.error!r})"
        )
