"""GH1200 fixture (OFI 7efbc77a, #1112) — constant whose VALUE substrings
sibling tests pin without ever naming the symbol.

`_PIP_EXTRA_HINT` opens a parenthesised run of two adjacent string literals, so
both the concatenation and each individual segment are coupling keys.
`STATE_OK` is the noise-floor probe for AC18 (2-char value).
"""

_PIP_EXTRA_HINT = (
    "hint: install the anthropic extra "
    "then rerun: pip install pydantic-ai-slim[anthropic]"
)

STATE_OK = "ok"


def build_hint():
    return _PIP_EXTRA_HINT
