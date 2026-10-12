"""bd#125 -- the role template is bounded by the bd#116 contract, pinned per run, hashed by its bytes.

RED tests for docs/decisions/2026-10-12-bd125-role-template-content-pin.md (AC1-AC24).
The module under test (`bytedigger_engine.role_template`) is reached only through
`_rt()` inside test bodies, so a missing API fails the test, never collection; the new
symbol `role_template_digest` is imported lazily inside the tests that need it.

RED-signal contract:
- Fail by assertion on today's tree: AC3-AC5, AC6, AC7 (non-core cases), AC8, AC9, AC10,
  AC11, AC12, AC13 (AttributeError on `.sha256`), AC14, AC15, AC17, AC18, AC19
  (AttributeError), AC20, AC21-AC24.
- PRE-PASSING GUARDS (correctness guards that protect post-GREEN behavior): AC1, AC2, AC16.

Pin cases (AC8-AC12, AC14) set a run via telemetry_ctx with a unique uuid run id per test
(the module's pin map is private and process-wide) and always clear it in teardown.
"""
from __future__ import annotations

import hashlib
import importlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import CodedStepError, WorkflowContext
from bytedigger_engine.lib.phase_sentinel import ctx_cfg_sha8
from bytedigger_engine.lib.step_sentinel import compute_ctx_hash
from bytedigger_engine.workflows import phase_workflows_common as common

KEY = "role_template_path"
CODE = "E_ROLE_TEMPLATE_INVALID"
OLD_REASONS = (
    "bad_value_type", "invalid_path", "missing", "unreadable", "not_regular_file",
    "over_cap", "not_utf8", "contains_nul", "empty",
)
NEW_REASONS = (
    "core_unavailable", "frontmatter_not_allowed", "section_required", "text_outside_section",
    "section_not_overridable", "forbidden_markup", "unclosed_fence", "heading_level_invalid",
    "setext_heading", "invalid_section_title", "duplicate_section", "empty_section", "pin_drift",
)
CANARY_A = "BD125-CANARY-AAAA"
CANARY_B = "BD125-CANARY-BBBB"


def _rt():
    """The module under test, imported at call time (never at collection)."""
    return importlib.import_module("bytedigger_engine.role_template")


@pytest.fixture(autouse=True)
def _bd125_isolation():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


def _set_run(run_id: str | None = None) -> str:
    rid = run_id or f"bd125-{uuid.uuid4().hex}"
    telemetry_ctx.set_current_run(event_log=None, run_id=rid, step_name="s")
    return rid


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _tpl(tmp_path: Path, body: str, name: str = "role.md") -> Path:
    return _write(tmp_path / name, body)


def _err(path: Path | str):
    rt = _rt()
    with pytest.raises(rt.RoleTemplateError) as ei:
        rt.load_role_template({KEY: str(path)})
    return ei.value


