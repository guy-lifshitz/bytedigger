"""RED-phase tests -- bd#131: ship.sh ships commits ahead of base, and the PR carries the review evidence.

Spec (FROZEN r2): docs/decisions/2026-09-30-bd131-ship-commits-ahead-pr-evidence.md (section 4, rows AC1..AC29).

AC -> tests   (RED = fails against ship.sh at HEAD; guard = passes today and protects post-GREEN behaviour)
-------------------------------------------------------------------------------------------------------
AC1   pilot shape, 2 commits ahead, nothing modified        test_ac1_pilot_shape_two_commits_ahead_pushes_without_new_commit   RED
AC2   tracked modification not listed is committed          test_ac2_tracked_modification_not_listed_is_committed              RED
AC3   tracked deletion not listed is committed              test_ac3_tracked_deletion_not_listed_is_committed                  RED
AC4   untracked unlisted stay out                           test_ac4_untracked_unlisted_files_stay_out_of_every_commit         RED
      untracked listed is committed                         test_ac4_listed_untracked_is_committed_unlisted_stay_out           RED
AC5   sensitive tracked files are skipped                   test_ac5_sensitive_modified_files_are_skipped_others_committed      RED
AC6   nothing staged, 0 ahead                               test_ac6_nothing_to_ship_warns_and_touches_nothing                 RED
AC7   base order (upstream wins over origin/main)           test_ac7_upstream_wins_over_origin_main_then_new_commit_pushes     RED
AC8   required:true + approved, AC1 shape                   test_ac8_required_true_approved_ahead_still_pushes                 guard
AC9   title from spec H1 with project convention prefix     test_ac9_title_is_h1_with_project_prefix                           RED
AC10  mixed prefixes -> H1 unchanged                        test_ac10_mixed_prefixes_leave_h1_unchanged                        RED
      same prefix added to a bare H1                        test_ac10_common_prefix_is_added_to_bare_h1                        RED
AC11  no spec -> task                                       test_ac11_no_spec_title_is_task                                    RED (needs the push)
      spec_path relative to the state dir                   test_ac11_spec_path_relative_to_state_dir_is_honoured              RED
AC12  250-char H1 cut at a word boundary                    test_ac12_long_h1_is_cut_at_a_word_boundary                        RED
AC13  commit subject == PR title, task in commit body       test_ac13_commit_subject_equals_pr_title_and_task_in_body          RED
AC14  pilot-like evidence body                              test_ac14_body_carries_scope_review_and_followups                  RED
AC15  duplicate key, missing cycles                         test_ac15_last_duplicate_key_wins_and_no_cycles_no_parenthetical   RED
AC16  marker safety                                         test_ac16_bd_marker_lines_in_copied_content_are_dropped            RED
AC17  no evidence -> today's two lines                      test_ac17_no_evidence_body_is_the_two_fixed_lines                  guard
AC18  200 000-char Scope is truncated                       test_ac18_oversize_scope_is_truncated_review_and_marker_survive    RED
AC19  helper fails -> fallback + one warning                test_ac19_helper_failure_falls_back_to_task_and_two_line_body      RED
AC20  helper unit (subprocess) + stdlib-only                test_ac20_helper_exits_0_on_task_only_state,
                                                            test_ac20_helper_imports_are_stdlib_only                           RED
AC21  .gitignore entries                                    test_ac21_gitignore_covers_cycle_files_and_session_file            RED
AC22  phase-7 cleanup / 7.5 / build.md 7.1c text            test_ac22_phase7_state_cleanup_names_cycle_files_and_session_file,
                                                            test_ac22_phase7_ship_section_mentions_ahead,
                                                            test_ac22_build_md_7_1c_mentions_ahead                             RED
AC23  docs / changelog                                      test_ac23_plugin_doc_lists_helper, test_ac23_changelog_unreleased_mentions_bd131   RED
AC24  inverted bd#117 row (required:false, 1 ahead -> PUSH) lives in test_bd117a_readiness.py as
      test_ac_a12a_required_false_now_pushes_when_ahead                                                                        RED
AC25  non-ASCII / spaced tracked paths, STAGE line          test_ac25_non_ascii_and_spaced_tracked_paths_are_staged_unquoted   RED
AC26  untracked-not-shipped warning                         test_ac26_untracked_unlisted_file_is_reported_not_shipped          RED
AC27  detached HEAD                                         test_ac27_detached_head_is_refused_before_any_mutation             RED
AC28  sensitive spec_path; fenced H1 / Scope                test_ac28_sensitive_spec_path_is_treated_as_no_spec,
                                                            test_ac28_fenced_h1_and_fenced_scope_heading_are_ignored           RED
AC27  (r3) runs under required:true, asserts no readiness gh call
AC30  unmerged paths refused before readiness               test_ac30_unmerged_paths_are_refused_before_readiness              RED
AC28  (r3) .env spec has `# Env title` + `## Scope`; AC5 also asserts no `STAGE (tracked change): .env` line
AC29  per-call helper checks                               test_ac29_body_without_marker_falls_back_title_keeps_first_line    RED

r2 tightening (gate r1 M4/m10): AC6/AC7 assert the base text in the warning; AC12 also has test_ac12_exactly_72_*
and test_ac12_single_80_char_word_*; AC13 also has test_ac13_task_equal_to_title_commit_has_no_body; AC16 asserts the
whole offending line is gone; AC18 measures UTF-8 bytes on a non-ASCII Scope; AC19's shim prints a Traceback; AC23 also
needs `STAGE (tracked change)` in docs/plugin.md.

Rig: copied from test_bd117a_readiness.py (as test_bd117b_companion_tune.py does): real temp repo whose origin push
URL is git@github.com:o/r.git, GIT_SSH_COMMAND shim onto a local bare repo with a pre-receive `PUSH <ref>` log, one
stateful fixture gh (HAL_GH_BIN and first on PATH), hermetic GIT_CONFIG_GLOBAL=/dev/null. Default policy
{"readiness": {"required": false}}; AC8 uses required:true + rig.approve(). Build states and specs are files next to the
repo (rig.root), i.e. outside the work tree, exactly as the spec's parsing rules describe. PR title/body are parsed from
the `gh pr create` argv (--title / --body) in the log.

workflows.md section 1i: every contested state (gh world, refs, upstream, python3 shim, state files) is pre-staged
before ship.sh runs; nothing races. Section 1q: nothing under test is imported; ship.sh and the helper are reached
as subprocesses only.
"""
from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
SHIP_SH = SCRIPTS / "ship.sh"
HELPER = SCRIPTS / "ship_pr_text.py"

