from __future__ import annotations
import os

SPEC_LINTS = ["scope-inverse-lint", "helper-extraction-lint", "spec-cite-lint", "spec-coverage-lint", "token-consistency-lint", "presence-triad-lint", "format-conversion-lint", "closure-evidence-lint", "mutation-two-sidedness-lint"]
TEST_LINTS = ["forbidden-import-lint", "stub-passability-lint", "empty-input-assert-lint"]
# bd#308: names the layer ratchets against HEAD (refuse only a file with MORE
# findings than its HEAD version) and the per-lint off switch of each. Not named
# `*_LINTS`: registry_names() sweeps every attribute with that suffix.
RATCHETED_NAMES = ("empty-input-assert-lint",)
LINT_OFF_ENVS = {"empty-input-assert-lint": "BD_EMPTY_INPUT_ASSERT_LINT"}
TS_TEST_LINTS = ["one-sided-predicate-lint"]
# bd#79: runs over any staged text file, not only specs and tests.
TEXT_LINTS = ["cyrillic-prose-lint"]

# bd#66 §2.1a — the canonical driver directory: the registry's own directory.
# realpath, not abspath: on macOS /tmp is a symlink to /private/tmp, and
# comparing an unexpanded path with an expanded one would red a correct layer.
DEFAULT_LINT_DIR = os.path.dirname(os.path.realpath(__file__))

# bd#66 §2.2 — names licensed to have no driver on disk. The enforced invariant
# is: every registry name must have either a driver in the lint directory or an
# entry here. A declaration licenses an absence; it does not suppress a driver
# that IS present. An entry matching no registry name is itself a refusal.
#
# bd#304: six spec lints left this list (drivers at the repo root, mapped in
# LINT_DRIVER_DIRS); bd#305: spec-coverage left it too (fed only a spec with an
# AC table, see feed_skip_reason); bd#315: token-consistency left it after the
# scanner stopped reporting file names and case-only drift. The one below is
# bd#323: spec-cite left it last, once the spec set was clean under the union
# rule (spec_cite.lint_spec_union). The list is now empty: every registry name
# has a driver.
DECLARED_ABSENT = []

_TS_TEST_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")

# bd#79: where each driver actually lives, when that is not the caller's
# build_dir. `cyrillic-prose-lint.py` sits at the repo root beside
# core-boundary-lint.py, because a hyphenated *.py inside `bytedigger_engine/`
# is unimportable and CI's import smoke globs that directory. Declared here so
# a caller resolves the path instead of guessing it -- a registry name whose
# driver cannot be executed is exactly the defect bd#66 is about.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
LINT_DRIVER_DIRS = {
    "cyrillic-prose-lint": _REPO_ROOT,
    "one-sided-predicate-lint": _REPO_ROOT,
    # bd#302: the two test lints; drivers sit at the repo root.
    "forbidden-import-lint": _REPO_ROOT,
    "stub-passability-lint": _REPO_ROOT,
    # bd#304: the six spec lints that left DECLARED_ABSENT.
    "scope-inverse-lint": _REPO_ROOT,
    "helper-extraction-lint": _REPO_ROOT,
    "presence-triad-lint": _REPO_ROOT,
    "format-conversion-lint": _REPO_ROOT,
    "closure-evidence-lint": _REPO_ROOT,
    "mutation-two-sidedness-lint": _REPO_ROOT,
    # bd#305: spec-coverage, fed only a spec that carries an AC table.
    "spec-coverage-lint": _REPO_ROOT,
    # bd#308: the empty-input-assert test lint, ratcheted against HEAD.
    "empty-input-assert-lint": _REPO_ROOT,
    # bd#315: token-consistency left DECLARED_ABSENT.
    "token-consistency-lint": _REPO_ROOT,
    # bd#323: spec-cite left DECLARED_ABSENT.
    "spec-cite-lint": _REPO_ROOT,
}


def driver_path(name: str, build_dir: str) -> str:
    return os.path.join(LINT_DRIVER_DIRS.get(name, build_dir), name + ".py")


def feed_skip_reason(name: str, path: str):  # -> str | None
    """bd#305: why the layer should not feed `path` to lint `name`, or None.

    spec-coverage-lint is fed only a spec that carries an AC table; the check
    is the rule's own module (`spec_coverage.check_ac_table`), not a copy. An
    unreadable or undecodable file returns None: the driver runs and reports
    exit 2 itself.
    """
    if name != "spec-coverage-lint":
        return None
    from bytedigger_engine import spec_coverage

    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        spec_coverage.check_ac_table(text)
    except spec_coverage.NoACTableError:
        return "no AC table"
    return None


# Text files the lint has nothing to say about: their bytes are not prose.
_NON_TEXT_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".whl",
    ".woff", ".woff2", ".ttf", ".so", ".dylib", ".pyc",
)

def is_spec_file(path: str) -> bool:
    # bd#81: case-insensitive. The repository's five frozen lot specs are named
    # `*_SPEC.md`, so a lowercase-only match measured the spec corpus at zero
    # when it is five -- and that measurement is what put the nine SPEC_LINTS
    # out of scope.
    return os.path.basename(path).lower().endswith("_spec.md")

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
