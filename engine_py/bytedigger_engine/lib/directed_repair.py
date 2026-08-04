"""GH371 / 457DC7DC — universal directed gate-repair loop («Слой 2»).

Spec: the state dir's memory/Decisions/2026-07-06_457DC7DC_gh371_universal_gate_repair_spec.md

A single gate-agnostic primitive that fronts each deterministic gate's
terminal/regen FAIL branch with a *cheap directed repair* pre-stage: feed the
gate's exact locus-bearing findings to a cheap model, write the patched
artifact, and RE-RUN THE SAME DETERMINISTIC GATE. The gate — never the model —
remains the correctness authority (§2.5): a bad cheap-model edit cannot pass,
and non-convergence falls through to today's unchanged expensive fallback.
"""
from __future__ import annotations

import ast
import contextvars
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bytedigger_engine.lib.env_limit import ENV_LIMIT_MARKERS

# Re-entry guard: the wired gates pass ``rerun_gate=lambda: <verify_fn>(ctx, prev)``
# (spec §2.1/§2.2 frozen pattern). Re-running the full verify fn would otherwise
# re-enter its directed-repair pre-stage recursively. This flag makes the nested
# re-run execute the gate check ONLY — the pre-stage is skipped while already
# inside a repair loop, so total work stays bounded by the single outer cap.
_in_repair: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_hal_in_directed_repair", default=False
)

# Module-level bindings so tests / callers can monkeypatch the seams
# (dr_mod.invoke_llm_subprocess, telemetry_ctx.emit_safe).
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess  # noqa: F401
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.io_utils import atomic_write

from bytedigger_engine.contracts import StepResult

_DEFAULT_MAX_ATTEMPTS = 2
_DEFAULT_TIMEOUT_SEC = 300  # retired hard timeout; kept for external refs.

# GH674 §1: chunking + scalable per-batch timeout.
_DEFAULT_REPAIR_BATCH_SIZE = 8
_DEFAULT_TIMEOUT_BASE_SEC = 120
_DEFAULT_TIMEOUT_PER_FINDING_SEC = 15
_DEFAULT_TIMEOUT_PER_KB_SEC = 2
_DEFAULT_TIMEOUT_CAP_SEC = 600

# GH482 §2.1: a directed-repair patch must actually BE an artifact, not prose.
# Size-collapse floor/ratio: below the floor the gate is skipped (a tiny
# artifact legitimately shrinking is not "collapse"); at/above it, a patch
# under the ratio of the pre-patch byte size is rejected.
_SIZE_GATE_MIN_BYTES = 200
_SIZE_COLLAPSE_RATIO = 0.5

# GH690 §2.2: per-batch circuit-breaker default consecutive-failure threshold
# (cfg key "directed_repair_batch_error_threshold"). Values < 1 clamp to this.
_DEFAULT_REPAIR_BATCH_ERROR_THRESHOLD = 3

# GH382 §2.A: a budget-exhausted (429/spend-limit) repair model must not be
# indistinguishable from a real gate failure. Default fallback model + the
# lowercase-substring markers that identify a "model unavailable" StepResult
# (matched against sr.error + sr.data["stdout_tail"]/["stderr_tail"], the
# E_LLM_EXIT payload shape — llm_subprocess.py:1392-1411).
_DEFAULT_REPAIR_FALLBACK_MODEL = "sonnet"
# GH514(3) §1g single-source: markers now live in lib.env_limit; this is the
# SAME tuple object (identity), not a copy — consumers still reference the
# name _MODEL_UNAVAILABLE_MARKERS.
_MODEL_UNAVAILABLE_MARKERS = ENV_LIMIT_MARKERS

