"""RED tests — GH1169: agent-sdk hang-to-timeout auto-recovery
(per-attempt IDLE-GAP watchdog + one-shot backend fallback).

Spec: SHARED/memory/Decisions/2026-07-25_GH1169_agent_sdk_hang_recovery_spec.md
(round 6 — RE-FROZEN after Opus gate round 5 REJECT: MAJOR-1 "AC28 cannot
detect the regression it exists to prevent" -> AC28 becomes an INPUT fence
via a pass-through RECORDER on `_effective_idle_gap` (D9), single-source-of-
`idle` clause added (D10), over-claims corrected (D11), round-5 minors +
cheap edges folded incl. new AC29 empty-env (D12). Round 5 was RE-FROZEN
after Opus gate round 4 REJECT: F1 unmeasured default-ON threshold ->
watchdog becomes OPT-IN, frozen default `0` = DISABLED (D5), AC18 re-pinned
180->0.0, new AC27 (opt-in arming works) + AC28 (default-off shield, the F1
fence) (D6))

29 tests, one per AC1-AC29 (spec §3 table).

Part A (agent_sdk.py, AC1-AC8, AC17-AC20, AC24-AC26): `_sdk_idle_timeout_sec`,
`_effective_idle_gap`, `_OUTER_TIE_EPSILON_SEC`, `_HANG_RETRY_FLOOR_SEC`,
and the per-attempt IDLE-GAP watchdog/retry machinery do not exist yet on
`lib.reference_backends.agent_sdk` — deferred access (getattr / direct call
inside test bodies) so the file COLLECTS cleanly and FAILS at assert time,
not at collection/import time (D1CF5FDF / §1q).

Part B (llm_subprocess.py, AC9-AC16, AC21-AC23): `_dispatch_backend` and the
one-shot hang-fallback predicate do not exist yet — same deferred-access
discipline.

Round-2 semantics: the watchdog bounds the GAP BETWEEN yielded messages
(re-armed on every message), NOT total attempt duration. Renames vs round 1:
`HAL_AGENT_SDK_ATTEMPT_TIMEOUT_SEC`->`HAL_AGENT_SDK_IDLE_TIMEOUT_SEC`,
`_attempt_timeout_sec`->`_sdk_idle_timeout_sec`,
`_effective_attempt_timeout`->`_effective_idle_gap`, retry reason
`attempt_timeout`->`idle_gap_timeout`. `hang_attempts` keeps its name.

Round-3 additions: AC17's long-but-alive fake now awaits the module-level
`_REAL_ASYNCIO_SLEEP` (captured before any fixture patches `asyncio.sleep`)
between yields, with re-pinned numbers (cap=0.5s, gap=0.01s, N=120,
total~1.2s) for >=10x margin both ways — a real event-loop yield, not
`time.sleep()` (which never yields control, so no asyncio timer could fire
mid-stream and a wrong total-duration implementation would pass vacuously).
New AC24 covers the second arm-guard conjunct (`_effective_idle_gap(...) > 0`)
independent of AC4's `_sdk_idle_timeout_sec() > 0` conjunct.

Round-4 additions: AC25 fences the third arm-guard conjunct
(`effective >= idle`) -- a clamped-but-POSITIVE effective gap (AC24 does not
catch it) must still NOT arm the inner timer (R3-F1, the round-3 gate
finding). AC26 is the positive-direction mirror of AC17: real messages flow
under an ARMED watchdog, then the stream goes silent, and the watchdog must
still fire and recover via retry -- pinned so exactly one outcome is
possible. AC18's frozen default moved 600 -> 180 (D2, round 4).

Round-5 additions (D5/D6, after gate round 4 REJECT F1): the watchdog is now
OPT-IN. AC18 re-pinned again, 180 -> 0.0 -- the frozen default with the env
UNSET is DISABLED, not a positive threshold. AC27 pins that opt-in arming
still WORKS (explicit positive env -> behaves per AC1/AC2). AC28 is the F1
fence: env UNSET + a fake silent past any plausible gap must behave exactly
as legacy prod (one call, no retry event, hang_attempts==0, and -- routed
through invoke_llm_subprocess -- no runner_backend_fallback either). Every
AC needing an ARMED watchdog already pinned its own env explicitly
(AC1/2/3/8/17/19/20/24/25/26); none relied on the module default, so only
AC18 (which asserts the default itself) needed its literal changed.

Round-6 additions (D9/D10/D11/D12, after gate round 5 REJECT MAJOR-1): AC28's
`timeout_sec=0.5` outcome-only form could not detect a GREEN that coalesces
`idle = _sdk_idle_timeout_sec() or 900.0` downstream -- the third arming
conjunct stands the run down for ANY re-armed default >=0.25s, so such a
GREEN passed all 28 round-5 ACs while arming on EVERY message in production.
AC28 is RE-SHAPED into an INPUT fence: a pass-through RECORDER wraps the
real `_effective_idle_gap` and asserts every recorded `idle_timeout` is <=0
and `t0` is constant, INDEPENDENTLY of the outer budget size (D9). AC5 gains
a direct probe of the new named helper `_remaining_outer_budget` (D12/
MINOR-1, both `_effective_idle_gap` and the retry-floor test route through
it). AC20 gains the assertion that a salvage-at-fire SUCCESS does not
increment `hang_attempts` (D12/edge 4). New AC29 pins that an empty/
whitespace env behaves exactly as unset, never a ValueError (D12/edge 2).

§1q: no `spec_from_file_location` / `exec_module`; no module-level
`sys.path` mutation; no `from conftest import`.
§1i: fake `claude_agent_sdk` (async-generator, deterministic `asyncio.Event().wait()`
hang, no real wall-clock for hangs), tiny env-driven timeouts
(`HAL_AGENT_SDK_IDLE_TIMEOUT_SEC`), `asyncio.sleep` patched to a pass-through
recorder, `HAL_OUTAGE_PROBE=0` + tiny backoff knobs pre-staged via monkeypatch
BEFORE invocation. AC17's long-but-alive fake awaits the REAL asyncio.sleep
(0.01s x 120 = ~1.2s total) — a real event-loop yield so a wrongly-armed
timer under test genuinely gets the chance to fire, not a wall-clock margin
being asserted on.
§1l: only the external `claude_agent_sdk` module (sys.modules injection, GH1157
sibling pattern) and, for Part B, the `_BACKENDS` registry entries (established
seam via `register_backend`/`reset_backends`) are faked. The UUT
(`agent_sdk_backend`, `_effective_idle_gap`, `invoke_llm_subprocess`,
`_dispatch_backend`) is exercised directly, unstubbed.

Expected pre-GREEN: 29 FAIL / 0 PASS / 0 collect errors.
"""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass

import asyncio

import pytest

from bytedigger_engine.event_log import EventLog

# §1i/round-3 AC17 fix: module-level reference captured BEFORE any fixture
# patches asyncio.sleep — a module-level binding is immune to
# `monkeypatch.setattr(asyncio, "sleep", ...)` performed later by the
# autouse `_record_sleeps` fixture (which rewrites asyncio.sleep to
# `sleep(0)`, collapsing elapsed time to ~0 and making a real-gap test
# toothless).
_REAL_ASYNCIO_SLEEP = asyncio.sleep

# Both modules already exist on prod today (only specific NEW symbols on them
# are not-yet-existing — those are accessed via getattr/direct-call inside
# test bodies, never at module import time).
from bytedigger_engine import llm_subprocess
from bytedigger_engine import telemetry_ctx


# ---------------------------------------------------------------------------
# Deferred import (non-collectable RED guard — D1CF5FDF / §1q)
# ---------------------------------------------------------------------------

def _import_agent_sdk_backend():
    from bytedigger_engine.lib.reference_backends import agent_sdk as mod  # noqa: PLC0415
    return mod


