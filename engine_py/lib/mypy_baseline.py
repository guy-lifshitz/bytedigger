"""mypy_baseline — shared mypy invocation primitives (GH316, §2.1).

Moved verbatim from phase_5_implement.py (was _mypy_base_argv / _MYPY_LINE_RE /
_parse_mypy_output, L2503-2576).  Public names are the canonical single source
(§1g); phase_5 re-imports under its private aliases to preserve call-site
compatibility.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path


def mypy_base_argv(git_cwd: str) -> list[str]:
    """Return the mypy invocation prefix (no target files).

    Binary: ['uvx','mypy'] if uvx on PATH, else ['mypy'] (mirrors canary.sh).
    Config detection: if mypy.ini / setup.cfg([mypy]) / pyproject.toml([tool.mypy])
    exists in git_cwd, rely on auto-discovery (no --ignore-missing-imports).
    Else append lenient defaults.
    Always appends --follow-imports=silent --no-error-summary --no-color-output.
    """
    if shutil.which("uvx"):
        argv = ["uvx", "mypy"]
    else:
        argv = ["mypy"]

    # Config detection: target-repo-first (Caveat-2)
    has_config = False
    mypy_ini = Path(git_cwd) / "mypy.ini"
    if mypy_ini.is_file():
        has_config = True
    if not has_config:
        setup_cfg = Path(git_cwd) / "setup.cfg"
        if setup_cfg.is_file():
            try:
                content = setup_cfg.read_text(encoding="utf-8", errors="replace")
                if "[mypy]" in content:
                    has_config = True
            except OSError:
                pass
    if not has_config:
        pyproject = Path(git_cwd) / "pyproject.toml"
        if pyproject.is_file():
            try:
                content = pyproject.read_text(encoding="utf-8", errors="replace")
                if "[tool.mypy]" in content:
                    has_config = True
            except OSError:
                pass

    if not has_config:
        argv.append("--ignore-missing-imports")

    argv += ["--follow-imports=silent", "--no-error-summary", "--no-color-output"]
    return argv


MYPY_LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?:\d+:)? (?P<severity>error|warning): "
    r"(?P<msg>.*?)(?:  \[(?P<code>[\w-]+)\])?$"
)


def parse_mypy_output(stdout: str) -> list[dict]:
    """Parse mypy text output into a list of finding dicts.

    Each finding: {check_id, severity, path, start}.
    Ignores note: lines and Success: lines.  Returns [] for empty/unparseable.
    """
    findings: list[dict] = []
    for line in stdout.splitlines():
        m = MYPY_LINE_RE.match(line)
        if not m:
            continue
        severity = m.group("severity")
        # note: lines are context-only, not findings
        if severity == "note":
            continue
        code = m.group("code")
        findings.append({
            "check_id": code if code else "mypy",
            "severity": severity,
            "path": m.group("path"),
            "start": int(m.group("line")),
        })
    return findings
