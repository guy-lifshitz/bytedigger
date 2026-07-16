"""RED tests for C844CC77 — phase_5 sibling-discovery misses repo-root tests/ + feature-named files.

9 ACs — pre-GREEN expected failures:
  FAIL: AC1  _module_tokens_for absent pre-GREEN
  FAIL: AC2  _grep_import_sibling_tests absent pre-GREEN
  FAIL: AC3  _grep_import_sibling_tests absent pre-GREEN
  FAIL: AC4  _grep_import_sibling_tests absent pre-GREEN
  FAIL: AC5  _grep_import_sibling_tests absent pre-GREEN
  FAIL: AC6  _grep_import_sibling_tests absent pre-GREEN
  FAIL: AC7  _grep_import_sibling_tests absent pre-GREEN
  FAIL: AC8  wiring — _sibling_test_paths does not call _grep_import_sibling_tests pre-GREEN
  PASS: AC9  backward-compat — conventional sibling already found by existing code
             (may already pass; serves as regression guard post-GREEN)

New helpers referenced via getattr() inside each test body — COLLECTABILITY safe (D1CF5FDF/§1q-ext).
"""
from __future__ import annotations

import re
import unittest.mock as mock
from pathlib import Path

import pytest

# sys.path managed by conftest.py (§1q / 81F97F3D — no module-level sys.path here).
# conftest already inserts engine_py root + engine_py/workflows so imports below work.

import phase_5_implement as _p5m


# ─── AC1: _module_tokens_for basic derivation ────────────────────────────────

class TestAC1ModuleTokensFor:
    def test_ac1_module_tokens_for_bark_core_strategy(self):
        fn = getattr(_p5m, "_module_tokens_for", None)
        assert fn is not None, "_module_tokens_for not implemented yet (BUG3 AC1)"
        result = fn("bark/core/strategy.py")
        assert result == {"strategy", "bark.core.strategy", "bark.core"}, (
            f"AC1: expected {{'strategy','bark.core.strategy','bark.core'}}; got {result!r}"
        )


# ─── AC2: feature-named test matched by from-import ─────────────────────────

class TestAC2GrepImportFeatureNamed:
    def test_ac2_feature_named_test_matched_by_from_import(self, tmp_path):
        fn = getattr(_p5m, "_grep_import_sibling_tests", None)
        assert fn is not None, "_grep_import_sibling_tests not implemented yet (BUG3 AC2)"

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        feat_test = tests_dir / "test_swv2_per_control_routing.py"
        feat_test.write_text("from bark.core.strategy import SWv2Strategy\n")

        result = fn(["bark/core/strategy.py"], str(tmp_path), [str(tests_dir)])
        assert str(feat_test.resolve()) in result, (
            f"AC2: expected {feat_test.resolve()} in result; got {result!r}"
        )


# ─── AC3: root conventional name matched by bare import ──────────────────────

class TestAC3RootConventionalName:
    def test_ac3_root_conventional_test_strategy_matched(self, tmp_path):
        fn = getattr(_p5m, "_grep_import_sibling_tests", None)
        assert fn is not None, "_grep_import_sibling_tests not implemented yet (BUG3 AC3)"

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        conv_test = tests_dir / "test_strategy.py"
        conv_test.write_text("import bark.core.strategy\n")

        result = fn(["bark/core/strategy.py"], str(tmp_path), [str(tests_dir)])
        assert str(conv_test.resolve()) in result, (
            f"AC3: expected {conv_test.resolve()} in result; got {result!r}"
        )


# ─── AC4: unrelated test not matched (bounded) ───────────────────────────────

class TestAC4UnrelatedNotMatched:
    def test_ac4_unrelated_test_not_in_result(self, tmp_path):
        fn = getattr(_p5m, "_grep_import_sibling_tests", None)
        assert fn is not None, "_grep_import_sibling_tests not implemented yet (BUG3 AC4)"

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Presence gate: AC2-equivalent file so matcher fires
        matching = tests_dir / "test_swv2_per_control_routing.py"
        matching.write_text("from bark.core.strategy import SWv2Strategy\n")

        # The file under test — should NOT match
        unrelated = tests_dir / "test_unrelated.py"
        unrelated.write_text("from other.pkg import thing\n")

        result = fn(["bark/core/strategy.py"], str(tmp_path), [str(tests_dir)])

        # Presence gate: matching file IS found (proves matcher fires)
        assert str(matching.resolve()) in result, (
            f"AC4 presence-gate: {matching.resolve()} must be in result; got {result!r}"
        )
        # Absence assertion: unrelated file is NOT found
        assert str(unrelated.resolve()) not in result, (
            f"AC4: unrelated file {unrelated.resolve()} must NOT be in result; got {result!r}"
        )


# ─── AC5: non-test file (helpers.py) not matched ────────────────────────────

