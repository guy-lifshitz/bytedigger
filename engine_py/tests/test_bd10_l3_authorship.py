"""RED tests for bd#10 — BD-L3, attested authorship and inputs (R3.1-R3.6).

Frozen spec: engine_py/conformance/AUTHORSHIP_SPEC.md (FROZEN v4, base dc6f0d0).
Discipline inherited verbatim: EMISSIONS_SPEC.md §0.1-§0.6, CONTRACTS_SPEC.md
§0.1-§0.8, plus AUTHORSHIP_SPEC.md §0.1 (two measured outcomes on one fixture),
§0.2 (external provenance for every pinned constant) and §0.4 (the
`effective_prompt` naming collision).

COLLECTION SAFETY (§1q / D1CF5FDF).  `conformance.attest` and
`invoke_llm_subprocess(injections=...)` do NOT exist on this base.  Every
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

PRE-PASSING, DECLARED (§0.6).  Three half-assertions pass today; every other
assertion in this file fails pre-GREEN, and no TEST passes today in full:
  * AC-I4 and AC-C5's `error_codes.main(["--check"]) == 0` half — the existing
    drift gate is already clean on this tree (tests/test_gh1067_ignored_dir_
    exclusion.py:120 is its enforcement).  It is a shield: it pins that this
    lot's three new codes do not break the gate.  The ERROR_CODES-membership
    half of both ACs FAILS today.
  * AC-I6's byte-comparison half, BY CONSTRUCTION — it pins the PRE-migration
    bytes, so of course the pre-migration tree produces them.  That is what a
    fence is.  It is not a vacuum: it dies the moment a GREEN routes the role
    template through `assemble`, which is the exact wrong implementation
    `[bd10:16]` was written to stop.  The test as a whole still fails today, on
    the attestation half that ties those bytes to the emitted digest.

ALIGNED TO SPEC v4 (FROZEN, 26 ACs).  Two cuts left the lot entirely and this
file follows them exactly:

  * §7 — the checker, the attestation report and `[bd10:13]`'s verdict
    AGGREGATION — is bd#28.  `conformance.bd_l3` is not referenced anywhere in
    this file: no `check_bd_l3`, no `validate_report`, no `REQUIREMENTS`, no
    `L0Report`, no `verdict:`-prefixed labels, no ADV-9 status.  AC-A1, AC-A2
    and AC-A3 are deleted, not weakened.
  * §5's in-session fail-closed flip — the supersession of 220E5F63 — is
    bd#29.  Nothing here touches `_invoke_in_session`,
    `llm_subprocess.py:917-919` or `tests/test_2FDA949D_model_pin_warn.py`,
    and there is no AC-M6.

WHAT SURVIVED THE §7 CUT, AND WHY IT MATTERS (`[bd10:12]`, `[bd10:13]`).
AC-M1 and AC-C3 each measured their third state through BOTH the payload and a
verdict label.  The verdict half left with the report; **the payload half is
now the entire measurement and it is sufficient** — `observed_model is None`,
and `observed_tools` `null` versus `[]`, are two observable values on one
fixture pair whose only difference is the adapter's report.  The nine payload
keys are unchanged: bd#28 recomputes verdicts FROM them, so recording them is
this lot's obligation.  AC-M1 keeps its foreign-family liveness control.

ATTRIBUTION IS SEPARATED FROM ASSEMBLY (`[bd10:16]`, gate BLOCKING-5).  The
caller places its own text; it DECLARES each block through `injections`; the
chokepoint records `{source_id, sha256}` and VERIFIES the declared content
occurs in the assembled prompt.  Three consequences visible throughout this
file: AC-I5's role template stays PREPENDED where
`workflows/phase_2_explore.py:294-297` puts it; AC-I6 fences that with a
byte-level comparison; AC-I7 makes verification real by declaring a block whose
text is absent.  Every other fixture that declares a block therefore supplies a
prompt that actually CONTAINS the declared content — otherwise it would trip
AC-I7's rule and measure that instead of its own AC.

AC map (test function → AC), 26 ACs:
  AC-P1  test_ac_p1_exact_payload_key_set
  AC-P2  test_ac_p2_prompt_sha256_recomputed_by_the_test
  AC-P3  test_ac_p3_hash_covers_pre_hoist_text
  AC-P4  test_ac_p4_both_dispatch_branches_emit
  AC-P5  test_ac_p5_one_call_two_dispatches_two_events
  AC-P6  test_ac_p6_emission_failure_does_not_break_execution
  AC-P7  test_ac_p7_requirement_labels_pinned_exactly_and_immutable
  AC-P8  test_ac_p8_no_run_context_emits_nothing_and_does_not_raise
  AC-I1  test_ac_i1_assemble_order_and_separator
  AC-I2  test_ac_i2_injections_in_declaration_order
         test_ac_i2_injections_empty_for_none_and_for_empty_sequence
  AC-I3  test_ac_i3_unattributed_block_blocks_dispatch (6 params, both orderings)
  AC-I4  test_ac_i4_inject_unattributed_registered_and_drift_gate_clean
  AC-I5  test_ac_i5_phase_2_explore_role_template_declared_through_injections
  AC-I6  test_ac_i6_migrated_phase_prompt_is_byte_identical
  AC-I7  test_ac_i7_declared_block_absent_from_prompt_is_unattributed
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

ONE SPEC RESIDUE, FLAGGED AND NOT GUESSED AT.  §2.4 still carries its v3
sentence "blocks are assembled into `prompt` before dispatch", which v4's §4
`[bd10:16]` supersedes ("the caller places its own text… VERIFIES each declared
block's `content` occurs in the assembled prompt").  The two cannot both hold:
if the chokepoint assembled the blocks itself, AC-I7's verification could never
fail and AC-I6's byte comparison would break.  These tests follow §4, which is
the clause v4 rewrote; §2.4's sentence is stale wording, not a second reading.
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

# CL:98 / CL:99 / CL:102 @ HAL fd35e1304 (R3.2 / R3.3 / R3.6).  NOT imported
# from error_codes.ERROR_CODES: this lot authors those entries (§0.2).
E_INJECT_UNATTRIBUTED = "E_INJECT_UNATTRIBUTED"
E_MODEL_PIN_MISMATCH = "E_MODEL_PIN_MISMATCH"
E_CAPABILITY_ESCAPE = "E_CAPABILITY_ESCAPE"

# CL:97-102 @ fd35e1304 — the six requirement ids AC-P7's mapping is keyed on.
REQUIREMENT_IDS = ("R3.1", "R3.2", "R3.3", "R3.4", "R3.5", "R3.6")

# AUTHORSHIP_SPEC.md §3 AC-P7 / `[bd10:23]` — the honesty qualifiers, one per
# narrowing this lot knows about, each with its own justification in the spec's
# table (CL §8 for R3.1, `[bd10:19]` for R3.2 and R3.5, `[bd10:2]` for R3.3,
# AC-C6 for R3.6).  R3.4 is DELIBERATELY unlabelled: it is recorded verbatim
# (AC-C1) with nothing narrowed, and exact-mapping equality is what makes that
# absence an assertion rather than an omission.
REQUIREMENT_LABELS = {
    "R3.1": "host-attested",
    "R3.2": "injections-channel-only",
    "R3.3": "in-session-warn-only",
    "R3.5": "adapter-declared",
    "R3.6": "tool-head-only",
}

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

    # `[bd10:16]`: the CALLER places the block's text; the chokepoint verifies
    # the declared content OCCURS in the prompt (AC-I7).  So this fixture must
    # supply a prompt that really contains it, or it would measure AC-I7's rule
    # instead of AC-P1's key set.
    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(
            prompt="BD10 PROMPT BODY\n\nP1 BLOCK",
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


def test_ac_p7_requirement_labels_pinned_exactly_and_immutable() -> None:
    """AC-P7 / `[bd10:23]`: `conformance.attest.REQUIREMENT_LABELS` is the
    record of every narrowing this lot knows about, pinned by EXACT MAPPING
    EQUALITY and immutable.

    The labels live in the SEAM, not in the report, which is what let them
    survive §7's cut to bd#28: the engine RECORDS what it can honestly claim,
    the checker READS it.

    Kills, and exact equality is load-bearing in BOTH directions: a GREEN that
    OMITS an entry — most plausibly `R3.3`, the one that admits an open hole
    (the in-session path still warns; bd#29 owns the flip) — makes the
    attestation silently read as unnarrowed; a GREEN that INVENTS an entry,
    e.g. labelling R3.4, claims a narrowing that does not exist.  A subset
    check would let the first through and a superset check the second, so
    neither is used.  The immutability half kills a plain-`dict` GREEN, under
    which any caller could edit the record of what we claim after the fact.

    Two measured outcomes on ONE fixture: the pinned mapping matches; the same
    mapping with any single entry dropped or added flips the same equality.
    The five values come from AUTHORSHIP_SPEC.md §3 AC-P7's table, each with
    its own external justification (CL §8, `[bd10:19]`, `[bd10:2]`, AC-C6), and
    the keys from CL:97-102 @ fd35e1304 — not from the module under test.

    Pre-GREEN: `conformance.attest` does not exist (ImportError).
    """
    from conformance.attest import REQUIREMENT_LABELS as GREEN_LABELS

    # Provenance sanity on the TEST's own constant: every key is a CL
    # requirement id, and R3.4 is the one deliberately absent (it is recorded
    # verbatim by AC-C1 with nothing narrowed).
    assert set(REQUIREMENT_LABELS) == set(REQUIREMENT_IDS) - {"R3.4"}, (
        "fixture sanity: the pinned label keys must be CL:97-102's requirement "
        "ids minus R3.4"
    )

    assert dict(GREEN_LABELS) == REQUIREMENT_LABELS, (
        f"AC-P7: REQUIREMENT_LABELS must equal {REQUIREMENT_LABELS!r} EXACTLY — an "
        f"omitted label reads as an unnarrowed requirement and an invented one is a "
        f"claim we cannot support; got {dict(GREEN_LABELS)!r} "
        f"(missing={sorted(set(REQUIREMENT_LABELS) - set(GREEN_LABELS))!r}, "
        f"extra={sorted(set(GREEN_LABELS) - set(REQUIREMENT_LABELS))!r})"
    )

    with pytest.raises(TypeError):
        GREEN_LABELS["R3.3"] = "enforced"  # type: ignore[index]


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

    # `[bd10:16]`: the caller places the text, so the prompt must really carry
    # all three declared contents (AC-I7's rule), and it carries them in an
    # order that is NOT the declaration order — the recorded order must come
    # from the declaration, never from where the bytes happen to sit.
    llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(
            prompt="CHARLIE CONTENT\n\nALPHA CONTENT\n\nBRAVO CONTENT",
            injections=blocks,
        )
    )

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

    # Every content below really occurs in `i3_prompt`, so the positive control
    # dispatches on the attribution rule alone and never on AC-I7's occurrence
    # rule (`[bd10:16]`).
    i3_prompt = "ALPHA BODY\n\nBRAVO BODY\n\nOFFENDER BODY"
    good_a = InjectedBlock(source_id="src-good-a", content="ALPHA BODY")
    good_b = InjectedBlock(source_id="src-good-b", content="BRAVO BODY")
    offender = InjectedBlock(source_id=bad_source_id, content="OFFENDER BODY")
    repaired = InjectedBlock(source_id="src-repaired", content="OFFENDER BODY")

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

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(prompt=i3_prompt, injections=blocks)
    )

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
        **invoke_kwargs(prompt=i3_prompt, injections=control_blocks)
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


def test_ac_i5_phase_2_explore_role_template_declared_through_injections(tmp_path) -> None:
    """AC-I5 (v4, `[bd10:16]`): `phase_2_explore` DECLARES its role-template
    block through the `injections` channel with `source_id` equal to the
    resolved role-template path, so ADV-8 tests a door the pipeline actually
    uses.

    ATTRIBUTION IS SEPARATED FROM ASSEMBLY.  The phase keeps placing the role
    template where `workflows/phase_2_explore.py:294-297` puts it — PREPENDED,
    exactly as `:22` documents — and declares it; the chokepoint records
    `{source_id, sha256}` and verifies the declared bytes occur in the prompt.
    This AC does NOT route that site through `assemble`, and AC-I6 is the fence
    that keeps a GREEN from doing so anyway.

    Kills: a GREEN that adds the `injections` parameter but migrates no real
    call site (the attributed channel would then be dead code); a GREEN that
    declares the block but attributes it to a constant like "role_template"
    instead of the resolved path; a GREEN that digests the wrong text.

    Two measured outcomes on ONE fixture: with the phase migrated, the emitted
    event's `injections` equals the single record below, recomputed here from
    the file this test wrote; with the phase left on undeclared string
    concatenation the same run emits `injections == []`.

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


def _explore_ctx(*, scratchpad, role_path, session_id: str) -> WorkflowContext:
    """One explore fixture, parameterised ONLY by whether a role template is
    configured — everything else (scratchpad, question, persona) is identical,
    which is what makes AC-I6's byte comparison attributable to the role
    template alone."""
    org_config = {"scratchpad_dir": str(scratchpad), "model": "sonnet"}
    if role_path is not None:
        org_config["role_template_path"] = str(role_path)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question="bd10 AC-I6 feature request",
        session_id=session_id,
        persona="hal",
        framework=None,
        domain=None,
    )


