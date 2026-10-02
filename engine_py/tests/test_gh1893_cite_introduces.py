"""RED tests for GH1893 — cite-lint rejects symbols a spec introduces itself.

Spec: SHARED/memory/Decisions/gh1893_cite_lint_introduces_spec.md

`declared_introduced_symbols()` does not yet exist on `spec_cite`. Once it
does (op1), `lint_spec` (op2) must consult it as a THIRD downgrade source
alongside `planned_symbols()` / `declared_symbols()` / `new_marked_symbols()`,
and `_verify_spec_citations` (op3) must treat a citation to a `CREATE:`-line
declared, repo-rooted target as WARNING instead of a fabricated-citation
ERROR (never a bare/escaping path — that is the lot 1896 lock, AC9/AC12).

`spec_cite` and `phase_45_spec` are imported at module level (both modules
exist today — engine_py/ (package parent) and tests/ are already on sys.path via
conftest.py's import-time singleton, §1q / 81F97F3D). The NOT-YET-EXISTING
`declared_introduced_symbols` is reached only INSIDE each test body as
`spec_cite.declared_introduced_symbols(...)`, so every op1/op2 test fails at
assert/AttributeError time during the run, never at collection.

§1i HERMETICITY: `_repo_symbol_index` walks the ENTIRE repo_root passed to
`lint_spec`, including this very tests/ dir. Every AC1-AC5/AC8/AC11 test
therefore builds its OWN tiny fixture repo under `tmp_path` via
`_make_fixture_repo()` and passes THAT as `repo_root` — never the real
checkout — so invented fixture symbols cannot get intercepted by the GH796
`wrong_file` branch (spec_cite.py:498-500) before reaching `unresolved_symbol`.
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine import spec_cite
from bytedigger_engine.workflows.phase_45_spec import _verify_spec_citations, _grounded_citation_contract
from bytedigger_engine.contracts import StepResult, WorkflowContext

_FIXTURE_FILE = "mod.py"
_FIXTURE_CONTENT = "def existing_helper():\n    pass\n"


def _make_fixture_repo(tmp_path: Path) -> Path:
    """§1i hermetic fixture repo: one real .py file with a real `def` line
    (`existing_helper`), under its own tmp_path/"repo" dir — never the real
    checkout. Returns the repo root to pass as `lint_spec`'s `repo_root`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / _FIXTURE_FILE).write_text(_FIXTURE_CONTENT, encoding="utf-8")
    return repo


def make_ctx(git_cwd: str) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd},
        question="GH1893 fixture",
        session_id="test-session-gh1893",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev(spec_path: Path, *, cycle: int = 1) -> StepResult:
    return StepResult(
        status="ok",
        data={"spec_path": str(spec_path), "cycle": cycle},
        duration_ms=0,
        step_name="write_spec_doc",
    )


# ─── AC1/AC2 (op2): section-declared symbol downgrades in lint_spec ──────────


