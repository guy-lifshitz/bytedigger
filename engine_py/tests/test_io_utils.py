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

# atomic_write under test — module does NOT exist yet (RED).
from bytedigger_engine import io_utils  # noqa: E402  (this import fails RED until GREEN ships)


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


# phase_1 / phase_4 import-surface tests retired by bd#89 P2a (modules deleted).


def test_phase_7_synthesize_imports_atomic_write():
    """AC4: from bytedigger_engine.workflows.phase_7_synthesize import atomic_write."""
    from bytedigger_engine.workflows.phase_7_synthesize import atomic_write  # noqa: F401


def test_phase_45_spec_imports_atomic_write():
    """AC4: from bytedigger_engine.workflows.phase_45_spec import atomic_write."""
    from bytedigger_engine.workflows.phase_45_spec import atomic_write  # noqa: F401



# ─── AC5: phases 1/4/7 USE atomic_write at write site (spy via monkeypatch) ──


# phase_1 / phase_4 AC5 write-site spies retired by bd#89 P2a (modules deleted).


def test_phase_7_write_post_deploy_report_uses_atomic_write(tmp_path, monkeypatch):
    """AC5 (bd#89 P3c re-point): the phase_7 ``write_post_deploy_report`` step writes
    the report through atomic_write with (doc_path, content), exactly once.
    """
    from bytedigger_engine.workflows import phase_7_synthesize
    from bytedigger_engine.contracts import WorkflowContext

    calls = []

    def spy(path, content):
        calls.append((Path(path), content))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    monkeypatch.setattr(phase_7_synthesize, "atomic_write", spy)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="Add foo to bar", session_id="s", persona="hal",
        framework=None, domain=None,
    )
    wf = phase_7_synthesize.phase_7_synthesize_workflow()
    step = next((s for s in wf.steps if s.name == "write_post_deploy_report"), None)
    assert step is not None, f"no write_post_deploy_report step; steps: {[s.name for s in wf.steps]}"
    step.execute(ctx, None)

    assert len(calls) == 1, f"expected atomic_write called once, got {len(calls)}"
    called_path, called_content = calls[0]
    assert called_path == scratch.resolve() / "post-deploy" / "post-deploy-report.md"
    assert called_content.startswith("# Post-Deploy Report")


# ─── AC6: phase_45_spec and _lite no longer define _atomic_write ─────────────


def test_phase_45_spec_does_not_define_underscore_atomic_write():
    """AC6: after GREEN, phase_45_spec has no module-local _atomic_write."""
    from bytedigger_engine.workflows import phase_45_spec
    assert not hasattr(phase_45_spec, "_atomic_write"), (
        "phase_45_spec must NOT define _atomic_write after GREEN — use io_utils.atomic_write"
    )
