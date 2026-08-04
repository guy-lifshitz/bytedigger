"""RED tests for GH1050 — fold org_config hash into resume-sentinel ctx-hash.

Spec: SHARED/memory/Decisions/2026-07-19_7FE17DC7_gh1050_retry_nonce_sentinel_spec.md

§1q: ``ctx_cfg_sha8`` (public alias in lib/phase_sentinel.py) does not exist
yet — it is imported INSIDE the relevant test bodies (AC1, AC6) so this
module collects cleanly; those tests fail at the in-body ImportError today.

Expected FAIL today: AC1 (ImportError on ctx_cfg_sha8), AC3 (org_config not
yet folded into compute_ctx_hash — hash config-insensitive), AC4 (same,
presence/value insensitivity), AC6 (phase_key already config-sensitive but
compute_ctx_hash is not — parity broken today), AC7 (stale sentinel replay:
maybe_read_sentinel under a different org_config still returns the cached
payload because compute_ctx_hash ignores org_config), AC8 (int-valued
org_config keys also currently invisible to the hash).

Expected PASS today (regression guards, not yet broken):
AC2 (legacy None-gate is untouched by this change — already true),
AC5 (compute_ctx_hash is already deterministic for identical ctx — true
before and after the fix),
AC9 (non-JSON org_config value doesn't crash today simply because
org_config isn't hashed at all yet; the exact legacy formula assertion
happens to hold already since compute_ctx_hash today IS the legacy
formula — this becomes the explicit degrade-path pin post-GREEN).
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.lib.step_sentinel import compute_ctx_hash, maybe_read_sentinel, write_step_sentinel  # noqa: E402
from bytedigger_engine.lib.phase_sentinel import phase_key  # noqa: E402


def make_ctx(scratchpad: Path, *, task_description: str = "t", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "task_description": task_description, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="GH1050 retry nonce sentinel",
        session_id="test-gh1050",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_hash_equals_inline_recomputed_v2_formula(tmp_path):
    import hashlib
    from bytedigger_engine.lib.phase_sentinel import ctx_cfg_sha8  # not-yet-existing alias (§1q)

    ctx = make_ctx(tmp_path)
    task = str(ctx.org_config.get("task_description") or "")
    doc = ctx.org_config.get("decision_doc")
    doc_bytes = b""
    cfg_sha = ctx_cfg_sha8({"org_config": ctx.org_config, "task_description": task})
    expected = hashlib.sha256(
        (ctx.question or "").encode() + b"\0" + task.encode() + b"\0" + doc_bytes + b"\0" + cfg_sha.encode()
    ).hexdigest()[:12]

    actual = compute_ctx_hash(ctx)
    assert actual == expected


# ─── AC2 (regression guard — may PASS today) ───────────────────────────────


def test_ac2_no_task_and_no_doc_returns_none_legacy_gate_intact(tmp_path):
    ctx = make_ctx(tmp_path, task_description="")
    ctx = dataclasses.replace(ctx, org_config={**ctx.org_config, "task_description": ""})
    assert compute_ctx_hash(ctx) is None


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_retry_nonce_present_differs_from_absent(tmp_path):
    ctx_without = make_ctx(tmp_path)
    ctx_with = dataclasses.replace(
        ctx_without, org_config={**ctx_without.org_config, "_retry_nonce": "1"}
    )
    h_without = compute_ctx_hash(ctx_without)
    h_with = compute_ctx_hash(ctx_with)
    assert h_without != h_with


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_retry_nonce_value_change_and_llm_timeout_recipe_key_change(tmp_path):
    base = make_ctx(tmp_path)

    ctx_nonce_1 = dataclasses.replace(base, org_config={**base.org_config, "_retry_nonce": "1"})
    ctx_nonce_2 = dataclasses.replace(base, org_config={**base.org_config, "_retry_nonce": "2"})
    assert compute_ctx_hash(ctx_nonce_1) != compute_ctx_hash(ctx_nonce_2)

    ctx_timeout_900 = dataclasses.replace(
        base, org_config={**base.org_config, "green_llm_timeout_sec": 900}
    )
    ctx_timeout_1800 = dataclasses.replace(
        base, org_config={**base.org_config, "green_llm_timeout_sec": 1800}
    )
    assert compute_ctx_hash(ctx_timeout_900) != compute_ctx_hash(ctx_timeout_1800)


# ─── AC5 (regression guard — may PASS today) ───────────────────────────────


def test_ac5_identical_ctx_hashed_twice_is_equal(tmp_path):
    ctx = make_ctx(tmp_path, extra_key="v")
    h1 = compute_ctx_hash(ctx)
    h2 = compute_ctx_hash(ctx)
    assert h1 == h2
    assert h1 is not None


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_parity_with_real_phase_key_on_org_config_diff(tmp_path):
    from bytedigger_engine.lib.phase_sentinel import ctx_cfg_sha8  # noqa: F401  (not-yet-existing alias, forces §1q deferral)

    base = make_ctx(tmp_path)
    other = dataclasses.replace(base, org_config={**base.org_config, "_retry_nonce": "1"})

    base_dict = dataclasses.asdict(base)
    other_dict = dataclasses.asdict(other)

    key_base = phase_key("run-ac6", "wf", base_dict)
    key_other = phase_key("run-ac6", "wf", other_dict)
    hash_base = compute_ctx_hash(base)
    hash_other = compute_ctx_hash(other)

    # differing pair: phase_key differs AND compute_ctx_hash differs
    assert key_base != key_other
    assert hash_base != hash_other

    # equal pair: identical org_config -> both phase_key and compute_ctx_hash equal
    same = make_ctx(tmp_path)
    same_dict = dataclasses.asdict(same)
    key_base_2 = phase_key("run-ac6", "wf", base_dict)
    key_same = phase_key("run-ac6", "wf", same_dict)
    hash_same = compute_ctx_hash(same)
    assert (key_base_2 == key_same) == (hash_base == hash_same)


# ─── AC7 (side-effect, real tmp scratchpad) ─────────────────────────────────


def test_ac7_retry_nonce_change_invalidates_stale_sentinel_no_replay(tmp_path):
    ctx_a = make_ctx(tmp_path)
    run_id = "run-ac7"

    hash_a = compute_ctx_hash(ctx_a)
    write_step_sentinel(tmp_path, "invoke_red_llm", 1, {"raw_response": "stale"}, run_id, hash_a)

    ctx_a_retry = dataclasses.replace(ctx_a, org_config={**ctx_a.org_config, "_retry_nonce": "1"})
    step = type("Step", (), {"name": "invoke_red_llm", "resume_sentinel": True, "sentinel_input_field": None})()

    def emit(event_type, payload, rid):
        pass

    result_retry = maybe_read_sentinel(ctx_a_retry, step, 1, run_id, emit)
    assert result_retry is None, "org_config-changed ctx must NOT replay the stale sentinel"

    result_same = maybe_read_sentinel(ctx_a, step, 1, run_id, emit)
    assert result_same is not None
    assert result_same.data["raw_response"] == "stale"


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_int_valued_retry_nonce_sensitive_and_distinguishes_values(tmp_path):
    base = make_ctx(tmp_path)
    ctx_without = base
    ctx_with_7 = dataclasses.replace(base, org_config={**base.org_config, "_retry_nonce": 7})
    ctx_with_8 = dataclasses.replace(base, org_config={**base.org_config, "_retry_nonce": 8})

    assert compute_ctx_hash(ctx_without) != compute_ctx_hash(ctx_with_7)
    assert compute_ctx_hash(ctx_with_7) != compute_ctx_hash(ctx_with_8)


# ─── AC9 (degrade path — regression guard, may PASS today) ─────────────────


def test_ac9_non_json_org_config_value_degrades_to_exact_legacy_formula(tmp_path):
    import hashlib

    class Unserializable:
        pass

    ctx = dataclasses.replace(
        make_ctx(tmp_path), org_config={**make_ctx(tmp_path).org_config, "weird": Unserializable()}
    )

    result = compute_ctx_hash(ctx)

    task = str(ctx.org_config.get("task_description") or "")
    doc = ctx.org_config.get("decision_doc")
    doc_bytes = b""
    expected_legacy = hashlib.sha256(
        (ctx.question or "").encode() + b"\0" + task.encode() + b"\0" + doc_bytes
    ).hexdigest()[:12]

    assert result == expected_legacy
