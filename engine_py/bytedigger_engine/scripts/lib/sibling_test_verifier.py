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

# Case-insensitive regex for accepted audit-block markers
_AUDIT_BLOCK_RE = re.compile(
    r"§3\.2|sibling-test\s+audit|sibling\s+test\s+audit|sibling-test-audit",
    re.IGNORECASE,
)


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
    2. If an audit block is present → return [] immediately (AC3).
    3. For each cited function, count sibling test files referencing it.
    4. Emit a SiblingFinding for each symbol with count > 0.
    Returns list sorted by offset.
    """
    # Import citation_verifier from the same lib dir (no engine deps).
    try:
        from bytedigger_engine.scripts.lib import citation_verifier  # noqa: PLC0415
    except ImportError:
        logger.warning("sibling_test_verifier: citation_verifier not importable; returning []")
        return []

    if not spec_text:
        return []

    # Step 1 — extract function citations only (Opus N3: filter kind=="function" explicitly)
    citations = [
        c for c in citation_verifier.extract_citations(spec_text)
        if c.kind == "function"
    ]
    if not citations:
        return []

    # Step 2 — early-return if audit block present (AC3)
    if _has_audit_block(spec_text):
        return []

    # Step 3+4 — check each symbol for sibling tests
    findings: list[SiblingFinding] = []
    for c in citations:
        symbol = c.identifier
        n = _count_sibling_tests(symbol, hal_root)
        if n > 0:
            findings.append(
                SiblingFinding(
                    offset=c.text_offset,
                    rule_id="sibling-audit-missing",
                    evidence=f"{symbol}:{n}-sibling-tests-no-audit-block",
                )
            )

    return sorted(findings, key=lambda f: f.offset)


def _has_audit_block(spec_text: str) -> bool:
    """Return True if *spec_text* contains any accepted sibling-audit marker.

    Accepted markers (case-insensitive):
      §3.2  |  sibling-test audit  |  sibling-test-audit  |  sibling test audit
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
