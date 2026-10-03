"""bd#150 RED: every file read that reaches a prompt is classified; the class-I ones are declared (R3.2).

Spec: docs/decisions/2026-10-02-bd150-class-i-inventory.md

Today the packaged prompt fragments (F1/F2/F3), the three spec.md inlines in
`_build_spec_prompt` (S10a/S10b/S10c) and the post-fix pytest report (R1) reach the
prompt undeclared, and no lint compares file-read call sites with an inventory.

AC -> test map
--------------
AC1  test_ac1_f1_prompt_fragment_declared[<6 builders>]
AC2  test_ac2_f2_producer_fragment_declared[<3 builders>]
AC3  test_ac3_f3_security_fragment_declared[red|green]
     test_ac3_f3_security_fragment_org_override_declared[red|green]
AC4  test_ac4_s10a_delta_spec_declared
     test_ac4_s10a_empty_spec_declares_nothing_and_dispatches
AC5  test_ac5_s10b_scaffold_spec_declared_after_decision_doc
AC6  test_ac6_s10c_prior_ship_base_declared
     test_ac6_s10c_stale_sidecar_declares_nothing
AC7  test_ac7_r1_postfix_report_declared[small|tail_capped]
     test_ac7_r1_absent_or_empty_report_declares_nothing
AC8  test_ac8_prompt_bytes_unchanged[f1_integrity|s10c|r1]
AC9  test_ac9_fragment_block_helpers_equal_legacy_views
     test_ac9_missing_security_fragment_still_raises
AC10 test_ac10_real_tree_passes_lint
AC11 test_ac11_new_unkeyed_read_reddens / stale_key / deferred_without_issue /
     class_with_forbidden_issue / unknown_class / empty_note / ordinal_rule
     (r2) test_ac5b_*, test_ac11_g_*, test_ac11_wrapper_* / open_on_any_receiver / alias_imports /
     qualname_* / ordinal_follows_source_position
     (r3) test_ac11_h_* (git_read unkeyed), test_ac11_i_* (wrapper import alias), git_read /
     helpers / scope-corner grammar (comprehension, genexpr, default arg, decorator, bases,
     duplicate qualname), site-bearing problems carry file:line, stale-key problem does not
AC12 test_ac12_inventory_classifies_declared_sites / deferred_sites (exact T1/T2 keys;
     192 on no other key)
AC14 test_ac14_deferred_issues_match_authorship_spec_section4 (regex: bd#N and (#N))
AC14b test_ac14b_standards_context_151_removed
AC15 test_ac15_green_retry_attestation_carries_f2_and_f3 (attestation captured in memory at
     event_log.append) / every_role_template_or_prompt_dict_literal_*
AC16 test_ac16_* (git_read family classification, by-path notes, helper-callers limit)
(AC13 = sibling suites, run by the orchestrator, not tested here.)

Harness: the bd#147 pattern. Real builders (driven as in test_bd141_p4d), the REAL
chokepoint `llm_subprocess.invoke_llm_subprocess` fed `injections=` from the real
`_declared_injections(built.data)` (the exact expression every dispatch site passes,
spec 2.4), a recording backend adapter, and the `model_invocation_attested` event
read from the recorded event log (workflows.md 1l). The builders, `_injected_blocks_record`,
`_declared_injections` and the lint are never mocked. No singleton-resource timing:
the backend registry and telemetry slot are pre-staged and restored by the autouse
fixture (workflows.md 1i).

AC10/AC11/AC12 import `bytedigger_engine.conformance.class_i_lint` lazily inside the
tests so a missing module reddens each AC individually.
"""
from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.conformance.attest import hash_text
from bytedigger_engine.contracts import StepResult, WorkflowContext

EVENT_TYPE = "model_invocation_attested"
F1_ID = "bytedigger_engine/lib/plugins/anti_hallucination/prompt_fragment.md"
F2_ID = "bytedigger_engine/lib/plugins/anti_hallucination/producer_prompt_fragment.md"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _mod(name: str):
    return importlib.import_module(f"bytedigger_engine.workflows.{name}")


def _sym(modname: str, name: str):
    fn = getattr(_mod(modname), name, None)
    assert fn is not None, f"{modname}.{name} is not defined (spec section 2)"
    return fn


def _prev(**data) -> StepResult:
    return StepResult(status="ok", data=dict(data), duration_ms=0, step_name="prev")


_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True, env=_GIT_ENV,
    )
    return out.stdout.strip()


class _Env:
    """One minimal real fixture: a git repo holding a scratchpad directory."""

    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.repo.mkdir(parents=True)
        self.scratch = self.repo / "scratch"
        _git(self.repo, "init", "-q")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "base")
        self.base_sha = _git(self.repo, "rev-parse", "HEAD")
        self.scratch.mkdir()
        self.cfg: dict = {
            "scratchpad_dir": str(self.scratch),
            "model": "sonnet",
            "current_worktree_path": str(self.repo),
            "git_cwd": str(self.repo),
        }

    def ctx(self, **extra) -> WorkflowContext:
        return WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={**self.cfg, **extra},
            question="bd150 feature request",
            session_id="bd150", persona="hal", framework=None, domain=None,
        )

    def dirty_a_txt(self) -> None:
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")

    def commit_a_txt(self) -> str:
        self.dirty_a_txt()
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "fix")
        return _git(self.repo, "rev-parse", "HEAD")

    def path(self, rel: str) -> str:
        return str(self.scratch / rel)


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """Backend registry and telemetry slot are process-wide singletons: pre-stage a
    known baseline before the body and restore after (workflows.md 1i)."""
    noop = lambda *a, **kw: None  # noqa: E731
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", noop)
    for modname in (
        "bytedigger_engine.lib.git_cwd",
        "bytedigger_engine.lib.project_root",
        "bytedigger_engine.lib.observability.emit_resolver",
    ):
        try:
            monkeypatch.setattr(importlib.import_module(modname), "emit_resolver_resolved", noop, raising=False)
        except ImportError:
            pass
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def attests(self) -> list:
        return [p for (t, p, _) in self.events if t == EVENT_TYPE]


class _RecordingAdapter:
    def __init__(self, raw_response: str = "done\n") -> None:
        self.raw_response = raw_response
        self.calls: list = []

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        data = {"raw_response": self.raw_response, "worker_written_paths": [],
                "manifest_source": "harness_tool_record"}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0,
                          step_name=kwargs.get("step_name", "rec"))


