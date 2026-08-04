"""Candidate simulation for bd#24/bd#39's `AC-C5` -- the `[G22:13]` deliverable.

NOT a pytest test and deliberately not named like one, for the same reason as
`_bd24_containment_check.py` beside it: it rewrites a module's source and
re-executes the RED dozens of times, which is a build-time audit rather than a
suite member. The leading underscore keeps pytest from collecting it.

Run it from anywhere in the worktree:

    python3 engine_py/tests/_bd24_mutation_matrix.py          # whole matrix
    python3 engine_py/tests/_bd24_mutation_matrix.py fixed_offset_operand

Exit status is 0 when every mutant dies and 1 when one survives.

WHY IT EXISTS (spec §5, "Disposal"). `[G22:13]` asks for the plausible
implementations to be ENUMERATED and each SHOWN TO DIE. Through gate round 2
that was done on paper by both the author and the gate, neither of which had a
shell. It has been executed since round 3. The harness was held back from the
repository for one stated reason -- GREEN must be written against the spec and
not copied from a validation harness -- and that reason expires the moment
GREEN lands, because by §5's own standard an unrepeatable proof is a claim.

WHAT CHANGED AT THAT HANDOVER, and it is a strengthening rather than a port.
Through round 11 the mutants were flips of a SEPARATE reference implementation
that shared an author with the spec. They are now source substitutions applied
to the SHIPPED `conformance/quant_lint.py`, so "candidate C dies" is a
statement about the code this lot actually delivers instead of about a
stand-in that agrees with it. A substitution that no longer matches its anchor
text is a hard error, not a silent skip -- a mutant that fails to apply would
otherwise report the reference's own score and read as a kill.

SCOPE OF THIS EVIDENCE, AND ITS LIMIT -- gate round 3's weighing, adopted and
not softened here. Mutation adequacy is measured against THE AUTHOR'S OWN
ENUMERATION OF DECISIONS, and a shared misreading is invisible to both the
spec and the implementation. Five times across bd#24 and bd#39 the gate named
a surviving candidate BY READING, predicted its exact score, and was right --
#28 (sentinel seam, 40/40), #29 (fixed offset, 41/41), #30 (case-sensitive row
markers, 42/42), and round 7's and round 8's survivors -- and not one of them
was in the author's enumeration at the time. Execution raises the floor. It
does not replace an adversary, and this file is not offered as if it did
(`[G22:13]`, hal#1511).

THE DIFFERENTIAL PRECONDITION (gate rounds 5 and 6, MINOR-C). "Mutant M failed
N tests" is an OBSERVATION; it becomes a CHECKED CLAIM only once M is shown to
diverge from the shipped implementation on at least one fixture. Three of this
harness's defects were mutants that expressed no candidate (`rsplit("—")[0]`,
which returns the same first segment as `split`) or far more than one (an early
`return`), and each produced a number that looked like a result.

AND THE INFERENCE FROM IT IS NOT A VERDICT (gate round 7, MINOR-B). Zero
divergence has TWO causes and this check cannot tell them apart: the mutant
failed to express its candidate, OR the candidate is real and NO FIXTURE
REACHES IT -- which is exactly what the gate exists to find. An author reading
zero divergence as "unfaithful mutant" would rewrite or discard a genuine
finding; both of round 7's MAJORs were of the second kind. A zero-divergence
mutant is therefore reported as a FORK to adjudicate, never silently dropped.
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from collections import Counter
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_ENGINE = _TESTS.parent
_IMPL = _ENGINE / "bytedigger_engine" / "conformance" / "quant_lint.py"
_RED = _TESTS / "test_bd24_quant_lint.py"

# Anchors: exact source fragments of the shipped implementation that mutants
# substitute into. Named once here so a refactor of the implementation breaks
# the matrix loudly at one place rather than silently un-killing candidates.
_A_OPERAND = "            return marker, line[len(marker):].strip()"
_A_RECOGNISE = "        if line[: len(marker)].upper() == marker:"
_A_LOOP = "    for marker in _MARKERS:"
_A_TOKENS = "    return [token for token in (raw.strip() for raw in operand.split(\",\")) if token]"
_A_ROWSUBJ = "    return operand.split(_EM_DASH, 1)[0].strip()"
_A_EMPTY_ADMITS = "            if not tokens:"
_A_ADMITS_GUARD = (
    "            if current_level is None:\n"
    "                continue\n"
    "            tokens = _reduction_tokens(operand)"
)
_A_EXCLUDES_GUARD = (
    "            if current_row is None:\n"
    "                continue\n"
    "            row = rows[current_row]"
)
_A_SEAM_GUARD = (
    "            if current_seam is None:\n"
    "                continue\n"
    "            if operand:"
)
_A_PIN = "                seams[current_seam].add(marker)"
_A_SET_LEVEL = "            current_level = operand"
_A_SET_SEAM = "            current_seam = operand"
_A_REG_SEAM = "            seams.setdefault(operand, set())"
_A_SET_ROW = "            current_row = _row_subject(operand)"
_A_REG_ROW = "            rows.setdefault(current_row, _Row())"
_A_COLLAPSE = (
    "        key = (kind, subject)\n"
    "        if key not in seen:\n"
    "            seen.add(key)\n"
    "            findings.append(Finding(kind, subject))"
)
_A_CHECK1 = "        if name not in rows:"
_A_CHECK1_LOOP = "    for name in levels:"
_A_LEVEL_TOKEN = "        if level.invalid_token:"
_A_LOOKUP = "        level = levels.get(subject)"
_A_DEFAULT = "            admits = REDUCTIONS"
_A_CHECK2 = (
    "        if not row.has_excludes or row.invalid_token or not row.excludes >= admits:\n"
    "            emit(KIND_MISSING_REDUCTIONS, subject)"
)
_A_CHECK3 = (
    "        if len(pinned) < len(_PROPERTIES):\n"
    "            emit(KIND_SEAM_NOT_PINNED, name)"
)
_A_CHECK3_LOOP = "    for name, pinned in seams.items():"
_A_BAD_ADMITS = "                    level.invalid_token = True"
_A_BAD_EXCLUDES = "                    row.invalid_token = True"
_A_INIT_ROW = "    current_row: str | None = None"
_A_INIT_SEAM = "    current_seam: str | None = None"
_A_FINDING = "    kind: str\n    subject: str"

def _offset_for(markers: str) -> tuple[str, str]:
    """A marker-family-scoped version of mutant #29's fixed offset.

    `[G24:7]`'s framing clause ranges over all eight operand-bearing markers,
    and the gate found the fixed-offset survivor three times on three
    different families (rounds 4, 6 and 7) -- so each family carries its own
    mutant rather than being taken as covered by the whole-file one.
    """
    return _A_OPERAND, (
        f"            if marker in {markers}:\n"
        f"                return marker, line[len(marker) + 1:].rstrip()\n"
        f"{_A_OPERAND}"
    )


def _anchor_closes(setter: str, victim: str) -> tuple[str, str]:
    """One ORDERED cell of `[G24:10]`'s 3 x 2 independence product (§0.9(3c)).

    Independence is not symmetric: "does an intervening X close a live Y?" and
    "does an intervening Y close a live X?" are different questions with
    different implementations, so all six cells get their own mutant.
    """
    return setter, f"{setter}\n            {victim} = None"


# name -> (spec §3 candidate IDs, [(anchor, replacement), ...])
MUTANTS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    # ── `[G24:7]` / `[G24:16]`: what an operand IS ────────────────────────
    "fixed_offset_operand": ("C-SPACING (#29)", [
        (_A_OPERAND, "            return marker, line[len(marker) + 1:].rstrip()"),
    ]),
    "fixed_offset_reduction_operand": ("C-ROWOP", [_offset_for("(_ADMITS, _EXCLUDES)")]),
    "fixed_offset_property_operand": ("C3-24", [_offset_for("_PROPERTIES")]),
    "row_fixed_offset": ("C2-32", [_offset_for("(_ROW,)")]),
    "verbatim_operand": ("C-CRLF", [
        (_A_OPERAND, "            return marker, line[len(marker):].strip(\" \\t\")"),
    ]),
    "trailing_whitespace_kept": ("C-WS", [
        (_A_OPERAND, "            return marker, line[len(marker):].lstrip()"),
    ]),
    "operand_split_every_colon": ("C1-18", [
        (_A_OPERAND, "            return marker, line.split(\":\")[1].strip()"),
    ]),
    "operand_collapse_spaces": ("C1-18", [
        (_A_OPERAND, "            return marker, \" \".join(line[len(marker):].split())"),
    ]),
    "operand_first_token": ("C1-17", [
        (_A_OPERAND, "            return marker, (line[len(marker):].split() or [\"\"])[0]"),
    ]),
    # ── `[G24:8]` / §2.2: recognition ─────────────────────────────────────
    "strip_start": ("C-INDENT", [(_A_LOOP, "    line = line.strip()\n" + _A_LOOP)]),
    "two_spellings": ("C-CASE", [
        (_A_RECOGNISE, "        if line[: len(marker)] in (marker, marker.lower()):"),
    ]),
    "case_sensitive_row_markers": ("F-5 (#30)", [
        (_A_RECOGNISE,
         "        _ci = marker in (_LEVEL, _SEAM) + _PROPERTIES\n"
         "        if line[: len(marker)] == marker or (_ci and line[: len(marker)].upper() == marker):"),
    ]),
    "indented_admits": ("C2-35", [
        (_A_LOOP, "    if line[:1].isspace() and line.strip()[:7].upper() == \"ADMITS:\":\n"
                  "        line = line.strip()\n" + _A_LOOP),
    ]),
    "indented_property_markers": ("C3-21", [
        (_A_LOOP, "    if line[:1].isspace() and any(line.strip()[: len(p)].upper() == p for p in _PROPERTIES):\n"
                  "        line = line.strip()\n" + _A_LOOP),
    ]),
    # ── `[G24:11]`: the delimiters ────────────────────────────────────────
    "separator_is_comma_space": ("C2-32", [
        (_A_TOKENS, "    return [token for token in operand.split(\", \") if token]"),
    ]),
    "reduction_tokens_strip_all_spaces": ("C2-37", [
        (_A_TOKENS, "    return [t for t in operand.replace(\" \", \"\").split(\",\") if t]"),
    ]),
    "row_dash_with_spaces": ("C1-16", [
        (_A_ROWSUBJ, "    return operand.split(\" \" + _EM_DASH + \" \", 1)[0]"),
    ]),
    "row_last_dash": ("C2-32", [
        (_A_ROWSUBJ, "    return operand.rsplit(_EM_DASH, 1)[0].strip()"),
    ]),
    "row_requires_dash": ("C2-32", [
        (_A_ROWSUBJ,
         "    return operand.split(_EM_DASH, 1)[0].strip() if _EM_DASH in operand else \"\""),
    ]),
    # ── `[G24:14]`: an empty token list is not an empty admitted set ──────
    "empty_admits_is_empty_set": ("C2-36", [(_A_EMPTY_ADMITS, "            if False:")]),
    # ── `[G24:1]` / `[G24:12]`: the reduction vocabulary ──────────────────
    "ignore_bad_excludes_token": ("C2-8", [(_A_BAD_EXCLUDES, "                    pass")]),
    "ignore_bad_admits_token": ("C2-14", [(_A_BAD_ADMITS, "                    pass")]),
    "case_fold_reduction_tokens": ("C2-7", [
        (_A_TOKENS,
         "    return [t.lower() for t in (r.strip() for r in operand.split(\",\")) if t]"),
    ]),
    "admits_token_needs_a_row": ("[G24:12]", [(_A_LEVEL_TOKEN, "        if False:")]),
    # ── `[G24:5]` / `[G24:13]`: the base case of the binding rules ────────
    "orphan_admits_raises": ("F-8", [
        (_A_ADMITS_GUARD, "            tokens = _reduction_tokens(operand)"),
    ]),
    "orphan_admits_sentinel_level": ("C2-26", [
        (_A_ADMITS_GUARD,
         "            if current_level is None:\n"
         "                current_level = \"\"\n"
         "                levels.setdefault(\"\", _Level())\n"
         "            tokens = _reduction_tokens(operand)"),
    ]),
    "orphan_sentinel_seam": ("C3-20 (#28)", [
        (_A_SEAM_GUARD,
         "            if current_seam is None:\n"
         "                current_seam = \"<unnamed>\"\n"
         "                seams.setdefault(current_seam, _EMPTY_PROPS())\n"
         "            if operand:"),
    ]),
    "forward_credit_property": ("C3-17", [
        (_A_INIT_SEAM, _A_INIT_SEAM + "\n    _pending_props: set[str] = set()"),
        (_A_SEAM_GUARD,
         "            if current_seam is None:\n"
         "                if operand:\n"
         "                    _pending_props.add(marker)\n"
         "                continue\n"
         "            if operand:"),
        (_A_REG_SEAM, _A_REG_SEAM + ".update(_pending_props)\n            _pending_props.clear()"),
    ]),
    "forward_credit_excludes": ("C2-21", [
        (_A_INIT_ROW, _A_INIT_ROW + "\n    _pending_exc: set[str] = set()"),
        (_A_EXCLUDES_GUARD,
         "            if current_row is None:\n"
         "                _pending_exc.update(_reduction_tokens(operand))\n"
         "                continue\n"
         "            row = rows[current_row]"),
        (_A_REG_ROW,
         "            _r = rows.setdefault(current_row, _Row())\n"
         "            if _pending_exc:\n"
         "                _r.has_excludes = True\n"
         "                _r.excludes.update(t for t in _pending_exc if t in REDUCTIONS)\n"
         "                _pending_exc.clear()"),
    ]),
    # ── `[G24:10]` / §0.9(3c): the six ORDERED independence cells ─────────
    "level_closes_row": ("C2-28", [_anchor_closes(_A_SET_LEVEL, "current_row")]),
    "level_closes_seam": ("C3-19", [_anchor_closes(_A_SET_LEVEL, "current_seam")]),
    "seam_closes_level": ("C2-27", [_anchor_closes(_A_SET_SEAM, "current_level")]),
    "seam_closes_row": ("C2-29", [_anchor_closes(_A_SET_SEAM, "current_row")]),
    "row_closes_level": ("C2-27", [_anchor_closes(_A_SET_ROW, "current_level")]),
    "row_closes_seam": ("C3-19", [_anchor_closes(_A_SET_ROW, "current_seam")]),
    "single_anchor_level_row": ("C2-28", [
        (_A_SET_LEVEL, _A_SET_LEVEL + "\n            _shared = operand"),
        (_A_SET_ROW, _A_SET_ROW + "\n            _shared = current_row"),
        (_A_INIT_ROW, _A_INIT_ROW + "\n    _shared: str | None = None"),
        (_A_EXCLUDES_GUARD,
         "            if _shared is None or _shared not in rows:\n"
         "                continue\n"
         "            row = rows[_shared]"),
    ]),
    "admits_previous_line_only": ("C2-27", [
        (_A_INIT_ROW, _A_INIT_ROW + "\n    _prev: str | None = None"),
        ("        marker, operand = matched",
         "        marker, operand = matched\n        _prev, _last = _last, marker"),
        (_A_INIT_SEAM, _A_INIT_SEAM + "\n    _last: str | None = None"),
        ("            if current_level is None:\n                continue\n            tokens",
         "            if current_level is None or _prev != _LEVEL:\n                continue\n            tokens"),
    ]),
    # ── P2 / P3 / P4 / `[G24:9]`: what binds to what ──────────────────────
    "row_by_proximity": ("C1-7, C2-13", [
        (_A_SET_ROW,
         "            current_row = current_level if current_level is not None else _row_subject(operand)"),
    ]),
    "excludes_by_level": ("C2-10", [
        (_A_EXCLUDES_GUARD,
         "            if current_level is None or current_level not in rows:\n"
         "                continue\n"
         "            row = rows[current_level]"),
    ]),
    "admits_by_proximity": ("C2-18", [
        (_A_INIT_ROW, _A_INIT_ROW + "\n    _near: dict[str, str] = {}"),
        (_A_REG_ROW,
         _A_REG_ROW + "\n            if current_level is not None:\n"
                      "                _near.setdefault(current_row, current_level)"),
        (_A_LOOKUP, "        level = levels.get(_near.get(subject, subject))"),
    ]),
    "seam_property_following": ("C3-14", [
        (_A_INIT_SEAM, _A_INIT_SEAM + "\n    _held: set[str] = set()"),
        (_A_SEAM_GUARD,
         "            if True:\n"
         "                if operand:\n"
         "                    _held.add(marker)\n"
         "                continue\n"
         "            if operand:"),
        (_A_REG_SEAM, _A_REG_SEAM + ".update(_held)\n            _held.clear()"),
    ]),
    "row_below_only": ("C1-14", [
        ("    for line in text.split(\"\\n\"):", "    for _ln, line in enumerate(text.split(\"\\n\")):"),
        (_A_INIT_ROW, _A_INIT_ROW + "\n    _lv_ln: dict[str, int] = {}\n    _rw_ln: dict[str, int] = {}"),
        (_A_SET_LEVEL, _A_SET_LEVEL + "\n            _lv_ln.setdefault(operand, _ln)"),
        (_A_REG_ROW, _A_REG_ROW + "\n            _rw_ln.setdefault(current_row, _ln)"),
        (_A_CHECK1, "        if name not in rows or _rw_ln.get(name, -1) < _lv_ln.get(name, 0):"),
    ]),
    "skip_undeclared": ("C2-19", [
        (_A_LOOKUP, _A_LOOKUP + "\n        if level is None:\n            continue"),
    ]),
    # ── check 1's matching relation ───────────────────────────────────────
    "case_fold_operand": ("C1-9", [
        (_A_CHECK1, "        if name.lower() not in {r.lower() for r in rows}:"),
    ]),
    "substring_a": ("C1-5", [(_A_CHECK1, "        if not any(name in r for r in rows):")]),
    "substring_b": ("C1-6", [(_A_CHECK1, "        if not any(r in name for r in rows):")]),
    "global_presence": ("C1-4", [(_A_CHECK1, "        if not rows:")]),
    "first_only": ("C1-2", [(_A_CHECK1_LOOP, "    for name in list(levels)[:1]:")]),
    "last_only": ("C1-3", [(_A_CHECK1_LOOP, "    for name in list(levels)[-1:]:")]),
    # ── check 2's relation ────────────────────────────────────────────────
    "equality": ("C2-20", [(_A_CHECK2, _A_CHECK2.replace("not row.excludes >= admits",
                                                         "row.excludes != admits"))]),
    "cardinality": ("C2-6", [
        ('    __slots__ = ("excludes", "has_excludes", "invalid_token")',
         '    __slots__ = ("excludes", "has_excludes", "invalid_token", "raw")'),
        ("        self.has_excludes = False\n        self.invalid_token = False",
         "        self.has_excludes = False\n        self.invalid_token = False\n        self.raw = 0"),
        ("            row.has_excludes = True",
         "            row.has_excludes = True\n            row.raw += len(_reduction_tokens(operand))"),
        (_A_CHECK2, _A_CHECK2.replace(
            "row.invalid_token or not row.excludes >= admits",
            "row.raw < len(admits)")),
    ]),
    "presence_only": ("C2-1", [(_A_CHECK2, _A_CHECK2.replace(
        "not row.has_excludes or row.invalid_token or not row.excludes >= admits",
        "not row.has_excludes"))]),
    "default_drops_last": ("C2-3", [(_A_DEFAULT, "            admits = REDUCTIONS - {\"last\"}")]),
    "default_drops_any": ("C2-3", [(_A_DEFAULT, "            admits = REDUCTIONS - {\"any\"}")]),
    "admits_override_ignored": ("C2-4", [("            admits = level.admits",
                                          "            admits = REDUCTIONS")]),
    "per_missing_reduction": ("C2-9", [
        (_A_CHECK2, _A_CHECK2.replace(
            "            emit(KIND_MISSING_REDUCTIONS, subject)",
            "            for _m in sorted(admits - row.excludes) or [\"\"]:\n"
            "                findings.append(Finding(KIND_MISSING_REDUCTIONS, subject))")),
    ]),
    "dup_token_is_defect": ("C2-22", [
        (_A_TOKENS, "    _t = [t for t in (r.strip() for r in operand.split(\",\")) if t]\n"
                    "    return _t + ([\"<dup>\"] if len(set(_t)) != len(_t) else [])"),
    ]),
    # ── check 3 ───────────────────────────────────────────────────────────
    "needs_a_property_line": ("C3-1", [
        (_A_REG_SEAM, "            pass"),
        (_A_PIN, "                seams.setdefault(current_seam, set()).add(marker)"),
    ]),
    "attribute_path_only": ("C3-3", [(_A_CHECK3, _A_CHECK3.replace(
        "len(pinned) < len(_PROPERTIES)", "_ATTRIBUTE_PATH not in pinned"))]),
    "two_properties_only": ("C3-4", [(_A_CHECK3, _A_CHECK3.replace(
        "len(pinned) < len(_PROPERTIES)", "not pinned >= {_ATTRIBUTE_PATH, _BINDING_TIME}"))]),
    "seam_presence_only": ("C3-2", [(_A_CHECK3, _A_CHECK3.replace(
        "len(pinned) < len(_PROPERTIES)", "not pinned"))]),
    "seam_first_only": ("C3-5", [(_A_CHECK3_LOOP, "    for name, pinned in list(seams.items())[:1]:")]),
    "seam_last_only": ("C3-6", [(_A_CHECK3_LOOP, "    for name, pinned in list(seams.items())[-1:]:")]),
    "seam_name_substring": ("C3-10, C3-11", [
        (_A_CHECK3, "        pinned = set().union(*(p for n, p in seams.items() if name in n))\n" + _A_CHECK3),
    ]),
    "seam_global_presence": ("C3-7", [
        (_A_CHECK3, "        pinned = set().union(*seams.values()) if seams else pinned\n" + _A_CHECK3),
    ]),
    "props_exactly_once": ("C3-8, C3-18", [
        (_A_REG_SEAM, "            seams.setdefault(operand, [])"),
        (_A_PIN, "                seams[current_seam].append(marker)"),
        (_A_CHECK3, _A_CHECK3.replace("len(pinned) < len(_PROPERTIES)",
                                      "len(pinned) != len(_PROPERTIES)")),
    ]),
    "per_missing_property": ("C3-9", [
        (_A_CHECK3, _A_CHECK3.replace(
            "            emit(KIND_SEAM_NOT_PINNED, name)",
            "            for _p in _PROPERTIES:\n"
            "                if _p not in pinned:\n"
            "                    findings.append(Finding(KIND_SEAM_NOT_PINNED, name))")),
    ]),
    "whitespace_only_property_pins": ("C3-24", [
        (_A_SEAM_GUARD, _A_SEAM_GUARD.replace(
            "            if operand:", "            if line[len(marker):].rstrip(\"\\r\\n\"):")),
    ]),
    "last_wins_property_value": ("C3-23", [
        (_A_SEAM_GUARD, _A_SEAM_GUARD.replace("            if operand:", "            if True:")),
        (_A_PIN, "                if operand:\n"
                 "                    seams[current_seam].add(marker)\n"
                 "                else:\n"
                 "                    seams[current_seam].discard(marker)"),
    ]),
    "first_wins_property_value": ("C3-23", [
        (_A_INIT_SEAM, _A_INIT_SEAM + "\n    _locked: set[tuple[str, str]] = set()"),
        (_A_SEAM_GUARD, _A_SEAM_GUARD.replace(
            "            if operand:",
            "            if (current_seam, marker) in _locked:\n"
            "                continue\n"
            "            _locked.add((current_seam, marker))\n"
            "            if operand:")),
    ]),
    # ── `[G24:3]`: the collapse ───────────────────────────────────────────
    "dedup_by_subject_only": ("C-COLLAPSE", [
        (_A_COLLAPSE, _A_COLLAPSE.replace("        key = (kind, subject)", "        key = subject")),
    ]),
    "no_collapse": ("[G24:3]", [
        (_A_COLLAPSE, "        findings.append(Finding(kind, subject))"),
    ]),
    # ── whole-function contract ───────────────────────────────────────────
    "finding_widened": ("F-9", [(_A_FINDING, _A_FINDING + "\n    message: str = \"\"")]),
    "module_level_state": ("F-6", [
        ("from dataclasses import dataclass", "from dataclasses import dataclass\n\n_ACCUM: list = []"),
        ("    findings: list[Finding] = []", "    findings: list[Finding] = _ACCUM"),
    ]),
}


def _load(source: str, name: str):
    """Install `source` as `conformance.quant_lint` and return the module."""
    for stale in ("bytedigger_engine.conformance.quant_lint", "conformance"):
        sys.modules.pop(stale, None)
    pkg = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("bytedigger_engine.conformance", loader=None, is_package=True)
    )
    pkg.__path__ = []
    sys.modules["bytedigger_engine.conformance"] = pkg
    spec = importlib.util.spec_from_loader("bytedigger_engine.conformance.quant_lint", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(_IMPL)
    sys.modules["bytedigger_engine.conformance.quant_lint"] = mod
    exec(compile(source, f"<{name}>", "exec"), mod.__dict__)
    pkg.quant_lint = mod
    return mod


def _red_module():
    """The RED, loaded by path. Its `conformance` imports are deferred into
    test bodies, so whatever is in `sys.modules` at CALL time is what runs."""
    spec = importlib.util.spec_from_file_location("bd24_red", _RED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixtures(red) -> dict[str, str]:
    return {n: getattr(red, n) for n in dir(red) if n.startswith("_") and n.endswith("SPEC")}


def _outputs(source: str, name: str, fixtures: dict[str, str]) -> dict[str, object]:
    """Every fixture's findings, as a comparable value. A raise is a result."""
    lint = _load(source, name).lint_quantifier_completeness
    out: dict[str, object] = {}
    for fname, text in fixtures.items():
        try:
            out[fname] = sorted(Counter((f.kind, f.subject) for f in lint(text)).items())
        except Exception as exc:  # noqa: BLE001 -- a raise IS the observation
            out[fname] = f"RAISED:{type(exc).__name__}"
    return out


