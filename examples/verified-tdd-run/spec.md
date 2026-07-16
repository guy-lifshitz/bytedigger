# Frozen spec: slugify() for toyrepo

Status: FROZEN. Nothing below this line changes during the build; RED tests
and the GREEN implementation are both written against this document as-is.

## 1. Feature

`slugify(text)` in `toyrepo/slugify.py` turns arbitrary text into a
URL-friendly slug.

## 2. Design (frozen)

Single pure function, stdlib only:

1. Lowercase the input.
2. Replace every run of whitespace or underscores with a single hyphen.
3. Drop every remaining character that is not `a-z`, `0-9` or `-`.
4. Collapse consecutive hyphens into one.
5. Strip leading and trailing hyphens.

No classes, no configuration, no I/O. Unicode transliteration is out of
scope on purpose (see Non-goals).

## 3. Acceptance criteria

| AC | Assertion | Verified by |
|----|-----------|-------------|
| AC1 | `slugify("Hello World") == "hello-world"` | test_lowercases_and_hyphenates |
| AC2 | `slugify("a_b c") == "a-b-c"` (underscores act like spaces) | test_underscores_become_hyphens |
| AC3 | `slugify("a!@#b") == "ab"` (symbols dropped, no hyphen inserted) | test_symbols_are_dropped |
| AC4 | `slugify("a  -  b") == "a-b"` (runs collapse to one hyphen) | test_hyphen_runs_collapse |
| AC5 | `slugify("  hi  ") == "hi"` (no edge hyphens) | test_edges_are_trimmed |
| AC6 | `slugify("!!!") == ""` (symbol-only input yields empty slug) | test_symbol_only_input_is_empty |

Every AC calls the real `slugify` import. A RED test that patches or mocks
`slugify` itself is invalid by definition and gets rejected by the
stub-passability lint before implementation starts.

## 4. Files in scope

- `slugify.py` (implementation -- GREEN writes this)
- `test_slugify.py` (RED test file)

## 5. Files NOT in scope

Everything else in the toy repo. The GREEN step may not create or modify any
path outside the two files above; the demo checks each write against this
allowlist before it happens.

## 6. Non-goals

- Unicode transliteration (umlauts, Cyrillic) -- deliberately excluded to
  keep the toy stdlib-only.
- Length caps, uniqueness suffixes, locale rules.
