"""RED tests for GH334 — structured `{prompt, stable_prefix}` prompt contract
at the LLM seam + per-adapter prompt caching (Slice 1+2+3), v3 contract.

Spec: SHARED/memory/Decisions/2026-07-12_GH334_prompt_caching_seam_spec.md
(§2.0 v3 REVISED — read fully before touching this file again).

v3 contract (NOT the earlier concat contract): the seam `prompt` param is
ALWAYS the FULL prompt, unchanged. `stable_prefix`, when non-empty, is a HINT
— a contiguous substring of `prompt` that an ADAPTER may hoist into its
cacheable channel. Dispatch passes `stable_prefix` to the backend ONLY when
non-empty (conditional threading, §2.2) — this is the back-compat fix for the
36 TypeError regressions the earlier (rejected) concat design caused.

Not-yet-existing symbols/kwargs (`stable_prefix` on invoke_llm_subprocess /
_invoke_subprocess / _invoke_in_session / _build_request_body,
`_RED_STABLE_PREFIX` / `_red_stable_prefix`) are accessed via
inspect.signature / getattr / a try/except TypeError INSIDE each test body
only (§1q extension / D1CF5FDF) so this file COLLECTS cleanly and fails at
assert time, never at collection time.

sys.path is NOT touched here — conftest.py already installs the
conftest-import-time singleton (engine_py root + workflows dir) per the
§1q/81F97F3D gate.

§1i: no singleton/time-dependent resource under test — argv/req.json/body
captures are pre-staged via mocked Popen/_atomic_write_json, never raced.
AC12 (full-suite delta) is NOT a unit test — verified at ship step 6; a
placeholder xfail notes that instead of faking a pass.

Stub-passability: only infra (subprocess.Popen, _atomic_write_json,
emit_resolver_resolved) is patched. The UUTs (invoke_llm_subprocess,
_invoke_subprocess, _invoke_in_session, _build_request_body,
_build_red_prompt) are never mocked/patched.
"""
from __future__ import annotations

import inspect
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine import llm_subprocess  # noqa: E402
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, register_backend  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.lib.reference_backends import anthropic_api  # noqa: E402


# ─── §1i autouse teardown ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


# ─── Popen capture helper (mirrors test_llm_subprocess_allowed_tools.py) ────
# This is the mechanism used to capture BOTH argv AND the stdin payload
# actually delivered to the read helper (_stream_read_events writes via
# proc.stdin.write(prompt) on a feeder thread) — same mechanism for AC2/AC3/AC9b.

_RESULT_EVENT = (
    '{"type":"result","subtype":"success",'
    '"result":"OK",'
    '"usage":{"input_tokens":1,"output_tokens":1,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":100}\n'
)


def _capture_popen():
    captured: list[dict] = []

    def _side_effect(argv, **kwargs):
        proc = MagicMock()
        proc.pid = 424242
        proc.returncode = 0
        proc.stdout = io.StringIO(_RESULT_EVENT)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        captured.append({"argv": list(argv), "stdin": proc.stdin})
        return proc

    return captured, _side_effect


def _stdin_writes(stdin_mock) -> list[str]:
    return [c.args[0] for c in stdin_mock.write.call_args_list]


# ─── phase_5_implement RED-prompt fixture helpers ──────────────────────────

def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar") -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-gh334",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_spec(scratchpad: Path, body: str) -> None:
    from bytedigger_engine.workflows.phase_5_implement import SPEC_DOC_RELPATH  # noqa: PLC0415
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body, encoding="utf-8")


def _seed_arch(scratchpad: Path, body: str) -> None:
    from bytedigger_engine.workflows.phase_5_implement import ARCHITECTURE_DOC_RELPATH  # noqa: PLC0415
    arch = scratchpad / ARCHITECTURE_DOC_RELPATH
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text(body, encoding="utf-8")


def _build_red(tmp_path: Path, *, question: str, spec_body: str, findings: str | None = None):
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = _make_ctx(scratchpad, question=question)
    _seed_spec(scratchpad, spec_body)
    _seed_arch(scratchpad, "## Approach\nbuild it\n")
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="prev")
    return _build_red_prompt(ctx, prev, findings=findings)


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — invoke_llm_subprocess accepts stable_prefix: str = ""
# ═══════════════════════════════════════════════════════════════════════════

