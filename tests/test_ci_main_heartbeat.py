"""RED tests for the bd#12 fix: scripts/ci_main_heartbeat.py + the ci.yml
concurrency group + .github/workflows/ci-heartbeat.yml.

Neither the script nor ci-heartbeat.yml exists yet, and ci.yml's concurrency
group is still the wedging one. Per workflows.md §1q, nothing here imports or
resolves the units under test at module import time -- every test resolves
paths and subprocess-invokes lazily, inside the test body, so collection
always succeeds and failure happens at assert time.

The script is never imported: it is invoked as a real subprocess via
sys.executable, always with an EXPLICIT --runs-json (input is never inferred
from cwd) and an EXPLICIT --now (the 7-day window is never read off the wall
clock, so no test rots). Default subprocess cwd is a neutral empty tmp dir,
never the repo.

Repo root is the parent of this tests/ directory.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ci_main_heartbeat.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HEARTBEAT_YML = REPO_ROOT / ".github" / "workflows" / "ci-heartbeat.yml"
ARCHIVE = Path(__file__).resolve().parent / "fixtures" / "bd12" / "archive_runs.json"

# Neutral, empty, non-repo cwd for every subprocess invocation. No test needs
# the ambient cwd to be the repo root -- input always arrives via --runs-json.
# This prevents a GREEN that silently falls back to "runs.json in cwd" or to
# `gh api` from passing. realpath() strips the /var/folders symlink macOS puts
# under mkdtemp() output (§1j).
NEUTRAL_CWD = Path(os.path.realpath(tempfile.mkdtemp(prefix="bd12_heartbeat_neutral_")))

# Fixed clock for every synthetic case. The real archive was captured at
# 2026-07-27T12:58:00Z, so AC8 pins --now to that instant.
NOW = "2026-07-27T12:00:00Z"
ARCHIVE_NOW = "2026-07-27T12:58:00Z"


def _run(*args, cwd=NEUTRAL_CWD, env=None):
    """Invoke the script as a real subprocess. Never imports/mocks the UUT."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
        env=env,
    )


def _entry(**overrides):
    """One workflow-run record, in the exact shape of the captured archive
    (flat `jobs_total_count`, as `gh api .../runs` is projected there).
    Default is an EXECUTED ci run on main inside the window."""
    base = {
        "id": 1,
        "name": "ci",
        "path": ".github/workflows/ci.yml",
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-07-27T06:00:00Z",
        "jobs_total_count": 3,
    }
    base.update(overrides)
    return base


