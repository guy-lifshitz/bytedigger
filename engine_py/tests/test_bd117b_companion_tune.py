"""RED-phase tests -- bd#117 Part B: weekly companion tuning (`companion_tune`, `scripts/companion-tune`).

Spec (FROZEN): docs/decisions/2026-09-30-bd117-companion-tune-readiness-gate.md
(Part B = section 2 "Part B" op-B1..op-B4, section 4 rows AC-B1..AC-B10, the Part-B slice of AC-R).
Part A (readiness) lives in test_bd117a_readiness.py and is not covered here.

AC -> tests
-----------
AC-B1  both ship paths end the PR body with `<!-- bd:built -->`    test_ac_b1_*
AC-B2  collect: exactly the expected signals (11 in/out cases)     test_ac_b2_*
AC-B3  truncation / paging / GraphQL shape / failures              test_ac_b3_*
AC-B4  propose steps 1-2: no signals, open tuner PR                test_ac_b4_*
AC-B5  non-overridable section refused                             test_ac_b5_*
AC-B6  a second path refused (extra_path), bad paths               test_ac_b6_*
AC-B7  valid draft: one push, one pr create, real diff             test_ac_b7_*
AC-B8  source scan of companion_tune.py                            test_ac_b8_*
AC-B9  invalid current companion / checker crash / no overridable  test_ac_b9_*
AC-B10 examples/github-actions/companion-tune.yml                  test_ac_b10_*
AC-R   registry slice (four codes, manifest, docs, changelog, lint) test_ac_r_*
(usage errors of the CLI, op-B2/op-B3: test_ac_b2_usage_errors_exit_2)
The former Part A guard `test_ship_sh_pr_body_unchanged_by_part_a` was folded into AC-B1 and deleted
from test_bd117a_readiness.py (its assertion is exactly what op-B1 changes). Sibling amended by this lot for the
same reason: test_gh1124_ship_pr_title.py::test_ac16_ship_to_pr_body_uses_report_text_stripped now expects the
report text plus `\\n<!-- bd:built -->`.

Contract points pinned here (the spec leaves them open; GREEN implements exactly these)
----------------------------------------------------------------------------------------
CLI
* `scripts/companion-tune {collect|propose} ...` is a bash wrapper: PYTHONPATH=$(dirname "$0")/../engine_py,
  `exec python3 -m bytedigger_engine.companion_tune "$@"` (same shape as scripts/skill-companion).
* collect: `[--repo .] [--since <N>d] [--out <file>] [--plugin-root <dir>]`; `--since` default `7d`;
  without `--out` the JSON goes to stdout, with `--out` it is written to the file (stdout empty).
* propose: `--core <id> --signals <file> [--repo .] [--llm-command <cmd>] [--dry-run] [--plugin-root <dir>]`.
* `--plugin-root <dir>` default: $CLAUDE_PLUGIN_ROOT else the parent of the scripts dir (as skill-companion).
* Exit codes: 0 ok / no-op, 2 usage, 3 `E_COMPANION_TUNE_REFUSED <reason>` or
  `E_COMPANION_TUNE_DRAFT_INVALID <reason>`, 4 `E_COMPANION_TUNE_UNAVAILABLE <detail>` or
  `E_COMPANION_TUNE_TRUNCATED <which>`. Exactly ONE stderr line starting `E_COMPANION_TUNE_` per failure.
* Reason / detail tokens pinned: REFUSED `current_companion_invalid`, `extra_path`, else the first #116 reason
  (the second word of the first `E_SKILL_COMPANION_INVALID <reason> <path>` line); DRAFT_INVALID `no_blocks`
  (zero blocks or empty output), `unclosed_fence`, `bad_path`; UNAVAILABLE `malformed_tuning`,
  `bd_logins_required`, otherwise any non-empty detail; TRUNCATED `pr_list` | `issue_list` | `tuner_list`.
* `gh pr create` failing in step 7 => UNAVAILABLE (exit 4); the pushed tuner branch is left in place.
* stdout lines (exit 0): `no signals` (step 1), `skipped: open PR #<n>` (step 2), `nothing overridable`
  (step 3), on success the URL printed by `gh pr create`; `--dry-run` prints the unified diff.

Tuning config / policy read
* `tuning` is read from `bytedigger.json` on the default branch of the PUSH target (Part A policy read path:
  `git remote get-url --push --all origin`, ls-remote --symref, fetch). File absent or no `tuning` key =>
  all three lists empty. Unreadable policy => UNAVAILABLE (exit 4).
* `gh` = get_config().binary("HAL_GH_BIN", "gh"); every GitHub call carries `-R o/r` (or GraphQL owner/name)
  parsed from the push URL.

gh argv forms (`pr` / `issue` subcommands carry `-R <o>/<n>`; `gh api` has no `-R`: the repo is in the
path or in the GraphQL `owner` / `name` variables)
* `gh api user`                                  -> {"login": ...} (403 => bd_logins_required if tuning.bd_logins empty)
* `gh api repos/<o>/<n>/collaborators/<login>/permission` -> {"permission": admin|write|read|none}; 404 = not a
  maintainer; any other failure = exit 4.
* `gh pr list -R o/r --state all --search "updated:>=<from>" --limit 1000 --json <fields>`
* `gh issue list -R o/r --state all --search "updated:>=<from>" --limit 1000 --json <fields>`
* `gh pr list -R o/r --state all --search "head:bd/companion-tune-" --limit 1000 --json <fields>`
  (collect AND propose step 2; propose keeps state OPEN rows). `<from>` is `%Y-%m-%dT%H:%M:%SZ` (UTC).
  The fixture applies `head:` LOOSELY (substring match, like GitHub's token-based qualifier), so the
  author / head / `bd:tune` rule must be re-applied in code: a BD-authored PR whose head is
  `feature/bd/companion-tune-x` is NOT a tuner PR.
* `gh pr view <N> -R o/r --json <fields>` reads a PR discovered through a closing reference that is not in the
  window listing (its `bd:built` marker and author live in the PR body; GraphQL requests no body field).
* Timelines: `gh api graphql --paginate -f query=<Q> -f owner=o -f name=r -F number=<N>`: one connection per
  query, the query declares `$endCursor` and selects `pageInfo{hasNextPage endCursor}`. The fixture prints the
  pages exactly like real gh without `--slurp`: the page JSON documents CONCATENATED with no separator (a
  line-splitting parser breaks; use `json.JSONDecoder.raw_decode` in a loop, or pass `--slurp`, which the
  fixture also honours). Queries: issue `timelineItems(itemTypes:[REOPENED_EVENT,LABELED_EVENT,
  UNLABELED_EVENT]...)`, pullRequest `timelineItems(itemTypes:[LABELED_EVENT,UNLABELED_EVENT]...)`.
* Closing references (`closedByPullRequestsReferences(first:100, includeClosedPrs:true, ...)` on an issue,
  `closingIssuesReferences(...)` on a PR): either `--paginate` or the module's own cursor loop
  (`-f after=<cursor>` / `-f endCursor=<cursor>`, like op-A1) is accepted by the fixture. Their nodes carry
  only `number`; author / body / state come from the listings or `gh pr view`.
  No comment/body/review field is ever requested in GraphQL; event nodes carry
  `__typename id actor{login} createdAt` (+ `label{name}` on label events). `actor` may be null (deleted
  account): such an event is not by a maintainer and never crashes the run.
* `gh pr create -R o/r --head <branch> --base <default> --title <t> --body <b>` (no `--label`).

signals.json: {"window": [from, to], "signals": [{id, kind, pr, issue|None, label|None, actor, title, at,
action}]}. `action` (addendum) = "added" (LabeledEvent) | "removed" (UnlabeledEvent) for `relabeled`, None for
`reopened`.
`pr` = the BD-built PR number (always); `issue` = the issue number when the event is on an issue, else None;
`label` = label name (None for reopened); `actor` = login verbatim; `title` = title of the object the event is on
(issue title for an issue event, PR title for a PR event); `at` = the event `createdAt` verbatim;
`id` = `<kind>:<event node id>`. "BD-built" = PR author is a BD login (case-insensitive) and the body has a line
that is exactly `<!-- bd:built -->`. Tuner PR (for dedupe / step 2) = author is a BD login AND head starts with
`bd/companion-tune-` AND the body has a `<!-- bd:tune signals=... -->` line.

--llm-command: `shlex.split`, run WITHOUT a shell, the prompt on stdin. The prompt carries the overridable
section titles and bodies, the current companion (if any) and the signals (ids and titles). The model's stdout
may carry chatter; every file block is a line `<<<bd:file path=<repo-relative posix path>>>`, the file
text, then a line `<<<bd:end>>>`. Block text is written verbatim (each line LF-terminated). A path that is
absolute, empty, or has a `..` component => DRAFT_INVALID bad_path (nothing written, nothing branched).
Zero blocks / empty stdout => DRAFT_INVALID no_blocks; a block with no end line => DRAFT_INVALID unclosed_fence.

Addendum rules (code-review defects):
* Cross-repo closing PR: `closedByPullRequestsReferences` nodes select `number repository{nameWithOwner}`; a
  node whose `nameWithOwner` differs (case-insensitively) from the push-target `<o>/<n>` is skipped: no
  `gh pr view` for it, never a signal, and never UNAVAILABLE. (The fixture emits `repository` only when the
  query selects `nameWithOwner`.)
* Draft write safety: any file block whose path is not exactly `bytedigger/companions/<id>.md` =>
  `E_COMPANION_TUNE_REFUSED extra_path` BEFORE anything is written; the tuner commit runs with hooks disabled
  (`core.hooksPath` pointing at a tracked hook must not execute it).
* Default model (no `--llm-command`): argv is exactly `claude -p --model <get_claude_fallback()> --tools ""`
  (`--tools` immediately followed by the empty string), prompt on stdin, `claude` resolved from PATH.
* Prompt: every signal line (containing the signal id) carries kind, title, `#<pr>`, `#<issue>` when issue is
  set, and for `relabeled` the label name plus `added` / `removed` (the `action` field).
* `gh api user`: only a failure whose stderr contains `HTTP 403` is tolerated (then `tuning.bd_logins` must be
  non-empty). Any other failure (e.g. `HTTP 502`) => exit 4 UNAVAILABLE even with non-empty `bd_logins`.

Base: the tuner worktree is cut from the default branch of the PUSH target as just fetched (not from the user's
HEAD or local branches, and not from a stale `origin/<default>`); the user's branch, HEAD, index and working
tree are never touched. A draft identical to the current companion => exit 0, stdout `no change`, no push, no
PR, no branch left behind. A failing `git push` in step 7 => exit 4 UNAVAILABLE and `gh pr create` is NOT called.
No signals wins over an open tuner PR (step 1 precedes step 2). The readiness label is the policy's
`readiness.label` (default `plan-approved`), never a signal whatever `watched_labels` says.

Gate seam: the checker is run as `<plugin-root>/scripts/skill-companion check --core <id> --repo <dir>
--plugin-root <plugin-root>` when that file exists (else the `skill-companion` wrapper that sits next to
`scripts/companion-tune`; the tests only exercise the first form), once in step 3 against a worktree of the default branch and
once in step 6 against the tuner worktree -- never against the user's own working tree. The test plugin root carries
a transparent shim that counts calls and then `exec`s the REAL wrapper; it is only made to fail in the two
"checker crashed" rows of AC-B9. The real checker decides every other verdict.

Branch: `bd/companion-tune-<YYYYMMDD UTC>-<first 8 hex of sha256("\\n".join(sorted(ids)))>` (plain str sort,
no trailing newline, UTF-8). PR body: one line per signal (`- <kind> <link> <actor> <at>`, link = the issue URL
when `issue` is not None else the PR URL, `https://github.com/<o>/<n>/issues/<N>` | `/pull/<N>`), then the
final line `<!-- bd:tune signals=<id>,<id>... -->` with the ids sorted, comma-joined, no spaces. The body never
carries `bd:built`.

Section 1i: every contested state (the gh world, permission tables, checker call counters / crash switches,
model output) is pre-staged in files before the unit under test runs; nothing races. The only clock use is the
branch date, asserted against the UTC date before AND after the run. Section 1q: the module under test is
never imported at collection time; the CLI and the phase_8 step are reached lazily.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = REPO_ROOT / "engine_py"
SCRIPTS = REPO_ROOT / "scripts"
CLI = SCRIPTS / "companion-tune"
SHIP_SH = SCRIPTS / "ship.sh"
REAL_CHECKER = SCRIPTS / "skill-companion"
COMPANION_TUNE_PY = ENGINE_ROOT / "bytedigger_engine" / "companion_tune.py"

BD_USER = "bd-bot"
MARK_BUILT = "<!-- bd:built -->"
COMP_PATH = "bytedigger/companions/bytedigger.md"
TIMEOUT = 90
GIT_ID = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}
DEFAULT_TUNING = {
    "bd_logins": ["bd-helper"],
    "bot_logins": ["ci-bot"],
    "watched_labels": ["regression", "wontfix", "plan-approved"],
}
PERMS = {"alice": "admin", "bob": "write", "ci-bot": "write", "eve": "read", "carol": "none"}
BRANCH_RE = re.compile(r"bd/companion-tune-(\d{8})-([0-9a-f]{8})")

CORE_TEXT = (
    "---\nname: ByteDigger\ndescription: a core skill\nmetadata:\n"
    '  overridable: "project-conventions"\n---\n\n'
    "# Core\n\nIntro line.\n\n## Intro\n\nintro text\n\n"
    "## Project conventions\n\ncore conv line\n\n## Other\n\nother text\n"
)
CORE_TEXT_NO_OVERRIDABLE = CORE_TEXT.replace('metadata:\n  overridable: "project-conventions"\n', "")


# --------------------------------------------------------------------------- pure helpers

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ago(days: float = 0, hours: float = 0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def companion(body: str = "Prefer tabs over spaces.", extra: str = "") -> str:
    return f"---\nspecializes: bytedigger\n---\n# Companion\n\n## Project conventions\n\n{body}\n{extra}"


def fenced(*files, before: str = "Here is the updated companion:\n", after: str = "Done.\n") -> str:
    out = before
    for path, text in files:
        if not text.endswith("\n"):
            text += "\n"
        out += f"<<<bd:file path={path}>>>\n{text}<<<bd:end>>>\n"
    return out + after


def ev(typename: str, eid: str, actor: str, at: str, label: str | None = None) -> dict:
    e = {"__typename": typename, "id": eid, "actor": {"login": actor}, "createdAt": at}
    if label is not None:
        e["label"] = {"name": label}
    return e


BUILT_BODY = f"Built via ByteDigger.\n{MARK_BUILT}"


def pr_row(n, *, author=BD_USER, body=BUILT_BODY, state="MERGED", updated=None, head=None, closing=(),
           timeline=(), title=None) -> dict:
    return {
        "number": n, "title": title or f"PR {n} title", "url": f"https://github.com/o/r/pull/{n}",
        "state": state, "mergedAt": ago(20) if state == "MERGED" else None,
        "updatedAt": updated or ago(20), "createdAt": ago(25), "headRefName": head or f"gh{n}-x",
        "author": {"login": author}, "body": body, "closing": list(closing), "timeline": list(timeline),
    }


def issue_row(n, *, author="eve", state="OPEN", updated=None, closed_by=(), timeline=(), title=None) -> dict:
    return {
        "number": n, "title": title or f"Issue {n} title", "url": f"https://github.com/o/r/issues/{n}",
        "state": state, "updatedAt": updated or ago(hours=12), "createdAt": ago(30),
        "author": {"login": author}, "body": "issue body", "closedBy": list(closed_by),
        "timeline": list(timeline),
    }


def tuner_pr(n, *, author=BD_USER, head="bd/companion-tune-20260101-abcd1234", ids=("reopened:X",),
             state="OPEN", updated=None) -> dict:
    body = "- one line per signal\n" + f"<!-- bd:tune signals={','.join(ids)} -->"
    return pr_row(n, author=author, body=body, state=state, head=head, updated=updated or ago(10))


def sig(kind, ev_id, *, pr=10, issue=5, label=None, actor="alice", title="Some title", at=None,
        action=None) -> dict:
    return {"id": f"{kind}:{ev_id}", "kind": kind, "pr": pr, "issue": issue, "label": label,
            "actor": actor, "title": title, "at": at or ago(2), "action": action}


# --------------------------------------------------------------------------- fake gh

FAKE_GH = r'''#!__PY__
import json, re, sys

STATE = "__STATE__"
LOG = "__LOG__"
argv = sys.argv[1:]
stdin = sys.stdin.read() if "--input" in argv else None
with open(LOG, "a") as fh:
    fh.write(json.dumps({"k": "gh", "argv": argv, "stdin": stdin}) + "\n")
st = json.load(open(STATE))
sw = st.get("switch", {})


def die(msg, rc=1, out=None):
    if out is not None:
        print(out)
    print(msg, file=sys.stderr)
    sys.exit(rc)


def opt(name):
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None


def need_repo():
    repo = opt("-R") or opt("--repo")
    if sw.get("require_R") and repo != "o/r":
        die("gh: no -R o/r given (would fall back to the cwd's default repo)")
    if repo not in (None, "o/r"):
        die("gh: Could not resolve to a Repository with the name %r" % repo)


REAL_FIELDS = {"number", "title", "url", "state", "mergedAt", "updatedAt", "createdAt", "closedAt",
               "headRefName", "baseRefName", "author", "body", "labels", "comments", "reviews", "isDraft", "id"}
LISTY = {"comments", "reviews", "labels"}


def project(row, fields):
    return {f: row.get(f, [] if f in LISTY else None) for f in fields}


if argv[:2] == ["pr", "create"]:
    need_repo()
    if st.get("pr_create_rc"):
        die("gh: pr create failed", st["pr_create_rc"])
    print("https://github.com/o/r/pull/99")
    sys.exit(0)

if argv[:1] in (["pr"], ["issue"]) and argv[1:2] in (["list"], ["view"]):
    need_repo()
    if sw.get("list_fails"):
        die("gh: list failed")
    rows = st["prs"] if argv[0] == "pr" else st["issues"]
    fields = [f for f in (opt("--json") or "").split(",") if f]
    if not fields:
        die("gh: this fixture requires --json")
    for f in fields:
        if f not in REAL_FIELDS:
            die("Unknown JSON field: %r" % f)
    if argv[1] == "view":
        row = next((r for r in rows if str(r["number"]) == argv[2]), None)
        if row is None:
            die("gh: Could not resolve to a PullRequest with the number of %s." % argv[2])
        print(json.dumps(project(row, fields)))
        sys.exit(0)
    state = (opt("--state") or "open").lower()
    search = (opt("--search") or "").strip()
    head = opt("--head")
    limit = int(opt("--limit") or 30)
    out = []
    for r in rows:
        if state == "open" and r["state"] != "OPEN":
            continue
        if state == "closed" and r["state"] == "OPEN":
            continue
        if state == "merged" and r["state"] != "MERGED":
            continue
        if head and r.get("headRefName") != head:
            continue
        m = re.fullmatch(r"updated:>=(\S+)", search)
        if m and r["updatedAt"] < m.group(1):
            continue
        m = re.fullmatch(r"head:(\S+)", search)
        if m and m.group(1).rstrip("-/") not in r.get("headRefName", ""):
            continue  # loose, like GitHub's token-based qualifier
        out.append(project(r, fields))
    print(json.dumps(out[:limit]))
    sys.exit(0)

if argv[:1] != ["api"]:
    sys.exit(0)

path = argv[1].lstrip("/") if len(argv) > 1 else ""

if argv[1:] == ["user"]:
    if sw.get("user_403"):
        die("gh: Resource not accessible by integration (HTTP 403)", out='{"message":"forbidden"}')
    if sw.get("user_502"):
        die("gh: Bad Gateway (HTTP 502)", out='{"message":"Bad Gateway"}')
    print(json.dumps({"login": st["bd_user"]}))
    sys.exit(0)

m = re.fullmatch(r"repos/o/r/collaborators/([^/]+)/permission", path)
if m:
    login = m.group(1)
    if login.lower() in [x.lower() for x in sw.get("perm_500", [])]:
        die("gh: Server Error (HTTP 500)", out='{"message":"Server Error"}')
    perms = {k.lower(): v for k, v in st["perms"].items()}
    perm = perms.get(login.lower())
    if perm is None:
        die("gh: Not Found (HTTP 404)", out='{"message":"Not Found","status":"404"}')
    print(json.dumps({"permission": perm, "user": {"login": login}}))
    sys.exit(0)

if argv[1] == "graphql":
    if sw.get("gql_fails"):
        die("gh: graphql failed")
    opts = {}
    i = 2
    while i < len(argv):
        if argv[i] in ("-f", "-F", "--raw-field", "--field") and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            opts[k] = v
            i += 2
        else:
            i += 1
    if opts.get("owner") != "o" or opts.get("name") != "r":
        die("graphql: wrong or missing repository %r/%r" % (opts.get("owner"), opts.get("name")))
    q = opts.get("query", "")
    if re.search(r"\bissue\(number", q):
        root, rows = "issue", st["issues"]
    elif re.search(r"\bpullRequest\(number", q):
        root, rows = "pullRequest", st["prs"]
    else:
        die("graphql: unknown query")
    number = opts.get("number")
    if number is None:
        mm = re.search(r"\(number:\s*(\d+)", q)
        number = mm.group(1) if mm else None
    obj = next((r for r in rows if str(r["number"]) == number), None)
    if obj is None:
        die("graphql: Could not resolve to a %s with the number of %s." % (root, number))
    prs_by_n = {r["number"]: r for r in st["prs"]}
    if "closedByPullRequestsReferences(" in q:
        conn = "closedByPullRequestsReferences"
        inc = re.search(r"includeClosedPrs:\s*true", q) is not None
        items = []
        for ref in obj.get("closedBy", []):
            n, repo = (ref, "o/r") if isinstance(ref, int) else (ref["number"], ref["repo"])
            if repo == "o/r":
                if n not in prs_by_n or not (inc or prs_by_n[n]["state"] == "OPEN"):
                    continue
            elif not inc:
                continue
            node = {"number": n}
            if "nameWithOwner" in q:  # only when the query selects repository{nameWithOwner}
                node["repository"] = {"nameWithOwner": repo}
            items.append(node)
    elif "closingIssuesReferences(" in q:
        conn = "closingIssuesReferences"
        items = [{"number": n} for n in obj.get("closing", [])]
    elif "timelineItems(" in q:
        conn = "timelineItems"
        kinds = {"REOPENED_EVENT": "ReopenedEvent", "LABELED_EVENT": "LabeledEvent",
                 "UNLABELED_EVENT": "UnlabeledEvent"}
        mm = re.search(r"itemTypes:\s*\[([^\]]*)\]", q)
        wanted = {kinds[t.strip()] for t in mm.group(1).split(",") if t.strip() in kinds} if mm else None
        items = [e for e in obj.get("timeline", []) if wanted is None or e["__typename"] in wanted]
    else:
        die("graphql: unknown connection")

    def page_out(start):
        nxt = start + 100
        return {"data": {"repository": {root: {conn: {
            "nodes": items[start:nxt],
            "pageInfo": {"hasNextPage": nxt < len(items), "endCursor": "cur:%d" % nxt},
        }}}}}

    if "--paginate" in argv:
        if "$endCursor" not in q or "pageInfo" not in q:
            die("gh: --paginate needs $endCursor and pageInfo in the query")
        pages, start = [], 0
        while True:
            p = page_out(start)
            pages.append(p)
            if not p["data"]["repository"][root][conn]["pageInfo"]["hasNextPage"]:
                break
            start += 100
        if "--slurp" in argv:
            print(json.dumps(pages))
        else:
            # real gh: the page documents are concatenated, no separator
            sys.stdout.write("".join(json.dumps(p) for p in pages))
            sys.stdout.flush()
    else:
        cur = opts.get("endCursor") or opts.get("after")
        print(json.dumps(page_out(int(cur.split(":")[1]) if cur else 0)))
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

FAKE_MODEL = """#!__PY__
import json, sys
prompt = sys.stdin.read()
with open("__MLOG__", "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "n": len(prompt)}) + "\\n")
with open("__PROMPT__", "w") as fh:
    fh.write(prompt)
sys.stdout.write(open("__OUT__").read())
"""

CHECKER_SHIM = """#!/bin/sh
d="$(dirname "$0")"
n=$(cat "$d/.count" 2>/dev/null || echo 0)
n=$((n+1))
echo "$n" > "$d/.count"
echo "$*" >> "$d/.calls"
if [ -f "$d/.crash_at" ] && [ "$n" -ge "$(cat "$d/.crash_at")" ]; then
  echo "checker crashed" >&2
  exit "$(cat "$d/.crash_rc" 2>/dev/null || echo 1)"
fi
exec bash "__REAL__" "$@"
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


_DEFAULT = object()


class Rig:
    def __init__(self, root, repo, bare, log, state_file, gh, plugin):
        self.root, self.repo, self.bare = root, repo, bare
        self.log, self.state_file, self.gh, self.plugin = log, state_file, gh, plugin
        self.state_yaml = root / "build-state.yaml"
        self.model = root / "model"
        self.model_out = root / "model.out"
        self.model_log = root / "model.log"
        self.model_prompt = root / "model.prompt"
        self.head0 = g(["rev-parse", "HEAD"], repo).stdout.strip()

    # ---- gh world
    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def save(self, st: dict) -> None:
        self.state_file.write_text(json.dumps(st))

    def set_world(self, prs=(), issues=(), **extra) -> None:
        st = self.state()
        st["prs"], st["issues"] = list(prs), list(issues)
        st.update(extra)
        self.save(st)

    def switch(self, **kw) -> None:
        st = self.state()
        st["switch"].update(kw)
        self.save(st)

    # ---- model / checker
    def stage_model(self, text: str) -> None:
        self.model_out.write_text(text)

    def model_calls(self) -> list[dict]:
        if not self.model_log.exists():
            return []
        return [json.loads(x) for x in self.model_log.read_text().splitlines() if x.strip()]

    def prompt(self) -> str:
        return self.model_prompt.read_text()

    def checker_calls(self) -> list[str]:
        p = self.plugin / "scripts" / ".calls"
        return p.read_text().splitlines() if p.exists() else []

    def crash_checker_at(self, n: int, rc: int = 1) -> None:
        (self.plugin / "scripts" / ".crash_at").write_text(str(n))
        (self.plugin / "scripts" / ".crash_rc").write_text(str(rc))

    # ---- log oracle
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

    def set_push_url(self, url: str) -> None:
        g(["remote", "set-url", "--push", "origin", url], self.repo)

    # ---- repo state
    def local_tuner_branches(self) -> list[str]:
        return g(["for-each-ref", "--format=%(refname:short)", "refs/heads/bd/"], self.repo).stdout.split()

    def bare_tuner_branches(self) -> list[str]:
        return g(["for-each-ref", "--format=%(refname:short)", "refs/heads/bd/"], self.bare).stdout.split()

    def worktree_count(self) -> int:
        out = g(["worktree", "list", "--porcelain"], self.repo).stdout
        return sum(1 for ln in out.splitlines() if ln.startswith("worktree "))


def make_rig(tmp_path: Path, monkeypatch, *, bytedigger_json=_DEFAULT, default_files=None,
             core_text=CORE_TEXT, branch="main", change=None, require_R=True) -> Rig:
    root = Path(os.path.realpath(str(tmp_path)))
    # Hermetic git: no developer global/system config (hooksPath, pushInsteadOf, ...).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    bare = root / "bare.git"
    g(["init", "-q", "--bare", "-b", "main", str(bare)], root)
    seed = root / "seed"
    g(["init", "-q", "-b", "main", str(seed)], root)
    (seed / "README").write_text("init\n")
    (seed / "feature.txt").write_text("base\n")
    if bytedigger_json is _DEFAULT:
        bytedigger_json = {"tuning": DEFAULT_TUNING}
    if bytedigger_json is not None:
        text = bytedigger_json if isinstance(bytedigger_json, str) else json.dumps(bytedigger_json)
        (seed / "bytedigger.json").write_text(text)
    for rel, text in (default_files or {}).items():
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
        "bd_user": BD_USER, "switch": {"require_R": require_R}, "perms": PERMS, "prs": [], "issues": [],
    }))

    monkeypatch.setenv("GIT_SSH_COMMAND", str(ssh))
    monkeypatch.setenv("GIT_SSH_VARIANT", "simple")
    monkeypatch.setenv("HAL_GH_BIN", str(gh))
    for k in ("BD_GH_BIN", "BYTEDIGGER_GH_BIN", "CLAUDE_PLUGIN_ROOT", "HAL_BUILD_SHIP_PR",
              "BD_BUILD_SHIP_PR", "BYTEDIGGER_BUILD_SHIP_PR", "BD_SHIP_PR", "BYTEDIGGER_SHIP_PR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(k, raising=False)

    plugin = root / "plugin"
    (plugin / "skills" / "bytedigger").mkdir(parents=True)
    (plugin / "skills" / "bytedigger" / "SKILL.md").write_text(core_text)
    (plugin / "scripts").mkdir()
    _write_exec(plugin / "scripts" / "skill-companion",
                CHECKER_SHIM.replace("__REAL__", str(REAL_CHECKER)))

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

    rig = Rig(root, repo, bare, log, state_file, gh, plugin)
    _write_exec(rig.model, FAKE_MODEL.replace("__PY__", sys.executable)
                .replace("__MLOG__", str(rig.model_log)).replace("__PROMPT__", str(rig.model_prompt))
                .replace("__OUT__", str(rig.model_out)))
    rig.stage_model("")
    return rig


# --------------------------------------------------------------------------- runners


def _env(extra=None) -> dict:
    env = dict(os.environ)
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def assert_never_merges_or_labels(rig: Rig) -> None:
    """Whole-run oracle for op-B3 step 7 / AC-B8: no merge, no label add, no default-branch push."""
    for e in rig.gh_calls():
        a = e["argv"]
        assert a[:2] != ["pr", "merge"], f"a BD code path must never merge: {a!r}"
        assert "--add-label" not in a and "--label" not in a, f"label add: {a!r}"
        assert not any("addLabelsToLabelable" in x for x in a), f"label add: {a!r}"
        if "POST" in a or "PUT" in a:
            assert not any(re.search(r"/labels/?$", x) for x in a), f"label add: {a!r}"
    assert "refs/heads/main" not in rig.pushes(), "never pushes to the default branch"


def run_cli(rig: Rig, *args, env=None, cwd=None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["bash", str(CLI), *args], cwd=str(cwd or rig.repo), capture_output=True, text=True,
        timeout=TIMEOUT, env=_env(env),
    )
    assert_never_merges_or_labels(rig)
    return proc


def run_collect(rig: Rig, *extra, out=True):
    """Returns (proc, parsed signals.json or None)."""
    args = ["collect", "--repo", str(rig.repo), "--plugin-root", str(rig.plugin)]
    path = rig.root / "signals-out.json"
    if out:
        args += ["--out", str(path)]
    proc = run_cli(rig, *args, *extra)
    if out and path.exists():
        return proc, json.loads(path.read_text())
    return proc, None


def write_signals(rig: Rig, sigs, name="signals.json") -> Path:
    p = rig.root / name
    p.write_text(json.dumps({"window": [ago(7), ago(0)], "signals": list(sigs)}))
    return p


def run_propose(rig: Rig, sigs, *extra, model=True, signals_name="signals.json"):
    path = write_signals(rig, sigs, signals_name)
    args = ["propose", "--core", "bytedigger", "--signals", str(path), "--repo", str(rig.repo),
            "--plugin-root", str(rig.plugin)]
    if model:
        args += ["--llm-command", str(rig.model)]
    return run_cli(rig, *args, *extra)


def tune_lines(proc) -> list[str]:
    return [ln for ln in proc.stderr.splitlines() if ln.startswith("E_COMPANION_TUNE_")]


def assert_fail(proc, rc: int, code: str, reason: str | None = None) -> None:
    assert proc.returncode == rc, f"expected exit {rc}, got {proc.returncode}: {proc.stderr!r}"
    lines = tune_lines(proc)
    assert len(lines) == 1, f"exactly one E_COMPANION_TUNE_ stderr line expected: {proc.stderr!r}"
    if reason is None:
        assert re.fullmatch(code + r" \S.*", lines[0]), lines[0]
    else:
        assert lines[0] == f"{code} {reason}", lines[0]


def assert_nothing_shipped(rig: Rig) -> None:
    assert rig.pushes() == [], f"no push expected: {rig.pushes()!r}"
    assert rig.pr_creates() == []
    assert rig.local_tuner_branches() == [], "the tuner branch must not survive a refusal"
    assert rig.bare_tuner_branches() == []
    assert rig.worktree_count() == 1, "the temporary worktree must be removed"
    assert g(["status", "--porcelain"], rig.repo).stdout == "", "the user's working tree is never touched"
    assert g(["rev-parse", "HEAD"], rig.repo).stdout.strip() == rig.head0
    assert g(["symbolic-ref", "--short", "HEAD"], rig.repo).stdout.strip() == "main"


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def expected_hex(ids) -> str:
    return sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()[:8]


def tiny_world(rig: Rig, extra_events=()) -> None:
    rig.set_world(
        prs=[pr_row(10, closing=[5])],
        issues=[issue_row(5, closed_by=[10], timeline=[ev("ReopenedEvent", "RE_1", "alice", ago(2)),
                                                       *extra_events])],
    )


# =========================================================================== AC-B1


def _body_lines(argv) -> list[str]:
    return argv[argv.index("--body") + 1].rstrip("\n").split("\n")


def _write_build_state(rig: Rig) -> None:
    rig.state_yaml.write_text("task: Add the feature\nfiles_modified:\n  - feature.txt\n")


def _run_phase8(rig: Rig, **org):
    from bytedigger_engine.contracts import WorkflowContext
    from bytedigger_engine.workflows.phase_8_post_deploy import _ship_to_pr

    cfg = {"scratchpad_dir": str(rig.root / "scratch"), "working_dir": str(rig.repo),
           "ship_pr": True, **org}
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=cfg, question="ship",
        session_id="test-session", persona="hal", framework=None, domain=None,
    )
    return _ship_to_pr(ctx, None)


