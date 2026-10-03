"""bd#89 P3b2 RED (spec FROZEN r1).

Spec: docs/decisions/2026-10-03-bd89-p3b2-drop-decorrelated-verifier.md (section 3, AC1-AC11).

Phase 6 drops the decorrelated verifier: three steps, the model role, two error codes and the
enforce reader go away; ``org_config["decorrelated_verify_enforce"]`` becomes an ignored key that
is made visible with one ``decorrelated_verify_enforce_ignored`` event.

AC mapping:
    test_ac1_workflow_has_17_steps_without_decorr_steps         -> AC1
    test_ac2_decorr_symbols_are_gone                            -> AC2
    test_ac3_enforce_true_is_ignored_with_one_event             -> AC3
    test_ac4_falsy_values_stay_quiet_and_truthy_value_emits     -> AC4
    test_ac5_model_role_removed_but_old_models_json_loads       -> AC5
    test_ac6_decorr_error_codes_are_gone                        -> AC6
    test_ac7_prod_source_and_config_doc_have_no_decorr          -> AC7
    test_ac8_models_pinned_fixture_has_no_decorr_role           -> AC8
    test_ac9_guard_review_fix_satisfaction_survive              -> AC9  (GUARD)
    test_ac10_guard_aggregation_still_writes_composite_review   -> AC10 (GUARD)
    test_ac11_guard_class_i_lint_exits_zero                     -> AC11 (GUARD)

Expected red today: AC1-AC8. Expected green today: AC9-AC11.
Singleton/timing: none (workflows.md 1i not applicable; nothing here races).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import error_codes, telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.lib import model_config
from bytedigger_engine.workflows import phase_6_review as p6

TESTS_DIR = Path(__file__).resolve().parent
ENGINE_PY = TESTS_DIR.parent
REPO_ROOT = ENGINE_PY.parent
ENGINE_PKG = ENGINE_PY / "bytedigger_engine"

_REMOVED_STEPS = ["build_decorr_prompt", "invoke_decorr_llm", "write_decorr_artifact"]

_STEPS_BEFORE = [
    "build_review_prompt", "invoke_review_llm", "write_review_artifact",
    "verify_findings", "verify_findings_semantic", "build_fix_prompt", "invoke_fix_llm", "fix_watchdog",
    "write_fix_artifact", "commit_fix_code", "commit_fix_tests", "run_pytest_post_fix",
    "verify_fix_typecheck", "build_decorr_prompt", "invoke_decorr_llm", "write_decorr_artifact",
    "build_satisfaction_prompt", "invoke_satisfaction_llm", "write_satisfaction_doc", "detect_mass_unverified",
]
_STEPS_AFTER = [n for n in _STEPS_BEFORE if n not in _REMOVED_STEPS]

_EVENT = "decorrelated_verify_enforce_ignored"
_ABSENT = object()
_TITLE = "Composite widget leaks handle"


# ─── helpers ──────────────────────────────────────────────────────────────────

class _Sink:
    """Real event-log sink: what telemetry_ctx.emit_safe appends to."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload))

    def named(self, name: str) -> list[dict]:
        return [p for n, p in self.events if n == name]

    def names(self) -> list[str]:
        return [n for n, _ in self.events]


@pytest.fixture(autouse=True)
def _clean_run_ctx():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


def _sink() -> _Sink:
    sink = _Sink()
    telemetry_ctx.set_current_run(event_log=sink, run_id="bd89-p3b2", step_name="s", phase="p6")
    return sink


def _ctx(tmp_path: Path, **extra) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    worktree = tmp_path / "fake_worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratch),
        "current_worktree_path": str(worktree),
        "complexity": "FEATURE",
    }
    org.update(extra)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="add feature X", session_id="test-bd89-p3b2", persona="hal",
        framework=None, domain=None,
    )


def _step(name: str):
    return next(s for s in p6.phase_6_review_workflow().steps if s.name == name)


@pytest.fixture
def model_config_fixture(tmp_path):
    """Point model_config at a tmp models.json; restore prior state after."""
    prev_path = model_config._CONFIG_PATH

    def _write(data: dict) -> Path:
        path = tmp_path / "models.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        model_config._CONFIG_PATH = path
        model_config.reset_cache()
        return path

    yield _write
    model_config._CONFIG_PATH = prev_path
    model_config.reset_cache()


# ─── AC1 / AC2 ────────────────────────────────────────────────────────────────

def test_ac1_workflow_has_17_steps_without_decorr_steps():
    names = [s.name for s in p6.phase_6_review_workflow().steps]
    assert len(names) == 17, f"expected 17 steps, got {len(names)}: {names}"
    assert names == _STEPS_AFTER
    assert names[names.index("verify_fix_typecheck") + 1] == "build_satisfaction_prompt"


@pytest.mark.parametrize("attr", [
    "_build_decorr_prompt", "_invoke_decorr_llm", "_write_decorr_artifact",
    "DECORR_DOC_RELPATH", "_DECORR_VERDICT_MARKERS",
])
def test_ac2_decorr_symbols_are_gone(attr):
    assert not hasattr(p6, attr), f"phase_6_review.{attr} must be deleted"


# ─── AC3 / AC4 (through the real _build_review_prompt, real sink) ─────────────

