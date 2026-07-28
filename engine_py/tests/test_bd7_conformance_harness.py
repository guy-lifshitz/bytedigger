"""RED tests for bd#7 — conformance harness + oracle interface + attestation
writer + BD-L0.

Spec v9 (amended after gate REJECTED v1 on 8 blocking defects, MAJOR-1..8,
REJECTED v2 on 4 blocking defects [G2:1]/[G2:2]/[G2:3]/[G2:4], REJECTED v3
on 4 more blocking defects [G3:MAJOR-1..4], REJECTED v4 on 5 more blocking
defects [G4:1]/[G4:2]/[G4:3]/[G4:4]/[G4:5], REJECTED v5 on ONE more blocking
defect [G5:accum], REJECTED v6 on ONE more blocking defect [G6:1], then
REJECTED v8 on THREE more blocking defects [G7:1]/[G7:1b]/[G7:2]):
engine_py/conformance/SPEC.md (frozen), source of truth
`2026-07-26_bytedigger_conformance_levels.md` §1-6, 9.

v9 [G7:1]/[G7:1b]/[G7:2] closes the three round-7 blocking defects, all
found in v7/v8's own round-7 RED additions:
  AC-E10      [G7:1] clause 1 no longer requires a NEWLY CREATED thread
      (which a daemonising pool reuses from an idle worker rather than
      creating) — the oracle now records its own threading.current_thread()
      into a caller-visible list, and the assertion checks THAT thread's
      is_alive()/daemon, admitting both per-call and pooled designs
  AC-L0-3d3   [G7:1b] the unshadowed joint-satisfiability control no
      longer reuses a boundary path count computed under DIFFERENT (shorter)
      identifiers — it now recomputes its own boundary from the exact
      identifiers it uses at runtime, closing a false-RED that was
      unsatisfiable by any implementation (18 B over the limit either way)
  AC-L0-6e / AC-L0-3b5   [G7:2a]/[G7:2b] both rewritten to assert on a
      NON-UNIFORM two-phase fixture (phase_b's step events dropped /
      phase_b's artifact record malformed) plus the well-formed two-phase
      positive control — v7 asserted both only over single-phase logs,
      admitting a GREEN that checked the precondition once per RUN
      instead of once per PHASE
plus six MINOR pins ([G7:MINOR-1..6]) and five new ACs promoted from
adversarial edges: AC-L0-15 (empty scope must not vacuously pass — the
cheapest false-green left, one scope out from the phase/run ladder),
AC-L0-3d4 (a single pathological ~4200-char path: the TRUNCATED payload
must itself fit under 4096, injected via the git_port seam to avoid a
real PATH_MAX collision), AC-L0-3d5 (exactly 4096 bytes is LEGAL, not
truncated — event_log.py rejects only strictly-greater), AC-L0-6g (a
writer whose append raises must not propagate AND must render R0.1
"not-checked", never "passed"), AC-L0-3b7 (a phase_artifacts preceding
its own workflow_started is valid — order within a scope carries no
normative meaning beyond AC-L0-2's positional rule).

v7 [G6:1] closes the ONE round-6 blocking defect — the LAST rung of
[G6:quant] (phase -> run; no collection sits above "the phases of one
scoped run" in this design):
  AC-L0-3a5   R0.2 is UNIVERSALLY quantified over the phases of the scoped
      run, asserted on a NON-UNIFORM two-phase fixture (phase_a git-delta,
      phase_b not-observed) across all four numbered sub-clauses: checker
      "not-checked" + positive control (uniform two-phase); the same
      L0Report through the WRITTEN attestation file; end-to-end on this
      host (two execute() calls, one run_id, one phase with git_cwd, one
      without); and the other three R0.2 clauses each asserted by
      mutating phase_b ONLY (phase-key strip / workflow_finished removal /
      status strip)
plus six new ACs and six rewrites the [G6:quant] sweep (SPEC.md §4.3)
required alongside it:
  AC-L0-3b4   written relpath spelling asserted for a NESTED path
      (sub/dir/nested.txt), exact string equality + digest recomputed
      over that spelling
  AC-L0-3b5   an artifact record must actually carry written/read/
      read_tracking, present and list-typed, or R0.2 is not-checked
  AC-L0-3b6   an orphan phase_artifacts (phase with no workflow_started in
      the scoped run) is an R0.2 violation ("failed")
  AC-L0-2d    run_identity is once per RUN, asserted per run (two
      execute() calls, DIFFERENT run_ids, both must report R0.3 passed)
  AC-A28      version resolution must not be memoised — two resolutions in
      one process with the seam changed between them must differ
  AC-E10      REPLACED (v6's grace-vs-sleep formulation was unmeasurable
      and partly false-failing): daemon-thread-after-grace + no
      accumulation over N repeated timed-out calls
  AC-A27      the UTC-host blind spot is now CLOSED (TZ=Etc/GMT-14 +
      time.tzset()), not merely declared
  AC-F14      the symlink half of the dedupe-on-normalised-relpath rule is
      now asserted (not just the "./" half)
  AC-L0-9     clause 7 (engine_version present but empty) is EXCLUDED from
      the "structural breach -> failed" rule and asserted "not-checked"
      instead — it is AC-L0-2c's unresolved-version case, an unmeasured
      channel, not a malformed log
  AC-L0-3d3   reworded so the shadow-envelope headroom reserve is asserted
      as conditional on the shadow branch applying, not unconditional —
      a direct joint-satisfiability check against AC-L0-3d2's just-under
      unshadowed control is now inside this test
AC-L0-6f already pinned tempfile.mkdtemp as the spied mechanism as of v6;
no change required for v7.

v6 [G5:accum] closes the ONE round-5 blocking defect:
  AC-L0-3b2   written is the UNION of every step's delta for the phase,
      never the last step's — asserted over a >=2-step phase, both the
      union half and the DISCRIMINATING half (early step writes, last step
      does not, written must still carry the early write)
  AC-L0-3b3   accumulation survives the validation-retry recursion
      (engine.py:638-645 re-entry; outer frame returns at :657 without its
      own tail) — the pre-retry step's write must reach the final
      phase_artifacts
plus round-5 minors/edges: AC-F13 (freeze reads bytes, not text — CRLF/LF
folded into AC-F12's own golden vector), AC-F14 (dedupe on normalised
relpath, not object identity), AC-E10 (timed-out oracle worker is reaped),
AC-L0-6e (checker must not grant R0.2 over zero step events — [G4:4]'s
vacuous all() one level up, inside the checker), AC-L0-6f (probe removes
its own temp dir), AC-L0-3d3 (truncation predicate leaves headroom for the
shadow envelope — overhead measured, not guessed), AC-A25 (writer creates
missing parent dirs), AC-A26 (L0Report immutability), AC-A27 (timestamp
UTC-ness vs an independent time.time() reading), AC-L0-9 clauses 1/6 pin
`requirements[r] == "failed"` for a structural breach (not "not-checked" —
that token is reserved for an unmeasured channel), AC-F12 docstring now
cites [G5:endian] instead of an open ambiguity (v6 pins big-endian
normatively). Also: converted the four remaining unguarded
`next(genexpr)` StopIteration traps (AC-L0-2b x2, AC-L0-3a x2) to the
guarded list form, plus a fifth found by the same pattern in AC-L0-3a2
(not one the gate named, but the identical defect class in the same file
region, fixed for consistency).

v5 [G4:*] additions close the five round-4 blocking defects:
  AC-A8    producer identity/provenance/timestamp/level_claimed asserted by
           VALUE against distinctive sentinels, not truthiness
  AC-A10/AC-A21/AC-L0-11b   the WRITTEN file is pinned to the built report
           via full dict equality (reparsed == report), not a subset spot-check
  AC-L0-9 clause 5   the injected duplicate phase_artifacts now carries
           write_tracking so duplication is the ONLY difference from baseline
  AC-L0-3a4 / AC-L0-3c   zero-step and partial-delta-failure negative
           controls close the "every step" quantifier's two false affirmatives
  AC-L0-3d   written_digest asserted by EQUALITY against a digest computed
           from the real path set, not startswith("sha256:")
plus round-4 minors/edges: AC-L0-3d2 (truncation boundary), AC-L0-3e2 (crash
path), AC-L0-6d (probe containment), AC-L0-12e (same-run_id duplicate),
AC-A7b (label derived from requirements, not l0.passed), AC-A24 (failed
adversary published as failed), AC-F12 (freeze golden vector), AC-P2
(core_manifest exclusion), AC-L0-3a3 extension (unrecognised tokens),
AC-L0-12d (structural not prose-coupled), AC-L0-2b (delenv all 3 env
spellings).

v5 addendum [G5:base]: AC-L0-2c — engine_version provenance survives
packaging (importlib.metadata first, pyproject.toml fallback for source
checkouts) and has no placeholder ("unknown"/"0.0.0"/"0+unknown") when
neither resolves. Landed in the spec after round 5's initial commit
(047ad91); added here without touching any other round-5 test.

v3 [G2:*] additions closed the four round-2 blocking defects:
  AC-L0-6c   R0.1 probe negative control (three inputs, not two)
  AC-A18     L0Report.passed/.violations are not ignorable
  AC-A11 (modified) / AC-A11b   level_achieved never capped by level_claimed
  AC-L0-3a / AC-A7b   write_tracking differential (git-delta vs not-observed)
plus round-2 minors/edges: AC-A19, AC-A20, AC-A21, AC-L0-3e, AC-L0-3f,
AC-L0-3g, AC-L0-12b, AC-L0-12c, AC-L0-13, AC-L0-14.

v4 [G3:*] additions close the four round-3 blocking defects:
  AC-L0-10/AC-L0-11 (modified) / AC-L0-11b / AC-L0-6b2
      drive the composed path with a REAL git repo (org_config["git_cwd"]),
      pair it with the explicit unmeasured case, and generalise the
      "not-checked fail-closes" rule to R0.2/R0.3, not just R0.1
  AC-A22   report["l0"] has a negative control (asserted on FAILING reports)
  AC-A23   conformant is False whenever level_achieved is None, all 4 claims
  AC-L0-3a2 / AC-L0-3a3
      negative control for the write-tracking failure branch (git_cwd
      pointing at a non-repo) and for a missing write_tracking key
plus round-3 minors/edges: AC-L0-12d (run_id scoping is functionally
asserted), whole-dict-shape payload assertion in AC-L0-3.

Covers every AC in the spec, one test function per AC:
  AC-O1..AC-O5   OracleOutcome — three unmergeable states, no mixin base (§2.1)
  AC-F1..AC-F14  freeze() — hash over the artifact set including membership (§2.2)
  AC-E1..AC-E10  evaluate_guarded — the indeterminate guard (§2.3)
  AC-A1..AC-A27  attestation writer (§3)
  AC-L0-1..AC-L0-14 (plus -2c/-3a4/-3b2/-3b3/-3d2/-3d3/-3e2/-6d/-6e/-6f/-12e)
                 BD-L0 engine + checker (§4)
  AC-P1/AC-P2    non-regression: pyproject include / core_manifest exclusion (§6)

§1q-ext: `engine_py/conformance/{oracle,attestation,bd_l0}.py` do not exist
yet. Every reference to those new symbols is deferred INSIDE the relevant
test function body so this file still COLLECTS cleanly under pytest; only
already-existing symbols (contracts, engine, event_log) are imported at
module level, mirroring test_engine.py / test_GH374_step_sentinel_primitive.py.

No sys.path manipulation here: conftest.py already inserts engine_py root
(and workflows/lib dirs) into sys.path at conftest-import time (§1q
conftest-import-time-singleton pattern) — a second, per-file sys.path
mutation is the exact shape the suite_safety scanner flags (81F97F3D).

No adversary (ADV-1..ADV-10) behaviour is exercised here — out of scope for
this lot (§5). The attestation tests exercise only the registry/status/
level-computation logic against synthetic result sets.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from engine import WorkflowEngine
from event_log import EventLog


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-q", "-b", "main")
    _git(repo_dir, "config", "user.email", "t@t")
    _git(repo_dir, "config", "user.name", "t")
    (repo_dir / "seed.txt").write_text("seed\n")
    _git(repo_dir, "add", "seed.txt")
    _git(repo_dir, "commit", "-q", "-m", "init")


def _make_ctx(**org_extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=dict(org_extra),
        question="bd7 conformance harness test",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


def _ok_step(name: str, payload=None):
    def _run(_ctx, _prev):
        return StepResult(status="ok", data=payload, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


# ═══════════════════════════════════════════════════════════════════════════
# §2.1 — OracleOutcome: three states, unmergeable by type
# ═══════════════════════════════════════════════════════════════════════════


def test_ac_o1_bool_outcome_raises_typeerror():
    """AC-O1: bool(outcome) MUST raise TypeError (Enum members are truthy by
    default; that silent collapse of INDETERMINATE into "accepted" is exactly
    what R2.1 exists to prevent).
    """
    from conformance.oracle import OracleOutcome  # noqa: PLC0415

    with pytest.raises(TypeError):
        bool(OracleOutcome.INDETERMINATE)
    with pytest.raises(TypeError):
        bool(OracleOutcome.ACCEPTED)
    with pytest.raises(TypeError):
        bool(OracleOutcome.REJECTED)


def test_ac_o2_equality_against_bool_raises_typeerror():
    """AC-O2: outcome == True / outcome == False MUST raise TypeError — the
    second collapse path (equality against bool) closed at the type."""
    from conformance.oracle import OracleOutcome  # noqa: PLC0415

    with pytest.raises(TypeError):
        _ = OracleOutcome.ACCEPTED == True  # noqa: E712
    with pytest.raises(TypeError):
        _ = OracleOutcome.REJECTED == False  # noqa: E712
    with pytest.raises(TypeError):
        _ = OracleOutcome.INDETERMINATE == True  # noqa: E712


def test_ac_o3_no_bool_constructor():
    """AC-O3: no from_bool constructor exists, and OracleOutcome(True) MUST
    raise ValueError."""
    from conformance.oracle import OracleOutcome  # noqa: PLC0415

    assert not hasattr(OracleOutcome, "from_bool"), (
        "OracleOutcome must not expose a from_bool constructor"
    )
    with pytest.raises(ValueError):
        OracleOutcome(True)
    with pytest.raises(ValueError):
        OracleOutcome(False)


def test_ac_o4_three_members_distinct():
    """AC-O4: the three members are distinct; INDETERMINATE is not REJECTED
    and is not ACCEPTED."""
    from conformance.oracle import OracleOutcome  # noqa: PLC0415

    assert OracleOutcome.INDETERMINATE is not OracleOutcome.REJECTED
    assert OracleOutcome.INDETERMINATE is not OracleOutcome.ACCEPTED
    assert OracleOutcome.REJECTED is not OracleOutcome.ACCEPTED
    assert len({OracleOutcome.REJECTED, OracleOutcome.ACCEPTED, OracleOutcome.INDETERMINATE}) == 3


def test_ac_o5_no_mixin_base_and_default_serialisation_is_not_json_native():
    """AC-O5 [G:edge-7]: OracleOutcome MUST NOT use a mixin base — its __mro__
    MUST contain no type other than OracleOutcome, Enum and object. A str/int
    mixin would satisfy AC-O1..O4 while json.dumps(outcome) re-emits a bare
    truthy scalar, so the collapse this AC exists to prevent returns at the
    serialisation boundary."""
    from conformance.oracle import OracleOutcome  # noqa: PLC0415

    mro_names = {c.__name__ for c in OracleOutcome.__mro__}
    assert mro_names == {"OracleOutcome", "Enum", "object"}, (
        f"OracleOutcome.__mro__ must contain only OracleOutcome/Enum/object, "
        f"got {mro_names!r} — a str/int mixin base is exactly the collapse this "
        f"AC forbids"
    )

    import json  # noqa: PLC0415

    with pytest.raises(TypeError):
        json.dumps(OracleOutcome.ACCEPTED)


# ═══════════════════════════════════════════════════════════════════════════
# §2.2 — freeze(): hash over the artifact set including membership
# ═══════════════════════════════════════════════════════════════════════════


def test_ac_f1_adding_empty_file_changes_hash(tmp_path):
    """AC-F1: adding a file to the set MUST change the hash, even when the
    added file is empty (membership is inside the digest via the leading
    count + length-prefixed relpaths)."""
    from conformance.oracle import freeze  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    h_before = freeze([root / "a.txt"], root=root)

    (root / "b.txt").write_text("")  # empty file
    h_after = freeze([root / "a.txt", root / "b.txt"], root=root)

    assert h_before != h_after


def test_ac_f2_removing_file_changes_hash(tmp_path):
    """AC-F2: removing a file MUST change the hash."""
    from conformance.oracle import freeze  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (root / "b.txt").write_text("world")
    h_two = freeze([root / "a.txt", root / "b.txt"], root=root)
    h_one = freeze([root / "a.txt"], root=root)

    assert h_two != h_one


def test_ac_f3_rename_identical_content_changes_hash(tmp_path):
    """AC-F3: renaming a file with identical content MUST change the hash."""
    from conformance.oracle import freeze  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    (root / "old.txt").write_text("same-content")
    h_old_name = freeze([root / "old.txt"], root=root)

    (root / "new.txt").write_text("same-content")
    h_new_name = freeze([root / "new.txt"], root=root)

    assert h_old_name != h_new_name


def test_ac_f4_one_byte_content_change_changes_hash(tmp_path):
    """AC-F4: changing one byte of content MUST change the hash."""
    from conformance.oracle import freeze  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("hello")
    h_before = freeze([target], root=root)

    target.write_text("hellp")  # last byte changed
    h_after = freeze([target], root=root)

    assert h_before != h_after


def test_ac_f5_reordering_input_iterable_does_not_change_hash(tmp_path):
    """AC-F5: reordering the input iterable MUST NOT change the hash
    (canonical sort inside freeze)."""
    from conformance.oracle import freeze  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    a = root / "a.txt"
    b = root / "b.txt"
    a.write_text("aaa")
    b.write_text("bbb")

    h_ab = freeze([a, b], root=root)
    h_ba = freeze([b, a], root=root)

    assert h_ab == h_ba


def test_ac_f6_name_content_alias_does_not_collide(tmp_path):
    """AC-F6: two artifact sets whose concatenated name/content bytes alias
    each other MUST NOT collide — {"ab": "c"} vs {"a": "bc"}. Without a
    length prefix, "ab"+"c" == "a"+"bc" == "abc"; length prefixes forbid it.
    """
    from conformance.oracle import freeze  # noqa: PLC0415

    root_a = tmp_path / "root_a"
    root_a.mkdir()
    (root_a / "ab").write_text("c")
    h_a = freeze([root_a / "ab"], root=root_a)

    root_b = tmp_path / "root_b"
    root_b.mkdir()
    (root_b / "a").write_text("bc")
    h_b = freeze([root_b / "a"], root=root_b)

    assert h_a != h_b


def test_ac_f7_missing_path_raises_oracle_freeze_error(tmp_path):
    """AC-F7: a missing or unreadable path MUST raise OracleFreezeError —
    silently skipping an unreadable oracle artifact weakens the freeze
    exactly when it matters."""
    from conformance.oracle import freeze, OracleFreezeError  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    missing = root / "does_not_exist.txt"

    with pytest.raises(OracleFreezeError):
        freeze([missing], root=root)


def test_ac_f8_duplicate_relpath_raises_oracle_freeze_error(tmp_path):
    """AC-F8: a duplicate relpath in the input MUST raise OracleFreezeError."""
    from conformance.oracle import freeze, OracleFreezeError  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    a = root / "a.txt"
    a.write_text("hi")

    with pytest.raises(OracleFreezeError):
        freeze([a, a], root=root)


def test_ac_f9_empty_artifact_set_raises_oracle_freeze_error(tmp_path):
    """AC-F9: an empty artifact set MUST raise OracleFreezeError — a freeze
    over nothing cannot detect anything."""
    from conformance.oracle import freeze, OracleFreezeError  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(OracleFreezeError):
        freeze([], root=root)


def test_ac_f10_freeze_returns_sha256_prefix_and_64_lowercase_hex_chars(tmp_path):
    """AC-F10 [G:MINOR-3]: freeze MUST return "sha256:" followed by exactly
    64 lowercase hex characters."""
    import re  # noqa: PLC0415

    from conformance.oracle import freeze  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")

    h = freeze([root / "a.txt"], root=root)

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", h), f"expected sha256:<64 lowercase hex>, got {h!r}"


def test_ac_f11_unreadable_paths_raise_oracle_freeze_error_not_os_errors(tmp_path):
    """AC-F11 [G:MINOR-4]: "unreadable" MUST include a directory and a
    mode-000 file — both MUST raise OracleFreezeError, never
    IsADirectoryError/PermissionError. A path outside root MUST also raise
    OracleFreezeError, never ValueError."""
    from conformance.oracle import freeze, OracleFreezeError  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()

    subdir = root / "a_directory"
    subdir.mkdir()
    with pytest.raises(OracleFreezeError):
        freeze([subdir], root=root)

    mode000 = root / "no_perm.txt"
    mode000.write_text("secret")
    mode000.chmod(0o000)
    try:
        with pytest.raises(OracleFreezeError):
            freeze([mode000], root=root)
    finally:
        mode000.chmod(0o644)  # restore so tmp_path cleanup can remove it

    outside_dir = tmp_path / "outside_root"
    outside_dir.mkdir()
    outside_file = outside_dir / "x.txt"
    outside_file.write_text("x")
    with pytest.raises(OracleFreezeError):
        freeze([outside_file], root=root)


def test_ac_f12_golden_vector_over_fixed_two_file_set(tmp_path):
    """AC-F12 [G4:MINOR-7]: one KNOWN-ANSWER vector. AC-F1..F11 are all
    relational, so any collision-resistant scheme satisfies them and the
    normative byte stream (§2.2) is not actually pinned — a published
    freeze would not be reproducible against the documented format across
    hosts or versions. Required: one golden-vector assertion over a fixed
    two-file set, with the expected digest computed from the documented
    stream (domain prefix, u64 count, u64-length-prefixed relpaths and
    contents) in the test itself.

    `[G5:endian]` u64 is normatively BIG-ENDIAN (`struct.pack("!Q", n)`,
    network byte order) — pinned in the spec, not assumed here.
    `[G5:EDGE-4]` (AC-F13's own requirement folded into this vector): one
    file's content contains a literal `\\r\\n` byte pair, so a GREEN
    reading via `path.read_text().encode(...)` (universal-newline
    translation collapsing CRLF to LF) would digest different bytes than
    the raw file and fail this known-answer vector."""
    import hashlib  # noqa: PLC0415
    import struct  # noqa: PLC0415

    from conformance.oracle import freeze  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_bytes(b"world!\r\n")  # [G5:EDGE-4]: literal CRLF byte pair

    paths = [root / "a.txt", root / "sub" / "b.txt"]
    relpaths = sorted(p.relative_to(root).as_posix() for p in paths)
    contents = {rp: (root / rp).read_bytes() for rp in relpaths}

    def u64(n: int) -> bytes:
        return struct.pack("!Q", n)  # [G5:endian]: big-endian, spec-pinned

    stream = b"bdconf-freeze/v1\0" + u64(len(relpaths))
    for rp in relpaths:
        rp_bytes = rp.encode("utf-8")
        content = contents[rp]
        stream += u64(len(rp_bytes)) + rp_bytes + u64(len(content)) + content

    expected = "sha256:" + hashlib.sha256(stream).hexdigest()

    actual = freeze(paths, root=root)
    assert actual == expected, (
        f"golden-vector digest mismatch — expected {expected!r}, got "
        f"{actual!r}. This pins the CONTENT of the freeze byte stream, not "
        f"merely its collision-resistance; any relational-only "
        f"implementation that satisfies AC-F1..F11 without matching the "
        f"documented format will fail here."
    )


def test_ac_f13_freeze_reads_bytes_crlf_and_lf_digest_differently(tmp_path):
    """AC-F13 [G5:EDGE-4]: freeze MUST read content as bytes. A GREEN using
    `path.read_text().encode("utf-8")` applies universal-newline
    translation, so CRLF and LF spellings of the "same" file digest
    identically — the cross-host irreproducibility AC-F12 exists to
    prevent, landing inside the digest itself. Two files, identical except
    for CRLF vs LF line endings (written via write_bytes to guarantee the
    exact bytes on disk, independent of platform text-mode translation),
    MUST produce different digests."""
    from conformance.oracle import freeze  # noqa: PLC0415

    root_crlf = tmp_path / "root_crlf"
    root_crlf.mkdir()
    (root_crlf / "a.txt").write_bytes(b"line one\r\nline two\r\n")

    root_lf = tmp_path / "root_lf"
    root_lf.mkdir()
    (root_lf / "a.txt").write_bytes(b"line one\nline two\n")

    h_crlf = freeze([root_crlf / "a.txt"], root=root_crlf)
    h_lf = freeze([root_lf / "a.txt"], root=root_lf)

    assert h_crlf != h_lf, (
        "freeze must read file content as RAW BYTES, not via a text-mode "
        "read that applies universal-newline translation — a GREEN using "
        "path.read_text().encode('utf-8') would collapse the CRLF and LF "
        "spellings of this 'same' file to an identical digest"
    )


def test_ac_f14_dedupe_on_normalised_relpath_not_object_identity(tmp_path):
    """AC-F14 [G5:EDGE-5] [G6:MINOR-9]: duplicate detection is on the
    NORMALISED RELPATH, not object identity — and the SYMLINK half is
    asserted, not just the "./" half. AC-F8 passes the SAME Path object
    twice. root/"a.txt" and root/"."/"a.txt" are two DIFFERENT Path objects
    (different identity) that normalise to the identical relpath "a.txt".
    Depending on whether GREEN dedupes before or after normalisation
    (e.g. an identity-based check like `id(p) in seen_ids` rather than a
    normalised-relpath set) it either raises OracleFreezeError or silently
    double-counts — and R1.4 makes set membership load-bearing. Normative:
    MUST raise, asserted with this distinct-object/same-relpath pair.

    [G6:MINOR-9]: a GREEN deduping on `Path.resolve()` and one deduping on
    `relative_to(root).as_posix()` AGREE on the "./" case above (both
    resolve/normalise "a.txt" and "./a.txt" to the same thing) but DIFFER
    exactly on a symlink beside its target — `Path.resolve()` follows the
    symlink to the same real path as its target, so a resolve()-based
    dedupe raises here too, while a normalised-relpath-only dedupe (which
    never resolves symlinks) sees two DISTINCT relpaths ("a.txt" and
    "a_link.txt") and would silently pass both through unchanged. Testing
    only the "./" case above leaves this discriminating clause unasserted.
    Both pairs MUST raise."""
    from conformance.oracle import freeze, OracleFreezeError  # noqa: PLC0415

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hi")

    distinct_objects_same_relpath = [root / "a.txt", root / "." / "a.txt"]
    assert distinct_objects_same_relpath[0] is not distinct_objects_same_relpath[1], (
        "sanity: these must be two DISTINCT Path objects (different "
        "identity) — reusing the same object is AC-F8's case, not this one"
    )

    with pytest.raises(OracleFreezeError):
        freeze(distinct_objects_same_relpath, root=root)

    # [G6:MINOR-9]: the SYMLINK half — a symlink beside its target. This is
    # the pair that DISCRIMINATES Path.resolve() (follows the symlink to the
    # target's real path, colliding) from relative_to(root).as_posix() (sees
    # two distinct relpath spellings, "a.txt" and "a_link.txt", and would
    # NOT collide under a normalised-relpath-only dedupe with no symlink
    # awareness at all).
    root_symlink = tmp_path / "root_symlink"
    root_symlink.mkdir()
    target = root_symlink / "a.txt"
    target.write_text("hi")
    link = root_symlink / "a_link.txt"
    link.symlink_to(target)

    with pytest.raises(OracleFreezeError):
        freeze([target, link], root=root_symlink)


# ═══════════════════════════════════════════════════════════════════════════
# §2.3 — evaluate_guarded: the indeterminate guard
# ═══════════════════════════════════════════════════════════════════════════


class _RaisingOracle:
    def freeze(self, paths, *, root):
        return "sha256:" + "0" * 64

    def evaluate(self, state):
        raise RuntimeError("oracle blew up")


class _ImportErrorOracle:
    def freeze(self, paths, *, root):
        return "sha256:" + "0" * 64

    def evaluate(self, state):
        raise ImportError("module for oracle not found")


class _SyntaxErrorOracle:
    def freeze(self, paths, *, root):
        return "sha256:" + "0" * 64

    def evaluate(self, state):
        raise SyntaxError("bad oracle source")


class _SlowOracle:
    def freeze(self, paths, *, root):
        return "sha256:" + "0" * 64

    def evaluate(self, state):
        time.sleep(2.0)
        return None  # would never reach here under a real timeout


class _BoolOracle:
    def freeze(self, paths, *, root):
        return "sha256:" + "0" * 64

    def evaluate(self, state):
        return True  # violates the interface (returns bool, not OracleOutcome)


class _CleanOracle:
    def __init__(self, outcome):
        self._outcome = outcome

    def freeze(self, paths, *, root):
        return "sha256:" + "0" * 64

    def evaluate(self, state):
        return self._outcome


class _KeyboardInterruptOracle:
    def freeze(self, paths, *, root):
        return "sha256:" + "0" * 64

    def evaluate(self, state):
        raise KeyboardInterrupt()


def test_ac_e1_arbitrary_exception_yields_indeterminate_never_rejected():
    """AC-E1: an oracle raising any Exception MUST yield INDETERMINATE, never
    REJECTED."""
    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    outcome, reason = evaluate_guarded(_RaisingOracle(), {"x": 1})

    assert outcome is OracleOutcome.INDETERMINATE
    assert outcome is not OracleOutcome.REJECTED


def test_ac_e2_load_error_yields_indeterminate():
    """AC-E2: ImportError/SyntaxError (load error) MUST yield INDETERMINATE."""
    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    outcome_import, _ = evaluate_guarded(_ImportErrorOracle(), {})
    outcome_syntax, _ = evaluate_guarded(_SyntaxErrorOracle(), {})

    assert outcome_import is OracleOutcome.INDETERMINATE
    assert outcome_syntax is OracleOutcome.INDETERMINATE


def test_ac_e3_timeout_yields_indeterminate():
    """AC-E3: a timeout MUST yield INDETERMINATE. Deliberately wide margin
    (0.1s budget vs 2s sleep) — a timing threshold, not a shared-resource
    race, so no pre-staging is required (workflows.md §1i is about singleton
    resources, not deadline checks)."""
    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    outcome, reason = evaluate_guarded(_SlowOracle(), {}, timeout_s=0.1)

    assert outcome is OracleOutcome.INDETERMINATE
    assert reason  # non-empty per AC-E5


def test_ac_e4_non_outcome_return_yields_indeterminate():
    """AC-E4: an oracle returning something that is not an OracleOutcome
    (including True/False) MUST yield INDETERMINATE — coercing a bool would
    reintroduce the collapse."""
    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    outcome, _ = evaluate_guarded(_BoolOracle(), {})

    assert outcome is OracleOutcome.INDETERMINATE


def test_ac_e5_reason_nonempty_when_indeterminate():
    """AC-E5: the reason string MUST be non-empty whenever the outcome is
    INDETERMINATE."""
    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    for oracle in (_RaisingOracle(), _ImportErrorOracle(), _BoolOracle()):
        outcome, reason = evaluate_guarded(oracle, {})
        assert outcome is OracleOutcome.INDETERMINATE
        assert isinstance(reason, str) and reason.strip() != "", (
            f"expected non-empty reason for {oracle!r}, got {reason!r}"
        )


def test_ac_e6_clean_outcome_passes_through_unchanged():
    """AC-E6: a clean REJECTED/ACCEPTED passes through unchanged with reason
    None."""
    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    rejected_outcome, rejected_reason = evaluate_guarded(
        _CleanOracle(OracleOutcome.REJECTED), {}
    )
    accepted_outcome, accepted_reason = evaluate_guarded(
        _CleanOracle(OracleOutcome.ACCEPTED), {}
    )

    assert rejected_outcome is OracleOutcome.REJECTED
    assert rejected_reason is None
    assert accepted_outcome is OracleOutcome.ACCEPTED
    assert accepted_reason is None


def test_ac_e7_does_not_catch_keyboard_interrupt_or_system_exit():
    """AC-E7: evaluate_guarded MUST NOT catch KeyboardInterrupt/SystemExit."""
    from conformance.oracle import evaluate_guarded  # noqa: PLC0415

    with pytest.raises(KeyboardInterrupt):
        evaluate_guarded(_KeyboardInterruptOracle(), {})

    class _SystemExitOracle:
        def freeze(self, paths, *, root):
            return "sha256:" + "0" * 64

        def evaluate(self, state):
            raise SystemExit(1)

    with pytest.raises(SystemExit):
        evaluate_guarded(_SystemExitOracle(), {})


def test_ac_e8_positive_control_fast_oracle_under_finite_timeout_returns_real_outcome():
    """AC-E8 [G:MAJOR-4a]: positive control for the timeout branch. A fast
    oracle called with a finite timeout_s MUST return its real outcome with
    reason None. Without this, `if timeout_s is not None: return
    INDETERMINATE` would satisfy AC-E3 and every other AC while never
    reaching a verdict at all."""
    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    rejected_outcome, rejected_reason = evaluate_guarded(
        _CleanOracle(OracleOutcome.REJECTED), {}, timeout_s=5.0
    )
    assert rejected_outcome is OracleOutcome.REJECTED, (
        "a fast oracle under a finite timeout must yield its real outcome, "
        "not INDETERMINATE"
    )
    assert rejected_reason is None

    accepted_outcome, accepted_reason = evaluate_guarded(
        _CleanOracle(OracleOutcome.ACCEPTED), {}, timeout_s=5.0
    )
    assert accepted_outcome is OracleOutcome.ACCEPTED
    assert accepted_reason is None


def test_ac_e9_timeout_mechanism_not_signal_based_works_off_main_thread():
    """AC-E9 [G:edge-8]: the timeout mechanism MUST NOT be signal-based —
    evaluate_guarded MUST behave identically (AC-E3 and AC-E8 both hold) when
    called from a non-main thread. signal.alarm/SIGALRM raise
    "signal only works in main thread" off the main thread, which this test
    would surface as an unexpected exception."""
    import threading  # noqa: PLC0415

    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def _run_timeout_case():
        try:
            outcome, reason = evaluate_guarded(_SlowOracle(), {}, timeout_s=0.1)
            results["timeout_outcome"] = outcome
            results["timeout_reason"] = reason
        except BaseException as e:  # noqa: BLE001 — capture across thread boundary
            errors.append(e)

    def _run_fast_case():
        try:
            outcome, reason = evaluate_guarded(
                _CleanOracle(OracleOutcome.ACCEPTED), {}, timeout_s=5.0
            )
            results["fast_outcome"] = outcome
            results["fast_reason"] = reason
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=_run_timeout_case)
    t1.start()
    t1.join(timeout=5)
    t2 = threading.Thread(target=_run_fast_case)
    t2.start()
    t2.join(timeout=5)

    assert not errors, f"evaluate_guarded raised off the main thread: {errors!r}"
    assert results.get("timeout_outcome") is OracleOutcome.INDETERMINATE
    assert results.get("timeout_reason")
    assert results.get("fast_outcome") is OracleOutcome.ACCEPTED
    assert results.get("fast_reason") is None


def test_ac_e10_abandoned_oracle_worker_does_not_hang_shutdown_and_does_not_accumulate():
    """AC-E10 [G5:EDGE-6] [G6:MINOR-5] [G7:self-1]: the abandoned oracle
    MUST NOT be able to hang shutdown, and MUST NOT accumulate. AC-E3/AC-E9
    assert only the VERDICT; nothing requires the timed-out worker to be
    reaped.

    v6's formulation is REPLACED here — it was unmeasurable and partly
    false-failing: it used a 2.5s grace against a 2.0s _SlowOracle sleep, so
    a GREEN with NO reaping logic at all passed because the worker died on
    its own inside the window. Normative instead, asserting what is
    actually achievable:

    (1) Any worker still alive after a grace period MUST have daemon is
        True, so it cannot hang interpreter shutdown. Asserted with an
        oracle whose sleep (5.0s) is deliberately LONGER than the grace
        period this test waits (0.5s), so the worker is GUARANTEED still
        alive when checked — a positive-existence assertion, not a
        best-effort poll for absence.

        [G7:self-1]: v7's own docstring called a stdlib
        `concurrent.futures.ThreadPoolExecutor` "a defensible design" here,
        contradicting this very clause — measured on this host (py3.14),
        `ThreadPoolExecutor` workers are `daemon=False`, and
        `concurrent.futures.thread._python_exit` joins them at interpreter
        exit, so an abandoned worker delays shutdown by its own sleep
        duration, unbounded for an oracle that never returns. The aside is
        WITHDRAWN (SPEC.md v8): a pooled design is admissible only if its
        workers are explicitly daemon threads; the stdlib default pool is
        NOT admissible for evaluate_guarded's timeout path. This clause's
        assertion (below) already catches that — recorded here so the next
        reader does not re-litigate it.
    (2) Workers MUST NOT ACCUMULATE: over N=5 repeated timed-out calls
        (each against an oracle that outlives its own timeout), waited past
        every oracle's own completion, the live-thread count MUST NOT have
        grown by N. This clause is design-agnostic: it holds for BOTH a
        per-call-thread design (each worker exits on its own once its
        sleep elapses) and a pooled design with a BOUNDED pool size (not N
        permanent threads) — only a genuine per-call leak fails it.

    [G7:1] BLOCKING FIX: clause 1's ORIGINAL check required a NEWLY
    CREATED thread (an enumerate()-before/after diff), which a daemonising
    POOL design — declared admissible by [G7:self-1] right above — does
    NOT produce, since it reuses an already-alive idle worker. Worse than
    order-dependent: AC-E3/AC-E8/AC-E9 (and this test's own clause 2, if
    order-dependent at all) call evaluate_guarded with a timeout earlier in
    file order, so a pool's worker would already exist and the diff would
    be empty even under `-p no:randomly` — a false-fail against the very
    design the spec blesses. FIXED: the oracle now records its OWN
    `threading.current_thread()` into a caller-visible list, and the
    liveness check uses THAT thread's `.is_alive()` — provably alive (the
    sleep outlasts the grace) regardless of whether the thread was newly
    created for this call or reused from a pool.

    [G8:B2] BLOCKING FIX: clause 2's ORIGINAL check counted threads
    (`len(surviving_new) < n_calls`), which is the wrong instrument —
    `before_accum` was captured AFTER clause 1's own 5.0s worker was
    already alive, so with a cold `ThreadPoolExecutor`-shaped pool, all
    five submits below spawn FRESH workers that finish their 0.3s sleeps
    and sit IDLE-BUT-ALIVE, giving `len(surviving_new) == 5` and `5 < 5`
    is False — a correct, spec-blessed GREEN fails. Under `pytest-randomly`
    (this suite's default) this test can also run FIRST with a cold pool,
    making the failure unconditional in that ordering. FIXED: dropped the
    count entirely and adopted the AC's own SECOND admissible form —
    `daemon is True` asserted over EVERY alive non-main thread, both right
    after clause 1's guaranteed-alive worker and again after the N-call
    batch. This is design-agnostic (accumulation in a bounded pool is
    harmless as long as nothing it leaves alive can block shutdown) and
    closes a real coverage gap the gate found: a daemon ORACLE worker
    paired with a non-daemon AUXILIARY thread (e.g. a watchdog joining the
    abandoned worker) passed the single-recorded-thread check while still
    hanging shutdown for the abandoned oracle's full duration."""
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    from conformance.oracle import OracleOutcome, evaluate_guarded  # noqa: PLC0415

    class _LongSleepOracle:
        def __init__(self, sleep_s, thread_recorder=None):
            self._sleep_s = sleep_s
            self._thread_recorder = thread_recorder

        def freeze(self, paths, *, root):
            return "sha256:" + "0" * 64

        def evaluate(self, state):
            if self._thread_recorder is not None:
                # [G7:1]: record the ACTUAL thread running this oracle body,
                # whether newly spawned per-call or reused from a pool.
                self._thread_recorder.append(threading.current_thread())
            time.sleep(self._sleep_s)
            return None  # would never reach here under a real timeout

    # --- (1) daemon is True, checked while the worker is GUARANTEED alive.
    grace_period_s = 0.5
    sleep_longer_than_grace_s = 5.0

    recorded_threads: list[threading.Thread] = []
    outcome, _reason = evaluate_guarded(
        _LongSleepOracle(sleep_longer_than_grace_s, thread_recorder=recorded_threads),
        {}, timeout_s=0.1,
    )
    assert outcome is OracleOutcome.INDETERMINATE, "sanity: the timeout must actually fire"
    assert recorded_threads, (
        "sanity: the oracle body must actually have run (and recorded its "
        "own thread) for this check to mean anything"
    )
    worker_thread = recorded_threads[-1]

    time.sleep(grace_period_s)
    assert worker_thread.is_alive(), (
        "sanity: the oracle sleeps 5.0s and only 0.5s elapsed since the "
        "timeout fired, so the thread running evaluate() MUST still be "
        "observably alive for this check to mean anything"
    )

    def _assert_every_alive_non_main_thread_is_daemon(context: str) -> None:
        non_main_alive = [
            t for t in threading.enumerate()
            if t is not threading.main_thread() and t.is_alive()
        ]
        non_daemon = [t for t in non_main_alive if t.daemon is not True]
        assert non_daemon == [], (
            f"[{context}] every thread alive (other than the main thread) "
            f"MUST be a daemon thread, so NONE of them can hang "
            f"interpreter shutdown — found non-daemon thread(s): "
            f"{[t.name for t in non_daemon]!r}. A daemon ORACLE worker "
            f"paired with a non-daemon AUXILIARY thread (e.g. a watchdog "
            f"joining the abandoned worker) would be caught here even "
            f"though the single recorded oracle thread is itself a daemon"
        )

    _assert_every_alive_non_main_thread_is_daemon("clause 1: single guaranteed-alive worker")

    # --- (2) [G8:B2] over N repeated timed-out calls, every thread left
    # alive (whether newly spawned per-call or reused/accumulated from a
    # pool) must still be a daemon — NOT a thread count, which false-fails
    # a cold pool (see the docstring's [G8:B2] note).
    n_calls = 5
    short_sleep_s = 0.3  # each call's oracle outlives its own timeout briefly

    for _ in range(n_calls):
        outcome, _reason = evaluate_guarded(
            _LongSleepOracle(short_sleep_s), {}, timeout_s=0.05
        )
        assert outcome is OracleOutcome.INDETERMINATE

    # Wait well past every call's own oracle completion so a correctly
    # behaving worker (per-call-thread OR pooled) has had time to actually
    # finish its sleep and either exit or settle to idle.
    time.sleep(short_sleep_s + 1.0)

    _assert_every_alive_non_main_thread_is_daemon(f"clause 2: after {n_calls} repeated timed-out calls")


# ═══════════════════════════════════════════════════════════════════════════
# §3 — attestation writer
# ═══════════════════════════════════════════════════════════════════════════

# ADV-1..2 -> BD-L1; ADV-3..6 -> BD-L2; ADV-7,8,10 -> BD-L3; ADV-9 declarative.
_L1_ADVS = ("ADV-1", "ADV-2")
_L2_ADVS = ("ADV-3", "ADV-4", "ADV-5", "ADV-6")
_L3_ADVS = ("ADV-7", "ADV-8", "ADV-10")
_ALL_EXECUTABLE_ADVS = _L1_ADVS + _L2_ADVS + _L3_ADVS  # ADV-9 excluded (declarative)


def _passed(*adv_ids):
    return {a: "passed" for a in adv_ids}


def _l0(*, passed=True, requirements=None, violations=None):
    """Build a synthetic L0Report double (§4.2). The attestation tests here
    exercise only the registry/status/level-computation logic in
    build_attestation_report — they do not run check_bd_l0 (see AC-L0-11 for
    the composed path)."""
    from conformance.bd_l0 import L0Report  # noqa: PLC0415

    if requirements is None:
        requirements = {"R0.1": "passed", "R0.2": "passed", "R0.3": "passed"}
    if violations is None:
        violations = []
    return L0Report(passed=passed, violations=violations, requirements=requirements)


def _build(**kw):
    from conformance.attestation import build_attestation_report  # noqa: PLC0415
    defaults = dict(
        level_claimed="BD-L0",
        results={},
        l0=_l0(),
        engine_version="0.0.0-test",
        adapter_identity={"backend": "agent-sdk", "source": "default"},
        host_identity={"host": "hal-test-host"},
        repo="hal/bytedigger",
        commit="deadbeefcafebabe0000000000000000000000",
        run_id="run-attestation-test",
    )
    defaults.update(kw)
    return build_attestation_report(**defaults)


def test_ac_a1_absent_adversary_reports_not_executed():
    """AC-A1: an adversary absent from the supplied results MUST appear in
    the report with status not_executed — no default-to-passed path."""
    report = _build(level_claimed="BD-L2", results=_passed(*_L1_ADVS))  # ADV-3..6 omitted

    by_id = {a["id"]: a["status"] for a in report["adversaries"]}
    for adv in _L2_ADVS:
        assert by_id[adv] == "not_executed", f"{adv} should be not_executed, got {by_id[adv]!r}"


def test_ac_a2_level_achieved_only_from_passed_with_positive_control():
    """AC-A2: level_achieved is computed only from adversaries with status
    passed. One not_executed in BD-L2's required set holds achieved below
    it; the positive control (same set, that adversary passed) DOES reach
    BD-L2."""
    partial = _passed(*_L1_ADVS, *("ADV-3", "ADV-5", "ADV-6"))  # ADV-4 missing
    report_partial = _build(level_claimed="BD-L2", results=partial)
    assert report_partial["level_achieved"] != "BD-L2"

    # Positive control: add the missing adversary as passed too.
    full = dict(partial)
    full["ADV-4"] = "passed"
    report_full = _build(level_claimed="BD-L2", results=full)
    assert report_full["level_achieved"] == "BD-L2"


def test_ac_a3_empty_results_yields_bd_l0_with_l0_passing():
    """AC-A3: empty results MUST yield level_achieved == "BD-L0" when the L0
    checks pass, and every ADV-1..ADV-8/ADV-10 listed as not_executed."""
    report = _build(level_claimed="BD-L0", results={}, l0=_l0(passed=True))

    assert report["level_achieved"] == "BD-L0"
    by_id = {a["id"]: a["status"] for a in report["adversaries"]}
    for adv in _ALL_EXECUTABLE_ADVS:
        assert by_id[adv] == "not_executed"


def test_ac_a4_failing_l0_yields_null_level_never_bd_l0():
    """AC-A4: failing L0 checks MUST yield level_achieved == null (None),
    never "BD-L0". AC-A22 [G3:MAJOR-2]: report["l0"] MUST equal
    dict(l0.requirements) on this FAILING report — a negative control
    against the literal constant {"R0.1":"passed","R0.2":"passed",
    "R0.3":"passed"}, which would pass this assertion trivially if not
    checked here. AC-A23 [G3:MAJOR-3]: conformant MUST be False when
    level_achieved is None."""
    failing_requirements = {"R0.1": "failed", "R0.2": "passed", "R0.3": "passed"}
    report = _build(
        level_claimed="BD-L3",
        results=_passed(*_ALL_EXECUTABLE_ADVS),
        l0=_l0(passed=False, requirements=failing_requirements),
    )

    assert report["level_achieved"] is None
    assert report["level_achieved"] != "BD-L0"
    assert report["l0"] == failing_requirements, (
        f"report['l0'] must equal the L0Report's own (failing) requirements "
        f"dict, not a fabricated all-passed constant, got {report['l0']!r}"
    )
    assert report["conformant"] is False, (
        "level_achieved is None must fail-close conformant to False"
    )


def test_ac_a5_levels_are_cumulative_l2_without_l1_does_not_achieve_l2():
    """AC-A5: ADV-3..6 all passed with ADV-1 not_executed MUST NOT achieve
    BD-L2 (or BD-L1) — levels are cumulative."""
    results = _passed(*_L2_ADVS)  # ADV-1/ADV-2 (BD-L1) omitted entirely
    report = _build(level_claimed="BD-L2", results=results)

    assert report["level_achieved"] not in ("BD-L2", "BD-L1")


def test_ac_a6_conformant_false_when_claimed_outranks_achieved(tmp_path):
    """AC-A6: conformant MUST be false when level_claimed outranks
    level_achieved; the report is still WRITTEN (not raised/suppressed).

    [G8:M2]: `assert report is not None` cannot fail — `_build()` returns a
    dict or raises, so a `None` return is not a reachable outcome; deleted.
    AC-A6's actual second half — `write_attestation_report` MUST still
    produce the file for a shortfall report — was unasserted in its own
    test. Added: call `write_attestation_report` and require the file to
    exist on disk with the correct (non-conformant) content."""
    from conformance.attestation import write_attestation_report  # noqa: PLC0415

    report = _build(level_claimed="BD-L3", results=_passed(*_L1_ADVS))

    assert report["level_achieved"] != "BD-L3"
    assert report["conformant"] is False

    out_path = tmp_path / "shortfall_attestation.json"
    write_attestation_report(report, out_path)
    assert out_path.exists(), (
        "a report whose level_claimed outranks level_achieved (a "
        "shortfall) MUST still be WRITTEN to disk — a report is evidence, "
        "and refusing to write it would hide the shortfall"
    )
    reparsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert reparsed["conformant"] is False, reparsed


def test_ac_a7_labels_equal_exactly_three_entries_by_value():
    """AC-A7 [G:MAJOR-5]: labels MUST equal exactly these three, asserted by
    value: R1.2: "adapter-observed", R3.1: "host-attested", and R0.2:
    "writes-observed; reads-declared-only" — the third publishes this lot's
    own R0.2-reads gap in the attestation itself, not just the event log."""
    report = _build()

    assert report["labels"] == {
        "R1.2": "adapter-observed",
        "R3.1": "host-attested",
        "R0.2": "writes-observed; reads-declared-only",
    }


def test_ac_a7b_label_derived_from_requirements_r02_not_from_l0_passed():
    """AC-A7b [G4:MINOR-2]: labels["R0.2"] MUST be derived from
    requirements["R0.2"] SPECIFICALLY, NOT from l0.passed — that mislabels a
    report where writes WERE observed but R0.1 failed. Pinned by one case
    with requirements = {"R0.1": "failed", "R0.2": "passed", "R0.3":
    "passed"} expecting "writes-observed; reads-declared-only" (l0.passed
    would be False here — the label must still read the observed write
    channel, not the aggregate pass/fail)."""
    requirements = {"R0.1": "failed", "R0.2": "passed", "R0.3": "passed"}
    l0 = _l0(passed=False, requirements=requirements, violations=["R0.1: something"])
    report = _build(level_claimed="BD-L0", results={}, l0=l0)

    assert report["labels"]["R0.2"] == "writes-observed; reads-declared-only", (
        f"labels['R0.2'] must be derived from requirements['R0.2'] == "
        f"'passed', regardless of l0.passed being False overall — got "
        f"{report['labels']!r}"
    )


def test_ac_a8_missing_producer_identity_raises():
    """AC-A8 [G:MAJOR-8] [G4:1]: the report MUST carry engine_version,
    adapter_identity, host_identity, repo, commit, run_id and a UTC
    timestamp with Z suffix; a missing or empty value for any of them MUST
    raise rather than emit a report with an anonymous producer.

    [G4:1]: these are echoes of the ARGUMENTS, asserted by VALUE, not by
    truthiness. v4 asserted only `assert report["repo"]` etc., so a GREEN
    hardcoding {"repo": "hal/bytedigger", "commit": "0"*40, "run_id":
    "unknown", "engine_version": "unknown", "timestamp": "Z"} ignored all
    six arguments and passed. Every field is asserted equal to a distinctive
    sentinel; level_claimed is asserted for TWO distinct claims (v4 never
    pinned the echo, and level_achieved is claim-independent per AC-A11b, so
    a hardcoded claim round-tripped cleanly); timestamp is asserted by a real
    parse plus a freshness window against datetime.now(timezone.utc) — the
    single character "Z" satisfied v4's endswith("Z") check."""
    from datetime import datetime, timezone  # noqa: PLC0415

    from conformance.attestation import build_attestation_report  # noqa: PLC0415

    _base = dict(
        level_claimed="BD-L0", results={}, l0=_l0(),
        engine_version="0.0.0",
        adapter_identity={"backend": "agent-sdk", "source": "default"},
        host_identity={"host": "h"},
        repo="hal/bytedigger", commit="c" * 40, run_id="run-a8",
    )

    for missing_field, missing_value in (
        ("engine_version", ""),
        ("adapter_identity", {}),
        ("host_identity", {}),
        ("repo", ""),
        ("commit", ""),
        ("run_id", ""),
    ):
        kw = dict(_base)
        kw[missing_field] = missing_value
        with pytest.raises((ValueError, TypeError)):
            build_attestation_report(**kw)

    sentinel_engine_version = "9.9.9-sentinel"
    sentinel_adapter_identity = {"backend": "sentinel-backend", "source": "env"}
    sentinel_host_identity = {"host": "sentinel-host"}
    sentinel_repo = "sentinel/repo-A8"
    sentinel_commit = "a1b2c3d4" + "0" * 32
    sentinel_run_id = "run-A8-sentinel"

    report = _build(
        level_claimed="BD-L0",
        engine_version=sentinel_engine_version,
        adapter_identity=sentinel_adapter_identity,
        host_identity=sentinel_host_identity,
        repo=sentinel_repo,
        commit=sentinel_commit,
        run_id=sentinel_run_id,
    )
    after = datetime.now(timezone.utc)

    assert report["engine_version"] == sentinel_engine_version, report["engine_version"]
    assert report["adapter_identity"] == sentinel_adapter_identity, report["adapter_identity"]
    assert report["host_identity"] == sentinel_host_identity, report["host_identity"]
    assert report["repo"] == sentinel_repo, report["repo"]
    assert report["commit"] == sentinel_commit, report["commit"]
    assert report["run_id"] == sentinel_run_id, report["run_id"]

    # timestamp: real parse (not "endswith('Z')") plus a freshness window —
    # "Z" alone is not a UTC timestamp, and a constant would fail this.
    ts_raw = report["timestamp"]
    assert isinstance(ts_raw, str) and ts_raw.endswith("Z"), ts_raw
    parsed = datetime.fromisoformat(ts_raw[:-1] + "+00:00")
    # [G8:M2]: `assert parsed.tzinfo is not None` deleted — always True
    # after `fromisoformat(ts[:-1] + "+00:00")`, which always attaches a
    # tzinfo; not a reachable failure.
    assert abs((parsed - after).total_seconds()) < 120, (
        f"timestamp {ts_raw!r} (parsed as {parsed!r}) is not within a 120s "
        f"freshness window of the build call (measured at {after!r}) — a "
        f"constant value like the single character 'Z' would fail this"
    )

    # level_claimed echo, asserted by value for TWO distinct claims.
    report_l0 = _build(level_claimed="BD-L0")
    report_l2 = _build(level_claimed="BD-L2")
    assert report_l0["level_claimed"] == "BD-L0", report_l0["level_claimed"]
    assert report_l2["level_claimed"] == "BD-L2", report_l2["level_claimed"]
    assert report_l0["level_claimed"] != report_l2["level_claimed"]


def test_ac_a27_timestamp_utc_ness_confirmed_against_independent_clock_reading(monkeypatch):
    """AC-A27 [G5:EDGE-8] [G6:MINOR-6]: timestamp UTC-ness MUST be
    host-independent, and the UTC-host blind spot is CLOSED here, not
    declared. AC-A8 parses `ts[:-1] + "+00:00"` against a freshness window
    using ONLY `datetime.now(timezone.utc)`; `datetime.now().isoformat() +
    "Z"` (naive LOCAL time mislabelled as UTC) resolves through
    `time.localtime`, so it numerically coincides with that check on a
    UTC-zone host and diverges elsewhere by the local offset.

    [G6:MINOR-6]: v6 merely DECLARED this as an unsolved limitation, but it
    is closable without any new seam: `monkeypatch.setenv("TZ",
    "Etc/GMT-14")` + `time.tzset()` (restored in the finally block) makes a
    naive-local implementation diverge by 14 HOURS on ANY POSIX host,
    INCLUDING a UTC-zone runner — a declared-but-avoidable blind spot is
    still an unmeasured claim, and three lines close it. This test also
    keeps the independent `time.time()` / `datetime.now(timezone.utc)`
    comparison from the base test."""
    import time  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    real_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Etc/GMT-14")  # UTC+14: a 14h offset from any real host's zone
    time.tzset()
    try:
        report = _build(level_claimed="BD-L0")
        epoch_after = time.time()
        now_after = datetime.now(timezone.utc)
    finally:
        if real_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", real_tz)
        time.tzset()

    ts_raw = report["timestamp"]
    assert isinstance(ts_raw, str) and ts_raw.endswith("Z"), ts_raw
    parsed = datetime.fromisoformat(ts_raw[:-1] + "+00:00")
    # [G8:M2]: `assert parsed.tzinfo is not None` deleted — same
    # always-True tautology as the base AC-A8 test.

    epoch_from_time_module = datetime.fromtimestamp(epoch_after, tz=timezone.utc)
    assert abs((parsed - epoch_from_time_module).total_seconds()) < 120, (
        f"timestamp {ts_raw!r} is not within a 120s freshness window of an "
        f"INDEPENDENT UTC reading via time.time() ({epoch_from_time_module!r}) "
        f"— with TZ forced 14h off UTC, a naive `datetime.now().isoformat() "
        f"+ 'Z'` implementation would diverge by ~14h here, on ANY POSIX "
        f"host including a UTC-zone runner"
    )
    assert abs((parsed - now_after).total_seconds()) < 120, (
        f"timestamp {ts_raw!r} is not within a 120s freshness window of "
        f"datetime.now(timezone.utc) ({now_after!r})"
    )


def test_ac_a9_adv9_declarative_never_counted_never_blocking():
    """AC-A9: ADV-9 MUST appear with status declarative and MUST NOT be
    counted as passed when computing BD-L3, nor block it (§8: BD-L3 v1 is
    reachable without it)."""
    results = _passed(*_ALL_EXECUTABLE_ADVS)
    results["ADV-9"] = "failed"  # attestation must ignore this attempt entirely

    report = _build(level_claimed="BD-L3", results=results, l0=_l0(passed=True))

    by_id = {a["id"]: a["status"] for a in report["adversaries"]}
    assert by_id["ADV-9"] == "declarative"
    assert report["level_achieved"] == "BD-L3", (
        "ADV-9 must not block BD-L3 even though it was fed a failing status"
    )


def test_ac_a10_round_trip_recomputes_from_reports_own_l0_block():
    """AC-A10 [G:MAJOR-7c]: the written file MUST parse as JSON and
    re-validate against the same level computation, yielding the identical
    level_achieved — recomputed from the report's OWN l0 block, not from a
    re-supplied argument. [G3:MAJOR-2] MUST round-trip a FAILING report
    through write -> reparse -> recompute too, not only the all-good case."""
    from conformance.attestation import write_attestation_report  # noqa: PLC0415

    import tempfile
    report = _build(level_claimed="BD-L1", results=_passed(*_L1_ADVS))

    with tempfile.TemporaryDirectory() as d:
        out_path = Path(d) / "attestation.json"
        write_attestation_report(report, out_path)

        with open(out_path, "r", encoding="utf-8") as fh:
            reparsed = json.load(fh)

        assert reparsed["level_achieved"] == report["level_achieved"] == "BD-L1"
        assert "l0" in reparsed and reparsed["l0"], "attestation must carry its own l0 block"

        # [G4:2]: the file MUST be the WHOLE report — full dict equality —
        # not just the four keys spot-checked above. A writer serialising a
        # subset (dropping labels/conformant/schema/unsigned/provenance)
        # would still pass the spot-checks; this closes that gap.
        assert reparsed == report, (
            f"the written file must round-trip the ENTIRE report, got a "
            f"partial serialisation. missing keys: "
            f"{set(report) - set(reparsed)!r}, extra keys: "
            f"{set(reparsed) - set(report)!r}"
        )

        # Recompute using the report's OWN l0 block (not a re-supplied
        # l0_passed=True argument) — this is what AC-A10 forces.
        recomputed = _build(
            level_claimed=reparsed["level_claimed"],
            results={a["id"]: a["status"] for a in reparsed["adversaries"] if a["status"] == "passed"},
            l0=_l0(passed=True, requirements=dict(reparsed["l0"])),
        )
        assert recomputed["level_achieved"] == reparsed["level_achieved"]


def test_ac_a10_round_trip_failing_report_through_write_reparse_recompute():
    """AC-A10 [G3:MAJOR-2]: round-trip a FAILING L0Report through write ->
    reparse -> recompute, not only the all-good case. v3 only exercised the
    all-passing path, so a GREEN publishing a fabricated all-good l0 block
    would round-trip identically regardless of the real (failing) input."""
    from conformance.attestation import build_attestation_report, write_attestation_report  # noqa: PLC0415

    import tempfile

    failing_requirements = {"R0.1": "passed", "R0.2": "not-checked", "R0.3": "passed"}
    report = build_attestation_report(
        level_claimed="BD-L0",
        results={},
        l0=_l0(passed=False, requirements=failing_requirements),
        engine_version="0.0.0-test",
        adapter_identity={"backend": "agent-sdk", "source": "default"},
        host_identity={"host": "hal-test-host"},
        repo="hal/bytedigger",
        commit="deadbeefcafebabe0000000000000000000000",
        run_id="run-a10-failing",
    )
    assert report["level_achieved"] is None

    with tempfile.TemporaryDirectory() as d:
        out_path = Path(d) / "attestation.json"
        write_attestation_report(report, out_path)

        with open(out_path, "r", encoding="utf-8") as fh:
            reparsed = json.load(fh)

        assert reparsed["level_achieved"] is None
        assert reparsed["l0"] == failing_requirements, (
            f"the round-tripped l0 block must be the REAL failing "
            f"requirements dict, not a fabricated all-good stand-in, got "
            f"{reparsed['l0']!r}"
        )

        # [G4:2]: full dict equality on the FAILING round-trip too — v4
        # asserted this only on the all-good path.
        assert reparsed == report, (
            f"the written failing report must round-trip in full, got "
            f"missing keys: {set(report) - set(reparsed)!r}, extra keys: "
            f"{set(reparsed) - set(report)!r}"
        )

        recomputed = build_attestation_report(
            level_claimed=reparsed["level_claimed"],
            results={},
            l0=_l0(passed=False, requirements=dict(reparsed["l0"])),
            engine_version="0.0.0-test",
            adapter_identity={"backend": "agent-sdk", "source": "default"},
            host_identity={"host": "hal-test-host"},
            repo="hal/bytedigger",
            commit="deadbeefcafebabe0000000000000000000000",
            run_id="run-a10-failing-recompute",
        )
        assert recomputed["level_achieved"] == reparsed["level_achieved"] is None


def test_ac_a11_failed_adversary_must_not_count_toward_level():
    """AC-A11 [G:MAJOR-1] [G2:3]: a failed adversary MUST NOT count toward a
    level. Positive control: the identical result set with ADV-4 passed MUST
    achieve BD-L2. Without this, a GREEN computing "achieved iff every
    required id is present" (or status != "not_executed") would award BD-L2
    to a host whose ADV-4 failed.

    [G2:3]: uses the L1+L2 set ONLY (ADV-1..ADV-6 passed; ADV-7/ADV-8/ADV-10
    ABSENT) so the measured maximum is genuinely BD-L2 — v2 used the FULL
    executable set here while AC-A9 used the identical measured set with
    level_claimed="BD-L3" and expected "BD-L3", which was only satisfiable
    under an unstated `level_achieved = min(measured, claimed)` cap.
    level_achieved is a measured fact and is NEVER capped by level_claimed
    (see AC-A11b)."""
    l1_l2_only = _passed(*_L1_ADVS, *_L2_ADVS)  # ADV-7/ADV-8/ADV-10 absent
    results_with_failure = dict(l1_l2_only)
    results_with_failure["ADV-4"] = "failed"
    report_failed = _build(level_claimed="BD-L2", results=results_with_failure)
    assert report_failed["level_achieved"] != "BD-L2", (
        "a failed ADV-4 must not count toward BD-L2"
    )

    results_positive = dict(results_with_failure)
    results_positive["ADV-4"] = "passed"
    report_positive = _build(level_claimed="BD-L2", results=results_positive)
    assert report_positive["level_achieved"] == "BD-L2", (
        "positive control: the identical set with ADV-4 passed must reach BD-L2"
    )


def test_ac_a24_failed_adversary_published_as_failed_distinct_from_not_executed():
    """AC-A24 [G4:MINOR-1]: a failed adversary MUST be PUBLISHED as failed.
    AC-A11 pins that failed does not earn a level, and AC-A1 pins absent ->
    not_executed, but nothing pinned the RENDERED status, so a GREEN
    reporting every non-passing adversary as not_executed erased a MEASURED
    failure from the reviewer artifact. Assert by_id["ADV-4"] == "failed" on
    AC-A11's set, plus an explicitly-supplied not_executed case, so the two
    are distinguishable in the report."""
    l1_l2_only = _passed(*_L1_ADVS, *_L2_ADVS)
    results_failed = dict(l1_l2_only)
    results_failed["ADV-4"] = "failed"
    report_failed = _build(level_claimed="BD-L2", results=results_failed)
    by_id_failed = {a["id"]: a["status"] for a in report_failed["adversaries"]}
    assert by_id_failed["ADV-4"] == "failed", (
        f"a MEASURED failure must be published as 'failed', not erased into "
        f"'not_executed', got {by_id_failed['ADV-4']!r}"
    )

    results_absent = dict(l1_l2_only)
    del results_absent["ADV-4"]  # never supplied at all -> not_executed
    report_absent = _build(level_claimed="BD-L2", results=results_absent)
    by_id_absent = {a["id"]: a["status"] for a in report_absent["adversaries"]}
    assert by_id_absent["ADV-4"] == "not_executed", by_id_absent

    assert by_id_failed["ADV-4"] != by_id_absent["ADV-4"], (
        "measured-failure and never-ran must be DISTINGUISHABLE in the "
        "report, not collapsed into the same rendered status"
    )


def test_ac_a11b_level_achieved_independent_of_level_claimed():
    """AC-A11b [G2:3]: level_achieved is independent of level_claimed. The
    IDENTICAL results set MUST produce the IDENTICAL level_achieved for
    every one of the four valid level_claimed values — pinning the rule the
    v2 contradiction (AC-A11 vs AC-A9) was silently mandating a cap against."""
    l1_l2_only = _passed(*_L1_ADVS, *_L2_ADVS)  # measured maximum is BD-L2

    achieved_by_claim = {}
    for claim in ("BD-L0", "BD-L1", "BD-L2", "BD-L3"):
        report = _build(level_claimed=claim, results=l1_l2_only)
        achieved_by_claim[claim] = report["level_achieved"]

    distinct_values = set(achieved_by_claim.values())
    assert len(distinct_values) == 1, (
        f"level_achieved must be identical across every level_claimed value "
        f"for the same results set — got {achieved_by_claim}"
    )
    assert achieved_by_claim["BD-L0"] == "BD-L2", (
        "the measured maximum over this results set is BD-L2, regardless of "
        f"what was claimed: {achieved_by_claim}"
    )


def test_ac_a12_status_outside_vocabulary_raises_value_error():
    """AC-A12 [G:MAJOR-1]: a status outside the vocabulary (passed | failed |
    not_executed | declarative) MUST raise ValueError, never be treated as
    passed and never be silently bucketed."""
    for bad_status in ("skipped", "ok", True, "PASSED"):
        with pytest.raises(ValueError):
            _build(level_claimed="BD-L1", results={"ADV-1": bad_status, "ADV-2": "passed"})


def test_ac_a13_positive_control_conformant_true_when_claimed_equals_achieved():
    """AC-A13 [G:MAJOR-4b]: positive control for conformant. A run where
    level_claimed equals level_achieved MUST yield conformant is True.
    Without this positive control, a constant False would pass AC-A6 and the
    whole file."""
    report = _build(level_claimed="BD-L1", results=_passed(*_L1_ADVS))

    assert report["level_achieved"] == "BD-L1"
    assert report["conformant"] is True


def test_ac_a14_adversaries_list_contains_exactly_ten_known_ids():
    """AC-A14 [G:MINOR-12]: the adversaries list MUST contain exactly the ten
    known ids ADV-1..ADV-10 — no fabricated entry, no dropped id."""
    report = _build(level_claimed="BD-L0", results={})

    ids = {a["id"] for a in report["adversaries"]}
    expected = {f"ADV-{i}" for i in range(1, 11)}
    assert ids == expected, f"expected exactly {expected}, got {ids}"
    assert len(report["adversaries"]) == 10


def test_ac_a15_schema_id_and_unsigned_true():
    """AC-A15 [G:MAJOR-8]: schema MUST equal
    "bytedigger.conformance.attestation/v1" and unsigned MUST be true."""
    report = _build()

    assert report["schema"] == "bytedigger.conformance.attestation/v1"
    assert report["unsigned"] is True


def test_ac_a16_invalid_level_claimed_raises():
    """AC-A16 [G:edge-9]: a level_claimed outside {"BD-L0","BD-L1","BD-L2",
    "BD-L3"} (including "", None, "BD-L4") MUST raise, never rank at zero
    and report conformant: true."""
    for bad_level in ("", None, "BD-L4", "bd-l0", "BD-L99"):
        with pytest.raises((ValueError, TypeError)):
            _build(level_claimed=bad_level, results={})


def test_ac_a17_level_achieved_none_when_l0_requirement_not_checked():
    """AC-A17 [G:MAJOR-7b]: build_attestation_report MUST derive l0 state
    from the supplied L0Report. Passing an L0Report whose R0.1 is
    "not-checked" MUST yield level_achieved is None, even when the caller's
    L0Report.passed flag says True — a level cannot be granted while a third
    of it was never evaluated."""
    conflicting_requirements = {"R0.1": "not-checked", "R0.2": "passed", "R0.3": "passed"}
    conflicting_l0 = _l0(
        passed=True,  # deliberately conflicting with requirements, to force
        # build_attestation_report to inspect .requirements, not just .passed
        requirements=conflicting_requirements,
    )

    report = _build(level_claimed="BD-L0", results={}, l0=conflicting_l0)

    assert report["level_achieved"] is None, (
        "a not-checked R0.1 must yield level_achieved is None regardless of "
        "L0Report.passed"
    )
    assert report["l0"] == conflicting_requirements, (
        f"AC-A22 [G3:MAJOR-2]: report['l0'] must equal the L0Report's own "
        f"(not-checked) requirements dict, got {report['l0']!r}"
    )
    assert report["conformant"] is False, (
        "AC-A23 [G3:MAJOR-3]: level_achieved is None must fail-close "
        "conformant to False"
    )


def test_ac_a18_l0report_passed_and_violations_are_not_ignorable():
    """AC-A18 [G2:2]: L0Report.passed and .violations are not ignorable. An
    L0Report with requirements all "passed" but passed is False, OR with a
    non-empty violations list, MUST yield level_achieved is None. Each of
    the two signals is asserted independently, each with a positive control
    (the same report with that one signal cleared reaches BD-L0). Without
    this, AC-A17's rewrite left a GREEN free to read only .requirements: a
    shadowed run (AC-L0-12) whose violations carry E_SHADOWED_RUN while
    requirements sit at their "passed" default would attest BD-L0."""
    all_passed_requirements = {"R0.1": "passed", "R0.2": "passed", "R0.3": "passed"}

    # Signal 1: passed=False despite requirements all "passed".
    l0_passed_false = _l0(passed=False, requirements=dict(all_passed_requirements), violations=[])
    report_passed_false = _build(level_claimed="BD-L0", results={}, l0=l0_passed_false)
    assert report_passed_false["level_achieved"] is None, (
        "requirements all 'passed' but L0Report.passed is False must still "
        "yield level_achieved is None — .passed is not ignorable"
    )
    assert report_passed_false["l0"] == all_passed_requirements, (
        f"AC-A22 [G3:MAJOR-2]: report['l0'] must equal l0.requirements even "
        f"when .passed independently fails the level, got "
        f"{report_passed_false['l0']!r}"
    )
    assert report_passed_false["conformant"] is False, (
        "AC-A23 [G3:MAJOR-3]: level_achieved is None must fail-close "
        "conformant to False"
    )

    # Positive control 1: same report with .passed cleared to True reaches BD-L0.
    l0_passed_true = _l0(passed=True, requirements=dict(all_passed_requirements), violations=[])
    report_passed_true = _build(level_claimed="BD-L0", results={}, l0=l0_passed_true)
    assert report_passed_true["level_achieved"] == "BD-L0", (
        "positive control: clearing .passed to True (all else identical) "
        "must reach BD-L0"
    )

    # Signal 2: non-empty violations despite requirements all "passed" and passed=True.
    l0_with_violations = _l0(
        passed=True, requirements=dict(all_passed_requirements), violations=["E_SHADOWED_RUN"]
    )
    report_with_violations = _build(level_claimed="BD-L0", results={}, l0=l0_with_violations)
    assert report_with_violations["level_achieved"] is None, (
        "a non-empty .violations list must yield level_achieved is None even "
        "when .passed is True and requirements all read 'passed' — "
        ".violations is not ignorable"
    )
    assert report_with_violations["l0"] == all_passed_requirements, (
        f"AC-A22 [G3:MAJOR-2]: report['l0'] must equal l0.requirements even "
        f"when .violations independently fails the level, got "
        f"{report_with_violations['l0']!r}"
    )
    assert report_with_violations["conformant"] is False, (
        "AC-A23 [G3:MAJOR-3]: level_achieved is None must fail-close "
        "conformant to False"
    )

    # Positive control 2: same report with .violations cleared reaches BD-L0.
    l0_no_violations = _l0(passed=True, requirements=dict(all_passed_requirements), violations=[])
    report_no_violations = _build(level_claimed="BD-L0", results={}, l0=l0_no_violations)
    assert report_no_violations["level_achieved"] == "BD-L0", (
        "positive control: clearing .violations to [] (all else identical) "
        "must reach BD-L0"
    )


def test_ac_a19_conformant_true_when_achieved_outranks_claimed():
    """AC-A19 [G2:3][G2:12]: conformant MUST be true when level_achieved
    OUTRANKS level_claimed — a host claiming less than it measured is not
    non-conformant."""
    l1_l2_only = _passed(*_L1_ADVS, *_L2_ADVS)  # measured maximum is BD-L2

    report = _build(level_claimed="BD-L1", results=l1_l2_only)

    assert report["level_achieved"] == "BD-L2"
    assert report["conformant"] is True, (
        "level_achieved (BD-L2) outranks level_claimed (BD-L1); this is "
        "still conformant, not a shortfall"
    )


def test_ac_a20_input_status_hygiene_fabricated_id_case_variant_and_adv9_out_of_vocab_raise():
    """AC-A20 [G2:edge-3/4]: input-side status hygiene. A fabricated
    adversary id ("ADV-42"), or a case variant ("adv-1"), MUST raise. And
    {"ADV-9": "<out-of-vocabulary>"} MUST raise — AC-A9's declarative
    override MUST NOT pre-empt AC-A12's validation."""
    with pytest.raises(ValueError):
        _build(level_claimed="BD-L1", results={"ADV-42": "passed"})

    with pytest.raises(ValueError):
        _build(level_claimed="BD-L1", results={"adv-1": "passed"})

    with pytest.raises(ValueError):
        _build(level_claimed="BD-L0", results={"ADV-9": "definitely-not-a-real-status"})


def test_ac_a23_conformant_false_for_all_four_level_claimed_values_on_null_achieved_report():
    """AC-A23 [G3:MAJOR-3]: level_achieved is None => conformant is False,
    for EVERY level_claimed value. The AC-A19 rank rewrite made
    `_RANK.get(achieved, 0)` yield conformant: true on a report with
    level_achieved: null and level_claimed: "BD-L0" — the "rank at zero"
    defect AC-A16 names for the claimed side, unguarded here on the achieved
    side. This is the single headline boolean a reviewer reads first."""
    failing_l0 = _l0(passed=False, requirements={"R0.1": "failed", "R0.2": "passed", "R0.3": "passed"})

    for claim in ("BD-L0", "BD-L1", "BD-L2", "BD-L3"):
        report = _build(level_claimed=claim, results={}, l0=failing_l0)
        assert report["level_achieved"] is None, (
            f"sanity: level_achieved must be None for level_claimed={claim!r}"
        )
        assert report["conformant"] is False, (
            f"level_achieved is None must yield conformant is False "
            f"regardless of level_claimed={claim!r}, got "
            f"{report['conformant']!r}"
        )


def test_ac_a21_write_attestation_report_returns_path_and_leaves_no_partial_file():
    """AC-A21 [G2:13][G2:edge-10]: write_attestation_report MUST return the
    Path it wrote, and MUST NOT leave a partial file on disk when
    serialisation fails (a non-JSON-serialisable adapter_identity/
    host_identity must raise with no file, or an intact prior file,
    remaining)."""
    from conformance.attestation import build_attestation_report, write_attestation_report  # noqa: PLC0415

    import tempfile

    report = _build(level_claimed="BD-L0", results={})

    with tempfile.TemporaryDirectory() as d:
        out_path = Path(d) / "attestation.json"
        returned = write_attestation_report(report, out_path)
        assert returned == out_path
        assert isinstance(returned, Path)
        assert out_path.exists()
        # [G4:2]: the written file must be the WHOLE report, not a subset
        # (labels/conformant/schema/unsigned/provenance are all load-bearing
        # per §4 — the written artifact, not the in-memory dict, is what the
        # reviewer receives).
        reparsed = json.loads(out_path.read_text(encoding="utf-8"))
        assert reparsed == report, (
            f"written file must equal the full report; missing keys: "
            f"{set(report) - set(reparsed)!r}, extra keys: "
            f"{set(reparsed) - set(report)!r}"
        )

    class _Unserialisable:
        def __repr__(self):
            return "<not JSON serialisable>"

    with tempfile.TemporaryDirectory() as d2:
        out_path2 = Path(d2) / "attestation.json"
        bad_report = dict(report)
        bad_report["adapter_identity"] = {"backend": _Unserialisable(), "source": "default"}
        with pytest.raises((TypeError, ValueError)):
            write_attestation_report(bad_report, out_path2)
        assert not out_path2.exists(), (
            "a serialisation failure must not leave a partial file on disk"
        )

    with tempfile.TemporaryDirectory() as d3:
        out_path3 = Path(d3) / "attestation.json"
        good_report = _build(level_claimed="BD-L0", results={})
        write_attestation_report(good_report, out_path3)
        prior_bytes = out_path3.read_bytes()

        bad_report2 = dict(report)
        bad_report2["adapter_identity"] = {"backend": _Unserialisable(), "source": "default"}
        with pytest.raises((TypeError, ValueError)):
            write_attestation_report(bad_report2, out_path3)

        assert out_path3.read_bytes() == prior_bytes, (
            "a serialisation failure over an EXISTING path must leave the "
            "prior intact file untouched, not a partial/corrupt overwrite"
        )


def test_ac_a25_write_attestation_report_creates_missing_parent_directories(tmp_path):
    """AC-A25 [G5:EDGE-9]: a missing parent directory MUST NOT silently
    lose the report. Every AC-A21 write goes into an already-created
    directory, leaving it undefined whether the writer creates parents or
    raises. §4 designates the written file as what the reviewer is given,
    so a FileNotFoundError in CI publishes nothing. write_attestation_report
    MUST create missing parents and write, asserted against a NESTED path
    whose parent (and grandparent) do not exist yet."""
    from conformance.attestation import write_attestation_report  # noqa: PLC0415

    report = _build(level_claimed="BD-L0", results={})

    nested_out_path = tmp_path / "does_not_exist_yet" / "nested" / "attestation.json"
    assert not nested_out_path.parent.exists(), (
        "sanity: the parent directory must genuinely not exist before the call"
    )

    returned = write_attestation_report(report, nested_out_path)

    assert returned == nested_out_path
    assert nested_out_path.exists(), (
        "write_attestation_report must create missing parent directories "
        "and write the file, not raise FileNotFoundError"
    )
    reparsed = json.loads(nested_out_path.read_text(encoding="utf-8"))
    assert reparsed == report


def test_ac_a26_l0report_is_immutable():
    """AC-A26 [G5:EDGE-10]: L0Report immutability is asserted, not only
    declared. §4.2 pins @dataclass(frozen=True), but nothing in v5 tested
    it, so a mutable L0Report — which build_attestation_report could
    rewrite between reading .requirements and publishing the l0 block —
    would pass every other test in this file. Attribute assignment MUST
    raise FrozenInstanceError."""
    import dataclasses  # noqa: PLC0415

    report = _l0()

    with pytest.raises(dataclasses.FrozenInstanceError):
        report.passed = not report.passed
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.violations = ["E_INJECTED"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.requirements = {"R0.1": "failed", "R0.2": "failed", "R0.3": "failed"}


# ═══════════════════════════════════════════════════════════════════════════
# §4.1 — Engine-side additions (AC-L0-1..4), driven against a REAL
# WorkflowEngine + real EventLog.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac_l0_1_step_events_carry_phase_and_existing_keys(tmp_path):
    """AC-L0-1: step_started and step_finished payloads MUST carry `phase`
    (the workflow name), alongside the existing keys, unchanged."""
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_1",
        WorkflowDefinition(name="wf_l0_1", steps=[_ok_step("s1", "a"), _ok_step("s2", "b")]),
    )
    eng.execute("wf_l0_1", _make_ctx(), run_id="run-l0-1")

    events = log.read_all()
    started = [e for e in events if e["event_type"] == "step_started"]
    finished = [e for e in events if e["event_type"] == "step_finished"]

    assert started and finished
    for e in started:
        assert e["payload"]["phase"] == "wf_l0_1"
        assert "step_name" in e["payload"]  # existing key untouched
    for e in finished:
        assert e["payload"]["phase"] == "wf_l0_1"
        for existing_key in ("step_name", "status", "duration_ms", "error"):
            assert existing_key in e["payload"]


def test_ac_l0_2_run_identity_emitted_once_immediately_after_workflow_started_by_index(tmp_path):
    """AC-L0-2 [G:MINOR-10]: a new run_identity event MUST be emitted once,
    as the event immediately following workflow_started FOUND BY INDEX of
    that event, not at position 0 — engine.py:250-268 emits
    phase_reroute_entry first whenever phase_reroute is set, so kinds[0] is
    a fixture property, not an engine invariant. This fixture deliberately
    triggers that reroute-entry path so workflow_started is NOT first."""
    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir()
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_2", WorkflowDefinition(name="wf_l0_2", steps=[_ok_step("s1")]))
    ctx = _make_ctx(
        scratchpad_dir=str(scratchpad),
        phase_reroute={"attempt": 1, "from_phase": "prior_phase"},
    )
    eng.execute("wf_l0_2", ctx, run_id="run-l0-2")

    events = log.read_all()
    kinds = [e["event_type"] for e in events]

    assert "phase_reroute_entry" in kinds, (
        "fixture must actually trigger the reroute-entry path so this test "
        "forces index-based lookup, not position 0"
    )
    ws_index = kinds.index("workflow_started")
    assert ws_index != 0, (
        f"fixture must place workflow_started after another event, got kinds={kinds}"
    )
    assert kinds[ws_index + 1] == "run_identity", (
        f"expected run_identity immediately after workflow_started BY INDEX, got {kinds}"
    )
    assert kinds.count("run_identity") == 1, "run_identity must be emitted exactly once"

    run_identity_payload = events[ws_index + 1]["payload"]
    assert run_identity_payload["engine_version"]
    assert run_identity_payload["adapter_identity"]


def test_ac_l0_2d_run_identity_once_per_run_checked_per_run(tmp_path):
    """AC-L0-2d [G6:EDGE-1]: run_identity is once per RUN, asserted PER
    RUN. execute() resets per-run state at engine.py:237-243; a GREEN
    guarding the identity emit with an INSTANCE flag it forgets to reset
    per-run would pass AC-L0-2 and everything else, because the only
    two-runs-on-one-engine fixture (AC-L0-3f) never feeds its log to
    check_bd_l0. Run 2 would then carry no identity and R0.3 would fail for
    a genuinely conformant engine — AC-L0-3f's own defect class applied to
    identity. Two execute() calls on ONE engine under DIFFERENT run_ids,
    checking EACH run: BOTH MUST report R0.3 "passed"."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_2d_run1", WorkflowDefinition(name="wf_l0_2d_run1", steps=[_ok_step("s1")]))
    eng.register("wf_l0_2d_run2", WorkflowDefinition(name="wf_l0_2d_run2", steps=[_ok_step("s1")]))

    eng.execute("wf_l0_2d_run1", _make_ctx(), run_id="run-l0-2d-1")
    eng.execute("wf_l0_2d_run2", _make_ctx(), run_id="run-l0-2d-2")

    events = log.read_all()
    report_run1 = check_bd_l0(events, run_id="run-l0-2d-1", writer=EventLog)
    report_run2 = check_bd_l0(events, run_id="run-l0-2d-2", writer=EventLog)

    assert report_run1.requirements["R0.3"] == "passed", (
        f"run 1 (first execute() call on this engine instance) must carry "
        f"its own run_identity, got {report_run1.requirements!r}"
    )
    assert report_run2.requirements["R0.3"] == "passed", (
        f"run 2 (second execute() call, SAME engine instance, a DIFFERENT "
        f"run_id) must ALSO carry its own run_identity — an instance-level "
        f"'already emitted' flag that is never reset per-run would leave "
        f"run 2 without one, got {report_run2.requirements!r}"
    )


def test_ac_l0_2_minor1_multiple_run_identities_in_one_scoped_run_all_must_satisfy_r03():
    """AC-L0-2 [G7:MINOR-1]: "once per run" is defined as once per
    `execute()` call — one `run_id` legitimately spans MANY `execute()`
    calls (the flagship consumer shape, AC-L0-3a5), so a scoped run will
    contain as many `run_identity` events as it had phases. The checker
    MUST accept `n >= 1` `run_identity` events in one scope and MUST
    require EVERY one to satisfy R0.3 — a per-`execute()` collection,
    asserted per `[G6:quant]` on a NON-UNIFORM two-phase run where
    phase_b's identity is malformed: R0.3 MUST NOT be "passed", with the
    uniform two-identity run as the positive control. One valid identity
    MUST NOT excuse a malformed sibling — `[G6:1]`'s mechanism on the
    identity channel, one level over from AC-L0-3a5's write-tracking
    version. (AC-L0-2d, by contrast, is a uniform fixture across DIFFERENT
    run_ids and does not discharge this — see SPEC.md §4.3's
    cross-reference.)"""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    def _two_phase_two_identities(*, phase_b_engine_version: str) -> list[dict]:
        return [
            _ev("workflow_started", {"workflow_name": "phase_a"}),
            _ev("run_identity", {
                "engine_version": "1.0.0",
                "adapter_identity": {"backend": "claude-subprocess", "source": "default"},
            }),
            _ev("step_started", {"step_name": "s1", "phase": "phase_a"}),
            _ev("step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1, "error": None, "phase": "phase_a"}),
            _ev("phase_artifacts", {
                "phase": "phase_a", "written": [], "read": [],
                "write_tracking": "git-delta", "read_tracking": "declared-only",
            }),
            _ev("workflow_finished", {"workflow_name": "phase_a", "status": "ok", "wall_ms": 1}),
            _ev("workflow_started", {"workflow_name": "phase_b"}),
            _ev("run_identity", {
                "engine_version": phase_b_engine_version,
                "adapter_identity": {"backend": "claude-subprocess", "source": "default"},
            }),
            _ev("step_started", {"step_name": "s1", "phase": "phase_b"}),
            _ev("step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1, "error": None, "phase": "phase_b"}),
            _ev("phase_artifacts", {
                "phase": "phase_b", "written": [], "read": [],
                "write_tracking": "git-delta", "read_tracking": "declared-only",
            }),
            _ev("workflow_finished", {"workflow_name": "phase_b", "status": "ok", "wall_ms": 1}),
        ]

    non_uniform = _two_phase_two_identities(phase_b_engine_version="")
    report = check_bd_l0(non_uniform, run_id="run-l0", writer=EventLog)
    assert report.requirements["R0.3"] != "passed", (
        f"phase_a's run_identity is well-formed but phase_b's carries an "
        f"empty engine_version — one valid identity MUST NOT excuse a "
        f"malformed sibling in the SAME scoped run, got "
        f"{report.requirements!r}"
    )
    assert report.passed is False

    uniform_positive_control = _two_phase_two_identities(phase_b_engine_version="1.0.0")
    report_uniform = check_bd_l0(uniform_positive_control, run_id="run-l0", writer=EventLog)
    assert report_uniform.requirements["R0.3"] == "passed", (
        f"positive control: BOTH phases' run_identity well-formed must "
        f"still pass — the checker must accept n >= 1 run_identity events "
        f"per scope, not exactly one, got {report_uniform.requirements!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC-L0-2c [G5:base] — engine_version provenance survives packaging, no
# placeholder. Resolution order per §4.1: importlib.metadata.version(
# "bytedigger-engine") FIRST (installed-wheel path), THEN a read of
# engine_py/pyproject.toml [project].version (source-checkout path). When
# NEITHER resolves: engine_version absent/empty, R0.3 "not-checked",
# never a placeholder ("unknown"/"0.0.0"/"0+unknown").
#
# SEAM (metadata half): these three tests patch ONLY
# importlib.metadata.version (a stdlib seam that exists today, per the
# spec's own resolution order) for the metadata half — not a new
# engine-internal function name.
#
# SEAM (pyproject-read half) [G5:seam]: the source-checkout read is
# SPEC-PINNED, not assumed — AC-L0-2c [G5:seam] states the read MUST go
# through `Path(<engine_py>/pyproject.toml).read_text()`. The
# both-seams-fail test (c) therefore patches `pathlib.Path.read_text`
# CONDITIONALLY (only when the resolved self equals the real
# engine_py/pyproject.toml path, delegating to the real method
# otherwise) against a stated interface requirement, not a guess. A
# blanket read_text failure would break unrelated engine reads, so the
# patch stays path-conditional.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac_l0_2c_source_checkout_path_resolves_to_real_pyproject_version(tmp_path, monkeypatch):
    """AC-L0-2c [G5:base] (1/3): the SOURCE-CHECKOUT path. With
    importlib.metadata.version forced to raise PackageNotFoundError
    (simulating a dev checkout where "bytedigger-engine" is not installed
    as a distribution), run_identity.engine_version MUST resolve to the
    REAL canonical value in engine_py/pyproject.toml [project].version —
    read HERE from the file (same tomllib/tomli approach as
    test_ac_p1_pyproject_packages_find_include_gains_conformance), never
    hardcoded, so a version bump cannot rot this test."""
    import importlib.metadata as importlib_metadata  # noqa: PLC0415

    try:
        import tomllib  # noqa: PLC0415
    except ImportError:  # pragma: no cover — py<3.11 fallback
        import tomli as tomllib  # type: ignore[no-redef]  # noqa: PLC0415

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    expected_version = data["project"]["version"]
    assert expected_version, "sanity: pyproject.toml must declare a non-empty [project].version"

    def _raise_not_found(name):
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib_metadata, "version", _raise_not_found)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_2c_source", WorkflowDefinition(name="wf_l0_2c_source", steps=[_ok_step("s1")]))
    eng.execute("wf_l0_2c_source", _make_ctx(), run_id="run-l0-2c-source")

    events = log.read_all()
    run_identity_events = [e for e in events if e["event_type"] == "run_identity"]
    assert run_identity_events, (
        f"expected a run_identity event in the log, got event_types="
        f"{[e['event_type'] for e in events]!r}"
    )
    run_identity = run_identity_events[0]["payload"]
    assert run_identity["engine_version"] == expected_version, (
        f"with the metadata seam forced to fail, engine_version must fall "
        f"back to the REAL pyproject.toml [project].version "
        f"({expected_version!r}), got {run_identity.get('engine_version')!r}"
    )


def test_ac_l0_2c_installed_metadata_path_resolves_and_wins_over_pyproject(tmp_path, monkeypatch):
    """AC-L0-2c [G5:base] (2/3): the INSTALLED-METADATA path.
    importlib.metadata.version(<dist name>) is consulted FIRST per §4.1's
    resolution order. Monkeypatch it to return a distinctive sentinel
    ("7.7.7-from-metadata") and assert the emitted engine_version is
    exactly that sentinel — proving metadata is actually consulted and
    WINS, not silently ignored in favour of the pyproject.toml file (whose
    real version, "0.1.1" at time of writing, is deliberately different
    from the sentinel so the two cannot be confused).

    [G7:MINOR-3]: the distribution name is asserted against the imported
    `package_meta.PACKAGE_DIST_NAME` constant, NOT the bare literal
    "bytedigger-engine" — since the constant currently EQUALS that literal,
    asserting the literal cannot discriminate a rename that updates one and
    not the other, leaving [G5:MINOR-7]'s anti-drift MUST with zero
    coverage."""
    import importlib.metadata as importlib_metadata  # noqa: PLC0415

    import package_meta  # noqa: PLC0415

    sentinel_version = "7.7.7-from-metadata"

    def _fake_version(name):
        assert name == package_meta.PACKAGE_DIST_NAME, (
            f"expected the distribution name package_meta.PACKAGE_DIST_NAME "
            f"({package_meta.PACKAGE_DIST_NAME!r}), got {name!r} — a rename "
            f"that updates one spelling and not the other must fail here"
        )
        return sentinel_version

    monkeypatch.setattr(importlib_metadata, "version", _fake_version)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_2c_metadata", WorkflowDefinition(name="wf_l0_2c_metadata", steps=[_ok_step("s1")]))
    eng.execute("wf_l0_2c_metadata", _make_ctx(), run_id="run-l0-2c-metadata")

    events = log.read_all()
    run_identity_events = [e for e in events if e["event_type"] == "run_identity"]
    assert run_identity_events, (
        f"expected a run_identity event in the log, got event_types="
        f"{[e['event_type'] for e in events]!r}"
    )
    run_identity = run_identity_events[0]["payload"]
    assert run_identity["engine_version"] == sentinel_version, (
        f"importlib.metadata.version must WIN over the pyproject.toml file "
        f"when it resolves — expected sentinel {sentinel_version!r}, got "
        f"{run_identity.get('engine_version')!r}"
    )


def test_ac_l0_2c_both_seams_fail_yields_not_checked_and_no_placeholder(tmp_path, monkeypatch):
    """AC-L0-2c [G5:base]/[G5:seam] (3/3): BOTH seams forced to fail => FAIL
    CLOSED. importlib.metadata.version raises PackageNotFoundError AND the
    pyproject.toml read is forced to fail via the spec-pinned [G5:seam]
    mechanism (Path.read_text on the canonical engine_py/pyproject.toml —
    see the SEAM note above this test block). Asserted: check_bd_l0 reports
    requirements["R0.3"] == "not-checked", passed is False, an R0.3-named
    violation — and, critically, NO placeholder string ("unknown"/"0.0.0"/"0+unknown")
    appears anywhere in the run_identity payload. This is the assertion
    that kills the `except: return "unknown"` reflex fix — an attested
    report carrying engine_version: "unknown" looks measured and is
    exactly this lot's disqualifying defect class landing on R0.3."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415
    import importlib.metadata as importlib_metadata  # noqa: PLC0415
    from pathlib import Path as _PathClass  # noqa: PLC0415

    def _raise_not_found(name):
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib_metadata, "version", _raise_not_found)

    pyproject_path = (Path(__file__).resolve().parent.parent / "pyproject.toml").resolve()
    real_read_text = _PathClass.read_text

    def _fail_only_for_pyproject(self, *a, **kw):
        if self.resolve() == pyproject_path:
            raise OSError(
                "simulated: pyproject.toml is build-only metadata, absent "
                "from an installed wheel"
            )
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(_PathClass, "read_text", _fail_only_for_pyproject)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_2c_both_fail", WorkflowDefinition(name="wf_l0_2c_both_fail", steps=[_ok_step("s1")]))
    eng.execute("wf_l0_2c_both_fail", _make_ctx(), run_id="run-l0-2c-both-fail")

    events = log.read_all()
    run_identity = next(e for e in events if e["event_type"] == "run_identity")["payload"]

    payload_text = json.dumps(run_identity)
    for placeholder in ("unknown", "0.0.0", "0+unknown"):
        assert placeholder not in payload_text, (
            f"run_identity payload must carry NO placeholder value when "
            f"both version seams fail — found {placeholder!r} in "
            f"{run_identity!r}. An attested report carrying "
            f"engine_version: {placeholder!r} looks measured and is not."
        )

    report = check_bd_l0(events, run_id="run-l0-2c-both-fail", writer=EventLog)
    assert report.requirements["R0.3"] == "not-checked", (
        f"with neither version seam resolving, R0.3 must fail-closed to "
        f"'not-checked', got {report.requirements!r}"
    )
    assert report.passed is False, (
        "a not-checked R0.3 must fail-close L0Report.passed"
    )
    assert any(v.startswith("R0.3") for v in report.violations), report.violations


