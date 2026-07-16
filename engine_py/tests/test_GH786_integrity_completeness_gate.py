"""RED tests for GH786 — deterministic pre-parser completeness gate + bounded
reviewer retry wrapping `_invoke_integrity_llm` (Step 2). Step 3
(`_classify_diff_verdict` / `_parse_verdict`) is UNCHANGED.

Spec: SHARED/memory/Decisions/2026-07-14_GH786_integrity_completeness_gate_spec.md

UUT: `_invoke_integrity_llm(ctx, prev)` in
`SYSTEM/cli/build/engine_py/workflows/phase_5_integrity.py`.
GREEN will wrap the single `invoke_llm_subprocess` call in a bounded retry
loop gated by `_parse_verdict(raw) != VERDICT_UNKNOWN` (a new, not-yet-existing
resolver `_resolve_integrity_verdict_retries(cfg)` bounds the loop) and will
add a new additive field `data["verdict_completeness_retries"]`.

Per §1q: UUTs are imported INSIDE each test body, never at module top level.
No `sys.path` mutation here — conftest.py already installs the
conftest-import-time singleton (engine_py root + workflows dir) per 81F97F3D.

§1i: no singleton/time-dependent resource under test — `invoke_llm_subprocess`
is monkeypatched deterministically (a plain call-counter + canned/side-effect
responses), never raced against real timing or a real subprocess/lock.

Fixture pattern (ctx/prev construction) copied from
`test_GH781_integrity_verdict_forcing.py`'s `_make_ctx` helper (private
helpers are not imported across test files — duplicated here per instruction).
"""
from __future__ import annotations

from pathlib import Path

from contracts import StepResult, WorkflowContext  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixture helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_ctx(scratchpad: Path, *, org_config_extra: dict | None = None) -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    fake_worktree = scratchpad.parent / "fake_worktree"
    fake_worktree.mkdir(parents=True, exist_ok=True)
    org_config = {
        "scratchpad_dir": str(scratchpad),
        "current_worktree_path": str(fake_worktree),
    }
    if org_config_extra:
        org_config.update(org_config_extra)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question="Add foo to bar",
        session_id="test-gh786",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(tmp_path: Path) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "REVIEW THIS DIFF AND EMIT A VERDICT.",
            "stable_prefix": "STABLE-PREFIX-GH786",
            "doc_path": str(tmp_path / "reviews" / "build-integrity-review.md"),
            "diff_path": str(tmp_path / "integrity" / "test-diff.patch"),
        },
        duration_ms=0,
        step_name="build_integrity_prompt",
    )


_PROSE_ONLY = (
    "This diff modifies two assertions in tests/test_foo.py to reflect an "
    "updated spec requirement around boundary handling. In my judgment this "
    "is a legitimate spec-driven change and the assertions were correctly "
    "updated. No gaming of assertions was observed."
)

_PROSE_WITH_EMBEDDED_MENTION = (
    "This diff appears related to a SPEC_CHANGE in boundary handling, but I "
    "am not issuing a standalone verdict line here, just prose discussion."
)

_STANDALONE_LEGITIMATE_REFACTOR = "VERDICT: LEGITIMATE_REFACTOR"
_STANDALONE_BOGUS = "VERDICT: BOGUS"


def _make_fake_invoke(monkeypatch, responses: list[str] | None = None, *, error: bool = False):
    """Install a fake `invoke_llm_subprocess` on the phase_5_integrity module.

    Records call count and the `prompt=`/`stable_prefix=` kwargs of every call.
    `responses` is consumed in order (last value repeats once exhausted).
    """
    import phase_5_integrity  # noqa: PLC0415

    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append({"prompt": kwargs.get("prompt"), "stable_prefix": kwargs.get("stable_prefix")})
        if error:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="invoke_integrity_llm",
                error="subprocess timed out",
                error_code="E_TIMEOUT",
            )
        idx = min(len(calls) - 1, len(responses) - 1)
        raw = responses[idx]
        extra = kwargs.get("extra_data") or {}
        return StepResult(
            status="ok",
            data={
                "raw_response": raw,
                "doc_path": extra.get("doc_path"),
                "diff_path": extra.get("diff_path"),
            },
            duration_ms=0,
            step_name="invoke_integrity_llm",
        )

    monkeypatch.setattr(phase_5_integrity, "invoke_llm_subprocess", _fake)
    return calls


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — retry on incomplete: prose-only, retries=1 config → called exactly 2x;
# final response still parses to UNKNOWN.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_retry_on_incomplete_prose_only(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm, _parse_verdict  # noqa: PLC0415

    calls = _make_fake_invoke(monkeypatch, [_PROSE_ONLY, _PROSE_ONLY])
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 1})
    prev = _make_prev(tmp_path)

    result = _invoke_integrity_llm(ctx, prev)

    assert len(calls) == 2, f"expected exactly 2 invoke_llm_subprocess calls, got {len(calls)}"
    assert _parse_verdict(result.data["raw_response"]) == "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — fail-closed on exhaustion: chain Step2 result into Step3 →