# ---------------------------------------------------------------------------
# Fake claude_agent_sdk — REUSED from test_gh1157_agent_sdk_retry.py
# (§1q RED harness note), EXTENDED per round-2/round-4 §1q with fake-behavior
# keys required by the redesign:
#   - "messages"/"gap_sec": yields N non-result messages with a small real
#     gap between each, then the ResultMessage (drives AC17 long-but-alive).
#   - "result_then_hang": yields the ResultMessage FIRST, then hangs (drives
#     AC20 salvage-at-watchdog-fire; the original fake checks "hang" before
#     any yield, so this terminal exit is not otherwise constructible).
#   - "messages"/"gap_sec" + "then_hang": yields the N messages with real
#     gaps, THEN hangs instead of yielding a ResultMessage (round-4, drives
#     AC26 mid-stream hang; no salvageable result exists here, so the
#     watchdog fire must go down the RETRY path, not salvage).
# query() is an ASYNC GENERATOR; each call pops the next behavior off a
# shared queue. `{"hang": True}` blocks deterministically on
# `asyncio.Event().wait()` — killed by the watchdog under test, never by
# real wall-clock.
# ---------------------------------------------------------------------------

@dataclass
class ResultMessage:
    result: str | None = None
    usage: dict | None = None
    session_id: str | None = None
    is_error: bool = False


class RetryableExc(Exception):
    def __init__(self, msg, status_code=None, exit_code=None, stderr=None):
        super().__init__(msg)
        if status_code is not None:
            self.status_code = status_code
        if exit_code is not None:
            self.exit_code = exit_code
        if stderr is not None:
            self.stderr = stderr


def _install_fake_agent_sdk(monkeypatch):
    recorded_calls = []
    behavior_queue = []

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.kwargs = kwargs

    def _assistant_like():
        return types.SimpleNamespace(kind="assistant-like")

    async def query(*, prompt, options):
        recorded_calls.append(options)
        behavior = behavior_queue.pop(0) if behavior_queue else {}
        if behavior.get("hang"):
            await asyncio.Event().wait()
            return
        if "result_then_hang" in behavior:
            yield behavior["result_then_hang"]
            await asyncio.Event().wait()
            return
        if "messages" in behavior:
            # §1i round-3 AC17 fix: await the REAL asyncio.sleep (captured at
            # module import time, immune to the autouse asyncio.sleep patch)
            # so the event loop actually yields between messages and a timer
            # armed by the UUT under test can fire mid-stream if the UUT is
            # wrongly bounding TOTAL duration instead of per-message gap.
            gap = behavior.get("gap_sec", 0)
            for m in behavior["messages"]:
                if gap:
                    await _REAL_ASYNCIO_SLEEP(gap)
                yield m
            if behavior.get("then_hang"):
                # round-4 AC26: real messages flowed, then silence forever —
                # mirrors "result_then_hang" but with no salvageable result,
                # so the watchdog fire must go down the RETRY path.
                await asyncio.Event().wait()
                return
            if "result" in behavior:
                yield behavior["result"]
            return
        yield _assistant_like()
        if "stderr_lines" in behavior:
            for line in behavior["stderr_lines"]:
                options.kwargs.get("stderr", lambda _l: None)(line)
        if "result" in behavior:
            yield behavior["result"]
        if "raise" in behavior:
            raise behavior["raise"]

    fake_mod = types.ModuleType("claude_agent_sdk")
    fake_mod.query = query
    fake_mod.ClaudeAgentOptions = ClaudeAgentOptions
    fake_mod.ResultMessage = ResultMessage
    fake_mod.recorded_calls = recorded_calls
    fake_mod.behavior_queue = behavior_queue
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_mod)
    return fake_mod


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root)] + list(args),
        check=True, capture_output=True, text=True,
    )


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "commit", "--allow-empty", "-q", "-m", "init")


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    return root


@pytest.fixture(autouse=True)
def _reset_state():
    _KEYS = ("lib.reference_backends.agent_sdk", "claude_agent_sdk")
    _saved = {k: sys.modules.get(k) for k in _KEYS}
    yield
    for k in _KEYS:
        orig = _saved[k]
        if orig is not None:
            sys.modules[k] = orig
        else:
            sys.modules.pop(k, None)


@pytest.fixture(autouse=True)
def _pre_stage_env(monkeypatch):
    """§1i: pre-stage outage-probe kill-switch + tiny deterministic backoff
    envs BEFORE invoking the backend — no network, no unbounded sleeps."""
    monkeypatch.setenv("HAL_OUTAGE_PROBE", "0")
    monkeypatch.setenv("HAL_AGENT_SDK_BACKOFF_BASE_SEC", "0.001")
    monkeypatch.setenv("HAL_AGENT_SDK_BACKOFF_CAP_SEC", "0.002")


@pytest.fixture(autouse=True)
def _record_sleeps(monkeypatch):
    """Pass-through recorder on asyncio.sleep so retry-loop delays are
    observable without actually waiting real wall-clock time in tests."""
    import asyncio

    recorded = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *a, **kw):
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return recorded


@pytest.fixture(autouse=True)
def _reset_llm_subprocess_backends():
    """§1i: pre-stage the backend registry to the clean shipped default
    before/after each test — Part B tests register/overwrite fake backends."""
    llm_subprocess.reset_backends()
    yield
    llm_subprocess.reset_backends()


@pytest.fixture(autouse=True)
def _clear_telemetry_ctx():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


def _common_kwargs(root, run_ctx=None):
    return dict(
        model="claude-sonnet",
        extra_data={"workspace_root": str(root)},
        allowed_tools=None,
        run_ctx=run_ctx,
        hard_gate=False,
        gate_label=None,
        straggler_cfg=None,
        idle_timeout_sec=None,
    )


def _make_run_ctx(tmp_path, run_id, step_name="gate.step1"):
    log = EventLog(tmp_path / "events.jsonl")
    return types.SimpleNamespace(
        event_log=log, run_id=run_id, step_name=step_name, phase=None, cycle=None,
    ), log


# ===========================================================================
# Part A — agent_sdk.py per-attempt IDLE-GAP watchdog + retry-on-hang
# (AC1-AC8, AC17-AC20, AC24-AC29)
# ===========================================================================

# ─── AC1 — hang then success: watchdog retries, returns SUCCESS, 2 calls ──────

def test_ac1_hang_then_success_retries_and_returns_success(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="OK_AFTER_HANG", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-hang-ok", is_error=False,
        ),
    })
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.05")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.status == "ok", (
        f"AC1: hang-then-success must recover via the per-attempt idle-gap watchdog retry; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 2, (
        f"AC1: expected exactly 2 query() invocations (hang attempt + success retry); "
        f"got {len(fake.recorded_calls)}"
    )


# ─── AC2 — idle-gap timeout retry emits agent_sdk_retry(reason=idle_gap_timeout) ──

def test_ac2_idle_gap_timeout_retry_emits_agent_sdk_retry_event(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="OK", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-ac2", is_error=False,
        ),
    })
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.05")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac2")

    mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    matching = [r for r in rows if r.get("payload", {}).get("reason") == "idle_gap_timeout"]
    assert matching, (
        f"AC2: expected an agent_sdk_retry event with reason=='idle_gap_timeout'; got rows={rows!r}"
    )
    payload = matching[0]["payload"]
    assert payload.get("attempt") == 1, f"AC2: expected attempt==1; got {payload!r}"
    assert payload.get("max_attempts") == 3, f"AC2: expected max_attempts==3; got {payload!r}"


# ─── AC3 — repeated hangs exhaust the attempt budget, bounded call count ──────

def test_ac3_hang_exhausts_max_attempts_then_times_out(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    fake.behavior_queue.append({"hang": True})
    monkeypatch.setenv("HAL_AGENT_SDK_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.05")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC3: exhausting the attempt budget under repeated hangs must yield "
        f"E_LLM_API_TIMEOUT; got {result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 2, (
        f"AC3: attempt budget=2 must bound query() calls at exactly 2 (no 3rd attempt); "
        f"got {len(fake.recorded_calls)}"
    )


# ─── AC4 — watchdog disabled (0 / -1): legacy behavior preserved exactly ──────

