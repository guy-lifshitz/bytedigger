"""RED tests for bd#164 -- host-callable deterministic preflight CLI (``preflight``).

Spec: docs/decisions/2026-10-02-bd164-preflight-cli.md (r1, ACs A1-A10 of section 3).

The module under test (``bytedigger_engine.preflight``) does not exist yet. Every
AC reaches it through a lazy ``_pf()`` import inside the test (or a real
``run.main`` subprocess), so the file collects and each test fails at assert time
independently (workflows.md 1q). The unit under test is never mocked: fixtures are
REAL temp git repos in tmp_path; classifiers are REAL python scripts run as
[sys.executable, script]; classifier timeouts are pre-staged (classifier_timeout_s
small, script sleeps 10 s), never raced (workflows.md 1i). The only monkeypatched
objects are collaborators (``llm_subprocess.invoke_llm_subprocess`` must NOT be
reached; ``check_ladder.prescreen`` forced to raise; recording wrappers around the
real building-block functions that delegate to the real implementation).

ACs -> tests:
  A1 test_A1_*   A2 test_A2_*   A3 test_A3_*   A4 test_A4_*   A5 test_A5_*
  A6 test_A6_*   A7 test_A7_*   A8 test_A8_*   A9 test_A9_*   A10 test_A10_*

Sibling tests found (models for fixtures / registration checks):
  test_bd141_check_ladder.py (fake real classifiers, lazy import, L19 registration),
  test_bd141_close_gate.py, test_core_boundary.py.
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, NamedTuple

import pytest

pytestmark = pytest.mark.timeout(180)

ENGINE_PY = Path(__file__).resolve().parents[1]
MODULE_FILE = ENGINE_PY / "bytedigger_engine" / "preflight.py"

CALC = "def add(a, b):\n    return a + b\n"
RED_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"
GREEN_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
VACUOUS_SRC = "def helper():\n    return 1\n"
STUB_SRC = (
    "from unittest.mock import patch\n"
    "from calc import add\n\n\n"
    "def test_add():\n"
    "    with patch(\"calc.add\", return_value=4):\n"
    "        assert add(1, 2) == 4\n"
)
SIB_SRC = "def test_sib_broken():\n    assert False\n"
OK_BODY = "# spec\n\n`calc.py` defines `add`.\n"
BAD_CITE_BODY = "# spec\n\n`calc.py` defines `nonexistent_fn_zz`.\n"
STEP_NAMES = ["syntax", "tier", "cite", "stub", "scoped", "siblings", "facts", "prescreen"]


def _pf() -> Any:
    """Lazy import of the unit under test (fails at assert time, not collect)."""
    return importlib.import_module("bytedigger_engine.preflight")


def _g(res: Any, key: str) -> Any:
    """Field of a PreflightResult whether it is a dict or an attribute object."""
    return res[key] if isinstance(res, dict) else getattr(res, key)


# --------------------------------------------------------------------------
# Fixture: real temp git repo
# --------------------------------------------------------------------------

class Fx(NamedTuple):
    repo: Path
    spec: Path
    top: str


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _spec_text(*, red_tests: tuple[str, ...] = ("tests/test_calc.py",),
               paths: tuple[str, ...] = ("calc.py",), sibling: tuple[str, ...] = (),
               pins: tuple[str, ...] = (), tier: str | None = None,
               body: str = OK_BODY) -> str:
    lines = ["---", f"red_tests: [{', '.join(red_tests)}]", f"paths: [{', '.join(paths)}]"]
    if sibling:
        lines.append(f"sibling_tests: [{', '.join(sibling)}]")
    if pins:
        lines.append(f"red_pins: [{', '.join(pins)}]")
    if tier:
        lines.append(f"tier: {tier}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _mk(tmp_path: Path, name: str = "w", *, test_src: str = RED_SRC,
        files: dict[str, str] | None = None, untracked: dict[str, str] | None = None,
        spec_kw: dict[str, Any] | None = None) -> Fx:
    """Temp repo: committed calc.py/tests/.gitignore, origin/main = HEAD, spec outside the tree."""
    root = tmp_path / name
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "calc.py", CALC)
    _write(repo / "tests" / "test_calc.py", test_src)
    _write(repo / ".gitignore", "ignored/\n__pycache__/\n.pytest_cache/\n")
    for rel, txt in (files or {}).items():
        _write(repo / rel, txt)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    for rel, txt in (untracked or {}).items():
        _write(repo / rel, txt)
    resolved = repo.resolve()
    kw = dict(spec_kw or {})
    kw["paths"] = tuple(p.replace("{repo}", str(resolved)) for p in kw.get("paths", ("calc.py",)))
    spec = root / "spec" / "spec.md"
    _write(spec, _spec_text(**kw))
    _write(resolved / "note.txt", "v1")  # untracked non-ignored file present at receipt time
    return Fx(resolved, spec, str(resolved))


def _run(fx: Fx, phase: str = "red", **kw: Any) -> Any:
    return _pf().run_preflight(str(fx.spec), phase, cwd=str(fx.repo), **kw)


def _receipt_json(fx: Fx) -> dict[str, Any]:
    return json.loads(_pf().receipt_path(fx.top).read_text(encoding="utf-8"))


def _fake(tmp_path: Path, name: str, *, out: str | None = None, rc: int = 0,
          sleep: float = 0.0, spawn_file: Path | None = None) -> list[str]:
    lines = ["import sys, time"]
    if spawn_file is not None:
        lines.append(f"open({str(spawn_file)!r}, 'w').write('spawned')")
    lines.append("sys.stdin.read()")
    if sleep:
        lines.append(f"time.sleep({sleep!r})")
    if out is not None:
        lines.append(f"print({out!r})")
    lines.append(f"sys.exit({rc})")
    script = tmp_path / f"{name}.py"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [sys.executable, str(script)]


def _ledger(path: Path, red_cell: str, kill_by: str) -> Path:
    path.write_text(
        "| Suite | Red | Scope | Issue | Kill-by | Class |\n"
        "|---|---|---|---|---|---|\n"
        f"| s | {red_cell} | all | #1 | {kill_by} | flaky |\n",
        encoding="utf-8",
    )
    return path


def _assert_red_at(res: Any, top: str, step: str, phase: str) -> dict[str, Any]:
    pf = _pf()
    assert _g(res, "exit_code") == 1, (step, _g(res, "error_code"), _g(res, "error"))
    assert _g(res, "error_code") == pf.STEP_CODES[step]
    rec = _g(res, "receipt")
    steps = rec["steps"]
    names = [s["name"] for s in steps]
    assert names == list(pf.STEPS)
    idx = names.index(step)
    assert steps[idx]["status"] == "red", steps[idx]
    assert [s["status"] for s in steps[:idx]] == ["ok"] * idx
    for s in steps[idx + 1:]:
        assert s["status"] == "skipped" and s["ms"] == 0 and s["detail"] == ""
    assert rec["ok"] is False
    assert pf.verify_receipt(phase, top) == "red"
    return rec


# --------------------------------------------------------------------------
# A1 parse_spec_fields
# --------------------------------------------------------------------------

def test_A1_parse_spec_fields_forms() -> None:
    """A1: block list, inline list, scalar, quotes, no front-matter, missing keys."""
    pf = _pf()
    text = (
        "---\n"
        "red_tests:\n"
        "  - tests/a.py\n"
        "  - \"tests/b.py\"\n"
        "sibling_tests: [tests/c.py, 'tests/d.py']\n"
        "paths: calc.py\n"
        "tier: micro\n"
        "unknown_key: [x, y]\n"
        "---\n"
        "body `calc.py` `add`\n"
    )
    got = pf.parse_spec_fields(text)
    assert got["red_tests"] == ["tests/a.py", "tests/b.py"]
    assert got["sibling_tests"] == ["tests/c.py", "tests/d.py"]
    assert got["paths"] == ["calc.py"]
    assert got["tier"] == ["micro"]
    assert got["red_pins"] == []
    assert "unknown_key" not in got


def test_A1_parse_spec_fields_degenerate() -> None:
    """A1: no front-matter -> all five keys empty; never raises on any str."""
    pf = _pf()
    empty = {k: [] for k in ("red_tests", "sibling_tests", "paths", "red_pins", "tier")}
    assert pf.parse_spec_fields("# just a body\nred_tests: [x]\n") == empty
    assert pf.parse_spec_fields("") == empty
    for weird in ("---\n", "---\nred_tests: [\n", "---\n: : :\n---\n", "\x00\n---\n---"):
        out = pf.parse_spec_fields(weird)
        assert isinstance(out, dict) and set(out) >= set(empty)


# --------------------------------------------------------------------------
# A2 green path
# --------------------------------------------------------------------------

def test_A2_green_path_red_phase(tmp_path: Path) -> None:
    """A2: exit 0, receipt on disk, 8 steps in order, ok, head/state_hash, facts, fresh."""
    pf = _pf()
    fx = _mk(tmp_path)
    res = _run(fx, "red")
    assert _g(res, "exit_code") == 0, (_g(res, "error_code"), _g(res, "error"))
    assert _g(res, "error_code") is None
    rp = pf.receipt_path(fx.top)
    assert rp.is_file() and rp.parent.name == pf.RECEIPT_DIRNAME == "bytedigger-preflight"
    assert str(rp).startswith(_git(fx.repo, "rev-parse", "--absolute-git-dir"))
    rec = _receipt_json(fx)
    assert rec == _g(res, "receipt")
    assert rec["schema"] == 1 and rec["phase"] == "red" and rec["ok"] is True
    assert [s["name"] for s in rec["steps"]] == list(pf.STEPS) == STEP_NAMES
    statuses = {s["name"]: s["status"] for s in rec["steps"]}
    assert all(v == "ok" for k, v in statuses.items() if k != "prescreen"), statuses
    assert statuses["prescreen"] == "off"
    for s in rec["steps"]:
        assert set(s) >= {"name", "status", "ms", "detail"}
    assert rec["head"] == _git(fx.repo, "rev-parse", "HEAD")
    assert rec["state_hash"] == pf.compute_state_hash(fx.top)
    assert pf.compute_state_hash(fx.top) == pf.compute_state_hash(fx.top)
    assert Path(rec["facts_path"]).is_file()
    assert rec["head"] in Path(rec["facts_path"]).read_text(encoding="utf-8")
    assert {"spec", "base", "prescreen", "ts"} <= set(rec)
    assert pf.verify_receipt("red", fx.top) == "fresh"


def test_A2_green_phase_with_passing_tests(tmp_path: Path) -> None:
    """A2: green phase over a passing test is ok and fresh for 'green'."""
    pf = _pf()
    fx = _mk(tmp_path, test_src=GREEN_SRC)
    res = _run(fx, "green")
    assert _g(res, "exit_code") == 0, (_g(res, "error_code"), _g(res, "error"))
    assert _g(res, "receipt")["phase"] == "green"
    assert pf.verify_receipt("green", fx.top) == "fresh"


# --------------------------------------------------------------------------
# A3 each red step
# --------------------------------------------------------------------------

def test_A3_syntax_red(tmp_path: Path) -> None:
    """A3: broken .py in changed set -> E_PREFLIGHT_SYNTAX, later steps skipped."""
    fx = _mk(tmp_path, untracked={"broken.py": "def (:\n"})
    res = _run(fx, "red")
    rec = _assert_red_at(res, fx.top, "syntax", "red")
    assert "broken.py" in rec["steps"][0]["detail"]


@pytest.mark.parametrize("how", ["arg", "spec"])
def test_A3_tier_micro_engine_prod_path(tmp_path: Path, how: str) -> None:
    """A3: tier MICRO + existing engine_py prod path -> E_PREFLIGHT_TIER."""
    prod = "{repo}/SYSTEM/cli/build/engine_py/prod.py"
    fx = _mk(tmp_path, files={"SYSTEM/cli/build/engine_py/prod.py": "X = 1\n"},
             spec_kw={"paths": ("calc.py", prod), "tier": "micro" if how == "spec" else None})
    res = _run(fx, "red", tier="MICRO" if how == "arg" else None)
    rec = _assert_red_at(res, fx.top, "tier", "red")
    assert "prod.py" in rec["steps"][1]["detail"]


def test_A3_tier_micro_without_engine_path_is_ok(tmp_path: Path) -> None:
    """A3 control: MICRO with a non-engine path passes the tier step."""
    fx = _mk(tmp_path)
    res = _run(fx, "red", tier="MICRO")
    assert _g(res, "exit_code") == 0, (_g(res, "error_code"), _g(res, "error"))


def test_A3_cite_red(tmp_path: Path) -> None:
    """A3: unresolved cited symbol -> E_PREFLIGHT_CITE."""
    fx = _mk(tmp_path, spec_kw={"body": BAD_CITE_BODY})
    res = _run(fx, "red")
    rec = _assert_red_at(res, fx.top, "cite", "red")
    assert "nonexistent_fn_zz" in rec["steps"][2]["detail"]


def test_A3_stub_red(tmp_path: Path) -> None:
    """A3: stub-passable RED (mocks its own UUT) -> E_PREFLIGHT_STUB."""
    fx = _mk(tmp_path, test_src=STUB_SRC)
    res = _run(fx, "red")
    _assert_red_at(res, fx.top, "stub", "red")


def test_A3_scoped_red_phase_passing_test(tmp_path: Path) -> None:
    """A3: red phase with a passing test -> E_PREFLIGHT_SCOPED 'passing test(s) in red phase'."""
    fx = _mk(tmp_path, test_src=GREEN_SRC)
    res = _run(fx, "red")
    rec = _assert_red_at(res, fx.top, "scoped", "red")
    assert "passing test" in rec["steps"][4]["detail"]


def test_A3_scoped_red_phase_vacuous_file(tmp_path: Path) -> None:
    """A3: red phase with no failing test -> E_PREFLIGHT_SCOPED 'vacuous RED'."""
    fx = _mk(tmp_path, test_src=VACUOUS_SRC)
    res = _run(fx, "red")
    rec = _assert_red_at(res, fx.top, "scoped", "red")
    assert "vacuous" in rec["steps"][4]["detail"]


def test_A3_scoped_green_phase_failing_test(tmp_path: Path) -> None:
    """A3: green phase with a failing test -> E_PREFLIGHT_SCOPED 'N failing'."""
    fx = _mk(tmp_path, test_src=RED_SRC)
    res = _run(fx, "green")
    rec = _assert_red_at(res, fx.top, "scoped", "green")
    assert "failing" in rec["steps"][4]["detail"] and "test_add" in rec["steps"][4]["detail"]


def test_A3_siblings_failing_not_in_ledger(tmp_path: Path) -> None:
    """A3: failing sibling with no ledger row -> E_PREFLIGHT_SIBLINGS."""
    fx = _mk(tmp_path, files={"tests/test_sib.py": SIB_SRC},
             spec_kw={"sibling": ("tests/test_sib.py",)})
    other = _ledger(tmp_path / "other.md", "other_module.other_test", "2999-01-01")
    res = _run(fx, "red", known_reds=str(other))
    rec = _assert_red_at(res, fx.top, "siblings", "red")
    assert "test_sib.py" in rec["steps"][5]["detail"]


def test_A3_siblings_tolerated_by_active_known_red(tmp_path: Path) -> None:
    """A3: failing sibling matched by an ACTIVE known-reds row -> ok."""
    pf = _pf()
    fx = _mk(tmp_path, files={"tests/test_sib.py": SIB_SRC},
             spec_kw={"sibling": ("tests/test_sib.py",)})
    led = _ledger(tmp_path / "ledger.md", "test_sib.py::test_sib_broken", "2999-01-01")
    res = _run(fx, "red", known_reds=str(led))
    assert _g(res, "exit_code") == 0, (_g(res, "error_code"), _g(res, "error"))
    assert pf.verify_receipt("red", fx.top) == "fresh"


def test_A3_siblings_expired_known_red_does_not_tolerate(tmp_path: Path) -> None:
    """A3: an EXPIRED known-reds row tolerates nothing."""
    fx = _mk(tmp_path, files={"tests/test_sib.py": SIB_SRC},
             spec_kw={"sibling": ("tests/test_sib.py",)})
    led = _ledger(tmp_path / "ledger.md", "test_sib.py::test_sib_broken", "2000-01-01")
    res = _run(fx, "red", known_reds=str(led))
    _assert_red_at(res, fx.top, "siblings", "red")


def test_A3_siblings_tolerated_by_red_pins_in_red_phase(tmp_path: Path) -> None:
    """A3: file in red_pins is tolerated in red phase, not in green phase."""
    fx = _mk(tmp_path, files={"tests/test_sib.py": SIB_SRC},
             spec_kw={"sibling": ("tests/test_sib.py",), "pins": ("tests/test_sib.py",)})
    res = _run(fx, "red")
    assert _g(res, "exit_code") == 0, (_g(res, "error_code"), _g(res, "error"))
    assert "pinned: tests/test_sib.py" in _g(res, "receipt")["steps"][5]["detail"]
    fx2 = _mk(tmp_path, "w2", test_src=GREEN_SRC, files={"tests/test_sib.py": SIB_SRC},
              spec_kw={"sibling": ("tests/test_sib.py",), "pins": ("tests/test_sib.py",)})
    res2 = _run(fx2, "green")
    _assert_red_at(res2, fx2.top, "siblings", "green")


# --------------------------------------------------------------------------
# A4 staleness
# --------------------------------------------------------------------------

def _m_edit_tracked(r: Path) -> None:
    (r / "calc.py").write_text(CALC + "# edit\n", encoding="utf-8")


def _m_stage(r: Path) -> None:
    _m_edit_tracked(r)
    _git(r, "add", "calc.py")


def _m_stage_then_revert(r: Path) -> None:
    _m_stage(r)
    (r / "calc.py").write_text(CALC, encoding="utf-8")


def _m_untracked_new(r: Path) -> None:
    (r / "new.txt").write_text("n", encoding="utf-8")


def _m_untracked_edit(r: Path) -> None:
    (r / "note.txt").write_text("v2", encoding="utf-8")


def _m_untracked_delete(r: Path) -> None:
    (r / "note.txt").unlink()


def _m_commit(r: Path) -> None:
    _git(r, "commit", "-q", "--allow-empty", "-m", "another")


_STALE_MUTATIONS: dict[str, Callable[[Path], None]] = {
    "edit_tracked_unstaged": _m_edit_tracked,
    "git_add_change": _m_stage,
    "stage_then_revert_worktree": _m_stage_then_revert,
    "untracked_created": _m_untracked_new,
    "untracked_edited": _m_untracked_edit,
    "untracked_deleted": _m_untracked_delete,
    "new_commit": _m_commit,
}


@pytest.mark.parametrize("mutation", sorted(_STALE_MUTATIONS))
def test_A4_mutation_makes_receipt_stale(tmp_path: Path, mutation: str) -> None:
    """A4: each tree mutation after a fresh receipt -> 'stale'."""
    pf = _pf()
    fx = _mk(tmp_path)
    assert _g(_run(fx, "red"), "exit_code") == 0
    assert pf.verify_receipt("red", fx.top) == "fresh"
    _STALE_MUTATIONS[mutation](fx.repo)
    assert pf.verify_receipt("red", fx.top) == "stale"


def test_A4_gitignored_file_stays_fresh(tmp_path: Path) -> None:
    """A4: a new gitignored file does not change the state hash."""
    pf = _pf()
    fx = _mk(tmp_path)
    assert _g(_run(fx, "red"), "exit_code") == 0
    _write(fx.repo / "ignored" / "x.txt", "ignored")
    assert pf.verify_receipt("red", fx.top) == "fresh"


def test_A4_missing_cases(tmp_path: Path) -> None:
    """A4: phase mismatch / no receipt / corrupt JSON / bad schema / non-git dir -> missing."""
    pf = _pf()
    fx = _mk(tmp_path)
    assert pf.verify_receipt("red", fx.top) == "missing"  # no receipt yet
    assert _g(_run(fx, "red"), "exit_code") == 0
    assert pf.verify_receipt("green", fx.top) == "missing"  # phase mismatch
    rec = _receipt_json(fx)
    rp = pf.receipt_path(fx.top)
    rp.write_text(json.dumps({**rec, "schema": 2}), encoding="utf-8")
    assert pf.verify_receipt("red", fx.top) == "missing"
    rp.write_text("{not json", encoding="utf-8")
    assert pf.verify_receipt("red", fx.top) == "missing"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert pf.verify_receipt("red", str(plain)) == "missing"


# --------------------------------------------------------------------------
# A5 prescreen advisory
# --------------------------------------------------------------------------

def _prescreen_step(res: Any) -> dict[str, Any]:
    return _g(res, "receipt")["steps"][7]


def test_A5_no_classifier_is_off(tmp_path: Path) -> None:
    """A5: no classifier -> step 'off', receipt prescreen null, ok."""
    fx = _mk(tmp_path)
    res = _run(fx, "red")
    assert _g(res, "exit_code") == 0
    assert _prescreen_step(res)["status"] == "off"
    assert _g(res, "receipt")["prescreen"] is None and _g(res, "receipt")["ok"] is True


@pytest.mark.parametrize("case", ["missing_binary", "nonzero", "timeout"])
def test_A5_classifier_failure_is_advisory(tmp_path: Path, case: str) -> None:
    """A5: missing binary / non-zero exit / timeout -> step 'error', ok and exit unchanged."""
    pf = _pf()
    fx = _mk(tmp_path)
    kw: dict[str, Any] = {}
    if case == "missing_binary":
        cmd = [str(tmp_path / "no-such-classifier-binary")]
    elif case == "nonzero":
        cmd = _fake(tmp_path, "nz", rc=3)
    else:
        cmd = _fake(tmp_path, "slow", out='{"label":"reject","confidence":1.0}', sleep=10)
        kw["classifier_timeout_s"] = 0.5
    res = _run(fx, "red", classifier_cmd=cmd, **kw)
    assert _g(res, "exit_code") == 0 and _g(res, "receipt")["ok"] is True
    step = _prescreen_step(res)
    assert step["status"] == "error"
    assert step["detail"] == ("timeout" if case == "timeout" else "error")
    assert _g(res, "receipt")["prescreen"]["status"] == step["detail"]
    assert pf.verify_receipt("red", fx.top) == "fresh"


def test_A5_classifier_reject_never_blocks(tmp_path: Path) -> None:
    """A5: classifier prints reject/0.99 -> step 'ok'; shadow never rejects the preflight."""
    fx = _mk(tmp_path)
    cmd = _fake(tmp_path, "rej", out='{"label":"reject","confidence":0.99}')
    res = _run(fx, "red", classifier_cmd=cmd)
    assert _g(res, "exit_code") == 0 and _g(res, "receipt")["ok"] is True
    step = _prescreen_step(res)
    assert step["status"] == "ok"
    assert "label=reject" in step["detail"] and "confidence=0.99" in step["detail"]
    pre = _g(res, "receipt")["prescreen"]
    assert pre["status"] == "ok" and pre["label"] == "reject" and pre["confidence"] == 0.99


def test_A5_prescreen_raising_is_advisory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A5: check_ladder.prescreen raising -> step 'error' 'internal error', ok unchanged."""
    cl = importlib.import_module("bytedigger_engine.check_ladder")

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("prescreen exploded")

    monkeypatch.setattr(cl, "prescreen", boom)
    fx = _mk(tmp_path)
    cmd = _fake(tmp_path, "unused", out='{"label":"pass","confidence":0.5}')
    res = _run(fx, "red", classifier_cmd=cmd)
    assert _g(res, "exit_code") == 0 and _g(res, "receipt")["ok"] is True
    step = _prescreen_step(res)
    assert step["status"] == "error" and step["detail"].startswith("internal error")


