"""bytedigger port note: carried as the helper module of
test_GH2007_timeout_none_stderr.py. AC6's db-missing preflight guard is
omitted — it relies on the host provider's home_root() being the host install
dir rather than $HOME.

RED-phase tests for GH1468 — inject-learnings silent-failure diagnosability.

Spec: 2026-08-02_GH1468_inject_learnings_silent_failure_spec.md — S2/S2b/S3/S4/S5.
Revised twice after gate rejections (1st: 2 MAJOR; 2nd: 0 MAJOR + 6 MINOR).
See inline notes tagged MAJOR-N/MINOR-N at each fix site.

Change under test: phase_05_inject.py's five `learning_inject_callout_failed`
call sites (`_run_inject`) gain `stderr_tail` + `stderr_bytes` +
`stderr_truncated` + `argv` + `argv_truncated`, a byte-budgeted (not
char-capped) fail-closed payload that always reaches the 4096-byte-limited
event log (including on the TimeoutExpired code path, S2b), a new
`"cli_no_output"` reason discriminating "bun died before the script ran"
(independent of exit code) from "the script ran and returned a JSON error",
an env-overridable subprocess timeout (`HAL_INJECT_TIMEOUT_S`, §1h), and the
soft-callout-failure path writing `[inject_cli_failed:<reason>]` instead of
the DB-missing preflight's `[memory_db_unavailable]` suffix.

§1q / D1CF5FDF: any new byte-budget constant (`_STDERR_TAIL_CAP_BYTES` or
similar) does not exist yet and is never imported or referenced by name here
— all assertions below check observable payload/event-log behavior instead.

§1i / §1l: fixture DBs and fake-bun scripts are pre-staged on disk before any
workflow invocation; assertions anchor on real emitted event payloads read
back from real on-disk `events.jsonl` files and real on-disk hal-memory.md
content — never on mocked return values.
"""
from __future__ import annotations

import json
import os
import sqlite3 as _stdlib_sqlite3
import stat
import subprocess
import textwrap
import time
from pathlib import Path

# conftest-import-time singleton installs engine_py + workflows dirs on
# sys.path. No module-level sys.path manipulation here (§1q / 81F97F3D).

from bytedigger_engine.workflows.phase_05_inject import NO_LEARNINGS_SENTINEL, phase_05_inject_workflow  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog, _LINE_LIMIT_BYTES  # noqa: E402 — both already exist today


# ─── shared fixture helpers ────────────────────────────────────────────────


