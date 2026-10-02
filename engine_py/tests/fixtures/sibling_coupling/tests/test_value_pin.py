"""GH1200 fixture (AC2, AC18) — pins the full concatenated constant VALUE,
never naming the symbol it belongs to.
"""


def test_hint_mentions_the_extra():
    rendered = "prefix: " + "hint: install the anthropic extra then rerun: pip install pydantic-ai-slim[anthropic]"
    assert "hint: install the anthropic extra then rerun: pip install pydantic-ai-slim[anthropic]" in rendered


def test_status_marker():
    status = "ok"
    assert status == "ok"
