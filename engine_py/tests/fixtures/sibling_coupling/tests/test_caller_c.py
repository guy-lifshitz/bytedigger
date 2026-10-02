"""GH1200 fixture (AC5) — a true call-site of the changed symbol."""


def _local_resolver(name, default=None):
    return name or default


def test_resolves_falsy():
    resolve_thing = _local_resolver
    assert resolve_thing("gamma") == "gamma"
