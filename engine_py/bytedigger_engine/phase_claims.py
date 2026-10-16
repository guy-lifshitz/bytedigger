"""bd#319: phases/*.md claims are rendered from code, not typed by hand.

One generator owns the claim registry (``BLOCKS``) and renders the generated
blocks of the phase docs. Sources (one canonical source per fact):

- thresholds and validation model: ``bytedigger.json``
- tier policy (reviewers / evaluators / dimensions): the module-level literal
  ``SATISFACTION_TIER_POLICY`` in ``workflows/phase_6_review.py``, read with
  ``ast.literal_eval`` (the module is never imported).

Stdlib only. CLI::

    python3 -m bytedigger_engine.phase_claims --write | --check [--repo-root P]

Exit codes: 0 ok, 1 ``--check`` found stale/invalid blocks, 2 error (a
``PhaseClaimsError`` or malformed markers; nothing is written).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

TIERS = ("SIMPLE", "FEATURE", "COMPLEX")
P6_REL = "engine_py/bytedigger_engine/workflows/phase_6_review.py"
DOC6 = "phases/phase-6-review.md"
DOC0 = "phases/phase-0-classify.md"

_MARKER = re.compile(r"^<!-- (BEGIN|END) GENERATED ([\w-]+) -->$")
_REVIEWER_SCOPE = (
    "composite reviewer (correctness, silent failures, test adequacy, types, "
    "simplification, comments; security pass on HIGH)"
)


class PhaseClaimsError(Exception):
    """A claim source is missing, malformed or non-literal (never a silent default)."""


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _load_thresholds(root: Path) -> tuple[dict, str]:
    path = root / "bytedigger.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PhaseClaimsError(f"bytedigger.json not found at {path}") from None
    except (OSError, ValueError) as exc:
        raise PhaseClaimsError(f"bytedigger.json unreadable ({path}): {exc}") from None
    if not isinstance(data, dict):
        raise PhaseClaimsError(f"bytedigger.json ({path}): top level is not an object")
    model = data.get("validation_model")
    if not isinstance(model, str) or not model:
        raise PhaseClaimsError(f"bytedigger.json ({path}): validation_model missing or not a string")
    thr = data.get("satisfaction_thresholds")
    if not isinstance(thr, dict):
        raise PhaseClaimsError(f"bytedigger.json ({path}): satisfaction_thresholds missing or not an object")
    out: dict = {}
    for tier in TIERS:
        if tier not in thr:
            raise PhaseClaimsError(f"bytedigger.json ({path}): satisfaction_thresholds lacks tier {tier}")
        if not _is_int(thr[tier]):
            raise PhaseClaimsError(
                f"bytedigger.json ({path}): satisfaction_thresholds.{tier} is not an int: {thr[tier]!r}"
            )
        out[tier] = thr[tier]
    return out, model


def _load_policy(root: Path) -> dict:
    path = root / P6_REL
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise PhaseClaimsError(f"cannot read {path} for SATISFACTION_TIER_POLICY: {exc}") from None
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        raise PhaseClaimsError(f"cannot parse {path} for SATISFACTION_TIER_POLICY: {exc}") from None
    value_node = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SATISFACTION_TIER_POLICY" for t in stmt.targets
        ):
            value_node = stmt.value
            break
    if value_node is None:
        raise PhaseClaimsError(f"SATISFACTION_TIER_POLICY assignment not found in {path}")
    try:
        policy = ast.literal_eval(value_node)
    except Exception:
        raise PhaseClaimsError(f"SATISFACTION_TIER_POLICY in {path} is not a pure literal") from None
    if not isinstance(policy, dict):
        raise PhaseClaimsError(f"SATISFACTION_TIER_POLICY in {path} is not a dict")
    out: dict = {}
    for tier in TIERS:
        entry = policy.get(tier)
        if not isinstance(entry, dict):
            raise PhaseClaimsError(f"SATISFACTION_TIER_POLICY in {path} lacks tier {tier}")
        for key in ("reviewers", "evaluators"):
            if not _is_int(entry.get(key)):
                raise PhaseClaimsError(
                    f"SATISFACTION_TIER_POLICY[{tier!r}] in {path}: {key} missing or not an int"
                )
        dims = entry.get("dimensions")
        if (
            not isinstance(dims, (tuple, list))
            or not dims
            or not all(isinstance(d, str) for d in dims)
        ):
            raise PhaseClaimsError(
                f"SATISFACTION_TIER_POLICY[{tier!r}] in {path}: dimensions missing or not a tuple of str"
            )
        out[tier] = {
            "reviewers": entry["reviewers"],
            "evaluators": entry["evaluators"],
            "dimensions": tuple(dims),
        }
    return out


def load_claims(repo_root) -> dict:
    root = Path(repo_root)
    thresholds, model = _load_thresholds(root)
    return {
        "satisfaction_thresholds": thresholds,
        "validation_model": model,
        "tier_policy": _load_policy(root),
    }


# --- renderers (each returns the block body lines, no line endings) -------------


def _model(claims: dict) -> str:
    return claims["validation_model"].capitalize()


def _render_tier_policy(claims: dict) -> list[str]:
    model = _model(claims)
    lines = [
        "| Complexity | Reviewer | Satisfaction |",
        "|-----------|---------------|-------------|",
    ]
    for tier in TIERS:
        pol = claims["tier_policy"][tier]
        thr = claims["satisfaction_thresholds"][tier]
        n = pol["evaluators"]
        who = f"{n}x {model}" if n == 1 else f"{n}x {model} voting"
        sat = f"{who}, {len(pol['dimensions'])} dim, >={thr}%"
        rev = f"{pol['reviewers']}: {_REVIEWER_SCOPE}"
        lines.append(f"| {tier} | {rev} | {sat} |")
        lines.append(f"| {tier} + HIGH security | {rev} | {sat} |")
    return lines


def _render_steps(claims: dict) -> list[str]:
    model = _model(claims)
    lines: list[str] = []
    for tier in TIERS:
        pol = claims["tier_policy"][tier]
        n = pol["evaluators"]
        dims = pol["dimensions"]
        kind = f"Single {model} Evaluator" if n == 1 else f"{n}-Agent Majority Vote"
        lines.append(f"### {tier} — {kind} ({len(dims)} dimensions)")
        lines.append("")
        lines.append(f"Dimensions: {', '.join(dims)}")
        lines.append(f"Threshold: >={claims['satisfaction_thresholds'][tier]}%")
        lines.append("")
        if tier == "COMPLEX":
            lines.append(f"1. Launch {n} {model} agents in parallel.")
    return lines


def _render_phase0_row(claims: dict) -> list[str]:
    model = _model(claims)
    pols = [claims["tier_policy"][t] for t in TIERS]
    rev = " | ".join(f"{p['reviewers']}x composite reviewer" for p in pols)
    sat_cells = []
    for p in pols:
        d = len(p["dimensions"])
        n = p["evaluators"]
        sat_cells.append(f"{model} ({d} dim)" if n == 1 else f"{n}x {model} voting ({d} dim)")
    return [f"| 6 Review | {rev} |", f"| 6 Satisfaction | {' | '.join(sat_cells)} |"]


BLOCKS: dict = {
    "phase6-tier-policy": (DOC6, _render_tier_policy),
    "phase6-satisfaction-steps": (DOC6, _render_steps),
    "phase0-satisfaction-row": (DOC0, _render_phase0_row),
}


# --- marker scanning / rendering -------------------------------------------------


def _scan(lines: list[str], allowed: set[str], require_all: bool):
    """Return (blocks, problems). blocks: [(id, begin_idx, end_idx)] of closed blocks."""
    blocks: list[tuple[str, int, int]] = []
    problems: list[str] = []
    seen: set[str] = set()
    open_id: str | None = None
    open_idx = -1

    def bad(bid: str) -> None:
        if bid not in problems:
            problems.append(bid)

    for i, raw in enumerate(lines):
        m = _MARKER.match(raw.rstrip("\r"))
        if not m:
            continue
        kind, bid = m.group(1), m.group(2)
        if bid not in allowed:
            bad(bid)
            continue
        if kind == "BEGIN":
            if open_id is not None:
                bad(bid)  # nested BEGIN
                continue
            if bid in seen:
                bad(bid)  # duplicated block id
            seen.add(bid)
            open_id, open_idx = bid, i
        else:
            if open_id is None or open_id != bid:
                bad(bid)  # END without matching BEGIN
                continue
            blocks.append((bid, open_idx, i))
            open_id = None
    if open_id is not None:
        bad(open_id)  # unbalanced: BEGIN never closed
    if require_all:
        for bid in sorted(allowed):
            if bid not in seen:
                bad(bid)
    return blocks, problems


def _process(text: str, claims: dict, allowed: set[str], require_all: bool):
    """Return (new_text, stale_ids, problems)."""
    lines = text.split("\n")
    blocks, problems = _scan(lines, allowed, require_all)
    if problems:
        return text, [], problems
    stale: list[str] = []
    for bid, b, e in sorted(blocks, key=lambda x: -x[1]):
        body = BLOCKS[bid][1](claims)
        cr = "\r" if lines[b].endswith("\r") else ""
        inner = [ln.rstrip("\r") for ln in lines[b + 1:e]]
        if inner != body:
            stale.append(bid)
            lines[b + 1:e] = [ln + cr for ln in body]
    stale.reverse()
    return "\n".join(lines), stale, []


def render_doc(text: str, claims: dict) -> str:
    """Replace the content of every registered generated block found in ``text``."""
    new, _stale, problems = _process(text, claims, set(BLOCKS), False)
    if problems:
        raise PhaseClaimsError("malformed generated-block markers: " + ", ".join(problems))
    return new


# --- CLI -------------------------------------------------------------------------


def _docs() -> dict[str, set[str]]:
    docs: dict[str, set[str]] = {}
    for bid, (rel, _fn) in BLOCKS.items():
        docs.setdefault(rel, set()).add(bid)
    return docs


def _run(mode: str, root: Path) -> int:
    claims = load_claims(root)
    results: dict[str, str] = {}
    originals: dict[str, str] = {}
    stale_all: list[str] = []
    problems_all: list[str] = []
    for rel, ids in _docs().items():
        path = root / rel
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, ValueError) as exc:
            raise PhaseClaimsError(f"cannot read {path}: {exc}") from None
        new, stale, problems = _process(text, claims, ids, True)
        originals[rel] = text
        results[rel] = new
        stale_all += stale
        problems_all += problems
    flagged = [b for b in BLOCKS if b in stale_all or b in problems_all]
    flagged += [b for b in problems_all if b not in BLOCKS and b not in flagged]
    if mode == "--check":
        for bid in flagged:
            print(bid)
        return 1 if flagged else 0
    if problems_all:
        raise PhaseClaimsError("malformed generated-block markers: " + ", ".join(problems_all))
    for rel, new in results.items():
        if new != originals[rel]:
            (root / rel).write_bytes(new.encode("utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phase_claims")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--write", action="store_true")
    grp.add_argument("--check", action="store_true")
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    args = ap.parse_args(argv)
    try:
        return _run("--write" if args.write else "--check", Path(args.repo_root))
    except PhaseClaimsError as exc:
        print(f"phase_claims: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
