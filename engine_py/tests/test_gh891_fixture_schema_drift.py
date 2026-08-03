"""RED tests for GH891 — fixture-DDL subset RED-lint (E_RED_FIXTURE_SCHEMA_DRIFT)
+ Data-Model Ground Truth spec-block (battle-818 fixture-fiction class).

Frozen spec: GH891 fixture-schema drift spec (host repo Decisions memory).

All tests referencing not-yet-existing modules (`fixture_schema`,
`ground_truth_verifier`) defer the import to inside the test body (D1CF5FDF)
so the file COLLECTS cleanly and each FAILS at assert time, never collect time.

§1i: no singleton/time resources; fixtures written deterministically to
tmp_path before invoking the UUT.
§1l: UUT (`fixture_schema`, `_collect_red_lint_findings`, `ground_truth_verifier`,
CLI drivers) is never mocked; only `_emit_safe` (an infra dependency) is
monkeypatched, per the existing sibling pattern.
"""
from __future__ import annotations

import json
import subprocess

from helpers.engine_subprocess import engine_env
import sys
from pathlib import Path
from unittest.mock import patch

from bytedigger_engine.contracts import StepResult, WorkflowContext


# ─── shared helpers (mirror test_gh595_red_lint_preflight_batch.py) ─────────

def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-gh891",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths: list, spec_path: str | None) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": spec_path,
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _write(tmp_path: Path, relpath: str, content: str) -> str:
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return relpath


def _capture_emit_factory():
    captured: list[tuple] = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    return captured, _capture


# ─── shared fixture text: battle-818 repro ──────────────────────────────────

# Reference (ground-truth) DDL matches the real behavioral_facts schema.
_GROUND_TRUTH_SPEC_TEXT = """\
## §2 Design

## Data-Model Ground Truth

```sql
CREATE TABLE behavioral_facts (
    id INTEGER PRIMARY KEY,
    fact TEXT,
    source_fact_ids TEXT,
    stale_reason TEXT
);
```

## §3 Acceptance Criteria
"""

_NO_HEADER_SPEC_TEXT = """\
## §2 Design

Some prose about pipeline-output/ppba-v2.db with no ground truth section.

## §3 Acceptance Criteria
"""

_EMPTY_HEADER_SPEC_TEXT = """\
## §2 Design

## Data-Model Ground Truth

No fence here, no opt-out marker.

## §3 Acceptance Criteria
"""

_OPT_OUT_SPEC_TEXT = _EMPTY_HEADER_SPEC_TEXT.replace(
    "No fence here, no opt-out marker.", "ground-truth: n/a"
)

# battle-818 violator: invented extra column source_doc_id (not in reference).
_DRIFT_RED_SOURCE = (
    "import sqlite3\n"
    "\n"
    "\n"
    "def test_behavioral_facts_shape():\n"
    "    conn = sqlite3.connect(':memory:')\n"
    "    conn.execute('''\n"
    "        CREATE TABLE behavioral_facts (\n"
    "            id INTEGER PRIMARY KEY,\n"
    "            fact TEXT,\n"
    "            source_fact_ids TEXT,\n"
    "            source_doc_id INTEGER,\n"
    "            stale_reason TEXT\n"
    "        )\n"
    "    ''')\n"
    "    assert conn is not None\n"
)

_PRAGMA_RED_SOURCE = _DRIFT_RED_SOURCE.replace(
    "        CREATE TABLE behavioral_facts (\n",
    "        CREATE TABLE behavioral_facts (  # fixture-schema: allow\n",
)

_HONEST_RED_SOURCE = (
    "import sqlite3\n"
    "\n"
    "\n"
    "def test_behavioral_facts_shape_honest():\n"
    "    conn = sqlite3.connect(':memory:')\n"
    "    conn.execute('''\n"
    "        CREATE TABLE behavioral_facts (\n"
    "            id INTEGER PRIMARY KEY,\n"
    "            fact TEXT\n"
    "        )\n"
    "    ''')\n"
    "    assert conn is not None\n"
)

_UNPARSEABLE_RED_SOURCE = (
    "def test_unparseable():\n"
    "    stmt = '''\n"
    "        CREATE TABLE behavioral_facts (\n"
    "            id INTEGER PRIMARY KEY,\n"
    "            fact TEXT\n"
    "    '''\n"
    "    assert stmt\n"
)


