"""bd#299 (Refs #89) RED: baseline-delta driver -- one env name, visible
failures, replay harness.

No sys.path mutation, no conftest import (workflows.md 1q): the driver and the
wiring are exercised through subprocesses, the catalog is read with ast, the
replay script is loaded lazily by file path. Deterministic, no network, no
model calls.

Row schema assumed for scripts/baseline_delta_replay.py summarize(rows): dicts
with keys id, verdict (PASS|FAIL|BLOCKED|ERROR), expect, seeded (bool),
false_block (bool), caught (bool). summarize returns the summary line string.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "baseline_delta_gate.py"
CATALOG_SRC = REPO / "engine_py" / "bytedigger_engine" / "flags_catalog.py"
REPLAY = REPO / "scripts" / "baseline_delta_replay.py"

FAIL_RESULTS = "FAILED x/y.py::t - boom\n=== 1 failed, 2 passed in 0.1s ===\n"
CLEAN_RESULTS = "=== 3 passed in 0.1s ===\n"
NO_SUMMARY_RESULTS = "ImportError: collection failed\n"


def _clean_env(**overrides):
    env = dict(os.environ)
    for k in (
        "HAL_BASELINE_DELTA_ENFORCE",
        "HAL_BASELINE_DELTA_GATE_ENFORCE",
        "HAL_CORPUS_PARITY_ENFORCE",
        "HAL_BASELINE_DELTA_GATE",
        "HAL_CORPUS_ALLOW_REMOVED",
        "HAL_KNOWN_REDS_TODAY",
    ):
        env.pop(k, None)
    env.update(overrides)
    return env


def _run_driver(tmp_path, env):
    results = tmp_path / "results.txt"
    results.write_text(FAIL_RESULTS)
    base = tmp_path / "base.txt"
    base.write_text("")
    return subprocess.run(
        [sys.executable, str(DRIVER), "--results", str(results), "--suite", "pytest",
         "--baseline", str(base)],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120,
    )


# ---------------------------------------------------------------- S1

def test_s1_old_env_name_is_dead(tmp_path):
    proc = _run_driver(tmp_path, _clean_env(HAL_BASELINE_DELTA_GATE_ENFORCE="1"))
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["delta_verdict"] == "FAIL"
    assert proc.returncode == 0, (proc.returncode, proc.stderr[-500:])
    assert out["enforce"] is False


def test_s1_new_env_name_exits_4(tmp_path):
    proc = _run_driver(tmp_path, _clean_env(HAL_BASELINE_DELTA_ENFORCE="1"))
    assert proc.returncode == 4, (proc.returncode, proc.stdout[-500:], proc.stderr[-500:])
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["enforce"] is True


def _catalog_keys():
    tree = ast.parse(CATALOG_SRC.read_text())
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id in ("FLAGS", "CATALOG") \
                and isinstance(value, ast.Dict):
            return {k.value for k in value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("flags catalog dict not found")


def _is_os_environ(node):
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _driver_enforce_env_reads():
    """HAL_*_ENFORCE names read via os.environ.get / os.environ[...] /
    os.getenv, with module-level string constants resolved."""
    tree = ast.parse(DRIVER.read_text())
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            consts[node.targets[0].id] = node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            consts[node.target.id] = node.value.value

    def resolve(expr):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        if isinstance(expr, ast.Name):
            return consts.get(expr.id)
        return None

    reads = set()
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            if isinstance(f, ast.Attribute) and (
                    (f.attr == "get" and _is_os_environ(f.value))
                    or (f.attr == "getenv" and isinstance(f.value, ast.Name)
                        and f.value.id == "os")):
                key = resolve(node.args[0])
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            sl = node.slice
            key = resolve(sl)
        if key and re.fullmatch(r"HAL_[A-Z0-9_]*_ENFORCE", key):
            reads.add(key)
    return reads


def test_s1_driver_enforce_env_names_are_in_catalog():
    reads = _driver_enforce_env_reads()
    assert reads, "driver reads no HAL_*_ENFORCE env name (parse found none)"
    missing = reads - _catalog_keys()
    assert not missing, f"driver reads env names not in flags catalog: {sorted(missing)}"


def test_s1_old_env_name_absent_from_tracked_files():
    old = "HAL_BASELINE_DELTA_GATE_ENFORCE"
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(REPO), capture_output=True, timeout=60,
    )
    assert listed.returncode == 0, listed.stderr[-500:]
    offenders = []
    for rel in listed.stdout.decode().split("\0"):
        if not rel:
            continue
        name = rel.rsplit("/", 1)[-1]
        if rel == "docs/decisions/2026-10-13-bd299-baseline-delta-driver.md":
            continue
        if rel.startswith("tests/") or "/tests/" in rel \
                or name.startswith("test_") or name.endswith("_test.py"):
            continue
        p = REPO / rel
        if not p.is_file():
            continue
        try:
            text = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if old in text:
            offenders.append(rel)
    assert not offenders, f"old env name still present in: {offenders}"


# ---------------------------------------------------------------- S2

_HARNESS = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    from bytedigger_engine.workflows._baseline_delta import run_baseline_delta_gate
    # PRELUDE

    class Cfg:
        def __init__(self, bin_path): self.bin_path = bin_path
        def gate_enabled(self, name): return True
        def flag(self, name): return False
        def path(self, name, default): return Path(self.bin_path)

    events = []
    def emit(name, payload=None, **kw):
        events.append([name, payload])

    ret = run_baseline_delta_gate(sys.argv[2], "pytest", sys.argv[3], "p5", "s", emit,
                                  cfg=Cfg(sys.argv[1]))
    print(json.dumps({"ret": ret, "events": events}))
""")


