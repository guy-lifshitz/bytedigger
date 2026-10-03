"""RED-phase tests -- bd#141 item 5 (residue): the `reverted` signal of `companion-tune collect`.

Spec (FROZEN): docs/decisions/2026-10-02-bd141-p5-revert-signal.md (section 3, ACs V1-V11).
UUT: bytedigger_engine/companion_tune.py (`collect`, `propose`) through the real CLI `scripts/companion-tune`.
The written signals.json is the production side-effect (spec 1l).

AC -> tests
-----------
V1  positive: maintainer merges `Reverts o/r#10`              test_v1_*
V2  negatives (a)-(m), (l) per inexact line                   test_v2_negative_case[...]
V3  dead target (404) is not UNAVAILABLE                      test_v3_*
V4  `gh pr view` HTTP 500 => exit 4 UNAVAILABLE               test_v4_*
V5  target outside / inside the window listing                test_v5_target_outside_window_*, test_v5_target_inside_window_*
V6  two Reverts lines: first one wins                         test_v6_*
V7  dedupe against a genuine tuner PR's bd:tune line          test_v7_*
V8  no gh call for a locally failing revert PR                test_v8_*
V9  window `gh pr list --json` carries id and mergedBy        test_v9_*
V10 propose renders it (GUARD, passes at RED)                 test_v10_*
V11 docs / changelog / module docstring                       test_v11_docs[...]

Fixture reuse: the fake-gh rig of test_bd117b_companion_tune.py (imported, not copied). Fixture-only edits
there (no assertion change): REAL_FIELDS += mergedBy; switch `pr_view_500` = N makes `gh pr view N` fail with
stderr `HTTP 500`.

Negative-case design: a bare "no reverted signal" assertion would pass today for every negative. Every V2 / V3 /
V7 world therefore also carries a CONTROL revert PR (41, maintainer-merged, valid, reverting BD-built PR 11) and
asserts the reverted ids are exactly {reverted:PR_ctl}; that fails until `reverted` exists and keeps each
negative coupled to its own check. V8 asserts the listing already requests `mergedBy` (the revert path is
live) before asserting the absence of `pr view` / permission calls.

Section 1i: every contested state is pre-staged in the fake-gh state file; nothing races. Section 1q: the module
under test is never imported; only the CLI runs (as a subprocess).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import test_bd117b_companion_tune as t  # noqa: E402  (tests dir is on sys.path via conftest)

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_CONFIG = REPO_ROOT / "docs" / "configuration.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
COMPANION_TUNE_PY = REPO_ROOT / "engine_py" / "bytedigger_engine" / "companion_tune.py"

CTL_ID = "reverted:PR_ctl"


def revert_row(n=40, target=10, *, rid="PR_rev40", author="alice", merged_by="alice", state="MERGED",
               merged_at=None, updated=None, body=None):
    row = t.pr_row(n, author=author, body=body if body is not None else f"Reverts o/r#{target}",
                   state=state, updated=updated or t.ago(2), title=f'Revert "PR {target} title"')
    row["id"] = rid
    row["mergedAt"] = (merged_at or t.ago(2)) if state == "MERGED" else None
    row["mergedBy"] = {"login": merged_by} if (merged_by and state == "MERGED") else None
    return row


def control_row():
    return revert_row(41, 11, rid="PR_ctl", merged_at=t.ago(1), updated=t.ago(1))


def reverted(data):
    return [s for s in data["signals"] if s["kind"] == "reverted"]


def collect_ok(rig):
    proc, data = t.run_collect(rig)
    assert proc.returncode == 0, proc.stderr
    assert data is not None, "collect --out must write signals.json"
    return data


# =========================================================================== V1


def test_v1_maintainer_merged_revert_pr_is_one_reverted_signal(tmp_path, monkeypatch):
    """V1: BD-built merged PR 10; alice merges PR 40 `Reverts o/r#10` => exactly one `reverted` signal."""
    rig = t.make_rig(tmp_path, monkeypatch)
    merged_at = t.ago(2)
    rig.set_world(prs=[t.pr_row(10), revert_row(merged_at=merged_at)])
    data = collect_ok(rig)
    assert data["signals"] == [{
        "id": "reverted:PR_rev40", "kind": "reverted", "pr": 10, "issue": None, "label": None,
        "action": None, "actor": "alice", "title": "PR 10 title", "at": merged_at,
    }]


# =========================================================================== V2

BD = t.BD_USER


def _v2_target(**kw):
    return t.pr_row(10, **kw)


V2_CASES = {
    "a_open": dict(revert=dict(state="OPEN")),
    "b_closed_unmerged": dict(revert=dict(state="CLOSED")),
    "c_merged_30d_ago": dict(revert=dict(merged_at=t.ago(30), updated=t.ago(1))),
    "d_merged_by_bot": dict(revert=dict(merged_by="ci-bot")),
    "e_merged_by_no_write": dict(revert=dict(merged_by="eve")),
    "f_merged_by_null": dict(revert=dict(merged_by=None)),
    "g_revert_author_is_bd": dict(revert=dict(author=BD)),
    "h_target_human_forged_marker": dict(target=dict(author="mallory")),
    "i_target_bd_without_marker": dict(target=dict(body="Built via ByteDigger.")),
    "j_target_closed_unmerged": dict(target=dict(state="CLOSED")),
    "k_other_repo": dict(revert=dict(body="Reverts x/y#10")),
    "l1_trailing_text": dict(revert=dict(body="Reverts o/r#10 please")),
    "l2_prefixed_text": dict(revert=dict(body="see Reverts o/r#10")),
    "l3_revert_singular": dict(revert=dict(body="Revert o/r#10")),
    "l4_no_slug": dict(revert=dict(body="Reverts #10")),
    "m_revert_id_null": dict(revert=dict(rid=None)),
}


@pytest.mark.parametrize("case", sorted(V2_CASES))
def test_v2_negative_case(tmp_path, monkeypatch, case):
    """V2 (a)-(m): the variant revert PR yields no `reverted` signal, exit 0; the control revert PR in the
    same run still does (so the assertion cannot pass without the `reverted` kind existing)."""
    spec = V2_CASES[case]
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[_v2_target(**spec.get("target", {})), t.pr_row(11),
                       revert_row(**spec.get("revert", {})), control_row()])
    data = collect_ok(rig)
    assert [s["id"] for s in reverted(data)] == [CTL_ID], f"{case}: {data['signals']!r}"


# =========================================================================== V3


def test_v3_dead_target_is_not_a_signal_nor_unavailable(tmp_path, monkeypatch):
    """V3: `Reverts o/r#999` (no PR 999) => exit 0, no signal for it, other signals still reported."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(
        prs=[t.pr_row(11), t.pr_row(12, closing=[5]), revert_row(body="Reverts o/r#999"), control_row()],
        issues=[t.issue_row(5, closed_by=[12],
                            timeline=[t.ev("ReopenedEvent", "RE_1", "alice", t.ago(2))])],
    )
    data = collect_ok(rig)
    ids = {s["id"] for s in data["signals"]}
    assert ids == {CTL_ID, "reopened:RE_1"}, ids


