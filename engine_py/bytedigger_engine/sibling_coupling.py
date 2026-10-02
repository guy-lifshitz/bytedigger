"""Deterministic sibling-coupling audit: seven channels, one chokepoint (bd#165).

Given the production files a change touches (the scope files), find the test
files that are coupled to them and report which of those the spec does not
cite.  The seven channels are: import, source-read, exec-invocation,
path-literal, value-literal, data-cell and call-site.

``audit()`` is the single chokepoint: it derives keys from the scope files,
runs the channels over the test corpus, reconciles against a spec and returns
rows.  The command line (``main``) and the phase-5 helper only add I/O.

Row layout (6 TSV fields)::

    file <TAB> line <TAB> func <TAB> token <TAB> channel <TAB> verdict

Design constraints: standard library only; no environment reads (every seam is
an explicit parameter or flag); the only subprocess is one optional
``git rev-parse --show-toplevel`` to find the default corpus root; nothing
here writes to stdout or stderr except ``main``; every diagnostic line is
collected in ``AuditResult.warnings``; the text cache lives for one ``audit()``
call, so a long-lived host never sees stale file content.

Known limitation: the corpus file-name pattern is ``test_*.py``,
``*.test.ts`` and ``*.test.sh``; ``*_test.py``, ``*.test.js`` and
``*.spec.ts`` are not collected.
"""

from __future__ import annotations

import argparse
import ast
import glob as globmod
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

JSON_SCHEMA = 1

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

DEFAULT_MIN_LITERAL_LEN = 6
DEFAULT_MAX_KEYS = 200
DEFAULT_MAX_CORPUS = 5000
DEFAULT_MAX_KEY_FILES = 25

CHANNELS = (
    "import",
    "source-read",
    "exec-invocation",
    "path-literal",
    "value-literal",
    "data-cell",
    "call-site",
)

# Path-family precedence: exec > read > bare.
PATH_FAMILY = ("exec-invocation", "source-read", "path-literal")

# Nine spawn primitives, verbatim.  A bare `run(` is NOT on the list.
SPAWN_PRIMITIVES = (
    "spawnSync",
    "Bun.spawn",
    "subprocess.run",
    "subprocess.Popen",
    "execFile",
    "execSync",
    'bash "',
    "sh -c",
    "$(bash ",
)

# Seven read primitives, verbatim.
READ_PRIMITIVES = (
    "read_text(",
    "readFileSync(",
    "readFile(",
    "open(",
    "Bun.file(",
    "$(cat ",
    "inspect.getsource(",
)

# Case-insensitive stop list.
STOP_LIST = frozenset(
    [
        "pytest",
        "canary",
        "status",
        "error",
        "failed",
        "expiry",
        "python3",
        "assert",
        "module",
        "import",
        "return",
        "true",
        "false",
        "none",
        "null",
    ]
)

# Extension -> extractor matrix.
EXT_IMPORT = frozenset([".py", ".ts", ".tsx", ".js", ".mjs"])
EXT_VALUE = frozenset([".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".bash"])
EXT_CALL = frozenset([".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".bash"])
EXT_DATA = frozenset(
    [".md", ".json", ".jsonl", ".txt", ".tsv", ".csv", ".yaml", ".yml"]
)
SUPPORTED_EXTS = EXT_IMPORT | EXT_VALUE | EXT_CALL | EXT_DATA

# Corpus file patterns and prunes.
_TEST_NAME_RE = re.compile(r"^(?:test_.+\.py|.+\.test\.ts|.+\.test\.sh)$")
_PRUNE_DIRNAMES = frozenset([".git", "node_modules", "graphify-out", "memory-backups"])

# Spec-token alternation.
_SPEC_TOKEN_RE = re.compile(r"[\w./-]+\.(?:py|ts|tsx|js|mjs|sh|bash)")
_NOT_IN_SCOPE_RE = re.compile(r"not in scope", re.IGNORECASE)

_DATA_CELL_RE = re.compile(r"^[A-Za-z0-9_./#:-]+$")
_UPPER_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
_PY_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_]\w*)")
_SH_FN_PAREN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\(\)\s*\{")
_SH_FN_WORD_RE = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_-]*)")
_TS_FN_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")
_TS_DECL_RE = re.compile(r"^\s*export\s+(?:const|class)\s+([A-Za-z_$][\w$]*)")
_TS_CONST_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|readonly)\s+([A-Za-z_$][\w$]*)\s*"
    r"(?::[^=]+)?=\s*(\"[^\"]*\"|'[^']*'|`[^`]*`)"
)
_SH_ASSIGN_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)=(?:'([^']*)'|\"([^\"]*)\")\s*$"
)

_PY_FROM_RE = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+")
_PY_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)")

_GRAPH_RELATIONS = frozenset(
    ["calls", "uses", "references", "imports", "imports_from", "method"]
)

# Warning prefixes that escalate to a gate exit under --require-clean.
_GATE_WARNINGS = ("W_UNSUPPORTED_SCOPE_EXT", "W_KEY_CAP", "W_CORPUS_CAP")

_ERR_PARTIAL = "E_PARTIAL_CHANNELS_GATE"
_ERR_INTERNAL = "E_SIBLING_AUDIT_INTERNAL"

Row = Tuple[str, int, str, str, str, str]