def test_ac_a28_version_resolution_not_memoised_across_two_calls_in_one_process(tmp_path, monkeypatch):
    """AC-A28 [G6:MINOR-8]: version resolution MUST NOT be memoised.
    AC-L0-2c's three tests demand three DIFFERENT resolutions from one
    process, so an `@lru_cache` or a module-level `_ENGINE_VERSION =
    _resolve()` would make their pass/fail depend on test ORDER (this suite
    runs pytest-randomly, hence `-p no:randomly` in the RED measurements).
    Resolved TWICE in one process with the metadata seam CHANGED in
    between: two DIFFERENT resolutions MUST be observed, not the first one
    cached and replayed for the second."""
    import importlib.metadata as importlib_metadata  # noqa: PLC0415

    import package_meta  # noqa: PLC0415

    first_sentinel = "1.1.1-a28-first"
    second_sentinel = "2.2.2-a28-second"
    current = {"value": first_sentinel}

    def _fake_version(name):
        # [G7:MINOR-3]: assert against package_meta.PACKAGE_DIST_NAME, not
        # the bare literal — see the AC-L0-2c installed-metadata test.
        assert name == package_meta.PACKAGE_DIST_NAME, name
        return current["value"]

    monkeypatch.setattr(importlib_metadata, "version", _fake_version)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_a28_run1", WorkflowDefinition(name="wf_a28_run1", steps=[_ok_step("s1")]))
    eng.register("wf_a28_run2", WorkflowDefinition(name="wf_a28_run2", steps=[_ok_step("s1")]))

    eng.execute("wf_a28_run1", _make_ctx(), run_id="run-a28-1")

    current["value"] = second_sentinel  # change the seam BETWEEN resolutions
    eng.execute("wf_a28_run2", _make_ctx(), run_id="run-a28-2")

    events = log.read_all()
    run_identities = [e["payload"] for e in events if e["event_type"] == "run_identity"]
    assert len(run_identities) == 2, (
        f"expected exactly one run_identity per execute() call, got "
        f"{len(run_identities)}"
    )
    assert run_identities[0]["engine_version"] == first_sentinel, run_identities[0]
    assert run_identities[1]["engine_version"] == second_sentinel, (
        f"the SECOND resolution must reflect the CHANGED seam, not a "
        f"memoised/cached first value — got {run_identities[1]!r}, "
        f"expected {second_sentinel!r}. An @lru_cache or module-level "
        f"_ENGINE_VERSION = _resolve() would replay {first_sentinel!r} here."
    )


