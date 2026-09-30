"""Seam: config_provider.memory_db_required (hal#1656 port follow-up).

Upstream (hal#1656) classifies a missing DEFAULT memory.db as a failed learning
injection: a stderr WARNING, the INJECT_FAILED marker and a
`learning_inject_callout_failed` event. That fits a host that always ships its
memory.db; here learning memory is optional, so under the neutral provider an
absent default DB is "not configured" and must stay quiet. A provider that
declares the DB required, or declares nothing, keeps the upstream behaviour.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bytedigger_engine import config_provider
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.workflows import phase_05_inject

import test_GH1471_inject_path_and_visibility as _gh1471

INJECT_FAILED = "<!-- HAL_MEMORY:INJECT_FAILED -->"


@pytest.fixture(autouse=True)
def _foreign_cwd(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    # cwd must be outside the provider's home_root for the upstream branch.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _run(tmp_path: Path):
    log_path = tmp_path / "events.jsonl"
    eng = WorkflowEngine(event_log=EventLog(log_path))
    eng.register("p05", phase_05_inject.phase_05_inject_workflow())
    scratchpad = tmp_path / "scratch"
    result, _ = eng.execute(
        "p05", _gh1471._make_ctx(scratchpad, task_description="optional memory seam check"),
    )
    events = EventLog(log_path).read_all()
    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    return result, events, body


def test_neutral_provider_missing_default_db_is_not_a_failure(tmp_path, capsys):
    config_provider.reset_default_config_provider_factory()
    assert config_provider.memory_db_required() is False

    result, events, body = _run(tmp_path)

    assert result.status == "ok", (result.error_code, result.error)
    assert result.data.get("learning_injection") == "no_match", result.data
    assert INJECT_FAILED not in body
    assert "[memory_db_not_configured]" in body
    assert not [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    assert "WARNING: learning injection unavailable" not in capsys.readouterr().err


class _RequiredProvider(config_provider._DefaultConfigProvider):
    def memory_db_required(self) -> bool:
        return True


def test_provider_requiring_the_db_keeps_the_upstream_failure(tmp_path, capsys):
    config_provider.set_default_config_provider_factory(_RequiredProvider)
    try:
        assert config_provider.memory_db_required() is True
        result, events, body = _run(tmp_path)
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result.status == "ok", (result.error_code, result.error)
    assert result.data.get("learning_injection") == "failed", result.data
    assert INJECT_FAILED in body
    assert [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    assert "WARNING: learning injection unavailable" in capsys.readouterr().err


def test_provider_without_an_opinion_keeps_the_upstream_default():
    class _Minimal:
        pass

    config_provider.set_default_config_provider_factory(_Minimal)
    try:
        assert config_provider.memory_db_required() is True
    finally:
        config_provider.reset_default_config_provider_factory()
