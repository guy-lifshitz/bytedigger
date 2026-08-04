"""RED tests for 1DA29C33 — GH276 step-2: per-run spec-§5 `run_allowlist` writer.

Agreement: 1DA29C33 (GH #276, step 2 of 3)
Spec: SHARED/memory/Decisions/2026-07-06_1DA29C33_gh276_step2_run_allowlist_writer_spec.md

Contract: a new module `lib.run_allowlist` provides `parse_spec_files_allowlist`,
`resolve_zones_config_path`, `update_run_allowlist`, `remove_run_allowlist`,
`write_run_allowlist_for_spec`. GREEN wires `write_run_allowlist_for_spec` into
`phase_45_spec._gate_on_review` / `phase_45_spec_lite._gate_on_review` SHIP
branches, adds `phase_8_post_deploy._cleanup_run_allowlist` (registered in
`phase_8_post_deploy_workflow()`), and re-exports
`phase_5_implement._parse_spec_files_allowlist` from the new module.

D1CF5FDF: all not-yet-existing symbols are imported INSIDE each test body so the
file collects cleanly today (module + symbols genuinely absent — verified by
`grep -r run_allowlist SYSTEM/cli/build/engine_py/lib/` = 0 hits pre-GREEN).
Conftest already inserts ENGINE_ROOT + ENGINE_ROOT/workflows onto sys.path
(§1q singleton) — no module-level sys.path manipulation here.
"""
from __future__ import annotations

import json
from pathlib import Path

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── fixtures / helpers ────────────────────────────────────────────────────


def _ctx(org_config: dict) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question="run_allowlist writer RED",
        session_id="test-1DA29C33",
        persona="hal",
        framework=None,
        domain=None,
    )


def _ship_prev(spec_path: str, review_path: str = "review.md", cycle: int = 1) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "verdict": "SHIP",
            "review_path": review_path,
            "spec_path": spec_path,
            "cycle": cycle,
            "review_raw": "",
        },
        duration_ms=0,
        step_name="write_review_doc",
    )


def _step1_config(**overrides) -> dict:
    cfg = {
        "version": 1,
        "enforce": False,
        "_enforce_note": "n",
        "zones": {"allow": ["~/.claude"], "deny": []},
        "run_allowlist": [],
        "x": "keepme",
    }
    cfg.update(overrides)
    return cfg


def _write_config(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg), encoding="utf-8")


_SPEC_WITH_FILES = """\
# Test Spec

## Files
- CREATE a.py
- MODIFY `b/c.py` (x)
- d.md
- MODIFY d2.py (why)

## §3 Acceptance Criteria
"""

_SPEC_WITH_SCOPE_HEADER = """\
# Test Spec

## §5 — Files in scope
- e.py
- f/g.py

## §3 Acceptance Criteria
"""

_SPEC_WITH_NEITHER = """\
# Test Spec

## §3 Acceptance Criteria
No files section here.
"""

