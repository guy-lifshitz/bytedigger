"""bd#197 (group 6) -- count the host adapter operations toward a level, opt-in.

Spec: `docs/decisions/2026-10-15-bd197-adapter-ops-count.md`.

`run_adversaries(adapter=..., adapter_ops="count")` returns an optional
operation's outcome without the `shadow:` prefix, so a `defended` counts and a
host can reach BD-L3. These tests fail today because "count" is not a supported
mode (ValueError; CLI usage error). The default ("shadow") and "off" are pinned
in `test_bd197_adapter_ops_count_pins.py`.

No test here reads the wall clock or the date (the hang rows use time.monotonic()). Stubs subclass nothing from the harness; they only
report raw facts, and wrap the engine's own deterministic checks where a
conforming answer is needed (`stub_passability.lint_red_file`).

`conformance.*` imports are deferred into test bodies. Nothing under
`bytedigger_engine/` is put on sys.path.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent

OP_OF = {
    "ADV-3": "lint_vacuity",
    "ADV-4": "record_oracle_step",
    "ADV-5": "record_gate_step",
    "ADV-6": "admit_ledger_row",
    "ADV-7": "attest_step",
    "ADV-8": "attribute_block",
    "ADV-10": "attest_step",
}
OPTIONAL = tuple(OP_OF)
LEVEL_OF = {
    "ADV-3": "BD-L2", "ADV-4": "BD-L2", "ADV-5": "BD-L2", "ADV-6": "BD-L2",
    "ADV-7": "BD-L3", "ADV-8": "BD-L3", "ADV-10": "BD-L3",
}
BELOW = {"BD-L2": "BD-L1", "BD-L3": "BD-L2"}
LEVELS = ("BD-L0", "BD-L1", "BD-L2", "BD-L3")
OP_NAMES = sorted(set(OP_OF.values()))


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _oracle():
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    return oracle


# --------------------------------------------------------------------------
# Stub adapters (live in the test module; subclass nothing from the harness).
# --------------------------------------------------------------------------

class ProviderUnavailable(Exception):
    pass


class _Base:
    identity = {"backend": "stub-backend", "source": "stub-source"}
    gate_error = None

    def __init__(self):
        self.calls = []

    def _gate(self):
        if self.gate_error is not None:
            raise self.gate_error

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


def _sha(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _op_lint(self, path):
    self.calls.append("lint_vacuity")
    self._gate()
    from bytedigger_engine import stub_passability  # noqa: PLC0415

    return stub_passability.lint_red_file(str(path)).has_violation()


def _op_oracle(self, fault):
    self.calls.append("record_oracle_step")
    self._gate()
    return {"status": "passed"} if fault is None else {"status": "load_failure"}


def _op_gate(self, fault):
    self.calls.append("record_gate_step")
    self._gate()
    return {"status": "passed"} if fault is None else {"status": "failed"}


def _op_ledger(self, row, as_of):
    self.calls.append("admit_ledger_row")
    self._gate()
    return {"accepted": bool(row["issue"].strip()) and row["kill_by"] >= as_of}


def _op_attest(self, fault):
    self.calls.append("attest_step")
    self._gate()
    used = ["Read", "Bash"] + (["Write"] if fault == "tool_escape" else [])
    return {
        "model_requested": "alpha-large-2",
        "model_observed": "beta-small-1" if fault == "model_drift" else "alpha-large-2",
        "tools_declared": ["Read", "Bash(x:*)"],
        "tools_observed": used,
    }


def _op_attribute(self, content):
    self.calls.append("attribute_block")
    self._gate()
    return {"source_id": "host-src-1", "digest": _sha(content)}


GOOD_OPS = {
    "lint_vacuity": _op_lint,
    "record_oracle_step": _op_oracle,
    "record_gate_step": _op_gate,
    "admit_ledger_row": _op_ledger,
    "attest_step": _op_attest,
    "attribute_block": _op_attribute,
}

# Deliberately wrong implementations, keyed by the adversary they must fail.
def _wrong_lint(self, path):
    self.calls.append("lint_vacuity")
    return True


def _wrong_oracle(self, fault):
    self.calls.append("record_oracle_step")
    return {"status": "passed"} if fault is None else {"status": "rejected"}


def _wrong_gate(self, fault):
    self.calls.append("record_gate_step")
    return {"status": "passed"} if fault is None else {"status": "absent"}


def _wrong_ledger(self, row, as_of):
    self.calls.append("admit_ledger_row")
    return {"accepted": True}


def _wrong_model(self, fault):
    self.calls.append("attest_step")
    used = ["Read", "Bash"] + (["Write"] if fault == "tool_escape" else [])
    return {
        "model_requested": "alpha-large-2",
        "model_observed": "alpha-large-2",
        "tools_declared": ["Read", "Bash(x:*)"],
        "tools_observed": used,
    }


def _wrong_tools(self, fault):
    self.calls.append("attest_step")
    return {
        "model_requested": "alpha-large-2",
        "model_observed": "beta-small-1" if fault == "model_drift" else "alpha-large-2",
        "tools_declared": ["Read", "Bash(x:*)"],
        "tools_observed": ["Read", "Bash"],
    }


def _wrong_attribute(self, content):
    self.calls.append("attribute_block")
    return {"source_id": "host-src-1", "digest": _sha(content + "\n")}


WRONG = {
    "ADV-3": ("lint_vacuity", _wrong_lint),
    "ADV-4": ("record_oracle_step", _wrong_oracle),
    "ADV-5": ("record_gate_step", _wrong_gate),
    "ADV-6": ("admit_ledger_row", _wrong_ledger),
    "ADV-7": ("attest_step", _wrong_model),
    "ADV-8": ("attribute_block", _wrong_attribute),
    "ADV-10": ("attest_step", _wrong_tools),
}

BILLING = {
    "subscription-session": (
        {"backend": "session-backend", "source": "session-probe"},
        ConnectionError("session probe failed"),
    ),
    "api-token": (
        {"backend": "token-backend", "source": "token-read"},
        ProviderUnavailable("credential missing"),
    ),
}
BILLINGS = list(BILLING)


def _full(billing="subscription-session", *, replace=None, omit=(), gate_fail=False):
    """A full conforming stub; `replace` swaps attributes, `omit` deletes them."""
    identity, failure = BILLING[billing]
    ns = {"identity": identity}
    ns.update(GOOD_OPS)
    if gate_fail:
        ns["gate_error"] = failure
    for name in omit:
        ns.pop(name, None)
    if replace:
        ns.update(replace)
    return type("Stub", (_Base,), ns)()


def _only(*ops):
    """A stub that implements only the listed operations."""
    return type("Stub", (_Base,), {n: GOOD_OPS[n] for n in ops})()


def _raiser(exc):
    def op(self, *args, **kwargs):
        self.calls.append("raised")
        raise exc

    return op


def _hanger(self, *args, **kwargs):
    time.sleep(5.0)
    return True


def _attestation(h, outcomes, claimed):
    return h.build_attestation(
        outcomes,
        level_claimed=claimed,
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-10-15T00:00:00Z",
    )


def _on_all_defended(h, partial):
    base = {n: h.OUTCOME_DEFENDED for n in h.ADVERSARIES}
    base.update(partial)
    return base


def _non_daemon_threads():
    main = threading.main_thread()
    return [
        t.name for t in threading.enumerate()
        if t is not main and t.is_alive() and not t.daemon
    ]


# --------------------------------------------------------------------------
# AC1 / AC6: count raises the level, for both billing modes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("billing", BILLINGS)
def test_ac1_count_gives_every_adversary_defended_and_bd_l3(billing):
    h = _h()
    out = h.run_adversaries(adapter=_full(billing), adapter_ops="count")
    assert set(out) == set(h.ADVERSARIES)
    assert out == {n: "defended" for n in h.ADVERSARIES}
    att = _attestation(h, out, "BD-L3")
    assert att["level_achieved"] == "BD-L3"
    assert h.validate_attestation(att) == ()


@pytest.mark.parametrize("billing", BILLINGS)
def test_ac1_the_same_stub_in_shadow_tops_out_at_bd_l1(billing):
    h = _h()
    # Count first, so this test is red until "count" exists.
    counted = h.run_adversaries(adapter=_full(billing), adapter_ops="count")
    assert counted["ADV-8"] == "defended"
    out = h.run_adversaries(adapter=_full(billing), adapter_ops="shadow")
    for n in OPTIONAL:
        assert out[n] == "shadow:defended", n
    att = _attestation(h, out, "BD-L2")
    assert att["level_achieved"] == "BD-L1"
    assert any("level_claimed" in c for c in h.validate_attestation(att))


def test_ac6_identity_differs_between_billing_modes_only():
    h = _h()
    a = h.adapter_identity(_full("subscription-session"))
    b = h.adapter_identity(_full("api-token"))
    assert a != b
    counted = [
        h.run_adversaries(adapter=_full(k), adapter_ops="count") for k in BILLINGS
    ]
    assert counted[0] == counted[1]


# --------------------------------------------------------------------------
# AC2: BD-L2 without BD-L3
# --------------------------------------------------------------------------

def test_ac2_only_the_l2_operations_reach_bd_l2_not_bd_l3():
    h = _h()
    a = _only("lint_vacuity", "record_oracle_step", "record_gate_step",
              "admit_ledger_row")
    out = h.run_adversaries(adapter=a, adapter_ops="count")
    for n in ("ADV-3", "ADV-4", "ADV-5", "ADV-6"):
        assert out[n] == "defended", n
    for n in ("ADV-7", "ADV-8", "ADV-10"):
        assert out[n] == "not_executed", n
    att = _attestation(h, out, "BD-L3")
    assert att["level_achieved"] == "BD-L2"
    assert any("level_claimed" in c for c in h.validate_attestation(att))


# --------------------------------------------------------------------------
# AC3: omitted stays absent
# --------------------------------------------------------------------------

def test_ac3_a_stub_with_only_the_required_operations_is_bd_l1_and_uncalled():
    h = _h()
    a = type("Bare", (_Base,), {})()
    out = h.run_adversaries(adapter=a, adapter_ops="count")
    assert out["ADV-1"] == "defended" and out["ADV-2"] == "defended"
    for n in OPTIONAL:
        assert out[n] == "not_executed", n
    assert set(a.calls) <= {"freeze", "evaluate"}
    assert _attestation(h, out, "BD-L1")["level_achieved"] == "BD-L1"


@pytest.mark.parametrize("op, capped", [
    ("lint_vacuity", "BD-L1"),
    ("record_oracle_step", "BD-L1"),
    ("record_gate_step", "BD-L1"),
    ("admit_ledger_row", "BD-L1"),
    ("attest_step", "BD-L2"),
    ("attribute_block", "BD-L2"),
])
@pytest.mark.parametrize("how", ["none", "deleted"])
def test_ac3_an_omitted_operation_is_not_executed_and_caps_the_level(op, capped, how):
    h = _h()
    a = _full(replace={op: None}) if how == "none" else _full(omit=(op,))
    out = h.run_adversaries(adapter=a, adapter_ops="count")
    advs = [n for n in OPTIONAL if OP_OF[n] == op]
    for n in advs:
        assert out[n] == "not_executed", n
    for n in OPTIONAL:
        if n not in advs:
            assert out[n] == "defended", n
    assert op not in a.calls
    assert _attestation(h, out, "BD-L3")["level_achieved"] == capped


# --------------------------------------------------------------------------
# AC4: wrong implementations are not counted (and AC6 over both billing modes)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("billing", BILLINGS)
@pytest.mark.parametrize("adv", OPTIONAL)
def test_ac4_a_wrong_implementation_is_plain_undefended_and_lowers_the_level(
    adv, billing
):
    h = _h()
    method, wrong = WRONG[adv]
    a = _full(billing, replace={method: wrong})
    out = h.run_adversaries(adapter=a, adapter_ops="count")
    assert out[adv] == "undefended", out
    att = _attestation(h, out, "BD-L3")
    assert att["level_achieved"] == BELOW[LEVEL_OF[adv]]
    assert any("level_claimed" in c for c in h.validate_attestation(att))


# --------------------------------------------------------------------------
# AC5: degrade, don't fail
# --------------------------------------------------------------------------

def _outcome_and_level(h, adv, adapter, timeout_s):
    out = h.run_adversaries(only=(adv,), adapter=adapter, adapter_ops="count",
                            timeout_s=timeout_s)
    att = _attestation(h, _on_all_defended(h, out), "BD-L3")
    return out, att


@pytest.mark.parametrize("adv", OPTIONAL)
def test_ac5_a_raise_is_plain_indeterminate(adv):
    h = _h()
    a = _full(replace={OP_OF[adv]: _raiser(RuntimeError("boom"))})
    out, att = _outcome_and_level(h, adv, a, 0.5)
    assert out == {adv: "indeterminate"}
    assert LEVELS.index(att["level_achieved"]) < LEVELS.index(LEVEL_OF[adv])


@pytest.mark.parametrize("adv", OPTIONAL)
def test_ac5_a_hang_is_indeterminate_in_time_with_daemon_workers(adv):
    h = _h()
    a = _full(replace={OP_OF[adv]: _hanger})
    t0 = time.monotonic()
    out, att = _outcome_and_level(h, adv, a, 0.2)
    elapsed = time.monotonic() - t0
    assert out == {adv: "indeterminate"}
    assert elapsed < 2.5, f"guard waited for the hung method ({elapsed:.2f}s)"
    assert _non_daemon_threads() == []
    assert LEVELS.index(att["level_achieved"]) < LEVELS.index(LEVEL_OF[adv])


@pytest.mark.parametrize("adv", OPTIONAL)
def test_ac5_a_malformed_return_is_plain_indeterminate(adv):
    h = _h()
    a = _full(replace={OP_OF[adv]: lambda self, *args, **kw: 42})
    out, att = _outcome_and_level(h, adv, a, 0.5)
    assert out == {adv: "indeterminate"}
    assert LEVELS.index(att["level_achieved"]) < LEVELS.index(LEVEL_OF[adv])


@pytest.mark.parametrize("adv", OPTIONAL)
def test_ac5_a_non_callable_attribute_is_plain_indeterminate(adv):
    h = _h()
    a = _full(replace={OP_OF[adv]: "not a function"})
    out, att = _outcome_and_level(h, adv, a, 0.5)
    assert out == {adv: "indeterminate"}
    assert LEVELS.index(att["level_achieved"]) < LEVELS.index(LEVEL_OF[adv])


@pytest.mark.parametrize("adv", OPTIONAL)
def test_ac5_a_failing_getattr_is_plain_indeterminate(adv):
    h = _h()

    def broken(self):
        raise RuntimeError("attribute read failed")

    a = _full(replace={OP_OF[adv]: property(broken)})
    out, att = _outcome_and_level(h, adv, a, 0.5)
    assert out == {adv: "indeterminate"}
    assert LEVELS.index(att["level_achieved"]) < LEVELS.index(LEVEL_OF[adv])


@pytest.mark.parametrize("billing", BILLINGS)
def test_ac5_a_provider_failure_is_indeterminate_for_every_operation(billing):
    h = _h()
    a = _full(billing, gate_fail=True)
    out = h.run_adversaries(adapter=a, adapter_ops="count", timeout_s=0.5)
    assert out["ADV-1"] == "defended" and out["ADV-2"] == "defended"
    for n in OPTIONAL:
        assert out[n] == "indeterminate", (n, out[n])
    att = _attestation(h, out, "BD-L3")
    assert att["level_achieved"] == "BD-L1"


@pytest.mark.parametrize("adv", OPTIONAL)
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)],
                         ids=["keyboard-interrupt", "system-exit"])
def test_ac5_interrupts_propagate_in_count_mode(adv, exc):
    h = _h()
    a = _full(replace={OP_OF[adv]: _raiser(exc)})
    with pytest.raises(type(exc)):
        h.run_adversaries(only=(adv,), adapter=a, adapter_ops="count",
                          timeout_s=5.0)


# --------------------------------------------------------------------------
# AC9: scoped run
# --------------------------------------------------------------------------

def test_ac9_scoped_adv3_calls_only_lint_twice():
    h = _h()
    a = _full()
    out = h.run_adversaries(only=("ADV-3",), adapter=a, adapter_ops="count")
    assert out == {"ADV-3": "defended"}
    assert a.calls.count("lint_vacuity") == 2
    assert "freeze" not in a.calls and "evaluate" not in a.calls


def test_ac9_scoped_adv7_adv10_call_attest_step_four_times():
    h = _h()
    a = _full()
    out = h.run_adversaries(only=("ADV-7", "ADV-10"), adapter=a,
                            adapter_ops="count")
    assert out == {"ADV-7": "defended", "ADV-10": "defended"}
    assert a.calls.count("attest_step") == 4


# --------------------------------------------------------------------------
# AC10: no interference, in every mode
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["shadow", "count", "off"])
def test_ac10_optional_operations_do_not_change_adv1_and_adv2(mode):
    h = _h()
    # Compare against "count" first so the test is red until it exists.
    h.run_adversaries(only=("ADV-1",), adapter=_full(), adapter_ops="count")
    names = ("ADV-1", "ADV-2")
    with_ops = h.run_adversaries(only=names, adapter=_full(), adapter_ops=mode)
    without = h.run_adversaries(only=names, adapter=_only(), adapter_ops=mode)
    assert with_ops == without == {"ADV-1": "defended", "ADV-2": "defended"}


def test_ac10_adapter_none_ignores_the_mode():
    h = _h()
    nine = {n: "defended" for n in h.ADVERSARIES}
    results = [h.run_adversaries(adapter=None, adapter_ops=m)
               for m in ("shadow", "count", "off")]
    assert results == [nine, nine, nine]


# --------------------------------------------------------------------------
# AC11: CLI end-to-end (subprocess)
# --------------------------------------------------------------------------

_FACTORY_SRC = textwrap.dedent('''
    import hashlib
    from pathlib import Path
    from bytedigger_engine import stub_passability
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

        def lint_vacuity(self, path):
            return stub_passability.lint_red_file(str(path)).has_violation()

        def record_oracle_step(self, fault):
            if fault is None:
                return {"status": "passed"}
            return {"status": "load_failure"}

        def record_gate_step(self, fault):
            return {"status": "passed"} if fault is None else {"status": "failed"}

        def admit_ledger_row(self, row, as_of):
            ok = bool(row["issue"].strip()) and row["kill_by"] >= as_of
            return {"accepted": ok}

        def attest_step(self, fault):
            used = ["Read", "Bash"] + (["Write"] if fault == "tool_escape" else [])
            return {
                "model_requested": "alpha-large-2",
                "model_observed": (
                    "beta-small-1" if fault == "model_drift" else "alpha-large-2"
                ),
                "tools_declared": ["Read", "Bash(x:*)"],
                "tools_observed": used,
            }

        def attribute_block(self, content):
            return {
                "source_id": "cli-src-1",
                "digest": "sha256:" + hashlib.sha256(
                    content.encode("utf-8")).hexdigest(),
            }

    class WrongHost(Host):
        def lint_vacuity(self, path):
            return True

    def make_host():
        return Host()

    def make_wrong_host():
        return WrongHost()
''')

_MOD = "bd197_count_cli_factories"


def _cli(tmp_path, factory, claimed, *extra):
    (tmp_path / f"{_MOD}.py").write_text(_FACTORY_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_PY) + os.pathsep + str(tmp_path)
    cmd = [
        sys.executable, "-m", "bytedigger_engine.conformance.harness",
        "--adapter", f"{_MOD}:{factory}",
        "--level-claimed", claimed,
        "--engine-version", "0.0.0-test",
        "--host-identity", "test-host",
        "--timeout-s", "5",
        *extra,
    ]
    return subprocess.run(
        cmd, cwd=str(ENGINE_PY), env=env, capture_output=True, text=True, timeout=120
    )


def test_ac11_cli_count_full_stub_reaches_bd_l3(tmp_path):
    r = _cli(tmp_path, "make_host", "BD-L3", "--adapter-ops", "count")
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert set(att) == {"level_claimed", "level_achieved", "adversaries",
                        "engine_version", "adapter_identity", "host_identity",
                        "timestamp"}
    assert att["level_achieved"] == "BD-L3"
    for name, outcome in att["adversaries"].items():
        expected = "not_executed" if name == "ADV-9" else "defended"
        assert outcome == expected, (name, outcome)


def test_ac11_cli_count_wrong_stub_exits_1_with_a_level_claimed_complaint(tmp_path):
    r = _cli(tmp_path, "make_wrong_host", "BD-L3", "--adapter-ops", "count")
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "level_claimed" in r.stderr
    att = json.loads(r.stdout)
    assert att["adversaries"]["ADV-3"] == "undefended"
    assert att["level_achieved"] == "BD-L1"
