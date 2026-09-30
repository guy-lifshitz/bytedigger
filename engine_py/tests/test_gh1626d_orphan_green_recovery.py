"""RED tests for hal#1626 part D (ROUND 4 — AC1''/AC13'', AC28, AC29, AC8
through the registry) — a completed GREEN that never got committed is
recoverable, and every OTHER dirty tree stays terminal.

Frozen spec (amended):
SHARED/memory/Decisions/2026-08-12_gh1626D_orphan_green_recovery_spec.md
  * "Gate round 1 — REJECTED, six MAJOR"
  * "Design v2 (replaces Design 1 and 3)"
  * "Acceptance criteria, round 2 (supersede AC1 and AC7)"
  * "Gate round 2" + "Acceptance criteria, round 3" — AC13' replaces AC13
    (a name in `step_finished` cannot tell a working recovery from an inert
    one), AC25 adds the declared-error-code pin (§1n).
  * "Gate round 3" + "Acceptance criteria, round 4":
    - MAJOR-D: the sentinel is keyed to `invoke_green_llm` (index 3) while
      `write_green_artifact` (index 5) is what runs `_parse_green_status` and
      raises `E_GREEN_BLOCKED` / `E_GREEN_NO_MARKER`. The gap between them IS
      the crash window, so a BLOCKED or truncated GREEN carries a perfectly
      valid ownership tuple and matching digests. The route target therefore
      moves from index 6 to **`write_green_artifact`**, whose marker parsing
      gates every recovery; the four verification steps still run after it.
      (AC1''/AC13'' retarget; AC28 makes the adoption hole bite.)
    - MAJOR-C: `ERROR_CODES.md` is GENERATED (`test_error_codes.py::test_ac4`
      asserts byte-equality with `render_markdown()`), so the routing code is
      declared in `error_codes.ERROR_CODES` and the resume/`_retry_nonce`
      explanation lives in ITS docstring — asserted through the registry
      (AC8), with regeneration proven by byte-equality (AC29).

UUT (never mocked, no MagicMock anywhere in this file):
  * `phase_5_implement._verify_red_fails_mechanically` — raise site 1 of
    `E_RED_WORKTREE_DIRTY` (`_verify_red_dirty_tree_guard`, :2974-3006).
  * `phase_5_implement._build_validation_prompt` — raise site 2 (:6835-6852),
    AC20 (both halves: recovery AND terminal).
  * `engine.WorkflowEngine._execute_steps` — the REAL re-entry mechanism
    (:405 loop, :400 `prev = initial_data` is a plain dict, :548/:556-560 the
    routing contract). AC13/AC14 drive it for real.

Design v2 + round-4 correction, restated so the assertions below are readable:
  1. Recovery re-enters at `write_green_artifact` — NOT `commit_green_code`,
     and NOT past the marker parse — so the GREEN's own completion marker is
     re-judged first and lint / security-lint / test-run / typecheck all
     execute after it; the commit is reached the normal way or not at all.
  2. The re-entry contract is `status="error"`, `recoverable=True`, an INT
     `retry_from_step`; the route target must be dict-tolerant.
  3. Ownership is the FULL sentinel key tuple (workflow, step, cycle, run_id,
     ctx_hash) — never run_id alone.
  4. The subset test binds CONTENT (per-path sha256 recorded at GREEN) and the
     porcelain XY — names alone prove nothing.

Fixture discipline:
  * REAL git repos, REAL commits, REAL dirty trees (§1i: the contested state is
    pre-staged deterministically; nothing races, nothing sleeps).
  * Resume artifacts are written with the PRODUCTION writers
    (`lib.step_sentinel.write_step_sentinel`, `_persist_green_complete_resume`)
    so the sentinel naming scheme (`lib/resume_keying.py:13`) is never guessed
    and the digest format (Design v2 §4) is never guessed either: the GREEN
    record is persisted by production code WHILE the tree holds the GREEN
    content, and the post-GREEN mutation happens strictly AFTER that call.
  * Every ctx sets `task_description` AND `decision_doc`, so
    `step_sentinel.compute_ctx_hash` is NOT None and every sentinel this file
    writes really carries an `_h<hash12>` segment (MAJOR-3b).

§1q: no not-yet-existing symbol is imported at module level; the new event name
only appears inside assert-time comparisons, so this file collects cleanly
today and fails at ASSERT time.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bytedigger_engine import error_codes  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext, WorkflowDefinition  # noqa: E402
from bytedigger_engine.engine import _RETRY_CONTROL_KEYS, WorkflowEngine  # noqa: E402
from bytedigger_engine.error_codes import ERROR_CODES  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.lib import step_sentinel as step_sentinel  # noqa: E402


ENGINE_ROOT = Path(__file__).resolve().parents[1]

GREEN_STEP = "invoke_green_llm"
PHASE_5_WORKFLOW_NAME = "phase_5_implement"
RECOVERY_EVENT = "orphan_green_recovery_routed"
# Round 4 / MAJOR-D: the target is the marker-parsing step, not the first
# verification gate. `write_green_artifact` (:7898) is the ONLY place
# `_parse_green_status` runs and the only source of E_GREEN_BLOCKED /
# E_GREEN_NO_MARKER; routing past it adopts a BLOCKED or truncated GREEN.
ROUTE_TARGET = "write_green_artifact"
LINT_STEP = "verify_green_lint_rules"
GREEN_VERIFY_STEPS = (
    "verify_green_lint_rules",
    "verify_security_lint",
    "verify_green_passing",
    "verify_green_typecheck",
)
GREEN_STATUS_ERROR_CODES = ("E_GREEN_BLOCKED", "E_GREEN_NO_MARKER")

GREEN_BODY = "def impl():\n    return 42\n"
POST_GREEN_EDIT_BODY = "def impl():\n    return 43  # edited AFTER the GREEN record\n"

# The fixture's RED test: genuinely failing at the RED commit (src/module.py does
# not exist yet) and genuinely passing once the orphaned GREEN is in the tree —
# so `verify_green_passing` on the recovery path exercises the real thing instead
# of dying on a permanently-red file. Imports nothing but the stdlib.
RED_TEST_BODY = (
    "import pathlib\n"
    "\n"
    "\n"
    "def test_x():\n"
    "    assert (pathlib.Path(__file__).resolve().parents[1]\n"
    "            / 'src' / 'module.py').is_file()\n"
)

# Build-critical external binaries the green gates shell out to (semgrep, the
# test runner, mypy). Their ABSENCE is an environment fact, not a verdict on the
# recovery, so AC13' leg (c) — which needs the whole chain to reach the commit —
# reports it loudly instead of failing the strand.
TOOLING_ABSENT_CODES = (
    "E_GREEN_LINT_SEMGREP_MISSING",
    "E_GREEN_TEST_RUNNER_MISSING",
    "E_GREEN_TYPECHECK_MYPY_MISSING",
)


# ═════════════════════════════════════════════════════════════════════════
# real-git harness — same idiom as tests/test_green_commit_nonempty_E7C4A1B9.py
# and tests/test_phase_5_9EDB7588_commit_idempotency.py (local copies; no
# existing test file is modified).
# ═════════════════════════════════════════════════════════════════════════


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str) -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return _head_sha(repo)


def _write_file(repo: Path, relpath: str, body: str) -> Path:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout.strip()


def _porcelain(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout


def _porcelain_xy(repo: Path, relpath: str) -> "str | None":
    """The verbatim XY column `git status --porcelain` reports for *relpath*."""
    for line in _porcelain(repo).splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path == relpath:
            return line[:2]
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_spec(scratchpad: Path, files: list) -> str:
    lines = ["# fixture spec (gh1626 D)\n", "\n## Files\n"]
    for f in files:
        lines.append(f"- `{f}`\n")
    p = scratchpad / "specs" / "build-spec.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)


def _make_ctx(scratchpad: Path, repo: Path, spec_path: str, org_extra: "dict | None" = None) -> WorkflowContext:
    """Explicit git_cwd — non-ambient (GH1220), so the ambient-refusal guard
    never fires here.

    `task_description` + `decision_doc` are set deliberately: without at least
    one of them `step_sentinel.compute_ctx_hash` returns None (:95) and NO
    `_h<hash>` sentinel is ever produced — which made the whole ctx-hash
    ownership class invisible in round 1 (MAJOR-3b).
    """
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
            "task_description": "gh1626D orphan GREEN recovery fixture",
            "decision_doc": spec_path,
            **(org_extra or {}),
        },
        question="q", session_id="test-1626d", persona="hal",
        framework=None, domain=None,
    )


class _Events:
    """Collect telemetry from BOTH seams: the `_emit_safe` module attribute
    (phase_5's own emitter) and the real EventLog jsonl the run context points
    at — so a GREEN that emits from `lib/dirty_tree_guard.py` instead of
    phase_5 is still observed."""

    def __init__(self, log_path: Path) -> None:
        self._captured: list = []
        self._log_path = log_path

    def record(self, event_type, payload=None, **kw):  # noqa: ANN001, ARG002
        self._captured.append({"type": str(event_type), "payload": dict(payload or {})})
        return None

    def all(self) -> list:
        events = list(self._captured)
        try:
            for line in self._log_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                events.append({
                    "type": str(obj.get("event_type") or ""),
                    "payload": dict(obj.get("payload") or {}),
                })
        except (OSError, ValueError):
            pass
        return events

    def types(self) -> list:
        return [e["type"] for e in self.all()]

    def of(self, event_type: str) -> list:
        """Events of *event_type*, INCLUDING the GH700 shadow-wrapped form
        (`shadowed_event` in the payload) so a non-authoritative emit is never
        silently invisible to an assertion."""
        out = []
        for e in self.all():
            if e["type"] == event_type:
                out.append(e)
            elif event_type in str(e["payload"].get("shadowed_event") or ""):
                out.append(e)
        return out


class _Stage:
    def __init__(self, repo, red_sha, ctx, prev, scratchpad, events, run_id, spec_path, head_before):
        self.repo = repo
        self.red_sha = red_sha
        self.ctx = ctx
        self.prev = prev
        self.scratchpad = scratchpad
        self.events = events
        self.run_id = run_id
        self.spec_path = spec_path
        self.head_before = head_before


def _green_sentinel_payload(
    red_sha: str,
    manifest: list,
    spec_path: str,
    cycle: int,
    scratchpad: Path,
    repo: Path,
    raw_response: str = "GREEN COMPLETE\n\n(fixture: gh1626D orphan GREEN)",
) -> dict:
    """Mirror the shape `_invoke_green_llm` returns (`data={**prev.data, ...}`
    over `_build_green_prompt`'s keys, :7783-7803 / :7827-7834), so whichever
    key the recovery forwards to `write_green_artifact` is present — that step
    reads `raw_response`, `log_path`, `spec_path`, `red_log_path` and
    `validation_doc_path` by subscript (:7906-7975).

    `raw_response` is a parameter because it is the ONE field MAJOR-D turns on:
    the ownership tuple and the digests are identical whether the GREEN said
    COMPLETE or BLOCKED.
    """
    return {
        "raw_response": raw_response,
        "prompt": "(fixture GREEN prompt)",
        "log_path": str(scratchpad / "tests" / "build-green-output.log"),
        "green_prompt_path": str(scratchpad / "tests" / "build-green-prompt.log"),
        "red_log_path": str(repo / "tests" / "build-red-output.log"),
        "validation_doc_path": str(scratchpad / "reviews" / "validation.md"),
        "red_test_paths": ["tests/test_x.py"],
        "cycle_count": cycle,
        "verdict": "PASS",
        "tokens_out": 0,
        "green_complete_resume": True,
        "green_resume_paths": list(manifest),
        "worker_written_paths": list(manifest),
        "manifest_source": "harness_tool_record",
        "red_commit_sha": red_sha,
        "spec_path": spec_path,
        "cycle": cycle,
    }


def _persist_green_record(scratchpad, red_sha: str, paths: list, git_cwd: str, ctx) -> None:
    """Call the PRODUCTION marker writer `_persist_green_complete_resume`
    (:1795) without guessing its post-GREEN signature.

    Design v2 §4 requires that writer to record a per-path sha256; doing so may
    require it to learn the repo (a `git_cwd`/`ctx` parameter it does not have
    today). This shim supplies any such parameter BY NAME if the GREEN adds it,
    so the fixture stages the record with production code either way — and
    fails loudly, not silently, if the GREEN grows a parameter this harness
    cannot fill.
    """
    fn = p5._persist_green_complete_resume
    params = list(inspect.signature(fn).parameters.items())
    supply = {
        "git_cwd": git_cwd, "repo": git_cwd, "cwd": git_cwd, "repo_root": git_cwd,
        "ctx": ctx, "context": ctx,
    }
    kwargs = {}
    for name, param in params[3:]:
        if name in supply:
            kwargs[name] = supply[name]
        elif param.default is inspect.Parameter.empty:
            raise AssertionError(
                f"fixture cannot stage the GREEN record: "
                f"`_persist_green_complete_resume` grew a required parameter "
                f"{name!r} this harness does not know how to supply "
                f"(signature={inspect.signature(fn)})"
            )
    fn(scratchpad, red_sha, list(paths), **kwargs)


def _stage(
    tmp_path: Path,
    name: str,
    monkeypatch,
    *,
    manifest: list,
    dirty_paths: list,
    run_id: str = "run1626dcafe",
    sentinel_run_id: "str | None" = "same",
    sentinel_cycle: "int | None" = None,
    current_cycle: int = 1,
    ctx_org_extra: "dict | None" = None,
    sentinel_ctx_org_extra: "dict | None" = None,
    write_marker: bool = True,
    write_legacy_sentinel: bool = False,
    pre_tracked: "list | None" = None,
    post_green_edit: "list | None" = None,
    post_green_delete: "list | None" = None,
    allowlist_extra: "list | None" = None,
    green_raw_response: "str | None" = None,
) -> _Stage:
    """Build a real repo whose RED commit is landed, whose working tree is
    genuinely dirty on `dirty_paths`, and whose resume artifacts are written
    with the PRODUCTION writers.

    Ordering is load-bearing (§1i):
      1. pre-tracked files committed, RED tests committed,
      2. GREEN content written to `dirty_paths`,
      3. the GREEN completion record persisted BY PRODUCTION CODE — this is
         the moment a per-path sha256 (Design v2 §4) is captured,
      4. ONLY THEN the post-GREEN mutations (`post_green_edit` rewrites the
         content, `post_green_delete` removes the file) are applied.

    `sentinel_run_id`: "same" -> this run's id; None -> no run-keyed sentinel
    at all; any other str -> a FOREIGN run's sentinel.
    `sentinel_ctx_org_extra`: org_config overlay used ONLY to compute the
    sentinel's ctx_hash — models a sentinel written BEFORE a `_retry_nonce`
    bump (§1ac).
    """
    monkeypatch.setenv("HAL_DIRTY_TREE_GUARD", "1")
    monkeypatch.setenv("HAL_GREEN_COMPLETE_RESUME_GATE", "1")

    repo = Path(str((tmp_path / f"repo_{name}").resolve()))
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    for rel in (pre_tracked or []):
        _commit_file(repo, rel, "# pre-existing tracked content\n", f"init {rel}")
    red_sha = _commit_file(
        repo, "tests/test_x.py", RED_TEST_BODY, "build: red cycle 1 tests"
    )

    # (2) the orphaned GREEN as it stood when GREEN completed.
    for rel in dirty_paths:
        _write_file(repo, rel, f"# GREEN implementation for {rel}\n{GREEN_BODY}")

    scratchpad = tmp_path / f"scratch_{name}"
    scratchpad.mkdir(parents=True, exist_ok=True)
    spec_path = _write_spec(
        scratchpad,
        sorted(set(list(manifest) + list(dirty_paths) + list(allowlist_extra or []))),
    )
    ctx = _make_ctx(scratchpad, repo, spec_path, ctx_org_extra)

    # (3) the GREEN completion record, written by production writers.
    s_cycle = current_cycle if sentinel_cycle is None else sentinel_cycle
    payload_kwargs = {} if green_raw_response is None else {"raw_response": green_raw_response}
    payload = _green_sentinel_payload(
        red_sha, manifest, spec_path, s_cycle, scratchpad, repo, **payload_kwargs
    )
    (scratchpad / "tests").mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews").mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews" / "validation.md").write_text(
        "# fixture validation doc\n\nVERDICT: PASS\n", encoding="utf-8"
    )
    (repo / "tests" / "build-red-output.log").write_text("FAILED test_x\n", encoding="utf-8")
    if sentinel_run_id is not None:
        rid = run_id if sentinel_run_id == "same" else sentinel_run_id
        hash_ctx = ctx
        if sentinel_ctx_org_extra:
            hash_ctx = replace(
                ctx, org_config={**ctx.org_config, **sentinel_ctx_org_extra}
            )
        ctx_hash = step_sentinel.compute_ctx_hash(hash_ctx)
        assert ctx_hash, (
            f"fixture precondition FAILED for {name}: compute_ctx_hash returned "
            f"{ctx_hash!r} — without task_description/decision_doc no `_h<hash>` "
            f"sentinel is produced and the ctx-hash ownership class is invisible"
        )
        step_sentinel.write_step_sentinel(
            scratchpad, GREEN_STEP, s_cycle, payload, rid,
            ctx_hash, PHASE_5_WORKFLOW_NAME,
        )
        written = sorted(p.name for p in (scratchpad / "resume").glob("*.json"))
        assert any("_h" in n for n in written), (
            f"fixture precondition FAILED for {name}: expected a real "
            f"`_h<hash12>` sentinel on disk; actual resume dir={written!r}"
        )
    if write_legacy_sentinel:
        # The LEGACY, pre-run_id name the issue itself cites as evidence
        # (`lib/resume_keying.py` predates it; no production writer emits it
        # any more, so it is written literally here).
        legacy = scratchpad / "resume" / f"{GREEN_STEP}_done_c{s_cycle}.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps(payload), encoding="utf-8")
    if write_marker:
        _persist_green_record(scratchpad, red_sha, list(manifest), str(repo), ctx)

    # (4) post-GREEN mutations — strictly AFTER the record was captured.
    for rel in (post_green_edit or []):
        _write_file(repo, rel, f"# GREEN implementation for {rel}\n{POST_GREEN_EDIT_BODY}")
    for rel in (post_green_delete or []):
        os.remove(repo / rel)

    log_path = tmp_path / f"events_{name}.jsonl"
    events = _Events(log_path)
    monkeypatch.setattr(p5, "_emit_safe", events.record)
    telemetry_ctx.set_current_run(
        event_log=EventLog(log_path), run_id=run_id,
        step_name="verify_red_fails_mechanically", phase=PHASE_5_WORKFLOW_NAME,
        cycle=current_cycle,
    )
    telemetry_ctx.set_invocation_run_id(run_id)

    prev = StepResult(
        status="ok",
        data={
            "red_test_paths": ["tests/test_x.py"],
            "red_log_path": "tests/build-red-output.log",
            "spec_path": spec_path,
            "red_commit_sha": red_sha,
            "cycle": current_cycle,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )

    # Fixture preconditions — the tree really is dirty on the paths claimed.
    porcelain = _porcelain(repo)
    for rel in dirty_paths:
        assert rel in porcelain, (
            f"fixture precondition FAILED for {name}: expected {rel!r} dirty in "
            f"`git status --porcelain`; actual output={porcelain!r}"
        )
    return _Stage(repo, red_sha, ctx, prev, scratchpad, events, run_id, spec_path, _head_sha(repo))


# ═════════════════════════════════════════════════════════════════════════
# routing helpers — the engine's REAL contract, not a paraphrase
# ═════════════════════════════════════════════════════════════════════════


def _workflow_step_names() -> list:
    return [s.name for s in p5.phase_5_implement_workflow().steps]


def _step_index(step_name: str) -> int:
    names = _workflow_step_names()
    assert step_name in names, (
        f"fixture precondition FAILED: {step_name!r} absent from the real "
        f"phase_5 workflow; actual steps={names!r}"
    )
    return names.index(step_name)


def _retry_from_step(result) -> "int | None":
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    idx = data.get("retry_from_step")
    if isinstance(idx, bool) or not isinstance(idx, int):
        return None
    return idx


def _engine_reentry_prev(result, next_cycle: int) -> dict:
    """The EXACT plain dict `engine.py:663-682` hands the route target as
    `prev` on re-entry: forwarded non-control keys + cycle + findings.
    §1y — the Host is reproduced, not imagined."""
    data = result.data if isinstance(getattr(result, "data", None), dict) else {}
    forwarded = {
        k: v for k, v in data.items()
        if k not in _RETRY_CONTROL_KEYS and k not in {"cycle", "findings"}
    }
    return {**forwarded, "cycle": next_cycle, "findings": ""}


def _assert_route_target_precedes_the_verification_gates(ac: str) -> None:
    """Round 4 / AC1'': the target is the marker-parsing step and the four
    verification gates sit AFTER it in the real workflow — read from
    `phase_5_implement_workflow()`, never hardcoded."""
    names = _workflow_step_names()
    target_idx = _step_index(ROUTE_TARGET)
    for step in GREEN_VERIFY_STEPS:
        assert _step_index(step) > target_idx, (
            f"{ac}: {step!r} must run AFTER the route target {ROUTE_TARGET!r} "
            f"(index {target_idx}) — otherwise the marker parse does not gate "
            f"the recovery; actual step order={names!r}"
        )


def _assert_routes_to_green_verification(ac: str, result) -> int:
    """The round-2 routing contract (`engine.py:548,556-560`), asserted whole:
    a `status='ok'` result, a missing `recoverable`, or a non-int
    `retry_from_step` all FAIL — each of them silently falls through to
    `verify_red_lint_rules` in production with GREEN sitting in the tree."""
    assert result.status == "error", (
        f"{ac}: engine.py:548 only inspects a result whose status is 'error' — "
        f"a 'ok'/'skip' result is NOT routing, it falls through to the next "
        f"step with GREEN uncommitted in the tree; actual status="
        f"{result.status!r} error_code={result.error_code!r} data={result.data!r}"
    )
    assert result.recoverable is True, (
        f"{ac}: engine.py:556 requires `recoverable` truthy to re-enter; "
        f"actual recoverable={result.recoverable!r}"
    )
    assert isinstance(result.data, dict), (
        f"{ac}: engine.py:558 requires `isinstance(result.data, dict)`; actual "
        f"data={result.data!r} ({type(result.data).__name__})"
    )
    idx = _retry_from_step(result)
    assert idx is not None, (
        f"{ac}: engine.py:559/:568 requires an INT `retry_from_step`; actual "
        f"{result.data.get('retry_from_step')!r} "
        f"({type(result.data.get('retry_from_step')).__name__})"
    )
    expected = _step_index(ROUTE_TARGET)
    assert idx == expected, (
        f"{ac} (AC1'', MAJOR-D): expected re-entry at {ROUTE_TARGET!r} (index "
        f"{expected}, read from the real `phase_5_implement_workflow()`) — the "
        f"sentinel is keyed to {GREEN_STEP!r} and only {ROUTE_TARGET!r} runs "
        f"`_parse_green_status`, so routing PAST it adopts a GREEN that reported "
        f"BLOCKED or was truncated mid-write; actual retry_from_step={idx} which "
        f"is {_workflow_step_names()[idx]!r}"
    )
    _assert_route_target_precedes_the_verification_gates(ac)
    return idx


def _dirty_tree_block_message(step_name: str, violations: list) -> str:
    """bytedigger has no shared message builder (the upstream one arrived with the
    RED-skeleton work, which this engine does not carry); both raise sites build the
    same inline text, reproduced here so the wording-unchanged assertion still holds."""
    return (
        f"uncommitted production changes present at {step_name}: "
        f"{violations} — the tree must be clean before RED can be certified "
        "(operator restart likely left GREEN uncommitted). Commit or revert these "
        "files, then resume. Escape: HAL_DIRTY_TREE_GUARD=0."
    )


def _assert_terminal_refusal(ac: str, stage: _Stage, result, expected_violations: list, step_name: str = "verify_red_fails_mechanically") -> None:
    """Shared guard-intact assertion set: exact error code, recoverable is
    False, wording unchanged (compared against the ONE production source,
    `_dirty_tree_block_message`), NO commit produced, and NO recovery event —
    so a GREEN that over-rescues cannot pass."""
    head_before = stage.head_before
    assert result.status == "error", (
        f"{ac}: expected status=='error' (terminal dirty-tree refusal); "
        f"actual status={result.status!r} data={result.data!r}"
    )
    assert result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"{ac}: expected error_code=='E_RED_WORKTREE_DIRTY'; actual "
        f"{result.error_code!r} (error={getattr(result, 'error', '')!r})"
    )
    assert result.recoverable is False, (
        f"{ac}: expected recoverable is False (unchanged terminal contract); "
        f"actual {result.recoverable!r}"
    )
    assert _retry_from_step(result) is None, (
        f"{ac}: a terminal refusal must declare NO re-entry target; actual "
        f"retry_from_step={_retry_from_step(result)!r}"
    )
    expected_msg = _dirty_tree_block_message(step_name, sorted(expected_violations))
    assert (getattr(result, "error", "") or "") == expected_msg, (
        f"{ac}: expected the refusal wording UNCHANGED (the single source, "
        f"`_dirty_tree_block_message`) over violations "
        f"{sorted(expected_violations)!r};\nexpected={expected_msg!r}\n"
        f"actual={getattr(result, 'error', '')!r}"
    )
    assert _head_sha(stage.repo) == head_before, (
        f"{ac}: expected NO commit produced on a terminal refusal; actual HEAD "
        f"moved {head_before!r} -> {_head_sha(stage.repo)!r}"
    )
    porcelain = _porcelain(stage.repo)
    for rel in expected_violations:
        assert rel in porcelain, (
            f"{ac}: expected {rel!r} still UNCOMMITTED after the refusal; "
            f"actual `git status --porcelain`={porcelain!r}"
        )
    assert stage.events.of(RECOVERY_EVENT) == [], (
        f"{ac}: expected NO {RECOVERY_EVENT!r} event on a terminal refusal "
        f"(an over-rescuing GREEN must not pass this AC); actual events="
        f"{stage.events.types()!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC1' — the REAL routing contract (MAJOR-5)
# ═════════════════════════════════════════════════════════════════════════


def test_ac1_recovery_returns_the_engines_real_reentry_contract(tmp_path, monkeypatch):
    """AC1'' (supersedes AC1'/round-1 AC1): every dirty production path is
    inside THIS run's completed-GREEN record, so the step must return the three
    things `engine.py:548,556-560` actually consume — `status=='error'`,
    `recoverable is True`, and an INT `retry_from_step` equal to the index of
    `write_green_artifact` (MAJOR-D: the marker-parsing step, read from the real
    workflow, never hardcoded — routing to index 6 would skip the parse and
    adopt a BLOCKED GREEN). Round 1 asserted none of them and a `status='ok'`
    result passed it.

    Pre-GREEN FAIL: today `_verify_red_dirty_tree_guard` (:2974-3006) never
    consults the GH483 seam and returns the terminal refusal unconditionally —
    `recoverable is False`, no `retry_from_step`."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac1", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    assert result.error_code != "E_RED_WORKTREE_DIRTY", (
        f"AC1': expected NO terminal E_RED_WORKTREE_DIRTY — the only dirty "
        f"production path {manifest!r} is inside this run's ({stage.run_id!r}) "
        f"completed-GREEN record; actual error_code={result.error_code!r} "
        f"error={getattr(result, 'error', '')!r}"
    )
    _assert_routes_to_green_verification("AC1'", result)
    assert _head_sha(stage.repo) == stage.head_before, (
        f"AC1': the guard itself must not commit anything — recovery restores "
        f"the pipeline's position, it never buys a commit; actual HEAD moved "
        f"{stage.head_before!r} -> {_head_sha(stage.repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC13' — a WORKING recovery, not an inert one (MAJOR-A; supersedes AC13)
# ═════════════════════════════════════════════════════════════════════════


def _recording_slice(steps: list, record: list, *, skip_on_error: "bool | None" = True) -> list:
    """Copy each real `StepContract` with a transparent recorder around its
    UNMODIFIED production `execute`, plus `skip_on_error=True`.

    This is an OBSERVER, not a stub: the production function is called with the
    engine's own arguments and its return value is passed straight back — the
    recorder only remembers `(name, prev, result)`. It is required because
    `step_finished` (`engine.py:509`) carries only name/status/duration and
    therefore cannot answer the MAJOR-A question: WHICH production set did the
    lint gate see? `skip_on_error=True` keeps a single gate failure from
    truncating the observed sequence; `skip_on_error=None` leaves each step's
    PRODUCTION value untouched — which is what AC28 needs, because there the
    whole point is that the chain stops exactly where production stops it.
    """
    out = []
    for s in steps:
        def _observer(real=s.execute, name=s.name):
            def _observed(ctx, prev):
                res = real(ctx, prev)
                record.append({"name": name, "prev": prev, "result": res})
                return res
            return _observed
        if skip_on_error is None:
            out.append(replace(s, execute=_observer()))
        else:
            out.append(replace(s, execute=_observer(), skip_on_error=skip_on_error))
    return out


def _prev_data(prev) -> dict:
    """`prev` is a StepResult on a normal step boundary and a plain dict on the
    engine's re-entry boundary (`engine.py:400`) — read both."""
    inner = getattr(prev, "data", None)
    if isinstance(inner, dict):
        return inner
    return prev if isinstance(prev, dict) else {}


def test_ac13_recovery_is_a_working_recovery_not_an_inert_one(tmp_path, monkeypatch):
    """AC13' (MAJOR-A, supersedes AC13). Round 2's AC13 asserted only that step
    NAMES appeared in `step_finished` — which `engine.py:509` emits regardless of
    status, and every sliced step carries `skip_on_error=True`. So a GREEN that
    routes to the right index but forwards no `red_commit_sha` made all four
    gates return `skipped: no_production_files` (`phase_5_implement.py:5973`) and
    `commit_green_code` fail, while the table went green. AC13' therefore asserts
    all three legs:

      (a) the run STARTS at `write_green_artifact` (AC13''/MAJOR-D: the marker
          parse gates the recovery) and the four green-verification steps then
          EXECUTE AFTER it — observed as their real recorded StepResults;
      (b) the lint gate saw a NON-EMPTY production set: the `red_commit_sha` the
          guard forwards reaches it (`:5945`), it did not fall back to the
          working-tree derivation (`:5970 green_lint_sha_fallback`), and it did
          NOT return `skipped: no_production_files` (`:5973-5981`);
      (c) the recovery actually MOVED HEAD and the manifest path is no longer in
          `git status --porcelain` — the engine committed its own work.

    Every `execute` on this path is the unmodified production function; the only
    fixture bounds are the slice end at `commit_green_code` (dropping
    `green_watchdog`, whose RetryPolicy sleeps 30s) and the recorder wrapper.

    Pre-GREEN FAIL: no routing exists at all, so `retry_from_step` is absent and
    the engine is never re-entered — the test dies on the routing contract."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac13", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    idx = _assert_routes_to_green_verification("AC13'", result)

    reentry = _engine_reentry_prev(result, 2)
    # Leg (b), the deterministic half — asserted BEFORE anything executes,
    # because this is precisely the inert shape MAJOR-A describes: a route to
    # the right index carrying no RED boundary, so every gate downstream scopes
    # its diff over an empty set and skips.
    assert reentry.get("red_commit_sha") == stage.red_sha, (
        f"AC13' leg (b): the routing result must FORWARD this run's RED boundary "
        f"so the green gates can diff against it (`:5945`, `:6560`); expected "
        f"red_commit_sha=={stage.red_sha!r}, actual "
        f"{reentry.get('red_commit_sha')!r}. Without it all four gates return "
        f"`skipped: no_production_files` and the recovery is inert."
    )

    recorded: list = []
    real_steps = p5.phase_5_implement_workflow().steps
    commit_idx = _step_index("commit_green_code")
    sliced = WorkflowDefinition(
        name=PHASE_5_WORKFLOW_NAME,
        steps=_recording_slice(real_steps[: commit_idx + 1], recorded),
    )
    engine_log = tmp_path / "events_ac13_engine.jsonl"
    engine = WorkflowEngine(EventLog(engine_log))
    engine._execute_steps(
        sliced, stage.ctx, stage.run_id,
        initial_data=reentry, start_step=idx, cycle=2,
    )

    # ── leg (a): the four gates really ran, starting at the route target ──
    executed = [r["name"] for r in recorded]
    assert executed, (
        f"AC13' leg (a): expected the re-entered run to execute real steps; "
        f"actual recorded={executed!r}, engine events="
        f"{_Events(engine_log).types()!r}"
    )
    assert executed[0] == ROUTE_TARGET, (
        f"AC13' leg (a): expected the re-entered run to START at {ROUTE_TARGET!r}; "
        f"actual first executed step={executed[0]!r} (sequence={executed!r})"
    )
    missing = [s for s in GREEN_VERIFY_STEPS if s not in executed]
    assert not missing, (
        f"AC13' leg (a): expected ALL of {list(GREEN_VERIFY_STEPS)!r} to execute "
        f"after recovery; MISSING={missing!r} — actual sequence={executed!r}. "
        f"Routing past these gates commits code whose tests never ran."
    )
    for step in GREEN_VERIFY_STEPS:
        assert executed.index(step) > executed.index(ROUTE_TARGET), (
            f"AC13'' leg (a): {step!r} must run AFTER {ROUTE_TARGET!r} — the "
            f"marker parse gates the recovery, the gates do not precede it; "
            f"actual sequence={executed!r}"
        )
    artifact = next((r for r in recorded if r["name"] == ROUTE_TARGET), None)
    assert artifact is not None and artifact["result"].status == "ok", (
        f"AC13'' leg (a): expected {ROUTE_TARGET!r} to EXECUTE and accept this "
        f"COMPLETE-marker GREEN; actual result="
        f"{None if artifact is None else (artifact['result'].status, artifact['result'].error_code, getattr(artifact['result'], 'error', ''))!r}"
    )

    # ── leg (b): the lint gate saw a non-empty production set ──
    lint = next((r for r in recorded if r["name"] == LINT_STEP), None)
    assert lint is not None, (
        f"AC13' leg (b): no recorded result for {LINT_STEP!r}; "
        f"sequence={executed!r}"
    )
    assert _prev_data(lint["prev"]).get("red_commit_sha") == stage.red_sha, (
        f"AC13' leg (b): {LINT_STEP!r} must RECEIVE the RED boundary through "
        f"the re-entered chain (routing prev -> {ROUTE_TARGET!r} -> here); "
        f"expected {stage.red_sha!r}, actual "
        f"{_prev_data(lint['prev']).get('red_commit_sha')!r} "
        f"(prev keys={sorted(_prev_data(lint['prev']))!r})"
    )
    lint_data = lint["result"].data if isinstance(lint["result"].data, dict) else {}
    assert lint_data.get("skipped") != "no_production_files", (
        f"AC13' leg (b): {LINT_STEP!r} returned `skipped: no_production_files` "
        f"(`phase_5_implement.py:5973`) — it saw an EMPTY production set, so the "
        f"recovery routed correctly and verified nothing. lint result data="
        f"{lint_data!r}"
    )
    assert not lint_data.get("green_lint_sha_fallback"), (
        f"AC13' leg (b): {LINT_STEP!r} fell back to the working-tree "
        f"derivation (`:5970`), i.e. it received no `red_commit_sha` and its "
        f"scope is not the RED boundary; lint result data={lint_data!r}"
    )

    # ── leg (c): the recovery MOVED HEAD; the manifest path is committed ──
    tooling_absent = [
        (r["name"], r["result"].error_code)
        for r in recorded
        if getattr(r["result"], "error_code", None) in TOOLING_ABSENT_CODES
    ]
    if tooling_absent:
        pytest.skip(
            f"AC13' leg (c) not observable on this machine: a build-critical "
            f"external binary is absent — {tooling_absent!r}. Legs (a) and (b) "
            f"were asserted above."
        )
    head_after = _head_sha(stage.repo)
    assert head_after != stage.head_before, (
        f"AC13' leg (c): the recovery must reach `commit_green_code` and the "
        f"ENGINE must produce the commit; HEAD never moved (still "
        f"{stage.head_before!r}). Recorded chain="
        f"{[(r['name'], r['result'].status, r['result'].error_code) for r in recorded]!r}"
    )
    assert "src/module.py" not in _porcelain(stage.repo), (
        f"AC13' leg (c): the recovered manifest path must no longer be dirty "
        f"after the engine's own commit; actual `git status --porcelain`="
        f"{_porcelain(stage.repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC14 — the route target survives the engine's REAL dict `prev` (MAJOR-1, §1y)
# ═════════════════════════════════════════════════════════════════════════


def test_ac14_route_target_survives_the_engines_plain_dict_prev(tmp_path, monkeypatch):
    """AC14 (§1y, Point->Host->Test): engine re-entry sets `prev = initial_data`
    — a plain DICT (`engine.py:400`, built at :663-682) — and hands it to the
    route target. Round 1 hid this behind a `MagicMock(data=...)`, concealing
    that `_commit_green_code` does `(prev.data or {})` (:8270) and would raise
    an unhandled `AttributeError` on a dict.

    This AC binds whatever step the guard actually names: the target is
    resolved from the declared `retry_from_step` and invoked with the engine's
    real dict. It must neither raise `AttributeError` nor refuse on prev
    handling (`E_MISSING_PREV_DATA` / `E_LLM_MANIFEST_MISSING_AT_CONSUMER`) —
    a route target that cannot read its own `prev` is a route to nowhere.

    Pre-GREEN FAIL: no routing exists at all. Even once it does,
    `_write_green_artifact` (:7899) opens with
    `if not isinstance(prev, StepResult) ...: E_MISSING_PREV_DATA` and then
    subscripts `prev.data["raw_response"]` / `["log_path"]`, so the round-4
    target's dict handling is genuinely not yet built either."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac14", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    idx = _assert_routes_to_green_verification("AC14", result)

    target = p5.phase_5_implement_workflow().steps[idx]
    reentry_prev = _engine_reentry_prev(result, 2)
    assert isinstance(reentry_prev, dict), "AC14 fixture precondition: prev must be a plain dict"

    try:
        target_result = target.execute(stage.ctx, reentry_prev)
    except AttributeError as exc:
        pytest.fail(
            f"AC14: route target {target.name!r} raised AttributeError on the "
            f"engine's real dict `prev` (engine.py:400): {exc}. prev keys="
            f"{sorted(reentry_prev)!r}"
        )

    assert target_result.error_code not in ("E_MISSING_PREV_DATA", "E_LLM_MANIFEST_MISSING_AT_CONSUMER"), (
        f"AC14: route target {target.name!r} refused the engine's real dict "
        f"`prev` with error_code={target_result.error_code!r} "
        f"({getattr(target_result, 'error', '')!r}) — the re-entry would land on "
        f"a step that cannot read its own input. prev keys={sorted(reentry_prev)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC28 — a BLOCKED GREEN is NOT adopted (MAJOR-D, the adoption hole)
# ═════════════════════════════════════════════════════════════════════════


def test_ac28_blocked_green_with_a_valid_ownership_tuple_is_not_adopted(tmp_path, monkeypatch):
    """AC28 (MAJOR-D). This is the case the round-2 design silently adopted.

    The sentinel is keyed to `invoke_green_llm` (index 3) and carries a PERFECT
    ownership tuple — this run's run_id, this cycle, this ctx hash — and the
    tree's digests match the record exactly. The ONLY difference from AC1'' is
    the marker inside the GREEN's own output: `GREEN BLOCKED` instead of
    `GREEN COMPLETE`. Nothing in the ownership check can see that, because
    `_invoke_green_llm` returns `status="ok"` regardless of the marker; only
    `write_green_artifact` (index 5) runs `_parse_green_status` (:7911) and
    raises `E_GREEN_BLOCKED` (:7989-7995) / `E_GREEN_NO_MARKER` (:7996-8002).
    The gap between index 3 and index 5 IS the crash window this lot exists for.

    So the run must END on the green-status error and produce NO commit. Steps
    keep their PRODUCTION `skip_on_error` here (no override): the point is that
    the chain stops where production stops it, instead of sailing on into
    `commit_green_code`.

    Pre-GREEN FAIL: no routing exists, so this dies on the routing contract.
    Post-GREEN it is the pin that stops a route to index 6 (which would skip
    the marker parse entirely and commit a BLOCKED GREEN)."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac28", monkeypatch, manifest=manifest, dirty_paths=manifest,
        green_raw_response=(
            "I could not implement this.\n\nGREEN BLOCKED\n\n"
            "(fixture: gh1626D — the GREEN reported BLOCKED before the artifact "
            "was ever written; the sentinel at index 3 knows nothing about it)"
        ),
    )
    assert p5._parse_green_status(
        "I could not implement this.\n\nGREEN BLOCKED\n"
    ) == p5.GREEN_BLOCKED, (
        "AC28 fixture precondition FAILED: the fixture's raw_response must be "
        "read as BLOCKED by the REAL `_parse_green_status`"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    idx = _assert_routes_to_green_verification("AC28", result)

    recorded: list = []
    real_steps = p5.phase_5_implement_workflow().steps
    commit_idx = _step_index("commit_green_code")
    sliced = WorkflowDefinition(
        name=PHASE_5_WORKFLOW_NAME,
        steps=_recording_slice(
            real_steps[: commit_idx + 1], recorded, skip_on_error=None
        ),
    )
    engine = WorkflowEngine(EventLog(tmp_path / "events_ac28_engine.jsonl"))
    final = engine._execute_steps(
        sliced, stage.ctx, stage.run_id,
        initial_data=_engine_reentry_prev(result, 2), start_step=idx, cycle=2,
    )

    executed = [r["name"] for r in recorded]
    chain = [(r["name"], r["result"].status, r["result"].error_code) for r in recorded]
    assert ROUTE_TARGET in executed, (
        f"AC28: {ROUTE_TARGET!r} must EXECUTE on the recovery path — it is the "
        f"only step that judges the GREEN's completion marker; actual "
        f"sequence={executed!r}"
    )
    artifact = next(r for r in recorded if r["name"] == ROUTE_TARGET)
    assert artifact["result"].error_code in GREEN_STATUS_ERROR_CODES, (
        f"AC28: expected {ROUTE_TARGET!r} to REFUSE this BLOCKED GREEN with one "
        f"of {list(GREEN_STATUS_ERROR_CODES)!r}; actual status="
        f"{artifact['result'].status!r} error_code={artifact['result'].error_code!r} "
        f"error={getattr(artifact['result'], 'error', '')!r}"
    )
    assert final.status == "error" and final.error_code in GREEN_STATUS_ERROR_CODES, (
        f"AC28: the run must END on the green-status error; actual "
        f"status={final.status!r} error_code={final.error_code!r} "
        f"error={getattr(final, 'error', '')!r} chain={chain!r}"
    )
    assert "commit_green_code" not in executed, (
        f"AC28: `commit_green_code` must NEVER be reached after a BLOCKED "
        f"GREEN; actual sequence={executed!r}"
    )
    assert _head_sha(stage.repo) == stage.head_before, (
        f"AC28: NO commit may be produced from a BLOCKED GREEN; actual HEAD "
        f"moved {stage.head_before!r} -> {_head_sha(stage.repo)!r} chain={chain!r}"
    )
    assert "src/module.py" in _porcelain(stage.repo), (
        f"AC28: the BLOCKED GREEN's work must stay UNCOMMITTED in the tree; "
        f"actual `git status --porcelain`={_porcelain(stage.repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC2 — guard intact: one dirty path OUTSIDE the manifest
# ═════════════════════════════════════════════════════════════════════════


def test_ac2_dirty_path_outside_the_green_manifest_stays_terminal(tmp_path, monkeypatch):
    """AC2: this run's completed-GREEN record covers only src/module.py, but
    src/other.py is ALSO dirty -> terminal `E_RED_WORKTREE_DIRTY`,
    recoverable=False, wording unchanged, no commit, no recovery event.

    Discriminator: a GREEN that derives the "manifest" from the git diff
    instead of the RECORDED manifest would adopt src/other.py and fail here.
    Today's guard already refuses, so this AC passes now — it is the
    correctness pin that an over-rescuing GREEN must not break."""
    manifest = ["src/module.py"]
    dirty = ["src/module.py", "src/other.py"]
    stage = _stage(tmp_path, "ac2", monkeypatch, manifest=manifest, dirty_paths=dirty)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC2", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC3 — guard intact: no completed GREEN for this run at all
# ═════════════════════════════════════════════════════════════════════════


def test_ac3_no_completed_green_for_this_run_stays_terminal(tmp_path, monkeypatch):
    """AC3: the tree is dirty on an in-allowlist production path, but NO
    completed-GREEN artifact exists for this run (no run-keyed sentinel, no
    GH483 marker) -> terminal, exactly as today. The git diff alone (which
    `_detect_green_complete_resume` would happily report) is NOT evidence that
    a GREEN ran."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac3", monkeypatch, manifest=dirty, dirty_paths=dirty,
        sentinel_run_id=None, write_marker=False,
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC3", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC4 — guard intact: a FOREIGN run's completed GREEN
# ═════════════════════════════════════════════════════════════════════════


def test_ac4_completed_green_of_a_different_run_id_is_never_adopted(tmp_path, monkeypatch):
    """AC4 (run identity, echo of part A): a completed-GREEN sentinel exists
    and its manifest covers the dirty path, but it is keyed to a DIFFERENT
    run_id (`..._rforeignrun9999_h<hash>.json`); the run-less GH483 marker is
    present too, and is likewise no proof of ownership -> terminal. Another
    run's work is never adopted."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac4", monkeypatch, manifest=dirty, dirty_paths=dirty,
        run_id="run1626dcafe", sentinel_run_id="foreignrun9999", write_marker=True,
    )
    resume_dir = stage.scratchpad / "resume"
    foreign = sorted(p.name for p in resume_dir.glob("*_rforeignrun9999*.json"))
    mine = sorted(p.name for p in resume_dir.glob(f"*_r{stage.run_id}*.json"))
    assert foreign and not mine, (
        f"AC4 fixture precondition FAILED: expected a foreign-run sentinel and "
        f"NO sentinel for this run; actual foreign={foreign!r} mine={mine!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC4", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC5 — guard intact: the LEGACY bare sentinel the issue cites as evidence
# ═════════════════════════════════════════════════════════════════════════


def test_ac5_legacy_bare_sentinel_name_proves_no_ownership_and_stays_terminal(tmp_path, monkeypatch):
    """AC5: the ONLY completed-GREEN sentinel is `invoke_green_llm_done_c1.json`
    — the pre-run_id LEGACY name the issue's own evidence cites, carrying a
    manifest that covers the dirty path, plus the run-less GH483 marker. It has
    no `_r<run_id>` segment (`lib/resume_keying.py:13`, `_NO_RUN='norun'` :10),
    so it cannot establish which run produced the work -> terminal. Accepting
    it would repeat part A's defect in a new place."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac5", monkeypatch, manifest=dirty, dirty_paths=dirty,
        sentinel_run_id=None, write_legacy_sentinel=True, write_marker=True,
    )
    legacy = stage.scratchpad / "resume" / f"{GREEN_STEP}_done_c1.json"
    assert legacy.is_file(), (
        f"AC5 fixture precondition FAILED: expected the legacy sentinel at "
        f"{legacy}; actual resume dir contents="
        f"{sorted(p.name for p in (stage.scratchpad / 'resume').glob('*'))!r}"
    )
    assert "_r" not in legacy.name.replace("done_c1", ""), (
        f"AC5 fixture precondition FAILED: the legacy name must carry NO "
        f"run_id segment; actual {legacy.name!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC5", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC15 — an EARLIER CYCLE of the SAME run is not ours (MAJOR-3a)
# ═════════════════════════════════════════════════════════════════════════


def test_ac15_completed_green_from_an_earlier_cycle_stays_terminal(tmp_path, monkeypatch):
    """AC15: the sentinel carries this run's run_id and this ctx's hash, but it
    was written at CYCLE 1 while the run is now at CYCLE 2 — the real sentinel
    key is (workflow, step, cycle, run_id, ctx_hash), and a previous cycle's
    GREEN is a different piece of work. The run-less GH483 marker is present
    too. -> terminal.

    Pre-GREEN this passes (nothing is ever adopted); post-GREEN it is the pin
    that stops ownership from collapsing to run_id alone."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac15", monkeypatch, manifest=dirty, dirty_paths=dirty,
        current_cycle=2, sentinel_cycle=1, write_marker=True,
    )
    resume_dir = stage.scratchpad / "resume"
    c1 = sorted(p.name for p in resume_dir.glob("*_done_c1_*.json"))
    c2 = sorted(p.name for p in resume_dir.glob("*_done_c2_*.json"))
    assert c1 and not c2, (
        f"AC15 fixture precondition FAILED: expected a cycle-1 sentinel and NO "
        f"cycle-2 sentinel while the run is at cycle 2; actual c1={c1!r} c2={c2!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC15", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC16 — a sentinel written before a `_retry_nonce` bump (MAJOR-3b, §1ac)
# ═════════════════════════════════════════════════════════════════════════


def test_ac16_sentinel_with_a_stale_ctx_hash_stays_terminal(tmp_path, monkeypatch):
    """AC16 (§1ac): the sentinel carries this run's run_id and this cycle, but
    its `ctx_hash` segment was computed with `_retry_nonce="1"` while the ctx
    now carries `_retry_nonce="2"` — exactly the state after an operator bumps
    the very knob AC8 documents. `compute_ctx_hash` folds org_config through
    `ctx_cfg_sha8` (`lib/step_sentinel.py:107`), so the two hashes really
    differ and the on-disk name really is `..._h<stale>.json`. That sentinel is
    NOT ours -> terminal.

    Round 1 could not see this class at all: its ctx set neither
    `task_description` nor `decision_doc`, so `compute_ctx_hash` returned None
    and no `_h` sentinel was ever produced."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac16", monkeypatch, manifest=dirty, dirty_paths=dirty,
        ctx_org_extra={"_retry_nonce": "2"},
        sentinel_ctx_org_extra={"_retry_nonce": "1"},
        write_marker=True,
    )
    current_hash = step_sentinel.compute_ctx_hash(stage.ctx)
    stale_hash = step_sentinel.compute_ctx_hash(
        replace(stage.ctx, org_config={**stage.ctx.org_config, "_retry_nonce": "1"})
    )
    assert current_hash and stale_hash and current_hash != stale_hash, (
        f"AC16 fixture precondition FAILED: a `_retry_nonce` bump must change "
        f"the ctx hash; actual current={current_hash!r} stale={stale_hash!r}"
    )
    resume_dir = stage.scratchpad / "resume"
    names = sorted(p.name for p in resume_dir.glob("*.json"))
    assert any(n.endswith(f"_h{stale_hash[:12]}.json") for n in names), (
        f"AC16 fixture precondition FAILED: expected a real STALE-hash sentinel "
        f"`..._h{stale_hash[:12]}.json` on disk; actual resume dir={names!r}"
    )
    assert not any(n.endswith(f"_h{current_hash[:12]}.json") for n in names), (
        f"AC16 fixture precondition FAILED: no sentinel may carry the CURRENT "
        f"ctx hash; actual resume dir={names!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC16", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC17 — an empty current run_id degrades to `_rnorun`, which never qualifies
# ═════════════════════════════════════════════════════════════════════════


def test_ac17_empty_current_run_id_never_qualifies_as_ownership(tmp_path, monkeypatch):
    """AC17 (MAJOR-3c): the current run has NO run_id (both telemetry slots
    empty), so `resume_keying._NO_RUN` degrades every name to `_rnorun` — and a
    sentinel written under `_rnorun` matches ANY other run whose id is likewise
    absent. "norun" is not an identity; ownership requires a NON-EMPTY run_id.
    The sentinel below is a real `_rnorun` file with this cycle and this ctx
    hash, and the GH483 marker is present -> still terminal."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac17", monkeypatch, manifest=dirty, dirty_paths=dirty,
        run_id="", sentinel_run_id="same", write_marker=True,
    )
    names = sorted(p.name for p in (stage.scratchpad / "resume").glob("*.json"))
    assert any("_rnorun" in n for n in names), (
        f"AC17 fixture precondition FAILED: expected a `_rnorun` sentinel "
        f"(resume_keying.py:10/:29); actual resume dir={names!r}"
    )
    assert telemetry_ctx.get_invocation_run_id() == "", (
        f"AC17 fixture precondition FAILED: the current run_id must be empty; "
        f"actual {telemetry_ctx.get_invocation_run_id()!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC17", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC18 — CONTENT, not names: a post-GREEN edit (MAJOR-4a)
# ═════════════════════════════════════════════════════════════════════════


def test_ac18_dirty_path_edited_after_the_green_record_stays_terminal(tmp_path, monkeypatch):
    """AC18: the dirty path IS in this run's manifest, under the right run_id,
    cycle and ctx hash — but its content changed AFTER the GREEN record was
    persisted (the window spans crash and restart; a post-GREEN edit, a
    truncated write and a partial hand-revert all satisfy `dirty ⊆ manifest`).
    The current sha256 differs from the digest recorded at GREEN -> terminal.

    The digest format is NOT guessed: the record is written by the production
    writer `_persist_green_complete_resume` while the tree still holds the
    GREEN content, and only then is the file rewritten. Design v2 §4 requires
    that writer to record a per-path sha256; if it records none, nothing can
    distinguish this tree from AC1's and the test fails — which is the point."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac18", monkeypatch, manifest=dirty, dirty_paths=dirty,
        post_green_edit=dirty, write_marker=True,
    )
    recorded_digest = hashlib.sha256(
        f"# GREEN implementation for src/module.py\n{GREEN_BODY}".encode()
    ).hexdigest()
    current_digest = _sha256(stage.repo / "src" / "module.py")
    assert current_digest != recorded_digest, (
        f"AC18 fixture precondition FAILED: the post-GREEN edit must change the "
        f"file digest; actual current={current_digest!r} at-GREEN={recorded_digest!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC18", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC19 — the porcelain XY: a deletion is not an implementation (MAJOR-4b)
# ═════════════════════════════════════════════════════════════════════════


def test_ac19_dirty_path_that_is_a_deletion_stays_terminal(tmp_path, monkeypatch):
    """AC19: src/module.py is tracked, was carrying the GREEN content when the
    record was persisted, and has since been DELETED from the working tree.
    Its NAME is still in the manifest, and `dirty_prod_paths`
    (`lib/dirty_tree_guard.py:83`) drops the porcelain XY column — so a
    name-only subset test would adopt a deletion as an implementation and
    "recover" by committing a removal. The XY really is a deletion here, and
    the answer must be terminal."""
    dirty = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac19", monkeypatch, manifest=dirty, dirty_paths=dirty,
        pre_tracked=dirty, post_green_delete=dirty, write_marker=True,
    )
    xy = _porcelain_xy(stage.repo, "src/module.py")
    assert xy is not None and "D" in xy, (
        f"AC19 fixture precondition FAILED: expected a DELETION status for "
        f"src/module.py; actual XY={xy!r} porcelain={_porcelain(stage.repo)!r}"
    )
    assert not (stage.repo / "src" / "module.py").exists(), (
        "AC19 fixture precondition FAILED: the file must really be gone"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    assert result.status == "error", (
        f"AC19: a deleted path is not a GREEN implementation — expected the "
        f"terminal refusal; actual status={result.status!r} "
        f"error_code={result.error_code!r} data={result.data!r}"
    )
    assert result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"AC19: expected E_RED_WORKTREE_DIRTY; actual {result.error_code!r} "
        f"({getattr(result, 'error', '')!r})"
    )
    assert result.recoverable is False, (
        f"AC19: expected recoverable is False; actual {result.recoverable!r}"
    )
    assert stage.events.of(RECOVERY_EVENT) == [], (
        f"AC19: a deletion must NEVER be adopted as recoverable orphan GREEN; "
        f"actual events={stage.events.types()!r}"
    )
    assert _head_sha(stage.repo) == stage.head_before, (
        f"AC19: expected NO commit; actual HEAD moved {stage.head_before!r} -> "
        f"{_head_sha(stage.repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC6 — no forged commit: the commit step still refuses an empty diff
# ═════════════════════════════════════════════════════════════════════════


def test_ac6_routed_commit_with_empty_diff_is_e_green_commit_empty(tmp_path, monkeypatch):
    """AC6: recovery is legitimately offered (dirty ⊆ this run's record), but
    between the routing decision and the commit the work vanishes from the tree
    (a REAL `git stash push -u`, the #1612 leak mechanism) -> `commit_green_code`
    returns `E_GREEN_COMMIT_EMPTY`, NOT a green-looking empty commit. Recovery
    buys the operator the engine's guards; it must never buy him a forged
    commit.

    `prev` here is a REAL `StepResult` (no MagicMock anywhere): the GH483
    resume shape `_commit_green_code` reads at :8299-8304.

    Leg 1 (routing not terminal) fails today; leg 2 is a correctness pin over
    already-shipped #1612-A behaviour, asserted on the recovery path."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac6", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    assert result.error_code != "E_RED_WORKTREE_DIRTY", (
        f"AC6 (leg 1): expected the recovery to be offered before the commit "
        f"leg can be judged at all; actual error_code={result.error_code!r}: "
        f"{getattr(result, 'error', '')!r}"
    )

    subprocess.run(
        ["git", "stash", "push", "-u", "-m", "gh1626d-ac6-leak"],
        cwd=stage.repo, check=True, capture_output=True, text=True,
    )
    assert not (stage.repo / "src" / "module.py").exists(), (
        "AC6 fixture precondition FAILED: expected the real `git stash push -u` "
        "to have removed the orphaned GREEN file from the tree"
    )

    commit_prev = StepResult(
        status="ok",
        data={
            "cycle": 1,
            "red_commit_sha": stage.red_sha,
            "green_complete_resume": True,
            "green_resume_paths": list(manifest),
            "worker_written_paths": list(manifest),
            "manifest_source": "harness_tool_record",
            "spec_path": stage.spec_path,
        },
        duration_ms=0,
        step_name="verify_green_typecheck",
    )
    commit_result = p5._commit_green_code(stage.ctx, commit_prev)

    assert commit_result.error_code == "E_GREEN_COMMIT_EMPTY", (
        f"AC6 (leg 2): expected error_code=='E_GREEN_COMMIT_EMPTY' on a commit "
        f"whose manifest yields an empty diff; actual "
        f"{commit_result.error_code!r} (status={commit_result.status!r}: "
        f"{getattr(commit_result, 'error', '')!r})"
    )
    assert _head_sha(stage.repo) == stage.head_before, (
        f"AC6: expected NO commit produced; actual HEAD moved "
        f"{stage.head_before!r} -> {_head_sha(stage.repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC20 — the SECOND raise site gets BOTH halves (MAJOR-6)
# ═════════════════════════════════════════════════════════════════════════


def test_ac20_site2_build_validation_prompt_recovers_under_the_same_preconditions(tmp_path, monkeypatch):
    """AC20a (supersedes round-1 AC7): the inline guard in
    `_build_validation_prompt` (:6835-6852) applies the SAME rule as
    `_verify_red_dirty_tree_guard` — under identical recovery preconditions it
    must not refuse with `E_RED_WORKTREE_DIRTY`, and when it routes it must use
    the same engine contract. One site fixed is half a class (part A's lesson).

    Pre-GREEN FAIL: today site 2 refuses unconditionally, identically to site 1."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac20a", monkeypatch, manifest=manifest, dirty_paths=manifest)
    (stage.scratchpad / "tests").mkdir(parents=True, exist_ok=True)
    (stage.scratchpad / "reviews").mkdir(parents=True, exist_ok=True)
    (Path(stage.repo) / "tests" / "build-red-output.log").write_text("FAILED test_x\n")

    try:
        result = p5._build_validation_prompt(stage.ctx, stage.prev)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"AC20a: expected `_build_validation_prompt` to return an early "
            f"recovery-routing result under the recovery preconditions (dirty "
            f"⊆ this run's GREEN record); actual it raised past the guard: "
            f"{type(exc).__name__}: {exc}"
        )

    assert result.error_code != "E_RED_WORKTREE_DIRTY", (
        f"AC20a: expected raise site 2 (build_validation_prompt, :6835-6852) to "
        f"apply the same recovery rule as site 1; actual "
        f"error_code={result.error_code!r}: {getattr(result, 'error', '')!r}"
    )
    if result.status == "error":
        _assert_routes_to_green_verification("AC20a", result)
    assert _head_sha(stage.repo) == stage.head_before, (
        f"AC20a: expected no commit produced by the guard itself; actual HEAD "
        f"moved {stage.head_before!r} -> {_head_sha(stage.repo)!r}"
    )


def test_ac20_site2_build_validation_prompt_stays_terminal_outside_the_manifest(tmp_path, monkeypatch):
    """AC20b (MAJOR-6): the terminal half of raise site 2 — without it,
    "delete the guard at :6835-6852" passes the whole AC table. This run's
    GREEN record covers only src/module.py while src/other.py is ALSO dirty ->
    `E_RED_WORKTREE_DIRTY`, `recoverable=False`, wording unchanged (the
    `build_validation_prompt` spelling from `_dirty_tree_block_message`), and
    NO recovery event.

    Passes today; it is the pin that keeps site 2 armed after the GREEN."""
    manifest = ["src/module.py"]
    dirty = ["src/module.py", "src/other.py"]
    stage = _stage(tmp_path, "ac20b", monkeypatch, manifest=manifest, dirty_paths=dirty)
    (Path(stage.repo) / "tests" / "build-red-output.log").write_text("FAILED test_x\n")

    result = p5._build_validation_prompt(stage.ctx, stage.prev)

    _assert_terminal_refusal(
        "AC20b", stage, result, dirty, step_name="build_validation_prompt"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC8 (restated, MAJOR-C) — the resume/`_retry_nonce` explanation lives in the
# REGISTRY ENTRY's docstring, and therefore in the GENERATED ERROR_CODES.md
# ═════════════════════════════════════════════════════════════════════════


def test_ac8_resume_and_retry_nonce_explanation_lives_in_the_registry_entry(tmp_path, monkeypatch):
    """AC8 restated. Round 3 asserted this by grepping prose out of
    `ERROR_CODES.md` — which cannot hold: that file is GENERATED
    (`test_error_codes.py::test_ac4` pins it byte-equal to `render_markdown()`,
    and the renderer emits only `## <family>` blocks over the registry). A
    hand-written resume section would make that sibling test red.

    So the explanation must live where the generator can see it: the docstring
    of the new routing code in `error_codes.ERROR_CODES` (a `dict[str, str]`;
    the value IS the docstring — `test_ac5` requires it meaningful). This AC
    reads the code off the REAL routing result, so it cannot be satisfied by
    documenting some unrelated code, and then requires the docstring to name
    `_retry_nonce` and state the replay consequence.

    Pre-GREEN FAIL: no routing result exists (the guard returns the terminal
    refusal), so the routing-contract assertion fires first; and no registry
    entry mentions `_retry_nonce` anywhere today."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac8", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    _assert_routes_to_green_verification("AC8", result)
    code = result.error_code

    doc = ERROR_CODES.get(code)
    assert isinstance(doc, str) and doc, (
        f"AC8: the routing code {code!r} has no registry docstring to carry the "
        f"resume explanation — the generated `ERROR_CODES.md` renders registry "
        f"values only, so an entry-less code documents nothing"
    )
    assert "_retry_nonce" in doc, (
        f"AC8: expected the {code!r} docstring to NAME `_retry_nonce` — its only "
        f"documentation anywhere today is a test "
        f"(`tests/test_gh1050_retry_nonce_sentinel.py:162,186`); actual "
        f"docstring={doc!r}"
    )
    assert re.search(r"replay|replayed|cached", doc, re.IGNORECASE), (
        f"AC8: expected the {code!r} docstring to state the replay consequence "
        f"(re-running a recoverable error WITHOUT bumping the nonce replays the "
        f"cached step result, so the fix looks inert — the ctx hash feeds the "
        f"sentinel filename, `lib/step_sentinel.py:68/:107`); actual "
        f"docstring={doc!r}"
    )
    rendered = error_codes.render_markdown()
    assert doc in rendered and code in rendered, (
        f"AC8: the explanation must reach the shipped catalogue through the "
        f"GENERATOR; `render_markdown()` output carries code={code in rendered} "
        f"docstring={doc in rendered}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC9 — the rescue is never silent
# ═════════════════════════════════════════════════════════════════════════


def test_ac9_recovery_emits_orphan_green_recovery_routed_with_paths_and_run_id(tmp_path, monkeypatch):
    """AC9: the recovery emits `orphan_green_recovery_routed` naming the paths
    AND the run_id. A silent rescue is unacceptable for something that changes
    where the pipeline resumes and therefore what gets committed.

    Pre-GREEN FAIL: this event name does not exist anywhere in production."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac9", monkeypatch, manifest=manifest, dirty_paths=manifest)

    p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    routed = stage.events.of(RECOVERY_EVENT)
    assert len(routed) == 1, (
        f"AC9: expected exactly 1 {RECOVERY_EVENT!r} event; actual events seen="
        f"{stage.events.types()!r}"
    )
    payload = routed[0]["payload"]
    flat = json.dumps(payload, default=str)
    assert "src/module.py" in flat, (
        f"AC9: expected the routed paths named in the event payload; actual "
        f"payload={payload!r}"
    )
    assert stage.run_id in flat, (
        f"AC9: expected the run_id {stage.run_id!r} named in the event payload "
        f"(whose work is being adopted is the whole question); actual "
        f"payload={payload!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC25 — the routing result carries a DECLARED error code (§1n)
# ═════════════════════════════════════════════════════════════════════════


def test_ac25_routing_result_carries_a_declared_error_code(tmp_path, monkeypatch):
    """AC25 (§1n): the recovery result travels as `status='error'` with
    `recoverable=True`, so its `error_code` is what `build_stuck_report`
    (`engine.py:599-608`), `emit_incident` (:523) and the dispatcher read. An
    undeclared ad-hoc string — or `None` — leaves those consumers with an
    unenumerated token.

    Round 4 de-duplication: the "is it DECLARED" half moved to AC29 (which owns
    registry membership plus proof that `ERROR_CODES.md` was REGENERATED). What
    remains here is the consumer-visible SHAPE: a non-empty string that is
    distinguishable from the terminal refusal — a recoverable route and a
    terminal refusal must never look alike to `build_stuck_report`.

    Pre-GREEN FAIL: no routing result exists — the guard returns the terminal
    refusal, so the routing contract assertion fires first."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac25", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    _assert_routes_to_green_verification("AC25", result)

    code = result.error_code
    assert isinstance(code, str) and code, (
        f"AC25: the routing result must carry an error_code the stuck-report and "
        f"the dispatcher can read; actual {code!r} "
        f"({type(code).__name__}) — data={result.data!r}"
    )
    assert code != "E_RED_WORKTREE_DIRTY", (
        f"AC25: the recoverable routing result must not reuse the TERMINAL "
        f"refusal code — the two verdicts would be indistinguishable to every "
        f"consumer; actual {code!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC29 — the routing code is DECLARED, and ERROR_CODES.md was REGENERATED
# ═════════════════════════════════════════════════════════════════════════


def test_ac29_routing_error_code_is_declared_and_error_codes_md_was_regenerated(tmp_path, monkeypatch):
    """AC29 (MAJOR-C). Two legs, both about the same fact — the catalogue is
    generated, not written.

      (a) the code the guard really returns is a member of
          `error_codes.ERROR_CODES`. This is what `error_codes.check()` /
          `test_error_codes.py::test_ac1` harvest against the production tree,
          so a routing code that skips registration turns a SIBLING test red.
      (b) `ERROR_CODES.md` is byte-equal to `render_markdown()` — the exact
          comparison `test_error_codes.py::test_ac4` makes
          (`read_bytes() == render_markdown().encode()`). After adding an entry
          that equality holds ONLY if the doc was regenerated; a hand-edited
          catalogue, or a registry addition with no regeneration, fails it.

    Leg (b) passes today (the committed doc matches the current registry) — it
    is the pin that catches a GREEN which adds the code and hand-edits the
    markdown. Leg (a) fails today: there is no routing result at all.

    §1v correction, recorded: round 3 said "no error code is added"; round 4
    corrects that to "one code is added, declared in the registry"."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac29", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    _assert_routes_to_green_verification("AC29", result)
    code = result.error_code

    assert code in ERROR_CODES, (
        f"AC29 leg (a): expected a DECLARED error code (§1n) — {code!r} is not "
        f"in the `error_codes.ERROR_CODES` registry ({len(ERROR_CODES)} codes). "
        f"`error_codes.check()` harvests production literals, so an undeclared "
        f"routing code makes `test_error_codes.py::test_ac1` red too."
    )

    committed = (ENGINE_ROOT / "bytedigger_engine/ERROR_CODES.md").read_bytes()
    rendered = error_codes.render_markdown().encode()
    assert committed == rendered, (
        f"AC29 leg (b): `ERROR_CODES.md` is GENERATED — it must be byte-equal to "
        f"`render_markdown()` (the comparison `test_error_codes.py::test_ac4` "
        f"makes). It is not: committed={len(committed)} bytes, "
        f"rendered={len(rendered)} bytes. Regenerate it "
        f"(`python3 error_codes.py --markdown`); never hand-edit it."
    )
    assert code.encode() in committed, (
        f"AC29 leg (b): the shipped catalogue must actually carry {code!r} — "
        f"byte-equality alone would also hold if the code were never rendered"
    )
