"""RED tests for bd#108 - the EventSink seam serializes appends and states the thread-safety rule.

Spec: docs/decisions/2026-10-11-bd108-event-sink-thread-safety.md (AC1-AC6).

Unit under test: `event_sink.get_event_sink` (real), with an injected factory.
New names (ABSENT pre-GREEN): `event_sink.SerializedEventSink`,
`config_provider.event_sink_serialize_enabled`, flag `HAL_EVENT_SINK_SERIALIZE`. They are
touched only inside test bodies, so every test fails at assert time (never at collection).

Determinism (workflows.md section 1i): the race is provoked by a rendezvous inside the
unsafe sink (the first caller waits on an Event until a second caller arrives; the wait is
bounded only so a serialized path cannot hang, and the bound is never asserted) and the
threads are released together by a Barrier. No sleeps, no time thresholds, no mocking of
the unit under test, no sys.path mutation. The factory is reset after every test.
AC7 (sibling suites stay green) is verified by running those suites; no test here.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine import config_provider, event_sink, flags_catalog
from bytedigger_engine.event_log import EventLog

REPO_ROOT = Path(__file__).resolve().parents[2]
VAR = "HAL_EVENT_SINK_SERIALIZE"
THREADS = 3
PER_THREAD = 20
TOTAL = THREADS * PER_THREAD
WINDOW = 2.0  # provocation window only, never asserted


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
        monkeypatch.delenv(prefix + "EVENT_SINK_SERIALIZE", raising=False)
    event_sink.reset_default_event_sink_factory()
    yield
    event_sink.reset_default_event_sink_factory()


class _Rendezvous:
    """First caller to arrive sets `entered` and waits for a second caller."""

    def __init__(self):
        self.entered = threading.Event()
        self.second_arrived = threading.Event()
        self._gate = threading.Lock()
        self._first_taken = False

    def hit(self):
        with self._gate:
            first = not self._first_taken
            self._first_taken = True
        if first:
            self.entered.set()
            self.second_arrived.wait(WINDOW)
        else:
            self.second_arrived.set()


class _UnsafeSink:
    """Not thread-safe on purpose: read counter, rendezvous, write counter+1."""

    def __init__(self):
        self.rv = _Rendezvous()
        self.count = 0
        self.events: list[dict] = []
        self._meta = threading.Lock()  # instrumentation only
        self.inside = 0
        self.max_inside = 0

    def append(self, event_type, payload, run_id=None):
        with self._meta:
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
        try:
            seq = self.count
            self.rv.hit()
            self.count = seq + 1
            ev = {"seq": seq, "event_type": event_type, "payload": payload}
            self.events.append(ev)
            return ev
        finally:
            with self._meta:
                self.inside -= 1


class _FileSink:
    """Not thread-safe on purpose: one event line is two write() calls."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.rv = _Rendezvous()

    def append(self, event_type, payload, run_id=None):
        fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            head = '{"event_type": %s, ' % json.dumps(event_type)
            os.write(fd, head.encode())
            self.rv.hit()
            os.write(fd, ('"payload": %s}\n' % json.dumps(payload)).encode())
        finally:
            os.close(fd)
        return {"event_type": event_type, "payload": payload}


def _drive(sink) -> None:
    barrier = threading.Barrier(THREADS, timeout=30)
    errors: list[BaseException] = []

    def work(t: int) -> None:
        try:
            barrier.wait()
            for i in range(PER_THREAD):
                sink.append("e", {"thread": t, "i": i})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    pool = [threading.Thread(target=work, args=(t,)) for t in range(THREADS)]
    for th in pool:
        th.start()
    for th in pool:
        th.join(60)
    assert not errors, errors
    assert not any(th.is_alive() for th in pool)


def _run_seam():
    """Inject an unsafe sink, resolve it through the seam, drive it. Returns (built, got)."""
    built = _UnsafeSink()
    event_sink.set_default_event_sink_factory(lambda path=None: built)
    got = event_sink.get_event_sink()
    _drive(got)
    return built, got


# ---------------------------------------------------------------- AC1

def test_ac1_seam_serializes_unsafe_injected_sink():
    sink, _ = _run_seam()
    assert sink.max_inside == 1
    assert sink.count == TOTAL
    assert len(sink.events) == TOTAL
    sent = {(t, i) for t in range(THREADS) for i in range(PER_THREAD)}
    seen = [(e["payload"]["thread"], e["payload"]["i"]) for e in sink.events]
    assert len(seen) == TOTAL and set(seen) == sent
    assert all(e["event_type"] == "e" for e in sink.events)
    assert sorted(e["seq"] for e in sink.events) == list(range(TOTAL))


