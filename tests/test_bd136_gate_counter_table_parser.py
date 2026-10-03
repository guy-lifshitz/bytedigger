"""RED tests for bd#136 items 4-6 -- per-phase gate counter, declarative
deliverable table, one learnings parser.

Spec: docs/decisions/2026-10-02-bd136-gate-counter-deliverable-table-learnings-parser.md

Coverage: A1-A9, A9b, A11 and the Rev 2 (section R2) items M1-M4, m1, m4, m5,
m6, m8, m9, m10. A10 (regression guards) is the existing suites
tests/build-gate.bats, tests/learning-store*.bats and
tests/test_worker_deliverables.py, which must stay green; nothing is added here.

Scripts under test are driven through subprocess in tmp_path fixtures (bash
gate, TS gate via `bun run`, learning stores, learnings_parse.py CLI). Nothing
is imported from the repo, no sys.path manipulation, no mocks. If `bun` is not
on PATH the TS-dependent tests FAIL (they never skip). CI wiring (A11): the
`manifests` job gets a step that sets BD_REQUIRE_SQLITE=1, installs bun
pinned and checksummed like the `pytest` job, and runs this file, so the bun
and sqlite parts run there. test_a11 pins that step. Locally, sqlite3-dependent
parts skip when the binary is missing (BD_REQUIRE_SQLITE=1 turns skip to fail).

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
    # bd#89 P3c: the learnings-raw.md deliverable row is gone from gate 7.
    "7": ("", f"review_complete=pass {_MISS}; "),
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

def _plugin_copy(tmp_path: Path, name: str = "plugin") -> Path:
    root = (tmp_path / name).resolve()
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
                  "test_integrity_check", "learnings-raw.md",
                  "opus_validation", "review_complete"]  # data only (M1, m-r2-1b)


@pytest.mark.parametrize("needle", _TABLE_STRINGS)
def test_a8_bash_gate_has_no_hand_written_deliverable_strings(needle):
    """A8: build-gate.sh keeps no deliverable-name literal outside comments."""
    assert needle not in _code_only("scripts/build-gate.sh", ("#",))


@pytest.mark.parametrize("needle", _TABLE_STRINGS)
def test_a8_ts_gate_has_no_hand_written_deliverable_strings(needle):
    """A8: build-phase-gate.ts keeps no deliverable-name literal outside comments."""
    assert needle not in _code_only("scripts/ts/build-phase-gate.ts", ("//", "/*", "*"))


def test_a8_m1_log_red_template_literal_once_in_bash_gate():
    """M1: the `contains no failures` kind template appears exactly once in build-gate.sh."""
    assert _code_only("scripts/build-gate.sh", ("#",)).count("contains no failures") == 1


def test_a8_m1_log_red_template_literal_once_in_ts_gate():
    """M1: the `contains no failures` kind template appears exactly once in build-phase-gate.ts."""
    assert _code_only("scripts/ts/build-phase-gate.ts",
                      ("//", "/*", "*")).count("contains no failures") == 1


_PARSER_FRAGMENTS =["(?:---?|", "re.sub(r'[^a-z0-9]+'"]


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


# ---------------------------------------------------------------------------
# M2 -- whole-table validation (both backends, byte-identical)
# ---------------------------------------------------------------------------

def _both_with_table(tmp_path: Path, table: bytes, phase: str, state: str,
                     expect_rc: int):
    """Run both backends against a plugin copy whose table is exactly `table`.
    Asserts equal exit code / stdout and returns the bash result."""
    runs = {}
    for b in BACKENDS:
        root = _plugin_copy(tmp_path, f"plugin-{b}")
        _table(root).write_bytes(table)
        wd = _make_workdir(tmp_path, f"w-{b}", phase, state)
        runs[b] = _run(b, wd, root)
    bash, ts = runs["bash"], runs["ts"]
    assert bash.returncode == ts.returncode == expect_rc, \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
    assert bash.stdout == ts.stdout, f"bash={bash.stdout!r} ts={ts.stdout!r}"
    return bash


def test_m2_spaces_instead_of_tabs_is_malformed_row(tmp_path):
    """M2(a): a 5.2 row split by spaces -> malformed row (line 3), counting comment+blank."""
    table = (b"# header\n"
             b"5.2\tfield_eq\topus_validation\tpass\n"
             b"5.2  field_eq  phase_52a_gherkin  complete\n")
    proc = _both_with_table(tmp_path, table, "5.2", "opus_validation: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: malformed row (line 3); ")


def test_m2_missing_expected_argument_is_malformed_row(tmp_path):
    """M2(b): `5.2 field_eq opus_validation` (no expected) -> malformed row (line 1)."""
    proc = _both_with_table(tmp_path, b"5.2\tfield_eq\topus_validation\n",
                            "5.2", "opus_validation: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: malformed row (line 1); ")


def test_m2_consecutive_tabs_is_malformed_row(tmp_path):
    """M2: consecutive TABs give an empty field -> malformed (not merged)."""
    proc = _both_with_table(tmp_path, b"\n5.2\tfield_eq\t\tpass\n",
                            "5.2", "opus_validation: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: malformed row (line 2); ")


def test_m2_unknown_kind_on_other_phase_row_is_reported(tmp_path):
    """M2(c): whole table validated: a bad phase-7 row blocks current_phase 4.5."""
    table = b"7\tbogus\tx\n4.5\tfield_eq\tplan_review\tpass\n"
    proc = _both_with_table(tmp_path, table, "4.5", "plan_review: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: unknown kind 'bogus' (line 1); ")


def test_m2_crlf_line_endings_tolerated_pass(tmp_path):
    """M2: CRLF table with a satisfied state still passes (trailing \\r stripped)."""
    table = b"# c\r\n5.2\tfield_eq\topus_validation\tpass\r\n"
    proc = _both_with_table(tmp_path, table, "5.2", "opus_validation: pass\n", 0)
    assert proc.stdout == b""


def test_m2_crlf_line_endings_failing_row_has_no_cr_in_reason(tmp_path):
    """M2: CRLF table, failing row -> the reason carries no \\r from the table."""
    table = b"5.2\tfield_eq\topus_validation\tpass\r\n"
    proc = _both_with_table(tmp_path, table, "5.2", "", 2)
    assert proc.stdout == _block_json(f"opus_validation=pass {_MISS}; ")


def test_m2_last_line_without_trailing_newline_is_read(tmp_path):
    """M2: the final row with no trailing newline still produces its entry."""
    table = b"4.5\tfield_eq\tplan_review\tpass\n5.2\tfield_eq\topus_validation\tpass"
    proc = _both_with_table(tmp_path, table, "5.2", "", 2)
    assert proc.stdout == _block_json(f"opus_validation=pass {_MISS}; ")


def test_m2_gitattributes_pins_tsv_eol_lf():
    """M2: .gitattributes keeps the table LF-only."""
    ga = REPO_ROOT / ".gitattributes"
    assert ga.exists(), ".gitattributes does not exist"
    lines = [re.sub(r"\s+", " ", l.strip()) for l in ga.read_text().splitlines()]
    assert "*.tsv text eol=lf" in lines


# ---------------------------------------------------------------------------
# m10 -- JSON-safe reason for a kind containing a quote
# ---------------------------------------------------------------------------

def test_m10_unknown_kind_with_quote_is_valid_json_and_byte_identical(tmp_path):
    """m10: kind `a"b` -> both backends emit valid JSON, identical bytes."""
    proc = _both_with_table(tmp_path, b'4.5\ta"b\tplan_review\n', "4.5",
                            "plan_review: pass\n", 2)
    parsed = json.loads(proc.stdout.decode("utf-8"))
    assert parsed["reason"] == "deliverable table: unknown kind 'a\"b' (line 1); "
    assert proc.stdout == _block_json(parsed["reason"])


# ---------------------------------------------------------------------------
# m1 -- table located relative to the script (path with a space)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_m1_plugin_copy_under_path_with_space_finds_table(tmp_path, backend):
    """m1: copy under 'my plugin' dir; table found via script location, not cwd/env.
    A custom row proves the copy's own table is the one read."""
    root = _plugin_copy(tmp_path, "my plugin")
    assert _table(root).exists(), "scripts/phase-deliverables.tsv does not exist yet"
    text = _table(root).read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    _table(root).write_text(text + "5.2\tfield_set\tcustom_marker\n", encoding="utf-8")
    ok_state = "opus_validation: pass\nphase_52a_gherkin: complete\ncustom_marker: x\n"
    ok = _run(backend, _make_workdir(tmp_path, "ok", "5.2", ok_state), root)
    assert ok.returncode == 0, _describe(ok)
    bad_state = "opus_validation: pass\nphase_52a_gherkin: complete\n"
    bad = _run(backend, _make_workdir(tmp_path, "bad", "5.2", bad_state), root)
    assert bad.returncode == 2, _describe(bad)
    assert bad.stdout == _block_json("custom_marker has no value; ")


