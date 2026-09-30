"""RED-phase tests -- bd#117 Part A: the `plan-approved` readiness gate.

Spec (FROZEN): docs/decisions/2026-09-30-bd117-companion-tune-readiness-gate.md
(Part A = section 2 "Part A", section 4 rows AC-A1..AC-A14, Part-A slice of AC-R).
Part B (companion_tune, tuning, bd_logins) is NOT covered here.

AC -> tests
-----------
AC-A1   decision table      test_ac_a1_*   (off rows, unavailable rows, non-GitHub rows, unborn HEAD,
                                            force-pushed default branch, GIT_CONFIG_* scrub, --json/API)
AC-A2   NOT_APPROVED reasons test_ac_a2_*  (one parametrized row per reason, spec_changed, positive)
AC-A3   policy = push target test_ac_a3_*
AC-A4   branch binding      test_ac_a4_*
AC-A5   gh failures, repo   test_ac_a5_*
AC-A6   pagination          test_ac_a6_*
AC-A7   record authorship / normalisation  test_ac_a7_*
AC-A8   `post`              test_ac_a8_*
AC-A9   real ship.sh        test_ac_a9_*
AC-A10  consumption         test_ac_a10_*
AC-A11  phase_8 ship_to_pr  test_ac_a11_*
AC-A12a ship.sh resume      test_ac_a12a_*
AC-A12  approvers / distinct_actor   test_ac_a12_*
AC-A13  no label add        test_ac_a13_* (plus assert_no_label_add inside every runner)
AC-A14  prompt contract     test_ac_a14_*
AC-R    registries / docs   test_ac_r_*
        (also: test_ship_sh_pr_body_unchanged_by_part_a -- Part B owns the marker)

Contract points pinned here (the spec leaves them open; GREEN implements exactly these)
--------------------------------------------------------------------------------------
* CLI: `scripts/readiness` is a bash wrapper: PYTHONPATH=$(dirname "$0")/../engine_py, then
  `exec python3 -m bytedigger_engine.readiness "$@"`. Subcommands:
    check --stage {start|ship} [--spec P] [--repo .] [--json]
    post  --spec P [--repo .]
  Exit codes per op-A1. stderr lines: `E_READINESS_NOT_APPROVED <reason> #<N>` /
  `W_READINESS_UNAVAILABLE <detail>`. `--json` prints the verdict dict to stdout.
* Python API: `readiness.verdict(repo, stage, spec_path=None) -> dict` with keys required, issue,
  label, verdict, reason, record_sha256; verdict strings are exactly "OFF" | "APPROVED" |
  "NOT_APPROVED" | "UNAVAILABLE". `readiness.parse_issue_from_branch(branch) -> int | None`;
  `phase_8_post_deploy._parse_issue_from_branch` is that same function object (re-export).
* `lib/git_blob.read_blob(repo, rev, path)` -> ("ok", bytes) | ("absent", None) | ("error", detail).
* gh argv forms (readiness always passes the repo explicitly):
    user:          `gh api user`
    issue reads:   `gh api graphql -f query=<Q> -f owner=<o> -f name=<n> -F number=<N> [-f after=<cursor>]`
                   (`-f` = raw string, so a numeric-looking repo name is not type-coerced; `-F` only
                   for the integer number). One connection per query; the query text contains
                   `labels(`, `comments(` or `timelineItems(`; the cursor is an opaque string echoed
                   back verbatim.
    comment:       `gh api -X POST repos/<o>/<n>/issues/<N>/comments --input -`, stdin `{"body": ...}`
    label removal: `gh api -X DELETE repos/<o>/<n>/issues/<N>/labels/<urlencoded label>`
* Readiness runs git with cwd=<repo> (never `git -C`), so a PATH `git` shim keyed on argv[1..2] works.
* When no issue is bound the stderr line is exactly `E_READINESS_NOT_APPROVED no_issue #` (a bare
  trailing `#`, nothing after it).
* Removing the label is best effort: a failing DELETE is a warning line on stderr that names the label;
  exit code and the push are unaffected.
* Wrong field types inside `readiness` => unavailable (ship 4, start warns), even when `required` is
  false (decision-table order: type errors are checked before the required:false off row).
* Re-read delay: after posting a consumption record readiness re-reads the comments exactly
  3 times, 2 s apart (tests with an invisible record therefore take about 6 s).
* Log format of the shared side-effect oracle: one line per event; `PUSH <ref>` from the bare
  repo's pre-receive hook, or a JSON object {"k": "gh", "argv": [...], "stdin": ...} per gh call.

Fixtures follow spec section 4: a real temp repo whose origin push URL is `git@github.com:o/r.git`,
a GIT_SSH_COMMAND shim (GIT_SSH_VARIANT=simple) running upload-pack/receive-pack on a local bare
repo carrying the fixture bytedigger.json, and one stateful python `gh` fixture reached both via
HAL_GH_BIN and as `gh` first on PATH.

Section 1i: every contested state (labels, comments, events, hidden-record counters, hook flags,
refs) is PRE-STAGED in the state file / bare repo before the unit under test runs; nothing races.
Section 1q: the new modules are imported lazily inside tests, so collection never errors.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = REPO_ROOT / "engine_py"
SCRIPTS = REPO_ROOT / "scripts"
READINESS_CLI = SCRIPTS / "readiness"
SHIP_SH = SCRIPTS / "ship.sh"

LABEL = "plan-approved"
SPEC_TEXT = "# Spec\n\nAdd the thing.\n"
OTHER_SPEC_TEXT = "# Spec\n\nAdd a different thing.\n"
BD_USER = "bd-bot"
TIMEOUT = 120
GIT_ID = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


# --------------------------------------------------------------------------- pure helpers


def norm(text: str) -> str:
    """Spec-record normalisation: CRLF -> LF, trailing whitespace stripped, one final newline."""
    return text.replace("\r\n", "\n").rstrip() + "\n"


def sha_of(text: str) -> str:
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()


def iso(n: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=n)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def record(db_id, text=SPEC_TEXT, *, author=BD_USER, ts=200, edited=None, sha=None):
    body = f"<!-- bd:spec sha256={sha or sha_of(text)} -->\n{text}"
    return {
        "id": f"IC_{db_id}",
        "databaseId": db_id,
        "author": {"login": author},
        "body": body,
        "createdAt": iso(ts),
        "lastEditedAt": edited,
    }


def consumption(db_id, event_id, branch, *, author=BD_USER, ts=500, edited=None):
    return {
        "id": f"IC_{db_id}",
        "databaseId": db_id,
        "author": {"login": author},
        "body": f"<!-- bd:consumed approval={event_id} branch={branch} -->",
        "createdAt": iso(ts),
        "lastEditedAt": edited,
    }


def filler(db_id, *, author="alice", ts=100):
    return {
        "id": f"IC_{db_id}",
        "databaseId": db_id,
        "author": {"login": author},
        "body": f"chatter {db_id}",
        "createdAt": iso(ts),
        "lastEditedAt": None,
    }


def levent(event_id, actor, ts, label=LABEL):
    return {
        "id": event_id,
        "actor": {"login": actor},
        "createdAt": iso(ts),
        "label": {"name": label},
    }


# --------------------------------------------------------------------------- fake gh


FAKE_GH = r'''#!__PY__
import json, re, sys, urllib.parse
from datetime import datetime, timedelta, timezone

STATE = "__STATE__"
LOG = "__LOG__"
argv = sys.argv[1:]
stdin = sys.stdin.read() if "--input" in argv else None
with open(LOG, "a") as fh:
    fh.write(json.dumps({"k": "gh", "argv": argv, "stdin": stdin}) + "\n")
st = json.load(open(STATE))
sw = st.get("switch", {})


def save():
    json.dump(st, open(STATE, "w"))


def iso(n):
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg, rc=1):
    print(msg, file=sys.stderr)
    sys.exit(rc)


if argv[:2] == ["pr", "create"]:
    if st.get("pr_create_rc"):
        die("gh: pr create failed", st["pr_create_rc"])
    print("https://github.com/o/r/pull/7")
    sys.exit(0)
if argv[:2] == ["pr", "list"]:
    print("[]")
    sys.exit(0)
if argv[:1] != ["api"]:
    sys.exit(0)

mode = sw.get("mode")
if mode == "exit1":
    die("boom")
if mode == "nonjson":
    print("<html>not json</html>")
    sys.exit(0)

if argv[1:] == ["user"]:
    if sw.get("user_403"):
        die("HTTP 403")
    print(json.dumps({"login": st["bd_user"]}))
    sys.exit(0)

if argv[1] == "graphql":
    opts = {}
    i = 2
    while i < len(argv):
        if argv[i] in ("-f", "-F") and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            opts[k] = v
            i += 2
        else:
            i += 1
    if opts.get("owner") != "o" or opts.get("name") != "r":
        die("graphql: wrong or missing repository %r/%r" % (opts.get("owner"), opts.get("name")))
    if opts.get("number") != "42":
        die("graphql: wrong or missing issue number %r" % (opts.get("number"),))
    q = opts.get("query", "")
    after = opts.get("after")
    if after is not None and sw.get("fail_page2"):
        die("page 2 failed")
    if "timelineItems(" in q:
        conn, items = "timelineItems", list(st["events"])
    elif "comments(" in q:
        conn = "comments"
        hidden = st.setdefault("hidden", {})
        items = [c for c in st["comments"] if hidden.get(str(c["databaseId"]), 0) <= 0]
        if after is None:
            for key in list(hidden):
                if hidden[key] > 0:
                    hidden[key] -= 1
            save()
        if sw.get("malformed"):
            items = [42]
    elif "labels(" in q:
        conn, items = "labels", [{"name": n} for n in st["labels"]]
    else:
        die("graphql: unknown query")
    start = int(after.split(":")[1]) if after else 0
    page = items[start:start + 100]
    has_next = start + 100 < len(items)
    print(json.dumps({"data": {"repository": {"issue": {conn: {
        "nodes": page,
        "pageInfo": {"hasNextPage": has_next, "endCursor": "cur:%d" % (start + 100)},
    }}}}}))
    sys.exit(0)

if "-X" in argv:
    method = argv[argv.index("-X") + 1]
    path = argv[argv.index("-X") + 2]
    if method == "POST" and re.fullmatch(r"repos/o/r/issues/42/comments", path):
        if sw.get("create_fails"):
            die("HTTP 500")
        body = json.loads(stdin)["body"]
        nid = max([c["databaseId"] for c in st["comments"]] + [100]) + 1
        st["clock"] += 1
        st["comments"].append({
            "id": "IC_%d" % nid, "databaseId": nid, "author": {"login": st["bd_user"]},
            "body": body, "createdAt": iso(st["clock"]), "lastEditedAt": None,
        })
        if sw.get("create_hidden_reads"):
            st.setdefault("hidden", {})[str(nid)] = sw["create_hidden_reads"]
        save()
        print(json.dumps({"id": nid}))
        sys.exit(0)
    m = re.fullmatch(r"repos/o/r/issues/42/labels/(.+)", path)
    if method == "DELETE" and m:
        if sw.get("delete_fails"):
            die("HTTP 500")
        name = urllib.parse.unquote(m.group(1))
        st["labels"] = [n for n in st["labels"] if n != name]
        save()
        print("[]")
        sys.exit(0)
die("unsupported gh call: %r" % (argv,))
'''

FAKE_SSH = """#!/bin/sh
case "$2" in
  git-upload-pack*) exec git upload-pack "__BARE__" ;;
  git-receive-pack*) exec git receive-pack "__BARE__" ;;
