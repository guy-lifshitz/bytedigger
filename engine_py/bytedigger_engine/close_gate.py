"""close_gate.py -- close gate: a "done" claim while the bound spec went untouched (bd#141 item 7).

Flags a "done" claim in the final assistant message of the current turn when the
spec the work is bound to has not been successfully edited for ``threshold`` or
more main-chain tool calls. The engine returns a verdict only; hook registration,
which spec belongs to the live work, the off switch and the block / log-only
policy belong to the host.

Public surface (spec: docs/decisions/2026-10-01-bd141-close-gate.md):

    evaluate(entries, spec_path, threshold=10, vocab=DEFAULT_VOCABULARY, cwd=None) -> Verdict
    latch(state_dir, run_id) -> bool

CLI: ``python -m bytedigger_engine.close_gate --transcript P --spec S [--threshold N]
[--vocab V] [--cwd D] [--state-dir D --run-id R]`` prints one JSON line and exits 0;
usage errors exit 2 with a message on stderr.

Stdlib only. Claim detection is imported from ``claim_evidence``, not re-implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, cast

from bytedigger_engine.claim_evidence import (
    DEFAULT_VOCABULARY,
    Vocabulary,
    claim_phrase,
    final_assistant_text,
    load_vocabulary,
    slice_current_turn,
    strip_quoted_text,
)

__all__ = ["evaluate", "latch", "evaluate_transcript", "main"]

Entry = Dict[str, Any]
Verdict = Dict[str, Any]
PathLike = Union[str, "os.PathLike[str]"]

_TOUCH_KEYS: Dict[str, str] = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}


def _verdict(outcome: str, phrase: Optional[str], calls: Optional[int], threshold: int) -> Verdict:
    return {"outcome": outcome, "phrase": phrase, "calls": calls, "threshold": threshold}


def _blocks(entry: Entry, kind: str) -> List[Dict[str, Any]]:
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return []
    content = cast(Dict[str, Any], msg).get("content")
    if not isinstance(content, list):
        return []
    out: List[Dict[str, Any]] = []
    for raw in content:
        if isinstance(raw, dict) and cast(Dict[str, Any], raw).get("type") == kind:
            out.append(cast(Dict[str, Any], raw))
    return out


def _resolve(path: str, cwd: str) -> str:
    return os.path.realpath(os.path.join(cwd, path))


def _is_touch(block: Dict[str, Any], spec_real: str, cwd: str, errored: Dict[str, bool]) -> bool:
    key = _TOUCH_KEYS.get(str(block.get("name")))
    if key is None:
        return False
    inp = block.get("input")
    if not isinstance(inp, dict):
        return False
    target = cast(Dict[str, Any], inp).get(key)
    if not isinstance(target, str) or not target:
        return False
    if _resolve(target, cwd) != spec_real:
        return False
    tid = block.get("id")
    return not (isinstance(tid, str) and errored.get(tid, False))


def _count_calls(entries: List[Entry], spec_real: str, cwd: str) -> int:
    main = [e for e in entries if isinstance(e, dict) and e.get("isSidechain") is not True]
    errored: Dict[str, bool] = {}
    for entry in main:
        for res in _blocks(entry, "tool_result"):
            use_id = res.get("tool_use_id")
            if isinstance(use_id, str):
                errored[use_id] = res.get("is_error") is True
    calls = 0
    for entry in main:
        if entry.get("type") != "assistant":
            continue
        for block in _blocks(entry, "tool_use"):
            if _is_touch(block, spec_real, cwd, errored):
                calls = 0
            else:
                calls += 1
    return calls


def evaluate(
    entries: List[Entry],
    spec_path: PathLike,
    threshold: int = 10,
    vocab: Vocabulary = DEFAULT_VOCABULARY,
    cwd: Optional[PathLike] = None,
) -> Verdict:
    spec = os.fspath(spec_path)
    if not os.path.isfile(spec):
        return _verdict("no-spec", None, None, threshold)
    turn = slice_current_turn(entries)
    if not turn:
        return _verdict("no-turn", None, None, threshold)
    final = final_assistant_text(turn)
    phrase = claim_phrase(strip_quoted_text(final), vocab)
    if phrase is None:
        return _verdict("no-claim", None, None, threshold)
    if os.path.basename(spec).lower() in final.lower():
        return _verdict("mentioned", phrase, None, threshold)
    base = os.fspath(cwd) if cwd is not None else os.getcwd()
    calls = _count_calls(entries, _resolve(spec, base), base)
    return _verdict("fire" if calls >= threshold else "clear", phrase, calls, threshold)


def latch(state_dir: PathLike, run_id: str) -> bool:
    """True when this call created the run's latch file (or the latch could not be used)."""
    name = "close-gate-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16] + ".latch"
    try:
        os.makedirs(state_dir, exist_ok=True)
    except OSError:
        return True
    try:
        fd = os.open(os.path.join(os.fspath(state_dir), name), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    except OSError:
        return True
    os.close(fd)
    return True


def _read_entries(path: PathLike) -> List[Entry]:
    entries: List[Entry] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return entries
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(cast(Entry, obj))
    return entries


def evaluate_transcript(
    transcript: PathLike,
    spec_path: PathLike,
    threshold: int = 10,
    vocab: Vocabulary = DEFAULT_VOCABULARY,
    cwd: Optional[PathLike] = None,
) -> Verdict:
    return evaluate(_read_entries(transcript), spec_path, threshold, vocab, cwd)


def _fail(message: str) -> int:
    sys.stderr.write("close_gate: %s\n" % message)
    return 2


def _parse_threshold(raw: str) -> Optional[int]:
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 1 else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="close_gate",
        description="Flag a done claim made while the bound spec went untouched.",
    )
    parser.add_argument("--transcript", required=True, help="path to a Claude Code transcript JSONL")
    parser.add_argument("--spec", required=True, help="path to the spec the work is bound to")
    parser.add_argument("--threshold", default="10", help="tool calls since the last spec touch (>= 1)")
    parser.add_argument("--vocab", default=None, help="optional JSON vocabulary extension")
    parser.add_argument("--cwd", default=None, help="base for relative tool paths")
    parser.add_argument("--state-dir", default=None, help="latch directory (needs --run-id)")
    parser.add_argument("--run-id", default=None, help="latch key (needs --state-dir)")
    args = parser.parse_args(argv)
    threshold = _parse_threshold(args.threshold)
    if threshold is None:
        return _fail("--threshold must be an integer >= 1")
    if (args.state_dir is None) != (args.run_id is None):
        return _fail("--state-dir and --run-id must be given together")
    vocab = DEFAULT_VOCABULARY
    if args.vocab is not None:
        try:
            vocab = load_vocabulary(args.vocab)
        except ValueError as exc:
            return _fail(str(exc))
    verdict = evaluate_transcript(args.transcript, args.spec, threshold, vocab, args.cwd)
    if verdict["outcome"] == "fire" and args.state_dir is not None:
        if not latch(args.state_dir, args.run_id):
            verdict["outcome"] = "latched"
    sys.stdout.write(json.dumps(verdict, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
