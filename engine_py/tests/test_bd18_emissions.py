"""RED round 1 — bd#18: engine conformance emissions (`phase`, `run_identity`,
`phase_artifacts`). Frozen spec: engine_py/conformance/EMISSIONS_SPEC.md.

No conformance checker exists for this lot (§6 out of scope) — every test
drives the real WorkflowEngine + EventLog directly and reads the log back.
Nothing here patches `is_authoritative_execution` — pytest runs on the main
thread by construction, so every emit here is authoritative/unshadowed
already; shadowed runs are out of scope for this lot (§6).

conftest.py already inserts the engine_py root onto sys.path at collection
time (the canonical §1q seam) — no module-level sys.path manipulation here.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import derive_state
import package_meta
from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from engine import WorkflowEngine
from event_log import EventLog
from lib import git_port


# ─── shared fixtures / helpers ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_ambient_git(tmp_path, monkeypatch):
    """Engine tests must not read the ambient repo working tree (GH780)."""
    monkeypatch.chdir(tmp_path)


def make_ctx(
    *,
    git_cwd: str | None = None,
    phase_reroute: dict | None = None,
    scratchpad_dir: str | None = None,
) -> WorkflowContext:
    org_config: dict[str, Any] = {}
    if git_cwd is not None:
        org_config["git_cwd"] = git_cwd
    if phase_reroute is not None:
        org_config["phase_reroute"] = phase_reroute
    if scratchpad_dir is not None:
        org_config["scratchpad_dir"] = scratchpad_dir
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config=org_config or None,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )


def ok_step(name: str) -> StepContract:
    def _run(_ctx, _prev):
        return StepResult(status="ok", data=None, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def write_step(name: str, base: Path, *relpaths: str) -> StepContract:
    """A step that writes each of ``relpaths`` (posix-style, relative to
    ``base``) with trivial content, then returns ok."""
    def _run(_ctx, _prev):
        for rel in relpaths:
            p = base / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
        return StepResult(status="ok", data=None, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def error_step(name: str) -> StepContract:
    def _run(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error="boom", error_code="E_TEST", recoverable=False,
        )
    return StepContract(name=name, execute=_run)


def escalate_step(name: str) -> StepContract:
    def _run(_ctx, _prev):
        return StepResult(status="escalate", data=None, duration_ms=0, step_name=name, error_code="E_ESC")
    return StepContract(name=name, execute=_run)


def raising_step(name: str, exc: Exception) -> StepContract:
    def _run(_ctx, _prev):
        raise exc
    return StepContract(name=name, execute=_run)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path) -> Path:
    """A real git repo with one committed seed file. Never chdir'd into —
    org_config['git_cwd'] is always passed explicitly (GH1082)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def make_log(tmp_path, name: str = "events.jsonl") -> EventLog:
    return EventLog(path=tmp_path / name)


def events_of(log: EventLog, event_type: str) -> list[dict]:
    """Full event dicts ({ts, run_id, event_type, payload}) of one type."""
    return [e for e in log.read_all() if e["event_type"] == event_type]


def index_of_event_type(events: list[dict], event_type: str, occurrence: int = 0) -> int:
    seen = 0
    for i, e in enumerate(events):
        if e["event_type"] == event_type:
            if seen == occurrence:
                return i
            seen += 1
    raise AssertionError(f"event_type {event_type!r} occurrence {occurrence} not found in {events!r}")


def _run_identity_payload(log: EventLog, occurrence: int = 0) -> dict:
    events = events_of(log, "run_identity")
    assert len(events) > occurrence, f"expected a run_identity event at occurrence {occurrence}"
    return events[occurrence]["payload"]


def _pyproject_path() -> Path:
    return Path(__file__).parent.parent / "pyproject.toml"


def _read_pyproject_version() -> str:
    """Read [project].version out of THIS engine_py's real pyproject.toml —
    the ground truth the source-fallback path is expected to reproduce."""
    text = _pyproject_path().read_text()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("pyproject.toml has no top-level version line")


def _expected_digest(paths: list[str]) -> str:
    joined = "\n".join(sorted(paths))
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


class _AlwaysRaisingEventLog:
    """Duck-typed event log whose append() ALWAYS raises, but records which
    event_type was attempted before raising."""

    def __init__(self) -> None:
        self.attempted: list[str] = []
        self.path = None

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.attempted.append(event_type)
        raise RuntimeError(f"append always fails for {event_type}")


# ─── §2 Additivity ─────────────────────────────────────────────────────────


_PRE_CHANGE_STEP_STARTED_KEYS = frozenset({"step_name"})
_PRE_CHANGE_STEP_FINISHED_KEYS = frozenset({"step_name", "status", "duration_ms", "error"})


