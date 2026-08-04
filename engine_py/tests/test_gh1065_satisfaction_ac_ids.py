"""RED tests for GH1065 (BE7C9CA0) — satisfaction AC-checklist id injection.

Spec: SHARED/memory/Decisions/2026-07-20_BE7C9CA0_gh1065_satisfaction_ac_ids_spec.md
Class: SYSTEMATIC. Tier: Option-D (modifies existing engine_py prod
`workflows/phase_6_review.py`).

The new module-level helper `_ac_checklist_ids_directive` (spec §2.1) and its
append into BOTH AC_CHECKLIST twins of `_build_satisfaction_prompt` (§2.3) DO
NOT EXIST YET. Per §1q extension D1CF5FDF every lookup of the new symbol is
deferred INSIDE the test bodies via `getattr(p6, "...", None)` so this file
COLLECTS cleanly and FAILS at assert time, never at collect time.
`phase_6_review`, `StepResult` and `WorkflowContext` already exist and are
imported at module scope (mirrors test_gh388_ac_checklist_satisfaction.py /
test_GH749_ac_parity_single_source.py — conftest's import-time singleton
provides sys.path, no module-level sys.path manipulation, §1q / 81F97F3D).

§1g / gate MAJOR-3: this file contains NO id parser of its own. There is no
regex re-derivation of ids anywhere. Every expected id set comes DIRECTLY from
`p6._parse_spec_ac_ids(...)`; assertions compare `f"AC{id}"` tokens built from
that output. `_boundary_tokens` below is a punctuation splitter used only for
word-boundary-safe membership (so `AC1` cannot match inside `AC100`) — it
normalizes nothing and derives nothing.

§1l bearing ACs: AC8, AC9, AC11, AC13 — each exercises the REAL production
surface (`p6._build_satisfaction_prompt(ctx, prev)` against a REAL spec file on
disk, or the real helper at n=200) and asserts on the REAL returned string.
Neither `_build_satisfaction_prompt`, `_parse_spec_ac_ids` nor
`_verify_ac_checklist` is ever mocked.

§1i: no shared singleton/time state — every fixture is built fresh per test on
its own `tmp_path`; env-gate toggling is monkeypatch-scoped to one test.

§1j: tmp dirs are wrapped in `os.path.realpath` (macOS /var/folders symlink).

Pre-GREEN classification: 13 FAIL — AC1-AC11, AC13, AC14 (helper absent /
directive absent from the emitted prompt). 1 PASS — AC12, the §3 regression pin
that `_parse_spec_ac_ids` behavior is unchanged; it must stay green after GREEN
too. Total 14 test functions.
"""
from __future__ import annotations

import os
from pathlib import Path

from bytedigger_engine.workflows import phase_6_review as p6
from bytedigger_engine.contracts import StepResult, WorkflowContext

# ─────────────────────────────────────────────────────────────────────────────
# Frozen pre-change AC_CHECKLIST block (gate MAJOR-2)
# ─────────────────────────────────────────────────────────────────────────────

