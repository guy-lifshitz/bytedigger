"""bytedigger port note: AC7 is omitted — it runs the real bun lane over a
host-repository test subset that this repository does not contain.

RED tests for GH1740 (ebdfdc7b) — `corpus_scope` must be DERIVED, not a literal.

Spec (rev 5, post Opus-gate REJECTED rounds 1-4 — lot NARROWED to the
HONESTY half only; the ENFORCEMENT half split off to issue #1741). Rev 5:
AC9 split into two MEASURED groups (three of seven branches are a
precondition failure BY CONSTRUCTION); AC2b/AC3a new; AC5 fixed to drive a
REAL pytest fixture instead of reusing the bun repo:
SHARED/memory/Decisions/2026-09-03_ebdfdc7b_corpus_scope_unverified.md

UUT does not exist yet / is not yet wired:
  - baseline_delta_gate.py:498 hardcodes `"corpus_scope": "repo-wide"` — must
    become derived from RunEvidence.n_files vs corpus size across the SEVEN
    branches of AC2 (repo-wide / partial / unverified x5 reasons), plus
    corpus_files_total/corpus_files_run/corpus_files_unrun/corpus_scope_reason.
  - lib/corpus_parity.py: RunEvidence has no `n_files` key yet;
    _parse_bun_evidence uses .search() (FIRST match only) and discards
    _BUN_SUMMARY_RE.group(2) (files count).

Rev 3 explicitly does NOT add any enforcement: no E_CORPUS_UNRUN code, no
HAL_CORPUS_UNRUN_ENFORCE flag, no flip-by token, no blocked_by/exit-5 wiring.
AC4 below is a NEGATIVE pin guarding exactly that boundary.

Per §1q: imports of not-yet-existing symbols/keys are deferred/defensive
(dict .get / getattr with sentinel) so collection succeeds cleanly and each
test fails individually at ASSERT time with an expected-vs-actual message,
never at import/collection time.

§1i: all fixtures below except AC7 are REAL git repos built fresh under
tmp_path per test — no shared/singleton resource, no timing race. AC7 is the
one §1l side-effect AC and deliberately runs a REAL `bun test` (idiom lifted
from test_gh1338_corpus_parity_gate.py's _build_parity_fixture: shutil.which
hard-asserted, real subprocess.run) over a small real subset of THIS repo's
own corpus, then feeds that captured real log to the real gate against the
REAL repo-wide corpus — not a synthetic log, not a mock of collect_corpus.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ─── repo/fixture paths ──────────────────────────────────────────────────────

_THIS = Path(__file__).resolve()
_ENGINE_ROOT = _THIS.parents[1]        # …/engine_py/
_BUILD_DIR = _THIS.parents[2]          # …/SYSTEM/cli/build/
_SCRIPT = _BUILD_DIR / "baseline_delta_gate.py"


from bytedigger_engine import flags_catalog  # noqa: E402  (already exists — safe at module level)
from bytedigger_engine.lib.corpus_parity import (  # noqa: E402  (already exist)
    BLOCKED_BY_ORDER,
    PRECONDITION_CODES,
)


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


def _run_gate(args, cwd, env):
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _empty_ledger(tmp_path, name="known-reds.md"):
    p = tmp_path / name
    p.write_text("| Suite | Red | Scope | Issue | Kill-by | Class |\n|---|---|---|---|---|---|\n")
    return p


def _empty_baseline(tmp_path, name="baseline.txt"):
    """An explicit --baseline with no fail lines — bypasses git-based
    resolve_baseline entirely (no network/origin dependency), so gate output
    reflects only what THIS test controls (delta_verdict PASS, verdict OK)."""
    p = tmp_path / name
    p.write_text("")
    return p


def _clean_env(**overrides):
    env = dict(os.environ)
    env.pop("HAL_BASELINE_DELTA_GATE_ENFORCE", None)
    env.pop("HAL_CORPUS_PARITY_ENFORCE", None)
    env.pop("HAL_BASELINE_DELTA_GATE", None)
    env.pop("HAL_CORPUS_ALLOW_REMOVED", None)
    env.pop("HAL_KNOWN_REDS_TODAY", None)
    env.pop("HAL_CORPUS_UNRUN_ENFORCE", None)
    env.update(overrides)
    return env


def _gate_json(proc):
    """Assert-time RED per §1q: a gate subprocess must produce parseable JSON
    on stdout, AS THE LAST NON-EMPTY LINE (AC9). A bare
    json.decoder.JSONDecodeError is an incidental crash, not a diagnostic
    failure — surface returncode/stdout/stderr explicitly.
    """
    out = proc.stdout or ""
    assert out.strip(), (
        f"gate subprocess produced EMPTY stdout (no JSON to parse); "
        f"returncode={proc.returncode!r} stdout={out!r} "
        f"stderr_tail={(proc.stderr or '')[-2000:]!r}"
    )
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # MINOR-J fence: this file's _gate_json reads only the LAST non-empty
    # stdout line, but the sibling test_gh1338_corpus_parity_gate.py's own
    # _gate_json does json.loads() on the WHOLE stdout. A GREEN that adds
    # even one diagnostic print() would silently pass THIS file's
    # last-line tolerance while reddening all 67 gh1338 tests. Diagnostics
    # belong on stderr (the gate already writes plenty there today — a
    # `baseline_delta_gate: ...` line and `WARN baseline-delta-gate: ...`
    # lines — and that is fine and must stay unbroken by this fence).
    assert len(lines) == 1, (
        f"gate stdout must contain EXACTLY ONE non-empty line (this file's "
        f"assumption of 'last line is JSON' only holds if it's the ONLY "
        f"line); a second/earlier line is a diagnostic that leaked onto "
        f"stdout and must instead go to stderr per gh1338's whole-stdout "
        f"json.loads() contract; got {len(lines)} non-empty line(s): "
        f"{lines!r} (full stdout={out!r}, "
        f"stderr_tail={(proc.stderr or '')[-2000:]!r})"
    )
    last_line = lines[-1] if lines else ""
    try:
        return json.loads(last_line)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"gate subprocess stdout's last non-empty line was not parseable "
            f"JSON ({exc}); returncode={proc.returncode!r} last_line={last_line!r} "
            f"full_stdout={out!r} stderr_tail={(proc.stderr or '')[-2000:]!r}"
        ) from exc


def _write_bun_test_files(repo, names):
    for name in names:
        (repo / name).write_text('import { test, expect } from "bun:test";\n')
    return _commit_all(repo, "seed bun corpus")


def _bun_results(tmp_path, n_tests, n_files, name="results.txt"):
    """Synthetic bun --results log carrying a real `Ran N tests across M
    files` summary line (used for AC2/AC3/AC5/AC6/AC8 — corpus-size math
    only cares about this line; AC7 alone requires the byte-exact REAL log)."""
    p = tmp_path / name
    p.write_text(
        "bun test v1.3.4 (fake)\n\n"
        f" {n_tests} pass\n 0 fail\n {n_tests * 2} expect() calls\n"
        f"Ran {n_tests} tests across {n_files} files. [1.00s]\n"
    )
    return p


def _bun_results_two_summaries(tmp_path, name="results_double.txt"):
    """A log carrying TWO 'Ran N tests across M files' summary lines — the
    'multiple_summaries' branch (AC1/AC2). Modeled on the spec's own
    concatenated-log attack: findall gives [('16','2'), ('900','300')], and
    the existing _BUN_SUMMARY_RE.search takes only the FIRST match."""
    p = tmp_path / name
    p.write_text(
        "bun test v1.3.4 (fake)\n\n 16 pass\n 0 fail\n 218 expect() calls\n"
        "Ran 16 tests across 2 files. [1.00s]\n\n"
        "bun test v1.3.4 (fake)\n\n 900 pass\n 0 fail\n 1800 expect() calls\n"
        "Ran 900 tests across 300 files. [1.00s]\n"
    )
    return p


def _bun_results_no_summary(tmp_path, name="results_crash.txt"):
    """A results log with NO recognizable bun summary line at all — the
    has_summary=False / n_files=None 'unverified'/'no_summary' case."""
    p = tmp_path / name
    p.write_text("error: could not resolve module 'x'\nBun panicked.\n")
    return p


def _pytest_results(tmp_path, name="results_pytest.txt"):
    """A pytest --results log — pytest has NO file-count oracle (spec),
    n_files must be None regardless of has_summary."""
    p = tmp_path / name
    p.write_text("10 passed in 1.02s\n")
    return p


def _init_pytest_repo(path):
    """Rev 5 (spec §B): a REAL pytest corpus — a git repo whose committed
    files are `test_*.py`, NOT `*.test.ts`. Measured: driving `--suite
    pytest` against a repo holding only *.test.ts files (the bun fixture)
    yields blocked_by ['E_CORPUS_UNKNOWN', 'E_RESULTS_UNPARSEABLE'], not the
    no_file_oracle branch — reusing the bun repo for pytest was the MAJOR-B
    self-contradiction this revision fixes. Returns (repo_path, head_sha)."""
    _init_repo(path)
    (path / "test_a.py").write_text("def test_x():\n    assert True\n")
    (path / "test_b.py").write_text("def test_y():\n    assert True\n")
    sha = _commit_all(path, "seed pytest corpus")
    return path, sha


def _pytest_results_with_summary(tmp_path, n_passed=2, name="results_pytest_summary.txt"):
    """A pytest --results log carrying a REAL pytest summary line (`N passed
    in Ts`) — measured, paired with `_init_pytest_repo`, this yields
    verdict PASS / delta_verdict PASS / corpus_parity OK / blocked_by [] /
    exit_code 0 under --require-corpus-parity with base-ref==head-ref."""
    p = tmp_path / name
    p.write_text(f"{n_passed} passed in 0.10s\n")
    return p


# ─── AC1: RunEvidence carries n_files, derived from bun's own file-count group ─


def test_ac1_run_evidence_n_files_from_bun_summary_second_group(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import parse_run_evidence

    text = (
        "bun test v1.3.4 (5eb2145b)\n\n 16 pass\n 0 fail\n 218 expect() calls\n"
        "Ran 16 tests across 2 files. [8.85s]\n"
    )
    evidence = parse_run_evidence(text, "bun")

    assert "n_files" in evidence, (
        f"RunEvidence must be a total TypedDict — the 'n_files' key must "
        f"ALWAYS be present (even when None), not merely retrievable via "
        f".get() — a consumer doing evidence['n_files'] would KeyError "
        f"today; got keys={sorted(evidence.keys())!r}"
    )
    assert evidence.get("n_files") == 2, (
        f"expected n_files derived from _BUN_SUMMARY_RE.group(2) ('2 files'), "
        f"got n_files={evidence.get('n_files')!r} (full evidence={evidence!r})"
    )
    # existing keys must retain their prior meaning/type (no regression)
    assert evidence.get("has_summary") is True, f"got {evidence!r}"
    assert evidence.get("n_tests") == 16, f"got {evidence!r}"
    assert evidence.get("collected_files") == [], (
        f"a green bun run prints NO per-file header lines — collected_files "
        f"must stay empty, got {evidence!r}"
    )


def test_ac1_run_evidence_n_files_none_for_pytest_no_oracle(tmp_path) -> None:
    from bytedigger_engine.lib.corpus_parity import parse_run_evidence

    evidence = parse_run_evidence("10 passed in 1.02s\n", "pytest")

    assert "n_files" in evidence, (
        f"RunEvidence key 'n_files' must always be present for pytest too "
        f"(total TypedDict), got keys={sorted(evidence.keys())!r}"
    )
    assert evidence.get("n_files") is None, (
        f"pytest has NO file-count oracle per spec — n_files must be None "
        f"(not 0, not omitted), got n_files={evidence.get('n_files')!r} "
        f"(full evidence={evidence!r})"
    )


def test_ac1_run_evidence_n_files_none_on_multiple_summary_lines(tmp_path) -> None:
    """F4 (spec ammendment after gate r.1): a log carrying MORE THAN ONE
    'Ran N tests across M files' summary line must NOT be summed and must
    NOT silently take the first match — n_files must be None, because
    correctly attributing the right total requires knowing the corpora don't
    overlap, which the parser cannot know. Measured: the existing
    _BUN_SUMMARY_RE.search() returns only the FIRST match (('16','2')) on a
    two-corpus concatenated log whose findall is
    [('16','2'), ('900','300')] — this pins the derived n_files must differ
    from that naive-first-match behavior."""
    from bytedigger_engine.lib.corpus_parity import parse_run_evidence

    text = (
        "bun test v1.3.4 (fake)\n\n 16 pass\n 0 fail\n 218 expect() calls\n"
        "Ran 16 tests across 2 files. [1.00s]\n\n"
        "bun test v1.3.4 (fake)\n\n 900 pass\n 0 fail\n 1800 expect() calls\n"
        "Ran 900 tests across 300 files. [1.00s]\n"
    )
    evidence = parse_run_evidence(text, "bun")

    assert "n_files" in evidence, (
        f"RunEvidence must be a total TypedDict — 'n_files' must be "
        f"ALWAYS present, even on the multiple-summaries branch; got "
        f"keys={sorted(evidence.keys())!r}"
    )
    # Bound to real behavior, not `.get()` defaulting: a GREEN that simply
    # never sets the key would pass a `.get(...) is None` check trivially.
    assert evidence["n_files"] is None, (
        f"a log with TWO summary lines must yield n_files=None per F4 "
        f"(never a guessed sum, never the naive first-match '2'), got "
        f"n_files={evidence['n_files']!r} (full evidence={evidence!r})"
    )
    # AC1 (rev 6, MAJOR-H, gate r.5): n_tests keeps FIRST-MATCH semantics —
    # it must NOT be summed across the two summary lines (16, not 916).
    # That pin was prose-only before this amendment; assert it here.
    assert evidence["n_tests"] == 16, (
        f"n_tests must keep FIRST-MATCH semantics on a multi-summary log "
        f"(expected 16, the first 'Ran N tests' group), NOT the sum across "
        f"summaries (which would be 916) — got n_tests={evidence['n_tests']!r} "
        f"(full evidence={evidence!r})"
    )


def test_ac1_n_tests_keeps_first_match_semantics_and_e_empty_run_stays_armed(
    tmp_path,
) -> None:
    """AC1 (rev 6, MAJOR-H, gate r.5): the pin 'n_tests keeps first-match
    semantics, only n_files sums/counts matches' was prose-only — no test
    discriminated it. GREEN rewrites _parse_bun_evidence to COUNT matches
    for n_files, which is exactly where summing n_tests tempts; summing
    would silently disarm the live fail-closed E_EMPTY_RUN code
    (baseline_delta_gate.py:320), reached unconditionally in production via
    --require-corpus-parity (_baseline_delta.py:45). MEASURED fixture: a
    bun log whose FIRST summary is zero, second is large.
    findall => [('0','0'), ('900','300')]; today's parse_run_evidence
    n_tests => 0 (if summed => 900); the real gate on this log with
    --require-corpus-parity => verdict BLOCKED, blocked_by
    ['E_EMPTY_RUN'], exit_code 5."""
    from bytedigger_engine.lib.corpus_parity import parse_run_evidence

    text = (
        "bun test v1 (x)\n\n"
        " 0 pass\n"
        "Ran 0 tests across 0 files. [1s]\n"
        "bun test v1 (x)\n\n"
        " 900 pass\n"
        "Ran 900 tests across 300 files. [9s]\n"
    )

    # ── parser level ──
    evidence = parse_run_evidence(text, "bun")
    assert evidence["n_tests"] == 0, (
        f"n_tests must keep FIRST-MATCH semantics (expected 0, the first "
        f"'Ran N tests' group) — NOT 900 (the sum), because summing would "
        f"silently disarm the live fail-closed E_EMPTY_RUN precondition "
        f"code at baseline_delta_gate.py:320; got "
        f"n_tests={evidence['n_tests']!r} (full evidence={evidence!r})"
    )
    assert "n_files" in evidence, (
        f"RunEvidence must be a total TypedDict — 'n_files' must ALWAYS be "
        f"present, even on this zero-first-summary/multiple-summaries "
        f"branch; got keys={sorted(evidence.keys())!r}"
    )
    assert evidence["n_files"] is None, (
        f"two summary lines => n_files must be None (multiple_summaries), "
        f"got n_files={evidence['n_files']!r} (full evidence={evidence!r})"
    )

    # ── real-gate level ──
    repo = tmp_path / "repo"
    _init_repo(repo)
    head_sha = _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)
    results = tmp_path / "results_zero_first_summary.txt"
    results.write_text(text)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline), "--require-corpus-parity",
         "--base-ref", head_sha, "--head-ref", head_sha],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)
    actual = {
        "verdict": data.get("verdict"),
        "blocked_by": data.get("blocked_by"),
        "exit_code": proc.returncode,
    }
    expected = {
        "verdict": "BLOCKED",
        "blocked_by": ["E_EMPTY_RUN"],
        "exit_code": 5,
    }
    assert actual == expected, (
        f"a bun log whose FIRST summary line is 'Ran 0 tests across 0 "
        f"files' must keep E_EMPTY_RUN ARMED (first-match n_tests==0) even "
        f"though a later summary line reports 900 tests — a GREEN that "
        f"sums n_tests across matches would silently disarm this live "
        f"fail-closed code; expected {expected!r}, got {actual!r} "
        f"(full={data!r}, stderr_tail={(proc.stderr or '')[-1000:]!r})"
    )


def test_ac1_n_files_survives_ci_ansi_and_group_decoration(tmp_path) -> None:
    """MINOR-K(a): a summary line decorated the way CI decorates it — ANSI
    SGR escapes and a leading `::group::`-style GH Actions workflow command
    — must still yield the real n_files via the module's OWN
    normalize_ci_line/_normalize_ci_text (lib/corpus_parity.py), not a
    hand-rolled strip in this test. parse_run_evidence already routes
    through _normalize_ci_text before parsing, so this exercises the real
    production normalization path."""
    from bytedigger_engine.lib.corpus_parity import parse_run_evidence

    text = (
        "\x1b[36m::group::bun test output\x1b[0m\n"
        "bun test v1.3.4 (fake)\n\n"
        " 16 pass\n 0 fail\n 218 expect() calls\n"
        "\x1b[32mRan 16 tests across 2 files. [8.85s]\x1b[0m\n"
        "::endgroup::\n"
    )
    evidence = parse_run_evidence(text, "bun")

    assert "n_files" in evidence, (
        f"RunEvidence must be a total TypedDict — 'n_files' must ALWAYS be "
        f"present, even on a CI-decorated (ANSI + ::group::) summary line; "
        f"got keys={sorted(evidence.keys())!r}"
    )
    assert evidence["n_files"] == 2, (
        f"expected n_files==2 derived from a CI-decorated 'Ran 16 tests "
        f"across 2 files.' summary line (ANSI SGR escapes + leading "
        f"::group:: workflow command) — the module's own "
        f"normalize_ci_line/_normalize_ci_text must strip the decoration "
        f"before the file-count group is captured, got "
        f"n_files={evidence['n_files']!r} (full evidence={evidence!r})"
    )


def test_ac1_n_files_parses_singular_grammar_one_test_one_file(tmp_path) -> None:
    """MINOR-K(b): `_BUN_SUMMARY_RE` has optional plurals (`tests?`,
    `files?`) — pin that the singular grammar bun prints for exactly one
    test across exactly one file ('Ran 1 test across 1 file.') still parses
    to n_files==1, not None."""
    from bytedigger_engine.lib.corpus_parity import parse_run_evidence

    text = (
        "bun test v1.3.4 (fake)\n\n 1 pass\n 0 fail\n 2 expect() calls\n"
        "Ran 1 test across 1 file. [0.50s]\n"
    )
    evidence = parse_run_evidence(text, "bun")

    assert "n_files" in evidence, (
        f"RunEvidence must be a total TypedDict — 'n_files' must ALWAYS be "
        f"present, even on the singular-grammar summary line; got "
        f"keys={sorted(evidence.keys())!r}"
    )
    assert evidence["n_files"] == 1, (
        f"expected n_files==1 from the singular-grammar summary line 'Ran 1 "
        f"test across 1 file.' (the regex's optional plurals must still "
        f"match the singular form), got n_files={evidence['n_files']!r} "
        f"(full evidence={evidence!r})"
    )


# ─── AC2/AC3: corpus_scope is a DERIVED FIVE-BRANCH classification ──────────


def test_ac2_ac3_repo_wide_requires_exact_n_files_equals_total(tmp_path) -> None:
    """repo-wide is reached ONLY on EXACT equality n_files == corpus_total,
    never merely n_files >= total (that would let a bogus over-count game
    'repo-wide', see AC8/AC2 population_mismatch branch below)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3

    results = _bun_results(tmp_path, n_tests=9, n_files=3)  # ran EXACTLY 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "repo-wide", (
        f"expected corpus_scope=='repo-wide' when n_files(3) EXACTLY equals "
        f"corpus size(3), got corpus_scope={data.get('corpus_scope')!r} "
        f"(full={data!r})"
    )
    assert data.get("corpus_scope_reason") is None, (
        f"repo-wide must carry a null reason, got {data!r}"
    )
    assert data.get("corpus_files_unrun") == 0, f"got {data!r}"
    unrun = data.get("corpus_files_unrun")
    assert unrun is not None and unrun >= 0, (
        f"corpus_files_unrun must never be negative, got {unrun!r} (full={data!r})"
    )


