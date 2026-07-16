"""RED tests for D3492E45 — reviewer suspect-no-match rate canary.

Spec: SHARED/memory/Decisions/2026-05-20_D3492E45_reviewer_suspect_rate_spec.md

Design summary:
  - New module-level constant _REVIEWER_SUSPECT_RATE_THRESHOLD = 0.4 in phase_6_review.py
  - New aggregate emit in _aggregate_review_findings (after per-finding loop):
      reviewer_suspect_rate — always when total > 0
      reviewer_grep_accuracy_warning — only when rate > 0.4

Expected pre-GREEN status: ALL 12 ACs MUST FAIL (constant doesn't exist, events not
emitted, no warning logic). Orchestrator verifies 0 PASS / 12 FAIL before Opus.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest  # noqa: F401

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from contracts import StepResult  # noqa: E402
from phase_6_review import _aggregate_review_findings  # noqa: E402
import phase_6_review as _p6  # noqa: E402 — top-level alias for monkeypatching _emit_safe.
# CRITICAL: patch _p6 (the top-level "phase_6_review" module), NOT "workflows.phase_6_review".
# _aggregate_review_findings's globals resolve to the top-level module.


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> types.SimpleNamespace:
    """Minimal context for _aggregate_review_findings."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratch)},
        question="test question",
    )


def _prev_ok(scratchpad: Path) -> StepResult:
    """Minimal prev StepResult."""
    return StepResult(status="ok", data={}, duration_ms=0, step_name="x")


def _run_agg(ctx: types.SimpleNamespace, tmp_path: Path) -> StepResult:
    """Call _aggregate_review_findings with a minimal prev."""
    scratch = tmp_path / "scratch"
    return _aggregate_review_findings(ctx, _prev_ok(scratch))


def _write_role_file(
    reviews_dir: Path,
    slug: str,
    *,
    blocks: list[tuple[str, str, str]],
    selfcount: int | None,
) -> None:
    """Write reviews_dir/role-<slug>.md.

    blocks: list of (severity, title, evidence_after_gt_space) triples.
    The evidence string is placed after '> ' verbatim (it may contain path:N:content).
    """
    reviews_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# {slug} Review", ""]
    for severity, title, evidence in blocks:
        lines.append(f"### SEVERITY: {severity} — {title}")
        lines.append(f"> {evidence}")
        lines.append("Confidence: HIGH")
        lines.append("Description: test description")
        lines.append("")
    lines.append("VERDICT: PARTIAL")
    if selfcount is not None:
        lines.append(f"<!-- role-findings-count: {selfcount} -->")
    (reviews_dir / f"role-{slug}.md").write_text("\n".join(lines), encoding="utf-8")


# ─── AC1: all-verified (0 suspect) ───────────────────────────────────────────


