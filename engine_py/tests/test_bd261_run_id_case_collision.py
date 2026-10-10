"""RED tests for bd#261 - run ids that differ only by case must not share a run directory.

Spec: docs/decisions/2026-10-10-bd261-run-id-case-collision.md (AC1-AC13).

Unit under test: `event_log.run_scoped_log_path(run_id, root)`.
New names (ABSENT pre-GREEN): `event_log.RunIdCaseCollision`, `event_log._on_disk_spelling`,
`config_provider.run_id_case_guard_enabled`, flag `HAL_RUN_ID_CASE_GUARD`. They are touched
only inside test bodies, so every test fails at assert time (never at collection).

Filesystem forms. "real": real directories under tmp_path; collision cases are skipped
where the filesystem does not fold case (probe fixture). "sim": a stand-in for the
filesystem only (`os.listdir` / `os.lstat` as seen from `event_log`), folding or exact;
the unit under test is the real function. Sim twins run everywhere.

Determinism (workflows.md section 1i): all state is pre-staged before the call; no
sleeps, no network, no model, no sys.path mutation. AC13 (sibling suites stay green) is
verified by running those suites; no test here.
"""
from __future__ import annotations

import ast
import datetime
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine import config_provider, event_log, flags_catalog, telemetry_ctx
from helpers.engine_subprocess import engine_env

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_PY = REPO_ROOT / "engine_py" / "bytedigger_engine" / "run.py"
VAR = "HAL_RUN_ID_CASE_GUARD"
CODE = "E_RUN_ID_CASE_COLLISION"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
        monkeypatch.delenv(prefix + "RUN_ID_CASE_GUARD", raising=False)
        monkeypatch.delenv(prefix + "EVENT_LOG_PATH", raising=False)
        if prefix != "HAL_":
            monkeypatch.delenv(prefix + "RUN_LOG_ROOT", raising=False)
    monkeypatch.delenv("HAL_SEMANTIC_VERIFY_RUN_LOG", raising=False)
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(tmp_path / "dbos.sqlite"))


@pytest.fixture
def folding_fs(tmp_path):
    """Skip unless the real filesystem under tmp_path folds case."""
    probe = tmp_path / "probe_area"
    probe.mkdir()
    (probe / "Probe").mkdir()
    if not (probe / "pROBE").exists():
        pytest.skip("filesystem does not fold case")


@pytest.fixture
def exact_fs(tmp_path):
    """Skip unless the real filesystem keeps `R1` and `r1` apart."""
    probe = tmp_path / "probe_area"
    probe.mkdir()
    (probe / "Probe").mkdir()
    if (probe / "pROBE").exists():
        pytest.skip("filesystem folds case")


class _SimOs:
    """Stand-in for `os` as seen from event_log: child lookups and listings of the
    registered roots behave like a folding (or exact) filesystem; all else is real."""

    def __init__(self, fold: bool = True):
        self.fold = fold
        self.fs: dict[Path, list[str]] = {}
        self.listdir_calls: list[Path] = []
        self.listdir_exc: Exception | None = None
        self.lstat_exc: Exception | None = None

    def add(self, root: Path, entries: list[str]) -> Path:
        self.fs[Path(root)] = list(entries)
        return Path(root)

    def __getattr__(self, name):
        return getattr(os, name)

    def listdir(self, path="."):
        p = Path(path)
        if p in self.fs:
            self.listdir_calls.append(p)
            if self.listdir_exc is not None:
                raise self.listdir_exc
            return list(self.fs[p])
        return os.listdir(path)

    def lstat(self, path, *args, **kwargs):
        p = Path(path)
        if p.parent in self.fs:
            if self.lstat_exc is not None:
                raise self.lstat_exc
            names = self.fs[p.parent]
            hit = any(n.lower() == p.name.lower() for n in names) if self.fold else p.name in names
            if hit:
                return os.lstat(os.getcwd())
            raise FileNotFoundError(2, "sim: no such entry", str(p))
        return os.lstat(path, *args, **kwargs)


@pytest.fixture
def sim(monkeypatch):
    s = _SimOs(fold=True)
    monkeypatch.setattr(event_log, "os", s)
    return s


def _tree(base: Path) -> set[str]:
    return {str(p) for p in base.rglob("*")}


def _refused(run_id: str, root: Path) -> "event_log.RunIdCaseCollision":
    with pytest.raises(event_log.RunIdCaseCollision) as info:
        event_log.run_scoped_log_path(run_id, root)
    return info.value