def test_ac1_introduces_section_symbol_downgrades_to_new_symbol(tmp_path):
    """AC1: `## Symbols this spec INTRODUCES` names JOB_STATUS_COMPLETED; a
    citation of that symbol against an existing hermetic fixture-repo file
    must downgrade to `new_symbol` and lint_spec must return exit_code==0.

    Pre-GREEN: `spec_cite.declared_introduced_symbols` does not exist ->
    AttributeError. Even bypassing that, lint_spec today has no declarative
    INTRODUCES source, so the symbol stays `unresolved_symbol` -> exit_code=1.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC1\n\n"
        "## Symbols this spec INTRODUCES\n\n"
        "- `JOB_STATUS_COMPLETED`\n\n"
        f"See `JOB_STATUS_COMPLETED` in {_FIXTURE_FILE}:1 for details.\n"
    )
    assert "JOB_STATUS_COMPLETED" in spec_cite.declared_introduced_symbols(text)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)

    matches = [f for f in findings if f.symbol == "JOB_STATUS_COMPLETED"]
    assert matches, f"expected a finding for JOB_STATUS_COMPLETED; got {findings!r}"
    assert matches[0].status == "new_symbol", (
        f"introduced symbol must downgrade to new_symbol; got {matches[0].status!r}"
    )
    assert exit_code == 0, f"expected exit_code==0; got {exit_code}"


def test_ac2_typo_after_following_heading_stays_unresolved(tmp_path):
    """AC2: same spec as AC1, plus a second citation — a TYPO of a REAL
    fixture-repo symbol (`existing_helper` misspelled `existing_helperz`)
    sitting AFTER a following `##` heading, outside the INTRODUCES section
    body. It must stay `unresolved_symbol` (exit_code==1) while the
    introduced symbol's own finding is still `new_symbol`.

    Pre-GREEN: AttributeError on declared_introduced_symbols (asserted first).
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC2\n\n"
        "## Symbols this spec INTRODUCES\n\n"
        "- `JOB_STATUS_COMPLETED`\n\n"
        f"See `JOB_STATUS_COMPLETED` in {_FIXTURE_FILE}:1 for details.\n\n"
        "## Next Section\n\n"
        f"See `existing_helperz` in {_FIXTURE_FILE}:1 (typo of existing_helper).\n"
    )
    introduced = spec_cite.declared_introduced_symbols(text)
    assert "JOB_STATUS_COMPLETED" in introduced
    assert "existing_helperz" not in introduced

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)

    introduced_match = [f for f in findings if f.symbol == "JOB_STATUS_COMPLETED"]
    typo_match = [f for f in findings if f.symbol == "existing_helperz"]
    assert introduced_match and introduced_match[0].status == "new_symbol"
    assert typo_match, f"expected a finding for existing_helperz; got {findings!r}"
    assert typo_match[0].status == "unresolved_symbol", (
        f"undeclared typo after a following heading must stay unresolved_symbol; "
        f"got {typo_match[0].status!r}"
    )
    assert exit_code == 1, f"expected exit_code==1 (typo blocks); got {exit_code}"


# ─── AC3/AC4/AC4b/AC5/AC8/AC11 (op1): declared_introduced_symbols() itself ───


def test_ac3_line_form_introduces_without_section(tmp_path):
    """AC3: line form `- INTRODUCES: \\`SOME_NEW_FLAG\\`` with no heading
    section still allowlists the symbol -> lint_spec exit_code==0.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC3\n\n"
        "- INTRODUCES: `SOME_NEW_FLAG`\n\n"
        f"See `SOME_NEW_FLAG` in {_FIXTURE_FILE}:1 for details.\n"
    )
    assert "SOME_NEW_FLAG" in spec_cite.declared_introduced_symbols(text)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)
    matches = [f for f in findings if f.symbol == "SOME_NEW_FLAG"]
    assert matches and matches[0].status == "new_symbol"
    assert exit_code == 0, f"expected exit_code==0; got {exit_code}"


def test_ac4_backticked_token_only_inside_sql_fence_is_not_allowlisted(tmp_path):
    """AC4: inside the INTRODUCES section body, a ```sql fence carries a
    BACKTICKED token (`-- \\`SQL_ONLY_TOKEN\\` is the new column`). That token
    must NOT enter the allowlist -> its own citation (outside the fence)
    stays unresolved_symbol -> exit_code==1.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC4\n\n"
        "## Symbols this spec INTRODUCES\n\n"
        "```sql\n"
        "- `SQL_ONLY_TOKEN` is the new column\n"
        "SELECT * FROM jobs;\n"
        "```\n\n"
        f"See `SQL_ONLY_TOKEN` in {_FIXTURE_FILE}:1 for details.\n"
    )
    assert "SQL_ONLY_TOKEN" not in spec_cite.declared_introduced_symbols(text)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)
    matches = [f for f in findings if f.symbol == "SQL_ONLY_TOKEN"]
    assert matches, f"expected a finding for SQL_ONLY_TOKEN; got {findings!r}"
    assert matches[0].status == "unresolved_symbol", (
        f"sql-fenced-only token must NOT downgrade; got {matches[0].status!r}"
    )
    assert exit_code == 1, f"expected exit_code==1; got {exit_code}"


