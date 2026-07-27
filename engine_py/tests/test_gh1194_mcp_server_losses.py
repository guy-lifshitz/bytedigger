"""RED tests for GH1194 — MCP server losses from the headless init event.

Agreement: C43577F9-3D2E-4B72-8F61-EC7F4C49476A
Spec:      SHARED/memory/Decisions/2026-07-26_C43577F9_gh1194_mcp_server_losses_spec.md

UUT (does NOT exist yet):
  * ``llm_subprocess._mcp_losses_from_events(events) -> list[dict]``
  * production side-effect 1: one ``logger.warning`` after the sole
    ``_stream_read_events(...)`` call-site (llm_subprocess.py:1431-1436)
  * production side-effect 2: ``data["mcp_server_losses"]`` beside
    ``data["worker_written_paths"]`` (llm_subprocess.py:1863)

§1q compliance
--------------
No ``spec_from_file_location`` / ``exec_module`` anywhere; no module-level
``sys.path`` mutation (``tests/conftest.py`` already puts engine_py on
``sys.path`` at conftest-import time, and installs the burn-guard PATH).
Only the MODULE is imported at top level — the not-yet-existing symbol
``_mcp_losses_from_events`` is resolved INSIDE each test body via
``_uut()``, so this file COLLECTS cleanly today and fails at ASSERT time
(a collect-time ImportError would hang red_runtime — D1CF5FDF).

§1l compliance
--------------
The UUT is never mocked, stubbed, shimmed or re-implemented here. There is
no local fallback: if ``_mcp_losses_from_events`` is missing, ``_uut()``
fails the assertion rather than substituting behaviour. The only mocked
objects are ``subprocess.Popen`` (the existing 23680DDA seam) and a logging
handler attached to the module logger to observe the production warning.

Fixture provenance
------------------
AC4 / AC5 payloads are the VERBATIM records captured from the live
CC 2.1.220 probe quoted in spec §1 (note the U+2014 em dash and the
embedded double quotes in the ``badentry`` message). AC3 uses the live
ambient shape: ``mcp_server_errors == None`` with every ``mcp_servers``
entry ``"pending"``.

Warning-text contract
---------------------
``_WarningCapture.mcp_messages`` isolates "warnings from this code path" by
filtering the module logger's WARNING records on the case-insensitive
substring ``"mcp"`` — i.e. GREEN's warning text is contractually required to
contain "mcp" (the count ACs AC8/AC9/AC12-AC16 are measured through it).

De-dup isolation (§1i / spec §2.2a)
-----------------------------------
``llm_subprocess._WARNED_MCP_LOSSES`` is module-level mutable state. The
autouse ``_reset_warned_mcp_losses`` fixture below clears it around every
test so no test observes another test's leaked names; the de-dup ACs
(AC15/AC16) deliberately do NOT clear it mid-test. The fixture resolves the
attribute defensively (it does not exist pre-GREEN) so setup never errors.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import llm_subprocess  # noqa: E402  (engine_py root on sys.path via conftest)


@pytest.fixture(autouse=True)
def _reset_warned_mcp_losses():
    """§1i isolation for the module-level de-dup set (spec §2.2a).

    Pre-stages the contested singleton deterministically instead of relying on
    test order. Defensive by design: pre-GREEN the attribute does not exist, so
    this must be a no-op rather than a setup error (a setup error would mask the
    real RED assertion failures).
    """
    def _clear():
        seen = getattr(llm_subprocess, "_WARNED_MCP_LOSSES", None)
        if seen is not None and hasattr(seen, "clear"):
            seen.clear()

    _clear()
    yield
    _clear()


# ─── Helpers (mirroring tests/test_llm_subprocess_23680DDA.py) ────────────────


def _ndjson_lines(*events: str) -> str:
    """Join NDJSON event lines into a single stdout string (23680DDA:102)."""
    return "".join(line if line.endswith("\n") else line + "\n" for line in events)


def _stream_proc(stdout_text: str, returncode: int = 0) -> MagicMock:
    """Mock Popen whose stdout iterates NDJSON lines (23680DDA:107)."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout_text)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.communicate = MagicMock(return_value=("CORRUPTED_NEVER_SEE_THIS", ""))
    return proc


# Canonical events (23680DDA:133-144).
_SYSTEM_INIT = '{"type":"system","subtype":"init","session_id":"s1"}'
_ASSISTANT_DELTA = (
    '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello "}],'
    '"usage":{"input_tokens":0,"output_tokens":0}}}'
)
_RESULT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"FINAL_ANSWER_TEXT",'
    '"usage":{"input_tokens":10,"output_tokens":20,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.0123,"duration_ms":500}'
)


# ─── Live CC 2.1.220 probe payloads (spec §1 — verbatim) ─────────────────────