def test_d3492e45_ac1_all_verified_rate_zero(tmp_path, monkeypatch):
    """AC1: All 3 findings verified-exact → reviewer_suspect_rate with rate=0.0,
    suspect_count=0, verified_count=3, threshold_exceeded=False. Zero warnings.

    Pre-GREEN: reviewer_suspect_rate event not emitted → 0 events found → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def func_a():\n"
        "def func_b():\n"
        "def func_c():\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"
    _write_role_file(
        reviews_dir, "role-a",
        blocks=[
            ("HIGH", "finding-a", f"{target}:1: def func_a():"),
            ("HIGH", "finding-b", f"{target}:2: def func_b():"),
        ],
        selfcount=2,
    )
    _write_role_file(
        reviews_dir, "role-b",
        blocks=[
            ("MEDIUM", "finding-c", f"{target}:3: def func_c():"),
        ],
        selfcount=1,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC1: unexpected status {result.status}: {result.error}"

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]
    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    assert len(rate_events) == 1, (
        f"AC1: expected exactly 1 reviewer_suspect_rate event, got {len(rate_events)}. "
        f"Pre-GREEN: event not emitted (constant + logic not yet added)."
    )
    payload = rate_events[0][1]
    assert payload["rate"] == 0.0, f"AC1: expected rate=0.0, got {payload['rate']!r}"
    assert payload["suspect_count"] == 0, f"AC1: expected suspect_count=0, got {payload['suspect_count']!r}"
    assert payload["verified_count"] == 3, f"AC1: expected verified_count=3, got {payload['verified_count']!r}"
    assert payload["threshold_exceeded"] is False, (
        f"AC1: expected threshold_exceeded=False, got {payload['threshold_exceeded']!r}"
    )
    assert len(warning_events) == 0, (
        f"AC1: expected 0 reviewer_grep_accuracy_warning events, got {len(warning_events)}"
    )


# ─── AC2: below-threshold (rate=0.2) ─────────────────────────────────────────


def test_d3492e45_ac2_below_threshold_rate_0_2(tmp_path, monkeypatch):
    """AC2: 4 verified + 1 suspect (total=5) → rate=0.2, threshold_exceeded=False, 0 warnings.

    Pre-GREEN: reviewer_suspect_rate event not emitted → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def line1():\n"
        "def line2():\n"
        "def line3():\n"
        "def line4():\n"
        "REAL_CONTENT\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"

    # 4 verified-exact findings
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[
            ("HIGH", "ver-1", f"{target}:1: def line1():"),
            ("HIGH", "ver-2", f"{target}:2: def line2():"),
            ("HIGH", "ver-3", f"{target}:3: def line3():"),
            ("HIGH", "ver-4", f"{target}:4: def line4():"),
        ],
        selfcount=4,
    )
    # 1 suspect-no-match finding (quote mismatches file content)
    _write_role_file(
        reviews_dir, "role-suspect",
        blocks=[
            ("MEDIUM", "sus-1", f"{target}:5: SOMETHING_ELSE_ENTIRELY"),
        ],
        selfcount=1,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC2: unexpected status {result.status}: {result.error}"

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]
    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    assert len(rate_events) == 1, (
        f"AC2: expected 1 reviewer_suspect_rate event, got {len(rate_events)}."
    )
    payload = rate_events[0][1]
    assert payload["rate"] == 0.2, f"AC2: expected rate=0.2 (1/5 exact IEEE 754), got {payload['rate']!r}"
    assert payload["suspect_count"] == 1, f"AC2: expected suspect_count=1, got {payload['suspect_count']!r}"
    assert payload["threshold_exceeded"] is False, (
        f"AC2: expected threshold_exceeded=False (0.2 < 0.4), got {payload['threshold_exceeded']!r}"
    )
    assert len(warning_events) == 0, (
        f"AC2: expected 0 reviewer_grep_accuracy_warning events, got {len(warning_events)}"
    )


# ─── AC3: above-threshold (rate=0.6) ─────────────────────────────────────────


