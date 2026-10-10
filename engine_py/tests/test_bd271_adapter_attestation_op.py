"""bd#271 (group 4) -- a host operation for ADV-7 and ADV-10 (attestation), in shadow.

Spec: `docs/decisions/2026-10-10-bd271-adapter-attestation-op.md`.

Under an adapter, ADV-7 (model family) and ADV-10 (tool set) used to be
`not_executed`. The adapter may now offer `attest_step(fault)`, returning a
mapping of raw facts (`model_requested`, `model_observed`, `tools_declared`,
`tools_observed`). The harness calls it with `None` (control) and then with
`"model_drift"` (ADV-7) or `"tool_escape"` (ADV-10), judges the facts itself,
and files the result as `shadow:<outcome>`, which never counts toward a level.
`adapter_ops="off"` restores the old ceiling.

No test here reads the clock. Stubs judge nothing: they only report facts.

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

METHOD = "attest_step"
DRIFT = "model_drift"
ESCAPE = "tool_escape"


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
        self.faults = []

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


def _make(responder, identity=None, extra=None):
    """A stub with `attest_step(fault)` that records the fault it is handed."""

    def op(self, fault):
        self.calls.append(METHOD)
        self.faults.append(fault)
        return responder(fault)

    ns = {METHOD: op}
    if identity is not None:
        ns["identity"] = identity
    if extra:
        ns.update(extra)
    return type("Stub", (_Base,), ns)()


def _by_fault(control, drift, escape):
    """Answers `control`, `drift` or `escape` depending on the fault it got."""
    table = {None: control, DRIFT: drift, ESCAPE: escape}
    return lambda fault: _resolve(table[fault])


def _facts(fault, *, drift=None, escape=None,
           declared=("Read", "Bash(x:*)"), used=("Read", "Bash"),
           requested="alpha-large-2", observed=None, escape_tool="Write"):
    """The facts a truthful host reports. `drift` / `escape` default to the fault."""
    if drift is None:
        drift = fault == DRIFT
    if escape is None:
        escape = fault == ESCAPE
    if observed is None:
        observed = "beta-small-1" if drift else requested
    tools = list(used) + ([escape_tool] if escape else [])
    return {
        "model_requested": requested,
        "model_observed": observed,
        "tools_declared": list(declared),
        "tools_observed": tools,
    }


def _conforming_responder(fault):
    return _facts(fault)


def _conforming():
    return _make(_conforming_responder)


ADV_FAULT = [
    pytest.param("ADV-7", DRIFT, id="adv7"),
    pytest.param("ADV-10", ESCAPE, id="adv10"),
]


# --------------------------------------------------------------------------
# AC1: the operation is called, not the engine probe
# --------------------------------------------------------------------------

@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac1_host_answer_decides_not_the_engine_probe(adv, fault):
    h = _h()
    assert h.run_adversaries(only=(adv,)) == {adv: "defended"}
    a = _make(lambda f: _facts(None))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:undefended"}
    assert a.calls.count(METHOD) >= 1


# --------------------------------------------------------------------------
# AC2: conforming host
# --------------------------------------------------------------------------

@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac2_conforming_is_shadow_defended_called_twice_in_order(adv, fault):
    h = _h()
    a = _conforming()
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:defended"}
    assert a.calls == [METHOD] * 2
    assert a.faults == [None, fault]


def test_ac2_full_run_calls_the_method_four_times_in_order():
    h = _h()
    a = _conforming()
    out = h.run_adversaries(adapter=a)
    assert out["ADV-7"] == "shadow:defended"
    assert out["ADV-10"] == "shadow:defended"
    assert a.faults == [None, DRIFT, None, ESCAPE]
    assert a.calls.count(METHOD) == 4


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


def _wrap_dict(f):
    return dict(f)


def _wrap_extra(f):
    return {**f, "detail": "x", "n": 3}


def _wrap_proxy(f):
    return types.MappingProxyType({**f, "extra": 1})


def _wrap_opaque(f):
    return _OpaqueMapping(f)


def _wrap_tuples(f):
    return {**f, "tools_declared": tuple(f["tools_declared"]),
            "tools_observed": tuple(f["tools_observed"])}


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
@pytest.mark.parametrize(
    "wrap", [_wrap_dict, _wrap_extra, _wrap_proxy, _wrap_opaque, _wrap_tuples],
    ids=["dict", "extra-keys", "mappingproxy", "non-dict-mapping", "tuples"],
)
def test_ac2_extra_keys_non_dict_mappings_and_tuples_are_accepted(adv, fault, wrap):
    h = _h()
    a = _make(lambda f: wrap(_facts(f)))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:defended"}
    assert a.faults == [None, fault]


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac2_empty_tool_sets_are_well_formed(adv, fault):
    h = _h()
    a = _make(lambda f: _facts(f, declared=(), used=()))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:defended"}
    assert a.faults == [None, fault]


# --------------------------------------------------------------------------
# AC3: wrong implementations fail
# --------------------------------------------------------------------------

def test_ac3_observed_equal_to_requested_is_undefended_after_two_calls():
    h = _h()
    a = _make(lambda f: _facts(f, drift=False))
    out = h.run_adversaries(only=("ADV-7",), adapter=a)
    assert out == {"ADV-7": "shadow:undefended"}
    assert a.faults == [None, DRIFT]


@pytest.mark.parametrize("observed", [
    "alpha-small-1", "alpha-large-3", "ALPHA-large-2", " alpha_x",
])
def test_ac3_a_same_family_observed_id_is_undefended(observed):
    h = _h()
    a = _make(_by_fault(_facts(None), _facts(None, observed=observed), _facts(None)))
    out = h.run_adversaries(only=("ADV-7",), adapter=a)
    assert out == {"ADV-7": "shadow:undefended"}
    assert a.faults == [None, DRIFT]


def test_ac3_observed_tools_equal_to_declared_is_undefended():
    h = _h()
    a = _make(lambda f: _facts(f, escape=False))
    out = h.run_adversaries(only=("ADV-10",), adapter=a)
    assert out == {"ADV-10": "shadow:undefended"}
    assert a.faults == [None, ESCAPE]


def test_ac3_a_tool_differing_only_by_case_is_an_escape():
    h = _h()
    a = _make(_by_fault(
        _facts(None), _facts(None), _facts(None, used=("Read", "bash")),
    ))
    out = h.run_adversaries(only=("ADV-10",), adapter=a)
    assert out == {"ADV-10": "shadow:defended"}
    assert a.faults == [None, ESCAPE]


def test_ac3_a_tool_whose_head_matches_a_declared_entry_is_not_an_escape():
    h = _h()
    a = _make(_by_fault(
        _facts(None), _facts(None), _facts(None, used=("Read", "Bash")),
    ))
    out = h.run_adversaries(only=("ADV-10",), adapter=a)
    assert out == {"ADV-10": "shadow:undefended"}
    assert a.faults == [None, ESCAPE]


def test_ac3_the_two_adversaries_are_judged_independently():
    h = _h()
    right_models = _make(lambda f: _facts(f, escape=False))
    out = h.run_adversaries(adapter=right_models)
    assert out["ADV-7"] == "shadow:defended"
    assert out["ADV-10"] == "shadow:undefended"
    assert right_models.faults == [None, DRIFT, None, ESCAPE]

    right_tools = _make(lambda f: _facts(f, drift=False))
    out = h.run_adversaries(adapter=right_tools)
    assert out["ADV-7"] == "shadow:undefended"
    assert out["ADV-10"] == "shadow:defended"
    assert right_tools.faults == [None, DRIFT, None, ESCAPE]


# --------------------------------------------------------------------------
# AC4: control first
# --------------------------------------------------------------------------

@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac4_a_mismatch_on_the_control_is_indeterminate_after_one_call(adv, fault):
    h = _h()
    bad = _facts(None, drift=(adv == "ADV-7"), escape=(adv == "ADV-10"))
    a = _make(lambda f: bad if f is None else _facts(f))
    out = h.run_adversaries(only=(adv,), adapter=a)
    assert out == {adv: "shadow:indeterminate"}
    assert a.faults == [None]


def test_ac4_a_mismatch_for_every_step_is_caught_not_defended():
    h = _h()
    a = _make(lambda f: _facts(f, drift=True, escape=True))
    out = h.run_adversaries(adapter=a)
    assert out["ADV-7"] == "shadow:indeterminate"
    assert out["ADV-10"] == "shadow:indeterminate"
    assert a.faults == [None, None]


def test_ac4_a_control_mismatch_for_one_adversary_leaves_the_other_alone():
    h = _h()
    a = _make(lambda f: _facts(f, drift=True if f is None else None))
    out = h.run_adversaries(adapter=a)
    assert out["ADV-7"] == "shadow:indeterminate"
    assert out["ADV-10"] == "shadow:defended"
    assert a.faults == [None, None, ESCAPE]


def test_ac4_a_same_family_other_tier_control_proceeds_to_the_fault_call():
    h = _h()
    a = _make(lambda f: _facts(f, observed="alpha-small-1") if f is None
              else _facts(f))
    out = h.run_adversaries(only=("ADV-7",), adapter=a)
    assert out == {"ADV-7": "shadow:defended"}
    assert a.faults == [None, DRIFT]


def test_ac4_a_control_tool_matching_a_declared_head_proceeds_to_the_fault_call():
    h = _h()
    a = _make(lambda f: _facts(f, used=("Bash",)))
    out = h.run_adversaries(only=("ADV-10",), adapter=a)
    assert out == {"ADV-10": "shadow:defended"}
    assert a.faults == [None, ESCAPE]


# --------------------------------------------------------------------------
# AC5: degrade, don't fail
# --------------------------------------------------------------------------

class _StrSub(str):
    pass


def _set(key, value):
    return lambda f: {**f, key: value}


def _drop(*keys):
    return lambda f: {k: v for k, v in f.items() if k not in keys}


def _sub(key):
    return lambda f: {**f, key: _StrSub(f[key])}


def _sub_item(key):
    return lambda f: {**f, key: [_StrSub("Read"), *f[key][1:]]}


_WHOLE = {
    "none": lambda f: None,
    "bare-true": lambda f: True,
    "bare-string": lambda f: "facts",
    "list": lambda f: [],
    "empty-dict": lambda f: {},
}

_MALFORMED = {
    "ADV-7": {
        **_WHOLE,
        "observed-none": _set("model_observed", None),
        "observed-empty": _set("model_observed", ""),
        "observed-int": _set("model_observed", 7),
        "observed-str-subclass": _sub("model_observed"),
        "observed-missing": _drop("model_observed"),
        "observed-no-family-digit": _set("model_observed", "4o-mini"),
        "observed-no-family-dash": _set("model_observed", "-x"),
        "requested-none": _set("model_requested", None),
        "requested-empty": _set("model_requested", ""),
        "requested-int": _set("model_requested", 7),
        "requested-str-subclass": _sub("model_requested"),
        "requested-missing": _drop("model_requested"),
        "requested-no-family-digit": _set("model_requested", "4o-mini"),
        "requested-no-family-dash": _set("model_requested", "-x"),
    },
    "ADV-10": {
        **_WHOLE,
        "declared-str": _set("tools_declared", "Read"),
        "declared-set": _set("tools_declared", {"Read"}),
        "declared-none": _set("tools_declared", None),
        "declared-missing": _drop("tools_declared"),
        "declared-non-str-item": _set("tools_declared", ["Read", 5]),
        "declared-str-subclass-item": _sub_item("tools_declared"),
        "observed-str": _set("tools_observed", "Read"),
        "observed-set": _set("tools_observed", {"Read"}),
        "observed-none": _set("tools_observed", None),
        "observed-missing": _drop("tools_observed"),
        "observed-non-str-item": _set("tools_observed", ["Read", 5]),
        "observed-str-subclass-item": _sub_item("tools_observed"),
    },
}

_CASES = [
    pytest.param(adv, fault, mut, id=f"{adv}-{name}")
    for adv, fault in (("ADV-7", DRIFT), ("ADV-10", ESCAPE))
    for name, mut in _MALFORMED[adv].items()
]


@pytest.mark.parametrize("adv,fault,mut", _CASES)
def test_ac5_malformed_control_is_indeterminate(adv, fault, mut):
    h = _h()
    a = _make(lambda f: mut(_facts(f)) if f is None else _facts(f))
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}
    assert a.faults == [None]


@pytest.mark.parametrize("adv,fault,mut", _CASES)
def test_ac5_malformed_fault_answer_is_indeterminate(adv, fault, mut):
    h = _h()
    a = _make(lambda f: mut(_facts(f)) if f is not None else _facts(f))
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}
    assert a.faults == [None, fault]


def test_ac5_another_adversarys_keys_do_not_matter():
    h = _h()
    no_tools = _make(lambda f: _drop("tools_declared", "tools_observed")(_facts(f)))
    out = h.run_adversaries(only=("ADV-7",), adapter=no_tools, timeout_s=0.5)
    assert out == {"ADV-7": "shadow:defended"}

    no_models = _make(lambda f: _drop("model_requested", "model_observed")(_facts(f)))
    out = h.run_adversaries(only=("ADV-10",), adapter=no_models, timeout_s=0.5)
    assert out == {"ADV-10": "shadow:defended"}


def test_ac5_a_malformed_answer_for_one_adversary_leaves_the_other_alone():
    h = _h()
    a = _make(lambda f: _drop("model_requested", "model_observed")(_facts(f)))
    out = h.run_adversaries(adapter=a, timeout_s=0.5)
    assert out["ADV-7"] == "shadow:indeterminate"
    assert out["ADV-10"] == "shadow:defended"
    assert a.faults == [None, None, ESCAPE]

    b = _make(lambda f: _drop("tools_declared", "tools_observed")(_facts(f)))
    out = h.run_adversaries(adapter=b, timeout_s=0.5)
    assert out["ADV-7"] == "shadow:defended"
    assert out["ADV-10"] == "shadow:indeterminate"
    assert b.faults == [None, DRIFT, None]


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
@pytest.mark.parametrize("where", ["control", "fault"])
def test_ac5_raising_method_is_indeterminate(adv, fault, where):
    h = _h()
    boom = _Raise(RuntimeError("boom"))
    good = _facts(None)
    a = _make(_by_fault(
        boom if where == "control" else good,
        boom if where == "fault" else _facts(None, drift=True),
        boom if where == "fault" else _facts(None, escape=True),
    ))
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}
    assert a.faults == ([None] if where == "control" else [None, fault])


def _non_daemon_threads():
    main = threading.main_thread()
    return [
        t.name for t in threading.enumerate()
        if t is not main and t.is_alive() and not t.daemon
    ]


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
@pytest.mark.parametrize("where,expected_calls", [("control", 1), ("fault", 2)])
def test_ac5_hung_method_is_indeterminate_and_worker_is_daemon(
    adv, fault, where, expected_calls
):
    h = _h()
    entered = threading.Event()

    def responder(f):
        if (where == "control") == (f is None):
            entered.set()
            time.sleep(5.0)
        return _facts(f)

    a = _make(responder)
    t0 = time.monotonic()
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {adv: "shadow:indeterminate"}
    assert elapsed < 2.5, f"guard waited for the hung method ({elapsed:.2f}s)"
    assert entered.wait(1.0)
    assert len(a.calls) == expected_calls
    assert _non_daemon_threads() == []


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac5_non_callable_attribute_is_indeterminate(adv, fault):
    h = _h()
    a = type("Stub", (_Base,), {METHOD: "not a function"})()
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


class ProviderUnavailable(Exception):
    pass


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac5_session_down_is_indeterminate(adv, fault):
    h = _h()
    a = _make(
        lambda f: _resolve(_Raise(ConnectionError("session probe failed"))),
        identity={"backend": "session-backend", "source": "env"},
    )
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac5_missing_credential_is_indeterminate(adv, fault):
    h = _h()
    a = _make(
        lambda f: _resolve(_Raise(ProviderUnavailable("credential missing"))),
        identity={"backend": "token-backend", "source": "kwarg"},
    )
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.5)
    assert out == {adv: "shadow:indeterminate"}


class _HangingMapping(Mapping):
    """A Mapping whose every read blocks well past the harness timeout."""

    def __getitem__(self, key):
        time.sleep(2.0)
        return "x"

    def get(self, key, default=None):
        time.sleep(2.0)
        return "x"

    def __contains__(self, key):
        time.sleep(2.0)
        return True

    def __iter__(self):
        return iter(("model_requested", "model_observed",
                     "tools_declared", "tools_observed"))

    def __len__(self):
        return 4


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
@pytest.mark.parametrize("where", ["control", "fault"])
def test_ac5_a_mapping_whose_reads_block_is_indeterminate_in_time(adv, fault, where):
    h = _h()
    hang = _HangingMapping()
    a = _make(_by_fault(
        hang if where == "control" else _facts(None),
        hang if where == "fault" else _facts(None, drift=True),
        hang if where == "fault" else _facts(None, escape=True),
    ))
    t0 = time.monotonic()
    out = h.run_adversaries(only=(adv,), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {adv: "shadow:indeterminate"}
    assert elapsed < 1.5, f"extraction ran on the calling thread ({elapsed:.2f}s)"
    assert _non_daemon_threads() == []


class _HangingList(list):
    """A list whose iteration blocks well past the harness timeout."""

    def __iter__(self):
        time.sleep(2.0)
        return super().__iter__()


@pytest.mark.parametrize("key", ["tools_declared", "tools_observed"])
@pytest.mark.parametrize("where", ["control", "fault"])
def test_ac5_a_tool_sequence_whose_iteration_blocks_is_indeterminate_in_time(
    key, where
):
    h = _h()

    def responder(f):
        facts = _facts(f)
        if (where == "control") == (f is None):
            facts[key] = _HangingList(facts[key])
        return facts

    a = _make(responder)
    t0 = time.monotonic()
    out = h.run_adversaries(only=("ADV-10",), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {"ADV-10": "shadow:indeterminate"}
    assert elapsed < 1.5, f"extraction ran on the calling thread ({elapsed:.2f}s)"
    assert _non_daemon_threads() == []


@pytest.mark.parametrize("adv,fault", ADV_FAULT)
@pytest.mark.parametrize("where", ["control", "fault"])
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)],
                         ids=["keyboard-interrupt", "system-exit"])
def test_ac5_interrupts_propagate(adv, fault, where, exc):
    h = _h()
    boom = _Raise(exc)
    a = _make(_by_fault(
        boom if where == "control" else _facts(None),
        boom if where == "fault" else _facts(None, drift=True),
        boom if where == "fault" else _facts(None, escape=True),
    ))
    with pytest.raises(type(exc)):
        h.run_adversaries(only=(adv,), adapter=a, timeout_s=5.0)


# --------------------------------------------------------------------------
# AC6 (shadow half; the not_executed half is pinned in the pins file)
# --------------------------------------------------------------------------

def test_ac6_the_operation_alone_gives_adv7_and_adv10_a_shadow_outcome():
    h = _h()
    a = type("OnlyAttest", (_Base,), {
        METHOD: lambda self, fault: (
            self.calls.append(METHOD) or self.faults.append(fault)
            or _facts(fault)
        ),
    })()
    out = h.run_adversaries(adapter=a)
    assert out["ADV-7"] == "shadow:defended"
    assert out["ADV-10"] == "shadow:defended"
    for other in ("ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-8"):
        assert out[other] == "not_executed"


# --------------------------------------------------------------------------
# AC7: shadow is not counted
# --------------------------------------------------------------------------

def test_ac7_shadow_defended_does_not_lift_the_level():
    h = _h()
    out = h.run_adversaries(adapter=_conforming())
    assert out["ADV-1"] == h.OUTCOME_DEFENDED
    assert out["ADV-2"] == h.OUTCOME_DEFENDED
    assert out["ADV-7"] == "shadow:defended"
    assert out["ADV-10"] == "shadow:defended"

    outcomes = {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}
    outcomes["ADV-7"] = out["ADV-7"]
    outcomes["ADV-10"] = out["ADV-10"]
    att = h.build_attestation(
        outcomes,
        level_claimed="BD-L3",
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-10-10T00:00:00Z",
    )
    assert att["level_achieved"] == "BD-L2"
    complaints = h.validate_attestation(att)
    assert any("level_claimed" in c for c in complaints), complaints


# --------------------------------------------------------------------------
# AC9: the family rule
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model_id,family", [
    ("alpha-large-2", "alpha"),
    ("Alpha-small-1", "alpha"),
    (" ALPHA_x", "alpha"),
    ("alpha", "alpha"),
    ("alpha2", "alpha"),
    ("beta.9", "beta"),
    ("4o-mini", None),
    ("-alpha", None),
    ("", None),
    ("   ", None),
    ("Ωmega-1", None),
    ("1alpha", None),
])
def test_ac9_model_family_of(model_id, family):
    h = _h()
    assert h._model_family_of(model_id) == family
    assert h._model_family_of(model_id) == family


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

        def attest_step(self, fault):
            used = ["Read", "Bash"]
            if fault == "tool_escape":
                used.append("Write")
            return {
                "model_requested": "alpha-large-2",
                "model_observed": (
                    "beta-small-1" if fault == "model_drift" else "alpha-large-2"
                ),
                "tools_declared": ["Read", "Bash(x:*)"],
                "tools_observed": used,
            }

    def make_host():
        return Host()
''')

_MOD = "bd271_cli_factories"


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
    assert att["adversaries"]["ADV-7"] == "shadow:defended"
    assert att["adversaries"]["ADV-10"] == "shadow:defended"
    assert att["level_achieved"] == "BD-L1"


def test_ac11_cli_adapter_ops_off_is_not_executed_for_a_host_that_has_the_op():
    # The same host is run with the operation on and off; only the second is skipped.
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        on = _cli(Path(d))
        off = _cli(Path(d), "--adapter-ops", "off")
    assert on.returncode == 0, on.stderr
    on_adv = json.loads(on.stdout)["adversaries"]
    assert on_adv["ADV-7"] == on_adv["ADV-10"] == "shadow:defended"
    assert off.returncode == 0, off.stderr
    off_adv = json.loads(off.stdout)["adversaries"]
    assert off_adv["ADV-7"] == off_adv["ADV-10"] == "not_executed"


# --------------------------------------------------------------------------
# AC12 (clock-free, file-free, provider-free probes)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("adv,fault", ADV_FAULT)
def test_ac12_the_probes_read_no_clock_no_file_and_name_no_provider(adv, fault):
    h = _h()
    assert adv in h._ADAPTER_OPS
    method_name, probe = h._ADAPTER_OPS[adv]
    assert method_name == METHOD
    src = inspect.getsource(probe)
    for word in ("datetime", "date.today", "time.time", "time.monotonic",
                 "open(", "tempfile"):
        assert word not in src, word
    for word in ("anthropic", "claude", "openai", "subscription", "api-token",
                 "api_key"):
        assert word not in src.lower(), word


def test_ac12_each_adversary_has_its_own_probe():
    h = _h()
    assert h._ADAPTER_OPS["ADV-7"][1] is not h._ADAPTER_OPS["ADV-10"][1]