def _tmp_plugin(tmp_path: Path, monkeypatch, overridable: str, slugs: list[str]) -> Path:
    plugin = tmp_path / "plugin"
    sections = "\n".join(f"## {s}\n\ncore text for {s}\n" for s in slugs)
    _write(
        plugin / "skills" / "bytedigger" / "SKILL.md",
        f'---\nname: ByteDigger\nmetadata:\n  overridable: "{overridable}"\n---\n\n'
        f"# ByteDigger\n\n{sections}",
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    return plugin


def _ctx_for(role_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config={KEY: str(role_path)},
        question="q", session_id="bd125", persona="hal", framework=None, domain=None,
    )


def _hash_ctx(**org):
    return SimpleNamespace(org_config=org, question="q")


VALID_A = f"## project-conventions\n{CANARY_A} use tabs\n"
VALID_B = f"## project-conventions\n{CANARY_B} use spaces\n"


# ---------------------------------------------------------------------------
# Item 1: content bounding
# ---------------------------------------------------------------------------

def test_ac3_non_overridable_section_rejected(tmp_path):
    """AC3: `## output-schema` -> section_not_overridable, coded, one line, no body text."""
    f = _tpl(tmp_path, f"## output-schema\n{CANARY_A}\n")
    exc = _err(f)
    assert exc.reason == "section_not_overridable"
    assert exc.code == CODE
    assert isinstance(exc, CodedStepError)
    msg = str(exc)
    assert "output-schema" in msg
    assert "\n" not in msg
    assert CANARY_A not in msg


def test_ac4_text_before_first_h2_rejected(tmp_path):
    """AC4: non-blank text before the first H2 -> text_outside_section."""
    f = _tpl(tmp_path, f"{CANARY_A} preamble\n\n## project-conventions\nbody\n")
    exc = _err(f)
    assert exc.reason == "text_outside_section"
    assert CANARY_A not in str(exc)


_AC5_CASES = [
    ("frontmatter_not_allowed", "---\nk: v\n---\n## project-conventions\nbody\n"),
    ("forbidden_markup", "## project-conventions\nbody <!-- x -->\n"),
    ("unclosed_fence", "## project-conventions\n```\ncode\n"),
    ("heading_level_invalid", "## project-conventions\nbody\n# Top\nmore\n"),
    ("setext_heading", "## project-conventions\nbody\n\nTitle\n=====\n"),
    ("duplicate_section", "## project-conventions\none\n\n## project-conventions\ntwo\n"),
    ("empty_section", "## project-conventions\n"),
]


@pytest.mark.parametrize("reason,body", _AC5_CASES, ids=[c[0] for c in _AC5_CASES])
def test_ac5_structural_rules_each_own_reason(reason, body, tmp_path):
    """AC5: each structural violation yields its matching reason."""
    exc = _err(_tpl(tmp_path, body))
    assert exc.reason == reason
    assert exc.code == CODE
    assert "\n" not in str(exc)


def test_ac6_core_unavailable_when_plugin_root_has_no_core(tmp_path, monkeypatch):
    """AC6: CLAUDE_PLUGIN_ROOT = empty dir -> core_unavailable (fail-closed)."""
    empty = tmp_path / "empty_plugin"
    empty.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(empty))
    f = _tpl(tmp_path, "plain text\n")
    assert _err(f).reason == "core_unavailable"


def test_ac7_overridable_set_comes_from_the_core(tmp_path, monkeypatch):
    """AC7: a tmp core overriding `a, b`: `## b` ok; plain text -> section_required;
    `## project-conventions` -> section_not_overridable."""
    _tmp_plugin(tmp_path, monkeypatch, "a, b", ["a", "b"])
    rt_mod = _rt()
    ok = _tpl(tmp_path, "## b\nbody\n", "ok.md")
    assert rt_mod.load_role_template({KEY: str(ok)}).content == "## b\nbody\n\n"
    assert _err(_tpl(tmp_path, "plain text\n", "plain.md")).reason == "section_required"
    other = _tpl(tmp_path, "## project-conventions\nbody\n", "other.md")
    assert _err(other).reason == "section_not_overridable"


# ---------------------------------------------------------------------------
# Item 2: per-run pin
# ---------------------------------------------------------------------------

def test_ac8_rewrite_within_run_is_pin_drift(tmp_path):
    """AC8: pinned under r1, file rewritten to other VALID bytes -> pin_drift."""
    f = _tpl(tmp_path, VALID_A)
    _set_run()
    rt_mod = _rt()
    rt_mod.load_role_template({KEY: str(f)})
    f.write_text(VALID_B, encoding="utf-8")
    exc = _err(f)
    assert exc.reason == "pin_drift"
    msg = str(exc)
    assert KEY in msg
    assert str(f) in msg
    assert CANARY_A not in msg and CANARY_B not in msg
    assert "\n" not in msg


