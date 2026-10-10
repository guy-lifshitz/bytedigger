"""bd#192 pins: behaviour that must stay the same after the tail source-id change.

Spec: docs/decisions/2026-10-10-bd192-tail-source-id.md

These tests pass on the code before the change and must keep passing after it. They live
apart from test_bd192_tail_source_id.py, where every test fails until the change is made.

AC -> test map (the other ACs are in test_bd192_tail_source_id.py)
-------------------------------------------------------------------
AC3  test_ac3_switch_off_t1_no_new_key /
     test_ac3_switch_matrix_t1_off[0|BD_alias_0|BYTEDIGGER_alias_0|HAL_0_beats_alias_1]
AC5  test_ac5_switch_off_declares_nothing_and_emits_nothing[green|red_delta|red_full]
AC6  test_ac6_probe_timeout_finding_carries_nothing
AC8  test_ac8_switch_off_t2_no_new_key
AC10 test_ac10_real_tree_passes_lint
AC11 test_ac11_flag_owner_lint_clean

Harness. Same as in test_bd192_tail_source_id.py: the real `_verify_green_passing`,
`_red_collect_probe` (through `_verify_red_lint_rules`), `_build_green_prompt`,
`_build_red_prompt`, `_declared_injections` and the real dispatch chokepoint
`llm_subprocess.invoke_llm_subprocess`, with a recording backend adapter. Replaced
boundaries: `run_test_command`, `bounded_run` (wrapped by a spy that runs the real probe),
and `shutil.which("semgrep")`. Singleton resources (backend registry, telemetry slot) are
pre-staged and restored by the autouse fixture; nothing here depends on timing.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.lib.plugins.disk_truth import test_runner as _test_runner
from bytedigger_engine.workflows import phase_5_implement as p5
from bytedigger_engine.workflows.phase_workflows_common import _declared_injections

EVENT_TYPE = "model_invocation_attested"
DROP_EVENT = "tail_chunk_not_declared"
FLAG = "HAL_TAIL_SOURCE_ID"
REPO_ROOT = Path(__file__).resolve().parents[2]

PY_TAIL = "FAILED tests/test_a.py::test_one - AssertionError: py side marker\n1 failed in 0.10s\n"
TS_TAIL = "error: expect(received).toBe(expected)\n  ts side marker\n1 fail\n"

_TEST_PATHS = {"py": "tests/test_a.py", "ts": "tests/a.test.ts", "sh": "tests/a.test.sh"}
_CAP = p5.FINDINGS_MAX_CHARS


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    noop = lambda *a, **kw: None  # noqa: E731
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", noop)
    for modname in (
        "bytedigger_engine.lib.git_cwd",
        "bytedigger_engine.lib.project_root",
        "bytedigger_engine.lib.observability.emit_resolver",
    ):
        try:
            monkeypatch.setattr(importlib.import_module(modname), "emit_resolver_resolved", noop, raising=False)
        except ImportError:
            pass
    for name in (FLAG, "BD_TAIL_SOURCE_ID", "BYTEDIGGER_TAIL_SOURCE_ID", "HAL_IMPL_DELTA_RETRY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


@pytest.fixture
def p5_events(monkeypatch):
    """Events emitted through phase_5_implement's `_emit_safe`, as (event, payload) pairs."""
    events: list = []

    def _fake(event, payload=None, severity="info", **kw):
        events.append((event, dict(payload or {})))

    monkeypatch.setattr(p5, "_emit_safe", _fake)
    return events


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def attests(self) -> list:
        return [p for (t, p, _) in self.events if t == EVENT_TYPE]


class _RecordingAdapter:
    def __init__(self, raw_response: str = "done\n") -> None:
        self.raw_response = raw_response
        self.calls: list = []

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        data = {"raw_response": self.raw_response, "worker_written_paths": [],
                "manifest_source": "harness_tool_record"}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0,
                          step_name=kwargs.get("step_name", "rec"))


