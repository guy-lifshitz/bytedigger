"""bd#119 -- one bounded, fail-closed reader for org_config["role_template_path"].

Stdlib plus contracts only. The operator-configured template file is read
through a single file descriptor (stat pre-filter, non-blocking open, fstat
re-check, bounded read loop) and returned as a `RoleTemplate`. Every failure
is a `RoleTemplateError` (a `CodedStepError`, registered code
E_ROLE_TEMPLATE_INVALID) whose message carries the key, a reason token and
the configured path, never file content or OS error text.
"""
from __future__ import annotations

import errno
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bytedigger_engine.contracts import CodedStepError

ROLE_TEMPLATE_KEY: str = "role_template_path"
ROLE_TEMPLATE_MAX_BYTES: int = 65_536
ERROR_CODE: str = "E_ROLE_TEMPLATE_INVALID"
ROLE_TEMPLATE_REASONS: tuple[str, ...] = (
    "bad_value_type", "invalid_path", "missing", "unreadable", "not_regular_file",
    "over_cap", "not_utf8", "contains_nul", "empty",
)

_SUGGESTION = "fix or unset org_config.role_template_path"
_PATH_ECHO_LIMIT = 256
_READ_CHUNK = 65_536


@dataclass(frozen=True)
class RoleTemplate:
    source_id: str
    content: str


class RoleTemplateError(CodedStepError):
    """Template misconfiguration; single-line message, no file content."""

    def __init__(self, reason: str, path: str | None = None, detail: str = "") -> None:
        self.reason = reason
        self.path = path
        message = f"{ROLE_TEMPLATE_KEY}: {reason}"
        if path is not None:
            escaped = path.encode("unicode_escape").decode("ascii")
            if len(escaped) > _PATH_ECHO_LIMIT:
                escaped = escaped[:_PATH_ECHO_LIMIT] + "..."
            message += f": {escaped}"
        if detail:
            message += f" ({detail})"
        super().__init__(ERROR_CODE, message, suggestion=_SUGGESTION, recoverable=False)


def _errno_name(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno, "OSError") if exc.errno is not None else "OSError"


def _os_failure(exc: OSError, value: str) -> RoleTemplateError:
    reason = "missing" if isinstance(exc, (FileNotFoundError, NotADirectoryError)) else "unreadable"
    return RoleTemplateError(reason, value, _errno_name(exc))


def _read_bounded(fd: int, cap: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < cap + 1:
        chunk = os.read(fd, min(_READ_CHUNK, cap + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def load_role_template(org_config: Mapping[str, Any] | None) -> RoleTemplate | None:
    """Return the configured template, None when the key is unset, else raise."""
    value = (org_config or {}).get(ROLE_TEMPLATE_KEY)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise RoleTemplateError("bad_value_type", None, f"got {type(value).__name__}")
    try:
        expanded = str(Path(value).expanduser())
    except RuntimeError:
        raise RoleTemplateError("invalid_path", value, "home directory not resolvable") from None

    try:
        st = os.stat(expanded)
    except ValueError:
        raise RoleTemplateError("invalid_path", value, "embedded NUL") from None
    except OSError as exc:
        raise _os_failure(exc, value) from None
    if not stat.S_ISREG(st.st_mode):
        raise RoleTemplateError("not_regular_file", value)

    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY
    try:
        fd = os.open(expanded, flags)
    except ValueError:
        raise RoleTemplateError("invalid_path", value, "embedded NUL") from None
    except OSError as exc:
        raise _os_failure(exc, value) from None

    cap = ROLE_TEMPLATE_MAX_BYTES
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise RoleTemplateError("not_regular_file", value)
        raw = _read_bounded(fd, cap)
    except OSError as exc:
        raise RoleTemplateError("unreadable", value, _errno_name(exc)) from None
    finally:
        os.close(fd)

    if len(raw) > cap:
        raise RoleTemplateError("over_cap", value, f"{max(fst.st_size, len(raw))} bytes > {cap}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RoleTemplateError("not_utf8", value) from None
    if b"\x00" in raw:
        raise RoleTemplateError("contains_nul", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    stripped = text.rstrip()
    if stripped == "":
        raise RoleTemplateError("empty", value)
    return RoleTemplate(expanded, stripped + "\n\n")
