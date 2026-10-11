"""GUARD tests for bd#130 -- regression guards that already pass on current main.

Spec: docs/decisions/2026-10-11-bd130-autonomous-cap-policy.md (AC5-AC10).
The RED tests live in test_bd130_autonomous_cap_policy.py. Same harness, copied
(no cross-import): both gate backends driven through subprocess in tmp_path,
nothing imported from the repo, no mocks, TS tests FAIL (never skip) without bun.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

BACKENDS = ["bash", "ts"]
POLICY = "BD_AUTONOMOUS_CAP_POLICY"

P5_BASE = "phase_5_implement: complete\nopus_validation: pass\n"

ROW_A = "plan_review=pass_with_concerns needs mode AUTONOMOUS (got mode: SUPERVISED)"
ROW_B = "plan_review_concerns has no value"
ROW_PAUSES_MISSING = "autonomous_pauses has no value"
ROW_COMPLEX = ("COMPLEX in AUTONOMOUS needs mode_override: auto_flag_complex "
               "(got: <missing>)")
ROW_LEGACY_PWC = "plan_review=pass (got: pass_with_concerns)"


def _env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for k in ("HAL_DIR", "CLAUDE_PLUGIN_ROOT", "BYTEDIGGER_PHRASES_PATH", POLICY):
        env.pop(k, None)
    env.update(extra)
    return env


def _make_workdir(base: Path, name: str, phase: str, extra_state: str = "",
                  complexity: str = "FEATURE", mode: str = "AUTONOMOUS") -> Path:
    wd = base / name
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "bytedigger.json").write_text(
        json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    (wd / "build-metadata.json").write_text(json.dumps({"complexity": complexity}))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (wd / "build-state.yaml").write_text(
        'task: "t"\n'
        f"complexity: {complexity}\n"
        f"mode: {mode}\n"
        f'current_phase: "{phase}"\n'
        f'last_updated: "{now}"\n'
        f"{extra_state}"
    )
    return wd


def _run(backend: str, wd: Path, root: Path = REPO_ROOT,
         policy: str | None = None) -> subprocess.CompletedProcess:
    extra = {"BYTEDIGGER_CONFIG": str(wd / "bytedigger.json")}
    if policy is not None:
        extra[POLICY] = policy
    env = _env(**extra)
    if backend == "bash":
        cmd = ["bash", str(root / "scripts" / "build-gate.sh")]
    else:
        bun = shutil.which("bun")
        if bun is None:
            pytest.fail("bun not found on PATH (TS-gate tests must not skip)")
        cmd = [bun, "run", str(root / "scripts" / "ts" / "build-phase-gate.ts")]
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                          env=env, cwd=str(wd), timeout=120)


def _describe(proc) -> str:
    return (f"rc={proc.returncode} stdout={proc.stdout.decode('utf-8', 'replace')!r} "
            f"stderr={proc.stderr.decode('utf-8', 'replace')!r}")


def _reason(proc) -> str:
    out = proc.stdout.decode("utf-8").strip()
    assert out, f"empty stdout; {_describe(proc)}"
    return json.loads(out.splitlines()[-1])["reason"]


def _assert_pass(proc):
    assert proc.returncode == 0, _describe(proc)
    assert proc.stdout == b"", _describe(proc)


def _assert_block_has(proc, *rows: str):
    assert proc.returncode == 2, _describe(proc)
    reason = _reason(proc)
    for r in rows:
        assert r in reason, f"{r!r} not in {reason!r}"
    return reason


_PWC_OK = P5_BASE + "plan_review: pass_with_concerns\nplan_review_concerns: [x]\n"
FIXTURES = {
    "ac1": ("5", "AUTONOMOUS", "FEATURE",
            _PWC_OK + "autonomous_pauses: none\n", None),
    "ac5_feature_auto": ("5", "AUTONOMOUS", "FEATURE",
                         P5_BASE + "plan_review: pass\n", None),
    "ac5_feature_sup": ("5", "SUPERVISED", "FEATURE",
                        P5_BASE + "plan_review: pass\n", None),
    "ac6_complex_no_override": ("5", "AUTONOMOUS", "COMPLEX",
                                P5_BASE + "plan_review: pass\n", (ROW_COMPLEX,)),
    "ac6_complex_override": ("5", "AUTONOMOUS", "COMPLEX",
                             P5_BASE + "plan_review: pass\n"
                             "mode_override: auto_flag_complex\n"
                             "autonomous_pauses: none\n", None),
    "ac6_complex_sup": ("5", "SUPERVISED", "COMPLEX",
                        P5_BASE + "plan_review: pass\n", None),
    "ac6_complex_auto_at_45": ("4.5", "AUTONOMOUS", "COMPLEX",
                               "plan_review: pass\n", None),
    # pwc fixtures, used only by the AC10 other-policy parity guard
    "ac2": ("5", "SUPERVISED", "FEATURE",
            _PWC_OK + "autonomous_pauses: none\n", (ROW_A,)),
    "ac3_no_concerns": ("5", "AUTONOMOUS", "FEATURE",
                        P5_BASE + "plan_review: pass_with_concerns\n"
                        "autonomous_pauses: none\n", (ROW_B,)),
    "ac3_empty_concerns": ("5", "AUTONOMOUS", "FEATURE",
                           P5_BASE + "plan_review: pass_with_concerns\n"
                           "plan_review_concerns: []\nautonomous_pauses: none\n",
                           (ROW_B,)),
    "ac3_no_pauses": ("5", "AUTONOMOUS", "FEATURE", _PWC_OK,
                      (ROW_PAUSES_MISSING,)),
    "ac3_unknown_pause": ("5", "AUTONOMOUS", "FEATURE",
                          _PWC_OK + "autonomous_pauses: [P1, P99]\n",
                          ("autonomous_pauses: unknown pause id 'P99'",)),
}

GUARD_AC9 = ["ac5_feature_auto", "ac5_feature_sup", "ac6_complex_no_override",
             "ac6_complex_override", "ac6_complex_sup", "ac6_complex_auto_at_45"]
GUARD_AC10 = ["ac5_feature_auto", "ac5_feature_sup", "ac6_complex_override",
              "ac6_complex_sup", "ac6_complex_auto_at_45"]


def _run_fixture(backend: str, tmp_path: Path, tag: str, name: str,
                 mode: str | None = None, policy: str | None = None):
    phase, fx_mode, complexity, extra, _ = FIXTURES[name]
    wd = _make_workdir(tmp_path, f"{tag}-{backend}", phase, extra,
                       complexity=complexity, mode=mode or fx_mode)
    return _run(backend, wd, policy=policy)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", ["ac5_feature_auto", "ac5_feature_sup"])
def test_ac5_plan_review_pass_unchanged(tmp_path, backend, name):
    """AC5 GUARD: plan_review pass on FEATURE or SUPERVISED -> pass, no new rows."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", name))


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_complex_override_is_not_enforced_at_phase_45(tmp_path, backend):
    """AC6 GUARD: the COMPLEX check runs on phase 5 only, 4.5 still passes."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", "ac6_complex_auto_at_45"))


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_complex_with_override_and_pauses_passes(tmp_path, backend):
    """AC6 GUARD: mode_override auto_flag_complex + autonomous_pauses none -> pass."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", "ac6_complex_override"))


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_supervised_complex_pass_unchanged(tmp_path, backend):
    """AC6 GUARD: SUPERVISED + COMPLEX + pass -> pass (no check)."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", "ac6_complex_sup"))


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac7_legacy_rejects_pass_with_concerns(tmp_path, backend):
    """AC7 GUARD: legacy policy -> pass_with_concerns gets the legacy row."""
    proc = _run_fixture(backend, tmp_path, "t", "ac1", policy="legacy")
    reason = _assert_block_has(proc, ROW_LEGACY_PWC)
    assert ROW_A not in reason and ROW_B not in reason


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac7_legacy_complex_autonomous_pass_passes(tmp_path, backend):
    """AC7 GUARD: legacy policy -> AUTONOMOUS COMPLEX pass has no COMPLEX check."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", "ac6_complex_no_override",
                              policy="legacy"))


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", GUARD_AC9)
def test_ac9_supervised_stdout_equals_legacy(tmp_path, backend, name):
    """AC9 GUARD: with mode SUPERVISED the default policy stdout equals legacy."""
    default = _run_fixture(backend, tmp_path, "d", name, mode="SUPERVISED")
    legacy = _run_fixture(backend, tmp_path, "l", name, mode="SUPERVISED",
                          policy="legacy")
    assert default.stdout == legacy.stdout, \
        f"default={default.stdout!r} legacy={legacy.stdout!r}"
    assert default.returncode == legacy.returncode


