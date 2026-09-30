"""RED-phase tests for GH2007 — TimeoutExpired with exc.stderr=None must
report stderr_bytes=0, not None.

Spec: SHARED/memory/Decisions/gh2007_timeout_none_stderr_spec.md.

Chokepoint: the `except subprocess.TimeoutExpired as exc:` branch of
`_run_inject` in phase_05_inject.py (~L568-582) — the ONLY place `exc.stderr`
is normalised into `partial_stderr`. Today, when `exc.stderr is None` (the
CPython contract for a child that wrote nothing before the timeout fired),
`partial_stderr` stays `None`, so `_emit_callout_failed` reports
`stderr_bytes=None` instead of the honest `stderr_bytes=0` (process WAS
spawned and its stderr pipe WAS captured — it just produced 0 bytes, which is
a different fact than "never captured").

Fixture helpers (`_make_fixture_db`, `_write_fake_bun`, `_run_workflow`) are
imported from the sibling module `test_GH1468_inject_failure_diagnosability`
(precedent: test_gh1600_d4_stale_hash_key_reconcile.py imports
test_gh960_preexisting_tamper_downgrade as a module) rather than duplicated —
that file is read-only and untouched.

§1i / §1l: fixture DBs and fake-bun scripts are pre-staged on disk before any
workflow invocation; assertions anchor on real emitted event payloads read
back from real on-disk `events.jsonl` files.
"""
from __future__ import annotations

import subprocess

# conftest-import-time singleton installs engine_py + workflows dirs on
# sys.path. No module-level sys.path manipulation here (§1q / 81F97F3D).

from bytedigger_engine.workflows import phase_05_inject as _p05_mod  # noqa: E402 — module already exists today
import test_GH1468_inject_failure_diagnosability as _gh1468_mod  # noqa: E402

_make_fixture_db = _gh1468_mod._make_fixture_db
_write_fake_bun = _gh1468_mod._write_fake_bun
_run_workflow = _gh1468_mod._run_workflow


def _assert_empty_captured_stderr(payload: dict) -> None:
    """Shared assertion for AC1/AC2/AC4: process WAS spawned, stderr pipe WAS
    captured, and it produced 0 bytes — the honest "captured, empty" shape,
    distinct from AC3's "never captured" (None) shape.
    """
    assert payload.get("reason") == "subprocess timeout", (
        f"expected reason='subprocess timeout', got {payload.get('reason')!r}"
    )
    stderr_bytes = payload.get("stderr_bytes")
    assert stderr_bytes == 0, (
        f"expected stderr_bytes == 0 (process spawned, pipe captured, 0 bytes "
        f"written), got {stderr_bytes!r} — likely None from the untouched "
        f"'else: partial_stderr = None' branch"
    )
    assert type(stderr_bytes) is int, (
        f"stderr_bytes must be a plain int (not bool/None), got "
        f"{type(stderr_bytes).__name__}"
    )
    assert payload.get("stderr_tail") == "", (
        f"expected stderr_tail == '', got {payload.get('stderr_tail')!r}"
    )
    assert payload.get("stderr_truncated") is False, (
        f"expected stderr_truncated is False, got {payload.get('stderr_truncated')!r}"
    )


# ─── AC1: real subprocess, no output before hang → stderr_bytes=0 ─────────


def test_ac1_real_timeout_with_no_stderr_output_reports_stderr_bytes_zero(tmp_path, monkeypatch):
    """AC1 (§1l, real subprocess, no mocking of subprocess boundary).

    Fake bun writes NOTHING to stdout/stderr before sleeping 5s, real
    HAL_INJECT_TIMEOUT_S=1 fires a genuine subprocess.TimeoutExpired with
    exc.stderr=None (CPython contract: nothing was read from the pipe before
    the timeout). The last learning_inject_callout_failed event must report
    the "captured, empty" shape asserted by `_assert_empty_captured_stderr`.

    RED: today the timeout branch's `else: partial_stderr = None` (no str/
    bytes branch matched for None) makes _emit_callout_failed compute
    stderr_bytes=None, not 0 — this test fails on that assertion today.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac1-learn-1",
            "content": "LESSON: timeout with no stderr output must report zero bytes",
            "type": "learning",
            "hash": "gh2007ac1hash01",
        }],
    )
    fake_bun = _write_fake_bun(
        tmp_path, "fake_bin_ac1",
        [{"stdout": "", "stderr": "", "exit_code": 0, "sleep_s": 5}],
    )
    fake_ts = tmp_path / "fake_inject_ac1.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))
    monkeypatch.setenv("HAL_INJECT_TIMEOUT_S", "1")
    for _v in ("PYTHONVERBOSE", "PYTHONPROFILEIMPORTTIME", "PYTHONWARNINGS"):
        monkeypatch.delenv(_v, raising=False)

    log_path = tmp_path / "events_ac1.jsonl"
    _, failed, _ = _run_workflow(
        tmp_path, db_path, "AC1 real timeout no stderr output", log_path
    )

    assert failed, "expected a 'learning_inject_callout_failed' event on subprocess timeout"
    _assert_empty_captured_stderr(failed[-1]["payload"])


# ─── AC2: deterministic boundary substitution, same payload contract ──────


def test_ac2_deterministic_timeout_expired_stderr_none_reports_stderr_bytes_zero(
    tmp_path, monkeypatch
):
    """AC2 (deterministic boundary substitution, GH1468 AC8b pattern).

    Patches `phase_05_inject.subprocess.run` (module-level attribute) to
    RAISE a real `subprocess.TimeoutExpired(stderr=None)` directly — removes
    the AC1 startup-vs-timeout race entirely. Substitutes the subprocess
    boundary, not the UUT under test (the timeout-handling code in
    `_run_inject`). Same payload assertions as AC1.

    RED: same untouched 'else: partial_stderr = None' branch as AC1 —
    stderr_bytes is None today, not 0.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac2-learn-1",
            "content": "LESSON: TimeoutExpired.stderr=None deterministic case",
            "type": "learning",
            "hash": "gh2007ac2hash01",
        }],
    )
    fake_ts = tmp_path / "fake_inject_ac2.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))
    monkeypatch.setenv("HAL_INJECT_TIMEOUT_S", "1")

    def _fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv, timeout=kwargs.get("timeout", 1), output=None, stderr=None
        )

    monkeypatch.setattr(_p05_mod.subprocess, "run", _fake_run)

    log_path = tmp_path / "events_ac2.jsonl"
    _, failed, _ = _run_workflow(
        tmp_path, db_path, "AC2 deterministic TimeoutExpired stderr None", log_path
    )

    assert failed, "expected a 'learning_inject_callout_failed' event on subprocess timeout"
    _assert_empty_captured_stderr(failed[-1]["payload"])


