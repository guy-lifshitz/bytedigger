"""bd#147 RED: file-sourced prompt segments are declared on the `injections` channel (R3.2).

Spec: docs/decisions/2026-10-02-bd147-injected-segments.md

Today only the role template is declared. The decision doc (S1, phase_45_spec), the
in-scope test files (S2, phase_6_review fix prompt) and the directed-repair artifact
(S3, lib/directed_repair) are inlined from files with no declaration, and
`_declared_injections` ignores anything but `role_template`.

AC -> test map
--------------
AC1  test_ac1_decision_doc_whole_declared[plain|with_role_template]
AC1b test_ac1b_decision_doc_bound_to_final_prompt_after_standards_prepend
AC2  test_ac2_decision_doc_truncated_declares_head_then_tail
     test_ac2_multibyte_over_byte_cap_declares_one_whole_block
AC3  test_ac3_inscope_test_files_declared_in_surviving_order
AC4  test_ac4_fix_retry_declares_same_inscope_blocks
AC5  test_ac5_directed_repair_declares_artifact
     test_ac5_unreadable_artifact_declares_nothing       (guard: green today)
AC6  test_ac6_decision_doc_whole_bytes_and_attestation
     test_ac6_decision_doc_truncated_bytes_and_attestation
     test_ac6_inscope_test_files_bytes_and_attestation
AC7  test_ac7_stale_record_is_not_declared
     test_ac7_forwarded_stale_record_dispatches_ok_through_chokepoint
     test_ac7_injected_blocks_record_helper
AC8  test_ac8_malformed_records_are_inert
     test_ac8_malformed_elements_fail_closed_helper
     test_ac8_malformed_elements_refused_through_chokepoint
AC10 test_ac10_bom_only_decision_doc_declares_no_block
     test_ac10_empty_test_file_not_declared_sibling_still_is
AC9  test_ac9_decision_doc_legacy_view_equals_inline_head
     test_ac9_inscope_legacy_view_equals_inline_first_three

The existing suites test_phase_45_spec_decision_doc_injection.py and
test_phase_6_fix_inline_head_tests_65EA1B86.py must stay unmodified and green (AC9).

Fixtures are real: temp git repo + temp scratchpad, the real producers, the real invoke
steps and the real chokepoint (`invoke_llm_subprocess`, `_dispatch_backend`,
`_injection_refusal`, attestation). Only the backend adapter (AC6/AC7 e2e) or the
`invoke_llm_subprocess` module attribute (spy tests) are doubled. No singleton-resource
timing is involved (workflows.md 1i: the backend registry and telemetry slot are
pre-staged and restored by the autouse fixture).
"""
from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.conformance.attest import InjectedBlock, hash_text
from bytedigger_engine.contracts import StepResult, WorkflowContext

ROLE_FILE_TEXT = "ROLE TEMPLATE LINE 1\nROLE TEMPLATE LINE 2\n   \n"
ROLE_CONTENT = ROLE_FILE_TEXT.rstrip() + "\n\n"
EVENT_TYPE = "model_invocation_attested"
BOM = b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# Harness (local copy of the bd#141 4(d) pattern)
# ---------------------------------------------------------------------------

def _mod(name: str):
    return importlib.import_module(f"bytedigger_engine.workflows.{name}")


def _sym(modname: str, name: str):
    """Lazy lookup so collection succeeds on the base commit and each test fails
    at assertion time with a precise message."""
    fn = getattr(_mod(modname), name, None)
    assert fn is not None, f"{modname}.{name} is not defined (spec section 3)"
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


def _make_role_file(root: Path) -> tuple[Path, str]:
    real = root / "real_roles"
    real.mkdir(parents=True)
    (real / "role.md").write_text(ROLE_FILE_TEXT, encoding="utf-8")
    link = root / "linked_roles"
    os.symlink(real, link)
    role_path = link / "role.md"
    return role_path, str(Path(str(role_path)).expanduser())


class _Env:
    """One minimal real fixture: a git repo holding a scratchpad directory."""

    def __init__(self, root: Path, role_path: "Path | None" = None) -> None:
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
        if role_path is not None:
            self.cfg["role_template_path"] = str(role_path)

    def ctx(self, **extra) -> WorkflowContext:
        return WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={**self.cfg, **extra},
            question="bd147 feature request",
            session_id="bd147", persona="hal", framework=None, domain=None,
        )

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
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


class _InvokeSpy:
    """Module-attribute spy for `invoke_llm_subprocess`: records kwargs, returns a
    canned ok StepResult whose data merges `extra_data` like the real backend."""

    def __init__(self, raw: str = "canned response\n") -> None:
        self.raw = raw
        self.calls: list = []

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        data = {"raw_response": self.raw, "worker_written_paths": []}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0,
                          step_name=kwargs.get("step_name", "spy"))


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def attests(self) -> list:
        return [p for (t, p, _) in self.events if t == EVENT_TYPE]


