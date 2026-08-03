"""Single source of truth for pure helpers shared between phase_45_spec and
phase_45_spec_lite.

Extracted by CF480CAE (2026-06-16) — parent SYSTEMATIC DC87240D (build-engine
architecture hardening / SSOT-01 closure).

Exports:
    _FINDINGS_MAX_BYTES   — byte cap for threaded findings (4096)
    VERDICT_SHIP          — "SHIP"
    VERDICT_REVISE        — "REVISE"
    VERDICT_UNKNOWN       — "UNKNOWN"
    truncate_findings()   — cap findings at max_bytes, return (text, was_truncated)
    resolve_command()     — per-step-override → global llm_command → default
    read_first_block()    — READ_FIRST pointer block for LLM prompts
"""
from __future__ import annotations

from pathlib import Path

# HIGH #2 — cap findings threaded to next cycle prompt (avoids 50KB token-budget breach).
_FINDINGS_MAX_BYTES = 4096

VERDICT_SHIP = "SHIP"
VERDICT_REVISE = "REVISE"
VERDICT_UNKNOWN = "UNKNOWN"


def truncate_findings(text: str, max_bytes: int = _FINDINGS_MAX_BYTES) -> tuple[str, bool]:
    """Cap findings at *max_bytes* when threading to next retry cycle.

    Returns (possibly-truncated text, was_truncated).

    The *max_bytes* parameter defaults to ``_FINDINGS_MAX_BYTES`` (4096) so
    production call-sites are unaffected; test harnesses can pass a custom
    value to exercise the truncation path cheaply.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    review_path_hint = "(full review on disk)"
    return truncated + f"\n\n[... findings truncated at {max_bytes}B; {review_path_hint} ...]", True


def resolve_model(cfg: dict, override_key: str, default: str | None = None) -> str:
    """Per-step override → global model → per-step default (25e75663).

    Returns a model string. Config keys renamed *_llm_command → *_model.
    Lets harness pin Opus for Plan-Review without coupling spec writer
    to the same model. Mirrors phase_workflows_common._resolve_model.
    """
    return cfg.get(override_key) or cfg.get("model") or default or ""


def resolve_command(cfg: dict, override_key: str, default: list[str] | None = None) -> list[str]:
    """Per-step override → global llm_command → per-step default.

    Back-compat alias. Callers migrated to resolve_model for new code.
    Lets harness pin Opus for Plan-Review without coupling spec writer
    to the same model. Mirrors phase_5_implement._resolve_command.
    """
    cmd = cfg.get(override_key) or cfg.get("llm_command") or default
    if cmd is None:
        raise ValueError(f"_resolve_command: no command resolved for {override_key!r} and no default given")
    return list(cmd)


def resolve_review_model(cfg: "dict | None") -> str:
    """GH597: resolve the review model — cfg override → cfg.model → models.json
    spec_reviewer role → get_claude_critical() legacy default. Emits
    `resolver_review_model_resolved` on every branch (lazy imports — test
    patch targets depend on import-inside-function).

    GH632: the role rung is gate-aware — plan-review is always hard-gated,
    so a role value below the gate floor escalates to get_claude_critical()
    instead of returning unchanged.
    """
    from bytedigger_engine.lib import model_config
    from bytedigger_engine.lib.observability import emit_resolver

    cfg = cfg or {}
    if cfg.get("review_model"):
        value = cfg["review_model"]
        emit_resolver.emit_resolver_resolved("review_model", "cfg_override", value)
        return value
    if cfg.get("model"):
        value = cfg["model"]
        emit_resolver.emit_resolver_resolved("review_model", "cfg_model", value)
        return value
    role_value = model_config.get_claude_spec_reviewer()
    if role_value:
        from bytedigger_engine.llm_subprocess import _meets_gate_floor  # lazy: plan-review is always hard-gated
        if _meets_gate_floor(role_value):
            emit_resolver.emit_resolver_resolved("review_model", "models_spec_reviewer_role", role_value)
            return role_value
        default_value = model_config.get_claude_critical()
        emit_resolver.emit_resolver_resolved(
            "review_model", "spec_reviewer_role_below_gate_floor", default_value,
            {"role_value": role_value},
        )
        return default_value
    default_value = model_config.get_claude_critical()
    emit_resolver.emit_resolver_resolved("review_model", "default_critical", default_value)
    return default_value


def read_first_block(scratchpad: Path) -> str:
    inj = scratchpad / "injection"
    return (
        "READ_FIRST — read these six files before proceeding:\n"
        f"- {inj}/hal-memory.md      (learnings)\n"
        f"- {inj}/constitution.md    (project rules)\n"
        f"- {inj}/quality-gate.md    (zero-cornercutting policy)\n"
        f"- {inj}/producer-rules.md  (producer anti-fabrication)\n"
        f"- {inj}/active-work.md     (current project focus)\n"
        f"- {inj}/security-rules.md  (secure-coding defaults)\n"
        "If any file is missing or empty: orchestrator Phase 0.5 failed — "
        "STATUS=block with SUMMARY 'injection files missing'."
    )
