"""verdict_gate.py — deterministic ACCEPT/REJECT gate for verdict docs.

GH517 (34E0B77B) lib extraction of the pilot VG1PILOT ``run_gate`` body
(previously inline in SYSTEM/cli/build/verdict_gate_lint.py) so both the
audit-gate commit-msg hook and the phase_5 acceptance seam can import it as a
sibling engine_py module. Spec: 2026-07-10_34E0B77B_gh517_verdict_gate_wiring_spec.md
(host-side Decisions dir).
"""
from __future__ import annotations

from pathlib import Path

from verdict_verify import (
    ANCHOR_LINE_RE,
    verify_anchor,
    verify_ac_parity,
    parse_checklist,
)
from audit_gate import doc_has_approval


def run_gate(doc_path, repo_root, env) -> tuple[int, dict]:
    """Run the deterministic verdict-doc gate. Returns (exit_code, report)."""
    if env.get("HAL_VERDICT_GATE_LINT") == "0":
        return 0, {"result": "SKIP"}

    text = Path(doc_path).read_text(encoding="utf-8")

    approved = doc_has_approval(text)
    if not approved:
        return 0, {"result": "NOT_APPROVED", "approved": False}

    anchor = verify_anchor(text, repo_root)
    if anchor.result != "VERIFIED":
        return 1, {"result": anchor.result, "anchor": anchor.result, "approved": True}

    checklist = parse_checklist(text)
    if checklist is None:
        return 1, {"result": "NO_CHECKLIST", "anchor": anchor.result, "approved": True}
    fail_rows = sorted(k for k, v in checklist.items() if v == "FAIL")
    if fail_rows:
        return 1, {
            "result": "FAIL_ROW",
            "anchor": anchor.result,
            "fail_rows": fail_rows,
            "approved": True,
        }

    spec_relpath = None
    for kind, relpath, _sha in [
        (m.group(1), m.group(2), m.group(3))
        for m in ANCHOR_LINE_RE.finditer(text)
    ]:
        if kind == "spec":
            spec_relpath = relpath
            break
    parity_result = "SKIP"
    if spec_relpath is not None:
        spec_text = (Path(repo_root) / spec_relpath).read_text(encoding="utf-8")
        parity = verify_ac_parity(spec_text, text)
        parity_result = parity.result
        if parity_result not in ("PARITY_OK", "SKIP"):
            return 1, {
                "result": f"PARITY_{parity_result}",
                "anchor": anchor.result,
                "parity": parity_result,
                "approved": True,
            }

    return 0, {
        "result": "GATE_PASS",
        "anchor": anchor.result,
        "parity": parity_result,
        "approved": True,
    }
