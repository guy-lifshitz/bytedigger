"""spec_cite.py — citation scan and check library for spec-cite-lint.

Public API:
  Citation  — dataclass(file, symbol, line_no)
  Finding   — dataclass(file, symbol, status)
  scan_citations(spec_text) -> list[Citation]
  check_citation(cit, repo_root, declared=..., repo_index=...) -> Finding
  lint_spec(spec_path, repo_root) -> tuple[int, list[Finding]]
  declared_introduced_symbols(spec_text) -> set[str]
  BLOCKING_STATUSES — Finding statuses that make lint_spec exit 1

Part of 52151A8F — spec-cite-lint.
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator, Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

# Matches code-file paths: e.g. bar.py, src/util.ts, ./router.sh
_CODE_FILE_RE = re.compile(r"[\w./-]+\.(?:py|ts|tsx|js|sh)\b")

# Matches backtick-quoted tokens: `anything here`
_BACKTICK_RE = re.compile(r"`([^`]+)`")

# Code-file extensions to exclude from symbol candidates
_CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".sh")

# Structural symbol-token charset (GH367, §2.1): identifier-start, body of word
# chars/dots/hyphens, optional literal `()` suffix. Rejects `[`, `]`, `"`, `'`,
# `==`, `<`, and non-trailing `(`.
_SYMBOL_TOKEN_RE = re.compile(r"^[A-Za-z_][\w.-]*(\(\))?$")

# New-symbol context marker (GH366, §2.2; extended GH382 §2.B.1): matched
# against the WHOLE spec line (not just the token) — a line describing a
# symbol being created/added/new, OR one describing a planned future change
# (modify/gains/extends/imports/wires/renames/becomes). Deliberately excludes
# "resolve" — too common in existing-code prose to signal a planned symbol.
_NEW_CONTEXT_RE = re.compile(
    r"(?i)("
    r"\bcreate[sd]?\b|\badd(?:s|ed|ing)?\b|\bnew\b|\(new\)|"
    r"\bmodif(?:y|ies|ied|ying)\b|\bgains?\b|\bextend(?:s|ed|ing)?\b|"
    r"\bimport(?:s|ed|ing)?\b|\bwire[sd]?\b|\brename[sd]?\b|\bbecomes?\b"
    r")"
)


# Matches a CREATE declaration line (GH631 §2.1): optional list-bullet prefix,
# "CREATE:" or (bd#87) colon-less "CREATE " — case-sensitive, so prose
# "Create ..." is not a declaration — then a code-file path (optionally
# backtick-quoted), optional trailing " (comment)".
_CREATE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?CREATE(?::\s*|\s+)`?(?P<path>" + _CODE_FILE_RE.pattern + r")`?"
)

# Matches the heading of a "Files this spec CREATES" section (GH631 §2.1.b).
_CREATES_HEADING_RE = re.compile(r"(?i)^#{1,6}\s*Files this spec CREATES")

# Matches a fenced-code-block delimiter line (GH689 §2.2): ``` or ~~~ of any
# length ≥3, optional language tag captured for python-vs-other dispatch.
_FENCE_RE = re.compile(r"^\s*(?:```+|~~~+)\s*([A-Za-z0-9_+.#-]*)")

# Language tags treated as python (or ambiguous/untagged, kept permissive to
# avoid over-suppressing legit python-fenced citations) — GH689 §2.2.
_PY_FENCE_LANGS = frozenset({"", "py", "python", "python3"})


def _norm_path(p: str) -> str:
    """Strip surrounding backticks and a leading "./" prefix (GH631 §2.1)."""
    p = p.strip()
    if p.startswith("`") and p.endswith("`") and len(p) >= 2:
        p = p[1:-1]
    if p.startswith("./"):
        p = p[2:]
    return p


def declared_created_files(spec_text: str) -> set[str]:
    """Return the set of normalized file paths declared as CREATE targets
    (GH631 §2.2): matched from `CREATE:` lines and from the body of a
    "Files this spec CREATES" section (until the next `#` heading). Lines
    inside a non-python fence are skipped (bd#87)."""
    declared: set[str] = set()
    in_creates_section = False
    # bd#87: fence-aware, like every other declaration parser — a CREATE
    # line quoted in an example fence declares nothing.
    for _line_no, line in _iter_scannable_lines(spec_text):
        m = _CREATE_LINE_RE.match(line)
        if m:
            declared.add(_norm_path(m.group("path")))
            continue
        if _CREATES_HEADING_RE.match(line):
            in_creates_section = True
            continue
        if in_creates_section:
            if re.match(r"^#", line):
                in_creates_section = False
                continue
            for f in _CODE_FILE_RE.findall(line):
                declared.add(_norm_path(f))
    return declared


@dataclass
class Citation:
    file: str
    symbol: str
    line_no: int
    is_new: bool = False


@dataclass
class Finding:
    file: str
    symbol: str
    status: str  # "resolved" | "unresolved_symbol" | "missing_file" | "new_symbol" | "planned_file" | "wrong_file" | "no_citations"


def _is_valid_symbol(token: str) -> bool:
    """Return True if token qualifies as a symbol citation candidate.

    Rules (derived from test AC10, AC11, AC9, GH367 T1/T2):
    - Must not contain spaces (AC10: prose phrases excluded)
    - Must not end with a code-file extension (AC11: path-like tokens excluded)
    - Must start with a letter or underscore (exclude pure punctuation / numeric)
    - Must match the structural token charset (GH367: rejects inline
      index/comparison/call expressions like `result[0]["status"]=="x"`)
    """
    if " " in token:
        return False
    for ext in _CODE_EXTS:
        if token.endswith(ext):
            return False
    if not re.match(r"^[A-Za-z_]", token):
        return False
    if not _SYMBOL_TOKEN_RE.match(token):
        return False
    return True


def _iter_scannable_lines(spec_text: str) -> Iterator[tuple[int, str]]:
    """Yield (line_no, line) for scannable lines (GH1005 §2.1): the same
    fence-skip semantics scan_citations previously inlined — lines inside a
    non-python fenced code block are excluded; fence delimiter lines
    themselves are excluded."""
    in_fence = False
    fence_is_python = True
    for line_no, line in enumerate(spec_text.splitlines(), start=1):
        m = _FENCE_RE.match(line)
        if m:
            if not in_fence:
                in_fence = True
                fence_is_python = m.group(1).lower() in _PY_FENCE_LANGS
            else:
                in_fence = False
                fence_is_python = True
            continue
        if in_fence and not fence_is_python:
            continue
        yield line_no, line


# bd#87: `_CODE_FILE_RE` tokens that are not files — a product name such as
# `Node.js` (capitalized, no directory) or a URL tail (`//host/app.js`). With
# missing_file blocking, pairing them with a line's symbols would fail the spec.
_PRODUCT_NAME_RE = re.compile(r"[A-Z][A-Za-z0-9]*\.js")


def _is_file_token(token: str) -> bool:
    return not token.startswith("//") and not _PRODUCT_NAME_RE.fullmatch(token)


def scan_citations(spec_text: str) -> list[Citation]:
    """Scan spec_text and return one Citation per (file, symbol) pair per line.

    Citations on a line matching _NEW_CONTEXT_RE (whole-line match, GH366) are
    marked is_new=True.

    GH689 §2.2: lines inside a non-python fenced code block (```sql, ```json,
    etc.) are skipped entirely — those blocks quote data/schema/example
    literals, not real code citations. Untagged/python-tagged fences are
    scanned normally.
    """
    citations: list[Citation] = []
    for line_no, line in _iter_scannable_lines(spec_text):
        files = [f for f in _CODE_FILE_RE.findall(line) if _is_file_token(f)]
        raw_tokens = _BACKTICK_RE.findall(line)
        symbols = [t for t in raw_tokens if _is_valid_symbol(t)]
        if not files or not symbols:
            continue
        is_new = bool(_NEW_CONTEXT_RE.search(line))
        for f in files:
            for s in symbols:
                citations.append(Citation(file=f, symbol=s, line_no=line_no, is_new=is_new))
    return citations


# GH1005 §2.2: signature-prefix extractor — a backtick token like
# `myGuardHelper(cfg)` on a "new" line names a planned symbol even with no
# code-file token on the same line.
_SIG_PREFIX_RE = re.compile(r"^([A-Za-z_][\w.-]*)\s*\(")


# bd#87 op1 (ported from HAL #1893 §2.1): declarative introduced-symbol
# allowlist — an `INTRODUCES:` line, or bullets under a
# "Symbols this spec INTRODUCES" heading.
_INTRODUCES_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?INTRODUCES:(?P<rest>.*)$")
_INTRODUCES_HEADING_RE = re.compile(r"(?i)^#{1,6}\s*Symbols this spec INTRODUCES")
_BULLET_RE = re.compile(r"^\s*[-*]\s")


def _backtick_symbols(text: str) -> set[str]:
    """Backtick tokens in text that name a symbol: a valid symbol token
    (trailing `()` stripped) or the leading identifier of a signature."""
    symbols: set[str] = set()
    for t in _BACKTICK_RE.findall(text):
        if _is_valid_symbol(t):
            symbols.add(t.removesuffix("()"))
            continue
        sm = _SIG_PREFIX_RE.match(t)
        if sm:
            symbols.add(sm.group(1))
    return symbols


def declared_introduced_symbols(spec_text: str) -> set[str]:
    """Return the symbols a spec explicitly declares it introduces (bd#87).

    Two forms, both walked through _iter_scannable_lines (neither works
    inside a non-python fence): an `INTRODUCES:` line, and bullet lines in
    the body of a "Symbols this spec INTRODUCES" section (until the next
    line starting with `#`). Tokens come only from backticks. A section
    body line carrying a code-file path is a citation, never a declaration,
    and is skipped whole — otherwise a last-in-document section would
    allowlist a typo cited below it."""
    introduced: set[str] = set()
    in_section = False
    for _line_no, line in _iter_scannable_lines(spec_text):
        m = _INTRODUCES_LINE_RE.match(line)
        if m:
            introduced |= _backtick_symbols(m.group("rest"))
            continue
        if _INTRODUCES_HEADING_RE.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("#"):
            in_section = False
            continue
        if _BULLET_RE.match(line) and not _CODE_FILE_RE.search(line):
            introduced |= _backtick_symbols(line)
    return introduced


def new_marked_symbols(spec_text: str) -> set[str]:
    """Return symbols marked as new/planned by _NEW_CONTEXT_RE (GH1005 §2.2),
    independent of any code-file token on the same line. For each scannable
    line matching _NEW_CONTEXT_RE, each backtick token that is a valid symbol
    (normalized via removesuffix("()")) or matches _SIG_PREFIX_RE (its
    leading identifier group) is added."""
    marked: set[str] = set()
    for _line_no, line in _iter_scannable_lines(spec_text):
        if _NEW_CONTEXT_RE.search(line):
            marked |= _backtick_symbols(line)
    return marked


# GH689 §2.3: stdlib module allowlist — a citation like `json.loads` is a
# legitimate reference to the standard library, not a local-repo symbol; it
# should never surface as unresolved_symbol just because the cited .py file
# doesn't contain that literal text.
_STDLIB_MODULES = frozenset(getattr(sys, "stdlib_module_names", frozenset()))


def _is_stdlib_symbol(symbol: str) -> bool:
    """Return True if symbol's leading dotted component names a stdlib module."""
    return symbol.split(".", 1)[0] in _STDLIB_MODULES


# GH699 §2.1: curated SQL table allowlist — a prose token <table>.<column>
# whose leading <table> component names a known schema table is a SQL
# identifier reference, not an unresolved code symbol.
_SQL_SCHEMA_ALLOWLIST_PATH = Path(__file__).parent / "sql-schema-allowlist.txt"

# Shape guard (GH699 §2.1, Variant B): lowercase-dotted-no-parens, >=1 dot.
_SQL_IDENTIFIER_SHAPE_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$")


def _load_sql_tables(path: Path = _SQL_SCHEMA_ALLOWLIST_PATH) -> frozenset[str]:
    """Load the curated SQL table allowlist (GH699 §2.1): one lowercase table
    name per line, blank lines and '#'-comment lines skipped. Missing file
    fails soft to an empty frozenset (never raises)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    tables: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tables.add(stripped.lower())
    return frozenset(tables)


_SQL_TABLES: frozenset[str] = _load_sql_tables()


def _is_sql_identifier(symbol: str, tables: AbstractSet[str]) -> bool:
    """Return True if symbol is a SQL <table>.<column> identifier (GH699
    §2.1): shape-matches AND its leading dotted component is in ``tables``."""
    if not _SQL_IDENTIFIER_SHAPE_RE.match(symbol):
        return False
    return symbol.split(".", 1)[0] in tables


# GH699 §2.2: declaration-position extractors for spec-declared net-new
# symbols — class lines, def lines, assignment targets, and heading tokens.
_DECLARED_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_]\w*)")
_DECLARED_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
_DECLARED_ASSIGN_RE = re.compile(r"^\s*(?:self\.)?([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)")
_DECLARED_HEADING_RE = re.compile(r"^#{1,6}\s+")

# GH906: quoted-signature extractors — a def/class signature cited inline
# inside quotes/backticks (e.g. path.py:"def load_retired_tech(...) -> ...")
# is a declaration of a spec-planned symbol, same as a line-anchored one.
_DECLARED_QUOTED_DEF_RE = re.compile(r"[\"'`]\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
_DECLARED_QUOTED_CLASS_RE = re.compile(r"[\"'`]\s*class\s+([A-Za-z_]\w*)")


def declared_symbols(spec_text: str) -> set[str]:
    """Extract names in declaration position from spec_text (GH699 §2.2):
    class lines, def lines, assignment targets (incl. self.<name>), and
    heading backtick/bare tokens (plus their dotted first/last components).
    Prose mentions (non-declaration position) are never extracted."""
    declared: set[str] = set()
    for line in spec_text.splitlines():
        for qm in _DECLARED_QUOTED_DEF_RE.finditer(line):
            declared.add(qm.group(1))
        for qm in _DECLARED_QUOTED_CLASS_RE.finditer(line):
            declared.add(qm.group(1))
        m = _DECLARED_CLASS_RE.match(line)
        if m:
            declared.add(m.group(1))
            continue
        m = _DECLARED_DEF_RE.match(line)
        if m:
            declared.add(m.group(1))
            continue
        m = _DECLARED_ASSIGN_RE.match(line)
        if m:
            declared.add(m.group(1))
            continue
        if _DECLARED_HEADING_RE.match(line):
            tokens = _BACKTICK_RE.findall(line)
            tokens += [t for t in line.split() if _is_valid_symbol(t)]
            for tok in tokens:
                if not _is_valid_symbol(tok):
                    continue
                declared.add(tok)
                if "." in tok:
                    parts = tok.split(".")
                    declared.add(parts[0])
                    declared.add(parts[-1])
    return declared


def _symbol_declared(symbol: str, declared: AbstractSet[str]) -> bool:
    """Return True if symbol is spec-declared (GH699 §2.2): full match, or
    its last dotted component (method) or first dotted component (class)."""
    if symbol in declared:
        return True
    if symbol.split(".")[-1] in declared:
        return True
    if symbol.split(".")[0] in declared:
        return True
    return False


# GH796 §2.2: repo-wide symbol index — kills the cross-product FP where a
# symbol resolves to a DIFFERENT file under repo_root than the one it was
# cited against (e.g. a `posture_store.upsert_record`-style dotted citation
# paired with the wrong module on the same line).
_INDEX_EXTS = _CODE_EXTS  # §1g: alias, NOT a second tuple
_INDEX_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".tox",
})
_MAX_INDEX_FILE_BYTES = 2_000_000  # skip minified/vendored bundles
_MAX_INDEX_FILES = 20_000  # bounded walk (§0.4 15s subprocess timeout budget)
_IDENT_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_REPO_INDEX_CACHE: dict[str, frozenset[str]] = {}  # per-run, keyed by resolved repo_root


def _in_nested_checkout(path: Path, repo_root: Path, _cache: dict[Path, bool]) -> bool:
    """Return True if any strict ancestor of path (up to, excluding,
    repo_root) contains a `.git` entry (GH895 §2.2): a nested worktree/repo
    checkout under repo_root leaks its own tree into the index otherwise.
    A `.git` entry may be a file (worktree) or a directory (full repo).
    Memoized per-ancestor in ``_cache`` (per _iter_code_files call). Never
    raises on an unreadable ancestor."""
    ancestor = path.parent
    while ancestor != repo_root and repo_root in ancestor.parents:
        cached = _cache.get(ancestor)
        if cached is not None:
            if cached:
                return True
            ancestor = ancestor.parent
            continue
        try:
            is_nested = ancestor.joinpath(".git").exists()
        except OSError:
            is_nested = False
        _cache[ancestor] = is_nested
        if is_nested:
            return True
        ancestor = ancestor.parent
    return False


def _iter_code_files(repo_root: Path) -> list[Path]:
    """Return indexable code files under repo_root (GH796 §2.2): suffix in
    _INDEX_EXTS, no path component in _INDEX_SKIP_DIRS, size <=
    _MAX_INDEX_FILE_BYTES, at most _MAX_INDEX_FILES entries. Excludes files
    under a nested worktree/repo checkout (GH895 §2.2). Sorted for
    determinism. Never raises on an unreadable entry. GH912: topdown-prune
    os.walk (skip-dirs + nested checkouts pruned before descent) instead of
    a full rglob materialization."""
    files: list[Path] = []
    hit_cap = False
    for dirpath, dirnames, filenames in os.walk(
        str(repo_root), topdown=True, onerror=None, followlinks=False
    ):
        pruned: list[str] = []
        for d in dirnames:
            if d in _INDEX_SKIP_DIRS:
                continue
            try:
                is_nested = Path(dirpath, d, ".git").exists()
            except OSError:
                is_nested = False
            if is_nested:
                continue
            pruned.append(d)
        pruned.sort()
        dirnames[:] = pruned
        for f in sorted(filenames):
            if hit_cap:
                break
            path = Path(dirpath, f)
            if path.suffix not in _INDEX_EXTS:
                continue
            try:
                if not path.is_file():
                    continue
                if path.stat().st_size > _MAX_INDEX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) >= _MAX_INDEX_FILES:
                hit_cap = True
        if hit_cap:
            break
    return sorted(files)


def _repo_symbol_index(repo_root: Path) -> frozenset[str]:
    """Set of identifier tokens appearing literally in any indexable file
    under repo_root (GH796 §2.2). Each `a.b.c` token is added whole AND
    component-wise. Memoized per resolved repo_root in _REPO_INDEX_CACHE
    (per-process; §1ab re-entry: a same-process retry reuses the cache, a
    different repo_root gets its own entry — no cross-contamination)."""
    key = str(repo_root.resolve())
    cached = _REPO_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    tokens: set[str] = set()
    for path in _iter_code_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for tok in _IDENT_TOKEN_RE.findall(text):
            tokens.add(tok)
            if "." in tok:
                tokens.update(tok.split("."))
    index = frozenset(tokens)
    _REPO_INDEX_CACHE[key] = index
    return index


def _symbol_in_repo(symbol: str, index: AbstractSet[str]) -> bool:
    """Return True if symbol (trailing `()` stripped, GH796 §2.2/AC15)
    resolves anywhere in ``index``: whole-token match, or its last dotted
    component (method) — NOT its first component (too loose)."""
    sym = symbol[:-2] if symbol.endswith("()") else symbol
    if sym in index:
        return True
    if sym.split(".")[-1] in index:
        return True
    return False


def check_citation(
    cit: Citation,
    repo_root: Path,
    declared: AbstractSet[str] = frozenset(),
    repo_index: AbstractSet[str] | None = None,
) -> Finding:
    """Check whether cit.symbol appears literally in the file at repo_root/cit.file.

    If the symbol is absent AND cit.is_new (GH366 new-symbol context marker),
    status is "new_symbol" (advisory) instead of "unresolved_symbol" (blocking).

    GH631 §2.3: if _norm_path(cit.file) is in ``declared`` (declared-created
    files), the file is treated as a planned net-new file: missing → advisory
    "planned_file"; existing + symbol present → "resolved"; existing + symbol
    absent → "new_symbol". Undeclared files keep prior behavior unchanged.

    GH796 §2.3: as the LAST check before the final "unresolved_symbol"
    return, if the symbol resolves anywhere else under repo_root (via
    ``repo_index``, lazily built from ``_repo_symbol_index`` when None),
    status is "wrong_file" (advisory) instead of "unresolved_symbol"
    (blocking) — kills the cross-product FP where a symbol is cited against
    the wrong file on the same spec line.
    """
    target = repo_root / cit.file
    if _norm_path(cit.file) in declared:
        if not target.exists() or not target.is_file():
            return Finding(file=cit.file, symbol=cit.symbol, status="planned_file")
        text = target.read_text(encoding="utf-8", errors="replace")
        if cit.symbol in text:
            return Finding(file=cit.file, symbol=cit.symbol, status="resolved")
        return Finding(file=cit.file, symbol=cit.symbol, status="new_symbol")
    if not target.exists() or not target.is_file():
        return Finding(file=cit.file, symbol=cit.symbol, status="missing_file")
    text = target.read_text(encoding="utf-8", errors="replace")
    if cit.symbol in text:
        return Finding(file=cit.file, symbol=cit.symbol, status="resolved")
    if cit.is_new:
        return Finding(file=cit.file, symbol=cit.symbol, status="new_symbol")
    # GH689 §2.3: stdlib symbols (e.g. `json.loads`) are allowlisted-resolved
    # even when the literal text is absent from the cited file — they refer
    # to the standard library, not a local-repo definition.
    if _is_stdlib_symbol(cit.symbol):
        return Finding(file=cit.file, symbol=cit.symbol, status="resolved")
    # GH699 §2.1: SQL <table>.<column> identifiers in prose (e.g.
    # documents.id) are resolved, not unresolved local-repo symbols.
    if _is_sql_identifier(cit.symbol, _SQL_TABLES):
        return Finding(file=cit.file, symbol=cit.symbol, status="resolved")
    # GH796 §2.3: repo-wide resolve — the symbol exists somewhere under
    # repo_root, just not in the cited file. Advisory, not blocking.
    idx = repo_index if repo_index is not None else _repo_symbol_index(repo_root)
    if _symbol_in_repo(cit.symbol, idx):
        return Finding(file=cit.file, symbol=cit.symbol, status="wrong_file")
    return Finding(file=cit.file, symbol=cit.symbol, status="unresolved_symbol")


def planned_symbols(citations: list[Citation], findings: list[Finding]) -> set[str]:
    """Return the set of symbols considered "planned" — about to be created by
    this spec (GH382 §2.B.2). A symbol is planned if ANY of its citations has
    ``is_new=True``, OR ANY of its findings has ``status=="missing_file"`` (a
    cited-but-absent file is itself a planned CREATE)."""
    planned: set[str] = set()
    for cit in citations:
        if cit.is_new:
            planned.add(cit.symbol)
    for f in findings:
        if f.status == "missing_file" or f.status == "planned_file":
            planned.add(f.symbol)
    return planned


# bd#87 op3: statuses that make lint_spec exit 1. missing_file is a cited
# file that neither exists nor is declared by a CREATE line/section (an
# invented path); no_citations means the lint saw no code reference at all
# (e.g. a spec wrapped whole in a markdown fence) — blind is not clean.
BLOCKING_STATUSES = frozenset({"unresolved_symbol", "missing_file", "no_citations"})


# A `<path>:<line>` or `<path>:"snippet"` anchor — the forms the phase_45
# citation verifier checks (mirrors its _CITATION_RE extensions), so a spec
# citing only in those forms is not blind.
_ANCHORED_CITATION_RE = re.compile(r"[\w./-]+\.(?:py|ts|tsx|js|sh|md|yml):(?:\d|\")")

# A source file in a language this lint does not index: a spec targeting it
# is out of the lint's reach, not blind. Anything in _CODE_EXTS is excluded,
# so extending the indexed set can never leave an extension in both.
_OTHER_SOURCE_EXTS = tuple(
    e for e in (
        "go", "rs", "java", "kt", "kts", "rb", "c", "h", "cc", "cpp", "hpp", "cs",
        "swift", "php", "scala", "m", "mm", "lua", "dart", "ex", "exs", "zig",
    )
    if f".{e}" not in _CODE_EXTS
)
_OTHER_SOURCE_FILE_RE = re.compile(r"[\w./-]+\.(?:" + "|".join(_OTHER_SOURCE_EXTS) + r")\b")


def _is_blind(spec_text: str, citations: list[Citation]) -> bool:
    """True when spec_text is not blank but nothing in it is checkable: no
    (file, symbol) citation for this lint, no anchored path citation, and no
    source file in a language outside this lint's reach on a scannable line.
    A bare path mention of an indexed language checks nothing."""
    if citations or not spec_text.strip():
        return False
    return not any(
        _ANCHORED_CITATION_RE.search(line) or _OTHER_SOURCE_FILE_RE.search(line)
        for _n, line in _iter_scannable_lines(spec_text)
    )


def lint_spec(spec_path: Path, repo_root: Path) -> tuple[int, list[Finding]]:
    """Lint a spec file: scan citations, check each, return (exit_code, findings).

    GH382 §2.B.3: after the per-citation check, every "unresolved_symbol"
    finding whose symbol is in the planned set (see ``planned_symbols``) is
    downgraded to "new_symbol" (advisory) — this closes the cross-product
    false-positive where a symbol is plainly cited on one line and
    CREATE/MODIFY-cited (planned) on another. exit_code is computed AFTER
    the downgrade.

    bd#87: symbols a spec declares it introduces (see
    ``declared_introduced_symbols``) are downgraded the same way. A blind
    spec (see ``_is_blind``) gets one "no_citations" finding.

    exit_code = 1 if any finding's status is in BLOCKING_STATUSES, else 0.
    new_symbol, planned_file, wrong_file, and resolved are advisory.
    """
    spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    citations = scan_citations(spec_text)
    declared = declared_created_files(spec_text)
    repo_index = _repo_symbol_index(repo_root)  # GH796 §2.4: computed once
    findings = [
        check_citation(c, repo_root, declared=declared, repo_index=repo_index)
        for c in citations
    ]
    planned = planned_symbols(citations, findings)
    declared_syms = declared_symbols(spec_text)
    new_marked = new_marked_symbols(spec_text)  # GH1005 §2.3
    introduced = declared_introduced_symbols(spec_text)  # bd#87 op2
    for f in findings:
        if f.status in ("unresolved_symbol", "wrong_file") and (  # GH796: + "wrong_file"
            f.symbol in planned
            or _symbol_declared(f.symbol, declared_syms)
            or f.symbol.removesuffix("()") in new_marked  # GH1005 §2.3
            or f.symbol.removesuffix("()") in introduced  # bd#87 op2
        ):
            f.status = "new_symbol"
    if _is_blind(spec_text, citations):
        findings.append(Finding(file="", symbol="", status="no_citations"))
    exit_code = 1 if any(f.status in BLOCKING_STATUSES for f in findings) else 0
    return exit_code, findings