def test_ac4_watchdog_disabled_legacy_behavior_preserved(monkeypatch, tmp_path):
    for disable_val in ("0", "-1"):
        fake = _install_fake_agent_sdk(monkeypatch)
        fake.behavior_queue.append({"hang": True})
        monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", disable_val)
        mod = _import_agent_sdk_backend()
        leg_tmp_path = tmp_path / f"leg-{disable_val}"
        leg_tmp_path.mkdir()
        root = _repo(leg_tmp_path)
        run_ctx, log = _make_run_ctx(tmp_path, f"run-ac4-{disable_val}")

        result = mod.agent_sdk_backend(
            prompt="p", timeout_sec=0.2, step_name="gate.step1", **_common_kwargs(root, run_ctx)
        )

        assert result.error_code == "E_LLM_API_TIMEOUT", (
            f"AC4 [{disable_val}]: disabled watchdog must still time out via the outer "
            f"wait_for; got {result.error_code!r}"
        )
        assert len(fake.recorded_calls) == 1, (
            f"AC4 [{disable_val}]: disabled watchdog means exactly 1 query() attempt "
            f"(legacy path); got {len(fake.recorded_calls)}"
        )
        retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
        assert retry_rows == [], (
            f"AC4 [{disable_val}]: disabled watchdog must emit NO agent_sdk_retry event; "
            f"got {retry_rows!r}"
        )
        assert result.data is not None and result.data["hang_attempts"] == 0, (
            f"AC4 [{disable_val}]: outer-expiry timeout_data must carry hang_attempts==0 "
            f"(KeyError today — key doesn't exist); got {result.data!r}"
        )


# ─── AC5 — _effective_idle_gap: clamp to remaining outer budget minus epsilon ──

def test_ac5_effective_idle_gap_clamps_to_remaining_budget_minus_epsilon(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()

    fn = getattr(mod, "_effective_idle_gap", None)
    assert callable(fn), (
        "AC5: _effective_idle_gap must exist as a named module-level helper (§1aa)"
    )

    epsilon = getattr(mod, "_OUTER_TIE_EPSILON_SEC", None)
    assert epsilon == 0.25, (
        f"AC5: _OUTER_TIE_EPSILON_SEC module constant must be exactly 0.25; got {epsilon!r}"
    )

    # §1aa/D12 (round 6, MINOR-1): the remaining-budget math is now named
    # separately -- both _effective_idle_gap's internal math AND the
    # §2.1.4c retry-floor test must route through THIS helper, never an
    # inline time.monotonic()-based expression.
    remaining_fn = getattr(mod, "_remaining_outer_budget", None)
    assert callable(remaining_fn), (
        "AC5: _remaining_outer_budget must exist as a named module-level helper (§1aa/D12)"
    )

    t0 = 1000.0

    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 40.0)
    remaining = remaining_fn(t0, 100.0)
    assert abs(remaining - 60.0) < 1e-9, (
        f"AC5: _remaining_outer_budget(t0, 100.0) at elapsed=40 must return the RAW "
        f"remaining budget 60.0 (outer_timeout_sec - elapsed), BEFORE epsilon subtraction; "
        f"got {remaining!r}"
    )
    got = fn(600.0, t0, 100.0)
    assert abs(got - (remaining - epsilon)) < 1e-9, (
        f"AC5: _effective_idle_gap must equal _remaining_outer_budget(...) - epsilon when "
        f"clamped (composition, §2.1.2b) -- both consumers must route through the SAME "
        f"named helper; got effective={got!r} remaining={remaining!r} epsilon={epsilon!r}"
    )
    assert abs(got - 59.75) < 1e-6, (
        f"AC5: _effective_idle_gap(600, t0, 100) at elapsed=40 must clamp to "
        f"~59.75 (remaining outer budget 60.0 minus epsilon 0.25), NOT 600; got {got!r}"
    )

    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 40.0)
    got2 = fn(50.0, t0, 1000.0)
    assert got2 == 50.0, (
        f"AC5: _effective_idle_gap(50, t0, 1000) must return the smaller "
        f"idle_timeout unclamped (remaining budget vastly exceeds it); got {got2!r}"
    )

    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 150.0)
    got3 = fn(600.0, t0, 100.0)
    assert got3 <= 0, (
        f"AC5: exhausted outer budget (elapsed=150 > outer=100) must clamp to <=0; "
        f"got {got3!r}"
    )

    # Epsilon must produce a STRICT inequality vs the bare remaining budget —
    # no same-instant tie with the outer timer (gate finding #6/#9).
    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 10.0)
    remaining = 100.0 - 10.0
    got4 = fn(600.0, t0, 100.0)
    assert got4 < remaining, (
        f"AC5: _effective_idle_gap must be STRICTLY LESS than the bare remaining budget "
        f"({remaining}) — got {got4!r}; epsilon={epsilon!r} not subtracted"
    )


# ─── AC6 — timeout_data.hang_attempts: >=1 on watchdog fire, ==0 on pure expiry ──

def test_ac6_hang_attempts_recorded_on_timeout_result(monkeypatch, tmp_path):
    # Case 1: watchdog fires, attempt budget exhausted immediately -> hang_attempts>=1
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    monkeypatch.setenv("HAL_AGENT_SDK_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.05")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root)
    )
    assert result.error_code == "E_LLM_API_TIMEOUT"
    assert result.data is not None and result.data.get("hang_attempts", 0) >= 1, (
        f"AC6: timeout after >=1 watchdog fire must carry hang_attempts>=1; got {result.data!r}"
    )

    # Case 2: pure outer-budget expiry (watchdog disabled) -> hang_attempts == 0
    fake2 = _install_fake_agent_sdk(monkeypatch)
    fake2.behavior_queue.append({"hang": True})
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0")
    mod2 = _import_agent_sdk_backend()

    result2 = mod2.agent_sdk_backend(
        prompt="p", timeout_sec=0.2, step_name="gate.step1", **_common_kwargs(root)
    )
    assert result2.error_code == "E_LLM_API_TIMEOUT"
    assert result2.data is not None and result2.data.get("hang_attempts", 0) == 0, (
        f"AC6: pure outer-budget expiry must carry hang_attempts==0; got {result2.data!r}"
    )


# ─── AC7 — existing timeout StepResult contract fields untouched (paired) ─────

