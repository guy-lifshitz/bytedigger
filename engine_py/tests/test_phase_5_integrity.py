"""Tests for phase_5_integrity workflow — Stage 2.10 port.

Test-Integrity Diff Guard. Three steps. The diff step uses real `git diff`
against an init'd repo in tmp_path; the LLM step is stubbed via subprocess.

Verdict markers parsed last-marker-wins via rfind, with cautious tiebreak:
ASSERTION_GAMING wins on tie. Hard gate on ASSERTION_GAMING and missing
marker — both block, matching `never_skip_opus_validation_gate` analogue.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import pytest  # noqa: E402

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.derive_state import replay  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.llm_subprocess import register_backend, reset_backends, StepResult as _StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_5_integrity import (  # noqa: E402
    DEFAULT_INTEGRITY_TIMEOUT_SEC,
    DEFAULT_LLM_COMMAND,
    DIFF_PATCH_RELPATH,
    REVIEW_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    phase_5_integrity_workflow,
)
from bytedigger_engine import workflows  # noqa: E402


# ─── autouse fixture: reset _BACKENDS singleton (§1i) ─────────────────────────


_DEFAULT_INTEGRITY_BACKEND = "p5i-default"


@pytest.fixture(autouse=True)
def _reset_backends_integrity(monkeypatch):
    """§1i: restore _BACKENDS singleton + set default backend env after every test.

    25e75663 R2: sets HAL_RUNNER_BACKEND to a no-op default so tests that
    call make_ctx(..., llm_command=...) no longer need to pass a real argv.
    Individual tests override by calling _register_echo / _register_passthrough /
    _register_fail with the same backend name, then monkeypatching env.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", _DEFAULT_INTEGRITY_BACKEND)
    # Register a no-op passthrough backend as the default (overridden per test).
    register_backend(
        _DEFAULT_INTEGRITY_BACKEND,
        _PassthroughBackend(),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    yield
    reset_backends()


# ─── Backend helpers replacing shell stubs (25e75663 R2) ─────────────────────


class _EchoBackend:
    """Replaces echo_stub — returns canned payload as raw_response."""
    def __init__(self, payload: str):
        self._payload = payload

    def __call__(self, **kw) -> _StepResult:
        data: dict = {
            "raw_response": self._payload,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
            "tokens_out": None,
            "tokens_in": None,
        }
        data.update(kw.get("extra_data") or {})
        return _StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_integrity_llm"),
            error=None,
            error_code=None,
            recoverable=True,
        )


class _PassthroughBackend:
    """Replaces passthrough_stub — echoes the prompt as raw_response."""
    def __call__(self, **kw) -> _StepResult:
        data: dict = {
            "raw_response": kw.get("prompt", ""),
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
            "tokens_out": None,
            "tokens_in": None,
        }
        data.update(kw.get("extra_data") or {})
        return _StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_integrity_llm"),
            error=None,
            error_code=None,
            recoverable=True,
        )


class _FailBackend:
    """Replaces fail_stub — returns an error StepResult."""
    def __init__(self, exit_code: int = 9):
        self._exit_code = exit_code

    def __call__(self, **kw) -> _StepResult:
        return _StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_integrity_llm"),
            error=f"boom (simulated exit {self._exit_code})",
            error_code="E_LLM_EXIT",
            recoverable=False,
        )


# ─── Legacy stub functions (kept for reference; no longer passed via llm_command=) ──

def echo_stub(payload: str) -> list[str]:
    """Legacy subprocess stub — kept for reference; replaced by _EchoBackend."""
    return ["python3", "-c", f"import sys; sys.stdin.read(); sys.stdout.write({payload!r})"]


def passthrough_stub() -> list[str]:
    """Legacy subprocess stub — kept for reference; replaced by _PassthroughBackend."""
    return ["python3", "-c", "import sys; sys.stdout.write(sys.stdin.read())"]


