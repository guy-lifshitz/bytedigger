"""Format-agnostic constitution loader/renderer (GH294 Ship B, 03938C06).

Pure stdlib, core-boundary-clean (no host literals/env — added to
core_manifest.json). Parses SpecKit JSON (`specs/<Project>/constitution.json`)
and plain markdown constitutions into a common `{"title","body","examples"}`
principle shape, supports variant-B layered merge (project overlays global by
matching slug(title)), and renders principles back to deterministic markdown.

Spec: 2026-07-06_03938C06_gh294b_speckit_layered_merge_spec.md §2.1 (agreement 03938C06)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_HEADER_RE = re.compile(r"^#{2,}\s+(.*)$", re.MULTILINE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slug(title: str) -> str:
    """Lowercase; each run of non-alphanumeric chars -> single '-'; strip
    leading/trailing '-'."""
    return _NON_ALNUM_RE.sub("-", title.lower()).strip("-")


def parse_markdown(text: str) -> list[dict]:
    """Split at lines matching `^#{2,}\\s+`; each section -> {title, body,
    examples: []}. Non-blank text before the first header -> one 'Preamble'
    principle. No headers at all + non-blank text -> single 'Preamble'
    principle."""
    matches = list(_HEADER_RE.finditer(text))
    principles: list[dict] = []

    if not matches:
        stripped = text.strip()
        if stripped:
            principles.append({"title": "Preamble", "body": stripped, "examples": []})
        return principles

    preamble = text[: matches[0].start()].strip()
    if preamble:
        principles.append({"title": "Preamble", "body": preamble, "examples": []})

    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        principles.append({"title": title, "body": body, "examples": []})

    return principles


def parse_speckit(text: str) -> tuple[str | None, list[dict]]:
    """`json.loads`; returns (project, principles). Each valid entry (dict
    with truthy 'rule') maps rule->title, rationale (default '')->body,
    examples (default [])->examples. Malformed JSON / non-dict top level ->
    raise ValueError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed SpecKit constitution JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("SpecKit constitution top-level must be a JSON object")

    principles: list[dict] = []
    for entry in data.get("principles") or []:
        if not isinstance(entry, dict):
            continue
        rule = entry.get("rule")
        if not rule:
            continue
        principles.append({
            "title": rule,
            "body": entry.get("rationale", "") or "",
            "examples": entry.get("examples", []) or [],
        })

    return data.get("project"), principles


def merge_layered(base: list[dict], overlay: list[dict]) -> tuple[list[dict], list[str]]:
    """Variant B: key = slug(title). Base principles get source='global'.
    Overlay principle with matching slug replaces body+examples in place
    (source='project', overrides=True); overlay-new -> append in overlay
    order (source='project'); global-only -> keep. Returns (merged,
    overridden_slugs)."""
    merged: list[dict] = []
    index: dict[str, dict] = {}

    for p in base:
        item = dict(p)
        item["source"] = "global"
        merged.append(item)
        index[slug(item["title"])] = item

    overridden: list[str] = []
    for p in overlay:
        key = slug(p["title"])
        existing = index.get(key)
        if existing is not None:
            existing["body"] = p.get("body", "")
            existing["examples"] = p.get("examples", [])
            existing["source"] = "project"
            existing["overrides"] = True
            overridden.append(key)
        else:
            new_item = dict(p)
            new_item["source"] = "project"
            merged.append(new_item)

    return merged, overridden


def render_principles(principles: list[dict]) -> str:
    """Deterministic markdown: `## <title>`, then `*source: <source>*` (or
    `*source: <source> — overrides global*`), then body, then examples as
    `- ` bullets under `Examples:` (omit when empty). Principles lacking
    `source` render `*source: project*`."""
    sections: list[str] = []
    for p in principles:
        title = p["title"]
        body = p.get("body", "")
        examples = p.get("examples") or []
        source = p.get("source", "project")
        overrides = p.get("overrides") is True

        tag = f"*source: {source}" + (" — overrides global" if overrides else "") + "*"

        lines = [f"## {title}", "", tag]
        if body:
            lines.append("")
            lines.append(body)
        if examples:
            lines.append("")
            lines.append("Examples:")
            for ex in examples:
                lines.append(f"- {ex}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections) + "\n"


def load_constitution(path: str) -> str:
    """Read file text; if path ends with '.json': parse_speckit ->
    render_principles (entries source='project'); on ValueError/KeyError ->
    return raw text unchanged (degrade, never raise past this point).
    Non-'.json' -> text as-is (byte-identical md passthrough)."""
    text = Path(path).read_text(encoding="utf-8")
    if not path.endswith(".json"):
        return text
    try:
        _project, principles = parse_speckit(text)
    except (ValueError, KeyError):
        return text
    for p in principles:
        p["source"] = "project"
    return render_principles(principles)


def resolve_render(layers: list[dict]) -> dict:
    """`layers` items are {"path","format","layer"}. Returns
    {"markdown": str|None, "overridden": list[str], "merged_count": int,
    "format": str}."""
    if not layers:
        return {"markdown": None, "overridden": [], "merged_count": 0, "format": "none"}

    if len(layers) == 1:
        layer = layers[0]
        try:
            markdown = load_constitution(layer["path"])
        except OSError:
            # is_file()-passing but unreadable (permission/TOCTOU) -> fold,
            # mirrors the 2-layer arm's fold-to-safe below.
            markdown = None
        return {
            "markdown": markdown,
            "overridden": [],
            "merged_count": 0,
            "format": layer["format"],
        }

    global_layer, project_layer = layers[0], layers[1]
    global_text = Path(global_layer["path"]).read_text(encoding="utf-8")
    global_principles = parse_markdown(global_text)

    try:
        project_text = Path(project_layer["path"]).read_text(encoding="utf-8")
        if project_layer.get("format") == "speckit":
            _project_name, project_principles = parse_speckit(project_text)
        else:
            project_principles = parse_markdown(project_text)
    except (OSError, ValueError, KeyError):
        # Project layer read/parse failure -> fold to global-only single-layer
        # behavior (no raise past this point).
        return {
            "markdown": load_constitution(global_layer["path"]),
            "overridden": [],
            "merged_count": 0,
            "format": global_layer["format"],
        }

    merged, overridden = merge_layered(global_principles, project_principles)
    return {
        "markdown": render_principles(merged),
        "overridden": overridden,
        "merged_count": len(merged),
        "format": "merged",
    }