def _write_minimal_checklist(scratchpad: Path, session_id: str) -> None:
    scratchpad.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "session_id": session_id,
        "complexity": "SIMPLE",
        "mode": "AUTONOMOUS",
        "cwd": str(scratchpad.parent),
        "pre_build_gate_version": "1.0.0",
        "written_at_ts": 1750000000,
    }
    (scratchpad / ".orchestrator-checklist.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _make_ctx(scratchpad: Path, session_id: str = "test-session-GH1468", **org_extra) -> WorkflowContext:
    _write_minimal_checklist(scratchpad, session_id)
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="task",
        session_id=session_id,
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_fixture_db(tmp_path: Path, rows: list[dict], name: str = "memory.db") -> Path:
    """Build a minimal memories + memories_fts fixture DB. `name` may embed
    subdirectories (e.g. for AC7's long-path scenario); parents are created.
    """
    db_path = tmp_path / name
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = _stdlib_sqlite3.connect(str(db_path))
    con.execute(
        """CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'semantic',
            category TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            hash TEXT NOT NULL UNIQUE,
            agent_id TEXT,
            run_id TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            metadata TEXT
        )"""
    )
    con.execute(
        """CREATE VIRTUAL TABLE memories_fts USING fts5(
            content,
            category,
            content=memories,
            content_rowid=id,
            tokenize='porter unicode61'
        )"""
    )
    for row in rows:
        cur = con.execute(
            "INSERT INTO memories (id, user_id, content, type, hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 0)",
            (row["id"], "test", row["content"], row.get("type", "learning"), row["hash"]),
        )
        rowid = cur.lastrowid
        con.execute(
            "INSERT INTO memories_fts (rowid, content, category) VALUES (?, ?, ?)",
            (rowid, row["content"], row.get("category", "")),
        )
    con.commit()
    con.close()
    return db_path


def _write_fake_bun(
    tmp_path: Path,
    dirname: str,
    plan: list[dict],
    argv_sidecar: Path | None = None,
) -> Path:
    """Pre-stage a fake `bun` binary driven by a python3 script + JSON "plan".

    MINOR-4 fix: the previous shell-heredoc + `textwrap.dedent` implementation
    broke on multiline/huge/non-ASCII stdout+stderr content (escaping
    fragility). This version writes a python3 script that reads its
    stdout/stderr/exit_code from an on-disk JSON plan file — content never
    has to survive shell quoting, and UTF-8 is handled natively.

    plan[i] = {"stdout": str, "stderr": str, "exit_code": int, "sleep_s": float?}
    Invocation N (0-based call count, clamped to len(plan)-1 for repeats)
    uses plan[N] — lets AC6/AC8 simulate a DIFFERENT response on the 2nd call
    (present -> absent retry) or a hang-then-timeout.

    §1i: written to disk BEFORE any workflow invocation reads it.
    """
    fake_dir = tmp_path / dirname
    fake_dir.mkdir(exist_ok=True)
    plan_path = fake_dir / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    counter_path = fake_dir / "counter.txt"
    fake_bun = fake_dir / "bun"
    argv_sidecar_repr = repr(str(argv_sidecar)) if argv_sidecar is not None else "None"

    script = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys
        import time

        PLAN_PATH = {str(plan_path)!r}
        COUNTER_PATH = {str(counter_path)!r}
        ARGV_SIDECAR = {argv_sidecar_repr}

        with open(PLAN_PATH, "r", encoding="utf-8") as f:
            plan = json.load(f)

        n = 0
        if os.path.exists(COUNTER_PATH):
            with open(COUNTER_PATH, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                n = int(raw) if raw else 0
        with open(COUNTER_PATH, "w", encoding="utf-8") as f:
            f.write(str(n + 1))

        idx = min(n, len(plan) - 1)
        entry = plan[idx]

        if ARGV_SIDECAR:
            with open(ARGV_SIDECAR, "a", encoding="utf-8") as f:
                f.write(json.dumps(sys.argv[1:]) + "\\n")

        sleep_s = entry.get("sleep_s")
        if sleep_s:
            sys.stderr.write(entry.get("stderr", ""))
            sys.stderr.flush()
            time.sleep(sleep_s)

        sys.stdout.write(entry.get("stdout", ""))
        if not sleep_s:
            sys.stderr.write(entry.get("stderr", ""))
        sys.exit(entry.get("exit_code", 0))
        """)
    fake_bun.write_text(script, encoding="utf-8")
    fake_bun.chmod(fake_bun.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake_bun


def _run_workflow(
    tmp_path: Path,
    db_path: Path,
    task_description: str,
    log_path: Path,
    session_id: str = "test-session-GH1468",
):
    """Execute phase_05_inject against a DEDICATED log file (MAJOR-1 fix: the
    previous helper shared one `events.jsonl` across all calls in a test;
    since EventLog is strictly append-only, `failed[0]` always resolved to
    the FIRST branch's event, making later-branch assertions vacuous — every
    caller here MUST pass its own `log_path`).

    Returns (result, callout_failed_events, scratchpad).
    """
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("p05", phase_05_inject_workflow())
    scratchpad = log_path.parent / f"scratch_{log_path.stem}"
    # bytedigger: the engine-repo root is the checkout root, so a cwd inside it
    # takes the recursive-build branch (which wants a MANIFEST.md there); run
    # from tmp_path, as a foreign project would (a test that already moved
    # the cwd elsewhere keeps it).
    prev_cwd = os.getcwd()
    if Path(prev_cwd).resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        os.chdir(tmp_path)
    try:
        result, _ = eng.execute(
            "p05",
            _make_ctx(
                scratchpad,
                session_id=session_id,
                task_description=task_description,
                memory_db_path=str(db_path),
            ),
        )
    finally:
        os.chdir(prev_cwd)
    events = EventLog(log_path).read_all()
    failed = [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    return result, failed, scratchpad


# ─── AC4: real subprocess, missing TS path → cli_no_output + diagnostics ─────


def test_ac4_missing_ts_path_real_bun_maps_to_cli_no_output_with_diagnostics(
    tmp_path, monkeypatch
):
    """AC4 (§1l, real subprocess, prod side-effect anchor).

    HAL_INJECT_LEARNINGS_TS points at a path that is never created. Real
    system `bun` is spawned (HAL_BUN_BIN left unset), fails with a genuine
    'Module not found' error, exits 1, prints nothing to stdout (mirrors
    §1-PREFLIGHT measurement (b) in the spec). Predusloviye: if `bun` cannot
    be resolved on this machine, this test fails loudly on the assertions
    below (wrong exit_code/reason/keys) rather than skipping — no `skip` is
    used anywhere in this file.

    RED: today the empty-stdout / non-zero-exit case is folded into the
    generic 'non-zero exit' reason with no stderr_tail/argv keys at all — this
    test asserts the post-GREEN discriminated shape and fails on all four
    counts today.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac4-learn-1",
            "content": "LESSON: diagnosable subprocess failures save debugging time",
            "type": "learning",
            "hash": "gh1468ac4hash01",
        }],
    )

    missing_ts = tmp_path / "definitely_absent_inject_GH1468.ts"
    assert not missing_ts.exists()  # §1i: pre-condition, never created
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(missing_ts))
    monkeypatch.delenv("HAL_BUN_BIN", raising=False)  # real system bun on PATH

    _, failed, _ = _run_workflow(
        tmp_path, db_path, "diagnosable subprocess failures", tmp_path / "events_ac4.jsonl"
    )

    assert failed, "expected at least one 'learning_inject_callout_failed' event"
    payload = failed[0]["payload"]

    assert payload.get("reason") == "cli_no_output", (
        f"expected reason='cli_no_output' for empty-stdout + non-zero exit, got "
        f"{payload.get('reason')!r} — S3 discriminator not implemented"
    )
    assert payload.get("exit_code") == 1

    stderr_tail = payload.get("stderr_tail")
    assert isinstance(stderr_tail, str) and stderr_tail, (
        f"expected non-empty stderr_tail string, got {stderr_tail!r} — S2 not implemented"
    )
    assert "Module not found" in stderr_tail

    argv = payload.get("argv")
    assert isinstance(argv, list) and len(argv) >= 2, (
        f"expected argv list with >=2 elements, got {argv!r} — S2 not implemented"
    )
    assert "bun" in os.path.basename(str(argv[0]))
    assert argv[1] == str(missing_ts)


