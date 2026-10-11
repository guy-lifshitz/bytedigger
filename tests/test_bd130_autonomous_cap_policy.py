"""RED tests for bd#130 -- AUTONOMOUS rules for machine caps and SUPERVISED pauses.

Spec: docs/decisions/2026-10-11-bd130-autonomous-cap-policy.md (Rev 2, section 4, AC1-AC17).

Both gate backends (scripts/build-gate.sh and `bun run scripts/ts/build-phase-gate.ts`)
are driven through subprocess in tmp_path fixtures. Nothing is imported from the
repo, no sys.path manipulation, no mocks. If `bun` is not on PATH the TS tests
FAIL (they never skip). Nothing here races or depends on timing.

Every test here is expected RED today. The GUARD tests (pass on current main,
including AC9 which after Rev 2 only covers `plan_review: pass` states) live in
test_bd130_autonomous_cap_policy_guards.py.

Scenario registry (SC) drives AC4/AC6x/AC12-AC16 and the AC10 byte-compare:
each scenario is a state fixture plus optional scripts-copy edit, policy value
and the expected rows (None = gate passes with empty stdout). Interpolated
values (<v>, <path>, <id>) vary across scenarios so a hard-coded row string
cannot satisfy them.
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


def _row_a(mode: str) -> str:
    return f"plan_review=pass_with_concerns needs mode AUTONOMOUS (got mode: {mode})"


def _row_complex(v: str) -> str:
    return ("COMPLEX in AUTONOMOUS needs mode_override: auto_flag_complex "
            f"(got: {v})")


def _row_unknown_policy(v: str) -> str:
    return f"{POLICY}: unknown value '{v}' (use autonomous|legacy)"


def _row_pause_malformed(v: str) -> str:
    return f"autonomous_pauses: malformed value '{v}'"


def _row_pause_unknown(i: str) -> str:
    return f"autonomous_pauses: unknown pause id '{i}'"


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
                  complexity: str = "FEATURE",
                  mode: str | None = "AUTONOMOUS") -> Path:
    """mode=None omits the `mode:` line (missing mode)."""
    wd = base / name
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "bytedigger.json").write_text(
        json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    (wd / "build-metadata.json").write_text(json.dumps({"complexity": complexity}))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mode_line = f"mode: {mode}\n" if mode is not None else ""
    (wd / "build-state.yaml").write_text(
        'task: "t"\n'
        f"complexity: {complexity}\n"
        f"{mode_line}"
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


def _rewrite_opus_row(root: Path, phase: str, how: str) -> None:
    """how: 'fail' (value -> fail) or 'comment' (row commented out)."""
    table = root / "scripts" / "phase-deliverables.tsv"
    out, hit = [], 0
    for l in table.read_text(encoding="utf-8").splitlines(keepends=True):
        cells = l.rstrip("\n").split("\t")
        if cells[:3] == [phase, "field_eq", "opus_validation"]:
            hit += 1
            l = ("\t".join(cells[:3] + ["fail"]) + "\n") if how == "fail" else "# " + l
        out.append(l)
    assert hit == 1, f"row {phase} field_eq opus_validation not found"
    table.write_text("".join(out), encoding="utf-8")


def _table_path(root: Path) -> Path:
    return root / "scripts" / "autonomous-pauses.tsv"


# -- scripts-copy edits (callables root -> None) ----------------------------

def _drop(phase):
    return lambda root: _drop_table_row(root, phase, "field_eq", "opus_validation")


def _opus(phase, how):
    return lambda root: _rewrite_opus_row(root, phase, how)


def _rm_pauses(root: Path) -> None:
    _table_path(root).unlink(missing_ok=True)


def _write_pauses(content: str):
    return lambda root: _table_path(root).write_text(content, encoding="utf-8")


def _edits(*fns):
    def run(root):
        for f in fns:
            f(root)
    return run


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
                          (_row_pause_unknown("P99"),)),
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

# Fixtures whose AC10 check already passes on main live in the guards file.
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
# scenario registry (AC4, AC6x, AC12-AC16)
# ---------------------------------------------------------------------------

SC: dict[str, dict] = {}


def _sc(name, phase, extra, *, mode="AUTONOMOUS", cx="FEATURE", edit=None,
        policy=None, rows=None, absent=()):
    assert name not in SC, name
    SC[name] = dict(phase=phase, extra=extra, mode=mode, cx=cx, edit=edit,
                    policy=policy, rows=rows, absent=tuple(absent))


def _names(prefix: str) -> list[str]:
    return [n for n in SC if n.startswith(prefix)]


def _run_sc(backend: str, tmp_path: Path, name: str):
    sc = SC[name]
    root = REPO_ROOT
    if sc["edit"] is not None:
        # one shared edited copy per scenario so both backends see the same path
        root = (tmp_path / f"plugin-{name}").resolve()
        if not root.exists():
            _scripts_copy(tmp_path, f"plugin-{name}")
            sc["edit"](root)
    wd = _make_workdir(tmp_path, f"w-{name}-{backend}", sc["phase"], sc["extra"],
                       complexity=sc["cx"], mode=sc["mode"])
    return _run(backend, wd, root=root, policy=sc["policy"]), root


def _check_sc(proc, name: str, root: Path):
    sc = SC[name]
    if sc["rows"] is None:
        _assert_pass(proc)
        return
    table = str(_table_path(root))
    rows = [r.replace("<TABLE>", table) for r in sc["rows"]]
    reason = _assert_block_has(proc, *rows)
    pos = [reason.index(r) for r in rows]
    assert pos == sorted(pos), f"rows out of order: {rows} in {reason!r}"
    for r in rows:
        assert reason.count(r) == 1, f"{r!r} not exactly once in {reason!r}"
    for a in sc["absent"]:
        assert a.replace("<TABLE>", table) not in reason, f"{a!r} in {reason!r}"


OK_PAUSES = _PWC_OK + "autonomous_pauses: none\n"
_NO_PAUSE_ROWS = ("autonomous_pauses has no value", "malformed value",
                  "unknown pause id")

# AC4 / AC16(c): mandatory opus rows in the (copied) deliverable table
_C_ABSENT = (ROW_A, ROW_B, ROW_PAUSES_MISSING, ROW_LEGACY_PWC)
_sc("ac4_drop_52", "5", OK_PAUSES, edit=_drop("5.2"), rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac4_drop_5", "5", OK_PAUSES, edit=_drop("5"), rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac16_c_52_fail", "5", OK_PAUSES, edit=_opus("5.2", "fail"),
    rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac16_c_5_fail", "5", OK_PAUSES, edit=_opus("5", "fail"),
    rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac16_c_52_commented", "5", OK_PAUSES, edit=_opus("5.2", "comment"),
    rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac16_c_5_commented", "5", OK_PAUSES, edit=_opus("5", "comment"),
    rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac16_c_both_dropped", "5", OK_PAUSES, edit=_edits(_drop("5.2"), _drop("5")),
    rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac16_c_both_commented", "5", OK_PAUSES,
    edit=_edits(_opus("5.2", "comment"), _opus("5", "comment")),
    rows=(ROW_C,), absent=_C_ABSENT)
_sc("ac16_c_both_mixed", "5", OK_PAUSES,
    edit=_edits(_drop("5.2"), _opus("5", "comment")),
    rows=(ROW_C,), absent=_C_ABSENT)

# AC16(b): "no value" after removing whitespace and quotes; valid values pass
for _tag, _raw in [("space", "[ ]"), ("quotes", '[""]'), ("nested", "[[]]"),
                   ("single_quotes", "[ '' ]")]:
    _sc(f"ac16_b_{_tag}", "5",
        P5_BASE + "plan_review: pass_with_concerns\n"
        f"plan_review_concerns: {_raw}\nautonomous_pauses: none\n",
        rows=(ROW_B,), absent=(ROW_A, ROW_PAUSES_MISSING, ROW_C))
for _tag, _raw in [("ok_quoted", '["risk: foo"]'), ("ok_two", "[a, b]")]:
    _sc(f"ac16_b_{_tag}", "5",
        P5_BASE + "plan_review: pass_with_concerns\n"
        f"plan_review_concerns: {_raw}\nautonomous_pauses: none\n")

# AC16 order a, b, d (c intact) and missing mode (phase 4.5 too: identical rule)
_sc("ac16_o_abd", "5", P5_BASE + "plan_review: pass_with_concerns\n",
    mode="SUPERVISED", rows=(ROW_A, ROW_B, ROW_PAUSES_MISSING),
    absent=(ROW_LEGACY_PWC, ROW_C))
_sc("ac16_o_abd_at_45", "4.5", "plan_review: pass_with_concerns\n",
    mode="SUPERVISED", rows=(ROW_A, ROW_B, ROW_PAUSES_MISSING),
    absent=(ROW_LEGACY_PWC, ROW_C))
_sc("ac16_o_mode_missing", "5", OK_PAUSES, mode=None,
    rows=(_row_a("<missing>"),), absent=(ROW_LEGACY_PWC, ROW_B, ROW_C))
_sc("ac16_o_mode_other", "5", OK_PAUSES, mode="Autonomous",
    rows=(_row_a("Autonomous"),), absent=(ROW_LEGACY_PWC, ROW_B, ROW_C))

# AC13 (M2): AUTONOMOUS + COMPLEX + pass_with_concerns
_sc("ac13_no_override", "5", OK_PAUSES, cx="COMPLEX",
    rows=(ROW_COMPLEX,), absent=(ROW_LEGACY_PWC, ROW_A, ROW_B, ROW_PAUSES_MISSING))
_sc("ac13_other_override", "5", OK_PAUSES + "mode_override: manual\n", cx="COMPLEX",
    rows=(_row_complex("manual"),), absent=(ROW_LEGACY_PWC, ROW_A))
_sc("ac13_override", "5", OK_PAUSES + "mode_override: auto_flag_complex\n",
    cx="COMPLEX")
_sc("ac13_at_45_no_complex_row", "4.5",
    "plan_review: pass_with_concerns\nplan_review_concerns: [x]\n"
    "autonomous_pauses: none\n", cx="COMPLEX")
_sc("ac13_at_45_pauses_missing_no_complex_row", "4.5",
    "plan_review: pass_with_concerns\nplan_review_concerns: [x]\n", cx="COMPLEX",
    rows=(ROW_PAUSES_MISSING,), absent=("COMPLEX in AUTONOMOUS",))
_sc("ac13_row_order_b_d_complex", "5",
    P5_BASE + "plan_review: pass_with_concerns\n", cx="COMPLEX",
    rows=(ROW_B, ROW_PAUSES_MISSING, _row_complex("<missing>")),
    absent=(ROW_A, ROW_LEGACY_PWC))

# AC6 extension: the <v> in the COMPLEX row is the real override value
_sc("ac6x_pass_other_override", "5",
    P5_BASE + "plan_review: pass\nmode_override: unattended\n"
    "autonomous_pauses: none\n", cx="COMPLEX",
    rows=(_row_complex("unattended"),))

# AC14 (M4): unknown policy values -> unknown row first, then legacy verdict
_UNKNOWN_POLICIES = [("bogus", "bogus"), ("upper", "AUTO"), ("lead_sp", " legacy"),
                     ("cap_l", "Legacy"), ("trail_sp", "legacy ")]
for _tag, _val in _UNKNOWN_POLICIES:
    _u = _row_unknown_policy(_val)
    _sc(f"ac14_{_tag}_pass", "5", P5_BASE + "plan_review: pass\n", policy=_val,
        rows=(_u,), absent=("plan_review=pass (got", "COMPLEX in AUTONOMOUS"))
    _sc(f"ac14_{_tag}_pwc", "5", OK_PAUSES, policy=_val,
        rows=(_u, ROW_LEGACY_PWC),
        absent=(ROW_A, ROW_B, ROW_C, ROW_PAUSES_MISSING))
    _sc(f"ac14_{_tag}_complex_pass", "5", P5_BASE + "plan_review: pass\n",
        cx="COMPLEX", policy=_val, rows=(_u,),
        absent=("COMPLEX in AUTONOMOUS", ROW_PAUSES_MISSING))
    # the pause table is never read under an unknown policy
    _sc(f"ac14_{_tag}_table_not_read", "5", OK_PAUSES, policy=_val,
        edit=_rm_pauses, rows=(_u, ROW_LEGACY_PWC),
        absent=("autonomous pause table",))

# AC12 (M3): pause table failure modes
_GOOD_TBL = ("# header\n\nP1\t3\tclarify\tproceed\tphase3_assumptions\n\n"
             "P7\t5\tcap\tstop\tblocked_reason\n")
_UNREADABLE = "autonomous pause table unreadable: <TABLE>"
_MALFORMED = "autonomous pause table malformed: <TABLE>"
_sc("ac12_missing", "5", OK_PAUSES, edit=_rm_pauses, rows=(_UNREADABLE,),
    absent=("autonomous pause table malformed",))
_sc("ac12_empty", "5", OK_PAUSES, edit=_write_pauses(""), rows=(_MALFORMED,),
    absent=("autonomous pause table unreadable",))
_sc("ac12_comments_only", "5", OK_PAUSES,
    edit=_write_pauses("# only a comment\n\n# another\n"), rows=(_MALFORMED,),
    absent=("autonomous pause table unreadable",))
_sc("ac12_bad_field_count", "5", OK_PAUSES,
    edit=_write_pauses(_GOOD_TBL + "P2\t4\tarch\tproceed\n"), rows=(_MALFORMED,),
    absent=("autonomous pause table unreadable",))
_sc("ac12_bad_id", "5", OK_PAUSES,
    edit=_write_pauses(_GOOD_TBL + "X9\t4\tarch\tproceed\tarchitecture_choice\n"),
    rows=(_MALFORMED,), absent=("autonomous pause table unreadable",))
_sc("ac12_bad_id_lowercase", "5", OK_PAUSES,
    edit=_write_pauses(_GOOD_TBL + "p2\t4\tarch\tproceed\tarchitecture_choice\n"),
    rows=(_MALFORMED,), absent=("autonomous pause table unreadable",))
_sc("ac12_complex_missing", "5",
    P5_BASE + "plan_review: pass\nmode_override: auto_flag_complex\n"
    "autonomous_pauses: none\n", cx="COMPLEX", edit=_rm_pauses,
    rows=(_UNREADABLE,))
_sc("ac12_complex_malformed", "5",
    P5_BASE + "plan_review: pass\nmode_override: auto_flag_complex\n"
    "autonomous_pauses: none\n", cx="COMPLEX", edit=_write_pauses(""),
    rows=(_MALFORMED,))
# the id set really comes from the file: a custom table accepts only its own ids
_sc("ac12_custom_ok", "5", _PWC_OK + "autonomous_pauses: [P1, P7]\n",
    edit=_write_pauses(_GOOD_TBL))
_sc("ac12_custom_unknown", "5", _PWC_OK + "autonomous_pauses: [P7, P3]\n",
    edit=_write_pauses(_GOOD_TBL), rows=(_row_pause_unknown("P3"),))
_sc("ac12_custom_unknown_repo_id", "5", _PWC_OK + "autonomous_pauses: [P1, P4, P7]\n",
    edit=_write_pauses(_GOOD_TBL), rows=(_row_pause_unknown("P4"),))

# AC15 (2.3): table-driven autonomous_pauses parsing
_PAUSE_CASES = [
    ("none", "ok", None), ("[P1, P3]", "ok", None), ("[P1,P1]", "ok", None),
    ("[ P2 , P10 ]", "ok", None), ("[P0]", "ok", None),
    ("[ ]", "novalue", None), ("[]", "novalue", None), ("", "novalue", None),
    ("none # x", "malformed", None), ("[none]", "malformed", None),
    ("none, P1", "malformed", None), ("[P1,]", "malformed", None),
    ("None", "malformed", None), ("[P1 P2]", "malformed", None),
    ("P1", "malformed", None),
    ("[P01]", "unknown", "P01"), ("[P11]", "unknown", "P11"),
    ("[P1, P11, P12]", "unknown", "P11"), ("[P3, P01]", "unknown", "P01"),
]
for _i, (_raw, _kind, _id) in enumerate(_PAUSE_CASES):
    _extra = _PWC_OK + (f"autonomous_pauses: {_raw}".rstrip() + "\n")
    if _kind == "ok":
        _sc(f"ac15_{_i:02d}", "5", _extra)
    elif _kind == "novalue":
        _sc(f"ac15_{_i:02d}", "5", _extra, rows=(ROW_PAUSES_MISSING,),
            absent=("malformed value", "unknown pause id"))
    elif _kind == "malformed":
        _sc(f"ac15_{_i:02d}", "5", _extra, rows=(_row_pause_malformed(_raw),),
            absent=(ROW_PAUSES_MISSING, "unknown pause id"))
    else:
        _sc(f"ac15_{_i:02d}", "5", _extra, rows=(_row_pause_unknown(_id),),
            absent=(ROW_PAUSES_MISSING, "malformed value"))


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
    """AC2: SUPERVISED never accepts pass_with_concerns -> row (a) replaces the legacy row."""
    reason = _assert_block_has(_run_fixture(backend, tmp_path, "t", "ac2"), ROW_A)
    assert ROW_LEGACY_PWC not in reason, reason


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
    reason = _assert_block_has(_run(backend, wd), _row_pause_unknown("P98"))
    assert "'P99'" not in reason


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac4_"))
def test_ac4_missing_mandatory_opus_row_adds_row_c(tmp_path, backend, name):
    """AC4: a table copy lacking an opus_validation pass row -> exactly one (c) row."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


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
@pytest.mark.parametrize("name", _names("ac6x_"))
def test_ac6_complex_row_reports_actual_override_value(tmp_path, backend, name):
    """AC6: a wrong mode_override value is echoed in the 2.2 row (got: <v>)."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac8_empty_policy_means_autonomous(tmp_path, backend):
    """AC8: empty policy value is the default (autonomous): AC1 fixture passes."""
    _assert_pass(_run_fixture(backend, tmp_path, "t", "ac1", policy=""))


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac8_unknown_policy_value_row(tmp_path, backend):
    """AC8: unknown policy value -> the unknown-value row and no other new rule."""
    proc = _run_fixture(backend, tmp_path, "t", "ac2", policy="bogus")
    reason = _assert_block_has(proc, _row_unknown_policy("bogus"))
    assert ROW_A not in reason


# ---------------------------------------------------------------------------
# AC10 -- bash vs TS byte-compare
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(n for n in FIXTURES if n not in GUARD_AC10))
def test_ac10_bash_and_ts_stdout_identical_and_correct(tmp_path, name):
    """AC10: bash and TS stdout/rc are byte-identical AND match the spec verdict."""
    bash = _run_fixture("bash", tmp_path, "t", name)
    ts = _run_fixture("ts", tmp_path, "t", name)
    assert (bash.returncode, bash.stdout) == (ts.returncode, ts.stdout), \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
    _assert_expected(bash, FIXTURES[name][4])


@pytest.mark.parametrize("name", sorted(SC))
def test_ac10_scenarios_bash_and_ts_identical_and_correct(tmp_path, name):
    """AC10: AC4/AC12/AC13-AC16 scenarios: bash == TS byte for byte AND spec verdict."""
    bash, root = _run_sc("bash", tmp_path, name)
    ts, _ = _run_sc("ts", tmp_path, name)
    assert (bash.returncode, bash.stdout) == (ts.returncode, ts.stdout), \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
    _check_sc(bash, name, root)


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
# AC12 -- pause table failure modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac12_"))
def test_ac12_pause_table_failure_modes(tmp_path, backend, name):
    """AC12: missing -> unreadable row; empty / bad field count / bad id -> malformed row;
    a custom table defines the accepted ids."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac12_malformed_table_row_names_the_table_path(tmp_path, backend):
    """AC12: the <path> in the row is the table the gate actually read (a copy here)."""
    name = "ac12_empty"
    proc, root = _run_sc(backend, tmp_path, name)
    assert str(_table_path(root)) != str(PAUSES_TSV)
    _assert_block_has(proc, f"autonomous pause table malformed: {_table_path(root)}")


