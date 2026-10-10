"""bd#192 RED: the two carried test-output tails are declared with a source id.

Spec: docs/decisions/2026-10-10-bd192-tail-source-id.md

Two slices of subprocess output reach a prompt as class-I text and are not declared:

  T1  `_verify_green_passing`: the stdout tails of the failing test groups, joined and
      left-truncated, carried as `green_test_findings` and rendered in the GREEN retry prompt.
  T2  `_red_collect_probe`: the last 400 characters of the probe output, carried in a batch
      finding and rendered in the RED retry prompt.

AC -> test map
--------------
AC1  test_ac1_t1_record_retry / test_ac1_t1_record_terminal
AC2  test_ac2_chunk_rule[<matrix>]
AC3  test_ac3_switch_matrix_t1[unset|1|yes|HAL_1_beats_alias_0] /
     test_ac3_on_and_off_share_the_findings_text
AC4  test_ac4_green_prompt_declares_chunks_through_chokepoint
AC5  test_ac5_green_bad_chunks_dropped_with_event_and_dispatch_ok /
     test_ac5_red_bad_chunks_dropped_with_event_and_dispatch_ok[delta|full]
AC6  test_ac6_t2_record_and_carry
AC7  test_ac7_red_prompt_declares_chunk_through_chokepoint[delta|full] /
     test_ac7_chunk_absent_from_prompt_is_dropped_and_dispatch_ok[delta|full]
AC8  test_ac8_on_and_off_share_the_findings_text
AC9  test_ac9_auth_modes_give_identical_declarations
AC10 test_ac10_* (inventory entries, AUTHORSHIP_SPEC section 4)
AC11 test_ac11_flag_registered / test_ac11_events_doc_has_row
(AC12 = the sibling suites, run separately.)

The tests that pass before the change (switch-off pins, the probe-timeout pin and the two lint
pins) are in test_bd192_tail_source_id_pins.py, so every test in this file fails until the
change is made:
AC3  test_ac3_switch_off_t1_no_new_key / test_ac3_switch_matrix_t1_off[<env>]
AC5  test_ac5_switch_off_declares_nothing_and_emits_nothing[green|red_delta|red_full]
AC6  test_ac6_probe_timeout_finding_carries_nothing
AC8  test_ac8_switch_off_t2_no_new_key
AC10 test_ac10_real_tree_passes_lint
AC11 test_ac11_flag_owner_lint_clean

Harness. The real functions are driven: `_verify_green_passing`, `_red_collect_probe` (through
`_verify_red_lint_rules`), `_build_green_prompt`, `_build_red_prompt`, `_declared_injections`
and the real dispatch chokepoint `llm_subprocess.invoke_llm_subprocess`, with a recording backend
adapter registered through `register_backend`. Replaced boundaries:

  * `run_test_command` (the test-runner process boundary) writes real stdout files and returns
    the result object `_verify_green_passing` already consumes.
  * `bounded_run` in phase_5_implement is wrapped by a spy that runs the real probe and records
    its real output; the replay and timeout cases hand back that recorded process (or a
    timed-out one) so no assertion depends on pytest's own wall-clock summary line.
  * `shutil.which("semgrep")` answers None so the batch holds only the collect-probe finding
    on machines that have semgrep installed.

Singleton resources (the backend registry, the telemetry slot) are pre-staged and restored by the
autouse fixture; nothing here depends on timing.
"""
from __future__ import annotations

import importlib
import re
import shutil
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.conformance.attest import hash_text
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.lib.plugins.disk_truth import test_runner as _test_runner
from bytedigger_engine.workflows import phase_5_implement as p5
from bytedigger_engine.workflows.phase_workflows_common import _declared_injections

EVENT_TYPE = "model_invocation_attested"
DROP_EVENT = "tail_chunk_not_declared"
FLAG = "HAL_TAIL_SOURCE_ID"
REPO_ROOT = Path(__file__).resolve().parents[2]
T1_KEY = "workflows/phase_5_implement.py::_verify_green_passing::_read_text_or_empty#0"
T2_KEY = "workflows/phase_5_implement.py::_red_collect_probe::bounded_run#0"

PY_TAIL = "FAILED tests/test_a.py::test_one - AssertionError: py side marker\n1 failed in 0.10s\n"
TS_TAIL = "error: expect(received).toBe(expected)\n  ts side marker\n1 fail\n"