# ─── AC5: all five failure branches carry stderr_tail + argv, 5 distinct reasons ──


def test_ac5_all_five_failure_branches_carry_stderr_tail_and_argv(tmp_path, monkeypatch):
    """AC5: each of the five `_run_inject` failure branches must attach
    `stderr_tail` and `argv` to its `learning_inject_callout_failed` payload,
    and the five branches must collectively yield 5 DISTINCT `reason` values.

    MAJOR-1 fix: each branch below runs against its OWN dedicated log file
    (via `_run_workflow(..., log_path=...)`) and its own dedicated fixture DB
    and scratchpad — no shared `events.jsonl`, so `failed[-1]` in one branch
    can never resolve to a different branch's event.

    RED: today only branch-1-equivalent (subprocess OSError) sets any extra
    field (a `reason` string embedding the exception), and NONE of the five
    sites emit `stderr_tail`/`argv` at all — every assertion below fails.

    §1i: each fake-bun script/DB/log file is pre-staged deterministically
    before the workflow invocation that exercises it — no timing races.
    """
    reasons: set[str] = set()

    def _fixture_db(tag: str) -> Path:
        return _make_fixture_db(
            tmp_path,
            [{
                "id": f"ac5-learn-{tag}",
                "content": f"LESSON: branch {tag} must carry diagnostics",
                "type": "learning",
                "hash": f"gh1468ac5hash{tag}",
            }],
            name=f"memory_{tag}.db",
        )

    # --- branch 1: bun binary itself is not executable (OSError at spawn) ---
    db1 = _fixture_db("b1")
    monkeypatch.setenv("HAL_BUN_BIN", str(tmp_path / "nonexistent_bun_binary_GH1468"))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(tmp_path / "irrelevant1.ts"))
    _, failed1, _ = _run_workflow(tmp_path, db1, "branch b1 diagnostics", tmp_path / "events_ac5_b1.jsonl")
    assert failed1, "branch 1 (OSError) must emit learning_inject_callout_failed"
    p1 = failed1[0]["payload"]
    assert "argv" in p1 and isinstance(p1["argv"], list) and p1["argv"], (
        "branch 1: argv must be present even though the process never spawned"
    )
    # MINOR-1 fix: assert KEY PRESENCE explicitly (not just `.get(...) is None`,
    # which is vacuously true when the key is absent entirely).
    assert "stderr_tail" in p1, (
        "branch 1: payload must carry the 'stderr_tail' key (value None is fine — "
        "no process ever ran to produce stderr — but the key itself must be present)"
    )
    assert p1["stderr_tail"] is None, (
        "branch 1: stderr_tail must be None — no process ever ran to produce stderr"
    )
    reasons.add(p1["reason"])

    # --- branch 2: unparseable stdout ---
    fake_bun2 = _write_fake_bun(tmp_path, "fake_bin_2", [{"stdout": "not-json-at-all{{{", "stderr": "garbled stderr", "exit_code": 1}])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun2))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(tmp_path / "irrelevant2.ts"))
    db2 = _fixture_db("b2")
    _, failed2, _ = _run_workflow(tmp_path, db2, "branch b2 diagnostics", tmp_path / "events_ac5_b2.jsonl")
    assert failed2, "branch 2 (unparseable stdout) must emit an event"
    p2 = failed2[0]["payload"]
    assert "stderr_tail" in p2 and "argv" in p2
    # MINOR-4 (2nd gate): check VALUES, not just key presence.
    assert p2["stderr_tail"] is not None and "garbled stderr" in p2["stderr_tail"], (
        f"branch 2: stderr_tail must contain what fake-bun actually wrote to stderr, got {p2.get('stderr_tail')!r}"
    )
    assert isinstance(p2["argv"], list) and any("irrelevant2.ts" in str(a) for a in p2["argv"]), (
        f"branch 2: argv must contain the resolved inject-learnings.ts path, got {p2.get('argv')!r}"
    )
    assert "--match" in p2["argv"], f"branch 2: argv must contain the --match flag, got {p2.get('argv')!r}"
    # MINOR-5 (2nd gate): argv_truncated is a short/normal argv here -> False.
    assert p2.get("argv_truncated") is False, (
        f"branch 2: argv is short/normal — argv_truncated must be False, got {p2.get('argv_truncated')!r}"
    )
    reasons.add(p2["reason"])

    # --- branch 3: stdout JSON is not an object ---
    fake_bun3 = _write_fake_bun(tmp_path, "fake_bin_3", [{"stdout": "[1,2,3]", "stderr": "array not object", "exit_code": 1}])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun3))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(tmp_path / "irrelevant3.ts"))
    db3 = _fixture_db("b3")
    _, failed3, _ = _run_workflow(tmp_path, db3, "branch b3 diagnostics", tmp_path / "events_ac5_b3.jsonl")
    assert failed3, "branch 3 (JSON not an object) must emit an event"
    p3 = failed3[0]["payload"]
    assert "stderr_tail" in p3 and "argv" in p3
    # MINOR-4 (2nd gate): check VALUES, not just key presence.
    assert p3["stderr_tail"] is not None and "array not object" in p3["stderr_tail"], (
        f"branch 3: stderr_tail must contain what fake-bun actually wrote to stderr, got {p3.get('stderr_tail')!r}"
    )
    assert isinstance(p3["argv"], list) and any("irrelevant3.ts" in str(a) for a in p3["argv"]), (
        f"branch 3: argv must contain the resolved inject-learnings.ts path, got {p3.get('argv')!r}"
    )
    assert "--match" in p3["argv"], f"branch 3: argv must contain the --match flag, got {p3.get('argv')!r}"
    reasons.add(p3["reason"])

    # --- branch 4: unknown error kind ---
    fake_bun4 = _write_fake_bun(
        tmp_path, "fake_bin_4",
        [{"stdout": json.dumps({"error": "totally_unknown_kind_gh1468"}), "stderr": "unknown kind stderr", "exit_code": 1}],
    )
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun4))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(tmp_path / "irrelevant4.ts"))
    db4 = _fixture_db("b4")
    _, failed4, _ = _run_workflow(tmp_path, db4, "branch b4 diagnostics", tmp_path / "events_ac5_b4.jsonl")
    assert failed4, "branch 4 (unknown error kind) must emit an event"
    p4 = failed4[0]["payload"]
    assert "stderr_tail" in p4 and "argv" in p4
    reasons.add(p4["reason"])

    # --- branch 5: parsed object, no 'error' key, non-zero exit, NON-empty stdout ---
    fake_bun5 = _write_fake_bun(
        tmp_path, "fake_bin_5",
        [{"stdout": json.dumps({"foo": "bar"}), "stderr": "no error key stderr", "exit_code": 1}],
    )
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun5))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(tmp_path / "irrelevant5.ts"))
    db5 = _fixture_db("b5")
    _, failed5, _ = _run_workflow(tmp_path, db5, "branch b5 diagnostics", tmp_path / "events_ac5_b5.jsonl")
    assert failed5, "branch 5 (no error key, non-empty stdout) must emit an event"
    p5 = failed5[0]["payload"]
    assert "stderr_tail" in p5 and "argv" in p5
    # MINOR-4 (2nd gate): check VALUES, not just key presence.
    assert p5["stderr_tail"] is not None and "no error key stderr" in p5["stderr_tail"], (
        f"branch 5: stderr_tail must contain what fake-bun actually wrote to stderr, got {p5.get('stderr_tail')!r}"
    )
    assert isinstance(p5["argv"], list) and any("irrelevant5.ts" in str(a) for a in p5["argv"]), (
        f"branch 5: argv must contain the resolved inject-learnings.ts path, got {p5.get('argv')!r}"
    )
    assert "--match" in p5["argv"], f"branch 5: argv must contain the --match flag, got {p5.get('argv')!r}"
    assert p5.get("reason") == "non-zero exit", (
        "branch 5 has NON-empty stdout — must stay distinct from cli_no_output"
    )
    reasons.add(p5["reason"])

    assert len(reasons) == 5, (
        f"expected 5 DISTINCT reason values across the 5 failure branches, "
        f"got {len(reasons)}: {sorted(reasons)}"
    )


