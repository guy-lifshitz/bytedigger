"""RED tests for bd#22 (L2): conformance package, shared contracts, and the
quantifier-completeness lint (7 ACs: AC-C1..AC-C5, AC-P1, AC-P2).

Spec: engine_py/conformance/CONTRACTS_SPEC.md. `engine_py/conformance/`
exists on this base only as that spec document — no `__init__.py`, so
`import conformance` alone already resolves as an empty **namespace**
package today (`[G22:3]`) and forces nothing. Every AC-C*/AC-P1 test
therefore imports a real submodule (`conformance.report`/`.tokens`/
`.quant_lint`) or reads a packaging artifact directly, and fails today —
either at `ModuleNotFoundError` (no such submodule exists) or at a real
assertion once the module resolves. AC-P2 is the spec's one declared
pre-passing shield (§0.6): it asserts an absence that cannot yet be
violated.

Deferred-import discipline (§1q): every `conformance.*` symbol is imported
inside the test body, never at module level, so collection does not break.

v2: module paths and exported names are now pinned by spec §1.5, and the
AC-C5 fixture-document grammar (LEVEL/NON-UNIFORMITY/EXCLUDES/SEAM/
ATTRIBUTE-PATH/BINDING-TIME/NORMALISATION) by §1.6 — this file asserts
against those names/grammar rather than inventing its own. `Finding`'s
attribute names (`kind`, `subject`) and the three `kind` string values
(`missing_non_uniformity_row`, `missing_reductions`, `seam_not_pinned`) are
now pinned verbatim by spec v4 §1.5, and every AC-C5 test asserts against
exactly those names/values.

v4 (gate round 1, `[G22:5]`/`[G22:6]`/`[G22:7]`): AC-C1's recorded seam set
now covers all five prohibited acts plus directory-scan (14 seams, not 3);
AC-C5's check-2 fixture now covers both the "no EXCLUDES" and "EXCLUDES
present but short" (against the new `ADMITS` marker) sub-cases with a
conformant control row alongside them in one document; check-3 is now three
fixtures, each omitting exactly one of the three property lines, each
alongside a conformant seam; plus case-insensitivity, empty-input/
trailing-bare-line, and idempotence coverage for properties the spec pins
but round 1 left unasserted.

v5 (gate round 2, `[G22:8]`/`[G22:9]`/`[G22:10]`/`[G22:11]`): §0.8 replaced
prose with three checkable rules — both orderings always, every optional
element's default path is its own fixture, enumerations exhausted at the
API level. AC-C1's seam set is now the spec-pinned 27-name list (not a
reasoned-about 14), filtered by `threading.get_ident()`. AC-C5's check-2 now
has two fixtures — `ADMITS`-absent (default) and explicit-`ADMITS`
present-but-short, kept separate per rule 2 — and the three check-3 property
fixtures now mix which position (first/last, or a third trailing conformant
seam) the offending seam occupies, per rule 1. The idempotence test now
calls a finding-producing document twice and compares the two results,
rather than comparing an always-empty control to itself.

v6 (gate round 3, `[G22:13]`/`[G22:14]`/`[G22:15]`): `[G22:13]` makes
per-check candidate-implementation simulation the primary obligation, not
fixture-derivation from requirement text — see the RED report for the
simulation table. `[G22:14]`: check-2's default-path fixture now omits each
of the four reductions (any/all/first/last) individually, not just `all`.
`[G22:15]`: a new check-1 fixture forces the row→level binding itself —
document-global presence and substring operand matching both survived
every prior round. The idempotence test now snapshots `first` and asserts
`second is not first`, since `second == first` alone is a tautology against
a GREEN that returns the same accumulator object both calls. One check-3
fixture's offender is now a mixed-case name, since an all-lowercase offender
made both the positive assertion and the `Path.read_text` negative guard
casing-insensitive; the same self-sweep applied to check 1 (`Audit_Field`
in `_MISSING_ROW_SPEC`, which was also all-lowercase-only until now). Also
added, proactively (not required this round): an `ADMITS`-binds-to-wrong-
neighbour test, on `[G22:13]`'s standing candidate list. NOTE: spec §3's `[G22:11]` clause still describes check 2's *round-2*
shape ("offenders are 1st and 2nd with the conformant row 3rd") as "the
model to copy" — that text is stale against the round-4 rebuild (5 rows, no
conformant row at all); flagged to the team lead rather than corrected here,
since it is spec prose, not this file.

v7 (gate round 4, `[G22:16]`): spec now pins the two semantics round 6's
simulation flagged as gaps instead of guessing — LEVEL/row/SEAM operands are
matched case-sensitively and verbatim (unlike the case-insensitive marker
prefixes), and seam property lines bind to the nearest preceding `SEAM`
(same rule as `ADMITS`→`LEVEL`). Four new fixtures: `_LEVEL_CASE_MISMATCH_SPEC`
and `_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC` assert the two newly-pinned
semantics directly; `_CHECK2_WRONG_LEVEL_BINDING_SPEC` and
`_SEAM_NAME_SUBSTRING_SPEC` close the two gaps flagged (not invented) in the
round-7 report.

Round 9: `_SEAM_NAME_CASE_MISMATCH_SPEC` closes the further gap flagged in
the round-9 report — SEAM-name case-sensitivity specifically (as opposed to
LEVEL/row-operand case-sensitivity, which `_LEVEL_CASE_MISMATCH_SPEC`
covers), which `_SEAM_NAME_SUBSTRING_SPEC` does not discharge since its two
seam names differ in more than case.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_ENGINE_ROOT = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────
# AC-C1 — package import has no import-time side effects
# ─────────────────────────────────────────────────────────────────────────


class TestConformancePackageImport:
    def test_ac_c1_import_has_no_side_effects(self, monkeypatch):
        """AC-C1. Kills an implementation that does eager work at import time
        (e.g. reading a fixture file, resolving a version via
        importlib.metadata, spawning a subprocess to discover a git root, or
        scanning a directory to build a registry).

        `[G22:3]`: forcing on `import conformance` alone is vacuous — a
        directory with no `__init__.py` is a namespace package, so the bare
        top-level import already succeeds today without touching any real
        code. This test instead imports the real submodules pinned by §1.5
        (`conformance.report`, `conformance.tokens`, `conformance.quant_lint`),
        which do not exist yet and raise `ModuleNotFoundError` today, and
        additionally asserts `conformance.__file__ is not None` — §1.5 pins
        `conformance/__init__.py`, so a namespace package is not the artifact
        this lot ships, independent of any RED-forcing concern.

        `[G22:10]` coverage: AC-C1's normative form is "no work at all";
        the recorded set below is the spec-pinned **27-name list** (§2
        `[G22:10]`), exhausted at the API level rather than reasoned about
        per-act — round 2's 14-seam, five-act reasoning still left
        `os.scandir`/`os.walk` (resolves `scandir` as an `os` module global,
        so a one-line `os.walk`-based registry escapes `os.listdir`/
        `Path.iterdir`/`Path.glob` entirely), `subprocess.Popen`/`os.system`/
        `os.popen` (coverage from `subprocess.run` alone does not flow to the
        API it wraps), and `importlib.metadata.distribution`, uncovered.

        Mechanical notes required by the spec: `socket.socket` and
        `subprocess.Popen` are classes, so replacing them with a plain
        function wrapper breaks `isinstance`/subclassing for the duration of
        the `with` block — acceptable only because that window contains
        nothing but imports, never constructed instances the rest of the
        suite depends on. The recorder also filters by
        `threading.get_ident()`, since `calls` is a closure-local list a
        concurrent thread could still write into; only calls from this
        test's own thread are attributed to the import.

        Record-and-delegate, not raise-and-sabotage: each seam is wrapped to
        record the call and then delegate to the real implementation, so
        pytest's own machinery (which also calls open/read_text/run) keeps
        working even if the import trips a seam. The assertion is on the
        recorded evidence, not on whether the interpreter survived. The
        patches are scoped to a `monkeypatch.context()` covering only the
        `sys.modules` purge and the import statements, so they are undone
        before this test's own teardown or any later test runs — no global
        state survives this test either way.
        """
        import builtins
        import importlib.metadata
        import io
        import os
        import socket
        import subprocess
        import tempfile
        import threading
        import urllib.request

        calls: list[tuple[str, tuple, dict]] = []
        _main_tid = threading.get_ident()

        def _record(seam_name, real):
            def _wrapped(*args, **kwargs):
                if threading.get_ident() == _main_tid:
                    calls.append((seam_name, args, kwargs))
                return real(*args, **kwargs)
            return _wrapped

        # (owner, attribute, recorded seam label) — the spec-pinned 27-name
        # list verbatim (§2 `[G22:10]`), exhausted at the API level: every
        # callable that performs a prohibited act, not one representative
        # per act. `socket.socket` and `subprocess.Popen` are classes — the
        # function wrapper below breaks isinstance/subclassing on them for
        # the duration of the `with` block, acceptable only because that
        # window holds nothing but the purge + import statements.
        seams = [
            (builtins, "open", "builtins.open"),
            (io, "open", "io.open"),
            (os, "open", "os.open"),
            (Path, "open", "Path.open"),
            (Path, "read_text", "Path.read_text"),
            (Path, "write_text", "Path.write_text"),
            (os, "listdir", "os.listdir"),
            (os, "scandir", "os.scandir"),
            (os, "walk", "os.walk"),
            (Path, "iterdir", "Path.iterdir"),
            (Path, "glob", "Path.glob"),
            (Path, "rglob", "Path.rglob"),
            (os, "mkdir", "os.mkdir"),
            (os, "makedirs", "os.makedirs"),
            (Path, "mkdir", "Path.mkdir"),
            (tempfile, "mkdtemp", "tempfile.mkdtemp"),
            (tempfile, "NamedTemporaryFile", "tempfile.NamedTemporaryFile"),
            (subprocess, "run", "subprocess.run"),
            (subprocess, "Popen", "subprocess.Popen"),
            (subprocess, "check_output", "subprocess.check_output"),
            (os, "system", "os.system"),
            (os, "popen", "os.popen"),
            (socket, "socket", "socket.socket"),
            (socket, "create_connection", "socket.create_connection"),
            (urllib.request, "urlopen", "urllib.request.urlopen"),
            (importlib.metadata, "version", "importlib.metadata.version"),
            (importlib.metadata, "distribution", "importlib.metadata.distribution"),
        ]

        with monkeypatch.context() as m:
            for owner, attr, seam_name in seams:
                real = getattr(owner, attr)
                m.setattr(owner, attr, _record(seam_name, real))

            for name in list(sys.modules):
                if name == "conformance" or name.startswith("conformance."):
                    del sys.modules[name]

            import conformance  # noqa: F401
            import conformance.quant_lint  # noqa: F401
            import conformance.report  # noqa: F401
            import conformance.tokens  # noqa: F401

        assert conformance.__file__ is not None, (
            "conformance resolved as a namespace package (no __init__.py) — "
            "§1.5 pins conformance/__init__.py as a real module"
        )
        assert calls == [], (
            "conformance import touched a recorded I/O seam: "
            + ", ".join(f"{seam}(args={args!r})" for seam, args, _kwargs in calls)
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-C2 — L0Report frozen dataclass carrier
# ─────────────────────────────────────────────────────────────────────────


class TestL0Report:
    def test_ac_c2_l0report_constructible_with_four_fields_and_frozen(self):
        """AC-C2. Kills an implementation using a mutable dataclass, a plain
        dict, or a NamedTuple in place of a frozen dataclass, and one that
        omits any of the four required fields (passed/requirements/
        violations/labels). Field *values* are deliberately not asserted —
        only structure and immutability, per the spec's L2-owns-the-carrier
        scope limit.
        """
        import dataclasses

        from conformance.report import L0Report

        report = L0Report(
            passed=True,
            requirements=(),
            violations=(),
            labels=(),
        )

        assert dataclasses.is_dataclass(report)
        field_names = {f.name for f in dataclasses.fields(report)}
        assert {"passed", "requirements", "violations", "labels"} <= field_names

        with pytest.raises(dataclasses.FrozenInstanceError):
            report.passed = False


# ─────────────────────────────────────────────────────────────────────────
# AC-C3 — token vocabulary, single source of truth, by value + distinct
# ─────────────────────────────────────────────────────────────────────────


class TestTokenVocabulary:
    def test_ac_c3_token_values_and_distinctness(self):
        """AC-C3. Kills an implementation that mis-spells a verdict token
        (per-field, §0.5 — no truthiness substitute), and one that unifies
        the hyphenated requirement verdicts with the underscored adversary
        status (e.g. spelling both "not-checked" the same as "not_executed",
        or vice versa) — bd#7's [G2:9] deliberately keeps them apart, and a
        lot that collapsed them would silently break every consumer.
        """
        from conformance.tokens import (
            ADVERSARY_NOT_EXECUTED,
            REQUIREMENT_FAILED,
            REQUIREMENT_NOT_CHECKED,
            REQUIREMENT_PASSED,
        )

        assert REQUIREMENT_PASSED == "passed"
        assert REQUIREMENT_FAILED == "failed"
        assert REQUIREMENT_NOT_CHECKED == "not-checked"
        assert ADVERSARY_NOT_EXECUTED == "not_executed"

        values = [
            REQUIREMENT_PASSED,
            REQUIREMENT_FAILED,
            REQUIREMENT_NOT_CHECKED,
            ADVERSARY_NOT_EXECUTED,
        ]
        assert len(values) == len(set(values))


# ─────────────────────────────────────────────────────────────────────────
# AC-C4 — no new third-party dependency, asserted against declared deps
# ─────────────────────────────────────────────────────────────────────────


class TestNoNewDependency:
    def test_ac_c4_no_new_declared_dependency_for_conformance(self):
        """AC-C4. Kills an implementation that adds a new pyproject
        dependency (e.g. a YAML/TOML parsing library, or a schema-validation
        package) to support the conformance package or its lint. Asserted
        against pyproject.toml's *declared* dependency lists — not an import
        scan, which cannot tell stdlib from vendored (per the spec's
        explicit rejection of that mechanism). `[G22:3]`: a bare
        `import conformance` is vacuous (namespace package, already
        resolves) — this imports the real `conformance.quant_lint`
        submodule instead, which forces `ModuleNotFoundError` today; after
        GREEN, that submodule must exist AND the declared dependency set
        must be unchanged from this baseline.
        """
        import conformance.quant_lint  # noqa: F401 — forces failure today

        pyproject_path = _ENGINE_ROOT / "pyproject.toml"
        text = pyproject_path.read_text()

        dep_match = re.search(r"(?m)^dependencies\s*=\s*\[(.*?)\]", text, re.S)
        core_deps = re.findall(r'"([^"]+)"', dep_match.group(1))

        opt_section_match = re.search(
            r"\[project\.optional-dependencies\](.*?)(?:\n\[|\Z)", text, re.S
        )
        opt_deps = re.findall(r'"([^"]+)"', opt_section_match.group(1))

        declared = set(core_deps) | set(opt_deps)

        baseline = {
            "typing_extensions>=4",
            "dbos==2.27.0",
            "pytest>=8",
            "pytest-timeout>=2.4.0",
            "pyyaml>=6",
            "pydantic-ai>=2.0,<3",
            "semgrep>=1.60",
        }
        assert declared == baseline


# ─────────────────────────────────────────────────────────────────────────
# AC-C5 — quantifier-completeness lint (three independent checks)
# ─────────────────────────────────────────────────────────────────────────

# §1.6 fixture-document grammar, verbatim: LEVEL / NON-UNIFORMITY / EXCLUDES /
# ADMITS / SEAM / ATTRIBUTE-PATH / BINDING-TIME / NORMALISATION, recognised
# at line start, case-insensitive. `ADMITS` is optional on a LEVEL; absent
# means all four reductions (any/all/first/last). This is the lint's input
# format, not this lot's own spec-document format (§1.6) — these fixtures
# are never run over CONTRACTS_SPEC.md itself.

# Three levels, one per direction named in §0.1 (container, element kind,
# payload field), all missing a non-uniformity row — a lint walking only
# two of the three directions (bd#7's round-9 gap: containers and element
# kinds, but not payload fields) still fails this fixture. `Audit_Field`'s
# name is deliberately mixed-case (proactive, `[G22:13]`'s "case-folding
# where the spec pins verbatim text" candidate) — the other two level names
# here are already lowercase, so a GREEN that case-folds level names before
# comparison would still match the expected literal for them; only the
# mixed-case one forces `Finding.subject` to preserve the document's
# verbatim casing (§1.5) rather than a normalised form of it.
_MISSING_ROW_SPEC = """\
# Fixture Spec — missing non-uniformity rows, all three directions

