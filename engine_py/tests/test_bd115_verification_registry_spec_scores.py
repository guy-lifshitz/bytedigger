"""RED tests for bd#115 - a registry of verifying skills (phase 5 + `verify`
command) and a scored spec review (`review.json`) in phase 4.5.

Spec: docs/decisions/2026-09-30-bd115-verification-registry-spec-scores.md (AC1-AC20).

Every test builds real temp git repos with real SKILL.md files and real commands
(`sys.executable -c ...`); the units under test are never mocked. New modules
(`verification_registry`, `spec_review_score`) and new attributes are imported
lazily inside test bodies, so collection works before GREEN and each test fails
on its own ImportError/AttributeError/assertion.

No sys.path manipulation: `bytedigger_engine` is importable through the
conftest-import-time singleton; the AC8 subprocess gets PYTHONPATH explicitly.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ENGINE = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE.parent

assert "'" not in sys.executable, "fixture quoting assumes no single quote in sys.executable"

AXES = ["completeness", "clarity", "feasibility", "issue_alignment", "consistency"]


# --------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = Path(os.path.realpath(str(tmp_path / name)))
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("readme\n")
    return repo


def _commit_all(repo: Path, msg: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _cmd(code: str, *args: str) -> str:
    """A verify_command value: python -c "<code>" args (code has no quotes)."""
    assert '"' not in code and "'" not in code
    tail = (" " + " ".join(args)) if args else ""
    return f"{shlex.quote(sys.executable)} -c \"{code}\"{tail}"


_TOUCH = "import sys; open(sys.argv[1], sys.argv[2]).close()"
_WRITE = "import sys; open(sys.argv[1], sys.argv[2]).write(sys.argv[3])"
_FAIL3 = "import sys; sys.stderr.write(sys.argv[1]); sys.exit(3)"
_OK = "import sys; sys.exit(0)"


def _skill(
    base: Path,
    reldir: str,
    *,
    name: str | None = None,
    verification: str | None = "true",
    command: str | None = None,
    raw: str | None = None,
) -> Path:
    """Write <base>/<reldir>/SKILL.md; returns its path."""
    d = base / reldir
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    if raw is not None:
        p.write_text(raw)
        return p
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    lines.append("description: a test skill")
    if verification is not None or command is not None:
        lines.append("metadata:")
        if verification is not None:
            lines.append(f"  verification: {verification}")
        if command is not None:
            lines.append(f"  verify_command: '{command}'")
    lines += ["---", "", "# body", ""]
    p.write_text("\n".join(lines))
    return p


def _g(obj: Any, key: str) -> Any:
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def _names(reg: Any) -> list[str]:
    return [_g(s, "name") for s in reg.skills]


def _by_name(report: dict) -> dict[str, dict]:
    return {s["name"]: s for s in report["skills"]}


def _run_registry(repo: Path, **kw: Any) -> dict:
    from bytedigger_engine import verification_registry as vr

    return vr.run_registry(repo, **kw)


# --------------------------------------------------------------------------
# op1 - discovery (AC1-AC3)
# --------------------------------------------------------------------------


def test_ac1_discover_registers_only_verification_true_sorted(tmp_path: Path) -> None:
    from bytedigger_engine import verification_registry as vr

    repo = _make_repo(tmp_path)
    _skill(repo, "skills/zeta", name="zeta", verification="true")
    _skill(repo, ".claude/skills/alpha", name="alpha", verification="True")
    _skill(repo, "extra/mid", name="mid", verification="TRUE")
    # not registered
    _skill(repo, "skills/off", name="off", verification="false")
    _skill(repo, "skills/yes", name="yes", verification='"yes"')
    _skill(repo, "skills/nometa", name="nometa", verification=None)
    _skill(repo, "skills/nofm", raw="# just a body, no frontmatter\n")
    # ordinary skill with constructs the subset parser does not model (F1)
    _skill(repo, "skills/ordinary", raw=(
        "---\n"
        "name: ordinary\n"
        "description: >\n"
        "  folded text that spans\n"
        "  two lines\n"
        "allowed-tools:\n"
        "  - Read\n"
        "  - Grep\n"
        "metadata: {tags: [a]}\n"
        "---\n\nbody\n"
    ))
    _commit_all(repo)

    reg = vr.discover(repo, extra_dirs=("extra",))
    assert _names(reg) == ["alpha", "mid", "zeta"]
    assert [_g(s, "path") for s in reg.skills] == [
        ".claude/skills/alpha/SKILL.md", "extra/mid/SKILL.md", "skills/zeta/SKILL.md",
    ]
    assert list(reg.errors) == [], f"ordinary skills must not produce errors: {reg.errors}"


def test_ac2_flow_style_verification_is_error_and_outside_repo_dir(tmp_path: Path) -> None:
    from bytedigger_engine import verification_registry as vr

    repo = _make_repo(tmp_path)
    _skill(repo, "skills/flow", raw=(
        "---\nname: flow\nmetadata: {verification: true}\n---\n\nbody\n"
    ))
    _commit_all(repo)
    # a directory next to the repo holding a verifying skill: must not be scanned
    outside = tmp_path / "x"
    _skill(outside, "evil", name="evil", verification="true")

    reg = vr.discover(repo, extra_dirs=("../x",))
    assert "flow" not in _names(reg) and "evil" not in _names(reg)
    rows = {(_g(e, "reason")): _g(e, "path") for e in reg.errors}
    assert "unsupported_frontmatter" in rows
    assert str(rows["unsupported_frontmatter"]).endswith("skills/flow/SKILL.md")
    assert "outside_repo" in rows
    assert "x" in str(rows["outside_repo"])


def test_ac3_kind_and_name_fallback(tmp_path: Path) -> None:
    from bytedigger_engine import verification_registry as vr

    repo = _make_repo(tmp_path)
    _skill(repo, "skills/cmdskill", name="cmdskill", command=_cmd(_OK))
    _skill(repo, "skills/emptycmd", name="emptycmd", raw=(
        "---\nname: emptycmd\nmetadata:\n  verification: true\n"
        "  verify_command: \"\"\n---\n\nbody\n"
    ))
    _skill(repo, "skills/nocmd", name="nocmd")
    _skill(repo, "skills/dirname-only")  # no `name:` -> directory name
    _commit_all(repo)

    reg = vr.discover(repo)
    kinds = {_g(s, "name"): _g(s, "kind") for s in reg.skills}
    assert kinds == {
        "cmdskill": "command", "emptycmd": "agent", "nocmd": "agent", "dirname-only": "agent",
    }


# --------------------------------------------------------------------------
# op1 - run_registry (AC4-AC7)
# --------------------------------------------------------------------------


def test_ac4_pass_and_fail_statuses(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _skill(repo, "skills/good", name="good", command=_cmd(_OK))
    _skill(repo, "skills/bad", name="bad", command=_cmd(_FAIL3, "BOOMMARK"))
    _commit_all(repo)

    report = _run_registry(repo, timeout_sec=60)
    sk = _by_name(report)
    assert sk["good"]["status"] == "pass" and sk["good"]["exit_code"] == 0
    assert sk["bad"]["status"] == "fail" and sk["bad"]["exit_code"] == 3
    assert "BOOMMARK" in sk["bad"]["output_tail"]
    assert report["ok"] is False
    assert report["schema"] == 1
    assert report["summary"] == {"total": 2, "pass": 1, "fail": 1, "agent": 0}


def test_ac5_mutation_detected_via_tree_snapshot(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "tracked.txt").write_text("orig\n")
    _skill(repo, "skills/maker", name="maker", command=_cmd(_TOUCH, "created.txt", "w"))
    _skill(repo, "skills/rewriter", name="rewriter",
           command=_cmd(_WRITE, "tracked.txt", "w", "second"))
    _commit_all(repo)
    # GREEN-style uncommitted edit that the rewriter will rewrite AGAIN
    (repo / "tracked.txt").write_text("first\n")
    index_before = (repo / ".git" / "index").read_bytes()

    report = _run_registry(repo, timeout_sec=60)
    sk = _by_name(report)
    assert sk["maker"]["status"] == "mutated"
    assert sk["rewriter"]["status"] == "mutated"
    assert report["ok"] is False
    assert (repo / ".git" / "index").read_bytes() == index_before, "real index must not be touched"


def test_ac5_not_a_git_repo_runs_nothing(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    marker = tmp_path / "ran.marker"
    _skill(plain, "skills/m", name="m", command=_cmd(_TOUCH, str(marker), "w"))

    report = _run_registry(plain, timeout_sec=60)
    assert {"path": ".", "reason": "not_a_git_repo"} in report["errors"]
    assert not marker.exists(), "no command may run outside a git work tree"
    assert report["ok"] is False
    assert _by_name(report)["m"]["status"] == "error"


def test_ac6_timeout_sleeping_command(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _skill(repo, "skills/slow", name="slow", command=_cmd("import time; time.sleep(30)"))
    _commit_all(repo)

    t0 = time.monotonic()
    report = _run_registry(repo, timeout_sec=1)
    assert _by_name(report)["slow"]["status"] == "timeout"
    assert time.monotonic() - t0 < 10
    assert report["ok"] is False


def test_ac6_timeout_with_grandchild_holding_stdout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _skill(repo, "skills/gc", name="gc", raw=(
        "---\nname: gc\nmetadata:\n  verification: true\n"
        "  verify_command: 'sh -c \"sleep 30 & sleep 30\"'\n---\n\nbody\n"
    ))
    _commit_all(repo)

    t0 = time.monotonic()
    report = _run_registry(repo, timeout_sec=1)
    elapsed = time.monotonic() - t0
    assert _by_name(report)["gc"]["status"] == "timeout"
    assert elapsed < 10, f"a grandchild holding the pipe must not hang the wait ({elapsed:.1f}s)"


def test_ac6_start_errors_do_not_raise(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _skill(repo, "skills/nobin", name="nobin", command="definitely-not-a-binary-bd115 --x")
    _skill(repo, "skills/unbal", name="unbal", raw=(
        "---\nname: unbal\nmetadata:\n  verification: true\n"
        "  verify_command: \"a 'b\"\n---\n\nbody\n"
    ))
    _commit_all(repo)

    report = _run_registry(repo, timeout_sec=30)
    sk = _by_name(report)
    assert sk["nobin"]["status"] == "error"
    assert sk["unbal"]["status"] == "error"
    assert report["ok"] is False


def test_ac7_agent_skills_and_list_mode(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    marker = repo / "should-not-exist.txt"
    _skill(repo, "skills/agentic", name="agentic")  # no verify_command -> agent
    _skill(repo, "skills/maker", name="maker", command=_cmd(_TOUCH, marker.name, "w"))
    _commit_all(repo)

    listed = _run_registry(repo, execute=False)
    assert {s["status"] for s in listed["skills"]} == {"listed"}
    assert not marker.exists(), "execute=False must not run any command"

    # real run: drop the mutating skill so only the agent skill remains
    _git(repo, "rm", "-q", "-r", "skills/maker")
    _git(repo, "commit", "-q", "-m", "drop maker")
    report = _run_registry(repo, timeout_sec=30)
    sk = _by_name(report)
    assert sk["agentic"]["status"] == "agent"
    assert report["summary"]["fail"] == 0 and report["summary"]["agent"] == 1
    assert report["ok"] is True


# --------------------------------------------------------------------------
# op1 - `verify` command (AC8)
# --------------------------------------------------------------------------


def _verify_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.run", "verify", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
    )


def test_ac8_verify_cli_exit_codes_stdout_and_clean_tree(tmp_path: Path) -> None:
    ok_repo = _make_repo(tmp_path, "okrepo")
    _skill(ok_repo, "skills/good", name="good", command=_cmd(_OK))
    _commit_all(ok_repo)
    before = _git(ok_repo, "status", "--porcelain=v1", "--untracked-files=all")

    proc = _verify_cli(tmp_path, "--repo", str(ok_repo))
    assert proc.returncode == 0, proc.stderr[-1500:]
    report = json.loads(proc.stdout)
    assert report["ok"] is True and _by_name(report)["good"]["status"] == "pass"
    assert _git(ok_repo, "status", "--porcelain=v1", "--untracked-files=all") == before

    bad_repo = _make_repo(tmp_path, "badrepo")
    _skill(bad_repo, "skills/bad", name="bad", command=_cmd(_FAIL3, "X"))
    _commit_all(bad_repo)
    proc = _verify_cli(tmp_path, "--repo", str(bad_repo))
    assert proc.returncode == 1, proc.stderr[-1500:]
    assert json.loads(proc.stdout)["ok"] is False


def test_ac8_verify_cli_extra_dir_registers_more_skills(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _skill(repo, "extra/gamma", name="gamma", command=_cmd(_OK))
    _commit_all(repo)

    without = _verify_cli(tmp_path, "--repo", str(repo), "--list")
    assert without.returncode == 0, without.stderr[-1500:]
    assert "gamma" not in _by_name(json.loads(without.stdout))

    with_extra = _verify_cli(tmp_path, "--repo", str(repo), "--list", "--extra-dir", "extra")
    assert with_extra.returncode == 0, with_extra.stderr[-1500:]
    listed = _by_name(json.loads(with_extra.stdout))
    assert listed["gamma"]["status"] == "listed"


# --------------------------------------------------------------------------
# op2 - phase 5 step (AC9-AC11, AC18)
# --------------------------------------------------------------------------


def _p5(repo: Path, scratch: Path, prev_extra: dict | None = None, **cfg: Any):
    from bytedigger_engine.contracts import StepResult, WorkflowContext

    org = {"git_cwd": str(repo), "scratchpad_dir": str(scratch), "build_class": "SIMPLE"}
    org.update(cfg)
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org, question="q",
        session_id="test-bd115", persona="hal", framework=None, domain=None,
    )
    data: dict[str, Any] = {"git_cwd": str(repo), "cycle": 1, "build_class": "SIMPLE"}
    data.update(prev_extra or {})
    prev = StepResult(status="ok", data=data, duration_ms=0, step_name="verify_green_typecheck")
    return ctx, prev


def test_ac9_step_registered_between_typecheck_and_commit() -> None:
    from bytedigger_engine.workflows import phase_5_implement as p5

    names = [s.name for s in p5.phase_5_implement_workflow().steps]
    i = names.index("verify_green_typecheck")
    assert names[i + 1] == "verify_registered_skills"
    assert names[i + 2] == "commit_green_code"


def test_ac10_failing_skill_stops_phase_and_writes_report(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_5_implement as p5

    repo = _make_repo(tmp_path)
    _skill(repo, "skills/failing-check", name="failing-check", command=_cmd(_FAIL3, "NOPE"))
    _commit_all(repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ctx, prev = _p5(repo, scratch, verification_timeout_sec=30)

    result = p5._verify_registered_skills(ctx, prev)
    assert result.status == "error"
    assert result.error_code == "E_VERIFICATION_SKILL_FAILED"
    assert result.recoverable is False
    assert "failing-check" in (result.error or "")
    report = json.loads((scratch / "reviews" / "verification-report.json").read_text())
    assert report["ok"] is False
    assert _by_name(report)["failing-check"]["status"] == "fail"


def test_ac11_empty_registry_is_ok_and_forwards_data(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_5_implement as p5

    repo = _make_repo(tmp_path)
    _skill(repo, "skills/ordinary", name="ordinary", verification=None)  # no metadata
    _commit_all(repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    extra = {"red_commit_sha": "a" * 40, "custom_key": {"n": 7}}
    ctx, prev = _p5(repo, scratch, prev_extra=extra)

    result = p5._verify_registered_skills(ctx, prev)
    assert result.status == "ok", result.error
    for k, v in prev.data.items():
        assert result.data[k] == v, f"prev.data key {k!r} must be forwarded unchanged"
    report_file = scratch / "reviews" / "verification-report.json"
    assert result.data["verification_report_path"]
    assert Path(result.data["verification_report_path"]).resolve() == report_file.resolve()
    assert json.loads(report_file.read_text())["summary"]["total"] == 0


def test_ac18_tampered_registry_skill_blocks_before_running(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_5_implement as p5

    repo = _make_repo(tmp_path)
    marker = tmp_path / "ran.marker"
    skill = _skill(repo, "skills/guard", name="guard", command=_cmd(_TOUCH, str(marker), "w"))
    _commit_all(repo)
    # GREEN flips its own check off (uncommitted)
    text = skill.read_text().replace("verification: true", "verification: false")
    skill.write_text(text)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ctx, prev = _p5(repo, scratch)

    result = p5._verify_registered_skills(ctx, prev)
    assert result.status == "error"
    assert result.error_code == "E_VERIFICATION_REGISTRY_TAMPERED"
    assert result.recoverable is False
    assert "skills/guard/SKILL.md" in (result.error or "")
    assert not marker.exists(), "no verify command may run when the registry was tampered with"


# --------------------------------------------------------------------------
# op4 - scores (AC12-AC16, AC19)
# --------------------------------------------------------------------------


def _scores_block(**over: Any) -> str:
    vals: dict[str, Any] = {a: 4 for a in AXES}
    vals.update(over)
    return "## Scores\n```json\n" + json.dumps(vals) + "\n```\n"


def test_ac12_parse_scores_ok_absent_invalid() -> None:
    from bytedigger_engine import spec_review_score as srs

    # ok: keys come back in axis order even when the reviewer shuffled them
    shuffled = {a: i + 1 for i, a in enumerate(reversed(AXES))}
    ok = srs.parse_scores("## Verdict\nSHIP\n\n## Scores\n```json\n" + json.dumps(shuffled) + "\n```\n")
    assert ok.status == "ok"
    assert list(ok.scores) == AXES
    assert ok.scores == {a: shuffled[a] for a in AXES}

    # absent: no heading; heading without a block; a json block only AFTER the next ## heading
    assert srs.parse_scores("## Verdict\nSHIP\n").status == "absent"
    assert srs.parse_scores("## Scores\nnone\n").status == "absent"
    late = "## Scores\nnone\n\n## Findings\n```json\n" + json.dumps({a: 4 for a in AXES}) + "\n```\n"
    assert srs.parse_scores(late).status == "absent"

    def block(text: str) -> str:
        return "## Scores\n```json\n" + text + "\n```\n"

    good = {a: 4 for a in AXES}
    bad_inputs = {
        "missing axis": {k: v for k, v in good.items() if k != "clarity"},
        "extra axis": {**good, "bonus": 3},
        "bool": {**good, "clarity": True},
        "zero": {**good, "clarity": 0},
        "six": {**good, "clarity": 6},
        "float": {**good, "clarity": 3.5},
    }
    for label, obj in bad_inputs.items():
        res = srs.parse_scores(block(json.dumps(obj)))
        assert res.status == "invalid", f"{label}: expected invalid, got {res.status}"
        assert res.reason, f"{label}: invalid must carry a reason"
    broken = srs.parse_scores(block("{not json"))
    assert broken.status == "invalid" and broken.reason


def test_ac13_review_schema_asks_for_scores() -> None:
    from bytedigger_engine.workflows import phase_45_spec as p45

    schema = p45._review_output_schema()
    assert "## Scores" in schema
    for axis in AXES:
        assert axis in schema, f"axis {axis!r} missing from the reviewer output schema"
    assert schema.index("## Verdict") < schema.index("## Scores")


def _review_prev(tmp_path: Path, raw: str, cycle: int = 1, **extra: Any):
    from bytedigger_engine.contracts import StepResult

    specs = tmp_path / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    name = "build-plan-review.md" if cycle == 1 else f"build-plan-review-cycle-{cycle}.md"
    data = {
        "raw_response": raw,
        "doc_path": str(specs / name),
        "spec_path": str(specs / "build-spec.md"),
        "cycle": cycle,
    }
    data.update(extra)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_review_llm")


# A cycle-1 SHIP survives reconciliation only with a structured `[]` block AND
# a parseable prose `## Verdict SHIP` (GH642 fail-closed otherwise).
_C1_TAIL = "\n## Findings (structured)\n```json\n[]\n```\n\n## Findings\n- none\n"


def _c1_review(scores: str | None) -> str:
    return "## Verdict\nSHIP\n\n" + (scores + "\n" if scores else "") + _C1_TAIL.lstrip("\n")


def test_ac14_low_score_downgrades_ship_to_revise(tmp_path: Path) -> None:
    from bytedigger_engine.workflows.phase_45_spec import _write_review_doc

    prev = _review_prev(tmp_path, _c1_review(_scores_block(clarity=2)))
    result = _write_review_doc(None, prev)

    assert result.status == "ok", result.error
    assert result.data["verdict"] == "REVISE"
    rj = json.loads((tmp_path / "specs" / "review.json").read_text())
    assert rj["verdict_before_scores"] == "SHIP"
    assert rj["verdict"] == "REVISE"
    assert rj["low_axes"] == ["clarity"]
    assert rj["scores_status"] == "ok"
    assert Path(result.data["review_json_path"]).resolve() == (tmp_path / "specs" / "review.json").resolve()


def test_ac15_ok_scores_keep_ship_and_record_min(tmp_path: Path) -> None:
    from bytedigger_engine.workflows.phase_45_spec import _write_review_doc

    prev = _review_prev(tmp_path, _c1_review(_scores_block(completeness=3, feasibility=5)))
    result = _write_review_doc(None, prev)
    assert result.status == "ok", result.error
    assert result.data["verdict"] == "SHIP"
    rj = json.loads((tmp_path / "specs" / "review.json").read_text())
    assert rj["scores_status"] == "ok"
    assert rj["min_score"] == 3
    assert rj["low_axes"] == []
    assert rj["verdict"] == "SHIP" and rj["verdict_before_scores"] == "SHIP"
    assert list(rj["scores"]) == AXES


def test_ac15_missing_scores_leave_verdict_unchanged(tmp_path: Path) -> None:
    from bytedigger_engine.workflows.phase_45_spec import _write_review_doc

    prev = _review_prev(tmp_path, _c1_review(None))
    result = _write_review_doc(None, prev)
    assert result.status == "ok", result.error
    assert result.data["verdict"] == "SHIP"
    rj = json.loads((tmp_path / "specs" / "review.json").read_text())
    assert rj["scores_status"] == "absent"
    assert rj["verdict"] == "SHIP"
    assert rj["min_score"] is None


def test_ac16_cycle2_early_result_is_record_only(tmp_path: Path) -> None:
    from bytedigger_engine.workflows.phase_45_spec import _write_review_doc

    raw = (
        "FINDING_1: RESOLVED - moved to Open Questions\n"
        "FINDING_2: RESOLVED - pinned in AC5\n"
        "VERDICT: PASS\n\n" + _scores_block(clarity=2)
    )
    prev = _review_prev(tmp_path, raw, cycle=2)
    result = _write_review_doc(None, prev)

    assert result.status == "ok", result.error
    assert result.data["verdict"] == "SHIP", "restricted cycle-2 review must never be downgraded"
    rj = json.loads((tmp_path / "specs" / "review-cycle-2.json").read_text())
    assert rj["verdict_before_scores"] == "SHIP" and rj["verdict"] == "SHIP"
    assert rj["cycle"] == 2


def test_ac16_cycle2_main_exit_downgrades(tmp_path: Path) -> None:
    from bytedigger_engine.workflows.phase_45_spec import _write_review_doc

    raw = "## Verdict\nSHIP\n\n" + _scores_block(feasibility=2) + _C1_TAIL
    prev = _review_prev(tmp_path, raw, cycle=2)
    result = _write_review_doc(None, prev)

    assert result.status == "ok", result.error
    assert result.data["verdict"] == "REVISE"
    rj = json.loads((tmp_path / "specs" / "review-cycle-2.json").read_text())
    assert rj["verdict_before_scores"] == "SHIP" and rj["verdict"] == "REVISE"
    assert rj["low_axes"] == ["feasibility"]


def test_ac19_frozen_spec_is_never_downgraded(tmp_path: Path) -> None:
    from bytedigger_engine.workflows.phase_45_spec import _write_review_doc

    prev = _review_prev(tmp_path, _c1_review(_scores_block(clarity=2)), is_frozen=True)
    result = _write_review_doc(None, prev)

    assert result.status == "ok", result.error
    assert result.data["verdict"] == "SHIP"
    rj = json.loads((tmp_path / "specs" / "review.json").read_text())
    assert rj["verdict"] == "SHIP" and rj["verdict_before_scores"] == "SHIP"
    assert rj["low_axes"] == ["clarity"]


# --------------------------------------------------------------------------
# registries and docs (AC17, AC20)
# --------------------------------------------------------------------------


def test_ac17_error_codes_and_core_manifest_registered() -> None:
    from bytedigger_engine import error_codes

    codes = ("E_VERIFICATION_SKILL_FAILED", "E_VERIFICATION_REGISTRY_TAMPERED")
    docs = [ENGINE / "ERROR_CODES.md", ENGINE / "bytedigger_engine" / "ERROR_CODES.md"]
    for code in codes:
        assert code in error_codes.ERROR_CODES, f"{code} missing from error_codes.py"
        for d in docs:
            assert code in d.read_text(), f"{code} missing from {d}"
    manifest = json.loads((ENGINE / "core_manifest.json").read_text())
    for mod in ("verification_registry.py", "spec_review_score.py"):
        assert mod in manifest["core_modules"], f"{mod} missing from core_manifest.json"


def test_ac20_verify_command_doc_and_phase5_links() -> None:
    cmd = REPO_ROOT / "commands" / "verify.md"
    assert cmd.is_file(), "commands/verify.md must exist"
    text = cmd.read_text()
    assert "bytedigger_engine.run verify" in text
    assert "no tracked file" in text.lower()
    phase5 = (REPO_ROOT / "phases" / "phase-5-implement.md").read_text()
    assert "verify_registered_skills" in phase5
    assert "commands/verify.md" in phase5