# ─── AC1 ─────────────────────────────────────────────────────────────────

def test_ac1_parse_reference_ddl_extracts_columns_excludes_extra() -> None:
    """AC1: parse_reference_ddl on a spec with Ground-Truth header + ```sql fence
    -> dict has 'behavioral_facts' key; column set equals DDL columns;
    'source_doc_id' NOT in that set."""
    from bytedigger_engine.fixture_schema import parse_reference_ddl  # ImportError at RED -> FAIL

    result = parse_reference_ddl(_GROUND_TRUTH_SPEC_TEXT)
    assert "behavioral_facts" in result, f"expected table key, got {result!r}"
    cols = result["behavioral_facts"]
    assert cols == {"id", "fact", "source_fact_ids", "stale_reason"}, f"got {cols!r}"
    assert "source_doc_id" not in cols, f"got {cols!r}"


# ─── AC2 ─────────────────────────────────────────────────────────────────

def test_ac2_parse_reference_ddl_no_header_returns_empty_dict() -> None:
    """AC2: spec-text without Ground-Truth header -> parse_reference_ddl == {}."""
    from bytedigger_engine.fixture_schema import parse_reference_ddl  # ImportError at RED -> FAIL

    result = parse_reference_ddl(_NO_HEADER_SPEC_TEXT)
    assert result == {}, f"expected empty dict, got {result!r}"


# ─── AC3 ─────────────────────────────────────────────────────────────────

def test_ac3_scan_fixture_schema_detects_battle818_drift() -> None:
    """AC3: battle-818 repro — RED source with invented source_doc_id column
    vs the AC1 reference -> exactly 1 drift finding naming the invented column."""
    from bytedigger_engine.fixture_schema import parse_reference_ddl, scan_fixture_schema  # FAIL

    reference = parse_reference_ddl(_GROUND_TRUTH_SPEC_TEXT)
    findings = scan_fixture_schema(_DRIFT_RED_SOURCE, reference)

    assert len(findings) == 1, f"expected exactly 1 finding, got {findings!r}"
    f = findings[0]
    assert f.table == "behavioral_facts", f"got {f.table!r}"
    assert "source_doc_id" in f.extra_columns, f"got {f.extra_columns!r}"
    assert f.kind == "drift", f"got {f.kind!r}"
    expected_line = _DRIFT_RED_SOURCE.splitlines().index(
        "        CREATE TABLE behavioral_facts (",
    ) + 1
    assert f.line == expected_line, f"expected line {expected_line}, got {f.line!r}"


# ─── AC4 ─────────────────────────────────────────────────────────────────

def test_ac4_scan_fixture_schema_honest_fixture_is_clean() -> None:
    """AC4: fixture whose columns are a strict subset of reference -> []."""
    from bytedigger_engine.fixture_schema import parse_reference_ddl, scan_fixture_schema  # FAIL

    reference = parse_reference_ddl(_GROUND_TRUTH_SPEC_TEXT)
    findings = scan_fixture_schema(_HONEST_RED_SOURCE, reference)
    assert findings == [], f"expected clean, got {findings!r}"


# ─── AC5 ─────────────────────────────────────────────────────────────────

def test_ac5_pragma_allow_suppresses_finding() -> None:
    """AC5: `# fixture-schema: allow` on the CREATE TABLE line suppresses
    the AC3 finding."""
    from bytedigger_engine.fixture_schema import parse_reference_ddl, scan_fixture_schema  # FAIL

    reference = parse_reference_ddl(_GROUND_TRUTH_SPEC_TEXT)
    findings = scan_fixture_schema(_PRAGMA_RED_SOURCE, reference)
    assert findings == [], f"expected pragma to suppress finding, got {findings!r}"


# ─── AC6 ─────────────────────────────────────────────────────────────────

def test_ac6_unparseable_column_list_is_loud_not_silent() -> None:
    """AC6: referenced table with unbalanced/unparseable column list ->
    1 loud finding kind=='unparseable' (GH882 lesson: never silent-pass)."""
    from bytedigger_engine.fixture_schema import parse_reference_ddl, scan_fixture_schema  # FAIL

    reference = parse_reference_ddl(_GROUND_TRUTH_SPEC_TEXT)
    findings = scan_fixture_schema(_UNPARSEABLE_RED_SOURCE, reference)
    assert len(findings) == 1, f"expected 1 loud finding, got {findings!r}"
    assert findings[0].kind == "unparseable", f"got {findings[0].kind!r}"