esac
echo "ssh shim: unexpected: $*" >&2
exit 255
"""

PRE_RECEIVE = """#!/bin/sh
while read old new ref; do echo "PUSH $ref" >> "__LOG__"; done
[ -e "__FLAG__" ] && exit 1
exit 0
"""


def _write_exec(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def g(args, cwd, *, check=True):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=check, capture_output=True, text=True,
        timeout=60, env={**os.environ, **GIT_ID},
    )


DEFAULT_POLICY = {"readiness": {"required": True}}


class Rig:
    def __init__(self, root: Path, repo: Path, bare: Path, log: Path, state_file: Path, gh: Path,
                 flag: Path):
        self.root, self.repo, self.bare = root, repo, bare
        self.log, self.state_file, self.gh, self.flag = log, state_file, gh, flag
        self.state_yaml = root / "build-state.yaml"

    # ---- fixture state
    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def save(self, st: dict) -> None:
        self.state_file.write_text(json.dumps(st))

    def seed(self, *, labels=None, comments=None, events=None, **extra) -> None:
        st = self.state()
        if labels is not None:
            st["labels"] = labels
        if comments is not None:
            st["comments"] = comments
        if events is not None:
            st["events"] = events
        st.update(extra)
        self.save(st)

    def switch(self, **kw) -> None:
        st = self.state()
        st["switch"].update(kw)
        self.save(st)

    def approve(self, text=SPEC_TEXT) -> None:
        """Approved plan: BD-user record (ts 200), approval event by alice (ts 300), label on."""
        self.seed(labels=[LABEL], comments=[record(1, text)], events=[levent("LE_1", "alice", 300)])

    # ---- log oracle
    def events(self) -> list[dict]:
        out = []
        if not self.log.exists():
            return out
        for line in self.log.read_text().splitlines():
            if line.startswith("PUSH "):
                out.append({"k": "push", "ref": line[5:]})
            elif line.strip():
                out.append(json.loads(line))
        return out

    def gh_calls(self) -> list[dict]:
        return [e for e in self.events() if e["k"] == "gh"]

    def pushes(self) -> list[str]:
        return [e["ref"] for e in self.events() if e["k"] == "push"]

    def clear_log(self) -> None:
        self.log.write_text("")

    def git_env_pushurl(self, url: str) -> None:
        g(["remote", "set-url", "--push", "origin", url], self.repo)

    def bare_has_branch(self, branch: str) -> bool:
        return g(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], self.bare,
                 check=False).returncode == 0


def make_rig(tmp_path: Path, monkeypatch, *, policy=DEFAULT_POLICY, branch="gh42-feature",
             change="commit") -> Rig:
    root = tmp_path
    # Hermetic git: no developer global/system config (hooksPath, pushInsteadOf, ...).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    bare = root / "bare.git"
    g(["init", "-q", "--bare", "-b", "main", str(bare)], root)
    seed = root / "seed"
    g(["init", "-q", "-b", "main", str(seed)], root)
    (seed / "README").write_text("init\n")
    (seed / "feature.txt").write_text("base\n")
    if policy is not None:
        text = policy if isinstance(policy, str) else json.dumps(policy)
        (seed / "bytedigger.json").write_text(text)
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"], seed)
    g(["push", "-q", str(bare), "main"], seed)

    log = root / "log.txt"
    log.write_text("")
    flag = root / "hook-fail.flag"
    hook = bare / "hooks" / "pre-receive"
    _write_exec(hook, PRE_RECEIVE.replace("__LOG__", str(log)).replace("__FLAG__", str(flag)))

    bindir = root / "bin"
    bindir.mkdir()
    ssh = _write_exec(root / "ssh-shim", FAKE_SSH.replace("__BARE__", str(bare)))
    state_file = root / "gh-state.json"
    gh = _write_exec(
        bindir / "gh",
        FAKE_GH.replace("__PY__", sys.executable).replace("__STATE__", str(state_file))
        .replace("__LOG__", str(log)),
    )
    state_file.write_text(json.dumps({
        "bd_user": BD_USER, "labels": [], "comments": [], "events": [], "clock": 1000,
        "switch": {}, "hidden": {},
    }))

    monkeypatch.setenv("GIT_SSH_COMMAND", str(ssh))
    monkeypatch.setenv("GIT_SSH_VARIANT", "simple")
    monkeypatch.setenv("HAL_GH_BIN", str(gh))
    monkeypatch.delenv("BD_GH_BIN", raising=False)
    monkeypatch.delenv("BYTEDIGGER_GH_BIN", raising=False)
    for k in ("HAL_BUILD_SHIP_PR", "BD_BUILD_SHIP_PR", "BYTEDIGGER_BUILD_SHIP_PR", "BD_SHIP_PR",
              "BYTEDIGGER_SHIP_PR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(k, raising=False)

    repo = root / "repo"
    g(["clone", "-q", str(bare), str(repo)], root)
    g(["config", "user.name", "t"], repo)
    g(["config", "user.email", "t@t"], repo)
    g(["config", "commit.gpgsign", "false"], repo)
    g(["remote", "set-url", "origin", "git@github.com:o/r.git"], repo)
    g(["fetch", "-q", "origin"], repo)
    if branch != "main":
        g(["checkout", "-q", "-b", branch], repo)
    if change == "commit":
        (repo / "feature.txt").write_text("branch\n")
        g(["add", "-A"], repo)
        g(["commit", "-q", "-m", "feat: branch change"], repo)
    elif change == "modify":
        (repo / "feature.txt").write_text("branch\n")
    log.write_text("")
    return Rig(root, repo, bare, log, state_file, gh, flag)


# --------------------------------------------------------------------------- runners


def _is_label_add(e: dict) -> bool:
    argv = e["argv"]
    if "--add-label" in argv:
        return True
    if any("addLabelsToLabelable" in a for a in argv):
        return True
    if "POST" in argv or "PUT" in argv:
        return any(re.search(r"/labels/?$", a) for a in argv)
    return False


def assert_no_label_add(rig: Rig) -> None:
    """AC-A13: no BD-invoked command ever adds a label."""
    adds = [e["argv"] for e in rig.gh_calls() if _is_label_add(e)]
    assert adds == [], f"AC-A13: BD must never add a label, saw {adds!r}"


def _env(extra=None) -> dict:
    env = dict(os.environ)
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def run_readiness(rig: Rig, *args, env=None, cwd=None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["bash", str(READINESS_CLI), *args], cwd=str(cwd or rig.repo), capture_output=True,
        text=True, timeout=TIMEOUT, env=_env(env),
    )
    assert_no_label_add(rig)
    return proc


def write_spec(rig: Rig, text=SPEC_TEXT, name="spec.md") -> Path:
    p = rig.root / name
    p.write_bytes(text.encode("utf-8"))
    return p


def write_build_state(rig: Rig, files=("feature.txt",)) -> None:
    lines = ["task: Add the feature", "files_modified:"] + [f"  - {f}" for f in files]
    rig.state_yaml.write_text("\n".join(lines) + "\n")


def run_ship(rig: Rig, *, pr=True, env=None) -> subprocess.CompletedProcess:
    write_build_state(rig) if not rig.state_yaml.exists() else None
    args = ["bash", str(SHIP_SH)] + (["--pr"] if pr else []) + ["--state", str(rig.state_yaml)]
    proc = subprocess.run(args, cwd=str(rig.repo), capture_output=True, text=True,
                          timeout=TIMEOUT, env=_env(env))
    assert_no_label_add(rig)
    return proc


def run_phase8(rig: Rig, **org):
    from bytedigger_engine.contracts import WorkflowContext
    from bytedigger_engine.workflows.phase_8_post_deploy import _ship_to_pr

    cfg = {"scratchpad_dir": str(rig.root / "scratch"), "working_dir": str(rig.repo),
           "ship_pr": True, **org}
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=cfg, question="ship",
        session_id="test-session", persona="hal", framework=None, domain=None,
    )
    result = _ship_to_pr(ctx, None)
    assert_no_label_add(rig)
    return result


def not_approved_line(reason: str, n: int = 42) -> re.Pattern:
    return re.compile(rf"^E_READINESS_NOT_APPROVED {re.escape(reason)} #{n}$", re.M)


def comment_creates(rig: Rig) -> list[dict]:
    out = []
    for e in rig.gh_calls():
        a = e["argv"]
        if a[:3] == ["api", "-X", "POST"] and len(a) > 3 and a[3].endswith("/comments"):
            out.append(e)
    return out


def consumption_creates(rig: Rig) -> list[dict]:
    return [e for e in comment_creates(rig)
            if json.loads(e["stdin"])["body"].startswith("<!-- bd:consumed")]


def label_removals(rig: Rig) -> list[dict]:
    return [e for e in rig.gh_calls() if e["argv"][:3] == ["api", "-X", "DELETE"]]


def pr_creates(rig: Rig) -> list[dict]:
    return [e for e in rig.gh_calls() if e["argv"][:2] == ["pr", "create"]]


def index_of(rig: Rig, pred) -> int:
    for i, e in enumerate(rig.events()):
        if pred(e):
            return i
    return -1


# =========================================================================== AC-A1


def _mut_unreachable(rig):
    rig.git_env_pushurl("/nonexistent/path/to/nowhere.git")


def _mut_detached_head(rig):
    sha = g(["rev-parse", "main"], rig.bare).stdout.strip()
    (rig.bare / "HEAD").write_text(sha + "\n")


def _mut_invalid_json(rig):
    seed = rig.root / "seed"
    (seed / "bytedigger.json").write_text("{not json")
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "bad"], seed)
    g(["push", "-q", "-f", str(rig.bare), "main"], seed)


def _mut_readiness_string(rig):
    seed = rig.root / "seed"
    (seed / "bytedigger.json").write_text(json.dumps({"readiness": "yes"}))
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "str"], seed)
    g(["push", "-q", "-f", str(rig.bare), "main"], seed)


def _mut_two_push_urls(rig):
    g(["config", "--add", "remote.origin.pushurl", "git@github.com:o/r.git"], rig.repo)
    g(["config", "--add", "remote.origin.pushurl", "git@github.com:o/r2.git"], rig.repo)


def _mut_local_push_url_required(rig):
    rig.git_env_pushurl(str(rig.bare))


def _mut_ghe_push_url_required(rig):
    rig.git_env_pushurl("git@ghe.example.com:o/r.git")


@pytest.mark.parametrize(
    "mutate",
    [_mut_unreachable, _mut_detached_head, _mut_invalid_json, _mut_readiness_string,
     _mut_two_push_urls, _mut_local_push_url_required, _mut_ghe_push_url_required],
    ids=["unreachable", "detached-head", "invalid-json", "readiness-string", "two-push-urls",
         "non-github-under-required", "ghe-under-required"],
)
def test_ac_a1_unreadable_policy_ship_4_start_warns_0(tmp_path, monkeypatch, mutate):
    """AC-A1: ship => exit 4; start => exit 0 with W_READINESS_UNAVAILABLE on stderr."""
    rig = make_rig(tmp_path, monkeypatch)
    mutate(rig)
    rig.clear_log()  # fixture-mutation pushes are not part of the unit under test
    ship = run_readiness(rig, "check", "--stage", "ship")
    assert ship.returncode == 4, f"ship must fail closed (4), got {ship.returncode}: {ship.stderr!r}"
    assert "W_READINESS_UNAVAILABLE" in ship.stderr
    start = run_readiness(rig, "check", "--stage", "start")
    assert start.returncode == 0, f"start never blocks on an unreadable policy: {start.stderr!r}"
    assert "W_READINESS_UNAVAILABLE" in start.stderr
    assert rig.pushes() == []


def _off_no_origin(rig):
    g(["remote", "remove", "origin"], rig.repo)


def _off_empty_get_url(rig):
    real_git = shutil.which("git")
    shim = rig.root / "bin" / "git"
    _write_exec(shim, f'#!/bin/sh\nif [ "$1" = remote ] && [ "$2" = get-url ]; then exit 0; fi\n'
                      f'exec "{real_git}" "$@"\n')


def _off_absent_file(rig):
    seed = rig.root / "seed"
    g(["rm", "-q", "bytedigger.json"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "rm"], seed)
    g(["push", "-q", "-f", str(rig.bare), "main"], seed)


def _off_readiness_absent(rig):
    seed = rig.root / "seed"
    (seed / "bytedigger.json").write_text(json.dumps({"other": 1}))
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "other"], seed)
    g(["push", "-q", "-f", str(rig.bare), "main"], seed)


def _off_required_false(rig):
    seed = rig.root / "seed"
    (seed / "bytedigger.json").write_text(json.dumps({"readiness": {"required": False}}))
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "false"], seed)
    g(["push", "-q", "-f", str(rig.bare), "main"], seed)


def _off_local_path_no_policy(rig):
    _off_absent_file(rig)
    rig.git_env_pushurl(str(rig.bare))


def _off_gitlab_no_policy(rig):
    _off_absent_file(rig)
    rig.git_env_pushurl("git@gitlab.com:o/r.git")


def _off_unborn_head(rig):
    g(["symbolic-ref", "HEAD", "refs/heads/nothing-here"], rig.bare)


@pytest.mark.parametrize(
    "mutate",
    [_off_no_origin, _off_empty_get_url, _off_absent_file, _off_readiness_absent,
     _off_required_false, _off_local_path_no_policy, _off_gitlab_no_policy, _off_unborn_head],
    ids=["no-origin", "empty-get-url", "file-absent", "readiness-absent", "required-false",
         "local-path-no-policy", "gitlab-no-policy", "unborn-head"],
)
def test_ac_a1_off_rows_exit_0_and_no_gh_call(tmp_path, monkeypatch, mutate):
    """AC-A1: every 'off' row => exit 0 at both stages, no gh call at all."""
    rig = make_rig(tmp_path, monkeypatch)
    mutate(rig)
    rig.clear_log()
    for stage in ("ship", "start"):
        proc = run_readiness(rig, "check", "--stage", stage)
        assert proc.returncode == 0, f"{stage}: expected off (0), got {proc.returncode}: {proc.stderr!r}"
        assert "E_READINESS" not in proc.stderr
    assert rig.gh_calls() == [], f"off must make no gh call, saw {rig.gh_calls()!r}"


def test_ac_a1_policy_fetch_survives_force_pushed_default_branch(tmp_path, monkeypatch):
    """AC-A1: the fetch is forced (+D:refs/bd/policy): a force-pushed default branch does not wedge it."""
    rig = make_rig(tmp_path, monkeypatch)
    first = run_readiness(rig, "check", "--stage", "ship")
    assert first.returncode == 3, first.stderr
    force = rig.root / "force"
    g(["init", "-q", "-b", "main", str(force)], rig.root)
    (force / "bytedigger.json").write_text(json.dumps(DEFAULT_POLICY))
    (force / "unrelated").write_text("x\n")
    g(["add", "-A"], force)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "orphan"], force)
    g(["push", "-q", "-f", str(rig.bare), "main:main"], force)
    second = run_readiness(rig, "check", "--stage", "ship")
    assert second.returncode == 3, f"policy must still be evaluated, got {second.returncode}: {second.stderr!r}"
    tip = g(["rev-parse", "main"], rig.bare).stdout.strip()
    assert g(["rev-parse", "refs/bd/policy"], rig.repo).stdout.strip() == tip


def test_ac_a1_git_config_env_cannot_redirect_the_policy_read(tmp_path, monkeypatch):
    """AC-A1: GIT_CONFIG_PARAMETERS / GIT_CONFIG_COUNT setting remote.origin.pushurl are scrubbed."""
    rig = make_rig(tmp_path, monkeypatch)
    decoy = rig.root / "decoy.git"
    g(["init", "-q", "--bare", "-b", "main", str(decoy)], rig.root)
    dseed = rig.root / "dseed"
    g(["init", "-q", "-b", "main", str(dseed)], rig.root)
    (dseed / "bytedigger.json").write_text(json.dumps({"readiness": {"required": False}}))
    g(["add", "-A"], dseed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "d"], dseed)
    g(["push", "-q", str(decoy), "main"], dseed)
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "remote.origin.pushurl",
        "GIT_CONFIG_VALUE_0": str(decoy),
        "GIT_CONFIG_PARAMETERS": f"'remote.origin.pushurl={decoy}'",
    }
    proc = run_readiness(rig, "check", "--stage", "ship", env=env)
    assert proc.returncode == 3, f"real policy (required) must still apply, got {proc.returncode}: {proc.stderr!r}"


def test_ac_a1_json_output_pins_verdict_strings(tmp_path, monkeypatch):
    """AC-A1/op-A1: `--json` prints the verdict dict; verdict strings are OFF / NOT_APPROVED / APPROVED."""
    rig = make_rig(tmp_path, monkeypatch)
    na = run_readiness(rig, "check", "--stage", "ship", "--json")
    assert na.returncode == 3
    d = json.loads(na.stdout)
    assert d["required"] is True and d["issue"] == 42 and d["label"] == LABEL
    assert d["verdict"] == "NOT_APPROVED" and d["reason"] == "no_spec_record"
    assert d["record_sha256"] is None
    rig.approve()
    ok = run_readiness(rig, "check", "--stage", "start", "--spec", str(write_spec(rig)), "--json")
    assert ok.returncode == 0, ok.stderr
    d = json.loads(ok.stdout)
    assert d["verdict"] == "APPROVED" and d["reason"] is None and d["record_sha256"] == sha_of(SPEC_TEXT)


def test_ac_a1_json_off_verdict(tmp_path, monkeypatch):
    """AC-A1/op-A1: an off policy reports verdict OFF, required false, under `--json`."""
    rig = make_rig(tmp_path, monkeypatch, policy=None)
    proc = run_readiness(rig, "check", "--stage", "ship", "--json")
    assert proc.returncode == 0, proc.stderr
    d = json.loads(proc.stdout)
    assert d["verdict"] == "OFF" and d["required"] is False


def test_ac_a1_python_api_verdict_dict(tmp_path, monkeypatch):
    """AC-A1/op-A1: readiness.verdict(repo, stage, spec_path=None) returns the pinned dict."""
    from bytedigger_engine import readiness

    rig = make_rig(tmp_path, monkeypatch)
    v = readiness.verdict(rig.repo, "ship")
    assert set(v) >= {"required", "issue", "label", "verdict", "reason", "record_sha256"}
    assert v["verdict"] == "NOT_APPROVED" and v["reason"] == "no_spec_record" and v["issue"] == 42


def test_ac_a1_git_blob_read_blob_tri_state(tmp_path, monkeypatch):
    """AC-A1: lib/git_blob.read_blob is tri-state: ok / absent / error."""
    from bytedigger_engine.lib.git_blob import read_blob

    rig = make_rig(tmp_path, monkeypatch)
    status, data = read_blob(rig.repo, "HEAD", "README")
    assert status == "ok" and data == b"init\n"
    status, data = read_blob(rig.repo, "HEAD", "no-such-file")
    assert status == "absent" and data is None
    status, detail = read_blob(rig.repo, "no-such-rev-xyz", "README")
    assert status == "error" and detail


# =========================================================================== AC-A2


def _seed_no_record(rig):
    rig.seed(labels=[LABEL], events=[levent("LE_1", "alice", 300)])


def _seed_edited(rig):
    rig.seed(labels=[LABEL], comments=[record(1, edited=iso(250))], events=[levent("LE_1", "alice", 300)])


def _seed_bad(rig):
    rig.seed(labels=[LABEL], comments=[record(1, sha="0" * 64)], events=[levent("LE_1", "alice", 300)])


def _seed_label_absent(rig):
    rig.seed(labels=[], comments=[record(1)], events=[levent("LE_1", "alice", 300)])


def _seed_predates(rig):
    rig.seed(labels=[LABEL], comments=[record(1, ts=200)], events=[levent("LE_1", "alice", 100)])


def _seed_equal_ts(rig):
    rig.seed(labels=[LABEL], comments=[record(1, ts=200)], events=[levent("LE_1", "alice", 200)])


def _seed_label_no_event(rig):
    rig.seed(labels=[LABEL], comments=[record(1)], events=[])


def _seed_self_approved(rig):
    rig.seed(labels=[LABEL], comments=[record(1)], events=[levent("LE_1", BD_USER, 300)])


def _seed_consumed(rig):
    rig.seed(labels=[LABEL], comments=[record(1), consumption(5, "LE_1", "gh42-other")],
             events=[levent("LE_1", "alice", 300)])


def _seed_older_valid_newer_edited(rig):
    # F5: editing the newest record must not fall back to the older approved one.
    rig.seed(labels=[LABEL],
             comments=[record(1, ts=100), record(2, OTHER_SPEC_TEXT, ts=200, edited=iso(250))],
             events=[levent("LE_1", "alice", 300)])


def _seed_older_valid_newer_bad(rig):
    rig.seed(labels=[LABEL],
             comments=[record(1, ts=100), record(2, OTHER_SPEC_TEXT, ts=200, sha="0" * 64)],
             events=[levent("LE_1", "alice", 300)])


def _seed_later_other_label_event(rig):
    # A later LabeledEvent for a DIFFERENT label must not become the approval event.
    rig.seed(labels=[LABEL, "bug"], comments=[record(1, ts=200)],
             events=[levent("LE_1", "alice", 100), levent("LE_2", "mallory", 300, label="bug")])


A2_ROWS = [
    ("no_issue", "main", DEFAULT_POLICY, lambda r: r.approve()),
    ("no_spec_record", "gh42-feature", DEFAULT_POLICY, _seed_no_record),
    ("spec_record_edited", "gh42-feature", DEFAULT_POLICY, _seed_edited),
    ("spec_record_bad", "gh42-feature", DEFAULT_POLICY, _seed_bad),
    ("label_absent", "gh42-feature", DEFAULT_POLICY, _seed_label_absent),
    ("label_predates_spec", "gh42-feature", DEFAULT_POLICY, _seed_predates),
    ("label_predates_spec", "gh42-feature", DEFAULT_POLICY, _seed_equal_ts),
    ("label_predates_spec", "gh42-feature", DEFAULT_POLICY, _seed_label_no_event),
    ("approver_not_allowed", "gh42-feature", {"readiness": {"required": True, "approvers": ["carol"]}},
     lambda r: r.approve()),
    ("self_approved", "gh42-feature", {"readiness": {"required": True, "distinct_actor": True}},
     _seed_self_approved),
    ("approval_consumed", "gh42-feature", DEFAULT_POLICY, _seed_consumed),
    ("spec_record_edited", "gh42-feature", DEFAULT_POLICY, _seed_older_valid_newer_edited),
    ("spec_record_bad", "gh42-feature", DEFAULT_POLICY, _seed_older_valid_newer_bad),
    ("label_predates_spec", "gh42-feature", DEFAULT_POLICY, _seed_later_other_label_event),
]


@pytest.mark.parametrize(
    "reason,branch,policy,seeder", A2_ROWS,
    ids=["no_issue", "no_spec_record", "spec_record_edited", "spec_record_bad", "label_absent",
         "predates", "predates-equal-ts", "predates-label-no-event", "approver_not_allowed",
         "self_approved", "approval_consumed", "edited-newer-no-fallback",
         "bad-newer-no-fallback", "later-other-label-event"],
)
def test_ac_a2_not_approved_reason_rows(tmp_path, monkeypatch, reason, branch, policy, seeder):
    """AC-A2: one row per reason => exit 3 and stderr `E_READINESS_NOT_APPROVED <reason> #<N>`."""
    rig = make_rig(tmp_path, monkeypatch, policy=policy, branch=branch)
    seeder(rig)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3, f"{reason}: expected 3, got {proc.returncode}: {proc.stderr!r}"
    if reason == "no_issue":
        pattern = re.compile(r"^E_READINESS_NOT_APPROVED no_issue #$", re.M)
    else:
        pattern = not_approved_line(reason, 42)
    assert pattern.search(proc.stderr), f"stderr must carry the pinned line for {reason}: {proc.stderr!r}"
    assert rig.pushes() == [] and consumption_creates(rig) == [], "a refusal consumes nothing"


def test_ac_a2_spec_changed_is_start_only(tmp_path, monkeypatch):
    """AC-A2: spec_changed at start; the same fixture at ship => 0."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    changed = write_spec(rig, OTHER_SPEC_TEXT, "changed.md")
    start = run_readiness(rig, "check", "--stage", "start", "--spec", str(changed))
    assert start.returncode == 3 and not_approved_line("spec_changed").search(start.stderr), start.stderr
    ship = run_readiness(rig, "check", "--stage", "ship", "--spec", str(changed))
    assert ship.returncode == 0, ship.stderr


