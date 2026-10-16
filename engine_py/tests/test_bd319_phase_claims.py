"""RED tests for bd#319 -- phases/*.md claims are rendered from code (generator phase_claims).

Spec: docs/decisions/2026-10-16-bd319-phase-docs-generated-claims.md (section 3, AC1-AC9).
AC10 is a green-today pin (bd#89 / bd#190 tests) and has no test here.

AC -> test map
  AC1 test_ac1_*  load_claims on the repo: thresholds, model, tier policy literal
  AC2 test_ac2_*  real docs carry exactly one BEGIN/END per id and render_doc reproduces them
  AC3 test_ac3_*  FEATURE threshold 86 in a copy: --check names the two threshold blocks only
  AC4 test_ac4_*  COMPLEX evaluators 5 in a copy: --check names the three blocks, --write renders 5
  AC5 test_ac5_*  hand edit inside a block fails --check, outside does not
  AC6 test_ac6_*  prose guard over real phases/*.md outside generated blocks + negative case
  AC7 test_ac7_*  unknown id / unbalanced / absent registered block: --check exit 1 naming it
  AC8 test_ac8_*  load_claims raises PhaseClaimsError, no default substituted
  AC9 test_ac9_*  engine derives COMPLEX n from SATISFACTION_TIER_POLICY (monkeypatched to 2)
  adversarial: test_adv_*

Layout assumption for tmp copies: the real relative layout from the repo root
(bytedigger.json, phases/*.md, engine_py/bytedigger_engine/workflows/phase_6_review.py).
phase_claims is imported inside tests so each test fails individually (no collection error).
The CLI runs as a subprocess with PYTHONPATH=<repo>/engine_py (no sys.path mutation; 1q).
No singleton-resource or timing fixtures (workflows.md 1i).
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.workflows import phase_6_review as p6

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO = ENGINE_ROOT.parent
P6_REL = "engine_py/bytedigger_engine/workflows/phase_6_review.py"
DOC6 = "phases/phase-6-review.md"
DOC0 = "phases/phase-0-classify.md"
BLOCK_DOC = {
    "phase6-tier-policy": DOC6,
    "phase6-satisfaction-steps": DOC6,
    "phase0-satisfaction-row": DOC0,
}
TIERS = ("SIMPLE", "FEATURE", "COMPLEX")


# --- helpers -------------------------------------------------------------------


def _pc():
    from bytedigger_engine import phase_claims
    return phase_claims


def _cli(root: Path, mode: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ENGINE_ROOT))
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.phase_claims", mode, "--repo-root", str(root)],
        capture_output=True, text=True, env=env, timeout=120,
    )


def _copy_repo(dst: Path) -> Path:
    for rel in ("bytedigger.json", P6_REL, DOC6, DOC0):
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst / rel)
    return dst


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _block_span(text: str, bid: str):
    m = re.search(
        rf"<!-- BEGIN GENERATED {re.escape(bid)} -->\n(.*?)<!-- END GENERATED {re.escape(bid)} -->",
        text, re.S,
    )
    return m


def _literal_policy(p6_text: str) -> dict:
    for node in ast.parse(p6_text).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SATISFACTION_TIER_POLICY" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("SATISFACTION_TIER_POLICY literal not found in phase_6_review.py")


def _pick(claims: dict, *keys):
    for k in keys:
        if k in claims:
            return claims[k]
    raise AssertionError(f"claims has none of {keys}; keys={sorted(claims)}")


MIN_POLICY = (
    "SATISFACTION_TIER_POLICY = {\n"
    + "".join(
        f'    "{t}": {{"reviewers": 1, "evaluators": {3 if t == "COMPLEX" else 1}, '
        f'"dimensions": ("A", "B", "C")}},\n' for t in TIERS
    )
    + "}\n"
)


def _min_repo(root: Path, thresholds=None, policy_src: str | None = MIN_POLICY, with_json=True) -> Path:
    if with_json:
        data = {
            "validation_model": "opus",
            "satisfaction_thresholds": thresholds if thresholds is not None
            else {"SIMPLE": 80, "FEATURE": 85, "COMPLEX": 90},
        }
        (root / "bytedigger.json").write_text(json.dumps(data), encoding="utf-8")
    p6f = root / P6_REL
    p6f.parent.mkdir(parents=True, exist_ok=True)
    p6f.write_text(policy_src if policy_src is not None else "X = 1\n", encoding="utf-8")
    return root


# --- AC1 -----------------------------------------------------------------------


def test_ac1_load_claims_on_repo_matches_sources():
    claims = _pc().load_claims(REPO)
    thr = _pick(claims, "satisfaction_thresholds", "thresholds")
    assert dict(thr) == {"SIMPLE": 80, "FEATURE": 85, "COMPLEX": 90}
    assert _pick(claims, "validation_model", "model") == "opus"
    expected = _literal_policy(_read(REPO, P6_REL))
    got = _pick(claims, "tier_policy", "SATISFACTION_TIER_POLICY", "policy")
    for t in TIERS:
        for k in ("reviewers", "evaluators"):
            assert got[t][k] == expected[t][k]
        assert tuple(got[t]["dimensions"]) == tuple(expected[t]["dimensions"])
    assert expected["COMPLEX"]["evaluators"] == 3 and len(expected["COMPLEX"]["dimensions"]) == 5


def test_ac1_min_repo_control_loads(tmp_path):
    """Positive control for AC8: a minimal valid tmp repo loads (raise-always stubs cannot pass AC8)."""
    claims = _pc().load_claims(_min_repo(tmp_path))
    assert _pick(claims, "validation_model", "model") == "opus"


# --- AC2 -----------------------------------------------------------------------


@pytest.mark.parametrize("bid", sorted(BLOCK_DOC))
def test_ac2_each_id_occurs_once_between_one_begin_and_end(bid):
    text = _read(REPO, BLOCK_DOC[bid])
    assert len(re.findall(rf"^<!-- BEGIN GENERATED {re.escape(bid)} -->\r?$", text, re.M)) == 1
    assert len(re.findall(rf"^<!-- END GENERATED {re.escape(bid)} -->\r?$", text, re.M)) == 1
    assert _block_span(text, bid) is not None


@pytest.mark.parametrize("rel", [DOC6, DOC0])
def test_ac2_render_doc_reproduces_real_docs_byte_for_byte(rel):
    pc = _pc()
    claims = pc.load_claims(REPO)
    text = _read(REPO, rel)
    assert pc.render_doc(text, claims) == text


def test_ac2_check_cli_clean_on_real_repo():
    assert set(_pc().BLOCKS) == set(BLOCK_DOC)
    r = _cli(REPO, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


# --- AC3 -----------------------------------------------------------------------


def test_ac3_feature_threshold_86_names_stale_blocks(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    cfg = json.loads(_read(root, "bytedigger.json"))
    cfg["satisfaction_thresholds"]["FEATURE"] = 86
    (root / "bytedigger.json").write_text(json.dumps(cfg), encoding="utf-8")
    r = _cli(root, "--check")
    assert r.returncode == 1
    assert "phase6-tier-policy" in r.stdout and "phase6-satisfaction-steps" in r.stdout
    assert "phase0-satisfaction-row" not in r.stdout


def test_ac3_write_then_check_passes_and_table_has_86(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    cfg = json.loads(_read(root, "bytedigger.json"))
    cfg["satisfaction_thresholds"]["FEATURE"] = 86
    (root / "bytedigger.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert _cli(root, "--write").returncode == 0
    r = _cli(root, "--check")
    assert r.returncode == 0, r.stdout + r.stderr
    span = _block_span(_read(root, DOC6), "phase6-tier-policy")
    assert span is not None and ">=86%" in span.group(1)


# --- AC4 -----------------------------------------------------------------------


def _set_complex_evaluators(root: Path, n: int) -> None:
    p = root / P6_REL
    src = p.read_text(encoding="utf-8")
    new, cnt = re.subn(r'("COMPLEX"\s*:\s*\{[^}]*?"evaluators"\s*:\s*)\d+', rf"\g<1>{n}", src, count=1)
    assert cnt == 1, "SATISFACTION_TIER_POLICY COMPLEX evaluators literal not found"
    p.write_text(new, encoding="utf-8")


def test_ac4_complex_evaluators_5_names_three_blocks(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    _set_complex_evaluators(root, 5)
    r = _cli(root, "--check")
    assert r.returncode == 1
    for bid in BLOCK_DOC:
        assert bid in r.stdout


def test_ac4_write_renders_five_evaluators(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    _set_complex_evaluators(root, 5)
    assert _cli(root, "--write").returncode == 0
    assert "Launch 5 Opus agents in parallel" in _read(root, DOC6)
    assert "5x Opus voting" in _read(root, DOC0)
    assert _cli(root, "--check").returncode == 0
    blanked = _GEN.sub(lambda m: "\n" * m.group(0).count("\n"), _read(root, DOC6))
    bad = [ln for ln in blanked.split("\n") if re.search(r"Evaluator [A-Z]\b|different focus", ln)]
    assert bad == []


def test_ac8b_check_stdout_one_block_id_per_line(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    _set_complex_evaluators(root, 5)
    r = _cli(root, "--check")
    assert r.returncode == 1
    lines = [ln for ln in r.stdout.split("\n") if ln.strip()]
    for bid in BLOCK_DOC:
        assert sum(bid in ln for ln in lines) == 1
    assert all(sum(b in ln for b in BLOCK_DOC) == 1 for ln in lines)


def test_ac8b_policy_error_exits_2_stderr_and_writes_nothing(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    assert _block_span(_read(root, DOC6), "phase6-tier-policy") is not None, "doc has no markers yet"
    p = root / P6_REL
    p.write_text("OTHER = 1\n", encoding="utf-8")
    before = {rel: (root / rel).read_bytes() for rel in (DOC6, DOC0, P6_REL, "bytedigger.json")}
    for mode in ("--write", "--check"):
        r = _cli(root, mode)
        assert r.returncode == 2, (mode, r.stdout, r.stderr)
        assert "SATISFACTION_TIER_POLICY" in r.stderr
    assert {rel: (root / rel).read_bytes() for rel in before} == before


@pytest.mark.parametrize("kind", ["unbalanced", "nested", "duplicated"])
def test_ac8b_write_on_malformed_markers_exits_2_docs_unchanged(tmp_path, kind):
    root = _prep_marked(tmp_path)
    t = _read(root, DOC6)
    if kind == "unbalanced":
        t = t.replace("<!-- END GENERATED phase6-tier-policy -->\n", "", 1)
    elif kind == "nested":
        t = t.replace(
            "<!-- BEGIN GENERATED phase6-tier-policy -->\n",
            "<!-- BEGIN GENERATED phase6-tier-policy -->\n<!-- BEGIN GENERATED phase6-satisfaction-steps -->\n", 1)
    else:
        m = _block_span(t, "phase6-tier-policy")
        t = t + "\n" + m.group(0) + "\n"
    (root / DOC6).write_text(t, encoding="utf-8")
    before = {rel: (root / rel).read_bytes() for rel in (DOC6, DOC0)}
    r = _cli(root, "--write")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert r.stderr.strip()
    assert {rel: (root / rel).read_bytes() for rel in before} == before


# --- AC5 -----------------------------------------------------------------------


def test_ac5_hand_edit_inside_block_fails_check(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    text = _read(root, DOC6)
    m = _block_span(text, "phase6-tier-policy")
    assert m is not None, "no generated block in phase-6 doc"
    inner = m.group(1)
    assert "80" in inner
    (root / DOC6).write_text(text.replace(inner, inner.replace("80", "81", 1), 1), encoding="utf-8")
    r = _cli(root, "--check")
    assert r.returncode == 1 and "phase6-tier-policy" in r.stdout


def test_ac5_edit_outside_blocks_does_not_fail_check(tmp_path):
    _pc()
    root = _copy_repo(tmp_path)
    assert _block_span(_read(root, DOC6), "phase6-tier-policy") is not None
    with open(root / DOC6, "a", encoding="utf-8") as f:
        f.write("\nSome new prose without claims.\n")
    r = _cli(root, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


# --- AC6 -----------------------------------------------------------------------

GUARD_PATTERNS = [
    re.compile(r">=\s*(80|85|90)\s*%"),
    re.compile(r"\b\d+\s*[x×]\s*(opus|sonnet|haiku)\b", re.I),
    re.compile(r"\b\d+-(agent|opus)\b"),
    re.compile(r"\b\d+/\d+\s+PASS\b"),
    re.compile(r"median of \d"),
    re.compile(r"launch \d+ ", re.I),
    re.compile(r"\b\d+ (composite |parallel )?(reviewers?|evaluators?|dimensions?)\b"),
]
_GEN = re.compile(r"<!-- BEGIN GENERATED [\w-]+ -->.*?<!-- END GENERATED [\w-]+ -->", re.S)


def scan_prose(name: str, text: str) -> list[tuple[str, int, str]]:
    """Return (file, line_no, line) for each claim-shaped line outside generated blocks."""
    blanked = _GEN.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    hits = []
    for i, line in enumerate(blanked.split("\n"), 1):
        if any(p.search(line) for p in GUARD_PATTERNS):
            hits.append((name, i, line))
    return hits


def test_ac6_no_typed_claims_outside_generated_blocks():
    files = sorted((REPO / "phases").glob("*.md"))
    names = {f.name for f in files}
    assert files, "no phases/*.md files found to scan"
    assert {"phase-6-review.md", "phase-0-classify.md"} <= names
    hits = []
    scanned_lines = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        scanned_lines += sum(1 for ln in text.split("\n") if ln.strip())
        hits += scan_prose(f.name, text)
    assert scanned_lines > 0, "scan saw no non-blank lines"
    assert hits == [], "typed claims outside generated blocks:\n" + "\n".join(map(str, hits))


def test_ac6_composite_reviewer_placeholder_gone():
    assert "[1 composite reviewer]" not in _read(REPO, DOC0)


def test_ac6_guard_reports_file_and_line_on_tmp_copy(tmp_path):
    _pc().load_claims(REPO)
    real = _read(REPO, DOC6)
    assert _block_span(real, "phase6-tier-policy") is not None, "real doc has no generated markers"
    assert scan_prose("real.md", real) == []
    extra = "Launch 3 Opus agents"
    doc = tmp_path / "phase-x.md"
    doc.write_text(real.rstrip("\n") + "\n" + extra + "\n", encoding="utf-8")
    text = doc.read_text(encoding="utf-8")
    expected_line = text.split("\n").index(extra) + 1
    hits = scan_prose(doc.name, text)
    assert [(h[0], h[1]) for h in hits] == [("phase-x.md", expected_line)]


# --- AC7 -----------------------------------------------------------------------


def _prep_marked(tmp_path: Path) -> Path:
    _pc()
    root = _copy_repo(tmp_path)
    assert _block_span(_read(root, DOC6), "phase6-tier-policy") is not None, "doc has no markers yet"
    return root


def test_ac7_unknown_block_id_fails_check(tmp_path):
    root = _prep_marked(tmp_path)
    with open(root / DOC6, "a", encoding="utf-8") as f:
        f.write("\n<!-- BEGIN GENERATED bogus-id -->\nx\n<!-- END GENERATED bogus-id -->\n")
    r = _cli(root, "--check")
    assert r.returncode == 1 and "bogus-id" in r.stdout


def test_ac7_unbalanced_begin_end_fails_check(tmp_path):
    root = _prep_marked(tmp_path)
    t = _read(root, DOC6).replace("<!-- END GENERATED phase6-tier-policy -->\n", "", 1)
    (root / DOC6).write_text(t, encoding="utf-8")
    r = _cli(root, "--check")
    assert r.returncode == 1 and "phase6-tier-policy" in r.stdout


def test_ac7_registered_block_absent_from_doc_fails_check(tmp_path):
    root = _prep_marked(tmp_path)
    t = _read(root, DOC0)
    m = re.search(
        r"<!-- BEGIN GENERATED phase0-satisfaction-row -->.*?<!-- END GENERATED phase0-satisfaction-row -->\n?",
        t, re.S,
    )
    assert m is not None
    (root / DOC0).write_text(t.replace(m.group(0), "", 1), encoding="utf-8")
    r = _cli(root, "--check")
    assert r.returncode == 1 and "phase0-satisfaction-row" in r.stdout


# --- adversarial markers / render ----------------------------------------------


def test_adv_nested_begin_fails_check(tmp_path):
    root = _prep_marked(tmp_path)
    t = _read(root, DOC6).replace(
        "<!-- BEGIN GENERATED phase6-tier-policy -->\n",
        "<!-- BEGIN GENERATED phase6-tier-policy -->\n<!-- BEGIN GENERATED phase6-satisfaction-steps -->\n", 1)
    (root / DOC6).write_text(t, encoding="utf-8")
    r = _cli(root, "--check")
    assert r.returncode == 1
    assert "phase6-satisfaction-steps" in r.stdout


def test_adv_end_before_begin_fails_check(tmp_path):
    root = _prep_marked(tmp_path)
    t = _read(root, DOC0)
    b, e = ("<!-- BEGIN GENERATED phase0-satisfaction-row -->", "<!-- END GENERATED phase0-satisfaction-row -->")
    t = t.replace(b, "@@B@@", 1).replace(e, b, 1).replace("@@B@@", e, 1)
    (root / DOC0).write_text(t, encoding="utf-8")
    r = _cli(root, "--check")
    assert r.returncode == 1 and "phase0-satisfaction-row" in r.stdout


def test_adv_duplicated_block_id_fails_check(tmp_path):
    root = _prep_marked(tmp_path)
    t = _read(root, DOC0)
    m = re.search(
        r"<!-- BEGIN GENERATED phase0-satisfaction-row -->.*?<!-- END GENERATED phase0-satisfaction-row -->\n",
        t, re.S)
    assert m is not None
    (root / DOC0).write_text(t + "\n" + m.group(0), encoding="utf-8")
    r = _cli(root, "--check")
    assert r.returncode == 1 and "phase0-satisfaction-row" in r.stdout


def test_adv_render_doc_idempotent_applied_twice():
    pc = _pc()
    claims = pc.load_claims(REPO)
    text = _read(REPO, DOC6)
    once = pc.render_doc(text, claims)
    assert pc.render_doc(once, claims) == once
    assert "BEGIN GENERATED phase6-tier-policy" in once


def test_adv_crlf_doc_renders_stably_and_keeps_markers():
    pc = _pc()
    claims = pc.load_claims(REPO)
    crlf = _read(REPO, DOC6).replace("\n", "\r\n")
    once = pc.render_doc(crlf, claims)
    assert "BEGIN GENERATED phase6-tier-policy" in once
    assert pc.render_doc(once, claims) == once


# --- AC8 -----------------------------------------------------------------------


def test_ac8_missing_bytedigger_json(tmp_path):
    pc = _pc()
    with pytest.raises(pc.PhaseClaimsError):
        pc.load_claims(_min_repo(tmp_path, with_json=False))


def test_ac8_threshold_missing_a_tier(tmp_path):
    pc = _pc()
    with pytest.raises(pc.PhaseClaimsError):
        pc.load_claims(_min_repo(tmp_path, thresholds={"SIMPLE": 80, "FEATURE": 85}))


@pytest.mark.parametrize("bad", ["90", True, 90.0, None])
def test_ac8_non_int_threshold(tmp_path, bad):
    pc = _pc()
    with pytest.raises(pc.PhaseClaimsError):
        pc.load_claims(_min_repo(tmp_path, thresholds={"SIMPLE": 80, "FEATURE": 85, "COMPLEX": bad}))


def test_ac8_policy_absent(tmp_path):
    pc = _pc()
    with pytest.raises(pc.PhaseClaimsError):
        pc.load_claims(_min_repo(tmp_path, policy_src="OTHER = {}\n"))


@pytest.mark.parametrize("src", [
    "SATISFACTION_TIER_POLICY = _build()\n",
    "_P = {}\nSATISFACTION_TIER_POLICY = _P\n",
])
def test_ac8_non_literal_policy(tmp_path, src):
    pc = _pc()
    with pytest.raises(pc.PhaseClaimsError):
        pc.load_claims(_min_repo(tmp_path, policy_src=src))


def test_ac8_tier_lacking_evaluators(tmp_path):
    pc = _pc()
    src = MIN_POLICY.replace('"evaluators": 3, ', "", 1)
    assert src != MIN_POLICY
    with pytest.raises(pc.PhaseClaimsError):
        pc.load_claims(_min_repo(tmp_path, policy_src=src))


def test_ac8_tier_missing_from_policy(tmp_path):
    pc = _pc()
    src = "\n".join(ln for ln in MIN_POLICY.split("\n") if '"FEATURE"' not in ln)
    with pytest.raises(pc.PhaseClaimsError):
        pc.load_claims(_min_repo(tmp_path, policy_src=src))


# --- AC9 -----------------------------------------------------------------------


def _satisfaction_calls(tmp_path: Path, monkeypatch, complexity: str) -> int:
    scratch = tmp_path / "scratch"
    (scratch / "reviews").mkdir(parents=True, exist_ok=True)
    calls: list = []

    def _stub(*args, **kwargs) -> StepResult:
        calls.append(kwargs)
        return StepResult(
            status="ok",
            data={"raw_response": "SCORE: 95\nVERDICT: PASS\n"},
            duration_ms=0, step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(p6, "invoke_llm_subprocess", _stub)
    policy = copy.deepcopy(p6.SATISFACTION_TIER_POLICY) if hasattr(p6, "SATISFACTION_TIER_POLICY") else None
    assert policy is not None, "SATISFACTION_TIER_POLICY missing in phase_6_review"
    policy["COMPLEX"]["evaluators"] = 2
    monkeypatch.setattr(p6, "SATISFACTION_TIER_POLICY", policy)
    telemetry_ctx.clear_current_run()
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "complexity": complexity},
        question="q", session_id="bd319", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={
            "prompt": "evaluate",
            "doc_path": str(scratch / "reviews" / "build-satisfaction.md"),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(scratch / "reviews" / "build-review.md"),
            "fix_doc_path": str(scratch / "reviews" / "build-fix.md"),
        },
        duration_ms=0, step_name="build_satisfaction_prompt",
    )
    p6._invoke_satisfaction_llm(ctx, prev)
    return len(calls)


def test_ac9_complex_dispatch_count_follows_policy_literal(tmp_path, monkeypatch):
    assert _satisfaction_calls(tmp_path, monkeypatch, "COMPLEX") == 2


@pytest.mark.parametrize("tier", ["SIMPLE", "FEATURE"])
def test_ac9_simple_and_feature_still_dispatch_one(tmp_path, monkeypatch, tier):
    assert _satisfaction_calls(tmp_path, monkeypatch, tier) == 1
