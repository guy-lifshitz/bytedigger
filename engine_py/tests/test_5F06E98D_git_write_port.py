"""5F06E98D RED tests — GitWritePort injectable delegator (OSS seam #7, write-half).

Agreement 5F06E98D: two byte-identical _git_op_with_lock_retry implementations live
in phase_5_implement.py and phase_6_review.py.  GREEN will consolidate them into a
new module lib/git_write_port.py and replace the local bodies with thin delegators.

Acceptance Criteria tested (§3):
  AC1 — GitWritePort is @runtime_checkable; isinstance(_GitWriteSubprocess(), GitWritePort)
  AC2 — set_default_git_write_factory(lambda:Fake()) → delegator returns sentinel
  AC3 — reset_default_git_write_factory() → original impl restored
  AC4 — subprocess behavior: timeout(rc124)→(None,"timeout"), OSError→(None,"os_error"),
         rc0→(<result>,"ok"), non-lock rc!=0→(<result>,"non_lock_error")
  AC5 — persistent index.lock stderr across 3 attempts → "lock_persisted" + backoff sleeps
  AC6 — phase_5_implement._git_op_with_lock_retry and phase_6_review._git_op_with_lock_retry
         still exist as callable module attrs after GREEN (delegator preserved)
  AC7 — phase_5_implement._git_op_with_lock_retry forwards verbatim to
         git_write_port.git_op_with_lock_retry for identical args
  AC8 — literals "lock_persisted" and "for attempt in range(3)" ABSENT from phase_5/6 source;
         PRESENT in git_write_port.py source

All tests FAIL today: git_write_port module does not yet exist (AC1–AC7 raise
ImportError inside the test body); AC8 fails because the literals still live in
phase_5/6 and git_write_port.py is absent.

§1q / D1CF5FDF: "from bytedigger_engine.lib import git_write_port" is deferred to INSIDE each test
function body — never at module top — so the file is COLLECTABLE before GREEN
ships the module.  Phase_5/phase_6 imports (existing modules) happen at module top
via sys.path, mirroring test_phase_5_implement_53BB3810.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

# ── sys.path bootstrap (mirrors 53BB3810 pattern) ─────────────────────────────
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

# Phase_5 / phase_6 import at module top (they already exist — no collection risk)
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows import phase_6_review     # noqa: E402


# ─── AC1 — GitWritePort is @runtime_checkable ─────────────────────────────────


def test_5f06e98d_ac1_git_write_port_is_runtime_checkable(tmp_path):
    """GitWritePort must be a @runtime_checkable Protocol and
    isinstance(_GitWriteSubprocess(), GitWritePort) must be True.

    FAILS today: lib/git_write_port.py does not exist → ImportError inside body.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF

    port_cls = git_write_port.GitWritePort
    concrete = git_write_port._GitWriteSubprocess()

    assert isinstance(concrete, port_cls), (
        f"_GitWriteSubprocess() must satisfy GitWritePort Protocol; "
        f"isinstance returned False.  Is GitWritePort decorated with "
        f"@runtime_checkable?"
    )


# ─── AC2 — injection: set_default_git_write_factory ──────────────────────────


def test_5f06e98d_ac2_set_factory_routes_delegator_to_fake(tmp_path):
    """After set_default_git_write_factory(lambda: Fake()), the module-level
    delegator git_op_with_lock_retry must return the Fake's sentinel tuple.

    FAILS today: module absent → ImportError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    class _FakeWriter:
        def op_with_lock_retry(self, cmd, *, cwd, timeout=30):
            return ("SENTINEL", "injected")

    git_write_port.set_default_git_write_factory(lambda: _FakeWriter())
    try:
        result = git_write_port.git_op_with_lock_retry(
            ["git", "status"], cwd=str(tmp_path)
        )
    finally:
        git_write_port.reset_default_git_write_factory()

    assert result == ("SENTINEL", "injected"), (
        f"Expected delegator to return sentinel ('SENTINEL','injected') after "
        f"factory injection, got {result!r}."
    )


# ─── AC3 — reset_default_git_write_factory restores original ─────────────────


def test_5f06e98d_ac3_reset_factory_restores_original(tmp_path):
    """reset_default_git_write_factory() must restore the original concrete impl.

    FAILS today: module absent → ImportError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    class _FakeWriter:
        def op_with_lock_retry(self, cmd, *, cwd, timeout=30):
            return ("SENTINEL", "injected")

    git_write_port.set_default_git_write_factory(lambda: _FakeWriter())
    git_write_port.reset_default_git_write_factory()

    restored = git_write_port.get_git_write()
    assert isinstance(restored, git_write_port._GitWriteSubprocess), (
        f"After reset, get_git_write() must return a _GitWriteSubprocess instance; "
        f"got {type(restored)!r}."
    )


