"""GH373 Part A — universal authored-diff boundary scanner.

Spec: the state dir's memory/Decisions/2026-07-07_GH373_authored_boundary_verdict_spec.md

One shared scanner + one registry (§1g single source of truth). A new
model-authored-diff boundary (GREEN commit, FIX commit, repair-loop patch) is
a registry entry here, not a new bespoke guard scattered across workflows.
"""
from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import telemetry_ctx

# Gate-suppression blocklist (moved verbatim from lib/directed_repair.py:53-58,
# GH371 §2.6). A model-authored diff can "pass" a downstream gate by inserting
# a token the gate itself honors — silently exempting a real finding.
SUPPRESSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("security-lint: allow", re.compile(r"security-lint:\s*allow", re.IGNORECASE)),
    ("nosemgrep", re.compile(r"\bnosemgrep\b", re.IGNORECASE)),
    ("nosem", re.compile(r"\bnosem\b", re.IGNORECASE)),
    ("gitleaks:allow", re.compile(r"gitleaks:\s*allow", re.IGNORECASE)),
]

# Registry: boundary name -> policy. Unknown boundary name => ValueError
# (fail-closed) in scan_boundary.
BOUNDARIES: dict[str, dict] = {
    "green_commit": {"scan_suppression": True, "assert_tests_untouched": True},
    "fix_commit": {"scan_suppression": True, "assert_tests_untouched": False},
    "repair_patch": {"scan_suppression": True, "assert_tests_untouched": False},
}


def _added_content(pre_text: str, post_text: str) -> str:
    """Return only the content the diff ADDED — inserted/replaced new-side
    lines per difflib. A pre-existing token (an unchanged line) is never
    scanned; only NEW content is surfaced."""
    pre_lines = pre_text.splitlines()
    post_lines = post_text.splitlines()
    sm = difflib.SequenceMatcher(a=pre_lines, b=post_lines, autojunk=False)
    added: list[str] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            added.extend(post_lines[j1:j2])
    return "\n".join(added)


def scan_added_content(
    pre_text: str,
    post_text: str,
    forbidden_new_tokens: list[str] | None = None,
) -> list[str]:
    """Scan the ADDED content only for gate-suppression tokens. Returns the
    list of matched token strings (empty => clean). ``forbidden_new_tokens``
    are additional caller-supplied literal (case-insensitive) blocklist
    entries layered on top of the built-in patterns."""
    added = _added_content(pre_text, post_text)
    if not added:
        return []
    matched: list[str] = []
    for _label, pattern in SUPPRESSION_PATTERNS:
        m = pattern.search(added)
        if m:
            matched.append(m.group(0))
    lowered = added.lower()
    for tok in forbidden_new_tokens or []:
        if tok and tok.lower() in lowered:
            matched.append(tok)
    return matched


@dataclass
class BoundaryScanResult:
    suppression_hits: list[dict] = field(default_factory=list)
    tampered_tests: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.suppression_hits and not self.tampered_tests


