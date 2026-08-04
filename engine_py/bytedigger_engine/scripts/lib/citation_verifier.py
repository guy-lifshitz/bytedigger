"""Citation verifier for HAL engine_py spec_lint (Step 5A, agreement D04A3BA8).
Extracts and verifies citation claims (function refs, line numbers) from arbitrary text
against actual source files on disk. Lib-tier: stdlib + `lib.git_port`.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bytedigger_engine.lib.git_port import git_read  # noqa: E402

logger = logging.getLogger(__name__)

CitationKind = Literal["function", "line", "case_vs_function", "snippet"]

_FILE_TOKEN = r"[\w./-]+\.(?:sh|py|ts|tsx|js)"
_FN_NAME = r"[A-Za-z_]\w*"
# source <file> && <fn>  or  source <file>; <fn>
_SOURCE_CALL_RE = re.compile(r"source\s+(" + _FILE_TOKEN + r")\s*(?:&&|;)\s*(" + _FN_NAME + r")\b")
# <file>:L<N>  or  <file>:<digits>
_LINE_CITE_RE = re.compile(r"(" + _FILE_TOKEN + r"):L?(\d+)")
_SNIPPET_CITE_RE = re.compile(r'(' + _FILE_TOKEN + r'):"((?:[^"\\\n]|\\.){3,200})"')
# Escaped-quote-aware: (?:[^"\\\n]|\\.) consumes \" as a unit so the capture
# terminates only on an unescaped ".  Pairs with phase_45_spec.py rule 5 (anti-drift).
_FILE_SIZE_LIMIT = 2 * 1024 * 1024  # 2 MB
_AMBIG_RESOLVE_MAX = 20  # candidate cap above which auto-resolve is skipped (GH853)


def _normalize_ws(s: str) -> str:
    """Collapse all whitespace runs to a single space and strip ends."""
    return " ".join(s.split())


def _unescape_snippet(s: str) -> str:
    """Reverse the contract's inner-quote escaping (\\" -> ") before content-grep.
    Pairs with phase_45_spec.py rule 5 (canonical snippet-citation syntax)."""
    return s.replace('\\"', '"')


def _decode_ws_escapes(s: str) -> str:
    """Decode literal whitespace escape sequences (\\n, \\t, \\r — two chars each)
    to a single space, so a multiline-wrapped snippet citation matches its source
    after _normalize_ws. Leaves every other escape (\\", \\\\, \\d, ...) untouched."""
    return s.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")  # nosemgrep: hal-regex-escape-chain — not regex-escaping; \\ never replaced, order-insensitive


@dataclass(frozen=True)
class Citation:
    """A single citation claim extracted from text."""
    kind: CitationKind
    identifier: str      # function name, "L591", or case_vs_function label
    file_path: str       # cited source path (relative or absolute)
    text_offset: int     # byte offset in source text where citation starts
    text_snippet: str    # ≤80-char excerpt around the citation


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of verifying one Citation against the actual file."""
    verified: bool
    evidence: str        # short explanation
    confidence: float    # 0.0–1.0; 1.0 = direct grep hit


def extract_citations(text: str) -> list[Citation]:
    """Scan *text* for citation patterns. Returns a (possibly empty) Citation list.

    Recognized patterns:
      - ``source <file> && <fn>`` or ``source <file>; <fn>`` → kind=function
      - ``<file>:L<N>`` or ``<file>:<N>`` → kind=line
    No file I/O is performed.
    """
    if not text:
        return []
    results: list[Citation] = []
    for m in _SOURCE_CALL_RE.finditer(text):
        start = m.start()
        results.append(Citation(
            kind="function",
            identifier=m.group(2),
            file_path=m.group(1),
            text_offset=start,
            text_snippet=text[max(0, start - 10): start + 70][:80],
        ))
    for m in _LINE_CITE_RE.finditer(text):
        start = m.start()
        results.append(Citation(
            kind="line",
            identifier=f"L{m.group(2)}",
            file_path=m.group(1),
            text_offset=start,
            text_snippet=text[max(0, start - 10): start + 70][:80],
        ))
    for m in _SNIPPET_CITE_RE.finditer(text):
        start = m.start()
        results.append(Citation(
            kind="snippet",
            identifier=m.group(2),
            file_path=m.group(1),
            text_offset=start,
            text_snippet=text[max(0, start - 10): start + 70][:80],
        ))
    return results


def _resolve_path(file_path: str, hal_root: Path) -> Path:
    p = Path(file_path)
    return p.resolve() if p.is_absolute() else (hal_root / file_path).resolve()


def _segment_walk_resolve(file_path: str, hal_root: Path) -> tuple[Path | None, list[str]]:
    """Resolve *file_path* via `git ls-files` tail-segment matching under *hal_root*.

    Returns:
        (resolved_path, [])       — exactly one match found.
        (None, [cand1, cand2...]) — zero or ≥2 matches (caller decides how to render).
    """
    try:
        result = git_read(["ls-files"], cwd=str(hal_root), timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return (None, [])
    if result.returncode != 0:
        return (None, [])
    # Tail-segment match: candidate must end with file_path, segment-aligned.
    needle = file_path if file_path.startswith("/") else "/" + file_path
    candidates: list[str] = []
    for line in result.stdout.splitlines():
        candidate = line.strip()
        p = "/" + candidate
        if p.endswith(needle) or candidate == file_path:
            candidates.append(candidate)
    candidates.sort()
    if len(candidates) == 1:
        return (hal_root / candidates[0], [])
    if len(candidates) >= 2:
        # Realpath dedup — symlinks pointing at the same inode collapse to one (8F4131E3).
        try:
            realpaths = {(hal_root / c).resolve() for c in candidates}
        except (OSError, RuntimeError):
            realpaths = set()
        if len(realpaths) == 1:
            return ((hal_root / candidates[0]).resolve(), [])
    return (None, candidates)


def _verify_function(fn: str, content: str) -> VerifyResult:
    """Check whether *fn* is defined as a function in *content*."""
    def_patterns = [
        re.compile(r"^\s*" + re.escape(fn) + r"\s*\(\)\s*\{?", re.MULTILINE),   # Bash fn() {
        re.compile(r"^\s*function\s+" + re.escape(fn) + r"\b", re.MULTILINE),    # Bash/JS function fn
        re.compile(r"^\s*def\s+" + re.escape(fn) + r"\s*\(", re.MULTILINE),      # Python def fn(
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+" + re.escape(fn) + r"\b", re.MULTILINE),  # JS/TS
    ]
    for pat in def_patterns:
        m = pat.search(content)
        if m:
            line_no = content[: m.start()].count("\n") + 1
            return VerifyResult(verified=True, evidence=f"function defined at line {line_no}", confidence=1.0)

    # Heuristic: token appears only as case-pattern  "  fn)"
    case_pat = re.compile(r"^\s+" + re.escape(fn) + r"\)", re.MULTILINE)
    any_occ = list(re.finditer(r"\b" + re.escape(fn) + r"\b", content))
    case_occ = list(case_pat.finditer(content))
    if any_occ and case_occ and len(any_occ) == len(case_occ):
        return VerifyResult(verified=False, evidence="token appears as case-pattern, not function definition", confidence=0.8)

    return VerifyResult(verified=False, evidence="function name not found in file", confidence=1.0)


def _verify_content(citation: Citation, path: Path) -> VerifyResult:
    """Size-cap, read, and kind-dispatch *citation* against the file at *path*.

    Extracted (§1aa) from the `verify()` tail so the ambiguous-basename
    auto-resolve branch (GH853) can call it once per candidate.
    """
    if path.stat().st_size > _FILE_SIZE_LIMIT:
        return VerifyResult(verified=False, evidence="file too large", confidence=1.0)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("citation_verifier: read error for %s: %s", path, exc)
        return VerifyResult(verified=False, evidence=f"read error: {exc}", confidence=1.0)

    if citation.kind in ("function", "case_vs_function"):
        return _verify_function(citation.identifier, content)

    if citation.kind == "snippet":
        haystack = _normalize_ws(content)
        needle_candidates = {
            _normalize_ws(_unescape_snippet(citation.identifier)),  # canonical escaped form
            _normalize_ws(citation.identifier),                     # raw, escaping-agnostic
            _normalize_ws(_decode_ws_escapes(_unescape_snippet(citation.identifier))),  # \n-wrapped multiline cite (GH922)
        }
        if any(c and c in haystack for c in needle_candidates):
            return VerifyResult(verified=True, evidence="snippet found (content-grep)", confidence=1.0)
        # Last-resort: backslash-escape-insensitive match (handles needles whose
        # backslashes were doubled/mangled upstream). Fires ONLY after exact misses.
        haystack_noesc = haystack.replace("\\", "")
        if any(c and c.replace("\\", "") in haystack_noesc for c in needle_candidates):
            return VerifyResult(verified=True, evidence="snippet found (content-grep, escape-insensitive)", confidence=0.9)
        return VerifyResult(verified=False, evidence="snippet not found in file", confidence=1.0)

    if citation.kind == "line":
        raw = citation.identifier.lstrip("L")
        try:
            n = int(raw)
        except ValueError:
            return VerifyResult(verified=False, evidence=f"invalid line identifier: {citation.identifier}", confidence=1.0)
        line_count = content.count("\n") + (0 if content.endswith("\n") else 1)
        if 1 <= n <= line_count:
            return VerifyResult(verified=True, evidence=f"line {n} within file ({line_count} lines)", confidence=1.0)
        return VerifyResult(verified=False, evidence=f"line {n} out of range (file has {line_count} lines)", confidence=1.0)

    return VerifyResult(verified=False, evidence=f"unknown citation kind: {citation.kind}", confidence=0.0)


def verify(citation: Citation, hal_root: Path, declared_new: set[str] | None = None) -> VerifyResult:
    """Resolve *citation.file_path* under *hal_root* and verify the cited identifier.

    *declared_new* (optional): basenames of planned-new artifacts (spec_lint_scope
    .declared_new_artifacts). If the cited file is absent from disk but its basename
    is declared-new, treat it as verified (planned-new exemption, GH761 §3b).
    """
    path = _resolve_path(citation.file_path, hal_root)
    if not path.exists():
        # Segment-walk fallback: bare-name → repo-segment unique match.
        if not Path(citation.file_path).is_absolute():
            resolved, candidates = _segment_walk_resolve(citation.file_path, hal_root)
            if resolved is not None:
                path = resolved
                # fall through to size + read + verify pipeline
            elif candidates:
                if len(candidates) > _AMBIG_RESOLVE_MAX:
                    return VerifyResult(
                        verified=False,
                        evidence=(
                            f"ambiguous: {len(candidates)} candidates: {', '.join(candidates)}"
                            " (too many to auto-resolve)"
                        ),
                        confidence=1.0,
                    )
                # Content-disambiguate: try each candidate, keep the ones that verify.
                verified_matches: list[tuple[str, VerifyResult]] = []
                for c in candidates:
                    p = (hal_root / c).resolve()
                    if not p.exists():
                        continue
                    r = _verify_content(citation, p)
                    if r.verified:
                        verified_matches.append((c, r))
                if len(verified_matches) == 1:
                    c, r = verified_matches[0]
                    return VerifyResult(
                        verified=True,
                        evidence=f"auto-resolved ambiguous basename to {c}: {r.evidence}",
                        confidence=min(r.confidence, 0.9),
                    )
                k = len(verified_matches)
                return VerifyResult(
                    verified=False,
                    evidence=f"ambiguous: {len(candidates)} candidates ({k} verified): {', '.join(candidates)}",
                    confidence=1.0,
                )
            else:
                if Path(citation.file_path).name in (declared_new or set()):
                    return VerifyResult(
                        verified=True,
                        evidence="planned-new artifact declared in spec §5",
                        confidence=1.0,
                    )
                return VerifyResult(verified=False, evidence=f"file not found: {citation.file_path}", confidence=1.0)
        else:
            if Path(citation.file_path).name in (declared_new or set()):
                return VerifyResult(
                    verified=True,
                    evidence="planned-new artifact declared in spec §5",
                    confidence=1.0,
                )
            return VerifyResult(verified=False, evidence=f"file not found: {citation.file_path}", confidence=1.0)
    return _verify_content(citation, path)


def verify_all(text: str, hal_root: Path) -> list[tuple[Citation, VerifyResult]]:
    """Extract all citations from *text* then verify each against *hal_root*.

    Returns an ordered list of (Citation, VerifyResult) pairs.
    """
    try:
        from bytedigger_engine.scripts.lib import spec_lint_scope  # noqa: PLC0415

        declared_new = spec_lint_scope.declared_new_artifacts(text)
    except ImportError:
        logger.debug("citation_verifier: spec_lint_scope not importable; declared_new disabled")
        declared_new = None
    return [(c, verify(c, hal_root, declared_new)) for c in extract_citations(text)]