_TEST_PATHS = {"py": "tests/test_a.py", "ts": "tests/a.test.ts", "sh": "tests/a.test.sh"}


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


def _as_blocks(chunks: list) -> list:
    return [{"source_id": c["source_id"], "content": c["content"]} for c in chunks]


def _as_attested(chunks: list) -> list:
    return [{"source_id": c["source_id"], "sha256": hash_text(c["content"])} for c in chunks]


def _assert_tail_declared(built: StepResult, chunks: list) -> None:
    """The chunks are the last blocks of the bound record, in prompt order, after the blocks the
    builder already declared, and the record is bound to the final prompt."""
    prompt = built.data["prompt"]
    record = built.data.get("injected_blocks")
    assert record, f"injected_blocks must carry the tail chunks, got {record!r}"
    assert record["prompt_sha256"] == hash_text(prompt)
    blocks = record["blocks"]
    n = len(chunks)
    assert blocks[-n:] == _as_blocks(chunks), f"tail chunks must close the record; blocks={blocks!r}"
    sids = {c["source_id"] for c in chunks}
    assert not [b for b in blocks[:-n] if b["source_id"] in sids], "a tail source id appears before the tail"
    positions = [prompt.index(c["content"]) for c in chunks]
    assert positions == sorted(positions) and len(set(positions)) == len(positions), (
        f"chunks must be declared in prompt order; positions={positions}")


def _declared_sids(built: StepResult) -> set:
    record = built.data.get("injected_blocks")
    return {b["source_id"] for b in (record or {}).get("blocks", [])}


# --- T1 fixture -------------------------------------------------------------

def _run_green(tmp_path: Path, monkeypatch, tails: list, cycle_count: int = 1):
    """Run the real `_verify_green_passing` with one failing group per (kind, text).

    `run_test_command` is the only replaced boundary: it hands back a result object whose
    stdout_path is a real file holding the text. Returns (result, {kind: stdout path})."""
    runs = tmp_path / "runs"
    work = tmp_path / "work"
    runs.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
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


def _expected_chunks(entries: list, cap: int) -> list:
    """Independent statement of the chunk rule. entries = [(source_id, tail)]: the part of each
    tail that survives the left-truncation of the joined string, cut at the rstripped end."""
    full = " ".join(text for _, text in entries)
    cut = max(0, len(full) - cap)
    end_all = len(full.rstrip())
    out = []
    pos = 0
    for sid, text in entries:
        start, end = pos, pos + len(text)
        pos = end + 1
        lo, hi = max(start, cut), min(end, end_all)
        piece = full[lo:hi] if hi > lo else ""
        if piece.strip():
            out.append({"source_id": sid, "content": piece})
    return out


def _entries(tails: list, out: dict) -> list:
    return [(out[kind], text) for kind, text in tails]