def scan_boundary(
    boundary: str,
    *,
    base_sha: str,
    paths: list[str],
    git_cwd: str,
    is_test_path: Callable[[str], bool],
    list_changed_since: Callable[[], list[str]] | None = None,
    forbidden_new_tokens: list[str] | None = None,
    authorized_test_edits: list[str] | None = None,
) -> BoundaryScanResult:
    """Scan a model-authored diff against a registered boundary policy.

    Raises ValueError for an unregistered boundary name (fail-closed,
    checked before any git work). Raises RuntimeError if the
    tests-untouched leg's git invocation fails — scan infrastructure
    failure must never silently degrade to a clean scan.
    """
    policy = BOUNDARIES.get(boundary)
    if policy is None:
        raise ValueError(f"Unknown authored-diff boundary: {boundary!r}")

    # Non-git-repo probe (§2 step 0): git_cwd may be a bare tmp dir with no
    # .git (e.g. gitignore-filter fixtures) — that is not a scan-infrastructure
    # FAILURE, it is simply nothing to scan. Skip both legs cleanly rather than
    # raising RuntimeError.
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=git_cwd, capture_output=True, text=True, timeout=30,
        )
        is_git_repo = probe.returncode == 0
    except OSError:
        is_git_repo = False

    if not is_git_repo:
        telemetry_ctx.emit_safe(
            "authored_boundary_scan",
            {
                "boundary": boundary,
                "n_paths": len(paths),
                "n_suppression_hits": 0,
                "n_tampered_tests": 0,
                "skipped": "no_git_repo",
            },
        )
        return BoundaryScanResult(suppression_hits=[], tampered_tests=[])

    suppression_hits: list[dict] = []
    tampered_tests: list[str] = []
    n_authorized_test_edits = 0

    if policy.get("scan_suppression"):
        for path in paths:
            show = subprocess.run(
                ["git", "show", f"{base_sha}:{path}"],
                cwd=git_cwd, capture_output=True, text=True, timeout=30,
            )
            pre = show.stdout if show.returncode == 0 else ""
            try:
                post = (Path(git_cwd) / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tokens = scan_added_content(pre, post, forbidden_new_tokens)
            if tokens:
                suppression_hits.append({"path": path, "tokens": tokens})

    if policy.get("assert_tests_untouched"):
        if list_changed_since is not None:
            changed = list_changed_since()
        else:
            diff = subprocess.run(
                ["git", "diff", "--name-only", base_sha],
                cwd=git_cwd, capture_output=True, text=True, timeout=30,
            )
            if diff.returncode != 0:
                raise RuntimeError(
                    f"scan_boundary: git diff --name-only {base_sha} failed "
                    f"(rc={diff.returncode}): {diff.stderr[:500]}"
                )
            changed = [line for line in diff.stdout.splitlines() if line.strip()]
        # GH436 §2.2: whitelist narrows the tamper leg via EXACT-match
        # exclusion only — no fnmatch/glob (fail-closed escape hatch).
        allowed = set(authorized_test_edits or [])
        tampered_tests = [p for p in changed if is_test_path(p) and p not in allowed]
        n_authorized_test_edits = len(
            [p for p in changed if is_test_path(p) and p in allowed]
        )

    telemetry_ctx.emit_safe(
        "authored_boundary_scan",
        {
            "boundary": boundary,
            "n_paths": len(paths),
            "n_suppression_hits": len(suppression_hits),
            "n_tampered_tests": len(tampered_tests),
            "n_authorized_test_edits": n_authorized_test_edits,
        },
    )

    return BoundaryScanResult(suppression_hits=suppression_hits, tampered_tests=tampered_tests)


# ─── GH639 (7C0FDE44) — frozen-hash manifest for committed RED tests ────────
# Git-diff-blind residual coverage: pure content-hash helpers, no git, no
# raise. See SHARED/memory/Decisions/2026-07-12_7C0FDE44_gh639_red_hash_freeze_spec.md


def compute_red_test_hashes(paths: "list[str]", git_cwd: str) -> "dict[str, str]":
    """SHA-256 hex digest per path (resolved under git_cwd). Unreadable /
    absent file -> sentinel ``"__MISSING__"``. Deterministic, no subprocess,
    never raises."""
    result: "dict[str, str]" = {}
    for path in paths:
        try:
            data = (Path(git_cwd) / path).read_bytes()
            result[path] = hashlib.sha256(data).hexdigest()
        except OSError:
            result[path] = "__MISSING__"
    return result


def verify_red_test_hashes(
    frozen: "dict[str, str]",
    git_cwd: str,
    authorized_test_edits: "list[str] | None" = None,
) -> "list[str]":
    """Recompute hashes for ``frozen.keys()`` and return the sorted list of
    paths whose current hash differs from the frozen one (content edit or
    now-``__MISSING__`` deletion), excluding exact-match
    ``authorized_test_edits``. ``[]`` => untouched. Never raises."""
    current = compute_red_test_hashes(list(frozen.keys()), git_cwd)
    allowed = set(authorized_test_edits or [])
    tampered = [
        path for path, digest in frozen.items()
        if current.get(path) != digest and path not in allowed
    ]
    return sorted(tampered)


# ─── GH921 — classify a frozen-hash mismatch (real tamper vs HEAD moved) ────


def classify_red_hash_mismatches(frozen: "dict[str, str]", git_cwd: str) -> "dict[str, str]":
    """For each ``path`` in ``frozen`` whose current content-hash differs from
    the frozen digest, classify the mismatch:

    - current is ``"__MISSING__"`` -> ``"missing"``
    - ``git show HEAD:<path>`` succeeds and its digest == current worktree
      digest -> ``"head_moved"`` (committed change; worktree clean vs HEAD)
    - ``git show HEAD:<path>`` succeeds and its digest != current -> ``"worktree_dirty"``
      (real tamper — uncommitted edit on top of HEAD)
    - ``git show HEAD:<path>`` fails (path not in HEAD / git error) ->
      ``"head_unreadable"`` (fail-CLOSED: treated as tamper)

    Matching paths are omitted from the result. ``{}`` when nothing mismatches.
    Never raises: subprocess errors are classified ``"head_unreadable"``."""
    current = compute_red_test_hashes(list(frozen.keys()), git_cwd)
    result: "dict[str, str]" = {}
    for path, frozen_digest in frozen.items():
        current_digest = current.get(path)
        if current_digest == frozen_digest:
            continue
        if current_digest == "__MISSING__":
            result[path] = "missing"
            continue
        try:
            show = subprocess.run(
                ["git", "show", f"HEAD:{path}"],
                cwd=git_cwd, capture_output=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            result[path] = "head_unreadable"
            continue
        if show.returncode != 0:
            result[path] = "head_unreadable"
            continue
        head_digest = hashlib.sha256(show.stdout).hexdigest()
        result[path] = "head_moved" if head_digest == current_digest else "worktree_dirty"
    return result
