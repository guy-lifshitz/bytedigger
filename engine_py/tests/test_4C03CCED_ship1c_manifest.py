"""RED tests for 4C03CCED Ship 1C — G1 worker_written_paths manifest equivalence.

Spec: SHARED/memory/Decisions/2026-05-23_4C03CCED_ship1c_manifest_equivalence_spec.md
Agreement: 4C03CCED (parent SYSTEMATIC — Runner contract, sub-ship 1C of 4)

7 tests, one per AC1-AC7. ALL must FAIL pre-GREEN because:
  - _validate_manifest_or_raise, _ManifestMissingError, _ManifestMalformedError,
    _ManifestInvalidSourceError, _BACKEND_MANIFEST_SOURCE, _ALLOWED_MANIFEST_SOURCES,
    _assert_backend_supports_manifest, manifest_from_result do NOT exist yet
    → ImportError / AttributeError fires at import time or first attribute access,
    guaranteeing FAIL in isolation.

§1q check: NO spec_from_file_location / module_from_spec / exec_module used.
  Standard sys.path pattern (grandfathered, matches all sibling tests in this dir).
§1i compliance: AC4 pre-stages files A and B on disk under tmp_path before invoking
  the commit step; no race — files materialized before function call.
§1m compliance: AC4 materializes tmp_path/A and tmp_path/B via Path.write_text().
§1l AC4 + AC3 paired: AC4 verifies real git mutation; AC3 verifies accessor invocation
  count; both must PASS post-GREEN for the manifest accessor swap to be complete.
§1l AC1+AC2 forcing-function pair: AC1 stubs success-path, AC2 stubs generic-error;
  both together force the two-bucket rejection taxonomy.
§1l AC6+AC7 forcing-function pair: AC6 wrong-type, AC7 wrong-element-type; together
  force per-element type checking beyond isinstance(_, list).

Pre-GREEN FAIL reasons per test (isolation-forcing):
  - AC1: ImportError on 'from llm_subprocess import _validate_manifest_or_raise'
  - AC2: same ImportError
  - AC3: ImportError on manifest_from_result; also grep assertion on accessor count
  - AC4: ImportError on manifest_from_result at consumer site; or if import is
    somehow deferred, accessor absent → E_LLM_MANIFEST_MISSING_AT_CONSUMER instead of ok
  - AC5: AttributeError on 'llm_subprocess._BACKEND_MANIFEST_SOURCE' (monkeypatch target
    doesn't exist); if monkeypatch creates it, capability-probe logic doesn't exist so
    invoke falls through to subprocess rather than returning E_LLM_BACKEND_NO_MANIFEST
  - AC6: ImportError on _validate_manifest_or_raise; or if added, str input is
    coerced via list() rather than rejected
  - AC7: ImportError on _validate_manifest_or_raise; or if added, per-element
    int not caught
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

# engine_py/ on sys.path — grandfathered pattern shared by all sibling tests
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

# These imports will trigger ImportError pre-GREEN (symbols not defined yet).
# That ImportError IS the RED signal — correct behavior.
import llm_subprocess as _llm_module  # noqa: E402
from llm_subprocess import (  # noqa: E402
    _validate_manifest_or_raise,
    _ManifestMissingError,
    _ManifestMalformedError,
    _ManifestInvalidSourceError,
    _BACKEND_MANIFEST_SOURCE,  # type: ignore[attr-defined]
    _ALLOWED_MANIFEST_SOURCES,  # type: ignore[attr-defined]
    _assert_backend_supports_manifest,
    manifest_from_result,
    invoke_llm_subprocess,
)
from contracts import StepResult  # noqa: E402
import telemetry_ctx  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, dict(payload), run_id or "ad-hoc"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_ctx():
    """Ensure no stale RunContext leaks between tests."""
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


@pytest.fixture
def event_log():
    return _FakeEventLog()


@pytest.fixture
def active_run_ctx(event_log):
    """Sets an active RunContext for tests that need telemetry capture."""
    telemetry_ctx.set_current_run(
        event_log=event_log, run_id="RUN-1C", step_name="t", phase="phase_X"
    )
    yield event_log
    telemetry_ctx.clear_current_run()


def _init_git_repo(repo: Path) -> str:
    """Init a real git repo with one empty initial commit. Returns HEAD SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True,
                   capture_output=True)
    # Empty initial commit so HEAD exists
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=repo, check=True, capture_output=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _make_ctx_from_repo(repo: Path, scratchpad: Path) -> "WorkflowContext":
    """Build a minimal WorkflowContext pointing at the real repo."""
    from contracts import WorkflowContext
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="Ship 1C test",
        session_id="test-session-4C03CCED-1C",
        persona="hal",
        framework=None,
        domain=None,
    )


