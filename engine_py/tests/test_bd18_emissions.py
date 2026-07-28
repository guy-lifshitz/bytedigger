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


def _raw_lines_of_type(raw_lines: list[bytes], event_type: str) -> list[bytes]:
    """Select raw JSONL lines by PARSED event_type, not substring match
    (`[G18:EDGE-6]`): a substring match on b'"phase_artifacts"' would also
    match a shadowed event carrying `"shadowed_event":"phase_artifacts"`.
    Cannot fire today (this lot patches nothing shadow-related, §6), but a
    `== 1` count assertion should not rest on a substring coincidence."""
    matched = []
    for ln in raw_lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if obj.get("event_type") == event_type:
            matched.append(ln)
    return matched


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


def test_ac_e1e_phase_uniform_independently_across_both_step_event_kinds(tmp_path):
    """AC-E1e [A2]: the EMISSION itself must be uniform across BOTH
    step-event kinds, asserted PER KIND with no merged/reduced collection
    (bd#7's round-9 rung: a fixture that stripped `phase` from both kinds
    TOGETHER let a consumer reducing with any/first/last over the merged
    event stream pass 123 tests, because the two-kind collection itself
    was never checked for non-uniformity). engine.py:370 (step_started)
    and engine.py:472 (step_finished) are genuinely separate emit sites,
    so a GREEN can add `phase` to one and forget the other. Kills BOTH:
      (1) phase added to step_started only (:370) — forgot step_finished (:472)
      (2) phase added to step_finished only (:472) — forgot step_started (:370)
    step_started and step_finished are collected and checked as two
    SEPARATE lists below — never merged/reduced into one "step events"
    collection, which is exactly the shape that hid this in bd#7.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    steps = [ok_step("first"), ok_step("middle"), ok_step("last")]
    eng.register("multi_phase_a2", WorkflowDefinition(name="multi_phase_a2", steps=steps))
    eng.execute("multi_phase_a2", make_ctx(), run_id="r1")

    started_events = events_of(log, "step_started")
    finished_events = events_of(log, "step_finished")
    assert len(started_events) == 3
    assert len(finished_events) == 3

    # Kind 1, checked entirely on its own — kills "phase added to
    # step_finished only (:472), forgot step_started (:370)".
    for evt in started_events:
        assert evt["payload"].get("phase") == "multi_phase_a2", (
            f"step_started for {evt['payload'].get('step_name')!r} missing/"
            f"wrong phase — got {evt['payload'].get('phase')!r}. If every "
            "step_finished in this same run carries phase correctly, the "
            "GREEN added phase to :472 only and forgot :370."
        )

    # Kind 2, checked entirely on its own — kills "phase added to
    # step_started only (:370), forgot step_finished (:472)".
    for evt in finished_events:
        assert evt["payload"].get("phase") == "multi_phase_a2", (
            f"step_finished for {evt['payload'].get('step_name')!r} missing/"
            f"wrong phase — got {evt['payload'].get('phase')!r}. If every "
            "step_started in this same run carries phase correctly, the "
            "GREEN added phase to :370 only and forgot :472."
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


_ADAPTER_SOURCE_CLOSED_SET = frozenset({"kwarg", "env", "default", "default-fallback"})


def test_ac_e2b_adapter_identity_is_mapping_with_nonempty_backend_and_source(tmp_path, monkeypatch):
    """AC-E2b: adapter_identity MUST be a mapping with non-empty `backend`
    and `source` — not a bare string (bd#7 [G2:8]: a bare-string fixture
    pinned a weaker contract than the engine emits). Asserted by shape AND
    by value against the configured backend (HAL_RUNNER_BACKEND — the
    engine's one existing backend-selection seam, pinned per-test to
    'claude-subprocess' by conftest's autouse fixture) across two DIFFERENT
    configured values, so a constant fails.

    `[G18:MINOR-3]` seam named (§0.6):
    `llm_subprocess._resolve_backend(kwarg, env)` (llm_subprocess.py:608-630),
    the engine's only backend selector, returning exactly a
    `(backend, source)` pair and reading the environment through
    `config_provider.env_mapping()` (:327-328) — a live `_AliasEnviron`, so
    `monkeypatch.setenv` on `HAL_RUNNER_BACKEND` reaches it. `source` is
    asserted BY VALUE (not merely non-empty, §0.5) against the closed set
    `{"kwarg","env","default","default-fallback"}` that resolver returns —
    kills `source: "x"` satisfying both spec and test, the defect `backend`
    already avoided and `source` did not in v1.
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
        assert identity.get("source") in _ADAPTER_SOURCE_CLOSED_SET, (
            f"adapter_identity['source'] must be one of {_ADAPTER_SOURCE_CLOSED_SET!r}, "
            f"got {identity.get('source')!r} — a bare non-emptiness check would let "
            f"source: 'x' through"
        )

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
    # Both runs here go through the resolver's ENV branch (no kwarg is ever
    # set) — asserted BY VALUE, not just closed-set membership.
    assert identity_a["source"] == "env", f"expected source == 'env', got {identity_a['source']!r}"
    assert identity_b["source"] == "env", f"expected source == 'env', got {identity_b['source']!r}"


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

    `[G18:MINOR-4]` the conditional compares RESOLVED paths
    (`Path(self).resolve() == target.resolve()`): `Path.__eq__` compares
    normalised strings, not filesystem identity, so a bare `self == target`
    gate would miss a correct GREEN spelling the file
    `Path(__file__).resolve().parent / "pyproject.toml"`, or any host where
    a parent of engine_py is a symlink — falling through to the real read
    and false-failing a correct GREEN.
    """
    def _raise_not_found(name):
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib_metadata, "version", _raise_not_found)

    real_read_text = Path.read_text
    target_resolved = _pyproject_path().resolve()

    def _conditional_read_text(self, *args, **kwargs):
        if Path(self).resolve() == target_resolved:
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
    seam is patched path-conditionally per the three failure modes, gated
    on RESOLVED paths (`[G18:MINOR-4]`) so a symlink-parent host or a
    differently-spelled-but-identical path is not false-failed.
    """
    def _raise_not_found(name):
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib_metadata, "version", _raise_not_found)

    real_read_text = Path.read_text
    target_resolved = _pyproject_path().resolve()

    def _conditional_read_text(self, *args, **kwargs):
        if Path(self).resolve() != target_resolved:
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

    `[G18:MINOR-8]` `read` MUST be `[]`, asserted BY VALUE. Only pinning
    the key in the exact key-set (as v1 did) lets a GREEN emit
    `read: ["anything"]` and still satisfy the suite — an affirmative
    claim over a channel that provably never opened (there is no read
    instrumentation in this lot at all, §6), which `read_tracking:
    "declared-only"` exists specifically to announce rather than leave
    to be misread.
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
    assert payload["read"] == [], (
        f"expected read == [] (no read instrumentation exists in this lot, "
        f"§6 — an unpinned value would let read: ['anything'] pass), got "
        f"{payload['read']!r}"
    )
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


