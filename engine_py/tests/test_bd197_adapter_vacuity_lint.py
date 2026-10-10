"""bd#197 (group 1) -- a host vacuity-lint operation for ADV-3, reported in shadow.

Spec: `docs/decisions/2026-10-10-bd197-adapter-vacuity-lint-op.md`.

Under an adapter, ADV-3 used to be `not_executed`. The adapter may now offer an
optional `lint_vacuity(path) -> bool`; the harness calls it on a control file
and on a vacuous file, and files the result as `shadow:<outcome>`, which never
counts toward a level. `adapter_ops="off"` restores the old ceiling.

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
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent


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


class NoLint(_Base):
    pass


class NoneLint(_Base):
    lint_vacuity = None


class Conforming(_Base):
    """Wraps the engine's real lint; records what it was handed."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        p = Path(path)
        self.seen.append((p, p.exists(), p.suffix, p.read_text(encoding="utf-8")))
        from bytedigger_engine import stub_passability  # noqa: PLC0415

        return stub_passability.lint_red_file(str(path)).has_violation()


class _Fixed(_Base):
    answer = False

    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        return self.answer


class AlwaysTrue(_Fixed):
    answer = True


class AlwaysFalse(_Fixed):
    answer = False


class NaivePatchLint(_Base):
    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        return "patch(" in Path(path).read_text(encoding="utf-8")


class LintRaises(_Base):
    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        raise RuntimeError("lint boom")


class LintNone(_Fixed):
    answer = None


class LintYesStr(_Fixed):
    answer = "yes"


class LintOne(_Fixed):
    answer = 1


class LintSleeps(_Base):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()

    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        self.entered.set()
        time.sleep(5.0)
        return True


class LintNotCallable(_Base):
    lint_vacuity = "not a function"


class SubscriptionUnavailable(_Base):
    identity = {"backend": "subscription-session", "source": "env"}

    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        raise ConnectionError("session probe failed")


class ProviderUnavailable(Exception):
    pass


class ApiTokenUnavailable(_Base):
    identity = {"backend": "api-token", "source": "kwarg"}

    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        raise ProviderUnavailable("token missing")


class LintKeyboardInterrupt(_Base):
    def lint_vacuity(self, path):
        raise KeyboardInterrupt()


class LintSystemExit(_Base):
    def lint_vacuity(self, path):
        raise SystemExit(3)


class AcceptAllWithLint(Conforming):
    def evaluate(self, state):
        self.calls.append("evaluate")
        return _oracle().OracleOutcome.ACCEPTED


class AcceptAllNoLint(_Base):
    def evaluate(self, state):
        self.calls.append("evaluate")
        return _oracle().OracleOutcome.ACCEPTED


# --------------------------------------------------------------------------
# AC1: the operation is called
# --------------------------------------------------------------------------

def test_ac1_host_lint_is_called_and_its_answer_decides():
    h = _h()
    a = AlwaysFalse()
    engine_probe = h.run_adversaries(only=("ADV-3",))
    assert engine_probe == {"ADV-3": "defended"}
    out = h.run_adversaries(only=("ADV-3",), adapter=a)
    assert out == {"ADV-3": "shadow:undefended"}
    assert a.calls.count("lint_vacuity") >= 1


# --------------------------------------------------------------------------
# AC2: conforming adapter
# --------------------------------------------------------------------------

def test_ac2_conforming_adapter_is_shadow_defended_with_two_distinct_files():
    h = _h()
    a = Conforming()
    out = h.run_adversaries(only=("ADV-3",), adapter=a)
    assert out == {"ADV-3": "shadow:defended"}
    assert a.calls == ["lint_vacuity", "lint_vacuity"]
    assert len(a.seen) == 2
    (p1, exists1, suf1, text1), (p2, exists2, suf2, text2) = a.seen
    assert exists1 and exists2
    assert suf1 == ".py" and suf2 == ".py"
    assert p1 != p2
    assert 'patch("mypkg.subject.compute_digest")' not in text1
    assert 'patch("mypkg.subject.compute_digest")' in text2


