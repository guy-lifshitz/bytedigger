"""RED tests for hal#1018 — the orphan-GREEN recovery must resolve the cycle
it is resuming INTO, not the cycle the loop counter happens to carry.

Frozen spec:
SHARED/memory/Decisions/2026-09-26_GH1018_orphan_green_cycle_resolution_spec.md

The defect, restated so the assertions below are readable: an auto-resume
re-enters `validation_cycle_loop` at its FIRST iteration, so `prev.data["cycle"]`
is 1 while the completed GREEN's sentinel is keyed at cycle 2.
`_orphan_green_recovery_result` (`phase_5_implement.py:3071-3078`) reads the
sentinel at `prev_data["cycle"]` with `read_step_sentinel`, an exact-filename
read -> None -> recovery declines -> terminal `E_RED_WORKTREE_DIRTY`
(live run `forge-1790401118-1896`). Second, independent mismatch on the same
entry: `_read_green_complete_resume_record(scratchpad, prev_data["red_commit_sha"])`
(`:3081`) keys the marker on cycle 1's RED sha while the marker holds cycle 2's.

Spec v2 (frozen after gate verdict 1 REJECTED) makes it three linked defects,
and this file covers all of them:

  * **Defect 1 / P1** — the evidence is never written on the path that needs it.
    `_persist_green_complete_resume` has exactly ONE call site today (`:3307`,
    block 5 of `_verify_red_fails_mechanically`), reached only when the RED suite
    PASSES; the dirty-tree guard in block 2 returns terminal before it whenever an
    allowlisted production path is dirty — which an uncommitted GREEN always is.
    So in the reported state there is NO marker at all. P1 writes it in
    `_write_green_artifact`, on `GREEN_COMPLETE` only, through a helper that
    never raises and reports every degrade branch
    (`green_complete_resume_persist_failed`). AC13, AC21n.
  * **Defect 2 / P2** — cycle keying, plus double linkage (sidecar AND the
    sentinel payload's own sha, since the sidecar is merge-on-write over a
    possibly shared scratchpad and carries no run identity). AC1-AC11, AC14-AC17n,
    AC20n, AC22n.
  * **Defect 3 / P3** — the routed result cannot pass `engine.py:563-574`
    (`_MAX_VALIDATION_CYCLES = 2`): the adopted payload's `cycle_count` is the
    GREEN cycle's, so the run terminates on the routing code instead of resuming.
    P3 sets `cycle_count = max(resolved_cycle - 1, 0)` AND `gate_budget_ok = True`
    — the second half is what a resolved cycle 3 needs, bounded by
    `_GATE_BUDGET_HARD_BACKSTOP`. AC12, AC12b.
  * **P4** — `_orphan_green_recovery_result` is the shared predicate of BOTH
    raise sites, so every part applies to `build_validation_prompt` too. AC18n.

Design (spec "Design"): cycle resolution becomes forward-only and
evidence-linked. Candidates are `{current_cycle} u {c >= current_cycle from
red-cycle-shas.json}`, tried DESCENDING; a LATER cycle qualifies only when its
sentinel exists AND `_read_red_cycle_sha(scratchpad, c) == marker sha`
(fail-closed on a missing/unreadable sidecar). `c < current_cycle` is NEVER a
candidate — that is AC15 of the sibling suite, which stays true. The routed
`red_commit_sha` becomes the MARKER's sha so the downstream `_invoke_green_llm`
marker read (`:8298`) matches and GREEN is skipped instead of re-invoked.

UUT, never mocked (§1l):
  * `phase_5_implement._verify_red_fails_mechanically` — through the real
    `_verify_red_dirty_tree_guard` -> `_orphan_green_recovery_result` path.
  * `phase_5_implement._build_validation_prompt` — raise site 2 (AC18n).
  * `phase_5_implement._write_green_artifact` — the P1 writer (AC13).
  * `phase_5_implement._invoke_green_llm` — AC10, the Point->Host->Test leg
    (§1y): the routed sha must actually make the GREEN-skip fire.
  * `engine.WorkflowEngine._execute_steps` — AC12 drives the REAL retry branch
    (`engine.py:563-574`), entered at index 0, so the cap decision is made by
    production code and not by a hand-picked `start_step`.
The only external side effect that is ever stood in for is
`invoke_llm_subprocess` — billed, and never the unit under test; the stand-in
fails loudly so "no LLM was reached" is an assertion, not an assumption.
Real git repos, real commits, real dirty trees, real `red-cycle-shas.json`,
real `resume/green-complete-resume.json`, real run-keyed sentinels written by
`lib.step_sentinel.write_step_sentinel` and `_persist_red_cycle_sha` /
`_persist_green_complete_resume` — no filename scheme and no digest format is
ever guessed.

Fixture discipline:
  * §1i — the contested state (the sidecar, the marker, the sentinels, the dirty
    tree) is PRE-STAGED deterministically before the UUT is entered. Nothing
    races, nothing sleeps, no timing is assumed.
  * §1q — no not-yet-existing symbol is imported at module level; the new
    behaviour only appears inside assert-time comparisons, so this file collects
    cleanly today and fails at ASSERT time.
  * Ordering is load-bearing: the GREEN content is in the tree when
    `_persist_green_complete_resume` captures its per-path sha256, and any
    post-GREEN mutation happens strictly AFTER that call.
  * Every ctx sets `task_description` AND `decision_doc`, so
    `step_sentinel.compute_ctx_hash` is not None and every sentinel really
    carries its `_h<hash12>` segment.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext, WorkflowDefinition  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.lib import step_sentinel as step_sentinel  # noqa: E402


GREEN_STEP = "invoke_green_llm"
PHASE_5_WORKFLOW_NAME = "phase_5_implement"
RECOVERY_EVENT = "orphan_green_recovery_routed"
ROUTE_TARGET = "write_green_artifact"
ROUTED_CODE = "E_GREEN_ORPHAN_RECOVERY_ROUTED"

GREEN_BODY = "def impl():\n    return 42\n"
POST_GREEN_EDIT_BODY = "def impl():\n    return 43  # edited AFTER the GREEN record\n"

RED_TEST_BODY = (
    "import pathlib\n"
    "\n"
    "\n"
    "def test_x():\n"
    "    assert (pathlib.Path(__file__).resolve().parents[1]\n"
    "            / 'src' / 'module.py').is_file()\n"
)

# A sha-shaped string that is NOT any commit in the fixture repo — used for the
# AC7 "sidecar exists but does not link" leg.
UNLINKED_SHA = "0" * 40
# The RED sha of a DIFFERENT run, as it would appear in a marker and a sidecar
# entry left behind in a shared scratchpad (AC14).
FOREIGN_MARKER_SHA = "f" * 40

LOOP_STEP = "validation_cycle_loop"
SITE_2_STEP = "build_validation_prompt"

# The ONE new emit name P1 adds. There is deliberately no success emit: the
# helper is silent on every healthy "nothing to record" state (gate off, no
# red_commit_sha, empty detector manifest) and speaks only when a write was
# ATTEMPTED and failed. AC21n pins the alarm, AC23n pins the silence.
PERSIST_FAILED_EVENT = "green_complete_resume_persist_failed"

GREEN_COMPLETE_RAW = "GREEN COMPLETE\n\n(fixture: gh1018 — GREEN finished, nothing committed yet)"
GREEN_BLOCKED_RAW = "I could not implement this.\n\nGREEN BLOCKED\n\n(fixture: gh1018)"
GREEN_NO_MARKER_RAW = "I wrote some code and then the output was truncated mid-sen"


# ═════════════════════════════════════════════════════════════════════════
# real-git harness (local copies — no existing test file is modified)
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_spec(scratchpad: Path, files: list) -> str:
    lines = ["# fixture spec (gh1018)\n", "\n## Files\n"]
    for f in files:
        lines.append(f"- `{f}`\n")
    p = scratchpad / "specs" / "build-spec.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)


def _make_ctx(scratchpad: Path, repo: Path, spec_path: str, org_extra: "dict | None" = None) -> WorkflowContext:
    """Explicit `git_cwd` — non-ambient (GH1220). `task_description` +
    `decision_doc` are set deliberately: without at least one of them
    `step_sentinel.compute_ctx_hash` returns None and no `_h<hash>` sentinel is
    ever produced, which would make the ctx-hash ownership class (AC6b)
    invisible."""
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
            "task_description": "gh1018 orphan GREEN cycle resolution fixture",
            "decision_doc": spec_path,
            **(org_extra or {}),
        },
        question="q", session_id="test-1018", persona="hal",
        framework=None, domain=None,
    )


class _Events:
    """Collect telemetry from the two seams that actually carry events here.

    `record` is installed over phase_5's own `_emit_safe` module attribute, so it
    observes phase_5's REAL emit call sites (arguments and all) — that is the
    only seam phase_5 emits through, and patching the attribute is what lets the
    payload be read back. The jsonl leg carries the ENGINE's emits (`engine._emit`
    writes to the `EventLog` this path was opened with), which is what AC12 reads
    for `iteration_started`. No claim is made that phase_5's emits reach the
    jsonl — with `_emit_safe` captured they do not."""

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
        out = []
        for e in self.all():
            if e["type"] == event_type:
                out.append(e)
            elif event_type in str(e["payload"].get("shadowed_event") or ""):
                out.append(e)
        return out


class _Stage:
    def __init__(self, repo, red_shas, ctx, prev, scratchpad, events, run_id, spec_path,
                 head_before, marker_sha, marker_bytes, sidecar_bytes,
                 sentinel_rid=None, sentinel_ctx_hash=None, log_path=None):
        self.sentinel_rid = sentinel_rid
        self.sentinel_ctx_hash = sentinel_ctx_hash
        self.log_path = log_path
        self.repo = repo
        self.red_shas = red_shas            # {cycle: sha}
        self.ctx = ctx
        self.prev = prev
        self.scratchpad = scratchpad
        self.events = events
        self.run_id = run_id
        self.spec_path = spec_path
        self.head_before = head_before
        self.marker_sha = marker_sha
        self.marker_bytes = marker_bytes
        self.sidecar_bytes = sidecar_bytes

    @property
    def marker_path(self) -> Path:
        return Path(self.scratchpad) / p5.GREEN_COMPLETE_RESUME_RELPATH

    @property
    def sidecar_path(self) -> Path:
        return Path(self.scratchpad) / p5.RED_CYCLE_SHAS_RELPATH


def _green_sentinel_payload(
    red_sha: str,
    manifest: list,
    spec_path: str,
    cycle: int,
    scratchpad: Path,
    repo: Path,
    raw_response: str = "GREEN COMPLETE\n\n(fixture: gh1018 orphan GREEN)",
) -> dict:
    """Mirror the shape `_invoke_green_llm` returns, so whichever key the
    recovery forwards to `write_green_artifact` is present.

    `red_commit_sha` is this cycle's RED sha — the run-authenticated half of the
    P2 linkage (the sidecar is merge-on-write over a shared scratchpad and
    carries no run identity, so the sentinel payload's own sha is what ties the
    marker to THIS run).

    `fixture_sentinel_cycle` is a fixture-only discriminator: it records WHICH
    cycle's sentinel payload was adopted. AC11/AC15n need it — `cycle` alone
    cannot tell "resolved 3" from "resolved 2 and relabelled"."""
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
        "fixture_sentinel_cycle": cycle,
    }


def _persist_green_record(scratchpad, red_sha: str, paths: list, git_cwd: str, ctx) -> None:
    """Call the PRODUCTION marker writer `_persist_green_complete_resume`
    (`:1813`) without guessing its signature — so the digest format is never
    guessed either. Fails loudly if the GREEN grows a required parameter this
    harness cannot fill."""
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
    n_red_cycles: int = 2,
    prev_cycle: int = 1,
    prev_red_cycle: int = 1,
    sentinel_cycles: tuple = (2,),
    marker_red_cycle: "int | None" = 2,
    marker_sha_literal: "str | None" = None,
    sidecar_cycles: tuple = (1, 2),
    sidecar_sha_overrides: "dict | None" = None,
    hand_written_sidecar: "dict | None" = None,
    run_id: str = "run1018cafe",
    sentinel_run_id: str = "same",
    ctx_org_extra: "dict | None" = None,
    sentinel_ctx_org_extra: "dict | None" = None,
    post_green_edit: "list | None" = None,
) -> _Stage:
    """Pre-stage (§1i) a real repo with `n_red_cycles` landed RED commits, a real
    per-cycle sidecar, real run-keyed GREEN sentinels, a real GREEN completion
    marker, and a genuinely dirty tree on `dirty_paths`.

    Ordering (load-bearing):
      1. every RED cycle's commit lands -> `red_shas = {cycle: sha}`,
      2. the sidecar `red-cycle-shas.json` is written by the PRODUCTION writer
         `_persist_red_cycle_sha` for each cycle in `sidecar_cycles`
         (`sidecar_sha_overrides` replaces a cycle's sha to model a sidecar that
         does not LINK — AC7),
      3. the orphaned GREEN content is written to `dirty_paths`,
      4. a run-keyed `invoke_green_llm` sentinel is written for each cycle in
         `sentinel_cycles` by `lib.step_sentinel.write_step_sentinel`,
      5. the GREEN completion marker is persisted by production code for
         `marker_red_cycle`'s RED sha WHILE the tree holds the GREEN content —
         this is the moment the per-path sha256 is captured,
      6. ONLY THEN `post_green_edit` rewrites the content (AC4).

    `prev` carries `cycle=prev_cycle` and `red_commit_sha=red_shas[prev_red_cycle]`
    — for AC1 that is the auto-resume shape: cycle 1 / sha1 while the real work
    is cycle 2 / sha2.
    """
    monkeypatch.setenv("HAL_DIRTY_TREE_GUARD", "1")
    monkeypatch.setenv("HAL_GREEN_COMPLETE_RESUME_GATE", "1")

    repo = Path(str((tmp_path / f"repo_{name}").resolve()))
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")

    # (1) one landed RED commit per cycle — distinct content, so each cycle
    # really has its own sha.
    red_shas: dict = {}
    for cycle in range(1, n_red_cycles + 1):
        red_shas[cycle] = _commit_file(
            repo, "tests/test_x.py",
            RED_TEST_BODY + f"\n# red cycle {cycle}\n",
            f"build: red cycle {cycle} tests",
        )
    assert len(set(red_shas.values())) == n_red_cycles, (
        f"fixture precondition FAILED for {name}: each RED cycle must have a "
        f"DISTINCT commit sha (the whole defect is cycle<->sha keying); actual "
        f"{red_shas!r}"
    )

    scratchpad = tmp_path / f"scratch_{name}"
    scratchpad.mkdir(parents=True, exist_ok=True)
    spec_path = _write_spec(scratchpad, sorted(set(list(manifest) + list(dirty_paths))))
    ctx = _make_ctx(scratchpad, repo, spec_path, ctx_org_extra)

    # (2) the per-cycle sidecar. Written by the PRODUCTION writer unless
    # `hand_written_sidecar` stages a hostile/legacy artifact verbatim (AC14
    # leg 2, AC17n) — a hand-edited sidecar is exactly the shape the linkage
    # must survive, and no production writer produces it.
    if hand_written_sidecar is not None:
        sidecar_file = Path(scratchpad) / p5.RED_CYCLE_SHAS_RELPATH
        sidecar_file.parent.mkdir(parents=True, exist_ok=True)
        sidecar_file.write_text(json.dumps(hand_written_sidecar, sort_keys=True))
    else:
        overrides = dict(sidecar_sha_overrides or {})
        for cycle in sidecar_cycles:
            p5._persist_red_cycle_sha(scratchpad, cycle, overrides.get(cycle, red_shas[cycle]))

    # (3) the orphaned GREEN as it stood when GREEN completed.
    for rel in dirty_paths:
        _write_file(repo, rel, f"# GREEN implementation for {rel}\n{GREEN_BODY}")

    (scratchpad / "tests").mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews").mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews" / "validation.md").write_text(
        "# fixture validation doc\n\nVERDICT: PASS\n", encoding="utf-8"
    )
    (repo / "tests" / "build-red-output.log").write_text("FAILED test_x\n", encoding="utf-8")

    # (4) the run-keyed GREEN sentinels.
    rid = run_id if sentinel_run_id == "same" else sentinel_run_id
    hash_ctx = ctx
    if sentinel_ctx_org_extra:
        hash_ctx = replace(ctx, org_config={**ctx.org_config, **sentinel_ctx_org_extra})
    ctx_hash = step_sentinel.compute_ctx_hash(hash_ctx)
    assert ctx_hash, (
        f"fixture precondition FAILED for {name}: compute_ctx_hash returned "
        f"{ctx_hash!r} — without task_description/decision_doc no `_h<hash>` "
        f"sentinel is produced and the ctx-hash ownership class is invisible"
    )
    for cycle in sentinel_cycles:
        step_sentinel.write_step_sentinel(
            scratchpad, GREEN_STEP, cycle,
            _green_sentinel_payload(
                red_shas[cycle], manifest, spec_path, cycle, scratchpad, repo
            ),
            rid, ctx_hash, PHASE_5_WORKFLOW_NAME,
        )
    for cycle in sentinel_cycles:
        written = sorted(p.name for p in (scratchpad / "resume").glob("*.json"))
        assert any(f"_done_c{cycle}_" in n and "_h" in n for n in written), (
            f"fixture precondition FAILED for {name}: expected a real "
            f"`..._done_c{cycle}_r<run>_h<hash12>.json` sentinel on disk; actual "
            f"resume dir={written!r}"
        )

    # (5) the GREEN completion marker, keyed to marker_red_cycle's RED sha —
    # or to `marker_sha_literal`, a sha this run never produced (AC14: the
    # marker a FOREIGN run left in a shared scratchpad).
    marker_sha: "str | None"
    if marker_sha_literal is not None:
        marker_sha = marker_sha_literal
    else:
        marker_sha = None if marker_red_cycle is None else red_shas[marker_red_cycle]
    if marker_sha is not None:
        _persist_green_record(scratchpad, marker_sha, list(manifest), str(repo), ctx)

    # (6) post-GREEN mutations — strictly AFTER the record was captured.
    for rel in (post_green_edit or []):
        _write_file(repo, rel, f"# GREEN implementation for {rel}\n{POST_GREEN_EDIT_BODY}")

    log_path = tmp_path / f"events_{name}.jsonl"
    events = _Events(log_path)
    monkeypatch.setattr(p5, "_emit_safe", events.record)
    telemetry_ctx.set_current_run(
        event_log=EventLog(log_path), run_id=run_id,
        step_name="verify_red_fails_mechanically", phase=PHASE_5_WORKFLOW_NAME,
        cycle=prev_cycle,
    )
    telemetry_ctx.set_invocation_run_id(run_id)

    prev = StepResult(
        status="ok",
        data={
            "red_test_paths": ["tests/test_x.py"],
            "red_log_path": "tests/build-red-output.log",
            "spec_path": spec_path,
            "red_commit_sha": red_shas[prev_red_cycle],
            "cycle": prev_cycle,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )

    porcelain = _porcelain(repo)
    for rel in dirty_paths:
        assert rel in porcelain, (
            f"fixture precondition FAILED for {name}: expected {rel!r} dirty in "
            f"`git status --porcelain`; actual output={porcelain!r}"
        )

    marker_path = Path(scratchpad) / p5.GREEN_COMPLETE_RESUME_RELPATH
    sidecar_path = Path(scratchpad) / p5.RED_CYCLE_SHAS_RELPATH
    return _Stage(
        repo, red_shas, ctx, prev, scratchpad, events, run_id, spec_path,
        _head_sha(repo), marker_sha,
        marker_path.read_bytes() if marker_path.is_file() else None,
        sidecar_path.read_bytes() if sidecar_path.is_file() else None,
        sentinel_rid=rid, sentinel_ctx_hash=ctx_hash, log_path=log_path,
    )


def _sentinel_payload(stage: _Stage, cycle: int) -> "dict | None":
    """The payload really on disk for `cycle`, read back with the PRODUCTION
    reader under this run's key tuple."""
    return step_sentinel.read_step_sentinel(
        Path(stage.scratchpad), GREEN_STEP, cycle, stage.sentinel_rid,
        stage.sentinel_ctx_hash, PHASE_5_WORKFLOW_NAME,
    )


