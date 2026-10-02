"""The one place an L-checker turns an event dict into its event-type name (bd#155).

`EventLog.append` writes the type under `event_type`; the conformance harness
and the tests build events with `type`. A checker that accepts both shapes
resolves them here, so one event is exactly one type and a new `check_bd_lN`
cannot grow its own reading of the key.

This module has NO import statements on purpose: annotations are string
literals, nothing is needed at runtime, and importing it loads no other module.
"""


def event_type_of(event: "Mapping[str, object]") -> "object":
    """Return the event-type name of `event`.

    A non-empty `str` under `event_type` (the on-disk key) wins; otherwise the
    `type` value (the harness shape) is returned, which may be `None` or any
    other value when the event carries neither key.
    """
    on_disk = event.get("event_type")
    if isinstance(on_disk, str) and on_disk:
        return on_disk
    return event.get("type")
