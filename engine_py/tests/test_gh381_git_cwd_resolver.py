"""RED tests for GH#381 — shared git_cwd resolver (lib/git_cwd.py).

Spec: SHARED/memory/Decisions/2026-07-07_GH381_git_cwd_shared_resolver_spec.md

Design summary:
  - NEW lib/git_cwd.py::resolve_git_cwd(cfg, prev_data=None) -> str.
    Precedence: cfg['git_cwd'] -> prev_data['git_cwd'] -> cfg['current_worktree_path']
    -> climb cfg['scratchpad_dir'] parents until (p/'.git').exists() -> Path.cwd().
    Emits resolver_git_cwd_resolved via lib.observability.emit_resolver.
  - phase_5_implement._resolve_git_cwd(ctx, prev) becomes a thin delegate.
  - 8 more prod sites (phase_5_integrity/phase_45_spec/phase_6_fix_integrity/
    phase_6_review/run_allowlist) funnel through the same resolver.

§1q collectability mandate: lib.git_cwd does NOT exist pre-GREEN. Import it
INSIDE each test body — never at module top level — so this file always
COLLECTS cleanly. phase_5_implement / contracts / telemetry_ctx already exist
today so they are imported at module scope (mirrors test_3F4B71D4 idiom).

Pre-GREEN PASS/FAIL:
  - AC1-AC6 -> FAIL: ImportError on lib.git_cwd inside test body (module absent).
  - AC7 -> FAIL: _resolve_git_cwd has no scratchpad-climb branch; with cfg/prev
    both lacking git_cwd it falls through to Path.cwd() (the monkeypatched
    "elsewhere" dir), not the tmp_repo root.
  - AC8 -> FAIL: current E_RED_NO_MARKER error string has no "git_cwd=" token
    and does not name the resolved path.
  - AC9 -> FAIL: the literal `cfg.get("git_cwd") or Path.cwd()`-shaped
    expression is still present in the §1 table's prod files today.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.workflows import phase_5_implement as p5  # conftest-import-time singleton puts workflows/ on sys.path
from bytedigger_engine.contracts import StepResult, WorkflowContext


# ─── shared helpers ────────────────────────────────────────────────────────────


def _build_repo_with_scratchpad(tmp_path: Path) -> tuple[Path, Path]:
    """A tmp_repo with a bare .git marker dir and a nested scratchpad dir.

    resolve_git_cwd's scratchpad-climb only checks (p / ".git").exists() — a
    bare marker dir is sufficient (team-lead directive; no real `git init`
    needed for AC4/AC6/AC7).
    """
    tmp_repo = tmp_path / "repo"
    (tmp_repo / ".git").mkdir(parents=True)
    scratchpad = tmp_repo / ".hal-build" / "scratchpad" / "r1"
    scratchpad.mkdir(parents=True)
    return tmp_repo.resolve(), scratchpad.resolve()


def _make_real_repo(base: Path) -> Path:
    """Create a minimal real git repo with one commit (AC8 needs real `git diff`)."""
    repo = Path(str(base / "repo")).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    placeholder = repo / "src" / "placeholder.py"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    placeholder.write_text("# placeholder\n")
    subprocess.run(["git", "add", "src/placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return r.stdout.strip()


def _write_pre_red_ref(scratchpad: Path, sha: str) -> None:
    ref = scratchpad / p5.PRE_RED_REF_RELPATH
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(sha)


def _make_scratchpad(tmp_path: Path) -> Path:
    sp = (tmp_path / "scratchpad").resolve()
    sp.mkdir(parents=True, exist_ok=True)
    return sp


def _make_ctx(scratchpad: Path, git_cwd: str | None = None) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="GH381 git_cwd resolver test",
        session_id="test-gh381",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_for_commit(scratchpad: Path, red_test_paths) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": 1,
            "red_test_paths": red_test_paths,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


# ─── AC1: cfg['git_cwd'] wins, verbatim ────────────────────────────────────────


def test_ac1_cfg_git_cwd_verbatim():
    """AC1: resolve_git_cwd({"git_cwd": "/x/y"}) returns "/x/y" verbatim.

    Pre-GREEN: FAIL — ImportError, lib.git_cwd does not exist yet.
    """
    from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # noqa: PLC0415

    result = resolve_git_cwd({"git_cwd": "/x/y"})

    assert result == "/x/y", (
        f"GH381 AC1: expected verbatim '/x/y', got {result!r}."
    )


# ─── AC2: prev_data['git_cwd'] fallback ───────────────────────────────────────


def test_ac2_prev_data_git_cwd_fallback():
    """AC2: cfg without git_cwd, prev_data={"git_cwd": "/p/q"} -> "/p/q".

    Pre-GREEN: FAIL — ImportError, lib.git_cwd does not exist yet.
    """
    from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # noqa: PLC0415

    result = resolve_git_cwd({}, {"git_cwd": "/p/q"})

    assert result == "/p/q", (
        f"GH381 AC2: expected prev_data fallback '/p/q', got {result!r}."
    )


# ─── AC3: current_worktree_path resolved ──────────────────────────────────────


def test_ac3_current_worktree_path_resolved(tmp_path):
    """AC3: cfg {"current_worktree_path": <tmp_wt>} only -> str(Path(tmp_wt).resolve()).

    Pre-GREEN: FAIL — ImportError, lib.git_cwd does not exist yet.
    """
    from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # noqa: PLC0415

    tmp_wt = tmp_path / "worktree"
    tmp_wt.mkdir()

    result = resolve_git_cwd({"current_worktree_path": str(tmp_wt)})

    assert result == str(tmp_wt.resolve()), (
        f"GH381 AC3: expected {str(tmp_wt.resolve())!r}, got {result!r}."
    )


# ─── AC4: scratchpad-climb finds the .git root, not cwd ───────────────────────


def test_ac4_scratchpad_climb_finds_git_root_not_cwd(tmp_path, monkeypatch):
    """AC4: cfg {"scratchpad_dir": <tmp_repo>/.hal-build/scratchpad/r1} with
    tmp_repo/.git present, cwd chdir'd to a DIFFERENT tmp dir -> returns
    str(tmp_repo.resolve()), NOT cwd.

    Pre-GREEN: FAIL — ImportError, lib.git_cwd does not exist yet.
    """
    from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # noqa: PLC0415

    tmp_repo, scratchpad = _build_repo_with_scratchpad(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = resolve_git_cwd({"scratchpad_dir": str(scratchpad)})

    assert result == str(tmp_repo), (
        f"GH381 AC4: expected scratchpad-climb to find {str(tmp_repo)!r}, got {result!r}. "
        f"Must NOT fall back to cwd={str(elsewhere)!r}."
    )


# ─── AC5: empty cfg -> Path.cwd() ──────────────────────────────────────────────


def test_ac5_empty_cfg_falls_back_to_cwd():
    """AC5: cfg {} -> str(Path.cwd()).

    Pre-GREEN: FAIL — ImportError, lib.git_cwd does not exist yet.
    """
    from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # noqa: PLC0415

    result = resolve_git_cwd({})

    assert result == str(Path.cwd()), (
        f"GH381 AC5: expected str(Path.cwd())={str(Path.cwd())!r}, got {result!r}."
    )


# ─── AC6: scratchpad-climb resolution emits resolver_git_cwd_resolved ─────────


def test_ac6_scratchpad_climb_emits_resolver_event(tmp_path, monkeypatch):
    """AC6: resolving via scratchpad-climb emits exactly one
    resolver_git_cwd_resolved event with {"source": "scratchpad_climb",
    "value": <tmp_repo>} (§1l production side-effect anchor).

    Pre-GREEN: FAIL — ImportError, lib.git_cwd does not exist yet.
    """
    from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # noqa: PLC0415

    captured: list[dict] = []
    monkeypatch.setattr(
        telemetry_ctx,
        "emit_safe",
        lambda et, payload, **kw: captured.append({"type": et, "payload": payload}),
    )

    tmp_repo, scratchpad = _build_repo_with_scratchpad(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    resolve_git_cwd({"scratchpad_dir": str(scratchpad)})

    matching = [e for e in captured if e["type"] == "resolver_git_cwd_resolved"]
    assert len(matching) == 1, (
        f"GH381 AC6: expected exactly one 'resolver_git_cwd_resolved' event, "
        f"got {[e['type'] for e in captured]!r}."
    )
    payload = matching[0]["payload"]
    assert payload.get("source") == "scratchpad_climb", (
        f"GH381 AC6: expected source=='scratchpad_climb', got {payload!r}."
    )
    assert payload.get("value") == str(tmp_repo), (
        f"GH381 AC6: expected value=={str(tmp_repo)!r}, got {payload!r}."
    )


# ─── AC7: phase_5._resolve_git_cwd delegates to the shared resolver ───────────


def test_ac7_resolve_git_cwd_delegates_to_scratchpad_climb(tmp_path, monkeypatch):
    """AC7: phase_5_implement._resolve_git_cwd(ctx, prev) with ctx.org_config
    carrying only scratchpad_dir (under tmp_repo), no git_cwd anywhere, cwd
    chdir'd elsewhere -> returns str(tmp_repo.resolve()) (proves delegation;
    §1y Point=phase_5_implement.py:942 region, Host=_resolve_git_cwd,
    Test-path=direct call).

    Pre-GREEN: FAIL — today's _resolve_git_cwd has no scratchpad-climb branch;
    cfg/prev both lack git_cwd -> falls straight to Path.cwd() == elsewhere.
    """
    tmp_repo, scratchpad = _build_repo_with_scratchpad(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    ctx = _make_ctx(scratchpad, git_cwd=None)

    result = p5._resolve_git_cwd(ctx, None)

    assert result == str(tmp_repo), (
        f"GH381 AC7: expected delegated scratchpad-climb result {str(tmp_repo)!r}, "
        f"got {result!r}. Pre-GREEN: falls back to cwd={str(elsewhere)!r}."
    )


# ─── AC8: E_RED_NO_MARKER error names the resolved git_cwd ────────────────────


def test_ac8_no_marker_error_names_resolved_git_cwd(tmp_path):
    """AC8: drive _commit_red_tests to the E_RED_NO_MARKER branch (git-diff
    empty, no persisted/disk-fallback recovery) -> result.error contains the
    resolved git_cwd path AND the substring "git_cwd="; error_code stays
    "E_RED_NO_MARKER" (mirrors test_3F4B71D4_red_path_fallback_chain.py AC10
    fixture shape).

    Pre-GREEN: FAIL — current error string has no "git_cwd=" token and does
    not name the resolved path (§2.3 not yet implemented).
    """
    repo = _make_real_repo(tmp_path)
    sp = _make_scratchpad(tmp_path)

    head = _head_sha(repo)
    _write_pre_red_ref(sp, head)
    # No red-test-paths.txt written, prev.data has no red_test_paths -> nothing
    # recoverable -> E_RED_NO_MARKER terminal.

    ctx = _make_ctx(sp, git_cwd=str(repo))
    prev = _prev_for_commit(sp, None)

    result = p5._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_NO_MARKER", (
        f"GH381 AC8: expected error_code=='E_RED_NO_MARKER', "
        f"got {result.error_code!r} status={result.status!r}."
    )
    error_msg = result.error or ""
    assert "git_cwd=" in error_msg, (
        f"GH381 AC8: expected 'git_cwd=' token in error message, got {error_msg!r}. "
        f"Pre-GREEN: §2.3 message rewrite absent — FAIL expected."
    )
    assert str(repo) in error_msg, (
        f"GH381 AC8: expected resolved git_cwd path {str(repo)!r} named in error "
        f"message, got {error_msg!r}."
    )


# ─── AC9: closure — no raw `cfg.get("git_cwd") or Path.cwd()` sites remain ────


def test_ac9_no_raw_git_cwd_or_cwd_fallback_sites_remain():
    """AC9: deterministic category-closure gate (Principle A). Three checks,
    ALL must hold post-GREEN (gate-REJECTED REQ1: the original regex-only
    version missed site 5's `if cfg.get("git_cwd") else None` shape):

    (a) zero matches of `git_cwd["']\\)\\s*or\\s*Path\\.cwd\\(\\)` across the
        §1-table prod files (sites 2/3/4/6-10's `... or Path.cwd()` shape).
    (b) zero matches of the literal `if cfg.get("git_cwd") else None` in
        workflows/phase_6_fix_integrity.py (site 5's distinct shape).
    (c) each of the 6 MOD prod files contains the literal
        `from bytedigger_engine.lib.git_cwd import resolve_git_cwd` (positive-presence proof
        that the file actually funnels through the shared resolver, not just
        that the old literal happens to be gone).

    Pre-GREEN: FAIL — (a) and (b) both still match today (cite-verified
    2026-07-07, HEAD f93ac0798), and (c) the import literal is absent from
    every target (lib/git_cwd.py does not exist yet).
    """
    engine_root = Path(__file__).resolve().parents[1]
    targets = [
        engine_root / "bytedigger_engine" / "workflows" / "phase_5_implement.py",
        engine_root / "bytedigger_engine" / "workflows" / "phase_5_integrity.py",
        engine_root / "bytedigger_engine" / "workflows" / "phase_45_spec.py",
        engine_root / "bytedigger_engine" / "workflows" / "phase_6_fix_integrity.py",
        engine_root / "bytedigger_engine" / "workflows" / "phase_6_review.py",
        engine_root / "bytedigger_engine" / "lib" / "run_allowlist.py",
    ]

    # (a) negative — the `... or Path.cwd()` shape (sites 2/3/4/6-10)
    pattern = re.compile(r'git_cwd["\']\)\s*or\s*Path\.cwd\(\)')
    or_cwd_matches: list[str] = []
    for f in targets:
        text = f.read_text()
        for m in pattern.finditer(text):
            or_cwd_matches.append(f"{f.name}: {m.group(0)!r}")

    assert or_cwd_matches == [], (
        f"GH381 AC9(a): expected zero raw `git_cwd or Path.cwd()`-shaped sites; "
        f"found {len(or_cwd_matches)}: {or_cwd_matches!r}. "
        f"Pre-GREEN: sites still present — FAIL expected."
    )

    # (b) negative — site 5's distinct `if cfg.get("git_cwd") else None` shape
    fix_integrity = engine_root / "bytedigger_engine" / "workflows" / "phase_6_fix_integrity.py"
    fix_integrity_text = fix_integrity.read_text()
    assert 'if cfg.get("git_cwd") else None' not in fix_integrity_text, (
        f"GH381 AC9(b): expected zero occurrences of the literal "
        f"'if cfg.get(\"git_cwd\") else None' in {fix_integrity.name}. "
        f"Pre-GREEN: literal still present — FAIL expected."
    )

    # (c) positive — every MOD file actually imports the shared resolver
    import_literal = "from bytedigger_engine.lib.git_cwd import resolve_git_cwd"
    missing_import: list[str] = []
    for f in targets:
        if import_literal not in f.read_text():
            missing_import.append(f.name)

    assert missing_import == [], (
        f"GH381 AC9(c): expected every MOD prod file to contain "
        f"{import_literal!r}; missing from: {missing_import!r}. "
        f"Pre-GREEN: lib/git_cwd.py does not exist yet, no file imports it "
        f"— FAIL expected."
    )