def test_A5_prescreen_skipped_after_red_step(tmp_path: Path) -> None:
    """A5: an earlier red step -> prescreen 'skipped', classifier never spawned."""
    spawned = tmp_path / "spawned.flag"
    cmd = _fake(tmp_path, "spy", out='{"label":"pass","confidence":0.5}', spawn_file=spawned)
    fx = _mk(tmp_path, untracked={"broken.py": "def (:\n"})
    res = _run(fx, "red", classifier_cmd=cmd)
    assert _g(res, "exit_code") == 1
    assert _prescreen_step(res)["status"] == "skipped"
    assert not spawned.exists()


def test_A5_prescreen_off_in_green_phase(tmp_path: Path) -> None:
    """A5: green phase -> prescreen 'off' (skip green), classifier never spawned."""
    spawned = tmp_path / "spawned.flag"
    cmd = _fake(tmp_path, "spy", out='{"label":"pass","confidence":0.5}', spawn_file=spawned)
    fx = _mk(tmp_path, test_src=GREEN_SRC)
    res = _run(fx, "green", classifier_cmd=cmd)
    assert _g(res, "exit_code") == 0
    assert _prescreen_step(res)["status"] == "off"
    assert not spawned.exists()


# --------------------------------------------------------------------------
# A6 exit 2
# --------------------------------------------------------------------------