def test_ac_a2_positive_row_exits_0(tmp_path, monkeypatch):
    """AC-A2: positive row => 0 at start (same spec) and no consumption at start."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    proc = run_readiness(rig, "check", "--stage", "start", "--spec", str(write_spec(rig)))
    assert proc.returncode == 0, proc.stderr
    assert consumption_creates(rig) == [] and label_removals(rig) == [], "start never consumes"


def test_ac_a2_later_label_event_for_other_label_positive_twin(tmp_path, monkeypatch):
    """AC-A2 (M3 twin): approval for `label` at 300, a later `bug` event at 400 => APPROVED, and the
    consumption names the plan-approved event LE_1, not the `bug` one."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.seed(labels=[LABEL, "bug"], comments=[record(1, ts=200)],
             events=[levent("LE_1", "alice", 300), levent("LE_2", "mallory", 400, label="bug")])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr
    bodies = [json.loads(e["stdin"])["body"] for e in consumption_creates(rig)]
    assert bodies == ["<!-- bd:consumed approval=LE_1 branch=gh42-feature -->"], bodies


def test_ac_a2_logins_compare_case_insensitively(tmp_path, monkeypatch):
    """AC-A2/A12 edge: actor `BD-Bot` is the BD user for distinct_actor; approvers ["Alice"] admits `alice`."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": True, "distinct_actor": True}})
    rig.seed(labels=[LABEL], comments=[record(1)], events=[levent("LE_1", "BD-Bot", 300)])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3 and not_approved_line("self_approved").search(proc.stderr), proc.stderr


def test_ac_a2_approvers_list_matches_case_insensitively(tmp_path, monkeypatch):
    """AC-A2 edge: approvers ["Alice"] counts an approval by `alice`."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": True, "approvers": ["Alice"]}})
    rig.approve()
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr


