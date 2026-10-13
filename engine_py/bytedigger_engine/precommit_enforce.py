"""bd#66 — the pre-commit lint enforcement layer.

A plain library module. The executable entry point is `scripts/precommit_enforce_cli.py`,
which `githooks/pre-commit` execs; the path bootstrap lives there, outside the package.
bd#44 forbids the installed package from touching `sys.path` (AC7) or importing a
sibling by bare name (AC11), and a lot about rules that are declared but not enforced
does not get to weaken a neighbouring rule in order to ship.

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
import shutil
import subprocess
import sys
import tempfile

from bytedigger_engine import green_entry_guard, precommit_lints

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"
REFUSE_DRIVER_ERROR = "BD66-REFUSE-DRIVER-ERROR"
LINT_OFF = "BD66-LINT-OFF"
LINT_OFF_UNKNOWN = "BD66-LINT-OFF-UNKNOWN"
LINT_NOT_FED = "BD66-LINT-NOT-FED"

SPEC_LINTS_OFF_ENV = "BD_SPEC_LINTS_OFF"
CRASH_IS_VIOLATION_ENV = "BD_LINT_CRASH_IS_VIOLATION"

# bd#311: range mode (server-side). `BD_PRECOMMIT_CI=off` (exactly) switches it off.
CI_OFF = "BD66-CI-OFF"
CI_RANGE_ERROR = "BD66-CI-RANGE-ERROR"
CI_OFF_ENV = "BD_PRECOMMIT_CI"

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
    """Every name the registry declares, read from the MODULE at call time (§1g).

    bd#81: the lists are DISCOVERED, not enumerated. A hand-written list of
    lanes was the bug — #80 adds a fourth (`TEXT_LINTS`), a name in it was
    never swept, so it was never required to have a driver, and `run_plan`
    skips exactly the names the pre-pass did not mark present. The lint would
    have read as enabled and been unable to fire: this layer's own subject,
    reproduced one level up inside the layer.
    """
    names = []
    for attr in sorted(dir(precommit_lints)):
        if not attr.endswith("_LINTS"):
            continue
        value = getattr(precommit_lints, attr)
        if isinstance(value, list):
            names.extend(value)
    return names


def driver_path(name, lint_dir):
    """Where `name`'s driver is looked for (bd#81).

    `lint_dir` is the default; a name whose directory the registry declares in
    `LINT_DRIVER_DIRS` is resolved there instead. The default directory is the
    package, and no registry name can legally hold a module there: every name
    is hyphenated, and `ci.yml`'s import smoke imports every `*.py` stem it
    globs out of the package. So a real driver has to live elsewhere, and the
    layer has to be told where rather than assume.

    One resolver, owned by the registry. The branch-independence fallback that
    stood here while #80 was unmerged is gone: `precommit_lints.driver_path` is
    on `main`, and a second implementation of the same rule is how the two
    would drift apart.
    """
    return precommit_lints.driver_path(name, lint_dir)


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
        path = driver_path(name, lint_dir)
        found = os.path.isfile(path)
        present[name] = found
        if not found and name not in declared_set:
            violations.append(
                "{token}: lint={name} has no driver at {path} and is not listed "
                "in DECLARED_ABSENT".format(
                    token=REFUSE_MISSING_DRIVER,
                    name=name,
                    path=path,
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


def _spec_lints_off():
    """Names switched off by `BD_SPEC_LINTS_OFF` (bd#305 switch 1).

    Comma-separated, whitespace stripped, empties dropped, case-sensitive. A
    name outside `SPEC_LINTS` disables nothing and is reported once per run.
    """
    raw = os.environ.get(SPEC_LINTS_OFF_ENV, "")
    off = set()
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name in precommit_lints.SPEC_LINTS:
            off.add(name)
        else:
            print("{token}: name={name}".format(token=LINT_OFF_UNKNOWN, name=name))
    sys.stdout.flush()
    return off


def _has_traceback(stderr_bytes):
    text = stderr_bytes.decode("utf-8", errors="replace")
    return any(
        line.startswith("Traceback (most recent call last):")
        for line in text.splitlines()
    )


def _count_lines(stdout_bytes):
    text = stdout_bytes.decode("utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if line.strip())


def _head_findings(command, root, ref="HEAD"):
    """bd#308: findings of the `ref` version of `command["file"]`.

    Returns (count, error line or None). An unborn HEAD or a path absent from
    `ref` is a baseline of 0. A blob the driver cannot scan is a driver error,
    never a baseline of 0. bd#311: range mode passes the merge-base as `ref`.
    """
    name = command["lint"]
    path = command["file"]
    shown = subprocess.run(
        ["git", "show", ref + ":" + path], cwd=root, capture_output=True
    )
    if shown.returncode != 0:
        return 0, None
    tmp = tempfile.mkdtemp(prefix="bd308-head-")
    try:
        blob = os.path.join(tmp, os.path.basename(path))
        with open(blob, "wb") as fh:
            fh.write(shown.stdout)
        argv = list(command["argv"])
        argv[-1] = blob
        proc = subprocess.run(
            argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as exc:  # noqa: BLE001 — never swallow a failed exec
        return 0, (
            "{token}: lint={name} file={file} rc={rc} baseline run on "
            "{ref}:{file} could not be executed ({exc})".format(
                token=REFUSE_DRIVER_ERROR, name=name, file=path,
                rc=EXEC_FAILURE_RC, exc=exc, ref=ref,
            )
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rc = proc.returncode
    if rc == 0:
        return 0, None
    if rc == 1 and not _has_traceback(proc.stderr or b""):
        return _count_lines(proc.stdout or b""), None
    return 0, (
        "{token}: lint={name} file={file} rc={rc} baseline run on {ref}:{file} "
        "failed (driver error, not a baseline of 0)".format(
            token=REFUSE_DRIVER_ERROR, name=name, file=path, rc=rc, ref=ref
        )
    )


def run_plan(commands, present, root, ref="HEAD"):
    """Execute the plan sequentially, in build_lint_commands order (§2.1).

    Outcome 0 (skip), outcome 2 (violation), outcome 3 (driver error).
    bd#305, per command: absent -> OFF -> NOT-FED -> run; a skip is not a
    refusal. A driver exiting 1 with a traceback on stderr crashed (outcome 3)
    unless `BD_LINT_CRASH_IS_VIOLATION=1`.
    bd#308: a name in `RATCHETED_NAMES` refuses on exit 1 only when the file
    has more findings than its version in HEAD; the per-lint switch sits
    between `BD_SPEC_LINTS_OFF` and NOT-FED. A ratcheted exit 1 with no
    finding lines on stdout is never passed; it refuses as a plain violation.
    bd#311: `ref` is the ratchet baseline revision; range mode passes the
    merge-base.
    """
    violations = []
    off = _spec_lints_off()
    crash_is_violation = os.environ.get(CRASH_IS_VIOLATION_ENV) == "1"
    for command in commands:
        name = command["lint"]
        if not present.get(name, False):
            # Outcome 0: the driver was absent at pre-pass time, and the
            # pre-pass already established the name is licensed to be absent.
            continue
        if name in off:
            print("{token}: lint={name} file={file}".format(
                token=LINT_OFF, name=name, file=command["file"]))
            sys.stdout.flush()
            continue
        off_env = precommit_lints.LINT_OFF_ENVS.get(name)
        if off_env is not None and os.environ.get(off_env) == "off":
            print("{token}: lint={name} file={file}".format(
                token=LINT_OFF, name=name, file=command["file"]))
            sys.stdout.flush()
            continue
        reason = precommit_lints.feed_skip_reason(
            name, os.path.join(root, command["file"])
        )
        if reason is not None:
            print("{token}: lint={name} file={file} reason={reason}".format(
                token=LINT_NOT_FED, name=name, file=command["file"], reason=reason))
            sys.stdout.flush()
            continue
        sys.stdout.flush()
        ratcheted = name in precommit_lints.RATCHETED_NAMES
        try:
            proc = subprocess.run(
                list(command["argv"]), cwd=root, stderr=subprocess.PIPE,
                stdout=subprocess.PIPE if ratcheted else None,
            )
            rc = proc.returncode
            stderr_bytes = proc.stderr or b""
            stdout_bytes = proc.stdout or b""
            # Unchanged, after the driver exits: the traceback still reaches the user.
            if stderr_bytes:
                sys.stderr.flush()
                out = getattr(sys.stderr, "buffer", None)
                if out is not None:
                    out.write(stderr_bytes)
                    out.flush()
                else:
                    sys.stderr.write(stderr_bytes.decode("utf-8", errors="replace"))
                    sys.stderr.flush()
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
        if rc == 1 and not crash_is_violation and _has_traceback(stderr_bytes):
            violations.append(
                "{token}: lint={name} file={file} rc={rc} driver crashed "
                "(uncaught exception, traceback on stderr)".format(
                    token=REFUSE_DRIVER_ERROR, name=name, file=command["file"], rc=rc
                )
            )
        elif rc == 1 and ratcheted and _count_lines(stdout_bytes) > 0:
            now = _count_lines(stdout_bytes)
            base, base_error = _head_findings(command, root, ref)
            if base_error is not None:
                violations.append(base_error)
            elif now > base:
                sys.stdout.write(stdout_bytes.decode("utf-8", errors="replace"))
                sys.stdout.flush()
                violations.append(
                    "{token}: lint={name} file={file} rc={rc} findings={now} "
                    "head={base}".format(
                        token=REFUSE_VIOLATION, name=name, file=command["file"],
                        rc=rc, now=now, base=base,
                    )
                )
        elif rc == 1:
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


def _range_error(reason):
    print("{token}: {reason}".format(token=CI_RANGE_ERROR, reason=reason))
    sys.stdout.flush()
    return 2


def _parse_range_args(args):
    """bd#311: return ((base, tip), None) or (None, reason). Fail closed."""
    value = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg != "--range":
            return None, "unknown argument {!r}".format(arg)
        if value is not None:
            return None, "--range given more than once"
        if i + 1 >= len(args):
            return None, "--range needs a value BASE..HEAD"
        value = args[i + 1]
        i += 2
    if value is None:
        return None, "no --range given"
    sep = "..." if "..." in value else (".." if ".." in value else None)
    if sep is None:
        return None, "range {!r} has no '..'".format(value)
    base, tip = value.split(sep, 1)
    if not base or not tip:
        return None, "range {!r} has an empty side".format(value)
    return (base, tip), None


def _resolve_commit(root, ref):
    result = _git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], root)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _range_plan_inputs(root, base, tip):
    """bd#311: resolve the range; return ((paths, merge_base), None) or (None, reason)."""
    base_sha = _resolve_commit(root, base)
    if base_sha is None:
        return None, "cannot resolve {!r} to a commit".format(base)
    tip_sha = _resolve_commit(root, tip)
    if tip_sha is None:
        return None, "cannot resolve {!r} to a commit".format(tip)
    head_sha = _resolve_commit(root, "HEAD")
    if head_sha is None:
        return None, "cannot resolve HEAD to a commit"
    if tip_sha != head_sha:
        return None, "range tip {!r} is not the checked-out HEAD".format(tip)
    merged = _git(["merge-base", base_sha, head_sha], root)
    merge_base = merged.stdout.strip()
    if merged.returncode != 0 or not merge_base:
        return None, "no merge-base between {!r} and HEAD".format(base)
    listed = _git(
        ["diff", "--name-only", "--diff-filter=ACMR", base_sha + "...HEAD"], root
    )
    if listed.returncode != 0:
        return None, "git diff failed for {!r}...HEAD".format(base)
    paths = [line for line in listed.stdout.splitlines() if line.strip()]
    return (paths, merge_base), None