# ---------------------------------------------------------------------------
# m9 -- unreadable table, phase with no rows, unwritable state
# ---------------------------------------------------------------------------

@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="chmod 000 does not restrict root")
@pytest.mark.parametrize("backend", BACKENDS)
def test_m9_unreadable_table_same_entry_as_missing(tmp_path, backend):
    """m9: table exists but chmod 000 -> `deliverable table unreadable: <abs path>`."""
    root = _plugin_copy(tmp_path)
    assert _table(root).exists(), "scripts/phase-deliverables.tsv does not exist yet"
    _table(root).chmod(0)
    try:
        proc = _run(backend, _make_workdir(tmp_path, "w", "4.5", "plan_review: pass\n"), root)
    finally:
        _table(root).chmod(0o644)
    assert proc.returncode == 2, _describe(proc)
    assert proc.stdout == _block_json(f"deliverable table unreadable: {_table(root)}; ")


@pytest.mark.parametrize("backend", BACKENDS)
def test_m9_phase_with_no_rows_passes(tmp_path, backend):
    """m9: a copy whose table has no 4.5 rows passes phase 4.5 with nothing set."""
    root = _plugin_copy(tmp_path)
    assert _table(root).exists(), "scripts/phase-deliverables.tsv does not exist yet"
    lines = _table(root).read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [l for l in lines if l.split("\t")[0] != "4.5"]
    assert len(kept) < len(lines), "no 4.5 rows found (TAB-separated)"
    _table(root).write_text("".join(kept), encoding="utf-8")
    proc = _run(backend, _make_workdir(tmp_path, "w", "4.5", ""), root)
    assert proc.returncode == 0, _describe(proc)
    assert proc.stdout == b""