def test_ac_i6_migrated_phase_prompt_is_byte_identical(tmp_path) -> None:
    """AC-I6 `[bd10:16]` (gate BLOCKING-5): the migrated phase's assembled
    prompt is BYTE-IDENTICAL to the pre-migration prompt.  This is the fence
    that makes AC-I5 a MIGRATION rather than a rewrite.

    v3's AC-I1 pinned `assemble` to APPEND blocks after the caller's prompt,
    while `workflows/phase_2_explore.py:22` documents the role template as
    "Prepended to prompt" and `:294-297` implements it.  A GREEN that migrated
    AC-I5's site through `assemble` would move ~3 KB of role framing from the
    HEAD of a live production prompt to its TAIL, behind the output schema and
    the out-of-role block — and `tests/test_phase_2_explore.py:303` asserts the
    role text's PRESENCE, not its position, so the existing suite would report
    a delta of zero.  This test is the instrument that does not.

    Kills: exactly that GREEN.  The pre-migration composition rule is measured
    HERE, on this base, rather than quoted: the same phase is driven twice on
    ONE fixture whose ONLY difference is `role_template_path`, and the pinned
    property is `with_role == role.rstrip() + "\\n\\n" + without_role` — the
    byte-level statement of ":294-297 prepends".  An `assemble`-routing GREEN
    produces `without_role + "\\n\\n" + role…` and fails.

    Two measured outcomes on ONE fixture: the byte comparison above, and the
    emitted `prompt_sha256`, recomputed here over the byte string THIS TEST
    composed from its own role file and the baseline run (§0.1 subtype 3 — the
    expected digest is never read back out of the event, nor taken from the
    adapter's kwargs).  The second half is what ties the bytes to the
    attestation and what fails pre-GREEN; the first half is declared
    pre-passing in the module docstring, by construction, because a fence pins
    the bytes that already exist.
    """
    scratchpad = tmp_path / "scratch"
    role_path = tmp_path / "role.md"
    role_file_text = "AC-I6 ROLE HEAD\nAC-I6 ROLE TAIL\n  \n"
    role_path.write_text(role_file_text, encoding="utf-8")
    expected_content = role_file_text.rstrip() + "\n\n"

    from phase_2_explore import phase_2_explore_workflow

    adapter = _RecordingAdapter("i6", data={"raw_response": "findings\n\nSTATUS: DONE\n"})
    register("claude-subprocess", adapter,
             capabilities=("manifest", "progress_since", "abort"))

    log = _FakeEventLog()
    engine = WorkflowEngine(event_log=log)
    engine.register("p2", phase_2_explore_workflow())
    with_role, _ = engine.execute("p2", _explore_ctx(
        scratchpad=scratchpad, role_path=role_path, session_id="bd10-i6-role"))

    baseline_log = _FakeEventLog()
    baseline_engine = WorkflowEngine(event_log=baseline_log)
    baseline_engine.register("p2", phase_2_explore_workflow())
    without_role, _ = baseline_engine.execute("p2", _explore_ctx(
        scratchpad=scratchpad, role_path=None, session_id="bd10-i6-plain"))

    # Fixture preconditions, NOT the AC: both runs must reach dispatch, so a
    # downstream contract break can never be mistaken for this RED.
    assert with_role.status == "ok" and without_role.status == "ok", (
        f"AC-I6 precondition: both explore runs must complete; got "
        f"{with_role.status!r} and {without_role.status!r} — fixture failure, not a "
        f"measurement of R3.2"
    )
    assert len(adapter.calls) == 2, (
        f"AC-I6 precondition: the same adapter must have seen both prompts; got "
        f"{len(adapter.calls)} call(s)"
    )

    role_prompt = adapter.calls[0]["prompt"]
    plain_prompt = adapter.calls[1]["prompt"]
    assert role_prompt != plain_prompt, (
        "fixture sanity: the role template must actually change the prompt, "
        "otherwise the byte comparison below cannot distinguish head from tail"
    )
    assert role_prompt == expected_content + plain_prompt, (
        "AC-I6 (`[bd10:16]`): the migrated phase's prompt must be BYTE-IDENTICAL to "
        "the pre-migration one — the role template PREPENDED, per "
        "workflows/phase_2_explore.py:22,294-297.  A GREEN routing it through "
        "`assemble` moves it to the tail.  Head of what we got: "
        f"{role_prompt[:120]!r}"
    )

    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-I6: the migrated dispatch must emit exactly one {EVENT_TYPE!r} event; "
        f"got {len(attests)}"
    )
    assert attests[0]["prompt_sha256"] == sha256_of(expected_content + plain_prompt), (
        "AC-I6: the attested digest must cover the byte string this test composed "
        "from its own role file plus the baseline prompt — so the bytes the fence "
        "pins and the bytes R3.1 hashes are the same bytes; got "
        f"{attests[0]['prompt_sha256']!r}"
    )