# ─── AC3: OSError branch keeps None — "not captured" distinction guard ────


def test_ac3_oserror_branch_keeps_stderr_bytes_none_not_captured(tmp_path, monkeypatch):
    """AC3 (distinction preserved — guards against fixing in
    `_emit_callout_failed` instead of the TimeoutExpired branch).

    Patches `subprocess.run` to raise OSError directly (process never
    spawned — no pipe to ever capture). The event's reason starts with
    "subprocess error:" and stderr_bytes/stderr_tail stay None: "not
    captured" is a genuinely different fact than "captured, 0 bytes" (AC1/
    AC2), and must remain distinguishable after the GH2007 fix.

    Passes today and must still pass after GREEN (regression guard).
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac3-learn-1",
            "content": "LESSON: OSError never-spawned must keep stderr_bytes None",
            "type": "learning",
            "hash": "gh2007ac3hash01",
        }],
    )
    fake_ts = tmp_path / "fake_inject_ac3.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))
    monkeypatch.setenv("HAL_INJECT_TIMEOUT_S", "1")

    def _fake_run(argv, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(_p05_mod.subprocess, "run", _fake_run)

    log_path = tmp_path / "events_ac3.jsonl"
    _, failed, _ = _run_workflow(
        tmp_path, db_path, "AC3 OSError distinction guard", log_path
    )

    assert failed, "expected a 'learning_inject_callout_failed' event on OSError"
    payload = failed[-1]["payload"]

    reason = payload.get("reason")
    assert isinstance(reason, str) and reason.startswith("subprocess error:"), (
        f"expected reason starting with 'subprocess error:', got {reason!r}"
    )
    assert "stderr_bytes" in payload and "stderr_tail" in payload, (
        f"OSError branch must still carry both keys (value None), payload={payload!r}"
    )
    assert payload.get("stderr_bytes") is None, (
        f"OSError branch must keep stderr_bytes None (never captured), got "
        f"{payload.get('stderr_bytes')!r}"
    )
    assert payload.get("stderr_tail") is None, (
        f"OSError branch must keep stderr_tail None, got {payload.get('stderr_tail')!r}"
    )


# ─── AC4: empty bytes regression — b"" stderr already yields 0 bytes ──────


def test_ac4_timeout_expired_stderr_empty_bytes_reports_stderr_bytes_zero(tmp_path, monkeypatch):
    """AC4 (empty-bytes regression guard on the existing bytes branch).

    `TimeoutExpired(..., stderr=b"")` already goes through the
    `isinstance(exc.stderr, (bytes, bytearray))` branch today (decodes to
    "", which is not None) — stderr_bytes == 0 and stderr_tail == "" already
    hold on current code. Guards that GH2007's fix to the None case does not
    regress this adjacent bytes-empty case.

    Passes today.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac4-learn-1",
            "content": "LESSON: empty bytes stderr must still report zero bytes",
            "type": "learning",
            "hash": "gh2007ac4hash01",
        }],
    )
    fake_ts = tmp_path / "fake_inject_ac4.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))
    monkeypatch.setenv("HAL_INJECT_TIMEOUT_S", "1")

    def _fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv, timeout=kwargs.get("timeout", 1), output=None, stderr=b""
        )

    monkeypatch.setattr(_p05_mod.subprocess, "run", _fake_run)

    log_path = tmp_path / "events_ac4.jsonl"
    _, failed, _ = _run_workflow(
        tmp_path, db_path, "AC4 empty bytes stderr regression guard", log_path
    )

    assert failed, "expected a 'learning_inject_callout_failed' event on subprocess timeout"
    _assert_empty_captured_stderr(failed[-1]["payload"])