def test_ac_l0_2b_adapter_identity_provenance_tracks_configuration(tmp_path, monkeypatch):
    """AC-L0-2b [G:MAJOR-6]: adapter_identity MUST be {"backend": b,
    "source": s} obtained from llm_subprocess._resolve_backend(kwarg, env),
    and the emitted value MUST TRACK CONFIGURATION — set the backend via the
    env seam (HAL_RUNNER_BACKEND) and assert both backend and source reflect
    it, then change it and assert the emitted value changes. A constant
    "unknown" would pass a non-empty check while never tracking anything.

    [G4:MINOR-4]: delenv ALL THREE spellings — HAL_RUNNER_BACKEND,
    BD_RUNNER_BACKEND, BYTEDIGGER_RUNNER_BACKEND — because
    config_provider._AliasEnviron (config_provider.py:258-288) resolves
    HAL_<X> from the BD_/BYTEDIGGER_ aliases, so a host or CI carrying
    BD_RUNNER_BACKEND would break the source == "default" assertion for an
    environment reason, inside the very test that closes [G:MAJOR-6]."""
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("BD_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("BYTEDIGGER_RUNNER_BACKEND", raising=False)

    log_default = EventLog(tmp_path / "events_default.jsonl")
    eng_default = WorkflowEngine(event_log=log_default)
    eng_default.register("wf_l0_2b_a", WorkflowDefinition(name="wf_l0_2b_a", steps=[_ok_step("s1")]))
    eng_default.execute("wf_l0_2b_a", _make_ctx(), run_id="run-l0-2b-a")

    events_default = log_default.read_all()
    run_identity_default_events = [e for e in events_default if e["event_type"] == "run_identity"]
    assert run_identity_default_events, (
        f"expected a run_identity event in the log, got event_types="
        f"{[e['event_type'] for e in events_default]!r}"
    )
    identity_default = run_identity_default_events[0]["payload"]["adapter_identity"]
    assert identity_default["backend"] == "agent-sdk"
    assert identity_default["source"] == "default"

    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    log_env = EventLog(tmp_path / "events_env.jsonl")
    eng_env = WorkflowEngine(event_log=log_env)
    eng_env.register("wf_l0_2b_b", WorkflowDefinition(name="wf_l0_2b_b", steps=[_ok_step("s1")]))
    eng_env.execute("wf_l0_2b_b", _make_ctx(), run_id="run-l0-2b-b")

    events_env = log_env.read_all()
    run_identity_env_events = [e for e in events_env if e["event_type"] == "run_identity"]
    assert run_identity_env_events, (
        f"expected a run_identity event in the log, got event_types="
        f"{[e['event_type'] for e in events_env]!r}"
    )
    identity_env = run_identity_env_events[0]["payload"]["adapter_identity"]
    assert identity_env["backend"] == "claude-subprocess"
    assert identity_env["source"] == "env"

    assert identity_default != identity_env, (
        "adapter_identity must track configuration — a constant value would "
        "pass a non-empty check while never reflecting the resolver's output"
    )


def test_ac_l0_3_phase_artifacts_emitted_unconditionally_at_phase_exit(tmp_path):
    """AC-L0-3: a new phase_artifacts event MUST be emitted at phase exit
    unconditionally (including when nothing changed), carrying
    {phase, written, read, read_tracking: "declared-only"}."""
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    # Step touches nothing on disk and no git_cwd is configured, so the
    # existing files_touched suppression path never fires here — the only
    # way "nothing was written" becomes a recorded fact is phase_artifacts.
    eng.register("wf_l0_3", WorkflowDefinition(name="wf_l0_3", steps=[_ok_step("noop")]))
    eng.execute("wf_l0_3", _make_ctx(), run_id="run-l0-3")

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]

    assert len(phase_artifacts) == 1, f"expected exactly one phase_artifacts event, got {len(phase_artifacts)}"
    payload = phase_artifacts[0]["payload"]
    # [G3:MINOR-3] whole-dict-shape assertion (not key-by-key): a GREEN that
    # always truncates or silently drops write_tracking cannot pass this.
    assert payload == {
        "phase": "wf_l0_3",
        "written": [],
        "read": [],
        "write_tracking": "not-observed",
        "read_tracking": "declared-only",
    }, payload


