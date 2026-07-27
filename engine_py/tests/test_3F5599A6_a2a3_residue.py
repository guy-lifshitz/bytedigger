"""RED tests for 3F5599A6 (GH279 A2/A3 residue): scratchpad cite-fallback +
manifest-sourced files_touched.

Spec: SHARED/memory/Decisions/2026-07-07_3F5599A6_gh279_a2a3_residue_spec.md

D1 (20F669AE): _verify_finding_quote gains a `fallback_dir` kwarg consulted only
when the base_dir/cwd resolution misses on a relative cite; call site
_aggregate_review_findings:1447 threads `fallback_dir=scratchpad`.

D2 (17E43F91): new engine.py module helper `_manifest_paths_from_result` +
files_touched emit block intersects scan-delta paths against a StepResult
manifest (worker_written_paths) when present, adding `source`/`n_scan_only`.

Expected pre-GREEN status:
  AC1  FAIL (TypeError — fallback_dir kwarg does not exist yet)
  AC2  PASS (unchanged miss-branch behavior — regression guard)
  AC3  FAIL (TypeError — fallback_dir kwarg does not exist yet)
  AC4  FAIL (TypeError — fallback_dir kwarg does not exist yet)
  AC5  FAIL (AssertionError — call site not wired; scratchpad-only cite stays
             suspect-file-not-found because target_root is the worktree root)
  AC6  FAIL (ImportError — _manifest_paths_from_result does not exist yet)
  AC7  FAIL (AssertionError — no manifest filtering; both paths present)
  AC8  FAIL (AssertionError — no additive "source" field yet)
  AC9  FAIL (AssertionError — no manifest filtering; both paths present)
  AC10 PASS (suppress-empty-event invariant unchanged — regression guard)
  AC11 FAIL (AssertionError — no additive "source" field yet)
"""
from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

import pytest

