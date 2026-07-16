"""ModelConfig — Python port of SYSTEM/lib/core/ModelConfig.ts.

Path resolved via config_provider: HAL → $HAL_DIR-relative state dir; OSS/neutral → ./config/models.json.
Engine_py phase files import this instead of hardcoding model names.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))
from config_provider import models_config_path as _models_config_path  # noqa: E402

# None → resolve lazily via the active config provider at call time
# (HAL provider: $HAL_DIR/SHARED/config/models.json; OSS/neutral cwd: ./config/models.json).
# Tests may set this to a fixture Path to override. Replaces the former import-time const that
# hardcoded SHARED/config/models.json (B2 import-time-const trap, OFI 9438df29).
_CONFIG_PATH: Optional[Path] = None


def _resolve_config_path() -> Path:
    """models.json path: explicit override if set, else config_provider seam (provider-aware, cwd-graceful)."""
    return _CONFIG_PATH if _CONFIG_PATH is not None else _models_config_path()

FALLBACK_CONFIG: dict = {
    "claude": {
        "primary": "sonnet",
        "fallback": "haiku",
        "critical": "opus",
        "spec_writer": "opus",
        "discovery": "sonnet",
        "explore": "sonnet",
        # GH386: role value widens to str | list[str] — a chain of
        # successors, resolved by get_role_model against unavailable entries.
        "decorrelated_verifier": ["fable", "opus"],
        "directed_repair": ["sonnet", "haiku"],
    },
}


def _as_chain(value) -> "list[str]":
    """GH386: normalize a FALLBACK_CONFIG/models.json role value into an
    ordered chain of candidate model names. None/""->[]; str->[value];
    list/tuple->[str(v) for v in value if v]; anything else->[]."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return []


def _unavailable_entries(provider: str = "claude") -> "list[str]":
    """GH386: union of env HAL_MODEL_UNAVAILABLE (comma-split, stripped,
    empties dropped) and models.json <provider>.unavailable (non-list treated
    as [] — §1x legacy-input: an existing models.json without the key is
    exact current behavior)."""
    import config_provider

    env_raw = config_provider.env_opt("HAL_MODEL_UNAVAILABLE") or ""
    env_entries = [e.strip() for e in env_raw.split(",") if e.strip()]
    json_val = _load().get(provider, {}).get("unavailable")
    json_entries = json_val if isinstance(json_val, list) else []
    return env_entries + [str(e) for e in json_entries]


def is_model_unavailable(model: str, provider: str = "claude") -> bool:
    """GH386: True iff some unavailable entry matches *model* — casefold
    equality OR same non-None family (lazy import to avoid an import cycle;
    llm_provider imports only stdlib)."""
    from lib.llm_provider import _claude_model_family

    model_cf = (model or "").casefold()
    model_family = _claude_model_family(model)
    for entry in _unavailable_entries(provider):
        entry_cf = entry.casefold()
        if entry_cf == model_cf:
            return True
        if model_family is not None and model_family == _claude_model_family(entry):
            return True
    return False

_cache: Optional[dict] = None


def reset_cache() -> None:
    """Clear the cached config. Called by tests; also useful when models.json is edited live."""
    global _cache
    _cache = None


def _load() -> dict:
    """Read + parse _resolve_config_path(). Absent file → silent debug fallback ({}).
    Unreadable/corrupt file (OSError / json.JSONDecodeError) → warning fallback ({}).
    Cached on first call.
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_resolve_config_path(), "r", encoding="utf-8") as fh:
            _cache = json.load(fh)
    except FileNotFoundError as err:
        logger.debug("model_config_absent: %s", err)
        _cache = {}
    except (OSError, json.JSONDecodeError) as err:
        logger.warning("model_config_unreadable: %s", err)
        _cache = {}
    return _cache


# §1g single source: the <provider>.<role> lookup lives HERE only; every
# get_claude_<role>() getter below is a one-line delegation.
def get_role_model(role: str, provider: str = "claude") -> "str | None":
    """GH386: chain resolution. Combine the configured value + FALLBACK_CONFIG
    value into a deduped chain, then return the first available entry; if all
    are unavailable, degrade to the chain head and warn (never crash — runtime
    markers/#385 fallback own the rest)."""
    config_value = _load().get(provider, {}).get(role)
    fallback_value = FALLBACK_CONFIG.get(provider, {}).get(role)
    chain: "list[str]" = []
    for entry in _as_chain(config_value) + _as_chain(fallback_value):
        if entry not in chain:
            chain.append(entry)
    if not chain:
        return None
    for entry in chain:
        if not is_model_unavailable(entry, provider):
            return entry
    logger.warning("model_config_all_unavailable role=%s chain=%s", role, chain)
    return chain[0]


def get_gate_floor(provider: str = "claude") -> "str | None":
    """Configured gate floor for *provider*, or None (no fallback — None means
    "use the provider's own default_gate_floor", §1x legacy-input: an existing
    models.json without a `gate_floor` key is exact current behavior)."""
    return _load().get(provider, {}).get("gate_floor")


def _required_role(role: str) -> str:
    """get_role_model for roles guaranteed by FALLBACK_CONFIG — None here means
    FALLBACK_CONFIG itself lost the role, which must fail loudly, not leak None
    through a str-annotated getter (GH485)."""
    value = get_role_model(role)
    if value is None:
        raise KeyError(f"model role {role!r} missing from models.json and FALLBACK_CONFIG")
    return value


def get_claude_primary() -> str:
    return _required_role("primary")


def get_claude_fallback() -> str:
    return _required_role("fallback")


def get_claude_critical() -> str:
    return _required_role("critical")


def get_claude_spec_writer() -> str:
    return _required_role("spec_writer")


def get_claude_discovery() -> str:
    return _required_role("discovery")


def get_claude_explore() -> str:
    return _required_role("explore")


def get_claude_decorrelated_verifier() -> str:
    return _required_role("decorrelated_verifier")


def get_claude_spec_reviewer() -> "str | None":
    """GH597: model-mix role for spec review (Optional — no FALLBACK_CONFIG entry)."""
    return get_role_model("spec_reviewer")


def get_claude_directed_repair() -> str:
    """GH386/GH597: model-mix role for directed_repair. FALLBACK_CONFIG now
    guarantees a chain (sonnet -> haiku, never opus), so this is required."""
    return _required_role("directed_repair")