def _dispatch(built: StepResult, step_name: str = "invoke_bd192"):
    """Dispatch a built prompt through the real chokepoint with the real
    `_declared_injections(built.data)`. Returns (result, adapter, attested injections)."""
    assert isinstance(built, StepResult) and built.status == "ok" and isinstance(built.data, dict) \
        and built.data.get("prompt"), (
        f"fixture precondition: builder must return an ok prompt, got status="
        f"{getattr(built, 'status', None)!r} error_code={getattr(built, 'error_code', None)!r}")
    declared = _declared_injections(built.data)
    adapter = _RecordingAdapter()
    llm_subprocess.register_backend(
        "claude-subprocess", adapter, manifest_source="harness_tool_record",
        capabilities=frozenset(("manifest", "progress_since", "abort")), overwrite=True,
    )
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="bd192", step_name=step_name)
    result = llm_subprocess.invoke_llm_subprocess(
        prompt=built.data["prompt"], model="sonnet", timeout_sec=30,
        step_name=step_name, injections=declared,
    )
    assert result.status == "ok", (
        f"dispatch must succeed: status={result.status!r} error_code={result.error_code!r} error={result.error!r}")
    assert len(adapter.calls) == 1, f"backend adapter must be called once, got {len(adapter.calls)}"
    attests = log.attests()
    assert len(attests) == 1, f"expected one {EVENT_TYPE!r} event, got {len(attests)}"
    return result, adapter, attests[0]["injections"]


def _declared_sids(built: StepResult) -> set:
    record = built.data.get("injected_blocks")
    return {b["source_id"] for b in (record or {}).get("blocks", [])}


def _drop_events(events: list) -> list:
    return [p for (e, p) in events if e == DROP_EVENT]


# --- T1 fixture -------------------------------------------------------------

def _run_green(tmp_path: Path, monkeypatch, tails: list, cycle_count: int = 1):
    """Run the real `_verify_green_passing` with one failing group per (kind, text).

    `run_test_command` is the only replaced boundary: it hands back a result object whose
    stdout_path is a real file holding the text. Returns (result, {kind: stdout path})."""
    runs = tmp_path / "runs"
    work = tmp_path / "work"
    runs.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    (runs / "empty.err").write_text("", encoding="utf-8")
    out: dict = {}
    for kind, text in tails:
        f = runs / f"{kind}.out"
        f.write_text(text, encoding="utf-8")
        out[kind] = str(f)

    def _fake(argv, git_cwd, timeout=None, **kw):
        for kind in ("py", "ts", "sh"):
            if kind in out and any(str(a).endswith(_TEST_PATHS[kind]) for a in argv):
                return _test_runner.TestRunResult(
                    exit_code=1, n_passed=0, n_failed=1,
                    stdout_path=out[kind], stderr_path=str(runs / "empty.err"))
        raise AssertionError(f"unexpected test argv {argv!r}")

    monkeypatch.setattr(p5, "run_test_command", _fake)
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"git_cwd": str(work), "verify_green_passing": True},
        question="q", session_id="bd192", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={
            "green_log_path": "tests/build-green-output.log",
            "spec_path": "scratchpad/spec.md",
            "red_log_path": "tests/build-red-output.log",
            "validation_doc_path": "scratchpad/validation.md",
            "green_status": "PASS",
            "red_test_paths": [_TEST_PATHS[kind] for kind, _ in tails],
            "cycle_count": cycle_count,
        },
        duration_ms=0, step_name="write_green_artifact",
    )
    return p5._verify_green_passing(ctx, prev), out