def test_A6_missing_spec_and_empty_fields_delete_old_receipt(tmp_path: Path) -> None:
    """A6: missing spec / empty red_tests -> exit 2 E_PREFLIGHT_SPEC_FIELDS, old receipt gone."""
    pf = _pf()
    fx = _mk(tmp_path)
    assert _g(_run(fx, "red"), "exit_code") == 0
    assert pf.receipt_path(fx.top).exists()
    res = pf.run_preflight(str(tmp_path / "nope.md"), "red", cwd=str(fx.repo))
    assert _g(res, "exit_code") == 2 and _g(res, "error_code") == "E_PREFLIGHT_SPEC_FIELDS"
    assert _g(res, "receipt") is None
    assert not pf.receipt_path(fx.top).exists()
    assert pf.verify_receipt("red", fx.top) == "missing"

    assert _g(_run(fx, "red"), "exit_code") == 0
    assert pf.receipt_path(fx.top).exists()
    empty = tmp_path / "empty_spec.md"
    empty.write_text(_spec_text(red_tests=()), encoding="utf-8")
    res2 = pf.run_preflight(str(empty), "red", cwd=str(fx.repo))
    assert _g(res2, "exit_code") == 2 and _g(res2, "error_code") == "E_PREFLIGHT_SPEC_FIELDS"
    assert not pf.receipt_path(fx.top).exists()