def _guard_is_live(s: _SimOs, tmp_path: Path) -> None:
    """Positive control: on a folding sim an aliased id is refused (fails pre-GREEN)."""
    ctl = s.add(tmp_path / "ctl-root", ["R1"])
    assert _refused("r1", ctl).code == CODE


def _check_reference(root: Path, before: set[str]) -> None:
    err = _refused("r1", root)
    assert isinstance(err, ValueError)
    assert err.code == CODE
    assert err.existing == "R1"
    assert err.run_id == "r1"
    msg = str(err)
    assert "R1" in msg and "r1" in msg and VAR in msg
    assert event_log.run_scoped_log_path("R1", root) == root / "R1" / "events.jsonl"
    assert _tree(root.parent) == before


# ---------------------------------------------------------------- AC1

def test_ac1_reference_case_real(tmp_path, folding_fs):
    root = tmp_path / "root"
    (root / "R1").mkdir(parents=True)
    _check_reference(root, _tree(tmp_path))


def test_ac1_reference_case_sim(tmp_path, sim):
    root = sim.add(tmp_path / "root", ["R1"])
    _check_reference(root, _tree(tmp_path))
    assert not root.exists()


# ---------------------------------------------------------------- AC2

def test_ac2_both_directions_and_mixed_case(tmp_path, sim):
    root = sim.add(tmp_path / "root", ["Run-Ab"])
    for rid in ("run-ab", "RUN-AB", "rUN-aB"):
        assert _refused(rid, root).existing == "Run-Ab"
    assert event_log.run_scoped_log_path("Run-Ab", root) == root / "Run-Ab" / "events.jsonl"


# ---------------------------------------------------------------- AC3

def test_ac3_no_false_positives_sim(tmp_path, sim):
    _guard_is_live(sim, tmp_path)
    empty = sim.add(tmp_path / "empty", [])
    only_r2 = sim.add(tmp_path / "r2", ["R2"])
    missing = tmp_path / "missing"
    for root in (empty, only_r2, missing):
        assert event_log.run_scoped_log_path("R1", root) == root / "R1" / "events.jsonl"
    present = sim.add(tmp_path / "present", ["R1"])
    for rid in ("R10", "R"):
        assert event_log.run_scoped_log_path(rid, present) == present / rid / "events.jsonl"
    assert not any(p.exists() for p in (empty, only_r2, missing, present))


def test_ac3_no_false_positives_real(tmp_path, folding_fs, sim, monkeypatch):
    _guard_is_live(sim, tmp_path)  # control on the sim, then back to the real os
    monkeypatch.setattr(event_log, "os", os)
    empty = tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "only_r2" / "R2").mkdir(parents=True)
    before = _tree(tmp_path)
    for root in (empty, tmp_path / "only_r2", tmp_path / "missing"):
        assert event_log.run_scoped_log_path("R1", root) == root / "R1" / "events.jsonl"
    (empty / "R1").mkdir()
    for rid in ("R10", "R"):
        assert event_log.run_scoped_log_path(rid, empty) == empty / rid / "events.jsonl"
    assert _tree(tmp_path) == before | {str(empty / "R1")}


# ---------------------------------------------------------------- AC4

def test_ac4_case_sensitive_fs_unchanged_sim(tmp_path, monkeypatch):
    folding = _SimOs(fold=True)
    monkeypatch.setattr(event_log, "os", folding)
    _guard_is_live(folding, tmp_path)
    exact = _SimOs(fold=False)
    monkeypatch.setattr(event_log, "os", exact)
    root = exact.add(tmp_path / "root", ["R1", "r1"])
    assert event_log.run_scoped_log_path("R1", root) == root / "R1" / "events.jsonl"
    assert event_log.run_scoped_log_path("r1", root) == root / "r1" / "events.jsonl"


def test_ac4_case_sensitive_fs_unchanged_real(tmp_path, exact_fs):
    root = tmp_path / "root"
    (root / "R1").mkdir(parents=True)
    (root / "r1").mkdir()
    assert event_log.run_scoped_log_path("R1", root) == root / "R1" / "events.jsonl"
    assert event_log.run_scoped_log_path("r1", root) == root / "r1" / "events.jsonl"
    # positive control: the guard exists (fails pre-GREEN)
    assert hasattr(event_log, "RunIdCaseCollision")


# ---------------------------------------------------------------- AC5

@pytest.mark.parametrize("name", [VAR, "BD_RUN_ID_CASE_GUARD"])
def test_ac5_kill_switch_restores_old_behaviour(tmp_path, sim, monkeypatch, name):
    root = sim.add(tmp_path / "root", ["R1"])
    _refused("r1", root)  # control: on by default
    assert sim.listdir_calls
    sim.listdir_calls.clear()
    monkeypatch.setenv(name, "0")
    assert event_log.run_scoped_log_path("r1", root) == root / "r1" / "events.jsonl"
    assert sim.listdir_calls == []