@pytest.mark.parametrize("backend", BACKENDS)
def test_m9_guard_phase_8_passes(tmp_path, backend):
    """m9 GUARD: phase 8 is not a deliverable phase in either dispatcher -> pass."""
    proc = _run(backend, _make_workdir(tmp_path, "w", "8", ""))
    assert proc.returncode == 0, _describe(proc)
    assert proc.stdout == b""


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="chmod does not restrict root")
@pytest.mark.parametrize("backend", BACKENDS)
def test_m9_unwritable_state_still_emits_block_with_warn(tmp_path, backend):
    """m9: state file and its directory read-only on a soft block -> verdict still
    emitted (exit 2, reason present) and stderr carries WARN."""
    wd = _make_workdir(tmp_path, "w", "5.2", "opus_validation: pass\n")
    (wd / "build-state.yaml").chmod(0o444)
    wd.chmod(0o555)
    try:
        proc = _run(backend, wd)
    finally:
        wd.chmod(0o755)
        (wd / "build-state.yaml").chmod(0o644)
    assert proc.returncode == 2, _describe(proc)
    assert "phase_52a_gherkin=complete" in _reason(proc)
    assert b"WARN" in proc.stderr, _describe(proc)


# ---------------------------------------------------------------------------
# m4 / m5 / m6 -- counter edge cases, TS alias canonicalisation, temp-file hygiene
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("stored", ["3abc", "-1"])
def test_m4_non_numeric_counter_counts_as_zero(tmp_path, backend, stored):
    """m4: counter `3abc` / `-1` is not ^[0-9]+$ -> 0 -> new count 1, still blocking."""
    wd = _make_workdir(tmp_path, "w", "5.2",
                       f"gate_block_counter: {stored}\ngate_block_phase: 5.2\n"
                       "opus_validation: pass\n")
    proc = _run(backend, wd)
    assert proc.returncode == 2, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["1"]
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_m4_empty_gate_block_phase_counts_as_absent_c1(tmp_path, backend):
    """m4: empty `gate_block_phase:` == absent (C1): counter 3 -> bypass, key written once."""
    wd = _make_workdir(tmp_path, "w", "5.2",
                       "gate_block_counter: 3\ngate_block_phase:\n")
    proc = _run(backend, wd)
    assert proc.returncode == 0, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["4"]
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]
    assert _state_vals(wd, "gate_bypass") == ["true"]


