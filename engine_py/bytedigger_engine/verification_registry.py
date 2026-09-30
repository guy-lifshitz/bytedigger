"""verification_registry.py — the registry of skills that verify a build (bd#115).

A skill verifies iff its SKILL.md frontmatter carries `metadata.verification: true`.
A verifying skill with a non-empty `metadata.verify_command` is a `command` skill the
engine runs; any other verifying skill is an `agent` skill, listed for the `verify`
command and never run by the engine.

Public API:
  FrontmatterError      — a `metadata` block the parser cannot read that mentions `verification`
  Skill                 — frozen dataclass(name, path, kind, verify_command)
  Registry              — frozen dataclass(skills, errors); each error row carries `fatal`
  FAIL_STATUSES         — the skill statuses that count as failures
  parse_frontmatter(text) -> dict | None
  discover(repo_root, extra_dirs=()) -> Registry        (reads files only)
  run_registry(repo_root, *, extra_dirs=(), timeout_sec=300, execute=True) -> dict
  blocking_errors(report) -> list[dict]      (fatal `errors[]` rows)
  failing_skill_names(report) -> list[str]   (skills whose status is in FAIL_STATUSES)
  head_registry_tampered(repo_root, extra_dirs=()) -> list[str]
  verify_main(argv) -> int   (`python3 -m bytedigger_engine.run verify ...`)

Discovery roots: `<repo>/skills`, `<repo>/.claude/skills`, then each `extra_dirs`
entry (repo-relative), one level deep (`<root>/*/SKILL.md`). Every git call runs with
GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE / GIT_OBJECT_DIRECTORY removed from its env.
Commands never run through a shell; the whole process group is killed after every
command (and on timeout).

Stdlib only, on purpose: this is a core module that also backs a standalone CLI
(`run verify`), so it does not use `lib.git_port` / `lib.bounded_spawn` and pulls
in no engine state. It emits no telemetry; its callers do.
Spec: docs/decisions/2026-09-30-bd115-verification-registry-spec-scores.md.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_TIMEOUT_SEC = 300
OUTPUT_TAIL_CHARS = 2000
_GIT_TIMEOUT_SEC = 120
_TRUE_VALUES = frozenset({"true", "True", "TRUE"})
_SCRUBBED_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
_DEFAULT_ROOTS = ("skills", ".claude/skills")  # core-boundary: allow repo-relative skill root inside the host repo, not the user config dir
FAIL_STATUSES = frozenset({"fail", "timeout", "error", "mutated"})

_KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*):(?:[ \t]+(.*))?$")
_VERIFICATION_KEY_RE = re.compile(r"(^|[\s{,])verification\s*:", re.MULTILINE)
_UNSCALAR_START = frozenset(">|[{&*!")


class FrontmatterError(ValueError):
    """A `metadata` value the subset parser cannot read that mentions `verification`."""


@dataclass(frozen=True)
class Skill:
    """One registered verifying skill.

    name            — frontmatter `name`, else the skill directory name.
    path            — repo-relative path of the SKILL.md as found.
    kind            — "command" (engine runs `verify_command`) or "agent".
    verify_command  — the raw command string for `command` skills, else None.
    """

    name: str
    path: str
    kind: str
    verify_command: str | None = None


@dataclass(frozen=True)
class Registry:
    """Result of `discover`: skills sorted by (name, path), errors sorted by path."""

    skills: tuple[Skill, ...]
    errors: tuple[dict[str, Any], ...]


# ─── frontmatter ─────────────────────────────────────────────────────────────


def _unquote(value: str) -> str:
    """Strip one pair of matching quotes around a scalar ('' unescapes in single quotes)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner
    return value


def _strip_comment(value: str) -> str:
    """Cut `value` at the first whitespace-then-`#` outside a quoted string, then trim."""
    quote = ""
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and i > 0 and value[i - 1] in " \t":
            return value[:i].strip()
    return value.strip()