def test_ac2_ac3_partial_when_n_files_below_corpus_size(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3

    results = _bun_results(tmp_path, n_tests=5, n_files=2)  # ran only 2 of 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "partial", (
        f"expected corpus_scope=='partial' when n_files(2) < corpus size(3) — "
        f"got corpus_scope={data.get('corpus_scope')!r} (full={data!r})"
    )
    assert data.get("corpus_scope_reason") is None, (
        f"partial must carry a null reason (reason column is for unverified "
        f"branches only per AC2's table), got {data!r}"
    )
    assert data.get("corpus_files_total") == 3, f"got {data!r}"
    assert data.get("corpus_files_run") == 2, f"got {data!r}"
    assert data.get("corpus_files_unrun") == 1, f"got {data!r}"
    unrun = data.get("corpus_files_unrun")
    assert unrun is not None and unrun >= 0, (
        f"corpus_files_unrun must never be negative, got {unrun!r} (full={data!r})"
    )


def test_ac2_ac3_unverified_population_mismatch_when_n_files_exceeds_total(
    tmp_path,
) -> None:
    """Live-and-large route per spec §F3: `.claude/worktrees/**` holds 768
    gitignored *.test.ts files against a real 340-file tracked corpus — `bun
    test` from repo root would report an n_files count LARGER than the
    tracked corpus size. A naive 'n_files >= total => repo-wide' rule would
    print repo-wide at a THIRD of real coverage and (total - run) would go
    NEGATIVE. This fixture reproduces that shape at small scale: n_files(5)
    > corpus_total(3)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3

    results = _bun_results(tmp_path, n_tests=20, n_files=5)  # n_files > total
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "unverified", (
        f"n_files(5) > corpus_total(3) must NEVER read as 'repo-wide' — got "
        f"corpus_scope={data.get('corpus_scope')!r} (full={data!r})"
    )
    assert data.get("corpus_scope_reason") == "population_mismatch", (
        f"expected reason=='population_mismatch' when n_files exceeds the "
        f"corpus total, got {data!r}"
    )
    unrun = data.get("corpus_files_unrun")
    assert unrun is None, (
        f"expected corpus_files_unrun to be JSON null on population_mismatch "
        f"(never a NEGATIVE number, e.g. 3-5=-2), got {unrun!r} (full={data!r})"
    )


def test_ac2_ac3_unverified_corpus_uncollected_total_is_null_not_zero(
    tmp_path,
) -> None:
    """When collect_corpus itself fails (E_CORPUS_UNKNOWN — e.g. an empty
    repo, rc!=0 or empty file set), corpus_files_total must be JSON null,
    explicitly NOT 0 — zero would misleadingly read as 'corpus is empty,
    therefore fully covered'."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "notes.md").write_text("no bun test files at all")
    _commit_all(repo, "no-tests")  # corpus fails to collect (E_CORPUS_UNKNOWN)

    results = _bun_results(tmp_path, n_tests=5, n_files=2)
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "unverified", (
        f"an uncollectable corpus must yield corpus_scope=='unverified', "
        f"got {data!r}"
    )
    assert data.get("corpus_scope_reason") == "corpus_uncollected", (
        f"expected reason=='corpus_uncollected', got {data!r}"
    )
    assert data.get("corpus_files_total") is None, (
        f"corpus_files_total must be JSON null (None) on the uncollected "
        f"branch, explicitly NOT 0 (0 would read as 'empty corpus, fully "
        f"covered'), got corpus_files_total={data.get('corpus_files_total')!r} "
        f"(full={data!r})"
    )


