#!/usr/bin/env python3
"""§1r baseline-delta deterministic gate (GH544, agreement 27428E6F).

Compares a current test-run's fail set against an explicit/cached baseline
and a known-reds.md ledger. Anything outside baseline+ledger is a
regression. stdlib-only, python3.

CLI:
  python3 baseline_delta_gate.py --results <file> --suite pytest|bun
    [--ledger <path>] [--baseline <file>] [--base-ref <ref>]
    [--base-sha <sha>] [--cache-dir <dir>] [--save-baseline]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "engine_py"))
from bytedigger_engine.known_reds_ledger import (  # noqa: E402
    KILL_BY_ACTIVE,
    LEDGER_COLUMNS,
    classify_kill_by,
    format_inactive,
    format_out_of_scope,
    format_scope_summary,
    kill_by_enforced,
    parse_table_rows,
    partition_by_kill_by,
    partition_by_scope,
    red_match_tokens,
    resolve_run_context,
    resolve_today,
    scope_enforced,
)
from bytedigger_engine.config_provider import foreign_state_dirname  # noqa: E402
from bytedigger_engine.lib.corpus_parity import (  # noqa: E402  (GH1338 §2.2, §10 rev4)
    BLOCKED_BY_ORDER,
    RunEvidence,
    collect_corpus,
    evaluate_preconditions,
    normalize_ci_line,
    parse_run_evidence,
)

_PYTEST_FAIL_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)")
_BUN_FAIL_RE = re.compile(r"^\s*\(fail\)\s*(.+)$")
_BUN_DURATION_RE = re.compile(r"\s*\[\d+(?:\.\d+)?\s*(?:ms|s)\]\s*$")

# bd port: no host literals. Anchored on the engine's own foreign-state dirname
# (§1g single source: config_provider.foreign_state_dirname), same convention as
# memory.db. Overridable via HAL_BASELINE_DELTA_CACHE_DIR, as upstream.
_DEFAULT_CACHE_DIR = str(Path.cwd() / foreign_state_dirname() / "baseline-cache")


def parse_pytest_fails(text: str) -> list:
    """Extract dedup, order-preserving list of pytest fail node-ids."""
    seen = set()
    result = []
    for line in text.splitlines():
        m = _PYTEST_FAIL_RE.match(normalize_ci_line(line))
        if not m:
            continue
        nodeid = m.group(2)
        if nodeid not in seen:
            seen.add(nodeid)
            result.append(nodeid)
    return result


def normalize_bun_fail_id(name: str) -> str:
    """Идентичность bun-красного без wall-clock: срезает ОДИН хвостовой суффикс длительности.

    Если после среза не остаётся ничего (строка была одной длительностью), возвращается
    исходное имя — пустая идентичность матчилась бы с любой другой пустой (fail-closed).
    """
    stripped = _BUN_DURATION_RE.sub("", name).strip()
    return stripped if stripped else name.strip()


def parse_bun_fails(text: str) -> list:
    """Extract dedup, order-preserving list of bun fail names."""
    seen = set()
    result = []
    for line in text.splitlines():
        m = _BUN_FAIL_RE.match(normalize_ci_line(line))
        if not m:
            continue
        name = normalize_bun_fail_id(m.group(1))
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def parse_ledger(path: str, suite: str, today=None) -> list:
    """Parse known-reds.md style table rows filtered by Suite column.

    Signature/return shape frozen (test_gh1164, test_27428E6F): no tuple
    return, no new required parameters. `today` is an optional threaded
    kwarg (advisory 11) so callers that already resolved it don't pay a
    second `resolve_today()` — the positional 2-arg pins stay compatible.
    """
    with open(path, "r") as f:
        text = f.read()

    if today is None:
        today = resolve_today()
    # rollout: CD666B9B-D319-403D-8EF2-8B8870E0F834 flip-by:2026-08-08 (HAL_KNOWN_REDS_KILL_BY_ENFORCE)
    enforced = kill_by_enforced()

    all_rows = parse_table_rows(text)
    suite_rows = []
    suite_lower = suite.strip().lower()
    for lineno, cells in all_rows:
        if len(cells) != 6:
            print(
                f"baseline_delta_gate: ledger row at line {lineno}: "
                f"{len(cells)} cells, expected 6 — row NOT matchable, "
                f"fix known-reds.md",
                file=sys.stderr,
            )
            continue
        if cells[0].lower() != suite_lower:
            continue
        if enforced:
            status, _kb = classify_kill_by(cells, today)
            if status != KILL_BY_ACTIVE:
                continue
        suite_rows.append((lineno, cells))

    # GH1470 (agreement 92237C8D-8B77-4294-8DC6-5B81020A86D7): the Scope column
    # is READ, through the ONE canonical classifier (§1g). Axis order is fixed
    # (§1.6): kill-by above, scope here. rollout flip-by:2026-08-16
    # (HAL_KNOWN_REDS_SCOPE_ENFORCE) — until the flip the returned list is byte
    # for byte the pre-lot one and only stderr grows, which is the whole point
    # of the warn-only fortnight: this gate is the merge oracle of every window
    # and it declares no run context (§0.4), i.e. it runs in the NARROWEST one.
    ctx = resolve_run_context()
    scope_enforce = scope_enforced()
    in_scope_rows, scope_reports = partition_by_scope(suite_rows, ctx)
    for report in scope_reports:
        sys.stderr.write(
            f"baseline_delta_gate: {format_out_of_scope(report, scope_enforce)}\n"
        )
    sys.stderr.write(
        "baseline_delta_gate: "
        + format_scope_summary(
            len(all_rows), len(suite_rows), in_scope_rows, scope_reports,
            scope_enforce, ctx,
        )
        + "\n"
    )

    rows = []
    for _lineno, cells in (in_scope_rows if scope_enforce else suite_rows):
        _row_suite, red, _scope, issue, kill_by, _cls = cells
        rows.append(
            {
                "red": red,
                "issue": issue,
                "kill_by": kill_by,
                "identifiers": red_match_tokens(cells),
            }
        )
    return rows


def inactive_ledger_rows(path: str, suite: str, today=None) -> list:
    """Report every inactive (expired/malformed) row scoped to `suite`.

    Deliberate second read of the ledger (gate note 6/7): applies the
    identical `len(cells) != 6` / suite skips as `parse_ledger` (silently,
    no duplicate diagnostic — that message belongs to `parse_ledger` alone),
    then runs the same `partition_by_kill_by` so drop and report can never
    disagree on which rows are inactive.
    """
    with open(path, "r") as f:
        text = f.read()

    if today is None:
        today = resolve_today()

    suite_lower = suite.strip().lower()
    rows_for_suite = []
    for lineno, cells in parse_table_rows(text):
        if len(cells) != LEDGER_COLUMNS:
            continue
        if cells[0].strip().lower() != suite_lower:
            continue
        rows_for_suite.append((lineno, cells))

    _active, inactive_reports = partition_by_kill_by(rows_for_suite, today)
    return inactive_reports


def match_ledger(fail_id: str, rows: list):
    """First ledger row matching fail_id, else None.

    Branch (a): any identifier token from the Red cell is a substring of
    fail_id.
    Branch (b): fail_id's final '::'-segment (test function name) is a
    substring of the Red cell. Same >=6-char minimum length floor as
    branch (a) identifiers applies here too, to avoid vacuous short-name
    substring matches (gate reviewer note 2).
    """
    for row in rows:
        for ident in row["identifiers"]:
            if ident in fail_id:
                return row

    segment = fail_id.rsplit("::", 1)[-1]
    if len(segment) >= 6:
        for row in rows:
            if segment in row["red"]:
                return row

    return None


def resolve_baseline(args):
    """Resolve (fail_ids, source) per §2.1 order: explicit -> cache -> none."""
    if args.baseline:
        with open(args.baseline, "r") as f:
            text = f.read()
        if args.suite == "pytest":
            return parse_pytest_fails(text), "explicit", None
        return parse_bun_fails(text), "explicit", None

    base_sha = args.base_sha
    if not base_sha:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", args.base_ref],
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError(f"git rev-parse failed: {exc}")
        if proc.returncode != 0:
            raise RuntimeError(
                f"git rev-parse {args.base_ref} failed: {proc.stderr.strip()}"
            )
        base_sha = proc.stdout.strip()

    cache_dir = args.cache_dir
    cache_file = Path(cache_dir) / f"{base_sha}.{args.suite}.fails"
    if cache_file.exists():
        with open(cache_file, "r") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        if args.suite == "bun":
            lines = [normalize_bun_fail_id(ln) for ln in lines]
        return lines, "cache", base_sha

    return [], "none", base_sha


def evaluate(current: list, baseline: list, rows: list) -> dict:
    """Classify each current fail into baseline_matched/ledgered/new_fails."""
    baseline_set = set(baseline)
    baseline_matched = []
    ledgered = []
    new_fails = []

    for fail_id in current:
        if fail_id in baseline_set:
            baseline_matched.append(fail_id)
            continue
        matched = match_ledger(fail_id, rows)
        if matched is not None:
            ledgered.append({"fail": fail_id, "issue": matched["issue"]})
            continue
        new_fails.append(fail_id)

    verdict = "PASS" if not new_fails else "FAIL"
    return {
        "baseline_matched": baseline_matched,
        "ledgered": ledgered,
        "new_fails": new_fails,
        "verdict": verdict,
    }


def _emit_error(msg: str) -> int:
    sys.stderr.write(f"ERROR baseline-delta-gate: {msg}\n")
    print(json.dumps({"verdict": "ERROR", "error": msg}))
    return 2


def _allow_removed_tokens(args) -> list:
    """§2.1: declaration channel union — CLI (repeatable) + env (comma-sep)."""
    tokens = list(args.allow_removed_test_file or [])
    env_tokens = os.environ.get("HAL_CORPUS_ALLOW_REMOVED")
    if env_tokens:
        tokens.extend(t for t in env_tokens.split(",") if t)
    return tokens


def _compute_precondition(args, run_evidence: RunEvidence) -> dict:
    """§2.2 gate M3: computed BEFORE resolve_baseline. NOT_REQUESTED when the
    flag is absent — the key is present in the output but the value is opt-in.

    §10 MAJOR-3: when requested, also folds in the run-evidence codes
    (E_RESULTS_UNPARSEABLE / E_EMPTY_RUN) so an empty/unparseable --results
    log or a parseable-but-zero-tests summary can never certify a match —
    only appended into blocked_by under --require-corpus-parity (opt-in,
    same as the freshness/corpus codes).
    """
    if not args.require_corpus_parity:
        return {"status": "NOT_REQUESTED"}
    base = evaluate_preconditions(
        args.base_ref, args.head_ref, args.suite,
        cwd=None, declared_removals=tuple(_allow_removed_tokens(args)),
    )
    blocked = set(base.get("blocked_by") or [])
    if not run_evidence["has_summary"]:
        blocked.add("E_RESULTS_UNPARSEABLE")
    elif run_evidence["n_tests"] == 0:
        blocked.add("E_EMPTY_RUN")
    blocked_by = [code for code in BLOCKED_BY_ORDER if code in blocked]
    status = "OK" if not blocked_by else "BLOCKED"
    result = dict(base)
    result["status"] = status
    result["blocked_by"] = blocked_by
    return result


def main(argv) -> int:
    # Kill-switch precedes ALL file IO (gate reviewer note 3 / §2.3). A
    # requested-but-disabled corpus-parity flag must be COUNTED, not silent
    # (§2.2 edge 3, AC18).
    if os.environ.get("HAL_BASELINE_DELTA_GATE") == "0":
        payload: dict = {"verdict": "SKIPPED"}
        if "--require-corpus-parity" in argv:
            payload["parity_requested_but_disabled"] = True
            sys.stderr.write(
                "WARN baseline-delta-gate: corpus parity requested but gate disabled by "
                "HAL_BASELINE_DELTA_GATE=0\n"
            )
        print(json.dumps(payload))
        return 0

    parser = argparse.ArgumentParser(prog="baseline_delta_gate.py")
    parser.add_argument("--results", required=True)
    parser.add_argument("--suite", required=True, choices=["pytest", "bun"])
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--base-sha", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--require-corpus-parity", action="store_true")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--allow-removed-test-file", action="append", default=None)
    args = parser.parse_args(argv)

    script_dir = Path(__file__).resolve().parent
    if not args.ledger:
        args.ledger = str(script_dir / "known-reds.md")
    if not args.cache_dir:
        args.cache_dir = os.environ.get(
            "HAL_BASELINE_DELTA_CACHE_DIR", _DEFAULT_CACHE_DIR
        )

    if not os.path.isfile(args.results):
        return _emit_error(f"--results not found: {args.results}")

    with open(args.results, "r") as f:
        results_text = f.read()

    if args.suite == "pytest":
        current = parse_pytest_fails(results_text)
    else:
        current = parse_bun_fails(results_text)

    if args.save_baseline:
        base_sha = args.base_sha or "unknown"
        cache_dir = Path(args.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{base_sha}.{args.suite}.fails"
        with open(cache_file, "w") as f:
            for fail_id in current:
                f.write(fail_id + "\n")
        print(
            json.dumps(
                {
                    "saved": len(current),
                    "path": str(cache_file),
                    "verdict": "SAVED",
                }
            )
        )
        return 0

    if not os.path.isfile(args.ledger):
        return _emit_error(f"ledger not found: {args.ledger}")

    try:
        today = resolve_today()
    except ValueError as exc:
        return _emit_error(f"malformed HAL_KNOWN_REDS_TODAY: {exc}")

    try:
        rows = parse_ledger(args.ledger, args.suite, today)
        expired_ledger_rows = inactive_ledger_rows(args.ledger, args.suite, today)
    except OSError as exc:
        return _emit_error(f"ledger unreadable: {exc}")

    # §10 MAJOR-3: parse the run-evidence shape of --results ONCE (used both
    # to fold E_RESULTS_UNPARSEABLE/E_EMPTY_RUN into the precondition and to
    # report run_evidence/collected_not_in_corpus in the JSON below).
    run_evidence_full = parse_run_evidence(results_text, args.suite)

    # §2.2 gate M3: precondition computed BEFORE resolve_baseline — an
    # unresolvable --base-ref must yield verdict BLOCKED under
    # --require-corpus-parity, not the driver-crash ERROR/exit-2 path.
    precondition = _compute_precondition(args, run_evidence_full)
    precondition_blocked = precondition.get("status") == "BLOCKED"

    try:
        baseline, baseline_source, base_sha = resolve_baseline(args)
    except RuntimeError as exc:
        if args.require_corpus_parity and precondition_blocked:
            baseline, baseline_source, base_sha = [], "unavailable", None
        else:
            return _emit_error(str(exc))
    except OSError as exc:
        return _emit_error(f"baseline unreadable: {exc}")

    result = evaluate(current, baseline, rows)
    delta_verdict = result["verdict"]
    overall_verdict = "BLOCKED" if precondition_blocked else delta_verdict

    # rollout: CD666B9B-D319-403D-8EF2-8B8870E0F834 flip-by:2026-08-08 (HAL_KNOWN_REDS_KILL_BY_ENFORCE)
    kill_by_enforce = kill_by_enforced()
    for report in expired_ledger_rows:
        sys.stderr.write(f"baseline_delta_gate: {format_inactive(report, kill_by_enforce)}\n")

    # rollout: 27428E6F-AD5C-4EAA-92B5-0C62A84CFFAC flip-by:2026-08-07 (warn-only until then)
    enforce = os.environ.get("HAL_BASELINE_DELTA_GATE_ENFORCE") == "1"

    # §10 MAJOR-4 exit ladder (supersedes §2.2 rev2-N2 / rev4 rewrite):
    #   1. delta_verdict == "FAIL" and HAL_BASELINE_DELTA_GATE_ENFORCE=1 -> 4
    #      (a known regression takes priority over "don't know").
    #   2. else verdict == "BLOCKED" -> 5 ALWAYS, regardless of
    #      HAL_CORPUS_PARITY_ENFORCE (that flag now governs ONLY the
    #      engine-level parity_block/E_CORPUS_PARITY branch, §2.3).
    #   3. else -> 0.
    # "verdict BLOCKED and exit 0" is impossible under any flag combination.
    exit_code = 0
    if delta_verdict == "FAIL" and enforce:
        exit_code = 4
    elif precondition_blocked:
        exit_code = 5

    if delta_verdict == "FAIL" and not enforce:
        sys.stderr.write(
            f"WARN baseline-delta-gate: {len(result['new_fails'])} new fail(s)\n"
        )

    if precondition_blocked:
        sys.stderr.write(
            f"WARN baseline-delta-gate: verdict BLOCKED by "
            f"{precondition.get('blocked_by')}; only_in_base: {precondition.get('only_in_base')}\n"
        )

    # §10 MAJOR-3: PR-side git corpus (independent of whether --require-corpus-parity
    # was passed) is used only to compute collected_not_in_corpus below —
    # cheap, and always an honest admission of what was actually compared.
    pr_corpus_for_evidence = collect_corpus(None, args.suite, cwd=None)
    pr_files_for_evidence = set(pr_corpus_for_evidence.get("files") or [])
    collected_not_in_corpus = sorted(
        set(run_evidence_full["collected_files"]) - pr_files_for_evidence
    )

    output = {
        "suite": args.suite,
        "base_sha": base_sha,
        "baseline_source": baseline_source,
        "current_fail_count": len(current),
        "new_fails": result["new_fails"],
        "ledgered": result["ledgered"],
        "baseline_matched": result["baseline_matched"],
        "delta_verdict": delta_verdict,
        "verdict": overall_verdict,
        "corpus_parity": precondition.get("status"),
        "blocked_by": precondition.get("blocked_by", []),
        "only_in_base": precondition.get("only_in_base", []),
        "only_in_pr": precondition.get("only_in_pr", []),
        "declared_removal_count": precondition.get("declared_removal_count", 0),
        "run_evidence": {
            "has_summary": run_evidence_full["has_summary"],
            "n_tests": run_evidence_full["n_tests"],
            "collected_files_seen": len(run_evidence_full["collected_files"]),
        },
        "corpus_scope": "repo-wide",
        "collected_not_in_corpus": collected_not_in_corpus,
        "enforce": enforce,
        "exit_code": exit_code,
        "expired_ledger_rows": expired_ledger_rows,
        "kill_by_enforce": kill_by_enforce,
    }
    print(json.dumps(output))
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
