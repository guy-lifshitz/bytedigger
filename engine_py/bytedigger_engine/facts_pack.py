"""facts_pack.py — deterministic repo facts for a prompt, collected before the model runs (bd#86).

Public API:
  Seeds                 — dataclass(files, symbols): what a text points at
  seed_tokens(text)     -> Seeds
  collect(repo_root, text, *, graph_json=None, ledger=None) -> dict   (pure, JSON-able)
  render(pack, audience) -> str
  facts_block(scratchpad, repo_root, text, audience) -> str  (cached; the builders' entry point)
  facts_block_for(ctx, scratchpad, text, audience) -> str    (resolves repo_root from ctx)
  spec_facts_block(ctx, scratchpad, spec_path, audience) -> str  (seeded from question + spec)
  unanchored_criteria(spec_text, repo_root) -> list[dict]
  defined_names(repo_root) -> list[str]

Stdlib only. Spec: docs/decisions/2026-09-27-bd86-fact-pack.md.
"""
from __future__ import annotations

import builtins
import difflib
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bytedigger_engine import known_reds_ledger, spec_cite, telemetry_ctx
from bytedigger_engine.config_provider import get_config
from bytedigger_engine.io_utils import atomic_write
from bytedigger_engine.lib.util.path_classifier import _is_test_path

logger = logging.getLogger(__name__)

FACTS_HEADER = "FACTS (collected deterministically before this call — verify, do not re-derive):"

_LEADS = {
    "spec": (
        "Cite SYMBOLS where they are defined. A name under NOT FOUND IN REPO does not exist: "
        "declare it under `## Symbols this spec INTRODUCES` or do not cite it."
    ),
    "red": "EXISTING TESTS already exist: do not duplicate or delete them; extend them where the spec says so.",
    "gate": "Check the RED tests and the spec against these facts; a claim that contradicts them is a finding.",
    "green": "EXISTING TESTS must stay green. KNOWN REDS are not yours to fix. Do not edit tests.",
    "review": "Check the change against these facts: callers in GRAPH and EXISTING TESTS are the blast radius.",
}

MAX_SEED_FILES = 20
MAX_SEED_SYMBOLS = 40
MAX_DEFS_PER_SYMBOL = 5
MAX_NEAR = 3
MAX_TEST_FILES = 25
MAX_TEST_IDS = 30
MAX_GRAPH_EDGES = 10
RENDER_CAP = 8000
_TRUNCATED = "(truncated)"
_LEDGER_REL = "known-reds.md"
_GRAPH_REL = Path("graphify-out") / "graph.json"
_PACK_KEYS = frozenset({"seeds", "files", "symbols", "unresolved", "tests", "graph", "known_reds"})

_CALL_TOKEN_RE = re.compile(r"(?<![\w.`])([A-Za-z_]\w*)\(\)")
_PY_DEF_RES = (
    spec_cite._DECLARED_DEF_RE,
    spec_cite._DECLARED_CLASS_RE,
    re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)"),
)
_JS_DEF_RES = (
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)"),
)
_SH_DEF_RES = (re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)"),)
_DEF_RES = {".py": _PY_DEF_RES, ".ts": _JS_DEF_RES, ".tsx": _JS_DEF_RES, ".js": _JS_DEF_RES, ".sh": _SH_DEF_RES}
_PY_TEST_ID_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)", re.MULTILINE)
_JS_TEST_ID_RE = re.compile(r"\b(?:test|it)\(\s*[\"'`]([^\"'`]+)[\"'`]")


_BUILTINS = frozenset(dir(builtins))


@dataclass(frozen=True)
class Seeds:
    files: tuple[str, ...]
    symbols: tuple[str, ...]


def _is_path(token: str) -> bool:
    return bool(spec_cite._CODE_FILE_RE.fullmatch(token)) and spec_cite._is_file_token(token)


def seed_tokens(text: str) -> Seeds:
    """The code files and symbols `text` names: code paths, backtick symbols, bare `name()` calls."""
    files: set[str] = set()
    for m in spec_cite._CODE_FILE_RE.finditer(text):
        if spec_cite._is_file_token(m.group(0)):
            files.add(spec_cite._norm_path(m.group(0)))
    symbols: set[str] = set()
    for tok in spec_cite._BACKTICK_RE.findall(text):
        tok = tok.strip()
        if _is_path(tok) or not spec_cite._is_valid_symbol(tok):
            continue
        symbols.add(tok.removesuffix("()"))
    # a bare `len()` / `print()` in prose names nothing in the repo and would match every test
    symbols.update(t for t in _CALL_TOKEN_RE.findall(text) if t not in _BUILTINS)
    return Seeds(
        files=tuple(sorted(files)[:MAX_SEED_FILES]),
        # all-caps constants (E_*, HAL_*) go last, so the cap drops them before function names
        symbols=tuple(sorted(symbols, key=lambda n: (n.isupper(), n))[:MAX_SEED_SYMBOLS]),
    )


