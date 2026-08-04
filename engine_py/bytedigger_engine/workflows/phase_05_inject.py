"""Phase 0.5 (learning + constitution injection) as a WorkflowDefinition.

Stage 2.1 port (2026-04-25). Captures the deterministic file-system pieces
of `phase-05-inject.md`: constitution discovery (§4) + the four injection
files written under `$SCRATCHPAD/injection/` (§7.5). LLM-driven pieces
(keyword extraction §1, memory queries §2-3, security sidequery §7,
and pre-build-gate session registration §5) stay outside — orchestrator
resolves them and passes results through `WorkflowContext.org_config`.

DB access (FTS5 query) is delegated to `inject-learnings.ts` via subprocess
(09F250F4 TS call-out). Token-filtering, query-mode selection, bullet
formatting, file writing, sentinels, and event emission remain in Python.

Inputs (via `ctx.org_config`):
    scratchpad_dir       — REQUIRED. Absolute path to scratchpad root.
    hal_memory_block     — Optional. Pre-formatted learnings text. Empty/missing
                           emits a sentinel into hal-memory.md.
    constitution_path    — Optional. If set and file exists, copied verbatim;
                           overrides standard-path discovery.
    active_work_context  — Optional. Active-work text from pre-build-gate.

Steps (both deterministic):
    1. discover_constitution  — resolve constitution path (explicit ▸ standard search)
    2. write_injection_files  — write hal-memory / constitution / quality-gate / active-work

Mirrors `SYSTEM/cli/build/write-injection-files.sh`. Quality-gate text is held
inline here (single source of truth lives in phase-05-inject.md §6) — when it
diverges, update both.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition
try:
    from ._task_description import normalize_task_description  # noqa: E402
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from bytedigger_engine.workflows._task_description import normalize_task_description  # type: ignore[no-redef]  # noqa: E402
from bytedigger_engine.config_provider import get_config, default_security_asset  # noqa: E402

try:
    from bytedigger_engine.lib.observability.emit_resolver import emit_resolver_resolved
except Exception:  # noqa: BLE001
    def emit_resolver_resolved(helper, source, value, extra=None):  # type: ignore[misc]
        pass

from bytedigger_engine.lib.constitution_loader import load_constitution, resolve_render
from bytedigger_engine.lib import git_port  # noqa: E402  D52228C3 §2.10 — reads route through the injectable git seam

logger = logging.getLogger(__name__)

# D2FCE4CA: required fields the orchestrator-checklist MUST carry.
# Missing any of these → E_ORCHESTRATOR_CHECKLIST_MALFORMED with the field name.
_CHECKLIST_REQUIRED_FIELDS = (
    "schema_version",
    "session_id",
    "complexity",
    "mode",
    "cwd",
    "pre_build_gate_version",
    "written_at_ts",
)

# BF6CB291: bounded-poll defaults for _read_orchestrator_checklist.
# Both overridable via HAL_CHECKLIST_POLL_TIMEOUT_MS / HAL_CHECKLIST_POLL_INTERVAL_MS.
_POLL_TIMEOUT_MS_DEFAULT = 5000
_POLL_INTERVAL_MS_DEFAULT = 100


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)

INJECTION_FILES = ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work", "security-rules")

PRODUCER_RULES_TEXT = """## Anti-Fabrication — Producer Rules

These rules apply to phases that PRODUCE artifacts (specs, architecture, tests, code, fixes). They keep producers honest about staying in the source.

- **Stay within the source.** Do NOT add features, flags, validations, citations, requirements, components, tests, findings, learnings, or behaviors that are not present in the input you were given (FEATURE REQUEST, SPEC, ARCHITECTURE, RED tests, diff, research files — whatever the phase contract names as authoritative).
- **Unclear → Open Questions, not invention.** If the input is silent or ambiguous on a point, list the question. Do NOT invent a plausible answer to keep the document moving.
- **Plausible-but-unrequested → Out of Scope, not Spec.** Phrases like `optional enhancement`, `low-cost high-value`, `for debugging`, `for future flexibility`, `nice to have`, `could also`, `while we are here`, `consider`, `might benefit from` are tells. If you find yourself writing one — STOP and move the item to ## Out of Scope (or drop it entirely).
- **Citations must be real.** Every `path:LINE`, file reference, function name, command-run, or tool-used in your output MUST come from a file/command you actually opened or executed. Do NOT guess paths, line numbers, function names, or tool output.
- **Length signals invention.** If your output is much longer than the task warrants (e.g. 200+ lines of tests for a 50-line script, multi-page architecture for a SIMPLE task), you are inventing scope. Trim back to what the input authorizes.
"""

QUALITY_GATE_TEXT = """## Quality Gate — Zero Corner-Cutting

- ALL issues found MUST be fixed. Not just severity 5+. ALL of them.
- "Acceptable" is NOT a valid status for any finding. Fix it.
- "Pre-existing issue" is NOT a valid reason to skip. Boy Scout Rule: you touched the file, you fix it.
- "Will fix in follow-up" is NOT allowed. There is no follow-up. Fix now.
- "Low severity / cosmetic" still gets fixed. Quality is in the details.
- "Acceptable for MVP" is NOT a valid reason to skip fixing issues.
- If you truly cannot fix something, report BLOCKED with clear explanation — never silently pass it.
- DONE_WITH_CONCERNS is for severity 1-4 issues ONLY, and they must still be fixed — just not blocking.
- Boy Scout Rule: leave every file you touch CLEANER than you found it. No exceptions.