def _run_wiring(tmp_path, stub_body, git_cwd=None, prelude=""):
    stub = tmp_path / "stub.py"
    stub.write_text(textwrap.dedent(stub_body))
    harness = tmp_path / "harness.py"
    harness.write_text(_HARNESS.replace("# PRELUDE", prelude))
    results = tmp_path / "r.txt"
    results.write_text(CLEAN_RESULTS)
    env = _clean_env(PYTHONPATH=str(REPO / "engine_py"))
    proc = subprocess.run(
        [sys.executable, str(harness), str(stub), str(results),
         str(git_cwd if git_cwd is not None else tmp_path)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    return data["ret"], {n: p for n, p in data["events"]}


def test_s2_traceback_exit1_emits_parse_error_event(tmp_path):
    ret, ev = _run_wiring(tmp_path, """
        import sys
        sys.stderr.write("Traceback (most recent call last):\\n  boom-marker-XYZ\\n")
        sys.exit(1)
    """)
    assert ret["skipped"] == "parse_error"
    assert "baseline_delta_gate_skipped" in ev, ev
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "parse_error"
    assert p["returncode"] == 1
    assert "boom-marker-XYZ" in p["stderr"]
    assert "stdout_tail" in p


def test_s2_garbage_stdout_emits_parse_error_event(tmp_path):
    ret, ev = _run_wiring(tmp_path, """
        print("not json garbage-marker-QQ")
    """)
    assert ret["skipped"] == "parse_error"
    p = ev.get("baseline_delta_gate_skipped")
    assert p is not None, ev
    assert p["reason"] == "parse_error"
    assert p["returncode"] == 0
    assert "garbage-marker-QQ" in p["stdout_tail"]


def test_s2_exit2_driver_error_event_carries_returncode(tmp_path):
    ret, ev = _run_wiring(tmp_path, """
        import sys
        print("some out-marker-77")
        sys.stderr.write("err\\n")
        sys.exit(2)
    """)
    assert ret["skipped"] == "driver_error"
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "driver_error"
    assert p["returncode"] == 2
    assert "out-marker-77" in p["stdout_tail"]


def test_s2_exec_error_event_carries_exception_text_as_detail(tmp_path):
    missing_cwd = tmp_path / "no-such-cwd-marker-ZZ"
    ret, ev = _run_wiring(tmp_path, "print('x')\n", git_cwd=missing_cwd)
    assert ret["skipped"] == "exec_error"
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "exec_error"
    assert "no-such-cwd-marker-ZZ" in p.get("detail", ""), p


_VALID_JSON_STUB = """
    import json, sys
    print(json.dumps({{"verdict": "PASS", "delta_verdict": "PASS", "new_fails": []}}))
    sys.exit({rc})
"""


def test_s2_exit1_with_valid_json_is_still_parse_error(tmp_path):
    ret, ev = _run_wiring(tmp_path, _VALID_JSON_STUB.format(rc=1))
    assert ret["skipped"] == "parse_error"
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "parse_error"
    assert p["returncode"] == 1
    assert "baseline_delta_gate_verdict" not in ev


def test_s2_exit3_with_valid_json_is_parse_error(tmp_path):
    ret, ev = _run_wiring(tmp_path, _VALID_JSON_STUB.format(rc=3))
    assert ret["skipped"] == "parse_error"
    assert ev["baseline_delta_gate_skipped"]["returncode"] == 3


def test_s2_exit_4_and_5_with_garbage_last_line_are_parse_error(tmp_path):
    for rc in (4, 5):
        sub = tmp_path / f"rc{rc}"
        sub.mkdir()
        ret, ev = _run_wiring(sub, f"""
            import sys
            print("not-json-garbage")
            sys.exit({rc})
        """)
        assert ret["skipped"] == "parse_error", (rc, ret)
        p = ev["baseline_delta_gate_skipped"]
        assert p["reason"] == "parse_error" and p["returncode"] == rc, (rc, p)
        assert "baseline_delta_gate_verdict" not in ev


def test_s2_exit0_with_empty_stdout_is_parse_error(tmp_path):
    ret, ev = _run_wiring(tmp_path, "pass\n")
    assert ret["skipped"] == "parse_error"
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "parse_error" and p["returncode"] == 0
    assert p["stdout_tail"] == ""


def test_s2_timeout_expired_emits_exec_error_with_detail(tmp_path):
    prelude = (
        "import subprocess\n"
        "def _boom(*a, **k):\n"
        "    raise subprocess.TimeoutExpired('cmd-marker-TO', 60)\n"
        "subprocess.run = _boom\n"
    )
    ret, ev = _run_wiring(tmp_path, "print('x')\n", prelude=prelude)
    assert ret["skipped"] == "exec_error"
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "exec_error"
    assert "cmd-marker-TO" in p.get("detail", ""), p


def test_s2_exit0_with_garbage_is_parse_error(tmp_path):
    ret, ev = _run_wiring(tmp_path, "print('garbage {not json')\n")
    assert ret["skipped"] == "parse_error"
    assert ev["baseline_delta_gate_skipped"]["reason"] == "parse_error"
    assert "baseline_delta_gate_verdict" not in ev


def test_s2_stderr_and_stdout_tail_truncated_to_last_2000(tmp_path):
    body = """
        import sys
        sys.stderr.write("HEADERR" + "e" * 4000 + "TAILERR")
        sys.stdout.write("HEADOUT" + "o" * 4000 + "TAILOUT\\n")
        sys.exit(1)
    """
    ret, ev = _run_wiring(tmp_path, body)
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "parse_error"
    assert len(p["stderr"]) == 2000 and p["stderr"].endswith("TAILERR")
    assert "HEADERR" not in p["stderr"]
    assert len(p["stdout_tail"]) == 2000 and p["stdout_tail"].rstrip().endswith("TAILOUT")
    assert "HEADOUT" not in p["stdout_tail"]


def test_s2_driver_error_stderr_and_stdout_tail_truncated(tmp_path):
    sub = tmp_path / "rc2"
    sub.mkdir()
    body = """
        import sys
        sys.stderr.write("HEADERR" + "e" * 4000 + "TAILERR")
        sys.stdout.write("HEADOUT" + "o" * 4000 + "TAILOUT\\n")
        sys.exit(2)
    """
    ret, ev = _run_wiring(sub, body)
    p = ev["baseline_delta_gate_skipped"]
    assert p["reason"] == "driver_error"
    assert len(p["stderr"]) == 2000 and p["stderr"].endswith("TAILERR")
    assert len(p["stdout_tail"]) == 2000 and p["stdout_tail"].rstrip().endswith("TAILOUT")
    assert "HEADOUT" not in p["stdout_tail"]


# ---------------------------------------------------------------- S3

def _load_replay():
    spec = importlib.util.spec_from_file_location("baseline_delta_replay_t", REPLAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # FileNotFoundError pre-GREEN
    return mod


def _offline_env(tmp_path):
    """Env whose PATH starts with a fake `git` that fails (and logs) any
    `ls-remote`, delegating every other subcommand to the real git. Replay
    results must not depend on the network or on origin/main state."""
    real_git = shutil.which("git") or "/usr/bin/git"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    log = tmp_path / "ls_remote.log"
    fake = fakebin / "git"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "ls-remote" ]; then\n'
        f'  echo "$@" >> "{log}"\n'
        '  echo "fatal: offline" >&2\n'
        "  exit 128\n"
        "fi\n"
        f'exec "{real_git}" "$@"\n'
    )
    fake.chmod(0o755)
    return _clean_env(PATH=str(fakebin) + os.pathsep + os.environ.get("PATH", "")), log


def _run_replay(tmp_path, cases):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases))
    out_md = tmp_path / "table.md"
    out_json = tmp_path / "out.json"
    env, _log = _offline_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPLAY), "--cases", str(cases_path),
         "--out", str(out_md), "--json", str(out_json)],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300,
    )
    return proc, out_md, out_json


