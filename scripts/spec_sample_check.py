#!/usr/bin/env python3
"""Provenance: bd#231 (lot S3), audit hal#2320 finding 2, class 1668.

Deterministic spec sample check: numeric/threshold acceptance criteria must be
backed by real samples, and RED tests must not take their expected numbers from
fixtures the lot wrote itself. Standalone, stdlib only, SHADOW by default.
Spec: docs/decisions/2026-10-03-s3-spec-sample-check.md.

Exit code plus one JSON object on stdout is the verdict: 0 clean or flagged in
SHADOW, 1 flagged in ENFORCE, 2 unavailable (fail-closed), 3 flag expired.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import re
import subprocess
import sys

FLAG_NAME = "HAL_SPEC_SAMPLE_ENFORCE"
FLAG_OWNER = "s3-bytedigger (MGR)"
FLAG_EXPIRES = "2026-10-17"

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_UNAVAILABLE = 2
EXIT_EXPIRED = 3

TOLERANCE = 1e-9
MIN_INLINE_LEN = 8
DATA_EXTS = (
    ".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt", ".yaml", ".yml",
    ".xml", ".log", ".html",
)

# Threshold vocabulary. Russian words are written as unicode escapes only.
WORD_OPS = {
    "<": ["less than", "below", "under", "fewer than",
          "\u043c\u0435\u043d\u044c\u0448\u0435",
          "\u043d\u0438\u0436\u0435"],
    "<=": ["at most", "no more than", "not more than", "up to", "max",
           "\u043d\u0435 \u0431\u043e\u043b\u0435\u0435"],
    ">": ["more than", "greater than", "above", "over", "exceeds",
          "\u0431\u043e\u043b\u044c\u0448\u0435",
          "\u0432\u044b\u0448\u0435"],
    ">=": ["at least", "not less than", "min",
           "\u043d\u0435 \u043c\u0435\u043d\u0435\u0435"],
}
SYMBOL_OPS = {"\u2264": "<=", "\u2265": ">="}
FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "=="}
UNITS = ["%", "ms", "seconds", "sec", "s", "KB", "MB", "chars", "characters",
         "tokens", "lines", "rows", "files"]

NUM = r"-?\d+(?:\.\d+)?"
SYM_ALT = "<=|>=|==|\u2264|\u2265|<|>"

PHRASE_OP = {}
for _op, _phrases in WORD_OPS.items():
    for _ph in _phrases:
        PHRASE_OP[_ph] = _op


def _phrase_re(phrase):
    return r"\s+".join(re.escape(w) for w in phrase.split(" "))


_WORD_ALT = "|".join(_phrase_re(p) for p in sorted(PHRASE_OP, key=len, reverse=True))
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))

RE_SYM_FWD = re.compile(r"(?<![-=<>!])(" + SYM_ALT + r")\s*(" + NUM + r")%?")
RE_SYM_REV = re.compile(
    r"(?<![\w.])(" + NUM + r")%?\s*(" + SYM_ALT + r")\s*([A-Za-z_]\w*)"
)
RE_WORD = re.compile(
    r"(?<!\w)(" + _WORD_ALT + r")(?!\w)\s+(" + NUM + r")%?", re.IGNORECASE
)
RE_UNIT = re.compile(r"(?<![\w.])(" + NUM + r")\s*(?:" + _UNIT_ALT + r")(?!\w)", re.IGNORECASE)
RE_AC_LINE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?\**(AC[-_]?\d[0-9A-Za-z_-]*)\**\s*[:.)(" + "\u2014" + r"-]"
)
RE_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
RE_KEYWORD = re.compile(r"(?<!\w)(sample|measured)\s*:", re.IGNORECASE)
RE_MEASURED = re.compile(r"[\s`]*(" + NUM + r"%?(?:\s*,\s*" + NUM + r"%?)*)")
RE_TOKEN_NUM = re.compile(r"(?<![\w.])" + NUM + r"(?!\w|\.\d)")
RE_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class _Fail(Exception):
    """Fail-closed condition; args are (reason, detail)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # never print usage: main emits a JSON verdict
        raise _Fail("bad_usage", message)


# ---------------------------------------------------------------- helpers --


def _default_today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def _mode():
    return "enforce" if os.environ.get(FLAG_NAME) == "1" else "shadow"


