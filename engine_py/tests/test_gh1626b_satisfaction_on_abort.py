"""RED — GH1626 part B: a phase-6 abort must not strand phase 7 forever.

Spec: SHARED/memory/Decisions/2026-08-12_gh1626B_satisfaction_doc_on_abort_spec.md
Class: MISSING-RECOVERY-PATH. Chokepoint: ``WorkflowDefinition.error_handler``
(contracts.py:317 — declared, zero production consumers today).

What is under test:
  - AC1/AC2/AC3 — phase 6 aborts before step 20 (``write_satisfaction_doc``)
    must still leave a truthful ``reviews/build-satisfaction.md`` on disk
    (§1l: asserted on the REAL file, driven through the REAL production entry
    point ``lib.phase_sentinel.execute_native_workflow``, never a mocked UUT).
  - AC4/AC5 — the EXECUTOR wiring of the dead hook: it fires, it fires on both
    execution paths (§1u), and it may enrich but never rescue.
  - AC6 — phase 7 tells the truth in three states. (AC7/AC8, the
    ``E_SYNTHESIZER_NEEDS_CONTEXT`` cases, were retired by bd#89 P3c.)

Round 2 (gate round 1 REJECTED, two MAJOR findings — the hook is now
SIDE-EFFECT-ONLY: return value discarded, exceptions contained):
  - AC9  (MAJOR-1) — a handler that RAISES cannot turn an abort into a crash.
  - AC10 (MAJOR-2) — a handler returning a DIFFERENT still-``error`` result
    cannot re-code the abort; ``recoverable is False`` is load-bearing at
    engine.py:313 (stuck-report).
  - AC11 — exactly one handler call per phase TERMINAL outcome, across both
    retry mechanisms (``step()``+RetryPolicy inner loop, and the engine's
    ``retry_from_step`` recursion at engine.py:639-658).
  - AC12 — a STALE stub from a previous abort is refreshed; refresh keys on the
    ``SATISFACTION: NOT_ASSESSED`` marker THIS lot emits (round 3, MAJOR-4),
    never on a guess about what someone else wrote.
  - AC13 — the phase-7 REPORT lists a NOT_ASSESSED satisfaction doc as a gap.

Round 3 (gate round 2 REJECTED, two MAJOR findings):
  - AC14 (MAJOR-3, aliasing) — ``StepResult`` is an UNFROZEN dataclass
    (contracts.py:64-92), so discarding the handler's RETURN value closes only
    half the door: the handler holds the LIVE object engine.py:313 reads. The
    executor must hand it ``dataclasses.replace(result)``.
  - AC15/AC16 (MAJOR-4a/b, predicate) — ``SCORE:``/``VERDICT:`` does not
    identify a real assessment: the COMPLEX composite the engine renders itself
    (phase_6_review.py:3507-3522) has neither, and the single-evaluator doc is
    raw LLM stdout parsed IGNORECASE (:535), so ``**Score: 62**`` is real. The
    predicate is a WHITELIST on our own marker.
  - AC17 (MAJOR-4c, ordering) — the doc is written at :3187 BEFORE the gate at
    :3152-3157/:3314, so a spec-faithful-but-blacklisting GREEN would overwrite
    a REAL FAILING assessment with a stub on E_SATISFACTION_BELOW_THRESHOLD.
  - AC18 (§1o) — the handler-raised warning event has ONE canonical name.

Round 4 (gate round 3 APPROVED with conditions):
  - AC19 (condition 1) — AC14 forces the OUTCOME, not the MECHANISM: a GREEN
    that calls the handler on the live result early in ``execute`` and returns a
    pre-call snapshot passes AC14 while engine.py:304/:313 still read the
    mutated object. AC19 asserts the STUCK REPORT written by engine.py:313-326
    still names ``E_GH1626B_BOOM`` (and exists at all, which IS the observable
    for ``recoverable is False``).
  - AC9 narrowed (condition 3) — its event predicate is the exact canonical name
    ``phase_error_handler_raised``, no longer any event_type containing
    "handler".

Harness: reuses the established engine_py seams — conftest's import-time
sys.path singleton (no module-level sys.path mutation here), the
``register_backend`` LLM stub pattern of tests/test_phase_6_review.py and
tests/test_phase_7_synthesize.py, and the ``workflows.register_all`` wrap of
tests/test_gh612_phase_sentinel_seam.py.

§1q: nothing that GREEN introduces is imported at module import time —
``_on_phase_6_abort`` is never imported here at all; every failure lands at
assert time, not at collection.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from os.path import realpath
from pathlib import Path
from typing import Any

import pytest

from bytedigger_engine import llm_subprocess as _llm_mod
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import (
    RetryPolicy,
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
    step,
)
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import register_backend
from bytedigger_engine.workflows.phase_6_review import SATISFACTION_DOC_RELPATH, phase_6_review_workflow
from bytedigger_engine.workflows.phase_7_synthesize import (
    REPORT_DOC_RELPATH,
    phase_7_synthesize_workflow,
)

_BACKEND_NAME = "gh1626b-stub"


# ─── isolation (mirrors test_phase_6_review / test_phase_7_synthesize) ────────


@pytest.fixture(autouse=True)
def _gh1626b_isolation(monkeypatch, tmp_path):
    """§1i: neutralise the resolver disk-write seam; pin the durable backend env
    to a known state; clear ALL telemetry_ctx run state on both edges.

    Round-2 MINOR: the executors this file drives call ``set_current_run`` AND
    ``set_invocation_run_id``; both are process-global, so a test that only
    cleared the former leaked a run id into sibling files. Both are reset before
    and after every test here, so nothing this file sets can be observed
    outside it.

    The reject-reasons log is redirected into tmp (``HAL_REJECT_LOG``, the
    call-time seam at reject_log.py:53) so a real phase-6 satisfaction FAIL
    cannot append to the repo's production log.

    The backend registry itself is reset by conftest's autouse
    ``_llm_backend_registry_isolation`` — nothing is left to leak.
    """
    monkeypatch.setattr(_llm_mod, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.delenv("HAL_ENGINE_DURABLE_BACKEND", raising=False)
    monkeypatch.delenv("HAL_DBOS_DB_PATH", raising=False)
    monkeypatch.setenv("HAL_REJECT_LOG", str(tmp_path / "gh1626b-reject-log.jsonl"))
    telemetry_ctx.clear_current_run()
    telemetry_ctx.set_invocation_run_id(None)
    yield
    telemetry_ctx.clear_current_run()
    telemetry_ctx.set_invocation_run_id(None)


# ─── LLM stub backends ───────────────────────────────────────────────────────


def _ok(raw: str, kw: dict, **extra: Any) -> StepResult:
    data: dict[str, Any] = {
        "raw_response": raw,
        "worker_written_paths": [],
        "manifest_source": "harness_tool_record",
    }
    data.update(extra)
    data.update(kw.get("extra_data") or {})
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name=kw.get("step_name", _BACKEND_NAME),
        error=None,
        error_code=None,
        recoverable=True,
    )


class _FixBlockedBackend:
    """Review PASSes conformance, the fix worker reports BLOCKED (legacy marker
    site, phase_6_review.py:2512) → terminal E_FIX_BLOCKED at write_fix_artifact.
    """

    def __call__(self, **kw) -> StepResult:
        p = kw.get("prompt", "")
        if "satisfaction evaluator" in p:
            raw = "SCORE: 95\nVERDICT: PASS\n"
        elif "fix worker" in p:
            raw = "FIX BLOCKED — 1 of 3 findings fixed. Diagnosis: spec ambiguity.\n"
        else:
            raw = "# Composite Review\n\n## Aggregated Findings\n\nSeverity: HIGH\nVERDICT: FAIL\n"
        return _ok(raw, kw)


class _FixCompleteBackend:
    """Fix worker reports COMPLETE and names one touched test file, so the
    post-fix pytest gate has a real manifest scope to run against.
    """

    def __call__(self, **kw) -> StepResult:
        p = kw.get("prompt", "")
        if "satisfaction evaluator" in p:
            raw = "SCORE: 95\nVERDICT: PASS\n"
        elif "fix worker" in p:
            raw = "FIX COMPLETE — 1 of 1 findings fixed. Files: [tests/test_x.py]\n"
            return _ok(raw, kw, worker_written_paths=["tests/test_x.py"])
        else:
            raw = "# Composite Review\n\n## Aggregated Findings\n\nSeverity: HIGH\nVERDICT: FAIL\n"
        return _ok(raw, kw)


class _PassthroughBackend:
    def __call__(self, **kw) -> StepResult:
        return _ok(kw.get("prompt", ""), kw)


def _register(backend, monkeypatch) -> None:
    register_backend(_BACKEND_NAME, backend, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", _BACKEND_NAME)


# ─── production-entry-point driver (no UUT is mocked) ────────────────────────


_CTX_TEMPLATE: dict[str, Any] = {
    "tenant_id": "hal",
    "scope": None,
    "db_path": None,
    "org_config": None,
    "question": "Add foo to bar",
    "session_id": "gh1626b-test",
    "persona": "hal",
    "framework": None,
    "domain": None,
    "enable_rag": False,
    "enable_reranker": False,
    "enable_domain_scoped_reranker": False,
    "message_history": None,
    "llm_provider": "azure",
    "llm_model": "gpt-4.1-datazone",
    "domains": [],
}


def _ctx_dict(scratchpad: Path, **org_extra: Any) -> dict[str, Any]:
    d = dict(_CTX_TEMPLATE)
    d["org_config"] = {"scratchpad_dir": str(scratchpad), **org_extra}
    return d


def _event_log_path(tmp_path: Path) -> str:
    real_tmp = Path(realpath(str(tmp_path)))  # §1j macOS /var-symlink guard
    d = real_tmp / ".hal-build"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "events.jsonl")


def _run_phase_6_native(
    scratchpad: Path, tmp_path: Path, run_id: str, git_cwd: Path | None = None
) -> dict[str, Any]:
    """Drive the REAL production entry point for a phase (lib/phase_sentinel.py:308).

    Not `engine.execute` directly: this is the path a build actually takes, so an
    executor-level GREEN and an engine-level GREEN are both exercised honestly.

    ``git_cwd`` is the sibling seam of
    tests/test_phase_6_post_fix_pytest_gate_7A940850.py:52-56 — an EXPLICIT
    ``org_config["git_cwd"]``. Without it ``resolve_git_cwd_with_source`` falls
    back to the ambient process CWD (source='cwd') and the GH1220 guard at
    phase_6_review.py:4342 aborts the phase with E_GIT_CWD_AMBIENT long before
    the post-fix pytest gate is reached.
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key

    event_log_path = _event_log_path(tmp_path)
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id=run_id, step_name="gh1626b"
    )
    org_extra: dict[str, Any] = {}
    if git_cwd is not None:
        git_cwd.mkdir(parents=True, exist_ok=True)
        org_extra["git_cwd"] = str(git_cwd)
    ctx = _ctx_dict(scratchpad, **org_extra)
    key = phase_key(run_id, "phase_6_review", ctx)
    return execute_native_workflow("phase_6_review", ctx, run_id, event_log_path, key)