def fail_stub(exit_code: int = 9) -> list[str]:
    """Legacy subprocess stub — kept for reference; replaced by _FailBackend."""
    return ["python3", "-c", f"import sys; sys.stderr.write('boom'); sys.exit({exit_code})"]


# ─── Backend registration helpers ─────────────────────────────────────────────

_ECHO_BACKEND = "p5i-echo"
_PASS_BACKEND = "p5i-pass"
_FAIL_BACKEND = "p5i-fail"
_MARKER_BACKEND = "p5i-marker"


def _register_echo(payload: str, name: str = _ECHO_BACKEND) -> str:
    """Register an echo backend and return the backend name for HAL_RUNNER_BACKEND."""
    register_backend(name, _EchoBackend(payload), manifest_source="harness_tool_record", overwrite=True)
    return name


def _register_passthrough(name: str = _PASS_BACKEND) -> str:
    register_backend(name, _PassthroughBackend(), manifest_source="harness_tool_record", overwrite=True)
    return name


def _register_fail(exit_code: int = 9, name: str = _FAIL_BACKEND) -> str:
    register_backend(name, _FailBackend(exit_code), manifest_source="harness_tool_record", overwrite=True)
    return name


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    # 25e75663 R4: drop legacy llm_command= / integrity_llm_command= (subprocess argv keys).
    # _resolve_model reads integrity_model or model (string). Callers that set
    # llm_command= or integrity_llm_command= must now set model= or integrity_model=.
    org_extra.pop("llm_command", None)
    org_extra.pop("integrity_llm_command", None)
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
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


def seed_repo_with_test_diff(repo: Path) -> None:
    """Two commits: first sets baseline test, second modifies it."""
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    commit_file(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 1\n", "red")
    commit_file(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 2\n", "green-gamed")


def seed_repo_no_test_diff(repo: Path) -> None:
    """Two commits, neither touches a test file."""
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    commit_file(repo, "src/foo.py", "def foo():\n    return 2\n", "code-only")


def seed_spec(scratchpad: Path, body: str = "## Spec\nfoo() returns 2\n") -> Path:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body)
    return spec


# ─── shape ────────────────────────────────────────────────────────────────────


def test_workflow_definition_shape():
    wf = phase_5_integrity_workflow()
    assert wf.name == "phase_5_integrity"
    assert [s.name for s in wf.steps] == [
        "build_integrity_prompt",
        "invoke_integrity_llm",
        "classify_diff_verdict",
        "schema_smoke",
    ]


def test_defaults_are_sensible():
    # Wave 6 (CRIT #7): integrity gate is HARD GATE — assertion-gaming detection
    # requires Opus reasoning. Haiku-default was the bug; chokepoint in
    # invoke_llm_subprocess (hard_gate=True, gate_label="integrity") now refuses
    # any non-Opus default. See test_llm_subprocess_hard_gate.py.
    # 955657B2 — model now sourced from ModelConfig (claude.critical = "opus").
    # Hard-gate chokepoint accepts "opus" alias via _is_opus_model (`m == "opus"`),
    # equivalent enforcement to the prior pinned "claude-opus-4-7" full ID.
    assert DEFAULT_LLM_COMMAND == ["claude", "-p", "--model", "opus"]
    assert DEFAULT_INTEGRITY_TIMEOUT_SEC == 600


def test_canonical_doc_paths():
    assert DIFF_PATCH_RELPATH == "integrity/test-diff.patch"
    assert REVIEW_DOC_RELPATH == "reviews/build-integrity-review.md"


# ─── diff collection ──────────────────────────────────────────────────────────


def test_diff_written_to_scratchpad(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    patch = scratchpad / DIFF_PATCH_RELPATH
    assert patch.is_file()
    body = patch.read_text()
    assert "tests/test_foo.py" in body
    assert "assert foo() == 2" in body


def test_empty_diff_short_circuits_to_no_changes(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    # Use default passthrough for LLM — must not be called when diff is empty.
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    assert result.status == "ok"
    assert result.data["verdict"] == "NO_CHANGES"
    assert result.data["skipped"] is True
    # Diff file is still written (0 bytes), but review doc is NOT.
    assert (scratchpad / DIFF_PATCH_RELPATH).is_file()
    assert (scratchpad / DIFF_PATCH_RELPATH).read_text() == ""
    assert not (scratchpad / REVIEW_DOC_RELPATH).exists()


def test_custom_diff_command_override(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    # Use a custom diff command that limits to a deeper ref window.
    custom = ["git", "diff", "HEAD~2..HEAD", "--", "*test*"]

    _register_echo("VERDICT: ASSERTION_GAMING\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), diff_command=custom),
    )

    patch = (scratchpad / DIFF_PATCH_RELPATH).read_text()
    # HEAD~2..HEAD in this seed includes the original add of tests/test_foo.py
    assert "tests/test_foo.py" in patch


def test_pre_red_ref_override(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), pre_red_ref="HEAD~2"),
    )

    patch = (scratchpad / DIFF_PATCH_RELPATH).read_text()
    assert "tests/test_foo.py" in patch


def test_diff_command_failure_blocks_workflow(tmp_path):
    """Bad ref → git exits non-zero → workflow errors before LLM call."""
    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/foo.py", "x\n", "init")
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_red_ref="HEAD~999",  # nonexistent — diff cmd fails before LLM
        ),
    )

    assert result.status == "error"
    assert result.error_code == "E_DIFF_EXIT"
    assert not (scratchpad / REVIEW_DOC_RELPATH).exists()


