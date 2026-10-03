"""bd#243 - GREEN cannot start without an approving gate verdict on the current spec.

`check(root, staged, env)` is called once by `precommit_enforce.main` (after
`repo_root()`, before `nothing_to_lint`). It returns refusal lines; an empty list
means the commit is allowed. No model call, stdlib plus `verdict_verify` (anchor
regexes) and `precommit_lints` (test-file predicates). Every git read goes
through a subprocess; nothing here walks the tree.

Spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Mapping

from bytedigger_engine import precommit_lints, verdict_verify

MISSING = "E_GREEN_GATE_MISSING"
REJECTED = "E_GREEN_GATE_REJECTED"
STALE = "E_GREEN_GATE_STALE"
UNREADABLE = "E_GREEN_GATE_UNREADABLE"
BYPASS_NO_REASON = "E_GREEN_GATE_BYPASS_NO_REASON"

KILL_VAR = "HAL_GREEN_GATE_GUARD"
REASON_VAR = "HAL_GREEN_GATE_BYPASS_REASON"

_SPEC_DIR = "docs/decisions/"
_NON_SOURCE_DIRS = frozenset({"docs", "tests", "__tests__"})
_EXCLUDED_SPEC_PATTERNS = (
    "*-gate-r[0-9]*.md",
    "*-gate-verdict-r[0-9]*.md",
    "*-escalation.md",
    "*-acceptance.md",
    "*-inventory.md",
    "*-close-gate.md",
    "*-post-mortem.md",
)
_GATE_DOC_RE = re.compile(r"^docs/decisions/(?P<prefix>.+)-gate-r(?P<n>[0-9]+)\.md$")
_VERDICT_RE = re.compile(r"^VERDICT:\s*(APPROVED?|REJECT(ED)?)\s*$", re.IGNORECASE)
_ESCALATION_RE = re.compile(r"^ESCALATION:[ \t]*(\S.*)$", re.MULTILINE)
_EXEMPT_RE = re.compile(r"^Gate-exempt:[ \t]*(\S.*)$")
_FETCH_HINT = " (base=origin/main; run git fetch if stale)"


class _GuardError(Exception):
    """A condition that must refuse with E_GREEN_GATE_UNREADABLE."""


def _git(root: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True)


def _git_listing(root: str, args: list[str]) -> bytes:
    result = _git(root, args)
    if result.returncode != 0:
        raise _GuardError(
            "git %s failed rc=%d" % (" ".join(args[:2]), result.returncode)
        )
    return result.stdout


def _is_source(path: str) -> bool:
    if precommit_lints.is_test_file(path) or precommit_lints.is_ts_test_file(path):
        return False
    if path.lower().endswith(".md"):
        return False
    parts = path.split("/")
    return not any(part in _NON_SOURCE_DIRS for part in parts[:-1])


def _resolve_base(root: str) -> str | None:
    for ref in ("origin/main", "main"):
        probe = _git(root, ["rev-parse", "--verify", "--quiet", ref])
        if probe.returncode == 0:
            return ref
    return None


def _line(code: str, spec: str, gate: str | None, detail: str) -> str:
    return "%s: spec=%s gate=%s detail=%s" % (code, spec, gate or "-", detail)


def _log_path(root: str) -> str:
    common = _git_listing(root, ["rev-parse", "--git-common-dir"]).decode(
        "utf-8", "replace"
    ).strip()
    if not os.path.isabs(common):
        common = os.path.join(root, common)
    return os.path.join(common, "bytedigger", "bypass.log")


def _append_bypass(root: str, kind: str, spec: str | None, reason: str) -> None:
    """Append one JSON line; any failure raises _GuardError (a bypass must be recorded)."""
    try:
        path = _log_path(root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "spec": spec,
            "reason": reason,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except (OSError, _GuardError) as exc:
        raise _GuardError("bypass log not writable: %s" % exc) from exc


def _read_bytes(root: str, rel: str) -> bytes:
    try:
        with open(os.path.join(root, rel), "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise _GuardError("cannot read %s: %s" % (rel, exc)) from exc


def _is_excluded_name(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in _EXCLUDED_SPEC_PATTERNS)


def _added_specs(root: str, base_ref: str) -> list[str]:
    mb = _git(root, ["merge-base", "HEAD", base_ref])
    if mb.returncode != 0:
        raise _GuardError("git merge-base failed rc=%d" % mb.returncode)
    merge_base = mb.stdout.decode("utf-8", "replace").strip()
    raw = _git_listing(
        root,
        [
            "diff", "--cached", "--name-status", "-z",
            "--diff-filter=A", "--no-renames", merge_base,
        ],
    )
    tokens = [t for t in raw.decode("utf-8", "replace").split("\0") if t]
    specs = []
    for status, path in zip(tokens[0::2], tokens[1::2]):
        if status != "A":
            continue
        if not (path.startswith(_SPEC_DIR) and path.endswith(".md")):
            continue
        name = path[len(_SPEC_DIR):]
        if "/" in name or _is_excluded_name(name):
            continue
        specs.append(path)
    return sorted(specs)


def _exemption_reason(root: str, spec: str) -> str | None:
    shown = _git(root, ["show", ":" + spec])
    if shown.returncode != 0:
        raise _GuardError("cannot read index content of %s" % spec)
    text = shown.stdout.decode("utf-8", "replace")
    for raw in text.splitlines()[:20]:
        match = _EXEMPT_RE.match(raw.rstrip("\r"))
        if match:
            return match.group(1).strip()
    return None


def _escalation_reason(root: str, stem: str) -> str | None:
    rel = "%s%s-escalation.md" % (_SPEC_DIR, stem)
    path = os.path.join(root, rel)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _GuardError("cannot read %s: %s" % (rel, exc)) from exc
    match = _ESCALATION_RE.search(data.decode("utf-8", "replace"))
    if not match:
        return None
    return match.group(1).strip()


def _gate_docs(root: str) -> list[str]:
    raw = _git_listing(root, ["ls-files", "-z", "--", _SPEC_DIR])
    return [p for p in raw.decode("utf-8", "replace").split("\0") if p]


def _newest_gate(stem: str, gate_paths: list[str]) -> str | None:
    segments = stem.split("-")
    allowed = {stem}
    for k in range(4, len(segments)):
        allowed.add("-".join(segments[:k]))
    best: tuple[int, int] | None = None
    best_path: str | None = None
    for path in gate_paths:
        match = _GATE_DOC_RE.match(path)
        if not match or match.group("prefix") not in allowed:
            continue
        n = int(match.group("n"))
        if n < 1:
            continue
        key = (n, len(match.group("prefix")))
        if best is None or key > best:
            best, best_path = key, path
    return best_path


def _verdict(text: str) -> str | None:
    found = None
    for raw in text.splitlines():
        match = _VERDICT_RE.match(raw)
        if match:
            found = match.group(1).upper()
    return found


def _anchor_hash(gate_text: str, spec: str) -> str | None:
    block = verdict_verify.ANCHOR_RE.search(gate_text)
    if not block:
        return None
    for line in verdict_verify.ANCHOR_LINE_RE.finditer(block.group(1)):
        kind, relpath, sha = line.groups()
        if kind == "spec" and relpath == spec:
            return sha
    return None


def _decide(root: str, spec: str, gate_paths: list[str], hint: str) -> str | None:
    """Per-spec decision (spec 2.2). Returns a refusal line or None (passes)."""
    stem = spec[len(_SPEC_DIR):-len(".md")]
    reason = _escalation_reason(root, stem)
    if reason is not None:
        _append_bypass(root, "escalation", spec, reason)
        return None
    gate = _newest_gate(stem, gate_paths)
    if gate is None:
        return _line(MISSING, spec, None, "no gate doc found" + hint)
    gate_text = _read_bytes(root, gate).decode("utf-8", "replace")
    verdict = _verdict(gate_text)
    if verdict is None:
        return _line(UNREADABLE, spec, gate, "no VERDICT line in the newest gate doc")
    if verdict.startswith("REJECT"):
        return _line(REJECTED, spec, gate, "newest gate verdict is a rejection" + hint)
    anchored = _anchor_hash(gate_text, spec)
    current = hashlib.sha256(_read_bytes(root, spec)).hexdigest()
    if anchored is None or anchored != current:
        return _line(
            STALE, spec, gate,
            "verdict anchor missing or not matching the current spec" + hint,
        )
    return None


def _check_lot(root: str) -> list[str]:
    base = _resolve_base(root)
    if base is None:
        return []
    hint = _FETCH_HINT if base == "origin/main" else ""
    refusals: list[str] = []
    to_check = []
    for spec in _added_specs(root, base):
        reason = _exemption_reason(root, spec)
        if reason is None:
            to_check.append(spec)
            continue
        try:
            _append_bypass(root, "gate_exempt", spec, reason)
        except _GuardError as exc:
            refusals.append(_line(UNREADABLE, spec, None, str(exc)))
    if not to_check:
        return refusals
    gate_paths = _gate_docs(root)
    for spec in to_check:
        try:
            line = _decide(root, spec, gate_paths, hint)
        except _GuardError as exc:
            line = _line(UNREADABLE, spec, None, str(exc))
        if line is not None:
            refusals.append(line)
    return refusals


def check(root: str, staged: list[str], env: Mapping[str, str]) -> list[str]:
    """Return refusal lines for a commit staging `staged`; empty list = allowed."""
    if env.get(KILL_VAR) == "0":
        reason = (env.get(REASON_VAR) or "").strip()
        if not reason:
            return [
                _line(
                    BYPASS_NO_REASON, "-", None,
                    "%s=0 needs a non-blank %s" % (KILL_VAR, REASON_VAR),
                )
            ]
        try:
            _append_bypass(root, "kill_switch", None, reason)
        except _GuardError as exc:
            return [_line(UNREADABLE, "-", None, str(exc))]
        return []

    if not any(_is_source(p) for p in staged):
        return []

    try:
        return _check_lot(root)
    except Exception as exc:  # noqa: BLE001 - fail closed, never a silent allow
        return [_line(UNREADABLE, "-", None, "%s: %s" % (type(exc).__name__, exc))]