# Gate-suppression blocklist (§2.6). A cheap model can "pass" a gate by inserting
# a token the gate itself honors — silently exempting a real (incl. terminal
# SECURITY) finding. Each entry (label, pattern) is matched case-insensitively
# against ADDED content only (see _scan_new_suppression_tokens). ``nosem`` uses a
# word boundary so it fires on ``# nosem`` but NOT on the substring inside
# ``nosemgrep`` (which is reported via its own pattern). ``gitleaks:allow`` is an
# inline marker honored unconditionally by the security gate's gitleaks scanner
# (any line containing it is skipped), so it can silence a terminal secret finding.
#
# GH373 §1g: single source of truth moved to lib/authored_boundary.py. This
# name is now the SAME object (identity, not just equal value) so any future
# boundary sharing the blocklist stays in sync automatically.
from bytedigger_engine.lib import authored_boundary as _authored_boundary

_SUPPRESSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = _authored_boundary.SUPPRESSION_PATTERNS


@dataclass
class DirectedRepairResult:
    """Outcome of a directed-repair loop.

    converged: the re-run gate returned status=="ok" on the patched artifact.
    attempts:  number of repair attempts made (== max_attempts on exhaustion).
    final:     the gate's ok StepResult on convergence, else None (the caller
               preserves the ORIGINAL FAIL — the primitive never fabricates a
               pass).
    exhausted_reason: None while converged; else one of FOUR values:
               "model_unavailable" (both the primary and fallback repair model
               were unavailable, GH382 §2.A), "no_progress" (every batched LLM
               call in an attempt errored -- the model emitted no output at
               all -- fail-fast before consuming another attempt, GH674 §1),
               "attempts" (normal cap exhaustion, unchanged behavior), or
               "batch_circuit_breaker" (GH690 §2.2: N consecutive failed
               batches -- LLM error, unparseable/inapplicable patch, or a
               validate/suppression rejection -- tripped the per-batch
               circuit-breaker before the attempt/batch budget was spent).
    """

    converged: bool
    attempts: int
    final: StepResult | None = None
    exhausted_reason: str | None = None


def _directed_repair_enabled(ctx) -> bool:
    """Kill switch (§2.4). Default ON; ``HAL_DIRECTED_REPAIR=0`` disables.
    ``org_config["directed_repair_skip"]`` is the test-isolation off-switch."""
    if _in_repair.get():
        return False
    from bytedigger_engine import config_provider
    if (config_provider.env_opt("HAL_DIRECTED_REPAIR") or "1").strip() == "0":
        return False
    cfg = getattr(ctx, "org_config", None) or {}
    if cfg.get("directed_repair_skip"):
        return False
    return True


def _resolve_directed_repair_model(cfg: dict | None) -> str:
    """GH386/GH597: resolve the cheap repair model (§2.4). ``org_config['directed_repair_model']``
    override wins, else the models.json/FALLBACK_CONFIG directed_repair role chain
    (sonnet -> haiku, never opus — FALLBACK_CONFIG now guarantees a value).
    Emits `resolver_directed_repair_model_resolved` on every branch (lazy
    imports — test patch targets depend on import-inside-function)."""
    from bytedigger_engine.lib import model_config
    from bytedigger_engine.lib.observability import emit_resolver

    cfg = cfg or {}
    if cfg.get("directed_repair_model"):
        value = cfg["directed_repair_model"]
        emit_resolver.emit_resolver_resolved("directed_repair_model", "cfg_override", value)
        return value
    value = model_config.get_claude_directed_repair()
    emit_resolver.emit_resolver_resolved("directed_repair_model", "models_directed_repair_role", value)
    return value


def _resolve_directed_repair_fallback_model(cfg: dict | None) -> str:
    """Resolve the fallback repair model (GH382 §2.A), used when the primary
    repair model reports "unavailable" (429/spend-limit). Default ``sonnet``.
    ``org_config['directed_repair_fallback_model']`` override wins."""
    cfg = cfg or {}
    return cfg.get("directed_repair_fallback_model") or _DEFAULT_REPAIR_FALLBACK_MODEL


