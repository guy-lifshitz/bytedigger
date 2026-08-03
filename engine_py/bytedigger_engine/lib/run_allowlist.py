"""Per-run spec-§5 `run_allowlist` writer — GH276 step-2 (agreement 1DA29C33).

Populates the state dir's `config/containment-zones.json`'s `run_allowlist` from a
frozen spec's `## Files` (or fallback `... files in scope ...`) section at
spec-freeze, and removes per-run entries at phase-8 cleanup. TTL-GCs entries
from crashed/killed runs (no engine terminal hook on crash covers this path).

Non-core module — NOT added to core_manifest.json. Must not import any
`workflows.*` module (avoids a workflows<->lib import cycle).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bytedigger_engine.config_provider import get_config, env_opt
from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # GH381

_FILES_SECTION_RE = re.compile(r"(?m)^##\s+Files\s*$([\s\S]*?)(?=^##\s|\Z)")
_FILES_IN_SCOPE_HEADER_RE = re.compile(r"(?mi)^#+\s.*files in scope\s*$")
_AUTHORIZED_TEST_EDITS_MARKER_RE = re.compile(r"(?mi)^authorized-test-edits:\s*$")
_VERBS = ("CREATE ", "MODIFY ", "DELETE ", "RENAME ")

DEFAULT_TTL_HOURS = 48


def _extract_path_from_item(item: str) -> str:
    """Enhanced item grammar (cycle-2 amendment): backtick segment wins,
    else strip a trailing parenthetical annotation."""
    m = re.search(r"`([^`]+)`", item)
    if m:
        return m.group(1).strip()
    item = re.sub(r"\s*\([^)]*\)\s*$", "", item).strip()
    if item.startswith("`") and item.endswith("`") and len(item) >= 2:
        item = item[1:-1]
    return item


_VERB_PREFIX_RE = re.compile(r"^(CREATE|MODIFY|DELETE|RENAME)(?::\s*|\s+)")


def _looks_like_path_token(item: str) -> bool:
    """Guard (GH569 spec rule 6): a produced item must contain '/' or '.'
    and no spaces, else it's a prose fragment, not a path."""
    return bool(item) and (" " not in item) and ("/" in item or "." in item)


def _extract_item_grammar(item: str) -> "str | None":
    """GH569 item grammar (rules 3, 5, 6): backtick segment wins; else strip
    a trailing parenthetical annotation and take the first whitespace-
    separated token (paths contain no spaces — kills ' — annotation' tails),
    strip a trailing comma; guard against non-path prose fragments."""
    m = re.search(r"`([^`]+)`", item)
    if m:
        candidate = m.group(1).strip()
        return candidate if _looks_like_path_token(candidate) else None
    item = re.sub(r"\s*\([^)]*\)\s*$", "", item).strip()
    if not item:
        return None
    tokens = item.split()
    if not tokens:
        return None
    candidate = tokens[0].rstrip(",")
    return candidate if _looks_like_path_token(candidate) else None


def _parse_table_row(row: str) -> "str | None":
    """GH569 item grammar (rule 4): markdown table row — skip separator rows
    (`|---|`), take the first cell containing a path-like token
    (backtick-stripped)."""
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    for cell in cells:
        if not cell or re.fullmatch(r"-{2,}", cell):
            continue
        candidate = cell
        m = re.search(r"`([^`]+)`", candidate)
        if m:
            candidate = m.group(1).strip()
        if _looks_like_path_token(candidate):
            return candidate
    return None


def _parse_items(body: str) -> "list[str]":
    """GH569 item grammar — accepts (per non-empty line): optional `- `
    bullet; optional verb prefix (`CREATE|MODIFY|DELETE|RENAME` + `:` or
    whitespace); backtick segment wins; markdown table rows; otherwise
    strip trailing parenthetical then take the first whitespace token.
    Lines yielding no path-like token (prose) are skipped, not garbage
    entries (spec rule 6)."""
    paths: "list[str]" = []
    for line in body.splitlines():
        ls = line.strip()
        if not ls:
            continue
        if ls.startswith("- "):
            ls = ls[2:].strip()
        if ls.startswith("|"):
            item = _parse_table_row(ls)
            if item:
                paths.append(item)
            continue
        vm = _VERB_PREFIX_RE.match(ls)
        if vm:
            ls = ls[vm.end():].strip()
        item = _extract_item_grammar(ls)
        if item:
            paths.append(item)
    return paths


def is_test_shaped(path: str) -> bool:
    """True iff `path` looks like a test file: basename starts with
    `test_`/`test-`, or ends with `.test.<ext>`/`.spec.<ext>`, or any path
    segment equals `tests`/`__tests__` (GH569 spec)."""
    normalized = path.replace("\\", "/")
    segments = [s for s in normalized.split("/") if s]
    if not segments:
        return False
    basename = segments[-1]
    if basename.startswith("test_") or basename.startswith("test-"):
        return True
    if re.search(r"\.(test|spec)\.[^./]+$", basename):
        return True
    if any(seg in ("tests", "__tests__") for seg in segments[:-1]):
        return True
    return False


