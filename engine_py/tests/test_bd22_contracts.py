"""RED tests for bd#22 (L2): conformance package, shared contracts, and the
quantifier-completeness lint (7 ACs: AC-C1..AC-C5, AC-P1, AC-P2).

Spec: engine_py/conformance/CONTRACTS_SPEC.md. `conformance` does not exist
yet on this base, so every AC-C*/AC-P1 test imports it (or reads artifacts
that would only change once it exists) and fails today — either at
`ModuleNotFoundError` (a missing package) or at a real assertion once the
module resolves. AC-P2 is the spec's one declared pre-passing shield (§0.6):
it asserts an absence that cannot yet be violated.

Deferred-import discipline (§1q): every `conformance.*` symbol is imported
inside the test body, never at module level, so collection does not break.

v2: module paths and exported names are now pinned by spec §1.5, and the
AC-C5 fixture-document grammar (LEVEL/NON-UNIFORMITY/EXCLUDES/SEAM/
ATTRIBUTE-PATH/BINDING-TIME/NORMALISATION) by §1.6 — this file asserts
against those names/grammar rather than inventing its own. `Finding`'s
attribute names (`kind`, `subject`) and the three `kind` string values
(`missing_non_uniformity_row`, `missing_reductions`, `seam_not_pinned`) are
not pinned by the spec beyond "identifies its kind and subject" (§1.5); this
file's choices for them are noted to the team lead as a possible further
pinning gap, not asserted as spec text.

v4 (gate round 1, `[G22:5]`/`[G22:6]`/`[G22:7]`): AC-C1's recorded seam set
now covers all five prohibited acts plus directory-scan (14 seams, not 3);
AC-C5's check-2 fixture now covers both the "no EXCLUDES" and "EXCLUDES
present but short" (against the new `ADMITS` marker) sub-cases with a
conformant control row alongside them in one document; check-3 is now three
fixtures, each omitting exactly one of the three property lines, each
alongside a conformant seam; plus case-insensitivity, empty-input/
trailing-bare-line, and idempotence coverage for properties the spec pins
but round 1 left unasserted.
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

        `[G22:6]` coverage: AC-C1 prohibits five acts (read a file, touch the
        network, resolve a version, spawn a process, create a directory),
        plus directory-scan as the most plausible eager-registry shape — all
        recorded here, not just the three (`open`/`read_text`/`run`) that
        happened to be handy. Per seam, the wrong implementation this kills:
        `os.listdir`/`Path.iterdir`/`Path.glob` — a `FIXTURES = ...glob(...)`
        registry built at import; `os.mkdir`/`os.makedirs`/`Path.mkdir`/
        `tempfile.mkdtemp` — an eager scratch/cache directory; `socket.socket`/
        `urllib.request.urlopen` — a network reachability probe; `Path.open`
        — a file read that routes through `io.open`, escaping a `builtins.open`
        patch (`Path.read_text` alone only catches this incidentally, and only
        on a cold path); `importlib.metadata.version` — resolved on the
        module attribute at call time, since a module-level
        `from importlib.metadata import version` binds too early for any
        patch reached only afterward (`[G18r2:MINOR-5]`).

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
        import os
        import socket
        import subprocess
        import tempfile
        import urllib.request

        calls: list[tuple[str, tuple, dict]] = []

        def _record(seam_name, real):
            def _wrapped(*args, **kwargs):
                calls.append((seam_name, args, kwargs))
                return real(*args, **kwargs)
            return _wrapped

        # (owner, attribute, recorded seam label) — one entry per plausible
        # eager-side-effect shape, covering all five acts AC-C1 prohibits
        # plus directory-scan (§0.1 over the "prohibited side-effect kinds"
        # collection: the recorded set must exclude every choice, not just
        # the ones that happened to be convenient).
        seams = [
            (builtins, "open", "builtins.open"),
            (Path, "read_text", "Path.read_text"),
            (Path, "open", "Path.open"),
            (subprocess, "run", "subprocess.run"),
            (os, "listdir", "os.listdir"),
            (Path, "iterdir", "Path.iterdir"),
            (Path, "glob", "Path.glob"),
            (os, "mkdir", "os.mkdir"),
            (os, "makedirs", "os.makedirs"),
            (Path, "mkdir", "Path.mkdir"),
            (tempfile, "mkdtemp", "tempfile.mkdtemp"),
            (socket, "socket", "socket.socket"),
            (urllib.request, "urlopen", "urllib.request.urlopen"),
            (importlib.metadata, "version", "importlib.metadata.version"),
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

_MISSING_ROW_SPEC = """\
# Fixture Spec — missing non-uniformity rows, both directions

LEVEL: phases