def _dispatch(built: StepResult, step_name: str = "invoke_bd150"):
    """Dispatch a built prompt through the REAL chokepoint with the REAL
    `_declared_injections(prev.data)` declaration; only the backend adapter is fake.
    Returns (result, adapter, injections attested; captured in memory at event_log.append)."""
    assert isinstance(built, StepResult) and built.status == "ok" and isinstance(built.data, dict) \
        and built.data.get("prompt"), (
        f"fixture precondition: builder must return an ok prompt, got status="
        f"{getattr(built, 'status', None)!r} error_code={getattr(built, 'error_code', None)!r}")
    declared = _sym("phase_workflows_common", "_declared_injections")(built.data)
    adapter = _RecordingAdapter()
    llm_subprocess.register_backend(
        "claude-subprocess", adapter, manifest_source="harness_tool_record",
        capabilities=frozenset(("manifest", "progress_since", "abort")), overwrite=True,
    )
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="bd150", step_name=step_name)
    result = llm_subprocess.invoke_llm_subprocess(
        prompt=built.data["prompt"], model="sonnet", timeout_sec=30,
        step_name=step_name, injections=declared,
    )
    assert result.status == "ok", (
        f"dispatch must succeed: status={result.status!r} error_code={result.error_code!r} error={result.error!r}")
    assert len(adapter.calls) == 1
    attests = log.attests()
    assert len(attests) == 1, f"expected one {EVENT_TYPE!r} event, got {len(attests)}"
    return result, adapter, attests[0]["injections"]


def _entry(source_id: str, content: str) -> dict:
    return {"source_id": source_id, "sha256": hash_text(content)}


def _with_source(injections: list, source_id: str) -> list:
    return [i for i in injections if i["source_id"] == source_id]


def _assert_declares(built: StepResult, source_id: str, content: str, step_name: str = "invoke_bd150") -> list:
    """'Declares X': the attestation (captured in memory at event_log.append) lists {source_id, sha256(content)} and the
    content occurs in the dispatched prompt."""
    assert content and content in built.data["prompt"], "fixture precondition: block content occurs in the prompt"
    _, _, injections = _dispatch(built, step_name)
    assert _entry(source_id, content) in injections, (
        f"attestation must declare {source_id!r} with sha256 of its content; attested={injections!r}")
    return injections


# ---------------------------------------------------------------------------
# Builder drivers (modelled on test_bd141_p4d_role_template_injections.py)
# ---------------------------------------------------------------------------

def _d_spec_review(env, **x):
    return _mod("phase_45_spec")._build_review_prompt(
        env.ctx(**x), _prev(cycle=1, spec_path=env.path("spec.md")))


def _d_red(env, **x):
    return _mod("phase_5_implement")._build_red_prompt(env.ctx(**x), None)


def _d_validation(env, **x):
    return _mod("phase_5_implement")._build_validation_prompt(
        env.ctx(**x), _prev(red_log_path=env.path("red.md"), spec_path=env.path("spec.md")))


def _d_green(env, **x):
    return _mod("phase_5_implement")._build_green_prompt(
        env.ctx(**x), _prev(spec_path=env.path("spec.md"), red_log_path=env.path("red.md"),
                            validation_doc_path=env.path("validation.md")))


def _d_integrity(env, **x):
    env.dirty_a_txt()
    return _mod("phase_5_integrity")._build_integrity_prompt(
        env.ctx(pre_red_ref=env.base_sha, diff_patterns=["*.txt"], **x), None)


def _d_review(env, **x):
    ref = env.scratch / "integrity" / "pre-red-ref.txt"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(env.base_sha, encoding="utf-8")
    return _mod("phase_6_review")._build_review_prompt(env.ctx(**x), None)


def _d_satisfaction(env, **x):
    return _mod("phase_6_review")._build_satisfaction_prompt(
        env.ctx(**x), _prev(spec_path=env.path("spec.md"), review_doc_path=env.path("review.md"),
                            fix_doc_path=env.path("fix.md")))


def _d_fix_integrity(env, **x):
    fix_sha = env.commit_a_txt()
    return _mod("phase_6_fix_integrity")._build_fix_integrity_prompt(
        env.ctx(pre_fix_sha=env.base_sha, fix_commit_sha=fix_sha, diff_patterns=["*.txt"], **x), None)


def _d_spec_writer(env, prev=None, **x):
    return _mod("phase_45_spec")._build_spec_prompt(env.ctx(**x), prev if prev is not None else _prev())


_F1_BUILDERS = {
    "phase_5_integrity": _d_integrity,
    "phase_6_fix_integrity": _d_fix_integrity,
    "phase_45_spec_review": _d_spec_review,
    "phase_5_validation": _d_validation,
    "phase_6_review": _d_review,
    "phase_6_satisfaction": _d_satisfaction,
}
_F2_BUILDERS = {
    "phase_5_red": _d_red,
    "phase_5_green": _d_green,
}


def _fragment_text(name: str) -> str:
    helper = importlib.import_module("bytedigger_engine.lib.plugins.anti_hallucination.helper")
    return (helper._PLUGIN_DIR / name).read_bytes().decode("utf-8")


# ---------------------------------------------------------------------------
# AC1 / AC2 / AC3 - fragments
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_F1_BUILDERS))
def test_ac1_f1_prompt_fragment_declared(name, tmp_path) -> None:
    """AC1 (F1): each of the 6 builders declares prompt_fragment.md with the sha256 of the file."""
    built = _F1_BUILDERS[name](_Env(tmp_path / "run"))
    _assert_declares(built, F1_ID, _fragment_text("prompt_fragment.md"))


@pytest.mark.parametrize("name", sorted(_F2_BUILDERS))
def test_ac2_f2_producer_fragment_declared(name, tmp_path) -> None:
    """AC2 (F2): each of the 2 producer builders declares producer_prompt_fragment.md
    (bd#89 P3c dropped the phase-7 synthesizer builder)."""
    built = _F2_BUILDERS[name](_Env(tmp_path / "run"))
    _assert_declares(built, F2_ID, _fragment_text("producer_prompt_fragment.md"))


def _security_block_in(injections: list, built: StepResult) -> dict:
    """The declared entry whose source_id names the secure-codegen fragment (default asset)."""
    hits = [i for i in injections if i["source_id"].endswith("secure-codegen-fragment.md")]
    assert len(hits) == 1, f"exactly one security-fragment block must be declared; attested={injections!r}"
    return hits[0]


@pytest.mark.parametrize("name", ["red", "green"])
def test_ac3_f3_security_fragment_declared(name, tmp_path) -> None:
    """AC3 (F3, default asset): RED and GREEN declare the security fragment, source_id == str(path read)."""
    # Looser than 'source_id == str(default path)': the default path is resolved by
    # `default_security_asset` (package vs checkout layout), so we identify the block by its
    # file name and then hold it to the spec's invariant: source_id is the path actually read,
    # and the sha256 is of that file's text.
    driver = {"red": _d_red, "green": _d_green}[name]
    built = driver(_Env(tmp_path / "run"))
    _, _, injections = _dispatch(built)
    entry = _security_block_in(injections, built)
    path = Path(entry["source_id"])
    assert path.is_file(), f"source_id must be the path actually read; got {entry['source_id']!r}"
    text = path.read_text(encoding="utf-8")
    assert entry["sha256"] == hash_text(text)
    assert text in built.data["prompt"]


