"""bd#102 — declared host-tool set + honest skip conversion.

All of M1 lives here (spec §2, rev 4): `HOST_TOOLS`, `_HOST_TOOL_AVAILABLE`,
`freeze_host_tool_availability`, `host_tool_skip_reason`, `skip_without`, and
the `pytest_runtest_makereport` hookwrapper body. `tests/conftest.py` only
imports this module (at module scope — pytester's SysModulesSnapshot would
otherwise evict a module first imported inside a test body) and re-exports
the hookwrapper. This module is NOT registered as a pytest plugin itself, so
it must never define `pytest_configure` — that would never run and leave
`_HOST_TOOL_AVAILABLE` empty, silently no-opping the whole mechanism.

Key set is exactly {git, bun, semgrep} — `zsh` is deliberately excluded (see
docs/host-requirements.md): it accounts for zero suite failures and
declaring it would silence the zsh_unavailable graceful-degradation
regression guard.

Availability is frozen ONCE, from `conftest.pytest_configure`, into
`_HOST_TOOL_AVAILABLE`. Neither this hook nor `skip_without` may call
`shutil.which` live afterward: two tests monkeypatch `shutil.which`
process-wide, which would otherwise make a live lookup report a tool absent
on a machine that has it.
"""
from __future__ import annotations

import os
import shutil

import pytest

HOST_TOOLS: dict[str, str] = {
    "git": "the engine and its test suite spawn git for fixture repos, diffs, and commits",
    "bun": "the ts/bun test groups require the bun runtime",
    "semgrep": "the red-lint preflight requires semgrep for pattern scanning",
}

# Frozen once at pytest_configure time (freeze_host_tool_availability). Never
# populated by a live shutil.which() call from the hook or skip_without.
_HOST_TOOL_AVAILABLE: dict[str, bool] = {}


def freeze_host_tool_availability() -> None:
    """Populate `_HOST_TOOL_AVAILABLE` once, from a real `shutil.which` probe.

    Called from inside conftest.py's existing `pytest_configure` (the
    FFEA1913 burn-guard hook) — never from a second `pytest_configure`
    defined in this module.
    """
    for tool in HOST_TOOLS:
        _HOST_TOOL_AVAILABLE[tool] = shutil.which(tool) is not None


def _find_file_not_found(
    exc: BaseException, _seen: set[int] | None = None
) -> FileNotFoundError | None:
    """Walk `exc` itself, then `__cause__`/`__context__`, for a FileNotFoundError."""
    if _seen is None:
        _seen = set()
    if id(exc) in _seen:
        return None
    _seen.add(id(exc))
    if isinstance(exc, FileNotFoundError):
        return exc
    for attr in ("__cause__", "__context__"):
        nxt = getattr(exc, attr, None)
        if isinstance(nxt, BaseException):
            found = _find_file_not_found(nxt, _seen)
            if found is not None:
                return found
    return None


def host_tool_skip_reason(exc: BaseException) -> str | None:
    """Return the skip reason for `exc`, or None if it must not be converted.

    Fires only when: `exc` (or its __cause__/__context__ chain) is a
    FileNotFoundError; `os.path.basename(exc.filename)` is a declared
    HOST_TOOLS key; and the frozen availability map says that tool was
    genuinely absent when the session started.
    """
    fnf = _find_file_not_found(exc)
    if fnf is None:
        return None
    filename = fnf.filename
    if not filename:
        return None
    tool = os.path.basename(filename)
    if tool not in HOST_TOOLS:
        return None
    if _HOST_TOOL_AVAILABLE.get(tool) is not False:
        return None
    return f"requires host tool '{tool}' — see docs/host-requirements.md"


def skip_without(tool: str) -> None:
    """Skip the current test when `tool` is absent per the frozen map.

    Reads `_HOST_TOOL_AVAILABLE` — never a live `shutil.which` probe — so it
    cannot be spoofed by the process-wide `shutil.which` monkeypatches some
    tests install.
    """
    if _HOST_TOOL_AVAILABLE.get(tool) is False:
        pytest.skip(f"requires host tool '{tool}' — see docs/host-requirements.md")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when not in ("setup", "call"):
        return
    if report.outcome != "failed":
        return
    if call.excinfo is None:
        return

    reason = host_tool_skip_reason(call.excinfo.value)
    if reason is None:
        return

    report.outcome = "skipped"
    report.longrepr = (str(report.fspath), None, "Skipped: " + reason)