def test_A6_bad_phase_usage(tmp_path: Path) -> None:
    """A6: phase not red/green -> exit 2 E_PREFLIGHT_USAGE, no receipt written."""
    pf = _pf()
    fx = _mk(tmp_path)
    res = _run(fx, "blue")
    assert _g(res, "exit_code") == 2 and _g(res, "error_code") == "E_PREFLIGHT_USAGE"
    assert _g(res, "receipt") is None and _g(res, "error")
    assert not pf.receipt_path(fx.top).exists()


def test_A6_unknown_base_is_git_error(tmp_path: Path) -> None:
    """A6: unknown --base -> exit 2 E_PREFLIGHT_GIT, no receipt written."""
    pf = _pf()
    fx = _mk(tmp_path)
    res = _run(fx, "red", base="refs/heads/no-such-branch")
    assert _g(res, "exit_code") == 2 and _g(res, "error_code") == "E_PREFLIGHT_GIT"
    assert not pf.receipt_path(fx.top).exists()


def test_A6_not_a_git_repo(tmp_path: Path) -> None:
    """A6: cwd outside any git repo -> exit 2 E_PREFLIGHT_GIT."""
    pf = _pf()
    plain = tmp_path / "plain"
    plain.mkdir()
    spec = plain / "spec.md"
    spec.write_text(_spec_text(), encoding="utf-8")
    res = pf.run_preflight(str(spec), "red", cwd=str(plain))
    assert _g(res, "exit_code") == 2 and _g(res, "error_code") == "E_PREFLIGHT_GIT"
    assert _g(res, "receipt") is None