def _capture_driver_argvs(tmp_path, extra_args):
    """Run replay main() in-process with subprocess.run stubbed; return the
    argv lists passed to the driver."""
    mod = _load_replay()
    cases_path = tmp_path / "cases_argv.json"
    cases_path.write_text(json.dumps([
        {"id": "one", "results": _write(tmp_path, "one.txt", CLEAN_RESULTS),
         "base_fails": [], "expect": "PASS"}]))
    seen = []

    class _Done:
        returncode = 0
        stdout = json.dumps({"verdict": "PASS", "delta_verdict": "PASS",
                             "new_fails": [], "blocked_by": []}) + "\n"
        stderr = ""

    def fake_run(argv, *a, **k):
        seen.append(list(argv))
        return _Done()

    real = subprocess.run
    subprocess.run = fake_run
    try:
        mod.main(["--cases", str(cases_path), *extra_args])
    finally:
        subprocess.run = real
    return seen


def _base_ref_of(argv):
    assert "--base-ref" in argv, argv
    return argv[argv.index("--base-ref") + 1]


def test_s3_driver_argv_defaults_to_base_ref_head(tmp_path):
    seen = _capture_driver_argvs(tmp_path, [])
    assert seen, "replay never invoked the driver"
    assert _base_ref_of(seen[0]) == "HEAD"
    assert "--require-corpus-parity" in seen[0]


