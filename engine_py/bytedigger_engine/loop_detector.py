"""loop_detector.py -- tool-call loop detector for agent hosts (bd#141 item 2).

Folds each tool call into a short per-session window and flags three loop
shapes: the same call repeated, two calls alternating, and one tool failing
over and over. An alert is advisory text only; the host decides what to do.

Public surface (spec: docs/decisions/2026-10-01-bd141-claim-evidence-loop-detector.md):

    Thresholds, thresholds_from_env(environ)
    call_hash(tool_input) -> str
    empty_state(), load_state(path), save_state(path, state)
    observe(state, tool, tool_input, failed, thresholds) -> (state, alert | None)
    process_event(payload, state_dir, log_path=None, thresholds=Thresholds()) -> str | None

``observe`` is pure (it never mutates its argument); ``process_event`` never
raises. CLI: ``python -m bytedigger_engine.loop_detector --state-dir DIR [--log PATH]``
reads one hook payload from stdin, prints the advisory line when there is one,
and always exits 0. ``BD_LOOP_DETECTOR=0`` turns it into a no-op.

Stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union, cast

__all__ = [
    "Thresholds",
    "thresholds_from_env",
    "call_hash",
    "empty_state",
    "load_state",
    "save_state",
    "observe",
    "process_event",
    "main",
]

State = Dict[str, Any]
Alert = Dict[str, str]
PathLike = Union[str, Path]


@dataclass(frozen=True)
class Thresholds:
    window: int = 20
    repeat: int = 3
    thrash_span: int = 8
    thrash_calls: int = 5
    thrash_fails: int = 3
    cooldown: int = 4


_ENV_NAMES: Tuple[Tuple[str, str], ...] = (
    ("window", "BD_LOOP_WINDOW"),
    ("repeat", "BD_LOOP_REPEAT"),
    ("thrash_span", "BD_LOOP_THRASH_SPAN"),
    ("thrash_calls", "BD_LOOP_THRASH_CALLS"),
    ("thrash_fails", "BD_LOOP_THRASH_FAILS"),
    ("cooldown", "BD_LOOP_COOLDOWN"),
)


def thresholds_from_env(environ: Mapping[str, str]) -> Thresholds:
    values: Dict[str, int] = {}
    for field, name in _ENV_NAMES:
        raw = environ.get(name)
        if raw is None:
            continue
        text = raw.strip()
        if not text.isdigit():
            continue
        try:
            num = int(text)
        except ValueError:
            continue
        if num > 0:
            values[field] = num
    return Thresholds(**values)


def call_hash(tool_input: Any) -> str:
    text = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def empty_state() -> State:
    return {"v": 1, "n": 0, "win": [], "last_alert_n": None, "live": {}}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_state(path: PathLike) -> State:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    d = cast(Dict[str, Any], data)
    if not _is_int(d.get("v")) or d.get("v") != 1:
        return empty_state()
    if not _is_int(d.get("n")):
        return empty_state()
    last = d.get("last_alert_n")
    if last is not None and not _is_int(last):
        return empty_state()
    win_raw = d.get("win")
    live_raw = d.get("live")
    if not isinstance(win_raw, list) or not isinstance(live_raw, dict):
        return empty_state()
    win: List[Dict[str, Any]] = []
    for item in win_raw:
        if not isinstance(item, dict):
            return empty_state()
        it = cast(Dict[str, Any], item)
        if not (isinstance(it.get("t"), str) and isinstance(it.get("h"), str)
                and isinstance(it.get("f"), bool)):
            return empty_state()
        win.append({"t": it["t"], "h": it["h"], "f": it["f"]})
    live: Dict[str, int] = {}
    for key, val in live_raw.items():
        if not isinstance(key, str) or not _is_int(val):
            return empty_state()
        live[key] = cast(int, val)
    return {"v": 1, "n": d["n"], "win": win, "last_alert_n": last, "live": live}


def save_state(path: PathLike, state: State) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / ("%s.%d.tmp" % (target.name, os.getpid()))
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(str(tmp), str(target))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Observe
# --------------------------------------------------------------------------

_RANK = {"thrash": 3, "oscillation": 2, "repeat": 1}


def observe(
    state: State,
    tool: str,
    tool_input: Any,
    failed: bool,
    thresholds: Thresholds,
) -> Tuple[State, Optional[Alert]]:
    new: State = copy.deepcopy(state)
    n = cast(int, new["n"]) + 1
    new["n"] = n
    win = cast(List[Dict[str, Any]], new["win"])
    win.append({"t": tool, "h": call_hash(tool_input), "f": bool(failed)})
    if len(win) > thresholds.window:
        del win[: len(win) - thresholds.window]
    live = cast(Dict[str, int], new["live"])

    def sig(item: Dict[str, Any]) -> str:
        return "%s:%s" % (item["t"], item["h"])

    hits: List[Tuple[str, str, str]] = []  # (kind, key, detail)
    current = sig(win[-1])
    count = sum(1 for item in win if sig(item) == current)
    if count >= thresholds.repeat:
        hits.append(("repeat", "rep:" + current,
                     "%s x%d identical calls in the last %d" % (tool, count, thresholds.window)))
    if len(win) >= 4:
        a, b, c, d = (sig(item) for item in win[-4:])
        if a == c and b == d and a != b:
            hits.append(("oscillation", "osc:%s|%s" % (min(a, b), max(a, b)),
                         "%s/%s alternate a-b-a-b" % (win[-2]["t"], win[-1]["t"])))
    span = win[-thresholds.thrash_span:]
    same = [item for item in span if item["t"] == tool]
    fails = sum(1 for item in same if item["f"])
    if len(same) >= thresholds.thrash_calls and fails >= thresholds.thrash_fails:
        hits.append(("thrash", "thr:" + tool,
                     "%s %d of the last %d, %d failed" % (tool, len(same), thresholds.thrash_span, fails)))

    for key in [k for k, v in live.items() if n - v > thresholds.window]:
        del live[key]

    alert: Optional[Alert] = None
    if hits:
        last_alert = cast(Optional[int], new["last_alert_n"])
        if any(key in live for _, key, _ in hits):
            for _, key, _ in hits:
                live[key] = n
        elif last_alert is None or n - last_alert > thresholds.cooldown:
            new["last_alert_n"] = n
            for _, key, _ in hits:
                live[key] = n
            kind, key, detail = max(hits, key=lambda h: _RANK[h[0]])
            alert = {"kind": kind, "key": key, "detail": detail}
    return new, alert


# --------------------------------------------------------------------------
# Event
# --------------------------------------------------------------------------

def _advisory(alert: Alert) -> str:
    return ("<system-reminder>[LOOP DETECTED] %s: %s. Advisory only: stop and change approach; "
            "repeating the same call will not give a different result.</system-reminder>"
            % (alert["kind"], alert["detail"]))


def process_event(
    payload: Any,
    state_dir: PathLike,
    log_path: Optional[PathLike] = None,
    thresholds: Thresholds = Thresholds(),
) -> Optional[str]:
    try:
        if not isinstance(payload, dict):
            return None
        p = cast(Dict[str, Any], payload)
        agent_id = p.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return None
        tool = p.get("tool_name")
        if not isinstance(tool, str) or not tool:
            return None
        raw_sid = p.get("session_id")
        sid = re.sub(r"[^A-Za-z0-9_-]", "", raw_sid) if isinstance(raw_sid, str) else ""
        if not sid:
            sid = "nosession"
        failed = p.get("hook_event_name") == "PostToolUseFailure"
        state_file = Path(state_dir) / (sid + ".json")
        state = load_state(state_file)
        new_state, alert = observe(state, tool, p.get("tool_input"), failed, thresholds)
        try:
            save_state(state_file, new_state)
        except Exception:
            return None
        if alert is None:
            return None
        if log_path is not None:
            try:
                row = {
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "session_id": sid,
                    "kind": alert["kind"],
                    "tool": tool,
                    "n": new_state["n"],
                    "key_sha256": hashlib.sha256(alert["key"].encode("utf-8")).hexdigest(),
                }
                log = Path(log_path)
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(str(log), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
            except Exception:
                pass
        return _advisory(alert)
    except Exception:
        return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    try:
        if os.environ.get("BD_LOOP_DETECTOR") == "0":
            return 0
        parser = argparse.ArgumentParser(
            prog="loop_detector",
            description="Advisory tool-call loop detector (reads a hook payload on stdin).",
        )
        parser.add_argument("--state-dir", required=True, help="directory for per-session state files")
        parser.add_argument("--log", default=None, help="optional JSONL journal path")
        args = parser.parse_args(argv)
        try:
            payload = json.loads(sys.stdin.read())
        except ValueError:
            return 0
        text = process_event(payload, args.state_dir, args.log, thresholds_from_env(os.environ))
        if text:
            sys.stdout.write(text + "\n")
    except SystemExit:
        return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