def _assert_payload_linkage(ac: str, stage: _Stage, cycle: int, *, links: bool) -> None:
    """P2 requires the sentinel PAYLOAD's own `red_commit_sha` to equal the
    marker's — the run-authenticated half of the linkage. Every stage states
    explicitly whether that half holds, so no AC is ambiguous about which half
    it is pinning."""
    payload = _sentinel_payload(stage, cycle)
    assert isinstance(payload, dict), (
        f"{ac} fixture precondition FAILED: expected a readable cycle-{cycle} "
        f"sentinel payload; actual {payload!r} (resume dir="
        f"{sorted(p.name for p in (stage.scratchpad / 'resume').glob('*.json'))!r})"
    )
    actual = payload.get("red_commit_sha")
    if links:
        assert actual == stage.marker_sha, (
            f"{ac} fixture precondition FAILED: the cycle-{cycle} sentinel "
            f"PAYLOAD sha must equal the marker's (the run-authenticated half of "
            f"the linkage); actual payload sha={actual!r} marker={stage.marker_sha!r}"
        )
    else:
        assert actual != stage.marker_sha, (
            f"{ac} fixture precondition FAILED: the cycle-{cycle} sentinel "
            f"PAYLOAD sha must NOT equal the marker's for this leg; actual "
            f"payload sha={actual!r} marker={stage.marker_sha!r}"
        )


# ═════════════════════════════════════════════════════════════════════════
# shared assertions
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


def _describe(result) -> str:
    return (
        f"status={getattr(result, 'status', None)!r} "
        f"error_code={getattr(result, 'error_code', None)!r} "
        f"recoverable={getattr(result, 'recoverable', None)!r} "
        f"error={getattr(result, 'error', '')!r} "
        f"data={getattr(result, 'data', None)!r}"
    )


def _retry_from_step(result) -> "int | None":
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    idx = data.get("retry_from_step")
    if isinstance(idx, bool) or not isinstance(idx, int):
        return None
    return idx


def _assert_routed(ac: str, result) -> dict:
    """The routing contract the engine actually consumes (`engine.py:548,
    556-560`) plus the declared code — asserted whole, with the real StepResult
    printed on failure (§1q)."""
    assert result.status == "error", (
        f"{ac}: the recovery travels as status=='error' with recoverable=True "
        f"(engine.py:548 only inspects an error result); actual {_describe(result)}"
    )
    assert result.error_code == ROUTED_CODE, (
        f"{ac}: expected error_code=={ROUTED_CODE!r} — the orphan GREEN of a "
        f"LATER cycle than the re-entered loop believes it is in must route "
        f"forward, not die on the dirty-tree refusal; actual {_describe(result)}"
    )
    assert result.recoverable is True, (
        f"{ac}: expected recoverable is True (engine.py:556 re-entry gate); "
        f"actual {_describe(result)}"
    )
    assert isinstance(result.data, dict), (
        f"{ac}: engine.py:558 requires `isinstance(result.data, dict)`; actual "
        f"{_describe(result)}"
    )
    idx = _retry_from_step(result)
    expected = _step_index(ROUTE_TARGET)
    assert idx == expected, (
        f"{ac}: expected an INT `retry_from_step` == index of {ROUTE_TARGET!r} "
        f"({expected}, read from the real `phase_5_implement_workflow()`); actual "
        f"{result.data.get('retry_from_step')!r} — {_describe(result)}"
    )
    return result.data


def _routed_event(ac: str, stage: _Stage) -> dict:
    routed = stage.events.of(RECOVERY_EVENT)
    assert len(routed) == 1, (
        f"{ac}: expected exactly 1 {RECOVERY_EVENT!r} event through the real "
        f"emit path; actual events seen={stage.events.types()!r}"
    )
    return routed[0]["payload"]


def _assert_cycle_source(ac: str, stage: _Stage, result, expected: str) -> None:
    """`cycle_source` is spec'd onto the `orphan_green_recovery_routed` payload
    (Design §4); a GREEN that also carries it in `data` is accepted, but it must
    appear on the event."""
    payload = _routed_event(ac, stage)
    actual = payload.get("cycle_source")
    if actual is None and isinstance(getattr(result, "data", None), dict):
        actual = result.data.get("cycle_source")
    assert actual == expected, (
        f"{ac}: expected cycle_source=={expected!r} on the {RECOVERY_EVENT!r} "
        f"payload (Design §4) — how the cycle was resolved is the whole question; "
        f"actual cycle_source={actual!r}, full payload={payload!r}, "
        f"result {_describe(result)}"
    )


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


