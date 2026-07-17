"""GH446: single-source catalogue of HAL_* env-flag tokens used by engine_py.

Pure stdlib, no imports of any scanned prod module (manifest-safe, no env
reads). See the GH446 flag-routing frozen spec (2026-07-09, EC898987) in the
host repo's Decisions memory.
"""
from __future__ import annotations

import re
from pathlib import Path

KINDS = ("gate", "flag", "path", "int", "str", "binary")

# Accessor call forms whose first string-literal argument is a HAL_* flag
# token. Also covers direct os.environ.get/os.environ[.../os.getenv reads.
_ACCESSOR_NAMES = (
    "gate_enabled",
    "flag",
    "timeout_ms",
    "binary",
    "path",
    "env_opt",
    "int_value",
)

_TOKEN_PATTERN = re.compile(
    r"("
    r"(?:" + "|".join(re.escape(n) for n in _ACCESSOR_NAMES) + r")\s*\(\s*"
    r"|os\.environ\.get\(\s*"
    r"|os\.environ\[\s*"
    r"|os\.getenv\(\s*"
    r")"
    r"[\"'](?P<name>HAL_[A-Z0-9_]+)[\"']"
)

_EXCLUDE_DIR_NAMES = {"tests", "__tests__"}
_EXCLUDE_FILE_TOKENS = ("conftest", "hal_config_provider.py")


def _is_excluded(path: Path) -> bool:
    if any(part in _EXCLUDE_DIR_NAMES for part in path.parts):
        return True
    name = path.name
    if name.startswith("conftest"):
        return True
    if name == "hal_config_provider.py":
        return True
    return False


def unregistered_tokens_in_files(paths: list) -> list[str]:
    """Scan the given .py files with the same accessor/env-read regexes as
    discover_flag_reads(); return sorted unique HAL_* tokens NOT in FLAGS."""
    found: set[str] = set()
    for p in paths:
        path = Path(p)
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for m in _TOKEN_PATTERN.finditer(text):
            token = m.group("name")
            if token not in FLAGS:
                found.add(token)
    return sorted(found)


def discover_flag_reads(root: Path) -> dict[str, list[str]]:
    """Regex-scan *.py under root for HAL_* flag reads. Returns token → [file:line, ...]."""
    root = Path(root)
    results: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        if _is_excluded(path.relative_to(root)):
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _TOKEN_PATTERN.finditer(line):
                token = m.group("name")
                site = f"{path.relative_to(root)}:{lineno}"
                results.setdefault(token, []).append(site)
    return results


# ROUTED_MODULES: the 7 files migrated by GH446 (§2.2). Flag-routing lint
# module-set seed; core-manifest modules are additionally always checked.
ROUTED_MODULES: tuple[str, ...] = (
    "lib/dbos_setup.py",
    "lib/run_allowlist.py",
    "lib/directed_repair.py",
    "scripts/spec_lint/lint_spec.py",
    "workflows/phase_5_devops_scan.py",
    "workflows/phase_45_spec.py",
    "workflows/phase_6_review.py",
)