@pytest.mark.parametrize("value", ["1", "", "off"])
def test_ac5_other_values_keep_guard_on(tmp_path, sim, monkeypatch, value):
    root = sim.add(tmp_path / "root", ["R1"])
    monkeypatch.setenv(VAR, value)
    assert _refused("r1", root).code == CODE


# ---------------------------------------------------------------- AC6

def test_ac6_listdir_permission_error_degrades(tmp_path, sim, capfd):
    root = sim.add(tmp_path / "root", ["R1"])
    _refused("r1", root)  # control
    capfd.readouterr()
    sim.listdir_exc = PermissionError(13, "denied")
    assert event_log.run_scoped_log_path("r1", root) == root / "r1" / "events.jsonl"
    out = capfd.readouterr()
    assert out.out == "" and out.err == ""


def test_ac6_lstat_oserror_degrades(tmp_path, sim, capfd):
    root = sim.add(tmp_path / "root", ["R1"])
    _refused("r1", root)  # control
    capfd.readouterr()
    sim.lstat_exc = PermissionError(13, "denied")
    assert event_log.run_scoped_log_path("r1", root) == root / "r1" / "events.jsonl"
    out = capfd.readouterr()
    assert out.out == "" and out.err == ""


def test_ac6_missing_root_returns_path(tmp_path, sim):
    _guard_is_live(sim, tmp_path)
    root = tmp_path / "nope"
    assert event_log.run_scoped_log_path("R1", root) == root / "R1" / "events.jsonl"
    assert not root.exists()


# ---------------------------------------------------------------- AC7

