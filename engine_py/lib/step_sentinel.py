"""Durable step-result sentinel primitive (GH374 §2.4).

``write_step_sentinel``/``read_step_sentinel`` are the BC45C403 helpers moved
verbatim from ``workflows/phase_6_review.py`` (same signatures, same
``scratchpad / "resume" / resume_sentinel_name(...)`` naming). Plus two
engine-facing helpers so ``engine.py`` can skip/write LLM-step results across
process restarts without importing the workflows layer into core.

Core-clean: no HAL_* env, no host path literals (core-boundary lint applies).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

from contracts import StepResult

_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from resume_keying import resume_sentinel_name  # noqa: E402
from io_utils import atomic_write  # noqa: E402

logger = logging.getLogger(__name__)


def write_step_sentinel(
    scratchpad: "Path",
    step_name: str,
    cycle: int,
    data: dict,
    run_id: str,
    ctx_hash: "str | None" = None,
    workflow_name: "str | None" = None,
) -> None:
    """Persist step result data for durable resume.  Failure is silently degraded."""
    try:
        sentinel_dir = scratchpad / "resume"
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        sentinel_file = sentinel_dir / resume_sentinel_name(step_name, cycle, run_id, ctx_hash, workflow_name)
        atomic_write(sentinel_file, json.dumps(data, default=str))
    except OSError:
        logger.warning("failed to write step sentinel for %s cycle %d", step_name, cycle)


def read_step_sentinel(
    scratchpad: "Path",
    step_name: str,
    cycle: int,
    run_id: str,
    ctx_hash: "str | None" = None,
    workflow_name: "str | None" = None,
) -> "dict | None":
    """Read persisted step result data. Returns None if missing or JSON corrupt."""
    # legacy-named sentinels (pre-run_id key) are simply not found → step re-runs once (idempotent, safe).
    try:
        sentinel_file = scratchpad / "resume" / resume_sentinel_name(step_name, cycle, run_id, ctx_hash, workflow_name)
        return json.loads(sentinel_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def compute_ctx_hash(context) -> "str | None":
    """Hash question + task_description + decision_doc bytes (GH443 part 3 §2.1).

    Missing/unreadable decision_doc degrades silently to empty bytes.

    Returns ``None`` when neither ``org_config["task_description"]`` nor
    ``org_config["decision_doc"]`` is set — i.e. there is nothing meaningful
    to distinguish this context from any other, so the legacy (non-hashed)
    sentinel filename is preserved (GH374 engine-level tests never set
    either field; part-3 scenarios always set at least one).
    """
    cfg = getattr(context, "org_config", None) or {}
    task = str(cfg.get("task_description") or "")
    doc = cfg.get("decision_doc")
    if not task and not doc:
        return None
    question = getattr(context, "question", "") or ""
    doc_bytes = b""
    if doc:
        try:
            p = Path(doc)
            if p.exists():
                doc_bytes = p.read_bytes()
        except OSError:
            doc_bytes = b""
    return hashlib.sha256(question.encode() + b"\0" + task.encode() + b"\0" + doc_bytes).hexdigest()[:12]


def _resolve_scratchpad(context) -> "Path | None":
    cfg = getattr(context, "org_config", None) or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        return None
    return Path(raw)


def maybe_read_sentinel(context, step, cycle: int, run_id: str, emit, workflow_name: "str | None" = None) -> "StepResult | None":
    """Return a synthesized ok StepResult from a cached sentinel, or None.

    None when: ``step.resume_sentinel`` is False, scratchpad unresolvable,
    the sentinel is absent/corrupt, or ``org_config["step_sentinel"]["disable"]``
    is truthy.
    """
    if not getattr(step, "resume_sentinel", False):
        return None
    cfg = getattr(context, "org_config", None) or {}
    if (cfg.get("step_sentinel") or {}).get("disable"):
        return None
    scratchpad = _resolve_scratchpad(context)
    if scratchpad is None:
        return None
    ctx_hash = None if (cfg.get("step_sentinel") or {}).get("ctx_hash_disable") else compute_ctx_hash(context)
    cached = read_step_sentinel(scratchpad, step.name, cycle, run_id, ctx_hash, workflow_name)
    if cached is None:
        return None
    try:
        emit("step_sentinel_resumed", {"step_name": step.name, "cycle": cycle, "source": "sentinel"}, run_id)
    except Exception:
        logger.warning("step_sentinel: failed to emit step_sentinel_resumed", exc_info=True)
    return StepResult(status="ok", data=cached, duration_ms=0, step_name=step.name)


def invalidate_cycle_sentinels(context, steps, cycle: int, run_id: str, emit=None, workflow_name: "str | None" = None) -> list:
    """Unlink both key variants of the resume sentinel for every flagged
    step in ``steps`` at ``(cycle, run_id)``. Returns the list of removed
    filenames. Degrades silently on missing files / OS errors / disabled
    config / unresolvable scratchpad.
    """
    cfg = getattr(context, "org_config", None) or {}
    if (cfg.get("step_sentinel") or {}).get("disable"):
        return []
    scratchpad = _resolve_scratchpad(context)
    if scratchpad is None:
        return []
    sentinel_dir = scratchpad / "resume"
    ctx_hash = compute_ctx_hash(context)
    removed: list = []
    for step in steps:
        if not getattr(step, "resume_sentinel", False):
            continue
        names = {resume_sentinel_name(step.name, cycle, run_id, None, workflow_name)}
        if ctx_hash:
            names.add(resume_sentinel_name(step.name, cycle, run_id, ctx_hash, workflow_name))
        for name in names:
            sentinel_file = sentinel_dir / name
            try:
                sentinel_file.unlink()
            except (FileNotFoundError, OSError):
                continue
            removed.append(name)
            if emit is not None:
                try:
                    emit(
                        "step_sentinel_invalidated",
                        {"step_name": step.name, "cycle": cycle, "reason": "same_cycle_retry"},
                        run_id,
                    )
                except Exception:
                    logger.warning("step_sentinel: failed to emit step_sentinel_invalidated", exc_info=True)
    return removed


def maybe_write_sentinel(context, step, cycle: int, run_id: str, result, workflow_name: "str | None" = None) -> None:
    """Write ``result.data`` to the sentinel — only for a flagged step's ok result."""
    if not getattr(step, "resume_sentinel", False):
        return
    if getattr(result, "status", None) != "ok":
        return
    cfg = getattr(context, "org_config", None) or {}
    if (cfg.get("step_sentinel") or {}).get("disable"):
        return
    scratchpad = _resolve_scratchpad(context)
    if scratchpad is None:
        return
    ctx_hash = None if (cfg.get("step_sentinel") or {}).get("ctx_hash_disable") else compute_ctx_hash(context)
    write_step_sentinel(scratchpad, step.name, cycle, result.data or {}, run_id, ctx_hash, workflow_name)