def test_ac3_enforce_true_is_ignored_with_one_event(tmp_path):
    sink = _sink()
    result = p6._build_review_prompt(_ctx(tmp_path, decorrelated_verify_enforce=True), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.error_code is None
    ignored = sink.named(_EVENT)
    assert len(ignored) == 1, sink.names()
    assert ignored[0]["value"] == "True"


def test_ac4_falsy_values_stay_quiet_and_truthy_value_emits(tmp_path):
    for value in (_ABSENT, None, False, 0, ""):
        telemetry_ctx.clear_current_run()
        sink = _sink()
        extra = {} if value is _ABSENT else {"decorrelated_verify_enforce": value}
        result = p6._build_review_prompt(_ctx(tmp_path, **extra), None)
        assert result.status == "ok", f"{value!r}: {result.error_code}: {result.error}"
        assert sink.named(_EVENT) == [], f"{value!r} must not emit"
    telemetry_ctx.clear_current_run()
    sink = _sink()
    result = p6._build_review_prompt(_ctx(tmp_path, decorrelated_verify_enforce="yes"), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    ignored = sink.named(_EVENT)
    assert len(ignored) == 1, sink.names()
    assert ignored[0]["value"] == "yes"


# ─── AC5 ──────────────────────────────────────────────────────────────────────

def test_ac5_model_role_removed_but_old_models_json_loads(model_config_fixture):
    assert not hasattr(model_config, "get_claude_decorrelated_verifier")
    assert "decorrelated_verifier" not in model_config.FALLBACK_CONFIG["claude"]
    model_config_fixture({"claude": {"decorrelated_verifier": "fable"}})
    assert model_config.get_claude_critical() == "opus"
    assert model_config.get_claude_primary() == "sonnet"


# ─── AC6 ──────────────────────────────────────────────────────────────────────

def test_ac6_decorr_error_codes_are_gone():
    for code in ("E_DECORR_INVOKE_FAILED", "E_DECORR_VERIFY_SUSPECT"):
        assert code not in error_codes.ERROR_CODES, code
        for md in (ENGINE_PY / "ERROR_CODES.md", ENGINE_PKG / "ERROR_CODES.md"):
            assert code not in md.read_text(encoding="utf-8"), f"{code} in {md}"
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.error_codes", "--check"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ─── AC7 ──────────────────────────────────────────────────────────────────────

def test_ac7_prod_source_and_config_doc_have_no_decorr():
    for rel in ("workflows/phase_6_review.py", "lib/model_config.py", "error_codes.py"):
        text = (ENGINE_PKG / rel).read_text(encoding="utf-8")
        if rel == "workflows/phase_6_review.py":
            # the only two allowed names, longest first
            text = text.replace(_EVENT, "").replace("decorrelated_verify_enforce", "")
        hits = [ln.strip() for ln in text.splitlines() if "decorr" in ln.lower()]
        assert not hits, f"{rel} still mentions decorr: {hits[:5]}"
    doc = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert "decorrelated verifier" not in doc.lower()


# ─── AC8 ──────────────────────────────────────────────────────────────────────

def test_ac8_models_pinned_fixture_has_no_decorr_role():
    data = json.loads((TESTS_DIR / "fixtures" / "models_pinned.json").read_text(encoding="utf-8"))
    assert "decorrelated_verifier" not in data["claude"]


# ─── AC9 (GUARD) ──────────────────────────────────────────────────────────────

def test_ac9_guard_review_fix_satisfaction_survive():
    doc = p6.__doc__ or ""
    start = doc.index("role_template_path")
    window = doc[start:doc.index("bd#119", start)]
    for word in ("review", "fix", "satisfaction"):
        assert word in window, word
    for name in ("_build_review_prompt", "_invoke_review_llm", "_verify_findings",
                 "_verify_findings_semantic", "_build_satisfaction_prompt", "_write_review_artifact"):
        assert callable(getattr(p6, name, None)), name


# ─── AC10 (GUARD) ─────────────────────────────────────────────────────────────

def test_ac10_guard_aggregation_still_writes_composite_review(tmp_path):
    ctx = _ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])
    src = tmp_path / "target.py"
    src.write_text("def foo():\n    return 42\n", encoding="utf-8")
    reviews = scratch / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "role-composite.md").write_text(
        f"### SEVERITY: HIGH — {_TITLE}\n"
        f"> {src}:1: def foo():\n"
        "Confidence: HIGH\n"
        "Description: handle is never released.\n\n"
        "VERDICT: FAIL\n",
        encoding="utf-8",
    )
    prev = StepResult(
        status="ok",
        data={
            "scratchpad": str(scratch), "complexity": "FEATURE",
            "doc_path": str(scratch / "reviews" / "build-review.md"),
            "spec_path": str(scratch / "spec.md"),
            "red_log_path": str(scratch / "red.log"),
            "green_log_path": str(scratch / "green.log"),
            "raw_response": "stub reviewer stdout",
        },
        duration_ms=0, step_name="invoke_review_llm",
    )
    result = _step("write_review_artifact").execute(ctx, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    doc = Path(prev.data["doc_path"]).read_text(encoding="utf-8")
    assert "# Composite Review" in doc
    assert _TITLE in doc


# ─── AC11 (GUARD) ─────────────────────────────────────────────────────────────

def test_ac11_guard_class_i_lint_exits_zero():
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.class_i_lint"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
