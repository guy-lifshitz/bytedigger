"""GH1245 RED tests — test_only mode stops silently disarming the RED/GREEN
boundary.

Frozen spec: SHARED/memory/Decisions/2026-07-26_GH1245_test_only_red_boundary_spec.md

Two independent defects, both covered here:
  - Change A (§2.1): `resolve_engine_mode` becomes a declared-field-only
    resolver — the prose-intent autodetect branch (`detect_test_only_intent`,
    `_engine_mode_task_text`, `_TEST_ONLY_INTENT_RE`) is removed from
    `phase_workflows_common`.
  - Change B (§2.2): a *declared* test_only build still produces a real
    RED/GREEN boundary SHA (pre-RED HEAD) instead of `red_commit_sha: None`,
    via a new module-level helper `_test_only_red_boundary(ctx, prev)` in
    `phase_5_implement`.

§1q/D1CF5FDF: `phase_5_implement` and `phase_workflows_common` already exist
and import cleanly today (this ship removes/adds symbols, it doesn't create
the modules), so this file's module-level imports are safe. The NOT-YET-
EXISTING symbols (`_test_only_red_boundary`, and the absence-checks in AC2)
are referenced only inside test bodies / via hasattr, so collection never
touches an AttributeError.

§1l: no test mocks the UUT. AC8/AC9/AC10 use a real filesystem (tmp_path)
and a real git repo (subprocess `git`), and read the pre-red ref file back
off disk after calling `_commit_red_tests` / the boundary helper.

§1i: singleton/time-shaped ACs (AC9 fresh vs AC10 retry) pre-stage state on
disk deterministically before invoking the UUT — no races (workflows.md §1i).

§1j: tmp_path is realpath-normalised before use as a subprocess cwd —
`/var/folders` symlinks on macOS otherwise break cwd equality between the
path pytest hands us and the path git reports back.

Do NOT implement the contract here — RED-only file.

AC mapping:
    test_ac1_resolve_engine_mode_ignores_prose_intent    -> AC1
    test_ac2_prose_autodetect_symbols_removed             -> AC2
    test_ac3_declared_marker_line1_still_resolves         -> AC3
    test_ac3_declared_marker_line2_still_resolves         -> AC3
    test_ac4_declared_non_test_only_marker_wins_verbatim  -> AC4
    test_ac5_no_autodetect_event_for_any_input            -> AC5
    test_ac6_declared_test_only_returns_real_head_sha     -> AC6
    test_ac7_declared_test_only_preserves_sentinel_contract -> AC7
    test_ac8_pre_red_ref_file_written_to_disk             -> AC8
    test_ac9_fresh_entry_creates_ref_returns_head          -> AC9
    test_ac10_retry_entry_returns_frozen_sha_write_once   -> AC10
    test_ac11_head_rev_parse_failure_returns_git_commit_failed -> AC11
    test_ac12_commit_green_code_no_longer_sees_missing_boundary -> AC12
    test_ac13_gh595_ac12_regression_floor_lint_rules_unbroken -> AC13
    test_ac13_gh595_ac12_regression_floor_guard_prev_unbroken -> AC13
    test_ac14_test_only_red_boundary_helper_exists_module_level -> AC14
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext


# ─── shared helpers ────────────────────────────────────────────────────────


def _make_ctx(
    *,
    scratchpad: Path | None = None,
    git_cwd: str | None = None,
    task_description: str | None = None,
    question: str = "benign build task",
) -> WorkflowContext:
    org: dict = {}
    if scratchpad is not None:
        org["scratchpad_dir"] = str(scratchpad)
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    if task_description is not None:
        org["task_description"] = task_description
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh1245",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(spec_path: str | None = None, **extra) -> StepResult:
    data: dict = {
        "red_log_path": "/tmp/test-gh1245-red-output.log",
        "cycle": 1,
        "red_test_paths": None,
    }
    if spec_path is not None:
        data["spec_path"] = spec_path
    data.update(extra)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_red_artifact")


def _spec_with_marker(tmp_path: Path, mode: str = "test_only", name: str = "spec.md") -> Path:
    p = tmp_path / name
    p.write_text(f"<!-- engine-mode: {mode} -->\n# spec\n", encoding="utf-8")
    return p


def _real(p: Path) -> Path:
    """§1j: realpath-normalise a tmp_path before using it as a git/subprocess cwd."""
    return Path(os.path.realpath(str(p)))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )


def _init_repo(cwd: Path) -> str:
    """Real git repo, one commit. Returns the initial commit SHA."""
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.email", "gh1245@example.com")
    _git(cwd, "config", "user.name", "GH1245 Test")
    (cwd / "README.md").write_text("init\n", encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", "init")
    return _head_sha(cwd)


def _advance_repo(cwd: Path) -> str:
    """Add a second commit, moving HEAD. Returns the new SHA."""
    (cwd / "second.txt").write_text("second\n", encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", "second")
    return _head_sha(cwd)


def _head_sha(cwd: Path) -> str:
    res = _git(cwd, "rev-parse", "HEAD")
    assert res.returncode == 0, f"git rev-parse HEAD failed: {res.stderr}"
    return res.stdout.strip()


def _is_valid_sha(s: object) -> bool:
    return (
        isinstance(s, str)
        and len(s) == 40
        and all(c in "0123456789abcdef" for c in s)
    )


# ─── AC1: prose never selects a mode ───────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "This is a test-only change.",
        "Tests Only fix incoming.",
        "This PR has no production code.",
        "please only fix the tests",
    ],
)
def test_ac1_resolve_engine_mode_ignores_prose_intent(text):
    """AC1: resolve_engine_mode(None, ctx) returns None even when task text
    contains the enumerated intent phrases — prose is no longer a source of
    the engine mode.

    Fails today: the prose-autodetect branch in resolve_engine_mode still
    fires and returns 'test_only' for each of these phrases.
    """
    from bytedigger_engine.workflows import phase_workflows_common as pwc  # noqa: PLC0415

    ctx = _make_ctx(question=text)
    result = pwc.resolve_engine_mode(None, ctx)

    assert result is None, (
        f"AC1: prose {text!r} must not select a mode; expected None, got {result!r}"
    )


# ─── AC2: the three now-unreachable symbols are gone ───────────────────────


def test_ac2_prose_autodetect_symbols_removed():
    """AC2: phase_workflows_common no longer defines detect_test_only_intent,
    _engine_mode_task_text, or _TEST_ONLY_INTENT_RE.

    Fails today: all three symbols exist in phase_workflows_common.
    """
    from bytedigger_engine.workflows import phase_workflows_common as pwc  # noqa: PLC0415

    for name in ("detect_test_only_intent", "_engine_mode_task_text", "_TEST_ONLY_INTENT_RE"):
        assert not hasattr(pwc, name), (
            f"AC2: phase_workflows_common must no longer define {name!r} (GH1245 §2.1)"
        )


# ─── AC3: declared marker still resolves (preserved contract) ─────────────


def test_ac3_declared_marker_line1_still_resolves(tmp_path):
    """AC3: explicit marker on line 1 still resolves to 'test_only'."""
    from bytedigger_engine.workflows import phase_workflows_common as pwc  # noqa: PLC0415

    spec = _spec_with_marker(tmp_path, "test_only")
    ctx = _make_ctx(question="benign")

    result = pwc.resolve_engine_mode(str(spec), ctx)

    assert result == "test_only", f"AC3: expected 'test_only' via marker, got {result!r}"


def test_ac3_declared_marker_line2_still_resolves(tmp_path):
    """AC3: explicit marker on line 2 (line 1 blank) still resolves."""
    from bytedigger_engine.workflows import phase_workflows_common as pwc  # noqa: PLC0415

    spec = tmp_path / "spec2.md"
    spec.write_text("\n<!-- engine-mode: test_only -->\n# spec\n", encoding="utf-8")
    ctx = _make_ctx(question="benign")

    result = pwc.resolve_engine_mode(str(spec), ctx)

    assert result == "test_only", f"AC3: expected 'test_only' via marker on line 2, got {result!r}"


# ─── AC4: manual-override precedence preserved ─────────────────────────────


def test_ac4_declared_non_test_only_marker_wins_verbatim(tmp_path):
    """AC4: an explicit non-test_only marker returns that mode verbatim,
    even when the task text carries test-only intent (manual override wins).
    """
    from bytedigger_engine.workflows import phase_workflows_common as pwc  # noqa: PLC0415

    spec = _spec_with_marker(tmp_path, "standard")
    ctx = _make_ctx(question="this is a test-only change, no production code")

    result = pwc.resolve_engine_mode(str(spec), ctx)

    assert result == "standard", f"AC4: expected manual override 'standard', got {result!r}"


# ─── AC5: no engine_mode_autodetected event for any input ─────────────────


@pytest.mark.parametrize(
    "spec_path_val,question",
    [
        (None, "this is a test-only change, no production code"),
        (None, "benign build task"),
    ],
)
def test_ac5_no_autodetect_event_for_any_input(tmp_path, monkeypatch, spec_path_val, question):
    """AC5: resolve_engine_mode emits no engine_mode_autodetected event,
    regardless of whether the task text carries test-only intent.

    Fails today: prose-intent input still emits engine_mode_autodetected
    with source='task_intent'.
    """
    from bytedigger_engine.workflows import phase_workflows_common as pwc  # noqa: PLC0415

    captured: list[str] = []
    ctx = _make_ctx(question=question)
    monkeypatch.setattr(
        pwc, "_emit_safe",
        lambda et, p, severity="warning": captured.append(et),
    )
    pwc.resolve_engine_mode(spec_path_val, ctx)

    assert "engine_mode_autodetected" not in captured, (
        f"AC5: engine_mode_autodetected must never fire post-GH1245, got events {captured!r}"
    )


# ─── AC6: declared test_only returns a real HEAD SHA, not None ────────────


def test_ac6_declared_test_only_returns_real_head_sha(tmp_path):
    """AC6: with a declared test_only marker, _commit_red_tests returns
    red_commit_sha == 40-hex SHA equal to `git rev-parse HEAD` of the build
    cwd — not None.

    Fails today: the test_only short-circuit returns red_commit_sha=None
    unconditionally.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    expected_sha = _init_repo(repo)

    spec = _spec_with_marker(tmp_path, "test_only")
    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad=scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    result = p5._commit_red_tests(ctx, prev)

    assert result.data is not None, "AC6: expected result.data dict"
    got_sha = result.data.get("red_commit_sha")
    assert _is_valid_sha(got_sha), (
        f"AC6: expected a 40-hex red_commit_sha, got {got_sha!r}"
    )
    assert got_sha == expected_sha, (
        f"AC6: red_commit_sha must equal git rev-parse HEAD of the build cwd; "
        f"expected {expected_sha!r}, got {got_sha!r}"
    )