def test_ac_l0_3b_written_nonempty_when_something_was_written(tmp_path):
    """AC-L0-3b [G:MAJOR-2]: written MUST be non-empty when something was
    written. A step that creates a file MUST produce a phase_artifacts whose
    written contains that path — a differential against a no-op step in the
    same repo state, so a constant {"written": []} cannot pass both."""
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    def _write_file(_ctx, _prev):
        (repo_dir / "new_file.txt").write_text("hello\n")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="write_step")

    log_write = EventLog(tmp_path / "events_write.jsonl")
    eng_write = WorkflowEngine(event_log=log_write)
    eng_write.register(
        "wf_l0_3b_write",
        WorkflowDefinition(name="wf_l0_3b_write", steps=[StepContract(name="write_step", execute=_write_file)]),
    )
    eng_write.execute("wf_l0_3b_write", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3b-write")

    events_write = log_write.read_all()
    phase_artifacts_write = [e for e in events_write if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts_write) == 1
    written = phase_artifacts_write[0]["payload"]["written"]
    assert "new_file.txt" in written, f"written must record the actually-written path, got {written}"

    # Differential control: a no-op step in the same repo must yield an
    # empty written list — proves `written` is not a constant non-empty stub.
    log_noop = EventLog(tmp_path / "events_noop.jsonl")
    eng_noop = WorkflowEngine(event_log=log_noop)
    eng_noop.register("wf_l0_3b_noop", WorkflowDefinition(name="wf_l0_3b_noop", steps=[_ok_step("noop")]))
    eng_noop.execute("wf_l0_3b_noop", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3b-noop")

    events_noop = log_noop.read_all()
    phase_artifacts_noop = [e for e in events_noop if e["event_type"] == "phase_artifacts"]
    assert phase_artifacts_noop[0]["payload"]["written"] == []


def test_ac_l0_3b2_written_is_union_of_every_steps_delta_not_last_step_only(tmp_path):
    """AC-L0-3b2 [G5:accum]: written is the UNION of every step's delta for
    the phase, never the last step's. v5 asserted written content only over
    ONE-step workflows, so `self._phase_written = paths` (assign) and
    `self._phase_written.update(paths)` (accumulate) were indistinguishable
    — and AC-L0-3f actively REWARDS the assign form, since assignment
    satisfies "run 2 must not contain run 1's paths" with no per-run reset
    at all. Both halves required, over a >=2-step phase with git_cwd on a
    real repo.

    (1) Union: step1 writes early.txt, step2 writes late.txt =>
        set(written) == {"early.txt", "late.txt"}, write_tracking ==
        "git-delta".
    (2) THE DISCRIMINATING HALF: step1 writes early2.txt, step2 writes
        NOTHING => written MUST still contain early2.txt. A last-step-only
        (assign) implementation yields written: [] here alongside
        write_tracking: "git-delta" — R0.2 attested "passed",
        labels["R0.2"] reading "writes-observed", level_achieved:
        "BD-L0", conformant: true, for a phase that DID write. This is the
        [G2:4] defect class reopened at the accumulation seam, and the one
        the flagship multi-step consumer hits on every real phase."""
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    def _write_step(step_name: str, fname: str):
        def _run(_ctx, _prev):
            (repo_dir / fname).write_text("x\n")
            return StepResult(status="ok", data=None, duration_ms=0, step_name=step_name)
        return StepContract(name=step_name, execute=_run)

    # (1) Union across two writing steps.
    log_union = EventLog(tmp_path / "events_union.jsonl")
    eng_union = WorkflowEngine(event_log=log_union)
    eng_union.register(
        "wf_l0_3b2_union",
        WorkflowDefinition(
            name="wf_l0_3b2_union",
            steps=[
                _write_step("write_early", "early.txt"),
                _write_step("write_late", "late.txt"),
            ],
        ),
    )
    eng_union.execute("wf_l0_3b2_union", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3b2-union")

    events_union = log_union.read_all()
    artifacts_union = [e for e in events_union if e["event_type"] == "phase_artifacts"]
    assert artifacts_union, (
        f"expected a phase_artifacts event, got event_types="
        f"{[e['event_type'] for e in events_union]!r}"
    )
    payload_union = artifacts_union[0]["payload"]
    assert payload_union["write_tracking"] == "git-delta", payload_union
    assert set(payload_union["written"]) == {"early.txt", "late.txt"}, (
        f"written must be the UNION of both steps' deltas, not just the "
        f"last step's, got {payload_union['written']!r}"
    )

    # (2) THE DISCRIMINATING HALF: step1 writes, step2 writes nothing.
    log_partial = EventLog(tmp_path / "events_partial.jsonl")
    eng_partial = WorkflowEngine(event_log=log_partial)
    eng_partial.register(
        "wf_l0_3b2_partial",
        WorkflowDefinition(
            name="wf_l0_3b2_partial",
            steps=[_write_step("write_early2", "early2.txt"), _ok_step("noop_last_step")],
        ),
    )
    eng_partial.execute("wf_l0_3b2_partial", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3b2-partial")

    events_partial = log_partial.read_all()
    artifacts_partial = [e for e in events_partial if e["event_type"] == "phase_artifacts"]
    assert artifacts_partial, (
        f"expected a phase_artifacts event, got event_types="
        f"{[e['event_type'] for e in events_partial]!r}"
    )
    payload_partial = artifacts_partial[0]["payload"]
    assert "early2.txt" in payload_partial["written"], (
        f"[G5:accum] DISCRIMINATING ASSERTION: an early step's write MUST "
        f"survive to the final phase_artifacts even when the LAST step "
        f"writes nothing. A last-step-only (assign, not accumulate) "
        f"implementation yields written=[] here while write_tracking "
        f"stays 'git-delta' — publishing 'we measured the write channel "
        f"and nothing was written' for a phase that DID write. Got "
        f"written={payload_partial['written']!r}"
    )


def test_ac_l0_3b3_written_accumulates_across_validation_retry_recursion(tmp_path):
    """AC-L0-3b3 [G5:EDGE-1]: accumulation survives the validation-retry
    recursion. _execute_steps is re-entered recursively at engine.py:638-645
    with start_step=red_index, and the outer frame's `return retry_result`
    at :657 skips its own tail entirely — so any write-accumulator scoped to
    ONE _execute_steps stack frame (rather than threaded across the
    recursion, e.g. on the engine instance) loses whatever the pre-retry
    step wrote. Reuses the AC-L0-3c retry fixture shape, but WITH git_cwd
    set: the pre-retry step (step0) writes a file before returning the
    retry-triggering error; that path MUST appear in the FINAL
    phase_artifacts.written. Distinct seam from AC-L0-3b2 (union across
    steps within one linear pass) — this is union across nested frames."""
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)
    call_counts = {"step0": 0}

    def step0_execute(_ctx, _prev):
        call_counts["step0"] += 1
        (repo_dir / "pre_retry.txt").write_text("written before the retry-triggering error\n")
        return StepResult(
            status="error",
            data={"retry_from_step": 1, "cycle_count": 1, "findings": "f"},
            duration_ms=0,
            step_name="step0",
            error_code="E_RETRY",
            recoverable=True,
        )

    workflow = WorkflowDefinition(
        name="wf_l0_3b3_retry",
        steps=[
            StepContract(name="step0", execute=step0_execute),
            _ok_step("step1_probe"),
        ],
    )

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3b3_retry", workflow)
    result, _ctx = eng.execute(
        "wf_l0_3b3_retry", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3b3-retry"
    )

    assert result.status == "ok"
    assert call_counts["step0"] == 1, "sanity: retry recursion re-enters at step1, not step0"

    events = log.read_all()
    assert any(e["event_type"] == "iteration_started" for e in events), (
        "fixture must actually take the validation-retry path (engine.py:589 "
        "emits iteration_started on the recursive re-entry)"
    )
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        f"expected exactly one phase_artifacts across the retry recursion, "
        f"got {len(phase_artifacts)}"
    )
    payload = phase_artifacts[0]["payload"]
    assert payload["write_tracking"] == "git-delta", payload
    assert "pre_retry.txt" in payload["written"], (
        f"[G5:EDGE-1]: the pre-retry step's write must survive the "
        f"recursive re-entry into _execute_steps (engine.py:638-645) — a "
        f"per-frame (rather than per-instance) write accumulator loses it "
        f"since the outer frame returns retry_result directly at :657 "
        f"without running its own tail. Got written={payload['written']!r}"
    )


def test_ac_l0_3b4_written_relpath_is_posix_against_scan_root_for_nested_paths(tmp_path):
    """AC-L0-3b4 [G6:EDGE-4] [G7:MINOR-7]: the relpath spelling is asserted
    for NESTED paths — both halves the AC names: exact string equality AND
    the digest recomputed over that spelling. Every existing write fixture
    writes to the repo root, so written/written_digest are only ever
    exercised on bare filenames — a GREEN emitting absolute paths,
    "./"-prefixed paths, or OS-separator paths for files in subdirectories
    would pass every other test in this file while making written_digest
    non-reproducible for any real phase that writes into a subtree (the
    normal case).

    [G7:MINOR-7]: `written` is quantified over path entries, and the prior
    fixture was UNIFORM IN DEPTH — all 300 paths sat under `sub/dir/`. A
    producer that special-cases depth (correct at root, wrong once nested,
    or the reverse) was untested in the combined case, since the loop over
    `sample` checked membership against a set of otherwise-identical-shaped
    names. Fixed: the fixture now writes THREE DISTINCT DEPTHS in the same
    phase — `root.txt` (repo root), `sub/one.txt`, `sub/dir/nested.txt` —
    alongside enough filler files (still at the `sub/dir/` depth) to keep
    forcing truncation as before.

    The per-depth guarantee is discharged by two assertions already
    present below, now made load-bearing by the depth mix rather than by
    any new per-name check: (1) the per-entry universal loop
    (`for p in sample: assert p in expected_paths`), which kills an
    absolute, `./`-prefixed, or backslash spelling AT ANY of the three
    depths now that the fixture actually spans three of them; and (2) the
    `written_digest` equality, recomputed over the FULL sorted three-depth
    list — which a depth-conditional spelling bug cannot survive, because
    the digest covers the whole list, including whichever depths the
    BOUNDED SAMPLE happens to omit. The spec does not pin which entries a
    bounded sample contains, so a sample-only per-name assertion would
    either be vacuous (if it degenerates to checking the test's own input
    list) or false-failing (if it required a specific path to be present
    in `sample`); the digest is what makes the per-depth spelling
    provable regardless of sampling.

    Enough files are written to force truncation (as AC-L0-3d does), so
    written_digest is present and can be checked, by EQUALITY, against the
    real full sorted list of mixed-depth relpaths. No byte-boundary
    arithmetic is involved here (unlike AC-L0-3d2/3d3/3d4/3d5) — this test
    forces truncation by file COUNT, not by hitting an exact byte target,
    so mixing depths changes no computed number."""
    import hashlib  # noqa: PLC0415

    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)
    n_filler_files = 300

    # [G7:MINOR-7]: three distinct depths, named so they cannot be confused
    # with the filler template's shape.
    root_depth_path = "root.txt"
    mid_depth_path = "sub/one.txt"
    nested_depth_path = "sub/dir/nested.txt"

    def _write_mixed_depths_and_filler(_ctx, _prev):
        (repo_dir / root_depth_path).write_text("x")
        (repo_dir / "sub").mkdir(parents=True, exist_ok=True)
        (repo_dir / mid_depth_path).write_text("x")
        nested_dir = repo_dir / "sub" / "dir"
        nested_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / nested_depth_path).write_text("x")
        for i in range(n_filler_files):
            (nested_dir / f"nested_filler_{i:05d}.txt").write_text("x")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="write_mixed_depths")

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_3b4",
        WorkflowDefinition(name="wf_l0_3b4", steps=[StepContract(name="write_mixed_depths", execute=_write_mixed_depths_and_filler)]),
    )
    result, _ctx = eng.execute("wf_l0_3b4", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3b4")
    assert result.status == "ok"

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1
    payload = phase_artifacts[0]["payload"]
    assert payload["write_tracking"] == "git-delta", payload

    filler_paths = [f"sub/dir/nested_filler_{i:05d}.txt" for i in range(n_filler_files)]
    expected_paths = sorted([root_depth_path, mid_depth_path, nested_depth_path, *filler_paths])
    assert len({root_depth_path.count("/"), mid_depth_path.count("/"), nested_depth_path.count("/")}) == 3, (
        "sanity: the three named paths must genuinely sit at THREE "
        "DISTINCT depths (0, 1 and 2 path separators)"
    )

    sample = payload["written"]
    assert sample, "expected a non-empty bounded sample of written paths"
    for p in sample:
        assert p in expected_paths, (
            f"written entry {p!r} is not an exact POSIX relpath against the "
            f"scan root — with THREE genuinely different depth shapes now "
            f"in play (root.txt / sub/one.txt / sub/dir/nested_*.txt), an "
            f"absolute path, a './'-prefixed path, or an OS-separator "
            f"('sub\\\\dir\\\\...') path at ANY one depth would fail this"
        )

    expected_digest = "sha256:" + hashlib.sha256(
        "\n".join(expected_paths).encode("utf-8")
    ).hexdigest()
    assert payload.get("written_digest") == expected_digest, (
        f"written_digest must be recomputed over the FULL sorted MIXED-"
        f"DEPTH relpath list in the SAME spelling `written` uses — "
        f"including root.txt, sub/one.txt and sub/dir/nested.txt as well "
        f"as the filler paths — got {payload.get('written_digest')!r}, "
        f"expected {expected_digest!r}"
    )

    report = check_bd_l0(events, run_id="run-l0-3b4", writer=EventLog)
    assert report.passed is True, getattr(report, "violations", report)


def test_ac_l0_3c_retry_path_still_yields_exactly_one_phase_artifacts(tmp_path):
    """AC-L0-3c [G:MINOR-7] (1/2): the emit host is WorkflowEngine.execute,
    NOT _execute_steps — which is re-entered recursively on the
    validation-retry path. A workflow that takes the retry path MUST still
    yield exactly one phase_artifacts."""
    call_counts = {"step0": 0}

    def step0_execute(_ctx, _prev):
        call_counts["step0"] += 1
        return StepResult(
            status="error",
            data={"retry_from_step": 1, "cycle_count": 1, "findings": "f"},
            duration_ms=0,
            step_name="step0",
            error_code="E_RETRY",
            recoverable=True,
        )

    workflow = WorkflowDefinition(
        name="wf_l0_3c_retry",
        steps=[
            StepContract(name="step0", execute=step0_execute),
            _ok_step("step1_probe"),
        ],
    )

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3c_retry", workflow)
    result, _ctx = eng.execute("wf_l0_3c_retry", _make_ctx(), run_id="run-l0-3c-retry")

    assert result.status == "ok"
    assert call_counts["step0"] == 1, "sanity: retry recursion re-enters at step1, not step0"

    events = log.read_all()
    assert any(e["event_type"] == "iteration_started" for e in events), (
        "fixture must actually take the validation-retry path"
    )
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        f"a workflow that takes the validation-retry path must still yield "
        f"exactly one phase_artifacts, got {len(phase_artifacts)}"
    )


