"""RED tests for bd#133 -- PreToolUse path guard for subagent Write/Edit.

Spec: docs/decisions/2026-09-30-bd133-worker-write-path-guard.md (FROZEN Rev 1).
Covers A1-A8 and decision rows R1-R8. The hook hooks/worker-write-guard.sh is
driven through `bash <hook>` with JSON on stdin inside tmp_path project dirs.

Nothing is imported from the repo; no sys.path manipulation. All tests are RED
today (the hook file does not exist); none are skipped or xfail.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "worker-write-guard.sh"
PFX = "BLOCKED (bytedigger write guard): "
WARN = "WARN (bytedigger write guard): python3 not found; write guard disabled"
RO_ROLES = ["explorer", "architect", "synthesizer"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _rp(p) -> str:
    return os.path.realpath(str(p))


def _project(tmp_path: Path, phase="4", scratch_value="default", raw_state=None) -> Path:
    """Project dir with build-state.yaml; scratchpad at <proj>/scratch/{research,architecture,reviews}."""
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    scratch = proj / "scratch"
    for d in ("research", "architecture", "reviews"):
        (scratch / d).mkdir(parents=True, exist_ok=True)
    if raw_state is not None:
        (proj / "build-state.yaml").write_text(raw_state)
        return proj
    lines = ['task: "t"', "mode: AUTONOMOUS"]
    if phase is not None:
        lines.append(f'current_phase: "{phase}"')
    if scratch_value == "default":
        scratch_value = f'"{scratch}"'
    if scratch_value is not None:
        lines.append(f"scratchpad_dir: {scratch_value}")
    (proj / "build-state.yaml").write_text("\n".join(lines) + "\n")
    return proj


def _run(proj: Path, payload, *, cwd_in_json=True, env=None, raw_stdin=None):
    if raw_stdin is None:
        if isinstance(payload, dict) and cwd_in_json and "cwd" not in payload:
            payload = {**payload, "cwd": str(proj)}
        raw_stdin = json.dumps(payload)
    return subprocess.run(
        ["bash", str(HOOK)], input=raw_stdin, capture_output=True, text=True,
        cwd=str(proj), env=env, timeout=60,
    )


def _call(tool="Write", path=None, agent_type="general-purpose", agent_id="a1", key=None):
    key = key or ("notebook_path" if tool == "NotebookEdit" else "file_path")
    d = {"tool_name": tool, "tool_input": {key: str(path)}}
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


def _r5(name):
    return f"{PFX}subagents may not write {name}; it is orchestrator state"


def _r7(role, scratch, target):
    return (f"{PFX}{role} may write only under {_rp(scratch)}/"
            f"{{research,architecture,reviews}}/, not {_rp(target)}")


# ---------------------------------------------------------------------------
# A1 -- hooks.json
# ---------------------------------------------------------------------------

def test_a1_hooks_json_registers_worker_write_guard():
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())
    pre = data["hooks"]["PreToolUse"]
    entries = [e for e in pre if e.get("matcher") == "Write|Edit|MultiEdit|NotebookEdit"]
    assert len(entries) == 1
    assert entries[0]["hooks"] == [{
        "type": "command",
        "command": "${CLAUDE_PLUGIN_ROOT}/hooks/worker-write-guard.sh",
        "timeout": 10,
    }]


def test_a1_bash_entry_unchanged_and_hook_file_exists():
    data = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())
    bash = [e for e in data["hooks"]["PreToolUse"] if e.get("matcher") == "Bash"]
    assert len(bash) == 1
    assert bash[0]["hooks"][0]["command"] == "${CLAUDE_PLUGIN_ROOT}/hooks/build-state-guard.sh"
    assert bash[0]["hooks"][0]["timeout"] == 10
    assert HOOK.is_file(), "hooks/worker-write-guard.sh must exist"


# ---------------------------------------------------------------------------
# R1 -- tools outside the write set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", ["Read", "Bash", "Grep", "Glob"])
def test_r1_non_write_tool_allowed(tmp_path, tool):
    proj = _project(tmp_path)
    _allow(_run(proj, _call(tool, proj / "build-state.yaml")))


# ---------------------------------------------------------------------------
# R2 / A6 -- build not active
# ---------------------------------------------------------------------------

def test_r2_a6_no_state_file_allows_everything(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _allow(_run(proj, _call("Write", proj / "build-state.yaml", agent_type="explorer")))
    _allow(_run(proj, _call("Write", proj / "src" / "a.py", agent_type="explorer")))


def test_r2_a6_phase_completed_allows_state_file(tmp_path):
    proj = _project(tmp_path, phase="completed")
    _allow(_run(proj, _call("Write", proj / "build-state.yaml")))
    _allow(_run(proj, _call("Write", proj / "build-metadata.json")))
    _allow(_run(proj, _call("Write", proj / "src" / "a.py", agent_type="architect")))


def test_r2_empty_current_phase_is_not_active(tmp_path):
    proj = _project(tmp_path, phase=None)
    _allow(_run(proj, _call("Write", proj / "build-state.yaml")))


def test_r2_no_state_and_garbage_stdin_allows(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _allow(_run(proj, None, raw_stdin="not json {{"))


# ---------------------------------------------------------------------------
# R3 -- fail closed while a build is active
# ---------------------------------------------------------------------------

R3 = f"{PFX}unreadable tool input during an active build"


def test_r3_invalid_json_blocks(tmp_path):
    proj = _project(tmp_path)
    _block(_run(proj, None, raw_stdin="this is not json"), R3)


def test_r3_empty_stdin_blocks(tmp_path):
    proj = _project(tmp_path)
    _block(_run(proj, None, raw_stdin=""), R3)


def test_r3_no_target_path_blocks(tmp_path):
    proj = _project(tmp_path)
    _block(_run(proj, {"tool_name": "Write", "tool_input": {}, "agent_id": "a1",
                       "agent_type": "general-purpose"}), R3)


def test_r3_no_target_path_blocks_even_for_main_thread(tmp_path):
    proj = _project(tmp_path)
    _block(_run(proj, {"tool_name": "Edit", "tool_input": {}}), R3)


def test_r3_notebook_edit_with_file_path_only_has_no_target(tmp_path):
    # NotebookEdit reads notebook_path; file_path must not be used as fallback.
    proj = _project(tmp_path)
    p = _call("NotebookEdit", proj / "src" / "n.ipynb", key="file_path")
    _block(_run(proj, p), R3)


# ---------------------------------------------------------------------------
# R4 -- main thread never blocked
# ---------------------------------------------------------------------------

def test_r4_main_thread_writes_state_file_allowed(tmp_path):
    proj = _project(tmp_path)
    _allow(_run(proj, _call("Write", proj / "build-state.yaml", agent_id=None, agent_type=None)))
    _allow(_run(proj, _call("Write", proj / "build-metadata.json", agent_id=None, agent_type=None)))


def test_r4_empty_agent_id_is_main_thread(tmp_path):
    proj = _project(tmp_path)
    _allow(_run(proj, _call("Write", proj / "build-state.yaml", agent_id="", agent_type="explorer")))


# ---------------------------------------------------------------------------
# R5 -- orchestrator state files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["build-state.yaml", "build-metadata.json"])
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_r5_subagent_state_file_blocked_all_tools(tmp_path, name, tool):
    proj = _project(tmp_path)
    _block(_run(proj, _call(tool, proj / name)), _r5(name))


def test_r5_blocked_for_read_only_role_too(tmp_path):
    proj = _project(tmp_path)
    # inside an allowed dir, still the orchestrator-state file name
    _block(_run(proj, _call("Write", proj / "scratch" / "research" / "build-state.yaml",
                            agent_type="explorer")), _r5("build-state.yaml"))


def test_r5_state_file_in_subdir_blocked(tmp_path):
    proj = _project(tmp_path)
    (proj / "sub" / "deep").mkdir(parents=True)
    _block(_run(proj, _call("Write", proj / "sub" / "deep" / "build-state.yaml")),
           _r5("build-state.yaml"))


def test_r5_symlink_to_state_file_blocked(tmp_path):
    proj = _project(tmp_path)
    link = proj / "notes.md"
    link.symlink_to(proj / "build-state.yaml")
    proc = _run(proj, _call("Write", link))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stderr.strip() in {_r5("notes.md"), _r5("build-state.yaml")}
    assert proc.stdout.strip() == proc.stderr.strip()


def test_r5_symlink_to_metadata_blocked(tmp_path):
    proj = _project(tmp_path)
    (proj / "build-metadata.json").write_text("{}")
    link = proj / "meta-link.txt"
    link.symlink_to(proj / "build-metadata.json")
    proc = _run(proj, _call("Edit", link))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stderr.strip() in {_r5("meta-link.txt"), _r5("build-metadata.json")}


# ---------------------------------------------------------------------------
# R6 -- read-only role, no scratchpad_dir
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scratch_value", [None, '""'])
@pytest.mark.parametrize("role", RO_ROLES)
def test_r6_read_only_role_without_scratchpad_blocked(tmp_path, role, scratch_value):
    proj = _project(tmp_path, scratch_value=scratch_value)
    _block(_run(proj, _call("Write", proj / "scratch" / "research" / "f.md", agent_type=role)),
           f"{PFX}{role} may write only its scratchpad deliverable, "
           "but build-state.yaml has no scratchpad_dir")


def test_r6_prefixed_role_is_stripped_in_message(tmp_path):
    proj = _project(tmp_path, scratch_value=None)
    _block(_run(proj, _call("Write", proj / "x.md", agent_type="bytedigger:synthesizer")),
           f"{PFX}synthesizer may write only its scratchpad deliverable, "
           "but build-state.yaml has no scratchpad_dir")


def test_r6_general_purpose_without_scratchpad_allowed(tmp_path):
    proj = _project(tmp_path, scratch_value=None)
    _allow(_run(proj, _call("Write", proj / "src" / "a.py")))


# ---------------------------------------------------------------------------
# R7 / A3 -- allowed dirs and escapes
# ---------------------------------------------------------------------------

def test_r7_source_file_blocked_for_read_only(tmp_path):
    proj = _project(tmp_path)
    t = proj / "src" / "a.py"
    _block(_run(proj, _call("Write", t, agent_type="explorer")),
           _r7("explorer", proj / "scratch", t))


def test_r7_a3_dotdot_out_of_research_blocked(tmp_path):
    proj = _project(tmp_path)
    scratch = proj / "scratch"
    t = Path(f"{scratch}/research/../../src/a.py")
    _block(_run(proj, _call("Write", t, agent_type="explorer")),
           _r7("explorer", scratch, t))


def test_r7_a3_dotdot_between_allowed_dirs_is_resolved(tmp_path):
    # research/../architecture/x.md resolves to an allowed dir -> allowed
    proj = _project(tmp_path)
    t = Path(f"{proj}/scratch/research/../architecture/approach-a.md")
    _allow(_run(proj, _call("Write", t, agent_type="architect")))


def test_r7_a3_sibling_prefix_blocked(tmp_path):
    proj = _project(tmp_path)
    scratch = proj / "scratch"
    (scratch / "research-evil").mkdir()
    t = scratch / "research-evil" / "x.md"
    _block(_run(proj, _call("Write", t, agent_type="explorer")),
           _r7("explorer", scratch, t))


def test_r7_a3_scratchpad_root_file_blocked(tmp_path):
    proj = _project(tmp_path)
    scratch = proj / "scratch"
    t = scratch / "notes.md"
    _block(_run(proj, _call("Write", t, agent_type="synthesizer")),
           _r7("synthesizer", scratch, t))


def test_r7_a3_symlinked_dir_inside_research_blocked(tmp_path):
    proj = _project(tmp_path)
    scratch = proj / "scratch"
    outside = tmp_path / "outside"
    outside.mkdir()
    (scratch / "research" / "link").symlink_to(outside)
    t = scratch / "research" / "link" / "x.md"
    _block(_run(proj, _call("Write", t, agent_type="explorer")),
           _r7("explorer", scratch, t))
    assert _rp(outside) in _rp(t)


def test_r7_a3_relative_file_path_blocked(tmp_path):
    proj = _project(tmp_path)
    scratch = proj / "scratch"
    p = _call("Write", "src/a.py", agent_type="architect")
    _block(_run(proj, p), _r7("architect", scratch, proj / "src" / "a.py"))


def test_r7_a3_relative_file_path_inside_allowed_dir_allowed(tmp_path):
    proj = _project(tmp_path)
    p = _call("Write", "scratch/research/findings-x.md", agent_type="explorer")
    _allow(_run(proj, p))


def test_r7_notebook_path_outside_blocked(tmp_path):
    proj = _project(tmp_path)
    t = proj / "src" / "n.ipynb"
    _block(_run(proj, _call("NotebookEdit", t, agent_type="explorer")),
           _r7("explorer", proj / "scratch", t))


@pytest.mark.parametrize("tool", ["Edit", "MultiEdit"])
def test_r7_edit_tools_use_file_path(tmp_path, tool):
    proj = _project(tmp_path)
    t = proj / "src" / "a.py"
    _block(_run(proj, _call(tool, t, agent_type="synthesizer")),
           _r7("synthesizer", proj / "scratch", t))


# ---------------------------------------------------------------------------
# R8 -- allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,rel", [
    ("explorer", "research/findings-x.md"),
    ("architect", "architecture/approach-a.md"),
    ("synthesizer", "reviews/learnings-raw.md"),
    ("explorer", "architecture/approach-b.md"),
    ("architect", "reviews/notes.md"),
])
def test_r8_read_only_role_scratchpad_deliverables_allowed(tmp_path, role, rel):
    proj = _project(tmp_path)
    _allow(_run(proj, _call("Write", proj / "scratch" / rel, agent_type=role)))


def test_r8_nested_path_inside_allowed_dir_allowed(tmp_path):
    proj = _project(tmp_path)
    _allow(_run(proj, _call("Write", proj / "scratch" / "research" / "sub" / "f.md",
                            agent_type="explorer")))


def test_r8_general_purpose_source_file_allowed(tmp_path):
    proj = _project(tmp_path)
    _allow(_run(proj, _call("Write", proj / "src" / "a.py")))
    _allow(_run(proj, _call("Edit", proj / "src" / "a.py")))


def test_r8_phase_7_is_active_and_synthesizer_allowed_in_reviews(tmp_path):
    proj = _project(tmp_path, phase="7")
    _allow(_run(proj, _call("Write", proj / "scratch" / "reviews" / "learnings-raw.md",
                            agent_type="synthesizer")))
    t = proj / "src" / "a.py"
    _block(_run(proj, _call("Write", t, agent_type="synthesizer")),
           _r7("synthesizer", proj / "scratch", t))
    _block(_run(proj, _call("Write", proj / "build-state.yaml")), _r5("build-state.yaml"))


def test_r8_phase_unquoted_yaml_active(tmp_path):
    scratch = tmp_path / "proj" / "scratch"
    proj = _project(tmp_path, raw_state=f'current_phase: 7\nscratchpad_dir: "{scratch}"\n')
    _block(_run(proj, _call("Write", proj / "build-state.yaml")), _r5("build-state.yaml"))


# ---------------------------------------------------------------------------
# A4 -- role parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_type,role", [
    ("bytedigger:explorer", "explorer"),
    ("explorer", "explorer"),
    ("architect", "architect"),
    ("bytedigger:architect", "architect"),
    ("synthesizer", "synthesizer"),
    ("bytedigger:synthesizer", "synthesizer"),
])
def test_a4_restricted_roles(tmp_path, agent_type, role):
    proj = _project(tmp_path)
    t = proj / "src" / "a.py"
    _block(_run(proj, _call("Write", t, agent_type=agent_type)),
           _r7(role, proj / "scratch", t))


@pytest.mark.parametrize("agent_type", ["general-purpose", "bytedigger:foo", None])
def test_a4_unrestricted_roles_only_hit_r5(tmp_path, agent_type):
    proj = _project(tmp_path)
    _allow(_run(proj, _call("Write", proj / "src" / "a.py", agent_type=agent_type)))
    _block(_run(proj, _call("Write", proj / "build-state.yaml", agent_type=agent_type)),
           _r5("build-state.yaml"))
    _block(_run(proj, _call("Write", proj / "build-metadata.json", agent_type=agent_type)),
           _r5("build-metadata.json"))


# ---------------------------------------------------------------------------
# A5 -- scratchpad_dir forms
# ---------------------------------------------------------------------------

def test_a5_spaces_in_double_quoted_scratchpad(tmp_path):
    scratch = tmp_path / "proj" / "my scratch"
    proj = _project(tmp_path, scratch_value=f'"{scratch}"')
    (scratch / "research").mkdir(parents=True)
    _allow(_run(proj, _call("Write", scratch / "research" / "findings-x.md", agent_type="explorer")))
    t = proj / "src" / "a.py"
    _block(_run(proj, _call("Write", t, agent_type="explorer")), _r7("explorer", scratch, t))


def test_a5_single_quoted_scratchpad_with_spaces(tmp_path):
    scratch = tmp_path / "proj" / "my scratch"
    proj = _project(tmp_path, scratch_value=f"'{scratch}'")
    (scratch / "reviews").mkdir(parents=True)
    _allow(_run(proj, _call("Write", scratch / "reviews" / "learnings-raw.md",
                            agent_type="synthesizer")))


def test_a5_relative_scratchpad_resolves_against_cwd(tmp_path):
    proj = _project(tmp_path, scratch_value='"scratch"')
    scratch = proj / "scratch"
    _allow(_run(proj, _call("Write", scratch / "research" / "findings-x.md", agent_type="explorer")))
    t = proj / "src" / "a.py"
    _block(_run(proj, _call("Write", t, agent_type="explorer")), _r7("explorer", scratch, t))


def test_a5_relative_scratchpad_with_space(tmp_path):
    proj = _project(tmp_path, scratch_value='"rel scratch"')
    scratch = proj / "rel scratch"
    (scratch / "architecture").mkdir(parents=True)
    _allow(_run(proj, _call("Write", scratch / "architecture" / "approach-a.md",
                            agent_type="architect")))
    t = scratch / "other.md"
    _block(_run(proj, _call("Write", t, agent_type="architect")), _r7("architect", scratch, t))


# ---------------------------------------------------------------------------
# cwd fallback
# ---------------------------------------------------------------------------

def test_cwd_absent_in_json_falls_back_to_process_cwd(tmp_path):
    proj = _project(tmp_path)
    p = _call("Write", proj / "build-state.yaml")
    assert "cwd" not in p
    _block(_run(proj, p, cwd_in_json=False), _r5("build-state.yaml"))
    t = proj / "src" / "a.py"
    _block(_run(proj, _call("Write", t, agent_type="explorer"), cwd_in_json=False),
           _r7("explorer", proj / "scratch", t))


def test_cwd_in_json_wins_over_process_cwd(tmp_path):
    proj = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    p = {**_call("Write", proj / "build-state.yaml"), "cwd": str(proj)}
    proc = subprocess.run(["bash", str(HOOK)], input=json.dumps(p), capture_output=True,
                          text=True, cwd=str(elsewhere), timeout=60)
    _block(proc, _r5("build-state.yaml"))


# ---------------------------------------------------------------------------
# A7 -- python3 absent
# ---------------------------------------------------------------------------

def test_a7_no_python3_allows_with_warn(tmp_path):
    proj = _project(tmp_path)
    bindir = tmp_path / "nopy-bin"
    bindir.mkdir()
    tools = ["bash", "cat", "grep", "sed", "tr", "head", "dirname", "basename", "cut", "awk",
             "tail", "wc", "sort", "uname", "env", "printf", "realpath", "readlink", "mkdir",
             "sh", "expr", "test", "echo", "ls", "rm", "mktemp", "tee", "xargs", "sleep", "jq"]
    for t in tools:
        src = shutil.which(t)
        if src and not (bindir / t).exists():
            (bindir / t).symlink_to(src)
    assert not (bindir / "python3").exists() and not (bindir / "python").exists()
    env = {"PATH": str(bindir), "HOME": str(tmp_path)}
    proc = subprocess.run(
        [str(bindir / "bash"), str(HOOK)],
        input=json.dumps({**_call("Write", proj / "build-state.yaml"), "cwd": str(proj)}),
        capture_output=True, text=True, cwd=str(proj), env=env, timeout=60,
    )
    assert proc.returncode == 0, f"rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}"
    assert proc.stderr.strip() == WARN


# ---------------------------------------------------------------------------
# A8 -- docs / changelog / CI
# ---------------------------------------------------------------------------

def test_a8_plugin_md_hooks_table_row():
    text = (REPO_ROOT / "docs" / "plugin.md").read_text(encoding="utf-8")
    m = re.search(r"^## Hooks\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, "docs/plugin.md has no '## Hooks' section"
    rows = [l for l in m.group(1).splitlines()
            if l.startswith("|") and "hooks/worker-write-guard.sh" in l]
    assert rows, "hooks table has no row for hooks/worker-write-guard.sh"
    assert "PreToolUse" in rows[0]


def test_a8_changelog_topmost_section_mentions_bd133():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heads = list(re.finditer(r"^## \[", text, re.M))
    assert heads, "CHANGELOG has no version sections"
    end = heads[1].start() if len(heads) > 1 else len(text)
    top = text[heads[0].start():end]
    assert re.search(r"bd#133|#133\b", top), "topmost CHANGELOG section must mention bd#133"


def test_a8_ci_manifests_job_runs_this_suite():
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"^  manifests:\n(.*?)(?=^  [A-Za-z0-9_-]+:\s*$|\Z)", text, re.S | re.M)
    assert m, "ci.yml has no manifests job"
    lines = [l for l in m.group(1).splitlines()
             if "tests/test_worker_write_guard.py" in l]
    assert lines, "manifests job does not run tests/test_worker_write_guard.py"
    assert "pytest" in m.group(1)