# E_INTEGRITY_NO_MARKER, recoverable=False.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac2_failclosed_on_exhaustion(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm, _classify_diff_verdict  # noqa: PLC0415

    _make_fake_invoke(monkeypatch, [_PROSE_ONLY, _PROSE_ONLY])
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 1})
    prev = _make_prev(tmp_path)

    step2 = _invoke_integrity_llm(ctx, prev)
    step3 = _classify_diff_verdict(ctx, step2)

    assert step3.error_code == "E_INTEGRITY_NO_MARKER"
    assert step3.recoverable is False


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — recovery: attempt1 prose-only, attempt2 standalone
# VERDICT: LEGITIMATE_REFACTOR → called exactly 2x; Step3 → ok, verdict match.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac3_recovery_on_second_attempt(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm, _classify_diff_verdict  # noqa: PLC0415

    calls = _make_fake_invoke(
        monkeypatch, [_PROSE_ONLY, _STANDALONE_LEGITIMATE_REFACTOR]
    )
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 1})
    prev = _make_prev(tmp_path)

    step2 = _invoke_integrity_llm(ctx, prev)
    step3 = _classify_diff_verdict(ctx, step2)

    assert len(calls) == 2
    assert step3.status == "ok"
    assert step3.data["verdict"] == "LEGITIMATE_REFACTOR"


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — no wasted retry on success: attempt1 already valid → called exactly
# 1x; data["verdict_completeness_retries"] == 0.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4_no_wasted_retry_on_first_success(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm  # noqa: PLC0415

    calls = _make_fake_invoke(monkeypatch, [_STANDALONE_LEGITIMATE_REFACTOR])
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 1})
    prev = _make_prev(tmp_path)

    result = _invoke_integrity_llm(ctx, prev)

    assert len(calls) == 1
    assert result.data["verdict_completeness_retries"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — out-of-enum/malformed treated as incomplete: standalone
# VERDICT: BOGUS persistently → gate retries (not silently accepted); final
# classify fail-closed.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac5_out_of_enum_treated_as_incomplete_and_retried(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm, _classify_diff_verdict  # noqa: PLC0415

    calls = _make_fake_invoke(monkeypatch, [_STANDALONE_BOGUS, _STANDALONE_BOGUS])
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 1})
    prev = _make_prev(tmp_path)

    step2 = _invoke_integrity_llm(ctx, prev)
    step3 = _classify_diff_verdict(ctx, step2)

    assert len(calls) == 2, "gate must retry on out-of-enum standalone token, not accept it"
    assert step3.status == "error"
    assert step3.error_code == "E_INTEGRITY_NO_MARKER"


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — retry count bound: HAL_INTEGRITY_VERDICT_RETRY_MAX=0 → called exactly
# 1x on persistent omission, still fail-closes.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac6_env_retry_max_zero_disables_retry(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm, _classify_diff_verdict  # noqa: PLC0415

    monkeypatch.setenv("HAL_INTEGRITY_VERDICT_RETRY_MAX", "0")
    calls = _make_fake_invoke(monkeypatch, [_PROSE_ONLY, _PROSE_ONLY, _PROSE_ONLY])
    # cfg says retry, but env (resolution order: env wins) must override to 0.
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 5})
    prev = _make_prev(tmp_path)

    step2 = _invoke_integrity_llm(ctx, prev)
    step3 = _classify_diff_verdict(ctx, step2)

    assert len(calls) == 1, "HAL_INTEGRITY_VERDICT_RETRY_MAX=0 must disable retry entirely"
    assert step3.error_code == "E_INTEGRITY_NO_MARKER"


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — GH705 stable-prefix invariant: across retries, prompt and
# stable_prefix args to every invoke_llm_subprocess call are byte-identical.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_prompt_and_stable_prefix_identical_across_retries(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm  # noqa: PLC0415

    calls = _make_fake_invoke(monkeypatch, [_PROSE_ONLY, _PROSE_ONLY, _STANDALONE_LEGITIMATE_REFACTOR])
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 2})
    prev = _make_prev(tmp_path)

    _invoke_integrity_llm(ctx, prev)

    assert len(calls) >= 2, "test requires at least one retry to exercise the invariant"
    prompts = {c["prompt"] for c in calls}
    stable_prefixes = {c["stable_prefix"] for c in calls}
    assert len(prompts) == 1, f"prompt must be byte-identical across retries, got {prompts!r}"
    assert len(stable_prefixes) == 1, (
        f"stable_prefix must be byte-identical across retries, got {stable_prefixes!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8 — subprocess error not completeness-retried: status="error" →
# returned immediately, called exactly 1x.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8_subprocess_error_not_completeness_retried(tmp_path, monkeypatch):
    from phase_5_integrity import _invoke_integrity_llm  # noqa: PLC0415

    calls = _make_fake_invoke(monkeypatch, error=True)
    ctx = _make_ctx(tmp_path / "scratch", org_config_extra={"integrity_verdict_retry_max": 3})
    prev = _make_prev(tmp_path)

    result = _invoke_integrity_llm(ctx, prev)

    assert len(calls) == 1
    assert result.status == "error"
    assert result.error_code == "E_TIMEOUT"


# ═══════════════════════════════════════════════════════════════════════════
# AC9 — regression: #781 fail-closed parser is unchanged. Prose that mentions
# a verdict token but never as a standalone line still parses to UNKNOWN.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac9_parser_unchanged_prose_with_embedded_mention_stays_unknown():
    from phase_5_integrity import _parse_verdict  # noqa: PLC0415

    assert _parse_verdict(_PROSE_WITH_EMBEDDED_MENTION) == "UNKNOWN"