def _assert_not_assessed(
    scratchpad: Path, ac: str, step_name: str, error_code: str
) -> str:
    sat = scratchpad / SATISFACTION_DOC_RELPATH
    assert sat.is_file(), (
        f"{ac}: expected the satisfaction doc to EXIST at {sat} after the abort "
        f"({error_code}); it is absent — phase 7 is stranded with no producer."
    )
    body = sat.read_text()
    assert "SATISFACTION: NOT_ASSESSED" in body, (
        f"{ac}: expected the marker line 'SATISFACTION: NOT_ASSESSED' in {sat}; "
        f"seen:\n{body!r}"
    )
    assert step_name in body, (
        f"{ac}: expected the aborting step name {step_name!r} in {sat}; seen:\n{body!r}"
    )
    assert error_code in body, (
        f"{ac}: expected the aborting error code {error_code!r} in {sat}; seen:\n{body!r}"
    )
    return body


# ═════════════════════ AC1 — abort at the fix step (§1l) ═════════════════════


def test_ac1_fix_blocked_abort_writes_not_assessed_satisfaction_doc_gh1626b(tmp_path, monkeypatch):
    """AC1 (§1l side-effect): phase_6_review aborting at write_fix_artifact with
    E_FIX_BLOCKED leaves reviews/build-satisfaction.md ON DISK carrying
    `SATISFACTION: NOT_ASSESSED`, the aborting step name and the error code —
    and no token that could read as acceptance.

    RED today: `write_satisfaction_doc` is step 20 of 21; a terminal exit at
    step 10 has NO producer for the artifact, so the file is never created.
    """
    scratchpad = Path(realpath(str(tmp_path))) / "scratch"
    _register(_FixBlockedBackend(), monkeypatch)

    result = _run_phase_6_native(scratchpad, tmp_path, "gh1626b-ac1")

    assert result["status"] == "error", (
        f"AC1 fixture: expected the phase to abort; got status={result['status']!r} "
        f"error_code={result.get('error_code')!r}"
    )
    assert result["error_code"] == "E_FIX_BLOCKED", (
        f"AC1 fixture: expected the abort to be E_FIX_BLOCKED; got {result.get('error_code')!r}"
    )

    body = _assert_not_assessed(scratchpad, "AC1", "write_fix_artifact", "E_FIX_BLOCKED")
    assert "SCORE:" not in body, (
        f"AC1: the stub must record NO score — a score reads as an assessment that never "
        f"happened; seen:\n{body!r}"
    )
    assert "VERDICT: PASS" not in body, (
        f"AC1: the stub must record NO acceptance verdict; seen:\n{body!r}"
    )


# ═════ AC2 — a second, structurally different abort (the post-fix gate) ══════


@dataclass
class _FakeTestRunResult:
    """Duck-typed stand-in for plugins.disk_truth.TestRunResult (the shape
    tests/test_phase_6_post_fix_pytest_gate_7A940850.py already stubs).
    """

    exit_code: int
    n_passed: int
    n_failed: int
    stdout_path: str
    stderr_path: str = "/tmp/gh1626b-fake-stderr.txt"


def _seam_post_fix_pytest_gate(monkeypatch, tmp_path: Path) -> Path:
    """§1aa named helper: stage the INFRA seams that let a real phase-6 run reach
    the deterministic post-fix pytest gate (E_POST_FIX_PYTEST_FAILED) and stop
    there. Returns the explicit ``git_cwd`` to hand ``_run_phase_6_native``.

    Two distinct things are staged, and both are required:

    1. ``org_config["git_cwd"]`` (returned) — the sibling seam of
       tests/test_phase_6_post_fix_pytest_gate_7A940850.py:52-56. It makes
       ``resolve_git_cwd_with_source`` report a NON-ambient source, so the GH1220
       refusal at phase_6_review.py:4342/:4713 (E_GIT_CWD_AMBIENT) does not
       pre-empt the gate.
    2. The seams are applied to ``workflows.phase_6_review`` — the module object
       ``workflows/__init__.py:18`` binds and the engine actually executes.
       Patching the top-level ``phase_6_review`` alias instead leaves the running
       code untouched (two module objects for one file, engine_py has both dirs
       on sys.path), which is exactly how the ambient-CWD abort slipped in.

    The synthetic-env guard is triplicated across commit_fix_code /
    commit_fix_tests / run_pytest_post_fix. Keep the commit steps skipping (no
    git repo under tmp_path) and let ONLY the pytest gate run — decided by the
    immediate caller's name, so nothing races and nothing else changes.

    Only INFRA is seamed (SHA resolution / the pytest subprocess / the
    telemetry-only baseline gate) — exactly the seams 7A940850's own tests stub.
    The production gate body, the terminal StepResult and the artifact writer all
    run for real.
    """
    from bytedigger_engine.workflows import phase_6_review as p6_live

    def _guard(cfg, git_cwd):  # noqa: ARG001
        return sys._getframe(1).f_code.co_name != "_run_pytest_post_fix"

    monkeypatch.setattr(p6_live, "_is_synthetic_test_env", _guard)
    monkeypatch.setattr(p6_live, "resolve_pre_phase_sha", lambda *a, **kw: "a" * 40)
    monkeypatch.setattr(p6_live, "run_baseline_delta_gate", lambda *a, **kw: None)

    stdout = Path(realpath(str(tmp_path))) / "pytest-stdout.txt"
    stdout.write_text(
        "FAILED tests/test_x.py::test_beta\nFAILED tests/test_x.py::test_gamma\n"
        "1 passed, 2 failed in 0.3s\n"
    )
    monkeypatch.setattr(
        p6_live,
        "run_test_command",
        lambda argv, cwd, timeout=180: _FakeTestRunResult(
            exit_code=1, n_passed=1, n_failed=2, stdout_path=str(stdout)
        ),
    )
    return Path(realpath(str(tmp_path))) / "repo"


def test_ac2_post_fix_pytest_abort_writes_not_assessed_satisfaction_doc_gh1626b(tmp_path, monkeypatch):
    """AC2: the same holds for a structurally different terminal — the
    deterministic post-fix pytest gate (phase_6_review.py:5400,
    E_POST_FIX_PYTEST_FAILED, step run_pytest_post_fix). The handler is bound to
    phase TERMINATION, not to one error code or one step.

    Only INFRA is seamed (git probe / SHA resolution / the pytest subprocess /
    the telemetry-only baseline gate) — exactly the seams 7A940850's own tests
    stub. The production gate body, the terminal StepResult and the artifact
    writer all run for real.

    RED today: same stranding as AC1 — the abort is at step 13 of 21.
    """
    scratchpad = Path(realpath(str(tmp_path))) / "scratch"
    _register(_FixCompleteBackend(), monkeypatch)

    git_cwd = _seam_post_fix_pytest_gate(monkeypatch, tmp_path)

    result = _run_phase_6_native(scratchpad, tmp_path, "gh1626b-ac2", git_cwd=git_cwd)

    assert result["status"] == "error", (
        f"AC2 fixture: expected the phase to abort at the post-fix pytest gate; got "
        f"status={result['status']!r} error_code={result.get('error_code')!r}"
    )
    assert result["error_code"] == "E_POST_FIX_PYTEST_FAILED", (
        f"AC2 fixture: expected E_POST_FIX_PYTEST_FAILED; got {result.get('error_code')!r} "
        f"(error={result.get('error')!r})"
    )

    _assert_not_assessed(
        scratchpad, "AC2", "run_pytest_post_fix", "E_POST_FIX_PYTEST_FAILED"
    )


# ═══════════ AC3 — a real assessment is never overwritten by a stub ══════════


def test_ac3_existing_satisfaction_doc_is_left_byte_identical_gh1626b(tmp_path, monkeypatch):
    """AC3: when reviews/build-satisfaction.md already holds a REAL assessment
    (SCORE:/VERDICT:), an abort must leave its bytes untouched.

    Pre-staged deterministically before the phase runs (§1i) — no race.

    Today this passes vacuously (nobody writes on abort at all); after GREEN it
    is the guard that stops the stub clobbering a genuine verdict.
    """
    scratchpad = Path(realpath(str(tmp_path))) / "scratch"
    sat = scratchpad / SATISFACTION_DOC_RELPATH
    sat.parent.mkdir(parents=True, exist_ok=True)
    original = b"# Build satisfaction\n\nSCORE: 88\nVERDICT: PASS\n"
    sat.write_bytes(original)

    _register(_FixBlockedBackend(), monkeypatch)
    result = _run_phase_6_native(scratchpad, tmp_path, "gh1626b-ac3")

    assert result["error_code"] == "E_FIX_BLOCKED", (
        f"AC3 fixture: expected the abort to be E_FIX_BLOCKED; got {result.get('error_code')!r}"
    )
    seen = sat.read_bytes()
    assert seen == original, (
        f"AC3: an existing real assessment must be left byte-identical after an abort; "
        f"expected {original!r}, seen {seen!r}"
    )
    assert b"NOT_ASSESSED" not in seen, (
        f"AC3: the NOT_ASSESSED stub must not be appended to a real assessment; seen {seen!r}"
    )


