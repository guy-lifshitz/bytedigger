"""GH1034 RED tests — RED cycle-2 degeneracy guard (restore cycle-1 tests).

Covers AC1-AC9 from
SHARED/memory/Decisions/2026-07-19_GH1034_red_cycle2_degeneracy_spec.md.

D1CF5FDF collectability: `_persist_red_cycle_sha` and `_read_red_cycle_sha`
do NOT exist yet on `phase_5_implement`. They are fetched via
`getattr(_p5, "name", None)` INSIDE each test body (never imported at module
top) so the file always COLLECTS cleanly and fails at assert time, never at
collection time. `_verify_red_fails_mechanically` itself already exists and
IS the UUT under test — it is invoked for real (never mocked) against a real
tmp git repo per §1i/§1l; only its test-execution collaborators
(`bounded_run`, `run_test_command`, `_infer_test_command_for_paths`,
`_resolve_git_cwd`) are stubbed to make pass/fail deterministic.

Expected pre-GREEN outcome (spec §3, revised gate cycle-2):
  FAIL (guard/helpers not yet implemented): AC1, AC2, AC3, AC4, AC4b, AC6,
    AC7, AC10, AC11, AC12
  PASS (regression floors, unchanged legacy behavior): AC5, AC8, AC9

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_5_implement as _p5  # noqa: E402
from helpers.git_repo import init_repo  # noqa: E402


# ─── generic helpers ───────────────────────────────────────────────────────


def _get_helper(name: str):
    fn = getattr(_p5, name, None)
    assert fn is not None, f"GH1034 helper {name!r} not implemented yet on phase_5_implement"
    return fn


def _make_ctx(scratchpad_dir: str, extra_cfg: dict | None = None) -> WorkflowContext:
    org_config = {"scratchpad_dir": scratchpad_dir}
    if extra_cfg:
        org_config.update(extra_cfg)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question="test question",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(data: dict, step_name: str = "commit_red_tests") -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name=step_name)


def _patch_emit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


def _write_spec_with_files(tmp_path: Path, files: list[str]) -> str:
    spec_path = tmp_path / "spec.md"
    body = "\n".join(f"- `{f}`" for f in files)
    spec_path.write_text(f"## Files\n{body}\n", encoding="utf-8")
    return str(spec_path)


# ─── §1i/§1j: real tmp git repo pre-staging (realpath'd, no races) ─────────


def _tmpdir() -> str:
    return os.path.realpath(tempfile.mkdtemp())


def _git(cwd: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=GH1034 RED", "-c", "user.email=gh1034@test.local", *args],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"git {args!r} failed rc={result.returncode}: {result.stderr}"
    return result.stdout.strip()


def _init_repo(cwd: str) -> None:
    init_repo(cwd)
    (Path(cwd) / "README.md").write_text("gh1034 fixture repo\n", encoding="utf-8")
    _git(cwd, "add", "--", "README.md")
    _git(cwd, "commit", "-q", "-m", "initial")


def _commit_file(cwd: str, relpath: str, content: str, message: str) -> str:
    p = Path(cwd) / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(cwd, "add", "--", relpath)
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


FAILING_CONTENT = "def test_thing():\n    assert False\n"
PASSING_CONTENT = "def test_thing():\n    assert True\n"
# byte-different from PASSING_CONTENT but still all-passing (AC4: degenerate
# both cycles with genuinely different content, vs AC4b: byte-identical).
PASSING_CONTENT_ALT = "def test_thing():\n    assert 1 == 1\n"


def _install_test_mocks(monkeypatch, git_cwd: str, rc_sequence: list[int]) -> dict:
    """Stub the mechanical-check collaborators; UUT (_verify_red_fails_mechanically)
    and real git restore side-effects are NOT mocked (§1l)."""
    calls = {"n": 0}

    def _bounded_run(argv, **kw):
        idx = min(calls["n"], len(rc_sequence) - 1)
        calls["n"] += 1
        rc = rc_sequence[idx]
        return SimpleNamespace(returncode=rc, stdout=("0 failed" if rc == 0 else "1 failed"))

    def _run_test_command(argv, cwd, timeout=120):
        idx = min(max(calls["n"] - 1, 0), len(rc_sequence) - 1)
        rc = rc_sequence[idx]
        return SimpleNamespace(exit_code=rc, n_passed=(1 if rc == 0 else 0), n_failed=(0 if rc == 0 else 1))

    monkeypatch.setattr(_p5, "bounded_run", _bounded_run)
    monkeypatch.setattr(_p5, "run_test_command", _run_test_command)
    monkeypatch.setattr(
        _p5, "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [{"kind": "py", "argv": ["pytest", *paths], "paths": list(paths)}]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev=None: git_cwd)
    return calls


# ─── AC1: content restored to c1 version + new GH1034 commit ──────────────


def test_ac1_restore_writes_c1_content_and_new_gh1034_commit(monkeypatch, tmp_path):
    """AC1: cycle-2 degenerate rewrite is restored to cycle-1 content, new commit created."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    c1_sha = _commit_file(git_cwd, rel, FAILING_CONTENT, "red cycle 1")
    persist_fn = _get_helper("_persist_red_cycle_sha")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    persist_fn(scratchpad, 1, c1_sha)
    _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 2 rewrite")
    _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0, 1])
    ctx = _make_ctx(scratchpad, {"git_cwd": git_cwd})
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    _p5._verify_red_fails_mechanically(ctx, prev)

    content = (Path(git_cwd) / rel).read_text(encoding="utf-8")
    assert content == FAILING_CONTENT, f"AC1: expected restored c1 content, got {content!r}"
    subject = _git(git_cwd, "log", "-1", "--format=%s")
    assert "GH1034" in subject, f"AC1: expected GH1034 in restore commit message, got {subject!r}"


