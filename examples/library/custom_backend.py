#!/usr/bin/env python3
"""Plug your own LLM into ByteDigger -- the backend injection seam.

Every LLM call in the pipeline goes through `invoke_llm_subprocess`, which
dispatches to a named backend from a registry. `register_backend` is the
public seam: register a callable matching the `LLMBackend` protocol and the
whole engine routes through it -- your API client, a replay cache, a spy
for tests.

This example registers a keyless stub so it runs without any API key:

    python3 examples/library/custom_backend.py

For real backends, install the pydantic-ai battery and set the vendor keys:

    pip install -e "engine_py[agentic-pydantic]"
    # pydantic_openai:   AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT
    # anthropic_api:     ANTHROPIC_API_KEY

then pass `backend="pydantic_openai"` (or "anthropic_api") instead of the
stub name below. `run.py` auto-registers both when the battery is installed.
"""
import sys
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[2] / "engine_py"
if _ENGINE.is_dir():
    sys.path.insert(0, str(_ENGINE))

from bytedigger_engine.contracts import StepResult                                    # noqa: E402
from bytedigger_engine.llm_subprocess import (                                        # noqa: E402
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)


def stub_backend(
    *,
    prompt: str,
    model: str,
    timeout_sec: int,
    step_name: str,
    extra_data,
    allowed_tools,
    run_ctx,
    hard_gate: bool,
    gate_label,
    straggler_cfg,
    idle_timeout_sec,
    stable_prefix: str = "",
) -> StepResult:
    """LLMBackend protocol implementation. Swap the body for a real client:
    send `prompt` to your API with `model`, return the text in `raw_response`.
    """
    answer = f"[stub:{model}] saw {len(prompt)} prompt bytes for step {step_name}"
    return StepResult(
        status="ok",
        data={"raw_response": answer, "response_bytes": len(answer)},
        duration_ms=0,
        step_name=step_name,
    )


def main() -> int:
    register_backend(
        "my-stub",
        stub_backend,
        manifest_source="orchestrator_observed",
        overwrite=True,
    )

    result = invoke_llm_subprocess(
        prompt="Summarize: the quick brown fox jumps over the lazy dog.",
        model="stub-model",
        timeout_sec=30,
        step_name="example_call",
        backend="my-stub",
    )
    print(f"status: {result.status}")
    print(f"response: {result.data['raw_response']}")

    # Restore the shipped defaults -- pair every register with a reset in tests.
    reset_backends()
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