def test_ac7_timeout_stepresult_retains_existing_contract_fields(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=0.2, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT"
    assert result.recoverable is True
    assert result.data is not None
    for key in ("worker_written_paths", "manifest_source", "timed_out", "stderr_tail", "retry_attempts"):
        assert key in result.data, f"AC7: existing timeout_data key {key!r} missing; got {result.data!r}"
    assert result.data["manifest_source"] == "git_diff"
    assert result.data["timed_out"] is True
    # Pairing assert (§3 AC7 rule): new key must ALSO be present so this test
    # is a genuine RED (existing-keys-only would pass vacuously today).
    assert result.data["hang_attempts"] == 0, (
        f"AC7: hang_attempts key must exist (0 on legacy watchdog-disabled path); "
        f"got {result.data!r}"
    )


# ─── AC8 — salvage wins even under an armed (but non-firing) watchdog ─────────

def test_ac8_salvage_wins_under_armed_watchdog(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="SALVAGE_UNDER_WATCHDOG", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-salvage-wd", is_error=False,
        ),
        "raise": RuntimeError("trailing error after salvageable success"),
    })
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "5")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac8")

    # Pairing assert (§3 AC8 rule): the watchdog helper must exist for this
    # test to be a genuine RED, not a pre-existing-behavior pass.
    assert callable(getattr(mod, "_sdk_idle_timeout_sec", None)), (
        "AC8: _sdk_idle_timeout_sec must exist as a module-level callable"
    )

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.status == "ok", (
        f"AC8: salvage must win over a trailing non-timeout exception even under an "
        f"armed watchdog; got status={result.status!r} error_code={result.error_code!r}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], f"AC8: no retry event expected on the salvage path; got {retry_rows!r}"


# ─── AC17 — long-but-alive stream never trips the idle-gap watchdog (decoy) ───

def test_ac17_long_but_alive_stream_never_trips_idle_gap_watchdog(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    # Round-3 re-pin (gate finding R2-F1): >=10x margin both ways.
    # cap=0.5s; per-gap=0.01s (1/50 of cap); N=120 -> total ~1.2s >> cap.
    # A WRONG total-duration-cap implementation would cancel well before
    # message 120 is reached; the correct per-message re-arm survives.
    n_messages = 120
    gap_sec = 0.01
    messages = [types.SimpleNamespace(kind=f"chunk-{i}") for i in range(n_messages)]
    fake.behavior_queue.append({
        "messages": messages,
        "gap_sec": gap_sec,
        "result": ResultMessage(
            result="LONG_ALIVE_OK", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-ac17", is_error=False,
        ),
    })
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.5")
    mod = _import_agent_sdk_backend()

    # Decoy pairing assert: the idle-gap helper must exist for this to be a
    # genuine RED, not a pre-existing-behavior pass.
    assert callable(getattr(mod, "_sdk_idle_timeout_sec", None)), (
        "AC17: _sdk_idle_timeout_sec must exist as a module-level callable"
    )

    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac17")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.status == "ok", (
        f"AC17: a long-but-alive stream (every individual gap far below the idle "
        f"timeout, TOTAL duration comfortably exceeding it) must complete successfully "
        f"via per-message re-arm, never be cancelled as a hang; got "
        f"status={result.status!r} error_code={result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC17: a total-duration-cap GREEN would cancel-and-retry here (>=2 calls); "
        f"idle-gap re-arm must invoke query() exactly once; got {len(fake.recorded_calls)}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], (
        f"AC17: no agent_sdk_retry event of ANY reason expected on a long-but-alive "
        f"stream; got {retry_rows!r}"
    )


# ─── AC18 — _sdk_idle_timeout_sec() frozen default 0.0 (DISABLED) with env unset ──
# (round-5 re-pin, D5: was 180 in round 4, 600 in rounds 1-3 — the watchdog is
# now OPT-IN. With the env unset the mechanism must be DISABLED, not armed at
# any positive threshold: an unmeasured default-ON value was gate round 4's
# blocking F1 finding — no live-replay baseline of healthy inter-message gaps
# exists, and a false fire is work-losing + budget-doubling. See §2.1.1/§2.4.)

def test_ac18_sdk_idle_timeout_sec_default_zero_disabled_when_unset(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()
    monkeypatch.delenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", raising=False)

    fn = getattr(mod, "_sdk_idle_timeout_sec", None)
    assert callable(fn), "AC18: _sdk_idle_timeout_sec must exist as a module-level callable"

    got = fn()
    assert got == 0.0, (
        f"AC18: default idle timeout with HAL_AGENT_SDK_IDLE_TIMEOUT_SEC unset must be "
        f"exactly 0.0 (the round-5 frozen default, §2.1.1, D5 — the watchdog is OPT-IN "
        f"and DISABLED by default; was 180.0 in round 4, 600.0 in rounds 1-3); got {got!r}"
    )


# ─── AC19 — budget floor blocks retry near outer expiry ───────────────────────

def test_ac19_budget_floor_blocks_retry_near_outer_expiry(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    monkeypatch.setenv("HAL_AGENT_SDK_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.05")
    mod = _import_agent_sdk_backend()

    floor = getattr(mod, "_HANG_RETRY_FLOOR_SEC", None)
    assert floor == 5.0, (
        f"AC19: _HANG_RETRY_FLOOR_SEC module constant must be exactly 5.0; got {floor!r}"
    )

    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac19")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=3, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC19: with the outer budget (3s) below the retry floor (5.0s), the idle-gap "
        f"fire must re-raise, NOT retry; got {result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC19: budget-floor conjunct must block retry even though attempt<max_attempts; "
        f"expected exactly 1 query() call, got {len(fake.recorded_calls)}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], (
        f"AC19: no retry event expected when the budget floor blocks retry; got {retry_rows!r}"
    )
    assert result.data is not None and result.data.get("hang_attempts") == 1, (
        f"AC19: the single watchdog fire before the floor-blocked re-raise must record "
        f"hang_attempts==1; got {result.data!r}"
    )


# ─── AC20 — salvage wins when the watchdog fires AFTER a ResultMessage ────────

def test_ac20_salvage_wins_when_watchdog_fires_after_result(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result_then_hang": ResultMessage(
            result="SALVAGE_AT_FIRE", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-ac20", is_error=False,
        ),
    })
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.05")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac20")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.status == "ok", (
        f"AC20: salvage must win when the idle-gap watchdog fires AFTER a ResultMessage "
        f"was already yielded (§2.1.4b); got status={result.status!r} "
        f"error_code={result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC20: exactly one query() call expected; got {len(fake.recorded_calls)}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], (
        f"AC20: no retry event expected on the salvage-at-fire path; got {retry_rows!r}"
    )
    # Round-6 addition (D12 / edge 4): a success via salvage-at-watchdog-fire
    # must NOT have incremented hang_attempts -- the increment (§2.1.4a) is
    # ordered AFTER the rule-b salvage check. `.get` with a 0 default is
    # correct HERE (the success path need not carry the key at all), unlike
    # the timeout rows elsewhere in this file which use SUBSCRIPT.
    assert result.data is None or result.data.get("hang_attempts", 0) == 0, (
        f"AC20: a SUCCESS via salvage-at-watchdog-fire must NOT increment hang_attempts "
        f"-- a GREEN incrementing BEFORE the rule-b salvage check ships a success whose "
        f"counter self-contradictorily reports one hang; got data={result.data!r}"
    )
    assert result.error_code is None, (
        f"AC20: salvage-at-fire must produce NO timeout StepResult at all -- error_code "
        f"must be None on this successful path; got error_code={result.error_code!r}"
    )


# ─── AC24 — arm-guard conjunct: positive knob but exhausted outer budget ──────

def test_ac24_arm_guard_blocks_timer_when_effective_gap_nonpositive(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    # Idle-timeout knob is positive (5s) but the outer budget (0.2s) is tiny,
    # so _effective_idle_gap(5, t0, 0.2) is negative: arming requires BOTH
    # _sdk_idle_timeout_sec() > 0 AND effective > 0 (§2.1.3) -- only the
    # first conjunct alone is exercised by AC4. Here no timer is armed; the
    # outer wait_for owns expiry at ~0.2s.
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "5")
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac24")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=0.2, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC24: a positive idle-timeout knob with an exhausted/negative effective "
        f"gap must still expire via the outer wait_for; got {result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC24: arm-guard must block the timer, no retry attempt; "
        f"expected exactly 1 query() call, got {len(fake.recorded_calls)}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], (
        f"AC24: no agent_sdk_retry event expected when the arm-guard blocks the timer; "
        f"got {retry_rows!r}"
    )
    assert result.data["hang_attempts"] == 0, (
        f"AC24: outer-expiry timeout_data must carry hang_attempts==0 via SUBSCRIPT "
        f"(key must exist unconditionally); got {result.data!r}"
    )


# ─── AC25 — R3-F1 fence: clamped-but-POSITIVE effective gap must NOT arm ──────

