"""RED tests for bd#10 — BD-L3, attested authorship and inputs (R3.1-R3.6).

Frozen spec: engine_py/conformance/AUTHORSHIP_SPEC.md (FROZEN v1, base dc6f0d0).
Discipline inherited verbatim: EMISSIONS_SPEC.md §0.1-§0.6, CONTRACTS_SPEC.md
§0.1-§0.8, plus AUTHORSHIP_SPEC.md §0.1 (two measured outcomes on one fixture),
§0.2 (external provenance for every pinned constant) and §0.4 (the
`effective_prompt` naming collision).

COLLECTION SAFETY (§1q / D1CF5FDF).  `conformance.attest`, `conformance.bd_l3`
and `invoke_llm_subprocess(injections=...)` do NOT exist on this base.  Every
reference to them is DEFERRED into a test body (`from conformance import attest`
inside the function, or a keyword argument that raises TypeError at call time),
so this module COLLECTS cleanly pre-GREEN and FAILS at assert/call time.  There
is no module-level `sys.path` mutation and no `from conftest import` — the
engine_py conftest injects the roots at conftest-import time (tests/conftest.py
:36-49), which is the sanctioned seam (81F97F3D).

EXTERNAL PROVENANCE (§0.2).  Every pinned constant below cites a source OUTSIDE
the artifact under test.  In particular the three new error-code strings are
pinned as literals citing CL:98/99/102 @ HAL `fd35e1304`; they are deliberately
NOT imported from `error_codes.ERROR_CODES`, because this lot writes those
entries and pinning against them would be §0.1 subtype (3).

DIGESTS (§0.1 subtype 3).  Every expected digest is recomputed in the test from
the text the test itself supplied, with stdlib `hashlib` — never read back out
of the event under test, and never through `attest.hash_text` (the artifact).

SEAM PINS (§0.2 of CONTRACTS_SPEC).
  * Adversarial adapters are installed through the public
    `llm_subprocess.register_backend(...)` seam (llm_subprocess.py:2026), which
    rebinds `_BACKENDS`/`_KNOWN_BACKENDS`/`_BACKEND_MANIFEST_SOURCE`/
    `_BACKEND_CAPABILITIES` at CALL time, and `_dispatch_backend`
    (llm_subprocess.py:971) reads `_BACKENDS` at call time (:1000, :1014) — so
    the interception is visible without any import-time binding assumption.
    Teardown calls `llm_subprocess.reset_backends()` (:2062) so a registration
    can never leak into a later test.
  * The tier-model config seam is `config_provider.models_config_path`,
    resolved at call time inside `_load_tier_model` (llm_subprocess.py:280) —
    the established seam (tests/test_32ED59E2_effort_lever.py:48).
  * `emit_resolver_resolved` is neutralised per test to keep the resolver's
    disk writes out of SHARED/state; it is NOT a UUT here.
  * [G22:2]: no test sabotages a primitive its own harness runs on.  The only
    raiser is `_TargetedRaisingEventLog`, which raises for exactly ONE event
    type and records-and-delegates everything else (AC-P6, [G18:EDGE-5]).

PRE-PASSING, DECLARED (§0.6).  Exactly two half-assertions pass today:
  * AC-I4 and AC-C5's `error_codes.main(["--check"]) == 0` half — the existing
    drift gate is already clean on this tree (tests/test_gh1067_ignored_dir_
    exclusion.py:120 is its enforcement).  It is a shield: it pins that this
    lot's three new codes do not break the gate.  The ERROR_CODES-membership
    half of both ACs FAILS today.
Every other assertion in this file fails pre-GREEN.

ALIGNED TO SPEC v3 (`b16915f`).  Every gap this RED refused to guess is now
closed by the spec, and each closure is asserted rather than worked around:
G1 by `[bd10:8]` (three constructible offender kinds), G2 by `[bd10:6]` (the
`observed_tools` channel — AC-C3's enforcement half is written), G3 by
`[bd10:5]` (`observed_model` in the key set), G4 by `[bd10:7]`
(`model_requested` is post-rebind), G5 by `[bd10:9]` (`validate_report` is the
judge), G6 by `[bd10:10]` (path spelling and content normalisation), G7 by
`[bd10:6]` (sorted, deduplicated), G8 and G9 by `[bd10:12]` + `[bd10:13]`
(`observed_tools` in the key set, and `verdict:`-prefixed log-level aggregates
in `labels`), G10 by `[bd10:14]` (adapter derives, checker consumes).  AC-P8 is
new per `[bd10:4]`; `[bd10:11]` closes both public export surfaces, which is
what makes AC-A3's exact-equality assertion safe in the other direction.

NO SPEC GAPS OPEN.  One editorial residue, recorded because a reader comparing
the RED to the spec will trip on it and it is NOT a gap: §5's AC-M1 bullet
still carries its v1 wording — "the payload's `observed_model` is ABSENT" and
"the report marks R3.3 not-checked FOR THAT INVOCATION".  Both are superseded
in the same document by `[bd10:5]` (the key is always present; its value is
`null`) and `[bd10:13]` (the verdict is a log-level aggregate, because a
whole-log report cannot express a per-invocation state).  The tests follow the
normative clauses: `observed_model is None`, and `labels["verdict:R3.3"]`.

AC map (test function → AC):
  AC-P1  test_ac_p1_exact_payload_key_set
  AC-P2  test_ac_p2_prompt_sha256_recomputed_by_the_test
  AC-P3  test_ac_p3_hash_covers_pre_hoist_text
  AC-P4  test_ac_p4_both_dispatch_branches_emit
  AC-P5  test_ac_p5_one_call_two_dispatches_two_events
  AC-P6  test_ac_p6_emission_failure_does_not_break_execution
  AC-P7  test_ac_p7_r31_labelled_host_attested
  AC-P8  test_ac_p8_no_run_context_emits_nothing_and_does_not_raise
  AC-I1  test_ac_i1_assemble_order_and_separator
  AC-I2  test_ac_i2_injections_in_declaration_order
         test_ac_i2_injections_empty_for_none_and_for_empty_sequence
  AC-I3  test_ac_i3_unattributed_block_blocks_dispatch (6 params, both orderings)
  AC-I4  test_ac_i4_inject_unattributed_registered_and_drift_gate_clean
  AC-I5  test_ac_i5_phase_2_explore_role_template_routes_through_injections
  AC-M1  test_ac_m1_absent_observed_model_is_a_third_state
  AC-M2  test_ac_m2_family_mismatch_errors_and_family_match_is_ok
  AC-M3  test_ac_m3_comparison_target_is_the_dispatched_tier_model
  AC-M4  test_ac_m4_family_comparison_not_raw_equality
  AC-M5  test_ac_m5_mismatch_event_still_emitted_and_step_errors
  AC-C1  test_ac_c1_declared_capabilities_verbatim_empty_and_null
  AC-C2  test_ac_c2_capability_enforcement_non_uniform_across_two_backends
  AC-C3  test_ac_c3_capability_escapes_both_orderings (pure function)
         test_ac_c3_escape_at_chokepoint_errors_both_orderings (2 params)
         test_ac_c3_absent_versus_empty_observed_tools (third state)
  AC-C4  test_ac_c4_none_declaration_versus_empty_declaration
  AC-C5  test_ac_c5_capability_and_pin_codes_registered_and_drift_gate_clean
  AC-C6  test_ac_c6_argument_level_escape_is_not_detected_in_v1
  AC-A1  test_ac_a1_report_shape_requirements_labels_and_coercion
  AC-A2  test_ac_a2_adv9_recorded_not_executed
  AC-A3  test_ac_a3_no_export_grants_a_level
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import config_provider
import error_codes
import llm_subprocess
import telemetry_ctx
from contracts import StepResult, WorkflowContext
from engine import WorkflowEngine
from lib.llm_provider import CLAUDE_PROVIDER


# ---------------------------------------------------------------------------
# Pinned constants — every one cites a source OUTSIDE the artifact under test
# (AUTHORSHIP_SPEC.md §0.2).
# ---------------------------------------------------------------------------

# AUTHORSHIP_SPEC.md §2.2 (`EVENT_TYPE`) and §3 AC-P1.
EVENT_TYPE = "model_invocation_attested"

# AUTHORSHIP_SPEC.md §3 AC-P1 (v3) — the payload key set, EXACTLY.
# NINE keys: `observed_model` joined in v2 (`[bd10:5]`) and `observed_tools` in
# v3 (`[bd10:12]`), so the log records not just what each adapter REPORTED but
# whether it could observe at all.
ATTEST_KEYS = frozenset({
    "step_name",
    "backend",
    "model_requested",
    "prompt_sha256",
    "injections",
    "declared_capabilities",
    "capability_enforcement",
    "observed_model",
    "observed_tools",
})

# AUTHORSHIP_SPEC.md §3 `[bd10:13]` — per-requirement verdicts are log-level
# AGGREGATES in `labels`, under a prefix that cannot collide with the qualifier
# labels `"R3.1": "host-attested"` / `"R3.6": "tool-head-only"`.
VERDICT_R33 = "verdict:R3.3"
VERDICT_R36 = "verdict:R3.6"

# CL:98 / CL:99 / CL:102 @ HAL fd35e1304 (R3.2 / R3.3 / R3.6).  NOT imported
# from error_codes.ERROR_CODES: this lot authors those entries (§0.2).
E_INJECT_UNATTRIBUTED = "E_INJECT_UNATTRIBUTED"
E_MODEL_PIN_MISMATCH = "E_MODEL_PIN_MISMATCH"
E_CAPABILITY_ESCAPE = "E_CAPABILITY_ESCAPE"

# CL:97-102 @ fd35e1304.
REQUIREMENT_IDS = ("R3.1", "R3.2", "R3.3", "R3.4", "R3.5", "R3.6")

# conformance/tokens.py:7-10 on this base (shipped by bd#22, not by this lot, so
# these ARE an external source under §0.2 — unlike the three new error codes).
REQUIREMENT_PASSED = "passed"
REQUIREMENT_NOT_CHECKED = "not-checked"
ADVERSARY_NOT_EXECUTED = "not_executed"

# workflows/phase_2_explore.py:372 on this base — a real declared capability
# set, including the tool-head-with-operand form (§0.2).
REAL_DECLARED_TOOLS = [
    "Read", "Grep", "Glob", "WebSearch", "WebFetch", "Write",
    "Bash(graphify-shim.sh:*)",
]

# AUTHORSHIP_SPEC.md §6 AC-C2 — the closed set of enforcement values.
ENFORCEMENT_VALUES = {"runtime-allowlist", "not-enforced"}

# AUTHORSHIP_SPEC.md §3 AC-P1 / EMISSIONS_SPEC.md §0.5 — the sentinel that MUST
# fail a digest assertion rather than satisfy a non-emptiness check.
ZERO_DIGEST = "sha256:" + "0" * 64

# lib/llm_provider.py:113 on this base — the external family ladder AC-M4 is
# asserted against (NOT a constant this lot writes).
EXTERNAL_FAMILIES = {"haiku", "sonnet", "opus", "fable"}


def sha256_of(text: str) -> str:
    """Recompute the spec's digest form from text the TEST supplied.

    Deliberately stdlib-only: `attest.hash_text` is part of the artifact under
    test, so using it here would be §0.1 subtype (3) — grading the emitter
    against itself.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _FakeEventLog:
    """Records `(event_type, payload, run_id)`; duck-types the EventSink
    contract engine.py:217 / llm_subprocess._emit_safe (:2980) expect."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def payloads(self, event_type: str) -> list[dict]:
        return [p for (t, p, _) in self.events if t == event_type]

    def attests(self) -> list[dict]:
        return self.payloads(EVENT_TYPE)

    def types(self) -> list[str]:
        return [t for (t, _, _) in self.events]


class _TargetedRaisingEventLog(_FakeEventLog):
    """AC-P6 / [G18:EDGE-5]: raises for EXACTLY one event type, records the
    rest.  A blanket raiser proves nothing in situ, and per [G22:2] this seam
    never touches a primitive pytest itself runs on — it is a purpose-built
    log double handed to one call."""

    def __init__(self, boom_event_type: str) -> None:
        super().__init__()
        self._boom = boom_event_type
        self.attempts = 0

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        if event_type == self._boom:
            self.attempts += 1
            raise RuntimeError(f"event log refuses {event_type!r}")
        super().append(event_type, payload, run_id)


class _RecordingAdapter:
    """Records every dispatch and returns a caller-chosen StepResult.

    Accepts `**kwargs` so it satisfies the 12-parameter LLMBackend protocol in
    both `_dispatch_backend` branches (llm_subprocess.py:999 with
    `stable_prefix=`, :1014 without) without binding parameter names.

    Merges the caller's `extra_data` into `StepResult.data`, exactly as the
    real backend does (llm_subprocess.py:1920) and as the sibling doubles do
    (tests/test_phase_2_explore.py:74).  Without that fidelity a double
    silently breaks the CALLER's downstream contract — `phase_2_explore`
    threads `doc_path`/`complexity` through `extra_data` and
    `_write_explore_doc` (workflows/phase_2_explore.py:429,444) reads them
    back — which would make AC-I5 fail for a reason no GREEN removes.
    Test-supplied `data` wins on collision, so the fixtures below still
    control `observed_model`/`raw_response`.
    """

    def __init__(
        self,
        name: str = "rec",
        *,
        data: "dict | None" = None,
        status: str = "ok",
        error_code: "str | None" = None,
        recoverable: bool = True,
    ) -> None:
        self.name = name
        self.calls: list[dict] = []
        self._data = data
        self._status = status
        self._error_code = error_code
        self._recoverable = recoverable

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        data = {
            "raw_response": f"{self.name} response",
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kwargs.get("extra_data") or {})
        if self._data is not None:
            data.update(self._data)
        return StepResult(
            status=self._status,
            data=data,
            duration_ms=0,
            step_name=kwargs.get("step_name", self.name),
            error=None if self._status == "ok" else f"{self.name} error",
            error_code=self._error_code,
            recoverable=self._recoverable,
        )


def register(name: str, adapter, *, capabilities=("manifest",)) -> None:
    """Install `adapter` through the PUBLIC seam (llm_subprocess.py:2026)."""
    llm_subprocess.register_backend(
        name,
        adapter,
        manifest_source="harness_tool_record",
        capabilities=frozenset(capabilities),
        overwrite=True,
    )


def set_run(log, *, tier: "str | None" = None, step_name: str = "invoke_bd10_llm") -> None:
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-BD10", step_name=step_name,
        phase="phase_bd10", tier=tier,
    )


def invoke_kwargs(**overrides) -> dict:
    """Minimal kwargs that reach `_dispatch_backend`.

    `idle_timeout_sec=0` + `straggler_cfg=None` short-circuit
    `_assert_backend_supports_watchdog` (llm_subprocess.py:449), so the
    dispatch guard cannot be mistaken for the assertion under test
    (§0.1 subtype 2).
    """
    base = dict(
        prompt="BD10 PROMPT BODY",
        model="sonnet",
        timeout_sec=1,
        step_name="invoke_bd10_llm",
        idle_timeout_sec=0,
        straggler_cfg=None,
        backend="bd10-rec",
    )
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _bd10_isolation(monkeypatch):
    """§1i singleton-resource guard: the backend registry, the telemetry slot
    and the resolver's disk sink are all process-wide singletons.  Each is
    pre-staged to a known baseline BEFORE the test body and restored after —
    never raced against a sibling's leftovers."""
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