def _assert_terminal_refusal(ac: str, stage: _Stage, result, expected_violations: list) -> None:
    """Guard intact: exact code, recoverable False, wording from the ONE
    production source, no commit, no recovery event — so an OVER-rescuing GREEN
    cannot pass."""
    step_name = "verify_red_fails_mechanically"
    assert result.status == "error", (
        f"{ac}: expected the terminal dirty-tree refusal; actual {_describe(result)}"
    )
    assert result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"{ac}: expected error_code=='E_RED_WORKTREE_DIRTY'; actual {_describe(result)}"
    )
    assert result.recoverable is False, (
        f"{ac}: expected recoverable is False (terminal contract unchanged); "
        f"actual {_describe(result)}"
    )
    assert _retry_from_step(result) is None, (
        f"{ac}: a terminal refusal must declare NO re-entry target; actual "
        f"{_describe(result)}"
    )
    expected_msg = _dirty_tree_block_message(step_name, sorted(expected_violations))
    assert (getattr(result, "error", "") or "") == expected_msg, (
        f"{ac}: expected the refusal WORDING UNCHANGED (the single source, "
        f"`_dirty_tree_block_message`) over violations "
        f"{sorted(expected_violations)!r};\nexpected={expected_msg!r}\n"
        f"actual={getattr(result, 'error', '')!r}"
    )
    assert _head_sha(stage.repo) == stage.head_before, (
        f"{ac}: expected NO commit on a terminal refusal; actual HEAD moved "
        f"{stage.head_before!r} -> {_head_sha(stage.repo)!r}"
    )
    assert stage.events.of(RECOVERY_EVENT) == [], (
        f"{ac}: expected NO {RECOVERY_EVENT!r} event on a terminal refusal (an "
        f"over-rescuing GREEN must not pass this AC); actual events="
        f"{stage.events.types()!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC1 — the reported blocker: auto-resume at cycle 1, GREEN at cycle 2
# ═════════════════════════════════════════════════════════════════════════


def test_ac1_auto_resume_at_cycle_1_adopts_the_cycle_2_green(tmp_path, monkeypatch):
    """AC1 (§1ab auto-resume leg). Sentinel at cycle 2, sidecar
    `{"1": sha1, "2": sha2}`, marker `red_commit_sha=sha2` with digests matching
    the dirty paths, and the loop re-entered with `prev.data["cycle"]=1` /
    `red_commit_sha=sha1` — the exact shape of live run
    `forge-1790401118-1896`.

    The recovery must resolve the cycle from PERSISTED evidence (the sidecar
    links cycle 2 to the marker's sha) rather than from the loop counter, route
    to `write_green_artifact`, and forward the MARKER's sha so the downstream
    GREEN-skip can fire (AC10).

    FAILS TODAY: `_orphan_green_recovery_result` takes `cycle = prev_data["cycle"]`
    (`:3071`) = 1 and `read_step_sentinel` is an exact-filename read (`:3075`),
    so the cycle-2 sentinel is invisible -> None -> `E_RED_WORKTREE_DIRTY`,
    `recoverable=False`."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac1", monkeypatch, manifest=manifest, dirty_paths=manifest)
    sha1, sha2 = stage.red_shas[1], stage.red_shas[2]
    assert stage.prev.data["red_commit_sha"] == sha1 and stage.prev.data["cycle"] == 1, (
        f"AC1 fixture precondition FAILED: the auto-resume entry must carry "
        f"cycle 1 / sha1; actual prev.data={stage.prev.data!r}"
    )
    assert stage.marker_sha == sha2, (
        f"AC1 fixture precondition FAILED: the marker must hold CYCLE 2's RED "
        f"sha; actual marker_sha={stage.marker_sha!r} sha2={sha2!r}"
    )
    assert p5._read_red_cycle_sha(stage.scratchpad, 2) == sha2, (
        f"AC1 fixture precondition FAILED: the sidecar must link cycle 2 -> "
        f"{sha2!r} (the linkage evidence the resolution needs); actual sidecar="
        f"{stage.sidecar_path.read_text()!r}"
    )
    _assert_payload_linkage("AC1", stage, 2, links=True)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    data = _assert_routed("AC1", result)
    assert data.get("red_commit_sha") == sha2, (
        f"AC1: the routed `red_commit_sha` must be the MARKER's sha ({sha2!r}, "
        f"cycle 2) and NOT the stale loop-counter sha ({sha1!r}, cycle 1) — "
        f"otherwise `_invoke_green_llm`'s marker read (`:8298`) misses and GREEN "
        f"is re-invoked; actual {_describe(result)}"
    )
    assert data.get("cycle") == 2, (
        f"AC1: the routed `cycle` must be the RESOLVED cycle 2, not the "
        f"re-entered loop's 1; actual {_describe(result)}"
    )
    assert data.get("fixture_sentinel_cycle") == 2, (
        f"AC1: the adopted payload must be CYCLE 2's sentinel (fixture "
        f"discriminator); actual fixture_sentinel_cycle="
        f"{data.get('fixture_sentinel_cycle')!r} — {_describe(result)}"
    )
    assert _head_sha(stage.repo) == stage.head_before, (
        f"AC1: the guard must not commit anything — recovery restores the "
        f"pipeline's position, it never buys a commit; actual HEAD moved "
        f"{stage.head_before!r} -> {_head_sha(stage.repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC2 — the AC15 direction is preserved: an EARLIER cycle is never ours
# ═════════════════════════════════════════════════════════════════════════


def test_ac2_sentinel_from_an_earlier_cycle_than_the_loop_stays_terminal(tmp_path, monkeypatch):
    """AC2: the run is at cycle 2 and the only sentinel is at cycle 1 — the
    opposite direction from AC1, and the one `tests/test_gh1626d_orphan_green_recovery.py:1118`
    (AC15) pins. Resolution is FORWARD-ONLY: `c < current_cycle` is never a
    candidate, no matter what the sidecar links. -> terminal
    `E_RED_WORKTREE_DIRTY`, `recoverable=False`, wording unchanged.

    PASSES TODAY (nothing is adopted); post-GREEN it is the pin that stops the
    descending candidate walk from reaching backwards."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac2", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2,
        sentinel_cycles=(1,), marker_red_cycle=1, sidecar_cycles=(1, 2),
    )
    names = sorted(p.name for p in (stage.scratchpad / "resume").glob("*.json"))
    assert any("_done_c1_" in n for n in names) and not any("_done_c2_" in n for n in names), (
        f"AC2 fixture precondition FAILED: expected a cycle-1 sentinel and NO "
        f"cycle-2 sentinel while the run is at cycle 2; actual resume dir={names!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC2", stage, result, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC3 — the fresh / same-cycle entry is unchanged, bit for bit
# ═════════════════════════════════════════════════════════════════════════


def test_ac3_same_cycle_entry_routes_exactly_as_today(tmp_path, monkeypatch):
    """AC3 (§1ab fresh / in-phase leg): current cycle 1, sentinel cycle 1, and
    `prev_data["red_commit_sha"]` equals the marker's sha — today's behaviour,
    which must survive bit-for-bit, with `cycle_source == "prev_data"`.

    The routing half PASSES TODAY. The `cycle_source` assertion FAILS TODAY: the
    `orphan_green_recovery_routed` payload (`:3101`) carries no `cycle_source`
    key at all, so this AC also pins that the new field distinguishes the two
    resolution routes instead of being hardcoded to the sidecar one."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac3", monkeypatch, manifest=manifest, dirty_paths=manifest,
        n_red_cycles=1, prev_cycle=1, prev_red_cycle=1,
        sentinel_cycles=(1,), marker_red_cycle=1, sidecar_cycles=(1,),
    )
    assert stage.prev.data["red_commit_sha"] == stage.marker_sha, (
        f"AC3 fixture precondition FAILED: the same-cycle precondition requires "
        f"prev_data.red_commit_sha == the marker's sha; actual prev="
        f"{stage.prev.data['red_commit_sha']!r} marker={stage.marker_sha!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    data = _assert_routed("AC3", result)
    assert data.get("cycle") == 1 and data.get("red_commit_sha") == stage.marker_sha, (
        f"AC3: the same-cycle route must be unchanged — cycle 1 and the marker's "
        f"sha {stage.marker_sha!r}; actual {_describe(result)}"
    )
    _assert_cycle_source("AC3", stage, result, "prev_data")


# ═════════════════════════════════════════════════════════════════════════
# AC4 — CONTENT still binds a later-cycle adoption
# ═════════════════════════════════════════════════════════════════════════


def test_ac4_later_cycle_sentinel_with_a_content_mismatch_stays_terminal(tmp_path, monkeypatch):
    """AC4: the cycle-2 sentinel qualifies on every KEY (run_id, ctx_hash,
    sidecar linkage to the marker's sha) — but the dirty path's current sha256
    differs from the digest recorded when GREEN completed (the post-GREEN edit
    window spans crash and restart). -> terminal.

    The digest format is not guessed: the marker is written by the production
    writer while the tree still holds the GREEN content, and only then is the
    file rewritten.

    PASSES TODAY (the later cycle is not adopted at all); post-GREEN it is the
    pin that stops forward resolution from loosening the CONTENT test."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac4", monkeypatch, manifest=manifest, dirty_paths=manifest,
        post_green_edit=manifest,
    )
    recorded = json.loads(stage.marker_path.read_text()).get("digests", {})
    current = _sha256(stage.repo / "src" / "module.py")
    assert recorded.get("src/module.py") and recorded["src/module.py"] != current, (
        f"AC4 fixture precondition FAILED: the post-GREEN edit must break the "
        f"recorded digest; recorded={recorded!r} current={current!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC4", stage, result, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC5 — the MANIFEST still binds a later-cycle adoption
# ═════════════════════════════════════════════════════════════════════════


def test_ac5_later_cycle_sentinel_with_a_path_outside_the_manifest_stays_terminal(tmp_path, monkeypatch):
    """AC5: the cycle-2 sentinel qualifies on keys and linkage, but `src/other.py`
    is ALSO dirty and is NOT in the marker's recorded manifest. A GREEN that
    derived the manifest from the git diff instead of the record would adopt it.
    -> terminal over BOTH violations.

    PASSES TODAY."""
    manifest = ["src/module.py"]
    dirty = ["src/module.py", "src/other.py"]
    stage = _stage(tmp_path, "ac5", monkeypatch, manifest=manifest, dirty_paths=dirty)
    recorded_paths = json.loads(stage.marker_path.read_text()).get("paths", [])
    assert "src/other.py" not in recorded_paths, (
        f"AC5 fixture precondition FAILED: `src/other.py` must be OUTSIDE the "
        f"recorded manifest; actual paths={recorded_paths!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC5", stage, result, dirty)


# ═════════════════════════════════════════════════════════════════════════
# AC6 — IDENTITY still binds a later-cycle adoption (two legs)
# ═════════════════════════════════════════════════════════════════════════


def test_ac6a_later_cycle_sentinel_of_a_foreign_run_id_stays_terminal(tmp_path, monkeypatch):
    """AC6 leg 1: the cycle-2 sentinel links through the sidecar and matches the
    marker's sha, but it was written under a FOREIGN `run_id`
    (`..._rforeignrun9999_h<hash>.json`). Another run's work is never adopted —
    forward cycle resolution must not become a way around run identity.
    -> terminal. PASSES TODAY."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac6a", monkeypatch, manifest=manifest, dirty_paths=manifest,
        sentinel_run_id="foreignrun9999",
    )
    resume_dir = stage.scratchpad / "resume"
    foreign = sorted(p.name for p in resume_dir.glob("*_rforeignrun9999*.json"))
    mine = sorted(p.name for p in resume_dir.glob(f"*_r{stage.run_id}*.json"))
    assert foreign and not mine, (
        f"AC6a fixture precondition FAILED: expected a foreign-run cycle-2 "
        f"sentinel and NO sentinel for this run; actual foreign={foreign!r} "
        f"mine={mine!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC6a", stage, result, manifest)


def test_ac6b_later_cycle_sentinel_with_a_stale_ctx_hash_stays_terminal(tmp_path, monkeypatch):
    """AC6 leg 2 (§1ac): the cycle-2 sentinel carries this run's id and links
    through the sidecar, but its `ctx_hash` segment was computed with
    `_retry_nonce="1"` while the ctx now carries `"2"` — the state right after an
    operator bumps that knob. `compute_ctx_hash` folds org_config, so the on-disk
    name really is `..._h<stale>.json`. -> terminal. PASSES TODAY."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac6b", monkeypatch, manifest=manifest, dirty_paths=manifest,
        ctx_org_extra={"_retry_nonce": "2"},
        sentinel_ctx_org_extra={"_retry_nonce": "1"},
    )
    current_hash = step_sentinel.compute_ctx_hash(stage.ctx)
    stale_hash = step_sentinel.compute_ctx_hash(
        replace(stage.ctx, org_config={**stage.ctx.org_config, "_retry_nonce": "1"})
    )
    assert current_hash and stale_hash and current_hash != stale_hash, (
        f"AC6b fixture precondition FAILED: a `_retry_nonce` bump must change the "
        f"ctx hash; actual current={current_hash!r} stale={stale_hash!r}"
    )
    names = sorted(p.name for p in (stage.scratchpad / "resume").glob("*.json"))
    assert any(n.endswith(f"_h{stale_hash[:12]}.json") for n in names), (
        f"AC6b fixture precondition FAILED: expected a real STALE-hash sentinel "
        f"`..._h{stale_hash[:12]}.json` on disk; actual resume dir={names!r}"
    )
    assert not any(n.endswith(f"_h{current_hash[:12]}.json") for n in names), (
        f"AC6b fixture precondition FAILED: no sentinel may carry the CURRENT "
        f"ctx hash; actual resume dir={names!r}"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC6b", stage, result, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC7 — LINKAGE: a later cycle qualifies only on persisted evidence
# ═════════════════════════════════════════════════════════════════════════


def test_ac7_later_cycle_without_sidecar_linkage_stays_terminal(tmp_path, monkeypatch):
    """AC7, both legs — the fail-closed half of the design. A later-cycle
    sentinel is adopted ONLY when `red-cycle-shas.json` independently links that
    cycle to the MARKER's `red_commit_sha`:

      leg 1: the sidecar has no entry for cycle 2 at all (missing/unreadable
             linkage evidence => no candidate);
      leg 2: the sidecar HAS cycle 2, but its sha is not the marker's.

    Without this, "try later cycles" degenerates into a scan and any sentinel
    lying around gets adopted. -> terminal in both. PASSES TODAY (no later cycle
    is adopted at all); post-GREEN these are the only thing bounding the walk."""
    manifest = ["src/module.py"]

    leg1 = _stage(
        tmp_path, "ac7a", monkeypatch, manifest=manifest, dirty_paths=manifest,
        sidecar_cycles=(1,),
    )
    assert p5._read_red_cycle_sha(leg1.scratchpad, 2) is None, (
        f"AC7 leg 1 fixture precondition FAILED: the sidecar must have NO cycle-2 "
        f"entry; actual sidecar={leg1.sidecar_path.read_text()!r}"
    )
    result1 = p5._verify_red_fails_mechanically(leg1.ctx, leg1.prev)
    _assert_terminal_refusal("AC7 leg 1 (no sidecar entry)", leg1, result1, manifest)

    leg2 = _stage(
        tmp_path, "ac7b", monkeypatch, manifest=manifest, dirty_paths=manifest,
        sidecar_cycles=(1, 2), sidecar_sha_overrides={2: UNLINKED_SHA},
    )
    assert p5._read_red_cycle_sha(leg2.scratchpad, 2) == UNLINKED_SHA != leg2.marker_sha, (
        f"AC7 leg 2 fixture precondition FAILED: the sidecar's cycle-2 sha must "
        f"exist and DIFFER from the marker's; actual sidecar="
        f"{leg2.sidecar_path.read_text()!r} marker={leg2.marker_sha!r}"
    )
    # Leg 2 breaks ONLY the sidecar half: the sentinel payload's sha still links
    # to the marker, so this leg genuinely pins the SIDECAR half of P2 and cannot
    # be satisfied by a GREEN that checks the payload alone.
    _assert_payload_linkage("AC7 leg 2", leg2, 2, links=True)
    result2 = p5._verify_red_fails_mechanically(leg2.ctx, leg2.prev)
    _assert_terminal_refusal("AC7 leg 2 (sidecar sha does not link)", leg2, result2, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC8 — idempotent double entry (§1ab-d)
# ═════════════════════════════════════════════════════════════════════════


def test_ac8_two_consecutive_entries_are_idempotent(tmp_path, monkeypatch):
    """AC8 (§1ab-d): re-entry happens for real (a retry, a resumed run), so the
    SECOND call on the AC1 stage must produce the same verdict as the first —
    equal `error_code`, `retry_from_step` and `cycle` — and must not mutate the
    evidence it reads: the marker and the sidecar stay byte-identical.

    FAILS TODAY: both calls return the terminal `E_RED_WORKTREE_DIRTY` (equal,
    but not routed), so the routing assertions fire on call 1."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac8", monkeypatch, manifest=manifest, dirty_paths=manifest)
    marker_before = stage.marker_path.read_bytes()
    sidecar_before = stage.sidecar_path.read_bytes()

    first = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    second = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    data1 = _assert_routed("AC8 (call 1)", first)
    data2 = _assert_routed("AC8 (call 2)", second)
    for key in ("retry_from_step", "cycle", "red_commit_sha"):
        assert data1.get(key) == data2.get(key), (
            f"AC8: re-entry must be idempotent on {key!r}; call1="
            f"{data1.get(key)!r} call2={data2.get(key)!r}\n"
            f"call1 {_describe(first)}\ncall2 {_describe(second)}"
        )
    assert first.error_code == second.error_code, (
        f"AC8: the two calls must agree on error_code; call1="
        f"{first.error_code!r} call2={second.error_code!r}"
    )
    assert stage.marker_path.read_bytes() == marker_before, (
        f"AC8: the GREEN marker must be READ-ONLY evidence — it changed across "
        f"two entries;\nbefore={marker_before!r}\nafter="
        f"{stage.marker_path.read_bytes()!r}"
    )
    assert stage.sidecar_path.read_bytes() == sidecar_before, (
        f"AC8: `red-cycle-shas.json` must be READ-ONLY evidence for the "
        f"resolution — it changed across two entries;\nbefore={sidecar_before!r}\n"
        f"after={stage.sidecar_path.read_bytes()!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC9 — the side effect (§1l): the resolution is never silent
# ═════════════════════════════════════════════════════════════════════════


def test_ac9_recovery_emits_the_resolved_cycle_and_its_source(tmp_path, monkeypatch):
    """AC9 (§1l): the AC1 stage emits `orphan_green_recovery_routed` through
    phase_5's real emit call site (`_emit_safe`, observed at the module
    attribute — the one seam phase_5 emits through) carrying `cycle == 2` (the
    RESOLVED cycle, not the loop's 1) and
    `cycle_source == "red_cycle_sidecar"`. Adopting a cycle other than the
    one the loop counter named is exactly the decision an operator must be able
    to read back out of the event log.

    FAILS TODAY: no event is emitted at all (the recovery declines), and the
    payload (`:3101`) has no `cycle_source` key."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac9", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    payload = _routed_event("AC9", stage)
    assert payload.get("cycle") == 2, (
        f"AC9: expected the emitted `cycle` to be the RESOLVED cycle 2 "
        f"(prev_data said 1); actual payload={payload!r}, result {_describe(result)}"
    )
    _assert_cycle_source("AC9", stage, result, "red_cycle_sidecar")
    assert payload.get("red_commit_sha") == stage.red_shas[2], (
        f"AC9: expected the emitted `red_commit_sha` to be cycle 2's "
        f"{stage.red_shas[2]!r} (the marker's sha); actual payload={payload!r}"
    )
    flat = json.dumps(payload, default=str)
    assert "src/module.py" in flat and stage.run_id in flat, (
        f"AC9: expected the routed paths AND the run_id named in the payload "
        f"(whose work is adopted, and which work, is the whole question); actual "
        f"payload={payload!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC10 — Point -> Host -> Test (§1y): the routed sha makes GREEN-skip fire
# ═════════════════════════════════════════════════════════════════════════


def test_ac10_routed_data_makes_the_real_invoke_green_llm_skip_green(tmp_path, monkeypatch):
    """AC10 (§1y). "Resume after GREEN works" does not mean "a StepResult with a
    nice error code": it means the downstream GREEN-skip fires. The Host is the
    real `_invoke_green_llm`, whose GH483 seam reads the marker with
    `_read_green_complete_resume(scratchpad, prev.data["red_commit_sha"])`
    (`:8298`). With the stale cycle-1 sha the marker (keyed to cycle 2's sha)
    does not match and the GREEN LLM is re-invoked — the adoption is worthless.

    `invoke_llm_subprocess` is fenced with a fixture stand-in that FAILS LOUDLY
    (it is an external, billed side effect, never the UUT): if the skip does not
    fire, the assertion prints that fact instead of spawning a model.

    FAILS TODAY: the recovery declines, so there is no routed data at all and
    the routing assertion fires first."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac10", monkeypatch, manifest=manifest, dirty_paths=manifest)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    data = _assert_routed("AC10", result)

    spawned: list = []

    def _fenced_invoke(*args, **kwargs):  # noqa: ANN002, ANN003
        spawned.append(kwargs.get("step_name") or "invoke_llm_subprocess")
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_green_llm",
            error="FIXTURE FENCE: the GREEN-skip seam did NOT fire, so production "
                  "reached the real LLM invocation (fenced here, not billed)",
            error_code="E_FIXTURE_LLM_FENCE",
        )

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _fenced_invoke)

    green_prev = StepResult(
        status="ok", data=dict(data), duration_ms=0, step_name="build_green_prompt"
    )
    green = p5._invoke_green_llm(stage.ctx, green_prev)

    assert not spawned, (
        f"AC10: `_invoke_green_llm` fell through to the LLM invocation instead of "
        f"taking the GH483 GREEN-skip seam (`:8298`) — the routed "
        f"red_commit_sha={data.get('red_commit_sha')!r} does not match the marker "
        f"{stage.marker_sha!r}; fence hits={spawned!r}"
    )
    assert green.status == "ok", (
        f"AC10: expected the real `_invoke_green_llm` to return status=='ok' on "
        f"the routed data; actual {_describe(green)}"
    )
    assert isinstance(green.data, dict) and green.data.get("green_complete_resume") is True, (
        f"AC10: expected `data['green_complete_resume'] is True` — the routed sha "
        f"must make the downstream GREEN-skip fire, which is what "
        f"\"resume after GREEN works\" means; actual {_describe(green)}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC11 — latest work wins: the candidate walk is DESCENDING
# ═════════════════════════════════════════════════════════════════════════


def test_ac11_latest_linked_cycle_wins_over_an_earlier_later_cycle(tmp_path, monkeypatch):
    """AC11: sentinels at BOTH cycle 2 and cycle 3 while the loop re-entered at
    cycle 1, with the sidecar linking cycle 3 to the marker's sha. Candidates are
    tried DESCENDING — latest work wins — so the resolved cycle is 3.

    The `fixture_sentinel_cycle` discriminator makes this unfakeable: it proves
    WHICH sentinel's payload was forwarded, which a relabelled `cycle` field
    cannot.

    FAILS TODAY: `prev_data["cycle"]` is 1, the cycle-1 sentinel does not exist,
    recovery declines -> `E_RED_WORKTREE_DIRTY`."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac11", monkeypatch, manifest=manifest, dirty_paths=manifest,
        n_red_cycles=3, prev_cycle=1, prev_red_cycle=1,
        sentinel_cycles=(2, 3), marker_red_cycle=3, sidecar_cycles=(1, 2, 3),
    )
    sha3 = stage.red_shas[3]
    assert stage.marker_sha == sha3 and p5._read_red_cycle_sha(stage.scratchpad, 3) == sha3, (
        f"AC11 fixture precondition FAILED: the marker and the sidecar must both "
        f"point at cycle 3's sha {sha3!r}; actual marker={stage.marker_sha!r} "
        f"sidecar={stage.sidecar_path.read_text()!r}"
    )
    assert p5._read_red_cycle_sha(stage.scratchpad, 2) != sha3, (
        f"AC11 fixture precondition FAILED: cycle 2 must NOT link to the marker's "
        f"sha, so only cycle 3 can qualify; actual sidecar="
        f"{stage.sidecar_path.read_text()!r}"
    )
    _assert_payload_linkage("AC11", stage, 3, links=True)
    _assert_payload_linkage("AC11", stage, 2, links=False)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    data = _assert_routed("AC11", result)
    assert data.get("cycle") == 3, (
        f"AC11: the descending candidate walk must resolve to the LATEST linked "
        f"cycle 3, not 2 and not the loop's 1; actual {_describe(result)}"
    )
    assert data.get("fixture_sentinel_cycle") == 3, (
        f"AC11: cycle 3's SENTINEL PAYLOAD must be the one forwarded (a relabelled "
        f"`cycle` on cycle 2's payload is not a resolution); actual "
        f"fixture_sentinel_cycle={data.get('fixture_sentinel_cycle')!r} — "
        f"{_describe(result)}"
    )
    assert data.get("red_commit_sha") == sha3, (
        f"AC11: the routed sha must be cycle 3's {sha3!r} (the marker's); actual "
        f"{_describe(result)}"
    )
    assert _routed_event("AC11", stage).get("cycle") == 3, (
        f"AC11: the emitted event must name the resolved cycle 3; actual payload="
        f"{_routed_event('AC11', stage)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# v2 additions — P1 (the evidence), P3 (the cap), P4 (both sites), and the
# linkage / robustness classes the gate's round-1 verdict added.
# ═════════════════════════════════════════════════════════════════════════


def _fence_billed_llm(monkeypatch, spawned: list) -> None:
    """Fence `invoke_llm_subprocess` — an EXTERNAL, BILLED side effect, never the
    UUT. The stand-in records the call and returns a loud error StepResult, so an
    AC whose premise is "no LLM is reached" reports that fact instead of spending
    money. (`conftest.py` also poisons PATH with a burn-guard; this is the
    in-process half.)"""

    def _fenced(*args, **kwargs):  # noqa: ANN002, ANN003
        name = str(kwargs.get("step_name") or "invoke_llm_subprocess")
        spawned.append(name)
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error="FIXTURE FENCE: a billed LLM invocation was reached (not billed)",
            error_code="E_FIXTURE_LLM_FENCE",
        )

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _fenced)


def _observed_steps(steps: list, record: list) -> list:
    """Copy each real `StepContract` with a transparent recorder around its
    UNMODIFIED production `execute`.

    This is an OBSERVER, not a stub: the production function is called with the
    engine's own arguments and its return value is passed straight back; the
    recorder remembers `(name, engine cycle, prev, result)`. The engine cycle is
    read from `telemetry_ctx` — the engine sets it per step (`engine.py:411-425`)
    — because `step_started` carries no cycle, and "did write_green_artifact run
    at cycle 2" is exactly what AC12 must answer. `skip_on_error` is left at each
    step's PRODUCTION value so the chain stops where production stops it."""
    out = []
    for s in steps:
        def _observer(real=s.execute, name=s.name):
            def _observed(ctx, prev):
                run_ctx = telemetry_ctx.get_current_run()
                res = real(ctx, prev)
                record.append({
                    "name": name,
                    "cycle": getattr(run_ctx, "cycle", None),
                    "prev": prev,
                    "result": res,
                })
                return res
            return _observed
        out.append(replace(s, execute=_observer()))
    return out


def _engine_harness_steps(record: list, guard_prev: StepResult) -> list:
    """The REAL top-level phase_5 steps, indices preserved, truncated after
    `write_green_artifact`, with ONE fixture bound at index 0.

    Index 0 is `validation_cycle_loop`, whose production `execute`
    (`_validation_cycle_loop_execute`) drives a 12-step LoopStepContract body
    containing two LLM invocations — unbounded and billed. It is replaced by a
    step that supplies what the body's `commit_red_tests` would have produced
    (`guard_prev`) and then calls the real `_verify_red_fails_mechanically` — the
    body step that actually produces the verdict under test — returning its result
    UNCHANGED. Supplying `prev` here is required because the engine's own entry
    point starts a workflow with `prev = None` (`execute()` -> `_execute_steps`
    with no `initial_data`), which is precisely how production reaches this step.
    Everything that decides anything stays production code: the guard, the
    predicate, `engine.py:563-574`, and `_write_green_artifact`.

    Indices are preserved because `retry_from_step` is an index into the real
    `phase_5_implement_workflow()` — a re-indexed slice would route to the wrong
    step and prove nothing."""
    real_steps = p5.phase_5_implement_workflow().steps
    names = [s.name for s in real_steps]
    assert names[0] == LOOP_STEP, (
        f"fixture precondition FAILED: expected {LOOP_STEP!r} at index 0 of the "
        f"real phase_5 workflow; actual steps={names!r}"
    )
    route_idx = _step_index(ROUTE_TARGET)
    bounded = list(real_steps[: route_idx + 1])

    def _guard_only(ctx, prev):  # noqa: ARG001 — see docstring: prev is None here
        return p5._verify_red_fails_mechanically(ctx, guard_prev)

    bounded[0] = replace(bounded[0], execute=_guard_only)
    assert not getattr(bounded[0], "skip_on_error", False), (
        f"fixture precondition FAILED: {LOOP_STEP!r} declares skip_on_error="
        f"{getattr(bounded[0], 'skip_on_error', None)!r} — with it truthy "
        f"`engine.py:555` never reaches the retry branch and AC12 could not be "
        f"decided by production code"
    )
    return _observed_steps(bounded, record)


# ═════════════════════════════════════════════════════════════════════════
# AC12 / AC12b (P3) — the routed result must pass the engine's cap, at the
# headline cycle AND at the backstop
# ═════════════════════════════════════════════════════════════════════════


def _drive_recovery_through_the_real_engine(tmp_path, name, monkeypatch, ac, resolved_cycle):
    """Stage the recovery state for `resolved_cycle` and drive it through the
    REAL engine entry point. Returns `(stage, recorded, final, spawned)`.

    Single-sourced so AC12, AC12b and AC25n all observe the SAME re-entry —
    three harnesses would be three chances to disagree about what production
    does."""
    manifest = ["src/module.py"]
    if resolved_cycle == 2:
        stage = _stage(tmp_path, name, monkeypatch, manifest=manifest, dirty_paths=manifest)
    else:
        # The AC11 state: sentinels at 2 and 3, linkage (sidecar AND payload) on 3.
        stage = _stage(
            tmp_path, name, monkeypatch, manifest=manifest, dirty_paths=manifest,
            n_red_cycles=3, prev_cycle=1, prev_red_cycle=1,
            sentinel_cycles=(2, 3), marker_red_cycle=3, sidecar_cycles=(1, 2, 3),
        )
    _assert_payload_linkage(ac, stage, resolved_cycle, links=True)
    sentinel_payload = _sentinel_payload(stage, resolved_cycle)
    assert sentinel_payload.get("cycle_count") == resolved_cycle, (
        f"{ac} fixture precondition FAILED: the adopted payload must carry the "
        f"GREEN cycle's own `cycle_count` == {resolved_cycle} — that is the value "
        f"today's `{{**payload, ...}}` forwards and the cap then refuses; actual "
        f"{sentinel_payload.get('cycle_count')!r}"
    )
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    recorded: list = []
    workflow = WorkflowDefinition(
        name=PHASE_5_WORKFLOW_NAME, steps=_engine_harness_steps(recorded, stage.prev)
    )
    engine = WorkflowEngine(EventLog(stage.log_path))
    engine.register(PHASE_5_WORKFLOW_NAME, workflow)
    final, _final_ctx = engine.execute(PHASE_5_WORKFLOW_NAME, stage.ctx, stage.run_id)
    return stage, recorded, final, spawned


@pytest.mark.parametrize("ac,resolved_cycle", [("AC12", 2), ("AC12b", 3)])
def test_ac12_routed_result_survives_the_engine_validation_cycle_cap(
    tmp_path, monkeypatch, ac, resolved_cycle
):
    """AC12 (resolved 2) and AC12b (resolved 3) — P3, the "not inert" ACs, over
    ONE harness so the engine entry is single-sourced.

    The ONLY consumer of `recoverable=True` + `retry_from_step` is
    `engine.py:563-574`. It reads `cycle_count` from the ROUTED DATA (`:568`) and
    admits the retry iff
    `cycle_count < _MAX_VALIDATION_CYCLES` OR (`gate_budget_ok` and
    `cycle_count < _GATE_BUDGET_HARD_BACKSTOP`) (`:571-572`). Today's routed data
    is `{**payload, ...}` and the adopted payload carries the GREEN cycle's
    `cycle_count`, so even a perfect cycle resolution terminates on
    `E_GREEN_ORPHAN_RECOVERY_ROUTED`.

    P3 sets `cycle_count = max(resolved_cycle - 1, 0)` AND `gate_budget_ok = True`.
    AC12b is what forces the second half: for resolved cycle 3 the formula alone
    yields `cycle_count == 2`, which is NOT `< 2`, so a `cycle_count`-only
    implementation routes and is then capped — the same inert termination this lot
    exists to remove. An outer phase-5 cycle above 2 is reachable through
    `lib/recoverable_gate.py:100-108`, so this is not a hypothetical. The
    admission predicate below is evaluated with the ENGINE's own constants, not
    hardcoded numbers.

    The decision is made by the REAL engine through its REAL entry point: the
    bounded workflow is `register`ed and driven with `WorkflowEngine.execute()`,
    so the per-run marks `execute()` sets (`engine.py:238-244` —
    `_rework_cycle_high`, `_rework_last_step`, `_same_cycle_retries`) are set by
    production code, exactly as in a live run. Entering below `execute()` would
    run a partially-initialised engine and the retry branch would die at `:630`
    on an attribute production always sets. The retry decision itself is still
    `engine.py:563-574`. The one fixture bound is documented in
    `_engine_harness_steps`.

    FAILS TODAY for both cases: the guard declines, and the payload's
    `cycle_count` would be capped even if it routed."""
    stage, recorded, final, spawned = _drive_recovery_through_the_real_engine(
        tmp_path, "ac12" if resolved_cycle == 2 else "ac12b", monkeypatch, ac, resolved_cycle,
    )

    chain = [(r["name"], r["cycle"], r["result"].status, r["result"].error_code) for r in recorded]
    assert recorded, (
        f"{ac}: expected the engine to execute at least the guard step; actual "
        f"recorded={chain!r} final {_describe(final)}"
    )
    routed = recorded[0]["result"]
    _assert_routed(f"{ac} (the guard's own verdict)", routed)

    # The engine's admission predicate, read off the engine itself (§1g) —
    # asserted BEFORE the re-entry evidence, because this is the exact arithmetic
    # that decides between "resumes" and "inert".
    max_cycles = WorkflowEngine._MAX_VALIDATION_CYCLES
    backstop = WorkflowEngine._GATE_BUDGET_HARD_BACKSTOP
    cycle_count = int(routed.data.get("cycle_count", -1))
    gate_budget_ok = bool(routed.data.get("gate_budget_ok"))
    assert cycle_count == max(resolved_cycle - 1, 0), (
        f"{ac}: the routed data must carry "
        f"`cycle_count == max(resolved_cycle - 1, 0)` == "
        f"{max(resolved_cycle - 1, 0)} so `next_cycle` lands on the resolved "
        f"cycle {resolved_cycle} — not the adopted payload's {resolved_cycle}; "
        f"actual cycle_count={routed.data.get('cycle_count')!r} — {_describe(routed)}"
    )
    assert gate_budget_ok is True, (
        f"{ac}: the routed data must also set `gate_budget_ok = True` — with "
        f"`_MAX_VALIDATION_CYCLES == {max_cycles}` the cycle_count formula alone "
        f"clears `engine.py:571` only for a resolved cycle <= {max_cycles}, so a "
        f"resolved cycle 3 would route and then be capped (inert). The backstop "
        f"`{backstop}` keeps it bounded; actual gate_budget_ok="
        f"{routed.data.get('gate_budget_ok')!r} — {_describe(routed)}"
    )
    assert cycle_count < max_cycles or (gate_budget_ok and cycle_count < backstop), (
        f"{ac}: `engine.py:571-572` would SKIP the retry branch for "
        f"cycle_count={cycle_count} gate_budget_ok={gate_budget_ok} "
        f"(max_cycles={max_cycles}, backstop={backstop}) — the run terminates on "
        f"{ROUTED_CODE!r} instead of resuming; {_describe(routed)}"
    )

    executed = [r["name"] for r in recorded]
    assert ROUTE_TARGET in executed, (
        f"{ac}: the REAL engine retry branch must re-enter at {ROUTE_TARGET!r} — "
        f"a routed result that never re-enters is inert; actual chain={chain!r} "
        f"final {_describe(final)}"
    )
    artifact = next(r for r in recorded if r["name"] == ROUTE_TARGET)
    assert artifact["cycle"] == resolved_cycle, (
        f"{ac}: {ROUTE_TARGET!r} must execute at cycle {resolved_cycle} (the "
        f"resolved cycle, `next_cycle = cycle_count + 1`), so the cycle-keyed "
        f"sentinels and the green gates belong to the GREEN being adopted; actual "
        f"engine cycle={artifact['cycle']!r} chain={chain!r}"
    )
    assert final.error_code != ROUTED_CODE, (
        f"{ac}: the run must NOT terminate on {ROUTED_CODE!r} — that is exactly "
        f"the capped, inert outcome; actual {_describe(final)} chain={chain!r}"
    )
    assert final.status == "ok", (
        f"{ac}: after re-entry the bounded chain ends at {ROUTE_TARGET!r}, which "
        f"accepts this COMPLETE-marker GREEN; actual {_describe(final)} "
        f"chain={chain!r}"
    )
    assert not spawned, (
        f"{ac}: no LLM invocation may be reached on this path; fence hits="
        f"{spawned!r} chain={chain!r}"
    )
    iteration_started = [
        e for e in stage.events.of("iteration_started")
        if e["payload"].get("cycle_number") == resolved_cycle
    ]
    assert iteration_started, (
        f"{ac}: the engine's own `iteration_started` for cycle {resolved_cycle} "
        f"must be emitted (proof the retry branch, not a fixture, drove the "
        f"re-entry); actual events={stage.events.types()!r}"
    )

    # P1 on the re-entry path: the engine hands the route target a plain DICT
    # `prev` (`engine.py:400`, built at `:670-686`), and this is the ONLY entry
    # into `_write_green_artifact` that no other AC exercises — AC13 calls it with
    # a real StepResult. `_resolve_git_cwd` must therefore fall back to
    # `org_config["git_cwd"]` here, and the marker must still be there afterwards,
    # refreshed from the current tree.
    assert stage.marker_path.is_file(), (
        f"{ac}: after the re-entry, {ROUTE_TARGET!r} ran with the engine's plain "
        f"dict `prev`; the marker must still be present (P1 rewrites it from the "
        f"current tree, it never deletes it); actual resume dir="
        f"{sorted(p.name for p in (stage.scratchpad / 'resume').glob('*'))!r}"
    )
    marker = json.loads(stage.marker_path.read_text())
    assert marker.get("red_commit_sha") == stage.red_shas[resolved_cycle], (
        f"{ac}: the refreshed marker must stay keyed to the resolved cycle's RED "
        f"sha {stage.red_shas[resolved_cycle]!r} — the routed sha is what reached "
        f"{ROUTE_TARGET!r} through the dict `prev`; actual marker={marker!r}"
    )
    assert marker.get("digests", {}).get("src/module.py") == _sha256(stage.repo / "src" / "module.py"), (
        f"{ac}: the refreshed marker's digest must match the tree as it stands "
        f"after the re-entry (a dict `prev` whose git root did not resolve would "
        f"leave an empty/stale record); actual marker={marker!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC13 (P1) — the evidence is written where GREEN completes
# ═════════════════════════════════════════════════════════════════════════


def _green_artifact_prev(
    stage: _Stage, cycle: int, raw_response: str, spec_path: "str | None" = None
) -> StepResult:
    """A real `StepResult` in the shape `_invoke_green_llm` hands
    `write_green_artifact` (via `check_green_token_budget`). `spec_path` overrides
    the stage's spec — AC23n leg 2 needs a spec whose §5 Files list does not
    parse, i.e. an EMPTY allowlist."""
    return StepResult(
        status="ok",
        data=_green_sentinel_payload(
            stage.red_shas[cycle], ["src/module.py"],
            spec_path or stage.spec_path, cycle,
            Path(stage.scratchpad), stage.repo, raw_response=raw_response,
        ),
        duration_ms=0,
        step_name="check_green_token_budget",
    )


def test_ac13a_write_green_artifact_persists_the_completion_marker(tmp_path, monkeypatch):
    """AC13 leg (a) — P1, defect 1. `_persist_green_complete_resume` (`:1813`)
    has exactly ONE call site today: block 5 of
    `_verify_red_fails_mechanically` (`:3307`), reached only when the RED suite
    PASSES. Block 2, the dirty-tree guard, returns terminal BEFORE it whenever an
    allowlisted production path is dirty — which an uncommitted GREEN always is.
    So in the very state the recovery exists for, the marker was never written and
    `:3081-3083` returns None: the recovery is structurally unreachable.

    P1 moves the write to `_write_green_artifact`, on `GREEN_COMPLETE` only. This
    leg pins the artifact: the marker exists, keyed to THIS cycle's RED sha, over
    the GREEN's production paths, with the on-disk sha256 digests captured at
    completion (no digests ⇒ AC20n refuses the adoption, so this half is
    load-bearing).

    FAILS TODAY: `_write_green_artifact` (`:8374-8490`) writes the log and returns;
    it never touches the marker."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac13a", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    assert not stage.marker_path.exists(), (
        f"AC13a fixture precondition FAILED: no marker may be hand-written — the "
        f"whole point is that PRODUCTION writes it; found {stage.marker_path}"
    )
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    result = p5._write_green_artifact(stage.ctx, _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW))

    assert result.status == "ok" and not spawned, (
        f"AC13a fixture precondition FAILED: a GREEN COMPLETE artifact write must "
        f"succeed without any LLM retry; actual {_describe(result)} fence={spawned!r}"
    )
    assert stage.marker_path.is_file(), (
        f"AC13a: expected `{p5.GREEN_COMPLETE_RESUME_RELPATH}` to exist after a "
        f"COMPLETE GREEN — without it the dirty-tree guard preempts the only "
        f"writer and the recovery can never see evidence; actual scratchpad "
        f"resume dir="
        f"{sorted(p.name for p in (stage.scratchpad / 'resume').glob('*')) if (stage.scratchpad / 'resume').is_dir() else 'MISSING'}"
    )
    marker = json.loads(stage.marker_path.read_text())
    assert marker.get("red_commit_sha") == stage.red_shas[2], (
        f"AC13a: the marker must be keyed to THIS cycle's RED sha "
        f"{stage.red_shas[2]!r} (the sha the resolution links against); actual "
        f"marker={marker!r}"
    )
    assert marker.get("paths") == ["src/module.py"], (
        f"AC13a: the marker's `paths` must be the GREEN's production paths as "
        f"`_detect_green_complete_resume` derives them (tests excluded, allowlist "
        f"applied); actual marker={marker!r}"
    )
    on_disk = _sha256(stage.repo / "src" / "module.py")
    assert marker.get("digests", {}).get("src/module.py") == on_disk, (
        f"AC13a: the marker must record the on-disk sha256 at completion "
        f"({on_disk!r}) — a marker without digests cannot satisfy the content "
        f"test (AC20n); actual marker={marker!r}"
    )


def test_ac13b_blocked_or_marker_less_green_writes_no_marker(tmp_path, monkeypatch):
    """AC13 leg (b): a GREEN that reported BLOCKED, and a GREEN whose output was
    truncated before any marker, are NOT completed work. Neither may leave a
    completion marker behind — otherwise P1 manufactures the very evidence the
    ownership rules exist to demand, and the next re-entry adopts a BLOCKED
    GREEN. Both raw responses are judged by the REAL `_parse_green_status`.

    PASSES TODAY (nothing writes a marker at all); post-GREEN it is the pin that
    keeps the new writer on the COMPLETE branch only."""
    manifest = ["src/module.py"]
    assert p5._parse_green_status(GREEN_BLOCKED_RAW) == p5.GREEN_BLOCKED, (
        "AC13b fixture precondition FAILED: the fixture's BLOCKED raw_response "
        "must be read as BLOCKED by the REAL `_parse_green_status`"
    )
    assert p5._parse_green_status(GREEN_NO_MARKER_RAW) == p5.GREEN_NO_MARKER, (
        "AC13b fixture precondition FAILED: the fixture's truncated raw_response "
        "must be read as NO_MARKER by the REAL `_parse_green_status`"
    )

    for leg, raw, expected_code in (
        ("blocked", GREEN_BLOCKED_RAW, "E_GREEN_BLOCKED"),
        ("no_marker", GREEN_NO_MARKER_RAW, None),
    ):
        stage = _stage(
            tmp_path, f"ac13b_{leg}", monkeypatch, manifest=manifest,
            dirty_paths=manifest, prev_cycle=2, prev_red_cycle=2,
            sentinel_cycles=(), marker_red_cycle=None,
        )
        spawned: list = []
        _fence_billed_llm(monkeypatch, spawned)

        result = p5._write_green_artifact(stage.ctx, _green_artifact_prev(stage, 2, raw))

        assert result.status == "error", (
            f"AC13b leg {leg!r}: a BLOCKED / marker-less GREEN must not be a "
            f"successful artifact write; actual {_describe(result)}"
        )
        if expected_code is not None:
            assert result.error_code == expected_code, (
                f"AC13b leg {leg!r}: expected error_code=={expected_code!r}; "
                f"actual {_describe(result)}"
            )
        assert not stage.marker_path.exists(), (
            f"AC13b leg {leg!r}: NO completion marker may be written — a marker "
            f"here would let the next re-entry adopt work that never completed; "
            f"actual marker contents={stage.marker_path.read_text()!r}"
        )


def test_ac13c_write_then_reenter_at_a_lower_cycle_routes_with_no_hand_written_marker(tmp_path, monkeypatch):
    """AC13 leg (c) — the two halves joined, with NO marker hand-written anywhere
    in this test. Production writes the evidence at GREEN completion (P1), and the
    next re-entry — at the LOWER cycle the auto-resume carries (P2) — adopts it.
    This is the reported failure end to end; either part missing fails it.

    FAILS TODAY on both halves: nothing writes the marker, and the cycle-1 entry
    could not see a cycle-2 sentinel anyway."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac13c", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=1, prev_red_cycle=1, sentinel_cycles=(2,), marker_red_cycle=None,
        sidecar_cycles=(1, 2),
    )
    assert not stage.marker_path.exists(), (
        f"AC13c fixture precondition FAILED: the marker must not pre-exist; "
        f"found {stage.marker_path.read_text()!r}"
    )
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    artifact = p5._write_green_artifact(stage.ctx, _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW))
    assert artifact.status == "ok" and not spawned, (
        f"AC13c: the cycle-2 GREEN artifact write must succeed; actual "
        f"{_describe(artifact)} fence={spawned!r}"
    )
    assert stage.marker_path.is_file(), (
        f"AC13c (P1 half): production must have written "
        f"`{p5.GREEN_COMPLETE_RESUME_RELPATH}` at GREEN completion; it did not, "
        f"so the re-entry below has no evidence to resolve against"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    data = _assert_routed("AC13c (P2 half)", result)
    assert data.get("red_commit_sha") == stage.red_shas[2] and data.get("cycle") == 2, (
        f"AC13c: the re-entry at cycle 1 must resolve to the cycle-2 GREEN that "
        f"production just recorded; expected sha={stage.red_shas[2]!r} cycle=2, "
        f"actual {_describe(result)}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC14 — the sidecar carries no run identity: a FOREIGN marker is never adopted
# ═════════════════════════════════════════════════════════════════════════


def test_ac14_foreign_run_marker_linked_only_by_the_sidecar_stays_terminal(tmp_path, monkeypatch):
    """AC14 (P2 linkage, cross-run). `red-cycle-shas.json` is merge-on-write over
    a scratchpad that another run can share, and it records nothing about WHO
    wrote an entry. So "sidecar links cycle c to the marker's sha" is not, by
    itself, evidence of ownership.

    Here the marker and the sidecar entry were left by a FOREIGN run (marker sha
    F, sidecar `2 -> F`), F's content is in the tree so the digests match, and OUR
    own legitimate cycle-2 sentinel exists — but ITS payload sha is ours, not F.
    The payload sha is the run-authenticated half of the linkage (the sentinel
    filename carries run_id + ctx_hash), so this must be terminal. Leg 2 is the
    same shape with a HAND-EDITED sidecar, which no production writer produces.

    PASSES TODAY (nothing later-cycle is adopted); post-GREEN these are the only
    thing standing between forward resolution and adopting another run's work."""
    manifest = ["src/module.py"]

    leg1 = _stage(
        tmp_path, "ac14a", monkeypatch, manifest=manifest, dirty_paths=manifest,
        marker_sha_literal=FOREIGN_MARKER_SHA,
        sidecar_cycles=(1, 2), sidecar_sha_overrides={2: FOREIGN_MARKER_SHA},
    )
    assert leg1.marker_sha == FOREIGN_MARKER_SHA, "AC14 leg 1 fixture precondition FAILED"
    assert p5._read_red_cycle_sha(leg1.scratchpad, 2) == FOREIGN_MARKER_SHA, (
        f"AC14 leg 1 fixture precondition FAILED: the sidecar must link cycle 2 "
        f"to the FOREIGN sha (the sidecar half passes); actual sidecar="
        f"{leg1.sidecar_path.read_text()!r}"
    )
    digests = json.loads(leg1.marker_path.read_text()).get("digests", {})
    assert digests.get("src/module.py") == _sha256(leg1.repo / "src" / "module.py"), (
        f"AC14 leg 1 fixture precondition FAILED: the CONTENT test must pass, so "
        f"only the run-identity linkage can refuse; actual digests={digests!r}"
    )
    _assert_payload_linkage("AC14 leg 1", leg1, 2, links=False)
    result1 = p5._verify_red_fails_mechanically(leg1.ctx, leg1.prev)
    _assert_terminal_refusal("AC14 leg 1 (foreign marker + sidecar)", leg1, result1, manifest)

    leg2 = _stage(
        tmp_path, "ac14b", monkeypatch, manifest=manifest, dirty_paths=manifest,
        marker_sha_literal=FOREIGN_MARKER_SHA,
        hand_written_sidecar={"2": FOREIGN_MARKER_SHA},
    )
    assert p5._read_red_cycle_sha(leg2.scratchpad, 2) == FOREIGN_MARKER_SHA, (
        f"AC14 leg 2 fixture precondition FAILED: expected the hand-edited "
        f"sidecar to link cycle 2 to the foreign sha; actual "
        f"{leg2.sidecar_path.read_text()!r}"
    )
    _assert_payload_linkage("AC14 leg 2", leg2, 2, links=False)
    result2 = p5._verify_red_fails_mechanically(leg2.ctx, leg2.prev)
    _assert_terminal_refusal("AC14 leg 2 (hand-edited sidecar)", leg2, result2, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC15n — the walk is DESCENDING, and an ascending one must fail this
# ═════════════════════════════════════════════════════════════════════════


def test_ac15n_descending_walk_prefers_the_later_linked_cycle(tmp_path, monkeypatch):
    """AC15n. Current cycle 1 with sentinels at BOTH 1 and 2 and linkage on 2.

    The discriminator is deliberate: `prev_data["red_commit_sha"]` equals the
    marker's sha, so candidate `c == current_cycle == 1` satisfies the
    same-cycle rule ("qualifies on the sentinel alone", precondition included)
    and an ASCENDING walk would stop there and adopt cycle 1's payload. Latest
    work wins, so the answer must be cycle 2 with
    `cycle_source == "red_cycle_sidecar"`, proven by which payload was forwarded.

    FAILS TODAY: today's code reads the sentinel at `prev_data["cycle"]` == 1 and
    the marker keyed on prev's sha (which matches), so it routes with cycle 1 —
    precisely the ascending answer this AC refuses."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac15n", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=1, prev_red_cycle=2, sentinel_cycles=(1, 2), marker_red_cycle=2,
        sidecar_cycles=(1, 2),
    )
    assert stage.prev.data["red_commit_sha"] == stage.marker_sha, (
        f"AC15n fixture precondition FAILED: prev's sha must equal the marker's, "
        f"so the cycle-1 candidate genuinely qualifies and only ORDER can decide; "
        f"actual prev={stage.prev.data['red_commit_sha']!r} "
        f"marker={stage.marker_sha!r}"
    )
    _assert_payload_linkage("AC15n", stage, 2, links=True)
    _assert_payload_linkage("AC15n", stage, 1, links=False)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    data = _assert_routed("AC15n", result)
    assert data.get("cycle") == 2, (
        f"AC15n: candidates are tried DESCENDING — latest work wins — so the "
        f"resolved cycle is 2, not the equally-qualifying current cycle 1; actual "
        f"{_describe(result)}"
    )
    assert data.get("fixture_sentinel_cycle") == 2, (
        f"AC15n: cycle 2's SENTINEL PAYLOAD must be the one forwarded; actual "
        f"fixture_sentinel_cycle={data.get('fixture_sentinel_cycle')!r} — "
        f"{_describe(result)}"
    )
    _assert_cycle_source("AC15n", stage, result, "red_cycle_sidecar")


# ═════════════════════════════════════════════════════════════════════════
# AC16n — §1ab in-phase retry: the same-cycle entry keeps its own source
# ═════════════════════════════════════════════════════════════════════════


def test_ac16n_in_phase_retry_at_the_same_cycle_routes_via_prev_data(tmp_path, monkeypatch):
    """AC16n (§1ab in-phase-retry leg): the run is genuinely AT cycle 2 — a retry
    inside the phase, not an auto-resume — with the cycle-2 sentinel, the sidecar
    `{1,2}`, the marker at sha2 and `prev_data.red_commit_sha == sha2`. The
    resolution must take the `c == current_cycle` branch: routed, `cycle == 2`,
    `cycle_source == "prev_data"`.

    Without this, a GREEN could hardcode `cycle_source="red_cycle_sidecar"` (or
    resolve every entry through the sidecar) and still pass AC1/AC15n.

    Routing PASSES TODAY (this is today's path); the `cycle_source` assertion
    FAILS TODAY — the payload (`:3101`) has no such key."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac16n", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(2,), marker_red_cycle=2,
        sidecar_cycles=(1, 2),
    )
    _assert_payload_linkage("AC16n", stage, 2, links=True)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    data = _assert_routed("AC16n", result)
    assert data.get("cycle") == 2 and data.get("red_commit_sha") == stage.marker_sha, (
        f"AC16n: the in-phase retry resolves its OWN cycle 2 and the marker's sha "
        f"{stage.marker_sha!r}; actual {_describe(result)}"
    )
    _assert_cycle_source("AC16n", stage, result, "prev_data")


# ═════════════════════════════════════════════════════════════════════════
# AC17n — the resolver never raises on a junk sidecar
# ═════════════════════════════════════════════════════════════════════════


def _call_guard_without_raising(ac: str, stage: _Stage):
    try:
        return p5._verify_red_fails_mechanically(stage.ctx, stage.prev)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"{ac}: the cycle resolution must NEVER raise — the sidecar is "
            f"merge-on-write JSON on a shared scratchpad and can hold anything. "
            f"Actual {type(exc).__name__}: {exc}. sidecar="
            f"{stage.sidecar_path.read_text()!r}"
        )


def test_ac17n_junk_sidecar_keys_are_skipped_and_never_raise(tmp_path, monkeypatch):
    """AC17n. The sidecar is JSON on disk written merge-on-write, so non-int keys,
    non-str values and non-positive cycles are all reachable. Candidate
    enumeration must SKIP them rather than raise: an exception here escapes the
    dirty-tree guard and takes down the phase with an unenumerated failure (§1n),
    which is strictly worse than the refusal it replaced.

    Leg 1: junk keys plus a valid, fully linked cycle 2 -> routed.
    Leg 2: the same junk with the linkage broken -> terminal. Both must return a
    StepResult, never raise.

    Leg 1 FAILS TODAY (the cycle-2 sentinel is invisible at cycle 1); leg 2 passes
    today and is the pin that the junk-tolerance did not become junk-acceptance."""
    manifest = ["src/module.py"]

    leg1 = _stage(
        tmp_path, "ac17na", monkeypatch, manifest=manifest, dirty_paths=manifest,
        sidecar_cycles=(1, 2),
    )
    junk = {"abc": leg1.red_shas[1], "-1": leg1.red_shas[1], "0": 17,
            "2": leg1.red_shas[2], "3": None}
    leg1.sidecar_path.write_text(json.dumps(junk, sort_keys=True))
    assert p5._read_red_cycle_sha(leg1.scratchpad, 2) == leg1.marker_sha, (
        f"AC17n leg 1 fixture precondition FAILED: cycle 2 must still link to the "
        f"marker through the junk; actual sidecar={leg1.sidecar_path.read_text()!r}"
    )
    _assert_payload_linkage("AC17n leg 1", leg1, 2, links=True)

    result1 = _call_guard_without_raising("AC17n leg 1", leg1)
    data1 = _assert_routed("AC17n leg 1", result1)
    assert data1.get("cycle") == 2, (
        f"AC17n leg 1: the junk keys must be SKIPPED and cycle 2 still resolved; "
        f"actual {_describe(result1)} sidecar={leg1.sidecar_path.read_text()!r}"
    )

    leg2 = _stage(
        tmp_path, "ac17nb", monkeypatch, manifest=manifest, dirty_paths=manifest,
        sidecar_cycles=(1, 2),
    )
    leg2.sidecar_path.write_text(json.dumps(
        {"abc": leg2.red_shas[1], "-1": leg2.red_shas[1], "0": 17,
         "2": UNLINKED_SHA, "3": None},
        sort_keys=True,
    ))
    assert p5._read_red_cycle_sha(leg2.scratchpad, 2) == UNLINKED_SHA, (
        f"AC17n leg 2 fixture precondition FAILED: cycle 2 must NOT link; actual "
        f"sidecar={leg2.sidecar_path.read_text()!r}"
    )

    result2 = _call_guard_without_raising("AC17n leg 2", leg2)
    _assert_terminal_refusal("AC17n leg 2 (junk + broken linkage)", leg2, result2, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC18n (P4) — raise site 2 shares the predicate, so it shares the fix
# ═════════════════════════════════════════════════════════════════════════


def test_ac18n_site_2_build_validation_prompt_resolves_the_same_cycle(tmp_path, monkeypatch):
    """AC18n (P4): `_orphan_green_recovery_result` is the shared predicate of
    `verify_red_fails_mechanically` (`:3144`) and `build_validation_prompt`
    (`:7259`). The AC1 state entered through site 2 must route identically —
    same error code, same `retry_from_step`, the marker's sha, the resolved
    cycle. One site fixed is half a class (the sibling suite's AC20 rule).

    FAILS TODAY: site 2 declines for exactly the same cycle-keying reason and
    returns the terminal refusal."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac18n", monkeypatch, manifest=manifest, dirty_paths=manifest)
    _assert_payload_linkage("AC18n", stage, 2, links=True)

    try:
        result = p5._build_validation_prompt(stage.ctx, stage.prev)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"AC18n: expected `_build_validation_prompt` to return the routing "
            f"result from its own guard (`:7259-7263`); actual it raised past the "
            f"guard: {type(exc).__name__}: {exc}"
        )

    data = _assert_routed("AC18n", result)
    assert data.get("cycle") == 2 and data.get("red_commit_sha") == stage.red_shas[2], (
        f"AC18n: site 2 must resolve the SAME cycle as site 1 (2) and forward the "
        f"marker's sha {stage.red_shas[2]!r}; actual {_describe(result)}"
    )
    assert result.step_name == SITE_2_STEP, (
        f"AC18n: the routed result must be attributed to {SITE_2_STEP!r} (the "
        f"step that refused), not to site 1; actual step_name="
        f"{result.step_name!r} — {_describe(result)}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC19n — the kill switch still kills
# ═════════════════════════════════════════════════════════════════════════


def test_ac19n_gate_off_declines_exactly_as_before(tmp_path, monkeypatch):
    """AC19n (Principle C): with `HAL_GREEN_COMPLETE_RESUME_GATE=0` the predicate
    returns None at `:3057` — BEFORE any cycle resolution — so the AC1 state gets
    the unchanged terminal refusal. The kill switch is the rollback plan for this
    whole lot; a resolution that runs before the gate check would void it.

    PASSES TODAY."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac19n", monkeypatch, manifest=manifest, dirty_paths=manifest)
    monkeypatch.setenv("HAL_GREEN_COMPLETE_RESUME_GATE", "0")
    assert not p5.get_config().gate_enabled("HAL_GREEN_COMPLETE_RESUME_GATE"), (
        "AC19n fixture precondition FAILED: the gate must read as OFF through the "
        "production config provider"
    )

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC19n", stage, result, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC20n — a pre-1626D marker carries no digests and is not content evidence
# ═════════════════════════════════════════════════════════════════════════


def test_ac20n_marker_without_digests_stays_terminal(tmp_path, monkeypatch):
    """AC20n: the AC1 state, except the marker is the PRE-1626D shape — a dict
    with `red_commit_sha` and `paths` and no `digests` key at all (a marker left
    by an older engine, or by a writer that could not read the tree). Names alone
    prove nothing: with no recorded digest there is no way to tell this tree from
    a post-GREEN edit, so the adoption must be refused.

    PASSES TODAY (`_read_green_complete_resume_record` degrades `digests` to `{}`
    and the per-path comparison fails closed); it is the pin that the new
    marker reader does not "helpfully" accept a digest-less record."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac20n", monkeypatch, manifest=manifest, dirty_paths=manifest)
    stage.marker_path.write_text(json.dumps({
        "red_commit_sha": stage.marker_sha, "paths": list(manifest),
    }))
    assert "digests" not in json.loads(stage.marker_path.read_text()), (
        f"AC20n fixture precondition FAILED: the marker must carry NO `digests` "
        f"key; actual {stage.marker_path.read_text()!r}"
    )
    _assert_payload_linkage("AC20n", stage, 2, links=True)

    result = p5._verify_red_fails_mechanically(stage.ctx, stage.prev)

    _assert_terminal_refusal("AC20n", stage, result, manifest)


# ═════════════════════════════════════════════════════════════════════════
# AC21n (P1) — a failed evidence write degrades VISIBLY and never raises
# ═════════════════════════════════════════════════════════════════════════


def test_ac21n_unwritable_marker_location_degrades_visibly_and_never_raises(tmp_path, monkeypatch):
    """AC21n (§1n): P1 adds a write to `_write_green_artifact`, whose contract
    out-of-scope suites depend on (`tests/test_phase_5_green_bash_removed.py:191`,
    `tests/test_phase_5_g1_band_aid_9_verify_green.py:334`). So the new writer must
    NEVER raise and never turn a good GREEN into a failure — and it must not be
    silent either: a silent evidence writer is the exact failure class this lot
    exists to remove, and an operator staring at a declined recovery has to be
    able to see WHY there was no marker.

    The failure is arranged deterministically (§1i) by pre-staging `resume/` as a
    read+execute-only directory, verified with a probe write; if the filesystem or
    the effective uid makes that unenforceable the test SKIPS loudly rather than
    passing vacuously.

    FAILS TODAY on the event: `green_complete_resume_persist_failed` does not
    exist in production (nor does the writer). `status == "ok"` and "no marker"
    pass today and are the pins that P1 did not change the step's contract."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac21n", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip(
            "AC21n not arrangeable as root: mode bits do not deny writes to uid 0, "
            "so an unwritable `resume/` cannot be staged deterministically."
        )
    resume_dir = stage.scratchpad / "resume"
    resume_dir.mkdir(parents=True, exist_ok=True)
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    os.chmod(resume_dir, 0o500)
    try:
        probe = resume_dir / ".probe"
        try:
            probe.write_text("x")
        except OSError:
            pass
        else:
            probe.unlink()
            pytest.skip(
                f"AC21n not arrangeable here: a 0o500 directory still accepted a "
                f"write ({resume_dir}), so the marker write cannot be made to fail "
                f"deterministically on this filesystem. Skipping loudly rather "
                f"than asserting nothing."
            )

        try:
            result = p5._write_green_artifact(
                stage.ctx, _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW)
            )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"AC21n: the P1 marker write must NEVER raise out of "
                f"`_write_green_artifact` — its contract is depended on by "
                f"out-of-scope suites; actual {type(exc).__name__}: {exc}"
            )

        assert result.status == "ok" and result.error_code is None, (
            f"AC21n: a COMPLETE GREEN must still succeed when the EVIDENCE write "
            f"fails — the marker is an optimisation for the next entry, never a "
            f"gate on this one; actual {_describe(result)}"
        )
        assert not stage.marker_path.exists(), (
            f"AC21n fixture precondition FAILED: the marker must not exist after "
            f"an unwritable-location degrade; actual "
            f"{stage.marker_path.read_text()!r}"
        )
        failed = stage.events.of(PERSIST_FAILED_EVENT)
        assert failed, (
            f"AC21n: expected {PERSIST_FAILED_EVENT!r} on the degrade branch (the "
            f"`red_cycle_sha_persist_failed` pattern, `:2466-2468`) — otherwise the "
            f"recovery declines later with no trace of why the evidence is "
            f"missing; actual events={stage.events.types()!r}"
        )
        payload = failed[0]["payload"]
        assert "cycle" in payload and str(payload.get("reason") or ""), (
            f"AC21n: the degrade event must name the `cycle` and a non-empty "
            f"`reason` (§1n — an unenumerated silent degrade is what this AC "
            f"forbids); actual payload={payload!r}"
        )
        assert not spawned, (
            f"AC21n: no LLM invocation may be reached on a COMPLETE response; "
            f"fence hits={spawned!r}"
        )
    finally:
        os.chmod(resume_dir, 0o700)


