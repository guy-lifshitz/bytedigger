"""RED tests for bd#22 (L2): conformance package and shared contracts
(6 ACs: AC-C1..AC-C4, AC-P1, AC-P2).

`AC-C5` (the quantifier-completeness lint) and every fixture/test that only
it used were REMOVED from this file and move to bd#24, which carries the
full inherited state. Round-4 gate: REJECTED (2 blocking); the dispatcher's
exit criterion fired because one blocker (`[G22:B2]` — check 3 never sees a
bare `SEAM:` with zero property lines, since commit `9947142` deleted the
`_SEAM_NOT_PINNED_SPEC` fixture that covered it when the `[G22:7]` fix
replaced it with the three one-property-missing fixtures) was introduced by
a previous fix rather than pre-existing. `AC-C5` was the only AC in this lot
that ever produced a blocking finding, across all four gate rounds; the six
survivors below were clean every round. This split is a scoping decision by
the team lead, not a defect in the six remaining ACs.

Spec: engine_py/conformance/CONTRACTS_SPEC.md. `engine_py/conformance/`
exists on this base only as `CONTRACTS_SPEC.md` — no `__init__.py`, so
`import bytedigger_engine.conformance` alone already resolves as an empty **namespace**
package today (`[G22:3]`) and forces nothing. Every test below therefore
imports a real submodule (`conformance.report`/`.tokens`/`.quant_lint`) or
reads a packaging artifact directly, and fails today — either at
`ModuleNotFoundError` (no such submodule exists) or at a real assertion once
the module resolves. AC-P2 is the spec's one declared pre-passing shield
(§0.6): it asserts an absence that cannot yet be violated.

Deferred-import discipline (§1q): every `conformance.*` symbol is imported
inside the test body, never at module level, so collection does not break.

Module paths and exported names are pinned by spec §1.5 (`conformance/
report.py` → `L0Report`; `conformance/tokens.py` → the four token
constants). `[G22:10]`: AC-C1's recorded seam set is the spec-pinned
27-name list (§2 `[G22:10]`), exhausted at the API level rather than
reasoned about per-act, filtered by `threading.get_ident()` since `calls`
is a closure-local list a concurrent thread could still write into.
`[G22:3]`: `conformance` must be a real package (`__init__.py` present),
forced by importing real submodules under the recorders rather than the
bare (vacuously-resolving) top-level name, and asserted directly via
`conformance.__file__ is not None`.
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

        `[G22:3]`: forcing on `import bytedigger_engine.conformance` alone is vacuous — a
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

            from bytedigger_engine import conformance  # noqa: F401
            from bytedigger_engine import conformance
            import bytedigger_engine.conformance.quant_lint  # noqa: F401
            from bytedigger_engine import conformance
            import bytedigger_engine.conformance.report  # noqa: F401
            from bytedigger_engine import conformance
            import bytedigger_engine.conformance.tokens  # noqa: F401

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

        `[G22:20]`: `frozen=True` blocks attribute *rebinding*, not in-place
        *mutation* of a mutable container field. Round 1 of this fix passed
        MUTABLE containers straight through and then asserted properties of
        the test's own fixture objects (a tuple's missing `.append`, a
        pre-built `MappingProxyType`'s rejection of item assignment) rather
        than of anything `L0Report` did — a GREEN annotating
        `requirements: list[str]` with no coercion passed all three,
        because dataclasses do not coerce and the field held exactly the
        list this test constructed. The forcing form passes MUTABLE inputs
        in (`list`, `list`, `dict`) and asserts the STORED fields are
        immutable *and* their contents survived the conversion — killing
        both the annotation-only GREEN (stores the list unchanged, so
        `isinstance(..., tuple)` fails) and a GREEN that "immutabilises" by
        discarding contents (the equality checks fail).
        """
        import dataclasses

        from bytedigger_engine.conformance.report import L0Report

        report = L0Report(
            passed=True,
            requirements=["a"],
            violations=["b"],
            labels={"k": "v"},
        )

        assert dataclasses.is_dataclass(report)
        field_names = {f.name for f in dataclasses.fields(report)}
        assert {"passed", "requirements", "violations", "labels"} <= field_names

        with pytest.raises(dataclasses.FrozenInstanceError):
            report.passed = False

        assert isinstance(report.requirements, tuple)
        assert isinstance(report.violations, tuple)
        assert report.requirements == ("a",)
        assert report.violations == ("b",)

        with pytest.raises(TypeError):
            report.labels["mutated"] = True
        assert report.labels["k"] == "v"


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

        M6: the distinctness check that used to follow these four assertions
        (`len(values) == len(set(values))`) was dead weight — since each
        value is pinned by-value to one of four already-distinct string
        literals, distinctness cannot fail once these four assertions pass.
        Dropped per §0.4 (no assertion that cannot fail); the by-value pins
        above are strictly stronger and are what actually kills the
        unification defect this test's docstring names.
        """
        from bytedigger_engine.conformance.tokens import (
            ADVERSARY_NOT_EXECUTED,
            REQUIREMENT_FAILED,
            REQUIREMENT_NOT_CHECKED,
            REQUIREMENT_PASSED,
        )

        assert REQUIREMENT_PASSED == "passed"
        assert REQUIREMENT_FAILED == "failed"
        assert REQUIREMENT_NOT_CHECKED == "not-checked"
        assert ADVERSARY_NOT_EXECUTED == "not_executed"


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
        `import bytedigger_engine.conformance` is vacuous (namespace package, already
        resolves) — this imports the real `conformance.quant_lint`
        submodule instead, which forces `ModuleNotFoundError` today; after
        GREEN, that submodule must exist AND the declared dependency set
        must be unchanged from this baseline.
        """
        from bytedigger_engine import conformance
        import bytedigger_engine.conformance.quant_lint  # noqa: F401 — forces failure today

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
# AC-P1 — packaging include gains conformance*
# ─────────────────────────────────────────────────────────────────────────