def test_ac1b_fresh_wrapper_per_emit_over_one_singleton_sink():
    built = _UnsafeSink()
    event_sink.set_default_event_sink_factory(lambda path=None: built)

    class _PerEmit:
        def append(self, event_type, payload, run_id=None):
            return event_sink.get_event_sink().append(event_type, payload, run_id=run_id)

    _drive(_PerEmit())
    assert built.max_inside == 1
    assert built.count == TOTAL
    assert len(built.events) == TOTAL
    sent = {(t, i) for t in range(THREADS) for i in range(PER_THREAD)}
    seen = [(e["payload"]["thread"], e["payload"]["i"]) for e in built.events]
    assert len(seen) == TOTAL and set(seen) == sent
    assert all(e["event_type"] == "e" for e in built.events)
    assert sorted(e["seq"] for e in built.events) == list(range(TOTAL))


# ---------------------------------------------------------------- AC2

@pytest.mark.parametrize("name", [VAR, "BD_EVENT_SINK_SERIALIZE"])
def test_ac2_switch_off_returns_factory_object_and_direct_emit(monkeypatch, name):
    control, _ = _run_seam()  # control: on by default serializes
    assert control.max_inside == 1
    event_sink.reset_default_event_sink_factory()
    monkeypatch.setenv(name, "0")
    built, got = _run_seam()
    assert got is built
    assert built.max_inside >= 2


@pytest.mark.parametrize("value", ["1", "", "off"])
def test_ac2_other_values_keep_serialization_on(monkeypatch, value):
    monkeypatch.setenv(VAR, value)
    built, got = _run_seam()
    assert got is not built
    assert built.max_inside == 1


# ---------------------------------------------------------------- AC3

def test_ac3_file_backed_unsafe_sink_lines_stay_whole(tmp_path):
    log = tmp_path / "events.jsonl"
    sink = _FileSink(log)
    event_sink.set_default_event_sink_factory(lambda path=None: sink)
    _drive(event_sink.get_event_sink())
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == TOTAL
    parsed = [json.loads(line) for line in lines]
    seen = [(p["payload"]["thread"], p["payload"]["i"]) for p in parsed]
    assert sorted(seen) == sorted((t, i) for t in range(THREADS) for i in range(PER_THREAD))


def test_ac3_stock_path_is_unwrapped_and_loses_nothing(tmp_path):
    log = tmp_path / "events.jsonl"
    sink = event_sink.get_event_sink(log)
    assert type(sink) is EventLog
    barrier = threading.Barrier(THREADS, timeout=30)

    def work(t: int) -> None:
        barrier.wait()
        for i in range(PER_THREAD):
            sink.append("e", {"thread": t, "i": i})

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        for f in [pool.submit(work, t) for t in range(THREADS)]:
            f.result(60)
    events = EventLog(log).read_all()
    assert len(events) == TOTAL
    assert {(e["payload"]["thread"], e["payload"]["i"]) for e in events} == {
        (t, i) for t in range(THREADS) for i in range(PER_THREAD)}
    # control: an injected (non-stock) factory IS wrapped
    event_sink.set_default_event_sink_factory(lambda path=None: _UnsafeSink())
    assert isinstance(event_sink.get_event_sink(log), event_sink.SerializedEventSink)


# ---------------------------------------------------------------- AC4

class _Inner:
    path = "inner-path"

    def __init__(self):
        self.raise_next = False

    def append(self, event_type, payload, run_id=None):
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("inner boom")
        return {"echo": (event_type, payload, run_id)}

    def read_all(self):
        return ["sentinel"]


def test_ac4_wrapper_contract():
    inner = _Inner()
    wrapped = event_sink.SerializedEventSink(inner)
    assert isinstance(wrapped, event_sink.EventSink)
    assert wrapped.append("t", {"a": 1}, run_id="r") == {"echo": ("t", {"a": 1}, "r")}
    assert wrapped.path == "inner-path"
    assert wrapped.read_all() == ["sentinel"]


