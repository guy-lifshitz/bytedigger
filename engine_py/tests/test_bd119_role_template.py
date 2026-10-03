"""bd#119 -- one bounded, fail-closed reader for org_config["role_template_path"].

RED tests for build-spec.md section 6 (groups A-H). The module under test
(`bytedigger_engine.role_template`) and `contracts.CodedStepError` are reached
only through `_rt()` / `_rt_contracts()` inside test bodies, so a missing API
fails the test, never collection.

RED-signal contract (spec section 6):
- Fail by assertion on the old tree: B1-B4, C- (16 rows through `_run_step`),
  C-engine, `test_c_p2_invoke_does_not_reopen_template`,
  `test_p2_template_opened_once_across_build_and_invoke`, F1-F3, F5-F9, H1, G6,
  (the phase 2 inversion in test_phase_2_explore.py is retired by bd#89 P2a).
- Fail by ImportError / AttributeError / KeyError raised inside the test body:
  A2-A12, A15-A22, `test_fstat_is_authoritative_after_open`,
  `test_valid_read_uses_one_fd_and_closes_it`, `test_read_error_is_unreadable_and_fd_closed`,
  `test_reason_tokens_and_constants`, `test_load_role_template_returns_none_for_unset_values`,
  `test_role_template_module_contract`, D1, D2, G1-G5, H2, H3;
  `test_c_bad_bytes_error_result_not_raise` fails on the UnicodeDecodeError the old reader raises.
- PRE-PASSING GUARD (labelled at the definition, excluded from the RED count):
  A1, A13, A14, C+, F4, H1b, `test_sentinel_written_for_ok_result`, the B3
  extractor fail-loud tests (they check this file's own extractor), and the
  template-free path tests (p45 / p5 delta retry; unchanged behavior).
"""
from __future__ import annotations

import ast
import errno
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from bytedigger_engine import engine as engine_module
from bytedigger_engine import llm_subprocess
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import (
    LoopStepContract,
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import LoopRunner, WorkflowEngine
from bytedigger_engine.lib.step_sentinel import maybe_read_sentinel
from bytedigger_engine.llm_subprocess import register_backend, reset_backends
from bytedigger_engine.workflows import phase_workflows_common as common
from bytedigger_engine.workflows import phase_45_spec as p45
from bytedigger_engine.workflows import phase_5_implement as p5
from bytedigger_engine.workflows import phase_5_integrity as p5i
from bytedigger_engine.workflows import phase_6_fix_integrity as p6fi
from bytedigger_engine.workflows import phase_6_review as p6

ENGINE_PY = Path(__file__).resolve().parent.parent
PKG = ENGINE_PY / "bytedigger_engine"
REPO = ENGINE_PY.parent

KEY = "role_template_path"
CODE = "E_ROLE_TEMPLATE_INVALID"
SUGGESTION = "fix or unset org_config.role_template_path"
CANARY = "BD119-CANARY-9f3e"
REASONS = (
    "bad_value_type", "invalid_path", "missing", "unreadable", "not_regular_file",
    "over_cap", "not_utf8", "contains_nul", "empty",
)
ATTEST_EVENT = "model_invocation_attested"

# F6: literal substrings shared with the docs writer (spec 5.6).
CONFIG_DOC_TRUST_PHRASES = (
    "org_config",
    "never read from bytedigger.json",
    "credentials",
    "tokens",
    "gate-integrity",
    "expanduser",
    "bd#116",
)


def _rt():
    """The module under test, imported at call time (never at collection)."""
    return importlib.import_module("bytedigger_engine.role_template")


def _rt_contracts():
    """`contracts` with the new CodedStepError; AttributeError before GREEN."""
    mod = importlib.import_module("bytedigger_engine.contracts")
    mod.CodedStepError  # noqa: B018 -- the attribute access IS the RED signal
    return mod


def _run_step(engine, step, ctx, prev):
    """Spec section 6: the single fallback helper for every C- / D2 / C-engine test.

    After GREEN the engine's `_execute_step` converts a CodedStepError; before
    GREEN the step runs directly. Never catches.
    """
    fn = getattr(engine, "_execute_step", None)
    if fn is not None:
        return fn(step, ctx, prev)
    return step.execute(ctx, prev)


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-bd119",
        persona="hal",
        framework=None,
        domain=None,
    )


def _org_ctx(org_config) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org_config,
        question="q", session_id="bd119", persona="hal", framework=None, domain=None,
    )


def _load(value):
    return _rt().load_role_template({KEY: value})


def _err(value):
    rt = _rt()
    with pytest.raises(rt.RoleTemplateError) as ei:
        rt.load_role_template({KEY: value})
    return ei.value


def _oracle(path: Path) -> str:
    """The pre-bd#119 reader, verbatim."""
    return Path(path).expanduser().read_text(encoding="utf-8").rstrip() + "\n\n"


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class _OpenSpy:
    """Records every os.open path while installed."""

    def __init__(self, monkeypatch):
        self.paths: list[str] = []
        real = os.open

        def _spy(path, flags, *a, **kw):
            self.paths.append(os.fsdecode(path) if not isinstance(path, int) else str(path))
            return real(path, flags, *a, **kw)

        monkeypatch.setattr(os, "open", _spy)


# Spec 5.1 step 5: the flags the single fd MUST carry (a flag absent on the platform is skipped).
_REQUIRED_OPEN_FLAGS = 0
for _flag in ("O_NONBLOCK", "O_NOCTTY", "O_CLOEXEC"):
    _REQUIRED_OPEN_FLAGS |= getattr(os, _flag, 0)


class _FdSpy:
    """Records (path, flags, fd) for every os.open and every fd passed to os.close."""

    def __init__(self, monkeypatch):
        self.opened: list[tuple[str, int, int]] = []
        self.closed: list[int] = []
        real_open, real_close = os.open, os.close

        def _open(path, flags, *a, **kw):
            fd = real_open(path, flags, *a, **kw)
            self.opened.append((os.fsdecode(path), flags, fd))
            return fd

        def _close(fd):
            self.closed.append(fd)
            return real_close(fd)

        monkeypatch.setattr(os, "open", _open)
        monkeypatch.setattr(os, "close", _close)

    def opens_of(self, path: Path) -> list[tuple[int, int]]:
        return [(flags, fd) for (p, flags, fd) in self.opened if p == str(path)]

    def fds_of(self, path: Path) -> set[int]:
        return {fd for (_flags, fd) in self.opens_of(path)}


class _FakeEventLog:
    """Records (event_type, payload, run_id); duck-types the EventSink contract."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def payloads(self, event_type: str) -> list[dict]:
        return [p for (t, p, _) in self.events if t == event_type]


class _CountingBackend:
    """Counts dispatches and returns a fixed ok payload that merges extra_data."""

    def __init__(self, raw: str = "findings\n\nSTATUS: DONE\n") -> None:
        self.calls: list[dict] = []
        self._raw = raw

    def __call__(self, **kw) -> StepResult:
        self.calls.append(dict(kw))
        data = {
            "raw_response": self._raw,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok", data=data, duration_ms=0,
            step_name=kw.get("step_name", "stub"),
        )


def _register(backend, capabilities=("manifest", "progress_since", "abort", "tool_allowlist")) -> None:
    register_backend(
        "claude-subprocess",
        backend,
        manifest_source="harness_tool_record",
        capabilities=frozenset(capabilities),
        overwrite=True,
    )


def _ledger(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _bd119_isolation(monkeypatch, tmp_path):
    """Backend registry and telemetry slot are process-wide singletons; the
    incident ledger is pointed at this test's tmp dir."""
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.setenv("BD_INCIDENT_LOG", str(tmp_path / "incidents.jsonl"))
    reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ---------------------------------------------------------------------------
# A. Reader unit
# ---------------------------------------------------------------------------

# PRE-PASSING GUARD
def test_unset_values_yield_no_template(tmp_path, monkeypatch):
    """A1: absent, None, "" and org_config=None yield "" through the wrapper, no file access."""
    spy = _OpenSpy(monkeypatch)
    for org in ({}, {KEY: None}, {KEY: ""}, None):
        assert common._maybe_role_template(_org_ctx(org)) == "", org
    assert spy.paths == []