def test_m5_ts_alias_current_phase_52_same_stdout_as_5_2(tmp_path):
    """m5 (TS): current_phase 52 yields the same bytes as 5.2 (bash-form reason)."""
    outs = {}
    for ph in ("5.2", "52"):
        outs[ph] = _run("ts", _make_workdir(tmp_path, f"w{ph}", ph, ""))
    assert outs["52"].returncode == outs["5.2"].returncode == 2, \
        f"{_describe(outs['52'])} {_describe(outs['5.2'])}"
    assert outs["52"].stdout == outs["5.2"].stdout
    assert outs["52"].stdout == _block_json(
        f"opus_validation=pass {_MISS}; phase_52a_gherkin=complete {_MISS}; ")


def test_m5_ts_alias_current_phase_counts_as_same_phase_c2(tmp_path):
    """m5 (TS): current_phase 52, stored phase 5.2, counter 3 -> C2 bypass, phase kept 5.2."""
    wd = _make_workdir(tmp_path, "w", "52", "gate_block_counter: 3\ngate_block_phase: 5.2\n")
    proc = _run("ts", wd)
    assert proc.returncode == 0, _describe(proc)
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]
    assert _state_vals(wd, "gate_block_counter") == ["4"]


def test_m4_ts_stored_phase_alias_is_canonicalised_c2(tmp_path):
    """m4 (TS): stored `gate_block_phase: 52` vs current 5.2 is the same phase -> bypass."""
    wd = _make_workdir(tmp_path, "w", "5.2", "gate_block_counter: 3\ngate_block_phase: 52\n")
    proc = _run("ts", wd)
    assert proc.returncode == 0, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["4"]
    assert _state_vals(wd, "gate_block_phase") == ["5.2"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_m_r2_6_stored_phase_alias_is_canonicalised_c2(tmp_path, backend):
    """m-r2-6: stored `gate_block_phase: 52` vs current 5.2, counter 3, failing 5.2
    -> same phase (C2): bypass exit 0, counter 4 (bash and TS share the alias map)."""
    wd = _make_workdir(tmp_path, "w", "5.2", "gate_block_counter: 3\ngate_block_phase: 52\n")
    proc = _run(backend, wd)
    assert proc.returncode == 0, _describe(proc)
    assert _state_vals(wd, "gate_block_counter") == ["4"]
    assert _state_vals(wd, "gate_bypass") == ["true"]


# ---------------------------------------------------------------------------
# m-r2-3 / m-r2-4 / m-r2-5 / m-r2-8 -- Rev 3 additions
# ---------------------------------------------------------------------------

def test_m_r2_3_validation_entries_first_in_physical_order_then_failing_rows(tmp_path):
    """m-r2-3: valid failing 5.2 row (line 1), malformed (line 2), unknown kind (line 3)
    -> validation entries in line order, then the failing row entry; byte-identical."""
    table = (b"5.2\tfield_eq\topus_validation\tpass\n"
             b"5.2\tfield_eq\topus_validation\n"
             b"7\tbogus\tx\n")
    proc = _both_with_table(tmp_path, table, "5.2", "", 2)
    assert proc.stdout == _block_json(
        "deliverable table: malformed row (line 2); "
        "deliverable table: unknown kind 'bogus' (line 3); "
        f"opus_validation=pass {_MISS}; ")


def test_m_r2_3_unknown_kind_wins_over_wrong_arity(tmp_path):
    """m-r2-3: `5.2 bogus x y z` is an unknown kind, not a malformed row."""
    proc = _both_with_table(tmp_path, b"5.2\tbogus\tx\ty\tz\n", "5.2",
                            "opus_validation: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: unknown kind 'bogus' (line 1); ")


def test_m_r2_3_bad_phase_is_malformed_row_before_kind_check(tmp_path):
    """m-r2-3: bad phase `x.y` with a valid kind -> malformed row."""
    proc = _both_with_table(tmp_path, b"x.y\tfield_set\tfoo\n", "5.2",
                            "opus_validation: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: malformed row (line 1); ")


def test_m_r2_3_fewer_than_two_fields_is_malformed_row(tmp_path):
    """m-r2-3: a single-field line -> malformed row (not unknown kind)."""
    proc = _both_with_table(tmp_path, b"5.2\n", "5.2", "opus_validation: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: malformed row (line 1); ")


def test_m_r2_4_whitespace_only_and_indented_comment_lines_are_ignored(tmp_path):
    """m-r2-4: blank-with-spaces/tab and `   # comment` lines are skipped by both
    backends. State satisfies only the table's own row (gherkin absent), so a pass
    proves the table, not the hand-written rules, decides."""
    table = (b"   \n"
             b"   # indented comment\n"
             b" \t \n"
             b"5.2\tfield_eq\topus_validation\tpass\n")
    proc = _both_with_table(tmp_path, table, "5.2", "opus_validation: pass\n", 0)
    assert proc.stdout == b""


def test_m_r2_4_ignored_lines_still_count_in_line_numbers(tmp_path):
    """m-r2-4: whitespace-only line 1 + indented comment line 2 -> malformed row is line 3."""
    table = b"   \n   # indented comment\n5.2\tfield_eq\topus_validation\n"
    proc = _both_with_table(tmp_path, table, "5.2", "opus_validation: pass\n", 2)
    assert proc.stdout == _block_json("deliverable table: malformed row (line 3); ")


@pytest.mark.parametrize("backend", BACKENDS)
def test_m_r2_5_script_adjacent_table_wins_over_claude_plugin_root_decoy(tmp_path, backend):
    """m-r2-5: CLAUDE_PLUGIN_ROOT points at a decoy table with a different extra row;
    the verdict follows the script-adjacent table (real_marker), not the decoy."""
    base = ("5.2\tfield_eq\topus_validation\tpass\n"
            "5.2\tfield_eq\tphase_52a_gherkin\tcomplete\n")
    root = _plugin_copy(tmp_path)
    _table(root).write_text(base + "5.2\tfield_set\treal_marker\n", encoding="utf-8")
    decoy = (tmp_path / "decoy").resolve()
    (decoy / "scripts").mkdir(parents=True)
    (decoy / "scripts" / "phase-deliverables.tsv").write_text(
        base + "5.2\tfield_set\tdecoy_marker\n", encoding="utf-8")
    wd = _make_workdir(tmp_path, "w", "5.2",
                       "opus_validation: pass\nphase_52a_gherkin: complete\n"
                       "decoy_marker: x\n")
    env = _env(BYTEDIGGER_CONFIG=str(wd / "bytedigger.json"),
               CLAUDE_PLUGIN_ROOT=str(decoy))
    if backend == "bash":
        cmd = ["bash", str(root / "scripts" / "build-gate.sh")]
    else:
        bun = shutil.which("bun")
        if bun is None:
            pytest.fail("bun not found on PATH (CI installs bun; TS-gate tests must not skip)")
        cmd = [bun, "run", str(root / "scripts" / "ts" / "build-phase-gate.ts")]
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                          env=env, cwd=str(wd), timeout=120)
    assert proc.returncode == 2, _describe(proc)
    assert proc.stdout == _block_json("real_marker has no value; ")


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="chmod 000 does not restrict root")
def test_m_r2_8_unreadable_red_log_is_no_failures_byte_identical(tmp_path):
    """m-r2-8: build-red-output.log exists with FAIL content but chmod 000, phase 5.1
    -> both backends exit 2 with the `contains no failures` reason, same bytes."""
    runs = {}
    for b in BACKENDS:
        wd = _make_workdir(tmp_path, f"w-{b}", "5.1", "")
        log = wd / "build-red-output.log"
        log.write_text("FAILED test_x\n")
        log.chmod(0)
        try:
            runs[b] = _run(b, wd)
        finally:
            log.chmod(0o644)
    bash, ts = runs["bash"], runs["ts"]
    assert bash.returncode == ts.returncode == 2, \
        f"bash: {_describe(bash)} ts: {_describe(ts)}"
    assert bash.stdout == ts.stdout, f"bash={bash.stdout!r} ts={ts.stdout!r}"
    assert bash.stdout == _block_json(
        "build-red-output.log contains no failures (tests must be RED); ")