# ---------------------------------------------------------------------------
# §3 — R3.1, the effective prompt is hashed into the event log
# ---------------------------------------------------------------------------

def test_ac_p1_exact_payload_key_set() -> None:
    """AC-P1 (v3): one dispatch UNDER AN ACTIVE RUN CONTEXT emits exactly one
    `model_invocation_attested` event whose payload key set is EXACTLY the
    NINE pinned keys (`[bd10:5]` added `observed_model`, `[bd10:12]` added
    `observed_tools`).

    Kills: an emitter that omits any of the nine; an emitter that adds a tenth
    diagnostic key (which would let a consumer's key set drift silently); a
    GREEN carrying v1's seven-key or v2's eight-key payload.

    Two measured outcomes on ONE fixture: with the emitter intact the
    equality holds; with the GREEN mutated to append e.g.
    `payload["debug_prompt"] = prompt`, the SAME fixture flips the equality to
    False.  Exact-set equality (not `>=`) is what makes the second measurement
    possible at all.

    The head clause's run-context condition (`[bd10:4]`) is measured by AC-P8,
    not restated here; this fixture always pushes a context.

    Pre-GREEN: no such event exists — `len(attests) == 1` fails.
    """
    from conformance.attest import EVENT_TYPE as GREEN_EVENT_TYPE, InjectedBlock

    assert GREEN_EVENT_TYPE == EVENT_TYPE, (
        f"AC-P1: attest.EVENT_TYPE must be {EVENT_TYPE!r} per AUTHORSHIP_SPEC.md §2.2; "
        f"got {GREEN_EVENT_TYPE!r}"
    )

    # Both observation keys meaningfully populated rather than null: the model
    # is the same family as the dispatched request (pin comparison passes) and
    # the tool head is inside the declaration below (no escape).
    adapter = _RecordingAdapter("p1", data={
        "observed_model": "claude-sonnet-4-6",
        "observed_tools": ["Read"],
    })
    register("bd10-rec", adapter)
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(
            allowed_tools=list(REAL_DECLARED_TOOLS),
            injections=(InjectedBlock(source_id="src-p1", content="P1 BLOCK"),),
        )
    )

    assert result.status == "ok", (
        f"AC-P1 precondition: the fixture must reach dispatch; got status="
        f"{result.status!r} error_code={result.error_code!r}"
    )
    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-P1: exactly one {EVENT_TYPE!r} event per dispatch; got "
        f"{len(attests)} (all events: {log.types()!r})"
    )
    payload = attests[0]
    assert set(payload) == set(ATTEST_KEYS), (
        f"AC-P1: payload key set must be EXACTLY {sorted(ATTEST_KEYS)!r}; got "
        f"{sorted(payload)!r} (missing={sorted(ATTEST_KEYS - set(payload))!r}, "
        f"extra={sorted(set(payload) - ATTEST_KEYS)!r})"
    )
    # §0.5: pin the identity fields by VALUE, not by presence.  No tier is
    # active on this fixture, so `model_requested` is unambiguous here.
    assert payload["backend"] == "bd10-rec", (
        f"AC-P1: backend must name the dispatched backend; got {payload['backend']!r}"
    )
    assert payload["step_name"] == "invoke_bd10_llm", (
        f"AC-P1: step_name must be the invoked step; got {payload['step_name']!r}"
    )
    assert payload["model_requested"] == "sonnet", (
        f"AC-P1: model_requested must be the dispatched request model 'sonnet'; "
        f"got {payload['model_requested']!r}"
    )
    assert payload["observed_model"] == "claude-sonnet-4-6", (
        f"AC-P1 (`[bd10:5]`): observed_model must carry the adapter's reported "
        f"identity verbatim; got {payload['observed_model']!r}"
    )
    assert payload["observed_tools"] == ["Read"], (
        f"AC-P1 (`[bd10:12]`): observed_tools must carry the adapter's reported "
        f"sequence verbatim; got {payload['observed_tools']!r}"
    )


def test_ac_p2_prompt_sha256_recomputed_by_the_test() -> None:
    """AC-P2: `prompt_sha256` equals the digest of the assembled text,
    recomputed here from the string this test supplied.

    Kills: an emitter that hashes a truncated/normalised prompt; an emitter
    that writes a constant; an emitter that writes the zero sentinel.

    Two measured outcomes on ONE fixture: intact, the equality holds; with
    `attest.hash_text` mutated to return `"sha256:" + "0"*64`, the SAME
    fixture fails the same equality.  The expected value is NEVER read out of
    the event (§0.1 subtype 3) — it comes from `hashlib` over the literal
    below.

    Pre-GREEN: no event, so the lookup fails.
    """
    prompt = "AC-P2 unique prompt body — do not normalise me\n"
    expected = sha256_of(prompt)

    adapter = _RecordingAdapter("p2")
    register("bd10-rec", adapter)
    log = _FakeEventLog()
    set_run(log)

    llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(prompt=prompt, injections=None))

    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-P2: expected exactly one {EVENT_TYPE!r} event; got {len(attests)}"
    )
    got = attests[0]["prompt_sha256"]
    assert got == expected, (
        f"AC-P2: prompt_sha256 must equal the test-recomputed digest {expected!r}; "
        f"got {got!r}"
    )
    assert got != ZERO_DIGEST, (
        "AC-P2: the zero sentinel must never satisfy this field (EMISSIONS_SPEC §0.5)"
    )


def test_ac_p3_hash_covers_pre_hoist_text() -> None:
    """AC-P3 (§0.4): the digest covers the COMPLETE assembled text, prefix
    included — not `llm_subprocess.py:1391`'s `effective_prompt` local, which
    is the prompt with `stable_prefix` REMOVED.

    Kills: a GREEN that hashes the post-hoist body, which would satisfy every
    loosely-worded reading of R3.1 while omitting the system-prompt bytes.

    Two measured outcomes on ONE fixture (a single call whose prompt CONTAINS a
    non-empty `stable_prefix`): the digest equals hash(full) AND differs from
    hash(full minus the prefix).  The inequality is the killing half; without
    it the AC is §0.1 subtype 1.

    Pre-GREEN: no event, so both halves fail.
    """
    stable_prefix = "SYSTEM PREFIX BLOCK — stable across calls\n\n"
    full_prompt = stable_prefix + "AC-P3 variable body\n"
    post_hoist = full_prompt.replace(stable_prefix, "", 1)
    assert post_hoist != full_prompt, "fixture sanity: the prefix must really be present"

    adapter = _RecordingAdapter("p3")
    register("bd10-rec", adapter)
    log = _FakeEventLog()
    set_run(log)

    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(prompt=full_prompt, stable_prefix=stable_prefix, injections=None)
    )

    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-P3: expected exactly one {EVENT_TYPE!r} event; got {len(attests)}"
    )
    got = attests[0]["prompt_sha256"]
    assert got == sha256_of(full_prompt), (
        f"AC-P3: prompt_sha256 must hash the PRE-hoist text (prefix included); "
        f"expected {sha256_of(full_prompt)!r}, got {got!r}"
    )
    assert got != sha256_of(post_hoist), (
        "AC-P3: prompt_sha256 must NOT equal the digest of the post-hoist body "
        "(llm_subprocess.py:1391's `effective_prompt`) — that GREEN omits the "
        "system-prompt bytes"
    )


def test_ac_p4_both_dispatch_branches_emit() -> None:
    """AC-P4: quantified over the TWO branches of `_dispatch_backend` —
    `stable_prefix` truthy (llm_subprocess.py:999) and falsy (:1014).  Both
    emit.

    Kills: a GREEN that records inside only one branch, which a
    uniform-fixture RED cannot see.  Each branch is measured separately and
    each carries its own digest, so a one-branch GREEN fails exactly one
    assertion and a copy-the-other-branch GREEN fails the digest.

    Two measured outcomes on ONE fixture set: with both branches instrumented
    the two digests match their own prompts; with the emit deleted from the
    `stable_prefix`-truthy branch the same two calls yield one event.

    Pre-GREEN: zero events — both halves fail.
    """
    adapter = _RecordingAdapter("p4")
    register("bd10-rec", adapter)

    prefixed_prompt = "PFX\n\nAC-P4 with prefix"
    plain_prompt = "AC-P4 without prefix"

    log_prefix = _FakeEventLog()
    set_run(log_prefix)
    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(prompt=prefixed_prompt, stable_prefix="PFX\n\n", injections=None)
    )

    log_plain = _FakeEventLog()
    set_run(log_plain)
    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(prompt=plain_prompt, stable_prefix="", injections=None)
    )

    assert len(log_prefix.attests()) == 1, (
        f"AC-P4: the stable_prefix-TRUTHY branch (llm_subprocess.py:999) must emit "
        f"exactly one event; got {len(log_prefix.attests())}"
    )
    assert len(log_plain.attests()) == 1, (
        f"AC-P4: the stable_prefix-FALSY branch (llm_subprocess.py:1014) must emit "
        f"exactly one event; got {len(log_plain.attests())}"
    )
    assert log_prefix.attests()[0]["prompt_sha256"] == sha256_of(prefixed_prompt), (
        "AC-P4: the truthy-branch event must carry that branch's own prompt digest"
    )
    assert log_plain.attests()[0]["prompt_sha256"] == sha256_of(plain_prompt), (
        "AC-P4: the falsy-branch event must carry that branch's own prompt digest"
    )


