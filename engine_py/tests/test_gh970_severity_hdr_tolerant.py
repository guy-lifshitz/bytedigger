"""RED tests for GH970 — tolerant SEVERITY-header parse + role-report malformed
header lint.

Spec: SHARED/memory/Decisions/2026-07-17_GH970_severity_hdr_tolerant_spec.md.

Pre-GREEN, the following are still true of production code:
  - `_AGG_SEVERITY_HDR_RE` (phase_6_review.py:1031) hard-anchors to exactly
    `###` — `##`/`####` SEVERITY headers are structurally invisible.
  - `SEVERITY_HDR_LINE_RE` / `SEVERITY_HDR_MULTILINE_RE` / `SEVERITY_HDR_CORE`
    do not exist yet in `plugins.review_schema.canonical`.
  - `lint_role_report` does not exist yet.
  - `phase_6_review.py:1628` still does a bare `.startswith("### SEVERITY:")`
    string check, not a regex match — so the AC8 tag-injection assertion for
    a `##`-form suspect header fails today.
  - `_AGG_SEVERITY_HDR_RE` / `_SEVERITY_HDR_RE` (helper.py, semantic_verifier.py)
    are locally-defined `re.compile(...)` objects, not aliases of the canonical
    `SEVERITY_HDR_*_RE` singletons, so the AC4 identity asserts fail today.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest  # noqa: F401 — pytest discovery convention

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from contracts import StepResult  # noqa: E402
from phase_6_review import _aggregate_review_findings, _parse_role_findings  # noqa: E402
import phase_6_review as _p6  # noqa: E402 — top-level alias for monkeypatching _emit_safe.
# CRITICAL: patch _p6 (the top-level "phase_6_review" module), NOT
# "workflows.phase_6_review" — _aggregate_review_findings's globals resolve to
# the top-level module (see test_906e37dc_review_findings_audit.py:358-363).


# ─── helpers ────────────────────────────────────────────────────────────────


def _write_role(reviews_dir: Path, slug: str, content: str) -> None:
    reviews_dir.mkdir(parents=True, exist_ok=True)
    (reviews_dir / f"role-{slug}.md").write_text(content, encoding="utf-8")


def _ctx(scratchpad: Path):
    return types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratchpad)},
        question="test question",
    )


def _prev_ok(scratchpad: Path | None = None) -> StepResult:
    data: dict = {}
    if scratchpad is not None:
        data["scratchpad"] = str(scratchpad)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="x")


def _run_agg(ctx, scratchpad: Path) -> tuple[StepResult, str]:
    result = _aggregate_review_findings(ctx, _prev_ok(scratchpad))
    content = (result.data or {}).get("aggregated_content", "") or ""
    return result, content


# ─── AC1: ## SEVERITY: form now parses (1 finding, HIGH, "t") ───────────────


def test_ac1_double_hash_severity_header_parses(tmp_path):
    """AC1: `_parse_role_findings("## SEVERITY: HIGH — t\\n> f.py:1: x\\n")`
    returns exactly 1 finding with severity HIGH, title 't'.

    Today's `_AGG_SEVERITY_HDR_RE` hard-anchors to `^###\\s+SEVERITY:` (3
    hashes) — a `##` header produces zero findings. MUST FAIL today.
    """
    content = "## SEVERITY: HIGH — t\n> f.py:1: x\n"
    findings = _parse_role_findings(content)
    assert len(findings) == 1, (
        f"AC1: expected 1 finding for '## SEVERITY:' header, got {len(findings)}. "
        f"Pre-GREEN, _AGG_SEVERITY_HDR_RE requires exactly '###' -> 0 findings."
    )
    assert findings[0]["severity"] == "HIGH"
    assert findings[0]["title"] == "t"


# ─── AC2: #### SEVERITY: parses; # SEVERITY: (single hash) does NOT ─────────


def test_ac2_quad_hash_parses_single_hash_does_not(tmp_path):
    """AC2: `#### SEVERITY:` form parses (1 finding); `# SEVERITY: HIGH — t`
    (single hash, below the 2-4 floor) returns 0 findings; `##### SEVERITY:`
    (5 hashes, above the 2-4 ceiling) also returns 0 findings.

    The `#### SEVERITY:` half MUST FAIL today (hard-anchored to exactly
    `###`). The `# SEVERITY:` and `##### SEVERITY:` halves already return 0
    today (also fail the `###`-only regex) — regression/ceiling guards, not
    forcing ACs, but they pin the {2,4} bound so a GREEN using `#{2,}` (no
    upper bound) would fail this test.
    """
    quad = "#### SEVERITY: HIGH — t\n> f.py:1: x\n"
    findings_quad = _parse_role_findings(quad)
    assert len(findings_quad) == 1, (
        f"AC2: expected 1 finding for '#### SEVERITY:' header, got "
        f"{len(findings_quad)}. Pre-GREEN, _AGG_SEVERITY_HDR_RE requires "
        f"exactly '###' -> 0 findings."
    )
    assert findings_quad[0]["severity"] == "HIGH"
    assert findings_quad[0]["title"] == "t"

    single = "# SEVERITY: HIGH — t\n> f.py:1: x\n"
    findings_single = _parse_role_findings(single)
    assert len(findings_single) == 0, (
        f"AC2: single-hash '# SEVERITY:' must NOT parse (below the 2-4 hash "
        f"floor), got {len(findings_single)} findings."
    )

    quintuple = "##### SEVERITY: HIGH — t\n> f.py:1: x\n"
    findings_quintuple = _parse_role_findings(quintuple)
    assert len(findings_quintuple) == 0, (
        f"AC2: five-hash '##### SEVERITY:' must NOT parse (above the 2-4 "
        f"hash ceiling), got {len(findings_quintuple)} findings. Pins the "
        f"{{2,4}} upper bound against an unbounded '#{{2,}}' GREEN."
    )


# ─── AC3: ### SEVERITY: still parses (regression, hyphen + em-dash) ─────────


def test_ac3_triple_hash_regression_hyphen_and_emdash(tmp_path):
    """AC3: `### SEVERITY:` form still parses with both a hyphen separator
    and an em-dash separator. This already passes today (regression guard,
    not a forcing AC) — kept to lock the unchanged baseline behavior.
    """
    hyphen = "### SEVERITY: HIGH - t\n> f.py:1: x\n"
    emdash = "### SEVERITY: HIGH — t\n> f.py:1: x\n"
    findings_hyphen = _parse_role_findings(hyphen)
    findings_emdash = _parse_role_findings(emdash)
    assert len(findings_hyphen) == 1, "AC3: '###' + hyphen must still parse"
    assert findings_hyphen[0]["severity"] == "HIGH"
    assert findings_hyphen[0]["title"] == "t"
    assert len(findings_emdash) == 1, "AC3: '###' + em-dash must still parse"
    assert findings_emdash[0]["severity"] == "HIGH"
    assert findings_emdash[0]["title"] == "t"


# ─── AC4: identity closure — call-site aliases ARE the canonical singleton ──


def test_ac4_identity_closure_canonical_singleton_aliases(tmp_path):
    """AC4 (§1l forcing function — object-identity, unfakeable by a stub):

    `phase_6_review._AGG_SEVERITY_HDR_RE is
     review_schema.canonical.SEVERITY_HDR_MULTILINE_RE`
    and the same `is`-identity for `anti_hallucination.helper._SEVERITY_HDR_RE`
    and `anti_hallucination.semantic_verifier._SEVERITY_HDR_RE`, each against
    `review_schema.canonical.SEVERITY_HDR_LINE_RE`.

    MUST FAIL today: none of `SEVERITY_HDR_LINE_RE` / `SEVERITY_HDR_MULTILINE_RE`
    / `SEVERITY_HDR_CORE` exist yet in canonical.py, and the three call sites
    are locally-defined `re.compile(...)` objects, not aliases.
    """
    canonical = importlib.import_module("plugins.review_schema.canonical")

    assert hasattr(canonical, "SEVERITY_HDR_MULTILINE_RE"), (
        "AC4: plugins.review_schema.canonical.SEVERITY_HDR_MULTILINE_RE "
        "does not exist yet (presence gate)."
    )
    assert hasattr(canonical, "SEVERITY_HDR_LINE_RE"), (
        "AC4: plugins.review_schema.canonical.SEVERITY_HDR_LINE_RE "
        "does not exist yet (presence gate)."
    )

    assert _p6._AGG_SEVERITY_HDR_RE is canonical.SEVERITY_HDR_MULTILINE_RE, (
        "AC4: phase_6_review._AGG_SEVERITY_HDR_RE must be the SAME object as "
        "review_schema.canonical.SEVERITY_HDR_MULTILINE_RE (identity, not "
        "equal-value). Pre-GREEN it is a locally-compiled regex."
    )

    helper_mod = importlib.import_module(
        "lib.plugins.anti_hallucination.helper"
    )
    assert helper_mod._SEVERITY_HDR_RE is canonical.SEVERITY_HDR_LINE_RE, (
        "AC4: anti_hallucination.helper._SEVERITY_HDR_RE must be the SAME "
        "object as review_schema.canonical.SEVERITY_HDR_LINE_RE. Pre-GREEN "
        "it is a locally-compiled regex."
    )

    semverify_mod = importlib.import_module(
        "lib.plugins.anti_hallucination.semantic_verifier"
    )
    assert semverify_mod._SEVERITY_HDR_RE is canonical.SEVERITY_HDR_LINE_RE, (
        "AC4: anti_hallucination.semantic_verifier._SEVERITY_HDR_RE must be "
        "the SAME object as review_schema.canonical.SEVERITY_HDR_LINE_RE. "
        "Pre-GREEN it is a locally-compiled regex."
    )


# ─── AC5: lint_role_report flags malformed, spares well-formed ─────────────


def test_ac5_lint_role_report_flags_malformed_spares_well_formed(tmp_path):
    """AC5: `lint_role_report` flags `# SEVERITY: HIGH — x`,
    `**SEVERITY: HIGH — x**`, bare `SEVERITY: HIGH — x`; does NOT flag
    `## SEVERITY: HIGH — x`, `### SEVERITY: HIGH — x`, `Severity: HIGH`
    (no dash), or `<!-- role-findings-count: 2 -->`.

    MUST FAIL today: `lint_role_report` does not exist yet in canonical.py
    (presence gate).
    """
    canonical = importlib.import_module("plugins.review_schema.canonical")
    assert hasattr(canonical, "lint_role_report"), (
        "AC5: plugins.review_schema.canonical.lint_role_report does not "
        "exist yet (presence gate)."
    )
    lint_role_report = canonical.lint_role_report

    malformed_lines = [
        "# SEVERITY: HIGH — x",
        "**SEVERITY: HIGH — x**",
        "SEVERITY: HIGH — x",
    ]
    well_formed_lines = [
        "## SEVERITY: HIGH — x",
        "### SEVERITY: HIGH — x",
        "Severity: HIGH",
        "<!-- role-findings-count: 2 -->",
    ]
    content = "\n".join(malformed_lines + well_formed_lines) + "\n"

    flagged = lint_role_report(content)

    for line in malformed_lines:
        assert any(line in fl for fl in flagged), (
            f"AC5: malformed line {line!r} was not flagged. Flagged: {flagged!r}"
        )
    for line in well_formed_lines:
        assert not any(line == fl or line in fl for fl in flagged), (
            f"AC5: well-formed/body line {line!r} must NOT be flagged. "
            f"Flagged: {flagged!r}"
        )


# ─── AC6: aggregate step, well-formed ## SEVERITY: block, no lint event ────


def test_ac6_aggregate_double_hash_finding_no_malformed_event(tmp_path, monkeypatch):
    """AC6: role file with one `## SEVERITY: HIGH — t` block -> findings_count
    >= 1 AND no `role_report_malformed` event emitted.

    findings_count only counts VERIFIED findings (phase_6_review.py:1570), so
    the quoted citation must resolve to a real file+line — matching the
    verified-citation fixture pattern in test_phase_6_1F39FB1A (good_file
    written under tmp_path/scratch, cited by absolute path).

    MUST FAIL today: `## SEVERITY:` is invisible to `_AGG_SEVERITY_HDR_RE`
    (findings_count == 0, the block is never parsed into a finding at all).
    """
    spy: list[tuple[str, dict]] = []
    monkeypatch.setattr(_p6, "_emit_safe", lambda et, p: spy.append((et, p)))

    scratchpad = tmp_path / "scratch"
    good_file = scratchpad / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("x = 1\n", encoding="utf-8")

    reviews = scratchpad / "reviews"
    _write_role(
        reviews, "a",
        "# a Review\n\n"
        f"## SEVERITY: HIGH — t\n"
        f"> {good_file}:1: x = 1\n"
        "Confidence: HIGH\n"
        "Description: desc\n\n"
        "VERDICT: PARTIAL\n"
        "<!-- role-findings-count: 1 -->\n",
    )

    ctx = _ctx(scratchpad)
    result, _content = _run_agg(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
    findings_count = (result.data or {}).get("findings_count", 0)
    assert findings_count >= 1, (
        f"AC6: expected findings_count >= 1 for a '## SEVERITY:' block, got "
        f"{findings_count}. Pre-GREEN, '##' is invisible to the aggregator."
    )

    malformed_events = [e for e in spy if e[0] == "role_report_malformed"]
    assert not malformed_events, (
        f"AC6: no role_report_malformed event expected for a well-formed "
        f"'## SEVERITY:' block, got {malformed_events!r}"
    )


# ─── AC7: aggregate step, malformed # SEVERITY: header -> lint event + audit ─


def test_ac7_aggregate_single_hash_malformed_emits_event_and_audit(tmp_path, monkeypatch):
    """AC7: role file with `# SEVERITY: HIGH — t` (malformed — below 2-4
    hash floor) -> `role_report_malformed` emitted with role slug + count 1,
    AND aggregated_content contains the '⚠ AUDIT WARNING:' + 'malformed
    SEVERITY header' literal, AND findings_audit['malformed_headers'] == 1.

    §1y: Point=`_aggregate_review_findings` per-role loop (~L1452-1467);
    Host=`_aggregate_review_findings` (called directly with ctx/scratchpad
    fixture); Test-path=role file on disk with the malformed header ->
    loop iterates it -> lint_role_report non-empty -> emit + audit line.

    MUST FAIL today: `_emit_safe("role_report_malformed", ...)` call and
    `findings_audit["malformed_headers"]` key do not exist yet.
    """
    spy: list[tuple[str, dict]] = []
    monkeypatch.setattr(_p6, "_emit_safe", lambda et, p: spy.append((et, p)))

    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"
    _write_role(
        reviews, "malformed-role",
        "# malformed-role Review\n\n"
        "# SEVERITY: HIGH — t\n"
        "> f.py:1: x = 1\n"
        "Confidence: HIGH\n"
        "Description: desc\n\n"
        "VERDICT: PARTIAL\n"
        "<!-- role-findings-count: 1 -->\n",
    )

    ctx = _ctx(scratchpad)
    result, content = _run_agg(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    malformed_events = [e for e in spy if e[0] == "role_report_malformed"]
    assert len(malformed_events) == 1, (
        f"AC7: expected exactly 1 role_report_malformed event, got "
        f"{len(malformed_events)}. All events: {[e[0] for e in spy]}"
    )
    _et, payload = malformed_events[0]
    assert payload.get("role") == "malformed-role", (
        f"AC7: expected role='malformed-role', got {payload.get('role')!r}"
    )
    assert payload.get("count") == 1, (
        f"AC7: expected count=1, got {payload.get('count')!r}"
    )

    assert "⚠ AUDIT WARNING:" in content, (
        f"AC7: expected '⚠ AUDIT WARNING:' literal in aggregated_content, "
        f"got:\n{content[:800]}"
    )
    assert "malformed SEVERITY header" in content, (
        f"AC7: expected 'malformed SEVERITY header' literal in "
        f"aggregated_content, got:\n{content[:800]}"
    )

    audit = (result.data or {}).get("findings_audit") or {}
    assert audit.get("malformed_headers") == 1, (
        f"AC7: expected findings_audit['malformed_headers'] == 1, got "
        f"{audit.get('malformed_headers')!r}. Full audit: {audit!r}"
    )


# ─── AC8: suspect-block tag injection on a ## SEVERITY: first line ─────────


def test_ac8_suspect_tag_injection_on_double_hash_header(tmp_path, monkeypatch):
    """AC8: a suspect finding whose block starts `## SEVERITY: HIGH — t` gets
    ` [verify: ` appended to its FIRST line in aggregated_content (L1628 fix).

    §1y: Point=phase_6_review.py:1628 header-tag branch (today: bare
    `block_lines[0].startswith("### SEVERITY:")` string check — a `##`
    header never matches, so the tag is never appended); Host=
    `_aggregate_review_findings` (suspect rendering, same step fn);
    Test-path=fixture role file with a `## SEVERITY:` block whose quoted
    file does not exist -> suspect-file-not-found -> lands in suspect
    findings -> tag-injection branch is reached but its guard rejects the
    `##` line today.

    MUST FAIL today: the L1628 startswith("### SEVERITY:") guard is False
    for a `##` header, so no ` [verify: ` tag is appended to that line.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"
    # Quoted file path does not exist on disk -> suspect-file-not-found ->
    # finding is rendered in the suspect-findings section with a tag.
    _write_role(
        reviews, "suspect",
        "# suspect Review\n\n"
        "## SEVERITY: HIGH — t\n"
        "> /nonexistent/gh970_ac8_fixture.py:1: x = 1\n"
        "Confidence: HIGH\n"
        "Description: desc\n\n"
        "VERDICT: PARTIAL\n"
        "<!-- role-findings-count: 1 -->\n",
    )

    ctx = _ctx(scratchpad)
    result, content = _run_agg(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    header_lines = [
        line for line in content.splitlines() if line.startswith("## SEVERITY: HIGH — t")
    ]
    assert header_lines, (
        f"AC8: expected a '## SEVERITY: HIGH — t' line in aggregated_content, "
        f"got:\n{content[:800]}"
    )
    assert any(" [verify: " in line for line in header_lines), (
        f"AC8: expected ' [verify: ' appended to the '## SEVERITY:' suspect "
        f"header line, got lines: {header_lines!r}"
    )


# ─── AC9: PER_ROLE_SCHEMA_TEMPLATE still contains ### SEVERITY: literal ────


def test_ac9_per_role_schema_template_unchanged_prescribed_form(tmp_path):
    """AC9: `PER_ROLE_SCHEMA_TEMPLATE` still contains the literal
    '### SEVERITY:' (prescribed form unchanged by D1). Already true today —
    regression guard, not a forcing AC.
    """
    canonical = importlib.import_module("plugins.review_schema.canonical")
    assert "### SEVERITY:" in canonical.PER_ROLE_SCHEMA_TEMPLATE, (
        "AC9: PER_ROLE_SCHEMA_TEMPLATE must still contain the '### SEVERITY:' "
        "literal (prescribed form unchanged)."
    )


# ─── AC10: helper / semantic_verifier _SEVERITY_HDR_RE tolerant match ──────


def test_ac10_helper_and_semantic_verifier_tolerant_match(tmp_path):
    """AC10: `helper._SEVERITY_HDR_RE.match("## SEVERITY: HIGH — t")` and
    `semantic_verifier._SEVERITY_HDR_RE.match(...)` are both truthy, with
    group(1) == "HIGH".

    MUST FAIL today: both are hard-anchored to `^### SEVERITY:` (helper.py:39)
    / `^###\\s+SEVERITY:` (semantic_verifier.py:34) — a `##` header does not
    match either.
    """
    helper_mod = importlib.import_module(
        "lib.plugins.anti_hallucination.helper"
    )
    semverify_mod = importlib.import_module(
        "lib.plugins.anti_hallucination.semantic_verifier"
    )

    line = "## SEVERITY: HIGH — t"

    helper_match = helper_mod._SEVERITY_HDR_RE.match(line)
    assert helper_match, (
        f"AC10: helper._SEVERITY_HDR_RE must match '## SEVERITY: HIGH — t'. "
        f"Pre-GREEN it is hard-anchored to exactly '###'."
    )
    assert helper_match.group(1) == "HIGH", (
        f"AC10: helper match group(1) must be 'HIGH', got "
        f"{helper_match.group(1)!r}"
    )

    semverify_match = semverify_mod._SEVERITY_HDR_RE.match(line)
    assert semverify_match, (
        f"AC10: semantic_verifier._SEVERITY_HDR_RE must match "
        f"'## SEVERITY: HIGH — t'. Pre-GREEN it is hard-anchored to exactly "
        f"'###'."
    )
    assert semverify_match.group(1) == "HIGH", (
        f"AC10: semantic_verifier match group(1) must be 'HIGH', got "
        f"{semverify_match.group(1)!r}"
    )
