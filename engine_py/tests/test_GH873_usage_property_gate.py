"""RED tests — `_extract_usage_tokens` pydantic-ai 2.10 property-form support (#873).

Spec: SHARED/memory/Decisions/2026-07-15_GH873_pydantic_usage_property_spec.md

6 tests, one per AC1-AC6. The module `lib.reference_backends.pydantic_openai`
and the `_extract_usage_tokens` symbol already exist today, so this file
collects cleanly; the import happens inside a small helper (mirroring the
sibling GH852/GH835 pattern) and each test asserts on the REAL function
directly (no mocking of the UUT itself, per §1l stub-passability).

§1q check: NO spec_from_file_location / exec_module used; no sys.path
mutation at module level; no `from conftest import`.
§1l: `_extract_usage_tokens` is imported and called directly, unstubbed.

Pre-GREEN predict: AC1, AC2, AC3, AC4 FAIL (callable-only gate rejects the
non-callable `usage` property / re-raises on a raising property); AC5, AC6
PASS (already-correct behavior pinned as regression guards).
"""
from __future__ import annotations

import types

import pytest


def _import_openai_backend():
    from bytedigger_engine.lib.reference_backends import pydantic_openai as mod  # noqa: PLC0415
    return mod


# ---------------------------------------------------------------------------
# AC1 — non-callable `usage` property, v2 names -> (1316, 80)
# ---------------------------------------------------------------------------

def test_ac1_noncallable_usage_property_v2_names():
    mod = _import_openai_backend()
    extract = mod._extract_usage_tokens

    usage_obj = types.SimpleNamespace(input_tokens=1316, output_tokens=80)
    agent_result = types.SimpleNamespace(usage=usage_obj)

    tokens_in, tokens_out = extract(agent_result)
    assert (tokens_in, tokens_out) == (1316, 80), (
        "AC1: non-callable `usage` property (pydantic-ai >=2.10) with v2 "
        f"names must yield (1316, 80), got ({tokens_in}, {tokens_out})"
    )


# ---------------------------------------------------------------------------
# AC2 — `usage` @property raises RuntimeError -> (None, None), no escape
# ---------------------------------------------------------------------------

def test_ac2_raising_usage_property_yields_none_none():
    mod = _import_openai_backend()
    extract = mod._extract_usage_tokens

    class RaisingUsageResult:
        @property
        def usage(self):
            raise RuntimeError("boom")

    agent_result = RaisingUsageResult()

    try:
        tokens_in, tokens_out = extract(agent_result)
    except RuntimeError:
        pytest.fail(
            "AC2: a raising `usage` @property getter must not escape "
            "_extract_usage_tokens; expected (None, None)"
        )

    assert (tokens_in, tokens_out) == (None, None), (
        "AC2: raising `usage` property must yield (None, None), got "
        f"({tokens_in}, {tokens_out})"
    )


# ---------------------------------------------------------------------------
# AC3 — non-callable `usage` property, v1 names fallback -> (55, 6)
# ---------------------------------------------------------------------------

def test_ac3_noncallable_usage_property_v1_names_fallback():
    mod = _import_openai_backend()
    extract = mod._extract_usage_tokens

    usage_obj = types.SimpleNamespace(request_tokens=55, response_tokens=6)
    agent_result = types.SimpleNamespace(usage=usage_obj)

    tokens_in, tokens_out = extract(agent_result)
    assert (tokens_in, tokens_out) == (55, 6), (
        "AC3: non-callable `usage` property with only v1 names "
        f"(request_tokens/response_tokens) must yield (55, 6), got "
        f"({tokens_in}, {tokens_out})"
    )


# ---------------------------------------------------------------------------
# AC4 — bool-guard applies on the property path too
# ---------------------------------------------------------------------------

def test_ac4_bool_guard_on_property_path():
    mod = _import_openai_backend()
    extract = mod._extract_usage_tokens

    usage_obj = types.SimpleNamespace(input_tokens=True, output_tokens=80)
    agent_result = types.SimpleNamespace(usage=usage_obj)

    tokens_in, tokens_out = extract(agent_result)
    assert (tokens_in, tokens_out) == (None, 80), (
        "AC4: bool `input_tokens` on the non-callable `usage` property path "
        f"must be rejected (None), got ({tokens_in}, {tokens_out})"
    )


# ---------------------------------------------------------------------------
# AC5 — non-callable `usage` property with no token attrs -> (None, None)
# ---------------------------------------------------------------------------

def test_ac5_noncallable_usage_property_no_token_attrs():
    mod = _import_openai_backend()
    extract = mod._extract_usage_tokens

    usage_obj = types.SimpleNamespace()
    agent_result = types.SimpleNamespace(usage=usage_obj)

    tokens_in, tokens_out = extract(agent_result)
    assert (tokens_in, tokens_out) == (None, None), (
        "AC5: non-callable `usage` property with no token attributes must "
        f"yield (None, None), got ({tokens_in}, {tokens_out})"
    )


# ---------------------------------------------------------------------------
# AC6 — legacy callable `usage()` form still works (regression pin)
# ---------------------------------------------------------------------------

def test_ac6_legacy_callable_usage_regression_pin():
    mod = _import_openai_backend()
    extract = mod._extract_usage_tokens

    usage_obj = types.SimpleNamespace(input_tokens=111, output_tokens=22)
    agent_result = types.SimpleNamespace(usage=lambda: usage_obj)

    tokens_in, tokens_out = extract(agent_result)
    assert (tokens_in, tokens_out) == (111, 22), (
        "AC6: legacy callable `usage()` form must keep working, got "
        f"({tokens_in}, {tokens_out})"
    )
