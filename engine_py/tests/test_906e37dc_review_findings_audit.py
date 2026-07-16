"""RED tests for 906E37DC — phase_6 composite-reviewer findings-count audit trail.

Spec: SHARED/memory/Decisions/2026-05-12_906E37DC_review_findings_audit_spec.md.

All assertions here FAIL against the current production code because:
  - `_aggregate_review_findings` does not emit a `## Findings Audit` section
  - `StepResult.data` does not carry a `findings_audit` key
  - `_ROLE_SELFCOUNT_RE` constant does not exist yet
  - `_build_review_prompt` does not include `role-findings-count:` or the
    INVISIBLE / NOT REPORTED prose-warning text
"""
from __future__ import annotations

import types
import sys
from pathlib import Path

import pytest  # noqa: F401  — pytest discovery convention

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from contracts import StepResult  # noqa: E402
from phase_6_review import _aggregate_review_findings, _build_review_prompt  # noqa: E402
import workflows.phase_6_review as _p6  # noqa: E402  — for monkeypatching _emit_safe


# ─── helpers ─────────────────────────────────────────────────────────────────


def _write_role(
    reviews_dir: Path,
    slug: str,
    *,
    blocks: list[tuple[str, str, str]],
    selfcount: int | None,
) -> None:
    """Write reviews_dir/role-<slug>.md.

    blocks: list of (severity, title, quote) triples.
    The quote is written verbatim as the `> <quote>` evidence line.
    selfcount: if not None, appended as `<!-- role-findings-count: N -->` (last line).
    """
    reviews_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# {slug} Review", ""]
    for severity, title, quote in blocks:
        lines.append(f"### SEVERITY: {severity} — {title}")
        lines.append(f"> {quote}")
        lines.append("Confidence: HIGH")
        lines.append("Description: x")
        lines.append("")
    lines.append("VERDICT: PARTIAL")
    if selfcount is not None:
        lines.append(f"<!-- role-findings-count: {selfcount} -->")
    (reviews_dir / f"role-{slug}.md").write_text("\n".join(lines), encoding="utf-8")


def _ctx(tmp_path: Path):
    """Minimal ctx. _resolve_scratchpad reads org_config['scratchpad_dir'].
    _build_review_prompt also reads ctx.question."""
    return types.SimpleNamespace(
        org_config={"scratchpad_dir": str(tmp_path)},
        question="test question",
    )


def _prev_ok(scratchpad: Path | None = None) -> StepResult:
    """Minimal prev StepResult — empty data is fine (forwarded dict)."""
    data: dict = {}
    if scratchpad is not None:
        data["scratchpad"] = str(scratchpad)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="x")


def _run(ctx, tmp_path: Path) -> tuple[StepResult, str]:
    """Call _aggregate_review_findings and return (result, aggregated_content)."""
    result = _aggregate_review_findings(ctx, _prev_ok(tmp_path))
    content = (result.data or {}).get("aggregated_content", "") or ""
    return result, content


# ─── AC1: consistent self-count == parsed blocks, no warning ─────────────────