@pytest.mark.parametrize("backend", BACKENDS)
def test_m6_guard_no_fixed_name_tmp_left_after_soft_block(tmp_path, backend):
    """m6 GUARD: no `build-state.yaml.tmp` (fixed name) remains after a soft block."""
    wd = _make_workdir(tmp_path, "w", "5.2", "gate_block_counter: 1\ngate_block_phase: 5.2\n")
    proc = _run(backend, wd)
    assert proc.returncode == 2, _describe(proc)
    assert not (wd / "build-state.yaml.tmp").exists()


# ---------------------------------------------------------------------------
# m8 -- parser CLI is locale independent
# ---------------------------------------------------------------------------

def test_m8_parser_cli_utf8_stdout_under_c_locale(tmp_path):
    """m8: LC_ALL=C, no LANG, no PYTHONUTF8 / PYTHONIOENCODING, no locale coercion ->
    still succeeds and prints the pinned UTF-8 output for the invalid-utf-8 corpus."""
    raw = tmp_path / "learnings-raw.md"
    raw.write_bytes(CORPUS)
    env = _env(LC_ALL="C", PYTHONCOERCECLOCALE="0", PYTHONUTF8="0")
    for k in ("LANG", "LC_CTYPE", "PYTHONIOENCODING"):
        env.pop(k, None)
    proc = subprocess.run(["python3", str(PARSER), str(raw)], stdin=subprocess.DEVNULL,
                          capture_output=True, env=env, timeout=60)
    assert proc.returncode == 0, _describe(proc)
    assert proc.stdout == EXPECTED_PARSER_STDOUT, proc.stdout
    proc.stdout.decode("utf-8")