def test_ac_e3a_phase_artifacts_emitted_once_when_retry_start_step_beyond_range(tmp_path, git_repo):
    """AC-E3a `[G18:EDGE-1]`: `start_step` beyond range raises RuntimeError
    at engine.py:679 (reached via a retry whose `retry_from_step` exceeds
    the last step index), from INSIDE the recursion — the place a
    phase-level accumulator double-counts, and the reason the emit
    belongs in execute()'s `finally` rather than inside `_execute_steps`.
    "Exactly one phase_artifacts across the two unwinding frames" (the
    recursive frame that raises, and the outer frame it propagates through)
    is the non-obvious half.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)

    def _trigger_out_of_range_retry(_ctx, _prev):
        return StepResult(
            status="error",
            data={"retry_from_step": 99, "cycle_count": 1, "findings": ""},
            duration_ms=0, step_name="s1", error_code="E_RETRY", recoverable=True,
        )

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[StepContract(name="s1", execute=_trigger_out_of_range_retry)],
    ))
    with pytest.raises(RuntimeError, match="start_step beyond range"):
        eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")

    assert len(events_of(log, "phase_artifacts")) == 1, (
        "exactly one phase_artifacts must be emitted across the two "
        "unwinding frames (the recursive _execute_steps call that raises, "
        "and the outer call it propagates through) — not zero, not "
        "double-counted"
    )


def test_ac_e3a_phase_artifacts_emitted_once_on_same_cycle_retry_cap_exit(tmp_path, git_repo):
    """AC-E3a `[G18:EDGE-2]`: the same-cycle-retry-cap exit at
    engine.py:575 returns from WITHIN the retry recursion after
    `same_cycle_retry_capped` fires — the only exit that leaves mid-retry
    state. A step that returns the SAME `retry_from_step`/`cycle_count`
    forever drives the engine's own same-cycle detection (`next_cycle <=
    cycle`) past `_MAX_SAME_CYCLE_RETRIES` (3), so the cap fires from a
    deeply-recursed frame rather than the outermost call. Exactly ONE
    phase_artifacts must still be emitted for the phase.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)

    def _same_cycle_retry_forever(_ctx, _prev):
        return StepResult(
            status="error",
            data={"retry_from_step": 0, "cycle_count": 1, "findings": ""},
            duration_ms=0, step_name="s1", error_code="E_RETRY", recoverable=True,
        )

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[StepContract(name="s1", execute=_same_cycle_retry_forever)],
    ))
    result, _ = eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")

    assert result.status == "error", (
        f"expected the same-cycle-retry-cap exit to terminate with the "
        f"underlying error result; got status={result.status!r}"
    )
    assert len(events_of(log, "phase_artifacts")) == 1, (
        "exactly one phase_artifacts must be emitted for the phase even "
        "though the same-cycle-retry-cap exit returns from mid-recursion"
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
    step of the phase -> 'git-delta'.

    `[G18:1]` raised to >=2 steps — a one-step control cannot be an
    all-satisfy control OVER A COLLECTION, which is what §0.1 requires of
    a positive control paired with a quantified requirement.
    """
    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[write_step("s1", git_repo, "a.txt"), write_step("s2", git_repo, "b.txt")],
    ))
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


class _OneShotFailAfterFlagGitPort:
    """Delegates to the real GitReadPort. The NEXT git_read call after a
    shared flag is set to True fails ONCE (consumed on use), then the port
    reverts to delegating normally for every subsequent call.

    The flag is set BY A STEP'S OWN BODY when it runs — never by counting
    calls — so the failure is keyed to step-body STATE, immune to how many
    git_read calls a differently-shaped GREEN issues per step or per phase
    (`[G18:2]`: a call-count boundary calibrated on one run shape is the
    mechanism of bd#7's first Class B defect, even though it is not a byte
    prediction and so does not violate §0.2 in letter).
    """

    def __init__(self, flag: dict) -> None:
        self._flag = flag
        self._real = git_port.default_git_read()

    def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
        if self._flag.get("fail_next"):
            self._flag["fail_next"] = False  # one-shot: consume, then delegate again
            return git_port.GitResult(returncode=1, stdout="", stderr="injected failure", timed_out=False)
        return self._real(args, cwd=cwd, timeout=timeout, dir_=dir_)


def test_ac_e3b_not_observed_on_partial_delta_failure_fail_early(tmp_path, git_repo):
    """AC-E3b `[G18:1]` fail-EARLY ordering (§0.1: the fixture set must
    exclude every reduction the implementation could choose, both
    orderings for an ordered collection, not one): step 1's delta
    computation fails while step 2's (the LATER step's) succeeds. This is
    the ordering that kills a `last`-shaped GREEN —
    `"git-delta" if n_steps >= 1 and last_step_delta is not None else
    "not-observed"` — which round 1's fail-LATE-only fixture could not
    distinguish from a correct `all()` reduction, because in that fixture
    the *last* delta was exactly the missing one.

    Triggered off state step 1's OWN BODY sets when it runs (`[G18:2]`),
    never a git-call ordinal: step 1 writes its file then flips the
    shared one-shot flag, which fails exactly the next real git_read call
    (step 1's own post-snapshot) and then self-clears, leaving step 2's
    snapshots to succeed for real regardless of how many git_read calls
    the GREEN issues per step.
    """
    flag = {"fail_next": False}

    def _step1(_ctx, _prev):
        (git_repo / "s1.txt").write_text("x\n")
        flag["fail_next"] = True  # fail THIS step's own next git snapshot
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s1")

    def _step2(_ctx, _prev):
        (git_repo / "s2.txt").write_text("x\n")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s2")

    git_port.set_default_git_read_factory(lambda: _OneShotFailAfterFlagGitPort(flag))
    try:
        log = make_log(tmp_path)
        eng = WorkflowEngine(event_log=log)
        eng.register("wf", WorkflowDefinition(
            name="wf",
            steps=[StepContract(name="s1", execute=_step1), StepContract(name="s2", execute=_step2)],
        ))
        eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    finally:
        git_port.reset_default_git_read_factory()

    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert payload["write_tracking"] == "not-observed", (
        f"step 1 (the EARLIER step) failed its delta computation while "
        f"step 2 (the LATER step) succeeded — a last-shaped GREEN would "
        f"wrongly publish 'git-delta' here; got {payload['write_tracking']!r}"
    )


def test_ac_e3b_not_observed_on_partial_delta_failure_fail_late(tmp_path, git_repo):
    """AC-E3b `[G18:1]` fail-LATE ordering (kills `any()`/`first()`-shaped
    GREENs): step 1 succeeds, step 2's (the LATER step's) delta
    computation fails. _git_changes_vs_head returns None on timeout/
    missing-git (engine.py:1082), so an `any()`-shaped implementation
    would still publish 'git-delta' here because SOME step's delta (step
    1's) did compute.

    Triggered off state step 2's OWN BODY sets when it runs (`[G18:2]`),
    never a git-call ordinal.
    """
    flag = {"fail_next": False}

    def _step1(_ctx, _prev):
        (git_repo / "s1.txt").write_text("x\n")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s1")

    def _step2(_ctx, _prev):
        (git_repo / "s2.txt").write_text("x\n")
        flag["fail_next"] = True  # fail THIS step's own next git snapshot
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s2")

    git_port.set_default_git_read_factory(lambda: _OneShotFailAfterFlagGitPort(flag))
    try:
        log = make_log(tmp_path)
        eng = WorkflowEngine(event_log=log)
        eng.register("wf", WorkflowDefinition(
            name="wf",
            steps=[StepContract(name="s1", execute=_step1), StepContract(name="s2", execute=_step2)],
        ))
        eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    finally:
        git_port.reset_default_git_read_factory()

    payload = events_of(log, "phase_artifacts")[0]["payload"]
    assert payload["write_tracking"] == "not-observed", (
        f"step 2 (the LATER step) failed its delta computation while "
        f"step 1 (the EARLIER step) succeeded — an any()/first()-shaped "
        f"GREEN would wrongly publish 'git-delta' here; got "
        f"{payload['write_tracking']!r}"
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
    """AC-E3c `[G18:3]`: `written` is per-run state (engine.py:237-243) —
    run 2's written MUST NOT carry run 1's paths.

    ONE registered workflow, executed TWICE under different run_ids —
    never two different workflow names. Round 1's `"wf"`/`"wf2"` fixture
    kills only an UNKEYED `self._written = set()`; it misses a
    `self._written_by_phase: dict[str, set]` left OUT of the reset at
    engine.py:237-243, because two different workflow names never collide
    in a keyed dict and run 2 simply reads a fresh (empty) bucket for its
    own phase name, passing regardless of whether the reset actually ran.
    That shape is actively invited here: the payload is keyed by `phase`,
    and the neighbouring per-run state `self._same_cycle_retries` is
    itself a keyed dict (:243, :540). The real defect this guards —
    re-running the SAME phase on one engine instance, the ordinary case
    and exactly what AC-E2d already does three times — would report run
    1's paths in run 2's `written`: an affirmative false claim about which
    files run 2 wrote.
    """
    calls = {"n": 0}

    def _maybe_write(_ctx, _prev):
        calls["n"] += 1
        if calls["n"] == 1:
            (git_repo / "run1_only.txt").write_text("x\n")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s1")

    log = make_log(tmp_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[StepContract(name="s1", execute=_maybe_write)]))
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="run1")
    eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="run2")

    pa = events_of(log, "phase_artifacts")
    assert len(pa) == 2, (
        f"expected exactly one phase_artifacts per execute() call on the "
        f"SAME registered workflow (§0.1: quantified over execute() calls "
        f"too), got {len(pa)}"
    )
    run1_payload = next(p["payload"] for p in pa if p["run_id"] == "run1")
    run2_payload = next(p["payload"] for p in pa if p["run_id"] == "run2")
    assert "run1_only.txt" in run1_payload["written"], "fixture sanity: run 1 must have written the file"
    assert "run1_only.txt" not in run2_payload["written"], (
        f"an instance-level (or phase-keyed-but-unreset) accumulator "
        f"leaked run 1's write into run 2 of the SAME phase: "
        f"{run2_payload['written']!r}"
    )


def test_ac_e3d_large_written_list_truncates_but_stays_within_line_limit(tmp_path, git_repo):
    """AC-E3d (§0.2 behavioural, NO predicted byte arithmetic): drive a
    phase that writes MANY files, then read the RAW SERIALISED LINE back
    off disk and require len(raw_line) <= 4096. Kills a GREEN whose
    oversized phase_artifacts line vanishes silently
    (event_log.py:90/:132-133, swallowed by engine.py:710). Also asserts the
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
    phase_artifacts_lines = _raw_lines_of_type(raw_lines, "phase_artifacts")
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

    The fake keys off STEP-BODY STATE (a flag the step sets when it runs)
    rather than "which numbered diff call is this" (`[G18:2]`): keying off
    the first `diff` call assumes the phase's first diff IS the step's
    pre-snapshot, which is false for a GREEN that takes an additional
    phase-level baseline before the per-step loop.
    """
    long_name = "d/" + ("p" * 4200) + ".txt"
    real = git_port.default_git_read()
    state = {"step_ran": False}

    def _fake_git_read(args, *, cwd=None, timeout=None, dir_=None):
        if args[:1] == ["diff"]:
            if state["step_ran"]:
                return git_port.GitResult(returncode=0, stdout=f"A\t{long_name}\n", stderr="", timed_out=False)
            return git_port.GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        if args[:1] == ["ls-files"]:
            return git_port.GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        return real(args, cwd=cwd, timeout=timeout, dir_=dir_)

    def _mark_ran(_ctx, _prev):
        state["step_ran"] = True
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s1")

    git_port.set_default_git_read_factory(lambda: _fake_git_read)
    try:
        log_path = tmp_path / "events.jsonl"
        log = EventLog(path=log_path)
        eng = WorkflowEngine(event_log=log)
        eng.register("wf", WorkflowDefinition(name="wf", steps=[StepContract(name="s1", execute=_mark_ran)]))
        eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")
    finally:
        git_port.reset_default_git_read_factory()

    raw_lines = log_path.read_bytes().splitlines(keepends=True)
    phase_artifacts_lines = _raw_lines_of_type(raw_lines, "phase_artifacts")
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


class _SelectivelyRaisingEventLog:
    """Wraps a REAL EventLog; raises only for the given ``raise_for``
    event_types, delegating every OTHER emit through to the real log
    unchanged — so neighbouring emits (workflow_started, step_started,
    workflow_cost_rollup, ...) land on disk exactly as production would,
    and only the new emit(s) are forced through the failure path."""

    def __init__(self, real_log: EventLog, raise_for: set[str]) -> None:
        self._real = real_log
        self._raise_for = raise_for
        self.attempted: list[str] = []
        self.path = real_log.path

    def append(self, event_type: str, payload: dict, run_id: str | None = None):
        self.attempted.append(event_type)
        if event_type in self._raise_for:
            raise RuntimeError(f"append always fails for {event_type}")
        return self._real.append(event_type, payload, run_id)


def test_ac_e3f_execution_completes_normally_when_only_new_emits_fail(tmp_path, git_repo):
    """AC-E3f `[G18:EDGE-5]`: the unconditional-raise double is too blunt
    on its own — its `path = None` short-circuits the `workflow_cost_
    rollup` block (engine.py:275-281) and the dispatcher/stuck-report
    paths (:302-325), so it proves swallowing only for a run whose
    neighbouring emits were skipped entirely. A REAL EventLog whose
    append raises ONLY for run_identity/phase_artifacts, leaving every
    other emit working, is the run that actually resembles production —
    it shows the new emits are swallowed IN SITU, not in a
    stripped-down execution.
    """
    real_log = EventLog(path=tmp_path / "selective.jsonl")
    log = _SelectivelyRaisingEventLog(real_log, raise_for={"run_identity", "phase_artifacts"})
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[write_step("s1", git_repo, "a.txt")]))
    result, _ = eng.execute("wf", make_ctx(git_cwd=str(git_repo)), run_id="r1")

    assert result.status == "ok", (
        f"execute() must complete with its normal status even when ONLY "
        f"run_identity/phase_artifacts appends raise; got status={result.status!r}"
    )
    assert "run_identity" in log.attempted, "run_identity must be attempted"
    assert "phase_artifacts" in log.attempted, "phase_artifacts must be attempted"

    on_disk_types = {e["event_type"] for e in real_log.read_all()}
    for expected in ("workflow_started", "step_started", "step_finished", "workflow_finished"):
        assert expected in on_disk_types, (
            f"expected {expected!r} to land on disk unaffected (only "
            f"run_identity/phase_artifacts were made to fail); got "
            f"types={sorted(on_disk_types)!r}"
        )
    for forbidden in ("run_identity", "phase_artifacts"):
        assert forbidden not in on_disk_types, (
            f"{forbidden!r}'s append raised — it must NOT have reached the "
            f"real log"
        )
