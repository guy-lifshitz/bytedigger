"""RED tests for bd#27 (L3): oracle plugin interface + guarded evaluation.

Carries exactly the lot's 15 ACs: `AC-O1`..`AC-O5` (the three-state outcome
type) and `AC-E1`..`AC-E10` (`evaluate_guarded`). Spec:
`engine_py/conformance/ORACLE_SPEC.md` (FROZEN, committed before this file).

Deferred-import discipline (§1q), inherited from L2's RED: every
`conformance.oracle` symbol is imported **inside the test body**, never at
module level, so **collection stays clean** and today's failures are real test
failures rather than a collection error. No stub module is added — a stub is a
passability surface (`engine_py/stub_passability.py` exists to detect exactly
that class), and the deferred-import idiom reaches clean collection without one.

**Pre-passing shields: none** (spec §5). `conformance/oracle.py` does not exist
on this base, so all 15 ACs fail today. This includes the absence-shaped
assertions (AC-O3's `from_bool` sweep, AC-E9's AST check), which still need the
import and so cannot pre-pass.

`AC-E10` is one of bd#7's two chronic anchors and carried a fresh Class B defect
in four consecutive rounds, each introduced by the previous round's fix. Its
normative form is **inherited, not re-derived** (carrier `lot-bd7`
`engine_py/conformance/SPEC.md:395-400`): after the grace period, **every alive
non-main thread MUST have `daemon is True`**. No thread counting. Two hazards are
closed by construction here and are why the fixture looks the way it does:
  * the `threading.Event` in `_SlowRecordingOracle` closes the thread-startup
    race — `assert holder` alone is load-dependent, since `evaluate_guarded`
    returns at `timeout_s=0.1` while the recorder append is the oracle body's
    first statement;
  * `_run_on_non_main_thread` (AC-E9) MUST spawn a **daemon** helper and join it,
    or it becomes exactly the foreign non-daemon thread that false-fails AC-E10.
"""
from __future__ import annotations

import ast
import enum
import threading
import time
from pathlib import Path

import pytest

# ── Measured constants (spec §7.2, measured on this host at this lot's base) ──
TIMEOUT_S = 0.1          # guarded call returns at median 0.1077 s / max 0.1103 s
ORACLE_SLEEP_S = 6.0     # AC-E10 worker sleep; must EXCEED the grace
GRACE_S = 1.0            # AC-E10 grace; sleep - grace = 5.0 s of margin
EVENT_WAIT_S = 10.0      # ~190_000x the measured max thread-start latency
E3_WALLCLOCK_BOUND_S = 2.0  # 1.89 s above measured max return, 4.0 s below the sleep

_STATE = object()  # opaque `state` argument; no AC constrains it


# ──────────────────────────────────────────────────────────────────────────
# Oracle doubles. None of these imports `conformance.oracle` at module level.
# ──────────────────────────────────────────────────────────────────────────


class _OracleBase:
    """Supplies the Protocol's `freeze` member so the doubles are shape-faithful.

    `freeze` itself is AC-F1..F14 and NOT this lot (spec §0/§6).
    """

    def freeze(self, paths, *, root):  # pragma: no cover - never called here
        return "sha256:" + "0" * 64


class _ReturningOracle(_OracleBase):
    def __init__(self, value):
        self._value = value

    def evaluate(self, state):
        return self._value


class _RaisingOracle(_OracleBase):
    """Raises `exc`. Appends to `record` FIRST, so a `pytest.raises` around this
    is not vacuous: the record proves the guarded path was *entered*."""

    def __init__(self, exc, record=None):
        self._exc = exc
        self._record = record

    def evaluate(self, state):
        if self._record is not None:
            self._record.append("entered")
        raise self._exc


class _SlowOracle(_OracleBase):
    def __init__(self, sleep_s, value=None):
        self._sleep_s = sleep_s
        self._value = value

    def evaluate(self, state):
        time.sleep(self._sleep_s)
        return self._value


class _SlowRecordingOracle(_OracleBase):
    """AC-E10's liveness anchor.

    Records its own thread and sets `event` as the FIRST statements of the body,
    then sleeps LONGER than the grace period so the worker is guaranteed alive
    when the daemon predicate is evaluated.
    """

    def __init__(self, holder, event, sleep_s):
        self._holder = holder
        self._event = event
        self._sleep_s = sleep_s

    def evaluate(self, state):
        self._holder.append(threading.current_thread())
        self._event.set()
        time.sleep(self._sleep_s)
        return None  # never reached within the timeout