def derived_test_scope_match(path: str, entries: "list[str]") -> bool:
    """True iff `path` is test-shaped AND lives under the dirname of any
    `entries` item, or up to 2 ancestor levels above that dirname (GH569
    spec's derived test-scope rule). Non-test-shaped paths never match."""
    if not is_test_shaped(path):
        return False
    normalized = os.path.normpath(path.replace("\\", "/")).replace("\\", "/")
    path_parts = normalized.split("/")
    for entry in entries:
        entry_norm = os.path.normpath(entry.replace("\\", "/")).replace("\\", "/")
        entry_dir_parts = entry_norm.split("/")[:-1]
        for up in range(3):  # 0, 1, or 2 ancestor levels above entry's dirname
            if up > len(entry_dir_parts):
                continue
            base_parts = entry_dir_parts[: len(entry_dir_parts) - up]
            if not base_parts:
                continue
            if len(path_parts) > len(base_parts) and path_parts[: len(base_parts)] == base_parts:
                return True
    return False


def parse_spec_files_allowlist(spec_path: "str | None") -> "list[str] | None":
    """Parse a frozen spec's `## Files` (primary) or `... files in scope ...`
    (fallback) section into a path allowlist. None when neither section is
    present, the file is missing, or unreadable."""
    if not spec_path:
        return None
    try:
        text = Path(spec_path).read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return None
    m = _FILES_SECTION_RE.search(text)
    if m:
        return _parse_items(m.group(1))
    header_m = _FILES_IN_SCOPE_HEADER_RE.search(text)
    if header_m:
        rest = text[header_m.end():]
        next_heading = re.search(r"(?m)^#+\s", rest)
        body = rest[:next_heading.start()] if next_heading else rest
        return _parse_items(body)
    return None


def parse_authorized_test_edits(spec_path: str) -> "list[str]":
    """Parse a frozen spec's `authorized-test-edits:` marker block (GH436
    §2.1). Returns the whitelisted test paths verbatim, or `[]` when the
    marker is absent, the file is unreadable, or zero bullets follow
    (fail-closed to today's behavior — reuses the `## Files` item grammar,
    §1g). Never raises."""
    try:
        text = Path(spec_path).read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return []
    m = _AUTHORIZED_TEST_EDITS_MARKER_RE.search(text)
    if not m:
        return []
    rest = text[m.end():]
    block_lines: "list[str]" = []
    for line in rest.splitlines():
        ls = line.strip()
        if not ls:
            block_lines.append(line)
            continue
        if ls.startswith("- "):
            block_lines.append(line)
            continue
        break
    return _parse_items("\n".join(block_lines))


_REASON_SEPARATORS = (" — ", " -- ", " # ")


def parse_authorized_test_edits_with_reasons(spec_path: str) -> "dict[str, str]":
    """Reason-aware sibling of ``parse_authorized_test_edits`` (GH639
    §2.2). Reuses the same marker + block extraction; for each bullet, path
    = existing item grammar, reason = text after the first `` — ``, `` -- ``,
    or `` # `` separator on the raw line item, stripped; no separator => ``""``.
    Missing marker / unreadable file => ``{}``. Never raises."""
    try:
        text = Path(spec_path).read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return {}
    m = _AUTHORIZED_TEST_EDITS_MARKER_RE.search(text)
    if not m:
        return {}
    rest = text[m.end():]
    block_lines: "list[str]" = []
    for line in rest.splitlines():
        ls = line.strip()
        if not ls:
            block_lines.append(line)
            continue
        if ls.startswith("- "):
            block_lines.append(line)
            continue
        break
    result: "dict[str, str]" = {}
    for line in block_lines:
        ls = line.strip()
        if not ls.startswith("- "):
            continue
        item = ls[2:].strip()
        path = _extract_item_grammar(item)
        if not path:
            continue
        best: "tuple[int, int] | None" = None
        for sep in _REASON_SEPARATORS:
            idx = item.find(sep)
            if idx != -1 and (best is None or idx < best[0]):
                best = (idx, len(sep))
        reason = item[best[0] + best[1]:].strip() if best is not None else ""
        result[path] = reason
    return result


def resolve_zones_config_path(cfg: dict) -> "Path | None":
    """Precedence: cfg key > env `HAL_SCOPE_ZONES_CONFIG` > hal-default (iff
    `cfg.get("hal") is True`) > None. hal-default is the hermeticity
    linchpin — a bare test cfg can never resolve the real machine config."""
    explicit = cfg.get("scope_zones_config")
    if explicit:
        return Path(explicit)
    env_val = env_opt("HAL_SCOPE_ZONES_CONFIG")
    if env_val:
        return Path(env_val)
    if cfg.get("hal") is True:
        hal_dir = cfg.get("hal_dir")
        if hal_dir:
            base = Path(str(hal_dir)).expanduser().resolve()
        else:
            base = get_config().hal_root()
        return base / "SHARED" / "config" / "containment-zones.json"
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _gc_meta(meta: dict, now_iso: str, ttl_hours: float) -> dict:
    now_dt = _parse_iso(now_iso)
    out: dict = {}
    for run_id, entry in meta.items():
        ts = entry.get("ts") if isinstance(entry, dict) else None
        keep = True
        if ts:
            try:
                age_hours = (now_dt - _parse_iso(ts)).total_seconds() / 3600.0
                keep = age_hours <= ttl_hours
            except (ValueError, TypeError):
                keep = True
        if keep:
            out[run_id] = entry
    return out