# ─── AC7 ─────────────────────────────────────────────────────────────────

def test_ac7_collect_red_lint_findings_batch_entry_and_event(
    tmp_path, monkeypatch
) -> None:
    """AC7: violating RED file + spec with ground truth -> batch entry with
    error_code=='E_RED_FIXTURE_SCHEMA_DRIFT', rule=='fixture-schema',
    recoverable is False; 'red_fixture_schema_violation' emitted."""
    from bytedigger_engine.workflows import phase_5_implement as p5  # symbol exists; new block-5 absent -> assertion FAIL

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(_GROUND_TRUTH_SPEC_TEXT)
    relpath = _write(tmp_path, "tests/test_drift.py", _DRIFT_RED_SOURCE)
    resolved = str((tmp_path / relpath).resolve())
    ctx = _make_ctx(tmp_path)

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        findings = p5._collect_red_lint_findings(
            [resolved], str(tmp_path), ctx, {}, spec_path=str(spec_path),
        )

    schema_entries = [f for f in findings if f.get("rule") == "fixture-schema"]
    assert schema_entries, f"expected a fixture-schema entry, got {findings!r}"
    entry = schema_entries[0]
    assert entry["error_code"] == "E_RED_FIXTURE_SCHEMA_DRIFT", f"got {entry!r}"
    assert entry["recoverable"] is False, f"got {entry!r}"

    names = [c[0] for c in captured]
    assert "red_fixture_schema_violation" in names, f"got events={names!r}"


# ─── AC8 ─────────────────────────────────────────────────────────────────

def test_ac8_kill_switch_disables_gate_no_entries(tmp_path, monkeypatch) -> None:
    """AC8: HAL_FIXTURE_SCHEMA_GATE=0 -> no fixture-schema entries + gate_disabled
    event with gate=='HAL_FIXTURE_SCHEMA_GATE'."""
    from bytedigger_engine.workflows import phase_5_implement as p5  # FAIL: no such gate block yet

    monkeypatch.setenv("HAL_FIXTURE_SCHEMA_GATE", "0")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(_GROUND_TRUTH_SPEC_TEXT)
    relpath = _write(tmp_path, "tests/test_drift.py", _DRIFT_RED_SOURCE)
    resolved = str((tmp_path / relpath).resolve())
    ctx = _make_ctx(tmp_path)

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        findings = p5._collect_red_lint_findings(
            [resolved], str(tmp_path), ctx, {}, spec_path=str(spec_path),
        )

    schema_entries = [f for f in findings if f.get("rule") == "fixture-schema"]
    assert schema_entries == [], f"expected no entries with gate off, got {findings!r}"
    disabled = [
        c for c in captured
        if c[0] == "gate_disabled" and c[1].get("gate") == "HAL_FIXTURE_SCHEMA_GATE"
    ]
    assert disabled, f"expected gate_disabled(HAL_FIXTURE_SCHEMA_GATE), got {captured!r}"


# ─── AC9 ─────────────────────────────────────────────────────────────────

def test_ac9_spec_path_none_skip_event_no_entries_no_exception(tmp_path) -> None:
    """AC9: spec_path=None -> fixture_schema_lint_skipped event reason=='no_spec_path',
    no entries, no exception raised."""
    from bytedigger_engine.workflows import phase_5_implement as p5  # FAIL: kwarg / block absent

    relpath = _write(tmp_path, "tests/test_drift.py", _DRIFT_RED_SOURCE)
    resolved = str((tmp_path / relpath).resolve())
    ctx = _make_ctx(tmp_path)

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        findings = p5._collect_red_lint_findings(
            [resolved], str(tmp_path), ctx, {}, spec_path=None,
        )

    schema_entries = [f for f in findings if f.get("rule") == "fixture-schema"]
    assert schema_entries == [], f"expected no entries, got {findings!r}"
    skips = [
        c for c in captured
        if c[0] == "fixture_schema_lint_skipped" and c[1].get("reason") == "no_spec_path"
    ]
    assert skips, f"expected fixture_schema_lint_skipped(no_spec_path), got {captured!r}"


# ─── AC10 ─────────────────────────────────────────────────────────────────

