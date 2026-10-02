"""RED tests for bd#158 -- a malformed `oracle_frozen` / `oracle_amended` row
refuses `E_ORACLE_INDETERMINATE` instead of escaping as a plain exception.

Spec: docs/decisions/2026-10-02-bd158-oracle-malformed-freeze.md (AC1-AC7, r1).

Every `bytedigger_engine.*` import is inside a helper or test body, and the new
symbols (`check_freeze_payload`, `OracleMalformedFreeze`) are fetched lazily, so
the file collects and each AC fails at assert time. Real scratchpad, real
events.jsonl, real CLI subprocess; nothing mocked.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

ENGINE_PY = Path(__file__).resolve().parents[1]
RUN_ID = "run-A"
CODE = "E_ORACLE_INDETERMINATE"
OMIT = object()  # sentinel: the row carries no `payload` key


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


def _freeze(fx: Fx) -> None:
    from bytedigger_engine import run  # noqa: PLC0415

    args = types.SimpleNamespace(workflow="phase_45_spec", event_log=str(fx.P))
    ret = run._oracle_after_execute(args, _ctx(fx.D), RUN_ID,
                                    types.SimpleNamespace(status="ok"))
    assert ret is None, f"real freeze did not succeed: {ret!r}"


def _good_payload(fx: Fx) -> "dict[str, Any]":
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    return oracle.build_freeze_payload("phase_45_spec", RUN_ID, fx.D, [])


def _row(payload: Any, top_run_id: "str | None" = RUN_ID,
         event_type: str = "oracle_frozen") -> "dict[str, Any]":
    ev: "dict[str, Any]" = {"ts": "2026-10-02T00:00:00Z", "event_type": event_type}
    if top_run_id is not None:
        ev["run_id"] = top_run_id
    if payload is not OMIT:
        ev["payload"] = payload
    return ev


def _write_rows(fx: Fx, *rows: "dict[str, Any]") -> None:
    fx.P.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _cli(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.oracle", *args],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=60,
    )


def _need(name: str) -> Any:
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    sym = getattr(oracle, name, None)
    assert sym is not None, f"bd#158: oracle.{name} does not exist yet"
    return sym


# ---- malformed shapes (spec AC1 list, plus `members: {}` from the §1 table) --

def _mut(fn):
    def apply(p):
        p = copy.deepcopy(p)
        fn(p)
        return p
    return apply


def _set(key, value):
    return _mut(lambda p: p.__setitem__(key, value))


def _member0(key, value):
    return _mut(lambda p: p["members"][0].__setitem__(key, value))


def _member0_drop(key):
    return _mut(lambda p: p["members"][0].pop(key))


# name -> (payload builder from good payload, top-level run_id on the row)
SHAPES: "dict[str, tuple[Any, str | None]]" = {
    "payload_str_runid": (lambda p: "x", RUN_ID),
    "payload_str_norun": (lambda p: "x", None),
    "no_payload_key": (lambda p: OMIT, RUN_ID),
    "payload_list": (lambda p: [1], RUN_ID),
    "members_int": (_set("members", 3), RUN_ID),
    "members_str": (_set("members", "ab"), RUN_ID),
    "members_dict": (_set("members", {}), RUN_ID),
    "member_no_path": (_mut(lambda p: p.__setitem__("members", [{"digest": "d"}])), RUN_ID),
    "member_path_int": (_mut(lambda p: p.__setitem__("members", [{"path": 3, "digest": "d"}])), RUN_ID),
    "member_int": (_mut(lambda p: p.__setitem__("members", [3])), RUN_ID),
    "member_null": (_mut(lambda p: p.__setitem__("members", [None])), RUN_ID),
    "scope_int": (_set("scope", 5), RUN_ID),
    "scope_list_int": (_set("scope", [1]), RUN_ID),
    "digest_int": (_set("digest", 1), RUN_ID),
    "member_digest_int": (_member0("digest", 1), RUN_ID),
    "scope_digest_absent": (_mut(lambda p: p.pop("scope_digest")), RUN_ID),
    "scope_digest_int": (_set("scope_digest", 1), RUN_ID),
    # spec r2 (gate r1 F1): str values the OS path layer rejects
    "scope_nul": (_set("scope", ["sp\x00ecs"]), RUN_ID),
    "member_path_nul": (_member0("path", "sp\x00ecs/a.md"), RUN_ID),
    # spec r2 (gate r1 F3): the same row defects under oracle_amended
    "amended_payload_str": (lambda p: "x", RUN_ID),
    "amended_member_no_path": (_mut(lambda p: p.__setitem__("members", [{"digest": "d"}])), RUN_ID),
}
EVENT_TYPE = {"amended_payload_str": "oracle_amended",
              "amended_member_no_path": "oracle_amended"}
SHAPE_IDS = list(SHAPES)
PAYLOAD_LEVEL = [s for s in SHAPE_IDS if s != "no_payload_key"]


def _write_shape(fx: Fx, shape: str) -> None:
    build, top = SHAPES[shape]
    _write_rows(fx, _row(build(_good_payload(fx)), top,
                         EVENT_TYPE.get(shape, "oracle_frozen")))


def _entry(fx: Fx, workflow: str = "phase_5_implement"):
    from bytedigger_engine import run  # noqa: PLC0415

    args = types.SimpleNamespace(workflow=workflow, event_log=str(fx.P))
    return run._oracle_entry_verify(args, _ctx(fx.D), RUN_ID)


def _exit(fx: Fx, workflow: str = "phase_5_implement", **org: Any):
    from bytedigger_engine import run  # noqa: PLC0415

    args = types.SimpleNamespace(workflow=workflow, event_log=str(fx.P))
    return run._oracle_after_execute(args, _ctx(fx.D, **org), RUN_ID,
                                     types.SimpleNamespace(status="ok"))


def _assert_malformed_refusal(res: Any, ac: str) -> None:
    assert res is not None, f"{ac}: malformed freeze was accepted (returned None)"
    assert res.status == "error", f"{ac}: status {res.status!r}"
    assert res.error_code == CODE, f"{ac}: error_code {res.error_code!r}, want {CODE}"
    assert res.recoverable is False, f"{ac}: recoverable must be False"
    assert "malformed freeze event" in (res.error or ""), \
        f"{ac}: error text lacks 'malformed freeze event': {res.error!r}"


def _call(fn, ac: str):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"{ac}: exception escaped instead of a refusal: "
                    f"{e.__class__.__name__}: {e}")


# ---- AC1 ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", SHAPE_IDS)
def test_ac1_engine_entry_refuses_malformed(tmp_path, shape):
    fx = _fx(tmp_path)
    _write_shape(fx, shape)
    res = _call(lambda: _entry(fx), f"AC1[{shape}]")
    _assert_malformed_refusal(res, f"AC1[{shape}]")


# ---- AC2 ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", [
    "no_payload_key", "payload_str_runid", "member_no_path", "digest_int",
    "members_int", "scope_digest_int"])
def test_ac2_engine_exit_refuses_malformed(tmp_path, shape):
    fx = _fx(tmp_path)
    _write_shape(fx, shape)
    res = _call(lambda: _exit(fx), f"AC2[{shape}]")
    _assert_malformed_refusal(res, f"AC2[{shape}]")


# ---- AC3 ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", ["payload_str_runid", "payload_list", "payload_str_norun"])
def test_ac3_reentry_amendment_refuses_and_writes_no_freeze(tmp_path, shape):
    fx = _fx(tmp_path)
    _write_shape(fx, shape)
    before = fx.P.read_bytes()
    res = _call(lambda: _exit(fx, "phase_45_spec"), f"AC3[{shape}]")
    _assert_malformed_refusal(res, f"AC3[{shape}]")
    after = fx.P.read_bytes()
    assert after.startswith(before), f"AC3[{shape}]: pre-existing log bytes were altered"
    added = [json.loads(x) for x in after[len(before):].decode("utf-8").splitlines() if x.strip()]
    # run.py's refusal helper (unchanged, out of scope) records `phase_refused`;
    # the forbidden outcome is any freeze/amendment row being written.
    bad = [e["event_type"] for e in added
           if e["event_type"] in ("oracle_frozen", "oracle_amended")]
    assert not bad, f"AC3[{shape}]: refusal still appended {bad}"


def test_ac3b_amendment_over_dict_but_malformed_previous_refuses(tmp_path):
    fx = _fx(tmp_path)
    _write_rows(fx, _row({**_good_payload(fx), "digest": 1}))
    before = fx.P.read_bytes()
    res = _call(lambda: _exit(fx, "phase_45_spec", oracle_amendment_reason="r"), "AC3b")
    _assert_malformed_refusal(res, "AC3b")
    after = fx.P.read_bytes()
    assert after.startswith(before), "AC3b: pre-existing log bytes were altered"
    added = [json.loads(x) for x in after[len(before):].decode("utf-8").splitlines() if x.strip()]
    bad = [e["event_type"] for e in added
           if e["event_type"] in ("oracle_frozen", "oracle_amended")]
    assert not bad, f"AC3b: refusal still appended {bad}"


# ---- AC4b / AC4c -------------------------------------------------------------

@pytest.mark.parametrize("shape", PAYLOAD_LEVEL)
def test_ac4b_verify_against_direct_raises(tmp_path, shape):
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    fx = _fx(tmp_path)
    exc_cls = _need("OracleMalformedFreeze")
    build, _top = SHAPES[shape]
    bad = build(_good_payload(fx))
    try:
        oracle.verify_against(bad, fx.D)
    except exc_cls:
        return
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"AC4b[{shape}]: {e.__class__.__name__} instead of OracleMalformedFreeze: {e}")
    pytest.fail(f"AC4b[{shape}]: verify_against accepted a malformed payload")


def test_ac4c_overlong_path_component_never_escapes(tmp_path):
    fx = _fx(tmp_path)
    long = "a" * 300
    payload = {**_good_payload(fx),
               "members": [{"path": f"{long}/a.md", "digest": "0" * 64}],
               "scope": [long]}
    _write_rows(fx, _row(payload))
    res = _call(lambda: _entry(fx), "AC4c")
    assert res is not None, "AC4c: overlong-path freeze was accepted"
    assert (res.error_code or "").startswith("E_ORACLE_"), \
        f"AC4c: error_code {res.error_code!r}"
    proc = _cli("verify", "--event-log", str(fx.P), "--scratchpad-dir", str(fx.D),
                "--run-id", RUN_ID)
    assert proc.returncode in (0, 2), \
        f"AC4c: CLI rc={proc.returncode} stderr={proc.stderr!r}"


# ---- AC4 ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", PAYLOAD_LEVEL)
def test_ac4_check_freeze_payload_raises(tmp_path, shape):
    fx = _fx(tmp_path)
    check = _need("check_freeze_payload")
    exc_cls = _need("OracleMalformedFreeze")
    refusal = _need("OracleRefusal")
    assert issubclass(exc_cls, refusal), "AC4: OracleMalformedFreeze must subclass OracleRefusal"
    build, _top = SHAPES[shape]
    bad = build(_good_payload(fx))
    with pytest.raises(exc_cls) as ei:
        check(bad)
    assert ei.value.code == CODE, f"AC4[{shape}]: code {ei.value.code!r}"


def _valid_variants(fx: Fx) -> "dict[str, Any]":
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    good = _good_payload(fx)
    amend = oracle.build_amendment_payload(
        "phase_45_spec", RUN_ID, fx.D, "reason", good["digest"], [])
    v: "dict[str, Any]" = {
        "real_freeze": good,
        "real_amendment": amend,
        "bare_str_members": {**good, "members": ["specs/a.md", "specs/b.md"]},
        "members_none": {**good, "members": None},
        "members_empty": {**good, "members": []},
        "scope_none": {**good, "scope": None},
        "scope_empty": {**good, "scope": []},
        "extra_key": {**good, "extra": object.__class__.__name__},
    }
    no_members = dict(good)
    no_members.pop("members")
    v["members_absent"] = no_members
    no_scope = dict(good)
    no_scope.pop("scope")
    v["scope_absent"] = no_scope
    return v


@pytest.mark.parametrize("variant", [
    "real_freeze", "real_amendment", "bare_str_members", "members_absent",
    "members_none", "members_empty", "scope_absent", "scope_none",
    "scope_empty", "extra_key"])
def test_ac4_check_freeze_payload_accepts_wellformed(tmp_path, variant):
    fx = _fx(tmp_path)
    check = _need("check_freeze_payload")
    assert check(_valid_variants(fx)[variant]) is None, f"AC4[{variant}]: not None"


# ---- AC5 ---------------------------------------------------------------------

def test_ac5_i_other_run_malformed_row_is_not_selected(tmp_path):
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    ev = [_row("x", top_run_id="other")]
    assert oracle.find_last_freeze(ev, RUN_ID) is None, "AC5(i): expected None"


@pytest.mark.parametrize("top", [RUN_ID, None])
def test_ac5_ii_malformed_then_wellformed_returns_wellformed(tmp_path, top):
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    fx = _fx(tmp_path)
    good_row = _row(_good_payload(fx))
    ev = [_row("x", top_run_id=top), good_row]
    got = _call(lambda: oracle.find_last_freeze(ev, RUN_ID), f"AC5(ii)[top={top}]")
    assert got is good_row, f"AC5(ii)[top={top}]: well-formed row not returned"


@pytest.mark.parametrize("bad", ["payload_str_runid", "no_payload_key", "payload_list"])
def test_ac5_iii_wellformed_then_malformed_raises(tmp_path, bad):
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    fx = _fx(tmp_path)
    exc_cls = _need("OracleMalformedFreeze")
    build, top = SHAPES[bad]
    ev = [_row(_good_payload(fx)), _row(build(_good_payload(fx)), top)]
    with pytest.raises(exc_cls):
        oracle.find_last_freeze(ev, RUN_ID)


def test_ac5_iv_payload_str_no_top_run_id_raises_malformed(tmp_path):
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    exc_cls = _need("OracleMalformedFreeze")
    try:
        oracle.find_last_freeze([_row("x", top_run_id=None)], RUN_ID)
    except exc_cls:
        return
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"AC5(iv): {e.__class__.__name__} escaped, want OracleMalformedFreeze")
    pytest.fail("AC5(iv): no exception raised")


# ---- AC6 ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", SHAPE_IDS)
def test_ac6_engine_cli_parity(tmp_path, shape):
    fx = _fx(tmp_path)
    _write_shape(fx, shape)
    proc = _cli("verify", "--event-log", str(fx.P), "--scratchpad-dir", str(fx.D),
                "--run-id", RUN_ID)
    assert proc.returncode == 2, \
        f"AC6[{shape}]: CLI rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stdout == "", f"AC6[{shape}]: CLI stdout not empty: {proc.stdout!r}"
    assert "malformed" in proc.stderr, f"AC6[{shape}]: stderr {proc.stderr!r}"
    res = _call(lambda: _entry(fx), f"AC6[{shape}]")
    _assert_malformed_refusal(res, f"AC6[{shape}]")


# ---- AC7 ---------------------------------------------------------------------

def test_ac7_wellformed_unchanged_verifies(tmp_path):
    fx = _fx(tmp_path)
    _freeze(fx)
    assert _entry(fx) is None, "AC7: unchanged freeze must verify"


def test_ac7_wellformed_rewritten_member_is_mutated(tmp_path):
    fx = _fx(tmp_path)
    _freeze(fx)
    (fx.specs / "a.md").write_text("changed\n", encoding="utf-8")
    res = _entry(fx)
    assert res is not None, "AC7: rewritten member was not refused"
    assert res.error_code == "E_ORACLE_MUTATED", f"AC7: code {res.error_code!r}"
    assert "mutated:content" in (res.error or ""), f"AC7: error {res.error!r}"
