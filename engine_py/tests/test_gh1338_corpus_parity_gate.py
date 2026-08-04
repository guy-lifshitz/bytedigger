"""RED tests for GH1338 — corpus-parity gate (base freshness + test-corpus divergence).

Spec: 2026-07-30_GH1338_corpus_parity_gate_spec.md

UUT does not exist yet:
  - SYSTEM/cli/build/engine_py/lib/corpus_parity.py (NEW module)
  - CLI flags on SYSTEM/cli/build/baseline_delta_gate.py: --require-corpus-parity,
    --head-ref, --allow-removed-test-file
  - workflows/_baseline_delta.py: unconditional --require-corpus-parity argv +
    parity_block/would_block wiring
  - error_codes.E_CORPUS_PARITY, flags_catalog.HAL_CORPUS_PARITY_ENFORCE
  - phase_5_implement.py: parity_block -> E_CORPUS_PARITY branch

Per §1q (D1CF5FDF extension): imports of the not-yet-existing target module
(lib.corpus_parity) are deferred into test bodies so collection succeeds
cleanly and each test fails individually at assert/call time.

§1i: AC1-AC3, AC10-AC14 fixtures are REAL git repos built fresh under
tmp_path per test — no shared/singleton resource, no timing race.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ─── repo/fixture paths ──────────────────────────────────────────────────────

_THIS = Path(__file__).resolve()
_ENGINE_ROOT = _THIS.parents[1]        # …/engine_py/
_BUILD_DIR = _THIS.parents[2]          # bytedigger repo root
_SCRIPT = _BUILD_DIR / "baseline_delta_gate.py"
_WORKFLOWS = _ENGINE_ROOT / "workflows"


from bytedigger_engine import error_codes
from bytedigger_engine import flags_catalog


# ─── git helpers (raw subprocess permitted in tests/ — git-port-lint excludes it) ──


def _git(cwd, *args):
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    })
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t"] + list(args),
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    r = _git(path, "init", "-b", "main")
    assert r.returncode == 0, f"git init failed: {r.stderr!r}"
    return path


def _commit_all(repo, msg):
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-m", msg)
    assert r.returncode == 0, f"git commit failed: {r.stderr!r}"
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _write_test_ts(repo, name, cases):
    """cases: list of (test_name, passing:bool) — writes a bun:test file."""
    lines = ['import { test, expect } from "bun:test";']
    for tname, passing in cases:
        body = "expect(1).toBe(1);" if passing else "expect(1).toBe(2);"
        lines.append(f'test("{tname}", () => {{ {body} }});')
    (repo / name).write_text("\n".join(lines) + "\n")


def _run_gate(args, cwd, env):
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _empty_ledger(tmp_path, name="known-reds.md"):
    p = tmp_path / name
    p.write_text("| Suite | Red | Scope | Issue | Kill-by | Class |\n|---|---|---|---|---|---|\n")
    return p


def _build_parity_fixture(tmp_path):
    """Real git repo, real `bun test` runs — per spec §4 AC11 fixture shape
    (rev2-N3 fixed): the HEAD COMMIT contains a.test.ts + b.test.ts + c.test.ts.
    The deletion of b.test.ts happens ONLY in the head-side worktree's working
    tree (os.unlink, never staged/committed) — a head_ref-TREE implementation
    would see b.test.ts present on both sides and wrongly pass; only a real
    worktree-based collect_corpus(None,...) genuinely observes the deletion.
    Returns (repo, base_sha, head_sha, base_out, head_out, ledger, head_wt) —
    callers MUST run the gate with cwd=head_wt (the worktree where the
    uncommitted deletion actually lives), not cwd=repo.
    """
    bun = shutil.which("bun")
    assert bun is not None, (
        "bun binary not found on PATH — GH1338 AC11/AC12/AC13 require REAL "
        "`bun test` runs per §4; this is a hard AC failure, not a skip"
    )

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_test_ts(repo, "a.test.ts", [("shared alpha", True), ("only base red", False)])
    _write_test_ts(repo, "b.test.ts", [("b passes", True)])
    base_sha = _commit_all(repo, "base")

    _write_test_ts(repo, "c.test.ts", [("c passes", True)])
    head_sha = _commit_all(repo, "head")  # head commit CONTAINS a+b+c; nothing deleted yet

    base_wt = tmp_path / "wt_base"
    head_wt = tmp_path / "wt_head"
    r1 = _git(repo, "worktree", "add", "--detach", str(base_wt), base_sha)
    assert r1.returncode == 0, f"git worktree add (base) failed: {r1.stderr!r}"
    r2 = _git(repo, "worktree", "add", "--detach", str(head_wt), head_sha)
    assert r2.returncode == 0, f"git worktree add (head) failed: {r2.stderr!r}"

    # deletion lives ONLY in the head worktree's WORKING TREE — never staged,
    # never committed (rev2-N3). `git status` in head_wt shows it as unstaged.
    (head_wt / "b.test.ts").unlink()

    base_out = tmp_path / "base.out"
    head_out = tmp_path / "head.out"
    bp = subprocess.run([bun, "test", "."], cwd=str(base_wt), capture_output=True, text=True)
    hp = subprocess.run([bun, "test", "."], cwd=str(head_wt), capture_output=True, text=True)
    base_out.write_text(bp.stdout + bp.stderr)
    head_out.write_text(hp.stdout + hp.stderr)

    base_text = base_out.read_text()
    head_text = head_out.read_text()
    assert "only base red" in base_text, (
        f"expected real bun fail on base side, got base stdout+stderr={base_text!r}"
    )
    assert "only base red" in head_text, (
        f"expected real bun fail on head side, got head stdout+stderr={head_text!r}"
    )

    ledger = _empty_ledger(tmp_path)
    return repo, base_sha, head_sha, base_out, head_out, ledger, head_wt


def _gate_json(proc):
    """Assert-time RED per §1q: a gate subprocess must produce parseable JSON
    on stdout. A bare json.decoder.JSONDecodeError is an incidental crash,
    not a diagnostic failure — surface returncode/stdout/stderr explicitly.
    """
    out = proc.stdout or ""
    assert out.strip(), (
        f"gate subprocess produced EMPTY stdout (no JSON to parse); "
        f"returncode={proc.returncode!r} stdout={out!r} "
        f"stderr_tail={(proc.stderr or '')[-2000:]!r}"
    )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"gate subprocess stdout was not parseable JSON ({exc}); "
            f"returncode={proc.returncode!r} stdout={out!r} "
            f"stderr_tail={(proc.stderr or '')[-2000:]!r}"
        ) from exc


def _clean_env(**overrides):
    env = dict(os.environ)
    env.pop("HAL_BASELINE_DELTA_GATE_ENFORCE", None)
    env.pop("HAL_CORPUS_PARITY_ENFORCE", None)
    env.pop("HAL_BASELINE_DELTA_GATE", None)
    env.pop("HAL_CORPUS_ALLOW_REMOVED", None)
    env.pop("HAL_KNOWN_REDS_TODAY", None)
    env.update(overrides)
    return env


# ─── AC1: check_base_freshness OK when base is an ancestor of head ──────────


def test_ac1_check_base_freshness_ok_when_base_is_ancestor(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import check_base_freshness  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("1")
    base_sha = _commit_all(repo, "base")
    (repo / "f.txt").write_text("2")
    head_sha = _commit_all(repo, "head")

    result = check_base_freshness(base_sha, head_sha, cwd=str(repo))

    assert result.get("status") == "OK", (
        f"expected status OK when base is ancestor of head, got {result!r}"
    )


# ─── AC2: base contains a commit not in head → E_STALE_BASE ─────────────────


def test_ac2_check_base_freshness_stale_when_base_not_ancestor(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import check_base_freshness  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("root")
    _commit_all(repo, "root")

    _git(repo, "checkout", "-b", "side")
    (repo / "f.txt").write_text("side-only")
    base_sha = _commit_all(repo, "side-commit")  # unique to 'side'

    _git(repo, "checkout", "main")
    (repo / "g.txt").write_text("main-only")
    head_sha = _commit_all(repo, "main-commit")  # does NOT contain base_sha

    result = check_base_freshness(base_sha, head_sha, cwd=str(repo))

    assert result.get("status") == "E_STALE_BASE", (
        f"expected E_STALE_BASE when base is not an ancestor of head, got {result!r}"
    )


# ─── AC3: nonexistent ref → E_FRESHNESS_UNKNOWN (fail-closed, not OK) ───────


def test_ac3_check_base_freshness_unresolvable_ref_fails_closed(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import check_base_freshness  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("1")
    head_sha = _commit_all(repo, "head")

    result = check_base_freshness("totally-bogus-ref-does-not-exist-xyz", head_sha, cwd=str(repo))

    assert result.get("status") != "OK", (
        f"a nonexistent ref must NEVER resolve to OK (fail-closed), got {result!r}"
    )
    assert result.get("status") == "E_FRESHNESS_UNKNOWN", (
        f"expected E_FRESHNESS_UNKNOWN for unresolvable ref, got {result!r}"
    )


# ─── AC4: collect_corpus filters by suite pattern (bun → *.test.ts only) ────


def test_ac4_collect_corpus_filters_bun_suite_pattern(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import collect_corpus  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.test.ts").write_text("x")
    (repo / "notes.md").write_text("notes")
    src = repo / "src"
    src.mkdir()
    (src / "x.ts").write_text("y")
    sha = _commit_all(repo, "seed")

    result = collect_corpus(sha, "bun", cwd=str(repo))

    files = set(result.get("files") or [])
    assert files == {"a.test.ts"}, (
        f"expected only a.test.ts (suite pattern *.test.ts), got {files!r} "
        f"(full result={result!r})"
    )


# ─── AC5: rc!=0 -> E_CORPUS_UNKNOWN; rc0 + empty set -> also E_CORPUS_UNKNOWN ─


def test_ac5_collect_corpus_unknown_on_bad_ref_and_on_empty_corpus(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import collect_corpus  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "notes.md").write_text("no test files here")
    empty_sha = _commit_all(repo, "no-tests")

    bad_ref_result = collect_corpus("bogus-ref-does-not-resolve", "bun", cwd=str(repo))
    assert bad_ref_result.get("status") == "E_CORPUS_UNKNOWN", (
        f"expected E_CORPUS_UNKNOWN on unresolvable ref (rc!=0), got {bad_ref_result!r}"
    )

    empty_result = collect_corpus(empty_sha, "bun", cwd=str(repo))
    assert empty_result.get("status") == "E_CORPUS_UNKNOWN", (
        f"expected E_CORPUS_UNKNOWN on rc==0 but EMPTY corpus (never OK-with-nothing), "
        f"got {empty_result!r}"
    )


# ─── AC6: collect_corpus(None,...) = PR/worktree side ───────────────────────


def test_ac6_collect_corpus_worktree_side_includes_untracked_excludes_deleted(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import collect_corpus  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.test.ts").write_text("x")
    (repo / "b.txt").write_text("y")
    _commit_all(repo, "seed")

    (repo / "tracked.test.ts").unlink()  # deleted in working tree, NOT committed
    (repo / "untracked_new.test.ts").write_text("z")  # never git-added

    result = collect_corpus(None, "bun", cwd=str(repo))

    files = set(result.get("files") or [])
    assert "untracked_new.test.ts" in files, (
        f"expected untracked test-file included on PR/worktree side, got {files!r}"
    )
    assert "tracked.test.ts" not in files, (
        f"expected a test-file deleted in the worktree to be excluded, got {files!r}"
    )
    assert result.get("source") == "worktree", (
        f"expected source=='worktree' for ref=None, got {result!r}"
    )


# ─── AC7: compare_corpus enumerates paths; clean superset -> OK ─────────────


def test_ac7_compare_corpus_enumerates_missing_and_extra(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import compare_corpus  # ImportError pre-GREEN → FAIL

    divergent = compare_corpus(["a.test.ts", "gone.test.ts"], ["a.test.ts"])
    assert divergent.get("only_in_base") == ["gone.test.ts"], (
        f"expected only_in_base to ENUMERATE the missing path, got {divergent!r}"
    )
    assert divergent.get("status") == "E_CORPUS_DIVERGENCE", (
        f"expected E_CORPUS_DIVERGENCE for undeclared missing file, got {divergent!r}"
    )

    superset = compare_corpus(["a.test.ts"], ["a.test.ts", "new.test.ts"])
    assert superset.get("status") == "OK", (
        f"expected OK for clean PR-superset-of-base, got {superset!r}"
    )
    assert superset.get("only_in_base") == [], f"got {superset!r}"
    assert superset.get("only_in_pr") == ["new.test.ts"], f"got {superset!r}"


# ─── AC8: declared removal (basename form) covers, but still enumerated ─────


def test_ac8_compare_corpus_declared_removal_honored_but_still_listed(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import compare_corpus  # ImportError pre-GREEN → FAIL

    result = compare_corpus(
        ["a.test.ts", "sub/gone.test.ts"], ["a.test.ts"],
        declared_removals=("gone.test.ts",),
    )

    assert result.get("status") == "OK", (
        f"expected OK once removal is declared, got {result!r}"
    )
    assert "sub/gone.test.ts" in (result.get("declared_removals_honored") or []), (
        f"expected sub/gone.test.ts in declared_removals_honored, got {result!r}"
    )
    assert result.get("declared_removal_count") == 1, f"got {result!r}"
    assert "sub/gone.test.ts" in (result.get("only_in_base") or []), (
        f"declaring a removal must NOT hide it from only_in_base, got {result!r}"
    )


# ─── AC9: declared removal that matches nothing -> declared_removals_stale ──


def test_ac9_compare_corpus_stale_declared_removal_still_ok(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import compare_corpus  # ImportError pre-GREEN → FAIL

    result = compare_corpus(
        ["a.test.ts"], ["a.test.ts"], declared_removals=("nope.test.ts",),
    )

    assert result.get("status") == "OK", f"got {result!r}"
    assert "nope.test.ts" in (result.get("declared_removals_stale") or []), (
        f"expected a removal token that matched nothing in declared_removals_stale, "
        f"got {result!r}"
    )


# ─── AC10: §1n closure — every status ∈ PRECONDITION_CODES; blocked_by order ─


def test_ac10_precondition_codes_closure_and_blocked_by_fixed_order(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import (  # ImportError pre-GREEN → FAIL
        PRECONDITION_CODES,
        evaluate_preconditions,
    )

    assert PRECONDITION_CODES == frozenset({
        "OK", "E_STALE_BASE", "E_STALE_REMOTE_REF", "E_REMOTE_UNREACHABLE",
        "E_FRESHNESS_UNKNOWN", "E_CORPUS_DIVERGENCE", "E_CORPUS_UNKNOWN",
        "E_RESULTS_UNPARSEABLE", "E_EMPTY_RUN",
    }), f"PRECONDITION_CODES drifted from the frozen §1n/§10 rev-4 set: {PRECONDITION_CODES!r}"

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_test_ts(repo, "shared.test.ts", [("shared ok", True)])
    _commit_all(repo, "root")

    _git(repo, "checkout", "-b", "side")
    _write_test_ts(repo, "onlybase.test.ts", [("only base ok", True)])
    base_sha = _commit_all(repo, "side-commit")

    _git(repo, "checkout", "main")
    _write_test_ts(repo, "onlyhead.test.ts", [("only head ok", True)])
    head_sha = _commit_all(repo, "main-commit")

    result = evaluate_preconditions(base_sha, head_sha, "bun", cwd=str(repo))

    assert result.get("status") == "BLOCKED", (
        f"expected BLOCKED for a stale+divergent scenario, got {result!r}"
    )
    assert result.get("blocked_by") == ["E_STALE_BASE", "E_CORPUS_DIVERGENCE"], (
        f"expected blocked_by in the FIXED §10 rev-4 order "
        f"(E_STALE_BASE, E_STALE_REMOTE_REF, E_REMOTE_UNREACHABLE, E_FRESHNESS_UNKNOWN, "
        f"E_CORPUS_DIVERGENCE, E_CORPUS_UNKNOWN, E_RESULTS_UNPARSEABLE, E_EMPTY_RUN), "
        f"got {result!r}"
    )


# ─── AC11: §1l REAL bun runs — Δ0 by reds, but BLOCKED by corpus divergence ─


def test_ac11_real_bun_fixture_delta_zero_but_corpus_blocked(tmp_path) -> None:
    repo, base_sha, head_sha, base_out, head_out, ledger, head_wt = _build_parity_fixture(tmp_path)

    env = _clean_env(HAL_CORPUS_PARITY_ENFORCE="1")
    proc = _run_gate(
        [
            "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
            "--baseline", str(base_out),
            "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
        ],
        cwd=head_wt, env=env,
    )

    data = _gate_json(proc)  # not-yet-implemented flags → malformed/empty stdout → FAIL today
    assert data.get("delta_verdict") == "PASS", (
        f"reds match after timing-tail normalization (Δ0) — expected delta_verdict PASS, "
        f"got {data!r} (stderr={proc.stderr!r})"
    )
    assert data.get("verdict") == "BLOCKED", (
        f"corpus divergence (b.test.ts missing on PR side) must BLOCK the overall verdict, "
        f"got {data!r}"
    )
    assert data.get("blocked_by") == ["E_CORPUS_DIVERGENCE"], f"got {data!r}"
    assert "b.test.ts" in (data.get("only_in_base") or []), f"got {data!r}"
    assert proc.returncode == 5, (
        f"expected exit 5 under HAL_CORPUS_PARITY_ENFORCE=1, got {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


# ─── AC12: negative control — declared removal defeats the block, even under enforce ─


def test_ac12_allow_removed_test_file_defeats_block_under_enforce(tmp_path) -> None:
    repo, base_sha, head_sha, base_out, head_out, ledger, head_wt = _build_parity_fixture(tmp_path)

    env = _clean_env(HAL_CORPUS_PARITY_ENFORCE="1")
    proc = _run_gate(
        [
            "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
            "--baseline", str(base_out),
            "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
            "--allow-removed-test-file", "b.test.ts",
        ],
        cwd=head_wt, env=env,
    )

    data = _gate_json(proc)
    assert data.get("verdict") == "PASS", (
        f"declared removal must defeat the block even under enforce, got {data!r} "
        f"(stderr={proc.stderr!r})"
    )
    assert proc.returncode == 0, (
        f"expected exit 0 (declared removal, obviated block) under enforce, "
        f"got {proc.returncode}; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert data.get("declared_removal_count") == 1, (
        f"the bypass must be COUNTED and VISIBLE in JSON, got {data!r}"
    )


# ─── AC13 (AMENDED rev4 MAJOR-4): BLOCKED ⇒ exit 5 ALWAYS, not just under enforce ──


def test_ac13_blocked_verdict_always_exits_5_stderr_enumerates(tmp_path) -> None:
    """§10 MAJOR-4 (rev4): the prior contract — 'no HAL_CORPUS_PARITY_ENFORCE →
    exit 0 even though verdict is BLOCKED' — is REVERSED. A consumer gating on
    the return code (`... && echo OK`) must never see success for a BLOCKED
    verdict. `HAL_CORPUS_PARITY_ENFORCE` now governs ONLY the engine-level
    parity_block/E_CORPUS_PARITY branch (§2.3), not the CLI exit code. The
    stderr enumeration requirement from the old AC13 is UNCHANGED and still
    asserted here.
    """
    repo, base_sha, head_sha, base_out, head_out, ledger, head_wt = _build_parity_fixture(tmp_path)

    env = _clean_env()  # HAL_CORPUS_PARITY_ENFORCE unset — must NOT matter for exit code now
    proc = _run_gate(
        [
            "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
            "--baseline", str(base_out),
            "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
        ],
        cwd=head_wt, env=env,
    )

    data = _gate_json(proc)
    assert data.get("verdict") == "BLOCKED", f"got {data!r}"
    assert proc.returncode == 5, (
        f"rev4 MAJOR-4: verdict BLOCKED must exit 5 ALWAYS, independent of "
        f"HAL_CORPUS_PARITY_ENFORCE — the prior 'exit 0 in warn-only mode' "
        f"contract is REVERSED and must no longer hold. got {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "b.test.ts" in proc.stderr, (
        f"stderr MUST still enumerate the divergent path, got stderr={proc.stderr!r}"
    )


# ─── AC14: bogus --base-ref under --require-corpus-parity → BLOCKED, never PASS ─


def test_ac14_bogus_base_ref_blocked_never_pass(tmp_path) -> None:
    """§2.2 (amended, gate M3): preconditions run BEFORE resolve_baseline, so an
    unresolvable --base-ref under --require-corpus-parity yields verdict BLOCKED
    (never the driver-crash ERROR/exit-2 path) — with NO --baseline given at all.
    The flagless path on the SAME bogus ref must still be ERROR/exit 2, pinning
    the ordering change in both directions at once.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_test_ts(repo, "a.test.ts", [("ok", True)])
    head_sha = _commit_all(repo, "head")

    results = tmp_path / "results.txt"
    results.write_text("(pass) suite > ok\n")
    ledger = _empty_ledger(tmp_path)

    env = _clean_env()
    proc = _run_gate(
        [
            "--results", str(results), "--suite", "bun", "--ledger", str(ledger),
            "--require-corpus-parity",
            "--base-ref", "totally-bogus-ref-xyz", "--head-ref", head_sha,
        ],
        cwd=repo, env=env,
    )

    data = _gate_json(proc)
    assert data.get("verdict") != "PASS", (
        f"an unresolvable base ref must never yield PASS (fail-closed), got {data!r}"
    )
    assert data.get("verdict") == "BLOCKED", f"got {data!r}"
    assert "E_FRESHNESS_UNKNOWN" in (data.get("blocked_by") or []), (
        f"expected E_FRESHNESS_UNKNOWN in blocked_by, got {data!r}"
    )

    # ── flagless path on the SAME bogus ref: unchanged, prior contract holds ──
    flagless_proc = _run_gate(
        [
            "--results", str(results), "--suite", "bun", "--ledger", str(ledger),
            "--base-ref", "totally-bogus-ref-xyz", "--head-ref", head_sha,
        ],
        cwd=repo, env=env,
    )
    flagless_data = _gate_json(flagless_proc)
    assert flagless_data.get("verdict") == "ERROR", (
        f"without --require-corpus-parity, the OLD RuntimeError->_emit_error path must "
        f"still fire (verdict ERROR), got {flagless_data!r}"
    )
    assert flagless_proc.returncode == 2, (
        f"expected exit 2 on the flagless path (unchanged §2.2 contract), "
        f"got {flagless_proc.returncode}; stdout={flagless_proc.stdout!r} "
        f"stderr={flagless_proc.stderr!r}"
    )