def test_ac_a2_bd_user_records_match_case_insensitively(tmp_path, monkeypatch):
    """AC-A2 edge: a record authored as `BD-BOT` still counts as the BD user's."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.seed(labels=[LABEL], comments=[record(1, author="BD-BOT")], events=[levent("LE_1", "alice", 300)])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr


def test_ac_a2_repo_flag_is_honoured_from_another_cwd(tmp_path, monkeypatch):
    """AC-A2 edge: `--repo <path>` gives the same verdict when run from an unrelated cwd."""
    rig = make_rig(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proc = run_readiness(rig, "check", "--stage", "ship", "--repo", str(rig.repo), cwd=elsewhere)
    assert proc.returncode == 3 and not_approved_line("no_spec_record").search(proc.stderr), proc.stderr


@pytest.mark.parametrize("args", [["check"], ["check", "--stage", "bogus"], ["post"], []],
                         ids=["no-stage", "bad-stage", "post-no-spec", "no-subcommand"])
def test_ac_a2_usage_errors_exit_2(tmp_path, monkeypatch, args):
    """op-A1: usage errors exit 2."""
    rig = make_rig(tmp_path, monkeypatch)
    proc = run_readiness(rig, *args)
    assert proc.returncode == 2, f"expected 2, got {proc.returncode}: {proc.stderr!r}"


# =========================================================================== AC-A3


def test_ac_a3_policy_comes_from_push_target_not_branch_or_fetch_url(tmp_path, monkeypatch):
    """AC-A3: required:true on D; the branch commits required:false; remote.origin.url is a decoy
    bare with an off policy while pushurl is the real one => still enforced."""
    rig = make_rig(tmp_path, monkeypatch, change=None)
    (rig.repo / "bytedigger.json").write_text(json.dumps({"readiness": {"required": False}}))
    g(["add", "-A"], rig.repo)
    g(["commit", "-q", "-m", "flip required off on the branch"], rig.repo)
    decoy = rig.root / "decoy.git"
    g(["init", "-q", "--bare", "-b", "main", str(decoy)], rig.root)
    dseed = rig.root / "dseed"
    g(["init", "-q", "-b", "main", str(dseed)], rig.root)
    (dseed / "bytedigger.json").write_text(json.dumps({"readiness": {"required": False}}))
    g(["add", "-A"], dseed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "d"], dseed)
    g(["push", "-q", str(decoy), "main"], dseed)
    g(["config", "remote.origin.url", str(decoy)], rig.repo)
    g(["config", "remote.origin.pushurl", "git@github.com:o/r.git"], rig.repo)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3, f"policy must be read from the push target: {proc.returncode} {proc.stderr!r}"
    assert not_approved_line("no_spec_record").search(proc.stderr)


# =========================================================================== AC-A4


@pytest.mark.parametrize("branch,expected", [
    ("high5-x", None), ("feat/x", None), ("build/x", None), ("main", None),
    ("gh42-x", 42), ("GH42-x", 42), ("gh42", 42), ("batch/42", 42),
    ("batch/42-x", 42), ("gh42x", None), ("gh42.x", None),
])
def test_ac_a4_anchored_branch_parser(branch, expected):
    """AC-A4: anchored, case-insensitive `^(?:gh|batch/)(\\d+)(?:-|$)`; phase_8 re-export agrees."""
    from bytedigger_engine import readiness
    from bytedigger_engine.workflows import phase_8_post_deploy as p8

    assert readiness.parse_issue_from_branch(branch) == expected
    assert p8._parse_issue_from_branch(branch) == expected


def test_ac_a4_phase8_parser_is_a_reexport():
    """AC-A4: `_parse_issue_from_branch` moved to readiness.py; phase_8 re-exports it."""
    from bytedigger_engine import readiness
    from bytedigger_engine.workflows import phase_8_post_deploy as p8

    assert p8._parse_issue_from_branch is readiness.parse_issue_from_branch


@pytest.mark.parametrize("branch", ["high5-x", "feat/x", "build/x"])
def test_ac_a4_loose_branches_are_no_issue_through_the_cli(tmp_path, monkeypatch, branch):
    """AC-A4: loosely-named branches are refused `no_issue` end to end."""
    rig = make_rig(tmp_path, monkeypatch, branch=branch)
    rig.approve()
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3
    assert re.search(r"^E_READINESS_NOT_APPROVED no_issue #$", proc.stderr, re.M), proc.stderr


# =========================================================================== AC-A5


@pytest.mark.parametrize("sw", [{"mode": "exit1"}, {"mode": "nonjson"}, {"fail_page2": True}],
                         ids=["gh-exit-1", "gh-non-json", "fail-on-page-2"])
def test_ac_a5_gh_failures_are_unavailable_exit_4(tmp_path, monkeypatch, sw):
    """AC-A5: gh exits 1 / prints non-JSON / fails on page 2 => exit 4 + W_READINESS_UNAVAILABLE."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    st = rig.state()
    st["comments"] += [filler(i) for i in range(2, 151)]  # 150 comments => a second page exists
    rig.save(st)
    rig.switch(**sw)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 4, f"expected 4, got {proc.returncode}: {proc.stderr!r}"
    assert "W_READINESS_UNAVAILABLE" in proc.stderr
    assert rig.pushes() == []


