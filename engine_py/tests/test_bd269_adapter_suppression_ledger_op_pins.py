"""bd#269 (group 3) -- pins for the ADV-6 suppression-ledger adapter operation.

These pin behaviour that must hold both before and after GREEN (omitted op stays
`not_executed`, kill switch, no interference with ADV-1..ADV-5, CLI usage error,
pinned harness surface). They pass today by design; the failing RED tests live in
`test_bd269_adapter_suppression_ledger_op.py`.

`conformance.*` imports are deferred into test bodies. Nothing under
`bytedigger_engine/` is put on sys.path.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _oracle():
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    return oracle


class _Base:
    identity = {"backend": "stub-backend", "source": "stub-source"}

    def __init__(self):
        self.calls = []

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


class _Ledger:
    def admit_ledger_row(self, row, as_of):
        self.calls.append("admit_ledger_row")
        ok = bool(row["issue"].strip()) and row["kill_by"] >= as_of
        return {"accepted": ok}


class _Steps:
    def record_oracle_step(self, fault):
        self.calls.append("record_oracle_step")
        return {"status": "passed"} if fault is None else {"status": "load_failure"}

    def record_gate_step(self, fault):
        self.calls.append("record_gate_step")
        return {"status": "passed"} if fault is None else {"status": "failed"}


class _Lint:
    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        from bytedigger_engine import stub_passability  # noqa: PLC0415

        return stub_passability.lint_red_file(str(path)).has_violation()


class NoOps(_Base):
    pass


class NoneOps(_Base):
    admit_ledger_row = None


class Conforming(_Ledger, _Base):
    pass


class OnlyLedger(_Ledger, _Base):
    pass


class AllButLedger(_Steps, _Lint, _Base):
    pass


class DigestWithAll(_Ledger, _Steps, _Lint, _Base):
    pass


class DigestWithoutLedger(_Steps, _Lint, _Base):
    pass


class AcceptAll(_Base):
    def evaluate(self, state):
        self.calls.append("evaluate")
        return _oracle().OracleOutcome.ACCEPTED


class AcceptAllWithAll(_Ledger, _Steps, _Lint, AcceptAll):
    pass


class AcceptAllWithoutLedger(_Steps, _Lint, AcceptAll):
    pass


# --------------------------------------------------------------------------
# AC6: omitted stays not_executed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [NoOps, NoneOps])
def test_ac6_omitted_operation_is_not_executed_and_nothing_called(cls):
    h = _h()
    a = cls()
    assert h.run_adversaries(only=("ADV-6",), adapter=a) == {"ADV-6": "not_executed"}
    assert a.calls == []

    full = h.run_adversaries(adapter=cls())
    assert set(full) == set(h.ADVERSARIES)
    assert len(full) == 9
    assert full["ADV-1"] == h.OUTCOME_DEFENDED
    assert full["ADV-2"] == h.OUTCOME_DEFENDED
    assert full["ADV-6"] == "not_executed"


def test_ac6_an_adapter_with_the_other_operations_but_not_this_one():
    h = _h()
    a = AllButLedger()
    out = h.run_adversaries(adapter=a)
    assert out["ADV-6"] == "not_executed"
    assert out["ADV-3"].startswith("shadow:")
    assert out["ADV-4"].startswith("shadow:")
    assert out["ADV-5"].startswith("shadow:")
    assert "admit_ledger_row" not in a.calls


def test_ac6_the_ledger_operation_alone_leaves_the_others_not_executed():
    h = _h()
    out = h.run_adversaries(adapter=OnlyLedger())
    for other in ("ADV-3", "ADV-4", "ADV-5"):
        assert out[other] == "not_executed"


# --------------------------------------------------------------------------
# AC8: kill switch
# --------------------------------------------------------------------------

def test_ac8_adapter_ops_off_never_calls_the_method():
    h = _h()
    on = h.run_adversaries(adapter=Conforming(), adapter_ops="shadow")
    a = Conforming()
    off = h.run_adversaries(adapter=a, adapter_ops="off")
    assert "admit_ledger_row" not in a.calls
    assert off["ADV-6"] == "not_executed"
    assert off["ADV-1"] == on["ADV-1"] == h.OUTCOME_DEFENDED
    assert off["ADV-2"] == on["ADV-2"] == h.OUTCOME_DEFENDED


@pytest.mark.parametrize("bad", ["enforce", "", "SHADOW", None, True])
def test_ac8_unknown_adapter_ops_value_raises(bad):
    h = _h()
    with pytest.raises(ValueError):
        h.run_adversaries(adapter=Conforming(), adapter_ops=bad)


def test_ac8_adapter_none_ignores_adapter_ops():
    h = _h()
    nine = {n: "defended" for n in h.ADVERSARIES}
    assert h.run_adversaries(adapter=None, adapter_ops="off") == nine
    assert h.run_adversaries(adapter=None, adapter_ops="shadow") == nine


# --------------------------------------------------------------------------
# AC10: no interference
# --------------------------------------------------------------------------

@pytest.mark.parametrize("with_ops,without_ops", [
    (DigestWithAll, DigestWithoutLedger),
    (AcceptAllWithAll, AcceptAllWithoutLedger),
])
def test_ac10_the_new_method_does_not_change_adv1_to_adv5(with_ops, without_ops):
    h = _h()
    names = ("ADV-1", "ADV-2", "ADV-3", "ADV-4", "ADV-5")
    base = h.run_adversaries(only=names, adapter=without_ops())
    got = h.run_adversaries(only=names, adapter=with_ops())
    assert got == base
    for n in ("ADV-3", "ADV-4", "ADV-5"):
        assert got[n].startswith("shadow:")


# --------------------------------------------------------------------------
# CLI usage error
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

_MOD = "bd269_cli_factories_pins"


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


def test_cli_adapter_ops_invalid_is_usage_error(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "bogus")
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stdout == ""
    assert "adapter-ops" in r.stderr


# --------------------------------------------------------------------------
# AC12: pinned surface
# --------------------------------------------------------------------------

def test_ac12_surface_unchanged():
    h = _h()
    assert set(h.__all__) == {
        "ADVERSARIES", "OUTCOME_DEFENDED", "OUTCOME_UNDEFENDED", "OUTCOME_ERRORED",
        "OUTCOME_INDETERMINATE", "AdapterState", "adapter_identity",
        "run_adversaries", "build_attestation", "validate_attestation",
    }
    assert len(h.__all__) == len(set(h.__all__)) == 10


def test_ac12_harness_source_names_no_provider():
    h = _h()
    src = Path(h.__file__).read_text(encoding="utf-8").lower()
    for bad in ("anthropic", "claude", "openai", "subscription", "api-token", "api_key"):
        assert bad not in src, bad


def test_ac12_no_adapter_run_is_unchanged():
    h = _h()
    nine = {n: "defended" for n in
            ("ADV-1", "ADV-2", "ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-7",
             "ADV-8", "ADV-10")}
    assert h.run_adversaries() == nine
    assert h.run_adversaries(adapter=None) == nine
