"""RED tests for HAL spec GH954 (2026-07-17_GH954_cite_neighbor_resolve_spec.md) —
neighbor-dir citation resolution + fuzzy-suggest before fabrication verdict in
`_verify_spec_citations` (phase_45_spec.py).

New module-level helpers `_neighbor_dir_resolve(path_str, resolved_dirs,
git_cwd, git_cwd_resolved)` and `_citation_suggestions(path_str, git_cwd)` do
not exist yet. Every test drives the EXISTING `_verify_spec_citations`
directly with real on-disk fixtures (real git repos, per the sibling
3F5599A6 pattern) and asserts on the new pass-2 neighbor-retry + candidates
semantics. Not-yet-existing symbols are presence-gated INSIDE test bodies
(getattr), never imported at module top level, per D1CF5FDF / §1q ext, so
the file always COLLECTS and fails at assert time.

These tests MUST fail today: prod's `_verify_spec_citations` has no pass-2
retry loop, no `resolved_dirs` accumulation, and emits ERROR findings with
NO `candidates` key ever — `_neighbor_dir_resolve`/`_citation_suggestions`
are absent from phase_45_spec. `test_ac10_...` presence-gates the missing
helpers at assert time.

GREEN step (Option-D manual subagent flow, spec GH954) implements the
helpers and wires the two-pass loop per spec §2.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine.workflows import phase_45_spec
from bytedigger_engine.contracts import StepResult


# ─── fixture helpers (mirrors test_phase_45_spec_cite_fallback_3F5599A6.py) ──


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=red-agent@example.com", "-c", "user.name=RED Agent", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


def _init_git_repo(root: Path, files: dict[str, str]) -> Path:
    """Create `root`, write+commit `files` (relpath -> content) as a real repo."""
    root.mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _write_spec(dir_: Path, body: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    spec_path = dir_ / "build-spec.md"
    spec_path.write_text(body, encoding="utf-8")
    return spec_path


def _make_prev(spec_path: Path, cycle: int = 1) -> StepResult:
    return StepResult(
        status="ok",
        data={"spec_path": str(spec_path), "cycle": cycle},
        duration_ms=0,
        step_name="write_spec_doc",
    )


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_repro_954_neighbor_dir_resolves_ambiguous_basename(tmp_path):
    """#954 repro: sibling cite resolves via git_cwd, ambiguous bare basename
    in a DIFFERENT dir then resolves via the neighbor-dir retry."""
    repo = _init_git_repo(
        tmp_path / "repo",
        {
            "experiments/pc_ab/mod.py": "1\n2\n",
            "experiments/pc_ab/temporal.py": "1\n2\n3\n",
            "other/temporal.py": "1\n2\n3\n",
        },
    )
    spec = _write_spec(
        tmp_path / "spec",
        "## Context\nSee `experiments/pc_ab/mod.py:1` and `temporal.py:1` for detail.\n",
    )
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    temporal = [f for f in findings if f["path"] == "temporal.py"]
    assert len(temporal) == 1, f"expected one temporal.py finding, got {findings}"
    assert temporal[0]["severity"] == "WARNING", f"expected WARNING, got {temporal[0]}"
    assert temporal[0].get("resolved_via") == "neighbor_dir", (
        f"expected resolved_via=neighbor_dir, got {temporal[0]}"
    )


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_neighbor_ambiguity_stays_error_with_sorted_candidates(tmp_path):
    """Two resolved dirs both containing dup.py -> stays ERROR, reason
    EXACTLY 'file does not exist', candidates lists both rel-paths sorted."""
    repo = _init_git_repo(
        tmp_path / "repo",
        {
            "a/anchor.py": "1\n",
            "b/anchor2.py": "1\n",
            "a/dup.py": "1\n2\n",
            "b/dup.py": "1\n2\n",
        },
    )
    spec = _write_spec(
        tmp_path / "spec",
        "## Context\nSee `a/anchor.py:1` and `b/anchor2.py:1` and `dup.py:1` for detail.\n",
    )
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"expected E_SPEC_CITATION_MALFORMED, got {result.status}/{result.error_code}"
    )
    findings_structured = result.data.get("findings_structured") or []
    dup_findings = [f for f in findings_structured if f["path"] == "dup.py"]
    assert len(dup_findings) == 1, f"expected one dup.py finding, got {findings_structured}"
    dup = dup_findings[0]
    assert dup["reason"] == "file does not exist", f"reason mismatch: {dup}"
    assert dup.get("candidates") == ["a/dup.py", "b/dup.py"], f"candidates mismatch: {dup}"


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_no_neighbor_cites_ambiguous_tracked_basename_suggests(tmp_path):
    """No neighbor cites at all; ambiguous tracked basename -> ERROR +
    candidates == sorted basename matches from git ls-files (<=5)."""
    repo = _init_git_repo(
        tmp_path / "repo",
        {
            "z/widget.py": "1\n",
            "y/widget.py": "1\n",
        },
    )
    spec = _write_spec(tmp_path / "spec", "## Context\nSee `widget.py:1` for detail.\n")
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"expected E_SPEC_CITATION_MALFORMED, got {result.status}/{result.error_code}"
    )
    findings_structured = result.data.get("findings_structured") or []
    widget_findings = [f for f in findings_structured if f["path"] == "widget.py"]
    assert len(widget_findings) == 1
    widget = widget_findings[0]
    assert widget["reason"] == "file does not exist", f"reason mismatch: {widget}"
    assert widget.get("candidates") == ["y/widget.py", "z/widget.py"], (
        f"candidates mismatch: {widget}"
    )


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_no_candidates_anywhere_key_absent(tmp_path):
    """Miss with NO candidates anywhere -> ERROR finding has NO 'candidates'
    key (absent, not empty list)."""
    repo = _init_git_repo(tmp_path / "repo", {"pkg/tools.py": "1\n2\n"})
    spec = _write_spec(
        tmp_path / "spec", "## Context\nSee `totally_nonexistent_thing.py:1` for detail.\n"
    )
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"expected E_SPEC_CITATION_MALFORMED, got {result.status}/{result.error_code}"
    )
    findings_structured = result.data.get("findings_structured") or []
    misses = [f for f in findings_structured if f["path"] == "totally_nonexistent_thing.py"]
    assert len(misses) == 1
    miss = misses[0]
    assert miss["reason"] == "file does not exist", f"reason mismatch: {miss}"
    assert "candidates" not in miss, f"expected 'candidates' key absent, got {miss}"


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_untracked_file_in_neighbor_dir_resolves(tmp_path):
    """Untracked file in a neighbor dir resolves via filesystem check (not
    git) -> WARNING resolved_via=neighbor_dir."""
    repo = _init_git_repo(
        tmp_path / "repo",
        {"a/anchor.py": "1\n"},
    )
    # Untracked sibling file dropped into the same resolved dir AFTER commit —
    # never `git add`ed, so it cannot appear in a suffix map or ls-files.
    (repo / "a" / "untracked.py").write_text("1\n2\n", encoding="utf-8")

    spec = _write_spec(
        tmp_path / "spec",
        "## Context\nSee `a/anchor.py:1` and `untracked.py:1` for detail.\n",
    )
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    untracked = [f for f in findings if f["path"] == "untracked.py"]
    assert len(untracked) == 1, f"expected one untracked.py finding, got {findings}"
    assert untracked[0]["severity"] == "WARNING", f"expected WARNING, got {untracked[0]}"
    assert untracked[0].get("resolved_via") == "neighbor_dir", (
        f"expected resolved_via=neighbor_dir, got {untracked[0]}"
    )


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_neighbor_dir_hit_out_of_range_line_still_errors(tmp_path):
    """Neighbor-dir hit with out-of-range line -> ERROR 'line N > total M'
    (line check still applies after neighbor resolution). `short.py` is
    globally ambiguous (two committed files share the basename) so the
    EXISTING suffix_map fallback drops it — only the NEW pass-2 neighbor-dir
    retry (scoped to the single resolved dir `a/`) can find the unique hit."""
    repo = _init_git_repo(
        tmp_path / "repo",
        {"a/anchor.py": "1\n", "a/short.py": "1\n2\n3\n", "b/short.py": "1\n2\n3\n4\n"},
    )
    spec = _write_spec(
        tmp_path / "spec",
        "## Context\nSee `a/anchor.py:1` and `short.py:99` for detail.\n",
    )
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"expected E_SPEC_CITATION_MALFORMED, got {result.status}/{result.error_code}"
    )
    findings_structured = result.data.get("findings_structured") or []
    short_findings = [f for f in findings_structured if f["path"] == "short.py"]
    assert len(short_findings) == 1
    assert short_findings[0]["reason"] == "line 99 > total 3", (
        f"reason mismatch: {short_findings[0]}"
    )


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_escape_guard_preserved_with_resolved_dirs_populated(tmp_path):
    """Escape-guard citation is still silently skipped even with
    resolved_dirs non-empty; a neighbor-dir candidate escaping git_cwd is
    skipped too."""
    repo = _init_git_repo(
        tmp_path / "repo",
        {"a/anchor.py": "1\n"},
    )
    (tmp_path / "outside.md").write_text("l1\n", encoding="utf-8")

    spec = _write_spec(
        tmp_path / "spec",
        "## Context\nSee `a/anchor.py:1` and `../outside.md:1` for detail.\n",
    )
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    escape_findings = [f for f in findings if f["path"] == "../outside.md"]
    assert escape_findings == [], f"expected no finding for escape path, got {escape_findings}"


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8a_precedence_git_cwd_over_suffix_and_scratchpad(tmp_path):
    """Precedence regression (git_cwd leg): git_cwd hit -> resolved_via ==
    'git_cwd' — existing 3F5599A6 semantics must stay green."""
    repo = _init_git_repo(tmp_path / "repo", {"pkg/tools.py": "1\n2\n3\n4\n5\n"})
    spec = _write_spec(tmp_path / "spec", "## Context\nSee `pkg/tools.py:3` for detail.\n")
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    assert len(findings) == 1
    assert findings[0].get("resolved_via") == "git_cwd"


def test_ac8b_precedence_unique_suffix_map_hit(tmp_path):
    """Precedence regression (suffix_map leg): unique bare basename resolves
    via suffix_map — existing 3F5599A6 semantics must stay green."""
    repo = _init_git_repo(tmp_path / "repo", {"nested/alpha.py": "1\n2\n"})
    spec = _write_spec(tmp_path / "spec", "## Context\nSee `alpha.py:1` here.\n")
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    assert len(findings) == 1
    assert findings[0].get("resolved_via") == "suffix_map"


def test_ac8c_scratchpad_resolved_dirs_excluded_from_neighbor_retry(tmp_path):
    """Precedence regression (scratchpad leg): a scratchpad-resolved citation
    must NOT seed resolved_dirs — a same-basename ambiguous citation must
    still fall through to ERROR, not spuriously resolve via the scratchpad's
    directory treated as a neighbor dir."""
    git_cwd = tmp_path / "gitcwd"
    git_cwd.mkdir()
    scratchpad = tmp_path / "scratchpad"
    artifact = scratchpad / "research" / "discovery.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("l1\nl2\nl3\n", encoding="utf-8")
    # Same basename living ONLY under the scratchpad dir path, never under
    # git_cwd — if scratchpad dirs wrongly seeded resolved_dirs this would
    # resolve via a bogus neighbor_dir hit; it must NOT (the dir doesn't
    # exist relative to git_cwd at all), so it must stay ERROR.
    spec = _write_spec(
        tmp_path / "spec",
        "## Context\nSee `research/discovery.md:2` and `research/other.md:1` for detail.\n",
    )
    ctx = SimpleNamespace(org_config={
        "git_cwd": str(git_cwd),
        "scratchpad_dir": str(scratchpad),
        "complexity": "SIMPLE",
    })
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"expected E_SPEC_CITATION_MALFORMED, got {result.status}/{result.error_code}"
    )
    findings_structured = result.data.get("findings_structured") or []
    other_findings = [f for f in findings_structured if f["path"] == "research/other.md"]
    assert len(other_findings) == 1, f"expected other.md to stay a miss, got {findings_structured}"
    assert other_findings[0]["reason"] == "file does not exist"


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_render_citation_findings_appends_did_you_mean_suffix():
    """_render_citation_findings appends ' (did you mean: a/x.py, b/x.py?)'
    when a finding carries non-empty candidates; renders unchanged without."""
    finding_with_candidates = {
        "path": "x.py",
        "line": 1,
        "severity": "ERROR",
        "reason": "file does not exist",
        "candidates": ["a/x.py", "b/x.py"],
    }
    finding_without_candidates = {
        "path": "y.py",
        "line": 2,
        "severity": "ERROR",
        "reason": "file does not exist",
    }

    rendered_with, _ = phase_45_spec._render_citation_findings([finding_with_candidates], cycle=1)
    rendered_without, _ = phase_45_spec._render_citation_findings(
        [finding_without_candidates], cycle=1
    )

    assert "  ERROR: x.py:1 — file does not exist (did you mean: a/x.py, b/x.py?)" in rendered_with, (
        f"missing did-you-mean suffix, got:\n{rendered_with}"
    )
    assert "  ERROR: y.py:2 — file does not exist" in rendered_without
    assert "did you mean" not in rendered_without, f"unexpected suggestion text: {rendered_without}"


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_helpers_exist_and_suggestions_degrade_gracefully_on_git_unavailable(
    tmp_path, monkeypatch
):
    """Helpers exist (declare-style presence) and _citation_suggestions
    returns [] (not raise) when git is unavailable."""
    neighbor_fn = getattr(phase_45_spec, "_neighbor_dir_resolve", None)
    suggest_fn = getattr(phase_45_spec, "_citation_suggestions", None)
    assert neighbor_fn is not None, "_neighbor_dir_resolve helper missing"
    assert suggest_fn is not None, "_citation_suggestions helper missing"

    def _raise_git_unavailable(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(phase_45_spec, "git_read", _raise_git_unavailable)

    result = suggest_fn("anything.py", tmp_path)

    assert result == [], f"expected [] on git-unavailable, got {result}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