def _sr_model_unavailable(sr: Any) -> bool:
    """True if ``sr`` is an error StepResult whose error/stdout_tail/stderr_tail
    carries a budget/rate-limit "model unavailable" marker (GH382 §2.A.2).
    False for an ok StepResult, or an error StepResult with no marker hit."""
    if not isinstance(sr, StepResult) or sr.status == "ok":
        return False
    data = sr.data if isinstance(sr.data, dict) else {}
    haystack = " ".join(
        str(part) for part in (sr.error, data.get("stdout_tail"), data.get("stderr_tail")) if part
    ).lower()
    return any(marker in haystack for marker in _MODEL_UNAVAILABLE_MARKERS)


def _repair_cap(cfg: dict | None) -> int:
    """Directed-repair attempt cap (§2.4). Default 2, independent of the
    fallback regen cap."""
    cfg = cfg or {}
    return int(cfg.get("directed_repair_max_attempts") or _DEFAULT_MAX_ATTEMPTS)


def _repair_batch_size(cfg: dict | None) -> int:
    """GH674 §1: per-attempt findings batch size (default 8). Only a POSITIVE
    int override (``org_config['directed_repair_batch_size']``) is honored;
    missing key, ``None`` cfg, ``0``, negative, or non-int falls back to the
    default."""
    cfg = cfg or {}
    value = cfg.get("directed_repair_batch_size")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return _DEFAULT_REPAIR_BATCH_SIZE


def _repair_batch_error_threshold(cfg: dict | None) -> int:
    """GH690 §2.2: per-batch circuit-breaker consecutive-failure threshold
    (default 3). Only a positive int override
    (``org_config['directed_repair_batch_error_threshold']``) is honored;
    missing key, ``None`` cfg, ``0``, negative, or non-int falls back to the
    default (values < 1 clamp to the default)."""
    cfg = cfg or {}
    value = cfg.get("directed_repair_batch_error_threshold")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return _DEFAULT_REPAIR_BATCH_ERROR_THRESHOLD


def _batch_findings(findings: list[dict], batch_size: int) -> list[list[dict]]:
    """GH674 §1: order-preserving contiguous partition of ``findings`` into
    chunks of ``batch_size`` (last chunk may be shorter). ``batch_size <= 0``
    yields a single batch containing all findings. Empty ``findings`` yields
    ``[]``. Invariant: flattening the result reproduces the input exactly."""
    items = list(findings or [])
    if not items:
        return []
    if batch_size <= 0:
        return [items]
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def _repair_timeout_sec(cfg: dict | None, num_findings: int, artifact_bytes: int) -> int:
    """GH674 §1: scalable per-batch repair timeout, replacing the retired flat
    300s constant. ``min(cap, base + per_finding*num_findings +
    per_kb*ceil(artifact_bytes/1024))``, floored at ``base``. Each term reads
    an ``org_config`` override else the matching ``_DEFAULT_TIMEOUT_*``
    constant."""
    cfg = cfg or {}
    base = int(cfg.get("directed_repair_timeout_base") or _DEFAULT_TIMEOUT_BASE_SEC)
    per_finding = int(cfg.get("directed_repair_timeout_per_finding") or _DEFAULT_TIMEOUT_PER_FINDING_SEC)
    per_kb = int(cfg.get("directed_repair_timeout_per_kb") or _DEFAULT_TIMEOUT_PER_KB_SEC)
    cap = int(cfg.get("directed_repair_timeout_cap") or _DEFAULT_TIMEOUT_CAP_SEC)
    kb = (artifact_bytes + 1023) // 1024
    total = base + per_finding * num_findings + per_kb * kb
    return int(min(cap, max(base, total)))


def _render_findings(findings: list[dict]) -> str:
    lines: list[str] = []
    for f in findings or []:
        if not isinstance(f, dict):
            lines.append(f"- {f}")
            continue
        path = f.get("path", "")
        line = f.get("line", "")
        rule = f.get("rule", "")
        evidence = f.get("evidence", "")
        loc = f"{path}:{line}" if line != "" else str(path)
        lines.append(f"- {loc} [{rule}]: {evidence}")
    return "\n".join(lines)