# ─── AC6: soft callout failure gets its own suffix (last-of-N reason); ───────
# ─── DB-missing preflight path unchanged ─────────────────────────────────────


def test_ac6_soft_callout_failure_writes_inject_cli_failed_suffix_from_last_reason(
    tmp_path, monkeypatch
):
    """AC6 (visibility, issue point 4) + S4 multi-call semantics.

    `_run_inject` is called up to three times (present -> absent retry ->
    AND/OR fallback). This test drives exactly two calls: the first
    (--present) reports `learning_entries_absent` (mapped to a silent RETRY —
    no event emitted for it), the second (--absent, after retry) reports an
    UNMAPPED error kind and becomes the one-and-only emitted sentinel event.
    The suffix must be built from THIS (the actually-emitted, i.e. last)
    failure's reason — not a hardcoded/first-guess value.

    RED: today `_query_memory_learnings` collapses EVERY sentinel-tuple
    failure onto the fixed `[memory_db_unavailable]` suffix
    (phase_05_inject.py :490 today), so both the positive and negative
    assertions below currently fail.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac6-learn-1",
            "content": "LESSON: soft callout failures must be visible in the artifact",
            "type": "learning",
            "hash": "gh1468ac6hash01",
        }],
    )
    fake_bun = _write_fake_bun(
        tmp_path, "fake_bin_ac6",
        [
            {"stdout": json.dumps({"error": "learning_entries_absent"}), "stderr": "", "exit_code": 1},
            {"stdout": json.dumps({"error": "totally_unknown_kind_ac6b"}), "stderr": "second call fails differently", "exit_code": 1},
        ],
    )
    fake_ts = tmp_path / "fake_inject_ac6.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))

    result, failed, scratchpad = _run_workflow(
        tmp_path, db_path, "soft callout failure visibility", tmp_path / "events_ac6.jsonl"
    )

    assert result.status == "ok", (
        f"soft callout failure must remain a degraded-OK path, got {result.status!r}"
    )
    assert failed, "expected 'learning_inject_callout_failed' event"
    assert failed[-1]["payload"].get("reason") == "totally_unknown_kind_ac6b", (
        "exactly one event is emitted in this scenario (the present-mode "
        "learning_entries_absent retry is silent by design); its reason must "
        f"be the actually-failing (second) call's reason, got {failed[-1]['payload'].get('reason')!r}"
    )

    hal_memory_path = scratchpad / "injection" / "hal-memory.md"
    assert hal_memory_path.is_file(), "hal-memory.md not written"
    body = hal_memory_path.read_text(encoding="utf-8")

    assert "[inject_cli_failed:totally_unknown_kind_ac6b]" in body, (
        "soft callout failure must be distinguishable via '[inject_cli_failed:<reason>]' "
        f"suffix carrying the LAST failure's reason; got body: {body!r}"
    )
    assert "[memory_db_unavailable]" not in body, (
        "'[memory_db_unavailable]' must be reserved for the DB-missing preflight check, "
        f"not a live callout failure; got body: {body!r}"
    )


# ─── AC7: byte-budgeted fail-closed event, worst-case non-ASCII stderr ───────


def test_ac7_worst_case_oversized_non_ascii_stderr_is_size_bounded_and_tail_preserved(
    tmp_path, monkeypatch
):
    """AC7 (rewritten after MAJOR-2 gate rejection): worst-case run — 100KB+
    of NON-ASCII stderr (Cyrillic + emoji, so bytes >> characters), a long DB
    path, a long FTS-derived task description, and an explicit --session-id.
    Asserts against the REAL on-disk `events.jsonl`, not a constructed
    payload:
      (a)+(b) the 'learning_inject_callout_failed' event is PHYSICALLY present
          AND its OWN serialized line is <= event_log._LINE_LIMIT_BYTES (4096)
          AND non-trivially sized (> 500 bytes). MINOR-1 (2nd gate) fix: a
          bare "every persisted line <= 4096" check is VACUOUS on its own —
          `event_log.append()` structurally rejects any line over budget
          BEFORE it ever reaches disk (event_log.py:132), so that fact holds
          no matter what GREEN does. The forcing function is that an event
          built from >100KB of stderr both SURVIVES (a) and still carries a
          non-trivial diagnostic payload under budget (b) — not a near-empty
          stub that trivially satisfies the structural guarantee;
      (c) stderr_truncated is True and stderr_bytes is the real (>>4096) size;
      (d) the TAIL of the real stderr is present in stderr_tail, the HEAD is not;
      (e) stderr_tail is valid text with no U+FFFD replacement characters
          (i.e. truncation did not cut a multi-byte UTF-8 codepoint in half);
      (f) argv_truncated is True — argv carries the long DB path / long FTS
          expression, which must exceed any reasonable per-element cap.

    RED: today `stderr_tail`/`stderr_bytes`/`stderr_truncated` are not
    attached to the payload at all, so assertion (a)'s companion key-presence
    checks fail immediately; a naive char-cap (mutant M3) would additionally
    make (a) fail differently — the whole event silently vanishes from the
    log via the swallowed EventLogLineTooLarge in `_emit_safe`.
    """
    head_marker = "AC7_HEAD_MARKER_ΣΚΟΥΠΙΔΙ_ΔΕΝ_ΕΠΙΖΕΙ"
    tail_marker = "AC7_TAIL_MARKER_ΟΥΡΑ_ΕΠΙΖΕΙ_🎯"
    filler_unit = ("é" * 2000) + ("🚀" * 500)
    target_bytes = 100_000
    # Derive the repeat count from the ACTUAL encoded size of one filler unit
    # (é=2 bytes, 🚀=4 bytes in UTF-8) instead of a guessed magic repeat count
    # — a prior fixed "* 15" undershot 100KB (90114 bytes) because the
    # per-unit byte size was never checked against the target.
    unit_bytes = len(filler_unit.encode("utf-8"))
    reps_needed = (target_bytes // unit_bytes) + 2  # +2 reps of margin
    huge_stderr = head_marker + (filler_unit * reps_needed) + tail_marker
    assert len(huge_stderr.encode("utf-8")) > target_bytes, "fixture stderr must exceed 100KB in bytes"

    long_task_description = "parallel throughput optimization " + ("distinctkeyword " * 150)
    long_session_id = "session-" + ("s" * 400)
    long_db_name = ("nested_dir_" + "d" * 150) + "/memory.db"

    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac7-learn-1",
            "content": "LESSON: stderr truncation must preserve the tail and stay size-bounded",
            "type": "learning",
            "hash": "gh1468ac7hash01",
        }],
        name=long_db_name,
    )
    fake_bun = _write_fake_bun(
        tmp_path, "fake_bin_ac7",
        [{"stdout": json.dumps({"error": "totally_unknown_kind_ac7"}), "stderr": huge_stderr, "exit_code": 1}],
    )
    fake_ts = tmp_path / "fake_inject_ac7.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))

    log_path = tmp_path / "events_ac7.jsonl"
    _run_workflow(tmp_path, db_path, long_task_description, log_path, session_id=long_session_id)

    assert log_path.is_file(), "events.jsonl must exist"
    raw_lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert raw_lines, (
        "events.jsonl must not be empty — fail-closed requirement (S2) violated: "
        "the diagnostic event was silently dropped"
    )

    events_with_raw = [(line, json.loads(line)) for line in raw_lines]
    callout_pairs = [
        (line, e) for line, e in events_with_raw if e["event_type"] == "learning_inject_callout_failed"
    ]
    assert callout_pairs, (
        "'learning_inject_callout_failed' event physically missing from events.jsonl — "
        "fail-closed (S2) violated: append() rejected the line or _emit_safe swallowed the error"
    )
    raw_line, callout_event = callout_pairs[-1]
    payload = callout_event["payload"]

    # (a)+(b), tied together (MINOR-1, 2nd gate): the event's OWN serialized
    # line must respect the atomic-append budget AND be substantial, not a
    # near-empty stub that trivially satisfies a structurally-guaranteed limit.
    callout_line_bytes = len(raw_line.encode("utf-8"))
    assert callout_line_bytes <= _LINE_LIMIT_BYTES, (
        f"callout event line is {callout_line_bytes} bytes, exceeds atomic-append "
        f"budget ({_LINE_LIMIT_BYTES}) — S2 byte-budget requirement violated"
    )
    assert callout_line_bytes > 500, (
        f"callout event line is only {callout_line_bytes} bytes — suspiciously small "
        "for an event meant to carry a >100KB-stderr diagnostic; GREEN must not satisfy "
        "the byte budget by emitting a near-empty stub"
    )

    assert payload.get("stderr_truncated") is True, (
        f"expected stderr_truncated=True for a >100KB stderr blob, got {payload.get('stderr_truncated')!r}"
    )
    stderr_bytes = payload.get("stderr_bytes")
    assert isinstance(stderr_bytes, int) and stderr_bytes > _LINE_LIMIT_BYTES, (
        f"expected stderr_bytes to reflect the REAL (>>4096) stderr size, got {stderr_bytes!r}"
    )

    stderr_tail = payload.get("stderr_tail")
    assert isinstance(stderr_tail, str), f"expected stderr_tail to be a string, got {stderr_tail!r}"
    assert tail_marker in stderr_tail, "stderr_tail must preserve the TAIL of the real stderr"
    assert head_marker not in stderr_tail, "stderr_tail must NOT contain the HEAD of a >100KB stderr blob"
    assert "�" not in stderr_tail, (
        "stderr_tail contains U+FFFD — truncation cut a multi-byte UTF-8 codepoint in half "
        "instead of cutting on a character boundary"
    )

    # (f) MINOR-5 (2nd gate): argv_truncated must be True — the long DB path /
    # long FTS expression exceeds any reasonable per-element cap.
    assert payload.get("argv_truncated") is True, (
        f"expected argv_truncated=True given the long DB path / long FTS expression "
        f"in argv, got {payload.get('argv_truncated')!r}"
    )


# ─── AC8a/AC8b: TimeoutExpired → distinct reason; byte budget on that path ───
# Split after a CI-only failure (3rd gate pass) — see AC8a docstring.


def test_ac8a_subprocess_timeout_gets_distinct_reason_deterministic_only(tmp_path, monkeypatch):
    """AC8a (real subprocess, deterministic assertions ONLY).

    S2b (spec addendum): the hardcoded `timeout=30` becomes env-overridable
    via `get_config().int_value("HAL_INJECT_TIMEOUT_S", 30)` (§1h). This test
    sets `HAL_INJECT_TIMEOUT_S=1` and has fake-bun sleep 3s, so the REAL
    `subprocess.TimeoutExpired` fires (§1l, no mocking the subprocess
    boundary) in ~1s instead of a hardcoded 31s wait. A separate elapsed-time
    assertion makes an ignored override FAIL LOUDLY and FAST.

    Split rationale (measured, not assumed): a prior single AC8 additionally
    asserted `stderr_truncated is True` and specific marker content in
    `stderr_tail`. That is a RACE, not a prod invariant — probed directly:
    a child that flushes ~9000 bytes to stderr then sleeps, read via
    `subprocess.run(..., text=True, timeout=T)`, yields `exc.stderr` of 9000
    bytes at T=1.0/0.3/0.05 but **0 bytes at T=0.01** — when the timeout
    fires before the child has even started+flushed, `exc.stderr` is
    genuinely empty and `stderr_truncated=False` is the CORRECT answer (there
    was nothing to truncate). A loaded CI runner under pytest-xdist can push
    python3 child startup past `HAL_INJECT_TIMEOUT_S=1`, so asserting a
    specific captured-byte-count here is asserting a race outcome, not a
    contract. This test therefore asserts only what MUST hold regardless of
    how much of `exc.stderr` won the race: the discriminated reason, physical
    event presence, line-size budget compliance, the override being honored,
    and that the five new payload keys are present with `stderr_bytes` being
    a valid non-negative int (proof prod reads `exc.stderr` AT ALL). The
    volume/marker-content assertions moved to AC8b, which removes the race by
    substituting the subprocess boundary directly.

    RED: today `TimeoutExpired` is folded into the same `except (OSError,
    subprocess.TimeoutExpired)` branch as OSError — no discriminated reason,
    none of the five new keys exist, and `HAL_INJECT_TIMEOUT_S` is unread.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac8a-learn-1",
            "content": "LESSON: timeouts must be distinguishable from OSError",
            "type": "learning",
            "hash": "gh1468ac8ahash01",
        }],
    )

    fake_bun = _write_fake_bun(
        tmp_path, "fake_bin_ac8a",
        [{"stdout": "", "stderr": "partial output before hang\n", "exit_code": 0, "sleep_s": 3}],
    )
    fake_ts = tmp_path / "fake_inject_ac8a.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))
    monkeypatch.setenv("HAL_INJECT_TIMEOUT_S", "1")  # §1h override — GREEN must read this

    log_path = tmp_path / "events_ac8a.jsonl"
    started = time.monotonic()
    _, failed, _ = _run_workflow(
        tmp_path, db_path, "timeout distinct reason diagnostics", log_path
    )
    elapsed_s = time.monotonic() - started

    # Explicit, separate assertion that the override was actually honored.
    # Without this, a GREEN that ignores HAL_INJECT_TIMEOUT_S silently falls
    # back to the hardcoded 30s timeout — the test would just run slow
    # instead of failing loudly and fast.
    assert elapsed_s < 10, (
        f"test took {elapsed_s:.1f}s — HAL_INJECT_TIMEOUT_S=1 override was not honored "
        "(prod is still using the hardcoded 30s timeout)"
    )

    assert failed, "expected 'learning_inject_callout_failed' event on subprocess timeout"

    raw_lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events_with_raw = [(line, json.loads(line)) for line in raw_lines]
    callout_pairs = [
        (line, e) for line, e in events_with_raw if e["event_type"] == "learning_inject_callout_failed"
    ]
    assert callout_pairs, "'learning_inject_callout_failed' event physically missing from events.jsonl"
    raw_line, callout_event = callout_pairs[-1]
    payload = callout_event["payload"]

    callout_line_bytes = len(raw_line.encode("utf-8"))
    assert callout_line_bytes <= _LINE_LIMIT_BYTES, (
        f"timeout callout event line is {callout_line_bytes} bytes, exceeds the atomic-append "
        f"budget ({_LINE_LIMIT_BYTES}) — the byte budget must also cover the TimeoutExpired "
        "code path (a DIFFERENT line of code than the OSError/unparseable-stdout branches)"
    )

    assert payload.get("reason") == "subprocess timeout", (
        f"expected reason='subprocess timeout' distinct from 'subprocess error', "
        f"got {payload.get('reason')!r}"
    )

    # Key presence only (no volume/content assertions — see docstring race).
    for key in ("stderr_tail", "stderr_bytes", "stderr_truncated", "argv", "argv_truncated"):
        assert key in payload, f"payload missing required key {key!r} on the TimeoutExpired path"

    stderr_bytes = payload.get("stderr_bytes")
    assert isinstance(stderr_bytes, int) and stderr_bytes >= 0, (
        f"stderr_bytes must be a non-negative int — proves prod reads exc.stderr at all "
        f"(whatever the race let survive), got {stderr_bytes!r}"
    )


