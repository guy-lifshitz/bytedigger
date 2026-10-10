"""bd#273 (group 5) -- a host operation for ADV-8 (attribution), in shadow.

Spec: `docs/decisions/2026-10-11-bd273-adapter-attribution-op.md`.

Under an adapter, ADV-8 (a block injected into a prompt must be attributable) used
to be `not_executed`. The adapter may now offer `attribute_block(content)`,
returning a mapping of raw facts (`source_id`, `digest`). The harness calls it
twice with control contents and once with a probe content, judges the facts
itself, and files the result as `shadow:<outcome>`, which never counts toward a
level. `adapter_ops="off"` restores the old ceiling.

No test here reads the clock. Stubs judge nothing: they only report facts, and
they compute digests with `hashlib` themselves, never with the harness.

`conformance.*` imports are deferred into test bodies. Nothing under
`bytedigger_engine/` is put on sys.path.
"""
from __future__ import annotations

import hashlib
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

METHOD = "attribute_block"


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _oracle():
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    return oracle


def _d(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Stub adapters (live in the test module). Each records a call log.
# `responder(content, seen)` gets the content and the contents handed earlier.
# --------------------------------------------------------------------------

class _Base:
    identity = {"backend": "stub-backend", "source": "stub-source"}

    def __init__(self):
        self.calls = []
        self.contents = []

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
    """A stub with `attribute_block(content)` that records what it is handed."""

    def op(self, content):
        self.calls.append(METHOD)
        seen = list(self.contents)
        self.contents.append(content)
        return _resolve(responder(content, seen))

    ns = {METHOD: op}
    if identity is not None:
        ns["identity"] = identity
    if extra:
        ns.update(extra)
    return type("Stub", (_Base,), ns)()


def _good(content, seen=()):
    return {"source_id": "host-src-1", "digest": _d(content)}


def _conforming():
    return _make(_good)


def _at(k, bad, good=_good):
    """`bad(content, seen)` on the k-th call (1-based), `good` on the others."""
    return lambda c, seen: bad(c, seen) if len(seen) == k - 1 else good(c, seen)


KS = [pytest.param(1, id="call1"), pytest.param(2, id="call2"),
      pytest.param(3, id="call3")]


# --------------------------------------------------------------------------
# AC1: the operation is called, not the engine probe
# --------------------------------------------------------------------------

def test_ac1_host_answer_decides_not_the_engine_probe():
    h = _h()
    assert h.run_adversaries(only=("ADV-8",)) == {"ADV-8": "defended"}
    a = _make(lambda c, seen: {"source_id": "host-src-1", "digest": _d(c + "\n")})
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:undefended"}
    assert a.calls.count(METHOD) >= 1


# --------------------------------------------------------------------------
# AC2: conforming host
# --------------------------------------------------------------------------

def _check_args(contents):
    assert len(contents) == 3
    assert all(type(c) is str for c in contents)
    assert all(c for c in contents)
    assert len(set(contents)) == 3
    assert not contents[2].isascii()
    assert "\n" in contents[2] or "\r" in contents[2]


def test_ac2_conforming_is_shadow_defended_called_three_times():
    h = _h()
    a = _conforming()
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:defended"}
    assert a.calls == [METHOD] * 3
    _check_args(a.contents)


def test_ac2_full_run_calls_the_method_three_times():
    h = _h()
    a = _conforming()
    out = h.run_adversaries(adapter=a)
    assert out["ADV-8"] == "shadow:defended"
    assert a.calls.count(METHOD) == 3
    _check_args(a.contents)


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


def _wrap_extra(f):
    return {**f, "detail": "x", "n": 3}


def _wrap_proxy(f):
    return types.MappingProxyType({**f, "extra": 1})


def _wrap_opaque(f):
    return _OpaqueMapping(f)


def _wrap_inner_spaces(f):
    return {**f, "source_id": "blk 1 of x"}


def _wrap_padded(f):
    return {**f, "source_id": "  blk  "}


@pytest.mark.parametrize(
    "wrap",
    [_wrap_extra, _wrap_proxy, _wrap_opaque, _wrap_inner_spaces, _wrap_padded],
    ids=["extra-keys", "mappingproxy", "non-dict-mapping", "inner-spaces", "padded"],
)
def test_ac2_extra_keys_non_dict_mappings_and_inner_spaces_are_accepted(wrap):
    h = _h()
    a = _make(lambda c, seen: wrap(_good(c)))
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:defended"}
    assert len(a.calls) == 3


# --------------------------------------------------------------------------
# AC3: wrong implementations fail
# --------------------------------------------------------------------------

class _StrSub(str):
    pass


def _drop(*keys):
    return lambda f: {k: v for k, v in f.items() if k not in keys}


def _set(key, value):
    return lambda f: {**f, key: value}


_SOURCE_VARIANTS = {
    "missing": _drop("source_id"),
    "none": _set("source_id", None),
    "empty": _set("source_id", ""),
    "blank": _set("source_id", "   "),
    "int": _set("source_id", 7),
    "bytes": _set("source_id", b"x"),
    "list": _set("source_id", ["x"]),
    "str-subclass": _set("source_id", _StrSub("host-src-1")),
}


@pytest.mark.parametrize("mut", list(_SOURCE_VARIANTS.values()),
                         ids=list(_SOURCE_VARIANTS))
def test_ac3_a_bad_source_id_is_undefended_after_three_calls(mut):
    h = _h()
    a = _make(lambda c, seen: mut(_good(c)))
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:undefended"}
    assert len(a.calls) == 3


_DIGEST_VARIANTS = {
    "other-text": lambda c, seen: _d("something else entirely"),
    "plus-newline": lambda c, seen: _d(c + "\n"),
    "stale-control": lambda c, seen: _d(seen[1]),
    "stale-first-control": lambda c, seen: _d(seen[0]),
    "bare-hex": lambda c, seen: _hex(c),
    "upper-hex": lambda c, seen: "sha256:" + _hex(c).upper(),
    "bare-upper-hex": lambda c, seen: _hex(c).upper(),
    "other-prefix": lambda c, seen: "sha1:" + _hex(c),
    "empty": lambda c, seen: "",
    "lossy-ascii": lambda c, seen: (
        "sha256:" + hashlib.sha256(c.encode("ascii", "ignore")).hexdigest()
    ),
}


@pytest.mark.parametrize("fn", list(_DIGEST_VARIANTS.values()),
                         ids=list(_DIGEST_VARIANTS))
def test_ac3_a_bad_probe_digest_is_undefended_after_three_calls(fn):
    h = _h()
    a = _make(_at(3, lambda c, seen: {"source_id": "host-src-1", "digest": fn(c, seen)}))
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:undefended"}
    assert len(a.calls) == 3


# --------------------------------------------------------------------------
# AC4: control first
# --------------------------------------------------------------------------

def test_ac4_the_same_digest_for_every_content_is_indeterminate_after_two_calls():
    h = _h()
    a = _make(lambda c, seen: {"source_id": "host-src-1", "digest": _d("constant")})
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:indeterminate"}
    assert len(a.calls) == 2


def test_ac4_an_empty_digest_for_every_content_is_indeterminate_after_two_calls():
    h = _h()
    a = _make(lambda c, seen: {"source_id": "host-src-1", "digest": ""})
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:indeterminate"}
    assert len(a.calls) == 2


def test_ac4_controls_with_wrong_but_different_digests_proceed_and_end_undefended():
    h = _h()
    a = _make(lambda c, seen: {"source_id": "host-src-1", "digest": _d("x-" + c)})
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:undefended"}
    assert len(a.calls) == 3


def test_ac4_wrong_but_different_control_digests_with_a_right_probe_are_defended():
    h = _h()
    a = _make(_at(3, lambda c, seen: _good(c),
                  good=lambda c, seen: {"source_id": "host-src-1",
                                        "digest": _d("x-" + c)}))
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:defended"}
    assert len(a.calls) == 3


def test_ac4_a_control_without_a_source_id_proceeds_and_ends_defended():
    h = _h()
    a = _make(_at(3, lambda c, seen: _good(c),
                  good=lambda c, seen: {"digest": _d(c)}))
    out = h.run_adversaries(only=("ADV-8",), adapter=a)
    assert out == {"ADV-8": "shadow:defended"}
    assert len(a.calls) == 3


# --------------------------------------------------------------------------
# AC5: degrade, don't fail
# --------------------------------------------------------------------------

_WHOLE = {
    "none": lambda f: None,
    "bare-true": lambda f: True,
    "bare-string": lambda f: "facts",
    "list": lambda f: [],
    "empty-dict": lambda f: {},
    "digest-missing": _drop("digest"),
    "digest-missing-and-no-source-id": _drop("digest", "source_id"),
    "digest-int": _set("digest", 7),
    "digest-bytes": _set("digest", b"x"),
    "digest-none": _set("digest", None),
    "digest-list": _set("digest", ["x"]),
    "digest-str-subclass": lambda f: {**f, "digest": _StrSub(f["digest"])},
}


@pytest.mark.parametrize("k", KS)
@pytest.mark.parametrize("mut", list(_WHOLE.values()), ids=list(_WHOLE))
def test_ac5_a_malformed_answer_is_indeterminate_and_stops_the_run(k, mut):
    h = _h()
    a = _make(_at(k, lambda c, seen: mut(_good(c))))
    out = h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-8": "shadow:indeterminate"}
    assert len(a.calls) == k


@pytest.mark.parametrize("k", KS)
def test_ac5_raising_method_is_indeterminate(k):
    h = _h()
    a = _make(_at(k, lambda c, seen: _Raise(RuntimeError("boom"))))
    out = h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-8": "shadow:indeterminate"}
    assert len(a.calls) == k


def _non_daemon_threads():
    main = threading.main_thread()
    return [
        t.name for t in threading.enumerate()
        if t is not main and t.is_alive() and not t.daemon
    ]


@pytest.mark.parametrize("k", KS)
def test_ac5_hung_method_is_indeterminate_and_worker_is_daemon(k):
    h = _h()
    entered = threading.Event()

    def hang(c, seen):
        entered.set()
        time.sleep(5.0)
        return _good(c)

    a = _make(_at(k, hang))
    t0 = time.monotonic()
    out = h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {"ADV-8": "shadow:indeterminate"}
    assert elapsed < 2.5, f"guard waited for the hung method ({elapsed:.2f}s)"
    assert entered.wait(1.0)
    assert len(a.calls) == k
    assert _non_daemon_threads() == []


def test_ac5_non_callable_attribute_is_indeterminate():
    h = _h()
    a = type("Stub", (_Base,), {METHOD: "not a function"})()
    out = h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-8": "shadow:indeterminate"}


class ProviderUnavailable(Exception):
    pass


def test_ac5_session_down_is_indeterminate():
    h = _h()
    a = _make(
        lambda c, seen: _Raise(ConnectionError("session probe failed")),
        identity={"backend": "session-backend", "source": "env"},
    )
    out = h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-8": "shadow:indeterminate"}


def test_ac5_missing_credential_is_indeterminate():
    h = _h()
    a = _make(
        lambda c, seen: _Raise(ProviderUnavailable("credential missing")),
        identity={"backend": "token-backend", "source": "kwarg"},
    )
    out = h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=0.5)
    assert out == {"ADV-8": "shadow:indeterminate"}


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
        return iter(("source_id", "digest"))

    def __len__(self):
        return 2


