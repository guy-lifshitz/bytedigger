"""readiness — the `plan-approved` readiness gate (bd#117 Part A).

One chokepoint decides whether an issue-bound build may send work off the
machine: ``verdict(repo, stage, spec_path=None)``. The CLI wrapper
``scripts/readiness`` exposes it as ``check`` and ``post``.

Policy is read from the repository BD *pushes to* (``git remote get-url --push
--all origin``), on its default branch, into ``refs/bd/policy`` -- never from the
working tree or the build branch. The approved plan lives in an issue comment
(a "spec record" by the BD user); approval is the ``label`` a human adds after
it. At stage ``ship`` an approved verdict is *consumed* before anything is
pushed: BD posts a consumption record, re-reads the comments, and removes the
label. BD never adds the label.

Exit codes of the CLI: 0 off/approved/done, 2 usage, 3 not approved, 4 unavailable.
stdlib + the ``git`` / ``gh`` binaries only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from bytedigger_engine.config_provider import get_config
from bytedigger_engine.lib.bounded_spawn import TIMEOUT_RETURNCODE, bounded_run
from bytedigger_engine.lib.git_blob import read_blob

LABEL_DEFAULT = "plan-approved"
POLICY_REF = "refs/bd/policy"
MAX_RECORD_CHARS = 65536

_GIT_TIMEOUT_S = 30
_GH_TIMEOUT_S = 60
_MAX_PAGES = 500
_REREADS = 3
_REREAD_DELAY_S = 2.0

_SCRUBBED_GIT_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})
_SCRUBBED_GIT_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")

_BRANCH_RE = re.compile(r"^(?:gh|batch/)(\d+)(?:-|$)", re.IGNORECASE)
_GITHUB_URL_RES = (
    re.compile(r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?$"),
    re.compile(r"^git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?$"),
)
_SPEC_PREFIX = "<!-- bd:spec"
_SPEC_MARKER_RE = re.compile(r"<!-- bd:spec sha256=([0-9a-fA-F]+) -->")
_CONSUMED_RE = re.compile(r"<!-- bd:consumed approval=(\S+) branch=(\S+) -->")

_ISSUE_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){issue(number:$number){%s}}}"
)
_PAGE_INFO = "pageInfo{hasNextPage endCursor}"
_LABELS_QUERY = _ISSUE_QUERY % ("labels(first:100,after:$after){nodes{name} " + _PAGE_INFO + "}")
_COMMENTS_QUERY = _ISSUE_QUERY % (
    "comments(first:100,after:$after){nodes{id databaseId author{login} body createdAt "
    "lastEditedAt} " + _PAGE_INFO + "}"
)
_EVENTS_QUERY = _ISSUE_QUERY % (
    "timelineItems(itemTypes:[LABELED_EVENT],first:100,after:$after){nodes{... on LabeledEvent"
    "{id actor{login} createdAt label{name}}} " + _PAGE_INFO + "}"
)


# --------------------------------------------------------------------------- small pure helpers


def parse_issue_from_branch(branch: str) -> int | None:
    """Issue number bound by a branch name: ``gh<N>``, ``gh<N>-...``, ``batch/<N>``; anchored."""
    match = _BRANCH_RE.match(branch)
    return int(match.group(1)) if match else None


def _normalise(text: str) -> str:
    """Spec-record normalisation: CRLF -> LF, trailing whitespace stripped, one final newline."""
    return text.replace("\r\n", "\n").rstrip() + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _one_line(text: str) -> str:
    return " ".join(text.split())


class _Unavailable(Exception):
    """git / gh / network / parse failure: the gate can neither approve nor refuse."""

    def __init__(self, detail: str, *, policy: bool = False) -> None:
        super().__init__(detail)
        self.detail = _one_line(detail)
        self.policy = policy  # raised while reading the policy (start stage only warns)


class _Policy(NamedTuple):
    push_url: str
    label: str
    approvers: list[str]
    distinct_actor: bool


class _Comment(NamedTuple):
    db_id: int
    author: str
    body: str
    created: datetime
    edited: bool


class _Event(NamedTuple):
    id: str
    actor: str
    created: datetime
    label: str


class _Snapshot(NamedTuple):
    bd_user: str
    labels: list[str]
    comments: list[_Comment]
    events: list[_Event]


class _SpecRecord(NamedTuple):
    valid: bool
    reason: str | None
    sha: str
    text: str
    created: datetime


class _Decision(NamedTuple):
    verdict: str
    reason: str | None
    sha: str | None
    event_id: str | None
    retry: bool


# --------------------------------------------------------------------------- git


def _git_env() -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items()
        if k not in _SCRUBBED_GIT_ENV and not k.startswith(_SCRUBBED_GIT_ENV_PREFIXES)
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(repo: Path, args: list[str]) -> tuple[int, str, str]:
    """git with cwd=repo (never ``-C``), scrubbed env, 30 s timeout. Never raises."""
    try:
        proc = bounded_run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_S, check=False, env=_git_env(),
        )
    except OSError as exc:
        return 127, "", str(exc)
    if proc.returncode == TIMEOUT_RETURNCODE:
        return TIMEOUT_RETURNCODE, "", f"git {args[0]}: timeout after {_GIT_TIMEOUT_S}s"
    return proc.returncode, proc.stdout or "", (proc.stderr or "").strip()


def _symref_head(ls_remote_out: str) -> str | None:
    """Default-branch ref named by ``ls-remote --symref P HEAD`` output, else None."""
    for line in ls_remote_out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].strip() == "HEAD" and parts[0].startswith("ref:"):
            ref = parts[0][4:].strip()
            if ref.startswith("refs/") and not any(ch in ref for ch in ": \t"):
                return ref
    return None


def _load_policy(repo: Path) -> _Policy | None:
    """Decision table, policy half. None = off; raises _Unavailable(policy=True) when unreadable."""
    rc, out, _ = _git(repo, ["remote", "get-url", "--push", "--all", "origin"])
    urls = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if rc != 0 or not urls:
        return None  # no origin
    if len(urls) > 1:
        raise _Unavailable("remote origin has more than one push URL", policy=True)
    push_url = urls[0]
    if push_url.startswith("-"):
        raise _Unavailable("push URL looks like an option", policy=True)
    rc, out, err = _git(repo, ["ls-remote", "--symref", push_url, "HEAD"])
    if rc != 0:
        raise _Unavailable(f"git ls-remote failed: {err or rc}", policy=True)
    if not out.strip():
        return None  # empty repo / unborn HEAD
    default_ref = _symref_head(out)
    if default_ref is None:
        raise _Unavailable("ls-remote reported HEAD without a symbolic ref", policy=True)
    rc, _, err = _git(repo, ["fetch", "--no-tags", push_url, f"+{default_ref}:{POLICY_REF}"])
    if rc != 0:
        raise _Unavailable(f"git fetch of the policy ref failed: {err or rc}", policy=True)
    status, payload = read_blob(repo, POLICY_REF, "bytedigger.json", env=_git_env())
    if status == "absent":
        return None
    if status != "ok" or not isinstance(payload, bytes):
        raise _Unavailable(f"cannot read bytedigger.json: {payload}", policy=True)
    try:
        data = json.loads(payload.decode("utf-8"))
    except ValueError as exc:
        raise _Unavailable(f"bytedigger.json is not valid JSON: {exc}", policy=True) from exc
    if not isinstance(data, dict):
        raise _Unavailable("bytedigger.json is not a JSON object", policy=True)
    if "readiness" not in data:
        return None
    cfg = data["readiness"]
    if not isinstance(cfg, dict):
        raise _Unavailable("readiness is not an object", policy=True)
    required = cfg.get("required", False)
    label = cfg.get("label", LABEL_DEFAULT)
    approvers = cfg.get("approvers", [])
    distinct_actor = cfg.get("distinct_actor", False)
    if (
        not isinstance(required, bool) or not isinstance(label, str) or not label
        or not isinstance(approvers, list) or not all(isinstance(a, str) for a in approvers)
        or not isinstance(distinct_actor, bool)
    ):
        raise _Unavailable("readiness has fields of the wrong type", policy=True)
    if not required:
        return None
    return _Policy(push_url, label, list(approvers), distinct_actor)


def _parse_github(push_url: str) -> tuple[str, str]:
    for pattern in _GITHUB_URL_RES:
        match = pattern.match(push_url)
        if match:
            return match.group(1), match.group(2)
    raise _Unavailable("push URL is not a github.com repository (v1 supports github.com only)", policy=True)


def _current_branch(repo: Path) -> str:
    rc, out, _ = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 else ""


# --------------------------------------------------------------------------- gh


def _gh(repo: Path, args: list[str], stdin: str | None = None) -> str:
    """Run gh (binary resolved by the config provider); return stdout or raise _Unavailable."""
    gh_bin = get_config().binary("HAL_GH_BIN", "gh")
    try:
        proc = bounded_run(
            [gh_bin, *args], cwd=str(repo), capture_output=True, text=True,
            timeout=_GH_TIMEOUT_S, check=False, input=stdin if stdin is not None else "",
        )
    except OSError as exc:
        raise _Unavailable(f"gh: {exc}") from exc
    if proc.returncode == TIMEOUT_RETURNCODE:
        raise _Unavailable(f"gh {args[0]}: timeout after {_GH_TIMEOUT_S}s")
    if proc.returncode != 0:
        raise _Unavailable(f"gh {args[0]} exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    return proc.stdout or ""


def _paged(repo: Path, owner: str, name: str, number: int, conn: str, query: str) -> list[dict[str, Any]]:
    """Every node of one issue connection; own cursor loop (the cursor is echoed back verbatim)."""
    nodes: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        args = ["api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}",
                "-f", f"name={name}", "-F", f"number={number}"]
        if cursor is not None:
            args += ["-f", f"after={cursor}"]
        out = _gh(repo, args)
        try:
            connection = json.loads(out)["data"]["repository"]["issue"][conn]
            page = connection["nodes"]
            has_next = connection["pageInfo"]["hasNextPage"]
            end_cursor = connection["pageInfo"]["endCursor"]
        except (ValueError, KeyError, TypeError) as exc:
            raise _Unavailable(f"unparseable {conn} page: {exc!r}") from exc
        if not isinstance(page, list) or not all(isinstance(n, dict) for n in page):
            raise _Unavailable(f"malformed {conn} page")
        nodes.extend(page)
        if has_next is not True:
            return nodes
        if not isinstance(end_cursor, str) or not end_cursor:
            raise _Unavailable(f"{conn} page has no end cursor")
        cursor = end_cursor
    raise _Unavailable(f"too many {conn} pages")


def _read_comments(repo: Path, owner: str, name: str, number: int) -> list[_Comment]:
    out: list[_Comment] = []
    for node in _paged(repo, owner, name, number, "comments", _COMMENTS_QUERY):
        try:
            db_id = node["databaseId"]
            body = node["body"]
            if isinstance(db_id, bool) or not isinstance(db_id, int) or not isinstance(body, str):
                raise TypeError("databaseId/body type")
            author = (node.get("author") or {}).get("login") or ""
            out.append(_Comment(db_id, str(author), body, _ts(node["createdAt"]),
                                node.get("lastEditedAt") is not None))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise _Unavailable(f"malformed comment node: {exc!r}") from exc
    return out


def _read_labels(repo: Path, owner: str, name: str, number: int) -> list[str]:
    labels: list[str] = []
    for node in _paged(repo, owner, name, number, "labels", _LABELS_QUERY):
        label = node.get("name")
        if not isinstance(label, str):
            raise _Unavailable("malformed label node")
        labels.append(label)
    return labels


def _read_events(repo: Path, owner: str, name: str, number: int) -> list[_Event]:
    events: list[_Event] = []
    for node in _paged(repo, owner, name, number, "timelineItems", _EVENTS_QUERY):
        if "label" not in node:
            continue  # a timeline node that is not a LabeledEvent
        try:
            events.append(_Event(str(node["id"]), str((node.get("actor") or {}).get("login") or ""),
                                 _ts(node["createdAt"]), str(node["label"]["name"])))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise _Unavailable(f"malformed labeled event: {exc!r}") from exc
    return events


def _read_issue(repo: Path, owner: str, name: str, number: int) -> _Snapshot:
    try:
        login = json.loads(_gh(repo, ["api", "user"]))["login"]
    except (ValueError, KeyError, TypeError) as exc:
        raise _Unavailable(f"unparseable `gh api user` output: {exc!r}") from exc
    if not isinstance(login, str) or not login:
        raise _Unavailable("`gh api user` returned no login")
    return _Snapshot(
        login,
        _read_labels(repo, owner, name, number),
        _read_comments(repo, owner, name, number),
        _read_events(repo, owner, name, number),
    )


def _remove_label(repo: Path, owner: str, name: str, number: int, label: str) -> None:
    """Best effort: a failure is a warning naming the label, never a verdict change."""
    path = f"repos/{owner}/{name}/issues/{number}/labels/{urllib.parse.quote(label, safe='')}"
    try:
        _gh(repo, ["api", "-X", "DELETE", path])
    except _Unavailable as exc:
        print(f'W_READINESS_LABEL_NOT_REMOVED could not remove label "{label}" from #{number}: {exc.detail}',
              file=sys.stderr)


# --------------------------------------------------------------------------- evaluation


def _latest_spec_record(comments: list[_Comment], bd_user: str) -> _SpecRecord | None:
    """The newest BD-user spec record (by databaseId), valid or not -- no fallback to older ones."""
    bd = bd_user.casefold()
    records = sorted(
        (c for c in comments if c.author.casefold() == bd and c.body.startswith(_SPEC_PREFIX)),
        key=lambda c: c.db_id,
    )
    if not records:
        return None
    latest = records[-1]
    marker, _, text = latest.body.partition("\n")
    match = _SPEC_MARKER_RE.fullmatch(marker.rstrip("\r"))
    sha = _sha(text)
    reason: str | None = None
    if latest.edited:
        reason = "spec_record_edited"
    elif match is None or match.group(1).lower() != sha:
        reason = "spec_record_bad"
    return _SpecRecord(reason is None, reason, sha, text, latest.created)


def _consumption_owners(comments: list[_Comment], bd_user: str) -> dict[str, tuple[int, str]]:
    """approval event id -> (databaseId, branch) of its earliest unedited consumption record."""
    bd = bd_user.casefold()
    owners: dict[str, tuple[int, str]] = {}
    for c in comments:
        if c.author.casefold() != bd or c.edited:
            continue
        match = _CONSUMED_RE.fullmatch(c.body.strip())
        if match is None:
            continue
        event_id, branch = match.group(1), match.group(2)
        if event_id not in owners or c.db_id < owners[event_id][0]:
            owners[event_id] = (c.db_id, branch)
    return owners


def _decide(snap: _Snapshot, pol: _Policy, branch: str, stage: str, spec_text: str | None) -> _Decision:
    def refuse(reason: str, sha: str | None = None) -> _Decision:
        return _Decision("NOT_APPROVED", reason, sha, None, False)

    record = _latest_spec_record(snap.comments, snap.bd_user)
    if record is None:
        return refuse("no_spec_record")
    if not record.valid:
        return refuse(record.reason or "spec_record_bad")

    want = pol.label.casefold()
    candidates = [(e.created, i, e) for i, e in enumerate(snap.events) if e.label.casefold() == want]
    event = max(candidates, key=lambda t: (t[0], t[1]))[2] if candidates else None
    bd = snap.bd_user.casefold()

    counts = (
        event is not None
        and event.created > record.created
        and (not pol.approvers or event.actor.casefold() in {a.casefold() for a in pol.approvers})
        and not (pol.distinct_actor and event.actor.casefold() == bd)
    )
    owner = _consumption_owners(snap.comments, snap.bd_user).get(event.id) if event is not None else None

    if stage == "ship" and event is not None and counts and owner is not None and owner[1] == branch:
        return _Decision("APPROVED", None, record.sha, event.id, True)
    if event is not None and owner is not None and owner[1] != branch:
        return refuse("approval_consumed", record.sha)
    if not any(label.casefold() == want for label in snap.labels):
        return refuse("label_absent", record.sha)
    if event is None or event.created <= record.created:
        return refuse("label_predates_spec", record.sha)
    if pol.approvers and event.actor.casefold() not in {a.casefold() for a in pol.approvers}:
        return refuse("approver_not_allowed", record.sha)
    if pol.distinct_actor and event.actor.casefold() == bd:
        return refuse("self_approved", record.sha)
    if stage == "start" and spec_text is not None and _normalise(spec_text) != _normalise(record.text):
        return refuse("spec_changed", record.sha)
    return _Decision("APPROVED", None, record.sha, event.id, False)


def _consume(repo: Path, owner: str, name: str, number: int, branch: str, event_id: str,
             bd_user: str) -> bool:
    """Post the consumption record, re-read, and report whether this branch owns the approval.

    Raises _Unavailable when the post fails or the record stays invisible over the re-reads.
    """
    body = f"<!-- bd:consumed approval={event_id} branch={branch} -->"
    out = _gh(repo, ["api", "-X", "POST", f"repos/{owner}/{name}/issues/{number}/comments",
                     "--input", "-"], stdin=json.dumps({"body": body}))
    posted_id: int | None = None
    try:
        raw_id = json.loads(out).get("id")
        if isinstance(raw_id, int) and not isinstance(raw_id, bool):
            posted_id = raw_id
    except (ValueError, AttributeError):
        posted_id = None
    bd = bd_user.casefold()
    for attempt in range(_REREADS):
        if attempt:
            time.sleep(_REREAD_DELAY_S)
        comments = _read_comments(repo, owner, name, number)
        visible = any(
            c.author.casefold() == bd and not c.edited and c.body.strip() == body
            and (posted_id is None or c.db_id == posted_id)
            for c in comments
        )
        if visible:
            mine = _consumption_owners(comments, bd_user).get(event_id)
            return mine is not None and mine[1] == branch
    raise _Unavailable(f"consumption record not visible after {_REREADS} re-reads")


def _read_spec_file(path: str) -> str:
    try:
        return Path(path).read_bytes().decode("utf-8", "replace")
    except OSError as exc:
        raise _Unavailable(f"cannot read spec file {path}: {exc}") from exc


def _evaluate(res: dict[str, Any], repo: Path, stage: str, spec_path: str | None) -> None:
    pol = _load_policy(repo)
    if pol is None:
        return
    res["required"] = True
    res["label"] = pol.label
    owner, name = _parse_github(pol.push_url)
    branch = _current_branch(repo)
    issue = parse_issue_from_branch(branch)
    res["issue"] = issue
    if issue is None:
        res.update(verdict="NOT_APPROVED", reason="no_issue")
        return
    spec_text = _read_spec_file(spec_path) if (stage == "start" and spec_path) else None
    snap = _read_issue(repo, owner, name, issue)
    decision = _decide(snap, pol, branch, stage, spec_text)
    res.update(verdict=decision.verdict, reason=decision.reason, record_sha256=decision.sha)
    if decision.verdict != "APPROVED" or stage != "ship" or decision.retry:
        return
    assert decision.event_id is not None
    if not _consume(repo, owner, name, issue, branch, decision.event_id, snap.bd_user):
        res.update(verdict="NOT_APPROVED", reason="approval_consumed")
        return
    _remove_label(repo, owner, name, issue, pol.label)


def _run_guarded(res: dict[str, Any], body: Any) -> bool:
    """Run ``body``; map any failure to UNAVAILABLE. Returns True if it failed reading the policy."""
    try:
        body()
    except _Unavailable as exc:
        res.update(verdict="UNAVAILABLE", reason=exc.detail)
        return exc.policy
    except Exception as exc:  # noqa: BLE001 -- a crash must never read as approval
        res.update(verdict="UNAVAILABLE", reason=_one_line(f"internal error: {exc!r}"))
    return False


def _verdict(repo: str | Path, stage: str, spec_path: str | None) -> tuple[dict[str, Any], bool]:
    if stage not in ("start", "ship"):
        raise ValueError(f"stage must be 'start' or 'ship', got {stage!r}")
    res: dict[str, Any] = {
        "required": False, "issue": None, "label": LABEL_DEFAULT,
        "verdict": "OFF", "reason": None, "record_sha256": None,
    }
    policy_failure = _run_guarded(res, lambda: _evaluate(res, Path(repo), stage, spec_path))
    return res, policy_failure


def verdict(repo: str | Path, stage: str, spec_path: str | None = None) -> dict[str, Any]:
    """Readiness verdict for the branch checked out in ``repo``.

    Keys: required, issue, label, verdict ("OFF" | "APPROVED" | "NOT_APPROVED" |
    "UNAVAILABLE"), reason, record_sha256. At stage ``ship`` an approval is consumed
    (record posted and re-read, label removed) before this returns APPROVED.
    """
    return _verdict(repo, stage, spec_path)[0]


# --------------------------------------------------------------------------- post


def _post(res: dict[str, Any], repo: Path, spec_path: str) -> None:
    pol = _load_policy(repo)
    if pol is None:
        return
    res["required"] = True
    res["label"] = pol.label
    owner, name = _parse_github(pol.push_url)
    branch = _current_branch(repo)
    issue = parse_issue_from_branch(branch)
    res["issue"] = issue
    if issue is None:
        res.update(verdict="NOT_APPROVED", reason="no_issue")
        return
    text = _normalise(_read_spec_file(spec_path))
    sha = _sha(text)
    body = f"<!-- bd:spec sha256={sha} -->\n{text}"
    if len(body) > MAX_RECORD_CHARS:
        res.update(verdict="NOT_APPROVED", reason="spec_too_large")
        return
    snap = _read_issue(repo, owner, name, issue)
    record = _latest_spec_record(snap.comments, snap.bd_user)
    label_on = any(label.casefold() == pol.label.casefold() for label in snap.labels)
    if record is not None and record.valid and record.sha == sha:
        decision = _decide(snap, pol, branch, "start", text)
        remove = decision.reason == "label_predates_spec"
    else:
        _gh(repo, ["api", "-X", "POST", f"repos/{owner}/{name}/issues/{issue}/comments",
                   "--input", "-"], stdin=json.dumps({"body": body}))
        remove = label_on
    res.update(verdict="DONE", record_sha256=sha)
    if remove:
        _remove_label(repo, owner, name, issue, pol.label)


# --------------------------------------------------------------------------- CLI


def _report(res: dict[str, Any], *, policy_failure: bool, stage: str) -> int:
    kind = res["verdict"]
    if kind == "NOT_APPROVED":
        issue = res["issue"]
        print(f"E_READINESS_NOT_APPROVED {res['reason']} #{'' if issue is None else issue}", file=sys.stderr)
        return 3
    if kind == "UNAVAILABLE":
        print(f"W_READINESS_UNAVAILABLE {res['reason']}", file=sys.stderr)
        return 0 if (stage == "start" and policy_failure) else 4
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    res, policy_failure = _verdict(args.repo, args.stage, args.spec)
    if args.json:
        print(json.dumps(res))
        sys.stdout.flush()
    return _report(res, policy_failure=policy_failure, stage=args.stage)


def _cmd_post(args: argparse.Namespace) -> int:
    res: dict[str, Any] = {
        "required": False, "issue": None, "label": LABEL_DEFAULT,
        "verdict": "OFF", "reason": None, "record_sha256": None,
    }
    _run_guarded(res, lambda: _post(res, Path(args.repo), args.spec))
    return _report(res, policy_failure=False, stage="ship")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="readiness", description="bd#117 readiness gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", help="evaluate the gate (at --stage ship: also consume)")
    check.add_argument("--stage", required=True, choices=("start", "ship"))
    check.add_argument("--spec", default=None)
    check.add_argument("--repo", default=".")
    check.add_argument("--json", action="store_true")
    post = sub.add_parser("post", help="post the spec record on the bound issue")
    post.add_argument("--spec", required=True)
    post.add_argument("--repo", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _cmd_check(args) if args.cmd == "check" else _cmd_post(args)
    except Exception as exc:  # noqa: BLE001 -- callers treat any crash like "unavailable"
        print(f"W_READINESS_UNAVAILABLE {_one_line(f'internal error: {exc!r}')}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