class _RecordingAdapter:
    """Merges `extra_data` into the result exactly as the real backend does."""

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


def _through_chokepoint(step, ctx, built, step_name: str):
    """Run the REAL invoke step through the REAL chokepoint; only the backend
    adapter and the event log are fakes."""
    adapter = _RecordingAdapter()
    llm_subprocess.register_backend(
        "claude-subprocess", adapter, manifest_source="harness_tool_record",
        capabilities=frozenset(("manifest", "progress_since", "abort")), overwrite=True,
    )
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="bd147", step_name=step_name)
    result = step(ctx, built)
    return result, adapter, log


def _declared(call: dict) -> tuple:
    return tuple(call.get("injections") or ())


# ---------------------------------------------------------------------------
# S1 fixtures: decision doc
# ---------------------------------------------------------------------------

def _truncated_doc_text() -> str:
    """> 80 000 bytes and > 75 000 chars; head / middle / tail sentinels placed so
    the middle one lies strictly between text[:60000] and text[-15000:]."""
    text = (
        "HEAD-SENTINEL-147\n" + "x" * 61000
        + "\nMIDDLE-SENTINEL-147\n" + "y" * 20000
        + "\nTAIL-SENTINEL-147\n" + "z" * 100
    )
    assert len(text.encode("utf-8")) > 80_000 and len(text) > 75_000
    assert text.index("MIDDLE-SENTINEL-147") >= 60_000
    assert text.index("MIDDLE-SENTINEL-147") + len("MIDDLE-SENTINEL-147") <= len(text) - 15_000
    assert "TAIL-SENTINEL-147" in text[-15_000:] and "HEAD-SENTINEL-147" in text[:60_000]
    return text


def _write_doc(root: Path, text: str, bom: bool = False) -> Path:
    doc = root / "decision.md"
    doc.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    return doc


def _spec_built(env: _Env, doc: Path) -> StepResult:
    built = _mod("phase_45_spec")._build_spec_prompt(env.ctx(decision_doc=str(doc)), _prev())
    assert isinstance(built, StepResult) and built.status == "ok" and built.data.get("prompt"), (
        f"fixture precondition: spec writer must return an ok prompt, got "
        f"status={built.status!r} error_code={built.error_code!r}")
    return built


def _wrapper_whole(doc: Path, text: str) -> str:
    return f"## DECISION DOC (full file at {doc} — text inlined below)\n\n{text}\n"


def _wrapper_truncated(doc: Path, text: str) -> str:
    nbytes = len(text.encode("utf-8"))
    return (
        f"## DECISION DOC (full file at {doc} — text inlined below)\n\n"
        f"{text[:60000]}\n\n"
        f"... (truncated — {nbytes} bytes total; full file at {doc} for omitted middle) ...\n\n"
        f"{text[-15000:]}\n"
    )


# ---------------------------------------------------------------------------
# S2 fixtures: in-scope test files in the fix prompt
# ---------------------------------------------------------------------------

_SMALL_REL = "tests/test_a_small.py"
_BIG_REL = "tests/test_b_big.py"
_SMALL_TEXT = "def test_small():\n    assert 'SMALL-SENTINEL-147'\n"
_BIG_TEXT = "# BIG-HEAD-147\n" + "#" * 25000 + "\n# BIG-TAIL-SENTINEL-147\n"
_BIG_TRUNC = _BIG_TEXT[:20000]


def _s2_env(root: Path) -> _Env:
    env = _Env(root)
    ref = env.scratch / "integrity" / "pre-red-ref.txt"
    ref.parent.mkdir(parents=True)
    ref.write_text(env.base_sha, encoding="utf-8")
    excluded_prefix = _mod("phase_6_review")._build_scope_exclude_prefixes()[0]
    for rel, text in (
        (_SMALL_REL, _SMALL_TEXT),
        (_BIG_REL, _BIG_TEXT),
        ("src/helper_147.py", "NONTEST-SENTINEL-147 = 1\n"),
        ("tests/helper_not_a_test.py", "NOT-A-TEST-SENTINEL-147 = 1\n"),
        (f"{excluded_prefix}test_excluded.py", "EXCLUDED-SENTINEL-147 = 1\n"),
    ):
        target = env.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return env


def _fix_built(env: _Env) -> StepResult:
    review_doc = Path(env.path("review.md"))
    review_doc.write_text("# Review\n\n## Aggregated Findings\n\n", encoding="utf-8")
    built = _mod("phase_6_review")._build_fix_prompt(
        env.ctx(), _prev(spec_path=env.path("spec.md"), review_doc_path=str(review_doc),
                         verdict="CHANGES_REQUESTED"))
    assert isinstance(built, StepResult) and built.status == "ok" and built.data.get("prompt"), (
        f"fixture precondition: fix prompt must be ok, got {built.error_code!r} {built.error!r}")
    return built