def test_ac_a5_readiness_crash_makes_ship_sh_exit_4_and_push_nothing(tmp_path, monkeypatch):
    """AC-A5: a malformed-but-parseable page (readiness.py cannot handle it) => ship.sh exits 4, no push."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    rig.switch(malformed=True)
    proc = run_ship(rig)
    assert proc.returncode == 4, f"expected 4, got {proc.returncode}: {proc.stderr!r}"
    assert rig.pushes() == [] and not rig.bare_has_branch("gh42-feature")
    assert pr_creates(rig) == []


@pytest.mark.parametrize("rc", [1, 127], ids=["python-crash-rc1", "python-missing-rc127"])
def test_ac_a5_wrapper_crash_or_missing_python_maps_to_4(tmp_path, monkeypatch, rc):
    """AC-A5/op-A4 (M1): the readiness wrapper dying with any code other than 0/3/4 (a `python3`
    stub exiting 1, or 127 as for a missing interpreter) => ship.sh exits 4, nothing pushed, no PR,
    nothing staged or committed."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    _write_exec(rig.root / "bin" / "python3", f"#!/bin/sh\nexit {rc}\n")
    head = g(["rev-parse", "HEAD"], rig.repo).stdout.strip()
    proc = run_ship(rig)
    assert proc.returncode == 4, f"expected 4, got {proc.returncode}: {proc.stderr!r}"
    assert rig.pushes() == [] and not rig.bare_has_branch("gh42-feature")
    assert pr_creates(rig) == []
    assert g(["diff", "--cached", "--quiet"], rig.repo, check=False).returncode == 0, "nothing staged"
    assert g(["rev-parse", "HEAD"], rig.repo).stdout.strip() == head, "nothing committed"


def test_ac_a5_phase8_malformed_page_is_readiness_unavailable(tmp_path, monkeypatch):
    """AC-A5/A11 edge: a malformed-but-parseable page => phase_8 returns E_READINESS_UNAVAILABLE
    (recoverable), no exception escapes, nothing pushed."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    rig.switch(malformed=True)
    result = run_phase8(rig)
    assert result.error_code == "E_READINESS_UNAVAILABLE" and result.recoverable is True
    assert rig.pushes() == []


@pytest.mark.parametrize("policy", [{"readiness": {"required": "true"}},
                                    {"readiness": {"required": False, "approvers": "alice"}}],
                         ids=["required-string", "approvers-string"])
def test_ac_a5_wrong_field_types_are_unavailable(tmp_path, monkeypatch, policy):
    """AC-A1 edge: wrong field types under `readiness` (even with required:false) => ship 4, start warns."""
    rig = make_rig(tmp_path, monkeypatch, policy=policy)
    assert run_readiness(rig, "check", "--stage", "ship").returncode == 4
    start = run_readiness(rig, "check", "--stage", "start")
    assert start.returncode == 0 and "W_READINESS_UNAVAILABLE" in start.stderr


def test_ac_a5_every_github_call_carries_the_push_url_repo(tmp_path, monkeypatch):
    """AC-A5: every GitHub call names <owner>/<name> parsed from the push URL (o/r), never gh's default."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr
    calls = rig.gh_calls()
    graphql = [c for c in calls if c["argv"][:2] == ["api", "graphql"]]
    rest = [c for c in calls if c["argv"][:2] == ["api", "-X"]]
    assert graphql and rest, f"expected both graphql and REST calls, got {[c['argv'][:3] for c in calls]}"
    for c in graphql:
        a = c["argv"]
        assert "owner=o" in a and "name=r" in a, f"graphql call without explicit repo: {a!r}"
        assert a[a.index("owner=o") - 1] == "-f" and a[a.index("name=r") - 1] == "-f", a
        assert "number=42" in a and a[a.index("number=42") - 1] == "-F", a
    for c in rest:
        assert c["argv"][3].startswith("repos/o/r/"), c["argv"]
    assert all("-R" not in c["argv"] or c["argv"][c["argv"].index("-R") + 1] == "o/r" for c in calls)


# =========================================================================== AC-A6


def test_ac_a6_newest_record_on_page_two_is_current(tmp_path, monkeypatch):
    """AC-A6: 150 comments; newest valid record on page 2 => current (label predates it)."""
    rig = make_rig(tmp_path, monkeypatch)
    comments = [record(1, ts=100)] + [filler(i) for i in range(2, 150)] + [record(150, OTHER_SPEC_TEXT, ts=400)]
    rig.seed(labels=[LABEL], comments=comments, events=[levent("LE_1", "alice", 300)])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3 and not_approved_line("label_predates_spec").search(proc.stderr), proc.stderr


def test_ac_a6_latest_labeled_event_on_page_two_is_the_approval(tmp_path, monkeypatch):
    """AC-A6: 150 LabeledEvents, the latest on page 2 => that one is the approval event."""
    rig = make_rig(tmp_path, monkeypatch)
    events = [levent(f"LE_{i}", "alice", i) for i in range(1, 150)] + [levent("LE_150", "alice", 300)]
    rig.seed(labels=[LABEL], comments=[record(1, ts=200)], events=events)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr
    bodies = [json.loads(e["stdin"])["body"] for e in consumption_creates(rig)]
    assert bodies and "approval=LE_150" in bodies[0], bodies


@pytest.mark.parametrize("hi_first", [False, True], ids=["served-lo-hi", "served-hi-lo"])
def test_ac_a6_equal_created_at_higher_database_id_is_current(tmp_path, monkeypatch, hi_first):
    """AC-A6: two records with equal createdAt => the higher databaseId is current, whatever the
    order the API serves them in (order is databaseId, not list position)."""
    rig = make_rig(tmp_path, monkeypatch)
    pair = [record(1, SPEC_TEXT, ts=200), record(2, OTHER_SPEC_TEXT, ts=200)]
    rig.seed(labels=[LABEL], comments=pair[::-1] if hi_first else pair,
             events=[levent("LE_1", "alice", 300)])
    ok = run_readiness(rig, "check", "--stage", "start", "--spec", str(write_spec(rig, OTHER_SPEC_TEXT)))
    assert ok.returncode == 0, ok.stderr
    stale = run_readiness(rig, "check", "--stage", "start", "--spec", str(write_spec(rig, SPEC_TEXT, "s2.md")))
    assert stale.returncode == 3 and not_approved_line("spec_changed").search(stale.stderr), stale.stderr


def test_ac_a6_label_on_page_two_counts_as_present(tmp_path, monkeypatch):
    """AC-A6: 150 labels with `label` on page 2 => label present."""
    rig = make_rig(tmp_path, monkeypatch)
    labels = [f"l{i}" for i in range(149)] + [LABEL]
    rig.seed(labels=labels, comments=[record(1)], events=[levent("LE_1", "alice", 300)])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, f"label on page 2 must be seen, got {proc.returncode}: {proc.stderr!r}"


# =========================================================================== AC-A7


def test_ac_a7_bd_spec_comment_by_non_bd_user_is_ignored(tmp_path, monkeypatch):
    """AC-A7: a newer malformed bd:spec comment by someone else changes nothing."""
    rig = make_rig(tmp_path, monkeypatch)
    evil = {"id": "IC_2", "databaseId": 2, "author": {"login": "mallory"},
            "body": "<!-- bd:spec sha256=deadbeef -->\nevil\n", "createdAt": iso(250), "lastEditedAt": None}
    rig.seed(labels=[LABEL], comments=[record(1), evil], events=[levent("LE_1", "alice", 300)])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr


def test_ac_a7_crlf_spec_file_hashes_like_lf_record(tmp_path, monkeypatch):
    """AC-A7: a CRLF spec file with trailing whitespace matches the LF record (same hash)."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    crlf = SPEC_TEXT.replace("\n", "\r\n") + "   \r\n\r\n"
    proc = run_readiness(rig, "check", "--stage", "start", "--spec", str(write_spec(rig, crlf)))
    assert proc.returncode == 0, proc.stderr


# =========================================================================== AC-A8


def _posted_record(rig: Rig) -> tuple[str, str]:
    creates = comment_creates(rig)
    assert len(creates) == 1, f"exactly one comment create, got {len(creates)}"
    body = json.loads(creates[0]["stdin"])["body"]
    first, rest = body.split("\n", 1)
    m = re.fullmatch(r"<!-- bd:spec sha256=([0-9a-f]{64}) -->", first)
    assert m, f"line 1 must be the spec marker, got {first!r}"
    return m.group(1), rest


def test_ac_a8_post_creates_one_record_then_removes_label(tmp_path, monkeypatch):
    """AC-A8: `post` => one comment create (marker + hash of normalised text), then one label removal."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.seed(labels=[LABEL], events=[levent("LE_1", "alice", 40)])
    spec = write_spec(rig, "# S\r\n\r\nbody  \r\n\r\n")
    proc = run_readiness(rig, "post", "--spec", str(spec))
    assert proc.returncode == 0, proc.stderr
    h, rest = _posted_record(rig)
    assert h == hashlib.sha256(norm(rest).encode()).hexdigest()
    assert norm(rest) == norm("# S\r\n\r\nbody  \r\n\r\n")
    assert len(label_removals(rig)) == 1
    argv = label_removals(rig)[0]["argv"]
    assert argv[3] == f"repos/o/r/issues/42/labels/{urllib.parse.quote(LABEL, safe='')}"
    ci = index_of(rig, lambda e: e["k"] == "gh" and e["argv"][:3] == ["api", "-X", "POST"])
    di = index_of(rig, lambda e: e["k"] == "gh" and e["argv"][:3] == ["api", "-X", "DELETE"])
    assert 0 <= ci < di, "the record is posted before the label is removed"
    assert LABEL not in rig.state()["labels"]