# ─── AC4 — subprocess behavior: rc124→timeout, OSError→os_error, etc. ────────


def test_5f06e98d_ac4_timeout_rc124_returns_none_timeout(tmp_path):
    """bounded_run returning rc=124 must map to (None, 'timeout').

    The port calls bounded_run which internally calls subprocess.run; patching
    phase_5_implement.subprocess.run (global infra, not UUT) simulates rc=124.

    FAILS today: module absent → ImportError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    class _Rc124:
        returncode = 124
        stdout = ""
        stderr = ""

    # Patch the subprocess module that bounded_spawn / phase_5 references.
    # The port's bounded_run resolves through phase_5_implement.subprocess at
    # the same stdlib level — patching the global module attribute is sufficient.
    with mock.patch.object(phase_5_implement.subprocess, "run", return_value=_Rc124()):
        result, outcome = git_write_port.git_op_with_lock_retry(
            ["git", "status"], cwd=str(tmp_path)
        )

    assert result is None, f"Expected None on rc=124, got {result!r}"
    assert outcome == "timeout", f"Expected 'timeout' on rc=124, got {outcome!r}"


def test_5f06e98d_ac4_oserror_returns_none_os_error(tmp_path):
    """OSError from bounded_run must map to (None, 'os_error').

    FAILS today: module absent → ImportError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    with mock.patch.object(
        phase_5_implement.subprocess, "run", side_effect=OSError("git not found")
    ):
        result, outcome = git_write_port.git_op_with_lock_retry(
            ["git", "status"], cwd=str(tmp_path)
        )

    assert result is None, f"Expected None on OSError, got {result!r}"
    assert outcome == "os_error", f"Expected 'os_error' on OSError, got {outcome!r}"


def test_5f06e98d_ac4_rc0_returns_result_ok(tmp_path):
    """rc=0 must map to (<result>, 'ok').

    FAILS today: module absent → ImportError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    class _Rc0:
        returncode = 0
        stdout = "on branch main"
        stderr = ""

    fake_result = _Rc0()
    with mock.patch.object(phase_5_implement.subprocess, "run", return_value=fake_result):
        result, outcome = git_write_port.git_op_with_lock_retry(
            ["git", "status"], cwd=str(tmp_path)
        )

    assert outcome == "ok", f"Expected 'ok' on rc=0, got {outcome!r}"
    assert result is not None, "Expected a non-None result on rc=0"


def test_5f06e98d_ac4_non_lock_error_returns_result_non_lock_error(tmp_path):
    """rc!=0 with stderr NOT containing index.lock must map to 'non_lock_error' (no retry).

    FAILS today: module absent → ImportError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    class _RcNonLock:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository"

    run_mock = mock.Mock(return_value=_RcNonLock())
    with mock.patch.object(phase_5_implement.subprocess, "run", run_mock):
        result, outcome = git_write_port.git_op_with_lock_retry(
            ["git", "add", "."], cwd=str(tmp_path)
        )

    assert outcome == "non_lock_error", (
        f"Expected 'non_lock_error' on rc!=0/non-lock stderr, got {outcome!r}"
    )
    assert run_mock.call_count == 1, (
        f"Non-lock error must NOT retry; expected 1 call, got {run_mock.call_count}"
    )


# ─── AC5 — persistent index.lock → lock_persisted + backoff sleeps ────────────