class _ForeignOutcome(enum.Enum):
    """AC-E4: identical member names and values, different type."""

    REJECTED = "rejected"
    ACCEPTED = "accepted"
    INDETERMINATE = "indeterminate"


class _LookalikeOutcome:
    """AC-E4: duck-types `.value`/`.name` without being an `OracleOutcome`."""

    value = "rejected"
    name = "REJECTED"


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _oracle_module():
    """Deferred import of the unit under test (§1q)."""
    import conformance.oracle as mod

    return mod


def _assert_indeterminate(outcome, reason, OracleOutcome):
    """AC-E5 is asserted HERE, so it lands on every INDETERMINATE-producing path
    (exception / load error / timeout / non-outcome return) rather than once."""
    assert outcome is OracleOutcome.INDETERMINATE
    assert outcome is not OracleOutcome.REJECTED
    assert outcome is not OracleOutcome.ACCEPTED
    assert isinstance(reason, str), f"AC-E5: reason must be a str, got {type(reason)!r}"
    assert reason.strip() != "", "AC-E5: reason must be non-empty when INDETERMINATE"


def _non_daemon_alive_non_main_threads():
    """AC-E10's predicate: every alive non-main thread MUST have `daemon is True`.

    Returns the offenders. Quantified over ALL alive non-main threads — not over
    the recorded anchor alone (`[G8:2]` Class A: a daemon worker plus a
    non-daemon watchdog passes a single-thread check and still hangs shutdown),
    and with no thread counting anywhere (`[G8:2]` Class B).
    """
    main = threading.main_thread()
    return [
        t
        for t in threading.enumerate()
        if t is not main and t.is_alive() and t.daemon is not True
    ]