def test_ac_p5_one_call_two_dispatches_two_events(monkeypatch) -> None:
    """AC-P5: "once per model invocation" means once per DISPATCH, not once
    per `invoke_llm_subprocess` call.  The GH1169 one-shot fallback
    (llm_subprocess.py:1299-1320) dispatches twice for one call.

    Kills: a once-per-call GREEN (emits 1) and a last-dispatch-only GREEN
    (emits 1, backend `claude-subprocess`).  The collection is NON-UNIFORM by
    construction — the two dispatches differ in `backend` — so `first`, `last`
    and `any` reductions all die: the assertion pins the ORDERED pair
    `["agent-sdk", "claude-subprocess"]`, not a membership test.

    Two measured outcomes on ONE fixture: the falling-back call emits two
    events in that order; the positive control (a call that does NOT fall
    back, same log-and-adapter shape) emits exactly one.

    Pre-GREEN: zero events on both halves.
    """
    # HAL_AGENT_SDK_HANG_FALLBACK == "0" would disable the fallback
    # (llm_subprocess.py:1055); the fixture must force the branch to RUN
    # (§0.1 subtype 2), so the kill-switch is cleared explicitly.
    monkeypatch.delenv("HAL_AGENT_SDK_HANG_FALLBACK", raising=False)

    hanging = _RecordingAdapter(
        "agent-sdk-hang",
        data={"hang_attempts": 1},
        status="error",
        error_code="E_LLM_API_TIMEOUT",
        recoverable=True,
    )
    fallback = _RecordingAdapter("subprocess-fallback")
    register("agent-sdk", hanging, capabilities=("manifest", "progress_since", "abort"))
    register("claude-subprocess", fallback,
             capabilities=("manifest", "progress_since", "abort"))

    log = _FakeEventLog()
    set_run(log)
    llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(backend="agent-sdk", injections=None))

    assert len(hanging.calls) == 1 and len(fallback.calls) == 1, (
        f"AC-P5 precondition: the fallback path must actually run — agent-sdk "
        f"calls={len(hanging.calls)}, claude-subprocess calls={len(fallback.calls)}"
    )
    backends = [p["backend"] for p in log.attests()]
    assert backends == ["agent-sdk", "claude-subprocess"], (
        f"AC-P5: one falling-back call must emit TWO events, in dispatch order, "
        f"the second carrying backend='claude-subprocess'; got {backends!r}"
    )

    # Positive control on the non-falling-back path: exactly one.
    control_log = _FakeEventLog()
    set_run(control_log)
    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(backend="claude-subprocess", injections=None)
    )
    control_backends = [p["backend"] for p in control_log.attests()]
    assert control_backends == ["claude-subprocess"], (
        f"AC-P5 positive control: a call that does not fall back must emit exactly "
        f"one event; got {control_backends!r}"
    )


def test_ac_p6_emission_failure_does_not_break_execution() -> None:
    """AC-P6: the attestation goes through `_emit_safe`
    (llm_subprocess.py:2980), so a failing event log cannot break execution.

    Kills: a GREEN calling `event_log.append(...)` directly — that raises out
    of the dispatch path and the step's real status disappears.

    The raiser is TARGETED at `model_invocation_attested` only
    ([G18:EDGE-5]); a blanket raiser would also break `runner_backend_resolved`
    and would prove nothing about this emission in situ.  Per [G22:2] the
    double is handed to one call and never patched over a pytest primitive.

    Two measured outcomes on ONE fixture: `attempts == 1` proves the emission
    was ATTEMPTED (so the test cannot pass by the event never being written),
    and `status == "ok"` proves it was swallowed.  Pre-GREEN both fail —
    `attempts` is 0.
    """
    adapter = _RecordingAdapter("p6")
    register("bd10-rec", adapter)
    log = _TargetedRaisingEventLog(EVENT_TYPE)
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(injections=None))

    assert log.attempts == 1, (
        f"AC-P6: the attestation must have been ATTEMPTED exactly once (otherwise "
        f"this test passes vacuously); got {log.attempts} attempts, other events "
        f"recorded: {log.types()!r}"
    )
    assert result.status == "ok", (
        f"AC-P6: a raising event log must not change the step's outcome; got "
        f"status={result.status!r} error_code={result.error_code!r}"
    )