# ─── AC7: sentinel contract preserved alongside the new SHA ───────────────


def test_ac7_declared_test_only_preserves_sentinel_contract(tmp_path, monkeypatch):
    """AC7: status == 'ok', commit_red_tests_skipped == 'test_only_mode',
    red_test_paths == [], and commit_red_tests_skipped event fires with
    reason == 'test_only_mode'."""
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    _init_repo(repo)

    spec = _spec_with_marker(tmp_path, "test_only")
    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()

    captured: list[dict] = []
    monkeypatch.setattr(
        p5, "_emit_safe",
        lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
    )
    ctx = _make_ctx(scratchpad=scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))
    result = p5._commit_red_tests(ctx, prev)

    assert result.status == "ok", f"AC7: expected status='ok', got {result.status!r}"
    assert result.data.get("commit_red_tests_skipped") == "test_only_mode"
    assert result.data.get("red_test_paths") == []
    skip_events = [c for c in captured if c["type"] == "commit_red_tests_skipped"]
    assert len(skip_events) == 1, (
        f"AC7: expected exactly 1 commit_red_tests_skipped event, got {len(skip_events)}"
    )
    assert skip_events[0]["payload"].get("reason") == "test_only_mode"
    # §2.2: the event payload is frozen byte-for-byte — no red_commit_sha key,
    # no other key. Gate v1 MAJOR-1 rejected an earlier draft that added
    # red_commit_sha here; this pins the preserved contract by exact equality
    # rather than relying solely on the sibling 885AAE33 file.
    assert skip_events[0]["payload"] == {
        "phase": 5, "step": "commit_red_tests", "reason": "test_only_mode",
    }, (
        f"AC7: commit_red_tests_skipped payload must equal the frozen dict "
        f"exactly; got {skip_events[0]['payload']!r}"
    )


