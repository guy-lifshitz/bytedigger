"""RED tests for bd#136 -- write guard follow-ups (Bash writes, stale main-checkout
state, atomic state rewrites).

Spec: docs/decisions/2026-10-02-bd136-write-guard-followups.md (FROZEN Rev 1), A1-A9.
The real hook hooks/worker-write-guard.sh is run as a subprocess with JSON on stdin
inside tmp_path project dirs. Nothing is imported from the repo; no sys.path changes.

Doc phrases GREEN must put in docs/security.md, section "Subagent write guard"
(case-insensitive, whitespace-normalised, so line wrapping is free):
  A8 removed : "the hook sees file tools only"  and  "stale state after a worktree build"
  A8 present : "name match"          (Bash check is a name match ...)
               "not a sandbox"       (... not a sandbox)
               "computed name"       (indirection such as computed names is not caught)
               "script file"         (... or a script file that writes the state)
               "pretooluse plugin hooks"  (runs only on hosts that fire PreToolUse plugin hooks)
               "hook-less backend"   (non-hook backend gets no guard)
               "build-gate.sh"       (... and keeps the gate checks of build-gate.sh only)

Phase-prompt pins (A7) are on behaviour-bearing shapes: `os.replace` inside every
`current_phase →` one-liner, no `write_text(` / `open('build-state.yaml','w')` on the
state file in phases/*.md, `mv` (not `cp`) for both state files in the worktree step.
No timing or singleton-resource fixtures: all state is pre-staged in tmp_path (workflows.md 1i).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "worker-write-guard.sh"
PHASES = REPO_ROOT / "phases"
PFX = "BLOCKED (bytedigger write guard): "
R3 = PFX + "unreadable tool input during an active build"
WARN = "WARN (bytedigger write guard): python3 not found; write guard disabled"
AGENT_TYPES = ["bytedigger:tdd-worker", "tdd-worker", "general-purpose"]


# ---------------------------------------------------------------------------
# helpers (copied small from tests/test_worker_write_guard.py)
# ---------------------------------------------------------------------------

def _project(tmp_path: Path, phase="4", raw_state=None, with_state=True) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    if not with_state:
        return proj
    if raw_state is not None:
        (proj / "build-state.yaml").write_text(raw_state)
    else:
        (proj / "build-state.yaml").write_text(f'task: "t"\ncurrent_phase: "{phase}"\n')
    return proj


def _run(proj: Path, payload, *, raw_stdin=None, direct=False):
    if raw_stdin is None:
        if isinstance(payload, dict) and "cwd" not in payload:
            payload = {**payload, "cwd": str(proj)}
        raw_stdin = json.dumps(payload)
    argv = [str(HOOK)] if direct else ["bash", str(HOOK)]
    return subprocess.run(argv, input=raw_stdin, capture_output=True, text=True,
                          cwd=str(proj), timeout=60)


def _bash(cmd, agent_type="general-purpose", agent_id="a1"):
    d = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    if agent_id is not None:
        d["agent_id"] = agent_id
    if agent_type is not None:
        d["agent_type"] = agent_type
    return d


def _write(path, agent_type="general-purpose", agent_id="a1"):
    d = {"tool_name": "Write", "tool_input": {"file_path": str(path)}}
    if agent_id is not None:
        d["agent_id"] = agent_id
    if agent_type is not None:
        d["agent_type"] = agent_type
    return d


def _allow(proc):
    assert proc.returncode == 0, f"rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}"
    assert "BLOCKED" not in proc.stderr and "BLOCKED" not in proc.stdout


def _block(proc, line: str):
    assert proc.returncode == 2, f"rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}"
    assert proc.stderr.strip() == line
    assert line in proc.stdout.splitlines() or proc.stdout.strip() == line


def _b4(name):
    return f"{PFX}subagents may not touch {name} from Bash; it is orchestrator state"


def _r5(name):
    return f"{PFX}subagents may not write {name}; it is orchestrator state"


# ---------------------------------------------------------------------------
# A1 -- rows B1-B5
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_type", [None, "general-purpose", "bytedigger:tdd-worker"])
def test_a1_b1_main_thread_bash_allowed_even_on_state_file(tmp_path, agent_type):
    proj = _project(tmp_path)
    _allow(_run(proj, _bash("sed -i s/a/b/ build-state.yaml", agent_type=agent_type, agent_id=None)))
    _allow(_run(proj, _bash("sed -i s/a/b/ build-state.yaml", agent_type=agent_type, agent_id="")))


@pytest.mark.parametrize("cmd", [None, "", 5, ["ls"], {"a": 1}])
def test_a1_b2_bad_command_blocks_with_r3(tmp_path, cmd):
    proj = _project(tmp_path)
    p = {"tool_name": "Bash", "tool_input": {"command": cmd}, "agent_id": "a1",
         "agent_type": "general-purpose"}
    _block(_run(proj, p), R3)


@pytest.mark.parametrize("ti", ["x", [], None, 5])
def test_a1_b2_non_object_tool_input_blocks_with_r3(tmp_path, ti):
    proj = _project(tmp_path)
    p = {"tool_name": "Bash", "tool_input": ti, "agent_id": "a1", "agent_type": "general-purpose"}
    _block(_run(proj, p), R3)


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
def test_a1_b3_tee_exemption_allowed(tmp_path, agent_type):
    proj = _project(tmp_path)
    _allow(_run(proj, _bash("pytest -q 2>&1 | tee build-red-output.log", agent_type=agent_type)))


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
def test_a1_b4_reference_blocked_exact_message(tmp_path, agent_type):
    proj = _project(tmp_path)
    _block(_run(proj, _bash("sed -i 's/x/y/' build-state.yaml", agent_type=agent_type)),
           _b4("build-state.yaml"))


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
def test_a1_b5_no_reference_allowed(tmp_path, agent_type):
    proj = _project(tmp_path)
    _allow(_run(proj, _bash("ls -la src && git status", agent_type=agent_type)))


# ---------------------------------------------------------------------------
# A2 -- forging forms, all blocked
# ---------------------------------------------------------------------------

def _forms(proj):
    return [
        ("sed -i 's/review_complete:.*/review_complete: pass/' build-state.yaml", "build-state.yaml"),
        ("echo x > build-state.yaml", "build-state.yaml"),
        ("echo x >> build-state.yaml", "build-state.yaml"),
        ("printf 'a: b' | tee build-state.yaml", "build-state.yaml"),
        ("cp x build-metadata.json", "build-metadata.json"),
        ("mv x build-state.yaml", "build-state.yaml"),
        ("python3 -c \"open('build-state.yaml','w')\"", "build-state.yaml"),
        ("cat build-state.yaml", "build-state.yaml"),
        ("cat ./build-state.yaml", "build-state.yaml"),
        (f"cat {proj}/build-state.yaml", "build-state.yaml"),
        ("cat BUILD-STATE.YAML", "build-state.yaml"),
        ("cat build-*.yaml", "build-state.yaml"),
        ("cat build-stat?.yaml", "build-state.yaml"),
        ("cat *.yaml", "build-state.yaml"),
        ("cat build-*.json", "build-metadata.json"),
        ("touch .bytedigger-orchestrator-pid", ".bytedigger-orchestrator-pid"),
        ("rm -f .bytedigger-orchestrator-pid", ".bytedigger-orchestrator-pid"),
        ("cat 'build-state.yaml'", "build-state.yaml"),
        ("cat \"build-\"'state.yaml'", "build-state.yaml"),
        ("x=build-state.yaml; cat $x", "build-state.yaml"),
        ("echo FAIL > build-green-output.log", "build-green-output.log"),
    ]


_FORM_IDX = list(range(len(_forms(Path("/p")))))


@pytest.mark.parametrize("idx", _FORM_IDX)
def test_a2_forging_form_blocked(tmp_path, idx):
    proj = _project(tmp_path)
    cmd, name = _forms(proj)[idx]
    _block(_run(proj, _bash(cmd)), _b4(name))


@pytest.mark.parametrize("idx", _FORM_IDX)
def test_a2_forging_form_blocked_for_prefixed_agent(tmp_path, idx):
    proj = _project(tmp_path)
    cmd, name = _forms(proj)[idx]
    _block(_run(proj, _bash(cmd, agent_type="bytedigger:tdd-worker")), _b4(name))


# ---------------------------------------------------------------------------
# A3 -- tee exemption
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "pytest -q 2>&1 | tee build-red-output.log",
    "pytest -q 2>&1 | tee build-green-output.log",
    "pytest -q 2>&1 | tee -a build-red-output.log",
    "pytest -q 2>&1 | tee -a build-green-output.log",
    "pytest -q 2>&1 | tee ./build-red-output.log",
    "pytest -q 2>&1 | tee -a ./build-green-output.log",
    "pytest -q 2>&1 | tee build-red-output.log  ",
])
def test_a3_tee_exemption_allowed(tmp_path, cmd):
    proj = _project(tmp_path)
    _allow(_run(proj, _bash(cmd)))


@pytest.mark.parametrize("cmd,name", [
    ("echo FAIL > build-red-output.log", "build-red-output.log"),
    ("pytest | tee build-red-output.log; sed -i x build-state.yaml",
     ("build-state.yaml", "build-red-output.log")),
    ("pytest | tee build-red-output.log && echo build-metadata.json",
     ("build-metadata.json", "build-red-output.log")),
    ("tee build-green-output.log < /dev/null", "build-green-output.log"),
    ("pytest | tee build-red-output.log | cat", "build-red-output.log"),
    ("pytest | tee build-red-output.log && echo done", "build-red-output.log"),
    ("pytest | tee build-red-output.log | tee build-state.yaml",
     ("build-state.yaml", "build-red-output.log")),
])
def test_a3_tee_exemption_not_applicable_blocked(tmp_path, cmd, name):
    proj = _project(tmp_path)
    proc = _run(proj, _bash(cmd))
    names = (name,) if isinstance(name, str) else name
    # several protected names may be referenced; which one is reported is not pinned
    assert proc.returncode == 2, f"rc={proc.returncode} err={proc.stderr!r}"
    assert proc.stderr.strip() in {_b4(n) for n in names}
    assert proc.stderr.strip() in proc.stdout.splitlines() or proc.stdout.strip() == proc.stderr.strip()


# ---------------------------------------------------------------------------
# A4 -- unrelated commands, no build, main thread
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["pytest", "cat README.md", "grep state src/",
                                 "python3 -m pytest tests/ -q", "git diff --stat"])
def test_a4_unrelated_command_allowed(tmp_path, cmd):
    proj = _project(tmp_path)
    _allow(_run(proj, _bash(cmd)))


@pytest.mark.parametrize("cmd", ["sed -i x build-state.yaml", "echo x > build-metadata.json",
                                 "touch .bytedigger-orchestrator-pid", "pytest"])
def test_a4_no_build_every_command_allowed(tmp_path, cmd):
    proj = _project(tmp_path, with_state=False)
    _allow(_run(proj, _bash(cmd)))


@pytest.mark.parametrize("cmd", ["sed -i x build-state.yaml", "echo x > build-metadata.json",
                                 "touch .bytedigger-orchestrator-pid", "pytest"])
def test_a4_completed_build_every_command_allowed(tmp_path, cmd):
    proj = _project(tmp_path, phase="completed")
    _allow(_run(proj, _bash(cmd)))


@pytest.mark.parametrize("cmd", ["sed -i x build-state.yaml", "echo x > build-metadata.json",
                                 "cat build-*.yaml"])
def test_a4_main_thread_every_command_allowed(tmp_path, cmd):
    proj = _project(tmp_path)
    _allow(_run(proj, _bash(cmd, agent_type=None, agent_id=None)))


# ---------------------------------------------------------------------------
# A5 -- B0: empty / whitespace-only state file is an active build
# ---------------------------------------------------------------------------

B0_STATES = ["", "   \n\t\n  ", "\n"]


@pytest.mark.parametrize("raw", B0_STATES)
def test_a5_b0_subagent_write_to_state_blocked(tmp_path, raw):
    proj = _project(tmp_path, raw_state=raw)
    _block(_run(proj, _write(proj / "build-state.yaml")), _r5("build-state.yaml"))


@pytest.mark.parametrize("raw", B0_STATES)
def test_a5_b0_subagent_bash_sed_on_state_blocked(tmp_path, raw):
    proj = _project(tmp_path, raw_state=raw)
    _block(_run(proj, _bash("sed -i 's/a/b/' build-state.yaml")), _b4("build-state.yaml"))


@pytest.mark.parametrize("raw", B0_STATES)
def test_a5_b0_r3_applies_fail_closed(tmp_path, raw):
    proj = _project(tmp_path, raw_state=raw)
    _block(_run(proj, None, raw_stdin="not json {{"), R3)


@pytest.mark.parametrize("raw", B0_STATES)
def test_a5_b0_main_thread_and_unrelated_still_allowed(tmp_path, raw):
    proj = _project(tmp_path, raw_state=raw)
    _allow(_run(proj, _write(proj / "build-state.yaml", agent_type=None, agent_id=None)))
    _allow(_run(proj, _bash("pytest -q")))
    _allow(_run(proj, _write(proj / "src" / "a.py")))


def test_a5_text_without_current_phase_stays_no_build(tmp_path):
    # bd#133 R2 unchanged: file has text but no current_phase -> no build -> allow
    proj = _project(tmp_path, raw_state='task: "t"\nmode: AUTONOMOUS\n')
    _allow(_run(proj, _write(proj / "build-state.yaml")))
    _allow(_run(proj, _bash("sed -i x build-state.yaml")))


# ---------------------------------------------------------------------------
# A6 -- hooks.json
# ---------------------------------------------------------------------------

def _bash_hook_commands():
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())
    cmds = []
    for entry in data["hooks"]["PreToolUse"]:
        matcher = entry.get("matcher", "")
        if re.fullmatch(matcher, "Bash"):
            cmds += [h.get("command", "") for h in entry.get("hooks", [])]
    return cmds


def test_a6_hooks_json_registers_worker_write_guard_for_bash():
    cmds = _bash_hook_commands()
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/worker-write-guard.sh" in cmds


def test_a6_build_state_guard_stays_registered_for_bash():
    cmds = _bash_hook_commands()
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/build-state-guard.sh" in cmds


# ---------------------------------------------------------------------------
# A7 -- phase prompt pins
# ---------------------------------------------------------------------------

ONE_LINER_FILES = ["phase-45-spec.md", "phase-5-implement.md", "phase-6-review.md",
                   "phase-7-synthesize.md"]


@pytest.mark.parametrize("fname", ONE_LINER_FILES)
def test_a7_current_phase_one_liner_uses_os_replace(tmp_path, fname):
    lines = [l for l in (PHASES / fname).read_text(encoding="utf-8").splitlines()
             if "current_phase →" in l]
    assert lines, f"{fname} has no `current_phase →` one-liner"
    for l in lines:
        assert "os.replace" in l, f"{fname}: state rewrite is not atomic: {l[:120]}"


def test_a7_no_truncating_state_writes_in_phase_prompts():
    open_w = re.compile(r"""open\(\s*['"]build-state\.yaml['"]\s*,\s*['"]w""")
    bad = []
    for f in sorted(PHASES.glob("*.md")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if open_w.search(line):
                bad.append(f"{f.name}:{n}: open(build-state.yaml,'w')")
            if "write_text(" in line and "build-state.yaml" in line:
                bad.append(f"{f.name}:{n}: write_text( on build-state.yaml")
    assert not bad, "non-atomic state writes remain:\n" + "\n".join(bad)


def _worktree_section():
    text = (PHASES / "phase-0-classify.md").read_text(encoding="utf-8")
    m = re.search(r"^## --worktree Isolation\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, "phase-0-classify.md has no '--worktree Isolation' section"
    return m.group(1)


def test_a7_worktree_step_moves_both_state_files():
    sec = _worktree_section()
    assert re.search(r"\bmv\s+build-state\.yaml\b", sec), "worktree step must mv build-state.yaml"
    assert re.search(r"\bmv\s+build-metadata\.json\b", sec), "worktree step must mv build-metadata.json"


def test_a7_worktree_step_no_cp_of_state_files():
    sec = _worktree_section()
    assert not re.search(r"\bcp\s+(-\S+\s+)*build-state\.yaml\b", sec)
    assert not re.search(r"\bcp\s+(-\S+\s+)*build-metadata\.json\b", sec)


# ---------------------------------------------------------------------------
# A8 -- docs/security.md
# ---------------------------------------------------------------------------

def _guard_section():
    text = (REPO_ROOT / "docs" / "security.md").read_text(encoding="utf-8")
    m = re.search(r"^## Subagent write guard\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, "docs/security.md has no '## Subagent write guard' section"
    return " ".join(m.group(1).split()).casefold()


def test_a8_bash_writes_limit_removed():
    sec = _guard_section()
    assert "the hook sees file tools only" not in sec
    assert "can still write with" not in sec


def test_a8_stale_worktree_state_limit_removed():
    assert "stale state after a worktree build" not in _guard_section()


@pytest.mark.parametrize("phrase", ["name match", "not a sandbox", "computed name", "script file"])
def test_a8_name_match_not_a_sandbox_limit_stated(phrase):
    assert phrase in _guard_section()


@pytest.mark.parametrize("phrase", ["pretooluse plugin hooks", "hook-less backend", "build-gate.sh"])
def test_a8_hookless_backend_limit_stated(phrase):
    assert phrase in _guard_section()


# ---------------------------------------------------------------------------
# A9 -- real .sh executed directly; no python3 path
# ---------------------------------------------------------------------------

def test_a9_direct_exec_blocks_bash_state_write(tmp_path):
    proj = _project(tmp_path)
    _block(_run(proj, _bash("echo x >> build-state.yaml"), direct=True), _b4("build-state.yaml"))


def test_a9_direct_exec_allows_tee_exemption(tmp_path):
    proj = _project(tmp_path)
    _allow(_run(proj, _bash("pytest 2>&1 | tee build-red-output.log"), direct=True))


def test_a9_no_python3_bash_input_allows_with_single_warn(tmp_path):
    proj = _project(tmp_path)
    bindir = tmp_path / "nopy-bin"
    bindir.mkdir()
    for t in ["bash", "cat", "grep", "sed", "tr", "head", "dirname", "basename", "cut", "awk",
              "tail", "wc", "sort", "uname", "env", "printf", "realpath", "readlink", "mkdir",
              "sh", "expr", "test", "echo", "ls", "rm", "mktemp", "tee", "xargs", "sleep", "jq"]:
        src = shutil.which(t)
        if src and not (bindir / t).exists():
            (bindir / t).symlink_to(src)
    assert not (bindir / "python3").exists() and not (bindir / "python").exists()
    payload = {**_bash("sed -i x build-state.yaml"), "cwd": str(proj)}
    proc = subprocess.run([str(bindir / "bash"), str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=str(proj),
                          env={"PATH": str(bindir), "HOME": str(tmp_path)}, timeout=60)
    assert proc.returncode == 0, f"rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}"
    assert proc.stderr.strip() == WARN
    assert len(proc.stderr.strip().splitlines()) == 1