@pytest.mark.parametrize("name", GUARD_AC10)
def test_ac10_bash_and_ts_stdout_identical_and_correct(tmp_path, name):
    """AC10 GUARD: bash and TS stdout/rc identical and passing for pass-today fixtures."""
    bash = _run_fixture("bash", tmp_path, "t", name)
    ts = _run_fixture("ts", tmp_path, "t", name)
    assert (bash.returncode, bash.stdout) == (ts.returncode, ts.stdout), \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
    _assert_pass(bash)


def _scripts_copy(tmp_path: Path, name: str) -> Path:
    root = (tmp_path / name).resolve()
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__tests__", "node_modules",
                                                  "__pycache__"))
    return root


def _set_table(root: Path, state: str) -> None:
    table = root / "scripts" / "autonomous-pauses.tsv"
    if state == "missing":
        table.unlink(missing_ok=True)
    elif state == "empty":
        table.write_text("", encoding="utf-8")
    else:  # bad_row: wrong field count and bad id
        table.write_text("X\tonly-two\n", encoding="utf-8")


TABLE_STATES = ["missing", "empty", "bad_row"]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("state", TABLE_STATES)
@pytest.mark.parametrize("name", ["ac5_feature_auto", "ac5_feature_sup",
                                  "ac6_complex_sup"])