def test_ac4b_line_form_inside_markdown_fence_is_not_allowlisted(tmp_path):
    """AC4b: line form `- INTRODUCES: \\`FENCED_LINE_TOKEN\\`` placed INSIDE a
    ```markdown fence must NOT be allowlisted (```markdown is not a
    python-fence language, so `_iter_scannable_lines` skips it entirely) ->
    its citation stays unresolved_symbol -> exit_code==1.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC4b\n\n"
        "```markdown\n"
        "- INTRODUCES: `FENCED_LINE_TOKEN`\n"
        "```\n\n"
        f"See `FENCED_LINE_TOKEN` in {_FIXTURE_FILE}:1 for details.\n"
    )
    assert "FENCED_LINE_TOKEN" not in spec_cite.declared_introduced_symbols(text)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)
    matches = [f for f in findings if f.symbol == "FENCED_LINE_TOKEN"]
    assert matches, f"expected a finding for FENCED_LINE_TOKEN; got {findings!r}"
    assert matches[0].status == "unresolved_symbol", (
        f"fenced line-form token must NOT downgrade; got {matches[0].status!r}"
    )
    assert exit_code == 1, f"expected exit_code==1; got {exit_code}"


def test_ac5_symbol_after_following_heading_is_out_of_section(tmp_path):
    """AC5: a backticked symbol named AFTER the next `#` heading (outside the
    INTRODUCES section body) is not allowlisted -> its citation stays
    unresolved_symbol -> exit_code==1.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC5\n\n"
        "## Symbols this spec INTRODUCES\n\n"
        "- `IN_SECTION_TOKEN`\n\n"
        "## Next Section\n\n"
        "- `AFTER_HEADING_TOKEN`\n\n"
        f"See `AFTER_HEADING_TOKEN` in {_FIXTURE_FILE}:1 for details.\n"
    )
    introduced = spec_cite.declared_introduced_symbols(text)
    assert "IN_SECTION_TOKEN" in introduced
    assert "AFTER_HEADING_TOKEN" not in introduced

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)
    matches = [f for f in findings if f.symbol == "AFTER_HEADING_TOKEN"]
    assert matches, f"expected a finding for AFTER_HEADING_TOKEN; got {findings!r}"
    assert matches[0].status == "unresolved_symbol", (
        f"symbol named after the next heading must stay unresolved; "
        f"got {matches[0].status!r}"
    )
    assert exit_code == 1, f"expected exit_code==1; got {exit_code}"


def test_ac8_signature_form_extracts_leading_identifier(tmp_path):
    """AC8: signature form `- INTRODUCES: \\`make_job(cfg)\\`` extracts the
    leading identifier `make_job` into the allowlist -> its bare citation
    downgrades -> lint_spec exit_code==0.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC8\n\n"
        "- INTRODUCES: `make_job(cfg)`\n\n"
        f"See `make_job` in {_FIXTURE_FILE}:1 for details.\n"
    )
    assert "make_job" in spec_cite.declared_introduced_symbols(text)

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)
    matches = [f for f in findings if f.symbol == "make_job"]
    assert matches and matches[0].status == "new_symbol"
    assert exit_code == 0, f"expected exit_code==0; got {exit_code}"


def test_ac11_dragnet_last_section_citation_line_is_discarded(tmp_path):
    """AC11 (drag-net): the INTRODUCES section is LAST in the document (no
    following `#` heading), and a citation line carrying a TYPO of a real
    fixture-repo symbol (`existing_helper` -> `existing_helperz`) sits inside
    its body. §2.1: "a body line containing a _CODE_FILE_RE match is
    discarded" pins that the typo is NOT swept into the allowlist just
    because the section runs to EOF -> exit_code==1 / unresolved_symbol.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC11\n\n"
        "## Symbols this spec INTRODUCES\n\n"
        "- `LAST_SECTION_TOKEN`\n"
        f"- `existing_helperz` in {_FIXTURE_FILE}:1 (typo)\n"
    )
    introduced = spec_cite.declared_introduced_symbols(text)
    assert "LAST_SECTION_TOKEN" in introduced
    assert "existing_helperz" not in introduced, (
        "a citation line (matches _CODE_FILE_RE) inside a to-EOF section body "
        "must be discarded, not swept into the allowlist"
    )

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)
    matches = [f for f in findings if f.symbol == "existing_helperz"]
    assert matches, f"expected a finding for existing_helperz; got {findings!r}"
    assert matches[0].status == "unresolved_symbol", (
        f"drag-netted typo must stay unresolved_symbol; got {matches[0].status!r}"
    )
    assert exit_code == 1, f"expected exit_code==1; got {exit_code}"


