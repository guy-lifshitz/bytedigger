"""RED test for GH769 — Gherkin section -> "Adversarial edges not covered
by AC-table" (validation-gate prompt compress).

Spec: SHARED/memory/Decisions/2026-07-14_GH769_gherkin_adversarial_edges_spec.md

UUT: `_build_validation_prompt` inside
SYSTEM/cli/build/engine_py/workflows/phase_5_implement.py, exercised through
the real workflow (WorkflowEngine + phase_5_implement_workflow) with a
passthrough backend so the built prompt is echoed verbatim into
VALIDATION_DOC_RELPATH — mirrors the existing
test_validation_prompt_includes_extended_hal_audit pattern in
test_phase_5_implement.py (25794DC9).

Pre-GREEN: AC1 and AC4 FAIL (adversarial-edges subsection not yet added;
prompt still contains the "Gherkin" vestige). AC2/AC3 also FAIL (the
duplication-forbidden / `none`-fallback instruction text doesn't exist yet).
AC5/AC6 are regression guards that already pass today and must continue to
pass post-GREEN (byte-untouched per spec §2.1 step 3).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from bytedigger_engine.contracts import StepResult
from bytedigger_engine.contracts import WorkflowContext
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.llm_subprocess import register_backend
from bytedigger_engine.workflows.phase_5_implement import (
    SPEC_DOC_RELPATH,
    VALIDATION_DOC_RELPATH,
    phase_5_implement_workflow,
)


class _PassthroughBackend:
    """Returns the prompt as raw_response (mirrors _PassthroughBackend in
    test_phase_5_implement.py)."""

    def __init__(self, name: str = "passthrough-gh769"):
        self._name = name
        self.calls: list[dict] = []

    def __call__(self, **kw) -> StepResult:
        self.calls.append(dict(kw))
        data: dict = {
            "raw_response": kw.get("prompt", ""),
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", self._name),
            error=None,
            error_code=None,
            recoverable=True,
        )


def _register_passthrough(name: str = "passthrough-gh769") -> _PassthroughBackend:
    b = _PassthroughBackend(name)
    register_backend(name, b, manifest_source="harness_tool_record", overwrite=True)
    return b


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "verify_green_delta_enforce": False, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def minimal_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one commit + an untracked test stub, mirroring
    test_phase_5_implement.py's minimal_repo helper (A4461B8F)."""
    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    test_stub = repo / "tests" / "test_stub.py"
    test_stub.parent.mkdir(parents=True, exist_ok=True)
    test_stub.write_text("def test_stub(): assert False\n")
    return repo


def seed_spec(scratchpad: Path, body: str = "## US1\nAdd foo\n") -> Path:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body)
    return spec


def test_gh769_adversarial_edges_replace_gherkin(tmp_path, monkeypatch):
    """GH769 AC1-AC6: _build_validation_prompt must drop the "Gherkin"
    vestige and add a REQUIRED "Adversarial edges" subsection (forbids
    AC-restatement, permits `none` fallback) while leaving the extended-HAL-
    audit forcing functions and required OUTPUT sections byte-untouched.
    """
    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)

    _register_passthrough("passthrough-gh769")
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "passthrough-gh769")
    eng = WorkflowEngine()
    eng.register("p5", phase_5_implement_workflow())
    eng.execute("p5", make_ctx(scratchpad, git_cwd=str(repo), model="sonnet"))

    validation = (scratchpad / VALIDATION_DOC_RELPATH).read_text()

    # AC1 — required "Adversarial edges" subsection present
    assert "Adversarial edge" in validation, (
        "AC1 FAIL: validation prompt missing 'Adversarial edge' required "
        "subsection (spec §2.1 step 2 — not yet added by GREEN)"
    )

    # Window-scoped view: the adversarial-edges subsection body only, bounded
    # by the next required-section header — prevents AC2/AC3 from being
    # satisfied by unrelated text living elsewhere in the prompt (MAJOR-1).
    start = validation.index("Adversarial edge")
    rest = validation[start:]
    end = rest.find("\n  ## ")
    window = rest if end == -1 else rest[:end]

    # AC2 — duplication-forbidden phrasing referencing the AC-table (window-scoped)
    assert "Do NOT restate" in window, (
        "AC2 FAIL: adversarial-edges window missing 'Do NOT restate' AC-duplication "
        "prohibition (spec §2.1 step 2 — not yet added by GREEN)"
    )
    assert "AC-table" in window, (
        "AC2 FAIL: adversarial-edges window missing 'AC-table' reference in the "
        "duplication-forbidden instruction (spec §2.1 step 2)"
    )

    # AC3 — `none` fallback permitted, scoped to the adversarial-edges window
    # (the backticked `none` occurs elsewhere in the prompt pre-GREEN, so an
    # unscoped substring check is vacuous — MAJOR-1)
    assert "`none`" in window, (
        "AC3 FAIL: adversarial-edges window missing backticked `none` fallback "
        "instruction (spec §2.1 step 2)"
    )

    # AC4 — negative control: the "Gherkin" vestige must be gone
    assert "Gherkin" not in validation, (
        "AC4 FAIL: validation prompt still contains 'Gherkin' vestige — "
        "spec §2.1 step 1 requires it be dropped from the four-step-audit line"
    )

    # AC4b — pin the surviving forward-map line so AC4 can't be satisfied by
    # deleting the whole line instead of just the "Gherkin" word (MINOR-4)
    assert "Forward map: every spec scenario" in validation, (
        "AC4b FAIL: validation prompt missing surviving 'Forward map: every "
        "spec scenario' line — AC4 must be satisfied by word-removal, not "
        "line-deletion (spec §2.1 step 1)"
    )

    # AC5 — regression guard: extended-HAL-audit forcing fns untouched
    for token in (
        "§2-vs-§3 cross-check",
        "Reachability trace",
        "Rule-overlap simulation",
        "Spec-internal-consistency",
    ):
        assert token in validation, (
            f"AC5 REGRESSION: validation prompt missing '{token}' — GREEN must "
            "leave the extended-HAL-audit block byte-untouched (spec §2.1 step 3)"
        )

    # AC6 — regression guard: ALL SIX required OUTPUT section headers untouched
    for header in (
        "## Forward Map",
        "## Reverse Map",
        "## Spec Compliance",
        "## Reachability & Cross-Check",
        "## Quality Findings",
        "## Verdict",
    ):
        assert header in validation, (
            f"AC6 REGRESSION: validation prompt missing '{header}' required "
            "output section — GREEN must leave OUTPUT sections byte-untouched "
            "(spec §2.1 step 3)"
        )

    # AC7 — placement: the adversarial-edges instruction must live between the
    # "## Quality Findings" and "## Verdict" REQUIRED sections (per the
    # prompt's own "REQUIRED sections, in this exact order, no others"
    # contract) — an inert ship placed elsewhere would satisfy AC1-AC6 yet
    # never be emitted by the validator (MAJOR-2)
    assert (
        validation.index("## Quality Findings")
        < validation.index("Adversarial edge")
        < validation.index("## Verdict")
    ), (
        "AC7 FAIL: 'Adversarial edge' subsection is not placed between "
        "'## Quality Findings' and '## Verdict' — spec §2.1 step 2 requires "
        "it live inside the Quality Findings REQUIRED section"
    )