def _s2_literal() -> str:
    """The pinned format of phase_6_review._inline_inscope_test_files."""
    fb_small = f"### {_SMALL_REL}\n```python\n{_SMALL_TEXT}\n```"
    fb_big = (
        f"### {_BIG_REL}\n```python\n{_BIG_TRUNC}"
        f"\n[TRUNCATED — file exceeds 20000 bytes; Read the full file at {_BIG_REL}]\n```"
    )
    return (
        "## CURRENT TEST FILE CONTENT (HEAD — authoritative)\n\n"
        "When a finding requires you to re-derive or re-check a test assertion, treat the\n"
        "content below as ground truth. Do NOT re-derive assertions from the spec ACs alone\n"
        "(codifies 012F2C02 RCA-3.1).\n\n"
        + fb_small + "\n" + fb_big
    )


_S2_EXPECTED = (
    InjectedBlock(source_id=_SMALL_REL, content=_SMALL_TEXT),
    InjectedBlock(source_id=_BIG_REL, content=_BIG_TRUNC),
)


# ---------------------------------------------------------------------------
# AC1 - S1 whole
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("with_role", [False, True], ids=["plain", "with_role_template"])
def test_ac1_decision_doc_whole_declared(with_role, tmp_path, monkeypatch) -> None:
    # Red under: declare the whole wrapper block instead of the body chunk (content mismatch).
    monkeypatch.chdir(tmp_path)
    text = "# Decision\n\nDISTINCT-SENTINEL-LINE-147\n\nbody line\n"
    doc = _write_doc(tmp_path, text, bom=True)  # BOM dropped by utf-8-sig: digest is over the inlined string
    role_path, role_source = _make_role_file(tmp_path / "roles") if with_role else (None, "")
    env = _Env(tmp_path / "run", role_path)
    built = _mod("phase_45_spec")._build_spec_prompt(env.ctx(decision_doc=str(doc)), _prev())
    assert built.status == "ok", f"fixture precondition: {built.error_code} {built.error}"
    p45 = _mod("phase_45_spec")
    spy = _InvokeSpy()
    monkeypatch.setattr(p45, "invoke_llm_subprocess", spy)
    p45._invoke_spec_llm(env.ctx(decision_doc=str(doc)), built)
    assert len(spy.calls) == 1
    expected = (InjectedBlock(source_id=str(doc), content=text),)
    if with_role:
        expected = (InjectedBlock(source_id=role_source, content=ROLE_CONTENT),) + expected
    assert _declared(spy.calls[0]) == expected, (
        f"spec dispatch must declare the decision-doc body (after the role block); got {_declared(spy.calls[0])!r}")


# ---------------------------------------------------------------------------
# AC1b - S1 record is bound to the FINAL prompt (after the standards prepend)
# ---------------------------------------------------------------------------

def test_ac1b_decision_doc_bound_to_final_prompt_after_standards_prepend(tmp_path, monkeypatch) -> None:
    # Red under: mutation "bind S1 before the standards prepend" (prompt_sha256 over the pre-prepend
    # prompt -> record ignored -> no declaration); and today (no record at all).
    monkeypatch.chdir(tmp_path)
    text = "# Decision\n\nBIND-SENTINEL-147\n"
    doc = _write_doc(tmp_path, text)
    env = _Env(tmp_path / "run")
    p45 = _mod("phase_45_spec")
    standards = "STANDARDS-BLOCK-147: follow the house rules.\n\n"
    # Dependency of the builder (host shim), not the unit under test.
    monkeypatch.setattr(p45, "get_standards_context", lambda *a, **kw: standards)
    built = _spec_built(env, doc)
    assert built.data["prompt"].startswith(standards), "fixture precondition: standards block was prepended"
    expected = (InjectedBlock(source_id=str(doc), content=text),)
    real_invoke = p45.invoke_llm_subprocess
    spy = _InvokeSpy()
    monkeypatch.setattr(p45, "invoke_llm_subprocess", spy)
    p45._invoke_spec_llm(env.ctx(decision_doc=str(doc)), built)
    assert len(spy.calls) == 1
    assert _declared(spy.calls[0]) == expected, (
        f"S1 must be declared even when a standards block is prepended; got {_declared(spy.calls[0])!r}")
    monkeypatch.setattr(p45, "invoke_llm_subprocess", real_invoke)  # real chokepoint below
    result, adapter, log = _through_chokepoint(p45._invoke_spec_llm, env.ctx(decision_doc=str(doc)), built,
                                               "invoke_spec_llm")
    assert result.status == "ok", f"dispatch refused: {result.error_code!r} {result.error!r}"
    assert len(adapter.calls) == 1
    assert log.attests()[0]["injections"] == [{"source_id": str(doc), "sha256": hash_text(text)}]