def _run_on_non_main_thread(fn, timeout=30.0):
    """AC-E9 harness.

    The helper is `daemon=True` and is joined before returning — a non-daemon or
    unjoined helper would be precisely the foreign non-daemon thread that
    false-fails AC-E10 (spec §1.3), self-inflicted. Exceptions are re-raised on
    the main thread: a bare `assert` inside a thread is invisible to pytest, i.e.
    an assertion that cannot fail.
    """
    box = {}

    def runner():
        try:
            box["ok"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            box["exc"] = exc

    t = threading.Thread(target=runner, daemon=True, name="bd27-ac-e9-helper")
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "AC-E9 helper thread did not finish"
    if "exc" in box:
        raise box["exc"]
    return box.get("ok")


# ══════════════════════════════════════════════════════════════════════════
# AC-O1..AC-O5 — OracleOutcome: three states unmergeable by type
# ══════════════════════════════════════════════════════════════════════════


class TestOracleOutcomeType:
    def test_ac_o1_bool_raises_typeerror_for_every_member(self):
        """AC-O1. Per-member: a `__bool__` defined only on the member the author
        was thinking about is the standing 'each individual member omitted'
        candidate. Vacuity guard: the member is proven real FIRST, so the
        `pytest.raises` is not satisfied by a half-built object."""
        OracleOutcome = _oracle_module().OracleOutcome

        for name, value in (
            ("REJECTED", "rejected"),
            ("ACCEPTED", "accepted"),
            ("INDETERMINATE", "indeterminate"),
        ):
            member = getattr(OracleOutcome, name)
            # Guarded path proven entered on a real member.
            assert isinstance(member, OracleOutcome)
            assert member.value == value

            with pytest.raises(TypeError):
                bool(member)

            # The collapse path the AC actually names: `if outcome:`.
            with pytest.raises(TypeError):
                _ = "yes" if member else "no"

    def test_ac_o2_equality_against_bool_raises_typeerror(self):
        """AC-O2. Three members x {True, False} x BOTH operand orders (the
        standing 'operand bound to the wrong neighbour' candidate)."""
        OracleOutcome = _oracle_module().OracleOutcome

        for member in OracleOutcome:
            for other in (True, False):
                with pytest.raises(TypeError):
                    _ = member == other
                with pytest.raises(TypeError):
                    _ = other == member
                with pytest.raises(TypeError):
                    _ = member != other

    def test_ac_o2_positive_control_normal_comparison_survives(self):
        """AC-O2 positive control, and the load-bearing half: an `__eq__` that
        raises for EVERY operand satisfies the AC's letter and destroys the type.

        The set/dict assertions also kill the live trap that defining `__eq__`
        sets `__hash__ = None`, which breaks Enum set and dict-key use."""
        OracleOutcome = _oracle_module().OracleOutcome
        R = OracleOutcome.REJECTED
        A = OracleOutcome.ACCEPTED
        I = OracleOutcome.INDETERMINATE

        assert (R == R) is True
        assert (R == A) is False
        assert (I == R) is False
        assert (R == object()) is False
        assert (R == "rejected") is False

        assert len({R, A, I}) == 3
        assert {R: 1}[R] == 1

    def test_ac_o3_no_boolean_constructor(self):
        """AC-O3. (a) the named absence; (b) 'and friends' made measurable by a
        case-insensitive sweep rather than left rhetorical; (c) the constructor
        rejects booleans -- and 1/0, since `True == 1`."""
        OracleOutcome = _oracle_module().OracleOutcome

        # (a) named absence
        assert not hasattr(OracleOutcome, "from_bool")

        # (b) 'and friends' — case-folding candidate, applied in the widening
        # direction: no attribute of the class mentions `bool` in any spelling.
        bool_named = [
            n for n in dir(OracleOutcome) if "bool" in n.lower() and n != "__bool__"
        ]
        assert bool_named == [], f"AC-O3: boolean constructor(s) exposed: {bool_named}"

        # (c) no boolean (or bool-equal integer) is a valid value
        for bad in (True, False, 1, 0):
            with pytest.raises(ValueError):
                OracleOutcome(bad)

    def test_ac_o3_positive_control_constructor_still_works(self):
        """AC-O3 positive control: without it, 'the constructor raises for
        everything' passes vacuously."""
        OracleOutcome = _oracle_module().OracleOutcome

        assert OracleOutcome("rejected") is OracleOutcome.REJECTED
        assert OracleOutcome("accepted") is OracleOutcome.ACCEPTED
        assert OracleOutcome("indeterminate") is OracleOutcome.INDETERMINATE

    def test_ac_o4_three_members_distinct(self):
        """AC-O4. The live candidate is Enum ALIASING: `INDETERMINATE = "rejected"`
        silently makes the two members the same object."""
        OracleOutcome = _oracle_module().OracleOutcome

        assert OracleOutcome.INDETERMINATE is not OracleOutcome.REJECTED
        assert OracleOutcome.INDETERMINATE is not OracleOutcome.ACCEPTED
        assert OracleOutcome.REJECTED is not OracleOutcome.ACCEPTED

        assert len(OracleOutcome) == 3
        assert {m.name for m in OracleOutcome} == {
            "REJECTED",
            "ACCEPTED",
            "INDETERMINATE",
        }
        # Exact, lowercase — the case-folding candidate.
        assert {m.value for m in OracleOutcome} == {
            "rejected",
            "accepted",
            "indeterminate",
        }

    def test_ac_o5_no_mixin_base(self):
        """AC-O5. Set-equality on the MRO names, exactly as mandated. Exhaustive
        whole-collection equality: it admits no any/all/first/last reduction, so
        it carries no `[G7:4]` quantifier obligation (`[G8:1]` clause (b))."""
        OracleOutcome = _oracle_module().OracleOutcome

        mro_names = {c.__name__ for c in OracleOutcome.__mro__}
        assert mro_names == {"OracleOutcome", "Enum", "object"}, (
            f"AC-O5: mixin base present — MRO is {mro_names}"
        )

    def test_ac_o5_corollary_not_json_serialisable(self):
        """AC-O5's declared corollary (spec §3.1). The AC's rationale names the
        process boundary, so it is asserted. LIVE, not decoration: a `str`-mixin
        member serialises to a bare `"rejected"` — the collapse returning at the
        serialised form, which is what AC-O5 exists to close."""
        import json

        OracleOutcome = _oracle_module().OracleOutcome

        for member in OracleOutcome:
            with pytest.raises(TypeError):
                json.dumps(member)


# ══════════════════════════════════════════════════════════════════════════
# AC-E1..AC-E10 — evaluate_guarded
# ══════════════════════════════════════════════════════════════════════════


class _LotLocalError(Exception):
    """AC-E1: a type no plausible `except` clause enumerates by name."""


class TestEvaluateGuardedExceptions:
    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("boom"),
            RuntimeError("boom"),
            ZeroDivisionError("boom"),
            _LotLocalError("boom"),
        ],
        ids=["ValueError", "RuntimeError", "ZeroDivisionError", "LotLocalError"],
    )
    def test_ac_e1_any_exception_yields_indeterminate_never_rejected(self, exc):
        """AC-E1. Four DISSIMILAR types: the 'any' quantifier ranges over
        exception types, and a single-type fixture cannot tell `except ValueError`
        from `except Exception`."""
        mod = _oracle_module()
        outcome, reason = mod.evaluate_guarded(_RaisingOracle(exc), _STATE)
        _assert_indeterminate(outcome, reason, mod.OracleOutcome)

    @pytest.mark.parametrize(
        "exc",
        [ImportError("no module"), SyntaxError("bad syntax")],
        ids=["ImportError", "SyntaxError"],
    )
    def test_ac_e2_load_errors_yield_indeterminate(self, exc):
        """AC-E2. Asserted per type, not via one representative — the standing
        'each individual member of a defaulted set omitted' candidate."""
        mod = _oracle_module()
        outcome, reason = mod.evaluate_guarded(_RaisingOracle(exc), _STATE)
        _assert_indeterminate(outcome, reason, mod.OracleOutcome)


