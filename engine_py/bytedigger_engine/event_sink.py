"""EventSink Protocol + factory seam for HAL workflow engine (E1 026599E4).

Formalises the already-existing event-emission contract into an explicit,
swappable seam. An OSS consumer can inject its own event sink; HAL keeps the
current EventLog by default. Behavior-preserving: zero change to what HAL
emits or where.

§1g: `_DEFAULT_FACTORY` is the single source of truth for default sink
construction. `get_event_sink` is the single resolver — the only place
construction sites should call.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from bytedigger_engine.event_log import EventLog, default_log_path  # noqa: E402 — event_log does not import event_sink


@runtime_checkable
class EventSink(Protocol):
    """Structural protocol matching EventLog.append's public contract.

    Any object with a conforming `.append` method satisfies this protocol
    via `isinstance(obj, EventSink)` without inheriting from this class.
    """

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        ...


# ── Module-global registry (§1g single-source-of-truth) ──────────────────────

def default_event_sink(path: "Any" = None) -> EventSink:
    """Return an EventLog at *path* (or the default path when None).

    This is the default factory implementation — the HAL concrete sink.
    OSS consumers may replace the factory via `set_default_event_sink_factory`.
    """
    return EventLog(path)


# The single mutable registry. Holds a callable (path) -> EventSink.
_DEFAULT_FACTORY = default_event_sink

# Snapshot of the original factory for restoration in reset.
_ORIGINAL_FACTORY = default_event_sink


def get_event_sink(path: "Any" = None) -> EventSink:
    """Return a sink instance from the registered factory (the single resolver).

    With no override registered this returns `EventLog(path)` — behavior
    identical to calling EventLog directly. After `set_default_event_sink_factory`
    is called, returns whatever the injected factory produces.

    Construction sites (run.py, lib/dbos_setup.py) MUST call this instead of
    constructing EventLog directly so that the factory override is respected.
    """
    return _DEFAULT_FACTORY(path)


def set_default_event_sink_factory(factory) -> None:
    """Override the module-global factory used by `get_event_sink`.

    *factory* must be a callable taking an optional *path* argument and
    returning an object satisfying the EventSink protocol.

    This is the OSS injection point — call `reset_default_event_sink_factory`
    in test teardown to avoid global-state pollution between tests (§1i).
    """
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory


def reset_default_event_sink_factory() -> None:
    """Restore `_DEFAULT_FACTORY` to the original EventLog-based default.

    Must be called in test teardown after any `set_default_event_sink_factory`
    call to prevent global-state leakage between tests (§1i singleton-reset).
    """
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = _ORIGINAL_FACTORY
