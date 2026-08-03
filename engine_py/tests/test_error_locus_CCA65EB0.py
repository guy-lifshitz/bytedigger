"""RED tests for error-locus stats analyzer — CCA65EB0.

Units under test:
  - error_locus.py (lib): aggregate(events, window_days=None), classify_error(error_code)
  - error-locus-stats.py (CLI at build/, sibling of reject-reason-stats.py)

All 12 tests MUST FAIL in RED because:
  - error_locus.py does not exist yet (ImportError inside body)
  - error-locus-stats.py does not exist yet (subprocess non-zero / missing file)

Collectability guard (D1CF5FDF §1q-ext): imports of the not-yet-existing module
are deferred to inside each test body so collection succeeds and every test is
individually reported as FAIL rather than the whole module failing to collect.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# CLI lives at build/error-locus-stats.py — parents[2] from this file:
#   parents[0] = tests/
#   parents[1] = engine_py/
#   parents[2] = build/
_CLI = Path(__file__).resolve().parents[2] / "error-locus-stats.py"

# ── shared event fixtures ──────────────────────────────────────────────────────

def _ft(run_id: str, paths: list[str], ts: str = "2026-06-16T10:00:00Z") -> dict:
    """Build a files_touched event."""
    return {
        "ts": ts,
        "run_id": run_id,
        "event_type": "files_touched",
        "payload": {
            "phase": "phase_5",
            "step_name": "implement",
            "paths": paths,
            "by_status": {"modified": paths, "added": []},
        },
    }


def _retry(run_id: str, error_code: str, ts: str = "2026-06-16T10:00:00Z") -> dict:
    """Build a phase_retry_triggered event."""
    return {
        "ts": ts,
        "run_id": run_id,
        "event_type": "phase_retry_triggered",
        "payload": {
            "phase_name": "phase_5",
            "step_name": "implement",
            "cycle": 2,
            "error_code": error_code,
            "findings_bytes": 100,
        },
    }


def _findings(run_id: str, aggregated: int, ts: str = "2026-06-16T10:00:00Z") -> dict:
    """Build a review_findings_audit event."""
    return {
        "ts": ts,
        "run_id": run_id,
        "event_type": "review_findings_audit",
        "payload": {"aggregated": aggregated},
    }


# ── AC1 ───────────────────────────────────────────────────────────────────────


def test_ac1_aggregate_empty_returns_empty_list():
    """AC1: aggregate([]) returns []."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    result = aggregate([])
    assert result == [], f"Expected [] for empty input, got {result!r}"


# ── AC2 ───────────────────────────────────────────────────────────────────────


def test_ac2_one_build_two_files_both_present_touches_1_builds_1():
    """AC2: one build with files_touched paths=['a.py','b.py'] → both files in result,
    each with touches==1 and builds==1."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [_ft("b1", ["a.py", "b.py"])]
    result = aggregate(events)
    files = {row["file"]: row for row in result}
    assert "a.py" in files, f"Expected 'a.py' in result files, got {list(files.keys())!r}"
    assert "b.py" in files, f"Expected 'b.py' in result files, got {list(files.keys())!r}"
    for f in ("a.py", "b.py"):
        assert files[f]["touches"] == 1, (
            f"Expected {f} touches==1, got {files[f].get('touches')!r}"
        )
        assert files[f]["builds"] == 1, (
            f"Expected {f} builds==1, got {files[f].get('builds')!r}"
        )


# ── AC3 ───────────────────────────────────────────────────────────────────────


def test_ac3_same_file_two_distinct_run_ids_builds_equals_2():
    """AC3: same file path touched in two distinct run_ids → builds==2."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        _ft("b1", ["shared.py"]),
        _ft("b2", ["shared.py"]),
    ]
    result = aggregate(events)
    files = {row["file"]: row for row in result}
    assert "shared.py" in files, (
        f"Expected 'shared.py' in result, got {list(files.keys())!r}"
    )
    assert files["shared.py"]["builds"] == 2, (
        f"Expected builds==2 for shared.py touched in two run_ids, "
        f"got {files['shared.py'].get('builds')!r}"
    )


# ── AC4 ───────────────────────────────────────────────────────────────────────


