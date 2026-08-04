"""RED tests for GH855 — red_lint rules.yml digest -> RED-writer prompt.

Spec: SHARED/memory/Decisions/2026-07-15_GH855_red_lint_digest_spec.md

UUTs: `_red_lint_prompt_digest`, `_RED_LINT_DIGEST_FALLBACK`,
`_RED_LINT_DIGEST_EXCLUDED_IDS` (all `phase_5_implement.py`), none of which
exist yet on this worktree base. They are accessed via getattr/deferred
import INSIDE each test body (D1CF5FDF/§1q) so this file COLLECTS cleanly
and fails at assert time, never at collection time. `_build_red_prompt` and
`_RED_STABLE_PREFIX` already exist and are imported top-level, mirroring
test_gh501_red_prompt_rules.py / test_gh724_green_lint_digest.py.

§1i: no singleton/time-dependent resource under test — all fixtures
(rules.yml files, spec files) are pre-staged deterministically before
invoking the UUT.

No UUT symbol is patched/mocked (§1l stub-passability).
"""
from __future__ import annotations

from pathlib import Path

import pytest

HERE = Path(__file__).parent
REAL_RULES_YML = HERE.parent / "scripts" / "red_lint" / "rules.yml"


def _get_digest_fn():
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    fn = getattr(phase_5_implement, "_red_lint_prompt_digest", None)
    if fn is None:
        pytest.fail(
            "phase_5_implement._red_lint_prompt_digest does not exist yet (GH855 not implemented)"
        )
    return fn


def _get_fallback_const():
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    const = getattr(phase_5_implement, "_RED_LINT_DIGEST_FALLBACK", None)
    if const is None:
        pytest.fail(
            "phase_5_implement._RED_LINT_DIGEST_FALLBACK does not exist yet (GH855 not implemented)"
        )
    return const


_FIXTURE_RULES = (
    "rules:\n"
    "  - id: fake-rule-alpha\n"
    "    message: |\n"
    "      Alpha bash rule hint for digest propagation testing.\n"
    "    severity: ERROR\n"
    "    languages: [bash]\n"
    "    pattern-regex: 'alpha_pattern'\n"
    "\n"
    "  - id: fake-rule-beta\n"
    "    message: |\n"
    "      Beta python rule hint for digest propagation testing.\n"
    "    severity: ERROR\n"
    "    languages: [python]\n"
    "    pattern-regex: 'beta_pattern'\n"
)


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh855",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_spec(scratchpad: Path, spec_text: str) -> None:
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "build-spec.md").write_text(spec_text, encoding="utf-8")


def _build_red(tmp_path: Path, spec_text: str | None, **org_extra):
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    if spec_text is not None:
        _write_spec(scratchpad, spec_text)
    ctx = _make_ctx(scratchpad, **org_extra)
    return _build_red_prompt(ctx, None)


# ─── AC1: novel fixture ids + hints, both .py and .sh spec_text ──────────────


def test_ac1_fixture_ids_and_hints_present_with_both_language_tokens(tmp_path):
    fn = _get_digest_fn()
    fixture = tmp_path / "rules.yml"
    fixture.write_text(_FIXTURE_RULES, encoding="utf-8")

    spec_text = "Fix widget.py and helper.sh together in one change."
    digest = fn(spec_text, fixture)

    lines = digest.splitlines()
    assert lines[0] == (
        "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):"
    )
    assert "- fake-rule-alpha:" in digest
    assert "- fake-rule-beta:" in digest
    assert "Alpha bash rule hint for digest propagation testing." in digest
    assert "Beta python rule hint for digest propagation testing." in digest


# ─── AC2: py-only spec_text → bash rule gated out ────────────────────────────


def test_ac2_py_only_spec_includes_beta_excludes_alpha(tmp_path):
    fn = _get_digest_fn()
    fixture = tmp_path / "rules.yml"
    fixture.write_text(_FIXTURE_RULES, encoding="utf-8")

    digest = fn("Only touches widget.py, nothing else.", fixture)
    assert "fake-rule-beta" in digest
    assert "fake-rule-alpha" not in digest


# ─── AC3: sh-only spec_text → python rule gated out ──────────────────────────


def test_ac3_sh_only_spec_includes_alpha_excludes_beta(tmp_path):
    fn = _get_digest_fn()
    fixture = tmp_path / "rules.yml"
    fixture.write_text(_FIXTURE_RULES, encoding="utf-8")

    digest = fn("Only touches helper.sh, nothing else.", fixture)
    assert "fake-rule-alpha" in digest
    assert "fake-rule-beta" not in digest