# ─── AC8: production side-effect — pre-red ref file written to disk ───────


def test_ac8_pre_red_ref_file_written_to_disk(tmp_path):
    """§1l: the test_only path writes scratchpad/PRE_RED_REF_RELPATH to disk
    containing the boundary SHA — read the real file, never a mock.

    Fails today: the test_only short-circuit returns before ever resolving
    scratchpad or persisting a ref (it returns red_commit_sha=None first),
    so the ref file is never created.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    expected_sha = _init_repo(repo)

    spec = _spec_with_marker(tmp_path, "test_only")
    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad=scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    result = p5._commit_red_tests(ctx, prev)

    ref_path = scratchpad / p5.PRE_RED_REF_RELPATH
    assert ref_path.exists(), (
        f"AC8: expected pre-red ref file at {ref_path} to exist after the "
        f"test_only path ran"
    )
    on_disk = ref_path.read_text(encoding="utf-8").strip()
    assert on_disk == expected_sha, (
        f"AC8: pre-red ref file must contain the boundary SHA; "
        f"expected {expected_sha!r}, got {on_disk!r}"
    )
    assert result.data.get("red_commit_sha") == on_disk, (
        "AC8: returned red_commit_sha must match what was persisted to disk"
    )


# ─── AC9: §1ab fresh entry ──────────────────────────────────────────────


def test_ac9_fresh_entry_creates_ref_returns_head(tmp_path):
    """AC9: first entry, no pre-red ref on disk -> ref created, returned SHA
    equals HEAD.

    Fails today: no ref is ever created and the returned SHA is None.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    head_sha = _init_repo(repo)

    spec = _spec_with_marker(tmp_path, "test_only")
    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()
    ref_path = scratchpad / p5.PRE_RED_REF_RELPATH
    assert not ref_path.exists(), "AC9 precondition: fresh scratchpad, no ref yet"

    ctx = _make_ctx(scratchpad=scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    result = p5._commit_red_tests(ctx, prev)

    assert ref_path.exists(), "AC9: fresh entry must create the pre-red ref file"
    assert result.data.get("red_commit_sha") == head_sha, (
        f"AC9: fresh entry must return HEAD; expected {head_sha!r}, "
        f"got {result.data.get('red_commit_sha')!r}"
    )


# ─── AC10: §1ab retry / auto-resume / DBOS-replay ──────────────────────


def test_ac10_retry_entry_returns_frozen_sha_write_once(tmp_path):
    """AC10: incident shape — ref file already holds SHA X, HEAD has since
    advanced to Y != X. Second entry returns X and the file still holds X
    (write-once, idempotent, no overwrite, no counter).

    Pre-stages the contested resource deterministically (workflows.md §1i) —
    the ref is written to disk BEFORE the UUT is invoked, never raced.

    Fails today: the test_only short-circuit never reads/writes the ref at
    all and always returns red_commit_sha=None, so it cannot equal X.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    sha_x = _init_repo(repo)
    sha_y = _advance_repo(repo)
    assert sha_y != sha_x

    spec = _spec_with_marker(tmp_path, "test_only")
    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()

    # Pre-stage: ref file already holds X (cycle-1 frozen boundary), written
    # deterministically before the UUT runs — no race with the UUT.
    ref_path = scratchpad / p5.PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(sha_x, encoding="utf-8")

    ctx = _make_ctx(scratchpad=scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    result = p5._commit_red_tests(ctx, prev)

    assert result.data.get("red_commit_sha") == sha_x, (
        f"AC10: retry entry must return the frozen X={sha_x!r}, not advanced "
        f"HEAD Y={sha_y!r}; got {result.data.get('red_commit_sha')!r}"
    )
    on_disk = ref_path.read_text(encoding="utf-8").strip()
    assert on_disk == sha_x, (
        f"AC10: ref file must still hold X={sha_x!r} (write-once, no "
        f"overwrite); got {on_disk!r}"
    )


# ─── AC11: git rev-parse HEAD failure -> E_GIT_COMMIT_FAILED, never silent None ──


def test_ac11_head_rev_parse_failure_returns_git_commit_failed(tmp_path):
    """AC11: when `git rev-parse HEAD` fails in the test_only path (git_cwd
    is not a git repo at all), the step returns status='error' with
    error_code='E_GIT_COMMIT_FAILED' — never a silent red_commit_sha=None.

    Fails today: the test_only short-circuit never calls git at all and
    always returns status='ok' with red_commit_sha=None, regardless of
    whether git_cwd is a valid repo.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    not_a_repo = _real(tmp_path / "not-a-repo")
    not_a_repo.mkdir()
    # Sanity: confirm rev-parse genuinely fails here.
    sanity = _git(not_a_repo, "rev-parse", "HEAD")
    assert sanity.returncode != 0, "AC11 precondition: git_cwd must not be a repo"

    spec = _spec_with_marker(tmp_path, "test_only")
    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad=scratchpad, git_cwd=str(not_a_repo))
    prev = _make_prev(spec_path=str(spec))

    result = p5._commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"AC11: unresolvable HEAD must produce status='error', got {result.status!r} "
        f"(data={result.data!r})"
    )
    assert getattr(result, "error_code", None) == "E_GIT_COMMIT_FAILED", (
        f"AC11: expected error_code='E_GIT_COMMIT_FAILED', "
        f"got {getattr(result, 'error_code', None)!r}"
    )


