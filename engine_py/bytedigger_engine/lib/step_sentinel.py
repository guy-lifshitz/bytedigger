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

from bytedigger_engine.contracts import StepResult


from bytedigger_engine.lib.resume_keying import resume_sentinel_name  # noqa: E402
from bytedigger_engine.io_utils import atomic_write  # noqa: E402
from bytedigger_engine.lib.phase_sentinel import ctx_cfg_sha8  # noqa: E402

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
    """Hash question + task_description + decision_doc (+ org_config) bytes (GH443 part 3 §2.1, GH1050 v2).

    Missing/unreadable decision_doc degrades silently to empty bytes.

    Returns ``None`` when neither ``org_config["task_description"]`` nor
    ``org_config["decision_doc"]`` is set — i.e. there is nothing meaningful
    to distinguish this context from any other, so the legacy (non-hashed)
    sentinel filename is preserved (GH374 engine-level tests never set
    either field; part-3 scenarios always set at least one). This gate is
    unchanged by GH1050.

    GH1050: after the gate, folds in ``ctx_cfg_sha8({"org_config": cfg,
    "task_description": task})`` — the SAME §1g single-source config-hash
    producer used by ``phase_sentinel.phase_key``/DBOS workflow_uuid keying —
    so operator edits to org_config (e.g. a retry nonce, a timeout bump) that
    re-key the durable workflow also invalidate the resume sentinel instead of
    replaying a stale recorded result. If org_config contains a non-JSON-
    serializable value, ``ctx_cfg_sha8`` raises ``TypeError``; this degrades
    to the exact pre-GH1050 legacy formula (no cfg segment, no crash).

    Deploy note: every hashed sentinel filename changes once at deploy time —
    in-flight crash-resumes re-execute the affected step once (safe direction).
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
    try:
        cfg_sha = ctx_cfg_sha8({"org_config": cfg, "task_description": task})
    except TypeError:
        return hashlib.sha256(question.encode() + b"\0" + task.encode() + b"\0" + doc_bytes).hexdigest()[:12]
    return hashlib.sha256(
        question.encode() + b"\0" + task.encode() + b"\0" + doc_bytes + b"\0" + cfg_sha.encode()
    ).hexdigest()[:12]


def effective_ctx_hash(ctx_hash: "str | None", step, prev) -> "str | None":
    """Fold a step-declared input field into ``ctx_hash`` (GH897 §2.2).

    ``step.sentinel_input_field`` names a key in ``prev.data`` whose value
    (when a non-empty str) is mixed into the sentinel-key hash, so a changed
    LLM-step input (e.g. an on-disk red-fix) cannot serve a stale cached
    result. Legacy degrade to plain ``ctx_hash`` when: no field declared,
    ``prev`` is None/has no ``.data``, the field is missing/non-str/empty.
    """
    field = getattr(step, "sentinel_input_field", None)
    if not field:
        return ctx_hash
    data = getattr(prev, "data", None)
    val = data.get(field) if isinstance(data, dict) else None
    if not isinstance(val, str) or not val:
        return ctx_hash
    return hashlib.sha256(((ctx_hash or "") + "\0" + val).encode()).hexdigest()[:12]


def _resolve_scratchpad(context) -> "Path | None":
    cfg = getattr(context, "org_config", None) or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        return None
    return Path(raw)


def maybe_read_sentinel(context, step, cycle: int, run_id: str, emit, workflow_name: "str | None" = None, prev=None) -> "StepResult | None":
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
    ctx_hash = effective_ctx_hash(ctx_hash, step, prev)
    cached = read_step_sentinel(scratchpad, step.name, cycle, run_id, ctx_hash, workflow_name)
    if cached is None:
        return None
    try:
        emit("step_sentinel_resumed", {"step_name": step.name, "cycle": cycle, "source": "sentinel"}, run_id)
    except Exception:
        logger.warning("step_sentinel: failed to emit step_sentinel_resumed", exc_info=True)
    return StepResult(status="ok", data=cached, duration_ms=0, step_name=step.name)


def invalidate_cycle_sentinels(context, steps, cycle: int, run_id: str, emit=None, workflow_name: "str | None" = None, reason: str = "same_cycle_retry") -> list:
    """Unlink both key variants of the resume sentinel for every flagged
    step in ``steps`` at ``(cycle, run_id)``. Returns the list of removed
    filenames. Degrades silently on missing files / OS errors / disabled
    config / unresolvable scratchpad.

    A step with ``sentinel_input_field`` set has an input-hashed sentinel
    key whose hash value is not reconstructible here (the input is only
    known at read/write time) — for those steps, invalidation instead
    globs ``{workflow}__{step}_done_c{cycle}_r{run_id}_h*.json`` (GH897
    §2/r2 MINOR-2) anchored with a literal ``_h`` immediately after the
    run_id segment so a run_id prefix collision (e.g. invalidating "R1"
    while "R12" sentinels exist) cannot over-match, plus the exact legacy
    (non-hashed) names. Assumes production run_id values are uuid4 hex
    (no glob metacharacters); an operator-supplied ``--run-id`` containing
    glob metacharacters is out of contract (r2 MINOR-3).
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
        names: set = set()
        input_field = getattr(step, "sentinel_input_field", None)
        if input_field:
            names.add(resume_sentinel_name(step.name, cycle, run_id, None, workflow_name))
            prefix = f"{workflow_name}__" if workflow_name else ""
            pattern = f"{prefix}{step.name}_done_c{cycle}_r{run_id}_h*.json"
            try:
                for match in sentinel_dir.glob(pattern):
                    names.add(match.name)
            except OSError:
                logger.warning("step_sentinel: glob failed for pattern %s", pattern, exc_info=True)
        else:
            names.add(resume_sentinel_name(step.name, cycle, run_id, None, workflow_name))
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
                        {"step_name": step.name, "cycle": cycle, "reason": reason},
                        run_id,
                    )
                except Exception:
                    logger.warning("step_sentinel: failed to emit step_sentinel_invalidated", exc_info=True)
    return removed