def test_ac_p8_no_run_context_emits_nothing_and_does_not_raise() -> None:
    """AC-P8 `[bd10:4]`: with NO active run context, `invoke_llm_subprocess`
    completes with its normal status and emits nothing.

    Kills: a GREEN that dereferences `run_ctx.event_log` unguarded.
    `telemetry_ctx.get_current_run()` is `None` outside a run
    (telemetry_ctx.py:73), so such a GREEN raises `AttributeError` on EVERY
    context-free call — including ones the existing suite already makes.

    Two measured outcomes on ONE fixture, attributable to the run context
    ALONE: the identical call and the identical adapter are made twice, first
    with the context cleared and then with one pushed.  The guarded GREEN
    returns `ok` both times and emits 0 then 1.  The unguarded GREEN dies on
    the first.  Counting the second emission is what stops the first half from
    being satisfiable by a GREEN that simply never emits (§0.1 subtype 1).

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    adapter = _RecordingAdapter("p8")
    register("bd10-rec", adapter)

    telemetry_ctx.clear_current_run()
    assert telemetry_ctx.get_current_run() is None, (
        "fixture sanity: the run context must really be absent for the first half"
    )
    unguarded = llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(injections=None))

    assert unguarded.status == "ok", (
        f"AC-P8: a context-free call must complete with its normal status; got "
        f"status={unguarded.status!r} error_code={unguarded.error_code!r}"
    )
    assert len(adapter.calls) == 1, (
        f"AC-P8: the context-free call must still reach the adapter (otherwise the "
        f"'emits nothing' half is measuring a call that never happened); got "
        f"{len(adapter.calls)} call(s)"
    )

    # Positive control: the SAME fixture with a run context emits exactly one.
    log = _FakeEventLog()
    set_run(log)
    guarded = llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(injections=None))

    assert guarded.status == "ok", (
        f"AC-P8 positive control: got status={guarded.status!r} "
        f"error_code={guarded.error_code!r}"
    )
    assert len(log.attests()) == 1, (
        f"AC-P8 positive control: the same fixture under an active run context must "
        f"emit exactly one {EVENT_TYPE!r} event, so the context-free half is a real "
        f"measurement and not a GREEN that never emits; got {len(log.attests())}"
    )


def test_ac_p7_r31_labelled_host_attested() -> None:
    """AC-P7: R3.1 is labelled `host-attested` (CL §8) — the engine hashes at
    assembly, not on the wire, so ADV-9 is not executable in v1.

    Kills: a report that omits the label (a consumer would then read R3.1 as
    wire-attested by default) and a report that overclaims it as
    `wire-attested`.  Equality rejects both.

    Two measured outcomes on ONE fixture: the conformant event set yields
    `host-attested`; a GREEN mutated to label R3.1 `wire-attested` — the
    overclaim CL §8 exists to prevent — fails the same assertion on the same
    events.

    Pre-GREEN: `conformance.bd_l3` does not exist (ImportError).
    """
    from conformance.bd_l3 import check_bd_l3

    report = check_bd_l3([_conformant_attest_payload()])

    assert report.labels["R3.1"] == "host-attested", (
        f"AC-P7: labels['R3.1'] must be 'host-attested' per CL §8; got "
        f"{report.labels.get('R3.1')!r}"
    )


# ---------------------------------------------------------------------------
# §4 — R3.2 and ADV-8, attributed injection
# ---------------------------------------------------------------------------

def test_ac_i1_assemble_order_and_separator() -> None:
    """AC-I1: `assemble(prompt, blocks)` returns the prompt followed by each
    block's content in LIST ORDER, separated by exactly "\\n\\n".

    Kills: a reordering GREEN (`sorted(blocks)`, `reversed(blocks)`), a
    different-separator GREEN ("\\n", " "), and a GREEN that drops the prompt.
    The expected string is composed here by explicit concatenation, so every
    one of those flips the equality.

    Two measured outcomes on ONE fixture: the three distinct blocks give the
    literal below; the same three blocks under a reversed-order GREEN give the
    second literal, which the test asserts is DIFFERENT — so the fixture is
    genuinely order-sensitive rather than accidentally symmetric.

    Pre-GREEN: `conformance.attest` does not exist (ImportError).
    """
    from conformance.attest import InjectedBlock, assemble

    prompt = "PROMPT HEAD"
    blocks = [
        InjectedBlock(source_id="a", content="ALPHA"),
        InjectedBlock(source_id="b", content="BRAVO"),
        InjectedBlock(source_id="c", content="CHARLIE"),
    ]

    expected = "PROMPT HEAD" + "\n\n" + "ALPHA" + "\n\n" + "BRAVO" + "\n\n" + "CHARLIE"
    reversed_expected = (
        "PROMPT HEAD" + "\n\n" + "CHARLIE" + "\n\n" + "BRAVO" + "\n\n" + "ALPHA"
    )
    assert expected != reversed_expected, "fixture sanity: the order must matter"

    got = assemble(prompt, blocks)
    assert got == expected, (
        f"AC-I1: assemble must join prompt+contents in list order with exactly "
        f"'\\n\\n'; expected {expected!r}, got {got!r}"
    )

    # An empty block list leaves the prompt untouched — the separator must not
    # be appended when there is nothing to separate.
    assert assemble(prompt, []) == prompt, (
        f"AC-I1: assemble(prompt, []) must return the prompt unchanged; got "
        f"{assemble(prompt, [])!r}"
    )


def test_ac_i2_injections_in_declaration_order() -> None:
    """AC-I2: `injections` is a list of `{source_id, sha256}` mappings, one per
    block, in DECLARATION ORDER, each digest over that block's content ALONE.

    Kills, quantified over the three blocks of one call (pairwise-distinct
    source_ids AND pairwise-distinct contents): a GREEN recording only the
    first; only the last; a set collapsed by de-duplication (three entries are
    required, and list equality pins the order so `first`/`last`/`any`
    reductions all die); a GREEN digesting the ASSEMBLED text instead of the
    block content (the per-block digests below differ from the assembled one).

    Two measured outcomes on ONE fixture: intact, the list equals the literal
    below; with the GREEN mutated to `injections[:1]` the same fixture yields a
    one-element list.  Every digest is recomputed here with hashlib from the
    content string the test supplied (§0.1 subtype 3).

    Pre-GREEN: `conformance.attest` does not exist (ImportError).
    """
    from conformance.attest import InjectedBlock

    blocks = [
        InjectedBlock(source_id="src-alpha", content="ALPHA CONTENT"),
        InjectedBlock(source_id="src-bravo", content="BRAVO CONTENT"),
        InjectedBlock(source_id="src-charlie", content="CHARLIE CONTENT"),
    ]
    expected = [
        {"source_id": "src-alpha", "sha256": sha256_of("ALPHA CONTENT")},
        {"source_id": "src-bravo", "sha256": sha256_of("BRAVO CONTENT")},
        {"source_id": "src-charlie", "sha256": sha256_of("CHARLIE CONTENT")},
    ]

    adapter = _RecordingAdapter("i2")
    register("bd10-rec", adapter)
    log = _FakeEventLog()
    set_run(log)

    llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(injections=blocks))

    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-I2: expected exactly one {EVENT_TYPE!r} event; got {len(attests)}"
    )
    got = attests[0]["injections"]
    assert got == expected, (
        f"AC-I2: injections must be the three per-block records in declaration "
        f"order; expected {expected!r}, got {got!r}"
    )


def test_ac_i2_injections_empty_for_none_and_for_empty_sequence() -> None:
    """AC-I2 (cont.): `injections` is `[]` for BOTH `None` and `()`.

    Kills: a GREEN that writes `null` for `None` (which would let a consumer
    read "no injection channel used" as the affirmative claim "nothing was
    injected by any route" — the [G2:4] overclaim shape), and a GREEN that
    distinguishes the two in the payload, which §4 AC-I2 forbids deliberately.

    Two measured outcomes on ONE fixture pair: both calls yield `[]` today's
    spec-conformant GREEN; a GREEN forwarding `None` through yields `None` for
    the first and `[]` for the second, failing the equality AND the
    equal-to-each-other assertion.  Note `[] == None` is False, so neither
    half is trivially satisfied.

    Pre-GREEN: no event at all.
    """
    adapter = _RecordingAdapter("i2b")
    register("bd10-rec", adapter)

    log_none = _FakeEventLog()
    set_run(log_none)
    llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(injections=None))

    log_empty = _FakeEventLog()
    set_run(log_empty)
    llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(injections=()))

    assert len(log_none.attests()) == 1 and len(log_empty.attests()) == 1, (
        f"AC-I2: both calls must emit exactly one event; got "
        f"{len(log_none.attests())} and {len(log_empty.attests())}"
    )
    none_value = log_none.attests()[0]["injections"]
    empty_value = log_empty.attests()[0]["injections"]
    assert none_value == [], (
        f"AC-I2: injections=None must record [] (not null); got {none_value!r}"
    )
    assert empty_value == [], (
        f"AC-I2: injections=() must record []; got {empty_value!r}"
    )
    assert none_value == empty_value, (
        "AC-I2: None and () must be indistinguishable in the payload"
    )


@pytest.mark.parametrize("bad_source_id", [None, "", 42], ids=["none", "empty", "not-str"])
@pytest.mark.parametrize("position", ["first", "last"])
def test_ac_i3_unattributed_block_blocks_dispatch(bad_source_id, position) -> None:
    """AC-I3 / ADV-8: a block whose `source_id` is `None`, `""` or not a `str`
    yields `E_INJECT_UNATTRIBUTED`, `recoverable=False`, and NO dispatch.

    Kills, quantified over the blocks of one call in BOTH ORDERINGS (offender
    FIRST of three in one fixture, LAST of three in another — [G18:1]'s exact
    shape): a GREEN validating only `blocks[0]`; only `blocks[-1]`; a GREEN
    that validates but dispatches anyway; a GREEN that returns `recoverable=True`.

    "No dispatch occurred" is asserted POSITIVELY, by the recording adapter's
    call count being 0 rather than by the absence of a side effect (§0.1
    subtype 1) — and the positive control below proves the counter can reach 1
    on this very adapter, so a 0 cannot come from a mis-registered backend.

    Two measured outcomes on ONE fixture: on the offender-LAST fixture the
    intact GREEN returns E_INJECT_UNATTRIBUTED with call count 0, while a
    GREEN mutated to check only `blocks[0]` returns ok with call count 1 —
    which is exactly what the positive control measures on the same three
    blocks with the offender repaired.

    Pre-GREEN: `injections` is not a parameter of `invoke_llm_subprocess`, so
    the call raises TypeError.
    """
    from conformance.attest import InjectedBlock

    good_a = InjectedBlock(source_id="src-good-a", content="A")
    good_b = InjectedBlock(source_id="src-good-b", content="B")
    offender = InjectedBlock(source_id=bad_source_id, content="OFFENDER")
    repaired = InjectedBlock(source_id="src-repaired", content="OFFENDER")

    if position == "first":
        blocks = [offender, good_a, good_b]
        control_blocks = [repaired, good_a, good_b]
    else:
        blocks = [good_a, good_b, offender]
        control_blocks = [good_a, good_b, repaired]

    adapter = _RecordingAdapter("i3")
    register("bd10-rec", adapter)
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(injections=blocks))

    assert result.status == "error", (
        f"AC-I3 ({position}, {bad_source_id!r}): an unattributed block must fail the "
        f"call; got status={result.status!r}"
    )
    assert result.error_code == E_INJECT_UNATTRIBUTED, (
        f"AC-I3 ({position}, {bad_source_id!r}): error_code must be "
        f"{E_INJECT_UNATTRIBUTED!r} (CL:98 @ fd35e1304); got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC-I3 ({position}, {bad_source_id!r}): E_INJECT_UNATTRIBUTED is "
        f"non-recoverable; got recoverable={result.recoverable!r}"
    )
    assert len(adapter.calls) == 0, (
        f"AC-I3 ({position}, {bad_source_id!r}): NO dispatch may occur; the adapter "
        f"recorded {len(adapter.calls)} call(s)"
    )

    # Positive control on the SAME three blocks with the offender attributed.
    control_result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(injections=control_blocks)
    )
    assert control_result.status == "ok", (
        f"AC-I3 positive control ({position}): three attributed blocks must dispatch; "
        f"got status={control_result.status!r} error_code={control_result.error_code!r}"
    )
    assert len(adapter.calls) == 1, (
        f"AC-I3 positive control ({position}): the adapter must have been called "
        f"exactly once in total; got {len(adapter.calls)}"
    )


def test_ac_i4_inject_unattributed_registered_and_drift_gate_clean() -> None:
    """AC-I4: `E_INJECT_UNATTRIBUTED` is registered in `error_codes.ERROR_CODES`
    with a trigger description, AND `error_codes.py --check` still exits 0 on
    the real tree.

    Kills: a GREEN that returns the code without registering it (the drift gate
    at tests/test_gh1067_ignored_dir_exclusion.py:120 would then fail on
    `main`), and a GREEN that registers a blank description.

    DECLARED PRE-PASSING (§0.6): the `--check == 0` half PASSES today — the
    gate is already clean on this base.  It is a shield, not a measurement: it
    pins that this lot's additions do not break the gate.  The membership half
    FAILS today.  `main(["--check"])` is called in-process, which is the same
    entry point `__main__` uses (error_codes.py:322-323).
    """
    assert E_INJECT_UNATTRIBUTED in error_codes.ERROR_CODES, (
        f"AC-I4: {E_INJECT_UNATTRIBUTED!r} (CL:98 @ fd35e1304) must be registered in "
        f"error_codes.ERROR_CODES"
    )
    description = error_codes.ERROR_CODES[E_INJECT_UNATTRIBUTED]
    assert isinstance(description, str) and description.strip(), (
        f"AC-I4: the registry entry must carry a one-line trigger description; got "
        f"{description!r}"
    )
    # Pre-passing shield — declared above.
    assert error_codes.main(["--check"]) == 0, (
        "AC-I4: error_codes.py --check must exit 0 on the real tree "
        "(no UNREGISTERED / DEAD drift introduced by this lot)"
    )


def test_ac_i5_phase_2_explore_role_template_routes_through_injections(tmp_path) -> None:
    """AC-I5: `phase_2_explore`'s role-template inlining
    (workflows/phase_2_explore.py:180,294) routes through the `injections`
    channel with `source_id` equal to the resolved role-template path, so
    ADV-8 tests a door the pipeline actually uses.

    Kills: a GREEN that adds the `injections` parameter but migrates no real
    call site (the attributed channel would then be dead code); a GREEN that
    routes the block but attributes it to a constant like "role_template"
    instead of the resolved path; a GREEN that digests the wrong text.

    Two measured outcomes on ONE fixture: with the phase migrated, the emitted
    event's `injections` equals the single record below, recomputed here from
    the file this test wrote; with the phase left on string concatenation the
    same run emits `injections == []`.

    `[bd10:10]` v2 PINS both normalisations the digest depends on, so the
    fixture no longer has to dodge them and the assertion is satisfiable
    exactly one way: `source_id` is `str(Path(role_path).expanduser())` — the
    same resolution `_maybe_role_template` performs at
    workflows/phase_2_explore.py:185, NOT `.resolve()`, so a symlinked home
    does not change the recorded identifier — and `content` is
    `rp.read_text(encoding="utf-8").rstrip() + "\\n\\n"` (:188), the trailing
    normalisation being part of the injected bytes and therefore part of the
    hash.  The role body below deliberately carries trailing whitespace, so a
    GREEN that digests the RAW file text produces a different digest and dies.

    SCOPE — option (a), the whole workflow, deliberately.  The alternative was
    to call `_invoke_explore_llm` alone, but AC-I5's point is that the
    attributed channel is load-bearing on a REAL phase; a test that hand-feeds
    the invoking step its `prev` would no longer prove the role template
    travelled from `_maybe_role_template` (workflows/phase_2_explore.py:180)
    through `_build_explore_prompt` (:294) into the dispatch.  Driving
    `engine.execute()` keeps that chain intact.  The price is that the double
    must honour the caller's downstream contract, so `_RecordingAdapter` merges
    `extra_data` exactly as the real backend does (llm_subprocess.py:1920) —
    without it `_write_explore_doc` raises `KeyError: 'doc_path'`
    (workflows/phase_2_explore.py:429), a failure NO GREEN removes, which is
    EMISSIONS_SPEC §0.4's dead assertion in a red hat.  The
    workflow-completed precondition below fails loudly if that ever regresses,
    instead of letting an unrelated exception masquerade as this RED.

    Pre-GREEN: the phase does not use `injections`, so no attest event exists.
    """
    scratchpad = tmp_path / "scratch"
    role_path = tmp_path / "role.md"
    # Trailing whitespace ON PURPOSE: it makes the pinned `.rstrip() + "\n\n"`
    # normalisation observable, so a raw-file-text GREEN fails the digest.
    role_file_text = "ROLE TEMPLATE LINE 1\nROLE TEMPLATE LINE 2\n   \n"
    role_path.write_text(role_file_text, encoding="utf-8")

    # The two values v2 pins, spelled exactly as `[bd10:10]` pins them.
    expected_source_id = str(Path(str(role_path)).expanduser())
    expected_content = role_file_text.rstrip() + "\n\n"
    assert expected_content != role_file_text, (
        "fixture sanity: the pinned normalisation must actually change the bytes, "
        "otherwise the digest assertion cannot distinguish the two readings"
    )

    from phase_2_explore import phase_2_explore_workflow

    register("claude-subprocess", _RecordingAdapter(
        "i5", data={"raw_response": "findings\n\nSTATUS: DONE\n"}
    ), capabilities=("manifest", "progress_since", "abort"))

    log = _FakeEventLog()
    engine = WorkflowEngine(event_log=log)
    engine.register("p2", phase_2_explore_workflow())
    result, _ = engine.execute("p2", WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "model": "sonnet",
            "role_template_path": str(role_path),
        },
        question="bd10 AC-I5 feature request",
        session_id="bd10-i5",
        persona="hal",
        framework=None,
        domain=None,
    ))

    # Fixture precondition, NOT the AC: the phase must run to completion, so a
    # downstream contract break can never be mistaken for this RED.
    assert result.status == "ok", (
        f"AC-I5 precondition: the explore workflow must complete (STATUS: DONE); got "
        f"status={result.status!r} error_code={result.error_code!r} — this is a "
        f"fixture failure, not a measurement of R3.2"
    )

    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-I5: the explore LLM dispatch must emit exactly one {EVENT_TYPE!r} event; "
        f"got {len(attests)} (events: {sorted(set(log.types()))!r})"
    )
    assert attests[0]["injections"] == [
        {"source_id": expected_source_id, "sha256": sha256_of(expected_content)}
    ], (
        f"AC-I5 (`[bd10:10]`): the role template must be attributed to "
        f"str(Path(p).expanduser()) with the digest of "
        f"read_text().rstrip() + '\\n\\n', recomputed here from the file this test "
        f"wrote; got {attests[0]['injections']!r}"
    )


# ---------------------------------------------------------------------------
# §5 — R3.3 and ADV-7, reported model identity versus the pin
# ---------------------------------------------------------------------------

def _write_tier_config(tmp_path, monkeypatch, *, tier: str, model: str) -> None:
    """Point the tier-model config seam at a controlled file.

    Seam: `config_provider.models_config_path`, resolved at CALL time inside
    `_load_tier_model` (llm_subprocess.py:280) — patching the module attribute
    therefore reaches it regardless of import order.  Established seam
    (tests/test_GH375_tier_model_dispatch.py:132).
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {tier: model}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)


