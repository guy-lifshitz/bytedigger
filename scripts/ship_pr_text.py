#!/usr/bin/env python3
"""ship_pr_text.py — PR title and body for scripts/ship.sh (bd#131).

Usage:
  ship_pr_text.py title --state <build-state.yaml> [--repo <dir>] [--base <ref>]
  ship_pr_text.py body  --state <build-state.yaml>

`title` prints one line (spec H1, project `<prefix>:` convention, at most 72
characters; the task string when there is no usable spec). `body` prints the
PR body: spec Scope / Follow-ups sections and the review fields of the state
file, ending with the fixed line and the provenance marker. Stdlib-only,
Python >= 3.9. ship.sh falls back to the task string on any failure here.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

FIXED = "Built via ByteDigger /build pipeline."
# Same literal as readiness.BUILT_MARKER in engine_py/bytedigger_engine/readiness.py.
MARKER = "<!-- bd:built -->"
MARKER_PREFIX = "<!-- bd:"
TRUNC = "(truncated; see build-spec.md)"
BODY_LIMIT = 60000  # UTF-8 bytes
VALUE_LIMIT = 2000  # UTF-8 bytes per one-line state value
TITLE_LIMIT = 72
# Same basename patterns as _is_sensitive in scripts/ship.sh.
SENSITIVE_PATTERNS = (".env", ".env.*", "*.env", "*.env.*", "*.pem", "*.key", "*.credentials*")

KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
SCOPE_RE = re.compile(r"\bscope\b", re.I)
FOLLOWUP_RE = re.compile(r"follow-?ups?", re.I)
PREFIX_RE = re.compile(r"^([^\s:]+):\s")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# --------------------------------------------------------------------------- state


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def _parse_inline(rest):
    """A value on the key line: scalar (str), flow list (list) or flow map (str)."""
    v = rest.strip()
    if v.startswith("["):
        try:
            data = json.loads(v)
        except ValueError:
            data = None
        if isinstance(data, list):
            items = [str(x).strip() for x in data]
        else:
            inner = v[1:-1] if v.endswith("]") else v[1:]
            items = [p.strip(" \t'\"") for p in inner.split(",")]
        return [x for x in items if x]
    if v.startswith("{"):
        return (v[1:-1] if v.endswith("}") else v[1:]).strip()
    return _unquote(v)


def parse_state(text):
    """Top-level `key:` lines only; the last occurrence of a key wins."""
    lines = text.splitlines()
    out = {}
    i, n = 0, len(lines)
    while i < n:
        m = KEY_RE.match(lines[i])
        i += 1
        if not m:
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest == "":
            items = []
            while i < n:
                im = ITEM_RE.match(lines[i])
                if im:
                    items.append(_unquote(im.group(1)))
                    i += 1
                elif not lines[i].strip():
                    i += 1
                else:
                    break
            out[key] = [x for x in items if x]
        else:
            out[key] = _parse_inline(rest)
    return out


def get_text(state, key):
    v = state.get(key)
    if v is None:
        return None
    if isinstance(v, list):
        v = ", ".join(v)
    v = v.strip()
    return v or None


def get_items(state, key):
    v = state.get(key)
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x.strip()]
    return [v] if v.strip() else []


def oneline(s):
    s = " ".join(CONTROL_RE.sub(" ", s).split())
    b = s.encode("utf-8")
    if len(b) > VALUE_LIMIT:
        s = b[:VALUE_LIMIT].decode("utf-8", "ignore") + " …"
    return s


# --------------------------------------------------------------------------- spec


def read_spec(state_path, state):
    """The spec text, or None (no spec, sensitive basename, not a regular file, unreadable)."""
    state_dir = os.path.dirname(os.path.abspath(state_path))
    sp = get_text(state, "spec_path")
    path = os.path.join(state_dir, sp) if sp else os.path.join(state_dir, "build-spec.md")
    base = os.path.basename(path)
    if any(fnmatch.fnmatchcase(base, pat) for pat in SENSITIVE_PATTERNS):
        return None
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return None


def _scan(lines):
    """Yield (index, line, in_fence); fence marker lines count as in a fence."""
    fence = None
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        marker = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if fence is None:
            if marker:
                fence = marker
                yield idx, line, True
                continue
            yield idx, line, False
        else:
            if marker == fence:
                fence = None
            yield idx, line, True


def spec_h1(lines):
    """(index, normalised text) of the first `# ` line outside a fence, or (None, '')."""
    for idx, line, in_fence in _scan(lines):
        if not in_fence and line.startswith("# "):
            return idx, normalise(line[2:])
    return None, ""


def spec_sections(lines, title_idx):
    """(scope_sections, followup_sections): lists of line lists, in spec order."""
    heads = []
    for idx, line, in_fence in _scan(lines):
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            heads.append((idx, len(m.group(1)), m.group(2)))
    scope, follow = [], []
    covered = 0
    for pos, (idx, level, text) in enumerate(heads):
        if idx == title_idx or idx < covered:
            continue
        if SCOPE_RE.search(text):
            target = scope
        elif FOLLOWUP_RE.search(text):
            target = follow
        else:
            continue
        end = len(lines)
        for nidx, nlevel, _ in heads[pos + 1:]:
            if nlevel <= level:
                end = nidx
                break
        covered = end
        sec = [ln for ln in lines[idx:end] if MARKER_PREFIX not in ln]
        while sec and not sec[-1].strip():
            sec.pop()
        if sec:
            target.append(sec)
    return scope, follow


# --------------------------------------------------------------------------- title


def normalise(s):
    return " ".join(CONTROL_RE.sub(" ", s).split())


def _commit_prefix(repo, base):
    """The shared `<P>` of every subject in base..HEAD, or None."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", "log", "--format=%s", base + "..HEAD", "--"],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    subjects = [s for s in proc.stdout.decode("utf-8", "replace").splitlines() if s.strip()]
    prefixes = set()
    for s in subjects:
        m = PREFIX_RE.match(s)
        if not m:
            return None
        prefixes.add(m.group(1))
    return prefixes.pop() if len(prefixes) == 1 else None