LEVEL: phases

LEVEL: step_events

LEVEL: Audit_Field

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:5]`/`[G22:7]`/`[G22:9]`/`[G22:14]` check-2 default-ADMITS fixture:
# FIVE rows, none carrying an ADMITS line, so every row is checked against
# the ADMITS-absent DEFAULT (all four) — the fix for round 2's mistake
# (explicit ADMITS, never exercising the default at all). `phases` has no
# EXCLUDES line at all. The other four each omit exactly ONE reduction from
# the default set — `step_events` omits `all`, `retry_window` omits `any`,
# `audit_gate` omits `first`, `commit_step` omits `last` — so the default
# set's MEMBERSHIP is load-bearing member by member (`[G22:14]`): round 3
# only ever omitted `all` (or `first`+`last` together on the explicit path),
# so a GREEN whose default set silently drops `any` (or `first`, or `last`)
# passed everything. All five rows are non-conformant, so this fixture needs
# no conformant row to be ordering-safe (§0.8 rule 1) — a first-only or
# last-only lint still misses at least one of the five and is caught. A
# third row is in the *separate* `_CHECK2_EXPLICIT_ADMITS_SPEC` below, per
# rule 2 (`[G22:8]`): present and default are two fixtures, not one.
_CHECK2_SPEC = """\
# Fixture Spec — check 2: no-EXCLUDES-at-all, and each of the four default
# reductions individually omitted, in the same document

LEVEL: phases
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control

LEVEL: step_events
NON-UNIFORMITY: step_events — fixture set is non-uniform in both orderings
EXCLUDES: any, first, last

LEVEL: Retry_Window
NON-UNIFORMITY: Retry_Window — fixture set has >=2 members, one violating, plus control
EXCLUDES: all, first, last

LEVEL: audit_gate
NON-UNIFORMITY: audit_gate — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, last

LEVEL: commit_step
NON-UNIFORMITY: commit_step — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:9]` check-2 EXPLICIT-ADMITS fixture, kept separate from the
# default-path fixture above per rule 2. TWO offending rows (`audit_step`,
# `retry_budget`) bracket ONE conformant row (`payload_field`) in the
# middle — mirroring `_CHECK2_SPEC`'s "both offenders individually asserted"
# shape rather than a single-offender/single-conformant pair, which a
# first-only or last-only evaluator could pass vacuously by landing on
# whichever member happens to sit at the position it checks (found during
# the `§0.8` self-sweep on this round; not present in the gate's own
# findings). `payload_field` explicitly ADMITS only `any, all` (an unordered
# collection, where `first`/`last` are meaningless) and its row fully covers
# that admitted set. `audit_step` and `retry_budget` both explicitly ADMIT
# all four and both EXCLUDES only two (present-but-short against an
# EXPLICIT admitted set, not the default).
_CHECK2_EXPLICIT_ADMITS_SPEC = """\
# Fixture Spec — check 2: explicit ADMITS, two offenders bracketing a
# conformant row

LEVEL: audit_step
ADMITS: any, all, first, last
NON-UNIFORMITY: audit_step — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: payload_field
ADMITS: any, all
NON-UNIFORMITY: payload_field — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: retry_budget
ADMITS: any, all, first, last
NON-UNIFORMITY: retry_budget — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# Check-2 row-to-level binding fixture (flagged as a gap in the round-7
# report, closed here): a present-but-short row bound to the WRONG level —
# every prior check-2 fixture had its row immediately follow its own level,
# so "incidental coverage" of the binding via check 1's fixture was never
# verified for check 2 specifically (`[G22:10]`'s own rule: incidental
# coverage is not coverage). The row here is textually adjacent to
# `beta_gate` but its `<level>` operand names `alpha_gate` — a lint binding
# by PROXIMITY (nearest preceding LEVEL) instead of reading the row's own
# operand would misattribute it to `beta_gate`, wrongly reporting `beta_gate`
# as present-but-short and `alpha_gate` as missing a row entirely (check 1),
# rather than the reverse — exactly what correct (operand-text) binding
# produces.
_CHECK2_WRONG_LEVEL_BINDING_SPEC = """\
# Fixture Spec — check 2: present-but-short row adjacent to one level,
# naming another