class TestEvaluateGuardedTimeout:
    def test_ac_e3_timeout_yields_indeterminate(self):
        """AC-E3, plus the added wall-clock bound (spec §3.2).

        Without the bound, an implementation that JOINS the worker
        unconditionally satisfies every outcome assertion while defeating the
        entire point of a timeout, and no AC notices. The bound is measured:
        2.0 s sits 1.89 s above the measured max return and 4.0 s below the
        oracle's sleep, so it cannot flake and a joining implementation misses it
        by ~4 s."""
        mod = _oracle_module()
        oracle = _SlowOracle(ORACLE_SLEEP_S, value=mod.OracleOutcome.ACCEPTED)

        started = time.perf_counter()
        outcome, reason = mod.evaluate_guarded(oracle, _STATE, timeout_s=TIMEOUT_S)
        elapsed = time.perf_counter() - started

        _assert_indeterminate(outcome, reason, mod.OracleOutcome)
        assert elapsed < E3_WALLCLOCK_BOUND_S, (
            f"AC-E3: evaluate_guarded took {elapsed:.3f}s against a "
            f"{ORACLE_SLEEP_S}s oracle and a {TIMEOUT_S}s timeout — the worker "
            f"is being joined rather than abandoned"
        )

    @pytest.mark.parametrize(
        "member_name", ["ACCEPTED", "REJECTED"], ids=["ACCEPTED", "REJECTED"]
    )
    def test_ac_e8_positive_control_timeout_branch_is_reachable(self, member_name):
        """AC-E8. A fast oracle with a FINITE timeout returns its real outcome.

        Kills `if timeout_s is not None: return INDETERMINATE`, which satisfies
        AC-E3 and every other AC by never reaching a verdict at all. BOTH members,
        because a `timeout_s is not None -> ACCEPTED` implementation passes an
        ACCEPTED-only positive control."""
        mod = _oracle_module()
        member = getattr(mod.OracleOutcome, member_name)

        outcome, reason = mod.evaluate_guarded(
            _ReturningOracle(member), _STATE, timeout_s=TIMEOUT_S
        )

        assert outcome is member
        assert reason is None


class TestEvaluateGuardedReturnValues:
    @pytest.mark.parametrize(
        "value",
        [True, False, None, 0, 1, "rejected", "accepted", _LookalikeOutcome(),
         _ForeignOutcome.REJECTED],
        ids=["True", "False", "None", "int_0", "int_1", "str_rejected",
             "str_accepted", "lookalike_obj", "foreign_enum"],
    )
    def test_ac_e4_non_outcome_return_yields_indeterminate(self, value):
        """AC-E4. The enumeration IS the requirement.

        `True`/`False` kill truthiness coercion; `"rejected"`/`"accepted"` kill
        `OracleOutcome(r)` value-coercion, which would happily manufacture a real
        REJECTED out of a string; the lookalike object and the foreign Enum kill
        duck-typing on `.value`/`.name`; `None` kills 'no verdict means it passed'."""
        mod = _oracle_module()
        outcome, reason = mod.evaluate_guarded(_ReturningOracle(value), _STATE)
        _assert_indeterminate(outcome, reason, mod.OracleOutcome)

    @pytest.mark.parametrize(
        "member_name", ["REJECTED", "ACCEPTED"], ids=["REJECTED", "ACCEPTED"]
    )
    def test_ac_e6_clean_outcome_passes_through_unchanged(self, member_name):
        """AC-E6. BOTH members.

        The `REJECTED` half is the one that matters: an implementation that
        over-reads AC-E1's 'never REJECTED' as 'never RETURN REJECTED' collapses
        the clean-rejection path to INDETERMINATE, and only this half catches it.
        `reason is None` is identity, not falsiness — `""` is a distinct and
        plausible wrong answer."""
        mod = _oracle_module()
        member = getattr(mod.OracleOutcome, member_name)

        outcome, reason = mod.evaluate_guarded(_ReturningOracle(member), _STATE)

        assert outcome is member
        assert reason is None


