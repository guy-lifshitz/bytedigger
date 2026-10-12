"""RED tests for bd#290: the unregistered-flag check is a permanent warning and
HAL_FLAG_UNREGISTERED_ENFORCE is retired.

Spec: docs/decisions/2026-10-13-bd290-unregistered-flag-warn-permanent.md

AC1 test_ac1_engine_tree_has_no_unregistered_tokens
AC2 test_ac2_catalog_retires_enforce_registers_two_tokens
AC3 test_ac3_real_step_warns_and_continues_without_enforce
AC4 test_ac4_enforce_env_is_no_longer_read
AC5 test_ac5_kill_switch_emits_gate_disabled_and_no_warn
AC6 test_ac6_error_codes_retired_and_registry_consistent
AC7 test_ac7_flip_horizon_does_not_name_retired_flag
AC8 test_ac8_no_remaining_reference_in_engine_sources

Harness: AC3-AC5 run the real step against a real tmp git repo (no mocks of the
unit under test). Events go through the telemetry slot the engine's own emission
writes to; the process-wide slot/config factory are pre-staged and restored
(workflows.md 1i, no timing). Units under test are imported lazily inside bodies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_PKG_ROOT = Path(__file__).resolve().parents[1] / "bytedigger_engine"
REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PY_ROOT = Path(__file__).resolve().parents[1]

RETIRED_FLAG = "HAL_FLAG_UNREGISTERED_ENFORCE"
RETIRED_CODES = ("E_FLAG_UNREGISTERED", "E_FLAG_UNREGISTERED_CAP2")
TOKEN = "HAL_NO_SUCH_FLAG_X"

_AMBIENT_GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT")

_NEVER_MATCHING_RULES = (
    "rules:\n"
    "  - id: bd290-never-matches\n"
    "    pattern: bd290_never_matches_xyz()\n"
    "    message: placeholder rule for the bd290 test harness\n"
    "    languages: [python]\n"
    "    severity: ERROR\n"
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def of(self, event_type: str) -> list:
        return [p for (t, p, _) in self.events if t == event_type]


class _RecordingConfig:
    """Delegates to the real provider and records every flag() env-var read."""

    def __init__(self, real, seen: list) -> None:
        self._real = real
        self._seen = seen

    def flag(self, env_var: str) -> bool:
        self._seen.append(env_var)
        return self._real.flag(env_var)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Pre-stages env, telemetry slot, config provider and the green_lint rules file."""
    from bytedigger_engine import config_provider, telemetry_ctx
    from bytedigger_engine.workflows import phase_5_implement as p5

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for var in _AMBIENT_GIT_VARS:
        monkeypatch.delenv(var, raising=False)
    for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
        for name in ("FLAG_UNREGISTERED_ENFORCE", "FLAG_UNREGISTERED_GATE"):
            monkeypatch.delenv(prefix + name, raising=False)

    # green_lint rules file may be absent from the tree; the step returns early
    # (rules_file_missing) without it, before the flag check is reached.
    rules_dir = Path(p5.__file__).parent.parent / "scripts" / "green_lint"
    rules_file = rules_dir / "rules.yml"
    created_dir = not rules_dir.exists()
    created_file = not rules_file.exists()
    rules_dir.mkdir(parents=True, exist_ok=True)
    if created_file:
        rules_file.write_text(_NEVER_MATCHING_RULES, encoding="utf-8")

    config_provider.reset_default_config_provider_factory()
    telemetry_ctx.clear_current_run()
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="bd290", step_name="bd290")

    seen_flags: list = []
    real = config_provider.get_config()
    config_provider.set_default_config_provider_factory(lambda: _RecordingConfig(real, seen_flags))

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    red_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "feature_x.py").write_text(
        'import os\n\nVALUE = os.environ.get("%s")\n' % TOKEN, encoding="utf-8",
    )

    try:
        yield {"p5": p5, "repo": repo, "red_sha": red_sha, "log": log, "seen_flags": seen_flags,
               "scratch": tmp_path / "scratch"}
    finally:
        telemetry_ctx.clear_current_run()
        config_provider.reset_default_config_provider_factory()
        if created_file:
            rules_file.unlink(missing_ok=True)
        if created_dir:
            try:
                rules_dir.rmdir()
            except OSError:
                pass


def _run_step(h: dict):
    from bytedigger_engine.contracts import StepResult, WorkflowContext

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(h["scratch"]), "git_cwd": str(h["repo"])},
        question="test", session_id="test-bd290", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={"red_commit_sha": h["red_sha"], "git_cwd": str(h["repo"]), "red_test_paths": []},
        duration_ms=0, step_name="prev",
    )
    return h["p5"]._verify_green_lint_rules(ctx, prev)


