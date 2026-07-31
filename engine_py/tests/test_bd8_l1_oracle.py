"""RED tests for bd#8 — BD-L1, oracle freeze-and-verify (R1.1-R1.5).

Frozen spec: engine_py/conformance/ORACLE_SPEC.md (FROZEN v5, base 2b6589f).
v5 is the round-2 post-gate freeze.  This file is a re-cut against v5, not a
patch of the v4 RED: the gate's R2-MAJOR-1 falsified `[bd8:1]`'s premise, so
the SET SOURCE moved and every fixture built on the old source moved with it.

WHAT MOVED, AND WHY (D1(a), spec §7 G9):
  * v1-v4 took the oracle set from `phase_artifacts.written`.  MEASURED on
    this engine, on the production topology, that payload is:
        {"phase": "phase_45_spec", "read": [], "read_tracking":
         "declared-only", "write_tracking": "git-delta", "written": []}
    with both spec documents sitting in the scratchpad and the git worktree
    clean.  `written` is EMPTY and flagged `"git-delta"` — indistinguishable
    from a phase that wrote nothing.  `self._written` is fed only by the git
    delta of `org_config["git_cwd"]` (engine.py:493; the manifest at :497-508
    NARROWS and DEFERs, never adds), while both spec workflows write under
    `org_config["scratchpad_dir"]` (phase_45_spec.py:372-376, 1005-1007).
  * So the set is now the non-recursive listing of `<scratchpad_dir>/specs`,
    files only, recorded relative to `scratchpad_dir` (`[bd8:1]`, `[bd8:3]`).
    The fixture topology below MIRRORS PRODUCTION for exactly this reason:
    the step writes into the scratchpad while `git_cwd` points at a git repo,
    which is what made the v4 fixture blind to the defect.
  * `phase_artifacts.written` survives as `written_crosscheck` (`[bd8:1b]`) —
    recorded, asserted EMPTY here, and deliberately not a gate.  bd#36 is the
    lot that would make it a source; this lot does not wait on it.
  * **AC-10 changed subject, it was not struck.**  "Not a directory listing"
    became the required behaviour, so AC-10 now forces the NAMESPACE BOUNDARY:
    not outside the document directory (leg 1), not outside this run's
    scratchpad (leg 2), plus the control leg that stops "does not go outside"
    from being satisfied by freezing nothing.
  * **AC-9a is STRUCK** and **AC-16/`[bd8:4a]` re-cut** — their subject
    (`written`) stopped being the source, and in their v4 form they would have
    refused every healthy build.  This is recorded in the spec, not decided
    here.

COLLECTION SAFETY (§1q / D1CF5FDF).  `conformance.oracle` does NOT exist on
this base.  Every reference to it is DEFERRED into a test body, so this
module COLLECTS cleanly pre-GREEN and FAILS at assert/call time.  There is no
module-level `sys.path` mutation and no `from conftest import` — the engine_py
conftest injects the roots at conftest-import time (tests/conftest.py:36-49).

EXTERNAL PROVENANCE (§0.2).  The four error codes and three category tokens
are pinned as LITERALS citing ORACLE_SPEC §5 and AC-4, never imported from
`error_codes.ERROR_CODES` (this lot writes those entries — pinning against
them would be the §0.1 subtype (3) vacuum).  Every expected digest is
recomputed here from the bytes this test wrote, per `[bd8:2]`/`[bd8:2b]`.

HARNESS SEAMS (measured, not assumed):
  * `lib/phase_sentinel.execute_engine` calls `workflows.register_all(eng)` at
    CALL time (:171-175), so monkeypatching it installs fixture workflows into
    a REAL engine run.  `WorkflowEngine.register` raises on duplicates
    (engine.py:228-231), so the registrar registers only fixtures.
  * Every fixture step SURVIVES a missing `git_cwd` or `scratchpad_dir`
    (gate E16): `engine.py:468-470` does not wrap `step.execute`, so a raise
    escapes to `run.py:224-226` as `E_NOT_REGISTERED`/rc 2 — a phase that
    CRASHED, which `[bd8:6]` never freezes after, making the AC unreachable
    by any conformant GREEN.
  * Re-entry is forced through `org_config`, which IS inside the phase-sentinel
    ctx hash (`_ctx_cfg_sha8`, phase_sentinel.py:78-85).  `question` is NOT —
    `WorkflowContext` has no `task_description` field — which is why the v4
    RED's re-entry measured the sentinel cache instead of a second `execute()`.
  * The event log and the sentinel store live OUTSIDE both the repo and the
    scratchpad, so neither can join the oracle set or the git delta.
  * `init_dbos()` returns immediately under the default `native` backend
    (dbos_setup.py:122-123).  No harness primitive this file runs on is
    sabotaged ([G22:2]).

DECLARED PRE-PASSING (§0.6) — measured; see the block above the test bodies.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers.git_repo import init_repo

_ENGINE_ROOT = Path(__file__).resolve().parent.parent

# ── Pinned literals (ORACLE_SPEC §5, AC-4) ───────────────────────────────
E_ORACLE_MUTATED = "E_ORACLE_MUTATED"
E_ORACLE_UNFROZEN = "E_ORACLE_UNFROZEN"
E_ORACLE_INDETERMINATE = "E_ORACLE_INDETERMINATE"
E_ORACLE_AMENDMENT_UNREASONED = "E_ORACLE_AMENDMENT_UNREASONED"

TOKEN_CONTENT = "mutated:content"
TOKEN_ADDED = "mutated:added"
TOKEN_REMOVED = "mutated:removed"
ALL_TOKENS = (TOKEN_CONTENT, TOKEN_ADDED, TOKEN_REMOVED)

# Registry names (workflows/__init__.py:34-36).
ORACLE_WORKFLOW = "phase_45_spec"
ORACLE_WORKFLOW_LITE = "phase_45_spec_lite"
IMPL_WORKFLOW = "phase_5_implement"
UNMAPPED_WORKFLOW = "phase_2_explore"

FROZEN_EVENT = "oracle_frozen"
AMENDED_EVENT = "oracle_amended"

# `[bd8:1]`: the document directory, relative to scratchpad_dir.  The name is
# external provenance: phase_45_spec.py:330-331 pins SPEC_DOC_RELPATH and
# REVIEW_DOC_RELPATH under "specs/".
DOC_DIR = "specs"
ORACLE_FILES = [f"{DOC_DIR}/build-spec.md", f"{DOC_DIR}/build-plan-review.md",
                f"{DOC_DIR}/notes.md"]
LOOSE_IN_SCRATCHPAD = "loose-note.md"          # AC-10 leg 1: outside DOC_DIR
WITNESS = "impl-witness.txt"                    # AC-2: proof the phase ran


# ─────────────────────────────────────────────────────────────────────────
# Independent digests — `[bd8:2]` / `[bd8:2b]`, over bytes this test wrote
# ─────────────────────────────────────────────────────────────────────────

def expected_digest(scratch: Path, relpaths: list[str]) -> str:
    lines = [
        f"{rel}\0{hashlib.sha256((scratch / rel).read_bytes()).hexdigest()}"
        for rel in sorted(relpaths)
    ]
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def expected_scope(relpaths: list[str]) -> list[str]:
    return sorted({str(Path(rel).parent) for rel in relpaths})


def expected_scope_digest(scratch: Path, relpaths: list[str]) -> str:
    """`[bd8:2b]` exactly as pinned: non-recursive, REGULAR FILES ONLY,
    `<reldir>\\0<names sorted, "\\n"-joined>`, lines "\\n"-joined, UTF-8."""
    lines = []
    for reldir in expected_scope(relpaths):
        names = sorted(p.name for p in (scratch / reldir).iterdir() if p.is_file())
        lines.append(f"{reldir}\0" + "\n".join(names))
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# Fixture topology — MIRRORS PRODUCTION (this is the point)
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def world(tmp_path: Path):
    """repo/ (git, git_cwd) + scratchpads/<run>/ (scratchpad_dir) + logs/.

    Deliberately the production shape measured in `[bd8:2a]`: documents go to
    the scratchpad, `git_cwd` is a real git repo, and the two are different
    trees.  A fixture that wrote into `git_cwd` — as the v4 RED did — cannot
    see G9 at all.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(str(repo))
    (repo / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

    roots = tmp_path / "scratchpads"
    scratch = roots / "run-this"
    (scratch / DOC_DIR).mkdir(parents=True)
    sibling = roots / "run-other"          # AC-10 leg 2: another namespace
    (sibling / DOC_DIR).mkdir(parents=True)

    logs = tmp_path / "logs"
    logs.mkdir()

    class W:
        pass
    w = W()
    w.repo, w.scratch, w.sibling, w.roots = repo, scratch, sibling, roots
    w.log = logs / "events.jsonl"
    return w


def read_events(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def events_of(path: Path | None, event_type: str) -> list[dict]:
    return [e for e in read_events(path) if e.get("event_type") == event_type]


def member_paths(payload: dict) -> list[str]:
    return sorted(m["path"] if isinstance(m, dict) else m for m in payload["members"])


# ─────────────────────────────────────────────────────────────────────────
# Fixture workflows.  EVERY step survives a missing git_cwd / scratchpad_dir.
# ─────────────────────────────────────────────────────────────────────────

def _scratch_of(context) -> Path | None:
    raw = (getattr(context, "org_config", None) or {}).get("scratchpad_dir")
    return Path(raw) if raw else None


def _writer_step(relpaths: list[str], unreadable: list[str] | None = None):
    """Writes `relpaths` under scratchpad_dir — what the real spec phase does.

    Content varies with `org_config['bd8_content_tag']` so an amendment yields
    a genuinely different digest, and because `org_config` is inside the
    phase-sentinel ctx hash, varying it is also what forces a real second
    `execute()` (`[bd8:10a]`/`[bd8:10b]`).
    """
    from contracts import StepResult  # noqa: PLC0415 — deferred (§1q)

    def _exec(context, prev):
        cfg = getattr(context, "org_config", None) or {}
        tag = cfg.get("bd8_content_tag", "v1")
        sp = _scratch_of(context)
        if sp is None:
            # AC-16's no-scratchpad leg runs here on purpose and the step must
            # SUCCEED: a crashing step is a crashed phase, which `[bd8:6]`
            # never freezes after, so no conformant GREEN could reach
            # E_ORACLE_INDETERMINATE (gate E16).
            return StepResult(status="ok", data=None, duration_ms=1,
                              step_name="bd8_write")
        for rel in relpaths:
            p = sp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content of {rel} at {tag}\n")
        for rel in unreadable or []:
            os.chmod(sp / rel, 0o000)
        return StepResult(status="ok", data={"written": relpaths}, duration_ms=1,
                          step_name="bd8_write")

    return _exec


def _impl_step(mutate: str | None = None, add: str | None = None,
               status: str = "ok"):
    """The implementing phase.  Always drops a WITNESS first (AC-2 reads its
    absence as proof the entry verify refused before `execute()`), then
    optionally plays ADV-1 / ADV-2 from INSIDE the phase (AC-2b / AC-3b).

    `status="error"` exercises `[bd8:6]`'s "regardless of the phase's own
    outcome": a phase that failed for an unrelated reason may still have
    mutated the oracle.
    """
    from contracts import StepResult  # noqa: PLC0415 — deferred (§1q)

    def _exec(context, prev):
        sp = _scratch_of(context)
        if sp is not None:
            (sp / WITNESS).write_text("the implementing phase ran\n")
            if mutate:
                p = sp / mutate
                p.write_bytes(p.read_bytes().replace(b"content", b"CONTENT"))
            if add:
                (sp / add).parent.mkdir(parents=True, exist_ok=True)
                (sp / add).write_text("added by the implementing phase\n")
        if status == "error":
            return StepResult(status="error", data=None, duration_ms=1,
                              step_name="bd8_impl", error="unrelated failure",
                              error_code="E_SOMETHING_ELSE", recoverable=False)
        return StepResult(status="ok", data=None, duration_ms=1, step_name="bd8_impl")

    return _exec


def install_fixture_workflows(monkeypatch, oracle_writes=None, unreadable=None,
                              impl=None, extra=None) -> None:
    from contracts import StepContract, WorkflowDefinition  # noqa: PLC0415
    import workflows  # noqa: PLC0415 — deferred

    writes = ORACLE_FILES if oracle_writes is None else oracle_writes

    def _register_all(engine) -> None:
        for name in (ORACLE_WORKFLOW, ORACLE_WORKFLOW_LITE):
            engine.register(name, WorkflowDefinition(
                name=name,
                steps=[StepContract(name="bd8_write",
                                    execute=_writer_step(writes, unreadable))]))
        engine.register(IMPL_WORKFLOW, WorkflowDefinition(
            name=IMPL_WORKFLOW,
            steps=[StepContract(name="bd8_impl", execute=impl or _impl_step())]))
        engine.register(UNMAPPED_WORKFLOW, WorkflowDefinition(
            name=UNMAPPED_WORKFLOW,
            steps=[StepContract(name="bd8_other", execute=_writer_step(ORACLE_FILES))]))
        for name, wf in (extra or {}).items():
            engine.register(name, wf)

    monkeypatch.setattr(workflows, "register_all", _register_all)


def run_phase(workflow: str, w, run_id: str, *, log=True, scratch=True,
              git_cwd=True, org_extra: dict | None = None):
    """Invoke run.main() for one phase → (rc, parsed_stdout_json, raw)."""
    import run as run_module  # noqa: PLC0415 — deferred (§1q)

    org: dict = {}
    if git_cwd:
        org["git_cwd"] = str(w.repo)
    if scratch:
        org["scratchpad_dir"] = str(w.scratch)
    org.update(org_extra or {})
    ctx = json.dumps({"tenant_id": "bd8", "session_id": "bd8-session",
                      "question": "bd8", "org_config": org})
    argv = ["run.py", "--workflow", workflow, "--ctx-json", ctx, "--run-id", run_id]
    if log:
        argv += ["--event-log", str(w.log)]

    out, err = io.StringIO(), io.StringIO()
    rc = 1
    with patch("sys.argv", argv), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = run_module.main()
        except SystemExit as e:  # pragma: no cover
            rc = int(e.code or 0)
    raw = out.getvalue() + err.getvalue()
    try:
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {}
    return rc, payload, raw


def freeze(w, run_id, **kw):
    return run_phase(ORACLE_WORKFLOW, w, run_id, **kw)


def verify(w, run_id, **kw):
    return run_phase(IMPL_WORKFLOW, w, run_id, **kw)


def assert_code(payload: dict, raw: str, expected: str, ctx: str) -> None:
    """`[bd8:6a]`: the refusal is the PARSED `error_code`, never a word in the
    output.  `E_RUNNER` is named because that is what `run.py:233-240` turns an
    escaping exception into — the single most likely way a GREEN gets this
    wrong, and invisible to a stdout-substring assertion."""
    got = payload.get("error_code")
    assert got == expected, (
        f"{ctx}: expected error_code == {expected!r}, got {got!r}.\n"
        f"status={payload.get('status')!r} error={payload.get('error')!r}\n"
        + ("HINT: E_RUNNER means the refusal was RAISED out of main()'s try block "
           "instead of reported on the StepResult (`[bd8:6a]`).\n"
           if got == "E_RUNNER" else "")
        + f"raw: {raw[:600]}"
    )


def assert_token(payload: dict, expected: str, ctx: str) -> None:
    msg = f"{payload.get('error') or ''} {payload.get('suggestion') or ''}"
    present = [t for t in ALL_TOKENS if t in msg]
    assert present == [expected], (
        f"{ctx}: expected exactly the category token {expected!r}; found {present!r} "
        f"in {msg!r}.  AC-4 distinguishes the three cases by token, not by the path "
        f"each happens to name."
    )


# ═════════════════════════════════════════════════════════════════════════
# DECLARED PRE-PASSING (§0.6) — MEASURED on base b5df16e (v5), not predicted.
#
#   MEASURED: 41 collected, **28 failed, 13 passed**.  Every passing test is
#   named here with its category.  A pre-passing test is legitimate ONLY as a
#   control, a fence or a shield — never as a requirement-bearing forcing leg,
#   and none of the 13 is one.
#
#   CONTROLS — 6.  Must pass BEFORE and AFTER.  Today they pass vacuously
#   (nothing verifies at all); post-GREEN they pass only if the verify
#   discriminates.  They are what stops a GREEN from satisfying every
#   adversary by refusing everything:
#     * AC-3a   — unchanged tree verifies and passes.
#     * AC-3c   — activity outside the scope must not trip the verify.
#     * AC-9b   — the §9 scope limits, both directions (a file in a
#                 SUBdirectory, and a new empty subdirectory, must not trip
#                 it).  A limit no test measures is a claim, not a limit.
#     * AC-13   — a workflow in neither mapping set is untouched (blast
#                 radius).  Non-vacuous: the fixture WRITES the same files.
#     * AC-15   — BOTH control legs: a logless non-mapped workflow, and a
#                 logless ORACLE phase (`[bd8:6b]`).
#
#   NOT pre-passing, though they are controls: the control legs of **AC-10**
#   and **AC-16**, and both legs of **AC-8b**, FAIL today.  Each asserts a
#   property OF a freeze — that the run's own directory froze with exactly its
#   files, that a non-empty directory freezes at all, that a resume left the one
#   freeze alone — and no freeze exists pre-GREEN.  They become controls the
#   moment GREEN lands; listing them as pre-passing would have been a false
#   declaration, and the measurement is what caught it.
#
#   FENCES — assert an absence, or an already-true property, that cannot yet
#   be violated:
#     * AC-6    — R1.1's ordering is already true on this base (spec §0).
#                 `test_ac6b`, which places the FREEZE between the phases, is
#                 the part this lot must make true, and it fails.
#     * AC-7    — adapter-observed; counts events rather than comparing
#                 constants, but the property already holds — which is exactly
#                 why §9 labels R1.2 `adapter-observed`, not `enforced`.
#     * AC-14a/b/c — §8 scope: not in engine.py's finally, not in workflows/,
#                 nowhere outside run.py + conformance/.
#
#   SHIELDS — already-true properties this lot must not break:
#     * AC-11b  — ERROR_CODES.md byte-equal to `--markdown` output.
#     * AC-12b  — `conformance` is a real package, so AC-12's import cannot
#                 resolve vacuously as a namespace package.
#
#   ONE HALF-ASSERTION inside a FAILING test is pre-passing and load-bearing:
#   AC-11's `error_codes.main(["--check"]) == 0`.  It is the shield for
#   `[bd8:11]` (TRAP 1): the membership half FAILS today, and registering the
#   four codes WITHOUT raising them in production flips this half to 1 with
#   four `DEAD` lines (`HARVEST_EXCLUDE_DIRS` contains `"tests"`,
#   error_codes.py:23).  Both halves are in ONE test; early registration does
#   not satisfy it.
# ═════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────
# AC-1 (R1.3) — the freeze event
# ─────────────────────────────────────────────────────────────────────────

class TestFreeze:
    def test_ac1_freeze_emits_one_event_with_both_digests(self, monkeypatch, world):
        """AC-1.  One `oracle_frozen`; 3 members relative to `scratchpad_dir`;
        `digest` = `[bd8:2]`, `scope_digest` = `[bd8:2b]`, both recomputed
        here; and `written_crosscheck` present and EMPTY (`[bd8:1b]`)."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = freeze(world, "run-ac1")
        assert rc == 0, f"oracle phase must succeed; rc={rc} raw={raw}"

        frozen = events_of(world.log, FROZEN_EVENT)
        assert len(frozen) == 1, (
            f"AC-1: expected exactly one {FROZEN_EVENT}, got {len(frozen)}. Types: "
            f"{sorted({e['event_type'] for e in read_events(world.log)})}"
        )
        p = frozen[0]["payload"]
        assert frozen[0]["run_id"] == "run-ac1"
        assert p["phase"] == ORACLE_WORKFLOW
        assert p["member_count"] == 3 == len(p["members"]), (
            f"AC-1: member_count {p['member_count']} / len(members) "
            f"{len(p['members'])} — expected 3 and agreeing"
        )
        assert member_paths(p) == sorted(ORACLE_FILES), (
            f"AC-1: members {p['members']!r} are not the three sorted paths "
            f"relative to scratchpad_dir {sorted(ORACLE_FILES)!r} (`[bd8:3]`)"
        )
        assert p["digest"] == expected_digest(world.scratch, ORACLE_FILES)
        assert sorted(p["scope"]) == expected_scope(ORACLE_FILES)
        assert p["scope_digest"] == expected_scope_digest(world.scratch, ORACLE_FILES)

        assert "written_crosscheck" in p, (
            "`[bd8:1b]`: the freeze must record the phase's own "
            "phase_artifacts.written as a cross-check, so the G9 divergence stays "
            "visible in the log instead of being silently designed around"
        )
        assert p["written_crosscheck"] == [], (
            f"`[bd8:1b]`/G9: on this engine's production topology the cross-check is "
            f"EMPTY while the members are not — that divergence IS the finding. "
            f"Got {p['written_crosscheck']!r}. If this ever becomes non-empty, "
            f"bd#36 landed and `[bd8:1]` should be revisited."
        )

    def test_ac1b_per_member_digests_are_bare_hex(self, monkeypatch, world):
        """`[bd8:2]`: per-member `digest` is the BARE lowercase hex sha256."""
        install_fixture_workflows(monkeypatch)
        freeze(world, "run-ac1b")
        p = events_of(world.log, FROZEN_EVENT)[0]["payload"]
        got = {m["path"]: m["digest"] for m in p["members"]}
        want = {rel: hashlib.sha256((world.scratch / rel).read_bytes()).hexdigest()
                for rel in ORACLE_FILES}
        assert got == want, f"`[bd8:2]`: got={got!r} want={want!r}"

    def test_ac1c_the_lite_oracle_workflow_also_freezes(self, monkeypatch, world):
        """`[bd8:7]`.  `phase_45_spec_lite` is in the oracle-authoring set; the
        OSS driver runs it directly before `phase_5_implement`, so a mapping
        that omits it kills every SIMPLE-tier build with `E_ORACLE_UNFROZEN`."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = run_phase(ORACLE_WORKFLOW_LITE, world, "run-lite")
        assert rc == 0, f"lite oracle phase must succeed; rc={rc} raw={raw}"
        frozen = events_of(world.log, FROZEN_EVENT)
        assert len(frozen) == 1, (
            f"`[bd8:7]`: {ORACLE_WORKFLOW_LITE} emitted {len(frozen)} freeze events"
        )
        assert frozen[0]["payload"]["phase"] == ORACLE_WORKFLOW_LITE

        rc2, payload2, _ = verify(world, "run-lite")
        assert rc2 == 0, (
            f"`[bd8:7]`: the implementing phase after a LITE freeze must pass; "
            f"rc={rc2} error_code={payload2.get('error_code')!r}"
        )

    def test_ac1d_freeze_then_verify_leaves_exactly_one_frozen_event(
        self, monkeypatch, world
    ):
        """AC-1, extended.  A GREEN that ALSO freezes at the implementing
        phase's exit passes everything else and corrupts the "LAST freeze
        event" lookup (`[bd8:9]`) for any third phase in the same log."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac1d")[0] == 0
        assert verify(world, "run-ac1d")[0] == 0
        frozen = events_of(world.log, FROZEN_EVENT)
        assert len(frozen) == 1, (
            f"AC-1: the pair left {len(frozen)} {FROZEN_EVENT} events; only the "
            "oracle phase may freeze"
        )
        assert frozen[0]["payload"]["phase"] == ORACLE_WORKFLOW


# ─────────────────────────────────────────────────────────────────────────
# AC-2 / AC-3 / AC-4 — adversaries BETWEEN phases (the ENTRY verify)
# ─────────────────────────────────────────────────────────────────────────

class TestAdversariesBetweenPhases:
    def _frozen_then(self, monkeypatch, world, run_id, mutate, impl=None):
        install_fixture_workflows(monkeypatch, impl=impl)
        rc, _, raw = freeze(world, run_id)
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"
        assert events_of(world.log, FROZEN_EVENT), "precondition: nothing frozen"
        mutate()
        return verify(world, run_id)

    def test_ac2_adv1_entry_verify_refuses_before_the_phase_runs(
        self, monkeypatch, world
    ):
        """AC-2 (ADV-1, entry) — INCLUDING the witness half.

        The code alone does not distinguish the two call sites (the exit verify
        produces it too).  What distinguishes them is that the implementing
        phase's steps NEVER RAN.  Without this, an EXIT-ONLY GREEN passes AC-2,
        AC-3, AC-4, AC-5 and AC-15 unchanged."""
        target = f"{DOC_DIR}/build-spec.md"

        def _rewrite():
            p = world.scratch / target
            p.write_bytes(p.read_bytes().replace(b"content", b"CONTENT"))

        rc, payload, raw = self._frozen_then(monkeypatch, world, "run-adv1", _rewrite)
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-2 (ADV-1, entry)")
        assert rc == 1, f"AC-2: `[bd8:6a]` pins exit 1; got {rc}"
        assert target in (payload.get("error") or ""), (
            f"AC-2: the failure must name {target!r}; error={payload.get('error')!r}"
        )
        assert_token(payload, TOKEN_CONTENT, "AC-2")

        assert not (world.scratch / WITNESS).exists(), (
            "AC-2: the implementing phase's step RAN — the witness file exists. "
            "The ENTRY verify must refuse before execute() is entered; this GREEN "
            "is exit-only and every other adversary AC would still pass."
        )
        started = [e for e in read_events(world.log)
                   if e["event_type"] == "step_started"
                   and e["payload"].get("step_name") == "bd8_impl"]
        assert not started, (
            f"AC-2: a step_started for the implementing step is in the log: {started!r}"
        )

    def test_ac3_adv2_adding_a_file_between_phases(self, monkeypatch, world):
        """AC-3 (ADV-2, entry).  Every frozen member is byte-identical here, so
        a GREEN that only recomputes the member digest cannot see this
        (`[bd8:2b]`)."""
        added = f"{DOC_DIR}/smuggled.md"
        rc, payload, raw = self._frozen_then(
            monkeypatch, world, "run-adv2",
            lambda: (world.scratch / added).write_text("added after the freeze\n"))
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-3 (ADV-2, entry)")
        assert_token(payload, TOKEN_ADDED, "AC-3")

    def test_ac4_removing_a_member_between_phases(self, monkeypatch, world):
        """AC-4.  Distinguished by CATEGORY TOKEN, not by the path named."""
        target = f"{DOC_DIR}/notes.md"
        rc, payload, raw = self._frozen_then(
            monkeypatch, world, "run-rm", lambda: (world.scratch / target).unlink())
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-4 (removal)")
        assert target in (payload.get("error") or "")
        assert_token(payload, TOKEN_REMOVED, "AC-4")


# ─────────────────────────────────────────────────────────────────────────
# AC-2b / AC-3b — adversaries DURING implementation (the EXIT verify).
# CL §4 defines ADV-1/ADV-2 this way; §9 may not name them without these.
# ─────────────────────────────────────────────────────────────────────────

class TestAdversariesDuringImplementation:
    def test_ac2b_adv1_implementing_phase_rewrites_a_member(self, monkeypatch, world):
        """AC-2b (ADV-1, as CL §4 defines it)."""
        target = f"{DOC_DIR}/build-spec.md"
        install_fixture_workflows(monkeypatch, impl=_impl_step(mutate=target))
        assert freeze(world, "run-ac2b")[0] == 0

        rc, payload, raw = verify(world, "run-ac2b")
        assert (world.scratch / WITNESS).exists(), (
            "AC-2b precondition: the implementing phase never ran, so the adversary "
            "never acted — the entry verify must NOT refuse here"
        )
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-2b (ADV-1 during impl)")
        assert rc == 1
        assert target in (payload.get("error") or "")
        assert_token(payload, TOKEN_CONTENT, "AC-2b")

    def test_ac3b_adv2_implementing_phase_adds_to_the_scope(self, monkeypatch, world):
        """AC-3b (ADV-2, as CL §4 defines it)."""
        added = f"{DOC_DIR}/smuggled-by-impl.md"
        install_fixture_workflows(monkeypatch, impl=_impl_step(add=added))
        assert freeze(world, "run-ac3b")[0] == 0

        rc, payload, raw = verify(world, "run-ac3b")
        assert (world.scratch / added).exists(), "AC-3b precondition: no file added"
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-3b (ADV-2 during impl)")
        assert_token(payload, TOKEN_ADDED, "AC-3b")

    def test_ac2c_exit_verify_runs_even_when_the_phase_itself_failed(
        self, monkeypatch, world
    ):
        """`[bd8:6]`: the EXIT verify runs REGARDLESS of the phase's own
        outcome.  A phase that failed for an unrelated reason may still have
        mutated the oracle, and a verify gated on the success path lets that
        mutation survive every restart-governor retry."""
        target = f"{DOC_DIR}/build-spec.md"
        install_fixture_workflows(
            monkeypatch, impl=_impl_step(mutate=target, status="error"))
        assert freeze(world, "run-ac2c")[0] == 0

        rc, payload, raw = verify(world, "run-ac2c")
        assert (world.scratch / WITNESS).exists(), "precondition: the phase must run"
        assert_code(payload, raw, E_ORACLE_MUTATED,
                    "`[bd8:6]` exit verify on a FAILED phase")
        assert rc != 0


# ─────────────────────────────────────────────────────────────────────────
# AC-3a / AC-3c / AC-9b — the FALSE-FREE direction and the declared limits
# ─────────────────────────────────────────────────────────────────────────

class TestFalseFree:
    def test_ac3a_unchanged_tree_verifies_and_passes(self, monkeypatch, world):
        """AC-3a — a PURE control: it must pass before AND after, so it carries
        no assertion that can only hold post-GREEN."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac3a")[0] == 0
        rc, payload, raw = verify(world, "run-ac3a")
        assert rc == 0, (
            f"AC-3a: an UNCHANGED oracle must verify and pass; rc={rc} "
            f"error_code={payload.get('error_code')!r}.  A verify that fails "
            "unconditionally would still pass every adversary."
        )
        assert payload.get("error_code") is None

    def test_ac3c_activity_outside_the_scope_does_not_trip_the_verify(
        self, monkeypatch, world
    ):
        """AC-3c.  Work in the git repo and elsewhere in the scratchpad must not
        trip the verify — this is what stops a GREEN from discharging AC-3 with
        a whole-worktree or git-delta scan, which would fail every real build
        (earlier phases leave uncommitted files all over the tree)."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac3c")[0] == 0

        (world.repo / "src.py").write_text("implementation work, not the oracle\n")
        (world.scratch / LOOSE_IN_SCRATCHPAD).write_text("scratch note, outside specs/\n")

        rc, payload, _ = verify(world, "run-ac3c")
        assert rc == 0, (
            f"AC-3c: work outside the oracle scope must not trip the verify; rc={rc} "
            f"error_code={payload.get('error_code')!r} — the verify is scanning the "
            "worktree or the scratchpad root instead of the scope (`[bd8:2b]`)"
        )
        assert payload.get("error_code") is None

    def test_ac9b_scope_limits_are_measured_not_merely_declared(
        self, monkeypatch, world
    ):
        """AC-9b.  §9's limits (i): non-recursive, regular-files-only.  Both
        directions asserted — a limit no test measures is a claim, not a
        limit."""
        install_fixture_workflows(monkeypatch)
        sub = world.scratch / DOC_DIR / "drafts"
        sub.mkdir()
        (sub / "pre-existing.md").write_text("inside a subdirectory at freeze time\n")
        assert freeze(world, "run-ac9b")[0] == 0

        (sub / "added-later.md").write_text("added inside the SUBdirectory\n")
        (world.scratch / DOC_DIR / "newdir").mkdir()

        rc, payload, _ = verify(world, "run-ac9b")
        assert rc == 0, (
            f"AC-9b: a file added in a SUBdirectory and a new empty subdirectory must "
            f"NOT trip the verify — `[bd8:2b]` is non-recursive and files-only. "
            f"rc={rc} error_code={payload.get('error_code')!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-5 / AC-15 (`[bd8:9]`, `[bd8:6b]`) — absence is not permission
# ─────────────────────────────────────────────────────────────────────────

class TestUnfrozen:
    def test_ac5_implementing_phase_with_no_freeze_in_the_log_fails(
        self, monkeypatch, world
    ):
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = verify(world, "run-ac5")
        assert_code(payload, raw, E_ORACLE_UNFROZEN, "AC-5")
        assert rc != 0

    def test_ac5b_run_id_mismatch_fails_closed(self, monkeypatch, world):
        """`[bd8:8a]` + `[bd8:9]`: filter by run_id, THEN take the last
        survivor.  A freeze carrying `run-A` must not authorise `run-B`."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-A")[0] == 0
        rc, payload, raw = verify(world, "run-B")
        assert_code(payload, raw, E_ORACLE_UNFROZEN, "`[bd8:8a]` cross-check")
        assert rc != 0

    def test_ac15_logless_implementing_phase_fails_closed(self, monkeypatch, world):
        """AC-15 (G4).  `--event-log` must not be a one-flag bypass of BD-L1."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = verify(world, "run-ac15", log=False)
        assert_code(payload, raw, E_ORACLE_UNFROZEN, "AC-15 (logless impl phase)")
        assert rc != 0

    def test_ac15_control_logless_unmapped_workflow_is_untouched(
        self, monkeypatch, world
    ):
        """AC-15 control leg 1: the rule is scoped to the implementing phase,
        not a blanket refusal of logless runs."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = run_phase(UNMAPPED_WORKFLOW, world, "run-ac15c", log=False)
        assert rc == 0, (
            f"AC-15 control: a logless NON-mapped workflow must be untouched; "
            f"rc={rc} error_code={payload.get('error_code')!r} raw={raw}"
        )
        assert payload.get("error_code") is None

    def test_ac15_control_logless_oracle_phase_is_untouched(self, monkeypatch, world):
        """AC-15 control leg 2 (`[bd8:6b]`).  A logless ORACLE phase freezes
        nothing and fails nothing: the digest is carried by the log and nowhere
        else, so there is no place to record one.  Failing it closed would break
        `build-cli.ts:40` on every spec phase and buy nothing AC-15 does not
        already buy."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = freeze(world, "run-ac15o", log=False)
        assert rc == 0, (
            f"`[bd8:6b]`: a logless ORACLE phase must be untouched; rc={rc} "
            f"error_code={payload.get('error_code')!r} raw={raw}"
        )
        assert payload.get("error_code") is None


# ─────────────────────────────────────────────────────────────────────────
# AC-6 / AC-7 — ordering and invocation distinctness
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseOrdering:
    def test_ac6_oracle_workflow_finished_precedes_the_implementing_phase(
        self, monkeypatch, world
    ):
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac6")[0] == 0
        assert verify(world, "run-ac6")[0] == 0
        evs = read_events(world.log)
        fin = [i for i, e in enumerate(evs)
               if e["event_type"] == "workflow_finished"
               and e["payload"].get("workflow_name") == ORACLE_WORKFLOW]
        impl = [i for i, e in enumerate(evs)
                if e["payload"].get("workflow_name") == IMPL_WORKFLOW]
        assert fin and impl and fin[0] < impl[0], (
            f"AC-6 (R1.1): oracle workflow_finished {fin} does not precede the "
            f"implementing phase's first event {impl}"
        )

    def test_ac6b_the_freeze_lands_between_the_two_phases(self, monkeypatch, world):
        """`[bd8:6]`: the freeze lands after the oracle phase's own
        `phase_artifacts` and before the implementing phase starts."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac6b")[0] == 0
        assert verify(world, "run-ac6b")[0] == 0
        evs = read_events(world.log)
        fz = [i for i, e in enumerate(evs) if e["event_type"] == FROZEN_EVENT]
        pa = [i for i, e in enumerate(evs)
              if e["event_type"] == "phase_artifacts"
              and e["payload"].get("phase") == ORACLE_WORKFLOW]
        impl = [i for i, e in enumerate(evs)
                if e["payload"].get("workflow_name") == IMPL_WORKFLOW]
        assert fz, f"`[bd8:6]`: no {FROZEN_EVENT} emitted"
        assert pa and pa[0] < fz[0], (
            f"`[bd8:1b]`: the freeze must follow the phase_artifacts it copies its "
            f"cross-check from (phase_artifacts {pa}, freeze {fz[0]})"
        )
        assert fz[0] < impl[0], (
            f"`[bd8:6]`: freeze {fz[0]} does not precede the implementing phase {impl[0]}"
        )

    def test_ac7_two_invocations_each_emit_their_own_run_identity(
        self, monkeypatch, world
    ):
        """AC-7 (R1.2, ADAPTER-OBSERVED).  Counts events rather than comparing
        constants — `run_identity`'s payload is `{engine_version,
        adapter_identity}`, constant on one tree, and `[bd8:8a]` permits one
        shared `run_id`.  Enforcing R1.2 needs a new invocation-scoped field:
        an engine.py change, out of scope per §8, therefore a different lot."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac7")[0] == 0
        assert verify(world, "run-ac7")[0] == 0
        evs = read_events(world.log)
        started = [i for i, e in enumerate(evs) if e["event_type"] == "workflow_started"]
        ident = [i for i, e in enumerate(evs) if e["event_type"] == "run_identity"]
        names = [evs[i]["payload"].get("workflow_name") for i in started]
        assert names == [ORACLE_WORKFLOW, IMPL_WORKFLOW], f"AC-7: got {names!r}"
        assert len(ident) == 2, f"AC-7: expected two run_identity events, got {len(ident)}"
        for s, i in zip(started, ident):
            assert i > s, f"AC-7: run_identity {i} does not follow workflow_started {s}"


# ─────────────────────────────────────────────────────────────────────────
# AC-8 / AC-8a / AC-8b (R1.5) — amendment, and the resume it is not
# ─────────────────────────────────────────────────────────────────────────

class TestAmendment:
    def test_ac8_reasoned_reentry_amends_with_the_full_payload(
        self, monkeypatch, world
    ):
        """AC-8, reasoned leg.  The amendment must carry the FULL `[bd8:10]`
        payload — including `scope` and `scope_digest` RECOMPUTED — and the
        next implementing phase must verify against BOTH new values and pass.

        Second leg below: an addition AFTER the amendment is still
        `E_ORACLE_MUTATED`.  Without it, a GREEN whose amendment omits the
        scope half (and whose verify skips the scope check when the event
        carries none) disables ADV-2 for the rest of any build that amends —
        the normal multi-cycle spec path."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac8", org_extra={"bd8_content_tag": "v1"})[0] == 0
        first_digest = events_of(world.log, FROZEN_EVENT)[0]["payload"]["digest"]

        rc2, payload2, raw2 = freeze(world, "run-ac8", org_extra={
            "bd8_content_tag": "v2",
            "oracle_amendment_reason": "review found the criteria underspecified",
        })
        assert rc2 == 0, (
            f"AC-8: a REASONED re-entry must succeed; rc={rc2} "
            f"error_code={payload2.get('error_code')!r} raw={raw2}"
        )
        amended = events_of(world.log, AMENDED_EVENT)
        assert len(amended) == 1, f"AC-8: expected one {AMENDED_EVENT}, got {len(amended)}"
        ap = amended[0]["payload"]
        assert ap["previous_digest"] == first_digest
        assert ap["digest"] != ap["previous_digest"], (
            "AC-8: previous_digest == digest — the amendment recorded no change"
        )
        assert ap["digest"] == expected_digest(world.scratch, ORACLE_FILES)
        assert ap.get("reason"), "AC-8: the amendment carries no reason"
        assert "scope" in ap and "scope_digest" in ap, (
            f"AC-8/`[bd8:10]`: the amendment must carry scope AND scope_digest, "
            f"recomputed. Payload keys: {sorted(ap)!r}"
        )
        assert ap["scope_digest"] == expected_scope_digest(world.scratch, ORACLE_FILES)
        assert len(events_of(world.log, FROZEN_EVENT)) == 1

        rc3, payload3, _ = verify(world, "run-ac8")
        assert rc3 == 0, (
            f"AC-8: the implementing phase must verify against the NEW digest and "
            f"scope_digest and PASS; rc={rc3} error_code={payload3.get('error_code')!r}"
        )

    def test_ac8_second_leg_addition_after_an_amendment_still_trips(
        self, monkeypatch, world
    ):
        """AC-8, second leg — ADV-2 must survive an amendment."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac8b2", org_extra={"bd8_content_tag": "v1"})[0] == 0
        assert freeze(world, "run-ac8b2", org_extra={
            "bd8_content_tag": "v2", "oracle_amendment_reason": "cycle 2"})[0] == 0

        (world.scratch / DOC_DIR / "post-amendment.md").write_text("smuggled\n")
        rc, payload, raw = verify(world, "run-ac8b2")
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-8 second leg (ADV-2 post-amend)")
        assert_token(payload, TOKEN_ADDED, "AC-8 second leg")

    def test_ac8a_unreasoned_reentry_is_refused(self, monkeypatch, world):
        """AC-8a.  A GENUINE second `execute()` (forced through `org_config`,
        `[bd8:10b]`) with no reason → `E_ORACLE_AMENDMENT_UNREASONED`."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac8a", org_extra={"bd8_content_tag": "v1"})[0] == 0
        rc, payload, raw = freeze(world, "run-ac8a", org_extra={"bd8_content_tag": "v2"})
        assert_code(payload, raw, E_ORACLE_AMENDMENT_UNREASONED, "AC-8a")
        assert rc != 0
        assert not events_of(world.log, AMENDED_EVENT)
        assert len(events_of(world.log, FROZEN_EVENT)) == 1

    def test_ac8b_leg_i_resume_over_an_unchanged_tree_is_a_noop(
        self, monkeypatch, world
    ):
        """AC-8b leg (i) (`[bd8:10b]`)."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac8bi", org_extra={"bd8_content_tag": "v1"})[0] == 0
        rc, payload, raw = freeze(world, "run-ac8bi", org_extra={"bd8_content_tag": "v1"})
        assert events_of(world.log, "phase_sentinel_resumed"), (
            "AC-8b precondition: identical ctx + run_id must yield the same phase_key "
            "and a served cache hit (phase_sentinel.py:78-85, 308-331)"
        )
        assert rc == 0, f"AC-8b(i): a resume must be a no-op; rc={rc} raw={raw}"
        assert payload.get("error_code") is None
        assert len(events_of(world.log, FROZEN_EVENT)) == 1
        assert not events_of(world.log, AMENDED_EVENT)

    def test_ac8b_leg_ii_resume_over_a_MUTATED_tree_is_still_a_noop(
        self, monkeypatch, world
    ):
        """AC-8b leg (ii) — the leg that actually forces the rule.

        A member is mutated directly between two IDENTICAL invocations.  That
        does not touch `org_config`, so the phase key is unchanged and the
        sentinel still serves a resume.  The resume must remain a no-op, and
        the mutation must surface at the IMPLEMENTING phase as
        `E_ORACLE_MUTATED` — never at the resume as
        `E_ORACLE_AMENDMENT_UNREASONED`.

        With leg (i) alone, "resume" and "nothing changed" coincide, and a GREEN
        that merely compares digests — never detecting a resume at all —
        satisfies AC-8, AC-8a and AC-8b together."""
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac8bii", org_extra={"bd8_content_tag": "v1"})[0] == 0

        target = world.scratch / DOC_DIR / "build-spec.md"
        target.write_bytes(target.read_bytes().replace(b"content", b"CONTENT"))

        rc, payload, raw = freeze(world, "run-ac8bii",
                                  org_extra={"bd8_content_tag": "v1"})
        assert events_of(world.log, "phase_sentinel_resumed"), (
            "AC-8b(ii) precondition: the sentinel must still serve a resume — the "
            "mutation is on disk, not in org_config, so the phase key is unchanged"
        )
        assert rc == 0, (
            f"AC-8b(ii): a RESUME over a mutated tree must still be a no-op; rc={rc} "
            f"error_code={payload.get('error_code')!r}.  Refusing here breaks every "
            "resume-after-interrupt of the spec phase (#299/#603/#611)."
        )
        assert payload.get("error_code") != E_ORACLE_AMENDMENT_UNREASONED
        assert len(events_of(world.log, FROZEN_EVENT)) == 1
        assert not events_of(world.log, AMENDED_EVENT)

        rc2, payload2, raw2 = verify(world, "run-ac8bii")
        assert_code(payload2, raw2, E_ORACLE_MUTATED,
                    "AC-8b(ii): the mutation must surface at the IMPLEMENTING phase")


# ─────────────────────────────────────────────────────────────────────────
# AC-9 / AC-16 — fail-closed freezes, each with its control leg
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="chmod 000 does not deny root; AC-9 is unmeasurable as root",
)
class TestIndeterminateFreeze:
    def test_ac9_unreadable_member_is_indeterminate_and_emits_no_freeze(
        self, monkeypatch, world
    ):
        """AC-9 (`[bd8:4]`).  A member unreadable at freeze time is NOT a
        zero-byte member: a half-happened freeze is worse than none."""
        install_fixture_workflows(monkeypatch, unreadable=[f"{DOC_DIR}/notes.md"])
        try:
            rc, payload, raw = freeze(world, "run-ac9")
            assert_code(payload, raw, E_ORACLE_INDETERMINATE, "AC-9")
            assert rc != 0
            assert not events_of(world.log, FROZEN_EVENT), (
                "AC-9: an oracle_frozen was emitted despite an unreadable member"
            )
        finally:
            os.chmod(world.scratch / DOC_DIR / "notes.md", 0o644)


class TestEmptyOracle:
    """AC-16 (`[bd8:4a]`, RE-CUT under D1(a)).

    The v4 form keyed this on `phase_artifacts.written` being empty or
    `not-observed`.  `[bd8:2a]` MEASURED that payload reporting exactly that on
    a healthy production run, so the v4 form would have refused every real
    build.  The rule was right and its subject was wrong; here it is bound to
    the source `[bd8:1]` actually uses.
    """

    def test_ac16_empty_document_directory_is_indeterminate(self, monkeypatch, world):
        install_fixture_workflows(monkeypatch, oracle_writes=[])
        rc, payload, raw = freeze(world, "run-ac16empty")
        assert (world.scratch / DOC_DIR).exists(), "precondition: the dir exists"
        assert not any((world.scratch / DOC_DIR).iterdir()), "precondition: it is empty"
        assert_code(payload, raw, E_ORACLE_INDETERMINATE, "AC-16 (empty doc dir)")
        assert rc != 0
        assert not events_of(world.log, FROZEN_EVENT), (
            "AC-16: a zero-member oracle was frozen — every subsequent verify would "
            "pass trivially (`[bd8:9]`)"
        )

    def test_ac16_absent_scratchpad_dir_is_indeterminate(self, monkeypatch, world):
        """No `scratchpad_dir` on an oracle-phase run.  The engine's own spec
        workflows already treat it as REQUIRED (phase_45_spec.py:375-376), so
        this refuses nothing that works today."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = freeze(world, "run-ac16nosp", scratch=False)
        assert_code(payload, raw, E_ORACLE_INDETERMINATE, "AC-16 (no scratchpad_dir)")
        assert rc != 0
        assert not events_of(world.log, FROZEN_EVENT)

    def test_ac16_control_a_nonempty_directory_freezes_normally(
        self, monkeypatch, world
    ):
        """AC-16 CONTROL LEG — otherwise "refuse the empty case" is satisfied by
        refusing every freeze."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = freeze(world, "run-ac16ctl")
        assert rc == 0, (
            f"AC-16 control: a document directory holding regular files must freeze "
            f"normally; rc={rc} error_code={payload.get('error_code')!r} raw={raw}"
        )
        frozen = events_of(world.log, FROZEN_EVENT)
        assert len(frozen) == 1
        assert frozen[0]["payload"]["digest"] == expected_digest(
            world.scratch, ORACLE_FILES)


# ─────────────────────────────────────────────────────────────────────────
# AC-10 — the NAMESPACE BOUNDARY (re-cut under D1(a))
# ─────────────────────────────────────────────────────────────────────────

class TestNamespaceBoundary:
    """AC-10.  v1-v4 forced "the set is not a directory listing"; under
    `[bd8:1]` a directory listing IS the required behaviour, so the AC changed
    subject rather than being struck.  What must still be forced is that the
    listing does not reach outside this run's namespace — otherwise "a glob"
    means "an arbitrary glob"."""

    def test_ac10_leg1_a_file_outside_the_document_directory_is_not_a_member(
        self, monkeypatch, world
    ):
        """FORCING LEG 1.  A GREEN that walks the scratchpad ROOT fails here."""
        (world.scratch / LOOSE_IN_SCRATCHPAD).write_text("not an oracle document\n")
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac10a")[0] == 0

        p = events_of(world.log, FROZEN_EVENT)[0]["payload"]
        assert LOOSE_IN_SCRATCHPAD not in member_paths(p), (
            f"AC-10 leg 1: {LOOSE_IN_SCRATCHPAD!r} sits in scratchpad_dir but OUTSIDE "
            f"{DOC_DIR}/, and must not be a member: {member_paths(p)!r}"
        )
        assert member_paths(p) == sorted(ORACLE_FILES)
        assert sorted(p["scope"]) == [DOC_DIR], (
            f"AC-10 leg 1: the scope must be exactly [{DOC_DIR!r}]; got {p['scope']!r}"
        )

    def test_ac10_leg2_a_sibling_runs_scratchpad_is_another_namespace(
        self, monkeypatch, world
    ):
        """FORCING LEG 2.  A GREEN that walks UP out of the given
        `scratchpad_dir`, or globs the shared scratchpad root, fails here.

        This is the leg that keeps `[bd8:1]`'s listing tied to the ONE namespace
        the driver scoped to this run (`[bd8:1a]`, lib/project_root.py:24-31)."""
        smuggled = world.sibling / DOC_DIR / "other-run-spec.md"
        smuggled.write_text("belongs to a DIFFERENT run\n")
        install_fixture_workflows(monkeypatch)
        assert freeze(world, "run-ac10b")[0] == 0

        p = events_of(world.log, FROZEN_EVENT)[0]["payload"]
        paths = member_paths(p)
        assert p["member_count"] == 3, (
            f"AC-10 leg 2: member_count is {p['member_count']}, not 3 — the freeze "
            f"reached outside its own namespace into {world.sibling}"
        )
        assert not any("other-run-spec" in m for m in paths), (
            f"AC-10 leg 2: a SIBLING run's document is in this run's frozen set: "
            f"{paths!r}"
        )
        assert paths == sorted(ORACLE_FILES)

    def test_ac10_control_the_runs_own_document_directory_freezes_normally(
        self, monkeypatch, world
    ):
        """CONTROL LEG.  Without it, "does not go outside the namespace" is
        satisfied by a freeze that returns nothing at all."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = freeze(world, "run-ac10c")
        assert rc == 0, f"AC-10 control: rc={rc} raw={raw}"
        p = events_of(world.log, FROZEN_EVENT)[0]["payload"]
        assert member_paths(p) == sorted(ORACLE_FILES), (
            f"AC-10 control: the run's own document directory must freeze with exactly "
            f"its files; got {member_paths(p)!r}"
        )
        assert p["digest"] == expected_digest(world.scratch, ORACLE_FILES)