# --------------------------------------------------------------------------
# AC3: wrong implementations fail
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [AlwaysTrue, AlwaysFalse, NaivePatchLint])
def test_ac3_wrong_lint_is_shadow_undefended(cls):
    h = _h()
    out = h.run_adversaries(only=("ADV-3",), adapter=cls())
    assert out == {"ADV-3": "shadow:undefended"}


# --------------------------------------------------------------------------
# AC4: degrade, don't fail
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    LintRaises, LintNone, LintYesStr, LintOne, LintNotCallable,
    SubscriptionUnavailable, ApiTokenUnavailable,
])
def test_ac4_failing_or_malformed_lint_is_shadow_indeterminate(cls):
    h = _h()
    out = h.run_adversaries(only=("ADV-3",), adapter=cls(), timeout_s=0.2)
    assert out == {"ADV-3": "shadow:indeterminate"}


def test_ac4_hung_lint_is_indeterminate_and_worker_is_daemon():
    h = _h()
    a = LintSleeps()
    t0 = time.monotonic()
    out = h.run_adversaries(only=("ADV-3",), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {"ADV-3": "shadow:indeterminate"}
    assert elapsed < 2.5, f"guard waited for the hung lint ({elapsed:.2f}s)"
    assert a.entered.wait(1.0)
    main = threading.main_thread()
    non_daemon = [
        t.name for t in threading.enumerate()
        if t is not main and t.is_alive() and not t.daemon
    ]
    assert non_daemon == []


@pytest.mark.parametrize("cls,exc", [
    (LintKeyboardInterrupt, KeyboardInterrupt),
    (LintSystemExit, SystemExit),
])
def test_ac4_interrupts_propagate(cls, exc):
    h = _h()
    with pytest.raises(exc):
        h.run_adversaries(only=("ADV-3",), adapter=cls(), timeout_s=5.0)


# --------------------------------------------------------------------------
# AC5: omitted stays not_executed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [NoLint, NoneLint])
def test_ac5_omitted_operation_is_not_executed_and_nothing_called(cls):
    h = _h()
    a = cls()
    assert h.run_adversaries(only=("ADV-3",), adapter=a) == {"ADV-3": "not_executed"}
    assert a.calls == []

    full = h.run_adversaries(adapter=cls())
    assert set(full) == set(h.ADVERSARIES)
    assert len(full) == 9
    assert full["ADV-1"] == h.OUTCOME_DEFENDED
    assert full["ADV-2"] == h.OUTCOME_DEFENDED
    assert full["ADV-3"] == "not_executed"


# --------------------------------------------------------------------------
# AC6: shadow is not counted
# --------------------------------------------------------------------------

def test_ac6_shadow_defended_does_not_lift_the_level():
    h = _h()
    out = h.run_adversaries(adapter=Conforming())
    assert out["ADV-1"] == h.OUTCOME_DEFENDED
    assert out["ADV-2"] == h.OUTCOME_DEFENDED
    assert out["ADV-3"] == "shadow:defended"

    outcomes = {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}
    outcomes["ADV-3"] = out["ADV-3"]
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
# AC7: kill switch
# --------------------------------------------------------------------------

def test_ac7_adapter_ops_off_never_calls_and_is_not_executed():
    h = _h()
    on = h.run_adversaries(adapter=Conforming(), adapter_ops="shadow")
    a = Conforming()
    off = h.run_adversaries(adapter=a, adapter_ops="off")
    assert a.calls.count("lint_vacuity") == 0
    assert off["ADV-3"] == "not_executed"
    assert off["ADV-1"] == on["ADV-1"] == h.OUTCOME_DEFENDED
    assert off["ADV-2"] == on["ADV-2"] == h.OUTCOME_DEFENDED
    assert on["ADV-3"] == "shadow:defended"


@pytest.mark.parametrize("bad", ["enforce", "", "SHADOW", None, True])
def test_ac7_unknown_adapter_ops_value_raises(bad):
    h = _h()
    with pytest.raises(ValueError):
        h.run_adversaries(adapter=Conforming(), adapter_ops=bad)


def test_ac7_adapter_none_ignores_adapter_ops():
    h = _h()
    nine = {n: "defended" for n in h.ADVERSARIES}
    assert h.run_adversaries(adapter=None, adapter_ops="off") == nine
    assert h.run_adversaries(adapter=None, adapter_ops="shadow") == nine


# --------------------------------------------------------------------------
# AC8: scoped run
# --------------------------------------------------------------------------

def test_ac8_scoped_adv3_calls_lint_twice_and_oracle_never():
    h = _h()
    a = Conforming()
    out = h.run_adversaries(only=("ADV-3",), adapter=a)
    assert out == {"ADV-3": "shadow:defended"}
    assert a.calls == ["lint_vacuity", "lint_vacuity"]
    assert "freeze" not in a.calls
    assert "evaluate" not in a.calls


# --------------------------------------------------------------------------
# AC9: no interference
# --------------------------------------------------------------------------

@pytest.mark.parametrize("with_lint,without_lint", [
    (Conforming, NoLint),
    (AcceptAllWithLint, AcceptAllNoLint),
])
def test_ac9_lint_does_not_change_adv1_adv2(with_lint, without_lint):
    h = _h()
    names = ("ADV-1", "ADV-2")
    base = h.run_adversaries(only=names, adapter=without_lint())
    got = h.run_adversaries(only=names + ("ADV-3",), adapter=with_lint())
    assert {n: got[n] for n in names} == base
    assert got["ADV-3"].startswith("shadow:")


# --------------------------------------------------------------------------
# AC10: CLI end-to-end (subprocess)
# --------------------------------------------------------------------------

_FACTORY_SRC = textwrap.dedent('''
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

    def make_host():
        return Host()
''')

_MOD = "bd197_cli_factories"


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


def test_ac10_cli_default_reports_shadow_defended(tmp_path):
    r = _cli(tmp_path)
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["adversaries"]["ADV-3"] == "shadow:defended"
    assert att["level_achieved"] == "BD-L1"


def test_ac10_cli_adapter_ops_shadow_flag_accepted(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "shadow")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["adversaries"]["ADV-3"] == "shadow:defended"


def test_ac10_cli_adapter_ops_off_is_not_executed(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "off")
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["adversaries"]["ADV-3"] == "not_executed"
    assert att["level_achieved"] == "BD-L1"


def test_ac10_cli_adapter_ops_invalid_is_usage_error(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "bogus")
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stdout == ""
    assert "adapter-ops" in r.stderr


# --------------------------------------------------------------------------
# AC11: pinned surface (passes today and must keep passing)
# --------------------------------------------------------------------------

def test_ac11_surface_unchanged():
    h = _h()
    assert set(h.__all__) == {
        "ADVERSARIES", "OUTCOME_DEFENDED", "OUTCOME_UNDEFENDED", "OUTCOME_ERRORED",
        "OUTCOME_INDETERMINATE", "AdapterState", "adapter_identity",
        "run_adversaries", "build_attestation", "validate_attestation",
    }
    assert len(h.__all__) == len(set(h.__all__)) == 10


def test_ac11_harness_source_names_no_provider():
    h = _h()
    src = Path(h.__file__).read_text(encoding="utf-8").lower()
    for bad in ("anthropic", "claude", "openai", "subscription", "api-token", "api_key"):
        assert bad not in src, bad


def test_ac11_no_adapter_run_is_unchanged():
    h = _h()
    nine = {n: "defended" for n in
            ("ADV-1", "ADV-2", "ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-7",
             "ADV-8", "ADV-10")}
    assert h.run_adversaries() == nine
    assert h.run_adversaries(adapter=None) == nine