def test_ac_n1_step_events_retain_every_pre_change_key_plus_phase(tmp_path):
    """AC-N1: step_started/step_finished retain every key they carried
    before this lot, unchanged, alongside the new `phase` key. Kills a
    GREEN that renames/drops an existing key (e.g. step_name->name) while
    adding phase — checked by exact key-set equality against the frozen
    pre-change key sets, not merely by checking `phase in payload`.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    eng.execute("wf", make_ctx(), run_id="r1")

    started = events_of(log, "step_started")
    finished = events_of(log, "step_finished")
    assert len(started) == 1 and len(finished) == 1
    assert set(started[0]["payload"].keys()) == _PRE_CHANGE_STEP_STARTED_KEYS | {"phase"}, (
        f"got {sorted(started[0]['payload'].keys())!r}"
    )
    assert set(finished[0]["payload"].keys()) == _PRE_CHANGE_STEP_FINISHED_KEYS | {"phase"}, (
        f"got {sorted(finished[0]['payload'].keys())!r}"
    )


def test_ac_n2_derive_state_replay_unaffected_by_additive_phase_key(tmp_path):
    """AC-N2: derive_state.py's replay() — the regression surface that
    actually matters (a reader, not just the writer) — is unaffected by the
    additive `phase` key. Kills a GREEN that changes an EXISTING key's type
    or meaning instead of purely adding `phase`.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1"), ok_step("s2")]))
    eng.execute("wf", make_ctx(), run_id="r1")

    state = derive_state.replay(log.read_all())
    run = state["runs"]["r1"]
    assert run["workflow_name"] == "wf"
    assert run["status"] == "ok"
    assert [s["step_name"] for s in run["steps"]] == ["s1", "s2"]
    assert all(s["status"] == "ok" for s in run["steps"])


def test_ac_n3_no_new_third_party_import_on_version_resolution_path(tmp_path, monkeypatch):
    """AC-N3: version resolution (feeding the new run_identity emission)
    adds no new hard third-party dependency — only importlib.metadata
    (stdlib) and a file read. Kills a GREEN that reaches for a TOML/YAML
    parsing library for the pyproject.toml fallback: the metadata seam is
    forced to miss (so the fallback path is exercised) while those
    third-party modules are made unimportable; version must still resolve
    to this repo's REAL pyproject.toml value.
    """
    import sys

    for name in ("toml", "tomli", "yaml", "pkg_resources"):
        monkeypatch.setitem(sys.modules, name, None)  # `import <name>` -> ImportError

    def _raise_not_found(name):
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib_metadata, "version", _raise_not_found)

    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    result, _ = eng.execute("wf", make_ctx(), run_id="r1")

    assert result.status == "ok", (
        "execute() must complete normally with third-party TOML/YAML libs "
        "unimportable and the metadata seam missing"
    )
    identities = events_of(log, "run_identity")
    assert len(identities) == 1, "run_identity must still be emitted"
    assert identities[0]["payload"].get("engine_version") == _read_pyproject_version(), (
        f"expected the real pyproject.toml fallback version "
        f"{_read_pyproject_version()!r}, got "
        f"{identities[0]['payload'].get('engine_version')!r} — resolved "
        "without toml/tomli/yaml/pkg_resources"
    )


# ─── §3 Emission 1 — `phase` on step events ────────────────────────────────


def test_ac_e1_step_events_carry_phase(tmp_path):
    """AC-E1: step_started (engine.py:370) and step_finished (engine.py:472)
    payloads carry `phase`. Kills a GREEN that never adds the key.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("workflow_alpha", WorkflowDefinition(name="workflow_alpha", steps=[ok_step("s1")]))
    eng.execute("workflow_alpha", make_ctx(), run_id="r1")

    started = events_of(log, "step_started")[0]["payload"]
    finished = events_of(log, "step_finished")[0]["payload"]
    assert "phase" in started
    assert "phase" in finished


def test_ac_e1b_phase_present_on_every_step_event_in_multistep_phase(tmp_path):
    """AC-E1b (§0.1 quantified over the steps of a phase): a first-step-only
    or last-step-only `phase` implementation is the plausible wrong GREEN
    here, and a single-step fixture cannot see it. Asserted over a >=3-step
    workflow: EVERY step_started and EVERY step_finished carries `phase`,
    checked per event.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    steps = [ok_step("first"), ok_step("middle"), ok_step("last")]
    eng.register("multi_phase", WorkflowDefinition(name="multi_phase", steps=steps))
    eng.execute("multi_phase", make_ctx(), run_id="r1")

    started = events_of(log, "step_started")
    finished = events_of(log, "step_finished")
    assert len(started) == 3 and len(finished) == 3
    for evt in started + finished:
        assert "phase" in evt["payload"], (
            f"step event for {evt['payload'].get('step_name')!r} is missing "
            "'phase' — a first/last-step-only implementation loses it here"
        )


