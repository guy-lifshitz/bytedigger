"""corpus_parity — GH1338 base-freshness + test-corpus-divergence preconditions.

Spec: 2026-07-30_GH1338_corpus_parity_gate_spec.md §2.1, §10 (rev 4)

Public API:
    PRECONDITION_CODES     — frozen §1n closure of leaf-function status codes
    BLOCKED_BY_ORDER        — fixed §10 rev-4 order for evaluate_preconditions.blocked_by
    SUITE_TEST_PATTERNS    — basename glob patterns per suite
    check_base_freshness(base_ref, head_ref="HEAD", *, cwd=None) -> dict
    collect_corpus(ref, suite, *, cwd=None) -> dict   # ref=None -> PR/worktree side
    compare_corpus(base_files, pr_files, declared_removals=()) -> dict
    evaluate_preconditions(base_ref, head_ref, suite, *, cwd=None,
                           declared_removals=()) -> dict
    parse_run_evidence(text, suite) -> RunEvidence

stdlib + lib.git_port.git_read ONLY — no raw subprocess (git-port-bypass-lint.py
scans this tree). Every git_read call carries an explicit timeout=30 (§8 m3).

§10 MAJOR-1 note: git_read has no env-passthrough parameter, so the
GIT_TERMINAL_PROMPT=0 non-interactivity guard the spec asks for on the
`ls-remote` network call is achieved via argv hardening instead
(`-c core.askPass=true`), NOT by bypassing the git_read port with a raw
subprocess call. The `timeout=30` bound remains the fail-closed backstop if
that hardening is ever insufficient on some git version.
"""
from __future__ import annotations

import fnmatch
import os
import re
from typing import TypedDict

from bytedigger_engine.lib.git_port import git_read


class FreshnessResult(TypedDict, total=False):
    status: str
    remote_checked: bool
    remote_check_reason: str


class CorpusResult(TypedDict, total=False):
    status: str
    files: list[str]
    source: str
    detail: str


class CompareResult(TypedDict, total=False):
    status: str
    only_in_base: list[str]
    only_in_pr: list[str]
    declared_removals_honored: list[str]
    declared_removals_stale: list[str]
    declared_removal_count: int
    undeclared_removals: list[str]


class PreconditionResult(TypedDict, total=False):
    status: str
    blocked_by: list[str]
    only_in_base: list[str]
    only_in_pr: list[str]
    declared_removal_count: int


class RunEvidence(TypedDict):
    has_summary: bool
    n_tests: int
    collected_files: list[str]
    detail: str


PRECONDITION_CODES = frozenset({
    "OK",
    "E_STALE_BASE",
    "E_STALE_REMOTE_REF",
    "E_REMOTE_UNREACHABLE",
    "E_FRESHNESS_UNKNOWN",
    "E_CORPUS_DIVERGENCE",
    "E_CORPUS_UNKNOWN",
    "E_RESULTS_UNPARSEABLE",
    "E_EMPTY_RUN",
})

SUITE_TEST_PATTERNS = {
    "pytest": ("test_*.py", "*_test.py"),
    "bun": ("*.test.ts",),
}

_GIT_TIMEOUT = 30

# §10 rev-4 evaluate_preconditions blocked_by FIXED order (8 codes; supersedes
# the 4-element §2.1 rev-1 order — §10.0 drift note).
BLOCKED_BY_ORDER = (
    "E_STALE_BASE",
    "E_STALE_REMOTE_REF",
    "E_REMOTE_UNREACHABLE",
    "E_FRESHNESS_UNKNOWN",
    "E_CORPUS_DIVERGENCE",
    "E_CORPUS_UNKNOWN",
    "E_RESULTS_UNPARSEABLE",
    "E_EMPTY_RUN",
)
# backward-compat internal alias
_BLOCKED_BY_ORDER = BLOCKED_BY_ORDER

_REMOTE_TRACKING_PREFIX = "refs/remotes/"


