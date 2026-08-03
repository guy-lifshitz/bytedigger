"""Single-source owner of test-path router classifier (4961254A — manifest allowlist inversion).

Agreement trail: A1D4A4C9 (SYSTEMATIC parent 427E3727) introduced centralisation.
4961254A (SYSTEMATIC cascade-terminator) removed the unbounded dirt-blocklist
classifiers — the commit-steps now commit exactly what the worker manifest says,
so the subtraction-classifiers are structurally impossible and deleted.

Only the _is_test_path ROUTER survives: it partitions an already-bounded manifest
into prod vs test paths (not a subtraction from the dirty tree).
"""

import fnmatch as _fn

_TEST_FILENAME_PATTERNS = (
    "*.test.sh",  "*_test.sh",
    "*.test.ts",  "*.test.tsx", "*.test.js", "*.test.jsx",
    "*.test.mjs", "*.test.cjs",
    "test_*.py",  "*_test.py",
    "*_test.go",
    "*Tests.swift",
    "*_test.rs",
)
_TEST_PATH_SEGMENTS = ("__tests__", "tests", "test")


def _is_test_path(path: str) -> bool:
    """True if path matches a known test-file convention.

    - Filename pattern (e.g. *.test.sh, test_*.py) — sibling-naming convention.
    - Path segment (e.g. tests/, __tests__/) — directory-grouped convention.

    Used as a ROUTER to partition the worker-written-paths manifest into prod vs
    test subsets.  NOT a subtraction filter against the dirty working tree.
    """
    if not path:
        return False
    norm = path.replace("\\", "/")
    name = norm.rsplit("/", 1)[-1]
    if any(_fn.fnmatch(name, pat) for pat in _TEST_FILENAME_PATTERNS):
        return True
    segments = norm.split("/")
    if any(seg in _TEST_PATH_SEGMENTS for seg in segments):
        return True
    return False
