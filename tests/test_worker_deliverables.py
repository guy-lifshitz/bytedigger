"""RED tests for bd#127 -- workers write their own deliverables.

Spec: SHARED/memory/Decisions/2026-09-30_bd127_worker_deliverables_spec.md
Covers AC1-AC3 (agent frontmatter + contract text), AC4/AC5 (bash gate only;
the TS twin is covered in scripts/ts/__tests__/worker-deliverables.test.ts --
local-only, CI does not run bun test, so exact reason strings are pinned HERE),
AC6 (parse-error reporting in BOTH learning-store backends) and AC7 (CI wiring).

Rev 2 (Opus gate) additions: exact reason strings, crash-safety when learning
keys / subdirs are absent (AC5c / R2), slice-scoped AC3 checks, empty-category
and whitespace/heading parse-error edges, CI wiring test. Tests marked "GUARD"
pass on main and protect post-GREEN behavior.

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


def _strip_fences(text: str) -> str:
    # M7: drop fenced code blocks so an example containing "## New Learnings"
    # cannot truncate the "## Deliverable" section.
    return re.sub(r"^```.*?^```[^\n]*\n?", "", text, flags=re.S | re.M)


def _deliverable_section(rel: str) -> str:
    text = _strip_fences(_read(rel))
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
    # M8: both words required ("no source or test files"), not just "other".
    assert re.search(r"\bsource\b", sec, re.I)
    assert re.search(r"\btest\b", sec, re.I)


@pytest.mark.parametrize("agent", ["explorer", "architect", "synthesizer"])
def test_ac2_agent_deliverable_names_build_state_yaml_as_forbidden(agent):
    # M8/R8: agents now hold Write; build-state.yaml must be named as off-limits.
    sec = _deliverable_section(f"agents/{agent}.md")
    assert "build-state.yaml" in sec
    assert re.search(r"(never|not|no)\b[^.\n]{0,120}build-state\.yaml"
                     r"|build-state\.yaml[^.\n]{0,80}(forbidden|off-limits|never|not)",
                     sec, re.I)


def test_ac2_architect_deliverable_different_path_rule():
    # M9/R8: security architect writes only the path its prompt names; never another agent's file.
    sec = _deliverable_section("agents/architect.md")
    assert re.search(r"different path", sec, re.I)
    assert re.search(r"(do not|don't|never)[^.\n]{0,60}(another|other)[^.\n]{0,30}(agent|architect)", sec, re.I)


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
# F3: only explicit re-prompt/respawn wording. "spawn fresh" and "send message" already
# appear in phase-2/phase-4 on main for unrelated reasons (vacuous), so they are excluded.
_REPROMPT = r"re-?prompt|re-?spawn"


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
    actions_idx = text.find("\n## Actions")
    assert actions_idx != -1, "section not found: ## Actions"
    step3 = _slice(text[actions_idx:], r"^3\. ", r"^4\. ")
    assert "{scratchpad_dir}/research/findings-{agent-name}.md" in step3 or \
        "{scratchpad_dir}/research/findings-{your-name}.md" in step3
    assert re.search(r"itself", step3, re.I)
    assert re.search(r"summary", step3, re.I) and re.search(r"\bpath\b", step3, re.I)
    _assert_orchestrator_verifies(step3)


def test_ac3_phase4_reanchor_contract():
    text = _read("phases/phase-4-architect.md")
    block = _slice(text, r"^## Re-Anchoring", r"^## Actions")
    assert "{scratchpad_dir}/architecture/approach-{your-name}.md" in block
    assert re.search(r"itself", block, re.I)
    assert re.search(r"summary", block, re.I) and re.search(r"\bpath\b", block, re.I)
    _assert_orchestrator_verifies(block)


def test_ac3_phase7_step1_contract():
    text = _read("phases/phase-7-synthesize.md")
    step1 = _slice(text, r"^1\. Launch", r"^\*\*Orchestrator flow")
    assert "reviews/learnings-raw.md" in step1
    assert re.search(r"itself", step1, re.I)
    assert re.search(r"summary", step1, re.I) and re.search(r"\bpath\b", step1, re.I)
    # R3: verification wording lives in step 1 .. before step 4 (slice), not anywhere in the file.
    verify_slice = _slice(text, r"^1\. Launch", r"^4\. ")
    _assert_orchestrator_verifies(verify_slice)


@pytest.mark.parametrize("rel", ["phases/phase-4-architect.md", "phases/phase-7-synthesize.md"])
def test_ac3_worker_constraints_no_direct_edit_bash_line(rel):
    # F7/R6: the old line contradicts AC1/AC2 exactly where Write is granted.
    assert "Use Read/Edit/Write/Bash directly" not in _read(rel)


def test_ac3_phase7_no_bare_scratchpad_placeholder():
    # M4: `{scratchpad}` -> `{scratchpad_dir}` (incl. the line-62 mention).
    text = _read("phases/phase-7-synthesize.md")
    assert "{scratchpad}" not in text
    assert "{scratchpad_dir}/reviews/learnings-raw.md" in text or \
        "reviews/learnings-raw.md" in text


def test_ac3_phase2_uses_your_name_placeholder():
    # M4: align phase-2 with explorer.md's {your-name}.
    text = _read("phases/phase-2-explore.md")
    assert "{agent-name}" not in text
    assert "{scratchpad_dir}/research/findings-{your-name}.md" in text


def test_ac3_phase7_documents_line_format():
    text = _read("phases/phase-7-synthesize.md")
    assert re.search(r"-\s*\[category\]\s*---\s*\[?lesson\]?", text)


# ---------------------------------------------------------------------------
# Gate fixtures (AC4 / AC5) -- bash build-gate.sh only
# ---------------------------------------------------------------------------

def _gate_fixture(tmp_path: Path, phase: str, extra_state: str, complexity="FEATURE",
                  subdirs=("research", "architecture", "reviews"), with_scratch_dir=True,
                  scratch_name="scratch"):
    # F2: subdirs is optional so tests can exercise an absent architecture/ or reviews/.
    # Rev3 C2: scratch_name lets a test put a space in the scratchpad dir name.
    scratch = tmp_path / scratch_name
    scratch.mkdir(parents=True, exist_ok=True)
    for d in subdirs:
        (scratch / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "bytedigger.json").write_text(
        json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    scratch_line = f'scratchpad_dir: "{scratch}"\n' if with_scratch_dir else ""
    state = (
        'task: "t"\n'
        f"complexity: {complexity}\n"
        "mode: AUTONOMOUS\n"
        f'current_phase: "{phase}"\n'
        'last_updated: "2026-09-30T00:00:00Z"\n'
        f"{scratch_line}"
        f"{extra_state}"
    )
    (tmp_path / "build-state.yaml").write_text(state)
    return scratch


def _run_gate(tmp_path: Path, extra_path: Path | None = None):
    env = _clean_env(tmp_path, BYTEDIGGER_CONFIG=str(tmp_path / "bytedigger.json"))
    if extra_path is not None:
        env["PATH"] = f"{extra_path}{os.pathsep}{env['PATH']}"
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
    # F1(a): exact bash reason; TS verdict reason is the same text minus the
    # "HARD BLOCK: " prefix, which toWirePayload adds on the wire (wire strings identical).
    # Hard block also wins over the (absent) approach deliverable.
    assert _reason(proc) == (
        f"HARD BLOCK: scratchpad_stale: no non-empty findings-*.md found in "
        f"{scratch}/research — Phase 2 exploration must complete before Phase 4")
    assert re.search(r"^scratchpad_stale: true$",
                     (tmp_path / "build-state.yaml").read_text(), re.M)


def test_ac4_missing_approach_soft_blocks(tmp_path):
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/architecture/approach-*.md; "


def test_ac4_zero_byte_approach_soft_blocks(tmp_path):
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    (scratch / "architecture" / "approach-a.md").write_text("")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/architecture/approach-*.md; "


def test_ac4_both_missing_exact_joined_reason(tmp_path):
    # F1(c): phase_4_architect entry first, deliverable entry after, bash printf '%s; ' format.
    scratch = _gate_fixture(tmp_path, "4", "")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == (
        "phase_4_architect=complete (got: <missing>); "
        f"missing deliverable: {scratch}/architecture/approach-*.md; ")


def test_ac4_architecture_dir_absent_soft_blocks_not_crash(tmp_path):
    # F2(d)/R2: no architecture/ directory at all -> soft block with the entry, never a crash.
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n",
                            subdirs=("research",))
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/architecture/approach-*.md; "


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
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/reviews/learnings-raw.md; "


def test_ac5_zero_byte_learnings_raw_soft_blocks(tmp_path):
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\n")
    (scratch / "reviews" / "learnings-raw.md").write_text("")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/reviews/learnings-raw.md; "


def test_ac5_both_missing_exact_joined_reason(tmp_path):
    # F1(d): review_complete entry, then deliverable entry, bash printf '%s; ' format.
    scratch = _gate_fixture(tmp_path, "7", "")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == (
        "review_complete=pass (got: <missing>); "
        f"missing deliverable: {scratch}/reviews/learnings-raw.md; ")


def test_ac5_no_scratchpad_dir_no_deliverable_entry(tmp_path):
    # F1(f): phase 7 without scratchpad_dir must not build "/reviews/learnings-raw.md".
    # AC5c on main: also dies on the missing learning_backend read -> RED on main.
    _gate_fixture(tmp_path, "7", "review_complete: pass\n", with_scratch_dir=False)
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "learnings-raw" not in proc.stdout


def test_ac5c_backend_file_no_extracted_missing_raw_soft_blocks(tmp_path):
    # F2(a)/R2: real synthesizer-stop state (learning_backend set, learnings_extracted absent)
    # must not crash the :309 read; it reaches the verdict.
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\nlearning_backend: file\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/reviews/learnings-raw.md; "


def test_ac5c_backend_file_no_extracted_header_only_passes(tmp_path):
    # F2(b): same state, header-only file -> exit 0 (stderr may carry the existing WARN).
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\nlearning_backend: file\n")
    (scratch / "reviews" / "learnings-raw.md").write_text("## New Learnings\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ac5c_trivial_backend_sqlite_no_extracted_passes(tmp_path):
    # F2(c): TRIVIAL + learning_backend: sqlite + no learnings_extracted -> exit 0, not checked.
    _gate_fixture(tmp_path, "7", "review_complete: pass\nlearning_backend: sqlite\n",
                  complexity="TRIVIAL")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "learnings-raw" not in proc.stdout


def test_ac5c_reviews_dir_absent_soft_blocks_not_crash(tmp_path):
    # F2(e)/R2: no reviews/ directory -> soft block with the AC5 entry, never a crash.
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\nlearning_backend: file\n",
                            subdirs=("research", "architecture"))
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/reviews/learnings-raw.md; "


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
# Rev 3 -- C2 (spaces in scratchpad path), C3 (TRIVIAL), C4 (no git diff by architect)
# ---------------------------------------------------------------------------

def test_c2_gate4_scratchpad_path_with_space_passes(tmp_path):
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n",
                            scratch_name="my scratch")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    (scratch / "architecture" / "approach-a.md").write_text("approach\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_c2_gate7_scratchpad_path_with_space_passes(tmp_path):
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\n",
                            scratch_name="my scratch")
    (scratch / "reviews" / "learnings-raw.md").write_text("- [testing] --- a lesson\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_c2_gate7_missing_file_reason_contains_real_spaced_path(tmp_path):
    scratch = _gate_fixture(tmp_path, "7", "review_complete: pass\n",
                            scratch_name="my scratch")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == f"missing deliverable: {scratch}/reviews/learnings-raw.md; "
    assert "my scratch" in _reason(proc)


def test_c3_gate7_trivial_without_review_complete_passes(tmp_path):
    # bash used to demand review_complete even for TRIVIAL (TS already skips).
    _gate_fixture(tmp_path, "7", "", complexity="TRIVIAL")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "review_complete" not in proc.stdout


def test_c4_phase4_reanchor_architect_does_not_run_git_diff():
    text = _read("phases/phase-4-architect.md")
    block = _slice(text, r"^## Re-Anchoring", r"^## Actions")
    assert not re.search(r"^\s*\d+\.\s*Run `git diff", block, re.M), \
        "architect has no shell: it must not be told to run git diff"
    # the orchestrator supplies the `git diff --stat` output instead
    paras = re.split(r"\n\s*\n", block)
    assert any(
        re.search(r"orchestrator", p, re.I) and "git diff --stat" in p
        and re.search(r"provid|paste|supplie|includ", p, re.I)
        for p in paras
    ), "block must say the orchestrator provides the `git diff --stat` output"


def test_c11_gate_survives_gnu_stat(tmp_path):
    # C11: on GNU coreutils `stat -f` means --file-system and exits 0 with
    # multi-line output, so `stat -f %m ... || stat -c %Y ...` never falls back
    # and the arithmetic on $mtime dies. The gate must survive a GNU-style stat.
    scratch = _gate_fixture(tmp_path, "4", "phase_4_architect: complete\n")
    (scratch / "research" / "findings-a.md").write_text("findings\n")
    (scratch / "architecture" / "approach-a.md").write_text("approach\n")

    shim = tmp_path / "shim"
    shim.mkdir()
    stat_shim = shim / "stat"
    stat_shim.write_text(
        "#!/bin/bash\n"
        'last="${@: -1}"\n'
        "has_f=0; has_c=0; has_y=0\n"
        'for a in "$@"; do\n'
        '  [ "$a" = "-f" ] && has_f=1\n'
        '  [ "$a" = "-c" ] && has_c=1\n'
        '  [ "$a" = "%Y" ] && has_y=1\n'
        "done\n"
        'if [ "$has_f" = 1 ]; then\n'
        '  printf \'  File: "%s"\\n    ID: 0 Namelen: 255 Type: apfs\\n\' "$last"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$has_c" = 1 ] && [ "$has_y" = 1 ]; then\n'
        "  python3 -c 'import os,sys;print(int(os.stat(sys.argv[1]).st_mtime))' \"$last\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    stat_shim.chmod(0o755)

    proc = _run_gate(tmp_path, extra_path=shim)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unbound variable" not in proc.stderr, proc.stderr


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
        # F8/R7: CI sets BD_REQUIRE_SQLITE=1 so the sqlite half can never silently skip.
        if os.environ.get("BD_REQUIRE_SQLITE") == "1":
            pytest.fail("sqlite3 binary not available but BD_REQUIRE_SQLITE=1")
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
    # M6: exactly ONE WARN line, and that same line carries the count and the format.
    warn = [l for l in proc.stderr.splitlines() if "WARN" in l]
    assert len(warn) == 1, proc.stderr
    assert re.search(r"\b3\b", warn[0]), warn[0]
    assert "- [category] --- lesson" in warn[0], warn[0]


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


EMPTYCAT_BODY = VALID_BODY + "- [!!!] --- lesson with no usable category\n"
BLANKISH_BODY = VALID_BODY + "   \n\t\n  ## indented heading\n"
HEADER_ONLY_BODY = "## New Learnings\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_empty_sanitized_category_counts_as_parse_error(tmp_path, backend):
    # F4/R4: `- [!!!] --- lesson` sanitizes to an empty category -> not a learning -> +1 error.
    # sqlite drops it in the bash read loop, so a python-only counter would miss it.
    proc, state = _extract(tmp_path, backend, EMPTYCAT_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 1$", state, re.M)
    warn = [l for l in proc.stderr.splitlines() if "WARN" in l]
    assert len(warn) == 1, proc.stderr
    assert re.search(r"\b1\b", warn[0]) and "- [category] --- lesson" in warn[0]


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_whitespace_only_and_indented_heading_are_not_errors(tmp_path, backend):
    # M5/R4: blank = strip()=="" ; heading = strip().startswith("#") -- same on both backends.
    proc, state = _extract(tmp_path, backend, BLANKISH_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


INDENTED_BODY = "## New Learnings\n\n  - [bug-fix] --- indented lesson\n"
FENCED_T12_BODY = "```markdown\n" + T12_BODY + "```\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_c6_indented_valid_bullet_is_stored_in_both_backends(tmp_path, backend):
    # Rev3 C6: file backend matches line.strip() like sqlite (sqlite is already a guard).
    proc, state = _extract(tmp_path, backend, INDENTED_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 1$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


@pytest.mark.parametrize("backend", BACKENDS)
def test_c7_code_fence_lines_are_not_parse_errors(tmp_path, backend):
    # Rev3 C7: ```markdown / ``` fence lines are skipped; T12 content still yields 3 errors.
    proc, state = _extract(tmp_path, backend, FENCED_T12_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 3$", state, re.M)
    warn = [l for l in proc.stderr.splitlines() if "WARN" in l]
    assert len(warn) == 1, proc.stderr
    assert re.search(r"\b3\b", warn[0]), warn[0]


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_header_only_reports_zero_no_warn(tmp_path, backend):
    # M5/R4: header-only file (sqlite early-exit path included) still writes 0, no WARN.
    proc, state = _extract(tmp_path, backend, HEADER_ONLY_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 0$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


# ---------------------------------------------------------------------------
# AC7 -- CI wiring (F8 / R7)
# ---------------------------------------------------------------------------

def test_ac7_ci_manifests_job_runs_this_suite_with_sqlite_required():
    import yaml  # pyyaml is installed in the manifests job (pip install pytest pyyaml)

    ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["manifests"]
    steps = [s for s in job["steps"]
             if "tests/test_worker_deliverables.py" in str(s.get("run", ""))]
    assert steps, "manifests job has no step running tests/test_worker_deliverables.py"
    step = steps[0]
    assert "pytest" in step["run"]
    # YAML may parse an unquoted 1 as int; compare as string.
    assert str((step.get("env") or {}).get("BD_REQUIRE_SQLITE")) == "1", \
        "CI step must set BD_REQUIRE_SQLITE=1 so the sqlite half fails instead of skipping"
