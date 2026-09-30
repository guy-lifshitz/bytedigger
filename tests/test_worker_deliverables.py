"""RED tests for bd#127 -- workers write their own deliverables.

Spec: SHARED/memory/Decisions/2026-09-30_bd127_worker_deliverables_spec.md
Covers AC1-AC3 (agent frontmatter + contract text), AC4/AC5 (bash gate only;
the TS twin is covered in scripts/ts/__tests__/build-phase-gate.test.ts) and
AC6 (parse-error reporting in BOTH learning-store backends).

Nothing is imported from the repo at module level; scripts are driven through
subprocess inside tmp_path fixtures. No sys.path manipulation.

Tests marked "GUARD" pass on current main and protect post-GREEN behavior.
All other tests are expected RED today.
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
GATE = REPO_ROOT / "scripts" / "build-gate.sh"
STORE = REPO_ROOT / "scripts" / "learning-store.sh"
SCHEMA = REPO_ROOT / "tests" / "fixtures" / "learning-schema.sql"

OLD_TOOLS = {
    "Glob", "Grep", "LS", "Read", "NotebookRead", "WebFetch",
    "TodoWrite", "WebSearch", "KillShell", "BashOutput",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _tools(rel: str) -> set[str]:
    text = _read(rel)
    m = re.search(r"^---\n(.*?)\n---", text, re.S)
    assert m, f"{rel}: no frontmatter"
    for line in m.group(1).splitlines():
        if line.startswith("tools:"):
            return {t.strip() for t in line[len("tools:"):].split(",") if t.strip()}
    raise AssertionError(f"{rel}: no tools: line in frontmatter")


def _deliverable_section(rel: str) -> str:
    text = _read(rel)
    m = re.search(r"^## Deliverable[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"{rel}: no '## Deliverable' section"
    return m.group(1)


def _clean_env(home: Path, **extra: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# AC1 -- tools: old set + Write, nothing else
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent", ["explorer", "architect", "synthesizer"])
def test_ac1_tools_are_old_set_plus_write(agent):
    assert _tools(f"agents/{agent}.md") == OLD_TOOLS | {"Write"}


# ---------------------------------------------------------------------------
# AC2 -- agent body has a ## Deliverable section
# ---------------------------------------------------------------------------

AGENT_PATHS = {
    "explorer": "{scratchpad_dir}/research/findings-{your-name}.md",
    "architect": "{scratchpad_dir}/architecture/approach-{your-name}.md",
    "synthesizer": "{scratchpad_dir}/reviews/learnings-raw.md",
}


@pytest.mark.parametrize("agent", ["explorer", "architect", "synthesizer"])
def test_ac2_agent_deliverable_section_names_single_path(agent):
    sec = _deliverable_section(f"agents/{agent}.md")
    assert AGENT_PATHS[agent] in sec


@pytest.mark.parametrize("agent", ["explorer", "architect", "synthesizer"])
def test_ac2_agent_deliverable_write_is_for_that_one_path_only(agent):
    sec = _deliverable_section(f"agents/{agent}.md")
    assert re.search(r"\bWrite\b", sec)
    assert re.search(r"\b(only|never|solely|no other)\b", sec, re.I)
    assert re.search(r"\b(source|test|other)\b", sec, re.I)


@pytest.mark.parametrize("agent", ["explorer", "architect", "synthesizer"])
def test_ac2_agent_deliverable_final_reply_is_summary_plus_path(agent):
    sec = _deliverable_section(f"agents/{agent}.md")
    assert re.search(r"summary", sec, re.I)
    assert re.search(r"\bpath\b", sec, re.I)
    assert re.search(r"\b(not|never|instead|rather than)\b", sec, re.I)


def test_ac2_architect_deliverable_covers_security_architect_prompt_path():
    sec = _deliverable_section("agents/architect.md")
    assert re.search(r"security", sec, re.I)
    assert re.search(r"(given|provided|specified|named)[^.\n]{0,30}prompt", sec, re.I)


def test_ac2_synthesizer_uses_scratchpad_dir_placeholder():
    text = _read("agents/synthesizer.md")
    assert "{scratchpad_dir}/reviews/learnings-raw.md" in text
    assert "{scratchpad}" not in text


# ---------------------------------------------------------------------------
# AC3 -- phase prompts
# ---------------------------------------------------------------------------

_NON_EMPTY = r"non-?empty|not empty|zero-byte|size\s*>\s*0"
_NOT_ON_BEHALF = (
    r"(?:not|never|n't)[^.\n]{0,80}behalf"
    r"|behalf[^.\n]{0,40}(?:not|never)"
    r"|(?:not|never)[^.\n]{0,60}\bwrit\w*[^.\n]{0,40}\bfor (?:the|that) "
    r"(?:agent|architect|explorer|synthesizer)"
)
_REPROMPT = r"re-?prompt|re-?spawn|spawn (?:a )?(?:fresh|new)|send ?message"


def _assert_orchestrator_verifies(text: str):
    assert re.search(_NON_EMPTY, text, re.I), "no non-empty-on-disk verification"
    assert re.search(_REPROMPT, text, re.I), "no re-prompt/respawn on a miss"
    assert re.search(_NOT_ON_BEHALF, text, re.I), "no 'orchestrator does not write it for the agent'"


def _slice(text: str, start_re: str, end_re: str) -> str:
    m = re.search(start_re + r"(.*?)(?=" + end_re + r"|\Z)", text, re.S | re.M)
    assert m, f"section not found: {start_re}"
    return m.group(1)


def test_ac3_phase2_step3_contract():
    text = _read("phases/phase-2-explore.md")
    step3 = _slice(text, r"^3\. ", r"^4\. ")
    assert "{scratchpad_dir}/research/findings-{agent-name}.md" in step3 or \
        "{scratchpad_dir}/research/findings-{your-name}.md" in step3
    assert re.search(r"itself", step3, re.I)
    assert re.search(r"summary", step3, re.I) and re.search(r"\bpath\b", step3, re.I)
    _assert_orchestrator_verifies(text)


def test_ac3_phase4_reanchor_contract():
    text = _read("phases/phase-4-architect.md")
    block = _slice(text, r"^## Re-Anchoring", r"^## Actions")
    assert "{scratchpad_dir}/architecture/approach-{your-name}.md" in block
    assert re.search(r"itself", block, re.I)
    assert re.search(r"summary", block, re.I) and re.search(r"\bpath\b", block, re.I)
    _assert_orchestrator_verifies(text)


def test_ac3_phase7_step1_contract():
    text = _read("phases/phase-7-synthesize.md")
    step1 = _slice(text, r"^1\. Launch", r"^\*\*Orchestrator flow")
    assert "reviews/learnings-raw.md" in step1
    assert re.search(r"itself", step1, re.I)
    assert re.search(r"summary", step1, re.I) and re.search(r"\bpath\b", step1, re.I)
    _assert_orchestrator_verifies(text)


def test_ac3_phase7_documents_line_format():
    text = _read("phases/phase-7-synthesize.md")
    assert re.search(r"-\s*\[category\]\s*---\s*\[?lesson\]?", text)


# ---------------------------------------------------------------------------
# Gate fixtures (AC4 / AC5) -- bash build-gate.sh only
# ---------------------------------------------------------------------------

def _gate_fixture(tmp_path: Path, phase: str, extra_state: str, complexity="FEATURE"):
    scratch = tmp_path / "scratch"
    for d in ("research", "architecture", "reviews"):
        (scratch / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "bytedigger.json").write_text(
        json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    state = (
        'task: "t"\n'
        f"complexity: {complexity}\n"
        "mode: AUTONOMOUS\n"
        f'current_phase: "{phase}"\n'
        'last_updated: "2026-09-30T00:00:00Z"\n'
        f'scratchpad_dir: "{scratch}"\n'
        f"{extra_state}"
    )
    (tmp_path / "build-state.yaml").write_text(state)
    return scratch


def _run_gate(tmp_path: Path):
    env = _clean_env(tmp_path, BYTEDIGGER_CONFIG=str(tmp_path / "bytedigger.json"))
    return subprocess.run(
        ["bash", str(GATE)], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, env=env, cwd=str(tmp_path), timeout=60,
    )


def _reason(proc) -> str:
    return json.loads(proc.stdout.strip().splitlines()[-1])["reason"]


# ---------------------------------------------------------------------------
# AC4 -- gate 4
# ---------------------------------------------------------------------------

def test_ac4_zero_byte_findings_only_hard_blocks_stale(tmp_path):
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "no non-empty findings-*.md" in _reason(proc)
    assert re.search(r"^scratchpad_stale: true$",
                     (tmp_path / "build-state.yaml").read_text(), re.M)


def test_ac4_missing_approach_soft_blocks(tmp_path):
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    reason = _reason(proc)
    assert "missing deliverable:" in reason
    assert "architecture/approach-*.md" in reason


def test_ac4_zero_byte_approach_soft_blocks(tmp_path):
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    (scratch / "architecture" / "approach-a.md").write_text("")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    reason = _reason(proc)
    assert "missing deliverable:" in reason
    assert "architecture/approach-*.md" in reason


def test_ac4_findings_and_approach_non_empty_passes(tmp_path):
    # Also satisfied by the post-GREEN gate: one non-empty approach suffices.
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    (scratch / "architecture" / "approach-a.md").write_text("approach\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ac4_guard_findings_and_approach_pass(tmp_path):
    # GUARD: passes on main and must keep passing.
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    (scratch / "architecture" / "approach-a.md").write_text("approach\n")
    (scratch / "architecture" / "approach-b.md").write_text("")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# AC5 -- gate 7
# ---------------------------------------------------------------------------

def test_ac5_missing_learnings_raw_soft_blocks(tmp_path):
    _gate_fixture(tmp_path, "7", "review_complete: pass\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    reason = _reason(proc)
    assert "missing deliverable:" in reason
    assert "reviews/learnings-raw.md" in reason


def test_ac5_zero_byte_learnings_raw_soft_blocks(tmp_path):
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\n")
    (scratch / "reviews" / "learnings-raw.md").write_text("")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    reason = _reason(proc)
    assert "missing deliverable:" in reason
    assert "reviews/learnings-raw.md" in reason


def test_ac5_header_only_learnings_raw_passes(tmp_path):
    # AC5c: RED on main (gate_phase_7 dies on missing learning_backend under pipefail).
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\n")
    (scratch / "reviews" / "learnings-raw.md").write_text("## New Learnings\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ac5_trivial_missing_file_not_checked(tmp_path):
    # AC5c: RED on main (same crash); TRIVIAL must never gain a learnings-raw check.
    _gate_fixture(tmp_path, "7", "review_complete: pass\n", complexity="TRIVIAL")
    proc = _run_gate(tmp_path)
    assert "learnings-raw" not in proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# AC6 -- extract reports parse errors (both backends)
# ---------------------------------------------------------------------------

T12_BODY = (
    "## New Learnings\n"
    "\n"
    "- [architecture] --- Good entry that should be stored\n"
    "- This line has no category prefix and should be skipped\n"
    "- [bug-fix] --- Another valid entry\n"
    "- [MALFORMED without closing bracket --- should be skipped\n"
    "- just plain text, no structure\n"
)
VALID_BODY = (
    "## New Learnings\n"
    "\n"
    "- [architecture] --- Good entry that should be stored\n"
    "- [bug-fix] --- Another valid entry\n"
)
PROSE_BODY = (
    "## New Learnings\n"
    "\n"
    "1. [workflow] — x\n"
    "**workflow**: y\n"
)

BACKENDS = ["file", "sqlite"]


def _extract(tmp_path: Path, backend: str, body: str):
    if backend == "sqlite" and shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 binary not available")
    cfg = tmp_path / "bytedigger.json"
    cfg.write_text(json.dumps({"learning": {
        "backend": backend, "max_inject": 10, "max_stored": 200,
        "storage_path": ".bytedigger/learnings"}}))
    scratch = tmp_path / "scratch"
    (scratch / "reviews").mkdir(parents=True)
    (scratch / "reviews" / "learnings-raw.md").write_text(body, encoding="utf-8")
    env = _clean_env(tmp_path, BYTEDIGGER_CONFIG=str(cfg))
    if backend == "sqlite":
        db = tmp_path / "learnings.db"
        subprocess.run(["sqlite3", str(db)], input=SCHEMA.read_text(),
                       text=True, check=True, env=env, timeout=60)
        env["LEARNING_DB_URL"] = str(db)
    proc = subprocess.run(
        ["bash", str(STORE), "extract", str(scratch), "--config", str(cfg)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
        cwd=str(tmp_path), timeout=60,
    )
    state = (tmp_path / "build-state.yaml").read_text()
    return proc, state


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_guard_t12_extracted_count_and_exit_unchanged(tmp_path, backend):
    # GUARD: passes on main (2 extracted, exit 0).
    proc, state = _extract(tmp_path, backend, T12_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_t12_reports_three_parse_errors(tmp_path, backend):
    proc, state = _extract(tmp_path, backend, T12_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 3$", state, re.M)
    warn = [l for l in proc.stderr.splitlines() if "WARN" in l]
    assert warn, proc.stderr
    assert any(re.search(r"\b3\b", l) for l in warn)
    assert "- [category] --- lesson" in proc.stderr


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_all_valid_reports_zero_and_no_warn(tmp_path, backend):
    proc, state = _extract(tmp_path, backend, VALID_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_numbered_list_and_prose_count_as_parse_errors(tmp_path, backend):
    proc, state = _extract(tmp_path, backend, PROSE_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 0$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 2$", state, re.M)
    assert "WARN" in proc.stderr