@pytest.mark.parametrize("name", ["red", "green"])
def test_ac3_f3_security_fragment_org_override_declared(name, tmp_path) -> None:
    """AC3 (F3, org override): security_fragment_path -> source_id is that tmp path, sha256 of its content."""
    custom = tmp_path / "custom-sec-fragment.md"
    text = "CUSTOM-SECURITY-FRAGMENT-150 never eval user input\n"
    custom.write_text(text, encoding="utf-8")
    driver = {"red": _d_red, "green": _d_green}[name]
    built = driver(_Env(tmp_path / "run"), security_fragment_path=str(custom))
    _assert_declares(built, str(custom), text)


# ---------------------------------------------------------------------------
# AC4 / AC5 / AC6 - spec.md inlines
# ---------------------------------------------------------------------------

_STRUCTURED_FINDINGS = [{"id": "F1", "type": "gap", "evidence": "e", "required_action": "a"}]
_STRUCTURED_REVIEW = (
    "# Review\n\n## Findings (structured)\n```json\n"
    '[{"id": "F1", "type": "gap", "evidence": "e", "required_action": "a"}]\n'
    "```\n"
)


def _write_spec(env: _Env, text: str) -> Path:
    spec_path = env.scratch / _mod("phase_45_spec").SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(text, encoding="utf-8")
    return spec_path


def test_ac4_s10a_delta_spec_declared(tmp_path) -> None:
    """AC4 (S10a): cycle 2, delta enabled, structured findings -> delta prompt declares the whole spec.md."""
    env = _Env(tmp_path / "run")
    spec_text = "## Context\nS10A-SPEC-SENTINEL-150 body\n\n"
    spec_path = _write_spec(env, spec_text)
    built = _d_spec_writer(env, prev=_prev(cycle=2, findings="f", structured_findings=_STRUCTURED_FINDINGS))
    assert built.status == "ok" and built.data.get("delta_retry") is True, "fixture precondition: delta path"
    _assert_declares(built, str(spec_path), spec_text)


def test_ac4_s10a_empty_spec_declares_nothing_and_dispatches(tmp_path) -> None:
    """AC4 (S10a): empty spec.md -> no block with that source_id, dispatch not refused."""
    env = _Env(tmp_path / "run")
    spec_path = _write_spec(env, "")
    built = _d_spec_writer(env, prev=_prev(cycle=2, findings="f", structured_findings=_STRUCTURED_FINDINGS))
    assert built.status == "ok" and built.data.get("delta_retry") is True, "fixture precondition: delta path"
    # The builder must have gone through the declaration chokepoint (the data dict carries the
    # key, None for no blocks) -- otherwise this guard would be green for the wrong reason.
    assert "injected_blocks" in built.data, "delta data dict must carry `injected_blocks` (None when empty)"
    _, _, injections = _dispatch(built)
    assert _with_source(injections, str(spec_path)) == []