# GH962/GH952: gate-specific resolution guidance for directed repair, keyed
# by finding `rule` id. Rules absent from this dict get no extra block --
# the prompt stays byte-identical to today (§1b prompt-byte-cap surface).
_RULE_GUIDANCE: dict[str, str] = {
    "missing-ground-truth-ddl": (
        "Add a `## Data-Model Ground Truth` section with a ```sql fenced "
        "CREATE TABLE DDL block; or opt out with `ground-truth: n/a — <reason>` "
        "inside that section; or, if the path is never actually opened/created, "
        "add the per-token pragma `ground-truth: never-opened <token>` or "
        "rephrase/elide the token."
    ),
    # 4197B484 (GH1120) §1.5: the marker vocabulary the retrying agent needs.
    # Every member is a verbatim copy of
    # sibling_test_verifier.AUDIT_MARKER_VOCABULARY (drift pinned by AC10).
    "sibling-audit-missing": (
        "Add a `## §3.2 Sibling-test audit` section that NAMES the cited symbol "
        "explicitly. Audit scoping is PER SYMBOL: a section that does not name "
        "this symbol -- including one whose body is `sibling-test audit: n/a` -- "
        "does not silence it. Accepted section markers (case-insensitive): "
        "§3.2 | sibling-test audit | sibling-test-audit | sibling test audit | "
        "sibling-audit:"
    ),
    "empty-ground-truth-ddl": (
        "Add a ```sql fenced CREATE TABLE DDL block inside the existing "
        "`## Data-Model Ground Truth` section, or opt out with "
        "`ground-truth: n/a — <reason>`; the per-token pragma "
        "`ground-truth: never-opened <token>` also applies if the path is "
        "never actually opened/created."
    ),
}


def _build_prompt(gate: str, artifact_path: str, artifact_text: str, findings: list[dict]) -> str:
    rules = sorted({str(f.get("rule", "")) for f in findings if isinstance(f, dict) and f.get("rule")})
    rule_str = ", ".join(rules) if rules else gate
    guidance_rules = [r for r in rules if r in _RULE_GUIDANCE]
    guidance_block = "".join(
        f"\nGate-specific guidance: {_RULE_GUIDANCE[r]}\n" for r in guidance_rules
    )
    return (
        f"You are performing a locus-bounded directed repair for the deterministic gate `{gate}`.\n"
        f"Artifact path: {artifact_path}\n\n"
        f"The gate reported the following finding(s), each with an exact locus:\n"
        f"{_render_findings(findings)}\n\n"
        f"--- BEGIN ARTIFACT ---\n{artifact_text}\n--- END ARTIFACT ---\n\n"
        f"Patch STRICTLY at these loci to satisfy `{rule_str}`; do not restructure "
        f"or reformat anything else.\n"
        f"{guidance_block}\n"
        f"Return ONLY one or more SEARCH/REPLACE blocks bounded to the cited "
        f"loci -- no prose, no other commentary. Each block must use exactly "
        f"this format:\n"
        f"<<<<<<< SEARCH\n"
        f"<exact text to find>\n"
        f"=======\n"
        f"<replacement text>\n"
        f">>>>>>> REPLACE\n"
    )


_SR_BLOCK_RE = re.compile(
    r"<<<<<<<\s*SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>>\s*REPLACE",
    re.DOTALL,
)


def _parse_search_replace_blocks(raw: str) -> list[tuple[str, str]]:
    """GH690 §2.1: scan ``raw`` for SEARCH/REPLACE marker blocks (tolerant of
    surrounding ``` code fences -- the fence lines simply fall outside the
    marker span and are ignored). Returns an ordered list of ``(search,
    replace)`` tuples; no well-formed block ⇒ ``[]``."""
    if not raw:
        return []
    return [(m.group(1), m.group(2)) for m in _SR_BLOCK_RE.finditer(raw)]


def _apply_search_replace(pre_text: str, blocks: list[tuple[str, str]]) -> str | None:
    """GH690 §2.1: apply ``blocks`` sequentially to ``pre_text``. Each SEARCH
    must be non-empty and occur exactly once in the current working text ⇒
    replaced with REPLACE. 0 occurrences, >1 (ambiguous), or an empty SEARCH
    ⇒ the whole patch is inapplicable ⇒ ``None``. Empty ``blocks`` ⇒ ``None``.
    On success returns the full patched text."""
    if not blocks:
        return None
    text = pre_text
    for search, replace in blocks:
        if not search:
            return None
        if text.count(search) != 1:
            return None
        text = text.replace(search, replace, 1)
    return text