def test_s3_base_ref_cli_override_reaches_driver(tmp_path):
    seen = _capture_driver_argvs(tmp_path, ["--base-ref", "origin/some-branch"])
    assert _base_ref_of(seen[0]) == "origin/some-branch"


def test_s3_replay_never_calls_git_ls_remote(tmp_path):
    cases = [{"id": "c", "results": _write(tmp_path, "c.txt", CLEAN_RESULTS),
              "base_fails": [], "expect": "PASS"}]
    proc, out_md, out_json = _run_replay(tmp_path, cases)
    assert proc.returncode == 0, proc.stderr[-1500:]
    _env, log = _offline_env(tmp_path)
    assert not log.exists() or log.read_text() == "", log.read_text()
    row = _row(out_json, "c")
    assert "E_REMOTE_UNREACHABLE" not in (row["blocked_by"] or [])
    assert "E_STALE_REMOTE_REF" not in (row["blocked_by"] or [])


def _row(out_json, case_id):
    data = json.loads(Path(out_json).read_text())
    rows = data["rows"] if isinstance(data, dict) else data
    return next(r for r in rows if r["id"] == case_id)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


TABLE_HEADER = "| id | verdict | expect | false_block | caught |"
ROW_KEYS = {"id", "verdict", "delta_verdict", "new_fails", "blocked_by",
            "corpus_scope_reason", "expect", "seeded", "false_block", "caught"}