# ─── AC6/AC7/AC9/AC12 (op3): _verify_spec_citations CREATE-declared paths ────


def test_ac6_line_only_create_declared_repo_rooted_citation_is_warning(tmp_path):
    """AC6: spec declares the target with ONLY a `CREATE: ` line (no
    `## Files this spec CREATES` heading) naming a repo-rooted path
    (`SYSTEM/cli/build/engine_py/gh1893_newmodule.py`), and cites it
    `...:12`; the file does not exist on disk yet. `_verify_spec_citations`
    must return status=="ok" with the matching citation_findings entry
    severity=="WARNING" (declared CREATE target, repo-rooted, not fabricated).

    Pre-GREEN: `declared_created_files`/a line-only CREATE collector is never
    consulted by `_verify_spec_citations` (grep confirms 0 hits) — the missing
    file is treated as a fabricated citation -> status=="error", not "ok".
    Also pins the §1g/§4 export name `create_line_declared_files` on
    `spec_cite` — ImportError pre-GREEN (the name does not exist yet).
    """
    from bytedigger_engine.spec_cite import create_line_declared_files  # noqa: F401

    git_cwd = tmp_path / "repo"
    git_cwd.mkdir()
    declared_path = "src/gh1893_newmodule.py"  # bytedigger: a top-level dir the neutral provider declares
    text = (
        f"CREATE: {declared_path}\n\n"
        f"See {declared_path}:12 for the new function.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")

    ctx = make_ctx(str(git_cwd))
    prev = make_prev(spec_path)
    result = _verify_spec_citations(ctx, prev)

    assert result.status == "ok", (
        f"line-only declared repo-rooted CREATE target must not fail citation "
        f"verify; got status={result.status!r}, error_code={result.error_code!r}, "
        f"data={result.data!r}"
    )
    findings = result.data.get("citation_findings", []) if isinstance(result.data, dict) else []
    matches = [f for f in findings if f.get("path") == declared_path]
    assert matches, f"expected a citation_findings entry for {declared_path}; got {findings!r}"
    assert matches[0]["severity"] == "WARNING", (
        f"declared repo-rooted CREATE target citation must be WARNING; got {matches[0]!r}"
    )
    assert matches[0]["reason"] == "declared CREATE target; not yet on disk", (
        f"expected the declared-CREATE reason literal; got {matches[0]!r}"
    )


def test_ac7_undeclared_repo_rooted_missing_path_regression_lock(tmp_path):
    """AC7 — REGRESSION LOCK, must PASS both before and after GREEN: the same
    shape of citation (missing file, repo-rooted `<path>:<line>`) but WITHOUT
    a `CREATE:` declaration must keep the prior fail-closed contract:
    error_code=="E_SPEC_CITATION_MALFORMED" and the finding severity=="ERROR".
    """
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir()
    undeclared_path = "SYSTEM/cli/build/engine_py/gh1893_undeclared.py"
    text = f"See {undeclared_path}:12 for details.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")

    ctx = make_ctx(str(git_cwd))
    prev = make_prev(spec_path)
    result = _verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"undeclared missing-path citation must keep the fabricated-citation "
        f"error_code; got {result.error_code!r} (status={result.status!r})"
    )
    errors = result.data.get("errors", []) if isinstance(result.data, dict) else []
    matches = [f for f in errors if f.get("path") == undeclared_path]
    assert matches, f"expected an ERROR finding for {undeclared_path}; got {errors!r}"
    assert matches[0]["severity"] == "ERROR", (
        f"undeclared missing path must stay ERROR severity; got {matches[0]!r}"
    )


