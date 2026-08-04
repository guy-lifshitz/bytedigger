"""RED-phase tests — GH1124 (6C2F1681): ship_pr derives PR title/body from phase-7
synthesizer artifact instead of raw commit subject/trail.

16 ACs from spec §"AC Table". All tests are expected to FAIL today because:
- `_parse_report_summary`, `_parse_issue_from_branch`, `_issue_type_prefix`,
  `_compose_pr_title` don't exist yet in phase_8_post_deploy.py (ImportError).
- `_ship_to_pr`'s title/body derivation still uses only commit-subject / commit-trail
  (AC13/AC15/AC16 assert on the NEW report-derived title/body which the current
  production code cannot produce).

Per §1q (D1CF5FDF extension), imports of the four not-yet-existing helpers are
deferred INSIDE each test body so the module collects cleanly and each test fails
at assert/call time (ImportError), not at collection time.

Fake-gh mechanism copied/extended from sibling test_phase_8_ship_to_pr_98364258.py:
a tiny shell script recording argv to gh-argv.log, dispatching on subcommand.
This file's local `write_fake_gh` additionally handles `issue view <n> --json labels
-q ...` (needed for AC10-AC13), which the sibling helper does not support. The
sibling file is left untouched per spec §5 "Files NOT in scope".
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

# Imports rely on conftest-import-time sys.path setup (§1q / 81F97F3D).
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_8_post_deploy import _ship_to_pr  # noqa: E402  (exists today)


# ─── helpers (copied from test_phase_8_ship_to_pr_98364258.py; local copy per §5) ──


def make_ctx(scratchpad: Path, *, working_dir: Path | None = None, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    if working_dir is not None:
        org["working_dir"] = str(working_dir)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="ship",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def init_git_repo(path: Path) -> None:
    """Init a real git repo with one commit on main, isolated config."""
    path.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(path), check=True, env=env)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(path), check=True, env=env)
    (path / "README").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True, env=env)


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def make_feature_branch_with_commit(
    repo: Path, branch: str = "feat/my-feature", commit_msg: str = "feat: add feature"
) -> None:
    """Create a feature branch with one commit ahead of main."""
    git(["checkout", "-b", branch], repo)
    (repo / "feature.txt").write_text("feature\n")
    git(["add", "."], repo)
    git(["commit", "-q", "-m", commit_msg], repo)


def push_to_bare_origin(repo: Path, tmp_path: Path, branch: str) -> None:
    origin = tmp_path / "origin.git"
    if not origin.exists():
        subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True,
                        capture_output=True)
        git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", branch], repo)


def write_fake_gh(
    tmp_path: Path,
    *,
    pr_list_json: str = "[]",
    pr_create_rc: int = 0,
    pr_create_output: str = "https://github.com/o/r/pull/7",
    issue_view_labels: str = "",
    issue_view_rc: int = 0,
) -> Path:
    """Write a fake `gh` shell script to tmp_path/fake-gh.

    Extends the sibling helper with an `issue view <n> --json labels -q ...` branch
    (needed by `_issue_type_prefix`, which the sibling test file's SUT-under-test
    coverage never exercised).

    Records all argv to gh-argv.log (one invocation per line, tab-separated).
    """
    log = tmp_path / "gh-argv.log"
    script = tmp_path / "fake-gh"
    script_body = f"""\
#!/bin/sh
printf '%s\\t' "$@" >> "{log}"
printf '\\n' >> "{log}"
case "$1 $2" in
  "pr list")
    printf '%s\\n' '{pr_list_json}'
    exit 0
    ;;
  "pr create")
    printf '%s\\0' "$@" > "{tmp_path}/pr-create-argv.nul"
    printf '%s\\n' '{pr_create_output}'
    exit {pr_create_rc}
    ;;
  "issue view")
    printf '%s\\n' '{issue_view_labels}'
    exit {issue_view_rc}
    ;;
  *)
    exit 0
    ;;
esac
"""
    script.write_text(script_body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def gh_argv_log(tmp_path: Path) -> list[list[str]]:
    """Parse gh-argv.log → list of invocations, each a list of args."""
    log = tmp_path / "gh-argv.log"
    if not log.exists():
        return []
    lines = log.read_text().splitlines()
    result = []
    for line in lines:
        if line.strip():
            result.append([a for a in line.split("\t") if a])
    return result


def pr_create_argv(tmp_path: Path) -> list[str]:
    """Read the NUL-separated full argv of the (single) `pr create` invocation.

    `gh_argv_log` splits on physical newlines, which corrupts multi-line
    `--body` values (the synthesizer report contains embedded newlines).
    This reads a dedicated NUL-separated dump written by the fake-gh script
    on `pr create` so multi-line values round-trip exactly.
    """
    nul_file = tmp_path / "pr-create-argv.nul"
    if not nul_file.exists():
        return []
    raw = nul_file.read_bytes().decode()
    parts = raw.split("\0")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def write_report(scratchpad: Path, done_line: str | None, extra_text: str = "") -> Path:
    """Write a post-deploy synthesizer report with an optional `Done:` line."""
    post_deploy_dir = scratchpad / "post-deploy"
    post_deploy_dir.mkdir(parents=True, exist_ok=True)
    report = post_deploy_dir / "post-deploy-report.md"
    lines = ["# Post-deploy report", ""]
    if extra_text:
        lines.append(extra_text)
    lines.append("## Final Checkpoint")
    if done_line is not None:
        lines.append(f"Done: {done_line}")
    report.write_text("\n".join(lines) + "\n")
    return report


# ─── AC1-AC3: _parse_report_summary ─────────────────────────────────────────────


def test_ac1_parse_report_summary_extracts_done_line():
    """AC1: report with '## Final Checkpoint'\\n'Done: close fail-open cluster' →
    returns 'close fail-open cluster'."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_report_summary  # deferred: absent pre-GREEN

    report_text = "## Final Checkpoint\nDone: close fail-open cluster\n"
    assert _parse_report_summary(report_text) == "close fail-open cluster"


