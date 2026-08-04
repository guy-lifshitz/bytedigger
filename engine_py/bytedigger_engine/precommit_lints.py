from __future__ import annotations
import os

SPEC_LINTS = ["scope-inverse-lint", "helper-extraction-lint", "spec-cite-lint", "spec-coverage-lint", "token-consistency-lint", "presence-triad-lint", "format-conversion-lint"]
TEST_LINTS = ["forbidden-import-lint", "stub-passability-lint"]

def is_spec_file(path: str) -> bool:
    return os.path.basename(path).endswith("_spec.md")

def is_test_file(path: str) -> bool:
    base = os.path.basename(path)
    return (base.startswith("test_") and base.endswith(".py")) or base.endswith("_test.py") or base == "conftest.py"

def classify_staged(paths):  # -> dict
    return {"specs": [p for p in paths if is_spec_file(p)],
            "tests": [p for p in paths if is_test_file(p)]}

def build_lint_commands(specs, tests, build_dir):  # -> list[dict]
    cmds = []
    for spec in specs:
        for name in SPEC_LINTS:
            cmds.append({"file": spec, "lint": name, "argv": [f"{build_dir}/{name}.py", "--spec", spec]})
    for test in tests:
        for name in TEST_LINTS:
            cmds.append({"file": test, "lint": name, "argv": [f"{build_dir}/{name}.py", test]})
    return cmds
