"""GH1200 fixture (AC39) — pins the LITERAL FRAGMENT of the partially-literal
constant `MIXED_HINT`, never the symbol name and never the runtime tail.

Only the literal half of that constant is greppable, so only the literal half
may become a key: this file is the reason a phantom concatenation key would
find nothing.
"""


def test_hint_fragment_is_stable():
    rendered = "gh1200-mixed-literal-fragment " + "whatever-the-tail-is"
    assert "gh1200-mixed-literal-fragment" in rendered
