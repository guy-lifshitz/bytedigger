"""RED tests for bd#260 - run directory retention, first release is a dry-run.

Spec: docs/decisions/2026-10-10-bd260-run-dir-retention-dry-run.md (AC1-AC12, r1).

Unit under test: `event_log.run_retention_dry_run(run_id=None, root=None, now=None)`.
New names (ABSENT pre-GREEN): `event_log.run_retention_dry_run`,
`config_provider.run_log_retention_days`, flag `HAL_RUN_LOG_RETENTION_DAYS`. They are
touched only inside test bodies, so every test fails individually at assert time
(never at collection). `now` is passed as a float epoch (seconds).

Determinism (workflows.md section 1i): real directories/files under tmp_path,
backdated with os.utime relative to a fixed `NOW`; no sleeps, no network, no real
`claude`, no sys.path mutation. CLI cases run run.py (echo, --neutral) in a subprocess.
AC13 (sibling suites stay green) is verified by running those suites; no test here.
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import config_provider, event_log, flags_catalog
from helpers.engine_subprocess import engine_env

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_PY = REPO_ROOT / "engine_py" / "bytedigger_engine" / "run.py"
VAR = "HAL_RUN_LOG_RETENTION_DAYS"
DAY = 86400.0
NOW = 1_800_000_000.0
SUMMARY2 = "run retention (dry-run): 2 run directories older than 90 days would be removed; nothing deleted"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
        monkeypatch.delenv(prefix + "RUN_LOG_RETENTION_DAYS", raising=False)
        monkeypatch.delenv(prefix + "EVENT_LOG_PATH", raising=False)
        if prefix != "HAL_":
            monkeypatch.delenv(prefix + "RUN_LOG_ROOT", raising=False)
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(tmp_path / "dbos.sqlite"))


def _mk(root: Path, name: str, age_days: float, *, files: dict[str, float] | None = None,
        now: float = NOW) -> Path:
    """Real run dir `name`; its files (name -> age days) and then the dir are backdated."""
    d = root / name
    d.mkdir(parents=True)
    ages = files if files is not None else {"events.jsonl": age_days}
    for fname, fage in ages.items():
        f = d / fname
        f.write_text("{}\n", encoding="utf-8")
        os.utime(f, (now - fage * DAY, now - fage * DAY))
    os.utime(d, (now - age_days * DAY, now - age_days * DAY))
    return d


def _tree(base: Path) -> set[tuple[str, bool]]:
    return {(str(p), p.is_symlink()) for p in base.rglob("*")}


def _call(root, run_id=None, now=NOW):
    return event_log.run_retention_dry_run(run_id=run_id, root=root, now=now)


def _cli(root: Path, cwd: Path, argv: list[str], **extra: str) -> subprocess.CompletedProcess:
    cwd.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(RUN_PY), *argv]
    return subprocess.run(cmd, cwd=str(cwd), env=engine_env(HAL_RUN_LOG_ROOT=str(root), **extra),
                          capture_output=True, text=True, timeout=120)


def _echo_argv(run_id: str = "R260") -> list[str]:
    return ["--workflow", "echo", "--neutral", "--run-id", run_id,
            "--ctx-json", json.dumps({"org_config": {}, "question": "hi"})]


def _cli_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    _mk(root, "oldA", 120, now=_now())
    _mk(root, "oldB", 120, now=_now())
    _mk(root, "fresh", 1, now=_now())
    return root


def _now() -> float:
    import time  # noqa: PLC0415
    return time.time()


# ---------------------------------------------------------------- AC1

def test_ac1_lists_only_old_closed_runs_and_deletes_nothing(tmp_path):
    root = tmp_path / "root"
    old1, old2 = _mk(root, "old1", 120), _mk(root, "old2", 120)
    _mk(root, "fresh", 1)
    _mk(root, "open1", 200)
    before = _tree(root)
    result = _call(root, run_id="open1")
    assert result == [old1, old2]
    assert _tree(root) == before


# ---------------------------------------------------------------- AC2

def test_ac2_age_is_newest_file_inside(tmp_path):
    root = tmp_path / "root"
    _mk(root, "active", 200, files={"events.jsonl": 1, "other.txt": 200})
    stale = _mk(root, "stale", 200, files={"events.jsonl": 200})
    assert _call(root) == [stale]


# ---------------------------------------------------------------- AC3

def test_ac3_boundary_is_strict(tmp_path, monkeypatch):
    monkeypatch.setenv(VAR, "30")
    root = tmp_path / "root"
    _mk(root, "exact", 30)
    over = _mk(root, "over", 30 + 1 / DAY)
    assert _call(root) == [over]


def test_ac3_future_mtime_is_not_a_candidate(tmp_path):
    root = tmp_path / "root"
    old1 = _mk(root, "old1", 120)
    _mk(root, "future", -1)
    assert _call(root) == [old1]


# ---------------------------------------------------------------- AC4

def _case_symlink_inside(root, outside):
    target = _mk(root, "realold", 120)
    (root / "lnk").symlink_to(target, target_is_directory=True)
    return [target], None


def _case_symlink_outside(root, outside):
    target = _mk(outside, "ext", 120)
    (root / "lnk").symlink_to(target, target_is_directory=True)
    return [], target


def _case_file(root, outside):
    f = root / "fileid"
    f.write_text("x", encoding="utf-8")
    os.utime(f, (NOW - 200 * DAY, NOW - 200 * DAY))
    return [], None


def _case_hidden(root, outside):
    _mk(root, ".hidden", 200)
    return [], None


def _case_space(root, outside):
    _mk(root, "a b", 200)
    return [], None


def _case_dangling_child(root, outside):
    d = _mk(root, "dang", 120)
    child = d / "dangling"
    child.symlink_to(root / "does-not-exist")
    os.utime(child, (NOW - 120 * DAY, NOW - 120 * DAY), follow_symlinks=False)
    os.utime(d, (NOW - 120 * DAY, NOW - 120 * DAY))
    return [d], None


@pytest.mark.parametrize("case", [_case_symlink_inside, _case_symlink_outside, _case_file,
                                  _case_hidden, _case_space, _case_dangling_child],
                         ids=["symlink_inside", "symlink_outside", "file", "hidden", "space",
                              "dangling_child"])
def test_ac4_non_candidates_even_when_old(tmp_path, case):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    old = _mk(root, "old1", 120)
    extra, target = case(root, outside)
    before = _tree(tmp_path)
    expected = sorted([old, *extra])
    assert _call(root) == expected
    assert _tree(tmp_path) == before
    if target is not None:
        assert (target / "events.jsonl").is_file()


# ---------------------------------------------------------------- AC5

@pytest.mark.parametrize("name", [VAR, "BD_RUN_LOG_RETENTION_DAYS"])
def test_ac5_zero_disables(tmp_path, monkeypatch, capfd, name):
    root = tmp_path / "root"
    old1 = _mk(root, "old1", 120)
    # positive control: with the default setting the old dir is listed (fails pre-GREEN)
    assert _call(root) == [old1]
    capfd.readouterr()
    monkeypatch.setenv(name, "0")
    assert _call(root) == []
    assert capfd.readouterr().err == ""
    assert old1.is_dir()


# ---------------------------------------------------------------- AC6

def _six_bad_setting(tmp_path, monkeypatch, value):
    monkeypatch.setenv(VAR, value)
    root = tmp_path / "root"
    _mk(root, "old1", 120)
    return root, True


def _six_missing(tmp_path, monkeypatch):
    return tmp_path / "nope", False


def _six_file(tmp_path, monkeypatch):
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    return f, True


def _six_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path / "rel", "old1", 120)
    return Path("rel"), True


@pytest.mark.parametrize("case", [
    lambda t, m: _six_bad_setting(t, m, "abc"),
    lambda t, m: _six_bad_setting(t, m, "-5"),
    _six_missing, _six_file, _six_relative,
], ids=["abc", "negative", "missing_root", "root_is_file", "relative_root"])
def test_ac6_degrades_never_raises(tmp_path, monkeypatch, capfd, case):
    root, expect_skip = case(tmp_path, monkeypatch)
    result = _call(root)
    err = capfd.readouterr().err
    assert result == []
    skipped = [ln for ln in err.splitlines() if "run retention skipped" in ln]
    if expect_skip:
        assert len(skipped) == 1, err
        assert len(err.splitlines()) == 1, err
    else:
        assert err == ""


# ---------------------------------------------------------------- AC7

def test_ac7_report_two_candidates(tmp_path, capfd):
    root = tmp_path / "root"
    _mk(root, "old1", 120)
    _mk(root, "old2", 120)
    assert len(_call(root)) == 2
    lines = capfd.readouterr().err.splitlines()
    assert lines == [SUMMARY2, "old1", "old2"]


def test_ac7_report_capped_at_twenty(tmp_path, capfd):
    root = tmp_path / "root"
    names = [f"c{i:02d}" for i in range(1, 26)]
    for n in names:
        _mk(root, n, 120)
    assert len(_call(root)) == 25
    lines = capfd.readouterr().err.splitlines()
    assert "25 run directories older than 90 days" in lines[0]
    assert lines[1:21] == names[:20]
    assert lines[21] == "... and 5 more"
    assert len(lines) == 22


def test_ac7_no_candidates_is_silent(tmp_path, capfd):
    root = tmp_path / "root"
    _mk(root, "fresh", 1)
    # the report channel must exist and find nothing (fails pre-GREEN: function absent)
    assert _call(root) == []
    assert capfd.readouterr().err == ""


# ---------------------------------------------------------------- AC8

def test_ac8_cli_reports_and_deletes_nothing(tmp_path):
    root = _cli_root(tmp_path)
    proc = _cli(root, tmp_path / "work", _echo_argv())
    assert proc.returncode == 0, proc.stderr
    assert SUMMARY2 in proc.stderr.splitlines(), proc.stderr
    for n in ("oldA", "oldB", "fresh"):
        assert (root / n).is_dir()
    assert (root / "R260" / "events.jsonl").is_file()


def test_ac8_cli_zero_disables(tmp_path):
    root = _cli_root(tmp_path)
    positive = _cli(root, tmp_path / "work", _echo_argv("R260"))
    assert SUMMARY2 in positive.stderr, positive.stderr  # fails pre-GREEN
    proc = _cli(root, tmp_path / "work", _echo_argv("R261"), **{VAR: "0"})
    assert proc.returncode == 0, proc.stderr
    assert "run retention" not in proc.stderr


def test_ac8_cli_list_does_not_call_it(tmp_path):
    root = _cli_root(tmp_path)
    positive = _cli(root, tmp_path / "work", _echo_argv())
    assert SUMMARY2 in positive.stderr, positive.stderr  # fails pre-GREEN
    proc = _cli(root, tmp_path / "work", ["--list"])
    assert proc.returncode == 0, proc.stderr
    assert "run retention" not in proc.stdout + proc.stderr


def test_ac8b_cli_resumed_run_is_not_a_candidate(tmp_path):
    root = tmp_path / "root"
    _mk(root, "R260b", 120, now=_now())
    _mk(root, "other", 120, now=_now())
    proc = _cli(root, tmp_path / "work", _echo_argv("R260b"))
    assert proc.returncode == 0, proc.stderr
    lines = proc.stderr.splitlines()
    assert "run retention (dry-run): 1 run directories older than 90 days would be removed; nothing deleted" in lines, proc.stderr
    assert "other" in lines
    assert "R260b" not in lines
    assert (root / "R260b").is_dir()
    assert (root / "other").is_dir()


# ---------------------------------------------------------------- AC9

def test_ac9_cli_bad_setting_still_runs(tmp_path):
    root = _cli_root(tmp_path)
    proc = _cli(root, tmp_path / "work", _echo_argv(), **{VAR: "abc"})
    assert proc.returncode == 0, proc.stderr
    assert len([ln for ln in proc.stderr.splitlines() if "run retention skipped" in ln]) == 1, proc.stderr
    assert (root / "R260" / "events.jsonl").is_file()
    for n in ("oldA", "oldB", "fresh"):
        assert (root / n).is_dir()


# ---------------------------------------------------------------- AC10

def test_ac10_listing_identical_with_and_without_api_key(tmp_path, monkeypatch):
    root = tmp_path / "root"
    old1, old2 = _mk(root, "old1", 120), _mk(root, "old2", 120)
    _mk(root, "fresh", 1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    with_key = _call(root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    without_key = _call(root)
    assert with_key == without_key == [old1, old2]


# ---------------------------------------------------------------- AC11 (guard)

def test_ac11_event_log_module_cannot_delete():
    tree = ast.parse(Path(event_log.__file__).read_text(encoding="utf-8"))
    forbidden = {"rmtree", "unlink", "rmdir", "remove", "removedirs", "rename", "replace", "truncate",
                 "move", "system", "Popen"}
    forbidden_modules = {"shutil", "subprocess"}
    hits = []
    # AST nodes only (Call func is a Name/Attribute; docstring prose is a Constant, never visited here)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr in forbidden:
            hits.append((n.attr, n.lineno))
        elif isinstance(n, ast.Name) and n.id in forbidden:
            hits.append((n.id, n.lineno))
        elif isinstance(n, ast.Import):
            hits.extend((a.name, n.lineno) for a in n.names if a.name.split(".")[0] in forbidden_modules)
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] in forbidden_modules:
                hits.append((n.module, n.lineno))
            hits.extend((a.name, n.lineno) for a in n.names if a.name in forbidden)
    assert not hits, hits
    assert any(isinstance(n, ast.FunctionDef) and n.name == "run_retention_dry_run"
               for n in ast.walk(tree)), "run_retention_dry_run must exist in event_log.py"


# ---------------------------------------------------------------- AC12 (guard)

def test_ac12_config_provider_default_is_90():
    assert config_provider.run_log_retention_days() == 90


def test_ac12_flag_catalogued():
    assert VAR in flags_catalog.FLAGS, "retention flag must be catalogued"
    entry = flags_catalog.FLAGS[VAR]
    assert entry.get("kind") == "int"
    assert entry.get("default") == "90"
    assert isinstance(entry.get("owner"), str) and entry["owner"].strip()
    assert str(entry.get("provenance", "")).startswith("introduced:")
    m = re.search(r"flip-by:(\d{4}-\d{2}-\d{2})", str(entry.get("description", "")))
    assert m, "description must carry flip-by:YYYY-MM-DD"
    flip = datetime.date.fromisoformat(m.group(1))
    today = datetime.date(2026, 10, 10)
    assert today <= flip <= today + datetime.timedelta(days=45)


def test_ac12_lints_pass_with_flag_catalogued():
    assert VAR in flags_catalog.FLAGS, "retention flag must be catalogued"
    for argv in (["scripts/flag_owner_lint.py"],
                 ["scripts/flip_horizon.py", "--check", "--today", "2026-10-10"]):
        r = subprocess.run([sys.executable, *argv], capture_output=True, text=True, cwd=str(REPO_ROOT))
        assert r.returncode == 0, f"{argv}: {r.stdout!r} {r.stderr!r}"


def test_ac12_docs_mention_variable():
    assert VAR in flags_catalog.FLAGS, "retention flag must be catalogued"
    text = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert VAR in text


def test_ac12_changelog_added_block_mentions_variable():
    from helpers.changelog import read_changelog, require_entry  # noqa: PLC0415

    require_entry(read_changelog(), VAR, block="Added")
