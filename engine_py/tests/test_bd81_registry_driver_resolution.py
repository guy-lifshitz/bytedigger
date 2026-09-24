"""bd#81 — the enforcement layer must see every registry name, and must look
for a driver where the driver is allowed to live.

Split out of #79/#80. Three defects, all of the same family bd#66 is about: a
rule that is declared and cannot fire.

1. THE COLLISION. `DEFAULT_LINT_DIR` is the package directory, so a driver is
   expected at `bytedigger_engine/<name>.py`. Every registry name is
   hyphenated, and a hyphenated `*.py` in the package is not an importable
   module -- `ci.yml`'s import smoke globs that directory and calls
   `importlib.import_module` on every stem it finds. The first real driver
   placed where the layer looks for it fails the `engine` CI job.

2. A WHOLE LANE INVISIBLE. `registry_names()` enumerates three name lists by
   hand. #80 adds a fourth (`TEXT_LINTS`). A name in it is never swept, so it
   is never required to have a driver, and `run_plan` skips any command whose
   name the pre-pass did not mark present -- silently. A registry name that
   cannot be enforced is precisely the defect this layer exists to remove, and
   here the layer reproduces it one level up.

3. AN EMPTY CORPUS THAT IS NOT EMPTY. `is_spec_file` matches `*_spec.md`,
   lowercase. The repository holds five frozen lot specs named `*_SPEC.md`
   (`conformance/{AUTHORSHIP,CONTRACTS,EMISSIONS,ORACLE,QUANT_LINT}_SPEC.md`),
   each of them the exact document class `scripts/spec_lint/lint_spec.py`
   consumes. #78 leaves the nine `SPEC_LINTS` out of scope on the measured
   ground that the spec corpus is size zero. The measurement is true of the
   predicate and false of the repository.

Deliberately NOT here: whether the repo root should become the DEFAULT driver
directory rather than a declared exception. That is a design question and it
stays open in #81.

PRE-FIX on b565a22 — 4 of 8 fail:
  AC1  PASS   — the package holds no unimportable stem today (the collision is
                latent, not live). Recorded, not rounded up.
  AC2  PASS   — it demonstrates the mechanism rather than being broken by it;
                the import of a hyphenated name already raises today.
  AC3  FAIL   — registry_prepass joins lint_dir and ignores driver_path.
  AC4  PASS   — outcome 1 already refuses an undeclared driverless name; this
                AC exists so the AC3 fix cannot buy its pass by weakening it.
  AC5  FAIL   — registry_names() enumerates three lists by hand.
  AC6  PASSES vacuously — this branch has exactly three `*_LINTS` lists, so a
                hand-written enumeration of three still agrees with the registry.
                It becomes load-bearing the moment #80's fourth list lands, and
                AC5 covers the same defect on a fixture that does not wait.
  AC7  FAIL   — is_spec_file is case-sensitive.
  AC8  FAIL   — the spec corpus measures 0 instead of 5.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import precommit_enforce, precommit_lints

_THIS = Path(__file__).resolve()
ENGINE_PY_ROOT = _THIS.parents[1]
REPO_ROOT = _THIS.parents[2]
PACKAGE_DIR = ENGINE_PY_ROOT / "bytedigger_engine"

# The five frozen lot specs. Named here rather than globbed, so a rename is a
# finding instead of a quietly shrinking corpus.
KNOWN_LOT_SPECS = [
    "engine_py/bytedigger_engine/conformance/AUTHORSHIP_SPEC.md",
    "engine_py/bytedigger_engine/conformance/CONTRACTS_SPEC.md",
    "engine_py/bytedigger_engine/conformance/EMISSIONS_SPEC.md",
    "engine_py/bytedigger_engine/conformance/ORACLE_SPEC.md",
    "engine_py/bytedigger_engine/conformance/QUANT_LINT_SPEC.md",
]


def _all_registry_names():
    return [
        name
        for attr in dir(precommit_lints)
        if attr.endswith("_LINTS") and isinstance(getattr(precommit_lints, attr), list)
        for name in getattr(precommit_lints, attr)
    ]


def _isolate_registry(monkeypatch, **lanes):
    """Empty EVERY name list the registry declares, then install `lanes`.

    Zeroing three lists by name is what this fix is about one level up: #80's
    fourth lane leaked into AC3 and AC4 the moment the layer started
    discovering lanes instead of enumerating them. A fixture that enumerates
    is the same defect wearing test clothes.
    """
    for attr in dir(precommit_lints):
        if attr.endswith("_LINTS") and isinstance(getattr(precommit_lints, attr), list):
            monkeypatch.setattr(precommit_lints, attr, [], raising=True)
    for attr, names in lanes.items():
        monkeypatch.setattr(precommit_lints, attr, list(names), raising=False)


# ─── AC1-AC2: the collision, and the mechanism that makes it a CI failure ───

def test_ac1_no_registry_name_can_legally_be_a_module_in_the_driver_directory():
    """The premise: the layer looks in a directory where no name may live."""
    assert Path(precommit_lints.DEFAULT_LINT_DIR).resolve() == PACKAGE_DIR.resolve(), (
        "this AC is about DEFAULT_LINT_DIR being the package directory; if that "
        "changed, the collision is gone and this AC must be re-baselined"
    )

    names = _all_registry_names()
    assert names, "an empty registry would make every AC here vacuous"
    importable = [n for n in names if n.isidentifier()]
    assert importable == [], (
        f"{len(importable)} of {len(names)} registry names are valid Python "
        f"identifiers: {importable!r}. The AC's claim is that NONE are, which is "
        "why the package directory cannot hold a driver."
    )

    # The invariant CI silently depends on, asserted here so it is a fact.
    unimportable = sorted(
        p.name for p in PACKAGE_DIR.glob("*.py") if not p.stem.isidentifier()
    )
    assert unimportable == [], (
        f"{unimportable!r} cannot be imported as `bytedigger_engine.<stem>`, and "
        "ci.yml's import smoke imports every stem it globs here. The `engine` job "
        "is red."
    )


def test_ac2_the_import_smoke_is_what_breaks_on_a_hyphenated_driver():
    """Reproduce the failure mechanism without polluting the package.

    ci.yml builds `f"bytedigger_engine.{f.stem}"` for every `*.py` it globs and
    calls importlib.import_module on it. Demonstrated on the name itself, so the
    tree is not modified to prove a point about the tree.
    """
    name = precommit_lints.TEST_LINTS[0]
    assert "-" in name, f"expected a hyphenated registry name, got {name!r}"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"bytedigger_engine.{name}")


# ─── AC3-AC4: the pre-pass resolves through the registry, and still refuses ──

def test_ac3_prepass_finds_a_driver_in_the_directory_the_registry_declares(
    tmp_path, monkeypatch
):
    """The fix. A declared directory must be honoured, not overridden by lint_dir."""
    driver_home = tmp_path / "drivers"
    driver_home.mkdir()
    (driver_home / "declared-elsewhere-lint.py").write_text("", encoding="utf-8")

    _isolate_registry(monkeypatch, TEST_LINTS=["declared-elsewhere-lint"])
    monkeypatch.setattr(precommit_lints, "DECLARED_ABSENT", [], raising=False)
    monkeypatch.setattr(
        precommit_lints, "LINT_DRIVER_DIRS",
        {"declared-elsewhere-lint": str(driver_home)}, raising=False,
    )

    # lint_dir is a directory that deliberately does NOT hold the driver.
    empty = tmp_path / "empty"
    empty.mkdir()
    present, violations = precommit_enforce.registry_prepass(str(empty))

    assert violations == [], (
        "the driver exists in the directory the registry declares for it, so the "
        f"pre-pass must find it rather than refuse: {violations!r}"
    )
    assert present["declared-elsewhere-lint"] is True


def test_ac4_prepass_still_refuses_a_driverless_undeclared_name(tmp_path, monkeypatch):
    """The fix must not buy AC3's pass by making absence unobservable."""
    _isolate_registry(monkeypatch, TEST_LINTS=["nowhere-at-all-lint"])
    monkeypatch.setattr(precommit_lints, "DECLARED_ABSENT", [], raising=False)
    monkeypatch.setattr(precommit_lints, "LINT_DRIVER_DIRS", {}, raising=False)

    present, violations = precommit_enforce.registry_prepass(str(tmp_path))

    assert len(violations) == 1 and "nowhere-at-all-lint" in violations[0]
    assert precommit_enforce.REFUSE_MISSING_DRIVER in violations[0]
    assert present["nowhere-at-all-lint"] is False