def _validate_patched_text(artifact_path: str, pre_text: str, patched: str) -> str | None:
    """GH482 §2.1: validate a directed-repair patch is actually an artifact,
    not prose, BEFORE it is ever written to disk. Pure (no I/O). Returns a
    rejection-reason string, or ``None`` when the patch is valid.

    1. syntax gate (``.py`` only): unparsable ⇒ ``"syntax_error"``.
    2. size-collapse gate (all types, checked after gate 1): a patch under
       ``_SIZE_COLLAPSE_RATIO`` of the pre-patch byte size, once the pre-patch
       size is at/above ``_SIZE_GATE_MIN_BYTES``, ⇒ ``"size_collapse"``.
    """
    if artifact_path.endswith(".py"):
        try:
            ast.parse(patched)
        except Exception:
            return "syntax_error"

    pre_bytes = len(pre_text.encode("utf-8", errors="replace"))
    post_bytes = len(patched.encode("utf-8", errors="replace"))
    if pre_bytes >= _SIZE_GATE_MIN_BYTES and post_bytes < _SIZE_COLLAPSE_RATIO * pre_bytes:
        return "size_collapse"

    return None


def _extract_patched_text(sr: StepResult, pre_text: str) -> str | None:
    """GH690 §2.1: parse ``sr.data['raw_response']`` for SEARCH/REPLACE blocks
    and apply them to ``pre_text``. A raw response with no well-formed blocks
    (e.g. a full-artifact dump) parses to ``[]`` ⇒ ``None`` -- there is no
    full-replacement fallback."""
    if not isinstance(sr, StepResult) or sr.status != "ok" or not isinstance(sr.data, dict):
        return None
    raw = sr.data.get("raw_response")
    if raw is None:
        return None
    blocks = _parse_search_replace_blocks(str(raw))
    return _apply_search_replace(pre_text, blocks)


def _refresh_findings(sr: StepResult) -> list[dict]:
    """Best-effort re-derive locus-bearing findings from the re-run gate's
    StepResult so the next attempt is re-directed. Tolerant of shape; falls
    back to an empty list (the loop continues on the original findings)."""
    if not isinstance(sr, StepResult) or not isinstance(sr.data, dict):
        return []
    for key in ("findings", "spec_lint_findings", "green_lint_findings",
                "security_lint_findings", "spec_cite_lint_findings"):
        val = sr.data.get(key)
        if isinstance(val, list) and val:
            out: list[dict] = []
            for item in val:
                if isinstance(item, dict):
                    out.append(item)
                else:
                    out.append({"evidence": str(item)})
            return out
    return []


# GH373 §1g migration: body moved to lib/authored_boundary.scan_added_content;
# these names stay as thin re-export/delegate wrappers so the repair loop's
# call site and tests/test_457DC7DC_directed_repair.py are unaffected.
_added_content = _authored_boundary._added_content


def _scan_new_suppression_tokens(
    pre_text: str,
    post_text: str,
    forbidden_new_tokens: list[str] | None = None,
) -> list[str]:
    """Scan the ADDED content only for gate-suppression tokens (§2.6). Returns
    the list of matched token strings (empty ⇒ clean). ``forbidden_new_tokens``
    are additional caller-supplied literal (case-insensitive) blocklist entries
    layered on top of the built-in primitive blocklist.

    Thin delegate (GH373 §1g) to ``lib.authored_boundary.scan_added_content`` —
    behavior identical."""
    return _authored_boundary.scan_added_content(pre_text, post_text, forbidden_new_tokens)