def test_ac_m1_absent_observed_model_is_a_third_state(tmp_path, monkeypatch) -> None:
    """AC-M1: an adapter that reports no `observed_model` is a first-class
    THIRD state — no comparison, no error.

    Kills: a GREEN that substitutes `model_requested` when the adapter reports
    nothing.  That is §0.1 subtype (3) promoted into production — a pin
    compared against itself, which can never fail.

    Two measured outcomes on ONE fixture, and `[bd10:5]` makes the
    substitution DIRECTLY observable in the log: the payload's
    `observed_model` MUST be `null`, so a backfilling GREEN writes the
    dispatched model there and fails the equality on the same fixture.  The
    fixture keeps tier dispatch ACTIVE (caller pins `opus`, SIMPLE maps to
    `haiku`, llm_subprocess.py:1254-1273 rebinds) so that a backfill from
    EITHER side of the rebind — `model_requested` or the caller's argument —
    is a visibly different string from `null`.  `None` is a specified value
    here, not an absence: §0.5 forbids discharging it with a presence check,
    and the key itself is required by AC-P1's exact set.

    THE REPORT HALF, writable at last via `[bd10:13]`: a log whose only
    invocation carried a `null` `observed_model` aggregates to
    `"verdict:R3.3" == not-checked`; the SAME log shape where the adapter DID
    report aggregates to `passed`.  That pair is the second reading of the
    same mutation — a GREEN backfilling `model_requested` compares a pin
    against itself, always matches, and reports `passed` where `not-checked`
    is required.

    A third call proves the comparison is live on this fixture at all — an
    adapter reporting a FOREIGN family errors — so the two `ok`s above cannot
    come from the check being skipped.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    from conformance.bd_l3 import check_bd_l3

    _write_tier_config(tmp_path, monkeypatch, tier="SIMPLE", model="haiku")

    silent = _RecordingAdapter("m1-silent")
    register("bd10-rec", silent)
    log = _FakeEventLog()
    set_run(log, tier="SIMPLE")

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="opus", injections=None)
    )

    assert len(silent.calls) == 1, (
        f"AC-M1 precondition: the adapter must be dispatched; got {len(silent.calls)} calls"
    )
    assert silent.calls[0]["model"] == "haiku", (
        f"AC-M1 precondition: tier dispatch must be ACTIVE so a backfill from either "
        f"side of the rebind is visible; the adapter was dispatched with model="
        f"{silent.calls[0]['model']!r}"
    )
    assert result.status == "ok", (
        f"AC-M1: an adapter that reports no observed_model must not be compared "
        f"against anything; got status={result.status!r} "
        f"error_code={result.error_code!r}"
    )
    assert result.error_code is None, (
        f"AC-M1: absence is not an error; got error_code={result.error_code!r}"
    )
    assert len(log.attests()) == 1, (
        f"AC-M1: the dispatch must still be attested; got {len(log.attests())} events"
    )
    assert log.attests()[0]["observed_model"] is None, (
        f"AC-M1 (`[bd10:5]`): the recorded third state is `null` and MUST NOT be "
        f"backfilled from model_requested or from the caller's pin; got "
        f"{log.attests()[0]['observed_model']!r}"
    )
    silent_verdict = check_bd_l3(log.attests()).labels.get(VERDICT_R33)
    assert silent_verdict == REQUIREMENT_NOT_CHECKED, (
        f"AC-M1 (`[bd10:13]`): a log in which NO invocation carried a non-null "
        f"observed_model aggregates to {REQUIREMENT_NOT_CHECKED!r} for R3.3 — a "
        f"backfilling GREEN compares the pin against itself, always matches, and "
        f"reports {REQUIREMENT_PASSED!r} here; got {silent_verdict!r}"
    )

    # Same log shape, adapter DID report the dispatched family → passed.
    register("bd10-rec", _RecordingAdapter(
        "m1-reporting", data={"observed_model": "claude-haiku-4-5-20251001"}
    ))
    log_reporting = _FakeEventLog()
    set_run(log_reporting, tier="SIMPLE")
    reported = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="opus", injections=None)
    )
    assert reported.status == "ok" and reported.error_code is None, (
        f"AC-M1: an adapter reporting the dispatched family must be ok; got "
        f"status={reported.status!r} error_code={reported.error_code!r}"
    )
    reported_verdict = check_bd_l3(log_reporting.attests()).labels.get(VERDICT_R33)
    assert reported_verdict == REQUIREMENT_PASSED, (
        f"AC-M1 (`[bd10:13]`): a log whose invocation DID report a matching model "
        f"aggregates to {REQUIREMENT_PASSED!r} for R3.3 — without this half, "
        f"{REQUIREMENT_NOT_CHECKED!r} above is satisfiable by a checker that returns "
        f"it unconditionally; got {reported_verdict!r}"
    )

    # Liveness control: the comparison IS live on this fixture shape.
    register("bd10-rec", _RecordingAdapter(
        "m1-foreign", data={"observed_model": "claude-sonnet-4-6"}
    ))
    set_run(log, tier="SIMPLE")
    control = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="opus", injections=None)
    )
    assert control.error_code == E_MODEL_PIN_MISMATCH, (
        f"AC-M1 liveness control: an adapter reporting a foreign family on this same "
        f"fixture must error, otherwise the 'ok' above measures nothing; got "
        f"error_code={control.error_code!r}"
    )


def test_ac_m2_family_mismatch_errors_and_family_match_is_ok() -> None:
    """AC-M2 / ADV-7: an adapter registered through the public seam that
    reports a model of a DIFFERENT family from the dispatched request yields
    `E_MODEL_PIN_MISMATCH`, `recoverable=False`, carrying both
    `observed_model` and `pinned_model` in `data`.

    Kills: a GREEN that warns and proceeds (the pre-`[bd10:2]` behaviour at
    llm_subprocess.py:917-919); a GREEN that errors but drops the two
    diagnostic fields; a GREEN that errors on EVERY invocation (the positive
    control catches that).

    Two measured outcomes on ONE fixture shape: the haiku-reporting adapter
    errors, the sonnet-reporting adapter on the identical call is `ok`.  No
    tier is active here, so the dispatched request model and the caller's
    argument coincide and `pinned_model` is unambiguous.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    drifting = _RecordingAdapter(
        "m2-drift", data={"observed_model": "claude-haiku-4-5-20251001"}
    )
    register("bd10-rec", drifting)
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="sonnet", injections=None)
    )

    assert result.status == "error", (
        f"AC-M2: a family-drifted invocation must FAIL (CL:99 governs over the "
        f"warn-only path); got status={result.status!r}"
    )
    assert result.error_code == E_MODEL_PIN_MISMATCH, (
        f"AC-M2: error_code must be {E_MODEL_PIN_MISMATCH!r} (CL:99 @ fd35e1304); "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC-M2: E_MODEL_PIN_MISMATCH is non-recoverable; got {result.recoverable!r}"
    )
    assert (result.data or {}).get("observed_model") == "claude-haiku-4-5-20251001", (
        f"AC-M2: data must carry the observed model verbatim; got "
        f"{(result.data or {}).get('observed_model')!r}"
    )
    assert (result.data or {}).get("pinned_model") == "sonnet", (
        f"AC-M2: data must carry the dispatched request model as pinned_model; got "
        f"{(result.data or {}).get('pinned_model')!r}"
    )

    # Positive control on the same fixture shape: same family → ok.
    matching = _RecordingAdapter("m2-match", data={"observed_model": "claude-sonnet-4-6"})
    register("bd10-rec", matching)
    set_run(log)
    control = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="sonnet", injections=None)
    )
    assert control.status == "ok" and control.error_code is None, (
        f"AC-M2 positive control: a same-family report must be ok; got "
        f"status={control.status!r} error_code={control.error_code!r}"
    )


def test_ac_m3_comparison_target_is_the_dispatched_tier_model(tmp_path, monkeypatch) -> None:
    """AC-M3 (Class-B fence): the comparison target is the DISPATCHED request
    model, not the caller's original `model` argument.

    `llm_subprocess.py:1254-1273` rebinds `model` to the tier model when tier
    dispatch applies, and `:1270` calls the PRE-rebind value `pinned_model` —
    so a GREEN that reuses that name as the comparison target false-fails
    every tier-dispatched step in production.

    Kills: exactly that GREEN.  Two measured outcomes on ONE fixture (tier
    dispatch active, caller pins `opus`, SIMPLE maps to `haiku`): an adapter
    reporting the TIER family must be `ok`, and — on the identical fixture —
    an adapter reporting the PRE-REBIND family must ERROR.  A pre-rebind
    comparison inverts both verdicts, so neither assertion can be satisfied by
    the wrong target.

    `[bd10:7]` v2 pins the payload's `model_requested` as the POST-rebind
    dispatched model, so it is asserted by value HERE — on the one fixture
    where the two candidates differ — and not only where they coincide
    (AC-P1).  Recording the pre-rebind value would put a model in the log that
    was never invoked.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    _write_tier_config(tmp_path, monkeypatch, tier="SIMPLE", model="haiku")

    tier_reporter = _RecordingAdapter(
        "m3-tier", data={"observed_model": "claude-haiku-4-5-20251001"}
    )
    register("bd10-rec", tier_reporter)
    log = _FakeEventLog()
    set_run(log, tier="SIMPLE")

    ok_result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="opus", injections=None)
    )

    dispatched_model = tier_reporter.calls[0]["model"] if tier_reporter.calls else None
    assert dispatched_model == "haiku", (
        f"AC-M3 precondition: tier dispatch must be active (dispatched model must be "
        f"'haiku'); adapter saw {dispatched_model!r} "
        f"({len(tier_reporter.calls)} call(s) recorded)"
    )
    assert ok_result.status == "ok" and ok_result.error_code is None, (
        f"AC-M3: an adapter reporting the TIER model must be ok — comparing against "
        f"the pre-rebind pin would false-fail every tier-dispatched step; got "
        f"status={ok_result.status!r} error_code={ok_result.error_code!r}"
    )
    assert len(log.attests()) == 1, (
        f"AC-M3: the tier-dispatched call must be attested; got {len(log.attests())}"
    )
    assert log.attests()[0]["model_requested"] == "haiku", (
        f"AC-M3 (`[bd10:7]`): model_requested is the POST-rebind dispatched model — "
        f"recording the caller's pre-rebind 'opus' would log a model that was never "
        f"invoked; got {log.attests()[0]['model_requested']!r}"
    )

    prerebind_reporter = _RecordingAdapter(
        "m3-prerebind", data={"observed_model": "claude-opus-4-8"}
    )
    register("bd10-rec", prerebind_reporter)
    set_run(log, tier="SIMPLE")
    err_result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="opus", injections=None)
    )
    assert err_result.error_code == E_MODEL_PIN_MISMATCH, (
        f"AC-M3: on the SAME fixture, an adapter reporting the caller's PRE-REBIND "
        f"family while 'haiku' was dispatched is real drift and must error; got "
        f"error_code={err_result.error_code!r}"
    )


def test_ac_m4_family_comparison_not_raw_equality() -> None:
    """AC-M4: mismatch iff BOTH families resolve via
    `lib.llm_provider._claude_model_family` and DIFFER.  An unresolvable
    observed family is `not-checked`, not drift.

    Kills: a raw-equality GREEN (the in-session servicer maps `"opus"` to a
    Task model id such as `"claude-opus-4-8"`, llm_subprocess.py:3074-3075, so
    raw equality false-fails every real invocation); and a GREEN that treats an
    unrecognised token as evidence of drift.

    Two measured outcomes on ONE fixture set, all three dispatched with
    `model="opus"`: `"claude-opus-4-8"` → ok (raw-equality GREEN errors here);
    `"octopus-3"` → ok (a substring/naive GREEN sees "opus"… and an
    unrecognised-means-drift GREEN errors here); `"claude-haiku-4-5-20251001"`
    → error (so the two `ok`s are not the check being dead).

    The family ladder is pinned against the EXTERNAL source at
    lib/llm_provider.py:113, not against a constant this lot writes.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    assert set(CLAUDE_PROVIDER.model_rank) == EXTERNAL_FAMILIES, (
        f"AC-M4: the external family ladder at lib/llm_provider.py:113 must be "
        f"{sorted(EXTERNAL_FAMILIES)!r}; got {sorted(CLAUDE_PROVIDER.model_rank)!r}"
    )

    log = _FakeEventLog()
    outcomes: dict[str, tuple] = {}
    for observed in ("claude-opus-4-8", "octopus-3", "claude-haiku-4-5-20251001"):
        register("bd10-rec", _RecordingAdapter("m4", data={"observed_model": observed}))
        set_run(log)
        r = llm_subprocess.invoke_llm_subprocess(
            **invoke_kwargs(model="opus", injections=None)
        )
        outcomes[observed] = (r.status, r.error_code)

    assert outcomes["claude-opus-4-8"] == ("ok", None), (
        f"AC-M4: a full model id of the SAME family as the request must be ok "
        f"(family comparison, not raw equality); got {outcomes['claude-opus-4-8']!r}"
    )
    assert outcomes["octopus-3"] == ("ok", None), (
        f"AC-M4: an unresolvable observed family is not-checked, not drift; got "
        f"{outcomes['octopus-3']!r}"
    )
    assert outcomes["claude-haiku-4-5-20251001"] == ("error", E_MODEL_PIN_MISMATCH), (
        f"AC-M4: two resolvable, differing families are drift; got "
        f"{outcomes['claude-haiku-4-5-20251001']!r}"
    )


