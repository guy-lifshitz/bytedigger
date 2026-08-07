#!/usr/bin/env python3
"""bd#66 — install the versioned git hooks of this repository.

Installation is `core.hooksPath=githooks` rather than a write into `.git/hooks`:
the directory is versioned and reviewable, and the installation is idempotent
and checkable with a single command.

The current state is read STRICTLY with `git config --get --local
core.hooksPath` (spec §2.1). A developer's global `~/.gitconfig` must neither
pass for an installation of THIS repository (a false `--check` rc=0) nor trigger
the foreign-value refusal over a value the repository does not own.

Exit codes: 0 installed / already installed / check satisfied · 1 not installed,
a foreign local value, or `--root` that is not a repository · 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

HOOKS_PATH_KEY = "core.hooksPath"
HOOKS_PATH_VALUE = "githooks"


def _git(args, cwd):
    return subprocess.run(
        ["git"] + list(args), cwd=cwd, capture_output=True, text=True
    )


def local_hooks_path(root):
    """The value the REPOSITORY owns, or None when it owns none."""
    result = _git(["config", "--get", "--local", HOOKS_PATH_KEY], root)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def is_repository(root) -> bool:
    return _git(["rev-parse", "--show-toplevel"], root).returncode == 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_git_hooks.py",
        description="Install or check core.hooksPath=githooks for this repository.",
    )
    parser.add_argument(
        "--install", action="store_true", help="write the local core.hooksPath"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the hooks are installed (the default)",
    )
    parser.add_argument(
        "--root", default=".", help="repository root to operate on (default: cwd)"
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.install and args.check:
        print(
            "usage: --install and --check are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    root = os.path.abspath(args.root)
    if not os.path.isdir(root) or not is_repository(root):
        print(
            "{root} is not a git repository; nothing was written".format(root=root),
            file=sys.stderr,
        )
        return 1

    current = local_hooks_path(root)

    # No flags behaves exactly as --check: a bare invocation never installs.
    if not args.install:
        return 0 if current == HOOKS_PATH_VALUE else 1

    if current is None:
        written = _git(
            ["config", "--local", HOOKS_PATH_KEY, HOOKS_PATH_VALUE], root
        )
        if written.returncode != 0:
            print(
                "failed to write {key}: {err}".format(
                    key=HOOKS_PATH_KEY, err=written.stderr.strip()
                ),
                file=sys.stderr,
            )
            return 1
        print(
            "installed {key}={value}".format(
                key=HOOKS_PATH_KEY, value=HOOKS_PATH_VALUE
            )
        )
        return 0

    if current == HOOKS_PATH_VALUE:
        print(
            "{key} is already {value}".format(
                key=HOOKS_PATH_KEY, value=HOOKS_PATH_VALUE
            )
        )
        return 0

    print(
        "refusing to overwrite the local {key}: it is {current!r}, not "
        "{value!r}. Resolve it by hand, then re-run --install.".format(
            key=HOOKS_PATH_KEY, current=current, value=HOOKS_PATH_VALUE
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