# ─── AC5-AC6: no name list may be invisible to the layer ────────────────────

def test_ac5_a_name_list_the_layer_never_heard_of_is_still_swept(tmp_path, monkeypatch):
    """A lane added later must not be silently unenforced.

    This is the shape #80 arrives in: `TEXT_LINTS`, a fourth list, declared in
    the registry and absent from a hand-written enumeration. A name that is
    never swept is never marked present, and run_plan skips exactly those --
    so the lint reads as enabled and cannot fire.
    """
    _isolate_registry(monkeypatch, FUTURE_LINTS=["phantom-future-lint"])
    monkeypatch.setattr(precommit_lints, "DECLARED_ABSENT", [], raising=False)
    monkeypatch.setattr(precommit_lints, "LINT_DRIVER_DIRS", {}, raising=False)

    names = precommit_enforce.registry_names()
    assert "phantom-future-lint" in names, (
        f"a *_LINTS list the enumeration does not mention is invisible: {names!r}"
    )

    _, violations = precommit_enforce.registry_prepass(str(tmp_path))
    assert any("phantom-future-lint" in v for v in violations), (
        "an unswept name is never required to have a driver, so it can never "
        f"refuse and never run: {violations!r}"
    )


def test_ac6_registry_names_covers_every_name_list_the_live_registry_declares():
    assert sorted(precommit_enforce.registry_names()) == sorted(_all_registry_names()), (
        "registry_names() must be derived from the registry, not from a "
        "hand-written list of the lanes that existed when it was written"
    )