# `badentry`: malformed config schema -> mcp_server_errors bucket.
_LIVE_BADENTRY_MESSAGE = (
    "Skipped — invalid MCP server config for \"badentry\": "
    "url: expected string, received undefined"
)
_LIVE_MCP_SERVER_ERRORS = [
    {
        "name": "badentry",
        "type": "invalid_config",
        "message": _LIVE_BADENTRY_MESSAGE,
    }
]

# `brokensrv`: valid config, command points at a nonexistent binary ->
# absent from mcp_server_errors, present in mcp_servers with status "failed".
_LIVE_MCP_SERVERS_FAILED = [{"name": "brokensrv", "status": "failed"}]

# A FILTERED SUBSET of the §1 live ambient sample: the raw sample also contains
# two servers already at status "failed" (`ideogram`, `telegram`) — they are
# removed here so this constant means exactly "healthy-at-init servers", usable
# as no-loss noise in the loss fixtures. "pending" is the NORMAL state at init
# (no --mcp-config override); "connected" never appears there.
_LIVE_AMBIENT_PENDING = [
    {"name": "sequential-thinking", "status": "pending"},
    {"name": "context7", "status": "pending"},
    {"name": "oscal", "status": "pending"},
]


def _init_dict(**extra) -> dict:
    """A ``type=system / subtype=init`` event dict with the given extra keys."""
    ev = {"type": "system", "subtype": "init", "session_id": "s1"}
    ev.update(extra)
    return ev


def _init_line(**extra) -> str:
    """The same init event serialised as one NDJSON line."""
    return json.dumps(_init_dict(**extra))


def _uut():
    """Resolve the real production helper — never stub it (§1l).

    Deferred to test-body call time so this module still COLLECTS before
    GREEN lands (§1q / D1CF5FDF): the RED must fail at assert time.
    """
    fn = getattr(llm_subprocess, "_mcp_losses_from_events", None)
    assert callable(fn), (
        "llm_subprocess._mcp_losses_from_events is not defined (or not "
        "callable). GH1194 spec §2.1 requires a module-level pure helper "
        "beside _written_paths_from_events. got: " + repr(fn)
    )
    return fn