def test_ac_l0_3c_zero_step_workflow_still_yields_exactly_one_phase_artifacts(tmp_path):
    """AC-L0-3c [G:MINOR-7] (2/2): _execute_steps returns early for a
    zero-step workflow (engine.py:355-361); a zero-step workflow MUST still
    yield one phase_artifacts.

    [G4:4] AC-L0-3a4(2): driven WITH org_config["git_cwd"] set to a real
    repo. `_scan_cwd` is resolved at engine.py:366, AFTER the zero-step early
    return at :361 — so nothing is ever scanned. `all([])` is True, so a
    literal reading of the "every step" quantifier would (wrongly) mandate
    "git-delta" here. Normative: write_tracking MUST be "not-observed" and
    check_bd_l0 MUST report R0.2 as "not-checked" for a zero-step phase, even
    with git_cwd genuinely pointing at a real repo."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3c_zero", WorkflowDefinition(name="wf_l0_3c_zero", steps=[]))
    result, _ctx = eng.execute(
        "wf_l0_3c_zero", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3c-zero"
    )

    assert result.status == "ok"

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        f"a zero-step workflow must still emit exactly one phase_artifacts "
        f"(the emit host is execute(), not _execute_steps' early return), "
        f"got {len(phase_artifacts)}"
    )
    payload = phase_artifacts[0]["payload"]
    assert payload.get("write_tracking") == "not-observed", (
        f"a zero-step phase scans nothing even with git_cwd set — "
        f"all([]) is True, so an 'every step' implementation that reduces "
        f"vacuously would (wrongly) publish 'git-delta' here; got {payload!r}"
    )

    report = check_bd_l0(events, run_id="run-l0-3c-zero", writer=EventLog)
    assert report.requirements["R0.2"] == "not-checked", report.requirements


def test_ac_l0_3a4_partial_delta_failure_on_second_step_yields_not_observed(tmp_path):
    """AC-L0-3a4(1) [G4:4]: an any()-shaped implementation ("git-delta" if
    SOME step computed a delta) would publish "git-delta" for a phase where
    a LATER step's git read failed (engine.py:1082, a real runtime path) —
    a step window that was never scanned, attested as measured. A two-step
    phase, git_cwd pointing at a real repo, with the git delta forced to
    fail on the SECOND step only (injected through the lib.git_port
    get_git_read() seam — the suite's established pattern, see
    test_gh1082_engine_scan_cwd.py's set_default_git_read_factory usage)
    MUST yield write_tracking: "not-observed" and requirements["R0.2"] ==
    "not-checked" — not "git-delta" from step 1's successful delta alone."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415
    from lib.git_port import (  # noqa: PLC0415
        GitResult,
        reset_default_git_read_factory,
        set_default_git_read_factory,
    )

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    # Real git_read for the first 4 calls (step1's git_pre + git_post, each
    # of which issues 2 calls: `diff --name-status HEAD` and
    # `ls-files --others --exclude-standard`, per engine.py:1076/:1079) —
    # then FAIL every call from the 5th onward (step2's git_pre), so step2's
    # window is genuinely never scanned.
    from lib.git_port import default_git_read  # noqa: PLC0415

    real = default_git_read()
    call_count = {"n": 0}

    class _FailAfterFourCallsSpy:
        def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
            call_count["n"] += 1
            if call_count["n"] <= 4:
                return real(args, cwd=cwd, timeout=timeout, dir_=dir_)
            return GitResult(returncode=1, stdout="", stderr="simulated git failure", timed_out=False)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_3a4_partial",
        WorkflowDefinition(
            name="wf_l0_3a4_partial",
            steps=[_ok_step("step1"), _ok_step("step2")],
        ),
    )
    try:
        set_default_git_read_factory(lambda: _FailAfterFourCallsSpy())
        result, _ctx = eng.execute(
            "wf_l0_3a4_partial", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3a4-partial"
        )
    finally:
        reset_default_git_read_factory()

    assert result.status == "ok"
    assert call_count["n"] > 4, (
        "sanity: step2's git_pre must actually have been attempted (and "
        "made to fail) for this test to force the failure branch"
    )

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1
    payload = phase_artifacts[0]["payload"]
    assert payload.get("write_tracking") == "not-observed", (
        f"step1's delta succeeded but step2's failed (returncode=1) — an "
        f"any()-shaped 'every step' implementation would wrongly publish "
        f"'git-delta' from step1 alone; got {payload!r}"
    )

    report = check_bd_l0(events, run_id="run-l0-3a4-partial", writer=EventLog)
    assert report.requirements["R0.2"] == "not-checked", report.requirements


def test_ac_l0_3d_oversized_artifact_list_does_not_vanish(tmp_path):
    """AC-L0-3d [G:edge-2]: EventLog.append raises EventLogLineTooLarge above
    4096 bytes and _emit swallows it, so a phase writing many files would
    silently lose its record. When the payload would exceed the limit,
    phase_artifacts MUST instead carry written_truncated: true,
    written_count: <n>, written_digest: "sha256:...", and a bounded written
    sample — asserted with a step writing enough paths to exceed 4096 bytes.

    [G4:5]: written_digest is a MEASURED value, not a shape — computed here
    as sha256 over the full sorted path list joined by newline (the same
    relpaths `written` uses: untracked new files surface via
    `git ls-files --others --exclude-standard` bare filenames, matching
    AC-L0-3b's "new_file.txt" spelling), asserted by EQUALITY — v4 asserted
    only startswith("sha256:"), so a constant "sha256:"+"0"*64 passed.
    [G4:MINOR-5]: the payload assertion is whole-dict-shape (exact key set +
    every fixed value), not key-by-key .get() probes; the sample list is
    handled explicitly (bounded subset of the real path set)."""
    import hashlib  # noqa: PLC0415

    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    n_files = 300

    def _write_many(_ctx, _prev):
        for i in range(n_files):
            (repo_dir / f"generated_artifact_file_number_{i:05d}.txt").write_text("x")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="write_many")

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_3d",
        WorkflowDefinition(name="wf_l0_3d", steps=[StepContract(name="write_many", execute=_write_many)]),
    )
    result, _ctx = eng.execute("wf_l0_3d", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3d")

    assert result.status == "ok"

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        "an oversize written-paths payload MUST NOT be silently dropped by "
        "the EventLog line-size guard (EventLogLineTooLarge swallowed at "
        "engine.py:710)"
    )
    payload = phase_artifacts[0]["payload"]

    # [G4:MINOR-5] whole-dict-shape: the exact key set, not key-by-key probes.
    assert set(payload.keys()) == {
        "phase", "written", "read", "write_tracking", "read_tracking",
        "written_truncated", "written_count", "written_digest",
    }, payload.keys()
    assert payload["phase"] == "wf_l0_3d"
    assert payload["read"] == []
    assert payload["write_tracking"] == "git-delta"
    assert payload["read_tracking"] == "declared-only"
    assert payload["written_truncated"] is True
    assert payload["written_count"] == n_files

    expected_paths = sorted(
        f"generated_artifact_file_number_{i:05d}.txt" for i in range(n_files)
    )
    expected_digest = "sha256:" + hashlib.sha256(
        "\n".join(expected_paths).encode("utf-8")
    ).hexdigest()
    assert payload["written_digest"] == expected_digest, (
        f"[G4:5]: written_digest must EQUAL the digest computed over the "
        f"REAL full sorted path set, got {payload['written_digest']!r}, "
        f"expected {expected_digest!r} — a constant 'sha256:'+'0'*64 must "
        f"fail this"
    )

    sample = payload["written"]
    assert isinstance(sample, list) and 0 < len(sample) < n_files, (
        f"expected a bounded sample smaller than the real count, got {len(sample) if sample else sample}"
    )
    assert set(sample).issubset(set(expected_paths)), (
        f"the bounded sample must be drawn from the real path set, got "
        f"unexpected entries: {set(sample) - set(expected_paths)!r}"
    )

    report = check_bd_l0(events, run_id="run-l0-3d", writer=EventLog)
    assert report.passed is True, getattr(report, "violations", report)


def test_ac_l0_4_new_emits_go_through_emit_never_raise_contract(tmp_path):
    """AC-L0-4: the run_identity and phase_artifacts emits MUST go through
    _emit, so the never-raise contract keeps working unchanged — a log whose
    .append() always raises must not break execution."""

    class _AlwaysBrokenLog:
        path = None

        def append(self, event_type, payload, run_id=None):
            raise RuntimeError(f"disk full while appending {event_type}")

    eng = WorkflowEngine(event_log=_AlwaysBrokenLog())
    eng.register("wf_l0_4", WorkflowDefinition(name="wf_l0_4", steps=[_ok_step("s1", "ok-data")]))

    result, _ctx = eng.execute("wf_l0_4", _make_ctx(), run_id="run-l0-4")

    assert result.status == "ok"
    assert result.data == "ok-data"


# ═══════════════════════════════════════════════════════════════════════════
# §4.2 — bd_l0.check_bd_l0 checker
# ═══════════════════════════════════════════════════════════════════════════


def _ev(event_type: str, payload: dict, run_id: str = "run-l0") -> dict:
    return {
        "ts": "2026-07-27T00:00:00.000Z",
        "run_id": run_id,
        "event_type": event_type,
        "payload": payload,
    }