def test_ac_m5_mismatch_event_still_emitted_and_step_errors() -> None:
    """AC-M5: the existing `model_pin_mismatch` telemetry event is STILL
    emitted on the flip (additivity for its existing consumers), AND the step
    now ends `status == "error"`.

    Kills: a GREEN that flips the status and drops the event (silently
    breaking every consumer of `model_pin_mismatch`); a GREEN that keeps the
    event and leaves the step `ok` (the pre-`[bd10:2]` warn-only behaviour).
    Both halves are asserted on ONE fixture, so the AC cannot be discharged by
    either half alone.

    Two measured outcomes on ONE fixture: intact, the event is present and the
    status is `error`; with the emit deleted the same fixture fails the first
    half, and with the flip reverted it fails the second.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    register("bd10-rec", _RecordingAdapter(
        "m5", data={"observed_model": "claude-haiku-4-5-20251001"}
    ))
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(model="sonnet", injections=None)
    )

    mismatch = log.payloads("model_pin_mismatch")
    assert len(mismatch) >= 1, (
        f"AC-M5: the existing 'model_pin_mismatch' event must still be emitted on the "
        f"flip; events seen: {log.types()!r}"
    )
    assert result.status == "error", (
        f"AC-M5: the step must now END in error, not warn-and-proceed; got "
        f"status={result.status!r}"
    )
    assert result.error_code == E_MODEL_PIN_MISMATCH, (
        f"AC-M5: error_code must be {E_MODEL_PIN_MISMATCH!r}; got {result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# §6 — R3.4, R3.5, R3.6 and ADV-10, the declared capability set
# ---------------------------------------------------------------------------

def test_ac_c1_declared_capabilities_verbatim_empty_and_null() -> None:
    """AC-C1: `declared_capabilities` records the `allowed_tools` argument
    VERBATIM AS A LIST when a declaration exists, and `null` when it is `None`.

    Kills: a GREEN normalising `None` to `[]`, which converts "no declaration"
    into the affirmative claim "no tools were granted" — the `[G2:4]` overclaim
    shape on the field where it matters most (llm_subprocess.py:1143 already
    makes the two semantically distinct); a GREEN coercing the list to a
    sorted/set/tuple form (the real declaration below is NOT sorted, so
    ordering is measured); a GREEN dropping the operand from
    `"Bash(graphify-shim.sh:*)"`.

    Three fixtures, one per value, because `[]` and `None` are distinct by
    value and neither is the other's default.  Two measured outcomes: the real
    declaration round-trips exactly, and the `None` fixture stays `None` while
    the `[]` fixture stays `[]` — a normalising GREEN collapses those two into
    one value and fails.

    Declaration pinned from workflows/phase_2_explore.py:372 (§0.2).

    Pre-GREEN: no event exists.
    """
    log_declared = _FakeEventLog()
    register("bd10-rec", _RecordingAdapter("c1a"))
    set_run(log_declared)
    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=list(REAL_DECLARED_TOOLS), injections=None)
    )

    log_empty = _FakeEventLog()
    set_run(log_empty)
    llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(allowed_tools=[], injections=None))

    log_none = _FakeEventLog()
    set_run(log_none)
    llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(allowed_tools=None, injections=None))

    for name, lg in (("declared", log_declared), ("empty", log_empty), ("none", log_none)):
        assert len(lg.attests()) == 1, (
            f"AC-C1 ({name}): expected exactly one {EVENT_TYPE!r} event; got "
            f"{len(lg.attests())}"
        )

    declared_value = log_declared.attests()[0]["declared_capabilities"]
    assert declared_value == REAL_DECLARED_TOOLS, (
        f"AC-C1: the declaration must be recorded verbatim as a list, in order, "
        f"operands intact; expected {REAL_DECLARED_TOOLS!r}, got {declared_value!r}"
    )
    assert log_empty.attests()[0]["declared_capabilities"] == [], (
        f"AC-C1: allowed_tools=[] must record []; got "
        f"{log_empty.attests()[0]['declared_capabilities']!r}"
    )
    assert log_none.attests()[0]["declared_capabilities"] is None, (
        f"AC-C1: allowed_tools=None must record null — normalising it to [] would "
        f"claim 'no tools were granted'; got "
        f"{log_none.attests()[0]['declared_capabilities']!r}"
    )


def test_ac_c2_capability_enforcement_non_uniform_across_two_backends() -> None:
    """AC-C2 (R3.5): `capability_enforcement` records WHO ENFORCES, from the
    closed set {"runtime-allowlist", "not-enforced"}, derived from a new
    `"tool_allowlist"` token in `_BACKEND_CAPABILITIES` (llm_subprocess.py:96).

    Kills: a GREEN recording `"runtime-allowlist"` uniformly — which would
    claim enforcement for an adapter that has none
    (lib/reference_backends/anthropic_api.py:139,149 ACCEPTS AND IGNORES
    `allowed_tools`); a GREEN keying on the backend NAME rather than the
    capability token (neither backend below is named `claude-subprocess`, so a
    name-keyed GREEN cannot produce `runtime-allowlist` at all); a GREEN
    inventing a third value.

    Non-uniform across TWO backends in ONE test, one of each value — a uniform
    fixture would leave both a constant-`runtime-allowlist` and a
    constant-`not-enforced` GREEN alive.  The shipped default map is asserted
    separately so the production token is not merely a test-registration
    artifact.

    Pre-GREEN: no event, and the token is absent from the shipped map.
    """
    assert "tool_allowlist" in llm_subprocess._DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"], (
        "AC-C2: 'claude-subprocess' must declare the new 'tool_allowlist' token in "
        "the shipped _BACKEND_CAPABILITIES (llm_subprocess.py:96) — it appends "
        "--allowed-tools before spawn (:1374-1376); got "
        f"{sorted(llm_subprocess._DEFAULT_BACKEND_CAPABILITIES['claude-subprocess'])!r}"
    )

    register("bd10-enforcing", _RecordingAdapter("c2-on"),
             capabilities=("manifest", "tool_allowlist"))
    register("bd10-inert", _RecordingAdapter("c2-off"), capabilities=("manifest",))

    log_on = _FakeEventLog()
    set_run(log_on)
    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(backend="bd10-enforcing",
                        allowed_tools=list(REAL_DECLARED_TOOLS), injections=None)
    )

    log_off = _FakeEventLog()
    set_run(log_off)
    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(backend="bd10-inert",
                        allowed_tools=list(REAL_DECLARED_TOOLS), injections=None)
    )

    assert len(log_on.attests()) == 1 and len(log_off.attests()) == 1, (
        f"AC-C2: both backends must emit exactly one event; got "
        f"{len(log_on.attests())} and {len(log_off.attests())}"
    )
    on_value = log_on.attests()[0]["capability_enforcement"]
    off_value = log_off.attests()[0]["capability_enforcement"]
    assert on_value == "runtime-allowlist", (
        f"AC-C2: a backend declaring 'tool_allowlist' must record 'runtime-allowlist'; "
        f"got {on_value!r}"
    )
    assert off_value == "not-enforced", (
        f"AC-C2: a backend NOT declaring 'tool_allowlist' must record 'not-enforced' — "
        f"claiming enforcement for it would be an overclaim; got {off_value!r}"
    )
    assert {on_value, off_value} <= ENFORCEMENT_VALUES, (
        f"AC-C2: values must come from the closed set {sorted(ENFORCEMENT_VALUES)!r}; "
        f"got {sorted({on_value, off_value})!r}"
    )


def test_ac_c3_capability_escapes_both_orderings() -> None:
    """AC-C3 / ADV-10 / R3.6, the pure function.  `[bd10:6]` v2 pins the first
    parameter as `observed_tools`, a `Sequence[str]` of distinct tool heads —
    NOT an event list — and pins the return as SORTED and deduplicated.

    Matching rule pinned (`[G22:4]`): an observed head is inside the declared
    set iff the set contains an entry whose text BEFORE the first "(" equals
    that head exactly, case-sensitively — so `"Bash(graphify-shim.sh:*)"`
    admits an observed `"Bash"`.

    Kills, quantified over `observed_tools` in BOTH ORDERINGS (offender FIRST
    of three in one fixture, LAST of three in another): an `any`/`first`/`last`
    reduction; a GREEN comparing the raw declared entry to the head (which
    would report `"Bash"` as an escape); a case-folding GREEN (`"bash"` below
    must be an escape); a GREEN that reports permitted heads; a GREEN
    returning input order rather than sorted order (the two-escape fixture is
    supplied in reverse-sorted order, so the two differ); a GREEN that does
    not deduplicate.

    Two measured outcomes on ONE fixture: the offender-last sequence yields
    `("Task",)`, while a `first`-reducing GREEN yields `()` on that same
    sequence.  The positive control spans ≥2 permitted heads and yields `()`,
    so a report-everything GREEN dies too.

    Pre-GREEN: `conformance.attest` does not exist (ImportError).
    """
    from conformance.attest import capability_escapes

    declared = list(REAL_DECLARED_TOOLS)

    assert tuple(capability_escapes(["Task", "Read", "Bash"], declared)) == ("Task",), (
        f"AC-C3 (offender FIRST): expected ('Task',); got "
        f"{tuple(capability_escapes(['Task', 'Read', 'Bash'], declared))!r}"
    )
    assert tuple(capability_escapes(["Read", "Bash", "Task"], declared)) == ("Task",), (
        f"AC-C3 (offender LAST): expected ('Task',) — a first-member reduction "
        f"returns () here; got "
        f"{tuple(capability_escapes(['Read', 'Bash', 'Task'], declared))!r}"
    )

    # Case-sensitivity is pinned, so a case-folded head is an escape.
    assert tuple(capability_escapes(["Read", "bash", "Write"], declared)) == ("bash",), (
        f"AC-C3: matching is case-sensitive; 'bash' must be an escape against "
        f"'Bash(graphify-shim.sh:*)'; got "
        f"{tuple(capability_escapes(['Read', 'bash', 'Write'], declared))!r}"
    )

    # SORTED, not input order: supplied reverse-sorted so the two differ.
    assert tuple(capability_escapes(["Task", "NotebookEdit", "Read"], declared)) == (
        "NotebookEdit", "Task",
    ), (
        f"AC-C3 (`[bd10:6]`): the result is SORTED, not input order; got "
        f"{tuple(capability_escapes(['Task', 'NotebookEdit', 'Read'], declared))!r}"
    )

    # DEDUPLICATED: a repeated escape appears once.
    assert tuple(capability_escapes(["Task", "Read", "Task"], declared)) == ("Task",), (
        f"AC-C3 (`[bd10:6]`): the result is deduplicated; got "
        f"{tuple(capability_escapes(['Task', 'Read', 'Task'], declared))!r}"
    )

    # Positive control over ≥2 permitted heads, including the operand form.
    assert tuple(capability_escapes(["Read", "Bash", "Write"], declared)) == (), (
        f"AC-C3 positive control: three permitted heads (one matched through the "
        f"operand form) must yield no escapes; got "
        f"{tuple(capability_escapes(['Read', 'Bash', 'Write'], declared))!r}"
    )


@pytest.mark.parametrize("position", ["first", "last"])
def test_ac_c3_escape_at_chokepoint_errors_both_orderings(position) -> None:
    """AC-C3, the enforcement half — writable at last because `[bd10:6]` gave
    R3.6 an input channel: the adapter reports `observed_tools` in
    `StepResult.data`, and `_dispatch_backend` adjudicates it.

    A non-empty escape set yields `StepResult(status="error",
    error_code="E_CAPABILITY_ESCAPE", recoverable=False)`.

    Kills: a GREEN that computes the escapes and does not act on them; one
    that returns `recoverable=True`; one that reduces `observed_tools` with
    `first`/`last` (hence BOTH ORDERINGS — the escaping head is first of three
    in one parametrisation and last of three in the other); one that errors
    unconditionally (the positive control below is three permitted heads on
    the same adapter shape and must stay `ok`).

    Two measured outcomes on ONE fixture: the escaping sequence errors with
    that code, the permitted sequence on the identical call is `ok` — so the
    verdict is attributable to `observed_tools` alone.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    escaping = ["Task", "Read", "Bash"] if position == "first" else ["Read", "Bash", "Task"]

    register("bd10-rec", _RecordingAdapter("c3", data={"observed_tools": escaping}))
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=list(REAL_DECLARED_TOOLS), injections=None)
    )

    assert result.status == "error", (
        f"AC-C3 ({position}): an observed tool head outside the declaration must fail "
        f"the call; got status={result.status!r}"
    )
    assert result.error_code == E_CAPABILITY_ESCAPE, (
        f"AC-C3 ({position}): error_code must be {E_CAPABILITY_ESCAPE!r} "
        f"(CL:102 @ fd35e1304); got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC-C3 ({position}): E_CAPABILITY_ESCAPE is non-recoverable; got "
        f"{result.recoverable!r}"
    )

    # Positive control: same adapter shape, all heads inside the declaration.
    register("bd10-rec", _RecordingAdapter(
        "c3-ok", data={"observed_tools": ["Read", "Bash", "Write"]}
    ))
    set_run(log)
    control = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=list(REAL_DECLARED_TOOLS), injections=None)
    )
    assert control.status == "ok" and control.error_code is None, (
        f"AC-C3 positive control ({position}): ≥2 permitted heads must not error; got "
        f"status={control.status!r} error_code={control.error_code!r}"
    )