LABEL = "plan-approved"
SPEC_TEXT = "# Spec\n\nAdd the thing.\n"
BD_USER = "bd-bot"
MARK = "<!-- bd:built -->"
FIXED = "Built via ByteDigger /build pipeline."
TIMEOUT = 90
BRANCH = "gh42-feature"
GIT_ID = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


# --------------------------------------------------------------------------- pure helpers


def norm(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip() + "\n"


def sha_of(text: str) -> str:
    import hashlib

    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()


def iso(n: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(db_id, text=SPEC_TEXT, *, author=BD_USER, ts=200):
    return {
        "id": f"IC_{db_id}", "databaseId": db_id, "author": {"login": author},
        "body": f"<!-- bd:spec sha256={sha_of(text)} -->\n{text}", "createdAt": iso(ts), "lastEditedAt": None,
    }


def levent(event_id, actor, ts, label=LABEL):
    return {"id": event_id, "actor": {"login": actor}, "createdAt": iso(ts), "label": {"name": label}}


# --------------------------------------------------------------------------- fake gh (copied from bd117a)

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

PY_SHIM = """#!/bin/sh
case "$*" in
  *ship_pr_text.py*)
    echo "Traceback (most recent call last):" >&2
    echo "shim: helper broken" >&2
    exit 1 ;;
esac
exec "__PY__" "$@"
"""

PY_SHIM_BAD_OUTPUT = """#!/bin/sh
case "$*" in
  *ship_pr_text.py*" title "*) printf 'Shim Title\\nSecond line\\n'; exit 0 ;;
  *ship_pr_text.py*" body "*) printf '## Scope\\n\\nfake scope\\n\\nBuilt via ByteDigger /build pipeline.\\n'; exit 0 ;;
esac
exec "__PY__" "$@"
"""


def _write_exec(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def g(args, cwd, *, check=True):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=check, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, env={**os.environ, **GIT_ID},
    )


NOT_REQUIRED = {"readiness": {"required": False}}


class Rig:
    def __init__(self, root: Path, repo: Path, bare: Path, log: Path, state_file: Path, gh: Path):
        self.root, self.repo, self.bare = root, repo, bare
        self.log, self.state_file, self.gh = log, state_file, gh
        self.state_yaml = root / "build-state.yaml"
        self.head0 = g(["rev-parse", "HEAD"], repo).stdout.strip()

    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def save(self, st: dict) -> None:
        self.state_file.write_text(json.dumps(st))

    def approve(self, text=SPEC_TEXT) -> None:
        st = self.state()
        st.update(labels=[LABEL], comments=[record(1, text)], events=[levent("LE_1", "alice", 300)])
        self.save(st)

    def events(self) -> list[dict]:
        out = []
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

    def pr_creates(self) -> list[dict]:
        return [e for e in self.gh_calls() if e["argv"][:2] == ["pr", "create"]]

    def clear_log(self) -> None:
        self.log.write_text("")

    def head(self) -> str:
        return g(["rev-parse", "HEAD"], self.repo).stdout.strip()


def make_rig(tmp_path: Path, monkeypatch, *, policy=NOT_REQUIRED, branch=BRANCH, seed_files=None) -> Rig:
    root = Path(os.path.realpath(str(tmp_path)))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    bare = root / "bare.git"
    g(["init", "-q", "--bare", "-b", "main", str(bare)], root)
    seed = root / "seed"
    g(["init", "-q", "-b", "main", str(seed)], root)
    (seed / "README").write_text("init\n")
    (seed / "feature.txt").write_text("base\n")
    if policy is not None:
        (seed / "bytedigger.json").write_text(json.dumps(policy))
    for rel, text in (seed_files or {}).items():
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text(text)
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"], seed)
    g(["push", "-q", str(bare), "main"], seed)

    log = root / "log.txt"
    log.write_text("")
    _write_exec(bare / "hooks" / "pre-receive",
                PRE_RECEIVE.replace("__LOG__", str(log)).replace("__FLAG__", str(root / "hook-fail.flag")))

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
    for k in ("BD_GH_BIN", "BYTEDIGGER_GH_BIN", "HAL_BUILD_SHIP_PR", "BD_BUILD_SHIP_PR",
              "BYTEDIGGER_BUILD_SHIP_PR", "BD_SHIP_PR", "BYTEDIGGER_SHIP_PR"):
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
    log.write_text("")
    return Rig(root, repo, bare, log, state_file, gh)


# --------------------------------------------------------------------------- scenario helpers


def commit_file(rig: Rig, rel: str, text: str, msg: str) -> None:
    p = rig.repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    g(["add", "-A"], rig.repo)
    g(["commit", "-q", "-m", msg], rig.repo)


def ahead(rig: Rig, *subjects: str) -> None:
    """Pre-stage N commits ahead of origin/main (no upstream) with the given subjects."""
    for i, s in enumerate(subjects):
        commit_file(rig, f"ahead{i}.txt", f"{i}\n", s)
    rig.clear_log()


def modify(rig: Rig, rel="feature.txt", text="branch\n") -> None:
    (rig.repo / rel).write_text(text)


def write_state(rig: Rig, *, task="Add the feature", files=(), extra="") -> None:
    lines = [f"task: {task}", "files_modified:" if files else "files_modified: []"]
    lines += [f"  - {f}" for f in files]
    rig.state_yaml.write_text("\n".join(lines) + "\n" + extra)


def write_spec(rig: Rig, text: str, rel="build-spec.md") -> Path:
    p = rig.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def run_ship(rig: Rig, *, env=None) -> subprocess.CompletedProcess:
    if not rig.state_yaml.exists():
        write_state(rig)
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["bash", str(SHIP_SH), "--pr", "--state", str(rig.state_yaml)],
                          cwd=str(rig.repo), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=TIMEOUT, env=e)


def ok_ship(rig: Rig, **kw) -> subprocess.CompletedProcess:
    proc = run_ship(rig, **kw)
    assert proc.returncode == 0, f"ship.sh exit {proc.returncode}: {proc.stderr!r}"
    return proc


def assert_pushed_and_one_pr(rig: Rig) -> None:
    assert rig.pushes() == [f"refs/heads/{BRANCH}"], f"expected one push of {BRANCH}: {rig.pushes()!r}"
    assert len(rig.pr_creates()) == 1, f"expected exactly one `gh pr create`: {len(rig.pr_creates())}"


def _opt(argv, name):
    return argv[argv.index(name) + 1]


def pr_title(rig: Rig) -> str:
    creates = rig.pr_creates()
    assert len(creates) == 1, f"expected exactly one `gh pr create`, got {len(creates)}"
    return _opt(creates[0]["argv"], "--title")


def pr_body(rig: Rig) -> str:
    creates = rig.pr_creates()
    assert len(creates) == 1, f"expected exactly one `gh pr create`, got {len(creates)}"
    return _opt(creates[0]["argv"], "--body")


def body_lines(rig: Rig) -> list[str]:
    return pr_body(rig).rstrip("\n").split("\n")


def tree_files(rig: Rig, rev="HEAD") -> set[str]:
    return set(g(["ls-tree", "-r", "--name-only", rev], rig.repo).stdout.splitlines())


def all_committed_paths(rig: Rig) -> set[str]:
    out = g(["log", "--all", "--name-only", "--format="], rig.repo).stdout
    return {ln for ln in out.splitlines() if ln.strip()}


def state_text(rig: Rig) -> str:
    return rig.state_yaml.read_text()


# =========================================================================== AC1-AC8: staging / push rule


def test_ac1_pilot_shape_two_commits_ahead_pushes_without_new_commit(tmp_path, monkeypatch):
    """AC1: 2 commits ahead of origin/main, no upstream, nothing modified, files_modified: [] -> push + PR."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "feat: one", "feat: two")
    head = rig.head()
    write_state(rig)
    ok_ship(rig)
    assert_pushed_and_one_pr(rig)
    assert "ship_complete: true" in state_text(rig)
    assert rig.head() == head, "ship.sh must not make a commit when nothing is staged"


def test_ac2_tracked_modification_not_listed_is_committed(tmp_path, monkeypatch):
    """AC2: tracked file modified, files_modified: [] -> ship.sh commits it, pushes, opens one PR."""
    rig = make_rig(tmp_path, monkeypatch)
    modify(rig)
    write_state(rig)
    ok_ship(rig)
    assert rig.head() != rig.head0, "ship.sh must make the commit"
    assert g(["show", "HEAD:feature.txt"], rig.repo).stdout == "branch\n"
    assert_pushed_and_one_pr(rig)


def test_ac3_tracked_deletion_not_listed_is_committed(tmp_path, monkeypatch):
    """AC3: a tracked file deleted and not listed -> the ship commit records the deletion."""
    rig = make_rig(tmp_path, monkeypatch)
    (rig.repo / "README").unlink()
    write_state(rig)
    ok_ship(rig)
    assert rig.head() != rig.head0
    assert "README" not in tree_files(rig), "the ship commit must record the deletion"
    assert "feature.txt" in tree_files(rig)
    assert_pushed_and_one_pr(rig)


def test_ac4_untracked_unlisted_files_stay_out_of_every_commit(tmp_path, monkeypatch):
    """AC4: untracked new.txt and build-plan-review-cycle1.md (not listed) are in no commit."""
    rig = make_rig(tmp_path, monkeypatch)
    modify(rig)  # something real to ship
    (rig.repo / "new.txt").write_text("new\n")
    (rig.repo / "build-plan-review-cycle1.md").write_text("leftover\n")
    write_state(rig)
    ok_ship(rig)
    assert g(["show", "HEAD:feature.txt"], rig.repo).stdout == "branch\n", "tracked change must ship"
    assert_pushed_and_one_pr(rig)
    committed = all_committed_paths(rig)
    assert "new.txt" not in committed and "build-plan-review-cycle1.md" not in committed, committed
    assert (rig.repo / "new.txt").exists() and (rig.repo / "build-plan-review-cycle1.md").exists()


def test_ac4_listed_untracked_is_committed_unlisted_stay_out(tmp_path, monkeypatch):
    """AC4: new.txt listed in files_modified -> committed; the cycle leftover and an unlisted tracked edit:
    leftover out, tracked edit in."""
    rig = make_rig(tmp_path, monkeypatch)
    modify(rig)
    (rig.repo / "new.txt").write_text("new\n")
    (rig.repo / "build-plan-review-cycle1.md").write_text("leftover\n")
    write_state(rig, files=["new.txt"])
    ok_ship(rig)
    assert_pushed_and_one_pr(rig)
    files = tree_files(rig)
    assert "new.txt" in files
    assert "build-plan-review-cycle1.md" not in all_committed_paths(rig)
    assert g(["show", "HEAD:feature.txt"], rig.repo).stdout == "branch\n", "unlisted tracked edit must ship too"


def test_ac5_sensitive_modified_files_are_skipped_others_committed(tmp_path, monkeypatch):
    """AC5: tracked .env and config/app.env modified -> not staged, SKIP lines on stdout; feature.txt committed."""
    rig = make_rig(tmp_path, monkeypatch, seed_files={".env": "SECRET=1\n", "config/app.env": "K=1\n"})
    modify(rig, ".env", "SECRET=2\n")
    modify(rig, "config/app.env", "K=2\n")
    modify(rig)
    write_state(rig)
    proc = ok_ship(rig)
    assert "SKIP (sensitive): .env" in proc.stdout.splitlines(), proc.stdout
    assert "SKIP (sensitive): config/app.env" in proc.stdout.splitlines(), proc.stdout
    assert "STAGE (tracked change): .env" not in proc.stdout.splitlines(), proc.stdout
    assert g(["show", "HEAD:feature.txt"], rig.repo).stdout == "branch\n"
    assert g(["show", "HEAD:.env"], rig.repo).stdout == "SECRET=1\n", ".env must not be committed"
    assert g(["show", "HEAD:config/app.env"], rig.repo).stdout == "K=1\n"
    assert (rig.repo / ".env").read_text() == "SECRET=2\n", "the working-tree edit is left alone"
    assert_pushed_and_one_pr(rig)


def test_ac25_non_ascii_and_spaced_tracked_paths_are_staged_unquoted(tmp_path, monkeypatch):
    """AC25: tracked `docs/ünï.txt` and `a b.txt` modified, not listed -> both in the ship commit,
    `STAGE (tracked change): docs/ünï.txt` (unquoted) on stdout."""
    uni = "docs/ünï.txt"
    rig = make_rig(tmp_path, monkeypatch, seed_files={uni: "a\n", "a b.txt": "a\n"})
    modify(rig, uni, "b\n")
    modify(rig, "a b.txt", "b\n")
    write_state(rig)
    proc = ok_ship(rig)
    assert rig.head() != rig.head0
    assert g(["show", f"HEAD:{uni}"], rig.repo).stdout == "b\n"
    assert g(["show", "HEAD:a b.txt"], rig.repo).stdout == "b\n"
    assert f"STAGE (tracked change): {uni}" in proc.stdout.splitlines(), proc.stdout
    assert "STAGE (tracked change): a b.txt" in proc.stdout.splitlines(), proc.stdout
    assert_pushed_and_one_pr(rig)


def test_ac26_untracked_unlisted_file_is_reported_not_shipped(tmp_path, monkeypatch):
    """AC26: tracked x.txt modified + untracked new.txt not listed -> x.txt committed, new.txt in no commit,
    stderr `WARNING: untracked files not shipped: new.txt`."""
    rig = make_rig(tmp_path, monkeypatch, seed_files={"x.txt": "x\n"})
    modify(rig, "x.txt", "y\n")
    (rig.repo / "new.txt").write_text("new\n")
    write_state(rig)
    proc = ok_ship(rig)
    assert g(["show", "HEAD:x.txt"], rig.repo).stdout == "y\n"
    assert "new.txt" not in all_committed_paths(rig)
    assert "WARNING: untracked files not shipped: new.txt" in proc.stderr.splitlines(), proc.stderr
    assert_pushed_and_one_pr(rig)


def test_ac27_detached_head_is_refused_before_any_mutation(tmp_path, monkeypatch):
    """AC27: detached HEAD with a modified tracked file -> exit 1, `ERROR: detached HEAD`, no PUSH, no commit,
    nothing staged."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": True}})  # not approved
    g(["checkout", "-q", "--detach"], rig.repo)
    modify(rig)
    rig.clear_log()
    head = rig.head()
    write_state(rig)
    proc = run_ship(rig)
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stderr!r}"
    assert "ERROR: detached HEAD" in proc.stderr, proc.stderr
    assert rig.gh_calls() == [], "the refusal precedes the readiness check (no gh call)"
    assert rig.pushes() == [] and rig.pr_creates() == []
    assert rig.head() == head, "no commit"
    assert g(["diff", "--cached", "--quiet"], rig.repo, check=False).returncode == 0, "nothing staged"