def _valid_l0_events() -> list[dict]:
    """Baseline valid log. AC-L0-14 [G2:8]: adapter_identity MUST be the dict
    shape the engine actually emits ({"backend", "source"}) — a bare string
    (v2's fixture) pins the checker to a weaker contract than AC-L0-2b
    requires. AC-L0-3a [G2:4]: phase_artifacts carries write_tracking;
    "git-delta" is the baseline "measured" case."""
    return [
        _ev("workflow_started", {"workflow_name": "wf"}),
        _ev("run_identity", {
            "engine_version": "1.0.0",
            "adapter_identity": {"backend": "claude-subprocess", "source": "default"},
        }),
        _ev("step_started", {"step_name": "s1", "phase": "wf"}),
        _ev("step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1, "error": None, "phase": "wf"}),
        _ev("phase_artifacts", {
            "phase": "wf", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }),
        _ev("workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 1}),
    ]


def _valid_l0_events_two_phases() -> list[dict]:
    """AC-L0-13 [G2:edge-8]: a run with TWO phases, each with its own
    workflow_started/workflow_finished/phase_artifacts triple, sharing one
    run_id — "exactly one phase_artifacts per phase" only means something
    when there is more than one phase to distinguish."""
    return [
        _ev("workflow_started", {"workflow_name": "phase_a"}),
        _ev("run_identity", {
            "engine_version": "1.0.0",
            "adapter_identity": {"backend": "claude-subprocess", "source": "default"},
        }),
        _ev("step_started", {"step_name": "s1", "phase": "phase_a"}),
        _ev("step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1, "error": None, "phase": "phase_a"}),
        _ev("phase_artifacts", {
            "phase": "phase_a", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }),
        _ev("workflow_finished", {"workflow_name": "phase_a", "status": "ok", "wall_ms": 1}),
        _ev("workflow_started", {"workflow_name": "phase_b"}),
        _ev("step_started", {"step_name": "s1", "phase": "phase_b"}),
        _ev("step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1, "error": None, "phase": "phase_b"}),
        _ev("phase_artifacts", {
            "phase": "phase_b", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }),
        _ev("workflow_finished", {"workflow_name": "phase_b", "status": "ok", "wall_ms": 1}),
    ]


def test_ac_l0_5_r0_1_behavioural_prefix_immutability(tmp_path):
    """AC-L0-5: R0.1 behavioural — append N events, snapshot the file bytes,
    append M more; the first snapshot MUST be a byte-exact prefix of the
    final file."""
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)

    for i in range(3):
        log.append("step_started", {"step_name": f"s{i}"}, run_id="r")

    snapshot = log_path.read_bytes()

    for i in range(2):
        log.append("step_finished", {"step_name": f"s{i}", "status": "ok"}, run_id="r")

    final_bytes = log_path.read_bytes()

    assert final_bytes.startswith(snapshot), (
        "the snapshot taken after N appends must be a byte-exact prefix of the "
        "file after M further appends"
    )
    assert len(final_bytes) > len(snapshot)


def test_ac_l0_6_r0_1_structural_append_uses_o_append_not_o_trunc(tmp_path, monkeypatch):
    """AC-L0-6: R0.1 structural — EventLog.append MUST open with O_APPEND
    and MUST NOT use O_TRUNC. Verified behaviourally by intercepting the
    actual os.open() syscall flags, not by reading source text."""
    import event_log as event_log_module

    captured_flags: list[int] = []
    real_open = os.open

    def _spy_open(path, flags, *a, **kw):
        captured_flags.append(flags)
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(event_log_module.os, "open", _spy_open)

    log = EventLog(tmp_path / "events.jsonl")
    log.append("workflow_started", {"workflow_name": "wf"}, run_id="r")

    assert captured_flags, "os.open was not called by EventLog.append"
    flags = captured_flags[0]
    assert flags & os.O_APPEND, "EventLog.append must open with O_APPEND"
    assert not (flags & os.O_TRUNC), "EventLog.append must NOT open with O_TRUNC"


def test_ac_l0_6b_r0_1_is_a_branch_inside_check_bd_l0(tmp_path):
    """AC-L0-6b [G:MAJOR-7a]: R0.1 MUST be a branch inside check_bd_l0. When
    writer is supplied, the checker MUST itself probe it (the AC-L0-6 flag
    interception, runnable at check time) and set requirements["R0.1"]. When
    writer is omitted, requirements["R0.1"] MUST be "not-checked" and passed
    MUST be False — fail-closed."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    events = _valid_l0_events()

    report_with_writer = check_bd_l0(events, run_id="run-l0", writer=EventLog)
    assert report_with_writer.requirements["R0.1"] == "passed", report_with_writer.requirements
    assert report_with_writer.passed is True, report_with_writer.violations

    report_without_writer = check_bd_l0(events, run_id="run-l0")
    assert report_without_writer.requirements["R0.1"] == "not-checked", (
        report_without_writer.requirements
    )
    assert report_without_writer.passed is False, (
        "passed MUST be False when a third of L0 (R0.1) was never evaluated "
        "(fail-closed, no writer supplied)"
    )


class _TruncatingBadWriter:
    """AC-L0-6c negative control 1/2: opens with O_TRUNC (destroys prior
    content on every append) — must fail the checker's R0.1 probe."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type, payload, run_id=None):
        event = {"ts": "2026-07-27T00:00:00.000Z", "run_id": run_id or "ad-hoc",
                  "event_type": event_type, "payload": payload}
        encoded = (json.dumps(event) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
        return event


class _NoAppendFlagBadWriter:
    """AC-L0-6c negative control 2/2: opens WITHOUT O_APPEND (and without
    O_TRUNC) — each call re-opens at position 0, so successive writes
    overwrite rather than append. Must fail the checker's R0.1 probe."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type, payload, run_id=None):
        event = {"ts": "2026-07-27T00:00:00.000Z", "run_id": run_id or "ad-hoc",
                  "event_type": event_type, "payload": payload}
        encoded = (json.dumps(event) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o644)  # no O_APPEND
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
        return event


def test_ac_l0_6c_r0_1_probe_negative_control_three_inputs_not_two():
    """AC-L0-6c [G2:1]: negative control for the R0.1 probe — three inputs,
    not two. v2 exercised only writer=EventLog -> "passed" and writer
    omitted -> "not-checked", so `requirements["R0.1"] = "passed" if writer
    is not None else "not-checked"` satisfied every AC in the file. A writer
    whose append opens with O_TRUNC, and a writer that opens WITHOUT
    O_APPEND, MUST each yield requirements["R0.1"] == "failed", an
    "R0.1"-named entry in violations, and passed is False — distinguishing
    "writer is not None" from "writer actually appends safely"."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    events = _valid_l0_events()

    report_truncating = check_bd_l0(events, run_id="run-l0", writer=_TruncatingBadWriter)
    assert report_truncating.requirements["R0.1"] == "failed", report_truncating.requirements
    assert any(v.startswith("R0.1") for v in report_truncating.violations), report_truncating.violations
    assert report_truncating.passed is False

    report_no_append = check_bd_l0(events, run_id="run-l0", writer=_NoAppendFlagBadWriter)
    assert report_no_append.requirements["R0.1"] == "failed", report_no_append.requirements
    assert any(v.startswith("R0.1") for v in report_no_append.violations), report_no_append.violations
    assert report_no_append.passed is False

    # Sanity positive control: the real EventLog (already covered by
    # AC-L0-6b) must still pass, proving the checker discriminates rather
    # than failing every writer.
    report_real = check_bd_l0(events, run_id="run-l0", writer=EventLog)
    assert report_real.requirements["R0.1"] == "passed", report_real.requirements


def test_ac_l0_6d_r0_1_probe_does_not_write_outside_a_path_it_owns(tmp_path, monkeypatch):
    """AC-L0-6d [G4:EDGE-3]: the R0.1 probe MUST NOT write outside a path it
    owns. Nothing in v4 constrained WHERE the probe writes, so
    writer(Path("events.jsonl")) would write into the caller's cwd during a
    read-only conformance check — and with an O_TRUNC writer of the
    AC-L0-6c shape it would truncate a real file at that path. The probe
    runs ~20x in this suite alone. check_bd_l0 is run with writer=EventLog
    from a cwd whose contents are snapshotted before and after and MUST be
    unchanged."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    probe_cwd = tmp_path / "caller_cwd"
    probe_cwd.mkdir()
    (probe_cwd / "pre_existing_file.txt").write_text("must survive untouched\n")
    monkeypatch.chdir(probe_cwd)

    before = sorted(p.name for p in probe_cwd.iterdir())
    before_contents = (probe_cwd / "pre_existing_file.txt").read_text()

    report = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=EventLog)
    assert report.requirements["R0.1"] == "passed", report.requirements

    after = sorted(p.name for p in probe_cwd.iterdir())
    assert after == before, (
        f"the R0.1 probe must not write into the caller's cwd: before={before}, after={after}"
    )
    assert (probe_cwd / "pre_existing_file.txt").read_text() == before_contents, (
        "the probe must not truncate/overwrite a real file at a path it does not own"
    )


def test_ac_l0_6f_r0_1_probe_removes_its_own_temp_dir(monkeypatch):
    """AC-L0-6f [G5:EDGE-7]: the probe's own scratch directory MUST be
    REMOVED, not merely located outside the caller's cwd — AC-L0-6d asserts
    only the first half of §4.2's "creates and removes", and the probe runs
    ~20x per suite run. Captured via tempfile.mkdtemp (the stdlib primitive
    every "creates a temporary directory" path — including
    tempfile.TemporaryDirectory — routes through), asserting the captured
    path is ABSENT after check_bd_l0 returns."""
    import tempfile as tempfile_module  # noqa: PLC0415

    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    created_dirs: list[str] = []
    real_mkdtemp = tempfile_module.mkdtemp

    def _spy_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    monkeypatch.setattr(tempfile_module, "mkdtemp", _spy_mkdtemp)

    report = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=EventLog)
    assert report.requirements["R0.1"] == "passed", report.requirements

    assert created_dirs, (
        "sanity: the R0.1 probe must actually create a temp directory via "
        "tempfile.mkdtemp for this test to observe anything"
    )
    for created_dir in created_dirs:
        assert not Path(created_dir).exists(), (
            f"the probe's own scratch directory {created_dir!r} must be "
            f"REMOVED after check_bd_l0 returns, not merely located "
            f"outside the caller's cwd"
        )


class _RaisingWriter:
    """AC-L0-6g negative control: a writer whose append RAISES
    unconditionally — check_bd_l0's R0.1 probe must not let this
    exception propagate, and must not default R0.1 to "passed" via a bare
    `except Exception: pass` reflex fix either."""

    def __init__(self, path):
        self.path = Path(path)

    def append(self, event_type, payload, run_id=None):
        raise RuntimeError("simulated: this writer always raises on append")


def test_ac_l0_6g_writer_that_raises_is_not_checked_not_passed_and_does_not_propagate():
    """AC-L0-6g [G7:EDGE-6]: a writer whose append RAISES MUST NOT be
    rendered as R0.1 "passed". AC-L0-6c supplies writers with wrong
    os.open flags; none that raises. check_bd_l0 is documented as a
    read-only check that runs ~20x per suite, so an unhandled exception
    from a caller-supplied writer makes the checker a crash surface — and
    the reflex fix, a bare `except Exception: pass` around the probe,
    leaves R0.1 at its default and publishes "passed" for a probe that
    never ran. Both failure modes are unacceptable and this AC pins BOTH
    directions: check_bd_l0 MUST NOT propagate the exception, AND MUST
    render R0.1 "not-checked" (with a violation naming R0.1) — never
    "passed"."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    # (1) MUST NOT propagate — this call itself must not raise.
    report = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=_RaisingWriter)

    # (2) MUST render R0.1 "not-checked", never "passed" (the bare
    # except-and-default-True reflex fix).
    assert report.requirements["R0.1"] == "not-checked", (
        f"a writer whose append raises unconditionally must render R0.1 "
        f"'not-checked' — NEVER 'passed' (a bare except-and-ignore reflex "
        f"fix would leave R0.1 at whatever default the GREEN chose), got "
        f"{report.requirements!r}"
    )
    assert any(v.startswith("R0.1") for v in report.violations), report.violations
    assert report.passed is False


def test_ac_l0_7_r0_2_phase_outcome_artifacts_pass_on_valid_log():
    """AC-L0-7: R0.2 — every workflow_started has a matching workflow_finished
    with a status; every step_started/step_finished carries phase; every
    phase has exactly one phase_artifacts."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    report = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=EventLog)

    assert report.passed is True, getattr(report, "violations", report)


def test_ac_l0_8_r0_3_run_identity_present_passes():
    """AC-L0-8: R0.3 — the log MUST contain a run_identity with non-empty
    engine_version and adapter_identity."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    report = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=EventLog)

    assert report.passed is True, getattr(report, "violations", report)


def test_ac_l0_9_eight_negative_controls_and_no_vacuous_pass():
    """AC-L0-9 [G:MAJOR-3]: one negative control per clause — eight, not
    three — each naming the right requirement id in violations. Plus: an
    empty log MUST fail.

    [G5:MINOR-4]: a STRUCTURAL BREACH is "failed", not "not-checked".
    "not-checked" is reserved for "the channel was never observed"
    (AC-L0-3a, AC-L0-6b) — a malformed-but-present log is a MEASURED
    failure, and since §3.1's schema carries `l0` but NOT `violations`, a
    GREEN rendering every structural breach as "not-checked" would publish
    "we did not measure the write channel" for a host whose log was
    demonstrably malformed (AC-A24's defect class, polarity reversed).
    Pinned on clause 1 (R0.2) and clause 6 (R0.3)."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    def _check(events):
        return check_bd_l0(events, run_id="run-l0", writer=EventLog)

    # Positive control against a vacuous checker: baseline passes.
    baseline = _check(_valid_l0_events())
    assert baseline.passed is True, baseline.violations

    # 1) phase key stripped from a step event -> R0.2
    events_no_phase = []
    for e in _valid_l0_events():
        if e["event_type"] in ("step_started", "step_finished"):
            payload = dict(e["payload"])
            payload.pop("phase", None)
            e = {**e, "payload": payload}
        events_no_phase.append(e)
    report = _check(events_no_phase)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), report.violations
    assert report.requirements["R0.2"] == "failed", (
        f"[G5:MINOR-4]: a structural breach (phase key stripped) is a "
        f"MEASURED failure and MUST render R0.2 == 'failed', not "
        f"'not-checked' (that token is reserved for an unmeasured "
        f"channel), got {report.requirements!r}"
    )

    # 2) workflow_finished removed -> R0.2
    events_no_finished = [e for e in _valid_l0_events() if e["event_type"] != "workflow_finished"]
    report = _check(events_no_finished)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), report.violations

    # 3) status key stripped from workflow_finished -> R0.2
    events_no_status = []
    for e in _valid_l0_events():
        if e["event_type"] == "workflow_finished":
            payload = dict(e["payload"])
            payload.pop("status", None)
            e = {**e, "payload": payload}
        events_no_status.append(e)
    report = _check(events_no_status)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), report.violations

    # 4) phase_artifacts removed -> R0.2
    events_no_artifacts = [e for e in _valid_l0_events() if e["event_type"] != "phase_artifacts"]
    report = _check(events_no_artifacts)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), report.violations

    # 5) a SECOND phase_artifacts added for the same phase -> R0.2 (the
    #    failure mode AC-L0-3c predicts on real retry runs). [G4:3]: the
    #    injected duplicate MUST differ from the baseline by duplication and
    #    NOTHING ELSE — it carries write_tracking: "git-delta" exactly like
    #    the fixture it duplicates. v4 omitted write_tracking here, so the
    #    AC-L0-3a3 fail-closed branch fired first and satisfied this
    #    assertion WITHOUT any duplicate detection existing at all.
    events_double_artifacts = list(_valid_l0_events())
    events_double_artifacts.append(
        _ev("phase_artifacts", {
            "phase": "wf", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        })
    )
    report = _check(events_double_artifacts)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), (
        f"a duplicate phase_artifacts event (write_tracking identical to "
        f"the baseline fixture, so ONLY duplication differs) must still be "
        f"caught as an R0.2 violation — this discriminates real duplicate "
        f"detection from the AC-L0-3a3 fail-closed branch, which the "
        f"baseline's own write_tracking now passes: {report.violations}"
    )

    # 6) run_identity removed -> R0.3
    events_no_identity = [e for e in _valid_l0_events() if e["event_type"] != "run_identity"]
    report = _check(events_no_identity)
    assert report.passed is False
    assert any("R0.3" in v for v in report.violations), report.violations
    assert report.requirements["R0.3"] == "failed", (
        f"[G5:MINOR-4]: a structural breach (run_identity removed) is a "
        f"MEASURED failure and MUST render R0.3 == 'failed', not "
        f"'not-checked', got {report.requirements!r}"
    )

    # 7) run_identity present but engine_version empty -> R0.3
    # [G6:MINOR-2]: clause 7 is EXCLUDED from the "structural breach ->
    # failed" rule. This is the SAME observable input AC-L0-2c's
    # unresolved-version case names: an empty/absent engine_version is an
    # UNRESOLVABLE version, not a malformed log, so it MUST render
    # "not-checked" here — NOT "failed". v6 swept clause 7 into "any of the
    # eight is a structural breach -> failed", trapping a spec-literal
    # GREEN between two of the spec's own rules demanding different tokens
    # for the identical input.
    events_empty_engine_version = []
    for e in _valid_l0_events():
        if e["event_type"] == "run_identity":
            payload = dict(e["payload"])
            payload["engine_version"] = ""
            e = {**e, "payload": payload}
        events_empty_engine_version.append(e)
    report = _check(events_empty_engine_version)
    assert report.passed is False
    assert any("R0.3" in v for v in report.violations), report.violations
    assert report.requirements["R0.3"] == "not-checked", (
        f"[G6:MINOR-2]: clause 7 (engine_version present but empty) is "
        f"AC-L0-2c's unresolved-version case, an UNOBSERVED channel, not a "
        f"structural breach — MUST render 'not-checked', NOT 'failed', got "
        f"{report.requirements!r}"
    )

    # 7b) [G7:self-2]: clause 7's OTHER half — engine_version key ABSENT
    # entirely (not merely empty). v7's spec text reads "empty OR ABSENT
    # engine_version"; the round-7 RED mutated only the empty-string case,
    # so a GREEN special-casing "" while treating a missing key as a
    # structural breach ("failed") passed undetected.
    events_absent_engine_version = []
    for e in _valid_l0_events():
        if e["event_type"] == "run_identity":
            payload = dict(e["payload"])
            payload.pop("engine_version", None)
            e = {**e, "payload": payload}
        events_absent_engine_version.append(e)
    report = _check(events_absent_engine_version)
    assert report.passed is False
    assert any("R0.3" in v for v in report.violations), report.violations
    assert report.requirements["R0.3"] == "not-checked", (
        f"[G7:self-2]: clause 7's ABSENT-engine_version half (key missing "
        f"entirely, not just empty) is the SAME unresolved-version case as "
        f"the empty-string half — MUST also render 'not-checked', NOT "
        f"'failed', got {report.requirements!r}"
    )

    # 8) run_identity present but adapter_identity empty -> R0.3
    events_empty_adapter_identity = []
    for e in _valid_l0_events():
        if e["event_type"] == "run_identity":
            payload = dict(e["payload"])
            payload["adapter_identity"] = ""
            e = {**e, "payload": payload}
        events_empty_adapter_identity.append(e)
    report = _check(events_empty_adapter_identity)
    assert report.passed is False
    assert any("R0.3" in v for v in report.violations), report.violations

    # [G7:self-2] precedence rule: "'failed' dominates 'not-checked'" when a
    # structural breach and an unmeasured channel COEXIST for the SAME
    # requirement. Coexistence fixture: clause 7's unmeasured channel
    # (engine_version absent) TOGETHER WITH a genuine R0.3 structural
    # breach on the same requirement (adapter_identity emptied, clause 8) —
    # a GREEN resolving this collision either way (i.e. picking
    # "not-checked" because engine_version is unresolved, or picking
    # "failed" only when engine_version itself is malformed) must still
    # land on "failed", not "not-checked". Without this the spec's own
    # reconciliation of clause 1 with AC-L0-6e is unasserted.
    events_r03_breach_and_unmeasured_coexist = []
    for e in _valid_l0_events():
        if e["event_type"] == "run_identity":
            payload = dict(e["payload"])
            payload.pop("engine_version", None)  # unmeasured channel (clause 7)
            payload["adapter_identity"] = ""       # structural breach (clause 8)
            e = {**e, "payload": payload}
        events_r03_breach_and_unmeasured_coexist.append(e)
    report_coexist = _check(events_r03_breach_and_unmeasured_coexist)
    assert report_coexist.passed is False
    assert any("R0.3" in v for v in report_coexist.violations), report_coexist.violations
    assert report_coexist.requirements["R0.3"] == "failed", (
        f"[G7:self-2]: 'failed' MUST dominate 'not-checked' when a "
        f"structural breach (adapter_identity emptied) and an unmeasured "
        f"channel (engine_version absent) coexist for the SAME requirement "
        f"(R0.3) — got {report_coexist.requirements!r}"
    )

    # empty log MUST NOT pass (vacuous-checker guard)
    report_empty = check_bd_l0([], run_id="run-l0", writer=EventLog)
    assert report_empty.passed is False, "check_bd_l0 must not pass an empty log"


def test_ac_l0_6e_checker_does_not_grant_r02_over_zero_step_events():
    """AC-L0-6e [G5:EDGE-2] [G7:2a]: the checker MUST NOT grant R0.2 over a
    phase with ZERO step events. "Every step_started/step_finished carries
    phase" is vacuously true over an empty step set, so a forged or
    future-engine log of workflow_started + phase_artifacts{write_tracking:
    "git-delta"} + workflow_finished{status} — no step events at all — MUST
    NOT yield R0.2 "passed". AC-L0-3a4 closes this for what OUR engine
    emits; this closes it INSIDE THE CHECKER, whose declared threat model
    (AC-L0-3a3) is explicitly "an arbitrary event list… a future engine's
    or a forged one". This is [G4:4]'s vacuous all() one level up.

    [G7:2a] BLOCKING FIX: this clause is quantified PER PHASE and MUST be
    asserted non-uniformly, per [G6:quant]. The single-phase fixture below
    left `any(len(steps_of(p)) > 0 for p in phases)` indistinguishable from
    the per-phase reduction, so a GREEN checking the zero-step precondition
    once against the WHOLE scoped run (instead of once per phase) passed.
    Fixed: the primary assertion now uses a NON-UNIFORM two-phase fixture
    (phase_a has its step events, phase_b's are DROPPED entirely) which
    that wrong GREEN would grant R0.2 "passed" — verbatim [G6:1] one
    sub-clause over. The single-phase fixture is kept as an additional
    (weaker) check; the uniform two-phase log is the positive control."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    events_zero_steps = [
        _ev("workflow_started", {"workflow_name": "wf"}),
        _ev("run_identity", {
            "engine_version": "1.0.0",
            "adapter_identity": {"backend": "claude-subprocess", "source": "default"},
        }),
        # Deliberately NO step_started/step_finished at all.
        _ev("phase_artifacts", {
            "phase": "wf", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }),
        _ev("workflow_finished", {"workflow_name": "wf", "status": "ok", "wall_ms": 1}),
    ]

    report = check_bd_l0(events_zero_steps, run_id="run-l0", writer=EventLog)

    assert report.requirements["R0.2"] == "not-checked", (
        f"a phase_artifacts claiming 'git-delta' over a phase with ZERO "
        f"step events must NOT be granted R0.2 'passed' — the checker's "
        f"declared threat model is an arbitrary (possibly forged) event "
        f"list, so 'every step carries phase' being vacuously true over an "
        f"empty step set must not count as measured, got "
        f"{report.requirements!r}"
    )
    assert report.passed is False

    # [G7:2a]: the DISCRIMINATING, non-uniform two-phase fixture. phase_a
    # keeps its step events; phase_b's are dropped entirely. A GREEN
    # checking the zero-step precondition once per RUN (not per phase)
    # would see phase_a's step events and vacuously pass.
    events_two_phase_phase_b_zero_steps = [
        e for e in _valid_l0_events_two_phases()
        if not (
            e["event_type"] in ("step_started", "step_finished")
            and e["payload"].get("phase") == "phase_b"
        )
    ]
    report_non_uniform = check_bd_l0(events_two_phase_phase_b_zero_steps, run_id="run-l0", writer=EventLog)
    assert report_non_uniform.requirements["R0.2"] == "not-checked", (
        f"phase_a has step events and phase_b does not — a per-RUN (rather "
        f"than per-PHASE) zero-step precondition would see phase_a's steps "
        f"and vacuously grant R0.2, got {report_non_uniform.requirements!r}"
    )
    assert report_non_uniform.passed is False

    # Positive control: the uniform two-phase log (both phases have step
    # events) must still pass, proving the checker discriminates rather
    # than always failing two-phase logs.
    report_uniform = check_bd_l0(_valid_l0_events_two_phases(), run_id="run-l0", writer=EventLog)
    assert report_uniform.requirements["R0.2"] == "passed", report_uniform.requirements


def test_ac_l0_10_end_to_end_real_engine_run_passes_check_bd_l0(tmp_path):
    """AC-L0-10 [G3:MAJOR-1]: running a real WorkflowEngine workflow with an
    attached EventLog MUST produce a log that check_bd_l0 passes — L0
    measured against our own host, not a fixture. MUST drive the engine with
    a REAL git repo via org_config["git_cwd"] so R0.2's write channel is
    genuinely measured (not the AC-L0-3a "not-observed" configuration) —
    v3 ran this against _make_ctx() with no git_cwd, byte-identically the
    configuration AC-L0-3a defines as unmeasured."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_e2e",
        WorkflowDefinition(name="wf_l0_e2e", steps=[_ok_step("only_step", "payload")]),
    )
    result, _ctx = eng.execute(
        "wf_l0_e2e", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-e2e"
    )

    assert result.status == "ok"

    events = log.read_all()
    report = check_bd_l0(events, run_id="run-l0-e2e", writer=EventLog)

    assert report.passed is True, getattr(report, "violations", report)
    assert report.requirements == {"R0.1": "passed", "R0.2": "passed", "R0.3": "passed"}, (
        f"a real run with git_cwd set must genuinely measure all three "
        f"requirements as passed, got {report.requirements}"
    )


def test_ac_l0_11_composed_path_engine_to_checker_to_attestation_to_disk(tmp_path):
    """AC-L0-11 [G:MAJOR-7b] [G3:MAJOR-1]: composed path. One test MUST run a
    real engine workflow WITH a real git repo attached via
    org_config["git_cwd"], feed the resulting log to check_bd_l0, feed that
    L0Report to build_attestation_report, write it, reparse it, and assert
    level_achieved == "BD-L0" with l0.requirements recording all three
    requirements as passed. v3 drove this composed path with no git_cwd,
    which is byte-identically the AC-L0-3a "not-observed" configuration — the
    flagship composed artifact asserted `l0 == {all passed}` for a run whose
    write channel never ran. See AC-L0-11b for the paired unmeasured case."""
    from conformance.attestation import build_attestation_report, write_attestation_report  # noqa: PLC0415
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_11", WorkflowDefinition(name="wf_l0_11", steps=[_ok_step("only_step", "payload")]))
    result, _ctx = eng.execute(
        "wf_l0_11", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-11"
    )
    assert result.status == "ok"

    events = log.read_all()
    l0_report = check_bd_l0(events, run_id="run-l0-11", writer=EventLog)
    assert l0_report.passed is True, l0_report.violations
    assert l0_report.requirements == {"R0.1": "passed", "R0.2": "passed", "R0.3": "passed"}, (
        l0_report.requirements
    )

    report = build_attestation_report(
        level_claimed="BD-L0",
        results={},
        l0=l0_report,
        engine_version="0.0.0-test",
        adapter_identity={"backend": "agent-sdk", "source": "default"},
        host_identity={"host": "hal-test-host"},
        repo="hal/bytedigger",
        commit="deadbeefcafebabe0000000000000000000000",
        run_id="run-l0-11",
    )

    assert report["level_achieved"] == "BD-L0"
    assert report["l0"] == {"R0.1": "passed", "R0.2": "passed", "R0.3": "passed"}
    assert report["conformant"] is True

    out_path = tmp_path / "attestation.json"
    write_attestation_report(report, out_path)
    reparsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert reparsed["level_achieved"] == "BD-L0"


def test_ac_l0_11b_composed_path_without_git_cwd_is_unmeasured_not_bd_l0(tmp_path):
    """AC-L0-11b [G3:MAJOR-1]: the unmeasured composed path, asserted
    explicitly. The SAME end-to-end path WITHOUT git_cwd MUST yield
    requirements["R0.2"] == "not-checked", report["l0"]["R0.2"] ==
    "not-checked", level_achieved is None, conformant is False, and
    labels["R0.2"] == "writes-not-observed; reads-declared-only" — the pair
    that makes "not measured" observably different from "passed" in the
    reviewer-facing artifact. This is the differential partner of
    test_ac_l0_11 (identical workflow, only git_cwd differs).

    [G4:2]: asserted from the WRITTEN file, not in memory. v4 asserted these
    five values on the in-memory dict and never wrote it, so the lot's own
    flagship "not measured != passed" pair said nothing about the artifact a
    reviewer actually receives."""
    from conformance.attestation import build_attestation_report, write_attestation_report  # noqa: PLC0415
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_11b", WorkflowDefinition(name="wf_l0_11b", steps=[_ok_step("only_step", "payload")]))
    result, _ctx = eng.execute("wf_l0_11b", _make_ctx(), run_id="run-l0-11b")  # no git_cwd
    assert result.status == "ok"

    events = log.read_all()
    l0_report = check_bd_l0(events, run_id="run-l0-11b", writer=EventLog)
    assert l0_report.requirements["R0.2"] == "not-checked", l0_report.requirements
    assert l0_report.passed is False, (
        "L0Report.passed must fail-close: R0.2 not-checked means a third of "
        "L0 was never evaluated"
    )

    report = build_attestation_report(
        level_claimed="BD-L0",
        results={},
        l0=l0_report,
        engine_version="0.0.0-test",
        adapter_identity={"backend": "agent-sdk", "source": "default"},
        host_identity={"host": "hal-test-host"},
        repo="hal/bytedigger",
        commit="deadbeefcafebabe0000000000000000000000",
        run_id="run-l0-11b",
    )

    # In-memory sanity (kept from v4).
    assert report["l0"]["R0.2"] == "not-checked", report["l0"]
    assert report["level_achieved"] is None, (
        "a level cannot be granted while the write channel was never measured"
    )
    assert report["conformant"] is False
    assert report["labels"]["R0.2"] == "writes-not-observed; reads-declared-only", (
        report["labels"]
    )

    # [G4:2]: re-assert ALL FIVE values from the REPARSED written file, not
    # the in-memory dict — this is the only place the "not measured != passed"
    # distinction has any effect on what a reviewer actually receives.
    out_path = tmp_path / "attestation.json"
    write_attestation_report(report, out_path)
    reparsed = json.loads(out_path.read_text(encoding="utf-8"))

    assert l0_report.requirements["R0.2"] == "not-checked", l0_report.requirements
    assert reparsed["l0"]["R0.2"] == "not-checked", reparsed["l0"]
    assert reparsed["level_achieved"] is None, reparsed["level_achieved"]
    assert reparsed["conformant"] is False, reparsed["conformant"]
    assert reparsed["labels"]["R0.2"] == "writes-not-observed; reads-declared-only", (
        reparsed["labels"]
    )


def test_ac_l0_6b2_not_checked_fail_closes_for_r02_and_r03_not_just_r01():
    """AC-L0-6b2 [G3:MAJOR-1]: normative rule — L0Report.passed MUST be False
    and level_achieved MUST be None whenever ANY requirements[r] !=
    "passed", asserted for R0.2 and R0.3 (AC-L0-6b already covers R0.1).
    v3 made R0.2 reachably "not-checked" (AC-L0-3a) without pinning what that
    does to the level, leaving the RED satisfiable by a GREEN that grants
    BD-L0 over an unmeasured write channel."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    # R0.2 not-checked (write_tracking not-observed), R0.1/R0.3 otherwise passed.
    events_r02_not_checked = []
    for e in _valid_l0_events():
        if e["event_type"] == "phase_artifacts":
            payload = dict(e["payload"])
            payload["write_tracking"] = "not-observed"
            e = {**e, "payload": payload}
        events_r02_not_checked.append(e)
    report_r02 = check_bd_l0(events_r02_not_checked, run_id="run-l0", writer=EventLog)
    assert report_r02.requirements["R0.2"] == "not-checked", report_r02.requirements
    assert report_r02.passed is False, (
        "R0.2 not-checked must fail-close L0Report.passed, not only R0.1"
    )

    # [G7:MINOR-5]: R0.3 NOT "passed" (run_identity removed entirely is a
    # STRUCTURAL breach per AC-L0-9 clause 6 -> "failed", not "not-checked" —
    # this comment previously mislabeled the fixture; the assertion below
    # (!= "passed") was always correct and is unchanged), R0.1/R0.2 otherwise
    # passed.
    events_r03_not_checked = [e for e in _valid_l0_events() if e["event_type"] != "run_identity"]
    report_r03 = check_bd_l0(events_r03_not_checked, run_id="run-l0", writer=EventLog)
    assert report_r03.requirements["R0.3"] != "passed", report_r03.requirements
    assert report_r03.passed is False, (
        "R0.3 not passed must fail-close L0Report.passed, not only R0.1"
    )

    # And downstream: neither reaches level_achieved via build_attestation_report.
    from conformance.attestation import build_attestation_report  # noqa: PLC0415

    for report_obj in (report_r02, report_r03):
        report = build_attestation_report(
            level_claimed="BD-L0", results={}, l0=report_obj,
            engine_version="0.0.0-test",
            adapter_identity={"backend": "agent-sdk", "source": "default"},
            host_identity={"host": "hal-test-host"},
            repo="hal/bytedigger", commit="c" * 40, run_id="run-fail-close",
        )
        assert report["level_achieved"] is None, (
            f"a requirement other than 'passed' must fail-close level_achieved "
            f"too, got requirements={report_obj.requirements}"
        )


def test_ac_l0_12_shadowed_run_reported_as_distinct_violation(tmp_path, monkeypatch):
    """AC-L0-12 [G:edge-3]: a shadowed run (non-authoritative execution,
    every event mangled to SHADOW_EVENT_TYPE at engine.py:699-708) MUST be
    reported as a distinct E_SHADOWED_RUN violation, not as a generic pile
    of R0.2/R0.3 failures — a shadowed log is out of BD-L0's scope by
    construction."""
    import engine as engine_module  # noqa: PLC0415

    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415
    from execution_provenance import SHADOW_EVENT_TYPE  # noqa: PLC0415

    monkeypatch.setattr(engine_module, "is_authoritative_execution", lambda: False)

    log = EventLog(tmp_path / "events.jsonl")
    eng = engine_module.WorkflowEngine(event_log=log)
    eng.register("wf_l0_12", WorkflowDefinition(name="wf_l0_12", steps=[_ok_step("s1")]))
    eng.execute("wf_l0_12", _make_ctx(), run_id="run-l0-12")

    events = log.read_all()
    assert any(e["event_type"] == SHADOW_EVENT_TYPE for e in events), (
        "fixture must actually produce shadowed events, not a normal run"
    )

    report = check_bd_l0(events, run_id="run-l0-12", writer=EventLog)

    assert report.passed is False
    assert any("E_SHADOWED_RUN" in v for v in report.violations), report.violations
    assert not any(v.startswith("R0.2") or v.startswith("R0.3") for v in report.violations), (
        "a shadowed run must be reported via the dedicated E_SHADOWED_RUN "
        "code, not as a pile of generic R0.2/R0.3 failures: "
        f"{report.violations}"
    )


def test_ac_l0_12b_shadowed_run_requirements_all_not_checked_and_passed_false(monkeypatch, tmp_path):
    """AC-L0-12b [G2:2]: a shadowed run's requirements MUST be "not-checked"
    for ALL THREE requirements, and passed MUST be False. v2 pinned only
    violations and left requirements at whatever default the GREEN chose —
    a "passed" default plus AC-A17 reading only requirements attests a
    shadowed run as BD-L0."""
    import engine as engine_module  # noqa: PLC0415

    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415
    from execution_provenance import SHADOW_EVENT_TYPE  # noqa: PLC0415

    monkeypatch.setattr(engine_module, "is_authoritative_execution", lambda: False)

    log = EventLog(tmp_path / "events.jsonl")
    eng = engine_module.WorkflowEngine(event_log=log)
    eng.register("wf_l0_12b", WorkflowDefinition(name="wf_l0_12b", steps=[_ok_step("s1")]))
    eng.execute("wf_l0_12b", _make_ctx(), run_id="run-l0-12b")

    events = log.read_all()
    assert any(e["event_type"] == SHADOW_EVENT_TYPE for e in events)

    report = check_bd_l0(events, run_id="run-l0-12b", writer=EventLog)

    assert report.passed is False
    assert report.requirements == {"R0.1": "not-checked", "R0.2": "not-checked", "R0.3": "not-checked"}, (
        f"a shadowed run must report ALL THREE requirements as not-checked, "
        f"got {report.requirements}"
    )


def test_ac_l0_12c_shadow_events_do_not_mask_real_run_violations(monkeypatch, tmp_path):
    """AC-L0-12c [G2:edge-2]: a log containing shadow events AND a real
    authoritative run under the SAME run_id MUST still surface the real
    run's genuine R0.2/R0.3 violations; the shadow branch MUST NOT mask
    them."""
    import engine as engine_module  # noqa: PLC0415

    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415
    from execution_provenance import SHADOW_EVENT_TYPE  # noqa: PLC0415

    run_id = "run-l0-12c"

    monkeypatch.setattr(engine_module, "is_authoritative_execution", lambda: False)
    log = EventLog(tmp_path / "events.jsonl")
    eng = engine_module.WorkflowEngine(event_log=log)
    eng.register("wf_l0_12c", WorkflowDefinition(name="wf_l0_12c", steps=[_ok_step("s1")]))
    eng.execute("wf_l0_12c", _make_ctx(), run_id=run_id)
    shadow_events = log.read_all()
    assert any(e["event_type"] == SHADOW_EVENT_TYPE for e in shadow_events)

    # Genuine real-run events under the SAME run_id, deliberately broken
    # (workflow_finished stripped -> a real R0.2 violation).
    real_events_broken = [
        {**e, "run_id": run_id}
        for e in _valid_l0_events()
        if e["event_type"] != "workflow_finished"
    ]

    combined = shadow_events + real_events_broken
    report = check_bd_l0(combined, run_id=run_id, writer=EventLog)

    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), (
        f"shadow events under the same run_id must not mask the real run's "
        f"genuine R0.2 violation: {report.violations}"
    )


def test_ac_l0_13_exactly_one_phase_artifacts_per_phase_with_two_phases():
    """AC-L0-13 [G2:edge-8]: "exactly one phase_artifacts PER PHASE" MUST be
    asserted with a run containing TWO phases — every v2 fixture had a
    single workflow_started/workflow_finished pair, so a checker keyed on
    "exactly one per run" passed every test."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    baseline = check_bd_l0(_valid_l0_events_two_phases(), run_id="run-l0", writer=EventLog)
    assert baseline.passed is True, baseline.violations

    # Drop phase_b's phase_artifacts only -> a checker counting "at least one
    # per run" would still see phase_a's event and vacuously pass.
    events_missing_one_phase = [
        e for e in _valid_l0_events_two_phases()
        if not (e["event_type"] == "phase_artifacts" and e["payload"]["phase"] == "phase_b")
    ]
    report = check_bd_l0(events_missing_one_phase, run_id="run-l0", writer=EventLog)
    assert report.passed is False, (
        "a checker keyed on 'exactly one phase_artifacts per RUN' (not per "
        "PHASE) would vacuously pass this — phase_a still has its event"
    )
    assert any("R0.2" in v for v in report.violations), report.violations


# ═══════════════════════════════════════════════════════════════════════════
# AC-L0-3a5 [G6:1] — R0.2 is UNIVERSALLY quantified over the PHASES of the
# scoped run: a single measured phase MUST NOT lift an unmeasured one. Every
# "not-observed" checker fixture in v6 mutated the single phase_artifacts of
# a SINGLE-phase log, and the only two-phase fixture (_valid_l0_events_two_
# phases, above) gave BOTH phases git-delta — so any()/first-phase-only/
# last-phase-only reductions were all indistinguishable from all() across
# every one of v6's 108 tests. This is the flagship shape: EventLog is one
# file per run and a real consumer drives ONE run_id across MANY phases,
# most of which run with no org_config["git_cwd"].
# ═══════════════════════════════════════════════════════════════════════════


def _two_phase_events_non_uniform(*, phase_b_write_tracking: str) -> list[dict]:
    """Non-uniform two-phase fixture for [G6:quant]/AC-L0-3a5: phase_a is
    always "git-delta"; phase_b's write_tracking is parameterised so callers
    can build both the violating case (phase_b "not-observed") and the
    uniform positive control (phase_b "git-delta") from one builder."""
    return [
        _ev("workflow_started", {"workflow_name": "phase_a"}),
        _ev("run_identity", {
            "engine_version": "1.0.0",
            "adapter_identity": {"backend": "claude-subprocess", "source": "default"},
        }),
        _ev("step_started", {"step_name": "s1", "phase": "phase_a"}),
        _ev("step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1, "error": None, "phase": "phase_a"}),
        _ev("phase_artifacts", {
            "phase": "phase_a", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }),
        _ev("workflow_finished", {"workflow_name": "phase_a", "status": "ok", "wall_ms": 1}),
        _ev("workflow_started", {"workflow_name": "phase_b"}),
        _ev("step_started", {"step_name": "s1", "phase": "phase_b"}),
        _ev("step_finished", {"step_name": "s1", "status": "ok", "duration_ms": 1, "error": None, "phase": "phase_b"}),
        _ev("phase_artifacts", {
            "phase": "phase_b", "written": [], "read": [],
            "write_tracking": phase_b_write_tracking, "read_tracking": "declared-only",
        }),
        _ev("workflow_finished", {"workflow_name": "phase_b", "status": "ok", "wall_ms": 1}),
    ]


def test_ac_l0_3a5_1_non_uniform_two_phase_not_checked_with_positive_control():
    """AC-L0-3a5(1) [G6:1]: non-uniform two-phase fixture — phase_a
    "git-delta", phase_b "not-observed", both retaining their step events.
    requirements["R0.2"] MUST be "not-checked" and passed MUST be False.
    Positive control: the UNIFORM all-"git-delta" two-phase fixture MUST
    report "passed" — proving the checker discriminates rather than always
    failing two-phase logs."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    non_uniform = _two_phase_events_non_uniform(phase_b_write_tracking="not-observed")
    report = check_bd_l0(non_uniform, run_id="run-l0", writer=EventLog)
    assert report.requirements["R0.2"] == "not-checked", (
        f"phase_a measured 'git-delta' but phase_b is 'not-observed' — an "
        f"any()-shaped (or first/last-phase-only) reduction would let "
        f"phase_a's measurement lift the whole run to 'passed'; got "
        f"{report.requirements!r}"
    )
    assert report.passed is False

    uniform_positive_control = _two_phase_events_non_uniform(phase_b_write_tracking="git-delta")
    report_uniform = check_bd_l0(uniform_positive_control, run_id="run-l0", writer=EventLog)
    assert report_uniform.requirements["R0.2"] == "passed", (
        f"positive control: BOTH phases 'git-delta' must still pass, "
        f"proving the checker discriminates rather than always failing "
        f"two-phase logs, got {report_uniform.requirements!r}"
    )


def test_ac_l0_3a5_2_composed_artifact_reflects_non_uniform_two_phase(tmp_path):
    """AC-L0-3a5(2) [G6:1]: the SAME L0Report carried into the reviewer
    artifact. Build, WRITE it, reparse it, and assert labels["R0.2"] ==
    "writes-not-observed; reads-declared-only", l0["R0.2"] ==
    "not-checked", level_achieved is None, conformant is False — over the
    SAME non-uniform two-phase fixture as (1)."""
    from conformance.attestation import build_attestation_report, write_attestation_report  # noqa: PLC0415
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    non_uniform = _two_phase_events_non_uniform(phase_b_write_tracking="not-observed")
    l0_report = check_bd_l0(non_uniform, run_id="run-l0", writer=EventLog)
    assert l0_report.requirements["R0.2"] == "not-checked", l0_report.requirements

    report = build_attestation_report(
        level_claimed="BD-L0", results={}, l0=l0_report,
        engine_version="0.0.0-test",
        adapter_identity={"backend": "agent-sdk", "source": "default"},
        host_identity={"host": "hal-test-host"},
        repo="hal/bytedigger", commit="c" * 40, run_id="run-l0-3a5-2",
    )

    out_path = tmp_path / "attestation.json"
    write_attestation_report(report, out_path)
    reparsed = json.loads(out_path.read_text(encoding="utf-8"))

    assert reparsed["labels"]["R0.2"] == "writes-not-observed; reads-declared-only", (
        reparsed["labels"]
    )
    assert reparsed["l0"]["R0.2"] == "not-checked", reparsed["l0"]
    assert reparsed["level_achieved"] is None, reparsed["level_achieved"]
    assert reparsed["conformant"] is False, reparsed["conformant"]


def test_ac_l0_3a5_3_end_to_end_two_phase_run_one_engine_one_run_id(tmp_path):
    """AC-L0-3a5(3) [G6:1]: end-to-end against our own host — the flagship
    shape. TWO execute() calls on ONE engine under ONE run_id: phase A with
    git_cwd on a real repo, phase B without. check_bd_l0(log.read_all(),
    run_id=...) MUST report R0.2 "not-checked". EventLog is one file per
    run and a real consumer drives one run_id across many phases, most of
    which run with no org_config["git_cwd"]."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)
    run_id = "run-l0-3a5-e2e"

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3a5_a", WorkflowDefinition(name="wf_l0_3a5_a", steps=[_ok_step("noop")]))
    eng.register("wf_l0_3a5_b", WorkflowDefinition(name="wf_l0_3a5_b", steps=[_ok_step("noop")]))

    eng.execute("wf_l0_3a5_a", _make_ctx(git_cwd=str(repo_dir)), run_id=run_id)
    eng.execute("wf_l0_3a5_b", _make_ctx(), run_id=run_id)  # no git_cwd

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 2
    write_trackings = {e["payload"]["phase"]: e["payload"].get("write_tracking") for e in phase_artifacts}
    assert write_trackings["wf_l0_3a5_a"] == "git-delta", write_trackings
    assert write_trackings["wf_l0_3a5_b"] == "not-observed", write_trackings

    report = check_bd_l0(events, run_id=run_id, writer=EventLog)
    assert report.requirements["R0.2"] == "not-checked", (
        f"one measured phase (git_cwd set) MUST NOT lift the whole run "
        f"when a second phase under the SAME run_id never observed its "
        f"write channel, got {report.requirements!r}"
    )
    assert report.passed is False


def test_ac_l0_3a5_4_other_r02_clauses_asserted_by_mutating_phase_b_only():
    """AC-L0-3a5(4) [G6:1]: the OTHER THREE R0.2 clauses are quantified
    identically over phases of a run and MUST be asserted by mutating
    phase_b ONLY on the same two-phase fixture: phase stripped from
    phase_b's step events; phase_b's workflow_finished removed; phase_b's
    status stripped — each MUST fail. v6 asserted all three only on
    single-phase logs, so any() survived in each.

    [G7:MINOR-6]: the phase-stripped case also pins
    `requirements["R0.2"] == "failed"` — AC-L0-9 clause 1 pins that EXACT
    token for the identical mutation on a single-phase log, but the
    quantifier here was previously asserted only via `passed`/`violations`,
    not the token, so a GREEN rendering the phase-level breach as
    "not-checked" passed here and failed clause 1 only by luck of which
    fixture it met."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    # phase stripped from phase_b's step events only (phase_a untouched).
    events_phase_b_no_phase_key = []
    for e in _two_phase_events_non_uniform(phase_b_write_tracking="git-delta"):
        if e["event_type"] in ("step_started", "step_finished") and e["payload"].get("phase") == "phase_b":
            payload = dict(e["payload"])
            payload.pop("phase", None)
            e = {**e, "payload": payload}
        events_phase_b_no_phase_key.append(e)
    report = check_bd_l0(events_phase_b_no_phase_key, run_id="run-l0", writer=EventLog)
    assert report.passed is False, (
        "phase_a is entirely well-formed; only phase_b's step events lost "
        "their phase key — a per-phase any()-shaped reduction would let "
        "phase_a's soundness pass the whole run"
    )
    assert any("R0.2" in v for v in report.violations), report.violations
    assert report.requirements["R0.2"] == "failed", (
        f"[G7:MINOR-6]: a phase-level structural breach (phase_b's step "
        f"events missing 'phase') MUST render requirements['R0.2'] == "
        f"'failed' — the SAME token AC-L0-9 clause 1 pins for the "
        f"identical mutation on a single-phase log — got "
        f"{report.requirements!r}"
    )

    # phase_b's workflow_finished removed only (phase_a's untouched).
    events_phase_b_no_finished = [
        e for e in _two_phase_events_non_uniform(phase_b_write_tracking="git-delta")
        if not (e["event_type"] == "workflow_finished" and e["payload"]["workflow_name"] == "phase_b")
    ]
    report = check_bd_l0(events_phase_b_no_finished, run_id="run-l0", writer=EventLog)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), report.violations

    # phase_b's status stripped from its workflow_finished only.
    events_phase_b_no_status = []
    for e in _two_phase_events_non_uniform(phase_b_write_tracking="git-delta"):
        if e["event_type"] == "workflow_finished" and e["payload"]["workflow_name"] == "phase_b":
            payload = dict(e["payload"])
            payload.pop("status", None)
            e = {**e, "payload": payload}
        events_phase_b_no_status.append(e)
    report = check_bd_l0(events_phase_b_no_status, run_id="run-l0", writer=EventLog)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), report.violations


