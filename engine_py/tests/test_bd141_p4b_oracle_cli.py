"""RED tests for bd#141 item 4 (b) -- `oracle verify` host CLI and the reader class fix.

Spec: docs/decisions/2026-10-02-bd141-p4b-oracle-cli.md (AC1-AC12, r2; gate r1
findings MAJOR 1-2 and MINOR 3, 5, 7 folded in).

Every `bytedigger_engine.*` import is inside a test body or helper, so the file
collects cleanly and each AC fails at assert time. Freezes are written by the
production writer `run._oracle_after_execute` (no hand-built `oracle_frozen`
line, except the malformed cases of AC9). Nothing mocked; the CLI is run as a
real subprocess.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

ENGINE_PY = Path(__file__).resolve().parents[1]
ORACLE_PY = ENGINE_PY / "bytedigger_engine" / "conformance" / "oracle.py"
RUN_ID = "run-A"
KEYS = {"outcome", "code", "token", "message", "event_type",
        "frozen_digest", "current_digest", "run_id"}


class Fx:
    def __init__(self, root: Path):
        self.root = root
        self.D = root / "scratch"
        self.specs = self.D / "specs"
        self.specs.mkdir(parents=True)
        (self.specs / "a.md").write_text("alpha\n", encoding="utf-8")
        (self.specs / "b.md").write_text("beta\n", encoding="utf-8")
        (root / "logs").mkdir()
        self.P = root / "logs" / "events.jsonl"


def _fx(tmp_path: Path) -> Fx:
    return Fx(tmp_path)


def _ctx(D: Path, **extra: Any):
    return types.SimpleNamespace(org_config={"scratchpad_dir": str(D), **extra})


def _freeze(fx: Fx, run_id: str = RUN_ID, **extra: Any) -> None:
    from bytedigger_engine import run  # noqa: PLC0415

    args = types.SimpleNamespace(workflow="phase_45_spec", event_log=str(fx.P))
    result = types.SimpleNamespace(status="ok")
    ret = run._oracle_after_execute(args, _ctx(fx.D, **extra), run_id, result)
    assert ret is None, f"real freeze did not succeed: {ret!r}"


def _events(P: Path) -> "list[dict]":
    return [json.loads(x) for x in P.read_text(encoding="utf-8").splitlines() if x.strip()]


def _cli(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.oracle", *args],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=60,
    )


def _verify_args(P, D, run_id=RUN_ID) -> "list[str]":
    return ["verify", "--event-log", str(P), "--scratchpad-dir", str(D),
            "--run-id", run_id]


def _report(proc: "subprocess.CompletedProcess[str]") -> "dict[str, Any]":
    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr!r}"
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1, f"expected exactly one stdout line, got {proc.stdout!r}"
    return json.loads(lines[0])


def _run(fx: Fx, run_id: str = RUN_ID) -> "dict[str, Any]":
    return _report(_cli(*_verify_args(fx.P, fx.D, run_id)))


def _tree_hash(D: Path) -> "dict[str, str]":
    return {str(p.relative_to(D)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(D.rglob("*")) if p.is_file()}


# ---- scenario mutators (shared by AC2/AC3/AC4/AC7) -------------------------

def _s_verified(fx):
    _freeze(fx)
    return RUN_ID


def _s_content(fx):
    _freeze(fx)
    (fx.specs / "a.md").write_text("changed\n", encoding="utf-8")
    return RUN_ID


def _s_added(fx):
    _freeze(fx)
    (fx.specs / ".new.md").write_text("n\n", encoding="utf-8")
    return RUN_ID


def _s_removed(fx):
    _freeze(fx)
    (fx.specs / "b.md").unlink()
    return RUN_ID


def _s_removed_dir(fx):
    import shutil  # noqa: PLC0415

    _freeze(fx)
    shutil.rmtree(fx.D)
    return RUN_ID


def _s_foreign(fx):
    fx.P.write_text(json.dumps({"event_type": "other", "run_id": RUN_ID,
                                "payload": {}}) + "\n", encoding="utf-8")
    return RUN_ID


def _s_other_run(fx):
    _freeze(fx, "A")
    return "B"


def _s_dir_log(fx):
    fx.P = fx.root / "logs" / "adir"
    fx.P.mkdir()
    return RUN_ID


def _s_nonjson(fx):
    _freeze(fx)
    with fx.P.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    return RUN_ID


def _s_utf8(fx):
    _freeze(fx)
    with fx.P.open("ab") as fh:
        fh.write(b'{"a": "x\xffy"}\n')
    return RUN_ID


def _s_nonobj(fx):
    _freeze(fx)
    with fx.P.open("a", encoding="utf-8") as fh:
        fh.write("[1,2]\n")
    return RUN_ID


def test_ac1_real_freeze_verified(tmp_path):
    fx = _fx(tmp_path)
    _freeze(fx)
    out = _run(fx)
    assert out["outcome"] == "verified"
    assert out["code"] is None and out["token"] is None
    assert out["frozen_digest"] == out["current_digest"]
    frozen = [e for e in _events(fx.P) if e["event_type"] == "oracle_frozen"]
    assert len(frozen) == 1
    assert out["frozen_digest"] == frozen[0]["payload"]["digest"]
    assert out["event_type"] == "oracle_frozen"
    assert out["run_id"] == RUN_ID


@pytest.mark.parametrize("scn,token", [
    ("content", "mutated:content"), ("added", "mutated:added"),
    ("removed", "mutated:removed"), ("removed_dir", "mutated:removed")])
def test_ac2_mutated_one_per_token(tmp_path, scn, token):
    fx = _fx(tmp_path)
    {"content": _s_content, "added": _s_added, "removed": _s_removed,
     "removed_dir": _s_removed_dir}[scn](fx)
    out = _run(fx)
    assert out["outcome"] == "mutated"
    assert out["code"] == "E_ORACLE_MUTATED"
    assert out["token"] == token
    if scn == "content":
        assert out["current_digest"] is not None
        assert out["current_digest"] != out["frozen_digest"]
    if scn == "added":
        assert out["current_digest"] == out["frozen_digest"]
    if scn in ("removed", "removed_dir"):
        assert out["current_digest"] is None


@pytest.mark.parametrize("scn", ["foreign", "missing_log", "other_run"])
def test_ac3_unfrozen(tmp_path, scn):
    fx = _fx(tmp_path)
    run_id = RUN_ID
    if scn == "foreign":
        run_id = _s_foreign(fx)
    elif scn == "missing_log":
        fx.P = fx.root / "nologs" / "events.jsonl"
    else:
        run_id = _s_other_run(fx)
    out = _report(_cli(*_verify_args(fx.P, fx.D, run_id)))
    assert out["outcome"] == "unfrozen"
    assert out["code"] == "E_ORACLE_UNFROZEN"
    assert out["frozen_digest"] is None
    assert out["current_digest"] is None
    assert out["event_type"] is None
    if scn == "missing_log":
        assert not fx.P.parent.exists(), "CLI must never create a directory"


@pytest.mark.parametrize("scn", ["dir_log", "nonjson", "utf8", "nonobj", "unreadable"])
def test_ac4_indeterminate(tmp_path, scn):
    if scn == "unreadable" and os.geteuid() == 0:
        pytest.skip("chmod 000 is not enforced for root")
    fx = _fx(tmp_path)
    member = fx.specs / "a.md"
    try:
        if scn == "unreadable":
            _freeze(fx)
            member.chmod(0)
            run_id = RUN_ID
        else:
            run_id = {"dir_log": _s_dir_log, "nonjson": _s_nonjson,
                      "utf8": _s_utf8, "nonobj": _s_nonobj}[scn](fx)
        out = _report(_cli(*_verify_args(fx.P, fx.D, run_id)))
    finally:
        if scn == "unreadable":
            member.chmod(0o644)
    assert out["outcome"] == "indeterminate"
    assert out["code"] == "E_ORACLE_INDETERMINATE"
    assert out["token"] is None
    assert isinstance(out["message"], str) and out["message"]


@pytest.mark.parametrize("scn", ["utf8", "nonobj"])
def test_ac5_reader_refuses_in_process(tmp_path, scn):
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    fx = _fx(tmp_path)
    {"utf8": _s_utf8, "nonobj": _s_nonobj}[scn](fx)
    with pytest.raises(oracle.OracleRefusal) as ei:
        oracle.read_log_events(fx.P)
    assert ei.value.code == "E_ORACLE_INDETERMINATE"


def test_ac6_amendment(tmp_path):
    fx = _fx(tmp_path)
    _freeze(fx)
    first = _events(fx.P)[0]["payload"]["digest"]
    (fx.specs / "a.md").write_text("amended\n", encoding="utf-8")
    _freeze(fx, oracle_amendment_reason="r")
    amended = [e for e in _events(fx.P) if e["event_type"] == "oracle_amended"]
    assert len(amended) == 1
    out = _run(fx)
    assert out["event_type"] == "oracle_amended"
    assert out["outcome"] == "verified"
    assert out["frozen_digest"] == amended[0]["payload"]["digest"]
    assert out["frozen_digest"] != first


@pytest.mark.parametrize("scn", [
    "verified", "content", "added", "removed", "removed_dir", "foreign",
    "other_run", "dir_log", "nonjson", "utf8", "nonobj"])
def test_ac7_engine_parity(tmp_path, scn):
    from bytedigger_engine import run  # noqa: PLC0415

    fx = _fx(tmp_path)
    mk = {"verified": _s_verified, "content": _s_content, "added": _s_added,
          "removed": _s_removed, "removed_dir": _s_removed_dir,
          "foreign": _s_foreign, "other_run": _s_other_run,
          "dir_log": _s_dir_log, "nonjson": _s_nonjson, "utf8": _s_utf8,
          "nonobj": _s_nonobj}[scn]
    run_id = mk(fx)
    out = _report(_cli(*_verify_args(fx.P, fx.D, run_id)))
    if fx.P.is_dir():
        copy = fx.P
    else:
        copy_dir = tmp_path / "copy"
        copy_dir.mkdir()
        copy = copy_dir / "events.jsonl"
        if fx.P.exists():
            copy.write_bytes(fx.P.read_bytes())
    args = types.SimpleNamespace(workflow="phase_5_implement", event_log=str(copy))
    res = run._oracle_entry_verify(args, _ctx(fx.D), run_id)
    expected = None if res is None else res.error_code
    assert out["code"] == expected


@pytest.mark.parametrize("scn", ["verified", "content", "foreign"])
def test_ac8_key_set_single_line(tmp_path, scn):
    fx = _fx(tmp_path)
    run_id = {"verified": _s_verified, "content": _s_content,
              "foreign": _s_foreign}[scn](fx)
    proc = _cli(*_verify_args(fx.P, fx.D, run_id))
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.strip().splitlines()) == 1
    assert set(json.loads(proc.stdout)) == KEYS


def _hand_frozen(fx: Fx, payload: Any, top_run_id: bool = True) -> None:
    ev: "dict[str, Any]" = {"event_type": "oracle_frozen", "payload": payload}
    if top_run_id:
        ev["run_id"] = RUN_ID
    fx.P.write_text(json.dumps(ev) + "\n", encoding="utf-8")


@pytest.mark.parametrize("case", [
    "no_args", "bogus_sub", "no_event_log", "no_run_id", "no_scratch",
    "unknown_flag", "positional", "payload_str", "members_no_path",
    "empty_run_id", "blank_run_id", "payload_str_no_run_id",
    "no_payload_key", "digest_int"])
def test_ac9_usage_errors_exit_2(tmp_path, case):
    fx = _fx(tmp_path)
    full = _verify_args(fx.P, fx.D)
    must_contain = None
    if case == "no_args":
        argv = []
    elif case == "bogus_sub":
        argv = ["bogus"]
    elif case in ("no_event_log", "no_run_id", "no_scratch"):
        _freeze(fx)
        flag = {"no_event_log": "--event-log", "no_run_id": "--run-id",
                "no_scratch": "--scratchpad-dir"}[case]
        i = full.index(flag)
        argv = full[:i] + full[i + 2:]
    elif case in ("unknown_flag", "positional"):
        _freeze(fx)
        argv = full + (["--bogus"] if case == "unknown_flag" else ["extra"])
    elif case in ("empty_run_id", "blank_run_id"):
        _freeze(fx)
        argv = _verify_args(fx.P, fx.D, "" if case == "empty_run_id" else "  ")
    elif case == "payload_str":
        _hand_frozen(fx, "x")
        argv, must_contain = full, "malformed"
    elif case == "payload_str_no_run_id":
        _hand_frozen(fx, "x", top_run_id=False)
        argv, must_contain = full, "malformed"
    elif case == "no_payload_key":
        fx.P.write_text(json.dumps({"event_type": "oracle_frozen",
                                    "run_id": RUN_ID}) + "\n", encoding="utf-8")
        argv, must_contain = full, "malformed"
    elif case == "digest_int":
        _hand_frozen(fx, {"run_id": RUN_ID, "digest": 1, "members": [],
                          "scope": []})
        argv, must_contain = full, "malformed"
    else:
        _hand_frozen(fx, {"run_id": RUN_ID, "members": [{"digest": "d"}]})
        argv, must_contain = full, "malformed"
    proc = _cli(*argv)
    assert proc.returncode == 2, f"rc={proc.returncode} stderr={proc.stderr!r}"
    assert proc.stdout == ""
    assert proc.stderr.startswith("oracle: "), proc.stderr
    if must_contain:
        assert must_contain in proc.stderr


@pytest.mark.parametrize("scn", ["verified", "content"])
def test_ac10_read_only(tmp_path, scn):
    fx = _fx(tmp_path)
    run_id = {"verified": _s_verified, "content": _s_content}[scn](fx)
    log_before = hashlib.sha256(fx.P.read_bytes()).hexdigest()
    tree_before = _tree_hash(fx.D)
    dirs_before = sorted(str(p) for p in tmp_path.rglob("*"))
    out = _report(_cli(*_verify_args(fx.P, fx.D, run_id)))
    assert out["outcome"] == ("verified" if scn == "verified" else "mutated")
    assert hashlib.sha256(fx.P.read_bytes()).hexdigest() == log_before
    assert _tree_hash(fx.D) == tree_before
    assert sorted(str(p) for p in tmp_path.rglob("*")) == dirs_before


def test_ac11_seam_hygiene():
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    tree = ast.parse(ORACLE_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & {"argparse", "sys"}
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in {"argparse", "sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "bytedigger_engine" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in oracle.py"
            assert (node.module or "").split(".")[0] != "bytedigger_engine"
    assert "argparse" not in vars(oracle)
    assert "sys" not in vars(oracle)
    assert any(isinstance(n, ast.FunctionDef) and n.name == "_main" for n in tree.body)
    assert hasattr(oracle, "_main") and callable(oracle._main)


def test_ac12_in_process_matches_subprocess(tmp_path, capsys):
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    fx = _fx(tmp_path)
    _freeze(fx)
    argv = _verify_args(fx.P, fx.D)
    sub = _report(_cli(*argv))
    capsys.readouterr()
    rc = oracle._main(argv)
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == sub
