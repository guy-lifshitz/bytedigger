#!/usr/bin/env python3
"""GH1318 — measurement-integrity guard for the Δ0 merge gate.

A suite result only means something if the tree that produced it held still for
the whole run. Issue #1318 measured the opposite: three runs of one tree
disagreed, and the skip half of the disagreement came from a writer OUTSIDE the
suite — the pre-commit hook's background `graphify` refresh, fired by commits
made while the suite was running. A test whose skip predicate reads a live tree
path then answers differently depending on whether anyone committed mid-run.

This guard makes that checkable rather than assumed. Two declared inputs —
git identity plus a set of declared artifacts — sampled at two points in time
(a before/after pair, not a continuous watch; see the limits table in the
frozen spec):

    suite_measurement_guard.py snapshot --root . --artifact <path> --out /tmp/guard-state/state.json
    <run the suite>
    suite_measurement_guard.py verify --root . --artifact <path> --state /tmp/guard-state/state.json --measurement-id <id-from-snapshot>

`snapshot` prints the plaintext `measurement_id` on stdout ONLY — the state
file it writes carries just that id's sha256 digest. `verify` REQUIRES
`--measurement-id` (the id `snapshot` printed) and refuses (rc2 ERROR) if it
is missing, wrong, or the state file has no digest at all: a stale or foreign
state file must never verify.

`verify` exits 0 and prints `{"verdict": "SOUND", ...}` when the declared
inputs are byte-identical, or exits 1 with `{"verdict": "UNSOUND", "changed":
[...]}` naming exactly what moved. Any refusal (bad input, degraded git,
mismatched id, a crash) is rc2 `{"verdict": "ERROR", "reason": ...}` on
exactly one JSON line — never rc1, so a crash can never masquerade as a
genuine UNSOUND verdict.

UNSOUND is NOT a failure of the code under test and NOT something to retry
around: it says this measurement proves nothing and must be taken again on a
still tree. It hides no red — that is the whole point (#1318 forbids retries
and mutes precisely because one genuine red showed up in 1 run of 3).

Content is hashed, never trusted to mtime or size: a rebuild can produce the
same length, and mtime granularity varies by filesystem. `--out`/`--state`
must resolve OUTSIDE `--root`, and `--artifact` must resolve strictly below
it — the guard's own bookkeeping must never become part of what it measures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

# Same path-setup shape as scripts/gen_flag_catalog.py: this file is invoked by
# absolute path, so engine_py itself is not on sys.path and `lib.git_port` would
# not resolve without this.
_ENGINE_ROOT = Path(__file__).resolve().parent.parent

from bytedigger_engine.lib.git_port import git_read  # noqa: E402 — needs the sys.path line above

ABSENT = "<absent>"
GIT_DEGRADED = "<git-degraded>"


class GuardError(Exception):
    """A refusal with a machine-readable reason token (M13's closed list)."""

    def __init__(self, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.reason: str = reason
        self.details: dict[str, Any] = details


def _git(root: str, *args: str) -> str:
    """Best-effort git read through the engine_py port seam (git-port-bypass-lint:
    raw `subprocess.run(["git", ...])` is not allowed here). Any degradation
    (unavailable, timeout, non-zero rc, OSError, not a repo) collapses to one
    sentinel so callers can detect it by equality rather than by parsing text."""
    try:
        result = git_read(list(args), cwd=root)
    except (OSError, subprocess.SubprocessError):
        return GIT_DEGRADED
    if result.timed_out:
        return GIT_DEGRADED
    if result.returncode != 0:
        return GIT_DEGRADED
    return result.stdout.strip()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _is_irregular(path: Path) -> bool:
    """True for a FIFO/socket/device — never opened, only stat'd (M20)."""
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return (
        stat.S_ISFIFO(mode)
        or stat.S_ISSOCK(mode)
        or stat.S_ISCHR(mode)
        or stat.S_ISBLK(mode)
    )


def _entry_marker(child: Path, base: Path) -> str:
    """Identity of one entry inside a declared artifact directory (M14/M20).

    Regular files are content-hashed. A symlink to a regular file is hashed
    by its target's content PLUS `os.readlink`, so a retarget to identical
    bytes is still visible. Everything else (empty dirs, dangling symlinks,
    FIFOs/sockets/devices) gets a type marker and is never opened."""
    rel = child.relative_to(base)
    if child.is_symlink():
        target = os.readlink(child)
        if child.is_file():
            return f"{rel}=symlink-file:{_hash_file(child)}:{target}"
        return f"{rel}=symlink:{target}"
    try:
        mode = child.lstat().st_mode
    except OSError:
        return f"{rel}=gone"
    if stat.S_ISFIFO(mode):
        return f"{rel}=fifo"
    if stat.S_ISSOCK(mode):
        return f"{rel}=socket"
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        return f"{rel}=device"
    if child.is_dir():
        return f"{rel}=dir"
    if child.is_file():
        return f"{rel}=file:{_hash_file(child)}"
    return f"{rel}=other"


def _describe(path: Path) -> str:
    if not path.exists():
        return ABSENT
    if path.is_dir():
        # Directory identity = sorted markers of everything under it — files,
        # empty subdirectories, symlinks and irregular entries all included —
        # so any of those appearing/changing inside a declared artifact dir
        # is caught too.
        entries = sorted(_entry_marker(child, path) for child in path.rglob("*"))
        return f"dir:{hashlib.sha256(chr(0).join(entries).encode()).hexdigest()}"
    return _hash_file(path)


def snapshot(root: str, artifacts: list[str]) -> dict[str, Any]:
    """Record the declared inputs of a measurement: git identity + artifacts.

    `root` is resolved to an absolute path here, BEFORE any git call (M1) —
    the recorded `root` is that resolved path, never the literal argument."""
    root_path = Path(root).resolve()
    root_str = str(root_path)
    return {
        "root": root_str,
        "HEAD": _git(root_str, "rev-parse", "HEAD"),
        "status": _git(root_str, "status", "--porcelain"),
        "artifacts": {name: _describe(root_path / name) for name in artifacts},
    }


def _channels_live(snap: dict[str, Any]) -> list[str]:
    """Channels with signal ACTUALLY collected in `snap` (M5) — never built
    from argv, so a declared-but-absent artifact never counts as live."""
    live: list[str] = []
    if snap.get("HEAD") != GIT_DEGRADED:
        live.append("HEAD")
    if snap.get("status") != GIT_DEGRADED:
        live.append("status")
    artifacts = snap.get("artifacts", {})
    if any(value != ABSENT for value in artifacts.values()):
        live.append("artifacts")
    return live


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Name every declared input that differs between the two snapshots.

    Order (M16): root identity, then git degradation (either side, either
    direction), then a declared artifact absent at both ends — each of these
    is a refusal to render ANY verdict, before the actual diff is computed."""
    before_root = before.get("root")
    after_root = after.get("root")
    if before_root != after_root:
        return {
            "verdict": "ERROR",
            "reason": "root-mismatch",
            "before_root": before_root,
            "after_root": after_root,
        }

    degraded = GIT_DEGRADED in (
        before.get("HEAD"), before.get("status"),
        after.get("HEAD"), after.get("status"),
    )
    if degraded:
        return {
            "verdict": "ERROR",
            "reason": "git-degraded",
            "channels_live": _channels_live(after),
        }

    before_artifacts = before.get("artifacts", {})
    after_artifacts = after.get("artifacts", {})
    all_names = sorted(set(before_artifacts) | set(after_artifacts))
    for name in all_names:
        if (
            before_artifacts.get(name, ABSENT) == ABSENT
            and after_artifacts.get(name, ABSENT) == ABSENT
        ):
            return {
                "verdict": "ERROR",
                "reason": "artifact-absent-both-ends",
                "artifact": name,
                "channels_live": _channels_live(after),
            }

    changed: list[str] = []
    for key in ("HEAD", "status"):
        if before.get(key) != after.get(key):
            changed.append(f"{key}: {before.get(key)!r} -> {after.get(key)!r}")

    for name in all_names:
        was = before_artifacts.get(name, ABSENT)
        now = after_artifacts.get(name, ABSENT)
        if was == now:
            continue
        if was == ABSENT:
            changed.append(f"{name}: appeared during the run ({now})")
        elif now == ABSENT:
            changed.append(f"{name}: disappeared during the run (was {was})")
        else:
            changed.append(f"{name}: content changed ({was} -> {now})")

    return {
        "verdict": "SOUND" if not changed else "UNSOUND",
        "changed": changed,
        "root": after_root,
        "channels_live": _channels_live(after),
    }


def _validate_artifact(root_path: Path, name: str) -> None:
    """M10/M20/M45: non-empty, resolves strictly BELOW root_path (by path
    components on the resolved path, never a string prefix), and is not an
    irregular entry (FIFO/socket/device, and not reachable only via a
    symlink that escapes root_path)."""
    if not name:
        raise GuardError("invalid-artifact", artifact=name)
    candidate = root_path / name
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(root_path)
    except ValueError:
        raise GuardError("invalid-artifact", artifact=name) from None
    if str(relative) == ".":
        raise GuardError("invalid-artifact", artifact=name)
    if _is_irregular(resolved):
        raise GuardError("invalid-artifact", artifact=name)


def _validate_out_or_state_type(path_str: str) -> None:
    """M20 (extended): --out/--state must not be a FIFO/socket/device —
    `write_text`/`open()` against one blocks forever, same hang risk as a
    FIFO artifact."""
    if _is_irregular(Path(path_str).resolve()):
        raise GuardError("invalid-artifact", path=path_str)


def _check_outside_root(root_path: Path, candidate_str: str, reason: str) -> None:
    """M8/M15: candidate must resolve OUTSIDE root_path, compared by path
    components on resolved paths — never `str.startswith` (a sibling whose
    name extends root's name must not be misread as in-tree, AC26)."""
    candidate = Path(candidate_str).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        return  # outside the tree: the good case
    raise GuardError(reason, root=str(root_path), path=str(candidate))


def _atomic_write(path: Path, content: str) -> None:
    """M17: write via a temp file beside the target, then `os.replace` —
    never a partial/torn read on a concurrent snapshot to the same path."""
    tmp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    tmp_path.write_text(content)
    os.replace(tmp_path, path)


def _emit_error(reason: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"verdict": "ERROR", "reason": reason}
    payload.update(extra)
    print(json.dumps(payload, sort_keys=True))
    return 2


def _run_snapshot(args: argparse.Namespace, root_path: Path) -> int:
    for name in args.artifact:
        _validate_artifact(root_path, name)

    if not args.out:
        raise GuardError("crash", error="snapshot needs --out")

    _validate_out_or_state_type(args.out)
    _check_outside_root(root_path, args.out, "out-inside-root")

    state = snapshot(str(root_path), args.artifact)

    # M9: identity of this measurement, provable only by presentation. The
    # plaintext id is printed on stdout ONLY; the state file carries just its
    # sha256 digest — a stale state file cannot produce its own id.
    measurement_id = str(uuid.uuid4())
    state["measurement_digest"] = hashlib.sha256(measurement_id.encode()).hexdigest()

    out_path = Path(args.out)
    _atomic_write(out_path, json.dumps(state, indent=2, sort_keys=True))

    print(json.dumps(
        {
            "verdict": "SNAPSHOT",
            "state": str(out_path),
            "HEAD": state["HEAD"],
            "measurement_id": measurement_id,
        },
        sort_keys=True,
    ))
    return 0


def _run_verify(args: argparse.Namespace, root_path: Path) -> int:
    for name in args.artifact:
        _validate_artifact(root_path, name)

    if args.state:
        _validate_out_or_state_type(args.state)
        _check_outside_root(root_path, args.state, "state-inside-root")

    if not args.state:
        raise GuardError("state-unreadable", error="verify needs --state")

    state_path = Path(args.state)
    if not state_path.is_file():
        raise GuardError("state-unreadable", path=str(state_path))

    try:
        before = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(
            "state-unreadable", path=str(state_path), error=str(exc),
        ) from None

    if args.measurement_id is None:
        raise GuardError("measurement-id-missing")

    # M9: absence of the digest in the file is ALSO a mismatch, never a pass
    # — `if "measurement_digest" in before and ...` (fail-open) is forbidden.
    expected_digest = before.get("measurement_digest")
    supplied_digest = hashlib.sha256(args.measurement_id.encode()).hexdigest()
    if expected_digest is None or supplied_digest != expected_digest:
        raise GuardError(
            "measurement-id-mismatch",
            supplied_measurement_id=args.measurement_id,
            expected_digest=expected_digest,
        )

    after = snapshot(str(root_path), args.artifact)
    report = compare(before, after)

    # #1330 (dispatcher decision on gate M1): zero declared artifacts is
    # "nothing to check", and that is a REFUSAL, not a pass. The asymmetry was
    # inverted: declaring an artifact that never appeared refused
    # (artifact-absent-both-ends), while declaring none at all sailed through as
    # SOUND — the weaker input got the kinder verdict. It matters because the
    # subject of #1318 (graphify-out/graph.json) is gitignored, so the HEAD and
    # status channels physically cannot see it; SOUND with no --artifact means
    # "I checked something else". `verdict` is the only field the shell gate
    # reads — `channels_live` next to it has no reader — so the refusal has to
    # land on `verdict`.
    #
    # Ordered AFTER compare(): a genuine inability (root-mismatch, git-degraded,
    # artifact-absent-both-ends) keeps its own, more specific reason, and the
    # `changed` list is carried through so nothing observed is thrown away.
    #
    # Scope of the refusal, stated as an invariant: with zero declared
    # artifacts, **rc0 is unreachable**. An UNSOUND that the porcelain channel
    # caught on its own is a true finding and keeps saying UNSOUND (rc1) —
    # downgrading it to ERROR would throw away a real observation to make a
    # point. What can never happen is a PASS earned by declaring nothing.
    if report.get("verdict") == "SOUND" and not args.artifact:
        report = {
            "verdict": "ERROR",
            "reason": "no-artifact-channel",
            "changed": report.get("changed", []),
            "root": report.get("root"),
            "channels_live": report.get("channels_live", []),
        }
    # One JSON object on ONE line: the repo's shell gates read the last stdout
    # line and parse it (parseJsonLastLine convention).
    print(json.dumps(report, sort_keys=True))
    if report.get("verdict") == "ERROR":
        return 2
    return 0 if report.get("verdict") == "SOUND" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("mode", choices=("snapshot", "verify"))
    parser.add_argument("--root", required=True, help="tree the suite is measured in")
    parser.add_argument(
        "--artifact", action="append", default=[],
        help="repo-relative path whose content must not move mid-run "
             "(repeatable; must resolve strictly below --root)",
    )
    parser.add_argument(
        "--out", help="snapshot mode: where to write the state file (must resolve OUTSIDE --root)",
    )
    parser.add_argument(
        "--state", help="verify mode: the state file to compare against (must resolve OUTSIDE --root)",
    )
    parser.add_argument(
        "--measurement-id",
        default=None,
        help="verify mode: the plaintext id printed by the matching snapshot (required; "
             "not enforced via argparse required= so a missing flag stays on the one-JSON-"
             "line error contract instead of going to stderr as a bare usage message)",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        root_path = Path(args.root).resolve()  # M1: before any git call
        if args.mode == "snapshot":
            return _run_snapshot(args, root_path)
        return _run_verify(args, root_path)
    except GuardError as exc:
        return _emit_error(exc.reason, **exc.details)
    except Exception as exc:  # noqa: BLE001 — boundary handler, M2: no crash may look like rc1
        return _emit_error("crash", error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
