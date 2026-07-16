"""RED tests for HAL agreement 3F5599A6 (GH279 slice 2) — citation-verifier
fallback resolution in `_verify_spec_citations`.

New module-level helper `_resolve_citation_target(path_str, git_cwd,
git_cwd_resolved, scratchpad_root, suffix_map)` does not exist yet. Every
test below drives the EXISTING `_verify_spec_citations` (phase_45_spec.py)
directly with real on-disk fixtures (real git repos where the suffix-map
fallback matters) and asserts on the new fallback semantics: bare-filename
resolution via a git suffix map, scratchpad-relative resolution, git_cwd
precedence, and an additive `resolved_via` key on WARNING findings.

These tests MUST fail today: prod resolves citations against `git_cwd` only
(single-root resolve/exists at phase_45_spec.py:1656-1669) with no fallback
chain and no `resolved_via` key. `test_ac9_...` presence-gates the missing
helper at assert time (not import time) per D1CF5FDF.

GREEN step (Option-D manual subagent flow, spec 3F5599A6) implements the
helper and rewires the loop per spec §2.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import phase_45_spec
from contracts import StepResult


# ─── fixture helpers ──────────────────────────────────────────────────────────


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


def test_ac1_bare_unique_resolves_via_suffix_map(tmp_path):
    """Bare unique basename citation resolves via the git suffix map."""
    repo = _init_git_repo(tmp_path / "repo", {"pkg/tools.py": "1\n2\n3\n4\n5\n"})
    spec = _write_spec(tmp_path / "spec", "## Context\nSee `tools.py:3` for detail.\n")
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARNING"
    assert findings[0].get("resolved_via") == "suffix_map"


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_ambiguous_basename_stays_error(tmp_path):
    """Ambiguous basename (two committed files share it) stays ERROR."""
    repo = _init_git_repo(
        tmp_path / "repo",
        {"a/dup.py": "1\n2\n", "b/dup.py": "1\n2\n"},
    )
    spec = _write_spec(tmp_path / "spec", "## Context\nSee `dup.py:1` for detail.\n")
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED"
    findings_structured = result.data.get("findings_structured") or []
    error_reasons = [f["reason"] for f in findings_structured if f["severity"] == "ERROR"]
    assert "file does not exist" in error_reasons
    # Forcing-fn: the new fallback helper must exist (fails today, D1CF5FDF-safe).
    assert getattr(phase_45_spec, "_resolve_citation_target", None) is not None


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_scratchpad_artifact_resolves(tmp_path):
    """Pipeline-artifact citation under scratchpad_dir resolves via fallback."""
    git_cwd = tmp_path / "gitcwd"
    git_cwd.mkdir()
    scratchpad = tmp_path / "scratchpad"
    artifact = scratchpad / "research" / "discovery.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("l1\nl2\nl3\n", encoding="utf-8")

    spec = _write_spec(
        tmp_path / "spec", "## Context\nSee `research/discovery.md:2` for detail.\n"
    )
    ctx = SimpleNamespace(org_config={
        "git_cwd": str(git_cwd),
        "scratchpad_dir": str(scratchpad),
        "complexity": "SIMPLE",
    })
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARNING"
    assert findings[0].get("resolved_via") == "scratchpad"


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_scratchpad_line_range_enforced(tmp_path):
    """Scratchpad-resolved file still enforces the line-range check."""
    git_cwd = tmp_path / "gitcwd"
    git_cwd.mkdir()
    scratchpad = tmp_path / "scratchpad"
    artifact = scratchpad / "research" / "discovery.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("l1\nl2\nl3\n", encoding="utf-8")

    spec = _write_spec(
        tmp_path / "spec", "## Context\nSee `research/discovery.md:99` for detail.\n"
    )
    ctx = SimpleNamespace(org_config={
        "git_cwd": str(git_cwd),
        "scratchpad_dir": str(scratchpad),
        "complexity": "SIMPLE",
    })
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED"
    findings_structured = result.data.get("findings_structured") or []
    reasons = [f["reason"] for f in findings_structured if f["severity"] == "ERROR"]
    assert "line 99 > total 3" in reasons


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_no_scratchpad_dir_graceful(tmp_path):
    """org_config without scratchpad_dir must not raise; degrades to ERROR."""
    git_cwd = tmp_path / "gitcwd"
    git_cwd.mkdir()
    spec = _write_spec(tmp_path / "spec", "## Context\nSee `missing.md:1` for detail.\n")
    ctx = SimpleNamespace(org_config={"git_cwd": str(git_cwd), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED"
    findings_structured = result.data.get("findings_structured") or []
    reasons = [f["reason"] for f in findings_structured if f["severity"] == "ERROR"]
    assert "file does not exist" in reasons
    # Forcing-fn: the new fallback helper must exist (fails today, D1CF5FDF-safe).
    assert getattr(phase_45_spec, "_resolve_citation_target", None) is not None


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_escape_skip_preserved(tmp_path):
    """Path-escape guard skips citations climbing out of every attempted root."""
    git_cwd = tmp_path / "gitcwd"
    git_cwd.mkdir()
    scratchpad = tmp_path / "otherscratch"
    scratchpad.mkdir()
    (tmp_path / "outside.md").write_text("l1\n", encoding="utf-8")

    spec = _write_spec(tmp_path / "spec", "## Context\nSee `../outside.md:1` for detail.\n")
    ctx = SimpleNamespace(org_config={
        "git_cwd": str(git_cwd),
        "scratchpad_dir": str(scratchpad),
        "complexity": "SIMPLE",
    })
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok"
    assert result.data.get("citation_findings") == []
    # Forcing-fn: the new fallback helper must exist (fails today, D1CF5FDF-safe).
    assert getattr(phase_45_spec, "_resolve_citation_target", None) is not None


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_git_cwd_precedence_over_scratchpad(tmp_path):
    """When both roots contain the citation path, git_cwd wins."""
    git_cwd = tmp_path / "gitcwd"
    gc_artifact = git_cwd / "research" / "discovery.md"
    gc_artifact.parent.mkdir(parents=True)
    gc_artifact.write_text("\n".join(str(i) for i in range(1, 11)) + "\n", encoding="utf-8")

    scratchpad = tmp_path / "scratchpad"
    sp_artifact = scratchpad / "research" / "discovery.md"
    sp_artifact.parent.mkdir(parents=True)
    sp_artifact.write_text("only-one-line\n", encoding="utf-8")

    spec = _write_spec(
        tmp_path / "spec", "## Context\nSee `research/discovery.md:5` for detail.\n"
    )
    ctx = SimpleNamespace(org_config={
        "git_cwd": str(git_cwd),
        "scratchpad_dir": str(scratchpad),
        "complexity": "SIMPLE",
    })
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    assert len(findings) == 1
    assert findings[0].get("resolved_via") == "git_cwd"


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_lazy_suffix_map_spy_counts(tmp_path, monkeypatch):
    """Suffix map is built lazily (0 misses -> 0 git_read calls) and memoized
    once per verify call (2 misses -> exactly 1 git_read call)."""
    original_git_read = phase_45_spec.git_read

    def _make_wrapper(counter):
        def _wrapped(*args, **kwargs):
            counter["n"] += 1
            return original_git_read(*args, **kwargs)
        return _wrapped

    # (a) citation hits directly under git_cwd -> zero git_read calls.
    repo_a = _init_git_repo(tmp_path / "repo_a", {"pkg/tools.py": "1\n2\n3\n"})
    spec_a = _write_spec(tmp_path / "spec_a", "## Context\nSee `pkg/tools.py:1` here.\n")
    ctx_a = SimpleNamespace(org_config={"git_cwd": str(repo_a), "complexity": "SIMPLE"})
    counter_a = {"n": 0}
    monkeypatch.setattr(phase_45_spec, "git_read", _make_wrapper(counter_a))

    result_a = phase_45_spec._verify_spec_citations(ctx_a, _make_prev(spec_a))

    assert result_a.status == "ok", f"AC8a setup: got {result_a.status}/{result_a.error_code}"
    assert counter_a["n"] == 0, f"expected 0 git_read calls, got {counter_a['n']}"

    # (b) two bare-missing citations resolved via nested files -> the lazy
    # suffix-map memo is built once and reused -> exactly 1 git_read call.
    repo_b = _init_git_repo(
        tmp_path / "repo_b",
        {"nested/alpha.py": "1\n2\n", "nested2/beta.py": "1\n2\n"},
    )
    spec_b = _write_spec(
        tmp_path / "spec_b", "## Context\nSee `alpha.py:1` and `beta.py:1` here.\n"
    )
    ctx_b = SimpleNamespace(org_config={"git_cwd": str(repo_b), "complexity": "SIMPLE"})
    counter_b = {"n": 0}
    monkeypatch.setattr(phase_45_spec, "git_read", _make_wrapper(counter_b))

    phase_45_spec._verify_spec_citations(ctx_b, _make_prev(spec_b))

    assert counter_b["n"] == 1, f"expected exactly 1 git_read call, got {counter_b['n']}"


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_resolve_citation_target_helper_unit(tmp_path):
    """Direct unit test of the new helper's suffix-map resolution branch."""
    fn = getattr(phase_45_spec, "_resolve_citation_target", None)
    assert fn is not None, "helper missing"

    git_cwd = tmp_path / "repo"
    tools_file = git_cwd / "pkg" / "tools.py"
    tools_file.parent.mkdir(parents=True)
    tools_file.write_text("1\n2\n3\n", encoding="utf-8")
    git_cwd_resolved = git_cwd.resolve()

    resolved_path, resolved_via = fn(
        "tools.py", git_cwd, git_cwd_resolved, None, {"tools.py": "pkg/tools.py"},
    )

    assert resolved_path == tools_file.resolve()
    assert resolved_via == "suffix_map"


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_direct_hit_warning_shape_pinned(tmp_path):
    """Repo-rooted citation resolved directly under git_cwd carries
    resolved_via='git_cwd' alongside the unchanged WARNING reason string."""
    repo = _init_git_repo(tmp_path / "repo", {"pkg/tools.py": "1\n2\n3\n4\n5\n"})
    spec = _write_spec(tmp_path / "spec", "## Context\nSee `pkg/tools.py:3` for detail.\n")
    ctx = SimpleNamespace(org_config={"git_cwd": str(repo), "complexity": "SIMPLE"})
    prev = _make_prev(spec)

    result = phase_45_spec._verify_spec_citations(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}/{result.error_code}"
    findings = result.data.get("citation_findings") or []
    assert len(findings) == 1
    assert findings[0]["reason"] == "exists; symbol-proximity not checked (A813CA08)"
    assert findings[0].get("resolved_via") == "git_cwd"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