def test_ac_c3_absent_versus_empty_observed_tools() -> None:
    """AC-C3's third state, `[bd10:12]`: `observed_tools` ABSENT means the
    adapter CANNOT OBSERVE; an EMPTY sequence means it observed and saw
    nothing.  v3 makes the two distinguishable, so this is a real pair.

    ASSERTED THROUGH THE PAYLOAD AND THE VERDICT LABEL, NOT THE STATUS.  The
    returned status is `ok` for both and cannot tell them apart — asserting it
    on both halves would be one measurement written twice (§0.1), which is the
    defect v2 carried and `[bd10:12]` fixes.  The observables are
    `observed_tools` `null` versus `[]` in the event, and `"verdict:R3.6"`
    `not-checked` versus `passed` in the report.

    Kills: a GREEN that coerces the absent key to `[]` (both payloads then read
    `[]` and both verdicts read `passed`); one that coerces `[]` to `null` (the
    mirror); one that raises `KeyError` on the absent key; and — via the
    verdict — one that records the key correctly but aggregates
    "no invocation could observe" as `passed`, which would attest a check that
    never ran.

    Two measured outcomes on ONE fixture pair whose ONLY difference is that
    key: same declaration, same call, same adapter shape.  The declaration is
    the strictest one (`allowed_tools=[]`, where ANY reported head is an
    escape), so a GREEN that adjudicates an adapter which cannot observe shows
    up as an error rather than a silent mislabel.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    from conformance.bd_l3 import check_bd_l3

    register("bd10-rec", _RecordingAdapter("c3-blind"))  # no observed_tools key
    log_blind = _FakeEventLog()
    set_run(log_blind)
    blind = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=[], injections=None)
    )

    register("bd10-rec", _RecordingAdapter("c3-empty", data={"observed_tools": []}))
    log_empty = _FakeEventLog()
    set_run(log_empty)
    empty = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=[], injections=None)
    )

    assert blind.status == "ok" and blind.error_code is None, (
        f"AC-C3: an adapter that cannot observe must not be adjudicated, even under "
        f"an empty declaration; got status={blind.status!r} "
        f"error_code={blind.error_code!r}"
    )
    assert empty.status == "ok" and empty.error_code is None, (
        f"AC-C3: an adapter reporting zero heads has escaped nothing; got "
        f"status={empty.status!r} error_code={empty.error_code!r}"
    )

    assert len(log_blind.attests()) == 1 and len(log_empty.attests()) == 1, (
        f"AC-C3: both calls must be attested; got {len(log_blind.attests())} and "
        f"{len(log_empty.attests())}"
    )
    assert log_blind.attests()[0]["observed_tools"] is None, (
        f"AC-C3 (`[bd10:12]`): an adapter that reported nothing records `null`, NOT "
        f"[] — that null is what makes 'cannot observe' distinguishable from "
        f"'observed nothing'; got {log_blind.attests()[0]['observed_tools']!r}"
    )
    assert log_empty.attests()[0]["observed_tools"] == [], (
        f"AC-C3 (`[bd10:12]`): an adapter that reported an empty sequence records [], "
        f"NOT null; got {log_empty.attests()[0]['observed_tools']!r}"
    )

    blind_verdict = check_bd_l3(log_blind.attests()).labels.get(VERDICT_R36)
    empty_verdict = check_bd_l3(log_empty.attests()).labels.get(VERDICT_R36)
    assert blind_verdict == REQUIREMENT_NOT_CHECKED, (
        f"AC-C3 (`[bd10:13]`): a log in which NO invocation carried a non-null "
        f"observed_tools aggregates to {REQUIREMENT_NOT_CHECKED!r} for R3.6 — "
        f"reporting {REQUIREMENT_PASSED!r} would attest a check that never ran; got "
        f"{blind_verdict!r}"
    )
    assert empty_verdict == REQUIREMENT_PASSED, (
        f"AC-C3 (`[bd10:13]`): a log whose invocation DID observe, and escaped "
        f"nothing, aggregates to {REQUIREMENT_PASSED!r} for R3.6; got {empty_verdict!r}"
    )


def test_ac_c4_none_declaration_versus_empty_declaration() -> None:
    """AC-C4: `allowed_tools is None` ⇒ NO escape check runs and no error is
    possible — nothing can be outside a set that was never declared.
    `allowed_tools == []` ⇒ ANY observed tool use is an escape.

    Kills: a GREEN normalising `None` to `[]` (which would turn every
    undeclared step into a capability escape) and a GREEN treating `[]` as
    "unrestricted" (which would make an explicit no-tools declaration
    unenforceable).

    Two measured outcomes on ONE fixture: the SAME adapter reporting the SAME
    `observed_tools` is dispatched twice and the ONLY difference between the
    two calls is the declaration, so the differing verdicts are attributable
    to it alone.  Asserted at the chokepoint rather than on the pure function,
    because "no check runs" is a decision of the chokepoint — the function may
    never be called at all in the `None` case, and pinning it to accept `None`
    would over-specify a signature v2 does not pin.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    observed = ["Read", "Bash"]

    register("bd10-rec", _RecordingAdapter("c4-none", data={"observed_tools": observed}))
    log = _FakeEventLog()
    set_run(log)
    undeclared = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=None, injections=None)
    )

    register("bd10-rec", _RecordingAdapter("c4-empty", data={"observed_tools": observed}))
    set_run(log)
    empty_declaration = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=[], injections=None)
    )

    assert undeclared.status == "ok" and undeclared.error_code is None, (
        f"AC-C4: allowed_tools=None must run no check — nothing can be outside a set "
        f"that was never declared; got status={undeclared.status!r} "
        f"error_code={undeclared.error_code!r}"
    )
    assert empty_declaration.error_code == E_CAPABILITY_ESCAPE, (
        f"AC-C4: allowed_tools=[] makes every observed head an escape, on the SAME "
        f"observed_tools; got error_code={empty_declaration.error_code!r}"
    )


def test_ac_c5_capability_and_pin_codes_registered_and_drift_gate_clean() -> None:
    """AC-C5: `E_CAPABILITY_ESCAPE` and `E_MODEL_PIN_MISMATCH` are registered
    in `error_codes.ERROR_CODES` with trigger descriptions, and
    `error_codes.py --check` exits 0 on the real tree.

    Kills: a GREEN returning either code without registering it (which breaks
    the drift gate on `main`), and a GREEN registering a blank description.

    DECLARED PRE-PASSING (§0.6): the `--check == 0` half PASSES today, exactly
    as in AC-I4 — it is a shield over the existing gate, not a measurement of
    new behaviour.  Both membership halves FAIL today.
    """
    for code, citation in (
        (E_CAPABILITY_ESCAPE, "CL:102 @ fd35e1304 (R3.6)"),
        (E_MODEL_PIN_MISMATCH, "CL:99 @ fd35e1304 (R3.3)"),
    ):
        assert code in error_codes.ERROR_CODES, (
            f"AC-C5: {code!r} ({citation}) must be registered in error_codes.ERROR_CODES"
        )
        description = error_codes.ERROR_CODES[code]
        assert isinstance(description, str) and description.strip(), (
            f"AC-C5: {code!r} must carry a one-line trigger description; got "
            f"{description!r}"
        )
    # Pre-passing shield — declared above.
    assert error_codes.main(["--check"]) == 0, (
        "AC-C5: error_codes.py --check must exit 0 on the real tree"
    )