## Anti-Fabrication — Evaluator Rules

These rules apply to phases that EVALUATE artifacts (reviews, satisfaction scores, verdicts). They prevent default-PASS and unearned approvals.

- **Don't pad, don't smooth, don't approve-by-default.** One meaningful test/finding/score beats five trivial ones. Composite scores are MIN not average. Default to FAIL/REVISE if you have not verified — finding nothing must be the result of evidence, not silence.

## Output Rules

- NEVER show inline code blocks, file contents, or large text dumps to the user.
- This includes test code, implementation code, config snippets — NOTHING inline.
- Report ONLY: what was done, what files changed, status (DONE/BLOCKED/etc).
- For tests: report "N tests written/pass/fail" — NOT the test code itself.
- The user reads code themselves — they want summaries, not reproductions.
"""

NO_CONSTITUTION_SENTINEL = "## Constitution\n\nNo project constitution found.\n"
NO_LEARNINGS_SENTINEL = "No learnings matched."
SECURITY_RULES_DISABLED_SENTINEL = (
    "## Secure Coding Defaults\n\nSecurity-rules injection disabled (HAL_SECURITY_RULES_INJECT=0).\n"
)

_FTS_STOP_WORDS = frozenset((
    "a", "an", "the", "in", "on", "of", "to", "for", "with",
    "and", "or", "but", "if", "is", "are", "was", "were", "be",
    "as", "by", "from", "not", "do", "does", "did", "this",
    "that", "these", "those", "it",
))


def _default_memory_db_path() -> str:
    # Rationale for the home-anchored, HAL_DIR-ignoring default lives on the
    # active provider's memory_db_path() (config seam, host-coupled provider
    # carries the concrete path).
    return get_config().memory_db_path()  # type: ignore[attr-defined]


# 06452D44: integration-class detection. Canonical trigger labels — order is
# the canonical output order. "hook" matches case-insensitive (English
# vocabulary word); "PreToolUse"/"SubagentStart" are camelCase identifiers in
# the codebase and match case-sensitive to avoid false positives on prose.
_INTEGRATION_TRIGGERS_CI = ("hook",)
_INTEGRATION_TRIGGERS_CS = ("PreToolUse", "SubagentStart")
_INTEGRATION_TRIGGERS_ORDER = _INTEGRATION_TRIGGERS_CI + _INTEGRATION_TRIGGERS_CS


def _cwd_inside_hal_dir(cwd: Path) -> bool:
    """Return True iff cwd is inside the user's HAL home root.

    Used to gate $HOME/~ constitution paths so HAL state does not leak into
    foreign projects that happen to have their own equivalent directory.
    Resolves via the active provider's home_root() so tests can monkeypatch
    HOME deterministically.

    Note: agreement 52D2D3D5 will refactor `event_log.HAL_DIR` resolution; this
    helper currently uses the provider's home_root() directly so HOME-monkeypatching
    tests work — when 52D2D3D5 lands we may switch to event_log.HAL_DIR for a single source.
    """
    try:
        hal_dir = get_config().home_root()  # type: ignore[attr-defined]
        if cwd.resolve().is_relative_to(hal_dir):
            return True
    except (OSError, ValueError, RuntimeError):
        # RuntimeError: Path.home() raises when HOME unset and pwd lookup fails.
        # In that case we can't determine HAL dir → treat cwd as foreign (safe
        # default: do not leak HAL paths). Memory_db path resolution further
        # downstream will surface E_MEMORY_DB_PATH_UNRESOLVED for the caller.
        return False

    # §2.1 fallback (defect 3): physical check failed — a git worktree of the
    # HAL repo may be checked out outside the physical home root. Resolve its
    # --git-common-dir and test that instead. Any failure folds to False
    # (preserve current safe-default verdict).
    try:
        out = git_port.git_read(
            ["rev-parse", "--git-common-dir"],
            dir_=str(cwd), timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if out.returncode == 0 and out.stdout.strip():
        try:
            common = (Path(cwd) / out.stdout.strip()).resolve()
            return common.is_relative_to(hal_dir)
        except (OSError, ValueError):
            return False
    return False


def _detect_integration_triggers(task_description: str) -> list[str]:
    """Return canonical trigger labels appearing in task_description.

    Substring match (no \\b regex — RED tests use simple "hook"/"runtime"
    words). Returns labels in canonical order with no duplicates. Empty
    input or no matches → empty list.
    """
    if not task_description:
        return []
    haystack_ci = task_description.lower()
    found: list[str] = []
    for label in _INTEGRATION_TRIGGERS_CI:
        if label in haystack_ci:
            found.append(label)
    for label in _INTEGRATION_TRIGGERS_CS:
        if label in task_description:
            found.append(label)
    # Canonical order is preserved by construction; preserve idempotency.
    return found


def _fts_tokens(task_description: str) -> tuple[list[str], int]:
    """Return (surviving_tokens_lowercased, raw_token_count). Single source of truth
    for BOTH _sanitize_fts_query and _or_fts_query — no drift."""
    raw = [t for t in re.split(r"\W+", task_description) if t]
    tokens = [t.lower() for t in raw if len(t) >= 3 and t.lower() not in _FTS_STOP_WORDS]
    return tokens, len(raw)


def _or_fts_query(task_description: str) -> str | None:
    """Explicit-OR FTS5 query over the SAME surviving tokens. None when no tokens.
    Used only as the AND→OR fallback when AND yields zero hits."""
    tokens, _ = _fts_tokens(task_description)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def _sanitize_fts_query(task_description: str) -> tuple[str | None, dict]:
    """Tokenize task description into FTS5-safe query string + diagnostic dict.

    Returns (query, diag) where diag = {raw_token_count, filtered_token_count, mode}.
    mode = 'AND' when ≥3 tokens remain (tight filter for long task descriptions);
    mode = 'OR' when ≤2 tokens (preserve existing behaviour for single-concept probes);
    mode = 'none' when no tokens survive filtering.

    Raw user input may contain quotes, hyphens, asterisks, or parens that make
    FTS5 throw a parse error. We split on non-word chars, drop stop words and
    short tokens (len<3), then wrap each remaining token in double quotes so FTS5
    treats them as phrase literals. AND-mode uses space-separated quoted phrases
    (FTS5 implicit-AND); OR-mode uses explicit OR joins.
    """
    tokens, raw_count = _fts_tokens(task_description)
    diag: dict[str, int | str] = {"raw_token_count": raw_count, "filtered_token_count": len(tokens)}
    if not tokens:
        diag["mode"] = "none"
        return None, diag
    if len(tokens) >= 3:
        diag["mode"] = "AND"
        return " ".join(f'"{t}"' for t in tokens), diag
    diag["mode"] = "OR"
    return " OR ".join(f'"{t}"' for t in tokens), diag


def _resolve_inject_ts_path() -> Path:
    """Resolve path to inject-learnings.ts CLI.
    Env-overridable via HAL_INJECT_LEARNINGS_TS for tests (§1h pattern).
    Default: relative to this file's engine repo root (SYSTEM/cli/build/).
    """
    # engine_py/workflows/phase_05_inject.py → engine_py/ → SYSTEM/cli/build/
    build_dir = Path(__file__).resolve().parents[3]
    return get_config().path("HAL_INJECT_LEARNINGS_TS", build_dir / "inject-learnings.ts")


def _query_memory_learnings(
    task_description: str,
    memory_db_path: str,
    is_default: bool = False,
    cfg: dict | None = None,
    session_id: str | None = None,
) -> tuple[str | None, StepResult | None, str]:
    """Query memories_fts for build-relevant rows matching task_description.

    DB access delegated to inject-learnings.ts via subprocess (09F250F4).
    Token-filtering, query-mode selection, bullet formatting remain here.

    session_id: optional build session id; when truthy, forwarded to
    inject-learnings.ts as --session-id for GH565 inject telemetry.

    Types included: learning, decision, integration, integration_test.
    Types excluded: preference (user prefs, not build-relevant),
                    semantic (general user memories, not build knowledge).

    Path check is performed BEFORE sanitize so a bad path is always surfaced,
    even when the task yields no keywords after stop-word filtering.

    Returns:
        (block, None, "")        — success with ≥1 match; block ready to write
        (None, None, "[suffix]") — 0 matches; caller writes sentinel + suffix for diagnostics
        (None, err, "")          — hard fail; caller must surface immediately

    Diagnostic suffixes (case labels map to _write_injection_files comments):
        "[no_keywords_extracted]"                                          — case (a)
        "[fts_hits=0]"                                                     — case (b)
        "[fts_hits=N, build_hits=0, type_filter=learning|decision|integration|integration_test]"
                                                                           — case (c)
        "[memory_db_unavailable]"                                          — case (e): default path missing in foreign cwd, graceful skip
    """
    # Resolve bm25 floor from cfg. Default 0.0 (admits any real FTS5 bm25 match,
    # which is always ≤ 0). Override via cfg to TIGHTEN (negative floor rejects
    # weak matches on a large corpus where bm25 hits -2..-5 range).
    # bm25 score is corpus-size-dependent — on tiny fixtures it's ~0, on prod ~-3.
    # Explicit None-check handles cfg=None, cfg={}, cfg={"phase_05_bm25_floor": None}.
    v = (cfg or {}).get("phase_05_bm25_floor")
    bm25_floor = float(v) if v is not None else 0.0

    # LOW: path check FIRST — bad path must surface even for stop-words-only tasks.
    expanded = os.path.expanduser(memory_db_path)
    if expanded.startswith("~"):
        # LOW-1: $HOME unresolved — os.path.expanduser returns literal ~/... when
        # HOME is unset and pwd entry is missing. Surface as distinct error code
        # so operators see the env issue rather than a misleading "not found".
        return None, StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="write_injection_files",
            error=f"$HOME could not be resolved; memory.db path remains unexpanded: {memory_db_path}",
            error_code="E_MEMORY_DB_PATH_UNRESOLVED",
        ), ""
    memory_db_path = expanded  # use expanded path for all subsequent checks

    if not os.path.exists(memory_db_path):
        # DA8D22E0: when default path is missing AND cwd is outside HAL,
        # foreign projects shouldn't hard-fail on a HAL-specific DB they have
        # no reason to provide. Explicit-path-missing OR HAL-dogfood still
        # hard-fails (real misconfiguration).
        if is_default and not _cwd_inside_hal_dir(Path.cwd()):
            return None, None, "[memory_db_unavailable]"
        return None, StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="write_injection_files",
            error=f"memory.db not found at {memory_db_path}",
            error_code="E_MEMORY_DB_NOT_FOUND",
        ), ""

    fts_query, diag = _sanitize_fts_query(task_description)
    if fts_query is None:
        # Case (a): stop-words-only or empty after strip
        return None, None, "[no_keywords_extracted]"

    # §1h: bun binary env-overridable.
    bun_bin = get_config().binary("HAL_BUN_BIN", "bun")
    inject_ts = _resolve_inject_ts_path()

    # §2.4 discriminated 3-state return type:
    #   ("rows", total_hits, rows)          — success
    #   ("retry",)                          — learning_entries_absent → caller retries with --absent
    #   ("hard", error_code, message)       — schema_missing / db_unreadable → caller hard-fails
    #   ("sentinel",)                       — any other failure → graceful [memory_db_unavailable]
    _TS_ERROR_TO_PY: dict[str, tuple] = {
        "schema_missing": ("hard", "E_MEMORY_DB_SCHEMA", "memory.db exists but has no memories schema"),
        "db_unreadable": ("hard", "E_MEMORY_DB_UNREADABLE", "memory.db file is not a valid SQLite database"),
        "db_empty": ("hard", "E_MEMORY_DB_EMPTY", f"memory.db is empty (no memories rows) at {memory_db_path}"),
        "learning_entries_absent": ("retry",),
    }

    def _run_inject(match_expr: str, mode_flag: str) -> tuple:
        """Invoke inject-learnings.ts subprocess; parse JSON; return discriminated 3-state tuple.

        Returns:
            ("rows", total_hits, rows_list) — success; caller uses the data
            ("retry",)                      — learning_entries absent; caller should retry --absent
            ("hard", error_code, message)   — hard failure; caller must surface immediately
            ("sentinel",)                   — soft failure; caller emits graceful sentinel
        """
        try:
            argv = [
                bun_bin,
                str(inject_ts),
                "--match", match_expr,
                "--bm25-floor", str(bm25_floor),
                "--limit", "5",
                "--db", memory_db_path,
                mode_flag,
            ]
            if session_id:
                argv.extend(["--session-id", session_id])
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _emit_safe("learning_inject_callout_failed", {
                "reason": f"subprocess error: {exc}",
                "exit_code": -1,
            })
            return ("sentinel",)

        # Defense-in-depth (BUG 1): parse the LAST non-empty stdout line so that any
        # stray console.log lines before the JSON result do not break JSON parsing.
        stdout_lines = [line for line in proc.stdout.splitlines() if line.strip()]
        last_line = stdout_lines[-1] if stdout_lines else ""

        try:
            data = json.loads(last_line) if last_line else {}
        except (json.JSONDecodeError, ValueError) as exc:
            _emit_safe("learning_inject_callout_failed", {
                "reason": f"unparseable stdout: {exc}",
                "exit_code": proc.returncode,
            })
            return ("sentinel",)

        if not isinstance(data, dict):
            _emit_safe("learning_inject_callout_failed", {
                "reason": "stdout JSON was not an object",
                "exit_code": proc.returncode,
            })
            return ("sentinel",)

        # §2.4: check for discriminated error kinds BEFORE checking returncode,
        # so that hard-fail codes are surfaced even when TS exits non-zero.
        error_kind = data.get("error")
        if error_kind is not None:
            mapped = _TS_ERROR_TO_PY.get(error_kind)
            if mapped is not None:
                if mapped[0] == "hard":
                    return mapped
                if mapped[0] == "retry":
                    return mapped
            # Includes "memory_db_unavailable" and any unknown error kind
            _emit_safe("learning_inject_callout_failed", {
                "reason": error_kind,
                "exit_code": proc.returncode,
            })
            return ("sentinel",)

        if proc.returncode != 0:
            _emit_safe("learning_inject_callout_failed", {
                "reason": "non-zero exit",
                "exit_code": proc.returncode,
            })
            return ("sentinel",)

        # Parse rows into (content, confidence, score) tuples to match
        # the shape _execute_fts_query previously returned.
        raw_rows = data.get("rows", [])
        rows = [(r["content"], r["confidence"], r["score"]) for r in raw_rows]
        total_hits = int(data.get("fts_total_hits", 0))
        return ("rows", total_hits, rows)

    # Mode detection: try --present first (production DBs always have learning_entries).
    # If TS emits {"error":"learning_entries_absent"} → retry with --absent.
    # Hard errors (schema_missing, db_unreadable) → surface immediately, no retry.
    inject_result = _run_inject(fts_query, "--present")
    mode_used = "present"

    if inject_result[0] == "hard":
        _, error_code, message = inject_result
        return None, StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="write_injection_files",
            error=message,
            error_code=error_code,
        ), ""

    if inject_result[0] == "retry":
        # learning_entries absent → retry with --absent
        inject_result = _run_inject(fts_query, "--absent")
        mode_used = "absent"
        # --absent path may also hard-fail (e.g. schema_missing on empty DB)
        if inject_result[0] == "hard":
            _, error_code, message = inject_result
            return None, StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="write_injection_files",
                error=message,
                error_code=error_code,
            ), ""

    if inject_result[0] == "sentinel":
        # Both branches failed → DB unavailable (case e)
        return None, None, "[memory_db_unavailable]"

    # inject_result[0] == "rows"
    _, total_fts_hits, rows = inject_result
    fallback = None
    if total_fts_hits == 0 and diag["mode"] == "AND":
        or_query = _or_fts_query(task_description)
        if or_query is not None:
            inject_result2 = _run_inject(or_query, f"--{mode_used}")
            if inject_result2[0] == "rows":
                _, total_fts_hits, rows = inject_result2
                fallback = "AND_to_OR"
                diag["mode"] = "OR" if total_fts_hits else "AND"

    # 1E588980: emit telemetry once per FTS-executed path (match AND no-match).
    # Placement: after fetchall(), before branching on rows, so both paths are covered.
    _emit_safe("learning_query_resolved", {
        "raw_token_count": diag["raw_token_count"],
        "filtered_token_count": diag["filtered_token_count"],
        "query_mode": diag["mode"],
        "fts_total_hits": total_fts_hits,
        "returned_count": len(rows),
        "top_bm25": rows[0][2] if rows else None,
        "bm25_floor": bm25_floor,
        "fallback": fallback,
    })

    if not rows:
        if total_fts_hits == 0:
            # Case (b): no FTS matches at all
            suffix = "[fts_hits=0]"
        else:
            # Case (c): FTS matched rows but type filter dropped them all
            suffix = (
                f"[fts_hits={total_fts_hits}, build_hits=0, "
                f"type_filter=learning|decision|integration|integration_test]"
            )
        return None, None, suffix

    # row[0]=content, row[1]=confidence, row[2]=bm25 score (score not included in output).
    bullets = "\n".join(f"- {row[0]}" for row in rows)
    return bullets, None, ""


# Agreement 12334B92: required fields enumerated in ONE place. When the set
# grows in future, _validate_org_config_required reports them all at once via
# E_CTX_MISSING_FIELDS rather than fail-fast on the first missing key.
_REQUIRED_ORG_CONFIG_FIELDS: tuple[str, ...] = ("scratchpad_dir",)


def _validate_org_config_required(cfg: dict) -> list[str]:
    """Return sorted list of required org_config keys that are missing/falsy.

    Empty list = all required keys present and non-empty. Treats `""`, `None`,
    and absent keys uniformly as missing — matches the engine boundary's
    truthy-check semantics for `required_ctx_fields`.
    """
    missing = [k for k in _REQUIRED_ORG_CONFIG_FIELDS if not cfg.get(k)]
    return sorted(missing)


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        # Defense in depth — _write_injection_files runs
        # _validate_org_config_required first and returns E_CTX_MISSING_FIELDS
        # before ever calling here. ValueError remains as a safety net for any
        # future caller that bypasses the validator.
        raise ValueError("org_config.scratchpad_dir required for phase_05_inject")
    return Path(raw).expanduser().resolve()


def _validate_checklist_dict(parsed, source_desc: str) -> StepResult | None:
    """2811D55F: shared validator for file-loaded and inline-provided checklists.

    Returns None if `parsed` is a dict containing all _CHECKLIST_REQUIRED_FIELDS.
    Returns an error StepResult (E_ORCHESTRATOR_CHECKLIST_MALFORMED, recoverable=False)
    on type mismatch or missing field. `source_desc` is interpolated into the error
    message so callers can distinguish file-on-disk vs inline-ctx sources.
    """
    if not isinstance(parsed, dict):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="read_orchestrator_checklist",
            error=(
                f"orchestrator-checklist {source_desc} malformed: "
                f"expected JSON object, got {type(parsed).__name__}"
            ),
            error_code="E_ORCHESTRATOR_CHECKLIST_MALFORMED",
            recoverable=False,
        )
    for field_name in _CHECKLIST_REQUIRED_FIELDS:
        if field_name not in parsed:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="read_orchestrator_checklist",
                error=(
                    f"orchestrator-checklist {source_desc} malformed: "
                    f"missing required field '{field_name}'"
                ),
                error_code="E_ORCHESTRATOR_CHECKLIST_MALFORMED",
                recoverable=False,
            )
    return None


def _read_orchestrator_checklist(ctx, _prev) -> StepResult:
    """D2FCE4CA: HARD-FAIL gate ensuring `pre-build-gate.ts` ran for this session.

    Reads `<scratchpad>/.orchestrator-checklist.json` written by the gate.

    BF6CB291: single-shot check replaced with a bounded poll loop to tolerate
    write races when non-canonical invocation paths start the engine before
    pre-build-gate.ts has flushed the file. Polls for up to
    HAL_CHECKLIST_POLL_TIMEOUT_MS (default 5000ms) at
    HAL_CHECKLIST_POLL_INTERVAL_MS (default 100ms) intervals.

    2811D55F: when the file is absent after the poll, falls back to
    `ctx.org_config.checklist_inline` so portable invocations (foreign /build
    without pre-build-gate.ts) can supply the checklist directly via ctx-json.
    File-on-disk wins over inline. Missing or malformed checklist (in either
    source) → recoverable=False error so the pipeline halts at phase_05_inject
    rather than proceeding blindly.
    """
    scratchpad = _resolve_scratchpad(ctx)
    checklist_path = scratchpad / ".orchestrator-checklist.json"
    cfg = ctx.org_config or {}

    timeout_ms = get_config().timeout_ms("HAL_CHECKLIST_POLL_TIMEOUT_MS", _POLL_TIMEOUT_MS_DEFAULT)
    interval_ms = get_config().timeout_ms("HAL_CHECKLIST_POLL_INTERVAL_MS", _POLL_INTERVAL_MS_DEFAULT)

    poll_count = 0
    broke_out_with_file = False
    start = time.monotonic()
    while True:
        poll_count += 1
        if checklist_path.is_file():
            broke_out_with_file = True
            break
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if elapsed_ms >= timeout_ms:
            break
        time.sleep(interval_ms / 1000.0)
    final_elapsed_ms = int((time.monotonic() - start) * 1000)

    inline = cfg.get("checklist_inline")
    if broke_out_with_file:
        found_source = "file"
    elif inline is not None:
        found_source = "inline"
    else:
        found_source = "none"
    _emit_safe("checklist_poll_attempts", {
        "poll_count": poll_count,
        "elapsed_ms": final_elapsed_ms,
        "timeout_ms": timeout_ms,
        "found_source": found_source,
    })

    if broke_out_with_file:
        try:
            raw = checklist_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="read_orchestrator_checklist",
                error=f"orchestrator-checklist.json malformed at {checklist_path}: {exc}",
                error_code="E_ORCHESTRATOR_CHECKLIST_MALFORMED",
                recoverable=False,
            )
        err = _validate_checklist_dict(parsed, f"at {checklist_path}")
        if err is not None:
            return err
        _emit_safe("orchestrator_checklist_received", {"checklist": parsed, "source": "file"})
        return StepResult(
            status="ok",
            data={"checklist": parsed},
            duration_ms=0,
            step_name="read_orchestrator_checklist",
        )

    # 2811D55F: file absent after poll — fall back to ctx-json inline checklist for
    # portable invocations without pre-build-gate.ts.
    if inline is not None:
        err = _validate_checklist_dict(inline, "inline ctx-json")
        if err is not None:
            return err
        _emit_safe(
            "orchestrator_checklist_received",
            {"checklist": inline, "source": "inline_ctx"},
        )
        return StepResult(
            status="ok",
            data={"checklist": inline},
            duration_ms=0,
            step_name="read_orchestrator_checklist",
        )

    return StepResult(
        status="error",
        data=None,
        duration_ms=0,
        step_name="read_orchestrator_checklist",
        error=(
            f"orchestrator-checklist.json missing at {checklist_path}; "
            f"pre-build-gate.ts was not invoked for session {ctx.session_id}. "
            f"For portable invocations, provide org_config.checklist_inline = {{...}} "
            f"matching the gate schema."
        ),
        error_code="E_ORCHESTRATOR_CHECKLIST_MISSING",
        recoverable=False,
    )


def _resolve_constitution_project(cfg: dict) -> str | None:
    """Returns the SpecKit project name: cfg['project'] (truthy str,
    stripped) wins; else the basename of `git rev-parse --show-toplevel`
    run against the cwd; else None on any failure (fold, mirrors
    `_cwd_inside_hal_dir` L178-185 style — never raises)."""
    proj = cfg.get("project")
    if isinstance(proj, str) and proj.strip():
        return proj.strip()

    try:
        out = git_port.git_read(
            ["rev-parse", "--show-toplevel"],
            dir_=str(Path.cwd()), timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode == 0 and out.stdout.strip():
        return Path(out.stdout.strip()).name
    return None


def _resolve_constitution_layers(cfg: dict) -> dict:
    """Single resolve source (§1g) for constitution discovery. Returns
    {"path": str|None, "source": str, "layers": [{"path","format","layer"}...],
    "project": str|None, "hal_internal": bool}. Precedence: explicit ▸
    checklist ▸ layered (project speckit/md + global md) ▸ none.
    """
    explicit = cfg.get("constitution_path")
    if explicit:
        p = Path(os.path.expandvars(explicit)).expanduser()
        if p.is_file():
            resolved = str(p.resolve())
            fmt = "speckit" if resolved.endswith(".json") else "markdown"
            return {
                "path": resolved,
                "source": "explicit",
                "layers": [{"path": resolved, "format": fmt, "layer": "explicit"}],
                "project": None,
                "hal_internal": False,
            }

    # AE0F261A: read constitution_path from orchestrator checklist if pre-build-gate resolved one.
    checklist = cfg.get("checklist") or {}
    chk_path = checklist.get("constitution_path")
    if chk_path:
        p = Path(os.path.expandvars(chk_path)).expanduser()
        if p.is_file():
            resolved = str(p.resolve())
            fmt = "speckit" if resolved.endswith(".json") else "markdown"
            return {
                "path": resolved,
                "source": "checklist",
                "layers": [{"path": resolved, "format": fmt, "layer": "checklist"}],
                "project": None,
                "hal_internal": False,
            }

    # Portability: when cwd is outside any HAL home-root ancestor, drop $HOME/~
    # search paths so HAL's constitution does not leak into foreign projects.
    inside_hal = _cwd_inside_hal_dir(Path.cwd())
    project_name = _resolve_constitution_project(cfg)

    project_layer = None
    if project_name is not None:
        for root in get_config().speckit_search_roots():  # type: ignore[attr-defined]
            if not inside_hal and ("$HOME" in root or root.startswith("~")):
                continue
            candidate = Path(os.path.expandvars(root)).expanduser() / project_name / "constitution.json"
            if candidate.is_file():
                project_layer = {"path": str(candidate.resolve()), "format": "speckit", "layer": "project"}
                break

    if project_layer is None:
        for raw in get_config().constitution_search_paths():  # type: ignore[attr-defined]
            if "$HOME" in raw or raw.startswith("~"):
                continue
            p = Path(os.path.expandvars(raw)).expanduser()
            if p.is_file():
                project_layer = {"path": str(p.resolve()), "format": "markdown", "layer": "project"}
                break

    global_layer = None
    if inside_hal:
        for raw in get_config().constitution_search_paths():  # type: ignore[attr-defined]
            if not ("$HOME" in raw or raw.startswith("~")):
                continue
            p = Path(os.path.expandvars(raw)).expanduser()
            if p.is_file():
                global_layer = {"path": str(p.resolve()), "format": "markdown", "layer": "global"}
                break

    layers = []
    if global_layer is not None:
        layers.append(global_layer)
    if project_layer is not None:
        layers.append(project_layer)

    if project_layer is not None:
        path = project_layer["path"]
        source = "speckit" if project_layer["format"] == "speckit" else "search"
    elif global_layer is not None:
        path = global_layer["path"]
        source = "search"
    else:
        path = None
        source = "none"

    return {
        "path": path,
        "source": source,
        "layers": layers,
        "project": project_name,
        "hal_internal": inside_hal,
    }


def _resolve_constitution(cfg: dict) -> tuple[str | None, str]:
    """Returns (resolved_path_or_None, source) — source in
    {"explicit","checklist","search","none"} (plus "speckit" — the new
    value). Contract-frozen 2-tuple; thin adapter over
    `_resolve_constitution_layers` (§1g single resolve source).
    """
    result = _resolve_constitution_layers(cfg)
    return result["path"], result["source"]


def _discover_constitution(ctx, _prev) -> StepResult:
    cfg = ctx.org_config or {}

    # AE0F261A: checklist thread-through if not already in cfg
    if "checklist" not in cfg and isinstance(_prev, StepResult) and isinstance(_prev.data, dict):
        cfg = {**cfg, "checklist": _prev.data.get("checklist", {})}

    result = _resolve_constitution_layers(cfg)
    path = result["path"]
    source = result["source"]

    # Data-shape rule (gate cycle-2 must-fix 1): explicit/checklist keep the
    # exact 2-key shape (GH294 test_ac8 lock); layered discovery (incl.
    # "none") additionally carries constitution_layers/project/hal_internal.
    if source in ("explicit", "checklist"):
        data = {"constitution_path": path, "source": source}
    else:
        data = {
            "constitution_path": path,
            "source": source,
            "constitution_layers": result["layers"],
            "project": result["project"],
            "hal_internal": result["hal_internal"],
        }

    # Telemetry is best-effort: resolve_render() re-reads/re-parses layer
    # files for the emit-only overridden/merged_count/format metadata, so any
    # OSError/parse failure here must never escape past the emit call.
    try:
        extra: dict[str, object] = {"cwd": str(Path.cwd())}
        if source not in ("explicit", "checklist"):
            render = resolve_render(list(result["layers"]))
            extra.update({
                "layers": list(result["layers"]),
                "overridden": render["overridden"],
                "merged_count": render["merged_count"],
                "hal_internal": result["hal_internal"],
                "format": render["format"],
                "project": result["project"],
            })
        emit_resolver_resolved("constitution", source, path or "", extra=extra)
    except Exception:  # noqa: BLE001
        pass

    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="discover_constitution",
    )


def _write_injection_files(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="write_injection_files",
            error="prev step did not produce constitution_path",
            error_code="E_MISSING_PREV_DATA",
        )

    # Agreement 12334B92: enumerate ALL missing required org_config fields up
    # front and surface them via E_CTX_MISSING_FIELDS with `data.missing_fields`
    # — operator/agent gets the full list in one shot. Today only
    # `scratchpad_dir` is required, but the validator scales with
    # _REQUIRED_ORG_CONFIG_FIELDS.
    cfg_for_validation = ctx.org_config or {}
    missing_fields = _validate_org_config_required(cfg_for_validation)
    if missing_fields:
        return StepResult(
            status="error",
            data={"missing_fields": missing_fields},
            duration_ms=0,
            step_name="write_injection_files",
            error=(
                "phase_05_inject: required org_config field(s) missing: "
                + ", ".join(missing_fields)
            ),
            error_code="E_CTX_MISSING_FIELDS",
            recoverable=False,
        )

    scratchpad = _resolve_scratchpad(ctx)
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)

    cfg = ctx.org_config or {}
    active_work = cfg.get("active_work_context") or ""
    constitution_path = prev.data.get("constitution_path")

    explicit_block = cfg.get("hal_memory_block")
    if explicit_block:
        hal_memory = explicit_block
    else:
        task_description = normalize_task_description(cfg)
        if task_description is not None:
            explicit_db_path = cfg.get("memory_db_path")
            is_default = not explicit_db_path
            db_path = explicit_db_path or _default_memory_db_path()
            # Cases (a)/(b)/(c)/(e): queried=None, suffix carries diagnostic;
            # case (success): queried=block, suffix="";
            # case (hard-fail): err is not None.
            # NOTE: _query_memory_learnings handles expanduser internally (path check first).
            queried, err, suffix = _query_memory_learnings(
                task_description, db_path, is_default=is_default, cfg=cfg, session_id=ctx.session_id
            )
            if err is not None:
                return err
            if queried is not None:
                hal_memory = queried
            else:
                # Append diagnostic suffix so operators can distinguish case (a)/(b)/(c).
                # Existing assertions use `NO_LEARNINGS_SENTINEL in hal_memory` — substring
                # match still holds because suffix is appended after the base sentinel.
                hal_memory = f"{NO_LEARNINGS_SENTINEL} {suffix}".rstrip() if suffix else NO_LEARNINGS_SENTINEL
        else:
            # Case (d): orchestrator never asked for a query — plain sentinel, no diagnostic.
            hal_memory = NO_LEARNINGS_SENTINEL

    # 06452D44: integration-class detection. AFTER hal_memory resolution,
    # BEFORE file writes. Empty trigger list → no emission (silent).
    task_description_for_triggers = normalize_task_description(cfg) or ""
    triggers = _detect_integration_triggers(task_description_for_triggers)
    if triggers:
        _emit_safe("integration_class_detected", {"triggers": triggers})

    written: dict[str, int] = {}

    # LOW-2: prefix hal-memory content with an HTML comment marker so automated
    # tools can detect match vs no-match without substring-scanning the prose.
    # NO_LEARNINGS_SENTINEL constant value is unchanged — existing substring
    # assertions (`NO_LEARNINGS_SENTINEL in body`) continue to hold because the
    # sentinel appears verbatim after the comment marker on the next line.
    if hal_memory == NO_LEARNINGS_SENTINEL or hal_memory.startswith(NO_LEARNINGS_SENTINEL + " "):
        marker = "<!-- HAL_MEMORY:NO_MATCH -->"
    else:
        count = hal_memory.count("\n- ") + (1 if hal_memory.startswith("- ") else 0)
        marker = f"<!-- HAL_MEMORY:MATCHED count={count} -->"

    hal_body = f"## HAL Memory\n\n{marker}\n{hal_memory}\n"
    (inj / "hal-memory.md").write_text(hal_body, encoding="utf-8")
    written["hal-memory.md"] = len(hal_body.encode("utf-8"))

    layers = prev.data.get("constitution_layers")
    if layers is None:
        # Legacy/explicit/checklist callers without constitution_layers: format-
        # agnostic single-path fallback — md stays byte-identical passthrough,
        # .json is rendered (never raw JSON).
        if constitution_path and Path(constitution_path).is_file():
            const_body = load_constitution(constitution_path)
        else:
            const_body = NO_CONSTITUTION_SENTINEL
    else:
        render = resolve_render(layers)
        const_body = render["markdown"] if render["markdown"] is not None else NO_CONSTITUTION_SENTINEL
    (inj / "constitution.md").write_text(const_body, encoding="utf-8")
    written["constitution.md"] = len(const_body.encode("utf-8"))

    (inj / "quality-gate.md").write_text(QUALITY_GATE_TEXT, encoding="utf-8")
    written["quality-gate.md"] = len(QUALITY_GATE_TEXT.encode("utf-8"))

    (inj / "producer-rules.md").write_text(PRODUCER_RULES_TEXT, encoding="utf-8")
    written["producer-rules.md"] = len(PRODUCER_RULES_TEXT.encode("utf-8"))

    # engine_py/workflows/phase_05_inject.py → engine_py/ → SYSTEM/cli/build/
    build_dir = Path(__file__).resolve().parents[3]

    if get_config().gate_enabled("HAL_SECURITY_RULES_INJECT"):
        rules_path = get_config().path(
            "HAL_SECURITY_RULES_PATH",
            default_security_asset(
                "secure-codegen-rules.md", build_dir / "security" / "secure-codegen-rules.md"
            ),
        )
        try:
            security_body = Path(rules_path).read_text(encoding="utf-8")
        except OSError as exc:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="write_injection_files",
                error=f"secure-codegen-rules not found at {rules_path}: {exc}",
                error_code="E_SECURITY_RULES_MISSING",
            )
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_SECURITY_RULES_INJECT",
            "step": "write_injection_files",
            "reason": "env_kill_switch",
        })
        security_body = SECURITY_RULES_DISABLED_SENTINEL
    (inj / "security-rules.md").write_text(security_body, encoding="utf-8")
    written["security-rules.md"] = len(security_body.encode("utf-8"))

    active_body = f"## Active Work Context\n\n{active_work}\n"
    (inj / "active-work.md").write_text(active_body, encoding="utf-8")
    written["active-work.md"] = len(active_body.encode("utf-8"))

    # Inject build-manifest when CWD is inside SYSTEM/cli/build/ (recursive /build case).
    cwd = Path.cwd().resolve()
    is_build_cwd = cwd.is_relative_to(build_dir)
    if is_build_cwd:
        manifest_path = build_dir / "MANIFEST.md"
        try:
            manifest_body = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="write_injection_files",
                error=f"MANIFEST.md not found at {manifest_path}: {exc}",
                error_code="E_MANIFEST_MISSING",
            )
        (inj / "build-manifest.md").write_text(manifest_body, encoding="utf-8")
        written["build-manifest.md"] = len(manifest_body.encode("utf-8"))

    files = [str(inj / f"{name}.md") for name in INJECTION_FILES]
    if is_build_cwd:
        files.append(str(inj / "build-manifest.md"))

    return StepResult(
        status="ok",
        data={
            "injection_dir": str(inj),
            "files": files,
            "bytes_written": written,
            "constitution_used": constitution_path,
        },
        duration_ms=0,
        step_name="write_injection_files",
    )


def phase_05_inject_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_05_inject",
        steps=[
            StepContract(
                name="read_orchestrator_checklist",
                execute=_read_orchestrator_checklist,
                required_ctx_fields=["org_config"],
            ),
            StepContract(name="discover_constitution", execute=_discover_constitution),
            StepContract(name="write_injection_files", execute=_write_injection_files),
        ],
    )
