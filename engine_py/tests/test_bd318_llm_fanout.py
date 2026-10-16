"""bd#318 RED: a fan-out of identical model calls cannot be built (structural) and is a finding (insurance).

Spec: docs/decisions/2026-10-16-bd318-llm-fanout-identical.md (frozen, revised after gate r1)
Audit: docs/decisions/2026-10-16-bd318-gate-r1.md

AC -> test map
--------------
AC1  test_ac1_distinct_jobs_run_in_job_order_each_with_own_prompt
AC2  test_ac2_identical_jobs_raise_before_any_model_call
     test_ac2_role_label_alone_never_distinguishes
     test_ac2_composition_collision_prompt_split_differently
     test_ac2_whitespace_and_case_variants_collide[...]
     test_ac2_distinct_text_is_not_collapsed_by_normalization
AC3  test_ac3_same_prompt_different_input_runs_both_and_includes_input
AC4  test_ac4_single_job_runs
     test_ac4_empty_list_blank_role_blank_prompt_raise_value_error[...]  (incl. whitespace-only)
AC5  test_ac5_pooled_submit_is_one_finding_and_main_returns_1
AC6  test_ac6_other_dispatcher_forms_each_one_finding[...]  (all dispatcher forms, real shape,
     lambda, partial, direct run_with_current_run)
AC7  test_ac7_direct_calls_and_non_model_pool_are_clean
AC8  test_ac8_helper_module_itself_is_exempt
     test_ac8_basename_decoy_is_not_exempt[...]
AC9  test_ac9_switch_off_returns_no_findings
     test_ac9_switch_values_other_than_zero_keep_rule_on[...]
     test_ac9_main_switch_off_prints_exactly_the_check_set
     test_ac9_main_switch_on_prints_check_then_fanout
     test_ac9_main_clean_inventory_returns_0
AC10 test_ac10_real_tree_has_no_fanout_findings
AC11 test_ac11_flag_registered_gate_default_on
     test_ac11_flag_owner_lint_passes
AC12 test_ac12_workers_really_run_concurrently
     test_ac12_results_in_job_order_though_first_job_finishes_last
     test_ac12_parent_telemetry_run_visible_in_every_worker

Harness: `invoke_llm_subprocess` is replaced on the helper module (the spec says the helper
resolves it through its own module attribute) by a thread-safe recording fake. The lint
is the real `class_i_lint`, run on tmp trees. `main` takes the tree root as argv[0] and the
inventory comes from the module-level `load_inventory()` (monkeypatched). Concurrency uses a
Barrier / Events with bounded timeouts (failure = test failure, never a hang); no sleeps as
sync. The only singleton resource is the telemetry thread-local slot, cleared before and
after (workflows.md 1i). Modules are imported lazily so a missing module reddens each AC.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FLAG = "HAL_LLM_FANOUT_LINT"
_WAIT = 5.0  # bounded timeout for barrier / event waits


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _fanout():
    return importlib.import_module("bytedigger_engine.lib.llm_fanout")


def _lint():
    return importlib.import_module("bytedigger_engine.conformance.class_i_lint")


def _engine_root() -> Path:
    import bytedigger_engine
    return Path(bytedigger_engine.__file__).parent


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.delenv(_FLAG, raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


class _RecordingFake:
    """Thread-safe stand-in for invoke_llm_subprocess; echoes the prompt it was given.

    `hook(kwargs)` (optional) runs inside the worker thread before the result is returned.
    """

    def __init__(self, hook=None) -> None:
        self._lock = threading.Lock()
        self.calls: list = []
        self.hook = hook

    def __call__(self, **kwargs) -> StepResult:
        with self._lock:
            self.calls.append(dict(kwargs))
        if self.hook is not None:
            self.hook(kwargs)
        return StepResult(status="ok", data={"echo_prompt": kwargs.get("prompt")},
                          duration_ms=0, step_name=kwargs.get("step_name", "fake"))

    def prompts(self) -> list:
        with self._lock:
            return [c.get("prompt") for c in self.calls]


@pytest.fixture
def fake(monkeypatch) -> _RecordingFake:
    mod = _fanout()
    rec = _RecordingFake()
    monkeypatch.setattr(mod, "invoke_llm_subprocess", rec, raising=False)
    return rec


def _install(monkeypatch, rec: _RecordingFake) -> None:
    monkeypatch.setattr(_fanout(), "invoke_llm_subprocess", rec, raising=False)


def _run(jobs):
    mod = _fanout()
    return mod.run_distinct_llm_jobs(
        jobs, model="sonnet", timeout_sec=30, extra_data={"k": "v"}, step_name="fanout_bd318")


# ---------------------------------------------------------------------------
# AC1 - AC4: the helper
# ---------------------------------------------------------------------------

def test_ac1_distinct_jobs_run_in_job_order_each_with_own_prompt(fake) -> None:
    """AC1: 3 jobs differing in prompt -> 3 model calls, results in job order, each got its own prompt."""
    mod = _fanout()
    jobs = [mod.FanoutJob(role=f"r{i}", prompt=f"PROMPT-{i}") for i in range(3)]
    results = _run(jobs)
    assert len(fake.calls) == 3
    assert sorted(fake.prompts()) == ["PROMPT-0", "PROMPT-1", "PROMPT-2"]
    assert [r.data["echo_prompt"] for r in results] == ["PROMPT-0", "PROMPT-1", "PROMPT-2"]
    assert all(c.get("model") == "sonnet" and c.get("timeout_sec") == 30 for c in fake.calls)


def test_ac2_identical_jobs_raise_before_any_model_call(fake) -> None:
    """AC2: 3 jobs with identical (prompt, input_text), roles differ -> IdenticalFanoutError, ZERO calls."""
    mod = _fanout()
    jobs = [mod.FanoutJob(role=f"focus-{i}", prompt="SAME", input_text="SAME-INPUT") for i in range(3)]
    with pytest.raises(mod.IdenticalFanoutError) as ei:
        _run(jobs)
    assert fake.calls == []
    assert isinstance(ei.value, ValueError)
    msg = str(ei.value)
    assert "focus-0" in msg and "focus-1" in msg, msg


def test_ac2_role_label_alone_never_distinguishes(fake) -> None:
    """AC2: assert_distinct itself rejects jobs that differ only in role."""
    mod = _fanout()
    jobs = [mod.FanoutJob(role="a", prompt="P"), mod.FanoutJob(role="b", prompt="P")]
    with pytest.raises(mod.IdenticalFanoutError):
        mod.assert_distinct(jobs)
    assert fake.calls == []


def test_ac2_composition_collision_prompt_split_differently(fake) -> None:
    """AC2: prompt="P\\n\\nIN" vs prompt="P", input_text="IN" compose to the same invoked prompt."""
    mod = _fanout()
    jobs = [mod.FanoutJob(role="a", prompt="P\n\nIN"),
            mod.FanoutJob(role="b", prompt="P", input_text="IN")]
    with pytest.raises(mod.IdenticalFanoutError) as ei:
        mod.assert_distinct(jobs)
    assert "a" in str(ei.value) and "b" in str(ei.value)
    with pytest.raises(mod.IdenticalFanoutError):
        _run(jobs)
    assert fake.calls == []


@pytest.mark.parametrize("a,b", [
    (("P", ""), ("P ", "")),
    (("P", ""), ("P\n", "")),
    (("P", ""), ("  P", "")),
    (("P", "IN"), ("P", "IN \n")),
    (("P", "IN"), ("P  ", "  IN")),
    (("alpha beta", ""), ("alpha   beta", "")),
    (("alpha\tbeta\ngamma", ""), ("alpha beta gamma", "")),
    (("Review The Code", ""), ("review the code", "")),
    (("P", "IN"), ("p", "in")),
], ids=["trailing_space", "trailing_newline", "leading_space", "input_trailing", "both_padded",
        "inner_space_run", "tab_newline_run", "case", "case_with_input"])
def test_ac2_whitespace_and_case_variants_collide(a, b, fake) -> None:
    """AC2: identity key = whitespace-collapsed, casefolded composed prompt; variants collide, zero calls."""
    mod = _fanout()
    jobs = [mod.FanoutJob(role="x", prompt=a[0], input_text=a[1]),
            mod.FanoutJob(role="y", prompt=b[0], input_text=b[1])]
    with pytest.raises(mod.IdenticalFanoutError):
        _run(jobs)
    assert fake.calls == []


def test_ac2_distinct_text_is_not_collapsed_by_normalization(fake) -> None:
    """AC2 guard: normalization must not over-collapse (words glued vs spaced are different prompts)."""
    mod = _fanout()
    jobs = [mod.FanoutJob(role="a", prompt="a b"), mod.FanoutJob(role="b", prompt="ab")]
    mod.assert_distinct(jobs)  # must not raise
    assert len(_run(jobs)) == 2
    assert sorted(fake.prompts()) == ["a b", "ab"]


def test_ac3_same_prompt_different_input_runs_both_and_includes_input(fake) -> None:
    """AC3: same prompt, different input_text -> allowed, both run, invoked prompt = prompt + blank line + input."""
    mod = _fanout()
    jobs = [mod.FanoutJob(role="a", prompt="P", input_text="IN-A"),
            mod.FanoutJob(role="b", prompt="P", input_text="IN-B")]
    results = _run(jobs)
    assert len(fake.calls) == 2
    assert [r.data["echo_prompt"] for r in results] == ["P\n\nIN-A", "P\n\nIN-B"]


def test_ac4_single_job_runs(fake) -> None:
    """AC4: one job is allowed (no fan-out); empty input_text -> invoked prompt is exactly the prompt."""
    mod = _fanout()
    results = _run([mod.FanoutJob(role="solo", prompt="ONLY")])
    assert fake.prompts() == ["ONLY"]
    assert len(results) == 1 and results[0].data["echo_prompt"] == "ONLY"


@pytest.mark.parametrize("case", ["empty_list", "blank_role", "blank_prompt", "whitespace_prompt",
                                  "whitespace_prompt_ws_input"])
def test_ac4_empty_list_blank_role_blank_prompt_raise_value_error(case, fake) -> None:
    """AC4: empty list / blank role / blank or whitespace-only prompt -> ValueError (not Identical), zero calls."""
    mod = _fanout()
    jobs = {
        "empty_list": [],
        "blank_role": [mod.FanoutJob(role="  ", prompt="P"), mod.FanoutJob(role="b", prompt="Q")],
        "blank_prompt": [mod.FanoutJob(role="a", prompt=""), mod.FanoutJob(role="b", prompt="Q")],
        "whitespace_prompt": [mod.FanoutJob(role="a", prompt="   "), mod.FanoutJob(role="b", prompt="Q")],
        "whitespace_prompt_ws_input": [mod.FanoutJob(role="a", prompt=" \t", input_text="\n "),
                                       mod.FanoutJob(role="b", prompt="Q")],
    }[case]
    with pytest.raises(ValueError) as ei:
        _run(jobs)
    assert not isinstance(ei.value, mod.IdenticalFanoutError)
    assert fake.calls == []


# ---------------------------------------------------------------------------
# AC5 - AC9: the lint rule
# ---------------------------------------------------------------------------

def _tree(tmp_path: Path, source: str, helper_source: str | None = None) -> Path:
    root = tmp_path / "bytedigger_engine"
    (root / "workflows").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "workflows" / "x.py").write_text(source, encoding="utf-8")
    if helper_source is not None:
        (root / "lib" / "llm_fanout.py").write_text(helper_source, encoding="utf-8")
    return root


_SRC_SUBMIT = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(prompts):\n"
    "    with ThreadPoolExecutor(max_workers=3) as executor:\n"
    "        futs = [executor.submit(invoke_llm_subprocess, prompt=p) for p in prompts]\n"
    "    return [x.result() for x in futs]\n"
)
_SRC_MAP = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(prompts):\n"
    "    with ThreadPoolExecutor(max_workers=3) as pool:\n"
    "        return list(pool.map(invoke_llm_subprocess, prompts))\n"
)
_SRC_THREAD = (
    "import threading\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f():\n"
    "    t = threading.Thread(target=invoke_llm_subprocess)\n"
    "    t.start()\n"
)
_SRC_THREAD_FROM = (
    "from threading import Thread\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f():\n"
    "    t = Thread(target=invoke_llm_subprocess)\n"
    "    t.start()\n"
)
_SRC_ALIAS = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess as call\n\n"
    "def f(p):\n"
    "    with ThreadPoolExecutor(max_workers=2) as executor:\n"
    "        return executor.submit(call, prompt=p).result()\n"
)
_SRC_ATTR = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine import llm_subprocess\n\n"
    "def f(p):\n"
    "    with ThreadPoolExecutor(max_workers=2) as executor:\n"
    "        return executor.submit(llm_subprocess.invoke_llm_subprocess, prompt=p).result()\n"
)
_SRC_RUN_IN_EXECUTOR = (
    "import asyncio\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "async def f(p):\n"
    "    loop = asyncio.get_running_loop()\n"
    "    return await loop.run_in_executor(None, invoke_llm_subprocess, p)\n"
)
_SRC_STARMAP = (
    "from multiprocessing.pool import ThreadPool\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(argsets):\n"
    "    with ThreadPool(3) as pool:\n"
    "        return pool.starmap(invoke_llm_subprocess, argsets)\n"
)
_SRC_APPLY_ASYNC = (
    "from multiprocessing.pool import ThreadPool\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(p):\n"
    "    with ThreadPool(3) as pool:\n"
    "        return pool.apply_async(invoke_llm_subprocess, (p,)).get()\n"
)
_SRC_TO_THREAD = (
    "import asyncio\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "async def f(p):\n"
    "    return await asyncio.to_thread(invoke_llm_subprocess, prompt=p)\n"
)
_SRC_LAMBDA = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(p):\n"
    "    with ThreadPoolExecutor(max_workers=2) as executor:\n"
    "        return executor.submit(lambda: invoke_llm_subprocess(prompt=p)).result()\n"
)
_SRC_PARTIAL = (
    "import functools\n"
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(p):\n"
    "    with ThreadPoolExecutor(max_workers=2) as executor:\n"
    "        return executor.submit(functools.partial(invoke_llm_subprocess, prompt=p)).result()\n"
)
# The REAL #317 shape: model callable at positional index 2, behind a wrapper callable.
_SRC_REAL_SHAPE = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine import telemetry_ctx\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(prompts):\n"
    "    parent_ctx = telemetry_ctx.get_current_run()\n"
    "    with ThreadPoolExecutor(max_workers=3) as executor:\n"
    "        futures = [\n"
    "            executor.submit(\n"
    "                telemetry_ctx.run_with_current_run,\n"
    "                parent_ctx,\n"
    "                invoke_llm_subprocess,\n"
    "                prompt=p,\n"
    "                model='m',\n"
    "            )\n"
    "            for p in prompts\n"
    "        ]\n"
    "    return [x.result() for x in futures]\n"
)
# A direct, non-pooled run_with_current_run(ctx, invoke_llm_subprocess, ...) is itself a dispatcher form.
_SRC_DIRECT_RWCR = (
    "from bytedigger_engine.telemetry_ctx import run_with_current_run\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def f(ctx, p):\n"
    "    return run_with_current_run(ctx, invoke_llm_subprocess, prompt=p)\n"
)
_SRC_CLEAN = (
    "from concurrent.futures import ThreadPoolExecutor\n"
    "from bytedigger_engine.llm_subprocess import invoke_llm_subprocess\n\n"
    "def single(p):\n"
    "    return invoke_llm_subprocess(prompt=p)\n\n"
    "def looped(ps):\n"
    "    out = []\n"
    "    for p in ps:\n"
    "        out.append(invoke_llm_subprocess(prompt=p))\n"
    "    return out\n\n"
    "def retried(p):\n"
    "    r = invoke_llm_subprocess(prompt=p)\n"
    "    if r.status != 'ok':\n"
    "        r = invoke_llm_subprocess(prompt=p)\n"
    "    return r\n\n"
    "def pooled_other(xs):\n"
    "    with ThreadPoolExecutor(max_workers=2) as pool:\n"
    "        return [pool.submit(len, x).result() for x in xs] + list(pool.map(str, xs))\n"
)


def _stale_inventory() -> dict:
    """An inventory whose only entry matches no call site: check() reports it as stale."""
    return {"version": 1, "sites": {"workflows/gone.py::nowhere::invoke_llm_subprocess#0": {
        "class": "I-deferred", "issue": "#1", "note": "stale on purpose"}}}


def _patch_inventory(lint, monkeypatch, inventory: dict) -> None:
    monkeypatch.setattr(lint, "load_inventory", lambda: inventory)


def _run_main(lint, root: Path, monkeypatch, inventory: dict | None = None) -> int:
    """`main` on a fixture tree: root is argv[0]; inventory from module-level load_inventory()."""
    _patch_inventory(lint, monkeypatch, inventory if inventory is not None else {"version": 1, "sites": {}})
    return lint.main([str(root)])


def _line_of(source: str, needle: str) -> int:
    for i, ln in enumerate(source.splitlines(), start=1):
        if needle in ln:
            return i
    raise AssertionError(needle)


def test_ac5_pooled_submit_is_one_finding_and_main_returns_1(tmp_path, monkeypatch, capsys) -> None:
    """AC5: executor.submit(invoke_llm_subprocess, ...) -> exactly one finding at workflows/x.py:<line>; main -> 1."""
    lint = _lint()
    root = _tree(tmp_path, _SRC_SUBMIT)
    found = lint.fanout_findings(root)
    assert len(found) == 1, found
    line = _line_of(_SRC_SUBMIT, "executor.submit")
    assert found[0].startswith(f"workflows/x.py:{line}:"), found
    assert "pooled model call bypasses lib/llm_fanout" in found[0]
    assert "invoke_llm_subprocess via submit" in found[0], found
    assert _run_main(lint, root, monkeypatch) == 1
    assert capsys.readouterr().out.splitlines() == found


@pytest.mark.parametrize("source,needle,dispatcher", [
    (_SRC_MAP, "pool.map", "map"),
    (_SRC_THREAD, "threading.Thread", "Thread"),
    (_SRC_THREAD_FROM, "Thread(target", "Thread"),
    (_SRC_ALIAS, "executor.submit", "submit"),
    (_SRC_ATTR, "executor.submit", "submit"),
    (_SRC_RUN_IN_EXECUTOR, "loop.run_in_executor", "run_in_executor"),
    (_SRC_STARMAP, "pool.starmap", "starmap"),
    (_SRC_APPLY_ASYNC, "pool.apply_async", "apply_async"),
    (_SRC_TO_THREAD, "asyncio.to_thread", "to_thread"),
    (_SRC_LAMBDA, "executor.submit", "submit"),
    (_SRC_PARTIAL, "executor.submit", "submit"),
    (_SRC_REAL_SHAPE, "executor.submit(", "submit"),
    (_SRC_DIRECT_RWCR, "run_with_current_run(ctx", "run_with_current_run"),
], ids=["pool_map", "thread_target", "thread_from_import", "import_alias", "attribute_form",
        "run_in_executor", "starmap", "apply_async", "to_thread", "lambda", "partial",
        "real_shape_index2_behind_run_with_current_run", "direct_run_with_current_run"])
def test_ac6_other_dispatcher_forms_each_one_finding(source, needle, dispatcher, tmp_path) -> None:
    """AC6: every dispatcher form, at any positional index / keyword / inside lambda or partial,
    gives exactly one finding anchored at the dispatching call line."""
    lint = _lint()
    found = lint.fanout_findings(_tree(tmp_path, source))
    assert len(found) == 1, found
    assert found[0].startswith(f"workflows/x.py:{_line_of(source, needle)}:"), found
    assert f"invoke_llm_subprocess via {dispatcher}" in found[0], found


def test_ac7_direct_calls_and_non_model_pool_are_clean(tmp_path, monkeypatch) -> None:
    """AC7: single/loop/retry direct calls and a pooled non-model function -> 0 findings, main -> 0."""
    lint = _lint()
    root = _tree(tmp_path, _SRC_CLEAN)
    scanned_files = list((root / "workflows").glob("*.py"))
    assert len(scanned_files) > 0, "denominator: the scanned tree holds the file with the direct calls"
    assert lint.fanout_findings(root) == []
    assert _run_main(lint, root, monkeypatch) == 0


def test_ac8_helper_module_itself_is_exempt(tmp_path) -> None:
    """AC8: lib/llm_fanout.py with a pooled invoke_llm_subprocess yields 0 findings (the same text
    in workflows/x.py is a finding, so the exemption is by file, not by blindness)."""
    lint = _lint()
    root = _tree(tmp_path, "x = 1\n", helper_source=_SRC_SUBMIT)
    assert lint.fanout_findings(root) == []
    assert len(lint.fanout_findings(_tree(tmp_path / "other", _SRC_SUBMIT))) == 1


@pytest.mark.parametrize("rel", ["workflows/llm_fanout.py", "lib/sub/llm_fanout.py"],
                         ids=["workflows_basename_decoy", "lib_subdir_basename_decoy"])
def test_ac8_basename_decoy_is_not_exempt(rel, tmp_path) -> None:
    """AC8: the exemption is the exact relpath lib/llm_fanout.py; a same-named file elsewhere is a finding."""
    lint = _lint()
    root = _tree(tmp_path, "x = 1\n")
    decoy = root / rel
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text(_SRC_SUBMIT, encoding="utf-8")
    found = lint.fanout_findings(root)
    assert len(found) == 1, found
    assert found[0].startswith(f"{rel}:{_line_of(_SRC_SUBMIT, 'executor.submit')}:"), found


def test_ac9_switch_off_returns_no_findings(tmp_path, monkeypatch) -> None:
    """AC9: HAL_LLM_FANOUT_LINT=0 -> AC5's tree gives []; control: on, it is found."""
    lint = _lint()
    root = _tree(tmp_path, _SRC_SUBMIT)
    monkeypatch.setenv(_FLAG, "1")
    assert len(lint.fanout_findings(root)) == 1, "control: switch on still finds it"
    monkeypatch.setenv(_FLAG, "0")
    assert lint.fanout_findings(root) == []