def test_ac5_s10b_scaffold_spec_declared_after_decision_doc(tmp_path, monkeypatch) -> None:
    """AC5 (S10b): cycle 2, delta disabled, structured findings -> scaffold declares the writer's spec.md,
    after the decision-doc block."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    doc = tmp_path / "decision.md"
    doc_text = "# Decision\n\nS10B-DECISION-SENTINEL-150\n"
    doc.write_text(doc_text, encoding="utf-8")
    env = _Env(tmp_path / "run")
    spec_text = "## Context\nS10B-SPEC-SENTINEL-150 body\n"
    spec_path = _write_spec(env, spec_text)
    built = _d_spec_writer(env, prev=_prev(cycle=2, findings=_STRUCTURED_REVIEW), decision_doc=str(doc))
    assert built.status == "ok" and built.data.get("delta_retry") is False, "fixture precondition: scaffold path"
    injections = _assert_declares(built, str(spec_path), spec_text)
    ids = [i["source_id"] for i in injections]
    assert str(doc) in ids, f"decision-doc block must still be declared; attested={injections!r}"
    assert ids.index(str(doc)) < ids.index(str(spec_path)), "decision doc precedes the spec.md block (prompt order)"


def _ship_spec(env: _Env, text: str) -> Path:
    spec_path = _write_spec(env, text)
    _mod("phase_45_spec")._write_ship_sidecar(str(spec_path))
    return spec_path


def test_ac5b_s10c_plus_s10b_same_source_two_chunks(tmp_path, monkeypatch) -> None:
    """AC5b: cycle 2 scaffold + valid SHIP sidecar -> S10c and S10b inline the same spec.md: two
    chunks with that source_id, spec_text.strip() first, the whole text second."""
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    env = _Env(tmp_path / "run")
    spec_text = "\n\n## Context\nS10C-S10B-SENTINEL-150 body\n\n  \n"
    assert spec_text != spec_text.strip()
    spec_path = _ship_spec(env, spec_text)
    built = _d_spec_writer(env, prev=_prev(cycle=2, findings=_STRUCTURED_REVIEW))
    assert built.status == "ok" and built.data.get("delta_retry") is False, "fixture precondition: scaffold path"
    assert "PRIOR SHIP-SPEC BASE" in built.data["prompt"], "fixture precondition: S10c active"
    _, _, injections = _dispatch(built)
    got = [i["sha256"] for i in _with_source(injections, str(spec_path))]
    assert got == [hash_text(spec_text.strip()), hash_text(spec_text)], (
        f"expected [sha(strip), sha(whole)] for {spec_path}; attested={injections!r}")


def test_ac6_s10c_prior_ship_base_declared(tmp_path) -> None:
    """AC6 (S10c): cycle 1, prior spec + matching SHIP sidecar -> declares sha256 of spec_text.strip()."""
    env = _Env(tmp_path / "run")
    spec_text = "\n\n## Context\nS10C-PRIOR-SPEC-SENTINEL-150\n\n   \n"
    spec_path = _ship_spec(env, spec_text)
    built = _d_spec_writer(env)
    assert built.status == "ok" and built.data.get("delta_retry") is False
    assert "PRIOR SHIP-SPEC BASE" in built.data["prompt"], "fixture precondition: prior-base block active"
    assert spec_text != spec_text.strip()
    _assert_declares(built, str(spec_path), spec_text.strip())


def test_ac6_s10c_stale_sidecar_declares_nothing(tmp_path) -> None:
    """AC6 (S10c): stale sidecar (spec mutated after SHIP) -> no block with the spec path."""
    env = _Env(tmp_path / "run")
    spec_path = _ship_spec(env, "## Context\nORIGINAL-150\n")
    spec_path.write_text("## Context\nMUTATED-AFTER-SHIP-150\n", encoding="utf-8")
    built = _d_spec_writer(env)
    assert built.status == "ok"
    assert "PRIOR SHIP-SPEC BASE" not in built.data["prompt"], "fixture precondition: prior-base block inactive"
    # The builder must still reach the declaration chokepoint for the sibling reads (F1/F2-free
    # spec writer has none today), so the 'declares nothing' claim is checked on the real dispatch.
    _, _, injections = _dispatch(built)
    assert _with_source(injections, str(spec_path)) == []


# ---------------------------------------------------------------------------
# AC7 - R1 post-fix pytest report
# ---------------------------------------------------------------------------

def _report_path(env: _Env) -> Path:
    return env.scratch / _sym("phase_6_review", "_POSTFIX_PYTEST_REPORT_RELPATH")


def _write_report(env: _Env, raw: bytes) -> Path:
    path = _report_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def test_ac7_r1_postfix_report_declared_small(tmp_path) -> None:
    """AC7 (R1): small report -> declares str(report_path) with sha256 of the whole appended text."""
    env = _Env(tmp_path / "run")
    text = "FAILED tests/test_x.py::test_a R1-SMALL-SENTINEL-150\n"
    path = _write_report(env, text.encode("utf-8"))
    built = _d_review(env)
    _assert_declares(built, str(path), text)


def test_ac7_r1_postfix_report_declared_tail_capped(tmp_path) -> None:
    """AC7 (R1): report > 16 KB -> declares the appended tail (last 16 KB), not the whole file."""
    env = _Env(tmp_path / "run")
    cap = _sym("phase_6_review", "_POSTFIX_REPORT_MAX_BYTES")
    raw = (b"HEAD-ONLY-R1-150 " + b"x" * (cap + 4000) + b"\nR1-TAIL-SENTINEL-150\n")
    assert len(raw) > cap
    path = _write_report(env, raw)
    tail = raw[-cap:].decode("utf-8", errors="replace")
    built = _d_review(env)
    assert "HEAD-ONLY-R1-150" not in built.data["prompt"], "fixture precondition: head cut off"
    _assert_declares(built, str(path), tail)


def test_ac7_r1_absent_or_empty_report_declares_nothing(tmp_path) -> None:
    """AC7 (R1): absent report -> no block; empty decoded text -> no block."""
    env = _Env(tmp_path / "run")
    path = _report_path(env)
    built = _d_review(env)
    assert "injected_blocks" in built.data, "phase_6 review data dict must carry `injected_blocks` (F1 is always present)"
    _, _, injections = _dispatch(built)
    assert _with_source(injections, str(path)) == []
    _write_report(env, b"")
    built2 = _d_review(env)
    _, _, injections2 = _dispatch(built2)
    assert _with_source(injections2, str(path)) == []
    # Coupling: the same dispatch must declare F1, so the 'nothing' above is from the real
    # declaration path and not from a builder that never records.
    assert _entry(F1_ID, _fragment_text("prompt_fragment.md")) in injections2


# ---------------------------------------------------------------------------
# AC8 - prompt bytes unchanged (declaration is data beside the prompt)
# ---------------------------------------------------------------------------

def _patch_record_to_none(monkeypatch) -> None:
    for modname in ("phase_workflows_common", "phase_5_integrity",
                    "phase_45_spec", "phase_6_review"):
        monkeypatch.setattr(_mod(modname), "_injected_blocks_record", lambda *a, **k: None, raising=False)


def _ac8_f1_integrity(env):
    return _d_integrity(env)


def _ac8_s10c(env):
    _ship_spec(env, "\n## Context\nAC8-S10C-150\n\n")
    return _d_spec_writer(env)


def _ac8_r1(env):
    _write_report(env, b"AC8 report body R1-150\n")
    return _d_review(env)


@pytest.mark.parametrize("build", [_ac8_f1_integrity, _ac8_s10c, _ac8_r1],
                         ids=["f1_integrity", "s10c", "r1"])
def test_ac8_prompt_bytes_unchanged(build, tmp_path, monkeypatch) -> None:
    """AC8: the prompt built with the declaration in place equals the prompt built with
    `_injected_blocks_record` patched to None (the record is data beside the prompt)."""
    env = _Env(tmp_path / "run")
    with_decl = build(env)
    assert with_decl.status == "ok"
    record = with_decl.data.get("injected_blocks")
    # Coupling to the real declaration: without it this equality is trivially true today.
    assert isinstance(record, dict) and record.get("blocks"), (
        f"fixture precondition: the builder must produce an injected_blocks record; got {record!r}")
    _patch_record_to_none(monkeypatch)
    without_decl = build(env)
    assert without_decl.status == "ok"
    assert not without_decl.data.get("injected_blocks"), "patched build carries no record"
    assert without_decl.data["prompt"] == with_decl.data["prompt"]


# ---------------------------------------------------------------------------
# AC9 - legacy views are one expression over the block helpers
# ---------------------------------------------------------------------------

def test_ac9_fragment_block_helpers_equal_legacy_views(tmp_path) -> None:
    """AC9: get_prompt_fragment() == get_prompt_fragment_block()['content'] (also producer + security)."""
    helper = importlib.import_module("bytedigger_engine.lib.plugins.anti_hallucination.helper")
    block_fn = getattr(helper, "get_prompt_fragment_block", None)
    pblock_fn = getattr(helper, "get_producer_prompt_fragment_block", None)
    assert block_fn is not None and pblock_fn is not None, "helper block accessors are not defined (spec 2.1)"
    block, pblock = block_fn(), pblock_fn()
    assert block == {"source_id": F1_ID, "content": _fragment_text("prompt_fragment.md")}
    assert pblock == {"source_id": F2_ID, "content": _fragment_text("producer_prompt_fragment.md")}
    assert helper.get_prompt_fragment() == block["content"]
    assert helper.get_producer_prompt_fragment() == pblock["content"]
    sec_block = _sym("phase_5_implement", "_security_fragment_block")
    get_sec = _sym("phase_5_implement", "_get_security_fragment")
    custom = tmp_path / "sec.md"
    custom.write_text("SEC-AC9-150\n", encoding="utf-8")
    cfg = {"security_fragment_path": str(custom)}
    assert sec_block(cfg) == {"source_id": str(custom), "content": "SEC-AC9-150\n"}
    assert get_sec(cfg) == sec_block(cfg)["content"]
    assert get_sec({}) == sec_block({})["content"]


def test_ac9_missing_security_fragment_still_raises(tmp_path) -> None:
    """AC9: a missing security fragment still raises FileNotFoundError (fail-closed contract), from both views."""
    cfg = {"security_fragment_path": str(tmp_path / "does-not-exist.md")}
    with pytest.raises(FileNotFoundError):
        _sym("phase_5_implement", "_get_security_fragment")(cfg)
    with pytest.raises(FileNotFoundError):
        _sym("phase_5_implement", "_security_fragment_block")(cfg)


# ---------------------------------------------------------------------------
# AC10 / AC11 / AC12 - the lint
# ---------------------------------------------------------------------------

def _lint():
    return importlib.import_module("bytedigger_engine.conformance.class_i_lint")


def _engine_root() -> Path:
    import bytedigger_engine
    return Path(bytedigger_engine.__file__).parent


def test_ac10_real_tree_passes_lint() -> None:
    """AC10: check(<real bytedigger_engine>, load_inventory()) == [] (every site keyed, no stale key)."""
    lint = _lint()
    assert lint.check(_engine_root(), lint.load_inventory()) == []


_SRC_ONE_READ = "from pathlib import Path\n\ndef f(p):\n    return Path(p).read_text()\n"
_KEY_F0 = "workflows/x.py::f::read_text#0"


def _tree(tmp_path: Path, source: str = _SRC_ONE_READ) -> Path:
    root = tmp_path / "bytedigger_engine"
    (root / "workflows").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "workflows" / "x.py").write_text(source, encoding="utf-8")
    return root


def _inv(sites: dict) -> dict:
    return {"version": 1, "sites": sites}


def _ok_site(**over) -> dict:
    return {"class": "E", "note": "reads p, goes to the engine only", **over}


def _site_problem(problems: list, key: str, line: int) -> bool:
    """A site-bearing problem names the key AND its file:line (spec 2.6, 'Ordinal churn')."""
    import re
    return any(key in p and re.search(rf"x\.py:{line}(?!\d)", p) for p in problems)


def test_ac11_clean_synthetic_tree_is_clean(tmp_path) -> None:
    """AC11 control: a keyed, well-formed site yields no problems (so the red cases below are not
    satisfiable by a lint that always complains)."""
    lint = _lint()
    root = _tree(tmp_path)
    assert lint.check(root, _inv({_KEY_F0: _ok_site()})) == []
    # reads outside workflows/ and lib/ are out of scope (spec Not-in-scope: root-level modules)
    (root / "facts.py").write_text(_SRC_ONE_READ, encoding="utf-8")
    assert lint.check(root, _inv({_KEY_F0: _ok_site()})) == []


def test_ac11_new_unkeyed_read_reddens(tmp_path) -> None:
    """AC11 (a): a new Path(...).read_text() in a workflows/ file with no key is reported, naming the key."""
    lint = _lint()
    problems = lint.check(_tree(tmp_path), _inv({}))
    assert problems and any(_KEY_F0 in p for p in problems), problems
    assert _site_problem(problems, _KEY_F0, 4), f"problem must carry file:line; got {problems}"


def test_ac11_g_unkeyed_bounded_run_reddens(tmp_path) -> None:
    """AC11 (g): a bounded_run(...) call with no key reddens (wrapper callee keyed at each caller)."""
    lint = _lint()
    source = ("from bytedigger_engine.lib.bounded_spawn import bounded_run\n\n"
              "def f():\n    return bounded_run(['x'])\n")
    key = "workflows/x.py::f::bounded_run#0"
    root = _tree(tmp_path, source)
    problems = lint.check(root, _inv({}))
    assert _site_problem(problems, key, 4), problems
    assert lint.check(root, _inv({key: _ok_site()})) == []


_SRC_GIT_ATTR = ("from bytedigger_engine.lib import git_port\n\n"
                 "def f():\n    return git_port.git_read(['status'])\n")
_SRC_GIT_BARE = ("from bytedigger_engine.lib.git_port import git_read\n\n"
                 "def f():\n    return git_read(['status'])\n")
_SRC_BR_ALIAS = ("from bytedigger_engine.lib.bounded_spawn import bounded_run as br\n\n"
                 "def f():\n    return br(['x'])\n")


@pytest.mark.parametrize("source", [_SRC_GIT_ATTR, _SRC_GIT_BARE], ids=["git_port.git_read", "bare_git_read"])
def test_ac11_h_unkeyed_git_read_reddens(source, tmp_path) -> None:
    """AC11 (h): an unkeyed git_port.git_read(...) and an unkeyed bare git_read(...) each redden
    (the git subprocess chokepoint is keyed at each caller); keyed, the tree is clean."""
    lint = _lint()
    key = "workflows/x.py::f::git_read#0"
    root = _tree(tmp_path, source)
    problems = lint.check(root, _inv({}))
    assert _site_problem(problems, key, 4), problems
    assert lint.check(root, _inv({key: _ok_site()})) == []


def test_ac11_i_unkeyed_wrapper_import_alias_reddens(tmp_path) -> None:
    """AC11 (i): an unkeyed br(...) where br is an import alias of bounded_run reddens, keyed as bounded_run."""
    lint = _lint()
    key = "workflows/x.py::f::bounded_run#0"
    root = _tree(tmp_path, _SRC_BR_ALIAS)
    problems = lint.check(root, _inv({}))
    assert _site_problem(problems, key, 4), problems
    assert lint.check(root, _inv({key: _ok_site()})) == []


def test_ac11_stale_key_reddens(tmp_path) -> None:
    """AC11 (b): an inventory key with no call site is reported, naming the key and 'stale', with
    no file:line (there is no site)."""
    import re
    lint = _lint()
    stale = "workflows/x.py::gone::read_text#0"
    problems = lint.check(_tree(tmp_path), _inv({_KEY_F0: _ok_site(), stale: _ok_site()}))
    hits = [p for p in problems if stale in p]
    assert hits, problems
    assert all("stale" in p.lower() for p in hits), hits
    assert not any(re.search(r"\.py:\d+", p) for p in hits), f"stale-key problem has no file:line; got {hits}"
    assert not any(_KEY_F0 in p for p in problems), "the live key must not be reported"


def test_ac11_deferred_without_issue_reddens(tmp_path) -> None:
    """AC11 (c): I-deferred without `issue` is reported; with issue '192' it is clean."""
    lint = _lint()
    root = _tree(tmp_path)
    problems = lint.check(root, _inv({_KEY_F0: _ok_site(**{"class": "I-deferred"})}))
    assert _site_problem(problems, _KEY_F0, 4), problems
    assert lint.check(root, _inv({_KEY_F0: _ok_site(**{"class": "I-deferred", "issue": "192"})})) == []


def test_ac11_class_with_forbidden_issue_reddens(tmp_path) -> None:
    """AC11 (d): class E carrying an `issue` is reported."""
    lint = _lint()
    problems = lint.check(_tree(tmp_path), _inv({_KEY_F0: _ok_site(issue="192")}))
    assert _site_problem(problems, _KEY_F0, 4), problems


def test_ac11_unknown_class_reddens(tmp_path) -> None:
    """AC11 (e): an unknown class is reported."""
    lint = _lint()
    problems = lint.check(_tree(tmp_path), _inv({_KEY_F0: _ok_site(**{"class": "Z"})}))
    assert _site_problem(problems, _KEY_F0, 4), problems


def test_ac11_empty_note_reddens(tmp_path) -> None:
    """AC11 (f): an empty note is reported."""
    lint = _lint()
    problems = lint.check(_tree(tmp_path), _inv({_KEY_F0: _ok_site(note="")}))
    assert _site_problem(problems, _KEY_F0, 4), problems


def test_ac11_ordinal_rule_and_key_format(tmp_path) -> None:
    """AC11: two read_text calls in one function get #0 and #1 (source order); key format is
    '<path>::<qualname or <module>>::<callee>#<n>' with the callee set of spec 2.6."""
    lint = _lint()
    source = (
        "import json\nimport subprocess\n\n"
        "def f(p):\n"
        "    a = p.read_text()\n"
        "    b = p.read_text()\n"
        "    with open(p) as h:\n"
        "        json.load(h)\n"
        "    subprocess.run(['x'])\n\n"
        "class C:\n"
        "    def m(self, p):\n"
        "        return p.read_bytes()\n\n"
        "X = open('m')\n"
    )
    root = _tree(tmp_path, source)
    assert lint.call_sites(root) == sorted([
        "workflows/x.py::<module>::open#0",
        "workflows/x.py::f::read_text#0",
        "workflows/x.py::f::read_text#1",
        "workflows/x.py::f::open#0",
        "workflows/x.py::f::json.load#0",
        "workflows/x.py::f::subprocess.run#0",
        "workflows/x.py::C.m::read_bytes#0",
    ])


def test_ac11_wrapper_callees_keyed_at_each_caller(tmp_path) -> None:
    """AC11 grammar: bounded_run, run_test_command, _read_text_or_empty, _read_or_empty, _git_read are
    keyed at each caller, by bare name or attribute name."""
    lint = _lint()
    source = (
        "def f(m, p):\n"
        "    bounded_run(['a'])\n"
        "    m.bounded_run(['b'])\n"
        "    run_test_command(['c'])\n"
        "    _read_text_or_empty(p)\n"
        "    m._read_or_empty(p)\n"
        "    _git_read(['d'])\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::f::bounded_run#0",
        "workflows/x.py::f::bounded_run#1",
        "workflows/x.py::f::run_test_command#0",
        "workflows/x.py::f::_read_text_or_empty#0",
        "workflows/x.py::f::_read_or_empty#0",
        "workflows/x.py::f::_git_read#0",
    ])


def test_ac11_open_on_any_receiver_keys_as_dot_open(tmp_path) -> None:
    """AC11 grammar: p.open(), io.open(), gzip.open() key as `.open`; the builtin keys as `open`."""
    lint = _lint()
    source = (
        "import io\nimport gzip\n\n"
        "def f(p):\n"
        "    p.open()\n"
        "    io.open('a')\n"
        "    gzip.open('b')\n"
        "    open('c')\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::f::.open#0",
        "workflows/x.py::f::.open#1",
        "workflows/x.py::f::.open#2",
        "workflows/x.py::f::open#0",
    ])


def test_ac11_alias_imports_resolve_to_canonical_callee(tmp_path) -> None:
    """AC11 grammar: `import json as j`, `from json import load as jl`, `import subprocess as sp`,
    `from subprocess import run as r, check_output as co, Popen as P` key in canonical form."""
    lint = _lint()
    source = (
        "import json as j\nimport subprocess as sp\n"
        "from json import load as jl\n"
        "from subprocess import run as r, check_output as co, Popen as P\n\n"
        "def f(h):\n"
        "    j.load(h)\n"
        "    jl(h)\n"
        "    sp.run(['a'])\n"
        "    r(['b'])\n"
        "    co(['c'])\n"
        "    P(['d'])\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::f::json.load#0",
        "workflows/x.py::f::json.load#1",
        "workflows/x.py::f::subprocess.run#0",
        "workflows/x.py::f::subprocess.run#1",
        "workflows/x.py::f::subprocess.check_output#0",
        "workflows/x.py::f::subprocess.Popen#0",
    ])


def test_ac11_qualname_nested_lambda_async_class_module(tmp_path) -> None:
    """AC11 grammar: Python __qualname__ scopes; nested scopes count ordinals separately."""
    lint = _lint()
    source = (
        "def outer(p):\n"
        "    p.read_text()\n"
        "    def inner(q):\n"
        "        return q.read_text()\n"
        "    g = lambda q: q.read_text()\n"
        "    return inner, g\n\n"
        "async def af(p):\n"
        "    return p.read_text()\n\n"
        "class K:\n"
        "    data = open('z')\n\n"
        "M = open('m')\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::outer::read_text#0",
        "workflows/x.py::outer.<locals>.inner::read_text#0",
        "workflows/x.py::outer.<locals>.<lambda>::read_text#0",
        "workflows/x.py::af::read_text#0",
        "workflows/x.py::K::open#0",
        "workflows/x.py::<module>::open#0",
    ])


def test_ac11_ordinal_follows_source_position(tmp_path) -> None:
    """AC11 grammar: ordinal is by (lineno, col_offset): with only #0 keyed, the unkeyed #1 problem
    points at the LATER line."""
    lint = _lint()
    source = "def f(p):\n    a = p.read_text()\n    b = p.read_text()\n"
    root = _tree(tmp_path, source)
    problems = lint.check(root, _inv({"workflows/x.py::f::read_text#0": _ok_site()}))
    assert len(problems) == 1 and "workflows/x.py::f::read_text#1" in problems[0] and "x.py:3" in problems[0], problems


def test_ac11_git_read_family_keyed_at_each_caller_with_aliases(tmp_path) -> None:
    """AC11 grammar (r3): git_port.git_read, git_port_mod.git_read, bare git_read and an import alias
    (`git_read as gr`) all key as `git_read#n` at the caller; the git_port helpers key by their own
    names, bare or attribute."""
    lint = _lint()
    source = (
        "from bytedigger_engine.lib import git_port\n"
        "import bytedigger_engine.lib.git_port as git_port_mod\n"
        "from bytedigger_engine.lib.git_port import git_read, git_read as gr\n\n"
        "def f():\n"
        "    git_port.git_read(['a'])\n"
        "    git_port_mod.git_read(['b'])\n"
        "    git_read(['c'])\n"
        "    gr(['d'])\n\n"
        "def g(r):\n"
        "    git_port.rev_parse(r)\n"
        "    git_port.status_porcelain(r)\n"
        "    git_port.ls_files_others(r)\n"
        "    git_port.worktree_list_porcelain(r)\n"
        "    from bytedigger_engine.lib.git_port import rev_parse\n"
        "    rev_parse(r)\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::f::git_read#0",
        "workflows/x.py::f::git_read#1",
        "workflows/x.py::f::git_read#2",
        "workflows/x.py::f::git_read#3",
        "workflows/x.py::g::rev_parse#0",
        "workflows/x.py::g::rev_parse#1",
        "workflows/x.py::g::status_porcelain#0",
        "workflows/x.py::g::ls_files_others#0",
        "workflows/x.py::g::worktree_list_porcelain#0",
    ])


def test_ac11_wrapper_import_alias_keys_as_canonical_wrapper(tmp_path) -> None:
    """AC11 grammar (r3): `from ...bounded_spawn import bounded_run as br; br(...)` keys as bounded_run."""
    lint = _lint()
    assert lint.call_sites(_tree(tmp_path, _SRC_BR_ALIAS)) == ["workflows/x.py::f::bounded_run#0"]


def test_ac11_comprehension_and_genexpr_open_no_scope(tmp_path) -> None:
    """AC11 grammar (r3): list/dict/set comprehensions and generator expressions open no scope; a read
    inside one keys under the enclosing function (or <module>)."""
    lint = _lint()
    source = (
        "def f(ps):\n"
        "    a = [p.read_text() for p in ps]\n"
        "    b = (p.read_bytes() for p in ps)\n"
        "    c = {p: p.read_text() for p in ps}\n"
        "    d = {p.read_bytes() for p in ps}\n"
        "    return a, b, c, d\n\n"
        "E = [q.read_text() for q in ()]\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::f::read_text#0",
        "workflows/x.py::f::read_text#1",
        "workflows/x.py::f::read_bytes#0",
        "workflows/x.py::f::read_bytes#1",
        "workflows/x.py::<module>::read_text#0",
    ])


def test_ac11_default_decorator_bases_belong_to_containing_scope(tmp_path) -> None:
    """AC11 grammar (r3): default-argument values, decorators, base classes and class keywords belong to
    the scope CONTAINING the def/class statement, not to it."""
    lint = _lint()
    source = (
        "@deco(P.read_text())\n"
        "def top(x=Q.read_text()):\n"
        "    return 1\n\n"
        "class C(base(R.read_text()), meta=S.read_text()):\n"
        "    pass\n\n"
        "def outer():\n"
        "    @deco(T.read_text())\n"
        "    def inner(y=U.read_text()):\n"
        "        return 1\n"
        "    return inner\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::<module>::read_text#0",
        "workflows/x.py::<module>::read_text#1",
        "workflows/x.py::<module>::read_text#2",
        "workflows/x.py::<module>::read_text#3",
        "workflows/x.py::outer::read_text#0",
        "workflows/x.py::outer::read_text#1",
    ])


def test_ac11_duplicate_qualname_defs_pool_their_ordinals(tmp_path) -> None:
    """AC11 grammar (r3): several defs sharing one qualname (conditional def in try/except) pool their
    sites under that qualname: #0, #1 (no collision)."""
    lint = _lint()
    source = (
        "try:\n"
        "    def f(p):\n"
        "        return p.read_text()\n"
        "except ImportError:\n"
        "    def f(p):\n"
        "        return p.read_text()\n"
    )
    assert lint.call_sites(_tree(tmp_path, source)) == sorted([
        "workflows/x.py::f::read_text#0",
        "workflows/x.py::f::read_text#1",
    ])


