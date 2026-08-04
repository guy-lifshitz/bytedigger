"""RED tests for 69AA5237: multi-language test routing in
`_verify_red_fails_mechanically` (phase_5_implement.py:1124-1176).

Today the helper hardcodes `python3 -m pytest …` regardless of the file
extension of `red_test_paths`. After migration from CodeForge → HALForge
(2026-02-27), TS / Swift / Rust routing was lost. These tests pin the
restored contract:

  .ts/.tsx → bun test
  .swift   → swift test
  .rs      → cargo test
  other (e.g. .go) → graceful skip (status="ok", data["skipped"]=<reason>),
                     pytest is NOT invoked.

Tests fail today: subprocess is mocked, and the helper today calls pytest
unconditionally — so argv assertions on bun/swift/cargo mismatch.

After GREEN, all four tests pass.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared fixtures (mirror C76F6F3C style) ────────────────────────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": "scratchpad/spec.md",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _stub_proc(returncode: int = 1, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


# ─── Test 1: TS routing → bun test ──────────────────────────────────────────


def test_ts_paths_dispatch_to_bun_test(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically

    prev = _make_prev(["scratch/foo.test.ts"])
    with patch("bytedigger_engine.workflows.phase_5_implement.subprocess.run", return_value=_stub_proc()) as mrun:
        result = _verify_red_fails_mechanically(_make_ctx(tmp_path), prev)

    assert mrun.called, "subprocess.run was not invoked"
    argv = list(mrun.call_args.args[0])
    assert "bun" in argv[0], f"expected bun runner, got argv[0]={argv[0]!r}"
    assert "pytest" not in " ".join(argv), f"pytest must not be invoked for .ts paths; argv={argv!r}"
    assert result.status == "ok", f"expected ok (RED failed), got {result.status} ({result.error_code})"


# ─── Test 2: Swift routing → swift test ─────────────────────────────────────


def test_swift_paths_dispatch_to_swift_test(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically

    prev = _make_prev(["scratch/FooTests.swift"])
    with patch("bytedigger_engine.workflows.phase_5_implement.subprocess.run", return_value=_stub_proc()) as mrun:
        result = _verify_red_fails_mechanically(_make_ctx(tmp_path), prev)

    assert mrun.called, "subprocess.run was not invoked"
    argv = list(mrun.call_args.args[0])
    assert argv[0] == "swift" and "test" in argv, f"expected `swift test`, got argv={argv!r}"
    assert "pytest" not in " ".join(argv), f"pytest must not be invoked for .swift paths; argv={argv!r}"
    assert result.status == "ok", f"expected ok (RED failed), got {result.status} ({result.error_code})"


# ─── Test 3: Rust routing → cargo test ──────────────────────────────────────


def test_rust_paths_dispatch_to_cargo_test(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically

    prev = _make_prev(["scratch/foo_test.rs"])
    with patch("bytedigger_engine.workflows.phase_5_implement.subprocess.run", return_value=_stub_proc()) as mrun:
        result = _verify_red_fails_mechanically(_make_ctx(tmp_path), prev)

    assert mrun.called, "subprocess.run was not invoked"
    argv = list(mrun.call_args.args[0])
    assert argv[0] == "cargo" and "test" in argv, f"expected `cargo test`, got argv={argv!r}"
    assert "pytest" not in " ".join(argv), f"pytest must not be invoked for .rs paths; argv={argv!r}"
    assert result.status == "ok", f"expected ok (RED failed), got {result.status} ({result.error_code})"


# ─── Test 4: Unsupported language (.go) → graceful skip, no pytest ──────────


def test_unsupported_language_skips_gracefully(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically

    prev = _make_prev(["scratch/foo_test.go"])
    with patch("bytedigger_engine.workflows.phase_5_implement.subprocess.run", return_value=_stub_proc()) as mrun:
        result = _verify_red_fails_mechanically(_make_ctx(tmp_path), prev)

    if mrun.called:
        argv = list(mrun.call_args.args[0])
        assert "pytest" not in " ".join(argv), (
            f".go path must NOT route to pytest; argv={argv!r}"
        )
    assert result.status == "ok", f"expected ok (skipped), got {result.status} ({result.error_code})"
    assert isinstance(result.data, dict) and result.data.get("skipped"), (
        f"expected data['skipped']=<reason> for unsupported language; got data={result.data!r}"
    )


# ─── Test 5: Mixed-language paths must NOT pytest everything ────────────────


def test_mixed_language_paths_does_not_pytest_everything(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically

    prev = _make_prev(["scratch/foo.test.ts", "scratch/bar_test.py"])
    with patch("bytedigger_engine.workflows.phase_5_implement.subprocess.run", return_value=_stub_proc()) as mrun:
        _verify_red_fails_mechanically(_make_ctx(tmp_path), prev)

    forbidden = ["python3", "-m", "pytest", "-x", "--tb=no", "-q",
                 "scratch/foo.test.ts", "scratch/bar_test.py"]
    runner_basenames = set()
    for call in mrun.call_args_list:
        argv = list(call.args[0])
        assert argv != forbidden, (
            f"mixed TS+Py paths must NOT be pytested in one call; argv={argv!r}"
        )
        # Extract runner basename — strip any version suffix like python3.13.
        head = argv[0].rsplit("/", 1)[-1]
        if head.startswith("python3"):
            runner_basenames.add("python3")
        else:
            runner_basenames.add(head)
    assert {"bun", "python3"}.issubset(runner_basenames), (
        f"both bun and python3 must be invoked for mixed TS+Py; got runners={runner_basenames!r}"
    )


# ─── Test 6: Mixed paths where one subgroup PASSES → fake RED → error ───────


def test_mixed_paths_with_passing_subgroup_returns_error(tmp_path):
    """Pillar 3 contract: ALL groups must individually fail. If TS fails (real
    RED) but PY passes (fake RED in that subgroup), function must return error
    with E_RED_NOT_FAILING — not silently accept the partial fake RED.
    """
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically

    prev = _make_prev(["scratch/foo.test.ts", "scratch/bar_test.py"])

    def _fake_run(argv, *args, **kwargs):
        head = argv[0].rsplit("/", 1)[-1]
        # TS group fails (real RED); Py group passes (fake RED).
        if head == "bun":
            return _stub_proc(returncode=1, stdout="ts failed")
        if head.startswith("python3"):
            return _stub_proc(returncode=0, stdout="py passed")
        return _stub_proc(returncode=1)

    with patch("bytedigger_engine.workflows.phase_5_implement.subprocess.run", side_effect=_fake_run):
        result = _verify_red_fails_mechanically(_make_ctx(tmp_path), prev)

    assert result.status == "error", (
        f"expected error for mixed real-RED + fake-RED (passing) subgroup, got {result.status}"
    )
    assert result.error_code == "E_RED_NOT_FAILING", (
        f"expected E_RED_NOT_FAILING, got {result.error_code!r}"
    )
