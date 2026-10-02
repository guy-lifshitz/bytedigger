"""GH1200 fixture (AC43, gate round-2 finding N10) — import-channel
over-detection probe.

The bare module stem callee_mod appears in this docstring and in a comment
below. There is NO import statement anywhere in this file, and the basename
literal with its extension never appears, so neither the `import` channel nor
the path family may emit a row for this file.

`tests/test_import_only.py` is the positive control in the same run: a bare
stem match must not be treated as an import, but a real import must still be.
"""


def test_prose_mention_is_not_coupling():
    # callee_mod is only named here in prose; nothing is imported from it.
    note = "prose-only mention, no binding"
    assert note
