"""audit_gate.py — engine_py-prod change requires co-staged APPROVED Opus-audit doc.

Part of 16ED0B52 — universal pre-commit teeth for the engine_py→Opus rule.
(SYSTEMATIC chokepoint; enforcement layer: deterministic commit-msg hook)

Exposes:
    is_build_tests_doc(path)         -> bool
    doc_has_approval(text)           -> bool
    scan_audit_violation(...)        -> AuditDecision
    _git_staged_modified_paths(...)  -> list[str]
    install_commit_msg_hook(...)     -> None
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field

from bytedigger_engine import config_provider
from bytedigger_engine.lib.git_port import git_read
from bytedigger_engine.tier_gate import is_engine_py_prod

# ─── constants ────────────────────────────────────────────────────────────────

BUILD_TESTS_SUFFIX = "_build_tests.md"
APPROVAL_TOKEN = "VERDICT: APPROVED"
KILL_SWITCH_ENV = "HAL_ENGINE_PY_AUDIT_GATE"

# Pragma regex: reason group (1) must be non-empty (\S.*?) — bare skip does NOT match.
_PRAGMA_RE = re.compile(
    r"engine-py-audit:\s*skip\s+(\S.*?)\s*$",
    re.MULTILINE,
)

# Sentinel delimiters for idempotent hook block.
_SENTINEL_OPEN = "# >>> hal engine-py-audit-gate >>>"
_SENTINEL_CLOSE = "# <<< hal engine-py-audit-gate <<<"


# ─── dataclass ────────────────────────────────────────────────────────────────


@dataclass
class AuditDecision:
    blocked: bool
    changed: list[str]
    reason: str
    escape: str | None  # None | "kill_switch" | "pragma" | "approved_doc"


# ─── helpers ──────────────────────────────────────────────────────────────────


def is_build_tests_doc(path: str) -> bool:
    """Return True iff path ends with the exact BUILD_TESTS_SUFFIX."""
    return path.endswith(BUILD_TESTS_SUFFIX)


def doc_has_approval(text: str) -> bool:
    """Return True iff APPROVAL_TOKEN is a substring of text."""
    return APPROVAL_TOKEN in text


# ─── core decision logic ──────────────────────────────────────────────────────


def scan_audit_violation(
    staged_paths: list[str],
    commit_message: str,
    *,
    modified_paths: list[str] | None = None,
    env=None,
    exists=os.path.exists,
    read_text=lambda p: open(p, encoding="utf-8").read(),
) -> AuditDecision:
    """Evaluate whether the staged commit requires an APPROVED audit doc.

    staged_paths — ALL staged paths (used for doc detection in step 4).
    modified_paths — Modified/Renamed paths only (used for engine_py prod detection
                     in step 1). When None, staged_paths is used for both (backward
                     compat — keeps AC1–AC9 green).

    Decision order (§1 frozen design):
    1. changed = engine_py prod paths that exist; if empty → ALLOW.
    2. Kill-switch env == "0" → ALLOW (escape=kill_switch).
    3. Pragma with non-empty reason in commit_message → ALLOW (escape=pragma).
    4. Co-staged *_build_tests.md with APPROVAL_TOKEN → ALLOW (escape=approved_doc).
    5. else → BLOCK.
    """
    env = config_provider.env_mapping() if env is None else env

    # Step 1 — collect changed engine_py prod paths that exist.
    # Use modified_paths if provided (CLI passes MR-only list); else fall back to
    # staged_paths so existing unit tests (modified_paths=None) remain green.
    mod = modified_paths if modified_paths is not None else staged_paths
    changed = [p for p in mod if is_engine_py_prod(p) and exists(p)]
    if not changed:
        return AuditDecision(
            blocked=False,
            changed=[],
            reason="no engine_py prod files modified",
            escape=None,
        )

    # Step 2 — kill-switch.
    if env.get(KILL_SWITCH_ENV) == "0":
        return AuditDecision(
            blocked=False,
            changed=changed,
            reason="kill_switch env override",
            escape="kill_switch",
        )

    # Step 3 — pragma with non-empty reason.
    m = _PRAGMA_RE.search(commit_message)
    if m:
        reason_text = m.group(1)
        return AuditDecision(
            blocked=False,
            changed=changed,
            reason=f"pragma escape: {reason_text}",
            escape="pragma",
        )

    # Step 4 — co-staged approved build_tests doc.
    for p in staged_paths:
        if not is_build_tests_doc(p):
            continue
        if not exists(p):
            continue
        try:
            text = read_text(p)
        except OSError:
            continue  # skip unreadable candidate docs, keep checking
        if doc_has_approval(text):
            return AuditDecision(
                blocked=False,
                changed=changed,
                reason="co-staged APPROVED build_tests doc",
                escape="approved_doc",
            )

    # Step 5 — block.
    return AuditDecision(
        blocked=True,
        changed=changed,
        reason=(
            "engine_py prod modified without co-staged APPROVED *_build_tests.md. "
            "Co-stage an APPROVED *_build_tests.md, or use "
            "'engine-py-audit: skip <reason>' in the commit message."
        ),
        escape=None,
    )


# ─── git integration ──────────────────────────────────────────────────────────


def _git_staged_modified_paths() -> list[str]:
    """Return repo-relative paths of staged Modified/Renamed files.

    Uses --diff-filter=MR (not ACMR) so that Added files are NOT gated —
    parity with tier_gate which intentionally allows new engine_py files under MICRO.
    """
    result = git_read(["diff", "--cached", "--name-only", "--diff-filter=MR"])  # ambient-cwd: allow commit-msg hook reads the index of whatever repo invoked it
    stdout = result.stdout if result.stdout else ""
    return [line for line in stdout.splitlines() if line.strip()]


def _git_all_staged_paths() -> list[str]:
    """Return repo-relative paths of ALL staged files (Added, Copied, Modified, Renamed).

    Uses --diff-filter=ACMR so that newly-added files such as *_build_tests.md
    docs are included — the CLI passes this list as staged_paths to
    scan_audit_violation so doc detection works even when the doc is brand-new.
    """
    result = git_read(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])  # ambient-cwd: allow commit-msg hook reads the index of whatever repo invoked it
    stdout = result.stdout if result.stdout else ""
    return [line for line in stdout.splitlines() if line.strip()]


# ─── hook installer ───────────────────────────────────────────────────────────


def _make_hook_block(gate_path: str) -> str:
    """Return the sentinel-wrapped hook block for the given gate_path."""
    return (
        f"{_SENTINEL_OPEN}\n"
        f'python3 "{gate_path}" --message-file "$1" || exit 1\n'
        f"{_SENTINEL_CLOSE}\n"
    )


def install_commit_msg_hook(hooks_dir: str, gate_path: str) -> None:
    """Install (or update) the HAL audit gate block in hooks_dir/commit-msg.

    Idempotent: if the sentinel block is already present, it is replaced in-place.
    Pre-existing non-sentinel content is preserved.
    hooks_dir is created if it does not exist.
    """
    hooks_path = os.fspath(hooks_dir)
    os.makedirs(hooks_path, exist_ok=True)

    hook_file = os.path.join(hooks_path, "commit-msg")
    block = _make_hook_block(gate_path)

    if not os.path.exists(hook_file):
        # No existing hook — write shebang + block.
        content = f"#!/usr/bin/env bash\n{block}\n"
        with open(hook_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(hook_file, 0o755)
        return

    # Existing hook — read content, replace or append the sentinel block.
    with open(hook_file, "r", encoding="utf-8") as fh:
        existing = fh.read()

    if _SENTINEL_OPEN in existing:
        # Replace the existing sentinel block in-place.
        # Strategy: split on open sentinel, then split the suffix on close sentinel.
        before, rest = existing.split(_SENTINEL_OPEN, 1)
        if _SENTINEL_CLOSE in rest:
            _, after = rest.split(_SENTINEL_CLOSE, 1)
        else:
            after = rest
        new_content = before + block + after
    else:
        # Append the block (preserve everything, add a trailing newline if needed).
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new_content = existing + block

    with open(hook_file, "w", encoding="utf-8") as fh:
        fh.write(new_content)

    # Ensure executable bit is set (in case the pre-existing hook lacked it).
    current_mode = os.stat(hook_file).st_mode
    os.chmod(hook_file, current_mode | 0o111)
