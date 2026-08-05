"""bd#36 — `phase_artifacts.written`: the manifest as a SOURCE, not a filter.

Spec: `docs/decisions/2026-08-04-bd36-phase-artifacts-written-source.md`.

Class: the instrument answers a question it was never asked, and its refusal looks
like a success. Today `written` is filled EXACTLY from the git delta of `git_cwd`
(`engine.py:493`), while the signature `write_tracking: "git-delta"` reads as "I
observed the phase's writes". Measured: a step that genuinely wrote a file OUTSIDE `git_cwd` and
declared it in a manifest yields `written: []` with `write_tracking: "git-delta"`
— that is, "the phase wrote nothing" instead of "my writes were outside the window".
`EMISSIONS_SPEC.md [G18r3:EDGE-4]` names exactly this pair an "overclaim shape" and
forbids it; AC-E3b, however, ties the rule to the question "was the delta
computed" rather than "did the window cover the writes" — so the letter is observed and the meaning is not.

The UUT is NOT mocked: every leg runs a real `WorkflowEngine`, the step physically
writes a file, the manifest is delivered in the regular `StepResult.data` form
(`worker_written_paths` + `manifest_source`), which is parsed by
`llm_subprocess.manifest_from_result`.

WHY `harness_tool_record` SPECIFICALLY, and not a reference backend. Measured: all
`lib/reference_backends/*` build their manifest via `_manifest_since`, i.e.
`git diff --name-only` inside `root` — their manifest IS ITSELF the git delta, and
a union with it adds nothing. Paths outside the repository are carried only by the production
path (`llm_subprocess._written_paths_from_events`, `file_path` from the transcript of
Write/Edit, verbatim). A RED written on a reference backend would be green both before
the fix and after — that is, not a RED at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine

from test_bd18_emissions import make_log, make_ctx, events_of, git_repo  # noqa: F401

MANIFEST_SOURCE = "harness_tool_record"


def _step(name: str, writes: list[Path], declared: list[str] | None) -> StepContract:
    """A step that REALLY writes `writes` and declares `declared`.

    `declared is None` ⇒ a StepResult with no manifest (the DEFER branch, §2 D2).
    """
    def _run(_ctx, _prev):
        for p in writes:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("bd36\n")
        data = None
        if declared is not None:
            data = {
                "worker_written_paths": declared,
                "manifest_source": MANIFEST_SOURCE,
            }
        return StepResult(status="ok", data=data, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _phase_artifacts(tmp_path, label, git_cwd, writes, declared):
    """Run one phase and return the payload of its `phase_artifacts`."""
    log_dir = tmp_path / label
    log_dir.mkdir(parents=True, exist_ok=True)
    log = make_log(log_dir)
    eng = WorkflowEngine(event_log=log)
    eng.register(label, WorkflowDefinition(
        name=label, steps=[_step("s1", writes, declared)],
    ))
    eng.execute(label, make_ctx(git_cwd=git_cwd), run_id="r")
    pa = events_of(log, "phase_artifacts")
    assert len(pa) == 1, f"expected exactly one phase_artifacts, got {len(pa)}"
    return pa[0]["payload"]


# ─── AC1: the subject — a declared path outside the repo must land in written ─

def test_ac1_manifest_declared_path_outside_git_cwd_lands_in_written(tmp_path, git_repo):
    """§1l: the file is created physically, outside `git_cwd`, and declared in a manifest.

    Red today: `written == []`. This is exactly the spec's §0 measurement (case C).
    """
    outside = tmp_path / "scratchpad" / "run1" / "note.md"
    payload = _phase_artifacts(
        tmp_path, "ac1", str(git_repo), [outside], [str(outside)],
    )

    assert outside.exists(), "the fixture must genuinely create the file"
    # §1j macOS realpath: tmp_path arrives as /var/..., while resolve() gives
    # /private/var/... The comparison must be against the RESOLVED form — otherwise a correct
    # GREEN that normalises paths (D3) could not satisfy this AC.
    assert str(outside.resolve()) in payload["written"], (
        f"a manifest-declared path outside git_cwd did not land in written: "
        f"written={payload['written']!r}. The manifest must be a SOURCE, "
        f"not a filter over the git delta."
    )


# ─── AC2: the control leg — without a manifest, exactly the git delta (D2) ─

def test_ac2_without_manifest_written_is_exactly_the_git_delta(tmp_path, git_repo):
    """Catches a "fix" satisfied by having stopped intersecting altogether.

    Without this leg it would be enough to rip out the manifest logic, AC1 would go green,
    and the behaviour of steps without a manifest would silently drift.
    """
    payload = _phase_artifacts(
        tmp_path, "ac2", str(git_repo), [git_repo / "c.txt"], None,
    )
    assert payload["written"] == ["c.txt"], (
        f"a step without a manifest must give EXACTLY the git delta, got "
        f"{payload['written']!r}"
    )
    assert payload["write_tracking"] == "git-delta"


# ─── AC3: NEGATIVE LEG — not-observed is not softened by a manifest (D6) ──

def test_ac3_absent_git_cwd_stays_not_observed_even_with_manifest(tmp_path):
    """A gate that permits an observation here is inert.

    The delta was not computed at all (no `git_cwd`), so a non-empty manifest has NO
    right to turn `not-observed` into an observation — this is directly forbidden by
    the issue and by `[G18r3:EDGE-4]`. Without this leg "the manifest as a source" would quietly
    rewrite the meaning of the signature on every path where the engine never looked.
    """
    outside = tmp_path / "scratchpad" / "run2" / "x.md"
    payload = _phase_artifacts(tmp_path, "ac3", None, [outside], [str(outside)])

    assert payload["write_tracking"] == "not-observed", (
        f"without git_cwd the delta was never computed — the signature must stay "
        f"'not-observed', got {payload['write_tracking']!r}"
    )


# ─── AC4: the new value appears ON THE MERITS, both sides (D5) ────────────

def test_ac4_new_tracking_value_appears_only_when_manifest_added_something(tmp_path, git_repo):
    """Both sides: otherwise the value either never appears or appears always."""
    outside = tmp_path / "scratchpad" / "run3" / "y.md"
    added = _phase_artifacts(
        tmp_path, "ac4_added", str(git_repo), [outside], [str(outside)],
    )
    assert added["write_tracking"] == "git-delta+manifest", (
        f"the manifest added a path beyond the delta — the signature must name that, "
        f"got {added['write_tracking']!r}"
    )

    nothing_new = _phase_artifacts(
        tmp_path, "ac4_plain", str(git_repo), [git_repo / "d.txt"], ["d.txt"],
    )
    assert nothing_new["write_tracking"] == "git-delta", (
        f"the manifest added nothing beyond the delta — the signature must stay "
        f"'git-delta', got {nothing_new['write_tracking']!r}"
    )


# ─── AC5/AC6: normalisation — one alphabet (D3) ───────────────────────────

def test_ac5_absolute_manifest_path_inside_repo_collapses_with_relative_delta(tmp_path, git_repo):
    """One file, two alphabets: the git delta gives `e.txt`, the manifest an absolute path.

    Catches the gluing of two namespaces: without normalisation `written` would hold
    TWO entries for one file.
    """
    inside = git_repo / "e.txt"
    payload = _phase_artifacts(
        tmp_path, "ac5", str(git_repo), [inside], [str(inside)],
    )
    assert payload["written"] == ["e.txt"], (
        f"one file must yield one repo-relative entry, got "
        f"{payload['written']!r}"
    )


def test_ac6_outside_path_normalisation_is_idempotent(tmp_path, git_repo):
    """A path outside the repo, declared twice in different forms, yields one entry."""
    outside = tmp_path / "scratchpad" / "run4" / "z.md"
    messy = str(outside.parent / "." / outside.name)
    payload = _phase_artifacts(
        tmp_path, "ac6", str(git_repo), [outside], [str(outside), messy],
    )
    assert payload["written"] == [str(outside.resolve())], (
        f"two forms of one path must collapse into one, got "
        f"{payload['written']!r}"
    )


# ─── AC7: the accepted price of trusting the manifest (D4) ────────────────

def test_ac7_declared_but_nonexistent_path_is_trusted_and_included(tmp_path, git_repo):
    """D4 is pinned DELIBERATELY, so that a future existence check cannot pass in silence.

    The manifest is the harness's record of its own tool calls (`4961254A`), not
    the worker's self-report; an existence check would introduce a race with a later
    step that legitimately deleted the file. The price is that a path reported in error
    will land in `written`.
    """
    ghost = tmp_path / "scratchpad" / "run5" / "never-written.md"
    payload = _phase_artifacts(
        tmp_path, "ac7", str(git_repo), [], [str(ghost)],
    )
    assert not ghost.exists(), "the fixture must NOT create this file"
    # §1j: the same resolved form as in AC1.
    assert str(ghost.resolve()) in payload["written"], (
        f"D4: a path from the manifest is included without an existence check, "
        f"got {payload['written']!r}"
    )


# ─── AC8: §1a sibling — the signature's consumers are untouched ───────────

def test_ac8_bd8_oracle_pinned_pair_still_holds(tmp_path, git_repo):
    """`test_bd8_l1_oracle.py:19` pins the pair `git-delta` + `written: []` literally.

    Its case is a phase with a computed delta and NO manifest, i.e. branch D2.
    The same form is reproduced here directly: the step writes nothing, there is no
    manifest ⇒ the signature must stay `git-delta` and the set empty.
    """
    payload = _phase_artifacts(tmp_path, "ac8", str(git_repo), [], None)
    assert payload["written"] == []
    assert payload["write_tracking"] == "git-delta", (
        f"the pair the bd#8 oracle rests on has drifted: "
        f"{payload['write_tracking']!r}"
    )