# ─── AC2: step returns ok, red_rewrite_rejected True, sha == new HEAD ─────


def test_ac2_step_returns_ok_with_rewrite_rejected_and_new_sha(monkeypatch, tmp_path):
    """AC2: status=='ok', data['red_rewrite_rejected'] is True, red_commit_sha == HEAD after restore."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    c1_sha = _commit_file(git_cwd, rel, FAILING_CONTENT, "red cycle 1")
    persist_fn = _get_helper("_persist_red_cycle_sha")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    persist_fn(scratchpad, 1, c1_sha)
    _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 2 rewrite")
    _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0, 1])
    ctx = _make_ctx(scratchpad, {"git_cwd": git_cwd})
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.status == "ok", f"AC2: expected ok, got {result.status!r} error={result.error!r}"
    assert result.data.get("red_rewrite_rejected") is True, f"AC2: {result.data!r}"
    head_sha = _git(git_cwd, "rev-parse", "HEAD")
    assert result.data.get("red_commit_sha") == head_sha, f"AC2: sha mismatch {result.data!r} vs {head_sha}"


# ─── AC3: red_cycle_rewrite_rejected emitted with cycle==2 + c1 sha ───────


def test_ac3_rewrite_rejected_event_emitted_with_cycle_and_sha(monkeypatch, tmp_path):
    """AC3: emit red_cycle_rewrite_rejected with cycle==2 and c1_sha in payload."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    c1_sha = _commit_file(git_cwd, rel, FAILING_CONTENT, "red cycle 1")
    persist_fn = _get_helper("_persist_red_cycle_sha")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    persist_fn(scratchpad, 1, c1_sha)
    _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 2 rewrite")
    captured = _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0, 1])
    ctx = _make_ctx(scratchpad, {"git_cwd": git_cwd})
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    _p5._verify_red_fails_mechanically(ctx, prev)

    events = [e for e in captured if e["type"] == "red_cycle_rewrite_rejected"]
    assert events, f"AC3: expected red_cycle_rewrite_rejected event, got {[e['type'] for e in captured]}"
    payload = events[0]["payload"]
    assert payload.get("cycle") == 2, f"AC3: {payload!r}"
    assert payload.get("c1_sha") == c1_sha, f"AC3: {payload!r}"