# ─── AC12: end-to-end boundary — commit_green_code no longer starves ──────


def test_ac12_commit_green_code_no_longer_sees_missing_boundary(tmp_path):
    """AC12: _commit_green_code fed the prev.data produced by the test_only
    _commit_red_tests does not return E_MISSING_RED_BOUNDARY (it may fail
    for other, unrelated reasons in this minimal fixture repo — only the
    absence of E_MISSING_RED_BOUNDARY specifically is asserted).

    Fails today: red_commit_sha is None in the test_only result, so
    _commit_green_code deterministically returns E_MISSING_RED_BOUNDARY —
    this is the exact defect of issue #1245.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    repo = _real(tmp_path / "repo")
    repo.mkdir()
    expected_sha = _init_repo(repo)

    spec = _spec_with_marker(tmp_path, "test_only")
    scratchpad = _real(tmp_path / "scratch")
    scratchpad.mkdir()

    ctx = _make_ctx(scratchpad=scratchpad, git_cwd=str(repo))
    prev = _make_prev(spec_path=str(spec))

    red_result = p5._commit_red_tests(ctx, prev)
    assert red_result.data is not None, "AC12 precondition: commit_red_tests must produce data"

    green_prev = StepResult(
        status="ok", data=red_result.data, duration_ms=0, step_name="commit_red_tests",
    )
    # Gate v1 MINOR-5: pair the negative assertion with a positive one — the
    # boundary actually handed over must itself be sound, so this AC cannot
    # pass on an unrelated downstream error in _commit_green_code.
    handed_over_sha = green_prev.data.get("red_commit_sha")
    assert _is_valid_sha(handed_over_sha), (
        f"AC12: the boundary handed to commit_green_code must be a 40-hex "
        f"SHA, got {handed_over_sha!r}"
    )
    assert handed_over_sha == expected_sha, (
        f"AC12: the boundary handed to commit_green_code must equal the "
        f"fixture repo's real HEAD; expected {expected_sha!r}, got "
        f"{handed_over_sha!r}"
    )

    green_result = p5._commit_green_code(ctx, green_prev)

    assert getattr(green_result, "error_code", None) != "E_MISSING_RED_BOUNDARY", (
        f"AC12: commit_green_code must not starve on a declared test_only "
        f"boundary; got error_code={getattr(green_result, 'error_code', None)!r} "
        f"error={getattr(green_result, 'error', None)!r}"
    )


# ─── AC13: GH595 AC12 regression floor — unaffected downstream skips ──────


def test_ac13_gh595_ac12_regression_floor_lint_rules_unbroken(tmp_path, monkeypatch):
    """AC13: _verify_red_lint_rules with commit_red_tests_skipped ==
    'test_only_mode' still returns status='ok' and emits
    red_verify_skipped_test_only. Preserved contract — this is expected to
    already hold today; it is a floor against regressing it while fixing
    GH1245."""
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    captured: list[dict] = []
    monkeypatch.setattr(
        p5, "_emit_safe",
        lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
    )
    ctx = _make_ctx()
    prev = _make_prev(
        red_test_paths=[], commit_red_tests_skipped="test_only_mode",
    )
    result = p5._verify_red_lint_rules(ctx, prev)

    assert result.status == "ok", f"AC13: expected status='ok', got {result.status!r}"
    skip_events = [c for c in captured if c["type"] == "red_verify_skipped_test_only"]
    assert len(skip_events) == 1, (
        f"AC13: expected exactly 1 red_verify_skipped_test_only event, got {len(skip_events)}"
    )


def test_ac13_gh595_ac12_regression_floor_guard_prev_unbroken():
    """AC13: _verify_red_guard_prev_and_paths short-circuits ok when
    commit_red_tests_skipped == 'test_only_mode'. Preserved contract."""
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    prev = _make_prev(red_test_paths=[], commit_red_tests_skipped="test_only_mode")

    early_result, red_test_paths = p5._verify_red_guard_prev_and_paths(prev)

    assert early_result is not None, "AC13: expected an early short-circuit result"
    assert early_result.status == "ok", f"AC13: expected status='ok', got {early_result.status!r}"
    assert red_test_paths == []


# ─── AC14: §1aa — named module-level helper, not inlined ─────────────────


def test_ac14_test_only_red_boundary_helper_exists_module_level():
    """AC14: the SHA resolution + validation live in a module-level named
    helper _test_only_red_boundary (§1aa), not inlined in the branch.

    Fails today: phase_5_implement defines no such symbol.
    """
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    assert hasattr(p5, "_test_only_red_boundary"), (
        "AC14: phase_5_implement must define a module-level "
        "_test_only_red_boundary helper (GH1245 §2.2 / §1aa)"
    )
    assert callable(p5._test_only_red_boundary), (
        "AC14: _test_only_red_boundary must be callable"
    )
