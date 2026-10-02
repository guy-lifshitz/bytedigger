# GH1200 fixture spec — INCOMPLETE sibling audit (AC7, AC9)

Reproduces the four OFIs: the auditor named only the one coupled test they
happened to grep for, so every other coupled row must come back MISSING.

## §4 Sibling-test audit

| Test | Channel |
|---|---|
| `tests/test_source_read.py` | source-read |

## §5 Scope

- `prod/source_read_target.py`
- `prod/value_const.py`