def _is_scalar(value: str) -> bool:
    return bool(value) and value[0] not in _UNSCALAR_START


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _parse_metadata(value: str, children: list[str]) -> dict[str, str] | None:
    """Parse the one-level `metadata:` map; None when unreadable and harmless.

    Raises FrontmatterError when the block is unreadable and mentions `verification`.
    """
    raw = value + "\n" + "\n".join(children)

    def unreadable() -> dict[str, str] | None:
        if _VERIFICATION_KEY_RE.search(raw):
            raise FrontmatterError("unsupported metadata block mentions verification")
        return None

    if value:
        return unreadable()
    content = [c for c in children if c.strip() and not c.lstrip().startswith("#")]
    if not content:
        return {}
    indent = _indent(content[0])
    if indent == 0:
        return unreadable()
    meta: dict[str, str] = {}
    for line in content:
        if _indent(line) != indent:
            return unreadable()
        m = _KEY_RE.match(line.strip())
        if m is None:
            return unreadable()
        item = _strip_comment(m.group(2) or "")
        if not _is_scalar(item):
            return unreadable()
        meta[m.group(1)] = _unquote(item)
    return meta


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Parse the leading `---` block with a deterministic YAML subset.

    Top-level `key: value` scalars and one level of nesting under `metadata:`.
    Other constructs (lists, folded scalars, other maps) are skipped. Returns None
    when there is no frontmatter block; raises FrontmatterError only for an
    unreadable `metadata` value that mentions `verification`.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        return None
    body = lines[1:end]
    result: dict[str, Any] = {}
    i = 0
    while i < len(body):
        line = body[i]
        m = _KEY_RE.match(line) if line and line[0] not in " \t-#" else None
        if m is None:
            i += 1
            continue
        j = i + 1
        while j < len(body) and (not body[j].strip() or body[j][0] in " \t-"):
            j += 1
        key, value = m.group(1), _strip_comment(m.group(2) or "")
        if key == "metadata":
            meta = _parse_metadata(value, body[i + 1:j])
            if meta is not None:
                result["metadata"] = meta
        elif _is_scalar(value):
            result[key] = _unquote(value)
        i = j
    return result


# ─── discovery ───────────────────────────────────────────────────────────────


def _inside(path: Path, repo: Path) -> bool:
    try:
        path.relative_to(repo)
    except ValueError:
        return False
    return True


def _error(path: str, reason: str, *, fatal: bool = True) -> dict[str, Any]:
    """One `errors[]` row; only a symlinked default-root skill dir is non-fatal."""
    return {"path": path, "reason": reason, "fatal": fatal}


def _roots(repo: Path, extra_dirs: Iterable[str]) -> tuple[list[tuple[str, Path]], list[dict[str, Any]]]:
    """Return ([(repo-relative display root, path)], errors), de-duplicated by resolved path.

    Default roots come first, so an `extra_dirs` entry equal to one is dropped and
    the root keeps its default status (membership in `_DEFAULT_ROOTS`).
    """
    errors: list[dict[str, Any]] = []
    candidates: list[tuple[str, Path]] = [(r, repo / r) for r in _DEFAULT_ROOTS]
    for entry in extra_dirs:
        resolved = (repo / str(entry)).resolve()
        if not _inside(resolved, repo):
            errors.append(_error(str(entry), "outside_repo"))
            continue
        rel = resolved.relative_to(repo).as_posix()
        candidates.append(("" if rel == "." else rel, resolved))
    roots: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for rel, path in candidates:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            roots.append((rel, path))
    return roots, errors


_REGISTERED, _UNSUPPORTED, _NOT_REGISTERED = "registered", "unsupported", "not_registered"