def _runs_json(tmp_path: Path, runs, name="runs.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "workflow": "ci",
                "captured_at": NOW,
                "note": "synthetic bd#12 test fixture",
                "workflow_runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    return path


def _out(result) -> str:
    return (result.stdout or "") + (result.stderr or "")


# --------------------------------------------------------------------------
# Minimal GitHub-Actions expression evaluator (AC11/AC12).
#
# The workflow ACs are about SEMANTICS, not text: "does this group isolate
# each main commit while still collapsing off-main pushes". So the group
# template is actually evaluated against synthetic (ref, sha) contexts rather
# than regex-matched. Only the operator subset the fix idiom needs is
# supported; anything else raises, with a message telling GREEN to stay
# inside that subset instead of failing opaquely.
# --------------------------------------------------------------------------
class _ExprUnsupported(Exception):
    pass


_TOKEN_RE = re.compile(r"'(?:[^']|'')*'|==|!=|&&|\|\||[(),]|[A-Za-z_][A-Za-z0-9_.]*|\S")


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != ""
    return bool(value)


class _Expr:
    def __init__(self, tokens, ctx):
        self.tokens = tokens
        self.i = 0
        self.ctx = ctx

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        value = self.or_expr()
        if self.peek() is not None:
            raise _ExprUnsupported(f"trailing token {self.peek()!r}")
        return value

    def or_expr(self):
        value = self.and_expr()
        while self.peek() == "||":
            self.take()
            right = self.and_expr()
            value = value if _truthy(value) else right
        return value

    def and_expr(self):
        value = self.cmp_expr()
        while self.peek() == "&&":
            self.take()
            right = self.cmp_expr()
            value = right if _truthy(value) else value
        return value

    def cmp_expr(self):
        left = self.primary()
        while self.peek() in ("==", "!="):
            op = self.take()
            right = self.primary()
            left = (left == right) if op == "==" else (left != right)
        return left

    def primary(self):
        tok = self.take()
        if tok is None:
            raise _ExprUnsupported("unexpected end of expression")
        if tok == "(":
            value = self.or_expr()
            if self.take() != ")":
                raise _ExprUnsupported("unbalanced parenthesis")
            return value
        if tok.startswith("'"):
            return tok[1:-1].replace("''", "'")
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok == "format":
            if self.take() != "(":
                raise _ExprUnsupported("format without argument list")
            args = [self.or_expr()]
            while self.peek() == ",":
                self.take()
                args.append(self.or_expr())
            if self.take() != ")":
                raise _ExprUnsupported("unbalanced format() argument list")
            rendered = args[0]
            for idx, arg in enumerate(args[1:]):
                rendered = rendered.replace("{%d}" % idx, _as_str(arg))
            return rendered
        if tok in self.ctx:
            return self.ctx[tok]
        raise _ExprUnsupported(
            f"unsupported token {tok!r}; the bd#12 concurrency idiom is expected to use "
            "only github.ref / github.sha, string literals, ==, !=, &&, ||, format()"
        )


def _as_str(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _eval_expr(expr: str, ctx: dict):
    tokens = _TOKEN_RE.findall(expr)
    for tok in tokens:
        if len(tok) == 1 and tok not in "(),":
            raise _ExprUnsupported(f"unsupported character {tok!r} in {expr!r}")
    return _Expr(tokens, ctx).parse()


def _render(template, ref: str, sha: str):
    """Render a workflow scalar (`${{ ... }}` template or plain literal)
    against a synthetic github context."""
    if isinstance(template, bool):
        return template
    template = str(template)
    ctx = {"github.ref": ref, "github.sha": sha}
    whole = re.fullmatch(r"\s*\$\{\{(.*)\}\}\s*", template, flags=re.S)
    if whole:
        return _eval_expr(whole.group(1).strip(), ctx)
    return re.sub(
        r"\$\{\{(.*?)\}\}",
        lambda m: _as_str(_eval_expr(m.group(1).strip(), ctx)),
        template,
        flags=re.S,
    )


def _load_workflow(path: Path) -> dict:
    assert path.exists(), f"{path} does not exist"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path} is not a YAML mapping"
    return data


def _on_block(data: dict):
    """PyYAML parses the workflow key `on:` as the boolean True (YAML 1.1
    truthy). Accept either spelling."""
    if "on" in data:
        return data["on"]
    return data.get(True)


class TestCiMainHeartbeat:
    # ---- the script ------------------------------------------------------

    def test_ac1_script_exists_is_executable_and_help_exits_zero(self):
        """AC1: scripts/ci_main_heartbeat.py exists, is executable, --help exits 0."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist"
        assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable (chmod +x)"
        result = _run("--help")
        assert result.returncode == 0, (
            f"--help must exit 0, got {result.returncode}; stderr={result.stderr!r}"
        )

    def test_ac2_days_window_filters_by_created_at(self, tmp_path):
        """AC2: --days N filters by run created_at; a run older than the window
        does not satisfy the check."""
        runs = _runs_json(tmp_path, [_entry(created_at="2026-07-01T06:00:00Z")])
        stale = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert stale.returncode == 1, (
            "an executed main run 26 days before --now must NOT satisfy a 7-day "
            f"window; got exit {stale.returncode}, output={_out(stale)!r}"
        )
        widened = _run("--runs-json", str(runs), "--now", NOW, "--days", "30")
        assert widened.returncode == 0, (
            "the same run inside a 30-day window must satisfy the check; got exit "
            f"{widened.returncode}, output={_out(widened)!r}"
        )

    def test_ac3_zero_jobs_is_not_executed(self, tmp_path):
        """AC3: a run with jobs.total_count == 0 is NOT executed → exit 1."""
        runs = _runs_json(tmp_path, [_entry(jobs_total_count=0)])
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 1, (
            "a completed/success main run that created 0 jobs must not count as "
            f"executed; got exit {result.returncode}, output={_out(result)!r}"
        )

    def test_ac4_cancelled_is_not_executed_even_with_jobs(self, tmp_path):
        """AC4: a run with conclusion == "cancelled" is NOT executed → exit 1
        even when jobs.total_count > 0."""
        runs = _runs_json(
            tmp_path, [_entry(conclusion="cancelled", jobs_total_count=3)]
        )
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 1, (
            "a cancelled main run with 3 jobs must not count as executed; got exit "
            f"{result.returncode}, output={_out(result)!r}"
        )

    def test_ac4b_unfinished_run_with_jobs_is_not_executed(self, tmp_path):
        """AC4b (the measured cause): a run with status queued/pending/in_progress,
        conclusion null and jobs.total_count > 0 is NOT executed → exit 1.
        Jobs created ≠ jobs run."""
        for status in ("queued", "pending", "in_progress"):
            runs = _runs_json(
                tmp_path,
                [_entry(id=30241562817, status=status, conclusion=None, jobs_total_count=3)],
                name=f"runs_{status}.json",
            )
            result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
            assert result.returncode == 1, (
                f"the wedged-run shape (status={status}, conclusion=null, "
                "jobs_total_count=3) is exactly what bd#12 looked like and must NOT "
                f"read as healthy; got exit {result.returncode}, output={_out(result)!r}"
            )

    def test_ac5_completed_success_or_failure_with_jobs_is_executed(self, tmp_path):
        """AC5: status completed + jobs.total_count > 0 + conclusion success → exit 0;
        same with conclusion failure → exit 0 (a red main is a signal; silence is not).
        timed_out likewise, per the executed predicate."""
        for conclusion in ("success", "failure", "timed_out"):
            runs = _runs_json(
                tmp_path,
                [_entry(conclusion=conclusion)],
                name=f"runs_{conclusion}.json",
            )
            result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
            assert result.returncode == 0, (
                f"a completed main run with 3 jobs and conclusion={conclusion} must "
                f"satisfy the check; got exit {result.returncode}, "
                f"output={_out(result)!r}"
            )

    def test_ac6_executed_run_on_other_branch_does_not_satisfy(self, tmp_path):
        """AC6: an executed run on a branch other than main does not satisfy → exit 1."""
        runs = _runs_json(
            tmp_path,
            [
                _entry(id=2, head_branch="ci/ubuntu-cleanroom", event="pull_request"),
                _entry(id=3, head_branch="fix/bd12-ci-main-wedge", event="pull_request"),
            ],
        )
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 1, (
            "green PR runs are exactly what masked bd#12 -- they must not satisfy a "
            f"main-liveness check; got exit {result.returncode}, output={_out(result)!r}"
        )

    def test_ac7_executed_run_of_different_workflow_does_not_satisfy(self, tmp_path):
        """AC7: an executed run of a different workflow (clean-room) does not
        satisfy → exit 1."""
        runs = _runs_json(
            tmp_path,
            [
                _entry(
                    id=4,
                    name="clean-room",
                    path=".github/workflows/clean-room.yml",
                )
            ],
        )
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 1, (
            "clean-room declares its own concurrency group and stayed healthy through "
            "bd#12; its runs must not vouch for ci.yml on main; got exit "
            f"{result.returncode}, output={_out(result)!r}"
        )

    def test_ac8_real_bd12_archive_reports_unhealthy(self):
        """AC8 (real-side-effect anchor, §1l): fed the captured bd#12 archive --
        the five cancelled main runs, the queued 30241562817, and the green PR
        runs -- the check exits 1 and names main and the window in its report."""
        assert ARCHIVE.exists(), f"committed bd#12 archive missing at {ARCHIVE}"
        result = _run("--runs-json", str(ARCHIVE), "--now", ARCHIVE_NOW, "--days", "7")
        assert result.returncode == 1, (
            "the real pre-intervention bd#12 snapshot (7 main runs, 0 executed) must "
            f"report unhealthy; got exit {result.returncode}, output={_out(result)!r}"
        )
        report = _out(result)
        assert "main" in report, f"report must name the branch main: {report!r}"
        assert re.search(r"\b7\b", report), (
            f"report must name the window it checked (7 days): {report!r}"
        )

    def test_ac9_usage_errors_exit_two(self, tmp_path):
        """AC9: usage errors (unknown flag, unreadable/malformed --runs-json) exit 2,
        distinct from the 1 used for "no executed run"."""
        good = _runs_json(tmp_path, [_entry()])
        unknown = _run(
            "--runs-json", str(good), "--now", NOW, "--days", "7", "--bogus-flag"
        )
        assert unknown.returncode == 2, (
            f"unknown flag must exit 2, got {unknown.returncode}: {_out(unknown)!r}"
        )

        missing = tmp_path / "nope.json"
        unreadable = _run("--runs-json", str(missing), "--now", NOW, "--days", "7")
        assert unreadable.returncode == 2, (
            f"unreadable --runs-json must exit 2, got {unreadable.returncode}: "
            f"{_out(unreadable)!r}"
        )

        bad = tmp_path / "malformed.json"
        bad.write_text('{"workflow_runs": [ {"id": 1,, ]')
        malformed = _run("--runs-json", str(bad), "--now", NOW, "--days", "7")
        assert malformed.returncode == 2, (
            f"malformed --runs-json must exit 2, got {malformed.returncode}: "
            f"{_out(malformed)!r}"
        )

    def test_ac10_fixture_mode_makes_no_network_or_gh_call(self, tmp_path):
        """AC10: fixture mode performs no network call and no gh invocation."""
        runs = _runs_json(tmp_path, [_entry()])

        # PATH is reduced to a single dir holding a `gh` shim that records the
        # call and fails. If the script shells out to gh in fixture mode, the
        # marker appears (and/or the run breaks); if it honours --runs-json, the
        # shim is never touched and the healthy fixture still yields exit 0.
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        marker = tmp_path / "gh_was_called"
        gh = fake_bin / "gh"
        gh.write_text(f'#!/bin/sh\necho "$@" >> "{marker}"\nexit 1\n')
        gh.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = str(fake_bin)
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
        env["http_proxy"] = env["https_proxy"] = "http://127.0.0.1:9"
        env["ALL_PROXY"] = env["all_proxy"] = "http://127.0.0.1:9"
        env["NO_PROXY"] = env["no_proxy"] = ""
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)

        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7", env=env)
        assert not marker.exists(), (
            f"fixture mode invoked gh: {marker.read_text()!r}"
        )
        assert result.returncode == 0, (
            "fixture mode must be fully offline -- with gh shimmed to fail and every "
            "proxy pointed at a dead port, the healthy fixture must still exit 0; got "
            f"exit {result.returncode}, output={_out(result)!r}"
        )

    # ---- the workflows ---------------------------------------------------

    def test_ac11_ci_concurrency_isolates_main_per_commit(self):
        """AC11: .github/workflows/ci.yml declares a concurrency group that includes
        github.sha on main and still cancels in progress off main."""
        data = _load_workflow(CI_YML)
        concurrency = data.get("concurrency")
        assert isinstance(concurrency, dict), (
            f"ci.yml must keep a top-level concurrency mapping, got {concurrency!r}"
        )
        group = concurrency.get("group")
        cancel = concurrency.get("cancel-in-progress")
        assert group is not None, "ci.yml concurrency has no group"

        try:
            main_a = _render(group, "refs/heads/main", "aaaaaaa")
            main_b = _render(group, "refs/heads/main", "bbbbbbb")
            branch_a = _render(group, "refs/heads/fix/bd12", "aaaaaaa")
            branch_b = _render(group, "refs/heads/fix/bd12", "bbbbbbb")
            cancel_main = _render(cancel, "refs/heads/main", "aaaaaaa")
            cancel_branch = _render(cancel, "refs/heads/fix/bd12", "aaaaaaa")
        except _ExprUnsupported as exc:  # pragma: no cover - diagnostic path
            pytest.fail(f"ci.yml concurrency expression not evaluable: {exc}")

        assert "aaaaaaa" in _as_str(main_a), (
            "on main the concurrency group must include github.sha, so a permanently "
            f"unschedulable run can block only its own commit; got {main_a!r}"
        )
        assert main_a != main_b, (
            "two different main commits must land in DIFFERENT concurrency groups -- "
            f"this is the bd#12 head-of-line wedge; both rendered {main_a!r}"
        )
        assert branch_a == branch_b, (
            "off main the group must stay per-ref (unchanged behaviour), got "
            f"{branch_a!r} vs {branch_b!r}"
        )
        assert not _truthy(cancel_main), (
            f"main must NOT cancel in progress, got cancel-in-progress={cancel_main!r}"
        )
        assert _truthy(cancel_branch), (
            "off main cancel-in-progress must stay true, got "
            f"{cancel_branch!r}"
        )

    def test_ac12_heartbeat_workflow_is_scheduled_hosted_and_isolated(self):
        """AC12: .github/workflows/ci-heartbeat.yml exists, has a schedule cron, runs
        on ubuntu-latest, invokes the script, and its concurrency group is not the
        ci- group."""
        data = _load_workflow(HEARTBEAT_YML)

        triggers = _on_block(data)
        assert isinstance(triggers, dict) and "schedule" in triggers, (
            f"ci-heartbeat.yml must declare a schedule: trigger, got {triggers!r}"
        )
        crons = [
            item.get("cron")
            for item in triggers["schedule"]
            if isinstance(item, dict) and item.get("cron")
        ]
        assert crons, f"schedule: must carry at least one cron, got {triggers['schedule']!r}"

        jobs = data.get("jobs")
        assert isinstance(jobs, dict) and jobs, "ci-heartbeat.yml declares no jobs"
        runners = {_as_str(job.get("runs-on")) for job in jobs.values() if isinstance(job, dict)}
        assert "ubuntu-latest" in runners, (
            "the heartbeat must run on stock hosted infra -- self-hosted labels with 0 "
            f"registered runners are the bd#12 mechanism; got runs-on {runners!r}"
        )

        steps_text = yaml.safe_dump(jobs)
        assert "ci_main_heartbeat.py" in steps_text, (
            "ci-heartbeat.yml must actually invoke scripts/ci_main_heartbeat.py"
        )

        concurrency = data.get("concurrency")
        assert isinstance(concurrency, dict), (
            f"ci-heartbeat.yml must declare its own concurrency mapping, got {concurrency!r}"
        )
        group = _as_str(concurrency.get("group"))
        assert not group.startswith("ci-"), (
            "the heartbeat must never share the ci- group family it is watching, got "
            f"{group!r}"
        )
        rendered = _as_str(_render(concurrency.get("group"), "refs/heads/main", "aaaaaaa"))
        assert not rendered.startswith("ci-"), (
            f"heartbeat group renders into the ci- family on main: {rendered!r}"
        )