def test_ac_e1c_phase_value_equals_workflow_name_exactly(tmp_path):
    """AC-E1c: `phase` MUST equal the workflow name BY EQUALITY, not merely
    be non-empty (§0.5). Workflow name is distinct from every step name, so
    a GREEN emitting a constant, the step name, or "" fails.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    workflow_name = "distinct_workflow_name_zz9k"
    steps = [ok_step("alpha_step"), ok_step("beta_step")]
    eng.register(workflow_name, WorkflowDefinition(name=workflow_name, steps=steps))
    eng.execute(workflow_name, make_ctx(), run_id="r1")

    for evt in events_of(log, "step_started") + events_of(log, "step_finished"):
        assert evt["payload"].get("phase") == workflow_name, (
            f"expected phase == {workflow_name!r}, got "
            f"{evt['payload'].get('phase')!r} for step "
            f"{evt['payload'].get('step_name')!r}"
        )


def test_ac_e1d_phase_present_on_both_pre_and_post_retry_frames(tmp_path):
    """AC-E1d (§0.1 quantified over the frames of a phase): `_execute_steps`
    re-enters itself recursively on a recoverable retry (engine.py:638-645),
    and the outer frame then returns `retry_result` (:657) WITHOUT running
    its own tail. A GREEN that adds `phase` only in the outer (pre-retry)
    call loses it exactly on the recursive (post-retry) frame's events.
    Non-uniform by construction: one step, first call returns a recoverable
    retry (cycle 1), the recursive re-entry (cycle 2) returns ok.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    calls = {"n": 0}

    def _flaky(_ctx, _prev):
        calls["n"] += 1
        if calls["n"] == 1:
            return StepResult(
                status="error", data={"retry_from_step": 0, "cycle_count": 1, "findings": ""},
                duration_ms=0, step_name="flaky", error_code="E_RETRY", recoverable=True,
            )
        return StepResult(status="ok", data=None, duration_ms=0, step_name="flaky")

    eng.register("retry_phase", WorkflowDefinition(
        name="retry_phase", steps=[StepContract(name="flaky", execute=_flaky)],
    ))
    eng.execute("retry_phase", make_ctx(), run_id="r1")

    started = events_of(log, "step_started")
    finished = events_of(log, "step_finished")
    assert len(started) == 2, "expected one step_started per frame (pre-retry, post-retry)"
    assert len(finished) == 2, "expected one step_finished per frame (pre-retry, post-retry)"
    for evt in started + finished:
        assert evt["payload"].get("phase") == "retry_phase", (
            f"phase missing/wrong on a retry-frame event: {evt['payload']!r} — a "
            "GREEN adding phase only on the pre-retry frame fails this on the "
            "post-retry pair"
        )


# ─── §4 Emission 2 — `run_identity` ────────────────────────────────────────