_SPEC_WITH_BOTH_SECTIONS = """\
# Test Spec

## Files
- CREATE p1.py

## §5 — Files in scope
- CREATE p2.py

## §3 Acceptance Criteria
"""


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — parse_spec_files_allowlist: ## Files primary grammar
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_parse_spec_files_allowlist_primary_grammar(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import parse_spec_files_allowlist

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(_SPEC_WITH_FILES, encoding="utf-8")

    result = parse_spec_files_allowlist(str(spec_path))

    assert result == ["a.py", "b/c.py", "d.md", "d2.py"], (
        f"AC1 FAIL: expected verb+backtick-stripped ordered list (backtick-wrapped "
        f"'b/c.py' keeps backtick contents and discards trailing annotation; plain "
        f"'d2.py' strips trailing parenthetical too), got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# A3 — ## Files wins over ## §5 — Files in scope when BOTH sections present
# ═══════════════════════════════════════════════════════════════════════════


def test_files_section_wins_over_scope_header_fallback(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import parse_spec_files_allowlist

    spec_path = tmp_path / "spec-both.md"
    spec_path.write_text(_SPEC_WITH_BOTH_SECTIONS, encoding="utf-8")

    result = parse_spec_files_allowlist(str(spec_path))

    assert result == ["p1.py"], (
        f"A3 FAIL: primary '## Files' grammar must win when both sections present, "
        f"got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — fallback "files in scope" header + neither-present + missing file
# ═══════════════════════════════════════════════════════════════════════════


def test_ac2_parse_spec_files_allowlist_fallback_and_none_cases(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import parse_spec_files_allowlist

    fallback_spec = tmp_path / "fallback.md"
    fallback_spec.write_text(_SPEC_WITH_SCOPE_HEADER, encoding="utf-8")
    result_fallback = parse_spec_files_allowlist(str(fallback_spec))
    assert result_fallback == ["e.py", "f/g.py"], (
        f"AC2 FAIL: fallback 'files in scope' header not parsed, got {result_fallback!r}"
    )

    neither_spec = tmp_path / "neither.md"
    neither_spec.write_text(_SPEC_WITH_NEITHER, encoding="utf-8")
    result_neither = parse_spec_files_allowlist(str(neither_spec))
    assert result_neither is None, (
        f"AC2 FAIL: spec with neither section should return None, got {result_neither!r}"
    )

    result_missing = parse_spec_files_allowlist(str(tmp_path / "does-not-exist.md"))
    assert result_missing is None, (
        f"AC2 FAIL: missing file should return None, got {result_missing!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — resolve_zones_config_path precedence
# ═══════════════════════════════════════════════════════════════════════════


def test_ac3_resolve_zones_config_path_precedence(tmp_path: Path, monkeypatch) -> None:
    from bytedigger_engine.lib.run_allowlist import resolve_zones_config_path

    monkeypatch.delenv("HAL_SCOPE_ZONES_CONFIG", raising=False)

    cfg_path_win = tmp_path / "cfg-explicit.json"
    env_path = tmp_path / "cfg-env.json"

    # cfg key wins over env
    monkeypatch.setenv("HAL_SCOPE_ZONES_CONFIG", str(env_path))
    result_cfg_wins = resolve_zones_config_path({"scope_zones_config": str(cfg_path_win)})
    assert result_cfg_wins == cfg_path_win or str(result_cfg_wins) == str(cfg_path_win), (
        f"AC3 FAIL: cfg key must win over env, got {result_cfg_wins!r}"
    )

    # env wins over hal-default when cfg key absent
    result_env_wins = resolve_zones_config_path({"hal": True, "hal_dir": str(tmp_path)})
    assert str(result_env_wins) == str(env_path), (
        f"AC3 FAIL: env must win over hal-default, got {result_env_wins!r}"
    )

    # env unset, hal:True + hal_dir → hal-default path
    monkeypatch.delenv("HAL_SCOPE_ZONES_CONFIG", raising=False)
    result_hal_default = resolve_zones_config_path({"hal": True, "hal_dir": str(tmp_path)})
    expected_hal_default = tmp_path / "SHARED" / "config" / "containment-zones.json"
    assert str(result_hal_default) == str(expected_hal_default), (
        f"AC3 FAIL: hal-default path wrong, got {result_hal_default!r}, "
        f"expected {expected_hal_default!r}"
    )

    # bare {} (no env, no hal marker) → None
    result_none = resolve_zones_config_path({})
    assert result_none is None, (
        f"AC3 FAIL: bare cfg with no env/hal marker must return None, got {result_none!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — update_run_allowlist: shape + preservation
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4_update_run_allowlist_shape_and_key_preservation(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import update_run_allowlist

    cfg_path = tmp_path / "containment-zones.json"
    _write_config(cfg_path, _step1_config())

    result = update_run_allowlist(
        cfg_path, "run1", ["/a/b.py", "/a/c.py"], now_iso="2026-07-03T01:00:00Z"
    )

    assert result["status"] == "written", f"AC4 FAIL: expected status written, got {result!r}"

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["run_allowlist"] == sorted(["/a/b.py", "/a/c.py"]), (
        f"AC4 FAIL: run_allowlist not sorted union, got {on_disk.get('run_allowlist')!r}"
    )
    assert all(isinstance(v, str) for v in on_disk["run_allowlist"]), (
        "AC4 FAIL: run_allowlist values must all be strings"
    )
    assert on_disk["run_allowlist_meta"]["run1"] == {
        "ts": "2026-07-03T01:00:00Z",
        "entries": ["/a/b.py", "/a/c.py"],
    }, f"AC4 FAIL: meta entry shape wrong, got {on_disk.get('run_allowlist_meta')!r}"
    assert on_disk["version"] == 1
    assert on_disk["enforce"] is False
    assert on_disk["zones"] == {"allow": ["~/.claude"], "deny": []}
    assert on_disk["x"] == "keepme", "AC4 FAIL: unknown key 'x' must be preserved exactly"


# ═══════════════════════════════════════════════════════════════════════════
# A4 — config valid JSON but missing both run_allowlist/run_allowlist_meta keys
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4b_update_run_allowlist_missing_keys_added(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import update_run_allowlist

    cfg_path = tmp_path / "containment-zones.json"
    bare_cfg = {"version": 1, "enforce": False, "zones": {"allow": [], "deny": []}}
    _write_config(cfg_path, bare_cfg)

    result = update_run_allowlist(
        cfg_path, "run1", ["/a.py"], now_iso="2026-07-03T01:00:00Z"
    )

    assert result["status"] == "written", f"A4 FAIL: expected written, got {result!r}"

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["run_allowlist"] == ["/a.py"], (
        f"A4 FAIL: run_allowlist key not added correctly, got {on_disk.get('run_allowlist')!r}"
    )
    assert on_disk["run_allowlist_meta"]["run1"]["entries"] == ["/a.py"], (
        f"A4 FAIL: run_allowlist_meta key not added correctly, got "
        f"{on_disk.get('run_allowlist_meta')!r}"
    )
    assert on_disk["version"] == 1 and on_disk["enforce"] is False, (
        "A4 FAIL: other keys must be preserved when adding both new keys"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — idempotent replace on same run_id
# ═══════════════════════════════════════════════════════════════════════════


def test_ac5_update_run_allowlist_same_run_id_idempotent_replace(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import update_run_allowlist

    cfg_path = tmp_path / "containment-zones.json"
    _write_config(cfg_path, _step1_config())

    update_run_allowlist(cfg_path, "runX", ["/first.py"], now_iso="2026-07-03T01:00:00Z")
    update_run_allowlist(cfg_path, "runX", ["/second.py"], now_iso="2026-07-03T01:05:00Z")

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert len(on_disk["run_allowlist_meta"]) == 1, (
        f"AC5 FAIL: expected ONE meta entry for repeated run_id, got "
        f"{on_disk['run_allowlist_meta']!r}"
    )
    assert on_disk["run_allowlist_meta"]["runX"]["entries"] == ["/second.py"], (
        f"AC5 FAIL: last entries must win, got {on_disk['run_allowlist_meta']['runX']!r}"
    )
    assert on_disk["run_allowlist"] == ["/second.py"], (
        f"AC5 FAIL: union must reflect only last entries, got {on_disk['run_allowlist']!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — TTL GC (§1i: now_iso pre-staged, no race)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac6_update_run_allowlist_ttl_gc_drops_stale_entry(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import update_run_allowlist

    cfg_path = tmp_path / "containment-zones.json"
    # Pre-stage a meta entry with ts 49h before the injected now_iso (default TTL 48h).
    seeded = _step1_config(
        run_allowlist=["/stale.py"],
        run_allowlist_meta={
            "stale_run": {"ts": "2026-07-01T00:00:00Z", "entries": ["/stale.py"]}
        },
    )
    _write_config(cfg_path, seeded)

    result = update_run_allowlist(
        cfg_path, "fresh_run", ["/fresh.py"], now_iso="2026-07-03T01:00:00Z"
    )
    assert result["status"] == "written"

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "stale_run" not in on_disk["run_allowlist_meta"], (
        f"AC6 FAIL: 49h-stale meta entry must be GC'd (default TTL 48h), "
        f"got meta={on_disk['run_allowlist_meta']!r}"
    )
    assert "/stale.py" not in on_disk["run_allowlist"], (
        f"AC6 FAIL: union must not include GC'd entries, got {on_disk['run_allowlist']!r}"
    )
    assert "fresh_run" in on_disk["run_allowlist_meta"], (
        "AC6 FAIL: fresh entry must survive GC"
    )
    assert "/fresh.py" in on_disk["run_allowlist"]

    # ttl_hours_default honored: json key run_allowlist_ttl_hours=1 makes a 2h-old
    # entry stale even though it would survive the 48h default.
    cfg_path2 = tmp_path / "containment-zones-ttl1.json"
    seeded2 = _step1_config(
        run_allowlist_ttl_hours=1,
        run_allowlist=["/two-hours-old.py"],
        run_allowlist_meta={
            "old2h": {"ts": "2026-07-02T23:00:00Z", "entries": ["/two-hours-old.py"]}
        },
    )
    _write_config(cfg_path2, seeded2)
    update_run_allowlist(cfg_path2, "another", ["/x.py"], now_iso="2026-07-03T01:00:00Z")
    on_disk2 = json.loads(cfg_path2.read_text(encoding="utf-8"))
    assert "old2h" not in on_disk2["run_allowlist_meta"], (
        f"AC6 FAIL: run_allowlist_ttl_hours=1 override not honored, "
        f"got meta={on_disk2['run_allowlist_meta']!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — phase_45_spec._gate_on_review SHIP branch writes run_allowlist
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_phase45_spec_gate_on_review_ship_writes_allowlist(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cfg_path = tmp_path / "containment-zones.json"
    _write_config(cfg_path, _step1_config())

    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# Spec\n\n## Files\n- x/y.py\n", encoding="utf-8")

    ctx = _ctx({
        "run_id": "testrun1",
        "git_cwd": str(repo_dir),
        "scope_zones_config": str(cfg_path),
    })
    prev = _ship_prev(str(spec_path))

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", f"AC7 FAIL: gate must still return ok, got {result.status!r}"
    for key in ("verdict", "review_path", "spec_path", "cycle"):
        assert key in result.data, f"AC7 FAIL: expected key {key!r} in result.data"

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    expected_entry = str((repo_dir / "x/y.py").resolve())
    assert expected_entry in on_disk.get("run_allowlist", []), (
        f"AC7 FAIL: expected absolutized entry {expected_entry!r} in run_allowlist, "
        f"got {on_disk.get('run_allowlist')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# A2 — already-absolute ## Files entry resolves via expanduser().resolve(),
# NOT git_cwd-joined
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7b_gate_on_review_absolute_entry_resolved_not_git_cwd_joined(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    abs_target_dir = tmp_path / "abs" / "tmp" / "dir"
    abs_target_dir.mkdir(parents=True)
    abs_target = abs_target_dir / "z.py"
    cfg_path = tmp_path / "containment-zones.json"
    _write_config(cfg_path, _step1_config())

    spec_path = tmp_path / "spec-abs.md"
    spec_path.write_text(
        f"# Spec\n\n## Files\n- MODIFY {abs_target} (note)\n", encoding="utf-8"
    )

    ctx = _ctx({
        "run_id": "testrun2",
        "git_cwd": str(repo_dir),
        "scope_zones_config": str(cfg_path),
    })
    prev = _ship_prev(str(spec_path))

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", f"A2 FAIL: gate must still return ok, got {result.status!r}"

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    expected_entry = str(Path(str(abs_target)).expanduser().resolve())
    assert expected_entry in on_disk.get("run_allowlist", []), (
        f"A2 FAIL: expected absolute entry resolved (not git_cwd-joined) as "
        f"{expected_entry!r} in run_allowlist, got {on_disk.get('run_allowlist')!r}"
    )
    unwanted_git_cwd_join = str(Path(f"{repo_dir}/{abs_target}"))
    assert unwanted_git_cwd_join not in on_disk.get("run_allowlist", []), (
        "A2 FAIL: absolute entry must not be joined onto git_cwd"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8 — phase_45_spec_lite._gate_on_review SHIP branch, same contract
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8_phase45_spec_lite_gate_on_review_ship_writes_allowlist(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec_lite

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cfg_path = tmp_path / "containment-zones.json"
    _write_config(cfg_path, _step1_config())

    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# Spec\n\n## Files\n- x/y.py\n", encoding="utf-8")

    ctx = _ctx({
        "run_id": "testrun1",
        "git_cwd": str(repo_dir),
        "scope_zones_config": str(cfg_path),
    })
    prev = _ship_prev(str(spec_path))

    result = phase_45_spec_lite._gate_on_review(ctx, prev)

    assert result.status == "ok", f"AC8 FAIL: gate must still return ok, got {result.status!r}"

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    expected_entry = str((repo_dir / "x/y.py").resolve())
    assert expected_entry in on_disk.get("run_allowlist", []), (
        f"AC8 FAIL: expected absolutized entry {expected_entry!r} in run_allowlist, "
        f"got {on_disk.get('run_allowlist')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC9 — writer failure (unwritable/missing config) never breaks the gate
# ═══════════════════════════════════════════════════════════════════════════


def test_ac9_phase45_spec_gate_writer_failure_never_breaks_ship(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    missing_cfg_path = tmp_path / "no-such-dir" / "containment-zones.json"

    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# Spec\n\n## Files\n- x/y.py\n", encoding="utf-8")

    ctx = _ctx({
        "run_id": "testrun1",
        "git_cwd": str(repo_dir),
        "scope_zones_config": str(missing_cfg_path),
    })
    prev = _ship_prev(str(spec_path))

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", (
        f"AC9 FAIL: writer failure (missing config) must NOT break gate SHIP, "
        f"got status {result.status!r}"
    )
    assert result.data["verdict"] == "SHIP"


# ═══════════════════════════════════════════════════════════════════════════
# AC10 — hermeticity: bare cfg never resolves a real machine path
# ═══════════════════════════════════════════════════════════════════════════


def test_ac10_gate_on_review_bare_cfg_hermetic_no_file_created(tmp_path: Path, monkeypatch) -> None:
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_SCOPE_ZONES_CONFIG", raising=False)

    canary_dir = tmp_path / "canary-home"
    canary_dir.mkdir()

    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# Spec\n\n## Files\n- x/y.py\n", encoding="utf-8")

    ctx = _ctx({"run_id": "testrun1"})
    prev = _ship_prev(str(spec_path))

    result = phase_45_spec._gate_on_review(ctx, prev)

    assert result.status == "ok", f"AC10 FAIL: gate must stay ok, got {result.status!r}"
    assert list(canary_dir.iterdir()) == [], (
        "AC10 FAIL: hermeticity violated — bare cfg (no scope_zones_config, no env, "
        "no hal marker) must never write anything, canary dir is no longer empty"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC11 — write_run_allowlist_for_spec guard order
# ═══════════════════════════════════════════════════════════════════════════


def test_ac11_write_run_allowlist_for_spec_guard_order(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import write_run_allowlist_for_spec

    cfg_path = tmp_path / "containment-zones.json"
    original_bytes = json.dumps(_step1_config()).encode("utf-8")
    cfg_path.write_bytes(original_bytes)

    spec_with_files = tmp_path / "spec-with-files.md"
    spec_with_files.write_text(_SPEC_WITH_FILES, encoding="utf-8")

    spec_without_files = tmp_path / "spec-no-files.md"
    spec_without_files.write_text(_SPEC_WITH_NEITHER, encoding="utf-8")

    # no run_id → skipped/no_run_id even with a resolvable config present
    result_no_run_id = write_run_allowlist_for_spec(
        {"scope_zones_config": str(cfg_path)}, str(spec_with_files)
    )
    assert result_no_run_id == {"status": "skipped", "reason": "no_run_id"}, (
        f"AC11 FAIL: expected skipped/no_run_id, got {result_no_run_id!r}"
    )

    # config unresolvable → skipped/no_config
    result_no_config = write_run_allowlist_for_spec(
        {"run_id": "r1"}, str(spec_with_files)
    )
    assert result_no_config == {"status": "skipped", "reason": "no_config"}, (
        f"AC11 FAIL: expected skipped/no_config, got {result_no_config!r}"
    )

    # spec without sections → skipped/no_files_section, config byte-unchanged
    result_no_files = write_run_allowlist_for_spec(
        {"run_id": "r1", "scope_zones_config": str(cfg_path)}, str(spec_without_files)
    )
    assert result_no_files == {"status": "skipped", "reason": "no_files_section"}, (
        f"AC11 FAIL: expected skipped/no_files_section, got {result_no_files!r}"
    )
    assert cfg_path.read_bytes() == original_bytes, (
        "AC11 FAIL: config file bytes must be unchanged after a no_files_section skip"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC12 — phase_8 cleanup step + workflow registry position
# ═══════════════════════════════════════════════════════════════════════════


def test_ac12_cleanup_run_allowlist_removes_only_own_run_and_registered(tmp_path: Path) -> None:
    from bytedigger_engine.workflows import phase_8_post_deploy

    cfg_path = tmp_path / "containment-zones.json"
    seeded = _step1_config(
        run_allowlist=["/a.py", "/b.py"],
        run_allowlist_meta={
            "A": {"ts": "2026-07-03T00:00:00Z", "entries": ["/a.py"]},
            "B": {"ts": "2026-07-03T00:00:00Z", "entries": ["/b.py"]},
        },
    )
    _write_config(cfg_path, seeded)

    ctx = _ctx({"run_id": "A", "scope_zones_config": str(cfg_path)})
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="cleanup_eval_rotation")

    result = phase_8_post_deploy._cleanup_run_allowlist(ctx, prev)

    assert result.status == "ok", f"AC12 FAIL: cleanup step must return ok, got {result.status!r}"

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "A" not in on_disk["run_allowlist_meta"], (
        f"AC12 FAIL: run A's meta must be removed, got {on_disk['run_allowlist_meta']!r}"
    )
    assert "B" in on_disk["run_allowlist_meta"], "AC12 FAIL: run B's meta must remain intact"
    assert "/a.py" not in on_disk["run_allowlist"], "AC12 FAIL: /a.py must be gone from union"
    assert "/b.py" in on_disk["run_allowlist"], "AC12 FAIL: /b.py must remain in union"

    # missing config → ok skip (best-effort cleanup never fails phase_8)
    ctx_missing = _ctx({"run_id": "A", "scope_zones_config": str(tmp_path / "nope.json")})
    result_missing = phase_8_post_deploy._cleanup_run_allowlist(ctx_missing, prev)
    assert result_missing.status == "ok", (
        f"AC12 FAIL: missing-config cleanup must still return ok, got {result_missing.status!r}"
    )

    # workflow registry: step name registered between cleanup_scratchpad_subdirs
    # and write_cleanup_report.
    step_names = [s.name for s in phase_8_post_deploy.phase_8_post_deploy_workflow().steps]
    assert "cleanup_run_allowlist" in step_names, (
        f"AC12 FAIL: 'cleanup_run_allowlist' not registered in workflow steps: {step_names!r}"
    )
    idx_cleanup = step_names.index("cleanup_run_allowlist")
    idx_scratchpad = step_names.index("cleanup_scratchpad_subdirs")
    idx_report = step_names.index("write_cleanup_report")
    assert idx_scratchpad < idx_cleanup < idx_report, (
        f"AC12 FAIL: expected cleanup_scratchpad_subdirs < cleanup_run_allowlist < "
        f"write_cleanup_report, got indices {idx_scratchpad}/{idx_cleanup}/{idx_report} "
        f"in {step_names!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC13 — invalid-JSON config: skip + byte-unchanged
# ═══════════════════════════════════════════════════════════════════════════


def test_ac13_update_run_allowlist_invalid_json_skip_byte_unchanged(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import update_run_allowlist

    cfg_path = tmp_path / "containment-zones.json"
    original_bytes = b"{ not valid json at all"
    cfg_path.write_bytes(original_bytes)

    result = update_run_allowlist(cfg_path, "runX", ["/a.py"], now_iso="2026-07-03T01:00:00Z")

    assert result["status"] == "skipped", (
        f"AC13 FAIL: invalid JSON must yield status=skipped, got {result!r}"
    )
    assert cfg_path.read_bytes() == original_bytes, (
        "AC13 FAIL: config bytes must be unchanged after an invalid-JSON skip"
    )


# ═══════════════════════════════════════════════════════════════════════════
# A5 — remove_run_allowlist: invalid-JSON skip + absent-run_id skip
# ═══════════════════════════════════════════════════════════════════════════


def test_remove_run_allowlist_invalid_json_and_absent_run_id_skipped(tmp_path: Path) -> None:
    from bytedigger_engine.lib.run_allowlist import remove_run_allowlist

    invalid_cfg_path = tmp_path / "invalid.json"
    original_bytes = b"{ not valid json at all"
    invalid_cfg_path.write_bytes(original_bytes)

    result_invalid = remove_run_allowlist(
        invalid_cfg_path, "runX", now_iso="2026-07-03T01:00:00Z"
    )
    assert result_invalid["status"] == "skipped", (
        f"A5 FAIL: invalid JSON must yield status=skipped, got {result_invalid!r}"
    )
    assert invalid_cfg_path.read_bytes() == original_bytes, (
        "A5 FAIL: invalid-JSON config bytes must be unchanged after skip"
    )

    valid_cfg_path = tmp_path / "valid.json"
    seeded = _step1_config(
        run_allowlist=["/kept.py"],
        run_allowlist_meta={
            "known_run": {"ts": "2026-07-03T00:00:00Z", "entries": ["/kept.py"]}
        },
    )
    _write_config(valid_cfg_path, seeded)
    original_json = json.loads(valid_cfg_path.read_text(encoding="utf-8"))

    result_absent = remove_run_allowlist(
        valid_cfg_path, "unknown_run_id", now_iso="2026-07-03T01:00:00Z"
    )
    assert result_absent["status"] == "skipped", (
        f"A5 FAIL: absent run_id must yield status=skipped, got {result_absent!r}"
    )
    on_disk = json.loads(valid_cfg_path.read_text(encoding="utf-8"))
    assert on_disk == original_json, (
        f"A5 FAIL: config must be semantically unchanged after absent-run_id skip, "
        f"got {on_disk!r} vs original {original_json!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC14 — single-source-of-truth re-export identity (§1g)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac14_phase5_implement_parser_is_canonical_run_allowlist_parser() -> None:
    from bytedigger_engine.workflows import phase_5_implement
    from bytedigger_engine.lib.run_allowlist import parse_spec_files_allowlist as canon

    assert phase_5_implement._parse_spec_files_allowlist is canon, (
        "AC14 FAIL: phase_5_implement._parse_spec_files_allowlist must be the SAME "
        "object as lib.run_allowlist.parse_spec_files_allowlist (re-export, not a copy)"
    )