def _classify(text: str) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Classify a SKILL.md: (status, frontmatter, metadata).

    status is `_REGISTERED` (metadata.verification is true), `_UNSUPPORTED`
    (unreadable metadata declaring `verification`) or `_NOT_REGISTERED`.
    """
    try:
        fm = parse_frontmatter(text)
    except FrontmatterError:
        return _UNSUPPORTED, {}, {}
    meta = (fm or {}).get("metadata")
    if not isinstance(meta, dict) or meta.get("verification") not in _TRUE_VALUES:
        return _NOT_REGISTERED, fm or {}, {}
    return _REGISTERED, fm or {}, meta


def _load_skill(
    repo: Path, root_rel: str, child: Path, seen_files: set[Path],
) -> tuple[Skill | None, dict[str, Any] | None]:
    """Read one `<root>/<child>/SKILL.md`: (skill or None, error row or None)."""
    md = child / "SKILL.md"
    if not md.is_file():
        return None, None
    rel = posixpath.join(root_rel, child.name, "SKILL.md")
    resolved = md.resolve()
    if not _inside(child.resolve(), repo) or not _inside(resolved, repo):
        non_fatal = root_rel in _DEFAULT_ROOTS and child.is_symlink()
        return None, _error(rel, "outside_repo", fatal=not non_fatal)
    if resolved in seen_files:
        return None, None
    seen_files.add(resolved)
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, _error(rel, "unreadable")
    status, fm, meta = _classify(text)
    if status == _UNSUPPORTED:
        return None, _error(rel, "unsupported_frontmatter")
    if status != _REGISTERED:
        return None, None
    fm_name = fm.get("name")
    name = fm_name if isinstance(fm_name, str) and fm_name else child.name
    command = meta.get("verify_command")
    if isinstance(command, str) and command != "":
        return Skill(name=name, path=rel, kind="command", verify_command=command), None
    return Skill(name=name, path=rel, kind="agent"), None


def discover(repo_root: str | os.PathLike[str], extra_dirs: Sequence[str] = ()) -> Registry:
    """Find every verifying skill under the discovery roots. Reads files only."""
    repo = Path(repo_root).resolve()
    roots, errors = _roots(repo, extra_dirs)
    skills: list[Skill] = []
    seen_files: set[Path] = set()
    for root_rel, root in roots:
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            skill, error = _load_skill(repo, root_rel, child, seen_files)
            if skill is not None:
                skills.append(skill)
            if error is not None:
                errors.append(error)
    skills.sort(key=lambda s: (s.name, s.path))
    errors.sort(key=lambda e: e["path"])
    return Registry(skills=tuple(skills), errors=tuple(errors))


# ─── git (scrubbed env) ──────────────────────────────────────────────────────


def _git(args: Sequence[str], cwd: Path, index_file: str | None = None) -> subprocess.CompletedProcess[bytes] | None:
    """Run git with the redirecting GIT_* vars removed; None when git cannot run."""
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_GIT_ENV}
    if index_file is not None:
        env["GIT_INDEX_FILE"] = index_file
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=_GIT_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _toplevel_index(repo: Path) -> Path | None:
    """The real index path iff `repo` is a git work tree AND its top level, else None."""
    r = _git(["rev-parse", "--show-toplevel", "--git-path", "index"], repo)
    if r is None or r.returncode != 0:
        return None
    lines = r.stdout.decode("utf-8", errors="replace").splitlines()
    if len(lines) != 2 or not lines[0] or Path(lines[0]).resolve() != repo:
        return None
    index = Path(lines[1])
    return index if index.is_absolute() else repo / index


def _tree_snapshot(repo: Path, index: Path) -> str | None:
    """Tree id of the whole work tree (tracked + untracked, .gitignore respected).

    Built through a temp copy of `index` so the real index is never touched.
    """
    with tempfile.TemporaryDirectory(prefix="bd-verify-") as td:
        tmp_index = str(Path(td) / "index")
        try:
            if index.is_file():
                shutil.copyfile(index, tmp_index)
        except OSError:
            return None
        add = _git(["add", "-A"], repo, index_file=tmp_index)
        if add is None or add.returncode != 0:
            return None
        tree = _git(["write-tree"], repo, index_file=tmp_index)
        if tree is None or tree.returncode != 0:
            return None
        return tree.stdout.decode("utf-8", errors="replace").strip() or None


# ─── running ─────────────────────────────────────────────────────────────────


def _row(skill: Skill, status: str) -> dict[str, Any]:
    return {
        "name": skill.name, "path": skill.path, "kind": skill.kind, "status": status,
        "exit_code": None, "duration_ms": 0, "output_tail": "",
    }


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run_command(
    skill: Skill, repo: Path, index: Path, timeout_sec: float, before: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Run one command skill between two tree snapshots; never raises.

    `before` is a snapshot already known to be current (chained from the previous
    command) or None to take one. Returns (row, snapshot reusable as the next
    `before` or None). The row stays `error` unless the command ran and both
    snapshots were taken; `exit_code` None means the command timed out.
    """
    row = _row(skill, "error")
    try:
        argv = shlex.split(skill.verify_command or "")
    except ValueError:
        return row, before
    if not argv:
        return row, before
    if before is None:
        before = _tree_snapshot(repo, index)
    if before is None:
        return row, None
    rc: int | None = None
    t0 = time.monotonic()
    try:
        with tempfile.TemporaryFile() as out:
            try:
                proc = subprocess.Popen(
                    argv, cwd=str(repo), stdin=subprocess.DEVNULL, stdout=out,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
            except (OSError, ValueError):
                return row, before
            try:
                rc = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                _kill_group(proc.pid)
                proc.wait()
            # Always reap the group: a background grandchild must not outlive the
            # check or change the tree after the post-snapshot.
            _kill_group(proc.pid)
            out.seek(0)
            row["output_tail"] = out.read().decode("utf-8", errors="replace")[-OUTPUT_TAIL_CHARS:]
    finally:
        row["duration_ms"] = int((time.monotonic() - t0) * 1000)
    row["exit_code"] = rc
    after = _tree_snapshot(repo, index)
    if after is not None:
        if after != before:
            row["status"] = "mutated"
        elif rc is None:
            row["status"] = "timeout"
        else:
            row["status"] = "pass" if rc == 0 else "fail"
    return row, (after if after == before else None)


def run_registry(
    repo_root: str | os.PathLike[str],
    *,
    extra_dirs: Sequence[str] = (),
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    execute: bool = True,
) -> dict[str, Any]:
    """Discover and (when `execute`) run the verifying skills; return the report.

    The single chokepoint that decides which skills verify a build and runs them.
    Git checks and snapshots happen only when at least one command skill runs.
    """
    repo = Path(repo_root).resolve()
    reg = discover(repo, extra_dirs)
    errors: list[dict[str, Any]] = [dict(e) for e in reg.errors]
    index: Path | None = None
    snapshot: str | None = None
    if execute and any(s.kind == "command" for s in reg.skills):
        index = _toplevel_index(repo)
        # The probe snapshot doubles as the first command's `before`.
        snapshot = _tree_snapshot(repo, index) if index is not None else None
        if snapshot is None:  # not a work tree, not its top level, or no snapshot: run nothing
            errors.append(_error(".", "not_a_git_repo"))
            index = None
    rows: list[dict[str, Any]] = []
    for skill in reg.skills:
        if not execute:
            rows.append(_row(skill, "listed"))
        elif skill.kind == "agent":
            rows.append(_row(skill, "agent"))
        elif index is None:
            rows.append(_row(skill, "error"))
        else:
            row, snapshot = _run_command(skill, repo, index, timeout_sec, snapshot)
            rows.append(row)
    errors.sort(key=lambda e: e["path"])
    counts = Counter(r["status"] for r in rows)
    failed = sum(counts[s] for s in FAIL_STATUSES)
    report: dict[str, Any] = {
        "schema": 1,
        "skills": rows,
        "errors": errors,
        "summary": {"total": len(rows), "pass": counts["pass"], "fail": failed, "agent": counts["agent"]},
    }
    report["ok"] = failed == 0 and not blocking_errors(report)
    return report


def blocking_errors(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The fatal `errors[]` rows of a report (a row without `fatal` counts as fatal)."""
    return [e for e in report.get("errors", []) if e.get("fatal", True)]


def failing_skill_names(report: dict[str, Any]) -> list[str]:
    """Names of the report's skills whose status is in `FAIL_STATUSES`."""
    return [s["name"] for s in report.get("skills", []) if s["status"] in FAIL_STATUSES]


# ─── tamper guard (phase 5) ──────────────────────────────────────────────────


def head_registry_tampered(repo_root: str | os.PathLike[str], extra_dirs: Sequence[str] = ()) -> list[str]:
    """Paths of HEAD-registered verifying SKILL.md files modified or deleted in the work tree.

    The HEAD registry is every `<root>/<dir>/SKILL.md` blob at HEAD (paths relative to
    `repo_root`, which may be a subdirectory of the work tree) that registers a
    verifying skill or carries an unreadable `metadata` block mentioning
    `verification`. Added skills are never reported. Returns [] when there is no HEAD.

    One `git diff --name-only HEAD` over the roots finds the tracked paths that differ
    from HEAD (modified or deleted); only those SKILL.md blobs are read and classified.
    """
    repo = Path(repo_root).resolve()
    roots, _errors = _roots(repo, extra_dirs)
    root_rels = [rel for rel, _path in roots]
    specs = [rel or "." for rel in root_rels]
    changed = _git(["diff", "--name-only", "--relative", "-z", "HEAD", "--", *specs], repo)
    if changed is None or changed.returncode != 0:
        return []
    tampered: set[str] = set()
    for rel_path in changed.stdout.decode("utf-8", errors="replace").split("\0"):
        if not rel_path.endswith("/SKILL.md"):
            continue
        if posixpath.dirname(posixpath.dirname(rel_path)) not in root_rels:
            continue
        blob = _git(["show", f"HEAD:./{rel_path}"], repo)
        if blob is None or blob.returncode != 0:
            continue  # not in HEAD: an added skill
        status, _fm, _meta = _classify(blob.stdout.decode("utf-8", errors="replace"))
        if status != _NOT_REGISTERED:
            tampered.add(rel_path)
    return sorted(tampered)


# ─── CLI ─────────────────────────────────────────────────────────────────────


def verify_main(argv: Sequence[str]) -> int:
    """`verify [--repo DIR] [--extra-dir DIR]... [--list] [--timeout SEC]`.

    Prints the report (JSON, sorted keys) to stdout and writes no file.
    Exit 0 when ok, 1 otherwise, 2 on a usage error.
    """
    parser = argparse.ArgumentParser(prog="bytedigger_engine.run verify")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--extra-dir", action="append", default=[], dest="extra_dirs")
    parser.add_argument("--list", action="store_true", dest="list_only")
    parser.add_argument("--timeout", type=float, default=float(DEFAULT_TIMEOUT_SEC))
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    report = run_registry(
        args.repo, extra_dirs=tuple(args.extra_dirs),
        timeout_sec=args.timeout, execute=not args.list_only,
    )
    sys.stdout.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    sys.stdout.flush()
    return 0 if report["ok"] else 1
