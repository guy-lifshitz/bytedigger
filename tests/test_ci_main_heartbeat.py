"""RED tests for the bd#12 fix: scripts/ci_main_heartbeat.py + the ci.yml
concurrency group + .github/workflows/ci-heartbeat.yml.

Neither the script nor ci-heartbeat.yml exists yet, and ci.yml's concurrency
group is still the wedging one. Per workflows.md §1q, nothing here imports or
resolves the units under test at module import time -- every test resolves
paths and subprocess-invokes lazily, inside the test body, so collection
always succeeds and failure happens at assert time.

The script is never imported: it is invoked as a real subprocess via
sys.executable. Fixture-mode tests always pass an EXPLICIT --runs-json (input
is never inferred from cwd); live-mode tests (AC13/AC14/AC14b) deliberately omit it
and instead put a `gh` shim first on PATH -- that stubs an external binary,
not the unit. Every invocation passes an EXPLICIT --now, so the 7-day window is
never read off the wall clock and no test rots. Default subprocess cwd is a
neutral empty tmp dir, never the repo.

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


def _raw_json(tmp_path: Path, payload, name="raw.json") -> Path:
    """A shape-invalid (but syntactically valid) document, written verbatim."""
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _out(result) -> str:
    return (result.stdout or "") + (result.stderr or "")


def _gh_shim(tmp_path: Path, body: str, tag: str):
    """Install a `gh` shim FIRST on PATH and point every proxy at a dead port.

    Stubs an external binary, never the unit under test. Returns (env, argv_log);
    argv_log accumulates one line per gh invocation, so a test can assert what
    the script actually asked GitHub for.
    """
    fake_bin = tmp_path / f"fakebin_{tag}"
    fake_bin.mkdir()
    argv_log = tmp_path / f"gh_argv_{tag}"
    gh = fake_bin / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> "{argv_log}"\n'
        f"{body}\n"
    )
    gh.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    for var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        env[var] = "http://127.0.0.1:9"
    env["NO_PROXY"] = env["no_proxy"] = ""
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return env, argv_log


def _extract_job_block(text: str, job_name: str) -> str:
    """The raw text of one top-level job block (house idiom, copied from
    tests/test_version_parity.py so AC15 reads like its precedent)."""
    m = re.search(rf"(?m)^  {re.escape(job_name)}:\s*\n", text)
    assert m, f"no top-level '{job_name}:' job in {text[:40]!r}..."
    start = m.end()
    m2 = re.search(r"(?m)^  \S", text[start:])
    end = start + m2.start() if m2 else len(text)
    return text[start:end]


def _runs_on_labels(value) -> set:
    """`runs-on` is legal as a scalar (`ubuntu-latest`) or as a list
    (`[ubuntu-latest]`). Normalise both to a set of labels before comparing, so
    a correct workflow is not failed on its YAML spelling."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple)):
        return {_as_str(item) for item in value}
    return {_as_str(value)}


def _fires_at_least_daily(cron: str) -> bool:
    """A 5-field cron fires at least once per day iff day-of-month, month and
    day-of-week are all unrestricted. `0 0 1 1 *` (annually) is not."""
    fields = str(cron).split()
    if len(fields) != 5:
        return False
    day_of_month, month, day_of_week = fields[2], fields[3], fields[4]
    return day_of_month == "*" and month == "*" and day_of_week in ("*", "?")


# --------------------------------------------------------------------------
# Minimal GitHub-Actions expression evaluator (AC11/AC12/AC23).
#
# The workflow ACs are about SEMANTICS, not text: "does this group isolate
# each main commit while still collapsing off-main pushes". So the group
# template is actually evaluated against synthetic (ref, sha, run_id) contexts
# rather than regex-matched. Only the operator subset the fix idiom needs is
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
            "only github.ref / github.sha / github.run_id / github.workflow, string "
            "literals, ==, !=, &&, ||, format()"
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


