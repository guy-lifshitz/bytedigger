"""Rule E on the pytest grammar — port of HAL hal#1541 (`empty_input_assert`).

Ordering, declared: the scanner was written BEFORE this file, because §1b demands a
live baseline measured before the rule is frozen, and a baseline needs an instrument.
The forms froze at that measurement (472 files, 183 candidates, 97 controlled, 86
violations in 38 files); these tests are written against the frozen forms and include
edges chosen to break the scanner, not to confirm it.

No enforcement layer exists for this module in bytedigger — see bd#66. These tests are
the only thing that runs it.
"""

from __future__ import annotations

import pytest

from bytedigger_engine.empty_input_assert import (
    identity_ok,
    scan_empty_input_asserts,
    summarize_empty_input_asserts,
)


def _sum(src: str):
    return summarize_empty_input_asserts(src)


# ─── candidates: the three frozen shapes of "this population is empty" ──────────


@pytest.mark.parametrize(
    "body",
    [
        "    assert findings == []",
        "    assert len(offenders) == 0",
        "    assert not violations",
        "    assert errors == set()",
        "    assert len(divergences) == 0",
    ],
)
def test_ac1_bare_emptiness_claim_is_an_uncontrolled_finding(body):
    s = _sum(f"def test_x():\n{body}\n")
    assert len(s["findings"]) == 1, f"{body!r} -> {s}"
    assert s["candidates"] == 1
    assert s["controlled"] == 0


def test_ac2_subject_without_a_population_word_is_not_a_candidate():
    """`assert total == 0` says nothing about a scanner having run."""
    s = _sum("def test_x():\n    assert total == 0\n    assert not flag\n")
    assert s["candidates"] == 0
    assert s["findings"] == []


# ─── controls: a live denominator in the same scope ─────────────────────────────


@pytest.mark.parametrize(
    "ctrl",
    [
        "    assert len(scanned) > 0",
        "    assert len(scanned) >= 1",
        "    assert len(scanned) == 3",
        "    assert n_scanned > 0",
        "    assert corpus_records",
    ],
)
def test_ac3_a_denominator_in_scope_turns_a_candidate_into_controlled(ctrl):
    s = _sum(f"def test_x():\n{ctrl}\n    assert findings == []\n")
    assert s["controlled"] == 1, f"{ctrl!r} -> {s}"
    assert s["findings"] == []


def test_ac4_exit_code_subject_is_not_a_denominator():
    """The defect edition E exists to fix: `rc`/`code`/`status` are not magnitudes
    that could have been zero, so they cannot vouch for a scanner having run."""
    for codey in ("assert rc > 0", "assert result.returncode > 0", "assert exit_code == 2"):
        s = _sum(f"def test_x():\n    {codey}\n    assert findings == []\n")
        assert len(s["findings"]) == 1, f"{codey!r} counted as a denominator: {s}"


def test_ac5_ge_zero_is_vacuous_and_is_not_a_denominator():
    """`>= 0` is true of every count, including the count a broken scanner produces."""
    s = _sum("def test_x():\n    assert len(scanned) >= 0\n    assert findings == []\n")
    assert len(s["findings"]) == 1, s


# ─── escapes ────────────────────────────────────────────────────────────────────


def test_ac6_wellformed_escape_within_the_four_line_window():
    src = (
        "def test_x():\n"
        "    # empty-ok: #66 corpus is empty by construction here\n"
        "    assert findings == []\n"
    )
    s = _sum(src)
    assert s["escaped"] == 1 and s["findings"] == [], s


def test_ac7_malformed_escape_does_not_escape():
    """No issue number, or a reason under 12 chars — otherwise the hatch becomes the
    way to pass the rule."""
    for bad in ("# empty-ok: corpus empty", "# empty-ok: #66 short"):
        s = _sum(f"def test_x():\n    {bad}\n    assert findings == []\n")
        assert s["escaped"] == 0 and len(s["findings"]) == 1, f"{bad!r} -> {s}"