def test_ac2_ac3_unverified_no_summary_reason_on_bun_crash_log(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _bun_results_no_summary(tmp_path)  # has_summary False -> n_files None
    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "unverified", f"got {data!r}"
    assert data.get("corpus_scope_reason") == "no_summary", (
        f"expected reason=='no_summary' when bun's log has no recognizable "
        f"summary line at all, got {data!r}"
    )


def test_ac2_ac3_unverified_no_file_oracle_reason_for_pytest(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "test_a.py").write_text("def test_x(): assert True\n")
    _commit_all(repo, "seed")
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _pytest_results(tmp_path)
    proc = _run_gate(
        ["--results", str(results), "--suite", "pytest", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "unverified", (
        f"pytest has no file-count oracle — corpus_scope must be "
        f"'unverified' even on a passing summary, got corpus_scope="
        f"{data.get('corpus_scope')!r} (full={data!r})"
    )
    assert data.get("corpus_scope_reason") == "no_file_oracle", (
        f"expected reason=='no_file_oracle' for pytest (spec: no oracle "
        f"exists, intentionally not built), got {data!r}"
    )
    assert data.get("corpus_files_unrun") is None, f"got {data!r}"
    # MINOR-I (AC2b): corpus_files_total must be defined on EVERY branch,
    # including no_file_oracle. This fixture's own repo (built just above
    # in this test — NOT _init_pytest_repo's 2-file helper) commits only
    # `test_a.py`, so its real tracked pytest corpus size is 1.
    assert "corpus_files_total" in data, (
        f"expected key 'corpus_files_total' present on the verdict even on "
        f"the no_file_oracle branch, got keys={sorted(data.keys())!r} "
        f"(full={data!r})"
    )
    assert data.get("corpus_files_total") == 1, (
        f"expected corpus_files_total==1 (this test's fixture repo commits "
        f"only test_a.py) even though pytest has no file-count oracle for "
        f"n_files, got corpus_files_total={data.get('corpus_files_total')!r} "
        f"(full={data!r})"
    )


def test_ac3_json_carries_all_four_corpus_fields(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    results = _bun_results(tmp_path, n_tests=5, n_files=2)
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    for key in (
        "corpus_files_total", "corpus_files_run", "corpus_files_unrun",
        "corpus_scope_reason",
    ):
        assert key in data, (
            f"expected JSON key {key!r} present on the verdict, got "
            f"keys={sorted(data.keys())!r} (full={data!r})"
        )


# ─── AC2b (rev 5, new, MINOR-E): run/total defined on EVERY branch ──────────


def test_ac2b_corpus_files_run_and_total_defined_on_population_mismatch(
    tmp_path,
) -> None:
    """AC2b: on population_mismatch, corpus_files_run and corpus_files_total
    must BOTH be real ints — their disagreement (run > total) IS the reason
    for the branch, so neither may collapse to null."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _bun_results(tmp_path, n_tests=20, n_files=5)  # n_files(5) > total(3)
    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope_reason") == "population_mismatch", f"got {data!r}"
    assert data.get("corpus_files_total") == 3, (
        f"expected corpus_files_total==3 (a real int, not null) on "
        f"population_mismatch, got {data.get('corpus_files_total')!r} (full={data!r})"
    )
    assert data.get("corpus_files_run") == 5, (
        f"expected corpus_files_run==5 (a real int, not null) on "
        f"population_mismatch — the disagreement IS the reason, got "
        f"{data.get('corpus_files_run')!r} (full={data!r})"
    )


def test_ac2b_corpus_files_run_defined_but_total_null_on_corpus_uncollected(
    tmp_path,
) -> None:
    """AC2b: on corpus_uncollected, corpus_files_total is null (the corpus
    itself is unknown) but corpus_files_run equals the parsed n_files (it
    IS known — the run happened and reported a number, only the
    denominator is missing)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "notes.md").write_text("no bun test files at all")
    _commit_all(repo, "no-tests")  # corpus fails to collect (E_CORPUS_UNKNOWN)

    results = _bun_results(tmp_path, n_tests=5, n_files=2)
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope_reason") == "corpus_uncollected", f"got {data!r}"
    assert data.get("corpus_files_total") is None, (
        f"expected corpus_files_total==None on corpus_uncollected, got "
        f"{data.get('corpus_files_total')!r} (full={data!r})"
    )
    assert data.get("corpus_files_run") == 2, (
        f"expected corpus_files_run==2 (the parsed n_files — known even "
        f"though the corpus denominator is not), got "
        f"{data.get('corpus_files_run')!r} (full={data!r})"
    )


# ─── AC2a (rev 4, new): AC2's table is read TOP-DOWN, first match wins ──────


def test_ac2a_corpus_uncollected_wins_over_no_summary_reason(tmp_path) -> None:
    """Overlap: corpus fails to collect AND the results log has no summary
    line at all. Per AC2a the table is read top-down — corpus_uncollected
    (row 1) must win over no_summary (row 3), not the other way around."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "notes.md").write_text("no bun test files at all")
    _commit_all(repo, "no-tests")  # corpus fails to collect (E_CORPUS_UNKNOWN)

    results = _bun_results_no_summary(tmp_path)  # AND no recognizable summary
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope_reason") == "corpus_uncollected", (
        f"corpus_uncollected AND no_summary overlap: top-down table order "
        f"requires corpus_uncollected (row 1) to win over no_summary "
        f"(row 3), got reason={data.get('corpus_scope_reason')!r} (full={data!r})"
    )


def test_ac2a_corpus_uncollected_wins_over_pytest_no_file_oracle_reason(
    tmp_path,
) -> None:
    """Overlap: pytest suite AND corpus fails to collect. Per AC2a the
    table is read top-down — corpus_uncollected (row 1) must win over
    no_file_oracle (row 2), not the other way around."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "notes.md").write_text("no bun test files at all")
    _commit_all(repo, "no-tests")  # corpus fails to collect (E_CORPUS_UNKNOWN)

    results = _pytest_results(tmp_path)  # AND suite is pytest (no file oracle)
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "pytest", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope_reason") == "corpus_uncollected", (
        f"pytest AND corpus_uncollected overlap: top-down table order "
        f"requires corpus_uncollected (row 1) to win over no_file_oracle "
        f"(row 2), got reason={data.get('corpus_scope_reason')!r} (full={data!r})"
    )


# ─── AC4 (negative pin, rev 3): enforcement half must NOT leak into GREEN ───


def test_ac4_precondition_codes_closure_unchanged_no_corpus_unrun_code() -> None:
    """Rev 3 split #1741 off entirely: this lot must add NO new closed-set
    member. Literal pin read from the real module today (9 codes, including
    'OK'), asserted to still hold and to explicitly EXCLUDE E_CORPUS_UNRUN."""
    assert len(PRECONDITION_CODES) == 9, (
        f"expected PRECONDITION_CODES to remain a 9-member closed set (no "
        f"enforcement code added by this lot), got "
        f"len={len(PRECONDITION_CODES)} PRECONDITION_CODES={PRECONDITION_CODES!r}"
    )
    assert "E_CORPUS_UNRUN" not in PRECONDITION_CODES, (
        f"E_CORPUS_UNRUN belongs to #1741 (enforcement), not this lot — "
        f"found it in PRECONDITION_CODES={PRECONDITION_CODES!r}"
    )


def test_ac4_blocked_by_order_unchanged_eight_tuple_no_corpus_unrun() -> None:
    expected = (
        "E_STALE_BASE",
        "E_STALE_REMOTE_REF",
        "E_REMOTE_UNREACHABLE",
        "E_FRESHNESS_UNKNOWN",
        "E_CORPUS_DIVERGENCE",
        "E_CORPUS_UNKNOWN",
        "E_RESULTS_UNPARSEABLE",
        "E_EMPTY_RUN",
    )
    assert BLOCKED_BY_ORDER == expected, (
        f"rev 3 SPLIT enforcement off to #1741 — BLOCKED_BY_ORDER must stay "
        f"the existing 8-tuple, unchanged; got BLOCKED_BY_ORDER={BLOCKED_BY_ORDER!r}"
    )
    assert "E_CORPUS_UNRUN" not in BLOCKED_BY_ORDER, (
        f"got BLOCKED_BY_ORDER={BLOCKED_BY_ORDER!r}"
    )


def test_ac4_hal_corpus_unrun_enforce_flag_not_registered() -> None:
    assert "HAL_CORPUS_UNRUN_ENFORCE" not in flags_catalog.FLAGS, (
        f"HAL_CORPUS_UNRUN_ENFORCE is enforcement (#1741), out of scope for "
        f"this honesty-only lot — found it registered in flags_catalog.FLAGS "
        f"keys={sorted(flags_catalog.FLAGS.keys())!r}"
    )


# ─── AC3a (rev 5, new, MAJOR-C): negative pin on the two early-exit payloads ─


def test_ac3a_skipped_payload_carries_no_corpus_scope_keys(tmp_path) -> None:
    """AC3a: `HAL_BASELINE_DELTA_GATE=0` short-circuits main() BEFORE any
    results/corpus parsing (baseline_delta_gate.py:334-343) and prints
    `{"verdict": "SKIPPED"}` (plus `parity_requested_but_disabled` when
    `--require-corpus-parity` is in argv). Nothing was measured on this
    path — a corpus_scope claim here would be exactly the false-green this
    lot fixes."""
    env = _clean_env(HAL_BASELINE_DELTA_GATE="0")
    proc = _run_gate(
        ["--results", "unused.txt", "--suite", "bun", "--require-corpus-parity"],
        cwd=tmp_path, env=env,
    )
    data = _gate_json(proc)

    assert data.get("verdict") == "SKIPPED", f"got {data!r}"
    assert data.get("parity_requested_but_disabled") is True, (
        f"expected parity_requested_but_disabled==True when "
        f"--require-corpus-parity is passed under the kill-switch, got {data!r}"
    )
    forbidden = (
        "corpus_scope", "corpus_scope_reason", "corpus_files_total",
        "corpus_files_run", "corpus_files_unrun",
    )
    for key in forbidden:
        assert key not in data, (
            f"SKIPPED payload must NOT carry {key!r} (nothing was measured "
            f"on the kill-switch path) — got keys={sorted(data.keys())!r} "
            f"(full={data!r})"
        )


def test_ac3a_saved_payload_carries_no_corpus_scope_keys(tmp_path) -> None:
    """AC3a: `--save-baseline` short-circuits main() right after parsing
    --results (baseline_delta_gate.py:378-395) and prints
    `{"saved", "path", "verdict": "SAVED"}`. Nothing about corpus coverage
    was measured on this path either."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    results = _bun_results(tmp_path, n_tests=5, n_files=2)
    cache_dir = tmp_path / "cache"

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--save-baseline",
         "--base-sha", "deadbeef", "--cache-dir", str(cache_dir)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("verdict") == "SAVED", f"got {data!r}"
    assert "saved" in data and "path" in data, f"got {data!r}"
    forbidden = (
        "corpus_scope", "corpus_scope_reason", "corpus_files_total",
        "corpus_files_run", "corpus_files_unrun",
    )
    for key in forbidden:
        assert key not in data, (
            f"SAVED payload must NOT carry {key!r} (nothing was measured "
            f"on the --save-baseline path) — got keys={sorted(data.keys())!r} "
            f"(full={data!r})"
        )


# ─── AC5 (Principle C, rev 4): the enumeration must live in PRODUCTION ──────


def _import_corpus_scope_reasons():
    """§1q: defer the not-yet-existing import so collection never breaks —
    the pre-GREEN failure below must be an AssertionError, never an
    ImportError/AttributeError at collection time."""
    try:
        from bytedigger_engine.lib.corpus_parity import CORPUS_SCOPE_REASONS
        return CORPUS_SCOPE_REASONS
    except ImportError:
        return None


def test_ac5_corpus_scope_reasons_is_exported_frozenset_in_production():
    """AC5: `CORPUS_SCOPE_REASONS` must be a frozenset EXPORTED from
    lib.corpus_parity (production), equal to exactly the five reason
    strings — a reason enumeration living only as a test-local literal is
    codified, not enforced (Principle C)."""
    reasons = _import_corpus_scope_reasons()
    assert reasons is not None, (
        "lib.corpus_parity.CORPUS_SCOPE_REASONS does not exist yet — GREEN "
        "must export it as a frozenset (Principle C: the reason "
        "enumeration must live in production, not in this test file)"
    )
    assert isinstance(reasons, frozenset), (
        f"expected CORPUS_SCOPE_REASONS to be a frozenset, got "
        f"type={type(reasons)!r} value={reasons!r}"
    )
    assert reasons == {
        "no_file_oracle", "no_summary", "multiple_summaries",
        "population_mismatch", "corpus_uncollected",
    }, (
        f"expected exactly the five reason strings from spec AC5, got "
        f"CORPUS_SCOPE_REASONS={reasons!r}"
    )


def test_ac5_corpus_scope_reason_closed_enumeration_across_all_driven_branches(
    tmp_path,
) -> None:
    """Drives every branch this test module exercises and asserts
    corpus_scope_reason is always a MEMBER of the PRODUCTION-exported
    CORPUS_SCOPE_REASONS set (plus None for the no-reason case) — never
    against a test-local literal. A GREEN that emits any 7th value (e.g. a
    typo'd reason string, or leaking an enforcement-only reason) fails
    here. Rev 5 (§C): the `no_file_oracle` branch is driven against a REAL
    pytest fixture (`_init_pytest_repo`), not the bun repo (which yields
    `corpus_uncollected` per AC2a's top-down table, MAJOR-B) — and the
    `corpus_uncollected` branch is added so all six values (five reasons +
    None) are genuinely OBSERVED, not merely asserted as members."""
    reasons = _import_corpus_scope_reasons()
    assert reasons is not None, (
        "lib.corpus_parity.CORPUS_SCOPE_REASONS does not exist yet — cannot "
        "drive the closed-enumeration check against production (AC5)"
    )
    closed_reasons = reasons | {None}

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3
    pytest_repo, _pytest_sha = _init_pytest_repo(tmp_path / "pytest_repo")
    uncollected_repo = tmp_path / "uncollected_repo_ac5"
    _init_repo(uncollected_repo)
    (uncollected_repo / "notes.md").write_text("no bun test files at all")
    _commit_all(uncollected_repo, "no-tests")
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)
    env = _clean_env()

    branches = [
        ("repo_wide", repo, "bun", _bun_results(tmp_path, 9, 3, name="s_rw.txt")),
        ("partial", repo, "bun", _bun_results(tmp_path, 5, 2, name="s_partial.txt")),
        ("population_mismatch", repo, "bun",
         _bun_results(tmp_path, 20, 5, name="s_mismatch.txt")),
        ("no_summary", repo, "bun", _bun_results_no_summary(tmp_path)),
        ("multiple_summaries", repo, "bun", _bun_results_two_summaries(tmp_path)),
        ("no_file_oracle_pytest", pytest_repo, "pytest",
         _pytest_results_with_summary(tmp_path, name="s_pytest.txt")),
        ("corpus_uncollected", uncollected_repo, "bun",
         _bun_results(tmp_path, 5, 2, name="s_uncollected.txt")),
    ]

    seen_reasons = set()
    for label, cwd, suite, results_path in branches:
        proc = _run_gate(
            ["--results", str(results_path), "--suite", suite, "--ledger", str(ledger),
             "--baseline", str(baseline)],
            cwd=cwd, env=env,
        )
        data = _gate_json(proc)
        reason = data.get("corpus_scope_reason")
        seen_reasons.add(reason)
        assert reason in closed_reasons, (
            f"[{label}] corpus_scope_reason={reason!r} is NOT a member of "
            f"the production closed set {closed_reasons!r} — full={data!r}"
        )

    assert seen_reasons == {
        None, "population_mismatch", "no_summary", "multiple_summaries",
        "no_file_oracle", "corpus_uncollected",
    }, (
        f"expected exactly these 6 distinct values (5 reasons + None) "
        f"across the 7 driven branches (repo_wide/partial share reason="
        f"None), got {seen_reasons!r}"
    )


# ─── AC6: gate-level multiple-summaries — the canonical `package.json::test` branch ─


def test_ac6_gate_level_two_summary_lines_yields_unverified_multiple_summaries(
    tmp_path,
) -> None:
    """The branch the canonical package.json::test lands in (5 bun-corpus
    invocations concatenated => multiple 'Ran N tests across M files'
    lines). Asserted at the GATE level (real subprocess), not just the
    parser level (AC1 already covers the parser)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)
    results = _bun_results_two_summaries(tmp_path)

    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "unverified", (
        f"a results log with TWO summary lines (the shape the canonical "
        f"5-corpus package.json::test run produces) must yield "
        f"corpus_scope=='unverified' at the GATE level, got corpus_scope="
        f"{data.get('corpus_scope')!r} (full={data!r})"
    )
    assert data.get("corpus_scope_reason") == "multiple_summaries", (
        f"expected reason=='multiple_summaries', got {data!r}"
    )
    # MINOR-I (AC2b): corpus_files_total must be defined on EVERY branch,
    # including multiple_summaries — the fixture repo holds 3 bun files.
    assert "corpus_files_total" in data, (
        f"expected key 'corpus_files_total' present on the verdict even on "
        f"the multiple_summaries branch, got keys={sorted(data.keys())!r} "
        f"(full={data!r})"
    )
    assert data.get("corpus_files_total") == 3, (
        f"expected corpus_files_total==3 (the fixture repo's real bun "
        f"corpus size — a.test.ts/b.test.ts/c.test.ts) even though "
        f"n_files is None on the multiple_summaries branch, got "
        f"corpus_files_total={data.get('corpus_files_total')!r} (full={data!r})"
    )


# ─── AC7 (§1l, REAL side effect — real bun test run, real repo, real gate) ──


# ─── AC9: no-raise; return code stays inside {0,2,4,5}; stdout always parses ─


def test_ac9_return_code_never_leaves_known_set_across_all_scope_branches(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    uncollected_repo = tmp_path / "uncollected_repo"
    _init_repo(uncollected_repo)
    (uncollected_repo / "notes.md").write_text("no bun test files at all")
    _commit_all(uncollected_repo, "no-tests")

    fixtures = [
        ("repo_wide", repo, _bun_results(tmp_path, 9, 3, name="rc_rw.txt"), _clean_env()),
        ("partial", repo, _bun_results(tmp_path, 5, 2, name="rc_partial.txt"), _clean_env()),
        (
            "population_mismatch",
            repo,
            _bun_results(tmp_path, 20, 5, name="rc_mismatch.txt"),
            _clean_env(),
        ),
        ("no_summary", repo, _bun_results_no_summary(tmp_path), _clean_env()),
        (
            "multi_summary",
            repo,
            _bun_results_two_summaries(tmp_path),
            _clean_env(),
        ),
        (
            "corpus_uncollected",
            uncollected_repo,
            _bun_results(tmp_path, 5, 2, name="rc_uncollected.txt"),
            _clean_env(),
        ),
    ]

    allowed_rc = {0, 2, 4, 5}
    for label, cwd, results_path, env in fixtures:
        proc = _run_gate(
            ["--results", str(results_path), "--suite", "bun", "--ledger", str(ledger),
             "--baseline", str(baseline)],
            cwd=cwd, env=env,
        )
        assert proc.returncode in allowed_rc, (
            f"[{label}] expected returncode in {allowed_rc!r} (rc==1, a "
            f"crash-without-JSON, must be unreachable), got "
            f"{proc.returncode!r}; stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        _ = _gate_json(proc)  # must always parse as the last non-empty line


def test_ac9_group_a_precondition_ok_branches_pin_pass_pass_ok_empty_zero(
    tmp_path,
) -> None:
    """N1 (round-2 rejection) + MAJOR-2 (round-3) + MAJOR-A (round-4, spec
    rev 5): the previous shape asserted a SINGLE literal across all seven
    AC2 branches — unsatisfiable, because three branches are a precondition
    failure BY CONSTRUCTION (see the Group B test below). Rev 5 splits AC9
    into two MEASURED groups. Group A = precondition OK: bun repo-wide /
    partial / population_mismatch / multiple_summaries (measured
    PASS/PASS/OK/[]/0 — a valid FIRST summary line keeps has_summary True
    and n_tests non-zero) and pytest no_file_oracle driven against a REAL
    pytest fixture (`_init_pytest_repo` + `_pytest_results_with_summary`,
    NOT the bun repo — reusing it yields corpus_uncollected per AC2a,
    MAJOR-B). Fixture idiom: `--base-ref <sha> --head-ref <sha>`, the same
    commit SHA for both (lifted from test_gh1338_corpus_parity_gate.py),
    keeps the freshness/corpus-divergence preconditions OK so blocked_by
    stays empty and corpus_scope derivation is what's under test."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    head_sha = _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    pytest_repo, pytest_sha = _init_pytest_repo(tmp_path / "pytest_repo9a")
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)
    env = _clean_env()

    branches = [
        ("repo_wide", repo, "bun", head_sha, _bun_results(tmp_path, 9, 3, name="p_rw.txt")),
        ("partial", repo, "bun", head_sha, _bun_results(tmp_path, 5, 2, name="p_partial.txt")),
        ("population_mismatch", repo, "bun", head_sha,
         _bun_results(tmp_path, 20, 5, name="p_mismatch.txt")),
        ("multiple_summaries", repo, "bun", head_sha, _bun_results_two_summaries(tmp_path)),
        ("no_file_oracle_pytest", pytest_repo, "pytest", pytest_sha,
         _pytest_results_with_summary(tmp_path, name="p_pytest.txt")),
    ]

    expected = {
        "verdict": "PASS",
        "delta_verdict": "PASS",
        "corpus_parity": "OK",
        "blocked_by": [],
        "exit_code": 0,
    }
    for label, cwd, suite, sha, results_path in branches:
        proc = _run_gate(
            ["--results", str(results_path), "--suite", suite, "--ledger", str(ledger),
             "--baseline", str(baseline), "--require-corpus-parity",
             "--base-ref", sha, "--head-ref", sha],
            cwd=cwd, env=env,
        )
        data = _gate_json(proc)
        actual = {k: data.get(k) for k in ("verdict", "delta_verdict", "corpus_parity", "blocked_by")}
        actual["exit_code"] = proc.returncode
        assert actual == expected, (
            f"[{label}] --require-corpus-parity precondition-OK output must "
            f"stay EXACTLY {expected!r} regardless of corpus_scope — got "
            f"{actual!r} (full={data!r}, stderr_tail={(proc.stderr or '')[-1000:]!r})"
        )


def test_ac9_group_b_precondition_blocked_by_construction_pins_measured_literals(
    tmp_path,
) -> None:
    """Group B (spec rev 5): two of the seven AC2 branches are a
    precondition failure BY CONSTRUCTION — `no_summary` (results log has no
    summary line at all ⇒ `E_RESULTS_UNPARSEABLE`) and `corpus_uncollected`
    (repo has no matching test files ⇒ `E_CORPUS_UNKNOWN`). Asserting
    Group A's PASS/OK/[]/0 literal on these is unsatisfiable — the fixture
    itself forces BLOCKED. Literals below are MEASURED with the same
    `--require-corpus-parity --base-ref <sha> --head-ref <sha>` shape as
    Group A."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    head_sha = _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)
    env = _clean_env()

    uncollected_repo = tmp_path / "uncollected_repo9b"
    _init_repo(uncollected_repo)
    (uncollected_repo / "notes.md").write_text("no bun test files at all")
    uncollected_sha = _commit_all(uncollected_repo, "no-tests")

    branches = [
        ("no_summary", repo, head_sha, _bun_results_no_summary(tmp_path),
         ["E_RESULTS_UNPARSEABLE"]),
        ("corpus_uncollected", uncollected_repo, uncollected_sha,
         _bun_results(tmp_path, 5, 2, name="p_uncollected.txt"),
         ["E_CORPUS_UNKNOWN"]),
    ]

    for label, cwd, sha, results_path, expected_blocked_by in branches:
        proc = _run_gate(
            ["--results", str(results_path), "--suite", "bun", "--ledger", str(ledger),
             "--baseline", str(baseline), "--require-corpus-parity",
             "--base-ref", sha, "--head-ref", sha],
            cwd=cwd, env=env,
        )
        data = _gate_json(proc)
        actual = {
            "verdict": data.get("verdict"),
            "corpus_parity": data.get("corpus_parity"),
            "blocked_by": data.get("blocked_by"),
            "exit_code": proc.returncode,
        }
        expected = {
            "verdict": "BLOCKED",
            "corpus_parity": "BLOCKED",
            "blocked_by": expected_blocked_by,
            "exit_code": 5,
        }
        assert actual == expected, (
            f"[{label}] precondition-BLOCKED-by-construction output must "
            f"be EXACTLY {expected!r}, got {actual!r} (full={data!r}, "
            f"stderr_tail={(proc.stderr or '')[-1000:]!r})"
        )


# ─── AC8: repo-wide is UNREACHABLE when n_files is None; branches non-tautological ─


def test_ac8_repo_wide_unreachable_when_n_files_none(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _bun_results_no_summary(tmp_path)  # has_summary False -> n_files None
    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") != "repo-wide", (
        f"corpus_scope must NEVER be 'repo-wide' when n_files is None (no "
        f"input can game it into full coverage), got corpus_scope="
        f"{data.get('corpus_scope')!r} (full={data!r})"
    )
    assert data.get("corpus_scope") == "unverified", f"got {data!r}"

    unrun = data.get("corpus_files_unrun")
    assert unrun is None, (
        f"expected corpus_files_unrun to be JSON null (None) when n_files is "
        f"unknown, got corpus_files_unrun={unrun!r} (full={data!r})"
    )
    assert data.get("corpus_files_run") is None, (
        f"expected corpus_files_run to be null alongside corpus_files_unrun, "
        f"got corpus_files_run={data.get('corpus_files_run')!r} (full={data!r})"
    )
    # MINOR-I (AC2b): corpus_files_total must be defined on EVERY branch,
    # including the n_files-is-None (no_summary) row — not merely on the
    # branches where corpus_files_unrun happens to be a real int.
    assert "corpus_files_total" in data, (
        f"expected key 'corpus_files_total' present on the verdict even on "
        f"the no_summary/n_files-None branch, got keys="
        f"{sorted(data.keys())!r} (full={data!r})"
    )
    assert data.get("corpus_files_total") == 3, (
        f"expected corpus_files_total==3 (the fixture repo's real bun "
        f"corpus size — a.test.ts/b.test.ts/c.test.ts) even though "
        f"n_files is None on the no_summary branch, got corpus_files_total="
        f"{data.get('corpus_files_total')!r} (full={data!r})"
    )


def test_ac8_repo_wide_unreachable_when_n_files_exceeds_total(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _bun_results(tmp_path, n_tests=20, n_files=5)  # n_files(5) > total(3)
    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") != "repo-wide", (
        f"corpus_scope must NEVER be 'repo-wide' when n_files(5) exceeds "
        f"corpus total(3) — a naive '>= total' rule would wrongly say "
        f"repo-wide here, got corpus_scope={data.get('corpus_scope')!r} "
        f"(full={data!r})"
    )
    assert data.get("corpus_scope") == "unverified", f"got {data!r}"


def test_ac8_corpus_files_total_not_none_on_the_collected_branch(tmp_path) -> None:
    """F12: with unrun forced None (n_files unknown case), asserting
    unrun != 0 / unrun != total is vacuous when total is ALSO None (both
    sides None). This test makes the distinction real by pinning that on a
    branch where the corpus DID collect (n_files known, partial), total is
    a genuine non-None int, not None."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _bun_results(tmp_path, n_tests=5, n_files=2)  # partial, corpus collected
    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_files_total") is not None, (
        f"expected corpus_files_total to be a real int on the collected "
        f"branch (not None), got corpus_files_total="
        f"{data.get('corpus_files_total')!r} (full={data!r})"
    )
    assert data.get("corpus_files_total") == 3, f"got {data!r}"


# ─── AC12 (rev 4, new): corpus_scope telemetry must reach the CONSUMER ──────


def _import_run_baseline_delta_gate():
    """§1q-deferred import — the AssertionError below (not ImportError at
    collection time) is the pre-GREEN failure signal for this AC."""
    workflows_dir = _ENGINE_ROOT / "bytedigger_engine/workflows"
    from bytedigger_engine.workflows import _baseline_delta
    return _baseline_delta


class _AC12StubCfg:
    """Minimal cfg stub matching test_GH561_baseline_delta_wiring.py's
    established idiom: gate_enabled/flag/path."""

    def __init__(self, path_override):
        self._path_override = path_override

    def gate_enabled(self, name):
        return True

    def flag(self, name):
        return False

    def path(self, name, default):
        return self._path_override


def _ac12_capture_emit():
    events = []

    def emit(name, payload, **kw):
        events.append((name, payload))

    return events, emit


def _ac12_write_fake_gate_script(tmp_path, verdict_json, name="fake_gate_ac12.py"):
    """A fake `baseline_delta_gate.py` stand-in (idiom lifted from
    test_GH561_baseline_delta_wiring.py's _write_fake_script) that prints a
    verdict JSON carrying the NEW corpus_scope fields on stdout, exit 0."""
    script = tmp_path / name
    script.write_text(
        "import sys, json\n"
        f"print(json.dumps({verdict_json!r}))\n"
        "sys.exit(0)\n"
    )
    return script


def test_ac12_baseline_delta_gate_verdict_event_forwards_corpus_scope_fields(
    tmp_path,
) -> None:
    """AC12: corpus_scope, corpus_scope_reason, corpus_files_total,
    corpus_files_run, corpus_files_unrun must be forwarded from the gate's
    JSON verdict into the `baseline_delta_gate_verdict` event payload built
    by run_baseline_delta_gate in _baseline_delta.py. Measured against
    production TODAY: the payload dict literal at _baseline_delta.py:92-105
    lists suite/verdict/new_fails/n_new_fails/ledgered/baseline_source/
    enforced/phase/step/blocked_by/only_in_base/declared_removal_count —
    none of the five corpus_scope keys are present, so this must fail
    pre-GREEN with a clean 'key not in payload' AssertionError, never a
    stub-passable no-op."""
    _baseline_delta = _import_run_baseline_delta_gate()

    stdout_path = tmp_path / "pytest_out.txt"
    stdout_path.write_text("collected 3 items\n")

    verdict_json: dict[str, object] = {
        "verdict": "PASS",
        "delta_verdict": "PASS",
        "new_fails": [],
        "ledgered": [],
        "baseline_source": "ledger-only",
        "corpus_scope": "partial",
        "corpus_scope_reason": None,
        "corpus_files_total": 340,
        "corpus_files_run": 2,
        "corpus_files_unrun": 338,
    }
    script = _ac12_write_fake_gate_script(tmp_path, verdict_json)
    events, emit = _ac12_capture_emit()
    cfg = _AC12StubCfg(path_override=script)

    _baseline_delta.run_baseline_delta_gate(
        str(stdout_path), "pytest", str(tmp_path), 5, "test_step", emit, cfg=cfg,
    )

    verdict_events = [e for e in events if e[0] == "baseline_delta_gate_verdict"]
    assert len(verdict_events) == 1, (
        f"expected exactly one baseline_delta_gate_verdict event, got "
        f"events={events!r}"
    )
    payload = verdict_events[0][1]

    for key, expected_value in (
        ("corpus_scope", "partial"),
        ("corpus_scope_reason", None),
        ("corpus_files_total", 340),
        ("corpus_files_run", 2),
        ("corpus_files_unrun", 338),
    ):
        assert key in payload, (
            f"expected key {key!r} forwarded into the "
            f"baseline_delta_gate_verdict event payload (AC12) — got "
            f"payload keys={sorted(payload.keys())!r} (full payload={payload!r})"
        )
        assert payload[key] == expected_value, (
            f"expected payload[{key!r}] == {expected_value!r} (forwarded "
            f"verbatim from the gate's JSON verdict), got "
            f"payload[{key!r}]={payload[key]!r} (full payload={payload!r})"
        )


# ─── AC13 (rev 4, new): three boundary cases ────────────────────────────────


def test_ac13_zero_ran_tests_against_nonempty_corpus_is_partial_fully_unrun(
    tmp_path,
) -> None:
    """Boundary 1: `Ran 0 tests across 0 files` against a NON-empty corpus.
    corpus_scope must be 'partial' with corpus_files_unrun ==
    corpus_files_total (nothing ran at all). This legitimately COEXISTS
    with the existing E_EMPTY_RUN under --require-corpus-parity — different
    fields; this test does not assert they conflict."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _bun_results(tmp_path, n_tests=0, n_files=0)  # Ran 0 tests across 0 files
    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "partial", (
        f"'Ran 0 tests across 0 files' against a non-empty (3-file) corpus "
        f"must yield corpus_scope=='partial' (0 < 3), got corpus_scope="
        f"{data.get('corpus_scope')!r} (full={data!r})"
    )
    total = data.get("corpus_files_total")
    unrun = data.get("corpus_files_unrun")
    assert total == 3, f"got corpus_files_total={total!r} (full={data!r})"
    assert unrun == total, (
        f"expected corpus_files_unrun == corpus_files_total (nothing ran), "
        f"got corpus_files_unrun={unrun!r} corpus_files_total={total!r} "
        f"(full={data!r})"
    )


def test_ac13_two_identical_summary_lines_still_yields_multiple_summaries(
    tmp_path,
) -> None:
    """Boundary 2 (trap): TWO IDENTICAL 'Ran N tests across M files' lines.
    A GREEN that dedups matches with set() before counting would collapse
    two identical tuples into ONE and wrongly report a single summary
    (repo-wide/partial) instead of multiple_summaries. Per F4/AC1, count of
    REGEX MATCHES (not distinct values) determines the branch."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results_path = tmp_path / "results_identical_double.txt"
    results_path.write_text(
        "bun test v1.3.4 (fake)\n\n 5 pass\n 0 fail\n 10 expect() calls\n"
        "Ran 5 tests across 2 files. [1.00s]\n\n"
        "bun test v1.3.4 (fake)\n\n 5 pass\n 0 fail\n 10 expect() calls\n"
        "Ran 5 tests across 2 files. [1.00s]\n"
    )

    proc = _run_gate(
        ["--results", str(results_path), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope_reason") == "multiple_summaries", (
        f"TWO IDENTICAL 'Ran N tests across M files' lines must still yield "
        f"reason=='multiple_summaries' (match COUNT, not distinct-value "
        f"count, decides the branch — a set()-deduping GREEN would wrongly "
        f"collapse this to a single summary), got reason="
        f"{data.get('corpus_scope_reason')!r} (full={data!r})"
    )
    assert data.get("corpus_scope") == "unverified", f"got {data!r}"


def test_ac13_partial_scope_in_non_parity_mode_pins_exit_code_zero(
    tmp_path,
) -> None:
    """Boundary 3: in NON-parity mode (no --require-corpus-parity), a
    'partial' corpus_scope must still yield exit_code == 0 and
    verdict == 'PASS'. The allowed {0,2,4,5} set ADMITS 5 — this test pins
    zero explicitly so a GREEN that folds corpus_scope into the
    precondition/blocked_by machinery even outside --require-corpus-parity
    (and thus produces rc=5) is caught."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_bun_test_files(repo, ["a.test.ts", "b.test.ts", "c.test.ts"])  # corpus size 3
    ledger = _empty_ledger(tmp_path)
    baseline = _empty_baseline(tmp_path)

    results = _bun_results(tmp_path, n_tests=5, n_files=2)  # partial: 2 < 3
    proc = _run_gate(
        ["--results", str(results), "--suite", "bun", "--ledger", str(ledger),
         "--baseline", str(baseline)],  # deliberately NO --require-corpus-parity
        cwd=repo, env=_clean_env(),
    )
    data = _gate_json(proc)

    assert data.get("corpus_scope") == "partial", f"got {data!r}"
    assert proc.returncode == 0, (
        f"non-parity mode with a 'partial' corpus_scope must yield "
        f"exit_code==0 (not merely 'in {{0,2,4,5}}' — the wider set would "
        f"pass a GREEN that returns rc=5), got returncode={proc.returncode!r} "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert data.get("verdict") == "PASS", (
        f"non-parity mode with a 'partial' corpus_scope must yield "
        f"verdict=='PASS', got verdict={data.get('verdict')!r} (full={data!r})"
    )
