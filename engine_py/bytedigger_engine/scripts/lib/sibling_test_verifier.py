"""Sibling-test verifier for HAL engine_py spec_lint (5153EDD4, §1a gate).

Detects when a spec cites a production function that has sibling tests
but omits a sibling-audit block. Lib-tier: stdlib + `lib.bounded_spawn`.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from bytedigger_engine.lib.git_port import git_read  # noqa: E402

logger = logging.getLogger(__name__)

# Test file globs passed to `git grep` (covers §1a dual-axis: __tests__/ + project-root)
# `*tests/*` matches root-level `tests/test_x.py` AND nested `a/b/tests/c.py` (git `*` spans `/`).
# `*__tests__/*` matches `__tests__/...` at any depth.
# `*.test.ts|sh|py` match those suffixes at any depth.
# `test_*.py` matches root-level pytest files; `*/test_*.py` matches nested pytest files.
_TEST_GLOBS = [
    "*tests/*",
    "*__tests__/*",
    "*.test.ts",
    "*.test.sh",
    "*.test.py",
    "test_*.py",
    "*/test_*.py",
]

# 4197B484 (GH1120) §1.1/§1g — the single canonical source of the accepted
# audit-marker spellings. Rendered into _AUDIT_BLOCK_RE at import time and
# re-used verbatim by directed_repair._RULE_GUIDANCE["sibling-audit-missing"]
# and phase_45_spec._sibling_vocab_hint (drift pinned by AC10).
AUDIT_MARKER_VOCABULARY: tuple[str, ...] = (
    "§3.2",
    "sibling-test audit",
    "sibling-test-audit",
    "sibling test audit",
    "sibling-audit:",
)

# 4197B484 (GH1121) §1.3 — a symbol matched by MORE than this many test files is
# too generic to audit; it yields an advisory `ambiguous-symbol` finding instead
# of `sibling-audit-missing`. Baseline in spec §2.3.
AMBIGUOUS_SYMBOL_THRESHOLD: int = 60


def _render_marker_pattern(marker: str) -> str:
    """Render one vocabulary member as a regex alternative.

    A literal space becomes `\\s+` (preserving today's whitespace flexibility —
    a naive `re.escape(marker)` would narrow `sibling-test\\s+audit` to exactly
    one space); every other character is `re.escape`d.
    """
    return r"\s+".join(re.escape(part) for part in marker.split(" "))


# Case-insensitive regex for accepted audit-block markers
_AUDIT_BLOCK_RE = re.compile(
    "|".join(_render_marker_pattern(m) for m in AUDIT_MARKER_VOCABULARY),
    re.IGNORECASE,
)

# Markdown heading primitives for the per-marker scope slice (§1.1). The
# `_section_slice` algorithm is DUPLICATED here rather than imported from
# ground_truth_verifier: scripts/lib is import-isolated from engine_py and
# ground_truth_verifier.py:10-13 documents that exact duplication precedent.
_HEADING_PREFIX_RE = re.compile(r"^(#{1,6})\s")
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)

# Identifier-shaped token, 3-char floor (1-2 char symbols are exactly the class
# AMBIGUOUS_SYMBOL_THRESHOLD already classifies as unauditable).
_IDENT_TOKEN_RE = re.compile(r"[A-Za-z_]\w{2,}")


@dataclass(frozen=True)
class SiblingFinding:
    """A single §1a sibling-audit finding from a spec."""

    offset: int    # byte offset in spec text of the triggering citation
    rule_id: str   # "sibling-audit-missing"
    evidence: str  # "<fn>:N-sibling-tests-no-audit-block"


def find_missing_audits(spec_text: str, hal_root: Path) -> list[SiblingFinding]:
    """Return sibling-audit findings for *spec_text* relative to *hal_root*.

    Algorithm:
    1. Extract function citations from spec_text via citation_verifier.
    2. Collect the PER-SYMBOL audit scope via audited_symbols() (4197B484 §1.1 —
       an audit block no longer silences the whole document).
    3. For each non-audited cited function, count sibling test files.
    4. Branch precedence: audited → over-threshold (`ambiguous-symbol`) →
       missing (`sibling-audit-missing`).
    Returns list sorted by offset.
    """
    # Import citation_verifier from the same lib dir (no engine deps).
    try:
        from bytedigger_engine.scripts.lib import citation_verifier  # noqa: PLC0415
    except ImportError:
        logger.warning("sibling_test_verifier: citation_verifier not importable; returning []")
        return [SiblingFinding(offset=0, rule_id="sibling-verifier-unavailable", evidence="citation_verifier-unimportable")]

    if not spec_text:
        return []

    # Step 1 — extract function citations only (Opus N3: filter kind=="function" explicitly)
    citations = [
        c for c in citation_verifier.extract_citations(spec_text)
        if c.kind == "function"
    ]
    if not citations:
        return []

    # Step 2 — per-symbol audit scope (4197B484 §1.1)
    audited = audited_symbols(spec_text)

    # Step 3+4 — branch precedence: audited → over-threshold → missing
    findings: list[SiblingFinding] = []
    for c in citations:
        symbol = c.identifier
        # (a) audited wins regardless of match count; no git grep is spawned.
        if symbol in audited:
            continue
        n = _count_sibling_tests(symbol, hal_root)
        # (b) too generic to audit → advisory (non-blocking, see lint_spec.py).
        if n > AMBIGUOUS_SYMBOL_THRESHOLD:
            findings.append(
                SiblingFinding(
                    offset=c.text_offset,
                    rule_id="ambiguous-symbol",
                    evidence=(
                        f"{symbol}:{n}-matches-exceeds-"
                        f"{AMBIGUOUS_SYMBOL_THRESHOLD}-specificity-threshold"
                    ),
                )
            )
        # (c) genuine un-audited symbol with sibling tests.
        elif n > 0:
            findings.append(
                SiblingFinding(
                    offset=c.text_offset,
                    rule_id="sibling-audit-missing",
                    evidence=f"{symbol}:{n}-sibling-tests-no-audit-block",
                )
            )

    return sorted(findings, key=lambda f: f.offset)


def _section_slice(text: str, start: int, search_from: int, level: int) -> str:
    """Return text[start:] truncated at the next heading of same-or-shallower level.

    Local duplicate of ground_truth_verifier._section_slice (scripts/lib is
    import-isolated from engine_py; see module header of that file).
    """
    for hm in _ANY_HEADING_RE.finditer(text, search_from):
        if len(hm.group(1)) <= level:
            return text[start:hm.start()]
    return text[start:]


def audited_symbols(spec_text: str) -> set[str]:
    """Return the set of identifier-shaped symbols declared audited in *spec_text*.

    Per marker occurrence:
      - marker on a markdown heading line → scope is that heading's section
        (heading → next heading of same-or-shallower level);
      - otherwise (inline marker, e.g. `sibling-audit: n/a — <reason>`) → scope
        is the marker's own physical line.
    Membership is exact set equality at the call site, never substring.
    """
    if not _has_audit_block(spec_text):
        return set()

    symbols: set[str] = set()
    for m in _AUDIT_BLOCK_RE.finditer(spec_text):
        line_start = spec_text.rfind("\n", 0, m.start()) + 1
        line_end = spec_text.find("\n", m.start())
        if line_end == -1:
            line_end = len(spec_text)
        line = spec_text[line_start:line_end]
        heading = _HEADING_PREFIX_RE.match(line)
        if heading is not None:
            scope = _section_slice(spec_text, line_start, line_end, len(heading.group(1)))
        else:
            scope = line
        symbols.update(_IDENT_TOKEN_RE.findall(scope))
    return symbols


def _has_audit_block(spec_text: str) -> bool:
    """Return True if *spec_text* contains any accepted sibling-audit marker.

    Accepted markers (case-insensitive) = AUDIT_MARKER_VOCABULARY:
      §3.2 | sibling-test audit | sibling-test-audit | sibling test audit |
      sibling-audit:
    """
    return bool(_AUDIT_BLOCK_RE.search(spec_text))


def _count_sibling_tests(symbol: str, hal_root: Path) -> int:
    """Return the number of test files under *hal_root* that reference *symbol*.

    Uses `git -C <hal_root> grep -lw -- <symbol> -- <TEST_GLOBS>`.
    Fail-open: any non-zero rc, timeout, or OSError → return 0, never raise (Opus N2).
    """
    try:
        from bytedigger_engine.scripts.lib import spec_lint_scope  # noqa: PLC0415

        pathspecs = _TEST_GLOBS + spec_lint_scope.corpus_exclude_pathspecs(keep_tests=True)
    except ImportError:
        logger.debug("sibling_test_verifier: spec_lint_scope not importable; using legacy globs only")
        pathspecs = _TEST_GLOBS

    try:
        result = git_read(
            ["grep", "-lw", "--", symbol, "--"] + pathspecs,
            dir_=str(hal_root),
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("sibling_test_verifier: git grep failed for %r: %s", symbol, exc)
        return 0

    # rc==0 → matches found; rc==1 → no matches; rc==128 → not a git repo; any other → error
    # Count stdout lines ONLY when rc==0 (Opus N2)
    if result.returncode != 0:
        return 0

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    return len(lines)
