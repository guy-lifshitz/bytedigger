"""corpus_extract.py — real post-mortem corpus extractor for C925E92A P2 proof harness.

Pure, stdlib-only, no I/O side-effects beyond reading files in the passed decisions_dir.
No subprocess, no telemetry, no side-effects on import.  Mirrors net_new_delta.py shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# §1: current_failed extraction patterns (first-hit wins, priority a→b→c→d)
# ---------------------------------------------------------------------------

# Rule a: last "NNNp/NNNs/NNNF" triple in the file
_RE_TRIPLE = re.compile(r"(\d+)P/(\d+)S/(\d+)F", re.IGNORECASE)

# Rule b: last case-insensitive "NNN fail(ed|ures?)?" word-boundary match
_RE_FAIL_WORD = re.compile(r"(\d+)\s+fail(?:ed|ures?)?\b", re.IGNORECASE)

# Rule c: pass count (used only to confirm GOOD signal when a/b absent)
_RE_PASS = re.compile(r"(\d+)\s+pass", re.IGNORECASE)

# ---------------------------------------------------------------------------
# §2: BAD / GOOD narrative-signal patterns
# ---------------------------------------------------------------------------

# BAD: leading digit ≥ 1 excludes "zero/no regressions"
_RE_BAD = re.compile(
    r"\b([1-9]\d*)\s+(?:net[-_ ]?new\s+)?(?:integration\s+)?(?:NEW\s+)?regress",
    re.IGNORECASE,
)

# GOOD: zero/no-regression phrasings
_RE_GOOD = re.compile(
    r"zero[ -]?regress|no[ -]?regress|0[ -]?regress|no new regress",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CorpusRow:
    """One extracted row from a post-mortem file."""

    baseline_failed: int | None
    current_failed: int
    expected_class: str   # narrative-derived, NOT classify-derived
    source: str           # post-mortem filename


def extract_corpus(decisions_dir: Path) -> list[CorpusRow]:
    """Extract CorpusRows from all *post_mortem*.md files in decisions_dir.

    Conservative high-precision extractor: emits a row ONLY when the narrative
    is unambiguous; skips otherwise.  Same corpus → same rows deterministically.
    Output is sorted by source filename.
    """
    rows: list[CorpusRow] = []

    for path in sorted(decisions_dir.glob("*post_mortem*.md")):
        text = path.read_text(errors="ignore")

        # ----------------------------------------------------------------
        # Step 1: determine current_failed (priority a → b → c → d)
        # ----------------------------------------------------------------
        triple_matches = _RE_TRIPLE.findall(text)
        if triple_matches:
            # Rule a: last P/S/F triple; F is group index 2
            current_failed = int(triple_matches[-1][2])
        else:
            fail_matches = _RE_FAIL_WORD.findall(text)
            if fail_matches:
                # Rule b: last "NNN failed/failures?" match
                current_failed = int(fail_matches[-1])
            else:
                # Rule c: GOOD signal + pass count present → current_failed = 0
                good_match = _RE_GOOD.search(text)
                pass_match = _RE_PASS.search(text)
                if good_match and pass_match:
                    current_failed = 0
                else:
                    # Rule d: skip
                    continue

        # ----------------------------------------------------------------
        # Step 2: determine (baseline_failed, expected_class) — BAD > GOOD
        # ----------------------------------------------------------------
        bad_match = _RE_BAD.search(text)
        good_match = _RE_GOOD.search(text)

        if bad_match:
            # BAD signal takes priority
            n = int(bad_match.group(1))
            if current_failed == 0:
                # Contradictory: bad signal but 0 current fails → skip
                continue
            expected_class = "net_new_regression"
            baseline_failed = max(0, current_failed - n)
        elif good_match:
            # GOOD signal (no BAD)
            if current_failed == 0:
                expected_class = "clean"
                baseline_failed = 0
            else:
                expected_class = "preexisting_only"
                baseline_failed = current_failed
        else:
            # Ambiguous → skip
            continue

        rows.append(
            CorpusRow(
                baseline_failed=baseline_failed,
                current_failed=current_failed,
                expected_class=expected_class,
                source=path.name,
            )
        )

    # Output sorted by source filename (determinism guaranteed by sorted(glob))
    return rows
