"""GH733 — mypy [arg-type] error at anthropic_api.py:172 (_build_request_body call).

AC1: lenient mypy reports ZERO [arg-type] errors for anthropic_api.py (the RED
     driver — fails today because line 172's `**_build_kwargs` call site trips
     mypy's arg-type checker; must pass after the GREEN fix).
AC2: invariant guard — _build_request_body with stable_prefix="" produces a
     plain-string messages[0]["content"], identical to omitting the kwarg.
AC3: invariant guard — _build_request_body with a stable_prefix substring
     produces the ordered 3-block content list with cache_control on the
     stable-prefix block.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine.lib.reference_backends import anthropic_api


def _engine_py_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if (root / "bytedigger_engine/mypy.ini").exists():
        return root
    for parent in root.parents:
        if (parent / "mypy.ini").exists():
            return parent
    raise AssertionError("could not locate engine_py root containing mypy.ini")


class TestGH733MypyArgTypeBuildRequestBody:
    def test_ac1_mypy_reports_zero_arg_type_errors_for_anthropic_api(self) -> None:
        root = _engine_py_root()
        if shutil.which("uvx"):
            argv = ["uvx", "mypy", "--config-file", "mypy.ini",
                    "lib/reference_backends/anthropic_api.py"]
        elif shutil.which("mypy"):
            argv = ["mypy", "--config-file", "mypy.ini",
                    "lib/reference_backends/anthropic_api.py"]
        else:
            pytest.skip("neither uvx nor mypy is available on PATH")

        proc = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = proc.stdout + proc.stderr

        offending = [
            line for line in output.splitlines()
            if "anthropic_api.py" in line and "[arg-type]" in line
        ]
        assert offending == [], (
            "expected zero [arg-type] errors for anthropic_api.py, found:\n"
            + "\n".join(offending)
            + "\n\nfull mypy output:\n"
            + output
        )

    def test_ac2_empty_stable_prefix_yields_plain_string_content(self) -> None:
        prompt = "hello world"

        body_with_empty_kwarg = anthropic_api._build_request_body(
            prompt,
            "model-id",
            model="m",
            step_name="s",
            hard_gate=True,
            stable_prefix="",
        )
        body_without_kwarg = anthropic_api._build_request_body(
            prompt,
            "model-id",
            model="m",
            step_name="s",
            hard_gate=True,
        )

        content = body_with_empty_kwarg["messages"][0]["content"]
        assert isinstance(content, str)
        assert content == prompt
        assert json.dumps(body_with_empty_kwarg, sort_keys=True) == json.dumps(
            body_without_kwarg, sort_keys=True
        )

    def test_ac3_stable_prefix_substring_yields_ordered_three_block_content(self) -> None:
        prompt = "AAA<<TEMPLATE>>BBB"
        stable_prefix = "<<TEMPLATE>>"

        body = anthropic_api._build_request_body(
            prompt,
            "model-id",
            model="m",
            step_name="s",
            hard_gate=True,
            stable_prefix=stable_prefix,
        )

        content = body["messages"][0]["content"]
        assert isinstance(content, list)
        assert len(content) == 3
        assert content[0] == {"type": "text", "text": "AAA"}
        assert content[1] == {
            "type": "text",
            "text": "<<TEMPLATE>>",
            "cache_control": {"type": "ephemeral"},
        }
        assert content[2] == {"type": "text", "text": "BBB"}
