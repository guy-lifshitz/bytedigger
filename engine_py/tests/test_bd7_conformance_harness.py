"""RED tests for bd#7 — conformance harness + oracle interface + attestation
writer + BD-L0.

Spec v3 (amended after gate REJECTED v1 on 8 blocking defects, MAJOR-1..8,
then REJECTED v2 on 4 blocking defects, [G2:1]/[G2:2]/[G2:3]/[G2:4]):
SHARED/memory/Decisions/2026-07-27_bd7_conformance_harness_spec.md (frozen),
source of truth `2026-07-26_bytedigger_conformance_levels.md` §1-6, 9.

v3 [G2:*] additions close the four round-2 blocking defects:
  AC-L0-6c   R0.1 probe negative control (three inputs, not two)
  AC-A18     L0Report.passed/.violations are not ignorable
  AC-A11 (modified) / AC-A11b   level_achieved never capped by level_claimed
  AC-L0-3a / AC-A7b   write_tracking differential (git-delta vs not-observed)
plus round-2 minors/edges: AC-A19, AC-A20, AC-A21, AC-L0-3e, AC-L0-3f,
AC-L0-3g, AC-L0-12b, AC-L0-12c, AC-L0-13, AC-L0-14.

Covers every AC in the spec, one test function per AC:
  AC-O1..AC-O5   OracleOutcome — three unmergeable states, no mixin base (§2.1)
  AC-F1..AC-F11  freeze() — hash over the artifact set including membership (§2.2)
  AC-E1..AC-E9   evaluate_guarded — the indeterminate guard (§2.3)
  AC-A1..AC-A17  attestation writer (§3)
  AC-L0-1..AC-L0-12  BD-L0 engine additions + checker (§4)
  AC-P1          non-regression: pyproject packages.find.include (§6)

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


def test_ac_a6_conformant_false_when_claimed_outranks_achieved():
    """AC-A6: conformant MUST be false when level_claimed outranks
    level_achieved; the report is still written (not raised/suppressed)."""
    report = _build(level_claimed="BD-L3", results=_passed(*_L1_ADVS))

    assert report["level_achieved"] != "BD-L3"
    assert report["conformant"] is False
    assert report is not None  # the shortfall report was still produced


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


def test_ac_a8_missing_producer_identity_raises():
    """AC-A8 [G:MAJOR-8]: the report MUST carry engine_version,
    adapter_identity, host_identity, repo, commit, run_id and a UTC
    timestamp with Z suffix; a missing or empty value for any of them MUST
    raise rather than emit a report with an anonymous producer."""
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

    report = _build()
    assert report["engine_version"]
    assert report["adapter_identity"]
    assert report["host_identity"]
    assert report["repo"]
    assert report["commit"]
    assert report["run_id"]
    assert isinstance(report["timestamp"], str) and report["timestamp"].endswith("Z")


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


def test_ac_l0_2b_adapter_identity_provenance_tracks_configuration(tmp_path, monkeypatch):
    """AC-L0-2b [G:MAJOR-6]: adapter_identity MUST be {"backend": b,
    "source": s} obtained from llm_subprocess._resolve_backend(kwarg, env),
    and the emitted value MUST TRACK CONFIGURATION — set the backend via the
    env seam (HAL_RUNNER_BACKEND) and assert both backend and source reflect
    it, then change it and assert the emitted value changes. A constant
    "unknown" would pass a non-empty check while never tracking anything."""
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    log_default = EventLog(tmp_path / "events_default.jsonl")
    eng_default = WorkflowEngine(event_log=log_default)
    eng_default.register("wf_l0_2b_a", WorkflowDefinition(name="wf_l0_2b_a", steps=[_ok_step("s1")]))
    eng_default.execute("wf_l0_2b_a", _make_ctx(), run_id="run-l0-2b-a")

    events_default = log_default.read_all()
    identity_default = next(
        e for e in events_default if e["event_type"] == "run_identity"
    )["payload"]["adapter_identity"]
    assert identity_default["backend"] == "agent-sdk"
    assert identity_default["source"] == "default"

    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    log_env = EventLog(tmp_path / "events_env.jsonl")
    eng_env = WorkflowEngine(event_log=log_env)
    eng_env.register("wf_l0_2b_b", WorkflowDefinition(name="wf_l0_2b_b", steps=[_ok_step("s1")]))
    eng_env.execute("wf_l0_2b_b", _make_ctx(), run_id="run-l0-2b-b")

    events_env = log_env.read_all()
    identity_env = next(
        e for e in events_env if e["event_type"] == "run_identity"
    )["payload"]["adapter_identity"]
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
    yield one phase_artifacts."""
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_l0_3c_zero", WorkflowDefinition(name="wf_l0_3c_zero", steps=[]))
    result, _ctx = eng.execute("wf_l0_3c_zero", _make_ctx(), run_id="run-l0-3c-zero")

    assert result.status == "ok"

    events = log.read_all()
    phase_artifacts = [e for e in events if e["event_type"] == "phase_artifacts"]
    assert len(phase_artifacts) == 1, (
        f"a zero-step workflow must still emit exactly one phase_artifacts "
        f"(the emit host is execute(), not _execute_steps' early return), "
        f"got {len(phase_artifacts)}"
    )


