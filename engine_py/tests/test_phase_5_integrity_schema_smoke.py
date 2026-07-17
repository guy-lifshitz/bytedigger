"""RED tests — GH892 integrity schema-smoke step.

Covers spec §3 AC1-AC13. The UUT (`lib/schema_smoke.py`, the 4th
`schema_smoke` step in `phase_5_integrity_workflow`, and the three new
E_SCHEMA_SMOKE_* error codes) does not exist yet. Imports of the UUT are
deferred to inside test bodies (D1CF5FDF) so this file COLLECTS cleanly
and fails at assert/import time, never at collection time.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

import pytest  # noqa: E402

from contracts import WorkflowContext  # noqa: E402


# ─── local fixtures/helpers (deliberately NOT imported from sibling test) ────


def make_ctx(scratchpad: Path, *, git_cwd: str | None = None, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="schema smoke",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def seed_repo_no_test_diff(repo: Path) -> None:
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    commit_file(repo, "src/foo.py", "def foo():\n    return 2\n", "code-only")


# Realistic v2-like DDL: behavioral_facts uses source_fact_ids (plural, TEXT),
# never had a source_doc_id column.
V2_DDL = """
CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE facts (id INTEGER PRIMARY KEY, doc_id INTEGER, body TEXT);
CREATE TABLE behavioral_facts (
    id INTEGER PRIMARY KEY,
    source_fact_ids TEXT,
    label TEXT
);
"""

# v4-like DDL: behavioral_facts does not exist at all.
V4_DDL = """
CREATE TABLE facts (id INTEGER PRIMARY KEY, doc_id INTEGER, body TEXT);
"""


def write_schema(tmp_path: Path, name: str, ddl: str) -> Path:
    p = tmp_path / name
    p.write_text(ddl)
    return p


def write_artifact(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


HONEST_ARTIFACT = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute("SELECT source_fact_ids FROM behavioral_facts").fetchall()
conn.execute("SELECT doc_id FROM facts").fetchall()
print("ok")
sys.exit(0)
"""

# Reproduces battle-818 exactly: prints the pseudo-error to stdout, exits 0,
# never actually touches the DB.
FAKE_SILENT_MISMATCH_ARTIFACT = """
import json
print(json.dumps({"error": "no such column: source_doc_id"}))
"""