def _blob(tag: str, n: int) -> str:
    text = "".join(f"{tag}{i:06d} failure line\n" for i in range(n // 20 + 2))
    return text[:n]


# --- T2 fixture -------------------------------------------------------------

_BROKEN_TEST = "def test_broken(:\n    assert True\n"


class _ProbeSpy:
    """Wraps phase_5_implement.bounded_run. The collect probe (`pytest --co`) runs for real the
    first time and its process is recorded; `fake`, when set, is handed back instead (replay of the
    recorded process, or a timed-out one). Every other call is passed through untouched."""

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


def _real_tail(spy: _ProbeSpy) -> str:
    assert len(spy.procs) == 1, "fixture precondition: the real probe ran once"
    proc = spy.procs[0]
    return ((proc.stdout or "") + (proc.stderr or ""))[-400:]


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


def _drop_events(events: list) -> list:
    return [p for (e, p) in events if e == DROP_EVENT]


# ---------------------------------------------------------------------------
# AC1 - T1 record
# ---------------------------------------------------------------------------

def _two_group_tails():
    return [("py", PY_TAIL), ("ts", TS_TAIL)]


def _assert_t1_record(result, out) -> None:
    tails = _two_group_tails()
    data = result.data or {}
    assert data.get("green_test_findings") == [" ".join([PY_TAIL, TS_TAIL])]
    assert "green_test_chunks" in data, f"green_test_chunks missing; data keys={sorted(data)}"
    assert data["green_test_chunks"] == [
        {"source_id": out["py"], "content": PY_TAIL},
        {"source_id": out["ts"], "content": TS_TAIL.rstrip()},
    ]
    assert data["green_test_chunks"] == _expected_chunks(_entries(tails, out), p5.FINDINGS_MAX_CHARS)


def test_ac1_t1_record_retry(tmp_path, monkeypatch) -> None:
    result, out = _run_green(tmp_path, monkeypatch, _two_group_tails(), cycle_count=1)
    assert result.error_code == "E_GREEN_NOT_PASSING" and result.recoverable is True
    _assert_t1_record(result, out)


def test_ac1_t1_record_terminal(tmp_path, monkeypatch) -> None:
    result, out = _run_green(tmp_path, monkeypatch, _two_group_tails(), cycle_count=p5.GREEN_TEST_CYCLE_CAP)
    assert result.error_code == "E_GREEN_NOT_PASSING" and result.recoverable is False
    _assert_t1_record(result, out)


# ---------------------------------------------------------------------------
# AC2 - chunk rule
# ---------------------------------------------------------------------------

_CAP = p5.FINDINGS_MAX_CHARS

_CHUNK_MATRIX = {
    # id: (tails, joinable). joinable: the chunks, joined with one space, equal the rstripped findings.
    "cut_inside_first": ([("py", _blob("P", 30000)), ("ts", _blob("T", 5000))], True),
    "cut_past_whole_first": ([("py", _blob("P", 3000)), ("ts", _blob("T", 33000))], True),
    "cut_exactly_at_end_of_first": ([("py", _blob("P", 3000)), ("ts", _blob("T", _CAP - 1))], False),
    "whitespace_only_middle": ([("py", _blob("P", 20000)), ("ts", "  \n\t \n"), ("sh", _blob("S", 20000))], False),
    "whitespace_only_last": ([("py", _blob("P", 33000)), ("ts", "real tail\n"), ("sh", "   \n")], False),
}


@pytest.mark.parametrize("case", sorted(_CHUNK_MATRIX))
def test_ac2_chunk_rule(case, tmp_path, monkeypatch) -> None:
    tails, joinable = _CHUNK_MATRIX[case]
    result, out = _run_green(tmp_path, monkeypatch, tails, cycle_count=1)
    data = result.data or {}
    full = " ".join(text for _, text in tails)
    assert data.get("green_test_findings") == [full[-_CAP:] if len(full) > _CAP else full]
    chunks = data.get("green_test_chunks")
    assert chunks is not None, f"green_test_chunks missing; data keys={sorted(data)}"
    expected = _expected_chunks(_entries(tails, out), _CAP)
    assert chunks == expected
    assert all(c["content"].strip() for c in chunks), "a chunk with no non-whitespace character is not declared"
    if joinable:
        assert " ".join(c["content"] for c in chunks) == data["green_test_findings"][0].rstrip()
    if case == "cut_inside_first":
        assert chunks[0]["source_id"] == out["py"] and 0 < len(chunks[0]["content"]) < len(tails[0][1])
        assert len(chunks) == 2
    if case == "cut_past_whole_first":
        assert [c["source_id"] for c in chunks] == [out["ts"]]
    if case == "whitespace_only_middle":
        assert [c["source_id"] for c in chunks] == [out["py"], out["sh"]]


# ---------------------------------------------------------------------------
# AC3 - switch, T1
# ---------------------------------------------------------------------------

def _independent_findings(tails: list) -> list:
    return [" ".join(text for _, text in tails)[-_CAP:]]


@pytest.mark.parametrize("env", [
    {},
    {FLAG: "1"},
    {FLAG: "yes"},
    {FLAG: "1", "BD_TAIL_SOURCE_ID": "0"},
], ids=["unset", "1", "yes", "HAL_1_beats_alias_0"])
def test_ac3_switch_matrix_t1(env, tmp_path, monkeypatch) -> None:
    """On-cases. The off-cases are pinned in test_bd192_tail_source_id_pins.py."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    tails = [("py", _blob("P", 30000) + "\n"), ("ts", _blob("T", 5000) + "\n")]
    result, out = _run_green(tmp_path, monkeypatch, tails)
    data = result.data or {}
    assert "green_test_chunks" in data, f"env={env!r}: chunks missing"
    assert data.get("green_test_findings") == _independent_findings(tails)
    assert data["green_test_chunks"] == _expected_chunks(_entries(tails, out), _CAP)


def test_ac3_on_and_off_share_the_findings_text(tmp_path, monkeypatch) -> None:
    tails = [("py", _blob("P", 30000) + "\n"), ("ts", _blob("T", 5000) + "\n")]
    on, _ = _run_green(tmp_path, monkeypatch, tails)
    monkeypatch.setenv(FLAG, "0")
    off, _ = _run_green(tmp_path, monkeypatch, tails)
    assert "green_test_chunks" in (on.data or {}), "the on state must carry the chunk record"
    assert "green_test_chunks" not in (off.data or {})
    assert on.data["green_test_findings"] == off.data["green_test_findings"] == _independent_findings(tails)


# ---------------------------------------------------------------------------
# AC4 - GREEN prompt to chokepoint
# ---------------------------------------------------------------------------

def test_ac4_green_prompt_declares_chunks_through_chokepoint(tmp_path, monkeypatch) -> None:
    result, out = _run_green(tmp_path, monkeypatch, _two_group_tails(), cycle_count=1)
    chunks = (result.data or {}).get("green_test_chunks")
    assert chunks, f"retry data must carry green_test_chunks; keys={sorted(result.data or {})}"
    built = _build_green(tmp_path / "build", result.data)
    assert "## TEST FAILURES" in built.data["prompt"]
    _assert_tail_declared(built, chunks)
    _, _, injections = _dispatch(built, "invoke_green_llm")
    assert injections[-len(chunks):] == _as_attested(chunks), f"attested={injections!r}"


# ---------------------------------------------------------------------------
# AC5 - a chunk that cannot be declared is dropped with an event, never refused
# ---------------------------------------------------------------------------

def _assert_drops(events: list, absent_sid: str) -> None:
    drops = _drop_events(events)
    reasons = sorted(p.get("reason") for p in drops)
    assert reasons == ["absent_from_prompt", "malformed", "malformed"], f"drop events={drops!r}"
    for p in drops:
        assert p.get("phase") == 5 and isinstance(p.get("step"), str) and p["step"], p
        assert "source_id" in p, p
    absent = [p for p in drops if p["reason"] == "absent_from_prompt"][0]
    assert absent["source_id"] == absent_sid
    empty_id = [p for p in drops if p["reason"] == "malformed" and p["source_id"] in ("", None)]
    typed = [p for p in drops if p["reason"] == "malformed" and p["source_id"] == "/runs/bad-type.out"]
    assert len(empty_id) == 1 and len(typed) == 1, drops


def test_ac5_green_bad_chunks_dropped_with_event_and_dispatch_ok(tmp_path, monkeypatch, p5_events) -> None:
    data = {**_manual_green_data(), "green_test_chunks": [_VALID_GREEN[0], _ABSENT, *_MALFORMED, _VALID_GREEN[1]]}
    built = _build_green(tmp_path, data)
    _assert_tail_declared(built, _VALID_GREEN)
    _assert_drops(p5_events, _ABSENT["source_id"])
    _, adapter, injections = _dispatch(built, "invoke_green_llm")
    assert injections[-2:] == _as_attested(_VALID_GREEN)
    assert not [i for i in injections if i["source_id"] in (_ABSENT["source_id"], "", "/runs/bad-type.out")]


@pytest.mark.parametrize("branch", ["delta", "full"])
def test_ac5_red_bad_chunks_dropped_with_event_and_dispatch_ok(branch, tmp_path, monkeypatch, p5_events) -> None:
    findings = "E_RED_COLLECT_PROBE at /w/tests/test_x.py:None 'boom tail text'"
    valid = {"source_id": "collect-probe:/w/tests/test_x.py", "content": "boom tail text"}
    data = {"findings": findings, "findings_chunks": [valid, _ABSENT, *_MALFORMED[:1], {"source_id": "/runs/bad-type.out", "content": 5}]}
    built = _build_red(tmp_path, data, monkeypatch, branch)
    _assert_tail_declared(built, [valid])
    _assert_drops(p5_events, _ABSENT["source_id"])
    _, _, injections = _dispatch(built, "invoke_red_llm")
    assert injections[-1:] == _as_attested([valid])


# ---------------------------------------------------------------------------
# AC6 - T2 record and carry
# ---------------------------------------------------------------------------

def test_ac6_t2_record_and_carry(tmp_path, monkeypatch) -> None:
    spy = _install_probe_spy(monkeypatch)
    result, finding = _run_probe(tmp_path, monkeypatch)
    tail = _real_tail(spy)
    assert tail.strip(), "fixture precondition: the broken file produced probe output"
    sid = "collect-probe:" + finding["path"]
    assert finding.get("source_id") == sid, f"finding keys={sorted(finding)}"
    assert finding.get("source_chunk") == tail
    assert finding["evidence"] == f"{finding['path']}: {tail}", "the evidence text is unchanged"
    data = result.data
    assert data.get("findings_chunks") == [{"source_id": sid, "content": tail}], f"data keys={sorted(data)}"
    assert tail in data["findings"]


# ---------------------------------------------------------------------------
# AC7 - RED prompt to chokepoint, both branches
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("branch", ["delta", "full"])
def test_ac7_red_prompt_declares_chunk_through_chokepoint(branch, tmp_path, monkeypatch) -> None:
    spy = _install_probe_spy(monkeypatch)
    result, finding = _run_probe(tmp_path / "probe", monkeypatch)
    chunks = (result.data or {}).get("findings_chunks")
    assert chunks, f"retry data must carry findings_chunks; keys={sorted(result.data or {})}"
    built = _build_red(tmp_path / "build", result.data, monkeypatch, branch)
    _assert_tail_declared(built, chunks)
    if branch == "delta":
        assert built.data["injected_blocks"]["blocks"] == _as_blocks(chunks), "the delta prompt declares only the tail"
    _, _, injections = _dispatch(built, "invoke_red_llm")
    assert injections[-len(chunks):] == _as_attested(chunks), f"attested={injections!r}"


@pytest.mark.parametrize("branch", ["delta", "full"])
def test_ac7_chunk_absent_from_prompt_is_dropped_and_dispatch_ok(branch, tmp_path, monkeypatch, p5_events) -> None:
    spy = _install_probe_spy(monkeypatch)
    result, finding = _run_probe(tmp_path / "probe", monkeypatch)
    chunks = (result.data or {}).get("findings_chunks")
    assert chunks, f"retry data must carry findings_chunks; keys={sorted(result.data or {})}"
    # The engine truncated the findings: the chunk no longer occurs in the prompt.
    data = {**result.data, "findings": "E_RED_COLLECT_PROBE (output omitted)"}
    built = _build_red(tmp_path / "build", data, monkeypatch, branch)
    assert not (_declared_sids(built) & {c["source_id"] for c in chunks})
    drops = _drop_events(p5_events)
    assert [(p["reason"], p["source_id"], p["phase"]) for p in drops] == [
        ("absent_from_prompt", chunks[0]["source_id"], 5)], drops
    _, _, injections = _dispatch(built, "invoke_red_llm")
    assert not [i for i in injections if i["source_id"] == chunks[0]["source_id"]]


# ---------------------------------------------------------------------------
# AC8 - switch, T2
# ---------------------------------------------------------------------------

def test_ac8_on_and_off_share_the_findings_text(tmp_path, monkeypatch) -> None:
    spy = _install_probe_spy(monkeypatch)
    on_result, on_finding = _run_probe(tmp_path, monkeypatch)
    assert on_finding.get("source_id") and on_finding.get("source_chunk"), "the on state must carry the tail record"
    spy.fake = spy.procs[0]  # replay the same real output for the off run
    monkeypatch.setenv(FLAG, "0")
    off_result, off_finding = _run_probe(tmp_path, monkeypatch)
    assert "source_id" not in off_finding and "findings_chunks" not in off_result.data
    assert off_result.data["findings"] == on_result.data["findings"]
    assert off_finding["evidence"] == on_finding["evidence"]


# ---------------------------------------------------------------------------
# AC9 - auth modes
# ---------------------------------------------------------------------------

def _set_auth(monkeypatch, mode: str) -> None:
    if mode == "api_key":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "bd192-test-key")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "bd192-test-token")


def test_ac9_auth_modes_give_identical_declarations(tmp_path, monkeypatch) -> None:
    spy = _install_probe_spy(monkeypatch)
    probe_result, _ = _run_probe(tmp_path / "probe", monkeypatch)
    spy.fake = spy.procs[0]
    summaries = {}
    for mode in ("api_key", "oauth"):
        _set_auth(monkeypatch, mode)
        run_dir = tmp_path / "t1"  # same files in both modes: same source ids
        green, _ = _run_green(run_dir, monkeypatch, _two_group_tails(), cycle_count=1)
        green_chunks = (green.data or {}).get("green_test_chunks")
        assert green_chunks, f"{mode}: green_test_chunks missing"
        built_g = _build_green(tmp_path / f"build_g_{mode}", green.data)
        _assert_tail_declared(built_g, green_chunks)
        _, _, inj_g = _dispatch(built_g, "invoke_green_llm")
        summary = {"green_chunks": green_chunks,
                   "green_blocks": built_g.data["injected_blocks"]["blocks"], "green_inj": inj_g}
        red_chunks = probe_result.data.get("findings_chunks")
        assert red_chunks, f"{mode}: findings_chunks missing"
        for branch in ("delta", "full"):
            built_r = _build_red(tmp_path / f"build_r_{mode}_{branch}", probe_result.data, monkeypatch, branch)
            _assert_tail_declared(built_r, red_chunks)
            _, _, inj_r = _dispatch(built_r, "invoke_red_llm")
            summary[f"red_{branch}_blocks"] = built_r.data["injected_blocks"]["blocks"]
            summary[f"red_{branch}_inj"] = inj_r
        summaries[mode] = summary
    assert summaries["api_key"] == summaries["oauth"]


# ---------------------------------------------------------------------------
# AC10 - inventory and AUTHORSHIP_SPEC section 4
# ---------------------------------------------------------------------------

def _lint():
    return importlib.import_module("bytedigger_engine.conformance.class_i_lint")


def _engine_root() -> Path:
    import bytedigger_engine
    return Path(bytedigger_engine.__file__).parent


def _authorship_text() -> str:
    return (_engine_root() / "conformance" / "AUTHORSHIP_SPEC.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", [T1_KEY, T2_KEY], ids=["T1", "T2"])
def test_ac10_inventory_declares_tail_site(key) -> None:
    sites = _lint().load_inventory()["sites"]
    assert key in sites, f"{key} missing from the inventory"
    entry = sites[key]
    assert entry.get("class") == "I-declared" and "issue" not in entry, entry
    assert str(entry.get("note", "")).strip(), "the entry keeps a note saying where it is declared"


def test_ac10_no_entry_defers_to_192() -> None:
    sites = _lint().load_inventory()["sites"]
    assert sorted(k for k, v in sites.items() if v.get("issue") == "192") == []


def test_ac10_authorship_spec_names_both_sites_as_declared() -> None:
    text = _authorship_text()
    start = text.index("**Declared:**")
    ends = [text.index(m, start) for m in ("**Not yet declared:**", "Any class-I segment") if m in text[start:]]
    declared = text[start:min(ends)].lower()
    assert "test-run" in declared, "the GREEN test-run stdout tail must be listed under Declared"
    assert "collect-probe" in declared, "the RED collect-probe tail must be listed under Declared"


def test_ac10_authorship_spec_states_the_chunk_rule() -> None:
    text = _authorship_text().lower()
    assert "longest suffix" in text and "survived" in text, "the part-chunk rule is written into section 4"


def test_ac10_authorship_spec_has_no_deferred_paragraph_naming_192() -> None:
    text = _authorship_text()
    if "Deferred class-I segments" in text:
        tail = text[text.index("Deferred class-I segments"):][:800]
        assert "bd#192" not in tail, "the deferral paragraph must not name bd#192 any more"
    assert "**Not yet declared:**" not in text or "bd#192" not in text[text.index("**Not yet declared:**"):][:600]


# ---------------------------------------------------------------------------
# AC11 - registry
# ---------------------------------------------------------------------------

def test_ac11_flag_registered() -> None:
    from bytedigger_engine.flags_catalog import FLAGS
    assert FLAG in FLAGS, f"{FLAG} missing from flags_catalog.FLAGS"
    entry = FLAGS[FLAG]
    assert entry.get("kind") == "gate", entry
    assert entry.get("default") == "1", entry
    assert entry.get("module") == "workflows/phase_5_implement.py", entry
    assert str(entry.get("owner", "")).strip(), entry
    assert str(entry.get("provenance", "")).strip().startswith("introduced:"), entry


def test_ac11_events_doc_has_row() -> None:
    text = (REPO_ROOT / "docs" / "events.md").read_text(encoding="utf-8")
    assert DROP_EVENT in text, "docs/events.md must document %s" % DROP_EVENT
    assert re.search(r"^\|.*" + DROP_EVENT, text, re.M), "the event is documented as a table row"
