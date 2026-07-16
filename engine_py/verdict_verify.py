"""verdict_verify.py — deterministic cross-check for self-attested LLM gate verdicts.

Agreement: DEE24674-A759-45F1-9488-416CA22E6A74 (GH398, audit R2 L3).

Two checks:
  - anchor-verify: a `<!-- verdict-anchor ... -->` block embeds sha256 hashes
    of the spec + RED files the verdict doc claims to review; recompute and
    compare against the files on disk.
  - ac-parity: the AC-id set in the frozen spec vs. the checklist rows in the
    verdict doc must match, and no checklist row may claim FAIL.

All classification functions (`verify_anchor`, `verify_ac_parity`) are pure
and ALWAYS return ok=True, reject=False — they only CLASSIFY via `.result`.
Enforce-vs-warn reject decisions are made by the `run()` CLI entry point (and
by the wrapper hook in engine-py-audit-gate.py), never by the classifiers
themselves.

AC-id regexes are DUPLICATED from phase_6_review.py:_parse_spec_ac_ids
(parity source of truth for spec AC-id extraction; kept in sync by hand
since phase_6_review.py is engine_py prod and out of scope for this ship —
see spec §5 Files NOT in scope).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ANCHOR_RE = re.compile(r"<!--\s*verdict-anchor\s*(.*?)-->", re.DOTALL)
ANCHOR_LINE_RE = re.compile(
    r"^\s*(spec|red):\s*(\S+)\s+sha256:([0-9a-f]{64})\s*$", re.MULTILINE
)

# AC id regexes — DUPLICATED from phase_6_review.py:_parse_spec_ac_ids (parity source).
_AC_SECTION_HEADER_RE = re.compile(
    r"^#{2,6}\s.*acceptance criteria", re.IGNORECASE | re.MULTILINE
)
_AC_TABLE_RE = re.compile(r"^\|\s*AC[- ]?([A-Za-z0-9_.-]+)\s*\|", re.MULTILINE)
_AC_NUMBERED_RE = re.compile(r"^\s{0,3}(\d+)[.)]\s", re.MULTILINE)
_CHECKLIST_ROW_RE = re.compile(
    r"^\s*[-*]\s*AC[- ]?([A-Za-z0-9_.-]+)\s*:\s*(PASS|FAIL)\b",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class VerifyResult:
    ok: bool
    reject: bool
    result: str
    reason: str
    details: dict = field(default_factory=dict)


def _normalize_ac_id(raw: str) -> str:
    """Normalize a bare/raw AC id (e.g. '1', 'AC1') to canonical 'AC1' form."""
    raw = raw.strip()
    if raw.upper().startswith("AC"):
        return "AC" + raw[2:]
    return f"AC{raw}"


def parse_anchor(doc_text: str) -> list[tuple[str, str, str]]:
    """Parse the verdict-anchor comment block into (kind, relpath, sha256) tuples."""
    match = ANCHOR_RE.search(doc_text)
    if not match:
        return []
    block = match.group(1)
    out: list[tuple[str, str, str]] = []
    for line_match in ANCHOR_LINE_RE.finditer(block):
        kind, relpath, sha = line_match.groups()
        out.append((kind, relpath, sha))
    return out


def file_sha256(path) -> str:
    """sha256 of the file's raw bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_anchor_block(entries: list[tuple[str, str, str]]) -> str:
    """Build a verdict-anchor comment block from (kind, relpath, sha) tuples."""
    lines = "\n".join(f"{kind}: {relpath} sha256:{sha}" for kind, relpath, sha in entries)
    return f"<!-- verdict-anchor\n{lines}\n-->"


def emit_anchor(doc_path, spec_path, red_paths, repo_root) -> str:
    """Compute sha256 anchors for spec+red files and write/replace the block in doc."""
    repo_root = Path(repo_root)
    entries: list[tuple[str, str, str]] = []
    spec_relpath = Path(spec_path).resolve().relative_to(repo_root.resolve()).as_posix()
    entries.append(("spec", spec_relpath, file_sha256(spec_path)))
    for red_path in red_paths:
        red_relpath = Path(red_path).resolve().relative_to(repo_root.resolve()).as_posix()
        entries.append(("red", red_relpath, file_sha256(red_path)))

    block = build_anchor_block(entries)

    doc_path = Path(doc_path)
    doc_text = doc_path.read_text(encoding="utf-8")
    if ANCHOR_RE.search(doc_text):
        new_text = ANCHOR_RE.sub(block, doc_text, count=1)
    else:
        new_text = doc_text + "\n" + block + "\n"
    doc_path.write_text(new_text, encoding="utf-8")
    return block