FLAGS: dict[str, dict] = {
    "HAL_DBOS_DB_PATH": {
        "kind": "path",
        "default": None,
        "module": "lib/dbos_setup.py",
        "description": "Override path for the DBOS durable-state sqlite file.",
    },
    "HAL_ENGINE_DURABLE_BACKEND": {
        "kind": "str",
        "default": "native",
        "module": "lib/phase_sentinel.py",
        "description": "Durable-execution backend: 'native' (default since GH839 B1, event-log phase_sentinel) | 'dbos' (explicit fallback; removed in B2). GH612 Ship B.",
    },
    "HAL_DIR": {
        "kind": "path",
        "default": None,
        "module": "lib/dbos_setup.py",
        "description": "HAL install-root override used to derive default paths.",
    },
    "HAL_FROZEN_SHORT_CIRCUIT": {
        "kind": "gate",
        "default": "1",
        "module": "skip_logic.py",
        "description": "Kill-switch for GH531 frozen-spec short-circuit (phase_1 self-skip + SIMPLE relax); '0' restores pre-GH531 behavior.",
    },
    "HAL_RED_PREFLIGHT_DELTA_RETRY": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: =0 restores terminal behavior of RED lint preflight batch (no delta-retry) (GH602).",
    },
    "HAL_RUNPY_HARD_EXIT": {
        "kind": "gate",
        "default": "1",
        "module": "lib/dbos_setup.py",
        "description": "Kill-switch: HAL_RUNPY_HARD_EXIT=0 restores legacy sys.exit instead of os._exit.",
    },
    "HAL_SCOPE_ZONES_CONFIG": {
        "kind": "path",
        "default": None,
        "module": "lib/run_allowlist.py",
        "description": "Override path for the scope-zones allowlist config file.",
    },
    "HAL_DIRECTED_REPAIR": {
        "kind": "gate",
        "default": "1",
        "module": "lib/directed_repair.py",
        "description": "Kill-switch: HAL_DIRECTED_REPAIR=0 disables directed-repair retries.",
    },
    "HAL_MODEL_UNAVAILABLE": {
        "kind": "str",
        "default": None,
        "module": "lib/model_config.py",
        "description": "CSV of model aliases/families treated as unavailable; role chains in model_config skip them (GH386).",
    },
    "HAL_CONSUMER_DISPATCH_MAX": {
        "kind": "int",
        "default": 0,
        "module": "scripts/spec_lint/lint_spec.py",
        "description": "Cap on consumer-dispatch findings reported by lint_spec.",
    },
    "HAL_INTEGRITY_VERDICT_RETRY_MAX": {
        "kind": "int",
        "default": 1,
        "module": "workflows/phase_5_integrity.py",
        "description": "GH786: bounded completeness-retry count for the integrity reviewer when it omits a standalone VERDICT marker (0 disables; fail-closed preserved).",
    },
    "HAL_DEVOPS_SCAN_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_devops_scan.py",
        "description": "Kill-switch: HAL_DEVOPS_SCAN_GATE=0 restores legacy fail-open devops-scan behavior.",
    },
    "HAL_DEVOPS_SCAN_CONFIG": {
        "kind": "path",
        "default": None,
        "module": "workflows/phase_5_devops_scan.py",
        "description": "Override path for the devops-scan fail-severities config JSON.",
    },
    "HAL_DEVOPS_SCAN_ALLOWLIST": {
        "kind": "path",
        "default": None,
        "module": "workflows/phase_5_devops_scan.py",
        "description": "Override path for the devops-scan finding allowlist file.",
    },
    "HAL_SPEC_DELTA_RETRY": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_DELTA_RETRY=0 restores legacy cycle>=2 scaffold assembly.",
    },
    "HAL_SURGICAL_REVISE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SURGICAL_REVISE=0 disables point-patch surgical revise (falls back to full-rewrite delta retry).",
    },
    "HAL_SPEC_HIGH_BINDING_PARITY": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_HIGH_BINDING_PARITY=0 restores the pre-GH729 cycle>=2 bodies byte-identically (drops the high-binding spec-rule block).",
    },
    "HAL_DELTA_REREVIEW": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_DELTA_REREVIEW=0 disables delta re-review — restricted reviewer falls back to full-spec embed.",
    },
    "HAL_IMPL_DELTA_RETRY": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_IMPL_DELTA_RETRY=0 restores legacy cycle>=2 full RED scaffold assembly.",
    },
    "HAL_AC_CHECKLIST_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_6_review.py",
        "description": "Kill-switch: HAL_AC_CHECKLIST_GATE=0 skips deterministic AC-checklist cross-check.",
    },
    "HAL_ORPHAN_CALLSITE_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "SYSTEM/cli/build/orphan-callsite-lint.py",
        "description": "Kill-switch: HAL_ORPHAN_CALLSITE_GATE=0 skips the GH564 orphan-callsite lint.",
    },
    "HAL_ORPHAN_CALLSITE_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "SYSTEM/cli/build/orphan-callsite-lint.py",
        "description": "GH564 warn-only rollout: =1 makes orphan scripts exit 1. flip-by:2026-08-07 issue #564.",
    },
    "HAL_PARENT_SKILL": {
        "kind": "str",
        "default": None,
        "module": "llm_subprocess.py",
        "description": "Parent skill name propagated into LLM subprocess metadata.",
    },
    "HAL_ALLOW_LEGACY_COMMUNICATE": {
        "kind": "flag",
        "default": "0",
        "module": "llm_subprocess.py",
        "description": "Feature flag: allow legacy subprocess.communicate() path when set to '1'.",
    },
    "HAL_AGENT_SDK_MAX_RESUMES": {
        "kind": "int",
        "default": "8",
        "module": "lib/reference_backends/agent_sdk.py",
        "description": "Warm-session resume cap for the agent-sdk reference backend; after N resumes a fresh session is started.",
    },
    "HAL_AGENT_SDK_STDERR_TAIL_LINES": {
        "kind": "int",
        "default": "50",
        "module": "lib/reference_backends/agent_sdk.py",
        "description": "Ring-buffer line cap for child CLI stderr tail captured by the agent-sdk backend.",
    },
    "HAL_OUTAGE_PROBE": {
        "kind": "gate",
        "default": "1",
        "module": "lib/reference_backends/agent_sdk.py",
        "description": "Kill-switch: HAL_OUTAGE_PROBE=0 disables the status-page external-outage probe on agent-sdk error paths.",
    },
    "HAL_STUB_PASSABILITY_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_STUB_PASSABILITY_GATE=0 disables the stub-passability RED lint.",
    },
    "HAL_SCHEMA_SMOKE_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_integrity.py",
        "description": "Kill-switch: HAL_SCHEMA_SMOKE_GATE=0 disables the GH892 schema-smoke step (dry-run against a real schema snapshot).",
    },
    "HAL_FIXTURE_SCHEMA_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_FIXTURE_SCHEMA_GATE=0 disables the GH891 fixture-schema (reference-DDL subset) RED lint.",
    },
    "HAL_RED_1Q_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_RED_1Q_GATE=0 disables the \u00a71q exec-import RED lint gate.",
    },
    "HAL_RED_COLLECT_PROBE_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_RED_COLLECT_PROBE_GATE=0 disables the §1q pytest --co collect-probe RED gate (GH542).",
    },
    "HAL_RED_COLLECT_PROBE_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_5_implement.py",
        "description": "=1 hard-blocks non-collectable RED (E_RED_COLLECT_PROBE, recoverable=True). Warn-only until flip-by:2026-07-24 Refs #542.",
    },
    "HAL_RED_COLLECT_PROBE_TIMEOUT_MS": {
        "kind": "int",
        "default": 60000,
        "module": "workflows/phase_5_implement.py",
        "description": "Timeout (ms) for the §1q pytest --co collect-probe subprocess (GH542).",
    },
    "HAL_GREEN_OUTPUT_TOKEN_BUDGET": {
        "kind": "int",
        "default": None,
        "module": "workflows/phase_5_implement.py",
        "description": "Absolute override for the green_token_budget_alert observability threshold (Step 11); unset/0 falls through to cfg green_output_token_budget, then COMPLEX default (40000), then base 5000 (GH727).",
    },
    "HAL_RED_LINT_PREFLIGHT_BATCH": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_RED_LINT_PREFLIGHT_BATCH=0 reverts to the legacy sequential first-fail RED-lint path (GH595).",
    },
    "HAL_SPEC_PREFLIGHT_BATCH": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_45_spec.py",
        "description": "Opt-in (default-OFF, flip-by:2026-08-13): HAL_SPEC_PREFLIGHT_BATCH=1 enables the GH747 phase_4.5 spec-gate pre-flight batch (spec_lint + spec_cite_lint drivers, one collect-then-report pass, single directed-repair). Off → legacy sequential steps.",
    },
    "HAL_SIBLING_AUDIT_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_SIBLING_AUDIT_GATE=0 disables the §1a sibling-test-audit warn pass (GH535, warn-only, flip-by:2026-07-24).",
    },
    "HAL_SIBLING_AUDIT_BIN": {
        "kind": "path",
        "default": None,
        "module": "workflows/phase_5_implement.py",
        "description": "Env seam: HAL_SIBLING_AUDIT_BIN overrides the sibling-test-audit.sh script path (GH535).",
    },
    "HAL_DIRTY_TREE_GUARD": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Default-ON gate: pre-RED-gate and pre-validation dirty-tree guard; uncommitted production changes → E_RED_WORKTREE_DIRTY hard-stop. =0 disables (legacy behavior).",
    },
    "HAL_RED_MASS_DELETION_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_RED_MASS_DELETION_GATE=0 disables the RED mass-deletion detection+telemetry entirely (GH282).",
    },
    "HAL_RED_MASS_DELETION_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_5_implement.py",
        "description": "=1 hard-blocks RED mass-deletion (E_RED_MASS_DELETION, recoverable=False). Warn-only until flip-by:2026-07-24 Refs #282.",
    },
    "HAL_RED_MASS_DELETION_MAX_LINES": {
        "kind": "int",
        "default": 120,
        "module": "workflows/phase_5_implement.py",
        "description": "Absolute deleted-lines floor for the RED mass-deletion gate (ANDed with >=50% base-file ratio, GH282).",
    },
    "HAL_REJECT_LOG": {
        "kind": "path",
        "default": None,
        "module": "reject_log.py",
        "description": "Env seam: HAL_REJECT_LOG overrides the reject-reasons.jsonl log path (GH497 D1, call-time resolved).",
    },
    "HAL_GREEN_COMPLETE_RESUME_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_GREEN_COMPLETE_RESUME_GATE=0 disables the GREEN-complete resume detection (GH483).",
    },
    "HAL_TIMEOUT_ACTIVE_THRESHOLD_BYTES": {
        "kind": "int",
        "default": 50000,
        "module": "llm_subprocess.py",
        "description": "Override for the ACTIVE-vs-STUCK timeout classification threshold in bytes (default 50000, GH285 C3); truthy int overrides, malformed falls back to default.",
    },
    "HAL_SECURITY_LINT_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_SECURITY_LINT_GATE=0 disables the security-lint gate.",
    },
    "HAL_AUTHORED_BOUNDARY_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_AUTHORED_BOUNDARY_GATE=0 disables the authored-boundary check.",
    },
    "HAL_SPEC_SCOPE_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_SCOPE_GATE=0 disables spec-scope enforcement.",
    },
    "HAL_SPEC_REENTRY_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_REENTRY_GATE=0 disables spec re-entry AC enforcement (GH823).",
    },
    "HAL_SPEC_REENTRY_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_45_spec.py",
        "description": (
            "Default-OFF enforce flip for the GH823 spec re-entry AC lint "
            "(warn-only until flip-by:2026-07-29, issue #823)."
        ),
    },
    "HAL_SPEC_HELPER_EXTRACTION_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_HELPER_EXTRACTION_GATE=0 disables spec helper-extraction (§1aa) enforcement (GH863).",
    },
    "HAL_SPEC_HELPER_EXTRACTION_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_45_spec.py",
        "description": (
            "Default-OFF enforce flip for the GH863 spec helper-extraction (§1aa) lint "
            "(warn-only until flip-by:2026-07-29, issue #863)."
        ),
    },
    "HAL_SPEC_COVERAGE_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_COVERAGE_GATE=0 disables spec-coverage enforcement.",
    },
    "HAL_SPEC_TAXONOMY_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_TAXONOMY_GATE=0 disables the GH824 §1n error-taxonomy OWN/DEFER warn-mode scan.",
    },
    "HAL_SPEC_FINALIZE_XCHECK_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_FINALIZE_XCHECK_GATE=0 disables the GH824 §2-vs-§3 finalize cross-check warn-mode scan.",
    },
    "HAL_SPEC_LINT_BATCH_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_SPEC_LINT_BATCH_GATE=0 disables the GH540 spec-lint batch step.",
    },
    "HAL_SPEC_LINT_BATCH_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_45_spec.py",
        "description": "Default-OFF enforce flip for the GH540 spec-lint batch (warn-only until flip-by:2026-07-24, #559).",
    },
    "HAL_AC_DSL_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_45_spec.py",
        "description": "Kill-switch: HAL_AC_DSL_GATE=0 disables the GH517 A2 AC-DSL admission step.",
    },
    "HAL_AC_DSL_GATE_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_45_spec.py",
        # flip-by:2026-07-25 Refs #517
        "description": "Default-OFF enforce flip for the GH517 A2 AC-DSL admission gate (warn-only until flip-by:2026-07-25, #517).",
    },
    "HAL_SPEC_CITE_PRELINT_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_45_spec.py",
        # flip-by:2026-07-26 Refs #681
        "description": "Default-OFF enforce flip for the GH675 spec-cite pre-lint (bounded 1× writer re-prompt; warn-only until flip-by:2026-07-26, #681).",
    },
    "HAL_INJECT_LEARNINGS_TS": {
        "kind": "path",
        "default": None,
        "module": "workflows/phase_05_inject.py",
        "description": "Override path for the inject-learnings.ts script.",
    },
    "HAL_BUN_BIN": {
        "kind": "binary",
        "default": "bun",
        "module": "workflows/phase_05_inject.py",
        "description": "Override binary name/path for the bun executable.",
    },
    "HAL_CHECKLIST_POLL_TIMEOUT_MS": {
        "kind": "int",
        "default": None,
        "module": "workflows/phase_05_inject.py",
        "description": "Timeout in ms for polling the learnings checklist.",
    },
    "HAL_CHECKLIST_POLL_INTERVAL_MS": {
        "kind": "int",
        "default": None,
        "module": "workflows/phase_05_inject.py",
        "description": "Poll interval in ms for the learnings checklist.",
    },
    "HAL_SECURITY_RULES_INJECT": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_05_inject.py",
        "description": "Kill-switch: HAL_SECURITY_RULES_INJECT=0 disables security-rules injection.",
    },
    "HAL_GH_BIN": {
        "kind": "binary",
        "default": "gh",
        "module": "workflows/phase_8_post_deploy.py",
        "description": "Override binary name/path for the gh CLI.",
    },
    "HAL_BUILD_SHIP_PR": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_8_post_deploy.py",
        "description": "Feature flag: enable automated ship-PR creation when set to '1'.",
    },
    "HAL_ENV_LIMIT_CLASSIFY": {
        "kind": "gate",
        "default": "1",
        "module": "llm_subprocess.py",
        "description": "Kill-switch: HAL_ENV_LIMIT_CLASSIFY=0 disables spend/usage-limit classification (legacy E_LLM_EXIT).",
    },
    "HAL_PERSIST_LEARNINGS_BUN_BIN": {
        "kind": "binary",
        "default": "bun",
        "module": "workflows/phase_8_post_deploy.py",
        "description": "Override binary name/path for the bun executable used to persist learnings.",
    },
    "HAL_VERDICT_GATE_LINT": {
        "kind": "gate",
        "default": "1",
        "module": "verdict_gate.py",
        "description": "Kill-switch: HAL_VERDICT_GATE_LINT=0 disables the full verdict-gate lint at both seams (hook + phase_5 acceptance).",
    },
    "HAL_VERDICT_GATE_LINT_ENFORCE": {
        "kind": "gate",
        "default": "0",
        "module": "engine-py-audit-gate.py + workflows/phase_5_implement.py",
        "description": "=1 flips verdict-gate lint warn→block at both seams. 34E0B77B flip-by:2026-08-01.",
    },
    "HAL_FLAG_UNREGISTERED_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/phase_5_implement.py",
        "description": "Kill-switch: HAL_FLAG_UNREGISTERED_GATE=0 disables the GH529 GREEN-time flag-registration lint.",
    },
    "HAL_FLAG_UNREGISTERED_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_5_implement.py",
        "description": "GH529 warn-only rollout: =1 makes unregistered HAL_* tokens a recoverable E_FLAG_UNREGISTERED gate failure. flip-by:2026-07-24 issue #529.",
    },
    "HAL_BASELINE_DELTA_GATE": {
        "kind": "gate",
        "default": "1",
        "module": "workflows/_baseline_delta.py",
        "description": "Kill-switch: HAL_BASELINE_DELTA_GATE=0 disables the GH561 §1r lane-2 baseline-delta gate wiring.",
    },
    "HAL_BASELINE_DELTA_ENFORCE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/_baseline_delta.py",
        "description": "GH561 warn-only rollout: =1 makes a FAIL verdict from baseline_delta_gate.py a recoverable E_BASELINE_DELTA gate failure in phase_5. flip-by:2026-08-07 Refs #561.",
    },
    "HAL_BASELINE_DELTA_BIN": {
        "kind": "path",
        "default": None,
        "module": "workflows/_baseline_delta.py",
        "description": "Env seam: HAL_BASELINE_DELTA_BIN overrides the baseline_delta_gate.py script path (GH561).",
    },
    "HAL_PAUSE_LANE": {
        "kind": "gate",
        "default": "1",
        "module": "engine.py",
        "description": "Kill-switch: HAL_PAUSE_LANE=0 disables the E_LLM_SPEND_LIMIT PAUSED lane (GH576) — engine emits raw error status; run-phase/last-phase-status/batch-build fall back to generic FAIL/respawn.",
    },
    "HAL_ENGINE_SHADOW_EMITS": {
        "kind": "gate",
        "default": "1",
        "module": "engine.py",
        "description": "Kill-switch: HAL_ENGINE_SHADOW_EMITS=0 disables the GH700 shadow-emit guard — a DBOS-recovery re-execution running off the process main thread again emits real workflow_started/workflow_finished into the shared event log, so a zombie's terminal error can be mistaken for the live run's (issue #700).",
    },
    "HAL_SPEC_DEFECT_REROUTE": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_5_implement.py",
        "description": "Opt-in (default-OFF, flip-by:2026-08-14 Refs #767): HAL_SPEC_DEFECT_REROUTE=1 enables the bounded auto-reroute of a SPEC_DEFECT verdict from phase_5_implement back to phase_45_spec. Off → legacy TEST_GAP retry path, byte-identical.",
    },
    "HAL_RED_BASELINE_REFRESH": {
        "kind": "flag",
        "default": "0",
        "module": "workflows/phase_5_implement.py",
        "description": "Operator opt-in (=1): on resume, re-freeze the RED frozen-hash baseline when every mismatch is head_moved (worktree==HEAD, committed operator RED change). Never blesses worktree_dirty.",
    },
}
