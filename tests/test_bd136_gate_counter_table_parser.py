"""RED tests for bd#136 items 4-6 -- per-phase gate counter, declarative
deliverable table, one learnings parser.

Spec: docs/decisions/2026-10-02-bd136-gate-counter-deliverable-table-learnings-parser.md

Coverage: A1-A9. A10 (regression guards) is the existing suites
tests/build-gate.bats, tests/learning-store*.bats and
tests/test_worker_deliverables.py, which must stay green; nothing is added here.

Scripts under test are driven through subprocess in tmp_path fixtures (bash
gate, TS gate via `bun run`, learning stores, learnings_parse.py CLI). Nothing
is imported from the repo, no sys.path manipulation, no mocks. If `bun` is not
on PATH the TS-dependent tests FAIL (CI installs bun); sqlite3-dependent parts
of A9 skip when the binary is missing (BD_REQUIRE_SQLITE=1 turns skip to fail).

Tests marked "GUARD" pass on current main and protect post-GREEN behavior.
All other tests are expected RED today.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "build-gate.sh"
TS_GATE = REPO_ROOT / "scripts" / "ts" / "build-phase-gate.ts"
STORE = REPO_ROOT / "scripts" / "learning-store.sh"
PARSER = REPO_ROOT / "scripts" / "learnings_parse.py"
SCHEMA = REPO_ROOT / "tests" / "fixtures" / "learning-schema.sql"

BACKENDS = ["bash", "ts"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for k in ("HAL_DIR", "CLAUDE_PLUGIN_ROOT", "BYTEDIGGER_PHRASES_PATH"):
        env.pop(k, None)
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def _make_workdir(base: Path, name: str, phase: str, extra_state: str = "",
                  complexity: str = "FEATURE", scratch: Path | None = None) -> Path:
    wd = base / name
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "bytedigger.json").write_text(
        json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    (wd / "build-metadata.json").write_text(json.dumps({"complexity": complexity}))
    scratch_line = f'scratchpad_dir: "{scratch}"\n' if scratch is not None else ""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # State is written last so any artifact written afterwards is not "stale".
    (wd / "build-state.yaml").write_text(
        'task: "t"\n'
        f"complexity: {complexity}\n"
        "mode: AUTONOMOUS\n"
        f'current_phase: "{phase}"\n'
        f'last_updated: "{now}"\n'
        f"{scratch_line}"
        f"{extra_state}"
    )
    return wd


def _run(backend: str, wd: Path, root: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    env = _env(BYTEDIGGER_CONFIG=str(wd / "bytedigger.json"))
    if backend == "bash":
        cmd = ["bash", str(root / "scripts" / "build-gate.sh")]
    else:
        bun = shutil.which("bun")
        if bun is None:
            pytest.fail("bun not found on PATH (CI installs bun; TS-gate tests must not skip)")
        cmd = [bun, "run", str(root / "scripts" / "ts" / "build-phase-gate.ts")]
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                          env=env, cwd=str(wd), timeout=120)


def _reason(proc: subprocess.CompletedProcess) -> str:
    out = proc.stdout.decode("utf-8").strip()
    assert out, f"empty stdout; stderr={proc.stderr.decode('utf-8', 'replace')}"
    return json.loads(out.splitlines()[-1])["reason"]


def _block_json(reason: str) -> bytes:
    return (json.dumps({"decision": "block", "reason": reason},
                       separators=(",", ":")) + "\n").encode("utf-8")


def _state_vals(wd: Path, key: str) -> list[str]:
    text = (wd / "build-state.yaml").read_text()
    return re.findall(rf"^{re.escape(key)}:[ \t]*(.*)$", text, re.M)


def _describe(proc) -> str:
    return (f"rc={proc.returncode} stdout={proc.stdout.decode('utf-8', 'replace')!r} "
            f"stderr={proc.stderr.decode('utf-8', 'replace')!r}")


# ---------------------------------------------------------------------------
# A1 / A2 / A3 / A4 -- per-phase gate counter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_a1_counter_resets_when_phase_changes(tmp_path, backend):
    """A1 (C3): counter 3 from phase 4.5, soft block in 5.2 -> block, counter 1, phase 5.2."""
    wd = _make_workdir(tmp_path, "w", "5.2",
                       "gate_block_counter: 3\ngate_block_phase: 4.5\n")
    proc = _run(backend, wd)
    assert proc.returncode == 2, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["1"]
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]
    assert _state_vals(wd, "gate_bypass") == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_a2_counter_same_phase_bypasses_after_three(tmp_path, backend):
    """A2 (C2): counter 3 in same phase -> bypass exit 0, counter 4, one gate_bypass."""
    wd = _make_workdir(tmp_path, "w", "5.2",
                       "gate_block_counter: 3\ngate_block_phase: 5.2\n")
    proc = _run(backend, wd)
    assert proc.returncode == 0, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["4"]
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]
    assert _state_vals(wd, "gate_bypass") == ["true"]
    assert _state_vals(wd, "gate_bypass_phase") == ["5.2"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_a3_legacy_state_without_phase_keeps_counting(tmp_path, backend):
    """A3 (C1): no gate_block_phase, counter 3 -> bypass; the new phase line is recorded."""
    wd = _make_workdir(tmp_path, "w", "5.2", "gate_block_counter: 3\n")
    proc = _run(backend, wd)
    assert proc.returncode == 0, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["4"]
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]
    assert _state_vals(wd, "gate_bypass") == ["true"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_a4_two_bypasses_keep_each_key_once(tmp_path, backend):
    """A4: two bypasses in a row -> each of the four keys appears exactly once."""
    wd = _make_workdir(tmp_path, "w", "5.2",
                       "gate_block_counter: 3\ngate_block_phase: 5.2\n")
    for _ in range(2):
        proc = _run(backend, wd)
        assert proc.returncode == 0, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["5"]
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]
    assert _state_vals(wd, "gate_bypass") == ["true"]
    assert _state_vals(wd, "gate_bypass_phase") == ["5.2"]


# ---------------------------------------------------------------------------
# A5 -- byte parity bash vs TS (exact bash-form reasons)
# ---------------------------------------------------------------------------

_MISS = "(got: <missing>)"
# phase -> (extra_state, expected reason; "{S}" = scratch dir)
_A5_CASES = {
    "4.5": ("", f"plan_review=pass {_MISS}; "),
    "5": ("", f"plan_review=pass {_MISS}; phase_5_implement=complete {_MISS}; "
              f"opus_validation=pass {_MISS}; "),
    "5.1": ("", "missing artifact: build-red-output.log; "),
    "5.2": ("", f"opus_validation=pass {_MISS}; phase_52a_gherkin=complete {_MISS}; "),
    "5.5": ("test_integrity_check: \n", "test_integrity_check has no value; "),
    "7": ("", f"review_complete=pass {_MISS}; "
              "missing deliverable: {S}/reviews/learnings-raw.md; "),
}


def _a5_parity(tmp_path: Path, phase: str):
    extra, expected = _A5_CASES[phase]
    scratch = tmp_path / "scratch"
    (scratch / "reviews").mkdir(parents=True, exist_ok=True)
    expected = expected.replace("{S}", str(scratch))
    runs = {}
    for b in BACKENDS:
        wd = _make_workdir(tmp_path, b, phase, extra,
                           scratch=scratch if phase == "7" else None)
        runs[b] = _run(b, wd)
    bash, ts = runs["bash"], runs["ts"]
    assert bash.returncode == ts.returncode == 2, \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
    assert bash.stdout == ts.stdout, f"bash={bash.stdout!r} ts={ts.stdout!r}"
    assert bash.stdout == _block_json(expected), bash.stdout


@pytest.mark.parametrize("phase", ["4.5", "5", "5.1", "7"])
def test_a5_guard_parity_phases_already_equal(tmp_path, phase):
    """A5 GUARD: these phases already match bash/TS byte-for-byte today."""
    _a5_parity(tmp_path, phase)


@pytest.mark.parametrize("phase", ["5.2", "5.5"])
def test_a5_parity_phases_that_drifted(tmp_path, phase):
    """A5: TS 5.2 / 5.5 reasons must equal the bash form (TS drops detail / trailing '; ')."""
    _a5_parity(tmp_path, phase)


# GUARD: fully satisfied state passes in both backends (protects table rewiring).
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("phase", ["4.5", "5", "5.1", "5.2", "5.5", "7"])
def test_guard_satisfied_state_passes(tmp_path, backend, phase):
    """GUARD: when every deliverable is present the gate exits 0 with empty stdout."""
    states = {
        "4.5": "plan_review: pass\n",
        "5": "plan_review: pass\nphase_5_implement: complete\nopus_validation: pass\n",
        "5.1": "",
        "5.2": "opus_validation: pass\nphase_52a_gherkin: complete\n",
        "5.5": "test_integrity_check: pass\n",
        "7": "review_complete: pass\n",
    }
    scratch = tmp_path / "scratch"
    (scratch / "reviews").mkdir(parents=True)
    (scratch / "reviews" / "learnings-raw.md").write_text("- [a] --- b\n")
    wd = _make_workdir(tmp_path, "w", phase, states[phase],
                       scratch=scratch if phase == "7" else None)
    if phase == "5.1":
        (wd / "build-red-output.log").write_text("FAILED test_x\n")
    proc = _run(backend, wd)
    assert proc.returncode == 0, _describe(proc)
    assert proc.stdout == b""


# ---------------------------------------------------------------------------
# A6 / A7 -- the table drives behavior (temp copy of the plugin)
# ---------------------------------------------------------------------------

def _plugin_copy(tmp_path: Path) -> Path:
    root = (tmp_path / "plugin").resolve()
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(GATE, root / "scripts" / "build-gate.sh")
    table = REPO_ROOT / "scripts" / "phase-deliverables.tsv"
    if table.exists():
        shutil.copy2(table, root / "scripts" / "phase-deliverables.tsv")
    shutil.copytree(REPO_ROOT / "scripts" / "ts", root / "scripts" / "ts",
                    ignore=shutil.ignore_patterns("__tests__", "node_modules"))
    return root


def _table(root: Path) -> Path:
    return root / "scripts" / "phase-deliverables.tsv"


@pytest.mark.parametrize("backend", BACKENDS)
def test_a6_removing_a_row_changes_verdict_without_code_edit(tmp_path, backend):
    """A6: dropping the 5.2 phase_52a_gherkin row turns a block into a pass."""
    root = _plugin_copy(tmp_path)
    assert _table(root).exists(), "scripts/phase-deliverables.tsv does not exist yet"
    state = "opus_validation: pass\n"  # gherkin missing
    base = _run(backend, _make_workdir(tmp_path, "before", "5.2", state), root)
    assert base.returncode == 2, _describe(base)
    assert _reason(base) == f"phase_52a_gherkin=complete {_MISS}; "

    lines = _table(root).read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [l for l in lines
            if l.rstrip("\n").split("\t")[:3] != ["5.2", "field_eq", "phase_52a_gherkin"]]
    assert len(kept) == len(lines) - 1, "5.2 phase_52a_gherkin row not found (TAB-separated)"
    _table(root).write_text("".join(kept), encoding="utf-8")

    after = _run(backend, _make_workdir(tmp_path, "after", "5.2", state), root)
    assert after.returncode == 0, _describe(after)


@pytest.mark.parametrize("backend", BACKENDS)
def test_a6_adding_a_row_changes_verdict_without_code_edit(tmp_path, backend):
    """A6: appending a field_set row for 5.2 makes a passing state block with that entry."""
    root = _plugin_copy(tmp_path)
    assert _table(root).exists(), "scripts/phase-deliverables.tsv does not exist yet"
    state = "opus_validation: pass\nphase_52a_gherkin: complete\n"
    base = _run(backend, _make_workdir(tmp_path, "before", "5.2", state), root)
    assert base.returncode == 0, _describe(base)

    text = _table(root).read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    _table(root).write_text(text + "5.2\tfield_set\tcustom_marker\n", encoding="utf-8")

    after = _run(backend, _make_workdir(tmp_path, "after", "5.2", state), root)
    assert after.returncode == 2, _describe(after)
    assert after.stdout == _block_json("custom_marker has no value; ")


@pytest.mark.parametrize("backend", BACKENDS)
def test_a7_missing_table_is_a_soft_block(tmp_path, backend):
    """A7: deleted table -> exit 2, reason 'deliverable table unreadable: <abs path>'."""
    root = _plugin_copy(tmp_path)
    if _table(root).exists():
        _table(root).unlink()
    wd = _make_workdir(tmp_path, "w", "4.5", "plan_review: pass\n")
    proc = _run(backend, wd, root)
    assert proc.returncode == 2, _describe(proc)
    reason = _reason(proc)
    assert "deliverable table unreadable:" in reason, reason
    assert "phase-deliverables.tsv" in reason, reason
    assert reason.startswith("deliverable table unreadable: /"), reason  # absolute path
    assert reason.endswith("; "), reason


@pytest.mark.parametrize("backend", BACKENDS)
def test_a7_unknown_kind_is_a_soft_block_entry(tmp_path, backend):
    """A7: unknown kind on physical line 3 -> exact documented entry."""
    root = _plugin_copy(tmp_path)
    _table(root).write_text("# comment\n\n4.5\tbogus_kind\tplan_review\n", encoding="utf-8")
    wd = _make_workdir(tmp_path, "w", "4.5", "plan_review: pass\n")
    proc = _run(backend, wd, root)
    assert proc.returncode == 2, _describe(proc)
    assert proc.stdout == _block_json("deliverable table: unknown kind 'bogus_kind' (line 3); ")


# ---------------------------------------------------------------------------
# A8 -- single-source pins (comments stripped)
# ---------------------------------------------------------------------------

def _code_only(rel: str, comment_prefixes: tuple[str, ...]) -> str:
    out = []
    for line in (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(comment_prefixes):
            continue
        out.append(line)
    return "\n".join(out)


_TABLE_STRINGS = ["plan_review", "phase_5_implement", "phase_52a_gherkin",
                  "test_integrity_check", "learnings-raw.md", "contains no failures"]


@pytest.mark.parametrize("needle", _TABLE_STRINGS)
def test_a8_bash_gate_has_no_hand_written_deliverable_strings(needle):
    """A8: build-gate.sh keeps no deliverable-name literal outside comments."""
    assert needle not in _code_only("scripts/build-gate.sh", ("#",))


@pytest.mark.parametrize("needle", _TABLE_STRINGS)
def test_a8_ts_gate_has_no_hand_written_deliverable_strings(needle):
    """A8: build-phase-gate.ts keeps no deliverable-name literal outside comments."""
    assert needle not in _code_only("scripts/ts/build-phase-gate.ts", ("//", "/*", "*"))


_PARSER_FRAGMENTS = ["(?:---?|", "re.sub(r'[^a-z0-9]+'"]


@pytest.mark.parametrize("fragment", _PARSER_FRAGMENTS)
@pytest.mark.parametrize("script", ["learning-store.sh", "learning-store-sqlite.sh"])
def test_a8_store_scripts_carry_no_parser_copy(script, fragment):
    """A8: neither store script embeds the learnings regex / category sanitiser."""
    assert fragment not in _code_only(f"scripts/{script}", ("#",))


# ---------------------------------------------------------------------------
# A9 -- one learnings parser
# ---------------------------------------------------------------------------

CORPUS = (
    b"## New Learnings\n"
    b"\n"
    b"- [Architecture] --- Valid triple dash\n"
    b"- [bug fix!] -- Valid double dash\n"
    b"- [Testing] \xe2\x80\x94 Valid em dash\n"
    b"```markdown\n"
    b"- no bracket here\n"
    b"- [MALFORMED --- no closing bracket\n"
    b"```\n"
    b"   \n"
    b"- [!!!] --- category sanitises to nothing\n"
    b"- [encoding] --- bad \xff byte\n"
)
EXPECTED_ENTRIES = [
    ("architecture", "Valid triple dash"),
    ("bug-fix", "Valid double dash"),
    ("testing", "Valid em dash"),
    ("encoding", "bad � byte"),
]
EXPECTED_ERRORS = 3
EXPECTED_PARSER_STDOUT = (
    f"{EXPECTED_ERRORS}\n"
    + "".join(f"{c}\x1f{l}\n" for c, l in EXPECTED_ENTRIES)
).encode("utf-8")


def test_a9_parser_cli_exact_output(tmp_path):
    """A9: `python3 learnings_parse.py <raw>` prints the error count, then cat\\x1flesson lines."""
    raw = tmp_path / "learnings-raw.md"
    raw.write_bytes(CORPUS)
    proc = subprocess.run(["python3", str(PARSER), str(raw)], stdin=subprocess.DEVNULL,
                          capture_output=True, env=_env(), timeout=60)
    assert proc.returncode == 0, _describe(proc)
    assert proc.stdout == EXPECTED_PARSER_STDOUT, proc.stdout


def test_a9_parser_cli_unopenable_file_exits_1(tmp_path):
    """A9: unopenable input -> message on stderr, exit 1, nothing on stdout."""
    proc = subprocess.run(["python3", str(PARSER), str(tmp_path / "nope.md")],
                          stdin=subprocess.DEVNULL, capture_output=True,
                          env=_env(), timeout=60)
    assert PARSER.exists(), "scripts/learnings_parse.py does not exist yet"
    assert proc.returncode == 1, _describe(proc)
    assert proc.stdout == b""
    assert proc.stderr.strip() != b""


def _extract(tmp_path: Path, backend: str):
    """Run learning-store.sh extract on CORPUS; return (proc, wd)."""
    wd = tmp_path / backend
    wd.mkdir()
    cfg = wd / "bytedigger.json"
    cfg.write_text(json.dumps({"learning": {
        "backend": backend, "max_inject": 10, "max_stored": 200,
        "storage_path": ".bytedigger/learnings"}}))
    scratch = wd / "scratch"
    (scratch / "reviews").mkdir(parents=True)
    (scratch / "reviews" / "learnings-raw.md").write_bytes(CORPUS)
    env = _env(BYTEDIGGER_CONFIG=str(cfg))
    if backend == "sqlite":
        db = wd / "learnings.db"
        subprocess.run(["sqlite3", str(db)], input=SCHEMA.read_text(), text=True,
                       check=True, env=env, timeout=60)
        env["LEARNING_DB_URL"] = str(db)
    proc = subprocess.run(["bash", str(STORE), "extract", str(scratch), "--config", str(cfg)],
                          stdin=subprocess.DEVNULL, capture_output=True, env=env,
                          cwd=str(wd), timeout=120)
    return proc, wd


def _file_entries(wd: Path) -> set[tuple[str, str]]:
    out = set()
    for f in (wd / ".bytedigger" / "learnings").glob("*.md"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("- "):
                out.add((f.stem, line[2:]))
    return out


def _sqlite_entries(wd: Path) -> set[tuple[str, str]]:
    res = subprocess.run(
        ["sqlite3", "-separator", "\x1f", str(wd / "learnings.db"),
         "SELECT pattern, approach FROM learning_entries"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return {tuple(l.split("\x1f", 1)) for l in res.stdout.splitlines() if l}


def _parse_errors(wd: Path) -> str | None:
    m = re.search(r"^learnings_parse_errors: (\d+)$",
                  (wd / "build-state.yaml").read_text(), re.M)
    return m.group(1) if m else None


def _need_sqlite():
    if shutil.which("sqlite3") is None:
        if os.environ.get("BD_REQUIRE_SQLITE") == "1":
            pytest.fail("sqlite3 binary not available but BD_REQUIRE_SQLITE=1")
        pytest.skip("sqlite3 binary not available")


def test_a9_guard_file_backend_stores_expected_set_and_errors(tmp_path):
    """A9 GUARD: file backend already stores the corpus set and reports 3 parse errors."""
    proc, wd = _extract(tmp_path, "file")
    assert proc.returncode == 0, _describe(proc)
    assert _file_entries(wd) == set(EXPECTED_ENTRIES)
    assert _parse_errors(wd) == str(EXPECTED_ERRORS)


def test_a9_sqlite_backend_matches_file_backend(tmp_path):
    """A9: sqlite rows equal the file backend set (invalid utf-8 degrades, not fails) and
    both backends write the same learnings_parse_errors."""
    _need_sqlite()
    fproc, fwd = _extract(tmp_path, "file")
    sproc, swd = _extract(tmp_path, "sqlite")
    assert fproc.returncode == 0, _describe(fproc)
    assert sproc.returncode == 0, _describe(sproc)
    assert _sqlite_entries(swd) == _file_entries(fwd) == set(EXPECTED_ENTRIES)
    assert _parse_errors(swd) == _parse_errors(fwd) == str(EXPECTED_ERRORS)
    assert re.search(r"^learnings_extracted: 4$",
                     (swd / "build-state.yaml").read_text(), re.M)