def verify_anchor(doc_text: str, repo_root, *, read=None, exists=None) -> VerifyResult:
    """Classify a doc's verdict-anchor block against on-disk files.

    Always returns ok=True, reject=False — classification only, no reject
    decision. `read`/`exists` are injectable seams (default: real filesystem).
    """
    repo_root = Path(repo_root)
    if exists is None:
        exists = lambda p: Path(p).exists()  # noqa: E731
    if read is None:
        read = lambda p: Path(p).read_bytes()  # noqa: E731

    anchors = parse_anchor(doc_text)
    if not anchors:
        return VerifyResult(
            ok=True,
            reject=False,
            result="UNANCHORED",
            reason="no verdict-anchor block found",
            details={},
        )

    for kind, relpath, embedded_sha in anchors:
        full_path = repo_root / relpath
        if not exists(full_path):
            return VerifyResult(
                ok=True,
                reject=False,
                result="DRIFT",
                reason=f"unreadable: {full_path}",
                details={"kind": kind, "path": relpath},
            )
        try:
            actual_sha = hashlib.sha256(read(full_path)).hexdigest()
        except OSError:
            return VerifyResult(
                ok=True,
                reject=False,
                result="DRIFT",
                reason=f"unreadable: {full_path}",
                details={"kind": kind, "path": relpath},
            )
        if actual_sha != embedded_sha:
            return VerifyResult(
                ok=True,
                reject=False,
                result="DRIFT",
                reason=f"sha256 mismatch for {relpath}",
                details={"kind": kind, "path": relpath},
            )

    return VerifyResult(
        ok=True, reject=False, result="VERIFIED", reason="all anchors match", details={}
    )


def parse_spec_ac_ids(spec_text: str) -> set[str]:
    """Extract the set of AC ids referenced in the spec's Acceptance Criteria section.

    Section-scoped (mirrors phase_6_review.py:_parse_spec_ac_ids): finds the
    first Acceptance Criteria header, slices to the next `^#{1,2}\\s` heading,
    and parses table rows first (numbered-list fallback only if zero table
    ids found), all WITHIN that slice. No header found → empty set.
    """
    lines = spec_text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if _AC_SECTION_HEADER_RE.match(line):
            header_idx = i
            break
    if header_idx is None:
        return set()

    end_idx = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if re.match(r"^#{1,2}\s", lines[i]):
            end_idx = i
            break

    section_text = "\n".join(lines[header_idx:end_idx])

    ids: set[str] = set()
    for m in _AC_TABLE_RE.finditer(section_text):
        ids.add(_normalize_ac_id(m.group(1)))
    if not ids:
        for m in _AC_NUMBERED_RE.finditer(section_text):
            ids.add(_normalize_ac_id(m.group(1)))
    return ids


def parse_checklist(doc_text: str) -> dict[str, str] | None:
    """Parse checklist rows '- ACn: PASS|FAIL' into {id: PASS|FAIL}. None if no rows."""
    result: dict[str, str] = {}
    for m in _CHECKLIST_ROW_RE.finditer(doc_text):
        raw_id, verdict = m.groups()
        result[_normalize_ac_id(raw_id)] = verdict.upper()
    if not result:
        return None
    return result


def _classify_parity(spec_ids, checklist, *, ignore_extra: bool = False) -> VerifyResult:
    """Classify AC-id parity given already-parsed spec_ids + checklist. Shared core."""
    spec_id_set = set(spec_ids)
    spec_ac_count = len(spec_id_set)

    if not spec_id_set:
        return VerifyResult(
            ok=True,
            reject=False,
            result="SKIP",
            reason="spec has zero AC ids",
            details={"missing": [], "extra": [], "failed": [], "spec_ac_count": spec_ac_count},
        )

    if checklist is None:
        return VerifyResult(
            ok=True,
            reject=False,
            result="MISSING",
            reason="no checklist rows",
            details={
                "missing": sorted(spec_id_set),
                "extra": [],
                "failed": [],
                "spec_ac_count": spec_ac_count,
            },
        )

    missing = spec_id_set - checklist.keys()
    if missing:
        return VerifyResult(
            ok=True,
            reject=False,
            result="MISSING",
            reason=f"checklist missing {sorted(missing)}",
            details={
                "missing": sorted(missing),
                "extra": [],
                "failed": [],
                "spec_ac_count": spec_ac_count,
            },
        )

    extra = checklist.keys() - spec_id_set
    if extra and not ignore_extra:
        return VerifyResult(
            ok=True,
            reject=False,
            result="EXTRA",
            reason=f"checklist has extra ids {sorted(extra)}",
            details={
                "missing": [],
                "extra": sorted(extra),
                "failed": [],
                "spec_ac_count": spec_ac_count,
            },
        )

    failed = sorted(k for k, v in checklist.items() if v == "FAIL" and k in spec_id_set)
    if failed:
        return VerifyResult(
            ok=True,
            reject=False,
            result="FAIL_CLAIMED",
            reason=f"checklist claims FAIL for {failed}",
            details={
                "missing": [],
                "extra": [],
                "failed": failed,
                "spec_ac_count": spec_ac_count,
            },
        )

    return VerifyResult(
        ok=True,
        reject=False,
        result="PARITY_OK",
        reason="all AC ids match, all PASS",
        details={"missing": [], "extra": [], "failed": [], "spec_ac_count": spec_ac_count},
    )


def verify_ac_parity(spec_text: str, checklist_text: str) -> VerifyResult:
    """Classify AC-id parity between spec and checklist. Classification only."""
    spec_ids = parse_spec_ac_ids(spec_text)
    checklist = parse_checklist(checklist_text)
    return _classify_parity(spec_ids, checklist)