# Verbatim reconstruction of workflows/phase_6_review.py L2532-2539 (COMPLEX
# twin) — byte-for-byte identical to L2571-2578 (SIMPLE twin), which is exactly
# why §1w demands both be patched. The interpolated constants are resolved to
# their literal values on purpose: this constant is the FROZEN pre-change text,
# so a change to AC_CHECKLIST_HEADER / AC_CHECKLIST_ENTRY_FORMAT must also trip
# these assertions.
_FROZEN_AC_CHECKLIST_BLOCK = (
    "  ## AC Checklist\n"
    "  <one bullet per Acceptance Criterion in the SPEC's ## Acceptance Criteria\n"
    "   section, in spec order, format exactly: `- AC<id>: PASS|FAIL — <path:line evidence>`.\n"
    "   Judge each AC on the IMPLEMENTATION's behavior (Read the code;\n"
    "   spec/impl drift is OK when the behavior satisfies the AC's intent). Any\n"
    "   FAIL must also appear under Concerns. If the spec has no ## Acceptance\n"
    "   Criteria section, write `- none`.>\n"
    "\n"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — spec texts
# ─────────────────────────────────────────────────────────────────────────────

# The GH1065 repro: a numbered AC list carrying inline AC labels in the prose.
# `_parse_spec_ac_ids` takes the numbered-list branch FIRST and therefore yields
# the ORDINALS "1".."13" — never the prose labels ("AC13a" etc.).
_REPRO_SPEC_TEXT = (
    "# GH1065 repro spec\n\n"
    "## Acceptance Criteria\n\n"
    "1. **AC1 — engine emits X**\n"
    "2. **AC2 — engine emits Y**\n"
    "3. **AC3 — parser tolerates bold labels**\n"
    "4. **AC4 — empty text degrades**\n"
    "5. **AC5 — table format honored**\n"
    "6. **AC6 — order and dedup preserved**\n"
    "7. **AC7 — unreadable spec degrades**\n"
    "8. **AC8 — COMPLEX twin carries the directive**\n"
    "9. **AC9 — SIMPLE twin carries the directive**\n"
    "10. **AC10 — single-source id parse**\n"
    "11. **AC11 — end-to-end parity**\n"
    "12. **AC12 — parser regression pin**\n"
    "13. **AC13a — retry path**\n\n"
    "## Verify plan\n"
    "14. this ordinal must NOT be collected\n"
)

_EXPECTED_REPRO_IDS = [str(n) for n in range(1, 14)]

_TABLE_SPEC_TEXT = (
    "# table spec\n\n"
    "## Acceptance Criteria\n\n"
    "| AC | Behavior |\n"
    "|---|---|\n"
    "| AC-7 | do the thing |\n\n"
    "## Next\n"
)

_MIXED_ORDER_SPEC_TEXT = (
    "## Acceptance Criteria\n"
    "3. third comes first in this spec\n"
    "1) then the one\n"
    "3. duplicate ordinal, must dedup\n"
    "2. and finally two\n\n"
    "## Open Questions\n"
)

_NO_AC_SECTION_SPEC_TEXT = "# Spec\n\nNo acceptance criteria section anywhere here.\n"

# AC13 (gate MAJOR-1) — n=200, far above any plausible `ids[:20]` / `ids[:50]`
# cap a GREEN might reach for.
_BIG_N = 200
_BIG_SPEC_TEXT = (
    "# big spec\n\n"
    "## Acceptance Criteria\n\n"
    + "".join(f"{n}. criterion number {n} must be enumerated\n" for n in range(1, _BIG_N + 1))
    + "\n## Verify plan\n"
    "1. this ordinal lives outside the AC section\n"
)
_EXPECTED_BIG_IDS = [str(n) for n in range(1, _BIG_N + 1)]

# Elision markers a truncating/summarising directive would introduce (§2.1
# no-truncation clause). Checked only INSIDE the id span so that legitimate
# prose punctuation outside the enumeration cannot false-fail.
_ELISION_MARKERS = ("...", "…", "–", "—", " more", "etc")


# ─────────────────────────────────────────────────────────────────────────────
# Shared builders (fixture shapes mirror test_gh388_ac_checklist_satisfaction.py)
# ─────────────────────────────────────────────────────────────────────────────


def _real(tmp_path: Path) -> Path:
    """§1j — macOS /var/folders symlink: resolve before any subprocess/path compare."""
    return Path(os.path.realpath(str(tmp_path)))


def _make_ctx(tmp_path: Path, *, complexity: "str | None" = None, threshold: int = 70) -> WorkflowContext:
    root = _real(tmp_path)
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        "scratchpad_dir": str(scratch),
        "satisfaction_threshold": threshold,
    }
    if complexity is not None:
        cfg["complexity"] = complexity
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="GH1065 satisfaction ac ids test",
        session_id="test-GH1065",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prompt_prev(tmp_path: Path, *, spec_text: "str | None") -> StepResult:
    """Real on-disk spec/review/fix docs + the prev StepResult _build_satisfaction_prompt reads.

    `spec_text=None` writes NO spec file at all (AC7: missing/unreadable spec).
    """
    root = _real(tmp_path)
    scratch = root / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    specs_dir = scratch / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: SUSPECT\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")

    spec_path = specs_dir / "build-spec.md"
    if spec_text is not None:
        spec_path.write_text(spec_text, encoding="utf-8")

    return StepResult(
        status="ok",
        data={
            "prompt": "dummy prompt",
            "doc_path": str(sat_doc),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "prompt_bytes": 12,
        },
        duration_ms=0,
        step_name="build_satisfaction_prompt",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Boundary-safe membership (NOT an id parser — §1g / gate MAJOR-3)
# ─────────────────────────────────────────────────────────────────────────────

_ALNUM = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def _boundary_tokens(text: str) -> set:
    """Split `text` on every non-alphanumeric character.

    This derives NOTHING and normalizes NOTHING — it exists only so that
    `"AC1" in tokens` is word-boundary safe (a plain substring test would match
    `AC1` inside `AC100`). Every fixture in this file parses to purely numeric
    ids, so an `AC<id>` token is never split by this rule.
    """
    return set("".join(c if c in _ALNUM else " " for c in (text or "")).split())


def _named_ac_tokens(text: str) -> set:
    """The `AC<digit…>` tokens literally present in `text`.

    Used only for exactness comparisons against a set built from
    `_parse_spec_ac_ids` output. No stripping, no case folding, no regex.
    """
    return {t for t in _boundary_tokens(text) if t.startswith("AC") and len(t) > 2 and t[2] in "0123456789"}


def _expected_tokens(ids) -> set:
    return {f"AC{i}" for i in ids}


def _assert_ids_in_parse_order(directive: str, ids, ac: str) -> None:
    """Order pin via substring index ordering — no re-derivation of ids."""
    for a, b in zip(ids, ids[1:]):
        ia = directive.index(f"AC{a}")
        ib = directive.index(f"AC{b}")
        assert ia < ib, (
            f"GH1065 {ac} FAIL: directive must name ids in `_parse_spec_ac_ids` order; "
            f"AC{a} (index {ia}) must precede AC{b} (index {ib}); directive={directive!r}"
        )


def _resolve_directive_fn():
    """§1q/D1CF5FDF — deferred lookup of the not-yet-existing helper."""
    fn = getattr(p6, "_ac_checklist_ids_directive", None)
    assert fn is not None, (
        "GH1065 FAIL: module-level helper `_ac_checklist_ids_directive(spec_text)` "
        "is not implemented in workflows/phase_6_review.py (spec §2.1)"
    )
    return fn


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — numbered-list repro spec (ids 1..13) -> directive names AC1…AC13
# ═══════════════════════════════════════════════════════════════════════════

def test_ac1_directive_names_every_parsed_id_for_numbered_repro_spec() -> None:
    fn = _resolve_directive_fn()

    parsed = p6._parse_spec_ac_ids(_REPRO_SPEC_TEXT)
    assert parsed == _EXPECTED_REPRO_IDS, (
        f"GH1065 AC1 setup: the repro spec must parse to the ordinals 1..13 "
        f"(that IS the defect); got {parsed!r}"
    )

    directive = fn(_REPRO_SPEC_TEXT)
    assert directive.strip(), (
        "GH1065 AC1 FAIL: directive must be non-empty for a spec whose AC section parses to ids"
    )

    tokens = _boundary_tokens(directive)
    missing = [i for i in parsed if f"AC{i}" not in tokens]
    assert not missing, (
        f"GH1065 AC1 FAIL: directive must name every id returned by _parse_spec_ac_ids "
        f"as AC<id>; missing {missing!r} in directive {directive!r}"
    )
    assert _named_ac_tokens(directive) == _expected_tokens(parsed), (
        f"GH1065 AC1 FAIL: directive must name EXACTLY the parsed ids and no others; "
        f"expected {sorted(_expected_tokens(parsed))!r}, "
        f"got {sorted(_named_ac_tokens(directive))!r}"
    )
    _assert_ids_in_parse_order(directive, parsed, "AC1")


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — directive mandates EXACTLY those ids + ignore prose-embedded labels
# ═══════════════════════════════════════════════════════════════════════════

def test_ac2_directive_mandates_exact_ids_and_ignoring_prose_labels() -> None:
    fn = _resolve_directive_fn()

    directive = fn(_REPRO_SPEC_TEXT)
    low = directive.lower()
    assert "exact" in low, (
        f"GH1065 AC2 FAIL: directive must instruct the model to use EXACTLY these ids "
        f"(no 'exact...' wording found); got {directive!r}"
    )
    assert "ignore" in low, (
        f"GH1065 AC2 FAIL: directive must instruct the model to IGNORE ids embedded in "
        f"AC prose; got {directive!r}"
    )
    assert "label" in low, (
        f"GH1065 AC2 FAIL: directive must name the failure mode — labels embedded in the "
        f"AC prose (the GH1065 defect: model echoed `AC13a` from the prose); "
        f"got {directive!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — spec with no '## Acceptance Criteria' section -> ""
# ═══════════════════════════════════════════════════════════════════════════

def test_ac3_directive_empty_when_no_ac_section() -> None:
    fn = _resolve_directive_fn()

    assert p6._parse_spec_ac_ids(_NO_AC_SECTION_SPEC_TEXT) == [], "GH1065 AC3 setup"
    assert fn(_NO_AC_SECTION_SPEC_TEXT) == "", (
        f"GH1065 AC3 FAIL: no AC section must degrade to '' so the prompt stays "
        f"byte-identical to today's; got {fn(_NO_AC_SECTION_SPEC_TEXT)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — empty / whitespace-only spec text -> ""
# ═══════════════════════════════════════════════════════════════════════════

def test_ac4_directive_empty_for_empty_or_whitespace_spec_text() -> None:
    fn = _resolve_directive_fn()

    for text in ("", "   ", "\n\n\t\n"):
        assert fn(text) == "", (
            f"GH1065 AC4 FAIL: empty/whitespace-only spec text must return ''; "
            f"got {fn(text)!r} for {text!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — table-format spec (| AC-7 |) -> directive names AC7 (label id)
# ═══════════════════════════════════════════════════════════════════════════

def test_ac5_directive_names_label_ids_for_table_format_spec() -> None:
    fn = _resolve_directive_fn()

    parsed = p6._parse_spec_ac_ids(_TABLE_SPEC_TEXT)
    assert parsed == ["7"], f"GH1065 AC5 setup: got {parsed!r}"

    directive = fn(_TABLE_SPEC_TEXT)
    assert "AC7" in _boundary_tokens(directive), (
        f"GH1065 AC5 FAIL: table-format spec must yield a directive naming AC7 "
        f"(label id, not an ordinal); got {directive!r}"
    )
    assert _named_ac_tokens(directive) == _expected_tokens(parsed), (
        f"GH1065 AC5 FAIL: directive must name exactly {sorted(_expected_tokens(parsed))!r}; "
        f"got {sorted(_named_ac_tokens(directive))!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — parse order + dedup mirrored exactly
# ═══════════════════════════════════════════════════════════════════════════

def test_ac6_directive_preserves_parse_order_and_dedup() -> None:
    fn = _resolve_directive_fn()

    parsed = p6._parse_spec_ac_ids(_MIXED_ORDER_SPEC_TEXT)
    assert parsed == ["3", "1", "2"], f"GH1065 AC6 setup: got {parsed!r}"

    directive = fn(_MIXED_ORDER_SPEC_TEXT)
    assert _named_ac_tokens(directive) == _expected_tokens(parsed), (
        f"GH1065 AC6 FAIL: directive ids must mirror _parse_spec_ac_ids output exactly "
        f"(deduped); expected {sorted(_expected_tokens(parsed))!r}, "
        f"got {sorted(_named_ac_tokens(directive))!r}"
    )
    _assert_ids_in_parse_order(directive, parsed, "AC6")


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — missing/unreadable spec file: no raise, prompt built, AC_CHECKLIST
#       block region BYTE-IDENTICAL to the frozen pre-change literal
# ═══════════════════════════════════════════════════════════════════════════

def test_ac7_missing_spec_file_degrades_without_raising(tmp_path: Path) -> None:
    _resolve_directive_fn()  # the degrade path only exists once the helper does

    ctx = _make_ctx(tmp_path, complexity="COMPLEX")
    prev = _make_prompt_prev(tmp_path, spec_text=None)
    spec_path = Path(prev.data["spec_path"])
    assert not spec_path.exists(), "GH1065 AC7 setup: spec file must be absent"

    result = p6._build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"GH1065 AC7 FAIL: an unreadable spec must degrade to '' and never raise / error; "
        f"got status={result.status!r} error={getattr(result, 'error', None)!r}"
    )
    prompt = (result.data or {}).get("prompt", "")
    assert p6.AC_CHECKLIST_HEADER in prompt, (
        "GH1065 AC7 FAIL: prompt must still be built with its AC_CHECKLIST block"
    )
    assert "SCORE:" in prompt and "# Satisfaction Evaluation" in prompt, (
        "GH1065 AC7 FAIL: existing control literals must be preserved"
    )
    assert _FROZEN_AC_CHECKLIST_BLOCK in prompt, (
        "GH1065 AC7 FAIL (byte-identity): with no readable spec the AC_CHECKLIST block "
        "must be BYTE-IDENTICAL to the frozen pre-change literal — no directive text, "
        "no whitespace drift. §2.1: 'prompt is byte-identical to today's'.\n"
        f"expected block={_FROZEN_AC_CHECKLIST_BLOCK!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8 [bearing, §1l] — COMPLEX twin prompt carries the directive with parsed ids
# ═══════════════════════════════════════════════════════════════════════════

def test_ac8_complex_branch_prompt_contains_directive_with_parsed_ids(tmp_path: Path) -> None:
    fn = _resolve_directive_fn()

    ctx = _make_ctx(tmp_path, complexity="COMPLEX")
    prev = _make_prompt_prev(tmp_path, spec_text=_REPRO_SPEC_TEXT)

    result = p6._build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"GH1065 AC8 setup FAIL (COMPLEX): {getattr(result, 'error', None)!r}"
    )
    prompt = (result.data or {}).get("prompt", "")

    directive = fn(_REPRO_SPEC_TEXT)
    assert directive.strip(), "GH1065 AC8 setup: directive must be non-empty for the repro spec"
    assert directive.strip() in prompt, (
        "GH1065 AC8 FAIL: the COMPLEX twin's AC_CHECKLIST block must carry the id "
        f"directive verbatim. directive={directive!r}"
    )
    tokens = _boundary_tokens(prompt)
    missing = [i for i in p6._parse_spec_ac_ids(_REPRO_SPEC_TEXT) if f"AC{i}" not in tokens]
    assert not missing, (
        f"GH1065 AC8 FAIL: COMPLEX prompt must name every parsed AC id; missing {missing!r}"
    )
    assert p6.AC_CHECKLIST_HEADER in prompt, "GH1065 AC8 FAIL: AC_CHECKLIST block lost"


# ═══════════════════════════════════════════════════════════════════════════
# AC9 [bearing, §1l] — SIMPLE twin prompt carries the directive too (§1w twin)
# ═══════════════════════════════════════════════════════════════════════════

def test_ac9_simple_branch_prompt_contains_directive_with_parsed_ids(tmp_path: Path) -> None:
    fn = _resolve_directive_fn()

    ctx = _make_ctx(tmp_path, complexity=None)  # non-COMPLEX -> SIMPLE twin
    prev = _make_prompt_prev(tmp_path, spec_text=_REPRO_SPEC_TEXT)

    result = p6._build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"GH1065 AC9 setup FAIL (SIMPLE): {getattr(result, 'error', None)!r}"
    )
    prompt = (result.data or {}).get("prompt", "")
    assert "Write tool" in prompt, (
        "GH1065 AC9 setup: expected the SIMPLE/default twin (Write-tool OUTPUT block)"
    )

    directive = fn(_REPRO_SPEC_TEXT)
    assert directive.strip() in prompt, (
        "GH1065 AC9 FAIL: the SIMPLE twin's AC_CHECKLIST block must carry the id directive "
        f"too — a one-sided fix leaves SIMPLE builds broken (§1w). directive={directive!r}"
    )
    tokens = _boundary_tokens(prompt)
    missing = [i for i in p6._parse_spec_ac_ids(_REPRO_SPEC_TEXT) if f"AC{i}" not in tokens]
    assert not missing, (
        f"GH1065 AC9 FAIL: SIMPLE prompt must name every parsed AC id; missing {missing!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC10 — §1g single source: the directive's ids come from _parse_spec_ac_ids
# ═══════════════════════════════════════════════════════════════════════════

def test_ac10_directive_ids_come_from_parse_spec_ac_ids_single_source(monkeypatch) -> None:
    fn = _resolve_directive_fn()

    sentinel = ["42", "99"]
    calls: list = []

    def _sentinel_parse(spec_text):
        calls.append(spec_text)
        return list(sentinel)

    # Forcing function: a SECOND/parallel id parser inside the helper would not
    # see this patch and would still emit 1..13 for the repro spec.
    monkeypatch.setattr(p6, "_parse_spec_ac_ids", _sentinel_parse)

    directive = fn(_REPRO_SPEC_TEXT)
    assert calls, (
        "GH1065 AC10 FAIL: `_ac_checklist_ids_directive` must call `_parse_spec_ac_ids` "
        "— a second id parser is forbidden (§1g); the patched parser was never invoked"
    )
    assert _named_ac_tokens(directive) == _expected_tokens(sentinel), (
        f"GH1065 AC10 FAIL: directive ids must be exactly _parse_spec_ac_ids(spec_text); "
        f"expected {sorted(_expected_tokens(sentinel))!r}, "
        f"got {sorted(_named_ac_tokens(directive))!r}"
    )
    _assert_ids_in_parse_order(directive, sentinel, "AC10")


# ═══════════════════════════════════════════════════════════════════════════
# AC11 [bearing, §1l] — end-to-end: prompt directive ids == _verify_ac_checklist ids
# ═══════════════════════════════════════════════════════════════════════════

def test_ac11_prompt_directive_ids_satisfy_verify_ac_checklist(tmp_path: Path, monkeypatch) -> None:
    fn = _resolve_directive_fn()
    # gate must be ENABLED, else _verify_ac_checklist returns skip(env_disabled)
    # and the assertion below would be vacuous (unset ⇒ enabled, config_provider).
    monkeypatch.delenv("HAL_AC_CHECKLIST_GATE", raising=False)
    assert p6._verify_ac_checklist(_REPRO_SPEC_TEXT, "no checklist")[0] != "skip", (
        "GH1065 AC11 setup: HAL_AC_CHECKLIST_GATE must be enabled for this test"
    )

    ctx = _make_ctx(tmp_path, complexity="COMPLEX")
    prev = _make_prompt_prev(tmp_path, spec_text=_REPRO_SPEC_TEXT)
    result = p6._build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", f"GH1065 AC11 setup FAIL: {getattr(result, 'error', None)!r}"
    prompt = (result.data or {}).get("prompt", "")

    directive = fn(_REPRO_SPEC_TEXT)
    assert directive.strip() and directive.strip() in prompt, (
        "GH1065 AC11 FAIL: the emitted prompt must carry the id directive"
    )

    # §1g single source: the ids the model is told to use ARE the gate's ids.
    ids = p6._parse_spec_ac_ids(_REPRO_SPEC_TEXT)
    assert ids, "GH1065 AC11 setup: repro spec must parse to a non-empty id list"
    assert _named_ac_tokens(directive) == _expected_tokens(ids), (
        f"GH1065 AC11 FAIL: directive id set must equal the id set the gate demands; "
        f"directive={sorted(_named_ac_tokens(directive))!r} "
        f"gate={sorted(_expected_tokens(ids))!r}"
    )

    # A model that echoes the directive's ids verbatim must pass the cross-check.
    response = (
        "# Satisfaction Evaluation\n\n"
        f"{p6.AC_CHECKLIST_HEADER}\n"
        + "".join(f"- AC{i}: PASS — f.py:1 evidence\n" for i in ids)
        + "\n## Concerns\n- none\n\nSCORE: 95\nVERDICT: PASS\n"
    )
    verdict, detail = p6._verify_ac_checklist(_REPRO_SPEC_TEXT, response)
    assert verdict == "pass", (
        f"GH1065 AC11 FAIL: a response echoing the prompt directive's ids must pass "
        f"_verify_ac_checklist (that is the whole point — parsed-ids == checklist-ids); "
        f"got verdict={verdict!r} detail={detail!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC12 — regression pin: _parse_spec_ac_ids behavior unchanged (§3)
# ═══════════════════════════════════════════════════════════════════════════

def test_ac12_parse_spec_ac_ids_behavior_unchanged() -> None:
    # numbered fixture: ordinals, section-scoped, deduped, order preserved
    assert p6._parse_spec_ac_ids(_REPRO_SPEC_TEXT) == _EXPECTED_REPRO_IDS, (
        "GH1065 AC12 FAIL: numbered-list parse changed — §3 forbids touching the regexes"
    )
    assert p6._parse_spec_ac_ids(_MIXED_ORDER_SPEC_TEXT) == ["3", "1", "2"], (
        "GH1065 AC12 FAIL: numbered order/dedup behavior changed"
    )
    # table fixture: label ids
    assert p6._parse_spec_ac_ids(_TABLE_SPEC_TEXT) == ["7"], (
        "GH1065 AC12 FAIL: table-row parse changed"
    )
    # no section / empty
    assert p6._parse_spec_ac_ids(_NO_AC_SECTION_SPEC_TEXT) == [], (
        "GH1065 AC12 FAIL: absent-section behavior changed"
    )
    assert p6._parse_spec_ac_ids("") == [], "GH1065 AC12 FAIL: empty-text behavior changed"


# ═══════════════════════════════════════════════════════════════════════════
# AC13 [bearing, §1l · gate MAJOR-1] — NO TRUNCATION at n=200
# ═══════════════════════════════════════════════════════════════════════════

def test_ac13_directive_names_all_ids_no_truncation() -> None:
    """A cap (`ids[:20]`), an elision ('…and 180 more') or a range collapse
    (`AC1–AC200`) silently reinstates `parsed-ids ≠ checklist-ids` for every
    spec above the cap — i.e. re-opens the exact class this ship closes."""
    fn = _resolve_directive_fn()

    parsed = p6._parse_spec_ac_ids(_BIG_SPEC_TEXT)
    assert parsed == _EXPECTED_BIG_IDS, (
        f"GH1065 AC13 setup: the n={_BIG_N} fixture must parse to ordinals 1..{_BIG_N}; "
        f"got {len(parsed)} ids, head={parsed[:5]!r} tail={parsed[-5:]!r}"
    )

    directive = fn(_BIG_SPEC_TEXT)
    assert directive.strip(), "GH1065 AC13 FAIL: directive must be non-empty at n=200"

    tokens = _boundary_tokens(directive)
    missing = [i for i in parsed if f"AC{i}" not in tokens]
    assert not missing, (
        f"GH1065 AC13 FAIL (no-truncation): the directive must name ALL {_BIG_N} parsed "
        f"ids. {len(missing)} missing, first 10: {missing[:10]!r}. Capping / eliding / "
        f"sampling the id list is FORBIDDEN (§2.1)."
    )

    named = _named_ac_tokens(directive)
    assert named == _expected_tokens(parsed), (
        f"GH1065 AC13 FAIL: directive must name EXACTLY the {_BIG_N} parsed ids and no "
        f"others; extra={sorted(named - _expected_tokens(parsed))[:10]!r}"
    )
    assert len(named) == _BIG_N, (
        f"GH1065 AC13 FAIL: expected {_BIG_N} distinct AC<id> tokens; got {len(named)}"
    )
    # explicit boundary samples: `AC1` must not be satisfied by `AC100`/`AC199`
    for sample in ("1", "2", "57", "100", "137", "199", str(_BIG_N)):
        assert f"AC{sample}" in tokens, (
            f"GH1065 AC13 FAIL: sample id AC{sample} absent from the directive"
        )

    _assert_ids_in_parse_order(directive, parsed, "AC13")

    # No elision marker anywhere inside the enumerated span (prose outside the
    # id list may legitimately contain punctuation, so the span is scoped).
    first = directive.index(f"AC{parsed[0]}")
    last = directive.rindex(f"AC{parsed[-1]}") + len(f"AC{parsed[-1]}")
    span = directive[first:last]
    for marker in _ELISION_MARKERS:
        assert marker not in span, (
            f"GH1065 AC13 FAIL (no-truncation): elision marker {marker!r} found inside the "
            f"id enumeration — the directive must list every id literally, with no "
            f"'…and N more' summary and no en/em-dash range collapse. span head="
            f"{span[:160]!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# AC14 [gate MAJOR-2] — no AC section ⇒ AC_CHECKLIST block byte-identical,
#                       BOTH twins
# ═══════════════════════════════════════════════════════════════════════════

def test_ac14_no_ac_section_prompt_block_byte_identical(tmp_path: Path) -> None:
    _resolve_directive_fn()  # the no-ids degrade path only exists once the helper does

    assert p6._parse_spec_ac_ids(_NO_AC_SECTION_SPEC_TEXT) == [], "GH1065 AC14 setup"

    for twin, complexity in (("COMPLEX", "COMPLEX"), ("SIMPLE", None)):
        sub = tmp_path / f"twin-{twin}"
        sub.mkdir(parents=True, exist_ok=True)
        ctx = _make_ctx(sub, complexity=complexity)
        prev = _make_prompt_prev(sub, spec_text=_NO_AC_SECTION_SPEC_TEXT)

        result = p6._build_satisfaction_prompt(ctx, prev)
        assert result.status == "ok", (
            f"GH1065 AC14 setup FAIL ({twin}): {getattr(result, 'error', None)!r}"
        )
        prompt = (result.data or {}).get("prompt", "")

        if twin == "COMPLEX":
            assert "OUTPUT — your response IS the file content of" in prompt, (
                "GH1065 AC14 setup: expected the COMPLEX twin"
            )
        else:
            assert "Write tool" in prompt, "GH1065 AC14 setup: expected the SIMPLE twin"

        assert _FROZEN_AC_CHECKLIST_BLOCK in prompt, (
            f"GH1065 AC14 FAIL ({twin} twin, byte-identity): a spec with no "
            f"## Acceptance Criteria section parses to zero ids, so the AC_CHECKLIST "
            f"block MUST be BYTE-IDENTICAL to the frozen pre-change literal — no "
            f"directive line, no empty 'ids: ' stub, no whitespace drift (§2.1). "
            f"expected block={_FROZEN_AC_CHECKLIST_BLOCK!r}"
        )
        assert prompt.count(_FROZEN_AC_CHECKLIST_BLOCK) == 1, (
            f"GH1065 AC14 FAIL ({twin} twin): the AC_CHECKLIST block must appear exactly "
            f"once; got {prompt.count(_FROZEN_AC_CHECKLIST_BLOCK)}"
        )