def build_title(state_path, state, repo, base):
    task = normalise(get_text(state, "task") or "") or "unnamed-build"
    candidate = ""
    text = read_spec(state_path, state)
    if text is not None:
        candidate = spec_h1(text.splitlines())[1]
    if not candidate:
        candidate = task
    if base:
        prefix = _commit_prefix(repo, base)
        if prefix:
            rest = candidate
            if candidate.startswith(prefix) and (
                len(candidate) == len(prefix) or candidate[len(prefix)] in ": —–-"
            ):
                rest = re.sub(r"^[\s:—–-]+", "", candidate[len(prefix):])
            if rest:
                candidate = prefix + ": " + rest
    if len(candidate) > TITLE_LIMIT:
        cut = candidate[: TITLE_LIMIT + 1].rfind(" ")
        head = candidate[:cut] if cut > 0 else candidate[:TITLE_LIMIT]
        head = head.rstrip(" ,;:—–-")
        candidate = head or candidate[:TITLE_LIMIT]
    return candidate


# --------------------------------------------------------------------------- body


class Cut:
    """A block of lines that may be cut from the end when the body is too big."""

    def __init__(self, lines, trunc=TRUNC):
        self.lines = lines
        self.keep = len(lines)
        self.trunc = trunc

    def out(self):
        res = self.lines[: self.keep]
        if self.keep < len(self.lines):
            res.append(self.trunc)
        return res


def _review_line(state, label, key, suffix_key=None, suffix_fmt="({})"):
    raw = [get_text(state, key)]
    if raw[0] is None:
        return None
    if suffix_key:
        raw.append(get_text(state, suffix_key))
    if any(r and MARKER_PREFIX in r for r in raw):
        return None
    line = "- {}: {}".format(label, oneline(raw[0]))
    if suffix_key and raw[1]:
        line += " " + suffix_fmt.format(oneline(raw[1]))
    return line


def build_body(state_path, state):
    scope_secs, follow_secs = [], []
    text = read_spec(state_path, state)
    if text is not None:
        lines = text.splitlines()
        title_idx, _ = spec_h1(lines)
        scope_secs, follow_secs = spec_sections(lines, title_idx)

    def safe_items(key):
        return [oneline(x) for x in get_items(state, key) if MARKER_PREFIX not in x]

    plan_line = _review_line(state, "Plan review", "plan_review", "plan_review_cycles", "({} cycles)")
    concerns = Cut(["  - " + c for c in safe_items("plan_review_concerns")], "  " + TRUNC) if plan_line else None
    other_review = [ln for ln in (
        _review_line(state, "Test validation (Opus)", "opus_validation", "opus_validation_cycles", "({} cycles)"),
        _review_line(state, "Reviewers", "phase_6_reviewer_verdicts"),
        _review_line(state, "Satisfaction", "review_satisfaction", "phase_6_satisfaction"),
    ) if ln]
    fu_items_lines = ["- " + x for x in safe_items("follow_ups") + safe_items("pre_existing_findings")]

    scope_cuts = [Cut(s) for s in scope_secs]
    follow_cuts = [Cut(s) for s in follow_secs]
    item_cut = Cut(fu_items_lines) if fu_items_lines else None
    concern_cut = concerns if concerns is not None and concerns.lines else None

    def join(cuts):
        res = []
        for c in cuts:
            if res:
                res.append("")
            res.extend(c.out())
        return res

    def render():
        blocks = []
        if scope_cuts:
            blocks.append(["## Scope", ""] + join(scope_cuts))
        review = []
        if plan_line:
            review.append(plan_line)
            if concern_cut is not None:
                review.extend(concern_cut.out())
        review.extend(other_review)
        if review:
            blocks.append(["## Review", ""] + review)
        fu = item_cut.out() if item_cut is not None else []
        secs = join(follow_cuts)
        if fu or secs:
            blocks.append(["## Follow-ups", ""] + fu + ([""] if fu and secs else []) + secs)
        if not blocks:
            return FIXED + "\n" + MARKER + "\n"
        out = []
        for b in blocks:
            out.extend(b)
            out.append("")
        out.extend([FIXED, MARKER])
        return "\n".join(out) + "\n"

    def fits():
        return len(render().encode("utf-8")) <= BODY_LIMIT

    def shrink(cut):
        lo, hi, best = 0, len(cut.lines) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            cut.keep = mid
            if fits():
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        cut.keep = best

    if not fits():
        groups = [follow_cuts, scope_cuts, [item_cut] if item_cut else [], [concern_cut] if concern_cut else []]
        for group in groups:
            for cut in reversed(group):
                if fits():
                    break
                shrink(cut)
            if fits():
                break
    return render()


# --------------------------------------------------------------------------- main


def _emit(text):
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ship_pr_text.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("title")
    t.add_argument("--state", required=True)
    t.add_argument("--repo", default=".")
    t.add_argument("--base", default=None)
    b = sub.add_parser("body")
    b.add_argument("--state", required=True)
    args = ap.parse_args(argv)

    with open(args.state, "rb") as fh:
        state = parse_state(fh.read().decode("utf-8", "replace"))
    if args.cmd == "title":
        _emit(build_title(args.state, state, args.repo, args.base) + "\n")
    else:
        _emit(build_body(args.state, state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