# ─── AC4: fixture whose only rule is excluded → empty string, not fallback ───


def test_ac4_only_excluded_rule_returns_empty_not_fallback(tmp_path):
    fn = _get_digest_fn()
    fallback = _get_fallback_const()

    fixture = tmp_path / "rules.yml"
    fixture.write_text(
        "rules:\n"
        "  - id: bash-grep-pcre\n"
        "    message: |\n"
        "      Excluded bash rule, already covered by the §1p block.\n"
        "    severity: ERROR\n"
        "    languages: [bash]\n"
        "    pattern-regex: 'grep -P'\n",
        encoding="utf-8",
    )

    digest = fn("touch scripts/foo.sh", fixture)
    assert digest == "", f"expected empty string for all-excluded fixture, got: {digest!r}"
    assert digest != fallback


# ─── AC5: nonexistent rules_path → fallback verbatim, no exception ──────────


def test_ac5_missing_rules_path_returns_fallback_verbatim(tmp_path):
    fn = _get_digest_fn()
    fallback = _get_fallback_const()

    result = fn("touch scripts/foo.sh and widget.py", Path("/nonexistent/gh855-rules.yml"))
    assert result == fallback


# ─── AC6: no ids parsed → fallback; missing languages: line → fail-inclusive ─


def test_ac6_no_ids_parsed_returns_fallback(tmp_path):
    fn = _get_digest_fn()
    fallback = _get_fallback_const()

    fixture = tmp_path / "rules.yml"
    fixture.write_text("rules: []\n# no rule entries here\n", encoding="utf-8")

    result = fn("touch scripts/foo.sh and widget.py", fixture)
    assert result == fallback


def test_ac6_rule_with_no_languages_line_is_fail_inclusive(tmp_path):
    fn = _get_digest_fn()
    fixture = tmp_path / "rules.yml"
    fixture.write_text(
        "rules:\n"
        "  - id: fake-rule-nolang\n"
        "    message: |\n"
        "      No languages line on this rule at all.\n"
        "    severity: ERROR\n"
        "    pattern-regex: 'anything'\n",
        encoding="utf-8",
    )

    digest = fn("nothing bash or python or ts here at all", fixture)
    assert "fake-rule-nolang" in digest, (
        "a rule with no languages: line must be fail-inclusive under every gate"
    )


# ─── AC7: real rules.yml + real _build_red_prompt, .sh-referencing spec ─────


def test_ac7_real_prompt_contains_sentinel_and_heredoc_rule_excludes_bash_grep_pcre(tmp_path):
    result = _build_red(tmp_path, "Fix bug in scripts/foo.sh discovered by CI")
    assert result.status == "ok"
    prompt = result.data["prompt"]

    sentinel = "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):"
    assert sentinel in prompt
    assert "- heredoc-fixture-grep-tautology:" in prompt
    assert "- bash-grep-pcre:" not in prompt, (
        "bash-grep-pcre is in the excluded-ids set (already covered by the §1p block)"
    )
    assert "§1p" in prompt

    digest_idx = prompt.index(sentinel)
    rules_idx = prompt.index("RULES:\n")
    grounding_idx = prompt.index("## RED-LINT GROUNDING RULES")
    assert digest_idx < rules_idx, "digest must appear before the general RULES: block"
    assert digest_idx < grounding_idx, (
        "digest must appear before the ## RED-LINT GROUNDING RULES block"
    )


# ─── AC8: pure-.py spec → sentinel absent (no stray fallback) ───────────────


def test_ac8_pure_py_spec_no_sentinel(tmp_path):
    result = _build_red(tmp_path, "Add a new Python helper function foo() in widget.py")
    assert result.status == "ok"
    prompt = result.data["prompt"]
    sentinel = "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):"
    assert sentinel not in prompt

    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    assert callable(getattr(p5, "_red_lint_prompt_digest", None)), (
        "GH855 helper not implemented yet"
    )


# ─── AC9: delta-retry path (cycle=2, findings, gate on) → sentinel absent ───


