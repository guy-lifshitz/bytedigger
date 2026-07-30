"""RED tests for bd#8 — BD-L1, oracle freeze-and-verify (R1.1-R1.5).

Frozen spec: engine_py/conformance/ORACLE_SPEC.md (FROZEN v3, base 2b6589f).
Discipline inherited verbatim from the sibling conformance lots:
EMISSIONS_SPEC.md §0.1-§0.6, CONTRACTS_SPEC.md §0.1-§0.8, and the bd#10
header conventions (collection safety, external provenance, declared
pre-passing shields).

COLLECTION SAFETY (§1q / D1CF5FDF).  `conformance.oracle` does NOT exist on
this base.  Every reference to it is DEFERRED into a test body, so this
module COLLECTS cleanly pre-GREEN and FAILS at assert/call time.  There is no
module-level `sys.path` mutation and no `from conftest import` — the engine_py
conftest injects the roots at conftest-import time (tests/conftest.py:36-49),
which is the sanctioned seam (81F97F3D).

EXTERNAL PROVENANCE (§0.2).  Every pinned constant below cites a source
OUTSIDE the artifact under test:
  * The four error-code strings are pinned as LITERALS citing ORACLE_SPEC §5.
    They are deliberately NOT imported from `error_codes.ERROR_CODES` —
    this lot writes those entries, and pinning against them would be the
    §0.1 subtype (3) vacuum.
  * Every expected digest is recomputed in the test from the bytes the test
    itself wrote, with stdlib `hashlib`, per the `[bd8:2]` construction —
    never read back out of the event under test, and never through the
    module under test.

HARNESS SEAMS (measured on this base, not assumed):
  * `lib/phase_sentinel.execute_engine` builds its engine and calls
    `workflows.register_all(eng)` at CALL time (phase_sentinel.py:171-175),
    so `monkeypatch.setattr(workflows, "register_all", ...)` installs fixture
    workflows into a REAL engine run.  `WorkflowEngine.register` RAISES on a
    duplicate name (engine.py:228-231), so the fixture registrar registers
    ONLY the fixtures — it does not re-register the production set.
  * Fixture workflows are registered under the REAL registry names
    `phase_45_spec` / `phase_5_implement` (workflows/__init__.py:34,36), so
    no test pins whatever internal name `[bd8:7]`'s module-level mapping ends
    up having.  See SPEC GAPS G3 on the hyphen/underscore spelling.
  * The written set is the engine's git delta: `_written` is populated only
    when `org_config["git_cwd"]` resolves to a git repo
    (engine.py:407,425,493 via `_resolve_scan_cwd`, engine.py:1146-1149), and
    the paths it records are repo-root-relative — which is what `[bd8:3]`
    requires and why this file never asserts an absolute path.
  * The event log is kept OUTSIDE the fixture repo on purpose: a log (or the
    phase sentinel / restart-governor state written beside it) inside the
    repo would enter the git delta and silently join the oracle set.
  * `execute_native_workflow` serves a cached success for a repeated
    `phase_key(run_id, workflow_name, ctx)` (phase_sentinel.py:308-331,
    dbos_setup.py:409), short-circuiting the engine.  Re-entering the same
    phase in one run therefore varies `question` so the ctx-hash suffix — and
    thus the key — differs; otherwise the second entry would emit no events
    at all and AC-8 would measure the sentinel, not the amendment.
  * `init_dbos()` returns immediately under the default `native` backend
    (dbos_setup.py:122-123), so nothing here patches it.  No harness
    primitive this file runs on is sabotaged ([G22:2]).

DECLARED PRE-PASSING (§0.6).  MEASURED on base 2b6589f/f175f10, not predicted:
`pytest tests/test_bd8_l1_oracle.py` is **18 failed, 8 passed**.  Every one of
the eight is named here with the reason it passes and what kills it, because
an undeclared pre-passing test is exactly the vacuum this lot exists to
refuse.  NONE of the eight is an AC that this lot must make true — they are
controls, fences and shields, and every requirement-bearing AC is in the 18.

  CONTROLS — these must pass BEFORE and AFTER.  Their whole force is
  post-GREEN: they are what stops a GREEN from satisfying the adversaries by
  refusing everything.  Today they pass vacuously (nothing verifies at all);
  after GREEN they pass only if the verify discriminates.
    1. `TestFalseFree::test_ac3a_unchanged_tree_verifies_and_passes` — AC-3a.
       The spec says so in as many words: "Without this, a verify that always
       fails passes AC-2/AC-3."
    2. `TestPhaseOrdering::test_ac6_...` — AC-6.  See the NOTE ON AC-6 below:
       R1.1 is already true on this base, so this is a fence over an existing
       property, and `test_ac6b` is the part this lot must make true.
    3. `test_bd8_7_unmapped_workflow_untouched` — `[bd8:7]`.  The blast-radius
       control: a GREEN that freezes on EVERY phase passes all 18 and breaks
       every other phase in the engine.

  FENCES — they assert an absence that cannot yet be violated.  That is what a
  fence IS.  Each dies the moment a GREEN reaches for the shape the frozen
  `chokepoint` header and §8 were written to forbid:
    4. `TestScopeFence::test_ac_s1a...` — the freeze must not migrate into
       `engine._emit_phase_artifacts`'s `finally` (`[G18r3:EDGE-1]`).
    5. `TestScopeFence::test_ac_s1b...` — no `engine_py/workflows/` module may
       reach the seam; a step runs inside the phase it is meant to bound.
    6. `TestScopeFence::test_ac_s1c...` — `[bd8:6]`: run.py is the only wiring
       site.

  SHIELDS — already-true properties this lot must not break:
    7. `TestErrorCodeRegistry::test_ac11b...` — `ERROR_CODES.md` is byte-equal
       to `--markdown` output.  §8 lists it as regenerated, not hand-edited;
       this is what makes "regenerated" checkable.
    8. `TestNoIoAtImport::test_ac12b_conformance_is_a_real_package` — bd#22's
       `__init__.py` invariant, which `[bd8:5]` binds.  Without it, AC-12's
       import could resolve as a namespace package and assert nothing.

  ONE HALF-ASSERTION inside a FAILING test is also pre-passing, and it is the
  load-bearing one: AC-11's `error_codes.main(["--check"]) == 0`.  The drift
  gate is clean on this tree.  It is the shield for `[bd8:11]` (TRAP 1): the
  ERROR_CODES-membership half FAILS today, and the moment a GREEN satisfies
  membership by registering the four codes WITHOUT raising them in production
  code, this half flips to 1 with four `DEAD` lines.  `HARVEST_EXCLUDE_DIRS`
  contains `"tests"` (error_codes.py:23), so raising them from THIS file does
  not revive them.  The two halves are in ONE test on purpose.

  NOTE ON AC-6 (in the 18 as `test_ac6b`, passing as `test_ac6`).  R1.1's
  ordering property is ALREADY TRUE on this base — ORACLE_SPEC §0 says so
  ("the engine already produces the two halves separately").  `test_ac6`
  therefore passes today and is a fence, not a measurement; `test_ac6b`, which
  places the FREEZE between the two phases, is the part this lot must make
  true, and it fails.

DERIVED, NOT NEW REQUIREMENTS.  Two checks below bind normative spec text
that carries no numbered AC, and are labelled as such rather than smuggled in
as ACs: `AC-S1` binds §8's scope list, and `test_bd8_7_unmapped_workflow_
untouched` binds `[bd8:7]`'s "a run whose workflow is neither is untouched".
If the gate disagrees that a frozen §8 may be enforced by a test, both are
severable without touching AC-1..AC-12.

SPEC GAPS — see the `SPEC GAPS` section at the END of this file.  Four gaps
were found while writing this RED (G3-G6).  G5 and G6 each block a leg of an
AC; those legs are NOT written, and no test below guesses past a gap.
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

# ── Pinned literals (ORACLE_SPEC §5) — never imported from error_codes.py ──
E_ORACLE_MUTATED = "E_ORACLE_MUTATED"
E_ORACLE_UNFROZEN = "E_ORACLE_UNFROZEN"
E_ORACLE_INDETERMINATE = "E_ORACLE_INDETERMINATE"
E_ORACLE_AMENDMENT_UNREASONED = "E_ORACLE_AMENDMENT_UNREASONED"

# Registry names, measured at workflows/__init__.py:34,36 (see SPEC GAPS G3).
ORACLE_WORKFLOW = "phase_45_spec"
IMPL_WORKFLOW = "phase_5_implement"

FROZEN_EVENT = "oracle_frozen"
AMENDED_EVENT = "oracle_amended"


# ─────────────────────────────────────────────────────────────────────────
# Independent digest — the `[bd8:2]` construction, recomputed from bytes the
# test itself wrote.  Never calls the module under test.
# ─────────────────────────────────────────────────────────────────────────

def expected_digest(repo: Path, relpaths: list[str]) -> str:
    """`[bd8:2]`: for each path in SORTED order, the line

        <relpath>\\0<sha256-of-bytes>

    joined by "\\n"; digest = "sha256:" + sha256(that).
    """
    lines = []
    for rel in sorted(relpaths):
        body = (repo / rel).read_bytes()
        lines.append(f"{rel}\0{hashlib.sha256(body).hexdigest()}")
    joined = "\n".join(lines)
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# Fixture repo + event-log reading
# ─────────────────────────────────────────────────────────────────────────

ORACLE_FILES = ["specs/build-spec.md", "specs/build-plan-review.md", "specs/notes.md"]
DECOY = "specs/decoy.md"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with ONE pre-committed file inside the oracle directory.

    The decoy is committed at HEAD, so it is invisible to the phase's git
    delta while being plainly visible to a directory listing — this is the
    whole force of AC-10.
    """
    r = tmp_path / "repo"
    (r / "specs").mkdir(parents=True)
    init_repo(str(r))
    (r / DECOY).write_text("decoy committed at HEAD; never in any written set\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=r, check=True)
    return r


@pytest.fixture
def logpath(tmp_path: Path) -> Path:
    """Event log OUTSIDE the fixture repo — see HARNESS SEAMS."""
    d = tmp_path / "eventlogs"
    d.mkdir()
    return d / "events.jsonl"


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def events_of(path: Path, event_type: str) -> list[dict]:
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
    """Build a step that writes `relpaths` into ctx.org_config['git_cwd'].

    `unreadable` members are chmod 000 AFTER writing: `git ls-files --others`
    lists a file by name without reading it, so the engine still records the
    path in `written` while the freeze cannot read its bytes (AC-9).
    """
    from contracts import StepResult  # noqa: PLC0415 — deferred (§1q)

    def _exec(context, prev):
        root = Path((getattr(context, "org_config", None) or {})["git_cwd"])
        for rel in relpaths:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content of {rel} for {context.question}\n")
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


def install_fixture_workflows(
    monkeypatch,
    oracle_writes: list[str] = ORACLE_FILES,
    unreadable: list[str] | None = None,
    extra: dict | None = None,
) -> None:
    """Replace `workflows.register_all` with a registrar of fixture workflows only."""
    from contracts import StepContract, WorkflowDefinition  # noqa: PLC0415 — deferred
    import workflows  # noqa: PLC0415 — deferred

    def _register_all(engine) -> None:
        engine.register(ORACLE_WORKFLOW, WorkflowDefinition(
            name=ORACLE_WORKFLOW,
            steps=[StepContract(name="bd8_write",
                                execute=_writer_step(oracle_writes, unreadable))],
        ))
        engine.register(IMPL_WORKFLOW, WorkflowDefinition(
            name=IMPL_WORKFLOW,
            steps=[StepContract(name="bd8_noop", execute=_noop_step())],
        ))
        for name, wf in (extra or {}).items():
            engine.register(name, wf)

    monkeypatch.setattr(workflows, "register_all", _register_all)


def run_phase(workflow: str, repo: Path, logpath: Path, run_id: str,
              question: str = "q") -> tuple[int, str]:
    """Invoke run.main() for one phase; return (exit_code, stdout+stderr).

    One run.main() call == one process boundary == the `chokepoint` this lot's
    freeze and verify are specified to live at.
    """
    import run as run_module  # noqa: PLC0415 — deferred (§1q)

    ctx = json.dumps({
        "tenant_id": "bd8",
        "session_id": "bd8-session",
        "question": question,
        "org_config": {"git_cwd": str(repo)},
    })
    argv = ["run.py", "--workflow", workflow, "--ctx-json", ctx,
            "--event-log", str(logpath), "--run-id", run_id]
    out, err = io.StringIO(), io.StringIO()
    rc = 1
    with patch("sys.argv", argv), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = run_module.main()
        except SystemExit as e:  # pragma: no cover — main() returns, it does not exit
            rc = int(e.code or 0)
    return rc, out.getvalue() + err.getvalue()


def freeze(repo: Path, logpath: Path, run_id: str, **kw) -> tuple[int, str]:
    return run_phase(ORACLE_WORKFLOW, repo, logpath, run_id, **kw)


def verify(repo: Path, logpath: Path, run_id: str, **kw) -> tuple[int, str]:
    return run_phase(IMPL_WORKFLOW, repo, logpath, run_id, **kw)


# ─────────────────────────────────────────────────────────────────────────
# AC-1 (R1.3) — the freeze event and its digest
# ─────────────────────────────────────────────────────────────────────────

class TestFreeze:
    def test_ac1_freeze_emits_one_event_with_the_bd8_2_digest(
        self, monkeypatch, repo, logpath
    ):
        """AC-1. Exactly one `oracle_frozen` for this run_id; member_count 3;
        members are the three repo-relative paths in sorted order; digest
        equals the `[bd8:2]` construction computed independently here."""
        install_fixture_workflows(monkeypatch)
        rc, out = freeze(repo, logpath, "run-ac1")
        assert rc == 0, f"oracle phase must succeed; got rc={rc}, out={out}"

        frozen = events_of(logpath, FROZEN_EVENT)
        assert len(frozen) == 1, (
            f"AC-1: expected exactly one {FROZEN_EVENT}, got {len(frozen)}. "
            f"Event types seen: {sorted({e['event_type'] for e in read_events(logpath)})}"
        )
        ev = frozen[0]
        assert ev["run_id"] == "run-ac1"
        p = ev["payload"]
        assert p["member_count"] == 3, f"AC-1: member_count {p['member_count']} != 3"
        assert member_paths(p) == sorted(ORACLE_FILES), (
            f"AC-1: members {p['members']!r} are not the three sorted "
            f"repo-relative paths {sorted(ORACLE_FILES)!r} (`[bd8:3]`)"
        )
        assert p["digest"] == expected_digest(repo, ORACLE_FILES), (
            "AC-1: frozen digest does not match the `[bd8:2]` construction "
            "recomputed independently from the bytes this test wrote"
        )
        assert p["phase"] == ORACLE_WORKFLOW

    def test_ac1b_per_member_digests_are_the_member_file_digests(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:8]` pins `members:[{path, digest}]`.  Recomputed here from
        the bytes on disk, not read back out of the event."""
        install_fixture_workflows(monkeypatch)
        freeze(repo, logpath, "run-ac1b")
        p = events_of(logpath, FROZEN_EVENT)[0]["payload"]
        got = {m["path"]: m["digest"] for m in p["members"]}
        want = {
            rel: hashlib.sha256((repo / rel).read_bytes()).hexdigest()
            for rel in ORACLE_FILES
        }
        assert got == want or got == {k: "sha256:" + v for k, v in want.items()}, (
            f"`[bd8:8]`: per-member digests {got!r} are not the sha256 of the "
            f"member bytes {want!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-2 / AC-3 / AC-4 — the two ADVERSARIES plus removal.  §9 licenses the
# BD-L1 claim over ADV-1 and ADV-2 *because these two run*, not because the
# design says they would fail.
# ─────────────────────────────────────────────────────────────────────────

class TestAdversaries:
    def _frozen_then(self, monkeypatch, repo, logpath, run_id, mutate) -> tuple[int, str]:
        install_fixture_workflows(monkeypatch)
        rc, out = freeze(repo, logpath, run_id)
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} out={out}"
        assert events_of(logpath, FROZEN_EVENT), "precondition: nothing was frozen"
        mutate()
        return verify(repo, logpath, run_id)

    def test_ac2_adv1_rewriting_a_member_is_e_oracle_mutated(
        self, monkeypatch, repo, logpath
    ):
        """AC-2 (ADV-1).  Freeze, rewrite one byte of one member, run the
        implementing phase → non-zero exit, `E_ORACLE_MUTATED`, and the
        message NAMES the offending path."""
        target = "specs/build-spec.md"

        def _rewrite():
            p = repo / target
            p.write_bytes(p.read_bytes().replace(b"content", b"CONTENT"))

        rc, out = self._frozen_then(monkeypatch, repo, logpath, "run-adv1", _rewrite)
        assert rc != 0, f"ADV-1: implementing phase exited 0 after a member was rewritten: {out}"
        assert E_ORACLE_MUTATED in out, f"ADV-1: {E_ORACLE_MUTATED} not raised; got: {out}"
        assert target in out, (
            f"AC-2 requires the failure to name the offending path {target!r}; got: {out}"
        )

    def test_ac3_adv2_adding_a_file_is_e_oracle_mutated(
        self, monkeypatch, repo, logpath
    ):
        """AC-3 (ADV-2).  Freeze, ADD a file to the oracle directory without
        re-entering the oracle phase → `E_ORACLE_MUTATED`.

        This is the AC that fails if the digest is taken over CONTENT alone
        (`[bd8:2]`): every frozen member is byte-identical here."""
        added = "specs/smuggled.md"

        def _add():
            (repo / added).write_text("added after the freeze, no re-entry\n")

        rc, out = self._frozen_then(monkeypatch, repo, logpath, "run-adv2", _add)
        assert rc != 0, f"ADV-2: implementing phase exited 0 after a file was added: {out}"
        assert E_ORACLE_MUTATED in out, f"ADV-2: {E_ORACLE_MUTATED} not raised; got: {out}"

    def test_ac4_removing_a_member_is_e_oracle_mutated(
        self, monkeypatch, repo, logpath
    ):
        """AC-4.  Freeze, delete a member → `E_ORACLE_MUTATED`, naming it."""
        target = "specs/notes.md"

        rc, out = self._frozen_then(
            monkeypatch, repo, logpath, "run-rm", lambda: (repo / target).unlink()
        )
        assert rc != 0, f"removal: implementing phase exited 0 after a member was deleted: {out}"
        assert E_ORACLE_MUTATED in out, f"removal: {E_ORACLE_MUTATED} not raised; got: {out}"
        assert target in out, f"AC-4: failure must name the removed path {target!r}; got: {out}"

    def test_ac4b_the_three_failure_messages_are_pairwise_distinguishable(
        self, monkeypatch, tmp_path
    ):
        """AC-4 requires the removal case be "distinguished in the message
        from the mutation and addition cases".

        The spec pins NO vocabulary for that distinction, so this test pins
        DISTINGUISHABILITY rather than words: three runs, three adversaries,
        three messages that must be pairwise different once the shared
        volatile parts (run ids, tmp paths, durations) are stripped.  A GREEN
        that emits one generic "digest mismatch" line for all three fails
        here without this test having invented spec text."""
        msgs = {}
        for tag, mutate_name in (("rewrite", "specs/build-spec.md"),
                                 ("add", "specs/smuggled.md"),
                                 ("remove", "specs/notes.md")):
            r = tmp_path / f"repo-{tag}"
            (r / "specs").mkdir(parents=True)
            init_repo(str(r))
            (r / DECOY).write_text("decoy\n")
            subprocess.run(["git", "add", "-A"], cwd=r, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=r, check=True)
            lp = tmp_path / f"log-{tag}" / "events.jsonl"
            lp.parent.mkdir()

            with pytest.MonkeyPatch.context() as mp:
                install_fixture_workflows(mp)
                rc, _ = freeze(r, lp, f"run-{tag}")
                assert rc == 0
                if tag == "rewrite":
                    p = r / mutate_name
                    p.write_bytes(p.read_bytes().replace(b"content", b"CONTENT"))
                elif tag == "add":
                    (r / mutate_name).write_text("smuggled\n")
                else:
                    (r / mutate_name).unlink()
                _, out = verify(r, lp, f"run-{tag}")
            msgs[tag] = out.replace(str(r), "<REPO>").replace(str(lp), "<LOG>") \
                           .replace(f"run-{tag}", "<RUN>")

        for a, b in (("rewrite", "add"), ("rewrite", "remove"), ("add", "remove")):
            assert msgs[a] != msgs[b], (
                f"AC-4: the {a!r} and {b!r} adversaries produce an IDENTICAL failure "
                f"message; the removal case is not distinguished. Message: {msgs[a]!r}"
            )


# ─────────────────────────────────────────────────────────────────────────
# AC-3a — the FALSE-FREE leg.  Without this, a verify that always fails
# passes AC-2 and AC-3 and nobody notices.
# ─────────────────────────────────────────────────────────────────────────

class TestFalseFree:
    def test_ac3a_unchanged_tree_verifies_and_passes(self, monkeypatch, repo, logpath):
        """AC-3a.  Freeze, change NOTHING, run the implementing phase →
        exit 0 and no `E_ORACLE_MUTATED` anywhere in the output."""
        install_fixture_workflows(monkeypatch)
        rc, out = freeze(repo, logpath, "run-ac3a")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} out={out}"

        rc2, out2 = verify(repo, logpath, "run-ac3a")
        assert rc2 == 0, (
            f"AC-3a: an UNCHANGED oracle must verify and pass; got rc={rc2}, out={out2}. "
            "A verify that fails unconditionally would still pass AC-2 and AC-3."
        )
        assert E_ORACLE_MUTATED not in out2, (
            f"AC-3a: {E_ORACLE_MUTATED} raised over an unchanged tree: {out2}"
        )
        assert E_ORACLE_UNFROZEN not in out2, (
            f"AC-3a: {E_ORACLE_UNFROZEN} raised although the freeze is in this very log: {out2}"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-5 (`[bd8:9]`) — absence of a freeze is NOT permission
# ─────────────────────────────────────────────────────────────────────────

class TestUnfrozen:
    def test_ac5_implementing_phase_with_no_freeze_event_fails(
        self, monkeypatch, repo, logpath
    ):
        """AC-5.  Implementing phase over a log carrying no freeze →
        `E_ORACLE_UNFROZEN`, never a pass.  This is the vacuous-pass shape
        `[bd8:9]` exists to forbid."""
        install_fixture_workflows(monkeypatch)
        rc, out = verify(repo, logpath, "run-ac5")
        assert rc != 0, f"AC-5: implementing phase passed with no freeze recorded: {out}"
        assert E_ORACLE_UNFROZEN in out, f"AC-5: {E_ORACLE_UNFROZEN} not raised; got: {out}"

    def test_ac5b_run_id_mismatch_against_a_freeze_in_this_log_fails_closed(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:8a]`.  The lookup is scoped to the LOG, with run_id as a
        fail-closed CROSS-CHECK: when both the freeze event and this
        invocation carry a run_id and they differ, that is
        `E_ORACLE_UNFROZEN` — never a pass.  Kills the stale-log class
        (GH#215) where a previous build's freeze authorises this one."""
        install_fixture_workflows(monkeypatch)
        rc, out = freeze(repo, logpath, "run-A")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} out={out}"

        rc2, out2 = verify(repo, logpath, "run-B")
        assert rc2 != 0, (
            f"`[bd8:8a]`: a freeze carrying run_id 'run-A' must not authorise an "
            f"invocation carrying 'run-B'; got rc={rc2}, out={out2}"
        )
        assert E_ORACLE_UNFROZEN in out2, (
            f"`[bd8:8a]`: run_id cross-check mismatch must be {E_ORACLE_UNFROZEN}; got: {out2}"
        )


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

    def test_ac6b_the_freeze_lands_before_the_implementing_phase_starts(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:6]`: freeze AFTER the oracle phase's execute() returns, verify
        BEFORE the implementing phase's execute() is entered.  A freeze emitted
        after the implementing phase began would bound nothing."""
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


# ─────────────────────────────────────────────────────────────────────────
# AC-8 (R1.5) — amendment.  Only the UNREASONED leg is written; the reasoned
# leg is blocked by SPEC GAP G6 (no declared input channel for `reason`).
# ─────────────────────────────────────────────────────────────────────────

class TestAmendment:
    def test_ac8_reentry_without_a_reason_is_unreasoned(
        self, monkeypatch, repo, logpath
    ):
        """AC-8, unreasoned leg.  A second oracle-phase execute() in the same
        run with NO reason supplied through any channel fails
        `E_ORACLE_AMENDMENT_UNREASONED` (`[bd8:10]`).

        Decidable without G6: whatever the channel turns out to be, ABSENT is
        absent.  `question` varies so the phase-sentinel key differs and the
        engine actually re-runs (see HARNESS SEAMS) — otherwise this would
        measure the sentinel cache, not the amendment."""
        install_fixture_workflows(monkeypatch)
        rc, out = freeze(repo, logpath, "run-ac8", question="first")
        assert rc == 0, f"precondition: first freeze must succeed; rc={rc} out={out}"
        assert len(events_of(logpath, FROZEN_EVENT)) == 1

        rc2, out2 = freeze(repo, logpath, "run-ac8", question="second")
        assert rc2 != 0, (
            f"AC-8: re-entering the oracle phase with no reason must fail "
            f"{E_ORACLE_AMENDMENT_UNREASONED}; got rc={rc2}, out={out2}"
        )
        assert E_ORACLE_AMENDMENT_UNREASONED in out2, (
            f"AC-8: {E_ORACLE_AMENDMENT_UNREASONED} not raised on a reasonless "
            f"re-entry; got: {out2}"
        )

    def test_ac8b_reentry_never_emits_a_second_oracle_frozen(
        self, monkeypatch, repo, logpath
    ):
        """`[bd8:10]`: re-entry emits `oracle_amended` INSTEAD OF a second
        `oracle_frozen`.  A second `oracle_frozen` would make the verify's
        "LAST freeze event" lookup silently authorise a re-write that never
        declared itself an amendment."""
        install_fixture_workflows(monkeypatch)
        assert freeze(repo, logpath, "run-ac8b", question="first")[0] == 0
        freeze(repo, logpath, "run-ac8b", question="second")
        assert len(events_of(logpath, FROZEN_EVENT)) == 1, (
            f"`[bd8:10]`: re-entry emitted a second {FROZEN_EVENT}; it must emit "
            f"{AMENDED_EVENT} (or fail unreasoned), never a second freeze"
        )


# ─────────────────────────────────────────────────────────────────────────
# AC-9 / AC-9a — fail-closed freezes, each with its CONTROL leg
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="chmod 000 does not deny root; AC-9 is unmeasurable as root",
)
class TestIndeterminateFreeze:
    def test_ac9_unreadable_member_is_indeterminate_and_emits_no_freeze(
        self, monkeypatch, repo, logpath
    ):
        """AC-9 (`[bd8:4]`).  A declared member unreadable at freeze time →
        `E_ORACLE_INDETERMINATE` and NO `oracle_frozen` event.  A member that
        cannot be read is NOT a zero-byte member: a half-happened freeze is
        worse than none."""
        install_fixture_workflows(monkeypatch, unreadable=["specs/notes.md"])
        try:
            rc, out = freeze(repo, logpath, "run-ac9")
            assert rc != 0, f"AC-9: freeze succeeded over an unreadable member: {out}"
            assert E_ORACLE_INDETERMINATE in out, (
                f"AC-9: {E_ORACLE_INDETERMINATE} not raised; got: {out}"
            )
            assert not events_of(logpath, FROZEN_EVENT), (
                "AC-9: an oracle_frozen was emitted despite an unreadable member — "
                "this is the half-happened freeze `[bd8:4]` forbids"
            )
        finally:
            os.chmod(repo / "specs/notes.md", 0o644)


class TestTruncatedWrittenSet:
    """AC-9a (G1).  `phase_artifacts` replaces `written` with a SAMPLE plus
    `written_truncated: true` when the serialised line exceeds the log's
    per-line limit (engine.py:1320-1334).  A freeze over a sampled set would
    record a digest for a membership the engine never saw whole.

    The truncation is forced by lowering engine.py's OWN limit constant from
    the test — the real truncation code path runs.  Nothing in `engine.py` is
    modified by this lot (§8); the constant is a harness knob, not a UUT.
    """

    def test_ac9a_truncated_written_is_indeterminate_and_emits_no_freeze(
        self, monkeypatch, repo, logpath
    ):
        import engine as engine_mod  # noqa: PLC0415 — deferred (§1q)

        install_fixture_workflows(monkeypatch)
        monkeypatch.setattr(engine_mod, "_EVENT_LOG_LINE_LIMIT_BYTES", 200, raising=True)
        rc, out = freeze(repo, logpath, "run-ac9a")

        pa = [e for e in events_of(logpath, "phase_artifacts")
              if e["payload"].get("phase") == ORACLE_WORKFLOW]
        assert pa and pa[0]["payload"].get("written_truncated") is True, (
            "AC-9a precondition: the harness failed to force a truncated "
            f"phase_artifacts payload; got {pa and pa[0]['payload']!r}"
        )
        assert rc != 0, f"AC-9a: freeze succeeded over a TRUNCATED written set: {out}"
        assert E_ORACLE_INDETERMINATE in out, (
            f"AC-9a: {E_ORACLE_INDETERMINATE} not raised on written_truncated; got: {out}"
        )
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
        rc, out = freeze(repo, logpath, "run-ac9a-ctl")

        pa = [e for e in events_of(logpath, "phase_artifacts")
              if e["payload"].get("phase") == ORACLE_WORKFLOW]
        assert pa and "written_truncated" not in pa[0]["payload"], (
            "AC-9a control precondition: this payload must NOT be truncated"
        )
        assert rc == 0, f"AC-9a control: the untruncated freeze must succeed; out={out}"
        frozen = events_of(logpath, FROZEN_EVENT)
        assert len(frozen) == 1, (
            "AC-9a control: no oracle_frozen over an untruncated set — a GREEN that "
            "refuses EVERY freeze would satisfy the truncated leg and this is what "
            "catches it"
        )
        assert frozen[0]["payload"]["digest"] == expected_digest(repo, ORACLE_FILES)


# ─────────────────────────────────────────────────────────────────────────
# AC-10 (§1g) — the FORCING form.  A GREEN that globs the directory fails.
# ─────────────────────────────────────────────────────────────────────────

class TestSetProvenance:
    def test_ac10_the_frozen_set_is_phase_artifacts_written_not_a_directory_glob(
        self, monkeypatch, repo, logpath
    ):
        """AC-10.  `specs/` holds FOUR files at freeze time; only THREE were
        written by the phase (the fourth is committed at HEAD and so is absent
        from the git delta).  `[bd8:1]` takes the engine's own record, so the
        frozen set is the three.

        A GREEN that derives the set by listing `specs/` freezes four members
        and fails here.  A GREEN that hand-lists a manifest or matches a
        naming convention also fails: `specs/notes.md` matches nothing
        special and `specs/decoy.md` matches whatever `specs/*.md` would."""
        install_fixture_workflows(monkeypatch)
        rc, out = freeze(repo, logpath, "run-ac10")
        assert rc == 0, f"precondition: freeze must succeed; rc={rc} out={out}"

        on_disk = sorted(p.name for p in (repo / "specs").iterdir())
        assert len(on_disk) == 4, f"AC-10 precondition: expected 4 files in specs/, got {on_disk}"

        p = events_of(logpath, FROZEN_EVENT)[0]["payload"]
        paths = member_paths(p)
        assert p["member_count"] == 3, (
            f"AC-10: member_count is {p['member_count']}, not 3 — the freeze took a "
            f"DIRECTORY LISTING of specs/ ({on_disk}) instead of the phase's own "
            f"phase_artifacts.written (§1g)"
        )
        assert DECOY not in paths, (
            f"AC-10: {DECOY!r} is committed at HEAD and was never written by the "
            f"phase, yet it is in the frozen set: {paths!r}"
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
        paths = member_paths(p)
        assert paths == sorted(pa["written"]), (
            f"`[bd8:1]`: frozen members {paths!r} != this phase's own "
            f"phase_artifacts.written {sorted(pa['written'])!r}"
        )


# ─────────────────────────────────────────────────────────────────────────
# `[bd8:7]` (DERIVED — normative text, no numbered AC) — an unmapped
# workflow is untouched: no freeze, no verify, no event.
# ─────────────────────────────────────────────────────────────────────────

def test_bd8_7_unmapped_workflow_untouched(monkeypatch, repo, logpath):
    """`[bd8:7]`: "A run whose workflow is neither is untouched by this lot —
    no freeze, no verify, no event."

    Also the blast-radius fence: without this, a GREEN that freezes on EVERY
    phase would pass every AC above while breaking every other phase in the
    engine."""
    from contracts import StepContract, WorkflowDefinition  # noqa: PLC0415 — deferred

    other = WorkflowDefinition(
        name="phase_2_explore",
        steps=[StepContract(name="bd8_write", execute=_writer_step(ORACLE_FILES))],
    )
    install_fixture_workflows(monkeypatch, extra={"phase_2_explore": other})
    rc, out = run_phase("phase_2_explore", repo, logpath, "run-other")

    assert rc == 0, f"`[bd8:7]`: an unmapped workflow must run untouched; rc={rc} out={out}"
    assert not events_of(logpath, FROZEN_EVENT), (
        "`[bd8:7]`: a freeze was emitted for a workflow that is neither the "
        "oracle-authoring nor the implementing phase"
    )
    assert not events_of(logpath, AMENDED_EVENT)
    for code in (E_ORACLE_MUTATED, E_ORACLE_UNFROZEN, E_ORACLE_INDETERMINATE):
        assert code not in out, f"`[bd8:7]`: {code} raised on an unmapped workflow: {out}"


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
        halves cannot be satisfied separately."""
        import error_codes  # noqa: PLC0415 — deferred (§1q)

        missing = [c for c in (E_ORACLE_MUTATED, E_ORACLE_UNFROZEN,
                               E_ORACLE_INDETERMINATE, E_ORACLE_AMENDMENT_UNREASONED)
                   if c not in error_codes.ERROR_CODES]
        assert not missing, f"AC-11: §5 codes absent from ERROR_CODES: {missing}"
        for c in (E_ORACLE_MUTATED, E_ORACLE_UNFROZEN,
                  E_ORACLE_INDETERMINATE, E_ORACLE_AMENDMENT_UNREASONED):
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
# AC-S1 (DERIVED from §8 "Explicitly NOT in scope") — the scope fence
# ─────────────────────────────────────────────────────────────────────────

class TestScopeFence:
    """§8: "A diff touching any of them is out of contract and should be
    rejected without reading further: it means the freeze migrated back into
    the phase it is meant to bound."

    Declared pre-passing (§0.6): this asserts an absence that cannot yet be
    violated.  It is a fence, not a measurement — it dies the moment a GREEN
    reaches for the shape the `chokepoint` header was written to forbid."""

    def test_ac_s1a_emit_phase_artifacts_finally_knows_nothing_about_the_oracle(self):
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

    def test_ac_s1b_no_workflow_module_imports_the_oracle_seam(self):
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

    def test_ac_s1c_run_py_is_the_only_wiring_site(self):
        """`[bd8:6]`: EXACTLY one call site each, both in `run.py main()`.
        Nothing outside run.py and the conformance package may reach the
        freeze/verify seam."""
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


# ═════════════════════════════════════════════════════════════════════════
# SPEC GAPS
# ═════════════════════════════════════════════════════════════════════════
#
# Found while writing this RED against ORACLE_SPEC.md FROZEN v3.  Recorded,
# NOT guessed past.  G5 and G6 each block a leg of an AC; those legs are not
# written above.  The gate rules on all four.
#
# ── G3 — workflow-name SPELLING.  Cosmetic, resolved by measurement. ──────
#   `[bd8:7]` names the two workflows `phase-45-spec` and `phase-5-implement`
#   (hyphens), and §1 `[bd8:2a]` does the same.  The REGISTRY names are
#   `phase_45_spec` and `phase_5_implement` (underscores) —
#   workflows/__init__.py:34,36 — and `phase_artifacts.phase` carries the
#   registry name verbatim (engine.py:793 passes `workflow_name`).  The
#   hyphen form is not accepted by `run.py --workflow`.
#   TAKEN: this RED binds the UNDERSCORE registry names.  Stated, not
#   assumed; if the gate reads the hyphen form as normative, the two
#   constants at the top of this file are the only change.
#
# ── G4 — an implementing-phase invocation with NO `--event-log`. ──────────
#   `[bd8:8]` scopes the lookup to "the event log this invocation was given".
#   `run.py --event-log` is OPTIONAL (run.py:115) and many invocations omit
#   it.  The spec does not say what the verify does when no log was given.
#   Two readings, materially different:
#     (a) fail-closed — no log ⊆ "finds no freeze event", so
#         `E_ORACLE_UNFROZEN` per `[bd8:9]`.  Consistent with the lot's whole
#         posture, but it fails EVERY logless `phase_5_implement` invocation,
#         which is a blast radius no AC declares.
#     (b) untouched — with no log there is no freeze to bind to and nothing
#         to emit into, so the run passes as it does today.  Consistent with
#         `[bd8:7]`'s "no freeze, no verify, no event", but it hands any
#         caller a one-flag bypass of BD-L1.
#   NOT GUESSED.  No test above exercises the logless implementing phase, and
#   the RED is silent on it.  This is the one gap with ship-blocking blast
#   radius: (a) without a declared migration breaks siblings, (b) without a
#   declared compensating control makes the level claim in §9 weaker than it
#   reads.  The gate must pick one, and it needs an AC either way.
#
# ── G5 — AC-7 is not measurable as written.  BLOCKS AC-7 entirely. ────────
#   AC-7 requires "their `run_identity` events carry distinct invocation
#   identity".  MEASURED: the `run_identity` payload is exactly
#   `{engine_version, adapter_identity}` (engine.py:296-303) — both constant
#   across invocations on one tree, and neither is an invocation identity.
#   The only per-invocation discriminator on the event is the envelope's
#   `run_id`, which `[bd8:8a]` establishes is DELIBERATELY allowed to be
#   IDENTICAL across the two phases (HAL's run-phase.ts threads one
#   `HAL_BUILD_RUN_ID`; the dogfood driver threads one `--run-id`).  So the
#   AC as written is either unsatisfiable or trivially false depending on the
#   driver, and the spec's own `[bd8:8a]` is what makes it so.
#   AC-7's second half compounds it: "no transcript of the authoring phase is
#   present in the implementing phase's assembled prompt", to be read from
#   `model_invocation_attested` payloads.  Those are emitted by
#   `llm_subprocess` on an LLM dispatch; a hermetic two-phase run has zero
#   LLM steps and therefore zero such events, so the check would pass over an
#   empty set — the vacuous shape `[bd8:9]` exists to forbid.  Making it
#   non-vacuous needs a declared fixture: either a recorded two-phase log
#   committed as an asset, or an adversarial backend registered through
#   `llm_subprocess.register_backend` (the bd#10 seam) with the spec naming
#   which invocation must carry what.
#   NOT WRITTEN.  AC-7 has no test above.  It needs either a new
#   per-invocation field on `run_identity` (an engine.py change — OUT OF
#   SCOPE per §8, so it is a different lot) or a re-cut of the AC onto a
#   discriminator that exists.
#
# ── G6 — the amendment `reason` has no input channel.  BLOCKS one leg. ────
#   `[bd8:10]` requires `oracle_amended {..., reason, ...}` and makes an
#   absent/empty `reason` `E_ORACLE_AMENDMENT_UNREASONED`.  The spec never
#   says HOW a reason reaches the freeze.  Candidates all exist and are all
#   guesses: a new `run.py` flag (§8 permits run.py edits, but "run.py stays
#   thin" per `[bd8:6]` and a new flag is new surface), an `org_config` key
#   threaded through `--ctx-json`, or an environment variable.
#   TAKEN: the UNREASONED leg IS written (`test_ac8_reentry_without_a_reason_
#   is_unreasoned`) — it is decidable without knowing the channel, because
#   whatever the channel is, absent is absent.  The REASONED leg —
#   `oracle_amended` carrying `previous_digest` ≠ `digest`, and a subsequent
#   implementing phase verifying against the NEW digest and PASSING — is NOT
#   written.  That leg is the false-free control for the whole amendment
#   path: without it, a GREEN that fails EVERY re-entry as unreasoned
#   satisfies AC-8's written half.  It must be added once the gate names the
#   channel.