def append_log(event: dict, *, log_path, now_iso: str) -> None:
    """Append one JSON object per line to log_path. Creates file+dirs if missing."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("ts", now_iso)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


GRANDFATHER_CUTOFF = "2026-07-09"

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


def is_grandfathered(doc_path, *, env=os.environ) -> bool:
    """True iff doc filename's date prefix predates the anchor-emission cutoff."""
    name = Path(doc_path).name
    match = _DATE_PREFIX_RE.match(name)
    if not match:
        return False
    cutoff = env.get("HAL_VERDICT_ANCHOR_GRANDFATHER_CUTOFF") or GRANDFATHER_CUTOFF
    return match.group(1) < cutoff


def anchor_gate_decision(
    result: str, doc_path, *, enforce: bool, env=os.environ
) -> tuple[bool, str]:
    """Single source of truth for anchor-mode reject decisions (CLI + wrapper)."""
    if result == "UNANCHORED" and is_grandfathered(doc_path, env=env):
        return (False, "UNANCHORED_GRANDFATHERED")
    if result in ("DRIFT", "UNANCHORED") and enforce:
        return (True, result)
    return (False, result)


def _default_repo_root() -> str:
    return os.getcwd()


def run(argv=None, *, env=os.environ) -> int:
    """CLI entry point. Returns process exit code (0/1/2)."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="verdict-verify",
        description=(
            "Deterministic cross-check for self-attested LLM gate verdicts. "
            "Kill-switch: HAL_VERDICT_VERIFY_GATE=0 skips entirely."
        ),
    )
    parser.add_argument("--mode", choices=("anchor", "ac-parity", "emit"), required=True)
    parser.add_argument("--doc", metavar="PATH", required=True)
    parser.add_argument("--spec", metavar="PATH", required=False, default=None)
    parser.add_argument("--repo-root", metavar="PATH", required=False, default=None)
    parser.add_argument("--red", metavar="PATH[,PATH…]", required=False, default=None)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return 0 if code == 0 else 2

    now_iso = datetime.now(timezone.utc).isoformat()
    log_path = env.get("HAL_VERDICT_VERIFY_LOG") or str(
        Path(args.repo_root or _default_repo_root()) / "SHARED" / "state" / "verdict-verify-gate.jsonl"
    )
    enforce = env.get("HAL_VERDICT_VERIFY_ENFORCE") == "1"

    if env.get("HAL_VERDICT_VERIFY_GATE") == "0":
        append_log(
            {
                "mode": args.mode,
                "doc": args.doc,
                "result": "SKIP",
                "reason": "kill-switch HAL_VERDICT_VERIFY_GATE=0",
                "enforce": enforce,
            },
            log_path=log_path,
            now_iso=now_iso,
        )
        return 0

    repo_root = args.repo_root or _default_repo_root()

    if args.mode == "emit":
        if not args.spec:
            print("error: --mode emit requires --spec", file=sys.stderr)
            return 2
        if not args.red:
            print("error: --mode emit requires --red", file=sys.stderr)
            return 2
        red_paths = [p for p in args.red.split(",") if p]
        try:
            for p in [args.doc, args.spec, *red_paths]:
                if not Path(p).exists():
                    raise OSError(f"path does not exist: {p}")
            emit_anchor(args.doc, args.spec, red_paths, repo_root)
        except OSError as exc:
            print(f"error: cannot emit anchor: {exc}", file=sys.stderr)
            return 2

        append_log(
            {
                "mode": "emit",
                "doc": args.doc,
                "result": "EMITTED",
                "reason": "anchor written",
                "enforce": enforce,
            },
            log_path=log_path,
            now_iso=now_iso,
        )
        return 0

    try:
        doc_text = Path(args.doc).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read --doc: {exc}", file=sys.stderr)
        return 2

    if args.mode == "anchor":
        result = verify_anchor(doc_text, repo_root)
    else:
        if not args.spec:
            print("error: --mode ac-parity requires --spec", file=sys.stderr)
            return 2
        try:
            spec_text = Path(args.spec).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read --spec: {exc}", file=sys.stderr)
            return 2
        result = verify_ac_parity(spec_text, doc_text)

    if args.mode == "anchor":
        blocking, logged_result = anchor_gate_decision(
            result.result, args.doc, enforce=enforce, env=env
        )
        append_log(
            {
                "mode": args.mode,
                "doc": args.doc,
                "result": logged_result,
                "reason": result.reason,
                "enforce": enforce,
            },
            log_path=log_path,
            now_iso=now_iso,
        )
        return 1 if blocking else 0

    append_log(
        {
            "mode": args.mode,
            "doc": args.doc,
            "result": result.result,
            "reason": result.reason,
            "enforce": enforce,
        },
        log_path=log_path,
        now_iso=now_iso,
    )

    reject_candidate = result.result not in ("VERIFIED", "PARITY_OK", "SKIP")
    if reject_candidate and enforce:
        return 1
    return 0
