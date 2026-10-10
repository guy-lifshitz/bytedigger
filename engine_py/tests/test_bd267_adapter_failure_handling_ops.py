"""bd#267 (group 2) -- host failure-handling operations for ADV-4 and ADV-5, in shadow.

Spec: `docs/decisions/2026-10-10-bd267-adapter-failure-handling-ops.md`.

Under an adapter, ADV-4 and ADV-5 used to be `not_executed`. The adapter may now
offer `record_oracle_step(fault)` (ADV-4) and `record_gate_step(fault)` (ADV-5),
each returning a mapping with a `"status"` string. The harness calls the method
with `None` (control, must be "passed") and then with the fault value, judges the
status, and files the result as `shadow:<outcome>`, which never counts toward a
level. `adapter_ops="off"` restores the old ceiling.

`conformance.*` imports are deferred into test bodies. Nothing under
`bytedigger_engine/` is put on sys.path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent

# (adversary, method name, fault value, defended status)
ROWS = [
    ("ADV-4", "record_oracle_step", "oracle_load_failure", "load_failure"),
    ("ADV-5", "record_gate_step", "gate_raises", "failed"),
]
ROW_IDS = [r[0] for r in ROWS]

ALL_STATUSES = ("passed", "failed", "rejected", "load_failure", "absent")


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _oracle():
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    return oracle


# --------------------------------------------------------------------------
# Stub adapters (live in the test module). Each records a call log.
# --------------------------------------------------------------------------

class _Base:
    identity = {"backend": "stub-backend", "source": "stub-source"}

    def __init__(self):
        self.calls = []
        self.args = []

    def _digest(self, root, members):
        rels = [str(Path(p).relative_to(root)) for p in members]
        return _oracle().compute_digest(root, rels)

    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        return self._digest(root, list(paths))

    def evaluate(self, state):
        self.calls.append("evaluate")
        O = _oracle().OracleOutcome
        now = self._digest(state.root, list(state.members))
        return O.ACCEPTED if now == state.frozen else O.REJECTED


class _Raise:
    def __init__(self, exc):
        self.exc = exc


def _resolve(x):
    if isinstance(x, _Raise):
        raise x.exc
    return x


def _st(status):
    return {"status": status}


def _make(method, responder, identity=None, extra=None):
    """A stub with `method(fault)` that records the fault argument."""

    def op(self, fault):
        self.calls.append(method)
        self.args.append(fault)
        return responder(fault)

    ns = {method: op}
    if identity is not None:
        ns["identity"] = identity
    if extra:
        ns.update(extra)
    return type("Stub", (_Base,), ns)()


def _answers(method, control, fault_answer, **kw):
    """Answers `control` to None and `fault_answer` to any other argument."""
    return _make(
        method,
        lambda f: _resolve(control if f is None else fault_answer),
        **kw,
    )


def _conforming_method(method, defended):
    return lambda f: _st("passed") if f is None else _st(defended)


class Conforming(_Base):
    """Answers correctly for both operations."""

    def record_oracle_step(self, fault):
        self.calls.append("record_oracle_step")
        self.args.append(fault)
        return _st("passed") if fault is None else _st("load_failure")

    def record_gate_step(self, fault):
        self.calls.append("record_gate_step")
        self.args.append(fault)
        return _st("passed") if fault is None else _st("failed")


# --------------------------------------------------------------------------
# AC1: the operation is called, not the engine probe
# --------------------------------------------------------------------------

@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac1_host_answer_decides_not_the_engine_probe(adv, method, fault, defended):
    h = _h()
    assert h.run_adversaries(only=(adv,)) == {adv: "defended"}
    a = _make(method, lambda f: _st("passed"))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:undefended"}
    assert a.calls.count(method) >= 1


# --------------------------------------------------------------------------
# AC2: conforming adapter
# --------------------------------------------------------------------------

def _wrap_dict(s):
    return {"status": s}


def _wrap_extra(s):
    return {"status": s, "detail": "x", "n": 3}


def _wrap_proxy(s):
    return types.MappingProxyType({"status": s, "extra": 1})


@pytest.mark.parametrize("wrap", [_wrap_dict, _wrap_extra, _wrap_proxy],
                         ids=["dict", "extra-keys", "mappingproxy"])
@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac2_conforming_is_shadow_defended_called_twice_in_order(
    adv, method, fault, defended, wrap
):
    h = _h()
    a = _make(method, lambda f: wrap("passed") if f is None else wrap(defended))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:defended"}
    assert a.calls == [method, method]
    assert a.args == [None, fault]


# --------------------------------------------------------------------------
# AC3: wrong implementations fail
# --------------------------------------------------------------------------

_WRONG = [
    ("ADV-4", "record_oracle_step", "oracle_load_failure", s)
    for s in ("rejected", "passed", "failed", "absent")
] + [
    ("ADV-5", "record_gate_step", "gate_raises", s)
    for s in ("absent", "passed", "rejected", "load_failure")
]


@pytest.mark.parametrize("adv,method,fault,wrong", _WRONG,
                         ids=[f"{r[0]}-{r[3]}" for r in _WRONG])
def test_ac3_wrong_fault_status_is_shadow_undefended(adv, method, fault, wrong):
    h = _h()
    a = _answers(method, _st("passed"), _st(wrong))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:undefended"}
    assert a.args == [None, fault]


# --------------------------------------------------------------------------
# AC4: control fails first
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["failed", "absent", "rejected", "load_failure"])
@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac4_control_not_passed_is_indeterminate_after_one_call(
    adv, method, fault, defended, bad
):
    h = _h()
    a = _answers(method, _st(bad), _st(defended))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:indeterminate"}
    assert a.calls == [method]
    assert a.args == [None]


@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac4_answering_the_defended_status_to_everything_is_caught(
    adv, method, fault, defended
):
    h = _h()
    a = _make(method, lambda f: _st(defended))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:indeterminate"}
    assert a.calls == [method]


# --------------------------------------------------------------------------
# AC5: degrade, don't fail
# --------------------------------------------------------------------------

_MALFORMED = [
    pytest.param(None, id="none"),
    pytest.param("passed", id="bare-string"),
    pytest.param([], id="list"),
    pytest.param({}, id="empty-dict"),
    pytest.param({"status": None}, id="status-none"),
    pytest.param({"status": 1}, id="status-int"),
    pytest.param({"status": "bogus"}, id="status-bogus"),
    pytest.param({"state": "passed"}, id="no-status-key"),
]


@pytest.mark.parametrize("bad", _MALFORMED)
@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_malformed_control_is_indeterminate(adv, method, fault, defended, bad):
    h = _h()
    a = _answers(method, bad, _st(defended))
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


@pytest.mark.parametrize("bad", _MALFORMED)
@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_malformed_fault_answer_is_indeterminate(adv, method, fault, defended, bad):
    h = _h()
    a = _answers(method, _st("passed"), bad)
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}
    assert a.args == [None, fault]


@pytest.mark.parametrize("where", ["control", "fault"])
@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_raising_method_is_indeterminate(adv, method, fault, defended, where):
    h = _h()
    boom = _Raise(RuntimeError("boom"))
    if where == "control":
        a = _answers(method, boom, _st(defended))
    else:
        a = _answers(method, _st("passed"), boom)
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


@pytest.mark.parametrize("where", ["control", "fault"])
@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_hung_method_is_indeterminate_and_worker_is_daemon(
    adv, method, fault, defended, where
):
    h = _h()
    entered = threading.Event()

    def hang():
        entered.set()
        time.sleep(5.0)
        return _st("passed")

    def responder(f):
        if where == "control":
            return hang()
        return _st("passed") if f is None else hang()

    a = _make(method, responder)
    t0 = time.monotonic()
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {adv: "shadow:indeterminate"}
    assert elapsed < 2.5, f"guard waited for the hung method ({elapsed:.2f}s)"
    assert entered.wait(1.0)
    if where == "control":
        assert a.args == [None]
    main = threading.main_thread()
    non_daemon = [
        t.name for t in threading.enumerate()
        if t is not main and t.is_alive() and not t.daemon
    ]
    assert non_daemon == []


@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_non_callable_attribute_is_indeterminate(adv, method, fault, defended):
    h = _h()
    a = type("Stub", (_Base,), {method: "not a function"})()
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


class ProviderUnavailable(Exception):
    pass


@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_subscription_session_down_is_indeterminate(adv, method, fault, defended):
    h = _h()
    a = _make(
        method,
        lambda f: _resolve(_Raise(ConnectionError("session probe failed"))),
        identity={"backend": "subscription-session", "source": "env"},
    )
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_api_token_missing_is_indeterminate(adv, method, fault, defended):
    h = _h()
    a = _make(
        method,
        lambda f: _resolve(_Raise(ProviderUnavailable("token missing"))),
        identity={"backend": "api-token", "source": "kwarg"},
    )
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)],
                         ids=["keyboard-interrupt", "system-exit"])
@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac5_interrupts_propagate(adv, method, fault, defended, exc):
    h = _h()
    a = _answers(method, _Raise(exc), _st(defended))
    with pytest.raises(type(exc)):
        h.run_adversaries(only=(adv,), adapter=a, timeout_s=5.0)


# --------------------------------------------------------------------------
# AC6 (independence, shadow half): each operation works without the other
# --------------------------------------------------------------------------

def test_ac6_operations_are_independent():
    h = _h()
    only4 = _make("record_oracle_step", _conforming_method("record_oracle_step", "load_failure"))
    out4 = h.run_adversaries(adapter=only4)
    assert out4["ADV-4"] == "shadow:defended"
    assert out4["ADV-5"] == "not_executed"

    only5 = _make("record_gate_step", _conforming_method("record_gate_step", "failed"))
    out5 = h.run_adversaries(adapter=only5)
    assert out5["ADV-5"] == "shadow:defended"
    assert out5["ADV-4"] == "not_executed"


# --------------------------------------------------------------------------
# AC7: shadow is not counted
# --------------------------------------------------------------------------

def test_ac7_shadow_defended_does_not_lift_the_level():
    h = _h()
    out = h.run_adversaries(adapter=Conforming())
    assert out["ADV-1"] == h.OUTCOME_DEFENDED
    assert out["ADV-2"] == h.OUTCOME_DEFENDED
    assert out["ADV-4"] == "shadow:defended"
    assert out["ADV-5"] == "shadow:defended"

    outcomes = {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}
    outcomes["ADV-4"] = out["ADV-4"]
    outcomes["ADV-5"] = out["ADV-5"]
    att = h.build_attestation(
        outcomes,
        level_claimed="BD-L2",
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-10-10T00:00:00Z",
    )
    assert att["level_achieved"] == "BD-L1"
    complaints = h.validate_attestation(att)
    assert any("level_claimed" in c for c in complaints), complaints


# --------------------------------------------------------------------------
# AC9: scoped run
# --------------------------------------------------------------------------

@pytest.mark.parametrize("adv,method,fault,defended", ROWS, ids=ROW_IDS)
def test_ac9_scoped_run_calls_only_its_own_method_twice(adv, method, fault, defended):
    h = _h()
    a = Conforming()
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:defended"}
    assert a.calls == [method, method]
    assert a.args == [None, fault]
    for other in ("freeze", "evaluate", "record_oracle_step", "record_gate_step"):
        if other != method:
            assert other not in a.calls


# --------------------------------------------------------------------------
# AC11: CLI end-to-end (subprocess)
# --------------------------------------------------------------------------

_FACTORY_SRC = textwrap.dedent('''
    from pathlib import Path
    from bytedigger_engine.conformance import oracle as _o

    class Host:
        identity = {"backend": "cli-backend", "source": "cli-source"}

        def _digest(self, root, members):
            rels = [str(Path(p).relative_to(root)) for p in members]
            return _o.compute_digest(root, rels)

        def freeze(self, paths, *, root):
            return self._digest(root, list(paths))

        def evaluate(self, state):
            O = _o.OracleOutcome
            now = self._digest(state.root, list(state.members))
            return O.ACCEPTED if now == state.frozen else O.REJECTED

        def record_oracle_step(self, fault):
            return {"status": "passed"} if fault is None else {"status": "load_failure"}

        def record_gate_step(self, fault):
            return {"status": "passed"} if fault is None else {"status": "failed"}

    def make_host():
        return Host()
''')

_MOD = "bd267_cli_factories"


def _cli(tmp_path, *extra):
    (tmp_path / f"{_MOD}.py").write_text(_FACTORY_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_PY) + os.pathsep + str(tmp_path)
    cmd = [
        sys.executable, "-m", "bytedigger_engine.conformance.harness",
        "--adapter", f"{_MOD}:make_host",
        "--level-claimed", "BD-L1",
        "--engine-version", "0.0.0-test",
        "--host-identity", "test-host",
        "--timeout-s", "5",
        *extra,
    ]
    return subprocess.run(
        cmd, cwd=str(ENGINE_PY), env=env, capture_output=True, text=True, timeout=60
    )


def test_ac11_cli_reports_shadow_defended_and_off_is_not_executed(tmp_path):
    r = _cli(tmp_path)
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["adversaries"]["ADV-4"] == "shadow:defended"
    assert att["adversaries"]["ADV-5"] == "shadow:defended"
    assert att["level_achieved"] == "BD-L1"

    r_off = _cli(tmp_path, "--adapter-ops", "off")
    assert r_off.returncode == 0, r_off.stderr
    off = json.loads(r_off.stdout)
    assert off["adversaries"]["ADV-4"] == "not_executed"
    assert off["adversaries"]["ADV-5"] == "not_executed"