def test_ac21n_b_resume_path_occupied_by_a_file_degrades_visibly_for_every_uid(tmp_path, monkeypatch):
    """AC21n leg (b) — the same contract, arranged WITHOUT mode bits so it is
    uid-independent and can never skip: `resume` is pre-staged as a regular FILE,
    so the `ref_path.parent.mkdir(parents=True, exist_ok=True)` inside
    `_persist_green_complete_resume` (`:1824`) raises `FileExistsError` (an
    `OSError`) for root and non-root alike. This is a real shape — a stray file at
    a directory path, e.g. a botched archive extraction into the scratchpad.

    FAILS TODAY on the event (neither the writer nor the emit name exists);
    `status == "ok"` and "no marker" pass today."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac21nb", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    resume_path = stage.scratchpad / "resume"
    assert not resume_path.exists(), (
        f"AC21n(b) fixture precondition FAILED: nothing may have created "
        f"{resume_path} before it is staged as a file"
    )
    resume_path.write_text("not a directory\n", encoding="utf-8")
    assert resume_path.is_file(), (
        f"AC21n(b) fixture precondition FAILED: expected a regular FILE at "
        f"{resume_path}"
    )
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    try:
        result = p5._write_green_artifact(
            stage.ctx, _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW)
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"AC21n(b): the P1 marker write must NEVER raise out of "
            f"`_write_green_artifact` — a stray file at the `resume` path must "
            f"degrade, not crash a completed GREEN; actual "
            f"{type(exc).__name__}: {exc}"
        )

    assert result.status == "ok" and result.error_code is None, (
        f"AC21n(b): a COMPLETE GREEN must still succeed when the EVIDENCE write "
        f"fails; actual {_describe(result)}"
    )
    assert not stage.marker_path.exists(), (
        f"AC21n(b) fixture precondition FAILED: no marker can exist under a "
        f"non-directory `resume` path; actual {resume_path.read_text()!r}"
    )
    failed = stage.events.of(PERSIST_FAILED_EVENT)
    assert failed, (
        f"AC21n(b): expected {PERSIST_FAILED_EVENT!r} — an ATTEMPTED write that "
        f"raised OSError is exactly the branch that must be visible; actual "
        f"events={stage.events.types()!r}"
    )
    payload = failed[0]["payload"]
    assert "cycle" in payload and str(payload.get("reason") or ""), (
        f"AC21n(b): the degrade event must name the `cycle` and a non-empty "
        f"`reason`; actual payload={payload!r}"
    )
    assert not spawned, (
        f"AC21n(b): no LLM invocation may be reached on a COMPLETE response; "
        f"fence hits={spawned!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC23n — nothing to record is a HEALTHY state, and must stay silent
# ═════════════════════════════════════════════════════════════════════════


def test_ac23n_nothing_to_record_emits_no_alarm(tmp_path, monkeypatch):
    """AC23n. `_detect_green_complete_resume` collapses gate-off (`:1767-1768`),
    an empty spec allowlist (`:1771-1772`), a missing red sha (`:1778-1779`) and a
    git error (`:1782-1783`) all to `[]` — "nothing to record" is
    indistinguishable from "could not record" there, so the helper must stay
    SILENT for all of them. A test-only or docs-only lot, a spec with no parseable
    §5 Files list, and the kill switch itself are healthy states.

    Leg 1 is the kill switch: `HAL_GREEN_COMPLETE_RESUME_GATE=0` is the rollback
    plan for this whole lot, and it must not turn into a per-build alarm — the
    helper checks the gate FIRST, before any work. Leg 2 is a spec whose §5 has no
    parseable Files list, i.e. an empty allowlist.

    Both legs: the step still succeeds, no marker is written, and NO
    `green_complete_resume_persist_failed` is emitted.

    PASSES TODAY (no writer, no emit); it is the pin that P1's alarm is scoped to
    an ATTEMPTED-and-failed write and cannot become routine noise."""
    manifest = ["src/module.py"]

    # ── leg 1: the kill switch ────────────────────────────────────────────
    leg1 = _stage(
        tmp_path, "ac23na", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    spawned1: list = []
    _fence_billed_llm(monkeypatch, spawned1)
    monkeypatch.setenv("HAL_GREEN_COMPLETE_RESUME_GATE", "0")
    assert not p5.get_config().gate_enabled("HAL_GREEN_COMPLETE_RESUME_GATE"), (
        "AC23n leg 1 fixture precondition FAILED: the gate must read as OFF "
        "through the production config provider"
    )

    result1 = p5._write_green_artifact(
        leg1.ctx, _green_artifact_prev(leg1, 2, GREEN_COMPLETE_RAW)
    )

    assert result1.status == "ok" and not spawned1, (
        f"AC23n leg 1: the kill switch must not change the step's own outcome; "
        f"actual {_describe(result1)} fence={spawned1!r}"
    )
    assert not leg1.marker_path.exists(), (
        f"AC23n leg 1: with the gate OFF no marker may be written; actual "
        f"{leg1.marker_path.read_text()!r}"
    )
    assert leg1.events.of(PERSIST_FAILED_EVENT) == [], (
        f"AC23n leg 1: the kill switch is a HEALTHY state — flipping it must not "
        f"raise a {PERSIST_FAILED_EVENT!r} alarm on every single build; actual "
        f"events={leg1.events.types()!r}"
    )

    # ── leg 2: a spec with no parseable §5 Files list ─────────────────────
    monkeypatch.setenv("HAL_GREEN_COMPLETE_RESUME_GATE", "1")
    leg2 = _stage(
        tmp_path, "ac23nb", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    spawned2: list = []
    _fence_billed_llm(monkeypatch, spawned2)
    listless_spec = leg2.scratchpad / "specs" / "build-spec-no-files.md"
    listless_spec.write_text(
        "# fixture spec (gh1018 AC23n leg 2)\n\n"
        "A docs-only lot: prose, no parseable `## Files` list at all.\n",
        encoding="utf-8",
    )
    assert not p5._parse_spec_files_allowlist(str(listless_spec)), (
        f"AC23n leg 2 fixture precondition FAILED: the spec must yield an EMPTY "
        f"allowlist through the production parser; actual "
        f"{p5._parse_spec_files_allowlist(str(listless_spec))!r}"
    )

    result2 = p5._write_green_artifact(
        leg2.ctx, _green_artifact_prev(leg2, 2, GREEN_COMPLETE_RAW, spec_path=str(listless_spec))
    )

    assert result2.status == "ok" and not spawned2, (
        f"AC23n leg 2: an unparseable §5 Files list must not change the step's "
        f"outcome; actual {_describe(result2)} fence={spawned2!r}"
    )
    assert not leg2.marker_path.exists(), (
        f"AC23n leg 2: with an empty allowlist the detector yields no manifest, so "
        f"no marker may be written; actual {leg2.marker_path.read_text()!r}"
    )
    assert leg2.events.of(PERSIST_FAILED_EVENT) == [], (
        f"AC23n leg 2: 'nothing to record' is not a failure — a docs-only or "
        f"test-only lot must not emit {PERSIST_FAILED_EVENT!r}; actual events="
        f"{leg2.events.types()!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC22n — an unparseable marker is not evidence, and not an exception either
# ═════════════════════════════════════════════════════════════════════════


def test_ac22n_corrupt_marker_stays_terminal_without_raising(tmp_path, monkeypatch):
    """AC22n: the marker is JSON on disk written by a process that can be killed
    mid-write, so a TRUNCATED file is reachable — and P1 makes it more reachable by
    writing it on every completed GREEN. An unparseable marker is not evidence:
    the entry must reach the unchanged terminal refusal, and must not raise past
    the dirty-tree guard (which would replace a clean `E_RED_WORKTREE_DIRTY` with
    an unenumerated crash, §1n).

    PASSES TODAY (`_read_green_complete_resume` catches everything and returns
    None); it is the pin that the NEW keyless marker reader keeps that property."""
    manifest = ["src/module.py"]
    stage = _stage(tmp_path, "ac22n", monkeypatch, manifest=manifest, dirty_paths=manifest)
    truncated = '{"red_commit_sha": "' + str(stage.marker_sha)[:20]
    stage.marker_path.write_text(truncated)
    try:
        json.loads(stage.marker_path.read_text())
    except ValueError:
        pass
    else:
        pytest.fail(
            f"AC22n fixture precondition FAILED: the staged marker must be "
            f"UNPARSEABLE; actual {stage.marker_path.read_text()!r}"
        )
    _assert_payload_linkage("AC22n", stage, 2, links=True)

    result = _call_guard_without_raising("AC22n", stage)

    _assert_terminal_refusal("AC22n", stage, result, manifest)


# ═════════════════════════════════════════════════════════════════════════
# Post-merge review of PR #1914 (findings A-G) — defects in the shipped GREEN
# ═════════════════════════════════════════════════════════════════════════

SKIP_EVENT = "invoke_green_llm_skipped_green_complete"
DECLINED_EVENT = "orphan_green_recovery_declined"


def _green_llm_prev(stage: _Stage, cycle: int, extra: dict) -> StepResult:
    """`prev` as `check_green_token_budget` hands it to `_invoke_green_llm`, with
    the post-GREEN keys stripped — otherwise `{**prev.data, ...}` would carry a
    `green_complete_resume` the step never set and no assertion could tell a skip
    from a non-skip."""
    data = _green_sentinel_payload(
        stage.red_shas[cycle], ["src/module.py"], stage.spec_path, cycle,
        Path(stage.scratchpad), stage.repo,
    )
    for key in ("green_complete_resume", "green_resume_paths", "raw_response", "tokens_out"):
        data.pop(key, None)
    data.update(extra)
    return StepResult(status="ok", data=data, duration_ms=0, step_name="check_green_token_budget")


# ═════════════════════════════════════════════════════════════════════════
# AC24n — P1's marker must not swallow an IN-RUN green-gate retry
# ═════════════════════════════════════════════════════════════════════════


def _green_chain_steps(record: list, index0: "Callable[..., StepResult]") -> list:
    """The REAL top-level phase_5 steps, indices preserved, truncated after
    `invoke_green_llm`, with the loop DRIVER at index 0 replaced by `index0`.

    Same bound and same reason as `_engine_harness_steps`: the production driver
    `_validation_cycle_loop_execute` runs a 12-step body with two LLM
    invocations. `index0` stands in for the step whose VERDICT starts the chain —
    either the loop's ok result (crash-resume leg) or a green gate's recoverable
    retry (retry legs). Everything the AC is about — `build_green_prompt`,
    `cwd_preflight`, `_invoke_green_llm`, and the engine's own retry re-entry —
    is production code."""
    real_steps = p5.phase_5_implement_workflow().steps
    bounded = list(real_steps[: _step_index(GREEN_STEP) + 1])
    bounded[0] = replace(bounded[0], execute=index0)
    return _observed_steps(bounded, record)


@pytest.mark.parametrize(
    "leg,extra,is_retry",
    [
        ("a", {"green_test_findings": ["FAILED tests/test_x.py::test_x - assert 1 == 2"]}, True),
        ("b", {"gate_attempts": {"verify_green_lint_rules": 1}}, True),
        ("c", {}, False),
    ],
)
def test_ac24n_in_run_green_retry_must_not_be_swallowed_by_the_marker(
    tmp_path, monkeypatch, leg, extra, is_retry
):
    """AC24n (HIGH — a regression P1 introduced). P1 writes the marker the moment
    GREEN reports COMPLETE, but it is GC'd only in `commit_green_code`. All three
    green gates hand the engine an IN-RUN retry that restarts at
    `build_green_prompt` (`_verify_green_passing:6219-6228` sets
    `green_test_findings` + `retry_from_step: 1`; the lint and typecheck gates go
    through `recoverable_gate.gated_step_result`, whose retry data carries
    `gate_attempts`) with the SAME `red_commit_sha`. If the GH483 skip in
    `_invoke_green_llm` (`:8462-8476`) then matches the marker, GREEN is never
    re-invoked WITH the findings, the tree is unchanged, the same gate fails
    again, and the build burns its cycles to the cap.

    §1y — the whole chain, not one frame. The retry entry is NOT hand-built: a
    real gate-shaped recoverable result is returned at index 0 and the ENGINE
    constructs the re-entry dict (`engine.py:670-686`, control keys stripped) and
    re-enters at `build_green_prompt`. The chain then runs
    `build_green_prompt` -> `cwd_preflight` -> `invoke_green_llm` as production
    code. That matters because `build_green_prompt` READS the findings keys and
    returns a freshly constructed explicit dict (`:8502-8522`) that carries
    neither, and `cwd_preflight` passes it through unchanged — so a discriminator
    that only exists on the retry dict is gone by the time `_invoke_green_llm`
    asks. Asserting at the `_invoke_green_llm` frame alone pins the
    discriminator's contract and never establishes that it is REACHABLE; this
    version asserts the observable outcome (GREEN invoked vs skipped) so it
    survives whatever plumbing carries the fact across the rebuild.

    The marker matches this `red_commit_sha` in ALL THREE legs (precondition), so
    nothing but "is this an in-run retry?" can separate them.

    Legs (a) and (b) FAIL TODAY: the skip fires and the LLM is never reached.
    Leg (c) — a straight-through entry with neither key, i.e. the genuine GH483
    crash-resume — PASSES today and must keep passing: this AC must not become
    "delete the skip"."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, f"ac24n_{leg}", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=2,
    )
    assert p5._read_green_complete_resume(stage.scratchpad, stage.red_shas[2]) is not None, (
        f"AC24n leg {leg!r} fixture precondition FAILED: the marker MUST match "
        f"this `red_commit_sha` in every leg — otherwise the legs differ by the "
        f"marker, not by the retry; actual marker={stage.marker_path.read_text()!r}"
    )
    retry_target = _step_index("build_green_prompt")
    base = _green_llm_prev(stage, 2, {}).data

    def _index0(ctx, prev):  # noqa: ARG001
        if not is_retry:
            # The loop's ok result: a fresh pass at the GREEN half, no findings,
            # no gate spend — the state the GH483 crash-resume exists for.
            return StepResult(
                status="ok", data=dict(base), duration_ms=0, step_name=LOOP_STEP,
            )
        # A green gate's recoverable retry, in the shape `_verify_green_passing`
        # returns at `:6222-6234` / `recoverable_gate.gated_step_result`.
        return StepResult(
            status="error",
            data={**base, **extra, "cycle_count": 1, "retry_from_step": retry_target},
            duration_ms=0, step_name="verify_green_passing",
            error="GREEN tests in groups [py] still failing (fixture: gh1018 AC24n)",
            error_code="E_GREEN_NOT_PASSING",
            recoverable=True,
        )

    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)
    recorded: list = []
    workflow = WorkflowDefinition(
        name=PHASE_5_WORKFLOW_NAME, steps=_green_chain_steps(recorded, _index0)
    )
    engine = WorkflowEngine(EventLog(stage.log_path))
    engine.register(PHASE_5_WORKFLOW_NAME, workflow)
    final, _final_ctx = engine.execute(PHASE_5_WORKFLOW_NAME, stage.ctx, stage.run_id)

    chain = [(r["name"], r["cycle"], r["result"].status, r["result"].error_code) for r in recorded]
    executed = [r["name"] for r in recorded]
    assert "build_green_prompt" in executed, (
        f"AC24n leg {leg!r}: the chain must reach `build_green_prompt` — "
        f"{'the engine retry branch never re-entered' if is_retry else 'the straight chain never ran'}; "
        f"actual chain={chain!r} final {_describe(final)}"
    )
    invoke = next((r for r in recorded if r["name"] == GREEN_STEP), None)
    assert invoke is not None, (
        f"AC24n leg {leg!r}: the chain must reach {GREEN_STEP!r}; actual "
        f"chain={chain!r} final {_describe(final)}"
    )
    result = invoke["result"]
    skipped_events = stage.events.of(SKIP_EVENT)

    if not is_retry:
        assert not spawned, (
            f"AC24n leg {leg!r}: a straight-through entry with no findings and no "
            f"gate spend is the genuine GH483 crash-resume — the GREEN LLM must "
            f"still be SKIPPED, not re-invoked; fence hits={spawned!r} "
            f"{_describe(result)} chain={chain!r}"
        )
        assert result.status == "ok" and isinstance(result.data, dict) and \
            result.data.get("green_complete_resume") is True, (
            f"AC24n leg {leg!r}: expected the unchanged GH483 skip (status ok, "
            f"green_complete_resume True); actual {_describe(result)}"
        )
        assert skipped_events, (
            f"AC24n leg {leg!r}: expected the {SKIP_EVENT!r} event on the genuine "
            f"crash-resume; actual events={stage.events.types()!r}"
        )
        return

    assert invoke["cycle"] == 2, (
        f"AC24n leg {leg!r} fixture precondition FAILED: the engine must have "
        f"RE-ENTERED (cycle 2) rather than run straight through; actual engine "
        f"cycle at {GREEN_STEP!r}={invoke['cycle']!r} chain={chain!r}"
    )
    assert spawned, (
        f"AC24n leg {leg!r}: the run re-entered at `build_green_prompt` carrying "
        f"{sorted(extra)!r} — an IN-RUN green-gate retry, not a crash-resume — so "
        f"GREEN must be RE-INVOKED with the findings. It was not: the invoke path "
        f"was never reached (no LLM was billed, the call site is fenced), which "
        f"means the marker swallowed the retry and the next gate run will see the "
        f"same unchanged tree. actual {_describe(result)} chain={chain!r} events="
        f"{stage.events.types()!r}"
    )
    assert not skipped_events, (
        f"AC24n leg {leg!r}: the GH483 skip must NOT fire on an in-run retry — the "
        f"tree is unchanged, so skipping re-runs the same failing gate until the "
        f"cap; actual events={stage.events.types()!r} chain={chain!r}"
    )
    assert not (isinstance(result.data, dict) and result.data.get("green_complete_resume")), (
        f"AC24n leg {leg!r}: a retry that reached the LLM must not also be "
        f"labelled a completed resume; actual {_describe(result)}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC25n — the recovery re-entry must carry its cycle_count to the green gates
# ═════════════════════════════════════════════════════════════════════════


def test_ac25n_reentry_forwards_the_resolved_cycle_count_to_the_green_gates(tmp_path, monkeypatch):
    """AC25n (MEDIUM). `cycle_count` is in `engine._RETRY_CONTROL_KEYS`, so the
    engine STRIPS it from the forwarded data; the only place it is re-derived is
    `build_green_prompt` (`:8437`), which the recovery route skips — it re-enters
    at `write_green_artifact`. That step then falls back to
    `prev_data.get("cycle_count", 1)` (`:8635`) and every downstream green gate
    (`:6220`, `:6618`, `:7276`) believes the adopted cycle-2 GREEN is cycle 1 — so
    the cap-2 retry logic gets a free extra cycle and the cycle-2-only prompt
    blocks never render.

    Asserted on the SAME real-engine re-entry AC12 drives, not a third harness.

    FAILS TODAY: the data `_write_green_artifact` produces carries
    `cycle_count == 1`."""
    ac = "AC25n"
    resolved_cycle = 2
    stage, recorded, final, spawned = _drive_recovery_through_the_real_engine(
        tmp_path, "ac25n", monkeypatch, ac, resolved_cycle,
    )
    chain = [(r["name"], r["cycle"], r["result"].status, r["result"].error_code) for r in recorded]
    artifact = next((r for r in recorded if r["name"] == ROUTE_TARGET), None)
    assert artifact is not None, (
        f"{ac}: the re-entry must reach {ROUTE_TARGET!r} before its data can be "
        f"judged; actual chain={chain!r} final {_describe(final)}"
    )
    data = artifact["result"].data
    assert isinstance(data, dict), (
        f"{ac}: expected {ROUTE_TARGET!r} to produce dict data; actual "
        f"{_describe(artifact['result'])}"
    )
    assert data.get("cycle_count") == resolved_cycle, (
        f"{ac}: the data {ROUTE_TARGET!r} hands the green gates must carry "
        f"`cycle_count == {resolved_cycle}` — the engine strips `cycle_count` as a "
        f"control key and only `build_green_prompt` re-derives it, which this route "
        f"skips, so the gates otherwise judge the adopted cycle-{resolved_cycle} "
        f"GREEN as cycle 1; actual cycle_count={data.get('cycle_count')!r} "
        f"(engine cycle at that step={artifact['cycle']!r}, data keys="
        f"{sorted(data)!r})"
    )
    assert not spawned, (
        f"{ac}: no LLM invocation may be reached on this path; fence hits={spawned!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC26n — a ctx with no scratchpad is a CONFIGURATION, not a failure
# ═════════════════════════════════════════════════════════════════════════


def test_ac26n_missing_scratchpad_is_not_an_alarm(tmp_path, monkeypatch):
    """AC26n (LOW, false alarm). `_resolve_scratchpad`
    (`phase_workflows_common.py:133-138`) RAISES `ValueError` when
    `org_config.scratchpad_dir` is unset — it does not return None. P1 calls it
    INSIDE its `try`, so a healthy no-scratchpad configuration is caught by the
    `except Exception` and reported as `reason="unexpected:ValueError"` on EVERY
    GREEN completion. An alarm that fires on a supported configuration trains the
    operator to ignore it, which costs exactly the signal AC21n bought.

    FAILS TODAY: the event is emitted."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac26n", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    ctx_no_scratchpad = replace(
        stage.ctx,
        org_config={k: v for k, v in stage.ctx.org_config.items() if k != "scratchpad_dir"},
    )
    try:
        p5._resolve_scratchpad(ctx_no_scratchpad)
    except ValueError:
        pass
    else:
        pytest.fail(
            "AC26n fixture precondition FAILED: `_resolve_scratchpad` must RAISE "
            "ValueError without `org_config.scratchpad_dir` — if it now returns "
            "None this AC is describing a defect that no longer exists"
        )
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    try:
        result = p5._write_green_artifact(
            ctx_no_scratchpad, _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW)
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"AC26n: a ctx without a scratchpad must not make "
            f"`_write_green_artifact` raise; actual {type(exc).__name__}: {exc}"
        )

    assert result.status == "ok" and not spawned, (
        f"AC26n: a COMPLETE GREEN must still succeed without a scratchpad; actual "
        f"{_describe(result)} fence={spawned!r}"
    )
    assert stage.events.of(PERSIST_FAILED_EVENT) == [], (
        f"AC26n: no scratchpad is a supported CONFIGURATION, not an attempted "
        f"write that failed — it must not raise {PERSIST_FAILED_EVENT!r} on every "
        f"GREEN completion; actual events={stage.events.types()!r} payloads="
        f"{[e['payload'] for e in stage.events.of(PERSIST_FAILED_EVENT)]!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC27n — the alarm reports the CYCLE, not the completed count
# ═════════════════════════════════════════════════════════════════════════


def test_ac27n_alarm_reports_the_cycle_not_the_completed_count(tmp_path, monkeypatch):
    """AC27n (LOW). The alarm's `cycle` is computed
    `prev_data.get("cycle_count", prev_data.get("cycle"))` (`:1860`), preferring
    the COMPLETED count over the cycle. On the recovery route the two genuinely
    differ: P3 sets `cycle_count = max(cycle - 1, 0)` alongside `cycle`, so at
    cycle 2 the alarm reports 1 — off by one against every other cycle field in
    this lot, `orphan_green_recovery_routed{cycle}` included. Two events about the
    same entry that disagree about which cycle it is are worse than one.

    The failure is staged the uid-independent AC21n(b) way (a regular FILE at the
    `resume` path), so the alarm is guaranteed to fire and only its payload is
    under test.

    FAILS TODAY: the emitted `cycle` is 1."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac27n", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    (stage.scratchpad / "resume").write_text("not a directory\n", encoding="utf-8")
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    prev = _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW)
    # The routed shape, verbatim: `cycle` is the resolved cycle and `cycle_count`
    # is P3's `max(cycle - 1, 0)`.
    prev.data["cycle"] = 2
    prev.data["cycle_count"] = 1

    result = p5._write_green_artifact(stage.ctx, prev)

    assert result.status == "ok" and not spawned, (
        f"AC27n fixture precondition FAILED: the artifact write must still "
        f"succeed; actual {_describe(result)} fence={spawned!r}"
    )
    failed = stage.events.of(PERSIST_FAILED_EVENT)
    assert failed, (
        f"AC27n fixture precondition FAILED: the alarm must fire so its payload "
        f"can be judged; actual events={stage.events.types()!r}"
    )
    payload = failed[0]["payload"]
    assert payload.get("cycle") == 2, (
        f"AC27n: the alarm must report the CYCLE (2 — the same number "
        f"`orphan_green_recovery_routed` reports), not the completed count "
        f"`cycle_count` (1); actual payload={payload!r} "
        f"(prev cycle={prev.data['cycle']!r} cycle_count={prev.data['cycle_count']!r})"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC28n — a git failure is not a docs-only lot
# ═════════════════════════════════════════════════════════════════════════


def test_ac28n_git_failure_during_detection_is_an_alarm_not_silence(tmp_path, monkeypatch):
    """AC28n (HIGH). `_detect_green_complete_resume` swallows EVERY
    `git_diff_files` exception and returns `[]` (`:1780-1783`), and P1 reads `[]`
    as the healthy "nothing to record". So a transient git failure — index.lock
    contention, an unresolvable sha — silently produces no marker and no event,
    re-arming GH1018 with a different cause and no trace at either end.

    The distinguishing evidence P1 already holds is on ITS side of the detector:
    the spec allowlist is NON-empty and `red_commit_sha` is present, so there WAS
    something to record. That is what separates this from AC23n's two legs (gate
    off, and an unparseable §5 Files list), which must stay silent.

    The git failure is staged deterministically (§1i) by pointing the resolved
    `git_cwd` at a directory that is NOT a git repository (verified with a real
    `git rev-parse`), so ALL THREE commands `_GitDiffSubprocess.diff_files` runs
    fail — `diff <sha> HEAD`, `diff HEAD` and `ls-files --others`. An unresolvable
    sha alone is NOT enough: only the first of those three needs the sha, and the
    `ls-files --others` leg still reports the untracked GREEN, so the detector
    would happily return a manifest. `red_commit_sha` stays present and real, so
    the ONLY thing missing is git's answer.

    FAILS TODAY: no event is emitted."""
    manifest = ["src/module.py"]
    stage = _stage(
        tmp_path, "ac28n", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    assert p5._parse_spec_files_allowlist(stage.spec_path), (
        f"AC28n fixture precondition FAILED: the allowlist must be NON-empty — "
        f"that is what makes this a git failure rather than a docs-only lot; "
        f"actual {p5._parse_spec_files_allowlist(stage.spec_path)!r}"
    )
    not_a_repo = tmp_path / "ac28n_not_a_repo"
    not_a_repo.mkdir(parents=True, exist_ok=True)
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=not_a_repo, capture_output=True, text=True, check=False,
    )
    assert toplevel.returncode != 0, (
        f"AC28n fixture precondition FAILED: {not_a_repo} must NOT be inside a git "
        f"repository (git climbs upward, so a tmp dir under a checkout would still "
        f"answer); actual `git rev-parse --show-toplevel` rc={toplevel.returncode} "
        f"stdout={toplevel.stdout.strip()!r}"
    )
    ctx_no_git = replace(
        stage.ctx, org_config={**stage.ctx.org_config, "git_cwd": str(not_a_repo)}
    )
    assert p5._resolve_git_cwd(ctx_no_git, None) == str(not_a_repo), (
        f"AC28n fixture precondition FAILED: the production resolver must return "
        f"the staged root; actual {p5._resolve_git_cwd(ctx_no_git, None)!r}"
    )
    prev = _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW)
    assert prev.data.get("red_commit_sha") == stage.red_shas[2], (
        f"AC28n fixture precondition FAILED: `red_commit_sha` must be PRESENT — "
        f"the missing piece is git's answer, not the sha; actual "
        f"{prev.data.get('red_commit_sha')!r}"
    )
    assert p5._detect_green_complete_resume(dict(prev.data), str(not_a_repo)) == [], (
        f"AC28n fixture precondition FAILED: the detector must yield NO manifest "
        f"when git cannot answer the diff (the error it swallows at `:1780-1783`); "
        f"actual {p5._detect_green_complete_resume(dict(prev.data), str(not_a_repo))!r}"
    )
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    result = p5._write_green_artifact(ctx_no_git, prev)

    assert result.status == "ok" and not spawned, (
        f"AC28n: a COMPLETE GREEN must still succeed when detection fails; actual "
        f"{_describe(result)} fence={spawned!r}"
    )
    assert not stage.marker_path.exists(), (
        f"AC28n: no marker can be written without a manifest; actual "
        f"{stage.marker_path.read_text()!r}"
    )
    failed = stage.events.of(PERSIST_FAILED_EVENT)
    assert failed, (
        f"AC28n: a NON-empty allowlist plus a present `red_commit_sha` and an "
        f"EMPTY manifest means the detector failed, not that there was nothing to "
        f"record — that must be visible, or GH1018 re-arms with no trace at either "
        f"end; actual events={stage.events.types()!r}"
    )
    reason = str(failed[0]["payload"].get("reason") or "").lower()
    assert any(token in reason for token in ("manifest", "paths", "empty", "detect")), (
        f"AC28n: the alarm's `reason` must name the empty manifest so the cause is "
        f"readable without re-running the build; actual payload="
        f"{failed[0]['payload']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC29n — a marker with fewer digests than paths is a silent dead end
# ═════════════════════════════════════════════════════════════════════════


def test_ac29n_incomplete_digests_are_reported_not_silently_written(tmp_path, monkeypatch):
    """AC29n (HIGH). `_persist_green_complete_resume` (`:1826-1831`) OMITS any path
    whose sha256 cannot be read and then reports success, so the marker lands with
    fewer digests than paths. The recovery predicate later refuses that lot at the
    digest check (`recorded` is None -> `return None`) — forever, with no trace at
    either end: the write said nothing and the decline says nothing.

    The unreadable path is staged uid-independently as a DANGLING SYMLINK (a real
    shape: a symlink whose target the GREEN never created). `read_bytes` raises
    for root too, so this can never skip.

    FAILS TODAY: the write is silent."""
    manifest = ["src/module.py", "src/other.py"]
    stage = _stage(
        tmp_path, "ac29n", monkeypatch, manifest=manifest, dirty_paths=manifest,
        prev_cycle=2, prev_red_cycle=2, sentinel_cycles=(), marker_red_cycle=None,
    )
    broken = stage.repo / "src" / "other.py"
    broken.unlink()
    os.symlink(str(stage.repo / "src" / "does-not-exist.py"), str(broken))
    assert broken.is_symlink() and not broken.exists(), (
        f"AC29n fixture precondition FAILED: expected a DANGLING symlink at "
        f"{broken}; is_symlink={broken.is_symlink()} exists={broken.exists()}"
    )
    assert p5._sha256_of_file(broken) is None, (
        f"AC29n fixture precondition FAILED: the production digest helper must be "
        f"unable to hash the dangling symlink; actual {p5._sha256_of_file(broken)!r}"
    )
    prev = _green_artifact_prev(stage, 2, GREEN_COMPLETE_RAW)
    detected = p5._detect_green_complete_resume(dict(prev.data), str(stage.repo))
    assert "src/other.py" in detected, (
        f"AC29n fixture precondition FAILED: the unhashable path must be INSIDE "
        f"the detected manifest — that is the whole defect; actual detected="
        f"{detected!r}"
    )
    spawned: list = []
    _fence_billed_llm(monkeypatch, spawned)

    result = p5._write_green_artifact(stage.ctx, prev)

    assert result.status == "ok" and not spawned, (
        f"AC29n: the GREEN itself must still succeed; actual {_describe(result)} "
        f"fence={spawned!r}"
    )
    failed = stage.events.of(PERSIST_FAILED_EVENT)
    assert failed, (
        f"AC29n: a marker with fewer digests than paths cannot satisfy the "
        f"recovery predicate, so writing one and reporting success is a silent "
        f"dead end — the incomplete write must be reported; actual events="
        f"{stage.events.types()!r}, marker on disk="
        f"{stage.marker_path.read_text() if stage.marker_path.exists() else 'ABSENT'}"
    )
    reason = str(failed[0]["payload"].get("reason") or "").lower()
    assert any(token in reason for token in ("digest", "unhashable", "incomplete")), (
        f"AC29n: the alarm's `reason` must name the incomplete digests — "
        f"'the marker is there but one path has no digest' is not derivable from "
        f"a generic reason; actual payload={failed[0]['payload']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC30n — every decline must say WHY
# ═════════════════════════════════════════════════════════════════════════


def test_ac30n_declines_emit_a_distinguishing_reason(tmp_path, monkeypatch):
    """AC30n (HIGH). `_orphan_green_recovery_result` has ten traceless
    `return None` exits, and the caller's `red_dirty_tree_blocked` carries only
    `paths`/`n`. So the operator staring at `E_RED_WORKTREE_DIRTY` cannot tell "no
    GREEN ever completed here" from "the GREEN is there but its cycle-3 sidecar
    entry does not link" — which is precisely the diagnosis this whole lot was
    spent reconstructing from source, because the live run's scratchpad had been
    reaped.

    Two declines with genuinely different causes must therefore carry DIFFERENT
    reason codes on an `orphan_green_recovery_declined` event, and the terminal
    result must be unchanged in code, `recoverable` and wording (the refusal is
    not being softened — only explained).

    FAILS TODAY: no such event exists."""
    manifest = ["src/module.py"]

    # Case A — no marker at all: nothing ever recorded a completed GREEN here.
    case_a = _stage(
        tmp_path, "ac30n_a", monkeypatch, manifest=manifest, dirty_paths=manifest,
        marker_red_cycle=None,
    )
    assert not case_a.marker_path.exists(), (
        "AC30n case A fixture precondition FAILED: no marker may exist"
    )
    result_a = _call_guard_without_raising("AC30n case A", case_a)
    _assert_terminal_refusal("AC30n case A", case_a, result_a, manifest)
    declined_a = case_a.events.of(DECLINED_EVENT)
    assert len(declined_a) == 1, (
        f"AC30n case A: expected exactly one {DECLINED_EVENT!r} naming why the "
        f"recovery declined (no marker); actual events={case_a.events.types()!r}"
    )
    reason_a = str(declined_a[0]["payload"].get("reason") or "")
    assert reason_a, (
        f"AC30n case A: the decline event must carry a non-empty `reason`; actual "
        f"payload={declined_a[0]['payload']!r}"
    )

    # Case B — the marker and the sentinel are both there, but the cycle-2
    # sidecar entry does not link (the AC7 leg-2 state).
    case_b = _stage(
        tmp_path, "ac30n_b", monkeypatch, manifest=manifest, dirty_paths=manifest,
        sidecar_cycles=(1, 2), sidecar_sha_overrides={2: UNLINKED_SHA},
    )
    _assert_payload_linkage("AC30n case B", case_b, 2, links=True)
    result_b = _call_guard_without_raising("AC30n case B", case_b)
    _assert_terminal_refusal("AC30n case B", case_b, result_b, manifest)
    declined_b = case_b.events.of(DECLINED_EVENT)
    assert len(declined_b) == 1, (
        f"AC30n case B: expected exactly one {DECLINED_EVENT!r} naming why the "
        f"recovery declined (no linkable cycle); actual events="
        f"{case_b.events.types()!r}"
    )
    reason_b = str(declined_b[0]["payload"].get("reason") or "")
    assert reason_b, (
        f"AC30n case B: the decline event must carry a non-empty `reason`; actual "
        f"payload={declined_b[0]['payload']!r}"
    )

    assert reason_a != reason_b, (
        f"AC30n: the two declines have genuinely different causes — 'no marker was "
        f"ever written' (case A) and 'the marker is there but no cycle links to it' "
        f"(case B) — and one reason code for both is no more diagnostic than the "
        f"silence it replaced; actual reason_a={reason_a!r} reason_b={reason_b!r}"
    )