def test_ac9_create_declared_non_repo_rooted_paths_stay_error_regression_lock(tmp_path):
    """AC9 — REGRESSION LOCK (lot 1896), must PASS both before and after
    GREEN: a `CREATE:` declaration does NOT lift the fabrication verdict for
    a path that is not ACTUALLY repo-rooted, in three shapes:
      - bare filename: `newmodule.py`
      - non-repo dir: `notarealdir/x.py`
      - escaping-through-a-real-dir: `SYSTEM/../notarealdir/x.py` (contains a
        real top-level dir as its FIRST segment, but does not resolve from
        the repo root after normalization — the naive `"/" in path` /
        naive-split trap this AC exists to catch).
    All three must still give error_code=="E_SPEC_CITATION_MALFORMED" and
    severity=="ERROR".
    """
    non_repo_rooted_paths = (
        "newmodule.py",
        "notarealdir/x.py",
        "SYSTEM/../notarealdir/x.py",
    )
    for bad_path in non_repo_rooted_paths:
        git_cwd = tmp_path / f"repo_{non_repo_rooted_paths.index(bad_path)}"
        git_cwd.mkdir()
        text = (
            f"CREATE: {bad_path}\n\n"
            f"See {bad_path}:12 for the new function.\n"
        )
        spec_path = git_cwd.parent / f"spec_{non_repo_rooted_paths.index(bad_path)}.md"
        spec_path.write_text(text, encoding="utf-8")

        ctx = make_ctx(str(git_cwd))
        prev = make_prev(spec_path)
        result = _verify_spec_citations(ctx, prev)

        assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
            f"CREATE-declared non-repo-rooted path {bad_path!r} must NOT lift "
            f"the fabrication lock; got error_code={result.error_code!r} "
            f"(status={result.status!r})"
        )
        errors = result.data.get("errors", []) if isinstance(result.data, dict) else []
        matches = [f for f in errors if f.get("path") == bad_path]
        assert matches, f"expected an ERROR finding for {bad_path!r}; got {errors!r}"
        assert matches[0]["severity"] == "ERROR", (
            f"non-repo-rooted declared path {bad_path!r} must stay ERROR "
            f"severity; got {matches[0]!r}"
        )


def test_ac12_decoy_creates_section_body_citation_stays_error_regression_lock(tmp_path):
    """AC12 — REGRESSION LOCK (decoy CREATES), must PASS both before and
    after GREEN: a `## Files this spec CREATES` section is present, but the
    citation to a non-existent path in its BODY prose is not itself declared
    via a literal `CREATE: ` line -> op3's line-only collector must NOT treat
    it as declared -> error_code=="E_SPEC_CITATION_MALFORMED" / severity=="ERROR".
    """
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir()
    decoy_path = "SYSTEM/cli/build/engine_py/gh1893_decoy.py"
    text = (
        "## Files this spec CREATES\n\n"
        f"See {decoy_path}:12 for details.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")

    ctx = make_ctx(str(git_cwd))
    prev = make_prev(spec_path)
    result = _verify_spec_citations(ctx, prev)

    assert result.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"decoy CREATES-section body citation (no literal CREATE: line) must "
        f"NOT be exempted; got error_code={result.error_code!r} "
        f"(status={result.status!r})"
    )
    errors = result.data.get("errors", []) if isinstance(result.data, dict) else []
    matches = [f for f in errors if f.get("path") == decoy_path]
    assert matches, f"expected an ERROR finding for {decoy_path}; got {errors!r}"
    assert matches[0]["severity"] == "ERROR", (
        f"decoy-section-body path must stay ERROR severity; got {matches[0]!r}"
    )


# ─── AC10 (op4): spec-writer prompt contract literal ─────────────────────────