def test_ac_b1_ship_sh_pr_body_ends_with_built_marker(tmp_path, monkeypatch):
    """AC-B1 (ship.sh): the PR body is today's line plus ONE appended line `<!-- bd:built -->`."""
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json=None, branch="feature-x", change="modify",
                   require_R=False)
    _write_build_state(rig)
    proc = subprocess.run(["bash", str(SHIP_SH), "--pr", "--state", str(rig.state_yaml)], cwd=str(rig.repo),
                          capture_output=True, text=True, timeout=TIMEOUT, env=_env())
    assert proc.returncode == 0, proc.stderr
    creates = rig.pr_creates()
    assert len(creates) == 1, "ship.sh must reach `gh pr create` through the fixture gh"
    assert _body_lines(creates[0]["argv"]) == ["Built via ByteDigger /build pipeline.", MARK_BUILT]


def test_ac_b1_phase8_default_body_ends_with_built_marker(tmp_path, monkeypatch):
    """AC-B1 (phase_8 `_ship_to_pr`): the derived body keeps its text and gains the marker as its last line."""
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json=None, branch="feature-x", change="commit",
                   require_R=False)
    result = _run_phase8(rig)
    assert result.status == "ok", (result.status, result.error_code)
    creates = rig.pr_creates()
    assert len(creates) == 1
    lines = _body_lines(creates[0]["argv"])
    assert lines[-1] == MARK_BUILT and lines[:-1] == ["- feat: branch change"], lines


