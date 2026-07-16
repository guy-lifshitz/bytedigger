"""Phase 5 DevOps scan — security scanning via bun shim.

Slots after phase_5_implement and before phase_5_integration_canary.  Reads
``artifact_type`` and ``changed_files`` from ``ctx.org_config``, delegates
scanning to the ``devops-scan.ts`` Bun shim (Architecture D / agreement
8BD901CB), and stores the result in ``StepResult.data`` for downstream phases.

Severity gate (GH #342 slice B, 2026-07-05): the "never fails the build"
behavior above was a fail-open hole (#221 lesson) — scan findings and scanner
malfunctions were both silently swallowed as ``status="ok"``. With
``HAL_DEVOPS_SCAN_GATE`` unset or not ``"0"`` (default ON):

- Skip paths (``no_files`` / ``no_artifact_type``) are unchanged — nothing to
  scan is legitimate and stays ``status="ok"``.
- Infra failures (bun missing / timeout / non-zero rc / bad JSON) now fail
  CLOSED: ``status="error"``, ``error_code="E_DEVOPS_SCAN_UNAVAILABLE"``.
- Happy-path findings that are gating (severity in the configured
  ``fail_on_severities``) and not allowlist-waived now fail CLOSED:
  ``status="error"``, ``error_code="E_DEVOPS_SCAN_BLOCKED"``.
- All-waived or non-gating (e.g. MEDIUM/LOW under default config) findings
  stay ``status="ok"``.

Set ``HAL_DEVOPS_SCAN_GATE=0`` to restore byte-for-byte legacy fail-open
behavior on every path (kill-switch, not a rollout flag — see spec).

Concrete ``skipped_reason`` labels (for reference by future tests / consumers):
- ``"no_artifact_type"``  — artifact_type key absent, None, or empty string
- ``"no_files"``          — changed_files key absent or empty list
- ``"bun_not_found"``     — FileNotFoundError when invoking bun
- ``"timeout"``           — subprocess.TimeoutExpired (60 s limit)
- ``"subprocess_error"``  — bun shim exited with non-zero returncode
- ``"json_decode_error"`` — bun stdout was not valid JSON

Agreement: 27843297 Stage 2 (2026-05-12). Severity gate: GH #342 slice B.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from bounded_spawn import bounded_run  # noqa: E402
from contracts import StepContract, StepResult, WorkflowDefinition

sys.path.insert(0, str(Path(__file__).parent.parent))
from config_provider import get_config, env_opt  # noqa: E402
import telemetry_ctx  # noqa: E402

logger = logging.getLogger(__name__)


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors.
    Mirror of the helper in phase_45_spec / phase_45_spec_lite / phase_5_implement / skip_logic."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _resolve_hal_dir(ctx) -> Path:
    """Return the HAL root.

    Delegates to get_config().hal_root() (§1g single-source).
    """
    return get_config().hal_root()


def _gate_enabled() -> bool:
    """Return True unless HAL_DEVOPS_SCAN_GATE=0 (default ON, kill-switch)."""
    return get_config().gate_enabled("HAL_DEVOPS_SCAN_GATE")


def _load_fail_severities(hal_dir: Path) -> list[str]:
    """Return the fail_on_severities list from config (§1g single-source).

    Reads JSON at HAL_DEVOPS_SCAN_CONFIG env override, else
    hal_dir/SYSTEM/cli/build/security/devops-scan-config.json. On missing
    file, bad JSON, or missing key, returns the default ["CRITICAL", "HIGH"]
    (mirror of scan-config.ts default).
    """
    cfg_path_str = env_opt("HAL_DEVOPS_SCAN_CONFIG")
    cfg_path = (
        Path(cfg_path_str)
        if cfg_path_str
        else hal_dir / "SYSTEM" / "cli" / "build" / "security" / "devops-scan-config.json"
    )
    try:
        parsed = json.loads(cfg_path.read_text())
        sevs = parsed.get("fail_on_severities")
        if isinstance(sevs, list):
            return sevs
    except (OSError, json.JSONDecodeError):
        pass
    return ["CRITICAL", "HIGH"]


def _load_allowlist(path: Path) -> list[dict]:
    """Parse allowlist lines '<pattern> :: <AGREEMENT-8HEX> :: kill-by:YYYY-MM-DD'.

    Blank / '#'-comment lines skipped. Malformed lines (no '::') skipped
    silently, never raises. Missing file returns [].
    """
    try:
        text = path.read_text()
    except OSError:
        return []

    entries: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [p.strip() for p in stripped.split("::")]
        if len(parts) != 3:
            continue
        pattern, agreement, kill_by_raw = parts
        if not kill_by_raw.startswith("kill-by:"):
            continue
        try:
            kill_by = datetime.date.fromisoformat(kill_by_raw[len("kill-by:"):])
        except ValueError:
            continue
        entries.append({"pattern": pattern, "agreement": agreement, "kill_by": kill_by})
    return entries


def _issue_key(issue: dict) -> str:
    """Return a stable substring-matchable key for an issue."""
    return f"{issue.get('scanner', '')}|{issue.get('file', '')}|{issue.get('description', '')}"


def _split_blocking(
    issues: list[dict],
    fail_sevs: list[str],
    allowlist: list[dict],
    today: datetime.date,
) -> tuple[list[dict], list[dict]]:
    """Split issues into (blocking, waived) per the ratified policy.

    An issue is gating iff its severity is in fail_sevs. A gating issue is
    waived iff some unexpired allowlist entry's pattern is a substring of the
    issue's key; otherwise it is blocking. Non-gating issues (e.g. MEDIUM/LOW
    under default config) are neither blocking nor waived (warn-only).
    """
    blocking: list[dict] = []
    waived: list[dict] = []
    for issue in issues:
        if issue.get("severity") not in fail_sevs:
            continue
        key = _issue_key(issue)
        match = next(
            (
                entry
                for entry in allowlist
                if entry["kill_by"] >= today and entry["pattern"] in key
            ),
            None,
        )
        if match is not None:
            waived.append({"key": key, "agreement": match["agreement"]})
        else:
            blocking.append(issue)
    return blocking, waived


# ─── step ────────────────────────────────────────────────────────────────────


def _scan_artifact(ctx, _prev) -> StepResult:
    """Invoke devops-scan.ts for the changed_files list; return scan result.

    Skip paths always return status="ok". With the severity gate ON (default,
    see module docstring), infra failures and blocking findings return
    status="error"; gate OFF restores legacy status="ok"-always behavior.
    """
    cfg = ctx.org_config or {}

    # AC4/AC5: skip when changed_files absent or empty
    files: list[str] = cfg.get("changed_files", [])
    if not files:
        return StepResult(
            status="ok",
            data={
                "skipped_reason": "no_files",
                "passed": True,
                "issues": [],
                "scanners_run": [],
                "scanners_missing": [],
                "summary": "",
                "stats": {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0},
            },
            duration_ms=0,
            step_name="scan_artifact",
        )

    # AC2/AC3: skip when artifact_type absent, None, or empty string
    artifact_type = cfg.get("artifact_type")
    if not artifact_type:
        return StepResult(
            status="ok",
            data={
                "skipped_reason": "no_artifact_type",
                "passed": True,
                "issues": [],
                "scanners_run": [],
                "scanners_missing": [],
                "summary": "",
                "stats": {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0},
            },
            duration_ms=0,
            step_name="scan_artifact",
        )

    hal_dir = _resolve_hal_dir(ctx)
    shim_path = hal_dir / "SYSTEM" / "cli" / "build" / "devops-scan.ts"

    cmd = [
        "bun",
        str(shim_path),
        "--files",
        ",".join(files),
        "--artifact-type",
        artifact_type,
        "--output",
        "json",
    ]

    try:
        proc = bounded_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        data = {
            "skipped_reason": "bun_not_found",
            "error": f"FileNotFoundError: {exc}",
            "passed": True,
            "issues": [],
        }
        if _gate_enabled():
            data["passed"] = False
            return StepResult(
                status="error",
                data=data,
                duration_ms=0,
                step_name="scan_artifact",
                error_code="E_DEVOPS_SCAN_UNAVAILABLE",
            )
        _emit_safe("gate_disabled", {
            "gate": "HAL_DEVOPS_SCAN_GATE",
            "step": "scan_artifact",
            "reason": "fail_open",
            "detail": "bun_not_found",
        })
        return StepResult(status="ok", data=data, duration_ms=0, step_name="scan_artifact")

    if proc.returncode == 124:
        data = {
            "skipped_reason": "timeout",
            "error": "timeout after 60s",
            "passed": True,
            "issues": [],
        }
        if _gate_enabled():
            data["passed"] = False
            return StepResult(
                status="error",
                data=data,
                duration_ms=0,
                step_name="scan_artifact",
                error_code="E_DEVOPS_SCAN_UNAVAILABLE",
            )
        _emit_safe("gate_disabled", {
            "gate": "HAL_DEVOPS_SCAN_GATE",
            "step": "scan_artifact",
            "reason": "fail_open",
            "detail": "timeout",
        })
        return StepResult(status="ok", data=data, duration_ms=0, step_name="scan_artifact")

    if proc.returncode != 0:
        data = {
            "skipped_reason": "subprocess_error",
            "error": (proc.stderr or "").strip() or f"rc={proc.returncode}",
            "passed": True,
            "issues": [],
        }
        if _gate_enabled():
            data["passed"] = False
            return StepResult(
                status="error",
                data=data,
                duration_ms=0,
                step_name="scan_artifact",
                error_code="E_DEVOPS_SCAN_UNAVAILABLE",
            )
        _emit_safe("gate_disabled", {
            "gate": "HAL_DEVOPS_SCAN_GATE",
            "step": "scan_artifact",
            "reason": "fail_open",
            "detail": "subprocess_error",
        })
        return StepResult(status="ok", data=data, duration_ms=0, step_name="scan_artifact")

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        data = {
            "skipped_reason": "json_decode_error",
            "error": str(exc),
            "passed": True,
            "issues": [],
        }
        if _gate_enabled():
            data["passed"] = False
            return StepResult(
                status="error",
                data=data,
                duration_ms=0,
                step_name="scan_artifact",
                error_code="E_DEVOPS_SCAN_UNAVAILABLE",
            )
        return StepResult(status="ok", data=data, duration_ms=0, step_name="scan_artifact")

    # Happy path: pass through all 7 fields from shim JSON
    issues: list[dict] = parsed.get("issues", [])
    data = {
        "passed": parsed.get("passed"),
        "issues": issues,
        "scanners_run": parsed.get("scanners_run", []),
        "scanners_missing": parsed.get("scanners_missing", []),
        "summary": parsed.get("summary", ""),
        "stats": parsed.get("stats", {}),
        "skipped_reason": parsed.get("skipped_reason"),
    }

    if not _gate_enabled():
        return StepResult(status="ok", data=data, duration_ms=0, step_name="scan_artifact")

    hal_dir = _resolve_hal_dir(ctx)
    fail_sevs = _load_fail_severities(hal_dir)
    allowlist_path_str = env_opt("HAL_DEVOPS_SCAN_ALLOWLIST")
    allowlist_path = (
        Path(allowlist_path_str)
        if allowlist_path_str
        else hal_dir / "SYSTEM" / "cli" / "build" / "security" / "devops-scan-allowlist.txt"
    )
    allowlist = _load_allowlist(allowlist_path)
    blocking, waived = _split_blocking(
        issues, fail_sevs, allowlist, datetime.date.today()
    )
    data["waived"] = waived

    if blocking:
        data["blocking_count"] = len(blocking)
        return StepResult(
            status="error",
            data=data,
            duration_ms=0,
            step_name="scan_artifact",
            error_code="E_DEVOPS_SCAN_BLOCKED",
        )

    return StepResult(status="ok", data=data, duration_ms=0, step_name="scan_artifact")


# ─── public factory ───────────────────────────────────────────────────────────


def phase_5_devops_scan_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_5_devops_scan",
        steps=[
            StepContract(name="scan_artifact", execute=_scan_artifact),
        ],
    )
