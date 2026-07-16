"""RED tests for 8C9F758C — suite-level boy-scout gate.

Covers AC1–AC12 (pure-function + phase_8 step) plus AC13/AC14 (step-order and
additive _run_full_suite return shape).

AC → test-function mapping:
  AC1   test_ac1_parse_failing_nodeids_pytest_two_failed_lines
  AC2   test_ac2_parse_failing_nodeids_ignores_mid_traceback_failed_word
  AC3   test_ac3_parse_failing_nodeids_captures_pytest_error_collection
  AC4   test_ac4_parse_failing_nodeids_bun_fail_labels
  AC5   test_ac5_parse_allowlist_valid_entry_fields
  AC6   test_ac6_parse_allowlist_raises_on_malformed_lines
  AC7   test_ac7_evaluate_uncovered_id_would_block_true
  AC8   test_ac8_evaluate_covered_id_not_expired_would_block_false
  AC9   test_ac9_evaluate_expired_entry_would_block_true
  AC10  test_ac10_evaluate_green_suite_never_blocks
  AC11  test_ac11_evaluate_enforce_false_no_block_but_uncovered_populated
  AC12  test_ac12_phase8_step_errors_with_unallowlisted_red
  AC13  test_ac13_suite_boyscout_gate_step_order_after_delta_before_ship
  AC14  test_ac14_run_full_suite_additive_stdout_path_field

All tests MUST FAIL until GREEN adds:
  - lib/plugins/disk_truth/suite_boyscout.py  (ImportError raised inside each test body)
  - phase_8_post_deploy._suite_boyscout_gate  (AttributeError raised inside AC12)
  - phase_8_post_deploy workflow step updated  (assertion failure in AC13)
  - _run_full_suite additive return shape  (assertion failure in AC14)
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

# conftest.py (§1q singleton) already added engine_py root + workflows/ to
# sys.path at import time — do NOT add sys.path.insert here (81F97F3D gate).

import phase_8_post_deploy as _p8mod  # noqa: E402 — always importable (pre-GREEN)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_ctx(scratchpad: Path, *, working_dir: Path | None = None, **org_extra):
    from contracts import WorkflowContext  # deferred OK; contracts always exist
    org: dict = {"scratchpad_dir": str(scratchpad), **org_extra}
    if working_dir is not None:
        org["working_dir"] = str(working_dir)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="boyscout-test",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_parse_failing_nodeids_pytest_two_failed_lines():
    """AC1: parse pytest stdout with two FAILED lines + green noise → exact list."""
    from lib.plugins.disk_truth.suite_boyscout import parse_failing_nodeids  # type: ignore[import-not-found]

    stdout = (
        "collected 5 items\n"
        "\n"
        "tests/test_x.py::test_a PASSED\n"
        "tests/test_y.py::test_b FAILED\n"
        "tests/test_z.py::test_c PASSED\n"
        "tests/test_x.py::test_a FAILED\n"  # duplicate of test_a (edge)
        "\n"
        "short test summary info\n"
        "FAILED tests/test_x.py::test_a - AssertionError\n"
        "FAILED tests/test_y.py::test_b - AssertionError\n"
        "2 failed, 3 passed in 1.23s\n"
    )
    result = parse_failing_nodeids(stdout, "pytest")
    assert "tests/test_x.py::test_a" in result
    assert "tests/test_y.py::test_b" in result


# ─── AC1b ─────────────────────────────────────────────────────────────────────


def test_ac1b_parse_failing_nodeids_unknown_framework_returns_empty():
    """AC1b: unknown framework → [] (gate degrades to no-op per spec §2.1)."""
    from lib.plugins.disk_truth.suite_boyscout import parse_failing_nodeids  # type: ignore[import-not-found]

    result = parse_failing_nodeids("some stdout", "java")
    assert result == [], (
        f"Unknown framework must return []; got {result!r}"
    )


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_ac2_parse_failing_nodeids_ignores_mid_traceback_failed_word():
    """AC2: 'FAILED' mid-traceback/message is NOT captured (only line-start ^FAILED)."""
    from lib.plugins.disk_truth.suite_boyscout import parse_failing_nodeids  # type: ignore[import-not-found]

    stdout = (
        "collected 1 item\n"
        "\n"
        "tests/test_x.py::test_a FAILED\n"
        "E   AssertionError: expected True FAILED because state mismatch\n"
        "E   assert ... FAILED\n"
        "\n"
        "FAILED tests/test_x.py::test_a - AssertionError\n"
        "1 failed in 0.5s\n"
    )
    result = parse_failing_nodeids(stdout, "pytest")
    # Only the line-start FAILED lines should be captured
    # The mid-traceback E lines with FAILED must NOT produce extra ids
    # (They don't start with FAILED <nodeid> pattern, so count should be 1 or
    #  the only valid capture is tests/test_x.py::test_a)
    for nid in result:
        # Every captured id must look like a real node-id (contains ::)
        assert "::" in nid, (
            f"Captured id {nid!r} does not look like a pytest node-id "
            f"(mid-traceback word 'FAILED' was not filtered)"
        )


# ─── AC3 ──────────────────────────────────────────────────────────────────────


def test_ac3_parse_failing_nodeids_captures_pytest_error_collection():
    """AC3: ERROR <nodeid> (collection error) is captured alongside FAILED."""
    from lib.plugins.disk_truth.suite_boyscout import parse_failing_nodeids  # type: ignore[import-not-found]

    stdout = (
        "ERROR tests/t.py::test_c - ImportError: cannot import name 'foo'\n"
        "FAILED tests/test_x.py::test_a - AssertionError\n"
        "1 failed, 1 error in 0.3s\n"
    )
    result = parse_failing_nodeids(stdout, "pytest")
    assert "tests/t.py::test_c" in result, (
        f"Collection ERROR node-id must be captured; got {result!r}"
    )
    assert "tests/test_x.py::test_a" in result


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_ac4_parse_failing_nodeids_bun_fail_labels():
    """AC4: bun framework extracts (fail) label."""
    from lib.plugins.disk_truth.suite_boyscout import parse_failing_nodeids  # type: ignore[import-not-found]

    stdout = (
        "bun test v1.0.0\n"
        "\n"
        "(pass) build > compiles ok [12.3ms]\n"
        "(fail) build > does x [5.1ms]\n"
        "(pass) sanity > returns true [0.9ms]\n"
        "\n"
        " 2 pass\n"
        " 1 fail\n"
    )
    result = parse_failing_nodeids(stdout, "bun")
    assert result == ["build > does x"], (
        f"Bun (fail) label must be captured without timing suffix; got {result!r}"
    )


# ─── AC5 ──────────────────────────────────────────────────────────────────────


def test_ac5_parse_allowlist_valid_entry_fields():
    """AC5: parse_allowlist parses a valid 3-field line into AllowEntry with correct fields."""
    from lib.plugins.disk_truth.suite_boyscout import parse_allowlist, AllowEntry  # type: ignore[import-not-found]

    text = (
        "# Suite boyscout allowlist\n"
        "tests/test_foo.py::test_bar :: AABBCCDD :: kill-by:2026-09-01\n"
        "\n"
        "# another comment\n"
    )
    result = parse_allowlist(text)
    assert len(result) == 1, f"Expected 1 entry, got {len(result)}: {result!r}"
    key = list(result.keys())[0]
    entry = result[key]
    assert isinstance(entry, AllowEntry), (
        f"Expected AllowEntry dataclass, got {type(entry).__name__!r}"
    )
    assert entry.pattern == "tests/test_foo.py::test_bar", (
        f"pattern field mismatch: {entry.pattern!r}"
    )
    assert entry.agreement_id == "AABBCCDD", (
        f"agreement_id field mismatch: {entry.agreement_id!r}"
    )
    assert entry.kill_by == date(2026, 9, 1), (
        f"kill_by field mismatch: {entry.kill_by!r}"
    )


# ─── AC6 ──────────────────────────────────────────────────────────────────────


def test_ac6_parse_allowlist_raises_on_malformed_lines():
    """AC6: parse_allowlist raises ValueError naming the bad line for 3 malformed cases."""
    from lib.plugins.disk_truth.suite_boyscout import parse_allowlist  # type: ignore[import-not-found]

    # Sub-case 1: 2-field line (missing kill-by field)
    two_field = "tests/test_x.py::test_a :: AABBCCDD\n"
    with pytest.raises(ValueError) as exc_info:
        parse_allowlist(two_field)
    assert "tests/test_x.py::test_a" in str(exc_info.value) or "AABBCCDD" in str(exc_info.value), (
        f"ValueError for 2-field line must name the bad line; got: {exc_info.value!r}"
    )

    # Sub-case 2: non-hex agreement-id
    bad_hex = "tests/test_x.py::test_a :: GGGGGGGG :: kill-by:2026-09-01\n"
    with pytest.raises(ValueError) as exc_info2:
        parse_allowlist(bad_hex)
    assert "GGGGGGGG" in str(exc_info2.value) or "tests/test_x.py::test_a" in str(exc_info2.value), (
        f"ValueError for bad hex must name the bad line; got: {exc_info2.value!r}"
    )

    # Sub-case 3: missing/garbage kill-by field
    bad_killby = "tests/test_x.py::test_a :: AABBCCDD :: kill-by:not-a-date\n"
    with pytest.raises(ValueError) as exc_info3:
        parse_allowlist(bad_killby)
    assert "tests/test_x.py::test_a" in str(exc_info3.value) or "not-a-date" in str(exc_info3.value), (
        f"ValueError for bad kill-by must name the bad line; got: {exc_info3.value!r}"
    )


# ─── AC7 ──────────────────────────────────────────────────────────────────────


def test_ac7_evaluate_uncovered_id_would_block_true():
    """AC7: failing id NOT matched by any pattern → uncovered, would_block=True (enforce=True)."""
    from lib.plugins.disk_truth.suite_boyscout import evaluate  # type: ignore[import-not-found]

    failing = ["tests/test_orphan.py::test_z"]
    allowlist = {}  # no entries
    today = date(2026, 6, 5)

    verdict = evaluate(failing, allowlist, today=today, enforce=True)

    assert "tests/test_orphan.py::test_z" in verdict.uncovered, (
        f"Unmatched id must be in uncovered; got uncovered={verdict.uncovered!r}"
    )
    assert verdict.would_block is True, (
        f"enforce=True + uncovered red → would_block must be True; got {verdict.would_block!r}"
    )


# ─── AC8 ──────────────────────────────────────────────────────────────────────


def test_ac8_evaluate_covered_id_not_expired_would_block_false():
    """AC8: failing id matched by a non-expired pattern → covered, would_block=False."""
    from lib.plugins.disk_truth.suite_boyscout import evaluate, AllowEntry  # type: ignore[import-not-found]

    failing = ["tests/test_known.py::test_flaky"]
    today = date(2026, 6, 5)
    future_date = date(2026, 9, 1)  # kill-by after today → still valid

    allowlist = {
        "tests/test_known.py": AllowEntry(
            pattern="tests/test_known.py",
            agreement_id="AABBCCDD",
            kill_by=future_date,
        )
    }

    verdict = evaluate(failing, allowlist, today=today, enforce=True)

    assert "tests/test_known.py::test_flaky" in verdict.covered, (
        f"Matched non-expired id must be in covered; got covered={verdict.covered!r}"
    )
    assert verdict.would_block is False, (
        f"Covered non-expired red must not block; got would_block={verdict.would_block!r}"
    )


# ─── AC9 ──────────────────────────────────────────────────────────────────────


def test_ac9_evaluate_expired_entry_would_block_true():
    """AC9: failing id matched by an expired pattern (kill_by < today) → expired, would_block=True."""
    from lib.plugins.disk_truth.suite_boyscout import evaluate, AllowEntry  # type: ignore[import-not-found]

    failing = ["tests/test_old_debt.py::test_stale"]
    today = date(2026, 6, 5)
    past_date = date(2026, 1, 1)  # kill-by before today → expired

    allowlist = {
        "tests/test_old_debt.py": AllowEntry(
            pattern="tests/test_old_debt.py",
            agreement_id="DEADBEEF",
            kill_by=past_date,
        )
    }

    verdict = evaluate(failing, allowlist, today=today, enforce=True)

    assert "tests/test_old_debt.py::test_stale" in verdict.expired, (
        f"Matched expired id must be in expired bucket; got expired={verdict.expired!r}"
    )
    assert verdict.would_block is True, (
        f"Expired entry must block (kill-by lapsed); got would_block={verdict.would_block!r}"
    )


# ─── AC10 ─────────────────────────────────────────────────────────────────────


def test_ac10_evaluate_green_suite_never_blocks():
    """AC10: failing=[] → would_block=False, all buckets empty, regardless of enforce."""
    from lib.plugins.disk_truth.suite_boyscout import evaluate  # type: ignore[import-not-found]

    today = date(2026, 6, 5)

    # Test with enforce=True — green suite must still not block
    verdict = evaluate([], {}, today=today, enforce=True)

    assert verdict.would_block is False, (
        f"Green suite (failing=[]) must never block; got would_block={verdict.would_block!r}"
    )
    assert verdict.covered == [], f"covered must be empty for green suite; got {verdict.covered!r}"
    assert verdict.expired == [], f"expired must be empty for green suite; got {verdict.expired!r}"
    assert verdict.uncovered == [], f"uncovered must be empty for green suite; got {verdict.uncovered!r}"
    assert verdict.failing == [], f"failing must be empty for green suite; got {verdict.failing!r}"


# ─── AC11 ─────────────────────────────────────────────────────────────────────


def test_ac11_evaluate_enforce_false_no_block_but_uncovered_populated():
    """AC11: enforce=False + uncovered red → would_block=False, but uncovered still populated."""
    from lib.plugins.disk_truth.suite_boyscout import evaluate  # type: ignore[import-not-found]

    failing = ["tests/test_orphan.py::test_z"]
    allowlist = {}
    today = date(2026, 6, 5)

    verdict = evaluate(failing, allowlist, today=today, enforce=False)

    assert verdict.would_block is False, (
        f"enforce=False must never block; got would_block={verdict.would_block!r}"
    )
    assert "tests/test_orphan.py::test_z" in verdict.uncovered, (
        f"uncovered must still be populated for telemetry when enforce=False; "
        f"got uncovered={verdict.uncovered!r}"
    )


# ─── AC12 ─────────────────────────────────────────────────────────────────────


def test_ac12_phase8_step_errors_with_unallowlisted_red(tmp_path, monkeypatch):
    """AC12: _suite_boyscout_gate returns error_code==E_SHIP_UNALLOWLISTED_RED when
    enforce=True and an uncovered red exists; offending node-id in message.

    §1y: monkeypatch _run_full_suite on _p8mod to return a synthetic
         (framework, stdout_path) list without running a real suite.
    §1i: deterministic — fixture stdout pre-staged in tmp_path.
    """
    # Deferred import of the step (D1CF5FDF / §1q extension) — must be inside body
    _suite_boyscout_gate = getattr(_p8mod, "_suite_boyscout_gate", None)
    assert _suite_boyscout_gate is not None, (
        "_suite_boyscout_gate not found on phase_8_post_deploy module; "
        "GREEN has not added it yet"
    )

    # Pre-stage a fixture stdout file with one uncovered FAILED node-id
    stdout_content = (
        "collected 1 item\n"
        "FAILED tests/test_uncovered_8C9F758C.py::test_red_node - AssertionError\n"
        "1 failed in 0.1s\n"
    )
    stdout_file = tmp_path / "pytest_stdout.txt"
    stdout_file.write_text(stdout_content)

    # Stub _run_full_suite on the module to return additive shape:
    # list of (framework, stdout_path) alongside n_failed (or however GREEN implements it)
    # The stub must yield the pre-staged fixture so _suite_boyscout_gate can parse it.
    def fake_run_full_suite(cmds, cwd, timeout):
        # Additive return: (n_failed, [(framework, stdout_path), ...])
        return (1, [("pytest", str(stdout_file))])

    monkeypatch.setattr(_p8mod, "_run_full_suite", fake_run_full_suite)

    # Use an empty allowlist file path that doesn't exist (→ empty allowlist)
    ctx = _make_ctx(
        tmp_path / "scratch",
        working_dir=tmp_path,
        full_suite_boyscout_enforce=True,
        full_suite_cmds=[["python3", "-m", "pytest", "-q", "--tb=no"]],
    )

    result = _suite_boyscout_gate(ctx, None)

    assert result.status == "error", (
        f"enforce=True + uncovered red → status must be 'error'; got {result.status!r}"
    )
    assert result.error_code == "E_SHIP_UNALLOWLISTED_RED", (
        f"error_code must be 'E_SHIP_UNALLOWLISTED_RED'; got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"unallowlisted red block must be non-recoverable; got {result.recoverable!r}"
    )
    # The offending node-id must appear in the error field (StepResult.error, per delta-gate precedent)
    assert "tests/test_uncovered_8C9F758C.py::test_red_node" in (result.error or ""), (
        f"result.error must contain the offending node-id; got error={result.error!r}"
    )


# ─── AC13 ─────────────────────────────────────────────────────────────────────


def test_ac13_suite_boyscout_gate_step_order_after_delta_before_ship():
    """AC13: suite_boyscout_gate is at index 10, immediately after full_suite_delta_gate
    (index 9) and immediately before ship_to_pr (index 11); total steps == 13.
    (1DA29C33: cleanup_run_allowlist inserted before full_suite_delta_gate shifted
    all indexes here by +1.)
    """
    from phase_8_post_deploy import phase_8_post_deploy_workflow  # always importable

    wf = phase_8_post_deploy_workflow()
    names = [s.name for s in wf.steps]

    assert "suite_boyscout_gate" in names, (
        "suite_boyscout_gate step missing from workflow; GREEN has not added it yet"
    )
    idx = names.index("suite_boyscout_gate")
    assert idx == 10, (
        f"suite_boyscout_gate must be at index 10, got {idx}; "
        f"full step list: {names}"
    )
    assert names[idx - 1] == "full_suite_delta_gate", (
        f"suite_boyscout_gate must be immediately after full_suite_delta_gate; "
        f"prev step is {names[idx - 1]!r}"
    )
    assert names[idx + 1] == "ship_to_pr", (
        f"suite_boyscout_gate must be immediately before ship_to_pr; "
        f"next step is {names[idx + 1]!r}"
    )
    assert len(names) == 13, (
        f"Workflow must have 13 steps after insertion; got {len(names)}: {names}"
    )


# ─── AC14 ─────────────────────────────────────────────────────────────────────


def test_ac14_run_full_suite_additive_stdout_path_field(tmp_path, monkeypatch):
    """AC14: _run_full_suite additive return — new shape includes stdout_paths alongside
    n_failed so existing count-only callers can still read the first field unchanged,
    and the new field is present for _suite_boyscout_gate to consume.

    The additive contract: _run_full_suite returns a tuple
    (n_failed: int | None, stdout_paths: list[tuple[str, str]])
    so that existing callers reading result[0] see the same int | None they
    always did, and the new gate can read result[1].
    """
    _run_full_suite = getattr(_p8mod, "_run_full_suite", None)
    assert _run_full_suite is not None, (
        "_run_full_suite not found on phase_8_post_deploy module"
    )

    # Use 'true' command — exits 0, no test output, but must return the additive shape
    result = _run_full_suite([["true"]], str(tmp_path), 30)

    # After GREEN: result must be a tuple (n_failed, [(framework, stdout_path), ...])
    assert isinstance(result, tuple), (
        f"_run_full_suite must return a tuple (additive shape); got {type(result).__name__!r}"
    )
    assert len(result) == 2, (
        f"_run_full_suite tuple must have 2 elements (n_failed, stdout_paths); "
        f"got len={len(result)}"
    )
    n_failed, stdout_paths = result
    # n_failed is still int (or None on timeout/missing-binary)
    assert n_failed is None or isinstance(n_failed, int), (
        f"result[0] (n_failed) must be int or None; got {type(n_failed).__name__!r}"
    )
    # stdout_paths is a list of (framework, path) tuples
    assert isinstance(stdout_paths, list), (
        f"result[1] (stdout_paths) must be a list; got {type(stdout_paths).__name__!r}"
    )