def test_ac_e2_run_identity_immediately_follows_this_calls_workflow_started(tmp_path):
    """AC-E2: run_identity is emitted once per execute() call, as the event
    immediately following THAT call's workflow_started — located by the
    INDEX of workflow_started, not position 0 (bd#7 [G:MINOR-10]):
    engine.py:250-268 emits phase_reroute_entry first whenever
    context.org_config['phase_reroute'] is set (and durably consumable via
    scratchpad_dir), so events[0] is a fixture property, not an engine
    invariant. Asserted WITH phase_reroute set so a position-0
    implementation fails.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    ctx = make_ctx(
        phase_reroute={"attempt": 1, "from_phase": "prior_phase"},
        scratchpad_dir=str(tmp_path / "scratch"),
    )
    eng.execute("wf", ctx, run_id="r1")

    events = log.read_all()
    assert events[0]["event_type"] == "phase_reroute_entry", (
        "fixture sanity: with phase_reroute set, events[0] must be "
        "phase_reroute_entry, not workflow_started — otherwise this test "
        "isn't exercising the located-by-index requirement"
    )
    ws_index = index_of_event_type(events, "workflow_started")
    assert ws_index + 1 < len(events), "no event follows workflow_started at all"
    following = events[ws_index + 1]
    assert following["event_type"] == "run_identity", (
        f"expected run_identity immediately after workflow_started (index "
        f"{ws_index}), got {following['event_type']!r} at index {ws_index + 1} "
        "— a position-0 implementation would look for run_identity at "
        "events[0], which is phase_reroute_entry here"
    )
    assert len(events_of(log, "run_identity")) == 1


def test_ac_e2b_adapter_identity_is_mapping_with_nonempty_backend_and_source(tmp_path, monkeypatch):
    """AC-E2b: adapter_identity MUST be a mapping with non-empty `backend`
    and `source` — not a bare string (bd#7 [G2:8]: a bare-string fixture
    pinned a weaker contract than the engine emits). Asserted by shape AND
    by value against the configured backend (HAL_RUNNER_BACKEND — the
    engine's one existing backend-selection seam, pinned per-test to
    'claude-subprocess' by conftest's autouse fixture) across two DIFFERENT
    configured values, so a constant fails.
    """
    def _run_once(backend_env: str, subdir: str) -> dict:
        monkeypatch.setenv("HAL_RUNNER_BACKEND", backend_env)
        log = make_log(tmp_path, name=f"{subdir}.jsonl")
        eng = WorkflowEngine(event_log=log)
        eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
        eng.execute("wf", make_ctx(), run_id="r1")
        return _run_identity_payload(log)["adapter_identity"]

    identity_a = _run_once("claude-subprocess", "a")
    identity_b = _run_once("claude-in-session", "b")

    for identity in (identity_a, identity_b):
        assert isinstance(identity, dict), f"adapter_identity must be a mapping, got {type(identity)}"
        assert identity.get("backend"), "adapter_identity['backend'] must be non-empty"
        assert identity.get("source"), "adapter_identity['source'] must be non-empty"

    assert identity_a["backend"] == "claude-subprocess", (
        f"expected backend to reflect HAL_RUNNER_BACKEND=claude-subprocess, "
        f"got {identity_a['backend']!r}"
    )
    assert identity_b["backend"] == "claude-in-session", (
        f"expected backend to reflect HAL_RUNNER_BACKEND=claude-in-session, "
        f"got {identity_b['backend']!r}"
    )
    assert identity_a["backend"] != identity_b["backend"], (
        "a constant/hardcoded adapter_identity['backend'] would be identical "
        "across differently-configured runs"
    )


def test_ac_e2c_engine_version_resolves_via_importlib_metadata_first(tmp_path, monkeypatch):
    """AC-E2c: metadata-first resolution — importlib.metadata.version(...)
    is tried before any pyproject.toml read, called with
    package_meta.PACKAGE_DIST_NAME (the imported constant), not a bare
    literal (bd#7 had this normative but asserted the literal on both
    sides, unable to discriminate a rename that updates one and not the
    other).
    """
    calls: list[str] = []

    def _fake_version(name):
        calls.append(name)
        return "9.9.9"

    monkeypatch.setattr(importlib_metadata, "version", _fake_version)

    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    eng.execute("wf", make_ctx(), run_id="r1")

    payload = _run_identity_payload(log)
    assert payload.get("engine_version") == "9.9.9", (
        f"expected metadata-resolved version '9.9.9', got {payload.get('engine_version')!r}"
    )
    assert calls == [package_meta.PACKAGE_DIST_NAME], (
        f"expected importlib.metadata.version called with the imported "
        f"PACKAGE_DIST_NAME constant {package_meta.PACKAGE_DIST_NAME!r}, "
        f"got calls={calls!r}"
    )


def test_ac_e2c_engine_version_falls_back_to_pyproject_when_metadata_absent(tmp_path, monkeypatch):
    """AC-E2c: source-checkout fallback — when importlib.metadata.version
    raises PackageNotFoundError, resolution reads engine_py/pyproject.toml
    [project].version via Path.read_text(), patched PATH-CONDITIONALLY
    (§0.6 seam pin) so every OTHER Path.read_text call in the process is
    untouched — a blanket patch would break unrelated engine reads.
    """
    def _raise_not_found(name):
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib_metadata, "version", _raise_not_found)

    real_read_text = Path.read_text
    target = _pyproject_path()

    def _conditional_read_text(self, *args, **kwargs):
        if self == target:
            return '[project]\nname = "bytedigger-engine"\nversion = "7.7.7"\n'
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _conditional_read_text)

    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    eng.execute("wf", make_ctx(), run_id="r1")

    payload = _run_identity_payload(log)
    assert payload.get("engine_version") == "7.7.7", (
        f"expected pyproject-fallback version '7.7.7', got {payload.get('engine_version')!r}"
    )


@pytest.mark.parametrize("source_effect", ["oserror", "missing_version_key", "parse_error"])
def test_ac_e2c_failure_contract_absent_no_placeholder_no_propagation(tmp_path, monkeypatch, source_effect):
    """AC-E2c: 'does not resolve' covers any OSError subclass, KeyError (no
    [project].version), and any parse error from the reader — none may
    propagate out of execute(), and engine_version is absent/empty, NEVER
    a placeholder ('unknown', '0.0.0', '0+unknown' all forbidden by §0.5).
    Metadata always misses here (PackageNotFoundError); the pyproject-read
    seam is patched path-conditionally per the three failure modes.
    """
    def _raise_not_found(name):
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib_metadata, "version", _raise_not_found)

    real_read_text = Path.read_text
    target = _pyproject_path()

    def _conditional_read_text(self, *args, **kwargs):
        if self != target:
            return real_read_text(self, *args, **kwargs)
        if source_effect == "oserror":
            raise PermissionError(f"blocked: {self}")
        if source_effect == "missing_version_key":
            return '[project]\nname = "bytedigger-engine"\n'  # no version key -> KeyError-shaped
        return "not valid toml [[[ at all"  # parse error

    monkeypatch.setattr(Path, "read_text", _conditional_read_text)

    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    result, _ = eng.execute("wf", make_ctx(), run_id="r1")

    assert result.status == "ok", (
        f"execute() must complete normally through a {source_effect} failure; "
        f"got status={result.status!r} error={result.error!r}"
    )
    payload = _run_identity_payload(log)
    version = payload.get("engine_version")
    assert not version, (
        f"engine_version must be absent or empty when neither seam resolves "
        f"({source_effect}), got {version!r}"
    )
    assert version not in ("unknown", "0.0.0", "0+unknown"), (
        f"engine_version must never be a placeholder sentinel, got {version!r}"
    )


def test_ac_e2c_version_resolution_not_memoized_across_two_resolutions(tmp_path, monkeypatch):
    """AC-E2c: resolution MUST NOT be memoised — resolved per run_identity
    emit, not cached across runs. An @lru_cache or module-level
    `_VERSION = _resolve()` would make the second call keep the first
    seam's value even after the seam changes; this suite runs
    pytest-randomly (§0.3), so a cached first value is a real, not
    hypothetical, defect. Asserted by resolving twice in one process with
    the seam changed in between.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))

    monkeypatch.setattr(importlib_metadata, "version", lambda name: "1.1.1")
    eng.execute("wf", make_ctx(), run_id="r1")

    monkeypatch.setattr(importlib_metadata, "version", lambda name: "2.2.2")
    eng.execute("wf", make_ctx(), run_id="r2")

    identities = [e["payload"] for e in events_of(log, "run_identity")]
    assert len(identities) == 2
    assert identities[0].get("engine_version") == "1.1.1", (
        f"first run_identity should resolve '1.1.1', got {identities[0].get('engine_version')!r}"
    )
    assert identities[1].get("engine_version") == "2.2.2", (
        f"second run_identity should resolve '2.2.2' (seam changed between "
        f"calls) — a memoised implementation would repeat '1.1.1', got "
        f"{identities[1].get('engine_version')!r}"
    )


