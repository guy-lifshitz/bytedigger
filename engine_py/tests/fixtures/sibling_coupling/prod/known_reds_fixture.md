# GH1200 fixture ledger (OFI 72970f44, #1165)

A machine-read markdown ledger. A sibling test hardcodes one verbatim cell of
the table below; the audit that grepped only the script/symbol name missed it.

The `pytest` cell below is the distinctiveness probe for AC33: it is a bare
lowercase word on the §1.5 stop-list, so it must be dropped BEFORE the grep
(otherwise it matches nearly every test file in the corpus — gate finding B4).

| entry | note | expiry |
|---|---|---|
| gh1165-oracle-baseline-cell | flaky under load | 2026-08-30 |
| pytest | stop-list probe cell | 2026-09-30 |