# ─── shared name-selective fake config (AC15/AC19/AC21 — gate M6) ───────────
#
# A `flag()` that returns True unconditionally cannot pin name-selectivity: a
# GREEN that keys `parity_block` off the WRONG env name (or a typo) would still
# satisfy it. This fake dispatches per-name, so AC21 can prove the two
# directions independently.


class _NamedFlagsCfg:
    def __init__(self, flags):
        self._flags = dict(flags)

    def gate_enabled(self, name):
        return True

    def flag(self, name):
        return bool(self._flags.get(name, False))

    def path(self, name, default):
        return default


def _fake_completed(returncode, stdout, stderr=""):
    class _FakeCompleted:
        pass

    obj = _FakeCompleted()
    obj.returncode = returncode
    obj.stdout = stdout
    obj.stderr = stderr
    return obj


# ─── AC15: _baseline_delta wiring — unconditional argv flag + parity_block ──


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac15_baseline_delta_wiring_passes_flag_and_sets_parity_block(tmp_path, monkeypatch) -> None:
    import workflows._baseline_delta as bd_mod  # exists today, but not yet wired

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["argv"] = cmd
        verdict_json = {
            "verdict": "BLOCKED", "delta_verdict": "PASS", "new_fails": [], "ledgered": [],
            "baseline_source": "explicit", "blocked_by": ["E_CORPUS_DIVERGENCE"],
        }
        return _fake_completed(0, json.dumps(verdict_json))

    monkeypatch.setattr(bd_mod.subprocess, "run", fake_run)

    # name-selective per §2.1 amendment (gate M6) — NOT "True for everything"
    cfg = _NamedFlagsCfg({
        "HAL_BASELINE_DELTA_ENFORCE": True,
        "HAL_CORPUS_PARITY_ENFORCE": True,
    })

    events = []

    def emit(name, payload, **kw):
        events.append((name, payload))

    stdout_path = tmp_path / "out.txt"
    stdout_path.write_text("collected 1 item\n")

    result = bd_mod.run_baseline_delta_gate(
        str(stdout_path), "bun", str(tmp_path), 5, "test_step", emit, cfg=cfg,
    )

    assert "--require-corpus-parity" in captured.get("argv", []), (
        f"expected --require-corpus-parity to ALWAYS be passed in argv per §2.3, "
        f"got argv={captured.get('argv')!r}"
    )
    assert result.get("parity_block") is True, (
        f"expected parity_block True when verdict BLOCKED and enforce flag True, "
        f"got {result!r}"
    )
    assert result.get("would_block") is True, (
        f"expected would_block True (parity_block ORs into would_block), got {result!r}"
    )
    assert result.get("blocked_by") == ["E_CORPUS_DIVERGENCE"], f"got {result!r}"
    verdict_events = [e for e in events if e[0] == "baseline_delta_gate_verdict"]
    assert verdict_events, f"expected a baseline_delta_gate_verdict event, got {events!r}"
    assert verdict_events[0][1].get("blocked_by") == ["E_CORPUS_DIVERGENCE"], (
        f"expected blocked_by threaded into the emitted event, got {events!r}"
    )


# ─── AC16: E_CORPUS_PARITY / HAL_CORPUS_PARITY_ENFORCE registered; phase_5 wired ─


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac16_error_code_flag_and_phase5_mapping_registered() -> None:
    assert "E_CORPUS_PARITY" in error_codes.ERROR_CODES, (
        f"expected E_CORPUS_PARITY registered, got sample="
        f"{[k for k in error_codes.ERROR_CODES if 'CORPUS' in k]!r}"
    )
    assert "HAL_CORPUS_PARITY_ENFORCE" in flags_catalog.FLAGS, (
        f"expected HAL_CORPUS_PARITY_ENFORCE registered, got sample="
        f"{[k for k in flags_catalog.FLAGS if 'CORPUS' in k]!r}"
    )
    assert "HAL_CORPUS_ALLOW_REMOVED" in flags_catalog.FLAGS, (
        f"expected HAL_CORPUS_ALLOW_REMOVED (engine-lane declaration channel, §2.1) "
        f"registered, got sample={[k for k in flags_catalog.FLAGS if 'CORPUS' in k]!r}"
    )

    phase5_path = _WORKFLOWS / "phase_5_implement.py"
    text = phase5_path.read_text(encoding="utf-8")
    idx = text.find("baseline_delta_blocks: list")
    assert idx != -1, (
        "anchor 'baseline_delta_blocks: list' not found in phase_5_implement.py — "
        "cannot bound the region for the parity_block/E_CORPUS_PARITY check"
    )
    end_idx = text.find("\n    # ── F23A1FDF", idx)
    region = text[idx:end_idx] if end_idx != -1 else text[idx:idx + 4000]

    assert "parity_block" in region, (
        f"expected the baseline-delta block region to key on 'parity_block', "
        f"region head={region[:400]!r}"
    )
    assert "E_CORPUS_PARITY" in region, (
        f"expected 'E_CORPUS_PARITY' returned in the baseline-delta block region, "
        f"region head={region[:400]!r}"
    )


# ═══ ACs 17-25 — added after Opus-gate REJECTED rev 1 (§4.1, spec §8) ═══════


# ─── AC17: uncommitted worktree deletion still BLOCKED (gate M4) ───────────


def test_ac17_worktree_only_uncommitted_deletion_still_blocked(tmp_path) -> None:
    """§2.1 pins the PR side at collect_corpus(None,...) (the WORKING TREE), not
    collect_corpus(head_ref,...). Here b.test.ts is deleted but NEVER committed,
    so a head_ref-tree implementation would see it present on BOTH sides and
    wrongly return OK/PASS. Real git fixture, no mocking of the UUT (§1l/§1i).
    """
    from bytedigger_engine.lib.corpus_parity import evaluate_preconditions  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_test_ts(repo, "a.test.ts", [("ok", True)])
    _write_test_ts(repo, "b.test.ts", [("ok2", True)])
    sha = _commit_all(repo, "seed")

    (repo / "b.test.ts").unlink()  # deleted in working tree, NOT committed

    result = evaluate_preconditions(sha, sha, "bun", cwd=str(repo))

    assert result.get("status") == "BLOCKED", (
        f"an UNCOMMITTED worktree deletion must still BLOCK — a head_ref-tree "
        f"implementation (collect_corpus(head_ref) instead of collect_corpus(None)) "
        f"would miss it entirely and return OK here, got {result!r}"
    )
    assert "E_CORPUS_DIVERGENCE" in (result.get("blocked_by") or []), f"got {result!r}"