# ---------------------------------------------------------------------------
# M3 A9b -- behavioral single parser (stub learnings_parse.py)
# ---------------------------------------------------------------------------

_STUB_OK = 'import sys\nsys.stdout.write("2\\nstubcat\\x1fstub lesson\\n")\n'
_STUB_FAIL = 'import sys\nsys.stderr.write("stub failure\\n")\nsys.exit(1)\n'


def _extract_with_stub(tmp_path: Path, backend: str, stub_src: str):
    """Temp scripts/ dir = both store scripts (+x) + stub parser; run extract on CORPUS."""
    wd = tmp_path / backend
    wd.mkdir()
    sdir = wd / "scripts"
    sdir.mkdir()
    for name in ("learning-store.sh", "learning-store-sqlite.sh"):
        shutil.copy2(REPO_ROOT / "scripts" / name, sdir / name)
        (sdir / name).chmod(0o755)
    (sdir / "learnings_parse.py").write_text(stub_src)
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
    proc = subprocess.run(["bash", str(sdir / "learning-store.sh"), "extract", str(scratch),
                           "--config", str(cfg)], stdin=subprocess.DEVNULL,
                          capture_output=True, env=env, cwd=str(wd), timeout=120)
    return proc, wd


def test_a9b_file_backend_stores_exactly_stub_entries(tmp_path):
    """A9b: file backend never re-parses; it stores the stub CLI output only."""
    proc, wd = _extract_with_stub(tmp_path, "file", _STUB_OK)
    assert proc.returncode == 0, _describe(proc)
    assert _file_entries(wd) == {("stubcat", "stub lesson")}
    assert _parse_errors(wd) == "2"


