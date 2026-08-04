"""RED tests for 97B6CF02 (GH #285) Cycle 1 — unified timeout policy resolver.

Covers AC1-AC12 from spec §3
(2026-07-06_97B6CF02_gh285_timeout_policy_c1_spec.md).

UUT: engine_py/lib/timeout_policy.py — `resolve_timeout_sec`, `load_policy`.
Does NOT exist at RED time. Per spec §1q/D1CF5FDF, each test imports the UUT
INSIDE the test function body (module-level `from bytedigger_engine.lib.timeout_policy import
...` would make the whole file non-collectable and risk the ~30 min
red_runtime collection hang). conftest.py already puts engine_py/ on
sys.path (Conftest-import-time singleton, §1q), so no per-file sys.path
manipulation is needed here.

Pre-GREEN predict: every test FAILS with ModuleNotFoundError at call time
(collection stays green — no module-level import of timeout_policy).
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# AC12 parity table — embedded verbatim from spec §3 AC12 table. `None` marks
# an undefined tier for that step (dashes in the spec table).
# ---------------------------------------------------------------------------

PARITY_TABLE = [
    # step, base, FEATURE, COMPLEX, opus, override_key
    ("discovery.llm", 300, None, None, None, "llm_timeout_sec"),
    ("explore.llm", 600, None, None, None, "explore_llm_timeout_sec"),
    ("clarify.llm", 300, None, None, None, "clarify_llm_timeout_sec"),
    ("architect.llm", 600, None, None, None, "llm_timeout_sec"),
    ("spec.writer", 600, None, 1800, 900, "spec_llm_timeout_sec"),
    ("spec.reviewer", 300, 600, 900, 600, "review_llm_timeout_sec"),
    ("spec_lite.writer", 600, None, None, None, "spec_llm_timeout_sec"),
    ("spec_lite.reviewer", 300, 600, 900, None, "review_llm_timeout_sec"),
    ("implement.red", 1200, 2400, 2400, None, "red_llm_timeout_sec"),
    ("implement.validation", 600, None, 1200, None, "validation_llm_timeout_sec"),
    ("implement.green", 900, 1500, 1800, None, "green_llm_timeout_sec"),
    ("integrity.llm", 600, None, None, None, "integrity_llm_timeout_sec"),
    ("review.reviewer", 600, 1000, 1500, None, "review_llm_timeout_sec"),
    ("review.fix", 900, None, None, None, "fix_llm_timeout_sec"),
    ("review.satisfaction", 600, 1000, 1500, None, "satisfaction_llm_timeout_sec"),
    ("fix_integrity.llm", 600, None, None, None, "fix_integrity_llm_timeout_sec"),
    ("synthesize.llm", 600, None, None, None, "synthesizer_llm_timeout_sec"),
]


# ---------------------------------------------------------------------------
# AC1: base default, no cfg
# ---------------------------------------------------------------------------


def test_ac1_base_default_no_cfg():
    """AC1: resolve_timeout_sec('implement.green', None) == 900."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    assert resolve_timeout_sec("implement.green", None) == 900


# ---------------------------------------------------------------------------
# AC2: complexity tiers, case-insensitive
# ---------------------------------------------------------------------------


def test_ac2_complexity_complex():
    """AC2: complexity=COMPLEX for implement.green -> 1800."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    assert resolve_timeout_sec("implement.green", {"complexity": "COMPLEX"}) == 1800


def test_ac2_complexity_feature_case_insensitive():
    """AC2: complexity='feature' (lowercase) for implement.green -> 1500."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    assert resolve_timeout_sec("implement.green", {"complexity": "feature"}) == 1500


# ---------------------------------------------------------------------------
# AC3: override beats class
# ---------------------------------------------------------------------------


def test_ac3_override_beats_complexity_class():
    """AC3: green_llm_timeout_sec=77 beats COMPLEX -> 77."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    cfg = {"green_llm_timeout_sec": 77, "complexity": "COMPLEX"}
    assert resolve_timeout_sec("implement.green", cfg) == 77


# ---------------------------------------------------------------------------
# AC4: malformed override falls through to base
# ---------------------------------------------------------------------------


def test_ac4_malformed_override_falls_through():
    """AC4: green_llm_timeout_sec='abc' (TypeError/ValueError) -> base 900."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    cfg = {"green_llm_timeout_sec": "abc"}
    assert resolve_timeout_sec("implement.green", cfg) == 900


# ---------------------------------------------------------------------------
# AC5a/AC5b: clamp + falsy-zero semantics
# ---------------------------------------------------------------------------