def test_ac_a8_same_spec_again_posts_nothing_but_removes_stale_label(tmp_path, monkeypatch):
    """AC-A8 (F13): same spec, label back on, verdict label_predates_spec => no comment, one removal."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.seed(labels=[LABEL], events=[levent("LE_1", "alice", 40)])
    spec = write_spec(rig)
    assert run_readiness(rig, "post", "--spec", str(spec)).returncode == 0
    st = rig.state()
    st["labels"] = [LABEL]  # a human re-added it, but the event still predates the record
    rig.save(st)
    rig.clear_log()
    proc = run_readiness(rig, "post", "--spec", str(spec))
    assert proc.returncode == 0, proc.stderr
    assert comment_creates(rig) == [], "same sha => no new comment"
    assert len(label_removals(rig)) == 1


def test_ac_a8_same_spec_after_approval_keeps_the_label(tmp_path, monkeypatch):
    """AC-A8 edge (m15): re-running `post` with an unchanged spec while the verdict is APPROVED must not
    remove the label (only `label_predates_spec` removes it) and posts nothing."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    proc = run_readiness(rig, "post", "--spec", str(write_spec(rig)))
    assert proc.returncode == 0, proc.stderr
    assert comment_creates(rig) == [] and label_removals(rig) == []
    assert LABEL in rig.state()["labels"]


def test_ac_a8_post_survives_label_removal_failure(tmp_path, monkeypatch):
    """Spec op-A2 (m6): `post` with a failing label DELETE still posts the record and exits 0."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.seed(labels=[LABEL], events=[levent("LE_1", "alice", 40)])
    rig.switch(delete_fails=True)
    proc = run_readiness(rig, "post", "--spec", str(write_spec(rig)))
    assert proc.returncode == 0, proc.stderr
    assert len(comment_creates(rig)) == 1


def test_ac_a8_oversize_spec_is_refused_without_a_comment(tmp_path, monkeypatch):
    """AC-A8: > 65 536 chars => exit 3 `spec_too_large`, no comment."""
    rig = make_rig(tmp_path, monkeypatch)
    spec = write_spec(rig, "x" * 70000 + "\n")
    proc = run_readiness(rig, "post", "--spec", str(spec))
    assert proc.returncode == 3, proc.stderr
    assert not_approved_line("spec_too_large").search(proc.stderr), proc.stderr
    assert comment_creates(rig) == []


def test_ac_a8_post_is_a_noop_when_readiness_is_off(tmp_path, monkeypatch):
    """AC-A8/op-A1: `post` under an off policy => exit 0, nothing done, no gh call."""
    rig = make_rig(tmp_path, monkeypatch, policy=None)
    proc = run_readiness(rig, "post", "--spec", str(write_spec(rig)))
    assert proc.returncode == 0, proc.stderr
    assert rig.gh_calls() == []


# =========================================================================== AC-A9


def test_ac_a9_ship_sh_refuses_unapproved_before_any_push_or_pr(tmp_path, monkeypatch):
    """AC-A9: required:true, not approved => exit 3, no PUSH, no new ref, no `pr create`."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    proc = run_ship(rig)
    assert proc.returncode == 3, f"expected 3, got {proc.returncode}: {proc.stderr!r}"
    assert not_approved_line("no_spec_record").search(proc.stderr), proc.stderr
    assert rig.pushes() == [] and not rig.bare_has_branch("gh42-feature")
    assert pr_creates(rig) == []


def test_ac_a9_refusal_happens_before_any_git_mutation(tmp_path, monkeypatch):
    """AC-A9/op-A4: the check sits right after the --pr gate, before feat/<slug> creation and staging."""
    rig = make_rig(tmp_path, monkeypatch, branch="main", change="modify")
    head = g(["rev-parse", "HEAD"], rig.repo).stdout.strip()
    proc = run_ship(rig)
    assert proc.returncode == 3, proc.stderr
    assert g(["branch", "--list", "feat/*"], rig.repo).stdout.strip() == ""
    assert g(["rev-parse", "HEAD"], rig.repo).stdout.strip() == head
    assert g(["diff", "--cached", "--quiet"], rig.repo, check=False).returncode == 0, "nothing staged"


def test_ac_a9_approved_ship_orders_consume_remove_push_pr(tmp_path, monkeypatch):
    """AC-A9: approved => consumption comment, label removal, PUSH, pr create -- in this order."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    consume = index_of(rig, lambda e: e["k"] == "gh" and e["argv"][:3] == ["api", "-X", "POST"]
                       and e["stdin"] and "bd:consumed" in e["stdin"])
    remove = index_of(rig, lambda e: e["k"] == "gh" and e["argv"][:3] == ["api", "-X", "DELETE"])
    push = index_of(rig, lambda e: e["k"] == "push")
    pr = index_of(rig, lambda e: e["k"] == "gh" and e["argv"][:2] == ["pr", "create"])
    assert 0 <= consume < remove < push < pr, (consume, remove, push, pr)
    body = json.loads(consumption_creates(rig)[0]["stdin"])["body"]
    assert re.fullmatch(r"<!-- bd:consumed approval=LE_1 branch=gh42-feature -->", body), body
    assert rig.bare_has_branch("gh42-feature")
    assert "ship_complete: true" in rig.state_yaml.read_text()


def test_ac_a9_ship_sh_without_pr_makes_no_readiness_call(tmp_path, monkeypatch):
    """AC-A9: ship.sh without --pr => no readiness call (no gh call, exit 0)."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    proc = run_ship(rig, pr=False)
    assert proc.returncode == 0, proc.stderr
    assert rig.gh_calls() == [] and rig.pushes() == []


def _stub_gh_first_on_path(rig: Rig, monkeypatch) -> None:
    stub = rig.root / "stubbin"
    stub.mkdir()
    _write_exec(stub / "gh", "#!/bin/sh\nexit 99\n")
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")


def test_ac_a9_hal_gh_bin_wins_over_path_gh(tmp_path, monkeypatch):
    """AC-A9: HAL_GH_BIN -> fixture while PATH's gh is a stub exiting 99 => the fixture is used."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    _stub_gh_first_on_path(rig, monkeypatch)
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    assert rig.gh_calls() and rig.pushes() == ["refs/heads/gh42-feature"]
    assert len(pr_creates(rig)) == 1, "ship.sh's own resolver must use HAL_GH_BIN for `pr create`"


def test_ac_a9_bd_gh_bin_is_honoured_when_hal_gh_bin_unset(tmp_path, monkeypatch):
    """AC-A9: HAL_GH_BIN unset, BD_GH_BIN set => BD_GH_BIN used (shell and python alike)."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    _stub_gh_first_on_path(rig, monkeypatch)
    monkeypatch.delenv("HAL_GH_BIN", raising=False)
    monkeypatch.setenv("BD_GH_BIN", str(rig.gh))
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    assert rig.gh_calls() and rig.pushes() == ["refs/heads/gh42-feature"]
    assert len(pr_creates(rig)) == 1


def test_ship_sh_pr_body_unchanged_by_part_a(tmp_path, monkeypatch):
    """Guard: Part A does not touch the PR body (the bd:built marker is Part B)."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    proc = run_ship(rig)
    assert proc.returncode == 0, proc.stderr
    argv = pr_creates(rig)[0]["argv"]
    assert argv[argv.index("--body") + 1] == "Built via ByteDigger /build pipeline."


# =========================================================================== AC-A10


def test_ac_a10_same_branch_retry_passes_and_other_branch_is_refused(tmp_path, monkeypatch):
    """AC-A10: after an approved ship, the same branch again => 0 via retry, no new comment/removal;
    another branch => 3 approval_consumed, no PUSH."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    assert run_ship(rig).returncode == 0
    assert LABEL not in rig.state()["labels"], "consumption removed the label"
    rig.clear_log()
    retry = run_readiness(rig, "check", "--stage", "ship")
    assert retry.returncode == 0, retry.stderr
    assert comment_creates(rig) == [] and label_removals(rig) == []
    g(["checkout", "-q", "-b", "gh42-other", "main"], rig.repo)
    (rig.repo / "feature.txt").write_text("other\n")
    rig.clear_log()
    other = run_ship(rig)
    assert other.returncode == 3, f"{other.returncode}: {other.stderr!r}"
    assert not_approved_line("approval_consumed").search(other.stderr), other.stderr
    assert rig.pushes() == []


@pytest.mark.parametrize("hi_first", [False, True], ids=["served-lo-hi", "served-hi-lo"])
def test_ac_a10_earliest_consumption_record_owns_the_approval(tmp_path, monkeypatch, hi_first):
    """AC-A10: two consumption records (equal createdAt), lower databaseId naming X => X passes, Y
    refused, whatever the order the API serves them in."""
    rig = make_rig(tmp_path, monkeypatch, branch="gh42-x")
    pair = [consumption(5, "LE_1", "gh42-x"), consumption(6, "LE_1", "gh42-y")]
    rig.seed(labels=[], comments=[record(1)] + (pair[::-1] if hi_first else pair),
             events=[levent("LE_1", "alice", 300)])
    x = run_readiness(rig, "check", "--stage", "ship")
    assert x.returncode == 0, x.stderr
    assert comment_creates(rig) == [], "X's retry posts nothing"
    g(["checkout", "-q", "-b", "gh42-y"], rig.repo)
    y = run_readiness(rig, "check", "--stage", "ship")
    assert y.returncode == 3 and not_approved_line("approval_consumed").search(y.stderr), y.stderr


def test_ac_a10_edited_consumption_record_is_ignored(tmp_path, monkeypatch):
    """AC-A10: an edited consumption record is ignored => the approval is still unowned and passes."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.seed(labels=[LABEL], comments=[record(1), consumption(5, "LE_1", "gh42-other", edited=iso(600))],
             events=[levent("LE_1", "alice", 300)])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr
    assert len(consumption_creates(rig)) == 1


def test_ac_a10_comment_create_failure_is_unavailable_and_pushes_nothing(tmp_path, monkeypatch):
    """AC-A10: consumption comment create fails => exit 4, no PUSH."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    rig.switch(create_fails=True)
    proc = run_ship(rig)
    assert proc.returncode == 4, f"{proc.returncode}: {proc.stderr!r}"
    assert rig.pushes() == [] and not rig.bare_has_branch("gh42-feature")
    assert label_removals(rig) == [], "no removal before a visible consumption"


