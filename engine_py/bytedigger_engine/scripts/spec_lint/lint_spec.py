"""spec_lint driver — Step 5B.1, agreement D04A3BA8.
Reads a spec file, calls citation_verifier.verify_all, and emits findings to stdout
in the format <basename>:<rule_id>:<short_evidence>. Exit 0 = no findings, 1 = findings,
2 = usage error.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# Resolve ../lib relative to this file so citation_verifier is importable standalone.
_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"

# Also expose engine_py root (parent of scripts/) so `lib.project_root` is importable.
_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from bytedigger_engine.scripts.lib import citation_verifier
    from bytedigger_engine.scripts.lib import sibling_test_verifier
    from bytedigger_engine.scripts.lib import consumer_dispatch_verifier
    from bytedigger_engine.scripts.lib import reachability_verifier
    from bytedigger_engine.scripts.lib import helper_extraction_verifier
    from bytedigger_engine.scripts.lib import ground_truth_verifier
    from bytedigger_engine.scripts.lib import closure_evidence_verifier
    from bytedigger_engine.scripts.lib import mutation_two_sidedness_verifier
except ImportError as _e:
    print(f"ERROR: cannot import citation_verifier, sibling_test_verifier, consumer_dispatch_verifier, reachability_verifier, helper_extraction_verifier, ground_truth_verifier, closure_evidence_verifier, mutation_two_sidedness_verifier from {_LIB_DIR}: {_e}", file=sys.stderr)
    sys.exit(2)

try:
    from bytedigger_engine.lib.project_root import resolve_project_root
except ImportError as _e:
    print(f"ERROR: cannot import resolve_project_root from {_ENGINE_ROOT}: {_e}", file=sys.stderr)
    sys.exit(2)

try:
    from bytedigger_engine import config_provider
except ImportError as _e:
    print(f"ERROR: cannot import config_provider from {_ENGINE_ROOT}: {_e}", file=sys.stderr)
    sys.exit(2)


# 4197B484 (GH1121) §1.4: advisory rule ids are NON-BLOCKING. They are routed
# to stderr with an `ADVISORY: ` prefix and never reach stdout -- phase_45_spec
# turns every stdout line into a directed-repair locus, so an advisory on
# stdout would become something the repair agent is told to patch.
_ADVISORY_RULE_IDS = frozenset({"ambiguous-symbol"})


def _rule_id(kind: str, evidence: str) -> str:
    if kind == "line":
        return "unverified-line-range"
    if kind == "snippet":
        return "unverified-snippet"
    if "case-pattern" in evidence:
        return "case-block-vs-function"
    return "unverified-function-citation"


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint spec file for fabricated citations.")
    parser.add_argument("spec_path", help="Path to the spec markdown file.")
    parser.add_argument(
        "--hal-root",
        default=None,
        help="Absolute path to HAL root (default: derived from git toplevel of cwd).",
    )
    args = parser.parse_args()

    spec_path = Path(args.spec_path)
    hal_root = Path(args.hal_root) if args.hal_root else resolve_project_root({})[0]

    if not spec_path.exists():
        print(f"ERROR: spec file not found: {spec_path}", file=sys.stderr)
        sys.exit(2)

    try:
        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: cannot read spec file {spec_path}: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        pairs = citation_verifier.verify_all(spec_text, hal_root)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: citation_verifier raised unexpected exception: {exc}", file=sys.stderr)
        sys.exit(2)

    basename = spec_path.name
    # (offset, rule_id, rendered-finding) — rule_id is carried explicitly so the
    # advisory partition below never has to re-parse the rendered string.
    findings: list[tuple[int, str, str]] = [
        (
            c.text_offset,
            _rule_id(c.kind, r.evidence),
            f"{basename}:{_rule_id(c.kind, r.evidence)}:{r.evidence}",
        )
        for c, r in pairs
        if not r.verified
    ]

    # Seven verifiers, seven distinct finding dataclasses, one shared loop variable —
    # annotate once so mypy does not re-narrow it to the first one (GH1409).
    f: Any
    for f in sibling_test_verifier.find_missing_audits(spec_text, hal_root):
        findings.append((f.offset, f.rule_id, f"{basename}:{f.rule_id}:{f.evidence}"))

    try:
        _cd_max = int(config_provider.env_opt("HAL_CONSUMER_DISPATCH_MAX") or "0") or None
    except ValueError:
        _cd_max = None
    _cd_kwargs = {"max_consumers": _cd_max} if _cd_max is not None and _cd_max > 0 else {}
    for f in consumer_dispatch_verifier.find_uncited_consumers(spec_text, hal_root, **_cd_kwargs):
        findings.append((f.offset, f.rule_id, f"{basename}:{f.rule_id}:{f.evidence}"))

    for f in reachability_verifier.find_unreachable_acs(spec_text, hal_root):
        findings.append((f.offset, f.rule_id, f"{basename}:{f.rule_id}:{f.evidence}"))

    for f in helper_extraction_verifier.find_missing_helpers(spec_text, hal_root):
        findings.append((f.offset, f.rule_id, f"{basename}:{f.rule_id}:{f.evidence}"))

    for f in ground_truth_verifier.find_missing_ground_truth(spec_text, hal_root):
        findings.append((f.offset, f.rule_id, f"{basename}:{f.rule_id}:{f.evidence}"))

    for f in closure_evidence_verifier.find_unbacked_closure_claims(spec_text, hal_root):
        findings.append((f.offset, f.rule_id, f"{basename}:{f.rule_id}:{f.evidence}"))

    for f in mutation_two_sidedness_verifier.find_one_sided_mutations(spec_text, hal_root):
        findings.append((f.offset, f.rule_id, f"{basename}:{f.rule_id}:{f.evidence}"))

    # Sort by spec text offset for deterministic output.
    findings.sort(key=lambda t: t[0])

    # GH371 §2.3: surface the (previously dropped) locus as a 1-indexed line
    # number. Emit <basename>:L<line>:<rule_id>:<evidence>; backward-safe (still
    # one finding per line, evidence — which may itself contain colons —
    # preserved verbatim after the L<line> field).
    # 4197B484 (GH1121) §1.4: UNCONDITIONAL advisory partition — every advisory
    # goes to stderr here, BEFORE the stdout loop and BEFORE the rc decision,
    # in the advisory-only run AND in a mixed run alike.
    blocking_findings: list[str] = []
    for offset, rule_id, finding in findings:
        lineno = spec_text.count("\n", 0, offset) + 1
        base, _, rest = finding.partition(":")
        rendered = f"{base}:L{lineno}:{rest}"
        if rule_id in _ADVISORY_RULE_IDS:
            print(f"ADVISORY: {rendered}", file=sys.stderr)
        else:
            blocking_findings.append(rendered)

    for rendered in blocking_findings:
        print(rendered)

    sys.exit(1 if blocking_findings else 0)


if __name__ == "__main__":
    main()