def test_load_role_template_returns_none_for_unset_values(monkeypatch):
    """AC2 on the reader API: unset values return None with no stat and no open."""
    rt = _rt()
    stats: list[object] = []
    real_stat = os.stat

    def _stat_spy(path, *a, **kw):
        stats.append(path)
        return real_stat(path, *a, **kw)

    spy = _OpenSpy(monkeypatch)
    monkeypatch.setattr(os, "stat", _stat_spy)
    for org in ({}, {KEY: None}, {KEY: ""}, None):
        assert rt.load_role_template(org) is None, org
    assert stats == []
    assert spy.paths == []


def test_whitespace_only_value_is_missing(tmp_path, monkeypatch):
    """A2."""
    monkeypatch.chdir(tmp_path)
    exc = _err("   ")
    assert exc.reason == "missing"
    assert exc.path == "   "


def test_non_str_value_is_bad_value_type(tmp_path):
    """A3: non-str values (falsy ones included) are bad_value_type; type name only."""
    valid = tmp_path / "role.md"
    valid.write_text("ok\n", encoding="utf-8")
    for value in (0, False, 1, True, [], {}, {"k": CANARY}, Path(valid)):
        exc = _err(value)
        assert exc.reason == "bad_value_type", value
        assert exc.path is None
        msg = str(exc)
        assert msg.startswith("role_template_path: bad_value_type"), msg
        assert f"got {type(value).__name__}" in msg, msg
        assert CANARY not in msg
        assert CANARY not in repr(exc)


def test_nonexistent_and_enotdir_are_missing(tmp_path):
    """A4: ENOENT / ENOTDIR -> missing; errno name only, no strerror."""
    nope = tmp_path / "nope.md"
    exc = _err(str(nope))
    assert exc.reason == "missing"
    assert "ENOENT" in str(exc)
    assert "No such file" not in str(exc)

    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    exc = _err(str(f / "sub"))
    assert exc.reason == "missing"
    assert "ENOTDIR" in str(exc)
    assert "Not a directory" not in str(exc)


def test_directory_is_not_regular_file(tmp_path, monkeypatch):
    """A5: rejected by the stat pre-filter, so the directory is never opened."""
    _rt()
    spy = _OpenSpy(monkeypatch)
    exc = _err(str(tmp_path))
    assert exc.reason == "not_regular_file"
    assert str(tmp_path) not in spy.paths


@pytest.mark.timeout(10)
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo unavailable")
def test_fifo_is_not_regular_file_and_never_blocks(tmp_path, monkeypatch):
    """A6: a FIFO with no writer is rejected without blocking and never opened."""
    fifo = tmp_path / "role.fifo"
    os.mkfifo(fifo)
    _rt()
    spy = _OpenSpy(monkeypatch)
    exc = _err(str(fifo))
    assert exc.reason == "not_regular_file"
    assert str(fifo) not in spy.paths


@pytest.mark.skipif(not Path("/dev/null").exists(), reason="/dev/null absent")
def test_dev_null_is_not_regular_file():
    """A7."""
    exc = _err("/dev/null")
    assert exc.reason == "not_regular_file"


def test_symlink_to_file_followed_dir_dangling_loop_fail_closed(tmp_path):
    """A8: symlink to file is followed; to dir -> not_regular_file; dangling -> missing; loop -> unreadable."""
    target = tmp_path / "real.md"
    target.write_text("SYMLINKED ROLE\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)
    rt = _load(str(link))
    assert rt.content == "SYMLINKED ROLE\n\n"
    assert rt.source_id == str(link)

    d = tmp_path / "dir"
    d.mkdir()
    dlink = tmp_path / "dlink"
    dlink.symlink_to(d)
    assert _err(str(dlink)).reason == "not_regular_file"

    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "gone")
    assert _err(str(dangling)).reason == "missing"

    a = tmp_path / "loop_a"
    b = tmp_path / "loop_b"
    a.symlink_to(b)
    b.symlink_to(a)
    exc = _err(str(a))
    assert exc.reason == "unreadable"
    assert "ELOOP" in str(exc)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses chmod 000")
def test_unreadable_file_is_unreadable(tmp_path):
    """A9: EACCES -> unreadable; errno name only."""
    f = tmp_path / "role.md"
    f.write_text("secret\n", encoding="utf-8")
    f.chmod(0)
    try:
        exc = _err(str(f))
    finally:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert exc.reason == "unreadable"
    msg = str(exc)
    assert "EACCES" in msg
    assert "Permission denied" not in msg


def test_cap_boundary_and_bytes_not_chars(tmp_path, monkeypatch):
    """A10: the cap is read at call time, counts raw bytes, never truncates."""
    rt = _rt()
    monkeypatch.setattr(rt, "ROLE_TEMPLATE_MAX_BYTES", 16)
    f = tmp_path / "role.md"
    f.write_bytes(b"a" * 16)
    assert rt.load_role_template({KEY: str(f)}).content == "a" * 16 + "\n\n"

    f.write_bytes(b"a" * 17)
    exc = _err(str(f))
    assert exc.reason == "over_cap"
    assert str(exc).endswith("(17 bytes > 16)"), str(exc)

    # Check order (spec 5.1 steps 8-10): the cap is checked before UTF-8 and NUL.
    f.write_bytes(b"\xff\x00" + b"a" * 20)
    assert _err(str(f)).reason == "over_cap"
    f.write_bytes(b"\xff\x00")
    assert _err(str(f)).reason == "not_utf8"

    monkeypatch.setattr(rt, "ROLE_TEMPLATE_MAX_BYTES", 11)
    f.write_text("é" * 6, encoding="utf-8")  # 6 chars, 12 bytes
    exc = _err(str(f))
    assert exc.reason == "over_cap"
    assert "12 bytes > 11" in str(exc), str(exc)


def test_huge_sparse_file_bounded_memory(tmp_path):
    """A10: a 1 GiB sparse file at the default cap is rejected with bounded memory."""
    rt = _rt()
    f = tmp_path / "huge.md"
    size = 1 << 30
    with open(f, "wb") as fh:
        fh.truncate(size)
    st = os.stat(f)
    if getattr(st, "st_blocks", None) is None or st.st_blocks * 512 >= size:
        pytest.skip("filesystem does not create sparse files")
    tracemalloc.start()
    try:
        with pytest.raises(rt.RoleTemplateError) as ei:
            rt.load_role_template({KEY: str(f)})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert ei.value.reason == "over_cap"
    assert str(ei.value).endswith("(1073741824 bytes > 65536)"), str(ei.value)
    assert peak < 4 * 1024 * 1024, peak


def test_cap_constant_is_65536():
    """A10."""
    assert _rt().ROLE_TEMPLATE_MAX_BYTES == 65_536


def test_reason_tokens_and_constants():
    """Spec 3 / 5.2: exactly the nine reason tokens, in order, and the public constants."""
    rt = _rt()
    assert rt.ROLE_TEMPLATE_REASONS == REASONS
    assert rt.ROLE_TEMPLATE_KEY == KEY
    assert rt.ERROR_CODE == CODE


def test_valid_read_uses_one_fd_and_closes_it(tmp_path, monkeypatch):
    """Spec 5.1 steps 5-7: one os.open with the required flags, read on that fd, closed."""
    rt = _rt()
    role = tmp_path / "role.md"
    role.write_text("ONE FD ROLE\n", encoding="utf-8")
    spy = _FdSpy(monkeypatch)
    assert rt.load_role_template({KEY: str(role)}).content == "ONE FD ROLE\n\n"
    opens = spy.opens_of(role)
    assert len(opens) == 1, spy.opened
    flags, fd = opens[0]
    assert flags & _REQUIRED_OPEN_FLAGS == _REQUIRED_OPEN_FLAGS, oct(flags)
    assert flags & os.O_ACCMODE == os.O_RDONLY, oct(flags)
    assert fd in spy.closed