def _run_red(source: str, name: str, red) -> list[tuple[str, str]]:
    """Run every test in the RED against `source`; return the failures."""
    _load(source, name)
    failed = []
    for cls_name in dir(red):
        cls = getattr(red, cls_name)
        if not (isinstance(cls, type) and cls_name.startswith("Test")):
            continue
        for meth in dir(cls):
            if not meth.startswith("test_"):
                continue
            try:
                getattr(cls(), meth)()
            except Exception:  # noqa: BLE001 -- collecting, not handling
                failed.append((f"{cls_name}::{meth}", traceback.format_exc().splitlines()[-1]))
    return failed


def _mutate(source: str, name: str) -> str:
    """Apply one mutant's substitutions. A missed anchor is a HARD ERROR.

    Silently skipping would make the mutant report the shipped
    implementation's own score, which reads in the matrix exactly like a kill.
    """
    _, edits = MUTANTS[name]
    for old, new in edits:
        if source.count(old) != 1:
            raise SystemExit(
                f"mutant {name!r}: anchor matched {source.count(old)} times, expected 1:\n"
                f"---\n{old}\n---\nThe implementation moved; re-point the anchor."
            )
        source = source.replace(old, new)
    return source


def main(argv: list[str]) -> int:
    base = _IMPL.read_text(encoding="utf-8")
    red = _red_module()
    fixtures = _fixtures(red)

    ref_failed = _run_red(base, "reference", red)
    ref_out = _outputs(base, "reference", fixtures)
    total = len(ref_failed) + sum(
        1
        for c in (getattr(red, n) for n in dir(red) if n.startswith("Test"))
        if isinstance(c, type)
        for m in dir(c)
        if m.startswith("test_")
    )
    print(f"SHIPPED: {total - len(ref_failed)} passed, {len(ref_failed)} failed "
          f"({len(fixtures)} fixtures)")
    for name, why in ref_failed:
        print(f"   FAIL {name}: {why[:100]}")
    if ref_failed:
        return 1

    names = argv or sorted(MUTANTS)
    survivors, forks = [], []
    for name in names:
        cands, _ = MUTANTS[name]
        source = _mutate(base, name)
        diverged = [k for k, v in ref_out.items() if v != _outputs(source, name, fixtures).get(k)]
        failed = _run_red(source, name, red)
        note = ""
        if not diverged and not failed:
            forks.append(name)
            note = "  <- ZERO DIVERGENCE: FORK, adjudicate (unfaithful mutant, or real and unmeasured)"
        elif not diverged:
            note = "  <- killed structurally, not by output"
        if not failed:
            survivors.append(name)
        print(f"{name:34s} [{cands:16s}] diverges {len(diverged):2d}/{len(fixtures)}, "
              f"kills {len(failed):2d} tests{note}")

    print(f"\n{len(names)} mutants: {len(names) - len(survivors)} died, {len(survivors)} survived")
    if forks:
        print(f"FORKS (zero divergence, NOT a verdict): {', '.join(forks)}")
    if survivors:
        print(f"SURVIVORS: {', '.join(survivors)}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ENGINE))
    raise SystemExit(main(sys.argv[1:]))
