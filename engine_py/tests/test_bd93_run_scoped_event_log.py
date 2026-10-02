"""RED tests for bd#93 -- the event log lives at its run-scoped home from the first event.

Spec: docs/decisions/2026-10-02-bd93-run-scoped-event-log.md (AC1..AC8).

Determinism: every test points HAL_RUN_LOG_ROOT at its own tmp_path (never the
real HOME), uses no network and no real `claude`. Production symbols that do
not exist yet (run_scoped_log_path, run_log_root) are imported INSIDE test
bodies so each test fails individually before GREEN.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers.engine_subprocess import engine_env

RUN_PY = Path(__file__).resolve().parent.parent / "bytedigger_engine" / "run.py"

_BAD_RUN_IDS = ["../x", "a/b", ".h", "a" * 129]


@pytest.fixture(autouse=True)
def _bd93_env_hermetic(monkeypatch, tmp_path):
    """No host-forced log path, no alias leakage, DBOS db under tmp."""
    for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
        monkeypatch.delenv(prefix + "EVENT_LOG_PATH", raising=False)
        if prefix != "HAL_":  # HAL_RUN_LOG_ROOT is left as conftest set it (AC8 reads it)
            monkeypatch.delenv(prefix + "RUN_LOG_ROOT", raising=False)
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(tmp_path / "dbos.sqlite"))


def _cli(run_id: str, root: Path, cwd: Path, extra: list[str] | None = None,
         question: str = "hi") -> subprocess.CompletedProcess:
    cwd.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(RUN_PY), "--workflow", "echo", "--neutral",
        "--run-id", run_id, "--ctx-json", json.dumps({"org_config": {}, "question": question}),
    ] + (extra or [])
    return subprocess.run(
        cmd, cwd=str(cwd), env=engine_env(HAL_RUN_LOG_ROOT=str(root)),
        capture_output=True, text=True, timeout=120,
    )


def _events(path: Path) -> list[dict]:
    from bytedigger_engine.event_log import EventLog  # noqa: PLC0415

    return EventLog(path).read_all()


def _main_in_process(monkeypatch, argv_tail: list[str]) -> int:
    from bytedigger_engine import run  # noqa: PLC0415

    monkeypatch.setattr(sys, "argv", ["run.py", *argv_tail])
    try:
        return run.main()
    except SystemExit as e:  # in-process callers must not kill pytest
        return e.code


def _register_extra_workflow(monkeypatch, name: str, execute) -> None:
    """Wrap workflows.register_all so a test workflow is registered alongside the real ones."""
    from bytedigger_engine import workflows  # noqa: PLC0415
    from bytedigger_engine.contracts import StepContract, WorkflowDefinition  # noqa: PLC0415

    orig = workflows.register_all

    def _wrapped(engine):
        orig(engine)
        engine.register(name, WorkflowDefinition(name=name, steps=[StepContract(name=name, execute=execute)]))

    monkeypatch.setattr(workflows, "register_all", _wrapped)


# --------------------------------------------------------------------------- AC1
def test_ac1_crash_survives_run_scoped_log(tmp_path, monkeypatch):
    """AC1: crashing workflow, no --event-log, --run-id R1 -> <root>/R1/events.jsonl readable,
    >=1 event, every event run_id == R1."""
    root = tmp_path / "root"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(tmp_path)

    def _boom(ctx, _prev):
        raise RuntimeError("bd93 deliberate crash")

    _register_extra_workflow(monkeypatch, "bd93_boom", _boom)
    _main_in_process(monkeypatch, ["--workflow", "bd93_boom", "--run-id", "R1",
                                   "--ctx-json", json.dumps({"org_config": {}, "question": "x"})])

    log_file = root / "R1" / "events.jsonl"
    assert log_file.is_file(), f"run-scoped log missing at {log_file}"
    events = _events(log_file)
    assert len(events) >= 1
    assert "workflow_started" in {e["event_type"] for e in events}
    assert {e["run_id"] for e in events} == {"R1"}


# --------------------------------------------------------------------------- AC2
def test_ac2_no_cross_contamination_two_runs_same_cwd(tmp_path):
    """AC2: R1 and R2 from same cwd (echo, subprocess, --neutral) -> two files, own ids only,
    no <cwd>/.bytedigger/events.jsonl."""
    root, cwd = tmp_path / "root", tmp_path / "work"
    for rid in ("R1", "R2"):
        proc = _cli(rid, root, cwd)
        assert proc.returncode == 0, proc.stderr
    f1, f2 = root / "R1" / "events.jsonl", root / "R2" / "events.jsonl"
    assert f1.is_file() and f2.is_file() and f1 != f2
    for f, rid in ((f1, "R1"), (f2, "R2")):
        evs = _events(f)
        assert evs and {e["run_id"] for e in evs} == {rid}
    assert not (cwd / ".bytedigger" / "events.jsonl").exists()


# --------------------------------------------------------------------------- AC3
def test_ac3_cwd_independent_resolution(tmp_path, monkeypatch):
    """AC3: same run id from two different cwds resolves to the same path under the root."""
    from bytedigger_engine.event_log import run_scoped_log_path  # noqa: PLC0415

    root = tmp_path / "root"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    monkeypatch.chdir(tmp_path / "a")
    pa = run_scoped_log_path("R3")
    monkeypatch.chdir(tmp_path / "b")
    pb = run_scoped_log_path("R3")
    assert pa == pb == root / "R3" / "events.jsonl"

    # End to end: both cwds land on the one file, neither cwd gets a local log.
    for sub, q in (("a", "one"), ("b", "two")):
        proc = _cli("R3", root, tmp_path / sub, question=q)
        assert proc.returncode == 0, proc.stderr
        assert not (tmp_path / sub / ".bytedigger").exists()
    evs = _events(root / "R3" / "events.jsonl")
    assert evs and {e["run_id"] for e in evs} == {"R3"}


# --------------------------------------------------------------------------- AC4
def test_ac4_explicit_event_log_wins_no_run_dir(tmp_path):
    """AC4a: explicit --event-log P writes to P and creates no <root>/<run_id>."""
    root, cwd = tmp_path / "root", tmp_path / "work"
    explicit = tmp_path / "explicit" / "my-events.jsonl"
    proc = _cli("R4", root, cwd, extra=["--event-log", str(explicit)])
    assert proc.returncode == 0, proc.stderr
    assert explicit.is_file() and {e["run_id"] for e in _events(explicit)} == {"R4"}
    assert not (root / "R4").exists()


def test_ac4_host_override_beats_run_scoped_default(tmp_path, monkeypatch):
    """AC4b: provider event_log_path_override() wins over the run-scoped default."""
    from bytedigger_engine import config_provider  # noqa: PLC0415

    root = tmp_path / "root"
    forced = tmp_path / "forced" / "host-events.jsonl"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(tmp_path)

    class _HostProvider(config_provider._DefaultConfigProvider):
        def event_log_path_override(self) -> str:
            return str(forced)

    config_provider.set_default_config_provider_factory(_HostProvider)
    try:
        rc = _main_in_process(monkeypatch, ["--workflow", "echo", "--run-id", "R4h",
                                            "--ctx-json", json.dumps({"org_config": {}, "question": "x"})])
    finally:
        config_provider.reset_default_config_provider_factory()
    assert rc == 0
    assert forced.is_file() and {e["run_id"] for e in _events(forced)} == {"R4h"}
    assert not (root / "R4h").exists()


# --------------------------------------------------------------------------- AC5
def _assert_ran_as_no_log(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["data"] == {"echoed": "hi"}
    assert "event log disabled:" in proc.stderr


@pytest.mark.parametrize("bad_id", _BAD_RUN_IDS)
def test_ac5_degrade_bad_run_id(tmp_path, bad_id):
    """AC5: bad run id -> workflow still runs, same rc/stdout, stderr 'event log disabled:',
    nothing written outside root."""
    root, cwd = tmp_path / "root", tmp_path / "work"
    proc = _cli(bad_id, root, cwd)
    _assert_ran_as_no_log(proc)
    assert not root.exists() or list(root.iterdir()) == []
    assert not (cwd / ".bytedigger").exists()
    assert not (tmp_path / "x").exists()  # '../x' must not escape the root


def test_ac5_degrade_root_is_regular_file(tmp_path):
    """AC5: unusable root (a regular file) -> degrade, workflow runs, nothing written."""
    root, cwd = tmp_path / "rootfile", tmp_path / "work"
    root.write_text("not a directory")
    proc = _cli("R5", root, cwd)
    _assert_ran_as_no_log(proc)
    assert root.is_file() and root.read_text() == "not a directory"
    assert not (cwd / ".bytedigger").exists()


# --------------------------------------------------------------------------- AC6
def _capture_org_config(monkeypatch) -> list:
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    seen: list = []

    def _cap(ctx, _prev):
        seen.append(dict(ctx.org_config or {}))
        return StepResult(status="ok", data={}, duration_ms=0, step_name="bd93_cap")

    _register_extra_workflow(monkeypatch, "bd93_cap", _cap)
    return seen


def test_ac6_ctx_gets_resolved_run_scoped_path(tmp_path, monkeypatch):
    """AC6: no --event-log -> ctx.org_config['events_log_path'] == <root>/<run_id>/events.jsonl."""
    root = tmp_path / "root"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    seen = _capture_org_config(monkeypatch)
    rc = _main_in_process(monkeypatch, ["--workflow", "bd93_cap", "--run-id", "R6",
                                        "--ctx-json", json.dumps({"org_config": {}, "question": "x"})])
    assert rc == 0 and len(seen) == 1
    assert seen[0].get("events_log_path") is not None
    assert Path(seen[0]["events_log_path"]) == (root / "R6" / "events.jsonl").absolute()
    assert Path(seen[0]["events_log_path"]).is_absolute()


def test_ac6_ctx_gets_explicit_event_log_path(tmp_path, monkeypatch):
    """AC6: explicit --event-log P is injected as events_log_path."""
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    explicit = tmp_path / "ex" / "e.jsonl"
    seen = _capture_org_config(monkeypatch)
    rc = _main_in_process(monkeypatch, ["--workflow", "bd93_cap", "--run-id", "R6b", "--event-log", str(explicit),
                                        "--ctx-json", json.dumps({"org_config": {}, "question": "x"})])
    assert rc == 0 and len(seen) == 1
    assert seen[0].get("events_log_path") is not None
    assert Path(seen[0]["events_log_path"]) == explicit.absolute()


def test_ac6_caller_provided_events_log_path_is_kept(tmp_path, monkeypatch):
    """AC6: a caller-provided events_log_path is not overwritten (and the run still gets a log)."""
    root = tmp_path / "root"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    seen = _capture_org_config(monkeypatch)
    ctx = {"org_config": {"events_log_path": "/caller/set.jsonl"}, "question": "x"}
    rc = _main_in_process(monkeypatch, ["--workflow", "bd93_cap", "--run-id", "R6c",
                                        "--ctx-json", json.dumps(ctx)])
    assert rc == 0 and len(seen) == 1
    assert seen[0]["events_log_path"] == "/caller/set.jsonl"
    # forcing: the run-scoped log exists even though ctx kept the caller's value
    assert (root / "R6c" / "events.jsonl").is_file()


@pytest.mark.parametrize("falsy", [None, ""])
def test_ac6_falsy_caller_events_log_path_is_replaced(tmp_path, monkeypatch, falsy):
    """AC6 (r2): only a truthy caller value counts as caller-set; null/"" is replaced."""
    root = tmp_path / "root"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    seen = _capture_org_config(monkeypatch)
    ctx = {"org_config": {"events_log_path": falsy}, "question": "x"}
    rc = _main_in_process(monkeypatch, ["--workflow", "bd93_cap", "--run-id", "R6d",
                                        "--ctx-json", json.dumps(ctx)])
    assert rc == 0 and len(seen) == 1
    assert seen[0].get("events_log_path")
    assert Path(seen[0]["events_log_path"]) == (root / "R6d" / "events.jsonl").absolute()


# --------------------------------------------------------------------------- AC1b
def test_ac1b_hard_kill_leaves_run_scoped_log(tmp_path):
    """AC1b: a step calling os._exit(17) after workflow_started -> rc 17, log readable with
    workflow_started, every event run_id == R1 (no end-of-run materialisation can satisfy this)."""
    root, cwd = tmp_path / "root", tmp_path / "work"
    cwd.mkdir()
    script = tmp_path / "kill_run.py"
    script.write_text(
        "import os, sys, json\n"
        "from bytedigger_engine import run, workflows\n"
        "from bytedigger_engine.contracts import StepContract, WorkflowDefinition\n"
        "_orig = workflows.register_all\n"
        "def _reg(engine):\n"
        "    _orig(engine)\n"
        "    engine.register('bd93_kill', WorkflowDefinition(name='bd93_kill', steps=[\n"
        "        StepContract(name='bd93_kill', execute=lambda ctx, prev: os._exit(17))]))\n"
        "workflows.register_all = _reg\n"
        "sys.argv = ['run.py', '--workflow', 'bd93_kill', '--run-id', 'R1', '--neutral',\n"
        "            '--ctx-json', json.dumps({'org_config': {}, 'question': 'x'})]\n"
        "sys.exit(run.main())\n"
    )
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=str(cwd),
        env=engine_env(HAL_RUN_LOG_ROOT=str(root)), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 17, (proc.returncode, proc.stderr)
    log_file = root / "R1" / "events.jsonl"
    assert log_file.is_file()
    events = _events(log_file)
    assert "workflow_started" in {e["event_type"] for e in events}
    assert {e["run_id"] for e in events} == {"R1"}


# --------------------------------------------------------------------------- AC5c
class _MinimalProvider:
    """Only the six Protocol methods (delegating to the neutral default); no
    event_log_path_override / home_root / foreign_state_dirname / run_log_root."""

    def __init__(self) -> None:
        from bytedigger_engine.config_provider import _DefaultConfigProvider  # noqa: PLC0415

        self._d = _DefaultConfigProvider()

    def gate_enabled(self, env_var): return self._d.gate_enabled(env_var)
    def flag(self, env_var): return self._d.flag(env_var)
    def timeout_ms(self, env_var, default): return self._d.timeout_ms(env_var, default)
    def binary(self, env_var, default): return self._d.binary(env_var, default)
    def hal_root(self): return self._d.hal_root()
    def path(self, env_var, default): return self._d.path(env_var, default)


def test_ac5c_minimal_provider_no_event_log_still_runs_ok(tmp_path, monkeypatch, capsys):
    """AC5c: minimal-Protocol provider (no event_log_path_override) + echo, no --event-log ->
    status ok, no crash, and the run-scoped log is still attached (path() honours the env root)."""
    from bytedigger_engine import config_provider  # noqa: PLC0415

    root = tmp_path / "root"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    config_provider.set_default_config_provider_factory(_MinimalProvider)
    try:
        rc = _main_in_process(monkeypatch, ["--workflow", "echo", "--run-id", "R5c",
                                            "--ctx-json", json.dumps({"org_config": {}, "question": "hi"})])
    finally:
        config_provider.reset_default_config_provider_factory()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["status"] == "ok"
    assert (root / "R5c" / "events.jsonl").is_file()


def test_ac5c_relative_host_override_degrades(tmp_path, monkeypatch, capsys):
    """AC5c: a relative host override degrades: 'event log disabled:' on stderr, status ok,
    nothing written at the relative path."""
    from bytedigger_engine import config_provider  # noqa: PLC0415

    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)

    class _RelOverride(config_provider._DefaultConfigProvider):
        def event_log_path_override(self) -> str:
            return "rel/events.jsonl"

    config_provider.set_default_config_provider_factory(_RelOverride)
    try:
        rc = _main_in_process(monkeypatch, ["--workflow", "echo", "--run-id", "R5r",
                                            "--ctx-json", json.dumps({"org_config": {}, "question": "hi"})])
    finally:
        config_provider.reset_default_config_provider_factory()
    cap = capsys.readouterr()
    out = json.loads(cap.out.strip().splitlines()[-1])
    assert rc == 0 and out["status"] == "ok"
    assert "event log disabled:" in cap.err
    assert not (tmp_path / "rel").exists()


# --------------------------------------------------------------------------- AC9
def _register_counting(monkeypatch, name: str) -> list:
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    calls: list = []

    def _body(ctx, _prev):
        calls.append(1)
        return StepResult(status="ok", data={"n": len(calls)}, duration_ms=0, step_name=name)

    _register_extra_workflow(monkeypatch, name, _body)
    return calls


def test_ac9a_sentinel_serves_same_run_id_from_cache(tmp_path, monkeypatch):
    """AC9a: same workflow + --run-id R1 + ctx, no --event-log, twice -> body runs once and
    phase_sentinel_resumed is logged; a different run id executes."""
    root = tmp_path / "root"
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    calls = _register_counting(monkeypatch, "bd93_count")
    ctx = json.dumps({"org_config": {}, "question": "same"})
    argv = ["--workflow", "bd93_count", "--ctx-json", ctx]
    assert _main_in_process(monkeypatch, argv + ["--run-id", "R1"]) == 0
    assert _main_in_process(monkeypatch, argv + ["--run-id", "R1"]) == 0
    assert len(calls) == 1
    types = [e["event_type"] for e in _events(root / "R1" / "events.jsonl")]
    assert "phase_sentinel_resumed" in types
    assert _main_in_process(monkeypatch, argv + ["--run-id", "R2"]) == 0
    assert len(calls) == 2


def _oracle_case(monkeypatch, extra_argv: list[str], capsys) -> dict:
    """In-process run of a stubbed oracle workflow (succeeds, no LLM), ctx without scratchpad_dir."""
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    monkeypatch.setattr(oracle, "ORACLE_WORKFLOWS", frozenset({"bd93_oracle"}))
    _register_counting(monkeypatch, "bd93_oracle")
    _main_in_process(monkeypatch, ["--workflow", "bd93_oracle", "--run-id", "R9",
                                   "--ctx-json", json.dumps({"org_config": {}, "question": "x"})] + extra_argv)
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_ac9b_implicit_log_without_scratchpad_keeps_bd8_6b(tmp_path, monkeypatch, capsys):
    """AC9b: oracle workflow, no --event-log, no scratchpad_dir -> not E_ORACLE_INDETERMINATE
    ([bd8:6b] kept for an implicit log)."""
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    out = _oracle_case(monkeypatch, [], capsys)
    assert out.get("error_code") != "E_ORACLE_INDETERMINATE"
    assert out["status"] == "ok"


def test_ac9c_explicit_log_without_scratchpad_still_indeterminate(tmp_path, monkeypatch, capsys):
    """AC9c: same oracle case with explicit --event-log -> still E_ORACLE_INDETERMINATE."""
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(tmp_path / "root"))
    monkeypatch.chdir(tmp_path)
    out = _oracle_case(monkeypatch, ["--event-log", str(tmp_path / "x" / "e.jsonl")], capsys)
    assert out.get("error_code") == "E_ORACLE_INDETERMINATE"


# --------------------------------------------------------------------------- AC10
def test_ac10_flags_catalog_has_run_log_root():
    """AC10: flags_catalog declares HAL_RUN_LOG_ROOT with kind 'path'."""
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    entry = flags_catalog.FLAGS.get("HAL_RUN_LOG_ROOT")
    assert entry is not None and entry["kind"] == "path"
    assert entry["default"] is None


# --------------------------------------------------------------------------- AC7
@pytest.mark.parametrize("good_id", ["R1", "a.b-c_d", "0", "A" * 128, "9f3c2a1b7d4e"])
def test_ac7_run_scoped_log_path_accepts_valid_ids(tmp_path, good_id):
    """AC7: valid ids map to <root>/<id>/events.jsonl with no filesystem I/O."""
    from bytedigger_engine.event_log import run_scoped_log_path  # noqa: PLC0415

    root = tmp_path / "never-created"
    assert run_scoped_log_path(good_id, root) == root / good_id / "events.jsonl"
    assert not root.exists()


@pytest.mark.parametrize("bad_id", _BAD_RUN_IDS + ["", "-x", "a..b", "x y", "a\\b"])
def test_ac7_run_scoped_log_path_rejects_bad_ids(tmp_path, bad_id):
    """AC7: invalid ids raise ValueError."""
    from bytedigger_engine.event_log import run_scoped_log_path  # noqa: PLC0415

    with pytest.raises(ValueError):
        run_scoped_log_path(bad_id, tmp_path)


def test_ac7_run_scoped_log_path_rejects_relative_root():
    """AC7: a non-absolute root raises ValueError (result never depends on cwd)."""
    from bytedigger_engine.event_log import run_scoped_log_path  # noqa: PLC0415

    with pytest.raises(ValueError):
        run_scoped_log_path("R1", Path("relative/root"))


def test_ac7_run_scoped_log_path_expands_user_root(tmp_path, monkeypatch):
    """AC7: expanduser() is applied to the root."""
    from bytedigger_engine.event_log import run_scoped_log_path  # noqa: PLC0415

    monkeypatch.setenv("HOME", str(tmp_path))
    assert run_scoped_log_path("R1", Path("~/runs")) == Path(str(tmp_path)) / "runs" / "R1" / "events.jsonl"


def test_ac7_run_log_root_honours_hal_env(tmp_path, monkeypatch):
    """AC7: run_log_root() honours HAL_RUN_LOG_ROOT."""
    from bytedigger_engine.config_provider import run_log_root  # noqa: PLC0415

    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(tmp_path / "hal"))
    assert run_log_root() == tmp_path / "hal"


def test_ac7_run_log_root_honours_bytedigger_alias(tmp_path, monkeypatch):
    """AC7: run_log_root() honours BYTEDIGGER_RUN_LOG_ROOT (and BD_ alias)."""
    from bytedigger_engine.config_provider import run_log_root  # noqa: PLC0415

    monkeypatch.delenv("HAL_RUN_LOG_ROOT", raising=False)
    monkeypatch.setenv("BYTEDIGGER_RUN_LOG_ROOT", str(tmp_path / "byted"))
    assert run_log_root() == tmp_path / "byted"
    monkeypatch.delenv("BYTEDIGGER_RUN_LOG_ROOT")
    monkeypatch.setenv("BD_RUN_LOG_ROOT", str(tmp_path / "bd"))
    assert run_log_root() == tmp_path / "bd"


def test_ac7_run_log_root_neutral_default(tmp_path, monkeypatch):
    """AC7: unset env -> <home>/.bytedigger/runs from the neutral provider."""
    from bytedigger_engine import config_provider  # noqa: PLC0415

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HAL_RUN_LOG_ROOT", raising=False)
    config_provider.reset_default_config_provider_factory()
    assert config_provider.run_log_root() == Path.home().resolve() / ".bytedigger" / "runs"


def test_ac7_run_log_root_minimal_protocol_fallback(tmp_path, monkeypatch):
    """AC7: a provider lacking run_log_root()/home_root()/foreign_state_dirname() falls back to the neutral default."""
    from bytedigger_engine import config_provider  # noqa: PLC0415

    class _Minimal:
        def gate_enabled(self, env_var): return True
        def flag(self, env_var): return False
        def timeout_ms(self, env_var, default): return default
        def binary(self, env_var, default): return default
        def hal_root(self): return Path.cwd()
        def path(self, env_var, default): return default

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HAL_RUN_LOG_ROOT", raising=False)
    config_provider.set_default_config_provider_factory(_Minimal)
    try:
        got = config_provider.run_log_root()
    finally:
        config_provider.reset_default_config_provider_factory()
    assert got == Path.home().resolve() / ".bytedigger" / "runs"


# --------------------------------------------------------------------------- AC8
_AC8_SEEN_ROOTS: list[str] = []


@pytest.mark.parametrize("case", ["first", "second"])
def test_ac8_per_test_root_under_basetemp_and_distinct(tmp_path_factory, case):
    """AC8: inside a test HAL_RUN_LOG_ROOT is set (conftest's per-test fixture, NOT set here),
    lies under pytest's basetemp, not under Path.home(), and differs from the other test's root."""
    val = os.environ.get("HAL_RUN_LOG_ROOT")
    assert val, "conftest must provide HAL_RUN_LOG_ROOT per test"
    root = Path(val).resolve()
    base = tmp_path_factory.getbasetemp().resolve()
    assert base in root.parents, f"{root} not under basetemp {base}"
    home = Path.home().resolve()
    assert home not in root.parents or home in base.parents  # basetemp itself may sit under HOME
    assert val not in _AC8_SEEN_ROOTS, "root must differ between tests"
    _AC8_SEEN_ROOTS.append(val)