def test_ac8_escape_outranks_control_and_is_not_double_counted():
    src = (
        "def test_x():\n"
        "    assert len(scanned) > 0\n"
        "    # empty-ok: #66 deliberately asserted on an empty corpus\n"
        "    assert findings == []\n"
    )
    s = _sum(src)
    assert (s["escaped"], s["controlled"]) == (1, 0), s
    assert identity_ok(s["candidates"], s["controlled"], s["escaped"], len(s["findings"]))


def test_ac9_escape_five_lines_above_is_out_of_window():
    src = (
        "def test_x():\n"
        "    # empty-ok: #66 this is too far above to apply\n"
        "    a = 1\n"
        "    b = 2\n"
        "    c = 3\n"
        "    assert findings == []\n"
    )
    s = _sum(src)
    assert s["escaped"] == 0 and len(s["findings"]) == 1, s


# ─── scope ──────────────────────────────────────────────────────────────────────


def test_ac10_a_denominator_in_another_test_does_not_cover_this_one():
    src = (
        "def test_a():\n"
        "    assert len(scanned) > 0\n"
        "\n"
        "def test_b():\n"
        "    assert findings == []\n"
    )
    s = _sum(src)
    assert len(s["findings"]) == 1, s
    assert s["findings"][0]["line"] == 5


def test_ac11_module_without_a_test_function_is_one_whole_module_scope():
    s = _sum("assert len(scanned) > 0\nassert findings == []\n")
    assert s["controlled"] == 1 and s["findings"] == [], s


# ─── what the parser buys over HAL's regex original ─────────────────────────────


def test_ac12_a_candidate_inside_a_string_literal_is_not_matched():
    """The TS original walks text and would match this; `ast` cannot."""
    s = _sum('def test_x():\n    doc = "assert findings == []"\n    assert len(scanned) > 0\n')
    assert s["candidates"] == 0, s


def test_ac13_a_multiline_assert_is_one_node_not_a_line_window():
    src = "def test_x():\n    assert (\n        findings\n        == []\n    )\n"
    s = _sum(src)
    assert len(s["findings"]) == 1, s
    assert s["findings"][0]["line"] == 2


def test_ac14_unparseable_source_is_reported_as_nothing_found_not_as_a_crash():
    s = _sum("def test_x(:\n    assert findings == []\n")
    assert s == {"candidates": 0, "controlled": 0, "escaped": 0, "findings": [], "identity_ok": True}


# ─── identity: every candidate lands in exactly one bucket ──────────────────────


def test_ac15_identity_holds_on_a_mixed_module():
    src = (
        "def test_a():\n"
        "    assert findings == []\n"
        "def test_b():\n"
        "    assert len(scanned) > 0\n"
        "    assert not offenders\n"
        "def test_c():\n"
        "    # empty-ok: #66 empty corpus is the point of this case\n"
        "    assert violations == []\n"
    )
    s = _sum(src)
    assert (s["candidates"], s["controlled"], s["escaped"], len(s["findings"])) == (3, 1, 1, 1), s
    assert s["identity_ok"]


def test_ac16_scan_and_summarize_agree_one_walker_not_two():
    src = "def test_x():\n    assert findings == []\n    assert not offenders\n"
    assert scan_empty_input_asserts(src) == _sum(src)["findings"]


# ─── the live corpus: the frozen §1b baseline, and the identity on all of it ────


def test_ac17_identity_holds_across_the_whole_live_corpus():
    """A scanner whose buckets do not add up is reporting about a population it did
    not fully walk. This is the only test here that reads the real corpus."""
    from pathlib import Path

    # NOT `git ls-files`: the clean-room job runs from a tree that is not a git
    # repository, and the subprocess died with exit 128 there while passing in the
    # git-backed job. The corpus is a directory, so read it as one.
    here = Path(__file__).resolve().parent
    listed = sorted(p for p in here.glob("test_*.py"))
    assert len(listed) > 100, f"fixture premise: corpus not taken, got {len(listed)} files"

    broken = []
    for rel in listed:
        s = _sum(rel.read_text(encoding="utf-8"))
        if not identity_ok(s["candidates"], s["controlled"], s["escaped"], len(s["findings"])):
            broken.append(rel)
    assert broken == [], broken