def test_ac_l0_12d_run_id_scoping_functionally_asserted():
    """AC-L0-12d [G3:MINOR-4]: run_id scoping is functionally asserted. v3's
    every checker test passed a run_id matching every event, so a GREEN
    ignoring run_id entirely would pass all of them while the [G:edge-4]
    scenario it exists for produces a false PASS. A log holds run A (MISSING
    run_identity) and run B (complete) under DIFFERENT run_ids: checking run
    A MUST fail R0.3 (B's identity must not satisfy A), and B's
    phase_artifacts for a same-named phase must not trip A's "exactly one
    per phase" for A."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    run_id_a = "run-l0-12d-a"
    run_id_b = "run-l0-12d-b"

    # Run A: same phase name as B, but NO run_identity at all.
    events_a = [
        _ev("workflow_started", {"workflow_name": "shared_phase_name"}, run_id=run_id_a),
        _ev("step_started", {"step_name": "s1", "phase": "shared_phase_name"}, run_id=run_id_a),
        _ev("step_finished", {
            "step_name": "s1", "status": "ok", "duration_ms": 1, "error": None,
            "phase": "shared_phase_name",
        }, run_id=run_id_a),
        _ev("phase_artifacts", {
            "phase": "shared_phase_name", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }, run_id=run_id_a),
        _ev("workflow_finished", {"workflow_name": "shared_phase_name", "status": "ok", "wall_ms": 1}, run_id=run_id_a),
    ]

    # Run B: same phase name, complete run_identity, DIFFERENT run_id.
    events_b = [
        _ev("workflow_started", {"workflow_name": "shared_phase_name"}, run_id=run_id_b),
        _ev("run_identity", {
            "engine_version": "1.0.0",
            "adapter_identity": {"backend": "claude-subprocess", "source": "default"},
        }, run_id=run_id_b),
        _ev("step_started", {"step_name": "s1", "phase": "shared_phase_name"}, run_id=run_id_b),
        _ev("step_finished", {
            "step_name": "s1", "status": "ok", "duration_ms": 1, "error": None,
            "phase": "shared_phase_name",
        }, run_id=run_id_b),
        _ev("phase_artifacts", {
            "phase": "shared_phase_name", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }, run_id=run_id_b),
        _ev("workflow_finished", {"workflow_name": "shared_phase_name", "status": "ok", "wall_ms": 1}, run_id=run_id_b),
    ]

    combined = events_a + events_b

    report_a = check_bd_l0(combined, run_id=run_id_a, writer=EventLog)
    assert report_a.passed is False, (
        "run A has no run_identity of its own; a GREEN ignoring run_id "
        "would let run B's run_identity satisfy run A and pass it"
    )
    assert any("R0.3" in v for v in report_a.violations), report_a.violations
    # Run A's own phase_artifacts is present exactly once for run A — a
    # GREEN ignoring run_id would see TWO phase_artifacts for
    # "shared_phase_name" (one from each run) and could wrongly fail A on
    # a duplicate-phase_artifacts violation instead of the real R0.3 gap.
    # [G4:MINOR-3]: asserted structurally — run A is R0.2-clean by
    # construction — not by matching violation prose ("second" in
    # v.lower()), which passes whenever a GREEN words its message
    # differently even if the guard DID wrongly trip.
    assert not any(v.startswith("R0.2") for v in report_a.violations), (
        f"run B's phase_artifacts for the same-named phase must not trip "
        f"run A's 'exactly one phase_artifacts per phase' check: "
        f"{report_a.violations}"
    )

    report_b = check_bd_l0(combined, run_id=run_id_b, writer=EventLog)
    assert report_b.passed is True, report_b.violations


def test_ac_l0_12e_two_phase_artifacts_same_phase_same_run_id_fail_closed():
    """AC-L0-12e [G4:EDGE-5]: two phase_artifacts for one phase under the
    SAME run_id — the scenario run_id scoping exists for
    (event_log.py:82-88: multiple appending processes) — yet AC-L0-12d
    covers only DIFFERENT run_ids. Normative: the checker cannot distinguish
    an interleaved co-writer from a real double emit, so it MUST fail-closed
    and report it as the AC-L0-9-clause-5 R0.2 violation."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    events = list(_valid_l0_events())
    events.append(
        _ev("phase_artifacts", {
            "phase": "wf", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        })
    )
    report = check_bd_l0(events, run_id="run-l0", writer=EventLog)
    assert report.passed is False, (
        "two phase_artifacts for the same phase under the SAME run_id must "
        "fail-close, indistinguishable from an interleaved co-writer"
    )
    assert any(v.startswith("R0.2") for v in report.violations), report.violations