def test_ac25_clamped_positive_effective_gap_does_not_arm(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    # idle=5 (positive), outer=1 => effective = min(5, 1 - ~0 - 0.25) = 0.75:
    # POSITIVE (AC24's `effective > 0` guard does NOT catch it) but SMALLER
    # than idle=5 -- under D1's third arming conjunct (effective >= idle) no
    # timer may be armed; the outer wait_for owns expiry at ~1s. A
    # clamp-and-arm GREEN would instead arm wait_for(..., 0.75), fire the
    # inner timer first, and record hang_attempts==1 here (the round-3
    # R3-F1 defect this AC exists to kill).
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "5")
    mod = _import_agent_sdk_backend()

    fn = getattr(mod, "_effective_idle_gap", None)
    assert callable(fn), "AC25: _effective_idle_gap must exist as a named module-level helper"
    # Pin the clamped-positive regime directly against the helper (not just
    # end-to-end) so this row is provably distinct from AC24's non-positive
    # case: 0 < effective < idle.
    probe = fn(5.0, mod.time.monotonic(), 1.0)
    assert probe > 0, f"AC25: _effective_idle_gap(5, t0, 1) must be POSITIVE; got {probe!r}"
    assert probe < 5.0, f"AC25: _effective_idle_gap(5, t0, 1) must be SMALLER than idle=5; got {probe!r}"

    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac25")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=1, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC25: a clamped-but-positive effective gap must NOT arm the inner timer -- "
        f"the outer wait_for must own expiry; got {result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC25: no timer armed means exactly 1 query() call; got {len(fake.recorded_calls)}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], (
        f"AC25: no agent_sdk_retry event of ANY reason expected; got {retry_rows!r}"
    )
    assert result.data["hang_attempts"] == 0, (
        f"AC25: outer-expiry timeout_data must carry hang_attempts==0 via SUBSCRIPT "
        f"(a clamp-and-arm GREEN records 1 here); got {result.data!r}"
    )


# ─── AC26 — mid-stream hang: watchdog still fires AFTER messages flowed ───────

def test_ac26_watchdog_fires_after_messages_then_silence(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    n_messages = 10
    gap_sec = 0.01
    messages = [types.SimpleNamespace(kind=f"chunk-{i}") for i in range(n_messages)]
    fake.behavior_queue.append({
        "messages": messages,
        "gap_sec": gap_sec,
        "then_hang": True,
    })
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="OK_AFTER_MIDSTREAM_HANG", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-ac26", is_error=False,
        ),
    })
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.5")
    monkeypatch.setenv("HAL_AGENT_SDK_MAX_ATTEMPTS", "2")
    mod = _import_agent_sdk_backend()

    assert callable(getattr(mod, "_sdk_idle_timeout_sec", None)), (
        "AC26: _sdk_idle_timeout_sec must exist as a module-level callable"
    )

    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac26")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.status == "ok", (
        f"AC26: after real messages flowed and the stream then went silent, the "
        f"idle-gap watchdog must still fire and the retry must recover on the 2nd "
        f"attempt; got status={result.status!r} error_code={result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 2, (
        f"AC26: expected exactly 2 query() calls (mid-stream hang attempt + retry "
        f"success); a GREEN that never fires once a message has arrived, or that "
        f"anchors the deadline at stream start instead of the last message, would "
        f"leave this at 1; got {len(fake.recorded_calls)}"
    )
    rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    matching = [r for r in rows if r.get("payload", {}).get("reason") == "idle_gap_timeout"]
    assert len(matching) == 1, (
        f"AC26: expected exactly ONE agent_sdk_retry event with reason=='idle_gap_timeout'; "
        f"got rows={rows!r}"
    )
    payload = matching[0]["payload"]
    assert payload.get("attempt") == 1, f"AC26: expected attempt==1; got {payload!r}"
    assert payload.get("max_attempts") == 2, f"AC26: expected max_attempts==2; got {payload!r}"


# ─── AC27 — opt-in arming works: explicit positive env arms + behaves per AC1/AC2 ──
# (D6, round 5 — pins that the ONLY thing the frozen default changes is
# whether the timer is armed, not whether the mechanism functions once an
# operator explicitly opts in.)

def test_ac27_explicit_positive_env_arms_watchdog_and_retries(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="OK_AFTER_HANG_ARMED", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-ac27", is_error=False,
        ),
    })
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "0.05")
    monkeypatch.setenv("HAL_AGENT_SDK_MAX_ATTEMPTS", "3")
    mod = _import_agent_sdk_backend()

    idle_fn = getattr(mod, "_sdk_idle_timeout_sec", None)
    assert callable(idle_fn), "AC27: _sdk_idle_timeout_sec must exist as a module-level callable"
    assert idle_fn() == 0.05, (
        f"AC27: with the env explicitly set to 0.05, the helper must read it back "
        f"as 0.05 (not the disabled default); got {idle_fn()!r}"
    )

    gap_fn = getattr(mod, "_effective_idle_gap", None)
    assert callable(gap_fn), "AC27: _effective_idle_gap must exist as a named module-level helper"
    effective = gap_fn(0.05, mod.time.monotonic(), 30.0)
    assert effective >= 0.05, (
        f"AC27: the arming precondition (remaining >= idle + epsilon) must hold for "
        f"idle=0.05 / timeout_sec=30 -- got effective={effective!r}"
    )

    # Round-7 (D17) -- the same pass-through RECORDER AC28 already installs,
    # here in the TWO-attempt armed scenario where a per-attempt `t0`
    # re-anchoring mutation is actually observable (AC28's single-attempt
    # run cannot discriminate it -- demoted to a same-scenario invariant).
    real_gap = mod._effective_idle_gap
    seen = []

    def _recording_gap(idle_timeout, t0, outer_timeout_sec):
        seen.append((idle_timeout, t0))
        return real_gap(idle_timeout, t0, outer_timeout_sec)

    monkeypatch.setattr(mod, "_effective_idle_gap", _recording_gap)

    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac27")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=30, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.status == "ok", (
        f"AC27: an explicitly-armed watchdog must behave per AC1/AC2 -- hang then "
        f"success, recovered via retry; got status={result.status!r} "
        f"error_code={result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 2, (
        f"AC27: expected exactly 2 query() invocations (hang attempt + success retry); "
        f"got {len(fake.recorded_calls)}"
    )
    rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    matching = [r for r in rows if r.get("payload", {}).get("reason") == "idle_gap_timeout"]
    assert len(matching) == 1, (
        f"AC27: expected exactly ONE agent_sdk_retry event with reason=='idle_gap_timeout'; "
        f"got rows={rows!r}"
    )
    assert matching[0]["payload"].get("attempt") == 1, (
        f"AC27: expected attempt==1; got {matching[0]['payload']!r}"
    )

    # Round-7 (D17) INPUT-fence assertions on the two-attempt recorder.
    assert seen, (
        "AC27: _effective_idle_gap must be called at least once on the armed "
        "arming path -- an open-coded 'remaining > idle' that bypasses the "
        "named helper entirely would leave this recorder empty"
    )
    assert all(idle_timeout == 0.05 for idle_timeout, _t0 in seen), (
        f"AC27: EVERY idle_timeout argument fed to _effective_idle_gap under the "
        f"explicitly-armed value must equal 0.05 exactly (single-sourced from "
        f"_sdk_idle_timeout_sec() -- no substitution, no per-attempt drift); got {seen!r}"
    )
    t0_values = {t0 for _idle, t0 in seen}
    assert len(t0_values) == 1, (
        f"AC27: the recorded t0 must collapse to exactly ONE distinct value across "
        f"BOTH attempts -- t0 is run-anchored at agent_sdk.py:283 and must never be "
        f"re-anchored per attempt; got distinct t0 values {t0_values!r}"
    )


# ─── AC28 — default-off shield: env UNSET behaves exactly as legacy prod ──────
# (D6, round 5 — the direct fence on gate round 4's blocking F1 finding. Makes
# "inert by default" a TESTED property, not a claim: no timer, ONE backend
# call, NO agent_sdk_retry event, hang_attempts==0 by SUBSCRIPT, and -- routed
# through invoke_llm_subprocess -- NO runner_backend_fallback either.)