def _blob(tag: str, n: int) -> str:
    text = "".join(f"{tag}{i:06d} failure line\n" for i in range(n // 20 + 2))
    return text[:n]


def _independent_findings(tails: list) -> list:
    return [" ".join(text for _, text in tails)[-_CAP:]]


# --- T2 fixture -------------------------------------------------------------

_BROKEN_TEST = "def test_broken(:\n    assert True\n"


class _ProbeSpy:
    """Wraps phase_5_implement.bounded_run. The collect probe (`pytest --co`) runs for real the
    first time and its process is recorded; `fake`, when set, is handed back instead (a
    timed-out process). Every other call is passed through untouched."""

    def __init__(self, real) -> None:
        self.real = real
        self.procs: list = []
        self.fake = None

    def __call__(self, argv, *a, **kw):
        if "--co" in argv and "pytest" in " ".join(str(x) for x in argv):
            if self.fake is not None:
                return self.fake
            proc = self.real(argv, *a, **kw)
            self.procs.append(proc)
            return proc
        return self.real(argv, *a, **kw)


def _install_probe_spy(monkeypatch) -> _ProbeSpy:
    spy = _ProbeSpy(p5.bounded_run)
    monkeypatch.setattr(p5, "bounded_run", spy)
    real_which = shutil.which
    monkeypatch.setattr(
        p5.shutil, "which", lambda name, *a, **k: None if name == "semgrep" else real_which(name, *a, **k))
    return spy


def _run_probe(tmp_path: Path, monkeypatch):
    """The real `_verify_red_lint_rules` over a real RED file that fails collection.
    Returns (result, collect-probe finding)."""
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True, exist_ok=True)
    (work / "tests" / "test_broken.py").write_text(_BROKEN_TEST, encoding="utf-8")
    monkeypatch.setenv("HAL_RED_COLLECT_PROBE_ENFORCE", "1")
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config={"git_cwd": str(work)},
        question="q", session_id="bd192", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={"red_test_paths": ["tests/test_broken.py"], "red_log_path": "tests/build-red-output.log",
              "spec_path": "scratchpad/spec.md", "cycle": 1},
        duration_ms=0, step_name="commit_red_tests",
    )
    result = p5._verify_red_lint_rules(ctx, prev)
    assert result.status == "error" and result.recoverable is True and result.error_code == "E_RED_COLLECT_PROBE", (
        f"fixture precondition: collect-probe delta-retry expected, got status={result.status!r} "
        f"recoverable={result.recoverable!r} error_code={result.error_code!r}")
    findings = [f for f in (result.data or {}).get("preflight_findings", []) if f.get("rule") == "collect-probe"]
    assert len(findings) == 1, f"fixture precondition: one collect-probe finding, got {findings!r}"
    return result, findings[0]


# --- builder fixtures -------------------------------------------------------

def _prompt_env(root: Path):
    scratch = root / "scratch"
    work = root / "work"
    scratch.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    for name, text in (("spec.md", "## Spec\nDo thing.\n"), ("red.md", "RED COMPLETE\n"),
                       ("validation.md", "## Verdict\nPASS\n"), ("first-block.md", "TASK: fix the thing\n")):
        (scratch / name).write_text(text, encoding="utf-8")
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "git_cwd": str(work)},
        question="fix the thing", session_id="bd192", persona="hal", framework=None, domain=None,
    )
    return ctx, scratch


def _green_prev(data: dict, scratch: Path) -> dict:
    return {**data, "cycle": 2, "spec_path": str(scratch / "spec.md"),
            "red_log_path": str(scratch / "red.md"), "validation_doc_path": str(scratch / "validation.md")}


def _build_green(root: Path, data: dict) -> StepResult:
    ctx, scratch = _prompt_env(root)
    built = p5._build_green_prompt(ctx, _green_prev(data, scratch))
    assert built.status == "ok" and "## TEST FAILURES" in built.data["prompt"], (
        f"fixture precondition: GREEN retry prompt with a TEST FAILURES block; status={built.status!r} "
        f"error_code={built.error_code!r}")
    return built


def _build_red(root: Path, data: dict, monkeypatch, branch: str) -> StepResult:
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "1" if branch == "delta" else "0")
    ctx, _ = _prompt_env(root)
    built = p5._build_red_prompt(ctx, {**data, "cycle": 2})
    assert built.status == "ok", f"fixture precondition: ok RED prompt, got {built.status!r} {built.error_code!r}"
    assert bool(built.data.get("delta_retry")) is (branch == "delta"), "fixture precondition: requested branch ran"
    return built


def _manual_green_data() -> dict:
    return {
        "green_test_findings": [" ".join([PY_TAIL, TS_TAIL])],
        "red_test_paths": [_TEST_PATHS["py"], _TEST_PATHS["ts"]],
    }


_VALID_GREEN = [
    {"source_id": "/runs/py.out", "content": PY_TAIL},
    {"source_id": "/runs/ts.out", "content": TS_TAIL.rstrip()},
]
_ABSENT = {"source_id": "/runs/gone.out", "content": "NOT-IN-ANY-PROMPT-bd192-marker"}
_MALFORMED = [
    {"source_id": "", "content": PY_TAIL},
    {"source_id": "/runs/bad-type.out", "content": 5},
]


# ---------------------------------------------------------------------------
# AC3 - switch off, T1
# ---------------------------------------------------------------------------

def test_ac3_switch_off_t1_no_new_key(tmp_path, monkeypatch) -> None:
    """Pin: with the switch off no chunk record is produced and the text is the pre-lot text."""
    monkeypatch.setenv(FLAG, "0")
    tails = [("py", _blob("P", 30000) + "\n"), ("ts", _blob("T", 5000) + "\n")]
    result, _ = _run_green(tmp_path, monkeypatch, tails)
    data = result.data or {}
    assert "green_test_chunks" not in data
    assert data.get("green_test_findings") == _independent_findings(tails)