# ─── AC7-AC8: the spec corpus is not size zero ──────────────────────────────

def test_ac7_is_spec_file_matches_the_lot_specs_in_either_case():
    for path in KNOWN_LOT_SPECS:
        assert precommit_lints.is_spec_file(path), (
            f"{path} is a frozen lot spec -- the exact document class "
            "scripts/spec_lint/lint_spec.py consumes -- and is not classified as one"
        )
    # The lowercase form keeps working; this widens, it does not move.
    assert precommit_lints.is_spec_file("some/lot_spec.md")
    assert not precommit_lints.is_spec_file("README.md")
    assert not precommit_lints.is_spec_file("engine_py/tests/test_spec_helper.py")

    classified = precommit_lints.classify_staged([KNOWN_LOT_SPECS[0], "README.md"])
    assert classified["specs"] == [KNOWN_LOT_SPECS[0]]


def test_ac8_the_repositorys_spec_corpus_is_five_files_not_zero():
    """#78 declares the nine SPEC_LINTS out of scope because the corpus is 0.

    Enforcement over a corpus of size zero is genuinely untestable -- that
    argument is sound. It is the measurement underneath it that is wrong.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if tracked.returncode != 0:
        pytest.skip("no git index in this tree, so there is no corpus to measure")

    specs = sorted(p for p in tracked.stdout.splitlines() if precommit_lints.is_spec_file(p))
    assert specs == sorted(KNOWN_LOT_SPECS), (
        f"expected the five frozen lot specs, measured {len(specs)}: {specs!r}"
    )
