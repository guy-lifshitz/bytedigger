"""RED tests for agreement 467E0B3B (HIGH, 2026-05-01).

Bug: phase_6 review aggregator marks valid sub-reviewer citations as
``[UNVERIFIED: path not found]`` when the citation uses ``~/`` prefix.

Observed in /build forge-1777619936-4d345d8e (2026-05-01): all 5 sub-reviewer
findings on ``SYSTEM/hooks/subagent-start-track.ts`` were demoted to
UNVERIFIED. The satisfaction evaluator had to independently re-verify
source to confirm fixes landed.

Root cause: ``check_citation`` in ``lib/plugins/anti_hallucination/helper.py``
resolves non-absolute paths via ``Path(cwd) / path_str``. When sub-reviewers
cite paths as ``~/.claude/SYSTEM/foo.ts`` (because that's how the path
surfaces in their tool calls), the regex ``_EVIDENCE_QUOTE_RE`` extracts the
literal string ``~/.claude/SYSTEM/foo.ts`` — which neither starts with ``/``
nor expands. Resolution becomes ``<cwd>/~/.claude/SYSTEM/foo.ts``, which
doesn't exist on disk, hence UNVERIFIED.

Fix: expand ``~`` in path strings before resolution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

from bytedigger_engine.contracts import StepResult  # noqa: E402

from bytedigger_engine.lib.plugins.anti_hallucination.helper import (  # noqa: E402
    check_citation,
    verify_validation_doc,
)


class _FakeCtx:
    def __init__(self, cwd: Path | None = None):
        self.cwd = str(cwd) if cwd is not None else None


def _make_prev(data):
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="prev_step",
    )


def test_check_citation_resolves_tilde_prefixed_path(tmp_path, monkeypatch):
    """Tilde-prefixed citation must be expanded before existence check.

    Reproduces forge-1777619936 failure mode: sub-reviewer cites
    ``~/.claude/SYSTEM/foo.ts``, file exists, citation valid → must return
    ``None`` (no tag), not ``[UNVERIFIED: path not found]``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    subdir = tmp_path / "claude_root" / "SYSTEM" / "hooks"
    subdir.mkdir(parents=True)
    target = subdir / "subagent-start-track.ts"
    target.write_text("#!/usr/bin/env bun\nconst x = 1;\n", encoding="utf-8")

    # ctx.cwd is set to a directory that does NOT contain the file via
    # cwd-relative resolution. Only tilde-expansion can reach the file.
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    ctx = _FakeCtx(cwd=other_cwd)

    result = check_citation(
        "~/claude_root/SYSTEM/hooks/subagent-start-track.ts",
        1,
        "#!/usr/bin/env bun",
        ctx,
    )
    assert result is None, (
        f"tilde-prefixed citation must resolve cleanly; got {result!r}"
    )


def test_check_citation_tilde_path_quote_mismatch_still_detected(
    tmp_path, monkeypatch
):
    """Regression: tilde expansion must not bypass other verification steps.

    After expansion, line/quote mismatch must still surface as UNVERIFIED.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    target = tmp_path / "file.txt"
    target.write_text("real content\n", encoding="utf-8")
    ctx = _FakeCtx(cwd=tmp_path / "elsewhere_unused")

    # Path resolves; quote mismatches.
    result = check_citation("~/file.txt", 1, "fabricated quote", ctx)
    assert result == "[verify: suspect-no-match]", (
        f"expected quote-mismatch tag, got {result!r}"
    )


def test_check_citation_absolute_path_unchanged(tmp_path):
    """Regression: absolute path resolution remains unchanged after tilde fix."""
    target = tmp_path / "abs.txt"
    target.write_text("absolute line\n", encoding="utf-8")
    ctx = _FakeCtx()

    result = check_citation(str(target), 1, "absolute line", ctx)
    assert result is None, f"absolute path must resolve cleanly; got {result!r}"


def test_check_citation_cwd_relative_path_unchanged(tmp_path):
    """Regression: cwd-relative path resolution remains unchanged after fix."""
    subdir = tmp_path / "sub"
    subdir.mkdir()
    target = subdir / "rel.txt"
    target.write_text("relative line\n", encoding="utf-8")
    ctx = _FakeCtx(cwd=tmp_path)

    result = check_citation("sub/rel.txt", 1, "relative line", ctx)
    assert result is None, f"cwd-relative path must resolve; got {result!r}"


def test_check_citation_tilde_path_missing_file_unverified(
    tmp_path, monkeypatch
):
    """Regression: tilde expansion must still catch genuinely missing files."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ctx = _FakeCtx(cwd=tmp_path)

    result = check_citation("~/nonexistent.txt", 1, "any quote", ctx)
    assert result == "[verify: suspect-file-not-found]", (
        f"missing tilde file must surface as path-not-found; got {result!r}"
    )


def test_verify_validation_doc_resolves_tilde_citation(tmp_path, monkeypatch):
    """Dual-callsite guard: ``check_citation`` is also called by
    ``verify_validation_doc`` (phase_5_validation post-processor).

    Locks the invariant that any future refactor exposing tilde-aware
    resolution must apply to BOTH callsites — otherwise a phase_5_validation
    citation citing ``~/.claude/...`` would silently get demoted.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    cited_dir = tmp_path / "src"
    cited_dir.mkdir()
    cited_file = cited_dir / "code.py"
    cited_file.write_text("first valid line\nsecond line\n", encoding="utf-8")

    doc_path = tmp_path / "elsewhere" / "validation.md"
    doc_path.parent.mkdir()
    doc_path.write_text(
        "## Forward Map\n"
        "\n"
        "- AC1 → src/code.py:1\n"
        "\n"
        "## Reverse Map\n"
        "\n"
        "- src/code.py:1 covered by tests/test_x.py\n"
        "\n"
        "## Spec Compliance\n"
        "\n"
        "Citation:\n"
        "> ~/src/code.py:1: first valid line\n"
        "\n"
        "## Quality Findings\n"
        "\n"
        "(none)\n",
        encoding="utf-8",
    )

    other_cwd = tmp_path / "engine_cwd"
    other_cwd.mkdir()
    ctx = _FakeCtx(cwd=other_cwd)
    prev = _make_prev(
        {
            "validation_doc_path": str(doc_path),
            "red_test_paths": [],
        }
    )

    result = verify_validation_doc(ctx, prev)

    assert result.status == "ok", (
        f"unexpected status {result.status!r}; error={result.error!r}"
    )
    assert result.data["verify_verified"] == 1, (
        f"tilde citation must be verified, got counters={result.data!r}"
    )
    assert result.data["verify_unverified"] == 0, (
        f"tilde citation must NOT be demoted, got counters={result.data!r}"
    )
