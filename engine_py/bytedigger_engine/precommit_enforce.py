"""bd#66 — the pre-commit lint enforcement layer.

Invoked by `githooks/pre-commit` BY PATH (never with `-m`), so `__package__` is
None and no relative import may be used here. The module bootstraps its own
`sys.path` from `__file__` and never relies on an inherited `PYTHONPATH`: a bare
developer clone has none.

Order is fixed (spec §2.1): the REGISTRY pre-pass first, the staged-file plan
second. The pre-pass runs on every commit, docs-only included, and is never
gated on the `BD66_LINT_DIR` override — that variable is an override on top of
the canon `precommit_lints.DEFAULT_LINT_DIR` (spec §2.1a), never a precondition.
Unset or empty means "canon"; it never means "nothing to check".

Outcomes (spec §2.1):
  0  a planned command whose driver was absent at pre-pass time is SKIPPED;
  1  a registry name with no driver and no declaration, or an orphaned
     declaration, refuses with BD66-REFUSE-MISSING-DRIVER;
  2  a driver exiting 1 refuses with BD66-REFUSE-VIOLATION;
  3  a driver exiting anything else, or failing to execute, refuses with
     BD66-REFUSE-DRIVER-ERROR;
  4  an empty pre-pass and no violations return 0.
"""

from __future__ import annotations

import os
import subprocess
import sys

_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

from bytedigger_engine import precommit_lints  # noqa: E402

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"
REFUSE_DRIVER_ERROR = "BD66-REFUSE-DRIVER-ERROR"

#: Reported as `rc=` when the driver could not be executed at all.
EXEC_FAILURE_RC = 127

#: The empty tree, so staged paths can be read on an unborn HEAD too.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def resolve_lint_dir():
    """Canonical driver directory, with `BD66_LINT_DIR` as an override (§2.1a)."""
    override = os.environ.get("BD66_LINT_DIR")
    if override is not None and override.strip():
        return override
    return precommit_lints.DEFAULT_LINT_DIR


def registry_names():
    """The three name lists, read from the MODULE at call time (§1g)."""
    return (
        list(precommit_lints.SPEC_LINTS)
        + list(precommit_lints.TEST_LINTS)
        + list(precommit_lints.TS_TEST_LINTS)
    )


def registry_prepass(lint_dir):
    """Sweep the WHOLE registry; return (driver presence map, violation lines).

    Outcome 1. Both directions of §2.2 are closed here and every violator is
    accumulated — no early exit — so an innocent, properly declared name is
    never named.
    """
    names = registry_names()
    declared = list(getattr(precommit_lints, "DECLARED_ABSENT", []))
    declared_set = set(declared)
    known = set(names)

    present = {}
    violations = []

    for name in names:
        found = os.path.isfile(os.path.join(lint_dir, name + ".py"))
        present[name] = found
        if not found and name not in declared_set:
            violations.append(
                "{token}: lint={name} has no driver at {path} and is not listed "
                "in DECLARED_ABSENT".format(
                    token=REFUSE_MISSING_DRIVER,
                    name=name,
                    path=os.path.join(lint_dir, name + ".py"),
                )
            )

    seen = set()
    for entry in declared:
        if entry in known or entry in seen:
            continue
        seen.add(entry)
        violations.append(
            "{token}: lint={entry} is declared absent but matches no registry "
            "name (stale declaration left by a rename)".format(
                token=REFUSE_MISSING_DRIVER, entry=entry
            )
        )

    return present, violations


def _git(args, cwd):
    return subprocess.run(
        ["git"] + list(args), cwd=cwd, capture_output=True, text=True
    )


def repo_root():
    """Top level of the repository the process is standing in, or None."""
    result = _git(["rev-parse", "--show-toplevel"], os.getcwd())
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return top or None


def staged_paths(root):
    """Staged paths, valid on an unborn HEAD and on a repo with history."""
    head = _git(["rev-parse", "--verify", "--quiet", "HEAD"], root)
    base = "HEAD" if head.returncode == 0 else _EMPTY_TREE
    listed = _git(
        ["diff", "--cached", "--name-only", "--diff-filter=ACMR", base], root
    )
    if listed.returncode != 0:
        return []
    return [line for line in listed.stdout.splitlines() if line.strip()]


def run_plan(commands, present, root):
    """Execute the plan sequentially, in build_lint_commands order (§2.1).

    Outcome 0 (skip), outcome 2 (violation), outcome 3 (driver error).
    """
    violations = []
    for command in commands:
        name = command["lint"]
        if not present.get(name, False):
            # Outcome 0: the driver was absent at pre-pass time, and the
            # pre-pass already established the name is licensed to be absent.
            continue
        sys.stdout.flush()
        try:
            rc = subprocess.call(list(command["argv"]), cwd=root)
        except Exception as exc:  # noqa: BLE001 — never swallow a failed exec
            violations.append(
                "{token}: lint={name} file={file} rc={rc} could not be "
                "executed ({exc})".format(
                    token=REFUSE_DRIVER_ERROR,
                    name=name,
                    file=command["file"],
                    rc=EXEC_FAILURE_RC,
                    exc=exc,
                )
            )
            continue
        if rc == 0:
            continue
        if rc == 1:
            violations.append(
                "{token}: lint={name} file={file} rc={rc}".format(
                    token=REFUSE_VIOLATION, name=name, file=command["file"], rc=rc
                )
            )
        else:
            violations.append(
                "{token}: lint={name} file={file} rc={rc} is neither clean (0) "
                "nor a violation (1)".format(
                    token=REFUSE_DRIVER_ERROR,
                    name=name,
                    file=command["file"],
                    rc=rc,
                )
            )
    return violations


def _report(violations):
    for line in violations:
        print(line)
    sys.stdout.flush()


def main(argv=None) -> int:
    lint_dir = resolve_lint_dir()

    present, prepass_violations = registry_prepass(lint_dir)
    if prepass_violations:
        _report(prepass_violations)
        return 1

    root = repo_root()
    if root is None:
        return 0

    classified = precommit_lints.classify_staged(staged_paths(root))
    if precommit_lints.nothing_to_lint(classified):
        return 0

    commands = precommit_lints.build_lint_commands(
        classified["specs"],
        classified["tests"],
        lint_dir,
        ts_tests=classified["ts_tests"],
    )
    violations = run_plan(commands, present, root)
    if violations:
        _report(violations)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
