"""bd#119 -- one bounded, fail-closed reader for org_config["role_template_path"].

Stdlib plus contracts only (bd#125: skill_companion, lib.frontmatter and telemetry_ctx
are imported lazily inside functions, for the content bound and the per-run pin). The operator-configured template file is read
through a single file descriptor (stat pre-filter, non-blocking open, fstat
re-check, bounded read loop) and returned as a `RoleTemplate`. Every failure
is a `RoleTemplateError` (a `CodedStepError`, registered code
E_ROLE_TEMPLATE_INVALID) whose message carries the key, a reason token and
the configured path, never file content or OS error text.
"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import threading
from collections import OrderedDict
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
    # bd#125: content bound, per-run pin
    "core_unavailable", "frontmatter_not_allowed", "section_required", "text_outside_section",
    "section_not_overridable", "forbidden_markup", "unclosed_fence", "heading_level_invalid",
    "setext_heading", "invalid_section_title", "duplicate_section", "empty_section", "pin_drift",
)
ROLE_CORE_ID: str = "bytedigger"

_SUGGESTION = "fix or unset org_config.role_template_path"
_PATH_ECHO_LIMIT = 256
_SLUG_ECHO_LIMIT = 64
_READ_CHUNK = 65_536
_PIN_MAX_RUNS = 64
# run_id -> {expanded path: sha256}; oldest run dropped past the cap, a read refreshes its run.
_pins: OrderedDict[str, dict[str, str]] = OrderedDict()
_pin_lock = threading.Lock()


@dataclass(frozen=True)
class RoleTemplate:
    source_id: str
    content: str
    sha256: str = ""


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


def _read_checked(value: Any) -> tuple[str, str, bytes, str]:
    """The bd#119 byte checks: (value, expanded path, raw bytes, normalised text stripped)."""
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
    return value, expanded, raw, stripped


def _core_overridable(value: str) -> set[str]:
    """Slugs of the core's `metadata.overridable`; core_unavailable when the bound cannot be checked."""
    from bytedigger_engine.skill_companion import _check_core, _decode, default_plugin_root

    rel = f"skills/{ROLE_CORE_ID}/SKILL.md"
    try:
        with open(os.path.join(default_plugin_root(), rel), "rb") as fh:
            core_text = _decode(fh.read())
    except OSError:
        raise RoleTemplateError("core_unavailable", value, "core skill unreadable") from None
    errors: list[dict[str, str]] = []
    _, overridable = _check_core(core_text, rel, errors)
    if errors or not overridable:
        raise RoleTemplateError("core_unavailable", value, "core skill has no usable overridable set")
    return overridable


def _check_content(text: str, value: str) -> None:
    """Raise the first content violation (documented order); detail counts the rest."""
    from bytedigger_engine.lib.frontmatter import BOM, FrontmatterError, body_start, parse_frontmatter
    from bytedigger_engine.skill_companion import _Doc, _heading_errors

    overridable = _core_overridable(value)
    found: list[tuple[str, str]] = []
    unbom = text[len(BOM):] if text.startswith(BOM) else text
    try:
        if parse_frontmatter(text) is not None or unbom.split("\n", 1)[0].rstrip() == "---":
            found.append(("frontmatter_not_allowed", ""))
    except FrontmatterError:
        found.append(("frontmatter_not_allowed", ""))
    doc = _Doc(text)
    if not doc.sections:
        if len(overridable) != 1:
            found.append(("section_required", ""))
        else:
            # The whole text is the body of the one overridable section.
            bare = text[len(BOM):] if text.startswith(BOM) else text
            doc = _Doc(f"## {next(iter(overridable))}\n{bare}")
    else:
        first = doc.sections[0].heading
        if any(ln.strip() for ln in doc.lines[body_start(doc.lines):first]):
            found.append(("text_outside_section", ""))
        for sec in doc.sections:
            if sec.slug and sec.slug not in overridable:
                found.append(("section_not_overridable", sec.slug))
    if doc.h1_after_h2:
        found.append(("heading_level_invalid", ""))
    heading_errors: list[dict[str, str]] = []
    _heading_errors(doc, value, heading_errors)
    found.extend((e["reason"], "") for e in heading_errors)
    seen: set[str] = set()
    for sec in doc.sections:
        body = doc.body(sec)
        if sec.unclosed_fence:
            found.append(("unclosed_fence", sec.slug))
        if any("<!--" in ln or "-->" in ln for ln in body):
            found.append(("forbidden_markup", sec.slug))
        if sec.slug and sec.slug in seen:
            found.append(("duplicate_section", sec.slug))
        seen.add(sec.slug)
        if not "\n".join(body).strip():
            found.append(("empty_section", sec.slug))
    if not found:
        return
    found.sort(key=lambda item: ROLE_TEMPLATE_REASONS.index(item[0]))  # stable
    reason, detail = found[0]
    detail = detail[:_SLUG_ECHO_LIMIT] or reason.replace("_", " ")
    if len(found) > 1:
        detail += f"; +{len(found) - 1} more"
    raise RoleTemplateError(reason, value, detail)


def _pin_and_check(value: str, expanded: str, digest: str, text: str) -> None:
    """Pin check, content check and pin record under one lock; no current run means no pin."""
    from bytedigger_engine.telemetry_ctx import get_current_run

    run = get_current_run()
    if run is None:
        _check_content(text, value)
        return
    with _pin_lock:
        pins = _pins.get(run.run_id)
        if pins is not None:
            _pins.move_to_end(run.run_id)
            pinned = pins.get(expanded)
            if pinned is not None and pinned != digest:
                raise RoleTemplateError("pin_drift", value, "sha256 changed during the run")
        _check_content(text, value)
        if pins is None:
            pins = _pins[run.run_id] = {}
            while len(_pins) > _PIN_MAX_RUNS:
                _pins.popitem(last=False)
        pins.setdefault(expanded, digest)


def load_role_template(org_config: Mapping[str, Any] | None) -> RoleTemplate | None:
    """Return the configured template, None when the key is unset, else raise."""
    value = (org_config or {}).get(ROLE_TEMPLATE_KEY)
    if value is None or value == "":
        return None
    value, expanded, raw, stripped = _read_checked(value)
    digest = hashlib.sha256(raw).hexdigest()
    _pin_and_check(value, expanded, digest, stripped)
    return RoleTemplate(expanded, stripped + "\n\n", digest)


def role_template_digest(org_config: Mapping[str, Any] | None) -> str | None:
    """None while the key is unset; else sha256 of the raw bytes, or "!<reason>". Never raises."""
    try:
        value = (org_config or {}).get(ROLE_TEMPLATE_KEY)
        if value is None or value == "":
            return None
        return hashlib.sha256(_read_checked(value)[2]).hexdigest()
    except RoleTemplateError as exc:
        return "!" + exc.reason
    except Exception:  # noqa: BLE001 -- the resume hash must never fail on a template problem
        return "!unreadable"
