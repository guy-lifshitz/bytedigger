"""ConfigProvider Protocol + factory seam for HAL workflow engine (E2 9AB32375).

Formalises the already-existing gate-flag config contract into an explicit,
swappable seam. An OSS consumer can inject its own config provider; HAL keeps
the current env-based default. Behavior-preserving: zero change to what HAL
gates do or how they decide.

§1g: `_DEFAULT_FACTORY` is the single source of truth for default provider
construction. `get_config` is the single resolver — the only place
construction sites should call.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

@runtime_checkable
class ConfigProvider(Protocol):
    def gate_enabled(self, env_var: str) -> bool: ...
    def flag(self, env_var: str) -> bool: ...
    def timeout_ms(self, env_var: str, default: int) -> int: ...
    def binary(self, env_var: str, default: str) -> str: ...
    def hal_root(self) -> Path: ...
    def path(self, env_var: str, default: Path) -> Path: ...

class _DefaultConfigProvider:
    """Host-neutral default provider — cwd-based, no HAL-specific env reads."""

    _org_config: dict

    def __init__(self) -> None:
        self._org_config = {}

    def gate_enabled(self, env_var: str) -> bool:
        # Canonical predicate: enabled unless explicitly "0". Unset ⇒ enabled.
        return os.environ.get(env_var) != "0"

    def flag(self, env_var: str) -> bool:
        # Default-OFF feature flag: True iff env var is exactly "1".
        return os.environ.get(env_var) == "1"

    def timeout_ms(self, env_var: str, default: int) -> int:
        # ValueError on non-int env MUST propagate (no try/except).
        return int(os.environ.get(env_var, str(default)))

    def binary(self, env_var: str, default: str) -> str:
        return os.environ.get(env_var, default)

    def int_value(self, env_var: str, default: int) -> int:
        # ValueError on non-int env MUST propagate (same contract as timeout_ms).
        return int(os.environ.get(env_var, str(default)))

    def path(self, env_var: str, default: Path) -> Path:
        # env-overridable path: Path(env) if set+non-empty, else default. No expanduser (matches _rework_log_path).
        val = os.environ.get(env_var, "")
        return Path(val) if val else default

    def hal_root(self) -> Path:
        # Priority: org_config["hal_dir"] (truthy) > cwd (neutral default, no HAL_DIR env read)
        raw = self._org_config.get("hal_dir")
        if raw:
            return Path(str(raw)).expanduser().resolve()
        return Path.cwd().resolve()

    # constitution_search_paths/memory_db_path/home_root are intentionally
    # off-Protocol (mirrors event_log_relpath et al. below) — keeps
    # ConfigProvider's runtime_checkable isinstance surface at the original
    # 6 methods (9AB32375 contract) so external minimal-Protocol implementers
    # stay isinstance-compatible.
    def constitution_search_paths(self) -> tuple[str, ...]:
        """Neutral default: single cwd-relative candidate, no host literals."""
        return ("./constitution.md",)

    def speckit_search_roots(self) -> tuple[str, ...]:
        """Neutral default: single cwd-relative SpecKit root, no host literals."""
        return ("./specs",)

    def memory_db_path(self) -> str:
        """Neutral default: cwd-anchored, mirrors the .hal-build/ convention."""
        return str(Path.cwd() / ".hal-build" / "memory.db")

    def home_root(self) -> Path:
        """Neutral default: the user-level workspace anchor. Resolved at call time."""
        return Path.home().resolve()

    def event_log_relpath(self) -> str:
        """Neutral event log relpath — relative to cwd hal_root."""
        return ".hal-build/events.jsonl"

    def reject_log_relpath(self) -> str:
        """Neutral reject-log relpath — relative to cwd hal_root."""
        return ".hal-build/reject-reasons.jsonl"

    def rework_log_relpath(self) -> str:
        """Neutral rework-log relpath — relative to cwd hal_root."""
        return ".hal-build/build-rework-log.jsonl"

    def build_runs_relpath(self) -> str:
        """Neutral build-runs relpath — relative to cwd hal_root."""
        return ".hal-build/build-runs"

    def models_config_relpath(self) -> str:
        """Neutral models config relpath — relative to hal_root."""
        return "config/models.json"

    def timeout_policy_relpath(self) -> str:
        """Neutral timeout policy relpath — relative to hal_root."""
        return "config/timeout-policy.json"

    def dbos_db_relpath(self) -> str:
        """Neutral DBOS sqlite relpath — relative to cwd hal_root."""
        return ".hal-build/dbos.sqlite"

    def resolve_event_log_root(self, fallback: Callable[[], Path]) -> Path:
        """Neutral: no HAL_DIR env read; delegates entirely to fallback()."""
        return fallback()

    def state_dir_prefix(self) -> str:
        """Neutral build-scope state-dir prefix — relative to cwd hal_root."""
        return ".hal-build/"

    def repo_top_level_dirs(self) -> tuple[str, ...]:
        """Neutral repo top-level directory names — used in prompt guidance."""
        return ("src/", "tests/")

    def foreign_state_dirname(self) -> str:
        """Neutral foreign-project state-dir name (bd convention, no leading path)."""
        return ".hal-build"

    def incident_log_relpath(self) -> str:
        """Neutral incident-ledger relpath — relative to cwd hal_root."""
        return f"{self.foreign_state_dirname()}/incidents.jsonl"

    def dispatcher_reports_relpath(self) -> str:
        """Neutral dispatcher-reports index relpath — relative to cwd hal_root."""
        return f"{self.foreign_state_dirname()}/dispatcher-reports.jsonl"

def default_security_asset(name: str, legacy_default: Path) -> Path:
    """Resolve a security asset shipped with the engine.

    The upstream build tree keeps security assets OUTSIDE the package
    (build_dir/security/, build_dir/security-lint.py) and phase code derives
    those paths via Path(__file__).parents[2]. That nesting exists only in the
    upstream checkout — from a pip install parents[2] lands in site-packages'
    parent and resolves nothing. Order: legacy path first (upstream layout
    keeps winning byte-for-byte), else the packaged copy that ships alongside
    this module (engine_py/security/ in a checkout, security/ in
    site-packages). Falls back to legacy_default when neither exists so
    callers' fail-closed error paths (E_SECURITY_RULES_MISSING,
    E_SEC_FRAGMENT_MISSING, E_SEC_LINT_UNAVAILABLE) keep firing with the
    path they always reported."""
    legacy_default = Path(legacy_default)
    if legacy_default.exists():
        return legacy_default
    packaged = Path(__file__).resolve().parent / "security" / name
    if packaged.exists():
        return packaged
    return legacy_default

def default_config_provider() -> ConfigProvider:
    return _DefaultConfigProvider()

_DEFAULT_FACTORY = default_config_provider          # §1g single source
_ORIGINAL_FACTORY = default_config_provider

def get_config() -> ConfigProvider:                  # the single resolver
    return _DEFAULT_FACTORY()

def set_default_config_provider_factory(factory) -> None:   # OSS injection
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory

def reset_default_config_provider_factory() -> None:        # §1i test teardown
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = _ORIGINAL_FACTORY

def hal_root() -> Path:
    """Free function: HAL install-root path (§1g single-source). Delegates to get_config().hal_root()."""
    return get_config().hal_root()

def path(env_var: str, default: Path) -> Path:
    """Free function: env-overridable path (§1g single-source). Delegates to get_config().path()."""
    return get_config().path(env_var, default)

def event_log_relpath() -> str:
    """Host-relative path of the canonical build-events log (single source, §1g)."""
    return get_config().event_log_relpath()  # type: ignore[attr-defined]  # relpath/resolve methods are provider-concrete, intentionally off the minimal ConfigProvider Protocol

def reject_log_relpath() -> str:
    """Host-relative path of the reject-reasons log (single source, §1g)."""
    return get_config().reject_log_relpath()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def rework_log_relpath() -> str:
    """Host-relative path of the rework-rate log (single source, §1g)."""
    return get_config().rework_log_relpath()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def build_runs_relpath() -> str:
    """Host-relative path of the build-runs artifact dir (single source, §1g)."""
    return get_config().build_runs_relpath()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def dbos_db_relpath() -> str:
    """Host-relative path of the DBOS durable-state sqlite (single source, §1g)."""
    return get_config().dbos_db_relpath()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def state_dir_prefix() -> str:
    """Host-relative build-scope state-dir prefix (single source, §1g)."""
    return get_config().state_dir_prefix()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def repo_top_level_dirs() -> tuple[str, ...]:
    """Repo top-level directory names for prompt guidance (single source, §1g)."""
    return get_config().repo_top_level_dirs()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def resolve_event_log_hal_dir(fallback: Callable[[], Path]) -> Path:
    """Event log root override: delegates to the active provider's resolve_event_log_root().
    Distinct from hal_root() — preserves event_log's legacy file-walk fallback (test A20C7674 AC6b).
    Free-fn name + signature unchanged so event_log.py import (L30) + call (L65) need NO edit."""
    return get_config().resolve_event_log_root(fallback)  # type: ignore[attr-defined]  # relpath/resolve methods are provider-concrete, intentionally off the minimal ConfigProvider Protocol

def models_config_path() -> Path:
    """Free function: path to the HAL models config (§1g single source). Delegates to hal_root() + relpath."""
    return get_config().hal_root() / get_config().models_config_relpath()  # type: ignore[attr-defined]  # relpath/resolve methods are provider-concrete, intentionally off the minimal ConfigProvider Protocol

def timeout_policy_path() -> Path:
    """Free function: path to the HAL timeout policy config (§1g single source). Delegates to hal_root() + relpath."""
    return get_config().hal_root() / get_config().timeout_policy_relpath()  # type: ignore[attr-defined]  # relpath/resolve methods are provider-concrete, intentionally off the minimal ConfigProvider Protocol

def env_opt(env_var: str) -> str | None:
    """Free function: optional env-var read; returns value if set, None if unset."""
    return os.environ.get(env_var)

def flag(env_var: str) -> bool:
    """Free function: default-OFF feature flag; True iff env var is exactly '1'. Delegates to get_config().flag()."""
    return get_config().flag(env_var)

def int_value(env_var: str, default: int) -> int:
    """Free function: int-typed env-var read with default. Delegates to get_config().int_value()."""
    return get_config().int_value(env_var, default)  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def foreign_state_dirname() -> str:
    """Host-relative foreign-project state-dir name (single source, §1g)."""
    return get_config().foreign_state_dirname()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def incident_log_relpath() -> str:
    """Host-relative path of the incident ledger (single source, §1g)."""
    return get_config().incident_log_relpath()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol

def dispatcher_reports_relpath() -> str:
    """Host-relative path of the dispatcher-reports index (single source, §1g)."""
    return get_config().dispatcher_reports_relpath()  # type: ignore[attr-defined]  # provider-concrete, off minimal Protocol