class TestEvaluateGuardedBaseExceptions:
    @pytest.mark.parametrize(
        "exc_type", [KeyboardInterrupt, SystemExit], ids=["KeyboardInterrupt", "SystemExit"]
    )
    @pytest.mark.parametrize(
        "timeout_s", [None, TIMEOUT_S], ids=["no_timeout", "finite_timeout"]
    )
    def test_ac_e7_keyboardinterrupt_and_systemexit_not_caught(self, exc_type, timeout_s):
        """AC-E7, asserted under BOTH `timeout_s=None` and a finite `timeout_s`.

        This is the kill a single-path fixture misses. `KeyboardInterrupt` and
        `SystemExit` derive from `BaseException`, so they do NOT propagate out of
        a worker thread on their own: an implementation that runs inline when
        `timeout_s is None` and threaded otherwise passes a `None`-only fixture
        while SILENTLY SWALLOWING the interrupt on the guarded path — the very
        path the AC is about.

        Vacuity guard (mandatory): `pytest.raises` here is vacuous unless the
        guarded path is proven entered, so the double records before raising and
        the record is asserted."""
        mod = _oracle_module()
        record = []
        oracle = _RaisingOracle(exc_type(), record=record)

        with pytest.raises(exc_type):
            mod.evaluate_guarded(oracle, _STATE, timeout_s=timeout_s)

        assert record == ["entered"], (
            "AC-E7 vacuity guard: the oracle body never ran, so the raises "
            "assertion proves nothing about the guarded path"
        )