# ═══════════ AC4/AC5 — the EXECUTOR wiring (not phase 6) ═════════════════════


def _register_handler_workflow(
    monkeypatch, name: str, marker: Path, *, handler_returns_ok: bool
) -> None:
    """Wrap the production ``workflows.register_all`` — the exact hook that both
    ``execute_engine`` (lib/phase_sentinel.py) and ``_dbos_execute_wrapper``
    (lib/dbos_setup.py) call — so the workflow only ever runs when the real
    executor really executes it (the tests/test_gh612_phase_sentinel_seam.py
    pattern).

    The workflow's ONLY step returns a terminal error; its ``error_handler``
    appends to an on-disk marker (§1l) so "did the hook fire" is a real
    side-effect, not a spy on a mocked seam.
    """
    from bytedigger_engine import workflows as workflows_pkg

    def _step(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error="gh1626b terminal", error_code="E_GH1626B_BOOM", recoverable=False,
        )

    def _handler(result: StepResult, _ctx) -> StepResult:
        with marker.open("a") as fh:
            fh.write(json.dumps({"code": result.error_code, "step": result.step_name}) + "\n")
        if handler_returns_ok:
            # A handler that tries to RESCUE a terminal result. AC4 forbids it
            # from taking effect.
            return StepResult(
                status="ok", data={"rescued": True}, duration_ms=0,
                step_name=result.step_name, error=None, error_code=None, recoverable=True,
            )
        return result

    orig = workflows_pkg.register_all

    def _patched(engine):
        orig(engine)
        engine.register(
            name,
            WorkflowDefinition(
                name=name,
                steps=[StepContract(name=name, execute=_step)],
                error_handler=_handler,
            ),
        )

    monkeypatch.setattr(workflows_pkg, "register_all", _patched)


def _handler_calls(marker: Path) -> list[dict]:
    if not marker.exists():
        return []
    return [json.loads(line) for line in marker.read_text().splitlines() if line.strip()]