# --------------------------------------------------------------------------
# A7 provider-agnostic
# --------------------------------------------------------------------------

def test_A7_backend_env_does_not_change_steps(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """A7: HAL_RUNNER_BACKEND values give identical step names/statuses; no LLM call."""
    ls = importlib.import_module("bytedigger_engine.llm_subprocess")
    reached: list[int] = []

    def boom(*a: Any, **k: Any) -> Any:
        reached.append(1)
        raise RuntimeError("LLM must not be reached by preflight")

    monkeypatch.setattr(ls, "invoke_llm_subprocess", boom)
    seen: dict[str, list[tuple[str, str]]] = {}
    for i, backend in enumerate(("claude-subprocess", "anthropic-api")):
        monkeypatch.setenv("HAL_RUNNER_BACKEND", backend)
        fx = _mk(tmp_path, f"b{i}")
        res = _run(fx, "red")
        assert _g(res, "exit_code") == 0, (backend, _g(res, "error_code"), _g(res, "error"))
        seen[backend] = [(s["name"], s["status"]) for s in _g(res, "receipt")["steps"]]
    assert seen["claude-subprocess"] == seen["anthropic-api"]
    assert [n for n, _ in seen["anthropic-api"]] == STEP_NAMES
    assert reached == []


def _strip_docstrings(source: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                d = body[0]
                for ln in range(d.lineno, (d.end_lineno or d.lineno) + 1):
                    lines[ln - 1] = ""
    return "\n".join(lines)


def test_A7_module_source_is_provider_agnostic() -> None:
    """A7: no 'HAL_' literal and no vendor name outside docstrings."""
    assert MODULE_FILE.exists(), "A7: preflight.py must exist"
    code = _strip_docstrings(MODULE_FILE.read_text(encoding="utf-8"))
    assert "HAL_" not in code
    assert re.search(r"anthropic|claude|openai|jev", code, re.IGNORECASE) is None


# --------------------------------------------------------------------------
# A8 CLI via run.main
# --------------------------------------------------------------------------

def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_PY) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; from bytedigger_engine.run import main; sys.exit(main())",
         "preflight", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=170,
    )