# ---------------------------------------------------------------------------
# AC2 - S1 truncated and the F2 multi-byte case
# ---------------------------------------------------------------------------

def test_ac2_decision_doc_truncated_declares_head_then_tail(tmp_path, monkeypatch) -> None:
    # Red under: one block of head+marker+tail; tail before head; marker or middle inside a block.
    monkeypatch.chdir(tmp_path)
    text = _truncated_doc_text()
    doc = _write_doc(tmp_path, text)
    env = _Env(tmp_path / "run")
    p45 = _mod("phase_45_spec")
    built = _spec_built(env, doc)
    spy = _InvokeSpy()
    monkeypatch.setattr(p45, "invoke_llm_subprocess", spy)
    p45._invoke_spec_llm(env.ctx(decision_doc=str(doc)), built)
    got = _declared(spy.calls[0])
    assert got == (
        InjectedBlock(source_id=str(doc), content=text[:60000]),
        InjectedBlock(source_id=str(doc), content=text[-15000:]),
    ), f"truncated doc must declare head then tail under one source_id; got {len(got)} block(s)"
    for block in got:
        assert "... (truncated —" not in block.content
        assert "MIDDLE-SENTINEL-147" not in block.content


def test_ac2_multibyte_over_byte_cap_declares_one_whole_block(tmp_path, monkeypatch) -> None:
    # Red under: declaring head/tail slices for the F2 case (duplicated content), or no declaration.
    monkeypatch.chdir(tmp_path)
    text = chr(0x436) * 41000 + "\n"  # 82 001 bytes, 41 001 chars: over the byte cap, under head+tail
    assert len(text.encode("utf-8")) > 80_000 and len(text) <= 75_000
    doc = _write_doc(tmp_path, text)
    env = _Env(tmp_path / "run")
    p45 = _mod("phase_45_spec")
    built = _spec_built(env, doc)
    spy = _InvokeSpy()
    monkeypatch.setattr(p45, "invoke_llm_subprocess", spy)
    p45._invoke_spec_llm(env.ctx(decision_doc=str(doc)), built)
    assert _declared(spy.calls[0]) == (InjectedBlock(source_id=str(doc), content=text),)


# ---------------------------------------------------------------------------
# AC3 / AC4 - S2
# ---------------------------------------------------------------------------

def test_ac3_inscope_test_files_declared_in_surviving_order(tmp_path, monkeypatch) -> None:
    # Red under: marker-suffixed content declared; source_id = str(full_path); a non-test or excluded file declared.
    env = _s2_env(tmp_path)
    p6 = _mod("phase_6_review")
    built = _fix_built(env)
    spy = _InvokeSpy()
    monkeypatch.setattr(p6, "invoke_llm_subprocess", spy)
    p6._invoke_fix_llm(env.ctx(), built)
    assert len(spy.calls) == 1
    got = _declared(spy.calls[0])
    assert got == _S2_EXPECTED, f"fix dispatch must declare the two in-scope test files in order; got {got!r}"
    for block in got:
        assert "[TRUNCATED" not in block.content
        assert "BIG-TAIL-SENTINEL-147" not in block.content
    assert {b.source_id for b in got} == {_SMALL_REL, _BIG_REL}  # no helper_147, no excluded, no non-test