# ─── AC18: kill-switch is never silent under --require-corpus-parity (edge 3) ─


def test_ac18_kill_switch_with_require_corpus_parity_is_counted_not_silent(tmp_path) -> None:
    results = tmp_path / "results.txt"
    results.write_text("(pass) suite > ok\n")
    ledger = _empty_ledger(tmp_path)

    env = _clean_env(HAL_BASELINE_DELTA_GATE="0")
    proc = _run_gate(
        [
            "--results", str(results), "--suite", "bun", "--ledger", str(ledger),
            "--require-corpus-parity",
        ],
        cwd=tmp_path, env=env,
    )

    data = _gate_json(proc)
    assert data.get("verdict") == "SKIPPED", f"got {data!r}"
    assert data.get("parity_requested_but_disabled") is True, (
        f"kill-switch + --require-corpus-parity must be COUNTED, not silent, got {data!r}"
    )
    assert (
        "WARN baseline-delta-gate: corpus parity requested but gate disabled by "
        "HAL_BASELINE_DELTA_GATE=0"
    ) in (proc.stderr or ""), (
        f"expected the exact WARN stderr line per §2.2, got stderr={proc.stderr!r}"
    )


# ─── AC19: would_block not masked by a stale `verdict` reading (gate M2) ────


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac19_would_block_not_masked_and_fails_closed_on_missing_delta_verdict(
    tmp_path, monkeypatch,
) -> None:
    import workflows._baseline_delta as bd_mod  # exists today, but not yet wired

    # Case A: verdict BLOCKED + delta_verdict FAIL + delta-enforce ON,
    # parity-enforce OFF -> would_block True (the delta lane is NOT masked by
    # a compound BLOCKED verdict).
    def fake_run_a(cmd, **kwargs):
        vj = {
            "verdict": "BLOCKED", "delta_verdict": "FAIL", "new_fails": ["x"],
            "ledgered": [], "baseline_source": "explicit",
            "blocked_by": ["E_CORPUS_DIVERGENCE"],
        }
        return _fake_completed(0, json.dumps(vj))

    monkeypatch.setattr(bd_mod.subprocess, "run", fake_run_a)
    cfg_a = _NamedFlagsCfg({
        "HAL_BASELINE_DELTA_ENFORCE": True, "HAL_CORPUS_PARITY_ENFORCE": False,
    })
    out_a = tmp_path / "out_a.txt"
    out_a.write_text("collected 1 item\n")
    result_a = bd_mod.run_baseline_delta_gate(
        str(out_a), "bun", str(tmp_path), 5, "s", lambda *a, **k: None, cfg=cfg_a,
    )
    assert result_a.get("would_block") is True, (
        f"verdict BLOCKED + delta_verdict FAIL + delta-enforce=1 must yield "
        f"would_block True (the delta lane must not be masked by the compound "
        f"BLOCKED verdict), got {result_a!r}"
    )

    # Case B: missing delta_verdict key entirely (old script via HAL_BASELINE_DELTA_BIN)
    # + delta-enforce ON -> fail-closed, would_block True regardless.
    def fake_run_b(cmd, **kwargs):
        vj = {
            "verdict": "BLOCKED", "new_fails": [], "ledgered": [],
            "baseline_source": "explicit",
        }  # NO delta_verdict key
        return _fake_completed(0, json.dumps(vj))

    monkeypatch.setattr(bd_mod.subprocess, "run", fake_run_b)
    cfg_b = _NamedFlagsCfg({
        "HAL_BASELINE_DELTA_ENFORCE": True, "HAL_CORPUS_PARITY_ENFORCE": False,
    })
    out_b = tmp_path / "out_b.txt"
    out_b.write_text("collected 1 item\n")
    result_b = bd_mod.run_baseline_delta_gate(
        str(out_b), "bun", str(tmp_path), 5, "s", lambda *a, **k: None, cfg=cfg_b,
    )
    assert result_b.get("would_block") is True, (
        f"a missing delta_verdict key under enforce must fail-closed to "
        f"would_block True, got {result_b!r}"
    )


# ─── AC20: phase_5 reachability on a GREEN group (gate M1 — blocker) ────────


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac20_run_baseline_delta_gate_call_not_nested_in_fail_only_branch() -> None:
    """§2.3 (gate M1): the existing call sits inside
    `if dt_result.exit_code != 0 or dt_result.n_failed > 0:` — on a fully GREEN
    group (exit_code 0, n_failed 0) the precondition NEVER runs, exactly the
    #1338 scenario. AST-based: find the Call node, find its enclosing If whose
    test matches the exit_code/n_failed disjunction, and assert the call's
    line range falls OUTSIDE that If's body range. Not a substring/indentation
    heuristic (§1w).
    """
    import ast

    phase5_path = _WORKFLOWS / "phase_5_implement.py"
    tree = ast.parse(phase5_path.read_text(encoding="utf-8"), filename=str(phase5_path))

    def _is_fail_only_test(test_node) -> bool:
        dumped = ast.dump(test_node)
        return (
            "dt_result" in dumped
            and "exit_code" in dumped
            and "n_failed" in dumped
            and ("NotEq" in dumped or "Gt" in dumped)
        )

    call_nodes = []
    fail_only_ifs = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_baseline_delta_gate"
        ):
            call_nodes.append(node)
        if isinstance(node, ast.If) and _is_fail_only_test(node.test):
            fail_only_ifs.append(node)

    assert call_nodes, (
        "expected at least one 'run_baseline_delta_gate(' call in phase_5_implement.py"
    )
    assert fail_only_ifs, (
        "expected to locate the "
        "'if dt_result.exit_code != 0 or dt_result.n_failed > 0:' If node via AST"
    )

    def _node_line_range(node):
        lo = node.lineno
        hi = getattr(node, "end_lineno", None)
        if hi is None:
            hi = max(
                (getattr(n, "lineno", lo) for n in ast.walk(node)), default=lo,
            )
        return lo, hi

    for call in call_nodes:
        call_line = call.lineno
        for if_node in fail_only_ifs:
            lo, hi = _node_line_range(if_node)
            assert not (lo <= call_line <= hi), (
                f"run_baseline_delta_gate call at line {call_line} is nested inside "
                f"the fail-only 'if dt_result.exit_code != 0 or dt_result.n_failed > 0:' "
                f"block spanning lines {lo}-{hi} — unreachable on a GREEN group (gate M1)"
            )


# ─── AC21: name-selective cfg.flag — GREEN keyed on the wrong name must fail (M6) ─


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac21_parity_block_is_name_selective_both_directions(tmp_path, monkeypatch) -> None:
    import workflows._baseline_delta as bd_mod  # exists today, but not yet wired

    def fake_run(cmd, **kwargs):
        vj = {
            "verdict": "BLOCKED", "delta_verdict": "PASS", "new_fails": [],
            "ledgered": [], "baseline_source": "explicit",
            "blocked_by": ["E_CORPUS_DIVERGENCE"],
        }
        return _fake_completed(0, json.dumps(vj))

    monkeypatch.setattr(bd_mod.subprocess, "run", fake_run)

    cfg_parity_on = _NamedFlagsCfg({
        "HAL_CORPUS_PARITY_ENFORCE": True, "HAL_BASELINE_DELTA_ENFORCE": False,
    })
    out1 = tmp_path / "out1.txt"
    out1.write_text("collected 1 item\n")
    result1 = bd_mod.run_baseline_delta_gate(
        str(out1), "bun", str(tmp_path), 5, "s", lambda *a, **k: None, cfg=cfg_parity_on,
    )
    assert result1.get("parity_block") is True, (
        f"HAL_CORPUS_PARITY_ENFORCE=1 (with delta-enforce=0) on a BLOCKED verdict "
        f"must give parity_block True; a GREEN keyed on the wrong env name would "
        f"invert this, got {result1!r}"
    )

    cfg_parity_off = _NamedFlagsCfg({
        "HAL_CORPUS_PARITY_ENFORCE": False, "HAL_BASELINE_DELTA_ENFORCE": True,
    })
    out2 = tmp_path / "out2.txt"
    out2.write_text("collected 1 item\n")
    result2 = bd_mod.run_baseline_delta_gate(
        str(out2), "bun", str(tmp_path), 5, "s", lambda *a, **k: None, cfg=cfg_parity_off,
    )
    assert result2.get("parity_block") is False, (
        f"HAL_CORPUS_PARITY_ENFORCE=0 (with delta-enforce=1) on a BLOCKED verdict "
        f"must give parity_block False; a GREEN keyed on the wrong env name would "
        f"invert this, got {result2!r}"
    )
    assert result2.get("would_block") is False, (
        f"rev2-N4: parity_block False and delta_verdict PASS must NOT over-block — "
        f"would_block must also be False here, got {result2!r}"
    )


# ─── AC22: branch closure across all four functions, error fixtures too (m2) ─