def test_ac_b1_phase8_ship_pr_body_override_also_gets_the_marker(tmp_path, monkeypatch):
    """AC-B1: with `ship_pr_body` set, the marker is appended AFTER the override."""
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json=None, branch="feature-x", change="commit",
                   require_R=False)
    result = _run_phase8(rig, ship_pr_body="Custom body from org config")
    assert result.status == "ok", (result.status, result.error_code)
    creates = rig.pr_creates()
    assert len(creates) == 1
    assert _body_lines(creates[0]["argv"]) == ["Custom body from org config", MARK_BUILT]


# =========================================================================== AC-B2

# case id -> (expected in the output, description)
B2_CASES = {
    "reopened:RE_1": (True, "maintainer reopen of an issue closed by a merged BD PR not updated in the window"),
    "reopened:RE_f": (False, "reopen, but the closing PR is human-authored with a forged bd:built"),
    "reopened:RE_2": (False, "reopen by the issue's non-maintainer author"),
    "reopened:RE_3": (False, "reopen by an actor whose permission lookup is 404"),
    "reopened:RE_old": (False, "maintainer reopen outside the window"),
    "relabeled:LE_1": (True, "maintainer relabel of a watched label on a BD PR"),
    "relabeled:LE_2": (False, "unwatched label"),
    "relabeled:LE_3": (False, "actor in bot_logins"),
    "relabeled:LE_4": (False, "the readiness label (plan-approved) is never a signal"),
    "reopened:RE_dup": (False, "id already in an earlier genuine bd:tune line"),
    "reopened:RE_forged": (True, "id in a forged bd:tune line from a non-BD author"),
    "reopened:RE_decoy": (True, "id in a BD-authored bd:tune line on a non-tuner head (head rule)"),
    "relabeled:UE_1": (True, "maintainer unlabel of a watched label"),
    "relabeled:LE_6": (True, "watched relabel on an issue closed by a BD PR (BD-Helper from tuning.bd_logins)"),
    "relabeled:LE_7": (True, "relabel reachable through both listings: reported once"),
    "reopened:RE_unmerged": (False, "closing BD PR was never merged"),
    "reopened:RE_nobd": (False, "issue closed by no BD PR"),
    "relabeled:LE_5": (False, "watched relabel by a maintainer outside the window"),
}