def test_ac4_same_file_two_events_same_run_id_touches_2_builds_1():
    """AC4: same file in two files_touched events under the SAME run_id → touches==2, builds==1."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        _ft("b1", ["x.py"]),
        _ft("b1", ["x.py"]),
    ]
    result = aggregate(events)
    files = {row["file"]: row for row in result}
    assert "x.py" in files, f"Expected 'x.py' in result, got {list(files.keys())!r}"
    assert files["x.py"]["touches"] == 2, (
        f"Expected x.py touches==2 (two events, same run_id), "
        f"got {files['x.py'].get('touches')!r}"
    )
    assert files["x.py"]["builds"] == 1, (
        f"Expected x.py builds==1 (same run_id), got {files['x.py'].get('builds')!r}"
    )


# ── AC5 ───────────────────────────────────────────────────────────────────────


def test_ac5_retry_incidence_present_absent():
    """AC5: a build with phase_retry_triggered touching file X → X retry_incidence==1;
    a separate build with NO retry touching file Y → Y retry_incidence==0."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        _ft("b_retry", ["x.py"]),
        _retry("b_retry", "E_SPEC_COVERAGE"),
        _ft("b_clean", ["y.py"]),
    ]
    result = aggregate(events)
    files = {row["file"]: row for row in result}
    assert "x.py" in files, f"Expected 'x.py' in result, got {list(files.keys())!r}"
    assert "y.py" in files, f"Expected 'y.py' in result, got {list(files.keys())!r}"
    assert files["x.py"]["retry_incidence"] == 1, (
        f"Expected x.py retry_incidence==1, got {files['x.py'].get('retry_incidence')!r}"
    )
    assert files["y.py"]["retry_incidence"] == 0, (
        f"Expected y.py retry_incidence==0 (no retry in its build), "
        f"got {files['y.py'].get('retry_incidence')!r}"
    )


# ── AC6 ───────────────────────────────────────────────────────────────────────


def test_ac6_finding_incidence_present_absent():
    """AC6: a build with review_findings_audit aggregated>0 touching file → finding_incidence==1;
    a build with aggregated==0 touching another file → finding_incidence==0."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        _ft("b_findings", ["hot.py"]),
        _findings("b_findings", 5),
        _ft("b_clean", ["cold.py"]),
        _findings("b_clean", 0),
    ]
    result = aggregate(events)
    files = {row["file"]: row for row in result}
    assert "hot.py" in files, f"Expected 'hot.py' in result, got {list(files.keys())!r}"
    assert "cold.py" in files, f"Expected 'cold.py' in result, got {list(files.keys())!r}"
    assert files["hot.py"]["finding_incidence"] == 1, (
        f"Expected hot.py finding_incidence==1 (aggregated=5), "
        f"got {files['hot.py'].get('finding_incidence')!r}"
    )
    assert files["cold.py"]["finding_incidence"] == 0, (
        f"Expected cold.py finding_incidence==0 (aggregated=0), "
        f"got {files['cold.py'].get('finding_incidence')!r}"
    )


# ── AC7 ───────────────────────────────────────────────────────────────────────


def test_ac7_dominant_error_class_most_frequent():
    """AC7: file touched across builds whose retry error_codes are ['E_A','E_A','E_B']
    → that file's dominant_error_class=='E_A' (most frequent)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        _ft("b1", ["shared.py"]),
        _retry("b1", "E_A"),
        _ft("b2", ["shared.py"]),
        _retry("b2", "E_A"),
        _ft("b3", ["shared.py"]),
        _retry("b3", "E_B"),
    ]
    result = aggregate(events)
    files = {row["file"]: row for row in result}
    assert "shared.py" in files, (
        f"Expected 'shared.py' in result, got {list(files.keys())!r}"
    )
    assert files["shared.py"]["dominant_error_class"] == "E_A", (
        f"Expected dominant_error_class=='E_A' (appears twice vs E_B once), "
        f"got {files['shared.py'].get('dominant_error_class')!r}"
    )


# ── AC8 ───────────────────────────────────────────────────────────────────────