def test_ac7_cli_degrades_and_keeps_log_bytes(tmp_path, folding_fs):
    root = tmp_path / "root"
    (root / "R261").mkdir(parents=True)
    log = root / "R261" / "events.jsonl"
    log.write_bytes(b'{"ts":"2026-01-01T00:00:00.000Z","run_id":"R261","event_type":"x","payload":{}}\n')
    before = log.read_bytes()
    work = tmp_path / "work"
    work.mkdir()
    argv = ["--workflow", "echo", "--neutral", "--run-id", "r261",
            "--ctx-json", json.dumps({"org_config": {}, "question": "hi"})]
    proc = subprocess.run([sys.executable, str(RUN_PY), *argv], cwd=str(work),
                          env=engine_env(HAL_RUN_LOG_ROOT=str(root)),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert CODE in proc.stderr, proc.stderr
    assert log.read_bytes() == before


def test_ac7_verifier_helper_falls_back_with_one_warning(tmp_path, sim, monkeypatch, caplog):
    from bytedigger_engine.lib.plugins.anti_hallucination import semantic_verifier as sv  # noqa: PLC0415

    telemetry_ctx.clear_current_run()
    telemetry_ctx.set_invocation_run_id(None)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    root = sim.add(tmp_path / "root", ["R261"])
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    default = event_log.default_log_path()
    with caplog.at_level(logging.WARNING, logger=sv.logger.name):
        log = sv._run_event_log(SimpleNamespace(session_id="r261"))
    assert log.path == default
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and CODE in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------- AC8

def test_ac8_outcome_identical_with_and_without_api_key(tmp_path, sim, monkeypatch):
    root = sim.add(tmp_path / "root", ["R1"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    with_key = _refused("r1", root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    without_key = _refused("r1", root)
    assert (with_key.code, with_key.existing, str(with_key)) == (
        without_key.code, without_key.existing, str(without_key))


# ---------------------------------------------------------------- AC9

def test_ac9_resumed_run_own_directory(tmp_path, sim):
    _guard_is_live(sim, tmp_path)
    root = sim.add(tmp_path / "root", ["R1"])
    expected = root / "R1" / "events.jsonl"
    assert event_log.run_scoped_log_path("R1", root) == expected
    assert event_log.run_scoped_log_path("R1", root) == expected


def test_ac9_resumed_run_own_directory_real(tmp_path, folding_fs):
    root = tmp_path / "root"
    (root / "R1").mkdir(parents=True)
    (root / "R1" / "events.jsonl").write_text("{}\n", encoding="utf-8")
    expected = root / "R1" / "events.jsonl"
    assert event_log.run_scoped_log_path("R1", root) == expected
    assert event_log.run_scoped_log_path("R1", root) == expected
    assert _refused("r1", root).existing == "R1"  # control


# ---------------------------------------------------------------- AC10 (guard)

def _module_tree() -> ast.Module:
    return ast.parse(Path(event_log.__file__).read_text(encoding="utf-8"))


def test_ac10_error_class_and_function_bodies():
    tree = _module_tree()
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "RunIdCaseCollision" in classes
    assert any(isinstance(b, ast.Name) and b.id == "ValueError"
               for b in classes["RunIdCaseCollision"].bases)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_on_disk_spelling" in funcs, "helper _on_disk_spelling must exist"
    banned = {"environ", "getenv", "mkdir", "makedirs", "open", "write_text"}
    for fname in ("run_scoped_log_path", "_on_disk_spelling"):
        hits = []
        for n in ast.walk(funcs[fname]):
            if isinstance(n, ast.Attribute) and n.attr in banned:
                hits.append((fname, n.attr))
            elif isinstance(n, ast.Name) and n.id in banned:
                hits.append((fname, n.id))
        assert not hits, hits


def test_ac10_module_still_cannot_delete():
    tree = _module_tree()
    forbidden = {"rmtree", "unlink", "rmdir", "remove", "removedirs", "rename", "replace",
                 "truncate", "move", "system", "Popen"}
    hits = [(n.attr if isinstance(n, ast.Attribute) else n.id, n.lineno) for n in ast.walk(tree)
            if (isinstance(n, ast.Attribute) and n.attr in forbidden)
            or (isinstance(n, ast.Name) and n.id in forbidden)]
    assert not hits, hits
    assert any(isinstance(n, ast.ClassDef) and n.name == "RunIdCaseCollision"
               for n in ast.walk(tree)), "RunIdCaseCollision must exist"


# ---------------------------------------------------------------- AC11 (guard)

def test_ac11_provider_function_default_and_off(monkeypatch):
    assert config_provider.run_id_case_guard_enabled() is True
    monkeypatch.setenv(VAR, "0")
    assert config_provider.run_id_case_guard_enabled() is False


def test_ac11_provider_without_method_is_enabled(monkeypatch):
    assert hasattr(config_provider, "run_id_case_guard_enabled")
    monkeypatch.setattr(config_provider, "get_config", lambda: SimpleNamespace())
    assert config_provider.run_id_case_guard_enabled() is True


def test_ac11_flag_catalogued():
    assert VAR in flags_catalog.FLAGS, "guard flag must be catalogued"
    entry = flags_catalog.FLAGS[VAR]
    assert entry.get("kind") == "gate"
    assert isinstance(entry.get("owner"), str) and entry["owner"].strip()
    assert str(entry.get("provenance", "")).startswith("introduced:")
    m = re.search(r"retire-by:(\d{4}-\d{2}-\d{2})", str(entry.get("description", "")))
    assert m, "description must carry retire-by:YYYY-MM-DD"
    retire = datetime.date.fromisoformat(m.group(1))
    today = datetime.date(2026, 10, 10)
    assert today <= retire <= today + datetime.timedelta(days=45)


def test_ac11_lints_pass_with_flag_catalogued():
    assert VAR in flags_catalog.FLAGS, "guard flag must be catalogued"
    for argv in (["scripts/flag_owner_lint.py"],
                 ["scripts/flip_horizon.py", "--check", "--today", "2026-10-10"]):
        r = subprocess.run([sys.executable, *argv], capture_output=True, text=True, cwd=str(REPO_ROOT))
        assert r.returncode == 0, f"{argv}: {r.stdout!r} {r.stderr!r}"


def test_ac11_docs_mention_variable():
    assert VAR in flags_catalog.FLAGS, "guard flag must be catalogued"
    text = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert VAR in text


def test_ac11_changelog_added_block_mentions_variable():
    from helpers.changelog import read_changelog, require_entry  # noqa: PLC0415

    require_entry(read_changelog(), VAR, block="Added")


# ---------------------------------------------------------------- AC12 (guard)

KEY = "event_log.py::_on_disk_spelling::listdir#0"


def test_ac12_tree_scan_inventory_classifies_listdir_site():
    from bytedigger_engine.conformance import tree_scan_lint  # noqa: PLC0415

    inventory = tree_scan_lint.load_inventory()
    entry = inventory["sites"].get(KEY)
    assert entry is not None, f"{KEY} must be inventoried"
    assert entry.get("class") == "not-a-gate"
    assert isinstance(entry.get("note"), str) and entry["note"].strip()
    package_root = Path(event_log.__file__).resolve().parent
    assert tree_scan_lint.check(package_root, inventory) == []