class SiblingAuditError(Exception):
    """A fatal, coded audit failure (unknown channel, unreadable input, ...)."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


@dataclass(frozen=True)
class AuditResult:
    rows: Tuple[Row, ...]
    warnings: Tuple[str, ...]
    gate_warn: bool
    partial_channels: bool
    call_site: Dict[str, Any]
    missing: int


class _Ctx(object):
    """State scoped to one audit() call: warnings, text cache, trace file."""

    def __init__(self, trace_path: Optional[str] = None) -> None:
        self.warnings: List[str] = []
        self.cache: Dict[str, Optional[str]] = {}
        self.noted: set = set()
        self.trace_path = trace_path

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def trace(self, line: str) -> None:
        dest = self.trace_path
        if not dest:
            return
        try:
            with open(dest, "a") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            # A dropped trace line is advisory but never silent.
            self.warn("W_TRACE_WRITE_FAILED %s %s" % (dest, _exc_name(exc)))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _exc_name(exc):
    return exc.__class__.__name__


def _raw_text(path, ctx):
    """Decoded file content (bytes -> utf-8 with replacement), or None if unreadable."""
    if path in ctx.cache:
        return ctx.cache[path]
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        ctx.noted.add(("err", path, _exc_name(exc)))
        text = None
    ctx.cache[path] = text
    return text


def _read_text(path, ctx):
    """Read a scope/corpus/spec file, naming any read failure.

    A file that will not read contributes zero keys and zero hits; left silent
    that degrades into a byte-identical "no coupling" clean.
    """
    text = _raw_text(path, ctx)
    if text is None:
        key = ("read", path)
        if key not in ctx.noted:
            ctx.noted.add(key)
            name = "OSError"
            for item in ctx.noted:
                if item[0] == "err" and item[1] == path:
                    name = item[2]
            ctx.warn("W_TEST_READ_FAILED %s %s" % (path, name))
        return ""
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


_SHEBANG_EXT = {
    "bash": ".sh", "sh": ".sh", "zsh": ".sh", "dash": ".sh", "ksh": ".sh",
    "python": ".py", "python3": ".py",
    "bun": ".ts", "deno": ".ts", "tsx": ".ts", "ts-node": ".ts",
    "node": ".js",
}


def _shebang_ext(path):
    """Extension implied by an extensionless script's shebang, else an empty string."""
    try:
        with open(path, "rb") as fh:
            first = fh.read(256).split(b"\n", 1)[0].decode("utf-8", "replace")
    except OSError:
        return ""
    if not first.startswith("#!"):
        return ""
    words = first[2:].split()
    if not words:
        return ""
    interp = os.path.basename(words[0])
    if interp == "env":
        rest = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
        if not rest:
            return ""
        interp = os.path.basename(rest[0])
    if re.match(r"^python3\.\d+$", interp):
        return ".py"
    return _SHEBANG_EXT.get(interp, "")


def _ext(path):
    ext = os.path.splitext(path)[1].lower()
    return ext if ext else _shebang_ext(path)


def _clean_token(value):
    return value.replace("\t", " ").replace("\r", " ").replace("\n", " ")


# ---------------------------------------------------------------------------
# Key distinctiveness
# ---------------------------------------------------------------------------

def is_distinctive_key(
    key,
    min_literal_len=DEFAULT_MIN_LITERAL_LEN,
    channel=None,
    suspend_noise=False,
):
    """Return ``(ok, failing_rule)``.

    Rule 1 (length) applies to every channel, always.  Rules 2 (distinctive)
    and 3 (stop-list) apply only to ``data-cell`` and ``value-literal`` and are
    suspended when the operator lowers ``--min-literal-len`` below its default.
    The lowest-numbered failing rule is reported.
    """
    if not key:
        return (False, 1)
    if len(key) < min_literal_len:
        return (False, 1)
    if channel in ("data-cell", "value-literal") and not suspend_noise:
        distinctive = (
            any(ch.isdigit() for ch in key)
            or any(ch in ":/._-" for ch in key)
            or any(ch.isupper() for ch in key)
            or len(key) >= 16
        )
        if not distinctive:
            return (False, 2)
        if key.strip().lower() in STOP_LIST:
            return (False, 3)
    return (True, None)


# ---------------------------------------------------------------------------
# value-literal extraction
# ---------------------------------------------------------------------------

_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeDecodeError, tokenize.TokenError)


def _python_string_tokens(text, path, ctx):
    """Return ``[(start_line, end_line, value), ...]`` for every STRING token.

    A tokenizer failure is NAMED (``W_SCOPE_PARSE_FAILED``): a scope file that
    will not tokenize yields no ``value-literal`` key, and a silent zero there
    reads exactly like "this file is not coupled".
    """
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.STRING:
                continue
            try:
                value = ast.literal_eval(tok.string)
            except (SyntaxError, ValueError):
                # Not a plain literal (f-string / prefix form): by design not a
                # value-literal key, so there is nothing to report.
                continue
            if isinstance(value, str):
                out.append((tok.start[0], tok.end[0], value))
    except _PARSE_ERRORS as exc:
        ctx.warn("W_SCOPE_PARSE_FAILED %s %s" % (path, _exc_name(exc)))
    return out


_COLLECTION_CTORS = ("frozenset", "set", "list", "tuple", "dict")


def _is_collection_display(node):
    """True when ``node`` is a collection literal / comprehension / constructor.

    Exactly three ``value-literal`` capture shapes exist: a scalar literal, a
    run of adjacent literals, and a mixed ``<literal> + <expr>``.  A
    tuple/list/set/dict display (or a ``frozenset(...)`` style constructor
    call, or a comprehension) is none of those, so its ELEMENTS are not
    coupling keys and the assignment is skipped silently.
    """
    if isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict,
                         ast.ListComp, ast.SetComp, ast.DictComp,
                         ast.GeneratorExp)):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in _COLLECTION_CTORS:
            return True
    return False