def test_ac_e2d_run_identity_emitted_per_execute_call_not_cached(tmp_path, monkeypatch):
    """AC-E2d (§0.1 quantified over the execute() calls of one engine
    instance): execute() resets per-run state at engine.py:237-243; a
    GREEN guarding the emit with an instance flag it forgets to reset
    emits for run 1 only. Three execute() calls on ONE engine instance,
    each under a different run_id: EACH call's workflow_started MUST be
    immediately followed by its OWN run_identity, with correct per-call
    payloads — non-uniform via a third call whose version seam is
    changed, so a cached-first-value implementation fails.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))

    monkeypatch.setattr(importlib_metadata, "version", lambda name: "1.0.0")
    eng.execute("wf", make_ctx(), run_id="run-a")
    eng.execute("wf", make_ctx(), run_id="run-b")
    monkeypatch.setattr(importlib_metadata, "version", lambda name: "3.0.0")
    eng.execute("wf", make_ctx(), run_id="run-c")

    events = log.read_all()
    ws_events = [
        (i, e) for i, e in enumerate(events)
        if e["event_type"] == "workflow_started" and e["run_id"] in ("run-a", "run-b", "run-c")
    ]
    assert len(ws_events) == 3, "expected exactly one workflow_started per execute() call"

    versions_by_run: dict[str, Any] = {}
    for idx, ws in ws_events:
        assert idx + 1 < len(events), f"no event follows workflow_started for run_id={ws['run_id']!r}"
        following = events[idx + 1]
        assert following["event_type"] == "run_identity", (
            f"run_id={ws['run_id']!r}: expected run_identity immediately after "
            f"its own workflow_started, got {following['event_type']!r}"
        )
        assert following["run_id"] == ws["run_id"], (
            "run_identity must carry the SAME run_id as the workflow_started it follows"
        )
        versions_by_run[ws["run_id"]] = following["payload"].get("engine_version")

    assert len(events_of(log, "run_identity")) == 3, (
        "expected exactly one run_identity per execute() call, three total"
    )
    assert versions_by_run["run-a"] == "1.0.0"
    assert versions_by_run["run-b"] == "1.0.0"
    assert versions_by_run["run-c"] == "3.0.0", (
        f"third call's changed seam must be reflected, not a cached first "
        f"value — got {versions_by_run['run-c']!r}"
    )


# ─── §5 Emission 3 — `phase_artifacts` ─────────────────────────────────────


def test_ac_e3_exactly_one_phase_artifacts_with_exact_key_set(tmp_path, git_repo):
    """AC-E3: exactly one phase_artifacts per phase; exact key-set equality
    for the untruncated case (phase, written, read, write_tracking,
    read_tracking) — kills a GREEN that always emits the truncation keys
    too. read_tracking == 'declared-only' (this lot adds no read
    instrumentation).
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[write_step("s1", git_repo, "a.txt"), write_step("s2", git_repo, "b.txt")],
    ))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")

    pa = events_of(log, "phase_artifacts")
    assert len(pa) == 1, f"expected exactly one phase_artifacts, got {len(pa)}"
    payload = pa[0]["payload"]
    assert set(payload.keys()) == {"phase", "written", "read", "write_tracking", "read_tracking"}, (
        f"expected exact key set for the untruncated case, got {sorted(payload.keys())!r}"
    )
    assert payload["read_tracking"] == "declared-only"
    assert payload["phase"] == "wf"


def test_ac_e3a_phase_artifacts_emitted_on_ok_exit(tmp_path, git_repo):
    """AC-E3a: emitted from the try/finally around the step loop
    (engine.py:271) on the ok exit path."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", git_repo, "a.txt")]))
    result, _ = eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    assert result.status == "ok"
    assert len(events_of(log, "phase_artifacts")) == 1


def test_ac_e3a_phase_artifacts_emitted_on_error_exit(tmp_path, git_repo):
    """AC-E3a: emitted on the error exit path (engine.py:674)."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[error_step("s1")]))
    result, _ = eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    assert result.status == "error"
    assert len(events_of(log, "phase_artifacts")) == 1


def test_ac_e3a_phase_artifacts_emitted_on_escalate_exit(tmp_path, git_repo):
    """AC-E3a: emitted on the escalate exit path (engine.py:682)."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[escalate_step("s1")]))
    result, _ = eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    assert result.status == "escalate"
    assert len(events_of(log, "phase_artifacts")) == 1


def test_ac_e3a_phase_artifacts_emitted_when_step_raises(tmp_path, git_repo):
    """AC-E3a: a step RAISING (crash path) — engine.py's per-step
    try/finally (around telemetry_ctx only, today) does not catch this;
    phase_artifacts must still be emitted, from the OUTER try/finally at
    engine.py:271, before the exception propagates out of execute().
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[raising_step("s1", RuntimeError("boom"))],
    ))
    with pytest.raises(RuntimeError, match="boom"):
        eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    assert len(events_of(log, "phase_artifacts")) == 1, (
        "phase_artifacts must be emitted from the outer try/finally even "
        "when a step raises, before the exception propagates"
    )


