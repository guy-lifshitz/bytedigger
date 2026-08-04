"""Unified LLM-subprocess timeout policy resolver (GH285 Cycle 1, 97B6CF02).

Pure stdlib, host-decoupled (no host-path literals, no env reads — the
override JSON path is injected by callers). `DEFAULT_POLICY` is the single
canonical matrix (today's effective values, zero behavior change); C2 will
migrate `workflows/phase_*.py` callsites onto `resolve_timeout_sec`.

Spec: 2026-07-06_97B6CF02_gh285_timeout_policy_c1_spec.md §2.1
"""
from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

DEFAULT_POLICY: dict[str, dict] = {
    "discovery.llm": {"base": 300, "override_key": "llm_timeout_sec"},
    "explore.llm": {"base": 600, "override_key": "explore_llm_timeout_sec"},
    "clarify.llm": {"base": 300, "override_key": "clarify_llm_timeout_sec"},
    "architect.llm": {"base": 600, "override_key": "llm_timeout_sec"},
    "spec.writer": {
        "base": 600,
        "COMPLEX": 1800,
        "opus": 900,
        "override_key": "spec_llm_timeout_sec",
    },
    "spec.reviewer": {
        "base": 300,
        "FEATURE": 600,
        "COMPLEX": 900,
        "opus": 600,
        "override_key": "review_llm_timeout_sec",
    },
    "spec_lite.writer": {"base": 600, "override_key": "spec_llm_timeout_sec"},
    "spec_lite.reviewer": {
        "base": 300,
        "FEATURE": 600,
        "COMPLEX": 900,
        "override_key": "review_llm_timeout_sec",
    },
    "implement.red": {
        "base": 1200,
        "FEATURE": 2400,
        "COMPLEX": 2400,
        "override_key": "red_llm_timeout_sec",
    },
    "implement.validation": {
        "base": 600,
        "COMPLEX": 1200,
        "override_key": "validation_llm_timeout_sec",
    },
    "implement.green": {
        "base": 900,
        "FEATURE": 1500,
        "COMPLEX": 1800,
        "override_key": "green_llm_timeout_sec",
    },
    "integrity.llm": {"base": 600, "override_key": "integrity_llm_timeout_sec"},
    "review.reviewer": {
        "base": 600,
        "FEATURE": 1000,
        "COMPLEX": 1500,
        "override_key": "review_llm_timeout_sec",
    },
    "review.fix": {"base": 900, "override_key": "fix_llm_timeout_sec"},
    "review.satisfaction": {
        "base": 600,
        "FEATURE": 1000,
        "COMPLEX": 1500,
        "override_key": "satisfaction_llm_timeout_sec",
    },
    "fix_integrity.llm": {
        "base": 600,
        "override_key": "fix_integrity_llm_timeout_sec",
    },
    "synthesize.llm": {"base": 600, "override_key": "synthesizer_llm_timeout_sec"},
}


def resolve_timeout_sec(
    step: str,
    cfg: dict | None,
    *,
    policy: dict | None = None,
    model_is_opus: bool = False,
) -> int:
    """Resolve the effective timeout for `step`. Precedence: cfg override
    (truthy -> max(1,int(raw)); malformed -> fall through) > COMPLEX >
    FEATURE > opus (only if entry defines it AND model_is_opus) > base.
    Unknown step -> ValueError (fail-closed)."""
    active_policy = policy if policy is not None else DEFAULT_POLICY
    entry = active_policy.get(step)
    if entry is None:
        raise ValueError(f"unknown timeout policy step: {step!r}")

    cfg = cfg or {}
    complexity = str(cfg.get("complexity") or "").upper()

    override_key = entry.get("override_key")
    if override_key:
        raw = cfg.get(override_key)
        if raw:
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                pass

    if complexity == "COMPLEX" and "COMPLEX" in entry:
        return entry["COMPLEX"]

    if complexity == "FEATURE" and "FEATURE" in entry:
        return entry["FEATURE"]

    if model_is_opus and "opus" in entry:
        return entry["opus"]

    return entry["base"]


def load_policy(path: str | Path | None) -> dict:
    """`None` or missing file -> deep copy of `DEFAULT_POLICY`. Existing
    file -> JSON parsed as an override map (`{"overrides": {...}}`),
    merged per-entry per-key over the defaults; may add new step keys.
    Malformed JSON / non-dict top level / non-int tier values -> ValueError
    (fail-closed)."""
    policy = copy.deepcopy(DEFAULT_POLICY)

    if path is None:
        return policy

    file_path = Path(path)
    if not file_path.is_file():
        return policy

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed timeout policy JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("timeout policy top-level must be a JSON object")

    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("timeout policy 'overrides' must be a JSON object")

    for step, tiers in overrides.items():
        if not isinstance(tiers, dict):
            raise ValueError(f"timeout policy override for {step!r} must be an object")
        step_entry = policy.setdefault(step, {})
        for key, value in tiers.items():
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(
                    f"timeout policy override {step!r}.{key!r} must be an int"
                )
            step_entry[key] = value

    return policy


ACTIVE_THRESHOLD_BYTES_DEFAULT = 50000


def resolve_active_threshold_bytes(raw: "str | int | None") -> int:
    """Resolve the ACTIVE/STUCK partial-stdout byte threshold (GH285 C3).
    Falsy `raw` (`None`/`""`/`0`) -> `ACTIVE_THRESHOLD_BYTES_DEFAULT`.
    Truthy int()-parsable -> `max(1, int(raw))` (mirrors `resolve_timeout_sec`
    override semantics). Truthy malformed -> `ACTIVE_THRESHOLD_BYTES_DEFAULT`
    (fail-open fall-through)."""
    if not raw:
        return ACTIVE_THRESHOLD_BYTES_DEFAULT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return ACTIVE_THRESHOLD_BYTES_DEFAULT


def classify_timeout_state(response_bytes: int, threshold: int) -> str:
    """Classify a timed-out subprocess's partial output: "active" if
    `response_bytes > threshold` (strict >), else "stuck" (boundary ==
    threshold is "stuck")."""
    return "active" if response_bytes > threshold else "stuck"


@functools.lru_cache(maxsize=8)
def cached_policy(path_str: str) -> dict:
    """Memoized `load_policy(path_str)` keyed on the (hashable) path string.

    GH285 C2: workflows callsites resolve a fresh policy-path string per call
    (cheap) but avoid re-reading/re-parsing the override file on every
    resolver invocation. Fail-closed ValueError from `load_policy` propagates
    and is NOT cached by lru_cache on raise (Python semantics: exceptions are
    not memoized) — acceptable, the file-read cost is trivial.

    §1i reset seam for tests: `cached_policy.cache_clear()`.
    """
    return load_policy(path_str)