def test_ac4_exception_propagates_and_lock_is_released():
    inner = _Inner()
    wrapped = event_sink.SerializedEventSink(inner)
    inner.raise_next = True
    with pytest.raises(RuntimeError, match="inner boom"):
        wrapped.append("t", {})
    out: list = []
    th = threading.Thread(target=lambda: out.append(wrapped.append("t", {"ok": 1})))
    th.start()
    th.join(10)
    assert not th.is_alive()
    assert out == [{"echo": ("t", {"ok": 1}, None)}]


def test_ac4_already_wrapped_sink_is_not_wrapped_twice():
    inner = _Inner()
    pre = event_sink.SerializedEventSink(inner)
    event_sink.set_default_event_sink_factory(lambda path=None: pre)
    got = event_sink.get_event_sink()
    assert got is pre
    assert got._inner is inner


def test_ac4b_reentrant_nested_wrappers_on_same_thread_complete():
    inner_b = _Inner()
    wrapper_b = event_sink.SerializedEventSink(inner_b)
    received_a: list = []
    received_b: list = []
    orig_b = inner_b.append

    def b_append(event_type, payload, run_id=None):
        received_b.append((event_type, payload))
        return orig_b(event_type, payload, run_id)

    inner_b.append = b_append

    class _InnerA:
        def append(self, event_type, payload, run_id=None):
            received_a.append((event_type, payload))
            return wrapper_b.append(event_type, payload, run_id=run_id)

    wrapper_a = event_sink.SerializedEventSink(_InnerA())
    out: list = []
    th = threading.Thread(target=lambda: out.append(wrapper_a.append("t", {"n": 1})), daemon=True)
    th.start()
    th.join(10)
    assert not th.is_alive(), "nested append deadlocked: lock must be re-entrant"
    assert received_a == [("t", {"n": 1})]
    assert received_b == [("t", {"n": 1})]
    assert out == [{"echo": ("t", {"n": 1}, None)}]


# ---------------------------------------------------------------- AC5

def test_ac5_contract_documents_thread_safety():
    doc = event_sink.EventSink.__doc__ or ""
    assert "thread" in doc.lower()
    assert "serializ" in doc.lower()
    assert "thread" in (event_sink.set_default_event_sink_factory.__doc__ or "").lower()
    assert hasattr(event_sink, "SerializedEventSink")  # contract names a real wrapper


# ---------------------------------------------------------------- AC6

def test_ac6_provider_function_default_and_off(monkeypatch):
    assert config_provider.event_sink_serialize_enabled() is True
    monkeypatch.setenv(VAR, "0")
    assert config_provider.event_sink_serialize_enabled() is False


def test_ac6_provider_without_method_is_enabled(monkeypatch):
    assert hasattr(config_provider, "event_sink_serialize_enabled")
    monkeypatch.setattr(config_provider, "get_config", lambda: SimpleNamespace())
    assert config_provider.event_sink_serialize_enabled() is True


def test_ac6_flag_catalogued():
    assert VAR in flags_catalog.FLAGS, "serialize flag must be catalogued"
    entry = flags_catalog.FLAGS[VAR]
    assert entry.get("kind") == "gate"
    assert isinstance(entry.get("owner"), str) and entry["owner"].strip()
    assert str(entry.get("provenance", "")).startswith("introduced:")
    m = re.search(r"retire-by:(\d{4}-\d{2}-\d{2})", str(entry.get("description", "")))
    assert m, "description must carry retire-by:YYYY-MM-DD"
    retire = datetime.date.fromisoformat(m.group(1))
    today = datetime.date(2026, 10, 11)
    assert today <= retire <= today + datetime.timedelta(days=45)


def test_ac6_lints_pass_with_flag_catalogued():
    assert VAR in flags_catalog.FLAGS, "serialize flag must be catalogued"
    for argv in (["scripts/flag_owner_lint.py"],
                 ["scripts/flip_horizon.py", "--check", "--today", "2026-10-11"]):
        r = subprocess.run([sys.executable, *argv], capture_output=True, text=True, cwd=str(REPO_ROOT))
        assert r.returncode == 0, f"{argv}: {r.stdout!r} {r.stderr!r}"


def test_ac6_docs_mention_variable():
    assert VAR in flags_catalog.FLAGS, "serialize flag must be catalogued"
    text = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert VAR in text


def test_ac6_changelog_added_block_mentions_variable_once_unreleased():
    from helpers.changelog import is_unreleased, parse_sections, read_changelog, require_entry  # noqa: PLC0415

    text = read_changelog()
    require_entry(text, VAR, block="Added")
    assert sum(1 for s in parse_sections(text) if is_unreleased(s)) == 1