@pytest.mark.parametrize("k", KS)
def test_ac5_a_mapping_whose_reads_block_is_indeterminate_in_time(k):
    h = _h()
    hang = _HangingMapping()
    a = _make(_at(k, lambda c, seen: hang))
    t0 = time.monotonic()
    out = h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert out == {"ADV-8": "shadow:indeterminate"}
    assert elapsed < 1.5, f"extraction ran on the calling thread ({elapsed:.2f}s)"
    assert _non_daemon_threads() == []


@pytest.mark.parametrize("k", KS)
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)],
                         ids=["keyboard-interrupt", "system-exit"])
def test_ac5_interrupts_propagate(k, exc):
    h = _h()
    a = _make(_at(k, lambda c, seen: _Raise(exc)))
    with pytest.raises(type(exc)):
        h.run_adversaries(only=("ADV-8",), adapter=a, timeout_s=5.0)


# --------------------------------------------------------------------------
# AC6 (shadow half; the not_executed half is pinned in the pins file)
# --------------------------------------------------------------------------

def test_ac6_the_operation_alone_gives_adv8_a_shadow_outcome():
    h = _h()
    a = type("OnlyAttribute", (_Base,), {
        METHOD: lambda self, content: (
            self.calls.append(METHOD) or self.contents.append(content)
            or _good(content)
        ),
    })()
    out = h.run_adversaries(adapter=a)
    assert out["ADV-8"] == "shadow:defended"
    for other in ("ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-7", "ADV-10"):
        assert out[other] == "not_executed"


