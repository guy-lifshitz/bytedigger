"""RED tests for GH596 + GH601 — prompt-parity: mirror gate rules into
spec-writer/RED/GREEN prompts.

Spec: SHARED/memory/Decisions/2026-07-12_GH596_GH601_prompt_parity_spec.md

Not-yet-existing symbols (`_spec_wants_py_gates`, `_RE_SPEC_PY_WANTS`,
`_RE_SPEC_FMT_WANTS`) are probed via getattr INSIDE test bodies only
(D1CF5FDF/§1q) so this file COLLECTS cleanly and fails at assert time, never
at collection time. `_build_red_prompt` / `_build_green_prompt` /
`_build_spec_prompt` all exist today and are imported at module top level,
mirroring test_gh501_red_prompt_rules.py / test_gh443_delta_retry.py.

§1i: no real singleton/time-dependent resource under test — all fixtures
(spec files, arch docs) are pre-staged deterministically before invoking the
UUT, per workflows.md §1i.
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _build_green_prompt, _build_red_prompt  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: E402


# ─── shared helpers ─────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh596-gh601",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_spec(scratchpad: Path, spec_text: str) -> Path:
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "build-spec.md"
    spec_path.write_text(spec_text, encoding="utf-8")
    return spec_path


_PY_SPEC_TEXT = (
    "## Context\nModify `engine_py/workflows/foo.py` to add a new helper "
    "function that the RED test in `tests/test_foo.py` exercises.\n"
)

_SH_ONLY_SPEC_TEXT = (
    "## Context\nModify `scripts/foo.sh` to add a new bash helper that the "
    "shellcheck lint verifies. No other files touched.\n"
)


def _build_red(tmp_path: Path, spec_text: str, **org_extra) -> StepResult:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _write_spec(scratchpad, spec_text)
    ctx = _make_ctx(scratchpad, **org_extra)
    return _build_red_prompt(ctx, None)


def _build_green(tmp_path: Path, spec_text: str, **org_extra) -> StepResult:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    spec_path = _write_spec(scratchpad, spec_text)
    red_log_path = scratchpad / "tests" / "build-red-output.log"
    red_log_path.parent.mkdir(parents=True, exist_ok=True)
    red_log_path.write_text("RED COMPLETE — 1 test written, all failing. Files: [tests/test_foo.py]\n")
    validation_doc_path = scratchpad / "specs" / "validation.md"
    validation_doc_path.write_text("VERDICT: PASS\n", encoding="utf-8")
    ctx = _make_ctx(scratchpad, **org_extra)
    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec_path),
            "red_log_path": str(red_log_path),
            "validation_doc_path": str(validation_doc_path),
            "cycle": 1,
        },
        duration_ms=0,
        step_name="gate_on_validation",
    )
    return _build_green_prompt(ctx, prev)


# ─── AC1/AC2/AC3: RED-prompt py-gates block presence (stub-passability + §1q + mass-deletion + suite-safety) ──


def test_ac1_red_prompt_py_spec_contains_stub_passability_rule(tmp_path):
    result = _build_red(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "E_RED_STUB_PASSABLE" in prompt, "py-spec must inject the stub-passability rule (GH596)"
    assert "module-attr" in prompt, "stub-passability block must mention the module-attr exemption"


def test_ac2_red_prompt_py_spec_contains_1q_rule(tmp_path):
    result = _build_red(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "spec_from_file_location" in prompt, "py-spec must inject the §1q literal-lint rule"


def test_ac3_red_prompt_py_spec_contains_mass_deletion_and_suite_safety(tmp_path):
    result = _build_red(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "MASS-DELETION" in prompt
    assert "SUITE-SAFETY" in prompt


def test_ac4_red_prompt_bash_only_spec_omits_py_gates_block(tmp_path):
    result = _build_red(tmp_path, _SH_ONLY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "E_RED_STUB_PASSABLE" not in prompt, "bash-only spec (no `.py` token) must omit the py-gates block"


# ─── AC5: GREEN-prompt TYPECHECK line gated on py-spec ──────────────────────


def test_ac5_green_prompt_py_spec_contains_typecheck_line(tmp_path):
    result = _build_green(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "TYPECHECK (GH596)" in prompt, "py-spec must inject the GREEN typecheck-gate rule"


def test_ac5_green_prompt_bash_only_spec_omits_typecheck_line(tmp_path):
    result = _build_green(tmp_path, _SH_ONLY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "TYPECHECK (GH596)" not in prompt, "bash-only spec must omit the GREEN typecheck-gate rule"


# ─── AC6: spec-writer prompt cycle-1 unconditional Block A (spec-lint parity) ──


def test_ac6_spec_prompt_cycle1_contains_block_a_unconditionally(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, question="Add a new Python helper to compute totals")
    result = _build_spec_prompt(ctx, None)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "TOKEN-CONSISTENCY" in prompt
    assert "PRESENCE-TRIAD" in prompt
    assert "FILES-NOT-IN-SCOPE" in prompt
    assert "OP↔AC CROSS-LINK" in prompt


# ─── AC7: spec-writer prompt Block B — python-wiring rule gated on `.py` probe ──


def test_ac7_spec_prompt_py_probe_contains_python_wiring_rule(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, question="Modify engine_py/workflows/foo.py to add a helper")
    result = _build_spec_prompt(ctx, None)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "PYTHON WIRING RULE" in prompt


def test_ac7_spec_prompt_no_py_probe_omits_python_wiring_rule(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, question="Fix a typo in the README wording")
    result = _build_spec_prompt(ctx, None)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "PYTHON WIRING RULE" not in prompt


# ─── AC8: spec-writer prompt Block C — format-conversion rule gated on probe ──


def test_ac8_spec_prompt_format_probe_contains_writer_behavior_rule(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, question="Change the on-disk storage format from JSON to JSONL")
    result = _build_spec_prompt(ctx, None)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "writer-behavior-on-legacy-input" in prompt


def test_ac8_spec_prompt_no_format_probe_omits_writer_behavior_rule(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, question="Fix a typo in the README wording")
    result = _build_spec_prompt(ctx, None)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "writer-behavior-on-legacy-input" not in prompt


# ─── AC9: phase_45 delta-retry path carries the new spec-lint tokens (GH729) ──
# GH729 supersedes GH601 AC-9: the cycle>=2 delta path now deliberately
# carries the high-binding spec-lint tokens via the bounded (2KB-capped)
# high-binding block, restoring parity that GH601/GH443 had dropped.


def test_ac9_spec_delta_retry_path_carries_high_binding_tokens_GH729(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## Context\nPrevious cycle-1 spec body.\n", encoding="utf-8")

    structured_findings_text = (
        "## Verdict\nREVISE\n\n"
        "## Findings (structured)\n"
        "```json\n"
        "[\n"
        '  {"id": "F1", "type": "gap", "evidence": "spec §3 AC2 missing validation",\n'
        '   "required_action": "add explicit forcing-function to AC2"}\n'
        "]\n"
        "```\n\n"
        "## Findings\n"
        "- spec is missing an Out of Scope section\n"
    )
    ctx = _make_ctx(scratchpad, question="Add delta-retry prompt parity check")
    prev = {"cycle": 2, "findings": structured_findings_text}

    result = _build_spec_prompt(ctx, prev)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert result.data.get("delta_retry") is True, "expected the delta-retry branch to fire on cycle>=2"
    assert "TOKEN-CONSISTENCY" in prompt, (
        "GH729: the cycle>=2 delta path now deliberately carries the high-binding "
        "spec-lint tokens (bounded block, 2KB cap) — GH729 supersedes GH601 AC-9"
    )


# ─── AC10: phase_5 RED delta-retry path does NOT carry the new py-gates tokens ──


def test_ac10_red_delta_retry_path_omits_new_tokens(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_IMPL_DELTA_RETRY", raising=False)
    scratchpad = tmp_path / "scratch"
    _write_spec(scratchpad, _PY_SPEC_TEXT)
    ctx = _make_ctx(scratchpad, question="Extend delta-retry to phase_5 implement RED cycle>=2")
    prev = {"cycle": 2, "findings": "FINDING_1: fix assertion"}

    result = _build_red_prompt(ctx, prev)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert result.data.get("delta_retry") is True, "expected the delta-retry branch to fire on cycle>=2"
    assert "E_RED_STUB_PASSABLE" not in prompt, "delta-retry prompt must NOT carry the new py-gates tokens"


# ─── AC11: prompt_bytes matches len(prompt.encode("utf-8")) on all three builders ──


def test_ac11_red_prompt_bytes_matches_encoded_length(tmp_path):
    result = _build_red(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert result.data["prompt_bytes"] == len(prompt.encode("utf-8"))


def test_ac11_green_prompt_bytes_matches_encoded_length(tmp_path):
    result = _build_green(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert result.data["prompt_bytes"] == len(prompt.encode("utf-8"))


def test_ac11_spec_prompt_bytes_matches_encoded_length(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, question="Add a new Python helper to compute totals")
    result = _build_spec_prompt(ctx, None)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert result.data["prompt_bytes"] == len(prompt.encode("utf-8"))