def test_ac5a_negative_override_clamps_to_one():
    """AC5a: green_llm_timeout_sec='-5' -> max(1,int(-5)) == 1."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    cfg = {"green_llm_timeout_sec": "-5"}
    assert resolve_timeout_sec("implement.green", cfg) == 1


def test_ac5b_falsy_zero_override_skipped():
    """AC5b: green_llm_timeout_sec=0 is falsy -> skip override -> base 900."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    cfg = {"green_llm_timeout_sec": 0}
    assert resolve_timeout_sec("implement.green", cfg) == 900


# ---------------------------------------------------------------------------
# AC6: COMPLEX beats opus-class model floor
# ---------------------------------------------------------------------------


def test_ac6a_opus_floor_no_complexity():
    """AC6a: spec.writer, model_is_opus=True, no complexity -> 900."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    assert resolve_timeout_sec("spec.writer", {}, model_is_opus=True) == 900


def test_ac6b_complexity_beats_opus_floor():
    """AC6b: spec.writer, model_is_opus=True, complexity=COMPLEX -> 1800."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    cfg = {"complexity": "COMPLEX"}
    assert resolve_timeout_sec("spec.writer", cfg, model_is_opus=True) == 1800


# ---------------------------------------------------------------------------
# AC7: FEATURE beats opus floor
# ---------------------------------------------------------------------------


def test_ac7_feature_beats_opus_floor():
    """AC7: spec.reviewer, complexity=FEATURE, model_is_opus=True -> 600."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    cfg = {"complexity": "FEATURE"}
    assert resolve_timeout_sec("spec.reviewer", cfg, model_is_opus=True) == 600


# ---------------------------------------------------------------------------
# AC8: unknown step -> ValueError, fail-closed
# ---------------------------------------------------------------------------


def test_ac8_unknown_step_raises_value_error():
    """AC8: unknown step 'nonexistent.role' raises ValueError (no silent default)."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    with pytest.raises(ValueError):
        resolve_timeout_sec("nonexistent.role", None)


# ---------------------------------------------------------------------------
# AC9: load_policy with None / missing path -> defaults
# ---------------------------------------------------------------------------


def test_ac9a_load_policy_none_returns_defaults():
    """AC9a: load_policy(None) resolves defaults; implement.green -> 900."""
    from bytedigger_engine.lib.timeout_policy import load_policy, resolve_timeout_sec

    policy = load_policy(None)
    assert resolve_timeout_sec("implement.green", None, policy=policy) == 900


def test_ac9b_load_policy_missing_path_returns_defaults(tmp_path):
    """AC9b: load_policy(<missing path>) resolves defaults; implement.green -> 900."""
    from bytedigger_engine.lib.timeout_policy import load_policy, resolve_timeout_sec

    missing = tmp_path / "does-not-exist.json"
    policy = load_policy(str(missing))
    assert resolve_timeout_sec("implement.green", None, policy=policy) == 900


# ---------------------------------------------------------------------------
# AC10: override merge preserves other tiers
# ---------------------------------------------------------------------------


def test_ac10_override_merge_preserves_base_and_overrides_complex(tmp_path):
    """AC10: overrides file bumps implement.green COMPLEX to 3600, base stays 900."""
    from bytedigger_engine.lib.timeout_policy import load_policy, resolve_timeout_sec

    override_path = tmp_path / "timeout-policy.json"
    override_path.write_text(
        json.dumps({"overrides": {"implement.green": {"COMPLEX": 3600}}})
    )
    policy = load_policy(str(override_path))

    assert (
        resolve_timeout_sec(
            "implement.green", {"complexity": "COMPLEX"}, policy=policy
        )
        == 3600
    )
    assert resolve_timeout_sec("implement.green", None, policy=policy) == 900


# ---------------------------------------------------------------------------
# AC11a/AC11b/AC11c: malformed policy sources -> ValueError, fail-closed
# ---------------------------------------------------------------------------


def test_ac11a_malformed_json_raises_value_error(tmp_path):
    """AC11a: non-JSON file content raises ValueError."""
    from bytedigger_engine.lib.timeout_policy import load_policy

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("not json {")

    with pytest.raises(ValueError):
        load_policy(str(bad_path))


def test_ac11b_toplevel_list_raises_value_error(tmp_path):
    """AC11b: top-level JSON array (non-dict) raises ValueError."""
    from bytedigger_engine.lib.timeout_policy import load_policy

    bad_path = tmp_path / "bad_list.json"
    bad_path.write_text("[]")

    with pytest.raises(ValueError):
        load_policy(str(bad_path))


