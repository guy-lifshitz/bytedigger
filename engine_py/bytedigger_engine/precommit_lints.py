from __future__ import annotations
import os

SPEC_LINTS = ["scope-inverse-lint", "helper-extraction-lint", "spec-cite-lint", "spec-coverage-lint", "token-consistency-lint", "presence-triad-lint", "format-conversion-lint", "closure-evidence-lint", "mutation-two-sidedness-lint"]
TEST_LINTS = ["forbidden-import-lint", "stub-passability-lint"]
TS_TEST_LINTS = ["one-sided-predicate-lint"]
# bd#79: runs over any staged text file, not only specs and tests.
TEXT_LINTS = ["cyrillic-prose-lint"]

_TS_TEST_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")

# bd#79: where each driver actually lives, when that is not the caller's
# build_dir. `cyrillic-prose-lint.py` sits at the repo root beside
# core-boundary-lint.py, because a hyphenated *.py inside `bytedigger_engine/`
# is unimportable and CI's import smoke globs that directory. Declared here so
# a caller resolves the path instead of guessing it -- a registry name whose
# driver cannot be executed is exactly the defect bd#66 is about.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
LINT_DRIVER_DIRS = {"cyrillic-prose-lint": _REPO_ROOT}


def driver_path(name: str, build_dir: str) -> str:
    return os.path.join(LINT_DRIVER_DIRS.get(name, build_dir), name + ".py")


# Text files the lint has nothing to say about: their bytes are not prose.
_NON_TEXT_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".whl",
    ".woff", ".woff2", ".ttf", ".so", ".dylib", ".pyc",
)

def is_spec_file(path: str) -> bool:
    return os.path.basename(path).endswith("_spec.md")

def is_test_file(path: str) -> bool:
    base = os.path.basename(path)
    return (base.startswith("test_") and base.endswith(".py")) or base.endswith("_test.py") or base == "conftest.py"

def is_ts_test_file(path: str) -> bool:
    return os.path.basename(path).endswith(_TS_TEST_SUFFIXES)

def is_text_file(path: str) -> bool:
    return not os.path.basename(path).lower().endswith(_NON_TEXT_SUFFIXES)

def classify_staged(paths):  # -> dict
    return {"specs": [p for p in paths if is_spec_file(p)],
            "tests": [p for p in paths if is_test_file(p)],
            "ts_tests": [p for p in paths if is_ts_test_file(p)],
            "texts": [p for p in paths if is_text_file(p)]}

def nothing_to_lint(c) -> bool:
    return not c["specs"] and not c["tests"] and not c["ts_tests"] and not c.get("texts")

def build_lint_commands(specs, tests, build_dir, ts_tests=(), texts=()):  # -> list[dict]
    cmds = []
    for spec in specs:
        for name in SPEC_LINTS:
            cmds.append({"file": spec, "lint": name, "argv": [driver_path(name, build_dir), "--spec", spec]})
    for test in tests:
        for name in TEST_LINTS:
            cmds.append({"file": test, "lint": name, "argv": [driver_path(name, build_dir), test]})
    for ts_test in ts_tests:
        for name in TS_TEST_LINTS:
            cmds.append({"file": ts_test, "lint": name, "argv": [driver_path(name, build_dir), ts_test]})
    for text in texts:
        for name in TEXT_LINTS:
            cmds.append({"file": text, "lint": name, "argv": [driver_path(name, build_dir), text]})
    return cmds