def test_ac9_delta_retry_path_omits_sentinel(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "1")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _write_spec(scratchpad, "Fix bug in scripts/foo.sh discovered by CI")
    ctx = _make_ctx(scratchpad)

    prev = {"cycle": 2, "findings": "prior cycle findings: fix the off-by-one bug"}
    result = _build_red_prompt(ctx, prev)

    assert result.status == "ok"
    prompt = result.data["prompt"]
    sentinel = "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):"
    assert sentinel not in prompt, (
        "delta-retry branch must not include the red-lint digest (spec §2.4(b))"
    )

    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    assert callable(getattr(p5, "_red_lint_prompt_digest", None)), (
        "GH855 helper not implemented yet"
    )


# ─── AC10: multi-language rule, ANY-gate ────────────────────────────────────


def test_ac10_multilanguage_rule_included_under_sh_only_spec(tmp_path):
    fn = _get_digest_fn()
    fixture = tmp_path / "rules.yml"
    fixture.write_text(
        "rules:\n"
        "  - id: fake-rule-multilang\n"
        "    message: |\n"
        "      Multi-language rule hint for ANY-gate propagation testing.\n"
        "    severity: ERROR\n"
        "    languages: [python, bash]\n"
        "    pattern-regex: 'multilang_pattern'\n",
        encoding="utf-8",
    )

    digest = fn("touch scripts/only_this.sh", fixture)
    assert "fake-rule-multilang" in digest, (
        "a rule spanning python+bash must be included if ANY of its languages passes its gate"
    )


# ─── AC11: hint truncation to exactly 100 chars ─────────────────────────────


def test_ac11_hint_truncated_to_exactly_100_chars(tmp_path):
    fn = _get_digest_fn()
    long_line = "X" * 150
    fixture = tmp_path / "rules.yml"
    fixture.write_text(
        "rules:\n"
        "  - id: fake-rule-longhint\n"
        f"    message: |\n"
        f"      {long_line}\n"
        "    severity: ERROR\n"
        "    languages: [bash]\n"
        "    pattern-regex: 'longhint_pattern'\n",
        encoding="utf-8",
    )

    digest = fn("touch scripts/foo.sh", fixture)
    assert f"- fake-rule-longhint: {long_line[:100]}" in digest, (
        f"expected hint truncated to exactly 100 chars in digest, got: {digest!r}"
    )
    assert long_line[:101] not in digest


# ─── AC12: _RED_STABLE_PREFIX byte-identical / cache-anchor non-regression ──


def test_ac12_red_stable_prefix_unchanged_and_digest_sentinel_not_embedded():
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    prefix = p5._RED_STABLE_PREFIX
    assert "## RED-LINT GROUNDING RULES" in prefix
    assert "F-CITATION (read before you cite)" in prefix

    sentinel = "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):"
    assert sentinel not in prefix, (
        "the new digest sentinel must never be baked into the GH334 cache-anchor "
        "_RED_STABLE_PREFIX — it is appended separately via _red_lint_prompt_digest"
    )

    # forces the new helper to actually exist (fails cleanly today, D1CF5FDF)
    fn = getattr(p5, "_red_lint_prompt_digest", None)
    assert callable(fn), (
        "phase_5_implement._red_lint_prompt_digest must exist as a module-level "
        "callable (GH855 not implemented yet)"
    )


# ─── AC13 (Opus advisory) — .ts-only spec: sentinel + real ts rule present ──


def test_ac13_ts_spec_includes_ts_rules(tmp_path):
    result = _build_red(tmp_path, "Refactor the build-runner.ts spawn wrapper for clarity.")
    assert result.status == "ok"
    prompt = result.data["prompt"]

    sentinel = "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):"
    assert sentinel in prompt
    assert "- ts-mkdtemp-no-realpath:" in prompt


# ─── AC14 (Opus advisory) — cycle=2 retry, delta gate OFF: full scaffold ────


def test_ac14_delta_gate_off_recompute_includes_sentinel(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    # gate_enabled(): enabled unless env var is exactly "0" — this pins it OFF.
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "0")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _write_spec(scratchpad, "Fix bug in scripts/foo.sh discovered by CI")
    ctx = _make_ctx(scratchpad)

    prev = {"cycle": 2, "findings": "prior cycle findings: fix the off-by-one bug"}
    result = _build_red_prompt(ctx, prev)

    assert result.status == "ok"
    prompt = result.data["prompt"]
    sentinel = "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):"
    assert sentinel in prompt, (
        "delta gate OFF must recompute the full scaffold, including the red-lint digest"
    )