def _union_entries(meta: dict) -> "list[str]":
    union: set = set()
    for entry in meta.values():
        if isinstance(entry, dict):
            for e in entry.get("entries", []) or []:
                union.add(e)
    return sorted(union)


def _atomic_write(cfg_path: Path, data: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(
        dir=str(cfg_path.parent), prefix=cfg_path.name + ".tmp-"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        os.replace(tmp_name, str(cfg_path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_config(cfg_path: Path) -> "dict | None":
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def update_run_allowlist(
    cfg_path: Path,
    run_id: str,
    entries: "list[str]",
    now_iso: "str | None" = None,
    ttl_hours_default: int = DEFAULT_TTL_HOURS,
) -> dict:
    """Replace run_id's meta entry (idempotent), GC stale entries, recompute
    the sorted union, preserve all other keys, atomic write. No write on
    missing file / invalid JSON."""
    data = _load_config(cfg_path)
    if data is None:
        return {"status": "skipped", "reason": "config_unreadable"}
    if now_iso is None:
        now_iso = _now_iso()
    ttl_hours = data.get("run_allowlist_ttl_hours", ttl_hours_default)
    meta = dict(data.get("run_allowlist_meta") or {})
    meta[run_id] = {"ts": now_iso, "entries": list(entries)}
    meta = _gc_meta(meta, now_iso, ttl_hours)
    data["run_allowlist_meta"] = meta
    data["run_allowlist"] = _union_entries(meta)
    _atomic_write(cfg_path, data)
    return {"status": "written", "n_entries": len(data["run_allowlist"])}


def remove_run_allowlist(
    cfg_path: Path,
    run_id: str,
    now_iso: "str | None" = None,
    ttl_hours_default: int = DEFAULT_TTL_HOURS,
) -> dict:
    """Drop run_id's meta entry, GC, recompute union, atomic write. Missing
    file / invalid JSON / absent run_id → skipped no-op.

    GC only sweeps OTHER entries for staleness when `now_iso` is explicitly
    supplied by the caller. Removal is the per-successful-run cleanup path
    (called once per completed build, every build, regardless of any other
    run's age) — an unconditional real-clock sweep here would non-
    deterministically evict unrelated sibling entries depending on wall-clock
    drift between a run's freeze and its own cleanup. The crash/kill-window
    staleness sweep this GC exists for is already covered by
    `update_run_allowlist`, which runs on every subsequent spec-freeze.
    """
    data = _load_config(cfg_path)
    if data is None:
        return {"status": "skipped", "reason": "config_unreadable"}
    meta = dict(data.get("run_allowlist_meta") or {})
    if run_id not in meta:
        return {"status": "skipped", "reason": "run_id_absent"}
    del meta[run_id]
    if now_iso is not None:
        ttl_hours = data.get("run_allowlist_ttl_hours", ttl_hours_default)
        meta = _gc_meta(meta, now_iso, ttl_hours)
    data["run_allowlist_meta"] = meta
    data["run_allowlist"] = _union_entries(meta)
    _atomic_write(cfg_path, data)
    return {"status": "removed", "n_entries": len(data["run_allowlist"])}


def write_run_allowlist_for_spec(cfg: dict, spec_path: str) -> dict:
    """Single call-site surface (§1aa). Guard order: no run_id > unresolvable
    config > spec without a Files section. Never raises — best-effort."""
    try:
        run_id = cfg.get("run_id")
        if not run_id:
            return {"status": "skipped", "reason": "no_run_id"}
        cfg_path = resolve_zones_config_path(cfg)
        if cfg_path is None:
            return {"status": "skipped", "reason": "no_config"}
        entries = parse_spec_files_allowlist(spec_path)
        if entries is None:
            return {"status": "skipped", "reason": "no_files_section"}
        git_cwd = resolve_git_cwd(cfg)
        abs_entries: "list[str]" = []
        for e in entries:
            p = Path(e)
            if p.is_absolute() or e.startswith("~"):
                abs_entries.append(str(p.expanduser().resolve()))
            else:
                abs_entries.append(str((Path(git_cwd) / e).resolve()))
        return update_run_allowlist(cfg_path, run_id, abs_entries)
    except Exception as exc:  # noqa: BLE001 — best-effort, never breaks caller
        return {"status": "error", "reason": str(exc)}