# ─── prompt content (token-spend guards) ──────────────────────────────────────


def test_prompt_references_diff_by_path_not_inlined(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    diff_path = scratchpad / DIFF_PATCH_RELPATH
    # Prompt references diff path
    assert str(diff_path) in review
    # But diff CONTENTS aren't inlined into the prompt
    assert "diff --git" not in review
    assert "+    assert foo() == 2" not in review


def test_prompt_references_spec_by_path_when_present(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad, "SPEC_BODY_DO_NOT_INLINE\n")

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    spec_path = scratchpad / SPEC_DOC_RELPATH
    assert str(spec_path) in review
    assert "SPEC_BODY_DO_NOT_INLINE" not in review


def test_prompt_handles_missing_spec_gracefully(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    assert "SPEC: (none" in review


def test_prompt_includes_anti_hallucination_fragment(tmp_path):
    """Agreement 3B0E1323 Step 2: phase_5_integrity HARD GATE must inject the
    anti_hallucination plugin's prompt fragment so the reviewer cannot file
    findings without verbatim evidence quotes — same protection now used by
    phase_5_validation + phase_6_review.
    """
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    # Anchors that uniquely identify the anti-hallucination plugin fragment.
    assert "ANTI-FABRICATION" in review
    assert "EVIDENCE QUOTE" in review
    assert "build 3E8E3A2A" in review  # failure-mode example anchor


def test_prompt_lists_read_first_paths(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    inj = scratchpad / "injection"
    for name in ("hal-memory", "constitution", "quality-gate", "active-work"):
        assert f"{inj}/{name}.md" in review


def test_role_template_prepended(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("# Read-only role\n")

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), role_template_path=str(role)),
    )

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    assert "# Read-only role" in review


# ─── verdict parsing (last-marker-wins) ───────────────────────────────────────


def test_verdict_spec_change(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("body\nVERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "ok"
    assert result.data["verdict"] == "SPEC_CHANGE"


def test_verdict_legitimate_refactor(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("VERDICT: LEGITIMATE_REFACTOR\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "ok"
    assert result.data["verdict"] == "LEGITIMATE_REFACTOR"


def test_verdict_assertion_gaming_blocks(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("Caught it.\nVERDICT: ASSERTION_GAMING\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "error"
    assert result.error_code == "E_INTEGRITY_ASSERTION_GAMING"
    assert result.data["verdict"] == "ASSERTION_GAMING"
    # Doc still written for human inspection
    assert (scratchpad / REVIEW_DOC_RELPATH).is_file()


def test_verdict_missing_marker_blocks(tmp_path):
    """Cautious default: no verdict → block. Mirrors validation gate UNKNOWN→FAIL."""
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("Reviewed but forgot the verdict line.\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "error"
    assert result.error_code == "E_INTEGRITY_NO_MARKER"
    assert result.data["verdict"] == "UNKNOWN"


def test_last_marker_wins(tmp_path):
    """Earlier ASSERTION_GAMING quoted in scratch text, then resolved to SPEC_CHANGE."""
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    body = (
        "I considered VERDICT: ASSERTION_GAMING but ruled it out.\n"
        "Final classification:\n"
        "VERDICT: SPEC_CHANGE\n"
    )
    _register_echo(body, name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "ok"
    assert result.data["verdict"] == "SPEC_CHANGE"


def test_assertion_gaming_wins_when_listed_after_spec_change(tmp_path):
    """Reviewer initially leans SPEC_CHANGE, then finds gaming on closer look."""
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    body = (
        "First pass: VERDICT: SPEC_CHANGE.\n"
        "On second look I found gamed assertions:\n"
        "VERDICT: ASSERTION_GAMING\n"
    )
    _register_echo(body, name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.error_code == "E_INTEGRITY_ASSERTION_GAMING"


def test_verdict_markdown_bold_tolerated(tmp_path):
    """66047463: Opus integrity reviewer drifted to '**Verdict: spec_change**'
    (markdown-bold + lowercase) on forge-1777701765 instead of the canonical
    'VERDICT: SPEC_CHANGE'. Strict parser returned UNKNOWN → E_INTEGRITY_NO_MARKER.

    Parser must tolerate: leading/trailing **/__ markdown emphasis, mixed case
    on both VERDICT keyword and verdict name. Last-marker-wins semantics
    preserved.
    """
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("Reviewed.\n**Verdict: spec_change**\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "ok"
    assert result.data["verdict"] == "SPEC_CHANGE"


def test_verdict_underscore_bold_tolerated(tmp_path):
    """Same drift, __underscore__ emphasis variant."""
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("__VERDICT: LEGITIMATE_REFACTOR__\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "ok"
    assert result.data["verdict"] == "LEGITIMATE_REFACTOR"


def test_verdict_assertion_gaming_lowercase_bold_blocks(tmp_path):
    """ASSERTION_GAMING must still BLOCK even when the LLM bolds and lowercases it."""
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("Caught it.\n**verdict: assertion_gaming**\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "error"
    assert result.error_code == "E_INTEGRITY_ASSERTION_GAMING"
    assert result.data["verdict"] == "ASSERTION_GAMING"


def test_prompt_lists_all_markers_doesnt_fool_parser(tmp_path):
    """The prompt itself enumerates all four markers in its output schema.
    With passthrough, the response echoes that schema. Last marker (rfind)
    is the last one listed in the schema — verify what we get is deterministic."""
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    # Under trailing-tolerant parsing (allow_trailing=True, post-GREEN), the schema's
    # indented glossed example lines DO match (the head anchor allows leading
    # whitespace, ^[ \t]*), so the echo now parses; last-match-wins lands on an
    # ASSERTION_GAMING variant from the WRONG-forms block (e.g. the lowercase
    # "Verdict: assertion_gaming", matched case-insensitively). A pure schema-echo
    # with no real decision therefore still BLOCKS. The durable safety property we
    # assert is result.status == "error" — regardless of which specific error_code
    # fires (E_INTEGRITY_NO_MARKER pre-GREEN, E_INTEGRITY_ASSERTION_GAMING post-GREEN).
    assert result.status == "error"


# ─── per-step command override ────────────────────────────────────────────────


def test_integrity_llm_command_overrides_global(tmp_path):
    """25e75663: integrity_llm_command → integrity_model override.

    Under the new model-string seam, the per-step override is expressed as
    integrity_model= in org_config. _resolve_model(cfg, "integrity_model", ...)
    reads integrity_model before falling back to global model. We verify that
    the workflow runs successfully with integrity_model set, producing SPEC_CHANGE.
    """
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            integrity_model="opus",  # per-step model override (was integrity_llm_command=)
        ),
    )
    assert result.status == "ok"
    assert result.data["verdict"] == "SPEC_CHANGE"


def test_per_step_command_falls_back_to_global(tmp_path):
    """25e75663: when integrity_model is absent, global model= is the fallback.

    Register echo backend at the default slot; no integrity_model in org_config.
    _resolve_model falls back to global model (or default). Workflow must succeed.
    """
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo), model="sonnet"),
    )
    assert result.status == "ok"


# ─── error paths ──────────────────────────────────────────────────────────────


def test_missing_scratchpad_dir_raises(tmp_path):
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},  # no scratchpad_dir — must raise ValueError
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    try:
        eng.execute("p5i", ctx)
    except ValueError as e:
        assert "scratchpad_dir" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_llm_failure_blocks_classification(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"

    _register_fail(7, name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )
    assert result.status == "error"
    assert result.error_code == "E_LLM_EXIT"
    # Diff still got written, but review doc never did
    assert (scratchpad / DIFF_PATCH_RELPATH).is_file()
    assert not (scratchpad / REVIEW_DOC_RELPATH).exists()


# ─── events + registry ────────────────────────────────────────────────────────


def test_events_emitted_three_steps_on_diff(tmp_path):
    repo = tmp_path / "repo"
    seed_repo_with_test_diff(repo)
    scratchpad = tmp_path / "scratch"
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_5_integrity", phase_5_integrity_workflow())
    eng.execute(
        "phase_5_integrity",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="rid-p5i",
    )

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    assert len(finished) == 4
    assert [e["payload"]["status"] for e in finished] == ["ok"] * 4
    assert [e["payload"]["step_name"] for e in finished] == [
        "build_integrity_prompt",
        "invoke_integrity_llm",
        "classify_diff_verdict",
        "schema_smoke",
    ]

    state = replay(events)
    run = state["runs"]["rid-p5i"]
    assert run["workflow_name"] == "phase_5_integrity"
    assert run["status"] == "ok"


def test_events_emitted_three_steps_on_no_changes(tmp_path):
    """Even on the short-circuit path all three steps emit ok events."""
    repo = tmp_path / "repo"
    seed_repo_no_test_diff(repo)
    scratchpad = tmp_path / "scratch"
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)

    eng = WorkflowEngine(event_log=log)
    eng.register("phase_5_integrity", phase_5_integrity_workflow())
    eng.execute(
        "phase_5_integrity",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="rid-p5i-noop",
    )

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    assert len(finished) == 4
    assert [e["payload"]["status"] for e in finished] == ["ok"] * 4


def test_registry_includes_phase_5_integrity():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_5_integrity" in eng.registered()


# ─── scratchpad-file baseline resolution (AC1, AC4, AC5, AC7) ────────────────


def test_integrity_gate_fires_after_red_commit(tmp_path):
    """AC1: When commit_red_tests writes the RED SHA and GREEN modifies test files
    in the working tree (uncommitted), phase_5_integrity must produce a non-empty
    diff and call classify_diff_verdict — not short-circuit to NO_CHANGES."""
    from bytedigger_engine.workflows.phase_5_integrity import PRE_RED_REF_RELPATH  # noqa: PLC0415

    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")

    # Simulate commit_red_tests: commit RED test file
    commit_file(
        repo,
        "tests/test_foo.py",
        "def test_foo():\n    assert foo() == 1\n",
        "build: red cycle 1 tests",
    )
    red_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()

    # Write RED SHA to scratchpad (what commit_red_tests would do)
    scratchpad = tmp_path / "scratch"
    pre_red_file = scratchpad / PRE_RED_REF_RELPATH
    pre_red_file.parent.mkdir(parents=True, exist_ok=True)
    pre_red_file.write_text(red_sha)

    # GREEN modifies test in working tree (not committed — the real bug scenario)
    (repo / "tests" / "test_foo.py").write_text(
        "def test_foo():\n    assert foo() == 2\n"
    )

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    # Gate must fire — LLM was called, verdict is real (not NO_CHANGES short-circuit)
    assert result.status == "ok"
    assert result.data["verdict"] != "NO_CHANGES"

    # Patch must capture GREEN's working-tree modification
    patch = (scratchpad / DIFF_PATCH_RELPATH).read_text()
    assert "assert foo() == 2" in patch, (
        "expected GREEN modification in patch; got empty or wrong diff"
    )


def test_scratchpad_file_overrides_default_pre_red_ref(tmp_path):
    """AC4: When scratchpad/integrity/pre-red-ref.txt is present and
    org_config does NOT set pre_red_ref, _resolve_diff_command uses the
    scratchpad SHA — not DEFAULT_PRE_RED_REF ('HEAD~1')."""
    from bytedigger_engine.workflows.phase_5_integrity import PRE_RED_REF_RELPATH  # noqa: PLC0415

    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    # Use a single-line assert (no def wrapper) so the diff shows
    # '-assert foo() == 1' with the leading diff marker, matching the assertions below.
    commit_file(repo, "tests/test_foo.py", "assert foo() == 1\n", "red")
    red_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()

    # Write RED SHA to scratchpad — no pre_red_ref in org_config
    scratchpad = tmp_path / "scratch"
    pre_red_file = scratchpad / PRE_RED_REF_RELPATH
    pre_red_file.parent.mkdir(parents=True, exist_ok=True)
    pre_red_file.write_text(red_sha)

    # GREEN modifies test in working tree
    (repo / "tests" / "test_foo.py").write_text("assert foo() == 2\n")

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            # no pre_red_ref — must resolve via scratchpad file
        ),
    )

    assert result.status == "ok"
    # Patch must show GREEN's modification (assert 1 → assert 2).
    # If HEAD~1 was used instead, the patch would show RED adding "assert foo() == 1"
    # but NOT the working-tree substitution to "assert foo() == 2".
    patch = (scratchpad / DIFF_PATCH_RELPATH).read_text()
    assert "-assert foo() == 1" in patch, (
        "expected RED baseline assertion removed in patch (working-tree diff)"
    )
    assert "+assert foo() == 2" in patch, (
        "expected GREEN modification in patch; scratchpad SHA may not have been used"
    )


def test_explicit_pre_red_ref_wins_over_scratchpad(tmp_path):
    """AC5: When org_config.pre_red_ref is set AND scratchpad file is present,
    the explicit org_config value takes precedence."""
    from bytedigger_engine.workflows.phase_5_integrity import PRE_RED_REF_RELPATH  # noqa: PLC0415

    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/foo.py", "initial\n", "init")
    commit_file(repo, "tests/test_foo.py", "def test_foo(): assert True\n", "red")

    # Write an INVALID SHA to scratchpad — if used, git will exit non-zero (E_DIFF_EXIT)
    scratchpad = tmp_path / "scratch"
    pre_red_file = scratchpad / PRE_RED_REF_RELPATH
    pre_red_file.parent.mkdir(parents=True, exist_ok=True)
    pre_red_file.write_text("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

    # GREEN modifies test in working tree
    (repo / "tests" / "test_foo.py").write_text("def test_foo(): assert False\n")

    _register_echo("VERDICT: SPEC_CHANGE\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(
            scratchpad,
            git_cwd=str(repo),
            pre_red_ref="HEAD~1",  # explicit — must win over invalid scratchpad SHA
        ),
    )

    # If scratchpad was consulted without honouring org_config priority,
    # git would fail with E_DIFF_EXIT (invalid SHA). Explicit ref must win.
    assert result.status == "ok", (
        f"expected ok (explicit pre_red_ref wins), got "
        f"status={result.status} code={result.error_code}"
    )


def test_working_tree_diff_form_produces_non_empty_patch(tmp_path):
    """AC7: After RED commits and GREEN modifies test files in the working tree
    (uncommitted), the diff form 'git diff {sha} -- {patterns}' (no ..HEAD)
    produces a non-empty patch capturing the working-tree change.

    With the old two-SHA form 'git diff {sha}..HEAD', HEAD==red_sha, so
    the diff is empty — the original bug. The working-tree form catches it.
    """
    from bytedigger_engine.workflows.phase_5_integrity import PRE_RED_REF_RELPATH  # noqa: PLC0415

    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/foo.py", "def foo(): return 1\n", "init")
    commit_file(
        repo,
        "tests/test_foo.py",
        "def test_foo(): assert foo() == 1\n",
        "build: red cycle 1 tests",
    )
    red_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()

    # Write SHA to scratchpad
    scratchpad = tmp_path / "scratch"
    pre_red_file = scratchpad / PRE_RED_REF_RELPATH
    pre_red_file.parent.mkdir(parents=True, exist_ok=True)
    pre_red_file.write_text(red_sha)

    # GREEN modifies the test assertion in working tree (not committed)
    (repo / "tests" / "test_foo.py").write_text(
        "def test_foo(): assert foo() == 2\n"
    )

    _register_echo("VERDICT: ASSERTION_GAMING\n", name=_DEFAULT_INTEGRITY_BACKEND)
    eng = WorkflowEngine()
    eng.register("p5i", phase_5_integrity_workflow())
    result, _ = eng.execute(
        "p5i",
        make_ctx(scratchpad, git_cwd=str(repo)),
    )

    patch = (scratchpad / DIFF_PATCH_RELPATH).read_text()
    # Patch must be non-empty — the working-tree form captures GREEN's change
    assert patch.strip(), (
        "patch is empty; two-SHA form 'git diff {sha}..HEAD' may still be used "
        "(HEAD==red_sha → empty diff, original bug)"
    )
    # Patch must specifically show the GREEN working-tree modification (assert 2),
    # not just the committed RED addition (assert 1). This distinguishes
    # 'git diff red_sha' (working-tree) from 'git diff HEAD~1..HEAD' (commits only).
    assert "assert foo() == 2" in patch, (
        "expected GREEN modification in patch; diff form may be wrong"
    )


def test_empty_scratchpad_file_raises(tmp_path):
    """A pre-red-ref.txt that exists but is empty after strip must raise ValueError,
    not silently fall through to DEFAULT_PRE_RED_REF ('HEAD~1')."""
    from bytedigger_engine.workflows.phase_5_integrity import PRE_RED_REF_RELPATH, _resolve_diff_command  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ref_file = scratchpad / PRE_RED_REF_RELPATH
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text("")  # empty — simulates truncation / failed write

    try:
        _resolve_diff_command({}, scratchpad)
        raise AssertionError("expected ValueError for empty scratchpad file")
    except ValueError as exc:
        assert "empty" in str(exc).lower() or "pre-red-ref" in str(exc)


def test_whitespace_only_scratchpad_file_raises(tmp_path):
    """A pre-red-ref.txt containing only whitespace is treated the same as empty."""
    from bytedigger_engine.workflows.phase_5_integrity import PRE_RED_REF_RELPATH, _resolve_diff_command  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ref_file = scratchpad / PRE_RED_REF_RELPATH
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text("   \n")

    try:
        _resolve_diff_command({}, scratchpad)
        raise AssertionError("expected ValueError for whitespace-only scratchpad file")
    except ValueError as exc:
        assert "empty" in str(exc).lower() or "pre-red-ref" in str(exc)