def test_ac10_grounded_citation_contract_mentions_introduces_rule(tmp_path):
    """AC10: `_grounded_citation_contract()` must gain rule 10 — a literal
    reference to the `INTRODUCES` declaration mechanism (either the section
    heading `Symbols this spec INTRODUCES` or the line form `INTRODUCES:`) —
    while STILL containing rule 9's citation-form ban text.

    Pre-GREEN: neither literal is present (grep-confirmed absent today);
    only rule 9's incidental use of the English word "introduces" exists,
    which is NOT the same as the declared literal this AC pins.
    """
    contract = _grounded_citation_contract()

    assert (
        "Symbols this spec INTRODUCES" in contract or "INTRODUCES:" in contract
    ), (
        "GROUNDED CITATION CONTRACT must reference the INTRODUCES declaration "
        "mechanism (heading 'Symbols this spec INTRODUCES' or line form "
        "'INTRODUCES:')"
    )
    assert "INTRODUCES" in contract and (
        "- `" in contract or "bullet" in contract
    ), (
        "GROUNDED CITATION CONTRACT must mention the bullet/backtick "
        "declaration form alongside the INTRODUCES literal"
    )
    assert "NEVER appear in citation form" in contract, (
        "rule 9 (citation-form ban for spec-introduced NEW symbols) must persist"
    )


# ─── AC13/AC14/AC15 — post-GREEN pre-PR-review holes (must FAIL today) ───────


def test_ac13_fence_decoy_untagged_and_python_not_excluded(tmp_path):
    """AC13 (§2.3 fence rule, op3 AND op1): `_iter_scannable_lines` skips only
    NON-python fences; `_PY_FENCE_LANGS` includes `""`, so an UNTAGGED ``` \
    block (and a ```python block) is scanned normally — a `CREATE:` line
    hidden inside an example fence is read as a REAL declaration.

    part a: untagged ``` fence around `CREATE: .../gh1893_fenced.py` + a
    citation of that path -> must stay fabricated (error_code ==
    "E_SPEC_CITATION_MALFORMED", severity == "ERROR"). TODAY the fenced
    CREATE line is (wrongly) honored, downgrading the citation to WARNING
    with status=="ok" — this assertion fails.

    part b: same shape inside a ```python fence (same `_PY_FENCE_LANGS`
    bucket as untagged) — same expected failure.

    part c (op1, symmetric): an `- INTRODUCES: \\`FENCED_UNTAGGED_TOKEN\\``
    bullet inside an untagged ``` fence in a hermetic fixture spec must NOT
    be allowlisted -> its citation stays unresolved_symbol / exit_code==1.
    TODAY the untagged fence is scanned normally, so the token IS allowlisted
    — this assertion fails.
    """
    # part a: untagged fence
    git_cwd = tmp_path / "repo_untagged"
    git_cwd.mkdir()
    declared_path_a = "SYSTEM/cli/build/engine_py/gh1893_fenced.py"
    text_a = (
        "```\n"
        f"CREATE: {declared_path_a}\n"
        "```\n\n"
        f"See {declared_path_a}:12 for details.\n"
    )
    spec_path_a = tmp_path / "spec_a.md"
    spec_path_a.write_text(text_a, encoding="utf-8")
    result_a = _verify_spec_citations(make_ctx(str(git_cwd)), make_prev(spec_path_a))

    assert result_a.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"a CREATE: line fenced inside an untagged ``` block must NOT exempt "
        f"the citation (decoy); got error_code={result_a.error_code!r} "
        f"(status={result_a.status!r}, data={result_a.data!r})"
    )
    errors_a = result_a.data.get("errors", []) if isinstance(result_a.data, dict) else []
    matches_a = [f for f in errors_a if f.get("path") == declared_path_a]
    assert matches_a and matches_a[0]["severity"] == "ERROR", (
        f"expected an ERROR finding for {declared_path_a}; got {errors_a!r}"
    )

    # part b: ```python fence (same _PY_FENCE_LANGS bucket as untagged)
    git_cwd_b = tmp_path / "repo_python_fence"
    git_cwd_b.mkdir()
    declared_path_b = "SYSTEM/cli/build/engine_py/gh1893_fenced_py.py"
    text_b = (
        "```python\n"
        f"CREATE: {declared_path_b}\n"
        "```\n\n"
        f"See {declared_path_b}:12 for details.\n"
    )
    spec_path_b = tmp_path / "spec_b.md"
    spec_path_b.write_text(text_b, encoding="utf-8")
    result_b = _verify_spec_citations(make_ctx(str(git_cwd_b)), make_prev(spec_path_b))

    assert result_b.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"a CREATE: line fenced inside a ```python block must NOT exempt the "
        f"citation (decoy); got error_code={result_b.error_code!r} "
        f"(status={result_b.status!r}, data={result_b.data!r})"
    )
    errors_b = result_b.data.get("errors", []) if isinstance(result_b.data, dict) else []
    matches_b = [f for f in errors_b if f.get("path") == declared_path_b]
    assert matches_b and matches_b[0]["severity"] == "ERROR", (
        f"expected an ERROR finding for {declared_path_b}; got {errors_b!r}"
    )

    # part c (op1, symmetric): INTRODUCES bullet inside an untagged fence
    repo_root_c = _make_fixture_repo(tmp_path)
    text_c = (
        "# GH1893 fixture — AC13c\n\n"
        "```\n"
        "- INTRODUCES: `FENCED_UNTAGGED_TOKEN`\n"
        "```\n\n"
        f"See `FENCED_UNTAGGED_TOKEN` in {_FIXTURE_FILE}:1 for details.\n"
    )
    assert "FENCED_UNTAGGED_TOKEN" not in spec_cite.declared_introduced_symbols(text_c), (
        "an INTRODUCES bullet fenced inside an untagged ``` block must NOT be "
        "allowlisted — an untagged fence is treated as python (scanned), "
        "correct for real code but wrong for a doc-example fence"
    )
    spec_path_c = tmp_path / "spec_c.md"
    spec_path_c.write_text(text_c, encoding="utf-8")
    exit_code_c, findings_c = spec_cite.lint_spec(spec_path_c, repo_root_c)
    matches_c = [f for f in findings_c if f.symbol == "FENCED_UNTAGGED_TOKEN"]
    assert matches_c, f"expected a finding for FENCED_UNTAGGED_TOKEN; got {findings_c!r}"
    assert matches_c[0].status == "unresolved_symbol", (
        f"fenced-in-untagged-block token must NOT downgrade; got {matches_c[0].status!r}"
    )
    assert exit_code_c == 1, f"expected exit_code==1; got {exit_code_c}"