def test_ac8b_timeout_stderr_bytes_over_budget_deterministic_truncation(tmp_path, monkeypatch):
    """AC8b (deterministic byte-budget regression guard on the TimeoutExpired
    path — no subprocess-startup-vs-timeout race).

    Patches `phase_05_inject.subprocess.run` (module-level attribute) to
    RAISE a real `subprocess.TimeoutExpired` whose `.stderr` is `bytes` (not
    `str`) of length > 100KB non-ASCII (Cyrillic + emoji). This substitutes
    the subprocess boundary, not the UUT: the UUT under test here is the
    timeout-handling + payload-assembly code in `_run_inject`, which must
    turn whatever `exc.stderr` it receives into a size-bounded event. The
    `bytes` type is deliberate and load-bearing — it is a regression guard
    for a measured prod defect where `TimeoutExpired.stderr` arrives as
    `bytes` despite `subprocess.run(..., text=True, ...)`.

    Asserts the CONTRACT, not today's implementation shape: event physically
    present in `events.jsonl`, that event's own line <= `_LINE_LIMIT_BYTES`
    and > 500 bytes (non-trivial payload, not a stub), `stderr_truncated is
    True`, `stderr_bytes` >> 4096 and EQUAL to the real captured length, the
    tail marker present / head marker absent in `stderr_tail`, and no U+FFFD
    (character-boundary-safe truncation).

    RED: today none of `stderr_tail`/`stderr_bytes`/`stderr_truncated` exist
    on the payload, and the `except (OSError, subprocess.TimeoutExpired)`
    branch does not distinguish `bytes` vs `str` stderr at all.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac8b-learn-1",
            "content": "LESSON: TimeoutExpired.stderr may arrive as bytes despite text=True",
            "type": "learning",
            "hash": "gh1468ac8bhash01",
        }],
    )
    fake_ts = tmp_path / "fake_inject_ac8b.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))
    monkeypatch.setenv("HAL_INJECT_TIMEOUT_S", "1")

    head_marker = "AC8B_HEAD_ΣΚΟΥΠΙΔΙ_ΔΕΝ_ΕΠΙΖΕΙ"
    tail_marker = "AC8B_TAIL_ΟΥΡΑ_ΕΠΙΖΕΙ_🔥"
    filler_unit = ("é" * 2000) + ("🚀" * 500)
    target_bytes = 100_000
    unit_bytes = len(filler_unit.encode("utf-8"))
    reps_needed = (target_bytes // unit_bytes) + 2  # margin, derived from real byte size
    huge_stderr_str = head_marker + (filler_unit * reps_needed) + tail_marker
    huge_stderr_bytes = huge_stderr_str.encode("utf-8")
    assert len(huge_stderr_bytes) > target_bytes, "fixture stderr must exceed 100KB in bytes"

    from bytedigger_engine.workflows import phase_05_inject as _p05_mod  # module already exists today — §1q-safe

    def _fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv, timeout=kwargs.get("timeout", 1), output=None, stderr=huge_stderr_bytes
        )

    monkeypatch.setattr(_p05_mod.subprocess, "run", _fake_run)

    log_path = tmp_path / "events_ac8b.jsonl"
    _run_workflow(tmp_path, db_path, "ac8b deterministic timeout bytes stderr", log_path)

    raw_lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert raw_lines, "events.jsonl must not be empty — fail-closed requirement (S2) violated"
    events_with_raw = [(line, json.loads(line)) for line in raw_lines]
    callout_pairs = [
        (line, e) for line, e in events_with_raw if e["event_type"] == "learning_inject_callout_failed"
    ]
    assert callout_pairs, "'learning_inject_callout_failed' event physically missing from events.jsonl"
    raw_line, callout_event = callout_pairs[-1]
    payload = callout_event["payload"]

    callout_line_bytes = len(raw_line.encode("utf-8"))
    assert callout_line_bytes <= _LINE_LIMIT_BYTES, (
        f"timeout callout event line is {callout_line_bytes} bytes, exceeds the atomic-append "
        f"budget ({_LINE_LIMIT_BYTES}) — byte budget must cover bytes-typed exc.stderr too"
    )
    assert callout_line_bytes > 500, (
        f"timeout callout event line is only {callout_line_bytes} bytes — too small to carry "
        "a >100KB-stderr diagnostic; GREEN must not satisfy the budget via a near-empty stub"
    )

    assert payload.get("reason") == "subprocess timeout"
    assert payload.get("stderr_truncated") is True, (
        f"expected stderr_truncated=True for a >100KB bytes-typed partial stderr, "
        f"got {payload.get('stderr_truncated')!r}"
    )
    stderr_bytes = payload.get("stderr_bytes")
    assert isinstance(stderr_bytes, int) and stderr_bytes > _LINE_LIMIT_BYTES, (
        f"expected stderr_bytes to reflect the real (>>4096) captured length, got {stderr_bytes!r}"
    )
    assert stderr_bytes == len(huge_stderr_bytes), (
        f"stderr_bytes must equal the REAL captured length ({len(huge_stderr_bytes)}), got {stderr_bytes!r}"
    )

    stderr_tail = payload.get("stderr_tail")
    assert isinstance(stderr_tail, str) and stderr_tail, (
        f"expected non-empty stderr_tail decoded from a bytes-typed exc.stderr, got {stderr_tail!r}"
    )
    assert tail_marker in stderr_tail, "stderr_tail must preserve the TAIL of the real (bytes) stderr"
    assert head_marker not in stderr_tail, "stderr_tail must NOT contain the HEAD of a >100KB partial stderr"
    assert "�" not in stderr_tail, (
        "stderr_tail contains U+FFFD — truncation cut a multi-byte UTF-8 codepoint in half "
        "instead of cutting on a character boundary"
    )


# ─── AC9: empty stdout at exit=0 must NOT silently look like a 0-match run ───


def test_ac9_empty_stdout_at_exit_zero_reports_cli_no_output_not_silent_success(
    tmp_path, monkeypatch
):
    """AC9: fake-bun prints NOTHING and exits 0 — this must be reported as
    reason='cli_no_output' (bun died before producing the S1 JSON contract),
    NOT silently accepted as a legitimate zero-row result (today's
    `[fts_hits=0]` outcome, with no event at all).

    RED: today empty stdout + exit 0 parses to `data={}`, `error_kind=None`,
    `proc.returncode == 0` so the code falls straight into the SUCCESS path
    with 0 rows — no 'learning_inject_callout_failed' event is ever emitted.
    """
    db_path = _make_fixture_db(
        tmp_path,
        [{
            "id": "ac9-learn-1",
            "content": "LESSON: empty stdout at exit 0 is not a legitimate empty result",
            "type": "learning",
            "hash": "gh1468ac9hash01",
        }],
    )
    fake_bun = _write_fake_bun(
        tmp_path, "fake_bin_ac9",
        [{"stdout": "", "stderr": "", "exit_code": 0}],
    )
    fake_ts = tmp_path / "fake_inject_ac9.ts"
    fake_ts.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(fake_ts))

    _, failed, scratchpad = _run_workflow(
        tmp_path, db_path, "empty stdout exit zero diagnostics", tmp_path / "events_ac9.jsonl"
    )

    assert failed, (
        "empty stdout at exit=0 must emit 'learning_inject_callout_failed', "
        "not silently succeed with 0 rows"
    )
    assert failed[-1]["payload"].get("reason") == "cli_no_output"

    hal_memory_path = scratchpad / "injection" / "hal-memory.md"
    assert hal_memory_path.is_file(), "hal-memory.md not written"
    body = hal_memory_path.read_text(encoding="utf-8")
    assert "[inject_cli_failed:" in body, (
        "empty-stdout-at-exit-0 must surface as a visible callout failure in the "
        f"artifact, not a plain silent no-match; got body: {body!r}"
    )