# ---------------------------------------------------------------------------
# AC13 / AC14 / AC15 / AC16
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac13_"))
def test_ac13_complex_autonomous_pass_with_concerns(tmp_path, backend, name):
    """AC13: AUTONOMOUS+COMPLEX+pass_with_concerns: 2.2 row at phase 5 only, override passes."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac14_"))
def test_ac14_unknown_policy_row_first_then_legacy_verdict(tmp_path, backend, name):
    """AC14: unknown policy (incl. ' legacy', 'Legacy') -> unknown row first, then legacy."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac15_"))
def test_ac15_autonomous_pauses_parsing(tmp_path, backend, name):
    """AC15: table-driven autonomous_pauses shapes -> ok / no value / malformed / unknown id."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac15_wrong_case_id_is_a_pause_row(tmp_path, backend):
    """AC15: `[p1]` is not an accepted form (regex is case-sensitive); it must yield a
    pause row (malformed value or unknown id), never pass and never the legacy row."""
    wd = _make_workdir(tmp_path, "w", "5", _PWC_OK + "autonomous_pauses: [p1]\n")
    reason = _assert_block_has(_run(backend, wd))
    assert (_row_pause_malformed("[p1]") in reason
            or _row_pause_unknown("p1") in reason), reason
    assert ROW_LEGACY_PWC not in reason


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac16_b_"))
def test_ac16_concerns_no_value_shapes(tmp_path, backend, name):
    """AC16(b): concerns `[ ]`, `[""]`, `[[]]` -> row (b); real values pass."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac16_c_"))