def _leaf(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1]


def _module_ref_re(seed_file: str) -> re.Pattern[str]:
    """A test references a seed file by its path, or by importing its module stem."""
    stem = Path(seed_file).stem
    return re.compile(
        re.escape(seed_file)
        + r"|^\s*(?:from|import)\s+[\w.]*\b" + re.escape(stem) + r"\b"
        + r"|\bfrom\s+[\"'][^\"']*\b" + re.escape(stem) + r"[\"']",
        re.MULTILINE,
    )


def _walk(repo_root: Path, paths: list[Path] | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in spec_cite._iter_code_files(repo_root) if paths is None else paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append((path.relative_to(repo_root).as_posix(), text))
    return out


def _definitions(files: list[tuple[str, str]]) -> dict[str, list[str]]:
    defs: dict[str, list[str]] = {}
    for rel, text in files:
        regexes = _DEF_RES.get(Path(rel).suffix, ())
        if not regexes:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in regexes:
                m = rx.match(line)
                if m:
                    defs.setdefault(m.group(1), []).append(f"{rel}:{line_no}")
                    break
    return defs


def defined_names(repo_root: Path) -> list[str]:
    """Every name the repo defines (def/class/module-level assignment and the ts/sh equivalents)."""
    return sorted(_definitions(_walk(Path(repo_root))))


def near_names(name: str, names: list[str]) -> list[str]:
    return difflib.get_close_matches(_leaf(name), names, n=MAX_NEAR, cutoff=0.8)


def _graph_path(repo_root: Path, graph_json: Path | None) -> Path | None:
    """The explicit graph, else the repo's own, else `$GRAPHIFY_OUT` (which a host may pin
    to another tree, so it never shadows the repo-local graph)."""
    candidates = [graph_json] if graph_json is not None else []
    candidates.append(repo_root / _GRAPH_REL)
    if os.environ.get("GRAPHIFY_OUT"):
        candidates.append(Path(os.environ["GRAPHIFY_OUT"]) / "graph.json")
    return next((p for p in candidates if p is not None and p.is_file()), None)


def _graph_facts(repo_root: Path, symbols: tuple[str, ...], graph_json: Path | None) -> dict[str, Any]:
    path = _graph_path(repo_root, graph_json)
    if path is None:
        return {"status": "absent", "path": None, "nodes": []}
    try:
        return {"status": "ok", "path": str(path), "nodes": _graph_nodes(path, symbols)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("facts pack: graph %s unreadable: %s", path, exc)
        return {"status": "unreadable", "path": str(path), "nodes": [], "error": f"{type(exc).__name__}: {exc}"}


def _graph_nodes(path: Path, symbols: tuple[str, ...]) -> list[dict[str, Any]]:
    graph = json.loads(path.read_text(encoding="utf-8"))
    nodes = graph["nodes"]
    links = graph.get("links") or graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise TypeError("graph nodes/links are not lists")
    labels = {n.get("id"): str(n.get("label", "")) for n in nodes if isinstance(n, dict)}
    callers_of: dict[Any, set[str]] = {}
    callees_of: dict[Any, set[str]] = {}
    for link in links:
        if link.get("relation") == "calls":
            src, dst = link.get("source"), link.get("target")
            callers_of.setdefault(dst, set()).add(labels.get(src, ""))
            callees_of.setdefault(src, set()).add(labels.get(dst, ""))
    wanted = {_leaf(s): s for s in symbols}
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        label = str(node.get("label", ""))
        seed = wanted.get(label.removesuffix("()"))
        if seed is None:
            continue
        nid = node.get("id")
        callers = sorted(callers_of.get(nid, set()) - {""})
        callees = sorted(callees_of.get(nid, set()) - {""})
        out.append({
            "symbol": seed,
            "source": f"{node.get('source_file', '')}:{str(node.get('source_location', '')).lstrip('L')}",
            "callers": callers[:MAX_GRAPH_EDGES],
            "callees": callees[:MAX_GRAPH_EDGES],
        })
    out.sort(key=lambda n: (n["symbol"], n["source"]))
    return out


def _known_reds(repo_root: Path, ledger: Path | None, needles: list[str]) -> dict[str, Any]:
    path = ledger if ledger is not None else repo_root / _LEDGER_REL
    if not path.is_file():
        return {"status": "absent", "path": str(path), "rows": []}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("facts pack: known-reds ledger %s unreadable: %s", path, exc)
        return {"status": "unreadable", "path": str(path), "rows": [], "error": f"{type(exc).__name__}: {exc}"}
    rows = [
        {"line": line_no, "cells": cells}
        for line_no, cells in known_reds_ledger.parse_table_rows(text)
        if any(n and n in cell for cell in cells for n in needles)
    ]
    return {"status": "ok", "path": str(path), "rows": rows}


def collect(
    repo_root: Path | str,
    text: str,
    *,
    graph_json: Path | None = None,
    ledger: Path | None = None,
    paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Collect the facts `text` points at in `repo_root`. Pure and deterministic.
    `paths` is the code-file list when the caller already walked the repo."""
    root = Path(repo_root)
    seeds = seed_tokens(text)
    files = _walk(root, paths)
    defs = _definitions(files)
    # uncached on purpose: spec_cite's per-process memo predates RED/GREEN edits
    index = spec_cite._index_tokens(body for _rel, body in files)
    names = sorted(defs)

    symbols, unresolved = [], []
    for sym in seeds.symbols:
        if spec_cite._symbol_in_repo(sym, index):
            symbols.append({"name": sym, "defined_at": defs.get(_leaf(sym), [])[:MAX_DEFS_PER_SYMBOL]})
        else:
            unresolved.append({"name": sym, "near": near_names(sym, names)})

    sym_re = (
        re.compile(r"\b(?:" + "|".join(re.escape(_leaf(s)) for s in seeds.symbols) + r")\b")
        if seeds.symbols else None
    )
    file_res = {f: _module_ref_re(f) for f in seeds.files}
    tests: list[dict[str, Any]] = []
    for rel, body in files:
        if not _is_test_path(rel):
            continue
        refs = set(sym_re.findall(body)) if sym_re else set()
        refs.update(f for f, rx in file_res.items() if rel != f and rx.search(body))
        if not refs:
            continue
        ids = _PY_TEST_ID_RE.findall(body) if rel.endswith(".py") else _JS_TEST_ID_RE.findall(body)
        tests.append({"file": rel, "references": sorted(refs), "test_ids": ids[:MAX_TEST_IDS]})
    # most-referencing files first, so the cap drops incidental matches, not the module's tests
    tests = sorted(tests, key=lambda t: (-len(t["references"]), t["file"]))[:MAX_TEST_FILES]

    needles: list[str] = [str(t["file"]) for t in tests] + list(seeds.files) + list(seeds.symbols)
    return {
        "version": 1,
        "seeds": {"files": list(seeds.files), "symbols": list(seeds.symbols)},
        "files": [{"path": f, "exists": (root / f).is_file()} for f in seeds.files],
        "symbols": symbols,
        "unresolved": unresolved,
        "tests": tests,
        "graph": _graph_facts(root, seeds.symbols, graph_json),
        "known_reds": _known_reds(root, ledger, needles),
    }


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"{title}:"] + ([f"  - {ln}" for ln in lines] or ["  (none)"])


def _why(section: dict[str, Any]) -> str:
    return f": {section['error']}" if section.get("error") else ""


def render(pack: dict[str, Any], audience: str) -> str:
    """One prompt block; every section is present, an empty one reads `(none)`."""
    graph = pack["graph"]
    kr = pack["known_reds"]
    lines = [FACTS_HEADER, _LEADS[audience]]
    lines += _section("SYMBOLS", [
        f"{s['name']} — {', '.join(s['defined_at']) or 'mentioned, no definition found'}"
        for s in pack["symbols"]
    ])
    lines += _section("NOT FOUND IN REPO", [
        u["name"] + (f" (near: {', '.join(u['near'])})" if u["near"] else "")
        for u in pack["unresolved"]
    ])
    lines += _section("FILES", [
        f"{f['path']} ({'exists' if f['exists'] else 'does not exist'})" for f in pack["files"]
    ])
    lines += _section("EXISTING TESTS", [
        f"{t['file']} [{', '.join(t['references'])}]: {', '.join(t['test_ids']) or '(no test ids)'}"
        for t in pack["tests"]
    ])
    lines += _section(f"GRAPH ({graph['status']}{_why(graph)})", [
        f"{n['symbol']} @ {n['source']} — callers: {', '.join(n['callers']) or '-'}; "
        f"callees: {', '.join(n['callees']) or '-'}"
        for n in graph["nodes"]
    ])
    lines += _section(f"KNOWN REDS ({kr['status']}{_why(kr)})", [
        f"line {r['line']}: {' | '.join(r['cells'])}" for r in kr["rows"]
    ])
    out: list[str] = []
    size = 0
    budget = RENDER_CAP - len(_TRUNCATED) - 1
    for ln in lines:
        if size + len(ln) + 1 > budget:
            out.append(_TRUNCATED)
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out)


def _emit_safe(event_type: str, payload: dict[str, Any]) -> None:
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break a prompt build
        logger.warning("telemetry append failed for %s: %s", event_type, exc)


def _failed(audience: str, exc: BaseException) -> str:
    _emit_safe("facts_pack_failed", {"audience": audience, "error": f"{type(exc).__name__}: {exc}"})
    return ""


def _read_cache(cache: Path) -> dict[str, Any] | None:
    """A cached pack, or None when absent or unparseable (a torn write is recollected, not served)."""
    try:
        pack = json.loads(cache.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("facts pack cache %s unreadable, recollecting: %s", cache, exc)
        return None
    if not (isinstance(pack, dict) and pack.get("version") == 1 and _PACK_KEYS <= pack.keys()):
        logger.warning("facts pack cache %s has the wrong shape, recollecting", cache)
        return None
    return pack



def _repo_fingerprint(root: Path, paths: list[Path]) -> str:
    """Path, size and mtime of every indexed file plus the ledger and graph: the cache key
    changes when RED, GREEN or a fix edits the repo, so no stage is served stale facts."""
    h = hashlib.sha256()
    graph = _graph_path(root, None)
    extra = [root / _LEDGER_REL] + ([graph] if graph is not None else [])
    for path in [*paths, *extra]:
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{path}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def facts_block(scratchpad: Path | str, repo_root: Path | str, text: str, audience: str) -> str:
    """The fact block for one prompt; the pack is cached per (repo_root, repo fingerprint, text)
    under `<scratchpad>/facts/`, so stages over an unchanged repo share one collection.

    Returns "" when HAL_FACTS_PACK=0, and on any failure (logged and emitted as
    `facts_pack_failed`): missing context degrades a prompt, it does not end a build.
    """
    if not get_config().gate_enabled("HAL_FACTS_PACK"):
        return ""
    try:
        root = Path(repo_root).resolve()
        paths = spec_cite._iter_code_files(root)
        key = hashlib.sha256(f"{root}\0{_repo_fingerprint(root, paths)}\0{text}".encode()).hexdigest()[:16]
        cache = Path(scratchpad) / "facts" / f"{key}.json"  # the pack is audience-independent
        pack = _read_cache(cache)
        cached = pack is not None
        if pack is None:
            pack = collect(root, text, paths=paths)
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(cache, json.dumps(pack, sort_keys=True, indent=1))
            except OSError as exc:  # the pack is good; only the cache is lost
                logger.warning("facts pack cache %s not written: %s", cache, exc)
        block = render(pack, audience)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("facts pack (%s) failed", audience, exc_info=True)
        return _failed(audience, exc)
    _emit_safe("facts_pack_collected", {
        "audience": audience,
        "cached": cached,
        "symbols": len(pack["symbols"]),
        "unresolved": len(pack["unresolved"]),
        "tests": len(pack["tests"]),
        "graph_status": pack["graph"]["status"],
        "known_reds_status": pack["known_reds"]["status"],
    })
    return block


def facts_block_for(ctx: Any, scratchpad: Path | str, text: str, audience: str) -> str:
    """`facts_block` with the repo root resolved the way spec citations resolve it."""
    from bytedigger_engine.lib.project_root import resolve_project_root  # noqa: PLC0415

    try:
        root, source = resolve_project_root(getattr(ctx, "org_config", None) or {})
    except Exception as exc:  # noqa: BLE001 — same degrade contract as facts_block
        logger.warning("facts pack (%s): repo root unresolved", audience, exc_info=True)
        return _failed(audience, exc)
    if source == "cwd":
        logger.warning("facts pack (%s): repo root fell back to the cwd %s", audience, root)
    return facts_block(scratchpad, root, text, audience)


def spec_facts_block(ctx: Any, scratchpad: Path | str, spec_path: Path | str, audience: str) -> str:
    """`facts_block_for` seeded from the feature request and the spec file (the post-spec stages)."""
    try:
        spec_text = Path(spec_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("facts pack (%s): spec %s unreadable, seeding from the request only: %s",
                       audience, spec_path, exc)
        spec_text = ""
    return facts_block_for(ctx, scratchpad, f"{getattr(ctx, 'question', '') or ''}\n{spec_text}", audience)


# ─── spec-vs-reality: mock-only acceptance criteria ──────────────────────────

_AC_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?\|?\s*(?:\*\*)?(AC[-_ ]?\d+[a-z]?)\b")
_MOCK_WORD_RE = re.compile(
    r"(?i)\b(?:mock(?:s|ed|ing)?|magicmock|patch(?:es|ed|ing)?|monkeypatch\w*|"
    r"stub(?:s|bed|bing)?|fake(?:s|d)?|spy|spies|spied)\b"
)
_MOCK_VERB_RE = re.compile(
    r"(?i)\b(?:mock|mocks|mocking|magicmock|patch|patches|patching|monkeypatch\w*|"
    r"stub|stubs|stubbing|fake|fakes|faking|spy on|spies on)\b"
)
_MOCKED_AFTER_RE = re.compile(r"(?i)^\s+(?:is\s+|are\s+)?(?:mocked|patched|stubbed|faked)\b")
_MOCK_PREFIX_RE = re.compile(r"^(?:mock|fake|stub)_")
_CONTINUATION_RE = re.compile(r"^\s+(?![-*]\s|\||#|(?:\*\*)?AC[-_ ]?\d)\S")
_PATCH_TARGET_RE = re.compile(r"patch(?:\.object)?\(\s*[\"']([\w.]+)[\"']")


def _is_mocked(line: str, start: int, end: int, token: str) -> bool:
    if _MOCK_PREFIX_RE.match(token) or _MOCKED_AFTER_RE.match(line[end:]):
        return True
    for verb in _MOCK_VERB_RE.finditer(line, 0, start):
        gap = line[verb.end():start]
        if not re.search(r"[,;.:]", gap.replace("`", "")) and len(gap.split()) <= 3:
            return True
    return False


def unanchored_criteria(spec_text: str, repo_root: Path | str) -> list[dict[str, Any]]:
    """The mock-only acceptance criteria when none is anchored; `[]` when any is.

    A criterion is mock-only when it carries a mock signal and every anchor on it
    (backtick symbol, code path, patch target) is mocked. A criterion with no mock
    signal asserts on real behaviour and is anchored; so is a mock criterion with
    an unmocked anchor that exists (repo, CREATE file, INTRODUCES symbol).
    """
    root = Path(repo_root)
    criteria: list[tuple[str, int, str]] = []
    last_line_no = 0
    for line_no, line in spec_cite._iter_scannable_lines(spec_text):
        m = _AC_LINE_RE.match(line)
        if m:
            criteria.append((m.group(1), line_no, line))
            last_line_no = line_no
        elif criteria and last_line_no + 1 == line_no and _CONTINUATION_RE.match(line):
            # a wrapped list criterion: an indented line right under it belongs to it
            ac_id, first, text = criteria[-1]
            criteria[-1] = (ac_id, first, text + " " + line.strip())
            last_line_no = line_no
    if not criteria:
        return []
    created = spec_cite.declared_created_files(spec_text)
    introduced = spec_cite.declared_introduced_symbols(spec_text)
    index = spec_cite._repo_symbol_index(root)

    def exists(token: str) -> bool:
        if _is_path(token):
            return (root / spec_cite._norm_path(token)).is_file() or spec_cite._norm_path(token) in created
        return token.removesuffix("()") in introduced or spec_cite._symbol_in_repo(token, index)

    failing: list[dict[str, Any]] = []
    for ac_id, line_no, line in criteria:
        anchors = [
            (m.start(), m.end(), tok) for m in spec_cite._BACKTICK_RE.finditer(line)
            if _is_path(tok := m.group(1).strip()) or spec_cite._is_valid_symbol(tok)
        ]
        mocked = {tok for s, e, tok in anchors if _is_mocked(line, s, e, tok)}
        for target in _PATCH_TARGET_RE.findall(line):  # `patch("a.b.fetch")` mocks `fetch` too
            mocked.update((target, _leaf(target)))
        signal = bool(_MOCK_WORD_RE.search(line)) or bool(mocked)
        if not signal:
            return []
        if any(tok not in mocked and exists(tok) for _s, _e, tok in anchors):
            return []
        failing.append({"id": ac_id, "line": line_no, "mocked": sorted(mocked)})
    return failing
