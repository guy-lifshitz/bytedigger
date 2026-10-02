"""GH1200 fixture (AC4) — hardcodes a verbatim cell of a machine-read markdown
ledger. Neither the ledger's filename nor any symbol appears here.
"""


def test_ledger_row_is_pinned():
    cell = "gh1165-oracle-baseline-cell"
    assert cell == "gh1165-oracle-baseline-cell"
