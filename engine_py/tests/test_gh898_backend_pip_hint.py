"""RED tests for GH898 — install-hint for known-but-unregistered reference backend.

Spec: GH898 backend pip-hint spec (host repo Decisions memory).

7 tests, one per AC1-AC7. `_REFERENCE_BACKEND_INSTALL_HINTS` and
`_unknown_backend_error_message` do not exist yet in llm_subprocess.py, so
those two symbols are imported INSIDE each test body (never at module level)
per §1q-extension D1CF5FDF — collection must succeed even though the symbols
are absent pre-GREEN.

No mocking/patching of the UUT or of invoke_llm_subprocess (§1l/7AD3D393).
AC5/AC6 call the real invoke_llm_subprocess — the unknown-backend branch
returns before any subprocess spawn, so no burn-guard risk. reset_backends()
is called in a finally block around AC5/AC6 (§1i: singleton _BACKENDS
registry pre-staged deterministically, never raced).

conftest.py (this dir) already puts engine_py root + lib/ on sys.path at
import time (conftest-import-time singleton, §1q) — no module-level
sys.path manipulation needed here.
"""
from __future__ import annotations

from llm_subprocess import invoke_llm_subprocess, reset_backends


class TestReferenceBackendInstallHints:
    """AC1: registry exists with exactly the four known reference-backend keys."""

    def test_registry_keys_match_known_reference_backends(self):
        from llm_subprocess import _REFERENCE_BACKEND_INSTALL_HINTS

        assert set(_REFERENCE_BACKEND_INSTALL_HINTS.keys()) == {
            "agent-sdk",
            "anthropic-api",
            "pydantic-openai",
            "pydantic-anthropic",
        }


class TestReferenceBackendHintContents:
    """AC2: hint strings carry the expected pip/extras install commands."""

    def test_hint_contents_carry_expected_install_commands(self):
        from llm_subprocess import _REFERENCE_BACKEND_INSTALL_HINTS
        from package_meta import EXTRA_AGENTIC_PYDANTIC, install_hint

        assert "pip install claude-agent-sdk" in _REFERENCE_BACKEND_INSTALL_HINTS["agent-sdk"]
        # D52228C3 / GH1112: dist name single-sourced via package_meta.install_hint()
        assert _REFERENCE_BACKEND_INSTALL_HINTS["pydantic-openai"] == install_hint(EXTRA_AGENTIC_PYDANTIC)
        # D52228C3 / GH1112: the `agentic-pydantic-anthropic` extra never existed
        assert _REFERENCE_BACKEND_INSTALL_HINTS["pydantic-anthropic"] == install_hint(EXTRA_AGENTIC_PYDANTIC)


class TestUnknownBackendErrorMessageHelper:
    """AC3: helper callable, message carries unknown-backend text + hint + HAL_BUILD_PYTHON."""

    def test_helper_message_contains_backend_hint_and_venv_pointer(self):
        from llm_subprocess import _unknown_backend_error_message

        msg = _unknown_backend_error_message("agent-sdk")
        assert "unknown runner backend: 'agent-sdk'" in msg
        assert "pip install claude-agent-sdk" in msg
        assert "HAL_BUILD_PYTHON" in msg


class TestUnknownBackendErrorMessageLegacyFallback:
    """AC4: unregistered/unmapped name gets the bare legacy message, no hint tail."""

    def test_unmapped_backend_name_gets_bare_legacy_message(self):
        from llm_subprocess import _unknown_backend_error_message

        assert _unknown_backend_error_message("totally-bogus") == (
            "unknown runner backend: 'totally-bogus'"
        )


class TestInvokeLlmSubprocessKnownReferenceBackendE2E:
    """AC5: real invoke_llm_subprocess call surfaces the install-hint end to end."""

    def test_invoke_with_agent_sdk_backend_surfaces_install_hint(self):
        try:
            result = invoke_llm_subprocess(
                backend="agent-sdk",
                prompt="x",
                model="haiku",
                timeout_sec=1,
                step_name="t",
            )
            assert result.status == "error"
            assert result.error_code == "E_LLM_BACKEND_UNKNOWN"
            assert "pip install claude-agent-sdk" in result.error
            assert "HAL_BUILD_PYTHON" in result.error
        finally:
            reset_backends()


class TestInvokeLlmSubprocessUnknownBackendE2E:
    """AC6: real invoke_llm_subprocess call with a bogus name stays bare/legacy."""

    def test_invoke_with_bogus_backend_stays_bare_legacy_message(self):
        try:
            result = invoke_llm_subprocess(
                backend="totally-bogus",
                prompt="x",
                model="haiku",
                timeout_sec=1,
                step_name="t",
            )
            assert result.status == "error"
            assert result.error_code == "E_LLM_BACKEND_UNKNOWN"
            assert result.error == "unknown runner backend: 'totally-bogus'"
        finally:
            reset_backends()


class TestReferenceBackendsDocstringMentionsHalBuildPython:
    """AC7: lib/reference_backends/__init__.py module docstring documents the
    HAL_BUILD_PYTHON venv nuance."""

    def test_reference_backends_docstring_mentions_hal_build_python(self):
        import lib.reference_backends as reference_backends

        assert reference_backends.__doc__ is not None
        assert "HAL_BUILD_PYTHON" in reference_backends.__doc__