def test_ac22_all_four_functions_returned_statuses_in_precondition_codes(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import (  # ImportError pre-GREEN → FAIL
        PRECONDITION_CODES,
        check_base_freshness,
        collect_corpus,
        compare_corpus,
        evaluate_preconditions,
    )

    leaf_statuses = set()

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("root")
    root_sha = _commit_all(repo, "root")

    _git(repo, "checkout", "-b", "side")
    (repo / "f.txt").write_text("side")
    side_sha = _commit_all(repo, "side-commit")  # unique to 'side', NOT in main

    _git(repo, "checkout", "main")
    (repo / "g.txt").write_text("main-only")
    main_sha = _commit_all(repo, "main-commit")

    # check_base_freshness: OK, E_STALE_BASE, E_FRESHNESS_UNKNOWN
    leaf_statuses.add(check_base_freshness(root_sha, main_sha, cwd=str(repo))["status"])
    leaf_statuses.add(check_base_freshness(side_sha, main_sha, cwd=str(repo))["status"])
    leaf_statuses.add(
        check_base_freshness("bogus-ref-nope", main_sha, cwd=str(repo))["status"]
    )

    # collect_corpus: success + E_CORPUS_UNKNOWN (bad ref) + E_CORPUS_UNKNOWN (empty corpus)
    _write_test_ts(repo, "a.test.ts", [("ok", True)])
    tests_sha = _commit_all(repo, "add-tests")
    ok_result = collect_corpus(tests_sha, "bun", cwd=str(repo))
    assert "status" in ok_result, (
        f"collect_corpus MUST return a 'status' key on the success path — "
        f"a missing key must FAIL this AC, not be silently skipped: {ok_result!r}"
    )
    leaf_statuses.add(ok_result["status"])
    leaf_statuses.add(collect_corpus("bogus-ref-nope", "bun", cwd=str(repo))["status"])
    leaf_statuses.add(collect_corpus(root_sha, "bun", cwd=str(repo))["status"])  # no test files

    # compare_corpus: OK (superset) + E_CORPUS_DIVERGENCE
    ok_cmp = compare_corpus(["a.test.ts"], ["a.test.ts", "new.test.ts"])
    leaf_statuses.add(ok_cmp["status"])
    div_cmp = compare_corpus(["a.test.ts", "gone.test.ts"], ["a.test.ts"])
    leaf_statuses.add(div_cmp["status"])

    assert leaf_statuses, "expected at least one collected status — did not check"
    unknown = leaf_statuses - set(PRECONDITION_CODES)
    assert not unknown, (
        f"check_base_freshness/collect_corpus/compare_corpus returned status(es) "
        f"outside PRECONDITION_CODES: {unknown!r} (full set observed: {leaf_statuses!r})"
    )

    # evaluate_preconditions: composite status is 'OK'|'BLOCKED' (not itself a member
    # of PRECONDITION_CODES per §2.1 — see AC10, which already asserts status=='BLOCKED');
    # the closure obligation applies to the CODES it reports in blocked_by.
    blocked_result = evaluate_preconditions(side_sha, main_sha, "bun", cwd=str(repo))
    blocked_codes = set(blocked_result.get("blocked_by") or [])
    assert blocked_codes, f"expected a non-empty blocked_by, got {blocked_result!r}"
    unknown_blocked = blocked_codes - set(PRECONDITION_CODES)
    assert not unknown_blocked, (
        f"evaluate_preconditions blocked_by contained code(s) outside "
        f"PRECONDITION_CODES: {unknown_blocked!r}, full result={blocked_result!r}"
    )


# ─── AC23: env declaration channel HAL_CORPUS_ALLOW_REMOVED (no CLI flag) (m6/m7) ─


def test_ac23_env_declaration_channel_and_pytest_patterns_and_source(tmp_path) -> None:
    import fnmatch

    from bytedigger_engine.lib.corpus_parity import SUITE_TEST_PATTERNS, collect_corpus  # ImportError pre-GREEN → FAIL

    pytest_patterns = SUITE_TEST_PATTERNS["pytest"]
    assert any(fnmatch.fnmatch("test_x.py", p) for p in pytest_patterns), (
        f"expected pytest patterns to match test_x.py, patterns={pytest_patterns!r}"
    )
    assert any(fnmatch.fnmatch("x_test.py", p) for p in pytest_patterns), (
        f"expected pytest patterns to match x_test.py, patterns={pytest_patterns!r}"
    )
    assert not any(fnmatch.fnmatch("helper.py", p) for p in pytest_patterns), (
        f"expected pytest patterns to NOT match helper.py, patterns={pytest_patterns!r}"
    )

    repo, base_sha, head_sha, base_out, head_out, ledger, head_wt = _build_parity_fixture(tmp_path)

    base_side = collect_corpus(base_sha, "bun", cwd=str(repo))
    pr_side = collect_corpus(None, "bun", cwd=str(head_wt))
    assert base_side.get("source") in {"git-tree", "worktree"}, f"got {base_side!r}"
    assert pr_side.get("source") in {"git-tree", "worktree"}, f"got {pr_side!r}"

    env = _clean_env(HAL_CORPUS_ALLOW_REMOVED="b.test.ts")
    proc = _run_gate(
        [
            "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
            "--baseline", str(base_out),
            "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
        ],
        cwd=head_wt, env=env,
    )
    data = _gate_json(proc)
    assert data.get("verdict") == "PASS", (
        f"HAL_CORPUS_ALLOW_REMOVED (env channel, no CLI flag) must defeat the block "
        f"on the real divergent fixture, got {data!r} (stderr={proc.stderr!r})"
    )
    assert data.get("declared_removal_count") == 1, f"got {data!r}"


# ─── AC24: gh1318 protocol doc — every invocation line carries the flag (M5) ─


@pytest.mark.skip(reason="bd port: not portable — asserts a HAL governance document at <repo_root>/SHARED/memory/Decisions/gh1318_delta_measurement_protocol.md. bytedigger has no SHARED/ tree, and the `_BUILD_DIR.parents[2]` anchor resolves OUTSIDE the repo here (HAL nests engine_py three levels deep, bytedigger one). The CLI it documents IS ported and covered by the other 14 ACs of this file; only the doc-existence claim is HAL-only.")
def test_ac24_gh1318_protocol_doc_every_gate_invocation_carries_flag() -> None:
    repo_root = _BUILD_DIR.parents[2]
    doc_path = (
        repo_root / "SHARED" / "memory" / "Decisions"
        / "gh1318_delta_measurement_protocol.md"
    )
    assert doc_path.is_file(), f"expected protocol doc at {doc_path}"
    text = doc_path.read_text(encoding="utf-8")

    # rev2: join backslash line-continuations into ONE logical line BEFORE
    # checking — otherwise a flag pushed onto the next physical line (a
    # perfectly normal shell continuation) would not be seen as part of the
    # same invocation, letting a broken/incomplete doc line slip past.
    raw_lines = text.splitlines()
    joined_lines = []
    pending = ""
    for raw in raw_lines:
        line = (pending + " " + raw.lstrip()) if pending else raw
        pending = ""
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending = stripped[:-1].rstrip()
        else:
            joined_lines.append(line)
    if pending:
        joined_lines.append(pending)

    invocation_lines = [
        ln for ln in joined_lines
        if "baseline_delta_gate.py" in ln and "--" in ln
    ]
    assert invocation_lines, (
        "expected >=1 line that actually INVOKES baseline_delta_gate.py (i.e. carries "
        "CLI flags, not just prose mention) in the protocol doc — an EMPTY corpus of "
        "invocation lines means the doc was never checked (§2.4, gate M5)"
    )
    missing_flag = [ln for ln in invocation_lines if "--require-corpus-parity" not in ln]
    assert not missing_flag, (
        f"every baseline_delta_gate.py invocation line in the protocol doc must carry "
        f"--require-corpus-parity, missing on: {missing_flag!r}"
    )


# ─── AC25: `git status --porcelain -z` — spaces in names + staged renames (m4) ─


def test_ac25_porcelain_z_parsing_handles_spaces_and_staged_renames(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import collect_corpus, compare_corpus  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_test_ts(repo, "name with space.test.ts", [("ok", True)])
    _write_test_ts(repo, "rename_me.test.ts", [("ok2", True)])
    sha = _commit_all(repo, "seed")

    (repo / "name with space.test.ts").unlink()  # deleted, uncommitted, has a SPACE
    mv = _git(repo, "mv", "rename_me.test.ts", "renamed.test.ts")  # staged rename
    assert mv.returncode == 0, f"git mv failed: {mv.stderr!r}"

    base = collect_corpus(sha, "bun", cwd=str(repo))
    pr = collect_corpus(None, "bun", cwd=str(repo))
    cmp = compare_corpus(base.get("files") or [], pr.get("files") or [])

    assert "name with space.test.ts" in (cmp.get("only_in_base") or []), (
        f"a deleted test-file WITH A SPACE in its name must show up in only_in_base "
        f"(NUL-delimited porcelain parsing), got base={base!r} pr={pr!r} compare={cmp!r}"
    )
    assert "rename_me.test.ts" in (cmp.get("only_in_base") or []), (
        f"a staged rename of a test-file must put the OLD path in only_in_base "
        f"(rename == deletion+addition), got base={base!r} pr={pr!r} compare={cmp!r}"
    )


# ═══ ACs 26-28, 23a, 19a — added after Opus-gate REJECTED rev 2 (§4.1, spec §9) ═══


# ─── AC27: phase_5 positive whitelist — every enclosing If mentions ONLY stdout_path (N1) ─


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac27_phase5_reachability_whitelist_only_stdout_path_condition() -> None:
    """§2.3 rev2-N1 (gate N1): AC20's negative check (call not nested inside ONE
    specific fail-only If) can be silently defeated by relocating the call under
    a DIFFERENT condition entirely, e.g. `if failing_groups:` or a post-loop
    walk over `failing_group_tails` — neither is the fail-only If AC20 checks
    for, so AC20 alone would pass while the call remains unreachable on a
    GREEN group. This AC pins a POSITIVE whitelist instead: walk every
    enclosing `If` between the per-group loop body and the call, and assert
    EACH ONE's test expression mentions `stdout_path` and ONLY `stdout_path` —
    no `exit_code`, `n_failed`, `failing_groups`, or `failing_group_tails`, and
    no other name-based condition at all. A relocation under
    `if failing_groups:` MUST fail this AC.
    """
    import ast

    phase5_path = _WORKFLOWS / "phase_5_implement.py"
    tree = ast.parse(phase5_path.read_text(encoding="utf-8"), filename=str(phase5_path))

    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    call_nodes = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "run_baseline_delta_gate"
    ]
    assert call_nodes, (
        "expected at least one 'run_baseline_delta_gate(' call in phase_5_implement.py"
    )

    # §2.3 amended (gate rev3): whitelist, not blacklist — the ONLY permitted
    # token set in any enclosing If's test is a stdout_path presence check.
    ALLOWED_TOKENS = frozenset({"stdout_path", "dt_result", "getattr", "None"})

    for call in call_nodes:
        enclosing_ifs = []
        boundary_for = None
        node = call
        while node in parent:
            node = parent[node]
            if isinstance(node, ast.If):
                enclosing_ifs.append(node)
            if isinstance(node, (ast.For, ast.AsyncFor, ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(node, (ast.For, ast.AsyncFor)):
                    boundary_for = node
                break  # per-group loop (or enclosing function) boundary reached

        # §2.3 rev3 (gate rev3-1): the ancestor walk breaking at the FIRST For/
        # FunctionDef is vacuous if that nearest For is the post-loop walk over
        # failing_group_tails (nested inside `if failing_groups:`) — zero
        # enclosing Ifs would be forbidden-token-free by construction and the
        # positive check below would be silently skipped. Pin the boundary
        # itself: it MUST exist, and it MUST be the per-group loop
        # `for group in plan["groups"]:` (phase_5_implement.py:4533), never a
        # loop over failing_groups/failing_group_tails.
        assert boundary_for is not None, (
            f"no enclosing For loop found between the run_baseline_delta_gate "
            f"call at line {call.lineno} and its outer function boundary — an "
            f"empty instrument is not a check; expected to find the per-group "
            f"loop `for group in plan[\"groups\"]:`"
        )

        boundary_iter_tokens = set()
        for sub in ast.walk(boundary_for.iter):
            if isinstance(sub, ast.Name):
                boundary_iter_tokens.add(sub.id)
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                boundary_iter_tokens.add(sub.value)

        assert {"plan", "groups"} <= boundary_iter_tokens, (
            f"the boundary For loop enclosing the run_baseline_delta_gate call "
            f"at line {call.lineno} (For at line {boundary_for.lineno}) does not "
            f"iterate plan[\"groups\"] — got iter={ast.dump(boundary_for.iter)!r}; "
            f"the per-group loop is the only valid reachability boundary "
            f"(phase_5_implement.py:4533)"
        )
        assert not ({"failing_groups", "failing_group_tails"} & boundary_iter_tokens), (
            f"the boundary For loop at line {boundary_for.lineno} iterates over "
            f"failing_groups/failing_group_tails, not plan[\"groups\"] — this is "
            f"a relocation OUTSIDE the per-group loop where green groups are "
            f"never seen; iter={ast.dump(boundary_for.iter)!r}"
        )

        for if_node in enclosing_ifs:
            # §2.3 rev3 (gate rev3-2): whitelist by ALL identifier-ish nodes,
            # including string Constants — a blacklist on Name/Attribute alone
            # lets `getattr(dt_result, "n_failed", 0) > 0` slip through as an
            # ast.Constant untouched by name-based forbidden-list checks.
            tokens = set()
            for sub in ast.walk(if_node.test):
                if isinstance(sub, ast.Name):
                    tokens.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    tokens.add(sub.attr)
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    tokens.add(sub.value)

            disallowed = tokens - ALLOWED_TOKENS
            assert not disallowed, (
                f"enclosing If at line {if_node.lineno} on the path to "
                f"run_baseline_delta_gate mentions token(s) outside the "
                f"whitelist {sorted(disallowed)!r} (allowed: "
                f"{sorted(ALLOWED_TOKENS)!r}) — test={ast.dump(if_node.test)!r}"
            )
            assert "stdout_path" in tokens, (
                f"enclosing If at line {if_node.lineno} does not mention stdout_path at all — "
                f"the whitelist requires every enclosing If's test to be a stdout_path check, "
                f"test={ast.dump(if_node.test)!r}"
            )


# ─── AC26: exit-code ladder from §2.2 (delta FAIL > corpus divergence > clean) (N2) ─


def test_ac26_exit_code_ladder_delta_fail_beats_corpus_divergence(tmp_path) -> None:
    """§2.2 ladder (rev2-N2, AMENDED rev4 MAJOR-4): (a) delta FAIL +
    HAL_BASELINE_DELTA_GATE_ENFORCE=1 + corpus divergence -> exit 4, verdict
    BLOCKED in stdout (known regression takes priority over 'don't know');
    (b) divergence only + HAL_CORPUS_PARITY_ENFORCE=1 (no delta-enforce) ->
    exit 5. (a)/(b) share ONE real fixture: b.test.ts deleted uncommitted on
    the head side (corpus divergence), plus a NEW failing test injected into
    a.test.ts on the head side ONLY, absent from the base-side run (genuine
    delta FAIL via two real `bun test` runs).

    (c) rev4 AMENDMENT: under the new ladder ('BLOCKED' ⇒ exit 5 ALWAYS,
    §10 MAJOR-4) the divergent fixture used in (a)/(b) is BLOCKED regardless
    of enforce flags, so it can no longer serve as the exit-0 rung — that is
    exactly the input the amendment reclassifies. (c) now uses a genuinely
    CLEAN, separate real-git+real-bun fixture (no divergence, no delta fail)
    to pin the one case that legitimately still exits 0.
    """
    bun = shutil.which("bun")
    assert bun is not None, (
        "bun binary not found on PATH — AC26 requires REAL `bun test` runs, "
        "this is a hard AC failure, not a skip"
    )

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_test_ts(repo, "a.test.ts", [("shared alpha", True), ("only base red", False)])
    _write_test_ts(repo, "b.test.ts", [("b passes", True)])
    base_sha = _commit_all(repo, "base")

    _write_test_ts(repo, "c.test.ts", [("c passes", True)])
    head_sha = _commit_all(repo, "head")  # head commit contains a+b+c, a unchanged from base

    base_wt = tmp_path / "wt_base"
    head_wt = tmp_path / "wt_head"
    r1 = _git(repo, "worktree", "add", "--detach", str(base_wt), base_sha)
    assert r1.returncode == 0, f"git worktree add (base) failed: {r1.stderr!r}"
    r2 = _git(repo, "worktree", "add", "--detach", str(head_wt), head_sha)
    assert r2.returncode == 0, f"git worktree add (head) failed: {r2.stderr!r}"

    # corpus divergence: uncommitted deletion in the head worktree ONLY
    (head_wt / "b.test.ts").unlink()

    # delta FAIL: a NEW failing test injected into a.test.ts, head worktree ONLY,
    # uncommitted — absent from the base-side run output entirely
    _write_test_ts(head_wt, "a.test.ts", [
        ("shared alpha", True), ("only base red", False), ("new head-only red", False),
    ])

    base_out = tmp_path / "base.out"
    head_out = tmp_path / "head.out"
    bp = subprocess.run([bun, "test", "."], cwd=str(base_wt), capture_output=True, text=True)
    hp = subprocess.run([bun, "test", "."], cwd=str(head_wt), capture_output=True, text=True)
    base_out.write_text(bp.stdout + bp.stderr)
    head_out.write_text(hp.stdout + hp.stderr)

    head_text = head_out.read_text()
    base_text = base_out.read_text()
    assert "new head-only red" in head_text, (
        f"expected the injected head-only red in the real bun output, got {head_text!r}"
    )
    assert "new head-only red" not in base_text, (
        f"expected the injected red ABSENT from the base-side real bun output, got {base_text!r}"
    )

    ledger = _empty_ledger(tmp_path)
    base_args = [
        "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
        "--baseline", str(base_out),
        "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
    ]

    # (a) delta FAIL + HAL_BASELINE_DELTA_GATE_ENFORCE=1 + divergence -> exit 4
    env_a = _clean_env(HAL_BASELINE_DELTA_GATE_ENFORCE="1")
    proc_a = _run_gate(base_args, cwd=head_wt, env=env_a)
    data_a = _gate_json(proc_a)
    assert data_a.get("delta_verdict") == "FAIL", f"got {data_a!r}"
    assert data_a.get("verdict") == "BLOCKED", (
        f"stdout verdict must remain BLOCKED even though exit is 4, got {data_a!r}"
    )
    assert proc_a.returncode == 4, (
        f"expected exit 4 per §2.2 ladder step 1 (delta FAIL takes priority over the "
        f"corpus-divergence exit 5), got {proc_a.returncode}; "
        f"stdout={proc_a.stdout!r} stderr={proc_a.stderr!r}"
    )

    # (b) divergence only + HAL_CORPUS_PARITY_ENFORCE=1 (no delta-enforce) -> exit 5
    env_b = _clean_env(HAL_CORPUS_PARITY_ENFORCE="1")
    proc_b = _run_gate(base_args, cwd=head_wt, env=env_b)
    data_b = _gate_json(proc_b)
    assert data_b.get("verdict") == "BLOCKED", f"got {data_b!r}"
    assert proc_b.returncode == 5, (
        f"expected exit 5 per §2.2 ladder step 2, got {proc_b.returncode}; "
        f"stdout={proc_b.stdout!r} stderr={proc_b.stderr!r}"
    )

    # (c) rev4 AMENDMENT: a genuinely CLEAN, SEPARATE fixture (no divergence,
    # no delta fail) is the only case that still exits 0 — the divergent
    # fixture above is BLOCKED under the new ladder regardless of flags.
    clean_repo = tmp_path / "clean_repo_ac26c"
    _init_repo(clean_repo)
    _write_test_ts(clean_repo, "a.test.ts", [("ok", True)])
    clean_sha = _commit_all(clean_repo, "seed")
    clean_results = tmp_path / "clean_results_ac26c.txt"
    cp = subprocess.run([bun, "test", "."], cwd=str(clean_repo), capture_output=True, text=True)
    clean_results.write_text(cp.stdout + cp.stderr)
    clean_ledger = _empty_ledger(tmp_path, name="clean-known-reds-ac26c.md")
    proc_c = _run_gate(
        [
            "--results", str(clean_results), "--suite", "bun", "--ledger", str(clean_ledger),
            "--require-corpus-parity", "--base-ref", clean_sha, "--head-ref", clean_sha,
        ],
        cwd=clean_repo, env=_clean_env(),
    )
    data_c = _gate_json(proc_c)
    assert data_c.get("verdict") == "PASS", (
        f"expected verdict PASS on a genuinely clean input, got {data_c!r}"
    )
    assert proc_c.returncode == 0, (
        f"expected exit 0 per §2.2 ladder step 3 on a CLEAN input (no divergence, "
        f"no delta fail, no enforce flags), got {proc_c.returncode}; "
        f"stdout={proc_c.stdout!r} stderr={proc_c.stderr!r}"
    )


# ─── AC28: `corpus_parity` key present in normal-mode JSON (rev2-m1) ────────


def test_ac28_corpus_parity_key_present_in_normal_mode_json(tmp_path) -> None:
    """§2.2: without --require-corpus-parity the gate must still land in
    NORMAL mode (verdict PASS/FAIL, not ERROR) and carry corpus_parity ==
    'NOT_REQUESTED'. The original fixture ran in a non-git tmp_path with no
    --base-sha, so resolve_baseline's `git rev-parse origin/main` failed and
    the gate short-circuited into the ERROR-mode object (no corpus_parity
    key at all per §2.2) — a fixture defect, not a contract failure. Passing
    a deterministic --base-sha (as AC9 does) plus an explicit --cache-dir
    reaches normal mode without needing a real repo.
    """
    results = tmp_path / "results.txt"
    results.write_text("(pass) suite > ok\n")
    ledger = _empty_ledger(tmp_path)
    cache_dir = tmp_path / "cache"

    env_no_flag = _clean_env()
    proc_no_flag = _run_gate(
        [
            "--results", str(results), "--suite", "bun", "--ledger", str(ledger),
            "--base-sha", "deadbeef0000", "--cache-dir", str(cache_dir),
        ],
        cwd=tmp_path, env=env_no_flag,
    )
    data_no_flag = _gate_json(proc_no_flag)
    assert data_no_flag.get("verdict") in {"PASS", "FAIL"}, (
        f"expected NORMAL-mode verdict (PASS/FAIL), not ERROR/SKIPPED/SAVED, "
        f"got {data_no_flag!r}"
    )
    assert data_no_flag.get("corpus_parity") == "NOT_REQUESTED", (
        f"expected corpus_parity=='NOT_REQUESTED' without the flag, got {data_no_flag!r}"
    )

    repo = tmp_path / "repo2"
    _init_repo(repo)
    _write_test_ts(repo, "a.test.ts", [("ok", True)])
    head_sha = _commit_all(repo, "head")
    results2 = tmp_path / "results2.txt"
    results2.write_text("(pass) suite > ok\n")

    env_flag = _clean_env()
    proc_flag = _run_gate(
        [
            "--results", str(results2), "--suite", "bun", "--ledger", str(ledger),
            "--require-corpus-parity", "--base-ref", head_sha, "--head-ref", head_sha,
        ],
        cwd=repo, env=env_flag,
    )
    data_flag = _gate_json(proc_flag)
    assert data_flag.get("corpus_parity") in {"OK", "BLOCKED"}, (
        f"expected corpus_parity in {{'OK','BLOCKED'}} with the flag on, got {data_flag!r}"
    )


# ─── AC23a: `source` pinned BY NAME per ref kind (rev2-m7) ──────────────────


def test_ac23a_collect_corpus_source_pinned_by_ref_kind(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import collect_corpus  # ImportError pre-GREEN → FAIL

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_test_ts(repo, "a.test.ts", [("ok", True)])
    sha = _commit_all(repo, "seed")

    ref_result = collect_corpus(sha, "bun", cwd=str(repo))
    assert ref_result.get("source") == "git-tree", (
        f"expected source=='git-tree' for collect_corpus(<ref>,...) — a membership "
        f"check against {{'git-tree','worktree'}} would pass on the WRONG value too, "
        f"got {ref_result!r}"
    )

    worktree_result = collect_corpus(None, "bun", cwd=str(repo))
    assert worktree_result.get("source") == "worktree", (
        f"expected source=='worktree' for collect_corpus(None,...), got {worktree_result!r}"
    )


# ─── AC19a: emitted verdict event carries only_in_base + declared_removal_count (rev2-m5) ─


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac19a_verdict_event_carries_only_in_base_and_declared_removal_count(
    tmp_path, monkeypatch,
) -> None:
    import workflows._baseline_delta as bd_mod  # exists today, but not yet wired

    def fake_run(cmd, **kwargs):
        vj = {
            "verdict": "BLOCKED", "delta_verdict": "PASS", "new_fails": [], "ledgered": [],
            "baseline_source": "explicit", "blocked_by": ["E_CORPUS_DIVERGENCE"],
            "only_in_base": ["b.test.ts"], "declared_removal_count": 0,
        }
        return _fake_completed(0, json.dumps(vj))

    monkeypatch.setattr(bd_mod.subprocess, "run", fake_run)
    cfg = _NamedFlagsCfg({
        "HAL_BASELINE_DELTA_ENFORCE": True, "HAL_CORPUS_PARITY_ENFORCE": True,
    })

    events = []

    def emit(name, payload, **kw):
        events.append((name, payload))

    stdout_path = tmp_path / "out.txt"
    stdout_path.write_text("collected 1 item\n")

    bd_mod.run_baseline_delta_gate(
        str(stdout_path), "bun", str(tmp_path), 5, "test_step", emit, cfg=cfg,
    )

    verdict_events = [e for e in events if e[0] == "baseline_delta_gate_verdict"]
    assert verdict_events, f"expected a baseline_delta_gate_verdict event, got {events!r}"
    payload = verdict_events[0][1]
    assert payload.get("only_in_base") == ["b.test.ts"], (
        f"expected only_in_base threaded into the emitted event, got {payload!r}"
    )
    assert payload.get("declared_removal_count") == 0, (
        f"expected declared_removal_count threaded into the emitted event, got {payload!r}"
    )
    assert payload.get("blocked_by") == ["E_CORPUS_DIVERGENCE"], (
        f"expected blocked_by (already required by AC15) still present, got {payload!r}"
    )


# ═══ AC31 — flow-blind hole in AC20/AC27: preceding-sibling continue/break/return (§4.1) ═══


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac31_call_not_bypassed_by_preceding_sibling_continue_break_return() -> None:
    """§4.1 (Opus-named remaining hole, rev5 WHITELIST): AC20/AC27 only inspect
    ANCESTOR `If` nodes of the run_baseline_delta_gate call. Both are blind to
    a bypass that inserts a SIBLING statement BEFORE the statement reaching
    the call, at any nesting level between the per-group `For` and the
    call — e.g.

        if dt_result.exit_code == 0 and dt_result.n_failed == 0:
            continue                                  # green group skipped

    placed ahead of the existing fail-only If. That shape satisfies AC20
    (call still outside the fail-only If's *own* body-range is irrelevant —
    AC20 only checks the call isn't NESTED inside it) and AC27 (every
    ancestor If of the call still whitelists to stdout_path) while the gate
    is *never invoked* for a GREEN group — the literal #1338 defect.

    Opus gate rejected the prior "narrowed" predicate as a one-spelling
    BLACKLIST (`if not dt_result.n_failed: continue`, `< 1`, `is_green = ...;
    if is_green: continue`, `if not failing_this_group: continue`, `<= 0`,
    `in (0,)` all slipped past it). Replaced with a real WHITELIST (§4.1
    updated row): a preceding-sibling Continue/Break/Return, found anywhere
    within a preceding sibling statement, with a governing enclosing If, is a
    VIOLATION *unless* that governing If's test mentions `maxsize` anywhere
    (Name or Attribute, e.g. `sys.maxsize`) — the only spelling used by the
    legitimate timeout guard at phase_5_implement.py:~4546. A `Return` nested
    inside an inner `FunctionDef`/`AsyncFunctionDef`/`Lambda` does not count
    (it belongs to an inner scope, not the enclosing per-group flow).

    This is a static AST walk: locate the per-group For (iterating
    `plan["groups"]`) and the call, then for every statement list on the
    path between them, inspect every statement PRECEDING the one that
    contains the call for an embedded Continue/Break/Return matching the
    whitelist above. Also rejects the call sitting inside any comprehension.
    §1i/§1w: an instrument that cannot locate the For or the call is a hard
    failure, not a silent pass.
    """
    import ast

    def _ac31_find_violations(tree, *, source_label):
        parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[child] = node

        call_nodes = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "run_baseline_delta_gate"
        ]
        assert call_nodes, (
            f"[{source_label}] expected >=1 'run_baseline_delta_gate(' call — an "
            f"instrument that finds nothing to inspect is not a check (§1i/§1w)"
        )

        def _iter_tokens(iter_node):
            tokens = set()
            for sub in ast.walk(iter_node):
                if isinstance(sub, ast.Name):
                    tokens.add(sub.id)
                elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    tokens.add(sub.value)
            return tokens

        def _test_mentions_maxsize(test_node):
            # WHITELIST (§4.1 rev5): the ONLY thing that suppresses a
            # preceding-sibling Continue/Break/Return is the governing If's
            # test mentioning the EXACT `maxsize` sentinel (e.g. `sys.maxsize`)
            # — the exact spelling used by the legitimate timeout guard. A
            # substring match (`"maxsize" in sub.id`) would let a local named
            # e.g. `maxsize_ok` buy suppression too, which is NOT the real
            # sentinel — so this checks exact identifier equality, not
            # substring containment. Everything else (however it spells
            # "n_failed is falsy") is a violation.
            for sub in ast.walk(test_node):
                if isinstance(sub, ast.Name) and sub.id == "maxsize":
                    return True
                if isinstance(sub, ast.Attribute) and sub.attr == "maxsize":
                    return True
            return False

        def _return_in_nested_scope(ctrl_node, stop_at):
            # A Return nested inside an inner FunctionDef/AsyncFunctionDef/
            # Lambda does not count — it belongs to an inner scope, not the
            # enclosing per-group control flow.
            if not isinstance(ctrl_node, ast.Return):
                return False
            cur = ctrl_node
            while cur in parent and cur is not stop_at:
                cur = parent[cur]
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    return True
                if cur is stop_at:
                    break
            return False

        def _governing_if(ctrl_node, stop_at):
            cur = ctrl_node
            while cur in parent and cur is not stop_at:
                cur = parent[cur]
                if isinstance(cur, ast.If):
                    return cur
                if cur is stop_at:
                    break
            return None

        violations = []

        for call in call_nodes:
            # item 4: call must not sit inside any comprehension
            node = call
            while node in parent:
                node = parent[node]
                if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    violations.append(
                        f"call at line {call.lineno} sits inside a "
                        f"{type(node).__name__} comprehension"
                    )
                    break
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break

            # locate the per-group boundary For (iterates plan["groups"]) — item 5 vacuity guard
            boundary_for = None
            node = call
            while node in parent:
                node = parent[node]
                if isinstance(node, (ast.For, ast.AsyncFor)):
                    if {"plan", "groups"} <= _iter_tokens(node.iter):
                        boundary_for = node
                        break
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
            assert boundary_for is not None, (
                f"[{source_label}] no per-group For (`for group in plan[\"groups\"]:`) "
                f"found enclosing the run_baseline_delta_gate call at line {call.lineno} "
                f"— an empty instrument is not a check"
            )

            chain = [call]
            node = call
            while node is not boundary_for and node in parent:
                node = parent[node]
                chain.append(node)
            assert chain[-1] is boundary_for, (
                f"[{source_label}] could not walk from the call at line {call.lineno} "
                f"up to the boundary For at line {boundary_for.lineno} — instrument "
                f"failed to establish the path (§1w)"
            )

            for i in range(len(chain) - 1):
                child, container = chain[i], chain[i + 1]
                for attr in ("body", "orelse", "finalbody"):
                    lst = getattr(container, attr, None)
                    if lst and child in lst:
                        idx = lst.index(child)
                        for preceding in lst[:idx]:
                            for sub in ast.walk(preceding):
                                if isinstance(sub, (ast.Continue, ast.Break, ast.Return)):
                                    if _return_in_nested_scope(sub, preceding):
                                        continue
                                    gov = _governing_if(sub, preceding)
                                    if gov is not None and not _test_mentions_maxsize(gov.test):
                                        tokens = sorted({
                                            t.id if isinstance(t, ast.Name) else t.attr
                                            for t in ast.walk(gov.test)
                                            if isinstance(t, (ast.Name, ast.Attribute))
                                        })
                                        violations.append(
                                            f"[{source_label}] preceding sibling at line "
                                            f"{preceding.lineno} contains a "
                                            f"{type(sub).__name__} governed by If at line "
                                            f"{gov.lineno} mentioning tokens {tokens!r} — "
                                            f"bypasses the call to run_baseline_delta_gate "
                                            f"at line {call.lineno}"
                                        )
                        break

        return violations

    phase5_path = _WORKFLOWS / "phase_5_implement.py"
    tree = ast.parse(phase5_path.read_text(encoding="utf-8"), filename=str(phase5_path))
    violations = _ac31_find_violations(tree, source_label=str(phase5_path))
    assert not violations, (
        "AC31: phase_5_implement.py has a preceding-sibling continue/break/return "
        "on the path to run_baseline_delta_gate that can bypass the gate for a "
        f"GREEN group — {violations!r}"
    )

    # ── two-sided discrimination check (item 5): run the SAME predicate over an
    # inline reproduction of the #1338 bypass shape and require it to REPORT a
    # violation — otherwise the instrument above would be vacuous (§1i/§1w).
    bypass_source = '''
def do_step(step, plan, git_cwd, prev, _emit_safe, run_test_command, run_baseline_delta_gate):
    baseline_delta_blocks = []
    for group in plan["groups"]:
        argv = group["argv"]
        kind = group["kind"]
        dt_result = run_test_command(argv, git_cwd, timeout=120)
        if dt_result.exit_code == 0 and dt_result.n_failed == 0:
            continue
        if dt_result.exit_code != 0 or dt_result.n_failed > 0:
            pass
        if getattr(dt_result, "stdout_path", None):
            _bd = run_baseline_delta_gate(
                dt_result.stdout_path, "bun", git_cwd, 5, step, _emit_safe,
            )
'''
    bypass_tree = ast.parse(bypass_source, filename="<ac31-bypass-fixture>")
    bypass_violations = _ac31_find_violations(bypass_tree, source_label="<ac31-bypass-fixture>")
    assert bypass_violations, (
        "AC31 instrument FAILED to discriminate: the classic #1338 bypass shape "
        "(`if exit_code==0 and n_failed==0: continue` placed as a sibling BEFORE "
        "the fail-only If that reaches run_baseline_delta_gate) reported ZERO "
        "violations — the predicate is vacuous and would not have caught #1338"
    )

    # ── negative direction: the legitimate timeout guard (sys.maxsize sentinel,
    # non-zero exit_code constant) placed in the EXACT same preceding-sibling
    # position must NOT be reported — otherwise the narrowed predicate is
    # over-broad and would flag phase_5_implement.py:~4546 (a real production
    # guard, not a bypass).
    guard_source = '''
def do_step(step, plan, git_cwd, prev, _emit_safe, run_test_command, run_baseline_delta_gate):
    baseline_delta_blocks = []
    for group in plan["groups"]:
        argv = group["argv"]
        kind = group["kind"]
        dt_result = run_test_command(argv, git_cwd, timeout=120)
        if dt_result.exit_code == 124 and dt_result.n_failed == sys.maxsize:
            return None
        if dt_result.exit_code != 0 or dt_result.n_failed > 0:
            pass
        if getattr(dt_result, "stdout_path", None):
            _bd = run_baseline_delta_gate(
                dt_result.stdout_path, "bun", git_cwd, 5, step, _emit_safe,
            )
'''
    guard_tree = ast.parse(guard_source, filename="<ac31-timeout-guard-fixture>")
    guard_violations = _ac31_find_violations(guard_tree, source_label="<ac31-timeout-guard-fixture>")
    assert not guard_violations, (
        "AC31 instrument OVER-FLAGGED: the legitimate timeout guard "
        "(`if exit_code == 124 and n_failed == sys.maxsize: return`) placed as "
        "a preceding sibling must NOT be reported (its test can never be TRUE "
        "for a green group), but got violations: "
        f"{guard_violations!r}"
    )

    # ── MINOR fix re-proof: the exact-identifier check must not be foolable
    # by a substring match on `maxsize`. A local named e.g. `maxsize_ok` is
    # NOT the real `sys.maxsize` sentinel and must still be reported; only the
    # real `sys.maxsize` attribute access is exempt.
    maxsize_substring_source = '''
def do_step(step, plan, git_cwd, prev, _emit_safe, run_test_command, run_baseline_delta_gate):
    baseline_delta_blocks = []
    for group in plan["groups"]:
        argv = group["argv"]
        kind = group["kind"]
        dt_result = run_test_command(argv, git_cwd, timeout=120)
        maxsize_ok = dt_result.n_failed == 0
        if dt_result.n_failed == maxsize_ok:
            continue
        if dt_result.exit_code != 0 or dt_result.n_failed > 0:
            pass
        if getattr(dt_result, "stdout_path", None):
            _bd = run_baseline_delta_gate(
                dt_result.stdout_path, "bun", git_cwd, 5, step, _emit_safe,
            )
'''
    maxsize_substring_tree = ast.parse(
        maxsize_substring_source, filename="<ac31-maxsize-substring-fixture>"
    )
    maxsize_substring_violations = _ac31_find_violations(
        maxsize_substring_tree, source_label="<ac31-maxsize-substring-fixture>"
    )
    assert maxsize_substring_violations, (
        "AC31 instrument was fooled by a SUBSTRING match on `maxsize`: "
        "`if dt_result.n_failed == maxsize_ok: continue` (a local named "
        "`maxsize_ok`, not the real `sys.maxsize` sentinel) reported ZERO "
        "violations — the predicate must require EXACT identifier equality, "
        "not `'maxsize' in name`"
    )

    real_maxsize_source = '''
def do_step(step, plan, git_cwd, prev, _emit_safe, run_test_command, run_baseline_delta_gate):
    baseline_delta_blocks = []
    for group in plan["groups"]:
        argv = group["argv"]
        kind = group["kind"]
        dt_result = run_test_command(argv, git_cwd, timeout=120)
        if dt_result.exit_code == 124 and dt_result.n_failed == sys.maxsize:
            return None
        if dt_result.exit_code != 0 or dt_result.n_failed > 0:
            pass
        if getattr(dt_result, "stdout_path", None):
            _bd = run_baseline_delta_gate(
                dt_result.stdout_path, "bun", git_cwd, 5, step, _emit_safe,
            )
'''
    real_maxsize_tree = ast.parse(real_maxsize_source, filename="<ac31-real-maxsize-fixture>")
    real_maxsize_violations = _ac31_find_violations(
        real_maxsize_tree, source_label="<ac31-real-maxsize-fixture>"
    )
    assert not real_maxsize_violations, (
        "AC31 instrument over-flagged the REAL `sys.maxsize` guard after "
        f"tightening to exact-identifier match: {real_maxsize_violations!r}"
    )

    # ── whitelist re-proof (Opus gate rejection): the prior "narrowed"
    # predicate was a one-spelling BLACKLIST that missed every rewording
    # below. The WHITELIST predicate (violation unless the governing If's
    # test mentions `maxsize`) must catch ALL of them.
    reported_bodies = {
        "not_n_failed": '''
        if not dt_result.n_failed:
            continue
''',
        "n_failed_lt_1": '''
        if dt_result.n_failed < 1:
            continue
''',
        "is_green_flag": '''
        is_green = not dt_result.n_failed
        if is_green:
            continue
''',
        "not_failing_this_group": '''
        if not failing_this_group:
            continue
''',
    }
    for shape_name, body in reported_bodies.items():
        src = (
            "def do_step(step, plan, git_cwd, prev, _emit_safe, "
            "run_test_command, run_baseline_delta_gate):\n"
            "    baseline_delta_blocks = []\n"
            "    for group in plan[\"groups\"]:\n"
            "        argv = group[\"argv\"]\n"
            "        kind = group[\"kind\"]\n"
            "        dt_result = run_test_command(argv, git_cwd, timeout=120)\n"
            "        failing_this_group = dt_result.exit_code != 0 or dt_result.n_failed > 0\n"
            f"{body}"
            "        if getattr(dt_result, \"stdout_path\", None):\n"
            "            _bd = run_baseline_delta_gate(\n"
            "                dt_result.stdout_path, \"bun\", git_cwd, 5, step, _emit_safe,\n"
            "            )\n"
        )
        shape_tree = ast.parse(src, filename=f"<ac31-whitelist-{shape_name}-fixture>")
        shape_violations = _ac31_find_violations(
            shape_tree, source_label=f"<ac31-whitelist-{shape_name}-fixture>"
        )
        assert shape_violations, (
            f"AC31 WHITELIST regression: shape '{shape_name}' rewords the same "
            "green-group bypass without mentioning 0/maxsize constants and MUST "
            f"still be reported, but got zero violations — {src}"
        )

    # ── nested-scope Return must NOT count: a Return inside an inner
    # FunctionDef/Lambda belongs to the inner scope, not the enclosing
    # per-group flow, and must not suppress-or-trigger a violation report
    # by itself (no governing If wraps the outer flow in this fixture).
    nested_return_source = '''
def do_step(step, plan, git_cwd, prev, _emit_safe, run_test_command, run_baseline_delta_gate):
    baseline_delta_blocks = []
    for group in plan["groups"]:
        argv = group["argv"]
        kind = group["kind"]
        dt_result = run_test_command(argv, git_cwd, timeout=120)
        def _inner_helper():
            if True:
                return None
        _inner_helper()
        if getattr(dt_result, "stdout_path", None):
            _bd = run_baseline_delta_gate(
                dt_result.stdout_path, "bun", git_cwd, 5, step, _emit_safe,
            )
'''
    nested_return_tree = ast.parse(
        nested_return_source, filename="<ac31-nested-scope-return-fixture>"
    )
    nested_return_violations = _ac31_find_violations(
        nested_return_tree, source_label="<ac31-nested-scope-return-fixture>"
    )
    assert not nested_return_violations, (
        "AC31 instrument OVER-FLAGGED: a Return nested inside an inner "
        "FunctionDef belongs to that inner scope, not the enclosing "
        f"per-group flow, and must not be reported — {nested_return_violations!r}"
    )


# ═══ ACs 32-41 — rev 4: dispatcher's pre-merge adversarial review (§10, §10.1) ═══


# ─── AC32: local origin/main tracking ref lags the TRUE remote (§10 MAJOR-1) ─


def test_ac32_stale_remote_ref_blocks_when_local_tracking_lags_real_remote(tmp_path) -> None:
    """§10 MAJOR-1: `merge-base --is-ancestor` only proves LOCAL ancestry — a
    local `origin/main` tracking ref that was never fetched can be stale
    relative to the TRUE remote while still passing local ancestry. Real bare
    remote, two independent clones: repo1 pushes base_sha and never fetches
    again; repo2 (a second, independent clone) later pushes a THIRD commit,
    advancing the true remote main. repo1's local origin/main ref is
    untouched. A merge-base-only implementation cannot see this divergence
    (it never talks to the remote) and MUST fail this AC.
    """
    from bytedigger_engine.lib.corpus_parity import (
        PRECONDITION_CODES,
        check_base_freshness,
        evaluate_preconditions,
    )

    bare = tmp_path / "origin.git"
    r0 = subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)], capture_output=True, text=True,
    )
    assert r0.returncode == 0, f"git init --bare failed: {r0.stderr!r}"

    repo1 = tmp_path / "repo1"
    c1 = subprocess.run(["git", "clone", str(bare), str(repo1)], capture_output=True, text=True)
    assert c1.returncode == 0, f"clone repo1 failed: {c1.stderr!r}"
    (repo1 / "f.txt").write_text("1")
    base_sha = _commit_all(repo1, "base")
    p1 = _git(repo1, "push", "origin", "main")
    assert p1.returncode == 0, f"push from repo1 failed: {p1.stderr!r}"

    # local descendant commit in repo1, NEVER pushed
    (repo1 / "g.txt").write_text("2")
    head_sha = _commit_all(repo1, "head-local")

    # a SECOND, independent clone advances the TRUE remote — repo1 never fetches
    repo2 = tmp_path / "repo2"
    c2 = subprocess.run(["git", "clone", str(bare), str(repo2)], capture_output=True, text=True)
    assert c2.returncode == 0, f"clone repo2 failed: {c2.stderr!r}"
    (repo2 / "h.txt").write_text("3")
    _commit_all(repo2, "advance-remote")
    p2 = _git(repo2, "push", "origin", "main")
    assert p2.returncode == 0, f"push from repo2 failed: {p2.stderr!r}"

    local_tracking = _git(repo1, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    assert local_tracking == base_sha, (
        f"fixture defect: repo1's local origin/main must still be base_sha "
        f"(never re-fetched), got {local_tracking!r} vs base_sha={base_sha!r}"
    )

    # §10 MAJOR-A (blocking, rev4-amendment): the LIVE production input is the
    # SHORT spelling `origin/main` — baseline_delta_gate.py:293 defaults
    # --base-ref origin/main and _baseline_delta.py passes no --base-ref at
    # all. A classifier keyed on `startswith("refs/remotes/")` would treat
    # the short form as "not a remote-tracking ref" and silently skip into
    # the remote_checked:false branch, passing all ACs while missing the
    # ONLY live input. Per spec, classification MUST go through
    # `git rev-parse --symbolic-full-name`, so exercise BOTH spellings (plus
    # the `@{u}` shorthand) against the SAME fixture and assert identity on
    # remote_checked, not truthiness.
    for spelling in ("origin/main", "refs/remotes/origin/main", "@{u}"):
        result = check_base_freshness(spelling, head_sha, cwd=str(repo1))
        assert result.get("status") == "E_STALE_REMOTE_REF", (
            f"[{spelling!r}] a local origin/main lagging the TRUE remote main "
            f"must be caught by a real `ls-remote` comparison regardless of "
            f"ref spelling (classification must use "
            f"`git rev-parse --symbolic-full-name`, not a string-prefix "
            f"check) — a merge-base-only OR prefix-string-only implementation "
            f"cannot see this, got {result!r}"
        )
        assert result.get("remote_checked") is True, (
            f"[{spelling!r}] expected remote_checked to be EXACTLY True "
            f"(identity, not merely truthy) for a real remote-tracking ref, "
            f"got {result!r}"
        )

    assert "E_STALE_REMOTE_REF" in PRECONDITION_CODES, (
        f"E_STALE_REMOTE_REF must be a member of the closed §1n code set, "
        f"got {PRECONDITION_CODES!r}"
    )

    for spelling in ("origin/main", "refs/remotes/origin/main"):
        composite = evaluate_preconditions(spelling, head_sha, "bun", cwd=str(repo1))
        assert composite.get("status") == "BLOCKED", f"[{spelling!r}] got {composite!r}"
        assert "E_STALE_REMOTE_REF" in (composite.get("blocked_by") or []), (
            f"[{spelling!r}] expected E_STALE_REMOTE_REF in evaluate_preconditions "
            f"blocked_by, got {composite!r}"
        )


# ─── AC33: remote unreachable → E_REMOTE_UNREACHABLE, fail-closed ───────────


def test_ac33_remote_unreachable_fails_closed_not_ok(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import PRECONDITION_CODES, check_base_freshness

    bare = tmp_path / "origin.git"
    r0 = subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)], capture_output=True, text=True,
    )
    assert r0.returncode == 0, f"git init --bare failed: {r0.stderr!r}"

    repo1 = tmp_path / "repo1"
    c1 = subprocess.run(["git", "clone", str(bare), str(repo1)], capture_output=True, text=True)
    assert c1.returncode == 0, f"clone failed: {c1.stderr!r}"
    (repo1 / "f.txt").write_text("1")
    base_sha = _commit_all(repo1, "base")
    p1 = _git(repo1, "push", "origin", "main")
    assert p1.returncode == 0, f"push failed: {p1.stderr!r}"

    (repo1 / "g.txt").write_text("2")
    head_sha = _commit_all(repo1, "head")

    shutil.rmtree(bare)  # the remote is now GENUINELY unreachable

    # §10 MAJOR-A: exercise BOTH the short (LIVE production) spelling and the
    # long form against the same now-unreachable-remote fixture; both must
    # classify identically via `git rev-parse --symbolic-full-name` and both
    # must report remote_checked is True (a genuine attempt was made).
    for spelling in ("origin/main", "refs/remotes/origin/main", "@{u}"):
        result = check_base_freshness(spelling, head_sha, cwd=str(repo1))
        assert result.get("status") != "OK", (
            f"[{spelling!r}] an unreachable remote must NEVER be treated as "
            f"fresh (fail-closed), got {result!r}"
        )
        assert result.get("status") == "E_REMOTE_UNREACHABLE", (
            f"[{spelling!r}] got {result!r}"
        )
        assert result.get("remote_checked") is True, (
            f"[{spelling!r}] expected remote_checked to be EXACTLY True (a "
            f"genuine remote-reachability attempt was made and failed), "
            f"got {result!r}"
        )
    assert "E_REMOTE_UNREACHABLE" in PRECONDITION_CODES, (
        f"E_REMOTE_UNREACHABLE must be a member of the closed §1n code set, "
        f"got {PRECONDITION_CODES!r}"
    )
    assert base_sha  # keep base_sha referenced; fixture-defect guard above matters more