def test_ac9_other_run_has_its_own_pin(tmp_path):
    """AC9: r1 read, rewrite, r1 read raises pin_drift; then run r2 reads the rewritten file."""
    f = _tpl(tmp_path, VALID_A)
    rt_mod = _rt()
    _set_run("bd125-ac9-r1-" + uuid.uuid4().hex)
    rt_mod.load_role_template({KEY: str(f)})
    f.write_text(VALID_B, encoding="utf-8")
    assert _err(f).reason == "pin_drift"
    _set_run("bd125-ac9-r2-" + uuid.uuid4().hex)
    assert CANARY_B in rt_mod.load_role_template({KEY: str(f)}).content


def test_ac10_no_run_means_no_pin(tmp_path):
    """AC10: r1 read, rewrite, r1 read raises pin_drift; then no current run reads OK."""
    f = _tpl(tmp_path, VALID_A)
    rt_mod = _rt()
    _set_run("bd125-ac10-r1-" + uuid.uuid4().hex)
    rt_mod.load_role_template({KEY: str(f)})
    f.write_text(VALID_B, encoding="utf-8")
    assert _err(f).reason == "pin_drift"
    telemetry_ctx.clear_current_run()
    assert CANARY_B in rt_mod.load_role_template({KEY: str(f)}).content


def test_ac11_pin_is_checked_before_content(tmp_path):
    """AC11: pinned, then rewritten to a content-invalid file -> pin_drift, not a content reason."""
    f = _tpl(tmp_path, VALID_A)
    _set_run()
    _rt().load_role_template({KEY: str(f)})
    f.write_text("## output-schema\nbody\n", encoding="utf-8")
    assert _err(f).reason == "pin_drift"


def test_ac12_failed_first_read_records_no_pin(tmp_path):
    """AC12: a content-invalid first read records no pin; a fixed file in the same run is accepted."""
    f = _tpl(tmp_path, "## output-schema\nbody\n")
    _set_run()
    assert _err(f).reason == "section_not_overridable"
    f.write_text(VALID_B, encoding="utf-8")
    assert CANARY_B in _rt().load_role_template({KEY: str(f)}).content


def test_ac13_sha256_is_hex_of_raw_bytes(tmp_path):
    """AC13: RoleTemplate.sha256 == sha256(raw file bytes).hexdigest()."""
    f = _tpl(tmp_path, VALID_A)
    rt = _rt().load_role_template({KEY: str(f)})
    assert rt.sha256 == hashlib.sha256(f.read_bytes()).hexdigest()


def test_ac14_common_seam_raises_on_bad_content_and_on_drift(tmp_path):
    """AC14: `_role_template(ctx)` (the one call seam) raises RoleTemplateError."""
    rt_mod = _rt()
    bad = _tpl(tmp_path, "## output-schema\nbody\n", "bad.md")
    with pytest.raises(rt_mod.RoleTemplateError) as ei:
        common._role_template(_ctx_for(bad))
    assert ei.value.reason == "section_not_overridable"

    f = _tpl(tmp_path, VALID_A, "good.md")
    _set_run()
    ctx = _ctx_for(f)
    common._role_template(ctx)
    f.write_text(VALID_B, encoding="utf-8")
    with pytest.raises(rt_mod.RoleTemplateError) as ei:
        common._role_template(ctx)
    assert ei.value.reason == "pin_drift"


# ---------------------------------------------------------------------------
# Item 3: resume hash folds the bytes
# ---------------------------------------------------------------------------

def test_ac15_hash_depends_on_template_bytes(tmp_path):
    """AC15: same path, bytes A vs B -> different hashes; same bytes -> same hash."""
    f = _tpl(tmp_path, VALID_A)
    mk = lambda: _hash_ctx(**{KEY: str(f), "task_description": "t"})  # noqa: E731
    h_a1 = compute_ctx_hash(mk())
    h_a2 = compute_ctx_hash(mk())
    f.write_text(VALID_B, encoding="utf-8")
    h_b = compute_ctx_hash(mk())
    assert h_a1 == h_a2
    assert h_a1 != h_b


