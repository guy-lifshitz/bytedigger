from __future__ import annotations
import os

SPEC_LINTS = ["scope-inverse-lint", "helper-extraction-lint", "spec-cite-lint", "spec-coverage-lint", "token-consistency-lint", "presence-triad-lint", "format-conversion-lint", "closure-evidence-lint", "mutation-two-sidedness-lint"]
TEST_LINTS = ["forbidden-import-lint", "stub-passability-lint"]
TS_TEST_LINTS = ["one-sided-predicate-lint"]

# bd#66 §2.1a — the canonical driver directory: the registry's own directory.
# realpath, not abspath: on macOS /tmp is a symlink to /private/tmp, and
# comparing an unexpanded path with an expanded one would red a correct layer.
DEFAULT_LINT_DIR = os.path.dirname(os.path.realpath(__file__))

# bd#66 §2.2 — names licensed to have no driver on disk. The enforced invariant
# is: every registry name must have either a driver in the lint directory or an
# entry here. A declaration licenses an absence; it does not suppress a driver
# that IS present. An entry matching no registry name is itself a refusal.
DECLARED_ABSENT = [
    "scope-inverse-lint",
    "helper-extraction-lint",
    "spec-cite-lint",
    "spec-coverage-lint",
    "token-consistency-lint",
    "presence-triad-lint",
    "format-conversion-lint",
    "closure-evidence-lint",
    "mutation-two-sidedness-lint",
    "forbidden-import-lint",
    "stub-passability-lint",
    "one-sided-predicate-lint",
]

_TS_TEST_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")

def is_spec_file(path: str) -> bool:
    return os.path.basename(path).endswith("_spec.md")

def is_test_file(path: str) -> bool:
    base = os.path.basename(path)
    return (base.startswith("test_") and base.endswith(".py")) or base.endswith("_test.py") or base == "conftest.py"

def is_ts_test_file(path: str) -> bool:
    return os.path.basename(path).endswith(_TS_TEST_SUFFIXES)

def classify_staged(paths):  # -> dict
    return {"specs": [p for p in paths if is_spec_file(p)],
            "tests": [p for p in paths if is_test_file(p)],
            "ts_tests": [p for p in paths if is_ts_test_file(p)]}

def nothing_to_lint(c) -> bool:
    return not c["specs"] and not c["tests"] and not c["ts_tests"]

def build_lint_commands(specs, tests, build_dir, ts_tests=()):  # -> list[dict]
    cmds = []
    for spec in specs:
        for name in SPEC_LINTS:
            cmds.append({"file": spec, "lint": name, "argv": [f"{build_dir}/{name}.py", "--spec", spec]})
    for test in tests:
        for name in TEST_LINTS:
            cmds.append({"file": test, "lint": name, "argv": [f"{build_dir}/{name}.py", test]})
    for ts_test in ts_tests:
        for name in TS_TEST_LINTS:
            cmds.append({"file": ts_test, "lint": name, "argv": [f"{build_dir}/{name}.py", ts_test]})
    return cmds
