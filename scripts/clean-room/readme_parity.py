#!/usr/bin/env python3
"""README-parity guard for the clean-room job (bd#100, AC5).

The clean-room job is only worth anything if it runs what the README actually
tells a newcomer to run. Without a guard, README and job drift apart silently
and the job keeps testing yesterday's instructions while staying green.

This compares the commands in README's `## Quickstart` section against a
checked-in manifest. Any edit to those commands fails the job until the
manifest -- and therefore, deliberately, the clean-room script -- is updated.

Manifest format, one record per line:

    <coverage>\t<command>

<coverage> is one of:
    quickstart      run by in-container.sh quickstart mode
    suite           run by in-container.sh suite mode
    skip:<reason>   deliberately not run; reason is mandatory and is printed

Exit 0 on parity, 1 on drift. Stdlib only, no network, py3.9-compatible.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MANIFEST_REL = "scripts/clean-room/readme-quickstart.manifest"
SCRIPT_REL = "scripts/clean-room/in-container.sh"


def readme_quickstart_commands(readme: str) -> list[str]:
    """Command lines from every ```bash fence under `## Quickstart`."""
    lines = readme.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "## Quickstart":
            start = i + 1
            break
    if start is None:
        raise SystemExit("FAIL: README has no '## Quickstart' section")

    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    cmds: list[str] = []
    in_fence = False
    for line in lines[start:end]:
        if line.startswith("```"):
            # only shell fences carry commands; ```mermaid etc. are prose
            in_fence = line.startswith("```bash") or line.startswith("```sh")
            continue
        if not in_fence:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # drop a trailing inline comment ("pip install -U pip   # why")
        stripped = re.sub(r"\s{2,}#.*$", "", stripped).strip()
        cmds.append(stripped)
    return cmds


def read_manifest(path: Path) -> list[tuple[str, str]]:
    records = []
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" not in raw:
            raise SystemExit(f"FAIL: manifest line lacks a TAB separator: {raw!r}")
        coverage, command = raw.split("\t", 1)
        records.append((coverage.strip(), command.strip()))
    return records


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    readme = (root / "README.md").read_text()
    manifest = read_manifest(root / MANIFEST_REL)
    script = (root / SCRIPT_REL).read_text()

    actual = readme_quickstart_commands(readme)
    expected = [cmd for _, cmd in manifest]

    if actual != expected:
        print("FAIL: README Quickstart drifted from the clean-room manifest.")
        print(f"      Update {MANIFEST_REL} *and* {SCRIPT_REL} together.\n")
        for i in range(max(len(actual), len(expected))):
            a = actual[i] if i < len(actual) else "<missing>"
            e = expected[i] if i < len(expected) else "<missing>"
            if a != e:
                print(f"  line {i + 1}:\n    README:   {a}\n    manifest: {e}")
        return 1

    # Coverage sanity: a command claimed to be run must actually appear in the
    # clean-room script, so bumping the manifest alone cannot silence the guard.
    problems = []
    for coverage, command in manifest:
        if coverage in ("quickstart", "suite"):
            head = " ".join(command.split()[:3])
            if head not in script:
                problems.append(f"  claimed {coverage!r} but not found in {SCRIPT_REL}: {command}")
        elif coverage.startswith("skip:"):
            if not coverage[len("skip:"):].strip():
                problems.append(f"  skip without a reason: {command}")
        else:
            problems.append(f"  unknown coverage {coverage!r}: {command}")

    if problems:
        print("FAIL: clean-room coverage claims do not hold.")
        print("\n".join(problems))
        return 1

    skipped = [(c, cmd) for c, cmd in manifest if c.startswith("skip:")]
    print(f"ok: README Quickstart parity — {len(manifest)} commands, {len(skipped)} deliberately skipped")
    for coverage, command in skipped:
        print(f"   skipped: {command}  [{coverage[len('skip:'):].strip()}]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