def test_ac_a10_invisible_record_after_three_rereads_is_unavailable(tmp_path, monkeypatch):
    """AC-A10: create succeeds but the record stays invisible over 3 re-reads => exit 4, no PUSH
    (about 6 s of real waiting; pre-staged via the hidden-reads counter, no race)."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    rig.switch(create_hidden_reads=3)
    proc = run_ship(rig)
    assert proc.returncode == 4, f"{proc.returncode}: {proc.stderr!r}"
    assert rig.pushes() == []
    assert len(consumption_creates(rig)) == 1, "the record was posted exactly once"
    assert label_removals(rig) == [], "no removal before a visible consumption"


def test_ac_a10_record_visible_on_second_reread_proceeds(tmp_path, monkeypatch):
    """AC-A10: visible on the 2nd re-read => proceeds and pushes."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    rig.switch(create_hidden_reads=1)
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    assert len(consumption_creates(rig)) == 1, "exactly one consumption record was posted"
    consume = index_of(rig, lambda e: e["k"] == "gh" and e["stdin"] and "bd:consumed" in e["stdin"])
    push = index_of(rig, lambda e: e["k"] == "push")
    assert 0 <= consume < push
    assert rig.pushes() == ["refs/heads/gh42-feature"]


def test_ac_a10_label_removal_failure_is_a_warning_and_ship_proceeds(tmp_path, monkeypatch):
    """Spec section 2 Consumption (m6): a failing label DELETE only warns; exit 0 and the push proceed.
    Pinned: the removal-failure warning line on stderr names the label."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    rig.switch(delete_fails=True)
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    assert len(consumption_creates(rig)) == 1 and len(label_removals(rig)) >= 1
    assert rig.pushes() == ["refs/heads/gh42-feature"]
    assert LABEL in proc.stderr, "the failed removal must be reported as a warning naming the label"


def test_ac_a10_label_removal_failure_warns_at_cli_level(tmp_path, monkeypatch):
    """CLI twin: `check --stage ship` with a failing label DELETE => exit 0 and a warning naming the label."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    rig.switch(delete_fails=True)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr
    assert LABEL in proc.stderr


def test_ac_a10_second_builds_post_refuses_the_first_label_absent(tmp_path, monkeypatch):
    """AC-A10 (gate r3 N2): a second build's `post` before the first consumed => first refused label_absent."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    g(["checkout", "-q", "-b", "gh42-second"], rig.repo)
    assert run_readiness(rig, "post", "--spec", str(write_spec(rig, OTHER_SPEC_TEXT))).returncode == 0
    g(["checkout", "-q", "gh42-feature"], rig.repo)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3 and not_approved_line("label_absent").search(proc.stderr), proc.stderr


def test_ac_a10_relabel_after_consumption_gives_another_branch_a_fresh_approval(tmp_path, monkeypatch):
    """AC-A10: after X consumed, a human removes and re-adds the label => a new unowned event; Y passes and owns it."""
    rig = make_rig(tmp_path, monkeypatch, branch="gh42-y")
    rig.seed(labels=[LABEL], comments=[record(1), consumption(5, "LE_1", "gh42-x")],
             events=[levent("LE_1", "alice", 300), levent("LE_2", "alice", 600)])
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr
    bodies = [json.loads(e["stdin"])["body"] for e in consumption_creates(rig)]
    assert bodies == ["<!-- bd:consumed approval=LE_2 branch=gh42-y -->"], bodies


# =========================================================================== AC-A11


def test_ac_a11_not_approved_is_non_recoverable_and_touches_nothing(tmp_path, monkeypatch):
    """AC-A11: phase_8 not approved => E_READINESS_NOT_APPROVED, recoverable False, no PUSH / pr create / pr list."""
    rig = make_rig(tmp_path, monkeypatch)
    result = run_phase8(rig)
    assert result.status == "error" and result.error_code == "E_READINESS_NOT_APPROVED", (
        result.status, result.error_code)
    assert result.recoverable is False
    assert rig.pushes() == []
    assert [e for e in rig.gh_calls() if e["argv"][:2] in (["pr", "create"], ["pr", "list"])] == []


def test_ac_a11_unavailable_is_recoverable(tmp_path, monkeypatch):
    """AC-A11: gh failing => E_READINESS_UNAVAILABLE, recoverable True, nothing pushed."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    rig.switch(mode="exit1")
    result = run_phase8(rig)
    assert result.error_code == "E_READINESS_UNAVAILABLE" and result.recoverable is True
    assert rig.pushes() == []


def test_ac_a11_reachable_target_without_policy_keeps_push_failed_path(tmp_path, monkeypatch):
    """AC-A11: reachable push target, no policy, pre-receive hook exits 1 => E_SHIP_PUSH_FAILED, recoverable."""
    rig = make_rig(tmp_path, monkeypatch, policy=None)
    rig.flag.write_text("fail")
    result = run_phase8(rig)
    assert result.status == "error" and result.error_code == "E_SHIP_PUSH_FAILED", (
        result.status, result.error_code)
    assert result.recoverable is True
    assert rig.gh_calls() == [], "off => no gh call"


def test_ac_a11_dead_origin_is_readiness_unavailable(tmp_path, monkeypatch):
    """AC-A11 (declared 1x change): origin /nonexistent/path with no readable policy => E_READINESS_UNAVAILABLE."""
    rig = make_rig(tmp_path, monkeypatch, policy=None)
    g(["remote", "set-url", "origin", "/nonexistent/path/to/nowhere.git"], rig.repo)
    result = run_phase8(rig)
    assert result.error_code == "E_READINESS_UNAVAILABLE" and result.recoverable is True


def test_ac_a11_consumed_then_dirty_tree_retries_via_consumption(tmp_path, monkeypatch):
    """AC-A11: approved+consumed, then E_SHIP_DIRTY_TREE => nothing pushed; after fixing, a re-run passes via retry."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    (rig.repo / "feature.txt").write_text("dirty\n")
    first = run_phase8(rig)
    assert first.error_code == "E_SHIP_DIRTY_TREE", (first.status, first.error_code)
    assert len(consumption_creates(rig)) == 1 and rig.pushes() == []
    g(["checkout", "--", "feature.txt"], rig.repo)
    rig.clear_log()
    second = run_phase8(rig)
    assert second.status == "ok", (second.status, second.error_code, second.error)
    assert comment_creates(rig) == [] and label_removals(rig) == [], "retry posts and removes nothing"
    assert rig.pushes() == ["refs/heads/gh42-feature"]


def test_ac_a11_consumed_then_rebase_conflict_retries_via_consumption(tmp_path, monkeypatch):
    """AC-A11: approved+consumed, then a rebase conflict => nothing pushed; after resolving, re-run passes via retry."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    drift = rig.root / "drift"
    g(["clone", "-q", str(rig.bare), str(drift)], rig.root)
    g(["config", "user.name", "t"], drift)
    g(["config", "user.email", "t@t"], drift)
    (drift / "feature.txt").write_text("drift\n")
    g(["commit", "-q", "-am", "drift"], drift)
    g(["push", "-q", "origin", "main"], drift)
    rig.clear_log()
    first = run_phase8(rig)
    assert first.error_code == "E_SHIP_REBASE_CONFLICT", (first.status, first.error_code)
    assert len(consumption_creates(rig)) == 1 and rig.pushes() == []
    g(["rebase", "--abort"], rig.repo)
    g(["rebase", "-X", "theirs", "origin/main"], rig.repo)
    rig.clear_log()
    second = run_phase8(rig)
    assert second.status == "ok", (second.status, second.error_code, second.error)
    assert comment_creates(rig) == [] and label_removals(rig) == []
    assert rig.pushes() == ["refs/heads/gh42-feature"]


def test_ac_a11_consumed_then_phantom_deletion_retries_via_consumption(tmp_path, monkeypatch):
    """AC-A11: approved+consumed, then E_SHIP_PHANTOM_DELETION => nothing pushed; after rebasing onto the
    drifted main and restoring the fetch URL, a re-run passes via retry."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    drift = rig.root / "drift"
    g(["clone", "-q", str(rig.bare), str(drift)], rig.root)
    g(["config", "user.name", "t"], drift)
    g(["config", "user.email", "t@t"], drift)
    (drift / "docs").mkdir()
    (drift / "docs" / "landed.md").write_text("landed\n")
    g(["add", "-A"], drift)
    g(["commit", "-q", "-m", "land"], drift)
    g(["push", "-q", "origin", "main"], drift)
    g(["fetch", "-q", "origin"], rig.repo)
    g(["config", "remote.origin.url", "/nonexistent/path/to/nowhere.git"], rig.repo)
    g(["config", "remote.origin.pushurl", "git@github.com:o/r.git"], rig.repo)
    rig.clear_log()
    first = run_phase8(rig)
    assert first.error_code == "E_SHIP_PHANTOM_DELETION", (first.status, first.error_code)
    assert len(consumption_creates(rig)) == 1 and rig.pushes() == []
    g(["rebase", "origin/main"], rig.repo)
    g(["config", "remote.origin.url", "git@github.com:o/r.git"], rig.repo)
    rig.clear_log()
    second = run_phase8(rig)
    assert second.status == "ok", (second.status, second.error_code, second.error)
    assert comment_creates(rig) == [] and label_removals(rig) == []
    assert rig.pushes() == ["refs/heads/gh42-feature"]


def test_ac_a11_pr_failure_then_rerun_passes_via_retry(tmp_path, monkeypatch):
    """AC-A11: after E_SHIP_PR_FAILED on an approved run, a re-run passes via retry."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    st = rig.state()
    st["pr_create_rc"] = 1
    rig.save(st)
    first = run_phase8(rig)
    assert first.error_code == "E_SHIP_PR_FAILED", (first.status, first.error_code)
    assert len(consumption_creates(rig)) == 1, "the failed run consumed the approval"
    assert LABEL not in rig.state()["labels"], "consumption removed the label"
    st = rig.state()
    st["pr_create_rc"] = 0
    rig.save(st)
    rig.clear_log()
    second = run_phase8(rig)
    assert second.status == "ok", (second.status, second.error_code, second.error)
    assert comment_creates(rig) == [] and label_removals(rig) == []
    assert len(pr_creates(rig)) == 1


