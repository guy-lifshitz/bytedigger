"""RED tests for GH923 Lot 2 (ported from hal) — incident classification CLI.

lib/incident_classify.py and classify_incident.py do NOT exist yet (§1q /
D1CF5FDF): ALL references to these NEW symbols are deferred to inside test
bodies so this file COLLECTS cleanly and fails at assert/import time, never
at collection time.

§1l forcing function: every AC anchors on a real file rewrite (os.replace)
or a real exit code / real subprocess invocation — none stub-passable.
classify / list_records / main / match_indices / load_entries ARE the UUT
and are never mocked/patched here.

Seam adaptations from the hal source (PORT_SPEC.md):
  - HAL_INCIDENT_LOG -> BD_INCIDENT_LOG (new-in-bd JSONL path seam, AC8)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _rec(run_id, phase, step_name, cycle=1, classification="unknown", workaround=None, status="error"):
    return {
        "ts": "2026-07-17T00:00:00Z",
        "run_id": run_id,
        "phase": phase,
        "step_name": step_name,
        "cycle": cycle,
        "status": status,
        "error_code": "E_X",
        "error": "boom",
        "wall_ms": 1,
        "tokens": None,
        "classification": classification,
        "workaround": workaround,
    }


def _write_ledger(path: Path, records: "list[str]"):
    """records: list of raw line strings (already JSON or garbage)."""
    path.write_text("\n".join(records) + "\n", encoding="utf-8")


# ─── AC1: single-target mutation, others byte-identical ─────────────────────

def test_ac1_classify_mutates_target_record_only(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1"))
    line1 = json.dumps(_rec("r1", "p2", "s2"))
    line2 = json.dumps(_rec("r2", "p1", "s1"))
    _write_ledger(log_path, [line0, line1, line2])

    exit_code, stdout, _stderr = classify(
        log_path, run_id="r1", phase="p2", classification="gate-FP",
    )
    assert exit_code == 0, f"AC1 FAIL: expected exit 0, got {exit_code}"

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, f"AC1 FAIL: expected 3 lines, got {len(lines)}"
    assert lines[0] == line0, "AC1 FAIL: non-target line 0 must be byte-identical"
    assert lines[2] == line2, "AC1 FAIL: non-target line 2 must be byte-identical"

    target = json.loads(lines[1])
    assert target["classification"] == "gate-FP", (
        f"AC1 FAIL: target classification not updated, got {target['classification']!r}"
    )
    for key in ("run_id", "phase", "step_name", "cycle", "status", "error_code", "error", "wall_ms"):
        orig = json.loads(line1)
        assert target[key] == orig[key], f"AC1 FAIL: field {key!r} changed unexpectedly"

    stdout_record = json.loads(stdout)
    assert stdout_record["classification"] == "gate-FP", (
        "AC1 FAIL: stdout must be the updated record as valid JSON"
    )


# ─── AC2: workaround truncation to 500 chars / preserved when omitted ───────

def test_ac2_workaround_truncated_to_500_chars(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1"))])

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", classification="gate-FP", workaround="x" * 100_000,
    )
    assert exit_code == 0
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert len(record["workaround"]) == 500, (
        f"AC2 FAIL: expected workaround truncated to 500 chars, got {len(record['workaround'])}"
    )


def test_ac2_workaround_omitted_preserves_existing_value(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1", workaround="w0"))])

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", classification="gate-FP",
    )
    assert exit_code == 0
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["workaround"] == "w0", (
        f"AC2 FAIL: workaround must be left as-is when arg omitted, got {record['workaround']!r}"
    )


# ─── AC3: argparse bad/missing --class → SystemExit 2, file untouched ───────

def test_ac3_main_bad_class_value_exits_2_file_unchanged(tmp_path):
    from lib.incident_classify import main  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1"))
    _write_ledger(log_path, [line0])

    with pytest.raises(SystemExit) as excinfo:
        main(["--run", "r1", "--class", "bogus", "--path", str(log_path)])
    assert excinfo.value.code == 2, f"AC3 FAIL: expected SystemExit 2, got {excinfo.value.code}"
    assert log_path.read_text(encoding="utf-8").splitlines()[0] == line0, (
        "AC3 FAIL: ledger file must be unchanged after argparse rejection"
    )


def test_ac3_main_missing_class_exits_2(tmp_path):
    from lib.incident_classify import main  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1"))])

    with pytest.raises(SystemExit) as excinfo:
        main(["--run", "r1", "--path", str(log_path)])
    assert excinfo.value.code == 2, f"AC3 FAIL: expected SystemExit 2, got {excinfo.value.code}"


# ─── AC4: no match / missing file → exit 4 ───────────────────────────────────

def test_ac4_no_matching_record_exits_4_file_unchanged(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1"))
    _write_ledger(log_path, [line0])

    exit_code, stdout, _stderr = classify(
        log_path, run_id="nope", classification="gate-FP",
    )
    assert exit_code == 4, f"AC4 FAIL: expected exit 4, got {exit_code}"
    assert stdout == "", "AC4 FAIL: stdout must be empty on no-match"
    assert log_path.read_text(encoding="utf-8").splitlines()[0] == line0, (
        "AC4 FAIL: ledger file must be byte-identical after a no-match classify"
    )


def test_ac4_missing_ledger_file_exits_4(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "does_not_exist.jsonl"
    exit_code, stdout, stderr = classify(
        log_path, run_id="r1", classification="gate-FP",
    )
    assert exit_code == 4, f"AC4 FAIL: expected exit 4 for missing ledger, got {exit_code}"
    assert stdout == ""
    assert stderr != "", "AC4 FAIL: stderr must explain the missing ledger"


# ─── AC5: multi-match ambiguity → exit 3; --last resolves to highest index ──

def test_ac5_multi_match_no_last_exits_3_lists_candidates_file_unchanged(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1", cycle=1))
    line1 = json.dumps(_rec("r1", "p1", "s1", cycle=2))
    _write_ledger(log_path, [line0, line1])

    exit_code, stdout, stderr = classify(
        log_path, run_id="r1", phase="p1", step_name="s1", classification="gate-FP",
    )
    assert exit_code == 3, f"AC5 FAIL: expected exit 3 for ambiguous match, got {exit_code}"
    assert stdout == ""
    candidate_lines = [ln for ln in stderr.splitlines() if ln.strip()]
    assert len(candidate_lines) == 2, (
        f"AC5 FAIL: expected 2 candidate lines in stderr, got {len(candidate_lines)}"
    )
    for cand_line in candidate_lines:
        cand = json.loads(cand_line)
        for key in ("idx", "ts", "run_id", "phase", "step_name", "cycle", "status", "classification"):
            assert key in cand, f"AC5 FAIL: candidate line missing key {key!r}: {cand!r}"

    assert log_path.read_text(encoding="utf-8").splitlines() == [line0, line1], (
        "AC5 FAIL: ledger file must be byte-identical after an ambiguous (no --last) classify"
    )


def test_ac5_multi_match_with_last_mutates_only_higher_index(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1", cycle=1))
    line1 = json.dumps(_rec("r1", "p1", "s1", cycle=2))
    _write_ledger(log_path, [line0, line1])

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", phase="p1", step_name="s1", classification="gate-FP", last=True,
    )
    assert exit_code == 0, f"AC5 FAIL: expected exit 0 with --last, got {exit_code}"

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == line0, "AC5 FAIL: earlier matching line must be untouched when --last is used"
    later = json.loads(lines[1])
    assert later["classification"] == "gate-FP", (
        "AC5 FAIL: --last must mutate the highest-index matching line"
    )


# ─── AC6: unparseable middle line survives, is never matched ────────────────

def test_ac6_unparseable_middle_line_survives_byte_identical(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1"))
    garbage = "not json{{{"
    line2 = json.dumps(_rec("r2", "p1", "s1"))
    _write_ledger(log_path, [line0, garbage, line2])

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", classification="gate-FP",
    )
    assert exit_code == 0, f"AC6 FAIL: expected exit 0, got {exit_code}"

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == garbage, "AC6 FAIL: unparseable middle line must survive byte-identical"


def test_ac6_unparseable_line_never_matched(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    garbage = "not json{{{"
    _write_ledger(log_path, [garbage])

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", classification="gate-FP",
    )
    assert exit_code == 4, (
        f"AC6 FAIL: an unparseable-only ledger must never match anything, got exit {exit_code}"
    )


# ─── AC7: list_records — all / run_id filter / unclassified filter / missing file ──

def test_ac7_list_records_prints_all_as_compact_json_lines(tmp_path):
    from lib.incident_classify import list_records  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1")), json.dumps(_rec("r2", "p1", "s1"))])

    exit_code, stdout, _stderr = list_records(log_path)
    assert exit_code == 0
    out_lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(out_lines) == 2, f"AC7 FAIL: expected 2 printed records, got {len(out_lines)}"
    for ln in out_lines:
        json.loads(ln)  # must be valid JSON


def test_ac7_list_records_run_id_filter(tmp_path):
    from lib.incident_classify import list_records  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1")), json.dumps(_rec("r2", "p1", "s1"))])

    exit_code, stdout, _stderr = list_records(log_path, run_id="r1")
    assert exit_code == 0
    out_lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(out_lines) == 1, f"AC7 FAIL: run_id filter expected 1 row, got {len(out_lines)}"
    assert json.loads(out_lines[0])["run_id"] == "r1"


def test_ac7_list_records_unclassified_filter_hides_classified_row(tmp_path):
    from lib.incident_classify import list_records  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [
        json.dumps(_rec("r1", "p1", "s1", classification="unknown")),
        json.dumps(_rec("r2", "p1", "s1", classification="gate-FP")),
    ])

    exit_code, stdout, _stderr = list_records(log_path, unclassified=True)
    assert exit_code == 0
    out_lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(out_lines) == 1, f"AC7 FAIL: unclassified filter expected 1 row, got {len(out_lines)}"
    assert json.loads(out_lines[0])["classification"] == "unknown"


def test_ac7_list_records_missing_file_exits_0_empty_stdout(tmp_path):
    from lib.incident_classify import list_records  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "does_not_exist.jsonl"
    exit_code, stdout, _stderr = list_records(log_path)
    assert exit_code == 0, f"AC7 FAIL: missing ledger for list must be exit 0, got {exit_code}"
    assert stdout == "", "AC7 FAIL: missing ledger for list must produce empty stdout"


# ─── AC8: env seam — BD_INCIDENT_LOG honored when --path omitted ────────────

def test_ac8_main_honors_env_seam_when_path_omitted(tmp_path, monkeypatch):
    from lib.incident_classify import main  # noqa: PLC0415

    seam_path = Path(tmp_path).resolve() / "seam" / "incidents.jsonl"
    seam_path.parent.mkdir(parents=True, exist_ok=True)
    _write_ledger(seam_path, [json.dumps(_rec("r1", "p1", "s1"))])

    # Set env AFTER import (call-time resolution, mirrors test_incident_ledger AC5).
    monkeypatch.setenv("BD_INCIDENT_LOG", str(seam_path))

    exit_code = main(["--run", "r1", "--class", "gate-FP"])
    assert exit_code == 0, f"AC8 FAIL: expected exit 0 via env-resolved path, got {exit_code}"

    record = json.loads(seam_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["classification"] == "gate-FP", (
        "AC8 FAIL: env-pointed ledger must have been mutated by main() with no --path"
    )


# ─── AC9: read-only parent → exit 5; successful classify leaves no *.tmp ────

def test_ac9_readonly_parent_exits_5_file_unchanged(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("AC9: chmod read-only has no effect when running as root")

    ro_dir = Path(tmp_path).resolve() / "ro"
    ro_dir.mkdir()
    log_path = ro_dir / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1"))
    log_path.write_text(line0 + "\n", encoding="utf-8")

    os.chmod(ro_dir, 0o500)
    try:
        exit_code, _stdout, stderr = classify(
            log_path, run_id="r1", classification="gate-FP",
        )
        assert exit_code == 5, f"AC9 FAIL: expected exit 5 on read-only parent, got {exit_code}"
        assert stderr != "", "AC9 FAIL: stderr must explain the rewrite failure"
        assert log_path.read_text(encoding="utf-8").splitlines()[0] == line0, (
            "AC9 FAIL: original file must be byte-identical after a failed rewrite"
        )
    finally:
        os.chmod(ro_dir, 0o700)


def test_ac9_successful_classify_leaves_no_tmp_files(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1"))])

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", classification="gate-FP",
    )
    assert exit_code == 0
    leftovers = [p for p in log_path.parent.iterdir() if ".tmp" in p.name]
    assert leftovers == [], f"AC9 FAIL: expected no *.tmp leftovers, found {leftovers!r}"


# ─── AC10: §1f entry-point shape + real subprocess smoke ────────────────────

def test_ac10_entry_point_imports_main_no_local_defs():
    entry_path = HERE.parent / "classify_incident.py"
    source = entry_path.read_text(encoding="utf-8")
    assert "from lib.incident_classify import main" in source, (
        "AC10 FAIL: classify_incident.py must import main from lib.incident_classify"
    )
    assert "def " not in source, (
        "AC10 FAIL: classify_incident.py must contain no helper bodies (§1f) — found 'def '"
    )


def test_ac10_entry_point_subprocess_list_smoke(tmp_path):
    entry_path = HERE.parent / "classify_incident.py"
    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1"))])

    result = subprocess.run(
        [sys.executable, str(entry_path), "--list", "--path", str(log_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"AC10 FAIL: subprocess exit expected 0, got {result.returncode}, stderr={result.stderr!r}"
    )
    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(out_lines) == 1, f"AC10 FAIL: expected 1 record on stdout, got {out_lines!r}"
    assert json.loads(out_lines[0])["run_id"] == "r1"


# ─── AC11: filters narrow correctly (cycle int, step_name) ──────────────────

def test_ac11_match_indices_cycle_and_step_name_filters_narrow():
    from lib.incident_classify import match_indices  # noqa: PLC0415

    entries = [
        (json.dumps(_rec("r1", "p1", "s1", cycle=1)), _rec("r1", "p1", "s1", cycle=1)),
        (json.dumps(_rec("r1", "p1", "s1", cycle=2)), _rec("r1", "p1", "s1", cycle=2)),
    ]
    idxs = match_indices(entries, run_id="r1", step_name="s1", cycle=2)
    assert idxs == [1], f"AC11 FAIL: cycle=2 filter expected [1], got {idxs!r}"


def test_ac11_match_indices_via_load_entries_round_trip(tmp_path):
    from lib.incident_classify import load_entries, match_indices  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    line0 = json.dumps(_rec("r1", "p1", "s1", cycle=1))
    line1 = json.dumps(_rec("r1", "p1", "s1", cycle=2))
    _write_ledger(log_path, [line0, line1])

    entries = load_entries(log_path)
    assert len(entries) == 2, f"AC11 FAIL: load_entries expected 2 entries, got {len(entries)}"

    idxs = match_indices(entries, run_id="r1", step_name="s1", cycle=1)
    assert idxs == [0], (
        f"AC11 FAIL: a record differing only in cycle must not be matched, got {idxs!r}"
    )


# ─── AC13: byte-identity of untouched lines + O_APPEND glue regression pin ──

def test_ac13_byte_identity_only_target_line_changes(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [
        json.dumps(_rec("r1", "p1", "s1")),
        json.dumps(_rec("r1", "p2", "s2")),
        json.dumps(_rec("r2", "p1", "s1")),
    ])
    original = log_path.read_bytes()

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", phase="p2", classification="gate-FP",
    )
    assert exit_code == 0, f"AC13 FAIL: expected exit 0, got {exit_code}"

    new_bytes = log_path.read_bytes()
    orig_parts = original.split(b"\n")
    new_parts = new_bytes.split(b"\n")
    assert len(orig_parts) == len(new_parts), (
        f"AC13 FAIL: line count changed, orig={len(orig_parts)} new={len(new_parts)}"
    )
    for i, (orig_part, new_part) in enumerate(zip(orig_parts, new_parts)):
        if i == 1:
            continue  # target line — expected to change
        assert orig_part == new_part, f"AC13 FAIL: non-target byte segment {i} changed"
    assert new_bytes.endswith(b"\n"), "AC13 FAIL: rewritten ledger must end with a trailing newline"


def test_ac13_oappend_glue_after_classify_every_line_still_parses(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415
    from lib.incident_ledger import emit_incident  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [
        json.dumps(_rec("r1", "p1", "s1")),
        json.dumps(_rec("r1", "p2", "s2")),
    ])

    exit_code, _stdout, _stderr = classify(
        log_path, run_id="r1", phase="p2", classification="gate-FP",
    )
    assert exit_code == 0
    lines_before = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    emit_incident(
        run_id="rX", phase="p", step_name="s", cycle=1, status="error",
        error_code="E_G", error=None, wall_ms=1, path=log_path,
    )

    lines_after = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines_after) == len(lines_before) + 1, (
        f"AC13 FAIL: expected line count to grow by exactly 1, "
        f"before={len(lines_before)} after={len(lines_after)}"
    )
    for ln in lines_after:
        json.loads(ln)  # AC13 FAIL if this raises: O_APPEND glue corrupted a line


# ─── AC12: §1ab idempotent re-entry — same classify twice ───────────────────

def test_ac12_repeat_classify_same_args_idempotent(tmp_path):
    from lib.incident_classify import classify  # noqa: PLC0415

    log_path = Path(tmp_path).resolve() / "incidents.jsonl"
    _write_ledger(log_path, [json.dumps(_rec("r1", "p1", "s1"))])

    exit1, _stdout1, _stderr1 = classify(
        log_path, run_id="r1", classification="gate-FP", workaround="w1",
    )
    assert exit1 == 0, f"AC12 FAIL: first classify expected exit 0, got {exit1}"
    content_after_run1 = log_path.read_text(encoding="utf-8")

    exit2, _stdout2, _stderr2 = classify(
        log_path, run_id="r1", classification="gate-FP", workaround="w1",
    )
    assert exit2 == 0, f"AC12 FAIL: second (re-entrant) classify expected exit 0, got {exit2}"
    content_after_run2 = log_path.read_text(encoding="utf-8")

    assert content_after_run1 == content_after_run2, (
        "AC12 FAIL: re-entrant classify with identical args must leave the file "
        "byte-identical to the first run's result"
    )