# --------------------------------------------------------------------------
# AC7: shadow is not counted
# --------------------------------------------------------------------------

def test_ac7_shadow_defended_does_not_lift_the_level():
    h = _h()
    out = h.run_adversaries(adapter=_conforming())
    assert out["ADV-1"] == h.OUTCOME_DEFENDED
    assert out["ADV-2"] == h.OUTCOME_DEFENDED
    assert out["ADV-8"] == "shadow:defended"

    outcomes = {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}
    outcomes["ADV-8"] = out["ADV-8"]
    att = h.build_attestation(
        outcomes,
        level_claimed="BD-L3",
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-10-11T00:00:00Z",
    )
    assert att["level_achieved"] == "BD-L2"
    complaints = h.validate_attestation(att)
    assert any("level_claimed" in c for c in complaints), complaints


# --------------------------------------------------------------------------
# AC11: CLI end-to-end (subprocess)
# --------------------------------------------------------------------------

_FACTORY_SRC = textwrap.dedent('''
    import hashlib
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

        def attribute_block(self, content):
            return {
                "source_id": "cli-src-1",
                "digest": "sha256:" + hashlib.sha256(
                    content.encode("utf-8")).hexdigest(),
            }

    def make_host():
        return Host()
''')

_MOD = "bd273_cli_factories"


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
    assert att["adversaries"]["ADV-8"] == "shadow:defended"
    assert att["level_achieved"] == "BD-L1"


def test_ac11_cli_adapter_ops_off_is_not_executed_for_a_host_that_has_the_op():
    # The same host is run with the operation on and off; only the second is skipped.
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        on = _cli(Path(d))
        off = _cli(Path(d), "--adapter-ops", "off")
    assert on.returncode == 0, on.stderr
    assert json.loads(on.stdout)["adversaries"]["ADV-8"] == "shadow:defended"
    assert off.returncode == 0, off.stderr
    assert json.loads(off.stdout)["adversaries"]["ADV-8"] == "not_executed"


# --------------------------------------------------------------------------
# AC12 (clock-free, file-free, provider-free probe)
# --------------------------------------------------------------------------

def test_ac12_the_probe_reads_no_clock_no_file_and_names_no_provider():
    h = _h()
    assert "ADV-8" in h._ADAPTER_OPS
    method_name, probe = h._ADAPTER_OPS["ADV-8"]
    assert method_name == METHOD
    assert callable(probe)
    src = inspect.getsource(probe)
    for word in ("datetime", "date.today", "time.time", "time.monotonic",
                 "open(", "tempfile"):
        assert word not in src, word
    for word in ("anthropic", "claude", "openai", "subscription", "api-token",
                 "api_key"):
        assert word not in src.lower(), word
