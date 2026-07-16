"""bounded_spawn — universal bounded subprocess wrapper (A375DD88).

Single source-of-truth (§1g) for engine_py subprocess spawning discipline.
Exports ``bounded_run`` — a drop-in for ``subprocess.run`` with a mandatory
keyword-only ``timeout`` argument so an unbounded spawn through this chokepoint
is impossible by construction.
"""
import subprocess
import logging

logger = logging.getLogger(__name__)

TIMEOUT_RETURNCODE = 124  # shell timeout convention (matches engine EXIT_CODE=124 usage)


def bounded_run(cmd, *, timeout, **kwargs):
    """subprocess.run with a MANDATORY keyword-only timeout.

    `timeout` is keyword-only with NO default → omitting it raises TypeError.
    This is the forcing function: an unbounded engine spawn through this
    chokepoint is impossible by construction.

    On subprocess.TimeoutExpired: logs a warning and returns a sentinel
    CompletedProcess(returncode=124, empty stdout/stderr) instead of raising,
    so existing call-sites (which branch on returncode / swallow on failure)
    degrade gracefully and uniformly rather than propagating a new exception
    type. The timeout is never silent — it is logged at WARNING.

    All other kwargs forward to subprocess.run unchanged.
    """
    try:
        return subprocess.run(cmd, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        logger.warning("bounded_run timeout after %ss: %r", timeout, cmd)
        text_mode = bool(
            kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding")
        )
        empty = "" if text_mode else b""
        return subprocess.CompletedProcess(
            args=cmd, returncode=TIMEOUT_RETURNCODE, stdout=empty, stderr=empty
        )