def test_ac8_ranking_more_touches_before_fewer():
    """AC8: file with more touches appears before file with fewer touches.
    hot.py has touches=3, cold.py has touches=1 → result[0]['file']=='hot.py'."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        _ft("b1", ["hot.py"]),
        _ft("b2", ["hot.py"]),
        _ft("b3", ["hot.py"]),
        _ft("b4", ["cold.py"]),
    ]
    result = aggregate(events)
    assert len(result) >= 2, f"Expected at least 2 rows in result, got {result!r}"
    assert result[0]["file"] == "hot.py", (
        f"Expected hot.py (touches=3) to appear first, "
        f"got result[0]['file']=={result[0].get('file')!r}"
    )


# ── AC9 ───────────────────────────────────────────────────────────────────────


def test_ac9_window_days_drops_older_build_file():
    """AC9: window_days=1 with one recent build (2026-06-16) and one old build (2026-06-11,
    5 days earlier) → the older build's file is dropped; only the recent file remains."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    recent_ts = "2026-06-16T10:00:00Z"
    old_ts = "2026-06-11T10:00:00Z"
    events = [
        _ft("b_recent", ["recent.py"], ts=recent_ts),
        _ft("b_old", ["old_only.py"], ts=old_ts),
    ]
    result = aggregate(events, window_days=1)
    files = {row["file"]: row for row in result}
    assert "old_only.py" not in files, (
        f"Expected 'old_only.py' (5 days old) to be absent with window_days=1, "
        f"but found it in {list(files.keys())!r}"
    )
    assert "recent.py" in files, (
        f"Expected 'recent.py' to be present with window_days=1, "
        f"got {list(files.keys())!r}"
    )


# ── AC10 ──────────────────────────────────────────────────────────────────────


def test_ac10_module_field_posix_dirname():
    """AC10: module field equals POSIX dirname.
    'bark/core/x.py' → module=='bark/core'; bare 'x.py' → module==''."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        _ft("b1", ["bark/core/x.py", "x.py"]),
    ]
    result = aggregate(events)
    files = {row["file"]: row for row in result}
    assert "bark/core/x.py" in files, (
        f"Expected 'bark/core/x.py' in result, got {list(files.keys())!r}"
    )
    assert files["bark/core/x.py"]["module"] == "bark/core", (
        f"Expected module=='bark/core' for 'bark/core/x.py', "
        f"got {files['bark/core/x.py'].get('module')!r}"
    )
    assert "x.py" in files, (
        f"Expected 'x.py' in result, got {list(files.keys())!r}"
    )
    assert files["x.py"]["module"] == "", (
        f"Expected module=='' for bare 'x.py', got {files['x.py'].get('module')!r}"
    )


# ── AC11 ──────────────────────────────────────────────────────────────────────


def test_ac11_classify_error_lintable_needs_prompt_none():
    """AC11: classify_error('E_SPEC_COVERAGE')=='lintable';
    classify_error('E_FOO')=='needs-prompt'; classify_error(None)=='needs-prompt'."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import classify_error  # ImportError until GREEN
    result_lintable = classify_error("E_SPEC_COVERAGE")
    assert result_lintable == "lintable", (
        f"Expected classify_error('E_SPEC_COVERAGE')=='lintable', got {result_lintable!r}"
    )
    result_needs_prompt = classify_error("E_FOO")
    assert result_needs_prompt == "needs-prompt", (
        f"Expected classify_error('E_FOO')=='needs-prompt', got {result_needs_prompt!r}"
    )
    result_none = classify_error(None)
    assert result_none == "needs-prompt", (
        f"Expected classify_error(None)=='needs-prompt', got {result_none!r}"
    )


# ── AC12 ──────────────────────────────────────────────────────────────────────


def test_ac12_millisecond_ts_does_not_raise():
    """AC12: event with millisecond ts '2026-06-16T10:00:00.123Z' must not raise
    when aggregate(events, window_days=7) is called; assert it returns a list."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bytedigger_engine.error_locus import aggregate  # ImportError until GREEN
    events = [
        {
            "ts": "2026-06-16T10:00:00.123Z",
            "run_id": "b1",
            "event_type": "files_touched",
            "payload": {
                "phase": "phase_5",
                "step_name": "implement",
                "paths": ["a.py"],
                "by_status": {"modified": ["a.py"], "added": []},
            },
        }
    ]
    result = aggregate(events, window_days=7)
    assert isinstance(result, list), (
        f"Expected aggregate() to return a list with ms-precision ts, got {type(result)!r}"
    )