def test_ac_i7_declared_block_absent_from_prompt_is_unattributed() -> None:
    """AC-I7 `[bd10:16]`: a declared block whose `content` does NOT occur in
    the assembled prompt yields `E_INJECT_UNATTRIBUTED` and NO dispatch.

    This is the clause that stops declaration being an honour system.  Once
    attribution is separated from assembly (§4, v4), the caller places its own
    text and merely TELLS the engine what it placed — so without verification a
    phase could declare a block it never wrote, and the attestation would carry
    a `{source_id, sha256}` record corresponding to no bytes the model ever
    read.

    Kills: a GREEN that records declarations without verifying them (returns
    `ok`, dispatches once); a GREEN that verifies but proceeds anyway
    (dispatch count 1); a GREEN that returns `recoverable=True`; a GREEN that
    verifies by comparing digests of the WHOLE prompt rather than looking for
    the block's bytes inside it (the positive control's block is a strict
    substring, never the whole prompt).

    "No dispatch occurred" is asserted POSITIVELY by the recording adapter's
    call count, and the positive control proves that counter can reach 1 on
    this very adapter (§0.1 subtype 1).

    Two measured outcomes on ONE fixture: the SAME prompt and the SAME
    `source_id` are used twice, and the ONLY difference is whether the declared
    `content` is a substring of that prompt — absent → error with call count 0,
    present → `ok` with call count 1.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
    from conformance.attest import InjectedBlock

    prompt = "AC-I7 PROMPT HEAD\n\nPRESENT BLOCK BODY\n\nAC-I7 PROMPT TAIL"
    absent = InjectedBlock(source_id="src-i7", content="ABSENT BLOCK BODY")
    present = InjectedBlock(source_id="src-i7", content="PRESENT BLOCK BODY")
    assert absent.content not in prompt and present.content in prompt, (
        "fixture sanity: the two blocks must differ exactly in whether their "
        "content occurs in the prompt"
    )

    adapter = _RecordingAdapter("i7")
    register("bd10-rec", adapter)
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(prompt=prompt, injections=(absent,))
    )

    assert result.status == "error", (
        f"AC-I7: a block declared but not present in the prompt must fail the call; "
        f"got status={result.status!r}"
    )
    assert result.error_code == E_INJECT_UNATTRIBUTED, (
        f"AC-I7: error_code must be {E_INJECT_UNATTRIBUTED!r} (CL:98 @ fd35e1304); "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC-I7: E_INJECT_UNATTRIBUTED is non-recoverable; got "
        f"recoverable={result.recoverable!r}"
    )
    assert len(adapter.calls) == 0, (
        f"AC-I7: NO dispatch may occur — an unverifiable declaration must fail "
        f"CLOSED; the adapter recorded {len(adapter.calls)} call(s)"
    )

    # Positive control on the SAME fixture: identical block, text present.
    control = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(prompt=prompt, injections=(present,))
    )
    assert control.status == "ok" and control.error_code is None, (
        f"AC-I7 positive control: a declaration whose bytes ARE in the prompt must "
        f"dispatch; got status={control.status!r} error_code={control.error_code!r}"
    )
    assert len(adapter.calls) == 1, (
        f"AC-I7 positive control: the adapter must have been called exactly once in "
        f"total; got {len(adapter.calls)}"
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

    THE PAYLOAD IS NOW THE WHOLE MEASUREMENT (§7's cut).  v3 read this third
    state twice — once in the payload and once as `"verdict:R3.3"` in the
    report — and the report half left with bd#28.  What remains is sufficient
    and is the side of the split this lot owns (`[bd10:12]`): `null` versus a
    substituted model id are two observable values on one fixture, and the
    aggregation bd#28 performs is a function OF this record, so a lot that
    records it wrongly cannot be rescued downstream.

    A second call proves the comparison is live on this fixture at all — an
    adapter reporting a FOREIGN family errors — so the `ok` above cannot come
    from the check being skipped entirely.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
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

    Kills: a GREEN that warns and proceeds at the CHOKEPOINT; a GREEN that
    errors but drops the two diagnostic fields; a GREEN that errors on EVERY
    invocation (the positive control catches that).

    `[bd10:2]`: the in-session path (llm_subprocess.py:917-919) is OUT OF SCOPE
    — the supersession of 220E5F63 is withdrawn to bd#29, and the residual gap
    is labelled rather than implied (`REQUIREMENT_LABELS["R3.3"] ==
    "in-session-warn-only"`, AC-P7).  This fixture reaches the chokepoint
    through a registered adapter and touches nothing in-session.

    Two measured outcomes on ONE fixture shape: the haiku-reporting adapter
    errors, the sonnet-reporting adapter on the identical call is `ok`.  No
    tier is active here, so the dispatched request model and the caller's
    argument coincide and `pinned_model` is unambiguous.

    A FAILED DISPATCH IS STILL ATTESTED, and that assertion comes FIRST.  The
    wrong GREEN this kills is the one that EARLY-RETURNS on the pin check
    BEFORE reaching `_emit_safe`: it satisfies every status/error_code
    assertion in this file while the attestation log ends up containing only
    the invocations where nothing went wrong.  bd#28's aggregation
    (`[bd10:13]` — `failed` if any invocation recorded a violation) could then
    never return `failed`, and an oracle whose rejection is impossible is green
    always.  §7 moved the ORACLE to bd#28; the obligation to produce its
    EVIDENCE stayed here.  Ordering the assertion first is also the semantics:
    a dispatch that OCCURRED is attested regardless of what the checker later
    decides about it.

    Both outcomes on ONE fixture: the emitting GREEN produces exactly one
    attestation carrying this call's `step_name` (with the error result); the
    early-returning GREEN produces ZERO on the identical fixture.

    NO `injections` OVERRIDE.  Passing `injections=None` would raise TypeError
    pre-GREEN before any assert ran, making the attestation assertion dead —
    and R3.2's kwarg is not what this AC measures.  Without it the call really
    dispatches today, so this assertion fails pre-GREEN on exactly the property
    it measures: the dispatch happens, no attestation is emitted.
    """
    drifting = _RecordingAdapter(
        "m2-drift", data={"observed_model": "claude-haiku-4-5-20251001"}
    )
    register("bd10-rec", drifting)
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(model="sonnet"))

    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-M2: a dispatch the chokepoint FAILED must still be attested — exactly "
        f"one {EVENT_TYPE!r} event.  A GREEN early-returning on the pin check before "
        f"`_emit_safe` logs only the clean invocations, which makes bd#28's "
        f"`failed` verdict (`[bd10:13]`) unreachable; got {len(attests)} "
        f"(all events: {log.types()!r})"
    )
    assert attests[0]["step_name"] == "invoke_bd10_llm", (
        f"AC-M2: the attestation must be THIS dispatch's — step_name "
        f"'invoke_bd10_llm', so a stray event from elsewhere cannot satisfy the "
        f"count above; got {attests[0]['step_name']!r}"
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
    control = llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(model="sonnet"))
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
    """AC-M5 (v5): a `model_pin_mismatch` telemetry event is emitted for a
    CHOKEPOINT-detected mismatch, its payload carries `chokepoint: true`, AND
    the step ends `status == "error"`.  All three on ONE fixture — one
    dispatch, one measurement — so the AC cannot be discharged by any half
    alone.

    `[bd10:26]` (gate round 2, M-2) THE WORD "ADDITIVITY" IS WITHDRAWN AND THE
    DISCRIMINATOR REPLACES IT.  v4 said the event was "still" emitted, which
    measurement disproved: on the chokepoint path it is not written at all
    today, and the tree's only production writer is llm_subprocess.py:919 — the
    in-session path this lot declared untouchable (`[bd10:2]`, bd#29 owns the
    flip).  So this lot adds a NEW writer of an EXISTING event type with a
    DIFFERENT meaning: until now the event meant "we warned and continued", and
    it will now also mean "the step failed".

    `chokepoint is True` is what kills a GREEN that reuses the event type
    WITHOUT a discriminator, under which the two meanings are indistinguishable
    to any consumer reading the type alone — the same overclaim-by-omission
    this lot exists to remove, arriving through a telemetry payload instead of
    an attestation label.  Asserted by IDENTITY against `True`, per-field per
    §0.5: a truthy stand-in such as `"yes"` or `1` is out of contract and must
    fail here.  The `:919` writer stays untouched and keeps emitting WITHOUT
    the key, which is precisely what makes the discriminator meaningful.

    Kills, additionally: a GREEN that errors and drops the event; a GREEN that
    emits the event and leaves the step `ok`.

    Two measured outcomes on ONE fixture: intact, the event is present with
    `chokepoint` `True` and the status is `error`; with the emit deleted the
    same fixture fails the first half, with the discriminator omitted (or
    written as a truthy non-`True`) it fails the second, and with the flip
    reverted it fails the third.

    THE TELEMETRY HALVES COME FIRST, AND THERE IS NO `injections` OVERRIDE.
    Passing `injections=None` would raise TypeError pre-GREEN before any assert
    ran, which would leave the `chokepoint` discriminator declared and
    unmeasured — the defect this very clause was added to close.  R3.2's kwarg
    is not what AC-M5 measures; `invoke_kwargs` does not carry it by default,
    so without the override the call really reaches `_dispatch_backend` and
    dispatches to the registered adapter today.  This event is not written on
    the chokepoint path at all on this base, so the existence half fails
    pre-GREEN on exactly the property it measures.
    """
    register("bd10-rec", _RecordingAdapter(
        "m5", data={"observed_model": "claude-haiku-4-5-20251001"}
    ))
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(**invoke_kwargs(model="sonnet"))

    mismatch = log.payloads("model_pin_mismatch")
    assert len(mismatch) >= 1, (
        f"AC-M5: a 'model_pin_mismatch' event must be emitted for a "
        f"chokepoint-detected mismatch; events seen: {log.types()!r}"
    )
    assert mismatch[0].get("chokepoint") is True, (
        f"AC-M5 (`[bd10:26]`): the payload must carry chokepoint=True by IDENTITY — "
        f"without the discriminator a consumer cannot tell this lot's 'the step "
        f"failed' from llm_subprocess.py:919's 'we warned and continued', and a "
        f"truthy stand-in is out of contract (§0.5); got "
        f"{mismatch[0].get('chokepoint')!r}"
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

    A FAILED DISPATCH IS STILL ATTESTED, and that assertion comes FIRST.  The
    wrong GREEN this kills is the one that EARLY-RETURNS on the escape check
    BEFORE reaching `_emit_safe`: it satisfies every status/error_code
    assertion here while the attestation log retains only the invocations where
    no escape occurred.  bd#28's aggregation (`[bd10:13]` — `failed` if any
    invocation recorded a violation) could then never return `failed`, and an
    oracle whose rejection is impossible is green always.  §7 moved the ORACLE
    to bd#28; the obligation to produce its EVIDENCE stayed here.  Ordering the
    assertion first is also the semantics: a dispatch that OCCURRED is attested
    regardless of what the checker later decides about it.

    Both outcomes on ONE fixture: the emitting GREEN produces exactly one
    attestation carrying this call's `step_name` (with the error result); the
    early-returning GREEN produces ZERO on the identical fixture.

    NO `injections` OVERRIDE.  Passing `injections=None` would raise TypeError
    pre-GREEN before any assert ran, making the attestation assertion dead —
    and R3.2's kwarg is not what this AC measures.  Without it the call really
    dispatches today, so this assertion fails pre-GREEN on exactly the property
    it measures: the dispatch happens, no attestation is emitted.
    """
    escaping = ["Task", "Read", "Bash"] if position == "first" else ["Read", "Bash", "Task"]

    register("bd10-rec", _RecordingAdapter("c3", data={"observed_tools": escaping}))
    log = _FakeEventLog()
    set_run(log)

    result = llm_subprocess.invoke_llm_subprocess(
        **invoke_kwargs(allowed_tools=list(REAL_DECLARED_TOOLS))
    )

    attests = log.attests()
    assert len(attests) == 1, (
        f"AC-C3 ({position}): a dispatch the chokepoint FAILED must still be "
        f"attested — exactly one {EVENT_TYPE!r} event.  A GREEN early-returning on "
        f"the escape check before `_emit_safe` logs only the clean invocations, "
        f"which makes bd#28's `failed` verdict (`[bd10:13]`) unreachable; got "
        f"{len(attests)} (all events: {log.types()!r})"
    )
    assert attests[0]["step_name"] == "invoke_bd10_llm", (
        f"AC-C3 ({position}): the attestation must be THIS dispatch's — step_name "
        f"'invoke_bd10_llm', so a stray event from elsewhere cannot satisfy the "
        f"count above; got {attests[0]['step_name']!r}"
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
        **invoke_kwargs(allowed_tools=list(REAL_DECLARED_TOOLS))
    )
    assert control.status == "ok" and control.error_code is None, (
        f"AC-C3 positive control ({position}): ≥2 permitted heads must not error; got "
        f"status={control.status!r} error_code={control.error_code!r}"
    )


def test_ac_c3_absent_versus_empty_observed_tools() -> None:
    """AC-C3's third state, `[bd10:12]`: `observed_tools` ABSENT means the
    adapter CANNOT OBSERVE; an EMPTY sequence means it observed and saw
    nothing.  v3 makes the two distinguishable, so this is a real pair.

    ASSERTED THROUGH THE PAYLOAD, NOT THE STATUS.  The returned status is `ok`
    for both and cannot tell them apart — asserting it on both halves would be
    one measurement written twice (§0.1), which is the defect v2 carried and
    `[bd10:12]` fixes.  The observable is `observed_tools` `null` versus `[]`
    in the event.

    THE VERDICT HALF LEFT WITH THE REPORT (§7 → bd#28) AND THE PAYLOAD HALF IS
    SUFFICIENT: `null` and `[]` are two observable values on one fixture pair
    whose ONLY difference is that key, which was the entire point of
    `[bd10:12]` — the observable had to exist somewhere, and the payload is the
    side of the split this lot owns.  bd#28 aggregates FROM this record, so a
    lot that flattens the two states here cannot be repaired downstream.

    Kills: a GREEN that coerces the absent key to `[]` (both payloads then read
    `[]`, and bd#28 would later attest `passed` for a check that never ran);
    one that coerces `[]` to `null` (the mirror); one that raises `KeyError` on
    the absent key.

    Two measured outcomes on ONE fixture pair whose ONLY difference is that
    key: same declaration, same call, same adapter shape.  The declaration is
    the strictest one (`allowed_tools=[]`, where ANY reported head is an
    escape), so a GREEN that adjudicates an adapter which cannot observe shows
    up as an error rather than a silent mislabel.

    Pre-GREEN: `injections` is not a parameter, so the call raises TypeError.
    """
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