def test_ac4_error_handler_returning_ok_cannot_rescue_a_terminal_result_gh1626b(tmp_path, monkeypatch):
    """AC4 (asserted on the EXECUTOR, not on phase 6): the declared
    ``WorkflowDefinition.error_handler`` fires for a terminal step result, and a
    handler that returns ``status="ok"`` does NOT change the phase outcome — the
    phase still fails with the ORIGINAL error code. It may enrich, never rescue.

    RED today: contracts.py:317 has zero production consumers, so the handler
    never fires (marker file empty). The no-rescue half is the invariant that
    must hold both before and after GREEN.
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key

    real_tmp = Path(realpath(str(tmp_path)))
    marker = real_tmp / "ac4-handler-calls.jsonl"
    _register_handler_workflow(monkeypatch, "gh1626b_ac4", marker, handler_returns_ok=True)

    event_log_path = _event_log_path(tmp_path)
    ctx = _ctx_dict(real_tmp / "scratch")
    run_id = "gh1626b-ac4"
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id=run_id, step_name="gh1626b"
    )
    key = phase_key(run_id, "gh1626b_ac4", ctx)

    result = execute_native_workflow("gh1626b_ac4", ctx, run_id, event_log_path, key)

    calls = _handler_calls(marker)
    assert len(calls) == 1, (
        f"AC4: the declared error_handler must fire exactly once for a terminal result; "
        f"expected 1 call, seen {len(calls)} ({calls!r}). "
        f"WorkflowDefinition.error_handler (contracts.py:317) is still unwired."
    )
    assert calls[0]["code"] == "E_GH1626B_BOOM", (
        f"AC4: the handler must receive the ORIGINAL terminal result; expected "
        f"code='E_GH1626B_BOOM', seen {calls[0]!r}"
    )
    assert result["status"] == "error", (
        f"AC4 (no rescue): a handler returning status='ok' must not change the phase "
        f"outcome; expected status='error', seen {result['status']!r} ({result!r})"
    )
    assert result["error_code"] == "E_GH1626B_BOOM", (
        f"AC4 (no rescue): the ORIGINAL error code must survive the handler; expected "
        f"'E_GH1626B_BOOM', seen {result.get('error_code')!r}"
    )


def test_ac5_error_handler_fires_on_both_execution_paths_gh1626b(tmp_path, monkeypatch):
    """AC5 (§1u call-path matrix): the hook fires under
    ``lib.phase_sentinel.execute_native_workflow`` AND under
    ``lib.dbos_setup.execute_durable_workflow``. A fix live on one path only is
    inert exactly where builds actually run.

    Leg 1: execute_native_workflow.
    Leg 2: execute_durable_workflow with the env unset (the shipped default —
           resolves to native, dbos_setup.py:415).
    Leg 3: execute_durable_workflow with HAL_ENGINE_DURABLE_BACKEND=dbos, i.e.
           the REAL @DBOS.workflow shell (the in-process pattern of
           tests/test_r_prime_dbos_ship1.py). Exercised only when the optional
           `dbos` package is importable (lib/dbos_setup.py keeps DBOS=None
           otherwise, per GH864); legs 1-2 still assert unconditionally.

    RED today: the handler is unwired on every path — all three markers empty.
    """
    from bytedigger_engine.lib import dbos_setup as dbos_setup
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key

    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)

    # ── Leg 1: native executor ────────────────────────────────────────────────
    m_native = real_tmp / "ac5-native.jsonl"
    _register_handler_workflow(monkeypatch, "gh1626b_ac5_native", m_native, handler_returns_ok=False)
    ctx = _ctx_dict(real_tmp / "scratch")
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id="gh1626b-ac5a", step_name="gh1626b"
    )
    key = phase_key("gh1626b-ac5a", "gh1626b_ac5_native", ctx)
    execute_native_workflow("gh1626b_ac5_native", ctx, "gh1626b-ac5a", event_log_path, key)
    assert len(_handler_calls(m_native)) == 1, (
        f"AC5 (path 1/3, execute_native_workflow @ lib/phase_sentinel.py:308): the "
        f"error_handler must fire once; seen {_handler_calls(m_native)!r}"
    )

    # ── Leg 2: durable entry point, default backend ───────────────────────────
    m_durable = real_tmp / "ac5-durable-default.jsonl"
    _register_handler_workflow(monkeypatch, "gh1626b_ac5_durable", m_durable, handler_returns_ok=False)
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id="gh1626b-ac5b", step_name="gh1626b"
    )
    dbos_setup.execute_durable_workflow(
        "gh1626b_ac5_durable", _ctx_dict(real_tmp / "scratch"), "gh1626b-ac5b",
        event_log_path=event_log_path,
    )
    assert len(_handler_calls(m_durable)) == 1, (
        f"AC5 (path 2/3, execute_durable_workflow @ lib/dbos_setup.py:383, default "
        f"backend): the error_handler must fire once; seen {_handler_calls(m_durable)!r}"
    )

    # ── Leg 3: durable entry point, REAL dbos backend ─────────────────────────
    if getattr(dbos_setup, "DBOS", None) is None:
        pytest.skip(
            "AC5 leg 3/3 not exercised: the optional `dbos` package is not importable, so "
            "lib/dbos_setup.py keeps DBOS=None (GH864) and the REAL @DBOS.workflow shell "
            "cannot be entered. Legs 1-2 asserted above; a skipped leg must be VISIBLE."
        )

    m_dbos = real_tmp / "ac5-durable-dbos.jsonl"
    _register_handler_workflow(monkeypatch, "gh1626b_ac5_dbos", m_dbos, handler_returns_ok=False)
    db_path = real_tmp / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_ENGINE_DURABLE_BACKEND", "dbos")
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id="gh1626b-ac5c", step_name="gh1626b"
    )
    try:
        dbos_setup.init_dbos(db_path)
        dbos_setup.execute_durable_workflow(
            "gh1626b_ac5_dbos", _ctx_dict(real_tmp / "scratch"), "gh1626b-ac5c",
            event_log_path=event_log_path,
        )
    finally:
        try:
            dbos_setup.DBOS.destroy()
        except Exception:  # noqa: BLE001 — teardown must never mask the assertion
            pass
        dbos_setup._dbos_initialized = False

    assert len(_handler_calls(m_dbos)) == 1, (
        f"AC5 (path 3/3, execute_durable_workflow @ lib/dbos_setup.py:383 under the REAL "
        f"DBOS wrapper): the error_handler must fire once; seen {_handler_calls(m_dbos)!r}"
    )


# ═════════════ AC6/AC7/AC8 — phase 7 tells the truth (three states) ══════════


def _p7_ctx(scratchpad: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo to bar",
        session_id="gh1626b-test",
        persona="hal",
        framework=None,
        domain=None,
    )


def _run_phase_7(scratchpad: Path):
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", _p7_ctx(scratchpad))
    return result


def _seed_sat(scratchpad: Path, body: str) -> Path:
    doc = scratchpad / SATISFACTION_DOC_RELPATH
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(body)
    return doc


_NOT_ASSESSED_DOC = (
    "# Build satisfaction — NOT ASSESSED\n\n"
    "SATISFACTION: NOT_ASSESSED\n\n"
    "Aborting step: write_fix_artifact\n"
    "Error code: E_FIX_BLOCKED\n"
    "Acceptance was never evaluated: the phase ended before step 20.\n"
)


def test_ac6_phase_7_renders_satisfaction_in_three_states_gh1626b(tmp_path, monkeypatch):
    """AC6: phase_7_synthesize.py:441 renders THREE states, not two.
      - a NOT_ASSESSED doc  → `satisfaction: NOT_ASSESSED` (never PRESENT)
      - an absent doc       → `satisfaction: MISSING`   (unchanged)
      - a real doc          → `satisfaction: PRESENT`   (unchanged)

    The rendered line is asserted on the report artifact on disk (bd#89 P3c:
    the deterministic report; the passthrough backend only guarantees that an
    engine run can never reach a real model).
    """
    _register(_PassthroughBackend(), monkeypatch)

    not_assessed = Path(realpath(str(tmp_path))) / "s-not-assessed"
    _seed_sat(not_assessed, _NOT_ASSESSED_DOC)
    _run_phase_7(not_assessed)
    report = (not_assessed / REPORT_DOC_RELPATH).read_text()
    assert "satisfaction: NOT_ASSESSED" in report, (
        f"AC6: a NOT_ASSESSED satisfaction doc must render 'satisfaction: NOT_ASSESSED'; "
        f"the ARTIFACTS block reads:\n"
        f"{[ln for ln in report.splitlines() if 'satisfaction:' in ln]!r}"
    )
    assert "satisfaction: PRESENT" not in report, (
        "AC6: a NOT_ASSESSED doc must NOT render 'satisfaction: PRESENT' — that tells the "
        "synthesizer acceptance evidence exists when it does not."
    )

    missing = Path(realpath(str(tmp_path))) / "s-missing"
    _run_phase_7(missing)
    report = (missing / REPORT_DOC_RELPATH).read_text()
    assert "satisfaction: MISSING" in report, (
        f"AC6: an absent satisfaction doc must still render 'satisfaction: MISSING'; seen "
        f"{[ln for ln in report.splitlines() if 'satisfaction:' in ln]!r}"
    )

    real = Path(realpath(str(tmp_path))) / "s-real"
    _seed_sat(real, "SCORE: 95\nVERDICT: PASS\n")
    _run_phase_7(real)
    report = (real / REPORT_DOC_RELPATH).read_text()
    assert "satisfaction: PRESENT" in report, (
        f"AC6: a real satisfaction doc must still render 'satisfaction: PRESENT'; seen "
        f"{[ln for ln in report.splitlines() if 'satisfaction:' in ln]!r}"
    )


# bd#89 P3c: AC7 and AC8 (E_SYNTHESIZER_NEEDS_CONTEXT names the artifact; the refusal
# survives a NOT_ASSESSED stub) are retired with the synthesizer LLM step and its
# error codes. The not-assessed signal now lives in the report (AC6, AC13 here, and
# tests/test_bd89_p3c_deterministic_synthesize_report.py AC4).


# ═════════════════════════════════════════════════════════════════════════════
# Round 2 — gate findings. The hook is SIDE-EFFECT-ONLY by construction.
# ═════════════════════════════════════════════════════════════════════════════


_ORIG_ERROR_TEXT = "gh1626b terminal — the original error text"
_HANDLER_BOOM = "gh1626b handler exploded on purpose"


def _terminal_step_contract(name: str) -> StepContract:
    """A step whose single outcome is the ORIGINAL terminal result the executor
    must preserve byte-for-byte: E_GH1626B_BOOM / recoverable=False."""

    def _step(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error=_ORIG_ERROR_TEXT, error_code="E_GH1626B_BOOM", recoverable=False,
        )

    return StepContract(name=name, execute=_step)


def _register_workflow(monkeypatch, name: str, steps: list[StepContract], handler) -> None:
    """Same production seam as ``_register_handler_workflow`` (wrap
    ``workflows.register_all``, which BOTH ``execute_engine`` and
    ``_dbos_execute_wrapper`` call) but with caller-supplied steps + handler.

    Kept separate so the round-1 helper — and the already-measured AC4/AC5 —
    are untouched.
    """
    from bytedigger_engine import workflows as workflows_pkg

    orig = workflows_pkg.register_all

    def _patched(engine):
        orig(engine)
        engine.register(
            name,
            WorkflowDefinition(name=name, steps=steps, error_handler=handler),
        )

    monkeypatch.setattr(workflows_pkg, "register_all", _patched)


def _drive_native(name: str, tmp_path: Path, run_id: str) -> tuple[dict[str, Any], str]:
    """Drive the REAL executor entry point (lib/phase_sentinel.py:308). Returns
    the phase result dict AND the event-log path so telemetry can be asserted."""
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key

    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    ctx = _ctx_dict(real_tmp / "scratch")
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id=run_id, step_name="gh1626b"
    )
    key = phase_key(run_id, name, ctx)
    return execute_native_workflow(name, ctx, run_id, event_log_path, key), event_log_path


def _events(event_log_path: str) -> list[dict[str, Any]]:
    p = Path(event_log_path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _assert_original_terminal_survived(result: dict[str, Any], ac: str) -> None:
    """The three fields the gate found silently mutable (MAJOR-2). ``recoverable
    is False`` is load-bearing at engine.py:313 — it is what turns a terminal
    failure into a stuck-report; a handler flipping it to True silently disarms
    that report."""
    assert result["status"] == "error", (
        f"{ac}: expected the phase outcome to stay status='error'; seen {result['status']!r} "
        f"({result!r})"
    )
    assert result["error_code"] == "E_GH1626B_BOOM", (
        f"{ac}: expected the ORIGINAL error_code 'E_GH1626B_BOOM' to survive the handler; "
        f"seen {result.get('error_code')!r}"
    )
    assert result["error"] == _ORIG_ERROR_TEXT, (
        f"{ac}: expected the ORIGINAL error text {_ORIG_ERROR_TEXT!r} to survive the "
        f"handler; seen {result.get('error')!r}"
    )
    assert result["recoverable"] is False, (
        f"{ac}: expected recoverable to stay exactly False (load-bearing at engine.py:313, "
        f"stuck-report); seen {result.get('recoverable')!r}"
    )


# The ONE canonical name for the warning event a contained handler exception
# emits (AC18, §1o). Declared here because AC9 asserts it by exact name too —
# gate round 3, condition 3: a substring predicate ("handler" in event_type)
# lets GREEN pick any name it likes and leaves §1o unenforced.
_HANDLER_RAISED_EVENT = "phase_error_handler_raised"


# ═══════════ AC9 (MAJOR-1) — a raising handler must not become a crash ═══════


def test_ac9_raising_error_handler_is_contained_and_logged_gh1626b(tmp_path, monkeypatch):
    """AC9: a ``WorkflowDefinition.error_handler`` that RAISES must not turn an
    abort into a crash.

    Why this is real and not hypothetical: a ctx whose org_config has no
    writable ``scratchpad_dir`` is live in production
    (tests/test_phase_6_review.py::test_missing_scratchpad_dir_raises), and the
    NOT_ASSESSED stub writer throws exactly there. If the executor lets the
    handler's exception out, EVERY abort on such a ctx stops being a typed
    engine error and becomes an uncaught traceback out of
    ``execute_native_workflow`` — a strictly worse failure than the one this
    lot is fixing.

    Three things are asserted, all on the REAL executor:
      1. nothing propagates out of ``execute_native_workflow``;
      2. the ORIGINAL error_code / error text / recoverable=False survive;
      3. a warning EVENT records that the handler raised — a contained
         exception that leaves no trace is indistinguishable from a handler
         that never fired.

    RED today: the hook has zero production consumers, so no event is ever
    emitted (leg 3 fails). Legs 1-2 are the invariants that must hold after
    GREEN too.
    """
    name = "gh1626b_ac9"

    def _raising_handler(_result, _ctx):
        raise RuntimeError(_HANDLER_BOOM)

    _register_workflow(monkeypatch, name, [_terminal_step_contract(name)], _raising_handler)

    try:
        result, event_log_path = _drive_native(name, tmp_path, "gh1626b-ac9")
    except Exception as exc:  # noqa: BLE001 — this IS the thing under test
        raise AssertionError(
            f"AC9: the handler's exception must be CONTAINED by the executor; expected "
            f"execute_native_workflow to return a normal error result, but "
            f"{type(exc).__name__}({exc!r}) propagated out of it."
        ) from exc

    _assert_original_terminal_survived(result, "AC9")

    evs = _events(event_log_path)
    hits = [
        e for e in evs
        if str(e.get("event_type", "")) == _HANDLER_RAISED_EVENT
        and (
            _HANDLER_BOOM in json.dumps(e.get("payload") or {})
            or "RuntimeError" in json.dumps(e.get("payload") or {})
        )
    ]
    assert hits, (
        "AC9 (narrowed, gate round 3 condition 3): expected a warning event recording "
        f"that the error_handler raised — event_type exactly {_HANDLER_RAISED_EVENT!r} "
        "(not merely a name containing 'handler') and a payload carrying the exception "
        "(message or type). Seen event_types: "
        f"{sorted({str(e.get('event_type')) for e in evs})!r}"
    )


# ═══════ AC10 (MAJOR-2) — a handler may not RE-CODE a terminal result ════════


def test_ac10_error_handler_cannot_mutate_the_terminal_result_gh1626b(tmp_path, monkeypatch):
    """AC10: the handler's RETURN VALUE is discarded entirely.

    AC4 already covers the ``ok``-rescue leg. This is the leg the gate found
    unguarded: a handler that returns a still-``error`` result but with a
    DIFFERENT error_code (``E_GH1626B_MUTATED``) and ``recoverable=True``. Both
    survive an "is it still an error?" check, so a wiring that merges the
    handler's return value would pass AC4 and still silently re-code the abort
    and disarm the engine.py:313 stuck-report.

    RED today: the handler never fires at all (marker empty), so the mutation
    is not yet even reachable — the fire-once assertion is the RED half, the
    no-mutation assertions are the invariant that must survive GREEN.
    """
    name = "gh1626b_ac10"
    real_tmp = Path(realpath(str(tmp_path)))
    marker = real_tmp / "ac10-handler-calls.jsonl"

    def _mutating_handler(result: StepResult, _ctx) -> StepResult:
        with marker.open("a") as fh:
            fh.write(json.dumps({"code": result.error_code, "step": result.step_name}) + "\n")
        return StepResult(
            status="error", data={"mutated": True}, duration_ms=0,
            step_name=result.step_name,
            error="gh1626b MUTATED error text",
            error_code="E_GH1626B_MUTATED",
            recoverable=True,
        )

    _register_workflow(monkeypatch, name, [_terminal_step_contract(name)], _mutating_handler)

    result, _ = _drive_native(name, tmp_path, "gh1626b-ac10")

    calls = _handler_calls(marker)
    assert len(calls) == 1, (
        f"AC10: the declared error_handler must fire exactly once for the terminal result; "
        f"expected 1 call, seen {len(calls)} ({calls!r}). WorkflowDefinition.error_handler "
        f"(contracts.py:317) is still unwired."
    )

    _assert_original_terminal_survived(result, "AC10")
    blob = json.dumps(result, default=str)
    assert "E_GH1626B_MUTATED" not in blob, (
        f"AC10: the handler's returned result must be DISCARDED — no trace of "
        f"'E_GH1626B_MUTATED' may reach the phase outcome; seen {result!r}"
    )


# ═════════ AC11 — exactly ONE handler call per TERMINAL phase outcome ════════


def test_ac11_error_handler_fires_once_not_once_per_retry_attempt_gh1626b(tmp_path, monkeypatch):
    """AC11: the handler is bound to the phase's TERMINAL outcome, so it fires
    exactly once even when the failing step was attempted more than once.

    Two independent retry mechanisms exist and each can double-fire a naive
    wiring; both are driven for real:

    Leg A — the ``step()`` factory's inner loop (contracts.py:624-666). The
      failing step carries ``RetryPolicy(max_retries=1)``, exactly as
      ``write_fix_artifact`` does (phase_6_review.py:5438/5448), and returns a
      RECOVERABLE error so the loop really runs both attempts.
    Leg B — the engine's own recursion (engine.py:639-658): the step asks for
      ``retry_from_step`` on attempt 1 and goes terminal on attempt 2, so
      ``_execute_steps`` is ENTERED TWICE for one phase.

    "Exactly once" is made observable by counting BOTH sides on disk: an
    attempt counter written by the step itself and a call counter written by
    the handler. Asserting the attempt counter is 2 first is what keeps the
    "== 1" honest — without it, a handler that never fires and a step that
    never retried would look identical.

    RED today: the hook is unwired, so both handler counters are 0.
    """
    real_tmp = Path(realpath(str(tmp_path)))

    def _make_handler(marker: Path):
        def _handler(result: StepResult, _ctx):
            with marker.open("a") as fh:
                fh.write(
                    json.dumps({"code": result.error_code, "step": result.step_name}) + "\n"
                )
        return _handler

    # ── Leg A: RetryPolicy(max_retries=1) inside the step() factory ──────────
    a_attempts = real_tmp / "ac11a-attempts.jsonl"
    a_handler = real_tmp / "ac11a-handler.jsonl"
    name_a = "gh1626b_ac11_steppolicy"

    def _fn_a(_ctx, _prev):
        with a_attempts.open("a") as fh:
            fh.write("attempt\n")
        # recoverable=True with data=None: the step() loop retries it, and the
        # engine's retry_from_step branch is NOT entered (data is not a dict),
        # so the phase terminates here — one terminal outcome, two attempts.
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name_a,
            error=_ORIG_ERROR_TEXT, error_code="E_GH1626B_BOOM", recoverable=True,
        )

    _register_workflow(
        monkeypatch, name_a,
        [step(name_a, _fn_a, retries=RetryPolicy(max_retries=1), sleep_fn=lambda _s: None)],
        _make_handler(a_handler),
    )
    result_a, _ = _drive_native(name_a, tmp_path, "gh1626b-ac11a")

    n_attempts = len(a_attempts.read_text().splitlines()) if a_attempts.exists() else 0
    assert n_attempts == 2, (
        f"AC11 leg A fixture: RetryPolicy(max_retries=1) must produce TWO attempts of the "
        f"failing step (else 'fires once' is vacuous); seen {n_attempts}"
    )
    assert result_a["error_code"] == "E_GH1626B_BOOM", (
        f"AC11 leg A fixture: expected the phase to end on E_GH1626B_BOOM; seen "
        f"{result_a.get('error_code')!r}"
    )
    calls_a = _handler_calls(a_handler)
    assert len(calls_a) == 1, (
        f"AC11 leg A (step() RetryPolicy, contracts.py:624-666): the handler must fire ONCE "
        f"for the phase's terminal outcome, not once per attempt; expected 1, seen "
        f"{len(calls_a)} ({calls_a!r})"
    )

    # ── Leg B: the engine's retry_from_step recursion (engine.py:639-658) ────
    b_attempts = real_tmp / "ac11b-attempts.jsonl"
    b_handler = real_tmp / "ac11b-handler.jsonl"
    name_b = "gh1626b_ac11_recursion"

    def _fn_b(_ctx, _prev):
        with b_attempts.open("a") as fh:
            fh.write("attempt\n")
        n = len(b_attempts.read_text().splitlines())
        if n == 1:
            return StepResult(
                status="error",
                data={"retry_from_step": 0, "cycle_count": 1, "findings": "gh1626b findings"},
                duration_ms=0, step_name=name_b,
                error="gh1626b retryable", error_code="E_GH1626B_RETRYABLE",
                recoverable=True,
            )
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name_b,
            error=_ORIG_ERROR_TEXT, error_code="E_GH1626B_BOOM", recoverable=False,
        )

    _register_workflow(
        monkeypatch, name_b,
        [StepContract(name=name_b, execute=_fn_b)],
        _make_handler(b_handler),
    )
    result_b, _ = _drive_native(name_b, tmp_path, "gh1626b-ac11b")

    n_b = len(b_attempts.read_text().splitlines()) if b_attempts.exists() else 0
    assert n_b == 2, (
        f"AC11 leg B fixture: the engine must recurse once (engine.py:639-658), giving TWO "
        f"step invocations; seen {n_b}"
    )
    _assert_original_terminal_survived(result_b, "AC11 leg B")
    calls_b = _handler_calls(b_handler)
    assert len(calls_b) == 1, (
        f"AC11 leg B (engine retry_from_step recursion, engine.py:639-658): _execute_steps is "
        f"entered TWICE for one phase, but the handler must fire ONCE; expected 1, seen "
        f"{len(calls_b)} ({calls_b!r})"
    )
    assert calls_b[0]["code"] == "E_GH1626B_BOOM", (
        f"AC11 leg B: the single call must carry the phase's TERMINAL code, not the "
        f"intermediate retryable one; expected 'E_GH1626B_BOOM', seen {calls_b[0]!r}"
    )


# ═════════════ AC12 — a STALE stub is refreshed; a REAL doc is not ═══════════


def test_ac12_stale_stub_is_refreshed_but_real_assessment_is_not_gh1626b(tmp_path, monkeypatch):
    """AC12: AC3 ("never overwrite") protects a REAL assessment only.

    A run that aborts twice — a resumed/re-entered build, which is precisely
    the scenario this lot exists for — would otherwise leave phase 7 reading a
    stub that names abort #1's step and code while the build actually died of
    something else. That is a truthful-looking artifact telling a lie, which is
    worse than the silence being fixed.

    Both aborts are REAL phase-6 runs on the SAME scratchpad (§1l): abort #1 is
    E_FIX_BLOCKED at write_fix_artifact, abort #2 is the structurally different
    deterministic post-fix pytest gate (E_POST_FIX_PYTEST_FAILED at
    run_pytest_post_fix). Only INFRA is seamed, exactly as in AC2.

    Round 3 (MAJOR-4): the refresh keys on the ``SATISFACTION: NOT_ASSESSED``
    marker THIS lot emits — a WHITELIST on our single emission point — never on
    a ``SCORE:``/``VERDICT:`` blacklist, which cannot tell a real assessment
    from a stub (see AC15/AC16/AC17). Leg 3 is therefore the whitelist's other
    half: a body that carries NEITHER the marker NOR any recognised token —
    unrecognised, foreign or half-written (``write_text`` is not atomic) — is
    left untouched, because closure is decided by what WE emit.

    RED today: nothing writes the stub on abort at all.
    """
    scratchpad = Path(realpath(str(tmp_path))) / "scratch"

    # INFRA seams (identical to AC2 — same named helper). None of them is
    # reached by abort #1, which dies at write_fix_artifact long before the
    # post-fix gate; the explicit git_cwd is what keeps abort #2 from being
    # pre-empted by the GH1220 ambient-CWD refusal.
    git_cwd = _seam_post_fix_pytest_gate(monkeypatch, tmp_path)

    # ── abort #1 — E_FIX_BLOCKED at write_fix_artifact ───────────────────────
    _register(_FixBlockedBackend(), monkeypatch)
    r1 = _run_phase_6_native(scratchpad, tmp_path, "gh1626b-ac12-first", git_cwd=git_cwd)
    assert r1["error_code"] == "E_FIX_BLOCKED", (
        f"AC12 fixture (abort #1): expected E_FIX_BLOCKED; got {r1.get('error_code')!r} "
        f"(error={r1.get('error')!r})"
    )
    _assert_not_assessed(scratchpad, "AC12 (abort #1)", "write_fix_artifact", "E_FIX_BLOCKED")

    # ── abort #2 — E_POST_FIX_PYTEST_FAILED at run_pytest_post_fix ───────────
    _register(_FixCompleteBackend(), monkeypatch)
    r2 = _run_phase_6_native(scratchpad, tmp_path, "gh1626b-ac12-second", git_cwd=git_cwd)
    assert r2["error_code"] == "E_POST_FIX_PYTEST_FAILED", (
        f"AC12 fixture (abort #2): expected E_POST_FIX_PYTEST_FAILED; got "
        f"{r2.get('error_code')!r} (error={r2.get('error')!r})"
    )

    body = _assert_not_assessed(
        scratchpad, "AC12 (abort #2)", "run_pytest_post_fix", "E_POST_FIX_PYTEST_FAILED"
    )
    assert "E_FIX_BLOCKED" not in body, (
        f"AC12: the stub left by abort #1 must be REFRESHED, not preserved — abort #1's code "
        f"'E_FIX_BLOCKED' must be gone from {scratchpad / SATISFACTION_DOC_RELPATH}; "
        f"seen:\n{body!r}"
    )
    assert "write_fix_artifact" not in body, (
        f"AC12: the refreshed stub must name abort #2's step only; abort #1's step "
        f"'write_fix_artifact' must be gone; seen:\n{body!r}"
    )

    # ── leg 3 — an UNRECOGNISED body is never touched (whitelist, MAJOR-4) ───
    real_scratch = Path(realpath(str(tmp_path))) / "scratch-foreign"
    real_sat = real_scratch / SATISFACTION_DOC_RELPATH
    real_sat.parent.mkdir(parents=True, exist_ok=True)
    # No marker, no SCORE:, no VERDICT: — the shape of a torn write or of a
    # producer this lot does not own. Everything outside our own emission point
    # is untouchable.
    original = b"# Multi-Evaluator Satisfaction Review (COMPLEX \xe2\x80\x94 3 eval"
    real_sat.write_bytes(original)
    assert b"SATISFACTION: NOT_ASSESSED" not in original, (
        "AC12 leg 3 fixture: the foreign body must NOT carry this lot's marker, else the "
        "leg cannot distinguish whitelist from blacklist."
    )

    _register(_FixBlockedBackend(), monkeypatch)
    r3 = _run_phase_6_native(real_scratch, tmp_path, "gh1626b-ac12-foreign", git_cwd=git_cwd)
    assert r3["error_code"] == "E_FIX_BLOCKED", (
        f"AC12 fixture (leg 3): expected E_FIX_BLOCKED; got {r3.get('error_code')!r}"
    )
    seen = real_sat.read_bytes()
    assert seen == original, (
        f"AC12 leg 3: 'refresh a stale STUB' must not become 'overwrite anything' — refresh "
        f"fires ONLY on a body carrying 'SATISFACTION: NOT_ASSESSED'; an unrecognised/torn "
        f"body stays byte-identical. Expected {original!r}, seen {seen!r}"
    )


# ═══ AC13 — the phase-7 PROMPT states NOT_ASSESSED is not acceptance evidence ═


def test_ac13_phase_7_report_lists_not_assessed_satisfaction_as_a_gap_gh1626b(tmp_path, monkeypatch):
    """AC13 (bd#89 P3c re-point): the phase-7 prompt is gone, so the "NOT_ASSESSED
    is not acceptance evidence" wording is now carried by the deterministic
    report: a NOT_ASSESSED satisfaction doc on disk must show up as a named
    "not assessed" gap in the report, never as a silent PRESENT.

    The passthrough backend stays registered so that an engine run can never
    reach a real model, whatever the phase-7 shape.
    """
    _register(_PassthroughBackend(), monkeypatch)

    scratchpad = Path(realpath(str(tmp_path))) / "s-not-assessed"
    _seed_sat(scratchpad, _NOT_ASSESSED_DOC)
    _run_phase_7(scratchpad)
    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    low = report.lower()

    assert "satisfaction: not_assessed" in low, (
        "AC13: the report must carry the NOT_ASSESSED state. Report lines mentioning "
        f"satisfaction: {[ln for ln in report.splitlines() if 'satisfaction' in ln.lower()]!r}"
    )
    assert "not assessed" in low, (
        "AC13: the report must say, in words, that the satisfaction doc was not assessed. "
        f"Report lines mentioning satisfaction: "
        f"{[ln for ln in report.splitlines() if 'satisfaction' in ln.lower()]!r}"
    )
    assert "satisfaction: PRESENT" not in report


# ═════════════════════════════════════════════════════════════════════════════
# Round 3 — gate round 2 findings. The ARGUMENT is a copy (MAJOR-3), and the
# refresh predicate is a WHITELIST on our own marker (MAJOR-4).
# ═════════════════════════════════════════════════════════════════════════════


# ═══ AC14 (MAJOR-3) — mutating the ARGUMENT in place must not take effect ════


def test_ac14_error_handler_cannot_mutate_its_argument_in_place_gh1626b(tmp_path, monkeypatch):
    """AC14: discarding the handler's RETURN value closes only half the door.

    ``StepResult`` is an UNFROZEN dataclass (contracts.py:64-92). If the
    executor hands the handler the LIVE result object, a single line —
    ``result.recoverable = True`` — silently disarms the stuck-report at
    engine.py:313 while passing AC4 (no rescue) and AC10 (return discarded)
    untouched, because neither of those looks at the argument. The handler in
    this test does exactly that, plus re-codes the abort in place to
    ``E_GH1626B_ALIASED``.

    Forcing shape: the ONLY way both assertions hold is if the executor invokes
    the handler with ``dataclasses.replace(result)`` (a copy) — the original
    never leaves the executor. A per-test stub cannot satisfy this: the mutation
    happens inside the real handler on whatever object the real executor passes.

    RED today: the hook has zero production consumers, so the handler never
    fires at all and the fire-once assertion fails first.
    """
    name = "gh1626b_ac14"
    real_tmp = Path(realpath(str(tmp_path)))
    marker = real_tmp / "ac14-handler-calls.jsonl"

    def _aliasing_handler(result: StepResult, _ctx) -> None:
        with marker.open("a") as fh:
            fh.write(
                json.dumps({"code": result.error_code, "recoverable": result.recoverable}) + "\n"
            )
        # MAJOR-3: mutate the argument IN PLACE and return nothing at all, so
        # the "return value is discarded" rule cannot possibly save us.
        result.recoverable = True
        result.error_code = "E_GH1626B_ALIASED"
        result.error = "gh1626b ALIASED error text"
        result.status = "ok"

    _register_workflow(monkeypatch, name, [_terminal_step_contract(name)], _aliasing_handler)

    result, _ = _drive_native(name, tmp_path, "gh1626b-ac14")

    calls = _handler_calls(marker)
    assert len(calls) == 1, (
        f"AC14: the declared error_handler must fire exactly once for the terminal result; "
        f"expected 1 call, seen {len(calls)} ({calls!r}). WorkflowDefinition.error_handler "
        f"(contracts.py:317) is still unwired."
    )
    assert calls[0] == {"code": "E_GH1626B_BOOM", "recoverable": False}, (
        f"AC14: the handler must be handed the terminal facts as they stand; expected "
        f"{{'code': 'E_GH1626B_BOOM', 'recoverable': False}}, seen {calls[0]!r}"
    )

    _assert_original_terminal_survived(result, "AC14")
    blob = json.dumps(result, default=str)
    assert "E_GH1626B_ALIASED" not in blob, (
        f"AC14: an IN-PLACE mutation of the handler's argument must not reach the phase "
        f"outcome — the executor must pass dataclasses.replace(result), a copy; seen {result!r}"
    )


# ═══ AC15/AC16 (MAJOR-4) — the refresh predicate is a WHITELIST ══════════════


_STALE_STUB_DOC = (
    "# Build satisfaction — NOT ASSESSED\n\n"
    "SATISFACTION: NOT_ASSESSED\n\n"
    "Aborting step: gh1626b_previous_step\n"
    "Error code: E_GH1626B_STALE_PREVIOUS\n"
    "Acceptance was never evaluated: the phase ended before step 20.\n"
)

# The COMPLEX composite the engine renders for itself, copied from the real
# shape at phase_6_review.py:3507-3522. It carries NO `SCORE:` and NO
# `VERDICT:` token — the blacklist predicate would classify this REAL
# assessment as a stub and overwrite it.
_COMPLEX_COMPOSITE_DOC = (
    "# Multi-Evaluator Satisfaction Review (COMPLEX — 3 evaluators)\n\n"
    "## Aggregation Summary\n"
    "| Evaluator | Status | Score | Verdict | Structured |\n"
    "|---|---|---|---|---|\n"
    "| 1 | ok | 91 | PASS | yes |\n"
    "| 2 | ok | 88 | PASS | yes |\n"
    "| 3 | ok | 93 | PASS | yes |\n\n"
    "- Valid evaluators: 3 / 3\n"
    "- Median score: 91  (threshold: 90)\n"
    "- Majority verdict: PASS  (agreement: 3/3)\n"
    "- Gate result: PASS\n"
    "- AC checklist: pass\n\n"
    "---\n## Evaluator 1\nAll ACs verified against the diff; two nits, none blocking.\n"
)

# A single-evaluator doc: raw LLM stdout. Its only score token is markdown and
# lowercase, which the production parser (phase_6_review.py:535, IGNORECASE +
# markdown-tolerant) reads as a real score of 62 — a real FAILING assessment.
_MARKDOWN_SCORE_DOC = (
    "# Satisfaction review\n\n"
    "**Score: 62**\n\n"
    "The GREEN patch does not satisfy AC3, and two ACs remain unverified.\n"
)


def _assert_marker_whitelist(
    tmp_path, monkeypatch, ac: str, tag: str, foreign_body: str
) -> None:
    """§1aa named helper for AC15/AC16: one REAL phase-6 abort per leg.

    Leg A (forcing): a doc carrying this lot's ``SATISFACTION: NOT_ASSESSED``
      marker IS refreshed — abort #1's code disappears, this abort's code
      appears. This is the leg that fails today, so neither AC can pass
      vacuously on "nobody writes anything".
    Leg B: ``foreign_body`` — a REAL assessment produced by a DIFFERENT
      producer — is left byte-identical.

    Both legs are pre-staged deterministically before the phase runs (§1i); no
    timing, no shared singleton, no race.
    """
    # ── leg A: our own marker → refreshed ────────────────────────────────────
    stale_scratch = Path(realpath(str(tmp_path))) / f"{tag}-stale"
    stale_sat = stale_scratch / SATISFACTION_DOC_RELPATH
    stale_sat.parent.mkdir(parents=True, exist_ok=True)
    stale_sat.write_text(_STALE_STUB_DOC)

    _register(_FixBlockedBackend(), monkeypatch)
    r_a = _run_phase_6_native(stale_scratch, tmp_path, f"gh1626b-{tag}-stale")
    assert r_a["error_code"] == "E_FIX_BLOCKED", (
        f"{ac} leg A fixture: expected E_FIX_BLOCKED; got {r_a.get('error_code')!r}"
    )
    body = _assert_not_assessed(stale_scratch, f"{ac} leg A", "write_fix_artifact", "E_FIX_BLOCKED")
    assert "E_GH1626B_STALE_PREVIOUS" not in body, (
        f"{ac} leg A: a body carrying 'SATISFACTION: NOT_ASSESSED' is OUR stub and must be "
        f"REFRESHED — the previous abort's code 'E_GH1626B_STALE_PREVIOUS' must be gone from "
        f"{stale_sat}; seen:\n{body!r}"
    )

    # ── leg B: someone else's real assessment → untouchable ──────────────────
    foreign_scratch = Path(realpath(str(tmp_path))) / f"{tag}-foreign"
    foreign_sat = foreign_scratch / SATISFACTION_DOC_RELPATH
    foreign_sat.parent.mkdir(parents=True, exist_ok=True)
    original = foreign_body.encode("utf-8")
    foreign_sat.write_bytes(original)

    assert "SCORE:" not in foreign_body and "VERDICT:" not in foreign_body, (
        f"{ac} fixture: the point of this AC is that a REAL assessment carries NEITHER "
        f"'SCORE:' NOR 'VERDICT:'; the fixture body does, so it would not exercise the "
        f"blacklist bug at all. Body:\n{foreign_body!r}"
    )
    assert "SATISFACTION: NOT_ASSESSED" not in foreign_body, (
        f"{ac} fixture: the foreign body must not carry this lot's marker."
    )

    _register(_FixBlockedBackend(), monkeypatch)
    r_b = _run_phase_6_native(foreign_scratch, tmp_path, f"gh1626b-{tag}-foreign")
    assert r_b["error_code"] == "E_FIX_BLOCKED", (
        f"{ac} leg B fixture: expected E_FIX_BLOCKED; got {r_b.get('error_code')!r}"
    )
    seen = foreign_sat.read_bytes()
    assert seen == original, (
        f"{ac} leg B: a REAL assessment written by a different producer must survive the "
        f"abort BYTE-FOR-BYTE — refresh fires only on our own marker, never on a "
        f"'SCORE:'/'VERDICT:' guess. Expected {original!r}, seen {seen!r}"
    )
    assert b"NOT_ASSESSED" not in seen, (
        f"{ac} leg B: the stub must not be written over or appended to a real assessment; "
        f"seen {seen!r}"
    )


def test_ac15_complex_composite_doc_is_not_overwritten_by_the_abort_stub_gh1626b(tmp_path, monkeypatch):
    """AC15 (MAJOR-4a): the COMPLEX composite the engine renders for itself
    (phase_6_review.py:3507-3522) contains neither ``SCORE:`` nor ``VERDICT:`` —
    only table columns and ``- Median score:``. Under the blacklist predicate the
    round-2 spec used, that REAL three-evaluator assessment reads as "not real"
    and gets overwritten by a NOT_ASSESSED stub: the engine would fabricate the
    absence of evidence it had already collected.

    Leg A (refresh on our marker) is the forcing leg and is RED today; leg B is
    the discrimination that must hold after GREEN.
    """
    _assert_marker_whitelist(
        tmp_path, monkeypatch, "AC15", "ac15", _COMPLEX_COMPOSITE_DOC
    )


def test_ac16_markdown_score_doc_is_not_overwritten_by_the_abort_stub_gh1626b(tmp_path, monkeypatch):
    """AC16 (MAJOR-4b): the single-evaluator doc is raw LLM stdout, and the
    production parser is IGNORECASE + markdown-tolerant (phase_6_review.py:535),
    so ``**Score: 62**`` IS a real (failing) assessment carrying no ``SCORE:``
    token. The fixture is validated against that PRODUCTION parser rather than
    against my reading of it, so the claim "this is a real assessment" is
    measured, not asserted.
    """
    from bytedigger_engine.workflows.phase_6_review import _parse_satisfaction_score

    parsed = _parse_satisfaction_score(_MARKDOWN_SCORE_DOC)
    assert parsed == 62, (
        f"AC16 fixture: the production parser (phase_6_review.py:535) must read this doc as "
        f"a REAL score of 62 — otherwise the AC is arguing about a document the engine does "
        f"not consider an assessment. Seen {parsed!r} for:\n{_MARKDOWN_SCORE_DOC!r}"
    )

    _assert_marker_whitelist(
        tmp_path, monkeypatch, "AC16", "ac16", _MARKDOWN_SCORE_DOC
    )


# ═══ AC17 (MAJOR-4c) — the dangerous ordering: write at :3187, abort at :3314 ═


_AC17_RAW = _MARKDOWN_SCORE_DOC


def _real_write_satisfaction_step() -> StepContract:
    """The REAL production ``write_satisfaction_doc`` StepContract, taken from
    ``phase_6_review_workflow()`` itself (phase_6_review.py:5459) — not a copy,
    not a stand-in. It writes the doc at :3187 and returns the terminal
    E_SATISFACTION_BELOW_THRESHOLD at :3314, both for real.
    """
    hits = [s for s in phase_6_review_workflow().steps if s.name == "write_satisfaction_doc"]
    assert len(hits) == 1, (
        f"AC17 fixture: expected exactly one 'write_satisfaction_doc' step in "
        f"phase_6_review_workflow(); seen {len(hits)}"
    )
    return hits[0]


def _sat_feeder_step(name: str, scratchpad: Path, raw: str) -> StepContract:
    """Harness-only predecessor: supplies exactly the ``prev.data`` contract that
    ``invoke_satisfaction_llm`` supplies in production (raw_response + the four
    paths read at phase_6_review.py:3183-3184/3236/3271-3273). The UUT itself is
    NOT stubbed — only its upstream LLM step is replaced by its own output.
    """
    docs = scratchpad / "reviews"
    docs.mkdir(parents=True, exist_ok=True)
    spec = docs / "spec.md"
    spec.write_text("# spec\n\nNo AC ids here, so the checklist gate skips.\n")
    review = docs / "build-review.md"
    review.write_text("# Review\n\nSeverity: HIGH\n")
    fix = docs / "build-fix.md"
    fix.write_text("FIX COMPLETE\n")

    def _fn(_ctx, _prev):
        return StepResult(
            status="ok",
            data={
                "raw_response": raw,
                "doc_path": str(scratchpad / SATISFACTION_DOC_RELPATH),
                "spec_path": str(spec),
                "review_doc_path": str(review),
                "fix_doc_path": str(fix),
            },
            duration_ms=0,
            step_name=name,
        )

    return StepContract(name=name, execute=_fn)


def _run_real_satisfaction_gate(
    tmp_path, monkeypatch, wf_name: str, scratchpad: Path, handler
) -> dict[str, Any]:
    """Drive the REAL satisfaction step through the REAL executor
    (lib/phase_sentinel.py:308) with the supplied ``error_handler``."""
    _register_workflow(
        monkeypatch,
        wf_name,
        [_sat_feeder_step(f"{wf_name}_feed", scratchpad, _AC17_RAW), _real_write_satisfaction_step()],
        handler,
    )

    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key

    event_log_path = _event_log_path(tmp_path)
    ctx = _ctx_dict(scratchpad)
    run_id = f"gh1626b-{wf_name}"
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id=run_id, step_name="gh1626b"
    )
    key = phase_key(run_id, wf_name, ctx)
    return execute_native_workflow(wf_name, ctx, run_id, event_log_path, key)


def test_ac17_real_failing_assessment_survives_below_threshold_abort_byte_for_byte_gh1626b(tmp_path, monkeypatch):
    """AC17 (MAJOR-4c) — the ordering that would have shipped a lie.

    ``_write_satisfaction_doc`` writes the assessment at phase_6_review.py:3187
    and only THEN runs the hard gate, returning E_SATISFACTION_BELOW_THRESHOLD
    at :3314. So on every below-threshold build the phase terminates with a
    REAL, FAILING assessment already on disk — and a handler that overwrites it
    with a NOT_ASSESSED stub would erase the evidence of the failure and claim
    nobody ever looked. That is fabricating the absence of evidence, the exact
    sin this lot exists to avoid.

    Byte-identity is proven WITHOUT trusting my reading of the writer: leg 1
    runs the SAME real step through the SAME real executor with NO handler
    wired, and its sha256 is the reference (§: the reference comes from outside
    the artifact — here, from production itself). Leg 2 runs it with the REAL
    ``phase_6_review_workflow().error_handler`` and must produce the identical
    digest.

    RED today: ``phase_6_review_workflow()`` declares no ``error_handler`` at
    all, so the first assertion fails; the digest legs are the discrimination
    that must hold after GREEN.
    """
    handler = phase_6_review_workflow().error_handler
    assert handler is not None, (
        "AC17: phase_6_review_workflow() must declare an error_handler (the lot's only one, "
        "_on_phase_6_abort); expected a callable, seen None — WorkflowDefinition.error_handler "
        "(contracts.py:317) is still unwired at the phase-6 end."
    )

    # ── leg 1: reference digest — the same real step, no handler wired ───────
    ref_scratch = Path(realpath(str(tmp_path))) / "ac17-reference"
    ref_scratch.mkdir(parents=True, exist_ok=True)
    r_ref = _run_real_satisfaction_gate(
        tmp_path, monkeypatch, "gh1626b_ac17_ref", ref_scratch, None
    )
    assert r_ref["error_code"] == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC17 fixture (leg 1): the real gate must abort below threshold (score 62); got "
        f"{r_ref.get('error_code')!r} (error={r_ref.get('error')!r})"
    )
    ref_bytes = (ref_scratch / SATISFACTION_DOC_RELPATH).read_bytes()
    digest_before = hashlib.sha256(ref_bytes).hexdigest()
    assert b"NOT_ASSESSED" not in ref_bytes, (
        f"AC17 fixture (leg 1): with no handler wired the doc on disk must be the REAL "
        f"assessment production wrote at :3187; seen {ref_bytes!r}"
    )

    # ── leg 2: the same run WITH the real phase-6 handler ────────────────────
    scratch = Path(realpath(str(tmp_path))) / "ac17-handled"
    scratch.mkdir(parents=True, exist_ok=True)
    result = _run_real_satisfaction_gate(
        tmp_path, monkeypatch, "gh1626b_ac17", scratch, handler
    )
    assert result["error_code"] == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC17 fixture (leg 2): expected the same real abort; got "
        f"{result.get('error_code')!r} (error={result.get('error')!r})"
    )

    sat = scratch / SATISFACTION_DOC_RELPATH
    seen_bytes = sat.read_bytes()
    digest_after = hashlib.sha256(seen_bytes).hexdigest()
    assert digest_after == digest_before, (
        f"AC17: the REAL failing assessment written at phase_6_review.py:3187 must survive "
        f"the E_SATISFACTION_BELOW_THRESHOLD abort BYTE-FOR-BYTE. sha256 expected "
        f"{digest_before} ({ref_bytes!r}), seen {digest_after} ({seen_bytes!r}) at {sat}."
    )
    assert b"SATISFACTION: NOT_ASSESSED" not in seen_bytes, (
        f"AC17: the abort stub must never replace a real assessment — acceptance WAS "
        f"evaluated here and it FAILED; seen {seen_bytes!r}"
    )


# ═══ AC18 (§1o) — the handler-raised warning event has ONE canonical name ════
#
# ``_HANDLER_RAISED_EVENT`` is declared above the AC9 section: gate round 3
# condition 3 narrowed AC9 onto the same exact name, so both ACs now key on one
# constant rather than on two independent predicates.


def test_ac18_handler_raised_event_has_the_canonical_name_gh1626b(tmp_path, monkeypatch):
    """AC18: the event name is a sentinel (§1o) — assert it EXACTLY, by name,
    never by substring, so GREEN cannot invent a name per execution path and
    leave ``rollout-completion-check.sh`` (or any downstream consumer) with
    nothing to key on.

    RED today: the hook is unwired, so no event of any name is emitted.
    """
    name = "gh1626b_ac18"

    def _raising_handler(_result, _ctx):
        raise RuntimeError(_HANDLER_BOOM)

    _register_workflow(monkeypatch, name, [_terminal_step_contract(name)], _raising_handler)

    result, event_log_path = _drive_native(name, tmp_path, "gh1626b-ac18")

    _assert_original_terminal_survived(result, "AC18")

    evs = _events(event_log_path)
    names = [str(e.get("event_type")) for e in evs]
    hits = [e for e in evs if e.get("event_type") == _HANDLER_RAISED_EVENT]
    assert len(hits) == 1, (
        f"AC18: exactly one event named EXACTLY {_HANDLER_RAISED_EVENT!r} must record the "
        f"contained handler exception; expected 1, seen {len(hits)}. Event types on the log: "
        f"{sorted(set(names))!r}"
    )
    payload = json.dumps(hits[0].get("payload") or {})
    assert _HANDLER_BOOM in payload or "RuntimeError" in payload, (
        f"AC18: the {_HANDLER_RAISED_EVENT!r} payload must carry the exception (message or "
        f"type) — a contained exception that leaves no trace is indistinguishable from a "
        f"handler that never fired; seen payload {payload!r}"
    )


# ═══ AC19 (gate r3, condition 1) — the STUCK REPORT survives the aliasing ════


def test_ac19_aliasing_handler_does_not_disarm_the_stuck_report_gh1626b(tmp_path, monkeypatch):
    """AC19: AC14 forces the OUTCOME (the dict ``execute_native_workflow``
    returns), not the MECHANISM. A GREEN that calls the handler on the LIVE
    ``StepResult`` early in ``WorkflowEngine.execute`` and returns a pre-call
    snapshot would pass AC14 while ``engine.py:304`` (dispatcher report) and
    ``engine.py:313-326`` (stuck report) still read the MUTATED object — and
    nothing in this file asserts either of those. This is that assertion.

    The real observable, read from production and not proxied: engine.py:313
    gates on ``final_result.recoverable is False`` and, when the event log has a
    path, writes ``<event_log_dir>/stuck-report.json`` via
    ``lib/stuck_report.build_stuck_report`` + ``emit_stuck_report``
    (``stuck_report.py:50``/``:79``, filename ``STUCK_REPORT_FILENAME``). Its
    ``error_code`` field is ``final_result.error_code`` and its ``last_error``
    is ``final_result.error``, both read AFTER the handler has run. So under the
    AC14 aliasing handler:

      * ``result.status = "ok"`` in place  ⇒ engine.py:303 skips the whole block
        ⇒ NO stuck report on disk at all;
      * ``result.recoverable = True`` in place ⇒ engine.py:313 is False ⇒ NO
        stuck report on disk;
      * ``result.error_code = "E_GH1626B_ALIASED"`` in place ⇒ a stuck report
        that MISNAMES the failure the operator has to fix.

    Every one of those is caught below, on the file the engine actually writes.
    The file's mere EXISTENCE is the observable for ``recoverable is False`` —
    that flag has no field of its own in the schema; it is what decides whether
    the report is emitted.

    Forcing shape (§1l, real production side-effect): a per-test stub cannot
    satisfy this. The mutation happens inside the real handler, on whatever
    object the real executor hands it, and the assertion reads a file written by
    unmodified production code three call frames away. The only way both the
    handler firing AND the intact report hold is if the executor never lets the
    handler touch the object engine.py:313 reads.

    §1i (pre-staged, not raced): the report directory is created and asserted
    EMPTY of ``stuck-report.json`` before the run, so "file present" cannot be
    leakage from a sibling test or a previous run — nothing here is timing- or
    ordering-dependent.

    RED today: ``WorkflowDefinition.error_handler`` (contracts.py:317) has zero
    production consumers, so the handler never fires and the fire-once assertion
    fails first. The report assertions are the invariants that must ALSO hold
    after GREEN — they are what forbid the snapshot-shaped GREEN.
    """
    from bytedigger_engine.lib.stuck_report import STUCK_REPORT_FILENAME

    name = "gh1626b_ac19"
    real_tmp = Path(realpath(str(tmp_path)))
    marker = real_tmp / "ac19-handler-calls.jsonl"

    # ── §1i pre-stage: the contested artifact must not pre-exist ─────────────
    report_dir = Path(_event_log_path(tmp_path)).parent
    stuck_path = report_dir / STUCK_REPORT_FILENAME
    assert not stuck_path.exists(), (
        f"AC19 fixture: {stuck_path} must not exist before the run — otherwise 'the stuck "
        f"report survived' would be measuring leaked state, not this phase."
    )

    def _aliasing_handler(result: StepResult, _ctx) -> None:
        """Byte-identical intent to AC14's handler: mutate the ARGUMENT in place
        and return nothing, so the discard-the-return rule cannot save us."""
        with marker.open("a") as fh:
            fh.write(
                json.dumps({"code": result.error_code, "recoverable": result.recoverable}) + "\n"
            )
        result.recoverable = True
        result.error_code = "E_GH1626B_ALIASED"
        result.error = "gh1626b ALIASED error text"
        result.status = "ok"

    _register_workflow(monkeypatch, name, [_terminal_step_contract(name)], _aliasing_handler)

    result, _ = _drive_native(name, tmp_path, "gh1626b-ac19")

    calls = _handler_calls(marker)
    assert len(calls) == 1, (
        f"AC19: the declared error_handler must fire exactly once for the terminal result — "
        f"without it firing there is no aliasing to defend against and this AC is vacuous; "
        f"expected 1 call, seen {len(calls)} ({calls!r}). WorkflowDefinition.error_handler "
        f"(contracts.py:317) is still unwired."
    )

    # The outcome half (same invariant AC14 pins) — kept so a failure here is
    # attributable to the same root cause without cross-reading AC14.
    _assert_original_terminal_survived(result, "AC19")

    # ── the mechanism half: what engine.py:313-326 actually wrote ───────────
    assert stuck_path.exists(), (
        f"AC19: a terminal, non-recoverable phase must still emit the stuck report at "
        f"{stuck_path} (engine.py:313 gates on final_result.recoverable is False; "
        f"engine.py:303 gates on the status being 'error'). It is absent, which means the "
        f"handler's in-place mutation (status='ok' / recoverable=True) reached the object "
        f"engine.py reads — returning a pre-call snapshot from execute() is NOT enough; the "
        f"handler must never receive the live result. Files in {report_dir}: "
        f"{sorted(p.name for p in report_dir.iterdir())!r}"
    )

    report = json.loads(stuck_path.read_text())
    assert report.get("error_code") == "E_GH1626B_BOOM", (
        f"AC19: the stuck report must name the ORIGINAL failure the operator has to fix; "
        f"expected error_code 'E_GH1626B_BOOM', seen {report.get('error_code')!r}. "
        f"engine.py:321 passes final_result.error_code AFTER the handler ran, so an aliased "
        f"code here means the handler mutated the live object. Report: {report!r}"
    )
    assert report.get("last_error") == _ORIG_ERROR_TEXT, (
        f"AC19: the stuck report's last_error is final_result.error (engine.py:324); expected "
        f"{_ORIG_ERROR_TEXT!r}, seen {report.get('last_error')!r}"
    )
    assert report.get("step_name") == name and report.get("breaker") == "terminal_failure", (
        f"AC19: expected the terminal-failure stuck report for step {name!r} "
        f"(engine.py:319-320); seen step_name={report.get('step_name')!r}, "
        f"breaker={report.get('breaker')!r}"
    )
    assert "E_GH1626B_ALIASED" not in json.dumps(report, default=str), (
        f"AC19: no trace of the handler's in-place mutation may reach the stuck report; "
        f"seen {report!r}"
    )