def test_A8_json_receipt_exit0(tmp_path: Path) -> None:
    """A8: run.main dispatches 'preflight'; --json emits exactly one receipt document."""
    fx = _mk(tmp_path)
    cp = _cli(fx.repo, "--spec", str(fx.spec), "--phase", "red", "--json")
    assert cp.returncode == 0, cp.stderr[-500:]
    doc = json.loads(cp.stdout)
    assert doc["ok"] is True and doc["phase"] == "red" and doc["schema"] == 1
    assert [s["name"] for s in doc["steps"]] == STEP_NAMES


def test_A8_json_receipt_exit1_and_exit2(tmp_path: Path) -> None:
    """A8: --json exit 1 -> receipt with ok false; exit 2 -> {ok:false,error_code,error}."""
    fx = _mk(tmp_path, untracked={"broken.py": "def (:\n"})
    cp = _cli(fx.repo, "--spec", str(fx.spec), "--phase", "red", "--json")
    assert cp.returncode == 1, cp.stderr[-500:]
    assert json.loads(cp.stdout)["ok"] is False
    cp2 = _cli(fx.repo, "--spec", str(tmp_path / "nope.md"), "--phase", "red", "--json")
    assert cp2.returncode == 2
    doc = json.loads(cp2.stdout)
    assert doc["ok"] is False and doc["error_code"] == "E_PREFLIGHT_SPEC_FIELDS" and doc["error"]


