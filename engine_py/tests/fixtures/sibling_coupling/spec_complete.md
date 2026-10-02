# GH1200 fixture spec — COMPLETE sibling audit (AC8, AC30)

The **table below is the load-bearing part of this fixture** (gate round-2
advisory A15): AC8's zero-MISSING verdict and AC30's `W_SPEC_UNCOUPLED` both
read it, and neither depends on any prose sentence in this file. Every row's
`Intent` column states which of the two directions of §1.6 that row exercises.

## §4 Sibling-test audit

| Test | Channel | Intent |
|---|---|---|
| `tests/test_source_read.py` | source-read | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_value_pin.py` | value-literal | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_value_pin_segment.py` | value-literal | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_data_cell.py` | data-cell | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_caller_a.py` | call-site | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_caller_b.py` | call-site | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_caller_c.py` | call-site | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_import_only.py` | import | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_module_file_read.py` | import + source-read (module object) | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_exec_invoke.py` | exec-invocation | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_exec_and_read.py` | exec-invocation (exec > read) | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_path_mention_only.py` | path-literal | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_mixed_literal_pin.py` | value-literal (literal fragment) | coupled + cited ⇒ `cited` (AC8) |
| `tests/test_noncaller_a.py` | none | **cited but UNCOUPLED** ⇒ `W_SPEC_UNCOUPLED` (AC30) |
| `tests/test_noncaller_b.py` | none | cited but uncoupled ⇒ `W_SPEC_UNCOUPLED` |
| `tests/test_noncaller_c.py` | none | cited but uncoupled ⇒ `W_SPEC_UNCOUPLED` |

## §5 Scope

- `prod/source_read_target.py`
- `prod/value_const.py`
- `prod/callee_mod.py`
- `prod/clean_target.py`
- `prod/known_reds_fixture.md`
- `prod/cli_target.sh`
- `prod/mixed_literal.py`