# ---------------------------------------------------------------------------
# AC12 - the shipped inventory classifies the named sites
# ---------------------------------------------------------------------------

def _sites() -> dict:
    inv = _lint().load_inventory()
    assert inv.get("version") == 1
    return inv["sites"]


def _parts(key: str) -> tuple:
    path, qual, callee = key.split("::")
    return path, qual, callee.rsplit("#", 1)[0]


def _select(path: str, quals: set, callees: set) -> dict:
    out = {}
    for key, val in _sites().items():
        p, q, c = _parts(key)
        if p == path and q in quals and c in callees:
            out[key] = val
    return out


def test_ac12_inventory_classifies_declared_sites() -> None:
    """AC12: S10a/S10b/S10c, F1/F2/F3 and R1 sites are I-declared."""
    # Looser than pinning exact keys: the qualnames GREEN gives its new helpers are spec-fixed only
    # where the spec names them (helper block accessors, _security_fragment_block,
    # _prior_ship_base_inline); S10c may sit in the old or new function, so both are accepted.
    groups = {
        "S10a+S10b (_build_spec_prompt spec.md reads)":
            (_select("workflows/phase_45_spec.py", {"_build_spec_prompt"}, {"read_text"}), 2),
        "S10c (prior SHIP base)":
            (_select("workflows/phase_45_spec.py", {"_prior_ship_base_block", "_prior_ship_base_inline"},
                     {"read_text"}), 1),
        "F1+F2 (helper block accessors)":
            (_select("lib/plugins/anti_hallucination/helper.py",
                     {"get_prompt_fragment_block", "get_producer_prompt_fragment_block"}, {"read_text"}), 2),
        "F3 (_security_fragment_block)":
            (_select("workflows/phase_5_implement.py", {"_security_fragment_block"}, {"read_text"}), 1),
        "R1 (_load_postfix_pytest_report)":
            (_select("workflows/phase_6_review.py", {"_load_postfix_pytest_report"}, {"read_bytes"}), 1),
    }
    for label, (found, minimum) in groups.items():
        assert len(found) >= minimum, f"{label}: expected >= {minimum} inventory site(s), found {sorted(found)}"
        for key, val in found.items():
            assert val.get("class") == "I-declared", f"{label}: {key} must be I-declared, got {val.get('class')!r}"