def b2_world(rig: Rig) -> None:
    prs = [
        pr_row(10, closing=[5]),
        pr_row(11, closing=[6]),
        pr_row(12, closing=[7]),
        pr_row(13, author="mallory", closing=[8]),  # forged marker, human author
        pr_row(14, closing=[9]),
        pr_row(15, state="OPEN", updated=ago(hours=12), closing=[26], timeline=[
            ev("LabeledEvent", "LE_1", "bob", ago(2), "regression"),
            ev("LabeledEvent", "LE_2", "bob", ago(2), "docs"),
            ev("LabeledEvent", "LE_3", "ci-bot", ago(2), "regression"),
            ev("LabeledEvent", "LE_4", "bob", ago(2), "plan-approved"),
            ev("LabeledEvent", "LE_5", "bob", ago(30), "regression"),
            ev("UnlabeledEvent", "UE_1", "alice", ago(3), "wontfix"),
            ev("AssignedEvent", "AE_1", "alice", ago(2)),
        ]),
        pr_row(17, author="BD-Helper", closing=[16]),
        pr_row(19, closing=[18]),
        tuner_pr(20, ids=("reopened:RE_dup",), updated=ago(10)),
        tuner_pr(21, author="mallory", head="bd/companion-tune-20260102-deadbeef",
                 ids=("reopened:RE_forged",)),
        pr_row(23, closing=[22]),
        pr_row(25, state="CLOSED", closing=[24]),
        # BD-authored bd:tune line on a NON-tuner head that the loose `head:` search still returns:
        # it must not dedupe (F11 head rule), so RE_decoy stays IN.
        tuner_pr(29, head="feature/bd/companion-tune-x", ids=("reopened:RE_decoy",), state="MERGED"),
        pr_row(31, closing=[30]),
    ]
    issues = [
        issue_row(30, closed_by=[31], timeline=[ev("ReopenedEvent", "RE_decoy", "alice", ago(2))]),
        issue_row(5, closed_by=[10], title="Issue five", timeline=[ev("ReopenedEvent", "RE_1", "alice", ago(2))]),
        issue_row(6, closed_by=[11], timeline=[ev("ReopenedEvent", "RE_2", "eve", ago(3))]),
        issue_row(7, closed_by=[12], timeline=[ev("ReopenedEvent", "RE_3", "ghost", ago(3))]),
        issue_row(8, closed_by=[13], timeline=[ev("ReopenedEvent", "RE_f", "alice", ago(2))]),
        issue_row(9, closed_by=[14], timeline=[ev("ReopenedEvent", "RE_old", "alice", ago(30))]),
        issue_row(16, closed_by=[17], title="Issue sixteen",
                  timeline=[ev("LabeledEvent", "LE_6", "alice", ago(2), "regression")]),
        issue_row(18, closed_by=[19], timeline=[ev("ReopenedEvent", "RE_dup", "alice", ago(2))]),
        issue_row(22, closed_by=[23], timeline=[ev("ReopenedEvent", "RE_forged", "alice", ago(2))]),
        issue_row(24, closed_by=[25], timeline=[ev("ReopenedEvent", "RE_unmerged", "alice", ago(2))]),
        issue_row(26, closed_by=[15], title="Issue twenty-six",
                  timeline=[ev("LabeledEvent", "LE_7", "bob", ago(2), "regression")]),
        issue_row(28, closed_by=[], timeline=[ev("ReopenedEvent", "RE_nobd", "alice", ago(2))]),
    ]
    rig.set_world(prs=prs, issues=issues)


B2_EXPECTED = {
    "reopened:RE_1": {"kind": "reopened", "pr": 10, "issue": 5, "label": None, "actor": "alice",
                      "title": "Issue five"},
    "reopened:RE_decoy": {"kind": "reopened", "pr": 31, "issue": 30, "label": None, "actor": "alice",
                          "title": "Issue 30 title"},
    "reopened:RE_forged": {"kind": "reopened", "pr": 23, "issue": 22, "label": None, "actor": "alice",
                           "title": "Issue 22 title"},
    "relabeled:LE_1": {"kind": "relabeled", "pr": 15, "issue": None, "label": "regression", "actor": "bob",
                       "title": "PR 15 title"},
    "relabeled:UE_1": {"kind": "relabeled", "pr": 15, "issue": None, "label": "wontfix", "actor": "alice",
                       "title": "PR 15 title"},
    "relabeled:LE_6": {"kind": "relabeled", "pr": 17, "issue": 16, "label": "regression", "actor": "alice",
                       "title": "Issue sixteen"},
    "relabeled:LE_7": {"kind": "relabeled", "pr": 15, "issue": 26, "label": "regression", "actor": "bob",
                       "title": "Issue twenty-six"},
}


def _check_b2_output(data: dict) -> None:
    ids = [s["id"] for s in data["signals"]]
    assert len(ids) == len(set(ids)), f"a signal must be reported once: {ids!r}"
    got = set(ids)
    bad = []
    for case_id, (expect_in, desc) in B2_CASES.items():
        if (case_id in got) != expect_in:
            bad.append(f"{case_id} should be {'IN' if expect_in else 'OUT'}: {desc}")
    assert not bad, "AC-B2 cases violated:\n  " + "\n  ".join(bad) + f"\n  got={sorted(got)}"
    assert got == set(B2_EXPECTED), f"exactly the expected signals: got {sorted(got)}"
    for s in data["signals"]:
        assert set(s) == {"id", "kind", "pr", "issue", "label", "actor", "title", "at", "action"}, s
        want = {"action": "added" if s["kind"] == "relabeled" else None, **B2_EXPECTED[s["id"]]}
        if s["id"] == "relabeled:UE_1":
            want["action"] = "removed"
        assert {k: s[k] for k in want} == want, (s, want)
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", s["at"]), s