@pytest.mark.parametrize("value", [None, "1", "yes", "", "false", " 0"],
                         ids=["unset", "one", "yes", "empty", "false", "padded_zero"])
def test_ac9_switch_values_other_than_zero_keep_rule_on(value, tmp_path, monkeypatch) -> None:
    """AC9: only the exact string `0` turns the rule off; unset / 1 / yes / anything else keeps it on."""
    lint = _lint()
    root = _tree(tmp_path, _SRC_SUBMIT)
    if value is None:
        monkeypatch.delenv(_FLAG, raising=False)
    else:
        monkeypatch.setenv(_FLAG, value)
    assert len(lint.fanout_findings(root)) == 1


def test_ac9_main_switch_off_prints_exactly_the_check_set(tmp_path, monkeypatch, capsys) -> None:
    """AC9: switch off, tree has a pooled call AND a non-empty check() problem set: main prints exactly
    the check() lines (stdout, one per line), returns 1."""
    lint = _lint()
    root = _tree(tmp_path, _SRC_SUBMIT)
    inv = _stale_inventory()
    expected = lint.check(root, inv)
    assert expected, "precondition: check() problem set is non-empty"
    monkeypatch.setenv(_FLAG, "0")
    assert _run_main(lint, root, monkeypatch, inv) == 1
    assert capsys.readouterr().out.splitlines() == expected


