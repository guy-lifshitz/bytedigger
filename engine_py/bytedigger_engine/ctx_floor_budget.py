"""ctx_floor_budget.py — GH1244 (agreement 1C3E114B-D760-4385-92DD-6C6EBD303E4A):
byte-budget measurement for the always-injected context layer
(`.claude/rules/`, `CLAUDE.md`, profile `MEMORY.md`).

Spec: SHARED/memory/Decisions/2026-07-26_1C3E114B_ctx_floor_rules_split_spec.md (§2.4)

Public API (§1f — the entry point SYSTEM/cli/build/ctx-floor-budget-lint.py
imports and calls these, no logic of its own):
    load_config(path)       -> parsed + validated config dict, raises ConfigError
    evaluate_budgets(...)   -> {"surfaces": [...], "enforce": bool, "verdict": "ok"|"over"}
    render_text(result)     -> human-readable report
    render_json(result)     -> json.dumps(result)
"""
from __future__ import annotations

import glob as _glob
import json
import os
from pathlib import Path
from typing import Any, Mapping, Union

REQUIRED_BUDGET_KEYS = ("rules_core_bytes_max", "claude_md_bytes_max", "memory_md_bytes_max")


class ConfigError(Exception):
    """Config missing, unparseable, or missing a required budget key (§2.4, exit 3)."""


def load_config(path: Union[str, Path]) -> dict[str, Any]:
    """Read + validate the ctx-floor-budget config. Raises ConfigError on any
    problem (missing file, invalid JSON, non-object, missing budgets object,
    or a missing budget key) — the caller maps ConfigError to exit code 3."""
    p = Path(path)
    try:
        raw = p.read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read config {p}: {exc}") from exc

    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config {p} is not valid JSON: {exc}") from exc

    if not isinstance(cfg, dict):
        raise ConfigError(f"config {p} must be a JSON object")

    budgets = cfg.get("budgets")
    if not isinstance(budgets, dict):
        raise ConfigError(f"config {p} missing 'budgets' object")

    missing = [k for k in REQUIRED_BUDGET_KEYS if k not in budgets]
    if missing:
        raise ConfigError(f"config {p} missing budget key(s): {missing}")

    return {
        "agreement": cfg.get("agreement"),
        "flip_by": cfg.get("flip_by"),
        # agreement 1C3E114B-D760-4385-92DD-6C6EBD303E4A flip-by:2026-08-09 —
        # ships advisory; a config that omits "enforce" defaults to False.
        "enforce": bool(cfg.get("enforce", False)),
        "budgets": budgets,
    }


def _resolve_enforce(config_enforce: bool, env: Mapping[str, str]) -> bool:
    """effective = env if env in {"0","1"} else config.enforce (spec §2.4)."""
    raw = env.get("HAL_CTX_FLOOR_BUDGET_ENFORCE")
    if raw in ("0", "1"):
        return raw == "1"
    return bool(config_enforce)


def _rules_core_surface(root: Path, budget: int) -> dict[str, Any]:
    rules_dir = root / ".claude" / "rules"
    present = rules_dir.is_dir()
    total = 0
    if present:
        for f in sorted(rules_dir.glob("*.md")):
            total += f.stat().st_size
    return {
        "name": "rules_core",
        "path": str(rules_dir),
        "bytes": total,
        "budget": budget,
        "over": total > budget,
        "present": present,
    }


def _claude_md_surfaces(root: Path, budget: int) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for path in (root / "CLAUDE.md", root / ".claude" / "CLAUDE.md"):
        present = path.is_file()
        size = path.stat().st_size if present else 0
        surfaces.append({
            "name": "claude_md",
            "path": str(path),
            "bytes": size,
            "budget": budget,
            "over": present and size > budget,
            "present": present,
        })
    return surfaces