def test_ac28_default_off_env_unset_behaves_as_legacy_prod(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    monkeypatch.delenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", raising=False)
    mod = _import_agent_sdk_backend()

    idle_fn = getattr(mod, "_sdk_idle_timeout_sec", None)
    assert callable(idle_fn), "AC28: _sdk_idle_timeout_sec must exist as a module-level callable"
    assert idle_fn() == 0.0, (
        f"AC28: with the env unset, the module default must be 0.0 (disabled); "
        f"got {idle_fn()!r}"
    )

    # D9 (round 6, R5-MAJOR-1) -- the INPUT fence: wrap the REAL helper in a
    # pass-through RECORDER before invoking the backend. This is a seam
    # wrapper on a collaborator (never a stub of the UUT -- the real helper
    # still executes and its return value is passed through unchanged), so
    # §1l / stub-passability-lint are unaffected.
    real_gap = mod._effective_idle_gap
    seen = []

    def _recording_gap(idle_timeout, t0, outer_timeout_sec):
        seen.append((idle_timeout, t0))
        return real_gap(idle_timeout, t0, outer_timeout_sec)

    monkeypatch.setattr(mod, "_effective_idle_gap", _recording_gap)

    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac28")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=0.5, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC28: with the watchdog default-off, a genuinely silent stream must time out "
        f"via the OUTER budget exactly as legacy prod; got {result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC28: default-off means exactly 1 query() call (no watchdog-driven restart); "
        f"got {len(fake.recorded_calls)}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], (
        f"AC28: NO agent_sdk_retry event of ANY reason expected with the watchdog off; "
        f"got {retry_rows!r}"
    )
    assert result.data["hang_attempts"] == 0, (
        f"AC28: outer-expiry timeout_data must carry hang_attempts==0 via SUBSCRIPT "
        f"(key must exist unconditionally); got {result.data!r}"
    )

    # D9 INPUT-fence assertions (round 6): these hold on the ARGUMENTS fed to
    # the arming decision, INDEPENDENTLY of the outer budget size -- unlike
    # round 5's outcome-only form, no downstream coalesce/`or DEFAULT` can
    # satisfy them regardless of what timeout_sec the run happens to use.
    assert seen, (
        "AC28: _effective_idle_gap must be called at least once on the production arming "
        "path with the env unset -- an open-coded 'remaining > idle' that bypasses the "
        "named helper entirely would leave this recorder empty"
    )
    assert all(idle_timeout <= 0 for idle_timeout, _t0 in seen), (
        f"AC28: EVERY idle_timeout argument fed to _effective_idle_gap under the frozen "
        f"default must be <=0 (each exactly 0.0) -- a downstream coalesce such as "
        f"'idle = _sdk_idle_timeout_sec() or 900.0' or 'if idle <= 0: idle = 900.0' would "
        f"record a POSITIVE value here regardless of the outer budget's size; got {seen!r}"
    )
    t0_values = {t0 for _idle, t0 in seen}
    assert len(t0_values) == 1, (
        f"AC28: the recorded t0 must be CONSTANT across all recorded calls -- t0 is "
        f"run-anchored at agent_sdk.py:283; a GREEN re-anchoring t0 per attempt would "
        f"restore the round-3 clamp-and-arm harm through the retry path; got distinct "
        f"t0 values {t0_values!r}"
    )

    # Part B leg: routed through invoke_llm_subprocess, the default-off
    # timeout (hang_attempts==0) must NOT trigger the one-shot fallback.
    subprocess_calls = []

    def _fake_agent_sdk_partb(**kwargs):
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=result.data["hang_attempts"])

    def _fake_subprocess_partb(**kwargs):
        subprocess_calls.append(kwargs)
        return _fake_success_result(kwargs.get("step_name"), marker="SHOULD_NOT_BE_CALLED")

    llm_subprocess.register_backend(
        "agent-sdk", _fake_agent_sdk_partb, manifest_source="git_diff", overwrite=True
    )
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess_partb, manifest_source="harness_tool_record", overwrite=True
    )

    log2 = EventLog(tmp_path / "events-ac28-partb.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log2, run_id="run-ac28-partb", step_name="gate.step1", phase=None, tier=None,
    )

    result_b = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result_b.error_code == "E_LLM_API_TIMEOUT", (
        f"AC28 (Part B): default-off hang_attempts==0 must return the original timeout "
        f"result unchanged; got {result_b!r}"
    )
    assert subprocess_calls == [], (
        f"AC28 (Part B): the fallback must NOT be attempted when hang_attempts==0 "
        f"(default-off); subprocess fake was called with {subprocess_calls!r}"
    )
    fallback_rows = [r for r in log2.read_all() if r.get("event_type") == "runner_backend_fallback"]
    assert fallback_rows == [], (
        f"AC28 (Part B): no runner_backend_fallback event expected; got {fallback_rows!r}"
    )


# ─── AC29 — empty/whitespace env behaves exactly as unset (D12 / edge 2) ──────
# (round 6 — `export HAL_AGENT_SDK_IDLE_TIMEOUT_SEC=` is a routine shell idiom
# for "turn this off". A naive `float(os.environ.get(..., "0"))` would raise
# ValueError INSIDE _run()'s attempt loop on this DEFAULT-OFF path, landing
# in the L354 `except Exception`, classifying non-retryable, and failing the
# WHOLE invoke. §2.1.1's frozen body must treat empty/all-whitespace exactly
# as unset.)

def test_ac29_empty_or_whitespace_env_behaves_as_unset(monkeypatch, tmp_path):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()

    idle_fn = getattr(mod, "_sdk_idle_timeout_sec", None)
    assert callable(idle_fn), "AC29: _sdk_idle_timeout_sec must exist as a module-level callable"

    # Leg (i): the pure helper call must not raise and must return 0.0 for
    # both an empty export and an all-whitespace value.
    for env_val in ("", "   "):
        monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", env_val)
        got = idle_fn()
        assert got == 0.0, (
            f"AC29: HAL_AGENT_SDK_IDLE_TIMEOUT_SEC={env_val!r} must behave exactly as "
            f"unset (0.0, watchdog disabled), never raise and never return a nonzero "
            f"value; got {got!r}"
        )

    # Leg (ii): end-to-end, reusing AC28's outer-expiry idiom -- an empty
    # export must NOT crash the whole invoke via a stray ValueError out of
    # float("") landing in the L354 `except Exception` classifier.
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    monkeypatch.setenv("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC", "")
    mod2 = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac29")

    result = mod2.agent_sdk_backend(
        prompt="p", timeout_sec=0.5, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC29: an empty-string env must behave exactly as unset -- a genuinely silent "
        f"stream must time out via the OUTER budget, NOT raise ValueError inside the "
        f"attempt loop and get misclassified non-retryable; got "
        f"error_code={result.error_code!r} status={result.status!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC29: exactly 1 query() call expected (inert watchdog, no restart); "
        f"got {len(fake.recorded_calls)}"
    )
    retry_rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert retry_rows == [], (
        f"AC29: NO agent_sdk_retry event expected with an empty-string env; got {retry_rows!r}"
    )
    assert result.data["hang_attempts"] == 0, (
        f"AC29: outer-expiry timeout_data must carry hang_attempts==0 via SUBSCRIPT "
        f"(key must exist unconditionally); got {result.data!r}"
    )


# ===========================================================================
# Part B — llm_subprocess.py one-shot backend fallback
# (AC9-AC16, AC21-AC23)
# ===========================================================================

def _fake_timeout_result(step_name, hang_attempts, manifest_source="git_diff", retry_attempts=1):
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415
    return StepResult(
        status="error",
        error="hung",
        error_code="E_LLM_API_TIMEOUT",
        recoverable=True,
        step_name=step_name,
        duration_ms=0,
        data={
            "worker_written_paths": [],
            "manifest_source": manifest_source,
            "timed_out": True,
            "stderr_tail": "",
            "retry_attempts": retry_attempts,
            "hang_attempts": hang_attempts,
        },
    )


def _fake_success_result(step_name, marker="OK"):
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415
    return StepResult(
        status="ok",
        data={"raw_response": marker, "response_bytes": 1, "command": []},
        duration_ms=0,
        step_name=step_name,
    )


# ─── AC9 — hang timeout (hang_attempts>=1) triggers one-shot fallback ─────────

