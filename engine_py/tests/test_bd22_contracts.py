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

The AC-C5 fixture documents use a small inline DSL (LEVEL/REDUCTION/
NON-UNIFORMITY/EXCLUDES/SEAM/ATTRIBUTE-PATH/BINDING-TIME/NORMALISATION line
markers) invented for this test file to give the lint something concrete to
parse; it is not part of the spec's prose and GREEN is free to choose any
parser that recognises it.
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

        Record-and-delegate, not raise-and-sabotage: each seam is wrapped to
        record the call and then delegate to the real implementation, so
        pytest's own machinery (which also calls open/read_text/run) keeps
        working even if the import trips a seam. The assertion is on the
        recorded evidence, not on whether the interpreter survived. The
        patches are scoped to a `monkeypatch.context()` covering only the
        `sys.modules` purge and the import statement, so they are undone
        before this test's own teardown or any later test runs — no global
        state survives this test either way.
        """
        import builtins
        import subprocess

        calls: list[tuple[str, tuple, dict]] = []
        _real_open = builtins.open
        _real_read_text = Path.read_text
        _real_run = subprocess.run

        def _recording_open(*args, **kwargs):
            calls.append(("builtins.open", args, kwargs))
            return _real_open(*args, **kwargs)

        def _recording_read_text(self_path, *args, **kwargs):
            calls.append(("Path.read_text", (self_path, *args), kwargs))
            return _real_read_text(self_path, *args, **kwargs)

        def _recording_run(*args, **kwargs):
            calls.append(("subprocess.run", args, kwargs))
            return _real_run(*args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(builtins, "open", _recording_open)
            m.setattr(Path, "read_text", _recording_read_text)
            m.setattr(subprocess, "run", _recording_run)

            for name in list(sys.modules):
                if name == "conformance" or name.startswith("conformance."):
                    del sys.modules[name]

            import conformance  # noqa: F401

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
        explicit rejection of that mechanism). `import conformance` forces
        RED today (ModuleNotFoundError); after GREEN, conformance must exist
        AND the declared dependency set must be unchanged from this baseline.
        """
        import conformance  # noqa: F401 — forces failure today

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

_MISSING_ROW_SPEC = """\
# Fixture Spec — missing non-uniformity rows, both directions

## Quantified Requirements
- LEVEL: phases (container)
  REDUCTION: implementation-choice

- LEVEL: step_events (element)
  REDUCTION: implementation-choice

## Seams
- SEAM: Path.read_text
  ATTRIBUTE-PATH: pathlib.Path.read_text
  BINDING-TIME: call-time
  NORMALISATION: none
"""

_ROW_NO_REDUCTIONS_SPEC = """\
# Fixture Spec — row present, reductions not enumerated

## Quantified Requirements
- LEVEL: phases (container)
  REDUCTION: implementation-choice
  NON-UNIFORMITY: fixture set has >=2 members, one violating, plus control

## Seams
- SEAM: Path.read_text
  ATTRIBUTE-PATH: pathlib.Path.read_text
  BINDING-TIME: call-time
  NORMALISATION: none
"""

_SEAM_NOT_PINNED_SPEC = """\
# Fixture Spec — seam named, interception property not pinned

## Quantified Requirements
- LEVEL: phases (container)
  REDUCTION: implementation-choice
  NON-UNIFORMITY: fixture set has >=2 members, one violating, plus control
  EXCLUDES: any, all, first, last

## Seams
- SEAM: Path.read_text
"""

_CONFORMANT_SPEC = """\
# Fixture Spec — fully conformant control

## Quantified Requirements
- LEVEL: phases (container)
  REDUCTION: implementation-choice
  NON-UNIFORMITY: fixture set has >=2 members, one violating, plus control
  EXCLUDES: any, all, first, last

- LEVEL: step_events (element)
  REDUCTION: implementation-choice
  NON-UNIFORMITY: fixture set is non-uniform in both orderings
  EXCLUDES: any, first, last

## Seams
- SEAM: Path.read_text
  ATTRIBUTE-PATH: pathlib.Path.read_text
  BINDING-TIME: call-time
  NORMALISATION: none
"""


class TestQuantifierCompletenessLint:
    def test_ac_c5_flags_missing_non_uniformity_row_both_directions(self, tmp_path):
        """AC-C5(1). Kills a lint that only walks collection levels in one
        direction — e.g. containers only, the exact shape of bd#7's rounds
        4-8, which climbed the ladder upward and missed the element-kind
        rung round 9 found below it. Both `phases` (container) and
        `step_events` (element) are missing a non-uniformity row entirely
        and must both be flagged independently.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        spec_path = tmp_path / "spec.md"
        spec_path.write_text(_MISSING_ROW_SPEC)
        violations = lint_quantifier_completeness(spec_path)

        assert any(
            v.startswith("phases:") and "no non-uniformity row" in v
            for v in violations
        )
        assert any(
            v.startswith("step_events:") and "no non-uniformity row" in v
            for v in violations
        )

    def test_ac_c5_flags_row_not_enumerating_reductions(self, tmp_path):
        """AC-C5(2). Kills a lint that accepts a bare "non-uniform, >=2
        members" row without naming which of any/all/first/last the
        fixtures exclude — the exact gap L1's [G18:1] found: a fixture
        non-uniform in one ordering excluded `any` and `first` but left
        `last` alive, and the wrong implementation passed all 38 tests.
        Also confirms check (1) does not spuriously fire here, since a row
        is present — a lint conflating "row present" with "row complete"
        would fail this orthogonality assertion.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        spec_path = tmp_path / "spec.md"
        spec_path.write_text(_ROW_NO_REDUCTIONS_SPEC)
        violations = lint_quantifier_completeness(spec_path)

        assert any(
            v.startswith("phases:") and "does not enumerate" in v
            for v in violations
        )
        assert not any("no non-uniformity row" in v for v in violations)

    def test_ac_c5_flags_seam_not_pinning_interception_property(self, tmp_path):
        """AC-C5(3). Kills a lint that accepts a bare seam name (mechanism
        only) without the attribute path, call-time-resolution statement,
        and normalisation note — the exact shape that bit three consecutive
        lots (`mkdtemp`, `Path.read_text`, `importlib.metadata.version`),
        each with the correct seam named but an unpinned property.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        spec_path = tmp_path / "spec.md"
        spec_path.write_text(_SEAM_NOT_PINNED_SPEC)
        violations = lint_quantifier_completeness(spec_path)

        assert any(
            v.startswith("Path.read_text:") and "does not pin interception property" in v
            for v in violations
        )

    def test_ac_c5_conformant_control_passes(self, tmp_path):
        """AC-C5 control. A spec documenting every level's non-uniformity
        row (both directions) with enumerated reductions, and a seam with
        its full interception property, must produce zero violations. Kills
        a lint that over-fires on well-formed input (e.g. a keyword sweep
        that mis-scores conformant documents, the failure mode the spec's
        §3 scope-limit note calls out from bd#7's own history).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        spec_path = tmp_path / "spec.md"
        spec_path.write_text(_CONFORMANT_SPEC)
        violations = lint_quantifier_completeness(spec_path)

        assert violations == []


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