def _resolve_single_profile_dir(profile_glob: str, env: Mapping[str, str]) -> tuple[Path | None, str | None]:
    """Deterministic single-profile resolution for `floor_total` (one
    session's floor, never a machine-wide fan-out over every profile the
    glob happens to match). Returns (profile_dir, None) on success, or
    (None, reason) when no profile — or more than one — can be picked
    without guessing."""
    cfg_dir = env.get("CLAUDE_CONFIG_DIR") if env else None
    if cfg_dir:
        p = Path(os.path.expanduser(cfg_dir))
        return (p, None) if p.is_dir() else (None, "missing")

    expanded = os.path.expanduser(profile_glob)
    matches = sorted(Path(p) for p in _glob.glob(expanded) if Path(p).is_dir())
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "missing"
    return None, "ambiguous"


def _single_profile_memory_md(profile_dir: Path) -> list[dict[str, Any]]:
    """MEMORY.md files under exactly ONE resolved profile dir (never
    fan-out across every profile a glob matches)."""
    surfaces: list[dict[str, Any]] = []
    projects_dir = profile_dir / "projects"
    if not projects_dir.is_dir():
        return surfaces
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        mem_path = project_dir / "memory" / "MEMORY.md"
        if mem_path.is_file():
            surfaces.append({"path": str(mem_path), "bytes": mem_path.stat().st_size})
    return surfaces


