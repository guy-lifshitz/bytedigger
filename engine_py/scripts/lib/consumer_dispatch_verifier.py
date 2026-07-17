"""Consumer-dispatch verifier for HAL engine_py spec_lint (5153EDD4 Ship 2, §1o gate).

Detects when a spec introduces a named dispatch token (status/sentinel/verdict assignment)
but does NOT cite the consumer files that dispatch on that value.  Lib-tier: stdlib +
`lib.bounded_spawn`.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from bounded_spawn import bounded_run  # noqa: E402

logger = logging.getLogger(__name__)

# Regex: capture named dispatch values assigned to status/sentinel/verdict keywords.
# Case-insensitive on keyword; value must start with a lowercase letter, length >= 3.
# ALL-CAPS values (e.g. STATUS=BLOCK) are excluded to avoid FP noise.
# Excludes bare integers (exit codes) — §1b calibration decision.
_DISPATCH_VALUE_RE = re.compile(
    r'\b(?i:status|sentinel|verdict)\b\s*[:=]\s*["\'\`]?([a-z][a-z0-9_]{2,})',
)

# Regex for bare file-path tokens in spec text (for _cited_basenames fallback).
_BARE_PATH_RE = re.compile(r'[\w./-]+\.(?:sh|py|ts)')

# Heading that opens an AC / §3 region. re.MULTILINE so ^ matches line starts.
_AC_HEADING_RE = re.compile(
    r'^\s*#+\s*(?:§\s*3\b|acceptance\s+criteria)',
    re.IGNORECASE | re.MULTILINE,
)
# Any next ##-level heading (signals end of the AC region).
_NEXT_H2_RE = re.compile(r'^\s*##\s+\S', re.MULTILINE)

# Pervasiveness threshold (GH368, §2.3): dispatch values with more than this
# many uncited consumers are treated as pervasive domain enums and skipped
# (advisory noise reduction). max_consumers <= 0 disables the threshold.
DEFAULT_MAX_CONSUMERS = 8


@dataclass(frozen=True)
class ConsumerFinding:
    """A single §1o consumer-dispatch finding from a spec."""

    offset: int    # text offset in spec of the triggering token
    rule_id: str   # always "consumer-dispatch-missing"
    evidence: str  # "<value>:<N>-consumers-not-cited"


def find_uncited_consumers(
    spec_text: str, hal_root: Path, max_consumers: int = DEFAULT_MAX_CONSUMERS
) -> list[ConsumerFinding]:
    """Return consumer-dispatch findings for *spec_text* relative to *hal_root*.

    Algorithm:
    1. Extract (value, offset) pairs via _extract_dispatch_values.
    2. Build cited basenames via _cited_basenames.
    3. For each unique value, run _consumers_dispatching; filter uncited.
    4. If max_consumers > 0 and len(uncited) > max_consumers, treat as a
       pervasive domain enum and skip (advisory noise reduction, GH368).
    5. Emit ConsumerFinding if uncited non-empty.
    Returns list sorted by offset.
    """
    if not spec_text:
        return []

    vals = _extract_dispatch_values(spec_text)
    if not vals:
        return []

    cited = _cited_basenames(spec_text)

    # Dedup values, preserving first offset per value.
    seen: dict[str, int] = {}
    for value, offset in vals:
        if value not in seen:
            seen[value] = offset

    findings: list[ConsumerFinding] = []
    for value, offset in seen.items():
        consumers = _consumers_dispatching(value, hal_root)
        uncited = [p for p in consumers if Path(p).name not in cited]
        if max_consumers > 0 and len(uncited) > max_consumers:
            logger.debug(
                "consumer_dispatch_verifier: %r pervasive (%d consumers > %d) — skipped",
                value, len(uncited), max_consumers,
            )
            continue
        if uncited:
            # GH761 commit-2: append the sorted uncited paths so directed repair
            # (lib/directed_repair._render_findings) is no longer blind to WHICH
            # files must be cited. Prefix "<value>:<N>-consumers-not-cited" is
            # unchanged; the driver + _normalize_spec_lint_findings preserve
            # everything after the rule verbatim (comma-joined paths, no colons).
            uncited_sorted = sorted(uncited)
            findings.append(
                ConsumerFinding(
                    offset=offset,
                    rule_id="consumer-dispatch-missing",
                    evidence=(
                        f"{value}:{len(uncited)}-consumers-not-cited:"
                        + ",".join(uncited_sorted)
                    ),
                )
            )

    return sorted(findings, key=lambda f: f.offset)


def _acceptance_spans(spec_text: str) -> list[tuple[int, int]]:
    """Return (start, end) byte spans for each AC/§3 region in *spec_text*.

    Each span starts at the heading's match.start() and ends at the start of the
    next ##-level heading, or len(spec_text) if none follows.
    Returns [] if no AC/§3 heading is found.
    """
    spans: list[tuple[int, int]] = []
    for heading in _AC_HEADING_RE.finditer(spec_text):
        start = heading.start()
        # Search for the next ##+ heading AFTER this heading ends.
        next_h = _NEXT_H2_RE.search(spec_text, heading.end())
        end = next_h.start() if next_h else len(spec_text)
        spans.append((start, end))
    return spans


def _extract_dispatch_values(spec_text: str) -> list[tuple[str, int]]:
    """Extract (value, offset) pairs for named dispatch tokens in AC/§3 regions only.

    Matches `status`, `sentinel`, or `verdict` keyword (case-insensitive via inline
    flag) followed by `=` or `:` and a lowercase value token (length >= 3).
    Only matches whose offset falls within an _acceptance_spans region are kept.
    Returns [] if no AC heading exists. Bare integers and exit-codes excluded (§1b).
    """
    spans = _acceptance_spans(spec_text)
    if not spans:
        return []

    results: list[tuple[str, int]] = []
    for m in _DISPATCH_VALUE_RE.finditer(spec_text):
        pos = m.start()
        if any(start <= pos < end for start, end in spans):
            results.append((m.group(1), pos))
    return results


def _consumers_dispatching(value: str, hal_root: Path) -> list[str]:
    """Return repo-relative file paths that dispatch on *value* under *hal_root*.

    Pattern has TWO alternatives (§2.1 F1 fix):
      alt-1: comparison/match-case form  (==|=~|case)[[:space:]]*["']?<value>
      alt-2: bash case-label form        ^[[:space:]]*["']?<value>["']?[[:space:]]*)

    Fail-open: any exception, timeout, or rc >= 2 → return [], never raise.
    rc==1 from git grep means no matches → return [] (not an error).
    """
    # value matches [a-z][a-z0-9_]{2,} (lowercase + digits + underscore, see
    # _DISPATCH_VALUE_RE) — safe to interpolate directly (no regex metachars).
    try:
        import spec_lint_scope  # noqa: PLC0415

        dispatch_pattern = spec_lint_scope.boundary_pattern(value)
        corpus = spec_lint_scope.corpus_pathspecs()
    except ImportError:
        logger.debug("consumer_dispatch_verifier: spec_lint_scope not importable; using legacy inline pattern")
        dispatch_pattern = (
            f'(==|=~|case)[[:space:]]*["\'"]?{value}([^A-Za-z0-9_]|$)'
            f'|^[[:space:]]*["\'"]?{value}["\'"]?[[:space:]]*\\)'
        )
        corpus = ["*.sh", "*.py", "*.ts"]
    cmd = [
        "git", "grep", "-lE", "--",
        dispatch_pattern,
        "--", *corpus,
    ]
    try:
        result = bounded_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=str(hal_root),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("consumer_dispatch_verifier: git grep failed for %r: %s", value, exc)
        return []

    # rc==0 → matches; rc==1 → no matches (not an error); rc>=2 → error → fail-open
    if result.returncode >= 2:
        logger.debug(
            "consumer_dispatch_verifier: git grep rc=%d for %r: %s",
            result.returncode, value, result.stderr.strip(),
        )
        return []
    if result.returncode != 0:
        return []

    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    return lines


def _cited_basenames(spec_text: str) -> set[str]:
    """Return the set of file basenames explicitly cited in *spec_text*.

    Union of:
    - citation_verifier.extract_citations(spec_text) basenames
    - bare path tokens matching [\\w./-]+\\.(sh|py|ts) in spec_text
    Fail-open: if citation_verifier not importable, fall back to bare-path tokens only.
    """
    basenames: set[str] = set()

    try:
        import citation_verifier  # noqa: PLC0415
        for c in citation_verifier.extract_citations(spec_text):
            if c.file_path:
                basenames.add(Path(c.file_path).name)
    except ImportError:
        logger.debug("consumer_dispatch_verifier: citation_verifier not importable; using bare-path fallback")

    for m in _BARE_PATH_RE.finditer(spec_text):
        token = m.group(0)
        basenames.add(Path(token).name)

    return basenames
