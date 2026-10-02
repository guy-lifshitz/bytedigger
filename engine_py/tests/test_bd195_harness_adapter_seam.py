"""bd#195 -- host-adapter seam for `harness.run_adversaries` (levels spec §9 step 1).

Spec: `docs/decisions/2026-10-02-bd195-harness-adapter-seam.md`.

Class: an attestation field the subject asserts about itself. Today
`run_adversaries(only=...)` accepts no adapter, so ADV-1..ADV-10 attest the
ENGINE and never a host, and `adapter_identity` is a free string. The seam:
ADV-1/ADV-2 run through the caller's adapter (freeze, control, mutate, attack);
every other adversary is `not_executed`; identity is read from the adapter; a
CLI loads `module:factory` and publishes.

Every test fails today inside its own body (missing `adapter` kwarg,
OUTCOME_INDETERMINATE, adapter_identity, AdapterState, CLI). `conformance.*`
imports are deferred into test bodies (bd#24). Nothing under
`bytedigger_engine/` is ever put on sys.path (bd#182).
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


class AcceptAll(_Base):
    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        return self._digest(root, list(paths))

    def evaluate(self, state):
        self.calls.append("evaluate")
        return _oracle().OracleOutcome.ACCEPTED


class RejectAll(_Base):
    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        return self._digest(root, list(paths))

    def evaluate(self, state):
        self.calls.append("evaluate")
        return _oracle().OracleOutcome.REJECTED


class DigestAdapter(_Base):
    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        return self._digest(root, list(paths))

    def evaluate(self, state):
        self.calls.append("evaluate")
        O = _oracle().OracleOutcome
        now = self._digest(state.root, list(state.members))
        return O.ACCEPTED if now == state.frozen else O.REJECTED


class RaisingEvaluate(DigestAdapter):
    def evaluate(self, state):
        self.calls.append("evaluate")
        raise RuntimeError("evaluate boom")


class RaisingFreeze(DigestAdapter):
    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        raise RuntimeError("freeze boom")


class FreezeEmpty(DigestAdapter):
    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        return ""


class FreezeInt(DigestAdapter):
    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        return 123


class EvaluateTrue(DigestAdapter):
    def evaluate(self, state):
        self.calls.append("evaluate")
        return True


class SleepingEvaluate(DigestAdapter):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()

    def evaluate(self, state):
        self.calls.append("evaluate")
        self.entered.set()
        time.sleep(5.0)
        return _oracle().OracleOutcome.ACCEPTED


class SleepingFreeze(DigestAdapter):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()

    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        self.entered.set()
        time.sleep(5.0)
        return self._digest(root, list(paths))


class FreezeKeyboardInterrupt(DigestAdapter):
    def freeze(self, paths, *, root):
        raise KeyboardInterrupt()


class FreezeSystemExit(DigestAdapter):
    def freeze(self, paths, *, root):
        raise SystemExit(3)


class EvaluateKeyboardInterrupt(DigestAdapter):
    def evaluate(self, state):
        raise KeyboardInterrupt()


class EvaluateSystemExit(DigestAdapter):
    def evaluate(self, state):
        raise SystemExit(3)


class SubscriptionDigest(DigestAdapter):
    identity = {"backend": "subscription-session", "source": "env"}


class ApiTokenDigest(DigestAdapter):
    identity = {"backend": "api-token", "source": "kwarg"}


class SubscriptionUnavailable(_Base):
    identity = {"backend": "subscription-session", "source": "env"}

    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        raise ConnectionError("session probe failed")

    def evaluate(self, state):
        self.calls.append("evaluate")
        raise ConnectionError("session probe failed")


class ProviderUnavailable(Exception):
    pass


class ApiTokenUnavailable(_Base):
    identity = {"backend": "api-token", "source": "kwarg"}

    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        raise ProviderUnavailable("token missing")

    def evaluate(self, state):
        self.calls.append("evaluate")
        raise ProviderUnavailable("token missing")


_DEGRADE = {
    "evaluate_raises": RaisingEvaluate,
    "freeze_raises": RaisingFreeze,
    "freeze_empty": FreezeEmpty,
    "freeze_int": FreezeInt,
    "evaluate_true": EvaluateTrue,
    "evaluate_sleeps": SleepingEvaluate,
    "freeze_sleeps": SleepingFreeze,
    "subscription_unavailable": SubscriptionUnavailable,
    "api_token_unavailable": ApiTokenUnavailable,
}

_NON_ORACLE = ("ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-7", "ADV-8", "ADV-10")


# --------------------------------------------------------------------------
# AC1-AC4: the seam
# --------------------------------------------------------------------------

def test_ac1_accept_all_adapter_is_undefended_not_engine_defended():
    h = _h()
    out = h.run_adversaries(only=("ADV-1", "ADV-2"), adapter=AcceptAll())
    assert out == {"ADV-1": h.OUTCOME_UNDEFENDED, "ADV-2": h.OUTCOME_UNDEFENDED}


def test_ac2_conforming_digest_adapter_defended_and_actually_called():
    h = _h()
    for name in ("ADV-1", "ADV-2"):
        a = DigestAdapter()
        out = h.run_adversaries(only=(name,), adapter=a)
        assert out == {name: h.OUTCOME_DEFENDED}
        assert a.calls.count("freeze") >= 1, a.calls
        assert a.calls.count("evaluate") >= 2, a.calls


def test_ac3_reject_all_adapter_is_not_a_defence():
    h = _h()
    out = h.run_adversaries(only=("ADV-1", "ADV-2"), adapter=RejectAll())
    assert out == {"ADV-1": h.OUTCOME_UNDEFENDED, "ADV-2": h.OUTCOME_UNDEFENDED}


@pytest.mark.parametrize("kind", sorted(_DEGRADE))
def test_ac4_failing_adapter_degrades_to_indeterminate(kind):
    h = _h()
    assert h.OUTCOME_INDETERMINATE == "indeterminate"
    adapter = _DEGRADE[kind]()
    t0 = time.monotonic()
    out = h.run_adversaries(
        only=("ADV-1", "ADV-2"), adapter=adapter, timeout_s=0.2
    )
    elapsed = time.monotonic() - t0
    assert out == {
        "ADV-1": h.OUTCOME_INDETERMINATE,
        "ADV-2": h.OUTCOME_INDETERMINATE,
    }
    assert elapsed < 2.5, f"guard waited for the hung adapter ({elapsed:.2f}s)"
    if kind in ("evaluate_sleeps", "freeze_sleeps"):
        # The stub is still asleep (5s): the abandoned worker must be a daemon.
        assert adapter.entered.wait(1.0)
        main = threading.main_thread()
        non_daemon = [
            t.name for t in threading.enumerate()
            if t is not main and t.is_alive() and not t.daemon
        ]
        assert non_daemon == []


@pytest.mark.parametrize("cls,exc", [
    (FreezeKeyboardInterrupt, KeyboardInterrupt),
    (FreezeSystemExit, SystemExit),
    (EvaluateKeyboardInterrupt, KeyboardInterrupt),
    (EvaluateSystemExit, SystemExit),
])
def test_ac4b_interrupts_propagate(cls, exc):
    h = _h()
    with pytest.raises(exc):
        h.run_adversaries(only=("ADV-1",), adapter=cls(), timeout_s=5.0)


# --------------------------------------------------------------------------
# AC5: indeterminate sinks the level
# --------------------------------------------------------------------------

def test_ac5_indeterminate_sinks_the_level():
    h = _h()
    got = h.run_adversaries(only=("ADV-1",), adapter=RaisingEvaluate(), timeout_s=0.2)
    assert got == {"ADV-1": h.OUTCOME_INDETERMINATE}
    outcomes = {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}
    outcomes["ADV-1"] = got["ADV-1"]
    att = h.build_attestation(
        outcomes,
        level_claimed="BD-L1",
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-10-02T00:00:00Z",
    )
    assert att["level_achieved"] == "BD-L0"
    assert h.validate_attestation(att) != ()


# --------------------------------------------------------------------------
# AC6: non-oracle adversaries are not borrowed
# --------------------------------------------------------------------------

def test_ac6_non_oracle_adversaries_not_executed_and_adapter_not_called():
    h = _h()
    a = DigestAdapter()
    out = h.run_adversaries(only=_NON_ORACLE, adapter=a)
    assert out == {n: "not_executed" for n in _NON_ORACLE}
    assert a.calls == []

    b = DigestAdapter()
    assert h.run_adversaries(only=("ADV-3",), adapter=b) == {"ADV-3": "not_executed"}
    assert b.calls == []

    c = DigestAdapter()
    assert h.run_adversaries(only=("ADV-99",), adapter=c) == {"ADV-99": "not_executed"}
    assert c.calls == []

    d = DigestAdapter()
    assert h.run_adversaries(only=("ADV-9",), adapter=d) == {"ADV-9": "not_executed"}
    assert d.calls == []

    full = h.run_adversaries(adapter=DigestAdapter())
    assert set(full) == set(h.ADVERSARIES)
    assert len(full) == 9
    assert full["ADV-1"] == h.OUTCOME_DEFENDED
    assert full["ADV-2"] == h.OUTCOME_DEFENDED
    for n in _NON_ORACLE:
        assert full[n] == "not_executed"


# --------------------------------------------------------------------------
# AC7: adapter=None unchanged
# --------------------------------------------------------------------------

_EXPECTED_ATTESTATION_JSON = (
    '{"adapter_identity": "test-adapter", "adversaries": {"ADV-1": "defended", '
    '"ADV-10": "defended", "ADV-2": "defended", "ADV-3": "defended", '
    '"ADV-4": "defended", "ADV-5": "defended", "ADV-6": "defended", '
    '"ADV-7": "defended", "ADV-8": "defended", "ADV-9": "not_executed"}, '
    '"engine_version": "0.0.0-test", "host_identity": "test-host", '
    '"level_achieved": "BD-L3", "level_claimed": "BD-L3", '
    '"timestamp": "2026-10-02T00:00:00Z"}'
)


def test_ac7_adapter_none_is_unchanged():
    h = _h()
    nine = {n: "defended" for n in
            ("ADV-1", "ADV-2", "ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-7",
             "ADV-8", "ADV-10")}
    assert h.run_adversaries() == nine
    assert h.run_adversaries(adapter=None) == nine
    att = h.build_attestation(
        h.run_adversaries(),
        level_claimed="BD-L3",
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-10-02T00:00:00Z",
    )
    assert json.dumps(att, sort_keys=True) == _EXPECTED_ATTESTATION_JSON


# --------------------------------------------------------------------------
# AC8: identity from the adapter
# --------------------------------------------------------------------------

def test_ac8_identity_exact_shape_for_each_backend_and_extra_key_dropped():
    h = _h()
    assert h.adapter_identity(SubscriptionUnavailable()) == {
        "backend": "subscription-session", "source": "env"}
    assert h.adapter_identity(ApiTokenUnavailable()) == {
        "backend": "api-token", "source": "kwarg"}

    class Extra:
        identity = {"backend": "b", "source": "s", "secret": "x"}

    assert h.adapter_identity(Extra()) == {"backend": "b", "source": "s"}


def test_ac8_malformed_identity_raises_value_error():
    h = _h()

    class Missing:
        pass

    class EmptyBackend:
        identity = {"backend": "", "source": "s"}

    class BadSource:
        identity = {"backend": "b", "source": 123}

    class NoneIdentity:
        identity = None

    class StrIdentity:
        identity = "b/s"

    class ListIdentity:
        identity = ["backend", "source"]

    class NoneBackend:
        identity = {"backend": None, "source": "s"}

    for bad in (Missing(), EmptyBackend(), BadSource(), NoneIdentity(),
                StrIdentity(), ListIdentity(), NoneBackend()):
        with pytest.raises(ValueError):
            h.adapter_identity(bad)


@pytest.mark.parametrize("cls", [SubscriptionDigest, ApiTokenDigest])
def test_ac8_conforming_adapter_of_each_identity_shape_is_defended(cls):
    h = _h()
    a = cls()
    out = h.run_adversaries(only=("ADV-1", "ADV-2"), adapter=a)
    assert out == {"ADV-1": h.OUTCOME_DEFENDED, "ADV-2": h.OUTCOME_DEFENDED}
    assert h.adapter_identity(a) == dict(cls.identity)


# --------------------------------------------------------------------------
# AC9: CLI end-to-end (subprocess)
# --------------------------------------------------------------------------

_FACTORY_SRC = textwrap.dedent('''
    import time
    from pathlib import Path
    from bytedigger_engine.conformance import oracle as _o

    class _Base:
        identity = {"backend": "cli-backend", "source": "cli-source", "extra": "dropped"}

        def _digest(self, root, members):
            rels = [str(Path(p).relative_to(root)) for p in members]
            return _o.compute_digest(root, rels)

    class Digest(_Base):
        def freeze(self, paths, *, root):
            return self._digest(root, list(paths))

        def evaluate(self, state):
            O = _o.OracleOutcome
            now = self._digest(state.root, list(state.members))
            return O.ACCEPTED if now == state.frozen else O.REJECTED

    class Accept(Digest):
        def evaluate(self, state):
            return _o.OracleOutcome.ACCEPTED

    class Unavailable(_Base):
        identity = {"backend": "api-token", "source": "kwarg"}

        def freeze(self, paths, *, root):
            raise ConnectionError("down")

        def evaluate(self, state):
            raise ConnectionError("down")

    def make_digest():
        return Digest()

    def make_accept():
        return Accept()

    def make_unavailable():
        return Unavailable()

    def make_noidentity():
        return type("Bare", (), {"freeze": Digest.freeze, "evaluate": Digest.evaluate,
                                 "_digest": Digest._digest})()

    def make_raises():
        raise RuntimeError("factory boom")

    class StrIdent(Digest):
        identity = "cli-backend/cli-source"

    def make_strident():
        return StrIdent()
''')

_MOD = "bd195_cli_factories"


def _cli(tmp_path, adapter, claimed, *extra):
    (tmp_path / f"{_MOD}.py").write_text(_FACTORY_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_PY) + os.pathsep + str(tmp_path)
    cmd = [
        sys.executable, "-m", "bytedigger_engine.conformance.harness",
        "--adapter", adapter,
        "--level-claimed", claimed,
        "--engine-version", "0.0.0-test",
        "--host-identity", "test-host",
        "--timeout-s", "5",
        *extra,
    ]
    return subprocess.run(
        cmd, cwd=str(ENGINE_PY), env=env, capture_output=True, text=True, timeout=60
    )


def test_ac9_cli_conforming_adapter_claim_l1_exit_0(tmp_path):
    r = _cli(tmp_path, f"{_MOD}:make_digest", "BD-L1")
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["adapter_identity"] == {"backend": "cli-backend", "source": "cli-source"}
    assert att["level_achieved"] == "BD-L1"
    assert att["adversaries"]["ADV-3"] == "not_executed"
    assert att["adversaries"]["ADV-1"] == "defended"


def test_ac9_cli_overclaim_exit_1_json_still_written(tmp_path):
    out = tmp_path / "att.json"
    r = _cli(tmp_path, f"{_MOD}:make_digest", "BD-L2", "--out", str(out))
    assert r.returncode == 1
    assert r.stderr.strip() != ""
    assert "level_claimed" in r.stderr
    att =json.loads(out.read_text(encoding="utf-8"))
    assert att["level_claimed"] == "BD-L2"
    assert att["level_achieved"] == "BD-L1"


def test_ac9_cli_accept_all_adapter_claim_l1_exit_1(tmp_path):
    r = _cli(tmp_path, f"{_MOD}:make_accept", "BD-L1")
    assert r.returncode == 1, r.stderr
    att = json.loads(r.stdout)
    assert att["adversaries"]["ADV-1"] == "undefended"


def test_ac9_cli_unavailable_backend_claim_l0_exit_0_indeterminate(tmp_path):
    r = _cli(tmp_path, f"{_MOD}:make_unavailable", "BD-L0")
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["adversaries"]["ADV-1"] == "indeterminate"
    assert att["adapter_identity"] == {"backend": "api-token", "source": "kwarg"}


@pytest.mark.parametrize("spec", [
    "nocolon",
    "bd195_no_such_module_xyz:make_digest",
    f"{_MOD}:no_such_attribute",
    f"{_MOD}:make_raises",
    f"{_MOD}:make_noidentity",
    f"{_MOD}:make_strident",
])
def test_ac9_cli_exit_2_writes_nothing(tmp_path, spec):
    out = tmp_path / "never.json"
    r = _cli(tmp_path, spec, "BD-L0", "--out", str(out))
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stdout == ""
    assert not out.exists()


def test_ac9_cli_identity_override_flag_is_rejected_exit_2(tmp_path):
    out = tmp_path / "never.json"
    r = _cli(tmp_path, f"{_MOD}:make_digest", "BD-L1",
             "--adapter-identity", "x", "--out", str(out))
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stdout == ""
    assert not out.exists()


# --------------------------------------------------------------------------
# AC10: surface
# --------------------------------------------------------------------------

def test_ac10_surface_gains_exactly_three_names_and_main_is_private():
    h = _h()
    old = {
        "ADVERSARIES", "OUTCOME_DEFENDED", "OUTCOME_UNDEFENDED", "OUTCOME_ERRORED",
        "run_adversaries", "build_attestation", "validate_attestation",
    }
    new = {"OUTCOME_INDETERMINATE", "AdapterState", "adapter_identity"}
    assert set(h.__all__) == old | new
    assert len(h.__all__) == len(set(h.__all__)) == 10
    assert hasattr(h, "_main")
    assert "_main" not in h.__all__


# --------------------------------------------------------------------------
# AC11: provider-agnostic
# --------------------------------------------------------------------------

def test_ac11_harness_source_names_no_provider():
    h = _h()
    # Coupled to the new seam: the scan is only meaningful once the identity
    # reader (the code that could tempt provider names) exists.
    assert callable(h.adapter_identity)
    src = Path(h.__file__).read_text(encoding="utf-8").lower()
    for bad in ("anthropic", "claude", "openai", "subscription", "api-token", "api_key"):
        assert bad not in src, bad