def test_a9b_sqlite_backend_stores_exactly_stub_entries(tmp_path):
    """A9b: sqlite backend consumes the stub CLI output only."""
    _need_sqlite()
    proc, wd = _extract_with_stub(tmp_path, "sqlite", _STUB_OK)
    assert proc.returncode == 0, _describe(proc)
    assert _sqlite_entries(wd) == {("stubcat", "stub lesson")}
    assert _parse_errors(wd) == "2"


def test_a9b_file_backend_parser_exit_1_writes_zero_and_no_error_line(tmp_path):
    """A9b: stub exits 1 -> learnings_extracted: 0 and no learnings_parse_errors line."""
    proc, wd = _extract_with_stub(tmp_path, "file", _STUB_FAIL)
    assert _file_entries(wd) == set(), "file backend parsed the corpus itself"
    state = (wd / "build-state.yaml").read_text()
    assert re.search(r"^learnings_extracted: 0$", state, re.M), state
    assert _parse_errors(wd) is None


# ---------------------------------------------------------------------------
# A11 -- CI wiring
# ---------------------------------------------------------------------------

def test_a11_ci_manifests_job_runs_this_suite_with_bun_and_sqlite():
    """A11: manifests job step (name has bd#136) sets BD_REQUIRE_SQLITE=1, installs bun with
    the same BUN_VERSION/sha256 as the pytest job BEFORE the pytest run of this file."""
    import yaml  # pyyaml is installed in the manifests job (pip install pytest pyyaml)

    ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    ref = [s for s in ci["jobs"]["pytest"]["steps"]
           if "install bun" in str(s.get("name", ""))]
    assert ref, "pytest job has no 'install bun' step"
    ref_env = {k: str(v) for k, v in (ref[0].get("env") or {}).items()
               if k in ("BUN_VERSION", "BUN_SHA256")}
    assert set(ref_env) == {"BUN_VERSION", "BUN_SHA256"}

    steps = ci["jobs"]["manifests"]["steps"]
    cmd = "python -m pytest tests/test_bd136_gate_counter_table_parser.py"
    idx = [i for i, s in enumerate(steps)
           if "bd#136" in str(s.get("name", "")) and cmd in str(s.get("run", ""))]
    assert idx, "manifests job has no bd#136 step running this test file"
    ti = idx[0]
    target = steps[ti]
    assert str((target.get("env") or {}).get("BD_REQUIRE_SQLITE")) == "1"

    run = str(target["run"])
    pos = run.index(cmd)
    installers = []
    for i, s in enumerate(steps[:ti + 1]):
        env = {k: str(v) for k, v in (s.get("env") or {}).items()}
        if (env.get("BUN_VERSION") == ref_env["BUN_VERSION"]
                and env.get("BUN_SHA256") == ref_env["BUN_SHA256"]
                and "sha256sum" in str(s.get("run", ""))):
            installers.append(i)
    assert installers, "no step before the pytest run installs bun with the pinned version+sha256"
    if installers[0] == ti:
        assert run.index("sha256sum") < pos, "bun installed after the pytest command"
        assert "PATH" in run[:pos], "same-step bun install must put bun on PATH before pytest"