def test_ac_b2_expected_signals_exactly(tmp_path, monkeypatch):
    """AC-B2: the full fixture (eleven spec cases plus six neighbours) yields exactly the expected signals,
    including the issue-listing path for a PR that was not updated in the window."""
    rig = make_rig(tmp_path, monkeypatch)
    b2_world(rig)
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert data is not None, "collect --out must write signals.json"
    _check_b2_output(data)


def test_ac_b2_window_is_since_seven_days_by_default(tmp_path, monkeypatch):
    """op-B2: `window` = [from, to], default --since 7d; `to` is now (UTC)."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig)
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    frm, to = (datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for x in data["window"])
    assert to - frm == timedelta(days=7)
    assert abs((datetime.now(timezone.utc) - to).total_seconds()) < 300
    assert [s["id"] for s in data["signals"]] == ["reopened:RE_1"]


def test_ac_b2_since_narrows_the_window(tmp_path, monkeypatch):
    """op-B2: `--since 1d` drops an event from two days ago even though its issue is in the listing."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig)
    proc, data = run_collect(rig, "--since", "1d")
    assert proc.returncode == 0, proc.stderr
    assert data["signals"] == []
    frm, to = (datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for x in data["window"])
    assert to - frm == timedelta(days=1)


def test_ac_b2_without_out_the_json_goes_to_stdout(tmp_path, monkeypatch):
    """Pinned CLI: no `--out` => signals.json on stdout."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig)
    proc, _ = run_collect(rig, out=False)
    assert proc.returncode == 0, proc.stderr
    assert [s["id"] for s in json.loads(proc.stdout)["signals"]] == ["reopened:RE_1"]


def test_ac_b2_bd_logins_from_config_when_api_user_is_403(tmp_path, monkeypatch):
    """op-B2 terms: under a workflow token `gh api user` is 403; a non-empty tuning.bd_logins is then
    enough and the result is unchanged."""
    tuning = {**DEFAULT_TUNING, "bd_logins": [BD_USER, "bd-helper"]}
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json={"tuning": tuning})
    b2_world(rig)
    rig.switch(user_403=True)
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    _check_b2_output(data)


def test_ac_b2_no_tuning_config_means_no_relabeled_signals(tmp_path, monkeypatch):
    """op-B2: absent `tuning` (here: no bytedigger.json at all) => watched_labels empty => no relabeled
    signals, and only `gh api user` identifies the BD account."""
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json=None)
    b2_world(rig)
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    got = {s["id"] for s in data["signals"]}
    assert got == {"reopened:RE_1", "reopened:RE_forged", "reopened:RE_decoy"}, got
    assert not any(i.startswith("relabeled:") for i in got)


def test_ac_b2_logins_compare_case_insensitively(tmp_path, monkeypatch):
    """op-B2 terms: logins compare case-insensitively (PR author `BD-BOT` is the BD user; actor `ALICE` has
    alice's permission; bot_logins `CI-BOT` matches `ci-bot`)."""
    tuning = {**DEFAULT_TUNING, "bot_logins": ["CI-BOT"]}
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json={"tuning": tuning})
    rig.set_world(
        prs=[pr_row(10, author="BD-BOT", closing=[5])],
        issues=[issue_row(5, closed_by=[10], timeline=[ev("ReopenedEvent", "RE_1", "ALICE", ago(2)),
                                                       ev("ReopenedEvent", "RE_bot", "ci-bot", ago(2))])],
    )
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert [s["id"] for s in data["signals"]] == ["reopened:RE_1"]


def test_ac_b2_custom_readiness_label_is_never_a_signal(tmp_path, monkeypatch):
    """op-B2: the readiness label is the policy's `readiness.label` (here `go`), not the literal
    `plan-approved`; `go` is watched yet never a signal, `plan-approved` (now an ordinary label) is."""
    cfg = {"readiness": {"label": "go"},
           "tuning": {**DEFAULT_TUNING, "watched_labels": ["regression", "go", "plan-approved"]}}
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json=cfg)
    rig.set_world(prs=[pr_row(15, state="OPEN", updated=ago(hours=12), timeline=[
        ev("LabeledEvent", "LE_go", "bob", ago(2), "go"),
        ev("LabeledEvent", "LE_pa", "bob", ago(2), "plan-approved"),
        ev("LabeledEvent", "LE_reg", "bob", ago(2), "regression"),
    ])])
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert sorted(s["id"] for s in data["signals"]) == ["relabeled:LE_pa", "relabeled:LE_reg"]


def test_ac_b2_null_actor_is_not_a_maintainer_and_does_not_crash(tmp_path, monkeypatch):
    """op-B2: GitHub returns `actor: null` for a deleted account: not a maintainer, no crash."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig, extra_events=[
        {"__typename": "ReopenedEvent", "id": "RE_null", "actor": None, "createdAt": ago(2)},
        {"__typename": "LabeledEvent", "id": "LE_null", "actor": None, "createdAt": ago(2),
         "label": {"name": "regression"}},
    ])
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert [s["id"] for s in data["signals"]] == ["reopened:RE_1"]


@pytest.mark.parametrize("args", [[], ["bogus"], ["collect", "--since", "abc"], ["propose"],
                                  ["propose", "--core", "bytedigger"]],
                         ids=["no-subcommand", "bad-subcommand", "bad-since", "propose-bare", "propose-no-signals"])
def test_ac_b2_usage_errors_exit_2(tmp_path, monkeypatch, args):
    """Pinned CLI (op-B2/op-B3): usage errors exit 2."""
    rig = make_rig(tmp_path, monkeypatch)
    proc = run_cli(rig, *args)
    assert proc.returncode == 2, f"expected 2, got {proc.returncode}: {proc.stderr!r}"


# =========================================================================== AC-B3


def _listing_rows(n: int, *, tuner: bool) -> list[dict]:
    rows = []
    for i in range(n):
        if tuner:
            rows.append(tuner_pr(1000 + i, author=BD_USER, head=f"bd/companion-tune-20260101-{i:08x}",
                                 ids=(f"reopened:X{i}",), state="MERGED", updated=ago(30)))
        else:
            rows.append(pr_row(1000 + i, author="mallory", body="", updated=ago(hours=12)))
    return rows


@pytest.mark.parametrize("which", ["pr_list", "issue_list", "tuner_list"])
def test_ac_b3_a_listing_of_exactly_1000_rows_is_truncated(tmp_path, monkeypatch, which):
    """AC-B3: either listing (and the tuner-PR dedupe listing) at exactly 1000 rows => TRUNCATED, exit 4,
    no output."""
    rig = make_rig(tmp_path, monkeypatch)
    if which == "pr_list":
        rig.set_world(prs=_listing_rows(1000, tuner=False))
    elif which == "issue_list":
        rig.set_world(issues=[issue_row(2000 + i, author="mallory") for i in range(1000)])
    else:
        rig.set_world(prs=_listing_rows(1000, tuner=True))
    proc, data = run_collect(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_TRUNCATED", which)
    assert data is None and proc.stdout.strip() == "", "no output on truncation"


def test_ac_b3_999_rows_are_not_truncated(tmp_path, monkeypatch):
    """AC-B3 boundary: 999 tuner rows is fine (only exactly 1000 is treated as a cut-off page)."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=_listing_rows(999, tuner=True))
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert data["signals"] == []


def test_ac_b3_timeline_reads_are_paged(tmp_path, monkeypatch):
    """AC-B3: a timeline of 101 events (the maintainer reopen is the 101st, on page 2) is read in full."""
    rig = make_rig(tmp_path, monkeypatch)
    noise = [ev("LabeledEvent", f"LE_n{i}", "bob", ago(2), "docs") for i in range(100)]
    rig.set_world(
        prs=[pr_row(10, closing=[5])],
        issues=[issue_row(5, closed_by=[10], timeline=[*noise, ev("ReopenedEvent", "RE_P2", "alice", ago(2))])],
    )
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert [s["id"] for s in data["signals"]] == ["reopened:RE_P2"]


def test_ac_b3_graphql_shape_and_argv_forms(tmp_path, monkeypatch):
    """AC-B3: closedByPullRequestsReferences asks includeClosedPrs:true; timelines use --paginate; no comment /
    body / review field is requested anywhere; listings use --state all --limit 1000 and the pinned searches;
    every GitHub call names o/r."""
    rig = make_rig(tmp_path, monkeypatch)
    b2_world(rig)
    proc, _ = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    queries = []
    for e in rig.gh_calls():
        a = e["argv"]
        if a[:2] == ["api", "graphql"]:
            assert "o" in [x[len("owner="):] for x in a if x.startswith("owner=")], a
            assert "r" in [x[len("name="):] for x in a if x.startswith("name=")], a
            queries.append(next(x[len("query="):] for x in a if x.startswith("query=")))
            if "timelineItems(" in queries[-1]:
                assert "--paginate" in a, "timeline reads are paged with --paginate"
        elif a[:1] in (["pr"], ["issue"]):
            assert "-R" in a and a[a.index("-R") + 1] == "o/r", a
        elif a[:1] == ["api"] and a[1] != "user":
            assert a[1].lstrip("/").startswith("repos/o/r/"), a
    closed_by = [q for q in queries if "closedByPullRequestsReferences(" in q]
    assert closed_by and all(re.search(r"includeClosedPrs:\s*true", q) for q in closed_by)
    assert any("timelineItems(" in q for q in queries)
    for q in queries:
        assert not re.search(r"\b(comments|body|bodyText|bodyHTML|reviews)\b", q), f"no free text: {q!r}"
    for e in rig.gh_calls():
        if "--json" in e["argv"]:
            fields = e["argv"][e["argv"].index("--json") + 1].split(",")
            assert not {"comments", "reviews"} & set(fields), fields
    lists = [e["argv"] for e in rig.gh_calls() if e["argv"][:2] in (["pr", "list"], ["issue", "list"])]
    assert all("--search" in a for a in lists), lists
    searches = sorted(a[a.index("--search") + 1] for a in lists)
    assert sum(1 for s in searches if s.startswith("updated:>=")) == 2, searches
    assert sum(1 for s in searches if s == "head:bd/companion-tune-") == 1, searches
    for a in lists:
        assert a[a.index("--state") + 1] == "all" and a[a.index("--limit") + 1] == "1000", a