def main(argv=None) -> int:
    args = list(argv) if argv else []
    range_mode = bool(args)

    # bd#311: range mode only. Before anything else: no git, no pre-pass.
    if range_mode and os.environ.get(CI_OFF_ENV) == "off":
        print(CI_OFF)
        sys.stdout.flush()
        return 0

    parsed = None
    if range_mode:
        parsed, reason = _parse_range_args(args)
        if parsed is None:
            return _range_error(reason)

    lint_dir = resolve_lint_dir()

    present, prepass_violations = registry_prepass(lint_dir)
    if prepass_violations:
        _report(prepass_violations)
        return 1

    root = repo_root()
    if root is None:
        if range_mode:
            return _range_error("not inside a git repository")
        return 0

    baseline_ref = "HEAD"
    if range_mode:
        assert parsed is not None
        inputs, reason = _range_plan_inputs(root, parsed[0], parsed[1])
        if inputs is None:
            return _range_error(reason)
        staged, baseline_ref = inputs
    else:
        staged = staged_paths(root)

        # bd#243: GREEN-entry guard. After the registry pre-pass and repo_root(),
        # BEFORE nothing_to_lint, so a commit staging only binary/unclassified
        # source still reaches it. A commit-time check on staged paths: not run
        # in range mode (bd#311).
        refusals = green_entry_guard.check(root, staged, os.environ)
        if refusals:
            _report(refusals)
            return 1

    classified = precommit_lints.classify_staged(staged)
    if precommit_lints.nothing_to_lint(classified):
        return 0

    commands = precommit_lints.build_lint_commands(
        classified["specs"],
        classified["tests"],
        lint_dir,
        ts_tests=classified["ts_tests"],
        # bd#81: the text lane is planned too. The pre-pass already sweeps the
        # name and requires its driver; planning nothing for it would leave the
        # lint declared, required to exist, and unable to run — the same silence
        # this layer removes, one step further along.
        texts=classified["texts"],
    )
    violations = run_plan(commands, present, root, baseline_ref)
    if violations:
        _report(violations)
        return 1
    return 0