# =========================================================================== V4


def test_v4_target_view_http_500_is_unavailable(tmp_path, monkeypatch):
    """V4: `gh pr view 10` fails with HTTP 500 => exit 4, one `E_COMPANION_TUNE_UNAVAILABLE` stderr line."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[t.pr_row(10), revert_row()])
    rig.switch(pr_view_500=10)
    proc, _ = t.run_collect(rig)
    t.assert_fail(proc, 4, "E_COMPANION_TUNE_UNAVAILABLE")


# =========================================================================== V5


def _views(rig, number):
    return [c for c in rig.gh_calls() if c["argv"][:3] == ["pr", "view", str(number)]]


def test_v5_target_outside_window_listing_costs_one_pr_view(tmp_path, monkeypatch):
    """V5 (outside): target not in the window listing => exactly one `gh pr view 10` and the V1 signal."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[t.pr_row(10), revert_row()])
    data = collect_ok(rig)
    assert [s["id"] for s in reverted(data)] == ["reverted:PR_rev40"]
    assert len(_views(rig, 10)) == 1, rig.gh_calls()


def test_v5_target_inside_window_listing_costs_no_pr_view(tmp_path, monkeypatch):
    """V5 (inside): target in the window listing => the signal is reported with zero `gh pr view 10`."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[t.pr_row(10, updated=t.ago(1)), revert_row()])
    data = collect_ok(rig)
    assert [s["id"] for s in reverted(data)] == ["reverted:PR_rev40"]
    assert _views(rig, 10) == [], "the listing already carries the target"


# =========================================================================== V6


def test_v6_first_reverts_line_wins(tmp_path, monkeypatch):
    """V6: body with `Reverts o/r#10` then `Reverts o/r#11` (both BD-built merged) => one signal, pr 10."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[t.pr_row(10), t.pr_row(11),
                       revert_row(body="Revert text\nReverts o/r#10\nReverts o/r#11")])
    data = collect_ok(rig)
    got = reverted(data)
    assert len(got) == 1 and got[0]["pr"] == 10, got


# =========================================================================== V7