def test_ac9_main_switch_on_prints_check_then_fanout(tmp_path, monkeypatch, capsys) -> None:
    """AC9/Change B: switch on: check() problems first, then fanout_findings; returns 1."""
    lint = _lint()
    root = _tree(tmp_path, _SRC_SUBMIT)
    inv = _stale_inventory()
    expected = lint.check(root, inv) + lint.fanout_findings(root)
    assert len(expected) >= 2
    assert _run_main(lint, root, monkeypatch, inv) == 1
    assert capsys.readouterr().out.splitlines() == expected


def test_ac9_main_clean_inventory_returns_0(tmp_path, monkeypatch, capsys) -> None:
    """AC9: switch off on a clean-inventory tree -> returns 0, prints nothing."""
    lint = _lint()
    root = _tree(tmp_path, _SRC_SUBMIT)
    monkeypatch.setenv(_FLAG, "0")
    assert _run_main(lint, root, monkeypatch) == 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# AC10: real tree
# ---------------------------------------------------------------------------

def test_ac10_real_tree_has_no_fanout_findings() -> None:
    """AC10: the real engine tree has no pooled model call bypassing lib/llm_fanout (lot #317 removed the last one)."""
    engine_root = _engine_root()
    scanned_files = list(engine_root.rglob("*.py"))
    assert len(scanned_files) > 10, "denominator: the real engine tree is non-trivially populated"
    assert (engine_root / "workflows" / "phase_6_review.py").is_file()
    assert _lint().fanout_findings(engine_root) == []