def test_ac11c_non_int_tier_value_raises_value_error(tmp_path):
    """AC11c: non-int tier value in overrides raises ValueError at load."""
    from bytedigger_engine.lib.timeout_policy import load_policy

    bad_path = tmp_path / "bad_tier.json"
    bad_path.write_text(
        json.dumps({"overrides": {"implement.green": {"COMPLEX": "abc"}}})
    )

    with pytest.raises(ValueError):
        load_policy(str(bad_path))


# ---------------------------------------------------------------------------
# AC12: full parity sweep over DEFAULT_POLICY seed table (data-driven)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step,base,feature,complex_,opus,override_key",
    PARITY_TABLE,
    ids=[row[0] for row in PARITY_TABLE],
)
def test_ac12_parity_sweep(step, base, feature, complex_, opus, override_key):
    """AC12: for every seeded step, base/FEATURE/COMPLEX/opus resolve to the
    frozen parity-table values (today's effective values, zero behavior change).
    """
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    # base (no cfg)
    assert resolve_timeout_sec(step, None) == base

    # FEATURE tier, if defined for this step
    if feature is not None:
        assert resolve_timeout_sec(step, {"complexity": "FEATURE"}) == feature

    # COMPLEX tier, if defined for this step
    if complex_ is not None:
        assert resolve_timeout_sec(step, {"complexity": "COMPLEX"}) == complex_

    # opus-class model floor, if defined for this step
    if opus is not None:
        assert resolve_timeout_sec(step, {}, model_is_opus=True) == opus

    # override_key sanity: a truthy override always wins over base
    override_cfg = {override_key: 55555}
    assert resolve_timeout_sec(step, override_cfg) == 55555


# ---------------------------------------------------------------------------
# Gate-1: distinguishable FEATURE-vs-opus / override-vs-opus precedence
# (existing AC7 was vacuous: default FEATURE=600 == opus=600 for
# spec.reviewer, so it could pass even with FEATURE/opus branches swapped.)
# ---------------------------------------------------------------------------


def test_gate1a_feature_beats_opus_distinguishable(tmp_path):
    """Gate1a: with FEATURE overridden to a value distinct from opus (600),
    FEATURE must still beat the opus-class floor for spec.reviewer."""
    from bytedigger_engine.lib.timeout_policy import load_policy, resolve_timeout_sec

    override_path = tmp_path / "timeout-policy.json"
    override_path.write_text(
        json.dumps({"overrides": {"spec.reviewer": {"FEATURE": 700}}})
    )
    policy = load_policy(str(override_path))

    cfg = {"complexity": "FEATURE"}
    assert (
        resolve_timeout_sec("spec.reviewer", cfg, policy=policy, model_is_opus=True)
        == 700
    )


def test_gate1b_override_beats_opus():
    """Gate1b: an explicit override key beats the opus-class model floor."""
    from bytedigger_engine.lib.timeout_policy import resolve_timeout_sec

    cfg = {"spec_llm_timeout_sec": 77}
    assert resolve_timeout_sec("spec.writer", cfg, model_is_opus=True) == 77


# ---------------------------------------------------------------------------
# Gate-2: shipped JSON shape tolerance (SHARED/config/timeout-policy.json)
# ---------------------------------------------------------------------------


def test_gate2a_shipped_json_shape_tolerated(tmp_path):
    """Gate2a: the exact shipped file shape (with $schema-note key and empty
    overrides) loads successfully and unknown top-level keys are tolerated."""
    from bytedigger_engine.lib.timeout_policy import load_policy, resolve_timeout_sec

    shipped_path = tmp_path / "timeout-policy.json"
    shipped_path.write_text(
        json.dumps(
            {
                "$schema-note": (
                    "overrides-only layer, defaults live in "
                    "lib/timeout_policy.py DEFAULT_POLICY"
                ),
                "overrides": {},
            }
        )
    )
    policy = load_policy(str(shipped_path))

    assert resolve_timeout_sec("implement.green", None, policy=policy) == 900


def test_gate2b_missing_overrides_key(tmp_path):
    """Gate2b: a bare '{}' (dict, no 'overrides' key) is accepted; absent
    overrides sub-object == empty, defaults remain intact."""
    from bytedigger_engine.lib.timeout_policy import load_policy, resolve_timeout_sec

    bare_path = tmp_path / "timeout-policy.json"
    bare_path.write_text(json.dumps({}))
    policy = load_policy(str(bare_path))

    assert resolve_timeout_sec("implement.green", None, policy=policy) == 900