LEVEL: alpha_gate

LEVEL: beta_gate
NON-UNIFORMITY: alpha_gate — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:15]` check-1 row-to-level BINDING fixture: kills two GREENs that
# survived all 19 tests through round 3 — document-global presence (any
# `NON-UNIFORMITY:` line anywhere ⇒ nothing missing) and substring rather
# than exact operand matching (`LEVEL: audit` discharged by a row naming
# `audit_field`). `phases` and `audit` each have NO row of their own, but
# the document DOES contain rows — for `workers` and `audit_field`
# respectively — so a document-global-presence lint would wrongly conclude
# nothing is missing, and a substring-matching lint would wrongly credit
# `audit_field`'s row to `audit`. Both non-adjacent offenders (`phases` at
# position 1, `audit` at position 3 of 4) are bracketed by conformant levels
# (`workers`, `audit_field`), so this fixture is also self-sufficient against
# first-only/last-only evaluators without needing a sibling fixture.
_ROW_LEVEL_BINDING_SPEC = """\
# Fixture Spec — check 1: row-to-level binding (wrong level named, and a
# level name that is a substring of another's)

LEVEL: phases

LEVEL: workers
NON-UNIFORMITY: workers — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: audit

LEVEL: audit_field
NON-UNIFORMITY: audit_field — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:16]` case-sensitive operand matching, pinned in spec v7: `LEVEL:
# Audit_Case` is NOT discharged by `NON-UNIFORMITY: audit_case — …`, since
# `Finding.subject` is verbatim and a case-folding lint cannot both fold to
# match and report verbatim. `Audit_Case` (mixed-case level) and
# `audit_case` (all-lowercase row operand) differ ONLY in casing — a lint
# that case-folds operands before comparing would wrongly see the row as
# discharging the level; the level must still be flagged.
_LEVEL_CASE_MISMATCH_SPEC = """\
# Fixture Spec — level and row differ only in casing

LEVEL: Audit_Case
NON-UNIFORMITY: audit_case — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""

# Bonus coverage (proactive, not required by the gate this round, but on
# `[G22:13]`'s standing candidate list — "operand bound to the wrong
# neighbour"): `ADMITS` binds to the NEAREST PRECEDING `LEVEL` (§1.6),
# never a following one. `alpha_level` explicitly `ADMITS: any, all` and its
# row covers exactly that (conformant *only* if its own `ADMITS` applies to
# it, not to `beta_level` after it). `beta_level` has NO `ADMITS` of its own
# (default: all four) and its row covers only `any, all` — conformant *only*
# if it is checked against the default, not against `alpha_level`'s `ADMITS`
# migrating forward onto it. A wrong-neighbour-binding lint gets BOTH
# levels backwards: it flags `alpha_level` (checks it against the default
# instead of its own `ADMITS`) and clears `beta_level` (checks it against
# `alpha_level`'s `ADMITS` instead of the default) — the opposite of a
# correct, preceding-binding lint.
_ADMITS_WRONG_NEIGHBOUR_SPEC = """\
# Fixture Spec — ADMITS binds to the nearest preceding LEVEL, not following

