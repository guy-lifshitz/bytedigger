"""RED tests for GH749 — single-source phase_6 AC-parity onto shared
`verdict_verify._classify_parity` (§1g).

Spec: SHARED/memory/Decisions/2026-07-13_GH749_ac_parity_single_source_spec.md
Class: SYSTEMATIC. Tier: Option-D (engine_py prod).

`verdict_verify._classify_parity` DOES NOT EXIST YET in production — GREEN
extracts it out of `verify_ac_parity`. Per §1q extension D1CF5FDF, every
lookup of `_classify_parity` (either as a bare import or as a
`phase_6_review._classify_parity` attribute) is deferred to INSIDE each test
body so this file COLLECTS cleanly and FAILS at assert/call time, never at
collection time. `verdict_verify` (verify_ac_parity, VerifyResult) and
`phase_6_review` (_verify_ac_checklist) already exist and are imported at
module scope (mirrors test_GH398_verdict_verify.py / test_gh388_ac_checklist
satisfaction.py conftest singleton sys.path — no module-level sys.path
manipulation here, §1q/81F97F3D).

§1i: no singleton/time-dependent shared resource — every fixture is a fresh
string built inline per test; env-gate toggling uses monkeypatch.setenv/
delenv scoped to the single test function, nothing raced across tests.
"""
from __future__ import annotations

import phase_6_review as p6
from verdict_verify import VerifyResult, verify_ac_parity

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture text (mirrors _SPEC_TABLE_3 in test_GH398_verdict_verify.py)
# ─────────────────────────────────────────────────────────────────────────────

_SPEC_TABLE_3 = (
    "## §3 — Acceptance Criteria\n\n"
    "| AC | Behavior |\n"
    "|---|---|\n"
    "| AC1 | thing one |\n"
    "| AC2 | thing two |\n"
    "| AC3 | thing three |\n"
)

_PHASE6_SPEC_AC1_2 = (
    "## Acceptance Criteria\n\n"
    "| AC | Behavior |\n"
    "|---|---|\n"
    "| AC1 | thing one |\n"
    "| AC2 | thing two |\n"
)

_PHASE6_SPEC_AC1_3 = (
    "## Acceptance Criteria\n\n"
    "| AC | Behavior |\n"
    "|---|---|\n"
    "| AC1 | thing one |\n"
    "| AC2 | thing two |\n"
    "| AC3 | thing three |\n"
)

_PHASE6_SPEC_AC1 = (
    "## Acceptance Criteria\n\n"
    "| AC | Behavior |\n"
    "|---|---|\n"
    "| AC1 | thing one |\n"
)


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — forcing-function: _verify_ac_checklist delegates to _classify_parity
# ═══════════════════════════════════════════════════════════════════════════

