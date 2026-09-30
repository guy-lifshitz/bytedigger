"""RED tests for hal#1600 D4 — a stale hash-map key for a path the current
cycle never touched must not be classified.

Spec v3 (frozen, supersedes v2 after a second gate REJECT — one MAJOR, three
MINOR):
SHARED/memory/Decisions/2026-08-09_1600_d4_stale_hash_key_spec_v3.md
ACs 1-18 (AC1-7 from v1; AC8 respecified in v3; AC9/AC10 from v2; AC11/AC12
amended in v3; AC12b new; AC13 from v2; AC14 new; AC15 new — folds in AC17's
null-byte sibling per round 4; AC16 re-fixtured round 4 to actually
discriminate ordering; AC18 new — unusable keys named in the drop event).

Contract, fixed by spec v3 rather than left to GREEN:
``_frozen_for_current_cycle(scratchpad, git_cwd) -> dict[str, str]`` — always
returns REPO-RELATIVE keys, whatever spelling either artifact used, resolved
via realpath (§1j) so a symlinked/aliased root cannot make a current path
look stale. Two telemetry events — ``red_freeze_stale_key_dropped``
(ordinary narrowing, ONE event per call, payload carries total dropped count
+ first 20 names) and ``red_freeze_disjoint_fallback`` (both inputs
non-empty, intersection empty -> no filtering; payload carries reason +
both input counts) — emitted at BOTH entry paths (§1ab).

Conventions (§1q / D1CF5FDF collectability guard, 81F97F3D no module-level
sys.path mutation): ``phase_5_implement`` and ``lib.authored_boundary``
already exist today, so they are imported at module top level (collectable,
mirrors test_gh639/test_gh921's import header exactly). The NEW symbol this
ship adds, ``phase_5_implement._frozen_for_current_cycle``, does NOT exist
yet, so it is probed via ``getattr(...)`` INSIDE each test body via
``_require``. The file COLLECTS and each test FAILS at assert time, never at
collection time.

Stub-passability (§1l/7AD3D393): UUT symbols (``_red_baseline_precheck``,
``_commit_green_code``, ``_read_red_test_hashes``, ``_frozen_for_current_cycle``)
are called directly, never patched/mocked.

§1u two entry paths: AC1/AC2/AC3/AC4/AC6 drive ``_red_baseline_precheck``
directly (it takes ``scratchpad, git_cwd, spec_path`` with no ctx scaffolding
needed); AC5 drives the same stale-key state through ``_commit_green_code``
(full ctx/prev, mirrors test_7C0FDE44's AC10 fixture style).

§1l AC6 is the production side-effect anchor: measured on the raw bytes of
``red-test-hashes.json`` on disk, not on a return value, per the spec's
PREFLIGHT correction (the draft's chokepoint would have pruned the durable
map on the sanctioned-refresh write path).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.lib import authored_boundary as boundary_mod  # noqa: E402
import test_gh960_preexisting_tamper_downgrade as gh960_downgrade_mod  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers (mirrors test_7C0FDE44_gh639_red_hash_freeze.py) ──────────


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
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _write_paths_file(scratchpad: Path, paths: "list[str] | None", *, blank: bool = False) -> None:
    """Write red-test-paths.txt directly (bypasses _persist_red_test_paths,
    which refuses an empty write — AC4 needs an on-disk empty/whitespace file,
    which is corruption per the spec, not "no file")."""
    ref_path = scratchpad / phase_5_implement.RED_TEST_PATHS_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    if blank:
        ref_path.write_text("   \n\t\n  ")
    else:
        ref_path.write_text("\n".join(paths or []))


def _write_hashes_file(scratchpad: Path, manifest: dict) -> None:
    ref_path = scratchpad / phase_5_implement.RED_TEST_HASHES_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(json.dumps(manifest, sort_keys=True))


def _make_green_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Fix the thing", session_id="gh1600-d4-green", persona="hal",
        framework=None, domain=None,
    )


def _require(mod, name: str):
    """Presence-gate for a not-yet-existing symbol (D1CF5FDF collectability)."""
    fn = getattr(mod, name, None)
    assert fn is not None, (
        f"{mod.__name__}.{name} does not exist yet (hal#1600 D4 not implemented) — expected RED fail"
    )
    return fn


# ═══════════════════════════════════════════════════════════════════════════
# TestGH1600D4StaleHashKeyReconcile
# ═══════════════════════════════════════════════════════════════════════════


class TestGH1600D4StaleHashKeyReconcile:
    """hal#1600 D4 — stale hash-map key not filtered by current cycle's
    red-test-paths.txt before classification (ACs 1-8)."""

    # ── AC1: mismatch on a path outside the current cycle -> no error ──────

    def test_ac1_stale_mismatch_outside_current_cycle_produces_no_error(
        self, tmp_path: Path
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")
        digest_a = boundary_mod.compute_red_test_hashes(["tests/test_a.py"], str(repo))["tests/test_a.py"]

        frozen = {"tests/test_a.py": digest_a, "tests/test_b.py": "stale-b-digest"}
        _write_hashes_file(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is None, (
            f"tests/test_b.py is not in the current cycle's red-test-paths.txt and must not be "
            f"classified; got a StepResult instead of None: "
            f"{getattr(result, 'error', None)!r} / {getattr(result, 'error_code', None)!r}"
        )

    # ── AC2: mismatch on a path INSIDE the current cycle still errors ──────

    def test_ac2_mismatch_on_current_cycle_path_still_errors_naming_it(
        self, tmp_path: Path
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")

        frozen = {"tests/test_a.py": "stale-a-digest", "tests/test_b.py": "stale-b-digest"}
        _write_hashes_file(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is not None and result.status == "error", (
            "a real mismatch on a path IN the current cycle must still fire"
        )
        assert "tests/test_a.py" in result.error
        assert "tests/test_b.py" not in result.error, (
            f"tests/test_b.py is outside the current cycle and must be excluded from the "
            f"classification input entirely, got: {result.error!r}"
        )

    # ── AC3: red-test-paths.txt MISSING -> fail-open, byte-identical to today ──

    def test_ac3_missing_paths_file_is_fail_open_both_paths_classified(
        self, tmp_path: Path
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")

        frozen = {"tests/test_a.py": "stale-a-digest", "tests/test_b.py": "stale-b-digest"}
        _write_hashes_file(scratchpad, frozen)
        # No red-test-paths.txt written at all.
        assert not (scratchpad / phase_5_implement.RED_TEST_PATHS_RELPATH).exists()

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is not None and result.status == "error"
        assert "tests/test_a.py" in result.error
        assert "tests/test_b.py" in result.error, (
            "an absent paths file must not silently disarm the freeze — every frozen key "
            "stays classified, exactly like before this fix"
        )

    # ── AC4: red-test-paths.txt empty/whitespace -> fail-open, same as AC3 ──

    def test_ac4_empty_paths_file_is_fail_open_both_paths_classified(
        self, tmp_path: Path
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")

        frozen = {"tests/test_a.py": "stale-a-digest", "tests/test_b.py": "stale-b-digest"}
        _write_hashes_file(scratchpad, frozen)
        _write_paths_file(scratchpad, None, blank=True)
        assert (scratchpad / phase_5_implement.RED_TEST_PATHS_RELPATH).exists()

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is not None and result.status == "error"
        assert "tests/test_a.py" in result.error
        assert "tests/test_b.py" in result.error, (
            "an empty/whitespace-only paths file can only mean corruption (the persist helper "
            "refuses to write an empty list) and must never be read as 'no path is a RED test "
            "path' — that would silently disable the freeze"
        )

    # ── AC5: second entry path — _commit_green_code sees the same fix ──────

    def test_ac5_commit_green_code_also_excludes_stale_key_from_classification(
        self, tmp_path: Path
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")
        red_sha = _commit_file(
            repo, "tests/test_b.py", "def test_b(): assert True\n", "RED: add test_b"
        )
        digest_a = boundary_mod.compute_red_test_hashes(["tests/test_a.py"], str(repo))["tests/test_a.py"]

        # A is clean (correct digest); B carries a stale digest that does NOT
        # reflect any real edit — B was simply never touched this cycle.
        frozen = {"tests/test_a.py": digest_a, "tests/test_b.py": "stale-b-digest"}
        phase_5_implement._persist_red_test_hashes(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        spec_path = scratchpad / "spec.md"
        spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = StepResult(
            status="ok",
            data={
                "cycle": 1, "red_commit_sha": red_sha,
                "worker_written_paths": ["src/module.py"], "manifest_source": "harness_tool_record",
                "spec_path": str(spec_path),
            },
            duration_ms=0, step_name="prev",
        )

        result = phase_5_implement._commit_green_code(ctx, prev)
        # D2 rename: the code this negative assertion names must be the CURRENT
        # one. Left on the retired pre-D2 name it would pass for the wrong
        # reason — no such code is registered any more — and stop guarding D4.
        assert result.error_code != "E_RED_BASELINE_FILE_MODIFIED", (
            f"tests/test_b.py is outside the current cycle's red-test-paths.txt and must not "
            f"block the commit; got status={result.status!r} error_code={result.error_code!r} "
            f"error={getattr(result, 'error', None)!r}"
        )
        assert result.status == "ok"

    # ── AC6 (§1l side-effect anchor): durable map is not pruned by a refresh ──

    def test_ac6_sanctioned_refresh_does_not_prune_stale_key_from_disk(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        # A: operator committed a strengthened version at HEAD (head_moved).
        _commit_file(
            repo, "tests/test_a.py",
            "def test_a(): assert True  # strengthened\n",
            "RED: strengthen test_a",
        )
        # B: frozen but never created on disk in this cycle at all, and
        # outside red-test-paths.txt — the stale key this ship targets.
        frozen = {"tests/test_a.py": "stale-digest-from-prior-cycle", "tests/test_b.py": "b-stale-digest"}
        phase_5_implement._persist_red_test_hashes(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])
        monkeypatch.setenv("HAL_RED_BASELINE_REFRESH", "1")

        ref_path = scratchpad / phase_5_implement.RED_TEST_HASHES_RELPATH
        bytes_before = ref_path.read_bytes()
        assert json.loads(bytes_before)["tests/test_b.py"] == "b-stale-digest"

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is None, (
            f"tests/test_a.py alone is all_head_moved and the refresh flag is set; the stale, "
            f"out-of-cycle tests/test_b.py key must not block (nor be required to match) the "
            f"refresh. Got: {getattr(result, 'error', None)!r} / "
            f"{getattr(result, 'error_code', None)!r}"
        )

        bytes_after = ref_path.read_bytes()
        manifest_after = json.loads(bytes_after)
        assert "tests/test_b.py" in manifest_after, (
            "the durable red-test-hashes.json map must NOT be pruned by a sanctioned refresh "
            "that only recomputed the current cycle's paths — the draft's chokepoint (filtering "
            "inside the read used for the merge/write) would have dropped this key entirely"
        )
        assert manifest_after["tests/test_b.py"] == "b-stale-digest", (
            "B was never part of this cycle's classification, so its digest in the durable map "
            "must be left untouched by the refresh write"
        )

    # ── AC7: _read_red_test_hashes itself stays UNFILTERED ──────────────────

    def test_ac7_read_red_test_hashes_is_not_filtered_by_current_paths(
        self, tmp_path: Path
    ) -> None:
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        frozen = {"tests/test_a.py": "digest-a", "tests/test_b.py": "digest-b"}
        _write_hashes_file(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        result = phase_5_implement._read_red_test_hashes(scratchpad)
        assert result == frozen, (
            "_read_red_test_hashes is the durable read that write paths and resume rely on — "
            "it must stay unfiltered even though a current-cycle paths file exists; the "
            "reconciled read is a SEPARATE new helper, not a change to this one "
            f"(got {result!r})"
        )

    # ── AC8 (respecified v3): pins the OUTPUT spelling, not just survival ──

    def test_ac8_path_normalisation_pins_returned_key_as_repo_relative(
        self, tmp_path: Path
    ) -> None:
        """F3 MAJOR (v3): v2's AC8 asserted only len()+digest presence, so a
        GREEN returning the key still in ABSOLUTE form would pass — and that
        absolute key then flows into classify_red_hash_mismatches, whose
        `git show HEAD:<path>` rejects an absolute path, firing the
        fail-CLOSED head_unreadable leg: a spurious tamper on the very path
        the helper just declared current. The returned key SET is pinned
        exactly, in both spelling directions."""
        fn = _require(phase_5_implement, "_frozen_for_current_cycle")
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "init a")
        _commit_file(repo, "tests/test_b.py", "def test_b(): assert True\n", "init b")

        # Variant A: hashes-map key is ABSOLUTE, paths.txt entry is repo-relative.
        scratch_a = tmp_path / "scratch_a"
        scratch_a.mkdir()
        abs_a = str((repo / "tests" / "test_a.py").resolve())
        _write_hashes_file(scratch_a, {abs_a: "digest-a"})
        _write_paths_file(scratch_a, ["tests/test_a.py"])
        result_a = fn(scratch_a, str(repo))
        assert set(result_a) == {"tests/test_a.py"}, (
            f"the reconciled read must always return REPO-RELATIVE keys — reconciling and "
            f"re-spelling are one operation, not two. A key returned still in absolute form "
            f"would later be rejected by `git show HEAD:<path>`, reintroducing a spurious "
            f"tamper on a path just declared current. Length/digest checks are not enough; "
            f"got key set {set(result_a)!r}"
        )
        assert result_a["tests/test_a.py"] == "digest-a"

        # Variant B: the spelling difference on the OTHER side.
        scratch_b = tmp_path / "scratch_b"
        scratch_b.mkdir()
        abs_b = str((repo / "tests" / "test_b.py").resolve())
        _write_hashes_file(scratch_b, {"tests/test_b.py": "digest-b"})
        _write_paths_file(scratch_b, [abs_b])
        result_b = fn(scratch_b, str(repo))
        assert set(result_b) == {"tests/test_b.py"}, (
            f"the same must hold in reverse — a repo-relative hashes-map key combined with an "
            f"ABSOLUTE paths.txt spelling must still return the REPO-RELATIVE key, not the "
            f"absolute one; got key set {set(result_b)!r}"
        )
        assert result_b["tests/test_b.py"] == "digest-b"

    # ── AC14 (new, §1j): realpath resolution across a symlinked root ───────

    def test_ac14_realpath_resolution_survives_symlinked_root_spelling(
        self, tmp_path: Path
    ) -> None:
        """A bare os.path.relpath(p, git_cwd) GREEN must fail here: the
        hashes-map key is spelled through a symlinked alias of the repo root
        (unresolved), while red-test-paths.txt holds the plain repo-relative
        spelling computed against the REAL (resolved) root — mirroring the
        macOS /tmp -> /private/tmp split without depending on host-specific
        tmpdir behaviour."""
        fn = _require(phase_5_implement, "_frozen_for_current_cycle")
        real_root = (tmp_path / "real_repo").resolve()
        _init_repo(real_root)
        _commit_file(real_root, "tests/test_a.py", "def test_a(): assert True\n", "init")

        alias_root = tmp_path / "alias_repo"
        alias_root.symlink_to(real_root, target_is_directory=True)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        # Unresolved: goes through the symlink, never realpath'd.
        unresolved_key = str(alias_root / "tests" / "test_a.py")
        _write_hashes_file(scratchpad, {unresolved_key: "digest-a"})
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        result = fn(scratchpad, str(real_root))
        assert set(result) == {"tests/test_a.py"}, (
            f"a bare os.path.relpath(p, git_cwd) without realpath resolution on both sides "
            f"would compute a different (symlink-relative) spelling for the unresolved key "
            f"and drop this current path as stale; got {result!r}"
        )

    # ── AC9 (F1): red_freeze_paths still sees the UNFILTERED map ───────────

    def test_ac9_red_freeze_paths_still_carries_unfiltered_map_including_stale_key(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")
        digest_a = boundary_mod.compute_red_test_hashes(["tests/test_a.py"], str(repo))["tests/test_a.py"]

        frozen = {"tests/test_a.py": digest_a, "tests/test_b.py": "stale-b-digest"}
        phase_5_implement._persist_red_test_hashes(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        captured_calls: list[dict] = []

        def _stub_scan_boundary(boundary, *, base_sha, paths, git_cwd, is_test_path,
                                 list_changed_since=None, forbidden_new_tokens=None,
                                 authorized_test_edits=None, red_freeze_paths=None):
            captured_calls.append({"red_freeze_paths": red_freeze_paths})
            return boundary_mod.BoundaryScanResult(suppression_hits=[], tampered_tests=[])

        monkeypatch.setattr(boundary_mod, "scan_boundary", _stub_scan_boundary)

        spec_path = scratchpad / "spec.md"
        spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = StepResult(
            status="ok",
            data={
                "cycle": 1, "red_commit_sha": red_sha,
                "worker_written_paths": ["src/module.py"], "manifest_source": "harness_tool_record",
                "spec_path": str(spec_path),
            },
            duration_ms=0, step_name="prev",
        )

        phase_5_implement._commit_green_code(ctx, prev)

        assert len(captured_calls) == 1, "scan_boundary must be called exactly once"
        red_freeze_paths = captured_calls[0]["red_freeze_paths"]
        assert red_freeze_paths is not None
        assert set(red_freeze_paths) == {"tests/test_a.py", "tests/test_b.py"}, (
            f"the fix must NOT shrink scan_boundary's red_freeze_paths coverage — it must still "
            f"see the unfiltered map (including the stale key), got {red_freeze_paths!r}"
        )

    # ── AC10 (F1): sibling gate-block pin must still pass ───────────────────

    def test_ac10_gh960_sibling_gate_block_pin_still_passes(self) -> None:
        """Runs (not inspects) test_gh960_preexisting_tamper_downgrade's own
        AC8, which pins the commit_green_code gate block (sliced between
        'GH373 Part A' and 'GH639') to contain exactly ONE
        _read_red_test_hashes( call. The reconciled read must be introduced
        INSIDE the `if _frozen:` leg, past that slice boundary — this fails
        if the fix instead switches the call at :7326."""
        sibling = gh960_downgrade_mod.TestGH960PreexistingTamperDowngrade()
        sibling.test_ac8_commit_green_code_wires_red_freeze_paths()

    # ── AC11 (F2): disjoint non-empty paths file fails OPEN, loudly ────────

    def test_ac11_disjoint_nonempty_paths_file_falls_back_to_unfiltered_and_emits(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")

        frozen = {"tests/test_a.py": "stale-a-digest"}
        _write_hashes_file(scratchpad, frozen)
        # Non-empty, but shares NO key with the map (e.g. RED persisted new
        # paths before the hashes were refrozen).
        _write_paths_file(scratchpad, ["tests/completely_different.py"])

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is not None and result.status == "error", (
            "a disjoint non-empty paths file must fall back to the unfiltered map — the "
            "current cycle's real RED tests must never lose protection because of a "
            "resume-window mismatch between the two artifacts"
        )
        assert "tests/test_a.py" in result.error, (
            "fail-open: the stale key IS still classified, exactly like before this fix"
        )

        disjoint_events = [e for e in captured if e["type"] == "red_freeze_disjoint_fallback"]
        assert len(disjoint_events) == 1, (
            f"the disagreement between the two artifacts must be observable, not silent; "
            f"got {captured!r}"
        )
        payload = disjoint_events[0]["payload"]
        assert payload.get("reason") == "disjoint", (
            f"the payload must name WHY this is a fallback, not just fire an empty event that "
            f"an operator cannot tell apart from a real tamper; got {payload!r}"
        )
        assert payload.get("n_frozen") == 1, "must count the frozen map's keys"
        assert payload.get("n_paths") == 1, "must count the paths-file's entries"

    # ── AC12 (amended v3): ONE event per call, capped names, dropped count ──

    def test_ac12_ordinary_filtering_emits_exactly_one_event_naming_dropped_keys(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A large stale map must not emit one event PER key (unbounded) —
        exactly one event per call, payload carries the total dropped count
        and at most the first 20 names."""
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")
        digest_a = boundary_mod.compute_red_test_hashes(["tests/test_a.py"], str(repo))["tests/test_a.py"]

        frozen = {"tests/test_a.py": digest_a}
        for i in range(25):
            frozen[f"tests/stale_{i:02d}.py"] = f"stale-digest-{i}"
        _write_hashes_file(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is None

        dropped_events = [e for e in captured if e["type"] == "red_freeze_stale_key_dropped"]
        assert len(dropped_events) == 1, (
            f"exactly ONE event per call, not one per dropped key (25 stale keys here); "
            f"got {len(dropped_events)}: {captured!r}"
        )
        payload = dropped_events[0]["payload"]
        assert payload.get("n_dropped") == 25, f"must carry the TOTAL dropped count; got {payload!r}"
        names = payload.get("dropped", [])
        assert len(names) == 20, f"must cap the listed names at 20; got {len(names)}: {names!r}"
        assert "tests/stale_00.py" in names, "must actually NAME a dropped key, not just count"

    # ── AC12b (new, §1ab): the drop event fires at the second entry path too ──

    def test_ac12b_commit_green_code_also_emits_stale_key_dropped_event(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")
        red_sha = _commit_file(
            repo, "tests/test_b.py", "def test_b(): assert True\n", "RED: add test_b"
        )
        digest_a = boundary_mod.compute_red_test_hashes(["tests/test_a.py"], str(repo))["tests/test_a.py"]

        frozen = {"tests/test_a.py": digest_a, "tests/test_b.py": "stale-b-digest"}
        phase_5_implement._persist_red_test_hashes(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        spec_path = scratchpad / "spec.md"
        spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = StepResult(
            status="ok",
            data={
                "cycle": 1, "red_commit_sha": red_sha,
                "worker_written_paths": ["src/module.py"], "manifest_source": "harness_tool_record",
                "spec_path": str(spec_path),
            },
            duration_ms=0, step_name="prev",
        )

        result = phase_5_implement._commit_green_code(ctx, prev)
        assert result.status == "ok"

        dropped_events = [e for e in captured if e["type"] == "red_freeze_stale_key_dropped"]
        assert len(dropped_events) == 1, (
            f"the second entry path (_commit_green_code) must ALSO emit the drop event, not "
            f"just _red_baseline_precheck; got {captured!r}"
        )
        assert "tests/test_b.py" in dropped_events[0]["payload"].get("dropped", [])

    # ── AC15 (F4, round 3/4): unusable keys must not raise, incl. the ──────
    # ── AC17 null-byte sibling ─────────────────────────────────────────────

    def test_ac15_key_resolving_outside_git_cwd_is_dropped_not_raised(
        self, tmp_path: Path
    ) -> None:
        """`Path(k).resolve().relative_to(Path(git_cwd).resolve())` raises
        ValueError for a key outside git_cwd. Every neighbour in this family
        documents never-raises, because an exception here aborts the step
        instead of failing the gate — worse than the spurious tamper being
        fixed. A scratchpad reused across worktree roots (or any stray
        absolute key from elsewhere) must be dropped silently, not explode.

        AC17 (round-4 condition 2, folded into this fixture per the gate's
        'your call'): `if not Path(k).resolve().is_relative_to(root): continue`
        is a natural AC15-satisfying shape with no try/except — and it STILL
        crashes on an embedded null byte, because `Path(k).resolve()` raises
        ValueError on the null byte itself, before any out-of-root check.
        `"tests/\\x00bad.py"` closes that class in the same map."""
        fn = _require(phase_5_implement, "_frozen_for_current_cycle")
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "init")

        outside_dir = (tmp_path / "other_worktree").resolve()
        outside_dir.mkdir()
        outside_key = str(outside_dir / "tests" / "test_x.py")
        null_byte_key = "tests/\x00bad.py"

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _write_hashes_file(scratchpad, {outside_key: "digest-outside", null_byte_key: "digest-null"})
        _write_paths_file(scratchpad, ["tests/test_x.py"])

        result = fn(scratchpad, str(repo))
        assert result == {}, (
            f"a map key resolving OUTSIDE git_cwd, and one containing a null byte, must both "
            f"simply be absent from the result — never raise and never appear; got {result!r}"
        )

    # ── AC16 (re-fixtured, round 4): counts are POST-drop, order discriminates ──

    def test_ac16_disjoint_fallback_counts_are_post_drop_not_raw(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Round-4 gate: the original AC16 fixture (out-of-root key + valid
        current key) INTERSECTED with the paths file, so the fallback
        precondition was unmet under either ordering — it didn't discriminate.
        This fixture is genuinely disjoint AFTER dropping the unusable key:
        map = {<out-of-root>, tests/test_b.py: stale}, paths = [test_c.py].
        Drop-first returns only test_b and fires ONE fallback event with
        n_frozen == 1 (post-drop); fallback-first would return BOTH keys."""
        fn = _require(phase_5_implement, "_frozen_for_current_cycle")
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "tests/test_b.py", "def test_b(): assert True\n", "init")

        outside_dir = (tmp_path / "other_worktree").resolve()
        outside_dir.mkdir()
        outside_key = str(outside_dir / "tests" / "test_x.py")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _write_hashes_file(scratchpad, {outside_key: "digest-outside", "tests/test_b.py": "stale-b-digest"})
        _write_paths_file(scratchpad, ["tests/test_c.py"])  # shares nothing with either key

        result = fn(scratchpad, str(repo))
        assert result == {"tests/test_b.py": "stale-b-digest"}, (
            f"drop-first: the out-of-root key is removed before reconciliation, leaving only "
            f"tests/test_b.py for the (disjoint) fallback to return unfiltered; a "
            f"fallback-first GREEN would instead return BOTH keys; got {result!r}"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )
        precheck_result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert precheck_result is not None and precheck_result.status == "error", (
            "tests/test_b.py is genuinely stale after the drop and must still be classified "
            "under the disjoint fail-open rule"
        )
        fallback_events = [e for e in captured if e["type"] == "red_freeze_disjoint_fallback"]
        assert len(fallback_events) == 1
        assert fallback_events[0]["payload"].get("n_frozen") == 1, (
            f"n_frozen must be the POST-drop count (1: just tests/test_b.py), not the raw "
            f"pre-drop count (2); got {fallback_events[0]['payload']!r}"
        )

    # ── AC18 (round 4 condition 3): unusable keys are named, not silent ────

    def test_ac18_stale_key_dropped_event_also_names_unusable_keys(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """An unusable (out-of-root) key narrows the gate exactly as an
        ordinary stale key does, and additionally signals a contaminated
        scratchpad — silence about it contradicts AC12's no-silent-narrowing
        principle. No new event: the existing red_freeze_stale_key_dropped
        payload gains n_unusable and a capped unusable list."""
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")
        digest_a = boundary_mod.compute_red_test_hashes(["tests/test_a.py"], str(repo))["tests/test_a.py"]

        outside_dir = (tmp_path / "other_worktree").resolve()
        outside_dir.mkdir()
        outside_key = str(outside_dir / "tests" / "test_u.py")

        frozen = {
            "tests/test_a.py": digest_a,
            "tests/test_b.py": "stale-b-digest",
            outside_key: "digest-outside",
        }
        _write_hashes_file(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is None

        dropped_events = [e for e in captured if e["type"] == "red_freeze_stale_key_dropped"]
        assert len(dropped_events) == 1, (
            "still exactly ONE event per call — the unusable key does not get its own event"
        )
        payload = dropped_events[0]["payload"]
        assert payload.get("n_dropped") == 1 and payload.get("dropped") == ["tests/test_b.py"], (
            f"the ordinary stale-key count/list must not be contaminated by the unusable one; "
            f"got {payload!r}"
        )
        assert payload.get("n_unusable") == 1, (
            f"an unusable key must be counted separately, not silently absorbed; got {payload!r}"
        )
        assert outside_key in payload.get("unusable", []), (
            f"the unusable key must be NAMED, not just counted; got {payload!r}"
        )

    # ── whitespace-padded entry (v3 MINOR): normalised, not treated as stale ──

    def test_ac_whitespace_padded_paths_entry_is_normalised_not_disjoint(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """_read_red_test_paths filters purely-blank lines but does NOT strip
        surviving ones (despite its docstring claim). A line like
        '  tests/test_a.py  ' is real content, padded — the reconciled read
        must strip it before matching, or an accidental padding makes a
        genuinely-current path look disjoint from the map."""
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "RED: add test_a")
        digest_a = boundary_mod.compute_red_test_hashes(["tests/test_a.py"], str(repo))["tests/test_a.py"]

        frozen = {"tests/test_a.py": digest_a, "tests/test_b.py": "stale-b-digest"}
        _write_hashes_file(scratchpad, frozen)
        ref_path = scratchpad / phase_5_implement.RED_TEST_PATHS_RELPATH
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text("  tests/test_a.py  \n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        result = phase_5_implement._red_baseline_precheck(scratchpad, str(repo), None)
        assert result is None, (
            "a whitespace-padded but otherwise-valid current-cycle path must still match the "
            "map key after normalisation — padding is not a genuinely different (stale) path"
        )

        disjoint_events = [e for e in captured if e["type"] == "red_freeze_disjoint_fallback"]
        assert len(disjoint_events) == 0, (
            "padding must not be mistaken for a disjoint artifact — this is ordinary filtering "
            "(only B is dropped), not the disjoint fallback"
        )

    # ── AC13 (MINOR): the reconciled read performs no writes ───────────────

    def test_ac13_frozen_for_current_cycle_performs_no_writes(self, tmp_path: Path) -> None:
        fn = _require(phase_5_implement, "_frozen_for_current_cycle")
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "tests/test_a.py", "def test_a(): assert True\n", "init")

        frozen = {"tests/test_a.py": "digest-a", "tests/test_b.py": "digest-b"}
        _write_hashes_file(scratchpad, frozen)
        _write_paths_file(scratchpad, ["tests/test_a.py"])

        hashes_path = scratchpad / phase_5_implement.RED_TEST_HASHES_RELPATH
        paths_path = scratchpad / phase_5_implement.RED_TEST_PATHS_RELPATH
        hashes_before = hashes_path.read_bytes()
        paths_before = paths_path.read_bytes()

        fn(scratchpad, str(repo))

        assert hashes_path.read_bytes() == hashes_before, (
            "_frozen_for_current_cycle must not write red-test-hashes.json — AC6/AC7 close the "
            "two write paths known today, this closes the class for any future one"
        )
        assert paths_path.read_bytes() == paths_before, (
            "_frozen_for_current_cycle must not write red-test-paths.txt either"
        )