@pytest.mark.parametrize("case", ["disabled", "on-main", "zero-ahead"])
def test_ac_a11_skips_make_no_readiness_call(tmp_path, monkeypatch, case):
    """AC-A11: ship disabled / on main / zero ahead => skipped as today, no readiness (gh) call."""
    if case == "on-main":
        rig = make_rig(tmp_path, monkeypatch, branch="main", change=None)
    elif case == "zero-ahead":
        rig = make_rig(tmp_path, monkeypatch, change=None)
    else:
        rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    result = run_phase8(rig, ship_pr=(case != "disabled"))
    assert result.status == "ok", (result.status, result.error_code)
    assert rig.gh_calls() == [] and rig.pushes() == []


# =========================================================================== AC-A12a


def _state_has_ship_complete(rig: Rig) -> bool:
    return "ship_complete: true" in rig.state_yaml.read_text()


def test_ac_a12a_resume_pushes_when_ahead_of_upstream(tmp_path, monkeypatch):
    """AC-A12a: required:true, nothing staged, 1 commit ahead of upstream, approved => PUSH + ship_complete."""
    rig = make_rig(tmp_path, monkeypatch)
    g(["push", "-q", "-u", "origin", "gh42-feature"], rig.repo)
    (rig.repo / "extra.txt").write_text("x\n")
    g(["add", "-A"], rig.repo)
    g(["commit", "-q", "-m", "extra"], rig.repo)
    rig.approve()
    rig.clear_log()
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    assert rig.pushes() == ["refs/heads/gh42-feature"]
    assert _state_has_ship_complete(rig)


def test_ac_a12a_resume_pushes_when_ahead_of_policy_ref_without_upstream(tmp_path, monkeypatch):
    """AC-A12a: no upstream, 1 ahead of refs/bd/policy => PUSH."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.approve()
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    assert rig.pushes() == ["refs/heads/gh42-feature"]
    assert _state_has_ship_complete(rig)


def test_ac_a12a_nothing_ahead_exits_0_without_push(tmp_path, monkeypatch):
    """AC-A12a: nothing staged, 0 ahead => exit 0, no PUSH (approval was still consumed first)."""
    rig = make_rig(tmp_path, monkeypatch, change=None)
    rig.approve()
    proc = run_ship(rig)
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stderr!r}"
    assert rig.pushes() == []
    assert rig.gh_calls(), "the readiness check ran (required: true)"


def test_ac_a12a_required_false_keeps_todays_exit_0_without_push(tmp_path, monkeypatch):
    """AC-A12a: required:false, nothing staged, 1 ahead => exit 0, no PUSH (today's behaviour), no gh call."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": False}})
    proc = run_ship(rig)
    assert proc.returncode == 0, proc.stderr
    assert rig.pushes() == [] and rig.gh_calls() == []


# =========================================================================== AC-A12


def test_ac_a12_distinct_actor_refuses_label_added_by_bd_user(tmp_path, monkeypatch):
    """AC-A12: label added by the BD user + distinct_actor:true => self_approved."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": True, "distinct_actor": True}})
    _seed_self_approved(rig)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3 and not_approved_line("self_approved").search(proc.stderr), proc.stderr


def test_ac_a12_approvers_list_refuses_other_actor(tmp_path, monkeypatch):
    """AC-A12: approvers ["alice"], label added by the BD user => approver_not_allowed."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": True, "approvers": ["alice"]}})
    _seed_self_approved(rig)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 3 and not_approved_line("approver_not_allowed").search(proc.stderr), proc.stderr


def test_ac_a12_defaults_accept_the_bd_user_label(tmp_path, monkeypatch):
    """AC-A12: defaults => 0 (documented residual risk: shared account)."""
    rig = make_rig(tmp_path, monkeypatch)
    _seed_self_approved(rig)
    proc = run_readiness(rig, "check", "--stage", "ship")
    assert proc.returncode == 0, proc.stderr


# =========================================================================== AC-A13


def test_ac_a13_no_label_add_across_approve_refuse_and_post_flows(tmp_path, monkeypatch):
    """AC-A13: across an approved ship, a refusal and a post, the log holds label removals but never an add."""
    rig = make_rig(tmp_path, monkeypatch, change="modify")
    rig.approve()
    assert run_ship(rig).returncode == 0
    g(["checkout", "-q", "-b", "gh42-late", "main"], rig.repo)
    assert run_readiness(rig, "check", "--stage", "ship").returncode == 3
    assert run_readiness(rig, "post", "--spec", str(write_spec(rig, OTHER_SPEC_TEXT))).returncode == 0
    assert label_removals(rig), "the flows must really have removed the label"
    assert_no_label_add(rig)


# =========================================================================== AC-A14


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


PROMPT_FILES = ["phases/phase-0-classify.md", "commands/build.md", "skills/bytedigger/SKILL.md"]


@pytest.mark.parametrize("rel", PROMPT_FILES)
def test_ac_a14_prompt_files_carry_intake_and_start_gate(rel):
    """AC-A14: --issue, gh<N>-, issue_mismatch, start gate, awaiting_stage: start, readiness.required."""
    text = _read(rel)
    for needle in ("--issue", "gh<N>-", "issue_mismatch", "awaiting_stage: start",
                   "readiness.required", "readiness check --stage start"):
        assert needle in text, f"{rel} must contain {needle!r}"


@pytest.mark.parametrize("rel", PROMPT_FILES)
def test_ac_a14_start_gate_is_named_for_every_tier(rel):
    """AC-A14: the start gate (before the first non-scratchpad write) is described for every tier."""
    text = _read(rel)
    pos = [m.start() for m in re.finditer(r"start gate", text, re.I)]
    assert pos, f"{rel} must describe the start gate"
    window = " ".join(text[p:p + 3000] for p in pos)
    for tier in ("TRIVIAL", "SIMPLE", "FEATURE", "COMPLEX"):
        assert tier in window, f"{rel}: start gate text must cover tier {tier}"


def test_ac_a14_pause_point_rule_names_readiness_required_exception():
    """AC-A14: the PAUSE-POINT HARD RULE gains the named exception keyed on readiness.required."""
    text = _read("commands/build.md")
    i = text.index("PAUSE-POINT HARD RULE")
    assert "readiness.required" in text[i:i + 1500], "exception must sit with the rule"


def test_ac_a14_resumable_paragraph_maps_awaiting_stage_ship_to_ship_only():
    """AC-A14: **Resumable:** maps awaiting_stage: ship to re-running SHIP only."""
    text = _read("commands/build.md")
    m = re.search(r"\*\*Resumable:\*\*.*?(?:\n\s*\n|\Z)", text, re.S)
    assert m, "Resumable paragraph missing"
    para = m.group(0)
    assert "awaiting_approval" in para and "awaiting_stage: start" in para
    assert re.search(r"awaiting_stage:\s*ship.{0,300}SHIP", para, re.S), para
    assert re.search(r"\bonly\b", para, re.I)


def test_ac_a14_phase7_ship_heading_precedes_state_cleanup():
    """AC-A14: in phase-7-synthesize.md the '7.5 SHIP' heading precedes 'State Cleanup'."""
    text = _read("phases/phase-7-synthesize.md")
    ship = re.search(r"^#+\s*7\.5 SHIP", text, re.M)
    cleanup = re.search(r"^#+\s*State Cleanup", text, re.M)
    assert ship and cleanup and ship.start() < cleanup.start()


def test_ac_a14_build_md_ship_step_precedes_state_cleanup():
    """AC-A14: in commands/build.md the SHIP step sits between 7.1b and 7.2 State Cleanup."""
    text = _read("commands/build.md")
    a = text.index("7.1b")
    b = text.index("7.2 State Cleanup")
    assert a < b and "SHIP" in text[a:b], "a SHIP step must sit between 7.1b and 7.2"


@pytest.mark.parametrize("rel", ["commands/build.md", "phases/phase-7-synthesize.md"])
def test_ac_a14_nonzero_ship_under_required_stops_awaiting_ship(rel):
    """AC-A14: any non-zero exit under required: true => STOP with awaiting_stage: ship, and the
    `readiness post` recovery is named."""
    text = _read(rel)
    assert re.search(r"non-zero.{0,400}awaiting_stage:\s*ship|awaiting_stage:\s*ship.{0,400}non-zero",
                     text, re.S | re.I), f"{rel}: non-zero => awaiting_stage: ship"
    assert "required: true" in text
    assert "scripts/readiness post --spec" in text, f"{rel}: STOP text must name the post recovery"


def test_ac_a14_exit_criterion_allows_awaiting_approval():
    """AC-A14: the Exit Criterion carries 'unless stopped awaiting approval'."""
    assert "unless stopped awaiting approval" in _read("phases/phase-7-synthesize.md")


# =========================================================================== AC-R


def test_ac_r_error_codes_registered():
    """AC-R: both Part-A codes are in ERROR_CODES."""
    from bytedigger_engine.error_codes import ERROR_CODES

    assert "E_READINESS_NOT_APPROVED" in ERROR_CODES
    assert "E_READINESS_UNAVAILABLE" in ERROR_CODES


@pytest.mark.parametrize("rel", ["ERROR_CODES.md", "bytedigger_engine/ERROR_CODES.md"])
def test_ac_r_error_codes_in_both_markdown_registries(rel):
    """AC-R: both ERROR_CODES.md files name the two codes."""
    md = (ENGINE_ROOT / rel).read_text(encoding="utf-8")
    assert "E_READINESS_NOT_APPROVED" in md and "E_READINESS_UNAVAILABLE" in md


def test_ac_r_core_manifest_lists_new_modules():
    """AC-R: readiness.py and lib/git_blob.py are in core_manifest.json."""
    manifest = json.loads((ENGINE_ROOT / "core_manifest.json").read_text(encoding="utf-8"))
    assert "readiness.py" in manifest["core_modules"]
    assert "lib/git_blob.py" in manifest["core_modules"]


def test_ac_r_configuration_doc_names_the_gate():
    """AC-R: docs/configuration.md names readiness, approvers, distinct_actor, --issue, the engine-build
    recovery and the github.com-only limit."""
    text = _read("docs/configuration.md")
    for needle in ("readiness", "approvers", "distinct_actor", "--issue",
                   "scripts/readiness post --spec"):
        assert needle in text, f"docs/configuration.md must mention {needle!r}"
    assert re.search(r"github\.com[^\n]{0,120}only|only[^\n]{0,120}github\.com", text, re.I), (
        "docs/configuration.md must state that v1 supports github.com only")


def test_ac_r_changelog_mentions_117():
    """AC-R: CHANGELOG.md mentions #117."""
    assert "#117" in _read("CHANGELOG.md")
