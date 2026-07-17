"""Tests for GH878 — foreign_state_dirname seam (.bytedigger default),
BD_/BYTEDIGGER_ env-alias layer, neutral user-facing strings.

Ported to bytedigger (the neutral OSS build). The HAL-only ACs from the
upstream suite are intentionally omitted here because they depend on
`hal_config_provider` / the `--neutral` run.py flag / the "## Org Memory"
prompt rename, none of which exist in the neutral build:
  AC2  — HAL provider byte-compat (.hal-build override)      [hal_config_provider]
  AC9  — run.py/hal_config_provider --neutral flag           [hal_config_provider]
  AC11 — phase_05_inject "## Org Memory" heading             [separate lot]

Retained (all exercise the neutral seam + alias layer bytedigger ships):
  AC1  — neutral provider: foreign_state_dirname()==".bytedigger"; relpaths derive from it
  AC3  — foreign-cwd fallbacks (event_log/reject_log/engine) route the seam at call time
  AC4  — phase_0_research._resolve_scratchpad routes the seam
  AC5  — phase_8 preserve_events_log + PERSIST_ARTIFACTS route the seam
  AC6  — BD_/BYTEDIGGER_ alias layer on flag/gate_enabled/timeout_ms/binary/int_value/path/env_opt
  AC7  — env_mapping() Mapping view (alias-aware, synthesizes HAL_<X> keys)
  AC8  — 5 raw os.environ reads route through env_mapping()
  AC10 — prod-literal pins (source-inspection, canonical file read)
  AC12 — _communicate_legacy alias-message + BD_ flag gate

§1i: any test registering a stub factory restores the default factory in a
try/finally via config_provider.reset_default_config_provider_factory().
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_ENGINE_PY_ROOT = HERE.parent
# sys.path (engine_py root + workflows/) is already set by conftest.py's
# conftest-import-time singleton. Do NOT add sys.path.insert here.

import config_provider  # noqa: E402
from config_provider import (  # noqa: E402
    get_config,
    reset_default_config_provider_factory,
    set_default_config_provider_factory,
)


# ─── shared stub provider (stub-unpassable proof for AC3/AC4) ────────────────


class _StubForeignDirProvider(config_provider._DefaultConfigProvider):
    """Overrides only foreign_state_dirname — proves consumers consult the
    seam at call time rather than re-hardcoding '.bytedigger'."""

    def foreign_state_dirname(self) -> str:
        return ".customdir"


@pytest.fixture()
def _restore_provider():
    """§1i snapshot/restore around factory-swap tests."""
    yield
    reset_default_config_provider_factory()


# ─── AC1: neutral provider foreign_state_dirname() + derived relpaths ────────


def test_ac1_neutral_provider_foreign_state_dirname(_restore_provider):
    reset_default_config_provider_factory()
    provider = config_provider.default_config_provider()
    dirname = getattr(provider, "foreign_state_dirname", None)
    assert dirname is not None, (
        "AC1 FAIL: _DefaultConfigProvider has no foreign_state_dirname()"
    )
    assert dirname() == ".bytedigger", f"AC1: expected '.bytedigger', got {dirname()!r}"


def test_ac1_neutral_provider_relpaths_derive_from_seam(_restore_provider):
    reset_default_config_provider_factory()
    assert config_provider.event_log_relpath() == ".bytedigger/events.jsonl"
    assert config_provider.reject_log_relpath() == ".bytedigger/reject-reasons.jsonl"
    assert config_provider.rework_log_relpath() == ".bytedigger/build-rework-log.jsonl"
    assert config_provider.build_runs_relpath() == ".bytedigger/build-runs"
    assert config_provider.dbos_db_relpath() == ".bytedigger/dbos.sqlite"
    assert config_provider.state_dir_prefix() == ".bytedigger/"
    provider = get_config()
    assert provider.memory_db_path().endswith(str(Path(".bytedigger") / "memory.db"))  # type: ignore[attr-defined]


# ─── AC3: foreign-cwd fallbacks route the seam at call time ──────────────────


def test_ac3_event_log_default_path_uses_seam(tmp_path, monkeypatch, _restore_provider):
    import event_log  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.chdir(tmp_path)
    result = event_log.default_log_path()
    expected = tmp_path / ".bytedigger" / "events.jsonl"
    assert result == expected, f"AC3 event_log: expected {expected}, got {result}"


def test_ac3_reject_log_default_path_uses_seam(tmp_path, monkeypatch, _restore_provider):
    import reject_log  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.chdir(tmp_path)
    result = reject_log.default_reject_log_path()
    expected = tmp_path / ".bytedigger" / "reject-reasons.jsonl"
    assert result == expected, f"AC3 reject_log: expected {expected}, got {result}"


def test_ac3_engine_default_rework_log_path_uses_seam(tmp_path, monkeypatch, _restore_provider):
    import engine  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.chdir(tmp_path)
    result = engine.default_rework_log_path()
    expected = tmp_path / ".bytedigger" / "build-rework-log.jsonl"
    assert result == expected, f"AC3 engine: expected {expected}, got {result}"


def test_ac3_stub_provider_relocates_all_three_fallbacks(tmp_path, monkeypatch, _restore_provider):
    """Stub-unpassable proof: a provider whose foreign_state_dirname() returns
    '.customdir' must relocate event_log/reject_log/engine fallbacks — this
    fails if the seam is re-hardcoded instead of called."""
    import event_log  # noqa: PLC0415
    import reject_log  # noqa: PLC0415
    import engine  # noqa: PLC0415

    set_default_config_provider_factory(_StubForeignDirProvider)
    monkeypatch.chdir(tmp_path)

    assert event_log.default_log_path() == tmp_path / ".customdir" / "events.jsonl"
    assert reject_log.default_reject_log_path() == tmp_path / ".customdir" / "reject-reasons.jsonl"
    assert engine.default_rework_log_path() == tmp_path / ".customdir" / "build-rework-log.jsonl"


# ─── AC4: phase_0_research._resolve_scratchpad routes the seam ───────────────


def test_ac4_resolve_scratchpad_neutral_uses_seam(tmp_path, monkeypatch, _restore_provider):
    import phase_0_research  # noqa: PLC0415

    class _Ctx:
        org_config = {}

    reset_default_config_provider_factory()
    monkeypatch.chdir(tmp_path)
    result = phase_0_research._resolve_scratchpad(_Ctx())
    expected = (tmp_path / ".bytedigger").resolve()
    assert result == expected, f"AC4: expected {expected}, got {result}"


def test_ac4_resolve_scratchpad_stub_provider_uses_seam(tmp_path, monkeypatch, _restore_provider):
    import phase_0_research  # noqa: PLC0415

    class _Ctx:
        org_config = {}

    set_default_config_provider_factory(_StubForeignDirProvider)
    monkeypatch.chdir(tmp_path)
    result = phase_0_research._resolve_scratchpad(_Ctx())
    expected = (tmp_path / ".customdir").resolve()
    assert result == expected, f"AC4 stub: expected {expected}, got {result}"


# ─── AC5: phase_8 preserve_events_log + PERSIST_ARTIFACTS route the seam ─────


def test_ac5_preserve_events_log_reads_seam_dirname(tmp_path, monkeypatch, _restore_provider):
    """Real event-log content under <working_dir>/.bytedigger/events.jsonl is
    found via the seam and copied to the post-deploy scratchpad."""
    from contracts import WorkflowContext  # noqa: PLC0415
    from engine import WorkflowEngine  # noqa: PLC0415
    from phase_8_post_deploy import phase_8_post_deploy_workflow  # noqa: PLC0415

    reset_default_config_provider_factory()
    working_dir = tmp_path / "repo"
    seam_dir = working_dir / ".bytedigger"
    seam_dir.mkdir(parents=True)
    known_content = '{"event":"seam-routed"}\n'
    (seam_dir / "events.jsonl").write_text(known_content)

    scratchpad = tmp_path / "scratch"
    ctx = WorkflowContext(
        tenant_id="oss",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "working_dir": str(working_dir)},
        question="cleanup",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", ctx)

    dest = scratchpad / "post-deploy" / "events.jsonl"
    assert dest.exists(), (
        "AC5 FAIL: dest not created — preserve_events_log did not route "
        "foreign_state_dirname()"
    )
    assert dest.read_text() == known_content


def test_ac5_persist_artifacts_working_dir_entry_dirname_agnostic(_restore_provider):
    """AC5 PERSIST_ARTIFACTS: the working_dir entry's relpath must not embed a
    state-dir literal (the dirname is resolved via foreign_state_dirname() at
    call time)."""
    from phase_8_post_deploy import PERSIST_ARTIFACTS  # noqa: PLC0415

    working_dir_entries = [relpath for kind, relpath in PERSIST_ARTIFACTS if kind == "working_dir"]
    assert working_dir_entries == ["events.jsonl"], (
        f"AC5 FAIL: expected PERSIST_ARTIFACTS working_dir entry to be bare "
        f"'events.jsonl', got {working_dir_entries!r}"
    )


# ─── AC6: BD_/BYTEDIGGER_ env-alias layer ────────────────────────────────────


def test_ac6_flag_honors_bd_alias_when_hal_unset(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_X", raising=False)
    monkeypatch.setenv("BD_X", "1")
    assert config_provider.flag("HAL_X") is True, (
        "AC6 FAIL: flag('HAL_X') does not honor BD_X alias"
    )


def test_ac6_gate_enabled_honors_bd_alias(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    provider = get_config()
    monkeypatch.delenv("HAL_Y", raising=False)
    monkeypatch.setenv("BD_Y", "0")
    assert provider.gate_enabled("HAL_Y") is False, (
        "AC6 FAIL: gate_enabled('HAL_Y') does not honor BD_Y=0 alias"
    )


def test_ac6_bytedigger_prefix_honored_when_bd_and_hal_unset(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_Z", raising=False)
    monkeypatch.delenv("BD_Z", raising=False)
    monkeypatch.setenv("BYTEDIGGER_Z", "1")
    assert config_provider.flag("HAL_Z") is True, (
        "AC6 FAIL: flag('HAL_Z') does not honor BYTEDIGGER_Z alias"
    )


def test_ac6_hal_set_wins_over_bd_alias(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    monkeypatch.setenv("HAL_X", "0")
    monkeypatch.setenv("BD_X", "1")
    assert config_provider.flag("HAL_X") is False, (
        "AC6 FAIL: HAL_X must win over BD_X when both set (byte-compat)"
    )


def test_ac6_typed_accessors_honor_bd_only(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    provider = get_config()
    monkeypatch.delenv("HAL_TMS", raising=False)
    monkeypatch.setenv("BD_TMS", "777")
    assert provider.timeout_ms("HAL_TMS", 100) == 777, "AC6 timeout_ms BD_ alias"

    monkeypatch.delenv("HAL_BIN", raising=False)
    monkeypatch.setenv("BD_BIN", "custom-bin")
    assert provider.binary("HAL_BIN", "default-bin") == "custom-bin", "AC6 binary BD_ alias"

    monkeypatch.delenv("HAL_IV", raising=False)
    monkeypatch.setenv("BD_IV", "42")
    assert provider.int_value("HAL_IV", 0) == 42, "AC6 int_value BD_ alias"  # type: ignore[attr-defined]

    monkeypatch.delenv("HAL_PTH", raising=False)
    monkeypatch.setenv("BD_PTH", "/tmp/bd-path")
    assert provider.path("HAL_PTH", Path("/default")) == Path("/tmp/bd-path"), "AC6 path BD_ alias"

    monkeypatch.delenv("HAL_OPT", raising=False)
    monkeypatch.setenv("BD_OPT", "opt-val")
    assert config_provider.env_opt("HAL_OPT") == "opt-val", "AC6 env_opt BD_ alias"


def test_ac6_hal_empty_string_wins_over_bd_alias(monkeypatch, _restore_provider):
    """Byte-compat pin: an explicitly-set-but-empty HAL_X (name in environ) still
    counts as 'set' per _aliased_env_get's `if name in environ` check — it must
    win over BD_X, matching os.environ.get(...) semantics untouched by the
    alias layer."""
    reset_default_config_provider_factory()
    monkeypatch.setenv("HAL_X", "")
    monkeypatch.setenv("BD_X", "1")
    assert config_provider.flag("HAL_X") is False, (
        "AC6 FAIL: empty-string HAL_X must win over BD_X=1 (byte-compat)"
    )
    assert config_provider.env_opt("HAL_X") == "", (
        "AC6 FAIL: env_opt('HAL_X') must return '' (the set-but-empty HAL_ value), "
        "not fall through to BD_X"
    )


def test_ac6_bd_empty_string_masks_bytedigger_prefix(monkeypatch, _restore_provider):
    """Prefix-order semantics: an explicitly-set-but-empty BD_X masks BYTEDIGGER_X
    (BD_ is consulted before BYTEDIGGER_ per _ENV_ALIAS_PREFIXES order; BD_X being
    present — even empty — short-circuits before BYTEDIGGER_X is ever read)."""
    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_Q", raising=False)
    monkeypatch.setenv("BD_Q", "")
    monkeypatch.setenv("BYTEDIGGER_Q", "1")
    assert config_provider.env_opt("HAL_Q") == "", (
        "AC6 FAIL: BD_Q='' must mask BYTEDIGGER_Q='1' (BD_ prefix-order wins, "
        "even when the BD_ value itself is empty)"
    )


def test_ac6_non_hal_prefixed_names_get_no_alias(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    monkeypatch.delenv("OTHER_X", raising=False)
    monkeypatch.setenv("BD_OTHER_X", "1")
    assert config_provider.flag("OTHER_X") is False, (
        "AC6 FAIL: non-HAL_-prefixed names must NOT get BD_ alias fallback"
    )


# ─── AC7: env_mapping() Mapping view ──────────────────────────────────────────


def test_ac7_env_mapping_reads_bd_fallback_and_synthesizes_key(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_MX", raising=False)
    monkeypatch.setenv("BD_MX", "mapped-val")

    env_mapping = getattr(config_provider, "env_mapping", None)
    assert env_mapping is not None, "AC7 FAIL: config_provider.env_mapping does not exist"

    m = env_mapping()
    assert m["HAL_MX"] == "mapped-val"
    assert "HAL_MX" in m
    with pytest.raises(KeyError):
        _ = m["HAL_TOTALLY_UNSET_KEY_GH878"]

    materialized = dict(m)
    assert materialized["HAL_MX"] == "mapped-val"
    assert materialized["BD_MX"] == "mapped-val"


def test_ac7_env_mapping_hal_set_wins_in_dict_view(monkeypatch, _restore_provider):
    reset_default_config_provider_factory()
    monkeypatch.setenv("HAL_MX2", "hal-value")
    monkeypatch.setenv("BD_MX2", "bd-value")

    env_mapping = getattr(config_provider, "env_mapping", None)
    assert env_mapping is not None, "AC7 FAIL: config_provider.env_mapping does not exist"

    m = env_mapping()
    materialized = dict(m)
    assert materialized["HAL_MX2"] == "hal-value"


# ─── AC8: 5 raw os.environ reads route through env_mapping() ─────────────────


def test_ac8_resolve_backend_sees_bd_alias(monkeypatch, _restore_provider):
    import llm_subprocess  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    monkeypatch.setenv("BD_RUNNER_BACKEND", "claude-subprocess")

    env_mapping = getattr(config_provider, "env_mapping", None)
    assert env_mapping is not None, "AC8 FAIL: config_provider.env_mapping does not exist"

    resolved, source = llm_subprocess._resolve_backend(None, env_mapping())
    assert resolved == "claude-subprocess" and source == "env", (
        f"AC8 FAIL: _resolve_backend did not see BD_RUNNER_BACKEND via env_mapping "
        f"(got {resolved!r}, {source!r})"
    )


def test_ac8_resolve_request_dir_sees_bd_alias(tmp_path, monkeypatch, _restore_provider):
    import llm_subprocess  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_RUNNER_REQUEST_DIR", raising=False)
    monkeypatch.setenv("BD_RUNNER_REQUEST_DIR", str(tmp_path / "req-dir"))

    env_mapping = getattr(config_provider, "env_mapping", None)
    assert env_mapping is not None, "AC8 FAIL: config_provider.env_mapping does not exist"

    resolved, source = llm_subprocess._resolve_request_dir(env_mapping())
    assert resolved == str(tmp_path / "req-dir") and source == "env", (
        f"AC8 FAIL: _resolve_request_dir did not see BD_RUNNER_REQUEST_DIR via "
        f"env_mapping (got {resolved!r}, {source!r})"
    )


def test_ac8_resolve_gate_floor_sees_bd_alias(monkeypatch, _restore_provider):
    import llm_subprocess  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)
    monkeypatch.setenv("BD_GATE_MODEL_FLOOR", "opus")

    env_mapping = getattr(config_provider, "env_mapping", None)
    assert env_mapping is not None, "AC8 FAIL: config_provider.env_mapping does not exist"

    floor = llm_subprocess._resolve_gate_floor(env_mapping())
    assert floor == "opus", f"AC8 FAIL: _resolve_gate_floor did not see BD_GATE_MODEL_FLOOR (got {floor!r})"


def test_ac8_audit_gate_default_env_sees_bd_kill_switch(tmp_path, monkeypatch, _restore_provider):
    from audit_gate import scan_audit_violation  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_ENGINE_PY_AUDIT_GATE", raising=False)
    monkeypatch.setenv("BD_ENGINE_PY_AUDIT_GATE", "0")

    prod = tmp_path / "SYSTEM" / "cli" / "build" / "engine_py" / "prod_module.py"
    prod.parent.mkdir(parents=True, exist_ok=True)
    prod.write_text("# prod\n")

    result = scan_audit_violation(
        [str(prod)], "refactor: touch prod",
        exists=os.path.exists,
        read_text=lambda p: Path(p).read_text(encoding="utf-8"),
    )
    assert result.blocked is False and result.escape == "kill_switch", (
        f"AC8 FAIL: scan_audit_violation(env=None default) did not honor "
        f"BD_ENGINE_PY_AUDIT_GATE=0 kill-switch (got blocked={result.blocked!r}, "
        f"escape={result.escape!r})"
    )


def test_ac8_test_subprocess_env_synthesizes_bd_alias(tmp_path, monkeypatch, _restore_provider):
    from lib.plugins.disk_truth.test_runner import test_subprocess_env  # noqa: PLC0415

    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_FOO", raising=False)
    monkeypatch.setenv("BD_FOO", "foo-val")

    env = test_subprocess_env(tmp_path)
    assert env.get("HAL_DIR") == str(tmp_path)
    assert env.get("HAL_FOO") == "foo-val", (
        f"AC8 FAIL: test_subprocess_env did not synthesize HAL_FOO from BD_FOO "
        f"(got {env.get('HAL_FOO')!r})"
    )


# ─── AC10: prod-literal pins (source-inspection, canonical file read) ────────


def _read_module_source(relpath: str) -> str:
    return (_ENGINE_PY_ROOT / relpath).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relpath",
    [
        "event_log.py",
        "engine.py",
        "reject_log.py",
        "workflows/phase_0_research.py",
        "workflows/phase_8_post_deploy.py",
    ],
)
def test_ac10_no_hal_build_literal_remains(relpath):
    src = _read_module_source(relpath)
    assert ".hal-build" not in src, (
        f"AC10 FAIL: {relpath} still contains a '.hal-build' literal"
    )


def test_ac10_llm_subprocess_raw_env_literals_removed():
    src = _read_module_source("llm_subprocess.py")
    assert "_resolve_request_dir(os.environ)" not in src
    assert "_resolve_backend(backend, os.environ)" not in src
    assert "os.environ if env is None" not in src
    assert "BD_ALLOW_LEGACY_COMMUNICATE" in src, (
        "AC10 FAIL: llm_subprocess.py legacy-communicate message does not "
        "mention BD_ALLOW_LEGACY_COMMUNICATE"
    )


def test_ac10_audit_gate_default_param_literal_removed():
    src = _read_module_source("audit_gate.py")
    assert "env=os.environ" not in src, (
        "AC10 FAIL: audit_gate.py still has env=os.environ default param"
    )


# ─── AC12: _communicate_legacy alias-message + BD_ flag gate ─────────────────


def test_ac12_communicate_legacy_message_mentions_bd_alias():
    src = _read_module_source("llm_subprocess.py")
    assert (
        "Route through stream-json (omit --output-format) or set "
        "BD_ALLOW_LEGACY_COMMUNICATE=1 (alias: HAL_ALLOW_LEGACY_COMMUNICATE=1)."
    ) in src, "AC12 FAIL: _communicate_legacy assert message not updated"


def test_ac12_flag_reaches_via_bd_alias_outside_pytest_context(monkeypatch, _restore_provider):
    """Simulates the non-test-context branch of the _communicate_legacy assert:
    with PYTEST_CURRENT_TEST absent and only BD_ALLOW_LEGACY_COMMUNICATE=1 set,
    config_provider.flag('HAL_ALLOW_LEGACY_COMMUNICATE') must resolve True."""
    reset_default_config_provider_factory()
    monkeypatch.delenv("HAL_ALLOW_LEGACY_COMMUNICATE", raising=False)
    monkeypatch.setenv("BD_ALLOW_LEGACY_COMMUNICATE", "1")
    assert config_provider.flag("HAL_ALLOW_LEGACY_COMMUNICATE") is True, (
        "AC12 FAIL: flag() does not honor BD_ALLOW_LEGACY_COMMUNICATE alias"
    )
