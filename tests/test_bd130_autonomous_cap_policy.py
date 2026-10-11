"""RED tests for bd#130 -- AUTONOMOUS rules for machine caps and SUPERVISED pauses.

Spec: docs/decisions/2026-10-11-bd130-autonomous-cap-policy.md (section 4, AC1-AC12).

Both gate backends (scripts/build-gate.sh and `bun run scripts/ts/build-phase-gate.ts`)
are driven through subprocess in tmp_path fixtures. Nothing is imported from the
repo, no sys.path manipulation, no mocks. If `bun` is not on PATH the TS tests
FAIL (they never skip). Nothing here races or depends on timing.

Every test here is expected RED today. The GUARD tests (pass on current main)
live in test_bd130_autonomous_cap_policy_guards.py.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAUSES_TSV = REPO_ROOT / "scripts" / "autonomous-pauses.tsv"
BUILD_MD = REPO_ROOT / "commands" / "build.md"

BACKENDS = ["bash", "ts"]
POLICY = "BD_AUTONOMOUS_CAP_POLICY"

P5_BASE = "phase_5_implement: complete\nopus_validation: pass\n"

ROW_A = "plan_review=pass_with_concerns needs mode AUTONOMOUS (got mode: SUPERVISED)"
ROW_B = "plan_review_concerns has no value"
ROW_C = "plan_review=pass_with_concerns needs the mandatory opus_validation rows in the deliverable table"
ROW_PAUSES_MISSING = "autonomous_pauses has no value"
ROW_COMPLEX = ("COMPLEX in AUTONOMOUS needs mode_override: auto_flag_complex "
               "(got: <missing>)")
ROW_LEGACY_PWC = "plan_review=pass (got: pass_with_concerns)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


def _scripts_copy(tmp_path: Path, name: str = "plugin") -> Path:
    root = (tmp_path / name).resolve()
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__tests__", "node_modules",
                                                  "__pycache__"))
    return root


def _drop_table_row(root: Path, phase: str, kind: str, field: str) -> None:
    table = root / "scripts" / "phase-deliverables.tsv"
    lines = table.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [l for l in lines
            if l.rstrip("\n").split("\t")[:3] != [phase, kind, field]]
    assert len(kept) == len(lines) - 1, f"row {phase} {kind} {field} not found"
    table.write_text("".join(kept), encoding="utf-8")


# fixtures: name -> (phase, mode, complexity, extra_state, expected)
# expected: None = gate passes (rc 0, empty stdout); tuple = soft block with
# every listed row present in the reason.
_PWC_OK = P5_BASE + "plan_review: pass_with_concerns\nplan_review_concerns: [x]\n"
FIXTURES = {
    "ac1": ("5", "AUTONOMOUS", "FEATURE",
            _PWC_OK + "autonomous_pauses: none\n", None),
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
}


# Fixtures whose AC9 / AC10 checks already pass on main live in
# test_bd130_autonomous_cap_policy_guards.py.
GUARD_AC9 = {"ac5_feature_auto", "ac5_feature_sup", "ac6_complex_no_override",
             "ac6_complex_override", "ac6_complex_sup", "ac6_complex_auto_at_45"}
GUARD_AC10 = {"ac5_feature_auto", "ac5_feature_sup", "ac6_complex_override",
              "ac6_complex_sup", "ac6_complex_auto_at_45"}


def _run_fixture(backend: str, tmp_path: Path, tag: str, name: str,
                 mode: str | None = None, policy: str | None = None):
    phase, fx_mode, complexity, extra, _ = FIXTURES[name]
    wd = _make_workdir(tmp_path, f"{tag}-{backend}", phase, extra,
                       complexity=complexity, mode=mode or fx_mode)
    return _run(backend, wd, policy=policy)


def _assert_expected(proc, expected):
    if expected is None:
        _assert_pass(proc)
    else:
        _assert_block_has(proc, *expected)


# ---------------------------------------------------------------------------
# AC1 -- AUTONOMOUS pass_with_concerns passes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_ac1_autonomous_pass_with_concerns_passes(tmp_path, backend):
    """AC1: phase 5, AUTONOMOUS, pass_with_concerns, concerns [x], pauses none -> pass."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", "ac1"))


