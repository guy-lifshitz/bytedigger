"""Tests for io_utils.atomic_write — 3ECCFF8E.

atomic_write is the shared temp+rename helper used by phase_1/4/7 +
phase_45_spec/_lite for canonical doc writes. Verifies AC1..AC6 of the
3ECCFF8E spec.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

# atomic_write under test — module does NOT exist yet (RED).
import io_utils  # noqa: E402  (this import fails RED until GREEN ships)


# ─── AC1 + AC2: basic callable / happy path ──────────────────────────────────


def test_atomic_write_callable_returns_none(tmp_path):
    """AC1: callable, returns None on success."""
    target = tmp_path / "out.md"
    result = io_utils.atomic_write(target, "hello")
    assert result is None


def test_atomic_write_writes_content(tmp_path):
    """AC2 happy path: target contains content."""
    target = tmp_path / "out.md"
    io_utils.atomic_write(target, "hello\nworld\n")
    assert target.read_text() == "hello\nworld\n"


def test_atomic_write_no_stale_tmp_after_success(tmp_path):
    """AC2 cleanup: no .tmp sibling remains after success."""
    target = tmp_path / "out.md"
    io_utils.atomic_write(target, "x")
    assert not (tmp_path / "out.md.tmp").exists()


# ─── AC3: atomicity invariant ────────────────────────────────────────────────


def test_atomic_write_atomicity_target_unchanged_on_failure(tmp_path, monkeypatch):
    """AC3: when os.replace raises, target file (pre-existing) is unchanged."""
    target = tmp_path / "out.md"
    target.write_text("ORIGINAL", encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated mid-replace kill")

    monkeypatch.setattr(io_utils.os, "replace", boom)

    try:
        io_utils.atomic_write(target, "NEW_CONTENT")
    except OSError:
        pass  # expected — atomic_write propagates the error

    # Target remains with original content.
    assert target.read_text() == "ORIGINAL"


# ─── AC4: import surface across 5 phase modules ──────────────────────────────


def test_phase_1_discovery_imports_atomic_write():
    """AC4: from phase_1_discovery import atomic_write."""
    from phase_1_discovery import atomic_write  # noqa: F401


def test_phase_4_architect_imports_atomic_write():
    """AC4: from phase_4_architect import atomic_write."""
    from phase_4_architect import atomic_write  # noqa: F401


def test_phase_7_synthesize_imports_atomic_write():
    """AC4: from phase_7_synthesize import atomic_write."""
    from phase_7_synthesize import atomic_write  # noqa: F401


def test_phase_45_spec_imports_atomic_write():
    """AC4: from phase_45_spec import atomic_write."""
    from phase_45_spec import atomic_write  # noqa: F401


def test_phase_45_spec_lite_imports_atomic_write():
    """AC4: from phase_45_spec_lite import atomic_write."""
    from phase_45_spec_lite import atomic_write  # noqa: F401


# ─── AC5: phases 1/4/7 USE atomic_write at write site (spy via monkeypatch) ──


def test_phase_1_write_discovery_doc_uses_atomic_write(tmp_path, monkeypatch):
    """AC5: phase_1 _write_discovery_doc invokes atomic_write with (doc_path, content)."""
    import phase_1_discovery
    from contracts import StepResult

    calls = []

    def spy(path, content):
        calls.append((Path(path), content))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    monkeypatch.setattr(phase_1_discovery, "atomic_write", spy)

    raw = "discovery content body\n"
    doc_path = tmp_path / "scratch" / "research" / "discovery.md"
    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "complexity": "SIMPLE",
        },
        duration_ms=0,
        step_name="invoke_discovery_llm",
    )
    from phase_1_discovery import _write_discovery_doc
    _write_discovery_doc(None, prev)

    assert len(calls) == 1, f"expected atomic_write called once, got {len(calls)}"
    called_path, called_content = calls[0]
    assert called_path == doc_path
    assert called_content == raw


def test_phase_4_write_architecture_doc_uses_atomic_write(tmp_path, monkeypatch):
    """AC5: phase_4 _write_architecture_doc invokes atomic_write with (doc_path, content)."""
    import phase_4_architect
    from contracts import StepResult

    calls = []

    def spy(path, content):
        calls.append((Path(path), content))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    monkeypatch.setattr(phase_4_architect, "atomic_write", spy)

    raw = "architecture body\n"
    doc_path = tmp_path / "scratch" / "architecture" / "architecture.md"
    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "security_classification": "LOW",
        },
        duration_ms=0,
        step_name="invoke_architect_llm",
    )
    from phase_4_architect import _write_architecture_doc
    _write_architecture_doc(None, prev)

    assert len(calls) == 1, f"expected atomic_write called once, got {len(calls)}"
    called_path, called_content = calls[0]
    assert called_path == doc_path
    assert called_content == raw


def test_phase_7_write_synthesizer_artifact_uses_atomic_write(tmp_path, monkeypatch):
    """AC5: phase_7 _write_synthesizer_artifact invokes atomic_write with (doc_path, content).

    prev.data shape (from phase_7_synthesize.py:531-542):
      raw_response, doc_path, spec_path, review_doc_path,
      fix_doc_path, satisfaction_doc_path.
    """
    import phase_7_synthesize
    from contracts import StepResult

    calls = []

    def spy(path, content):
        calls.append((Path(path), content))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    monkeypatch.setattr(phase_7_synthesize, "atomic_write", spy)

    raw = "## STATUS: SHIP\nsynthesizer body\n"
    doc_path = tmp_path / "scratch" / "synthesize" / "report.md"
    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "spec_path": str(tmp_path / "spec.md"),
            "review_doc_path": str(tmp_path / "review.md"),
            "fix_doc_path": str(tmp_path / "fix.md"),
            "satisfaction_doc_path": str(tmp_path / "satisfaction.md"),
        },
        duration_ms=0,
        step_name="invoke_synthesizer_llm",
    )
    from phase_7_synthesize import _write_synthesizer_artifact
    _write_synthesizer_artifact(None, prev)

    assert len(calls) == 1, f"expected atomic_write called once, got {len(calls)}"
    called_path, called_content = calls[0]
    assert called_path == doc_path
    assert called_content == raw


# ─── AC6: phase_45_spec and _lite no longer define _atomic_write ─────────────


def test_phase_45_spec_does_not_define_underscore_atomic_write():
    """AC6: after GREEN, phase_45_spec has no module-local _atomic_write."""
    import phase_45_spec
    assert not hasattr(phase_45_spec, "_atomic_write"), (
        "phase_45_spec must NOT define _atomic_write after GREEN — use io_utils.atomic_write"
    )


def test_phase_45_spec_lite_does_not_define_underscore_atomic_write():
    """AC6: after GREEN, phase_45_spec_lite has no module-local _atomic_write."""
    import phase_45_spec_lite
    assert not hasattr(phase_45_spec_lite, "_atomic_write"), (
        "phase_45_spec_lite must NOT define _atomic_write after GREEN"
    )
