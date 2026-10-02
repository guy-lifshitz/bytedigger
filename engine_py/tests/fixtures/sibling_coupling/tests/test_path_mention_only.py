"""GH1200 fixture (AC20) — names the production artifact and nothing else.

Deliberately contains NO spawn primitive and NO file-read primitive anywhere,
so the path family must still emit a bare `path-literal` row: a production path
literal inside a test IS coupling and must not be silent (spec §1.3).
"""

EXPECTED_CLI = "cli_target.sh"


def test_expected_cli_name_is_stable():
    assert EXPECTED_CLI.endswith(".sh")