def test_5f06e98d_ac5_persistent_lock_returns_lock_persisted_with_backoff(tmp_path):
    """Persistent index.lock stderr across all 3 attempts must return
    (<last>, 'lock_persisted') and time.sleep must be called for attempts 1,2
    with args [1, 2].

    §1i: pre-stage contested resource deterministically via mock — no timing race.
    Cite: workflows.md §1i (Singleton-resource tests pre-stage state, never race).

    FAILS today: module absent → ImportError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    LOCK_STDERR = "fatal: Unable to create '.git/index.lock': File exists"

    class _LockResult:
        returncode = 1
        stdout = ""
        stderr = LOCK_STDERR

    lock_result = _LockResult()
    run_mock = mock.Mock(return_value=lock_result)
    sleep_calls: list = []

    with mock.patch.object(phase_5_implement.subprocess, "run", run_mock), \
         mock.patch.object(phase_5_implement.time, "sleep",
                           side_effect=lambda s: sleep_calls.append(s)):
        last, outcome = git_write_port.git_op_with_lock_retry(
            ["git", "add", "."], cwd=str(tmp_path)
        )

    assert outcome == "lock_persisted", (
        f"Expected 'lock_persisted' after 3 lock-error returns, got {outcome!r}"
    )
    assert run_mock.call_count == 3, (
        f"Expected 3 subprocess.run calls on persistent lock, got {run_mock.call_count}"
    )
    assert sleep_calls == [1, 2], (
        f"Expected backoff sleeps [1, 2] for attempts 1 and 2, got {sleep_calls!r}"
    )
    assert last is not None, "Expected a non-None last_result on lock_persisted"


# ─── AC6 — phase_5 and phase_6 delegators still exist as callable attrs ───────


def test_5f06e98d_ac6_phase5_git_op_with_lock_retry_is_callable():
    """phase_5_implement._git_op_with_lock_retry must exist and be callable
    (consumer-rebind patch sites stay green after GREEN replaces the body).

    FAILS today: after GREEN the delegator exists, but TODAY the test verifies
    a callable attr exists — this PASSES in RED (the original body is callable).
    Kept as a correctness guard; post-GREEN it remains green.

    Actually: this test is a preservation guard — it must PASS pre-GREEN too.
    The real failing AC6 is that the delegator forwards (AC7).
    """
    fn = getattr(phase_5_implement, "_git_op_with_lock_retry", None)
    assert callable(fn), (
        "phase_5_implement._git_op_with_lock_retry must exist and be callable"
    )
    import inspect
    sig = inspect.signature(fn)
    params = sig.parameters
    assert "cmd" in params, "Signature must include 'cmd' parameter"
    assert "cwd" in params, "Signature must include 'cwd' keyword parameter"
    assert "timeout" in params, "Signature must include 'timeout' keyword parameter"


def test_5f06e98d_ac6_phase6_git_op_with_lock_retry_is_callable():
    """phase_6_review._git_op_with_lock_retry must exist and be callable.

    Preservation guard — PASSES pre-GREEN and post-GREEN.
    """
    fn = getattr(phase_6_review, "_git_op_with_lock_retry", None)
    assert callable(fn), (
        "phase_6_review._git_op_with_lock_retry must exist and be callable"
    )
    import inspect
    sig = inspect.signature(fn)
    params = sig.parameters
    assert "cmd" in params, "Signature must include 'cmd' parameter"
    assert "cwd" in params, "Signature must include 'cwd' keyword parameter"
    assert "timeout" in params, "Signature must include 'timeout' keyword parameter"


# ─── AC7 — phase_5 delegator forwards verbatim to git_write_port ──────────────


def test_5f06e98d_ac7_phase5_delegator_forwards_to_git_write_port(tmp_path):
    """phase_5_implement._git_op_with_lock_retry(cmd, cwd=c, timeout=t) must
    return exactly the same result as git_write_port.git_op_with_lock_retry
    for identical args, proving the delegator forwards verbatim.

    FAILS today: git_write_port module absent → ImportError.
    After GREEN: phase_5's body is a thin delegator; this assertion verifies
    round-trip equality with a Fake factory injected into both.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    class _FakeWriter:
        def op_with_lock_retry(self, cmd, *, cwd, timeout=30):
            return ("DELEGATED", f"cmd={cmd},cwd={cwd},timeout={timeout}")

    git_write_port.set_default_git_write_factory(lambda: _FakeWriter())
    try:
        cmd = ["git", "status"]
        cwd = str(tmp_path)
        timeout = 15

        port_result = git_write_port.git_op_with_lock_retry(cmd, cwd=cwd, timeout=timeout)
        phase5_result = phase_5_implement._git_op_with_lock_retry(cmd, cwd=cwd, timeout=timeout)
    finally:
        git_write_port.reset_default_git_write_factory()

    assert phase5_result == port_result, (
        f"phase_5_implement._git_op_with_lock_retry must delegate verbatim to "
        f"git_write_port.git_op_with_lock_retry.\n"
        f"  port returned:   {port_result!r}\n"
        f"  phase5 returned: {phase5_result!r}\n"
        f"GREEN must replace the phase_5 body with: "
        f"return git_write_port.git_op_with_lock_retry(cmd, cwd=cwd, timeout=timeout)"
    )