class _WarningCapture(logging.Handler):
    """Records WARNING+ LogRecords emitted on the llm_subprocess module logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        if record.levelno >= logging.WARNING:
            self.records.append(record)

    @property
    def messages(self) -> list[str]:
        """Fully %-formatted warning messages (args interpolated)."""
        out = []
        for r in self.records:
            try:
                out.append(r.getMessage())
            except Exception:  # pragma: no cover - defensive
                out.append(str(r.msg))
        return out

    @property
    def mcp_messages(self) -> list[str]:
        """Warnings attributable to the GH1194 MCP-loss code path."""
        return [m for m in self.messages if "mcp" in m.lower()]


@contextlib.contextmanager
def _capture_module_warnings():
    """Attach a handler to ``llm_subprocess.logger`` (defined at :62).

    Handler-on-the-module-logger rather than caplog so the observation does
    not depend on root-logger propagation/level configuration leaking in
    from a sibling test — this must hold in an isolated single-test run.
    """
    handler = _WarningCapture()
    lg = llm_subprocess.logger
    prev_level = lg.level
    prev_disabled = lg.disabled
    lg.addHandler(handler)
    lg.setLevel(logging.WARNING)
    lg.disabled = False
    try:
        yield handler
    finally:
        lg.removeHandler(handler)
        lg.setLevel(prev_level)
        lg.disabled = prev_disabled


def _run_stream(stdout_text: str, returncode: int = 0):
    """Drive one full stream-json run through the real invoke_llm_subprocess."""
    with patch(
        "llm_subprocess.subprocess.Popen",
        return_value=_stream_proc(stdout_text, returncode=returncode),
    ):
        return llm_subprocess.invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )


# ─── AC1: no init event → [] , no warning ────────────────────────────────────


def test_ac1_no_init_event_yields_empty_and_no_warning():
    """AC1 — events without any ``system/init`` event → ``[]``, no warning."""
    fn = _uut()
    events = [
        json.loads(_ASSISTANT_DELTA),
        json.loads(_RESULT_OK),
    ]
    assert fn(events) == [], (
        "no system/init event in the stream must yield [] (nothing to report); "
        "got: " + repr(fn(events))
    )

    with _capture_module_warnings() as cap:
        result = _run_stream(_ndjson_lines(_ASSISTANT_DELTA, _RESULT_OK))
    assert cap.mcp_messages == [], (
        "a run whose stream carries no init event must emit no MCP-loss "
        "warning; got: " + repr(cap.mcp_messages)
    )
    assert result is not None


# ─── AC2: empty event list → [] ──────────────────────────────────────────────


def test_ac2_empty_events_yields_empty():
    """AC2 — ``events == []`` → ``[]``, no warning."""
    fn = _uut()
    assert fn([]) == [], "empty event list must yield []; got: " + repr(fn([]))

    with _capture_module_warnings() as cap:
        _run_stream(_ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK))
    assert cap.mcp_messages == [], (
        "an init event with no MCP keys at all must emit no MCP-loss warning; "
        "got: " + repr(cap.mcp_messages)
    )


# ─── AC3: live-ambient shape (errors=None, all pending) → [], no warning ─────


def test_ac3_live_ambient_shape_errors_none_all_pending_yields_empty():
    """AC3 — the LIVE ambient shape (spec §1): ``mcp_server_errors`` is
    ``null`` (not ``[]``, not absent) and every ``mcp_servers`` entry is
    ``"pending"``. ``pending`` is the NORMAL state at init — reporting it
    would fire on every single run.
    """
    fn = _uut()
    events = [
        _init_dict(mcp_server_errors=None, mcp_servers=_LIVE_AMBIENT_PENDING),
        json.loads(_RESULT_OK),
    ]
    out = fn(events)
    assert out == [], (
        "live ambient shape (mcp_server_errors=None + all-pending servers) "
        "must yield [] — 'pending' is the normal at-init state, not a loss. "
        "got: " + repr(out)
    )

    stdout = _ndjson_lines(
        _init_line(mcp_server_errors=None, mcp_servers=_LIVE_AMBIENT_PENDING),
        _ASSISTANT_DELTA,
        _RESULT_OK,
    )
    with _capture_module_warnings() as cap:
        _run_stream(stdout)
    assert cap.mcp_messages == [], (
        "the live ambient (all-pending) run must emit ZERO MCP-loss warnings "
        "— otherwise every single build warns. got: " + repr(cap.mcp_messages)
    )


# ─── AC4: populated mcp_server_errors → invalid_config records ───────────────


def test_ac4_mcp_server_errors_become_invalid_config_records():
    """AC4 — verbatim live ``mcp_server_errors`` entry → one record with
    ``reason == "invalid_config"``, ``name`` and ``message`` carried through
    byte-for-byte (em dash U+2014 and embedded quotes included).
    """
    fn = _uut()
    events = [
        _init_dict(mcp_server_errors=_LIVE_MCP_SERVER_ERRORS, mcp_servers=[]),
    ]
    out = fn(events)
    assert isinstance(out, list) and len(out) == 1, (
        "one mcp_server_errors entry must produce exactly one record; got: "
        + repr(out)
    )
    rec = out[0]
    assert rec.get("name") == "badentry", (
        "record name must carry the platform name verbatim; got: "
        + repr(rec.get("name"))
    )
    assert rec.get("reason") == "invalid_config", (
        "mcp_server_errors entries map to reason 'invalid_config'; got: "
        + repr(rec.get("reason"))
    )
    assert rec.get("message") == _LIVE_BADENTRY_MESSAGE, (
        "record message must carry the live CC 2.1.220 message verbatim "
        "(U+2014 em dash + escaped quotes preserved); got: "
        + repr(rec.get("message"))
    )


# ─── AC5: mcp_servers status=failed → connect_failed record ─────────────────


def test_ac5_failed_mcp_server_becomes_connect_failed_record():
    """AC5 — verbatim live ``{"name":"brokensrv","status":"failed"}`` →
    one record, ``reason == "connect_failed"``. This entry is NOT in
    ``mcp_server_errors`` — it is the everyday case (bad binary path) the
    issue is filed about.
    """
    fn = _uut()
    events = [
        _init_dict(mcp_server_errors=None, mcp_servers=_LIVE_MCP_SERVERS_FAILED),
    ]
    out = fn(events)
    assert isinstance(out, list) and len(out) == 1, (
        "one failed mcp_servers entry must produce exactly one record; got: "
        + repr(out)
    )
    rec = out[0]
    assert rec.get("name") == "brokensrv", (
        "record name must equal the failed server's name; got: "
        + repr(rec.get("name"))
    )
    assert rec.get("reason") == "connect_failed", (
        "mcp_servers[status=failed] maps to reason 'connect_failed'; got: "
        + repr(rec.get("reason"))
    )
    assert rec.get("message") == "", (
        "message must be \"\" when the platform supplies none (spec §2.1 "
        "record shape); got: " + repr(rec.get("message"))
    )


# ─── AC6: pending / connected are never reported ────────────────────────────


def test_ac6_pending_and_connected_statuses_never_reported():
    """AC6 — only ``status == "failed"`` is a loss; ``pending`` (the normal
    at-init state) and ``connected`` are never reported.
    """
    fn = _uut()
    events = [
        _init_dict(
            mcp_server_errors=None,
            mcp_servers=[
                {"name": "pendingsrv", "status": "pending"},
                {"name": "connectedsrv", "status": "connected"},
                {"name": "brokensrv", "status": "failed"},
            ],
        )
    ]
    out = fn(events)
    names = [r.get("name") for r in out]
    assert names == ["brokensrv"], (
        "only status=='failed' is a loss — 'pending' and 'connected' must "
        "never be reported (a != 'connected' predicate would fire on every "
        "run). got names: " + repr(names)
    )


# ─── AC7: both buckets, errors first ────────────────────────────────────────


def test_ac7_both_buckets_reported_errors_first_platform_order_within_bucket():
    """AC7 — both buckets populated with TWO entries each, in deliberately
    non-alphabetical platform order. The exact output name sequence is
    asserted: errors-bucket records first, servers-bucket records second,
    **and platform order preserved within each bucket**.

    Forcing function (gate MAJOR-4): the sibling helper GREEN is told to
    mirror — ``_written_paths_from_events`` (llm_subprocess.py:2403) — is
    documented as "Returns a sorted, deduplicated list". A GREEN that copies
    that sorting habit would emit
    ``["alpha", "zeta", "bravo", "yankee"]`` (or fully sorted) and must FAIL
    here. Spec §2.1 freezes platform order, not sorted order.
    """
    fn = _uut()
    events = [
        _init_dict(
            mcp_server_errors=[
                {"name": "zeta", "type": "invalid_config", "message": "z-msg"},
                {"name": "alpha", "type": "invalid_config", "message": "a-msg"},
            ],
            mcp_servers=[
                {"name": "yankee", "status": "failed"},
                # healthy noise interleaved — must not shift the surviving order
                {"name": "context7", "status": "pending"},
                {"name": "bravo", "status": "failed"},
            ],
        )
    ]
    out = fn(events)
    assert [r.get("name") for r in out] == ["zeta", "alpha", "yankee", "bravo"], (
        "exact output name sequence must be errors-bucket in platform order "
        "('zeta' BEFORE 'alpha'), then failed servers in platform order "
        "('yankee' BEFORE 'bravo'). A sorting GREEN (mirroring "
        "_written_paths_from_events:2403) fails here by design. got: " + repr(out)
    )
    assert [r.get("reason") for r in out] == [
        "invalid_config",
        "invalid_config",
        "connect_failed",
        "connect_failed",
    ], ("reason sequence must mirror the bucket sequence; got: " + repr(out))
    assert [r.get("message") for r in out] == ["z-msg", "a-msg", "", ""], (
        "messages must ride along with their own record (and be \"\" for the "
        "servers bucket); got: " + repr(out)
    )


# ─── AC8: exactly one warning naming every lost server ──────────────────────


def test_ac8_exactly_one_warning_names_every_lost_server():
    """AC8 — losses non-empty → EXACTLY ONE ``logger.warning`` from this code
    path, whose message names EVERY lost server (production side-effect 1,
    emitted right after the ``_stream_read_events`` call-site).
    """
    # Fail early + loudly if the helper itself is missing.
    _uut()
    stdout = _ndjson_lines(
        _init_line(
            mcp_server_errors=_LIVE_MCP_SERVER_ERRORS,
            mcp_servers=_LIVE_AMBIENT_PENDING + _LIVE_MCP_SERVERS_FAILED,
        ),
        _ASSISTANT_DELTA,
        _RESULT_OK,
    )
    with _capture_module_warnings() as cap:
        result = _run_stream(stdout)

    assert result.status == "ok", (
        "precondition: the fixture stream is a successful run; got status="
        + repr(result.status)
        + " err="
        + repr(result.error)
    )
    assert len(cap.mcp_messages) == 1, (
        "exactly ONE logger.warning must be emitted per run when MCP servers "
        "were lost (spec §2.2); got "
        + repr(len(cap.mcp_messages))
        + " -> "
        + repr(cap.mcp_messages)
    )
    msg = cap.mcp_messages[0]
    for lost in ("badentry", "brokensrv"):
        assert lost in msg, (
            "the warning text must NAME every lost server (silent-loss is the "
            "whole bug); " + repr(lost) + " missing from: " + repr(msg)
        )
    for healthy in ("sequential-thinking", "context7", "oscal"):
        assert healthy not in msg, (
            "pending (healthy-at-init) servers must NOT be named in the loss "
            "warning; " + repr(healthy) + " leaked into: " + repr(msg)
        )


# ─── AC9: no losses → zero warnings ─────────────────────────────────────────


def test_ac9_no_losses_emits_zero_warnings():
    """AC9 — losses empty → ZERO ``logger.warning`` calls from this code path.

    The bare ``_uut()`` call is load-bearing (gate MAJOR-2): AC9's payload
    assertion is an ABSENCE assertion, so without it this test would pass
    today purely because the feature does not exist — a false green dressed
    as a RED. Pinning the helper's existence makes it fail pre-GREEN like
    every other behavioural AC (same shape as AC8 and AC12).
    """
    _uut()
    stdout = _ndjson_lines(
        _init_line(mcp_server_errors=None, mcp_servers=_LIVE_AMBIENT_PENDING),
        _ASSISTANT_DELTA,
        _RESULT_OK,
    )
    with _capture_module_warnings() as cap:
        result = _run_stream(stdout)

    assert result.status == "ok", (
        "precondition: healthy run; got status=" + repr(result.status)
    )
    assert cap.mcp_messages == [], (
        "no losses must mean no MCP warning at all — otherwise the signal is "
        "noise. got: " + repr(cap.mcp_messages)
    )


# ─── AC10: malformed shapes never raise ─────────────────────────────────────


def test_ac10_malformed_shapes_never_raise():
    """AC10 — malformed-shape battery: each case must return a list
    (possibly empty) and never raise.
    """
    fn = _uut()
    cases = {
        "mcp_servers not a list": _init_dict(mcp_servers="not-a-list"),
        "mcp_servers entries not dicts": _init_dict(
            mcp_servers=["brokensrv", 3, None, ["x"]]
        ),
        "mcp_servers entry missing name": _init_dict(
            mcp_servers=[{"status": "failed"}]
        ),
        "mcp_server_errors a str": _init_dict(mcp_server_errors="boom"),
        "init event missing both keys": _init_dict(),
    }
    for label, init_ev in cases.items():
        try:
            out = fn([init_ev])
        except Exception as exc:  # noqa: BLE001 - the point of the AC
            raise AssertionError(
                "_mcp_losses_from_events must never raise (best-effort, "
                "mirrors _written_paths_from_events). case="
                + repr(label)
                + " raised "
                + type(exc).__name__
                + ": "
                + str(exc)
            ) from exc
        assert isinstance(out, list), (
            "case " + repr(label) + " must return a list; got " + repr(out)
        )
        for rec in out:
            assert isinstance(rec, dict), (
                "case " + repr(label) + " emitted a non-dict record: " + repr(rec)
            )
            assert isinstance(rec.get("name"), str) and rec.get("name"), (
                "case " + repr(label) + " emitted a record without a usable "
                "name: " + repr(rec)
            )
            assert rec.get("reason") in ("invalid_config", "connect_failed"), (
                "case " + repr(label) + " emitted an unknown reason: " + repr(rec)
            )


# ─── AC11: StepResult.data["mcp_server_losses"] on a successful run ─────────


def test_ac11_step_result_data_carries_mcp_server_losses():
    """AC11 — production side-effect 2: on a successful stream-json run,
    ``StepResult.data["mcp_server_losses"]`` is present and equals the
    helper's output over the same events.
    """
    fn = _uut()
    init_dict = _init_dict(
        mcp_server_errors=_LIVE_MCP_SERVER_ERRORS,
        mcp_servers=_LIVE_AMBIENT_PENDING + _LIVE_MCP_SERVERS_FAILED,
    )
    stdout = _ndjson_lines(
        json.dumps(init_dict), _ASSISTANT_DELTA, _RESULT_OK
    )
    expected = fn([init_dict, json.loads(_ASSISTANT_DELTA), json.loads(_RESULT_OK)])
    assert expected, (
        "precondition: this fixture must produce a non-empty loss list; got "
        + repr(expected)
    )

    result = _run_stream(stdout)

    assert result.status == "ok", (
        "precondition: successful run; got status="
        + repr(result.status)
        + " err="
        + repr(result.error)
    )
    assert result.data is not None
    assert "mcp_server_losses" in result.data, (
        "successful StepResult.data must carry the reserved key "
        "'mcp_server_losses' (set beside worker_written_paths, AFTER the "
        "extra_data merge). got keys: " + repr(sorted(result.data.keys()))
    )
    assert result.data["mcp_server_losses"] == expected, (
        "data['mcp_server_losses'] must equal _mcp_losses_from_events(events); "
        "got " + repr(result.data["mcp_server_losses"]) + " expected " + repr(expected)
    )


# ─── AC12: warning fires on a NON-success branch too ────────────────────────


def test_ac12_warning_fires_on_non_success_branch():
    """AC12 — loss visibility must NOT be conditional on success. Stream
    carries losses in init but ends with NO usable result event → the run
    errors, and the MCP-loss warning must still have fired exactly once.
    """
    _uut()
    stdout = _ndjson_lines(
        _init_line(
            mcp_server_errors=_LIVE_MCP_SERVER_ERRORS,
            mcp_servers=_LIVE_MCP_SERVERS_FAILED,
        ),
        _ASSISTANT_DELTA,
        # no type=result event at all -> E_LLM_NO_RESULT_EVENT branch
    )
    with _capture_module_warnings() as cap:
        result = _run_stream(stdout, returncode=0)

    assert result.status == "error", (
        "precondition: a stream with no result event must error (this is the "
        "non-success branch under test); got status=" + repr(result.status)
    )
    assert len(cap.mcp_messages) == 1, (
        "the MCP-loss warning must be emitted at the _stream_read_events "
        "call-site, so EVERY downstream branch (including the failing ones) "
        "reports losses — a failed run is exactly when a missing MCP server "
        "matters most. got " + repr(cap.mcp_messages)
    )
    msg = cap.mcp_messages[0]
    for lost in ("badentry", "brokensrv"):
        assert lost in msg, (
            "non-success-branch warning must still name every lost server; "
            + repr(lost)
            + " missing from: "
            + repr(msg)
        )


# ─── AC13: branch coverage — non-zero exit (E_LLM_EXIT, :1682) ──────────────


def test_ac13_warning_fires_on_non_zero_exit_branch():
    """AC13 — losses + ``returncode=3`` → the run errors with
    ``E_LLM_EXIT`` AND the MCP-loss warning still fired exactly once.

    Forcing function (gate MAJOR-1): §2.2 freezes the emission point at the
    ``_stream_read_events`` call-site (:1436), BEFORE every error branch. A
    GREEN that emits later — anywhere in the gap 1702…1707, just ahead of the
    success return — satisfies AC8/AC11/AC12 while silently dropping losses on
    exit-code failures. This AC closes that gap.
    """
    _uut()
    stdout = _ndjson_lines(
        _init_line(
            mcp_server_errors=_LIVE_MCP_SERVER_ERRORS,
            mcp_servers=_LIVE_MCP_SERVERS_FAILED,
        ),
        _ASSISTANT_DELTA,
        _RESULT_OK,
    )
    with _capture_module_warnings() as cap:
        result = _run_stream(stdout, returncode=3)

    assert result.status == "error", (
        "precondition: returncode=3 must error; got " + repr(result.status)
    )
    assert result.error_code == "E_LLM_EXIT", (
        "precondition: non-zero subprocess exit maps to E_LLM_EXIT (:1682); "
        "got " + repr(result.error_code)
    )
    assert len(cap.mcp_messages) == 1, (
        "the MCP-loss warning must fire on the non-zero-exit branch too — it "
        "is emitted at the _stream_read_events call-site, ahead of every "
        "error branch. got " + repr(cap.mcp_messages)
    )
    msg = cap.mcp_messages[0]
    for lost in ("badentry", "brokensrv"):
        assert lost in msg, (
            "E_LLM_EXIT-branch warning must still name every lost server; "
            + repr(lost)
            + " missing from: "
            + repr(msg)
        )


# ─── AC14: branch coverage — timeout (E_LLM_TIMEOUT, :1625) ─────────────────


def test_ac14_warning_fires_on_timeout_branch(request):
    """AC14 — losses + a stdout that delivers the init line then BLOCKS →
    the run errors ``E_LLM_TIMEOUT`` AND the MCP-loss warning fired once.

    The timeout branch returns earliest of all, so it is the strictest probe
    of the frozen emission point. Losses ARE available here: the reader thread
    appends every parsed line into the same ``events`` list (:2130-2131) that
    ``_stream_read_events`` returns at :2046-2052, so the init event is in
    hand even though the stream never terminated.

    §1i: the blocking is done with a ``threading.Event`` released by a
    finalizer (pattern from tests/test_llm_subprocess_23680DDA.py:650-674) —
    no sleep-vs-timeout race, and straggler threads unblock immediately at
    test end instead of sleeping the full window.
    """
    _uut()
    init_line = _init_line(
        mcp_server_errors=_LIVE_MCP_SERVER_ERRORS,
        mcp_servers=_LIVE_MCP_SERVERS_FAILED,
    )

    release = threading.Event()
    request.addfinalizer(release.set)

    class _BlockingStdout:
        def __init__(self) -> None:
            self._delivered = False

        def __iter__(self):
            yield init_line + "\n"
            release.wait(30)
            yield _RESULT_OK + "\n"

        def readline(self):
            if not self._delivered:
                self._delivered = True
                return init_line + "\n"
            release.wait(30)
            return ""

        def read(self, *a, **k):
            release.wait(30)
            return ""

        def close(self):
            return None

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None  # still "running" past the deadline
    proc.stdout = _BlockingStdout()
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=-9)
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("CORRUPTED_NEVER_SEE_THIS", ""))

    with _capture_module_warnings() as cap:
        with patch("llm_subprocess.subprocess.Popen", return_value=proc):
            result = llm_subprocess.invoke_llm_subprocess(
                prompt="x", model="sonnet", timeout_sec=2, step_name="s"
            )

    assert result.status == "error", (
        "precondition: blocked stdout past timeout_sec must error; got "
        + repr(result.status)
    )
    assert result.error_code == "E_LLM_TIMEOUT", (
        "precondition: deadline exceeded maps to E_LLM_TIMEOUT (:1625); got "
        + repr(result.error_code)
    )
    assert len(cap.mcp_messages) == 1, (
        "the MCP-loss warning must fire on the TIMEOUT branch — the earliest "
        "return of all. Losses are available (_stream_read_events accumulates "
        "into the returned events list). got " + repr(cap.mcp_messages)
    )
    msg = cap.mcp_messages[0]
    for lost in ("badentry", "brokensrv"):
        assert lost in msg, (
            "E_LLM_TIMEOUT-branch warning must still name every lost server; "
            + repr(lost)
            + " missing from: "
            + repr(msg)
        )


# ─── AC15: de-dup — same losses across two runs warn once in total ─────────


def test_ac15_repeat_losses_warn_once_per_process():
    """AC15 (spec §2.2a) — two runs in ONE process over the SAME losses →
    EXACTLY ONE warning in total; the second run emits zero.

    Deliberately does NOT clear ``_WARNED_MCP_LOSSES`` between the two runs —
    that is the state under test. The autouse fixture guarantees the set is
    empty at entry, so this holds in an isolated single-test run.
    """
    _uut()
    stdout = _ndjson_lines(
        _init_line(
            mcp_server_errors=_LIVE_MCP_SERVER_ERRORS,
            mcp_servers=_LIVE_MCP_SERVERS_FAILED,
        ),
        _ASSISTANT_DELTA,
        _RESULT_OK,
    )

    with _capture_module_warnings() as first:
        r1 = _run_stream(stdout)
    assert r1.status == "ok", "precondition: run 1 succeeds; got " + repr(r1.status)
    assert len(first.mcp_messages) == 1, (
        "run 1 must warn once about the newly-seen losses; got "
        + repr(first.mcp_messages)
    )

    with _capture_module_warnings() as second:
        r2 = _run_stream(stdout)
    assert r2.status == "ok", "precondition: run 2 succeeds; got " + repr(r2.status)
    assert second.mcp_messages == [], (
        "spec §2.2a: a second run surfacing only ALREADY-REPORTED (name, "
        "reason) pairs must emit ZERO warnings — otherwise a permanently "
        "broken ambient environment warns on every single LLM call and the "
        "signal is trained away. got " + repr(second.mcp_messages)
    )


# ─── AC16: de-dup does not silence a NEW loss ──────────────────────────────


def test_ac16_new_loss_warns_again_naming_only_the_new_server():
    """AC16 (spec §2.2a) — a later run that introduces an ADDITIONAL lost
    server warns again, naming ONLY the newly-seen one.

    Guards the obvious over-fix: "warn at most once per process, ever" would
    pass AC15 and re-open the silent-loss channel this ship exists to close.
    """
    _uut()
    first_stdout = _ndjson_lines(
        _init_line(mcp_server_errors=None, mcp_servers=_LIVE_MCP_SERVERS_FAILED),
        _ASSISTANT_DELTA,
        _RESULT_OK,
    )
    second_stdout = _ndjson_lines(
        _init_line(
            mcp_server_errors=None,
            mcp_servers=_LIVE_MCP_SERVERS_FAILED
            + [{"name": "freshlybroken", "status": "failed"}],
        ),
        _ASSISTANT_DELTA,
        _RESULT_OK,
    )

    with _capture_module_warnings() as first:
        _run_stream(first_stdout)
    assert len(first.mcp_messages) == 1, (
        "precondition: run 1 warns once about brokensrv; got "
        + repr(first.mcp_messages)
    )
    assert "brokensrv" in first.mcp_messages[0]

    with _capture_module_warnings() as second:
        _run_stream(second_stdout)
    assert len(second.mcp_messages) == 1, (
        "a run introducing a NEW lost server must warn again — de-dup must "
        "not become 'warn at most once per process'. got "
        + repr(second.mcp_messages)
    )
    msg2 = second.mcp_messages[0]
    assert "freshlybroken" in msg2, (
        "the second warning must name the newly-seen lost server; got "
        + repr(msg2)
    )
    assert "brokensrv" not in msg2, (
        "the second warning must name ONLY the newly-seen server — already "
        "reported (name, reason) pairs must not be repeated. got " + repr(msg2)
    )


# ─── AC17: telemetry key is full + unshadowable, independent of log de-dup ──


def test_ac17_data_key_is_full_list_and_cannot_be_shadowed():
    """AC17 (spec §2.2a last ¶ + §2.3) — ``data["mcp_server_losses"]`` carries
    the FULL loss list for the run even when the LOG was suppressed by
    de-duplication, and an ``extra_data`` entry of the same name cannot
    shadow it (the key is set AFTER the extra_data merge, :1863 discipline).

    Telemetry must not inherit a logging concern.
    """
    fn = _uut()
    init_dict = _init_dict(
        mcp_server_errors=_LIVE_MCP_SERVER_ERRORS,
        mcp_servers=_LIVE_AMBIENT_PENDING + _LIVE_MCP_SERVERS_FAILED,
    )
    stdout = _ndjson_lines(json.dumps(init_dict), _ASSISTANT_DELTA, _RESULT_OK)
    expected = fn([init_dict, json.loads(_ASSISTANT_DELTA), json.loads(_RESULT_OK)])
    assert len(expected) == 2, (
        "precondition: fixture must carry both lost servers; got " + repr(expected)
    )

    # Run 1 — burns the de-dup entries so run 2's log is suppressed.
    with _capture_module_warnings() as first:
        _run_stream(stdout)
    assert len(first.mcp_messages) == 1, (
        "precondition: run 1 warns once; got " + repr(first.mcp_messages)
    )

    with _capture_module_warnings() as second:
        with patch(
            "llm_subprocess.subprocess.Popen",
            return_value=_stream_proc(stdout),
        ):
            result = llm_subprocess.invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                extra_data={"mcp_server_losses": "SENTINEL_MUST_NOT_SURVIVE"},
            )

    assert second.mcp_messages == [], (
        "precondition: run 2's log is de-dup suppressed; got "
        + repr(second.mcp_messages)
    )
    assert result.status == "ok", (
        "precondition: run 2 succeeds; got " + repr(result.status)
    )
    assert result.data is not None
    assert result.data.get("mcp_server_losses") != "SENTINEL_MUST_NOT_SURVIVE", (
        "extra_data must NOT be able to shadow the reserved key — it is set "
        "AFTER the extra_data merge (:1863 discipline). got: "
        + repr(result.data.get("mcp_server_losses"))
    )
    assert result.data.get("mcp_server_losses") == expected, (
        "data['mcp_server_losses'] must carry the FULL loss list for the run "
        "even when the log was de-dup suppressed — telemetry must not inherit "
        "a logging concern (spec §2.2a). got: "
        + repr(result.data.get("mcp_server_losses"))
    )


# ─── AC18: reason is a mapped constant; missing message → "" ───────────────


def test_ac18_reason_is_mapped_constant_not_passthrough():
    """AC18 — an ``mcp_server_errors`` entry whose ``"type"`` is NOT
    ``"invalid_config"`` still yields ``reason == "invalid_config"``, and an
    entry with no ``message`` key yields ``""``.

    AC4's live fixture happens to carry ``type == "invalid_config"``, so it
    cannot distinguish "maps the constant" from "passes ``type`` through".
    This AC can.
    """
    fn = _uut()
    events = [
        _init_dict(
            mcp_server_errors=[
                {"name": "oddtype", "type": "some_future_platform_type",
                 "message": "m1"},
                {"name": "nomessage", "type": "invalid_config"},
            ],
            mcp_servers=None,
        )
    ]
    out = fn(events)
    assert [r.get("name") for r in out] == ["oddtype", "nomessage"], (
        "both mcp_server_errors entries must be reported in platform order; "
        "got: " + repr(out)
    )
    assert out[0].get("reason") == "invalid_config", (
        "reason must be the MAPPED constant 'invalid_config' for every "
        "mcp_server_errors entry — not the platform's `type` field passed "
        "through (entry type was 'some_future_platform_type'). got: "
        + repr(out[0].get("reason"))
    )
    assert out[0].get("message") == "m1", (
        "message must be carried verbatim; got: " + repr(out[0].get("message"))
    )
    assert out[1].get("reason") == "invalid_config", (
        "second entry reason; got: " + repr(out[1].get("reason"))
    )
    assert out[1].get("message") == "", (
        "an entry with no `message` key must yield \"\" (spec §2.1 record "
        "shape), not None and not a missing key. got: "
        + repr(out[1].get("message"))
    )


# ─── §1o guard: no existing consumer dispatches on mcp_server_losses ────────


def test_s1o_no_existing_consumer_reads_mcp_server_losses():
    """§1o — the new ``data`` key is write-only in this ship.

    Spec §4: ``mcp_server_losses`` must have NO reader anywhere in engine_py;
    the only producer-side hit is ``llm_subprocess.py`` itself (plus this
    RED file). GREEN must not add a consumer. Cheap deterministic scan.
    """
    engine_root = Path(llm_subprocess.__file__).resolve().parent
    allowed = {"llm_subprocess.py", Path(__file__).name}
    hits = sorted(
        p.name
        for p in engine_root.rglob("*.py")
        if "mcp_server_losses" in p.read_text(encoding="utf-8", errors="replace")
    )
    unexpected = [h for h in hits if h not in allowed]
    assert not unexpected, (
        "§1o: 'mcp_server_losses' is write-only this ship — no module may "
        "dispatch on it. Unexpected files under "
        + str(engine_root)
        + ": "
        + repr(unexpected)
    )