def test_ac12_inventory_classifies_deferred_sites() -> None:
    """AC12: the exact T1/T2 keys of spec 2.5 are I-deferred with issue '192', and '192' sits only there."""
    t1 = "workflows/phase_5_implement.py::_verify_green_passing::_read_text_or_empty#0"
    t2 = "workflows/phase_5_implement.py::_red_collect_probe::bounded_run#0"
    sites = _sites()
    for key in (t1, t2):
        assert key in sites, f"{key} missing from the inventory"
        assert sites[key].get("class") == "I-deferred" and sites[key].get("issue") == "192", sites[key]
    with_192 = sorted(k for k, v in sites.items() if v.get("issue") == "192")
    assert with_192 == sorted([t1, t2]), f"issue 192 must be referenced only on T1/T2; got {with_192}"


# ---------------------------------------------------------------------------
# AC14 - inventory <-> AUTHORSHIP_SPEC section 4 tie
# ---------------------------------------------------------------------------

def _not_yet_declared_issues() -> set:
    import re
    text = _authorship_text()
    start = text.index("**Not yet declared:**")
    end = text.index("Any class-I segment", start)
    return {a or b for a, b in re.findall(r"bd#(\d+)|\(#(\d+)\)", text[start:end])}


def _authorship_text() -> str:
    return (_engine_root() / "conformance" / "AUTHORSHIP_SPEC.md").read_text(encoding="utf-8")


