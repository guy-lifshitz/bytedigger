"""claim_evidence.py -- claim-vs-evidence check for a finished agent turn (bd#141 item 1).

Detects a "done / all tests pass" claim in the final assistant message of the
current turn while the same turn's test-runner output is red. The engine returns
a verdict only; the log-only / block policy, hook registration and the journal
belong to the host.

Public surface (spec: docs/decisions/2026-10-01-bd141-claim-evidence-loop-detector.md):

    Vocabulary, DEFAULT_VOCABULARY, load_vocabulary(path)
    turn_boundary_index(entries) -> int
    slice_current_turn(entries) -> list
    final_assistant_text(entries) -> str
    strip_quoted_text(text) -> str
    claim_phrase(stripped_text, vocab) -> str | None
    runner_id(command, vocab) -> str | None
    bun_fail_identity(line) -> str | None
    text_is_red(text) -> bool
    evaluate_turn(entries, vocab) -> {"outcome", "phrase", "runner"}
    evaluate_transcript(path, vocab) -> same verdict, read from a JSONL file

CLI: ``python -m bytedigger_engine.claim_evidence --transcript PATH [--vocab PATH]``
prints one JSON line and exits 0; a bad ``--vocab`` or a missing ``--transcript``
exits 2 with a message on stderr.

Stdlib only. The vocabulary is data: the default is English, a host extends it
through ``load_vocabulary``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Tuple, Union, cast

__all__ = [
    "Vocabulary",
    "DEFAULT_VOCABULARY",
    "load_vocabulary",
    "turn_boundary_index",
    "slice_current_turn",
    "final_assistant_text",
    "strip_quoted_text",
    "claim_phrase",
    "runner_id",
    "bun_fail_identity",
    "text_is_red",
    "evaluate_turn",
    "evaluate_transcript",
    "main",
]

Entry = Dict[str, Any]
Verdict = Dict[str, Optional[str]]

_FIELDS: Tuple[str, ...] = ("claim", "downgrade", "negation", "runner")


def _merge(base: Tuple[str, ...], extra: Tuple[str, ...]) -> Tuple[str, ...]:
    seen: List[str] = []
    for item in base + extra:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


@dataclass(frozen=True)
class Vocabulary:
    """Four tuples of regex alternatives (joined with ``|`` at match time)."""

    claim: Tuple[str, ...]
    downgrade: Tuple[str, ...]
    negation: Tuple[str, ...]
    runner: Tuple[str, ...]

    def extended(self, other: "Vocabulary") -> "Vocabulary":
        return Vocabulary(
            claim=_merge(self.claim, other.claim),
            downgrade=_merge(self.downgrade, other.downgrade),
            negation=_merge(self.negation, other.negation),
            runner=_merge(self.runner, other.runner),
        )


DEFAULT_VOCABULARY = Vocabulary(
    claim=(
        "done",
        "all green",
        "all tests pass(?:ing|ed)?",
        "tests pass(?:ing|ed)?",
        "suite is green",
        "everything passes",
    ),
    downgrade=(
        "known[- ]red",
        "except",
        "red on main",
        "pre-?existing",
        r"[1-9]\d*\s*(?:fail(?:s|ed|ures?)?)",
        "(?:failing|failed) tests?",
    ),
    negation=("not", "never", "should", "will", "todo", "going to"),
    runner=(
        "bun test",
        "bun run test",
        "pytest",
        "python3? -m pytest",
        "npm test",
        "jest",
        "vitest",
        "go test",
        "cargo test",
        "gh pr checks",
        "gh run (?:view|watch)",
    ),
)


def _bounded_pattern(alternatives: Tuple[str, ...]) -> str:
    joined = "|".join("(?:" + a + ")" for a in alternatives)
    return r"(?<!\w)(?:" + joined + r")(?!\w)"


def load_vocabulary(path: Union[str, Path]) -> Vocabulary:
    """Read a JSON vocabulary file; return DEFAULT_VOCABULARY extended by it."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("cannot read vocabulary file: %s" % exc) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("vocabulary file is not valid JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise ValueError("vocabulary must be a JSON object")
    fields: Dict[str, Tuple[str, ...]] = {name: () for name in _FIELDS}
    for key, value in cast(Dict[str, Any], data).items():
        if key not in _FIELDS:
            raise ValueError("unknown vocabulary key: %r" % (key,))
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError("vocabulary key %r must be a list of strings" % (key,))
        items = tuple(cast(List[str], value))
        for alt in items:
            try:
                re.compile(alt)
            except re.error as exc:
                raise ValueError("vocabulary key %r has an invalid regex %r: %s" % (key, alt, exc)) from exc
        if items:
            try:
                re.compile(_bounded_pattern(items), re.IGNORECASE)
            except re.error as exc:
                raise ValueError("vocabulary key %r has an invalid joined regex: %s" % (key, exc)) from exc
        fields[key] = items
    return DEFAULT_VOCABULARY.extended(Vocabulary(
        claim=fields["claim"],
        downgrade=fields["downgrade"],
        negation=fields["negation"],
        runner=fields["runner"],
    ))


