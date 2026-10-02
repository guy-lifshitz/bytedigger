"""RED tests for bd#166 -- port of Rule P (one-sided negative exit-code predicate).

Spec: docs/decisions/2026-10-02-bd166-rule-p-port.md (section 3).
Oracle (H group, AC ids kept, fixtures verbatim): HAL
engine_py/tests/test_one_sided_assert_gh1373.py, Rule P subset
(AC7-AC13, AC29, AC31, AC15, AC16, AC23-AC25, AC28, AC30, AC34-AC37).

Collectability (workflows.md 1q): module scope imports only stdlib, pytest and
modules that already exist. `bytedigger_engine.one_sided_predicate` does not exist
yet, so it is imported INSIDE each test body; every test fails at assert/import
time for its own reason. No sys.path mutation, no conftest import.

Principle (HAL GH1373 rev2): a clean side is the dirty fixture PLUS the control,
never a candidate-free fixture, so a no-op scanner cannot pass.
Determinism (workflows.md 1i): tmp_path fixtures, subprocess stdin=DEVNULL,
timeout=60, env via monkeypatch. No LLM, no network.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bytedigger_engine import error_codes, flags_catalog, precommit_lints
from bytedigger_engine.contracts import StepResult, WorkflowContext

_HERE = Path(__file__).resolve().parent            # .../engine_py/tests
_ENGINE_PY = _HERE.parent                           # .../engine_py
_REPO_ROOT = Path(__file__).resolve().parents[2]    # repo root
_DRIVER = _REPO_ROOT / "one-sided-predicate-lint.py"
_PKG_DIR = _ENGINE_PY / "bytedigger_engine"
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
_PHASE5_SRC = _PKG_DIR / "workflows" / "phase_5_implement.py"


# --------------------------------------------------------------- fixtures


def _block(body: str, name: str = "checks failure") -> str:
    return f'test("{name}", () => {{\n{body}\n}});\n'


def _run_pred_lint(*files, json_output: bool = False):
    argv = [sys.executable, str(_DRIVER)]
    argv.extend(str(f) for f in files)
    if json_output:
        argv.append("--json")
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _find_dict_with_keys(obj, keys: set):
    if isinstance(obj, dict):
        if keys.issubset(obj.keys()):
            return obj
        for v in obj.values():
            found = _find_dict_with_keys(v, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_dict_with_keys(item, keys)
            if found is not None:
                return found
    return None


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"git_cwd": str(tmp_path)}, question="q",
        session_id="test-bd166", persona="hal", framework=None, domain=None,
    )


def _make_legacy_prev(red_test_paths: list) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": "scratchpad/spec.md",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _write_test_file(tmp_path: Path, relpath: str, content: str) -> str:
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return relpath


def _unreadable_ts_target(tmp_path: Path, name: str) -> str:
    """Dangling symlink; the OSError is observed, not assumed."""
    link = tmp_path / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "definitely_absent_target.test.ts")
    try:
        link.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return name
    raise AssertionError(f"fixture precondition failed: {link} is READABLE")


def _scan(text: str):
    from bytedigger_engine.one_sided_predicate import scan_one_sided_predicates
    return scan_one_sided_predicates(text)


def _summ(text: str):
    from bytedigger_engine.one_sided_predicate import summarize_predicates
    return summarize_predicates(text)


# ------------------------------------------------------------ H group: scanner


def test_ac7_rule_p_catches_bare_negative_code_predicate():
    text = _block("  const r = run();\n  expect(r.code).not.toBe(0);")

    findings = _scan(text)

    assert len(findings) == 1, f"expected exactly 1 finding, got {findings!r}"
    required_keys = {"line", "subject", "form", "reason"}
    assert required_keys.issubset(findings[0].keys()), findings[0].keys()
    predicate_line = text.splitlines().index("  expect(r.code).not.toBe(0);") + 1
    assert findings[0]["line"] == predicate_line


def test_ac8_rule_p_same_block_positive_control_silences():
    text = _block(
        "  const r = run();\n"
        "  expect(r.code).not.toBe(0);\n"
        "  expect(r.code).toBe(9);"
    )

    findings = _scan(text)
    summary = _summ(text)

    assert findings == [], f"expected [], got {findings!r}"
    assert summary["candidates"] == 1, f"got {summary!r}"
    assert summary["controlled"] == 1, f"got {summary!r}"


def test_ac9_rule_p_neighbouring_block_control_does_not_silence():
    text = (
        _block("  expect(r.code).toBe(9);", name="positive")
        + "\n"
        + _block("  expect(r.code).not.toBe(0);", name="negative")
    )

    findings = _scan(text)

    assert len(findings) == 1, f"control in a neighbouring block must not silence, got {findings!r}"


def test_ac10_rule_p_escape_window_is_exactly_three_lines_above():
    same_line = _block("  expect(r.code).not.toBe(0); // one-sided-ok: legacy assert")
    three_above = _block(
        "  // one-sided-ok: legacy assert\n"
        "  const a = 1;\n"
        "  const b = 2;\n"
        "  expect(r.code).not.toBe(0);"
    )
    four_above = _block(
        "  // one-sided-ok: legacy assert\n"
        "  const a = 1;\n"
        "  const b = 2;\n"
        "  const c = 3;\n"
        "  expect(r.code).not.toBe(0);"
    )
    no_escape = _block("  expect(r.code).not.toBe(0);")

    assert _scan(same_line) == [], "same-line escape must silence"
    assert _scan(three_above) == [], "escape 3 lines above must silence"
    assert len(_scan(four_above)) == 1, "escape 4 lines above must NOT silence"
    assert len(_scan(no_escape)) == 1, "without escape the finding must stand"


def test_ac11_rule_p_non_code_subject_neighbour_still_flags_code_one():
    text = (
        _block("  expect(r.stdout).not.toBe(0);", name="stdout check")
        + "\n"
        + _block("  expect(r.code).not.toBe(0);", name="code check")
    )

    findings = _scan(text)

    assert len(findings) == 1, f"expected exactly 1 finding, got {findings!r}"
    predicate_line = text.splitlines().index("  expect(r.code).not.toBe(0);") + 1
    assert findings[0]["line"] == predicate_line


def test_ac12_rule_p_not_to_be_null_form_flagged():
    findings = _scan(_block("  expect(r.code).not.toBeNull();"))

    assert len(findings) == 1, f"expected exactly 1 finding, got {findings!r}"


def test_ac13_rule_p_truthy_form_excluded_neighbour_still_flags():
    text = (
        _block("  expect(r.code).toBeTruthy();", name="truthy check")
        + "\n"
        + _block("  expect(r.code).not.toBe(0);", name="code check")
    )

    findings = _scan(text)

    assert len(findings) == 1, f"expected exactly 1 finding, got {findings!r}"
    predicate_line = text.splitlines().index("  expect(r.code).not.toBe(0);") + 1
    assert findings[0]["line"] == predicate_line, f"got {findings[0]!r}"


def test_ac29_candidate_outside_any_block_whole_file_scope():
    clean_text = "const setup = 1;\nexpect(r.code).not.toBe(0);\nexpect(r.code).toBe(9);\n"
    dirty_text = "const setup = 1;\nexpect(r.code).not.toBe(0);\n"

    assert _scan(clean_text) == [], f"got {_scan(clean_text)!r}"
    findings = _scan(dirty_text)
    assert len(findings) == 1, f"expected exactly 1 finding, got {findings!r}"


def test_ac31_summarize_predicates_escape_is_enumerable():
    escaped_text = _block("  expect(r.code).not.toBe(0); // one-sided-ok: legacy assert")
    not_escaped_text = _block("  expect(r.code).not.toBe(0);")

    s_escaped = _summ(escaped_text)
    s_not_escaped = _summ(not_escaped_text)

    assert s_escaped["escaped"] == 1 and s_escaped["one_sided"] == 0, f"got {s_escaped!r}"
    assert s_not_escaped["escaped"] == 0 and s_not_escaped["one_sided"] == 1, f"got {s_not_escaped!r}"


# ----------------------------------------------------------------- B1 (D1)


@pytest.mark.parametrize("subject", ["r.exit_code", "r.statusCode"])
def test_b1_snake_and_camel_subjects_are_codey_two_sided(subject):
    dirty = _block(f"  expect({subject}).not.toBe(0);")
    clean = _block(f"  expect({subject}).not.toBe(0);\n  expect({subject}).toBe(2);")

    findings = _scan(dirty)
    assert len(findings) == 1, f"{subject}: dirty side must yield 1 finding, got {findings!r}"

    assert _scan(clean) == [], f"{subject}: control side must be clean, got {_scan(clean)!r}"
    summary = _summ(clean)
    assert summary["controlled"] == 1, f"{subject}: got {summary!r}"


# ----------------------------------------------------------------- B2 / B3 / B4


def test_b2_empty_input_assert_shares_the_codey_helper():
    from bytedigger_engine import empty_input_assert, one_sided_predicate

    assert empty_input_assert.is_codey is one_sided_predicate.is_codey
    assert not hasattr(empty_input_assert, "_CODEY_WORDS"), "duplicate word list must be deleted"
    assert not hasattr(empty_input_assert, "_tokens"), "duplicate tokenizer must be deleted"


def test_b3_precommit_lints_uses_the_one_declared_corpus_and_a_real_driver():
    from bytedigger_engine import one_sided_predicate

    # Parity pin, not identity: precommit_lints must stay standalone-copyable
    # (bd66 _materialize_package_copy), so it must NOT import one_sided_predicate.
    assert tuple(precommit_lints._TS_TEST_SUFFIXES) == tuple(one_sided_predicate.TS_TEST_SUFFIXES)
    assert "one-sided-predicate-lint" not in precommit_lints.DECLARED_ABSENT
    drv = Path(precommit_lints.driver_path("one-sided-predicate-lint", "/nonexistent-build-dir"))
    assert drv.is_file(), f"driver must exist, resolved to {drv}"
    assert drv.resolve().parent == _REPO_ROOT.resolve(), f"driver must sit at repo root, got {drv}"


def test_b4_module_is_core_and_pure():
    manifest = json.loads((_ENGINE_PY / "core_manifest.json").read_text(encoding="utf-8"))
    core = manifest["core_modules"]
    assert any(m in ("one_sided_predicate.py", "one_sided_predicate") for m in core), (
        "one_sided_predicate must be listed in core_manifest.json core_modules"
    )

    src_path = _PKG_DIR / "one_sided_predicate.py"
    assert src_path.is_file(), f"missing {src_path}"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in ("subprocess", "bytedigger_engine"):
                    offenders.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in ("subprocess", "bytedigger_engine"):
                offenders.append(node.module)
            if node.level and node.level > 0:
                offenders.append("relative-import")
        elif isinstance(node, ast.Attribute) and node.attr == "environ":
            offenders.append("environ")
    assert offenders == [], f"purity violated: {offenders!r}"


# --------------------------------------------------------------- CLI (AC15/16)


def test_ac15_cli_rule_p_exit_codes(tmp_path):
    clean = tmp_path / "clean.test.ts"
    clean.write_text(
        _block("  expect(r.code).not.toBe(0);\n  expect(r.code).toBe(9);"), encoding="utf-8",
    )
    dirty = tmp_path / "dirty.test.ts"
    dirty.write_text(_block("  expect(r.code).not.toBe(0);"), encoding="utf-8")
    missing = tmp_path / "does_not_exist.test.ts"

    rc_dirty, _o2, e2 = _run_pred_lint(dirty)
    rc_clean, _o, e1 = _run_pred_lint(clean)
    rc_missing, _o3, e3 = _run_pred_lint(missing)

    assert rc_dirty == 1, f"expected 1 with the control removed, got {rc_dirty}; stderr={e2!r}"
    assert rc_clean == 0, f"expected 0 with the control present, got {rc_clean}; stderr={e1!r}"
    assert rc_missing == 2, f"expected 2 on unreadable path, got {rc_missing}; stderr={e3!r}"


def test_ac16_cli_rule_p_json_output_parses_with_expected_fields(tmp_path):
    dirty = tmp_path / "dirty.test.ts"
    dirty.write_text(_block("  expect(r.code).not.toBe(0);"), encoding="utf-8")

    rc, out, err = _run_pred_lint(dirty, json_output=True)

    assert rc == 1, f"expected rc==1 on the finding file, got {rc}; stderr={err!r}"
    payload = json.loads(out)
    found = _find_dict_with_keys(payload, {"line", "form", "subject"})
    assert found is not None, f"expected line/form/subject in JSON payload, got {payload!r}"


# --------------------------------------------------- phase-5 wiring (AC23/24/28)


def test_ac23_collect_red_lint_findings_real_side_effect(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    ctx = _make_ctx(tmp_path)
    cfg = ctx.org_config or {}

    dirty = tmp_path / "bd166_dirty.test.ts"
    dirty.write_text(_block("  expect(r.code).not.toBe(0);"), encoding="utf-8")
    captured: list = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture):
        findings_dirty = p5._collect_red_lint_findings(
            [str(dirty.resolve())], str(tmp_path), ctx, cfg
        )
    violations = [c for c in captured if c[0] == "red_one_sided_predicate_violation"]
    assert len(violations) == 1, f"expected exactly one violation event, got {captured!r}"
    matches = [f for f in findings_dirty if f.get("error_code") == "E_RED_ONE_SIDED_PREDICATE"]
    assert matches, f"expected an E_RED_ONE_SIDED_PREDICATE record, got {findings_dirty!r}"
    assert matches[0].get("rule") == "one-sided-predicate", f"got {matches[0]!r}"
    assert matches[0].get("recoverable") is True, f"got {matches[0]!r}"

    clean = tmp_path / "bd166_clean.test.ts"
    clean.write_text(
        _block("  expect(r.code).not.toBe(0);\n  expect(r.code).toBe(9);"), encoding="utf-8",
    )
    findings_clean = p5._collect_red_lint_findings([str(clean.resolve())], str(tmp_path), ctx, cfg)
    assert not [
        f for f in findings_clean if f.get("error_code") == "E_RED_ONE_SIDED_PREDICATE"
    ], f"controlled file must stay clean, got {findings_clean!r}"


def test_ac24_kill_switch_disables_gate_and_emits_gate_disabled(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    ctx = _make_ctx(tmp_path)
    cfg = ctx.org_config or {}
    dirty = tmp_path / "bd166_dirty2.test.ts"
    dirty.write_text(_block("  expect(r.code).not.toBe(0);"), encoding="utf-8")

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    findings_on = p5._collect_red_lint_findings([str(dirty.resolve())], str(tmp_path), ctx, cfg)
    assert any(f.get("error_code") == "E_RED_ONE_SIDED_PREDICATE" for f in findings_on), (
        f"expected a record with the gate enabled, got {findings_on!r}"
    )

    monkeypatch.setenv("HAL_ONE_SIDED_PREDICATE_GATE", "0")
    captured: list = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture):
        findings_off = p5._collect_red_lint_findings([str(dirty.resolve())], str(tmp_path), ctx, cfg)

    assert not [
        f for f in findings_off if f.get("error_code") == "E_RED_ONE_SIDED_PREDICATE"
    ], f"expected no record with the gate disabled, got {findings_off!r}"
    assert [
        c for c in captured
        if c[0] == "gate_disabled" and c[1].get("gate") == "HAL_ONE_SIDED_PREDICATE_GATE"
    ], f"expected gate_disabled(HAL_ONE_SIDED_PREDICATE_GATE), got {captured!r}"


def test_ac28_suffix_restriction_py_not_scanned_ts_scanned(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    ctx = _make_ctx(tmp_path)
    cfg = ctx.org_config or {}
    body = _block("  expect(r.code).not.toBe(0);")

    ts_file = tmp_path / "bd166_dirty.test.ts"
    ts_file.write_text(body, encoding="utf-8")
    findings_ts = p5._collect_red_lint_findings([str(ts_file.resolve())], str(tmp_path), ctx, cfg)
    assert [
        f for f in findings_ts if f.get("error_code") == "E_RED_ONE_SIDED_PREDICATE"
    ], f".test.ts path must be scanned, got {findings_ts!r}"

    py_file = tmp_path / "bd166_dirty.py"
    py_file.write_text(body, encoding="utf-8")
    findings_py = p5._collect_red_lint_findings([str(py_file.resolve())], str(tmp_path), ctx, cfg)
    assert not [
        f for f in findings_py if f.get("error_code") == "E_RED_ONE_SIDED_PREDICATE"
    ], f".py path must be excluded, got {findings_py!r}"


# ------------------------------------------------- legacy path (AC30/AC35)


def test_ac30_legacy_red_lint_path_reports_one_sided_predicate(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    ctx = _make_ctx(tmp_path)

    dirty_rel = _write_test_file(
        tmp_path, "tests/bd166_legacy_dirty.test.ts", _block("  expect(r.code).not.toBe(0);"),
    )
    result_dirty = p5._verify_red_lint_rules(ctx, _make_legacy_prev([dirty_rel]))
    assert result_dirty.error_code == "E_RED_ONE_SIDED_PREDICATE", (
        f"got {result_dirty.error_code!r} (status={result_dirty.status!r})"
    )
    assert result_dirty.status == "error" and result_dirty.recoverable is True

    clean_rel = _write_test_file(
        tmp_path, "tests/bd166_legacy_clean.test.ts",
        _block("  expect(r.code).not.toBe(0);\n  expect(r.code).toBe(9);"),
    )
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    result_clean = p5._verify_red_lint_rules(ctx, _make_legacy_prev([clean_rel]))
    assert result_clean.error_code == "E_RED_LINT_SEMGREP_MISSING", (
        f"clean file must fall through to semgrep-missing (PATH empty), got "
        f"{result_clean.error_code!r} (status={result_clean.status!r})"
    )


def test_ac34_unreadable_red_target_is_a_finding_not_a_silent_pass(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    ctx = _make_ctx(tmp_path)
    cfg = ctx.org_config or {}

    rel = _unreadable_ts_target(tmp_path, "bd166_unreadable.test.ts")
    captured: list = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture):
        unreadable_findings = p5._collect_red_lint_findings(
            [str(tmp_path / rel)], str(tmp_path), ctx, cfg,
        )
    unreadable_events = [c for c in captured if c[0] == "red_one_sided_predicate_unreadable_target"]
    assert len(unreadable_events) == 1, f"expected exactly one unreadable event, got {captured!r}"
    matches = [
        f for f in unreadable_findings if f.get("error_code") == "E_RED_LINT_TARGET_UNREADABLE"
    ]
    assert matches, f"unreadable target must produce a record, got {unreadable_findings!r}"
    assert matches[0].get("rule") == "one-sided-predicate-unreadable", f"got {matches[0]!r}"
    assert matches[0].get("recoverable") is True, f"got {matches[0]!r}"

    readable = tmp_path / "bd166_readable.test.ts"
    readable.write_text(
        _block("  expect(r.code).not.toBe(0);\n  expect(r.code).toBe(9);"), encoding="utf-8",
    )
    clean_findings = p5._collect_red_lint_findings([str(readable.resolve())], str(tmp_path), ctx, cfg)
    assert not [
        f for f in clean_findings
        if f.get("error_code") in ("E_RED_LINT_TARGET_UNREADABLE", "E_RED_ONE_SIDED_PREDICATE")
    ], f"readable controlled file must stay clean, got {clean_findings!r}"


def test_ac35_legacy_path_reports_unreadable_target_distinctly(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    ctx = _make_ctx(tmp_path)

    rel = _unreadable_ts_target(tmp_path, "tests/bd166_legacy_unreadable.test.ts")
    result = p5._verify_red_lint_rules(ctx, _make_legacy_prev([rel]))
    assert result.error_code == "E_RED_LINT_TARGET_UNREADABLE", (
        f"got {result.error_code!r} (status={result.status!r})"
    )

    clean_rel = _write_test_file(
        tmp_path, "tests/bd166_legacy_readable.test.ts",
        _block("  expect(r.code).not.toBe(0);\n  expect(r.code).toBe(9);"),
    )
    empty_bin = tmp_path / "empty_bin_ac35"
    empty_bin.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty_bin))
    clean = p5._verify_red_lint_rules(ctx, _make_legacy_prev([clean_rel]))
    assert clean.error_code == "E_RED_LINT_SEMGREP_MISSING", (
        f"readable clean target must fall through to semgrep terminal, got {clean.error_code!r}"
    )


# ------------------------------------------------------ AC25 / AC36 / AC37


@pytest.mark.parametrize("code", ["E_RED_ONE_SIDED_PREDICATE", "E_RED_LINT_TARGET_UNREADABLE"])
def test_ac25_error_code_registered_everywhere(code):
    assert code in error_codes.ERROR_CODES, f"{code} missing from ERROR_CODES"
    for md in (
        _ENGINE_PY / "ERROR_CODES.md",
        _ENGINE_PY / "bytedigger_engine" / "ERROR_CODES.md",
    ):
        assert code in md.read_text(encoding="utf-8"), f"{code} missing from {md}"
    rendered = error_codes.render_markdown().encode("utf-8")
    for md in (
        _ENGINE_PY / "ERROR_CODES.md",
        _ENGINE_PY / "bytedigger_engine" / "ERROR_CODES.md",
    ):
        assert md.read_bytes() == rendered, f"{md} is not byte-identical to render_markdown()"
    assert error_codes.main(["--check"]) == 0, (
        "harvested E_* codes in the source are not all in the registry (or vice versa)"
    )


def test_ac25_flag_registered_and_names_bd166():
    assert "HAL_ONE_SIDED_PREDICATE_GATE" in flags_catalog.FLAGS
    desc = flags_catalog.FLAGS["HAL_ONE_SIDED_PREDICATE_GATE"].get("description", "")
    assert "bd#166" in desc, f"flag description must name bd#166, got {desc!r}"


def test_ac36_declared_suffix_corpus():
    from bytedigger_engine import one_sided_predicate
    from bytedigger_engine.workflows import phase_5_implement as p5

    expected = (".test.ts", ".test.js", ".spec.ts", ".spec.js")
    assert tuple(one_sided_predicate.TS_TEST_SUFFIXES) == expected
    assert tuple(p5._ONE_SIDED_PREDICATE_TS_SUFFIXES) == expected
    for name in ("a.test.ts", "a.test.js", "a.spec.ts", "a.spec.js"):
        assert name.endswith(tuple(p5._ONE_SIDED_PREDICATE_TS_SUFFIXES)), name
    assert not "a.test.mts".endswith(tuple(p5._ONE_SIDED_PREDICATE_TS_SUFFIXES))


def test_ac37_path_outside_declared_corpus_is_emitted_not_silent(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    odd = tmp_path / "bd166_oddly_named.mts"
    odd.write_text(_block("  expect(r.code).not.toBe(0);"), encoding="utf-8")

    captured: list = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture):
        p5._one_sided_predicate_hits([str(odd.resolve())])
    unmatched = [c for c in captured if c[0] == "red_one_sided_predicate_unmatched_path"]
    assert unmatched, f"out-of-corpus path must be emitted, got {captured!r}"
    assert unmatched[0][1].get("unmatched_n") == 1, f"got {unmatched[0]!r}"

    py_path = tmp_path / "test_bd166_notapplicable.py"
    py_path.write_text("def test_x():\n    pass\n", encoding="utf-8")
    captured.clear()
    with patch.object(p5, "_emit_safe", side_effect=_capture):
        p5._one_sided_predicate_hits([str(py_path.resolve())])
    assert not [
        c for c in captured if c[0] == "red_one_sided_predicate_unmatched_path"
    ], f".py is the known-zero corpus and must not be reported, got {captured!r}"


# ---------------------------------------------------- B5 / B6 / B7


def test_b5_d1_decoys_are_pinned_as_the_chosen_behaviour():
    # false positive: an unrelated camel-hump subject becomes a candidate
    fp = _scan(_block("  expect(zipCode).not.toBeNull();"))
    assert len(fp) == 1, f"zipCode decoy must be flagged (declared D1 behaviour), got {fp!r}"

    # false negative: an unrelated codey toBe(<int>) controls a real predicate
    fn_text = _block("  expect(r.code).not.toBe(0);\n  expect(user.zipCode).toBe(90210);")
    assert _scan(fn_text) == [], f"decoy control must silence (declared D1), got {_scan(fn_text)!r}"
    assert _summ(fn_text)["controlled"] == 1, f"got {_summ(fn_text)!r}"


def test_b6_cli_imports_the_rule_and_holds_no_copy():
    assert _DRIVER.is_file(), f"missing driver {_DRIVER}"
    tree = ast.parse(_DRIVER.read_text(encoding="utf-8"))
    imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "bytedigger_engine.one_sided_predicate":
            if any(a.name == "scan_one_sided_predicates" for a in node.names):
                imported = True
    assert imported, "driver must import scan_one_sided_predicates from the engine module"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "re" for a in node.names), "driver must not import re"
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "re", "driver must not import from re"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
                raise AssertionError("driver must not call re.*")


def test_b7_hits_helper_calls_no_llm_or_subprocess_path():
    tree = ast.parse(_PHASE5_SRC.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_one_sided_predicate_hits"),
        None,
    )
    assert fn is not None, "phase_5_implement._one_sided_predicate_hits does not exist"
    banned_names = {"invoke_llm_subprocess", "bounded_run"}
    banned_roots = {"check_ladder", "subprocess", "llm_subprocess"}
    offenders = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in banned_names:
            offenders.append(f.id)
        elif isinstance(f, ast.Attribute):
            if f.attr in banned_names:
                offenders.append(f.attr)
            root = f
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in banned_roots:
                offenders.append(root.id)
    assert offenders == [], f"helper must stay deterministic, found calls: {offenders!r}"


# ----------------------------------------------- AC18 / AC19 / AC20 (precommit)


def test_ac18_is_ts_test_file_both_directions():
    for p in ["x.test.ts", "x.test.js", "x.spec.ts", "some/dir/x.spec.js"]:
        assert precommit_lints.is_ts_test_file(p) is True, p
    for p in ["test_x.py", "x.ts", "README.md", "x_test.py"]:
        assert precommit_lints.is_ts_test_file(p) is False, p


def test_ac19_classify_staged_routes_ts_tests_separately():
    c = precommit_lints.classify_staged(["bd166.test.ts", "test_bd166.py", "README.md"])

    assert "bd166.test.ts" in c["ts_tests"], f"got {c!r}"
    assert "test_bd166.py" in c["tests"], f"got {c!r}"
    assert "README.md" not in c["ts_tests"] and "README.md" not in c["tests"], f"got {c!r}"


def test_ac20_build_lint_commands_ts_tests_argv_is_the_real_root_driver():
    old_cmds = precommit_lints.build_lint_commands(["s.md"], ["test_t.py"], precommit_lints.DEFAULT_LINT_DIR)
    assert not [c for c in old_cmds if c["lint"] == "one-sided-predicate-lint"], (
        f"call without ts_tests must not plan TS lints, got {old_cmds!r}"
    )

    new_cmds = precommit_lints.build_lint_commands(
        ["s.md"], ["test_t.py"], precommit_lints.DEFAULT_LINT_DIR, ts_tests=["x.test.ts"],
    )
    mine = [c for c in new_cmds if c["lint"] == "one-sided-predicate-lint"]
    assert len(mine) == 1, f"expected exactly one TS-lint command, got {mine!r}"
    assert "x.test.ts" in mine[0]["argv"] and "--spec" not in mine[0]["argv"], f"got {mine[0]['argv']!r}"
    drv = Path(mine[0]["argv"][0])
    assert drv.is_file(), f"argv[0] must be a real file, got {drv}"
    assert drv.resolve() == _DRIVER.resolve(), f"argv[0] must be the repo-root driver, got {drv}"


# ------------------------------------------------------------------ AC27 (bd)


def _hermetic_git_env(tmp_path: Path) -> dict:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    empty_global = fake_home / ".gitconfig"
    empty_global.write_text("")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("BD66_LINT_DIR", None)
    env["HOME"] = str(fake_home)
    env["GIT_CONFIG_GLOBAL"] = str(empty_global)
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args, cwd: Path, env: dict):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL,
    )


def _staged_repo(tmp_path: Path, name: str, content: str):
    env = _hermetic_git_env(tmp_path)
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo, env).returncode == 0
    for key, value in (("user.email", "bd166@example.com"), ("user.name", "bd166"),
                       ("commit.gpgsign", "false")):
        assert _git(["config", key, value], repo, env).returncode == 0
    (repo / "x.test.ts").write_text(content, encoding="utf-8")
    assert _git(["add", "x.test.ts"], repo, env).returncode == 0
    return repo, env


def _run_enforce(repo: Path, env: dict):
    proc = subprocess.run(
        [sys.executable, str(_ENFORCE_CLI)], cwd=str(repo), env=env,
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines()
             if "BD66-REFUSE-VIOLATION" in ln and "lint=one-sided-predicate-lint" in ln]
    return proc, lines


def test_ac27_nothing_to_lint_pair():
    assert precommit_lints.nothing_to_lint(
        {"specs": [], "tests": [], "ts_tests": ["x.test.ts"]}
    ) is False
    assert precommit_lints.nothing_to_lint({"specs": [], "tests": [], "ts_tests": []}) is True


def test_ac27_staged_one_sided_ts_test_is_refused_end_to_end(tmp_path):
    repo, env = _staged_repo(tmp_path, "dirty_repo", _block("  expect(r.code).not.toBe(0);"))

    proc, lines = _run_enforce(repo, env)

    assert proc.returncode != 0, f"one-sided TS test must be refused; out={proc.stdout!r} err={proc.stderr!r}"
    assert len(lines) == 1, f"expected ONE refusal line naming the lint, got {lines!r}; out={proc.stdout!r}"


def test_ac27_control_side_is_not_refused(tmp_path):
    repo, env = _staged_repo(
        tmp_path, "clean_repo",
        _block("  expect(r.code).not.toBe(0);\n  expect(r.code).toBe(9);"),
    )

    proc, lines = _run_enforce(repo, env)

    assert lines == [], f"controlled file must not be refused by the lint, got {lines!r}"
    assert proc.returncode == 0, f"controlled file must commit, rc={proc.returncode} out={proc.stdout!r}"


def test_ac27_driver_is_executable_in_git_and_has_shebang():
    assert _DRIVER.is_file(), f"missing driver {_DRIVER}"
    assert _DRIVER.read_text(encoding="utf-8").splitlines()[0].startswith("#!"), "driver needs a shebang"
    assert os.stat(_DRIVER).st_mode & 0o111, "driver must have an exec bit in the working tree"

    if not (_REPO_ROOT / ".git").exists():
        pytest.skip(
            "bd#166 AC27: this corpus is a `git archive` tree with no .git, so the "
            "versioned index mode cannot be read; the working-tree exec bit and "
            "shebang were asserted above."
        )
    listed = subprocess.run(
        ["git", "ls-files", "-s", "one-sided-predicate-lint.py"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert listed.returncode == 0, f"git ls-files failed: {listed.stderr!r}"
    assert listed.stdout.startswith("100755 "), f"driver must be versioned as 100755, got {listed.stdout!r}"


# ------------------------------------------------------------ AC-L1 / AC-L2


def test_acl1_legacy_kill_switch_emits_gate_disabled_and_falls_through(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    ctx = _make_ctx(tmp_path)
    rel = _write_test_file(
        tmp_path, "tests/bd166_l1_dirty.test.ts", _block("  expect(r.code).not.toBe(0);"),
    )

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    on = p5._verify_red_lint_rules(ctx, _make_legacy_prev([rel]))
    assert on.error_code == "E_RED_ONE_SIDED_PREDICATE", f"control (gate on) got {on.error_code!r}"

    monkeypatch.setenv("HAL_ONE_SIDED_PREDICATE_GATE", "0")
    empty_bin = tmp_path / "empty_bin_l1"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    captured: list = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    with patch.object(p5, "_emit_safe", side_effect=_capture):
        off = p5._verify_red_lint_rules(ctx, _make_legacy_prev([rel]))

    assert [
        c for c in captured
        if c[0] == "gate_disabled" and c[1].get("gate") == "HAL_ONE_SIDED_PREDICATE_GATE"
    ], f"expected gate_disabled(HAL_ONE_SIDED_PREDICATE_GATE), got {captured!r}"
    assert off.error_code != "E_RED_ONE_SIDED_PREDICATE", f"gate off must not report it, got {off.error_code!r}"
    assert off.error_code == "E_RED_LINT_SEMGREP_MISSING", f"must fall through, got {off.error_code!r}"


def test_acl2_legacy_unreadable_wins_over_a_finding(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    monkeypatch.setenv("HAL_RED_LINT_PREFLIGHT_BATCH", "0")
    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    ctx = _make_ctx(tmp_path)

    dirty_rel = _write_test_file(
        tmp_path, "tests/bd166_l2_dirty.test.ts", _block("  expect(r.code).not.toBe(0);"),
    )
    unreadable_rel = _unreadable_ts_target(tmp_path, "tests/bd166_l2_unreadable.test.ts")

    alone = p5._verify_red_lint_rules(ctx, _make_legacy_prev([dirty_rel]))
    assert alone.error_code == "E_RED_ONE_SIDED_PREDICATE", f"dirty alone got {alone.error_code!r}"

    mixed = p5._verify_red_lint_rules(ctx, _make_legacy_prev([dirty_rel, unreadable_rel]))
    assert mixed.error_code == "E_RED_LINT_TARGET_UNREADABLE", (
        f"unreadable must win over a finding, got {mixed.error_code!r}"
    )


# ------------------------------------------------------ T-B / T-C


def _finding_keys(findings):
    return sorted(
        (f.get("error_code"), f.get("rule"), f.get("line"))
        for f in findings
        if f.get("error_code") == "E_RED_ONE_SIDED_PREDICATE"
    )


@pytest.mark.parametrize("backend", ["claude-subprocess", "anthropic-api"])
def test_tb_result_identical_under_each_runner_backend(tmp_path, monkeypatch, backend):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    ctx = _make_ctx(tmp_path)
    cfg = ctx.org_config or {}
    dirty = tmp_path / "bd166_tb.test.ts"
    dirty.write_text(_block("  expect(r.code).not.toBe(0);"), encoding="utf-8")

    baseline = _finding_keys(
        p5._collect_red_lint_findings([str(dirty.resolve())], str(tmp_path), ctx, cfg)
    )
    assert len(baseline) == 1, f"baseline must carry the record (non-vacuous), got {baseline!r}"

    monkeypatch.setenv("HAL_RUNNER_BACKEND", backend)
    got = _finding_keys(
        p5._collect_red_lint_findings([str(dirty.resolve())], str(tmp_path), ctx, cfg)
    )
    assert got == baseline, f"backend {backend} changed the result: {got!r} vs {baseline!r}"


def test_tc_provider_down_does_not_change_the_result(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.delenv("HAL_ONE_SIDED_PREDICATE_GATE", raising=False)

    def _down(*_a, **_k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _down)
    ctx = _make_ctx(tmp_path)
    cfg = ctx.org_config or {}

    dirty = tmp_path / "bd166_tc_dirty.test.ts"
    dirty.write_text(_block("  expect(r.code).not.toBe(0);"), encoding="utf-8")
    findings = p5._collect_red_lint_findings([str(dirty.resolve())], str(tmp_path), ctx, cfg)
    assert len(_finding_keys(findings)) == 1, f"dirty must still yield the record, got {findings!r}"

    clean = tmp_path / "bd166_tc_clean.test.ts"
    clean.write_text(
        _block("  expect(r.code).not.toBe(0);\n  expect(r.code).toBe(9);"), encoding="utf-8",
    )
    findings_clean = p5._collect_red_lint_findings([str(clean.resolve())], str(tmp_path), ctx, cfg)
    assert _finding_keys(findings_clean) == [], f"clean must yield none, got {findings_clean!r}"