def test_ac14_deferred_issues_match_authorship_spec_section4() -> None:
    """AC14: every I-deferred issue number appears as bd#<N> in the 'not yet declared' list, and every
    bd#<N> there has at least one I-deferred entry."""
    listed = _not_yet_declared_issues()
    deferred = {v.get("issue") for v in _sites().values() if v.get("class") == "I-deferred"}
    assert deferred and "192" in deferred, f"inventory must defer T1/T2 to 192; got {deferred}"
    assert deferred == listed, f"I-deferred issues {sorted(deferred)} != bd# list in AUTHORSHIP_SPEC s4 {sorted(listed)}"


def test_ac14b_standards_context_151_removed() -> None:
    """AC14b (spec 2.7): the 'not yet declared' list names no 151 (bd#151 or (#151)), and the R3.2 table
    row no longer names the standards context (#151). Coupled to the lint pairing (AC14): the list must
    still be anchored and carry the 192 deferral."""
    import re
    text = _authorship_text()
    start = text.index("**Not yet declared:**")
    end = text.index("Any class-I segment", start)
    section = text[start:end]
    assert "151" not in _not_yet_declared_issues() and not re.search(r"(bd#|\(#)151", section), section
    assert "192" in _not_yet_declared_issues(), "list must carry the bd#192 deferral"
    rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("|") and "`R3.2`" in ln]
    assert rows, "R3.2 table row not found"
    for row in rows:
        assert "#151" not in row and "standards context" not in row.lower(), row


