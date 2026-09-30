"""RED tests for GH1562 — cross-tree auto-revert bounded by the build's own
worker manifest.

Frozen spec: SHARED/memory/Decisions/
2026-08-14_C8E48307_gh1562_cross_tree_revert_manifest_bounded_spec.md

Contract summary (spec §2): `_maybe_emit_cross_tree_warning`'s best-effort
auto-revert currently reverts EVERY tracked-modified file it finds in the
main checkout (`_verify_no_cross_tree_edits`'s unfiltered `modified_files`).
This is the #1562 incident: a tracked state file with an uncommitted append
made by another process gets silently `git checkout --`'d away. This lot
adds `_filter_cross_tree_to_worker_manifest` (new, module-level,
`phase_workflows_common.py`) that partitions the detected files into
`owned` (this build's own `worker_written_paths` manifest proves it wrote
that file, `os.path.realpath` on both sides — §1j) and `refused`
(everything else). Only `owned` is ever passed to
`_revert_cross_tree_modifications`; `refused` is reported via a new
`cross_tree_revert_refused` event and never touched. The anti-rot registry
(`lib/mutating_git_lint.py`) moves `_revert_cross_tree_modifications` out of
`DECLARED_NON_GIT_CWD_SITES` into a new `MANIFEST_BOUNDED_WRITE_SITES`,
re-verified against a real AST call to the new marker
(`_filter_cross_tree_to_worker_manifest`) — not a rubber stamp (AC10 mirrors
AC34's shape for the sibling `is_ambient_git_cwd` registry).

§1q: `_filter_cross_tree_to_worker_manifest`, `MANIFEST_BOUNDED_WRITE_SITES`
do not exist on `main` today. `phase_workflows_common` and
`mutating_git_lint` themselves already exist (only gain new symbols), so
importing THOSE modules at top level is safe and this file always collects
cleanly; the not-yet-existing symbols are resolved via `getattr(...)` /
deferred access INSIDE each test body, never at import time, so every
failure below happens at assert time, never at collect time (D1CF5FDF).

§1l: AC1-AC4, AC6, AC7, AC12, AC13 drive the REAL production functions
against a REAL temp git repo (`git init`) + a REAL `git worktree add`
secondary worktree — no monkeypatching of `_verify_no_cross_tree_edits`,
`_revert_cross_tree_modifications`, or `_filter_cross_tree_to_worker_manifest`
themselves. The only monkeypatched seam is `_emit_safe`, to capture
telemetry — the existing house idiom for this file family (see
`test_phase_5_implement.py`'s F3 cross-tree tests). Every real-git AC asserts
on raw file BYTES (`Path.read_bytes()`) and/or `git status --porcelain`,
never only on an event or a returned code (§1l).

§1j: every temp git root that participates in a byte/porcelain assertion is
wrapped in `os.path.realpath(...)` (macOS `/var` -> `/private/var`) EXCEPT
AC6's own fixture, which deliberately withholds `os.path.realpath` on one
side to construct the exact `/var`-alias-vs-`/private/var` mismatch AC6
tests the production code's OWN `os.path.realpath` handling against — its
final assertions still resolve both sides so the test's own comparison is
correct regardless of the OS symlink quirk.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows import phase_workflows_common  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════
# shared helpers — real git repos, no mocking of the UUTs (§1l)
# ═════════════════════════════════════════════════════════════════════════


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True, capture_output=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "-C", str(repo), "add", relpath], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", msg,
        ],
        check=True, capture_output=True,
    )
    return _head_sha(repo)


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def _porcelain(repo: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "-uall"],
        capture_output=True, text=True, check=True,
    )
    return r.stdout


def _add_worktree(main: Path, wt: Path, branch: str) -> None:
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-b", branch, str(wt)],
        check=True, capture_output=True,
    )


def _build_repo_pair(
    tmp_path: Path, name: str, files: "dict[str, str]", apply_realpath: bool = True,
) -> "tuple[Path, Path]":
    """Real main checkout with `files` (relpath -> body) committed, plus a
    real `git worktree add` secondary worktree. §1j: realpath'd by default;
    AC6 deliberately passes `apply_realpath=False` (see module docstring)."""
    raw_main = tmp_path / f"{name}_main"
    main = Path(os.path.realpath(str(raw_main))) if apply_realpath else raw_main
    _init_repo(main)
    for relpath, body in files.items():
        _commit_file(main, relpath, body, f"init {relpath}")
    raw_wt = tmp_path / f"{name}_wt"
    wt = Path(os.path.realpath(str(raw_wt))) if apply_realpath else raw_wt
    _add_worktree(main, wt, f"{name}-feat")
    return main, wt


def _append_uncommitted(path: Path, extra_text: str) -> None:
    path.write_bytes(path.read_bytes() + extra_text.encode("utf-8"))


def _record_events(monkeypatch, module) -> "list[dict]":
    captured: list[dict] = []

    def _recorder(event_type, payload=None, **kw):
        captured.append({"type": str(event_type), "payload": dict(payload or {})})
        return None

    monkeypatch.setattr(module, "_emit_safe", _recorder)
    return captured


def _make_result(data: dict) -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_red_llm")


OFI_RELPATH = "SHARED/state/ofi-log.jsonl"
LEAKED_RELPATH = "src/leaked.py"


# ═════════════════════════════════════════════════════════════════════════
# AC1 — §1l PRODUCTION-SIDE-EFFECT ANCHOR (op: skip-all)
# ═════════════════════════════════════════════════════════════════════════


def test_ac1_skip_all_guard_never_reverts_unowned_appended_ofi_log(tmp_path):
    """AC1: real main checkout, tracked SHARED/state/ofi-log.jsonl committed,
    then an UNCOMMITTED append (mirrors the observed #1562 loss). The real
    `_maybe_emit_cross_tree_warning`, called with an EMPTY worker manifest
    (this build wrote nothing), must NOT revert it: appended bytes survive
    byte-identical, and `git status --porcelain` still reports it modified.

    Pre-GREEN FAIL: today's wrapper reverts EVERY tracked-modified file
    unconditionally (no manifest check exists at all), so the appended line
    is destroyed and porcelain goes clean — the exact #1562 incident
    signature."""
    main, wt = _build_repo_pair(tmp_path, "ac1", {OFI_RELPATH: '{"id":"075a725a"}\n'})
    ofi_path = main / OFI_RELPATH
    pre_append_bytes = ofi_path.read_bytes()
    _append_uncommitted(ofi_path, '{"id":"bc461c1d"}\n')
    appended_bytes = ofi_path.read_bytes()
    assert appended_bytes != pre_append_bytes, "AC1 fixture precondition: append must change bytes"

    result = _make_result({"worker_written_paths": [], "manifest_source": "harness_tool_record"})

    phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    after_bytes = ofi_path.read_bytes()
    assert after_bytes == appended_bytes, (
        f"AC1: expected {OFI_RELPATH!r} bytes UNCHANGED (appended line "
        f"survives an unowned/empty-manifest revert attempt); expected "
        f"{appended_bytes!r}, actual {after_bytes!r}"
    )
    porcelain = _porcelain(main)
    assert OFI_RELPATH in porcelain, (
        f"AC1: expected `git status --porcelain` to still report "
        f"{OFI_RELPATH!r} modified; expected it present, actual "
        f"porcelain={porcelain!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC2 — op: own
# ═════════════════════════════════════════════════════════════════════════


def test_ac2_owned_file_reverted_and_event_carries_owned_files_only(tmp_path, monkeypatch):
    """AC2: tracked src/leaked.py dirty AND a second tracked file
    src/other.py ALSO dirty but NOT in the manifest; manifest names only
    src/leaked.py -> src/leaked.py IS reverted to HEAD bytes,
    `cross_tree_edit_reverted` fires with `files == ["src/leaked.py"]` --
    the unowned src/other.py must NOT appear in that payload and must NOT be
    reverted (a single-owned-file fixture would not discriminate: today's
    unconditional revert produces an identical result for one file, so a
    second UNOWNED dirty file is required to force the partition).

    Pre-GREEN FAIL: today's wrapper reverts EVERY detected file
    unconditionally regardless of manifest, so src/other.py is ALSO
    reverted and appears in the (unfiltered) `files` payload -- both
    assertions below fail."""
    other_relpath = "src/other.py"
    main, wt = _build_repo_pair(
        tmp_path, "ac2", {LEAKED_RELPATH: "# leaked v1\n", other_relpath: "# other v1\n"},
    )
    leaked_path = main / LEAKED_RELPATH
    other_path = main / other_relpath
    leaked_original = leaked_path.read_bytes()
    other_original = other_path.read_bytes()
    leaked_path.write_text("# polluted by another process\n")
    other_path.write_text("# polluted, NOT owned by this build\n")
    assert leaked_path.read_bytes() != leaked_original, "AC2 fixture precondition (leaked)"
    assert other_path.read_bytes() != other_original, "AC2 fixture precondition (other)"

    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({
        "worker_written_paths": [str(leaked_path)],
        "manifest_source": "harness_tool_record",
    })

    phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    assert leaked_path.read_bytes() == leaked_original, (
        f"AC2: expected {LEAKED_RELPATH!r} reverted to HEAD bytes "
        f"{leaked_original!r}; actual {leaked_path.read_bytes()!r}"
    )
    assert other_path.read_bytes() != other_original, (
        f"AC2: expected UNOWNED {other_relpath!r} to remain dirty (NOT "
        f"reverted); actual it was reverted to HEAD bytes {other_original!r}"
    )
    reverted_events = [e for e in events if e["type"] == "cross_tree_edit_reverted"]
    assert len(reverted_events) == 1, (
        f"AC2: expected exactly one cross_tree_edit_reverted event; actual "
        f"types={[e['type'] for e in events]!r}"
    )
    assert reverted_events[0]["payload"].get("files") == [LEAKED_RELPATH], (
        f"AC2: expected reverted event files==[{LEAKED_RELPATH!r}] (owned "
        f"only, NOT {other_relpath!r}); actual "
        f"{reverted_events[0]['payload'].get('files')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC3 — ops: own + refuse, exact partition
# ═════════════════════════════════════════════════════════════════════════


def test_ac3_mixed_manifest_exact_partition_reverted_vs_refused(tmp_path, monkeypatch):
    """AC3: both ofi-log.jsonl and src/leaked.py dirty, manifest names ONLY
    src/leaked.py -> exact values: reverted files == ["src/leaked.py"],
    refused_files == ["SHARED/state/ofi-log.jsonl"], ofi-log bytes unchanged.
    PLUS the detection-shield (M3): the `cross_tree_edit_detected` payload's
    `modified_files` AND `result.data["cross_tree_files"]` each equal the
    UNFILTERED pair `["SHARED/state/ofi-log.jsonl", "src/leaked.py"]`
    (`_verify_no_cross_tree_edits`'s own sort order) -- narrowing detection
    to `owned` is a regression.

    Pre-GREEN FAIL: today's wrapper reverts BOTH files unconditionally
    (no manifest, no partition) -- ofi-log's bytes are destroyed. The
    detection-shield assertions pin that a future GREEN cannot "fix" the
    partition by narrowing `cross_tree_edit_detected` / `cross_tree_files`
    instead of narrowing only the mutation."""
    unfiltered_pair = [OFI_RELPATH, LEAKED_RELPATH]  # git status --porcelain sort order
    main, wt = _build_repo_pair(
        tmp_path, "ac3", {LEAKED_RELPATH: "# leaked v1\n", OFI_RELPATH: '{"id":"075a725a"}\n'},
    )
    leaked_path = main / LEAKED_RELPATH
    ofi_path = main / OFI_RELPATH
    leaked_original = leaked_path.read_bytes()
    leaked_path.write_text("# polluted\n")
    ofi_appended = ofi_path.read_bytes() + b'{"id":"bc461c1d"}\n'
    ofi_path.write_bytes(ofi_appended)

    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({
        "worker_written_paths": [str(leaked_path)],
        "manifest_source": "harness_tool_record",
    })

    out = phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    assert leaked_path.read_bytes() == leaked_original, (
        f"AC3: expected {LEAKED_RELPATH!r} reverted; actual "
        f"{leaked_path.read_bytes()!r}"
    )
    assert ofi_path.read_bytes() == ofi_appended, (
        f"AC3: expected {OFI_RELPATH!r} bytes UNCHANGED (refused, unowned); "
        f"actual {ofi_path.read_bytes()!r}"
    )
    reverted = next((e for e in events if e["type"] == "cross_tree_edit_reverted"), None)
    refused = next((e for e in events if e["type"] == "cross_tree_revert_refused"), None)
    detected = next((e for e in events if e["type"] == "cross_tree_edit_detected"), None)
    assert reverted is not None, f"AC3: expected a cross_tree_edit_reverted event; actual events={events!r}"
    assert refused is not None, f"AC3: expected a cross_tree_revert_refused event; actual events={events!r}"
    assert detected is not None, f"AC3: expected a cross_tree_edit_detected event; actual events={events!r}"
    assert reverted["payload"].get("files") == [LEAKED_RELPATH], (
        f"AC3: expected reverted files==[{LEAKED_RELPATH!r}]; actual "
        f"{reverted['payload'].get('files')!r}"
    )
    assert refused["payload"].get("refused_files") == [OFI_RELPATH], (
        f"AC3: expected refused_files==[{OFI_RELPATH!r}]; actual "
        f"{refused['payload'].get('refused_files')!r}"
    )
    assert detected["payload"].get("modified_files") == unfiltered_pair, (
        f"AC3 (M3 detection-shield): expected cross_tree_edit_detected "
        f"modified_files==UNFILTERED {unfiltered_pair!r} (never narrowed to "
        f"owned); actual {detected['payload'].get('modified_files')!r}"
    )
    assert out.data.get("cross_tree_files") == unfiltered_pair, (
        f"AC3 (M3 detection-shield): expected result.data['cross_tree_files']"
        f"==UNFILTERED {unfiltered_pair!r} (never narrowed to owned); actual "
        f"{out.data.get('cross_tree_files')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC4 — fail-closed on absent manifest
# ═════════════════════════════════════════════════════════════════════════


def test_ac4_absent_manifest_key_fails_closed_refuses_everything(tmp_path, monkeypatch):
    """AC4: result.data has NO worker_written_paths key -> nothing reverted,
    both files survive byte-identical, `cross_tree_revert_refused` fires
    with manifest_source=='unavailable:missing' (round-3 B3: the §2.2
    rejection-taxonomy token for a missing manifest, not bare
    'unavailable') and refused_count==2.

    Pre-GREEN FAIL: today's wrapper has no manifest concept, so it reverts
    both files regardless of the absent key."""
    main, wt = _build_repo_pair(
        tmp_path, "ac4", {LEAKED_RELPATH: "# leaked v1\n", OFI_RELPATH: '{"id":"075a725a"}\n'},
    )
    leaked_path = main / LEAKED_RELPATH
    ofi_path = main / OFI_RELPATH
    leaked_path.write_text("# polluted\n")
    leaked_dirty = leaked_path.read_bytes()
    ofi_appended = ofi_path.read_bytes() + b'{"id":"bc461c1d"}\n'
    ofi_path.write_bytes(ofi_appended)

    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({"prompt": "noop"})  # NO worker_written_paths key at all

    phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    assert leaked_path.read_bytes() == leaked_dirty, (
        f"AC4: expected {LEAKED_RELPATH!r} untouched (fail-closed); actual "
        f"{leaked_path.read_bytes()!r}"
    )
    assert ofi_path.read_bytes() == ofi_appended, (
        f"AC4: expected {OFI_RELPATH!r} untouched (fail-closed); actual "
        f"{ofi_path.read_bytes()!r}"
    )
    refused = [e for e in events if e["type"] == "cross_tree_revert_refused"]
    assert len(refused) == 1, f"AC4: expected one cross_tree_revert_refused event; actual events={events!r}"
    payload = refused[0]["payload"]
    assert payload.get("manifest_source") == "unavailable:missing", (
        f"AC4: expected manifest_source=='unavailable:missing'; actual "
        f"{payload.get('manifest_source')!r}"
    )
    assert payload.get("refused_count") == 2, (
        f"AC4: expected refused_count==2; actual {payload.get('refused_count')!r}"
    )
    reverted = [e for e in events if e["type"] == "cross_tree_edit_reverted"]
    assert reverted == [], f"AC4: expected NO cross_tree_edit_reverted event; actual events={events!r}"


# ═════════════════════════════════════════════════════════════════════════
# AC5 — §1aa named module-level function, direct call, exact 3-tuple
# ═════════════════════════════════════════════════════════════════════════


def test_ac5_filter_is_module_level_function_returns_exact_three_tuple(tmp_path):
    """AC5: `_filter_cross_tree_to_worker_manifest` is a module-level
    function (`inspect.isfunction`) and for a given (root, files, result)
    returns the exact 3-tuple (owned, refused, source).

    Pre-GREEN FAIL: attribute does not exist yet."""
    fn = getattr(phase_workflows_common, "_filter_cross_tree_to_worker_manifest", None)
    assert fn is not None, (
        "AC5: expected phase_workflows_common._filter_cross_tree_to_worker_"
        "manifest to exist as a module-level function; actual attribute is "
        "absent (None)"
    )
    assert inspect.isfunction(fn), (
        f"AC5: expected a module-level function (inspect.isfunction); "
        f"actual {fn!r}"
    )

    root = tmp_path / "ac5_root"
    root.mkdir()
    owned_rel = "src/owned.py"
    other_rel = "src/other.py"
    manifest = [str(root / owned_rel)]
    result = _make_result({
        "worker_written_paths": manifest, "manifest_source": "harness_tool_record",
    })

    out = fn(root, [owned_rel, other_rel], result)

    assert out == ([owned_rel], [other_rel], "harness_tool_record"), (
        f"AC5: expected exact 3-tuple "
        f"(['{owned_rel}'], ['{other_rel}'], 'harness_tool_record'); "
        f"actual {out!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC6 — §1j macOS /var vs /private/var, real git
# ═════════════════════════════════════════════════════════════════════════


def test_ac6_manifest_entry_through_var_alias_matches_via_realpath(tmp_path):
    """AC6 (M1 — rewritten): pytest's `tmp_path` is ALREADY realpath-resolved
    (macOS `TempPathFactory` resolves it), so an implicit `/var` vs
    `/private/var` OS quirk is vacuous here and would not force `realpath`
    handling at all. Build the alias EXPLICITLY instead: a real symlink
    `ac6_alias -> main_repo_root`, manifest entry
    `str(alias / "src/leaked.py")` (a genuinely different absolute path
    string that only resolves to the same file via `os.path.realpath`). The
    file IS reverted -- proves both sides of the ownership predicate are
    realpath'd. A GREEN that omits `os.path.realpath` entirely (naive string
    equality) MUST fail this AC, since the alias path never string-equals
    the detected repo-relative resolution.

    Pre-GREEN FAIL: `_filter_cross_tree_to_worker_manifest` does not exist
    at all yet; ImportError-equivalent (AttributeError via getattr) at
    assert time."""
    fn = getattr(phase_workflows_common, "_filter_cross_tree_to_worker_manifest", None)
    assert fn is not None, (
        "AC6: expected phase_workflows_common._filter_cross_tree_to_worker_"
        "manifest to exist; actual attribute is absent (None)"
    )

    main, wt = _build_repo_pair(tmp_path, "ac6", {LEAKED_RELPATH: "# leaked v1\n"})
    leaked_path = main / LEAKED_RELPATH
    original_bytes = leaked_path.read_bytes()
    leaked_path.write_text("# polluted\n")

    alias = tmp_path / "ac6_alias"
    os.symlink(main, alias)
    assert os.path.realpath(str(alias)) == os.path.realpath(str(main)), (
        "AC6 fixture precondition: symlink alias must realpath to main"
    )

    manifest = [str(alias / LEAKED_RELPATH)]  # genuinely different string, same realpath target
    result = _make_result({
        "worker_written_paths": manifest, "manifest_source": "harness_tool_record",
    })

    phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    actual_bytes = leaked_path.read_bytes()
    assert actual_bytes == original_bytes, (
        f"AC6: expected {LEAKED_RELPATH!r} reverted -- the manifest entry "
        f"was addressed through a real symlink alias "
        f"({alias / LEAKED_RELPATH!s}) whose os.path.realpath equals the "
        f"real main-repo file, forcing the ownership predicate to realpath "
        f"both sides; expected {original_bytes!r}, actual {actual_bytes!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC7 — op: refuse (wrong tree)
# ═════════════════════════════════════════════════════════════════════════


def test_ac7_manifest_entry_naming_worktree_tree_is_refused(tmp_path, monkeypatch):
    """AC7: manifest entry points at <worktree_root>/src/leaked.py (same
    repo-relative name, DIFFERENT tree) while src/leaked.py is dirty in the
    MAIN checkout -> NOT reverted, appears in refused.

    Pre-GREEN FAIL: today's wrapper has no per-file ownership check at all,
    so this file is reverted regardless of which tree the manifest names."""
    main, wt = _build_repo_pair(tmp_path, "ac7", {LEAKED_RELPATH: "# leaked v1\n"})
    main_leaked = main / LEAKED_RELPATH
    original_bytes = main_leaked.read_bytes()
    main_leaked.write_text("# polluted in MAIN\n")
    dirty_bytes = main_leaked.read_bytes()

    events = _record_events(monkeypatch, phase_workflows_common)
    wrong_tree_entry = str(wt / LEAKED_RELPATH)
    result = _make_result({
        "worker_written_paths": [wrong_tree_entry], "manifest_source": "harness_tool_record",
    })

    phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    after_bytes = main_leaked.read_bytes()
    assert after_bytes == dirty_bytes, (
        f"AC7: expected {LEAKED_RELPATH!r} in MAIN to remain UNREVERTED "
        f"(manifest entry pointed at the worktree tree, not main); expected "
        f"still-dirty {dirty_bytes!r}, actual {after_bytes!r} "
        f"(HEAD bytes were {original_bytes!r})"
    )
    refused = [e for e in events if e["type"] == "cross_tree_revert_refused"]
    assert len(refused) == 1, f"AC7: expected one cross_tree_revert_refused event; actual events={events!r}"
    assert refused[0]["payload"].get("refused_files") == [LEAKED_RELPATH], (
        f"AC7: expected refused_files==[{LEAKED_RELPATH!r}]; actual "
        f"{refused[0]['payload'].get('refused_files')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC8 — refused event payload contract
# ═════════════════════════════════════════════════════════════════════════


def test_ac8_refused_event_payload_carries_required_fields(tmp_path, monkeypatch):
    """AC8: `cross_tree_revert_refused` payload carries refused_files,
    refused_count, manifest_source, step, main_repo_root;
    refused_count == len(refused_files).

    Pre-GREEN FAIL: the event type `cross_tree_revert_refused` is never
    emitted at all today."""
    main, wt = _build_repo_pair(
        tmp_path, "ac8", {LEAKED_RELPATH: "# leaked v1\n", OFI_RELPATH: '{"id":"075a725a"}\n'},
    )
    (main / LEAKED_RELPATH).write_text("# polluted\n")
    ofi_path = main / OFI_RELPATH
    ofi_path.write_bytes(ofi_path.read_bytes() + b'{"id":"bc461c1d"}\n')

    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({"worker_written_paths": [], "manifest_source": "harness_tool_record"})

    phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    refused = [e for e in events if e["type"] == "cross_tree_revert_refused"]
    assert len(refused) == 1, f"AC8: expected one cross_tree_revert_refused event; actual events={events!r}"
    payload = refused[0]["payload"]
    for key in ("refused_files", "refused_count", "manifest_source", "step", "main_repo_root"):
        assert key in payload, (
            f"AC8: expected key {key!r} present in refused payload; actual "
            f"keys={sorted(payload.keys())!r}"
        )
    assert payload["refused_count"] == len(payload["refused_files"]), (
        f"AC8: expected refused_count==len(refused_files); actual "
        f"refused_count={payload['refused_count']!r} "
        f"refused_files={payload['refused_files']!r}"
    )
    assert payload["step"] == "invoke_red_llm", (
        f"AC8: expected step=='invoke_red_llm'; actual {payload['step']!r}"
    )
    assert Path(payload["main_repo_root"]).resolve() == main.resolve(), (
        f"AC8: expected main_repo_root to resolve to the real main checkout "
        f"{main!r}; actual {payload['main_repo_root']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC9 — anti-rot registry, real engine tree
# ═════════════════════════════════════════════════════════════════════════


def test_ac9_registry_moved_to_manifest_bounded_and_real_tree_clean():
    """AC9: `MANIFEST_BOUNDED_WRITE_SITES` contains
    `_revert_cross_tree_modifications`; `DECLARED_NON_GIT_CWD_SITES` does
    NOT; `find_unclassified_sites(<real engine root>)` returns [].

    Pre-GREEN FAIL: `MANIFEST_BOUNDED_WRITE_SITES` does not exist yet, and
    `_revert_cross_tree_modifications` is still declared in
    `DECLARED_NON_GIT_CWD_SITES` today."""
    from bytedigger_engine.lib import mutating_git_lint  # noqa: PLC0415

    manifest_sites = getattr(mutating_git_lint, "MANIFEST_BOUNDED_WRITE_SITES", None)
    assert manifest_sites is not None, (
        "AC9: expected mutating_git_lint.MANIFEST_BOUNDED_WRITE_SITES to "
        "exist; actual attribute is absent (None)"
    )
    assert "_revert_cross_tree_modifications" in manifest_sites, (
        f"AC9: expected '_revert_cross_tree_modifications' registered in "
        f"MANIFEST_BOUNDED_WRITE_SITES; actual keys="
        f"{sorted(manifest_sites)!r}"
    )
    assert "_revert_cross_tree_modifications" not in mutating_git_lint.DECLARED_NON_GIT_CWD_SITES, (
        f"AC9: expected '_revert_cross_tree_modifications' REMOVED from "
        f"DECLARED_NON_GIT_CWD_SITES (moved to the manifest-bounded "
        f"registry); actual it is still present: "
        f"{sorted(mutating_git_lint.DECLARED_NON_GIT_CWD_SITES)!r}"
    )

    engine_root = Path(__file__).resolve().parents[1]
    unclassified = mutating_git_lint.find_unclassified_sites(engine_root)
    assert unclassified == [], (
        f"AC9: expected zero unclassified mutating-git sites on the real "
        f"engine prod tree; actual {unclassified!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC10 — anti-rot re-verification, synthetic AST
# ═════════════════════════════════════════════════════════════════════════


def test_ac10_manifest_bounded_site_without_marker_call_is_flagged(tmp_path):
    """AC10: a synthetic module whose `_revert_cross_tree_modifications`
    body lacks any call to the marker -> `find_unclassified_sites` reports
    it (registry is not a rubber stamp), mirroring AC34's shape for the
    sibling `is_ambient_git_cwd` registry.

    Pre-GREEN FAIL: today `_revert_cross_tree_modifications` is still
    classified via `DECLARED_NON_GIT_CWD_SITES` (a documented-reason
    registry with no marker re-verification at all), so this literal
    function name is currently classified AWAY and never reported --
    `find_unclassified_sites` returns [] for it today, the opposite of the
    genuinely-unguarded reality this AC targets."""
    from bytedigger_engine.lib import mutating_git_lint  # noqa: PLC0415

    synthetic_root = tmp_path / "synthetic_engine_gh1562"
    synthetic_root.mkdir()
    (synthetic_root / "rogue_revert_module.py").write_text(
        "def _revert_cross_tree_modifications(main_repo_root, files):\n"
        "    import subprocess\n"
        "    # NOTE: no call to the manifest-bounding marker here -- guard "
        "was dropped\n"
        "    for f in files:\n"
        "        subprocess.run(\n"
        "            ['git', '-C', str(main_repo_root), 'checkout', '--', f]\n"
        "        )\n",
        encoding="utf-8",
    )

    unclassified = mutating_git_lint.find_unclassified_sites(synthetic_root)

    reported = [s for s in unclassified if s.get("function") == "_revert_cross_tree_modifications"]
    assert len(reported) == 1, (
        f"AC10: expected the synthetic '_revert_cross_tree_modifications' "
        f"(a real MANIFEST_BOUNDED_WRITE_SITES registry member by literal "
        f"name) whose body lacks any call to the manifest-bounding marker "
        f"to be FLAGGED, not rubber-stamped; actual unclassified="
        f"{unclassified!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC11 — direct-call return-dict contract
# ═════════════════════════════════════════════════════════════════════════


def test_ac11_revert_return_dict_carries_refused_fields_and_invariant_holds(tmp_path):
    """AC11: `_revert_cross_tree_modifications` return dict carries
    `refused_count` and `refused_files`; `reverted_count + failed_count ==
    len(files)` holds for a mixed success/failure input (direct call --
    manifest filtering happens only at the wrapper level, so refused_count
    is always 0 / refused_files always [] here).

    Pre-GREEN FAIL: today's return dict has no `refused_count` /
    `refused_files` keys at all."""
    main, _wt = _build_repo_pair(tmp_path, "ac11", {LEAKED_RELPATH: "# leaked v1\n"})
    (main / LEAKED_RELPATH).write_text("# polluted\n")

    out = phase_workflows_common._revert_cross_tree_modifications(
        main, [LEAKED_RELPATH, "does/not/exist.py"],
    )

    assert "refused_count" in out, (
        f"AC11: expected key 'refused_count' in return dict; actual keys="
        f"{sorted(out.keys())!r}"
    )
    assert "refused_files" in out, (
        f"AC11: expected key 'refused_files' in return dict; actual keys="
        f"{sorted(out.keys())!r}"
    )
    assert out.get("refused_count") == 0, (
        f"AC11: expected refused_count==0 for a DIRECT call; actual "
        f"{out.get('refused_count')!r}"
    )
    assert out.get("refused_files") == [], (
        f"AC11: expected refused_files==[] for a DIRECT call; actual "
        f"{out.get('refused_files')!r}"
    )
    assert out["reverted_count"] + out["failed_count"] == 2, (
        f"AC11: expected reverted_count+failed_count==len(files)==2; "
        f"actual reverted_count={out['reverted_count']!r} "
        f"failed_count={out['failed_count']!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC12 — §1l N>1, no cross-invocation accumulation
# ═════════════════════════════════════════════════════════════════════════


def test_ac12_two_invocations_different_manifests_no_accumulation(tmp_path, monkeypatch):
    """AC12: two invocations in one run with DIFFERENT manifests -> each
    filters against its own manifest; no accumulation (call 2's refused set
    is not polluted by call 1's owned set, and vice versa).

    Pre-GREEN FAIL: today's wrapper reverts every detected file
    unconditionally, so call 1 already reverts BOTH files -- the "still
    dirty after call 1" precondition for call 2's discriminating power never
    holds."""
    a_relpath = "src/leaked_a.py"
    b_relpath = "src/leaked_b.py"
    main, wt = _build_repo_pair(
        tmp_path, "ac12", {a_relpath: "# a v1\n", b_relpath: "# b v1\n"},
    )
    a_path = main / a_relpath
    b_path = main / b_relpath
    a_original = a_path.read_bytes()
    b_original = b_path.read_bytes()
    a_path.write_text("# a polluted call1\n")
    b_path.write_text("# b polluted call1\n")

    events1 = _record_events(monkeypatch, phase_workflows_common)
    result1 = _make_result({
        "worker_written_paths": [str(a_path)], "manifest_source": "harness_tool_record",
    })
    phase_workflows_common._maybe_emit_cross_tree_warning(result1, wt)

    assert a_path.read_bytes() == a_original, (
        f"AC12 call1: expected {a_relpath!r} reverted (owned by call1's "
        f"manifest); actual {a_path.read_bytes()!r}"
    )
    b_after_call1 = b_path.read_bytes()
    assert b_after_call1 != b_original, (
        f"AC12 call1: expected {b_relpath!r} refused (unowned by call1), "
        f"still dirty; actual reverted to HEAD bytes {b_original!r}"
    )
    refused1 = [e for e in events1 if e["type"] == "cross_tree_revert_refused"]
    assert refused1 and refused1[-1]["payload"].get("refused_files") == [b_relpath], (
        f"AC12 call1: expected refused_files==[{b_relpath!r}]; actual "
        f"{[e['payload'].get('refused_files') for e in refused1]!r}"
    )

    # Re-dirty A after call1's revert -- unrelated to call1's ownership,
    # sets up call2's discriminating precondition.
    a_path.write_text("# a polluted call2\n")
    a_call2_dirty = a_path.read_bytes()

    events2 = _record_events(monkeypatch, phase_workflows_common)
    result2 = _make_result({
        "worker_written_paths": [str(b_path)], "manifest_source": "harness_tool_record",
    })
    phase_workflows_common._maybe_emit_cross_tree_warning(result2, wt)

    assert b_path.read_bytes() == b_original, (
        f"AC12 call2: expected {b_relpath!r} reverted (owned by call2's OWN "
        f"manifest, independent of call1); actual {b_path.read_bytes()!r}"
    )
    assert a_path.read_bytes() == a_call2_dirty, (
        f"AC12 call2: expected {a_relpath!r} REFUSED (call2's manifest does "
        f"NOT own it, even though call1's manifest did -- no accumulation "
        f"across invocations); actual {a_path.read_bytes()!r}"
    )
    refused2 = [e for e in events2 if e["type"] == "cross_tree_revert_refused"]
    assert refused2 and refused2[-1]["payload"].get("refused_files") == [a_relpath], (
        f"AC12 call2: expected refused_files==[{a_relpath!r}]; actual "
        f"{[e['payload'].get('refused_files') for e in refused2]!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC13 — §1ab idempotence
# ═════════════════════════════════════════════════════════════════════════


def test_ac13_idempotent_second_invocation_reverts_nothing(tmp_path, monkeypatch):
    """AC13: invoke twice on the same state -> second invocation finds the
    owned file clean, reverts nothing, emits no cross_tree_edit_reverted,
    ofi-log still intact.

    Pre-GREEN FAIL: today's wrapper reverts every detected file
    unconditionally on call 1, wiping ofi-log's uncommitted append (the
    fixture's own first-call setup assertion below fails)."""
    main, wt = _build_repo_pair(
        tmp_path, "ac13", {LEAKED_RELPATH: "# leaked v1\n", OFI_RELPATH: '{"id":"075a725a"}\n'},
    )
    leaked_path = main / LEAKED_RELPATH
    ofi_path = main / OFI_RELPATH
    leaked_original = leaked_path.read_bytes()
    leaked_path.write_text("# polluted\n")
    ofi_appended = ofi_path.read_bytes() + b'{"id":"bc461c1d"}\n'
    ofi_path.write_bytes(ofi_appended)

    manifest = [str(leaked_path)]

    _record_events(monkeypatch, phase_workflows_common)
    result1 = _make_result({
        "worker_written_paths": list(manifest), "manifest_source": "harness_tool_record",
    })
    phase_workflows_common._maybe_emit_cross_tree_warning(result1, wt)

    assert leaked_path.read_bytes() == leaked_original, (
        f"AC13 setup: expected first call to revert {LEAKED_RELPATH!r}; "
        f"actual {leaked_path.read_bytes()!r}"
    )
    assert ofi_path.read_bytes() == ofi_appended, (
        f"AC13 setup: expected {OFI_RELPATH!r} untouched (refused) after "
        f"the first call; actual {ofi_path.read_bytes()!r}"
    )

    events2 = _record_events(monkeypatch, phase_workflows_common)
    result2 = _make_result({
        "worker_written_paths": list(manifest), "manifest_source": "harness_tool_record",
    })
    phase_workflows_common._maybe_emit_cross_tree_warning(result2, wt)

    reverted2 = [e for e in events2 if e["type"] == "cross_tree_edit_reverted"]
    assert reverted2 == [], (
        f"AC13: expected NO cross_tree_edit_reverted event on the second, "
        f"idempotent invocation (the owned file is already clean); actual "
        f"events={events2!r}"
    )
    assert ofi_path.read_bytes() == ofi_appended, (
        f"AC13: expected {OFI_RELPATH!r} STILL intact (unreverted) after "
        f"the second invocation; actual {ofi_path.read_bytes()!r}"
    )
    assert leaked_path.read_bytes() == leaked_original, (
        f"AC13: expected {LEAKED_RELPATH!r} to remain at HEAD bytes "
        f"(nothing reverted again, nothing repolluted); actual "
        f"{leaked_path.read_bytes()!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC14 — owner-indirection discrimination (B1/M4, BLOCKER fix)
# ═════════════════════════════════════════════════════════════════════════


def test_ac14_owner_indirection_discrimination_flags_hazard_without_marker_in_owner_body(tmp_path):
    """AC14 (B1/M4 — the BLOCKER fix). The registry maps hazard -> guard
    OWNER: `MANIFEST_BOUNDED_WRITE_SITES == {"_revert_cross_tree_modifications":
    "_maybe_emit_cross_tree_warning"}`. A site classifies only if the
    OWNER's OWN body contains BOTH a real Call to the marker AND a real
    Call to the hazard. Synthetic module: the owner calls the hazard but
    NOT the marker; an UNRELATED function in the same module DOES call the
    marker. A module-scope check would wrongly classify this as guarded
    (the marker IS called somewhere in the module) -- the site must still
    be FLAGGED.

    Pre-GREEN FAIL: `MANIFEST_BOUNDED_WRITE_SITES` does not exist yet."""
    from bytedigger_engine.lib import mutating_git_lint  # noqa: PLC0415

    manifest_sites = getattr(mutating_git_lint, "MANIFEST_BOUNDED_WRITE_SITES", None)
    assert manifest_sites is not None, (
        "AC14: expected mutating_git_lint.MANIFEST_BOUNDED_WRITE_SITES to "
        "exist; actual attribute is absent (None)"
    )

    synthetic_root = tmp_path / "synthetic_engine_ac14"
    synthetic_root.mkdir()
    (synthetic_root / "owner_indirection_module.py").write_text(
        "def _revert_cross_tree_modifications(main_repo_root, files):\n"
        "    import subprocess\n"
        "    for f in files:\n"
        "        subprocess.run(\n"
        "            ['git', '-C', str(main_repo_root), 'checkout', '--', f]\n"
        "        )\n"
        "\n"
        "\n"
        "def _maybe_emit_cross_tree_warning(result, worktree_root):\n"
        "    # calls the hazard directly but NEVER the marker\n"
        "    _revert_cross_tree_modifications(worktree_root, [])\n"
        "    return result\n"
        "\n"
        "\n"
        "def _unrelated_helper(main_repo_root, files, result):\n"
        "    # calls the marker, but is NOT the registered owner\n"
        "    return _filter_cross_tree_to_worker_manifest(main_repo_root, files, result)\n",
        encoding="utf-8",
    )

    unclassified = mutating_git_lint.find_unclassified_sites(synthetic_root)

    reported = [s for s in unclassified if s.get("function") == "_revert_cross_tree_modifications"]
    assert len(reported) == 1, (
        f"AC14: expected the hazard site to be FLAGGED -- the marker call "
        f"lives in an UNRELATED function, not in the registered OWNER's "
        f"own body, so a module-scope check would wrongly classify this "
        f"away; actual unclassified={unclassified!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC15 — dead-marker rejection (M4)
# ═════════════════════════════════════════════════════════════════════════


def test_ac15_dead_marker_call_in_owner_body_is_rejected(tmp_path):
    """AC15 (M4 dead-marker rejection). Synthetic module: the owner calls
    the hazard, and the marker call sits in a statically-dead branch
    (`if False:`) INSIDE the owner's body -> the site must still be
    FLAGGED (forecloses the "just add `if False: marker()`" rubber stamp).

    Pre-GREEN FAIL: `MANIFEST_BOUNDED_WRITE_SITES` does not exist yet."""
    from bytedigger_engine.lib import mutating_git_lint  # noqa: PLC0415

    manifest_sites = getattr(mutating_git_lint, "MANIFEST_BOUNDED_WRITE_SITES", None)
    assert manifest_sites is not None, (
        "AC15: expected mutating_git_lint.MANIFEST_BOUNDED_WRITE_SITES to "
        "exist; actual attribute is absent (None)"
    )

    synthetic_root = tmp_path / "synthetic_engine_ac15"
    synthetic_root.mkdir()
    (synthetic_root / "dead_marker_module.py").write_text(
        "def _revert_cross_tree_modifications(main_repo_root, files):\n"
        "    import subprocess\n"
        "    for f in files:\n"
        "        subprocess.run(\n"
        "            ['git', '-C', str(main_repo_root), 'checkout', '--', f]\n"
        "        )\n"
        "\n"
        "\n"
        "def _maybe_emit_cross_tree_warning(result, worktree_root, main_repo_root=None, files=None):\n"
        "    if False:\n"
        "        _filter_cross_tree_to_worker_manifest(main_repo_root, files, result)\n"
        "    _revert_cross_tree_modifications(worktree_root, [])\n"
        "    return result\n",
        encoding="utf-8",
    )

    unclassified = mutating_git_lint.find_unclassified_sites(synthetic_root)

    reported = [s for s in unclassified if s.get("function") == "_revert_cross_tree_modifications"]
    assert len(reported) == 1, (
        f"AC15: expected the hazard site to be FLAGGED -- the marker call "
        f"is inside a statically-dead `if False:` branch and must NOT "
        f"count as a real guard; actual unclassified={unclassified!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC16 — result.data/metadata mirroring (M2)
# ═════════════════════════════════════════════════════════════════════════


def test_ac16_refused_files_reflected_in_result_data_and_metadata(tmp_path, monkeypatch):
    """AC16 (M2). On the AC3 fixture: `result.data["cross_tree_refused_files"]`
    == `["SHARED/state/ofi-log.jsonl"]` AND
    `result.metadata["cross_tree_refused_files"]` equals it -- the existing
    code mirrors every cross-tree key into `result.metadata`; the new key
    follows that convention.

    Pre-GREEN FAIL: today's result.data/metadata have no
    'cross_tree_refused_files' key at all."""
    main, wt = _build_repo_pair(
        tmp_path, "ac16", {LEAKED_RELPATH: "# leaked v1\n", OFI_RELPATH: '{"id":"075a725a"}\n'},
    )
    leaked_path = main / LEAKED_RELPATH
    ofi_path = main / OFI_RELPATH
    leaked_path.write_text("# polluted\n")
    ofi_path.write_bytes(ofi_path.read_bytes() + b'{"id":"bc461c1d"}\n')

    _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({
        "worker_written_paths": [str(leaked_path)], "manifest_source": "harness_tool_record",
    })

    out = phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    assert out.data.get("cross_tree_refused_files") == [OFI_RELPATH], (
        f"AC16: expected result.data['cross_tree_refused_files']=="
        f"[{OFI_RELPATH!r}]; actual "
        f"{out.data.get('cross_tree_refused_files')!r}"
    )
    assert out.metadata.get("cross_tree_refused_files") == [OFI_RELPATH], (
        f"AC16: expected result.metadata['cross_tree_refused_files']=="
        f"[{OFI_RELPATH!r}]; actual "
        f"{out.metadata.get('cross_tree_refused_files')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC17 — relative manifest entry (D2, round-3 rewrite)
# ═════════════════════════════════════════════════════════════════════════


def test_ac17_relative_manifest_entry_resolves_against_main_repo_root(tmp_path, monkeypatch):
    """AC17 (D2, round-3 rewrite). Manifest is a well-formed, all-str list
    holding the RELATIVE entry "src/leaked.py" (no non-str element anywhere)
    -> it resolves against main_repo_root, the file IS reverted, and does
    NOT appear in refused_files. A second, UNOWNED dirty file
    (src/other.py, not named in the manifest) is included so old
    unconditional-revert-everything behavior is forced to diverge (a
    single-owned-file fixture would not discriminate -- mirrors AC2's
    reasoning).

    Pre-GREEN FAIL: today's wrapper reverts EVERY detected file
    unconditionally regardless of manifest, so the unowned src/other.py is
    ALSO reverted, and 'cross_tree_revert_refused' never fires at all."""
    other_relpath = "src/other.py"
    main, wt = _build_repo_pair(
        tmp_path, "ac17", {LEAKED_RELPATH: "# leaked v1\n", other_relpath: "# other v1\n"},
    )
    leaked_path = main / LEAKED_RELPATH
    other_path = main / other_relpath
    leaked_original = leaked_path.read_bytes()
    other_original = other_path.read_bytes()
    leaked_path.write_text("# polluted\n")
    other_path.write_text("# other polluted, NOT owned\n")

    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({
        "worker_written_paths": [LEAKED_RELPATH],  # relative, all-str, owns only leaked
        "manifest_source": "harness_tool_record",
    })

    phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)

    assert leaked_path.read_bytes() == leaked_original, (
        f"AC17: expected {LEAKED_RELPATH!r} reverted via the RELATIVE "
        f"manifest entry resolved against main_repo_root; actual "
        f"{leaked_path.read_bytes()!r}"
    )
    assert other_path.read_bytes() != other_original, (
        f"AC17: expected UNOWNED {other_relpath!r} to remain dirty "
        f"(refused, not named by the manifest); actual it was reverted to "
        f"HEAD bytes {other_original!r}"
    )
    refused = [e for e in events if e["type"] == "cross_tree_revert_refused"]
    assert refused, f"AC17: expected a cross_tree_revert_refused event; actual events={events!r}"
    refused_files = refused[-1]["payload"].get("refused_files") or []
    assert other_relpath in refused_files, (
        f"AC17: expected {other_relpath!r} in refused_files; actual "
        f"{refused_files!r}"
    )
    assert LEAKED_RELPATH not in refused_files, (
        f"AC17: expected {LEAKED_RELPATH!r} NOT in refused_files (it was "
        f"owned via the relative entry); actual {refused_files!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC18 — malformed-manifest whole-rejection (round-3, replaces old AC17(c))
# ═════════════════════════════════════════════════════════════════════════


def test_ac18_nonstr_manifest_element_rejects_whole_manifest(tmp_path, monkeypatch):
    """AC18 (round-3, replaces old AC17(c)). Manifest contains a non-str
    element (e.g. 123) -> `_validate_manifest_or_raise` rejects the WHOLE
    manifest as malformed BEFORE any per-entry logic runs, so EVERY file is
    refused, `manifest_source == "unavailable:malformed"`, both files
    survive byte-identical, and `_maybe_emit_cross_tree_warning` returns
    normally (never raises).

    Pre-GREEN FAIL: today's wrapper has no manifest concept at all -- both
    files are reverted unconditionally regardless of the malformed entry,
    and 'cross_tree_revert_refused' never fires."""
    main, wt = _build_repo_pair(
        tmp_path, "ac18", {LEAKED_RELPATH: "# leaked v1\n", OFI_RELPATH: '{"id":"075a725a"}\n'},
    )
    leaked_path = main / LEAKED_RELPATH
    ofi_path = main / OFI_RELPATH
    leaked_path.write_text("# polluted\n")
    leaked_dirty = leaked_path.read_bytes()
    ofi_appended = ofi_path.read_bytes() + b'{"id":"bc461c1d"}\n'
    ofi_path.write_bytes(ofi_appended)

    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({
        # non-str element -> the WHOLE manifest is malformed, not just this entry
        "worker_written_paths": [str(leaked_path), 123],
        "manifest_source": "harness_tool_record",
    })

    try:
        out = phase_workflows_common._maybe_emit_cross_tree_warning(result, wt)
    except Exception as exc:  # pragma: no cover - exactly the failure this AC forbids
        pytest.fail(
            f"AC18: expected _maybe_emit_cross_tree_warning to return "
            f"normally (never raise) on a non-str manifest element; actual "
            f"raised {type(exc).__name__}: {exc}"
        )

    assert out is not None and out.status == "ok", (
        f"AC18: expected a normal StepResult return with status=='ok'; "
        f"actual {out!r}"
    )
    assert leaked_path.read_bytes() == leaked_dirty, (
        f"AC18: expected {LEAKED_RELPATH!r} untouched (whole manifest "
        f"rejected as malformed); actual {leaked_path.read_bytes()!r}"
    )
    assert ofi_path.read_bytes() == ofi_appended, (
        f"AC18: expected {OFI_RELPATH!r} untouched (whole manifest rejected "
        f"as malformed); actual {ofi_path.read_bytes()!r}"
    )
    refused = [e for e in events if e["type"] == "cross_tree_revert_refused"]
    assert len(refused) == 1, f"AC18: expected one cross_tree_revert_refused event; actual events={events!r}"
    payload = refused[0]["payload"]
    assert payload.get("manifest_source") == "unavailable:malformed", (
        f"AC18: expected manifest_source=='unavailable:malformed'; actual "
        f"{payload.get('manifest_source')!r}"
    )
    assert payload.get("refused_count") == 2, (
        f"AC18: expected refused_count==2; actual {payload.get('refused_count')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# AC19 — realpath forced on the MODIFIED-PATH side (mutation-testing gap)
# ═════════════════════════════════════════════════════════════════════════


def test_ac19_modified_path_side_is_realpathed_against_aliased_main_repo_root(tmp_path):
    """AC19 (mutation-testing finding): AC6 only forces `os.path.realpath`
    on the MANIFEST-ENTRY side (the alias lives in the manifest, while
    `main_repo_root` passed to the filter is already resolved). Nothing in
    AC1-AC18 forces `realpath` on the MODIFIED-PATH side -- dropping
    `os.path.realpath` from
    `os.path.join(str(main_repo_root), p)` (keeping only the join) survives
    all 18 prior ACs. This is the mirror image of AC6: alias
    `main_repo_root` itself (the unresolved symlink path), while the
    manifest holds the REAL resolved absolute path.

    Direct call to `_filter_cross_tree_to_worker_manifest` (module-level,
    per AC5) so `main_repo_root` is controlled exactly, independent of git's
    own worktree-list resolution.

    Forcing mechanism: without `os.path.realpath` on the modified-path side,
    `os.path.join(alias, "src/leaked.py")` keeps the alias prefix and never
    string-equals the REAL resolved manifest entry -> the file lands in
    `refused`, not `owned`. With `realpath` applied on both sides, they
    resolve to the identical real path -> `owned`.

    An unowned sibling file is included at no extra fixture cost, pinning
    that only the aliased path is affected."""
    fn = getattr(phase_workflows_common, "_filter_cross_tree_to_worker_manifest", None)
    assert fn is not None, (
        "AC19: expected phase_workflows_common._filter_cross_tree_to_worker_"
        "manifest to exist as a module-level function; actual attribute is "
        "absent (None)"
    )

    real_main, _wt = _build_repo_pair(
        tmp_path, "ac19", {LEAKED_RELPATH: "# leaked v1\n"},
    )

    # Inverse of AC6: alias the main_repo_root argument itself (UNRESOLVED
    # symlink path), while the manifest holds the REAL resolved path.
    alias = tmp_path / "ac19_alias"
    os.symlink(real_main, alias)
    assert os.path.realpath(str(alias)) == os.path.realpath(str(real_main)), (
        "AC19 fixture precondition: symlink alias must realpath to real_main"
    )

    real_manifest_entry = str(Path(os.path.realpath(str(real_main))) / LEAKED_RELPATH)
    other_rel = "src/other.py"
    result = _make_result({
        "worker_written_paths": [real_manifest_entry],
        "manifest_source": "harness_tool_record",
    })

    owned, refused, source = fn(alias, [LEAKED_RELPATH, other_rel], result)

    assert owned == [LEAKED_RELPATH], (
        f"AC19: expected owned==[{LEAKED_RELPATH!r}] -- the modified-path "
        f"side must be realpath'd against the ALIASED main_repo_root so it "
        f"matches the REAL manifest entry; actual owned={owned!r} "
        f"(refused={refused!r}, source={source!r})"
    )
    assert refused == [other_rel], (
        f"AC19: expected refused==[{other_rel!r}] (the unowned sibling, "
        f"unaffected by the alias); actual refused={refused!r}"
    )