def test_AC1_invoke_llm_subprocess_accepts_stable_prefix_kwarg_default_empty():
    sig = inspect.signature(invoke_llm_subprocess)
    assert "stable_prefix" in sig.parameters, (
        f"AC1: invoke_llm_subprocess must accept a stable_prefix kwarg; "
        f"params seen={list(sig.parameters)!r}"
    )
    param = sig.parameters["stable_prefix"]
    assert param.default == "", (
        f"AC1: stable_prefix must default to '' (empty string); got {param.default!r}"
    )
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "AC1: stable_prefix must be keyword-only (mirrors prompt/model/...)."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — subprocess back-compat inert: stable_prefix="" → no flag, whole prompt on stdin
# ═══════════════════════════════════════════════════════════════════════════

def test_AC2_subprocess_stable_prefix_empty_is_byte_identical_to_today():
    captured, side = _capture_popen()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        try:
            invoke_llm_subprocess(
                prompt="THE FULL PROMPT",
                model="claude-3-haiku-20240307",
                timeout_sec=10,
                step_name="ac2",
                stable_prefix="",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
        except TypeError as e:
            pytest.fail(
                f"AC2 pre-GREEN: invoke_llm_subprocess does not accept "
                f"stable_prefix kwarg yet (TypeError: {e}). Expected RED failure."
            )

    assert len(captured) == 1, f"expected exactly 1 Popen call, got {len(captured)}"
    argv = captured[0]["argv"]
    assert "--append-system-prompt" not in argv, (
        f"AC2: stable_prefix='' must NOT inject --append-system-prompt; argv={argv!r}"
    )
    writes = _stdin_writes(captured[0]["stdin"])
    assert writes == ["THE FULL PROMPT"], (
        f"AC2: stdin must carry the whole (unmodified) prompt when "
        f"stable_prefix=''; got writes={writes!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — subprocess split (non-empty): argv gets flag, stdin gets prompt minus template
# ═══════════════════════════════════════════════════════════════════════════

def test_AC3_subprocess_stable_prefix_nonempty_splits_argv_and_stdin():
    prompt = "AAA TPL BBB"
    captured, side = _capture_popen()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        try:
            invoke_llm_subprocess(
                prompt=prompt,
                model="claude-3-haiku-20240307",
                timeout_sec=10,
                step_name="ac3",
                stable_prefix="TPL",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
        except TypeError as e:
            pytest.fail(
                f"AC3 pre-GREEN: invoke_llm_subprocess does not accept "
                f"stable_prefix kwarg yet (TypeError: {e}). Expected RED failure."
            )

    assert len(captured) == 1, f"expected exactly 1 Popen call, got {len(captured)}"
    argv = captured[0]["argv"]
    assert "--append-system-prompt" in argv, (
        f"AC3: stable_prefix='TPL' must inject --append-system-prompt; argv={argv!r}"
    )
    idx = argv.index("--append-system-prompt")
    assert argv[idx + 1] == "TPL", (
        f"AC3: --append-system-prompt value must be the stable_prefix ('TPL'); "
        f"got {argv[idx + 1]!r} argv={argv!r}"
    )
    writes = _stdin_writes(captured[0]["stdin"])
    expected_stdin = prompt.replace("TPL", "", 1)
    assert writes == [expected_stdin], (
        f"AC3: stdin must carry prompt.replace(stable_prefix, '', 1) "
        f"(template removed from the user turn); expected {expected_stdin!r}, "
        f"got writes={writes!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC4/AC5 — in-session req.json payload (v3: prompt stays FULL, always)
# ═══════════════════════════════════════════════════════════════════════════

def _invoke_in_session_capture(
    stable_prefix_kwargs: dict, tmp_path: Path, *, prompt: str,
) -> dict:
    """Call invoke_llm_subprocess(backend='claude-in-session', timeout_sec=0)
    with _atomic_write_json patched to capture the req.json payload.
    timeout_sec=0 makes the poll loop exit immediately (E_LLM_NO_RESULT_EVENT)
    without sleeping — we only care about the captured payload, not the result.
    """
    telemetry_ctx.set_current_run(
        event_log=None, run_id="RUN-GH334", step_name="ac", phase="phase_X"
    )
    captured: list[dict] = []

    def _fake_atomic_write(path, payload):
        captured.append(dict(payload))

    with patch("bytedigger_engine.llm_subprocess._atomic_write_json", side_effect=_fake_atomic_write), \
         patch.dict("os.environ", {"HAL_RUNNER_REQUEST_DIR": str(tmp_path)}):
        try:
            invoke_llm_subprocess(
                prompt=prompt,
                model="claude-opus-4-5",
                timeout_sec=0,
                step_name="ac_in_session",
                backend="claude-in-session",
                **stable_prefix_kwargs,
            )
        except TypeError as e:
            pytest.fail(
                f"in-session pre-GREEN: invoke_llm_subprocess does not accept "
                f"stable_prefix kwarg yet (TypeError: {e}). Expected RED failure."
            )
    assert len(captured) == 1, f"expected exactly 1 req.json write, got {len(captured)}"
    return captured[0]


def test_AC4_in_session_stable_prefix_empty_omits_key_prompt_unchanged(tmp_path):
    prompt = "THE FULL PROMPT"
    payload = _invoke_in_session_capture({"stable_prefix": ""}, tmp_path, prompt=prompt)
    assert "stable_prefix" not in payload, (
        f"AC4: req.json must NOT carry a stable_prefix key when empty; "
        f"payload keys={list(payload)!r}"
    )
    assert payload.get("prompt") == prompt, (
        f"AC4: req.json prompt must be byte-identical to the input prompt "
        f"when stable_prefix=''; got {payload.get('prompt')!r}"
    )


def test_AC5_in_session_prompt_stays_full_plus_hint(tmp_path):
    """AC5 (v3 §2.0): in-session is NON-splitting. payload['prompt'] MUST
    remain the FULL, UNCHANGED prompt (NOT concatenated, NOT split) — the
    servicer reads only 'prompt' and must see the complete template.
    payload['stable_prefix'] carries the hint additively.
    """
    prompt = "AAA TPL BBB"
    payload = _invoke_in_session_capture(
        {"stable_prefix": "TPL"}, tmp_path, prompt=prompt,
    )
    assert payload.get("prompt") == prompt, (
        f"AC5: payload['prompt'] must stay the FULL, unchanged prompt "
        f"(v3: in-session does not split); got {payload.get('prompt')!r}"
    )
    assert payload.get("stable_prefix") == "TPL", (
        f"AC5: req.json must carry stable_prefix='TPL'; payload={payload!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC6/AC7 — anthropic _build_request_body (order-preserving split)
# ═══════════════════════════════════════════════════════════════════════════

def _call_build_request_body(prompt: str, **extra):
    fn = anthropic_api._build_request_body
    kwargs = dict(model="sonnet", step_name="ac_anthropic", hard_gate=False)
    kwargs.update(extra)
    try:
        return fn(prompt, "claude-model-id", **kwargs)
    except TypeError as e:
        pytest.fail(
            f"pre-GREEN: _build_request_body does not accept stable_prefix "
            f"kwarg yet (TypeError: {e}). Expected RED failure."
        )


def test_AC6_anthropic_build_request_body_stable_prefix_empty_is_plain_string():
    body = _call_build_request_body("USER PROMPT", stable_prefix="")
    assert body["messages"][0]["content"] == "USER PROMPT", (
        f"AC6: stable_prefix='' must keep plain-string content (byte-identical "
        f"to pre-change body); got content={body['messages'][0]['content']!r}"
    )


def test_AC7_anthropic_build_request_body_stable_prefix_nonempty_order_preserving():
    prompt = "AAATPLBBB"
    body = _call_build_request_body(prompt, stable_prefix="TPL")
    content = body["messages"][0]["content"]
    expected = [
        {"type": "text", "text": "AAA"},
        {"type": "text", "text": "TPL", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "BBB"},
    ]
    assert content == expected, (
        f"AC7: stable_prefix='TPL' must produce an order-preserving "
        f"cache-breakpoint content list; got {content!r}"
    )
    joined = "".join(block["text"] for block in content)
    assert joined == prompt, (
        f"AC7: concatenation of block texts must equal the full prompt "
        f"(no bytes lost/reordered); got {joined!r} vs prompt={prompt!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8 — _RED_STABLE_PREFIX constant: call-invariant, len>500, substring of full prompt
# ═══════════════════════════════════════════════════════════════════════════

def _get_stable_prefix_from_result(result: StepResult) -> str:
    assert result.status == "ok", f"_build_red_prompt failed: {result.error!r}"
    sp = result.data.get("stable_prefix")
    assert sp, (
        f"AC8/AC10: prev.data['stable_prefix'] must be a non-empty string "
        f"after _build_red_prompt; data keys={list(result.data)!r}"
    )
    return sp


def test_AC8_red_stable_prefix_constant_call_invariant_long_and_substring(tmp_path):
    r1 = _build_red(
        tmp_path / "a", question="Add foo to bar", spec_body="## US1\nAdd foo\n",
    )
    r2 = _build_red(
        tmp_path / "b", question="A totally different feature request",
        spec_body="## US2\nDo something else entirely\n",
    )
    sp1 = _get_stable_prefix_from_result(r1)
    sp2 = _get_stable_prefix_from_result(r2)
    assert sp1 == sp2, (
        "AC8: the extracted stable_prefix (§1aa constant) must be byte-identical "
        "across two RED-prompt builds with different question/spec inputs.\n"
        f"sp1[:200]={sp1[:200]!r}\nsp2[:200]={sp2[:200]!r}"
    )
    assert len(sp1) > 500, (
        f"AC8: stable_prefix must be a substantial extracted static run "
        f"(len>500); got len={len(sp1)}"
    )
    assert r1.data["prompt"].count(sp1) == 1, (
        "AC8 (v3, hardened): stable_prefix must occur EXACTLY ONCE in "
        "prev.data['prompt'] — this locks the single-occurrence contract "
        "that prompt.replace(stable_prefix, '', 1) depends on (adapter-side "
        f"split); got count={r1.data['prompt'].count(sp1)}"
    )
    assert r2.data["prompt"].count(sp2) == 1, (
        "AC8 (v3, hardened): stable_prefix must occur EXACTLY ONCE in "
        "prev.data['prompt'] for the second build too; "
        f"got count={r2.data['prompt'].count(sp2)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC9a — conditional dispatch: empty stable_prefix invokes backend WITHOUT the kwarg
# ═══════════════════════════════════════════════════════════════════════════

class _StrictSignatureBackend:
    """A backend double whose __call__ signature mirrors the real backend
    params but OMITS stable_prefix and has NO **kwargs — mirrors the 36
    pre-existing strict-signature test doubles that the v3 conditional-dispatch
    fix (§2.2) protects. If dispatch ever passes stable_prefix unconditionally,
    calling this backend raises TypeError.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self, *, prompt, model, timeout_sec, step_name, extra_data=None,
        allowed_tools=None, run_ctx=None, hard_gate=False, gate_label=None,
        straggler_cfg=None, idle_timeout_sec=None,
    ) -> StepResult:
        self.calls.append(dict(
            prompt=prompt, model=model, timeout_sec=timeout_sec, step_name=step_name,
            extra_data=extra_data, allowed_tools=allowed_tools, run_ctx=run_ctx,
            hard_gate=hard_gate, gate_label=gate_label, straggler_cfg=straggler_cfg,
            idle_timeout_sec=idle_timeout_sec,
        ))
        return StepResult(status="ok", data={"raw_response": "OK"}, duration_ms=0, step_name=step_name)


def test_AC9a_conditional_dispatch_empty_stable_prefix_omits_kwarg_for_strict_backend():
    backend = _StrictSignatureBackend()
    register_backend("strict-ac9a", backend, manifest_source="harness_tool_record", overwrite=True)

    try:
        result = invoke_llm_subprocess(
            prompt="x",
            model="m",
            timeout_sec=1,
            step_name="ac9a",
            backend="strict-ac9a",
            stable_prefix="",
        )
    except TypeError as e:
        pytest.fail(
            f"AC9a: invoke_llm_subprocess(stable_prefix='') must NOT raise "
            f"TypeError against a strict-signature backend lacking the "
            f"stable_prefix param (conditional dispatch, §2.2). Got: {e}"
        )

    assert result.status == "ok", f"unexpected backend error: {result.error!r}"
    assert len(backend.calls) == 1, f"expected exactly 1 backend call, got {len(backend.calls)}"
    assert "stable_prefix" not in backend.calls[0], (
        "AC9a: dispatch must NOT pass a stable_prefix kwarg to the backend "
        "when stable_prefix is empty (conditional threading, §2.2); "
        f"captured kwargs={list(backend.calls[0])!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC9b — subprocess split lossless (>=2 distinct inputs)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "prompt,stable_prefix",
    [
        ("AAATPLBBB", "TPL"),
        ("head STABLE tail more tail", "STABLE"),
    ],
)
def test_AC9b_subprocess_split_lossless(prompt, stable_prefix):
    assert stable_prefix in prompt, "fixture precondition: stable_prefix must be a substring of prompt"
    captured, side = _capture_popen()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        try:
            invoke_llm_subprocess(
                prompt=prompt,
                model="claude-3-haiku-20240307",
                timeout_sec=10,
                step_name="ac9b",
                stable_prefix=stable_prefix,
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
        except TypeError as e:
            pytest.fail(
                f"AC9b pre-GREEN: invoke_llm_subprocess does not accept "
                f"stable_prefix kwarg yet (TypeError: {e}). Expected RED failure."
            )

    assert len(captured) == 1
    writes = _stdin_writes(captured[0]["stdin"])
    expected_stdin = prompt.replace(stable_prefix, "", 1)
    assert writes == [expected_stdin], (
        f"AC9b: no bytes lost — stdin must equal prompt.replace(stable_prefix,'',1); "
        f"expected {expected_stdin!r}, got writes={writes!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC13 — subprocess defensive not-found: stable_prefix NOT a substring of prompt
# (gate REVISE Finding: guards template duplication / spurious flag when the
# defensive "non-empty AND NOT in prompt" branch is hit — §2.4 "else" clause)
# ═══════════════════════════════════════════════════════════════════════════

def test_AC13_subprocess_stable_prefix_not_substring_no_flag_full_stdin():
    prompt = "no match here"
    stable_prefix = "ZZZ"
    assert stable_prefix not in prompt, "fixture precondition: stable_prefix must NOT be a substring"
    captured, side = _capture_popen()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        try:
            invoke_llm_subprocess(
                prompt=prompt,
                model="claude-3-haiku-20240307",
                timeout_sec=10,
                step_name="ac13",
                stable_prefix=stable_prefix,
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
        except TypeError as e:
            pytest.fail(
                f"AC13 pre-GREEN: invoke_llm_subprocess does not accept "
                f"stable_prefix kwarg yet (TypeError: {e}). Expected RED failure."
            )

    assert len(captured) == 1, f"expected exactly 1 Popen call, got {len(captured)}"
    argv = captured[0]["argv"]
    assert "--append-system-prompt" not in argv, (
        "AC13: stable_prefix not-found in prompt must NOT inject "
        f"--append-system-prompt (defensive fallback); argv={argv!r}"
    )
    writes = _stdin_writes(captured[0]["stdin"])
    assert writes == [prompt], (
        "AC13: stable_prefix not-found must leave stdin as the FULL, "
        f"unmodified prompt (no removal, no duplication); got writes={writes!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC14 — anthropic defensive not-found: no ValueError from .index(), plain content
# ═══════════════════════════════════════════════════════════════════════════

def test_AC14_anthropic_stable_prefix_not_substring_plain_content():
    prompt = "no match"
    stable_prefix = "ZZZ"
    assert stable_prefix not in prompt, "fixture precondition: stable_prefix must NOT be a substring"
    try:
        body = _call_build_request_body(prompt, stable_prefix=stable_prefix)
    except ValueError as e:
        pytest.fail(
            f"AC14: _build_request_body must NOT raise ValueError when "
            f"stable_prefix is not found in prompt (defensive .index() guard); "
            f"got: {e}"
        )
    assert body["messages"][0]["content"] == prompt, (
        "AC14: stable_prefix not-found must fall back to plain-string content "
        f"(unchanged); got content={body['messages'][0]['content']!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC15 — anthropic empty before/after block omission (prefix at start/end)
# ═══════════════════════════════════════════════════════════════════════════

def test_AC15_anthropic_prefix_at_start_and_end_no_empty_blocks():
    # prefix at start — NO leading empty {"text": ""} block
    body_start = _call_build_request_body("TPLxxx", stable_prefix="TPL")
    content_start = body_start["messages"][0]["content"]
    expected_start = [
        {"type": "text", "text": "TPL", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "xxx"},
    ]
    assert content_start == expected_start, (
        "AC15: prefix-at-start must omit the empty leading block "
        f"(no {{'text': ''}} before the breakpoint); got {content_start!r}"
    )

    # prefix at end — NO trailing empty block
    body_end = _call_build_request_body("xxxTPL", stable_prefix="TPL")
    content_end = body_end["messages"][0]["content"]
    expected_end = [
        {"type": "text", "text": "xxx"},
        {"type": "text", "text": "TPL", "cache_control": {"type": "ephemeral"}},
    ]
    assert content_end == expected_end, (
        "AC15: prefix-at-end must omit the empty trailing block "
        f"(no {{'text': ''}} after the breakpoint); got {content_end!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC10 — RED call-site wired + prev.data["prompt"] UNCHANGED (full, contains template)
# ═══════════════════════════════════════════════════════════════════════════

def test_AC10_red_callsite_wires_stable_prefix_and_prompt_stays_full(tmp_path):
    # Part A: unit-assert the builder sets prev.data["stable_prefix"] non-empty
    # AND prev.data["prompt"] is the FULL prompt (still contains the template).
    r = _build_red(tmp_path, question="Add foo to bar", spec_body="## US1\nAdd foo\n")
    stable_prefix = _get_stable_prefix_from_result(r)
    assert stable_prefix in r.data["prompt"], (
        "AC10 (v3): prev.data['prompt'] must remain the FULL prompt — the "
        "builder must NOT remove the stable_prefix from it (no content "
        "subtraction at the builder level; the split happens adapter-side)."
    )

    # Part B: grep prod source for the literal wiring at the invoke_llm_subprocess
    # call inside _invoke_red_llm (deterministic cite-verify, not a text-presence
    # stand-in for behavior — Part A already forces the real behavioral contract).
    from bytedigger_engine.workflows import phase_5_implement as _p5
    src_path = Path(inspect.getsourcefile(_p5))
    src = src_path.read_text(encoding="utf-8")
    # Isolate the _invoke_red_llm function body to avoid false hits elsewhere.
    start = src.index("def _invoke_red_llm(")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    assert 'stable_prefix=prev.data.get("stable_prefix' in body, (
        "AC10: _invoke_red_llm must pass stable_prefix=prev.data.get(\"stable_prefix\", ...) "
        "to invoke_llm_subprocess; literal kwarg not found in the function body."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC11 — retry re-entry idempotent: stable_prefix identical with/without findings
# ═══════════════════════════════════════════════════════════════════════════

def test_AC11_retry_with_findings_yields_identical_stable_prefix(tmp_path):
    r_no_findings = _build_red(
        tmp_path / "no_findings", question="Add foo to bar",
        spec_body="## US1\nAdd foo\n", findings=None,
    )
    r_with_findings = _build_red(
        tmp_path / "with_findings", question="Add foo to bar",
        spec_body="## US1\nAdd foo\n", findings="Validator found: missing edge case X.",
    )
    sp_no = _get_stable_prefix_from_result(r_no_findings)
    sp_with = _get_stable_prefix_from_result(r_with_findings)
    assert sp_no == sp_with, (
        "AC11: stable_prefix must be byte-identical between the first attempt "
        "(no findings) and a retry (findings appended) — the system-prompt stays "
        "cacheable across retries.\n"
        f"sp_no[:200]={sp_no[:200]!r}\nsp_with[:200]={sp_with[:200]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC12 — full-suite delta is a ship-step-6 verification gate, not a unit test.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.xfail(
    reason="AC12 (zero new full-suite failures, §1r delta math) is verified at "
    "ship step 6 (full pytest + bun test), not as a unit test. Placeholder only.",
    strict=False,
)
def test_AC12_placeholder_full_suite_delta_verified_at_ship_step_6():
    pytest.skip("AC12 is a full-suite verify-step gate — see ship checklist step 6.")
