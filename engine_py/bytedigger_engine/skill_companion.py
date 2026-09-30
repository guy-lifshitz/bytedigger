"""skill_companion.py — a core skill plus a host's local companion (bd#116).

The one place that decides whether a host's local text may extend a ByteDigger
core skill, and what the merged text is.

A core skill is `<plugin-root>/skills/<core-id>/SKILL.md`. It declares the H2
sections a host may extend in `metadata.overridable` (one comma-separated
scalar). A companion is a committed host file
`<repo>/bytedigger/companions/<core-id>.md` whose frontmatter says
`specializes: <core-id>`; each of its H2 sections is appended to the core
section with the same slug, inside `bd:local begin/end` markers. Any error
leaves the core text unchanged (all-or-nothing).

Public API:
  UsageError                                  — bad core id or `--repo` not a top level
  resolve(core_id, repo, plugin_root) -> dict — {core, companion, sections, text,
                                                 companion_sha256, errors}
  main(argv) -> int  (`python3 -m bytedigger_engine.skill_companion {render|check} ...`)

CLI exit codes: 0 valid, 3 invalid (stderr: `E_SKILL_COMPANION_INVALID <reason> <path>`
per error), 2 usage error. Git runs through `verification_registry._git`, which
removes GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE / GIT_OBJECT_DIRECTORY from its
env. Nothing is cached.

Stdlib only. Spec: docs/decisions/2026-09-30-bd116-core-skill-local-companion.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from .lib.frontmatter import BOM, FrontmatterError, body_start, parse_frontmatter
from .verification_registry import _git

ERROR_CODE = "E_SKILL_COMPANION_INVALID"
COMPANION_DIR = "bytedigger/companions"
PREFACE = (
    "> Host-local guidance for this section. It cannot change output schema, "
    "verdict tokens or safety rules."
)

_ID_RE = re.compile(r"[a-z0-9-]+")  # a core id and an overridable entry
_H2_RE = re.compile(r"^ {0,3}## +(.+?)(?: +#+)? *$")
_H1_RE = re.compile(r"^ {0,3}# +")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_SLUG_RUN_RE = re.compile(r"[^a-z0-9]+")
# Core errors that leave the overridable set unusable (stage 7 is skipped).
_UNUSABLE_SET = frozenset({
    "unsupported_frontmatter", "invalid_overridable_entry",
    "core_section_missing", "ambiguous_core_section",
})


class UsageError(ValueError):
    """A bad core id, or a `--repo` that is not its git top level."""


# ─── text model ──────────────────────────────────────────────────────────────


def _keep_lines(text: str) -> list[str]:
    """Lines with their line break. Only LF / CRLF break lines (no str.splitlines)."""
    pieces = text.split("\n")
    keep = [p + "\n" for p in pieces[:-1]]
    if pieces[-1]:
        keep.append(pieces[-1])
    return keep


def _split(text: str) -> list[str]:
    """Lines of `text` without a leading BOM and without their LF / CRLF."""
    if text.startswith(BOM):
        text = text[len(BOM):]
    bare = [k[:-1] if k.endswith("\n") else k for k in _keep_lines(text)]
    return [b[:-1] if b.endswith("\r") else b for b in bare]


def _slug(title: str) -> str:
    return _SLUG_RUN_RE.sub("-", title.lower()).strip("-")


def _fence_opener(line: str) -> tuple[str, int] | None:
    """(fence char, length) when `line` opens a fence; a backtick info string has no backtick."""
    m = _FENCE_OPEN_RE.match(line)
    if m is None:
        return None
    run = m.group(1)
    if run[0] == "`" and "`" in line[m.end():]:
        return None
    return run[0], len(run)


class _Section:
    def __init__(self, title: str, heading: int) -> None:
        self.title = title
        self.slug = _slug(title)
        self.heading = heading
        self.end = heading + 1  # exclusive
        self.unclosed_fence = False


class _Doc:
    """Line-level facts of a markdown body (after frontmatter, outside fences)."""

    def __init__(self, text: str) -> None:
        lines = _split(text)
        start = body_start(lines)
        self.lines = lines
        self.sections: list[_Section] = []
        self.h1_after_h2 = False
        self.setext = False
        fence: tuple[str, int] | None = None
        current: _Section | None = None
        for i in range(start, len(lines)):
            line = lines[i]
            if fence is not None:
                m = _FENCE_CLOSE_RE.match(line)
                if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                    fence = None
                continue
            fence = _fence_opener(line)
            if fence is not None:
                continue
            h2 = _H2_RE.match(line)
            if h2:
                if current is not None:
                    current.end = i
                current = _Section(h2.group(1), i)
                self.sections.append(current)
                continue
            if _H1_RE.match(line):
                if current is not None:
                    current.end = i
                    current = None
                    self.h1_after_h2 = True
                elif self.sections:
                    self.h1_after_h2 = True
                continue
            if i > start and _SETEXT_RE.match(line) and lines[i - 1].strip():
                self.setext = True
        if current is not None:
            current.end = len(lines)
            if fence is not None:
                current.unclosed_fence = True

    def body(self, sec: _Section) -> list[str]:
        return self.lines[sec.heading + 1: sec.end]


# ─── git ─────────────────────────────────────────────────────────────────────


def _toplevel(repo: str) -> str:
    """Stage 0: the directory companions are read from; UsageError for a subdirectory."""
    real = os.path.realpath(repo)
    if not os.path.isdir(real):
        raise UsageError(f"--repo is not a directory: {repo}")
    r = _git(["rev-parse", "--show-toplevel"], Path(real))
    if r is None or r.returncode != 0:
        return real  # not a git repo: a companion there cannot be committed
    top = r.stdout.decode("utf-8", errors="replace").strip()
    if not top or os.path.realpath(top) != real:
        raise UsageError(f"--repo must be the git top level ({top or '?'}): {repo}")
    return real


def _committed(top: str, rel: str) -> bytes | None:
    """Stage 4: the working-tree bytes iff they are exactly the committed text."""
    path = os.path.join(top, rel)
    if os.path.realpath(path) != path:
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    head = _git(["cat-file", "--filters", f"HEAD:{rel}"], Path(top))
    if head is None or head.returncode != 0 or head.stdout != data:
        return None
    return data


def _head_sha256(top: str, rel: str) -> str | None:
    blob = _git(["cat-file", "blob", f"HEAD:{rel}"], Path(top))
    if blob is None or blob.returncode != 0:
        return None
    return hashlib.sha256(blob.stdout).hexdigest()


# ─── resolve ─────────────────────────────────────────────────────────────────


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")


def _err(path: str, reason: str, detail: str = "") -> dict[str, str]:
    return {"path": path, "reason": reason, "detail": detail}


def _heading_errors(doc: _Doc, path: str, errors: list[dict[str, str]]) -> None:
    """Rules shared by core and companion: every H2 has a slug; no setext headings."""
    for sec in doc.sections:
        if not sec.slug:
            errors.append(_err(path, "invalid_section_title", sec.title))
    if doc.setext:
        errors.append(_err(path, "setext_heading"))


def _check_core(text: str, path: str, errors: list[dict[str, str]]) -> tuple[_Doc, set[str] | None]:
    """Stage 2. Returns (core doc, overridable set or None when the set is unusable)."""
    first = len(errors)
    doc = _Doc(text)
    try:
        fm = parse_frontmatter(text)
    except FrontmatterError as exc:
        errors.append(_err(path, "unsupported_frontmatter", str(exc)))
        fm = None
    meta = (fm or {}).get("metadata")
    raw = meta.get("overridable") if isinstance(meta, dict) else None
    overridable: set[str] = set()
    if raw is not None:
        counts = Counter(s.slug for s in doc.sections)
        for entry in (e.strip() for e in raw.split(",")):
            if not _ID_RE.fullmatch(entry):
                errors.append(_err(path, "invalid_overridable_entry", repr(entry)))
            elif counts[entry] == 0:
                errors.append(_err(path, "core_section_missing", entry))
            elif counts[entry] > 1:
                errors.append(_err(path, "ambiguous_core_section", entry))
            else:
                overridable.add(entry)
    unusable = any(e["reason"] in _UNUSABLE_SET for e in errors[first:])
    _heading_errors(doc, path, errors)
    return doc, (None if unusable else overridable)


def _check_companion(
    text: str, core_id: str, path: str, overridable: set[str] | None,
    errors: list[dict[str, str]],
) -> _Doc:
    """Stages 5-7 on a committed companion."""
    try:
        fm = parse_frontmatter(text)
    except FrontmatterError as exc:
        errors.append(_err(path, "unsupported_frontmatter", str(exc)))
    else:
        spec = (fm or {}).get("specializes")
        if not spec:
            errors.append(_err(path, "missing_specializes"))
        elif spec != core_id:
            errors.append(_err(path, "specializes_mismatch", str(spec)))
        meta = (fm or {}).get("metadata")
        if isinstance(meta, dict) and "verification" in meta:
            errors.append(_err(path, "companion_sets_verification"))
    doc = _Doc(text)
    if doc.h1_after_h2:
        errors.append(_err(path, "heading_level_invalid"))
    _heading_errors(doc, path, errors)
    seen: set[str] = set()
    for sec in doc.sections:
        body = doc.body(sec)
        if sec.unclosed_fence:
            errors.append(_err(path, "unclosed_fence", sec.title))
        if any("<!--" in ln or "-->" in ln for ln in body):
            errors.append(_err(path, "forbidden_markup", sec.title))
        if sec.slug and sec.slug in seen:
            errors.append(_err(path, "duplicate_section", sec.slug))
        seen.add(sec.slug)
        if not "\n".join(body).strip():
            errors.append(_err(path, "empty_section", sec.title))
    if overridable is not None:
        for sec in doc.sections:
            if sec.slug and sec.slug not in overridable:
                errors.append(_err(path, "section_not_overridable", sec.slug))
    return doc


def _trimmed(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _merge(core_text: str, core: _Doc, comp: _Doc, rel: str) -> str:
    """Append each companion section after the last non-blank line of its core section."""
    bom = BOM if core_text.startswith(BOM) else ""
    keep = _keep_lines(core_text[len(bom):])
    nl = core_text.find("\n")
    eol = "\r\n" if nl > 0 and core_text[nl - 1] == "\r" else "\n"
    by_slug = {s.slug: s for s in core.sections}
    inserts: dict[int, list[str]] = {}
    for sec in comp.sections:
        target = by_slug[sec.slug]
        last = max(i for i in range(target.heading, target.end) if core.lines[i].strip())
        inserts[last] = [
            "", f"<!-- bd:local begin {sec.slug} {rel} -->", PREFACE, "",
            *_trimmed(comp.body(sec)), f"<!-- bd:local end {sec.slug} -->",
        ]
    out: list[str] = [bom]
    for i, line in enumerate(keep):
        out.append(line)
        block = inserts.get(i)
        if block is None:
            continue
        final = i == len(keep) - 1 and not line.endswith("\n")
        if final:
            out.append(eol)
        out.append(eol.join(block) + ("" if final else eol))
    return "".join(out)


def overridable_sections(core_text: str) -> list[tuple[str, str]]:
    """(title, body) of each section `metadata.overridable` lists, in declared order.

    An entry with no matching H2 gives `(entry, "")`; reporting that is `check`'s job, not this
    function's. Raises FrontmatterError for an unreadable `metadata` block. Used by `companion_tune`.
    """
    meta = (parse_frontmatter(core_text) or {}).get("metadata")
    raw = meta.get("overridable") if isinstance(meta, dict) else None
    wanted = [e.strip() for e in (raw or "").split(",") if e.strip()]
    doc = _Doc(core_text)
    by_slug = {sec.slug: sec for sec in reversed(doc.sections)}  # first section wins on a duplicate
    return [
        (by_slug[e].title, "\n".join(doc.body(by_slug[e]))) if e in by_slug else (e, "")
        for e in wanted
    ]


def resolve(core_id: str, repo: str, plugin_root: str) -> dict[str, Any]:
    """Validate the companion of `core_id` in `repo` and return the merged text.

    Returns {"core", "companion" (repo-relative path or None), "sections" (merged
    slugs), "text", "companion_sha256" (sha256 of the HEAD blob or None), "errors"}.
    Any error leaves `text` equal to the core text ("" for `unknown_core`).
    Raises UsageError for a bad `core_id` or a `repo` that is not its git top level.
    """
    if not _ID_RE.fullmatch(core_id or ""):
        raise UsageError(f"invalid core id: {core_id!r}")
    top = _toplevel(repo)
    core_rel = f"skills/{core_id}/SKILL.md"
    comp_rel = f"{COMPANION_DIR}/{core_id}.md"
    result: dict[str, Any] = {
        "core": core_id, "companion": None, "sections": [], "text": "",
        "companion_sha256": None, "errors": [],
    }
    errors: list[dict[str, str]] = result["errors"]
    core_path = os.path.join(plugin_root, core_rel)
    try:
        with open(core_path, "rb") as fh:
            core_text = _decode(fh.read())
    except OSError:
        errors.append(_err(core_rel, "unknown_core"))
        return result
    result["text"] = core_text
    core_doc, overridable = _check_core(core_text, core_rel, errors)
    comp_path = os.path.join(top, comp_rel)
    if not os.path.lexists(comp_path):
        return result
    result["companion"] = comp_rel
    result["companion_sha256"] = _head_sha256(top, comp_rel)
    data = _committed(top, comp_rel)
    if data is None:
        errors.append(_err(comp_rel, "companion_not_committed"))
        return result
    comp_doc = _check_companion(_decode(data), core_id, comp_rel, overridable, errors)
    if errors:
        return result
    result["text"] = _merge(core_text, core_doc, comp_doc, comp_rel)
    result["sections"] = [s.slug for s in comp_doc.sections]
    return result


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _core_arg(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"must match [a-z0-9-]+: {value!r}")
    return value


def default_plugin_root() -> str:
    """Non-empty $CLAUDE_PLUGIN_ROOT, else the parent of the wrapper's directory.

    The wrapper puts its own `../engine_py` on PYTHONPATH, so this module's
    grandparent's parent is the wrapper's parent.
    """
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or str(Path(__file__).resolve().parents[2])


def main(argv: Sequence[str] | None = None) -> int:
    """`{render|check} --core <id> [--repo .] [--plugin-root <dir>] [--json]`."""
    parser = argparse.ArgumentParser(prog="skill-companion")
    parser.add_argument("command", choices=("render", "check"))
    parser.add_argument("--core", required=True, type=_core_arg)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--plugin-root", default=None, dest="plugin_root")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    plugin_root = args.plugin_root or default_plugin_root()
    try:
        res = resolve(args.core, args.repo, plugin_root)
    except UsageError as exc:
        parser.print_usage(sys.stderr)
        sys.stderr.write(f"skill-companion: error: {exc}\n")
        return 2
    errors = res["errors"]
    for e in errors:
        sys.stderr.write(f"{ERROR_CODE} {e['reason']} {e['path']}\n")
    if args.command == "check":
        if args.json_output:
            payload = {k: v for k, v in res.items() if k != "text"}
            sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    elif not errors:
        sys.stdout.buffer.write(res["text"].encode("utf-8", errors="surrogateescape"))
    sys.stdout.flush()
    return 3 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