def _classify_remote_tracking(base_ref: str, *, cwd: str | None) -> tuple[str, str] | None:
    """(remote, branch) if base_ref resolves (by RESOLUTION, not string prefix)
    to a refs/remotes/<remote>/<branch> ref; else None. §10 MAJOR-1: prod
    passes the SHORT form `origin/main`, and `@{u}` must classify identically
    — a startswith("refs/remotes/") check on the RAW input string would miss
    both."""
    try:
        result = git_read(
            ["rev-parse", "--symbolic-full-name", base_ref], cwd=cwd, timeout=_GIT_TIMEOUT,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    full = result.stdout.strip()
    if not full.startswith(_REMOTE_TRACKING_PREFIX):
        return None
    rest = full[len(_REMOTE_TRACKING_PREFIX):]
    parts = rest.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def check_base_freshness(base_ref: str, head_ref: str = "HEAD", *, cwd: str | None = None) -> FreshnessResult:
    """Two INDEPENDENT freshness measurements (§10 MAJOR-1):

    1. local ancestry: `merge-base --is-ancestor base_ref head_ref`. rc0 -> local
       ancestry OK; rc1 -> E_STALE_BASE; any other rc (incl. 124-timeout) or
       OSError -> E_FRESHNESS_UNKNOWN. Fail-closed: there is no third
       "probably fresh" outcome.
    2. if local ancestry is OK AND base_ref classifies (by `git rev-parse
       --symbolic-full-name`) as a remote-tracking ref: `git ls-remote
       <remote> refs/heads/<branch>` must agree with the LOCAL sha of
       base_ref. Disagreement -> E_STALE_REMOTE_REF. Unreachable/rc!=0/
       OSError -> E_REMOTE_UNREACHABLE (fail-closed). A non-remote-tracking
       base_ref (raw sha, local branch) records remote_checked=False with a
       non-empty remote_check_reason — local ancestry alone decides.
    """
    try:
        result = git_read(["merge-base", "--is-ancestor", base_ref, head_ref], cwd=cwd, timeout=_GIT_TIMEOUT)
    except OSError:
        return {
            "status": "E_FRESHNESS_UNKNOWN", "remote_checked": False,
            "remote_check_reason": "local ancestry check raised OSError",
        }
    if result.returncode == 1:
        return {
            "status": "E_STALE_BASE", "remote_checked": False,
            "remote_check_reason": "local ancestry failed; remote check not attempted",
        }
    if result.returncode != 0:
        return {
            "status": "E_FRESHNESS_UNKNOWN", "remote_checked": False,
            "remote_check_reason": f"local ancestry check returned rc={result.returncode}",
        }

    # local ancestry is OK — classify and, if applicable, verify against the
    # TRUE remote (§10 MAJOR-1: a stale LOCAL tracking ref must not read as fresh).
    remote_branch = _classify_remote_tracking(base_ref, cwd=cwd)
    if remote_branch is None:
        return {
            "status": "OK", "remote_checked": False,
            "remote_check_reason": f"{base_ref!r} is not a remote-tracking ref; local ancestry decides",
        }

    remote_name, branch_name = remote_branch

    try:
        local_sha_result = git_read(["rev-parse", base_ref], cwd=cwd, timeout=_GIT_TIMEOUT)
    except OSError:
        return {
            "status": "E_REMOTE_UNREACHABLE", "remote_checked": True,
            "remote_check_reason": "could not resolve local sha for base_ref (OSError)",
        }
    if local_sha_result.returncode != 0:
        return {
            "status": "E_REMOTE_UNREACHABLE", "remote_checked": True,
            "remote_check_reason": "could not resolve local sha for base_ref",
        }
    local_sha = local_sha_result.stdout.strip()

    try:
        ls_remote_result = git_read(
            ["-c", "core.askPass=true", "ls-remote", remote_name, f"refs/heads/{branch_name}"],
            cwd=cwd, timeout=_GIT_TIMEOUT,
        )
    except OSError:
        return {
            "status": "E_REMOTE_UNREACHABLE", "remote_checked": True,
            "remote_check_reason": "ls-remote raised OSError",
        }
    if ls_remote_result.returncode != 0:
        return {
            "status": "E_REMOTE_UNREACHABLE", "remote_checked": True,
            "remote_check_reason": f"ls-remote returned rc={ls_remote_result.returncode}",
        }

    remote_lines = [ln for ln in ls_remote_result.stdout.splitlines() if ln.strip()]
    if not remote_lines:
        return {
            "status": "E_REMOTE_UNREACHABLE", "remote_checked": True,
            "remote_check_reason": "ls-remote returned no matching ref",
        }
    remote_sha = remote_lines[0].split()[0]

    if remote_sha != local_sha:
        return {
            "status": "E_STALE_REMOTE_REF", "remote_checked": True,
            "remote_check_reason": f"local tracking sha {local_sha} != true remote sha {remote_sha}",
        }

    return {
        "status": "OK", "remote_checked": True,
        "remote_check_reason": "local tracking ref sha matches the true remote",
    }


def _match_suite(path: str, suite: str) -> bool:
    name = os.path.basename(path)
    patterns = SUITE_TEST_PATTERNS.get(suite, ())
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _parse_porcelain_z(raw: str) -> list[tuple[str, str, str | None]]:
    """Parse `git status --porcelain -z` records into (code, path, old_path|None).

    NUL-delimited (handles spaces/unicode in paths). For rename/copy records
    (R/C anywhere in the 2-char code) the path field is followed by an
    additional NUL-terminated field carrying the OLD path.
    """
    entries = raw.split("\0")
    parsed: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        code = entry[:2]
        path = entry[3:]
        if "R" in code or "C" in code:
            old_path = entries[i] if i < len(entries) else ""
            i += 1
            parsed.append((code, path, old_path))
        else:
            parsed.append((code, path, None))
    return parsed


def _deleted_paths_from_status(raw: str) -> set[str]:
    """Deleted-in-worktree paths: plain `D` entries plus rename OLD paths
    (a rename is a deletion+addition — same as an uncommitted delete)."""
    deleted: set[str] = set()
    for code, path, old_path in _parse_porcelain_z(raw):
        if "D" in code:
            deleted.add(path)
        if old_path is not None:
            deleted.add(old_path)
    return deleted


def _collect_corpus_ref(ref: str, suite: str, *, cwd: str | None) -> CorpusResult:
    try:
        result = git_read(["ls-tree", "-r", "--name-only", ref], cwd=cwd, timeout=_GIT_TIMEOUT)
    except OSError:
        return {"status": "E_CORPUS_UNKNOWN"}
    if result.returncode != 0:
        return {"status": "E_CORPUS_UNKNOWN"}
    files = sorted({p for p in result.stdout.splitlines() if p and _match_suite(p, suite)})
    if not files:
        return {"status": "E_CORPUS_UNKNOWN"}
    return {"status": "OK", "files": files, "source": "git-tree"}


def _collect_corpus_worktree(suite: str, *, cwd: str | None) -> CorpusResult:
    try:
        head_result = git_read(["ls-tree", "-r", "--name-only", "HEAD"], cwd=cwd, timeout=_GIT_TIMEOUT)
    except OSError:
        return {"status": "E_CORPUS_UNKNOWN"}
    if head_result.returncode != 0:
        return {"status": "E_CORPUS_UNKNOWN"}

    try:
        status_result = git_read(["status", "--porcelain", "-z"], cwd=cwd, timeout=_GIT_TIMEOUT)
    except OSError:
        return {"status": "E_CORPUS_UNKNOWN"}
    if status_result.returncode != 0:
        return {"status": "E_CORPUS_UNKNOWN"}

    try:
        others_result = git_read(
            ["ls-files", "--others", "--exclude-standard", "-z"], cwd=cwd, timeout=_GIT_TIMEOUT,
        )
    except OSError:
        return {"status": "E_CORPUS_UNKNOWN"}
    if others_result.returncode != 0:
        return {"status": "E_CORPUS_UNKNOWN"}

    deleted = _deleted_paths_from_status(status_result.stdout)
    tracked = {p for p in head_result.stdout.splitlines() if p and _match_suite(p, suite)}
    tracked -= deleted
    untracked = {p for p in others_result.stdout.split("\0") if p and _match_suite(p, suite)}
    files = sorted(tracked | untracked)
    if not files:
        return {"status": "E_CORPUS_UNKNOWN"}
    return {"status": "OK", "files": files, "source": "worktree"}


def collect_corpus(ref: str | None, suite: str, *, cwd: str | None = None) -> CorpusResult:
    """Test-file set for one side. ref=None -> PR side (the WORKING TREE, §8 M4)."""
    if ref is not None:
        return _collect_corpus_ref(ref, suite, cwd=cwd)
    return _collect_corpus_worktree(suite, cwd=cwd)


def _token_covers(token: str, path: str) -> bool:
    return path == token or path.endswith("/" + token)


def compare_corpus(
    base_files: list[str], pr_files: list[str], declared_removals: tuple[str, ...] = (),
) -> CompareResult:
    """missing = base - pr; extra = pr - base. Both are ALWAYS fully enumerated."""
    base_set = set(base_files)
    pr_set = set(pr_files)
    missing = sorted(base_set - pr_set)
    extra = sorted(pr_set - base_set)

    tokens = list(declared_removals)
    honored: list[str] = []
    undeclared: list[str] = []
    for path in missing:
        if any(_token_covers(token, path) for token in tokens):
            honored.append(path)
        else:
            undeclared.append(path)

    stale = [
        token for token in tokens
        if not any(_token_covers(token, path) for path in missing)
    ]

    status = "OK" if not undeclared else "E_CORPUS_DIVERGENCE"

    return {
        "status": status,
        "only_in_base": missing,
        "only_in_pr": extra,
        "declared_removals_honored": honored,
        "declared_removals_stale": stale,
        "declared_removal_count": len(honored),
        "undeclared_removals": undeclared,
    }


def evaluate_preconditions(
    base_ref: str, head_ref: str, suite: str, *, cwd: str | None = None,
    declared_removals: tuple[str, ...] = (),
) -> PreconditionResult:
    """Composite precondition: status 'OK'|'BLOCKED', blocked_by in the fixed
    §10 rev-4 order.

    PR side is ALWAYS collect_corpus(None, suite) — the working tree, not the
    head_ref tree (§8 M4) — an uncommitted test-file deletion must still show.
    """
    blocked: set[str] = set()

    freshness = check_base_freshness(base_ref, head_ref, cwd=cwd)
    if freshness["status"] != "OK":
        blocked.add(freshness["status"])

    base_corpus = collect_corpus(base_ref, suite, cwd=cwd)
    pr_corpus = collect_corpus(None, suite, cwd=cwd)

    only_in_base: list[str] = []
    only_in_pr: list[str] = []
    declared_removal_count = 0

    if base_corpus.get("status") != "OK" or pr_corpus.get("status") != "OK":
        blocked.add("E_CORPUS_UNKNOWN")
    else:
        cmp_result = compare_corpus(
            base_corpus.get("files") or [], pr_corpus.get("files") or [],
            declared_removals=declared_removals,
        )
        only_in_base = cmp_result.get("only_in_base") or []
        only_in_pr = cmp_result.get("only_in_pr") or []
        declared_removal_count = cmp_result.get("declared_removal_count", 0)
        if cmp_result.get("status") == "E_CORPUS_DIVERGENCE":
            blocked.add("E_CORPUS_DIVERGENCE")

    blocked_by = [code for code in BLOCKED_BY_ORDER if code in blocked]
    status = "OK" if not blocked_by else "BLOCKED"

    return {
        "status": status,
        "blocked_by": blocked_by,
        "only_in_base": only_in_base,
        "only_in_pr": only_in_pr,
        "declared_removal_count": declared_removal_count,
    }


# ── §10 MAJOR-3: run-evidence parsing (results-log emptiness/crash detection) ──

_PYTEST_PASSED_RE = re.compile(r"(\d+)\s+passed")
_PYTEST_FAILED_RE = re.compile(r"(\d+)\s+failed")
_PYTEST_NO_TESTS_RE = re.compile(r"no tests ran")
_PYTEST_COLLECTED_RE = re.compile(r"collected\s+(\d+)\s+item")
_PYTEST_NODEID_RE = re.compile(r"(\S+\.py)::")

_BUN_SUMMARY_RE = re.compile(r"Ran\s+(\d+)\s+tests?\s+across\s+(\d+)\s+files?")
_BUN_FILE_HEADER_RE = re.compile(r"^(\S+\.test\.ts):\s*$", re.MULTILINE)

# §AC42: GitHub Actions decorates raw stdout with ANSI SGR colour escapes and
# `::group::`/`::endgroup::`-style workflow-command prefixes glued onto the
# SAME line as the content they wrap. Both must be stripped, PER LINE, before
# any file-header or summary-line matching — otherwise the anchored regexes
# above either miss entirely or capture the decoration as part of the match.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_GH_WORKFLOW_CMD_RE = re.compile(r"^::[A-Za-z0-9_-]+::")


def normalize_ci_line(line: str) -> str:
    """Strip ANSI SGR escapes, then a leading `::<cmd>::` GH Actions
    workflow-command prefix, then surrounding whitespace. Order matters:
    ANSI stripping first so a `::group::<ESC>[1mtext<ESC>[0m` line still
    has its workflow-command prefix at position 0 for the anchored strip.

    Public API (GH1338 §AC43): reused verbatim by
    `baseline_delta_gate.parse_pytest_fails`/`parse_bun_fails` so both
    consumers normalize identically instead of duplicating the regexes."""
    line = _ANSI_ESCAPE_RE.sub("", line)
    line = _GH_WORKFLOW_CMD_RE.sub("", line)
    return line.strip()


# backward-compat internal alias (module-private name used above in this file)
_normalize_ci_line = normalize_ci_line


def _normalize_ci_text(text: str) -> str:
    """Apply _normalize_ci_line to every line, rejoined with '\\n'. A clean
    undecorated log is unchanged (normalization is a no-op on it) so
    decorated and undecorated logs converge to the SAME normalized text —
    this must not be implemented as "drop decorated lines"."""
    return "\n".join(_normalize_ci_line(ln) for ln in text.splitlines())


def _parse_pytest_evidence(text: str) -> tuple[bool, int, list[str], str]:
    files = sorted(set(_PYTEST_NODEID_RE.findall(text)))
    if _PYTEST_NO_TESTS_RE.search(text):
        return True, 0, files, "no tests ran"
    passed_m = _PYTEST_PASSED_RE.search(text)
    failed_m = _PYTEST_FAILED_RE.search(text)
    if passed_m or failed_m:
        n_tests = (int(passed_m.group(1)) if passed_m else 0) + (
            int(failed_m.group(1)) if failed_m else 0
        )
        return True, n_tests, files, "passed/failed summary line"
    collected_m = _PYTEST_COLLECTED_RE.search(text)
    if collected_m:
        return True, int(collected_m.group(1)), files, "collected N items summary line"
    return False, 0, [], "no recognizable pytest summary line"


def _parse_bun_evidence(text: str) -> tuple[bool, int, list[str], str]:
    files = sorted(set(_BUN_FILE_HEADER_RE.findall(text)))
    m = _BUN_SUMMARY_RE.search(text)
    if m:
        return True, int(m.group(1)), files, "Ran N tests across M files summary line"
    return False, 0, files, "no recognizable bun summary line"


def parse_run_evidence(text: str, suite: str) -> RunEvidence:
    """§10 MAJOR-3: does `text` (a --results log) carry a real run summary, and
    how many tests did it report? An empty/truncated file or a bare collector
    crash line (e.g. `ImportError: collection failed`) has has_summary=False.
    A parseable summary reporting zero tests (`Ran 0 tests across 0 files`,
    `no tests ran`) has has_summary=True but n_tests=0 — 'nothing to check'
    printed as 'matched' is exactly the #1428 class this closes.
    """
    normalized = _normalize_ci_text(text)
    if suite == "bun":
        has_summary, n_tests, files, detail = _parse_bun_evidence(normalized)
    else:
        has_summary, n_tests, files, detail = _parse_pytest_evidence(normalized)
    return {
        "has_summary": has_summary,
        "n_tests": n_tests,
        "collected_files": files,
        "detail": detail,
    }