def _floor_total(
    surfaces: list[dict[str, Any]],
    repo_only: bool,
    profile_glob: str,
    budgets: dict[str, int],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """GH1438 §1B: the summed bytes of the actually-injected set for ONE
    session — never a machine-wide aggregate over every profile a glob
    happens to match. Additive to the existing per-surface budgets —
    expresses the issue's "floor <= 8KB" target, which today is not
    expressible because no surface aggregates.

    Sums rules_core + repo CLAUDE.md (always, from `surfaces` — those two
    never fan out) plus, in non-repo-only mode, exactly one profile's
    CLAUDE.md and MEMORY.md, resolved deterministically (§ below). Anything
    that cannot be resolved to exactly one profile is left OUT of the sum
    and NAMED in `excluded_surfaces`, so a partial total is never read as
    the whole floor (same no-silent-caps rule `--repo-only` already
    follows).
    """
    aggregated_bytes = sum(s["bytes"] for s in surfaces if s["name"] in ("rules_core", "claude_md"))
    excluded_surfaces: list[str] = []
    if repo_only:
        excluded_surfaces.append("memory_md")
        excluded_surfaces.append("profile_claude_md")
    else:
        profile_dir, reason = _resolve_single_profile_dir(profile_glob, env)
        if profile_dir is None:
            excluded_surfaces.append(f"profile_claude_md:{reason}")
            excluded_surfaces.append(f"memory_md:{reason}")
        else:
            claude_md = profile_dir / "CLAUDE.md"
            if claude_md.is_file():
                aggregated_bytes += claude_md.stat().st_size
            else:
                excluded_surfaces.append("profile_claude_md:absent")

            mem_surfaces = _single_profile_memory_md(profile_dir)
            if len(mem_surfaces) == 1:
                aggregated_bytes += mem_surfaces[0]["bytes"]
            elif not mem_surfaces:
                excluded_surfaces.append("memory_md:absent")
            else:
                excluded_surfaces.append("memory_md:ambiguous")

    # R7-MAJOR-3 follow-up: floor_total_bytes_max is deliberately NOT in
    # REQUIRED_BUDGET_KEYS (a config may omit it), so an omitted key must
    # not silently become budget=0 and flip every nonzero floor to "over"
    # once floor_total participates in verdict/any_over. It participates
    # ONLY when a budget was actually configured — explicitly in the
    # config, or via the env override — same no-silent-caps rule the rest
    # of this function already follows for excluded surfaces.
    budget_configured = "floor_total_bytes_max" in budgets
    budget = budgets.get("floor_total_bytes_max", 0)
    raw_env = env.get("HAL_CTX_FLOOR_TOTAL_MAX") if env else None
    if raw_env is not None:
        try:
            budget = int(raw_env)
        except ValueError as exc:
            # A malformed override must never be swallowed and silently
            # fall back to the config budget (§2.4 exit-3 contract) — the
            # operator believes they set a threshold; a silent fallback
            # would use a different one and say nothing.
            raise ConfigError(
                f"HAL_CTX_FLOOR_TOTAL_MAX={raw_env!r} is not a valid integer"
            ) from exc
        budget_configured = True

    return {
        "name": "floor_total",
        "bytes": aggregated_bytes,
        "budget": budget,
        "budget_configured": budget_configured,
        "over": budget_configured and aggregated_bytes > budget,
        "excluded_surfaces": excluded_surfaces,
    }


def _memory_md_surfaces(profile_glob: str, budget: int) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    expanded = os.path.expanduser(profile_glob)
    profile_dirs = sorted(Path(p) for p in _glob.glob(expanded) if Path(p).is_dir())
    for profile_dir in profile_dirs:
        projects_dir = profile_dir / "projects"
        if not projects_dir.is_dir():
            continue
        project_dirs = sorted(p for p in projects_dir.iterdir() if p.is_dir())
        for project_dir in project_dirs:
            mem_path = project_dir / "memory" / "MEMORY.md"
            present = mem_path.is_file()
            size = mem_path.stat().st_size if present else 0
            surfaces.append({
                "name": "memory_md",
                "path": str(mem_path),
                "bytes": size,
                "budget": budget,
                "over": present and size > budget,
                "present": present,
            })
    return surfaces


def evaluate_budgets(
    root: Path,
    budgets: dict[str, int],
    config_enforce: bool = False,  # agreement 1C3E114B-D760-4385-92DD-6C6EBD303E4A flip-by:2026-08-09 — ships advisory
    repo_only: bool = False,
    profile_glob: str = "~/.claude*",
    env: Union[Mapping[str, str], None] = None,
) -> dict[str, Any]:
    """Measure every surface (§2.4) and produce the top-level payload.

    `--repo-only` REMOVES the memory_md surfaces entirely (spec §2.4) rather
    than reporting them as within budget.
    """
    resolved_env: Mapping[str, str] = env if env is not None else os.environ
    effective_enforce = _resolve_enforce(config_enforce, resolved_env)

    surfaces: list[dict[str, Any]] = [_rules_core_surface(root, budgets["rules_core_bytes_max"])]
    surfaces.extend(_claude_md_surfaces(root, budgets["claude_md_bytes_max"]))
    if not repo_only:
        surfaces.extend(_memory_md_surfaces(profile_glob, budgets["memory_md_bytes_max"]))

    floor_total = _floor_total(surfaces, repo_only, profile_glob, budgets, resolved_env)
    # R7-MAJOR-3: floor_total is a SIBLING key, not one of `surfaces` — a
    # verdict/exit-code computation that only walks `surfaces` silently
    # drops it, so `enforce: true` stops being enforcement for exactly the
    # aggregate GH1438 added. It must count the same as any other surface.
    verdict = "over" if any(s["over"] for s in surfaces) or floor_total["over"] else "ok"
    return {
        "surfaces": surfaces,
        "enforce": effective_enforce,
        "verdict": verdict,
        "floor_total": floor_total,
    }


def render_text(result: dict[str, Any]) -> str:
    lines = [f"ctx-floor-budget: verdict={result['verdict']} enforce={result['enforce']}"]
    for s in result["surfaces"]:
        lines.append(
            f"  {s['name']:<10} {s['path']} bytes={s['bytes']} budget={s['budget']} "
            f"over={s['over']} present={s['present']}"
        )
    ft = result.get("floor_total")
    if ft is not None:
        lines.append(
            f"  {'floor_total':<10} bytes={ft['bytes']} budget={ft['budget']} over={ft['over']}"
        )
    return "\n".join(lines)


def render_json(result: dict[str, Any]) -> str:
    return json.dumps(result)