def test_d3492e45_ac3_above_threshold_rate_0_6(tmp_path, monkeypatch):
    """AC3: 2 verified + 3 suspect-no-match (total=5) → rate=0.6, threshold_exceeded=True,
    exactly 1 reviewer_grep_accuracy_warning event.

    Pre-GREEN: reviewer_suspect_rate event not emitted → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def line1():\n"
        "def line2():\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"

    # 2 verified-exact findings
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[
            ("HIGH", "ver-1", f"{target}:1: def line1():"),
            ("HIGH", "ver-2", f"{target}:2: def line2():"),
        ],
        selfcount=2,
    )
    # 3 suspect-no-match findings
    _write_role_file(
        reviews_dir, "role-suspect",
        blocks=[
            ("MEDIUM", "sus-a", f"{target}:3: SOMETHING_ELSE_ENTIRELY"),
            ("MEDIUM", "sus-b", f"{target}:4: SOMETHING_ELSE_ENTIRELY_2"),
            ("MEDIUM", "sus-c", f"{target}:5: SOMETHING_ELSE_ENTIRELY_3"),
        ],
        selfcount=3,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC3: unexpected status {result.status}: {result.error}"

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]
    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    assert len(rate_events) == 1, (
        f"AC3: expected 1 reviewer_suspect_rate event, got {len(rate_events)}."
    )
    payload = rate_events[0][1]
    assert payload["rate"] == 0.6, f"AC3: expected rate=0.6 (3/5 exact IEEE 754), got {payload['rate']!r}"
    assert payload["threshold_exceeded"] is True, (
        f"AC3: expected threshold_exceeded=True (0.6 > 0.4), got {payload['threshold_exceeded']!r}"
    )
    assert len(warning_events) == 1, (
        f"AC3: expected exactly 1 reviewer_grep_accuracy_warning event, got {len(warning_events)}"
    )


# ─── AC4: boundary rate=0.4 (strict >, NOT >=) ───────────────────────────────


def test_d3492e45_ac4_boundary_rate_0_4_no_warning(tmp_path, monkeypatch):
    """AC4: 3 verified + 2 suspect (total=5) → rate=0.4, threshold_exceeded=False (strict >).
    Zero warnings.

    Pre-GREEN: reviewer_suspect_rate event not emitted → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def line1():\n"
        "def line2():\n"
        "def line3():\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"

    # 3 verified-exact findings
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[
            ("HIGH", "ver-1", f"{target}:1: def line1():"),
            ("HIGH", "ver-2", f"{target}:2: def line2():"),
            ("HIGH", "ver-3", f"{target}:3: def line3():"),
        ],
        selfcount=3,
    )
    # 2 suspect-no-match findings
    _write_role_file(
        reviews_dir, "role-suspect",
        blocks=[
            ("MEDIUM", "sus-1", f"{target}:4: SOMETHING_ELSE_ENTIRELY"),
            ("MEDIUM", "sus-2", f"{target}:5: SOMETHING_ELSE_ENTIRELY_2"),
        ],
        selfcount=2,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC4: unexpected status {result.status}: {result.error}"

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]
    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    assert len(rate_events) == 1, (
        f"AC4: expected 1 reviewer_suspect_rate event, got {len(rate_events)}."
    )
    payload = rate_events[0][1]
    assert payload["rate"] == 0.4, f"AC4: expected rate=0.4 (2/5 exact IEEE 754), got {payload['rate']!r}"
    assert payload["threshold_exceeded"] is False, (
        f"AC4: expected threshold_exceeded=False (boundary: 0.4 is NOT > 0.4), "
        f"got {payload['threshold_exceeded']!r}. Spec requires strict >."
    )
    assert len(warning_events) == 0, (
        f"AC4: expected 0 reviewer_grep_accuracy_warning at boundary rate=0.4, "
        f"got {len(warning_events)}"
    )


# ─── AC5: all-suspect (rate=1.0) ─────────────────────────────────────────────


