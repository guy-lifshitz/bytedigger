"""RED tests for bd#324 -- evaluator-aggregation prose lives inside the generated block.

phases/phase-6-review.md Step 3 items 2-6 (majority vote / median / split decisions)
were hand-written outside the `phase6-satisfaction-steps` block and still described
multi-evaluator voting although SATISFACTION_TIER_POLICY has evaluators=1 everywhere.
The block must render the numbered aggregation steps from the policy (max evaluators).

Test -> requirement
  test_no_voting_prose_outside_generated_blocks   real doc: no majority vote/median/split decisions outside blocks
  test_block_renders_single_evaluator_wording     real doc block: "Launch 1 Opus agent", "no voting", no majority/median
  test_evaluators_3_write_renders_voting_steps    tmp copy, COMPLEX evaluators=3: --write renders voting, --check clean
  test_simple_only_evaluators_3_uses_max_rule     tmp copy, only SIMPLE=3: same voting wording (max rule)
  test_write_twice_is_idempotent                  all 1: block has exact line, 2nd --write byte-identical

The CLI runs as a subprocess with PYTHONPATH=<repo>/engine_py (no sys.path mutation; 1q).
No singleton-resource or timing fixtures (workflows.md 1i).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO = ENGINE_ROOT.parent
P6_REL = "engine_py/bytedigger_engine/workflows/phase_6_review.py"
DOC6 = "phases/phase-6-review.md"
DOC0 = "phases/phase-0-classify.md"
BID = "phase6-satisfaction-steps"
_GEN = re.compile(r"<!-- BEGIN GENERATED [\w-]+ -->.*?<!-- END GENERATED [\w-]+ -->", re.S)
_PROSE = re.compile(r"majority vote|median|split decisions", re.I)


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


def _block_inner(text: str, bid: str) -> str:
    m = re.search(
        rf"<!-- BEGIN GENERATED {re.escape(bid)} -->\n(.*?)<!-- END GENERATED {re.escape(bid)} -->",
        text, re.S,
    )
    assert m is not None, f"block {bid} not found"
    return m.group(1)


def _set_evaluators(root: Path, tier: str, n: int) -> None:
    p = root / P6_REL
    src = p.read_text(encoding="utf-8")
    new, cnt = re.subn(rf'("{tier}"\s*:\s*\{{[^}}]*?"evaluators"\s*:\s*)\d+', rf"\g<1>{n}", src, count=1)
    assert cnt == 1, f"SATISFACTION_TIER_POLICY {tier} evaluators literal not found"
    p.write_text(new, encoding="utf-8")


def _assert_voting_block(inner: str, n: int) -> None:
    lines = inner.split("\n")
    assert any(re.fullmatch(rf"\d+\. Launch {n} Opus agents in parallel\.", l) for l in lines), inner
    assert any(re.fullmatch(r"\d+\. Each returns: .*Top concerns.*", l) for l in lines), inner
    assert any(re.fullmatch(r"\d+\. Final verdict: Majority vote.*", l) for l in lines), inner
    assert any(re.fullmatch(r"\d+\. Final score: the median score.*", l) for l in lines), inner
    assert any(re.fullmatch(r"\d+\. Split decisions: .*", l) for l in lines), inner
    assert any(re.fullmatch(r"\d+\. Threshold: .*", l) for l in lines), inner
    assert "no voting" not in inner.lower()


def test_no_voting_prose_outside_generated_blocks():
    text = _read(REPO, DOC6)
    assert f"BEGIN GENERATED {BID}" in text
    blanked = _GEN.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    hits = [(i, ln) for i, ln in enumerate(blanked.split("\n"), 1) if _PROSE.search(ln)]
    scanned_lines = sum(1 for ln in blanked.split("\n") if ln.strip())
    assert scanned_lines > 0, "scan saw no non-blank lines"
    assert hits == [], "voting prose outside generated blocks:\n" + "\n".join(map(str, hits))


def test_block_renders_single_evaluator_wording():
    inner = _block_inner(_read(REPO, DOC6), BID)
    lines = inner.split("\n")
    assert "1. Launch 1 Opus agent." in lines
    assert any(l.startswith("2. It returns:") and "Top concerns" in l for l in lines)
    assert any(l.startswith("3. ") and "(no voting)" in l for l in lines)
    assert any(l.startswith("4. Threshold:") for l in lines)
    assert not re.search(r"majority|median", inner, re.I)


def test_evaluators_3_write_renders_voting_steps(tmp_path):
    root = _copy_repo(tmp_path)
    _set_evaluators(root, "COMPLEX", 3)
    assert _cli(root, "--write").returncode == 0
    _assert_voting_block(_block_inner(_read(root, DOC6), BID), 3)
    r = _cli(root, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_simple_only_evaluators_3_uses_max_rule(tmp_path):
    root = _copy_repo(tmp_path)
    _set_evaluators(root, "SIMPLE", 3)
    assert _cli(root, "--write").returncode == 0
    _assert_voting_block(_block_inner(_read(root, DOC6), BID), 3)
    r = _cli(root, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_write_twice_is_idempotent(tmp_path):
    root = _copy_repo(tmp_path)
    assert _cli(root, "--write").returncode == 0
    first = (root / DOC6).read_bytes()
    assert "1. Launch 1 Opus agent." in _block_inner(first.decode("utf-8"), BID).split("\n")
    assert _cli(root, "--write").returncode == 0
    assert (root / DOC6).read_bytes() == first