def test_ac9_hang_timeout_triggers_one_shot_fallback_to_subprocess(monkeypatch, tmp_path):
    agent_sdk_calls = []
    subprocess_calls = []

    def _fake_agent_sdk(**kwargs):
        agent_sdk_calls.append(kwargs)
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=1)

    def _fake_subprocess(**kwargs):
        subprocess_calls.append(kwargs)
        return _fake_success_result(kwargs.get("step_name"), marker="FALLBACK_OK")

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result.status == "ok" and result.data.get("raw_response") == "FALLBACK_OK", (
        f"AC9: fallback must return the claude-subprocess SUCCESS result; got {result!r}"
    )
    assert len(agent_sdk_calls) == 1, f"AC9: agent-sdk fake must be called exactly once; got {len(agent_sdk_calls)}"
    assert len(subprocess_calls) == 1, (
        f"AC9: subprocess fake must be called exactly once (one-shot fallback); "
        f"got {len(subprocess_calls)}"
    )


# ─── AC10 — fallback emits runner_backend_fallback with the exact payload ─────

def test_ac10_fallback_emits_runner_backend_fallback_event(monkeypatch, tmp_path):
    log = EventLog(tmp_path / "events-ac10.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="run-ac10", step_name="gate.step1", phase=None, tier=None,
    )

    def _fake_agent_sdk(**kwargs):
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=1)

    def _fake_subprocess(**kwargs):
        return _fake_success_result(kwargs.get("step_name"))

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet-model", timeout_sec=10, step_name="gate.step1",
    )

    rows = [r for r in log.read_all() if r.get("event_type") == "runner_backend_fallback"]
    assert rows, (
        f"AC10: expected a 'runner_backend_fallback' event; "
        f"got types={[r.get('event_type') for r in log.read_all()]!r}"
    )
    payload = rows[0].get("payload", {})
    assert payload.get("from") == "agent-sdk", f"AC10: unexpected payload {payload!r}"
    assert payload.get("to") == "claude-subprocess", f"AC10: unexpected payload {payload!r}"
    assert payload.get("reason") == "hang_timeout", f"AC10: unexpected payload {payload!r}"
    assert payload.get("hang_attempts") == 1, f"AC10: unexpected payload {payload!r}"
    assert payload.get("step_name") == "gate.step1", f"AC10: unexpected payload {payload!r}"
    assert payload.get("model") == "sonnet-model", f"AC10: unexpected payload {payload!r}"


# ─── AC11 — slow-but-alive (hang_attempts==0) does NOT trigger fallback ───────

def test_ac11_slow_but_alive_timeout_does_not_trigger_fallback(monkeypatch, tmp_path):
    assert callable(getattr(llm_subprocess, "_dispatch_backend", None)), (
        "AC11: _dispatch_backend must exist as a module-level callable (§2.2.1 extraction)"
    )

    subprocess_calls = []

    def _fake_agent_sdk(**kwargs):
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=0, retry_attempts=3)

    def _fake_subprocess(**kwargs):
        subprocess_calls.append(kwargs)
        return _fake_success_result(kwargs.get("step_name"), marker="SHOULD_NOT_BE_CALLED")

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC11: hang_attempts==0 (slow-but-alive) must return the original timeout "
        f"result unchanged; got {result!r}"
    )
    assert subprocess_calls == [], (
        f"AC11: fallback must NOT be attempted when hang_attempts==0; "
        f"subprocess fake was called with {subprocess_calls!r}"
    )


# ─── AC12 (amended, F2) — env kill-switch disables fallback AND the event ─────

def test_ac12_env_kill_switch_disables_fallback_and_event(monkeypatch, tmp_path):
    # AC11-style pairing assert (F2): must be present for this test to be a
    # genuine RED — without it, the assertions below vacuously pass today.
    fn = getattr(llm_subprocess, "_dispatch_backend", None)
    assert callable(fn), (
        "AC12: _dispatch_backend must exist as a module-level callable (§2.2.1 extraction)"
    )

    monkeypatch.setenv("HAL_AGENT_SDK_HANG_FALLBACK", "0")

    log = EventLog(tmp_path / "events-ac12.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="run-ac12", step_name="gate.step1", phase=None, tier=None,
    )

    subprocess_calls = []

    def _fake_agent_sdk(**kwargs):
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=1)

    def _fake_subprocess(**kwargs):
        subprocess_calls.append(kwargs)
        return _fake_success_result(kwargs.get("step_name"), marker="SHOULD_NOT_BE_CALLED")

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result.error_code == "E_LLM_API_TIMEOUT"
    assert subprocess_calls == [], (
        f"AC12: HAL_AGENT_SDK_HANG_FALLBACK=0 must suppress the fallback; "
        f"got calls={subprocess_calls!r}"
    )
    # The previously-missing half (F2): no runner_backend_fallback event either.
    fallback_rows = [r for r in log.read_all() if r.get("event_type") == "runner_backend_fallback"]
    assert fallback_rows == [], (
        f"AC12: the kill-switch must ALSO suppress the runner_backend_fallback event; "
        f"got {fallback_rows!r}"
    )


# ─── AC13 — fallback's own error returned as-is; one-shot, no recursion ───────

def test_ac13_fallback_error_returned_as_is_one_shot(monkeypatch, tmp_path):
    agent_sdk_calls = []
    subprocess_calls = []

    def _fake_agent_sdk(**kwargs):
        agent_sdk_calls.append(kwargs)
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=1)

    def _fake_subprocess(**kwargs):
        from bytedigger_engine.contracts import StepResult  # noqa: PLC0415
        subprocess_calls.append(kwargs)
        return StepResult(
            status="error", error="subprocess also failed", error_code="E_LLM_EXIT",
            recoverable=True, step_name=kwargs.get("step_name"), duration_ms=0, data=None,
        )

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result.status == "error" and result.error_code == "E_LLM_EXIT", (
        f"AC13: the fallback's OWN error must be returned as-is (no 3rd attempt); got {result!r}"
    )
    assert len(agent_sdk_calls) == 1, f"AC13: agent-sdk fake called {len(agent_sdk_calls)} times, expected 1"
    assert len(subprocess_calls) == 1, (
        f"AC13: subprocess fake called {len(subprocess_calls)} times, expected exactly 1 (one-shot)"
    )


# ─── AC14 — fail-safe legs: missing backend / manifest-gate rejection ─────────

def test_ac14_fallback_fails_safe_when_claude_subprocess_unavailable(monkeypatch, tmp_path):
    assert callable(getattr(llm_subprocess, "_dispatch_backend", None)), (
        "AC14: _dispatch_backend must exist as a module-level callable (§2.2.1 extraction)"
    )

    def _fake_agent_sdk(**kwargs):
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=1)

    # AC14a: "claude-subprocess" absent from _BACKENDS -> original result kept, no crash.
    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    monkeypatch.delitem(llm_subprocess._BACKENDS, "claude-subprocess", raising=False)

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )
    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC14a: fallback must fail-safe (no crash) when claude-subprocess is absent "
        f"from _BACKENDS; got {result!r}"
    )

    llm_subprocess.reset_backends()

    # AC14b: manifest gate rejects claude-subprocess -> skip fallback, keep original result.
    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)

    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415
    gate_err = StepResult(
        status="error", data=None, duration_ms=0, step_name="invoke_llm_subprocess",
        error="no manifest support", error_code="E_LLM_BACKEND_NO_MANIFEST", recoverable=False,
    )
    real_gate = llm_subprocess._assert_backend_supports_manifest

    def _fake_gate(backend):
        if backend == "claude-subprocess":
            return gate_err
        return real_gate(backend)

    monkeypatch.setattr(llm_subprocess, "_assert_backend_supports_manifest", _fake_gate)

    result2 = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )
    assert result2.error_code == "E_LLM_API_TIMEOUT", (
        f"AC14b: fallback manifest-gate rejection must skip fallback, keep original "
        f"timeout result; got {result2!r}"
    )


# ─── AC15 — non-agent-sdk backends structurally unreachable by the fallback ───

