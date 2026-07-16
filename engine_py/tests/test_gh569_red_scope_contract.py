"""RED tests for GH569 — red_scope allowlist parser + derived test-scope contract.

Agreement: AD14A3ED (issue #569, track #560)
Spec: SHARED/memory/Decisions/2026-07-11_GH569_red_scope_allowlist_spec.md

Contract:
1. `lib.run_allowlist._parse_items` accepts a broader bulletless/backtick/table
   grammar (AC1-AC6).
2. New pure helpers `lib.run_allowlist.is_test_shaped` and
   `lib.run_allowlist.derived_test_scope_match` (AC7, AC8) — do not exist yet.
3. `phase_5_implement._commit_red_tests`'s AD14A3ED gate composes an effective
   allowlist = ## Files entries UNION authorized-test-edits UNION derived
   test-scope, and emits `derived_matched_n` / `authorized_test_edits_n`
   telemetry fields (AC9, AC10).

Conftest (§1q singleton) already puts ENGINE_ROOT / ENGINE_ROOT/workflows /
ENGINE_ROOT/lib on sys.path — no module-level sys.path manipulation here.
Symbols that do not exist yet (`is_test_shaped`, `derived_test_scope_match`)
are imported INSIDE each test body so the file collects cleanly today.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from lib.run_allowlist import _parse_items  # noqa: E402
from contracts import WorkflowContext  # noqa: E402


# ─── repo helpers (same pattern as test_phase_5_AD14A3ED_red_scope.py) ────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _add_untracked(repo: Path, relpath: str, body: str = "# test\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_ctx(scratchpad: Path, git_cwd: str, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="GH569 red_scope RED",
        session_id="test-gh569",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths, cycle: int = 1, spec_path: "str | None" = None):
    prev = MagicMock()
    prev.data = {"red_test_paths": red_test_paths, "cycle": cycle}
    if spec_path is not None:
        prev.data["spec_path"] = spec_path
    return prev


# ═══════════════════════════════════════════════════════════════════════════
# AC1-AC6 — _parse_items grammar
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_parse_items_bulletless_modify_with_annotation() -> None:
    body = "MODIFY: a/b.py (ann)\n"
    assert _parse_items(body) == ["a/b.py"], (
        f"AC1 FAIL: bulletless 'MODIFY:' line with trailing paren annotation "
        f"must parse to ['a/b.py'], got {_parse_items(body)!r}"
    )


def test_ac2_parse_items_bulletless_create_with_emdash_tail() -> None:
    body = "CREATE: a/b.py — long annotation\n"
    assert _parse_items(body) == ["a/b.py"], (
        f"AC2 FAIL: em-dash annotation tail must be dropped, got {_parse_items(body)!r}"
    )


def test_ac3_parse_items_bare_backtick_and_bare_paren() -> None:
    body = "`a/b.py`\na/b.py  (new — lib)\n"
    result = _parse_items(body)
    assert result == ["a/b.py", "a/b.py"], (
        f"AC3 FAIL: bare backticked path and bare parenthetical-annotated path "
        f"must both parse to just the path, got {result!r}"
    )


def test_ac4_parse_items_markdown_table_row_skips_separator() -> None:
    body = (
        "| Verb | Path | Note |\n"
        "|---|---|---|\n"
        "| MODIFY | `a/b.py` | ann |\n"
    )
    result = _parse_items(body)
    assert result == ["a/b.py"], (
        f"AC4 FAIL: markdown table row must yield ['a/b.py'] with header/separator "
        f"rows skipped (no '/' or '.' path-like token in them), got {result!r}"
    )


def test_ac5_parse_items_prose_lines_yield_no_entries() -> None:
    body = "**Files NOT in scope:** everything else is off limits here.\n"
    result = _parse_items(body)
    assert result == [], (
        f"AC5 FAIL: prose line with no path-like token must yield no entries, "
        f"got {result!r}"
    )


def test_ac6_parse_items_legacy_bulleted_form_regression() -> None:
    body = "- MODIFY a/b.py\n- CREATE c/d.py\n"
    result = _parse_items(body)
    assert result == ["a/b.py", "c/d.py"], (
        f"AC6 FAIL (regression): legacy bulleted verb-prefixed form must still "
        f"parse identically, got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — is_test_shaped
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_is_test_shaped_true_cases() -> None:
    from lib.run_allowlist import is_test_shaped

    for path in (
        "x/__tests__/y.test.ts",
        "x/tests/test_y.py",
        "test_y.py",
        "y.spec.ts",
    ):
        assert is_test_shaped(path) is True, (
            f"AC7 FAIL: {path!r} must be test-shaped"
        )


def test_ac7_is_test_shaped_false_cases() -> None:
    from lib.run_allowlist import is_test_shaped

    for path in ("x/y.py", "x/contest.py"):
        assert is_test_shaped(path) is False, (
            f"AC7 FAIL: {path!r} must NOT be test-shaped (contest.py has 'test' "
            f"as a substring but not as basename-prefix/suffix/segment)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# AC8 — derived_test_scope_match
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8_derived_test_scope_match_zero_levels_up() -> None:
    from lib.run_allowlist import derived_test_scope_match

    entries = ["USER/skills/PAHelper/Tools/bridge.ts"]
    assert derived_test_scope_match(
        "USER/skills/PAHelper/Tools/__tests__/bridge.test.ts", entries
    ) is True, "AC8 FAIL: 0-levels-up __tests__ sibling dir must be admitted"


def test_ac8_derived_test_scope_match_one_level_up() -> None:
    from lib.run_allowlist import derived_test_scope_match

    entries = ["engine_py/lib/x.py"]
    assert derived_test_scope_match("engine_py/tests/test_x.py", entries) is True, (
        "AC8 FAIL: 1-level-up tests/ sibling of parent dir must be admitted"
    )


def test_ac8_derived_test_scope_match_three_levels_up_not_admitted() -> None:
    from lib.run_allowlist import derived_test_scope_match

    entries = ["SYSTEM/cli/build/engine_py/lib/x.py"]
    assert derived_test_scope_match(
        "SYSTEM/other/tests/test_z.py", entries
    ) is False, (
        "AC8 FAIL: test-shaped path 3+ ancestor levels above the entry's dirname "
        "must NOT be admitted (only up to 2 ancestor levels are allowed)"
    )


def test_ac8_derived_test_scope_match_non_test_shaped_rogue_not_admitted() -> None:
    from lib.run_allowlist import derived_test_scope_match

    entries = ["engine_py/lib/x.py"]
    assert derived_test_scope_match("engine_py/rogue.py", entries) is False, (
        "AC8 FAIL: a non-test-shaped path must never be admitted via derivation, "
        "regardless of directory proximity"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC9 — gate integration: derived test-scope admits co-located test, rogue
# non-test path still violates, enforce=True raises E_RED_SCOPE_VIOLATION
# ═══════════════════════════════════════════════════════════════════════════


class TestGateDerivedScopeIntegration:
    def _spec_bulletless_prod_only(self, path: Path, prod_path: str) -> None:
        path.write_text(
            f"# Spec\n\n## Files\nMODIFY: {prod_path} (impl)\n\n## End\n"
        )

    def test_ac9_derived_match_admits_colocated_test_zero_violations(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Bulletless MODIFY: prod-only ## Files + RED path = co-located test
        dir (engine_py/lib/x.py -> engine_py/tests/test_x.py) must parse the
        allowlist as non-empty (AC1-AC6 parser fix) AND admit the RED path via
        derivation: violations_n=0, derived_matched_n=1."""
        import phase_5_implement

        repo = _make_repo(tmp_path)
        spec_file = tmp_path / "spec.md"
        self._spec_bulletless_prod_only(spec_file, "engine_py/lib/x.py")

        _add_untracked(repo, "engine_py/tests/test_x.py", "def test_x(): assert True\n")

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo), enforce_red_scope_allowlist=False)
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        result = phase_5_implement._commit_red_tests(ctx, prev)

        assert result.status == "ok"
        scope_events = [(n, p) for (n, p) in captured if n == "red_scope_check"]
        assert len(scope_events) == 1, f"expected exactly one red_scope_check event, got {captured!r}"
        payload = scope_events[0][1]
        assert payload.get("allowlist_size") == 1, (
            f"AC9 FAIL: bulletless 'MODIFY: path (ann)' line must parse to a "
            f"1-entry allowlist (parser fix), got allowlist_size="
            f"{payload.get('allowlist_size')!r}"
        )
        assert payload.get("violations_n") == 0, (
            f"AC9 FAIL: co-located derived test path must not violate, got "
            f"violations_n={payload.get('violations_n')!r}"
        )
        assert payload.get("derived_matched_n") == 1, (
            f"AC9 FAIL: expected derived_matched_n=1 for the one RED path admitted "
            f"only via derivation, got {payload.get('derived_matched_n')!r}"
        )

    def test_ac9_rogue_non_test_path_elsewhere_is_violation_under_enforce(
        self, tmp_path: Path
    ) -> None:
        """A RED path that is neither directly allowlisted nor derivable
        must still violate under enforce=True -> E_RED_SCOPE_VIOLATION.

        Gate-approved fixture amendment: the rogue path is test-shaped
        (`somewhere/else/tests/test_rogue.py`) because post-γ disk_truth git
        enumeration only surfaces test-shaped files — a non-test-shaped rogue
        never reaches the scope gate at all. It sits outside the ≤2-level
        derivation reach of the `engine_py/lib/x.py` entry."""
        import phase_5_implement

        repo = _make_repo(tmp_path)
        spec_file = tmp_path / "spec.md"
        self._spec_bulletless_prod_only(spec_file, "engine_py/lib/x.py")

        _add_untracked(repo, "engine_py/tests/test_x.py", "def test_x(): assert True\n")
        _add_untracked(repo, "somewhere/else/tests/test_rogue.py", "x = 1\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo), enforce_red_scope_allowlist=True)
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        result = phase_5_implement._commit_red_tests(ctx, prev)

        assert result.status == "error"
        assert result.error_code == "E_RED_SCOPE_VIOLATION", (
            f"AC9 FAIL: rogue non-test-shaped path outside allowlist/derivation "
            f"must trip E_RED_SCOPE_VIOLATION under enforce=True, got "
            f"status={result.status!r} error_code={result.error_code!r}"
        )
        assert "somewhere/else/tests/test_rogue.py" in (result.error or ""), (
            f"AC9 FAIL: violation error must name the rogue path, got {result.error!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# AC10 — authorized-test-edits entry match
# ═══════════════════════════════════════════════════════════════════════════


class TestGateAuthorizedTestEditsIntegration:
    def test_ac10_authorized_test_edits_match_zero_violations_and_counted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A RED path matched only via the `authorized-test-edits:` marker
        block (not derivable, not in ## Files) must yield violations_n=0 and
        authorized_test_edits_n>=1."""
        import phase_5_implement

        repo = _make_repo(tmp_path)
        spec_file = tmp_path / "spec.md"
        spec_file.write_text(
            "# Spec\n\n## Files\nMODIFY: engine_py/lib/x.py (impl)\n\n"
            "authorized-test-edits:\n- some/other/dir/test_helper.py\n\n## End\n"
        )

        _add_untracked(
            repo, "some/other/dir/test_helper.py", "def test_h(): assert True\n"
        )

        captured: list[tuple[str, dict]] = []

        def fake_emit(event_name, payload, severity=None):
            captured.append((event_name, dict(payload)))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(scratchpad, str(repo), enforce_red_scope_allowlist=False)
        prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))

        result = phase_5_implement._commit_red_tests(ctx, prev)

        assert result.status == "ok"
        scope_events = [(n, p) for (n, p) in captured if n == "red_scope_check"]
        assert len(scope_events) == 1
        payload = scope_events[0][1]
        assert payload.get("violations_n") == 0, (
            f"AC10 FAIL: authorized-test-edits match must not violate, got "
            f"violations_n={payload.get('violations_n')!r}"
        )
        assert payload.get("authorized_test_edits_n", 0) >= 1, (
            f"AC10 FAIL: expected authorized_test_edits_n>=1, got "
            f"{payload.get('authorized_test_edits_n')!r}"
        )