from contracts import (
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from engine import WorkflowEngine
import phase_6_review as _p6
from phase_6_review import _aggregate_review_findings, _verify_finding_quote


# ─── AC1-AC4: _verify_finding_quote fallback_dir kwarg ────────────────────────


def test_ac1_relative_cite_resolves_via_fallback_dir_when_base_dir_misses(tmp_path):
    """AC1: file exists ONLY under fallback_dir (exact quote at cited line)
    -> ("verified-exact", "OK"). base_dir exists but does not contain the file.
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir()
    (fallback_dir / "cited.py").write_text(
        "# line 1\ndef fallback_only():\n    pass\n", encoding="utf-8"
    )

    block = (
        "### SEVERITY: HIGH — FallbackOnly-AC1\n"
        "> cited.py:2: def fallback_only():\n"
        "Confidence: HIGH\n"
        "Description: fallback-dir citation test\n"
    )

    # Pre-GREEN: TypeError — fallback_dir kwarg does not exist yet.
    status, reason = _verify_finding_quote(
        block, {}, base_dir=base_dir, fallback_dir=fallback_dir
    )

    assert status == "verified-exact", (
        f"AC1: expected verified-exact when the cited file exists only under "
        f"fallback_dir. Got status={status!r} reason={reason!r}."
    )
    assert reason == "OK"


def test_ac2_fallback_dir_omitted_preserves_current_miss_behavior(tmp_path):
    """AC2: same fixture, fallback_dir omitted (3-arg call) ->
    ("suspect-file-not-found", "OK-UNVERIFIABLE-RELATIVE") — unchanged miss branch.
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    # NOTE: no fallback_dir supplied and no file under base_dir either.

    block = (
        "### SEVERITY: HIGH — NoFallback-AC2\n"
        "> cited.py:2: def fallback_only():\n"
        "Confidence: HIGH\n"
        "Description: 3-arg backward-compat citation test\n"
    )

    status, reason = _verify_finding_quote(block, {}, base_dir=base_dir)

    assert status == "suspect-file-not-found"
    assert reason == "OK-UNVERIFIABLE-RELATIVE"


def test_ac3_base_dir_hit_wins_over_fallback_dir(tmp_path):
    """AC3: file present under BOTH roots with differing content. Quote matching
    the base_dir version -> verified-exact. Quote matching only the fallback_dir
    version -> suspect-no-match (proves base_dir is checked first and wins).
    """
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir()

    (base_dir / "cited.py").write_text(
        "# line 1\ndef base_version():\n    return 1\n", encoding="utf-8"
    )
    (fallback_dir / "cited.py").write_text(
        "# line 1\ndef totally_different_fallback_impl():\n    return 2\n",
        encoding="utf-8",
    )

    block_base_quote = (
        "### SEVERITY: HIGH — PrecedenceBase-AC3\n"
        "> cited.py:2: def base_version():\n"
        "Confidence: HIGH\n"
        "Description: base-wins precedence test\n"
    )
    block_fallback_quote = (
        "### SEVERITY: HIGH — PrecedenceFallback-AC3\n"
        "> cited.py:2: def totally_different_fallback_impl():\n"
        "Confidence: HIGH\n"
        "Description: base-wins precedence test (fallback-only quote)\n"
    )

    # Pre-GREEN: TypeError — fallback_dir kwarg does not exist yet.
    status_base, _ = _verify_finding_quote(
        block_base_quote, {}, base_dir=base_dir, fallback_dir=fallback_dir
    )
    assert status_base == "verified-exact", (
        f"AC3 (sub-assert 1): expected verified-exact for the base_dir-matching "
        f"quote. Got {status_base!r}."
    )

    status_fallback, _ = _verify_finding_quote(
        block_fallback_quote, {}, base_dir=base_dir, fallback_dir=fallback_dir
    )
    assert status_fallback == "suspect-no-match", (
        f"AC3 (sub-assert 2): expected suspect-no-match — base_dir file exists so "
        f"base_dir wins (not fallback_dir), and the fallback-only quote does not "
        f"match the base_dir content. Got {status_fallback!r}."
    )


def test_ac4_fallback_dir_symlink_escape_rejected(tmp_path):
    """AC4: fallback_dir/<name> is a symlink escaping fallback_dir ->
    ("suspect-file-not-found", "PATH-ESCAPES-ROOT")."""
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_target = outside_dir / "secret.py"
    outside_target.write_text("# line 1\nSECRET = 1\n", encoding="utf-8")

    escape_link = fallback_dir / "escape.py"
    os.symlink(outside_target, escape_link)

    block = (
        "### SEVERITY: HIGH — SymlinkEscape-AC4\n"
        "> escape.py:2: SECRET = 1\n"
        "Confidence: HIGH\n"
        "Description: fallback_dir symlink-escape containment test\n"
    )

    # Pre-GREEN: TypeError — fallback_dir kwarg does not exist yet.
    status, reason = _verify_finding_quote(
        block, {}, base_dir=base_dir, fallback_dir=fallback_dir
    )

    assert status == "suspect-file-not-found"
    assert reason == "PATH-ESCAPES-ROOT"


# ─── AC5: _aggregate_review_findings call-site wiring ─────────────────────────


def test_ac5_aggregate_review_findings_verifies_scratchpad_only_artifact(
    tmp_path, monkeypatch
):
    """AC5: a role-file finding cites `reviews/post-fix-pytest.md:1`, a pipeline
    artifact that exists ONLY under the scratchpad (not under the worktree root
    that _verify_finding_quote's base_dir resolves to). Post-GREEN call-site
    wiring (fallback_dir=scratchpad) must resolve this to verified-exact.

    Pre-GREEN: call site passes only base_dir=target_root (worktree root) — the
    scratchpad-only artifact is absent there -> suspect-file-not-found.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    # Worktree root: separate, empty of the cited artifact.
    worktree_root = tmp_path / "worktree_root"
    worktree_root.mkdir()

    # Scratchpad: holds reviews/role-*.md AND the cited pipeline artifact.
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir()
    artifact = reviews_dir / "post-fix-pytest.md"
    artifact.write_text("# post-fix pytest report\nline2\n", encoding="utf-8")

    assert not (worktree_root / "reviews" / "post-fix-pytest.md").exists(), (
        "test setup: the artifact must NOT exist under worktree_root"
    )

    # cwd elsewhere so cwd-fallback resolution can't accidentally succeed.
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    role_lines = [
        "# ac5 reviewer", "",
        "### SEVERITY: HIGH — ScratchpadOnlyFinding",
        "> reviews/post-fix-pytest.md:1: # post-fix pytest report",
        "Confidence: HIGH",
        "Description: scratchpad-only pipeline artifact citation test",
        "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-ac5.md").write_text("\n".join(role_lines), encoding="utf-8")

    ctx = types.SimpleNamespace(
        org_config={
            "scratchpad_dir": str(scratchpad),
            "current_worktree_path": str(worktree_root),
        },
        question="test question",
    )
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")

    result = _aggregate_review_findings(ctx, prev)

    assert result.status == "ok", (
        f"AC5: _aggregate_review_findings must return ok, got {result.status!r}: "
        f"{result.error!r}"
    )

    data = result.data or {}
    verified_findings = data.get("verified_findings", [])
    matches = [f for f in verified_findings if f.get("title") == "ScratchpadOnlyFinding"]

    assert matches, (
        f"AC5: expected a finding titled 'ScratchpadOnlyFinding' in verified_findings. "
        f"Got titles: {[f.get('title') for f in verified_findings]}"
    )

    assert matches[0].get("verify_status") == "verified-exact", (
        f"AC5: expected verify_status=='verified-exact' for the scratchpad-only "
        f"artifact citation once fallback_dir=scratchpad is threaded at the call "
        f"site. Got {matches[0].get('verify_status')!r} "
        f"(reason={matches[0].get('verify_reason')!r}). "
        f"Pre-GREEN: base_dir=worktree_root only -> artifact absent there -> "
        f"suspect-file-not-found."
    )

    findings_audit = data.get("findings_audit", {})
    assert findings_audit.get("match_kinds", {}).get("verified-exact", 0) >= 1, (
        f"AC5: expected findings_audit.match_kinds to record >=1 'verified-exact' "
        f"entry. Got match_kinds={findings_audit.get('match_kinds')!r}."
    )


# ─── AC6: engine._manifest_paths_from_result helper ───────────────────────────


@pytest.mark.parametrize(
    "data,expected",
    [
        pytest.param(
            {"worker_written_paths": ["a.txt", "b.txt"], "manifest_source": "harness_tool_record"},
            {"a.txt", "b.txt"},
            id="valid-manifest",
        ),
        pytest.param(None, None, id="data-none"),
        pytest.param(
            {"manifest_source": "harness_tool_record"},
            None,
            id="missing-worker_written_paths",
        ),
        pytest.param(
            {"worker_written_paths": "x", "manifest_source": "harness_tool_record"},
            None,
            id="non-list-worker_written_paths",
        ),
    ],
)
def test_ac6_manifest_paths_from_result(data, expected):
    """AC6: _manifest_paths_from_result returns set(worker_written_paths) for a
    valid manifest dict; returns None for data=None / missing field / malformed
    (non-list) field.

    §1q-ext (D1CF5FDF): imported INSIDE the test body — the symbol does not
    exist pre-GREEN, so this raises ImportError at assert-time, not collect-time.
    """
    from engine import _manifest_paths_from_result  # noqa: PLC0415

    result = StepResult(status="ok", data=data, duration_ms=0, step_name="x")

    got = _manifest_paths_from_result(result)

    assert got == expected, (
        f"AC6: expected {expected!r} for data={data!r}, got {got!r}."
    )


# ─── AC7-AC11: engine files_touched manifest-intersection ─────────────────────


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def make_ctx(git_cwd: str | None = None) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"git_cwd": git_cwd} if git_cwd else None,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """Initialise a git repo in tmp_path with one committed file, chdir into it."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n")
    _git(tmp_path, "add", "seed.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path.resolve())
    return tmp_path.resolve()


def make_step_with_data(name: str, fn, data):
    """StepContract whose execute() mutates the filesystem via fn(), then
    returns a StepResult carrying `data` (used to smuggle manifest fields
    for AC7-AC11)."""
    def _run(_ctx, _prev):
        fn()
        return StepResult(status="ok", data=data, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def test_ac7_engine_filters_files_touched_to_manifest_paths(git_repo):
    """AC7: manifest lists only fileA while fileA+fileB changed -> emitted
    payload paths==["fileA.txt"], source=="manifest", n_scan_only==1."""
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def add_both():
        (git_repo / "fileA.txt").write_text("a\n")
        (git_repo / "fileB.txt").write_text("b\n")

    data = {"worker_written_paths": ["fileA.txt"], "manifest_source": "harness_tool_record"}
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_with_data("manifest_step", add_both, data)],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1
    payload = touched[0][1]

    assert payload["paths"] == ["fileA.txt"], (
        f"AC7: expected manifest-filtered paths==['fileA.txt'], got {payload['paths']!r}. "
        f"Pre-GREEN: no manifest filtering -> both scan-delta paths present."
    )
    assert payload.get("source") == "manifest", (
        f"AC7: expected additive source=='manifest', got {payload.get('source')!r}."
    )
    assert payload.get("n_scan_only") == 1, (
        f"AC7: expected n_scan_only==1 (fileB.txt scan-only), got {payload.get('n_scan_only')!r}."
    )


def test_ac8_engine_falls_back_to_scan_when_no_manifest(git_repo):
    """AC8: result WITHOUT manifest fields -> paths contains both files,
    source=="scan", "n_scan_only" absent from payload."""
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def add_both():
        (git_repo / "fileA.txt").write_text("a\n")
        (git_repo / "fileB.txt").write_text("b\n")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_with_data("no_manifest_step", add_both, None)],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1
    payload = touched[0][1]

    assert "fileA.txt" in payload["paths"] and "fileB.txt" in payload["paths"]
    assert payload.get("source") == "scan", (
        f"AC8: expected additive source=='scan' when no manifest present, "
        f"got {payload.get('source')!r}."
    )
    assert "n_scan_only" not in payload, (
        f"AC8: n_scan_only must be absent in scan-source mode, got payload={payload!r}."
    )


