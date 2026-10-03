"""RED tests for bd flip — HAL_RED_MASS_DELETION_ENFORCE defaults ON.

Spec: docs/decisions/2026-10-03-bd-flip-red-mass-deletion.md (AC1..AC7; AC8 is
the sibling-test migration in test_gh282_red_mass_deletion.py and
test_gh1600_red_tests_in_existing_file.py).

Every behavioural test drives the REAL `_commit_red_tests` on a tmp git repo
(the unit under test is never mocked; only the telemetry sink `_emit_safe` is
wrapped to observe events). The ENFORCE env var is never set to "1" here: the
default is the thing under test.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests
from bytedigger_engine.contracts import WorkflowContext

REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_TOKENS = (
    "HAL_RED_MASS_DELETION_GATE",
    "HAL_RED_MASS_DELETION_ENFORCE",
    "HAL_RED_MASS_DELETION_MAX_LINES",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for tok in ENV_TOKENS:
        monkeypatch.delenv(tok, raising=False)
        suffix = tok[len("HAL_"):]
        for prefix in ("BD_", "BYTEDIGGER_"):
            monkeypatch.delenv(prefix + suffix, raising=False)


# ─── helpers (copied from test_gh282_red_mass_deletion.py) ────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _lines(n: int, prefix: str = "L") -> str:
    return "".join(f"{prefix}{i}\n" for i in range(n))


def _make_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths, cycle: int = 1, spec_path: str | None = None):
    prev = MagicMock()
    prev.data = {"red_test_paths": red_test_paths, "cycle": cycle}
    if spec_path is not None:
        prev.data["spec_path"] = spec_path
    return prev


def _setup_deletion_repo(tmp_path: Path, kept_lines: int = 100, pragma: bool = False):
    """Committed tests/wanted.py at 300 lines, then cut to `kept_lines`
    (default 100 -> deleted=200 >= default max 120 and ratio >= 50%)."""
    repo = _make_repo(tmp_path)
    _commit_file(repo, "tests/wanted.py", _lines(300), "base")
    body = _lines(kept_lines)
    if pragma:
        body += "# red-mass-deletion: allow\n"
    (repo / "tests" / "wanted.py").write_text(body)
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Spec\n\n## Files\n- tests/wanted.py\n\n## End\n")
    return repo, spec_file


def _run(tmp_path: Path, monkeypatch, repo: Path, spec_file: Path):
    from bytedigger_engine.workflows import phase_5_implement

    captured: list[tuple[str, dict]] = []

    def fake_emit(event_name, payload, severity=None):
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(red_test_paths="<missing>", spec_path=str(spec_file))
    result = _commit_red_tests(ctx, prev)
    return result, captured


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — catalog entry
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_catalog_entry_is_default_on_gate_with_kill_switch_description() -> None:
    from bytedigger_engine import flags_catalog

    entry = flags_catalog.FLAGS["HAL_RED_MASS_DELETION_ENFORCE"]
    assert entry["kind"] == "gate"
    assert entry["default"] == "1"
    desc = entry["description"]
    assert "=0" in desc, f"description must name the =0 kill-switch: {desc!r}"
    for token in ("flip-by", "kill-by", "retire-by"):
        assert token not in desc, f"description must carry no horizon token {token!r}: {desc!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — production side effect: default (env unset) hard-blocks
# ═══════════════════════════════════════════════════════════════════════════


def test_ac2_default_blocks_mass_deletion_with_error_code_and_blocked_event(
    tmp_path: Path, monkeypatch
) -> None:
    repo, spec_file = _setup_deletion_repo(tmp_path)

    result, captured = _run(tmp_path, monkeypatch, repo, spec_file)

    assert result.status == "error"
    assert result.error_code == "E_RED_MASS_DELETION"
    assert result.recoverable is False
    blocked = [n for (n, _p) in captured if n == "red_mass_deletion_blocked"]
    assert len(blocked) == 1


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — =0 kill-switch restores warn-only
# ═══════════════════════════════════════════════════════════════════════════


def test_ac3_enforce_zero_restores_warn_only(tmp_path: Path, monkeypatch) -> None:
    repo, spec_file = _setup_deletion_repo(tmp_path)
    monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "0")

    result, captured = _run(tmp_path, monkeypatch, repo, spec_file)

    assert result.error_code != "E_RED_MASS_DELETION"
    events = [p for (n, p) in captured if n == "red_mass_deletion_check"]
    assert len(events) == 1
    assert events[0]["enforced"] is False
    assert events[0]["violations_n"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — pragma escape unchanged under the default
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4_default_pragma_escape_still_exempts(tmp_path: Path, monkeypatch) -> None:
    repo, spec_file = _setup_deletion_repo(tmp_path, pragma=True)

    result, captured = _run(tmp_path, monkeypatch, repo, spec_file)

    assert result.error_code != "E_RED_MASS_DELETION"
    events = [p for (n, p) in captured if n == "red_mass_deletion_check"]
    assert len(events) == 1
    assert events[0]["enforced"] is True, "default must be enforcing (pragma is what exempts)"
    assert events[0]["exempted_n"] == 1
    assert events[0]["violations_n"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — below threshold: no false positive, enforcement visible in the event
# ═══════════════════════════════════════════════════════════════════════════


def test_ac5_default_below_threshold_not_blocked(tmp_path: Path, monkeypatch) -> None:
    repo, spec_file = _setup_deletion_repo(tmp_path, kept_lines=290)  # deletes 10

    result, captured = _run(tmp_path, monkeypatch, repo, spec_file)

    assert result.error_code != "E_RED_MASS_DELETION"
    events = [p for (n, p) in captured if n == "red_mass_deletion_check"]
    assert len(events) == 1
    assert events[0]["enforced"] is True
    assert events[0]["violations_n"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — GATE=0 turns the whole gate off even though ENFORCE defaults ON
# ═══════════════════════════════════════════════════════════════════════════


def test_ac6_gate_zero_turns_gate_fully_off(tmp_path: Path, monkeypatch) -> None:
    repo, spec_file = _setup_deletion_repo(tmp_path)
    monkeypatch.setenv("HAL_RED_MASS_DELETION_GATE", "0")

    result, captured = _run(tmp_path, monkeypatch, repo, spec_file)

    assert result.error_code != "E_RED_MASS_DELETION"
    assert [n for (n, _p) in captured if n == "red_mass_deletion_check"] == []


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — horizon guard stays green (token and ledger line removed together)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_horizon_token_and_ledger_entry_removed_together() -> None:
    import datetime
    import importlib.util
    import inspect
    import json

    from bytedigger_engine import flags_catalog
    from bytedigger_engine.workflows import phase_5_implement

    flag = "HAL_RED_MASS_DELETION_ENFORCE"
    spec = importlib.util.spec_from_file_location(
        "flip_horizon_under_test", REPO_ROOT / "scripts" / "flip_horizon.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ledger = json.loads((REPO_ROOT / "scripts" / "flip_horizon_ledger.json").read_text())
    problems = mod.check(flags_catalog.FLAGS, ledger, datetime.date(2026, 10, 3))

    # (a) no guard problem line names the flag
    assert not [p for p in problems if flag in p], problems
    # (b) the ledger key is gone
    assert flag not in ledger, "ledger entry must be deleted together with the catalog token"
    # (c) the mass-deletion read site carries no flip-by token (other flags'
    # tokens in the same function are unrelated and legitimate)
    lines = inspect.getsource(phase_5_implement._commit_red_tests).splitlines()
    start = next(i for i, ln in enumerate(lines) if "HAL_RED_MASS_DELETION_GATE" in ln)
    end = next(
        i for i, ln in enumerate(lines) if i >= start and "_red_mass_deletion_violations(" in ln
    )
    block = "\n".join(lines[start : end + 1])
    assert block, "mass-deletion block slice must be non-empty"
    assert "HAL_RED_MASS_DELETION_ENFORCE" in block
    assert "flip-by" not in block


# ═══════════════════════════════════════════════════════════════════════════
# AC9 — only exactly "0" disables; "false" still enforces
# ═══════════════════════════════════════════════════════════════════════════


def test_ac9_enforce_false_string_still_enforces(tmp_path: Path, monkeypatch) -> None:
    repo, spec_file = _setup_deletion_repo(tmp_path)
    monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "false")

    result, captured = _run(tmp_path, monkeypatch, repo, spec_file)

    assert result.status == "error"
    assert result.error_code == "E_RED_MASS_DELETION"
    events = [p for (n, p) in captured if n == "red_mass_deletion_check"]
    assert len(events) == 1
    assert events[0]["enforced"] is True


# ═══════════════════════════════════════════════════════════════════════════
# AC10 — BD_ alias of the kill-switch restores warn-only
# ═══════════════════════════════════════════════════════════════════════════


def test_ac10_bd_alias_enforce_zero_restores_warn_only(tmp_path: Path, monkeypatch) -> None:
    repo, spec_file = _setup_deletion_repo(tmp_path)
    monkeypatch.setenv("BD_RED_MASS_DELETION_ENFORCE", "0")

    result, captured = _run(tmp_path, monkeypatch, repo, spec_file)

    assert result.error_code != "E_RED_MASS_DELETION"
    events = [p for (n, p) in captured if n == "red_mass_deletion_check"]
    assert len(events) == 1
    assert events[0]["enforced"] is False
    assert events[0]["violations_n"] >= 1
