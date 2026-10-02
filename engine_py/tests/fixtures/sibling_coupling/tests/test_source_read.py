"""GH1200 fixture (AC1) — couples to the production SOURCE FORM only.

The path-construction line carries the basename literal; the assert names
nothing greppable. Body is import-light and trivially passing so a stray
collection by the real pytest corpus cannot error.
"""

from pathlib import Path


def test_stream_uses_async_iteration():
    prod = Path(__file__).resolve().parent.parent / "prod" / "source_read_target.py"
    src = prod.read_text()
    assert "async for" in src