# ─── AC4: both cycles degenerate → legacy error, but restore commit made ──


def test_ac4_both_cycles_degenerate_but_different_falls_back_to_legacy_error(monkeypatch, tmp_path):
    """AC4: c1 content ALSO all-passing but byte-DIFFERENT from c2 → restore commit IS
    created (content differs), re-check still passes → legacy E_RED_NOT_FAILING."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    c1_sha = _commit_file(git_cwd, rel, PASSING_CONTENT_ALT, "red cycle 1 (also degenerate, different bytes)")
    persist_fn = _get_helper("_persist_red_cycle_sha")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    persist_fn(scratchpad, 1, c1_sha)
    pre_restore_head = _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 2 rewrite")
    _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0, 0])
    ctx = _make_ctx(scratchpad, {"git_cwd": git_cwd})
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", f"AC4: {result.error_code!r}"
    assert result.recoverable is False, f"AC4: expected recoverable=False, got {result.recoverable!r}"
    head_after = _git(git_cwd, "rev-parse", "HEAD")
    assert head_after != pre_restore_head, (
        "AC4: expected exactly-one restore commit even though final result is the legacy error"
    )
    subject = _git(git_cwd, "log", "-1", "--format=%s")
    assert "GH1034" in subject, f"AC4: expected GH1034 restore commit message, got {subject!r}"


# ─── AC4b: c1 byte-identical to c2 → no diff, no commit, reason=no_diff ───


def test_ac4b_identical_c1_c2_no_commit_no_diff_reason(monkeypatch, tmp_path):
    """AC4b: c1 content byte-IDENTICAL to c2 (checkout yields no diff) → NO new
    commit; red_cycle_restore_unavailable emitted with reason 'no_diff'; legacy error."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    # single commit stands in for both cycle-1 and current (identical) content —
    # a real second "rewrite" commit with byte-identical content has nothing to
    # stage, so the fixture legitimately collapses to one commit (§1i: no race,
    # deterministic state).
    c1_sha = _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 1 == cycle 2, byte-identical")
    persist_fn = _get_helper("_persist_red_cycle_sha")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    persist_fn(scratchpad, 1, c1_sha)
    head_before = _git(git_cwd, "rev-parse", "HEAD")
    captured = _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0])
    ctx = _make_ctx(scratchpad, {"git_cwd": git_cwd})
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", f"AC4b: {result.error_code!r}"
    events = [e for e in captured if e["type"] == "red_cycle_restore_unavailable"]
    assert events, f"AC4b: expected red_cycle_restore_unavailable event, got {[e['type'] for e in captured]}"
    assert events[0]["payload"].get("reason") == "no_diff", f"AC4b: {events[0]['payload']!r}"
    head_after = _git(git_cwd, "rev-parse", "HEAD")
    assert head_after == head_before, "AC4b: expected NO new commit when checkout yields no diff"


# ─── AC5 (regression floor): cycle==1, no sidecar → legacy, no restore ────


def test_ac5_cycle1_all_passing_no_sidecar_legacy_error_no_restore(monkeypatch, tmp_path):
    """AC5. regression-anchor (may pass pre-GREEN): cycle==1 path untouched."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    head_before = _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 1 content")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0])
    ctx = _make_ctx(scratchpad)
    prev = _make_prev({"red_test_paths": [rel], "cycle": 1})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", f"AC5: {result.error_code!r}"
    head_after = _git(git_cwd, "rev-parse", "HEAD")
    assert head_after == head_before, "AC5: expected no restore commit for cycle==1"


# ─── AC6: cycle==2, sidecar absent → legacy error + unavailable event ─────


def test_ac6_cycle2_no_sidecar_legacy_error_and_unavailable_event(monkeypatch, tmp_path):
    """AC6: sidecar absent → legacy E_RED_NOT_FAILING + red_cycle_restore_unavailable."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    head_before = _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 2 rewrite, no cycle-1 sidecar")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    captured = _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0])
    ctx = _make_ctx(scratchpad)
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", f"AC6: {result.error_code!r}"
    events = [e for e in captured if e["type"] == "red_cycle_restore_unavailable"]
    assert events, f"AC6: expected red_cycle_restore_unavailable event, got {[e['type'] for e in captured]}"
    assert events[0]["payload"].get("cycle") == 2, f"AC6: {events[0]['payload']!r}"
    head_after = _git(git_cwd, "rev-parse", "HEAD")
    assert head_after == head_before, "AC6: expected no restore commit when sidecar absent"


