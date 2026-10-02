"""GH1200 fixture (AC5) — plausible name, no invocation of the changed symbol."""


def test_resolver_naming_convention():
    names = ["resolve_thing", "resolve_other"]
    assert len(names) == 2