def test_ac9_engine_manifest_filter_can_produce_empty_paths(git_repo):
    """AC9: manifest lists only an UNCHANGED path while A+B actually changed ->
    event still emitted with paths==[], n_scan_only==2, source=="manifest"."""
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def add_both():
        (git_repo / "fileA.txt").write_text("a\n")
        (git_repo / "fileB.txt").write_text("b\n")

    data = {"worker_written_paths": ["untouched.txt"], "manifest_source": "harness_tool_record"}
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_with_data("empty_filter_step", add_both, data)],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1, (
        "AC9: a manifest-filtered event with paths==[] is still a non-empty "
        "delta observation and must be emitted (suppress-empty invariant is "
        "about the underlying scan delta, not the filtered paths list)."
    )
    payload = touched[0][1]

    assert payload["paths"] == [], (
        f"AC9: expected manifest-filtered paths==[] (neither changed file is in "
        f"the manifest), got {payload['paths']!r}."
    )
    assert payload.get("n_scan_only") == 2, (
        f"AC9: expected n_scan_only==2 (both fileA.txt+fileB.txt scan-only), "
        f"got {payload.get('n_scan_only')!r}."
    )
    assert payload.get("source") == "manifest"


def test_ac10_no_delta_suppresses_event_even_with_manifest(git_repo):
    """AC10: no working-tree delta (manifest present) -> NO files_touched event
    (suppress-empty-event invariant preserved)."""
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def noop():
        pass

    data = {"worker_written_paths": ["fileA.txt"], "manifest_source": "harness_tool_record"}
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_with_data("noop_manifest_step", noop, data)],
    ))
    eng.execute("wf", make_ctx(), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert touched == [], (
        f"AC10: expected no files_touched event when there is no working-tree "
        f"delta, even though a manifest is present. Got: {touched!r}"
    )


def test_ac11_malformed_manifest_defers_to_scan_without_raising(git_repo):
    """AC11: worker_written_paths malformed ("notalist") -> behaves as AC8
    (source=="scan", full scan paths, no raise) — §1n DEFER-to-scan-fallback."""
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def add_both():
        (git_repo / "fileA.txt").write_text("a\n")
        (git_repo / "fileB.txt").write_text("b\n")

    data = {"worker_written_paths": "notalist", "manifest_source": "harness_tool_record"}
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_with_data("malformed_manifest_step", add_both, data)],
    ))
    # Must not raise — malformed manifest DEFERs to scan-source, never crashes
    # the step loop.
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1
    payload = touched[0][1]

    assert "fileA.txt" in payload["paths"] and "fileB.txt" in payload["paths"]
    assert payload.get("source") == "scan", (
        f"AC11: expected DEFER-to-scan behavior (source=='scan') for a malformed "
        f"manifest, got {payload.get('source')!r}."
    )
    assert "n_scan_only" not in payload