def test_ac1_same_stdout_in_both_backends(tmp_path):
    """AC1: both backends yield the same (empty) stdout and rc."""
    bash = _run_fixture("bash", tmp_path, "t", "ac1")
    ts = _run_fixture("ts", tmp_path, "t", "ac1")
    _assert_pass(bash)
    assert (bash.returncode, bash.stdout) == (ts.returncode, ts.stdout), \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"


# ---------------------------------------------------------------------------
# AC2 / AC3 / AC4
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_ac2_supervised_pass_with_concerns_soft_blocks(tmp_path, backend):
    """AC2: SUPERVISED never accepts pass_with_concerns -> row (a)."""
    _assert_block_has(_run_fixture(backend, tmp_path, "t", "ac2"), ROW_A)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", ["ac3_no_concerns", "ac3_empty_concerns",
                                  "ac3_no_pauses", "ac3_unknown_pause"])
def test_ac3_each_failed_condition_adds_its_own_row(tmp_path, backend, name):
    """AC3: missing/[] concerns -> (b); missing pauses / unknown id -> pause rows."""
    proc = _run_fixture(backend, tmp_path, "t", name)
    _assert_block_has(proc, *FIXTURES[name][4])


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac3_only_first_unknown_pause_id_reported(tmp_path, backend):
    """AC3: unknown-id row names the first unknown id only."""
    wd = _make_workdir(tmp_path, "w", "5",
                       _PWC_OK + "autonomous_pauses: [P1, P98, P99]\n")
    reason = _assert_block_has(_run(backend, wd),
                               "autonomous_pauses: unknown pause id 'P98'")
    assert "'P99'" not in reason


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("dropped_phase", ["5.2", "5"])
def test_ac4_missing_mandatory_opus_row_adds_row_c(tmp_path, backend, dropped_phase):
    """AC4: a table copy lacking an opus_validation pass row -> (c) row."""
    root = _scripts_copy(tmp_path)
    _drop_table_row(root, dropped_phase, "field_eq", "opus_validation")
    wd = _make_workdir(tmp_path, "w", "5", _PWC_OK + "autonomous_pauses: none\n")
    _assert_block_has(_run(backend, wd, root=root), ROW_C)