def _render(template, ref: str, sha: str, run_id: str = "1000", workflow: str = "ci"):
    """Render a workflow scalar (`${{ ... }}` template or plain literal)
    against a synthetic github context. `None` (an absent key) renders to None,
    never to the truthy string "None"."""
    if template is None:
        return None
    if isinstance(template, bool):
        return template
    template = str(template)
    ctx = {
        "github.ref": ref,
        "github.sha": sha,
        "github.run_id": run_id,
        "github.run_number": run_id,
        "github.workflow": workflow,
    }
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

    def test_ac2b_now_is_honoured_not_the_wall_clock(self, tmp_path):
        """AC2b: --now is honoured, not the wall clock: a run at
        2029-12-31T00:00:00Z with --now 2030-01-01T00:00:00Z --days 7 exits 0."""
        runs = _runs_json(tmp_path, [_entry(created_at="2029-12-31T00:00:00Z")])
        result = _run(
            "--runs-json", str(runs), "--now", "2030-01-01T00:00:00Z", "--days", "7"
        )
        assert result.returncode == 0, (
            "--now must define the window's right edge; a wall-clock implementation "
            "sees this run as years in the future and reports unhealthy; got exit "
            f"{result.returncode}, output={_out(result)!r}"
        )

    def test_ac3_zero_jobs_is_not_executed(self, tmp_path):
        """AC3: a run with jobs_total_count == 0 is NOT executed → exit 1."""
        runs = _runs_json(tmp_path, [_entry(jobs_total_count=0)])
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 1, (
            "a completed/success main run that created 0 jobs must not count as "
            f"executed; got exit {result.returncode}, output={_out(result)!r}"
        )

    def test_ac4_cancelled_is_not_executed_even_with_jobs(self, tmp_path):
        """AC4: a run with conclusion == "cancelled" is NOT executed → exit 1
        even when jobs_total_count > 0."""
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
        conclusion null and jobs_total_count > 0 is NOT executed → exit 1.
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
        """AC5: status completed + jobs_total_count > 0 + conclusion success → exit 0;
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
        """AC9: usage errors exit 2 (unknown flag, unreadable path, malformed JSON,
        --days notanint), distinct from the 1 used for "no executed run".

        A missing script also makes the interpreter itself exit 2 ("can't open
        file"), which would satisfy a bare returncode==2 assertion without argparse
        ever running -- so SCRIPT.exists() is asserted first and every case is
        additionally checked for the interpreter's own failure text, matching the
        idiom in tests/test_version_parity.py AC22.
        """
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"

        good = _runs_json(tmp_path, [_entry()])
        missing = tmp_path / "nope.json"
        bad = tmp_path / "malformed.json"
        bad.write_text('{"workflow_runs": [ {"id": 1,, ]')

        cases = {
            "unknown flag": (
                "--runs-json", str(good), "--now", NOW, "--days", "7", "--bogus-flag",
            ),
            "unreadable --runs-json": (
                "--runs-json", str(missing), "--now", NOW, "--days", "7",
            ),
            "malformed --runs-json": (
                "--runs-json", str(bad), "--now", NOW, "--days", "7",
            ),
            "--days notanint": (
                "--runs-json", str(good), "--now", NOW, "--days", "notanint",
            ),
        }
        for label, args in cases.items():
            result = _run(*args)
            combined = _out(result)
            assert "can't open file" not in combined, (
                f"{label}: exit came from the interpreter, not from the script's own "
                f"usage handling: {combined!r}"
            )
            assert "Traceback" not in combined, (
                f"{label}: usage errors must be reported, not raised: {combined!r}"
            )
            assert result.returncode == 2, (
                f"{label} must exit 2, got {result.returncode}: {combined!r}"
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

    def test_ac16_any_executed_run_in_window_not_only_the_latest(self, tmp_path):
        """AC16: ∃ not latest -- newest entry queued/null/3 jobs, then a cancelled
        one, then an executed one inside the window → exit 0."""
        runs = _runs_json(tmp_path, [
            _entry(id=9, status="queued", conclusion=None, jobs_total_count=3,
                   created_at="2026-07-27T11:59:00Z"),          # newest: wedged/fresh
            _entry(id=8, conclusion="cancelled", jobs_total_count=0,
                   created_at="2026-07-26T10:00:00Z"),
            _entry(id=7, created_at="2026-07-25T10:00:00Z"),     # executed, in window
        ])
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 0, (
            "the check is ANY executed main run in the window, not 'the latest run is "
            "executed' -- a run queued seconds ago must not mask a healthy window; got "
            f"exit {result.returncode}, output={_out(result)!r}"
        )

    def test_ac17_branch_match_is_exact_not_substring(self, tmp_path):
        """AC17: a sole entry, executed, in window, on main-backup → exit 1;
        likewise mainline and release/main."""
        for branch in ("main-backup", "mainline", "release/main"):
            runs = _runs_json(
                tmp_path,
                [_entry(head_branch=branch)],
                name=f"runs_{branch.replace('/', '_')}.json",
            )
            result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
            assert result.returncode == 1, (
                f"head_branch={branch!r} is not main -- a substring match would let a "
                "green feature branch vouch for main, which is bd#12's exact "
                f"masking; got exit {result.returncode}, output={_out(result)!r}"
            )

    def test_ac18_workflow_identity_is_path_not_name(self, tmp_path):
        """AC18: an executed run with name == "ci" but path ==
        .github/workflows/nightly-ci.yml → exit 1; the mirror (name "nightly",
        canonical path) → exit 0. `path` is canonical; `name` is free text."""
        decoy = _runs_json(
            tmp_path,
            [_entry(id=11, name="ci", path=".github/workflows/nightly-ci.yml")],
            name="runs_decoy.json",
        )
        result = _run("--runs-json", str(decoy), "--now", NOW, "--days", "7")
        assert result.returncode == 1, (
            "a different workflow file that merely calls itself 'ci' must not vouch "
            f"for .github/workflows/ci.yml; got exit {result.returncode}, "
            f"output={_out(result)!r}"
        )

        mirror = _runs_json(
            tmp_path,
            [_entry(id=12, name="nightly", path=".github/workflows/ci.yml")],
            name="runs_mirror.json",
        )
        result = _run("--runs-json", str(mirror), "--now", NOW, "--days", "7")
        assert result.returncode == 0, (
            "matching on the free-text `name:` breaks the moment an editor renames "
            "the workflow -- `path` is the identity; got exit "
            f"{result.returncode}, output={_out(result)!r}"
        )

    def test_ac19_empty_run_list_is_unhealthy_not_vacuously_healthy(self, tmp_path):
        """AC19: {"workflow_runs": []} → exit 1, never 0, never 2."""
        runs = _runs_json(tmp_path, [])
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 1, (
            "a repo with no runs at all is unhealthy, never vacuously healthy -- an "
            "all()-shaped predicate over an empty list returns True and reports bd#12 "
            f"as fine; got exit {result.returncode}, output={_out(result)!r}"
        )

    def test_ac20_shape_invalid_input_exits_two_with_the_reason_named(self, tmp_path):
        """AC20: shape-valid-but-wrong input exits 2 with the reason named and no
        Traceback: workflow_runs absent / a dict / a list of strings; an entry
        missing jobs_total_count; an unparsable created_at."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"

        entry_no_jobs = _entry()
        entry_no_jobs.pop("jobs_total_count")

        cases = {
            "workflow_runs absent": (
                {"workflow": "ci", "captured_at": NOW}, "workflow_runs",
            ),
            "workflow_runs is a dict": (
                {"workflow_runs": {"id": 1}}, "workflow_runs",
            ),
            "workflow_runs is a list of strings": (
                {"workflow_runs": ["30241562817", "30268020985"]}, "workflow_runs",
            ),
            "entry missing jobs_total_count": (
                {"workflow_runs": [entry_no_jobs]}, "jobs_total_count",
            ),
            "unparsable created_at": (
                {"workflow_runs": [_entry(created_at="last tuesday")]}, "created_at",
            ),
        }
        for label, (payload, reason) in cases.items():
            path = _raw_json(tmp_path, payload, name=f"{label.replace(' ', '_')}.json")
            result = _run("--runs-json", str(path), "--now", NOW, "--days", "7")
            combined = _out(result)
            assert "Traceback" not in combined, (
                f"{label}: an uncaught exception exits 1, which this script's contract "
                f"reads as 'no executed run' -- a crash disguised as a verdict: {combined!r}"
            )
            assert result.returncode == 2, (
                f"{label}: shape-invalid input is a usage error (2), not a health "
                f"verdict (0/1); got {result.returncode}: {combined!r}"
            )
            assert reason in combined, (
                f"{label}: the report must name what was wrong ({reason!r}): {combined!r}"
            )

    def test_ac21_window_boundary_is_closed_on_both_ends(self, tmp_path):
        """AC21: the window is closed on BOTH ends (now - days <= created_at <= now):
        created_at == now - days exactly → inside (exit 0); one second older →
        outside (exit 1); created_at == now exactly → inside (exit 0); one second
        newer than --now → outside (exit 1)."""
        # --now 2026-07-27T12:00:00Z, --days 7 → left edge 2026-07-20T12:00:00Z.
        cases = {
            "2026-07-20T12:00:00Z": 0,  # exactly on the left edge: inside
            "2026-07-20T11:59:59Z": 1,  # one second older: outside
            "2026-07-27T12:00:00Z": 0,  # exactly on the right edge (== --now): inside
            "2026-07-27T12:00:01Z": 1,  # one second after --now: outside
        }
        for created_at, expected in cases.items():
            runs = _runs_json(
                tmp_path,
                [_entry(created_at=created_at)],
                name=f"runs_{created_at.replace(':', '')}.json",
            )
            result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
            assert result.returncode == expected, (
                f"created_at={created_at} with --now {NOW} --days 7 must exit "
                f"{expected} (the window is closed on both ends: "
                f"now - days <= created_at <= now); got "
                f"{result.returncode}, output={_out(result)!r}"
            )

    def test_ac24_predicate_is_event_agnostic(self, tmp_path):
        """AC24: a sole executed main run with event == "workflow_dispatch" → exit 0.
        `event` is never part of the predicate -- a dispatched run is equally valid
        evidence that main executed."""
        runs = _runs_json(tmp_path, [_entry(id=24, event="workflow_dispatch")])
        result = _run("--runs-json", str(runs), "--now", NOW, "--days", "7")
        assert result.returncode == 0, (
            "a completed main ci run with 3 jobs is evidence that main executed no "
            "matter what triggered it; an event == 'push' fence reports a repo kept "
            f"alive by dispatched runs as dead; got exit {result.returncode}, "
            f"output={_out(result)!r}"
        )

    def test_ac25_job_threshold_is_one_and_non_integer_is_a_shape_error(self, tmp_path):
        """AC25: the job-count threshold is 1, not the 3 this repo happens to have --
        a sole executed main run with jobs_total_count == 1 → exit 0. A string
        jobs_total_count ("3") is a shape error → exit 2, reason named, no Traceback."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"

        one = _runs_json(
            tmp_path, [_entry(id=25, jobs_total_count=1)], name="runs_one_job.json"
        )
        result = _run("--runs-json", str(one), "--now", NOW, "--days", "7")
        assert result.returncode == 0, (
            "the predicate is jobs_total_count >= 1; hard-coding this repo's current 3 "
            "makes the check fail the day ci.yml drops to a single job; got exit "
            f"{result.returncode}, output={_out(result)!r}"
        )

        stringy = _raw_json(
            tmp_path,
            {"workflow_runs": [_entry(id=26, jobs_total_count="3")]},
            name="runs_string_jobs.json",
        )
        result = _run("--runs-json", str(stringy), "--now", NOW, "--days", "7")
        combined = _out(result)
        assert "Traceback" not in combined, (
            "a non-integer jobs_total_count must be reported, not raised -- an uncaught "
            f"comparison against a str exits 1, a crash disguised as a verdict: {combined!r}"
        )
        assert result.returncode == 2, (
            "jobs_total_count as the string '3' is shape-invalid input (2), not a "
            f"health verdict (0/1); got {result.returncode}: {combined!r}"
        )
        assert "jobs_total_count" in combined, (
            f"the report must name the offending field: {combined!r}"
        )

    def test_ac26_days_zero_is_an_empty_window_and_negative_is_usage(self, tmp_path):
        """AC26: --days 0 → empty window → exit 1. --days -1 → exit 2."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"

        runs = _runs_json(tmp_path, [_entry(created_at="2026-07-26T06:00:00Z")])

        zero = _run("--runs-json", str(runs), "--now", NOW, "--days", "0")
        zero_out = _out(zero)
        assert "Traceback" not in zero_out, f"--days 0 raised: {zero_out!r}"
        assert zero.returncode == 1, (
            "--days 0 is a legal, empty window: no run can be inside it, so the verdict "
            "is 'no executed run' (1), not a usage error and certainly not healthy; got "
            f"exit {zero.returncode}, output={zero_out!r}"
        )

        negative = _run("--runs-json", str(runs), "--now", NOW, "--days", "-1")
        neg_out = _out(negative)
        assert "Traceback" not in neg_out, f"--days -1 raised: {neg_out!r}"
        assert negative.returncode == 2, (
            "a negative --days inverts the window -- it is a usage error (2), never a "
            f"health verdict; got exit {negative.returncode}, output={neg_out!r}"
        )

    def test_ac27_unusable_now_is_a_usage_error_not_a_verdict(self, tmp_path):
        """AC27: --now 2026-07-27T12:00:00 (timezone-naive) and an unparsable --now
        → exit 2, reason named, no Traceback. A naive/aware TypeError would exit 1,
        which this contract reads as "main is dead"."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"

        runs = _runs_json(tmp_path, [_entry()])
        cases = {
            "timezone-naive --now": "2026-07-27T12:00:00",
            "unparsable --now": "last tuesday",
        }
        for label, now in cases.items():
            result = _run("--runs-json", str(runs), "--now", now, "--days", "7")
            combined = _out(result)
            assert "Traceback" not in combined, (
                f"{label}: an uncaught datetime error exits 1, which this contract "
                f"reads as 'no executed run' -- a crash disguised as a verdict: {combined!r}"
            )
            assert result.returncode == 2, (
                f"{label}: an unusable --now is a usage error (2), not a health "
                f"verdict (0/1); got {result.returncode}: {combined!r}"
            )
            assert "now" in combined.lower(), (
                f"{label}: the report must name --now as what was wrong: {combined!r}"
            )

    # ---- live mode -------------------------------------------------------

    def test_ac13_live_mode_failure_exits_two_and_never_zero(self, tmp_path):
        """AC13: live mode (no --runs-json) with gh shimmed to exit 1 or emit
        garbage → exit 2, output names gh, no Traceback. Never exit 0."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"

        bodies = {
            "gh exits 1": 'echo "gh: authentication required" >&2\nexit 1',
            "gh emits garbage": 'echo "<html>rate limited</html>"\nexit 0',
        }
        for label, body in bodies.items():
            env, argv_log = _gh_shim(tmp_path, body, tag=label.replace(" ", "_"))
            result = _run("--now", NOW, "--days", "7", env=env)
            combined = _out(result)
            assert "Traceback" not in combined, (
                f"{label}: a live-mode failure must be reported, not raised: {combined!r}"
            )
            assert result.returncode == 2, (
                f"{label}: a live-mode failure must exit 2 -- silently exiting 0 "
                "reproduces bd#12's pathology inside the watchdog built to detect it; "
                f"got {result.returncode}: {combined!r}"
            )
            assert "gh" in combined, (
                f"{label}: the report must name gh as the failing collector: {combined!r}"
            )
            assert argv_log.exists(), (
                f"{label}: live mode (no --runs-json) never invoked gh at all: "
                f"{combined!r}"
            )

    def test_ac14_live_mode_shares_one_predicate_and_scopes_to_ci_yml(self, tmp_path):
        """AC14: live mode with gh shimmed to print the captured archive verbatim →
        exit 1 with the same report as AC8. The recorded argv scopes the query to
        ci.yml AND requests a window-sized page (per_page >= 100, and no `-L 1` /
        `per_page=1`) -- proving both modes share one predicate and that live mode
        cannot degenerate into a latest-run reading."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
        assert ARCHIVE.exists(), f"committed bd#12 archive missing at {ARCHIVE}"

        env, argv_log = _gh_shim(tmp_path, f'cat "{ARCHIVE}"', tag="archive")
        result = _run("--now", ARCHIVE_NOW, "--days", "7", env=env)
        combined = _out(result)

        assert "Traceback" not in combined, f"live mode raised: {combined!r}"
        assert result.returncode == 1, (
            "the same archive that AC8 feeds through --runs-json must produce the same "
            "verdict through the live collector -- one predicate, two modes; got exit "
            f"{result.returncode}, output={combined!r}"
        )
        assert "main" in combined, f"report must name the branch main: {combined!r}"
        assert re.search(r"\b7\b", combined), (
            f"report must name the window it checked (7 days): {combined!r}"
        )

        assert argv_log.exists(), f"live mode never invoked gh: {combined!r}"
        argv = argv_log.read_text()
        assert "ci.yml" in argv, (
            "the live query must be scoped to the ci.yml workflow -- an unscoped query "
            f"lets clean-room runs vouch for ci on main; recorded argv: {argv!r}"
        )

        page_sizes = [int(n) for n in re.findall(r"per[_-]page[=\s]+(\d+)", argv)]
        assert page_sizes and max(page_sizes) >= 100, (
            "the live query must request a page large enough to cover the window "
            "(per_page >= 100). A collector that asks for one page of a few entries "
            "reinstates the latest-run reading the ∃ quantifier forbids, in the only "
            f"mode CI actually runs; recorded argv: {argv!r}"
        )
        assert not re.search(r"per[_-]page[=\s]+1(?![0-9])", argv), (
            f"per_page=1 is a latest-run query in disguise; recorded argv: {argv!r}"
        )
        assert not re.search(r"(?:^|\s)(?:-L|--limit)[=\s]+1(?![0-9])", argv), (
            "`-L 1` / `--limit 1` fetches only the newest run -- the ∃ quantifier "
            f"needs the whole window; recorded argv: {argv!r}"
        )

    def test_ac14b_live_mode_is_exists_not_latest(self, tmp_path):
        """AC14b: live-mode ∃ -- gh shimmed to print newest = queued/null, older =
        executed and in window → exit 0. A collector that fetches only the newest
        run reads this healthy repo as dead."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"

        payload = _runs_json(
            tmp_path,
            [
                _entry(id=91, status="queued", conclusion=None, jobs_total_count=3,
                       created_at="2026-07-27T11:59:00Z"),   # newest: just pushed
                _entry(id=90, created_at="2026-07-25T10:00:00Z"),  # executed, in window
            ],
            name="live_exists.json",
        )
        env, argv_log = _gh_shim(tmp_path, f'cat "{payload}"', tag="exists")
        result = _run("--now", NOW, "--days", "7", env=env)
        combined = _out(result)

        assert "Traceback" not in combined, f"live mode raised: {combined!r}"
        assert argv_log.exists(), f"live mode never invoked gh: {combined!r}"
        assert result.returncode == 0, (
            "live mode must apply the same ∃ predicate as fixture mode: a run queued "
            "seconds ago must not mask an executed run earlier in the window. Exit 1 "
            "here is the flapping latest-run reading, which trains everyone to ignore "
            f"the detector; got exit {result.returncode}, output={combined!r}"
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

    def test_ac12_heartbeat_workflow_is_daily_hosted_live_and_non_serialising(self):
        """AC12: .github/workflows/ci-heartbeat.yml exists, runs on ubuntu-latest,
        has a schedule cron firing at least daily (day-of-month, month and
        day-of-week all *), and the step whose run: names the script contains no
        --runs-json, no `|| true`, and neither the step nor its job sets
        continue-on-error: true. Its concurrency is non-serialising -- either
        cancel-in-progress truthy, or a group keyed per run."""
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
        assert any(_fires_at_least_daily(c) for c in crons), (
            "the heartbeat must fire at least daily -- a weekly or annual cron makes a "
            f"7-day window structurally undetectable; got crons {crons!r}"
        )

        jobs = data.get("jobs")
        assert isinstance(jobs, dict) and jobs, "ci-heartbeat.yml declares no jobs"
        runners = set()
        for job in jobs.values():
            if isinstance(job, dict):
                runners |= _runs_on_labels(job.get("runs-on"))
        assert "ubuntu-latest" in runners, (
            "the heartbeat must run on stock hosted infra -- self-hosted labels with 0 "
            f"registered runners are the bd#12 mechanism; got runs-on {runners!r}"
        )

        # The step that actually runs the check, and the job holding it.
        invocations = [
            (job_name, job, step)
            for job_name, job in jobs.items()
            if isinstance(job, dict)
            for step in (job.get("steps") or [])
            if isinstance(step, dict) and "ci_main_heartbeat.py" in _as_str(step.get("run", ""))
        ]
        assert invocations, (
            "no step in ci-heartbeat.yml has a run: that invokes "
            "scripts/ci_main_heartbeat.py"
        )
        for job_name, job, step in invocations:
            step_run = _as_str(step.get("run"))
            assert "--runs-json" not in step_run, (
                f"{job_name}: the scheduled heartbeat must run LIVE, not against a "
                f"committed fixture -- that check is about a frozen snapshot: {step_run!r}"
            )
            assert not re.search(r"\|\|\s*true", step_run), (
                f"{job_name}: the heartbeat must not swallow its own exit code; the "
                f"whole point is a red X rather than silence: {step_run!r}"
            )
            assert step.get("continue-on-error") is not True, (
                f"{job_name}: continue-on-error on the heartbeat step turns the alarm "
                "back into silence"
            )
            assert job.get("continue-on-error") is not True, (
                f"{job_name}: continue-on-error on the heartbeat job turns the alarm "
                "back into silence"
            )

        concurrency = data.get("concurrency")
        assert isinstance(concurrency, dict), (
            f"ci-heartbeat.yml must declare its own concurrency mapping, got {concurrency!r}"
        )
        group_template = concurrency.get("group")
        assert group_template is not None, "ci-heartbeat.yml concurrency has no group"
        # The group's NAME is unconstrained -- `ci-heartbeat-${{ github.run_id }}`
        # is a correct group and must pass. What matters is (a) non-serialising,
        # asserted below, and (b) distinctness from ci.yml's group, which AC23
        # asserts semantically by rendering both. No prefix/substring proxy.
        try:
            cancel = _render(
                concurrency.get("cancel-in-progress"), "refs/heads/main", "aaaaaaa"
            )
            per_run_a = _as_str(
                _render(group_template, "refs/heads/main", "aaaaaaa", run_id="111")
            )
            per_run_b = _as_str(
                _render(group_template, "refs/heads/main", "aaaaaaa", run_id="222")
            )
        except _ExprUnsupported as exc:  # pragma: no cover - diagnostic path
            pytest.fail(f"ci-heartbeat.yml concurrency expression not evaluable: {exc}")

        assert _truthy(cancel) or per_run_a != per_run_b, (
            "a schedule-triggered workflow has a constant github.ref, so a group with a "
            "constant key and cancel-in-progress false is byte-for-byte the bd#12 "
            "mechanism installed in the only detector of bd#12 -- one stuck run and the "
            "watchdog goes permanently silent. Require cancel-in-progress truthy OR a "
            f"group keyed per run; got cancel={cancel!r}, group renders {per_run_a!r} "
            f"for both run ids"
        )

    def test_ac15_manifests_job_runs_this_test_file(self):
        """AC15: the manifests job of ci.yml contains a step whose run: invokes
        tests/test_ci_main_heartbeat.py. Codified ≠ enforced: without this the
        whole file runs in no CI job."""
        assert CI_YML.exists(), f"{CI_YML} missing -- fixture broken"
        text = CI_YML.read_text()
        block = _extract_job_block(text, "manifests")

        step_chunks = re.split(r"(?m)^(?=\s*- )", block)
        test_steps = [
            s for s in step_chunks
            if re.search(r"run:[\s\S]*?tests/test_ci_main_heartbeat\.py", s)
        ]
        assert test_steps, (
            "no step in the manifests job invokes tests/test_ci_main_heartbeat.py -- "
            "the pytest job runs engine_py/tests/, not the repo-root tests/, so this "
            "whole file (AC11's re-wedge fence included) would run in no CI job"
        )

        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict) and "jobs" in parsed, (
            "ci.yml does not parse as a well-formed YAML jobs document"
        )

    def test_ac23_ci_and_heartbeat_groups_never_collide(self):
        """AC23: ci.yml's and ci-heartbeat.yml's concurrency groups render to
        different strings under a shared (ref, sha) context."""
        ci_group = _load_workflow(CI_YML).get("concurrency", {}).get("group")
        hb_group = _load_workflow(HEARTBEAT_YML).get("concurrency", {}).get("group")
        assert ci_group is not None, "ci.yml concurrency has no group"
        assert hb_group is not None, "ci-heartbeat.yml concurrency has no group"

        ref, sha, run_id = "refs/heads/main", "aaaaaaa", "111"
        try:
            rendered_ci = _as_str(_render(ci_group, ref, sha, run_id=run_id))
            rendered_hb = _as_str(_render(hb_group, ref, sha, run_id=run_id))
        except _ExprUnsupported as exc:  # pragma: no cover - diagnostic path
            pytest.fail(f"concurrency expression not evaluable: {exc}")

        assert rendered_ci != rendered_hb, (
            "the watchdog and the watched must never land in one concurrency group -- "
            "a shared group serialises them, so a wedged ci run can silence the very "
            f"check meant to detect it; both rendered {rendered_ci!r}"
        )