def attempt_directed_repair(
    *,
    gate: str,
    artifact_path: str,
    findings: list[dict],
    rerun_gate: Callable[[], StepResult],
    cheap_model: str,
    repair_step_name: str,
    max_attempts: int,
    ctx: Any,
    forbidden_new_tokens: list[str] | None = None,
) -> DirectedRepairResult:
    """Cheap directed-repair pre-stage (spec §2.1).

    Loops up to ``max_attempts``: prompt a cheap model with ONLY the listed
    locus-bearing findings, atomically write the patched artifact, then re-run
    the SAME deterministic gate. Returns converged on the first gate-verified
    PASS; on exhaustion returns ``converged=False, final=None`` and the caller
    falls through to today's unchanged terminal/regen behavior.
    """
    cur_findings = list(findings or [])
    cfg = getattr(ctx, "org_config", None) or {}
    token = _in_repair.set(True)
    try:
        return _run_repair_loop(
            gate=gate,
            artifact_path=artifact_path,
            findings=cur_findings,
            rerun_gate=rerun_gate,
            cheap_model=cheap_model,
            repair_step_name=repair_step_name,
            max_attempts=max_attempts,
            forbidden_new_tokens=forbidden_new_tokens,
            cfg=cfg,
        )
    finally:
        _in_repair.reset(token)


