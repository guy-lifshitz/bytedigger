"""bd#218 step 3 RED (spec r2) -- preflight siblings runs the six inventory-lint tests whenever
the change set contains a .py under engine_py/bytedigger_engine/ (PATH-based trigger).

Spec: docs/decisions/2026-10-03-bd218-s3-inventory-lint-rung.md (AC1-AC10).
Real tmp git repos, real receipts on disk; preflight itself is never mocked. Every state
(base commit, lint stubs, diff) is pre-staged deterministically (workflows.md 1i); no timing.
Unit under test is imported lazily so every test fails at assert time, not at collection.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(240)

CALC = "def add(a, b):\n    return a + b\n"
RED_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"
BODY = "# spec\n\n`calc.py` defines `add`.\n"
LINTS = (
    "test_bd94_engine_owned_paths.py",
    "test_bd150_class_i_inventory.py",
    "test_bd152_output_digest.py",
    "test_bd206_class_m_sites.py",
    "test_bd89_p3c_deterministic_synthesize_report.py",
    "test_bd89_p3b1b_ii_aggregation_helper.py",
)
PASS_SRC = "def test_ac():\n    assert True\n"
FAIL_SRC = "def test_ac10():\n    assert False\n"
ENG = "engine_py/bytedigger_engine/mod.py"
BASE_MOD = "def f():\n    return 1\n"
SUFFIX6 = "inventory-lint: 6 file(s)"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL_KNOWN_REDS_TODAY", raising=False)
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


def _pf() -> Any:
    return importlib.import_module("bytedigger_engine.preflight")


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


def _mk(tmp_path: Path, name: str, *, lints: dict[str, str] | None = None,
        engine: bool = True, mod_src: str = BASE_MOD, sibling: tuple[str, ...] = ()) -> tuple[Path, Path]:
    """Repo with committed base (origin/main == HEAD); returns (repo, spec)."""
    root = tmp_path / name
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "calc.py", CALC)
    _write(repo / "tests" / "test_calc.py", RED_SRC)
    _write(repo / ".gitignore", "__pycache__/\n.pytest_cache/\n")
    if engine:
        _write(repo / ENG, mod_src)
        for lint, src in (lints or {}).items():
            _write(repo / "engine_py" / "tests" / lint, src)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    head = ["---", "red_tests: [tests/test_calc.py]", "paths: [calc.py]"]
    if sibling:
        head.append(f"sibling_tests: [{', '.join(sibling)}]")
    spec = root / "spec" / "spec.md"
    _write(spec, "\n".join(head + ["---"]) + "\n" + BODY)
    return repo.resolve(), spec


def _all_lints(fail: tuple[str, ...] = ()) -> dict[str, str]:
    return {n: (FAIL_SRC if n in fail else PASS_SRC) for n in LINTS}


def _run(repo: Path, spec: Path, **kw: Any) -> dict:
    res = _pf().run_preflight(str(spec), "red", cwd=str(repo), **kw)
    assert res["receipt"] is not None, (res["error_code"], res["error"])
    return json.loads(_pf().receipt_path(repo).read_text(encoding="utf-8"))


def _sib(doc: dict) -> dict:
    return next(s for s in doc["steps"] if s["name"] == "siblings")


def _append(repo: Path, rel: str, line: str) -> None:
    p = repo / rel
    p.write_text(p.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")


CALLS = [
    "x = p.read_text()",
    "subprocess.run(['echo'])",
    'CMD = ["git", "status"]',
    "x = p.read_bytes()",
    "x = _git_read(r)",
    "x = os.walk(r)",
]


# ------------------------------------------------------------------ AC1 / AC2 / AC3 triggers

@pytest.mark.parametrize("line", CALLS)
def test_AC1_engine_py_edit_failing_lint_red(tmp_path: Path, line: str) -> None:
    bad = "test_bd150_class_i_inventory.py"
    repo, spec = _mk(tmp_path, "red", lints=_all_lints((bad,)))
    _append(repo, ENG, line)
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red", sib
    assert bad in sib["detail"]


@pytest.mark.parametrize("line", CALLS)
def test_AC1_engine_py_edit_passing_lints_ok_suffix_6(tmp_path: Path, line: str) -> None:
    repo, spec = _mk(tmp_path, "ok", lints=_all_lints())
    _append(repo, ENG, line)
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"].endswith(SUFFIX6), sib


def test_AC1_untracked_new_engine_file_triggers(tmp_path: Path) -> None:
    bad = "test_bd94_engine_owned_paths.py"
    repo, spec = _mk(tmp_path, "untracked", lints=_all_lints((bad,)))
    _write(repo / "engine_py/bytedigger_engine/new_mod.py", "y = p.read_text()\n")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red" and bad in sib["detail"], sib


@pytest.mark.parametrize("bad", LINTS[4:])
def test_AC1_added_p3_lint_files_are_in_the_set(tmp_path: Path, bad: str) -> None:
    repo, spec = _mk(tmp_path, "p3", lints=_all_lints((bad,)))
    _append(repo, ENG, "x = 1")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red" and bad in sib["detail"], sib


def test_AC2_plain_assignment_edit_triggers(tmp_path: Path) -> None:
    bad = "test_bd152_output_digest.py"
    repo, spec = _mk(tmp_path, "plain", lints=_all_lints((bad,)))
    _append(repo, ENG, "z = 1 + 1")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red" and bad in sib["detail"], sib


def test_AC3_removal_only_edit_triggers(tmp_path: Path) -> None:
    bad = "test_bd206_class_m_sites.py"
    repo, spec = _mk(tmp_path, "removed", lints=_all_lints((bad,)),
                     mod_src=BASE_MOD + "v = 2\n")
    (repo / ENG).write_text(BASE_MOD, encoding="utf-8")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red" and bad in sib["detail"], sib


# ------------------------------------------------------------------ AC4 non-triggers

def _stage_non_trigger(repo: Path, kind: str) -> None:
    if kind == "tests_only":
        _write(repo / "engine_py/tests/helper_mod.py", "x = p.read_text()\n")
    elif kind == "non_py":
        _write(repo / "engine_py/bytedigger_engine/notes.txt", "p.read_text()\n")
    else:
        _append(repo, "calc.py", "x = p.read_text()")


@pytest.mark.parametrize("kind", ["tests_only", "non_py", "outside"])
def test_AC4_non_trigger_empty_siblings_exactly_no_siblings(tmp_path: Path, kind: str) -> None:
    repo, spec = _mk(tmp_path, "nt", lints=_all_lints(LINTS))
    _stage_non_trigger(repo, kind)
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"] == "no siblings", sib


@pytest.mark.parametrize("kind", ["tests_only", "non_py", "outside"])
def test_AC4_non_trigger_nonempty_siblings_no_suffix(tmp_path: Path, kind: str) -> None:
    lints = _all_lints(LINTS)
    lints["test_sib.py"] = PASS_SRC
    repo, spec = _mk(tmp_path, "nts", lints=lints, sibling=("engine_py/tests/test_sib.py",))
    _stage_non_trigger(repo, kind)
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok", sib
    assert sib["detail"] != "no siblings" and "inventory-lint" not in sib["detail"], sib


# ------------------------------------------------------------------ AC5 dedupe

def _counting_lints(count: Path) -> dict[str, str]:
    lints = _all_lints()
    lints["test_bd94_engine_owned_paths.py"] = (
        f"def test_ac():\n    open({str(count)!r}, 'a').write('x\\n')\n")
    return lints


@pytest.mark.parametrize("listed", [
    "engine_py/tests/test_bd94_engine_owned_paths.py",
    "./engine_py/tests/test_bd94_engine_owned_paths.py",
])
def test_AC5_listed_lint_runs_once_and_n_counts_only_appended(tmp_path: Path, listed: str) -> None:
    count = tmp_path / "count.log"
    repo, spec = _mk(tmp_path, "dedupe", lints=_counting_lints(count), sibling=(listed,))
    _append(repo, ENG, "x = 1")
    sib = _sib(_run(repo, spec))
    assert sib["detail"].endswith("inventory-lint: 5 file(s)"), sib
    assert count.read_text(encoding="utf-8").count("x") == 1, "lint file ran more than once"


def test_AC5_all_six_listed_n_zero_no_suffix(tmp_path: Path) -> None:
    listed = tuple(f"./engine_py/tests/{n}" for n in LINTS)
    repo, spec = _mk(tmp_path, "all6", lints=_all_lints(), sibling=listed)
    _append(repo, ENG, "x = 1")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and "inventory-lint" not in sib["detail"], sib
    assert sib["detail"] != "no siblings", sib


# ------------------------------------------------------------------ AC6 absence

def test_AC6_no_engine_py_no_trigger_no_crash(tmp_path: Path) -> None:
    repo, spec = _mk(tmp_path, "noeng", engine=False)
    _append(repo, "calc.py", "x = p.read_text()")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"] == "no siblings"


def test_AC6_missing_lint_files_skipped_no_crash(tmp_path: Path) -> None:
    repo, spec = _mk(tmp_path, "nolints")
    _append(repo, ENG, "x = 1")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"] == "no siblings", sib


def test_AC6_partial_lint_set_counts_existing_only(tmp_path: Path) -> None:
    two = {n: PASS_SRC for n in LINTS[:2]}
    repo, spec = _mk(tmp_path, "partial", lints=two)
    _append(repo, ENG, "x = 1")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"].endswith("inventory-lint: 2 file(s)"), sib


# ------------------------------------------------------------------ AC7 known-reds

def test_AC7_known_reds_tolerates_failing_lint(tmp_path: Path) -> None:
    bad = "test_bd150_class_i_inventory.py"
    repo, spec = _mk(tmp_path, "kr", lints=_all_lints((bad,)))
    _append(repo, ENG, "x = 1")
    led = tmp_path / "ledger.md"
    led.write_text(
        "| Suite | Red | Scope | Issue | Kill-by | Class |\n|---|---|---|---|---|---|\n"
        f"| s | {bad}::test_ac10 | all | #1 | 2999-01-01 | flaky |\n", encoding="utf-8")
    sib = _sib(_run(repo, spec, known_reds=str(led)))
    assert sib["status"] == "ok", sib
    assert "inventory-lint: " in sib["detail"] and "tolerated" in sib["detail"]


# ------------------------------------------------------------------ AC8 engine producer

def test_AC8_engine_producer_runs_no_lint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _spec = _mk(tmp_path, "eng", lints=_all_lints(LINTS))
    _append(repo, ENG, "x = p.read_text()")
    real = subprocess.run
    cmds: list[str] = []

    def rec(cmd: Any, *a: Any, **k: Any) -> Any:
        cmds.append(" ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd))
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", rec)
    res = _pf().run_engine_preflight(str(repo), ["tests/test_calc.py"], "# spec\n")
    assert res["exit_code"] == 0 and cmds, "forcing: producer ran and spawned git reads"
    assert not [c for c in cmds if "pytest" in c]
    doc = json.loads(_pf().receipt_path(repo).read_text(encoding="utf-8"))
    assert [s["name"] for s in doc["steps"]] == ["syntax", "stub", "facts"]
    assert "inventory-lint" not in json.dumps(doc)


# ------------------------------------------------------------------ AC9 reachability (CLI)

def test_AC9_cli_main_reaches_trigger_receipt_on_disk(tmp_path: Path) -> None:
    bad = "test_bd152_output_digest.py"
    repo, spec = _mk(tmp_path, "cli", lints=_all_lints((bad,)))
    _append(repo, ENG, "x = p.read_text()")
    code = _pf().preflight_main(["--spec", str(spec), "--phase", "red", "--cwd", str(repo)])
    doc = json.loads(_pf().receipt_path(repo).read_text(encoding="utf-8"))
    sib = _sib(doc)
    assert sib["status"] == "red" and bad in sib["detail"], sib
    assert code == 1 and doc["ok"] is False


# ------------------------------------------------------------------ AC10 committed since merge-base

def test_AC10_change_committed_since_merge_base_triggers(tmp_path: Path) -> None:
    bad = "test_bd94_engine_owned_paths.py"
    repo, spec = _mk(tmp_path, "committed", lints=_all_lints((bad,)))
    _append(repo, ENG, "x = 1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "engine change")
    assert _git(repo, "status", "--porcelain") == ""
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red" and bad in sib["detail"], sib