def test_v7_signal_named_in_a_genuine_tuner_pr_is_not_reported_again(tmp_path, monkeypatch):
    """V7: `reverted:PR_rev40` is in a genuine tuner PR's bd:tune line => not reported; the control still is."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[t.pr_row(10), t.pr_row(11), revert_row(), control_row(),
                       t.tuner_pr(50, ids=("reverted:PR_rev40",))])
    data = collect_ok(rig)
    assert [s["id"] for s in reverted(data)] == [CTL_ID]


# =========================================================================== V8


def test_v8_locally_failing_revert_prs_cost_no_gh_call(tmp_path, monkeypatch):
    """V8: revert PRs failing a local check (open, closed, stale, bot/null actor, BD author, other repo,
    null id) and no BD-built PR => no `pr view` and no `.../permission` call. The precondition (the window
    listing already requests `mergedBy`) proves the revert code path is live, so this fails at RED too."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[
        revert_row(40, 10, rid="PR_a", state="OPEN"),
        revert_row(42, 10, rid="PR_b", state="CLOSED"),
        revert_row(43, 10, rid="PR_c", merged_at=t.ago(30), updated=t.ago(1)),
        revert_row(44, 10, rid="PR_d", merged_by="ci-bot"),
        revert_row(45, 10, rid="PR_f", merged_by=None),
        revert_row(46, 10, rid="PR_g", author=t.BD_USER),
        revert_row(47, 10, rid="PR_k", body="Reverts x/y#10"),
        revert_row(48, 10, rid=None),
    ])
    data = collect_ok(rig)
    assert reverted(data) == []
    lists = [c for c in rig.gh_calls() if c["argv"][:2] == ["pr", "list"]
             and any(a.startswith("updated:>=") for a in c["argv"])]
    assert lists and "mergedBy" in lists[0]["argv"][lists[0]["argv"].index("--json") + 1].split(","), \
        "the revert path must be live (listing requests mergedBy)"
    assert [c for c in rig.gh_calls() if c["argv"][:2] == ["pr", "view"]] == []
    assert [c for c in rig.gh_calls() if any(a.endswith("/permission") for a in c["argv"])] == []


# =========================================================================== V9


def test_v9_window_pr_listing_requests_id_and_merged_by(tmp_path, monkeypatch):
    """V9: the window `gh pr list --json` fields include `id` and `mergedBy`."""
    rig = t.make_rig(tmp_path, monkeypatch)
    rig.set_world(prs=[t.pr_row(10), revert_row()])
    collect_ok(rig)
    lists = [c["argv"] for c in rig.gh_calls() if c["argv"][:2] == ["pr", "list"]
             and any(a.startswith("updated:>=") for a in c["argv"])]
    assert len(lists) == 1, lists
    fields = set(lists[0][lists[0].index("--json") + 1].split(","))
    assert {"id", "mergedBy"} <= fields, fields


# =========================================================================== V10


def test_v10_propose_renders_a_reverted_signal(tmp_path, monkeypatch):
    """V10 (GUARD, passes at RED): a `reverted` signal reaches the prompt and the PR body generically.
    Reddens if GREEN makes `_prompt` / `_pr_body` special-case kinds and drop or mangle unknown ones
    (e.g. a `kind in {reopened, relabeled}` filter in `_prompt`, or a link built from `issue` unguarded)."""
    at = t.ago(2)
    s = t.sig("reverted", "PR_rev40", pr=10, issue=None, label=None, actor="alice", title="PR 10 title", at=at)
    rig = t.make_rig(tmp_path, monkeypatch)
    proc, _ = t._run_valid(rig, [s])
    assert proc.returncode == 0, proc.stderr
    line = next((ln for ln in rig.prompt().splitlines() if "reverted:PR_rev40" in ln), None)
    assert line is not None, "the signal line is missing from the prompt"
    assert all(x in line for x in ("reverted", "#10", "PR 10 title")), line
    body = rig.pr_creates()[0]["argv"]
    body = body[body.index("--body") + 1].rstrip("\n").split("\n")
    assert f"- reverted https://github.com/o/r/pull/10 alice {at}" in body, body


# =========================================================================== V11


def _unreleased() -> str:
    from helpers.changelog import require_entry

    text = CHANGELOG.read_text(encoding="utf-8")
    return require_entry(text, "bd#141 item 5").body


@pytest.mark.parametrize("where", ["configuration_md", "changelog_unreleased", "module_docstring"])
def test_v11_docs_name_the_reverted_signal(where):
    """V11: configuration.md names `reverted` and `Reverts <owner>/<repo>#<N>`; CHANGELOG [Unreleased]
    mentions the `reverted` signal; the module docstring mentions `reverted`."""
    if where == "configuration_md":
        text = DOC_CONFIG.read_text(encoding="utf-8")
        assert "reverted" in text and "Reverts <owner>/<repo>#<N>" in text
    elif where == "changelog_unreleased":
        body = _unreleased()
        assert "`reverted`" in body and "Reverts <owner>/<repo>#<N>" in body
    else:
        import ast

        doc = ast.get_docstring(ast.parse(COMPANION_TUNE_PY.read_text(encoding="utf-8"))) or ""
        assert "reverted" in doc
