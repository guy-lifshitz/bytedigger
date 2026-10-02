"""GH1200 fixture (AC22, gate finding M11) — module-object source-read.

Couples to the production module's SOURCE FORM through the imported module
object. The basename literal appears nowhere in this file, so the path-family
AND is blind to it; only `import`-coupling plus a read primitive finds it.
Real in-tree instance: engine_py/tests/test_GH866_oauth_boundary.py:84.
"""

from pathlib import Path


def test_module_source_declares_the_helper():
    try:
        import callee_mod
        from callee_mod import OTHER
    except ImportError:
        return
    src = Path(callee_mod.__file__).read_text()
    assert OTHER
    assert "def resolve_thing" in src
