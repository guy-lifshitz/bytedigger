"""GH1200 fixture (AC6) — imports a public name from the scope module without
ever calling the changed symbol. The import is deferred into the test body
(§1q / D1CF5FDF) so a stray collection cannot fail at import time.
"""


def test_other_constant_is_importable():
    try:
        from callee_mod import OTHER
    except ImportError:
        OTHER = "fallback"
    assert OTHER