def test_ac12_plain_pass_never_reads_pause_table(tmp_path, backend, state, name):
    """AC12 GUARD: plan_review pass outside AUTONOMOUS COMPLEX -> pass, table not read."""
    root = _scripts_copy(tmp_path, "plugin")
    _set_table(root, state)
    phase, mode, cx, extra, _ = FIXTURES[name]
    wd = _make_workdir(tmp_path, "w", phase, extra, complexity=cx, mode=mode)
    _assert_pass(_run(backend, wd, root=root))


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("state", TABLE_STATES)
def test_ac12_legacy_never_reads_pause_table(tmp_path, backend, state):
    """AC12 GUARD: under legacy a missing/malformed table never surfaces a table row."""
    root = _scripts_copy(tmp_path, "plugin")
    _set_table(root, state)
    for name in ("ac1", "ac6_complex_no_override"):
        phase, mode, cx, extra, _ = FIXTURES[name]
        wd = _make_workdir(tmp_path, f"w-{name}", phase, extra, complexity=cx,
                           mode=mode)
        proc = _run(backend, wd, root=root, policy="legacy")
        if name == "ac1":
            reason = _assert_block_has(proc, ROW_LEGACY_PWC)
            assert "autonomous pause table" not in reason, reason
        else:
            _assert_pass(proc)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("value", ["approved", "fail", "", "PASS"])
def test_plan_review_other_values_keep_legacy_row(tmp_path, backend, value):
    """GUARD (2.1): any other plan_review value -> exactly the legacy row text."""
    extra = P5_BASE + (f"plan_review: {value}\n" if value else "")
    wd = _make_workdir(tmp_path, "w", "5", extra)
    reason = _assert_block_has(
        _run(backend, wd), f"plan_review=pass (got: {value or '<missing>'})")
    assert ROW_A not in reason and ROW_B not in reason


@pytest.mark.parametrize("policy", ["legacy", "bogus"])
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_ac10_bash_and_ts_identical_under_other_policies(tmp_path, name, policy):
    """AC10 GUARD: parity also holds for legacy and unknown policy values."""
    bash = _run_fixture("bash", tmp_path, "t", name, policy=policy)
    ts = _run_fixture("ts", tmp_path, "t", name, policy=policy)
    assert (bash.returncode, bash.stdout) == (ts.returncode, ts.stdout), \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