# --------------------------------------------------------------------------
# Transcript helpers
# --------------------------------------------------------------------------

def _drop_sidechain(entries: List[Entry]) -> List[Entry]:
    return [e for e in entries if not (isinstance(e, dict) and e.get("isSidechain") is True)]


def _content(entry: Entry) -> Any:
    msg = entry.get("message")
    if isinstance(msg, dict):
        return cast(Dict[str, Any], msg).get("content")
    return None


def _filtered_boundary(filtered: List[Entry]) -> int:
    idx = -1
    for i, entry in enumerate(filtered):
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        content = _content(entry)
        if isinstance(content, str):
            idx = i
        elif isinstance(content, list):
            for block in content:
                if not (isinstance(block, dict) and cast(Dict[str, Any], block).get("type") == "tool_result"):
                    idx = i
                    break
    return idx


def turn_boundary_index(entries: List[Entry]) -> int:
    """Index (sidechain-filtered list) of the last real user entry, or -1."""
    return _filtered_boundary(_drop_sidechain(entries))


def slice_current_turn(entries: List[Entry]) -> List[Entry]:
    filtered = _drop_sidechain(entries)
    idx = _filtered_boundary(filtered)
    if idx == -1:
        return []
    return filtered[idx:]


def final_assistant_text(entries: List[Entry]) -> str:
    last: Optional[Entry] = None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") == "assistant":
            last = entry
    if last is None:
        return ""
    content = _content(last)
    if isinstance(content, str):
        return content
    parts: List[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                b = cast(Dict[str, Any], block)
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    parts.append(cast(str, b["text"]))
    return "\n".join(parts)


_INLINE_CODE = re.compile(r"`[^`\n]*`")


def strip_quoted_text(text: str) -> str:
    """Drop fenced blocks, quote lines and inline code spans."""
    out: List[str] = []
    fence: Optional[str] = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        if stripped.startswith(">"):
            continue
        out.append(line)
    return _INLINE_CODE.sub("", "\n".join(out))


# --------------------------------------------------------------------------
# Claim
# --------------------------------------------------------------------------

def _bounded(alternatives: Tuple[str, ...]) -> Optional[Pattern[str]]:
    if not alternatives:
        return None
    return re.compile(_bounded_pattern(alternatives), re.IGNORECASE)


_CLAUSE_BOUNDARY = ".;!?—\n"


def _is_negated(text: str, index: int, negation: Optional[Pattern[str]]) -> bool:
    if negation is None:
        return False
    start = 0
    for i in range(index - 1, -1, -1):
        if text[i] in _CLAUSE_BOUNDARY:
            start = i + 1
            break
    return negation.search(text[start:index]) is not None


def claim_phrase(stripped_text: str, vocab: Vocabulary) -> Optional[str]:
    downgrade = _bounded(vocab.downgrade)
    if downgrade is not None and downgrade.search(stripped_text):
        return None
    claim = _bounded(vocab.claim)
    if claim is None:
        return None
    negation = _bounded(vocab.negation)
    for m in claim.finditer(stripped_text):
        if _is_negated(stripped_text, m.start(), negation):
            continue
        end = m.end()
        while end < len(stripped_text) and stripped_text[end] not in _CLAUSE_BOUNDARY:
            end += 1
        if end < len(stripped_text) and stripped_text[end] == "?":
            continue
        return m.group(0)
    return None


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_ENV_ASSIGN = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
_TIMEOUT = re.compile(r"^timeout\s+\d+\S*\s+")


def runner_id(command: str, vocab: Vocabulary = DEFAULT_VOCABULARY) -> Optional[str]:
    if not vocab.runner:
        return None
    pattern = re.compile(r"^(?:" + "|".join("(?:" + a + ")" for a in vocab.runner) + r")(?!\w)", re.IGNORECASE)
    for raw in _SEGMENT_SPLIT.split(command):
        seg = raw.strip()
        seg = _ENV_ASSIGN.sub("", seg)
        seg = _TIMEOUT.sub("", seg)
        m = pattern.match(seg)
        if m:
            return m.group(0).lower()
    return None


# --------------------------------------------------------------------------
# Red detection
# --------------------------------------------------------------------------

_P = r"(?:[^\t\n]*\t){0,2}(?:\S+Z\s+)?"
_BUN_FAIL = re.compile(r"^" + _P + r"\s*\(fail\)\s+(.*?)(?:\s+\[[0-9.]+ms\])?\s*$")
_RED_LINES: Tuple[Pattern[str], ...] = (
    re.compile(r"^" + _P + r"\s*[1-9]\d*\s+fail(?:s|ed|ures?)?\s*$"),
    re.compile(r"^=+ .*\b[1-9]\d* failed\b"),
    re.compile(r"^" + _P + r"\s*FAILED\b"),
    re.compile(r"^\s*[Ee]xit code:?\s*[1-9]"),
    re.compile(r"^[^\t]+\tfail\t"),
)
_RED_WHOLE = re.compile(r'"conclusion"\s*:\s*"failure"|conclusion:\s*failure')


def bun_fail_identity(line: str) -> Optional[str]:
    m = _BUN_FAIL.match(line)
    return m.group(1) if m else None


def text_is_red(text: str) -> bool:
    for line in text.split("\n"):
        if "(pass)" in line:
            continue
        if _RED_WHOLE.search(line):
            return True
        if bun_fail_identity(line) is not None:
            return True
        if any(rx.search(line) for rx in _RED_LINES):
            return True
    return False


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(cast(Dict[str, Any], item).get("text"), str):
                parts.append(cast(str, cast(Dict[str, Any], item)["text"]))
            else:
                parts.append("")
        return "\n".join(parts)
    return ""


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------

def _verdict(outcome: str, phrase: Optional[str], runner: Optional[str]) -> Verdict:
    return {"outcome": outcome, "phrase": phrase, "runner": runner}


def evaluate_turn(entries: List[Entry], vocab: Vocabulary = DEFAULT_VOCABULARY) -> Verdict:
    turn = slice_current_turn(entries)
    final = final_assistant_text(turn) if turn else ""
    if not turn or final == "":
        return _verdict("no-turn", None, None)
    phrase = claim_phrase(strip_quoted_text(final), vocab)
    if phrase is None:
        return _verdict("no-claim", None, None)

    runner_by_id: Dict[str, str] = {}
    last_red: Dict[str, bool] = {}
    for entry in turn:
        content = _content(entry) if isinstance(entry, dict) else None
        if not isinstance(content, list):
            continue
        for raw_block in content:
            if not isinstance(raw_block, dict):
                continue
            block = cast(Dict[str, Any], raw_block)
            btype = block.get("type")
            if btype == "tool_use":
                tid = block.get("id")
                if block.get("name") != "Bash" or not isinstance(tid, str):
                    continue
                inp = block.get("input")
                if not isinstance(inp, dict):
                    continue
                inp_d = cast(Dict[str, Any], inp)
                cmd = inp_d.get("command")
                if not isinstance(cmd, str) or inp_d.get("run_in_background") is True:
                    continue
                rid = runner_id(cmd, vocab)
                if rid is not None:
                    runner_by_id[tid] = rid
            elif btype == "tool_result":
                use_id = block.get("tool_use_id")
                if not isinstance(use_id, str) or use_id not in runner_by_id:
                    continue
                rid2 = runner_by_id[use_id]
                red = (block.get("is_error") is True and not rid2.startswith("gh ")) or text_is_red(
                    _result_text(block.get("content")))
                last_red.pop(rid2, None)
                last_red[rid2] = red
    if not last_red:
        return _verdict("no-runner", phrase, None)
    for rid3, red3 in last_red.items():
        if red3:
            return _verdict("fire", phrase, rid3)
    return _verdict("clear", phrase, None)


def evaluate_transcript(path: Union[str, Path], vocab: Vocabulary = DEFAULT_VOCABULARY) -> Verdict:
    entries: List[Entry] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _verdict("no-turn", None, None)
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(cast(Entry, obj))
    return evaluate_turn(entries, vocab)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claim_evidence",
        description="Check a transcript's final claim against same-turn runner output.",
    )
    parser.add_argument("--transcript", required=True, help="path to a Claude Code transcript JSONL")
    parser.add_argument("--vocab", default=None, help="optional JSON vocabulary extension")
    args = parser.parse_args(argv)
    vocab = DEFAULT_VOCABULARY
    if args.vocab is not None:
        try:
            vocab = load_vocabulary(args.vocab)
        except ValueError as exc:
            sys.stderr.write("claim_evidence: %s\n" % exc)
            return 2
    sys.stdout.write(json.dumps(evaluate_transcript(args.transcript, vocab), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
