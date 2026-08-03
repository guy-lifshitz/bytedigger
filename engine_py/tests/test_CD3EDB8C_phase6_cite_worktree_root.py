"""RED tests for CD3EDB8C — phase_6 cite-verify resolves relative paths against worktree root.

Spec: SHARED/memory/Decisions/2026-06-14_CD3EDB8C_phase6_cite_worktree_root_spec.md

Bug: _verify_finding_quote resolves relative citations against Path.cwd() (the orchestrator
main-checkout), not the build-target worktree root. On worktree builds the cited files exist
under the worktree root, not under cwd -> tagged suspect-file-not-found -> excluded from
verified_findings -> fix-worker never sees them -> false-clean.

Fix (§2):
  - Change 1: _verify_finding_quote gains base_dir: Path | None = None; uses
    (base_dir or Path.cwd()) for relative-path resolution.
  - Change 2: _aggregate_review_findings computes target_root = _resolve_worktree_root(ctx,
    scratchpad) and threads it as base_dir= to _verify_finding_quote.

Expected pre-GREEN status: ALL 3 ACs FAIL.
  AC1: FAIL - TypeError (base_dir kwarg does not exist yet)
  AC2: FAIL - TypeError (base_dir kwarg does not exist yet)
  AC3: FAIL - resolves vs cwd -> suspect-file-not-found -> excluded from verified_findings
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest  # noqa: F401

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    _aggregate_review_findings,
    _verify_finding_quote,
)
from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402


# ─── AC1: base_dir kwarg routes resolution to worktree root ───────────────────


def test_ac1_relative_cite_resolves_against_base_dir(tmp_path, monkeypatch):
    """AC1: _verify_finding_quote(block, {}, base_dir=<wt>) resolves relative path against
    <wt>, not cwd.

    Fixture: <wt>/sub/real.py exists with known content; cwd set to a DIFFERENT tmpdir
    where sub/real.py does NOT exist.

    Pre-GREEN FAIL: TypeError — _verify_finding_quote does not accept base_dir kwarg.
    Post-GREEN PASS: returns status startswith "verified".
    """
    # Worktree root with the cited file
    wt = tmp_path / "worktree"
    wt.mkdir()
    cited_dir = wt / "sub"
    cited_dir.mkdir()
    real_file = cited_dir / "real.py"
    real_file.write_text(
        "# line 1\ndef worktree_function():\n    pass\n",
        encoding="utf-8",
    )
    # Line 2 is "def worktree_function():" (1-based)
    quote = "def worktree_function():"
    lineno = 2

    # cwd is a DIFFERENT temp dir — sub/real.py does NOT exist there
    other_dir = tmp_path / "other_cwd"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    # Verify the file is absent under cwd
    assert not (other_dir / "sub" / "real.py").exists(), (
        "test setup: sub/real.py must not exist under other_dir (cwd)"
    )

    block = (
        f"### SEVERITY: HIGH — WtRelFinding-AC1\n"
        f"> sub/real.py:{lineno}: {quote}\n"
        "Confidence: HIGH\n"
        "Description: worktree-relative citation test\n"
    )

    # Pre-GREEN: raises TypeError because base_dir kwarg does not exist.
    # Post-GREEN: returns ("verified-exact"|"verified-windowed"|"verified-substring", ...).
    status, _reason = _verify_finding_quote(block, {}, base_dir=wt)

    assert status.startswith("verified"), (
        f"AC1: expected status.startswith('verified') when base_dir=<wt> and the file "
        f"exists under <wt>/sub/real.py but NOT under cwd. "
        f"Got status={status!r}. "
        f"This asserts that _verify_finding_quote uses base_dir for relative resolution."
    )


# ─── AC2: base_dir=None preserves Path.cwd() fallback ────────────────────────


def test_ac2_base_dir_none_preserves_cwd_fallback(tmp_path, monkeypatch):
    """AC2: _verify_finding_quote(block, {}, base_dir=None) falls back to Path.cwd().

    Fixture: the cited file exists UNDER cwd. base_dir=None must preserve the current
    cwd-resolution behavior (backward-compat, grounding-fallback-chain).

    Pre-GREEN FAIL: TypeError — _verify_finding_quote does not accept base_dir kwarg.
    Post-GREEN PASS: returns status startswith "verified" (cwd fallback intact).
    """
    # Create the cited file under tmp_path, then chdir there
    cited_dir = tmp_path / "pkg"
    cited_dir.mkdir()
    cited_file = cited_dir / "util.py"
    cited_file.write_text(
        "# line 1\ndef cwd_function():\n    return True\n",
        encoding="utf-8",
    )
    quote = "def cwd_function():"
    lineno = 2

    # cwd = tmp_path; pkg/util.py exists relative to cwd
    monkeypatch.chdir(tmp_path)
    assert (tmp_path / "pkg" / "util.py").exists(), (
        "test setup: pkg/util.py must exist under tmp_path (cwd)"
    )

    block = (
        f"### SEVERITY: MEDIUM — CwdFallback-AC2\n"
        f"> pkg/util.py:{lineno}: {quote}\n"
        "Confidence: HIGH\n"
        "Description: cwd-fallback citation test\n"
    )

    # Pre-GREEN: raises TypeError because base_dir kwarg does not exist.
    # Post-GREEN: returns ("verified-exact"|..., ...) using Path.cwd() fallback.
    status, _reason = _verify_finding_quote(block, {}, base_dir=None)

    assert status.startswith("verified"), (
        f"AC2: expected status.startswith('verified') with base_dir=None when the file "
        f"exists under cwd. Got status={status!r}. "
        f"This asserts the Path.cwd() fallback is retained (backward-compat)."
    )


# ─── AC3: _aggregate_review_findings threads worktree root to verifier ────────


def test_ac3_worktree_relative_finding_reaches_verified_findings(tmp_path, monkeypatch):
    """AC3 (consumer-trace forcing function): _aggregate_review_findings with
    ctx.org_config['current_worktree_path']=<wt> produces a finding in verified_findings
    when the cited file exists under <wt> but NOT under cwd.

    Fixture mirrors test_ac11_aggregate_review_findings_partitions_and_forwards_verified_only
    from test_phase_6_verified_only_gate_65695203.py, with this difference: the 'good' file
    is worktree-relative (exists under <wt>/sub/wt_target.py) and cwd is set elsewhere.

    Pre-GREEN FAIL: _aggregate_review_findings calls _verify_finding_quote(block, cache)
    without base_dir -> uses Path.cwd() -> file absent under cwd -> suspect-file-not-found
    -> excluded from verified_findings -> assertion on 'WtRelFinding' fails.
    Post-GREEN PASS: base_dir=_resolve_worktree_root(ctx, scratchpad) threaded in ->
    resolved against <wt> -> verified-exact -> appears in verified_findings.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    # Worktree root: the target repo the reviewer was run against
    wt = tmp_path / "worktree_root"
    wt.mkdir()
    wt_sub = wt / "sub"
    wt_sub.mkdir()
    wt_file = wt_sub / "wt_target.py"
    wt_file.write_text(
        "# line 1\ndef build_target_fn():\n    return 42\n",
        encoding="utf-8",
    )
    wt_quote = "def build_target_fn():"
    wt_lineno = 2

    # Scratchpad + reviews dir live in tmp_path (NOT inside wt so cwd is unambiguous)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # Set cwd to yet another dir where sub/wt_target.py does NOT exist
    cwd_dir = tmp_path / "main_checkout"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)

    assert not (cwd_dir / "sub" / "wt_target.py").exists(), (
        "test setup: sub/wt_target.py must not exist under cwd"
    )

    # role-wt: worktree-relative citation (relative path, file exists under wt NOT cwd)
    wt_role_lines = [
        "# ac3 worktree-relative reviewer", "",
        "### SEVERITY: HIGH — WtRelFinding",
        f"> sub/wt_target.py:{wt_lineno}: {wt_quote}",
        "Confidence: HIGH",
        "Description: worktree-relative finding for CD3EDB8C",
        "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-ac3-wt.md").write_text(
        "\n".join(wt_role_lines), encoding="utf-8"
    )

    # ctx: current_worktree_path points to <wt> so _resolve_worktree_root returns it
    ctx = types.SimpleNamespace(
        org_config={
            "scratchpad_dir": str(scratchpad),
            "current_worktree_path": str(wt),
        },
        question="test question",
    )
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")

    result = _aggregate_review_findings(ctx, prev)

    assert result.status == "ok", (
        f"AC3: _aggregate_review_findings must return ok, got {result.status!r}: "
        f"{result.error!r}"
    )

    data = result.data or {}

    assert "verified_findings" in data, (
        "AC3: 'verified_findings' key absent from result.data — "
        "regression: must be present (gate 65695203)."
    )

    verified = data.get("verified_findings", [])
    verified_titles = [f.get("title", "") for f in verified]

    # WtRelFinding cites a worktree-relative path; must appear in verified_findings
    # when ctx.org_config['current_worktree_path'] points to the worktree root.
    assert "WtRelFinding" in verified_titles, (
        f"AC3: expected 'WtRelFinding' in verified_findings but got: {verified_titles}. "
        f"Pre-GREEN: resolves vs cwd (absent there) -> suspect-file-not-found -> excluded. "
        f"Post-GREEN: resolves vs worktree_path -> verified-exact -> included. "
        f"This is the consumer-trace forcing function: proves base_dir is threaded through "
        f"_aggregate_review_findings (Change 2 in §2)."
    )

    # Confirm it is not in the suspect bucket either (just in case result.data exposes it)
    suspect = [
        f for f in (data.get("all_findings") or [])
        if str(f.get("verify_status", "")).startswith("suspect")
           and f.get("title") == "WtRelFinding"
    ]
    assert not suspect, (
        f"AC3: 'WtRelFinding' must NOT appear as a suspect finding. "
        f"Found: {suspect}"
    )