def test_ac14_introduces_line_form_has_no_code_file_guard(tmp_path):
    """AC14 (§2.1 line-form guard): the `INTRODUCES:` LINE form has no
    `_CODE_FILE_RE` guard (the SECTION form does) — it allowlists EVERY
    backtick token on the line, including a file-path token and any symbol
    named alongside it, e.g. a typo mid-sentence.

    Fixture line: `- INTRODUCES: \\`build_job\\` — replaces
    \\`existing_helperz\\` in \\`mod.py\\``. Neither `build_job` nor
    `existing_helperz` (a typo of the fixture-repo's real `existing_helper`)
    should be allowlisted, and a citation of `existing_helperz` (embedded on
    that very line, against `mod.py`) must stay unresolved_symbol/exit_code==1.

    TODAY: the line-form guard is absent, so both tokens ARE allowlisted and
    exit_code==0 — these assertions fail.
    """
    repo_root = _make_fixture_repo(tmp_path)
    text = (
        "# GH1893 fixture — AC14\n\n"
        "- INTRODUCES: `build_job` — replaces `existing_helperz` in `mod.py`\n"
    )
    introduced = spec_cite.declared_introduced_symbols(text)
    assert "build_job" not in introduced, (
        "a line-form INTRODUCES token must not sweep in unrelated backtick "
        "tokens just because the line also carries a code-file token"
    )
    assert "existing_helperz" not in introduced, (
        "the line-form guard must reject the whole line once it contains a "
        "_CODE_FILE_RE match — it is a citation, not a declaration"
    )

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(text, encoding="utf-8")
    exit_code, findings = spec_cite.lint_spec(spec_path, repo_root)
    matches = [f for f in findings if f.symbol == "existing_helperz"]
    assert matches, f"expected a finding for existing_helperz; got {findings!r}"
    assert matches[0].status == "unresolved_symbol", (
        f"typo swept in by the unguarded line form must stay unresolved_symbol; "
        f"got {matches[0].status!r}"
    )
    assert exit_code == 1, f"expected exit_code==1; got {exit_code}"