LEVEL: alpha_level
ADMITS: any, all
NON-UNIFORMITY: alpha_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: beta_level
NON-UNIFORMITY: beta_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""

# `[G22:7]`/`[G22:11]` check-3 fixtures: THREE documents, each with TWO (or
# three) seams — one fully conformant, one missing exactly one of the three
# property lines — so a lint testing presence of only one property line
# cannot pass all three, and a lint reporting only the first or only the
# last SEAM block cannot pass all three either. The offending seam sits
# FIRST in the ATTRIBUTE-PATH and NORMALISATION fixtures (the latter also
# carries a third, trailing conformant seam), and LAST in the BINDING-TIME
# fixture — both orderings represented across the set (`[G22:8]` rule 1;
# round 2 put the offender last in all three, so a last-block-only lint
# passed everything).
_SEAM_MISSING_ATTRIBUTE_PATH_SPEC = """\
# Fixture Spec — check 3: one seam missing ATTRIBUTE-PATH only (offender first)

SEAM: importlib.metadata.version
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# Offender name is deliberately MIXED-CASE (`MixedCase.Attribute`, not the
# usual all-lowercase `importlib.metadata.version`) — the other two check-3
# fixtures' offender happens to be all-lowercase already, so a GREEN that
# case-folds every line before parsing (or that stores `subject` upper/
# lower-cased) would still match the expected literal there, and the
# negative guards against `Path.read_text` were vacuous for the same
# reason (a case-folding GREEN can never produce that exact mixed-case
# string). This fixture forces `Finding.subject` to preserve the document's
# verbatim casing (§1.5), not a normalised form of it.
_SEAM_MISSING_BINDING_TIME_SPEC = """\
# Fixture Spec — check 3: one seam missing BINDING-TIME only (offender
# last, mixed-case name)

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: MixedCase.Attribute
ATTRIBUTE-PATH: MixedCase.Attribute
NORMALISATION: none
"""

_SEAM_MISSING_NORMALISATION_SPEC = """\
# Fixture Spec — check 3: one seam missing NORMALISATION only (offender
# first, plus a third conformant seam trailing it)