def _flag_obj(today):
    expired = today > datetime.date.fromisoformat(FLAG_EXPIRES)
    return {"name": FLAG_NAME, "owner": FLAG_OWNER, "expires": FLAG_EXPIRES, "expired": expired}


def _under(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _finding(code, ac, detail):
    return {"code": code, "ac": ac, "detail": detail}


def _nontrivial(value):
    return (not float(value).is_integer()) or abs(value) >= 10


def _satisfies(measured, op, threshold):
    if op == "<":
        return measured < threshold
    if op == "<=":
        return measured <= threshold
    if op == ">":
        return measured > threshold
    if op == ">=":
        return measured >= threshold
    return abs(measured - threshold) <= TOLERANCE


def _has_token(text, number_text):
    pat = r"(?<![\w.])" + re.escape(number_text) + r"(?!\w|\.\d)"
    return re.search(pat, text) is not None


def _git(root, *args):
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise _Fail("git_failed", "git could not be run")
    return proc.returncode, proc.stdout


def _git_ok(root, *args):
    rc, out = _git(root, *args)
    if rc != 0:
        raise _Fail("git_failed", "git " + " ".join(args) + " failed")
    return out


def _split_z(raw):
    return [os.fsdecode(b) for b in raw.split(b"\0") if b]


def _read_in_root(raw, root):
    """Resolve an in-root regular file; return (rel posix path, text)."""
    full = raw if os.path.isabs(raw) else os.path.join(root, raw)
    real = os.path.realpath(full)
    if not _under(real, root):
        raise _Fail("bad_input", "outside root: " + raw)
    if not os.path.isfile(real):
        raise _Fail("bad_input", "not a file: " + raw)
    try:
        with open(real, "rb") as fh:
            data = fh.read()
    except OSError:
        raise _Fail("bad_input", "unreadable: " + raw)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _Fail("bad_input", "not utf-8: " + raw)
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    return rel, text


def _read_lenient(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None


# ------------------------------------------------------------- spec parse --


def _parse_blocks(text):
    blocks = []
    cur = None
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            if cur is not None:
                cur["lines"].append(line)
            continue
        if not fence:
            if RE_HEADING.match(line):
                cur = None
                continue
            m = RE_AC_LINE.match(line)
            if m:
                cur = {"id": m.group(1), "lines": [line[m.end():]]}
                blocks.append(cur)
                continue
        if cur is not None:
            cur["lines"].append(line)
    return blocks


def _split_paths(seg):
    out = []
    for piece in seg.split(","):
        p = piece.strip(" \t`'\";")
        if p:
            out.append(p)
    return out


def _is_path_token(tok):
    return bool(tok) and not re.search(r"[\s<>=]", tok)


def _sample_segment(seg):
    """Return (cited paths, consumed length) for the text after `sample:`."""
    m = re.match(r"[\s`]*([^\s,;]+)", seg)
    if not m:
        return [], 0
    first = m.group(1).strip("`'\";")
    if not _is_path_token(first):
        return [], 0
    paths = [first]
    pos = m.end()
    while True:
        c = re.compile(r"\s*,\s*([^,;]*)").match(seg, pos)
        if not c:
            break
        item = c.group(1).strip(" \t`'\";")
        if not _is_path_token(item):
            break
        paths.append(item)
        pos = c.end()
    return paths, pos


def _parse_measured(seg):
    m = RE_MEASURED.match(seg)
    if not m:
        return []
    texts = re.findall(NUM, m.group(1))
    return [(t, float(t)) for t in texts]


def _analyze(lines):
    samples = []
    measured = []
    kept = []
    for line in lines:
        hits = list(RE_KEYWORD.finditer(line))
        if not hits:
            kept.append(line)
            continue
        spans = []
        for i, hit in enumerate(hits):
            limit = hits[i + 1].start() if i + 1 < len(hits) else len(line)
            seg = line[hit.end():limit]
            if hit.group(1).lower() == "sample":
                paths, used = _sample_segment(seg)
                for p in paths:
                    if p not in samples:
                        samples.append(p)
            else:
                measured.extend(_parse_measured(seg))
                m = RE_MEASURED.match(seg)
                used = m.end() if m else 0
            spans.append((hit.start(), hit.end() + used))
        out = line
        for s, e in reversed(spans):
            out = out[:s] + " " + out[e:]
        kept.append(out)
    text = "\n".join(kept)

    raw_comps = []
    for m in RE_SYM_FWD.finditer(text):
        raw_comps.append((m.start(), SYMBOL_OPS.get(m.group(1), m.group(1)), m.group(2)))
    for m in RE_SYM_REV.finditer(text):
        op = SYMBOL_OPS.get(m.group(2), m.group(2))
        raw_comps.append((m.start(1), FLIP[op], m.group(1)))
    for m in RE_WORD.finditer(text):
        phrase = re.sub(r"\s+", " ", m.group(1).lower())
        raw_comps.append((m.start(), PHRASE_OP[phrase], m.group(2)))
    raw_comps.sort(key=lambda c: c[0])
    comps = [(op, nt, float(nt)) for _pos, op, nt in raw_comps if _nontrivial(float(nt))]

    nums = []
    for _op, nt, _val in comps:
        if nt not in nums:
            nums.append(nt)
    for m in RE_UNIT.finditer(text):
        if _nontrivial(float(m.group(1))) and m.group(1) not in nums:
            nums.append(m.group(1))
    return {
        "numeric": bool(nums),
        "comps": comps,
        "nums": nums,
        "samples": samples,
        "measured": measured,
    }


# ------------------------------------------------------------ sample check --


def _judge_sample(raw, ctx):
    """Return (ok, text_or_reason) for one cited sample."""
    root = ctx["root"]
    if os.path.isabs(raw):
        full = os.path.normpath(raw)
        real = os.path.realpath(full)
        if _under(full, root):
            inside = True
            rel = os.path.relpath(full, root)
        elif _under(real, root):
            inside = True
            rel = os.path.relpath(real, root)
        else:
            inside = False
            rel = ""
    else:
        full = os.path.normpath(os.path.join(root, raw))
        if not _under(full, root) or not _under(os.path.realpath(full), root):
            return False, "resolves outside root"
        inside = True
        rel = os.path.relpath(full, root)
    if not os.path.lexists(full):
        return False, "missing"
    if os.path.islink(full):
        return False, "symlink"
    if not os.path.isfile(full):
        return False, "not a regular file"
    try:
        if os.path.getsize(full) == 0:
            return False, "empty"
    except OSError:
        return False, "unreadable"
    if inside:
        rel = rel.replace(os.sep, "/")
        if rel in ctx["lot"]:
            return False, "lot-authored"
        rc, _out = _git(root, "cat-file", "-e", ctx["base"] + ":" + rel)
        if rc != 0:
            return False, "untracked at base"
    text = _read_lenient(full)
    if text is None:
        return False, "unreadable"
    return True, text


def _check_acs(blocks, ctx):
    findings = []
    verified = []
    unverified = []
    ok_vals = []
    sample_texts = []
    for blk in blocks:
        info = _analyze(blk["lines"])
        if not info["numeric"]:
            continue
        ac = blk["id"]
        own = []
        if not info["samples"]:
            own.append(_finding("NO_SAMPLE", ac,
                                "numeric AC cites no sample; thresholds: " + ", ".join(info["nums"])))
        valid_texts = []
        for raw in info["samples"]:
            ok, payload = _judge_sample(raw, ctx)
            if ok:
                valid_texts.append(payload)
                sample_texts.append(payload)
            else:
                own.append(_finding("SAMPLE_NOT_REAL", ac, raw + ": " + payload))
        measured = info["measured"]
        comps = info["comps"]
        if valid_texts and not measured:
            own.append(_finding("NO_MEASURED", ac, "sample cited but no measured value"))
        if measured and comps:
            pairs = []
            if len(comps) == 1:
                pairs = [(comps[0], mv) for mv in measured]
            elif len(measured) == len(comps):
                pairs = list(zip(comps, measured))
            else:
                own.append(_finding(
                    "AMBIGUOUS_PAIRING", ac,
                    "%d comparisons but %d measured values" % (len(comps), len(measured))))
            for (op, ntext, nval), (mtext, mval) in pairs:
                if not _satisfies(mval, op, nval):
                    own.append(_finding(
                        "MEASURED_CONTRADICTS", ac,
                        "measured %s does not satisfy %s %s" % (mtext, op, ntext)))
        findings.extend(own)
        if own:
            continue
        for _op, _nt, nval in comps:
            ok_vals.append(nval)
        for nt in info["nums"]:
            ok_vals.append(float(nt))
        for _mt, mval in measured:
            ok_vals.append(mval)
        joined = "\n".join(valid_texts)
        entry = {
            "ac": ac,
            "sample": list(info["samples"]),
            "measured": [mt for mt, _mv in measured],
        }
        if all(_has_token(joined, mt) for mt, _mv in measured):
            verified.append(entry)
        else:
            unverified.append(entry)
    return findings, verified, unverified, ok_vals, sample_texts


# --------------------------------------------------------------- RED check --


def _variants(value):
    out = [repr(value)]
    if isinstance(value, float) and value.is_integer():
        out.append(str(int(value)))
    return out


def _is_approx(func):
    if isinstance(func, ast.Attribute):
        return func.attr == "approx"
    return isinstance(func, ast.Name) and func.id == "approx"


def _py_numbers(tree):
    """Non-trivial numeric constants inside assert statements and approx(...) calls."""
    values = []

    def grab(root_node):
        for sub in ast.walk(root_node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float)) \
                    and not isinstance(sub.value, bool):
                values.append(sub.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            grab(node)
        elif isinstance(node, ast.Call) and _is_approx(node.func):
            grab(node)
    found = []
    seen = set()
    for v in values:
        if not _nontrivial(v) or v in seen:
            continue
        seen.add(v)
        found.append((_variants(v)[0], _variants(v), float(v)))
    return found


def _text_numbers(text):
    found = []
    seen = set()
    for line in text.splitlines():
        if not any(k in line for k in ("assert", "expect", "toBe", "toEqual", "==")):
            continue
        for m in RE_TOKEN_NUM.finditer(line):
            tok = m.group(0)
            val = float(tok)
            if not _nontrivial(val) or tok in seen:
                continue
            seen.add(tok)
            found.append((tok, [tok, tok.lstrip("-")], val))
    return found


def _check_red(reds, ctx, ok_vals, sample_texts):
    findings = []
    lot_texts = []
    for rel in sorted(ctx["lot"]):
        if rel in ctx["skip"] or not rel.lower().endswith(DATA_EXTS):
            continue
        full = os.path.join(ctx["root"], rel)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        text = _read_lenient(full)
        if text is not None:
            lot_texts.append((rel, text))
    for rel, text, tree in reds:
        if tree is not None:
            numbers = _py_numbers(tree)
            literals = [
                sub.value
                for sub in ast.walk(tree)
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                and len(sub.value) >= MIN_INLINE_LEN
            ]
        else:
            numbers = _text_numbers(text)
            literals = []
        for display, variants, value in numbers:
            if any(abs(value - ov) <= TOLERANCE for ov in ok_vals):
                continue
            if any(_has_token(st, v) for st in sample_texts for v in variants):
                continue
            hit = None
            for path, ltext in lot_texts:
                if any(_has_token(ltext, v) for v in variants):
                    hit = path
                    break
            if hit is not None:
                findings.append(_finding(
                    "RED_EXPECTED_FROM_LOT_FIXTURE", "",
                    "%s: asserted %s also appears in lot-authored fixture %s" % (rel, display, hit)))
                continue
            if any(_has_token(lit, v) for lit in literals for v in variants):
                findings.append(_finding(
                    "RED_EXPECTED_FROM_INLINE_FIXTURE", "",
                    "%s: asserted %s also appears in an inline string literal" % (rel, display)))
    return findings


# ------------------------------------------------------------------- scan --


def _parse_args(argv):
    p = _Parser(prog="spec_sample_check.py", add_help=False, allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--red", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--today", default=None)
    p.add_argument("--shadow-log", dest="shadow_log", default=None)
    return p.parse_args(argv)


def _unavailable(reason, today):
    return {
        "status": "unavailable",
        "mode": _mode(),
        "would_block": False,
        "findings": [],
        "verified": [],
        "unverified": [],
        "flag": _flag_obj(today),
        "reason": reason,
    }


def _scan_inner(argv, state):
    args = _parse_args(argv)
    if args.today is not None:
        if not RE_DATE.fullmatch(args.today):
            raise _Fail("bad_usage", "bad --today")
        try:
            state["today"] = datetime.date.fromisoformat(args.today)
        except ValueError:
            raise _Fail("bad_usage", "bad --today")
    today = state["today"]

    root_abs = os.path.abspath(args.root)
    if not os.path.isdir(root_abs):
        raise _Fail("git_failed", "root is not a directory")
    root = os.path.realpath(root_abs)
    top = _git_ok(root, "rev-parse", "--show-toplevel").decode("utf-8", errors="replace").strip()
    if os.path.realpath(top) != root:
        raise _Fail("bad_input", "root is not the work tree top level")
    if args.base.startswith("-"):
        raise _Fail("git_failed", "bad base ref")
    base_raw = _git_ok(root, "rev-parse", "--verify", "--quiet", args.base + "^{commit}")
    base = base_raw.decode("utf-8", errors="replace").strip()
    if not base:
        raise _Fail("git_failed", "base does not resolve")

    spec_rel, spec_text = _read_in_root(args.spec, root)
    red_raw = [x.strip() for x in args.red.split(",") if x.strip()]
    if not red_raw:
        raise _Fail("bad_input", "empty --red list")
    reds = []
    red_rels = []
    for raw in red_raw:
        rel, text = _read_in_root(raw, root)
        if rel in red_rels:
            continue
        red_rels.append(rel)
        reds.append((rel, text))

    rc, _out = _git(root, "cat-file", "-e", base + ":" + spec_rel)
    if rc == 0:
        raise _Fail("base_contains_lot", "spec is tracked at base")

    red_items = []
    for rel, text in reds:
        tree = None
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                raise _Fail("red_unparseable", rel)
        red_items.append((rel, text, tree))

    lot = set(_split_z(_git_ok(
        root, "diff", "--no-renames", "--name-only", "--diff-filter=ACMRT", "-z", base, "--")))
    lot.update(_split_z(_git_ok(root, "ls-files", "--others", "--exclude-standard", "-z")))
    lot.add(spec_rel)
    lot.update(red_rels)
    ctx = {"root": root, "base": base, "lot": lot, "skip": set(red_rels) | {spec_rel}}

    findings, verified, unverified, ok_vals, sample_texts = _check_acs(
        _parse_blocks(spec_text), ctx)
    findings.extend(_check_red(red_items, ctx, ok_vals, sample_texts))

    mode = _mode()
    flag = _flag_obj(today)
    if findings:
        status = "flagged"
    else:
        status = "clean"
    reason = ""
    if mode == "shadow" and flag["expired"]:
        status = "flag_expired"
        reason = "flag_expired"
    verdict = {
        "status": status,
        "mode": mode,
        "would_block": bool(findings),
        "findings": findings,
        "verified": verified,
        "unverified": unverified,
        "flag": flag,
        "reason": reason,
    }
    if args.shadow_log:
        rec = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "spec": spec_rel,
            "mode": mode,
            "would_block": bool(findings),
            "codes": [f["code"] for f in findings],
        }
        try:
            with open(os.path.abspath(args.shadow_log), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            raise _Fail("bad_input", "shadow log not writable")
    return verdict


def _scan(argv):
    state = {"today": _default_today()}
    try:
        return _scan_inner(argv, state)
    except _Fail as exc:
        detail = exc.args[1] if len(exc.args) > 1 else ""
        sys.stderr.write("spec_sample_check: %s: %s\n" % (exc.args[0], detail))
        return _unavailable(exc.args[0], state["today"])


def _exit_code(verdict):
    status = verdict["status"]
    if status == "unavailable":
        return EXIT_UNAVAILABLE
    if status == "flag_expired":
        return EXIT_EXPIRED
    if status == "flagged" and verdict["mode"] == "enforce":
        return EXIT_BLOCKED
    return EXIT_OK


def _emit(verdict):
    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()


def main(argv=None) -> int:
    try:
        verdict = _scan(sys.argv[1:] if argv is None else argv)
        code = _exit_code(verdict)
    except Exception:
        _emit(_unavailable("internal_error", _default_today()))
        return EXIT_UNAVAILABLE
    _emit(verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