# ---------------------------------------------------------------------------
# AC1 — _validate_manifest_or_raise raises _ManifestMissingError when
#        worker_written_paths is absent
# ---------------------------------------------------------------------------

def test_AC1_validate_manifest_or_raise_missing_worker_written_paths() -> None:
    """AC1: _validate_manifest_or_raise({'manifest_source': 'harness_tool_record'}, 'claude-in-session')
    must raise _ManifestMissingError mentioning 'worker_written_paths'.

    Pre-GREEN FAIL: symbol not yet defined → ImportError at module level.
    Post-GREEN PASS: validator rejects the absent required field.

    §1l: AC1 is the "stub-success-fails-this" forcing-function. A stub that
    always returns ([], "harness_tool_record") passes AC2-AC7 but fails AC1.
    """
    result_obj = {"manifest_source": "harness_tool_record"}

    with pytest.raises(_ManifestMissingError) as exc_info:
        _validate_manifest_or_raise(result_obj, "claude-in-session")

    msg = str(exc_info.value)
    assert "worker_written_paths" in msg, (
        f"AC1: error message must mention 'worker_written_paths'; got {msg!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — _validate_manifest_or_raise raises _ManifestInvalidSourceError when
#        manifest_source = 'worker_self_report' (the F9F7E4FD anti-pattern)
# ---------------------------------------------------------------------------

def test_AC2_validate_manifest_or_raise_invalid_source_worker_self_report() -> None:
    """AC2: _validate_manifest_or_raise({'worker_written_paths': [], 'manifest_source': 'worker_self_report'},
    'claude-in-session') must raise _ManifestInvalidSourceError mentioning 'worker_self_report'.

    Pre-GREEN FAIL: symbol not yet defined → ImportError at module level.
    Post-GREEN PASS: 'worker_self_report' is structurally rejected (not in _ALLOWED_MANIFEST_SOURCES).

    §1l: AC2 is the "stub-generic-error-fails-this" forcing-function. A stub that
    raises _ManifestMissingError for any rejection fails AC2 (wrong error class).
    The two-class taxonomy (MISSING vs INVALID_SOURCE) must be distinct.
    """
    result_obj = {
        "worker_written_paths": [],
        "manifest_source": "worker_self_report",
    }

    with pytest.raises(_ManifestInvalidSourceError) as exc_info:
        _validate_manifest_or_raise(result_obj, "claude-in-session")

    msg = str(exc_info.value)
    assert "worker_self_report" in msg, (
        f"AC2: error message must mention 'worker_self_report'; got {msg!r}"
    )

    # Confirm NOT a _ManifestMissingError (distinct taxonomy)
    assert not isinstance(exc_info.value, _ManifestMissingError), (
        "AC2: _ManifestInvalidSourceError must NOT be a subclass of _ManifestMissingError "
        "(they are sibling classes — caller maps them to distinct error codes)"
    )


# ---------------------------------------------------------------------------
# AC3 — manifest_from_result accessor replaces legacy `.get("worker_written_paths") or []`
#        pattern at ≥4 invocation sites in engine_py/workflows/
# ---------------------------------------------------------------------------

def test_AC3_manifest_from_result_replaces_legacy_pattern_in_workflows() -> None:
    """AC3: grep engine_py/workflows/*.py for:
      - legacy pattern r'\\.get\\("worker_written_paths"\\)\\s*or\\s*\\[\\]' → ZERO hits
      - new accessor 'manifest_from_result(' → AT LEAST 4 hits

    Pre-GREEN FAIL: legacy pattern returns 4 hits; accessor returns 0 hits.
    Post-GREEN PASS: accessor replaces all 4 consumer sites.

    §1l AC4 + AC3 paired: AC3 gates the accessor INVOCATION; AC4 gates the
    git mutation. Both must PASS for the swap to be complete.
    """
    workflows_dir = ENGINE_ROOT / "workflows"
    assert workflows_dir.is_dir(), f"AC3: workflows dir not found at {workflows_dir}"

    workflow_files = list(workflows_dir.glob("*.py"))
    assert workflow_files, f"AC3: no .py files found under {workflows_dir}"

    # Collect all source text in one pass
    combined_source = "\n".join(f.read_text(encoding="utf-8") for f in workflow_files)

    # Legacy pattern MUST be absent post-GREEN
    legacy_pattern = re.compile(r'\.get\("worker_written_paths"\)\s*or\s*\[\]')
    legacy_hits = legacy_pattern.findall(combined_source)
    assert len(legacy_hits) == 0, (
        f"AC3: legacy pattern '.get(\"worker_written_paths\") or []' found {len(legacy_hits)} time(s) "
        f"in engine_py/workflows/ — must be ZERO after accessor swap. "
        f"First hit preview: {repr(legacy_hits[0]) if legacy_hits else 'n/a'}"
    )

    # New accessor MUST have ≥4 invocations post-GREEN
    accessor_hits = combined_source.count("manifest_from_result(")
    assert accessor_hits >= 4, (
        f"AC3: 'manifest_from_result(' invocation count must be ≥4 in engine_py/workflows/; "
        f"found {accessor_hits}. Sites expected: commit_green_code (phase_5), "
        f"commit_fix_code (phase_6), commit_fix_tests (phase_6), run_pytest_post_fix (phase_6)."
    )


# ---------------------------------------------------------------------------
# AC4 — F9F7E4FD canary: commit_green_code gates on exact {A, B} from manifest,
#        regardless of raw_response free-text claiming "wrote C".
#        Parametrized over both allowed manifest_source values.
# ---------------------------------------------------------------------------

@pytest.fixture(
    params=["harness_tool_record", "orchestrator_observed"],
    ids=["src=harness_tool_record", "src=orchestrator_observed"],
)
def manifest_source_param(request):
    """§1l: AC4 must exercise BOTH allowed manifest_source values — identical behavior."""
    return request.param


def test_AC4_F9F7E4FD_canary_commit_gates_exactly_AB_from_manifest(
    tmp_path: Path, monkeypatch, manifest_source_param: str
) -> None:
    """AC4 (F9F7E4FD canary): commit_green_code must commit EXACTLY {A, B} from manifest.
    C is mentioned in raw_response but NOT materialized on disk and NOT in manifest;
    it must be absent from the git index and from disk.

    Parametrized over manifest_source ∈ {'harness_tool_record', 'orchestrator_observed'}.
    Both parametrize values must produce identical commit behavior.

    Pre-GREEN FAIL: manifest_from_result accessor doesn't exist → ImportError.
    If imports were somehow deferred, the accessor swap hasn't happened so the commit
    step falls back to .get("worker_written_paths") or [] which accepts the data dict
    directly — BUT the E_LLM_MANIFEST_MISSING_AT_CONSUMER path gates on manifest_source
    absent → commit step would fail with error status rather than ok.
    Post-GREEN PASS: accessor extracts [A, B]; real git add stages exactly those two;
    git ls-files returns exactly 2 lines with paths A and B.

    §1i compliance: files A and B materialized on disk BEFORE commit step invocation.
    §1m compliance: real files written via Path.write_text().
    §1j compliance: os.path.realpath() applied to tmp_path for macOS symlink resolution.
    """
    from phase_5_implement import _commit_green_code
    import phase_5_implement

    # §1j — macOS /var/folders symlink resolution
    repo = Path(os.path.realpath(str(tmp_path / "repo")))
    scratchpad = Path(os.path.realpath(str(tmp_path / "scratch")))

    # Real git repo with initial empty commit so red_commit_sha exists
    red_sha = _init_git_repo(repo)
    scratchpad.mkdir(parents=True, exist_ok=True)

    # §1m: materialize A and B on disk (NOT C — that's only in raw_response)
    (repo / "A").write_text("content-a\n", encoding="utf-8")
    (repo / "B").write_text("content-b\n", encoding="utf-8")
    # C deliberately NOT created on disk

    # Suppress telemetry emission (not under test here)
    monkeypatch.setattr(
        phase_5_implement,
        "_emit_safe",
        lambda et, p, **kw: None,
    )

    ctx = _make_ctx_from_repo(repo, scratchpad)

    # prev.data: manifest says {A, B}; raw_response misleadingly mentions C
    prev = StepResult(
        status="ok",
        data={
            "worker_written_paths": ["A", "B"],
            "manifest_source": manifest_source_param,
            "raw_response": "I wrote A, B, and also C as planned",
            "red_commit_sha": red_sha,
            "cycle": 1,
        },
        duration_ms=0,
        step_name="implement_green_code",
    )

    result = _commit_green_code(ctx, prev)

    # Step must succeed
    assert result.status == "ok", (
        f"AC4[{manifest_source_param}]: expected status='ok'; got "
        f"status={result.status!r} error_code={result.error_code!r} "
        f"error={result.error!r}"
    )

    # Verify git index contains EXACTLY {A, B} — not C, not anything else
    ls_staged = subprocess.run(
        ["git", "ls-files", "--stage"],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    staged_lines = [line.strip() for line in ls_staged.stdout.splitlines() if line.strip()]
    staged_paths = set()
    for line in staged_lines:
        # Format: "<mode> <sha> <stage>\t<path>"
        parts = line.split("\t", 1)
        if len(parts) == 2:
            staged_paths.add(parts[1])

    assert len(staged_paths) == 2, (
        f"AC4[{manifest_source_param}]: expected EXACTLY 2 staged paths; "
        f"got {len(staged_paths)}: {staged_paths!r}"
    )
    assert "A" in staged_paths, (
        f"AC4[{manifest_source_param}]: 'A' must be staged; staged: {staged_paths!r}"
    )
    assert "B" in staged_paths, (
        f"AC4[{manifest_source_param}]: 'B' must be staged; staged: {staged_paths!r}"
    )
    assert "C" not in staged_paths, (
        f"AC4[{manifest_source_param}]: 'C' must NOT be staged (only in raw_response, "
        f"not in manifest and not on disk); staged: {staged_paths!r}"
    )

    # C must also be absent from disk (it was never materialized)
    assert not (repo / "C").exists(), (
        f"AC4[{manifest_source_param}]: 'C' must NOT exist on disk"
    )

    # No untracked files should be present from the commit step itself
    ls_untracked = subprocess.run(
        ["git", "ls-files", "-o", "--exclude-standard"],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    untracked = [line.strip() for line in ls_untracked.stdout.splitlines() if line.strip()]
    # A and B should be tracked; if any unexpected untracked files from the commit step, note them
    assert "C" not in untracked, (
        f"AC4[{manifest_source_param}]: 'C' must not appear as untracked; got: {untracked!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — capability-probe: invoke_llm_subprocess with 'claude-in-session' backend
#        returns E_LLM_BACKEND_NO_MANIFEST (recoverable=False) when that backend
#        is removed from _BACKEND_MANIFEST_SOURCE. No subprocess spawned.
# ---------------------------------------------------------------------------

def test_AC5_capability_probe_returns_E_LLM_BACKEND_NO_MANIFEST(
    monkeypatch, tmp_path, active_run_ctx
) -> None:
    """AC5: when _BACKEND_MANIFEST_SOURCE does NOT include 'claude-in-session',
    invoke_llm_subprocess(backend='claude-in-session') returns
    StepResult(status='error', error_code='E_LLM_BACKEND_NO_MANIFEST', recoverable=False)
    WITHOUT spawning a subprocess or emitting 'subprocess_spawned'.

    Pre-GREEN FAIL: _BACKEND_MANIFEST_SOURCE doesn't exist as an attribute;
    monkeypatch.setattr would fail with AttributeError. Even if the attribute existed,
    no capability-probe gate exists → falls through to subprocess invocation.
    Post-GREEN PASS: probe fires before subprocess; returns error with correct code.

    §1l Layer 1+5: error_code assertion is the Layer 5 anchor; absence of
    'subprocess_spawned' event is the Layer 1 anchor (no subprocess was started).
    """
    # Remove claude-in-session from the manifest-source map to simulate a backend
    # that is registered in _KNOWN_BACKENDS but lacks manifest capability.
    monkeypatch.setattr(
        _llm_module,
        "_BACKEND_MANIFEST_SOURCE",
        {"claude-subprocess": "harness_tool_record"},  # claude-in-session intentionally absent
    )
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))

    # Mock Popen to catch any accidental subprocess spawn
    popen_mock = MagicMock()

    with patch("llm_subprocess.subprocess.Popen", popen_mock):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=10,
            step_name="t",
            backend="claude-in-session",
        )

    # Must return the manifest-capability error
    assert result.status == "error", (
        f"AC5: expected status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_BACKEND_NO_MANIFEST", (
        f"AC5: expected error_code='E_LLM_BACKEND_NO_MANIFEST'; got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC5: E_LLM_BACKEND_NO_MANIFEST must be non-recoverable (config error); "
        f"got recoverable={result.recoverable!r}"
    )

    # No subprocess must have been spawned
    assert popen_mock.call_count == 0, (
        f"AC5: subprocess.Popen must NOT be called when capability-probe fails; "
        f"call_count={popen_mock.call_count}"
    )

    # 'subprocess_spawned' must be absent from event log (no subprocess was started)
    event_types = [e[0] for e in active_run_ctx.events]
    assert "subprocess_spawned" not in event_types, (
        f"AC5: 'subprocess_spawned' must be absent when probe fails; "
        f"got event_types={event_types!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — _validate_manifest_or_raise raises _ManifestMalformedError when
#        worker_written_paths is a str (not list[str])
# ---------------------------------------------------------------------------

def test_AC6_validate_manifest_or_raise_rejects_str_instead_of_list() -> None:
    """AC6: _validate_manifest_or_raise({'worker_written_paths': 'ABC', 'manifest_source': 'harness_tool_record'},
    'claude-subprocess') must raise _ManifestMalformedError mentioning 'list' and 'str'.

    Pre-GREEN FAIL: ImportError on symbol; or if somehow added, 'ABC' is coerced
    via list() to ['A','B','C'] (the pre-GREEN behavior at line 418).
    Post-GREEN PASS: type-check rejects str input.

    §1l AC6+AC7 forcing-function pair: AC6 catches list-vs-str; AC7 catches
    per-element type. Together they mandate isinstance checks at two levels.
    """
    result_obj = {
        "worker_written_paths": "ABC",
        "manifest_source": "harness_tool_record",
    }

    with pytest.raises(_ManifestMalformedError) as exc_info:
        _validate_manifest_or_raise(result_obj, "claude-subprocess")

    msg = str(exc_info.value)
    # Must mention both the expected type and the actual type
    assert "list" in msg, (
        f"AC6: error message must mention 'list' (expected type); got {msg!r}"
    )
    assert "str" in msg, (
        f"AC6: error message must mention 'str' (actual type); got {msg!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — _validate_manifest_or_raise raises _ManifestMalformedError when
#        worker_written_paths has a non-string element
# ---------------------------------------------------------------------------

def test_AC7_validate_manifest_or_raise_rejects_non_string_list_element() -> None:
    """AC7: _validate_manifest_or_raise({'worker_written_paths': ['A', 123, 'B'], 'manifest_source': 'harness_tool_record'},
    'claude-subprocess') must raise _ManifestMalformedError mentioning '[1]' and 'int'.

    Pre-GREEN FAIL: ImportError on symbol; or if added, ['A', 123, 'B'] passes
    because no per-element type check exists pre-GREEN.
    Post-GREEN PASS: per-element isinstance check rejects int element at index 1.

    §1l AC6+AC7 forcing-function pair: a stub doing isinstance(paths, list) but
    not per-element isinstance passes AC6 (list IS a list) but fails AC7 (int in list).
    """
    result_obj = {
        "worker_written_paths": ["A", 123, "B"],
        "manifest_source": "harness_tool_record",
    }

    with pytest.raises(_ManifestMalformedError) as exc_info:
        _validate_manifest_or_raise(result_obj, "claude-subprocess")

    msg = str(exc_info.value)
    # Must mention the index of the offending element
    assert "[1]" in msg, (
        f"AC7: error message must mention '[1]' (index of non-str element); got {msg!r}"
    )
    # Must mention the actual type
    assert "int" in msg, (
        f"AC7: error message must mention 'int' (actual element type); got {msg!r}"
    )