# ─── AC8 — de-dup forcing function: literals moved to git_write_port only ─────


def test_5f06e98d_ac8_lock_persisted_literal_absent_from_phase5_and_phase6():
    """The RETURN form ', "lock_persisted"' must NOT appear in phase_5_implement.py
    or phase_6_review.py source after GREEN (the `return last_result, "lock_persisted"`
    statement moved to git_write_port).

    Uses the comma-prefixed form to distinguish the return statement from the 8 legitimate
    consumer-dispatch sites (`== "lock_persisted"`) that remain in phase_5/6 and are
    untouched by this seam.

    FAILS today: the return form still lives in both phase_5/6 source files.
    """
    phase5_src = Path(phase_5_implement.__file__).read_text()
    phase6_src = Path(phase_6_review.__file__).read_text()

    assert ', "lock_persisted"' not in phase5_src, (
        'phase_5_implement.py still contains \', "lock_persisted"\' — GREEN must move '
        "the retry body to git_write_port.py and replace the local body with a thin delegator."
    )
    assert ', "lock_persisted"' not in phase6_src, (
        'phase_6_review.py still contains \', "lock_persisted"\' — GREEN must move '
        "the retry body to git_write_port.py and replace the local body with a thin delegator."
    )


def test_5f06e98d_ac8_range3_loop_absent_from_phase5_and_phase6():
    """The loop literal 'for attempt in range(3)' must NOT appear in
    phase_5_implement.py or phase_6_review.py after GREEN.

    FAILS today: both files still contain the loop.
    """
    phase5_src = Path(phase_5_implement.__file__).read_text()
    phase6_src = Path(phase_6_review.__file__).read_text()

    assert "for attempt in range(3)" not in phase5_src, (
        "phase_5_implement.py still contains 'for attempt in range(3)' — "
        "GREEN must consolidate the loop into git_write_port.py."
    )
    assert "for attempt in range(3)" not in phase6_src, (
        "phase_6_review.py still contains 'for attempt in range(3)' — "
        "GREEN must consolidate the loop into git_write_port.py."
    )


def test_5f06e98d_ac8_literals_present_in_git_write_port():
    """Both 'lock_persisted' and 'for attempt in range(3)' MUST appear in
    git_write_port.py source — proving the logic landed there.

    FAILS today: git_write_port.py does not exist → FileNotFoundError.
    """
    from bytedigger_engine.lib import git_write_port  # noqa: PLC0415

    port_path = Path(git_write_port.__file__)
    port_src = port_path.read_text()

    assert '"lock_persisted"' in port_src, (
        f'git_write_port.py must contain "lock_persisted" (retry loop lives here). '
        f"Path: {port_path}"
    )
    assert "for attempt in range(3)" in port_src, (
        f"git_write_port.py must contain 'for attempt in range(3)' (retry loop lives here). "
        f"Path: {port_path}"
    )