def test_ac17_template_alone_gives_a_hash_that_differs_by_bytes(tmp_path):
    """AC17: only role_template_path set -> hash is not None and differs by bytes."""
    f = _tpl(tmp_path, VALID_A)
    h_a = compute_ctx_hash(_hash_ctx(**{KEY: str(f)}))
    assert h_a is not None
    f.write_text(VALID_B, encoding="utf-8")
    h_b = compute_ctx_hash(_hash_ctx(**{KEY: str(f)}))
    assert h_b is not None and h_b != h_a


def test_ac18_missing_template_does_not_raise_and_is_stable(tmp_path):
    """AC18: a missing template file -> compute_ctx_hash returns a stable str, never raises."""
    ctx = _hash_ctx(**{KEY: str(tmp_path / "nope.md")})
    h1 = compute_ctx_hash(ctx)
    h2 = compute_ctx_hash(ctx)
    assert isinstance(h1, str) and h1 == h2


def test_ac19_role_template_digest_contract(tmp_path):
    """AC19: None unset; 64-hex of raw bytes for a file; "!missing" for a missing path; never raises."""
    from bytedigger_engine.role_template import role_template_digest

    assert role_template_digest({}) is None
    assert role_template_digest({KEY: None}) is None
    assert role_template_digest({KEY: ""}) is None
    f = _tpl(tmp_path, VALID_A)
    d = role_template_digest({KEY: str(f)})
    assert re.fullmatch(r"[0-9a-f]{64}", d)
    assert d == hashlib.sha256(f.read_bytes()).hexdigest()
    assert role_template_digest({KEY: str(tmp_path / "nope.md")}) == "!missing"
    assert role_template_digest({KEY: 5}) == "!bad_value_type"


_AC21_CASES = [
    ("frontmatter_not_allowed",
     "---\nk: v\n---\n## project-conventions\n```\ncode\n"),
    ("forbidden_markup",
     "## project-conventions\none <!-- x -->\n\n## project-conventions\ntwo\n"),
]


@pytest.mark.parametrize("first,body", _AC21_CASES, ids=[c[0] for c in _AC21_CASES])
def test_ac21_two_violations_first_in_order_plus_one_more(first, body, tmp_path):
    """AC21: two violations -> the first in documented order is the reason; "; +1 more"."""
    exc = _err(_tpl(tmp_path, body))
    assert exc.reason == first
    msg = str(exc)
    assert "; +1 more" in msg
    assert "\n" not in msg


def test_ac22_empty_slug_title_rejected(tmp_path):
    """AC22: `## !!!` (empty slug) -> invalid_section_title."""
    assert _err(_tpl(tmp_path, "## !!!\nbody\n")).reason == "invalid_section_title"


def test_ac23_core_without_overridable_metadata_is_unavailable(tmp_path, monkeypatch):
    """AC23: tmp plugin whose core has no `overridable` metadata -> core_unavailable."""
    plugin = tmp_path / "plugin_nometa"
    _write(
        plugin / "skills" / "bytedigger" / "SKILL.md",
        "---\nname: ByteDigger\n---\n\n# ByteDigger\n\n## project-conventions\n\ncore text\n",
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    f = _tpl(tmp_path, "## project-conventions\nbody\n")
    assert _err(f).reason == "core_unavailable"


_AC24_CASES = [
    ("heading_level_invalid", "# Read-only role\nbody text\n"),
    ("frontmatter_not_allowed", "---\nk: v\n---\nbody text\n"),
    ("forbidden_markup", "body text <!-- x -->\n"),
]


@pytest.mark.parametrize("reason,body", _AC24_CASES, ids=[c[0] for c in _AC24_CASES])
def test_ac24_plain_text_is_not_exempt_from_structural_rules(reason, body, tmp_path):
    """AC24: plain text (no H2) with a leading H1 / `---` / `<!-- -->` is rejected."""
    assert _err(_tpl(tmp_path, body)).reason == reason


def test_ac20_reason_tokens_old_then_new_in_order():
    """AC20: the 9 old tokens then the 13 new ones, in order."""
    assert _rt().ROLE_TEMPLATE_REASONS == OLD_REASONS + NEW_REASONS
