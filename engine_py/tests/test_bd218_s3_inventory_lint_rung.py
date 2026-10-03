"""bd#218 step 3 RED -- preflight siblings runs the inventory-lint tests when the diff adds
git / read_text / subprocess calls under engine_py/bytedigger_engine.

Spec: docs/decisions/2026-10-03-bd218-s3-inventory-lint-rung.md (AC1-AC9).
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
)
PASS_SRC = "def test_ac():\n    assert True\n"
FAIL_SRC = "def test_ac10():\n    assert False\n"
ENG = "engine_py/bytedigger_engine/mod.py"
BASE_MOD = "def f():\n    return 1\n"


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


# ------------------------------------------------------------------ AC1-AC3 triggers

@pytest.mark.parametrize("line", [
    "x = p.read_text()",                      # AC1
    "subprocess.run(['echo'])",               # AC2
    'CMD = ["git", "status"]',                # AC3
])
def test_AC1_AC2_AC3_added_call_runs_failing_lint_red(tmp_path: Path, line: str) -> None:
    bad = "test_bd150_class_i_inventory.py"
    repo, spec = _mk(tmp_path, "red", lints=_all_lints((bad,)))
    _append(repo, ENG, line)
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red", sib
    assert bad in sib["detail"]


@pytest.mark.parametrize("line", [
    "x = p.read_text()",
    "subprocess.run(['echo'])",
    'CMD = ["git", "status"]',
])
def test_AC1_AC2_AC3_added_call_passing_lint_ok_with_suffix(tmp_path: Path, line: str) -> None:
    repo, spec = _mk(tmp_path, "ok", lints=_all_lints())
    _append(repo, ENG, line)
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and "inventory-lint: " in sib["detail"], sib


def test_AC1_untracked_new_engine_file_triggers(tmp_path: Path) -> None:
    bad = "test_bd94_engine_owned_paths.py"
    repo, spec = _mk(tmp_path, "untracked", lints=_all_lints((bad,)))
    _write(repo / "engine_py/bytedigger_engine/new_mod.py", "y = p.read_text()\n")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "red" and bad in sib["detail"], sib


# ------------------------------------------------------------------ AC4 / AC5 non-triggers

def test_AC4_unrelated_line_does_not_run_lints(tmp_path: Path) -> None:
    repo, spec = _mk(tmp_path, "unrel", lints=_all_lints(LINTS))
    _append(repo, ENG, "z = 1 + 1")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"] == "no siblings"


def test_AC4_call_in_engine_tests_or_non_py_does_not_run_lints(tmp_path: Path) -> None:
    repo, spec = _mk(tmp_path, "paths", lints=_all_lints(LINTS))
    _write(repo / "engine_py/tests/helper_mod.py", "x = p.read_text()\n")
    _write(repo / "engine_py/bytedigger_engine/notes.txt", "p.read_text() subprocess \"git\"\n")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"] == "no siblings"


def test_AC5_removed_read_text_line_does_not_trigger(tmp_path: Path) -> None:
    repo, spec = _mk(tmp_path, "removed", lints=_all_lints(LINTS),
                     mod_src=BASE_MOD + "v = p.read_text()\n")
    (repo / ENG).write_text(BASE_MOD, encoding="utf-8")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"] == "no siblings"


# ------------------------------------------------------------------ AC6 dedupe / absence

def test_AC6_listed_sibling_lint_runs_once_and_rung_still_triggers(tmp_path: Path) -> None:
    count = tmp_path / "count.log"
    counting = (f"def test_ac():\n    open({str(count)!r}, 'a').write('x\\n')\n")
    lints = _all_lints()
    lints["test_bd94_engine_owned_paths.py"] = counting
    listed = "engine_py/tests/test_bd94_engine_owned_paths.py"
    repo, spec = _mk(tmp_path, "dedupe", lints=lints, sibling=(listed,))
    _append(repo, ENG, "x = p.read_text()")
    sib = _sib(_run(repo, spec))
    assert "inventory-lint: " in sib["detail"], sib
    assert count.read_text(encoding="utf-8").count("x") == 1, "lint file ran more than once"


def test_AC6_no_engine_py_and_missing_lint_files_no_trigger_no_crash(tmp_path: Path) -> None:
    repo, spec = _mk(tmp_path, "noeng", engine=False)
    _append(repo, "calc.py", "x = p.read_text()")
    sib = _sib(_run(repo, spec))
    assert sib["status"] == "ok" and sib["detail"] == "no siblings"

    repo2, spec2 = _mk(tmp_path, "nolints")        # engine_py/bytedigger_engine only, no lint files
    _append(repo2, ENG, "x = p.read_text()")
    sib2 = _sib(_run(repo2, spec2))
    assert sib2["status"] == "ok" and sib2["detail"] == "no siblings"


# ------------------------------------------------------------------ AC7 known-reds

def test_AC7_known_reds_tolerates_failing_lint(tmp_path: Path) -> None:
    bad = "test_bd150_class_i_inventory.py"
    repo, spec = _mk(tmp_path, "kr", lints=_all_lints((bad,)))
    _append(repo, ENG, "x = p.read_text()")
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