def test_ac2_parse_report_summary_no_done_line_returns_empty():
    """AC2: report text with no 'Done:' line → returns ''."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_report_summary  # deferred: absent pre-GREEN

    report_text = "## Final Checkpoint\nNothing here.\nSome other line.\n"
    assert _parse_report_summary(report_text) == ""


def test_ac3_parse_report_summary_truncates_to_100_chars():
    """AC3: 'Done:' value 140 chars → returns first 100 chars."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_report_summary  # deferred: absent pre-GREEN

    long_summary = "x" * 140
    report_text = f"## Final Checkpoint\nDone: {long_summary}\n"
    result = _parse_report_summary(report_text)
    assert result == long_summary[:100]
    assert len(result) == 100


# ─── AC4-AC6: _parse_issue_from_branch ──────────────────────────────────────────


def test_ac4_parse_issue_from_branch_batch_prefix():
    """AC4: branch 'batch/1378' → returns 1378."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_issue_from_branch  # deferred: absent pre-GREEN

    assert _parse_issue_from_branch("batch/1378") == 1378


def test_ac5_parse_issue_from_branch_gh_prefix():
    """AC5: branch 'gh1124-ship-pr-title' → returns 1124."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_issue_from_branch  # deferred: absent pre-GREEN

    assert _parse_issue_from_branch("gh1124-ship-pr-title") == 1124


def test_ac6_parse_issue_from_branch_no_match_returns_none():
    """AC6: branch 'feat/my-feature' → returns None."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_issue_from_branch  # deferred: absent pre-GREEN

    assert _parse_issue_from_branch("feat/my-feature") is None


# ─── AC7-AC9: _compose_pr_title ─────────────────────────────────────────────────


def test_ac7_compose_pr_title_wraps_with_issue_and_prefix():
    """AC7: summary='x', issue_n=1124, prefix='fix' → returns 'fix(#1124): x'."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _compose_pr_title  # deferred: absent pre-GREEN

    assert _compose_pr_title("x", 1124, "fix") == "fix(#1124): x"


def test_ac8_compose_pr_title_no_issue_returns_summary_only():
    """AC8: summary='x', issue_n=None, prefix='fix' → returns 'x'."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _compose_pr_title  # deferred: absent pre-GREEN

    assert _compose_pr_title("x", None, "fix") == "x"


def test_ac9_compose_pr_title_empty_summary_returns_empty():
    """AC9: summary='', issue_n=1124 → returns ''."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _compose_pr_title  # deferred: absent pre-GREEN

    assert _compose_pr_title("", 1124, "fix") == ""


# ─── AC10-AC12: _issue_type_prefix ──────────────────────────────────────────────


def test_ac10_issue_type_prefix_bug_label_returns_fix(tmp_path, monkeypatch):
    """AC10: fake gh 'issue view' → labels 'bug,tier/micro', issue_n=1124 →
    returns 'fix'."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _issue_type_prefix  # deferred: absent pre-GREEN

    fake_gh = write_fake_gh(tmp_path, issue_view_labels="bug,tier/micro", issue_view_rc=0)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    assert _issue_type_prefix(tmp_path, 1124) == "fix"
    invocations = gh_argv_log(tmp_path)
    view_calls = [inv for inv in invocations if "view" in inv]
    assert len(view_calls) >= 1, f"Expected 'issue view' invocation, got: {invocations}"


def test_ac11_issue_type_prefix_enhancement_label_returns_feat(tmp_path, monkeypatch):
    """AC11: fake gh 'issue view' → labels 'enhancement', issue_n=1124 →
    returns 'feat'."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _issue_type_prefix  # deferred: absent pre-GREEN

    fake_gh = write_fake_gh(tmp_path, issue_view_labels="enhancement", issue_view_rc=0)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    assert _issue_type_prefix(tmp_path, 1124) == "feat"