def test_ac10_no_ground_truth_block_skip_event(tmp_path) -> None:
    """AC10: spec without ground-truth block -> skip event reason=='no_ground_truth_block'."""
    from bytedigger_engine.workflows import phase_5_implement as p5  # FAIL: block absent

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(_NO_HEADER_SPEC_TEXT)
    relpath = _write(tmp_path, "tests/test_drift.py", _DRIFT_RED_SOURCE)
    resolved = str((tmp_path / relpath).resolve())
    ctx = _make_ctx(tmp_path)

    captured, _capture = _capture_emit_factory()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        p5._collect_red_lint_findings(
            [resolved], str(tmp_path), ctx, {}, spec_path=str(spec_path),
        )

    skips = [
        c for c in captured
        if c[0] == "fixture_schema_lint_skipped"
        and c[1].get("reason") == "no_ground_truth_block"
    ]
    assert skips, f"expected fixture_schema_lint_skipped(no_ground_truth_block), got {captured!r}"


# ─── AC11 ─────────────────────────────────────────────────────────────────

def test_ac11_data_model_ground_truth_axis_registered() -> None:
    """AC11: new axis 'DATA-MODEL GROUND TRUTH (§1ae)' is in SPEC_HIGH_BINDING_AXES
    AND a verbatim substring of _SPEC_STABLE_PREFIX (GH729 parity-by-construction)."""
    from bytedigger_engine.workflows.phase_45_spec import SPEC_HIGH_BINDING_AXES, _SPEC_STABLE_PREFIX

    axis = "DATA-MODEL GROUND TRUTH (§1ae)"
    assert axis in SPEC_HIGH_BINDING_AXES, f"got axes={SPEC_HIGH_BINDING_AXES!r}"
    assert axis in _SPEC_STABLE_PREFIX, "axis text missing from _SPEC_STABLE_PREFIX"


# ─── AC12 ─────────────────────────────────────────────────────────────────

def test_ac12_find_missing_ground_truth_three_cases() -> None:
    """AC12: find_missing_ground_truth — .db mention w/o header -> missing-ground-truth-ddl;
    header+fence -> []; header w/o fence & w/o opt-out -> empty-ground-truth-ddl."""
    engine_root = Path(__file__).resolve().parent.parent
    lib_dir = str(engine_root / "bytedigger_engine" / "scripts" / "lib")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    from bytedigger_engine.scripts.lib.ground_truth_verifier import find_missing_ground_truth  # ImportError -> FAIL

    missing = find_missing_ground_truth(_NO_HEADER_SPEC_TEXT, "/dummy")
    assert len(missing) == 1, f"got {missing!r}"
    assert missing[0].rule_id == "missing-ground-truth-ddl", f"got {missing[0]!r}"
    assert missing[0].offset >= 0

    clean = find_missing_ground_truth(_GROUND_TRUTH_SPEC_TEXT, "/dummy")
    assert clean == [], f"expected [], got {clean!r}"

    empty = find_missing_ground_truth(_EMPTY_HEADER_SPEC_TEXT, "/dummy")
    assert len(empty) == 1, f"got {empty!r}"
    assert empty[0].rule_id == "empty-ground-truth-ddl", f"got {empty[0]!r}"
    assert empty[0].offset >= 0


# ─── AC13 ─────────────────────────────────────────────────────────────────

def test_ac13_lint_spec_subprocess_flags_missing_ground_truth(tmp_path) -> None:
    """AC13: lint_spec.py subprocess on a spec mentioning .db without a header
    -> exit 1, stdout contains 'missing-ground-truth-ddl'; a clean spec
    (header+fence, no other trigger tokens) does not contain that token."""
    engine_root = Path(__file__).resolve().parent.parent
    lint_spec = engine_root / "bytedigger_engine" / "scripts" / "spec_lint" / "lint_spec.py"
    assert lint_spec.exists(), f"lint_spec.py driver not found at {lint_spec}"

    bad_spec = tmp_path / "bad_spec.md"
    bad_spec.write_text(_NO_HEADER_SPEC_TEXT)
    proc_bad = subprocess.run(
        [sys.executable, str(lint_spec), str(bad_spec), "--hal-root", str(tmp_path)], env=engine_env(),
        capture_output=True, text=True,
    )
    assert proc_bad.returncode == 1, (
        f"expected exit 1, got {proc_bad.returncode}; stdout={proc_bad.stdout!r}"
    )
    assert "missing-ground-truth-ddl" in proc_bad.stdout, (
        f"expected token in stdout, got {proc_bad.stdout!r}"
    )

    clean_spec = tmp_path / "clean_spec.md"
    clean_spec.write_text(_GROUND_TRUTH_SPEC_TEXT)
    proc_clean = subprocess.run(
        [sys.executable, str(lint_spec), str(clean_spec), "--hal-root", str(tmp_path)], env=engine_env(),
        capture_output=True, text=True,
    )
    assert "missing-ground-truth-ddl" not in proc_clean.stdout, (
        f"did not expect token in clean-spec stdout, got {proc_clean.stdout!r}"
    )