def test_ac_e3a_phase_artifacts_emitted_for_zero_step_workflow(tmp_path, git_repo):
    """AC-E3a: zero-step workflow — _execute_steps returns early at
    engine.py:355-361, BEFORE _scan_cwd resolves at :366; phase_artifacts
    (from the OUTER try/finally) must still be emitted exactly once.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("empty", WorkflowDefinition(name="empty", steps=[]))
    result, _ = eng.execute("empty", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    assert result.status == "ok"
    assert len(events_of(log, "phase_artifacts")) == 1


def test_ac_e3b_git_delta_when_all_steps_succeed_in_real_repo(tmp_path, git_repo):
    """AC-E3b positive control: >=1 step AND a computed delta for EVERY
    step of the phase -> 'git-delta'."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", git_repo, "a.txt")]))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert payload["write_tracking"] == "git-delta"


def test_ac_e3b_not_observed_when_git_cwd_absent(tmp_path):
    """AC-E3b: absent org_config['git_cwd'] -> 'not-observed' —
    _resolve_scan_cwd (engine.py:1062-1065) never falls back to ambient
    cwd, so git_pre is None at :384. Kills a GREEN reading ambient cwd as
    a fallback."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    eng.execute("wf", make_ctx(), run_id="r1")  # no git_cwd at all
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert payload["write_tracking"] == "not-observed"


def test_ac_e3b_not_observed_when_git_cwd_is_non_git_dir(tmp_path):
    """AC-E3b: git_cwd pointing at a non-git directory -> 'not-observed'."""
    non_git = tmp_path / "plain"
    non_git.mkdir()
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", non_git, "a.txt")]))
    eng.execute("wf", make_ctx(git_cwd=str(non_git)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert payload["write_tracking"] == "not-observed"


def test_ac_e3b_not_observed_for_zero_step_workflow(tmp_path, git_repo):
    """AC-E3b: zero steps -> 'not-observed', because _execute_steps returns
    before _scan_cwd resolves, so the engine scanned nothing. all([]) is
    True, so a literal 'every step' reading would wrongly publish
    'git-delta' for a phase never looked at (bd#7's [G4:4])."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("empty", WorkflowDefinition(name="empty", steps=[]))
    eng.execute("empty", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert payload["write_tracking"] == "not-observed", (
        "all([]) is True — an all()-over-empty-list implementation would "
        "wrongly publish git-delta for a phase that scanned nothing"
    )


class _CountingGitPort:
    """Delegates to the real GitReadPort, counting calls (no failure
    injection) — used to MEASURE, not predict, a call-count boundary."""

    def __init__(self) -> None:
        self.n = 0
        self._real = git_port.default_git_read()

    def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
        self.n += 1
        return self._real(args, cwd=cwd, timeout=timeout, dir_=dir_)


class _FailAfterNGitPort:
    """Delegates to the real GitReadPort for the first N calls, then fails
    (non-zero returncode, no exception) every subsequent call."""

    def __init__(self, n_success: int) -> None:
        self._n = n_success
        self.count = 0
        self._real = git_port.default_git_read()

    def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
        self.count += 1
        if self.count <= self._n:
            return self._real(args, cwd=cwd, timeout=timeout, dir_=dir_)
        return git_port.GitResult(returncode=1, stdout="", stderr="injected failure", timed_out=False)


def test_ac_e3b_not_observed_on_partial_delta_failure_across_steps(tmp_path, git_repo):
    """AC-E3b (§0.1 quantified over steps): _git_changes_vs_head returns
    None on timeout/missing-git (engine.py:1082), so an any()-shaped
    implementation would publish 'git-delta' when only SOME step's delta
    computed. Non-uniform via the lib.git_port seam (git_port.py:145-157
    resolves get_git_read() at call time, reaching engine.py:1076/:1079):
    step 1's delta computation succeeds for real; step 2's fails from its
    first git call onward.

    The failure boundary (n_success) is MEASURED live against TODAY's
    unmodified per-step git-snapshot machinery (files_touched, unaffected
    by this lot) rather than a predicted call count, so it cannot drift
    out from under a differently-shaped GREEN the way a byte boundary did
    in bd#7 (§0.2 — this is a call-count measurement of pre-existing,
    unchanged code, not a prediction of new serialised content).
    """
    counting = _CountingGitPort()
    git_port.set_default_git_read_factory(lambda: counting)
    try:
        calib_log = make_log(tmp_path, name="calibration.jsonl")
        calib_eng = WorkflowEngine(event_log=calib_log)
        calib_eng.register("calib", WorkflowDefinition(
            name="calib", steps=[write_step("only_step", git_repo, "calib.txt")],
        ))
        calib_eng.execute("calib", make_ctx(git_cwd=str(git_repo)), run_id="calib")
    finally:
        git_port.reset_default_git_read_factory()
    n_success = counting.n
    assert n_success > 0, "calibration must have observed at least one git_read call"

    failing = _FailAfterNGitPort(n_success)
    git_port.set_default_git_read_factory(lambda: failing)
    try:
        log = make_log(tmp_path, name="actual.jsonl")
        eng = WorkflowEngine(event_log=log)
        eng.register("wf", WorkflowDefinition(
            name="wf",
            steps=[
                write_step("s1", git_repo, "s1_written.txt"),
                write_step("s2", git_repo, "s2_written.txt"),
            ],
        ))
        eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    finally:
        git_port.reset_default_git_read_factory()

    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert payload["write_tracking"] == "not-observed", (
        f"step 2's git delta computation failed (calibrated boundary "
        f"n_success={n_success}); an any()-shaped implementation would "
        f"still publish 'git-delta' here — got {payload['write_tracking']!r}"
    )


def test_ac_e3b_write_channel_never_ran_must_not_publish_written_alongside_git_delta(tmp_path):
    """AC-E3b: a phase whose write channel never ran (no git_cwd) MUST NOT
    publish written: [] alongside 'git-delta' — that would be the
    affirmative claim 'nothing was written' over a channel that never
    opened (bd#7's [G2:4])."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
    eng.execute("wf", make_ctx(), run_id="r1")  # no git_cwd
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert not (payload["write_tracking"] == "git-delta" and payload["written"] == []), (
        f"write channel never ran (no git_cwd) but published "
        f"write_tracking={payload['write_tracking']!r} written={payload['written']!r}"
    )


def test_ac_e3c_written_accumulates_as_union_across_steps(tmp_path, git_repo):
    """AC-E3c (§0.1 quantified over steps): `written` accumulates over ALL
    steps of the phase as a union — non-uniform: step 1 writes early.txt,
    step 2 writes nothing. A last-step-only implementation yields
    written=[], AC-E3b's overclaim through the accumulation seam.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[write_step("s1", git_repo, "early.txt"), ok_step("s2")],
    ))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert "early.txt" in payload["written"], (
        f"a last-step-only accumulator drops step 1's write; got written={payload['written']!r}"
    )


