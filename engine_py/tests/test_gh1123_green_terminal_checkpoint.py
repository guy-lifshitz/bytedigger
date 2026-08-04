"""RED tests for 4D604942 / GH1123 — durable GREEN checkpoint on terminal
`E_GREEN_NOT_PASSING`.

Contract (spec `2026-07-25_4D604942_green_terminal_checkpoint_spec.md`):
GREEN will add THREE module-level named helpers to
`workflows/phase_5_implement.py` —

  * `_checkpoint_green_worktree(git_cwd, scratchpad, cycle, reason, git_cwd_source) -> dict`
  * `_build_green_checkpoint_message(cycle) -> str`
  * `_terminal_green_result(step, data, error, git_cwd, scratchpad, cycle, reason)`

§2.2 step 0 / §2.7 (AMBIENT-CWD GUARD, GH449) — the FIRST GREEN of this spec
checkpointed the orchestrator's OWN worktree, producing two real `--no-verify`
commits on the ship branch, because sibling tests drive `_verify_green_passing`
with no `org_config["git_cwd"]` and `lib/git_cwd.resolve_git_cwd` then falls
through its 5-level precedence chain to a bare `Path.cwd()`.  The helper must
now receive the source label from `resolve_git_cwd_with_source` and REFUSE to
touch the repo when that label is the literal `"cwd"`.  AC19 pins the unit
guard across all five labels; AC20 is the end-to-end regression pin for the
exact incident shape.

— and rewrite the FOUR `recoverable=False` return sites of
`_verify_green_passing` to route through `_terminal_green_result`.

§1q / D1CF5FDF — the three symbols do not exist yet, so every reference is
resolved with `getattr(phase_5_implement, "<name>", None)` INSIDE the test
body. The module therefore COLLECTS cleanly and FAILS at assert time.

§1l / stub-passability — none of the three UUT symbols is ever patched. The
only monkeypatched seams are infra/config: `phase_5_implement.run_test_command`
(disk_truth test runner), `phase_5_implement._emit_safe` (event sink, module
level binding from phase_workflows_common),
`phase_5_implement._git_op_with_lock_retry` (git write seam, required by AC11)
and `config_provider.foreign_state_dirname` (config seam, required by AC17).

§1i — no timing / racing fixture. Every singleton-ish resource (the git repo,
its index, the pre-commit hook, `.git/MERGE_HEAD`, the untracked foreign-state
`events.jsonl`, the blocked scratchpad `integrity` path, the
`HAL_BASELINE_DELTA_GATE` env pin) is PRE-STAGED deterministically before the
unit-under-test is invoked; nothing depends on wall-clock or contention.

§1j — every temp git repo is `Path(tempfile.mkdtemp()).resolve()` so the macOS
`/var/folders` symlink cannot break subprocess-cwd path equality.

SF-6 — every test that drives `_verify_green_passing` end-to-end (AC5, AC6,
AC7, AC11, AC15, AC18) pins `HAL_BASELINE_DELTA_GATE=0` so an ambient
`HAL_BASELINE_DELTA_ENFORCE=1` (or a `BD_`/`BYTEDIGGER_` alias) cannot divert
the step to `E_BASELINE_DELTA`.

Expected pre-GREEN: 20 tests, 0 passed, 20 failed.
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from bytedigger_engine import config_provider  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402


# ─── git fixture helpers (SETUP + assertion reads only) ───────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60,
    )


def _init_repo() -> Path:
    """Real git repo in a resolved temp dir (§1j) with exactly one commit."""
    repo = Path(tempfile.mkdtemp(prefix="gh1123-repo-")).resolve()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "red@example.invalid")
    _git(repo, "config", "user.name", "RED Fixture")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.hooksPath", ".git/hooks")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture()
def repo():
    r = _init_repo()
    try:
        yield r
    finally:
        shutil.rmtree(str(r), ignore_errors=True)


def _dirty(repo: Path, name: str = "newfile.txt", body: str = "green impl\n") -> Path:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _commit_count(repo: Path) -> int:
    r = _git(repo, "rev-list", "--count", "HEAD")
    return int((r.stdout or "0").strip() or 0)


def _head_sha(repo: Path) -> str:
    return (_git(repo, "rev-parse", "HEAD").stdout or "").strip()


def _ls_files(repo: Path) -> str:
    return _git(repo, "ls-files").stdout or ""


def _porcelain(repo: Path) -> str:
    return _git(repo, "status", "--porcelain").stdout or ""


# ─── phase_5 fixture helpers (mirrors tests/test_green_test_feedback_AEC7E800.py) ──


def _make_ctx(git_cwd: Path, scratchpad: Path, **extra_cfg) -> WorkflowContext:
    cfg: dict = {
        "git_cwd": str(git_cwd),
        "scratchpad_dir": str(scratchpad),
        "verify_green_passing": True,
    }
    cfg.update(extra_cfg)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(cycle_count: int = 2, red_commit_sha: str | None = None, **extra) -> StepResult:
    """prev StepResult WITHOUT red_commit_sha so the sibling / net-new-added
    gates are skipped and the run lands on the cycle-cap terminal site."""
    data: dict = {
        "green_log_path": "tests/build-green-output.log",
        "spec_path": "scratchpad/spec.md",
        "red_log_path": "tests/build-red-output.log",
        "validation_doc_path": "scratchpad/validation.md",
        "green_status": "PASS",
        "red_test_paths": ["tests/test_example.py"],
        "cycle_count": cycle_count,
    }
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    data.update(extra)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_green_artifact")


def _make_fail_stdout(dirpath: Path, content: str = "") -> tuple[str, str]:
    if not content:
        content = (
            "FAILED tests/test_example.py::test_something - AssertionError: expected True\n"
            "1 failed in 0.10s\n"
        )
    stdout_file = dirpath / "stdout.txt"
    stdout_file.write_text(content, encoding="utf-8")
    stderr_file = dirpath / "stderr.txt"
    stderr_file.write_text("", encoding="utf-8")
    return str(stdout_file), str(stderr_file)


def _patch_run_test_command(monkeypatch, stdout_path: str, stderr_path: str,
                            exit_code: int = 1, n_passed: int = 0, n_failed: int = 1):
    """Monkeypatch the disk_truth test-runner seam inside phase_5_implement."""
    from bytedigger_engine.lib.plugins.disk_truth.test_runner import TestRunResult  # noqa: PLC0415

    fake_result = TestRunResult(
        exit_code=exit_code,
        n_passed=n_passed,
        n_failed=n_failed,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    monkeypatch.setattr(phase_5_implement, "run_test_command", lambda *a, **kw: fake_result)
    return fake_result


def _record_events(monkeypatch) -> list[tuple[str, dict]]:
    """Capture the module-level `_emit_safe` binding (infra seam, not the UUT)."""
    seen: list[tuple[str, dict]] = []

    def _recorder(event_type, payload=None, **kw):
        seen.append((str(event_type), dict(payload or {})))
        return None

    monkeypatch.setattr(phase_5_implement, "_emit_safe", _recorder)
    return seen


def _checkpoint_events(seen: list[tuple[str, dict]]) -> list[dict]:
    return [p for (e, p) in seen if e == "green_terminal_checkpoint"]


def _gate_disabled_events(seen: list[tuple[str, dict]]) -> list[dict]:
    return [p for (e, p) in seen if e == "gate_disabled"]


def _resolve(name: str):
    return getattr(phase_5_implement, name, None)


# ═════════════════════════════════════════════════════════════════════════════
# AC1 — the helper exists as a module-level function
# ═════════════════════════════════════════════════════════════════════════════


def test_ac1_checkpoint_green_worktree_is_module_level_function():
    """AC1: `_checkpoint_green_worktree` is a module-level function on
    phase_5_implement.  FAILS pre-GREEN — 0 hits for the symbol today."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; "
        "actual: attribute absent from the module"
    )
    assert inspect.isfunction(fn), (
        f"expected inspect.isfunction(_checkpoint_green_worktree) is True; actual type={type(fn)!r}"
    )
    params = list(inspect.signature(fn).parameters)
    assert params[:5] == ["git_cwd", "scratchpad", "cycle", "reason", "git_cwd_source"], (
        "expected leading parameters ['git_cwd', 'scratchpad', 'cycle', 'reason', "
        "'git_cwd_source'] (§2.1 — git_cwd_source is load-bearing, it drives the §2.2 "
        f"step 0 ambient-cwd guard); actual {params!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC2 — exactly ONE new commit with the frozen subject, outcome=committed
# ═════════════════════════════════════════════════════════════════════════════


def test_ac2_checkpoint_creates_exactly_one_commit_with_frozen_subject(repo, tmp_path, monkeypatch):
    """AC2: on a real dirty repo the helper creates EXACTLY one new commit whose
    subject is `build: green cycle 2 (FAILED GATE — checkpoint)`, returns
    outcome=='committed' and a 40-char sha == `git rev-parse HEAD`."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    _record_events(monkeypatch)
    _dirty(repo)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    before = _commit_count(repo)
    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert isinstance(out, dict), f"expected a plain dict return; actual {type(out)!r}"
    assert out.get("outcome") == "committed", (
        f"expected outcome=='committed'; actual {out.get('outcome')!r} (detail={out.get('detail')!r})"
    )
    after = _commit_count(repo)
    assert after == before + 1, (
        f"expected commit count {before + 1} (exactly one checkpoint commit); actual {after}"
    )
    subject = (_git(repo, "log", "-1", "--format=%s").stdout or "").strip()
    assert subject == "build: green cycle 2 (FAILED GATE — checkpoint)", (
        "expected subject 'build: green cycle 2 (FAILED GATE — checkpoint)'; "
        f"actual {subject!r}"
    )
    sha = out.get("sha")
    assert isinstance(sha, str) and len(sha) == 40, (
        f"expected a 40-char sha string; actual {sha!r}"
    )
    assert sha == _head_sha(repo), (
        f"expected returned sha == git rev-parse HEAD ({_head_sha(repo)!r}); actual {sha!r}"
    )
    assert out.get("reason") == "cycle_cap", (
        f"expected reason echoed back as 'cycle_cap'; actual {out.get('reason')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC3 — patch artifact at <scratchpad>/integrity/green-checkpoint-c2-cycle_cap.patch
# ═════════════════════════════════════════════════════════════════════════════


def test_ac3_checkpoint_writes_patch_artifact_under_scratchpad(repo, tmp_path, monkeypatch):
    """AC3 (SF-4): the helper writes
    `<scratchpad>/integrity/green-checkpoint-c2-cycle_cap.patch` — the `reason`
    segment is mandatory — non-empty, naming the changed file, and returns that
    absolute path."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    _record_events(monkeypatch)
    _dirty(repo)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    expected = scratchpad / "integrity" / "green-checkpoint-c2-cycle_cap.patch"
    assert expected.exists(), (
        f"expected patch artifact at {str(expected)!r}; actual: file does not exist "
        f"(returned patch_path={out.get('patch_path')!r}, outcome={out.get('outcome')!r})"
    )
    text = expected.read_text(encoding="utf-8")
    assert text.strip() != "", (
        f"expected a non-empty patch body at {str(expected)!r}; actual: empty file"
    )
    assert "newfile.txt" in text, (
        "expected the staged diff to name 'newfile.txt'; "
        f"actual first 300 chars: {text[:300]!r}"
    )
    assert out.get("patch_path") == str(expected), (
        f"expected patch_path == {str(expected)!r}; actual {out.get('patch_path')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC4 — commit-message helper
# ═════════════════════════════════════════════════════════════════════════════


def test_ac4_build_green_checkpoint_message_exact_string():
    """AC4: `_build_green_checkpoint_message(3)` returns the frozen string."""
    fn = _resolve("_build_green_checkpoint_message")
    assert fn is not None, (
        "expected phase_5_implement._build_green_checkpoint_message to exist; actual: absent"
    )
    got = fn(3)
    assert got == "build: green cycle 3 (FAILED GATE — checkpoint)", (
        "expected 'build: green cycle 3 (FAILED GATE — checkpoint)'; "
        f"actual {got!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC5 — terminal cycle-cap path decorates the StepResult
# ═════════════════════════════════════════════════════════════════════════════


def test_ac5_terminal_cycle_cap_result_carries_checkpoint_metadata_and_suffix(
    repo, tmp_path, monkeypatch,
):
    """AC5: the cycle-cap terminal return of `_verify_green_passing` keeps
    E_GREEN_NOT_PASSING / recoverable=False, gains
    metadata['green_checkpoint']['outcome']=='committed', appends
    ' [checkpoint <sha12>]' to `error`, and stays within 120 chars."""
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")  # SF-6
    _record_events(monkeypatch)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    stdout_path, stderr_path = _make_fail_stdout(tmp_path)
    _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                            exit_code=1, n_passed=0, n_failed=1)
    _dirty(repo)

    ctx = _make_ctx(repo, scratchpad)
    prev = _make_prev(cycle_count=2)  # == GREEN_TEST_CYCLE_CAP → terminal site

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.error_code == "E_GREEN_NOT_PASSING", (
        f"expected error_code=='E_GREEN_NOT_PASSING'; actual {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False on the cycle-cap terminal site; actual {result.recoverable!r}"
    )
    meta = result.metadata or {}
    cp = meta.get("green_checkpoint")
    assert isinstance(cp, dict), (
        "expected metadata['green_checkpoint'] to be the checkpoint dict; "
        f"actual {cp!r} (metadata keys={sorted(meta.keys())!r})"
    )
    assert cp.get("outcome") == "committed", (
        f"expected metadata['green_checkpoint']['outcome']=='committed'; "
        f"actual {cp.get('outcome')!r} (detail={cp.get('detail')!r})"
    )
    sha = cp.get("sha") or ""
    assert isinstance(sha, str) and len(sha) == 40, (
        f"expected a 40-char checkpoint sha in metadata; actual {sha!r}"
    )
    err = result.error or ""
    assert "[checkpoint " in err, (
        f"expected '[checkpoint ' inside result.error; actual {err!r}"
    )
    assert sha[:12] in err, (
        f"expected the first 12 sha chars {sha[:12]!r} inside result.error; actual {err!r}"
    )
    assert len(err) <= 120, (
        f"expected len(result.error) <= 120 (run-phase.ts slice); actual {len(err)} ({err!r})"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC6 — green_terminal_checkpoint event
# ═════════════════════════════════════════════════════════════════════════════


def test_ac6_green_terminal_checkpoint_event_emitted_and_under_line_limit(
    repo, tmp_path, monkeypatch,
):
    """AC6: a `green_terminal_checkpoint` event is emitted with
    outcome=='committed', reason=='cycle_cap', phase==5, and the serialized
    JSONL line stays under EventLog._LINE_LIMIT_BYTES (4096)."""
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")  # SF-6
    seen = _record_events(monkeypatch)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    stdout_path, stderr_path = _make_fail_stdout(tmp_path)
    _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                            exit_code=1, n_passed=0, n_failed=1)
    _dirty(repo)

    ctx = _make_ctx(repo, scratchpad)
    prev = _make_prev(cycle_count=2)

    phase_5_implement._verify_green_passing(ctx, prev)

    events = _checkpoint_events(seen)
    assert len(events) == 1, (
        "expected exactly 1 'green_terminal_checkpoint' event; "
        f"actual {len(events)} (all event types seen: {[e for e, _ in seen]!r})"
    )
    payload = events[0]
    assert payload.get("outcome") == "committed", (
        f"expected payload['outcome']=='committed'; actual {payload.get('outcome')!r}"
    )
    assert payload.get("reason") == "cycle_cap", (
        f"expected payload['reason']=='cycle_cap'; actual {payload.get('reason')!r}"
    )
    assert payload.get("phase") == 5, (
        f"expected payload['phase']==5; actual {payload.get('phase')!r}"
    )
    line = json.dumps({
        "ts": "x", "run_id": "y", "event_type": "green_terminal_checkpoint", "payload": payload,
    })
    assert len(line) < 4096, (
        f"expected serialized JSONL line < 4096 bytes (patch body must NOT be in the payload); "
        f"actual {len(line)} bytes"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC7 — recoverable (cycle 1 < cap) return must NOT checkpoint  (§1ab path (b))
# ═════════════════════════════════════════════════════════════════════════════


def test_ac7_recoverable_cycle_one_return_does_not_checkpoint(repo, tmp_path, monkeypatch):
    """AC7: the recoverable (cycle 1 of 2) return creates NO commit, NO patch
    file, NO event, and leaves metadata['green_checkpoint'] unset — GREEN's
    retry needs the working tree exactly as it is."""
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")  # SF-6
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist before the "
        "selectivity guard is meaningful; actual: absent"
    )
    seen = _record_events(monkeypatch)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    stdout_path, stderr_path = _make_fail_stdout(tmp_path)
    _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                            exit_code=1, n_passed=0, n_failed=1)
    _dirty(repo)

    ctx = _make_ctx(repo, scratchpad)
    prev = _make_prev(cycle_count=1)  # < GREEN_TEST_CYCLE_CAP → recoverable site

    before = _commit_count(repo)
    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.recoverable is True, (
        f"expected recoverable is True at cycle 1 of 2; actual {result.recoverable!r}"
    )
    after = _commit_count(repo)
    assert after == before, (
        f"expected commit count unchanged at {before} on the recoverable path; actual {after}"
    )
    stray = sorted(str(p) for p in scratchpad.glob("integrity/green-checkpoint-*.patch"))
    assert stray == [], (
        "expected NO 'integrity/green-checkpoint-*.patch' artifact of ANY name on the "
        f"recoverable path; actual: {stray!r}"
    )
    assert _checkpoint_events(seen) == [], (
        "expected no 'green_terminal_checkpoint' event on the recoverable path; "
        f"actual {_checkpoint_events(seen)!r}"
    )
    assert (result.metadata or {}).get("green_checkpoint") is None, (
        "expected metadata['green_checkpoint'] to be unset on the recoverable path; "
        f"actual {(result.metadata or {}).get('green_checkpoint')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC8 — idempotency / re-entry (§1ab paths (c) + (d))
# ═════════════════════════════════════════════════════════════════════════════


def test_ac8_second_checkpoint_call_is_clean_noop(repo, tmp_path, monkeypatch):
    """AC8: a SECOND `_checkpoint_green_worktree` call straight after a
    successful checkpoint returns outcome=='clean', sha is None, n_files==0,
    and creates no second commit (porcelain-clean idempotency anchor)."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    _record_events(monkeypatch)
    _dirty(repo)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    first = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")
    assert first.get("outcome") == "committed", (
        f"expected the FIRST call to return outcome=='committed'; actual {first.get('outcome')!r} "
        f"(detail={first.get('detail')!r})"
    )
    count_after_first = _commit_count(repo)

    second = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert second.get("outcome") == "clean", (
        f"expected the SECOND call to return outcome=='clean'; actual {second.get('outcome')!r}"
    )
    assert second.get("sha") is None, (
        f"expected sha is None on the clean re-entry path; actual {second.get('sha')!r}"
    )
    assert second.get("n_files") == 0, (
        f"expected n_files==0 on the clean re-entry path; actual {second.get('n_files')!r}"
    )
    assert _commit_count(repo) == count_after_first, (
        f"expected commit count unchanged at {count_after_first} (no second commit); "
        f"actual {_commit_count(repo)}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC9 — HAL_GREEN_CHECKPOINT_GATE=0 kill-switch (SF-1 canonical gate seam)
# ═════════════════════════════════════════════════════════════════════════════


def test_ac9_kill_switch_disables_checkpoint(repo, tmp_path, monkeypatch):
    """AC9 (SF-1): with HAL_GREEN_CHECKPOINT_GATE=0 the helper returns
    outcome=='disabled', creates no commit, emits NO 'green_terminal_checkpoint'
    event, and DOES emit a 'gate_disabled' event naming the gate — even on a
    dirty tree (a silently-disabled preservation path is unobservable)."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    seen = _record_events(monkeypatch)
    monkeypatch.setenv("HAL_GREEN_CHECKPOINT_GATE", "0")
    _dirty(repo)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    before = _commit_count(repo)
    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert out.get("outcome") == "disabled", (
        f"expected outcome=='disabled' under HAL_GREEN_CHECKPOINT_GATE=0 "
        f"(env now {os.environ.get('HAL_GREEN_CHECKPOINT_GATE')!r}); "
        f"actual {out.get('outcome')!r}"
    )
    assert _commit_count(repo) == before, (
        f"expected commit count unchanged at {before}; actual {_commit_count(repo)}"
    )
    assert _checkpoint_events(seen) == [], (
        "expected no 'green_terminal_checkpoint' event when disabled; "
        f"actual {_checkpoint_events(seen)!r}"
    )
    gates = [p.get("gate") for p in _gate_disabled_events(seen)]
    assert "HAL_GREEN_CHECKPOINT_GATE" in gates, (
        "expected a 'gate_disabled' event whose payload['gate'] == "
        f"'HAL_GREEN_CHECKPOINT_GATE'; actual gate_disabled payload gates={gates!r} "
        f"(all event types seen: {[e for e, _ in seen]!r})"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC10 — bad-git-state guard
# ═════════════════════════════════════════════════════════════════════════════


def test_ac10_bad_git_state_guard_blocks_checkpoint(repo, tmp_path, monkeypatch):
    """AC10: with `.git/MERGE_HEAD` present the helper returns
    outcome=='bad_git_state', creates no commit, and the dirty file stays
    uncommitted."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    _record_events(monkeypatch)
    _dirty(repo)
    (repo / ".git" / "MERGE_HEAD").write_text(_head_sha(repo) + "\n", encoding="utf-8")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    before = _commit_count(repo)
    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert out.get("outcome") == "bad_git_state", (
        f"expected outcome=='bad_git_state' with .git/MERGE_HEAD present; "
        f"actual {out.get('outcome')!r} (detail={out.get('detail')!r})"
    )
    assert _commit_count(repo) == before, (
        f"expected commit count unchanged at {before}; actual {_commit_count(repo)}"
    )
    assert "newfile.txt" in _porcelain(repo), (
        "expected 'newfile.txt' to still be uncommitted in git status --porcelain; "
        f"actual porcelain={_porcelain(repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC11 — commit failure never masks E_GREEN_NOT_PASSING
# ═════════════════════════════════════════════════════════════════════════════


def test_ac11_commit_failure_does_not_mask_original_error(repo, tmp_path, monkeypatch):
    """AC11: when the git commit step fails, the terminal StepResult still
    reports E_GREEN_NOT_PASSING / recoverable=False, records
    metadata['green_checkpoint']['outcome']=='error', and does NOT append the
    '[checkpoint ' suffix."""
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")  # SF-6
    _record_events(monkeypatch)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    stdout_path, stderr_path = _make_fail_stdout(tmp_path)
    _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                            exit_code=1, n_passed=0, n_failed=1)
    _dirty(repo)

    # Infra seam only (§1l): let `git add` through, force the commit to fail.
    real_op = phase_5_implement._git_op_with_lock_retry

    def _failing_commit(cmd, *, cwd, timeout=30):
        if "commit" in list(cmd):
            return None, "non_lock_error"
        return real_op(cmd, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(phase_5_implement, "_git_op_with_lock_retry", _failing_commit)

    ctx = _make_ctx(repo, scratchpad)
    prev = _make_prev(cycle_count=2)

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.error_code == "E_GREEN_NOT_PASSING", (
        "expected the original error_code 'E_GREEN_NOT_PASSING' to survive a failed "
        f"checkpoint commit; actual {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False; actual {result.recoverable!r}"
    )
    cp = (result.metadata or {}).get("green_checkpoint")
    assert isinstance(cp, dict), (
        "expected metadata['green_checkpoint'] dict even when the commit fails; "
        f"actual {cp!r}"
    )
    assert cp.get("outcome") == "error", (
        f"expected metadata['green_checkpoint']['outcome']=='error'; actual {cp.get('outcome')!r} "
        f"(detail={cp.get('detail')!r})"
    )
    assert "[checkpoint " not in (result.error or ""), (
        "expected NO '[checkpoint ' suffix when nothing was committed; "
        f"actual error={result.error!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC12 — commit uses --no-verify (hostile pre-commit hook)
# ═════════════════════════════════════════════════════════════════════════════


def test_ac12_checkpoint_commit_bypasses_precommit_hook(repo, tmp_path, monkeypatch):
    """AC12: a repo-side `.git/hooks/pre-commit` that `exit 1`s must NOT be able
    to block the preservation commit (the commit is made with --no-verify)."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    _record_events(monkeypatch)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(str(hook), 0o755)
    _dirty(repo)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    before = _commit_count(repo)
    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert out.get("outcome") == "committed", (
        "expected outcome=='committed' despite an exit-1 pre-commit hook (--no-verify); "
        f"actual {out.get('outcome')!r} (detail={out.get('detail')!r})"
    )
    assert _commit_count(repo) == before + 1, (
        f"expected commit count {before + 1} (hook bypassed); actual {_commit_count(repo)}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC13 — the foreign-state dir NAMED BY THE SEAM is excluded (MF-3, §1g)
# ═════════════════════════════════════════════════════════════════════════════


def test_ac13_foreign_state_dir_from_seam_excluded_from_checkpoint(repo, tmp_path, monkeypatch):
    """AC13 (MF-3): with an untracked `<dirname>/events.jsonl` in a dirty repo,
    after the checkpoint `git ls-files` contains the GREEN file but NOT
    `<dirname>/events.jsonl`.  The dirname is READ FROM THE CANONICAL SEAM
    `config_provider.foreign_state_dirname()` at test time — never hardcoded, so
    a GREEN pinning a `.hal-build` literal cannot certify the wrong invariant."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    dirname = config_provider.foreign_state_dirname()
    assert isinstance(dirname, str) and dirname, (
        f"expected config_provider.foreign_state_dirname() to yield a non-empty str; "
        f"actual {dirname!r}"
    )
    _record_events(monkeypatch)
    _dirty(repo)
    rel_foreign = f"{dirname}/events.jsonl"
    foreign = repo / dirname / "events.jsonl"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text('{"event_type":"x"}\n', encoding="utf-8")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert out.get("outcome") == "committed", (
        f"expected outcome=='committed'; actual {out.get('outcome')!r} "
        f"(detail={out.get('detail')!r})"
    )
    tracked = _ls_files(repo)
    assert "newfile.txt" in tracked, (
        f"expected 'newfile.txt' to be tracked after the checkpoint; actual git ls-files={tracked!r}"
    )
    assert rel_foreign not in tracked, (
        f"expected {rel_foreign!r} (dirname from the config seam) to stay OUT of the "
        f"user's repo index; actual git ls-files={tracked!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC14 — MF-1 structural forcing function: ONE shared helper, all four sites
# ═════════════════════════════════════════════════════════════════════════════


def test_ac14_all_four_terminal_sites_route_through_shared_helper():
    """AC14 (MF-1): `inspect.getsource(_verify_green_passing)` must contain
    EXACTLY four `_terminal_green_result(` call sites and ZERO surviving inline
    `StepResult` returns pairing error_code="E_GREEN_NOT_PASSING" with
    recoverable=False.  This is what makes 'ONE shared helper, all call-sites'
    verifiable rather than aspirational — a GREEN wiring only the cycle-cap site
    leaves 3 of 4 terminal aborts still destroying the tree."""
    src = inspect.getsource(phase_5_implement._verify_green_passing)

    n_sites = src.count("_terminal_green_result(")
    assert n_sites == 4, (
        "expected exactly 4 '_terminal_green_result(' call sites in the source of "
        f"_verify_green_passing (sibling_net_new / net_new_added_test / net_new_delta / "
        f"cycle_cap); actual {n_sites}"
    )

    chunks = src.split("return StepResult(")
    offenders = [
        i for i, chunk in enumerate(chunks)
        if 'error_code="E_GREEN_NOT_PASSING"' in chunk and "recoverable=False" in chunk
    ]
    first_offender = chunks[offenders[0]][:400] if offenders else ""
    assert offenders == [], (
        "expected ZERO surviving inline `return StepResult(` blocks pairing "
        'error_code="E_GREEN_NOT_PASSING" with recoverable=False; actual offending '
        f"chunk index/indices {offenders!r}; first offending chunk (index "
        f"{offenders[0] if offenders else None}): {first_offender!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC15 — MF-1 behavioural: the non-cycle_cap `net_new_added_test` site
# ═════════════════════════════════════════════════════════════════════════════


def test_ac15_net_new_added_test_terminal_site_checkpoints(repo, tmp_path, monkeypatch):
    """AC15 (MF-1 behavioural): the `net_new_added_test` terminal site (a DIFFERENT
    site from cycle_cap) also checkpoints — reason=='net_new_added_test',
    outcome=='committed', E_GREEN_NOT_PASSING / recoverable=False preserved, and
    the decorated error still fits run-phase.ts's 120-char slice."""
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")  # SF-6
    _record_events(monkeypatch)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    stdout_path, stderr_path = _make_fail_stdout(tmp_path)
    _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                            exit_code=1, n_passed=0, n_failed=1)

    # The red test file is ADDED since red_commit_sha: untracked counts as added
    # (`_get_diff_added_files` source 3), and it is simultaneously the dirty tree
    # the checkpoint must preserve.
    red_sha = _head_sha(repo)
    _dirty(repo, name="tests/test_example.py", body="def test_x():\n    assert False\n")

    ctx = _make_ctx(repo, scratchpad)
    prev = _make_prev(cycle_count=1, red_commit_sha=red_sha)

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.error_code == "E_GREEN_NOT_PASSING", (
        "expected error_code=='E_GREEN_NOT_PASSING' at the net_new_added_test site; "
        f"actual {result.error_code!r} (error={result.error!r})"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False at the net_new_added_test site; actual {result.recoverable!r}"
    )
    cp = (result.metadata or {}).get("green_checkpoint")
    assert isinstance(cp, dict), (
        "expected metadata['green_checkpoint'] dict at the net_new_added_test site "
        "(this site must route through the SAME shared helper as cycle_cap); "
        f"actual {cp!r}"
    )
    assert cp.get("reason") == "net_new_added_test", (
        f"expected metadata['green_checkpoint']['reason']=='net_new_added_test'; "
        f"actual {cp.get('reason')!r}"
    )
    assert cp.get("outcome") == "committed", (
        f"expected metadata['green_checkpoint']['outcome']=='committed'; "
        f"actual {cp.get('outcome')!r} (detail={cp.get('detail')!r})"
    )
    err = result.error or ""
    assert len(err) <= 120, (
        "expected len(result.error) <= 120 at the net_new_added_test site (the widest "
        f"of the four, run-phase.ts slice); actual {len(err)} ({err!r})"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC16 — ADV-5: a failed patch write must NOT abort the commit
# ═════════════════════════════════════════════════════════════════════════════


def test_ac16_patch_write_failure_still_commits(repo, tmp_path, monkeypatch):
    """AC16 (ADV-5): when the patch artifact cannot be written (a REGULAR FILE
    pre-staged where the `integrity` dir must go, so mkdir raises), the helper
    still commits — patch_path is None, detail=='patch_write_failed',
    outcome=='committed'.  Preserving work outranks preserving the artifact;
    this clause is the whole justification for writing the patch BEFORE the
    commit."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    _record_events(monkeypatch)
    _dirty(repo)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    blocker = scratchpad / "integrity"          # pre-staged deterministically (§1i)
    blocker.write_text("not a directory\n", encoding="utf-8")

    before = _commit_count(repo)
    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert out.get("outcome") == "committed", (
        "expected outcome=='committed' even though the patch write failed; "
        f"actual {out.get('outcome')!r} (detail={out.get('detail')!r})"
    )
    assert out.get("patch_path") is None, (
        f"expected patch_path is None when the patch write failed; actual {out.get('patch_path')!r}"
    )
    assert out.get("detail") == "patch_write_failed", (
        f"expected detail=='patch_write_failed'; actual {out.get('detail')!r}"
    )
    assert _commit_count(repo) == before + 1, (
        f"expected commit count {before + 1} (the work is preserved regardless of the "
        f"artifact); actual {_commit_count(repo)}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC17 — MF-3 / ADV-6: neutral provider dirname (.bytedigger) also excluded
# ═════════════════════════════════════════════════════════════════════════════


def test_ac17_neutral_provider_foreign_state_dir_excluded(repo, tmp_path, monkeypatch):
    """AC17 (MF-3 / ADV-6): under the NEUTRAL `_DefaultConfigProvider`, whose
    `foreign_state_dirname()` is '.bytedigger' (config_provider.py:99-101), an
    untracked `.bytedigger/events.jsonl` must stay OUT of `git ls-files` after
    the checkpoint.  A GREEN that hardcodes '.hal-build' sweeps the engine's
    entire durable state (events.jsonl, dbos.sqlite, the scratchpad tree) into
    the user's repo inside a --no-verify commit no hook can stop.

    Only the CONFIG seam is patched, never the UUT (§1l).  Both binding forms
    are patched so the test is agnostic to whether GREEN does
    `from bytedigger_engine.config_provider import foreign_state_dirname` or
    `config_provider.foreign_state_dirname()`."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    monkeypatch.setattr(config_provider, "foreign_state_dirname", lambda: ".bytedigger")
    monkeypatch.setattr(
        phase_5_implement, "foreign_state_dirname", lambda: ".bytedigger", raising=False,
    )
    _record_events(monkeypatch)
    _dirty(repo)
    foreign = repo / ".bytedigger" / "events.jsonl"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text('{"event_type":"x"}\n', encoding="utf-8")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd")

    assert out.get("outcome") == "committed", (
        f"expected outcome=='committed'; actual {out.get('outcome')!r} "
        f"(detail={out.get('detail')!r})"
    )
    tracked = _ls_files(repo)
    assert "newfile.txt" in tracked, (
        f"expected 'newfile.txt' to be tracked after the checkpoint; actual git ls-files={tracked!r}"
    )
    assert ".bytedigger/events.jsonl" not in tracked, (
        "expected '.bytedigger/events.jsonl' (neutral-provider foreign-state dir) to stay "
        f"OUT of the user's repo index; actual git ls-files={tracked!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC18 — ADV-7: GREEN legitimately produced no edits → clean, no suffix
# ═════════════════════════════════════════════════════════════════════════════


def test_ac18_clean_tree_terminal_site_reports_clean_without_suffix(repo, tmp_path, monkeypatch):
    """AC18 (ADV-7): on a CLEAN working tree at the terminal cycle-cap site the
    checkpoint reports outcome=='clean', `result.error` carries NO
    '[checkpoint ' suffix (byte-identical to today's terminal message), and
    exactly ONE 'green_terminal_checkpoint' event with outcome=='clean' is still
    emitted — the event fires on every non-disabled outcome."""
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")  # SF-6
    seen = _record_events(monkeypatch)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    # stdout/stderr live OUTSIDE the repo so the tree stays porcelain-clean.
    stdout_path, stderr_path = _make_fail_stdout(tmp_path)
    _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                            exit_code=1, n_passed=0, n_failed=1)
    # NO _dirty() — the tree is clean (§1i: pre-staged, not raced).
    assert _porcelain(repo).strip() == "", (
        f"fixture precondition: expected a porcelain-clean repo; actual {_porcelain(repo)!r}"
    )

    ctx = _make_ctx(repo, scratchpad)
    prev = _make_prev(cycle_count=2)

    before = _commit_count(repo)
    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.error_code == "E_GREEN_NOT_PASSING", (
        f"expected error_code=='E_GREEN_NOT_PASSING'; actual {result.error_code!r}"
    )
    cp = (result.metadata or {}).get("green_checkpoint")
    assert isinstance(cp, dict), (
        f"expected metadata['green_checkpoint'] dict even on a clean tree; actual {cp!r}"
    )
    assert cp.get("outcome") == "clean", (
        f"expected metadata['green_checkpoint']['outcome']=='clean' on a clean tree; "
        f"actual {cp.get('outcome')!r} (detail={cp.get('detail')!r})"
    )
    assert cp.get("sha") is None, (
        f"expected sha is None on the clean outcome; actual {cp.get('sha')!r}"
    )
    assert "[checkpoint " not in (result.error or ""), (
        "expected result.error to stay byte-identical to today's terminal message (no "
        f"'[checkpoint ' suffix when nothing was committed); actual {result.error!r}"
    )
    assert _commit_count(repo) == before, (
        f"expected commit count unchanged at {before} on a clean tree; actual {_commit_count(repo)}"
    )
    events = _checkpoint_events(seen)
    assert len(events) == 1, (
        "expected exactly 1 'green_terminal_checkpoint' event on the clean outcome; "
        f"actual {len(events)} (all event types seen: {[e for e, _ in seen]!r})"
    )
    assert events[0].get("outcome") == "clean", (
        f"expected payload['outcome']=='clean'; actual {events[0].get('outcome')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC19 — §2.2 step 0 AMBIENT-CWD GUARD (unit, all five source labels)
# ═════════════════════════════════════════════════════════════════════════════


#: The four EXPLICIT derivations of `lib/git_cwd.resolve_git_cwd_with_source`
#: (module docstring, precedence levels 1-4).  All four must still checkpoint —
#: `scratchpad_climb` included, it is a deliberate derivation from a configured
#: scratchpad, not an ambient default.  Only the level-5 literal `"cwd"` is refused.
_EXPLICIT_GIT_CWD_SOURCES = (
    "cfg_git_cwd",
    "prev_data",
    "cfg_current_worktree_path",
    "scratchpad_climb",
)


def test_ac19_ambient_cwd_source_is_refused_other_sources_still_commit(tmp_path, monkeypatch):
    """AC19 (§2.2 step 0, GH449 — the highest-severity guard in this spec):
    `git_cwd_source == "cwd"` means resolution fell through every explicit level
    of `resolve_git_cwd_with_source` to a bare `Path.cwd()`.  A checkpoint is
    exactly the "dirty-tree fallback commit" that `lib/git_cwd.py`'s own docstring
    (Amendment 1) says must NEVER happen against an ambient cwd — so on a DIRTY
    real repo the helper must return outcome=='ambient_cwd' /
    detail=='ambient_cwd_refused' and touch nothing: no commit, no staging, no
    patch artifact.

    The four EXPLICIT labels are asserted on the same fixture shape (a fresh repo
    each, so they cannot interfere) to prove the guard discriminates on the label
    rather than simply disabling the feature — a GREEN that refuses everything
    would satisfy the refusal half alone.

    §1i — every repo, its dirt and its scratchpad are pre-staged before the call;
    nothing races and nothing is time-dependent."""
    fn = _resolve("_checkpoint_green_worktree")
    assert fn is not None, (
        "expected phase_5_implement._checkpoint_green_worktree to exist; actual: absent"
    )
    _record_events(monkeypatch)

    # ── half 1: source == "cwd" → refuse, touch nothing ──────────────────────
    repo = _init_repo()
    scratchpad = tmp_path / "scratch-cwd"
    scratchpad.mkdir()
    try:
        _dirty(repo)
        before = _commit_count(repo)
        head_before = _head_sha(repo)

        out = fn(str(repo), str(scratchpad), 2, "cycle_cap", "cwd")

        assert out.get("outcome") == "ambient_cwd", (
            "expected outcome=='ambient_cwd' when git_cwd_source=='cwd' (ambient "
            f"Path.cwd() fallback must never be committed); actual {out.get('outcome')!r} "
            f"(detail={out.get('detail')!r})"
        )
        assert out.get("detail") == "ambient_cwd_refused", (
            f"expected detail=='ambient_cwd_refused'; actual {out.get('detail')!r}"
        )
        assert out.get("sha") is None, (
            f"expected sha is None on the refused path; actual {out.get('sha')!r}"
        )
        assert out.get("patch_path") is None, (
            f"expected patch_path is None on the refused path; actual {out.get('patch_path')!r}"
        )
        assert out.get("n_files") == 0, (
            f"expected n_files==0 on the refused path (nothing was even inspected); "
            f"actual {out.get('n_files')!r}"
        )
        after = _commit_count(repo)
        assert after == before, (
            f"expected commit count unchanged at {before} in the ambient repo; actual {after} "
            f"(HEAD was {head_before!r}, now {_head_sha(repo)!r})"
        )
        porcelain = _porcelain(repo)
        assert porcelain.strip() != "", (
            "expected the dirty file to still be UNCOMMITTED (git status --porcelain "
            f"non-empty) after the refusal; actual porcelain={porcelain!r}"
        )
        assert "newfile.txt" in porcelain, (
            f"expected 'newfile.txt' to still show as dirty; actual porcelain={porcelain!r}"
        )
        strays = sorted(str(p) for p in scratchpad.rglob("*.patch"))
        assert strays == [], (
            "expected NO patch artifact anywhere under the scratchpad on the refused "
            f"path; actual {strays!r}"
        )
    finally:
        shutil.rmtree(str(repo), ignore_errors=True)

    # ── half 2: every EXPLICIT source label still checkpoints ────────────────
    for label in _EXPLICIT_GIT_CWD_SOURCES:
        other = _init_repo()
        sp = tmp_path / f"scratch-{label}"
        sp.mkdir()
        try:
            _dirty(other)
            before_n = _commit_count(other)

            res = fn(str(other), str(sp), 2, "cycle_cap", label)

            assert res.get("outcome") == "committed", (
                f"expected outcome=='committed' for the EXPLICIT source label {label!r} "
                "(only the literal 'cwd' is refused; the other four are explicit "
                f"derivations); actual {res.get('outcome')!r} (detail={res.get('detail')!r})"
            )
            assert _commit_count(other) == before_n + 1, (
                f"expected commit count {before_n + 1} for source label {label!r}; "
                f"actual {_commit_count(other)}"
            )
        finally:
            shutil.rmtree(str(other), ignore_errors=True)


# ═════════════════════════════════════════════════════════════════════════════
# AC20 — §2.7 regression pin: the EXACT incident shape (end-to-end)
# ═════════════════════════════════════════════════════════════════════════════


def test_ac20_terminal_site_never_commits_into_ambient_cwd(repo, tmp_path, monkeypatch):
    """AC20 (§2.7): the exact shape that committed twice into the orchestrator's
    own ship branch.  `org_config` carries NO `git_cwd`, NO `current_worktree_path`
    and NO `scratchpad_dir`, and `prev.data` carries no `git_cwd` — so
    `resolve_git_cwd_with_source` genuinely falls through all four explicit levels
    to `Path.cwd()` with source 'cwd'.  `monkeypatch.chdir` puts a REAL DIRTY git
    repo at that ambient cwd (§1j-resolved temp dir), so if the terminal path
    still commits, it commits there — observable as a bumped commit count.

    `_make_ctx` is deliberately NOT reused: it sets `git_cwd`, which is precisely
    the key whose absence caused the incident.  `_resolve_scratchpad(ctx)` raises
    when `scratchpad_dir` is unset; production guards it with try/except, so the
    checkpoint scratchpad is simply None here — expected, and the test must still
    pass post-GREEN.

    monkeypatch.chdir auto-restores the process cwd at teardown (never os.chdir)."""
    monkeypatch.setenv("HAL_BASELINE_DELTA_GATE", "0")  # SF-6
    _record_events(monkeypatch)
    stdout_path, stderr_path = _make_fail_stdout(tmp_path)  # OUTSIDE the repo
    _patch_run_test_command(monkeypatch, stdout_path, stderr_path,
                            exit_code=1, n_passed=0, n_failed=1)
    _dirty(repo)

    # The ambient process cwd IS a real dirty git repo — the incident's blast radius.
    monkeypatch.chdir(str(repo))

    cfg: dict = {"verify_green_passing": True}  # NO git_cwd / current_worktree_path / scratchpad_dir
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )
    prev = _make_prev(cycle_count=2)  # == cap → cycle_cap terminal site
    assert "git_cwd" not in (prev.data or {}), (
        "fixture precondition: expected prev.data to carry NO 'git_cwd'; actual keys "
        f"{sorted((prev.data or {}).keys())!r}"
    )

    before = _commit_count(repo)
    head_before = _head_sha(repo)

    result = phase_5_implement._verify_green_passing(ctx, prev)

    after = _commit_count(repo)
    assert after == before, (
        f"expected NO commit in the ambient cwd repo (count stays {before}); actual {after} "
        f"— HEAD moved {head_before!r} -> {_head_sha(repo)!r}.  This is the GH449 incident: "
        "a --no-verify `git add -A` commit into a repo the engine was never pointed at."
    )
    porcelain = _porcelain(repo)
    assert porcelain.strip() != "", (
        "expected the ambient repo's dirty file to remain UNCOMMITTED (git status "
        f"--porcelain non-empty); actual porcelain={porcelain!r}"
    )
    cp = (result.metadata or {}).get("green_checkpoint")
    assert isinstance(cp, dict), (
        "expected metadata['green_checkpoint'] dict on the terminal site even when the "
        f"checkpoint refuses; actual {cp!r} (error_code={result.error_code!r})"
    )
    assert cp.get("outcome") == "ambient_cwd", (
        "expected metadata['green_checkpoint']['outcome']=='ambient_cwd' — the terminal "
        "path must thread the source label from resolve_git_cwd_with_source, not the "
        f"source-discarding wrapper; actual {cp.get('outcome')!r} (detail={cp.get('detail')!r})"
    )