def test_fstat_is_authoritative_after_open(tmp_path, monkeypatch):
    """Security MUST (spec 5.1 step 6): the fd is re-checked with fstat; a non-regular fd is
    rejected even though the stat pre-filter saw a regular file; the fd is closed."""
    rt = _rt()
    role = tmp_path / "role.md"
    role.write_text("RACED ROLE\n", encoding="utf-8")
    spy = _FdSpy(monkeypatch)
    real_fstat = os.fstat

    def _fstat(fd):
        if fd in spy.fds_of(role):
            return os.stat_result((stat.S_IFIFO | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        return real_fstat(fd)

    monkeypatch.setattr(os, "fstat", _fstat)
    with pytest.raises(rt.RoleTemplateError) as ei:
        rt.load_role_template({KEY: str(role)})
    assert ei.value.reason == "not_regular_file"
    opens = spy.opens_of(role)
    assert len(opens) == 1, spy.opened
    flags, fd = opens[0]
    assert flags & _REQUIRED_OPEN_FLAGS == _REQUIRED_OPEN_FLAGS, oct(flags)
    assert flags & os.O_ACCMODE == os.O_RDONLY, oct(flags)
    assert fd in spy.closed


def test_read_error_is_unreadable_and_fd_closed(tmp_path, monkeypatch):
    """Spec 5.1 step 7: an OSError during the read loop -> unreadable, errno name only; fd closed."""
    rt = _rt()
    role = tmp_path / "role.md"
    role.write_text("READ FAILS\n", encoding="utf-8")
    spy = _FdSpy(monkeypatch)
    real_read = os.read

    def _read(fd, n):
        if fd in spy.fds_of(role):
            raise OSError(errno.EIO, "Input/output error")
        return real_read(fd, n)

    monkeypatch.setattr(os, "read", _read)
    with pytest.raises(rt.RoleTemplateError) as ei:
        rt.load_role_template({KEY: str(role)})
    assert ei.value.reason == "unreadable"
    msg = str(ei.value)
    assert "EIO" in msg
    assert "Input/output error" not in msg
    fds = spy.fds_of(role)
    assert len(fds) == 1, spy.opened
    assert fds <= set(spy.closed)


def test_non_utf8_is_not_utf8_without_byte_leak(tmp_path):
    """A11: strict UTF-8; no offending byte or offset in the message."""
    f = tmp_path / "role.md"
    f.write_bytes(b"ok\xff\xfe")
    exc = _err(str(f))
    assert exc.reason == "not_utf8"
    msg = str(exc)
    for leak in ("0xff", "\\xff", "position", "can't decode"):
        assert leak not in msg, leak

    f.write_bytes("hello".encode("utf-16"))  # BOM-prefixed UTF-16
    assert _err(str(f)).reason == "not_utf8"


def test_bom_is_kept_byte_identical_to_old_reader(tmp_path):
    """A12: the UTF-8 BOM is kept (no utf-8-sig), exactly as read_text does."""
    f = tmp_path / "role.md"
    f.write_bytes("\ufeffBOM ROLE\n".encode("utf-8"))
    rt = _load(str(f))
    assert rt.content.startswith("\ufeff")
    assert rt.content == _oracle(f)


# PRE-PASSING GUARD
def test_newlines_normalised_to_lf(tmp_path):
    """A13: CRLF, lone CR and mixed newlines become LF (through the wrapper)."""
    f = tmp_path / "role.md"
    for raw, want in (
        (b"a\r\nb\r\n", "a\nb\n\n"),
        (b"a\rb\r", "a\nb\n\n"),
        (b"a\r\nb\rc\n", "a\nb\nc\n\n"),
    ):
        f.write_bytes(raw)
        assert common._maybe_role_template(_org_ctx({KEY: str(f)})) == want, raw


# PRE-PASSING GUARD
def test_byte_identity_with_old_algorithm(tmp_path):
    """A14: for every file that works today, content == read_text().rstrip() + "\\n\\n"."""
    f = tmp_path / "role.md"
    for text in (
        "plain ascii\n",
        "multibyte é中文 \U0001f600\n",
        "trailing whitespace   \n\t \n",
        "line1\n\n\ninterior blank\n",
        "  indented\n    more\n",
        "crlf\r\nbody\r\n",
    ):
        f.write_bytes(text.encode("utf-8"))
        assert common._maybe_role_template(_org_ctx({KEY: str(f)})) == _oracle(f), text


def test_nul_in_content_and_in_path(tmp_path):
    """A15."""
    f = tmp_path / "role.md"
    f.write_bytes(b"ab\x00cd")
    assert _err(str(f)).reason == "contains_nul"
    exc = _err("a\x00b")
    assert exc.reason == "invalid_path"
    assert "embedded NUL" in str(exc)


def test_empty_and_whitespace_only_file_is_empty(tmp_path):
    """A16."""
    f = tmp_path / "role.md"
    f.write_bytes(b"")
    assert _err(str(f)).reason == "empty"
    f.write_bytes(b"  \n\t\r\n  ")
    assert _err(str(f)).reason == "empty"


def test_expanduser_only(tmp_path, monkeypatch):
    """A17: expanduser only; no expandvars; CWD-relative; unknown ~user -> invalid_path."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "role.md").write_text("HOME ROLE\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert _load("~/role.md").content == "HOME ROLE\n\n"
    assert _err("$HOME/role.md").reason == "missing"

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "role.md").write_text("CWD ROLE\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    assert _load("role.md").content == "CWD ROLE\n\n"

    exc = _err("~nosuchuser_x/y")
    assert exc.reason == "invalid_path"
    assert "home directory not resolvable" in str(exc)


def test_role_template_error_not_oserror_valueerror_unicodeerror():
    """A18."""
    rt = _rt()
    coded = _rt_contracts().CodedStepError
    assert issubclass(rt.RoleTemplateError, coded)
    for base in (OSError, ValueError, UnicodeError):
        assert not issubclass(rt.RoleTemplateError, base), base


def test_error_result_shape_and_message_format(tmp_path):
    """A19."""
    configured = str(tmp_path / "nope.md")
    exc = _err(configured)
    res = exc.to_result("build_spec_prompt")
    assert res.status == "error"
    assert res.data is None
    assert res.error_code == CODE
    assert res.recoverable is False
    assert res.suggestion == SUGGESTION
    assert res.step_name == "build_spec_prompt"
    assert res.error == str(exc)
    assert re.match(r"^role_template_path: missing: ", res.error), res.error
    assert f"role_template_path: missing: {configured} (ENOENT)" == res.error


def test_message_truncates_path_and_suppresses_context(tmp_path, monkeypatch):
    """A20: path echoed as 256 chars + "..."; not_utf8 raised from None."""
    monkeypatch.chdir(tmp_path)
    long_value = "x" * 300
    exc = _err(long_value)
    msg = str(exc)
    # A 300-byte component exceeds NAME_MAX (255) on APFS, ext4 and tmpfs.
    assert exc.reason == "unreadable"
    assert "ENAMETOOLONG" in msg
    assert long_value[:256] + "..." in msg
    assert long_value[:257] not in msg
    assert exc.path == long_value

    f = tmp_path / "bad.md"
    f.write_bytes(b"\xff")
    exc = _err(str(f))
    assert exc.reason == "not_utf8"
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_load_returns_source_id_expanduser_not_resolve(tmp_path, monkeypatch):
    """A21: a symlinked home keeps the unresolved spelling in source_id ([bd10:10])."""
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / "role.md").write_text("ROLE\n", encoding="utf-8")
    link_home = tmp_path / "link_home"
    link_home.symlink_to(real_home)
    monkeypatch.setenv("HOME", str(link_home))
    rt = _load("~/role.md")
    assert rt.source_id == str(link_home / "role.md")
    assert rt.source_id == str(Path("~/role.md").expanduser())
    assert rt.source_id != str((link_home / "role.md").resolve())


def test_message_is_single_line_ascii_for_hostile_paths(tmp_path, monkeypatch):
    """A22: the echoed path is unicode_escape'd, then truncated: one printable ASCII line."""
    monkeypatch.chdir(tmp_path)
    cases = (
        ("a\nb", "\\n"),
        ("a\rb", "\\r"),
        ("a\x1b[31mb", "\\x1b"),
        ("a\x00b", "\\x00"),
        ("café", "\\xe9"),
        ("\n" * 300, "\\n"),
    )
    for value, spelling in cases:
        exc = _err(value)
        msg = str(exc)
        assert msg.isprintable() and msg.isascii(), repr(msg)
        assert "\n" not in msg and "\r" not in msg
        assert spelling in msg, (value, msg)
        escaped = value.encode("unicode_escape").decode("ascii")
        echoed = escaped[:256] + "..." if len(escaped) > 256 else escaped
        assert echoed in msg, (value, msg)
        if len(escaped) > 256:
            assert escaped[:257] not in msg
        assert exc.path == value
        assert exc.to_result("s").error == msg
    assert _err("a\x00b").reason == "invalid_path"


# Spec 5.7: core literals the new module must not carry (split so this file carries none).
_FORBIDDEN_CORE_LITERALS = (
    "/" + "Users/", "." + "claude/", "~/." + "claude", "SHA" + "RED/", "MEM" + "ORY.md", "HAL_",
)
_ALLOWED_ENGINE_IMPORTS = {"contracts", "bytedigger_engine.contracts"}


def test_role_template_module_contract():
    """Spec 3 / 5.7: `from __future__ import annotations` first; stdlib + contracts only;
    no forbidden core literals anywhere in the source (docstrings and comments included)."""
    source = Path(_rt().__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    first = body[0]
    assert isinstance(first, ast.ImportFrom) and first.module == "__future__", ast.dump(first)
    assert [a.name for a in first.names] == ["annotations"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in sys.stdlib_module_names, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                assert node.module == "contracts", (node.level, node.module)
            else:
                assert (
                    node.module.split(".")[0] in sys.stdlib_module_names
                    or node.module in _ALLOWED_ENGINE_IMPORTS
                ), node.module
    for literal in _FORBIDDEN_CORE_LITERALS:
        assert literal not in source, literal


# ---------------------------------------------------------------------------
# B. Structural (AST over PKG/**/*.py)
# ---------------------------------------------------------------------------


def _pkg_files() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_single_def_maybe_role_template_in_common():
    """B1: exactly one `def _maybe_role_template`, in phase_workflows_common.py."""
    found = []
    for path in _pkg_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_maybe_role_template":
                found.append(f"{path.relative_to(PKG)}:{node.lineno}")
    assert len(found) == 1, f"expected one def in common, found {found}"
    assert found[0].startswith("workflows/phase_workflows_common.py:"), found


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def test_key_literal_only_in_role_template_module():
    """B2: a Constant exactly equal to the key exists only in role_template.py."""
    holders = set()
    for path in _pkg_files():
        tree = _parse(path)
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == KEY and id(node) not in docs:
                holders.add(str(path.relative_to(PKG)))
    assert holders == {"role_template.py"}, sorted(holders)


# C table: row id -> (module, function, StepContract.name). Shared by B3 and C.
C_ROWS = (
    ("p45-spec", "phase_45_spec", "_build_spec_prompt", "build_spec_prompt"),
    ("p45-review", "phase_45_spec", "_build_review_prompt", "build_review_prompt"),
    ("p5-integrity", "phase_5_integrity", "_build_integrity_prompt", "build_integrity_prompt"),
    ("p6-fix-integrity", "phase_6_fix_integrity", "_build_fix_integrity_prompt", "build_fix_integrity_prompt"),
    ("p5-red", "phase_5_implement", "_build_red_prompt", "build_red_prompt"),
    ("p5-validation", "phase_5_implement", "_build_validation_prompt", "build_validation_prompt"),
    ("p5-green", "phase_5_implement", "_build_green_prompt", "build_green_prompt"),
    ("p6-review", "phase_6_review", "_build_review_prompt", "build_review_prompt"),
    ("p6-fix", "phase_6_review", "_build_fix_prompt", "build_fix_prompt"),
    ("p6-satisfaction", "phase_6_review", "_build_satisfaction_prompt", "build_satisfaction_prompt"),
)
C_ROW_IDS = [r[0] for r in C_ROWS]

B3_TARGETS = {
    ("phase_workflows_common", "_maybe_role_template"),
    ("role_template", "load_role_template"),
}


def _import_map(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Local name -> (source module short name, source name) for every from-import."""
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            short = node.module.rsplit(".", 1)[-1]
            for alias in node.names:
                out[alias.asname or alias.name] = (short, alias.name)
    return out


def _module_defs(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        n.name: n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _resolver(modname: str, tree: ast.Module):
    defs = _module_defs(tree)
    imports = _import_map(tree)

    def resolve(name: str):
        if name in defs:
            return (modname, name)
        if name in imports:
            return imports[name]
        return None

    return resolve


def _call_name(func: ast.AST):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _workflow_call_graph() -> dict[tuple[str, str], set[tuple[str, str]]]:
    """(module, function) -> callees, over PKG/workflows/*.py; inner defs folded in."""
    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for path in sorted((PKG / "workflows").glob("*.py")):
        modname = path.stem
        tree = _parse(path)
        resolve = _resolver(modname, tree)
        for name, fn in _module_defs(tree).items():
            edges: set[tuple[str, str]] = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    callee = _call_name(node.func)
                    target = resolve(callee) if callee else None
                    if target is not None:
                        edges.add(target)
            graph[(modname, name)] = edges
    return graph


def _reaches(graph, start, targets) -> set[tuple[str, str]]:
    seen, stack, hit = set(), [start], set()
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if node in targets:
            hit.add(node)
            continue
        stack.extend(graph.get(node, ()))
    return hit


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _extract_steps(path: Path, within: str | None = None):
    """Step functions registered in `path`: list of ((module, function), form, line).

    Forms: "name" (Name / Attribute), "lambda" (callees of the lambda body),
    "factory" (contracts.step(name, fn, ...)). Any other execute form raises
    AssertionError naming file and line.
    """
    modname = path.stem
    tree = _parse(path)
    resolve = _resolver(modname, tree)
    root = tree
    if within is not None:
        root = _module_defs(tree)[within]
    local_defs = set(_module_defs(tree))
    rel = path.relative_to(PKG) if PKG in path.parents else path.name
    out = []
    for node in ast.walk(root):
        if not isinstance(node, ast.Call):
            continue
        cname = _call_name(node.func)
        where = f"{rel}:{node.lineno}"
        if cname == "StepContract":
            expr = _kwarg(node, "execute")
            if expr is None and len(node.args) >= 2:
                expr = node.args[1]
            assert expr is not None, f"B3: StepContract without a resolvable execute at {where}"
            form = "name"
        elif cname == "step" and isinstance(node.func, ast.Name):
            expr = _kwarg(node, "fn")
            if expr is None and len(node.args) >= 2:
                expr = node.args[1]
            assert expr is not None, f"B3: step() factory without a resolvable fn at {where}"
            form = "factory"
        else:
            continue
        if isinstance(expr, (ast.Name, ast.Attribute)):
            name = _call_name(expr)
            if modname == "contracts" and name in local_defs | {"_execute"}:
                continue  # the factory's own closure is not a step function
            target = resolve(name)
            if target is None and modname == "contracts":
                continue
            assert target is not None, f"B3: unresolvable execute name {name!r} at {where}"
            out.append((target, form, node.lineno))
        elif isinstance(expr, ast.Lambda) and form == "name":
            callees = [
                resolve(_call_name(c.func)) for c in ast.walk(expr.body)
                if isinstance(c, ast.Call) and _call_name(c.func)
            ]
            callees = [c for c in callees if c is not None]
            assert callees, f"B3: lambda execute with no resolvable callee at {where}"
            for target in callees:
                out.append((target, "lambda", node.lineno))
        else:
            raise AssertionError(
                f"B3: unsupported execute form {type(expr).__name__} at {where}"
            )
    return out


def test_role_template_consumers_match_step_table():
    """B3: step functions with a call path to the reader == the 16 C rows."""
    graph = _workflow_call_graph()
    steps: set[tuple[str, str]] = set()
    for path in _pkg_files():
        for target, _form, _line in _extract_steps(path):
            steps.add(target)
    observed = {s for s in steps if _reaches(graph, s, B3_TARGETS)}
    expected = {(m, f) for (_row, m, f, _name) in C_ROWS}

    assert ("phase_45_spec", "_build_spec_prompt") in observed
    assert ("phase_45_spec", "_build_review_prompt") in observed

    p45_steps = _extract_steps(PKG / "workflows" / "phase_45_spec.py", within="phase_45_spec_workflow")
    # The workflow registers 21 lambda-form steps; the count is read from the definition itself.
    assert len({t for t, _f, _l in p45_steps}) == len(p45.phase_45_spec_workflow().steps)
    assert sum(1 for _t, f, _l in p45_steps if f == "lambda") >= 2

    assert observed == expected, (
        f"missing: {sorted(expected - observed)}; unexpected: {sorted(observed - expected)}"
    )


_UNSUPPORTED_EXECUTE_FORMS = {
    "partial": "functools.partial(f)",
    "call": "make()",
    "ifexp": "f if c else g",
    "subscript": "fns[0]",
}


# PRE-PASSING GUARD
@pytest.mark.parametrize("form", sorted(_UNSUPPORTED_EXECUTE_FORMS))
def test_b3_extractor_fails_loudly_on_unsupported_execute_form(form, tmp_path):
    """B3 extractor: a new execute form cannot silently drop a step from the observed set."""
    mod = tmp_path / "synthetic_steps.py"
    mod.write_text(
        f'StepContract(name="x", execute={_UNSUPPORTED_EXECUTE_FORMS[form]})\n', encoding="utf-8",
    )
    with pytest.raises(AssertionError, match=r"unsupported execute form \w+ at .+:\d+"):
        _extract_steps(mod)


# PRE-PASSING GUARD
@pytest.mark.parametrize("source", [
    "StepContract(**kw)\n",
    'StepContract(name="x")\n',
    "step(**kw)\n",
], ids=["contract_kwargs", "contract_no_execute", "factory_kwargs"])
def test_b3_extractor_fails_loudly_without_resolvable_execute(source, tmp_path):
    """B3 extractor: a registration whose execute cannot be read is an error, not a skip."""
    mod = tmp_path / "synthetic_steps.py"
    mod.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError, match=r"without a resolvable \w+ at synthetic_steps\.py:\d+"):
        _extract_steps(mod)


def test_engine_has_no_bare_execute_outside_execute_step():
    """B4: every step execution in engine.py goes through `_execute_step`."""
    tree = _parse(PKG / "engine.py")
    defs = _module_defs(tree)
    assert "_execute_step" in defs, "engine._execute_step is not defined"
    inside = {id(n) for n in ast.walk(defs["_execute_step"])}
    bare = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "execute" and id(n) not in inside
    ]
    assert bare == [], f"bare .execute( calls at engine.py lines {bare}"
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_name(n.func) == "_execute_step"
    ]
    assert len(calls) >= 5, len(calls)


# ---------------------------------------------------------------------------
# C. Per call-site (11 build rows through the engine; bd#89 P3c dropped the phase-7 row)
# ---------------------------------------------------------------------------

_MODS = {
    "phase_45_spec": p45,
    "phase_5_integrity": p5i, "phase_6_fix_integrity": p6fi,
    "phase_5_implement": p5, "phase_6_review": p6,
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: Path, rel: str, body: str, msg: str) -> str:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _ok_prev(step_name: str, data: dict) -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name=step_name)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _stub_anchor(monkeypatch, mod, row: str) -> str:
    anchor = f"BD119-ANCHOR-{row}"
    monkeypatch.setattr(mod, "_read_first_block", lambda s: f"{anchor}\n")
    return anchor


def _real_anchor(mod, scratchpad: Path) -> str:
    block = mod._read_first_block(scratchpad)
    anchor = block.splitlines()[0] if block else ""
    assert anchor, "fixture: real _read_first_block returned an empty first line"
    return anchor


def _row_setup(row: str, tmp_path: Path, monkeypatch, role_value: str, **org_extra):
    """Return (fn, step_name, ctx, prev, anchor) for one C row.

    Never patches `_maybe_role_template` or `load_role_template`.
    """
    _r, modname, fname, step_name = next(r for r in C_ROWS if r[0] == row)
    mod = _MODS[modname]
    fn = getattr(mod, fname)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    org = {KEY: role_value, "complexity": "COMPLEX", **org_extra}
    prev = None
    anchor = None

    if row == "p45-spec":
        prev = {"cycle": 1}
        anchor = _real_anchor(mod, scratchpad)
    elif row == "p45-review":
        spec = _write(scratchpad / "specs" / "build-spec.md", "## US1\nAdd foo\n")
        prev = _ok_prev("verify_spec", {"spec_path": str(spec), "cycle": 1})
        anchor = _real_anchor(mod, scratchpad)
    elif row == "p5-integrity":
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "src/foo.py", "def foo(): return 1\n", "init")
        _commit(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 1\n", "red")
        _commit(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 2\n", "green")
        org["git_cwd"] = str(repo)
        anchor = _real_anchor(mod, scratchpad)
    elif row == "p6-fix-integrity":
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "src/foo.py", "def foo(): return 1\n", "init")
        pre = _commit(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 1\n", "build: red cycle")
        fix = _commit(repo, "tests/test_foo.py", "def test_foo():\n    assert foo() == 2\n", "fix")
        org.update(git_cwd=str(repo), pre_fix_sha=pre, fix_commit_sha=fix)
        anchor = _real_anchor(mod, scratchpad)
    elif row in ("p5-red", "p5-validation"):
        prev = {"cycle": 1}
        anchor = _stub_anchor(monkeypatch, p5, row)
        monkeypatch.setattr(p5, "_resolve_worktree_root", lambda ctx, sp: tmp_path)
        monkeypatch.setattr(p5, "_worktree_edit_boundary_block", lambda _root: "")
        monkeypatch.setattr(p5, "_get_producer_anti_fab_prompt", lambda: "")
        if row == "p5-validation":
            monkeypatch.setattr(p5, "_parse_spec_files_allowlist", lambda _p: [])
            spec = _write(scratchpad / "specs" / "build-spec.md", "## US1\nAdd foo\n")
            red_log = _write(scratchpad / "red.log", "RED COMPLETE\n")
            prev = _ok_prev("check_red_executable", {
                "red_log_path": str(red_log), "spec_path": str(spec), "cycle": 1,
            })
    elif row == "p5-green":
        prev = {
            "cycle": 1,
            "spec_path": str(_write(scratchpad / "specs" / "build-spec.md", "## US1\nAdd foo\n")),
            "red_log_path": str(_write(scratchpad / "red.log", "RED\n")),
            "validation_doc_path": str(_write(scratchpad / "validation.md", "PASS\n")),
            "green_lint_findings": [],
        }
        anchor = _stub_anchor(monkeypatch, p5, row)
        monkeypatch.setattr(p5, "_resolve_scratchpad", lambda _c: scratchpad)
        monkeypatch.setattr(p5, "_get_producer_anti_fab_prompt", lambda: "")
        monkeypatch.setattr(p5, "_resolve_worktree_root", lambda ctx, sp: tmp_path)
        monkeypatch.setattr(p5, "_worktree_edit_boundary_block", lambda _root: "")
        monkeypatch.setattr(p5, "_red_baseline_precheck", lambda *a, **kw: None)
    elif row == "p6-review":
        monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
        monkeypatch.setattr(p6, "_inline_inscope_test_files", lambda ctx, s: ("", 0, 0))
        anchor = _stub_anchor(monkeypatch, p6, row)
    elif row == "p6-fix":
        monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
        monkeypatch.setattr(p6, "_inline_inscope_test_files", lambda ctx, s: ("", 0, 0))
        anchor = _stub_anchor(monkeypatch, p6, row)
        review = _write(scratchpad / p6.REVIEW_DOC_RELPATH, "# Review\n\nVERDICT: FAIL\n")
        spec = _write(scratchpad / p6.SPEC_DOC_RELPATH, "## US1\nAdd foo\n")
        prev = _ok_prev("write_review_artifact", {
            "spec_path": str(spec), "review_doc_path": str(review), "verdict": "FAIL",
        })
    elif row == "p6-satisfaction":
        org.pop("complexity")
        monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
        anchor = _stub_anchor(monkeypatch, p6, row)
        spec = _write(scratchpad / p6.SPEC_DOC_RELPATH, "## US1\nAdd foo to bar\n")
        review = _write(scratchpad / p6.REVIEW_DOC_RELPATH, "# Review\n\nVERDICT: PASS\n")
        fix = _write(scratchpad / p6.FIX_DOC_RELPATH, "FIX COMPLETE — 0 findings.\n")
        prev = _ok_prev("write_fix_artifact", {
            "spec_path": str(spec), "review_doc_path": str(review), "fix_doc_path": str(fix),
            "doc_path": str(scratchpad / p6.SATISFACTION_DOC_RELPATH),
        })
    else:  # pragma: no cover -- table and setup must stay in sync
        raise AssertionError(f"no fixture for row {row}")

    ctx = make_ctx(scratchpad, **org)
    assert ctx.org_config.get("artifact_type") is None
    return fn, step_name, ctx, prev, anchor


def _role_file(tmp_path: Path, row: str) -> tuple[Path, str]:
    marker = f"BD119-ROLE-{row}"
    role = tmp_path / "role.md"
    role.write_text(f"{marker}\nrole body line\n  \n", encoding="utf-8")
    return role, marker


# PRE-PASSING GUARD
@pytest.mark.parametrize("row", C_ROW_IDS)
def test_c_plus_valid_template_reaches_prompt(row, tmp_path, monkeypatch):
    """C+: a valid template is at the head of the prompt, before the family anchor."""
    role, marker = _role_file(tmp_path, row)
    fn, step_name, ctx, prev, anchor = _row_setup(row, tmp_path, monkeypatch, str(role))
    assert ctx.org_config.get("artifact_type") is None
    res = _run_step(engine_module, StepContract(name=step_name, execute=fn), ctx, prev)
    assert res.status == "ok", (res.error_code, res.error)
    prompt = res.data["prompt"]
    assert anchor and anchor in prompt, anchor
    assert prompt.startswith(marker), prompt[:200]
    assert prompt.startswith(f"{marker}\nrole body line\n\n"), prompt[:200]
    assert prompt.index(marker) < prompt.index(anchor)


# PRE-PASSING GUARD
def test_c_plus_spec_prompt_with_reroute_nudge_precedes_template(tmp_path, monkeypatch):
    """C+ extra case: with phase_reroute, only the nudge block (+ blank line) precedes the template."""
    reroute = {"reason": "AC3 unsatisfiable", "attempt": 1, "from_phase": "phase_5_implement"}
    role, marker = _role_file(tmp_path, "p45-spec")
    fn, step_name, ctx, prev, anchor = _row_setup(
        "p45-spec", tmp_path, monkeypatch, str(role), phase_reroute=reroute,
    )
    res = _run_step(engine_module, StepContract(name=step_name, execute=fn), ctx, prev)
    assert res.status == "ok", (res.error_code, res.error)
    prompt = res.data["prompt"]
    nudge_block = p45._spec_defect_nudge_block(reroute)
    nudge = nudge_block.splitlines()[0]
    assert prompt.index(nudge) < prompt.index(marker) < prompt.index(anchor)
    assert prompt[: prompt.index(marker)] == nudge_block + "\n\n"


@pytest.mark.parametrize("row", C_ROW_IDS)
def test_c_minus_missing_template_fails_closed_via_engine(row, tmp_path, monkeypatch):
    """C-: a configured-but-missing template is a coded, non-recoverable error; no raise."""
    fn, step_name, ctx, prev, _anchor = _row_setup(
        row, tmp_path, monkeypatch, str(tmp_path / "nope.md"),
    )
    res = _run_step(engine_module, StepContract(name=step_name, execute=fn), ctx, prev)
    assert res.status == "error", f"{row}: missing template was skipped (status={res.status!r})"
    assert res.error_code == CODE
    assert res.recoverable is False
    assert "missing" in (res.error or "")
    assert res.step_name == step_name


def _p2_ctx(scratchpad: Path, role_value) -> WorkflowContext:
    """Context for the engine-level role-template cases (re-pointed to phase_45_spec by bd#89 P2a)."""
    return make_ctx(
        scratchpad, model="sonnet", complexity="COMPLEX",
        **({KEY: role_value} if role_value is not None else {}),
    )


def _spec_build_workflow() -> WorkflowDefinition:
    """One real step (`p45._build_spec_prompt`) run by the real engine."""
    return WorkflowDefinition(
        name="p45_build_only",
        steps=[StepContract(name="build_spec_prompt", execute=p45._build_spec_prompt)],
    )


def test_c_p2_invoke_does_not_reopen_template(tmp_path):
    """Build, delete the file, invoke: ok, and the declared block equals the placed block."""
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("P2 ROLE HEAD\nP2 ROLE TAIL\n  \n", encoding="utf-8")
    placed = "P2 ROLE HEAD\nP2 ROLE TAIL\n\n"
    ctx = _p2_ctx(scratchpad, str(role))
    built = p45._build_spec_prompt(ctx, {"cycle": 1})
    assert built.status == "ok"
    assert built.data["prompt"].startswith(placed)
    role.unlink()

    backend = _CountingBackend()
    _register(backend)
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-BD119", step_name="invoke_spec_llm", phase="phase_45_spec",
    )
    res = p45._invoke_spec_llm(ctx, built)
    assert res.status == "ok", (res.error_code, res.error)
    attests = log.payloads(ATTEST_EVENT)
    assert len(attests) == 1
    assert attests[0]["injections"] == [{"source_id": str(role), "sha256": _sha(placed)}]


def test_c_engine_p2_missing_no_backend_call(tmp_path):
    """C-engine: WorkflowEngine.execute halts at build_spec_prompt, no dispatch, one incident."""
    scratchpad = tmp_path / "scratch"
    backend = _CountingBackend()
    _register(backend)
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("p2", _spec_build_workflow())
    result, _ = eng.execute("p2", _p2_ctx(scratchpad, str(tmp_path / "nope.md")))

    assert result.status == "error"
    assert result.error_code == CODE
    assert result.step_name == "build_spec_prompt"
    assert backend.calls == []
    finished = [p for p in log.payloads("step_finished") if p["step_name"] == "build_spec_prompt"]
    assert finished and finished[0]["status"] == "error"
    records = _ledger(tmp_path / "incidents.jsonl")
    assert len(records) == 1, records
    assert records[0]["error_code"] == CODE


@pytest.mark.parametrize("row", ["p45-spec", "p5-green"])
def test_c_bad_bytes_error_result_not_raise(row, tmp_path, monkeypatch):
    """A non-UTF-8 template is an error result, not a UnicodeDecodeError."""
    role = tmp_path / "role.md"
    role.write_bytes(b"\xff")
    fn, step_name, ctx, prev, _anchor = _row_setup(row, tmp_path, monkeypatch, str(role))
    res = _run_step(engine_module, StepContract(name=step_name, execute=fn), ctx, prev)
    assert res.status == "error"
    assert res.error_code == CODE
    assert "not_utf8" in res.error


_STRUCTURED_FINDINGS = (
    "## Findings (structured)\n```json\n"
    '[{"id": "F1", "severity": "MAJOR", "path": "build-spec.md", "description": "fix AC3"}]\n'
    "```\n"
)


def _assert_template_free(res, missing: Path, spy: _OpenSpy) -> None:
    assert res.status == "ok", (res.error_code, res.error)
    assert res.data.get("delta_retry") is True, "fixture: the delta-retry early return was not taken"
    assert res.error_code is None
    assert CODE not in (res.error or "")
    assert str(missing) not in spy.paths


# PRE-PASSING GUARD
def test_template_free_p45_delta_retry_ignores_missing_template(tmp_path, monkeypatch):
    """Spec 5.5: the p45 cycle>=2 delta-retry early return carries no template and never reads it."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    missing = tmp_path / "nope.md"
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    ctx = make_ctx(scratchpad, complexity="COMPLEX", **{KEY: str(missing)})
    spy = _OpenSpy(monkeypatch)
    res = _run_step(
        engine_module, StepContract(name="build_spec_prompt", execute=p45._build_spec_prompt),
        ctx, {"cycle": 2, "findings": _STRUCTURED_FINDINGS},
    )
    _assert_template_free(res, missing, spy)


# PRE-PASSING GUARD
def test_template_free_p5_red_delta_retry_ignores_missing_template(tmp_path, monkeypatch):
    """Spec 5.5: the p5 red delta-retry early return (gate on) carries no template and never reads it."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    missing = tmp_path / "nope.md"
    monkeypatch.setenv("HAL_IMPL_DELTA_RETRY", "1")
    monkeypatch.setattr(p5, "_resolve_worktree_root", lambda ctx, sp: tmp_path)
    monkeypatch.setattr(p5, "_worktree_edit_boundary_block", lambda _root: "")
    ctx = make_ctx(scratchpad, complexity="COMPLEX", **{KEY: str(missing)})
    spy = _OpenSpy(monkeypatch)
    res = _run_step(
        engine_module, StepContract(name="build_red_prompt", execute=p5._build_red_prompt),
        ctx, {"cycle": 2, "findings": "F1: fix AC3"},
    )
    _assert_template_free(res, missing, spy)


# ---------------------------------------------------------------------------
# D. No content leak
# ---------------------------------------------------------------------------


def _canary_value(reason: str, tmp_path: Path, monkeypatch):
    rt = _rt()
    if reason == "bad_value_type":
        return {"k": CANARY}
    f = tmp_path / "canary.md"
    if reason == "over_cap":
        body = (CANARY + "\n").encode("utf-8")
        f.write_bytes(body * (rt.ROLE_TEMPLATE_MAX_BYTES // len(body) + 2))
    elif reason == "not_utf8":
        f.write_bytes(CANARY.encode("utf-8") + b"\xff\xfe" + CANARY.encode("utf-8"))
    elif reason == "contains_nul":
        f.write_bytes(CANARY.encode("utf-8") + b"\x00" + CANARY.encode("utf-8"))
    return str(f)


@pytest.mark.parametrize("reason", ["over_cap", "not_utf8", "contains_nul", "bad_value_type"])
def test_d_canary_absent_from_exception_and_result(reason, tmp_path, monkeypatch):
    """D1."""
    value = _canary_value(reason, tmp_path, monkeypatch)
    exc = _err(value)
    assert exc.reason == reason
    for text in (str(exc), repr(exc), repr(exc.args)):
        assert CANARY not in text
    res = exc.to_result("build_spec_prompt")
    assert res.data is None
    assert CANARY not in (res.error or "")
    assert CANARY not in (res.suggestion or "")


@pytest.mark.parametrize("reason", ["over_cap", "not_utf8", "contains_nul"])
def test_d_canary_absent_from_event_log_and_incident_ledger(reason, tmp_path, monkeypatch):
    """D2: engine path; canary in no event payload and nowhere in the incident ledger."""
    value = _canary_value(reason, tmp_path, monkeypatch)
    _register(_CountingBackend())
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("p2", _spec_build_workflow())
    result, _ = eng.execute("p2", _p2_ctx(tmp_path / "scratch", value))

    assert result.error_code == CODE
    for event_type, payload, _rid in log.events:
        assert CANARY not in json.dumps(payload, default=str), event_type
    ledger_path = tmp_path / "incidents.jsonl"
    assert CANARY not in ledger_path.read_text(encoding="utf-8")
    records = _ledger(ledger_path)
    assert len(records) == 1
    assert records[0]["error_code"] == CODE


# ---------------------------------------------------------------------------
# F. Registry and docs
# ---------------------------------------------------------------------------


def test_error_code_registered_with_bd119_ref():
    """F1."""
    from bytedigger_engine import error_codes

    assert CODE in error_codes.ERROR_CODES
    assert "bd#119" in error_codes.ERROR_CODES[CODE]


def test_error_code_in_both_catalogues():
    """F2."""
    for cat in (ENGINE_PY / "ERROR_CODES.md", PKG / "ERROR_CODES.md"):
        text = cat.read_text(encoding="utf-8")
        assert CODE in text, cat
        assert "## E_ROLE" in text, cat


def _config_doc() -> str:
    return (REPO / "docs" / "configuration.md").read_text(encoding="utf-8")


def test_configuration_doc_documents_key():
    """F3."""
    doc = _config_doc()
    assert "## Role template (engine)" in doc
    for needle in (KEY, CODE):
        assert needle in doc, needle
    assert "65536" in doc or "64 KiB" in doc
    for reason in REASONS:
        assert reason in doc, reason
    # bd#89 P2b: the fast-path sentence and its two needles retired with the fast path.


# PRE-PASSING GUARD
def test_error_code_not_in_dbos_retry_list():
    """F4: the code is not in the dbos retry list (read from source, no dbos import)."""
    tree = _parse(PKG / "lib" / "dbos_setup.py")
    retry = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_EVICT_ON_RETRY_CODES" for t in node.targets
        ):
            retry = {c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant)}
    assert retry, "fixture: _EVICT_ON_RETRY_CODES not found"
    assert "E_CONFIG_INVALID" in retry
    assert CODE not in retry


def test_role_template_in_core_manifest():
    """F5."""
    manifest = json.loads((ENGINE_PY / "core_manifest.json").read_text(encoding="utf-8"))
    assert "role_template.py" in manifest["core_modules"]


def test_configuration_doc_trust_and_gate_warnings():
    """F6."""
    doc = _config_doc()
    for phrase in CONFIG_DOC_TRUST_PHRASES:
        assert phrase in doc, phrase


def test_security_doc_amended():
    """F7."""
    text = " ".join((REPO / "docs" / "security.md").read_text(encoding="utf-8").split())
    assert "and the operator's `role_template_path` file" in text
    assert "gate-integrity" in text
    assert "are assembled from repo content -- so" not in text


def _unreleased() -> str:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [Unreleased]")
    end = text.find("\n## [", start + 1)
    return text[start:end if end != -1 else len(text)]


def test_changelog_entries():
    """F8."""
    section = _unreleased()
    assert "bd#119" in section
    assert CODE in section
    assert "65536" in section or "64 KiB" in section
    assert any("delimited" in line and "bounded" in line for line in section.splitlines())
    changed = section[section.index("### Changed"):]
    changed = changed[: changed.find("\n### ", 1)] if changed.find("\n### ", 1) != -1 else changed
    assert "missing" in changed
    assert CODE in changed


def test_authorship_spec_amended():
    """F9."""
    text = (PKG / "conformance" / "AUTHORSHIP_SPEC.md").read_text(encoding="utf-8")
    assert "phase_workflows_common.py:410" not in text
    assert "phase_2_explore.py:185" not in text
    assert text.count("*bd#119 amendment:*") >= 2


# ---------------------------------------------------------------------------
# G. Engine conversion
# ---------------------------------------------------------------------------


def _coded(msg: str = "role_template_path: missing: x (ENOENT)"):
    return _rt_contracts().CodedStepError(CODE, msg, suggestion=SUGGESTION)


def test_coded_step_error_to_result_fields():
    """G1."""
    cls = _rt_contracts().CodedStepError
    exc = cls("E_X", "boom msg", suggestion="do y", recoverable=True)
    assert str(exc) == "boom msg"
    assert (exc.error_code, exc.message, exc.suggestion, exc.recoverable) == ("E_X", "boom msg", "do y", True)
    res = exc.to_result("my_step")
    assert isinstance(res, StepResult)
    assert (res.status, res.data, res.duration_ms, res.step_name) == ("error", None, 0, "my_step")
    assert (res.error, res.error_code, res.suggestion, res.recoverable) == ("boom msg", "E_X", "do y", True)
    default = cls("E_Y", "m")
    assert default.suggestion is None
    assert default.recoverable is False


def test_execute_step_converts_only_coded_step_error(tmp_path):
    """G2."""
    execute_step = engine_module._execute_step
    ctx = make_ctx(tmp_path)

    def _raise(exc):
        def _fn(_ctx, _prev):
            raise exc
        return _fn

    res = execute_step(StepContract(name="coded", execute=_raise(_coded())), ctx, None)
    assert (res.status, res.error_code, res.step_name) == ("error", CODE, "coded")
    for exc in (ValueError("v"), OSError(errno.EIO, "io"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")):
        with pytest.raises(type(exc)):
            execute_step(StepContract(name="other", execute=_raise(exc)), ctx, None)


def _sentinel_run(tmp_path, fn):
    step = StepContract(name="g3_step", execute=fn, resume_sentinel=True)
    eng = WorkflowEngine()
    eng.register("g3", WorkflowDefinition(name="g3", steps=[step]))
    ctx = make_ctx(tmp_path / "scratch")
    result, _ = eng.execute("g3", ctx, run_id="RUN-G3")
    cached = maybe_read_sentinel(ctx, step, 1, "RUN-G3", lambda *a: None, workflow_name="g3")
    return result, cached


def test_coded_error_not_retried_and_not_sentinel_persisted(tmp_path):
    """G3."""
    exc = _coded()
    calls = []

    def _fn(_ctx, _prev):
        calls.append(1)
        raise exc

    result, cached = _sentinel_run(tmp_path, _fn)
    assert result.status == "error"
    assert result.error_code == CODE
    assert len(calls) == 1
    assert cached is None


# PRE-PASSING GUARD
def test_sentinel_written_for_ok_result(tmp_path):
    """G3 positive twin: an ok result of the same flagged step IS sentinel-written."""
    def _fn(_ctx, _prev):
        return StepResult(status="ok", data={"v": 1}, duration_ms=0, step_name="g3_step")

    result, cached = _sentinel_run(tmp_path, _fn)
    assert result.status == "ok"
    assert cached is not None and cached.data == {"v": 1}


@pytest.mark.parametrize("consume", [False, True], ids=["default_loop", "run_consuming"])
@pytest.mark.parametrize("run_set", [True, False], ids=["run_ctx_set", "run_ctx_unset"])
def test_engine_converts_coded_error_in_loop_body(consume, run_set, tmp_path, monkeypatch):
    """G4: the 2x2 of consume_recoverable_retry x run context; each cell hits its own site."""
    exc = _coded()
    body_runs: list[object] = []
    consuming_calls: list[int] = []
    real_consuming = LoopRunner._run_consuming

    def _spy_consuming(self, ctx, prev):
        consuming_calls.append(1)
        return real_consuming(self, ctx, prev)

    monkeypatch.setattr(LoopRunner, "_run_consuming", _spy_consuming)

    def _body(_ctx, _prev):
        body_runs.append(telemetry_ctx.get_current_run())
        raise exc

    contract = LoopStepContract(
        name="g4_loop",
        body=[StepContract(name="g4_body", execute=_body)],
        max_iterations=2,
        consume_recoverable_retry=consume,
    )
    ctx = make_ctx(tmp_path / "scratch")
    if run_set:
        eng = WorkflowEngine()
        eng.register("g4", WorkflowDefinition(name="g4", steps=[
            StepContract(name="g4_composite", execute=lambda c, p: LoopRunner(contract).run(c, p)),
        ]))
        result, _ = eng.execute("g4", ctx)
    else:
        telemetry_ctx.clear_current_run()
        getter_reads: list[object] = []
        real_get = telemetry_ctx.get_current_run

        def _spy_get():
            value = real_get()
            getter_reads.append(value)
            return value

        monkeypatch.setattr(telemetry_ctx, "get_current_run", _spy_get)
        result = LoopRunner(contract).run(ctx, None)
        assert getter_reads and all(v is None for v in getter_reads), getter_reads

    assert result.status == "error"
    assert result.error_code == CODE
    assert result.metadata.get("terminated_by") == "error"
    assert len(consuming_calls) == (1 if consume else 0)
    assert len(body_runs) == 1
    assert (body_runs[0] is not None) is run_set


def test_phase6_abort_handler_runs_on_template_error(tmp_path):
    """G5: error_handler fires once on a coded error; real phase 6 writes the NOT_ASSESSED stub."""
    exc = _coded()
    handled: list[StepResult] = []

    def _boom(_ctx, _prev):
        raise exc

    eng = WorkflowEngine()
    eng.register("g5", WorkflowDefinition(
        name="g5",
        steps=[StepContract(name="g5_step", execute=_boom)],
        error_handler=lambda res, _ctx: handled.append(res),
    ))
    result, _ = eng.execute("g5", make_ctx(tmp_path / "g5"))
    assert result.error_code == CODE
    assert len(handled) == 1
    assert handled[0].error_code == CODE

    _register(_CountingBackend("review\n"))
    scratchpad = tmp_path / "scratch"
    eng6 = WorkflowEngine()
    eng6.register("p6", p6.phase_6_review_workflow())
    result6, _ = eng6.execute("p6", make_ctx(
        scratchpad, complexity="COMPLEX", **{KEY: str(tmp_path / "nope.md")},
    ))
    assert result6.status == "error"
    assert result6.error_code == CODE
    doc = scratchpad / p6.SATISFACTION_DOC_RELPATH
    assert doc.is_file()
    assert p6.NOT_ASSESSED_MARKER in doc.read_text(encoding="utf-8")


# G6 (the SIMPLE fast path's halt-without-stub test) retired by bd#89 P2b; the
# COMPLEX stub-on-abort test above (G5) is the twin.


# ---------------------------------------------------------------------------
# H. Phase 2 single read
# ---------------------------------------------------------------------------

# Re-pointed from phase_2_explore to phase_45_spec by bd#89 P2a (the single-read
# contract now lives on the surviving spec writer).
_P2_NOT_SKIPPED = {"cycle": 1}


def test_p2_data_role_template_shape(tmp_path):
    """H1: data["role_template"] is {source_id, content} when configured, None when not."""
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("H1 ROLE\n  \n", encoding="utf-8")
    res = p45._build_spec_prompt(_p2_ctx(scratchpad, str(role)), _P2_NOT_SKIPPED)
    assert res.status == "ok"
    assert "role_template" in res.data
    assert res.data["role_template"] == {
        "source_id": str(Path(str(role)).expanduser()),
        "content": "H1 ROLE\n\n",
    }
    plain = p45._build_spec_prompt(_p2_ctx(scratchpad, None), _P2_NOT_SKIPPED)
    assert "role_template" in plain.data
    assert plain.data["role_template"] is None


# H1b (the decision-doc skip path never reads the template) retired by bd#89 P2a:
# phase_2_explore and its skip path are deleted.


def test_p2_injections_from_prev_declared_equals_placed(tmp_path):
    """H2."""
    role = tmp_path / "role.md"
    role.write_text("H2 ROLE\nsecond\n", encoding="utf-8")
    built = p45._build_spec_prompt(_p2_ctx(tmp_path / "scratch", str(role)), _P2_NOT_SKIPPED)
    rt = built.data["role_template"]
    blocks = common._declared_injections(built.data)
    assert len(blocks) == 1
    assert blocks[0].source_id == rt["source_id"] == str(role)
    assert blocks[0].content == rt["content"]
    assert built.data["prompt"].startswith(rt["content"])


def test_p2_template_opened_once_across_build_and_invoke(tmp_path, monkeypatch):
    """AC12: across build + invoke the template path is opened exactly once (the single fd)."""
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("ONCE ROLE\n", encoding="utf-8")
    ctx = _p2_ctx(scratchpad, str(role))
    _register(_CountingBackend())
    spy = _OpenSpy(monkeypatch)
    built = p45._build_spec_prompt(ctx, _P2_NOT_SKIPPED)
    assert built.status == "ok"
    telemetry_ctx.set_current_run(
        event_log=_FakeEventLog(), run_id="RUN-BD119-ONCE",
        step_name="invoke_spec_llm", phase="phase_45_spec",
    )
    res = p45._invoke_spec_llm(ctx, built)
    assert res.status == "ok", (res.error_code, res.error)
    assert spy.paths.count(str(role)) == 1, spy.paths


def test_p2_file_swap_between_build_and_invoke_declares_placed_bytes(tmp_path):
    """H3: a file swapped between build and invoke does not change the declared block."""
    role = tmp_path / "role.md"
    role.write_text("ORIGINAL ROLE\n", encoding="utf-8")
    built = p45._build_spec_prompt(_p2_ctx(tmp_path / "scratch", str(role)), _P2_NOT_SKIPPED)
    placed = built.data["role_template"]["content"]
    role.write_text("SWAPPED ROLE\n", encoding="utf-8")
    blocks = common._declared_injections(built.data)
    assert [b.content for b in blocks] == [placed] == ["ORIGINAL ROLE\n\n"]
    assert built.data["prompt"].startswith(placed)