def _assert_warn_and_continue(h: dict, result) -> None:
    assert result.status == "ok", (
        f"status={result.status!r} error_code={result.error_code!r} error={result.error!r}"
    )
    code = result.error_code or ""
    assert not code.startswith("E_FLAG_UNREGISTERED"), f"unexpected stop code {code!r}"
    warns = h["log"].of("flag_unregistered_warn")
    assert len(warns) == 1, f"expected exactly one flag_unregistered_warn, got {h['log'].events!r}"
    assert TOKEN in warns[0]["tokens"], warns[0]
    data = result.data or {}
    assert any(TOKEN in str(w) for w in data.get("flag_unregistered_warnings", [])), data
    assert RETIRED_FLAG not in h["seen_flags"], (
        f"the retired flag is still read through the config provider: {h['seen_flags']!r}"
    )


# ---------------------------------------------------------------------------
# AC1 / AC2
# ---------------------------------------------------------------------------

def test_ac1_engine_tree_has_no_unregistered_tokens() -> None:
    from bytedigger_engine import flags_catalog

    files = sorted(str(p) for p in ENGINE_PKG_ROOT.rglob("*.py"))
    assert files, f"no .py files found under {ENGINE_PKG_ROOT}"
    assert flags_catalog.unregistered_tokens_in_files(files) == []


def test_ac2_catalog_retires_enforce_registers_two_tokens() -> None:
    from bytedigger_engine.flags_catalog import FLAGS

    assert RETIRED_FLAG not in FLAGS
    assert "HAL_SECURITY_RULES_PATH" in FLAGS
    assert "HAL_STATUS_PROBE_URL" in FLAGS
    gate = FLAGS["HAL_FLAG_UNREGISTERED_GATE"]
    assert gate["kind"] == "gate"
    assert gate["default"] == "1"


# ---------------------------------------------------------------------------
# AC3 - AC5 (real step, real tmp git repo)
# ---------------------------------------------------------------------------

def test_ac3_real_step_warns_and_continues_without_enforce(harness) -> None:
    result = _run_step(harness)
    _assert_warn_and_continue(harness, result)


def test_ac4_enforce_env_is_no_longer_read(harness, monkeypatch) -> None:
    monkeypatch.setenv(RETIRED_FLAG, "1")
    result = _run_step(harness)
    _assert_warn_and_continue(harness, result)


def test_ac5_kill_switch_emits_gate_disabled_and_no_warn(harness, monkeypatch) -> None:
    from bytedigger_engine.flags_catalog import FLAGS

    monkeypatch.setenv("HAL_FLAG_UNREGISTERED_GATE", "0")
    result = _run_step(harness)
    assert result.status == "ok", (
        f"status={result.status!r} error_code={result.error_code!r} error={result.error!r}"
    )
    disabled = [
        p for p in harness["log"].of("gate_disabled")
        if p.get("gate") == "HAL_FLAG_UNREGISTERED_GATE"
    ]
    assert len(disabled) == 1, harness["log"].events
    assert harness["log"].of("flag_unregistered_warn") == []
    # Kill switch stays the only switch: the retired rollout flag is gone.
    assert RETIRED_FLAG not in FLAGS
    assert RETIRED_FLAG not in harness["seen_flags"]


# ---------------------------------------------------------------------------
# AC6
# ---------------------------------------------------------------------------

def test_ac6_error_codes_retired_and_registry_consistent() -> None:
    from bytedigger_engine import error_codes

    for code in RETIRED_CODES:
        assert code not in error_codes.ERROR_CODES, f"{code} still registered"

    for md in (ENGINE_PY_ROOT / "ERROR_CODES.md", ENGINE_PKG_ROOT / "ERROR_CODES.md"):
        text = md.read_text(encoding="utf-8")
        for code in RETIRED_CODES:
            assert code not in text, f"{code} still listed in {md}"

    unregistered, dead = error_codes.check(ENGINE_PKG_ROOT)
    assert unregistered == set(), f"unregistered codes: {sorted(unregistered)}"
    assert dead == set(), f"dead codes: {sorted(dead)}"


# ---------------------------------------------------------------------------
# AC7
# ---------------------------------------------------------------------------

def test_ac7_flip_horizon_does_not_name_retired_flag() -> None:
    script = REPO_ROOT / "scripts" / "flip_horizon.py"
    ledger_path = REPO_ROOT / "scripts" / "flip_horizon_ledger.json"
    r = subprocess.run(
        [sys.executable, str(script), "--check", "--today", "2026-11-04"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60,
    )
    assert RETIRED_FLAG not in r.stdout, r.stdout
    assert RETIRED_FLAG not in r.stderr, r.stderr
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert RETIRED_FLAG not in ledger


# ---------------------------------------------------------------------------
# AC8
# ---------------------------------------------------------------------------

def test_ac8_no_remaining_reference_in_engine_sources() -> None:
    needles = (RETIRED_FLAG, "E_FLAG_UNREGISTERED")
    hits: list[str] = []
    files = sorted(ENGINE_PKG_ROOT.rglob("*.py"))
    assert files, f"no .py files found under {ENGINE_PKG_ROOT}"
    for p in files:
        text = p.read_text(encoding="utf-8", errors="ignore")
        for needle in needles:
            if needle in text:
                hits.append(f"{p.relative_to(ENGINE_PKG_ROOT)}: {needle}")
    assert hits == [], hits