# ─── AC14 ─────────────────────────────────────────────────────────────────

def test_ac14_registries_carry_new_entries() -> None:
    """AC14: error_codes.ERROR_CODES has E_RED_FIXTURE_SCHEMA_DRIFT;
    flags_catalog carries HAL_FIXTURE_SCHEMA_GATE with kind=='gate', default=='1'."""
    from bytedigger_engine import error_codes
    from bytedigger_engine import flags_catalog

    assert "E_RED_FIXTURE_SCHEMA_DRIFT" in error_codes.ERROR_CODES, (
        f"got keys sample: {list(error_codes.ERROR_CODES)[:5]!r}..."
    )

    catalog = getattr(flags_catalog, "FLAGS", None) or getattr(
        flags_catalog, "FLAGS_CATALOG", None
    )
    assert catalog is not None, "flags_catalog has no discoverable FLAGS dict"
    assert "HAL_FIXTURE_SCHEMA_GATE" in catalog, f"got keys sample: {list(catalog)[:5]!r}..."
    entry = catalog["HAL_FIXTURE_SCHEMA_GATE"]
    assert entry.get("kind") == "gate", f"got {entry!r}"
    assert entry.get("default") == "1", f"got {entry!r}"


# ─── AC15 ─────────────────────────────────────────────────────────────────

def test_ac15_cli_fixture_schema_lint_exit_codes(tmp_path) -> None:
    """AC15: fixture-schema-lint.py --spec <spec> <bad.py> -> exit 1, stdout has
    FIXTURE-SCHEMA-DRIFT + source_doc_id; clean file -> exit 0; missing spec -> exit 2."""
    build_dir = Path(__file__).resolve().parent.parent.parent
    cli = build_dir / "fixture-schema-lint.py"
    if not cli.exists():
        pytest_fail_msg = f"fixture-schema-lint.py does not exist yet at {cli}"
        import pytest
        pytest.fail(pytest_fail_msg)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(_GROUND_TRUTH_SPEC_TEXT)
    bad_file = _write(tmp_path, "bad.py", _DRIFT_RED_SOURCE)
    clean_file = _write(tmp_path, "clean.py", _HONEST_RED_SOURCE)

    proc_bad = subprocess.run(
        [sys.executable, str(cli), "--spec", str(spec_path), str(tmp_path / bad_file)], env=engine_env(),
        capture_output=True, text=True,
    )
    assert proc_bad.returncode == 1, (
        f"expected exit 1, got {proc_bad.returncode}; stdout={proc_bad.stdout!r}"
    )
    assert "FIXTURE-SCHEMA-DRIFT" in proc_bad.stdout, f"got {proc_bad.stdout!r}"
    assert "source_doc_id" in proc_bad.stdout, f"got {proc_bad.stdout!r}"

    proc_clean = subprocess.run(
        [sys.executable, str(cli), "--spec", str(spec_path), str(tmp_path / clean_file)], env=engine_env(),
        capture_output=True, text=True,
    )
    assert proc_clean.returncode == 0, (
        f"expected exit 0, got {proc_clean.returncode}; stdout={proc_clean.stdout!r}"
    )

    proc_missing_spec = subprocess.run(
        [sys.executable, str(cli), "--spec", str(tmp_path / "nope.md"), str(tmp_path / bad_file)], env=engine_env(),
        capture_output=True, text=True,
    )
    assert proc_missing_spec.returncode == 2, (
        f"expected exit 2, got {proc_missing_spec.returncode}; stderr={proc_missing_spec.stderr!r}"
    )
