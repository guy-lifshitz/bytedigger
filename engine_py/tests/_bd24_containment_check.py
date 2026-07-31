"""Coverage-diff containment check for bd#24 (spec §0.9 direction 1).

NOT a pytest test and deliberately not named like one: it shells out to `git`
to read bd#22's round-9 artifact, and the CI pytest job runs from an installed
wheel with no repository around it. The leading underscore keeps pytest from
collecting it, matching `_live_repo_sentinel.py`'s precedent in this directory.

Run it from anywhere in the worktree:

    python3 engine_py/tests/_bd24_containment_check.py

Exit status is 0 when containment holds and 1 when it does not, so it can be
wired into a pre-GREEN step later without being reinterpreted.

WHY THIS EXISTS AS A COMMITTED SCRIPT RATHER THAN A NUMBER IN THE SPEC. bd#22
round 3 deleted `_SEAM_NOT_PINNED_SPEC`, the only fixture covering check 3's
base case, while satisfying its requirement to the letter; nobody noticed, and
round 4 rejected on it. §0.9 therefore requires every round to show that the
candidates the previous fixture set killed are still killed. bd#24's rounds are
additive, so containment against the round-9 artifact IS that proof -- and a
proof nobody can re-run is a claim. Gate rounds 1 and 2 both lacked a shell and
so could endorse no containment numbers; this file is the answer to that, not a
second assertion of the same numbers.

Fixture constants are compared by value (`ast.literal_eval`) rather than by
source text, so reflowed quoting or an added comment cannot be mistaken for a
changed fixture, and a changed fixture cannot hide behind identical formatting.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

# bd#22 round 9 -- the last commit whose test file still carried AC-C5, before
# 5689b42 moved the AC to bd#24.
PREDECESSOR_REV = "d39371f"
PREDECESSOR_PATH = "engine_py/tests/test_bd22_contracts.py"
CURRENT_PATH = Path(__file__).with_name("test_bd24_quant_lint.py")


def _fixture_constants(source: str) -> dict[str, str]:
    """Module-level `_*_SPEC` string constants, by value."""
    return {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.startswith("_")
        and node.targets[0].id.endswith("SPEC")
    }


def _ac_c5_tests(source: str) -> set[str]:
    return {
        fn.name
        for cls in ast.parse(source).body
        if isinstance(cls, ast.ClassDef)
        for fn in cls.body
        if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_ac_c5")
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    previous = subprocess.run(
        ["git", "show", f"{PREDECESSOR_REV}:{PREDECESSOR_PATH}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    ).stdout
    current = CURRENT_PATH.read_text()

    old_fixtures = _fixture_constants(previous)
    new_fixtures = _fixture_constants(current)
    old_tests = _ac_c5_tests(previous)
    new_tests = _ac_c5_tests(current)

    absent = sorted(k for k in old_fixtures if k not in new_fixtures)
    changed = sorted(
        k
        for k in old_fixtures
        if k in new_fixtures and new_fixtures[k] != old_fixtures[k]
    )
    lost_tests = sorted(old_tests - new_tests)

    print(f"containment of {PREDECESSOR_REV}:{PREDECESSOR_PATH} in {CURRENT_PATH.name}")
    print(
        f"  carried fixture constants: {len(old_fixtures) - len(absent)}/"
        f"{len(old_fixtures)} present, {len(changed)} modified"
    )
    print(f"    absent : {absent or 'NONE'}")
    print(f"    changed: {changed or 'NONE'}")
    print(f"  carried tests: {len(old_tests & new_tests)}/{len(old_tests)} present")
    print(f"    absent : {lost_tests or 'NONE'}")
    print(
        f"  totals now: {len(new_fixtures)} fixtures "
        f"({len(old_fixtures)} carried + {len(new_fixtures) - len(old_fixtures)} added), "
        f"{len(new_tests)} tests"
    )

    if absent or changed or lost_tests:
        print("CONTAINMENT VIOLATED — spec §0.9 direction 1 applies: state which")
        print("candidates the previous fixture set killed and show each is still killed.")
        return 1
    print("CONTAINMENT HOLDS — the round is additive over the round-9 fixture set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