def test_ac1_consistent_selfcount_no_warning(tmp_path):
    """AC1: role-a(3 blocks, count=3) + role-b(2 blocks, count=2).

    Expects:
      - '## Findings Audit' in aggregated_content
      - 'self-reported (sub-reviewer counts): 5' in content
      - 'parsed as ### SEVERITY: blocks: 5' in content
      - NO '⚠ AUDIT WARNING' line
      - data['findings_audit']['consistent'] is True
      - data['findings_audit']['lost_to_prose'] == 0
    """
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    # 1F39FB1A co-change: relative paths now return suspect-file-not-found (honest,
    # not benefit-of-the-doubt kept). All 5 findings are suspect, so filtered=5,
    # aggregated=0. consistent = (5==5 and 5==0) = False.
    _write_role(reviews, "a", blocks=[
        ("HIGH", "h1", "some/file.py:1: pass"),
        ("MEDIUM", "m1", "some/file.py:2: x = 1"),
        ("LOW", "l1", "some/file.py:3: return x"),
    ], selfcount=3)
    _write_role(reviews, "b", blocks=[
        ("MEDIUM", "m2", "other/file.py:4: foo()"),
        ("LOW", "l2", "other/file.py:5: bar()"),
    ], selfcount=2)

    ctx = _ctx(scratchpad)
    result, content = _run(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    assert "## Findings Audit" in content, (
        f"'## Findings Audit' not found in aggregated_content:\n{content[:600]}"
    )
    assert "self-reported (sub-reviewer counts): 5" in content, (
        f"self-reported count missing in:\n{content[:600]}"
    )
    assert "parsed as ### SEVERITY: blocks: 5" in content, (
        f"parsed-blocks count missing in:\n{content[:600]}"
    )
    assert "⚠ AUDIT WARNING" not in content, (
        "No AUDIT WARNING expected when self-count matches parsed-blocks"
    )

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "findings_audit key missing from StepResult.data"
    # 1F39FB1A: consistent=False because filtered=5 (all findings now suspect).
    assert audit["consistent"] is False, (
        f"1F39FB1A: consistent=False (relative paths now suspect, filtered=5!=0), "
        f"got {audit['consistent']}"
    )
    assert audit["lost_to_prose"] == 0, f"expected lost_to_prose=0, got {audit['lost_to_prose']}"


# ─── AC2: self-count inflated → AUDIT WARNING ────────────────────────────────


def test_ac2_overclaimed_selfcount_warning(tmp_path):
    """AC2: role-a claims 5 but has 2 blocks; role-b has 1 block + count=1.

    Expects warning with correct numbers and lost_to_prose=3, consistent=False.
    """
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    _write_role(reviews, "a", blocks=[
        ("HIGH", "h1", "some/file.py:1: pass"),
        ("MEDIUM", "m1", "some/file.py:2: x = 1"),
    ], selfcount=5)   # claims 5, only 2 blocks
    _write_role(reviews, "b", blocks=[
        ("LOW", "l1", "other/file.py:4: foo()"),
    ], selfcount=1)

    ctx = _ctx(scratchpad)
    result, content = _run(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    assert "## Findings Audit" in content, "'## Findings Audit' missing"
    assert "self-reported (sub-reviewer counts): 6" in content, (
        f"Expected self-reported=6:\n{content[:600]}"
    )
    assert "parsed as ### SEVERITY: blocks: 3" in content, (
        f"Expected parsed=3:\n{content[:600]}"
    )
    assert "⚠ AUDIT WARNING: 3 finding(s) self-reported but not emitted as ### SEVERITY: blocks" in content, (
        f"Expected AUDIT WARNING with 3:\n{content[:600]}"
    )

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "findings_audit key missing from StepResult.data"
    assert audit["lost_to_prose"] == 3, f"expected lost_to_prose=3, got {audit.get('lost_to_prose')}"
    assert audit["consistent"] is False, f"expected consistent=False, got {audit.get('consistent')}"


# ─── AC3: backward-compat — no selfcount → n/a, no warning ──────────────────


def test_ac3_no_selfcount_backward_compat(tmp_path):
    """AC3: role files with blocks but NO <!-- role-findings-count --> comment.

    Expects: ## Findings Audit present, self-reported=n/a, correct block count,
    no WARNING, findings_audit['self_reported'] is None, ['consistent'] is None,
    ['lost_to_prose'] == 0.
    """
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    _write_role(reviews, "a", blocks=[
        ("HIGH", "h1", "some/file.py:1: pass"),
    ], selfcount=None)
    _write_role(reviews, "b", blocks=[
        ("LOW", "l1", "other/file.py:4: foo()"),
    ], selfcount=None)

    ctx = _ctx(scratchpad)
    result, content = _run(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    assert "## Findings Audit" in content, "'## Findings Audit' missing"
    assert "self-reported (sub-reviewer counts): n/a" in content, (
        f"Expected 'n/a' for self-reported:\n{content[:600]}"
    )
    assert "parsed as ### SEVERITY: blocks: 2" in content, (
        f"Expected parsed=2:\n{content[:600]}"
    )
    assert "⚠ AUDIT WARNING" not in content, (
        "No AUDIT WARNING expected when self-count absent"
    )

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "findings_audit key missing from StepResult.data"
    assert audit["self_reported"] is None, (
        f"expected self_reported=None, got {audit.get('self_reported')}"
    )
    assert audit["consistent"] is None, (
        f"expected consistent=None, got {audit.get('consistent')}"
    )
    assert audit["lost_to_prose"] == 0, (
        f"expected lost_to_prose=0, got {audit.get('lost_to_prose')}"
    )


# ─── AC4: findings_audit dict has required keys + correct types ───────────────


def test_ac4_findings_audit_schema(tmp_path):
    """AC4: findings_audit always has the required keys with correct types."""
    required_keys = {
        "self_reported", "parsed_blocks", "aggregated", "filtered",
        "role_files", "consistent", "lost_to_prose",
    }

    # Setup 1: consistent counts (AC1-like)
    scratchpad1 = tmp_path / "s1"
    reviews1 = scratchpad1 / "reviews"
    _write_role(reviews1, "a", blocks=[
        ("HIGH", "h1", "some/file.py:1: pass"),
    ], selfcount=1)
    result1 = _aggregate_review_findings(_ctx(scratchpad1), _prev_ok(scratchpad1))
    audit1 = (result1.data or {}).get("findings_audit")
    assert audit1 is not None, "findings_audit missing in AC4/setup1"
    assert set(audit1.keys()) >= required_keys, (
        f"Missing keys in AC4/setup1: {required_keys - set(audit1.keys())}"
    )
    assert isinstance(audit1["parsed_blocks"], int)
    assert isinstance(audit1["lost_to_prose"], int)
    assert audit1["consistent"] in (True, False, None)

    # Setup 2: overclaimed (AC2-like)
    scratchpad2 = tmp_path / "s2"
    reviews2 = scratchpad2 / "reviews"
    _write_role(reviews2, "a", blocks=[
        ("HIGH", "h1", "some/file.py:1: pass"),
    ], selfcount=5)
    result2 = _aggregate_review_findings(_ctx(scratchpad2), _prev_ok(scratchpad2))
    audit2 = (result2.data or {}).get("findings_audit")
    assert audit2 is not None, "findings_audit missing in AC4/setup2"
    assert set(audit2.keys()) >= required_keys, (
        f"Missing keys in AC4/setup2: {required_keys - set(audit2.keys())}"
    )
    assert isinstance(audit2["parsed_blocks"], int)
    assert isinstance(audit2["lost_to_prose"], int)
    assert audit2["consistent"] in (True, False, None)

    # Setup 3: no selfcount (AC3-like)
    scratchpad3 = tmp_path / "s3"
    reviews3 = scratchpad3 / "reviews"
    _write_role(reviews3, "a", blocks=[
        ("MEDIUM", "m1", "some/file.py:2: x = 1"),
    ], selfcount=None)
    result3 = _aggregate_review_findings(_ctx(scratchpad3), _prev_ok(scratchpad3))
    audit3 = (result3.data or {}).get("findings_audit")
    assert audit3 is not None, "findings_audit missing in AC4/setup3"
    assert set(audit3.keys()) >= required_keys, (
        f"Missing keys in AC4/setup3: {required_keys - set(audit3.keys())}"
    )
    assert isinstance(audit3["parsed_blocks"], int)
    assert isinstance(audit3["lost_to_prose"], int)
    assert audit3["consistent"] in (True, False, None)


# ─── AC5: aggregated reflects quote-filter drops ─────────────────────────────


def test_ac5_aggregated_reflects_quote_filter(tmp_path):
    """AC5: both findings become suspect (1F39FB1A pivot changes relative-path behavior).

    1F39FB1A co-change: 'OK-UNVERIFIABLE-RELATIVE' no longer means KEEP.
    Post-pivot, relative nonexistent path → suspect-file-not-found (honest).
    Absolute nonexistent path → suspect-file-not-found.
    Both findings are now suspect, so aggregated=0, filtered=2.

    self-count=2, parsed_blocks=2, but aggregated=0, filtered=2.
    lost_to_prose==0, consistent=False (filtered != 0).

    Note on the verify-quote filter (_verify_finding_quote ~L1242):
    - Relative path that doesn't exist in cwd → suspect-file-not-found (1F39FB1A: was kept).
    - Absolute path that doesn't exist → FILE-NOT-FOUND → suspect-file-not-found.
    Both blocks are now suspect.
    """
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    _write_role(reviews, "a", blocks=[
        # This block's quote has a relative path not on disk → suspect-file-not-found.
        # 1F39FB1A: was KEPT (OK-UNVERIFIABLE-RELATIVE benefit-of-doubt), now suspect (honest).
        ("HIGH", "good-finding", "relative/nonexistent_file.py:1: pass"),
        # This block's quote has an absolute nonexistent path → FILE-NOT-FOUND → suspect.
        ("MEDIUM", "fabricated-finding", "/absolutely/nonexistent/fabricated_906e37dc.py:99: x = 0"),
    ], selfcount=2)

    ctx = _ctx(scratchpad)
    result, content = _run(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    assert "## Findings Audit" in content, "'## Findings Audit' missing"
    assert "parsed as ### SEVERITY: blocks: 2" in content, (
        f"Expected parsed=2 in:\n{content[:600]}"
    )
    # 1F39FB1A: both findings are now suspect → filtered=2, aggregated=0
    assert "quote-filtered (21792EE7): 2" in content, (
        f"Expected quote-filtered=2 in:\n{content[:600]}"
    )
    assert "aggregated (post-dedup, post-quote-verify): 0" in content, (
        f"Expected aggregated=0 in:\n{content[:600]}"
    )

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "findings_audit key missing from StepResult.data"
    assert audit["lost_to_prose"] == 0, (
        f"lost_to_prose should be 0 (quote-filtered != prose-lost), got {audit.get('lost_to_prose')}"
    )
    assert audit["consistent"] is False, (
        f"consistent should be False (filtered=2 violates strict-consistent contract), got {audit.get('consistent')}"
    )
    assert audit.get("filtered") == 2, (
        f"filtered should be 2 (both findings suspect, 1F39FB1A), got {audit.get('filtered')}"
    )
    assert audit.get("aggregated") == 0, (
        f"aggregated should be 0 after soft-tag pivot (both findings suspect), got {audit.get('aggregated')}"
    )


# ─── AC6: telemetry — review_findings_audit event emitted ────────────────────


def test_ac6_telemetry_review_findings_audit_emitted(tmp_path, monkeypatch):
    """AC6: monkeypatch _emit_safe to spy; AC2-like setup must produce exactly
    one 'review_findings_audit' event whose payload equals data['findings_audit'].
    """
    spy: list[tuple[str, dict]] = []

    def _fake_emit_safe(event_type: str, payload: dict) -> None:
        spy.append((event_type, payload))

    # _emit_safe is defined in phase_6_review module; called as module-level function.
    # Must patch the SAME module object _aggregate_review_findings is bound to.
    # `from phase_6_review import ...` loads it as the top-level "phase_6_review" module,
    # NOT "workflows.phase_6_review" — patching _p6 (the latter) leaves the spy invisible.
    import phase_6_review as _p6mod
    monkeypatch.setattr(_p6mod, "_emit_safe", _fake_emit_safe)

    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    # AC2-like: role-a overclaims (5 but 2 blocks), role-b honest (1 block, count=1).
    _write_role(reviews, "a", blocks=[
        ("HIGH", "h1", "some/file.py:1: pass"),
        ("MEDIUM", "m1", "some/file.py:2: x = 1"),
    ], selfcount=5)
    _write_role(reviews, "b", blocks=[
        ("LOW", "l1", "other/file.py:4: foo()"),
    ], selfcount=1)

    ctx = _ctx(scratchpad)
    result, _content = _run(ctx, scratchpad)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "findings_audit key missing from StepResult.data"

    # Exactly one review_findings_audit event must appear.
    audit_events = [(et, p) for et, p in spy if et == "review_findings_audit"]
    assert len(audit_events) == 1, (
        f"Expected exactly 1 'review_findings_audit' event, got {len(audit_events)}. "
        f"All events: {[et for et, _ in spy]}"
    )

    _et, emitted_payload = audit_events[0]
    assert emitted_payload == audit, (
        f"Telemetry payload != data['findings_audit'].\n"
        f"  payload: {emitted_payload}\n"
        f"  audit:   {audit}"
    )


# ─── AC7: prompt contains role-findings-count + INVISIBLE / NOT REPORTED ─────


def test_ac7_prompt_contains_selfcount_instruction(tmp_path):
    """AC7: _build_review_prompt must include 'role-findings-count:' and
    prose-findings-are-INVISIBLE (or NOT REPORTED) instruction in the prompt.

    _build_review_prompt(ctx, None) returns StepResult with data['prompt'].
    The scratchpad dir must exist; spec/log files are optional (guarded by .is_file()).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    # reviews dir not needed for prompt building; spec/log .is_file() guards handle absence.

    ctx = _ctx(scratchpad)
    result = _build_review_prompt(ctx, None)

    assert result.status == "ok", f"_build_review_prompt failed: {result.error}"
    prompt = result.data.get("prompt", "")
    assert isinstance(prompt, str) and prompt, "prompt must be a non-empty string"

    assert "role-findings-count:" in prompt, (
        "Prompt must contain 'role-findings-count:' so sub-reviewers know to emit the count line"
    )
    # The spec says: prose findings are "INVISIBLE" or "NOT REPORTED".
    assert "INVISIBLE" in prompt or "NOT REPORTED" in prompt.upper(), (
        "Prompt must warn that prose-only findings are INVISIBLE / NOT REPORTED. "
        f"First 800 chars of prompt:\n{prompt[:800]}"
    )


# ─── AC8: E_NO_ROLE_FILES path unchanged ─────────────────────────────────────


def test_ac8_empty_reviews_dir_no_findings_audit(tmp_path):
    """AC8: empty reviews/ dir → E_NO_ROLE_FILES, no findings_audit key in data.

    The new audit code must run ONLY when role files exist; the early-return
    path at ~L1339 is unchanged.
    """
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)  # empty — no role-*.md files

    ctx = _ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev_ok(scratchpad))

    assert result.status == "error", f"expected error status, got {result.status}"
    assert result.error_code == "E_NO_ROLE_FILES", (
        f"expected E_NO_ROLE_FILES, got {result.error_code}"
    )
    data = result.data or {}
    assert "findings_audit" not in data, (
        f"findings_audit must NOT appear on the E_NO_ROLE_FILES path, got keys: {list(data.keys())}"
    )
