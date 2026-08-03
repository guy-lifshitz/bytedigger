"""Shared spec_lint scope/boundary/planned-new chokepoint (GH761).

Single chokepoint for three primitives reused by consumer_dispatch_verifier,
citation_verifier, and sibling_test_verifier (5153EDD4 family §1o/§1a gates):

  - corpus_pathspecs / corpus_exclude_pathspecs: canonical `git grep` pathspec
    scoping (positive globs + `:(exclude)` non-corpus/test excludes) so
    non-prod corpora (experiments/, fixtures/, archive/, vendor, tests/) are
    never mistaken for dispatch consumers.
  - boundary_pattern: portable ERE token-boundary guard (kills `===`-banner
    and `case`-in-`lowercase` false positives; see incident ppba#1273 cal9).
  - declared_new_artifacts: extract basenames of source files a spec declares
    it will create (`CREATE:` / `NEW:` markers), used by citation_verifier to
    skip "file not found" for planned-new artifacts.

Lib-tier: stdlib only. No engine_py deps. All callers must import this
module fail-open (ImportError -> legacy inline behavior, never raise).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_FILE_GLOBS = ("*.sh", "*.py", "*.ts")

# Always-excluded non-corpus directories/patterns (both exclude tiers).
NON_CORPUS_EXCLUDES = ("*experiments/*", "*fixtures/*", "*archive/*", "*vendor*")

# Test-file excludes; dropped when keep_tests=True (sibling verifier needs
# to search test files but still wants archive/fixtures/vendor excluded).
TEST_EXCLUDES = ("*tests/*", "*__tests__/*", "test_*")

_ENV_VAR = "HAL_SPEC_LINT_CORPUS_EXCLUDE"

_CREATE_NEW_RE = re.compile(
    r'(?:CREATE|NEW):\s*`?([\w./-]+\.(?:sh|py|ts|tsx|js))`?',
)


def _env_extra_excludes() -> list[str]:
    """Return extra exclude patterns from env HAL_SPEC_LINT_CORPUS_EXCLUDE.

    Comma-split, strip whitespace, drop blanks. Unset/blank -> [].
    """
    raw = os.environ.get(_ENV_VAR, "")
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def corpus_exclude_pathspecs(*, keep_tests: bool = False) -> list[str]:
    """Return `:(exclude)<pat>` pathspecs for the non-corpus scope.

    Always includes NON_CORPUS_EXCLUDES; includes TEST_EXCLUDES unless
    keep_tests=True. Env extras (HAL_SPEC_LINT_CORPUS_EXCLUDE) are appended
    in both branches.
    """
    patterns = list(NON_CORPUS_EXCLUDES)
    if not keep_tests:
        patterns.extend(TEST_EXCLUDES)
    patterns.extend(_env_extra_excludes())
    return [f":(exclude){p}" for p in patterns]


def corpus_pathspecs(file_globs: tuple[str, ...] = DEFAULT_FILE_GLOBS) -> list[str]:
    """Return the canonical git-grep pathspec list: positive globs then excludes."""
    return list(file_globs) + corpus_exclude_pathspecs(keep_tests=False)


def boundary_pattern(token: str) -> str:
    """Return an ERE (for `git grep -E`) detecting a dispatch on *token*.

    *token* must match [a-z][a-z0-9_]{2,} (caller-guaranteed, no metachars).
    Three alternatives: comparison (==|=~), match/case keyword, bash
    case-label. Uses the portable `(^|[^A-Za-z0-9_])` boundary form, not \\b.
    """
    quote = '["\'`]?'
    alt1 = rf'(^|[^=])(==|=~)[[:space:]]*{quote}{token}([^A-Za-z0-9_]|$)'
    alt2 = rf'(^|[^A-Za-z0-9_])case[[:space:]]*{quote}{token}([^A-Za-z0-9_]|$)'
    alt3 = rf'^[[:space:]]*{quote}{token}{quote}[[:space:]]*\)'
    return f'{alt1}|{alt2}|{alt3}'


def declared_new_artifacts(spec_text: str) -> set[str]:
    """Return basenames of source artifacts a spec declares under CREATE:/NEW:.

    Empty/None text -> set(). Only source-file extensions (sh/py/ts/tsx/js)
    are considered; other extensions (e.g. .md) are ignored.
    """
    if not spec_text:
        return set()
    return {Path(m).name for m in _CREATE_NEW_RE.findall(spec_text)}