def test_ac15_scripts_dir_repo_rooted_widening_hole(tmp_path):
    """AC15 (§2.3 widened discriminator): the op3 discriminator uses
    `repo_top_level_dirs()`, which names only 5 dirs (SYSTEM/, SHARED/,
    DOCS/, USER/, .claude/) while this repo actually has 23 top-level dirs
    (scripts/, lib/, tools/, tests/, skills/, …). A genuinely repo-rooted
    CREATE-declared path under `scripts/` still hard-fails as fabricated.

    Two independent `_verify_spec_citations` calls (kept separate so this AC
    stays satisfiable by a real GREEN — mixing a genuine undeclared
    fabrication into the SAME spec as the scripts/ case would force the
    overall StepResult to status=="error" regardless of the scripts/
    finding's own severity, since ANY ERROR finding short-circuits the whole
    gate):

      call 1: `CREATE: scripts/gh1893_helper.sh` + a citation of the same
      path -> TODAY: ERROR/fabricated (the hole this AC pins) — must become
      status=="ok" / severity=="WARNING" once the discriminator recognizes
      `scripts/` as repo-rooted.

      call 2: `notarealdir/x.py`, NOT declared via CREATE: -> must stay
      error_code=="E_SPEC_CITATION_MALFORMED" / severity=="ERROR" regardless
      of the widening (lot 1896 lock intact) — this call already passes
      today and must keep passing.
    """
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir()
    (git_cwd / "scripts").mkdir()
    declared_path = "scripts/gh1893_helper.sh"
    text = (
        f"CREATE: {declared_path}\n\n"
        f"See {declared_path}:12 for the new script.\n"
    )
    spec_path = tmp_path / "spec_scripts.md"
    spec_path.write_text(text, encoding="utf-8")
    result = _verify_spec_citations(make_ctx(str(git_cwd)), make_prev(spec_path))

    assert result.status == "ok", (
        f"scripts/ is a real repo top-level dir; a CREATE-declared target "
        f"under it must not fail; got status={result.status!r}, "
        f"error_code={result.error_code!r}, data={result.data!r}"
    )
    findings = result.data.get("citation_findings", []) if isinstance(result.data, dict) else []
    matches = [f for f in findings if f.get("path") == declared_path]
    assert matches, f"expected a citation_findings entry for {declared_path}; got {findings!r}"
    assert matches[0]["severity"] == "WARNING", (
        f"scripts/-rooted declared CREATE target must be WARNING; got {matches[0]!r}"
    )

    git_cwd2 = tmp_path / "repo2"
    git_cwd2.mkdir()
    undeclared_path = "notarealdir/x.py"
    text2 = f"See {undeclared_path}:5 for an undeclared path.\n"
    spec_path2 = tmp_path / "spec_notarealdir.md"
    spec_path2.write_text(text2, encoding="utf-8")
    result2 = _verify_spec_citations(make_ctx(str(git_cwd2)), make_prev(spec_path2))

    assert result2.error_code == "E_SPEC_CITATION_MALFORMED", (
        f"undeclared notarealdir/x.py must stay fabricated even with the "
        f"scripts/ widening; got error_code={result2.error_code!r} "
        f"(status={result2.status!r})"
    )
    errors2 = result2.data.get("errors", []) if isinstance(result2.data, dict) else []
    matches2 = [f for f in errors2 if f.get("path") == undeclared_path]
    assert matches2, f"expected an ERROR finding for {undeclared_path}; got {errors2!r}"
    assert matches2[0]["severity"] == "ERROR", (
        f"undeclared notarealdir/x.py must stay ERROR severity; got {matches2[0]!r}"
    )
