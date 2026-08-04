"""Engine_py test-suite shared fixtures.

3E6EBDF4 (closes CF2EE8ED Risk-R1): pin HAL_RUNNER_BACKEND=claude-subprocess
by default for every engine_py test. Prerequisite for DA1174AE default-flip
retry; ensures phase_45_spec* tests don't hang when `_DEFAULT_BACKEND` flips
to `claude-in-session`.

FFEA1913: poison-PATH burn-guard — pytest_configure/pytest_unconfigure install
a fake `claude` sentinel binary ahead of the real one on PATH so any test that
accidentally spawns the real LLM exits 99 (BURN-GUARD) instead of billing.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from helpers import host_tools as _host_tools
from helpers import live_repo as _live_repo

# bd#102: re-export the real hookwrapper object (not a reimplementation) so
# pytest registers the same function this module owns and tests. The body,
# HOST_TOOLS, and _HOST_TOOL_AVAILABLE all live in helpers.host_tools; this
# module only imports it (module scope — pytester's SysModulesSnapshot would
# otherwise evict a module first imported inside a test body) and calls
# freeze_host_tool_availability() from inside the existing pytest_configure
# below.
pytest_runtest_makereport = _host_tools.pytest_runtest_makereport

# Conftest-import-time singleton: expose engine_py root (the PARENT of the
# `bytedigger_engine` package) plus the tests dir, so test files don't need
# module-level sys.path manipulation (§1q / 81F97F3D gate).
#
# bd#44 fence (spec §5): the entries for `engine_py/workflows` and
# `engine_py/lib` are GONE and must not come back in any spelling. Those two
# directories now live INSIDE the package; putting them back on sys.path would
# make `phase_5_implement` and `model_config` resolve as flat top-level names
# again — the exact defect this lot removes — and would make the suite green
# over a package that is broken for anyone who pip-installs it. Only the
# package's parent directory is exposed.
_ENGINE_ROOT = Path(__file__).parent.parent
for _p in [str(_ENGINE_ROOT), str(Path(__file__).parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Model-hermetic pin (canary-model-pin): tests must not redden when the live
# ~/.claude/SHARED/config/models.json is flipped for a temp window (e.g.
# critical/spec_writer -> fable). Pin model_config to a static fixture at
# conftest import time -- BEFORE any test module imports phase_45_spec, whose
# module-level DEFAULT_*_LLM_COMMAND constants are computed at import time.
from bytedigger_engine.lib import model_config as _model_config  # noqa: E402

_MODELS_PINNED_FIXTURE = Path(__file__).parent / "fixtures" / "models_pinned.json"
_model_config._CONFIG_PATH = _MODELS_PINNED_FIXTURE
_model_config.reset_cache()

# 4F86153A bootstrap: register HAL config provider at collection time so that
# event_log imports (which compute module-level HAL_DIR/DEFAULT_LOG_PATH) resolve
# under the HAL provider, not the neutral default. Mirrors run.py module-top
# registration. Guarded by try/except so RED (before hal_config_provider exists)
# does not break collection of the whole suite.
try:
    import hal_config_provider as _hal_config_provider_mod
    _hal_config_provider_mod.register_hal_config_provider()
except ImportError:
    _hal_config_provider_mod = None  # type: ignore[assignment]


# Module-level globals for PATH snapshot and guard dir (Opus advisory A2).
_ORIG_PATH: str = ""
_GUARD_DIR: str = ""


def pytest_configure(config: pytest.Config) -> None:
    """Install the poison-PATH burn-guard (FFEA1913 §2.1).

    Prepends a fake `claude` script to PATH that exits 99 and emits BURN-GUARD
    to stderr if invoked.  Any test that reaches the real LLM trips the guard.

    Also wires the GH1220 Change C live-repo HEAD sentinel (guarded by
    try/except ImportError so RED, before the module exists, collects the
    rest of the suite unaffected).
    """
    global _ORIG_PATH, _GUARD_DIR

    try:
        import _live_repo_sentinel as _sentinel  # noqa: PLC0415
        _sentinel.pytest_configure(config)
    except ImportError:
        pass

    guard_dir = tempfile.mkdtemp(prefix="hal-burn-guard-")
    log_path = os.path.join(guard_dir, "burn-guard.log")
    claude_path = os.path.join(guard_dir, "claude")

    script = (
        "#!/bin/sh\n"
        'echo "BURN-GUARD: real \'claude\' invoked during pytest'
        ' (test failed to stub the LLM seam). argv: $*" >&2\n'
        f'printf \'%s argv=%s PATH=%s\\n\' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"'
        f' "$*" "$PATH" >> "{log_path}"\n'
        "exit 99\n"
    )

    with open(claude_path, "w") as fh:
        fh.write(script)
    os.chmod(claude_path, 0o755)

    _ORIG_PATH = os.environ.get("PATH", "")
    _GUARD_DIR = guard_dir

    os.environ["PATH"] = guard_dir + os.pathsep + _ORIG_PATH
    os.environ["HAL_LLM_BURN_GUARD"] = guard_dir

    # bd#102 M1: freeze host-tool availability once, from inside this SAME
    # pytest_configure — never a second one (that would shadow this hook and
    # reopen the burn-guard billing seam).
    _host_tools.freeze_host_tool_availability()

    # bd#2: same rule, second environment axis — is this tree a git checkout?
    # Must run AFTER _sentinel.pytest_configure above, which is what puts
    # `_live_repo_sentinel` in sys.modules for the climb.
    _live_repo.freeze_git_checkout_availability()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore PATH and remove the burn-guard env marker (FFEA1913 §2.1)."""
    if _ORIG_PATH or _GUARD_DIR:
        os.environ["PATH"] = _ORIG_PATH
    os.environ.pop("HAL_LLM_BURN_GUARD", None)