# ─── AC7: persist/read round-trip, re-entry overwrite (§1ab) ──────────────


def test_ac7_persist_read_roundtrip_and_reentry_overwrite(tmp_path):
    """AC7: round-trip match; second persist same cycle overwrites without error."""
    persist_fn = _get_helper("_persist_red_cycle_sha")
    read_fn = _get_helper("_read_red_cycle_sha")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    sha_a = "a" * 40
    sha_b = "b" * 40

    persist_fn(scratchpad, 1, sha_a)
    assert read_fn(scratchpad, 1) == sha_a, "AC7: round-trip mismatch after first persist"

    persist_fn(scratchpad, 1, sha_b)  # re-entry: same cycle, overwrite
    assert read_fn(scratchpad, 1) == sha_b, "AC7: expected overwrite on re-entry persist"

    sidecar = Path(scratchpad) / "red-cycle-shas.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"AC7: expected valid JSON dict, got {data!r}"


# ─── AC8 (regression floor): genuine RED cycle 2 → ok, no restore ─────────


def test_ac8_genuine_failing_red_cycle2_no_restore_no_event(monkeypatch, tmp_path):
    """AC8. regression-anchor (may pass pre-GREEN): normal failing-RED path untouched."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    head_before = _commit_file(git_cwd, rel, FAILING_CONTENT, "genuine red, cycle 2")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    captured = _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [1])
    ctx = _make_ctx(scratchpad)
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.status == "ok", f"AC8: {result.status!r} error={result.error!r}"
    events = [e for e in captured if e["type"] == "red_cycle_rewrite_rejected"]
    assert not events, f"AC8: expected no restore event on genuine RED, got {events!r}"
    head_after = _git(git_cwd, "rev-parse", "HEAD")
    assert head_after == head_before, "AC8: expected no restore commit on genuine RED"


# ─── AC9 (regression floor): green_complete_resume precedence unchanged ───


def test_ac9_green_complete_resume_precedence_over_cycle2_guard(monkeypatch, tmp_path):
    """AC9. regression-anchor (may pass pre-GREEN): resume branch wins over cycle-2 guard."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    head_before = _commit_file(git_cwd, rel, PASSING_CONTENT, "impl landed, red now passes for real reason")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("HAL_GREEN_COMPLETE_RESUME_GATE", raising=False)
    spec_path = _write_spec_with_files(tmp_path, ["src/a.py"])
    monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["src/a.py"])
    captured = _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0])
    ctx = _make_ctx(scratchpad)
    prev = _make_prev({
        "red_test_paths": [rel],
        "cycle": 2,
        "spec_path": spec_path,
        "red_commit_sha": "c" * 40,
    })

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.status == "ok", f"AC9: {result.status!r} error={result.error!r}"
    assert result.data.get("green_complete_resume") is True, f"AC9: {result.data!r}"
    rewrite_events = [e for e in captured if e["type"] == "red_cycle_rewrite_rejected"]
    assert not rewrite_events, f"AC9: cycle-2 guard must not fire when resume wins, got {rewrite_events!r}"
    head_after = _git(git_cwd, "rev-parse", "HEAD")
    assert head_after == head_before, "AC9: expected no restore commit — resume branch must win"


# ─── AC10 (BLOCKER-2): real _commit_red_tests wires the cycle sidecar ─────


