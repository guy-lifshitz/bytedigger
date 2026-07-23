"""The engine's `doctor` — offline neutral self-check command (bd#16).

Runs a fixed sequence of checks (see spec §2), each producing a CheckResult
dict `{"name": str, "status": "ok"|"warn"|"fail", "detail": str}`. No LLM
calls, no network, no writes outside tempdirs.

Public surface: CheckResult (shape only, documented above), run_doctor(argv),
doctor_main(argv), check_event_log(dir_path).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CheckResult = dict[str, str]

HERE = Path(__file__).parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Single source for the dist name + extras (GH1112). Stdlib-only leaf, so it is
# importable in exactly the degraded environments doctor exists to diagnose.
from package_meta import (  # noqa: E402  (needs the sys.path insert above)
    EXTRA_AGENTIC_PYDANTIC,
    EXTRA_SECURITY,
    EXTRA_TEST,
    install_hint,
)

# Monkeypatchable seam (AC5): checks 6+8 create their scratch dirs through this.
_mkdtemp = tempfile.mkdtemp

# Monkeypatchable seams (bd#77 v2): backend/runtime checks.
_which = shutil.which
_find_spec = importlib.util.find_spec


def _run_cmd(argv: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        out = proc.stdout.strip() or proc.stderr.strip()
        return proc.returncode, out
    except (subprocess.TimeoutExpired, OSError) as e:
        return 1, f"{type(e).__name__}: {e}"


def _ok(name: str, detail: str = "") -> CheckResult:
    return {"name": name, "status": "ok", "detail": detail}


def _warn(name: str, detail: str = "") -> CheckResult:
    return {"name": name, "status": "warn", "detail": detail}


def _fail(name: str, detail: str = "") -> CheckResult:
    return {"name": name, "status": "fail", "detail": detail}


def check_python_version() -> CheckResult:
    if sys.version_info >= (3, 9):
        return _ok("python-version", f"{sys.version_info.major}.{sys.version_info.minor}")
    return _fail("python-version", f"requires >=3.9, got {sys.version_info.major}.{sys.version_info.minor}")


def check_engine_imports() -> CheckResult:
    try:
        import contracts  # noqa: F401
        import engine
        import event_log  # noqa: F401
        import derive_state  # noqa: F401
        import workflows
    except ImportError as e:
        return _fail("engine-imports", f"missing module: {e.name}")
    try:
        eng = engine.WorkflowEngine()
        workflows.register_all(eng)
        if not eng.registered():
            return _fail("engine-imports", "no workflows registered after register_all")
    except Exception as e:  # last-resort — degrade to fail, don't crash doctor
        return _fail("engine-imports", f"{type(e).__name__}: {e}")
    return _ok("engine-imports", f"registered: {', '.join(eng.registered())}")


def check_gates_importable() -> CheckResult:
    modules = ["stub_passability", "red_lint_checks", "verdict_gate", "spec_cite", "tier_gate"]
    for mod_name in modules:
        try:
            __import__(mod_name)
        except ImportError:
            return _fail("gates-importable", f"missing module: {mod_name}")
    return _ok("gates-importable", f"importable: {', '.join(modules)}")


def check_optional_deps() -> CheckResult:
    # dbos is a hard dependency, not an extra — its hint carries no extra name
    # (GH1112: a `[dbos]` hint would name something pip cannot resolve).
    probes: list[tuple[str, str | None]] = [
        ("yaml", EXTRA_TEST),
        ("dbos", None),
        ("pydantic_ai", EXTRA_AGENTIC_PYDANTIC),
    ]
    missing = []
    for mod_name, extra in probes:
        try:
            __import__(mod_name)
        except ImportError:
            missing.append(f"{mod_name} ({install_hint(extra)})")
    if shutil.which("semgrep") is None:
        missing.append(f"semgrep ({install_hint(EXTRA_SECURITY)})")
    if not missing:
        return _ok("optional-deps", "all optional deps present")
    return _warn("optional-deps", "missing: " + "; ".join(missing))


def check_config_resolve() -> CheckResult:
    try:
        import config_provider
        provider = config_provider.get_config()
        paths = provider.constitution_search_paths()  # type: ignore[attr-defined]
        for candidate in paths:
            if Path(candidate).expanduser().exists():
                return _ok("config-resolve", candidate)
        return _warn("config-resolve", f"no constitution.md found (searched: {', '.join(paths)})")
    except Exception as e:
        return _fail("config-resolve", f"{type(e).__name__}: {e}")


def check_event_log(dir_path: str) -> CheckResult:
    try:
        import event_log as event_log_mod
        log_path = Path(dir_path) / "doctor-events.jsonl"
        log = event_log_mod.EventLog(str(log_path))
        log.append("doctor_probe", {"probe": True}, run_id="doctor")
        records = log.read_all()
        if not records:
            return _fail("event-log-writable", "no records read back after append")
        return _ok("event-log-writable", f"wrote+read {len(records)} record(s) at {log_path}")
    except Exception as e:
        return _fail("event-log-writable", f"{type(e).__name__}: {e}")


def check_event_log_writable() -> CheckResult:
    tmp = _mkdtemp()
    return check_event_log(tmp)


def check_backend_registry() -> CheckResult:
    try:
        from lib import llm_provider
        spec = llm_provider.get_provider()
        return _ok("backend-registry", spec.name)
    except Exception as e:
        return _fail("backend-registry", f"{type(e).__name__}: {e}")


def check_engine_smoke() -> CheckResult:
    try:
        from contracts import WorkflowContext
        from engine import WorkflowEngine
        from event_log import EventLog
        import workflows

        tmp = _mkdtemp()
        log_path = Path(tmp) / "doctor-events.jsonl"
        log = EventLog(str(log_path))
        eng = WorkflowEngine(event_log=log)
        workflows.register_all(eng)

        ctx = WorkflowContext(
            tenant_id="doctor",
            scope=None,
            db_path=None,
            org_config={},
            question="doctor smoke",
            session_id="doctor",
            persona="doctor",
            framework=None,
            domain=None,
        )
        result, _ctx = eng.execute("echo", ctx, run_id="doctor-smoke")
        if result.status != "ok":
            return _fail("engine-smoke", f"echo workflow status={result.status} error={result.error}")
        if not log_path.exists() or log_path.stat().st_size == 0:
            return _fail("engine-smoke", "event log file empty after echo workflow execution")
        return _ok("engine-smoke", f"echo workflow status=ok, events written to {log_path}")
    except Exception as e:
        return _fail("engine-smoke", f"{type(e).__name__}: {e}")


def check_claude_cli() -> CheckResult:
    path = _which("claude")
    if path is None:
        return _warn(
            "claude-cli",
            "claude CLI not found on PATH — optional, only needed by the "
            "agent-sdk and subprocess backends",
        )
    rc, out = _run_cmd(["claude", "--version"])
    if rc == 0:
        return _ok("claude-cli", out)
    return _warn("claude-cli", f"found at {path} but --version failed: {out}")


def check_agent_sdk_import() -> CheckResult:
    if _find_spec("claude_agent_sdk") is not None:
        return _ok("agent-sdk-import", "claude_agent_sdk importable")
    from llm_subprocess import _REFERENCE_BACKEND_INSTALL_HINTS

    hint = _REFERENCE_BACKEND_INSTALL_HINTS.get("agent-sdk", "")
    return _warn("agent-sdk-import", f"claude_agent_sdk not importable — {hint}")


def check_git_runtime() -> CheckResult:
    if _which("git") is None:
        return _fail("git-runtime", "git not found on PATH")
    # Function-local import (§2.13, AC36b): doctor stays importable standalone,
    # with nothing but lib/ present, while the probes still route through the port.
    from lib.git_port import git_read

    def _probe(args: list[str]) -> tuple[int, str]:
        try:
            result = git_read(args, timeout=10)
        except (subprocess.SubprocessError, OSError) as e:
            return 1, f"{type(e).__name__}: {e}"
        return result.returncode, (result.stdout.strip() or result.stderr.strip())

    rc, out = _probe(["rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        return _warn("git-runtime", f"not inside a git repo — worktree isolation unavailable: {out}")
    rc, out = _probe(["worktree", "list"])
    if rc == 0:
        return _ok("git-runtime", "git available, worktree supported")
    return _warn("git-runtime", f"git worktree unsupported: {out}")


def check_config_json() -> CheckResult:
    path = Path.cwd() / "bytedigger.json"
    if not path.exists():
        return _ok("config-json", "absent — engine defaults")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        n_keys = len(data) if isinstance(data, dict) else 0
        return _ok("config-json", f"parsed ok, {n_keys} top-level key(s)")
    except (json.JSONDecodeError, OSError) as e:
        return _fail("config-json", f"{type(e).__name__}: {e}")


def check_env_alias() -> CheckResult:
    try:
        import config_provider
        config_provider.get_config()
        mapping = config_provider.env_mapping()
        return _ok("env-alias", f"{len(mapping)} BD_/BYTEDIGGER_ alias entries materialized")
    except Exception as e:
        return _fail("env-alias", f"{type(e).__name__}: {e}")


_CHECKS = [
    check_python_version,
    check_engine_imports,
    check_gates_importable,
    check_optional_deps,
    check_config_resolve,
    check_event_log_writable,
    check_backend_registry,
    check_engine_smoke,
    check_claude_cli,
    check_agent_sdk_import,
    check_git_runtime,
    check_config_json,
    check_env_alias,
]


def run_doctor(argv: list[str]) -> tuple[list[CheckResult], int]:
    """Run all checks in contract order. Returns (checks, exit_code).

    exit_code: 1 if any check status == "fail", else 0.
    """
    checks = [check() for check in _CHECKS]
    exit_code = 1 if any(c["status"] == "fail" for c in checks) else 0
    return checks, exit_code


def _print_human(checks: list[CheckResult]) -> None:
    n_ok = sum(1 for c in checks if c["status"] == "ok")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    n_fail = sum(1 for c in checks if c["status"] == "fail")
    for c in checks:
        tag = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}[c["status"]]
        detail = f" — {c['detail']}" if c["detail"] else ""
        print(f"{tag} {c['name']}{detail}")
    print(f"doctor: {n_ok} ok, {n_warn} warn, {n_fail} fail")


def doctor_main(argv: list[str]) -> int:
    as_json = False
    for arg in argv:
        if arg == "--json":
            as_json = True
        else:
            sys.stderr.write(f"doctor: unknown argument {arg!r} (usage: doctor [--json])\n")
            return 2

    checks, exit_code = run_doctor(argv)

    if as_json:
        status = "fail" if any(c["status"] == "fail" for c in checks) else "ok"
        print(json.dumps({"status": status, "checks": checks}))
    else:
        _print_human(checks)

    return exit_code