def test_ac30_unmerged_paths_are_refused_before_readiness(tmp_path, monkeypatch):
    """AC30: a merge conflict left in the index -> exit 1, `ERROR: unmerged paths`, no PUSH, no commit, no gh call."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": True}})  # not approved
    commit_file(rig, "feature.txt", "ours\n", "ours")
    g(["checkout", "-q", "-b", "other", "main"], rig.repo)
    commit_file(rig, "feature.txt", "theirs\n", "theirs")
    g(["checkout", "-q", BRANCH], rig.repo)
    merge = g(["merge", "-q", "other"], rig.repo, check=False)
    assert merge.returncode != 0, "fixture must leave a conflicted merge"
    assert g(["ls-files", "-u"], rig.repo).stdout.strip(), "fixture must leave unmerged index entries"
    rig.clear_log()
    head = rig.head()
    write_state(rig)
    proc = run_ship(rig)
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stderr!r}"
    assert "ERROR: unmerged paths" in proc.stderr, proc.stderr
    assert rig.gh_calls() == [], "the refusal precedes the readiness check (no gh call)"
    assert rig.pushes() == [] and rig.pr_creates() == []
    assert rig.head() == head, "no commit"


def test_ac6_nothing_to_ship_warns_and_touches_nothing(tmp_path, monkeypatch):
    """AC6: nothing staged, 0 ahead -> exit 0, `WARNING: nothing to ship`, no push, no PR, state untouched."""
    rig = make_rig(tmp_path, monkeypatch)
    write_state(rig)
    before = rig.state_yaml.read_bytes()
    proc = ok_ship(rig)
    assert "WARNING: nothing to ship" in proc.stderr, proc.stderr
    assert "refs/bd/policy" in proc.stderr, f"the warning must name the resolved base: {proc.stderr!r}"
    assert rig.pushes() == [] and rig.pr_creates() == []
    assert rig.state_yaml.read_bytes() == before, "build-state.yaml must stay byte-identical"


def test_ac7_upstream_wins_over_origin_main_then_new_commit_pushes(tmp_path, monkeypatch):
    """AC7: upstream == HEAD while origin/main is behind -> nothing to ship; one more commit -> PUSH."""
    rig = make_rig(tmp_path, monkeypatch)
    commit_file(rig, "a.txt", "a\n", "work a")
    g(["push", "-q", "-u", "origin", BRANCH], rig.repo)  # upstream = already-pushed same-name branch
    rig.clear_log()
    write_state(rig)
    before = rig.state_yaml.read_bytes()
    proc = ok_ship(rig)
    assert "WARNING: nothing to ship" in proc.stderr, proc.stderr
    assert "@{upstream}" in proc.stderr, f"the warning must name the upstream base: {proc.stderr!r}"
    assert rig.pushes() == [] and rig.pr_creates() == [], "upstream (not origin/main) is the base"
    assert rig.state_yaml.read_bytes() == before
    commit_file(rig, "b.txt", "b\n", "work b")
    rig.clear_log()
    ok_ship(rig)
    assert_pushed_and_one_pr(rig)


def test_ac8_required_true_approved_ahead_still_pushes(tmp_path, monkeypatch):
    """AC8 (guard): required:true + approved, AC1 shape -> still pushes (bd#117 AC-A12a rows 1-3)."""
    rig = make_rig(tmp_path, monkeypatch, policy={"readiness": {"required": True}})
    ahead(rig, "feat: one", "feat: two")
    rig.approve()
    write_state(rig)
    ok_ship(rig)
    assert rig.pushes() == [f"refs/heads/{BRANCH}"]
    assert len(rig.pr_creates()) == 1
    assert "ship_complete: true" in state_text(rig)


# =========================================================================== AC9-AC13: title / commit subject


def test_ac9_title_is_h1_with_project_prefix(tmp_path, monkeypatch):
    """AC9: H1 `bd#7 — Make the widget fast`, commits `bd#7: RED ...`, `bd#7: GREEN ...` -> `bd#7: Make the widget fast`."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "bd#7: RED tests", "bd#7: GREEN impl")
    write_spec(rig, "# bd#7 — Make the widget fast\n\nBody.\n")
    write_state(rig, task="a long rambling task string")
    ok_ship(rig)
    assert pr_title(rig) == "bd#7: Make the widget fast"


def test_ac10_mixed_prefixes_leave_h1_unchanged(tmp_path, monkeypatch):
    """AC10: commits `feat: x` and `fix: y` (mixed) -> title is the H1 text unchanged."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "feat: x", "fix: y")
    write_spec(rig, "# Add a widget\n\nBody.\n")
    write_state(rig, task="the task")
    ok_ship(rig)
    assert pr_title(rig) == "Add a widget"


def test_ac10_common_prefix_is_added_to_bare_h1(tmp_path, monkeypatch):
    """AC10: H1 `Add a widget`, commits all `feat: ...` -> `feat: Add a widget`."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "feat: x", "feat: y")
    write_spec(rig, "# Add a widget\n\nBody.\n")
    write_state(rig, task="the task")
    ok_ship(rig)
    assert pr_title(rig) == "feat: Add a widget"


def test_ac11_no_spec_title_is_task(tmp_path, monkeypatch):
    """AC11: no spec file -> the PR title is `task` (and ship still happens)."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    assert not (rig.root / "build-spec.md").exists()
    write_state(rig, task="Plain task title")
    ok_ship(rig)
    assert pr_title(rig) == "Plain task title"


def test_ac11_spec_path_relative_to_state_dir_is_honoured(tmp_path, monkeypatch):
    """AC11: `spec_path: "other/my-spec.md"` next to the state file (state outside the repo) is read."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    write_spec(rig, "# Custom Title From Spec\n\nBody.\n", rel="other/my-spec.md")
    write_state(rig, task="the task", extra='spec_path: "other/my-spec.md"\n')
    ok_ship(rig)
    assert pr_title(rig) == "Custom Title From Spec"


def test_ac12_long_h1_is_cut_at_a_word_boundary(tmp_path, monkeypatch):
    """AC12: 250-char H1 -> title <= 72 chars, ends on a word boundary, no trailing `,;:-` or space."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    words, i = [], 0
    while len(" ".join(words)) < 250:
        words.append(f"w{i:06d},")
        i += 1
    h1 = " ".join(words)
    assert len(h1) >= 250
    write_spec(rig, f"# {h1}\n\nBody.\n")
    write_state(rig, task="the task")
    ok_ship(rig)
    title = pr_title(rig)
    assert 0 < len(title) <= 72, (len(title), title)
    assert not re.search(r"[,;:—–\- ]$", title), title
    assert h1.startswith(title), "the title is a prefix of the H1"
    assert h1[len(title)] in " ,;:—–-", f"cut must fall on a word boundary: {title!r}"


def test_ac12_exactly_72_char_h1_is_unchanged(tmp_path, monkeypatch):
    """AC12: an H1 of exactly 72 characters is used as is."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    h1 = ("abcdefgh " * 9)[:71] + "z"
    assert len(h1) == 72 and not h1.endswith(" ")
    write_spec(rig, f"# {h1}\n\nBody.\n")
    write_state(rig, task="the task")
    ok_ship(rig)
    assert pr_title(rig) == h1


def test_ac12_single_80_char_word_is_hard_cut_at_72(tmp_path, monkeypatch):
    """AC12: a single 80-character word is hard-cut at 72."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    word = "x" * 80
    write_spec(rig, f"# {word}\n\nBody.\n")
    write_state(rig, task="the task")
    ok_ship(rig)
    assert pr_title(rig) == "x" * 72


def test_ac28_sensitive_spec_path_is_treated_as_no_spec(tmp_path, monkeypatch):
    """AC28: `spec_path: .env` holding `# Scope` + `SECRET=abc` -> title = task, body has no SECRET, no `## Scope`."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    write_spec(rig, "# Env title\n\n## Scope\n\nSECRET=abc\n", rel=".env")
    write_state(rig, task="Plain task", extra="spec_path: .env\n")
    ok_ship(rig)
    assert pr_title(rig) == "Plain task" and pr_title(rig) != "Env title"
    body = pr_body(rig)
    assert "SECRET" not in body and "## Scope" not in body, body
    assert body_lines(rig) == [FIXED, MARK]


def test_ac28_fenced_h1_and_fenced_scope_heading_are_ignored(tmp_path, monkeypatch):
    """AC28: an H1 inside a ``` fence before the real H1 is skipped; a `## Scope` line inside a fence is no section."""
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    spec = ("```\n# Fake Title In Fence\n```\n\n# Real Title\n\n## Overview\n\ntext\n\n"
            "```\n## Scope\nzzfencedsecret\n```\n")
    write_spec(rig, spec)
    write_state(rig, task="Plain task")
    ok_ship(rig)
    assert pr_title(rig) == "Real Title"
    body = pr_body(rig)
    assert "zzfencedsecret" not in body and "## Scope" not in body, body
    assert body_lines(rig) == [FIXED, MARK]


def test_ac13_commit_subject_equals_pr_title_and_task_in_body(tmp_path, monkeypatch):
    """AC13: ship.sh commits (AC2 shape) with a spec present -> subject == PR title; `task` in the commit body."""
    rig = make_rig(tmp_path, monkeypatch)
    modify(rig)
    write_spec(rig, "# Make the widget fast\n\nBody.\n")
    write_state(rig, task="Add the feature")
    ok_ship(rig)
    assert rig.head() != rig.head0, "ship.sh must have committed"
    subject = g(["log", "-1", "--format=%s"], rig.repo).stdout.strip()
    body = g(["log", "-1", "--format=%b"], rig.repo).stdout
    assert subject == "Make the widget fast"
    assert subject == pr_title(rig)
    assert "Add the feature" in body, body


def test_ac13_task_equal_to_title_commit_has_no_body(tmp_path, monkeypatch):
    """AC13: task == title -> ship.sh commits with the subject only (no body)."""
    rig = make_rig(tmp_path, monkeypatch)
    modify(rig)
    write_spec(rig, "# Add the feature\n\nBody.\n")
    write_state(rig, task="Add the feature")
    ok_ship(rig)
    assert rig.head() != rig.head0, "ship.sh must have committed"
    assert g(["log", "-1", "--format=%s"], rig.repo).stdout.strip() == "Add the feature"
    assert g(["log", "-1", "--format=%b"], rig.repo).stdout.strip() == "", "no body when task == title"
    assert pr_title(rig) == "Add the feature"


# =========================================================================== AC14-AC18: PR body

PILOT_STATE_EXTRA = """plan_review: pass
plan_review_cycles: 2
plan_review_concerns:
  - concern one
  - concern two
opus_validation: approved
opus_validation_cycles: 1
phase_6_reviewer_verdicts: {a: PASS, b: PASS}
review_satisfaction: satisfied
phase_6_satisfaction: {a: 9, b: 8}
pre_existing_findings: ["pe one", "pe two"]
follow_ups:
  - follow one
  - follow two
"""

PILOT_SPEC = """# Build the widget

## Overview

Not a scope section.

### Scope IN

- in1
- in2

### Scope OUT

- out1

### Follow-ups

- spec follow

## Other

stuff
"""


def _ship_with_evidence(tmp_path, monkeypatch, *, spec: str, extra: str) -> Rig:
    rig = make_rig(tmp_path, monkeypatch)
    ahead(rig, "work one", "work two")
    write_spec(rig, spec)
    write_state(rig, task="Build the widget", extra=extra)
    ok_ship(rig)
    return rig


def test_ac14_body_carries_scope_review_and_followups(tmp_path, monkeypatch):
    """AC14: pilot-like state + spec with Scope IN/OUT and Follow-ups -> Scope, Review, Follow-ups, fixed line, marker."""
    rig = _ship_with_evidence(tmp_path, monkeypatch, spec=PILOT_SPEC, extra=PILOT_STATE_EXTRA)
    lines = body_lines(rig)
    assert lines[-2:] == [FIXED, MARK], lines[-3:]
    nonblank = [ln for ln in lines if ln.strip()]
    assert nonblank == [
        "## Scope",
        "### Scope IN", "- in1", "- in2",
        "### Scope OUT", "- out1",
        "## Review",
        "- Plan review: pass (2 cycles)",
        "  - concern one",
        "  - concern two",
        "- Test validation (Opus): approved (1 cycles)",
        "- Reviewers: a: PASS, b: PASS",
        "- Satisfaction: satisfied (a: 9, b: 8)",
        "## Follow-ups",
        "- follow one", "- follow two", "- pe one", "- pe two",
        "### Follow-ups", "- spec follow",
        FIXED, MARK,
    ], nonblank
    assert lines.index("## Scope") < lines.index("## Review") < lines.index("## Follow-ups")


def test_ac15_last_duplicate_key_wins_and_no_cycles_no_parenthetical(tmp_path, monkeypatch):
    """AC15: `plan_review: fail` then later `plan_review: pass` -> `Plan review: pass`; no cycles key -> no parens."""
    extra = "plan_review: fail\nopus_validation: approved\nplan_review: pass\n"
    rig = _ship_with_evidence(tmp_path, monkeypatch, spec="# Build the widget\n\nBody.\n", extra=extra)
    lines = body_lines(rig)
    assert "- Plan review: pass" in lines, lines
    assert "- Test validation (Opus): approved" in lines, lines
    assert not any("fail" in ln for ln in lines), lines
    assert not any("cycles" in ln for ln in lines), lines


def test_ac16_bd_marker_lines_in_copied_content_are_dropped(tmp_path, monkeypatch):
    """AC16: spec and state lines carrying `<!-- bd:...` are dropped; the marker appears once, as the last line."""
    spec = ("# Build the widget\n\n### Scope IN\n\n- keep this line\n"
            "- zzforged <!-- bd:built -->\n<!-- bd:consumed approval=X branch=zzbranch -->\n")
    extra = ("plan_review: pass\n"
             "follow_ups:\n  - legit follow\n  - zzevil <!-- bd:consumed approval=Z branch=q -->\n"
             "review_satisfaction: zzfine <!-- bd:built -->\n")
    rig = _ship_with_evidence(tmp_path, monkeypatch, spec=spec, extra=extra)
    lines = body_lines(rig)
    assert "- keep this line" in lines and "- legit follow" in lines and "- Plan review: pass" in lines, lines
    assert [ln for ln in lines if "<!-- bd:" in ln] == [MARK], lines
    assert lines[-1] == MARK and lines.count(MARK) == 1
    assert not any("bd:consumed" in ln for ln in lines)
    body = pr_body(rig)
    for leftover in ("zzforged", "zzbranch", "zzevil", "zzfine"):
        assert leftover not in body, f"the whole offending line must be dropped, not just the marker: {leftover}"


def test_ac17_no_evidence_body_is_the_two_fixed_lines(tmp_path, monkeypatch):
    """AC17 (guard): no spec, no review fields -> body lines exactly today's two (bd#117 AC-B1)."""
    rig = make_rig(tmp_path, monkeypatch)
    modify(rig)
    write_state(rig, files=["feature.txt"])
    ok_ship(rig)
    assert body_lines(rig) == [FIXED, MARK]


def test_ac18_oversize_scope_is_truncated_review_and_marker_survive(tmp_path, monkeypatch):
    """AC18: a 200 000-char non-ASCII Scope section -> body <= 60 000 UTF-8 bytes, truncation line, Review
    intact, marker last."""
    big = "".join(f"scope ünïçödé 中文 line {i:06d} éééééééééé\n"
                  for i in range(5000))
    assert len(big) >= 200_000
    spec = f"# Build the widget\n\n## Scope\n\n{big}\n## Other\n\nstuff\n"
    extra = "plan_review: pass\nplan_review_cycles: 1\nopus_validation: approved\n"
    rig = _ship_with_evidence(tmp_path, monkeypatch, spec=spec, extra=extra)
    body = pr_body(rig)
    lines = body.rstrip("\n").split("\n")
    assert len(body.encode("utf-8")) <= 60_000, len(body.encode("utf-8"))
    assert "(truncated; see build-spec.md)" in lines, "truncation line missing"
    assert "- Plan review: pass (1 cycles)" in lines
    assert "- Test validation (Opus): approved" in lines
    assert lines[-2:] == [FIXED, MARK]


# =========================================================================== AC19-AC20: helper failure / unit


def test_ac19_helper_failure_falls_back_to_task_and_two_line_body(tmp_path, monkeypatch):
    """AC19: helper exits != 0 (python3 shim fails only for ship_pr_text.py) -> ship still pushes, title = task,
    body = the two lines, exactly one `WARNING: ship_pr_text failed` line, no Traceback."""
    rig = make_rig(tmp_path, monkeypatch)
    shimdir = rig.root / "pyshim"
    shimdir.mkdir()
    _write_exec(shimdir / "python3", PY_SHIM.replace("__PY__", sys.executable))
    monkeypatch.setenv("PATH", f"{shimdir}{os.pathsep}{os.environ['PATH']}")
    ahead(rig, "feat: one", "feat: two")
    write_spec(rig, "# Spec title that must not be used\n\n### Scope IN\n\n- in1\n")
    write_state(rig, task="Fallback task title", extra="plan_review: pass\n")
    proc = ok_ship(rig)
    assert_pushed_and_one_pr(rig)
    assert pr_title(rig) == "Fallback task title"
    assert body_lines(rig) == [FIXED, MARK]
    warns = [ln for ln in proc.stderr.splitlines() if "WARNING: ship_pr_text failed" in ln]
    assert len(warns) == 1, proc.stderr
    assert "Traceback" not in proc.stderr and "Traceback" not in proc.stdout


def test_ac29_body_without_marker_falls_back_title_keeps_first_line(tmp_path, monkeypatch):
    """AC29: a helper `body` whose last line is not the marker -> two-line body + warning; a two-line `title`
    output -> only the first line is used (and that call is not a failure: exactly one warning)."""
    rig = make_rig(tmp_path, monkeypatch)
    shimdir = rig.root / "pyshim"
    shimdir.mkdir()
    _write_exec(shimdir / "python3", PY_SHIM_BAD_OUTPUT.replace("__PY__", sys.executable))
    monkeypatch.setenv("PATH", f"{shimdir}{os.pathsep}{os.environ['PATH']}")
    ahead(rig, "work one", "work two")
    write_spec(rig, "# Spec title\n\n### Scope IN\n\n- in1\n")
    write_state(rig, task="Task title", extra="plan_review: pass\n")
    proc = ok_ship(rig)
    assert_pushed_and_one_pr(rig)
    assert pr_title(rig) == "Shim Title"
    assert body_lines(rig) == [FIXED, MARK]
    warns = [ln for ln in proc.stderr.splitlines() if "WARNING: ship_pr_text failed" in ln]
    assert len(warns) == 1, proc.stderr


def _need_helper() -> None:
    assert HELPER.is_file(), f"scripts/ship_pr_text.py does not exist yet: {HELPER}"


def test_ac20_helper_exits_0_on_task_only_state(tmp_path):
    """AC20: `title` and `body` exit 0 on a state with only `task:` (subprocess)."""
    _need_helper()
    state = tmp_path / "build-state.yaml"
    state.write_text("task: Just a task\n")
    title = subprocess.run([sys.executable, str(HELPER), "title", "--state", str(state)], cwd=str(tmp_path),
                           capture_output=True, text=True, timeout=TIMEOUT)
    assert title.returncode == 0, title.stderr
    assert title.stdout.rstrip("\n") == "Just a task"
    body = subprocess.run([sys.executable, str(HELPER), "body", "--state", str(state)], cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=TIMEOUT)
    assert body.returncode == 0, body.stderr
    assert body.stdout.rstrip("\n").split("\n") == [FIXED, MARK]


def test_ac20_helper_imports_are_stdlib_only():
    """AC20: AST scan of the helper: every import is a stdlib module."""
    _need_helper()
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            mods.add((node.module or "").split(".")[0])
    stdlib = getattr(sys, "stdlib_module_names", None) or {
        "__future__", "argparse", "json", "os", "re", "subprocess", "sys", "pathlib", "unicodedata",
        "typing", "shlex", "textwrap"}
    assert mods <= set(stdlib), f"non-stdlib imports: {sorted(mods - set(stdlib))}"


# =========================================================================== AC21-AC23: leftovers / docs


def test_ac21_gitignore_covers_cycle_files_and_session_file():
    """AC21: .gitignore lists both entries and git check-ignore confirms them in this repo."""
    lines = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert "build-*-cycle*.md" in lines, "missing .gitignore line build-*-cycle*.md"
    assert ".bytedigger-sessions.json" in lines, "missing .gitignore line .bytedigger-sessions.json"
    for path in ("build-plan-review-cycle1.md", "build-opus-validation-cycle2.md", ".bytedigger-sessions.json"):
        proc = subprocess.run(["git", "check-ignore", "-v", "--no-index", path], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"{path} is not ignored"
        assert proc.stdout.startswith(".gitignore:"), f"{path} must be ignored by .gitignore: {proc.stdout!r}"


def _section(text: str, start_pat: str, end_pat: str | None) -> str:
    m = re.search(start_pat, text, re.M)
    assert m, f"section {start_pat!r} not found"
    rest = text[m.start():]
    if end_pat:
        e = re.search(end_pat, rest[1:], re.M)
        if e:
            return rest[: e.start() + 1]
    return rest


def test_ac22_phase7_state_cleanup_names_cycle_files_and_session_file():
    """AC22: phase-7 State Cleanup deletes build-*-cycle*.md and .bytedigger-sessions.json."""
    text = (REPO_ROOT / "phases" / "phase-7-synthesize.md").read_text(encoding="utf-8")
    cleanup = _section(text, r"^## State Cleanup", r"^## ")
    assert "build-*-cycle*.md" in cleanup
    assert ".bytedigger-sessions.json" in cleanup


def test_ac22_phase7_ship_section_mentions_ahead():
    """AC22: phase-7 section 7.5 says ship.sh ships every commit *ahead* of the base."""
    text = (REPO_ROOT / "phases" / "phase-7-synthesize.md").read_text(encoding="utf-8")
    sec = _section(text, r"^## 7\.5 ", r"^## ")
    assert "ahead" in sec.lower(), "section 7.5 must mention commits ahead of the base"


def test_ac22_build_md_7_1c_mentions_ahead():
    """AC22: commands/build.md step 7.1c says ship.sh ships every commit *ahead* of the base."""
    text = (REPO_ROOT / "commands" / "build.md").read_text(encoding="utf-8")
    para = next((ln for ln in text.splitlines() if ln.startswith("**7.1c SHIP")), None)
    assert para is not None, "7.1c SHIP paragraph not found"
    assert "ahead" in para.lower(), "7.1c must mention commits ahead of the base"


def test_ac23_plugin_doc_lists_helper():
    """AC23: docs/plugin.md scripts table lists scripts/ship_pr_text.py and documents the STAGE line."""
    text = (REPO_ROOT / "docs" / "plugin.md").read_text(encoding="utf-8")
    assert "scripts/ship_pr_text.py" in text
    assert "STAGE (tracked change)" in text, "the 'What ship.sh ships' paragraph must mention the STAGE line"


def test_ac23_changelog_unreleased_mentions_bd131():
    """AC23: CHANGELOG.md Unreleased section mentions bd#131."""
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[Unreleased\]\s*$(.*?)(?=^## \[)", text, re.M | re.S)
    assert m, "Unreleased section not found"
    assert "bd#131" in m.group(1)
