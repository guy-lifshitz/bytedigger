"""bd#269 (group 3) -- a host operation for ADV-6 (suppression ledger), in shadow.

Spec: `docs/decisions/2026-10-10-bd269-adapter-suppression-ledger-op.md`.

Under an adapter, ADV-6 used to be `not_executed`. The adapter may now offer
`admit_ledger_row(row, as_of)`, returning a mapping with an `"accepted"` bool.
The harness calls it with three fixed rows (control, no owner, expired), always
with `as_of == "2026-01-01"`, judges the answers itself, and files the result
as `shadow:<outcome>`, which never counts toward a level. `adapter_ops="off"`
restores the old ceiling.

No test here reads the clock. Stubs that judge dates compare the ISO strings of
`row["kill_by"]` and the `as_of` they are handed.

`conformance.*` imports are deferred into test bodies. Nothing under
`bytedigger_engine/` is put on sys.path.
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import types
from collections.abc import Mapping
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent

METHOD = "admit_ledger_row"
AS_OF = "2026-01-01"

CONTROL = {"issue": "#1", "kill_by": "2099-01-01"}
NO_OWNER = {"issue": "", "kill_by": "2099-01-01"}
EXPIRED = {"issue": "#1", "kill_by": "2020-01-01"}
TABLE = [CONTROL, NO_OWNER, EXPIRED]


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
        self.rows = []    # snapshot of each row, taken before the host sees it
        self.as_ofs = []
        self.objs = []    # the row objects themselves

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


def _acc(value):
    return {"accepted": value}


def _make(responder, identity=None, extra=None):
    """A stub with `admit_ledger_row(row, as_of)` that records both arguments."""

    def op(self, row, as_of):
        self.calls.append(METHOD)
        self.rows.append(dict(row))
        self.as_ofs.append(as_of)
        self.objs.append(row)
        return responder(row, as_of)

    ns = {METHOD: op}
    if identity is not None:
        ns["identity"] = identity
    if extra:
        ns.update(extra)
    return type("Stub", (_Base,), ns)()


def _kind(row):
    if row["issue"] == "":
        return "no_owner"
    if row["kill_by"] == EXPIRED["kill_by"]:
        return "expired"
    return "control"


def _per_row(control, no_owner, expired):
    """Answers `control`, `no_owner` or `expired` depending on which row it got."""
    table = {"control": control, "no_owner": no_owner, "expired": expired}
    return lambda row, as_of: _resolve(table[_kind(row)])


def _judge(row, as_of):
    """A host that judges from its arguments, with no clock."""
    return bool(row["issue"].strip()) and row["kill_by"] >= as_of


def _conforming_responder(row, as_of):
    return _acc(_judge(row, as_of))


def _conforming():
    return _make(_conforming_responder)


# --------------------------------------------------------------------------
# AC1: the operation is called, not the engine probe
# --------------------------------------------------------------------------

def test_ac1_host_answer_decides_not_the_engine_probe():
    h = _h()
    assert h.run_adversaries(only=("ADV-6",)) == {"ADV-6": "defended"}
    a = _make(lambda row, as_of: _acc(True))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:undefended"}
    assert a.calls.count(METHOD) >= 1


# --------------------------------------------------------------------------
# AC2: conforming host
# --------------------------------------------------------------------------

def test_ac2_conforming_is_shadow_defended_called_three_times_in_order():
    h = _h()
    a = _conforming()
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:defended"}
    assert a.calls == [METHOD] * 3
    assert a.rows == TABLE
    assert a.as_ofs == [AS_OF] * 3


def test_ac2_each_call_gets_a_fresh_dict():
    h = _h()
    a = _conforming()
    h.run_adversaries(only=("ADV-6",), adapter=a)
    assert len(a.objs) == 3
    assert all(type(o) is dict for o in a.objs)
    assert len({id(o) for o in a.objs}) == 3


class _OpaqueMapping(Mapping):
    """A Mapping that is not a dict."""

    def __init__(self, data):
        self._data = dict(data)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def _wrap_dict(v):
    return {"accepted": v}


def _wrap_extra(v):
    return {"accepted": v, "detail": "x", "n": 3}


def _wrap_proxy(v):
    return types.MappingProxyType({"accepted": v, "extra": 1})


def _wrap_opaque(v):
    return _OpaqueMapping({"accepted": v})


@pytest.mark.parametrize(
    "wrap", [_wrap_dict, _wrap_extra, _wrap_proxy, _wrap_opaque],
    ids=["dict", "extra-keys", "mappingproxy", "non-dict-mapping"],
)
def test_ac2_extra_keys_and_non_dict_mappings_are_accepted(wrap):
    h = _h()
    a = _make(lambda row, as_of: wrap(_judge(row, as_of)))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:defended"}
    assert a.calls == [METHOD] * 3


def test_ac2_a_host_that_clears_its_row_argument_changes_nothing():
    h = _h()

    def responder(row, as_of):
        answer = _acc(_judge(row, as_of))
        row.clear()
        return answer

    a = _make(responder)
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:defended"}
    assert a.rows == TABLE


# --------------------------------------------------------------------------
# AC3: wrong implementations fail
# --------------------------------------------------------------------------

def test_ac3_accepting_the_no_owner_row_is_undefended_after_two_calls():
    h = _h()
    a = _make(_per_row(_acc(True), _acc(True), _acc(False)))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:undefended"}
    assert a.calls == [METHOD] * 2
    assert a.rows == [CONTROL, NO_OWNER]


def test_ac3_accepting_the_expired_row_is_undefended_after_three_calls():
    h = _h()
    a = _make(_per_row(_acc(True), _acc(False), _acc(True)))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:undefended"}
    assert a.calls == [METHOD] * 3
    assert a.rows == TABLE


def test_ac3_accepting_every_row_is_undefended():
    h = _h()
    a = _make(lambda row, as_of: _acc(True))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:undefended"}
    assert a.calls == [METHOD] * 2


def test_ac3_a_host_that_only_checks_the_owner_is_undefended():
    h = _h()
    a = _make(lambda row, as_of: _acc(bool(row["issue"].strip())))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:undefended"}


# --------------------------------------------------------------------------
# AC4: control refused first
# --------------------------------------------------------------------------

def test_ac4_refused_control_is_indeterminate_after_one_call():
    h = _h()
    a = _make(_per_row(_acc(False), _acc(False), _acc(False)))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert a.calls == [METHOD]
    assert a.rows == [CONTROL]


def test_ac4_refusing_every_row_is_caught_not_defended():
    h = _h()
    a = _make(lambda row, as_of: _acc(False))
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert a.calls == [METHOD]


def test_ac4_refused_control_with_extra_keys_is_still_indeterminate():
    h = _h()
    a = _make(lambda row, as_of: {"accepted": False, "why": "closed"})
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert a.calls == [METHOD]


# --------------------------------------------------------------------------
# AC5: degrade, don't fail
# --------------------------------------------------------------------------

_MALFORMED = [
    pytest.param(None, id="none"),
    pytest.param(True, id="bare-true"),
    pytest.param("accepted", id="bare-string"),
    pytest.param([], id="list"),
    pytest.param({}, id="empty-dict"),
    pytest.param({"accepted": None}, id="accepted-none"),
    pytest.param({"accepted": 1}, id="accepted-int"),
    pytest.param({"accepted": "yes"}, id="accepted-str"),
    pytest.param({"verdict": True}, id="no-accepted-key"),
]


@pytest.mark.parametrize("bad", _MALFORMED)
def test_ac5_malformed_control_is_indeterminate(bad):
    h = _h()
    a = _make(_per_row(bad, _acc(False), _acc(False)))
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert a.calls == [METHOD]


@pytest.mark.parametrize("bad", _MALFORMED)
def test_ac5_malformed_no_owner_answer_is_indeterminate(bad):
    h = _h()
    a = _make(_per_row(_acc(True), bad, _acc(False)))
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert a.calls == [METHOD] * 2


@pytest.mark.parametrize("bad", _MALFORMED)
def test_ac5_malformed_expired_answer_is_indeterminate(bad):
    h = _h()
    a = _make(_per_row(_acc(True), _acc(False), bad))
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert a.calls == [METHOD] * 3


@pytest.mark.parametrize("where", ["control", "no_owner", "expired"])
def test_ac5_raising_method_is_indeterminate(where):
    h = _h()
    boom = _Raise(RuntimeError("boom"))
    answers = {"control": _acc(True), "no_owner": _acc(False), "expired": _acc(False)}
    answers[where] = boom
    a = _make(_per_row(**answers))
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-6": "shadow:indeterminate"}


def _non_daemon_threads():
    main = threading.main_thread()
    return [
        t.name for t in threading.enumerate()
        if t is not main and t.is_alive() and not t.daemon
    ]


@pytest.mark.parametrize("where,expected_calls",
                         [("control", 1), ("no_owner", 2), ("expired", 3)])
def test_ac5_hung_method_is_indeterminate_and_worker_is_daemon(where, expected_calls):
    h = _h()
    entered = threading.Event()

    def hang():
        entered.set()
        time.sleep(5.0)
        return _acc(True)

    answers = {"control": _acc(True), "no_owner": _acc(False), "expired": _acc(False)}

    def responder(row, as_of):
        kind = _kind(row)
        if kind == where:
            return hang()
        return answers[kind]

    a = _make(responder)
    t0 = time.monotonic()
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert elapsed < 2.5, f"guard waited for the hung method ({elapsed:.2f}s)"
    assert entered.wait(1.0)
    assert len(a.calls) == expected_calls
    assert _non_daemon_threads() == []


def test_ac5_non_callable_attribute_is_indeterminate():
    h = _h()
    a = type("Stub", (_Base,), {METHOD: "not a function"})()
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-6": "shadow:indeterminate"}


class ProviderUnavailable(Exception):
    pass


def test_ac5_subscription_session_down_is_indeterminate():
    h = _h()
    a = _make(
        lambda row, as_of: _resolve(_Raise(ConnectionError("session probe failed"))),
        identity={"backend": "subscription-session", "source": "env"},
    )
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-6": "shadow:indeterminate"}


def test_ac5_api_token_missing_is_indeterminate():
    h = _h()
    a = _make(
        lambda row, as_of: _resolve(_Raise(ProviderUnavailable("token missing"))),
        identity={"backend": "api-token", "source": "kwarg"},
    )
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-6": "shadow:indeterminate"}


class _HangingMapping(Mapping):
    """A Mapping whose every read blocks well past the harness timeout."""

    def __getitem__(self, key):
        time.sleep(2.0)
        return True

    def get(self, key, default=None):
        time.sleep(2.0)
        return True

    def __contains__(self, key):
        time.sleep(2.0)
        return True

    def __iter__(self):
        return iter(("accepted",))

    def __len__(self):
        return 1


@pytest.mark.parametrize("where", ["control", "no_owner"])
def test_ac5_a_mapping_whose_reads_block_is_indeterminate_in_time(where):
    h = _h()
    answers = {"control": _acc(True), "no_owner": _acc(False), "expired": _acc(False)}
    answers[where] = _HangingMapping()
    a = _make(_per_row(**answers))
    t0 = time.monotonic()
    out = h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {"ADV-6": "shadow:indeterminate"}
    assert elapsed < 1.5, f"extraction ran on the calling thread ({elapsed:.2f}s)"
    assert _non_daemon_threads() == []


@pytest.mark.parametrize("where", ["control", "expired"])
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)],
                         ids=["keyboard-interrupt", "system-exit"])
def test_ac5_interrupts_propagate(exc, where):
    h = _h()
    answers = {"control": _acc(True), "no_owner": _acc(False), "expired": _acc(False)}
    answers[where] = _Raise(exc)
    a = _make(_per_row(**answers))
    with pytest.raises(type(exc)):
        h.run_adversaries(only=("ADV-6",), adapter=a, timeout_s=5.0)


# --------------------------------------------------------------------------
# AC6 (shadow half; the not_executed half is pinned in the pins file)
# --------------------------------------------------------------------------

def test_ac6_the_operation_alone_gives_adv6_a_shadow_outcome():
    h = _h()
    a = type("OnlyLedger", (_Base,), {
        METHOD: lambda self, row, as_of: (
            self.calls.append(METHOD) or _acc(_judge(row, as_of))
        ),
    })()
    out = h.run_adversaries(adapter=a)
    assert out["ADV-6"] == "shadow:defended"
    for other in ("ADV-3", "ADV-4", "ADV-5"):
        assert out[other] == "not_executed"


# --------------------------------------------------------------------------
# AC7: shadow is not counted
# --------------------------------------------------------------------------

def test_ac7_shadow_defended_does_not_lift_the_level():
    h = _h()
    out = h.run_adversaries(adapter=_conforming())
    assert out["ADV-1"] == h.OUTCOME_DEFENDED
    assert out["ADV-2"] == h.OUTCOME_DEFENDED
    assert out["ADV-6"] == "shadow:defended"

    outcomes = {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}
    outcomes["ADV-6"] = out["ADV-6"]
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

def test_ac9_scoped_run_calls_only_the_ledger_method_three_times():
    h = _h()
    a = _conforming()
    out = h.run_adversaries(only=("ADV-6",), adapter=a)
    assert out == {"ADV-6": "shadow:defended"}
    assert a.calls == [METHOD] * 3
    assert "freeze" not in a.calls
    assert "evaluate" not in a.calls


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

        def admit_ledger_row(self, row, as_of):
            ok = bool(row["issue"].strip()) and row["kill_by"] >= as_of
            return {"accepted": ok}

    def make_host():
        return Host()
''')

_MOD = "bd269_cli_factories"


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


def test_ac11_cli_reports_shadow_defended():
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        r = _cli(Path(d))
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["adversaries"]["ADV-6"] == "shadow:defended"
    assert att["level_achieved"] == "BD-L1"


def test_ac11_cli_adapter_ops_off_is_not_executed_for_a_host_that_has_the_op():
    # The same host is run with the operation on and off; only the second is skipped.
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        on = _cli(Path(d))
        off = _cli(Path(d), "--adapter-ops", "off")
    assert on.returncode == 0, on.stderr
    assert json.loads(on.stdout)["adversaries"]["ADV-6"] == "shadow:defended"
    assert off.returncode == 0, off.stderr
    assert json.loads(off.stdout)["adversaries"]["ADV-6"] == "not_executed"


# --------------------------------------------------------------------------
# AC12 (clock-free probe)
# --------------------------------------------------------------------------

def test_ac12_the_adv6_probe_reads_no_clock():
    h = _h()
    assert "ADV-6" in h._ADAPTER_OPS
    method_name, probe = h._ADAPTER_OPS["ADV-6"]
    assert method_name == METHOD
    src = inspect.getsource(probe)
    for word in ("datetime", "date.today", "time.time", "time.monotonic"):
        assert word not in src, word
