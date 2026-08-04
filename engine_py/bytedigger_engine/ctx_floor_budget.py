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

    verdict = "over" if any(s["over"] for s in surfaces) else "ok"
    return {"surfaces": surfaces, "enforce": effective_enforce, "verdict": verdict}


def render_text(result: dict[str, Any]) -> str:
    lines = [f"ctx-floor-budget: verdict={result['verdict']} enforce={result['enforce']}"]
    for s in result["surfaces"]:
        lines.append(
            f"  {s['name']:<10} {s['path']} bytes={s['bytes']} budget={s['budget']} "
            f"over={s['over']} present={s['present']}"
        )
    return "\n".join(lines)


def render_json(result: dict[str, Any]) -> str:
    return json.dumps(result)