def _table_rows(md_text):
    """Parse the markdown table into {id: [cells]} by the id column."""
    lines = [ln.strip() for ln in md_text.splitlines() if ln.strip().startswith("|")]
    assert lines and lines[0] == TABLE_HEADER, lines[:1]
    rows = {}
    for ln in lines[2:]:  # skip header + separator
        cells = [c.strip() for c in ln.strip("|").split("|")]
        rows[cells[0]] = cells
    return rows


def _mixed_cases(tmp_path):
    return [
        {"id": "seed-reg", "results": _write(tmp_path, "a.txt", FAIL_RESULTS),
         "base_fails": [], "expect": "FAIL", "seeded": True},
        {"id": "seed-parity", "results": _write(tmp_path, "b.txt", NO_SUMMARY_RESULTS),
         "base_fails": [], "expect": "BLOCKED", "seeded": True},
        # seeded but NOT caught: clean results can never yield FAIL
        {"id": "seed-miss", "results": _write(tmp_path, "d.txt", CLEAN_RESULTS),
         "base_fails": [], "expect": "FAIL", "seeded": True},
        {"id": "clean", "results": _write(tmp_path, "c.txt", CLEAN_RESULTS),
         "base_fails": [], "expect": "PASS"},
    ]


def test_s3_seeded_regression_is_caught(tmp_path):
    proc, out_md, out_json = _run_replay(tmp_path, _mixed_cases(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    reg = _row(out_json, "seed-reg")
    assert reg["delta_verdict"] == "FAIL"
    assert reg["new_fails"] == ["x/y.py::t"]
    assert reg["caught"] is True


def test_s3_seeded_parity_mismatch_is_blocked_and_caught(tmp_path):
    proc, out_md, out_json = _run_replay(tmp_path, _mixed_cases(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    par = _row(out_json, "seed-parity")
    assert par["verdict"] == "BLOCKED"
    assert "E_RESULTS_UNPARSEABLE" in par["blocked_by"]
    assert par["caught"] is True


def test_s3_clean_case_false_block_matches_verdict(tmp_path):
    proc, out_md, out_json = _run_replay(tmp_path, _mixed_cases(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    clean = _row(out_json, "clean")
    # Corpus precondition may block in a sandbox; a non-PASS on an expect-PASS
    # case must be marked a false block, a PASS must not.
    assert clean["false_block"] is (clean["verdict"] != "PASS")
    cells = _table_rows(out_md.read_text())["clean"]
    assert cells[3].lower() == ("no" if clean["verdict"] == "PASS" else "yes")


def test_s3_summary_seeded_caught_is_exact(tmp_path):
    proc, out_md, out_json = _run_replay(tmp_path, _mixed_cases(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert _row(out_json, "seed-miss")["caught"] is False
    summary = [ln for ln in proc.stdout.splitlines() if ln.startswith("PASS=")]
    assert summary, proc.stdout
    assert re.fullmatch(
        r"PASS=\d+ FAIL=\d+ BLOCKED=\d+ ERROR=\d+ false_blocks=\d+ seeded_caught=2/3",
        summary[-1],
    ), summary[-1]


def test_s3_json_shape_rows_and_summary(tmp_path):
    proc, out_md, out_json = _run_replay(tmp_path, _mixed_cases(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    data = json.loads(Path(out_json).read_text())
    assert isinstance(data, dict) and set(data) == {"rows", "summary"}, data.keys()
    assert [r["id"] for r in data["rows"]] == ["seed-reg", "seed-parity", "seed-miss", "clean"]
    for r in data["rows"]:
        assert set(r) == ROW_KEYS, set(r) ^ ROW_KEYS
        assert isinstance(r["new_fails"], list)
        assert isinstance(r["seeded"], bool)
        assert isinstance(r["false_block"], bool) and isinstance(r["caught"], bool)
    assert data["summary"]


def test_s3_table_header_exact_and_rows_by_id(tmp_path):
    proc, out_md, out_json = _run_replay(tmp_path, _mixed_cases(tmp_path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    rows = _table_rows(out_md.read_text())
    assert set(rows) == {"seed-reg", "seed-parity", "seed-miss", "clean"}
    assert rows["seed-reg"][1] == "FAIL" and rows["seed-reg"][2] == "FAIL"
    assert rows["seed-reg"][4].lower() == "yes"
    assert rows["seed-parity"][1] == "BLOCKED"
    assert rows["seed-miss"][4].lower() == "no"


def test_s3_missing_results_file_is_error_row_exit_0(tmp_path):
    cases = [{"id": "gone", "results": str(tmp_path / "does-not-exist.txt"),
              "base_fails": [], "expect": "PASS"}]
    proc, out_md, out_json = _run_replay(tmp_path, cases)
    assert proc.returncode == 0, (proc.returncode, proc.stderr[-1500:])
    row = _row(out_json, "gone")
    assert row["verdict"] == "ERROR"
    assert _table_rows(out_md.read_text())["gone"][1] == "ERROR"
    assert "ERROR=1" in proc.stdout


def test_s3_base_fails_containing_failing_id_yields_pass(tmp_path):
    cases = [{"id": "known-red", "results": _write(tmp_path, "a.txt", FAIL_RESULTS),
              "base_fails": ["x/y.py::t"], "expect": "PASS"}]
    proc, out_md, out_json = _run_replay(tmp_path, cases)
    assert proc.returncode == 0, proc.stderr[-1500:]
    row = _row(out_json, "known-red")
    assert row["delta_verdict"] == "PASS"
    assert row["new_fails"] == []
    assert row["verdict"] == "PASS"
    assert row["false_block"] is False


def test_s3_summarize_arithmetic_and_format():
    mod = _load_replay()
    rows = [
        {"id": "a", "verdict": "PASS", "expect": "PASS", "seeded": False,
         "false_block": False, "caught": False},
        {"id": "b", "verdict": "FAIL", "expect": "PASS", "seeded": False,
         "false_block": True, "caught": False},
        {"id": "c", "verdict": "FAIL", "expect": "FAIL", "seeded": True,
         "false_block": False, "caught": True},
        {"id": "d", "verdict": "BLOCKED", "expect": "FAIL", "seeded": True,
         "false_block": False, "caught": False},
        {"id": "e", "verdict": "ERROR", "expect": None, "seeded": False,
         "false_block": False, "caught": False},
    ]
    assert mod.summarize(rows) == \
        "PASS=1 FAIL=2 BLOCKED=1 ERROR=1 false_blocks=1 seeded_caught=1/2"
    assert mod.summarize([]) == \
        "PASS=0 FAIL=0 BLOCKED=0 ERROR=0 false_blocks=0 seeded_caught=0/0"


def test_s3_unreadable_cases_exits_2(tmp_path):
    assert REPLAY.is_file(), f"missing {REPLAY}"
    missing = tmp_path / "nope.json"
    proc = subprocess.run(
        [sys.executable, str(REPLAY), "--cases", str(missing)],
        cwd=str(REPO), env=_clean_env(), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stderr[-500:])
    assert str(missing) in proc.stderr, proc.stderr[-500:]
    assert "Traceback" not in proc.stderr, proc.stderr[-500:]


def test_s3_empty_cases_list_exits_0(tmp_path):
    assert REPLAY.is_file(), f"missing {REPLAY}"
    cases = tmp_path / "empty.json"
    cases.write_text("[]")
    proc = subprocess.run(
        [sys.executable, str(REPLAY), "--cases", str(cases)],
        cwd=str(REPO), env=_clean_env(), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr[-500:])
    assert "PASS=0 FAIL=0 BLOCKED=0 ERROR=0 false_blocks=0 seeded_caught=0/0" in proc.stdout