def test_ac_e3c_written_accumulates_positive_control_both_steps_write(tmp_path, git_repo):
    """AC-E3c positive control: both steps write — union must contain both."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[write_step("s1", git_repo, "one.txt"), write_step("s2", git_repo, "two.txt")],
    ))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert "one.txt" in payload["written"]
    assert "two.txt" in payload["written"]


def test_ac_e3c_written_accumulation_survives_retry_recursion(tmp_path, git_repo):
    """AC-E3c (§0.1 quantified over frames): the pre-retry step's write
    MUST still be present after _execute_steps is re-entered at
    engine.py:638-645 and the outer frame returns retry_result at :657
    without running its own tail.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    calls = {"n": 0}

    def _flaky(_ctx, _prev):
        calls["n"] += 1
        if calls["n"] == 1:
            (git_repo / "pre_retry_write.txt").write_text("x\n")
            return StepResult(
                status="error", data={"retry_from_step": 1, "cycle_count": 1, "findings": ""},
                duration_ms=0, step_name="flaky", error_code="E_RETRY", recoverable=True,
            )
        return StepResult(status="ok", data=None, duration_ms=0, step_name="probe")

    eng.register("wf", WorkflowDefinition(
        name="wf",
        steps=[
            StepContract(name="flaky", execute=_flaky),
            StepContract(name="probe", execute=_flaky),
        ],
    ))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert "pre_retry_write.txt" in payload["written"], (
        f"pre-retry step's write must survive into the recursive frame's "
        f"final phase_artifacts payload; got written={payload['written']!r}"
    )