# Real dry-run against v4-like snapshot: queries behavioral_facts for real,
# so sqlite3 itself raises "no such table: behavioral_facts" on stderr.
REAL_QUERY_ARTIFACT = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute("SELECT source_fact_ids FROM behavioral_facts").fetchall()
"""

DRYRUN_ERROR_ARTIFACT = """
import sys
sys.stderr.write("boom, unrelated failure\\n")
sys.exit(2)
"""

CANARY_ARTIFACT_TEMPLATE = """
import sys
open(sys.argv[2], "w").write("ran")
"""


def _py_argv(script: Path, *extra: str) -> list[str]:
    return [sys.executable, str(script), *extra]


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_schema_smoke_module_importable_with_expected_symbols():
    try:
        mod = __import__("lib.schema_smoke", fromlist=["_"])
    except ImportError as e:
        pytest.fail(f"lib.schema_smoke not importable yet: {e}")
    assert callable(getattr(mod, "restore_schema_to_tempdb", None))
    assert callable(getattr(mod, "classify_dryrun_output", None))
    assert callable(getattr(mod, "run_schema_smoke", None))


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_restore_schema_to_tempdb_creates_empty_real_table(tmp_path):
    import sqlite3
    from lib.schema_smoke import restore_schema_to_tempdb

    dest = Path(os.path.realpath(tmp_path)) / "restored.db"
    restore_schema_to_tempdb(V2_DDL, dest)

    assert dest.is_file()
    conn = sqlite3.connect(str(dest))
    try:
        rows = conn.execute("SELECT count(*) FROM behavioral_facts").fetchone()
        assert rows[0] == 0
    finally:
        conn.close()


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_honest_artifact_passes_against_v2_snapshot(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)

    targets = [
        {
            "name": "honest-v2",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    assert report["overall"] == "pass"
    assert report["targets"][0]["status"] == "pass"


def test_ac3_step_writes_report_file_via_workflow(tmp_path, monkeypatch):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)
    targets = [
        {
            "name": "honest-v2",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets),
    )

    report_path = scratchpad / "integrity" / "schema-smoke.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text())
    assert report["overall"] == "pass"


# ─── AC4 (battle-818 exact repro) ───────────────────────────────────────────


def test_ac4_battle_818_silent_mismatch_stdout_exit0_is_caught(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "violator.py", FAKE_SILENT_MISMATCH_ARTIFACT)

    targets = [
        {
            "name": "battle818",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact)],
        }
    ]
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    assert report["overall"] == "mismatch"
    assert "no such column: source_doc_id" in report["targets"][0]["signature"]


def test_ac4_step_returns_e_schema_smoke_mismatch(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "violator.py", FAKE_SILENT_MISMATCH_ARTIFACT)
    targets = [
        {
            "name": "battle818",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact)],
        }
    ]

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets),
    )

    assert result.status == "error"
    assert result.error_code == "E_SCHEMA_SMOKE_MISMATCH"
    assert result.recoverable is False
    assert result.data["category"] == "FIXTURE_SCHEMA_MISMATCH"
    report_path = scratchpad / "integrity" / "schema-smoke.json"
    assert "no such column: source_doc_id" in report_path.read_text()


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_battle_818_violator_against_v4_snapshot_no_such_table(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    schema = write_schema(tmp_path, "v4.sql", V4_DDL)
    artifact = write_artifact(tmp_path, "real_query.py", REAL_QUERY_ARTIFACT)

    targets = [
        {
            "name": "battle818-v4",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    assert report["overall"] == "mismatch"
    assert "no such table" in report["targets"][0]["signature"].lower()
    assert "behavioral_facts" in report["targets"][0]["signature"]


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_missing_schema_path_is_unavailable_not_ok(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    missing = tmp_path / "does_not_exist.sql"
    artifact = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)
    targets = [
        {
            "name": "missing-schema",
            "schema_path": str(missing),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    assert report["overall"] == "unavailable"
    assert report["targets"][0]["status"] == "unavailable"
    assert report["targets"][0]["reason"] == "missing_schema"


def test_ac6_step_returns_e_schema_smoke_unavailable(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    missing = tmp_path / "does_not_exist.sql"
    artifact = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)
    targets = [
        {
            "name": "missing-schema",
            "schema_path": str(missing),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets),
    )

    assert result.status == "error"
    assert result.error_code == "E_SCHEMA_SMOKE_UNAVAILABLE"
    assert result.recoverable is False


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_dryrun_nonzero_exit_without_signature_is_dryrun_error(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "boom.py", DRYRUN_ERROR_ARTIFACT)
    targets = [
        {
            "name": "boom",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact)],
        }
    ]
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    assert report["overall"] == "dryrun_error"


def test_ac7_step_returns_e_schema_smoke_dryrun_error(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "boom.py", DRYRUN_ERROR_ARTIFACT)
    targets = [
        {
            "name": "boom",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact)],
        }
    ]

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets),
    )

    assert result.status == "error"
    assert result.error_code == "E_SCHEMA_SMOKE_DRYRUN_ERROR"
    assert result.recoverable is False


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_not_configured_is_explicit_skip_ok(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    assert result.step_name == "schema_smoke"
    assert result.status == "ok"
    assert result.data["skipped"] is True
    assert result.data["reason"] == "not_configured"


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_gate_disabled_skips_dryrun_and_no_canary_file(tmp_path, monkeypatch):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "canary.py", CANARY_ARTIFACT_TEMPLATE)
    canary_marker = tmp_path / "canary_marker.txt"
    targets = [
        {
            "name": "canary",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact), "{db}", str(canary_marker)],
        }
    ]

    monkeypatch.setenv("HAL_SCHEMA_SMOKE_GATE", "0")

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets),
    )

    assert result.step_name == "schema_smoke"
    assert result.status == "ok"
    assert result.data["reason"] == "gate_disabled"
    assert not canary_marker.exists()


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_workflow_definition_has_four_steps_ending_schema_smoke():
    from phase_5_integrity import phase_5_integrity_workflow

    wf = phase_5_integrity_workflow()
    assert len(wf.steps) == 4
    assert wf.steps[3].name == "schema_smoke"


def test_ac10_full_engine_execute_reaches_schema_smoke_on_no_changes(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)
    targets = [
        {
            "name": "honest-v2",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets),
    )

    assert result.step_name == "schema_smoke"
    report_path = scratchpad / "integrity" / "schema-smoke.json"
    assert report_path.is_file()


# ─── AC11 ───────────────────────────────────────────────────────────────────


def test_ac11_reentry_is_idempotent_and_does_not_accumulate_tempdirs(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)
    targets = [
        {
            "name": "honest-v2",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]

    tmp_root = Path(os.path.realpath(tempfile_gettempdir()))
    before = set(p.name for p in tmp_root.iterdir())

    report1 = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    report2 = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)

    after = set(p.name for p in tmp_root.iterdir())

    assert report1["overall"] == report2["overall"] == "pass"
    # No leaked schema-smoke temp directories after two invocations. Spec
    # §2.2 mandates mkdtemp(prefix="schema_smoke_") — search by that exact
    # mandated prefix, not a loose substring.
    leaked = after - before
    assert not any(name.startswith("schema_smoke_") for name in leaked)


def tempfile_gettempdir() -> str:
    import tempfile

    return tempfile.gettempdir()


def test_ac11_step_reentry_rewrites_report_same_status(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    artifact = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)
    targets = [
        {
            "name": "honest-v2",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(artifact), "{db}"],
        }
    ]
    ctx = make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets)

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result1, _ = eng.execute("p5i", ctx)
    report_path = scratchpad / "integrity" / "schema-smoke.json"
    mtime1 = report_path.stat().st_mtime_ns
    content1 = report_path.read_text()

    result2, _ = eng.execute("p5i", ctx)
    mtime2 = report_path.stat().st_mtime_ns
    content2 = report_path.read_text()

    assert result1.status == result2.status == "ok"
    assert json.loads(content1)["overall"] == json.loads(content2)["overall"]
    assert mtime2 >= mtime1


# ─── AC12 ───────────────────────────────────────────────────────────────────


def test_ac12_timeout_is_unavailable_not_pass(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    sleeper = write_artifact(
        tmp_path,
        "sleeper.py",
        "import time\ntime.sleep(5)\n",
    )
    targets = [
        {
            "name": "sleeper",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(sleeper)],
        }
    ]
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=1)
    assert report["overall"] == "unavailable"
    assert report["targets"][0]["reason"] == "timeout"


def test_ac12_step_plumbs_schema_smoke_timeout_sec_from_org_config(tmp_path):
    """Pins config plumbing: org_config['schema_smoke_timeout_sec'] must
    actually reach run_schema_smoke via the _schema_smoke step, not just the
    lib function called directly."""
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    sleeper = write_artifact(
        tmp_path,
        "sleeper.py",
        "import time\ntime.sleep(5)\n",
    )
    targets = [
        {
            "name": "sleeper",
            "schema_path": str(schema),
            "cmd": [sys.executable, str(sleeper)],
        }
    ]

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            schema_smoke_targets=targets,
            schema_smoke_timeout_sec=1,
        ),
    )

    assert result.step_name == "schema_smoke"
    assert result.status == "error"
    assert result.error_code == "E_SCHEMA_SMOKE_UNAVAILABLE"


# ─── AC13 ───────────────────────────────────────────────────────────────────


def test_ac13_error_codes_registry_has_schema_smoke_entries():
    from error_codes import ERROR_CODES

    for code in (
        "E_SCHEMA_SMOKE_MISMATCH",
        "E_SCHEMA_SMOKE_UNAVAILABLE",
        "E_SCHEMA_SMOKE_DRYRUN_ERROR",
    ):
        assert code in ERROR_CODES, f"{code} missing from ERROR_CODES registry"


# ─── AC14 (blocker: multi-target priority, order-independent) ───────────────


def _multi_targets(tmp_path: Path, *, violator_first: bool) -> list[dict]:
    schema = write_schema(tmp_path, "v2.sql", V2_DDL)
    honest = write_artifact(tmp_path, "honest.py", HONEST_ARTIFACT)
    violator = write_artifact(tmp_path, "violator.py", FAKE_SILENT_MISMATCH_ARTIFACT)

    honest_target = {
        "name": "honest-v2",
        "schema_path": str(schema),
        "cmd": [sys.executable, str(honest), "{db}"],
    }
    violator_target = {
        "name": "battle818",
        "schema_path": str(schema),
        "cmd": [sys.executable, str(violator)],
    }
    if violator_first:
        return [violator_target, honest_target]
    return [honest_target, violator_target]


def test_ac14_mismatch_wins_over_pass_violator_first(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    targets = _multi_targets(tmp_path, violator_first=True)
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    assert report["overall"] == "mismatch"


def test_ac14_mismatch_wins_over_pass_violator_last(tmp_path):
    from lib.schema_smoke import run_schema_smoke

    targets = _multi_targets(tmp_path, violator_first=False)
    report = run_schema_smoke(targets, cwd=tmp_path, timeout_sec=10)
    assert report["overall"] == "mismatch"


def test_ac14_step_returns_mismatch_regardless_of_target_order(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    for violator_first in (True, False):
        repo = tmp_path / f"repo_{violator_first}"
        seed_repo_no_test_diff(repo)
        scratchpad = tmp_path / f"scratch_{violator_first}"
        sub = tmp_path / f"targets_{violator_first}"
        sub.mkdir()
        targets = _multi_targets(sub, violator_first=violator_first)

        eng = WorkflowEngine()
        eng.register("p5i", phase_5_integrity_workflow())
        result, _ = eng.execute(
            "p5i",
            make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=targets),
        )

        assert result.step_name == "schema_smoke"
        assert result.status == "error"
        assert result.error_code == "E_SCHEMA_SMOKE_MISMATCH"


# ─── AC15 ───────────────────────────────────────────────────────────────────


def test_ac15_empty_targets_list_is_not_configured_skip(tmp_path):
    from phase_5_integrity import phase_5_integrity_workflow
    from engine import WorkflowEngine

    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), schema_smoke_targets=[]),
    )

    assert result.step_name == "schema_smoke"
    assert result.status == "ok"
    assert result.data["skipped"] is True
    assert result.data["reason"] == "not_configured"