LEVEL: step_events

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:5]`/`[G22:7]` check-2 fixture: THREE rows, non-uniform in each of the
# two ways check 2 can fire, plus a conformant row — so a lint reporting only
# the first offending row, or implementing only "no EXCLUDES line at all"
# while missing "EXCLUDES present but short", cannot pass this fixture.
_CHECK2_SPEC = """\
# Fixture Spec — check 2: no-EXCLUDES-at-all, present-but-short, and a
# conformant row, in the same document (non-uniform within the check)

LEVEL: phases
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control

LEVEL: step_events
ADMITS: any, all, first, last
NON-UNIFORMITY: step_events — fixture set is non-uniform in both orderings
EXCLUDES: any, first, last

LEVEL: payload_field
ADMITS: any, all
NON-UNIFORMITY: payload_field — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:7]` check-3 fixtures: THREE documents, each with TWO seams (one fully
# conformant, one missing exactly one of the three property lines) — so a
# lint testing presence of only one property line cannot pass all three, and
# a lint reporting only the first seam cannot pass any of them.
_SEAM_MISSING_ATTRIBUTE_PATH_SPEC = """\
# Fixture Spec — check 3: one seam missing ATTRIBUTE-PATH only

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: importlib.metadata.version
BINDING-TIME: call-time
NORMALISATION: none
"""

_SEAM_MISSING_BINDING_TIME_SPEC = """\
# Fixture Spec — check 3: one seam missing BINDING-TIME only

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: importlib.metadata.version
ATTRIBUTE-PATH: importlib.metadata.version
NORMALISATION: none
"""

_SEAM_MISSING_NORMALISATION_SPEC = """\
# Fixture Spec — check 3: one seam missing NORMALISATION only

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: importlib.metadata.version
ATTRIBUTE-PATH: importlib.metadata.version
BINDING-TIME: call-time
"""

# Conformant control: every level's row covers exactly the reductions its
# level ADMITS (default four, since neither level overrides ADMITS) — the
# `[G22:5]` fix. A round-1 control named only three reductions for
# `step_events` while requiring zero findings, which false-failed any GREEN
# reading check 2 the only computable way ("cover all four").
_CONFORMANT_SPEC = """\
# Fixture Spec — fully conformant control

LEVEL: phases
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: step_events
NON-UNIFORMITY: step_events — fixture set is non-uniform in both orderings
EXCLUDES: any, all, first, last

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
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
        """AC-C5(1). Kills a lint that only walks collection levels in one
        direction — e.g. containers only, the exact shape of bd#7's rounds
        4-8, which climbed the ladder upward and missed the element-kind
        rung round 9 found below it. Both `phases` and `step_events` are
        missing a non-uniformity row entirely and must both be flagged
        independently — kills a lint that reports only one of the two.

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

        finding = findings[0]
        assert dataclasses.is_dataclass(finding)
        with pytest.raises(dataclasses.FrozenInstanceError):
            finding.kind = "mutated"

    def test_ac_c5_flags_row_not_enumerating_reductions(self):
        """AC-C5(2). `[G22:5]`/`[G22:7]`: kills a lint implementing only the
        "no EXCLUDES line at all" half of check 2 — `phases` has no EXCLUDES
        line at all; `step_events` has one that is present but short
        (EXCLUDES three of the four reductions its level ADMITS, the exact
        gap `[G18:1]` requires and a level-only enumeration misses). Also
        kills a lint reporting only the first offending row (three rows
        here, two non-conformant, one conformant) and one that ignores an
        explicit `ADMITS` override (`payload_field` ADMITS only `any, all` —
        an unordered collection, where `first`/`last` are meaningless — and
        its row's `EXCLUDES: any, all` fully covers that admitted set, so it
        must NOT be flagged). Also confirms check (1) does not spuriously
        fire, since every row here is present.
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
        assert not any(f.subject == "payload_field" for f in findings)
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_flags_seam_missing_attribute_path(self):
        """AC-C5(3). `[G22:7]`: one of three fixtures, each omitting exactly
        one of the three property lines, so each is independently
        load-bearing — a lint testing only ATTRIBUTE-PATH presence would
        pass this fixture's `Path.read_text` row (conformant) but must still
        flag `importlib.metadata.version` (ATTRIBUTE-PATH missing here), and
        must not flag the conformant seam (non-uniform within the check:
        two seams, one conformant, one not).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_ATTRIBUTE_PATH_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "importlib.metadata.version"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_flags_seam_missing_binding_time(self):
        """AC-C5(3). `[G22:7]`: kills a lint that tests only ATTRIBUTE-PATH
        presence — `importlib.metadata.version` here pins ATTRIBUTE-PATH
        correctly (as `mkdtemp` and `importlib.metadata.version` both
        historically did, per §0.2) and omits only BINDING-TIME, which such
        a lint would silently pass.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_BINDING_TIME_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "importlib.metadata.version"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_flags_seam_missing_normalisation(self):
        """AC-C5(3). `[G22:7]`: kills a lint that tests only ATTRIBUTE-PATH
        (or ATTRIBUTE-PATH + BINDING-TIME) presence — `importlib.metadata.version`
        here pins both correctly (mirroring `Path.read_text`'s historical gap,
        which pinned the seam and BINDING-TIME but not NORMALISATION) and
        omits only NORMALISATION.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_NORMALISATION_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "importlib.metadata.version"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_conformant_control_passes(self):
        """AC-C5 control. A document with every level's non-uniformity row
        (both directions) fully covering its ADMITS-ed reductions (`[G22:5]`
        — both levels here cover all four, the default admitted set), and a
        seam with its full interception property, must produce zero
        findings. Kills a lint that over-fires on well-formed input (e.g. a
        keyword sweep that mis-scores conformant documents — the failure
        mode §3's scope-limit note calls out from bd#7's own history: 11 of
        13 ACs mis-scored).
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
        `text.splitlines()` that raises `IndexError` on empty input, instead
        of returning an empty findings list (nothing quantified yet).
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

    def test_ac_c5_lint_is_idempotent_on_control(self):
        """Gate item 6: kills a GREEN that accumulates findings into
        module-level state across calls (e.g. an `_ALL_FINDINGS.append(...)`
        instead of building a fresh list per call) — caught here directly on
        repeated calls, rather than relying on incidental test-execution
        ordering, which pytest's randomisation may not preserve.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        first = lint_quantifier_completeness(_CONFORMANT_SPEC)
        second = lint_quantifier_completeness(_CONFORMANT_SPEC)

        assert first == []
        assert second == []


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