def test_A8_human_output_lines(tmp_path: Path) -> None:
    """A8: human output is '<name>: <status> <detail>' per step then 'preflight: ok'."""
    fx = _mk(tmp_path)
    cp = _cli(fx.repo, "--spec", str(fx.spec), "--phase", "red")
    assert cp.returncode == 0, cp.stderr[-500:]
    lines = [ln for ln in cp.stdout.splitlines() if ln.strip()]
    assert [ln.split(":", 1)[0] for ln in lines[:8]] == STEP_NAMES
    assert lines[0].startswith("syntax: ok") and lines[7].startswith("prescreen: off")
    assert lines[-1] == "preflight: ok"
    bad = _mk(tmp_path, "w2", untracked={"broken.py": "def (:\n"})
    cp2 = _cli(bad.repo, "--spec", str(bad.spec), "--phase", "red")
    assert cp2.returncode == 1
    assert cp2.stdout.strip().splitlines()[-1].startswith("preflight: red E_PREFLIGHT_SYNTAX")


def test_A8_verify_prints_word_and_exit_code(tmp_path: Path) -> None:
    """A8: --verify prints the verify word; exit 0 only for fresh."""
    fx = _mk(tmp_path)
    cp0 = _cli(fx.repo, "--verify", "--phase", "red", "--cwd", str(fx.repo))
    assert cp0.stdout.strip() == "missing" and cp0.returncode == 1
    assert _cli(fx.repo, "--spec", str(fx.spec), "--phase", "red").returncode == 0
    cp1 = _cli(fx.repo, "--verify", "--phase", "red", "--cwd", str(fx.repo))
    assert cp1.stdout.strip() == "fresh" and cp1.returncode == 0
    _m_untracked_new(fx.repo)
    cp2 = _cli(fx.repo, "--verify", "--phase", "red", "--cwd", str(fx.repo))
    assert cp2.stdout.strip() == "stale" and cp2.returncode == 1