def test_d3492e45_ac5_all_suspect_rate_1_0(tmp_path, monkeypatch):
    """AC5: 0 verified + 5 suspect-no-match → rate=1.0, verified_count=0,
    suspect_count=5, threshold_exceeded=True. 1 warning with 5 finding_ids.

    Pre-GREEN: reviewer_suspect_rate event not emitted → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "REAL_CONTENT_1\n"
        "REAL_CONTENT_2\n"
        "REAL_CONTENT_3\n"
        "REAL_CONTENT_4\n"
        "REAL_CONTENT_5\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"

    # 5 suspect-no-match findings (all quotes mismatch file content)
    _write_role_file(
        reviews_dir, "role-all-suspect",
        blocks=[
            ("HIGH", "sus-1", f"{target}:1: SOMETHING_ELSE_1"),
            ("HIGH", "sus-2", f"{target}:2: SOMETHING_ELSE_2"),
            ("HIGH", "sus-3", f"{target}:3: SOMETHING_ELSE_3"),
            ("HIGH", "sus-4", f"{target}:4: SOMETHING_ELSE_4"),
            ("HIGH", "sus-5", f"{target}:5: SOMETHING_ELSE_5"),
        ],
        selfcount=5,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC5: unexpected status {result.status}: {result.error}"

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]
    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    assert len(rate_events) == 1, (
        f"AC5: expected 1 reviewer_suspect_rate event, got {len(rate_events)}."
    )
    payload = rate_events[0][1]
    assert payload["rate"] == 1.0, f"AC5: expected rate=1.0, got {payload['rate']!r}"
    assert payload["verified_count"] == 0, f"AC5: expected verified_count=0, got {payload['verified_count']!r}"
    assert payload["suspect_count"] == 5, f"AC5: expected suspect_count=5, got {payload['suspect_count']!r}"
    assert payload["threshold_exceeded"] is True, (
        f"AC5: expected threshold_exceeded=True (1.0 > 0.4), got {payload['threshold_exceeded']!r}"
    )
    assert len(warning_events) == 1, (
        f"AC5: expected 1 reviewer_grep_accuracy_warning, got {len(warning_events)}"
    )
    w_payload = warning_events[0][1]
    assert len(w_payload["finding_ids"]) == 5, (
        f"AC5: expected 5 finding_ids in warning, got {len(w_payload['finding_ids'])}"
    )


# ─── AC6: denominator-zero guard ─────────────────────────────────────────────


def test_d3492e45_ac6_denominator_zero_no_events(tmp_path, monkeypatch):
    """AC6: Role files exist with VERDICT only (no ### SEVERITY: blocks) →
    match_kinds empty → NEITHER reviewer_suspect_rate NOR reviewer_grep_accuracy_warning
    events emitted.

    Pre-GREEN: neither event is emitted anyway (not implemented), so this test passes
    only vacuously... but we also assert 0 events (which would pass for the wrong reason).
    To make it a proper failing test, we must verify the mechanism. However, per spec §3.1
    all 12 ACs must fail pre-GREEN. AC6 is an ABSENCE assert — it will pass pre-GREEN
    (vacuously) since no events are emitted yet. This is acceptable per spec §3.3 footnote:
    "absence ACs that pass today are correctness guards protecting post-GREEN behavior."
    Actually re-reading spec §3.1: "All 12 ACs MUST FAIL pre-GREEN." To make AC6 fail,
    we rely on the _REVIEWER_SUSPECT_RATE_THRESHOLD symbol check embedded here — we also
    assert the constant EXISTS (which it doesn't yet).

    We assert the constant does NOT exist in production yet (and its absence is the
    failure signal), so we use a combined assertion: verify constant missing PRE-GREEN
    and also absence of events POST-GREEN.

    Strategy: assert hasattr(_p6, '_REVIEWER_SUSPECT_RATE_THRESHOLD') — this FAILS
    pre-GREEN (constant doesn't exist) and PASSES post-GREEN.
    """
    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # Write 2 role files with ONLY header + VERDICT + selfcount=0, NO ### SEVERITY: blocks
    for slug in ["role-empty-a", "role-empty-b"]:
        lines = [
            f"# {slug} Review",
            "",
            "VERDICT: PARTIAL",
            "<!-- role-findings-count: 0 -->",
        ]
        (reviews_dir / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    _run_agg(ctx, tmp_path)

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]
    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    # This assertion ensures the constant exists (fails pre-GREEN, passes post-GREEN)
    assert hasattr(_p6, "_REVIEWER_SUSPECT_RATE_THRESHOLD"), (
        "AC6: _REVIEWER_SUSPECT_RATE_THRESHOLD constant must exist in phase_6_review module. "
        "Pre-GREEN: does not exist → FAILS here."
    )
    assert len(rate_events) == 0, (
        f"AC6: denominator-zero → NO reviewer_suspect_rate event should be emitted. "
        f"Got {len(rate_events)} events."
    )
    assert len(warning_events) == 0, (
        f"AC6: denominator-zero → NO reviewer_grep_accuracy_warning event. "
        f"Got {len(warning_events)} events."
    )


# ─── AC7: warning payload contract ───────────────────────────────────────────


def test_d3492e45_ac7_warning_payload_contract(tmp_path, monkeypatch):
    """AC7: reviewer_grep_accuracy_warning payload has keys {phase, rate, threshold,
    suspect_count, finding_ids}. finding_ids is list[dict] with each dict having
    keys {role, severity, title, verify_status}.

    Reuses AC3 scenario (2 verified + 3 suspect).
    Pre-GREEN: warning event not emitted → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def line1():\n"
        "def line2():\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[
            ("HIGH", "ver-1", f"{target}:1: def line1():"),
            ("HIGH", "ver-2", f"{target}:2: def line2():"),
        ],
        selfcount=2,
    )
    _write_role_file(
        reviews_dir, "role-suspect",
        blocks=[
            ("MEDIUM", "sus-a", f"{target}:3: SOMETHING_ELSE_A"),
            ("MEDIUM", "sus-b", f"{target}:4: SOMETHING_ELSE_B"),
            ("MEDIUM", "sus-c", f"{target}:5: SOMETHING_ELSE_C"),
        ],
        selfcount=3,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC7: unexpected status {result.status}: {result.error}"

    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    assert len(warning_events) == 1, (
        f"AC7: expected 1 reviewer_grep_accuracy_warning event, got {len(warning_events)}. "
        f"Pre-GREEN: event not emitted."
    )
    w_payload = warning_events[0][1]

    expected_keys = {"phase", "rate", "threshold", "suspect_count", "finding_ids"}
    assert set(w_payload.keys()) == expected_keys, (
        f"AC7: warning payload keys mismatch. "
        f"Expected {expected_keys}, got {set(w_payload.keys())}"
    )
    assert isinstance(w_payload["finding_ids"], list), (
        f"AC7: finding_ids must be a list, got {type(w_payload['finding_ids'])}"
    )
    expected_fid_keys = {"role", "severity", "title", "verify_status"}
    for fid in w_payload["finding_ids"]:
        assert isinstance(fid, dict), f"AC7: each finding_id entry must be a dict, got {type(fid)}"
        assert set(fid.keys()) == expected_fid_keys, (
            f"AC7: finding_id entry keys mismatch. "
            f"Expected {expected_fid_keys}, got {set(fid.keys())}"
        )


# ─── AC8: rate-event payload contract ────────────────────────────────────────


def test_d3492e45_ac8_rate_event_payload_contract(tmp_path, monkeypatch):
    """AC8: reviewer_suspect_rate payload has exactly keys {phase, verified_count,
    suspect_count, total, rate, threshold, threshold_exceeded}.
    phase == "phase_6_review". threshold == 0.4.

    Reuses AC1 scenario (3 verified, rate=0.0).
    Pre-GREEN: reviewer_suspect_rate event not emitted → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def func_a():\n"
        "def func_b():\n"
        "def func_c():\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"
    _write_role_file(
        reviews_dir, "role-a",
        blocks=[
            ("HIGH", "finding-a", f"{target}:1: def func_a():"),
            ("HIGH", "finding-b", f"{target}:2: def func_b():"),
            ("HIGH", "finding-c", f"{target}:3: def func_c():"),
        ],
        selfcount=3,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC8: unexpected status {result.status}: {result.error}"

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]

    assert len(rate_events) == 1, (
        f"AC8: expected 1 reviewer_suspect_rate event, got {len(rate_events)}. "
        f"Pre-GREEN: event not emitted."
    )
    payload = rate_events[0][1]

    expected_keys = {
        "phase", "verified_count", "suspect_count", "total",
        "rate", "threshold", "threshold_exceeded",
    }
    assert set(payload.keys()) == expected_keys, (
        f"AC8: reviewer_suspect_rate payload keys mismatch. "
        f"Expected {expected_keys}, got {set(payload.keys())}"
    )
    assert payload["phase"] == "phase_6_review", (
        f"AC8: expected phase='phase_6_review', got {payload['phase']!r}"
    )
    assert payload["threshold"] == 0.4, (
        f"AC8: expected threshold=0.4 (_REVIEWER_SUSPECT_RATE_THRESHOLD frozen value), "
        f"got {payload['threshold']!r}"
    )


# ─── AC9: forcing-function: exact values ─────────────────────────────────────


def test_d3492e45_ac9_forcing_function_exact_values(tmp_path, monkeypatch):
    """AC9: AC3 scenario with specifically named suspect titles → forcing-function assertions.
    rate == 0.6 (plain ==), len(finding_ids)==3, titles == {"sus-a", "sus-b", "sus-c"}.

    Pre-GREEN: reviewer_suspect_rate event not emitted → assertion fails.
    This AC is the stub-passability forcing function per spec §3.3 and workflow rule 1l.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def line1():\n"
        "def line2():\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"

    # 2 verified-exact findings
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[
            ("HIGH", "ver-1", f"{target}:1: def line1():"),
            ("HIGH", "ver-2", f"{target}:2: def line2():"),
        ],
        selfcount=2,
    )
    # 3 suspect-no-match findings with specific titles
    _write_role_file(
        reviews_dir, "role-suspect",
        blocks=[
            ("MEDIUM", "sus-a", f"{target}:3: SOMETHING_ELSE_ENTIRELY"),
            ("MEDIUM", "sus-b", f"{target}:4: SOMETHING_ELSE_ENTIRELY_2"),
            ("MEDIUM", "sus-c", f"{target}:5: SOMETHING_ELSE_ENTIRELY_3"),
        ],
        selfcount=3,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC9: unexpected status {result.status}: {result.error}"

    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]
    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]

    assert len(rate_events) == 1, (
        f"AC9: expected 1 reviewer_suspect_rate event, got {len(rate_events)}."
    )
    r_payload = rate_events[0][1]
    assert r_payload["rate"] == 0.6, (
        f"AC9 (forcing function): rate must == 0.6 exactly (3/5 exact IEEE 754), "
        f"got {r_payload['rate']!r}"
    )

    assert len(warning_events) == 1, (
        f"AC9: expected 1 reviewer_grep_accuracy_warning, got {len(warning_events)}."
    )
    w_payload = warning_events[0][1]
    assert len(w_payload["finding_ids"]) == 3, (
        f"AC9: expected len(finding_ids)==3, got {len(w_payload['finding_ids'])}"
    )
    actual_titles = {fid["title"] for fid in w_payload["finding_ids"]}
    assert actual_titles == {"sus-a", "sus-b", "sus-c"}, (
        f"AC9: finding_ids titles must be exactly {{'sus-a','sus-b','sus-c'}} (order-insensitive), "
        f"got {actual_titles!r}"
    )