def test_ac_c6_argument_level_escape_is_not_detected_in_v1() -> None:
    """AC-C6: DECLARED LIMIT OF v1, recorded rather than discovered.  Only the
    tool HEAD is observable in the transcript block's `name`, so an
    argument-level escape WITHIN an allowed head — a `Bash` call outside
    `graphify-shim.sh` under `"Bash(graphify-shim.sh:*)"` — is NOT detected.

    Kills: a GREEN that silently starts matching operands (the boundary would
    then be undocumented and the `"R3.6": "tool-head-only"` label an
    overclaim in the other direction); and, via the second half, a GREEN whose
    head matching is broken outright.

    `[bd10:14]` states WHY, and keeps the two sides apart: the ADAPTER derives
    `observed_tools` by walking its own `tool_use` blocks and keeps only each
    block's `name`, so the operand never leaves the adapter; the CHECKER only
    ever sees the derived `Sequence[str]`.  The operand is therefore not merely
    unchecked — it is not representable in the evidence the chokepoint
    receives.  The characterisation records that boundary rather than leaving a
    reader to discover it.

    Two measured outcomes on ONE fixture: `["Bash"]` under a declaration that
    permits `Bash` only for one operand yields `()`, while `["Bash", "Write"]`
    against the same declaration yields `("Write",)` — so the `()` is the
    documented limit and not a dead checker.

    Pre-GREEN: `conformance.attest` does not exist (ImportError).
    """
    from conformance.attest import capability_escapes

    declared = ["Bash(graphify-shim.sh:*)"]

    assert tuple(capability_escapes(["Bash"], declared)) == (), (
        f"AC-C6 (characterisation): v1 admits any `Bash` operand under "
        f"'Bash(graphify-shim.sh:*)' — the operand never reaches the checker; got "
        f"{tuple(capability_escapes(['Bash'], declared))!r}"
    )
    assert tuple(capability_escapes(["Bash", "Write"], declared)) == ("Write",), (
        f"AC-C6: the checker is nonetheless live — a disallowed HEAD in the same "
        f"sequence is reported; got "
        f"{tuple(capability_escapes(['Bash', 'Write'], declared))!r}"
    )


# ---------------------------------------------------------------------------
# §7 — attestation: what may be claimed, and what may not
# ---------------------------------------------------------------------------

def _conformant_attest_payload() -> dict:
    """A payload with exactly AC-P1's NINE-key set (v3), used as checker input.

    Both observations are non-`null` and both are clean: `observed_model` is
    the same family as `model_requested`, and `observed_tools` holds one head
    inside `declared_capabilities`.  So the log describes an invocation for
    which R3.3 and R3.6 were both CHECKED and both passed — `[bd10:13]`'s
    `passed` branch, not its `not-checked` one.
    """
    return {
        "step_name": "invoke_bd10_llm",
        "backend": "bd10-rec",
        "model_requested": "sonnet",
        "prompt_sha256": sha256_of("BD10 PROMPT BODY"),
        "injections": [],
        "declared_capabilities": list(REAL_DECLARED_TOOLS),
        "capability_enforcement": "runtime-allowlist",
        "observed_model": "claude-sonnet-4-6",
        "observed_tools": ["Read"],
    }


def test_ac_a1_report_shape_requirements_labels_and_coercion() -> None:
    """AC-A1: `check_bd_l3(events)` returns an `L0Report`
    (conformance/report.py:14) whose `requirements` is exactly `REQUIREMENTS`
    and whose `labels` carry `"R3.1": "host-attested"` (CL §8, ADV-9 struck
    from the executable set) and `"R3.6": "tool-head-only"` (AC-C6).

    Kills: a checker returning a bare dict or tuple; one whose `requirements`
    drifts from R3.1-R3.6 (tuple equality pins order and membership); one that
    omits either label; one that hands back the caller's mutable containers.

    Constructed with MUTABLE containers per `[G22:23]` — the events are a
    `list` and the labels are asserted to have been coerced — so
    `L0Report.__post_init__`'s coercion (report.py:32-35) is actually
    exercised rather than bypassed by a pre-frozen input.  `REQUIREMENTS` is
    pinned here against CL:97-102 @ fd35e1304, not against the module's own
    export.

    Pre-GREEN: `conformance.bd_l3` does not exist (ImportError).
    """
    from conformance.report import L0Report
    from conformance.bd_l3 import REQUIREMENTS, check_bd_l3

    assert tuple(REQUIREMENTS) == REQUIREMENT_IDS, (
        f"AC-A1: REQUIREMENTS must be {REQUIREMENT_IDS!r} (CL:97-102 @ fd35e1304); "
        f"got {tuple(REQUIREMENTS)!r}"
    )

    events = [_conformant_attest_payload()]  # mutable container, [G22:23]
    report = check_bd_l3(events)

    assert isinstance(report, L0Report), (
        f"AC-A1: check_bd_l3 must return an L0Report; got {type(report).__name__}"
    )
    assert report.requirements == REQUIREMENT_IDS, (
        f"AC-A1: report.requirements must be exactly {REQUIREMENT_IDS!r}; got "
        f"{report.requirements!r}"
    )
    assert isinstance(report.requirements, tuple), (
        f"AC-A1: requirements must be coerced to an immutable tuple; got "
        f"{type(report.requirements).__name__}"
    )
    assert report.labels["R3.1"] == "host-attested", (
        f"AC-A1: labels['R3.1'] must be 'host-attested'; got "
        f"{report.labels.get('R3.1')!r}"
    )
    assert report.labels["R3.6"] == "tool-head-only", (
        f"AC-A1: labels['R3.6'] must be 'tool-head-only' (AC-C6's declared limit); "
        f"got {report.labels.get('R3.6')!r}"
    )
    # `[bd10:13]`: the qualifier label and the verdict label say DIFFERENT
    # things about the same requirement and must both remain readable.  A GREEN
    # that wrote the verdict under the bare "R3.6" key would destroy the
    # qualifier — the assertion above catches that, and this one catches the
    # mirror, a GREEN that never writes the prefixed key at all.
    assert report.labels.get(VERDICT_R36) == REQUIREMENT_PASSED, (
        f"AC-A1 (`[bd10:13]`): the fixture's one invocation observed a permitted head, "
        f"so {VERDICT_R36!r} aggregates to {REQUIREMENT_PASSED!r} — and it must live "
        f"under the prefixed key, alongside the separate 'R3.6' qualifier, not instead "
        f"of it; got {report.labels.get(VERDICT_R36)!r}"
    )
    with pytest.raises(TypeError):
        report.labels["R3.1"] = "wire-attested"  # type: ignore[index]


def test_ac_a2_adv9_recorded_not_executed() -> None:
    """AC-A2: ADV-9's status is `tokens.ADVERSARY_NOT_EXECUTED`, and a report
    that lists ADV-9 as executed is itself a failure — CL:221-224, "an
    implementation that quietly counts an unexecuted adversary as passed is
    itself a conformance failure".

    `[bd10:9]` v2 supplies the judge this clause was missing:
    `bd_l3.validate_report(report)` returns violation strings, empty when the
    report is well-formed.  BOTH directions therefore run through the
    MECHANISM rather than through this test's own opinion, which is the whole
    point of the addition.

    Kills: a checker that omits the `"ADV-9"` label; one that marks it
    executed or passed; and — the direction v1 could not express — a
    `validate_report` that rubber-stamps everything, since a report mutated to
    mark ADV-9 executed must come back with a NON-EMPTY violation tuple.

    Two measured outcomes on ONE fixture set: the produced report validates
    clean, and the same report reconstructed with `"ADV-9": "executed"` — one
    label changed, everything else identical — does not.  `"not_executed"` is
    pinned from conformance/tokens.py:10 (shipped by bd#22, external to this
    lot, §0.2); the underscore spelling is deliberate (tokens.py:1-5).

    Pre-GREEN: `conformance.bd_l3` does not exist (ImportError).
    """
    from conformance import tokens
    from conformance.report import L0Report
    from conformance.bd_l3 import check_bd_l3, validate_report

    assert tokens.ADVERSARY_NOT_EXECUTED == ADVERSARY_NOT_EXECUTED, (
        f"AC-A2: conformance/tokens.py:10 must still spell the token "
        f"{ADVERSARY_NOT_EXECUTED!r}; got {tokens.ADVERSARY_NOT_EXECUTED!r}"
    )

    report = check_bd_l3([_conformant_attest_payload()])

    assert report.labels.get("ADV-9") == ADVERSARY_NOT_EXECUTED, (
        f"AC-A2: the report must record ADV-9 under the literal 'ADV-9' label as "
        f"{ADVERSARY_NOT_EXECUTED!r}; got {report.labels.get('ADV-9')!r}"
    )
    assert tuple(validate_report(report)) == (), (
        f"AC-A2 (direction 1): a conformant report must validate clean; got "
        f"{tuple(validate_report(report))!r}"
    )

    # Direction 2: one label changed, nothing else.
    mutated_labels = dict(report.labels)
    mutated_labels["ADV-9"] = "executed"
    mutated = L0Report(
        passed=report.passed,
        requirements=list(report.requirements),
        violations=list(report.violations),
        labels=mutated_labels,
    )
    assert tuple(validate_report(mutated)) != (), (
        "AC-A2 (direction 2): a report marking ADV-9 executed must be rejected by "
        "validate_report — CL:221-224, quietly counting an unexecuted adversary as "
        "passed is itself a conformance failure; got an empty violation tuple"
    )


def test_ac_a3_no_export_grants_a_level() -> None:
    """AC-A3 / `[bd10:3]`: `check_bd_l3` REPORTS REQUIREMENTS; it does NOT
    grant BD-L3.  The level is cumulative (CL §2) and BD-L0/L1/L2 are not
    implemented on this base (bd#8, bd#9, bd#27 open), so a function returning
    "BD-L3 achieved" here would be the exact overclaim CL exists to prevent.

    Kills: a GREEN that adds a `grant_bd_l3()` / `attest_level()` export
    alongside the checker (the public-surface equalities below fail on any
    extra export), and a GREEN whose report scope creeps beyond R3.1-R3.6
    (the requirements equality fails if an L0/L1/L2 token appears).

    This is asserted against each module's runtime surface and the report's
    value, not against source text — a pattern-presence assertion would pass
    for a module that merely spells its grant differently.

    `[bd10:11]` **EXACT EQUALITY IS LOAD-BEARING; DO NOT WEAKEN IT TO A SUBSET
    CHECK.**  I raised this against my own AC-A3 and v2 closed it: §2.2 and
    §2.3's tables are normatively EXHAUSTIVE public surfaces, anything else
    GREEN needs is a module-private `_name`.  A later round "simplifying"
    these to `>=` would delete the only thing that detects an added export —
    and only the closedness of the tables makes the equality safe from the
    other side, i.e. from false-failing a correct GREEN.  Both modules are
    checked, because `[bd10:11]` pins both.

    Names re-exported from elsewhere (`L0Report`, `tokens`, typing aliases)
    are excluded BY ORIGIN, not by name, so a correct GREEN that imports its
    dependencies normally is not false-failed.

    Two measured outcomes on ONE fixture: the conformant modules expose
    exactly the pinned names and the report is scoped to R3.1-R3.6; adding one
    level-granting export flips the corresponding equality on the same import.

    Pre-GREEN: `conformance.bd_l3` does not exist (ImportError).
    """
    import types

    import conformance.attest as attest
    import conformance.bd_l3 as bd_l3

    def _public_surface(module) -> set:
        return {
            name for name, value in vars(module).items()
            if not name.startswith("_")
            and not isinstance(value, types.ModuleType)
            and getattr(value, "__module__", module.__name__) == module.__name__
        }

    bd_l3_public = _public_surface(bd_l3)
    assert bd_l3_public == {"REQUIREMENTS", "check_bd_l3", "validate_report"}, (
        f"AC-A3 (`[bd10:11]`): conformance.bd_l3 must expose exactly the three names "
        f"in AUTHORSHIP_SPEC.md §2.3 — no level-granting export (the harness lot lands "
        f"the grant, consuming this as one input among four); got {sorted(bd_l3_public)!r}"
    )

    attest_public = _public_surface(attest)
    assert attest_public == {
        "EVENT_TYPE", "hash_text", "InjectedBlock", "assemble", "capability_escapes",
    }, (
        f"AC-A3 (`[bd10:11]`): conformance.attest must expose exactly the five names in "
        f"AUTHORSHIP_SPEC.md §2.2; anything else GREEN needs is a module-private "
        f"`_name`; got {sorted(attest_public)!r}"
    )

    report = bd_l3.check_bd_l3([_conformant_attest_payload()])
    assert report.requirements == REQUIREMENT_IDS, (
        f"AC-A3: L0Report.passed is scoped to R3.1-R3.6 only; requirements were "
        f"{report.requirements!r}"
    )
    assert isinstance(report.passed, bool), (
        f"AC-A3: `passed` is a requirement-scoped boolean, not a level grant; got "
        f"{type(report.passed).__name__}"
    )