def test_ac_b3_api_user_403_with_empty_bd_logins(tmp_path, monkeypatch):
    """AC-B3: `gh api user` 403 and no tuning.bd_logins => bd_logins_required, exit 4."""
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json={"tuning": {"bot_logins": ["ci-bot"]}})
    tiny_world(rig)
    rig.switch(user_403=True)
    proc, data = run_collect(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE", "bd_logins_required")
    assert data is None


@pytest.mark.parametrize("tuning", [
    "x", [], {"bd_logins": "bd-bot"}, {"bd_logins": [1]}, {"bot_logins": [["x"]]},
    {"watched_labels": {"a": 1}}, {"watched_labels": ["ok", None]},
], ids=["string", "list", "bd_logins-str", "bd_logins-int-entry", "bot_logins-nested", "watched-dict",
        "watched-null-entry"])
def test_ac_b3_malformed_tuning(tmp_path, monkeypatch, tuning):
    """AC-B3: not an object / a non-list field / a non-string entry => malformed_tuning, exit 4."""
    rig = make_rig(tmp_path, monkeypatch, bytedigger_json={"tuning": tuning})
    tiny_world(rig)
    proc, data = run_collect(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE", "malformed_tuning")
    assert data is None


def test_ac_b3_permission_lookup_500_is_exit_4(tmp_path, monkeypatch):
    """AC-B3: a permission lookup failing with 500 (not 404) => UNAVAILABLE, exit 4, no output."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig)
    rig.switch(perm_500=["alice"])
    proc, data = run_collect(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert data is None


@pytest.mark.parametrize("switch", [{"list_fails": True}, {"gql_fails": True}], ids=["list", "graphql"])
def test_ac_b3_gh_failure_is_unavailable(tmp_path, monkeypatch, switch):
    """op-B2: a failing listing / GraphQL read => UNAVAILABLE, exit 4, no output."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig)
    rig.switch(**switch)
    proc, data = run_collect(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert data is None


def test_ac_b3_unreadable_policy_is_unavailable(tmp_path, monkeypatch):
    """Policy read (Part A path): an unreachable push target => UNAVAILABLE, exit 4."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig)
    rig.set_push_url("/nonexistent/path/to/nowhere.git")
    proc, data = run_collect(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert data is None


# =========================================================================== AC-B4

SIGS = [sig("reopened", "RE_1", pr=10, issue=5, title="SIGNAL TITLE TOKEN five"),
        sig("relabeled", "LE_1", pr=15, issue=None, label="regression", actor="bob", title="PR fifteen",
            action="added"),
        sig("reopened", "RE_0", pr=11, issue=6, title="Issue six")]


def test_ac_b4_no_signals_is_a_clean_no_op(tmp_path, monkeypatch):
    """AC-B4 / step 1: no signals => exit 0, `no signals`, no model call, no branch, no PR."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, [])
    assert proc.returncode == 0, proc.stderr
    assert "no signals" in proc.stdout.splitlines()
    assert rig.model_calls() == []
    assert_nothing_shipped(rig)


def test_ac_b4_no_signals_wins_over_an_open_tuner_pr(tmp_path, monkeypatch):
    """op-B3: step 1 precedes step 2 -- no signals AND an open genuine tuner PR => `no signals`, not `skipped`."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[tuner_pr(41)])
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, [])
    assert proc.returncode == 0, proc.stderr
    assert "no signals" in proc.stdout.splitlines() and "skipped" not in proc.stdout, proc.stdout
    assert rig.model_calls() == []
    assert_nothing_shipped(rig)


@pytest.mark.parametrize("author", ["bd-bot", "BD-Helper"], ids=["api-user", "tuning-bd-login"])
def test_ac_b4_genuine_open_tuner_pr_skips(tmp_path, monkeypatch, author):
    """AC-B4 / step 2: an open tuner PR (BD-login author, bd/companion-tune- head, bd:tune line) => exit 0,
    `skipped: open PR #<n>`, no model call, nothing shipped."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[tuner_pr(41, author=author)])
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, SIGS)
    assert proc.returncode == 0, proc.stderr
    assert "skipped: open PR #41" in proc.stdout.splitlines(), proc.stdout
    assert rig.model_calls() == []
    assert_nothing_shipped(rig)


@pytest.mark.parametrize("row", [
    tuner_pr(41, author="mallory"),
    tuner_pr(41, head="feature/bd/companion-tune-x"),  # passes the loose head: search, fails the prefix rule
    tuner_pr(41, state="MERGED"),
], ids=["forged-author", "wrong-head", "not-open"])
def test_ac_b4_forged_or_finished_tuner_pr_is_not_a_skip(tmp_path, monkeypatch, row):
    """AC-B4: a forged one (wrong author / not a tuner head) or a finished one does not skip: the run goes on."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[row])
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, SIGS)
    assert proc.returncode == 0, proc.stderr
    assert not any(ln.startswith("skipped") for ln in proc.stdout.splitlines()), proc.stdout
    assert len(rig.model_calls()) == 1 and len(rig.pr_creates()) == 1


# =========================================================================== AC-B5


def test_ac_b5_non_overridable_section_is_refused(tmp_path, monkeypatch):
    """AC-B5: a model draft with a non-overridable section => E_COMPANION_TUNE_REFUSED
    section_not_overridable (the real #116 checker's reason), no push, branch deleted."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.stage_model(fenced((COMP_PATH, companion(extra="\n## Other\n\nrewritten other\n"))))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 3, "E_COMPANION_TUNE_REFUSED", "section_not_overridable")
    assert len(rig.model_calls()) == 1
    assert_nothing_shipped(rig)


# =========================================================================== AC-B6


@pytest.mark.parametrize("files", [
    [(COMP_PATH, companion()), ("README", "changed\n")],
    [("docs/notes.md", "hello\n")],
    [(COMP_PATH, companion()), ("bytedigger/companions/other.md", companion())],
], ids=["companion-plus-readme", "other-path-only", "companion-plus-second-companion"])
def test_ac_b6_a_second_path_is_refused(tmp_path, monkeypatch, files):
    """AC-B6: the branch diff must be exactly the companion path, else REFUSED extra_path."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.stage_model(fenced(*files))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 3, "E_COMPANION_TUNE_REFUSED", "extra_path")
    assert_nothing_shipped(rig)


def test_ac_b6_extra_path_is_decided_before_the_checker_verdict(tmp_path, monkeypatch):
    """op-B3 step 6: (a) extra_path precedes (b) the #116 check, even when both would fail."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.stage_model(fenced((COMP_PATH, companion(extra="\n## Other\n\nx\n")), ("README", "changed\n")))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 3, "E_COMPANION_TUNE_REFUSED", "extra_path")
    assert_nothing_shipped(rig)


@pytest.mark.parametrize("bad_path", [
    "../evil.md", "ABS", "bytedigger/../../evil.md", "bytedigger/companions/../companions/bytedigger.md",
    "",
], ids=["parent", "absolute", "nested-escape", "dotdot-but-inside", "empty"])
def test_ac_b6_an_unsafe_block_path_writes_nothing(tmp_path, monkeypatch, bad_path):
    """op-B3 step 4: an absolute / empty / `..` path => DRAFT_INVALID bad_path; nothing is written or
    branched, not even the valid block that came before it."""
    rig = make_rig(tmp_path, monkeypatch)
    if bad_path == "ABS":
        bad_path = str(rig.root / "abs-evil.md")  # absolute, under tmp_path (hermetic)
    rig.stage_model(fenced((COMP_PATH, companion()), (bad_path, "x\n")))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 3, "E_COMPANION_TUNE_DRAFT_INVALID", "bad_path")
    assert_nothing_shipped(rig)
    assert not (rig.root / "evil.md").exists() and not (rig.root / "abs-evil.md").exists()


@pytest.mark.parametrize("output,reason", [
    ("", "no_blocks"),
    ("I would rather not.\n", "no_blocks"),
    (f"<<<bd:file path={COMP_PATH}>>>\n---\nspecializes: bytedigger\n---\n", "unclosed_fence"),
], ids=["empty", "no-blocks", "unclosed"])
def test_ac_b6_unparseable_model_output_is_draft_invalid(tmp_path, monkeypatch, output, reason):
    """op-B3 step 4: empty / block-less / unclosed output => DRAFT_INVALID, nothing shipped."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.stage_model(output)
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 3, "E_COMPANION_TUNE_DRAFT_INVALID", reason)
    assert_nothing_shipped(rig)


# =========================================================================== AC-B7


def _run_valid(rig: Rig, sigs=SIGS, *extra):
    rig.stage_model(fenced((COMP_PATH, companion())))
    before = utc_today()
    proc = run_propose(rig, sigs, *extra)
    after = utc_today()
    return proc, {before, after}


def test_ac_b7_valid_draft_one_push_one_pr(tmp_path, monkeypatch):
    """AC-B7: one push of `bd/companion-tune-<date>-<8 hex>`, one `pr create` against the default branch, the
    body's bd:tune line holds every signal id, and the pushed branch's REAL diff is the companion path."""
    rig = make_rig(tmp_path, monkeypatch)
    proc, dates = _run_valid(rig)
    assert proc.returncode == 0, proc.stderr
    assert "https://github.com/o/r/pull/99" in proc.stdout
    pushes = rig.pushes()
    assert len(pushes) == 1, pushes
    ref = pushes[0]
    m = BRANCH_RE.fullmatch(ref.removeprefix("refs/heads/"))
    assert m, ref
    ids = [s["id"] for s in SIGS]
    assert m.group(1) in dates and m.group(2) == expected_hex(ids), (ref, dates, expected_hex(ids))
    branch = ref.removeprefix("refs/heads/")
    assert g(["diff", "--name-only", f"main...{branch}"], rig.bare).stdout.split() == [COMP_PATH]
    assert g(["show", f"{branch}:{COMP_PATH}"], rig.bare).stdout == companion()
    assert g(["rev-parse", f"{branch}^"], rig.bare).stdout == g(["rev-parse", "main"], rig.bare).stdout
    creates = rig.pr_creates()
    assert len(creates) == 1
    a = creates[0]["argv"]
    assert a[a.index("-R") + 1] == "o/r"
    assert a[a.index("--base") + 1] == "main" and a[a.index("--head") + 1] == branch
    assert a[a.index("--title") + 1].strip()
    assert "--label" not in a
    body = a[a.index("--body") + 1].rstrip("\n").split("\n")
    assert body[-1] == f"<!-- bd:tune signals={','.join(sorted(ids))} -->", body[-1]
    assert len(body) == len(SIGS) + 1, "one line per signal plus the bd:tune line"
    for s in SIGS:
        link = f"https://github.com/o/r/issues/{s['issue']}" if s["issue"] else f"https://github.com/o/r/pull/{s['pr']}"
        line = next((ln for ln in body[:-1] if link in ln), None)
        assert line is not None, f"no body line for {s['id']}: {body!r}"
        assert s["kind"] in line and s["actor"] in line and s["at"] in line, line
    assert MARK_BUILT not in "\n".join(body), "a tuner PR never carries bd:built"
    assert rig.worktree_count() == 1 and g(["status", "--porcelain"], rig.repo).stdout == ""


def test_ac_b7_branch_name_hash_ignores_signal_order(tmp_path, monkeypatch):
    """AC-B7 / F12: the 8 hex are sha256 of the SORTED ids joined by newline."""
    rig = make_rig(tmp_path, monkeypatch)
    shuffled = [SIGS[1], SIGS[0], SIGS[2]]
    proc, _ = _run_valid(rig, shuffled)
    assert proc.returncode == 0, proc.stderr
    m = BRANCH_RE.fullmatch(rig.pushes()[0].removeprefix("refs/heads/"))
    assert m and m.group(2) == expected_hex([s["id"] for s in SIGS])


def test_ac_b7_model_prompt_carries_sections_current_companion_and_signals(tmp_path, monkeypatch):
    """op-B3 step 4: the model is given the overridable section title and body, the current companion and
    the signals; and the checker ran twice (step 3 on the default branch, step 6 on the tuner worktree),
    never against the user's own tree."""
    rig = make_rig(tmp_path, monkeypatch, default_files={COMP_PATH: companion("CURRENT-COMP-TOKEN")})
    proc, _ = _run_valid(rig)
    assert proc.returncode == 0, proc.stderr
    assert len(rig.model_calls()) == 1
    prompt = rig.prompt()
    for needle in ("Project conventions", "core conv line", "CURRENT-COMP-TOKEN", "reopened:RE_1",
                   "relabeled:LE_1", "SIGNAL TITLE TOKEN five"):
        assert needle in prompt, f"prompt lacks {needle!r}"
    calls = rig.checker_calls()
    assert len(calls) == 2, calls
    repos = []
    for c in calls:
        toks = shlex.split(c)
        assert toks[:3] == ["check", "--core", "bytedigger"], c
        repos.append(os.path.realpath(toks[toks.index("--repo") + 1]))
        assert toks[toks.index("--plugin-root") + 1] == str(rig.plugin)
    assert str(rig.repo) not in repos, "the checker must not read the user's own working tree"


def test_ac_b7_llm_command_runs_without_a_shell(tmp_path, monkeypatch):
    """Pinned: --llm-command is shlex-split and executed without a shell (a `;` is just an argument)."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.stage_model(fenced((COMP_PATH, companion())))
    marker = rig.root / "PWNED"
    path = write_signals(rig, SIGS)
    cmd = f"{shlex.quote(str(rig.model))} 'a b' ';' touch {shlex.quote(str(marker))}"
    proc = run_cli(rig, "propose", "--core", "bytedigger", "--signals", str(path), "--repo", str(rig.repo),
                   "--plugin-root", str(rig.plugin), "--llm-command", cmd)
    assert proc.returncode == 0, proc.stderr
    assert rig.model_calls()[0]["argv"] == ["a b", ";", "touch", str(marker)]
    assert not marker.exists()


def test_ac_b7_dry_run_stops_after_the_gate_and_prints_the_diff(tmp_path, monkeypatch):
    """op-B3 step 7: --dry-run runs steps 1-6, pushes nothing, opens no PR, prints the diff."""
    rig = make_rig(tmp_path, monkeypatch)
    proc, _ = _run_valid(rig, SIGS, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert COMP_PATH in proc.stdout and "+Prefer tabs over spaces." in proc.stdout, proc.stdout
    assert rig.pushes() == [] and rig.pr_creates() == []
    assert len(rig.model_calls()) == 1
    assert rig.worktree_count() == 1 and g(["status", "--porcelain"], rig.repo).stdout == ""


def test_ac_b7_pr_create_failure_is_unavailable(tmp_path, monkeypatch):
    """op-B3 step 7: `gh pr create` failing => UNAVAILABLE (exit 4); the tuner branch is not merged anywhere."""
    rig = make_rig(tmp_path, monkeypatch)
    st = rig.state()
    st["pr_create_rc"] = 1
    rig.save(st)
    proc, _ = _run_valid(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert "refs/heads/main" not in rig.pushes()


def test_ac_b7_branch_is_cut_from_the_fetched_default_branch_not_the_users_checkout(tmp_path, monkeypatch):
    """AC-B7 (diverged world): the user is on `feature-x` with a local commit and a dirty tree, local `main`
    has an UNPUSHED commit, and the bare `main` moved ahead after the clone (stale origin/main). The pushed
    tuner branch's parent is the NEW bare main, its diff is exactly the companion path, and the user's
    branches, HEAD, index and working tree are untouched."""
    rig = make_rig(tmp_path, monkeypatch, branch="feature-x", change="commit")
    g(["checkout", "-q", "main"], rig.repo)
    (rig.repo / "README").write_text("unpushed local main commit\n")
    g(["commit", "-q", "-am", "local main only"], rig.repo)
    g(["checkout", "-q", "feature-x"], rig.repo)
    seed = rig.root / "seed"
    (seed / "upstream.txt").write_text("landed on the remote after the clone\n")
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "upstream"], seed)
    g(["push", "-q", str(rig.bare), "main"], seed)
    rig.log.write_text("")  # the fixture push is not part of the unit under test
    (rig.repo / "feature.txt").write_text("dirty, uncommitted\n")
    new_main = g(["rev-parse", "main"], rig.bare).stdout.strip()
    heads_before = g(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"], rig.repo).stdout
    status_before = g(["status", "--porcelain"], rig.repo).stdout
    head_before = g(["rev-parse", "HEAD"], rig.repo).stdout.strip()
    assert status_before != ""
    proc, _ = _run_valid(rig)
    assert proc.returncode == 0, proc.stderr
    pushes = rig.pushes()
    assert len(pushes) == 1 and pushes[0] != "refs/heads/main", pushes
    branch = pushes[0].removeprefix("refs/heads/")
    assert g(["rev-parse", f"{branch}^"], rig.bare).stdout.strip() == new_main
    assert g(["diff", "--name-only", f"main...{branch}"], rig.bare).stdout.split() == [COMP_PATH]
    assert g(["diff", "--name-only", new_main, branch], rig.bare).stdout.split() == [COMP_PATH]
    assert g(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"], rig.repo).stdout == heads_before
    assert g(["rev-parse", "HEAD"], rig.repo).stdout.strip() == head_before
    assert g(["symbolic-ref", "--short", "HEAD"], rig.repo).stdout.strip() == "feature-x"
    assert g(["status", "--porcelain"], rig.repo).stdout == status_before
    assert (rig.repo / "feature.txt").read_text() == "dirty, uncommitted\n"
    assert rig.worktree_count() == 1


def test_ac_b7_identical_draft_is_a_no_change_no_op(tmp_path, monkeypatch):
    """op-B3: a draft identical to the current companion => exit 0, `no change`, no push, no PR, no branch."""
    rig = make_rig(tmp_path, monkeypatch, default_files={COMP_PATH: companion("Same as before.")})
    rig.stage_model(fenced((COMP_PATH, companion("Same as before."))))
    proc = run_propose(rig, SIGS)
    assert proc.returncode == 0, proc.stderr
    assert "no change" in proc.stdout.splitlines(), proc.stdout
    assert len(rig.model_calls()) == 1
    assert_nothing_shipped(rig)


def test_ac_b7_failed_push_does_not_open_a_pr(tmp_path, monkeypatch):
    """op-B3 step 7: `git push` failing (pre-receive exits 1) => exit 4 UNAVAILABLE and NO `gh pr create`."""
    rig = make_rig(tmp_path, monkeypatch)
    (rig.root / "hook-fail.flag").write_text("fail")
    proc, _ = _run_valid(rig)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert len(rig.pushes()) == 1, "the push was attempted (and rejected by the hook)"
    assert rig.pr_creates() == []
    assert rig.bare_tuner_branches() == []
    assert rig.worktree_count() == 1


# --------------------------------------------------------------------------- addendum (code review)


def test_ac_b2_cross_repo_closing_pr_is_skipped_quietly(tmp_path, monkeypatch):
    """Addendum 1: issue #5 is closed by `other/lib#42` while THIS repo has a BD-built PR #42. The foreign
    reference is skipped (queried with repository{nameWithOwner}, compared case-insensitively): no signal, no
    `gh pr view 42`, exit 0; the same-repo control issue still yields its signal."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.set_world(
        prs=[pr_row(42, closing=[5]), pr_row(11, closing=[6])],
        issues=[
            issue_row(5, closed_by=[{"number": 42, "repo": "Other/Lib"}],
                      timeline=[ev("ReopenedEvent", "RE_x", "alice", ago(2))]),
            issue_row(6, closed_by=[11], timeline=[ev("ReopenedEvent", "RE_ok", "alice", ago(2))]),
        ],
    )
    proc, data = run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert [s["id"] for s in data["signals"]] == ["reopened:RE_ok"]
    views = [e["argv"] for e in rig.gh_calls() if e["argv"][:2] == ["pr", "view"]]
    assert not any(a[2] == "42" for a in views), f"a foreign PR must never be looked up: {views!r}"
    closed_by = [a for e in rig.gh_calls() for a in e["argv"]
                 if a.startswith("query=") and "closedByPullRequestsReferences(" in a]
    assert closed_by and all("nameWithOwner" in q for q in closed_by)


def _install_tracked_hook(rig: Rig) -> Path:
    """A tracked executable `.githooks/pre-commit` on the default branch, `core.hooksPath=.githooks`;
    the hook touches a sentinel under tmp_path."""
    sentinel = rig.root / "hook-ran.sentinel"
    seed = rig.root / "seed"
    hook = seed / ".githooks" / "pre-commit"
    hook.parent.mkdir()
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 0\n")
    hook.chmod(0o755)
    g(["add", "-A"], seed)
    g(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "hook"], seed)
    g(["push", "-q", str(rig.bare), "main"], seed)
    g(["config", "core.hooksPath", ".githooks"], rig.repo)
    rig.log.write_text("")
    return sentinel


