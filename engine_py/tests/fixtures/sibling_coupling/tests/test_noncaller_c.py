"""GH1200 fixture (AC5) — plausible name, no invocation of the changed symbol."""


def test_resolver_absent():
    assert "resolve_thing" != "resolve_other"
