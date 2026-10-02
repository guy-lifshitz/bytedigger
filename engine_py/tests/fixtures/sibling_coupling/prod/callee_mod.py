"""GH1200 fixture (OFI 11b8bd6a, #1179) — call-site enumeration vs file-name
intuition.

`resolve_thing` is called by exactly three sibling fixtures; three other
plausibly-named fixtures mention it in prose only. `OTHER` is imported (never
called) by one more.
"""

OTHER = "other-fixture-constant"


def resolve_thing(name, default=None):
    return name or default
