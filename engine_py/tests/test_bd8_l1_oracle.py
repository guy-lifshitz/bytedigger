"""RED tests for bd#8 — BD-L1, oracle freeze-and-verify (R1.1-R1.5).

Frozen spec: engine_py/conformance/ORACLE_SPEC.md (FROZEN v4, base 2b6589f).
v4 is the post-gate freeze: the Opus gate REJECTED v3 on 9 blocking findings
and this file is the re-cut against v4, not a patch of the v3 RED.
Discipline inherited verbatim from the sibling conformance lots:
EMISSIONS_SPEC.md §0.1-§0.6, CONTRACTS_SPEC.md §0.1-§0.8, and the bd#10
header conventions (collection safety, external provenance, declared
pre-passing).

WHAT CHANGED FROM THE v3 RED, AND WHY (each is a gate finding, not a taste):
  * MAJOR-1 — the adversaries now act DURING the implementing phase (AC-2b,
    AC-3b), which is what CL §4 defines ADV-1/ADV-2 to be.  The v3 legs
    mutated BETWEEN invocations, which an entry-only verify observes and the
    real adversary never does.  Both are kept: entry AND exit (`[bd8:6]`).
  * MAJOR-2 — the addition case is asserted against `scope_digest`
    (`[bd8:2b]`), because recomputing the frozen member construction is
    provably invariant under a file appearing beside the members.  AC-3c is
    the containment fence that stops a GREEN from discharging it with a
    whole-worktree or git-delta scan.
  * MAJOR-4 — every `E_ORACLE_*` assertion reads the PARSED `error_code`
    field, never a stdout substring.  A substring pins vocabulary; the field
    pins the surface the restart governor, `--status` and `derive_state`
    actually read (`[bd8:6a]`).
  * MAJOR-4 (harness) — the v3 header claimed `question` varies the phase
    key.  MEASURED FALSE: `_ctx_cfg_sha8` hashes exactly
    `{"org_config", "task_description"}` (lib/phase_sentinel.py:78-85) and
    `WorkflowContext` has no `task_description` field (contracts.py), so
    `question` is not in the key at all and the v3 AC-8 measured the sentinel
    cache rather than a second `execute()`.  Re-entry is now forced through
    `org_config`, which IS in the key — and AC-8b pins the resume it must not
    be confused with (`[bd8:10b]`).
  * MAJOR-8 — AC-4 asserts a CATEGORY TOKEN (`mutated:content` /
    `mutated:added` / `mutated:removed`), not whole-message inequality.  Three
    adversaries acting on three different paths yield three different generic
    messages, so the v3 comparison measured nothing.

COLLECTION SAFETY (§1q / D1CF5FDF).  `conformance.oracle` does NOT exist on
this base.  Every reference to it is DEFERRED into a test body, so this
module COLLECTS cleanly pre-GREEN and FAILS at assert/call time.  There is no
module-level `sys.path` mutation and no `from conftest import` — the engine_py
conftest injects the roots at conftest-import time (tests/conftest.py:36-49),
which is the sanctioned seam (81F97F3D).

EXTERNAL PROVENANCE (§0.2).  Every pinned constant below cites a source
OUTSIDE the artifact under test:
  * The four error-code strings and the three category tokens are pinned as
    LITERALS citing ORACLE_SPEC §5 and AC-4.  They are deliberately NOT
    imported from `error_codes.ERROR_CODES` — this lot writes those entries,
    and pinning against them would be the §0.1 subtype (3) vacuum.
  * Every expected digest is recomputed in the test from the bytes the test
    itself wrote, with stdlib `hashlib`, per the `[bd8:2]` and `[bd8:2b]`
    constructions — never read back out of the event under test, and never
    through the module under test.

HARNESS SEAMS (measured on this base, not assumed):
  * `lib/phase_sentinel.execute_engine` builds its engine and calls
    `workflows.register_all(eng)` at CALL time (phase_sentinel.py:171-175),
    so `monkeypatch.setattr(workflows, "register_all", ...)` installs fixture
    workflows into a REAL engine run.  `WorkflowEngine.register` RAISES on a
    duplicate name (engine.py:228-231), so the fixture registrar registers
    ONLY the fixtures — it does not re-register the production set.
  * Fixture workflows are registered under the REAL registry names
    `phase_45_spec` / `phase_45_spec_lite` / `phase_5_implement`
    (workflows/__init__.py:34-36), so no test pins whatever internal name
    `[bd8:7]`'s module-level mapping ends up having.
  * The written set is the engine's git delta: `_written` is populated only
    when `org_config["git_cwd"]` resolves to a git repo
    (engine.py:407,425,493 via `_resolve_scan_cwd`, engine.py:1146-1149), and
    the paths it records are repo-root-relative — which is what `[bd8:3]`
    requires and why this file never asserts an absolute path.  AC-4a's
    not-observed leg exploits the same seam by OMITTING `git_cwd`.
  * The event log is kept OUTSIDE the fixture repo on purpose: a log (or the
    phase sentinel / restart-governor state written beside it) inside the
    repo would enter the git delta and silently join the oracle set.
  * `init_dbos()` returns immediately under the default `native` backend
    (dbos_setup.py:122-123), so nothing here patches it.  No harness
    primitive this file runs on is sabotaged ([G22:2]).

DECLARED PRE-PASSING (§0.6) — see the block above the test bodies.  Measured,
not predicted; every passing test is named there with its category.

SPEC GAPS.  None open.  G1-G8 are resolved in ORACLE_SPEC.md §7 (v4).  If a
new gap is found while making this RED green, it belongs in §7 by amendment,
not in a comment here.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers.git_repo import init_repo

_ENGINE_ROOT = Path(__file__).resolve().parent.parent

# ── Pinned literals (ORACLE_SPEC §5, AC-4) — never imported from error_codes ──
E_ORACLE_MUTATED = "E_ORACLE_MUTATED"
E_ORACLE_UNFROZEN = "E_ORACLE_UNFROZEN"
E_ORACLE_INDETERMINATE = "E_ORACLE_INDETERMINATE"
E_ORACLE_AMENDMENT_UNREASONED = "E_ORACLE_AMENDMENT_UNREASONED"

TOKEN_CONTENT = "mutated:content"
TOKEN_ADDED = "mutated:added"
TOKEN_REMOVED = "mutated:removed"
ALL_TOKENS = (TOKEN_CONTENT, TOKEN_ADDED, TOKEN_REMOVED)

# Registry names, measured at workflows/__init__.py:34-36 (spec §7 G3).
ORACLE_WORKFLOW = "phase_45_spec"
ORACLE_WORKFLOW_LITE = "phase_45_spec_lite"
IMPL_WORKFLOW = "phase_5_implement"
UNMAPPED_WORKFLOW = "phase_2_explore"

FROZEN_EVENT = "oracle_frozen"
AMENDED_EVENT = "oracle_amended"


# ─────────────────────────────────────────────────────────────────────────
# Independent digests — the `[bd8:2]` and `[bd8:2b]` constructions,
# recomputed from bytes the test itself wrote.  Never call the module
# under test.
# ─────────────────────────────────────────────────────────────────────────

def expected_digest(repo: Path, relpaths: list[str]) -> str:
    """`[bd8:2]`: for each path in SORTED order the line

        <relpath>\\0<sha256-of-bytes>

    UTF-8 encoded, joined by "\\n"; digest = "sha256:" + sha256(that).
    """
    lines = [
        f"{rel}\0{hashlib.sha256((repo / rel).read_bytes()).hexdigest()}"
        for rel in sorted(relpaths)
    ]
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def expected_scope(relpaths: list[str]) -> list[str]:
    """`[bd8:2b]`: the directories containing at least one frozen member,
    NON-recursively, derived from the member paths and from nothing else."""
    return sorted({str(Path(rel).parent) for rel in relpaths})


def expected_scope_digest(repo: Path, relpaths: list[str]) -> str:
    """`[bd8:2b]`: per scope directory, the line

        <reldir>\\0<sorted \\n-joined basenames>

    joined by "\\n"; digest = "sha256:" + sha256(that).  Computed over the
    directory as it stands, so a file present but not written by the phase
    (the HEAD-committed decoy) is INSIDE the snapshot and is not an addition.
    """
    lines = []
    for reldir in expected_scope(relpaths):
        names = sorted(p.name for p in (repo / reldir).iterdir() if p.is_file())
        lines.append(f"{reldir}\0" + "\n".join(names))
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# Fixture repo + event-log reading
# ─────────────────────────────────────────────────────────────────────────

ORACLE_FILES = ["specs/build-spec.md", "specs/build-plan-review.md", "specs/notes.md"]
DECOY = "specs/decoy.md"
OUTSIDE = "src/unrelated.py"        # outside every scope directory (AC-3c)
DIRTY_OUTSIDE = "src/already-dirty.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with a pre-committed file INSIDE the oracle directory and
    two files OUTSIDE it.

    `specs/decoy.md` is committed at HEAD, so it is invisible to the phase's
    git delta while being plainly visible to a directory listing — that is
    the force of AC-10, and `[bd8:2b]` requires it to be inside the scope
    snapshot rather than read as an addition.

    `src/already-dirty.py` is left DIRTY before the freeze so AC-3c can prove
    the verify does not scan the worktree.
    """
    r = tmp_path / "repo"
    (r / "specs").mkdir(parents=True)
    (r / "src").mkdir(parents=True)
    init_repo(str(r))
    (r / DECOY).write_text("decoy committed at HEAD; never in any written set\n")
    (r / DIRTY_OUTSIDE).write_text("committed clean\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=r, check=True)
    (r / DIRTY_OUTSIDE).write_text("dirty BEFORE the freeze — not a member\n")
    return r


@pytest.fixture
def logpath(tmp_path: Path) -> Path:
    """Event log OUTSIDE the fixture repo — see HARNESS SEAMS."""
    d = tmp_path / "eventlogs"
    d.mkdir()
    return d / "events.jsonl"


def read_events(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def events_of(path: Path | None, event_type: str) -> list[dict]:
    return [e for e in read_events(path) if e.get("event_type") == event_type]


def member_paths(payload: dict) -> list[str]:
    """`[bd8:8]` pins `members:[{path, digest}]`; a bare-string list is
    tolerated here ONLY so the path assertions report the real mismatch
    instead of dying on a TypeError.  `test_ac1b` is what pins the shape."""
    return sorted(m["path"] if isinstance(m, dict) else m for m in payload["members"])


# ─────────────────────────────────────────────────────────────────────────
# Fixture workflows + the run.main() driver
# ─────────────────────────────────────────────────────────────────────────

def _writer_step(relpaths: list[str], unreadable: list[str] | None = None):
    """A step that writes `relpaths` into `ctx.org_config['git_cwd']`.

    Content varies with `org_config['bd8_content_tag']` so an amendment can
    produce a genuinely DIFFERENT digest (AC-8 requires
    `previous_digest != digest`), and — because `org_config` is inside the
    phase-sentinel ctx hash (`[bd8:10a]`) — varying the tag is also what
    forces a real second `execute()` instead of a served cache hit.

    `unreadable` members are chmod 000 AFTER writing: `git ls-files --others`
    lists a file by name without reading it (engine.py:1152-1185), so the
    engine still records the path in `written` while the freeze cannot read
    its bytes (AC-9).
    """
    from contracts import StepResult  # noqa: PLC0415 — deferred (§1q)

    def _exec(context, prev):
        cfg = getattr(context, "org_config", None) or {}
        root = Path(cfg["git_cwd"])
        tag = cfg.get("bd8_content_tag", "v1")
        for rel in relpaths:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content of {rel} at {tag}\n")
        for rel in unreadable or []:
            os.chmod(root / rel, 0o000)
        return StepResult(status="ok", data={"written": relpaths}, duration_ms=1,
                          step_name="bd8_write")

    return _exec


def _noop_step():
    from contracts import StepResult  # noqa: PLC0415 — deferred (§1q)

    def _exec(context, prev):
        return StepResult(status="ok", data=None, duration_ms=1, step_name="bd8_noop")

    return _exec


def _mutating_step(target: str):
    """AC-2b: the implementing phase's OWN step rewrites a frozen member.
    This is ADV-1 as CL §4 defines it — the adversary acts DURING the
    implementation phase, where only the EXIT verify can see it."""
    from contracts import StepResult  # noqa: PLC0415 — deferred (§1q)

    def _exec(context, prev):
        root = Path((getattr(context, "org_config", None) or {})["git_cwd"])
        p = root / target
        p.write_bytes(p.read_bytes().replace(b"content", b"CONTENT"))
        return StepResult(status="ok", data=None, duration_ms=1, step_name="bd8_mutate")

    return _exec


def _adding_step(target: str):
    """AC-3b: the implementing phase's OWN step adds a file to a scope
    directory.  ADV-2 as CL §4 defines it."""
    from contracts import StepResult  # noqa: PLC0415 — deferred (§1q)

    def _exec(context, prev):
        root = Path((getattr(context, "org_config", None) or {})["git_cwd"])
        (root / target).write_text("added by the implementing phase itself\n")
        return StepResult(status="ok", data=None, duration_ms=1, step_name="bd8_add")

    return _exec


def install_fixture_workflows(
    monkeypatch,
    oracle_writes: list[str] = ORACLE_FILES,
    unreadable: list[str] | None = None,
    impl_step=None,
    extra: dict | None = None,
) -> None:
    """Replace `workflows.register_all` with a registrar of fixture workflows
    only.  `impl_step` lets the IMPLEMENTING phase carry an adversary."""
    from contracts import StepContract, WorkflowDefinition  # noqa: PLC0415 — deferred
    import workflows  # noqa: PLC0415 — deferred

    def _register_all(engine) -> None:
        for name in (ORACLE_WORKFLOW, ORACLE_WORKFLOW_LITE):
            engine.register(name, WorkflowDefinition(
                name=name,
                steps=[StepContract(name="bd8_write",
                                    execute=_writer_step(oracle_writes, unreadable))],
            ))
        engine.register(IMPL_WORKFLOW, WorkflowDefinition(
            name=IMPL_WORKFLOW,
            steps=[StepContract(name="bd8_impl", execute=impl_step or _noop_step())],
        ))
        engine.register(UNMAPPED_WORKFLOW, WorkflowDefinition(
            name=UNMAPPED_WORKFLOW,
            steps=[StepContract(name="bd8_other", execute=_writer_step(ORACLE_FILES))],
        ))
        for name, wf in (extra or {}).items():
            engine.register(name, wf)

    monkeypatch.setattr(workflows, "register_all", _register_all)


def run_phase(workflow: str, repo: Path | None, logpath: Path | None, run_id: str,
              org_extra: dict | None = None) -> tuple[int, dict, str]:
    """Invoke run.main() for one phase.

    Returns `(exit_code, parsed_stdout_json, raw_stdout_plus_stderr)`.  The
    PARSED payload is what every `E_ORACLE_*` assertion reads: `[bd8:6a]`
    requires the code on the `error_code` field, and `run.py:233-240` maps any
    exception escaping `main()` to `E_RUNNER`, so a stdout substring would
    pass over exactly the shape the spec forbids.

    One run.main() call == one process boundary == the `chokepoint`.
    """
    import run as run_module  # noqa: PLC0415 — deferred (§1q)

    org: dict = {}
    if repo is not None:
        org["git_cwd"] = str(repo)
    org.update(org_extra or {})
    ctx = json.dumps({
        "tenant_id": "bd8",
        "session_id": "bd8-session",
        "question": "bd8",
        "org_config": org,
    })
    argv = ["run.py", "--workflow", workflow, "--ctx-json", ctx, "--run-id", run_id]
    if logpath is not None:
        argv += ["--event-log", str(logpath)]

    out, err = io.StringIO(), io.StringIO()
    rc = 1
    with patch("sys.argv", argv), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = run_module.main()
        except SystemExit as e:  # pragma: no cover — main() returns, it does not exit
            rc = int(e.code or 0)
    raw = out.getvalue() + err.getvalue()
    try:
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {}
    return rc, payload, raw


def freeze(repo, logpath, run_id, **kw):
    return run_phase(ORACLE_WORKFLOW, repo, logpath, run_id, **kw)


def verify(repo, logpath, run_id, **kw):
    return run_phase(IMPL_WORKFLOW, repo, logpath, run_id, **kw)


def assert_code(payload: dict, raw: str, expected: str, ctx: str) -> None:
    """`[bd8:6a]`: the refusal is the PARSED `error_code`, not a word in the
    output.  `E_RUNNER` is called out by name because that is what `run.py`
    turns an escaping exception into, and it is the single most likely way a
    GREEN gets this wrong."""
    got = payload.get("error_code")
    assert got == expected, (
        f"{ctx}: expected error_code == {expected!r}, got {got!r}.\n"
        f"status={payload.get('status')!r} error={payload.get('error')!r}\n"
        + ("HINT: E_RUNNER means the refusal was RAISED out of main()'s try block "
           "instead of reported on the StepResult (`[bd8:6a]`); the restart governor, "
           "--status and derive_state would all see a runner crash.\n"
           if got == "E_RUNNER" else "")
        + f"raw: {raw[:600]}"
    )


def assert_token(payload: dict, expected: str, ctx: str) -> None:
    """AC-4: the message carries EXACTLY ONE of the three category tokens."""
    msg = f"{payload.get('error') or ''} {payload.get('suggestion') or ''}"
    present = [t for t in ALL_TOKENS if t in msg]
    assert present == [expected], (
        f"{ctx}: expected exactly the category token {expected!r}; found {present!r} "
        f"in {msg!r}. AC-4 distinguishes the three cases by token, not by the path "
        f"each happens to name."
    )


# ═════════════════════════════════════════════════════════════════════════
# DECLARED PRE-PASSING (§0.6) — MEASURED on base d86c266, not predicted.
#
#   MEASURED: `pytest tests/test_bd8_l1_oracle.py -p no:randomly`
#             →  36 collected, **25 failed, 11 passed**.
#
#   All 11 are named below with the reason each passes.  A pre-passing test is
#   legitimate ONLY as a control, a fence or a shield — never as a
#   requirement-bearing forcing leg, and none of the 11 is one.
#
#   CONTROLS — 4.  Must pass BEFORE and AFTER.  Their force is entirely
#   post-GREEN: they are what stops a GREEN from satisfying the adversaries by
#   refusing everything.  Today they pass vacuously (nothing verifies at all).
#     1. `TestFalseFree::test_ac3a_unchanged_tree_verifies_and_passes` — AC-3a.
#        The spec says it in as many words: "Without this, a verify that always
#        fails passes AC-2/AC-3."  Deliberately carries NO post-GREEN-only
#        assertion — the "exactly one oracle_frozen" property the gate asked
#        for lives in `test_ac1d`, which fails, because bundling it here would
#        turn the control into a forcing leg and destroy its signal.
#     2. `TestFalseFree::test_ac3c_activity_outside_the_scope_...` — AC-3c.
#        The containment fence against the whole-worktree / git-delta scan that
#        MAJOR-2 pushes a GREEN toward.  That GREEN passes AC-3 and AC-3b and
#        then fails every real build.
#     3. `TestUnfrozen::test_ac15_control_logless_unmapped_workflow_...` —
#        AC-15's control leg: fail-closed is scoped to the implementing phase,
#        not a blanket refusal of logless runs.
#     4. `test_ac13_unmapped_workflow_untouched` — AC-13, the blast-radius
#        control.  A GREEN that freezes on EVERY phase satisfies the other 24
#        and breaks every other phase in the engine.  Non-vacuous by
#        construction: the fixture workflow WRITES the same files an oracle
#        phase would, so it cannot pass by touching nothing.
#
#   FENCES — 5.  They assert an absence, or an already-true property, that
#   cannot yet be violated.  That is what a fence IS.  Each dies the moment a
#   GREEN reaches for the shape §8 and the `chokepoint` header forbid.
#     5. `TestPhaseOrdering::test_ac6_...` — AC-6.  R1.1's ordering is ALREADY
#        true on this base and spec §0 says so outright.  The part this lot
#        must MAKE true is `test_ac6b`, which places the FREEZE between the two
#        phases — and it fails.
#     6. `TestPhaseOrdering::test_ac7_...` — AC-7, ADAPTER-OBSERVED.  It counts
#        events (two `workflow_started`, two `run_identity`, each following its
#        own) rather than comparing constants, so it is not vacuous — but the
#        property is already true, which is precisely why §9 labels R1.2
#        `adapter-observed` and NOT `enforced`.  Enforcing it needs a new
#        invocation-scoped field on `run_identity`: an engine.py change, out of
#        scope per §8, therefore a different lot.
#     7-9. `TestScopeFence::test_ac14a/b/c` — AC-14, §8 scope: not inside
#        `engine._emit_phase_artifacts`'s `finally`, not in `workflows/`,
#        nowhere outside `run.py` + `conformance/`.
#
#   SHIELDS — 2.  Already-true properties this lot must not break.
#     10. `test_ac11b_error_codes_md_is_regenerated_not_hand_edited` —
#         ERROR_CODES.md byte-equal to `--markdown` output (§8 lists it as
#         regenerated, not hand-edited; this is what makes that checkable).
#     11. `test_ac12b_conformance_is_a_real_package` — bd#22's `__init__.py`
#         invariant, which `[bd8:5]` binds.  Without it AC-12's import could
#         resolve as a namespace package and assert nothing.
#
#   NOT pre-passing, though it may read that way: AC-8b (the sentinel-resume
#   control) FAILS today.  Its harness precondition is satisfied — the resume
#   really does fire — but its post-conditions count a freeze that does not yet
#   exist.  It becomes a control only once GREEN lands.
#
#   ONE HALF-ASSERTION inside a FAILING test is also pre-passing and is the
#   load-bearing one: AC-11's `error_codes.main(["--check"]) == 0`.  The drift
#   gate is clean on this tree.  It is the shield for `[bd8:11]` (TRAP 1): the
#   ERROR_CODES-membership half FAILS today, and the moment a GREEN satisfies
#   membership by registering the four codes WITHOUT raising them in
#   production code, this half flips to 1 with four `DEAD` lines.
#   `HARVEST_EXCLUDE_DIRS` contains `"tests"` (error_codes.py:23), so raising
#   them from THIS file does not revive them.  The two halves are in ONE test
#   on purpose and early registration does not satisfy it.
# ═════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────
# AC-1 (R1.3) — the freeze event, its digest, and its scope digest
# ─────────────────────────────────────────────────────────────────────────

class TestFreeze:
    def test_ac1_freeze_emits_one_event_with_both_digests(
        self, monkeypatch, repo, logpath
    ):
        """AC-1.  Exactly one `oracle_frozen` for this run_id; member_count 3;
        members are the three repo-relative paths in sorted order; `digest`
        equals the `[bd8:2]` construction and `scope_digest` the `[bd8:2b]`
        construction, both computed independently here."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = freeze(repo, logpath, "run-ac1")
        assert rc == 0, f"oracle phase must succeed; rc={rc} raw={raw}"

        frozen = events_of(logpath, FROZEN_EVENT)
        assert len(frozen) == 1, (
            f"AC-1: expected exactly one {FROZEN_EVENT}, got {len(frozen)}. "
            f"Event types seen: {sorted({e['event_type'] for e in read_events(logpath)})}"
        )
        ev = frozen[0]
        assert ev["run_id"] == "run-ac1"
        p = ev["payload"]
        assert p["phase"] == ORACLE_WORKFLOW
        assert p["member_count"] == 3, f"AC-1: member_count {p['member_count']} != 3"
        assert p["member_count"] == len(p["members"]), (
            f"AC-1: member_count {p['member_count']} disagrees with len(members) "
            f"{len(p['members'])}"
        )
        assert member_paths(p) == sorted(ORACLE_FILES), (
            f"AC-1: members {p['members']!r} are not the three sorted "
            f"repo-relative paths {sorted(ORACLE_FILES)!r} (`[bd8:3]`)"
        )
        assert p["digest"] == expected_digest(repo, ORACLE_FILES), (
            "AC-1: frozen digest does not match the `[bd8:2]` construction "
            "recomputed independently from the bytes this test wrote"
        )
        assert sorted(p["scope"]) == expected_scope(ORACLE_FILES), (
            f"`[bd8:2b]`: scope {p.get('scope')!r} is not the non-recursive set of "
            f"directories containing frozen members {expected_scope(ORACLE_FILES)!r}"
        )
        assert p["scope_digest"] == expected_scope_digest(repo, ORACLE_FILES), (
            "`[bd8:2b]`: scope_digest does not match the construction recomputed here. "
            "It must be taken at FREEZE time over the whole directory, so the "
            "HEAD-committed decoy is inside the snapshot and is not an addition."
        )

    def test_ac1b_per_member_digests_are_the_bare_hex_member_digests(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:2]` as amended by E4 pins the per-member `digest` as the BARE
        lowercase hex sha256, with no `sha256:` prefix.  Recomputed here from
        the bytes on disk, not read back out of the event."""
        install_fixture_workflows(monkeypatch)
        freeze(repo, logpath, "run-ac1b")
        p = events_of(logpath, FROZEN_EVENT)[0]["payload"]
        got = {m["path"]: m["digest"] for m in p["members"]}
        want = {
            rel: hashlib.sha256((repo / rel).read_bytes()).hexdigest()
            for rel in ORACLE_FILES
        }
        assert got == want, (
            f"`[bd8:2]`: per-member digests must be bare lowercase hex sha256 of the "
            f"member bytes. got={got!r} want={want!r}"
        )

    def test_ac1c_the_lite_oracle_workflow_also_freezes(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:7]` (MAJOR-3).  `phase_45_spec_lite` is in the oracle-authoring
        set: it writes the spec and review documents, and the OSS driver's
        declared sequence runs it directly before `phase_5_implement`.  A
        mapping that omits it makes every SIMPLE-tier build die at phase 5 on
        `E_ORACLE_UNFROZEN` — which no AC declares."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = run_phase(ORACLE_WORKFLOW_LITE, repo, logpath, "run-lite")
        assert rc == 0, f"lite oracle phase must succeed; rc={rc} raw={raw}"

        frozen = events_of(logpath, FROZEN_EVENT)
        assert len(frozen) == 1, (
            f"`[bd8:7]`: {ORACLE_WORKFLOW_LITE} emitted {len(frozen)} {FROZEN_EVENT} "
            "events; it is an oracle-authoring workflow and must freeze exactly once"
        )
        assert frozen[0]["payload"]["phase"] == ORACLE_WORKFLOW_LITE

        rc2, payload2, raw2 = verify(repo, logpath, "run-lite")
        assert rc2 == 0, (
            f"`[bd8:7]`: the implementing phase after a LITE freeze must verify and "
            f"pass; got rc={rc2} error_code={payload2.get('error_code')!r}. This is "
            "the SIMPLE-tier build path."
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-2 / AC-2b / AC-3 / AC-3b / AC-4 — the ADVERSARIES.  §9 licenses the
# BD-L1 claim ONLY because AC-2b and AC-3b run: CL §4 defines ADV-1 and
# ADV-2 as acting DURING the implementation phase.
# ─────────────────────────────────────────────────────────────────────────

class TestAdversariesBetweenPhases:
    """The ENTRY verify (`[bd8:6]`): mutation lands between the invocations."""

    def _frozen_then(self, monkeypatch, repo, logpath, run_id, mutate):
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, run_id)
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"
        assert events_of(logpath, FROZEN_EVENT), "precondition: nothing was frozen"
        mutate()
        return verify(repo, logpath, run_id)

    def test_ac2_adv1_rewriting_a_member_between_phases(
        self, monkeypatch, repo, logpath
    ):
        """AC-2 (ADV-1, entry).  error_code == E_ORACLE_MUTATED, exit 1, the
        failure names the offending path and carries `mutated:content`."""
        target = "specs/build-spec.md"

        def _rewrite():
            p = repo / target
            p.write_bytes(p.read_bytes().replace(b"content", b"CONTENT"))

        rc, payload, raw = self._frozen_then(
            monkeypatch, repo, logpath, "run-adv1", _rewrite)
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-2 (ADV-1, entry)")
        assert rc == 1, f"AC-2: `[bd8:6a]` pins exit 1; got {rc}"
        assert target in (payload.get("error") or ""), (
            f"AC-2 requires the failure to name the offending path {target!r}; "
            f"error={payload.get('error')!r}"
        )
        assert_token(payload, TOKEN_CONTENT, "AC-2")

    def test_ac3_adv2_adding_a_file_between_phases(self, monkeypatch, repo, logpath):
        """AC-3 (ADV-2, entry).  The message must name `scope_digest` as the
        mismatching half (`[bd8:2b]`): every frozen member is byte-identical
        here, so a GREEN that only recomputes the member digest CANNOT see
        this and fails."""
        added = "specs/smuggled.md"

        rc, payload, raw = self._frozen_then(
            monkeypatch, repo, logpath, "run-adv2",
            lambda: (repo / added).write_text("added after the freeze\n"))
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-3 (ADV-2, entry)")
        assert "scope_digest" in (payload.get("error") or ""), (
            "AC-3: the message must name `scope_digest` as the mismatching half — "
            "the member digest is provably invariant under an addition "
            f"(`[bd8:2b]`). error={payload.get('error')!r}"
        )
        assert_token(payload, TOKEN_ADDED, "AC-3")

    def test_ac4_removing_a_member_between_phases(self, monkeypatch, repo, logpath):
        """AC-4.  Removal is distinguished by CATEGORY TOKEN, not by the path
        it happens to name."""
        target = "specs/notes.md"

        rc, payload, raw = self._frozen_then(
            monkeypatch, repo, logpath, "run-rm", lambda: (repo / target).unlink())
        assert_code(payload, raw, E_ORACLE_MUTATED, "AC-4 (removal)")
        assert target in (payload.get("error") or ""), (
            f"AC-4: failure must name the removed path {target!r}; "
            f"error={payload.get('error')!r}"
        )
        assert_token(payload, TOKEN_REMOVED, "AC-4")


class TestAdversariesDuringImplementation:
    """The EXIT verify (`[bd8:6]`) — ADV-1 and ADV-2 AS CL §4 DEFINES THEM.

    The adversary is a step of the implementing phase itself.  An entry-only
    verify has already run and returned clean when these fire; only the exit
    verify can observe them, and §9 may not name ADV-1/ADV-2 without them.
    """

    def test_ac2b_adv1_implementing_phase_rewrites_a_member(
        self, monkeypatch, repo, logpath
    ):
        """AC-2b (ADV-1, as defined by CL §4)."""
        target = "specs/build-spec.md"
        install_fixture_workflows(monkeypatch, impl_step=_mutating_step(target))
        rc, _, raw = freeze(repo, logpath, "run-ac2b")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"

        rc2, payload, raw2 = verify(repo, logpath, "run-ac2b")
        assert_code(payload, raw2, E_ORACLE_MUTATED, "AC-2b (ADV-1 during impl)")
        assert rc2 == 1, f"AC-2b: exit must be 1; got {rc2}"
        assert target in (payload.get("error") or "")
        assert_token(payload, TOKEN_CONTENT, "AC-2b")

        assert (repo / target).read_bytes().count(b"CONTENT") >= 1, (
            "AC-2b precondition: the implementing phase's own step did not actually "
            "rewrite the member — the adversary never ran"
        )

    def test_ac3b_adv2_implementing_phase_adds_to_the_scope(
        self, monkeypatch, repo, logpath
    ):
        """AC-3b (ADV-2, as defined by CL §4)."""
        added = "specs/smuggled-by-impl.md"
        install_fixture_workflows(monkeypatch, impl_step=_adding_step(added))
        rc, _, raw = freeze(repo, logpath, "run-ac3b")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"

        rc2, payload, raw2 = verify(repo, logpath, "run-ac3b")
        assert_code(payload, raw2, E_ORACLE_MUTATED, "AC-3b (ADV-2 during impl)")
        assert rc2 == 1, f"AC-3b: exit must be 1; got {rc2}"
        assert_token(payload, TOKEN_ADDED, "AC-3b")
        assert (repo / added).exists(), (
            "AC-3b precondition: the implementing phase's own step did not add the file"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-3a / AC-3c — the FALSE-FREE direction.  Without these, a verify that
# always fails passes every adversary above and nobody notices.
# ─────────────────────────────────────────────────────────────────────────

class TestFalseFree:
    def test_ac3a_unchanged_tree_verifies_and_passes(self, monkeypatch, repo, logpath):
        """AC-3a.  Freeze, change NOTHING, run the implementing phase →
        exit 0 and no oracle error code.

        This is a PURE control: it must pass before AND after GREEN, so it
        carries no assertion that can only hold post-GREEN.  The "exactly one
        oracle_frozen" property the gate asked for lives in its own test
        below, because bundling it here would turn the control into a forcing
        leg and lose exactly the signal AC-3a exists to give."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, "run-ac3a")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"

        rc2, payload, raw2 = verify(repo, logpath, "run-ac3a")
        assert rc2 == 0, (
            f"AC-3a: an UNCHANGED oracle must verify and pass; got rc={rc2}, "
            f"error_code={payload.get('error_code')!r}. A verify that fails "
            "unconditionally would still pass every adversary above."
        )
        assert payload.get("error_code") is None, (
            f"AC-3a: error_code {payload.get('error_code')!r} over an unchanged tree"
        )

    def test_ac1d_freeze_then_verify_leaves_exactly_one_frozen_event(
        self, monkeypatch, repo, logpath
    ):
        """AC-1, extended (gate adversarial edge 6).  Nothing else asserts that
        the `oracle_frozen` count stays at ONE after a freeze-then-verify pair.

        A GREEN that also freezes at the IMPLEMENTING phase's exit passes every
        other test here and corrupts the "LAST freeze event" lookup
        (`[bd8:9]`) for any third phase in the same log."""
        install_fixture_workflows(monkeypatch)
        assert freeze(repo, logpath, "run-ac1d")[0] == 0
        assert verify(repo, logpath, "run-ac1d")[0] == 0

        frozen = events_of(logpath, FROZEN_EVENT)
        assert len(frozen) == 1, (
            f"AC-1: the freeze-then-verify pair left {len(frozen)} {FROZEN_EVENT} "
            "events; only the oracle phase may freeze"
        )
        assert frozen[0]["payload"]["phase"] == ORACLE_WORKFLOW, (
            f"AC-1: the freeze was emitted for phase "
            f"{frozen[0]['payload']['phase']!r}, not the oracle phase"
        )

    def test_ac3c_activity_outside_the_scope_does_not_trip_the_verify(
        self, monkeypatch, repo, logpath
    ):
        """AC-3c (scope containment, decoy fence).

        Freeze; then ADD a file outside every scope directory and REWRITE an
        unrelated file that was already dirty at freeze time; verify → exit 0.

        This is what stops a GREEN from discharging AC-3 with a whole-worktree
        or git-delta-vs-HEAD scan.  That GREEN passes AC-3 and AC-3b and then
        fails every real build, where earlier phases leave uncommitted files
        all over the tree."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, "run-ac3c")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"

        (repo / OUTSIDE).write_text("added outside every scope directory\n")
        (repo / DIRTY_OUTSIDE).write_text("rewritten after the freeze, still not a member\n")

        rc2, payload, raw2 = verify(repo, logpath, "run-ac3c")
        assert rc2 == 0, (
            f"AC-3c: work OUTSIDE the oracle scope must not trip the verify; got "
            f"rc={rc2}, error_code={payload.get('error_code')!r}. The verify is "
            "scanning the worktree instead of the scope (`[bd8:2b]`), which fails "
            "every real build."
        )
        assert payload.get("error_code") is None


# ─────────────────────────────────────────────────────────────────────────
# AC-5 / AC-15 (`[bd8:9]`) — absence of a freeze is NOT permission
# ─────────────────────────────────────────────────────────────────────────

class TestUnfrozen:
    def test_ac5_implementing_phase_with_no_freeze_in_the_log_fails(
        self, monkeypatch, repo, logpath
    ):
        """AC-5.  Asserted on the parsed `error_code`, not on stdout."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = verify(repo, logpath, "run-ac5")
        assert_code(payload, raw, E_ORACLE_UNFROZEN, "AC-5")
        assert rc != 0, f"AC-5: implementing phase passed with no freeze recorded: {raw}"

    def test_ac5b_run_id_mismatch_fails_closed(self, monkeypatch, repo, logpath):
        """`[bd8:8a]` + `[bd8:9]` lookup order.  The lookup is scoped to the
        LOG and FILTERS by run_id BEFORE taking the last survivor.  A freeze
        carrying `run-A` must not authorise an invocation carrying `run-B`
        (the stale-log class, GH#215)."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, "run-A")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"

        rc2, payload, raw2 = verify(repo, logpath, "run-B")
        assert_code(payload, raw2, E_ORACLE_UNFROZEN, "`[bd8:8a]` run_id cross-check")
        assert rc2 != 0

    def test_ac15_logless_implementing_phase_fails_closed(self, monkeypatch, repo):
        """AC-15 (G4).  An implementing-phase invocation given NO `--event-log`
        fails `E_ORACLE_UNFROZEN`.  A logless run has no log in which a freeze
        could exist, so it is the strongest case of absence, not an exemption.

        Measured migration cost, per the spec: zero in-tree callers, zero
        production build paths, one out-of-tree ad-hoc caller."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = run_phase(IMPL_WORKFLOW, repo, None, "run-ac15")
        assert_code(payload, raw, E_ORACLE_UNFROZEN, "AC-15 (logless impl phase)")
        assert rc != 0, (
            f"AC-15: a logless implementing phase passed — `--event-log` is a "
            f"one-flag bypass of BD-L1: {raw}"
        )

    def test_ac15_control_logless_unmapped_workflow_is_untouched(
        self, monkeypatch, repo
    ):
        """AC-15 CONTROL LEG.  The rule is scoped to the implementing phase and
        is NOT a blanket refusal of logless runs — without this, a GREEN that
        fails every logless invocation satisfies AC-15."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = run_phase(UNMAPPED_WORKFLOW, repo, None, "run-ac15c")
        assert rc == 0, (
            f"AC-15 control: a logless NON-mapped workflow must run untouched; "
            f"rc={rc} error_code={payload.get('error_code')!r} raw={raw}"
        )
        assert payload.get("error_code") is None


# ─────────────────────────────────────────────────────────────────────────
# AC-6 (R1.1) — phase ordering, over the REAL log of a two-invocation run
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseOrdering:
    def test_ac6_oracle_workflow_finished_precedes_the_implementing_phase(
        self, monkeypatch, repo, logpath
    ):
        """AC-6.  Asserted over the real log of two run.main() invocations
        sharing one event log — not over a constructed fixture."""
        install_fixture_workflows(monkeypatch)
        assert freeze(repo, logpath, "run-ac6")[0] == 0
        assert verify(repo, logpath, "run-ac6")[0] == 0

        evs = read_events(logpath)
        fin = [i for i, e in enumerate(evs)
               if e["event_type"] == "workflow_finished"
               and e["payload"].get("workflow_name") == ORACLE_WORKFLOW]
        first_impl = [i for i, e in enumerate(evs)
                      if e["payload"].get("workflow_name") == IMPL_WORKFLOW]
        assert fin, "AC-6: no workflow_finished for the oracle phase in the log"
        assert first_impl, "AC-6: no implementing-phase event in the log"
        assert fin[0] < first_impl[0], (
            f"AC-6 (R1.1): the oracle phase's workflow_finished (index {fin[0]}) does "
            f"not precede the implementing phase's first event (index {first_impl[0]})"
        )

    def test_ac6b_the_freeze_lands_between_the_two_phases(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:6]`: the freeze lands after the oracle phase's own
        `phase_artifacts` (which it reads its member set from) and before the
        implementing phase starts.  A freeze emitted after the implementing
        phase began would bound nothing."""
        install_fixture_workflows(monkeypatch)
        assert freeze(repo, logpath, "run-ac6b")[0] == 0
        assert verify(repo, logpath, "run-ac6b")[0] == 0

        evs = read_events(logpath)
        fz = [i for i, e in enumerate(evs) if e["event_type"] == FROZEN_EVENT]
        pa = [i for i, e in enumerate(evs)
              if e["event_type"] == "phase_artifacts"
              and e["payload"].get("phase") == ORACLE_WORKFLOW]
        impl = [i for i, e in enumerate(evs)
                if e["payload"].get("workflow_name") == IMPL_WORKFLOW]
        assert fz, f"`[bd8:6]`: no {FROZEN_EVENT} emitted"
        assert pa and pa[0] < fz[0], (
            "`[bd8:1]`: the freeze must come AFTER the phase_artifacts it reads its "
            f"member set from (phase_artifacts at {pa}, freeze at {fz[0]})"
        )
        assert fz[0] < impl[0], (
            f"`[bd8:6]`: freeze at index {fz[0]} does not precede the implementing "
            f"phase's first event at index {impl[0]}"
        )

    def test_ac7_two_invocations_each_emit_their_own_run_identity(
        self, monkeypatch, repo, logpath
    ):
        """AC-7 (R1.2, ADAPTER-OBSERVED — see §9).

        Non-vacuous because it COUNTS events rather than comparing constants.
        It deliberately does NOT assert that the two `run_identity` payloads
        differ: that payload is `{engine_version, adapter_identity}`, both
        constant on one tree, and `[bd8:8a]` permits one shared `run_id`.
        Making R1.2 enforceable needs a new invocation-scoped field on
        `run_identity` — an engine.py change, out of scope per §8, therefore a
        different lot."""
        install_fixture_workflows(monkeypatch)
        assert freeze(repo, logpath, "run-ac7")[0] == 0
        assert verify(repo, logpath, "run-ac7")[0] == 0

        evs = read_events(logpath)
        started = [i for i, e in enumerate(evs) if e["event_type"] == "workflow_started"]
        ident = [i for i, e in enumerate(evs) if e["event_type"] == "run_identity"]
        names = [evs[i]["payload"].get("workflow_name") for i in started]

        assert names == [ORACLE_WORKFLOW, IMPL_WORKFLOW], (
            f"AC-7: expected exactly two workflow_started events for the two phases, "
            f"got {names!r}"
        )
        assert len(ident) == 2, (
            f"AC-7: expected exactly two run_identity events, one per invocation; "
            f"got {len(ident)}"
        )
        for s, i in zip(started, ident):
            assert i > s, (
                f"AC-7: run_identity at {i} does not follow its own workflow_started "
                f"at {s} (engine.py:286-298)"
            )


# ─────────────────────────────────────────────────────────────────────────
# AC-8 / AC-8a / AC-8b (R1.5) — amendment, and the resume it is not
# ─────────────────────────────────────────────────────────────────────────

class TestAmendment:
    def test_ac8_reasoned_reentry_amends_and_the_new_digest_verifies(
        self, monkeypatch, repo, logpath
    ):
        """AC-8, reasoned leg — the false-free control for the whole amendment
        path.  Without it, a GREEN that refuses EVERY re-entry satisfies AC-8a.

        `oracle_amendment_reason` is the `[bd8:10a]` channel: an `org_config`
        key through the EXISTING `--ctx-json`, no new CLI surface.  Because
        `org_config` is inside the phase-sentinel ctx hash, supplying it is
        also what makes this a genuine second `execute()` rather than a served
        cache hit (`[bd8:10b]`)."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, "run-ac8", org_extra={"bd8_content_tag": "v1"})
        assert rc == 0, f"precondition: first freeze must succeed; rc={rc} raw={raw}"
        first = events_of(logpath, FROZEN_EVENT)
        assert len(first) == 1
        first_digest = first[0]["payload"]["digest"]

        rc2, payload2, raw2 = freeze(repo, logpath, "run-ac8", org_extra={
            "bd8_content_tag": "v2",
            "oracle_amendment_reason": "review found the acceptance criteria underspecified",
        })
        assert rc2 == 0, (
            f"AC-8: a REASONED re-entry must succeed; rc={rc2} "
            f"error_code={payload2.get('error_code')!r} raw={raw2}"
        )

        amended = events_of(logpath, AMENDED_EVENT)
        assert len(amended) == 1, (
            f"AC-8: expected exactly one {AMENDED_EVENT}; got {len(amended)}"
        )
        ap = amended[0]["payload"]
        assert ap["previous_digest"] == first_digest, (
            f"AC-8: previous_digest {ap['previous_digest']!r} is not the digest the "
            f"first freeze recorded {first_digest!r}"
        )
        assert ap["digest"] != ap["previous_digest"], (
            "AC-8: previous_digest == digest — the amendment recorded no change"
        )
        assert ap["digest"] == expected_digest(repo, ORACLE_FILES), (
            "AC-8: the amended digest is not the `[bd8:2]` construction over the "
            "bytes now on disk"
        )
        assert ap.get("reason"), "AC-8: the amendment carries no reason"
        assert len(events_of(logpath, FROZEN_EVENT)) == 1, (
            f"`[bd8:10]`: re-entry emitted a second {FROZEN_EVENT}; it must emit "
            f"{AMENDED_EVENT} instead"
        )

        rc3, payload3, raw3 = verify(repo, logpath, "run-ac8")
        assert rc3 == 0, (
            f"AC-8: the implementing phase must verify against the NEW digest and "
            f"PASS; got rc={rc3} error_code={payload3.get('error_code')!r}"
        )

    def test_ac8a_unreasoned_reentry_is_refused(self, monkeypatch, repo, logpath):
        """AC-8a, unreasoned leg.  A GENUINE second `execute()` — forced by a
        differing `org_config` key so the phase key differs (`[bd8:10b]`) —
        with no reason fails `E_ORACLE_AMENDMENT_UNREASONED` and emits neither
        an amendment nor a second freeze."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, "run-ac8a", org_extra={"bd8_content_tag": "v1"})
        assert rc == 0, f"precondition: first freeze must succeed; rc={rc} raw={raw}"

        rc2, payload2, raw2 = freeze(repo, logpath, "run-ac8a",
                                     org_extra={"bd8_content_tag": "v2"})
        assert_code(payload2, raw2, E_ORACLE_AMENDMENT_UNREASONED, "AC-8a")
        assert rc2 != 0
        assert not events_of(logpath, AMENDED_EVENT), (
            "AC-8a: an oracle_amended was emitted without a reason"
        )
        assert len(events_of(logpath, FROZEN_EVENT)) == 1, (
            "AC-8a: a reasonless re-entry emitted a second oracle_frozen"
        )

    def test_ac8b_a_sentinel_resumed_phase_is_not_a_reentry(
        self, monkeypatch, repo, logpath
    ):
        """AC-8b (`[bd8:10b]`, resume control).

        Re-invoking with an IDENTICAL ctx and run_id makes the native durable
        backend serve the cached success and emit `phase_sentinel_resumed`
        without running the engine.  No second `execute()` happened, so this is
        a NO-OP for this lot.  Without this control, the ordinary
        resume-after-interrupt of `phase_45_spec` fails
        `E_ORACLE_AMENDMENT_UNREASONED` and the success-only sentinel
        (#299/#603/#611) is destroyed."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, "run-ac8b", org_extra={"bd8_content_tag": "v1"})
        assert rc == 0, f"precondition: first freeze must succeed; rc={rc} raw={raw}"

        rc2, payload2, raw2 = freeze(repo, logpath, "run-ac8b",
                                     org_extra={"bd8_content_tag": "v1"})
        assert events_of(logpath, "phase_sentinel_resumed"), (
            "AC-8b precondition: the harness failed to trigger a sentinel resume — "
            "an identical ctx and run_id must produce the same phase_key "
            "(lib/phase_sentinel.py:78-85, 308-331)"
        )
        assert rc2 == 0, (
            f"AC-8b: a sentinel-resumed oracle phase must be a no-op; got rc={rc2} "
            f"error_code={payload2.get('error_code')!r}"
        )
        assert payload2.get("error_code") != E_ORACLE_AMENDMENT_UNREASONED, (
            "AC-8b: a RESUME was treated as an unreasoned re-entry — this breaks "
            "every resume-after-interrupt of the spec phase"
        )
        assert len(events_of(logpath, FROZEN_EVENT)) == 1, (
            "AC-8b: the resume emitted a second oracle_frozen"
        )
        assert not events_of(logpath, AMENDED_EVENT), (
            "AC-8b: the resume emitted an oracle_amended"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-9 / AC-9a / AC-4a — fail-closed freezes, each with its CONTROL leg
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="chmod 000 does not deny root; AC-9 is unmeasurable as root",
)
class TestIndeterminateFreeze:
    def test_ac9_unreadable_member_is_indeterminate_and_emits_no_freeze(
        self, monkeypatch, repo, logpath
    ):
        """AC-9 (`[bd8:4]`).  A member unreadable at freeze time is NOT a
        zero-byte member: a half-happened freeze is worse than none."""
        install_fixture_workflows(monkeypatch, unreadable=["specs/notes.md"])
        try:
            rc, payload, raw = freeze(repo, logpath, "run-ac9")
            assert_code(payload, raw, E_ORACLE_INDETERMINATE, "AC-9")
            assert rc != 0
            assert not events_of(logpath, FROZEN_EVENT), (
                "AC-9: an oracle_frozen was emitted despite an unreadable member — "
                "the half-happened freeze `[bd8:4]` forbids"
            )
        finally:
            os.chmod(repo / "specs/notes.md", 0o644)


class TestTruncatedWrittenSet:
    """AC-9a (G1).  `phase_artifacts` replaces `written` with a SAMPLE plus
    `written_truncated: true` when the serialised line exceeds the log's
    per-line limit (engine.py:1320-1334).  A freeze over a sampled set would
    record a digest for a membership the engine never saw whole.

    The truncation is forced by lowering engine.py's OWN limit constant from
    the test — the real truncation code path runs.  `engine.py` is not
    modified by this lot (§8); the constant is a harness knob, not a UUT.
    """

    def test_ac9a_truncated_written_is_indeterminate_and_emits_no_freeze(
        self, monkeypatch, repo, logpath
    ):
        import engine as engine_mod  # noqa: PLC0415 — deferred (§1q)

        install_fixture_workflows(monkeypatch)
        monkeypatch.setattr(engine_mod, "_EVENT_LOG_LINE_LIMIT_BYTES", 200, raising=True)
        rc, payload, raw = freeze(repo, logpath, "run-ac9a")

        pa = [e for e in events_of(logpath, "phase_artifacts")
              if e["payload"].get("phase") == ORACLE_WORKFLOW]
        assert pa and pa[0]["payload"].get("written_truncated") is True, (
            "AC-9a precondition: the harness failed to force a truncated "
            f"phase_artifacts payload; got {pa and pa[0]['payload']!r}"
        )
        assert_code(payload, raw, E_ORACLE_INDETERMINATE, "AC-9a (truncated)")
        assert rc != 0
        assert not events_of(logpath, FROZEN_EVENT), (
            "AC-9a: an oracle_frozen was emitted over a sampled membership"
        )

    def test_ac9a_control_the_same_set_untruncated_freezes_normally(
        self, monkeypatch, repo, logpath
    ):
        """AC-9a CONTROL LEG, required by the spec in so many words: "the same
        set under the untruncated payload freezes normally, so the AC cannot be
        satisfied by refusing every freeze"."""
        install_fixture_workflows(monkeypatch)
        rc, payload, raw = freeze(repo, logpath, "run-ac9a-ctl")

        pa = [e for e in events_of(logpath, "phase_artifacts")
              if e["payload"].get("phase") == ORACLE_WORKFLOW]
        assert pa and "written_truncated" not in pa[0]["payload"], (
            "AC-9a control precondition: this payload must NOT be truncated"
        )
        assert rc == 0, (
            f"AC-9a control: the untruncated freeze must succeed; "
            f"error_code={payload.get('error_code')!r} raw={raw}"
        )
        frozen = events_of(logpath, FROZEN_EVENT)
        assert len(frozen) == 1, (
            "AC-9a control: no oracle_frozen over an untruncated set — a GREEN that "
            "refuses EVERY freeze would satisfy the truncated leg and this catches it"
        )
        assert frozen[0]["payload"]["digest"] == expected_digest(repo, ORACLE_FILES)


class TestUnobservedWrittenSet:
    """`[bd8:4a]` (gate G8).  A written set the engine did not observe is not
    an empty oracle.  Reachable through a real caller: `build-cli.ts:19-34,40`
    builds its ctx with no `git_cwd` and spawns the phase runner."""

    def test_ac4a_not_observed_write_tracking_is_indeterminate(
        self, monkeypatch, tmp_path
    ):
        """No `git_cwd` → `write_tracking: "not-observed"` and `written: []`
        (engine.py:787-791).  Freezing that would record a zero-member oracle
        whose every subsequent verify passes trivially — the vacuous-freeze
        shape `[bd8:9]` forbids."""
        install_fixture_workflows(monkeypatch)
        lp = tmp_path / "logs" / "events.jsonl"
        lp.parent.mkdir(parents=True)

        # repo=None → the ctx carries no git_cwd at all.
        rc, payload, raw = run_phase(ORACLE_WORKFLOW, None, lp, "run-ac4a")

        pa = [e for e in events_of(lp, "phase_artifacts")
              if e["payload"].get("phase") == ORACLE_WORKFLOW]
        assert pa and pa[0]["payload"].get("write_tracking") == "not-observed", (
            "`[bd8:4a]` precondition: expected write_tracking 'not-observed' with no "
            f"git_cwd; got {pa and pa[0]['payload'].get('write_tracking')!r}"
        )
        assert_code(payload, raw, E_ORACLE_INDETERMINATE, "`[bd8:4a]` (not-observed)")
        assert rc != 0
        assert not events_of(lp, FROZEN_EVENT), (
            "`[bd8:4a]`: an oracle_frozen was emitted over an UNOBSERVED written set — "
            "a zero-member oracle makes every subsequent verify vacuous"
        )

    def test_ac4a_empty_written_under_git_delta_is_indeterminate(
        self, monkeypatch, repo, logpath
    ):
        """The same rule for an oracle phase that observably wrote NOTHING:
        `write_tracking: "git-delta"` with `written: []`."""
        install_fixture_workflows(monkeypatch, oracle_writes=[])
        rc, payload, raw = freeze(repo, logpath, "run-ac4a-empty")

        pa = [e for e in events_of(logpath, "phase_artifacts")
              if e["payload"].get("phase") == ORACLE_WORKFLOW]
        assert pa and pa[0]["payload"]["written"] == [], (
            f"precondition: expected an empty written set; got {pa and pa[0]['payload']!r}"
        )
        assert_code(payload, raw, E_ORACLE_INDETERMINATE, "`[bd8:4a]` (empty set)")
        assert rc != 0
        assert not events_of(logpath, FROZEN_EVENT), (
            "`[bd8:4a]`: a zero-member oracle was frozen"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-10 (§1g) — the FORCING form.  A GREEN that globs the directory fails.
# ─────────────────────────────────────────────────────────────────────────

class TestSetProvenance:
    def test_ac10_the_frozen_set_is_phase_artifacts_written_not_a_directory_glob(
        self, monkeypatch, repo, logpath
    ):
        """AC-10.  `specs/` holds FOUR files at freeze time; only THREE were
        written by the phase (the fourth is committed at HEAD and so is absent
        from the git delta).  `[bd8:1]` takes the engine's own record.

        A GREEN that derives the SET by listing `specs/` freezes four members
        and fails here.  Note this is about `members`, not `scope`: `[bd8:2b]`
        deliberately puts the decoy inside the SCOPE snapshot."""
        install_fixture_workflows(monkeypatch)
        rc, _, raw = freeze(repo, logpath, "run-ac10")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} raw={raw}"

        on_disk = sorted(p.name for p in (repo / "specs").iterdir())
        assert len(on_disk) == 4, f"AC-10 precondition: expected 4 files, got {on_disk}"

        p = events_of(logpath, FROZEN_EVENT)[0]["payload"]
        paths = member_paths(p)
        assert p["member_count"] == 3, (
            f"AC-10: member_count is {p['member_count']}, not 3 — the freeze took a "
            f"DIRECTORY LISTING of specs/ ({on_disk}) instead of the phase's own "
            f"phase_artifacts.written (§1g)"
        )
        assert DECOY not in paths, (
            f"AC-10: {DECOY!r} is committed at HEAD and was never written by the "
            f"phase, yet it is in the frozen member set: {paths!r}"
        )
        assert paths == sorted(ORACLE_FILES)

    def test_ac10b_the_frozen_members_equal_that_phases_phase_artifacts_written(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:1]`, stated as an identity against the engine's OWN record in
        the same log rather than against the test's expectation — so it holds
        even if the git delta surprises us."""
        install_fixture_workflows(monkeypatch)
        assert freeze(repo, logpath, "run-ac10b")[0] == 0

        pa = [e for e in events_of(logpath, "phase_artifacts")
              if e["payload"].get("phase") == ORACLE_WORKFLOW][0]["payload"]
        p = events_of(logpath, FROZEN_EVENT)[0]["payload"]
        assert member_paths(p) == sorted(pa["written"]), (
            f"`[bd8:1]`: frozen members {member_paths(p)!r} != this phase's own "
            f"phase_artifacts.written {sorted(pa['written'])!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-13 (`[bd8:7]`) — the blast-radius control
# ─────────────────────────────────────────────────────────────────────────

def test_ac13_unmapped_workflow_untouched(monkeypatch, repo, logpath):
    """AC-13.  A run whose workflow is in NEITHER mapping set is untouched:
    exit 0, no freeze, no amendment, none of the §5 codes.

    The blast-radius control: a GREEN that freezes on EVERY phase satisfies
    AC-1..AC-12 while breaking every other phase in the engine.  The fixture
    workflow here WRITES the same files an oracle phase would, so the control
    cannot be satisfied by the run happening to touch nothing."""
    install_fixture_workflows(monkeypatch)
    rc, payload, raw = run_phase(UNMAPPED_WORKFLOW, repo, logpath, "run-ac13")

    assert rc == 0, (
        f"AC-13: an unmapped workflow must run untouched; rc={rc} "
        f"error_code={payload.get('error_code')!r} raw={raw}"
    )
    assert not events_of(logpath, FROZEN_EVENT), (
        "AC-13: a freeze was emitted for a workflow in neither mapping set"
    )
    assert not events_of(logpath, AMENDED_EVENT)
    assert payload.get("error_code") is None, (
        f"AC-13: {payload.get('error_code')!r} raised on an unmapped workflow"
    )


# ─────────────────────────────────────────────────────────────────────────
# AC-11 — the error-code registry, and TRAP 1 (`[bd8:11]`)
# ─────────────────────────────────────────────────────────────────────────

class TestErrorCodeRegistry:
    def test_ac11_four_codes_registered_with_a_condition_and_no_drift(self):
        """AC-11.  Every §5 code present in `error_codes.ERROR_CODES` with a
        one-line condition, AND `error_codes.py --check` reports no drift.

        `[bd8:11]` TRAP 1 — this is ONE test on purpose.  `--check` returns 1
        on `DEAD` (registered, raised nowhere) as well as on `UNREGISTERED`
        (error_codes.py:305-317), and `HARVEST_EXCLUDE_DIRS` contains
        `"tests"` (error_codes.py:23), so a code raised only from THIS file is
        still dead.  Registering the four codes without raising them in
        production code turns the currently-green `--check` half RED.  The two
        halves cannot be satisfied separately, and early registration does not
        satisfy this test."""
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
            "(tests/ is excluded from the harvest) — register the codes in the "
            "SAME change that raises them."
        )

    def test_ac11b_error_codes_md_is_regenerated_not_hand_edited(self):
        """§8 in-scope list: `ERROR_CODES.md` is `--markdown` OUTPUT.  Pinned
        byte-for-byte against the renderer so a hand-edit cannot drift."""
        import error_codes  # noqa: PLC0415 — deferred (§1q)

        doc = _ENGINE_ROOT / "ERROR_CODES.md"
        assert doc.exists(), "ERROR_CODES.md is missing"
        assert doc.read_text(encoding="utf-8") == error_codes.render_markdown(), (
            "ERROR_CODES.md is not the current `error_codes.py --markdown` output — "
            "regenerate it, do not hand-edit it (§8)"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-12 (bd#22 AC-C1) — importing conformance.oracle does no I/O
# ─────────────────────────────────────────────────────────────────────────

class TestNoIoAtImport:
    def test_ac12_import_conformance_oracle_has_no_side_effects(self, monkeypatch):
        """AC-12.  Kills an implementation that does eager work at import time
        (reading a fixture, resolving a version, spawning a subprocess to find
        a git root, or scanning a directory to build a registry).

        The recorders are installed and the module is force-reimported inside
        this test, so a module already resident from an earlier test cannot
        make the assertion vacuous."""
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
        """`[bd8:5]` binds the bd#22 package invariant; a namespace package
        would make the import above resolve vacuously."""
        import conformance  # noqa: PLC0415 — deferred (§1q)

        assert conformance.__file__ is not None, (
            "conformance resolved as a NAMESPACE package — `[bd8:5]`/bd#22 AC-C1 "
            "requires a real package"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-14 (§8 scope) — the fence
# ─────────────────────────────────────────────────────────────────────────

class TestScopeFence:
    """AC-14.  §8: "A diff touching any of them is out of contract and should
    be rejected without reading further: it means the freeze migrated back
    into the phase it is meant to bound."

    A fence asserts an absence that cannot yet be violated.  It dies the
    moment a GREEN reaches for the shape the `chokepoint` header forbids."""

    def test_ac14a_emit_phase_artifacts_finally_knows_nothing_about_the_oracle(self):
        """`_emit_phase_artifacts` runs in a `finally` documented as unable to
        do I/O or raise (`[G18r3:EDGE-1]`, engine.py:770-786).  Both the
        freeze and the verify must READ FILE CONTENTS."""
        import ast  # noqa: PLC0415 — deferred (§1q)

        src = (_ENGINE_ROOT / "engine.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_emit_phase_artifacts"),
            None,
        )
        assert fn is not None, "engine.py no longer defines _emit_phase_artifacts"
        body = ast.get_source_segment(src, fn) or ""
        assert "oracle" not in body.lower(), (
            "§8: the freeze/verify migrated into engine._emit_phase_artifacts — the "
            "finally that `[G18r3:EDGE-1]` documents as unable to do I/O or raise. "
            "The chokepoint is run.py main()."
        )

    def test_ac14b_no_workflow_module_reaches_the_oracle_seam(self):
        """§8: `engine_py/workflows/` is out of scope.  A step inside the
        oracle phase could rewrite its own frozen set AFTER the freeze — the
        exact reason the `chokepoint` header rejects a per-workflow step."""
        # Narrow to the SEAM, not the word: `phase_5_integrity.py:470` already
        # says "the sole completeness oracle" in a comment, and a fence that
        # fires on prose is a fence nobody keeps.
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
        """`[bd8:6]`: the three call sites are all in `run.py main()`. Nothing
        outside run.py and the conformance package may reach the seam."""
        allowed = {"run.py"}
        offenders = []
        for p in _ENGINE_ROOT.rglob("*.py"):
            rel = p.relative_to(_ENGINE_ROOT)
            if rel.parts[0] in {"tests", "conformance", "__pycache__"}:
                continue
            if str(rel) in allowed:
                continue
            if "conformance.oracle" in p.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(rel))
        assert not offenders, (
            f"`[bd8:6]`/§8: modules other than run.py wire the oracle seam: {offenders}"
        )