SEAM: importlib.metadata.version
ATTRIBUTE-PATH: importlib.metadata.version
BINDING-TIME: call-time

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: subprocess.run
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:16]` property-line-to-seam wrong-neighbour binding, pinned in spec
# v7 (same rule as `ADMITS`→`LEVEL`, now stated for `SEAM` too): property
# lines bind to the NEAREST PRECEDING `SEAM`, never a following one.
# `Seam.Alpha`'s three property lines sit directly above `Seam.Beta`'s
# declaration; `Seam.Beta` supplies only two lines of its own
# (ATTRIBUTE-PATH, BINDING-TIME — missing NORMALISATION under correct
# binding). A following-binding lint attributes `Seam.Alpha`'s block
# forward to `Seam.Beta` (the nearest SEAM *after* those lines), so
# `Seam.Beta` looks fully pinned (NORMALISATION "borrowed" from above) and
# `Seam.Alpha` looks entirely unpinned (its own lines never attributed back
# to it, and nothing follows it to inherit from) — the reverse of correct,
# preceding-binding output.
_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC = """\
# Fixture Spec — property lines bind to the nearest preceding SEAM, not
# following

SEAM: Seam.Alpha
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Seam.Beta
ATTRIBUTE-PATH: Seam.Beta
BINDING-TIME: call-time
"""

# Check-3 seam-name substring confusion (flagged as a gap in the round-7
# report, closed here) — the check-3 analogue of check 1's `audit`/
# `audit_field`. `Path.read` is a substring/prefix of `Path.read_text`
# and is fully conformant; `Path.read_text` is the longer name and is
# offending (missing NORMALISATION). A substring-matching lint might credit
# `Path.read_text`'s missing property from `Path.read`'s complete block,
# since `Path.read` textually matches as a substring of `Path.read_text`.
_SEAM_NAME_SUBSTRING_SPEC = """\
# Fixture Spec — one seam name is a substring of another's

SEAM: Path.read
ATTRIBUTE-PATH: pathlib.Path.read
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
"""

# `[G22:16]` seam-name case-sensitivity, distinct from the substring fixture
# above (whose two names differ in more than case) — flagged as a live-but-
# untested candidate in the round-9 report, closed here rather than left
# for a later round. `path.read_text` (lowercase) and `Path.Read_Text`
# (mixed-case) are the SAME text except for casing — pinned by `[G22:16]`
# as two DISTINCT seams, not one. `path.read_text` is fully conformant;
# `Path.Read_Text` is missing NORMALISATION. A case-folding lint that
# conflates the two (treating them as one seam) would see the union of
# both blocks' properties as complete — ATTRIBUTE-PATH and BINDING-TIME
# from `Path.Read_Text`'s own lines, NORMALISATION borrowed from
# `path.read_text`'s block — and wrongly clear `Path.Read_Text`.
_SEAM_NAME_CASE_MISMATCH_SPEC = """\
# Fixture Spec — two seam names differ only in casing

SEAM: path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Path.Read_Text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
"""

# `[G22:9]` bonus coverage (not required by the gate, flagged as such in the
# report): an ADMITS token outside the four reductions is itself pinned as a
# `missing_reductions` finding (§1.6), and ADMITS binds to the *nearest
# preceding* LEVEL.
_ADMITS_INVALID_TOKEN_SPEC = """\
# Fixture Spec — ADMITS names a token outside any/all/first/last

LEVEL: phases
ADMITS: any, bogus, all, first, last
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""

# Conformant control: every level's row covers exactly the reductions its
# level ADMITS (default four, since neither level overrides ADMITS) — the
# `[G22:5]` fix. A round-1 control named only three reductions for
# `step_events` while requiring zero findings, which false-failed any GREEN
# reading check 2 the only computable way ("cover all four").
# `step_events`'s EXCLUDES tokens and the seam's property lines are each in
# a deliberately NON-canonical order (proactive, `[G22:13]`'s "fixed
# required order for unordered properties" candidate) — every other fixture
# in this file lists EXCLUDES tokens and property lines in the same
# canonical order every time, so a GREEN requiring that exact order (instead
# of treating both as unordered sets) would pass every other fixture and
# only be caught here, where this control's own emptiness assertion
# (`findings == []`) would fail for it.
_CONFORMANT_SPEC = """\
# Fixture Spec — fully conformant control

LEVEL: phases
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: step_events
NON-UNIFORMITY: step_events — fixture set is non-uniform in both orderings
EXCLUDES: last, any, first, all

SEAM: Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
ATTRIBUTE-PATH: pathlib.Path.read_text
"""

# Free-form prose with none of the §1.6 markers at all — exercises the
# spec's explicit "MUST NOT raise" contract point (§1.5) rather than the
# three checks, which the other fixtures already exercise.
_MALFORMED_SPEC = """\
This is not a spec-fixture document at all. It is free-form prose with no
LEVEL, NON-UNIFORMITY, EXCLUDES or SEAM markers anywhere in it, and a stray
mention of "excludes" and "level" in ordinary sentences, which are not
markers because they are not at line start in the pinned form.
"""

# §1.6 markers spelled lowercase — exercises the "recognised... case
# insensitive" clause directly. Kills a case-sensitive GREEN that only
# matches uppercase LEVEL:/SEAM:/etc, which would see this whole document as
# unstructured prose (no findings at all, and no seam recognised either).
_LOWERCASE_MARKERS_SPEC = """\
# Fixture Spec — grammar markers spelled lowercase (§1.6 case-insensitivity)

level: phases

seam: Path.read_text
attribute-path: pathlib.Path.read_text
binding-time: call-time
normalisation: none
"""


class TestQuantifierCompletenessLint:
    def test_ac_c5_flags_missing_non_uniformity_row_both_directions(self):
        """AC-C5(1). Kills a lint that only walks collection levels in one or
        two of the three directions §0.1 names (containers, element kinds,
        payload fields) — e.g. containers-and-elements only, the exact shape
        of bd#7's rounds 4-8 (which climbed the ladder upward) and round 9
        (which found a rung *below* it, at the payload-field/merged-set
        direction). `phases`, `step_events` and `Audit_Field` — one per
        direction — are all missing a non-uniformity row entirely and must
        all three be flagged independently — kills a lint that reports only
        one or two of the three. `Audit_Field`'s mixed-case name (self-sweep
        addition) also forces `Finding.subject` to preserve verbatim casing,
        not a case-folded form — see the module docstring's v6 note.

        `[G22:4]` coverage: the last two assertions cover `Finding`'s pinned
        type contract (§1.5) using a `Finding` already in hand from this
        fixture's result — a frozen dataclass. Kills a plain object,
        `NamedTuple`, or dict masquerading via `.kind`/`.subject` attribute
        access (not a dataclass), and a mutable dataclass (assignment does
        not raise).
        """
        import dataclasses

        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_MISSING_ROW_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "step_events"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "Audit_Field"
            for f in findings
        )

        finding = findings[0]
        assert dataclasses.is_dataclass(finding)
        with pytest.raises(dataclasses.FrozenInstanceError):
            finding.kind = "mutated"

    def test_ac_c5_check1_row_binds_to_exact_named_level_not_globally_or_by_substring(self):
        """AC-C5(1). `[G22:15]`: kills two GREENs that survived every prior
        round — document-global presence (any `NON-UNIFORMITY:` line
        anywhere in the document ⇒ nothing missing, so `phases` is wrongly
        cleared by `workers`'s row) and substring operand matching (`audit`
        wrongly discharged by `audit_field`'s row, since "audit" is a
        substring of "audit_field"). `phases` and `audit` each have no row of
        their own and must still be flagged, even though the document
        contains rows for other levels and one of those other levels'
        names contains the un-named level's name as a substring.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_LEVEL_BINDING_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "audit"
            for f in findings
        )
        assert not any(f.subject == "workers" for f in findings)
        assert not any(f.subject == "audit_field" for f in findings)

    def test_ac_c5_level_operand_matched_case_sensitively(self):
        """AC-C5(1). `[G22:16]`: `LEVEL: Audit_Case` and
        `NON-UNIFORMITY: audit_case — …` differ only in casing — pinned in
        spec v7 as NOT a match (`Finding.subject` is verbatim from the
        document, and a lint that case-folds operands to match them cannot
        also report verbatim). Kills a lint that lower-cases (or otherwise
        folds) operands before comparing `LEVEL` names against row
        `<level>` operands; `Audit_Case` must still be flagged as missing
        its own row.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_LEVEL_CASE_MISMATCH_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "Audit_Case"
            for f in findings
        )

    def test_ac_c5_admits_binds_to_preceding_level_not_following(self):
        """AC-C5(2), bonus coverage (proactive, `[G22:13]`'s standing
        candidate list — "operand bound to the wrong neighbour"; not
        explicitly required this round). `alpha_level` explicitly `ADMITS:
        any, all` and its row covers exactly that set — conformant only
        under correct (preceding) binding; a wrong-neighbour lint would
        check it against the default instead and wrongly flag it.
        `beta_level` has no `ADMITS` of its own (default: all four) and its
        row covers only `any, all` — non-conformant only under correct
        binding; a wrong-neighbour lint would misattribute `alpha_level`'s
        `ADMITS` onto it and wrongly clear it. A wrong-neighbour-binding
        lint gets both backwards simultaneously.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ADMITS_WRONG_NEIGHBOUR_SPEC)

        assert not any(f.subject == "alpha_level" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "beta_level"
            for f in findings
        )

    def test_ac_c5_check2_row_binds_by_operand_not_proximity(self):
        """AC-C5(2). Flagged as a gap in the round-7 report, closed here:
        a present-but-short row textually adjacent to `beta_gate` but whose
        `<level>` operand names `alpha_gate`. Correct (operand-text)
        binding: `alpha_gate` gets the present-but-short row (missing
        `last`); `beta_gate` has no row of its own at all (check 1). A
        proximity-binding lint (attributing the row to the nearest
        preceding `LEVEL` instead of reading its operand) would get this
        backwards or miss it, since it was never forced by any prior check-2
        fixture (all of which had rows immediately following their own
        level).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CHECK2_WRONG_LEVEL_BINDING_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "alpha_gate"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "beta_gate"
            for f in findings
        )
        assert not any(
            f.kind == "missing_reductions" and f.subject == "beta_gate"
            for f in findings
        )
        assert not any(
            f.kind == "missing_non_uniformity_row" and f.subject == "alpha_gate"
            for f in findings
        )

    def test_ac_c5_flags_row_not_enumerating_reductions(self):
        """AC-C5(2). `[G22:5]`/`[G22:7]`/`[G22:9]`/`[G22:14]`: kills a lint
        implementing only the "no EXCLUDES line at all" half of check 2 —
        `phases` has no EXCLUDES line at all. The other four rows are each
        present-but-short against the ADMITS-absent **default** set (none of
        the five rows in this fixture carries an ADMITS line, so this
        specifically exercises the default path, not an explicit override),
        and each omits exactly ONE of the four reductions — `step_events`
        omits `all`, `retry_window` omits `any`, `audit_gate` omits `first`,
        `commit_step` omits `last` — so the default set's membership is
        load-bearing member by member (`[G22:14]`: round 3 only ever
        omitted `all`, so a GREEN whose default set silently dropped `any`,
        `first`, or `last` individually passed everything). Also kills a
        lint reporting only the first offending row (five rows here, all
        non-conformant) and one that only checks coverage when an explicit
        `ADMITS:` line is present. Also confirms check (1) does not
        spuriously fire, since every row here is present.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CHECK2_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "phases"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "step_events"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "Retry_Window"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "audit_gate"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "commit_step"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_flags_row_not_enumerating_reductions_explicit_admits(self):
        """AC-C5(2). `[G22:8]` rule 2: the explicit-`ADMITS` present-but-short
        case, kept as its own fixture separate from the default-path test
        above. `payload_field` explicitly `ADMITS` only `any, all` (an
        unordered collection — `first`/`last` are meaningless for it) and its
        row covers that admitted set exactly (conformant, must NOT be
        flagged) — kills a lint that ignores an `ADMITS` override and demands
        all four regardless. `audit_step` and `retry_budget` both explicitly
        `ADMITS` all four and both cover only two (present-but-short against
        an explicit admitted set, not the default) — kills a lint that only
        checks coverage against the default, and (self-sweep finding, this
        round) `audit_step`/`retry_budget` bracket the conformant
        `payload_field` first-and-last, so a lint evaluating only the first
        or only the last row cannot pass — it would catch one offender and
        silently miss the other.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CHECK2_EXPLICIT_ADMITS_SPEC)

        assert not any(f.subject == "payload_field" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "audit_step"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "retry_budget"
            for f in findings
        )

    def test_ac_c5_flags_admits_token_outside_the_four_reductions(self):
        """AC-C5(2), bonus coverage flagged in the RED report (not explicitly
        requested by the gate, but now-pinned spec text §1.6): an `ADMITS`
        token outside `any`/`all`/`first`/`last` is itself a `missing_reductions`
        finding, and `ADMITS` binds to the nearest preceding `LEVEL` — kills a
        lint that silently ignores an unrecognised token instead of flagging
        it, or that mis-attributes an `ADMITS` line to the wrong level.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ADMITS_INVALID_TOKEN_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "phases"
            for f in findings
        )

    def test_ac_c5_flags_seam_missing_attribute_path(self):
        """AC-C5(3). `[G22:7]`/`[G22:11]`: one of three fixtures, each
        omitting exactly one of the three property lines, so each is
        independently load-bearing — a lint testing only ATTRIBUTE-PATH
        presence would pass this fixture's `Path.read_text` row (conformant)
        but must still flag `importlib.metadata.version` (ATTRIBUTE-PATH
        missing here), and must not flag the conformant seam (non-uniform
        within the check: two seams, one conformant, one not). The
        offending seam is placed **first** here (round 2 put it last in all
        three fixtures, so a lint evaluating only the final `SEAM:` block —
        a loop-variable escape — passed everything; this fixture kills that).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_ATTRIBUTE_PATH_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "importlib.metadata.version"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_flags_seam_missing_binding_time(self):
        """AC-C5(3). `[G22:7]`/`[G22:11]`: kills a lint that tests only
        ATTRIBUTE-PATH presence — the offender here pins ATTRIBUTE-PATH
        correctly (as `mkdtemp` and `importlib.metadata.version` both
        historically did, per §0.2) and omits only BINDING-TIME, which such
        a lint would silently pass. The offending seam is placed **last**
        here — the other position from the ATTRIBUTE-PATH fixture above, so
        a lint evaluating only the *first* `SEAM:` block is caught by this
        one instead. The offender's name (`MixedCase.Attribute`) is
        deliberately mixed-case, not the usual all-lowercase
        `importlib.metadata.version` used elsewhere — a GREEN that
        case-folds lines before parsing (or normalises `subject`'s casing)
        fails the exact-match assertion below, where an all-lowercase
        offender would have let it through undetected; the negative guard
        also now discriminates, since a case-folding GREEN could never
        reproduce the mixed-case `Path.read_text` literal either.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_BINDING_TIME_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "MixedCase.Attribute"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_flags_seam_missing_normalisation(self):
        """AC-C5(3). `[G22:7]`/`[G22:11]`: kills a lint that tests only
        ATTRIBUTE-PATH (or ATTRIBUTE-PATH + BINDING-TIME) presence —
        `importlib.metadata.version` here pins both correctly (mirroring
        `Path.read_text`'s historical gap, which pinned the seam and
        BINDING-TIME but not NORMALISATION) and omits only NORMALISATION.
        The offending seam is placed **first**, with a *second* conformant
        seam (`subprocess.run`) trailing it — mirroring check 2's
        offender-then-conformant shape, so neither a first-only nor a
        last-only evaluator passes this fixture on its own.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_NORMALISATION_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "importlib.metadata.version"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)
        assert not any(f.subject == "subprocess.run" for f in findings)

    def test_ac_c5_seam_property_line_binds_to_preceding_seam_not_following(self):
        """AC-C5(3). `[G22:16]`: property lines bind to the NEAREST
        PRECEDING `SEAM`, never a following one (spec v7, same rule as
        `ADMITS`→`LEVEL`). `Seam.Alpha`'s three property lines sit directly
        above `Seam.Beta`'s declaration; `Seam.Beta` supplies only two lines
        of its own. A following-binding lint attributes `Seam.Alpha`'s block
        forward onto `Seam.Beta` (making it look fully pinned) and leaves
        `Seam.Alpha` with nothing (making it look entirely unpinned) — the
        reverse of correct output. Kills that lint on both halves at once.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC)

        assert not any(f.subject == "Seam.Alpha" for f in findings)
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Seam.Beta"
            for f in findings
        )

    def test_ac_c5_flags_seam_name_substring_confusion(self):
        """AC-C5(3). Flagged as a gap in the round-7 report, closed here —
        the check-3 analogue of check 1's `audit`/`audit_field`. `Path.read`
        is a substring/prefix of `Path.read_text` and is fully conformant;
        `Path.read_text` is the longer name and is missing NORMALISATION. A
        substring-matching lint (crediting `Path.read_text`'s properties
        from any seam whose name it contains as a substring) would wrongly
        treat `Path.read_text` as fully pinned via `Path.read`'s complete
        block.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_NAME_SUBSTRING_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Path.read_text"
            for f in findings
        )
        assert not any(f.subject == "Path.read" for f in findings)

    def test_ac_c5_flags_seam_name_case_mismatch_as_distinct_seams(self):
        """AC-C5(3). `[G22:16]`: flagged as a live-but-untested candidate in
        the round-9 report (distinct from `_SEAM_NAME_SUBSTRING_SPEC`, whose
        two names differ in more than case), closed here. `path.read_text`
        and `Path.Read_Text` are the same text except for casing — pinned
        as two DISTINCT seams, not one, for the same reason a level and a
        row differing only in casing are distinct (`Finding.subject` is
        verbatim, and a lint that case-folds to conflate them cannot also
        report each verbatim). `path.read_text` is fully conformant;
        `Path.Read_Text` is missing NORMALISATION. A case-folding lint that
        treats them as one seam sees the union of both blocks' properties as
        complete and wrongly clears `Path.Read_Text`.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_NAME_CASE_MISMATCH_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Path.Read_Text"
            for f in findings
        )
        assert not any(f.subject == "path.read_text" for f in findings)

    def test_ac_c5_conformant_control_passes(self):
        """AC-C5 control. A document with every level's non-uniformity row
        (both directions) fully covering its ADMITS-ed reductions (`[G22:5]`
        — both levels here cover all four, the default admitted set), and a
        seam with its full interception property, must produce zero
        findings. Kills a lint that over-fires on well-formed input (e.g. a
        keyword sweep that mis-scores conformant documents — the failure
        mode §3's scope-limit note calls out from bd#7's own history: 11 of
        13 ACs mis-scored). Also kills a lint requiring EXCLUDES tokens or
        seam property lines in a fixed canonical order — `step_events`'s
        EXCLUDES and the seam's property lines are both listed out of the
        order every other fixture in this file happens to use (proactive,
        self-sweep addition).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CONFORMANT_SPEC)

        assert findings == []

    def test_ac_c5_does_not_raise_on_malformed_document(self):
        """§1.5's explicit contract point: `lint_quantifier_completeness`
        MUST NOT raise on a non-conformant (here: entirely unstructured)
        document — raising would make "conformant" and "malformed"
        indistinguishable to the build step consuming the lint's output.
        Kills an implementation that raises KeyError/IndexError/ValueError
        parsing a document with none of the §1.6 markers instead of
        returning a (here, empty) findings list.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_MALFORMED_SPEC)

        assert findings == []

    def test_ac_c5_does_not_raise_on_empty_input(self):
        """§1.6/gate item 5: kills a GREEN doing an `i+1` lookahead over
        `text.split("\\n")` (or similar) that raises `IndexError` indexing
        into an empty sequence, instead of returning an empty findings list
        (nothing quantified yet). Paired with the trailing-bare-`LEVEL:`
        test below, which covers the `.splitlines()` variant instead — that
        one starts nonempty (`"".splitlines() == []`, so an empty string
        alone cannot force a `.splitlines()`-based lookahead to run at all).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness("")

        assert findings == []

    def test_ac_c5_does_not_raise_on_trailing_bare_level_line(self):
        """§1.6/gate item 5: kills the same `i+1`-lookahead GREEN on a
        document whose *last* line is a bare `LEVEL:` with nothing after it
        — the lookahead has no next line to read. Must still report the
        missing-row finding for that level rather than raising.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness("LEVEL: phases")

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )

    def test_ac_c5_recognises_markers_case_insensitively(self):
        """§1.6: markers are recognised at line start, case-insensitive.
        Kills a case-sensitive GREEN that only matches uppercase
        `LEVEL:`/`SEAM:`/etc — it would see this entire fixture as
        unstructured prose: no missing-row finding for `phases` (from the
        lowercase `level:` line) and no recognition of `seam:` as a seam
        declaration at all.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_LOWERCASE_MARKERS_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_lint_is_idempotent_on_finding_producing_document(self):
        """Gate item 5 (round 4 correction): kills a GREEN that accumulates
        findings into module-level state across calls (e.g. an
        `_ALL_FINDINGS.append(...)` instead of building a fresh list per
        call). The round-2 version called the *conformant* document twice
        and asserted both `[]` (vacuous: a conformant document appends zero
        findings either way, so it only discriminated via cross-test
        pollution). The round-3 fix called a finding-producing document
        twice and compared `second == first` — but a GREEN whose
        accumulator *returns the same list object it appended to* (simpler,
        and likelier, than returning a defensive copy) makes `first` and
        `second` literally the same object: any mutation from the second
        call is visible through `first` too, so `second == first` degenerates
        to `x == x` and cannot fail. Fixed by snapshotting `first` into a
        plain list *before* the second call, and asserting object identity
        directly: an accumulating GREEN either returns the same object twice
        (`second is first` — caught by the identity assertion) or returns a
        growing copy each time (caught by the snapshot-equality assertion).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        first = lint_quantifier_completeness(_MISSING_ROW_SPEC)
        assert first != []
        first_snapshot = list(first)

        second = lint_quantifier_completeness(_MISSING_ROW_SPEC)

        assert second == first_snapshot
        assert second is not first


# ─────────────────────────────────────────────────────────────────────────
# AC-P1 — packaging include gains conformance*
# ─────────────────────────────────────────────────────────────────────────


class TestPackaging:
    def test_ac_p1_packages_find_include_gains_conformance(self):
        """AC-P1. Kills an implementation that adds the conformance package
        files but forgets to add `"conformance*"` to
        `[tool.setuptools.packages.find] include`, silently excluding it
        from the shipped package — the packaging-side twin of the AC-P2
        manifest gap.
        """
        pyproject_path = _ENGINE_ROOT / "pyproject.toml"
        text = pyproject_path.read_text()

        section_match = re.search(
            r"\[tool\.setuptools\.packages\.find\](.*?)(?:\n\[|\Z)", text, re.S
        )
        include_match = re.search(r"include\s*=\s*\[(.*?)\]", section_match.group(1), re.S)
        include_list = re.findall(r'"([^"]+)"', include_match.group(1))

        assert "conformance*" in include_list

    def test_ac_p2_core_manifest_excludes_conformance(self):
        """AC-P2 (declared pre-passing shield, §0.6). `core_manifest.json`
        necessarily excludes `conformance` today because no implementation
        has added it yet — there is nothing to violate this before GREEN.
        It gains power at GREEN, where an implementation that mistakenly
        registers `conformance` (or a `conformance/*.py` entry) as a core
        module will fail it, holding `extra_bd` at zero per §1.
        """
        manifest_path = _ENGINE_ROOT / "core_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        core_modules = manifest.get("core_modules", [])

        assert not any("conformance" in entry for entry in core_modules)