def test_ac12_issue_type_prefix_gh_failure_returns_fix_never_raises(tmp_path, monkeypatch):
    """AC12: fake gh 'issue view' → rc!=0, issue_n=1124 → returns 'fix' (never
    raises)."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _issue_type_prefix  # deferred: absent pre-GREEN

    fake_gh = write_fake_gh(tmp_path, issue_view_labels="", issue_view_rc=1)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    result = _issue_type_prefix(tmp_path, 1124)
    assert result == "fix"


# ─── AC13-AC16: _ship_to_pr full path ───────────────────────────────────────────


def test_ac13_ship_to_pr_title_uses_report_summary_and_issue_labels(tmp_path, monkeypatch):
    """AC13: ship_pr=1, branch 'gh1124-x', report with 'Done: my summary', fake gh
    issue-view → 'bug' → pr create argv '--title' == 'fix(#1124): my summary'."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "gh1124-x", commit_msg="build: auto-commit tail")
    push_to_bare_origin(repo, tmp_path, "gh1124-x")

    scratchpad = tmp_path / "scratch"
    write_report(scratchpad, "my summary")

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/1124",
        issue_view_labels="bug",
        issue_view_rc=0,
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)
    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r} / {result.error_code!r}"
    invocations = gh_argv_log(tmp_path)
    create_calls = [inv for inv in invocations if "create" in inv]
    assert len(create_calls) >= 1, f"Expected pr create in argv log, got: {invocations}"
    create_argv = pr_create_argv(tmp_path)
    assert "--title" in create_argv, f"Expected --title in argv: {create_argv}"
    title_idx = create_argv.index("--title")
    title = create_argv[title_idx + 1]
    assert title == "fix(#1124): my summary", (
        f"Expected title 'fix(#1124): my summary', got {title!r}; full argv: {create_argv}"
    )


def test_ac14_ship_to_pr_title_falls_back_to_commit_subject_when_no_report(tmp_path, monkeypatch):
    """AC14: ship_pr=1, branch 'feat/my-feature', NO report dir, commit subject
    'feat: add feature' → '--title' == 'feat: add feature' (legacy fallback,
    byte-identical)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/my-feature", commit_msg="feat: add feature")
    push_to_bare_origin(repo, tmp_path, "feat/my-feature")

    scratchpad = tmp_path / "scratch"
    # Deliberately NOT calling write_report — no post-deploy report exists.

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/2",
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)
    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r} / {result.error_code!r}"
    invocations = gh_argv_log(tmp_path)
    create_calls = [inv for inv in invocations if "create" in inv]
    assert len(create_calls) >= 1, f"Expected pr create in argv log, got: {invocations}"
    create_argv = pr_create_argv(tmp_path)
    assert "--title" in create_argv, f"Expected --title in argv: {create_argv}"
    title_idx = create_argv.index("--title")
    title = create_argv[title_idx + 1]
    assert title == "feat: add feature", (
        f"Expected legacy-fallback title 'feat: add feature', got {title!r}; full argv: {create_argv}"
    )


def test_ac15_ship_to_pr_title_org_config_override_wins(tmp_path, monkeypatch):
    """AC15: ship_pr=1, report with 'Done: X', ship_pr_title='OVERRIDE' in
    org_config → '--title' == 'OVERRIDE' (override wins)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "gh1124-y", commit_msg="build: auto-commit tail")
    push_to_bare_origin(repo, tmp_path, "gh1124-y")

    scratchpad = tmp_path / "scratch"
    write_report(scratchpad, "X")

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/3",
        issue_view_labels="bug",
        issue_view_rc=0,
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True, ship_pr_title="OVERRIDE")
    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r} / {result.error_code!r}"
    invocations = gh_argv_log(tmp_path)
    create_calls = [inv for inv in invocations if "create" in inv]
    assert len(create_calls) >= 1, f"Expected pr create in argv log, got: {invocations}"
    create_argv = pr_create_argv(tmp_path)
    assert "--title" in create_argv, f"Expected --title in argv: {create_argv}"
    title_idx = create_argv.index("--title")
    title = create_argv[title_idx + 1]
    assert title == "OVERRIDE", (
        f"Expected override title 'OVERRIDE', got {title!r}; full argv: {create_argv}"
    )


def test_ac16_ship_to_pr_body_uses_report_text_stripped(tmp_path, monkeypatch):
    """AC16: ship_pr=1, report present with body content → pr create '--body' ==
    report text (stripped)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "gh1124-z", commit_msg="build: auto-commit tail")
    push_to_bare_origin(repo, tmp_path, "gh1124-z")

    scratchpad = tmp_path / "scratch"
    report_path = write_report(scratchpad, "some summary", extra_text="Some intro paragraph.")
    expected_body = report_path.read_text(encoding="utf-8").strip()

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/4",
        issue_view_labels="bug",
        issue_view_rc=0,
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)
    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r} / {result.error_code!r}"
    invocations = gh_argv_log(tmp_path)
    create_calls = [inv for inv in invocations if "create" in inv]
    assert len(create_calls) >= 1, f"Expected pr create in argv log, got: {invocations}"
    create_argv = pr_create_argv(tmp_path)
    assert "--body" in create_argv, f"Expected --body in argv: {create_argv}"
    body_idx = create_argv.index("--body")
    body = create_argv[body_idx + 1]
    assert body == expected_body, (
        f"Expected body to equal stripped report text, got {body!r}"
    )