# ─────────────────────────────────────────────────────────────────────────
# AC-13 (`[bd8:7]`) — the blast-radius control
# ─────────────────────────────────────────────────────────────────────────

def test_ac13_unmapped_workflow_untouched(monkeypatch, world):
    """AC-13.  Non-vacuous by construction: the fixture workflow WRITES the same
    documents an oracle phase would, so it cannot pass by touching nothing."""
    install_fixture_workflows(monkeypatch)
    rc, payload, raw = run_phase(UNMAPPED_WORKFLOW, world, "run-ac13")
    assert rc == 0, (
        f"AC-13: an unmapped workflow must run untouched; rc={rc} "
        f"error_code={payload.get('error_code')!r} raw={raw}"
    )
    assert not events_of(world.log, FROZEN_EVENT)
    assert not events_of(world.log, AMENDED_EVENT)
    assert payload.get("error_code") is None


# ─────────────────────────────────────────────────────────────────────────
# AC-11 — the error-code registry, and TRAP 1 (`[bd8:11]`)
# ─────────────────────────────────────────────────────────────────────────

class TestErrorCodeRegistry:
    def test_ac11_four_codes_registered_with_a_condition_and_no_drift(self):
        """AC-11.  ONE test on purpose (`[bd8:11]`, TRAP 1): `--check` returns 1
        on `DEAD` as well as `UNREGISTERED` (error_codes.py:305-317), and
        `HARVEST_EXCLUDE_DIRS` contains `"tests"` (:23), so a code raised only
        from THIS file is still dead.  Registering the four without raising them
        in production turns the currently-green half RED.  Early registration
        does not satisfy this test."""
        import error_codes  # noqa: PLC0415 — deferred (§1q)

        four = (E_ORACLE_MUTATED, E_ORACLE_UNFROZEN,
                E_ORACLE_INDETERMINATE, E_ORACLE_AMENDMENT_UNREASONED)
        missing = [c for c in four if c not in error_codes.ERROR_CODES]
        assert not missing, f"AC-11: §5 codes absent from ERROR_CODES: {missing}"
        for c in four:
            desc = error_codes.ERROR_CODES[c]
            assert isinstance(desc, str) and desc.strip() and "\n" not in desc, (
                f"AC-11: {c} needs a non-empty ONE-LINE condition; got {desc!r}"
            )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = error_codes.main(["--check"])
        assert rc == 0, (
            "AC-11 / `[bd8:11]`: error_codes.py --check reports drift:\n"
            f"{buf.getvalue()}\n"
            "DEAD means registered but raised nowhere in the production tree "
            "(tests/ is excluded from the harvest) — register the codes in the SAME "
            "change that raises them."
        )

    def test_ac11b_error_codes_md_is_regenerated_not_hand_edited(self):
        import error_codes  # noqa: PLC0415 — deferred (§1q)

        doc = _ENGINE_ROOT / "ERROR_CODES.md"
        assert doc.exists(), "ERROR_CODES.md is missing"
        assert doc.read_text(encoding="utf-8") == error_codes.render_markdown(), (
            "ERROR_CODES.md is not the current `--markdown` output — regenerate it, "
            "do not hand-edit it (§8)"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-12 (bd#22 AC-C1) — importing conformance.oracle does no I/O
# ─────────────────────────────────────────────────────────────────────────

class TestNoIoAtImport:
    def test_ac12_import_conformance_oracle_has_no_side_effects(self, monkeypatch):
        """AC-12.  The recorders are installed and the module force-reimported
        inside this test, so a module already resident cannot make it vacuous."""
        import builtins  # noqa: PLC0415 — deferred (§1q)
        import importlib  # noqa: PLC0415

        calls: list[str] = []
        real_open = builtins.open
        monkeypatch.setattr(builtins, "open",
                            lambda *a, **k: (calls.append(f"open{a[:1]}"), real_open(*a, **k))[1])
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: calls.append(f"subprocess.run{a[:1]}"))
        monkeypatch.setattr(subprocess, "Popen",
                            lambda *a, **k: calls.append(f"subprocess.Popen{a[:1]}"))
        real_scandir = os.scandir
        monkeypatch.setattr(os, "scandir",
                            lambda *a, **k: (calls.append(f"scandir{a[:1]}"), real_scandir(*a, **k))[1])
        real_listdir = os.listdir
        monkeypatch.setattr(os, "listdir",
                            lambda *a, **k: (calls.append(f"listdir{a[:1]}"), real_listdir(*a, **k))[1])

        for mod in [m for m in list(sys.modules) if m == "conformance.oracle"]:
            del sys.modules[mod]
        importlib.invalidate_caches()
        oracle = importlib.import_module("conformance.oracle")

        assert oracle is not None
        assert calls == [], f"AC-12: conformance.oracle did I/O at import time: {calls}"

    def test_ac12b_conformance_is_a_real_package(self):
        import conformance  # noqa: PLC0415 — deferred (§1q)

        assert conformance.__file__ is not None, (
            "conformance resolved as a NAMESPACE package — `[bd8:5]`/bd#22 AC-C1 "
            "requires a real package"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-14 (§8 scope) — the fence
# ─────────────────────────────────────────────────────────────────────────

class TestScopeFence:
    """AC-14.  §8: a diff touching these is out of contract — it means the
    freeze migrated back into the phase it is meant to bound."""

    def test_ac14a_emit_phase_artifacts_finally_knows_nothing_about_the_oracle(self):
        import ast  # noqa: PLC0415 — deferred (§1q)

        src = (_ENGINE_ROOT / "engine.py").read_text(encoding="utf-8")
        fn = next((n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_emit_phase_artifacts"), None)
        assert fn is not None, "engine.py no longer defines _emit_phase_artifacts"
        body = ast.get_source_segment(src, fn) or ""
        assert "oracle" not in body.lower(), (
            "§8: the freeze/verify migrated into engine._emit_phase_artifacts — the "
            "finally that `[G18r3:EDGE-1]` documents as unable to do I/O or raise."
        )

    def test_ac14b_no_workflow_module_reaches_the_oracle_seam(self):
        # Narrow to the SEAM, not the word: phase_5_integrity.py:470 says "the
        # sole completeness oracle" in prose, and a fence that fires on prose is
        # a fence nobody keeps.
        seam = re.compile(
            r"conformance\.oracle|from\s+conformance\s+import\s+.*\boracle\b"
            r"|\boracle_frozen\b|\boracle_amended\b|\bE_ORACLE_"
        )
        offenders = [
            str(p.relative_to(_ENGINE_ROOT))
            for p in (_ENGINE_ROOT / "workflows").rglob("*.py")
            if seam.search(p.read_text(encoding="utf-8", errors="replace"))
        ]
        assert not offenders, (
            f"§8: workflow modules reference the oracle seam: {offenders}. A step runs "
            "INSIDE the phase it is meant to bound."
        )

    def test_ac14c_run_py_is_the_only_wiring_site(self):
        offenders = []
        for p in _ENGINE_ROOT.rglob("*.py"):
            rel = p.relative_to(_ENGINE_ROOT)
            if rel.parts[0] in {"tests", "conformance", "__pycache__"} or str(rel) == "run.py":
                continue
            if "conformance.oracle" in p.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(rel))
        assert not offenders, (
            f"`[bd8:6]`/§8: modules other than run.py wire the oracle seam: {offenders}"
        )