class TestEvaluateGuardedNotSignalBased:
    def test_ac_e9_behaves_identically_off_the_main_thread(self):
        """AC-E9, normative half: AC-E3 and AC-E8 must BOTH hold off the main
        thread. A `signal`-based timeout raises `ValueError` off the main thread;
        one that catches that and silently skips the timeout is killed by the
        AC-E3 half, which would then see the oracle's real value instead of
        INDETERMINATE."""
        mod = _oracle_module()

        def body():
            # AC-E3 half
            outcome, reason = mod.evaluate_guarded(
                _SlowOracle(ORACLE_SLEEP_S, value=mod.OracleOutcome.ACCEPTED),
                _STATE,
                timeout_s=TIMEOUT_S,
            )
            _assert_indeterminate(outcome, reason, mod.OracleOutcome)

            # AC-E8 half
            for member_name in ("ACCEPTED", "REJECTED"):
                member = getattr(mod.OracleOutcome, member_name)
                outcome, reason = mod.evaluate_guarded(
                    _ReturningOracle(member), _STATE, timeout_s=TIMEOUT_S
                )
                assert outcome is member
                assert reason is None
            return True

        assert _run_on_non_main_thread(body) is True

    def test_ac_e9_module_never_imports_signal(self):
        """AC-E9, seam half — the decisive pin.

        Naming a seam is not enough; the property the interception depends on
        must be pinned. A monkeypatched `signal.alarm` is only reached if the
        implementation resolves the attribute at CALL time (`import signal;
        signal.alarm(...)`). `from signal import alarm` binds at IMPORT time and
        slips straight past the patch. That resolution-moment hole cannot be
        closed by patching, so the decisive assertion is source-level and exact:
        `oracle.py` imports `signal` in NO form. No normalisation is relied on."""
        mod = _oracle_module()
        source = Path(mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_roots.add(node.module.split(".")[0])

        assert "signal" not in imported_roots, (
            "AC-E9: oracle.py imports `signal` — the timeout mechanism must not "
            "be signal-based (it would not work off the main thread)"
        )

    def test_ac_e9_no_signal_calls_during_guarded_evaluation(self, monkeypatch):
        """AC-E9, call-time-resolution pin.

        RECORD AND DELEGATE, never substitute. `pytest-timeout` runs this very
        suite on the signal mechanism (spec §1.3), so replacing `signal.signal`
        with a raiser or a no-op would sabotage a primitive the harness itself
        runs on — bd#22's session-killing defect. Every wrapper below calls
        through to the real function; only the call is observed."""
        import signal as signal_mod

        calls = []
        for attr in ("signal", "alarm", "setitimer"):
            real = getattr(signal_mod, attr, None)
            if real is None:
                continue

            def recorder(*args, _attr=attr, _real=real, **kwargs):
                calls.append(_attr)
                return _real(*args, **kwargs)  # delegate

            monkeypatch.setattr(signal_mod, attr, recorder)

        mod = _oracle_module()
        outcome, reason = mod.evaluate_guarded(
            _SlowOracle(ORACLE_SLEEP_S, value=mod.OracleOutcome.ACCEPTED),
            _STATE,
            timeout_s=TIMEOUT_S,
        )
        _assert_indeterminate(outcome, reason, mod.OracleOutcome)

        assert calls == [], (
            f"AC-E9: evaluate_guarded called signal.{{{','.join(sorted(set(calls)))}}} "
            f"during a guarded evaluation"
        )


class TestEvaluateGuardedAbandonedWorker:
    def test_ac_e10_offender_helper_reports_a_non_daemon_thread(self):
        """AC-E10's quantifier obligation, non-uniform member.

        The §4.3 row requires a non-uniform fixture (daemon worker + non-daemon
        auxiliary) and an all-daemon positive control. This test IS the
        non-uniform member: it spawns a deliberate non-daemon thread and requires
        the predicate to report it. Without it, `offenders == []` in the next test
        is an assertion with no demonstrated ability to fail — invisible in the
        pass/fail count and misclassifiable as an inherited gap, which is exactly
        the failure mode the coverage-diff rule names for ADDED assertions.

        The deliberate thread is joined before returning, or it would poison the
        very assertion it exists to validate."""
        started = threading.Event()
        release = threading.Event()

        def body():
            started.set()
            release.wait(EVENT_WAIT_S)

        offender = threading.Thread(
            target=body, daemon=False, name="bd27-deliberate-non-daemon"
        )
        offender.start()
        try:
            assert started.wait(EVENT_WAIT_S), "deliberate offender thread never started"
            reported = _non_daemon_alive_non_main_threads()
            assert offender in reported, (
                "AC-E10 predicate cannot fail: it did not report a live "
                "non-daemon thread"
            )
        finally:
            release.set()
            offender.join(EVENT_WAIT_S)

        assert not offender.is_alive(), "deliberate offender thread outlived its test"

    def test_ac_e10_abandoned_worker_cannot_hang_shutdown(self):
        """AC-E10, positive control and the AC proper.

        Normative form INHERITED, not re-derived (carrier lot-bd7 SPEC.md:395-400):
        after the grace period, EVERY alive non-main thread MUST have
        `daemon is True`. No thread counting anywhere — counting false-failed a
        cold pool twice (`[G8:2]`), and a requirement whose verdict depends on
        test order is not a requirement.

        The `Event` closes the inherited thread-startup race: `evaluate_guarded`
        returns at `timeout_s=0.1` while the recorder append is the oracle body's
        first statement, so `assert holder` alone is load-dependent."""
        mod = _oracle_module()
        holder = []
        recorded = threading.Event()
        oracle = _SlowRecordingOracle(holder, recorded, ORACLE_SLEEP_S)

        outcome, reason = mod.evaluate_guarded(oracle, _STATE, timeout_s=TIMEOUT_S)
        _assert_indeterminate(outcome, reason, mod.OracleOutcome)

        # Close the startup race BEFORE touching the holder.
        assert recorded.wait(EVENT_WAIT_S), (
            "AC-E10: the oracle body never ran, so there is no abandoned worker "
            "to make a claim about"
        )
        assert holder, "AC-E10: no worker thread was recorded"
        worker = holder[0]

        time.sleep(GRACE_S)

        # Liveness anchor: provably alive, since its sleep outlasts the grace.
        assert worker.is_alive(), (
            f"AC-E10: the recorded worker died inside the {GRACE_S}s grace, so "
            f"this fixture cannot distinguish reaping from luck"
        )

        offenders = _non_daemon_alive_non_main_threads()
        assert offenders == [], (
            "AC-E10: alive non-main thread(s) with daemon is not True — these can "
            f"hang interpreter shutdown: {[t.name for t in offenders]}"
        )