def pytest_runtest_teardown(item, nextitem) -> None:  # noqa: ARG001
    """GH1220 Change C: cheap per-test HEAD-drift check (culprit attribution)."""
    try:
        import _live_repo_sentinel as _sentinel  # noqa: PLC0415
        _sentinel.pytest_runtest_teardown(item)
    except ImportError:
        pass


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """GH1220 Change C: post-suite live-repo HEAD drift report/exitstatus."""
    try:
        import _live_repo_sentinel as _sentinel  # noqa: PLC0415
        _sentinel.pytest_sessionfinish(session)
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _hal_runner_backend_pin(monkeypatch):
    """Pin HAL_RUNNER_BACKEND=claude-subprocess per-test by default.

    Per-test override semantics: tests that call `monkeypatch.setenv(
    "HAL_RUNNER_BACKEND", ...)` or `monkeypatch.delenv("HAL_RUNNER_BACKEND",
    raising=False)` win — pytest's `monkeypatch` is function-scoped and stacks
    latest-call-wins. Autouse runs first; test body overrides freely.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")


@pytest.fixture(autouse=True)
def _hal_directed_repair_default_off(monkeypatch):
    """Default HAL_DIRECTED_REPAIR=0 per-test (GH371 test isolation, spec §2.4).

    The directed gate-repair pre-stage is default-ON in production, but in tests
    it would make every gate-FAIL path re-run the gate (extra telemetry, and a
    real invoke_llm_subprocess spawn). Existing gate tests assert pre-GH371
    behavior, so isolate them here. Per-test override semantics: GH371's own
    tests that need the pre-stage active call monkeypatch.setenv(
    "HAL_DIRECTED_REPAIR", "1") in their body and win (function-scoped, latest
    call wins); autouse runs first.
    """
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")


_STATE_LOG_ENV_SEAMS = {
    "HAL_REJECT_LOG": "reject-reasons.jsonl",
    "HAL_REWORK_LOG": "build-rework-log.jsonl",
    "HAL_VERDICT_VERIFY_LOG": "verdict-verify-gate.jsonl",
    "HAL_INCIDENT_LOG": "incidents.jsonl",
    "HAL_DISPATCHER_REPORTS_LOG": "dispatcher-reports.jsonl",
}


@pytest.fixture(autouse=True)
def _telemetry_log_isolation(monkeypatch, tmp_path):
    """GH497 D1 + GH1113: isolate every durable HAL_*_LOG state seam listed in
    `_STATE_LOG_ENV_SEAMS` under pytest tmp for every engine test — prevents
    build_id=null fixture-pollution rows landing in the real prod logs.

    These seams MUST be set through os.environ (monkeypatch.setenv, which mutates
    the real environment) rather than passed as fixture arguments: the writers are
    reached through subprocess CLI and git-hook invocations, and a child process
    only inherits the environment — an in-process fixture-arg seam would not
    reach it, and the child would fall back to its cwd-relative default.

    Per-test override semantics: a test that calls monkeypatch.setenv(...) itself
    wins (function-scoped, latest-call-wins); autouse runs first.
    """
    for var, filename in _STATE_LOG_ENV_SEAMS.items():
        monkeypatch.setenv(var, str(tmp_path / filename))


# GH1118: coord-claim file seam. Kept SEPARATE from _STATE_LOG_ENV_SEAMS so the
# GH1113 exact-5 invariant (test_gh1113 AC1) stays intact.
_CLAIM_ENV_SEAM = "HAL_AGREEMENT_LOCKS_FILE"


@pytest.fixture(autouse=True)
def _claim_locks_isolation(monkeypatch, tmp_path):
    """GH1118: isolate the coord-claim file seam so a live cross-lot claim in
    the real global agreement-locks.jsonl cannot pre-empt the run-phase.ts
    re-entrancy guard (E_PARALLEL_CLAIM_CONFLICT exit 2 instead of
    RE_ENTRANT_BLOCKED exit 7). Points HAL_AGREEMENT_LOCKS_FILE at a per-test
    tmp path (absent → fold-locks.sh returns empty locks → no conflict). Set
    through os.environ (monkeypatch.setenv) because coord/list is reached via
    subprocess + Bun.spawnSync, which only inherit the environment. Per-test
    override: a test that setenv's it itself wins (function-scoped, latest-call).
    """
    monkeypatch.setenv(_CLAIM_ENV_SEAM, str(tmp_path / "agreement-locks.jsonl"))


@pytest.fixture(autouse=True)
def _hal_config_provider_default():
    """Ensure every test runs with the HAL config provider registered (4F86153A).

    Setup: register HAL provider so all config_provider.* calls resolve HAL paths
    (mirrors run.py bootstrap; prevents pricing/cost tests from reading a neutral
    cwd-relative models.json instead of ~/.claude/SHARED/config/models.json).

    Teardown: re-register HAL (NOT reset-to-neutral) so any test that called
    reset_default_config_provider_factory() in its body does not leak neutral
    state to the next test. Tests that need neutral semantics (AC4/AC6) call
    reset_default_config_provider_factory() at the START of their body to opt out,
    which overrides this fixture's setup; their finally-block then calls reset again —
    this teardown re-instates HAL after that.

    Guarded by try/except ImportError so RED (before hal_config_provider exists)
    runs all pre-existing tests without disruption.
    """
    try:
        import hal_config_provider as _hcp  # noqa: PLC0415
        _hcp.register_hal_config_provider()
    except ImportError:
        pass
    yield
    try:
        import hal_config_provider as _hcp  # noqa: PLC0415
        _hcp.register_hal_config_provider()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _llm_backend_registry_isolation():
    """Hermetic backend registry per test (GH1082 — #1092/#1098)."""
    from bytedigger_engine import llm_subprocess as _llm  # noqa: PLC0415
    _llm.reset_backends()
    yield
    _llm.reset_backends()
