"""ProviderSpec seam — provider-agnostic LLM invocation (GH451).

Frozen spec: the state dir's memory/Decisions/2026-07-09_GH451_llm_provider_port_spec.md

Houses the PURE logic formerly hardcoded in llm_subprocess.py: argv building,
stream-json flags, model-family taxonomy, rank ladder, and last-result-event
parsing for the Claude provider. `llm_subprocess.py` delegates to
`get_provider()` for these; this module must NOT import from llm_subprocess
(avoids an import cycle — llm_subprocess imports this module).

No second real provider ships yet (§2.4 non-goal) — the registry +
register_provider/reset_providers seam is proven by the stub-provider tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    build_argv: Callable[[str], "list[str]"]
    stream_flags: "tuple[str, ...]"
    parse_result: Callable[["list[dict]"], "dict | None"]
    model_rank: "dict[str, int]"
    model_family: Callable[["str | None"], "str | None"]
    default_gate_floor: str


def _claude_build_argv(model: str) -> "list[str]":
    """Base Claude headless argv. Moved verbatim from llm_subprocess._build_claude_argv."""
    return ["claude", "-p", "--model", model]


def _is_fable_model(model: str) -> bool:
    """Return True if model name unambiguously identifies a Fable variant.

    Anchored to avoid substring false-positives like 'fablegrande' or
    'affable'. Accepts: 'fable', 'fable-<suffix>', '<prefix>/fable',
    '<prefix>-fable-<suffix>', '<prefix>-fable'.
    """
    m = model.lower()
    return (
        m == "fable"
        or m.startswith("fable-")
        or "/fable" in m
        or "-fable-" in m
        or m.endswith("-fable")
    )


def _is_opus_model(model: str) -> bool:
    """Return True if model name unambiguously identifies an Opus variant.

    Anchored to avoid substring false-positives like 'octopus-3' or
    'claude-not-opus'. Accepts: 'opus', 'opus-<suffix>', '<prefix>/opus',
    '<prefix>-opus', '<prefix>-opus-<suffix>'.
    """
    m = model.lower()
    return (
        m == "opus"
        or m.startswith("opus-")
        or "/opus" in m
        or "-opus-" in m
        or m.endswith("-opus")
    )


def _claude_model_family(model: "str | None") -> "str | None":
    """Return a canonical family string for *model*, or None if unrecognised.

    Families: "fable", "opus", "sonnet", "haiku". None/empty input → None.
    Moved verbatim from llm_subprocess._model_family.
    """
    if not model:
        return None
    m = model.lower()
    if _is_fable_model(m):
        return "fable"
    if _is_opus_model(m):
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return None


def _claude_parse_result(events: "list[dict]") -> "dict | None":
    """Return the LAST event with ``type == "result"``, or None. Moved verbatim
    from llm_subprocess._find_last_result_event (reverse walk — trailing events
    must not shadow the result)."""
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("type") == "result":
            return ev
    return None


# haiku-pinned SIMPLE RED/GREEN stay haiku. Rank keys MUST stay in sync with
# _claude_model_family's recognized families.
CLAUDE_PROVIDER = ProviderSpec(
    name="claude",
    build_argv=_claude_build_argv,
    stream_flags=("--output-format", "stream-json", "--verbose"),
    parse_result=_claude_parse_result,
    model_rank={"haiku": 0, "sonnet": 1, "opus": 2, "fable": 3},
    model_family=_claude_model_family,
    default_gate_floor="opus",
)

_PROVIDERS: "dict[str, ProviderSpec]" = {"claude": CLAUDE_PROVIDER}
_DEFAULT_PROVIDERS: "dict[str, ProviderSpec]" = dict(_PROVIDERS)
_DEFAULT_PROVIDER = "claude"
_PROVIDER_ENV_VAR = "HAL_LLM_PROVIDER"


def register_provider(spec: ProviderSpec) -> None:
    """Register a new provider spec. Mirrors register_backend (llm_subprocess.py:1675)."""
    if not spec.name or not isinstance(spec.name, str):
        raise ValueError("register_provider: spec.name must be a non-empty str")
    _PROVIDERS[spec.name] = spec


def reset_providers() -> None:
    """Restore the provider registry to its built-in defaults. Test-only. Mirrors
    reset_backends (llm_subprocess.py:1711)."""
    global _PROVIDERS
    _PROVIDERS = dict(_DEFAULT_PROVIDERS)


def get_provider(name: "str | None" = None) -> ProviderSpec:
    """Resolve a ProviderSpec by precedence: explicit name > env HAL_LLM_PROVIDER > "claude".

    Unknown provider name raises KeyError listing the known providers.
    """
    resolved = name or os.environ.get(_PROVIDER_ENV_VAR) or _DEFAULT_PROVIDER
    try:
        return _PROVIDERS[resolved]
    except KeyError:
        known = sorted(_PROVIDERS)
        raise KeyError(
            f"unknown LLM provider {resolved!r}; known providers: {known}"
        ) from None