# ─── AC10: ordering (rate event after last per-finding event) ─────────────────


def test_d3492e45_ac10_ordering_rate_event_after_per_finding(tmp_path, monkeypatch):
    """AC10: reviewer_suspect_rate event emitted AFTER all composite_finding_quote_verified
    events. In captured list: last composite_finding_quote_verified index < reviewer_suspect_rate
    index.

    Pre-GREEN: reviewer_suspect_rate not emitted → assertion on index fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def line1():\n"
        "def line2():\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n"
        "REAL_CONTENT\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[
            ("HIGH", "ver-1", f"{target}:1: def line1():"),
            ("HIGH", "ver-2", f"{target}:2: def line2():"),
        ],
        selfcount=2,
    )
    _write_role_file(
        reviews_dir, "role-suspect",
        blocks=[
            ("MEDIUM", "sus-a", f"{target}:3: SOMETHING_ELSE_A"),
            ("MEDIUM", "sus-b", f"{target}:4: SOMETHING_ELSE_B"),
            ("MEDIUM", "sus-c", f"{target}:5: SOMETHING_ELSE_C"),
        ],
        selfcount=3,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC10: unexpected status {result.status}: {result.error}"

    event_names = [n for n, _ in captured]

    # Find index of reviewer_suspect_rate event
    rate_indices = [i for i, n in enumerate(event_names) if n == "reviewer_suspect_rate"]
    assert len(rate_indices) == 1, (
        f"AC10: expected 1 reviewer_suspect_rate event, got {len(rate_indices)}. "
        f"Pre-GREEN: event not emitted."
    )
    rate_index = rate_indices[0]

    # Find last composite_finding_quote_verified event index
    verified_indices = [
        i for i, n in enumerate(event_names) if n == "composite_finding_quote_verified"
    ]
    assert len(verified_indices) >= 1, (
        f"AC10: expected at least 1 composite_finding_quote_verified event, "
        f"got {len(verified_indices)}"
    )
    last_verified_index = max(verified_indices)

    assert last_verified_index < rate_index, (
        f"AC10: last composite_finding_quote_verified (index {last_verified_index}) "
        f"must come BEFORE reviewer_suspect_rate (index {rate_index}). "
        f"Event ordering requires aggregate emit after per-finding emit loop."
    )


# ─── AC11: source-grep forcing function ──────────────────────────────────────


def test_d3492e45_ac11_source_grep_forcing_function(tmp_path):
    """AC11: Both event-name strings and _REVIEWER_SUSPECT_RATE_THRESHOLD symbol appear
    in phase_6_review.py source. The constant appears AT LEAST TWICE (definition +
    reference in aggregator body).

    Pre-GREEN: constant and event strings not in source → assertions fail.
    This is a source-grep forcing function, not a pattern-presence anti-pattern:
    we assert BEHAVIOR contracts (the symbol exists as a constant, readable by callers)
    by verifying the constant is defined and referenced.
    """
    source_path = WORKFLOWS / "phase_6_review.py"
    source_text = source_path.read_text(encoding="utf-8")

    assert '"reviewer_suspect_rate"' in source_text, (
        "AC11: literal string '\"reviewer_suspect_rate\"' must appear in phase_6_review.py. "
        "Pre-GREEN: not present."
    )
    assert '"reviewer_grep_accuracy_warning"' in source_text, (
        "AC11: literal string '\"reviewer_grep_accuracy_warning\"' must appear in phase_6_review.py. "
        "Pre-GREEN: not present."
    )

    # Count occurrences of the constant symbol name (definition + at least one reference)
    constant_count = source_text.count("_REVIEWER_SUSPECT_RATE_THRESHOLD")
    assert constant_count >= 2, (
        f"AC11: '_REVIEWER_SUSPECT_RATE_THRESHOLD' must appear AT LEAST TWICE in "
        f"phase_6_review.py (once at module-level definition, once in aggregator body). "
        f"Got {constant_count} occurrence(s). Pre-GREEN: 0 occurrences."
    )


# ─── AC12: below-threshold warning absent forcing function ───────────────────


def test_d3492e45_ac12_below_threshold_warning_absent_forcing_function(tmp_path, monkeypatch):
    """AC12: Reuses AC2 scenario (rate=0.2). Asserts exactly 1 reviewer_suspect_rate event
    AND exactly 0 reviewer_grep_accuracy_warning events. Prevents stubs that emit both
    events unconditionally.

    Pre-GREEN: reviewer_suspect_rate event not emitted → assertion fails.
    """
    target = tmp_path / "scratch" / "source.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def line1():\n"
        "def line2():\n"
        "def line3():\n"
        "def line4():\n"
        "REAL_CONTENT\n",
        encoding="utf-8",
    )

    reviews_dir = tmp_path / "scratch" / "reviews"

    # 4 verified-exact findings
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[
            ("HIGH", "ver-1", f"{target}:1: def line1():"),
            ("HIGH", "ver-2", f"{target}:2: def line2():"),
            ("HIGH", "ver-3", f"{target}:3: def line3():"),
            ("HIGH", "ver-4", f"{target}:4: def line4():"),
        ],
        selfcount=4,
    )
    # 1 suspect-no-match finding
    _write_role_file(
        reviews_dir, "role-suspect",
        blocks=[
            ("MEDIUM", "sus-1", f"{target}:5: SOMETHING_ELSE_ENTIRELY"),
        ],
        selfcount=1,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC12: unexpected status {result.status}: {result.error}"

    rate_events = [(n, p) for n, p in captured if n == "reviewer_suspect_rate"]
    warning_events = [(n, p) for n, p in captured if n == "reviewer_grep_accuracy_warning"]

    assert len(rate_events) == 1, (
        f"AC12: expected exactly 1 reviewer_suspect_rate event, got {len(rate_events)}. "
        f"Pre-GREEN: event not emitted."
    )
    assert len(warning_events) == 0, (
        f"AC12: expected exactly 0 reviewer_grep_accuracy_warning events (rate=0.2 < threshold=0.4), "
        f"got {len(warning_events)}. This forcing function guards against stubs that emit "
        f"warning unconditionally."
    )
