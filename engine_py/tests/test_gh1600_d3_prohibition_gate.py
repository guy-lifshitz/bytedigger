"""RED tests for hal#1600 (D3) v2 — plan-review must REVISE when the
spec's file list violates a stated prohibition.

Spec: SHARED/memory/Decisions/2026-08-08_1600_d3_prohibition_gate_spec.md
(v2, after gate round 1 — 7 MAJOR; clause segmentation corrected to blank-line
+ `(?<=[.;])\\s+`, verbs MODIFY/DELETE/RENAME only, exact path match, minimal
negation->path reason span, check runs before write_run_allowlist_for_spec).

UUTs (never mocked, per §1l):
  - `workflows/phase_45_spec.py::_gate_on_review` — SHIP branch gains a
    deterministic pre-`ok`/pre-write-allowlist check that can override SHIP
    into REVISE.
  - `lib/run_allowlist.py::parse_spec_files_with_verbs` — new verb-preserving
    entry point; does NOT exist yet on main, looked up via `getattr` inside
    each test body (§1q) so this file collects cleanly today.

`_gate_on_review` is exercised directly with a real `StepResult` `prev` and a
lightweight `org_config`-bearing context (matches sibling convention in
tests/test_run_allowlist_1DA29C33.py and tests/test_gh633_revise_idempotent.py
— SimpleNamespace(org_config=...) is sufficient because the gate only reads
`_ctx.org_config`). Real spec files are written under `tmp_path` with a
`## Files` section using real verb-prefixed lines — no mocking of the parser
or the gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bytedigger_engine.contracts import StepResult


# ─── fixtures / helpers ────────────────────────────────────────────────────


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


def _revise_prev(spec_path: str, review_path: str = "review.md", cycle: int = 1) -> StepResult:
    data = {
        "verdict": "REVISE",
        "review_path": review_path,
        "spec_path": spec_path,
        "cycle": cycle,
        "review_raw": "## Verdict\nREVISE\n\n## Findings (structured)\n```json\n[]\n```\n",
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_review_doc")


def _unknown_blank_prev(spec_path: str, review_path: str = "review.md", cycle: int = 1) -> StepResult:
    data = {
        "verdict": "UNKNOWN",
        "review_path": review_path,
        "spec_path": spec_path,
        "cycle": cycle,
        "review_raw": "   ",
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_review_doc")


_ALL_ALREADY_DONE_RAW = (
    "## Verdict\nREVISE\n\n"
    "## Findings (structured)\n```json\n"
    '[{"id":"1","type":"missing","evidence":"already fixed at HEAD","required_action":"none",'
    '"root":"already-done"}]\n'
    "```\n"
)


def _already_done_prev(spec_path: str, review_path: str = "review.md", cycle: int = 1) -> StepResult:
    data = {
        "verdict": "REVISE",
        "review_path": review_path,
        "spec_path": spec_path,
        "cycle": cycle,
        "review_raw": _ALL_ALREADY_DONE_RAW,
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_review_doc")


def _write_spec(tmp_path: Path, name: str, files_body: str) -> str:
    spec_path = tmp_path / name
    spec_path.write_text(
        f"# Test Spec\n\n## Files\n{files_body}\n\n## §3 Acceptance Criteria\n",
        encoding="utf-8",
    )
    return str(spec_path)


def _reason_text(result: StepResult) -> str:
    return " ".join([
        str(result.error or ""),
        str((result.data or {}).get("findings", "")),
        json.dumps(result.data or {}),
    ])


_TD_MODIFY_FORBIDDEN = (
    "Do NOT modify `tests/test_x.py` — this file is frozen and off-limits. "
    "Two build attempts have already died exactly this way."
)

_TD_MENTION_NO_NEGATION = "See `tests/test_x.py` for the helper used by other tests."

_TD_REAL_RUN_SHAPE = (
    "In this same clause, CREATE `tests/test_new_helper.py` but do NOT "
    "modify `tests/test_old.py`"
)

_TD_MULTILINE_NO_BLANK = "Do NOT modify\n`tests/test_y.py`, this file is critical."

_TD_COLON_MODIFY = "Do NOT modify `tests/test_z.py`."
_TD_COLON_RENAME = "Do NOT change `tests/test_w.py` in any way."

_TD_AFFIRMATIVE_DECOY = "modify `lib/foo.py` to add the new branch"


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — SHIP + violated prohibition -> deterministic REVISE overrides SHIP
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_ship_with_violated_prohibition_routes_revise_below_cap(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec1.md", "- MODIFY tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC1 FAIL: a stated prohibition violated by a MODIFY entry must override "
        f"an LLM SHIP verdict into REVISE, but gate returned status={result.status!r} "
        f"(verdict in data={(result.data or {}).get('verdict')!r})"
    )
    assert result.error_code == "E_VALIDATION_RETRY", (
        f"AC1 FAIL: below-cap prohibition REVISE must carry error_code "
        f"'E_VALIDATION_RETRY', got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC1 FAIL: below-cap prohibition REVISE must be recoverable, got {result.recoverable!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — reason contains the offending path AND the MINIMAL negation->path
# span, NOT the whole clause (the trailing "off-limits" text must be absent).
# ═══════════════════════════════════════════════════════════════════════════


def test_ac2_reason_names_path_with_minimal_span_not_whole_clause(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec2.md", "- MODIFY tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)
    combined = _reason_text(result)

    assert "tests/test_x.py" in combined, (
        f"AC2 FAIL: reason must name the offending path 'tests/test_x.py', "
        f"got combined reason text: {combined!r}"
    )
    assert "Do NOT modify" in combined, (
        f"AC2 FAIL: reason must include the minimal negation->path span starting "
        f"at 'Do NOT modify', got combined reason text: {combined!r}"
    )
    assert "off-limits" not in combined, (
        f"AC2 FAIL: reason must carry the MINIMAL negation->path span, not the "
        f"whole clause (trailing 'off-limits' text must be excluded), got: "
        f"{combined!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — same prohibition, path listed as CREATE -> no refusal
# (regression pin — already holds on main today, no prohibition check exists)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac3_create_entry_never_triggers_prohibition(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec3.md", "- CREATE tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC3 FAIL: CREATE entry for a prohibited path must not trigger REVISE, "
        f"got status={result.status!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — path mentioned with no negation -> SHIP stands (regression pin)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4_mention_without_negation_never_triggers_prohibition(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec4.md", "- MODIFY tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MENTION_NO_NEGATION, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC4 FAIL: a mere mention of a path (no negation token) must not trigger "
        f"REVISE, got status={result.status!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC4b (gate MAJOR 3) — affirmative mutation decoy: verb present, NO negation
# -> SHIP stands. Forces the negation conjunct; a GREEN testing only for a
# mutation verb false-REVISEs this, the commonest instruction shape.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4b_affirmative_mutation_decoy_no_negation_ship_stands(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec4b.md", "- MODIFY: lib/foo.py")
    ctx = _ctx({"task_description": _TD_AFFIRMATIVE_DECOY, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC4b FAIL: an affirmative instruction ('modify lib/foo.py to add the new "
        f"branch') carries a mutation verb but NO negation token — SHIP must stand. "
        f"A GREEN checking only the mutation-verb conjunct false-REVISEs this. Got "
        f"status={result.status!r}, reason={_reason_text(result)!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — task_description absent -> byte-identical to today (SHIP and REVISE)
# (regression pin — both sub-cases already hold on main today, §1x legacy input)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac5_absent_task_description_unchanged_for_ship_and_revise(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec5.md", "- MODIFY tests/test_x.py")

    ctx = _ctx({"complexity": "SIMPLE"})
    ship_prev = _ship_prev(spec_path, gate_attempts={})
    ship_result = phase_45_spec._gate_on_review(ctx, ship_prev)
    assert ship_result.status == "ok", (
        f"AC5 FAIL: absent task_description must leave SHIP unchanged, "
        f"got status={ship_result.status!r}"
    )
    assert ship_result.data.get("verdict") == "SHIP"

    revise_prev = _revise_prev(spec_path)
    revise_result = phase_45_spec._gate_on_review(ctx, revise_prev)
    assert revise_result.status == "error", (
        f"AC5 FAIL: absent task_description must leave legacy REVISE behavior "
        f"unchanged, got status={revise_result.status!r}"
    )
    assert revise_result.error_code == "E_VALIDATION_RETRY", (
        f"AC5 FAIL: legacy first-cycle REVISE must still be E_VALIDATION_RETRY, "
        f"got {revise_result.error_code!r}"
    )
    assert revise_result.recoverable is True


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — LLM REVISE stays REVISE with its ORIGINAL REASON PRESERVED, regardless
# of a violated prohibition present in task_description (one-directional).
# ═══════════════════════════════════════════════════════════════════════════


def test_ac6_llm_revise_stays_revise_original_reason_preserved(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec6.md", "- MODIFY tests/test_x.py")

    ctx_with_prohibition = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    ctx_without_prohibition = _ctx({"complexity": "SIMPLE"})

    result_with = phase_45_spec._gate_on_review(ctx_with_prohibition, _revise_prev(spec_path, cycle=1))
    result_without = phase_45_spec._gate_on_review(ctx_without_prohibition, _revise_prev(spec_path, cycle=1))

    assert result_with.status == "error" and result_without.status == "error", (
        f"AC6 FAIL: both must be REVISE-shaped errors, got "
        f"{result_with.status!r} / {result_without.status!r}"
    )
    assert result_with.error_code == result_without.error_code == "E_VALIDATION_RETRY", (
        f"AC6 FAIL: expected error_code 'E_VALIDATION_RETRY' unchanged by the "
        f"prohibition check, got {result_with.error_code!r} / {result_without.error_code!r}"
    )
    assert result_with.error == result_without.error, (
        f"AC6 FAIL: the LLM REVISE's ORIGINAL reason must be preserved byte-identical "
        f"regardless of a violated prohibition in task_description; got "
        f"with-prohibition error={result_with.error!r} vs "
        f"without-prohibition error={result_without.error!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — real-run shape: one clause names BOTH files; REVISE names only the
# existing (MODIFY) one, the prescribed CREATE file appears nowhere.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_real_run_shape_revise_names_only_existing_file(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(
        tmp_path,
        "spec7.md",
        "- CREATE tests/test_new_helper.py\n- MODIFY tests/test_old.py",
    )
    ctx = _ctx({"task_description": _TD_REAL_RUN_SHAPE, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC7 FAIL: MODIFY of the prohibited existing file must trigger REVISE "
        f"even when the same clause also names the prescribed CREATE file, "
        f"got status={result.status!r}"
    )
    combined = _reason_text(result)
    assert "tests/test_old.py" in combined, (
        f"AC7 FAIL: reason must name the prohibited MODIFY file 'tests/test_old.py', "
        f"got: {combined!r}"
    )
    assert "tests/test_new_helper.py" not in combined, (
        f"AC7 FAIL: reason must NOT name the prescribed CREATE file "
        f"'tests/test_new_helper.py' anywhere in the result (false-positive case "
        f"the MODIFY/DELETE/RENAME-only rule and minimal-span reason both guard "
        f"against), got: {combined!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC7b (gate MAJOR 4) — multi-line prohibition: negation on one line, path on
# the NEXT line, no blank line between -> still REVISE. This is the actual
# defect shape; a `\n`-splitting GREEN is inert on it.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7b_multiline_prohibition_no_blank_line_still_revise(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec7b.md", "- MODIFY tests/test_y.py")
    ctx = _ctx({"task_description": _TD_MULTILINE_NO_BLANK, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC7b FAIL: negation and path on consecutive lines (no blank line "
        f"between) must still be recognized as ONE prohibitive clause and "
        f"trigger REVISE — this is the actual production defect shape. "
        f"task_description={_TD_MULTILINE_NO_BLANK!r}, got status={result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_RETRY", (
        f"AC7b FAIL: expected 'E_VALIDATION_RETRY', got {result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8 — at the cap, a prohibition hit yields terminal E_REVIEW_FAILED
# (§1i: pre-staged deterministically via gate_attempts={"spec_retry": 1},
#  matching the SAME cap/gate_attempts accounting already used for the
#  existing LLM-REVISE spec_retry cap at phase_45_spec.py:4482-4527)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8_prohibition_hit_at_cap_yields_terminal_e_review_failed(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8.md", "- MODIFY tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    # spec_gates cycle_cap == 1 for every build_class (workflows/_recoverable_policy.py;
    # bytedigger: bd#85 spends a deterministic spec gate under "spec_gates");
    # pre-staging gate_attempts={"spec_gates": 1} deterministically means attempts(1) >= cap(1).
    prev = _ship_prev(spec_path, gate_attempts={"spec_gates": 1})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC8 FAIL: at-cap prohibition hit must be terminal (status='error'), "
        f"got {result.status!r}"
    )
    assert result.error_code == "E_REVIEW_FAILED", (
        f"AC8 FAIL: at-cap prohibition hit must carry error_code 'E_REVIEW_FAILED', "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC8 FAIL: at-cap prohibition hit must be non-recoverable (terminal), "
        f"got {result.recoverable!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8b (gate MAJOR 6) — colon verb form MODIFY: <path> triggers; RENAME:
# <path> also triggers. Every draft-2 fixture used whitespace form only.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8b_colon_verb_form_modify_triggers(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8b1.md", "- MODIFY: tests/test_z.py")
    ctx = _ctx({"task_description": _TD_COLON_MODIFY, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC8b FAIL: colon-verb-form entry 'MODIFY: tests/test_z.py' must still "
        f"be recognized as a MODIFY hit — a GREEN capturing only '^(VERB)\\s+' "
        f"misses this (the literal shape from the observed production run). "
        f"Got status={result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_RETRY"


def test_ac8b_colon_verb_form_rename_triggers(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8b2.md", "- RENAME: tests/test_w.py")
    ctx = _ctx({"task_description": _TD_COLON_RENAME, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC8b FAIL: colon-verb-form entry 'RENAME: tests/test_w.py' must still "
        f"be recognized as a RENAME hit, got status={result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_RETRY"


# ═══════════════════════════════════════════════════════════════════════════
# AC8c (gate r1 MAJOR 5, RE-FIXTURED after r2 MAJOR 1 — the previous
# tests/test_x_helper.py decoy could not fail: neither path is a substring of
# the other). Genuine containment pair: prohibition names tests/test_x.py;
# spec MODIFIES SYSTEM/tests/test_x.py and tests/test_x.py.orig — entries the
# prohibited string genuinely IS a substring of -> SHIP stands (exact match
# only). Kills a substring GREEN (`prohibited in entry`/`entry in prohibited`/
# `startswith`/`endswith`), which would REVISE on either entry here.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8c_containment_decoy_ship_stands(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(
        tmp_path,
        "spec8c.md",
        "- MODIFY SYSTEM/tests/test_x.py\n- MODIFY tests/test_x.py.orig",
    )
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC8c FAIL: 'SYSTEM/tests/test_x.py' and 'tests/test_x.py.orig' merely "
        f"CONTAIN the prohibited 'tests/test_x.py' as a substring — neither is an "
        f"EXACT match, so SHIP must stand. A substring-matching GREEN "
        f"(prohibited-in-entry / entry-in-prohibited / startswith / endswith) "
        f"REVISEs on either entry here and fails. Got status={result.status!r}, "
        f"reason={_reason_text(result)!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC8e (gate r2 MAJOR 2) — bullet-list collapse decoy: two bullets, no
# terminal periods, negation+path on the SECOND bullet only. Since \n is
# correctly not a clause boundary this collapses into ONE clause; only
# path-like tokens occurring AFTER the negation token may enter `prohibited`.
# Kills a GREEN that collects every path token in a prohibitive clause
# (it would also prohibit lib/a.py, which precedes the negation and is
# correctly MODIFY-listed) -> SHIP stands.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8e_bullet_list_collapse_only_post_negation_tokens_prohibited(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    td = "Update `lib/a.py` to add the flag but do NOT touch `tests/test_x.py`"
    spec_path = _write_spec(tmp_path, "spec8e.md", "- MODIFY: lib/a.py")
    ctx = _ctx({"task_description": td, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC8e FAIL: 'lib/a.py' precedes the negation token in the (single, "
        f"newline-joined) clause and must NEVER enter the prohibited set — only "
        f"tokens AFTER 'do not' qualify. A GREEN collecting every path token in "
        f"the clause false-REVISEs the correctly-listed 'MODIFY: lib/a.py' entry. "
        f"Got status={result.status!r}, reason={_reason_text(result)!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC8f (§1w, gate r2 MINOR 4) — DELETE: <prohibited path> triggers REVISE.
# The spec checks MODIFY|DELETE|RENAME; no earlier AC covered DELETE, so a
# MODIFY|RENAME-only GREEN would pass the whole suite and miss production.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8f_delete_verb_form_triggers(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8f.md", "- DELETE tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC8f FAIL: a DELETE entry for a prohibited path must trigger REVISE "
        f"exactly like MODIFY/RENAME do — a MODIFY|RENAME-only GREEN misses "
        f"this. Got status={result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_RETRY", (
        f"AC8f FAIL: expected 'E_VALIDATION_RETRY', got {result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8g (gate r2 MINOR 5/6) — normalisation (leading './', trailing sentence
# period) and token breadth (negation token other than "do not", mutation
# verb other than "modify"/"change") — neither token set is pinned to a
# single member.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8g_leading_dotslash_normalised_and_matches(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8g1.md", "- MODIFY tests/test_x.py")
    ctx = _ctx({"task_description": "Do NOT modify `./tests/test_x.py`", "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC8g FAIL: a prohibition written with a leading './' must normalise "
        f"and still match the bare spec entry 'tests/test_x.py'. Got "
        f"status={result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_RETRY"


def test_ac8g_trailing_sentence_period_normalised_and_matches(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8g2.md", "- MODIFY tests/test_x.py")
    ctx = _ctx({
        "task_description": "For this build, do NOT modify tests/test_x.py.",
        "complexity": "SIMPLE",
    })
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC8g FAIL: a prohibition ending the sentence with a trailing period "
        f"('...tests/test_x.py.') must strip that trailing punctuation and "
        f"still match. Got status={result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_RETRY"


def test_ac8g_alternate_negation_and_verb_tokens_match(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8g3.md", "- MODIFY tests/test_y.py")
    ctx = _ctx({
        "task_description": "You must not touch `tests/test_y.py` under any circumstances",
        "complexity": "SIMPLE",
    })
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "error", (
        f"AC8g FAIL: negation token 'must not' and mutation verb 'touch' "
        f"(neither 'do not' nor 'modify'/'change') must still qualify a clause "
        f"as prohibitive — neither token set is pinned to a single member. "
        f"Got status={result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_RETRY"


# ═══════════════════════════════════════════════════════════════════════════
# AC8d — verbless entry: prohibition names the path, spec lists it with no
# verb -> SHIP stands (accepted miss, pinned as a decision).
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8d_verbless_entry_ship_stands_accepted_miss(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec8d.md", "- tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC8d FAIL: a verbless spec entry naming the prohibited path is a "
        f"knowingly accepted miss (engine's own specs are verbless) — SHIP must "
        f"stand, got status={result.status!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC8h (gate r3 MAJOR) — sub-case 1: mirror bullet order (prohibition FIRST,
# affirmative mutation SECOND). No blank line, no terminal periods -> one
# clause without the list-marker boundary. Kills a GREEN lacking the
# `^\s*([-*+]|\d+[.)])\s` list-marker clause boundary.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8h_mirror_bullet_order_ship_stands(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    td = "- Do NOT modify any existing test file\n- Add the new flag to `lib/config.py`"
    spec_path = _write_spec(tmp_path, "spec8h1.md", "- MODIFY: lib/config.py")
    ctx = _ctx({"task_description": td, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC8h sub-case 1 FAIL: 'lib/config.py' sits in a SEPARATE bullet from "
        f"the prohibition (mirror order — prohibition first) and must never "
        f"enter the prohibited set. A GREEN lacking the list-marker clause "
        f"boundary collapses both bullets into one clause and false-REVISEs "
        f"this correctly-listed 'MODIFY: lib/config.py' entry. Got "
        f"status={result.status!r}, reason={_reason_text(result)!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC8h sub-case 2 — blank-line-only clause boundary. No list markers, no
# terminal period on the affirmative paragraph — only the blank line
# separates it from the prohibitive paragraph. Kills a GREEN that splits only
# on `(?<=[.;])\s+` (which passes by accident when prose paragraphs happen to
# end in periods).
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8h_blank_line_only_boundary_ship_stands(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    td = "Do NOT modify any existing test file\n\nAdd the new flag to `lib/config.py`"
    spec_path = _write_spec(tmp_path, "spec8h2.md", "- MODIFY: lib/config.py")
    ctx = _ctx({"task_description": td, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC8h sub-case 2 FAIL: the affirmative paragraph naming 'lib/config.py' "
        f"has NO terminal period and NO list marker — only the blank line "
        f"separates it from the prohibitive paragraph. A GREEN splitting only "
        f"on sentence punctuation (no blank-line boundary) collapses both "
        f"paragraphs into one clause and false-REVISEs this correctly-listed "
        f"entry. Got status={result.status!r}, reason={_reason_text(result)!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC8h sub-case 3 (gate r5, item 1) — the `(?<=[.;])\s+` sentence-punctuation
# boundary is itself unpinned until this fixture: no list marker, no blank
# line — only sentence-punctuation splitting can save this ordinary
# two-sentence instruction from a false REVISE.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8h_sentence_punctuation_boundary_ship_stands(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    td = "Do not modify the tests. Update `lib/a.py`."
    spec_path = _write_spec(tmp_path, "spec8h3.md", "- MODIFY: lib/a.py")
    ctx = _ctx({"task_description": td, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC8h sub-case 3 FAIL: no list marker, no blank line separate the two "
        f"sentences — only the '(?<=[.;])\\s+' sentence-punctuation boundary "
        f"can put 'Do not modify the tests.' and 'Update `lib/a.py`.' in "
        f"separate clauses. A GREEN implementing only the list-marker and "
        f"blank-line boundaries collapses this into one clause and "
        f"false-REVISEs the correctly-listed 'MODIFY: lib/a.py' entry. Got "
        f"status={result.status!r}, reason={_reason_text(result)!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC8i (gate r5, item 2) — nearest-preceding-negation, not first-negation.
# One clause, two negations, one path BETWEEN them. "never mind about" is a
# negation token ("never") with NO mutation verb in its own local segment
# (before the next negation) -> its window contributes nothing. "do NOT
# modify" is the second negation, locally paired with "modify" -> its window
# is prohibitive. A first-negation-anchored GREEN (collect every path token
# after the FIRST negation in the whole clause, regardless of local verb
# pairing) wrongly sweeps `lib/a.py` into the prohibited set too (superset,
# biased toward false REVISE). The spec-mandated nearest-preceding-negation
# reading excludes `lib/a.py` -> SHIP stands.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8i_nearest_preceding_negation_not_first_negation(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    td = "Never mind about `lib/a.py` for now, but do NOT modify `tests/test_x.py`."
    spec_path = _write_spec(tmp_path, "spec8i.md", "- MODIFY: lib/a.py")
    ctx = _ctx({"task_description": td, "complexity": "SIMPLE"})
    prev = _ship_prev(spec_path, gate_attempts={})

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC8i FAIL: 'lib/a.py' sits between two negations ('never' and 'do "
        f"NOT modify') in one clause; it belongs to the FIRST negation's local "
        f"window, which has no mutation verb of its own ('mind about' is not a "
        f"mutation verb) — the nearest-preceding-negation rule must exclude "
        f"it. A first-negation-anchored GREEN that sweeps every path after the "
        f"first negation in the whole clause wrongly includes it (superset) "
        f"and false-REVISEs the correctly-listed 'MODIFY: lib/a.py' entry. Got "
        f"status={result.status!r}, reason={_reason_text(result)!r}"
    )
    assert result.data.get("verdict") == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC9 — parse_spec_files_allowlist is a pure projection of
# parse_spec_files_with_verbs, over a corpus covering EVERY _parse_items
# branch, plus the None (no section) vs [] (empty section) distinction.
# ═══════════════════════════════════════════════════════════════════════════

_AC9_CORPUS_BODY = (
    "- CREATE: alpha.py\n"
    "- MODIFY beta.py\n"
    "- DELETE: gamma.py\n"
    "- RENAME delta.py\n"
    "- MODIFY `epsilon.py` (annotation ignored, backtick wins)\n"
    "- zeta.py (trailing paren stripped, no backtick present)\n"
    "- eta.py,\n"
    "\n"
    "| `theta.py` | note |\n"
    "| note | `iota.py` |\n"
    "| kappa.py | plain-in-table |\n"
    "|---|---|\n"
    "- `## Files`\n"
    "- MODIFY lib/x-emdash.py — description text here\n"
    "| some description | more description |\n"
    "This prose line has no path token at all and must yield nothing.\n"
)

_EXPECTED_AC9_PATHS = [
    "alpha.py", "beta.py", "gamma.py", "delta.py", "epsilon.py",
    "zeta.py", "eta.py", "theta.py", "iota.py", "kappa.py",
    "lib/x-emdash.py",
]

_EXPECTED_AC9_VERBS = {
    "alpha.py": "CREATE",
    "beta.py": "MODIFY",
    "gamma.py": "DELETE",
    "delta.py": "RENAME",
    "epsilon.py": "MODIFY",
    "zeta.py": None,
    "eta.py": None,
    "theta.py": None,
    "iota.py": None,
    "kappa.py": None,
    "lib/x-emdash.py": "MODIFY",
}


def test_ac9_allowlist_parser_is_pure_projection_of_verb_preserving_parser(
    tmp_path: Path,
) -> None:
    from bytedigger_engine.lib import run_allowlist as run_allowlist

    with_verbs_fn = getattr(run_allowlist, "parse_spec_files_with_verbs", None)
    assert with_verbs_fn is not None, (
        "AC9 FAIL: lib.run_allowlist.parse_spec_files_with_verbs does not exist "
        f"yet (GREEN pending) — getattr returned {with_verbs_fn!r}"
    )

    spec_path = tmp_path / "corpus_spec.md"
    spec_path.write_text(
        f"# Test Spec\n\n## Files\n{_AC9_CORPUS_BODY}\n## §3 Acceptance Criteria\n",
        encoding="utf-8",
    )

    flat = run_allowlist.parse_spec_files_allowlist(str(spec_path))
    with_verbs = with_verbs_fn(str(spec_path))

    assert isinstance(with_verbs, list) and all(
        isinstance(t, tuple) and len(t) == 2 for t in with_verbs
    ), (
        f"AC9 FAIL: parse_spec_files_with_verbs must return a list of "
        f"(verb_or_None, path) tuples, got {with_verbs!r}"
    )

    projected = [path for _verb, path in with_verbs]
    assert projected == flat == _EXPECTED_AC9_PATHS, (
        f"AC9 FAIL: parse_spec_files_allowlist must be byte-identical to the "
        f"path-projection of parse_spec_files_with_verbs over a corpus covering "
        f"every _parse_items branch (bullet/verb-colon/verb-whitespace/all-four-"
        f"verbs/backtick-wins/parenthetical-no-backtick/trailing-comma/table-"
        f"backtick-first-cell/table-backtick-later-cell/table-plain/prose-skip/"
        f"blank-line/separator-row/non-path-backtick-segment/em-dash-first-token/"
        f"table-row-yields-nothing). flat={flat!r}, projected={projected!r}, "
        f"expected={_EXPECTED_AC9_PATHS!r}"
    )

    actual_verbs = {path: verb for verb, path in with_verbs}
    assert actual_verbs == _EXPECTED_AC9_VERBS, (
        f"AC9 FAIL: verb-per-path mapping wrong, got {actual_verbs!r}, "
        f"expected {_EXPECTED_AC9_VERBS!r}"
    )

    # None (no "## Files" section at all) vs [] (section present but empty).
    no_section_spec = tmp_path / "no_section_spec.md"
    no_section_spec.write_text("# Test Spec\n\n## §3 Acceptance Criteria\nNo files here.\n", encoding="utf-8")
    assert run_allowlist.parse_spec_files_allowlist(str(no_section_spec)) is None, (
        "AC9 FAIL: parse_spec_files_allowlist must return None for a spec with "
        "no '## Files' section at all"
    )
    assert with_verbs_fn(str(no_section_spec)) is None, (
        f"AC9 FAIL: parse_spec_files_with_verbs must ALSO report the None case "
        f"for a spec with no '## Files' section (phase_5_implement's scope "
        f"guard depends on this distinction at 6 call sites), got "
        f"{with_verbs_fn(str(no_section_spec))!r}"
    )

    empty_section_spec = tmp_path / "empty_section_spec.md"
    empty_section_spec.write_text(
        "# Test Spec\n\n## Files\n\n## §3 Acceptance Criteria\n", encoding="utf-8"
    )
    assert run_allowlist.parse_spec_files_allowlist(str(empty_section_spec)) == [], (
        "AC9 FAIL: parse_spec_files_allowlist must return [] (not None) for a "
        "present-but-empty '## Files' section"
    )
    assert with_verbs_fn(str(empty_section_spec)) == [], (
        f"AC9 FAIL: parse_spec_files_with_verbs must ALSO return [] for a "
        f"present-but-empty '## Files' section, got "
        f"{with_verbs_fn(str(empty_section_spec))!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC10 (§1l side effect, gate MAJOR 7) — position pinned from BOTH sides:
# on a hit, run_allowlist is NOT written; with no hit, it IS written.
# ═══════════════════════════════════════════════════════════════════════════


def _write_zones_cfg(cfg_path: Path) -> None:
    cfg_path.write_text(
        json.dumps({
            "version": 1,
            "enforce": False,
            "zones": {"allow": ["~/.claude"], "deny": []},
            "run_allowlist": [],
        }),
        encoding="utf-8",
    )


def test_ac10_run_allowlist_write_position_pinned_both_sides(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    # ── side A: prohibition HIT -> allowlist file must NOT be written ──────
    repo_dir_hit = tmp_path / "repo_hit"
    repo_dir_hit.mkdir()
    cfg_path_hit = tmp_path / "containment-zones-hit.json"
    _write_zones_cfg(cfg_path_hit)
    original_bytes_hit = cfg_path_hit.read_bytes()

    spec_path_hit = _write_spec(tmp_path, "spec10hit.md", "- MODIFY tests/test_x.py")
    ctx_hit = _ctx({
        "task_description": _TD_MODIFY_FORBIDDEN,
        "run_id": "testrun_gh1600d3_hit",
        "git_cwd": str(repo_dir_hit),
        "scope_zones_config": str(cfg_path_hit),
        "complexity": "SIMPLE",
    })
    prev_hit = _ship_prev(spec_path_hit, gate_attempts={})

    result_hit = phase_45_spec._gate_on_review(ctx_hit, prev_hit)

    assert result_hit.status == "error", (
        f"AC10 FAIL (hit side): expected REVISE on a violated prohibition, "
        f"got status={result_hit.status!r}"
    )
    assert cfg_path_hit.read_bytes() == original_bytes_hit, (
        "AC10 FAIL (hit side): write_run_allowlist_for_spec must NOT run when "
        "the prohibition check fires — a spec about to be rejected must not "
        "have an allowlist written for it. Config bytes changed."
    )

    # ── side B: no hit -> allowlist file IS written ─────────────────────────
    repo_dir_nohit = tmp_path / "repo_nohit"
    repo_dir_nohit.mkdir()
    cfg_path_nohit = tmp_path / "containment-zones-nohit.json"
    _write_zones_cfg(cfg_path_nohit)

    spec_path_nohit = _write_spec(tmp_path, "spec10nohit.md", "- x/y.py")
    ctx_nohit = _ctx({
        "run_id": "testrun_gh1600d3_nohit",
        "git_cwd": str(repo_dir_nohit),
        "scope_zones_config": str(cfg_path_nohit),
        "complexity": "SIMPLE",
        # no task_description -> no prohibition can fire
    })
    prev_nohit = _ship_prev(spec_path_nohit, gate_attempts={})

    result_nohit = phase_45_spec._gate_on_review(ctx_nohit, prev_nohit)

    assert result_nohit.status == "ok", (
        f"AC10 FAIL (no-hit side): SHIP with no prohibition must still return "
        f"ok, got status={result_nohit.status!r}"
    )
    on_disk = json.loads(cfg_path_nohit.read_text(encoding="utf-8"))
    expected_entry = str((repo_dir_nohit / "x/y.py").resolve())
    assert expected_entry in on_disk.get("run_allowlist", []), (
        f"AC10 FAIL (no-hit side): write_run_allowlist_for_spec must still write "
        f"the SHIP path's allowlist entry {expected_entry!r} when no prohibition "
        f"fires, got run_allowlist={on_disk.get('run_allowlist')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC11 (§1u) — the check runs ONLY in the SHIP branch: E_REVIEW_UNPARSEABLE
# and the ALREADY_DONE branch (which also returns ok) are unaffected by a
# violated prohibition.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac11_unparseable_and_already_done_branches_unaffected(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    spec_path = _write_spec(tmp_path, "spec11.md", "- MODIFY tests/test_x.py")
    ctx = _ctx({"task_description": _TD_MODIFY_FORBIDDEN, "complexity": "SIMPLE"})

    unparseable_result = phase_45_spec._gate_on_review(ctx, _unknown_blank_prev(spec_path))
    assert unparseable_result.status == "error", (
        f"AC11 FAIL: UNKNOWN + blank review_raw must still hit "
        f"E_REVIEW_UNPARSEABLE regardless of a violated prohibition, got "
        f"status={unparseable_result.status!r}"
    )
    assert unparseable_result.error_code == "E_REVIEW_UNPARSEABLE", (
        f"AC11 FAIL: expected error_code 'E_REVIEW_UNPARSEABLE' unaffected by "
        f"the prohibition check, got {unparseable_result.error_code!r}"
    )
    assert unparseable_result.recoverable is False

    already_done_result = phase_45_spec._gate_on_review(ctx, _already_done_prev(spec_path))
    assert already_done_result.status == "ok", (
        f"AC11 FAIL: ALREADY_DONE branch must still return ok regardless of a "
        f"violated prohibition, got status={already_done_result.status!r}"
    )
    assert already_done_result.data.get("verdict") == "ALREADY_DONE", (
        f"AC11 FAIL: expected verdict 'ALREADY_DONE' unaffected by the "
        f"prohibition check, got {already_done_result.data.get('verdict')!r}"
    )