class TestPackaging:
    def test_ac_p1_packages_find_include_gains_conformance(self):
        """AC-P1. Kills an implementation that adds the conformance package
        files but forgets to add `"conformance*"` to
        `[tool.setuptools.packages.find] include`, silently excluding it
        from the shipped package — the packaging-side twin of the AC-P2
        manifest gap.

        `[G22:21]` regression shield: `"conformance*" in include_list` alone
        is satisfied by a GREEN that *replaces* the list with just
        `["conformance*"]` — nothing else in the repo pins that list (no
        other test, and CI parity reads `py-modules`, not this list), so
        `lib*`/`workflows*`/`security*`/`scripts*` would silently stop
        shipping. Kills that GREEN by asserting the four pre-existing
        entries are still present as a subset, not merely that the new one
        was added.
        """
        pyproject_path = _ENGINE_ROOT / "pyproject.toml"
        text = pyproject_path.read_text()

        section_match = re.search(
            r"\[tool\.setuptools\.packages\.find\](.*?)(?:\n\[|\Z)", text, re.S
        )
        include_match = re.search(r"include\s*=\s*\[(.*?)\]", section_match.group(1), re.S)
        include_list = re.findall(r'"([^"]+)"', include_match.group(1))

        # bd#44: the five shipped dirs are no longer five top-level `include`
        # patterns — they are SUBpackages of the one package the distribution
        # owns, so `bytedigger_engine*` matches all of them. The AC's subject is
        # unchanged (conformance ships, and adding it did not stop the other
        # four from shipping); only the spelling of "ships" moved. Asserted
        # against setuptools' own matching rule rather than against a literal
        # list, so it survives the next re-spelling too.
        import fnmatch

        for sub in ("conformance", "lib", "workflows", "security", "scripts"):
            dotted = f"bytedigger_engine.{sub}"
            assert any(fnmatch.fnmatchcase(dotted, pat) for pat in include_list), (
                f"{dotted} matches no include pattern in {include_list}"
            )
            assert (_ENGINE_ROOT / "bytedigger_engine" / sub / "__init__.py").is_file(), (
                f"{sub} has no __init__.py, so packages.find would not ship it"
            )

    def test_ac_p2_core_manifest_excludes_conformance(self):
        """AC-P2 (declared pre-passing shield, §0.6). `core_manifest.json`
        necessarily excludes `conformance` today because no implementation
        has added it yet — there is nothing to violate this before GREEN.
        It gains power at GREEN, where an implementation that mistakenly
        registers `conformance` (or a `conformance/*.py` entry) as a core
        module will fail it, holding `extra_bd` at zero per §1.

        `[G22:22]`: narrowed from a bare `"conformance" in entry` substring
        check to an exact/prefix match — the substring form would false-fail
        a legitimately-core module like `lib/conformance_checker.py`, which
        L7-L12 plausibly add. Verified against the current 82-entry
        `core_modules` list that no entry matches the substring form but not
        this narrowed one, so nothing already covered is lost by narrowing.
        """
        manifest_path = _ENGINE_ROOT / "core_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        core_modules = manifest.get("core_modules", [])

        assert not any(
            entry == "conformance" or entry.startswith("conformance/")
            for entry in core_modules
        )