def test_ac_b6_hook_block_is_refused_before_anything_runs(tmp_path, monkeypatch):
    """Addendum 2: the model emits the companion plus `.githooks/pre-commit` => REFUSED extra_path, the
    tracked hook never runs (sentinel absent), no push, no branch."""
    rig = make_rig(tmp_path, monkeypatch)
    sentinel = _install_tracked_hook(rig)
    rig.stage_model(fenced((COMP_PATH, companion()), (".githooks/pre-commit", "#!/bin/sh\ntouch /x\n")))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 3, "E_COMPANION_TUNE_REFUSED", "extra_path")
    assert not sentinel.exists(), "the commit hook ran"
    assert_nothing_shipped(rig)


def test_ac_b7_tuner_commit_runs_with_hooks_disabled(tmp_path, monkeypatch):
    """Addendum 2: a valid single-block draft with a tracked hook installed => the hook does not run and
    the PR opens."""
    rig = make_rig(tmp_path, monkeypatch)
    sentinel = _install_tracked_hook(rig)
    proc, _ = _run_valid(rig)
    assert proc.returncode == 0, proc.stderr
    assert not sentinel.exists(), "the tuner commit must run with hooks disabled"
    assert len(rig.pushes()) == 1 and len(rig.pr_creates()) == 1