def test_ac10_commit_red_tests_persists_cycle_sha_to_sidecar(monkeypatch, tmp_path):
    """AC10: real _commit_red_tests invocation writes {'2': HEAD} to the
    scratchpad red-cycle-shas.json sidecar — producer wiring proven."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    rel = "tests/test_gh1034_fixture.py"
    (Path(git_cwd) / rel).parent.mkdir(parents=True, exist_ok=True)
    (Path(git_cwd) / rel).write_text(FAILING_CONTENT, encoding="utf-8")
    _patch_emit(monkeypatch)
    ctx = _make_ctx(scratchpad, extra_cfg={"git_cwd": git_cwd})
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2, "spec_path": None}, step_name="build_red_prompt")

    _p5._commit_red_tests(ctx, prev)

    sidecar = Path(scratchpad) / "red-cycle-shas.json"
    assert sidecar.exists(), f"AC10: expected sidecar at {sidecar}"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    head_sha = _git(git_cwd, "rev-parse", "HEAD")
    assert data.get("2") == head_sha, f"AC10: expected sidecar {{'2': head}}, got {data!r}"


# ─── AC11: persist failure degrades gracefully, no exception ─────────────


def test_ac11_commit_red_tests_persist_failure_emits_event_no_crash(monkeypatch, tmp_path):
    """AC11: _persist_red_cycle_sha target unwritable (pre-staged) → step still
    ok, red_cycle_sha_persist_failed emitted, no exception propagates."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    # pre-stage unwritable target (§1i): sidecar path collides with a directory,
    # forcing an OSError when the write is attempted.
    (Path(scratchpad) / "red-cycle-shas.json").mkdir(parents=True, exist_ok=True)
    rel = "tests/test_gh1034_fixture.py"
    (Path(git_cwd) / rel).parent.mkdir(parents=True, exist_ok=True)
    (Path(git_cwd) / rel).write_text(FAILING_CONTENT, encoding="utf-8")
    captured = _patch_emit(monkeypatch)
    ctx = _make_ctx(scratchpad, extra_cfg={"git_cwd": git_cwd})
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2, "spec_path": None}, step_name="build_red_prompt")

    result = _p5._commit_red_tests(ctx, prev)  # must not raise

    assert result.status == "ok", (
        f"AC11: expected step to stay ok despite persist failure, got {result.status!r}"
    )
    events = [e for e in captured if e["type"] == "red_cycle_sha_persist_failed"]
    assert events, f"AC11: expected red_cycle_sha_persist_failed event, got {[e['type'] for e in captured]}"


# ─── AC12: corrupt sidecar JSON fails safe, no crash ──────────────────────


def test_ac12_corrupt_sidecar_json_fails_safe_to_unavailable_legacy_error(monkeypatch, tmp_path):
    """AC12: corrupt sidecar JSON on disk, cycle==2 → _read_red_cycle_sha
    returns None → unavailable path, legacy error, no crash."""
    git_cwd = _tmpdir()
    _init_repo(git_cwd)
    rel = "tests/test_gh1034_fixture.py"
    head_before = _commit_file(git_cwd, rel, PASSING_CONTENT, "red cycle 2, sidecar corrupt")
    scratchpad = str(tmp_path / "scratchpad")
    Path(scratchpad).mkdir(parents=True, exist_ok=True)
    (Path(scratchpad) / "red-cycle-shas.json").write_text("{not valid json", encoding="utf-8")

    read_fn = _get_helper("_read_red_cycle_sha")
    assert read_fn(scratchpad, 1) is None, "AC12: expected None on corrupt sidecar JSON"

    captured = _patch_emit(monkeypatch)
    _install_test_mocks(monkeypatch, git_cwd, [0])
    ctx = _make_ctx(scratchpad)
    prev = _make_prev({"red_test_paths": [rel], "cycle": 2})

    result = _p5._verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", f"AC12: {result.error_code!r}"
    events = [e for e in captured if e["type"] == "red_cycle_restore_unavailable"]
    assert events, f"AC12: expected red_cycle_restore_unavailable on corrupt sidecar, got {[e['type'] for e in captured]}"
    head_after = _git(git_cwd, "rev-parse", "HEAD")
    assert head_after == head_before, "AC12: expected no restore commit on corrupt sidecar (no crash)"