def invalidate_validation_sentinels_for_run(
    scratchpad: "Path", run_id: str,
    workflow_name: str = "phase_5_validation_cycle",
    step_names: "tuple[str, ...]" = ("invoke_validation_llm",),
) -> list:
    """GH963 §2.6: unlink every validation-cycle sentinel for ``run_id``,
    scoped to ``step_names`` only (RED/review sentinels are never touched —
    over-deletion re-pays LLM calls, §1ac scope rule). Two anchored glob
    variants per step (legacy + input-hashed), anchored immediately after
    the run_id segment (GH897 r2 MINOR-2) so a prefix collision ("R1" vs
    "R12") cannot over-match. UNLINK, not read-suppression — must not
    survive a crash-restart (§1ac). Degrades silently on OSError / missing
    dir. Returns the list of removed filenames; idempotent (second call on
    an already-invalidated run returns []).
    """
    removed: list = []
    sentinel_dir = Path(scratchpad) / "resume"
    if not sentinel_dir.exists():
        return removed
    for step_name in step_names:
        patterns = (
            f"{workflow_name}__{step_name}_done_c*_r{run_id}.json",
            f"{workflow_name}__{step_name}_done_c*_r{run_id}_h*.json",
        )
        for pattern in patterns:
            try:
                matches = list(sentinel_dir.glob(pattern))
            except OSError:
                continue
            for match in matches:
                try:
                    match.unlink()
                except (FileNotFoundError, OSError):
                    continue
                removed.append(match.name)
    return removed


def maybe_write_sentinel(context, step, cycle: int, run_id: str, result, workflow_name: "str | None" = None, prev=None) -> None:
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
    ctx_hash = effective_ctx_hash(ctx_hash, step, prev)
    write_step_sentinel(scratchpad, step.name, cycle, result.data or {}, run_id, ctx_hash, workflow_name)