def test_ac_b7_default_model_is_claude_with_no_tools(tmp_path, monkeypatch):
    """Addendum 3: without --llm-command the model command is exactly
    `claude -p --model <get_claude_fallback()> --tools ""` (a fake `claude` first on PATH records argv)."""
    from bytedigger_engine.lib.model_config import get_claude_fallback

    rig = make_rig(tmp_path, monkeypatch)
    _write_exec(rig.root / "bin" / "claude", (rig.model).read_text())  # same recorder as the fixture model
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, SIGS, model=False)
    assert proc.returncode == 0, proc.stderr
    calls = rig.model_calls()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert argv == ["-p", "--model", get_claude_fallback(), "--tools", ""], argv


def test_ac_b7_prompt_signal_lines_carry_label_action_pr_issue_and_title(tmp_path, monkeypatch):
    """Addendum 4: each signal line in the prompt has kind, title, #pr, #issue (when set) and, for relabeled,
    the label with added / removed."""
    sigs = [
        sig("relabeled", "LE_a", pr=15, issue=None, label="regression", actor="bob", title="TITLE-ADDED",
            action="added"),
        sig("relabeled", "UE_b", pr=17, issue=16, label="wontfix", actor="alice", title="TITLE-REMOVED",
            action="removed"),
        sig("reopened", "RE_c", pr=10, issue=5, title="TITLE-REOPENED"),
    ]
    rig = make_rig(tmp_path, monkeypatch)
    proc, _ = _run_valid(rig, sigs)
    assert proc.returncode == 0, proc.stderr
    lines = rig.prompt().splitlines()

    def line_of(sid):
        return next(ln for ln in lines if sid in ln)

    a, r, o = line_of("relabeled:LE_a"), line_of("relabeled:UE_b"), line_of("reopened:RE_c")
    assert all(x in a for x in ("relabeled", "regression", "added", "#15", "TITLE-ADDED")), a
    assert all(x in r for x in ("relabeled", "wontfix", "removed", "#17", "#16", "TITLE-REMOVED")), r
    assert "removed" not in a and "added" not in r
    assert all(x in o for x in ("reopened", "#5", "#10", "TITLE-REOPENED")), o


@pytest.mark.parametrize("cmd", ["collect", "propose"])
def test_ac_b3_api_user_failure_other_than_403_is_unavailable(tmp_path, monkeypatch, cmd):
    """Addendum 5: only `HTTP 403` on `gh api user` is tolerated. A 502 => exit 4 UNAVAILABLE even though
    tuning.bd_logins is non-empty (fixture default), for both collect and propose."""
    rig = make_rig(tmp_path, monkeypatch)
    tiny_world(rig)
    rig.switch(user_502=True)
    rig.stage_model(fenced((COMP_PATH, companion())))
    if cmd == "collect":
        proc, data = run_collect(rig)
        assert data is None
    else:
        proc = run_propose(rig, SIGS)
        assert rig.model_calls() == []
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert rig.pushes() == [] and rig.pr_creates() == []


# =========================================================================== AC-B8


def _literals(tree: ast.AST):
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                doc_ids.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            yield node.value


def test_ac_b8_source_never_merges_pushes_default_or_adds_labels():
    """AC-B8: source scan of companion_tune.py (string literals, docstrings excluded): no `pr merge`, no
    merge API path, no label add, no forced / default-branch push refspec. (Behaviour is covered by the log
    oracle inside every runner and by AC-B7.)"""
    assert COMPANION_TUNE_PY.is_file(), "companion_tune.py does not exist yet"
    lits = list(_literals(ast.parse(COMPANION_TUNE_PY.read_text(encoding="utf-8"))))
    assert lits, "a real module has string literals"
    forbidden = [
        (r"^merge$", "gh pr merge argv"), (r"\bpr\s+merge\b", "pr merge"), (r"/merge\b", "merge API path"),
        (r"^--admin$|^--auto$", "merge flags"),
        (r"--add-label|^--label\b|addLabelsToLabelable|/labels/?$", "label add"),
        (r"^--force|^--mirror$|^\+refs|:refs/heads/(main|master)\b|:(main|master)$",
         "forced / default-branch push"),
    ]
    hits = [(why, s) for s in lits for pat, why in forbidden if re.search(pat, s)]
    assert hits == [], f"forbidden literals: {hits!r}"


# =========================================================================== AC-B9


def test_ac_b9_invalid_current_companion_is_refused_without_a_model_call(tmp_path, monkeypatch):
    """AC-B9 / step 3: the companion on the default branch is itself invalid (non-overridable section) =>
    REFUSED current_companion_invalid, no model call."""
    bad = companion(extra="\n## Other\n\nbad\n")
    rig = make_rig(tmp_path, monkeypatch, default_files={COMP_PATH: bad})
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 3, "E_COMPANION_TUNE_REFUSED", "current_companion_invalid")
    assert rig.model_calls() == []
    assert_nothing_shipped(rig)


@pytest.mark.parametrize("rc", [1, 2, 127])
def test_ac_b9_checker_crash_on_the_current_companion_is_unavailable(tmp_path, monkeypatch, rc):
    """AC-B9: the #116 check exiting with anything but 0/3 in step 3 => UNAVAILABLE (exit 4), no model call."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.crash_checker_at(1, rc)
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert rig.model_calls() == []
    assert rig.pushes() == [] and rig.pr_creates() == []


def test_ac_b9_checker_crash_in_the_gate_is_unavailable_and_pushes_nothing(tmp_path, monkeypatch):
    """op-B3 step 6(b): the gate's check crashing (second call) => UNAVAILABLE, nothing pushed."""
    rig = make_rig(tmp_path, monkeypatch)
    rig.crash_checker_at(2, 1)
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, SIGS)
    assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")
    assert len(rig.model_calls()) == 1
    assert rig.pushes() == [] and rig.pr_creates() == []


def test_ac_b9_core_with_nothing_overridable_is_a_no_op(tmp_path, monkeypatch):
    """op-B3 step 3: no `metadata.overridable` on the core => exit 0, `nothing overridable`, no model call."""
    rig = make_rig(tmp_path, monkeypatch, core_text=CORE_TEXT_NO_OVERRIDABLE)
    rig.stage_model(fenced((COMP_PATH, companion())))
    proc = run_propose(rig, SIGS)
    assert proc.returncode == 0, proc.stderr
    assert "nothing overridable" in proc.stdout.splitlines(), proc.stdout
    assert rig.model_calls() == []
    assert_nothing_shipped(rig)


# =========================================================================== AC-B10

WORKFLOW_REL = "examples/github-actions/companion-tune.yml"


def test_ac_b10_example_workflow_parses_with_schedule_permissions_and_steps():
    """AC-B10: parses; weekly schedule + workflow_dispatch; the three permissions; `collect --since 7d` then
    `propose`."""
    path = REPO_ROOT / WORKFLOW_REL
    assert path.is_file(), f"{WORKFLOW_REL} does not exist yet"
    yaml = pytest.importorskip("yaml")  # pyyaml is a CI test dependency
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = doc.get("on", doc.get(True))  # PyYAML (YAML 1.1) parses the bare key `on` as True
    assert isinstance(on, dict) and "workflow_dispatch" in on
    assert "0 6 * * 1" in [s["cron"] for s in on["schedule"]]
    want = {"contents": "write", "pull-requests": "write", "issues": "read"}
    perms = [doc.get("permissions")] + [j.get("permissions") for j in (doc.get("jobs") or {}).values()]
    assert want in perms, f"permissions must be exactly {want!r}: {perms!r}"
    runs = "\n".join(step["run"] for job in doc["jobs"].values() for step in job.get("steps", []) if "run" in step)
    c = re.search(r"companion-tune\s+collect[^\n]*", runs)
    p = re.search(r"companion-tune\s+propose", runs)
    assert c and p and c.start() < p.start(), "collect must run before propose"
    assert re.search(r"--since\s+7d", c.group(0)), c.group(0)


def test_ac_b10_bd_itself_does_not_ship_the_workflow():
    """AC-B10: .github/workflows/ has no companion-tune workflow (BD has no companion of its own)."""
    wf = REPO_ROOT / ".github" / "workflows"
    assert [p.name for p in wf.iterdir() if "companion-tune" in p.name] == []
    for p in wf.iterdir():
        if p.is_file():
            assert not re.search(r"companion-tune\s+(collect|propose)", p.read_text(encoding="utf-8")), p.name


# =========================================================================== AC-R (Part B slice)

FOUR_CODES = ("E_COMPANION_TUNE_DRAFT_INVALID", "E_COMPANION_TUNE_REFUSED", "E_COMPANION_TUNE_UNAVAILABLE",
              "E_COMPANION_TUNE_TRUNCATED")


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_ac_r_four_tune_codes_registered():
    """AC-R: the four E_COMPANION_TUNE_* codes are in error_codes.ERROR_CODES."""
    from bytedigger_engine.error_codes import ERROR_CODES

    for code in FOUR_CODES:
        assert code in ERROR_CODES, code


@pytest.mark.parametrize("rel", ["ERROR_CODES.md", "bytedigger_engine/ERROR_CODES.md"])
def test_ac_r_four_tune_codes_in_both_markdown_registries(rel):
    """AC-R: both ERROR_CODES.md files name the four codes."""
    md = (ENGINE_ROOT / rel).read_text(encoding="utf-8")
    for code in FOUR_CODES:
        assert code in md, f"{rel} lacks {code}"


def test_ac_r_core_manifest_lists_companion_tune():
    """AC-R: companion_tune.py is in core_manifest.json."""
    manifest = json.loads((ENGINE_ROOT / "core_manifest.json").read_text(encoding="utf-8"))
    assert "companion_tune.py" in manifest["core_modules"]


def test_ac_r_configuration_doc_names_tuning():
    """AC-R: docs/configuration.md names `tuning`, `bd_logins`, both marker lines and the Actions setting."""
    text = _read("docs/configuration.md")
    for needle in ("tuning", "bd_logins", "bd:built", "bd:tune",
                   "Allow GitHub Actions to create and approve pull requests"):
        assert needle in text, f"docs/configuration.md must mention {needle!r}"


def test_ac_r_changelog_mentions_part_b_companion_tuning():
    """AC-R: CHANGELOG.md [Unreleased] mentions #117 Part B / companion tuning (Part A's entry does not)."""
    text = _read("CHANGELOG.md")
    m = re.search(r"(?ms)^## \[Unreleased\]\s*$(.*?)(?=^## \[)", text)
    assert m, "CHANGELOG.md needs an [Unreleased] section"
    assert re.search(r"(?i)companion[- ]tun", m.group(1)), "[Unreleased] must mention companion tuning"


def test_ac_r_core_boundary_lint_clean_with_companion_tune():
    """AC-R: core-boundary-lint.py is clean once companion_tune.py exists."""
    assert COMPANION_TUNE_PY.is_file(), "companion_tune.py does not exist yet"
    cp = subprocess.run(["python3", str(REPO_ROOT / "core-boundary-lint.py")], cwd=str(REPO_ROOT),
                        capture_output=True, text=True, timeout=90, env=_env())
    assert cp.returncode == 0, cp.stdout + cp.stderr