def test_ac16_opus_rows_invalid_give_exactly_one_row_c(tmp_path, backend, name):
    """AC16(c): opus row value fail / commented out / both dropped -> exactly one (c) row."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", _names("ac16_o_"))
def test_ac16_row_order_and_missing_mode(tmp_path, backend, name):
    """AC16: a+b+d rows in that order; missing mode -> `(got mode: <missing>)`."""
    proc, root = _run_sc(backend, tmp_path, name)
    _check_sc(proc, name, root)


# ---------------------------------------------------------------------------
# AC17 -- docs mention the new rule set
# ---------------------------------------------------------------------------

DOCS_AC17 = ["commands/build.md", "phases/phase-45-spec.md",
             "phases/phase-5-implement.md", "phases/phase-0-classify.md",
             "skills/bytedigger/SKILL.md", "CHANGELOG.md"]


@pytest.mark.parametrize("doc", DOCS_AC17)
def test_ac17_doc_mentions_autonomous_pauses_or_pass_with_concerns(doc):
    """AC17: each listed doc contains `autonomous-pauses` or `pass_with_concerns`."""
    path = REPO_ROOT / doc
    assert path.is_file(), f"{path} missing"
    text = path.read_text(encoding="utf-8")
    assert "autonomous-pauses" in text or "pass_with_concerns" in text, \
        f"{doc} mentions neither autonomous-pauses nor pass_with_concerns"