def _run_repair_loop(
    *,
    gate: str,
    artifact_path: str,
    findings: list[dict],
    rerun_gate: Callable[[], StepResult],
    cheap_model: str,
    repair_step_name: str,
    max_attempts: int,
    forbidden_new_tokens: list[str] | None = None,
    cfg: dict | None = None,
) -> DirectedRepairResult:
    """Manual-counter repair loop (GH382 §2.A restructure — was a plain
    ``for attempt in range(...)``). A "model unavailable" (429/spend-limit)
    response switches to the fallback model WITHOUT consuming the attempt (a
    dead model never ran a real repair); at most one switch, then every
    iteration either consumes an attempt or breaks — the loop stays bounded.
    """
    cur_findings = list(findings or [])
    cur_model = cheap_model
    fallback_model = _resolve_directed_repair_fallback_model(cfg)
    switched_to_fallback = False
    model_unavailable_exhausted = False
    no_progress_exhausted = False
    circuit_breaker_exhausted = False
    batch_size = _repair_batch_size(cfg)
    # GH690 §2.2: per-batch circuit-breaker. Scoped to the whole invocation
    # (spans batches AND attempts) -- initialized ONCE before the loop.
    consecutive_batch_failures = 0
    batch_error_threshold = _repair_batch_error_threshold(cfg)
    attempt = 1
    while attempt <= max_attempts:
        telemetry_ctx.emit_safe(
            "directed_repair_attempted",
            {"gate": gate, "attempt": attempt, "model": cur_model},
        )

        batches = _batch_findings(cur_findings, batch_size)
        telemetry_ctx.emit_safe(
            "directed_repair_batched",
            {
                "gate": gate,
                "attempt": attempt,
                "batches": len(batches),
                "batch_size": batch_size,
                "total_findings": len(cur_findings),
            },
        )

        attempt_produced_ok = False
        attempt_skip_gate = False
        bi = 0
        while bi < len(batches):
            batch = batches[bi]
            try:
                artifact_text = Path(artifact_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                artifact_text = ""

            timeout = _repair_timeout_sec(
                cfg, len(batch), len(artifact_text.encode("utf-8", "replace"))
            )
            prompt = _build_prompt(gate, artifact_path, artifact_text, batch)
            llm_sr = invoke_llm_subprocess(
                prompt=prompt,
                model=cur_model,
                timeout_sec=timeout,
                step_name=repair_step_name,
                hard_gate=False,
            )

            if _sr_model_unavailable(llm_sr):
                telemetry_ctx.emit_safe(
                    "directed_repair_model_unavailable",
                    {"gate": gate, "model": cur_model, "attempt": attempt},
                )
                if not switched_to_fallback and cur_model != fallback_model:
                    cur_model = fallback_model
                    switched_to_fallback = True
                    continue  # dead model: retry the SAME batch, no advance
                model_unavailable_exhausted = True
                break

            batch_failed = False
            if isinstance(llm_sr, StepResult) and llm_sr.status == "ok":
                attempt_produced_ok = True
                patched = _extract_patched_text(llm_sr, artifact_text)
                if patched is None:
                    # GH690 §2.2: unparseable/inapplicable patch -- FAILURE.
                    batch_failed = True
                else:
                    # GH482 §2.2: validate-before-write — a prose/collapsed
                    # patch must never reach disk.
                    reject_reason = _validate_patched_text(artifact_path, artifact_text, patched)
                    if reject_reason is not None:
                        telemetry_ctx.emit_safe(
                            "directed_repair_patch_rejected",
                            {
                                "gate": gate,
                                "attempt": attempt,
                                "reason": reject_reason,
                                "pre_bytes": len(artifact_text.encode("utf-8", errors="replace")),
                                "post_bytes": len(patched.encode("utf-8", errors="replace")),
                            },
                        )
                        attempt_skip_gate = True
                        batch_failed = True
                    else:
                        try:
                            atomic_write(Path(artifact_path), patched)
                        except OSError:
                            pass

                        # §2.6 suppression guard: a cheap model can "pass" a gate
                        # by inserting a token the gate itself honors. Scan the
                        # ADDED content for gate-suppression tokens BEFORE
                        # accepting any convergence.
                        new_tokens = _scan_new_suppression_tokens(
                            artifact_text, patched, forbidden_new_tokens
                        )
                        if new_tokens:
                            try:
                                atomic_write(Path(artifact_path), artifact_text)
                            except OSError:
                                pass
                            telemetry_ctx.emit_safe(
                                "directed_repair_suppression_rejected",
                                {"gate": gate, "tokens": new_tokens},
                            )
                            attempt_skip_gate = True
                            batch_failed = True
                        else:
                            # SUCCESS: patch applied + validated + written,
                            # no new suppression tokens -- reset the breaker.
                            consecutive_batch_failures = 0
            else:
                # LLM error, not model-unavailable -- FAILURE.
                batch_failed = True

            if batch_failed:
                consecutive_batch_failures += 1
                if consecutive_batch_failures >= batch_error_threshold:
                    telemetry_ctx.emit_safe(
                        "directed_repair_circuit_breaker",
                        {
                            "gate": gate,
                            "consecutive_failures": consecutive_batch_failures,
                            "threshold": batch_error_threshold,
                        },
                    )
                    circuit_breaker_exhausted = True
                    break

            if attempt_skip_gate:
                break

            bi += 1

        if model_unavailable_exhausted:
            break

        if circuit_breaker_exhausted:
            break

        if attempt_skip_gate:
            attempt += 1
            continue

        # The deterministic gate is the correctness authority (§2.5).
        sr = rerun_gate()
        if isinstance(sr, StepResult) and sr.status == "ok":
            telemetry_ctx.emit_safe(
                "directed_repair_converged",
                {"gate": gate, "attempt": attempt, "model": cur_model},
            )
            return DirectedRepairResult(converged=True, attempts=attempt, final=sr)

        # GH674 §1 fail-fast: every batch call this attempt errored (the
        # model emitted no output at all) -- error-scoped, NOT count-scoped.
        if not attempt_produced_ok:
            no_progress_exhausted = True
            break

        cur_findings = _refresh_findings(sr) or cur_findings
        attempt += 1

    if circuit_breaker_exhausted:
        attempts_used = attempt
        reason = "batch_circuit_breaker"
    elif model_unavailable_exhausted:
        attempts_used = attempt - 1
        reason = "model_unavailable"
    elif no_progress_exhausted:
        attempts_used = attempt
        reason = "no_progress"
    else:
        attempts_used = max_attempts
        reason = "attempts"

    telemetry_ctx.emit_safe(
        "directed_repair_exhausted",
        {"gate": gate, "attempts": attempts_used, "model": cur_model, "reason": reason},
    )
    return DirectedRepairResult(
        converged=False, attempts=attempts_used, final=None, exhausted_reason=reason
    )