class TestAC5NonTestFileExcluded:
    def test_ac5_helper_py_not_in_result(self, tmp_path):
        fn = getattr(_p5m, "_grep_import_sibling_tests", None)
        assert fn is not None, "_grep_import_sibling_tests not implemented yet (BUG3 AC5)"

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Presence gate: a real test file that DOES match
        real_test = tests_dir / "test_real.py"
        real_test.write_text("from bark.core.strategy import X\n")

        # Non-test helper — must NOT appear in result
        helper = tests_dir / "helpers.py"
        helper.write_text("from bark.core.strategy import X\n")

        result = fn(["bark/core/strategy.py"], str(tmp_path), [str(tests_dir)])

        # Presence gate
        assert str(real_test.resolve()) in result, (
            f"AC5 presence-gate: {real_test.resolve()} must be in result; got {result!r}"
        )
        # Absence: helpers.py excluded by test-file filter
        assert str(helper.resolve()) not in result, (
            f"AC5: helpers.py must NOT be in result (not a test file); got {result!r}"
        )


# ─── AC6: package-form import matched ────────────────────────────────────────

class TestAC6PackageFormImport:
    def test_ac6_package_form_from_bark_core_import_strategy_matched(self, tmp_path):
        fn = getattr(_p5m, "_grep_import_sibling_tests", None)
        assert fn is not None, "_grep_import_sibling_tests not implemented yet (BUG3 AC6)"

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        pkgform = tests_dir / "test_pkgform.py"
        pkgform.write_text("from bark.core import strategy\n")

        result = fn(["bark/core/strategy.py"], str(tmp_path), [str(tests_dir)])
        assert str(pkgform.resolve()) in result, (
            f"AC6: package-form import 'from bark.core import strategy' must match; got {result!r}"
        )


# ─── AC7: missing test root → empty set, no exception ────────────────────────

class TestAC7MissingRootNoCrash:
    def test_ac7_missing_root_returns_empty_set_no_exception(self, tmp_path):
        fn = getattr(_p5m, "_grep_import_sibling_tests", None)
        assert fn is not None, "_grep_import_sibling_tests not implemented yet (BUG3 AC7)"

        missing_root = str(tmp_path / "nope")
        result = fn(["a/b.py"], str(tmp_path), [missing_root])
        assert result == set(), (
            f"AC7: missing root → must return set(); got {result!r}"
        )


# ─── AC8: wiring — _sibling_test_paths unions root-tests/ via _grep_import ───

class TestAC8Wiring:
    def test_ac8_sibling_test_paths_includes_root_test_via_import_grep(self, tmp_path):
        from phase_5_implement import _sibling_test_paths  # type: ignore[attr-defined]

        # Build tmp git_cwd with tests/test_feat.py importing bark.core.strategy
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        feat_test = tests_dir / "test_feat.py"
        feat_test.write_text("from bark.core.strategy import SWv2Strategy\n")

        # Red test path (different dir)
        t_dir = tmp_path / "t"
        t_dir.mkdir(parents=True, exist_ok=True)
        red_path = str(tmp_path / "t" / "red.py")
        (tmp_path / "t" / "red.py").write_text("# red\n")

        with mock.patch.object(_p5m, "git_diff_files", return_value=["bark/core/strategy.py"]):
            result = _sibling_test_paths([red_path], "deadbeef", str(tmp_path))

        assert str(feat_test.resolve()) in result, (
            f"AC8: wiring — root tests/test_feat.py (importing bark.core.strategy) "
            f"must appear in _sibling_test_paths result; got {result!r}"
        )


# ─── AC9: backward-compat — conventional sibling still found post-wiring ─────

class TestAC9BackwardCompat:
    def test_ac9_conventional_sibling_and_root_test_both_in_result(self, tmp_path):
        from phase_5_implement import _sibling_test_paths  # type: ignore[attr-defined]

        # Conventional sibling: <prod_dir>/test_<stem>.py
        prod_dir = tmp_path / "bark" / "core"
        prod_dir.mkdir(parents=True, exist_ok=True)
        (prod_dir / "strategy.py").write_text("# prod\n")
        conv_sibling = prod_dir / "test_strategy.py"
        conv_sibling.write_text("# sibling\n")

        # Root-tests feature file (new discovery)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        feat_test = tests_dir / "test_feat.py"
        feat_test.write_text("from bark.core.strategy import SWv2Strategy\n")

        # Red test path
        t_dir = tmp_path / "t"
        t_dir.mkdir(parents=True, exist_ok=True)
        red_path = str(tmp_path / "t" / "red.py")
        (tmp_path / "t" / "red.py").write_text("# red\n")

        with mock.patch.object(_p5m, "git_diff_files", return_value=["bark/core/strategy.py"]):
            result = _sibling_test_paths([red_path], "deadbeef", str(tmp_path))

        assert str(conv_sibling.resolve()) in result, (
            f"AC9: conventional sibling {conv_sibling.resolve()} must still appear (backward-compat); "
            f"got {result!r}"
        )
        assert str(feat_test.resolve()) in result, (
            f"AC9: root tests/test_feat.py {feat_test.resolve()} must also appear (new discovery); "
            f"got {result!r}"
        )