def test_ac1_verify_ac_checklist_delegates_to_classify_parity(monkeypatch) -> None:
    monkeypatch.delenv("HAL_AC_CHECKLIST_GATE", raising=False)

    def _fake_classify(*args, **kwargs):
        return VerifyResult(
            ok=True,
            reject=False,
            result="PARITY_OK",
            reason="stubbed",
            details={"missing": [], "extra": [], "failed": [], "spec_ac_count": 3},
        )

    monkeypatch.setattr(p6, "_classify_parity", _fake_classify, raising=False)

    # Real classification of this fixture is FAIL (spec has AC1-3, checklist
    # omits AC3) — only a real delegation call to the stubbed core flips the
    # verdict to "pass". On origin/main (inline derivation, no
    # `_classify_parity` call) the stub is never invoked -> verdict stays
    # "fail" -> this assertion FAILS, which is the RED.
    checklist_text = (
        "## AC Checklist\n\n"
        "- AC1: PASS\n"
        "- AC2: PASS\n"
    )
    verdict, _detail = p6._verify_ac_checklist(_PHASE6_SPEC_AC1_3, checklist_text)
    assert verdict == "pass", (
        f"GH749 AC1 FAIL: expected _verify_ac_checklist to delegate to the "
        f"stubbed _classify_parity and return 'pass'; got {verdict!r} — "
        f"production still derives the verdict inline instead of calling "
        f"the shared _classify_parity core"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — _classify_parity: missing AC2
# ═══════════════════════════════════════════════════════════════════════════

def test_ac2_classify_parity_missing() -> None:
    from verdict_verify import _classify_parity

    result = _classify_parity({"AC1", "AC2"}, {"AC1": "PASS"})
    assert result.result == "MISSING"
    assert result.details["missing"] == ["AC2"]
    assert result.details["failed"] == []


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — _classify_parity: ignore_extra flag flips EXTRA vs PARITY_OK
# ═══════════════════════════════════════════════════════════════════════════

def test_ac3_classify_parity_ignore_extra_flag() -> None:
    from verdict_verify import _classify_parity

    ignored = _classify_parity(
        {"AC1"}, {"AC1": "PASS", "AC9": "PASS"}, ignore_extra=True
    )
    assert ignored.result == "PARITY_OK"

    not_ignored = _classify_parity(
        {"AC1"}, {"AC1": "PASS", "AC9": "PASS"}, ignore_extra=False
    )
    assert not_ignored.result == "EXTRA"


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — _classify_parity: FAIL_CLAIMED
# ═══════════════════════════════════════════════════════════════════════════

def test_ac4_classify_parity_fail_claimed() -> None:
    from verdict_verify import _classify_parity

    result = _classify_parity({"AC1", "AC2"}, {"AC1": "PASS", "AC2": "FAIL"})
    assert result.result == "FAIL_CLAIMED"
    assert result.details["failed"] == ["AC2"]
    assert result.details["missing"] == []


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — verify_ac_parity byte-identity pin over the GH398 matrix
# ═══════════════════════════════════════════════════════════════════════════

def test_ac5_verify_ac_parity_matrix_byte_identical() -> None:
    all_pass = verify_ac_parity(_SPEC_TABLE_3, "- AC1: PASS\n- AC2: PASS\n- AC3: PASS\n")
    assert all_pass.result == "PARITY_OK"

    missing = verify_ac_parity(_SPEC_TABLE_3, "- AC1: PASS\n- AC2: PASS\n")
    assert missing.result == "MISSING"
    assert missing.details.get("missing") == ["AC3"]

    extra = verify_ac_parity(
        _SPEC_TABLE_3, "- AC1: PASS\n- AC2: PASS\n- AC3: PASS\n- AC9: PASS\n"
    )
    assert extra.result == "EXTRA"
    assert extra.details.get("extra") == ["AC9"]

    fail_claimed = verify_ac_parity(_SPEC_TABLE_3, "- AC1: PASS\n- AC2: FAIL\n- AC3: PASS\n")
    assert fail_claimed.result == "FAIL_CLAIMED"
    assert fail_claimed.details.get("failed") == ["AC2"]

    skip = verify_ac_parity("## Some Section\n\nno ac ids here.\n", "- AC1: PASS\n")
    assert skip.result == "SKIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — phase_6 fixture: section-scope + bold/backtick parser tolerance
# preserved after delegation, failed ids reported in raw form
# ═══════════════════════════════════════════════════════════════════════════

def test_ac6_verify_ac_checklist_bold_backtick_entries_fail(monkeypatch) -> None:
    monkeypatch.delenv("HAL_AC_CHECKLIST_GATE", raising=False)

    checklist_text = (
        "## AC Checklist\n\n"
        "- **AC1**: PASS\n"
        "- `AC2`: FAIL\n"
    )
    verdict, detail = p6._verify_ac_checklist(_PHASE6_SPEC_AC1_2, checklist_text)
    assert verdict == "fail"
    assert detail.get("reason") == "entries"
    assert detail.get("failed") == ["2"]


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — phase_6 fixture: extra checklist id never faults (ignore_extra=True)
# ═══════════════════════════════════════════════════════════════════════════

def test_ac7_verify_ac_checklist_extra_id_never_faults(monkeypatch) -> None:
    monkeypatch.delenv("HAL_AC_CHECKLIST_GATE", raising=False)

    checklist_text = (
        "## AC Checklist\n\n"
        "- AC1: PASS\n"
        "- AC9: PASS\n"
    )
    verdict, _detail = p6._verify_ac_checklist(_PHASE6_SPEC_AC1, checklist_text)
    assert verdict == "pass"


# ═══════════════════════════════════════════════════════════════════════════
# AC8 — env gate disabled: skip(env_disabled), _classify_parity never called
# ═══════════════════════════════════════════════════════════════════════════

def test_ac8_env_gate_disabled_skips_and_never_calls_classify_parity(monkeypatch) -> None:
    monkeypatch.setenv("HAL_AC_CHECKLIST_GATE", "0")

    calls: list = []

    def _tracking_classify(*args, **kwargs):
        calls.append((args, kwargs))
        return VerifyResult(
            ok=True, reject=False, result="PARITY_OK", reason="unused", details={}
        )

    monkeypatch.setattr(p6, "_classify_parity", _tracking_classify, raising=False)

    verdict, detail = p6._verify_ac_checklist(_PHASE6_SPEC_AC1_3, "- AC1: PASS\n")
    assert verdict == "skip"
    assert detail == {"reason": "env_disabled"}
    assert calls == []
