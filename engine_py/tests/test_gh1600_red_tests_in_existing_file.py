"""RED tests for hal#1600 D1 — refuse RED tests written into a
pre-existing test file, at `_commit_red_tests` time (before the RED commit
lands).

Spec (v4, post third Opus-gate revision): SHARED/memory/Decisions/
2026-08-08_1600_d1_red_tests_in_existing_file_spec.md — ACs 1, 1b, 2-6, 7,
7b, 8a, 8b, 8c.

Conventions (§1q / 81F97F3D no module-level sys.path mutation): imports at
module top level only for symbols that already exist today
(`phase_5_implement`, `lib.authored_boundary`, `lib.git_port`,
`error_codes`, `contracts`) — conftest.py's import-time singleton already
exposes engine_py/ (package parent) and tests/ on sys.path. The new behaviour under
test (the refusal check, `E_RED_TESTS_IN_EXISTING_FILE`) does not exist yet,
so every test FAILS at assert time against real production code, never at
collection time — EXCEPT AC7 and AC8a, which are regression pins that
already pass today (see their docstrings).

M4 (ordering): the check now lands AFTER the GH282 mass-deletion gate
(`_red_mass_deletion_violations`, invoked at :2018), still before the commit
and before `_persist_pre_red_ref` (:2106).

M5 (second exemption redefined): `G` is bound to the AUTHORIZATION EVENT —
the `_mdl_exempted` partition GH282 itself produces (:2028-2034) — NOT to
the `# red-mass-deletion: allow` TOKEN. A path RED merely wrote the token
into, with no real over-threshold deletion for GH282 to have exempted, is
NOT in G (AC8c). Only a path GH282 actually placed in `_mdl_exempted` is
exempt (AC8a). `G = ∅` when the `HAL_RED_MASS_DELETION_GATE` kill-switch is
OFF.

Real git repos in tmp_path throughout (§1l) — the UUT (`_commit_red_tests`)
is never mocked; only the git READ seam is optionally wrapped (AC6) to
*observe* calls while still delegating to the real subprocess.

M1 (probe target is the FROZEN pre-RED SHA, not HEAD): every fixture below
writes `scratchpad/integrity/pre-red-ref.txt` EXPLICITLY, so the frozen
boundary F is never an accidental default — even on cycle 1, where F
happens to equal HEAD, the equality is asserted rather than assumed.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from bytedigger_engine.workflows import phase_5_implement as mod
from bytedigger_engine.lib import authored_boundary as authored_boundary
from bytedigger_engine import error_codes
from bytedigger_engine.lib.git_port import _git_read_subprocess, set_default_git_read_factory, reset_default_git_read_factory
from bytedigger_engine.contracts import StepResult, WorkflowContext

PRE_RED_REF_RELPATH = "integrity/pre-red-ref.txt"

# ─── shared git-repo helpers (pattern copied from sibling GH637/GH639 tests) ──


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


def _write_file(repo: Path, relpath: str, body: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return r.stdout.strip()


def _write_frozen_ref(scratchpad: Path, sha: str) -> None:
    ref_path = scratchpad / PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(sha)


def _make_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Write RED tests", session_id="gh1600-red", persona="hal",
        framework=None, domain=None,
    )


def _make_prev(cycle: int = 1, red_test_paths=None, spec_path=None) -> StepResult:
    return StepResult(
        status="ok",
        data={"cycle": cycle, "spec_path": spec_path, "red_test_paths": red_test_paths},
        duration_ms=0,
        step_name="write_red_artifact",
    )


def _repo_with_existing_tracked_test_edited_uncommitted(tmp_path: Path, subdir: str = "a"):
    """Build a repo where `tests/test_existing.py` is ALREADY in the tree at
    the FROZEN pre-RED SHA (committed at init, ref explicitly written) and
    then modified UNCOMMITTED — RED wrote its new tests into an already-
    tracked file. `_derive_red_paths_via_git_diff` (git diff vs the frozen
    SHA, untracked=True) surfaces it as a changed test path.

    Returns (repo, scratchpad, frozen_sha). frozen_sha == HEAD here (cycle 1),
    but the ref file is written EXPLICITLY so the distinction from HEAD is
    asserted, not accidental (gate M1).
    """
    repo = (tmp_path / f"{subdir}_repo").resolve()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    frozen_sha = _commit_file(
        repo, "tests/test_existing.py", "def test_a():\n    assert True\n", "add existing test"
    )
    # RED writes a new test INTO the existing file, uncommitted.
    _write_file(
        repo, "tests/test_existing.py",
        "def test_a():\n    assert True\n\n\ndef test_new_red():\n    assert False\n",
    )
    scratchpad = tmp_path / f"{subdir}_scratch"
    scratchpad.mkdir()
    _write_frozen_ref(scratchpad, frozen_sha)
    return repo, scratchpad, frozen_sha


def _repo_with_brand_new_test_file(tmp_path: Path, subdir: str = "new"):
    """Build a repo where the RED test path does NOT exist in the frozen
    pre-RED tree at all."""
    repo = (tmp_path / f"{subdir}_repo").resolve()
    _init_repo(repo)
    frozen_sha = _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    _write_file(repo, "tests/test_new.py", "def test_x():\n    assert True\n")
    scratchpad = tmp_path / f"{subdir}_scratch"
    scratchpad.mkdir()
    _write_frozen_ref(scratchpad, frozen_sha)
    return repo, scratchpad


def _spec_with_authorized_edits(scratchpad: Path, block_body: str) -> Path:
    spec_path = scratchpad / "spec.md"
    spec_path.write_text(
        "## Files\n\n- MODIFY `src/placeholder.py`\n\n"
        f"authorized-test-edits:\n{block_body}"
    )
    return spec_path


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — refusal against the FROZEN SHA + no RED commit + no pre-red-ref left
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_refuses_red_test_present_in_frozen_tree_no_commit_no_ref_persisted(tmp_path: Path) -> None:
    repo, scratchpad, frozen_sha = _repo_with_existing_tracked_test_edited_uncommitted(tmp_path)

    head_before = _head_sha(repo)
    assert head_before == frozen_sha, "sanity: HEAD before the call must equal the frozen pre-RED SHA"

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1)

    result = mod._commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"expected refusal (status='error'), got {result.status!r}: "
        f"error_code={getattr(result, 'error_code', None)!r} data={result.data!r}"
    )
    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"expected error_code='E_RED_TESTS_IN_EXISTING_FILE' for a RED path "
        f"already present in the tree at the frozen SHA and not authorized, got "
        f"{getattr(result, 'error_code', None)!r} (error={getattr(result, 'error', None)!r})"
    )
    assert result.recoverable is False, (
        f"E_RED_TESTS_IN_EXISTING_FILE must be non-recoverable, got recoverable={result.recoverable!r}"
    )
    assert result.data is None, f"error result must carry data=None, got {result.data!r}"

    head_after = _head_sha(repo)
    assert head_after == head_before, (
        "GH1600 D1: no RED commit may be created once an offending pre-existing "
        f"test path is detected — HEAD moved from {head_before!r} to {head_after!r}."
    )
    # The refusal lands BEFORE _persist_pre_red_ref (:2106) — a fresh
    # scratchpad (no pre-existing ref for THIS run) must stay pre-red-ref-free.
    # We wrote the ref ourselves above to seed the frozen boundary for
    # derivation; assert the FILE CONTENT is unchanged (no re-stamp / no
    # additional persist side-effect happened as part of the refusal path).
    ref_path = scratchpad / PRE_RED_REF_RELPATH
    assert ref_path.read_text().strip() == frozen_sha, (
        "the refusal path must not touch the pre-red-ref file at all — expected "
        f"it to remain exactly the fixture-seeded {frozen_sha!r}, got "
        f"{ref_path.read_text().strip()!r}"
    )


def test_ac1_fresh_scratchpad_refusal_leaves_no_pre_red_ref_file(tmp_path: Path) -> None:
    """AC1 (no-persist side-effect, real-world shape): a FRESH scratchpad
    with no ref file at all (cycle-1, ref not pre-seeded) — the frozen SHA
    resolver falls back to HEAD, refusal fires, and _persist_pre_red_ref
    must never have run, so no ref file is created."""
    repo = (tmp_path / "b_repo").resolve()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    _commit_file(repo, "tests/test_existing.py", "def test_a():\n    assert True\n", "add existing test")
    _write_file(
        repo, "tests/test_existing.py",
        "def test_a():\n    assert True\n\n\ndef test_new_red():\n    assert False\n",
    )
    scratchpad = tmp_path / "b_scratch"
    scratchpad.mkdir()  # no ref file written — fresh cycle-1 scratchpad

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1)

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"pre-condition not met: got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    ref_path = scratchpad / PRE_RED_REF_RELPATH
    assert not ref_path.exists(), (
        "the refusal must land BEFORE _persist_pre_red_ref (:2106) — "
        f"a fresh scratchpad must have NO {PRE_RED_REF_RELPATH} after a refusal, "
        f"but it exists."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC1b (gate M2) — refusal is not defeatable by "a spec_path merely exists"
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1b_refuses_even_with_spec_path_set_listing_a_different_path(tmp_path: Path) -> None:
    repo, scratchpad, _frozen_sha = _repo_with_existing_tracked_test_edited_uncommitted(tmp_path, "c1b_diff")
    spec_path = _spec_with_authorized_edits(
        scratchpad, "- tests/test_some_other_file.py — unrelated migration\n"
    )

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1, spec_path=str(spec_path))

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"a spec_path whose authorized-test-edits: lists a DIFFERENT path must "
        f"NOT exempt the offending path — got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}. A GREEN of "
        f"'if spec_path: skip' would wrongly pass here."
    )
    assert result.recoverable is False


def test_ac1b_refuses_when_authorized_test_edits_block_is_literally_none(tmp_path: Path) -> None:
    repo, scratchpad, _frozen_sha = _repo_with_existing_tracked_test_edited_uncommitted(tmp_path, "c1b_none")
    spec_path = _spec_with_authorized_edits(scratchpad, "none\n")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1, spec_path=str(spec_path))

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"a literal 'none' authorized-test-edits: block must parse to zero "
        f"exemptions and still refuse — got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    assert result.recoverable is False


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — offending path's BASENAME + remedy phrase within error[:120]
# ═══════════════════════════════════════════════════════════════════════════


def test_ac2_error_message_basename_and_remedy_within_first_120_chars(tmp_path: Path) -> None:
    repo, scratchpad, _frozen_sha = _repo_with_existing_tracked_test_edited_uncommitted(tmp_path, "ac2")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1)

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"pre-condition for this AC not met: got error_code="
        f"{getattr(result, 'error_code', None)!r}"
    )
    error_msg = result.error or ""
    full_path = "tests/test_existing.py"
    basename = "test_existing.py"

    assert full_path in error_msg, (
        f"the full repo-relative offending path must appear somewhere in the "
        f"message, got: {error_msg!r}"
    )

    head = error_msg[:120]
    assert basename in head, (
        f"the offending path's BASENAME must be within the first 120 chars "
        f"(a long directory prefix must not defeat this), got head={head!r} "
        f"(full message={error_msg!r})"
    )

    # Condition C4: derive the expectation INDEPENDENTLY of the message text
    # (from the fixture's own offending path), so this cannot be satisfied by
    # any coincidental tests/*.py-shaped substring elsewhere in the message —
    # it must specifically be built from the offending file's own slug.
    offending_slug = "existing"  # from "tests/test_existing.py", independent of error_msg

    suggested = [
        s for s in re.findall(r"tests/test_[\w./-]+\.py", error_msg) if s != full_path
    ]
    assert suggested, (
        f"error message must contain a suggested NEW test file path distinct "
        f"from the offending path, got no such match in: {error_msg!r}"
    )
    assert any(offending_slug in s for s in suggested), (
        f"the suggested new path must be DERIVED from the offending file's own "
        f"slug ({offending_slug!r}, computed independently of the message), not "
        f"an arbitrary distinct tests/*.py string; candidates={suggested!r} "
        f"(message={error_msg!r})"
    )

    remedy_tokens = ("new test file", "new file")
    assert any(tok in head.lower() for tok in remedy_tokens), (
        f"the required-new-file remedy phrase must be within the first 120 "
        f"chars, got head={head!r}"
    )
    assert "authorized-test-edits" in error_msg, (
        f"the message must also name 'authorized-test-edits:' as the supported "
        f"override, got: {error_msg!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — all paths absent from frozen tree: byte-identical baseline
# ═══════════════════════════════════════════════════════════════════════════


def test_ac3_all_new_paths_absent_from_frozen_tree_baseline_unchanged(tmp_path: Path) -> None:
    repo, scratchpad = _repo_with_brand_new_test_file(tmp_path)

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1)

    result = mod._commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"expected ok for a RED path absent from the frozen tree, got "
        f"{result.status!r}: {getattr(result, 'error', None)!r} "
        f"(error_code={getattr(result, 'error_code', None)!r})"
    )
    assert result.data is not None
    for key in ("red_commit_sha", "red_test_paths"):  # bytedigger: no RED-skeleton commit, so no red_skeleton_sha key
        assert key in result.data, f"expected {key!r} in result.data, got keys={list(result.data)!r}"

    manifest_path = scratchpad / mod.RED_TEST_HASHES_RELPATH
    assert manifest_path.is_file(), f"expected frozen hash manifest at {manifest_path}"
    manifest = json.loads(manifest_path.read_text())
    assert "tests/test_new.py" in manifest, (
        f"expected a digest for tests/test_new.py in the frozen manifest, "
        f"got keys={list(manifest.keys())!r}"
    )
    computed = authored_boundary.compute_red_test_hashes(["tests/test_new.py"], str(repo))
    assert manifest["tests/test_new.py"] == computed["tests/test_new.py"], (
        "frozen manifest digest must equal a real sha256 of the committed content, "
        f"got {manifest['tests/test_new.py']!r} vs recomputed {computed['tests/test_new.py']!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — differential twin of AC1b: SAME spec_path, only the listing differs
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4_authorized_listing_is_differential_twin_exempting_a_live_refusal(tmp_path: Path) -> None:
    repo, scratchpad, _frozen_sha = _repo_with_existing_tracked_test_edited_uncommitted(tmp_path, "ac4")
    ctx = _make_ctx(scratchpad, str(repo))

    # Variant A (unauthorized): SAME spec_path file, lists a DIFFERENT path.
    # Proves this is a refusal from a LIVE gate, not from an absent one.
    spec_path = _spec_with_authorized_edits(
        scratchpad, "- tests/test_some_other_file.py — unrelated migration\n"
    )
    prev_unauthorized = _make_prev(cycle=1, spec_path=str(spec_path))
    result_unauthorized = mod._commit_red_tests(ctx, prev_unauthorized)
    assert result_unauthorized.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"pre-condition: the SAME fixture without the path listed must refuse "
        f"live, got status={result_unauthorized.status!r} "
        f"error_code={getattr(result_unauthorized, 'error_code', None)!r}"
    )
    assert _head_sha(repo) == _frozen_sha, "no commit must have landed from the refused variant"

    # Variant B (authorized): SAME spec_path path, ONLY the listing changes —
    # now names the offending path.
    spec_path.write_text(
        "## Files\n\n- MODIFY `src/placeholder.py`\n\n"
        "authorized-test-edits:\n"
        "- tests/test_existing.py — contract migration, intentional\n"
    )
    prev_authorized = _make_prev(cycle=1, spec_path=str(spec_path))
    result = mod._commit_red_tests(ctx, prev_authorized)

    assert result.error_code != "E_RED_TESTS_IN_EXISTING_FILE", (
        f"listing tests/test_existing.py in authorized-test-edits: must exempt "
        f"it from the SAME refusal proven live above, got error_code="
        f"{getattr(result, 'error_code', None)!r} error={getattr(result, 'error', None)!r}"
    )
    assert result.status == "ok", (
        f"expected ok (proceeds exactly as AC3) for an authorized existing-file "
        f"edit, got {result.status!r}: {getattr(result, 'error', None)!r}"
    )
    assert result.data is not None
    for key in ("red_commit_sha", "red_test_paths"):  # bytedigger: no RED-skeleton commit, so no red_skeleton_sha key
        assert key in result.data, f"expected {key!r} in result.data, got keys={list(result.data)!r}"

    manifest_path = scratchpad / mod.RED_TEST_HASHES_RELPATH
    assert manifest_path.is_file(), f"expected frozen hash manifest at {manifest_path}"
    manifest = json.loads(manifest_path.read_text())
    assert "tests/test_existing.py" in manifest, (
        f"expected a digest for the authorized existing path, got keys="
        f"{list(manifest.keys())!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — error code registered in error_codes.py AND mirrored in ERROR_CODES.md
# ═══════════════════════════════════════════════════════════════════════════


def test_ac5_error_code_registered_in_error_codes_module_and_markdown(tmp_path: Path) -> None:
    assert "E_RED_TESTS_IN_EXISTING_FILE" in error_codes.ERROR_CODES, (
        "E_RED_TESTS_IN_EXISTING_FILE must be registered in error_codes.ERROR_CODES; "
        f"got keys sample={sorted(k for k in error_codes.ERROR_CODES if k.startswith('E_RED'))!r}"
    )
    desc = error_codes.ERROR_CODES.get("E_RED_TESTS_IN_EXISTING_FILE", "")
    assert isinstance(desc, str) and desc.strip(), (
        f"E_RED_TESTS_IN_EXISTING_FILE must carry a non-empty one-line description, got {desc!r}"
    )
    # D2 has since done that job: the tamper leg is now
    # `E_RED_BASELINE_FILE_MODIFIED`. The invariant D1 wanted is unchanged —
    # the tamper code must still EXIST and be registered — only its name moved.
    # Absence of the retired name is asserted once, corpus-wide, by D2's AC5;
    # duplicating it here would re-introduce the very literal that AC5 bans.
    assert "E_RED_BASELINE_FILE_MODIFIED" in error_codes.ERROR_CODES, (
        "the tamper leg must stay registered under its post-D2 name "
        "E_RED_BASELINE_FILE_MODIFIED"
    )

    md_path = Path(__file__).resolve().parents[1] / "ERROR_CODES.md"
    md_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    assert "E_RED_TESTS_IN_EXISTING_FILE" in md_text, (
        f"E_RED_TESTS_IN_EXISTING_FILE must be mirrored in {md_path} — not found "
        f"(file exists={md_path.is_file()!r})"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC6 (condition C1) — probe verb is FIXED to `cat-file -e <frozen_sha>:<path>`
# ═══════════════════════════════════════════════════════════════════════════


def _is_cat_file_existence_probe(argv: list[str]) -> bool:
    """Condition C1: the probe verb is FIXED to `cat-file -e`, not `show` and
    not "any of ls-tree/cat-file/show". `_red_mass_deletion_violations`
    issues a `show <base_sha>:<path>` call (:1794-1796) that names the frozen
    SHA and the offending path — a perfect false positive for a permissive
    ls-tree/cat-file/show assertion. `show` (and `diff`/`status`) are
    explicitly excluded here."""
    if not argv:
        return False
    if argv[0] in ("diff", "status", "show"):
        return False
    return "cat-file" in argv and "-e" in argv


def test_ac6_existence_probe_is_cat_file_e_against_frozen_sha(tmp_path: Path) -> None:
    """AC6 (C1): the probe must be exactly `cat-file -e <frozen_sha>:<path>`
    with the FROZEN SHA as the base ref — NOT `show`, NOT `diff`/`--numstat`.

    This deliberately excludes BOTH `_red_mass_deletion_violations` seam
    calls (:1778 `diff --numstat`, gate default-ON at :2018; AND :1794-1796
    `show <base_sha>:<path>`, unreached only because this fixture's edit is a
    pure append with deleted==0 — a fixture accident, not a guarantee this
    test relies on). A permissive "any tree-lookup verb" assertion would be
    satisfied by the pre-existing `show` call with NO new probe added.
    """
    repo, scratchpad, frozen_sha = _repo_with_existing_tracked_test_edited_uncommitted(tmp_path, "ac6")

    calls: list[list[str]] = []

    def _spy(args, *, cwd=None, timeout=None, dir_=None):
        calls.append(list(args))
        return _git_read_subprocess(args, cwd=cwd, timeout=timeout, dir_=dir_)

    set_default_git_read_factory(lambda: _spy)
    try:
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(cycle=1)
        result = mod._commit_red_tests(ctx, prev)
    finally:
        reset_default_git_read_factory()

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"pre-condition for this AC not met: got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )

    object_ref = f"{frozen_sha}:tests/test_existing.py"
    cat_file_calls = [
        c for c in calls
        if _is_cat_file_existence_probe(c) and any(object_ref in tok for tok in c)
    ]
    assert cat_file_calls, (
        f"the existence probe must issue `cat-file -e {object_ref}` (verb "
        f"FIXED to cat-file, not show/ls-tree, not diff/status); observed seam "
        f"argvs={calls!r}"
    )
    # `_is_cat_file_existence_probe` already excludes verb=="show"/"diff"/
    # "status" by construction, so `cat_file_calls` above being non-empty
    # already proves a DISTINCT cat-file call exists — no separate assertion
    # needed here (a prior draft's :528-534 restated :514 and was dropped).


# ═══════════════════════════════════════════════════════════════════════════
# AC7 (gate M1) — cycle-≥2 re-entry: path in HEAD but absent from frozen tree
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_reentry_path_committed_in_head_but_absent_from_frozen_tree_no_refusal(
    tmp_path: Path,
) -> None:
    """AC7: models tests/test_phase_5_9EDB7588_commit_idempotency.py's
    `_seed_reentry_fixture` (:96-132) and `test_reentry_unchanged_paths_returns_ok`
    (:138-166) — a cycle-2 re-entry where `tests/test_foo.py` is already
    committed at HEAD (sha_1) but is ABSENT from the frozen pre-RED tree
    (sha_0, the boundary BEFORE that commit). Must be status='ok',
    error_code is None — no refusal.

    NOTE (§ report): this is a PIN, not a forcing RED. The 9EDB7588
    idempotent-skip guard already returns ok/error_code=None for this exact
    shape today (no staged diff on re-entry -> skip commit -> ok), and a
    correct D1 GREEN must not disturb it because the offending path is
    absent from the FROZEN tree (F=sha_0), not present in it. Kept as an
    explicit regression pin per gate M1 rather than silently assumed.
    """
    repo = (tmp_path / "reentry_repo")
    scratchpad = tmp_path / "reentry_scratch"
    _init_repo(repo)

    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    sha_0 = _head_sha(repo)
    _write_frozen_ref(scratchpad, sha_0)

    test_file = repo / "tests" / "test_foo.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_stub(): assert False\n")
    subprocess.run(["git", "add", "tests/test_foo.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "build: red cycle 1 tests [main]"], cwd=repo, check=True)
    sha_1 = _head_sha(repo)
    assert sha_1 != sha_0, "sanity: the RED test commit must have advanced HEAD past the frozen SHA"

    # Dirty prod file so `git status --porcelain` is non-empty (reproduces
    # the re-entry code path per the 9EDB7588 fixture).
    (repo / "prod_mod.py").write_text("# GREEN prod change\n")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=2, red_test_paths=["tests/test_foo.py"])

    result = mod._commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"cycle-2 re-entry where the RED path is committed at HEAD ({sha_1!r}) "
        f"but ABSENT from the frozen tree ({sha_0!r}) must NOT be refused — "
        f"got {result.status!r} (error={getattr(result, 'error', None)!r}, "
        f"error_code={getattr(result, 'error_code', None)!r})"
    )
    assert result.error_code is None, (
        f"expected error_code=None on re-entry, got {result.error_code!r} — "
        "a HEAD-based probe would wrongly refuse every cycle-2+ re-entry."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC7b (condition C2) — cycle-≥2 where the path IS present in frozen tree
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7b_reentry_cycle2_path_present_in_frozen_tree_still_refuses(tmp_path: Path) -> None:
    """AC7b (C2): kills the `cycle == 1` cheat. Without this, a GREEN that
    gates the whole check on `cycle == 1` passes every other AC in this file
    (all refusal ACs use cycle=1, and AC7 is the only cycle-2 case and
    expects NO refusal) — this is the one cycle-2 case that DOES expect a
    refusal, because the offending path is present in the FROZEN tree here
    (unlike AC7, where it is absent from the frozen tree)."""
    repo, scratchpad, frozen_sha = _repo_with_existing_tracked_test_edited_uncommitted(tmp_path, "ac7b")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=2)

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"cycle-2 with the offending path PRESENT in the frozen tree "
        f"({frozen_sha!r}) must still refuse — a GREEN gating the whole check "
        f"on cycle==1 would wrongly pass here. Got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    assert result.recoverable is False
    assert _head_sha(repo) == frozen_sha, "no RED commit may land from a refused cycle-2 run"


# ═══════════════════════════════════════════════════════════════════════════
# AC8 (gate M4) — GH282 mass-deletion gate keeps precedence; pragma exempts
# ═══════════════════════════════════════════════════════════════════════════


def _setup_mass_deletion_repo(tmp_path: Path, subdir: str, pragma: bool):
    """Mirrors test_gh282_pragma_escape.py:198-211 — committed
    'tests/wanted.py' at 3100 lines, mass-deleted to 693 lines (deleted=2407
    >= default max 120 -> violation). Optionally leaves the pragma token in
    the post-deletion content. Frozen ref written explicitly (M1)."""
    repo = (tmp_path / f"{subdir}_repo").resolve()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    frozen_sha = _commit_file(
        repo, "tests/wanted.py", "".join(f"L{i}\n" for i in range(3100)), "base"
    )
    content = "".join(f"L{i}\n" for i in range(693))
    if pragma:
        content += "# red-mass-deletion: allow\n"
    _write_file(repo, "tests/wanted.py", content)

    scratchpad = tmp_path / f"{subdir}_scratch"
    scratchpad.mkdir()
    _write_frozen_ref(scratchpad, frozen_sha)

    spec_path = scratchpad / "spec.md"
    spec_path.write_text("# Spec\n\n## Files\n- tests/wanted.py\n\n## End\n")
    return repo, scratchpad, frozen_sha, spec_path


def test_ac8a_real_over_threshold_deletion_ghpragma_exempted_no_refusal(
    tmp_path: Path, monkeypatch,
) -> None:
    """AC8a (M5): G is bound to the AUTHORIZATION EVENT, not the token — a RED
    path present in the frozen tree with a REAL over-threshold deletion (same
    3100->693-line magnitude as ac8b) that GH282 actually placed in its
    `_mdl_exempted` partition (:2028-2034, pragma present) -> no refusal.

    A pure append merely carrying the token does NOT qualify (that shape is
    ac8c, and must refuse) — this fixture must land a genuine violation so
    `_mdl_exempted` is non-empty, exactly like `_setup_mass_deletion_repo`
    with pragma=True. Enforcement is set explicitly, same as ac8b, since
    HAL_RED_MASS_DELETION_ENFORCE is a default-ON kill-switch gate."""
    repo, scratchpad, frozen_sha, spec_path = _setup_mass_deletion_repo(
        tmp_path, "ac8a", pragma=True
    )
    monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "1")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1, spec_path=str(spec_path))

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code != "E_RED_MASS_DELETION", (
        f"pre-condition: the pragma must have exempted this real deletion from "
        f"GH282's own gate first, got error_code={getattr(result, 'error_code', None)!r}"
    )
    assert result.error_code != "E_RED_TESTS_IN_EXISTING_FILE", (
        f"a path GH282 actually EXEMPTED (real over-threshold deletion, pragma "
        f"present) must also be exempt from D1's refusal (G) — got "
        f"status={result.status!r} error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )
    assert result.status == "ok", (
        f"expected ok for this genuinely GH282-exempted path, got {result.status!r}: "
        f"{getattr(result, 'error', None)!r}"
    )


def test_ac8c_plain_append_with_pragma_no_deletion_still_refuses(tmp_path: Path) -> None:
    """AC8c (M5 — the self-granting hole must stay shut): a pre-existing path
    edited as a PLAIN APPEND (no deletion at all), carrying the
    `# red-mass-deletion: allow` token, must STILL refuse with
    `E_RED_TESTS_IN_EXISTING_FILE`. GH282 classifies nothing here (deleted==0
    at :1792 -> continue), so `_mdl_exempted` stays empty — G is empty for
    this path, and the token alone must NOT buy an exemption. This is the
    test that keeps the exemption bound to the authorization EVENT rather
    than to RED typing the token itself (the hole ac8a used to pin open)."""
    repo = (tmp_path / "ac8c_repo").resolve()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    frozen_sha = _commit_file(
        repo, "tests/test_existing.py", "def test_a():\n    assert True\n", "add existing test"
    )
    # Plain append — strictly ADDS lines, deletes nothing — carrying the
    # pragma token. GH282's diff --numstat will show deleted==0 for this path.
    _write_file(
        repo, "tests/test_existing.py",
        "def test_a():\n    assert True\n\n\ndef test_new_red():\n    assert False\n"
        "# red-mass-deletion: allow\n",
    )
    scratchpad = tmp_path / "ac8c_scratch"
    scratchpad.mkdir()
    _write_frozen_ref(scratchpad, frozen_sha)

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1)

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"a plain append that merely CARRIES the pragma token, with no real "
        f"deletion for GH282 to have exempted, must NOT be exempt from D1's "
        f"refusal — RED typing one comment must not buy its own exemption. "
        f"Got status={result.status!r} error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )
    assert result.recoverable is False
    assert _head_sha(repo) == frozen_sha, "no RED commit may land from a refused run"


def test_ac8d_real_deletion_no_pragma_enforce_off_default_still_refuses(
    tmp_path: Path, monkeypatch,
) -> None:
    """AC8d (gate round 4 — kills the `G = _mdl_all` naive GREEN): a
    pre-existing path with a REAL over-threshold deletion, NO pragma, and
    `HAL_RED_MASS_DELETION_ENFORCE=0` (kill-switch, set explicitly below —
    the shipped default is ON since the bd flip 2026-10-03) must still
    refuse with `E_RED_TESTS_IN_EXISTING_FILE`.

    This is the discriminator ac8a/ac8b/ac8c cannot provide: ac8a's path IS
    in `_mdl_all` (real deletion) so a `G = _mdl_all` GREEN would wrongly
    exempt it there too (indistinguishable from `G = _mdl_exempted`); ac8b
    forces enforce=1 so GH282 itself returns first, masking G entirely; ac8c
    has no deletion at all, so it lands in neither set. Only this fixture —
    real deletion, NO pragma, enforce OFF (the production default) — lets
    `_mdl_all` and `_mdl_exempted` diverge: the path is in `_mdl_all` (a
    `G = _mdl_all` GREEN would wrongly exempt it) but NOT in `_mdl_exempted`
    (the correct `G` refuses it, since nothing authorised this deletion)."""
    repo, scratchpad, frozen_sha, spec_path = _setup_mass_deletion_repo(
        tmp_path, "ac8d", pragma=False
    )
    # ENFORCE=0 (kill-switch, warn-only) so GH282's own gate never blocks
    # here and D1 is the only thing that can refuse this path.
    monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "0")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1, spec_path=str(spec_path))

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"a real over-threshold deletion with NO pragma, under the default "
        f"(enforce-OFF) GH282 posture, must still be refused by D1 using "
        f"G=_mdl_exempted (empty here, since nothing was authorised) — a "
        f"G=_mdl_all GREEN would wrongly exempt this path instead. Got "
        f"status={result.status!r} error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )
    assert result.recoverable is False


def test_ac8b_mass_deletion_without_pragma_yields_mass_deletion_not_existing_file(
    tmp_path: Path, monkeypatch,
) -> None:
    """AC8b (M4 ordering pin): mirrors test_gh282_pragma_escape.py:246-274
    (`test_enforced_no_pragma_still_blocks_negative_guard`) — a mass-deletion
    input on a pre-existing file WITHOUT the pragma must still yield
    `E_RED_MASS_DELETION`, NOT `E_RED_TESTS_IN_EXISTING_FILE`. GH282 keeps
    precedence because the mass-deletion gate runs first (:2018), before
    D1's check.

    This test sets `HAL_RED_MASS_DELETION_ENFORCE=1` EXPLICITLY (same as the
    default since the bd flip 2026-10-03) — GH282 only keeps precedence when
    its own enforcement is on. With the kill-switch (`=0`, warn-only), this
    same non-pragma'd mass deletion on a pre-existing file would terminate
    as `E_RED_TESTS_IN_EXISTING_FILE` instead (still actionable, just a
    different code) — D1 does not defer to a gate deliberately held in warn
    mode; this AC only pins the enforce-on case."""
    repo, scratchpad, frozen_sha, spec_path = _setup_mass_deletion_repo(
        tmp_path, "ac8b", pragma=False
    )
    monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "1")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1, spec_path=str(spec_path))

    result = mod._commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"expected a refusal for this over-threshold, non-pragma'd deletion, "
        f"got {result.status!r}"
    )
    assert result.error_code == "E_RED_MASS_DELETION", (
        f"GH282's mass-deletion gate must keep PRECEDENCE over D1's "
        f"'E_RED_TESTS_IN_EXISTING_FILE' — since tests/wanted.py IS present in "
        f"the frozen tree ({frozen_sha!r}) and not authorized, a D1-first "
        f"ordering would wrongly report E_RED_TESTS_IN_EXISTING_FILE instead. "
        f"Got error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )
    assert result.recoverable is False


# ═══════════════════════════════════════════════════════════════════════════
# AC9a/AC9b (gate round 5, Group A) — the dirty-in-worktree conjunct
# ═══════════════════════════════════════════════════════════════════════════


def _recovery_fixture_no_persisted_ref(tmp_path: Path, subdir: str, dirty: bool):
    """Shared recovery-shape fixture for AC9a/AC9b — differential twin, the
    ONLY difference being whether the RED edit is committed (dirty=False,
    AC9a) or left uncommitted (dirty=True, AC9b). Deliberately writes NO
    `pre-red-ref.txt` — this is the recovery shape v1-v4 fixtures avoided by
    writing the ref explicitly; `_resolve_frozen_pre_red_sha` (:592-614)
    falls back to `rev-parse HEAD`."""
    repo = (tmp_path / f"{subdir}_repo").resolve()
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    _commit_file(
        repo, "tests/test_existing.py", "def test_a():\n    assert True\n",
        "add existing test (already landed)",
    )
    if dirty:
        _write_file(
            repo, "tests/test_existing.py",
            "def test_a():\n    assert True\n\n\ndef test_new_red():\n    assert False\n",
        )
    scratchpad = tmp_path / f"{subdir}_scratch"
    scratchpad.mkdir()  # NO pre-red-ref.txt written — the point of AC9a/AC9b
    return repo, scratchpad


def test_ac9a_recovery_no_persisted_ref_path_clean_at_head_no_refusal(tmp_path: Path) -> None:
    """AC9a (gate round 5): no persisted pre-red-ref.txt, the RED commit is
    ALREADY LANDED (tests/test_existing.py present at HEAD, worktree CLEAN
    for it) -> no refusal, status='ok'. This is the recovery shape all
    v1-v4 fixtures avoided by writing the ref explicitly — without the
    dirty-in-worktree conjunct, D1 wrongly refuses a legitimate recovery
    where the frozen-SHA fallback (no ref persisted) resolves to HEAD, which
    already contains the committed RED file."""
    repo, scratchpad = _recovery_fixture_no_persisted_ref(tmp_path, "ac9a", dirty=False)

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1, red_test_paths=["tests/test_existing.py"])

    result = mod._commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"a path already committed at HEAD, clean in the worktree, with no "
        f"persisted pre-red-ref.txt (fallback resolves F=HEAD) must NOT be "
        f"refused — got {result.status!r} "
        f"(error_code={getattr(result, 'error_code', None)!r}, "
        f"error={getattr(result, 'error', None)!r})"
    )
    assert result.error_code is None, (
        f"expected error_code=None for this clean-at-HEAD recovery, got "
        f"{result.error_code!r}"
    )


def test_ac9b_recovery_no_persisted_ref_path_dirty_still_refuses(tmp_path: Path) -> None:
    """AC9b (MANDATORY twin of AC9a — do not ship one without the other):
    IDENTICAL fixture to AC9a except the RED edit is left UNCOMMITTED (dirty
    relative to HEAD) -> still refuses with E_RED_TESTS_IN_EXISTING_FILE.

    Without this twin, a GREEN of "no persisted ref => skip the check
    entirely" would pass AC9a and every other AC, and D1 would go INERT on
    the fresh-scratchpad cycle-1 run that is its main case (no ref is ever
    persisted before the very first _commit_red_tests call of a build)."""
    repo, scratchpad = _recovery_fixture_no_persisted_ref(tmp_path, "ac9b", dirty=True)

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1)

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"the SAME fixture as AC9a, but with the RED edit left UNCOMMITTED "
        f"(dirty relative to HEAD), must still refuse — a GREEN that treats "
        f"'no persisted ref' as a blanket skip would wrongly pass here too. "
        f"Got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    assert result.recoverable is False


# ═══════════════════════════════════════════════════════════════════════════
# AC10 (gate round 5, decision B3) — mass-deletion branch names the right remedy
# ═══════════════════════════════════════════════════════════════════════════


def test_ac10_mass_deletion_branch_error_names_deletion_and_gh282_not_new_file(
    tmp_path: Path, monkeypatch,
) -> None:
    """AC10: when the offending path is in `_mdl_all` (GH282 classified a
    real over-threshold deletion) but D1 still refuses it (same shape as
    AC8d — no pragma, ENFORCE=0 set explicitly, so GH282 itself never
    blocks and D1's refusal is what fires), `error[:120]` must name the
    DELETION as the problem and GH282 as its owner — "write a new test
    file" (AC2's remedy) is the WRONG remedy for an operator who just
    deleted ~2400 lines, and must NOT appear in the first 120 chars."""
    repo, scratchpad, frozen_sha, spec_path = _setup_mass_deletion_repo(
        tmp_path, "ac10", pragma=False
    )
    # ENFORCE=0 (kill-switch) so GH282 itself does not block and D1's own
    # refusal message is what this AC inspects (same fixture shape as AC8d).
    monkeypatch.setenv("HAL_RED_MASS_DELETION_ENFORCE", "0")

    ctx = _make_ctx(scratchpad, str(repo))
    prev = _make_prev(cycle=1, spec_path=str(spec_path))

    result = mod._commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_TESTS_IN_EXISTING_FILE", (
        f"pre-condition for this AC not met: got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}"
    )
    error_msg = result.error or ""
    head = error_msg[:120]

    assert re.search(r"delet", head, re.IGNORECASE), (
        f"the mass-deletion branch's error[:120] must name the DELETION as "
        f"the problem, got head={head!r} (full message={error_msg!r})"
    )
    assert "gh282" in head.lower(), (
        f"the mass-deletion branch's error[:120] must name GH282 as the "
        f"owner of this check, got head={head!r} (full message={error_msg!r})"
    )
    assert "new test file" not in head.lower() and "new file" not in head.lower(), (
        f"'write a new test file' is the WRONG remedy for a real mass "
        f"deletion (AC2's remedy must not leak into this branch), got "
        f"head={head!r} (full message={error_msg!r})"
    )
