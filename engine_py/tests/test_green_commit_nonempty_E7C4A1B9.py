"""RED tests for #1612-A -- `_commit_green_code` must prove its own commit is
not empty.

Frozen spec (v2, supersedes v1 -- gate REJECTED v1 with 3 blocking findings):
SHARED/memory/Decisions/2026-08-09_1612_green_commit_nonempty_spec_v2.md

Mechanism (from the spec): `git stash push -u` (leaked from the baseline-delta
helpers, or any other route that empties the tree) takes BOTH edits to
existing tracked files AND GREEN's newly-created untracked files -- two
distinct silent-PASS routes through `_commit_green_code`:

  Route 1 (AC1/AC2/AC3/AC5/AC9): `_paths_have_staged_changes` finds nothing
  staged -- the 5325B280 idempotent-skip leg. The fix (D2) compares the
  resulting HEAD against `red_commit_sha` with
  `git diff --name-only <red_commit_sha> HEAD -- <prod_paths>`: empty output
  WITH rc==0, prod_paths non-empty, is the incident -> terminal
  `E_GREEN_COMMIT_EMPTY`. rc!=0 (unresolvable rev) is NEVER a verdict (D3,
  AC4/AC9) -- fail open, emit `green_commit_nonempty_check_skipped`.

  Route 2 (AC7/AC8): `_filter_phantom_deleted_paths` drops every path absent
  from disk AND untracked -- exactly what a stashed new file looks like.
  D1 separates "manifest was empty to begin with" (AC8, unchanged `ok`) from
  "manifest was non-empty and filtering emptied it" (AC7, now terminal
  `E_GREEN_COMMIT_EMPTY`, naming the filtered paths).

UUT: `_commit_green_code` (workflows/phase_5_implement.py) -- never mocked,
always invoked against a REAL on-disk git repo (§1l): every AC below drives
real `git init`/commit/stash side effects, asserting against real
`git diff`/`git rev-parse` output, never a stubbed diff.

§1q: every symbol this file needs (`E_GREEN_COMMIT_EMPTY`,
`green_commit_nonempty_check_skipped`) does not exist yet on `main` today.
No module-level reference to either literal string constant is required for
import/collection -- they only ever appear inside assert-time string
comparisons inside test bodies, so this file collects cleanly and fails at
assert-time, never at collect-time.

Do NOT implement the contract here -- RED-only file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from bytedigger_engine.contracts import WorkflowContext
from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════
# shared helpers (real git repos, no mocking of the UUT -- §1l).
# Local copies, matching the idiom in test_gh1220_ambient_cwd_commit_refusal.py
# -- this is a new file, no existing test is modified (authorized-test-edits:
# none, per spec §5).
# ═════════════════════════════════════════════════════════════════════════


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str) -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str) -> Path:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout.strip()


def _make_repo_with_base_commit(tmp_path: Path, name: str = "repo") -> "tuple[Path, str]":
    repo = Path(str((tmp_path / name).resolve()))
    _init_repo(repo)
    base_sha = _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo, base_sha


def _record_events(monkeypatch, module) -> "list[dict]":
    captured: list[dict] = []

    def _recorder(event_type, payload=None, **kw):
        captured.append({"type": str(event_type), "payload": dict(payload or {})})
        return None

    monkeypatch.setattr(module, "_emit_safe", _recorder)
    return captured


def _make_ctx(scratchpad: Path, repo: Path) -> WorkflowContext:
    """Explicit git_cwd -- non-ambient (GH1220), so the ambient-refusal guard
    at :2125/:7390 never fires here; that guard is out of scope for this lot."""
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)},
        question="q", session_id="test-1612", persona="hal",
        framework=None, domain=None,
    )


def _prev(red_sha: str, cycle: int = 1) -> MagicMock:
    prev = MagicMock()
    prev.data = {
        "cycle": cycle,
        "red_commit_sha": red_sha,
        "worker_written_paths": ["src/module.py"],
        "manifest_source": "harness_tool_record",
    }
    return prev


# ═════════════════════════════════════════════════════════════════════════
# AC1 -- real commit, healthy (regression pin: byte-identical to today)
# ═════════════════════════════════════════════════════════════════════════


def test_ac1_real_commit_healthy_returns_ok_with_nonempty_diff_since_red(tmp_path):
    """AC1: prod_paths has staged changes, the commit lands, and
    `git diff --name-only <red_sha> HEAD -- prod_paths` is non-empty ->
    status='ok' with green_commit_sha == new HEAD. Spec: 'Byte-identical to
    today.' Expected to PASS already -- pinned so the new AC2 guard cannot
    regress the healthy path."""
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_x.py", "def test_x():\n    assert False\n", "RED")
    _write_file(repo, "src/module.py", "def foo():\n    return 42\n")

    scratchpad = tmp_path / "scratch_ac1"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = _prev(red_sha)

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok'; actual {result.status!r}: {getattr(result, 'error', '')!r}"
    )
    new_head = _head_sha(repo)
    assert result.data.get("green_commit_sha") == new_head, (
        f"expected green_commit_sha == new HEAD {new_head!r}; actual "
        f"{result.data.get('green_commit_sha')!r}"
    )
    diff = subprocess.run(
        ["git", "diff", "--name-only", red_sha, new_head, "--", "src/module.py"],
        capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert "src/module.py" in diff, (
        f"expected a real non-empty diff between red_sha and the new HEAD on "
        f"src/module.py; actual diff output={diff!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC2 -- the incident
# ═════════════════════════════════════════════════════════════════════════


def test_ac2_no_staged_diff_and_no_diff_since_red_is_terminal_error(tmp_path):
    """AC2 (the incident): prod_paths has NO staged changes, and HEAD carries
    NO in-scope change since red_commit_sha (the working tree already matches
    red_sha exactly) -> status='error', error_code='E_GREEN_COMMIT_EMPTY',
    recoverable=False. Pre-GREEN FAIL: today this returns status='ok' via the
    5325B280 idempotent-skip leg (git add stages nothing, HEAD unchanged,
    green_commit_sha=red_sha) -- the exact silent-PASS-on-an-empty-tree shape
    named in the spec's incident report."""
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(
        repo, "src/module.py", "def foo():\n    return 42\n",
        "RED cycle -- module already at final content, nothing left to stage",
    )

    scratchpad = tmp_path / "scratch_ac2"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = _prev(red_sha, cycle=2)

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "error", (
        f"expected status=='error' -- the GREEN cycle produced NO change to "
        f"any in-scope path since red_commit_sha; actual status={result.status!r} "
        f"data={result.data!r}"
    )
    assert result.error_code == "E_GREEN_COMMIT_EMPTY", (
        f"expected error_code=='E_GREEN_COMMIT_EMPTY'; actual {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False (a GREEN retry cannot fix a tree "
        f"that no longer holds the work -- #1633 burned ~$11 on exactly that); "
        f"actual {result.recoverable!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC3 -- benign re-entry preserved (the 5325B280 guard must not be weakened)
# ═════════════════════════════════════════════════════════════════════════


def test_ac3_benign_reentry_head_differs_from_red_stays_ok(tmp_path, monkeypatch):
    """AC3: prod_paths has no staged changes BECAUSE A PRIOR CYCLE already
    committed them -- HEAD DOES differ from red_commit_sha on the in-scope
    path -- step still returns 'ok', 'commit_green_code_idempotent_skip'
    still emitted. Expected to PASS already (today's real, correct 5325B280
    behavior) -- pinned so the new AC2 guard cannot weaken this leg."""
    captured = _record_events(monkeypatch, p5)
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_x.py", "def test_x():\n    assert False\n", "RED")
    _commit_file(repo, "src/module.py", "def foo():\n    return 42\n", "prior cycle green commit")

    scratchpad = tmp_path / "scratch_ac3"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = _prev(red_sha, cycle=2)

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' -- HEAD already differs from red_commit_sha "
        f"on src/module.py (a prior cycle committed it); actual "
        f"status={result.status!r} error_code={result.error_code!r}"
    )
    skip_events = [e for e in captured if e["type"] == "commit_green_code_idempotent_skip"]
    assert len(skip_events) == 1, (
        f"expected exactly 1 'commit_green_code_idempotent_skip' event (the "
        f"5325B280 guard unweakened); actual events seen: "
        f"{[e['type'] for e in captured]!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC4 -- fail-open on a missing/unresolvable floor
# ═════════════════════════════════════════════════════════════════════════


def test_ac4_unresolvable_red_sha_fails_open_and_emits_skip_reason(tmp_path, monkeypatch):
    """AC4: red_commit_sha is a well-formed 40-hex string (passes the
    existing top-of-function format validation) but is NOT a resolvable rev
    in THIS repo -> the new diff-boundary guard must not run; the step
    returns as it does today (a real commit lands, status='ok'), and emits
    'green_commit_nonempty_check_skipped' carrying a non-empty 'reason'.
    Pre-GREEN FAIL: this event does not exist anywhere in production today --
    no guard, no skip event, ever fires.

    Reachability note (gate round 3): with HAL_AUTHORED_BOUNDARY_GATE at its
    default (enabled unless the env value is exactly "0",
    config_provider.py:52-54), green_commit's policy sets
    assert_tests_untouched=True (authored_boundary.py:37), and :7328 calls
    scan_boundary with no list_changed_since override -- that scan's own
    `git diff --name-only <red_sha>` (authored_boundary.py:217-220) ALSO
    hits this bogus, unresolvable sha, exits 128, and RAISES, which :7337
    turns into E_BOUNDARY_SCAN_FAILED before git add and before this lot's
    guard is ever reached. AC4 is unsatisfiable by any in-scope GREEN edit
    with that gate on, so it is disabled here (mirrors AC9's identical
    reachability fix) -- an orthogonal, out-of-scope gate must not be the
    thing this AC's status=='ok' assertion depends on."""
    captured = _record_events(monkeypatch, p5)
    monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "0")
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "src/module.py", "def foo():\n    return 42\n")
    bogus_red_sha = "a" * 40  # well-formed hex, never a real commit in this repo

    scratchpad = tmp_path / "scratch_ac4"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = _prev(bogus_red_sha)

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' -- a fail-open guard must not invent a "
        f"verdict when its own input (an unresolvable red_commit_sha) is "
        f"unavailable; actual {result.status!r}: {getattr(result, 'error', '')!r}"
    )
    skip_events = [e for e in captured if e["type"] == "green_commit_nonempty_check_skipped"]
    assert len(skip_events) == 1, (
        f"expected exactly 1 'green_commit_nonempty_check_skipped' event on "
        f"an unresolvable red_commit_sha; actual events seen: "
        f"{[e['type'] for e in captured]!r}"
    )
    assert skip_events[0]["payload"].get("reason"), (
        f"expected a non-empty 'reason' in the skip event payload; actual "
        f"{skip_events[0]['payload']!r}"
    )

    # D4 (spec v3, blocking finding #2): a MALFORMED/missing red_commit_sha is
    # a DIFFERENT leg entirely -- the existing :7167-7185 early return, out of
    # scope for this lot -- and must stay untouched. Pinned here so the AC4
    # fail-open guard cannot be widened to also swallow this case. Expected
    # to PASS already (today's real, correct :7167 behavior).
    scratchpad_malformed = tmp_path / "scratch_ac4_malformed"
    scratchpad_malformed.mkdir()
    ctx_malformed = _make_ctx(scratchpad_malformed, repo)
    prev_malformed = _prev("not-a-valid-sha")

    result_malformed = p5._commit_green_code(ctx_malformed, prev_malformed)

    assert result_malformed.error_code == "E_MISSING_RED_BOUNDARY", (
        f"expected the malformed-sha leg to stay routed through the existing "
        f":7167 early return (E_MISSING_RED_BOUNDARY), untouched by this "
        f"lot's fail-open guard; actual {result_malformed.error_code!r} "
        f"(status={result_malformed.status!r})"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC5 -- side-effect anchor (§1l): a REAL `git stash push -u` leak, not a
# mocked diff.
# ═════════════════════════════════════════════════════════════════════════


def test_ac5_real_stash_leak_produces_terminal_error_naming_path_and_recovery(tmp_path):
    """AC5 (§1l side-effect anchor): a REAL on-disk git repo -- RED commit
    already carries src/module.py's placeholder content, then the GREEN
    worker's real edit is removed from the working tree by a REAL
    `git stash push -u` (the exact leak mechanism the spec names) ->
    `_commit_green_code` returns 'E_GREEN_COMMIT_EMPTY', and the error text
    contains BOTH an in-scope path (src/module.py) AND the literal
    'git stash list' (the recovery recipe the issue starts from). Pre-GREEN
    FAIL: today returns status='ok' (idempotent-skip), no error text at all."""
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(
        repo, "src/module.py", "def foo():\n    return 0  # placeholder\n",
        "RED cycle -- placeholder module",
    )
    module_path = repo / "src" / "module.py"
    module_path.write_text("def foo():\n    return 42  # GREEN impl\n")
    subprocess.run(
        ["git", "stash", "push", "-u", "-m", "leaked-p2-baseline"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    # Fixture precondition: the REAL stash reverted the tree to the placeholder.
    assert module_path.read_text() == "def foo():\n    return 0  # placeholder\n", (
        "fixture precondition FAILED: expected the real `git stash push -u` "
        "to have reverted src/module.py to its placeholder content"
    )
    stash_before = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert stash_before.strip(), "fixture precondition FAILED: expected a real stash entry"

    scratchpad = tmp_path / "scratch_ac5"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = _prev(red_sha)

    result = p5._commit_green_code(ctx, prev)

    assert result.error_code == "E_GREEN_COMMIT_EMPTY", (
        f"expected error_code=='E_GREEN_COMMIT_EMPTY' after the GREEN edit "
        f"was leaked into a real git stash; actual {result.error_code!r} "
        f"(status={result.status!r})"
    )
    err = getattr(result, "error", "") or ""
    assert "src/module.py" in err, (
        f"expected the in-scope path 'src/module.py' named in the error "
        f"text; actual error text={err!r}"
    )
    assert "git stash list" in err, (
        f"expected the literal recovery hint 'git stash list' in the error "
        f"text; actual error text={err!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC7 / AC8 -- route 2 (spec v3 D1: ONE rule over P, the pre-phantom-filter
# manifest). `git stash push -u` also removes GREEN's NEW untracked files.
# `_filter_phantom_deleted_paths` drops them as phantom, prod_paths empties
# -- but P (the manifest as it stood BEFORE that filter) is still non-empty,
# and `git diff <red_sha>..HEAD -- P` is EMPTY (the new file never landed in
# any commit) -> terminal, under the SAME single rule AC10 uses to stay
# `ok` for a committed deletion. AC7/AC10 together are what discriminates
# this v3 rule from v2's (now-rejected) separate "phantom filtering is
# always terminal" special case.
# ═════════════════════════════════════════════════════════════════════════


def test_ac7_phantom_filtered_new_untracked_manifest_is_terminal_error(tmp_path):
    """AC7 (route 2, under the v3 unified rule): manifest is non-empty and
    names only a NEW untracked file (never committed, never `git add`ed); a
    real `git stash push -u` removes it from disk entirely -> P (pre-phantom
    manifest) stays non-empty, but `git diff <red_sha>..HEAD -- P` is EMPTY
    (that file never landed in any commit) -> terminal
    'E_GREEN_COMMIT_EMPTY', naming the filtered path AND containing the
    literal 'git stash list' (spec v3 MINOR 5, every terminal leg). Pre-GREEN
    FAIL: today this returns status='ok', green_commit_sha=None at :7295
    (green_commit_skipped{empty_manifest}) -- the declared work silently
    vanishes and nobody notices (green_commit_sha has zero production
    readers per the spec's Mechanism section)."""
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_x.py", "def test_x():\n    assert False\n", "RED")
    new_untracked_path = "src/newmod.py"
    _write_file(repo, new_untracked_path, "def bar():\n    return 7\n")
    # Real side effect: `-u` sweeps the new untracked file into the stash too.
    subprocess.run(
        ["git", "stash", "push", "-u", "-m", "leaked-new-file"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    assert not (repo / new_untracked_path).exists(), (
        "fixture precondition FAILED: expected the real `git stash push -u` "
        "to have removed the new untracked file from disk entirely"
    )

    scratchpad = tmp_path / "scratch_ac7"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = MagicMock()
    prev.data = {
        "cycle": 1, "red_commit_sha": red_sha,
        "worker_written_paths": [new_untracked_path],
        "manifest_source": "harness_tool_record",
    }

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "error", (
        f"expected status=='error' -- the declared manifest was non-empty "
        f"but phantom-filtering emptied prod_paths entirely; actual "
        f"status={result.status!r} data={result.data!r}"
    )
    assert result.error_code == "E_GREEN_COMMIT_EMPTY", (
        f"expected error_code=='E_GREEN_COMMIT_EMPTY'; actual {result.error_code!r}"
    )
    err = getattr(result, "error", "") or ""
    assert new_untracked_path in err, (
        f"expected the filtered path {new_untracked_path!r} named in the "
        f"error text; actual error text={err!r}"
    )
    assert "git stash list" in err, (
        f"expected the literal recovery hint 'git stash list' on this "
        f"terminal leg too (spec v3 MINOR 5: every terminal leg names the "
        f"path AND the recovery recipe); actual error text={err!r}"
    )


def test_ac8_manifest_empty_from_the_start_stays_ok_empty_manifest_skip(tmp_path, monkeypatch):
    """AC8 (route 2 negative): manifest is empty FROM THE START (GREEN wrote
    nothing at all) -> unchanged behaviour: status='ok',
    green_commit_skipped{reason: empty_manifest}. The new AC7 guard must NOT
    fire here -- 'GREEN wrote nothing' is a different, already-reported
    failure, explicitly out of scope for this lot. Expected to PASS already
    (today's real, correct :7295 empty-manifest behavior)."""
    captured = _record_events(monkeypatch, p5)
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_x.py", "def test_x():\n    assert False\n", "RED")
    # No GREEN write at all -- working tree is clean relative to red_sha.

    scratchpad = tmp_path / "scratch_ac8"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = MagicMock()
    prev.data = {
        "cycle": 1, "red_commit_sha": red_sha,
        "worker_written_paths": [],
        "manifest_source": "harness_tool_record",
    }

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' -- an empty-from-the-start manifest is a "
        f"DIFFERENT, already-reported failure (GREEN wrote nothing), out of "
        f"scope for this lot; actual status={result.status!r} "
        f"error_code={result.error_code!r}"
    )
    assert result.data.get("green_commit_sha") is None, (
        f"expected green_commit_sha is None (unchanged empty-manifest "
        f"contract); actual {result.data.get('green_commit_sha')!r}"
    )
    skip_events = [e for e in captured if e["type"] == "green_commit_skipped"]
    assert len(skip_events) == 1, (
        f"expected exactly 1 'green_commit_skipped' event; actual events "
        f"seen: {[e['type'] for e in captured]!r}"
    )
    assert skip_events[0]["payload"].get("reason") == "empty_manifest", (
        f"expected reason=='empty_manifest' (unweakened by the AC7 guard); "
        f"actual {skip_events[0]['payload'].get('reason')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC10 -- the rule discriminator (spec v3 D1): an in-scope path whose
# DELETION IS ALREADY COMMITTED. `_filter_phantom_deleted_paths` empties
# prod_paths (absent from disk AND untracked -- exactly GH514(2)'s case),
# but P (pre-phantom manifest) is non-empty AND
# `git diff <red_sha>..HEAD -- P` IS non-empty (the deletion itself is a
# real, committed change) -> `ok`, NOT terminal. AC7 alone cannot
# distinguish v3's one-rule-over-P design from v2's rejected
# "phantom-filtering is always terminal" rule -- this is the test that can.
# ═════════════════════════════════════════════════════════════════════════


def test_ac10_committed_deletion_phantom_filtered_but_diff_nonempty_stays_ok(tmp_path):
    """AC10 (the rule discriminator): src/module.py exists at red_sha, its
    deletion is committed in a LATER commit (a healthy same-cycle re-entry
    referencing a manifest entry whose work is a deletion already landed) ->
    `_filter_phantom_deleted_paths` empties prod_paths (module.py is absent
    from disk AND no longer tracked), but P (the manifest before that
    filter) is non-empty and `git diff <red_sha>..HEAD -- P` shows the
    deletion itself as a real change -> status='ok', NOT
    'E_GREEN_COMMIT_EMPTY'. Under v2's now-rejected rule this healthy run
    would have died. Expected to PASS already (today's real, correct :7295
    empty-manifest behavior happens to also return 'ok' here, for the wrong
    reason -- this AC pins the RIGHT reason survives GREEN)."""
    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(
        repo, "src/module.py", "def foo():\n    return 0\n",
        "RED cycle -- module.py exists",
    )
    subprocess.run(["git", "rm", "-q", "src/module.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "cycle 1: module.py deletion already committed"],
        cwd=repo, check=True,
    )
    assert not (repo / "src" / "module.py").exists(), (
        "fixture precondition FAILED: expected the deletion commit to have "
        "removed src/module.py from disk"
    )

    scratchpad = tmp_path / "scratch_ac10"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = MagicMock()
    prev.data = {
        "cycle": 2, "red_commit_sha": red_sha,
        "worker_written_paths": ["src/module.py"],
        "manifest_source": "harness_tool_record",
    }

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' -- the deletion IS a real, committed "
        f"in-scope change since red_commit_sha, even though phantom "
        f"filtering emptied prod_paths; actual status={result.status!r} "
        f"error_code={result.error_code!r}: {getattr(result, 'error', '')!r}"
    )
    assert result.error_code != "E_GREEN_COMMIT_EMPTY", (
        "expected this healthy committed-deletion re-entry to NEVER be "
        "misclassified as an empty GREEN commit (the exact v2 regression "
        "the gate rejected)"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC9 -- rc-blindness, THE DISCRIMINATING CASE (spec v3.1 AMENDMENT): floor
# RESOLVABLE (the `rev-parse --verify` precheck succeeds against a genuine
# commit, for real, unmocked), but ANY `git diff` invocation the guard makes
# returns rc=128 with empty stdout. Fail-open + skip event, never terminal.
#
# §1d / honesty note, per the v3.1 amendment: a BAD-PATHSPEC construction
# (v3's original mechanism) was investigated and is UNREACHABLE -- `git
# diff`, `git ls-files` and `git add` all share git's pathspec parser, so
# any string that fails the guard's diff also fails
# `_filter_phantom_deleted_paths`'s ls-files call, whose degraded fallback
# (`phase_workflows_common.py:530-536`) then keeps that path UNFILTERED in
# prod_paths -- so `git add` dies on the identical pathspec BEFORE the new
# post-commit guard is ever reached. No fixture can make `add` succeed and
# the guard's `diff` fail this way. DO NOT "fix" this test back to a
# bad-pathspec manifest entry -- it was tried and does not work.
#
# AC9 instead forces rc != 0 at the git-READ SEAM (`lib/git_port.py`'s
# injectable `GitReadPort` factory -- a port designed for exactly this,
# `set_default_git_read_factory` / `reset_default_git_read_factory`, §2.6):
# every `git diff ...` invocation, regardless of caller, is rerouted to a
# synthetic rc=128/empty-stdout result; every OTHER git invocation
# (`rev-parse`, `add`, `commit`, `ls-files`, `check-ignore`, ...) passes
# through to the real subprocess unchanged. This is NOT a mock of the UUT:
# `_commit_green_code` and its guard still execute for real against a real
# on-disk repo, and it is invocation-shape-blind -- it catches a `diff`
# issued directly via `git_port.git_read(["diff", ...])` AND one issued
# indirectly through `git_diff_files` (which routes through the identical
# `lib.git_port.git_read` seam, per its own `from lib.git_port import
# git_read` import), so a GREEN that shells out with a pathspec and one
# that reuses `git_diff_files` + a python-side intersection are judged the
# same way. AC5/AC7/AC10 remain the unmocked real-git §1l anchors; this is
# the one AC that legitimately needs the injectable read seam.
# ═════════════════════════════════════════════════════════════════════════


def test_ac9_resolvable_floor_diff_seam_rc128_never_reads_as_empty(tmp_path, monkeypatch):
    """AC9 (discriminating case, v3.1): red_commit_sha is a REAL, resolvable
    commit -- the `rev-parse --verify <sha>^{commit}` precheck genuinely
    succeeds (proven as a fixture precondition), so this can never
    degenerate into AC4's unresolvable-floor mechanism. Every `git diff`
    call issued through `lib.git_port.git_read` (directly OR via
    `git_diff_files`) is rerouted, at the read seam, to rc=128/empty stdout.

    Residual-window note (r3 MINOR 2): a PERSISTENT rc failure never reaches
    this guard at all -- the authored-boundary scan's own `git diff` (see
    the HAL_AUTHORED_BOUNDARY_GATE disable below) hits it first and would
    return E_BOUNDARY_SCAN_FAILED upstream. So the guard's own rc-handling
    only ever bites on a TRANSIENT failure appearing in the narrow window
    between that scan and this guard -- most plausibly index-lock
    contention on a concurrent git operation, or a momentarily-unreadable
    object during GC. Narrow, but real and production-reachable; this test
    pins exactly that window, not one specific cause.

    A correct guard must fail open: status='ok' (unchanged from today),
    'green_commit_nonempty_check_skipped' emitted with a non-empty 'reason'
    (no severity requirement -- v3's severity="warning" claim was withdrawn,
    r3 MINOR 3: production `_emit_safe` already defaults to "warning" and
    severity never enters the event itself, phase_workflows_common.py:99-117
    -- vacuous either way). An rc-blind guard that reads the resulting empty
    stdout as 'no changes' returns 'E_GREEN_COMMIT_EMPTY' here instead --
    REJECTED. Pre-GREEN FAIL: the skip event does not exist in production at
    all today (no guard exists yet)."""
    from bytedigger_engine.lib import git_port as git_port_mod  # noqa: PLC0415

    repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_x.py", "def test_x():\n    assert False\n", "RED")
    _write_file(repo, "src/module.py", "def foo():\n    return 42\n")

    # Fixture precondition -- prove the floor is genuinely resolvable, for
    # real, BEFORE the seam is touched.
    verified = subprocess.run(
        ["git", "rev-parse", "--verify", f"{red_sha}^{{commit}}"],
        capture_output=True, text=True, cwd=repo,
    )
    assert verified.returncode == 0, (
        "fixture precondition FAILED: expected the real red_sha to verify "
        f"as a resolvable commit; actual rc={verified.returncode} "
        f"stderr={verified.stderr!r}"
    )

    real_port = git_port_mod.default_git_read()

    def _diff_rc128_port(args, *, cwd=None, timeout=None, dir_=None):
        if args and args[0] == "diff":
            return git_port_mod.GitResult(
                returncode=128, stdout="",
                stderr="fatal: injected rc=128 at the git-read seam (AC9 fixture)",
                timed_out=False,
            )
        return real_port(args, cwd=cwd, timeout=timeout, dir_=dir_)

    # Patch the module-private factory slot directly (monkeypatch restores
    # it automatically at teardown) -- every `git diff` reached through
    # `lib.git_port.git_read`, from ANY caller, is intercepted; `rev-parse`,
    # `add`, `commit`, `ls-files`, `check-ignore` all pass through for real.
    monkeypatch.setattr(git_port_mod, "_DEFAULT_FACTORY", lambda: _diff_rc128_port)

    # Out-of-scope-for-this-lot orthogonal gate (GH373/HAL_AUTHORED_BOUNDARY_GATE)
    # also issues its own `git diff --name-only <base_sha>` and RAISES
    # RuntimeError on any non-zero rc (lib/authored_boundary.py:217-225) --
    # with the seam injecting rc=128 on every diff, that raise would short
    # -circuit the step at E_BOUNDARY_SCAN_FAILED before ever reaching this
    # lot's guard. Disabled here so AC9 isolates the ONE seam this lot owns.
    monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "0")

    captured = _record_events(monkeypatch, p5)
    scratchpad = tmp_path / "scratch_ac9"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, repo)
    prev = _prev(red_sha)

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' (unchanged from today) -- an rc!=0/empty "
        f"diff at the read seam is NOT evidence of 'no changes' and must "
        f"never be read as a terminal verdict; actual {result.status!r} "
        f"error_code={result.error_code!r}: {getattr(result, 'error', '')!r}"
    )
    skip_events = [e for e in captured if e["type"] == "green_commit_nonempty_check_skipped"]
    assert len(skip_events) == 1, (
        f"expected exactly 1 'green_commit_nonempty_check_skipped' event; "
        f"actual events seen: {[e['type'] for e in captured]!r}"
    )
    assert skip_events[0]["payload"].get("reason"), (
        f"expected a non-empty 'reason' in the skip event payload; actual "
        f"{skip_events[0]['payload']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC6 -- E_GREEN_COMMIT_EMPTY is enumerated (§1n) and every real consumer
# that branches on phase-5 error codes treats it as terminal.
# ═════════════════════════════════════════════════════════════════════════


def test_ac6a_registered_in_error_codes_registry_and_markdown():
    """AC6 / §1n (two-direction): E_GREEN_COMMIT_EMPTY registered in
    error_codes.py's ERROR_CODES dict AND documented in ERROR_CODES.md.
    Pre-GREEN FAIL: neither exists yet -- ERROR_CODES is a manually curated
    dict (error_codes.py:28), not auto-harvested."""
    from bytedigger_engine import error_codes  # noqa: PLC0415

    assert "E_GREEN_COMMIT_EMPTY" in error_codes.ERROR_CODES, (
        f"expected 'E_GREEN_COMMIT_EMPTY' registered in error_codes.ERROR_CODES; "
        f"actual it is absent (registry has {len(error_codes.ERROR_CODES)} codes)"
    )
    engine_root = Path(__file__).resolve().parents[1]
    md_text = (engine_root / "bytedigger_engine/ERROR_CODES.md").read_text(encoding="utf-8")
    assert "E_GREEN_COMMIT_EMPTY" in md_text, (
        "expected 'E_GREEN_COMMIT_EMPTY' documented in ERROR_CODES.md; actual: absent"
    )


def test_ac6b_absent_from_dbos_evict_on_retry_codes():
    """AC6 consumer 1 (real, literal-keyed): lib.dbos_setup._EVICT_ON_RETRY_CODES
    -- a code that is a MEMBER of this frozenset is classified 'evict_code'
    (retried); E_GREEN_COMMIT_EMPTY must stay OUT of it so a stuck GREEN-empty
    DBOS row is classified 'failed_terminal', never silently re-queued for
    another wasted GREEN retry. Expected to PASS already (nothing adds the
    literal here without a deliberate GREEN edit) -- pinned as a regression
    guard against exactly that mistake."""
    from bytedigger_engine.lib.dbos_setup import _EVICT_ON_RETRY_CODES  # noqa: PLC0415

    assert "E_GREEN_COMMIT_EMPTY" not in _EVICT_ON_RETRY_CODES, (
        f"expected 'E_GREEN_COMMIT_EMPTY' absent from _EVICT_ON_RETRY_CODES "
        f"(terminal, never retried); actual members={sorted(_EVICT_ON_RETRY_CODES)!r}"
    )


def test_ac6c_classified_quality_gate_by_dispatcher_report():
    """AC6 consumer 2 (real, literal-keyed): lib.dispatcher_report.classify_error_code
    -- the 'GREEN_' prefix rule classifies any E_GREEN_* code 'quality-gate'.
    Pinned so the new sentinel does not silently fall through to 'unknown'.
    Expected to PASS already (the prefix rule is generic on the 'GREEN_'
    tail) -- a correctness guard for this specific new literal."""
    from bytedigger_engine.lib.dispatcher_report import classify_error_code  # noqa: PLC0415

    actual = classify_error_code("E_GREEN_COMMIT_EMPTY")
    assert actual == "quality-gate", (
        f"expected classify_error_code('E_GREEN_COMMIT_EMPTY')=='quality-gate'; "
        f"actual {actual!r}"
    )


def test_ac6d_engine_terminal_nonrecoverable_stuck_report_and_no_retry(tmp_path):
    """AC6 consumer 3 (real, generic dispatch on `.recoverable`):
    engine.WorkflowEngine -- a StepResult carrying error_code
    'E_GREEN_COMMIT_EMPTY' with recoverable=False must reach the terminal
    non-recoverable stuck-report path (breaker='terminal_failure', §GH1041
    2.3.2) and the step must be invoked exactly ONCE (no GREEN retry).
    Real production consumer, not mocked: engine.WorkflowEngine.execute.
    Mirrors test_gh1041_stuck_report.py AC10. Expected to PASS already
    (engine.py's terminal-failure dispatch is generic on `.recoverable`, not
    keyed to a specific error_code string) -- pinned as the AC2/AC5
    production return must actually reach this real dispatch when wired into
    a workflow, not merely satisfy an isolated unit assertion."""
    import json  # noqa: PLC0415

    from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition  # noqa: PLC0415
    from bytedigger_engine.engine import WorkflowEngine  # noqa: PLC0415
    from bytedigger_engine.event_log import EventLog  # noqa: PLC0415

    calls: list[int] = []

    def _exec(ctx, prev):
        calls.append(1)
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_green_code",
            error=(
                "GREEN commit landed with no in-scope change to src/module.py "
                "since red_commit_sha -- recover via `git stash list`"
            ),
            error_code="E_GREEN_COMMIT_EMPTY", recoverable=False,
        )

    workflow = WorkflowDefinition(
        name="wf_1612_ac6d", steps=[StepContract(name="commit_green_code", execute=_exec)]
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_1612_ac6d", workflow)
    ctx = _make_ctx(tmp_path / "scratch_ac6d", tmp_path)  # git_cwd unused by this fake step
    (tmp_path / "scratch_ac6d").mkdir(parents=True, exist_ok=True)

    eng.execute("wf_1612_ac6d", ctx, run_id="run-1612-ac6d")

    assert len(calls) == 1, (
        f"expected the step invoked exactly once (no GREEN retry on a "
        f"terminal E_GREEN_COMMIT_EMPTY); actual calls={len(calls)}"
    )
    report_path = tmp_path / "stuck-report.json"
    assert report_path.exists(), (
        "expected stuck-report.json emitted for a terminal E_GREEN_COMMIT_EMPTY"
    )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data.get("breaker") == "terminal_failure", (
        f"expected breaker=='terminal_failure'; actual {data.get('breaker')!r}"
    )
    assert data.get("error_code") == "E_GREEN_COMMIT_EMPTY", (
        f"expected error_code pinned in the stuck report; actual "
        f"{data.get('error_code')!r}"
    )