# ---------------------------------------------------------------------------
# AC5 / AC6 / AC7 / AC8
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_complex_autonomous_needs_override_at_phase_5(tmp_path, backend):
    """AC6: AUTONOMOUS + COMPLEX + pass, no mode_override -> section 2.2 row at phase 5."""
    _assert_block_has(_run_fixture(backend, tmp_path, "t", "ac6_complex_no_override"),
                      ROW_COMPLEX)


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_complex_override_without_pauses_blocks(tmp_path, backend):
    """AC6: override present but no pause log -> section 2.3 row (2.2 requires it)."""
    wd = _make_workdir(tmp_path, "w", "5",
                       P5_BASE + "plan_review: pass\nmode_override: auto_flag_complex\n",
                       complexity="COMPLEX")
    _assert_block_has(_run(backend, wd), ROW_PAUSES_MISSING)


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac8_empty_policy_means_autonomous(tmp_path, backend):
    """AC8: empty policy value is the default (autonomous): AC1 fixture passes."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", "ac1", policy=""))


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac8_unknown_policy_value_row(tmp_path, backend):
    """AC8: unknown policy value -> the unknown-value row and no other new rule."""
    proc = _run_fixture(backend, tmp_path, "t", "ac2", policy="bogus")
    reason = _assert_block_has(
        proc, "BD_AUTONOMOUS_CAP_POLICY: unknown value 'bogus' (use autonomous|legacy)")
    assert ROW_A not in reason



# ---------------------------------------------------------------------------
# AC9 / AC10
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", sorted(n for n in FIXTURES if n not in GUARD_AC9))
def test_ac9_supervised_stdout_equals_legacy(tmp_path, backend, name):
    """AC9: with mode SUPERVISED the default policy equals legacy (pwc fixtures only here).

    Fixtures whose plan_review is pass_with_concerns are exempt from byte
    equality: spec AC2 requires the extra (a) row there, so they only need the
    same verdict (rc 2) and must carry the legacy row too.
    """
    default = _run_fixture(backend, tmp_path, "d", name, mode="SUPERVISED")
    legacy = _run_fixture(backend, tmp_path, "l", name, mode="SUPERVISED",
                          policy="legacy")
    if "pass_with_concerns" in FIXTURES[name][3]:
        assert default.returncode == legacy.returncode == 2, \
            f"default: {_describe(default)} legacy: {_describe(legacy)}"
        _assert_block_has(default, ROW_A)
        assert ROW_LEGACY_PWC in _reason(legacy)
    else:
        assert default.stdout == legacy.stdout, \
            f"default={default.stdout!r} legacy={legacy.stdout!r}"
        assert default.returncode == legacy.returncode


@pytest.mark.parametrize("name", sorted(n for n in FIXTURES if n not in GUARD_AC10))
def test_ac10_bash_and_ts_stdout_identical_and_correct(tmp_path, name):
    """AC10: bash and TS stdout/rc are byte-identical AND match the spec verdict."""
    bash = _run_fixture("bash", tmp_path, "t", name)
    ts = _run_fixture("ts", tmp_path, "t", name)
    assert (bash.returncode, bash.stdout) == (ts.returncode, ts.stdout), \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
    _assert_expected(bash, FIXTURES[name][4])



# ---------------------------------------------------------------------------
# AC11 -- pause table file == table in commands/build.md
# ---------------------------------------------------------------------------

def _read_pauses_tsv() -> dict[str, list[str]]:
    assert PAUSES_TSV.exists(), f"{PAUSES_TSV} does not exist yet"
    rows: dict[str, list[str]] = {}
    for line in PAUSES_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")]
        if re.fullmatch(r"P\d+", cells[0]):
            assert len(cells) == 5, f"bad row (need 5 TAB fields): {line!r}"
            rows[cells[0]] = cells
    return rows


def _read_build_md_pauses() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in BUILD_MD.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*P\d+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"bad build.md pause row: {line!r}"
        rows[cells[0]] = cells
    return rows


def _rule(text: str) -> str:
    m = re.match(r"\s*(proceed|stop)\b", text.replace("`", ""), re.I)
    assert m, f"autonomous cell does not start with proceed/stop: {text!r}"
    return m.group(1).lower()


def test_ac11_pauses_tsv_has_p0_to_p10_with_valid_rules():
    """AC11: every id P0..P10 present; the autonomous column is proceed|stop."""
    rows = _read_pauses_tsv()
    assert sorted(rows, key=lambda i: int(i[1:])) == [f"P{n}" for n in range(11)]
    for pid, cells in rows.items():
        assert cells[3] in {"proceed", "stop"}, (pid, cells)


def test_ac11_build_md_table_matches_pauses_tsv():
    """AC11: ids, phase, rule and state field in commands/build.md equal the tsv."""
    tsv = _read_pauses_tsv()
    md = _read_build_md_pauses()
    assert set(md) == set(tsv), f"md={sorted(md)} tsv={sorted(tsv)}"
    for pid, cells in tsv.items():
        m = md[pid]
        assert m[1] == cells[1], (pid, "phase", m[1], cells[1])
        assert _rule(m[3]) == cells[3], (pid, "rule", m[3], cells[3])
        assert m[4].replace("`", "") == cells[4].replace("`", ""), (pid, "field")


# ---------------------------------------------------------------------------
# AC12 -- missing pause table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_ac12_missing_pause_table_row(tmp_path, backend):
    """AC12: the gate reads the pause table only for id validation; missing -> row."""
    root = _scripts_copy(tmp_path)
    table = root / "scripts" / "autonomous-pauses.tsv"
    if table.exists():
        table.unlink()
    wd = _make_workdir(tmp_path, "w", "5", _PWC_OK + "autonomous_pauses: none\n")
    _assert_block_has(_run(backend, wd, root=root),
                      f"autonomous pause table unreadable: {table}")