# ---------------------------------------------------------------------------
# AC11: flag registry
# ---------------------------------------------------------------------------

def test_ac11_flag_registered_gate_default_on() -> None:
    """AC11: HAL_LLM_FANOUT_LINT in flags_catalog.FLAGS, kind gate, default on, module class_i_lint."""
    catalog = importlib.import_module("bytedigger_engine.flags_catalog")
    entry = catalog.FLAGS.get(_FLAG)
    assert entry is not None, f"{_FLAG} is not registered in flags_catalog.FLAGS"
    assert entry["kind"] == "gate" and entry["kind"] in catalog.KINDS
    assert entry["default"] == "1"
    assert entry["module"] == "conformance/class_i_lint.py"
    assert entry.get("description"), entry


def test_ac11_flag_owner_lint_passes() -> None:
    """AC11: scripts/flag_owner_lint.py exits 0 with the flag registered and read in class_i_lint."""
    catalog = importlib.import_module("bytedigger_engine.flags_catalog")
    assert _FLAG in catalog.FLAGS, "precondition: flag registered (else this guard is vacuous)"
    proc = subprocess.run(
        [sys.executable, "scripts/flag_owner_lint.py"], cwd=str(_REPO_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
        env={k: v for k, v in os.environ.items() if k not in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE")},
    )
    assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# AC12: helper behaviour (concurrency, order, telemetry)
# ---------------------------------------------------------------------------

def test_ac12_workers_really_run_concurrently(monkeypatch) -> None:
    """AC12: every call blocks on a 3-party Barrier (bounded timeout). A serial implementation can never
    release it: the barrier breaks, the call raises, the run fails. Distinct thread ids are recorded."""
    mod = _fanout()
    barrier = threading.Barrier(3, timeout=_WAIT)
    tids: list = []
    tid_lock = threading.Lock()

    def hook(_kw) -> None:
        with tid_lock:
            tids.append(threading.get_ident())
        barrier.wait()  # BrokenBarrierError after _WAIT if the calls are not concurrent

    rec = _RecordingFake(hook=hook)
    _install(monkeypatch, rec)
    jobs = [mod.FanoutJob(role=f"r{i}", prompt=f"P{i}") for i in range(3)]
    results = _run(jobs)
    assert not barrier.broken
    assert len(results) == 3 and len(rec.calls) == 3
    assert len(set(tids)) == 3, tids


def test_ac12_results_in_job_order_though_first_job_finishes_last(monkeypatch) -> None:
    """AC12: job 0 is held until the other two jobs have completed, so completion order is [1,2 in
    some order, 0]; the returned list is still in job order."""
    mod = _fanout()
    others_done = threading.Event()
    done_lock = threading.Lock()
    completed: list = []
    state = {"others": 0}

    def hook(kw) -> None:
        p = kw["prompt"]
        if p == "PROMPT-0":
            assert others_done.wait(_WAIT), "job 0 never saw the others finish (not concurrent?)"
        with done_lock:
            completed.append(p)
            if p != "PROMPT-0":
                state["others"] += 1
                if state["others"] == 2:
                    others_done.set()

    rec = _RecordingFake(hook=hook)
    _install(monkeypatch, rec)
    jobs = [mod.FanoutJob(role=f"r{i}", prompt=f"PROMPT-{i}") for i in range(3)]
    results = _run(jobs)
    assert completed[-1] == "PROMPT-0", completed
    assert [r.data["echo_prompt"] for r in results] == ["PROMPT-0", "PROMPT-1", "PROMPT-2"]


def test_ac12_parent_telemetry_run_visible_in_every_worker(monkeypatch) -> None:
    """AC12: the parent's telemetry_ctx current run is carried into every worker thread."""
    mod = _fanout()
    seen: list = []
    seen_lock = threading.Lock()
    main_tid = threading.get_ident()

    def hook(_kw) -> None:
        cur = telemetry_ctx.get_current_run()
        with seen_lock:
            seen.append((threading.get_ident(), None if cur is None else cur.run_id))

    rec = _RecordingFake(hook=hook)
    _install(monkeypatch, rec)
    telemetry_ctx.set_current_run(event_log=object(), run_id="RUN-BD318", step_name="parent", phase="p")
    jobs = [mod.FanoutJob(role=f"r{i}", prompt=f"P{i}") for i in range(3)]
    _run(jobs)
    assert len(seen) == 3, seen
    assert all(tid != main_tid for tid, _ in seen), "workers must be pool threads"
    assert [rid for _, rid in seen] == ["RUN-BD318"] * 3, seen
