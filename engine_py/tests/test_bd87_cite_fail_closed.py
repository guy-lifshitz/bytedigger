"""RED tests for bd#87 (P6) — cite-lint fails closed when it is blind, and
reads the symbols a spec introduces.

Spec: docs/decisions/2026-09-26-bd87-cite-lint-fail-closed.md §4 AC1-AC17.

`spec_cite` and `phase_45_spec` exist today and are imported at module
level. The NEW names (`declared_introduced_symbols`, `BLOCKING_STATUSES`,
`_cite_finding_evidence`) are reached only inside test bodies, so the file
collects cleanly and fails at run time.

Hermeticity: `_repo_symbol_index` walks the whole `repo_root`, and a symbol
found anywhere under it becomes `wrong_file` instead of `unresolved_symbol`.
Every `lint_spec` test therefore builds its own fixture repo under
`tmp_path/"repo"` and writes the spec outside it.
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import patch

from bytedigger_engine import spec_cite
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.workflows import phase_45_spec

_FIXTURE_FILE = "mod.py"
_FIXTURE_CONTENT = "def existing_helper():\n    pass\n"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / _FIXTURE_FILE).write_text(_FIXTURE_CONTENT, encoding="utf-8")
    return repo


def _lint(tmp_path: Path, spec_text: str) -> tuple[int, list[spec_cite.Finding]]:
    repo = _repo(tmp_path)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text, encoding="utf-8")
    return spec_cite.lint_spec(spec_path, repo)


def _statuses(findings: list[spec_cite.Finding], symbol: str) -> set[str]:
    return {f.status for f in findings if f.symbol == symbol}


# ── op1/op2: declarative introduced-symbol allowlist ─────────────────────


def test_ac1_section_declared_symbol_is_new_symbol(tmp_path: Path) -> None:
    spec = (
        "# Spec\n"
        "## Symbols this spec INTRODUCES\n"
        "- `JOB_STATUS_COMPLETED`\n"
        "## Design\n"
        "The step sets `JOB_STATUS_COMPLETED` in `mod.py` on success.\n"
    )
    rc, findings = _lint(tmp_path, spec)
    assert rc == 0, f"AC1: expected exit 0, got {rc}; {findings!r}"
    assert _statuses(findings, "JOB_STATUS_COMPLETED") == {"new_symbol"}, findings


def test_ac2_typo_after_section_still_blocks(tmp_path: Path) -> None:
    spec = (
        "# Spec\n"
        "## Symbols this spec INTRODUCES\n"
        "- `JOB_STATUS_COMPLETED`\n"
        "## Design\n"
        "The step sets `JOB_STATUS_COMPLETED` in `mod.py` on success.\n"
        "It then calls `existing_helpr` in `mod.py`.\n"
    )
    rc, findings = _lint(tmp_path, spec)
    assert rc == 1, f"AC2: expected exit 1, got {rc}; {findings!r}"
    assert _statuses(findings, "existing_helpr") == {"unresolved_symbol"}, findings
    assert _statuses(findings, "JOB_STATUS_COMPLETED") == {"new_symbol"}, findings


def test_ac3_line_form_declares(tmp_path: Path) -> None:
    spec = (
        "- INTRODUCES: `SOME_NEW_FLAG`\n"
        "The guard reads `SOME_NEW_FLAG` in `mod.py`.\n"
    )
    rc, findings = _lint(tmp_path, spec)
    assert rc == 0, f"AC3: expected exit 0, got {rc}; {findings!r}"
    assert _statuses(findings, "SOME_NEW_FLAG") == {"new_symbol"}, findings


def test_ac4_line_form_inside_markdown_fence_does_not_declare(tmp_path: Path) -> None:
    spec = (
        "```markdown\n"
        "- INTRODUCES: `FENCED_LINE_TOKEN`\n"
        "```\n"
        "The guard reads `FENCED_LINE_TOKEN` in `mod.py`.\n"
    )
    assert "FENCED_LINE_TOKEN" not in spec_cite.declared_introduced_symbols(spec)
    rc, findings = _lint(tmp_path, spec)
    assert rc == 1, f"AC4: expected exit 1, got {rc}; {findings!r}"
    assert _statuses(findings, "FENCED_LINE_TOKEN") == {"unresolved_symbol"}, findings


def test_ac4b_section_inside_markdown_fence_does_not_declare() -> None:
    spec = (
        "```markdown\n"
        "## Symbols this spec INTRODUCES\n"
        "- `FENCED_SECTION_TOKEN`\n"
        "```\n"
    )
    assert "FENCED_SECTION_TOKEN" not in spec_cite.declared_introduced_symbols(spec)


def test_ac5_citation_line_in_last_section_is_not_a_declaration(tmp_path: Path) -> None:
    spec = (
        "# Spec\n"
        "## Symbols this spec INTRODUCES\n"
        "- `JOB_STATUS_COMPLETED`\n"
        "- the step then calls `existing_helpr` in `mod.py`\n"
    )
    introduced = spec_cite.declared_introduced_symbols(spec)
    assert "existing_helpr" not in introduced, introduced
    assert "JOB_STATUS_COMPLETED" in introduced, introduced
    rc, findings = _lint(tmp_path, spec)
    assert rc == 1, f"AC5: expected exit 1, got {rc}; {findings!r}"
    assert _statuses(findings, "existing_helpr") == {"unresolved_symbol"}, findings


def test_ac6_signature_form_declares_leading_identifier(tmp_path: Path) -> None:
    spec = (
        "- INTRODUCES: `make_job(cfg)`\n"
        "The runner calls `make_job` in `mod.py`.\n"
    )
    rc, findings = _lint(tmp_path, spec)
    assert rc == 0, f"AC6: expected exit 0, got {rc}; {findings!r}"
    assert _statuses(findings, "make_job") == {"new_symbol"}, findings


# ── op3: blindness and invented paths block ──────────────────────────────


def test_ac7_spec_wrapped_in_markdown_fence_is_no_citations(tmp_path: Path) -> None:
    spec = (
        "```markdown\n"
        "# Spec\n"
        "The step calls `existing_helper` in `mod.py`.\n"
        "```\n"
    )
    rc, findings = _lint(tmp_path, spec)
    assert rc == 1, f"AC7: expected exit 1, got {rc}; {findings!r}"
    assert [f.status for f in findings] == ["no_citations"], findings


def test_ac8_prose_spec_without_citations_blocks(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "# Spec\nMake the thing faster.\n")
    assert rc == 1, f"AC8: expected exit 1, got {rc}; {findings!r}"
    assert [f.status for f in findings] == ["no_citations"], findings


def test_ac9_blank_spec_is_not_this_gates_concern(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "  \n\n")
    assert (rc, findings) == (0, []), f"AC9: got {rc}; {findings!r}"


def test_ac10_invented_path_blocks(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "The step calls `existing_helper` in `nothere/ghost.py`.\n")
    assert rc == 1, f"AC10: expected exit 1, got {rc}; {findings!r}"
    assert _statuses(findings, "existing_helper") == {"missing_file"}, findings


def test_ac11_create_declared_path_is_planned(tmp_path: Path) -> None:
    for i, decl in enumerate(("CREATE: pkg/new_mod.py", "CREATE:pkg/new_mod.py")):
        case = tmp_path / str(i)
        case.mkdir()
        spec = f"{decl}\nThe step calls `existing_helper` in `pkg/new_mod.py`.\n"
        rc, findings = _lint(case, spec)
        assert rc == 0, f"AC11 {decl!r}: expected exit 0, got {rc}; {findings!r}"
        assert _statuses(findings, "existing_helper") == {"planned_file"}, findings


def test_ac12_colonless_create_line_declares(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "CREATE `pkg/new_mod.py` with `new_fn`\n")
    assert rc == 0, f"AC12: expected exit 0, got {rc}; {findings!r}"
    assert _statuses(findings, "new_fn") == {"planned_file"}, findings


def test_ac13_symbol_first_create_line_does_not_declare(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "CREATE `new_fn` in `pkg/new_mod.py` for the pipeline.\n")
    assert rc == 1, f"AC13: expected exit 1, got {rc}; {findings!r}"
    assert _statuses(findings, "new_fn") == {"missing_file"}, findings


def test_ac13b_lowercase_prose_create_does_not_declare(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "Create `pkg/new_mod.py` with `new_fn`\n")
    assert rc == 1, f"AC13b: expected exit 1, got {rc}; {findings!r}"
    assert _statuses(findings, "new_fn") == {"missing_file"}, findings


def test_ac20_snippet_only_spec_is_not_blind(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, 'The helper is defined at mod.py:"def existing_helper".\n')
    assert (rc, findings) == (0, []), f"AC20: got {rc}; {findings!r}"


def test_ac14_blocking_statuses_are_public() -> None:
    assert spec_cite.BLOCKING_STATUSES == frozenset(
        {"unresolved_symbol", "missing_file", "no_citations"}
    )


# ── op4: phase_45 consumers ──────────────────────────────────────────────


def test_ac15_parse_keeps_every_blocking_status() -> None:
    statuses = [
        "resolved", "unresolved_symbol", "missing_file", "no_citations",
        "new_symbol", "planned_file", "wrong_file",
    ]
    stdout = json.dumps({
        "findings": [{"file": "a.py", "symbol": "s", "status": s} for s in statuses],
    })
    kept = sorted(f["status"] for f in phase_45_spec._parse_cite_blocking(stdout))
    assert kept == ["missing_file", "no_citations", "unresolved_symbol"], kept


def test_ac16_evidence_text_per_status() -> None:
    evidence = phase_45_spec._cite_finding_evidence
    no_cit = evidence({"file": "", "symbol": "", "status": "no_citations"})
    assert "no citations" in no_cit.lower(), no_cit
    missing = evidence({"file": "nothere/ghost.py", "symbol": "f", "status": "missing_file"})
    assert "nothere/ghost.py" in missing and "CREATE" in missing, missing
    unresolved = evidence({"file": "mod.py", "symbol": "f", "status": "unresolved_symbol"})
    assert unresolved.startswith("unresolved citation: symbol 'f'"), unresolved


def test_ac17_prompt_names_the_introduces_declaration() -> None:
    contract = phase_45_spec._grounded_citation_contract()
    assert "Symbols this spec INTRODUCES" in contract
    assert "INTRODUCES:" in contract
    assert "  9. A NEW symbol" in contract


class _FakeCtx:
    def __init__(self, org_config: dict | None = None) -> None:
        self.org_config = org_config or {}


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _prev(spec_path: Path) -> StepResult:
    return StepResult(
        status="ok", data={"cycle": 1, "spec_path": str(spec_path)},
        duration_ms=0, step_name="verify_spec_lint",
    )


def test_ac18_preflight_evidence_names_no_citations(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    cite_json = json.dumps(
        {"findings": [{"file": "", "symbol": "", "status": "no_citations"}]}
    )

    def _fake(cmd, **kwargs):
        if "spec-cite-lint" in " ".join(str(c) for c in cmd):
            return _FakeProc(1, cite_json)
        return _FakeProc(0)

    ctx = _FakeCtx()
    with patch.object(phase_45_spec, "bounded_run", side_effect=_fake):
        findings = phase_45_spec._collect_spec_gate_findings(
            ctx, _prev(spec_path), str(spec_path), str(tmp_path), ctx.org_config, str(tmp_path),
        )
    cite = [f for f in findings if f["rule"] == "spec-cite-lint"]
    assert len(cite) == 1, findings
    assert "no citations" in cite[0]["evidence"].lower(), cite


def test_ac19_cite_gate_repairs_and_reports_missing_file(tmp_path: Path, monkeypatch) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    cite_json = json.dumps(
        {"findings": [{"file": "nothere/ghost.py", "symbol": "f", "status": "missing_file"}]}
    )
    real_is_file = Path.is_file
    monkeypatch.setattr(
        phase_45_spec.Path, "is_file",
        lambda self: self.name == "spec-cite-lint.py" or real_is_file(self),
    )
    emitted: list[tuple] = []
    monkeypatch.setattr(
        phase_45_spec, "_emit_safe",
        lambda event, payload, *a, **k: emitted.append((event, payload)),
    )
    repair_calls: list[dict] = []

    def _fake_repair(**kwargs):
        repair_calls.append(kwargs)
        return types.SimpleNamespace(converged=False, final=None)

    monkeypatch.setattr(phase_45_spec, "_directed_repair_enabled", lambda ctx: True)
    monkeypatch.setattr(phase_45_spec, "attempt_directed_repair", _fake_repair)
    with patch.object(phase_45_spec, "bounded_run", return_value=_FakeProc(1, cite_json)):
        result = phase_45_spec._verify_spec_cite_lint(_FakeCtx(), _prev(spec_path))

    assert result.error_code == "E_SPEC_CITE_LINT_FAIL", result
    assert "nothere/ghost.py" in (result.error or ""), result.error
    assert len(repair_calls) == 1 and repair_calls[0]["findings"], repair_calls
    assert "nothere/ghost.py" in repair_calls[0]["findings"][0]["evidence"], repair_calls
    advisory = [p for e, p in emitted if e == "spec_cite_advisory"]
    assert advisory and "no_citations" in advisory[0], emitted


def test_ac21_path_only_mention_is_blind(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "Modify `engine/fake_mod.py` to add retry.\nCREATE: pkg/x.py\n")
    assert rc == 1, f"AC21: expected exit 1, got {rc}; {findings!r}"
    assert [f.status for f in findings] == ["no_citations"], findings


def test_ac22_preflight_unparseable_rc1_still_blocks(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")

    def _fake(cmd, **kwargs):
        if "spec-cite-lint" in " ".join(str(c) for c in cmd):
            return _FakeProc(1, "not json")
        return _FakeProc(0)

    ctx = _FakeCtx()
    with patch.object(phase_45_spec, "bounded_run", side_effect=_fake):
        findings = phase_45_spec._collect_spec_gate_findings(
            ctx, _prev(spec_path), str(spec_path), str(tmp_path), ctx.org_config, str(tmp_path),
        )
    cite = [f for f in findings if f["rule"] == "spec-cite-lint"]
    assert len(cite) == 1 and cite[0]["error_code"] == "E_SPEC_CITE_LINT_FAIL", findings


def test_ac16b_unknown_status_is_not_called_unresolved() -> None:
    text = phase_45_spec._cite_finding_evidence({"file": "a.py", "symbol": "s", "status": "weird"})
    assert "unresolved" not in text and "weird" in text, text


def test_ac23_other_language_target_is_not_blind(tmp_path: Path) -> None:
    rc, findings = _lint(tmp_path, "Change `Handler` in `server/main.go` to retry.\n")
    assert (rc, findings) == (0, []), f"AC23: got {rc}; {findings!r}"


def test_ac24_product_names_and_urls_are_not_cited_files(tmp_path: Path) -> None:
    spec = (
        "Like Node.js, call `existing_helper` in `mod.py`.\n"
        "See https://example.com/app.js for `existing_helper` in `mod.py`.\n"
    )
    rc, findings = _lint(tmp_path, spec)
    assert rc == 0, f"AC24: expected exit 0, got {rc}; {findings!r}"
    assert {f.file for f in findings} == {"mod.py"}, findings


def test_ac25_fenced_create_line_does_not_declare(tmp_path: Path) -> None:
    spec = (
        "```markdown\n"
        "CREATE: pkg/new.py\n"
        "```\n"
        "Use `existing_helper` in `pkg/new.py`.\n"
    )
    rc, findings = _lint(tmp_path, spec)
    assert rc == 1, f"AC25: expected exit 1, got {rc}; {findings!r}"
    assert _statuses(findings, "existing_helper") == {"missing_file"}, findings


def test_ac26_rule_10_examples_parse_as_declarations() -> None:
    contract = phase_45_spec._grounded_citation_contract()
    lines = [ln.strip() for ln in contract.splitlines()]
    assert "INTRODUCES: `symbol`" in lines, contract
    assert "CREATE: path/to/file.py" in lines, contract
    assert spec_cite.declared_introduced_symbols("INTRODUCES: `symbol`\n") == {"symbol"}
    assert spec_cite.declared_created_files("CREATE: path/to/file.py\n") == {"path/to/file.py"}


def test_ac27_one_missing_file_is_one_repair_finding(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    cite_json = json.dumps({"findings": [
        {"file": "nope.py", "symbol": s, "status": "missing_file"} for s in ("a", "b", "c")
    ]})

    def _fake(cmd, **kwargs):
        if "spec-cite-lint" in " ".join(str(c) for c in cmd):
            return _FakeProc(1, cite_json)
        return _FakeProc(0)

    ctx = _FakeCtx()
    with patch.object(phase_45_spec, "bounded_run", side_effect=_fake):
        findings = phase_45_spec._collect_spec_gate_findings(
            ctx, _prev(spec_path), str(spec_path), str(tmp_path), ctx.org_config, str(tmp_path),
        )
    assert len([f for f in findings if f["rule"] == "spec-cite-lint"]) == 1, findings