@pytest.mark.parametrize("env", [
    {FLAG: "0"},
    {"BD_TAIL_SOURCE_ID": "0"},
    {"BYTEDIGGER_TAIL_SOURCE_ID": "0"},
    {FLAG: "0", "BD_TAIL_SOURCE_ID": "1"},
], ids=["0", "BD_alias_0", "BYTEDIGGER_alias_0", "HAL_0_beats_alias_1"])
def test_ac3_switch_matrix_t1_off(env, tmp_path, monkeypatch) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    tails = [("py", _blob("P", 30000) + "\n"), ("ts", _blob("T", 5000) + "\n")]
    result, _ = _run_green(tmp_path, monkeypatch, tails)
    data = result.data or {}
    assert "green_test_chunks" not in data, f"env={env!r}: chunks present"
    assert data.get("green_test_findings") == _independent_findings(tails)


# ---------------------------------------------------------------------------
# AC5 - switch off declares nothing and emits nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("where", ["green", "red_delta", "red_full"])
def test_ac5_switch_off_declares_nothing_and_emits_nothing(where, tmp_path, monkeypatch, p5_events) -> None:
    monkeypatch.setenv(FLAG, "0")
    if where == "green":
        chunks = [*_VALID_GREEN, _ABSENT, *_MALFORMED]
        built = _build_green(tmp_path, {**_manual_green_data(), "green_test_chunks": chunks})
    else:
        valid = {"source_id": "collect-probe:/w/tests/test_x.py", "content": "boom tail text"}
        chunks = [valid, _ABSENT, *_MALFORMED]
        data = {"findings": "E_RED_COLLECT_PROBE at /w/x.py:None 'boom tail text'", "findings_chunks": chunks}
        built = _build_red(tmp_path, data, monkeypatch, "delta" if where == "red_delta" else "full")
    assert not (_declared_sids(built) & {c["source_id"] for c in chunks}), "switch off: nothing is declared from tail records"
    assert _drop_events(p5_events) == [], "switch off: no tail_chunk_not_declared event"
    _dispatch(built)


# ---------------------------------------------------------------------------
# AC6 - probe timeout carries nothing
# ---------------------------------------------------------------------------

def test_ac6_probe_timeout_finding_carries_nothing(tmp_path, monkeypatch) -> None:
    spy = _install_probe_spy(monkeypatch)
    spy.fake = types.SimpleNamespace(returncode=124, stdout="", stderr="")
    result, finding = _run_probe(tmp_path, monkeypatch)
    assert finding["evidence"] == "collect-probe timeout"
    assert "source_id" not in finding and "source_chunk" not in finding
    assert not (result.data or {}).get("findings_chunks")


# ---------------------------------------------------------------------------
# AC8 - switch off, T2
# ---------------------------------------------------------------------------

def test_ac8_switch_off_t2_no_new_key(tmp_path, monkeypatch) -> None:
    """Pin: with the switch off the finding and the retry data carry no tail record, and the
    delta prompt declares nothing."""
    monkeypatch.setenv(FLAG, "0")
    _install_probe_spy(monkeypatch)
    result, finding = _run_probe(tmp_path / "probe", monkeypatch)
    assert "source_id" not in finding and "source_chunk" not in finding
    assert "findings_chunks" not in result.data
    built = _build_red(tmp_path / "build", result.data, monkeypatch, "delta")
    assert built.data["injected_blocks"] is None


# ---------------------------------------------------------------------------
# AC10 / AC11 - lint pins
# ---------------------------------------------------------------------------

def _engine_root() -> Path:
    import bytedigger_engine
    return Path(bytedigger_engine.__file__).parent


def test_ac10_real_tree_passes_lint() -> None:
    """Pin: every site is still keyed and no key is stale after the change."""
    lint = importlib.import_module("bytedigger_engine.conformance.class_i_lint")
    assert lint.check(_engine_root(), lint.load_inventory()) == []


def test_ac11_flag_owner_lint_clean() -> None:
    proc = subprocess.run([sys.executable, "scripts/flag_owner_lint.py"], cwd=str(REPO_ROOT),
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"flag_owner_lint rc={proc.returncode} {proc.stdout!r} {proc.stderr!r}"
