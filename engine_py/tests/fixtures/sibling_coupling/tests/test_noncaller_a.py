"""GH1200 fixture (AC5) — file-name intuition would name this file, but it
never invokes the changed symbol; it mentions it in prose/data only.
"""


def test_resolution_docstring_smoke():
    mentioned = "resolve_thing"
    assert mentioned