def test_ac_l0_3d_oversized_artifact_list_does_not_vanish(tmp_path):
    """AC-L0-3d [G:edge-2]: EventLog.append raises EventLogLineTooLarge above
    4096 bytes and _emit swallows it, so a phase writing many files would
    silently lose its record. When the payload would exceed the limit,
    phase_artifacts MUST instead carry written_truncated: true,
    written_count: <n>, written_digest: "sha256:...", and a bounded written
    sample — asserted with a step writing enough paths to exceed 4096 bytes."""
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
    assert payload.get("written_truncated") is True
    assert payload.get("written_count") == n_files
    digest = payload.get("written_digest")
    assert isinstance(digest, str) and digest.startswith("sha256:"), digest
    sample = payload.get("written")
    assert isinstance(sample, list) and 0 < len(sample) < n_files, (
        f"expected a bounded sample smaller than the real count, got {len(sample) if sample else sample}"
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
    empty log MUST fail."""
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
    #    failure mode AC-L0-3c predicts on real retry runs)
    events_double_artifacts = list(_valid_l0_events())
    events_double_artifacts.append(
        _ev("phase_artifacts", {"phase": "wf", "written": [], "read": [], "read_tracking": "declared-only"})
    )
    report = _check(events_double_artifacts)
    assert report.passed is False
    assert any("R0.2" in v for v in report.violations), report.violations

    # 6) run_identity removed -> R0.3
    events_no_identity = [e for e in _valid_l0_events() if e["event_type"] != "run_identity"]
    report = _check(events_no_identity)
    assert report.passed is False
    assert any("R0.3" in v for v in report.violations), report.violations

    # 7) run_identity present but engine_version empty -> R0.3
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

    # empty log MUST NOT pass (vacuous-checker guard)
    report_empty = check_bd_l0([], run_id="run-l0", writer=EventLog)
    assert report_empty.passed is False, "check_bd_l0 must not pass an empty log"


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
    test_ac_l0_11 (identical workflow, only git_cwd differs)."""
    from conformance.attestation import build_attestation_report  # noqa: PLC0415
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

    assert report["l0"]["R0.2"] == "not-checked", report["l0"]
    assert report["level_achieved"] is None, (
        "a level cannot be granted while the write channel was never measured"
    )
    assert report["conformant"] is False
    assert report["labels"]["R0.2"] == "writes-not-observed; reads-declared-only", (
        report["labels"]
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

    # R0.3 not-checked (run_identity removed entirely), R0.1/R0.2 otherwise passed.
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
    assert not any(
        v.startswith("R0.2") and "second" in v.lower() for v in report_a.violations
    ), (
        f"run B's phase_artifacts for the same-named phase must not trip "
        f"run A's 'exactly one phase_artifacts per phase' check: "
        f"{report_a.violations}"
    )

    report_b = check_bd_l0(combined, run_id=run_id_b, writer=EventLog)
    assert report_b.passed is True, report_b.violations


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
    payload_git = next(
        e for e in log_git.read_all() if e["event_type"] == "phase_artifacts"
    )["payload"]
    assert payload_git["write_tracking"] == "git-delta", (
        f"org_config['git_cwd'] set -> a scan tree was resolved -> "
        f"write_tracking must be 'git-delta', got {payload_git.get('write_tracking')!r}"
    )

    log_none = EventLog(tmp_path / "events_none.jsonl")
    eng_none = WorkflowEngine(event_log=log_none)
    eng_none.register("wf_l0_3a_none", WorkflowDefinition(name="wf_l0_3a_none", steps=[_ok_step("noop")]))
    eng_none.execute("wf_l0_3a_none", _make_ctx(), run_id="run-l0-3a-none")  # no git_cwd at all
    payload_none = next(
        e for e in log_none.read_all() if e["event_type"] == "phase_artifacts"
    )["payload"]
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
    payload = next(e for e in events if e["event_type"] == "phase_artifacts")["payload"]
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
    list MUST NOT set written_truncated, and the payload assertion is
    whole-dict-shape so an always-truncating GREEN cannot pass."""
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