# ─── AC34: raw sha base_ref → remote_checked is False, non-empty reason ─────


def test_ac34_raw_sha_base_ref_skips_remote_check_but_records_reason(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import check_base_freshness

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("1")
    base_sha = _commit_all(repo, "base")
    (repo / "f.txt").write_text("2")
    head_sha = _commit_all(repo, "head")

    result = check_base_freshness(base_sha, head_sha, cwd=str(repo))

    assert result.get("status") == "OK", f"got {result!r}"
    assert result.get("remote_checked") is False, (
        f"a raw sha is NOT a remote-tracking ref — remote_checked must be "
        f"exactly False (not merely falsy/absent, not a missing key), "
        f"got {result!r}"
    )
    reason = result.get("remote_check_reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"expected a non-empty remote_check_reason explaining WHY the remote "
        f"check was skipped — silent absence of the key is not acceptable, "
        f"got {result!r}"
    )


# ─── AC35: empty / unparseable results log → E_RESULTS_UNPARSEABLE (§10 MAJOR-3) ─


def test_ac35_empty_or_unparseable_results_block_as_e_results_unparseable(tmp_path) -> None:
    """§10 MAJOR-3: an empty results file or a results file containing only a
    collector crash line must never certify PASS — 'nothing to check' printed
    as 'matched' is exactly the #1428 class this lot is meant to close.
    """
    from bytedigger_engine.lib.corpus_parity import PRECONDITION_CODES

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "test_x.py").write_text("def test_ok(): assert True\n")
    head_sha = _commit_all(repo, "seed")
    ledger = _empty_ledger(tmp_path)

    for label, content in (
        ("empty", ""),
        ("import-error", "ImportError: collection failed\n"),
    ):
        results = tmp_path / f"results_{label}.txt"
        results.write_text(content)
        env = _clean_env()
        proc = _run_gate(
            [
                "--results", str(results), "--suite", "pytest", "--ledger", str(ledger),
                "--require-corpus-parity", "--base-ref", head_sha, "--head-ref", head_sha,
            ],
            cwd=repo, env=env,
        )
        data = _gate_json(proc)
        assert data.get("verdict") != "PASS", (
            f"[{label}] an empty/unparseable results log must never certify "
            f"PASS, got {data!r}"
        )
        assert data.get("verdict") == "BLOCKED", f"[{label}] got {data!r}"
        assert "E_RESULTS_UNPARSEABLE" in (data.get("blocked_by") or []), (
            f"[{label}] expected E_RESULTS_UNPARSEABLE in blocked_by, got {data!r}"
        )
        assert proc.returncode == 5, (
            f"[{label}] BLOCKED must exit 5 per the rev4 ladder, "
            f"got {proc.returncode}; stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )

    assert "E_RESULTS_UNPARSEABLE" in PRECONDITION_CODES, (
        f"E_RESULTS_UNPARSEABLE must be a member of the closed §1n code set, "
        f"got {PRECONDITION_CODES!r}"
    )


# ─── AC36: summary present but zero tests → E_EMPTY_RUN (§10 MAJOR-3) ───────


def test_ac36_summary_present_but_zero_tests_blocks_as_e_empty_run(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import PRECONDITION_CODES

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "test_x.py").write_text("def test_ok(): assert True\n")
    # rev4-MAJOR-A follow-up: a real a.test.ts must exist so the bun corpus is
    # NON-EMPTY — otherwise E_CORPUS_UNKNOWN also fires alongside E_EMPTY_RUN,
    # compounding two blockages where this AC means to isolate E_EMPTY_RUN.
    _write_test_ts(repo, "a.test.ts", [("a ok", True)])
    head_sha = _commit_all(repo, "seed")
    ledger = _empty_ledger(tmp_path)

    for label, suite, content in (
        ("bun-zero", "bun", "Ran 0 tests across 0 files. [1.00ms]\n"),
        ("pytest-no-tests", "pytest", "no tests ran in 0.01s\n"),
    ):
        results = tmp_path / f"results_{label}.txt"
        results.write_text(content)
        env = _clean_env()
        proc = _run_gate(
            [
                "--results", str(results), "--suite", suite, "--ledger", str(ledger),
                "--require-corpus-parity", "--base-ref", head_sha, "--head-ref", head_sha,
            ],
            cwd=repo, env=env,
        )
        data = _gate_json(proc)
        assert data.get("verdict") == "BLOCKED", f"[{label}] got {data!r}"
        assert "E_EMPTY_RUN" in (data.get("blocked_by") or []), (
            f"[{label}] expected E_EMPTY_RUN in blocked_by (a parseable summary "
            f"reporting ZERO tests is not proof of comparability), got {data!r}"
        )
        assert "E_CORPUS_UNKNOWN" not in (data.get("blocked_by") or []), (
            f"[{label}] the corpus is non-empty (real a.test.ts committed) — "
            f"E_CORPUS_UNKNOWN must NOT also fire, this AC isolates E_EMPTY_RUN "
            f"alone, got {data!r}"
        )

    assert "E_EMPTY_RUN" in PRECONDITION_CODES, (
        f"E_EMPTY_RUN must be a member of the closed §1n code set, "
        f"got {PRECONDITION_CODES!r}"
    )


# ─── AC37: run_evidence + corpus_scope present on a real bun run (§10 MAJOR-3) ─


def test_ac37_run_evidence_and_corpus_scope_present_on_real_bun_run(tmp_path) -> None:
    repo, base_sha, head_sha, base_out, head_out, ledger, head_wt = _build_parity_fixture(tmp_path)

    env = _clean_env()
    proc = _run_gate(
        [
            "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
            "--baseline", str(base_out),
            "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
        ],
        cwd=head_wt, env=env,
    )
    data = _gate_json(proc)
    run_evidence = data.get("run_evidence")
    assert isinstance(run_evidence, dict), (
        f"expected a run_evidence dict in the normal-mode JSON, got {data!r}"
    )
    assert run_evidence.get("has_summary") is True, (
        f"expected has_summary True for a real bun run with a 'Ran N tests' "
        f"summary line, got {run_evidence!r}"
    )
    assert run_evidence.get("collected_files_seen", 0) > 0, (
        f"expected collected_files_seen > 0 on a real bun run whose stdout "
        f"carries per-file headers, got {run_evidence!r}"
    )
    assert data.get("corpus_scope") == "repo-wide", (
        f"expected corpus_scope=='repo-wide' — an explicit admission that "
        f"parity compares the WHOLE repo, not plan['groups'] argv subset, "
        f"got {data!r}"
    )

    # rev4 follow-up (AC37): collected_not_in_corpus is the field that actually
    # detects "parity measured a different run than the one that produced
    # --results" — no prior AC asserted it. On this clean real fixture (the
    # results file was produced by the SAME bun run over the SAME corpus) it
    # must be present and empty.
    collected_not_in_corpus = data.get("collected_not_in_corpus")
    assert collected_not_in_corpus == [], (
        f"expected collected_not_in_corpus == [] on a clean matching fixture "
        f"(results and corpus come from the same run), got {data!r}"
    )

    # Now construct a mismatch: a file collected in the RUN OUTPUT that is
    # absent from the PR-side corpus entirely (e.g. run against a stale copy
    # of the tree). Inject a synthetic per-file header for a file that does
    # NOT exist in head_wt's corpus at all.
    mismatched_out = tmp_path / "mismatched_head.out"
    mismatched_out.write_text(
        head_out.read_text() + "\nzzz_not_in_corpus.test.ts:\n(pass) zzz > ok\n"
    )
    proc2 = _run_gate(
        [
            "--results", str(mismatched_out), "--suite", "bun", "--ledger", str(ledger),
            "--baseline", str(base_out),
            "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
        ],
        cwd=head_wt, env=env,
    )
    data2 = _gate_json(proc2)
    assert "zzz_not_in_corpus.test.ts" in (data2.get("collected_not_in_corpus") or []), (
        f"expected a run-collected file absent from the PR corpus to be "
        f"enumerated in collected_not_in_corpus, got {data2!r}"
    )


# ─── AC38: exit ladder rev4 — BLOCKED always non-zero; delta beats it; clean is 0 ─


def test_ac38_exit_ladder_blocked_always_nonzero_delta_still_beats_it(tmp_path) -> None:
    """§10 MAJOR-4 ladder: (1) delta FAIL + HAL_BASELINE_DELTA_GATE_ENFORCE=1 ->
    4; (2) else BLOCKED -> 5 REGARDLESS of HAL_CORPUS_PARITY_ENFORCE; (3) else
    -> 0. 'BLOCKED and exit 0' must be impossible under ANY flag combination.
    """
    bun = shutil.which("bun")
    assert bun is not None, "bun binary not found on PATH — AC38 requires REAL bun test runs"

    # ── leg 2: BLOCKED, no enforce flags at all -> exit 5 (rev4: no longer 0) ──
    repo, base_sha, head_sha, base_out, head_out, ledger, head_wt = _build_parity_fixture(tmp_path)
    base_args = [
        "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
        "--baseline", str(base_out),
        "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
    ]
    proc_none = _run_gate(base_args, cwd=head_wt, env=_clean_env())
    data_none = _gate_json(proc_none)
    assert data_none.get("verdict") == "BLOCKED", f"got {data_none!r}"
    assert proc_none.returncode == 5, (
        f"rev4: BLOCKED must exit 5 with NO enforce flags at all — the prior "
        f"warn-only exit-0 contract is REVERSED, got {proc_none.returncode}; "
        f"stdout={proc_none.stdout!r} stderr={proc_none.stderr!r}"
    )

    # ── leg 1: delta FAIL + HAL_BASELINE_DELTA_GATE_ENFORCE=1 (+ same divergence) -> 4 ──
    (head_wt / "a.test.ts").write_text(
        'import { test, expect } from "bun:test";\n'
        'test("shared alpha", () => { expect(1).toBe(1); });\n'
        'test("only base red", () => { expect(1).toBe(2); });\n'
        'test("new head-only red", () => { expect(1).toBe(2); });\n'
    )
    hp2 = subprocess.run([bun, "test", "."], cwd=str(head_wt), capture_output=True, text=True)
    head_out.write_text(hp2.stdout + hp2.stderr)
    assert "new head-only red" in head_out.read_text(), (
        f"expected the injected head-only red in the real bun output, "
        f"got {head_out.read_text()!r}"
    )
    proc_delta = _run_gate(base_args, cwd=head_wt, env=_clean_env(HAL_BASELINE_DELTA_GATE_ENFORCE="1"))
    data_delta = _gate_json(proc_delta)
    assert data_delta.get("delta_verdict") == "FAIL", f"got {data_delta!r}"
    assert proc_delta.returncode == 4, (
        f"expected exit 4 (delta FAIL + delta-enforce beats corpus divergence), "
        f"got {proc_delta.returncode}; stdout={proc_delta.stdout!r} stderr={proc_delta.stderr!r}"
    )

    # ── leg 3: a genuinely CLEAN, separate fixture -> exit 0 ──
    clean_repo = tmp_path / "clean_repo_ac38"
    _init_repo(clean_repo)
    _write_test_ts(clean_repo, "a.test.ts", [("ok", True)])
    clean_sha = _commit_all(clean_repo, "seed")
    clean_results = tmp_path / "clean_results_ac38.txt"
    cp = subprocess.run([bun, "test", "."], cwd=str(clean_repo), capture_output=True, text=True)
    clean_results.write_text(cp.stdout + cp.stderr)
    clean_ledger = _empty_ledger(tmp_path, name="clean-known-reds-ac38.md")
    proc_clean = _run_gate(
        [
            "--results", str(clean_results), "--suite", "bun", "--ledger", str(clean_ledger),
            "--require-corpus-parity", "--base-ref", clean_sha, "--head-ref", clean_sha,
        ],
        cwd=clean_repo, env=_clean_env(),
    )
    data_clean = _gate_json(proc_clean)
    assert data_clean.get("verdict") == "PASS", f"got {data_clean!r}"
    assert proc_clean.returncode == 0, (
        f"expected exit 0 for a genuinely clean input, got {proc_clean.returncode}; "
        f"stdout={proc_clean.stdout!r} stderr={proc_clean.stderr!r}"
    )


# ─── AC39: HAL_CORPUS_PARITY_ENFORCE no longer affects the exit code ────────


def test_ac39_corpus_parity_enforce_flag_no_longer_affects_exit_code(tmp_path) -> None:
    """§10 MAJOR-4: HAL_CORPUS_PARITY_ENFORCE now governs ONLY the engine-level
    parity_block/E_CORPUS_PARITY branch (§2.3), not the CLI exit code. The
    SAME divergent input must give exit 5 whether the flag is unset OR '1'.
    """
    repo, base_sha, head_sha, base_out, head_out, ledger, head_wt = _build_parity_fixture(tmp_path)
    base_args = [
        "--results", str(head_out), "--suite", "bun", "--ledger", str(ledger),
        "--baseline", str(base_out),
        "--require-corpus-parity", "--base-ref", base_sha, "--head-ref", head_sha,
    ]

    proc_unset = _run_gate(base_args, cwd=head_wt, env=_clean_env())
    proc_enforced = _run_gate(base_args, cwd=head_wt, env=_clean_env(HAL_CORPUS_PARITY_ENFORCE="1"))

    assert proc_unset.returncode == 5, (
        f"expected exit 5 with HAL_CORPUS_PARITY_ENFORCE unset, "
        f"got {proc_unset.returncode}; stdout={proc_unset.stdout!r} stderr={proc_unset.stderr!r}"
    )
    assert proc_enforced.returncode == 5, (
        f"expected exit 5 with HAL_CORPUS_PARITY_ENFORCE=1 too — the flag must "
        f"NOT change the exit code on the SAME divergent input, "
        f"got {proc_enforced.returncode}; stdout={proc_enforced.stdout!r} "
        f"stderr={proc_enforced.stderr!r}"
    )
    assert proc_unset.returncode == proc_enforced.returncode, (
        f"expected identical exit codes regardless of HAL_CORPUS_PARITY_ENFORCE, "
        f"got unset={proc_unset.returncode} enforced={proc_enforced.returncode}"
    )


# ─── AC40: delta FAIL + parity_block both reported, not `elif`-masked ───────


@pytest.mark.skip(reason="hal#1145 Ф1 declared gap: asserts wiring in workflows/phase_5_implement.py (UUT-B). Ф1 ships the additive module only; wiring existing product files belongs to Ф B. Re-enable in Ф B — this AC is the wiring's proof.")
def test_ac40_delta_and_parity_block_both_reported_not_elif_masked(tmp_path) -> None:
    """§10 'Взято в этот шип из MINOR': today's phase_5_implement.py keys

        if _bd.get("parity_block"):
            parity_blocks.append(...)
        elif _bd.get("would_block"):
            baseline_delta_blocks.append(...)

    so a group with BOTH parity_block AND would_block True is counted ONLY
    into parity_blocks — the real delta regression (E_BASELINE_DELTA) is
    masked and reported as E_CORPUS_PARITY instead. The fix makes the two
    checks INDEPENDENT (`if` / `if`, not `if` / `elif`), so a group with both
    flags true populates BOTH lists and E_BASELINE_DELTA (checked first,
    higher priority) still fires. This AST check locates the enclosing `If`
    whose test mentions 'parity_block' inside the per-group loop and asserts
    it is NOT chained via `elif` to a sibling `If` testing 'would_block' —
    today's source IS such an elif chain, so this AC must fail now.
    """
    import ast

    phase5_path = _WORKFLOWS / "phase_5_implement.py"
    tree = ast.parse(phase5_path.read_text(encoding="utf-8"), filename=str(phase5_path))

    def _test_mentions(test_node, token) -> bool:
        return any(
            (isinstance(n, ast.Constant) and isinstance(n.value, str) and token in n.value)
            for n in ast.walk(test_node)
        )

    all_ifs = [n for n in ast.walk(tree) if isinstance(n, ast.If)]
    parity_ifs = [n for n in all_ifs if _test_mentions(n.test, "parity_block")]
    would_block_ifs = [n for n in all_ifs if _test_mentions(n.test, "would_block")]
    assert parity_ifs, (
        "expected to locate an `if ...parity_block...:` If node inside the "
        "per-group loop in phase_5_implement.py"
    )
    assert would_block_ifs, (
        "expected to locate an `if ...would_block...:` If node inside the "
        "per-group loop in phase_5_implement.py"
    )

    def _elif_chain_targets(if_node):
        """Walk an `elif` chain (single-If orelse) and return every nested
        If's test-mentions set, so BOTH orders (parity->would_block and
        would_block->parity) and longer chains (parity elif X elif
        would_block) are all caught — not just the depth-1 direct pairing.
        """
        chained = []
        node = if_node
        seen = set()
        while (
            len(node.orelse) == 1
            and isinstance(node.orelse[0], ast.If)
            and id(node.orelse[0]) not in seen
        ):
            nxt = node.orelse[0]
            seen.add(id(nxt))
            chained.append(nxt)
            node = nxt
        return chained

    elif_masked = []
    # direction 1: parity_block ... elif ... would_block (anywhere in chain)
    for if_node in parity_ifs:
        for chained in _elif_chain_targets(if_node):
            if _test_mentions(chained.test, "would_block"):
                elif_masked.append(("parity_block-then-would_block", if_node.lineno))
                break

    # direction 2 (reversed order): would_block ... elif ... parity_block
    for if_node in would_block_ifs:
        for chained in _elif_chain_targets(if_node):
            if _test_mentions(chained.test, "parity_block"):
                elif_masked.append(("would_block-then-parity_block", if_node.lineno))
                break

    assert not elif_masked, (
        f"phase_5_implement.py keys parity_block and would_block via a "
        f"mutually-exclusive `if`/`elif` chain (either order, any chain "
        f"length) at {elif_masked!r} — a group with BOTH flags true is "
        f"counted into only ONE list, masking the real E_BASELINE_DELTA "
        f"regression as E_CORPUS_PARITY (or vice versa). The two checks must "
        f"be INDEPENDENT `if` statements, not chained via `elif` in either "
        f"direction."
    )


# ─── AC41: E_STALE_BASE description drops the false 'freshness window' claim ─


def test_ac41_stale_base_error_description_drops_false_freshness_window_claim() -> None:
    """§10 'Взято в этот шип из MINOR': `error_codes.ERROR_CODES['E_STALE_BASE']`
    currently reads '...older than the configured freshness window...' — there
    is no freshness WINDOW; the real mechanism is git ancestry / merge-base.
    A correct fix does not resurrect a false rationale for a correct behavior.
    """
    from bytedigger_engine import error_codes as _error_codes_mod

    description = _error_codes_mod.ERROR_CODES.get("E_STALE_BASE", "")
    assert "freshness window" not in description, (
        f"E_STALE_BASE description must NOT claim a 'freshness window' — there "
        f"is no window, only merge-base ancestry (§10 false-rationale fix), "
        f"got description={description!r}"
    )
    assert ("merge-base" in description) or ("ancestor" in description) or (
        "ancestry" in description
    ), (
        f"expected the E_STALE_BASE description to mention merge-base/ancestry "
        f"instead, got description={description!r}"
    )


# ─── AC42: parse_run_evidence normalizes CI log decoration (GH Actions groups + ANSI) ─


def test_ac42_parse_run_evidence_normalizes_group_prefixes_and_ansi() -> None:
    """A real CI defect: bun's stdout in GitHub Actions is wrapped with
    `::group::` / `::endgroup::` grouping markers and ANSI colour escapes.
    The bun file-header regex is anchored (`^\\S+\\.test\\.ts:\\s*$`,
    MULTILINE) so an undecorated local log parses fine but a decorated CI
    log captures the file name WITH the `::group::` prefix glued on —
    every collected file then looks absent from the corpus, a permanently
    noisy false E_CORPUS_DIVERGENCE-adjacent signal in exactly the
    environment the gate exists to protect. Fix: parse_run_evidence must
    strip `::group::`/`::endgroup::` prefixes and ANSI SGR escapes before
    matching file headers and the summary line.
    """
    from bytedigger_engine.lib.corpus_parity import parse_run_evidence

    ESC = "\x1b"
    decorated = (
        "::group::run\n"
        "a.test.ts:\n"
        "(pass) alpha > ok\n"
        f"{ESC}[32mc.test.ts:{ESC}[0m\n"
        "(pass) charlie > ok\n"
        "::endgroup::\n"
        f"::group::{ESC}[1mRan 2 tests across 2 files. [12.00ms]{ESC}[0m\n"
        "::endgroup::\n"
    )
    clean = (
        "a.test.ts:\n"
        "(pass) alpha > ok\n"
        "c.test.ts:\n"
        "(pass) charlie > ok\n"
        "Ran 2 tests across 2 files. [12.00ms]\n"
    )

    decorated_result = parse_run_evidence(decorated, "bun")
    clean_result = parse_run_evidence(clean, "bun")

    decorated_files = decorated_result.get("collected_files") or []
    assert decorated_files == ["a.test.ts", "c.test.ts"], (
        f"expected bare file paths after normalization, got {decorated_files!r} "
        f"(full result={decorated_result!r}) — today's parser glues the "
        f"'::group::' prefix onto the captured file name instead of "
        f"stripping it"
    )
    for entry in decorated_files:
        assert "::group::" not in entry and "::endgroup::" not in entry and ESC not in entry, (
            f"decorated collected_files entry {entry!r} still carries CI "
            f"log decoration, full result={decorated_result!r}"
        )

    assert decorated_result.get("has_summary") is True, (
        f"expected has_summary True even when the summary line itself "
        f"carries a '::group::' prefix and ANSI escapes, "
        f"got {decorated_result!r}"
    )
    assert decorated_result.get("n_tests") == 2, (
        f"expected n_tests==2 parsed from the decorated summary line, "
        f"got {decorated_result!r}"
    )

    # two-sided (§1-anti-pattern guard): the clean, undecorated log must
    # yield the EXACT SAME collected_files — a fix that simply drops every
    # '::group::'-prefixed line (rather than normalizing) would pass the
    # decorated assertions above by accident on a differently-shaped
    # fixture but diverge here, or vice versa a fix tuned only to the
    # clean path would fail the decorated assertions above.
    clean_files = clean_result.get("collected_files") or []
    assert clean_files == ["a.test.ts", "c.test.ts"], (
        f"expected the clean fixture to already parse to bare paths, "
        f"got {clean_files!r} (full result={clean_result!r})"
    )
    assert clean_files == decorated_files, (
        f"expected identical collected_files between the clean and "
        f"decorated logs, got clean={clean_files!r} "
        f"decorated={decorated_files!r}"
    )


# ─── AC43: parse_bun_fails / parse_pytest_fails normalize CI log decoration ──


def test_ac43_fail_name_parsers_normalize_group_prefixes_and_ansi() -> None:
    """Same defect class as AC42, worse: `_PYTEST_FAIL_RE` / `_BUN_FAIL_RE`
    in baseline_delta_gate.py are anchored at line start (`^(FAILED|ERROR)`,
    `^\\s*\\(fail\\)`), so a real GitHub Actions `::group::` wrapper prefix or
    a leading ANSI colour escape on an otherwise-genuine failure line makes it
    either not match at all (the failure is silently DROPPED from the current
    set — a real regression then reads as delta-zero) or match with the
    decoration glued onto the captured name (a phantom new_fail that never
    matches any baseline/ledger entry). Both directions poison the delta
    verdict this module exists to compute. Fix: strip `::group::`/`::endgroup::`
    prefixes and ANSI SGR escapes before matching fail lines, in both parsers.
    """
    if str(_BUILD_DIR) not in sys.path:
        sys.path.insert(0, str(_BUILD_DIR))
    from baseline_delta_gate import parse_bun_fails, parse_pytest_fails  # noqa: PLC0415

    ESC = "\x1b"

    # ── bun ──────────────────────────────────────────────────────────────
    bun_clean = (
        "(fail) suite > alpha\n"
        "(pass) suite > beta\n"
        "(fail) suite > charlie [0.10ms]\n"
    )
    bun_decorated = (
        "::group::run\n"
        "(fail) suite > alpha\n"
        "(pass) suite > beta\n"
        f"{ESC}[31m(fail) suite > charlie [0.10ms]{ESC}[0m\n"
        "::endgroup::\n"
    )

    bun_clean_result = parse_bun_fails(bun_clean)
    bun_decorated_result = parse_bun_fails(bun_decorated)

    assert bun_clean_result == ["suite > alpha", "suite > charlie"], (
        f"sanity: expected the clean bun log (with duration-tail "
        f"normalization intact) to parse to ['suite > alpha', "
        f"'suite > charlie'], got {bun_clean_result!r}"
    )
    for name in bun_decorated_result:
        assert "::group::" not in name and "::endgroup::" not in name and ESC not in name, (
            f"decorated bun fail id {name!r} still carries CI log decoration "
            f"(full result={bun_decorated_result!r})"
        )
    assert len(bun_decorated_result) == len(bun_clean_result), (
        f"a decorated failure line must not be silently DROPPED — that is "
        f"exactly how a real regression reads as delta-zero; "
        f"clean={bun_clean_result!r} decorated={bun_decorated_result!r}"
    )
    assert bun_decorated_result == bun_clean_result, (
        f"decorated and clean bun fail-id lists must be IDENTICAL — neither "
        f"smaller (silent drop) nor differently-named (phantom new_fail); "
        f"clean={bun_clean_result!r} decorated={bun_decorated_result!r}"
    )

    # ── pytest ───────────────────────────────────────────────────────────
    pytest_clean = (
        "FAILED tests/x.py::test_y\n"
        "ERROR tests/z.py::test_w\n"
    )
    pytest_decorated = (
        "::group::run\n"
        "FAILED tests/x.py::test_y\n"
        f"{ESC}[31mERROR tests/z.py::test_w{ESC}[0m\n"
        "::endgroup::\n"
    )

    pytest_clean_result = parse_pytest_fails(pytest_clean)
    pytest_decorated_result = parse_pytest_fails(pytest_decorated)

    assert pytest_clean_result == ["tests/x.py::test_y", "tests/z.py::test_w"], (
        f"sanity: expected the clean pytest log to parse to "
        f"['tests/x.py::test_y', 'tests/z.py::test_w'], got {pytest_clean_result!r}"
    )
    for nodeid in pytest_decorated_result:
        assert "::group::" not in nodeid and ESC not in nodeid, (
            f"decorated pytest node-id {nodeid!r} still carries CI log "
            f"decoration (full result={pytest_decorated_result!r})"
        )
    assert len(pytest_decorated_result) == len(pytest_clean_result), (
        f"a decorated pytest FAILED/ERROR line must not be silently DROPPED "
        f"— that is exactly how a real regression reads as delta-zero; "
        f"clean={pytest_clean_result!r} decorated={pytest_decorated_result!r}"
    )
    assert pytest_decorated_result == pytest_clean_result, (
        f"decorated and clean pytest node-id lists must be IDENTICAL — "
        f"neither smaller (silent drop) nor differently-named (phantom "
        f"new_fail); clean={pytest_clean_result!r} "
        f"decorated={pytest_decorated_result!r}"
    )