def test_A8_bad_classifier_cmd_and_usage_exit2(tmp_path: Path) -> None:
    """A8: --classifier-cmd not a JSON list of strings -> exit 2 E_PREFLIGHT_USAGE; usage error -> 2."""
    fx = _mk(tmp_path)
    for bad in ("not json", '{"a": 1}', "[1, 2]"):
        cp = _cli(fx.repo, "--spec", str(fx.spec), "--phase", "red", "--json",
                  "--classifier-cmd", bad)
        assert cp.returncode == 2, (bad, cp.stderr[-300:])
        doc = json.loads(cp.stdout)
        assert doc["ok"] is False and doc["error_code"] == "E_PREFLIGHT_USAGE"
    cp2 = _cli(fx.repo, "--phase", "red")  # no --spec and no --verify
    assert cp2.returncode == 2
    assert "preflight" in (cp2.stderr + cp2.stdout).lower()


# --------------------------------------------------------------------------
# A9 registries
# --------------------------------------------------------------------------

def test_A9_registries() -> None:
    """A9: manifest + mypy list + error codes + constants."""
    pf = _pf()
    manifest = json.loads((ENGINE_PY / "core_manifest.json").read_text(encoding="utf-8"))
    assert "preflight.py" in manifest["core_modules"]
    strict = (ENGINE_PY / "bytedigger_engine" / "mypy-strict-modules.txt").read_text(
        encoding="utf-8").splitlines()
    assert "preflight.py" in [ln.strip() for ln in strict]
    codes = importlib.import_module("bytedigger_engine.error_codes").ERROR_CODES
    assert tuple(pf.STEPS) == tuple(STEP_NAMES)
    assert pf.STEP_CODES == {s: "E_PREFLIGHT_" + s.upper() for s in STEP_NAMES}
    for code in (*pf.STEP_CODES.values(), "E_PREFLIGHT_SPEC_FIELDS",
                 "E_PREFLIGHT_GIT", "E_PREFLIGHT_USAGE"):
        assert code in codes, code
    assert pf.RECEIPT_SCHEMA == 1 and pf.RECEIPT_DIRNAME == "bytedigger-preflight"
    md = (ENGINE_PY / "ERROR_CODES.md").read_text(encoding="utf-8")
    assert "E_PREFLIGHT_SCOPED" in md and "E_PREFLIGHT_USAGE" in md
    src = MODULE_FILE.read_text(encoding="utf-8")
    assert re.search("[" + chr(0x400) + "-" + chr(0x4FF) + "]", src) is None


# --------------------------------------------------------------------------
# A10 one implementation
# --------------------------------------------------------------------------

_DELEGATES = {
    "spec_cite": "lint_spec",
    "stub_passability": "lint_red_file",
    "tier_gate": "lint_paths",
    "facts_pack": "collect",
    "check_ladder": "prescreen",
    "known_reds_ledger": "red_match_tokens",
}


def test_A10_module_references_building_blocks_and_redefines_none() -> None:
    """A10: module references each building block via its module and defines no same-named function."""
    assert MODULE_FILE.exists(), "A10: preflight.py must exist"
    tree = ast.parse(MODULE_FILE.read_text(encoding="utf-8"))
    refs = {(n.value.id, n.attr) for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    for mod, fn in _DELEGATES.items():
        assert (mod, fn) in refs, f"A10: missing reference {mod}.{fn}"
    forbidden = {"lint_spec", "lint_spec_text", "scan_citations", "check_citation",
                 "lint_red_file", "scan_stub_passability", "lint_paths",
                 "scan_tier_violation", "is_engine_py_prod", "collect", "render",
                 "prescreen", "run_classifier", "red_match_tokens", "parse_table_rows",
                 "partition_by_kill_by", "classify_kill_by"}
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not (defined & forbidden), defined & forbidden


def test_A10_run_preflight_delegates_to_real_building_blocks(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A10: a full red-phase run calls each real building block (recording wrappers delegate)."""
    calls: list[str] = []
    for mod_name, fn in _DELEGATES.items():
        mod = importlib.import_module(f"bytedigger_engine.{mod_name}")
        real = getattr(mod, fn)

        def make(real: Callable[..., Any], tag: str) -> Callable[..., Any]:
            def wrapper(*a: Any, **k: Any) -> Any:
                calls.append(tag)
                return real(*a, **k)
            return wrapper

        monkeypatch.setattr(mod, fn, make(real, f"{mod_name}.{fn}"))
    fx = _mk(tmp_path, files={"tests/test_sib.py": SIB_SRC},
             spec_kw={"sibling": ("tests/test_sib.py",)})
    led = _ledger(tmp_path / "ledger.md", "test_sib.py::test_sib_broken", "2999-01-01")
    cmd = _fake(tmp_path, "pass", out='{"label":"pass","confidence":0.5}')
    res = _run(fx, "red", tier="MICRO", classifier_cmd=cmd, known_reds=str(led))
    assert _g(res, "exit_code") == 0, (_g(res, "error_code"), _g(res, "error"))
    for mod_name, fn in _DELEGATES.items():
        assert f"{mod_name}.{fn}" in calls, f"A10: {mod_name}.{fn} not called"
