"""GH1200 fixture (AC3) — pins ONLY the second adjacent string literal segment
of the multi-segment constant, not the concatenation.
"""


def test_hint_names_the_pip_command():
    rendered = "then rerun: pip install pydantic-ai-slim[anthropic]"
    assert "then rerun: pip install pydantic-ai-slim[anthropic]" in rendered