def test_ac4_fix_retry_declares_same_inscope_blocks(tmp_path, monkeypatch) -> None:
    # Red under: first dispatch's extra_data does not forward `injected_blocks` (retry declares only the role/none).
    env = _s2_env(tmp_path)
    p6 = _mod("phase_6_review")
    ctx = env.ctx()
    built = _fix_built(env)
    calls: list = []

    def spy(**kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            data = {"raw_response": "did some work but forgot the completion marker",
                    "worker_written_paths": [], **(kwargs.get("extra_data") or {})}
            return StepResult(status="ok", data=data, duration_ms=0, step_name=kwargs["step_name"])
        return StepResult(status="error", data=None, duration_ms=0, step_name=kwargs["step_name"],
                          error="spy retry stop", error_code="E_SPY_STOP")

    monkeypatch.setattr(p6, "invoke_llm_subprocess", spy)
    first = p6._invoke_fix_llm(ctx, built)
    assert first.status == "ok"
    p6._write_fix_artifact(ctx, first)
    assert len(calls) == 2, f"fixture precondition: first dispatch + retry expected, got {len(calls)}"
    assert _declared(calls[0]) == _S2_EXPECTED, f"first dispatch declares {_declared(calls[0])!r}"
    assert _declared(calls[1]) == _declared(calls[0]), (
        f"the retry must declare exactly what its first attempt did; got {_declared(calls[1])!r}")


# ---------------------------------------------------------------------------
# AC5 - S3 directed repair
# ---------------------------------------------------------------------------

def _run_repair(tmp_path, monkeypatch, artifact: Path):
    dr = importlib.import_module("bytedigger_engine.lib.directed_repair")
    calls: list = []

    def spy(**kwargs):
        calls.append(dict(kwargs))
        return StepResult(status="error", data=None, duration_ms=0, step_name=kwargs.get("step_name", "x"),
                          error="spy stop", error_code="E_SPY_STOP")

    monkeypatch.setattr(dr, "invoke_llm_subprocess", spy)
    ctx = _Env(tmp_path / "run").ctx()
    dr.attempt_directed_repair(
        gate="spec_lint", artifact_path=str(artifact),
        findings=[{"path": str(artifact), "line": 1, "rule": "R1", "evidence": "bad line"}],
        rerun_gate=lambda: StepResult(status="error", data=None, duration_ms=0, step_name="gate",
                                      error="still failing", error_code="E_GATE"),
        cheap_model="haiku", repair_step_name="repair_147", max_attempts=1, ctx=ctx,
    )
    assert len(calls) == 1, f"fixture precondition: one repair dispatch expected, got {len(calls)}"
    return calls[0]


def test_ac5_directed_repair_declares_artifact(tmp_path, monkeypatch) -> None:
    # Red under: no `injections=` at the dispatch, or a different source_id / digest string.
    artifact = tmp_path / "artifact_147.md"
    text = "# Artifact\nARTIFACT-SENTINEL-147\n"
    artifact.write_text(text, encoding="utf-8")
    call = _run_repair(tmp_path, monkeypatch, artifact)
    assert _declared(call) == (InjectedBlock(source_id=str(artifact), content=text),)
    assert text in call["prompt"], "the declared artifact must occur in the dispatched prompt"


def test_ac5_unreadable_artifact_declares_nothing(tmp_path, monkeypatch) -> None:
    # Red under: dropping the `if artifact_text` guard (declares an empty-content block). Green today.
    call = _run_repair(tmp_path, monkeypatch, tmp_path / "does_not_exist_147.md")
    assert _declared(call) == ()


# ---------------------------------------------------------------------------
# AC6 - byte identity + the real chokepoint, S1 whole / S1 truncated / S2
# ---------------------------------------------------------------------------

def _assert_attested(result, adapter, log, built, literal: str, expected_injections: list) -> None:
    # AC6 is CONTAINMENT: `literal in sent`, never `sent == literal`.
    assert result.status == "ok", (
        f"dispatch must succeed (a refusal is status error / 0 adapter calls): "
        f"status={result.status!r} error_code={result.error_code!r} error={result.error!r}")
    assert len(adapter.calls) == 1, f"adapter must be called exactly once, got {len(adapter.calls)}"
    sent = adapter.calls[0]["prompt"]
    assert sent == built.data["prompt"], "prompt bytes must be unchanged by the dispatch"
    assert literal in sent, "the dispatched prompt must contain the pinned literal block, byte for byte"
    attests = log.attests()
    assert len(attests) == 1, f"expected one {EVENT_TYPE!r} event, got {len(attests)}"
    assert attests[0]["injections"] == expected_injections, (
        f"attested injections mismatch; got {attests[0]['injections']!r}")


def test_ac6_decision_doc_whole_bytes_and_attestation(tmp_path, monkeypatch) -> None:
    # Containment (spec AC6): the dispatched prompt CONTAINS the literal block composed here from the
    # pinned formats; the rest of the prompt is engine text other suites pin. Not an equality check.
    # Red under: any prompt-byte change at the site (literal), or no declaration (attested injections == []).
    monkeypatch.chdir(tmp_path)
    text = "# Decision\n\nWHOLE-SENTINEL-147\n\nbody line\n"
    doc = _write_doc(tmp_path, text)
    env = _Env(tmp_path / "run")
    built = _spec_built(env, doc)
    result, adapter, log = _through_chokepoint(
        _mod("phase_45_spec")._invoke_spec_llm, env.ctx(decision_doc=str(doc)), built, "invoke_spec_llm")
    _assert_attested(result, adapter, log, built, _wrapper_whole(doc, text),
                     [{"source_id": str(doc), "sha256": hash_text(text)}])


def test_ac6_decision_doc_truncated_bytes_and_attestation(tmp_path, monkeypatch) -> None:
    # Red under: marker/wrapper byte change, head+tail merged into one block, or no declaration.
    monkeypatch.chdir(tmp_path)
    text = _truncated_doc_text()
    doc = _write_doc(tmp_path, text)
    env = _Env(tmp_path / "run")
    built = _spec_built(env, doc)
    result, adapter, log = _through_chokepoint(
        _mod("phase_45_spec")._invoke_spec_llm, env.ctx(decision_doc=str(doc)), built, "invoke_spec_llm")
    _assert_attested(result, adapter, log, built, _wrapper_truncated(doc, text), [
        {"source_id": str(doc), "sha256": hash_text(text[:60000])},
        {"source_id": str(doc), "sha256": hash_text(text[-15000:])},
    ])


def test_ac6_inscope_test_files_bytes_and_attestation(tmp_path) -> None:
    # Red under: header/instruction/fence byte change, marker-suffixed digest, or no declaration.
    env = _s2_env(tmp_path)
    built = _fix_built(env)
    result, adapter, log = _through_chokepoint(
        _mod("phase_6_review")._invoke_fix_llm, env.ctx(), built, "invoke_fix_llm")
    _assert_attested(result, adapter, log, built, _s2_literal(), [
        {"source_id": _SMALL_REL, "sha256": hash_text(_SMALL_TEXT)},
        {"source_id": _BIG_REL, "sha256": hash_text(_BIG_TEXT[:20000])},
    ])


# ---------------------------------------------------------------------------
# AC7 - stale-forward guard
# ---------------------------------------------------------------------------

_ROLE_RECORD = {"source_id": "/r/role.md", "content": "ROLE\n\n"}
_ROLE_BLOCK = InjectedBlock(source_id="/r/role.md", content="ROLE\n\n")
_REC_BLOCKS = [{"source_id": "a.py", "content": "AAA"}, {"source_id": "b.py", "content": "BBB"}]
_REC_EXPECTED = (InjectedBlock(source_id="a.py", content="AAA"), InjectedBlock(source_id="b.py", content="BBB"))


def test_ac7_stale_record_is_not_declared() -> None:
    # Red under: omitting the prompt binding in `_declared_injections` (stale record declared) or never
    # reading the record (positive control fails).
    declared = _sym("phase_workflows_common", "_declared_injections")
    bound_prompt = "P1 AAA BBB"
    record = {"prompt_sha256": hash_text(bound_prompt), "blocks": _REC_BLOCKS}
    for role_rec, role_only in ((_ROLE_RECORD, (_ROLE_BLOCK,)), (None, ())):
        stale = {"role_template": role_rec, "prompt": "P2 a different prompt", "injected_blocks": record}
        assert declared(stale) == role_only, f"stale record must be ignored; got {declared(stale)!r}"
        assert "injected_blocks" in stale, "a stale record is ignored, never deleted"
        control = {"role_template": role_rec, "prompt": bound_prompt, "injected_blocks": record}
        assert declared(control) == role_only + _REC_EXPECTED, (
            f"positive control: the bound record's blocks follow the role block in order; got {declared(control)!r}")
    assert declared({"injected_blocks": record}) == (), "no prompt at all cannot match a record bound to a prompt"


def test_ac7_forwarded_stale_record_dispatches_ok_through_chokepoint(tmp_path) -> None:
    # Red under: omitting the prompt binding (later builder declares test files its prompt lacks ->
    # E_INJECT_UNATTRIBUTED, 0 adapter calls). Precondition fails today: no record is produced.
    env = _s2_env(tmp_path)
    p6 = _mod("phase_6_review")
    fix_built = _fix_built(env)
    record = fix_built.data.get("injected_blocks")
    assert isinstance(record, dict) and len(record.get("blocks") or ()) == 2, (
        f"precondition: the fix builder must produce an injected_blocks record; got {record!r}")
    # A real later builder that spreads prev.data into its own data and replaces the prompt.
    later = p6._build_decorr_prompt(env.ctx(complexity="FEATURE"), fix_built)
    assert later.status == "ok" and not later.data.get("decorr_skipped"), "fixture precondition: decorr builder"
    assert later.data.get("injected_blocks") == record, "fixture precondition: the stale record is forwarded"
    assert later.data["prompt"] != fix_built.data["prompt"]
    result, adapter, log = _through_chokepoint(p6._invoke_decorr_llm, env.ctx(), later, "invoke_decorr_llm")
    assert result.status == "ok" and "decorr_error" not in result.data, (
        f"the later dispatch must not be refused: {result.data.get('decorr_error')!r}")
    assert len(adapter.calls) == 1
    assert log.attests()[0]["injections"] == [], "the later prompt contains no test files: nothing to declare"


def test_ac7_injected_blocks_record_helper() -> None:
    # Red under: helper missing, record not bound to hash_text(prompt), or an empty list not mapped to None.
    record = _sym("phase_workflows_common", "_injected_blocks_record")
    assert record("any prompt", []) is None
    got = record("P1 AAA BBB", _REC_BLOCKS)
    assert got == {"prompt_sha256": hash_text("P1 AAA BBB"), "blocks": _REC_BLOCKS}


# ---------------------------------------------------------------------------
# AC8 - malformed records are inert
# ---------------------------------------------------------------------------

def test_ac8_malformed_records_are_inert() -> None:
    # Red under: indexing the record without type checks (raises), or never reading a valid record
    # (the well-formed control fails).
    declared = _sym("phase_workflows_common", "_declared_injections")
    prompt = "P1 AAA BBB"
    sha = hash_text(prompt)
    good = {"role_template": _ROLE_RECORD, "prompt": prompt,
            "injected_blocks": {"prompt_sha256": sha, "blocks": _REC_BLOCKS}}
    assert declared(good) == (_ROLE_BLOCK,) + _REC_EXPECTED, "control: a well-formed record is honoured"

    def with_record(record):
        return {"role_template": _ROLE_RECORD, "prompt": prompt, "injected_blocks": record}

    cases = [
        with_record(None),
        with_record([]),
        with_record(["a.py"]),
        with_record("garbage"),
        with_record({}),
        with_record({"prompt_sha256": sha}),
        with_record({"blocks": _REC_BLOCKS}),
        with_record({"prompt_sha256": sha, "blocks": None}),
        with_record({"prompt_sha256": sha, "blocks": "not a list"}),
        with_record({"prompt_sha256": sha, "blocks": {"source_id": "a.py", "content": "AAA"}}),
    ]
    for data in cases:
        assert declared(data) == (_ROLE_BLOCK,), f"malformed record must yield the role-only result: {data!r}"
    for data in (None, "injected_blocks", ["injected_blocks"], 7):
        assert declared(data) == (), f"non-dict data must yield (): {data!r}"
    assert declared({"prompt": prompt, "injected_blocks": {"prompt_sha256": sha, "blocks": "x"}}) == ()
    # non-str data["prompt"]: a hash mismatch -> role-only, never a raise (spec 3.1)
    rec = {"prompt_sha256": sha, "blocks": _REC_BLOCKS}
    for bad_prompt in (["P1 AAA BBB"], 123, {"p": prompt}, b"P1 AAA BBB"):
        data = {"role_template": _ROLE_RECORD, "prompt": bad_prompt, "injected_blocks": rec}
        assert declared(data) == (_ROLE_BLOCK,), f"non-str prompt must yield the role-only result: {bad_prompt!r}"
        data = {"prompt": bad_prompt, "injected_blocks": {"prompt_sha256": hash_text(""), "blocks": _REC_BLOCKS}}
        assert declared(data) == (), f"non-str prompt must never bind a record: {bad_prompt!r}"


def _bound(element) -> dict:
    prompt = "P1 AAA BBB"
    return {"role_template": _ROLE_RECORD, "prompt": prompt,
            "injected_blocks": {"prompt_sha256": hash_text(prompt), "blocks": [_REC_BLOCKS[0], element]}}


def test_ac8_malformed_elements_fail_closed_helper() -> None:
    # Red under: dropping malformed elements (tuple lacks the unattributed block), indexing without
    # type checks (raises), and today (records ignored entirely: the good element is not declared).
    declared = _sym("phase_workflows_common", "_declared_injections")
    good = InjectedBlock(source_id="a.py", content="AAA")
    got = declared(_bound("not-a-dict"))
    assert got == (_ROLE_BLOCK, good, InjectedBlock(None, None)), f"non-dict element -> InjectedBlock(None, None); got {got!r}"
    got = declared(_bound({"content": "BBB"}))
    assert got == (_ROLE_BLOCK, good, InjectedBlock(None, "BBB")), f"missing source_id -> None source_id; got {got!r}"


@pytest.mark.parametrize("element", ["not-a-dict", {"content": "ELEMENT-SENTINEL-147"}], ids=["non_dict", "no_source_id"])
def test_ac8_malformed_elements_refused_through_chokepoint(element, tmp_path, monkeypatch) -> None:
    # Red under: dropping malformed elements (dispatch would go ok with 1 adapter call), and today
    # (records ignored -> dispatch ok). The unit under test is the real _declared_injections + chokepoint.
    monkeypatch.chdir(tmp_path)
    text = "# Decision\n\nELEMENT-SENTINEL-147\n"
    doc = _write_doc(tmp_path, text)
    env = _Env(tmp_path / "run")
    built = _spec_built(env, doc)
    prompt = built.data["prompt"]
    tampered = StepResult(status="ok", duration_ms=0, step_name=built.step_name, data={
        **built.data,
        "injected_blocks": {"prompt_sha256": hash_text(prompt), "blocks": [element]},
    })
    result, adapter, log = _through_chokepoint(
        _mod("phase_45_spec")._invoke_spec_llm, env.ctx(decision_doc=str(doc)), tampered, "invoke_spec_llm")
    assert result.status == "error" and result.error_code == "E_INJECT_UNATTRIBUTED", (
        f"unattributed element must be refused: status={result.status!r} code={result.error_code!r}")
    assert len(adapter.calls) == 0, f"adapter must not be called, got {len(adapter.calls)}"


# ---------------------------------------------------------------------------
# AC10 - empty chunks are not declared
# ---------------------------------------------------------------------------

def test_ac10_bom_only_decision_doc_declares_no_block(tmp_path, monkeypatch) -> None:
    # _resolve_decision_doc_path accepts a 3-byte BOM-only file (size > 0), so text == "" is reachable.
    # Red under: declaring a "" chunk; today the helper is missing.
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "decision.md"
    doc.write_bytes(BOM)
    inline = _sym("phase_45_spec", "_decision_doc_inline")
    wrapper, records = inline({"decision_doc": str(doc)})
    assert wrapper == _wrapper_whole(doc, ""), "fixture precondition: the (empty-body) wrapper is still emitted"
    assert not records, f"an empty chunk must not be declared; got {records!r}"
    env = _Env(tmp_path / "run")
    p45 = _mod("phase_45_spec")
    built = _spec_built(env, doc)
    spy = _InvokeSpy()
    monkeypatch.setattr(p45, "invoke_llm_subprocess", spy)
    p45._invoke_spec_llm(env.ctx(decision_doc=str(doc)), built)
    assert _declared(spy.calls[0]) == ()
    assert not built.data.get("injected_blocks"), "no record when there are no chunks (None)"


def test_ac10_empty_test_file_not_declared_sibling_still_is(tmp_path, monkeypatch) -> None:
    # Red under: declaring a "" chunk for the empty file; today the non-empty siblings are not declared either.
    env = _s2_env(tmp_path)
    (env.repo / "tests" / "test_c_empty.py").write_text("", encoding="utf-8")
    p6 = _mod("phase_6_review")
    built = _fix_built(env)
    spy = _InvokeSpy()
    monkeypatch.setattr(p6, "invoke_llm_subprocess", spy)
    p6._invoke_fix_llm(env.ctx(), built)
    got = _declared(spy.calls[0])
    assert got == _S2_EXPECTED, f"non-empty siblings declared, empty file not; got {got!r}"
    assert all(b.content != "" for b in got)


# ---------------------------------------------------------------------------
# AC9 - legacy views
# ---------------------------------------------------------------------------

def test_ac9_decision_doc_legacy_view_equals_inline_head(tmp_path, monkeypatch) -> None:
    # Red under: helper missing, or the legacy view diverging from the new helper's text.
    monkeypatch.chdir(tmp_path)
    inline = _sym("phase_45_spec", "_decision_doc_inline")
    legacy = _sym("phase_45_spec", "_read_decision_doc_block")
    whole_text = "# Decision\n\nLEGACY-SENTINEL-147\n"
    trunc_text = _truncated_doc_text()
    whole = _write_doc(tmp_path, whole_text)
    trunc = tmp_path / "trunc.md"
    trunc.write_bytes(trunc_text.encode("utf-8"))
    multi = tmp_path / "multi.md"
    multi_text = chr(0x436) * 41000 + "\n"
    multi.write_bytes(multi_text.encode("utf-8"))
    for cfg, literal in (
        ({"decision_doc": str(whole)}, _wrapper_whole(whole, whole_text)),
        ({"decision_doc": str(trunc)}, _wrapper_truncated(trunc, trunc_text)),
        ({"decision_doc": str(multi)}, _wrapper_whole(multi, multi_text)),
    ):
        text, records = inline(cfg)
        assert text == literal == legacy(cfg), f"views diverge for {cfg!r}"
        assert all(set(r) == {"source_id", "content"} for r in records)
    for cfg in (None, {}, {"decision_doc": str(tmp_path / "missing.md")}):
        text, records = inline(cfg)
        assert text == "" == legacy(cfg) and not records, f"unset/missing doc must be inert: {cfg!r}"


def test_ac9_inscope_legacy_view_equals_inline_first_three(tmp_path) -> None:
    # Red under: helper missing, or the legacy view not being [:3] of the new helper.
    inline = _sym("phase_6_review", "_inscope_test_files_inline")
    legacy = _sym("phase_6_review", "_inline_inscope_test_files")
    env = _s2_env(tmp_path)
    ctx = env.ctx()
    out = inline(ctx, env.scratch)
    assert len(out) == 4, f"_inscope_test_files_inline must return a 4-tuple, got {len(out)}"
    assert tuple(legacy(ctx, env.scratch)) == tuple(out[:3])
    assert out[0] == _s2_literal(), "the block text is the pinned literal, not derived from the helper"
    assert out[1] == 2
    assert [(r["source_id"], r["content"]) for r in out[3]] == [
        (_SMALL_REL, _SMALL_TEXT), (_BIG_REL, _BIG_TRUNC)]
    # no pre-red-ref: the helper is inert and still returns a 4-tuple
    bare = _Env(tmp_path / "bare")
    empty = inline(bare.ctx(), bare.scratch)
    assert tuple(empty[:3]) == ("", 0, 0) and not empty[3]
