#!/usr/bin/env python3
"""bd#133 PreToolUse path guard for subagent Write/Edit (logic of worker-write-guard.sh).

Spec: docs/decisions/2026-09-30-bd133-worker-write-path-guard.md (decision table R1-R8).
Every value (stdin JSON, cwd, state file) is data here, never program text.
Exit codes: 0 = allow, 2 = block (one line on stderr and stdout).
"""
import json
import os
import sys

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
ROLE_DIR = {"explorer": "research", "architect": "architecture", "synthesizer": "reviews"}
PROTECTED = ("build-state.yaml", "build-metadata.json", "build-red-output.log",
             "build-green-output.log", ".bytedigger-orchestrator-pid")
PFX = "BLOCKED (bytedigger write guard): "
R3_MSG = PFX + "unreadable tool input during an active build"


def block(msg):
    # A newline inside an interpolated value must not split the one-line reason.
    msg = msg.replace("\n", "\\n").replace("\r", "\\r")
    for stream in (sys.stderr, sys.stdout):
        try:
            stream.write(msg + "\n")
            stream.flush()
        except Exception:
            pass
    sys.exit(2)


def state_value(text, key):
    """First line starting with `key:` at column 0; value unquoted; missing -> ''."""
    for line in text.split("\n"):
        if line.startswith(key + ":"):
            val = line.split(":", 1)[1].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            return val
    return ""


def role_of(agent_type):
    if not isinstance(agent_type, str):
        return ""
    if agent_type.startswith("bytedigger:"):
        return agent_type[len("bytedigger:"):]
    return agent_type


def protected_name(raw_path, target, cwd):
    """Canonical name if target is an orchestrator state file, else ''."""
    resolved = os.path.basename(target).casefold()
    if resolved in PROTECTED:
        return resolved
    if os.path.exists(target):
        for name in PROTECTED:
            ref = os.path.join(cwd, name)
            if os.path.exists(ref) and os.path.samefile(target, ref):
                return name
    raw = os.path.basename(raw_path).casefold()
    return raw if raw in PROTECTED else ""


def check(data, cwd, scratch_value):
    """Rows R3-R8 for an active build. Returns normally to allow, else blocks."""
    tool = data.get("tool_name")
    if not isinstance(tool, str) or not tool:
        block(R3_MSG)
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        block(R3_MSG)
    path = tool_input.get("notebook_path" if tool == "NotebookEdit" else "file_path")
    if not isinstance(path, str) or not path:
        block(R3_MSG)

    agent_id = data.get("agent_id")
    if not (isinstance(agent_id, str) and agent_id):
        return  # R4: main thread

    target = os.path.realpath(os.path.join(cwd, path))
    name = protected_name(path, target, cwd)
    if name:
        block(PFX + "subagents may not write " + name + "; it is orchestrator state")

    role = role_of(data.get("agent_type"))
    sub = ROLE_DIR.get(role)
    if sub is None:
        return  # R8
    if not scratch_value:
        block(PFX + role + " may write only its scratchpad deliverable, "
              "but build-state.yaml has no scratchpad_dir")
    scratch = os.path.realpath(os.path.join(cwd, scratch_value))
    base = scratch.rstrip("/") + "/"
    allowed = os.path.realpath(os.path.join(scratch, sub))
    if allowed.startswith(base) and target.startswith(allowed + "/"):
        return
    block(PFX + role + " may write only under " + scratch + "/" + sub
          + "/, not " + target)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            pass
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = None

    # R1: a named tool outside the write set is none of our business.
    if isinstance(data, dict):
        tool = data.get("tool_name")
        if isinstance(tool, str) and tool and tool not in WRITE_TOOLS:
            return 0

    cwd = data.get("cwd") if isinstance(data, dict) else None
    if not (isinstance(cwd, str) and cwd):
        cwd = os.getcwd()

    # R2: build active <=> state file exists with a non-empty, non-completed phase.
    try:
        with open(os.path.join(cwd, "build-state.yaml"), encoding="utf-8",
                  errors="replace") as fh:
            text = fh.read()
    except Exception:
        return 0
    phase = state_value(text, "current_phase")
    if not phase or phase == "completed":
        return 0

    # From here a build is active: any unexpected error fails closed (R3).
    try:
        if not isinstance(data, dict):
            block(R3_MSG)
        check(data, cwd, state_value(text, "scratchpad_dir"))
    except SystemExit:
        raise
    except Exception:
        block(R3_MSG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