def test_ac15_non_agent_sdk_backend_unaffected_by_fallback_predicate(monkeypatch, tmp_path):
    assert callable(getattr(llm_subprocess, "_dispatch_backend", None)), (
        "AC15: _dispatch_backend must exist as a module-level callable (§2.2.1 extraction)"
    )

    calls = []

    def _fake_subprocess(**kwargs):
        calls.append(kwargs)
        return _fake_timeout_result(
            kwargs.get("step_name"), hang_attempts=1, manifest_source="harness_tool_record",
        )

    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="claude-subprocess", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result.error_code == "E_LLM_API_TIMEOUT"
    assert result.data is not None and result.data.get("hang_attempts") == 1
    assert len(calls) == 1, (
        f"AC15: resolved_backend != 'agent-sdk' must never trigger the fallback dispatch "
        f"loop (fake called {len(calls)} times, expected exactly 1)"
    )


# ─── AC16 — _dispatch_backend preserves GH334 two-branch stable_prefix threading ──

def test_ac16_dispatch_backend_preserves_stable_prefix_two_branch_threading(monkeypatch, tmp_path):
    fn = getattr(llm_subprocess, "_dispatch_backend", None)
    assert callable(fn), "AC16: _dispatch_backend must exist as a module-level callable (§2.2.1 extraction)"

    captured_empty = {}
    captured_nonempty = {}

    def _strict_fake_empty(*, prompt, model, timeout_sec, step_name, extra_data, allowed_tools,
                            run_ctx, hard_gate, gate_label, straggler_cfg, idle_timeout_sec):
        captured_empty["called"] = True
        return _fake_success_result(step_name)

    def _fake_with_prefix(*, prompt, model, timeout_sec, step_name, extra_data, allowed_tools,
                           run_ctx, hard_gate, gate_label, straggler_cfg, idle_timeout_sec,
                           stable_prefix=""):
        captured_nonempty["stable_prefix"] = stable_prefix
        return _fake_success_result(step_name)

    llm_subprocess.register_backend("ac16-empty", _strict_fake_empty, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend("ac16-nonempty", _fake_with_prefix, manifest_source="git_diff", overwrite=True)

    common = dict(
        prompt="p", model="sonnet", timeout_sec=10, step_name="s",
        extra_data=None, allowed_tools=None, run_ctx=None, hard_gate=False,
        gate_label=None, straggler_cfg=None, idle_timeout_sec=None,
    )

    # Empty branch: strict-signature backend WITHOUT a stable_prefix param must
    # stay call-compatible (GH334 back-compat) — a leaked kwarg raises TypeError.
    fn("ac16-empty", **common, stable_prefix="")
    assert captured_empty.get("called") is True, "AC16: empty-prefix branch did not reach the backend"

    # Non-empty branch: backend receives stable_prefix="X" explicitly.
    fn("ac16-nonempty", **common, stable_prefix="X")
    assert captured_nonempty.get("stable_prefix") == "X", (
        f"AC16: non-empty stable_prefix must be threaded through explicitly; got {captured_nonempty!r}"
    )


# ─── AC21 — one-shot fence: predicate-matching fallback result returned as-is ─

def test_ac21_fallback_target_matching_predicate_returned_as_is_one_shot(monkeypatch, tmp_path):
    log = EventLog(tmp_path / "events-ac21.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="run-ac21", step_name="gate.step1", phase=None, tier=None,
    )

    agent_sdk_calls = []
    subprocess_calls = []

    def _fake_agent_sdk(**kwargs):
        agent_sdk_calls.append(kwargs)
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=1)

    def _fake_subprocess(**kwargs):
        subprocess_calls.append(kwargs)
        return _fake_timeout_result(
            kwargs.get("step_name"), hang_attempts=1, manifest_source="harness_tool_record",
        )

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result.error_code == "E_LLM_API_TIMEOUT" and result.data.get("hang_attempts") == 1, (
        f"AC21: a fallback-target result that ITSELF matches the fallback predicate must "
        f"be returned as-is (no recursive 2nd fallback); got {result!r}"
    )
    assert len(agent_sdk_calls) == 1, f"AC21: agent-sdk fake called {len(agent_sdk_calls)} times, expected 1"
    assert len(subprocess_calls) == 1, (
        f"AC21: subprocess fake called {len(subprocess_calls)} times, expected exactly 1 (no recursion)"
    )
    fallback_rows = [r for r in log.read_all() if r.get("event_type") == "runner_backend_fallback"]
    assert len(fallback_rows) == 1, (
        f"AC21: exactly ONE runner_backend_fallback event expected (no recursive 2nd "
        f"fallback attempt); got {fallback_rows!r}"
    )


# ─── AC22 — success-path decoy: ordinary success never triggers fallback ──────

def test_ac22_success_path_never_triggers_fallback(monkeypatch, tmp_path):
    assert callable(getattr(llm_subprocess, "_dispatch_backend", None)), (
        "AC22: _dispatch_backend must exist as a module-level callable (§2.2.1 extraction)"
    )

    log = EventLog(tmp_path / "events-ac22.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="run-ac22", step_name="gate.step1", phase=None, tier=None,
    )

    agent_sdk_calls = []
    subprocess_calls = []

    def _fake_agent_sdk(**kwargs):
        agent_sdk_calls.append(kwargs)
        return _fake_success_result(kwargs.get("step_name"), marker="SDK_OK")

    def _fake_subprocess(**kwargs):
        subprocess_calls.append(kwargs)
        return _fake_success_result(kwargs.get("step_name"), marker="SHOULD_NOT_BE_CALLED")

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
    )

    assert result.status == "ok" and result.data.get("raw_response") == "SDK_OK", (
        f"AC22: an ordinary successful agent-sdk dispatch must be returned UNCHANGED; got {result!r}"
    )
    assert len(agent_sdk_calls) == 1, f"AC22: agent-sdk fake called {len(agent_sdk_calls)} times, expected 1"
    assert subprocess_calls == [], (
        f"AC22: the fallback (guards against `or` instead of `and` in the predicate) must "
        f"NOT be attempted on a success result; subprocess fake was called with {subprocess_calls!r}"
    )
    fallback_rows = [r for r in log.read_all() if r.get("event_type") == "runner_backend_fallback"]
    assert fallback_rows == [], f"AC22: no runner_backend_fallback event expected; got {fallback_rows!r}"


# ─── AC23 — non-empty stable_prefix survives the fallback dispatch ────────────

def test_ac23_stable_prefix_survives_fallback_dispatch(monkeypatch, tmp_path):
    agent_sdk_calls = []
    subprocess_calls = []

    def _fake_agent_sdk(**kwargs):
        agent_sdk_calls.append(kwargs)
        return _fake_timeout_result(kwargs.get("step_name"), hang_attempts=1)

    def _fake_subprocess(**kwargs):
        subprocess_calls.append(kwargs)
        return _fake_success_result(kwargs.get("step_name"), marker="FALLBACK_OK")

    llm_subprocess.register_backend("agent-sdk", _fake_agent_sdk, manifest_source="git_diff", overwrite=True)
    llm_subprocess.register_backend(
        "claude-subprocess", _fake_subprocess, manifest_source="harness_tool_record", overwrite=True
    )

    result = llm_subprocess.invoke_llm_subprocess(
        backend="agent-sdk", prompt="p", model="sonnet", timeout_sec=10, step_name="gate.step1",
        stable_prefix="PFX",
    )

    assert result.status == "ok" and result.data.get("raw_response") == "FALLBACK_OK", (
        f"AC23: fallback with a non-empty stable_prefix must still succeed; got {result!r}"
    )
    assert len(agent_sdk_calls) == 1 and len(subprocess_calls) == 1, (
        f"AC23: expected exactly one call each; got agent_sdk={len(agent_sdk_calls)} "
        f"subprocess={len(subprocess_calls)}"
    )
    assert agent_sdk_calls[0].get("stable_prefix") == "PFX", (
        f"AC23: the first (agent-sdk) dispatch must receive stable_prefix=='PFX'; "
        f"got {agent_sdk_calls[0]!r}"
    )
    assert subprocess_calls[0].get("stable_prefix") == "PFX", (
        f"AC23: the fallback dispatch must receive the SAME stable_prefix=='PFX' as the "
        f"first dispatch (§2.2.3 'SAME kwargs'); got {subprocess_calls[0]!r}"
    )