# ---------------------------------------------------------------------------
# AC15 - GREEN retry carries the declaration (spec 2.4, workflows.md 1ab)
# ---------------------------------------------------------------------------

def test_ac15_green_retry_attestation_carries_f2_and_f3(tmp_path) -> None:
    """AC15: _invoke_green_llm -> _write_green_artifact (GREEN_NO_MARKER retry): both attestations
    (captured in memory at event_log.append) carry the same F2 and F3 entries."""
    env = _Env(tmp_path / "run")
    built = _d_green(env)
    assert built.status == "ok" and built.data.get("prompt")
    adapter = _RecordingAdapter()  # raw_response "done\n": no GREEN marker -> retry once
    llm_subprocess.register_backend(
        "claude-subprocess", adapter, manifest_source="harness_tool_record",
        capabilities=frozenset(("manifest", "progress_since", "abort")), overwrite=True,
    )
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="bd150", step_name="invoke_green_llm")
    impl = _mod("phase_5_implement")
    data = {"log_path": env.path("green.md"), "spec_path": env.path("spec.md"),
            "red_log_path": env.path("red.md"), "validation_doc_path": env.path("validation.md"),
            **built.data}
    ctx = env.ctx()
    invoked = impl._invoke_green_llm(ctx, StepResult(status="ok", data=data, duration_ms=0, step_name="prev"))
    assert invoked.status == "ok", (invoked.status, invoked.error_code, invoked.error)
    impl._write_green_artifact(ctx, invoked)
    assert len(adapter.calls) == 2, "fixture precondition: first attempt + one GREEN_NO_MARKER retry"
    attests = log.attests()
    assert len(attests) == 2, f"expected two attestations, got {len(attests)}"
    first, retry = attests[0]["injections"], attests[1]["injections"]
    f2 = _entry(F2_ID, _fragment_text("producer_prompt_fragment.md"))
    assert f2 in first, f"first attempt must declare F2; attested={first!r}"
    assert f2 in retry, f"retry must declare F2; attested={retry!r}"
    sec_first = _security_block_in(first, built)
    assert sec_first in retry, f"retry must declare the same F3 entry; attested={retry!r}"


def test_ac15_every_role_template_or_prompt_dict_literal_also_has_injected_blocks() -> None:
    """AC15 (AST, widened r3): every dict literal under workflows/ with a "role_template" key OR a
    "prompt" key also has "injected_blocks" (value may be None)."""
    import ast
    bad = []
    for path in sorted((_engine_root() / "workflows").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if keys & {"role_template", "prompt"} and "injected_blocks" not in keys:
                    bad.append(f"{path.name}:{node.lineno}")
    assert bad == [], f"dict literals with role_template/prompt but no injected_blocks: {bad}"


# ---------------------------------------------------------------------------
# AC16 - the git_read family is classified (spec 2.6, MAJOR-1)
# ---------------------------------------------------------------------------

_VALID_CLASSES = {"not-prompt", "I-declared", "I-deferred", "E", "M"}


def _git_read_keys() -> list:
    lint = _lint()
    return [k for k in lint.call_sites(_engine_root()) if _parts(k)[2] == "git_read"]


def test_ac16_every_git_read_site_is_keyed_not_prompt_or_i_with_note() -> None:
    """AC16: the real inventory has a key for every git_read call site found by call_sites, and each
    is not-prompt or I-* with a non-empty note."""
    keys = _git_read_keys()
    assert len(keys) >= 20, f"call_sites must see the git_read family (88 sites measured); got {len(keys)}"
    sites = _sites()
    for key in keys:
        assert key in sites, f"git_read site {key} has no inventory key"
        cls = sites[key].get("class")
        assert cls == "not-prompt" or str(cls).startswith("I-"), (key, cls)
        assert isinstance(sites[key].get("note"), str) and sites[key]["note"].strip(), key


@pytest.mark.parametrize("path,scope", [
    ("workflows/phase_5_integrity.py", "_build_integrity_prompt"),
    ("workflows/phase_6_fix_integrity.py", "_build_fix_integrity_prompt"),
])
def test_ac16_prompt_builder_git_reads_are_not_prompt_by_path(path, scope) -> None:
    """AC16: git_read keys in the two prompt builders hand the diff over by path: class not-prompt and
    the note contains 'path'."""
    keys = [k for k in _git_read_keys() if _parts(k)[0] == path and _parts(k)[1] == scope]
    assert keys, f"expected git_read call sites in {path}::{scope}"
    sites = _sites()
    for key in keys:
        assert sites[key].get("class") == "not-prompt", (key, sites[key])
        assert "path" in sites[key].get("note", ""), (key, sites[key])


def test_ac16_git_port_helpers_have_no_callers_outside_git_port() -> None:
    """AC16: no module under workflows/ or lib/ other than lib/git_port.py calls rev_parse /
    ls_files_others / worktree_list_porcelain (the 2.6 limit is checked, not assumed)."""
    lint = _lint()
    keys = lint.call_sites(_engine_root())
    helpers = {"rev_parse", "ls_files_others", "worktree_list_porcelain"}
    outside = [k for k in keys if _parts(k)[2] in helpers and _parts(k)[0] != "lib/git_port.py"]
    assert outside == [], f"git_port helper called outside lib/git_port.py: {outside}"
    # Coupling: the lint must see the family at all (else 'outside == []' is vacuous).
    assert any(_parts(k)[2] == "git_read" for k in keys), "call_sites must key git_read sites"