def test_ac_e3c_written_resets_between_sequential_execute_calls(tmp_path, git_repo):
    """AC-E3c: `written` is per-run state (engine.py:237-243) — run 2's
    written MUST NOT carry run 1's paths. Kills an instance-level (rather
    than per-run) accumulator."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", git_repo, "run1_only.txt")]))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="run1")

    eng.register("wf2", WorkflowDefinition(name="wf2", steps=[ok_step("s1")]))
    eng.execute("wf2", make_ctx(git_cwd=str(git_repo)), run_id="run2")

    pa = events_of(log, "phase_artifacts")
    assert len(pa) == 2
    run2_payload = next(p["payload"] for p in pa if p["run_id"] == "run2")
    assert "run1_only.txt" not in run2_payload["written"], (
        f"instance-level accumulator leaked run 1's write into run 2: "
        f"{run2_payload['written']!r}"
    )


def test_ac_e3d_large_written_list_truncates_but_stays_within_line_limit(tmp_path, git_repo):
    """AC-E3d (§0.2 behavioural, NO predicted byte arithmetic): drive a
    phase that writes MANY files, then read the RAW SERIALISED LINE back
    off disk and require len(raw_line) <= 4096. Kills a GREEN whose
    oversized phase_artifacts line vanishes silently
    (event_log.py:74/:116, swallowed by engine.py:710). Also asserts the
    elided-case digest is recomputable over the full sorted written list
    (a constant/zero digest MUST fail, §0.5).
    """
    log_path = tmp_path / "events.jsonl"
    log = EventLog(path=log_path)
    eng = WorkflowEngine(event_log=log)

    many_paths = [f"dir{i}/file_{i}.txt" for i in range(400)]
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", git_repo, *many_paths)]))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")

    raw_lines = log_path.read_bytes().splitlines(keepends=True)
    phase_artifacts_lines = [ln for ln in raw_lines if b'"phase_artifacts"' in ln]
    assert len(phase_artifacts_lines) == 1, (
        f"expected exactly one phase_artifacts line on disk, got "
        f"{len(phase_artifacts_lines)} — an oversized event that trips "
        "EventLogLineTooLarge is silently swallowed by engine.py:710, "
        "vanishing instead of truncating"
    )
    assert len(phase_artifacts_lines[0]) <= 4096, (
        f"raw phase_artifacts line is {len(phase_artifacts_lines[0])} bytes, "
        "exceeding the atomic-append limit"
    )

    payload = json.loads(phase_artifacts_lines[0])["payload"]
    assert payload.get("written_truncated"), "expected written_truncated=true when elided"
    assert payload.get("written_count") == len(many_paths), (
        f"expected written_count == {len(many_paths)} (the TRUE total), got "
        f"{payload.get('written_count')!r}"
    )
    expected_digest = _expected_digest(many_paths)
    assert payload.get("written_digest") == expected_digest, (
        f"written_digest must equal sha256 over the newline-joined full "
        f"sorted written list; got {payload.get('written_digest')!r}"
    )
    assert payload["written_digest"] != "sha256:" + "0" * 64


def test_ac_e3d_pathological_single_long_path_still_emits_within_limit(tmp_path, git_repo):
    """AC-E3d: a single pathological ~4200-char filename, supplied through
    the lib.git_port seam, MUST still produce an emitted phase_artifacts
    event within the limit — a count-based bound alone does not survive
    it: a sample-bounding (first-N-paths) implementation would still
    overflow on this single entry and vanish.
    """
    long_name = "d/" + ("p" * 4200) + ".txt"
    real = git_port.default_git_read()
    diff_calls = {"n": 0}

    def _fake_git_read(args, *, cwd=None, timeout=None, dir_=None):
        if args[:1] == ["diff"]:
            diff_calls["n"] += 1
            if diff_calls["n"] == 1:
                return git_port.GitResult(returncode=0, stdout="", stderr="", timed_out=False)
            return git_port.GitResult(returncode=0, stdout=f"A\t{long_name}\n", stderr="", timed_out=False)
        if args[:1] == ["ls-files"]:
            return git_port.GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        return real(args, cwd=cwd, timeout=timeout, dir_=dir_)

    git_port.set_default_git_read_factory(lambda: _fake_git_read)
    try:
        log_path = tmp_path / "events.jsonl"
        log = EventLog(path=log_path)
        eng = WorkflowEngine(event_log=log)
        eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1")]))
        eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    finally:
        git_port.reset_default_git_read_factory()

    raw_lines = log_path.read_bytes().splitlines(keepends=True)
    phase_artifacts_lines = [ln for ln in raw_lines if b'"phase_artifacts"' in ln]
    assert len(phase_artifacts_lines) == 1, (
        "a single ~4200-char path must not make the whole phase_artifacts "
        "record vanish"
    )
    assert len(phase_artifacts_lines[0]) <= 4096

    payload = json.loads(phase_artifacts_lines[0])["payload"]
    assert payload.get("written_digest") == _expected_digest([long_name]), (
        f"written_digest must equal sha256 over the (single-entry) sorted "
        f"written list; got {payload.get('written_digest')!r}"
    )


def test_ac_e3d_written_truncated_absent_or_falsey_when_no_elision(tmp_path, git_repo):
    """AC-E3d: written_truncated MUST be absent or falsey when no elision
    occurred, asserted on a small-list run."""
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", git_repo, "small.txt")]))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert not payload.get("written_truncated"), (
        f"expected written_truncated absent/falsey on a small run, got "
        f"{payload.get('written_truncated')!r}"
    )


def test_ac_e3e_written_paths_are_posix_relpaths_across_three_depths(tmp_path, git_repo):
    """AC-E3e: `written` entries are POSIX relpaths against the scan root,
    asserted with a fixture spanning THREE distinct depths (repo root, one
    level, two levels) in one phase — every existing bd#7 fixture wrote at
    a single depth, so a producer special-casing depth was untested. Exact
    string equality per depth; absolute paths, './'-prefixed paths, and
    OS-separator spellings all fail.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(
        name="wf",
        steps=[write_step("s1", git_repo, "root.txt", "one/level.txt", "two/levels/deep.txt")],
    ))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    payload = events_of(log, "phase_artifacts")[0]["payload"]
    written = set(payload["written"])
    for expected in ("root.txt", "one/level.txt", "two/levels/deep.txt"):
        assert expected in written, (
            f"expected exact posix relpath {expected!r} in written, got {sorted(written)!r}"
        )
    for forbidden in (
        str(git_repo / "root.txt"),  # absolute path
        "./root.txt",                 # ./-prefixed
        "one\\level.txt",             # OS-separator (Windows-style) spelling
    ):
        assert forbidden not in written, (
            f"forbidden path spelling {forbidden!r} must not appear in written={sorted(written)!r}"
        )


def test_ac_e3f_execution_completes_normally_when_event_log_append_always_raises(tmp_path, git_repo):
    """AC-E3f: both new emits (run_identity, phase_artifacts) go through
    _emit (engine.py:696-711), whose except at :710 swallows logging
    failures. A direct self._event_log.append(...) for either new emit
    would let the exception escape and break execution. Asserted by
    recording which event_types were ATTEMPTED before the always-raising
    append call: today (pre-GREEN) run_identity/phase_artifacts are never
    attempted at all, so this fails for the right reason.
    """
    log = _AlwaysRaisingEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", git_repo, "a.txt")]))
    result, _ = eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")

    assert result.status == "ok", (
        f"execute() must complete with its normal status even when every "
        f"event-log append raises; got status={result.status!r}"
    )
    assert "run_identity" in log.attempted, (
        f"run_identity must be attempted (and its failure swallowed by "
        f"_emit); attempted={log.attempted!r}"
    )
    assert "phase_artifacts" in log.attempted, (
        f"phase_artifacts must be attempted (and its failure swallowed by "
        f"_emit); attempted={log.attempted!r}"
    )