def _python_constants(text, path, ctx):
    pairs = []
    partial = []
    try:
        tree = ast.parse(text)
    except _PARSE_ERRORS as exc:
        ctx.warn("W_SCOPE_PARSE_FAILED %s %s" % (path, _exc_name(exc)))
        return pairs, partial
    strings = _python_string_tokens(text, path, ctx)
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names = [stmt.target.id]
            value = stmt.value
        else:
            continue
        if value is None:
            continue
        if _is_collection_display(value):
            # Out of contract: skip silently, no W_PARTIAL_LITERAL.
            continue
        names = [n for n in names if _UPPER_NAME_RE.match(n)]
        if not names:
            continue
        lo = getattr(value, "lineno", stmt.lineno)
        hi = getattr(value, "end_lineno", None) or lo
        segments = [v for (s, e, v) in strings if s >= lo and e <= hi]
        pure = isinstance(value, ast.Constant) and (
            isinstance(value.value, str)
            or (isinstance(value.value, int) and not isinstance(value.value, bool))
        )
        for name in names:
            if pure:
                full = value.value
                pairs.append((name, full if isinstance(full, str) else str(full)))
                if len(segments) > 1:
                    for seg in segments:
                        pairs.append((name, seg))
            else:
                # Mixed <literal> + <expr> / f-string / .format() / %: capture
                # ONLY the literal fragments.  Never synthesise a concatenation
                # across a non-literal operand (that key would be a phantom).
                fragments = []
                for sub in ast.walk(value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        fragments.append(sub.value)
                if not fragments:
                    fragments = list(segments)
                for frag in fragments:
                    pairs.append((name, frag))
                partial.append(name)
    return pairs, partial


def _bash_constants(text):
    pairs = []
    for line in text.splitlines():
        m = _SH_ASSIGN_RE.match(line)
        if not m:
            continue
        value = m.group(2) if m.group(2) is not None else m.group(3)
        if value:
            pairs.append((m.group(1), value))
    return pairs


def _ts_constants(text):
    pairs = []
    for line in text.splitlines():
        m = _TS_CONST_RE.match(line)
        if not m:
            continue
        raw = m.group(2)
        value = raw[1:-1]
        if value:
            pairs.append((m.group(1), value))
    return pairs


def extract_constant_values(path, text=None, ctx=None):
    """Return ``(pairs, partial_names)``: the ``value-literal`` channel keys.

    ``pairs`` is a list of ``(NAME, literal_value)``; ``partial_names`` names
    every constant that is only partly literal (``W_PARTIAL_LITERAL``).
    """
    ctx = ctx or _Ctx()
    if text is None:
        text = _read_text(path, ctx)
    ext = _ext(path)
    if ext == ".py":
        return _python_constants(text, path, ctx)
    if ext in (".sh", ".bash"):
        return _bash_constants(text), []
    if ext in (".ts", ".tsx", ".js", ".mjs"):
        return _ts_constants(text), []
    return [], []


# ---------------------------------------------------------------------------
# data-cell extraction
# ---------------------------------------------------------------------------

def _json_leaves(node, out):
    if isinstance(node, dict):
        for value in node.values():
            _json_leaves(value, out)
    elif isinstance(node, list):
        for value in node:
            _json_leaves(value, out)
    elif isinstance(node, str):
        out.append(node)


def extract_data_cells(path, text=None, ctx=None):
    """Return the candidate ``data-cell`` keys of a data scope file."""
    ctx = ctx or _Ctx()
    if text is None:
        text = _read_text(path, ctx)
    ext = _ext(path)
    cells = []
    if ext == ".md":
        for line in text.splitlines():
            for part in line.split("|"):
                cells.append(part.strip())
    elif ext in (".json", ".jsonl"):
        leaves = []
        if ext == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    _json_leaves(json.loads(line), leaves)
                except ValueError:
                    continue
        else:
            try:
                _json_leaves(json.loads(text), leaves)
            except ValueError:
                leaves = []
        cells.extend(leaves)
    else:
        for line in text.splitlines():
            cells.extend(re.findall(r"`([^`]+)`", line))
            cells.extend(line.split())
    out = []
    seen = set()
    for cell in cells:
        cell = cell.strip()
        if not cell or cell in seen:
            continue
        if not _DATA_CELL_RE.match(cell):
            continue
        seen.add(cell)
        out.append(cell)
    return out


# ---------------------------------------------------------------------------
# defined symbols (call-site)
# ---------------------------------------------------------------------------

def extract_defined_symbols(path, text=None, ctx=None):
    """Return the symbols DEFINED by the scope file (``call-site`` keys)."""
    ctx = ctx or _Ctx()
    if text is None:
        text = _read_text(path, ctx)
    ext = _ext(path)
    names = []
    for line in text.splitlines():
        match = None
        if ext == ".py":
            match = _PY_DEF_RE.match(line) or _PY_CLASS_RE.match(line)
        elif ext in (".sh", ".bash"):
            match = _SH_FN_PAREN_RE.match(line) or _SH_FN_WORD_RE.match(line)
        elif ext in (".ts", ".tsx", ".js", ".mjs"):
            match = _TS_FN_RE.match(line) or _TS_DECL_RE.match(line)
        if match:
            name = match.group(1)
            if name not in names:
                names.append(name)
    return names


# ---------------------------------------------------------------------------
# path family / primitives
# ---------------------------------------------------------------------------

def detect_source_read(text):
    """True iff the test text applies one of the seven read primitives."""
    return any(prim in text for prim in READ_PRIMITIVES)


def detect_exec_invocation(text):
    """True iff the test text applies one of the nine spawn primitives."""
    return any(prim in text for prim in SPAWN_PRIMITIVES)


def classify_path_family(text):
    """Label a path-coupled test file: exec > read > bare."""
    if detect_exec_invocation(text):
        return "exec-invocation"
    if detect_source_read(text):
        return "source-read"
    return "path-literal"


def path_family_keys(scope_path):
    """basename plus the 2-component path suffix."""
    scope_path = scope_path.rstrip("/")
    base = os.path.basename(scope_path)
    keys = [base]
    parent = os.path.basename(os.path.dirname(scope_path))
    if parent:
        keys.append(parent + "/" + base)
    return keys


# ---------------------------------------------------------------------------
# import channel
# ---------------------------------------------------------------------------

def detect_imports(text, stem, path=None):
    """Return the 1-based line numbers on which ``text`` imports ``stem``.

    Python: the module path's LAST dotted component must equal the stem (so a
    dotless ``from callee_mod import X`` matches, and a bare prose mention of
    the stem does not).  TS/JS: ``from "x/S"`` / ``require("x/S")`` with an
    optional ``.ts`` / ``.js`` suffix.
    """
    hits = []
    if not stem:
        return hits
    quoted = re.escape(stem)
    ts_from = re.compile(
        r"""from\s+["'](?:[^"']*/)?%s(?:\.tsx?|\.m?js)?["']""" % quoted
    )
    ts_require = re.compile(
        r"""require\(\s*["'](?:[^"']*/)?%s(?:\.tsx?|\.m?js)?["']""" % quoted
    )
    for idx, line in enumerate(text.splitlines(), 1):
        matched = _PY_FROM_RE.match(line) or _PY_IMPORT_RE.match(line)
        if matched and matched.group(1).split(".")[-1] == stem:
            hits.append(idx)
            continue
        if ts_from.search(line) or ts_require.search(line):
            hits.append(idx)
    return hits


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def _default_root(ctx):
    """Repository top-level, else the current directory (named in a warning)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        ctx.warn("W_GIT_ROOT_UNRESOLVED %s" % _exc_name(exc))
        return os.getcwd()
    if proc.returncode == 0:
        top = proc.stdout.decode("utf-8", "replace").strip()
        if top:
            return os.path.realpath(top)
        ctx.warn("W_GIT_ROOT_UNRESOLVED empty")
    else:
        ctx.warn("W_GIT_ROOT_UNRESOLVED rc=%d" % proc.returncode)
    return os.getcwd()


def collect_test_corpus(root=None, globs=None, max_corpus=DEFAULT_MAX_CORPUS,
                        exclude=None, ctx=None):
    """Return ``(files, seen_total)`` for the test corpus.

    ``globs`` are expanded HERE (``glob.glob``).  Without globs the corpus is a
    walk of ``root`` (default: the git top-level) for test-named files.
    """
    ctx = ctx or _Ctx()
    excluded = set(os.path.realpath(p) for p in (exclude or []))
    found = []
    if globs:
        for pattern in globs:
            for hit in globmod.glob(pattern):
                if os.path.isfile(hit):
                    found.append(os.path.realpath(hit))
    else:
        walk_root = root or _default_root(ctx)
        for dirpath, dirnames, filenames in os.walk(walk_root):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRNAMES]
            for name in filenames:
                if _TEST_NAME_RE.match(name):
                    found.append(os.path.realpath(os.path.join(dirpath, name)))
    ordered = []
    seen = set()
    for path in sorted(found):
        if path in seen or path in excluded:
            continue
        seen.add(path)
        ordered.append(path)
    total = len(ordered)
    if max_corpus and max_corpus > 0:
        ordered = ordered[:max_corpus]
    return ordered, total


# ---------------------------------------------------------------------------
# one batched scan per channel
# ---------------------------------------------------------------------------

def grep_keys_batched(keys, files, channel="grep", ctx=None):
    """ONE fixed-string scan of ``files`` for the whole key set.

    Returns ``[(file, lineno, content, key), ...]``: one entry per key present
    on a line.  Files are read as bytes, decoded utf-8 with replacement and
    split on a bare newline only; line numbers are 1-based and a trailing
    empty piece after a final newline is not a line.  A file that cannot be
    read contributes no hits and is named once in the warnings.

    Cost bound: a file is skipped unless it contains at least one key; for the
    remaining files one compiled alternation of the escaped keys present is a
    line prefilter, then the exact ``key in line`` test runs per matching line.
    """
    ctx = ctx or _Ctx()
    keys = [k for k in dict.fromkeys(keys) if k and "\n" not in k]
    if not keys or not files:
        return []
    results = []
    patterns: Dict[Tuple[str, ...], Any] = {}
    for fname in files:
        text = _raw_text(fname, ctx)
        if text is None:
            note = ("grep", fname)
            if note not in ctx.noted:
                ctx.noted.add(note)
                ctx.warn("W_CORPUS_UNREADABLE %s" % fname)
            continue
        present = tuple(k for k in keys if k in text)
        if not present:
            continue
        pattern = patterns.get(present)
        if pattern is None:
            ordered = sorted(present, key=len, reverse=True)
            pattern = re.compile("|".join(re.escape(k) for k in ordered))
            patterns[present] = pattern
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for idx, line in enumerate(lines, 1):
            if not pattern.search(line):
                continue
            for key in present:
                if key in line:
                    results.append((fname, idx, line, key))
    ctx.trace("grep %s keys=%d files=%d" % (channel, len(keys), len(files)))
    return results


# ---------------------------------------------------------------------------
# call-site (graph + grep, always)
# ---------------------------------------------------------------------------

def _load_graph(graph_path):
    """Parsed graph as ``{"nodes": [...], "links": [...]}``, or None if unusable."""
    if not graph_path or not os.path.isfile(graph_path):
        return None
    try:
        with open(graph_path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    nodes = data.get("nodes", []) or []
    links = data.get("links", []) or []
    if not isinstance(nodes, list) or not isinstance(links, list):
        return None
    return {
        "nodes": [n for n in nodes if isinstance(n, dict)],
        "links": [lk for lk in links if isinstance(lk, dict)],
    }


def _graph_callers(symbols, graph):
    rows = []
    nodes = graph["nodes"]
    links = graph["links"]
    by_id = {}
    for node in nodes:
        by_id[node.get("id")] = node
    for symbol in symbols:
        ids = set()
        for node in nodes:
            label = node.get("label", "")
            norm = node.get("norm_label", "")
            if label == symbol or label == symbol + "()" or norm == symbol:
                ids.add(node.get("id"))
        if not ids:
            continue
        for link in links:
            if link.get("target") not in ids:
                continue
            if link.get("relation", "") not in _GRAPH_RELATIONS:
                continue
            source = by_id.get(link.get("source"))
            if not source:
                continue
            src_file = source.get("source_file", "")
            raw_loc = str(source.get("source_location", "") or "")
            raw_loc = raw_loc.lstrip("L").split("-")[0]
            try:
                lineno = int(raw_loc)
            except ValueError:
                lineno = 0
            rows.append((src_file, lineno, symbol))
    return rows


def detect_call_sites(symbols, corpus, graph_path=None, graph_enabled=False,
                      keys=None, channel="call-site", ctx=None, graph=None):
    """Enumerate callers as the UNION of the graph and grep halves, ALWAYS.

    A graph hit with zero caller edges must NOT suppress the grep half.
    Returns ``[(file, lineno, key, token), ...]``.  ``graph`` is an already
    parsed graph (see ``_load_graph``); without it the graph half runs only
    when ``graph_enabled`` is set and ``graph_path`` is a usable graph file.
    """
    ctx = ctx or _Ctx()
    rows = []
    if keys is None:
        keys = []
        for name in symbols:
            keys.append(name + "(")
            keys.append("." + name + "(")
    key_owner = {}
    for name in symbols:
        key_owner[name + "("] = name
        key_owner["." + name + "("] = name
    for fname, lineno, content, key in grep_keys_batched(keys, corpus, channel, ctx):
        name = key_owner.get(key)
        if not name:
            name = key.lstrip(".").rstrip("(")
        if ("def " + name + "(") in content:
            continue
        rows.append((fname, lineno, key, name))
    if graph is None and graph_enabled and graph_path:
        graph = _load_graph(graph_path)
    if graph is not None:
        by_base = {}
        for path in corpus:
            by_base.setdefault(os.path.basename(path), path)
        for src_file, lineno, symbol in _graph_callers(symbols, graph):
            if not src_file:
                continue
            resolved = None
            for path in corpus:
                if path == src_file or path.endswith("/" + src_file.lstrip("./")):
                    resolved = path
                    break
            if resolved is None:
                resolved = by_base.get(os.path.basename(src_file))
            if resolved is None:
                continue
            rows.append((resolved, lineno, symbol, symbol))
    return rows


# ---------------------------------------------------------------------------
# spec reconcile (bidirectional)
# ---------------------------------------------------------------------------

def reconcile_with_spec(spec_text, coupled_basenames=None):
    """Return ``(cited_basenames, uncoupled_cited)``.

    A basename is cited iff it appears on a spec line that does NOT contain the
    case-insensitive phrase ``not in scope``, and it matches a corpus test
    pattern (so production basenames never enter the cited set).
    """
    cited = set()
    for line in (spec_text or "").splitlines():
        if _NOT_IN_SCOPE_RE.search(line):
            continue
        for token in _SPEC_TOKEN_RE.findall(line):
            name = os.path.basename(token)
            if _TEST_NAME_RE.match(name):
                cited.add(name)
    coupled = set(coupled_basenames or [])
    uncoupled = sorted(cited - coupled)
    return cited, uncoupled


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------

def _func_names(path, ctx):
    """Single in-Python pass per hit file: line number -> enclosing function."""
    text = _read_text(path, ctx)
    ext = _ext(path)
    names = []
    current = "MODULE"
    for line in text.splitlines():
        match = None
        if ext == ".py":
            match = _PY_DEF_RE.match(line)
        else:
            match = (
                _PY_DEF_RE.match(line)
                or _TS_FN_RE.match(line)
                or _SH_FN_PAREN_RE.match(line)
                or _SH_FN_WORD_RE.match(line)
            )
        if match:
            current = match.group(1)
        names.append(current)
    return names


class _Row(object):
    __slots__ = ("path", "line", "channel", "key", "token")

    def __init__(self, path, line, channel, key, token):
        self.path = path
        self.line = line
        self.channel = channel
        self.key = key
        self.token = token


class _KeySel(object):
    """The key-selection parameters, threaded to every channel helper."""

    __slots__ = ("min_len", "suspend_noise", "max_keys", "scope_label", "ctx", "graph")

    def __init__(self, min_len, suspend_noise, max_keys, scope_label, ctx, graph=None):
        self.min_len = min_len
        self.suspend_noise = suspend_noise
        self.max_keys = max_keys
        self.scope_label = scope_label
        self.ctx = ctx
        self.graph = graph


def _select_keys(candidates, channel, min_literal_len, suspend_noise, max_keys,
                 scope_label, ctx):
    """Apply the distinctiveness rules + the per-channel ``--max-keys`` cap.

    ``candidates`` is an ordered list of ``(key, token)``.  Returns
    ``(kept, capped)``.
    """
    kept = []
    seen = set()
    for key, token in candidates:
        if key in seen:
            continue
        seen.add(key)
        ok, rule = is_distinctive_key(key, min_literal_len, channel, suspend_noise)
        if not ok:
            ctx.warn("W_KEY_DROPPED %s %d" % (_clean_token(key), rule))
            continue
        kept.append((key, token))
    capped = False
    if max_keys and max_keys > 0 and len(kept) > max_keys:
        dropped = len(kept) - max_keys
        kept = kept[:max_keys]
        capped = True
        ctx.warn("W_KEY_CAP %s %s dropped=%d" % (channel, scope_label, dropped))
    return kept, capped


def _pick(candidates, channel, sel):
    """``_select_keys`` with the frozen parameter block."""
    return _select_keys(
        candidates, channel, sel.min_len, sel.suspend_noise, sel.max_keys,
        sel.scope_label, sel.ctx,
    )


def _apply_key_file_cap(rows, max_key_files, ctx):
    """Post-scan drop: a key matching > N distinct files is not distinctive."""
    if not max_key_files or max_key_files <= 0:
        return rows
    files_by_key = {}
    for row in rows:
        files_by_key.setdefault(row.key, set()).add(row.path)
    dropped = set()
    for key in sorted(files_by_key):
        count = len(files_by_key[key])
        if count > max_key_files:
            dropped.add(key)
            ctx.warn("W_KEY_NONDISTINCTIVE %s %d" % (_clean_token(key), count))
    if not dropped:
        return rows
    return [row for row in rows if row.key not in dropped]


def _dedup_longest(rows):
    """One row per (file, line, channel): longest key wins, ties bytewise."""
    best = {}
    for row in rows:
        slot = (row.path, row.line, row.channel)
        current = best.get(slot)
        if current is None:
            best[slot] = row
            continue
        if len(row.key) > len(current.key):
            best[slot] = row
        elif len(row.key) == len(current.key) and row.key < current.key:
            best[slot] = row
    return list(best.values())


# ---------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------

def _scope_channel_matrix(scope_files, active, ctx):
    """Extension -> extractor matrix.  Returns ``(applicable, escalate)``."""
    applicable = {}
    escalate = False
    for scope in scope_files:
        ext = _ext(scope)
        channels_for_file = set(PATH_FAMILY)
        if ext in EXT_IMPORT:
            channels_for_file.add("import")
        if ext in EXT_VALUE:
            channels_for_file.add("value-literal")
        if ext in EXT_DATA:
            channels_for_file.add("data-cell")
        if ext in EXT_CALL:
            channels_for_file.add("call-site")
        applicable[scope] = channels_for_file
        if ext not in SUPPORTED_EXTS:
            ctx.warn("W_UNSUPPORTED_SCOPE_EXT %s %s" % (ext or "(none)", scope))
            # An empty scope file has no content keys, so "unsupported" masks nothing.
            try:
                empty = os.path.getsize(scope) == 0
            except OSError:
                empty = False
            if not empty:
                escalate = True
            continue
        for channel in active:
            if channel not in channels_for_file:
                ctx.warn("W_NO_EXTRACTOR %s %s %s" % (channel, ext, scope))
    return applicable, escalate


def _channel_import(scope_files, applicable, corpus, sel):
    """Returns ``(rows, import_coupled, capped)``."""
    ctx = sel.ctx
    rows = []
    coupled = {}
    candidates = []
    for scope in scope_files:
        if "import" not in applicable[scope]:
            continue
        stem = os.path.splitext(os.path.basename(scope))[0]
        candidates.append((stem, stem))
    kept, capped = _pick(candidates, "import", sel)
    if not kept:
        return rows, coupled, capped
    hits = grep_keys_batched([k for k, _ in kept], corpus, "import", ctx)
    candidate_files = {}
    for fname, _lineno, _content, key in hits:
        candidate_files.setdefault(key, set()).add(fname)
    for stem, files in candidate_files.items():
        for path in sorted(files):
            linenos = detect_imports(_read_text(path, ctx), stem, path)
            if not linenos:
                continue
            coupled.setdefault(path, set()).add(stem)
            for lineno in linenos:
                rows.append(_Row(path, lineno, "import", stem, stem))
    return rows, coupled, capped


def _channel_path_family(scope_files, corpus, active_set, sel):
    """One pass over the path family; the most-specific label wins."""
    ctx = sel.ctx
    rows = []
    family_active = [c for c in PATH_FAMILY if c in active_set]
    if not family_active:
        return rows, False
    family_label = family_active[0]
    candidates = []
    for scope in scope_files:
        for key in path_family_keys(scope):
            candidates.append((key, key))
    kept, capped = _pick(candidates, family_label, sel)
    if not kept:
        return rows, capped
    hits = grep_keys_batched([k for k, _ in kept], corpus, family_label, ctx)
    labels = {}
    for fname, lineno, _content, key in hits:
        label = labels.get(fname)
        if label is None:
            label = classify_path_family(_read_text(fname, ctx))
            labels[fname] = label
        if label not in active_set:
            continue
        rows.append(_Row(fname, lineno, label, key, key))
    return rows, capped


def _channel_module_source_read(import_coupled, ctx):
    """``<module>.__file__`` read with no path literal anywhere."""
    rows = []
    for path in sorted(import_coupled):
        lines = _read_text(path, ctx).splitlines()
        for stem in sorted(import_coupled[path]):
            marker = stem + ".__file__"
            for idx, line in enumerate(lines, 1):
                if marker in line and (
                    detect_source_read(line) or "inspect.getsource(" in line
                ):
                    rows.append(_Row(path, idx, "source-read", marker, marker))
    return rows


def _channel_value_literal(scope_files, applicable, corpus, sel):
    ctx = sel.ctx
    rows = []
    candidates = []
    for scope in scope_files:
        if "value-literal" not in applicable[scope]:
            continue
        pairs, partial = extract_constant_values(scope, ctx=ctx)
        for name in sorted(set(partial)):
            ctx.warn("W_PARTIAL_LITERAL %s" % name)
        for name, value in pairs:
            token = "%s=%s" % (name, _clean_token(value)[:40])
            candidates.append((value, token))
    kept, capped = _pick(candidates, "value-literal", sel)
    if not kept:
        return rows, capped
    tokens = dict(kept)
    hits = grep_keys_batched([k for k, _ in kept], corpus, "value-literal", ctx)
    for fname, lineno, _content, key in hits:
        rows.append(_Row(fname, lineno, "value-literal", key, tokens.get(key, key)))
    return rows, capped


def _channel_data_cell(scope_files, applicable, corpus, sel):
    ctx = sel.ctx
    rows = []
    candidates = []
    for scope in scope_files:
        if "data-cell" not in applicable[scope]:
            continue
        for cell in extract_data_cells(scope, ctx=ctx):
            candidates.append((cell, cell))
    kept, capped = _pick(candidates, "data-cell", sel)
    if not kept:
        return rows, capped
    hits = grep_keys_batched([k for k, _ in kept], corpus, "data-cell", ctx)
    for fname, lineno, _content, key in hits:
        rows.append(_Row(fname, lineno, "data-cell", key, key))
    return rows, capped


def _channel_call_site(scope_files, applicable, corpus, sel):
    ctx = sel.ctx
    rows = []
    symbols = []
    for scope in scope_files:
        if "call-site" not in applicable[scope]:
            continue
        for name in extract_defined_symbols(scope, ctx=ctx):
            if name not in symbols:
                symbols.append(name)
    candidates = []
    for name in symbols:
        candidates.append((name + "(", name))
        candidates.append(("." + name + "(", name))
    kept, capped = _pick(candidates, "call-site", sel)
    if not kept:
        return rows, capped
    for fname, lineno, key, token in detect_call_sites(
        symbols,
        corpus,
        keys=[k for k, _ in kept],
        ctx=ctx,
        graph=sel.graph,
    ):
        rows.append(_Row(fname, lineno, "call-site", key, token))
    return rows, capped


# Channels whose helper takes the uniform (scope_files, applicable, corpus, sel)
# signature.  Order is the frozen emission order.
_UNIFORM_CHANNELS = (
    ("value-literal", _channel_value_literal),
    ("data-cell", _channel_data_cell),
    ("call-site", _channel_call_site),
)


def _collect_rows(scope_files, applicable, corpus, active_set, sel):
    """Run every active channel in the frozen order.  Returns ``(rows, capped)``."""
    rows = []
    capped_any = False
    import_coupled = {}
    if "import" in active_set:
        got, import_coupled, capped = _channel_import(
            scope_files, applicable, corpus, sel
        )
        rows.extend(got)
        capped_any = capped_any or capped
    got, capped = _channel_path_family(scope_files, corpus, active_set, sel)
    rows.extend(got)
    capped_any = capped_any or capped
    if "source-read" in active_set and import_coupled:
        rows.extend(_channel_module_source_read(import_coupled, sel.ctx))
    for name, runner in _UNIFORM_CHANNELS:
        if name not in active_set:
            continue
        got, capped = runner(scope_files, applicable, corpus, sel)
        rows.extend(got)
        capped_any = capped_any or capped
    return rows, capped_any


def _report_spec(spec_path, rows, ctx):
    """Tests -> spec direction.  Returns the cited-basename set."""
    spec_text = _read_text(spec_path, ctx) if spec_path else ""
    coupled_basenames = set(os.path.basename(row.path) for row in rows)
    cited, uncoupled = reconcile_with_spec(spec_text, coupled_basenames)
    for name in uncoupled:
        ctx.warn("W_SPEC_UNCOUPLED %s" % name)
    return cited


def _final_rows(rows, cited, ctx):
    """Resolve function names and verdicts, then sort bytewise like a C-locale sort."""
    func_cache = {}
    built = []
    for row in rows:
        names = func_cache.get(row.path)
        if names is None:
            names = _func_names(row.path, ctx)
            func_cache[row.path] = names
        if 1 <= row.line <= len(names):
            func = names[row.line - 1]
        else:
            func = "MODULE"
        verdict = "cited" if os.path.basename(row.path) in cited else "MISSING"
        token = _clean_token(row.token)
        whole = "%s\t%d\t%s\t%s\t%s\t%s\t%s" % (
            row.path, row.line, func, token, row.channel, "0", verdict)
        sort_key = (
            row.path.encode("utf-8", "surrogateescape"),
            row.line,
            0,
            whole.encode("utf-8", "surrogateescape"),
        )
        built.append((sort_key, (row.path, row.line, func, token, row.channel, verdict)))
    built.sort(key=lambda item: item[0])
    return tuple(item[1] for item in built)


# ---------------------------------------------------------------------------
# audit: the chokepoint
# ---------------------------------------------------------------------------

def _normalize_channels(channels):
    if channels is None:
        names: List[str] = []
    elif isinstance(channels, str):
        names = [c.strip() for c in channels.split(",") if c.strip()]
    else:
        names = [str(c).strip() for c in channels if str(c).strip()]
    for name in names:
        if name not in CHANNELS:
            raise SiblingAuditError(
                "E_UNKNOWN_CHANNEL",
                "E_UNKNOWN_CHANNEL: %s; valid channels: %s" % (name, ", ".join(CHANNELS)),
            )
    return list(dict.fromkeys(names)) or list(CHANNELS)


def _readable_file(path):
    return os.path.isfile(path) and os.access(path, os.R_OK)


def audit(scope_files, *, test_globs=None, corpus_root=None, channels=None,
          spec_path=None, min_literal_len=DEFAULT_MIN_LITERAL_LEN,
          max_keys=DEFAULT_MAX_KEYS, max_corpus=DEFAULT_MAX_CORPUS,
          max_key_files=DEFAULT_MAX_KEY_FILES, graph_path=None, exclude=None,
          trace_path=None):
    """Run the seven channels over the corpus and return an ``AuditResult``.

    Never writes to stdout or stderr: every diagnostic goes to ``warnings``.
    """
    ctx = _Ctx(trace_path)
    given = list(scope_files or [])
    if not given:
        raise SiblingAuditError("E_NO_SCOPE_FILES", "E_NO_SCOPE_FILES: no scope files given")
    active = _normalize_channels(channels)
    active_set = set(active)
    scopes = [os.path.realpath(p) for p in given]
    for orig, real in zip(given, scopes):
        if not _readable_file(real):
            raise SiblingAuditError(
                "E_SCOPE_FILE_UNREADABLE", "E_SCOPE_FILE_UNREADABLE %s" % orig)
    if spec_path and not _readable_file(spec_path):
        raise SiblingAuditError(
            "E_SCOPE_FILE_UNREADABLE", "E_SCOPE_FILE_UNREADABLE %s" % spec_path)

    excluded = set(scopes)
    for item in (exclude or []):
        excluded.add(os.path.realpath(item))
    root = os.path.realpath(corpus_root) if corpus_root else None
    corpus, seen_total = collect_test_corpus(
        root=root,
        globs=list(test_globs or []),
        max_corpus=max_corpus,
        exclude=excluded,
        ctx=ctx,
    )
    if not corpus:
        raise SiblingAuditError(
            "E_NO_TEST_CORPUS",
            "E_NO_TEST_CORPUS: no test files matched the scope-mode corpus")
    corpus_capped = False
    if seen_total > len(corpus):
        ctx.warn("W_CORPUS_CAP %d %d" % (seen_total, max_corpus))
        corpus_capped = True

    graph = None
    if "call-site" not in active_set:
        call_site: Dict[str, Any] = {"source": "grep", "reason": "channel_off"}
    elif not graph_path:
        call_site = {"source": "grep", "reason": "no_graph"}
    else:
        graph = _load_graph(graph_path)
        if graph is None:
            ctx.warn("W_GRAPH_UNREADABLE %s" % graph_path)
            call_site = {"source": "grep", "reason": "graph_unreadable"}
        else:
            call_site = {"source": "graph+grep", "reason": None}

    sel = _KeySel(
        min_literal_len,
        min_literal_len < DEFAULT_MIN_LITERAL_LEN,
        max_keys,
        ",".join(scopes),
        ctx,
        graph,
    )
    applicable, unsupported = _scope_channel_matrix(scopes, active, ctx)
    rows, keys_capped = _collect_rows(scopes, applicable, corpus, active_set, sel)
    rows = _apply_key_file_cap(rows, max_key_files, ctx)
    rows = _dedup_longest(rows)
    final = _final_rows(rows, _report_spec(spec_path, rows, ctx), ctx)
    missing = sum(1 for r in final if r[5] == "MISSING")
    return AuditResult(
        rows=final,
        warnings=tuple(ctx.warnings),
        gate_warn=bool(corpus_capped or unsupported or keys_capped),
        partial_channels=active_set != set(CHANNELS),
        call_site=call_site,
        missing=missing,
    )


def scope_files_from_spec(paths, root):
    """Scope files from an already-parsed spec file list.

    Each entry is resolved against ``root`` and kept iff it is a file inside
    ``root`` whose basename is not a corpus test name.  Sorted, unique,
    absolute (realpath).
    """
    base = os.path.realpath(root)
    out = set()
    for item in paths or []:
        cand = item if os.path.isabs(item) else os.path.join(base, item)
        real = os.path.realpath(cand)
        if not os.path.isfile(real):
            continue
        if real != base and not real.startswith(base.rstrip(os.sep) + os.sep):
            continue
        if _TEST_NAME_RE.match(os.path.basename(real)):
            continue
        out.add(real)
    return sorted(out)


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def _nonneg_int(value):
    if re.fullmatch(r"[0-9]+", value) is None:
        raise argparse.ArgumentTypeError("expected a non-negative integer, got %r" % value)
    return int(value)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="sibling-audit",
        description=(
            "Find the test files coupled to the given production (scope) files "
            "and report which of them the spec does not cite.\n"
            "Output: one TSV row per line: file, line, func, token, channel, verdict."
        ),
        epilog=(
            "channels (default: all seven):\n"
            "  import\n  source-read\n  exec-invocation\n  path-literal\n"
            "  value-literal\n  data-cell\n  call-site\n\n"
            "exit codes: 0 ok; 1 --require-clean and a MISSING row; "
            "2 error or gate."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--scope-file", action="append", default=None, metavar="PATH",
                        help="production file to audit (repeatable, required)")
    parser.add_argument("--test-glob", action="append", default=None, metavar="PAT",
                        help="test corpus glob (repeatable); default: walk the corpus root")
    parser.add_argument("--channels", default="", metavar="LIST",
                        help="comma-separated channel subset (see the list below)")
    parser.add_argument("--spec", default="", metavar="PATH",
                        help="spec file; test files it cites get verdict cited")
    parser.add_argument("--min-literal-len", type=_nonneg_int, default=DEFAULT_MIN_LITERAL_LEN,
                        metavar="N", help="shortest key kept (default 6)")
    parser.add_argument("--max-keys", type=_nonneg_int, default=DEFAULT_MAX_KEYS,
                        metavar="N", help="per-channel key cap (default 200)")
    parser.add_argument("--max-corpus", type=_nonneg_int, default=DEFAULT_MAX_CORPUS,
                        metavar="N", help="corpus file cap (default 5000)")
    parser.add_argument("--max-key-files", type=_nonneg_int, default=DEFAULT_MAX_KEY_FILES,
                        metavar="N", help="drop a key matching more files than this (default 25)")
    parser.add_argument("--require-clean", action="store_true",
                        help="exit 1 when any row is MISSING; exit 2 on a partial channel set or a gate warning")
    parser.add_argument("--corpus-root", default="", metavar="DIR",
                        help="corpus walk root (default: the git top-level)")
    parser.add_argument("--graph", default="", metavar="PATH",
                        help="optional code graph JSON for the call-site channel")
    parser.add_argument("--trace", default="", metavar="PATH",
                        help="append one trace line per channel scan to this file")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object instead of TSV rows")
    return parser


def _emit_json(rows, warnings, call_site, missing, code, error):
    obj: Dict[str, Any] = {
        "schema": JSON_SCHEMA,
        "rows": [
            {"file": r[0], "line": r[1], "func": r[2], "token": r[3],
             "channel": r[4], "verdict": r[5]}
            for r in rows
        ],
        "warnings": list(warnings),
        "call_site": call_site,
        "missing": missing,
        "exit": code,
    }
    if error:
        obj["error"] = error
    sys.stdout.write(json.dumps(obj) + "\n")


def _run(ns):
    try:
        result = audit(
            ns.scope_file or [],
            test_globs=ns.test_glob or None,
            corpus_root=ns.corpus_root or None,
            channels=ns.channels or None,
            spec_path=ns.spec or None,
            min_literal_len=ns.min_literal_len,
            max_keys=ns.max_keys,
            max_corpus=ns.max_corpus,
            max_key_files=ns.max_key_files,
            graph_path=ns.graph or None,
            trace_path=ns.trace or None,
        )
    except SiblingAuditError as exc:
        sys.stderr.write(exc.message + "\n")
        if ns.json:
            _emit_json((), (), None, 0, 2, exc.code)
        return 2

    for line in result.warnings:
        sys.stderr.write(line + "\n")
    code = 0
    error = ""
    if ns.require_clean:
        if result.partial_channels:
            sys.stderr.write(
                "%s: --require-clean needs the full channel set (%s)\n"
                % (_ERR_PARTIAL, " ".join(CHANNELS)))
            code = 2
            error = _ERR_PARTIAL
        elif result.gate_warn:
            code = 2
            for line in result.warnings:
                head = line.split(" ", 1)[0]
                if head in _GATE_WARNINGS:
                    error = head
                    break
        elif result.missing > 0:
            code = 1
    if ns.json:
        _emit_json(result.rows, result.warnings, result.call_site, result.missing, code, error)
    elif result.rows:
        sys.stdout.write(
            "\n".join("\t".join([r[0], str(r[1]), r[2], r[3], r[4], r[5]]) for r in result.rows)
            + "\n")
    return code


def main(argv=None):
    """Command-line entry point.  Returns the exit code; never exits itself."""
    ns = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return _run(ns)
    except Exception as exc:  # noqa: BLE001 - exit 2 is reserved for failures, never exit 1
        sys.stderr.write("%s %s: %s\n" % (_ERR_INTERNAL, _exc_name(exc), exc))
        if ns.json:
            _emit_json((), (), None, 0, 2, _ERR_INTERNAL)
        return 2


if __name__ == "__main__":
    sys.exit(main())