def test_ac_l0_14_checker_enforces_adapter_identity_shape():
    """AC-L0-14 [G2:8]: the checker MUST enforce the AC-L0-2b SHAPE of
    adapter_identity — a mapping with non-empty backend and source — not
    merely non-emptiness. The v2 fixture used a bare string, which pinned
    the checker to a weaker contract than the engine emits."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    baseline = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=EventLog)
    assert baseline.passed is True, baseline.violations

    def _with_adapter_identity(value):
        events = []
        for e in _valid_l0_events():
            if e["event_type"] == "run_identity":
                payload = dict(e["payload"])
                payload["adapter_identity"] = value
                e = {**e, "payload": payload}
            events.append(e)
        return events

    # Bare string (non-empty, v2's fixture shape) MUST now fail.
    report_string = check_bd_l0(
        _with_adapter_identity("claude-subprocess"), run_id="run-l0", writer=EventLog
    )
    assert report_string.passed is False
    assert any("R0.3" in v for v in report_string.violations), report_string.violations

    # Dict missing "source" MUST fail.
    report_missing_source = check_bd_l0(
        _with_adapter_identity({"backend": "claude-subprocess"}), run_id="run-l0", writer=EventLog
    )
    assert report_missing_source.passed is False
    assert any("R0.3" in v for v in report_missing_source.violations), report_missing_source.violations

    # Dict with an empty backend MUST fail.
    report_empty_backend = check_bd_l0(
        _with_adapter_identity({"backend": "", "source": "default"}), run_id="run-l0", writer=EventLog
    )
    assert report_empty_backend.passed is False
    assert any("R0.3" in v for v in report_empty_backend.violations), report_empty_backend.violations


def test_ac_l0_15_empty_scope_must_not_vacuously_pass():
    """AC-L0-15 [G7:EDGE-1]: an EMPTY SCOPE MUST NOT vacuously pass. This is
    the cheapest false-green left in the checker — the one `all([])` shape
    this lot had not yet closed one scope out. AC-L0-9's vacuity guard is
    `check_bd_l0([], ...)` — the EMPTY LOG. Nothing exercised a POPULATED
    log scoped to a `run_id` matching NOTHING. The natural implementation,
    `scoped = [e for e in events if e["run_id"] == run_id]` guarded only by
    `if not events: fail`, then evaluates every universal R0.2/R0.3 clause
    over an empty `scoped` set — vacuously true — and reports all three
    requirements "passed", `level_achieved: "BD-L0"`, `conformant: true`
    for a run that produced NO EVIDENCE WHATSOEVER. Rounds 4-7 chased
    `all([])` down the collection ladder; this is the same defect one
    scope out, over the event set itself.

    Normative: a scope containing no events for the requested run_id MUST
    yield `passed is False` with all three requirements "not-checked"
    (nothing was observed, so nothing was measured — this is NOT a
    structural breach), and MUST NOT grant a level. Asserted with a log
    holding a complete, PASSING run under a DIFFERENT run_id, so the
    fixture also proves the checker is not merely reading the whole log
    (i.e. it genuinely scopes by run_id, not "the log is non-empty
    somewhere")."""
    from conformance.attestation import build_attestation_report  # noqa: PLC0415
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    # A complete, PASSING run under a DIFFERENT run_id — proves the
    # checker is genuinely scoping, not just checking "events is non-empty".
    events_other_run_passing = _valid_l0_events()  # run_id="run-l0" by default
    assert all(e["run_id"] == "run-l0" for e in events_other_run_passing)

    scoped_run_id = "run-l0-15-empty-scope-matches-nothing"
    report = check_bd_l0(events_other_run_passing, run_id=scoped_run_id, writer=EventLog)

    assert report.passed is False, (
        "a scope matching NO events must not pass, even though the log "
        "itself holds a complete passing run under a different run_id"
    )
    assert report.requirements == {"R0.1": "not-checked", "R0.2": "not-checked", "R0.3": "not-checked"}, (
        f"an empty scope observed NOTHING, so nothing was measured — all "
        f"three requirements must be 'not-checked' (not a structural "
        f"breach), got {report.requirements!r}"
    )

    report_attest = build_attestation_report(
        level_claimed="BD-L0", results={}, l0=report,
        engine_version="0.0.0-test",
        adapter_identity={"backend": "agent-sdk", "source": "default"},
        host_identity={"host": "hal-test-host"},
        repo="hal/bytedigger", commit="c" * 40, run_id=scoped_run_id,
    )
    assert report_attest["level_achieved"] is None, (
        f"an empty scope MUST NOT grant any level, got "
        f"{report_attest['level_achieved']!r}"
    )
    assert report_attest["conformant"] is False


def test_ac_l0_3a_write_tracking_differential_git_cwd_vs_none(tmp_path):
    """AC-L0-3a [G2:4]: written: [] MUST NOT be able to mean two different
    things. write_tracking MUST be "git-delta" when a scan tree was resolved
    (org_config["git_cwd"] set) and "not-observed" when it was not,
    asserted DIFFERENTIALLY: one run with git_cwd set, one without."""
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    log_git = EventLog(tmp_path / "events_git.jsonl")
    eng_git = WorkflowEngine(event_log=log_git)
    eng_git.register("wf_l0_3a_git", WorkflowDefinition(name="wf_l0_3a_git", steps=[_ok_step("noop")]))
    eng_git.execute("wf_l0_3a_git", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3a-git")
    events_git = log_git.read_all()
    phase_artifacts_git = [e for e in events_git if e["event_type"] == "phase_artifacts"]
    assert phase_artifacts_git, (
        f"expected a phase_artifacts event in the log, got event_types="
        f"{[e['event_type'] for e in events_git]!r}"
    )
    payload_git = phase_artifacts_git[0]["payload"]
    assert payload_git["write_tracking"] == "git-delta", (
        f"org_config['git_cwd'] set -> a scan tree was resolved -> "
        f"write_tracking must be 'git-delta', got {payload_git.get('write_tracking')!r}"
    )

    log_none = EventLog(tmp_path / "events_none.jsonl")
    eng_none = WorkflowEngine(event_log=log_none)
    eng_none.register("wf_l0_3a_none", WorkflowDefinition(name="wf_l0_3a_none", steps=[_ok_step("noop")]))
    eng_none.execute("wf_l0_3a_none", _make_ctx(), run_id="run-l0-3a-none")  # no git_cwd at all
    events_none = log_none.read_all()
    phase_artifacts_none = [e for e in events_none if e["event_type"] == "phase_artifacts"]
    assert phase_artifacts_none, (
        f"expected a phase_artifacts event in the log, got event_types="
        f"{[e['event_type'] for e in events_none]!r}"
    )
    payload_none = phase_artifacts_none[0]["payload"]
    assert payload_none["write_tracking"] == "not-observed", (
        f"no org_config['git_cwd'] -> _resolve_scan_cwd returns None -> the "
        f"write channel is inert -> write_tracking must be 'not-observed', "
        f"NOT 'git-delta' (that would convert 'not measured' into the false "
        f"affirmative claim 'nothing was written'), got "
        f"{payload_none.get('write_tracking')!r}"
    )

    assert payload_git["write_tracking"] != payload_none["write_tracking"]


def test_ac_l0_3a_check_bd_l0_reports_not_checked_when_write_tracking_not_observed():
    """AC-L0-3a [G2:4] (checker half): check_bd_l0 MUST report R0.2 as
    "not-checked" — NOT "passed" — when any phase carries write_tracking:
    "not-observed"."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    baseline = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=EventLog)
    assert baseline.requirements["R0.2"] == "passed", baseline.requirements

    events_not_observed = []
    for e in _valid_l0_events():
        if e["event_type"] == "phase_artifacts":
            payload = dict(e["payload"])
            payload["write_tracking"] = "not-observed"
            e = {**e, "payload": payload}
        events_not_observed.append(e)
    report = check_bd_l0(events_not_observed, run_id="run-l0", writer=EventLog)

    assert report.requirements["R0.2"] != "passed", (
        "a phase carrying write_tracking: 'not-observed' must NEVER let "
        "R0.2 read 'passed' — that would attest an unmeasured write channel "
        "as observed"
    )
    assert report.requirements["R0.2"] == "not-checked", report.requirements


def test_ac_l0_3a2_negative_control_git_cwd_points_at_non_git_dir(tmp_path):
    """AC-L0-3a2 [G3:MAJOR-4]: negative control for the failure branch.
    _resolve_scan_cwd returning a path does NOT mean anything was measured:
    _git_changes_vs_head returns None for a non-git directory
    (returncode != 0), and engine.py:434/:436 then skip the delta entirely.
    A run with git_cwd pointing at a NON-REPO MUST therefore yield
    write_tracking: "not-observed" and requirements["R0.2"] == "not-checked"
    — NOT "git-delta" with written: [], which is the affirmative claim
    "nothing was written" over a channel that never ran."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    non_repo_dir = tmp_path / "plain_dir_not_a_git_repo"
    non_repo_dir.mkdir()
    (non_repo_dir / "some_file.txt").write_text("not tracked by any vcs\n")

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3a2", WorkflowDefinition(name="wf_l0_3a2", steps=[_ok_step("noop")]))
    eng.execute("wf_l0_3a2", _make_ctx(git_cwd=str(non_repo_dir)), run_id="run-l0-3a2")

    events = log.read_all()
    phase_artifacts_events = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert phase_artifacts_events, (
        f"expected a phase_artifacts event in the log, got event_types="
        f"{[e['event_type'] for e in events]!r}"
    )
    payload = phase_artifacts_events[0]["payload"]
    assert payload["write_tracking"] == "not-observed", (
        f"git_cwd resolved to a non-git directory: _git_changes_vs_head "
        f"returns None (returncode != 0), so no delta was ever computed — "
        f"write_tracking must be 'not-observed', NOT 'git-delta' with "
        f"written: [], got {payload!r}"
    )

    report = check_bd_l0(events, run_id="run-l0-3a2", writer=EventLog)
    assert report.requirements["R0.2"] == "not-checked", report.requirements


def test_ac_l0_3a3_missing_write_tracking_key_treated_as_not_observed(tmp_path):
    """AC-L0-3a3 [G3:MINOR-5]: a phase_artifacts event with NO write_tracking
    key at all MUST be treated as "not-observed" (fail-closed), not silently
    accepted as passing."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    events_no_write_tracking_key = []
    for e in _valid_l0_events():
        if e["event_type"] == "phase_artifacts":
            payload = dict(e["payload"])
            del payload["write_tracking"]
            e = {**e, "payload": payload}
        events_no_write_tracking_key.append(e)

    report = check_bd_l0(events_no_write_tracking_key, run_id="run-l0", writer=EventLog)
    assert report.requirements["R0.2"] != "passed", (
        "a phase_artifacts event with no write_tracking key at all must "
        "fail-close, not silently pass R0.2"
    )
    assert report.requirements["R0.2"] == "not-checked", report.requirements


def test_ac_l0_3a3_unrecognised_write_tracking_tokens_fail_closed():
    """AC-L0-3a3 extension [G4:EDGE-2]: the same fail-close MUST apply to an
    UNRECOGNISED token — write_tracking of "observed", "git_delta" (wrong
    separator), or "" MUST yield requirements["R0.2"] == "not-checked", not
    "passed". `if wt != "not-observed": passed` would render any unknown
    spelling — a future engine's or a forged one — as a measured pass. Only
    the exact token "git-delta" counts."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    for bad_token in ("observed", "git_delta", ""):
        events = []
        for e in _valid_l0_events():
            if e["event_type"] == "phase_artifacts":
                payload = dict(e["payload"])
                payload["write_tracking"] = bad_token
                e = {**e, "payload": payload}
            events.append(e)

        report = check_bd_l0(events, run_id="run-l0", writer=EventLog)
        assert report.requirements["R0.2"] == "not-checked", (
            f"write_tracking={bad_token!r} must fail-close R0.2 to "
            f"'not-checked', got {report.requirements['R0.2']!r} — only "
            f"the exact token 'git-delta' may count as measured"
        )


def test_ac_l0_3b5_artifact_record_must_carry_artifact_fields_present_and_list_typed():
    """AC-L0-3b5 [G6:EDGE-3] [G7:2b]: an artifact record MUST actually
    carry its artifact fields. AC-L0-3a3 fail-closes only on the
    write_tracking key/token, so a scoped phase_artifacts of {"phase":
    "wf", "write_tracking": "git-delta"} — no written, no read, no
    read_tracking — currently is not required to fail. Given the checker's
    declared threat model (an arbitrary, possibly forged event list), the
    fields carrying the actual artifact references MUST be required
    present and LIST-typed (written/read), or R0.2 is "not-checked".

    [G7:2b] BLOCKING FIX: validation is PER RECORD and MUST be asserted
    non-uniformly, per [G6:quant]. v7 mutated the SINGLE phase_artifacts
    of a single-phase log for both halves, so validating the shape once
    against `records[0]` / `next(...)` — the first-phase-only reduction
    [G6:1] named — passed both. Fixed: the malformed record now sits on
    phase_b of a TWO-phase fixture (phase_a's record left well-formed),
    for both the missing-fields half and the wrong-type half, with the
    well-formed two-phase log as the positive control. The single-phase
    mutations are kept as an additional (weaker) check."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    baseline = check_bd_l0(_valid_l0_events(), run_id="run-l0", writer=EventLog)
    assert baseline.requirements["R0.2"] == "passed", baseline.requirements

    # written/read/read_tracking entirely absent — only phase + write_tracking.
    events_bare_artifact_record = []
    for e in _valid_l0_events():
        if e["event_type"] == "phase_artifacts":
            e = {**e, "payload": {"phase": "wf", "write_tracking": "git-delta"}}
        events_bare_artifact_record.append(e)

    report = check_bd_l0(events_bare_artifact_record, run_id="run-l0", writer=EventLog)
    assert report.requirements["R0.2"] == "not-checked", (
        f"a phase_artifacts record carrying only phase/write_tracking (no "
        f"written, read, or read_tracking) must NOT be granted R0.2 "
        f"'passed' — the fields carrying the actual artifact references "
        f"must be required present, got {report.requirements!r}"
    )

    # written/read present but NOT list-typed must also fail-close.
    events_wrong_types = []
    for e in _valid_l0_events():
        if e["event_type"] == "phase_artifacts":
            payload = dict(e["payload"])
            payload["written"] = "not-a-list"
            payload["read"] = "not-a-list"
            e = {**e, "payload": payload}
        events_wrong_types.append(e)
    report_wrong_types = check_bd_l0(events_wrong_types, run_id="run-l0", writer=EventLog)
    assert report_wrong_types.requirements["R0.2"] == "not-checked", (
        f"written/read present but not LIST-typed must fail-close R0.2 to "
        f"'not-checked', got {report_wrong_types.requirements!r}"
    )

    # [G7:2b] DISCRIMINATING two-phase fixtures: phase_a's record is
    # well-formed throughout; only phase_b's is malformed. A GREEN
    # validating shape once against the first record it finds (or any
    # single record) would let phase_a's well-formed record pass the
    # whole run.
    events_two_phase_phase_b_bare = []
    for e in _valid_l0_events_two_phases():
        if e["event_type"] == "phase_artifacts" and e["payload"]["phase"] == "phase_b":
            e = {**e, "payload": {"phase": "phase_b", "write_tracking": "git-delta"}}
        events_two_phase_phase_b_bare.append(e)
    report_two_phase_bare = check_bd_l0(events_two_phase_phase_b_bare, run_id="run-l0", writer=EventLog)
    assert report_two_phase_bare.requirements["R0.2"] == "not-checked", (
        f"phase_a's phase_artifacts is well-formed; only phase_b's is bare "
        f"(no written/read/read_tracking) — a first-record-only validation "
        f"would let phase_a's soundness pass the whole run, got "
        f"{report_two_phase_bare.requirements!r}"
    )

    events_two_phase_phase_b_wrong_types = []
    for e in _valid_l0_events_two_phases():
        if e["event_type"] == "phase_artifacts" and e["payload"]["phase"] == "phase_b":
            payload = dict(e["payload"])
            payload["written"] = "not-a-list"
            payload["read"] = "not-a-list"
            e = {**e, "payload": payload}
        events_two_phase_phase_b_wrong_types.append(e)
    report_two_phase_wrong_types = check_bd_l0(
        events_two_phase_phase_b_wrong_types, run_id="run-l0", writer=EventLog
    )
    assert report_two_phase_wrong_types.requirements["R0.2"] == "not-checked", (
        f"phase_a's record is well-formed; only phase_b's written/read are "
        f"NOT list-typed — must still fail-close R0.2, got "
        f"{report_two_phase_wrong_types.requirements!r}"
    )

    # Positive control: the well-formed two-phase log must still pass.
    report_two_phase_positive_control = check_bd_l0(
        _valid_l0_events_two_phases(), run_id="run-l0", writer=EventLog
    )
    assert report_two_phase_positive_control.requirements["R0.2"] == "passed", (
        report_two_phase_positive_control.requirements
    )


def test_ac_l0_3b6_orphan_phase_artifacts_with_no_workflow_started_fails():
    """AC-L0-3b6 [G6:EDGE-5]: an orphan phase_artifacts MUST NOT lift the
    write half. A phase_artifacts record for a phase with NO
    workflow_started in the scoped run is currently undefined, so a forged
    log can add a git-delta record for a phase that never ran. Normative: a
    phase_artifacts whose phase has no workflow_started in the scoped run
    is an R0.2 violation ("failed" — a structural forgery, not an
    unmeasured channel)."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    events_with_orphan = list(_valid_l0_events())
    events_with_orphan.append(
        _ev("phase_artifacts", {
            "phase": "phase_never_started", "written": [], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        })
    )
    report = check_bd_l0(events_with_orphan, run_id="run-l0", writer=EventLog)
    assert report.passed is False, (
        "a phase_artifacts for a phase with no workflow_started in the "
        "scoped run must not be silently accepted — a forged log could "
        "otherwise add a git-delta record for a phase that never ran"
    )
    assert any("R0.2" in v for v in report.violations), report.violations
    assert report.requirements["R0.2"] == "failed", (
        f"an orphan phase_artifacts is a structural forgery, not an "
        f"unmeasured channel — MUST render 'failed', not 'not-checked', "
        f"got {report.requirements!r}"
    )


def test_ac_l0_3b7_phase_artifacts_preceding_its_own_workflow_started_is_valid():
    """AC-L0-3b7 [G7:EDGE-7]: a phase_artifacts PRECEDING its own
    workflow_started is DEFINED, not left to GREEN. AC-L0-3b6 defines the
    ABSENT workflow_started case as "failed"; a record appearing BEFORE
    the workflow_started for the SAME phase in the scoped run was
    undefined, so a streaming implementation could call it an orphan
    ("failed") while a pre-indexing one calls it valid — neither is a
    false-green, which is exactly why the spec decides it now rather than
    leaving it for a future round to find as a defect.

    Normative: the checker indexes the whole scoped run before reducing —
    event order within a scope carries no meaning beyond AC-L0-2's
    "immediately follows workflow_started" positional rule — so a
    phase_artifacts whose phase HAS a workflow_started anywhere in the
    scope is valid regardless of relative order. Asserted with the record
    moved AHEAD of its own workflow_started: R0.2 MUST still be
    "passed"."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    baseline_events = _valid_l0_events()
    workflow_started_index = next(
        i for i, e in enumerate(baseline_events) if e["event_type"] == "workflow_started"
    )
    phase_artifacts_index = next(
        i for i, e in enumerate(baseline_events) if e["event_type"] == "phase_artifacts"
    )
    assert phase_artifacts_index > workflow_started_index, (
        "sanity: the baseline fixture's phase_artifacts must normally come "
        "AFTER workflow_started, so moving it ahead is a genuine reorder"
    )

    reordered_events = list(baseline_events)
    phase_artifacts_event = reordered_events.pop(phase_artifacts_index)
    reordered_events.insert(workflow_started_index, phase_artifacts_event)

    kinds = [e["event_type"] for e in reordered_events]
    assert kinds.index("phase_artifacts") < kinds.index("workflow_started"), (
        "sanity: phase_artifacts must genuinely precede workflow_started "
        "in this reordered fixture"
    )

    report = check_bd_l0(reordered_events, run_id="run-l0", writer=EventLog)
    assert report.requirements["R0.2"] == "passed", (
        f"a phase_artifacts preceding its own workflow_started (for a "
        f"phase that DOES have a workflow_started somewhere in the scoped "
        f"run) is valid — event order within a scope carries no meaning "
        f"beyond AC-L0-2's positional rule for run_identity — got "
        f"{report.requirements!r}"
    )
    assert report.passed is True, getattr(report, "violations", report)


def test_ac_l0_3e_phase_artifacts_emitted_on_error_exit(tmp_path):
    """AC-L0-3e [G2:edge-7]: phase_artifacts MUST also be emitted when the
    workflow ends error/escalate (engine.py:674, :682), not only on the ok
    path — a failed run is exactly when the artifact record matters most."""
    def _fail(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="fail_step",
            error="boom", error_code="E_TERMINAL", recoverable=False,
        )

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_3e",
        WorkflowDefinition(name="wf_l0_3e", steps=[StepContract(name="fail_step", execute=_fail)]),
    )
    result, _ctx = eng.execute("wf_l0_3e", _make_ctx(), run_id="run-l0-3e")

    assert result.status == "error"
    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        f"a workflow ending on the terminal error path (engine.py:674) must "
        f"still emit exactly one phase_artifacts, got {len(phase_artifacts)}"
    )
    assert phase_artifacts[0]["payload"]["phase"] == "wf_l0_3e"


def test_ac_l0_3e2_crash_path_step_raises_still_yields_exactly_one_phase_artifacts(tmp_path):
    """AC-L0-3e2 [G4:EDGE-4]: "unconditionally" includes the CRASH path. A
    step RAISING (or the RuntimeError at engine.py:679) propagates out of
    execute() with no try/finally around the emit, so AC-L0-3's
    "unconditionally at phase exit" is unasserted on the one exit v4 never
    drove. Normative: it holds — a step that raises, pytest.raises around
    execute(), and exactly one phase_artifacts in the log afterwards. This
    is not a false-green: the log also lacks workflow_finished, so R0.2
    fails honestly — asserted here so a future GREEN cannot silently narrow
    the claim instead of implementing it."""
    def _crash(_ctx, _prev):
        raise RuntimeError("step blew up before returning a StepResult")

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_3e2",
        WorkflowDefinition(name="wf_l0_3e2", steps=[StepContract(name="crash_step", execute=_crash)]),
    )

    with pytest.raises(RuntimeError):
        eng.execute("wf_l0_3e2", _make_ctx(), run_id="run-l0-3e2")

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        f"a step that raises must still leave exactly one phase_artifacts "
        f"record — 'unconditionally at phase exit' must hold even on the "
        f"crash path, got {len(phase_artifacts)}"
    )
    assert not any(e["event_type"] == "workflow_finished" for e in events), (
        "sanity: this is genuinely the crash path (no workflow_finished), "
        "not a normal error-status exit already covered by AC-L0-3e"
    )


def test_ac_l0_3f_written_reset_between_sequential_runs_on_one_engine(tmp_path):
    """AC-L0-3f [G2:edge-1]: whatever carries per-step written paths from
    _execute_steps to the execute() emit MUST be reset per run, as
    engine.py:237-243 already does for _same_cycle_retries. Two sequential
    execute() calls on ONE engine instance: run 2's phase_artifacts.written
    MUST NOT contain run 1's paths."""
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    def _make_write_step(fname):
        def _run(_ctx, _prev):
            (repo_dir / fname).write_text("x\n")
            return StepResult(status="ok", data=None, duration_ms=0, step_name="write_step")
        return StepContract(name="write_step", execute=_run)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3f_run1", WorkflowDefinition(name="wf_l0_3f_run1", steps=[_make_write_step("run1_file.txt")]))
    eng.register("wf_l0_3f_run2", WorkflowDefinition(name="wf_l0_3f_run2", steps=[_make_write_step("run2_file.txt")]))

    eng.execute("wf_l0_3f_run1", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3f-1")
    eng.execute("wf_l0_3f_run2", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3f-2")

    events = log.read_all()
    artifacts_run2 = [
        e for e in events
        if e["event_type"] == "phase_artifacts" and e["run_id"] == "run-l0-3f-2"
    ]
    assert len(artifacts_run2) == 1
    written_run2 = artifacts_run2[0]["payload"]["written"]
    assert "run2_file.txt" in written_run2
    assert "run1_file.txt" not in written_run2, (
        f"run 2's phase_artifacts.written must not carry run 1's paths — a "
        f"reused engine instance leaked prior-run written paths: {written_run2}"
    )


def test_ac_l0_3g_small_artifact_list_does_not_set_written_truncated(tmp_path):
    """AC-L0-3g [G2:11]: inverse control for truncation — a small artifact
    list MUST NOT set written_truncated.

    [G7:MINOR-4]: the key set of a SUCCESSFUL UNTRUNCATED git-delta payload
    is now pinned by EXACT key-set equality — AC-L0-3's whole-dict equality
    covers only the "not-observed" 5-key case and AC-L0-3d's exact-key-set
    assertion covers only the truncated 8-key case, so nothing previously
    pinned the untruncated SUCCESS case, and a GREEN that ALWAYS emits
    written_count/written_digest (even when untruncated) passed everything.
    This docstring previously claimed to be the whole-dict-shape control
    while asserting only two keys — that claim is now backed by the
    assertion below, not just stated."""
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    def _write_one(_ctx, _prev):
        (repo_dir / "one_small_file.txt").write_text("hi\n")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="write_one")

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "wf_l0_3g",
        WorkflowDefinition(name="wf_l0_3g", steps=[StepContract(name="write_one", execute=_write_one)]),
    )
    eng.execute("wf_l0_3g", _make_ctx(git_cwd=str(repo_dir)), run_id="run-l0-3g")

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1
    payload = phase_artifacts[0]["payload"]
    assert payload.get("written_truncated") is not True, (
        f"a small artifact list must NOT set written_truncated, got "
        f"{payload.get('written_truncated')!r} — an always-truncating GREEN "
        f"must not pass"
    )
    assert payload["written"] == ["one_small_file.txt"], (
        "whole-dict-shape assertion: the untruncated case must carry the "
        "real path list, not a bounded sample"
    )
    # [G7:MINOR-4]: exact key-set equality for the UNTRUNCATED success case —
    # exactly the five AC-L0-3 keys; truncation keys (written_truncated,
    # written_count, written_digest) MUST be absent, not merely unset/False.
    assert set(payload.keys()) == {
        "phase", "written", "read", "write_tracking", "read_tracking",
    }, (
        f"an untruncated git-delta payload must carry EXACTLY the five "
        f"AC-L0-3 keys — truncation keys must be ABSENT (not just falsy) "
        f"when truncation did not occur, got {sorted(payload.keys())!r}"
    )


def test_ac_l0_3d2_truncation_boundary_just_under_and_just_over_4096_bytes(tmp_path):
    """AC-L0-3d2 [G4:EDGE-6]: the truncation threshold is asserted AT ITS
    BOUNDARY. AC-L0-3d uses ~13KB and AC-L0-3g uses one path, so nothing
    exercises a payload just over or just under 4096 bytes. The size
    predicate is over the SERIALISED EVENT as EventLog.append sees it —
    envelope (ts, run_id, event_type, payload) included, ~60 bytes — not the
    payload alone (event_log.py:74/:108-116). We compute the exact predicted
    byte length of the untruncated 5-key phase_artifacts event (as pinned by
    test_ac_l0_3's whole-dict-equality) for a candidate path count using a
    fixed-length ISO timestamp placeholder (millisecond precision + "Z" is
    always 24 chars, per event_log.py's own docstring), then search for the
    exact path count N where the predicted size first exceeds 4096 — N-1
    files is the just-under run, N files is the just-over run. An off-by-one
    that measures the payload alone (missing the ~60-byte envelope) would
    misplace this boundary by one file."""
    def _predicted_event_bytes(run_id: str, phase: str, paths: list[str]) -> int:
        placeholder_ts = "2000-01-01T00:00:00.000Z"
        assert len(placeholder_ts) == 24
        payload = {
            "phase": phase,
            "written": list(paths),
            "read": [],
            "write_tracking": "git-delta",
            "read_tracking": "declared-only",
        }
        event = {
            "ts": placeholder_ts, "run_id": run_id,
            "event_type": "phase_artifacts", "payload": payload,
        }
        return len((json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"))

    def _paths_for(n: int) -> list[str]:
        return [f"boundary_probe_file_{i:05d}.txt" for i in range(n)]

    run_id_under = "run-l0-3d2-a"
    run_id_over = "run-l0-3d2-b"
    phase_under = "wf-l0-3d2-a"
    phase_over = "wf-l0-3d2-b"
    assert len(run_id_under) == len(run_id_over)
    assert len(phase_under) == len(phase_over)

    n = 0
    while _predicted_event_bytes(run_id_under, phase_under, _paths_for(n)) <= 4096:
        n += 1
    n_just_over = n
    n_just_under = n - 1
    assert n_just_under >= 1, "sanity: envelope overhead alone must not already exceed 4096 bytes"

    predicted_under = _predicted_event_bytes(run_id_under, phase_under, _paths_for(n_just_under))
    predicted_over = _predicted_event_bytes(run_id_over, phase_over, _paths_for(n_just_over))
    assert predicted_under <= 4096 < predicted_over, (predicted_under, predicted_over)

    def _run(run_id: str, phase: str, n_paths: int) -> dict:
        repo_dir = tmp_path / f"repo_{run_id}"
        _init_git_repo(repo_dir)

        def _write_paths(_ctx, _prev):
            for name in _paths_for(n_paths):
                (repo_dir / name).write_text("x")
            return StepResult(status="ok", data=None, duration_ms=0, step_name="write_probe")

        log = EventLog(tmp_path / f"events_{run_id}.jsonl")
        eng = WorkflowEngine(event_log=log)
        eng.register(phase, WorkflowDefinition(name=phase, steps=[StepContract(name="write_probe", execute=_write_paths)]))
        result, _ctx = eng.execute(phase, _make_ctx(git_cwd=str(repo_dir)), run_id=run_id)
        assert result.status == "ok"
        events = log.read_all()
        artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
        assert len(artifacts) == 1
        return artifacts[0]["payload"]

    payload_under = _run(run_id_under, phase_under, n_just_under)
    assert payload_under.get("written_truncated") is not True, (
        f"just-under-4096 run must NOT truncate, got {payload_under!r}"
    )
    assert sorted(payload_under["written"]) == sorted(_paths_for(n_just_under)), (
        "just-under-4096 run must list EVERY path, not a sample"
    )

    payload_over = _run(run_id_over, phase_over, n_just_over)
    assert payload_over.get("written_truncated") is True, (
        f"just-over-4096 run (one more file than the just-under run) MUST "
        f"truncate, got {payload_over!r} — an off-by-one measuring the "
        f"payload alone (missing the ~60-byte envelope) would misplace this "
        f"boundary"
    )


def test_ac_l0_3d4_truncated_payload_must_itself_fit_under_the_limit(tmp_path):
    """AC-L0-3d4 [G7:EDGE-3]: the TRUNCATED payload MUST itself fit under
    the limit. AC-L0-3d bounds the sample by COUNT, not by BYTES: every
    fixture uses many short paths, so sample + digest always fit. A phase
    reporting one pathological path (a synthetic ~4200-character relpath)
    produces a *truncated* payload that STILL exceeds 4096, so append
    raises EventLogLineTooLarge, _emit swallows it at engine.py:710, and
    the record vanishes entirely — the inverted control AC-L0-3d exists to
    prevent, reopened through AC-L0-3d's OWN escape hatch. Normative: the
    truncation predicate MUST bound the SERIALISED payload, dropping or
    eliding sample entries until it fits (the digest, being fixed-width,
    is what survives); an emitted phase_artifacts MUST NEVER exceed the
    limit.

    Injected through the lib.git_port get_git_read() seam (the suite's
    established pattern, matching AC-L0-3a4) rather than a real filesystem
    write — a 4200-character path component would exceed real filesystem/
    PATH_MAX limits on this host, and the checker's own declared threat
    model is exactly "an arbitrary, possibly forged event list", so a
    synthetic git-reported path is not a weaker test, it is the actual
    threat model. Asserted with a single oversized path and by requiring
    exactly one phase_artifacts with written_truncated: true to be
    present in the log, and the RAW SERIALISED LINE (as EventLog.append
    actually wrote it) to fit within 4096 bytes."""
    from lib.git_port import GitResult, reset_default_git_read_factory, set_default_git_read_factory  # noqa: PLC0415

    huge_path = "x" * 4200

    call_count = {"n": 0}

    def _spy(args, *, cwd=None, timeout=None, dir_=None):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            # git_pre: nothing yet.
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        # git_post: the pathological path appears as untracked ('A').
        if args[:1] == ["ls-files"]:
            return GitResult(returncode=0, stdout=huge_path + "\n", stderr="", timed_out=False)
        return GitResult(returncode=0, stdout="", stderr="", timed_out=False)

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3d4", WorkflowDefinition(name="wf_l0_3d4", steps=[_ok_step("noop")]))
    try:
        set_default_git_read_factory(lambda: _spy)
        result, _ctx = eng.execute(
            "wf_l0_3d4", _make_ctx(git_cwd=str(tmp_path / "fake_repo")), run_id="run-l0-3d4"
        )
    finally:
        reset_default_git_read_factory()

    assert result.status == "ok"
    assert call_count["n"] >= 4, (
        "sanity: both git_pre and git_post must actually have been queried "
        "for this test to force the pathological-path branch"
    )

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        f"a single pathological (huge) path must not silently vanish the "
        f"phase_artifacts record — the truncation predicate must bound "
        f"the SERIALISED payload, not just the sample COUNT, got "
        f"{len(phase_artifacts)}"
    )
    payload = phase_artifacts[0]["payload"]
    assert payload.get("written_truncated") is True, (
        f"a single ~4200-character path already exceeds 4096 bytes on its "
        f"own, so the truncated form MUST still be reported as "
        f"written_truncated, got {payload!r}"
    )
    assert payload.get("written_count") == 1, payload

    phase_artifacts_index = next(
        i for i, e in enumerate(events) if e["event_type"] == "phase_artifacts"
    )
    raw_line = log_path.read_bytes().splitlines(keepends=True)[phase_artifacts_index]
    assert len(raw_line) <= 4096, (
        f"the TRUNCATED payload's ACTUAL serialised line (as "
        f"EventLog.append wrote it, including the envelope and trailing "
        f"newline) must itself fit under the 4096-byte limit — got "
        f"{len(raw_line)} bytes. The sample must be dropped/elided (not "
        f"just capped by count) until the serialised form fits; the "
        f"digest, being fixed-width, is what survives"
    )


def test_ac_l0_3d5_exactly_4096_bytes_is_legal_not_truncated(tmp_path):
    """AC-L0-3d5 [G7:EDGE-4]: the limit is `> 4096`, not `>= 4096`.
    event_log.py:116 rejects only strictly-greater, so a 4096-byte line is
    LEGAL. AC-L0-3d2's search finds the largest count whose predicted size
    is `<= 4096` and generally lands short of the exact boundary for its
    identifiers, so a GREEN using `>= 4096` would still pass AC-L0-3d2 by
    truncating a payload that would have fit exactly. Normative: a payload
    serialising to EXACTLY 4096 bytes MUST NOT be truncated.

    The exact size is constructed PROGRAMMATICALLY (not hand-computed): a
    base single-path payload's serialised length is measured first, then
    a single filler path name is padded by EXACTLY the byte difference
    needed to land on 4096 — since every added filler character is one
    plain ASCII byte with no JSON escaping, this hits the boundary
    precisely rather than approaching it, and is immune to the
    hand-arithmetic error class [G7:1b] found (a boundary computed for one
    identifier pair silently reused under different ones)."""
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    placeholder_ts = "2000-01-01T00:00:00.000Z"
    run_id = "run-l0-3d5-exact"
    phase = "wf-l0-3d5-exact"

    def _event_bytes_for_path_len(path_len: int) -> int:
        path_name = "p" * path_len
        payload = {
            "phase": phase, "written": [path_name], "read": [],
            "write_tracking": "git-delta", "read_tracking": "declared-only",
        }
        event = {
            "ts": placeholder_ts, "run_id": run_id,
            "event_type": "phase_artifacts", "payload": payload,
        }
        return len((json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"))

    # Every additional filler character adds exactly one byte (plain ASCII,
    # no JSON-escaping needed), so the exact byte diff at path_len=1 gives
    # the precise padding needed to land on exactly 4096 — computed, not
    # guessed.
    base_len_at_1 = _event_bytes_for_path_len(1)
    exact_path_len = 1 + (4096 - base_len_at_1)
    assert exact_path_len >= 1, "sanity: the envelope alone must not already reach 4096 bytes"
    predicted_exact = _event_bytes_for_path_len(exact_path_len)
    assert predicted_exact == 4096, (
        f"sanity: the constructed path length must serialise to EXACTLY "
        f"4096 bytes, got {predicted_exact}"
    )

    from lib.git_port import GitResult, reset_default_git_read_factory, set_default_git_read_factory  # noqa: PLC0415

    exact_path = "p" * exact_path_len
    call_count = {"n": 0}

    def _spy(args, *, cwd=None, timeout=None, dir_=None):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        if args[:1] == ["ls-files"]:
            return GitResult(returncode=0, stdout=exact_path + "\n", stderr="", timed_out=False)
        return GitResult(returncode=0, stdout="", stderr="", timed_out=False)

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register(phase, WorkflowDefinition(name=phase, steps=[_ok_step("noop")]))
    try:
        set_default_git_read_factory(lambda: _spy)
        result, _ctx = eng.execute(
            phase, _make_ctx(git_cwd=str(tmp_path / "fake_repo")), run_id=run_id
        )
    finally:
        reset_default_git_read_factory()

    assert result.status == "ok"
    assert call_count["n"] >= 4, "sanity: both git_pre and git_post must actually have been queried"

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1
    payload = phase_artifacts[0]["payload"]

    phase_artifacts_index = next(
        i for i, e in enumerate(events) if e["event_type"] == "phase_artifacts"
    )
    raw_line = log_path.read_bytes().splitlines(keepends=True)[phase_artifacts_index]
    assert len(raw_line) == predicted_exact, (
        f"sanity: the ACTUAL serialised line must match the predicted "
        f"exact-4096 size (run_id/phase/event_type must line up with the "
        f"prediction), got {len(raw_line)}, predicted {predicted_exact}"
    )
    assert len(raw_line) <= 4096

    assert payload.get("written_truncated") is not True, (
        f"a payload serialising to EXACTLY 4096 bytes is LEGAL "
        f"(event_log.py rejects only strictly > 4096) and MUST NOT be "
        f"truncated — a GREEN using >= 4096 as its threshold would wrongly "
        f"truncate here, got {payload!r}"
    )
    assert payload.get("written") == [exact_path], (
        "the exact-boundary path must be listed in full, not sampled/elided"
    )

    report = check_bd_l0(events, run_id=run_id, writer=EventLog)
    assert report.passed is True, getattr(report, "violations", report)


def test_ac_l0_3d3_truncation_predicate_leaves_headroom_for_shadow_envelope(tmp_path, monkeypatch):
    """AC-L0-3d3 [G5:EDGE-3] [G6:MINOR-3]: the truncation predicate MUST
    leave headroom for the shadow envelope — IFF the shadow branch
    (engine.py:699-700) applies to this emit, excluded when it does not.
    _emit's shadow branch (engine.py:701-707) adds `shadowed_event` +
    `provenance` to the payload and swaps `event_type` to
    `engine_shadow_emit`, so a `phase_artifacts` the predicate measured as
    just-under 4096 UNSHADOWED exceeds the limit once shadowed and is
    swallowed at :710 — the inverted control AC-L0-3d exists to close,
    reopened on the shadow path. Every shadow fixture in this suite uses
    tiny payloads, so nothing exercises it.

    [G6:MINOR-3]: "leave headroom" MUST NOT be read as an UNCONDITIONAL
    reserve — reserving the measured shadow overhead on EVERY emit would
    truncate AC-L0-3d2's just-under (~4079-byte, UNSHADOWED) control and
    false-fail an otherwise-correct GREEN. This AC and AC-L0-3d2's
    just-under control are read TOGETHER and are jointly satisfiable only
    by measuring the actual serialised form PER EMIT. That joint
    requirement is asserted directly in this test's final assertion below:
    a NON-shadowed run at the identical borderline path count MUST NOT
    truncate — proving the predicate reserves headroom only when the
    shadow branch genuinely applies, not unconditionally.

    The overhead is MEASURED here (constructed and JSON-serialised exactly
    as EventLog.append/`_emit`'s shadow branch does — `shadow_event_name`
    leaves "phase_artifacts" unchanged since it has no "workflow_"
    substring to mangle, and `provenance` reads "foreground" since this
    test forces the shadow BRANCH via the established
    `is_authoritative_execution` monkeypatch seam (matching
    test_ac_l0_12's pattern) while genuinely running on the main thread),
    not guessed as a constant. A shadowed run is then driven at exactly
    the boundary that measurement identifies.

    [G7:1b] BLOCKING FIX: the boundary path count is a function of the
    IDENTIFIERS used (`run_id`/`phase` are part of the serialised event),
    so the unshadowed joint-satisfiability control below now RECOMPUTES
    its own boundary from the identifiers it actually uses
    (`f"{run_id}-unshadowed"` / `f"{phase}-unshadowed"`), rather than
    reusing `n_boundary` (computed for the shorter shadowed-run
    identifiers). Reusing it was unsatisfiable by ANY implementation: at
    114 paths the shadowed-run identifiers measured 4092 B unshadowed (4 B
    of slack), and the `-unshadowed` suffix on both identifiers added 22 B
    — 4114 B, 18 B over the limit — so a GREEN that truncates fails
    `written_truncated is not True` and a GREEN that does not truncate
    raises `EventLogLineTooLarge` (swallowed at `engine.py:710`, so the
    `exactly one` assertion fails instead). No third branch existed.
    Recomputing per run is robust to any future identifier rename."""
    import engine as engine_module  # noqa: PLC0415
    from execution_provenance import SHADOW_EVENT_TYPE  # noqa: PLC0415

    def _predicted_bytes(run_id: str, event_type: str, phase: str, paths: list[str], *, shadowed: bool) -> int:
        placeholder_ts = "2000-01-01T00:00:00.000Z"
        base_payload = {
            "phase": phase,
            "written": list(paths),
            "read": [],
            "write_tracking": "git-delta",
            "read_tracking": "declared-only",
        }
        if shadowed:
            payload = {
                **base_payload,
                "shadowed_event": event_type.replace("workflow_", "wf_"),
                "provenance": "foreground",
            }
            wire_event_type = SHADOW_EVENT_TYPE
        else:
            payload = base_payload
            wire_event_type = event_type
        event = {
            "ts": placeholder_ts, "run_id": run_id,
            "event_type": wire_event_type, "payload": payload,
        }
        return len((json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"))

    def _paths_for(n: int) -> list[str]:
        return [f"shadow_boundary_probe_{i:05d}.txt" for i in range(n)]

    run_id = "run-l0-3d3-shadow"
    phase = "wf-l0-3d3-shadow"

    # Find the largest N whose UNSHADOWED serialised event still fits
    # under 4096 — the boundary a headroom-BLIND predicate would use.
    n = 0
    while _predicted_bytes(run_id, "phase_artifacts", phase, _paths_for(n), shadowed=False) <= 4096:
        n += 1
    n_boundary = n - 1
    assert n_boundary >= 1, "sanity: envelope overhead alone must not already exceed 4096 bytes"

    unshadowed_bytes = _predicted_bytes(run_id, "phase_artifacts", phase, _paths_for(n_boundary), shadowed=False)
    shadowed_bytes = _predicted_bytes(run_id, "phase_artifacts", phase, _paths_for(n_boundary), shadowed=True)
    measured_overhead = shadowed_bytes - unshadowed_bytes
    assert measured_overhead > 0, "sanity: the shadow envelope must add bytes"
    assert unshadowed_bytes <= 4096 < shadowed_bytes, (
        f"sanity: at this path count the UNSHADOWED form fits (got "
        f"{unshadowed_bytes}) but the SHADOWED form exceeds 4096 (got "
        f"{shadowed_bytes}, measured overhead={measured_overhead}) — "
        f"exactly the scenario a headroom-blind predicate mishandles"
    )

    # Drive a REAL shadowed run at this exact borderline path count.
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    def _write_paths(_ctx, _prev):
        for name in _paths_for(n_boundary):
            (repo_dir / name).write_text("x")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="write_probe")

    monkeypatch.setattr(engine_module, "is_authoritative_execution", lambda: False)
    log = EventLog(tmp_path / "events.jsonl")
    eng = engine_module.WorkflowEngine(event_log=log)
    eng.register(phase, WorkflowDefinition(name=phase, steps=[StepContract(name="write_probe", execute=_write_paths)]))
    eng.execute(phase, _make_ctx(git_cwd=str(repo_dir)), run_id=run_id)

    events = log.read_all()
    shadow_phase_artifacts = [
        e for e in events
        if e["event_type"] == SHADOW_EVENT_TYPE and e["payload"].get("shadowed_event") == "phase_artifacts"
    ]
    assert len(shadow_phase_artifacts) == 1, (
        f"expected exactly one shadow-wrapped phase_artifacts event to "
        f"SURVIVE at this borderline path count; a predicate that only "
        f"measured headroom for the UNSHADOWED form would let "
        f"EventLogLineTooLarge swallow it here (engine.py:710), leaving "
        f"{len(shadow_phase_artifacts)} instead of 1"
    )
    payload = shadow_phase_artifacts[0]["payload"]
    assert payload.get("written_truncated") is True, (
        f"at a path count that fits UNSHADOWED but overflows once "
        f"shadow-wrapped (measured overhead={measured_overhead} bytes), "
        f"the predicate MUST truncate proactively to leave headroom for "
        f"the shadow envelope, got {payload!r}"
    )

    # [E2]: digest equality for the SHADOWED run too. Previously this test
    # asserted only written_truncated on the shadow path, so a GREEN
    # correct unshadowed but emitting a CONSTANT digest once shadowed would
    # pass — [G4:5]'s "a hash that cannot detect anything" reopened on the
    # one path where the sample is GUARANTEED elided (this is the
    # borderline path count, chosen precisely because it overflows once
    # shadow-wrapped).
    import hashlib  # noqa: PLC0415

    expected_shadowed_digest = "sha256:" + hashlib.sha256(
        "\n".join(sorted(_paths_for(n_boundary))).encode("utf-8")
    ).hexdigest()
    assert payload.get("written_digest") == expected_shadowed_digest, (
        f"the SHADOWED run's written_digest must equal the digest "
        f"recomputed over the real full sorted path set — a constant "
        f"digest under the shadow branch would pass written_truncated: "
        f"true alone, got {payload.get('written_digest')!r}, expected "
        f"{expected_shadowed_digest!r}"
    )

    # [G6:MINOR-3] joint-satisfiability check, [G7:1b] arithmetic FIXED: a
    # run driven WITHOUT the shadow branch MUST NOT truncate at ITS OWN
    # just-under-4096 boundary. An UNCONDITIONAL headroom reserve (rather
    # than "iff the shadow branch applies") would truncate this too, and
    # would then fail AC-L0-3d2's own just-under-4096 unshadowed control —
    # the two ACs are jointly satisfiable only by a predicate that measures
    # the actual serialised form per emit, not by a predicate that always
    # reserves the overhead.
    #
    # [G8:B1] BLOCKING FIX: the shadowed portion above monkeypatched
    # `engine_module.is_authoritative_execution` to False and NEVER undid
    # it, so `eng_unshadowed` below (the same patched `engine` module) was
    # ALSO forced onto the shadow branch — every one of its emits,
    # including phase_artifacts, was wrapped to SHADOW_EVENT_TYPE, so
    # filtering by the plain "phase_artifacts" event_type found ZERO
    # events and `len(unshadowed_artifacts) == 1` was `0 == 1` for EVERY
    # implementation. Undo the monkeypatch here, before driving the
    # "unshadowed" run, and guard against a future re-break with an
    # explicit assertion that no shadow event appears in this run's log.
    monkeypatch.undo()

    # [G7:1b]: the boundary is a function of the IDENTIFIERS, so it is
    # RECOMPUTED here from the identifiers this sub-check actually uses
    # (run_id_unshadowed / phase_unshadowed), rather than reusing
    # n_boundary (computed for the shorter shadowed-run identifiers above,
    # which left only 4 B of slack — an 11-character-longer identifier
    # pair would have overshot the limit by 18 B, unsatisfiable by any
    # implementation).
    run_id_unshadowed = f"{run_id}-unshadowed"
    phase_unshadowed = f"{phase}-unshadowed"

    n_u = 0
    while _predicted_bytes(run_id_unshadowed, "phase_artifacts", phase_unshadowed, _paths_for(n_u), shadowed=False) <= 4096:
        n_u += 1
    n_boundary_unshadowed = n_u - 1
    assert n_boundary_unshadowed >= 1, "sanity: envelope overhead alone must not already exceed 4096 bytes"
    predicted_unshadowed_bytes = _predicted_bytes(
        run_id_unshadowed, "phase_artifacts", phase_unshadowed, _paths_for(n_boundary_unshadowed), shadowed=False
    )
    assert predicted_unshadowed_bytes <= 4096, (
        f"sanity: the RECOMPUTED boundary for these identifiers must itself "
        f"fit under 4096, got {predicted_unshadowed_bytes}"
    )

    repo_dir_unshadowed = tmp_path / "repo_unshadowed"
    _init_git_repo(repo_dir_unshadowed)

    def _write_paths_unshadowed(_ctx, _prev):
        for name in _paths_for(n_boundary_unshadowed):
            (repo_dir_unshadowed / name).write_text("x")
        return StepResult(status="ok", data=None, duration_ms=0, step_name="write_probe")

    log_unshadowed = EventLog(tmp_path / "events_unshadowed.jsonl")
    eng_unshadowed = WorkflowEngine(event_log=log_unshadowed)  # is_authoritative_execution UNDONE above — genuinely unshadowed
    eng_unshadowed.register(
        phase_unshadowed,
        WorkflowDefinition(name=phase_unshadowed, steps=[StepContract(name="write_probe", execute=_write_paths_unshadowed)]),
    )
    eng_unshadowed.execute(
        phase_unshadowed, _make_ctx(git_cwd=str(repo_dir_unshadowed)), run_id=run_id_unshadowed
    )

    events_unshadowed = log_unshadowed.read_all()
    # [G8:B1] guard: loud failure if this run is ever accidentally shadowed
    # again (e.g. a future edit re-patches is_authoritative_execution
    # without undoing it before this point).
    assert not any(e["event_type"] == SHADOW_EVENT_TYPE for e in events_unshadowed), (
        "sanity: this run must be genuinely UNSHADOWED — found "
        "SHADOW_EVENT_TYPE events, meaning is_authoritative_execution is "
        "still patched to False here"
    )
    unshadowed_artifacts = [e for e in events_unshadowed if e["event_type"] == "phase_artifacts"]
    assert len(unshadowed_artifacts) == 1
    unshadowed_payload = unshadowed_artifacts[0]["payload"]
    assert unshadowed_payload.get("written_truncated") is not True, (
        f"a run driven WITHOUT the shadow branch, at ITS OWN recomputed "
        f"just-under-4096 boundary ({predicted_unshadowed_bytes} B), MUST "
        f"NOT truncate — an unconditional headroom reserve (rather than "
        f"'iff the shadow branch applies') would wrongly truncate here "
        f"too, got {unshadowed_payload!r}"
    )


def test_ac_a7b_r0_2_label_tracks_write_tracking_differential_over_two_real_runs(tmp_path):
    """AC-A7b [G2:4]: labels["R0.2"] is "writes-observed; reads-declared-only"
    ONLY when every phase was "git-delta", else "writes-not-observed;
    reads-declared-only" — asserted differentially over two real runs. v2
    hard-pinned the optimistic string while the composed attestation of
    AC-L0-11 runs without git_cwd, so its own flagship artifact would have
    published "writes-observed" for a run whose write channel was inert."""
    from conformance.attestation import build_attestation_report  # noqa: PLC0415
    from conformance.bd_l0 import check_bd_l0  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    def _run_and_attest(run_id, git_cwd):
        log = EventLog(tmp_path / f"events_{run_id}.jsonl")
        eng = WorkflowEngine(event_log=log)
        eng.register(f"wf_{run_id}", WorkflowDefinition(name=f"wf_{run_id}", steps=[_ok_step("noop")]))
        ctx = _make_ctx(git_cwd=git_cwd) if git_cwd else _make_ctx()
        eng.execute(f"wf_{run_id}", ctx, run_id=run_id)
        l0_report = check_bd_l0(log.read_all(), run_id=run_id, writer=EventLog)
        return build_attestation_report(
            level_claimed="BD-L0", results={}, l0=l0_report,
            engine_version="0.0.0-test",
            adapter_identity={"backend": "agent-sdk", "source": "default"},
            host_identity={"host": "hal-test-host"},
            repo="hal/bytedigger", commit="c" * 40, run_id=run_id,
        )

    report_observed = _run_and_attest("run-a7b-observed", str(repo_dir))
    report_not_observed = _run_and_attest("run-a7b-not-observed", None)

    assert report_observed["labels"]["R0.2"] == "writes-observed; reads-declared-only", (
        report_observed["labels"]
    )
    assert report_not_observed["labels"]["R0.2"] == "writes-not-observed; reads-declared-only", (
        report_not_observed["labels"]
    )
    assert report_observed["labels"]["R0.2"] != report_not_observed["labels"]["R0.2"]


# ═══════════════════════════════════════════════════════════════════════════
# §6 — non-regression
# ═══════════════════════════════════════════════════════════════════════════


def test_ac_p1_pyproject_packages_find_include_gains_conformance():
    """AC-P1 [G:MINOR-9]: pyproject.toml [tool.setuptools.packages.find]
    include MUST gain "conformance*" — a wheel shipped without the package
    would fail silently."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject_path.read_text(encoding="utf-8")

    try:
        import tomllib  # noqa: PLC0415
    except ImportError:  # pragma: no cover — py<3.11 fallback
        import tomli as tomllib  # type: ignore[no-redef]  # noqa: PLC0415

    data = tomllib.loads(text)
    include = data["tool"]["setuptools"]["packages"]["find"]["include"]

    assert "conformance*" in include, f"expected 'conformance*' in include, got {include}"


def test_ac_p2_core_manifest_excludes_conformance():
    """AC-P2 [G4:EDGE-1]: the manifest exclusion is a TEST, not an ops note.
    §1 makes "conformance/ is NOT in core_manifest.json" a load-bearing
    design claim — it is what keeps extra_bd at zero — yet nothing stopped
    a future GREEN from adding it, and the only prior check was a manual
    bd-drift-check.py run. AC-P1 asserts the opposite direction (the
    pyproject include) and does not cover this."""
    manifest_path = Path(__file__).resolve().parent.parent / "core_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    core_modules = data.get("core_modules", [])
    conformance_entries = [m for m in core_modules if "conformance" in m]
    assert conformance_entries == [], (
        f"engine_py/core_manifest.json must contain no 'conformance' entry "
        f"in core_modules — found {conformance_entries!r}. This is the "
        f"design invariant that keeps extra_bd at zero (§1)."
    )
