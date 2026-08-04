#!/usr/bin/env python3
"""security-lint.py — deterministic semgrep + gitleaks security gate (gh-issue-339, SECBUILD 2-A).

Usage: security-lint.py FILE [FILE...] [--json]
Exit codes: 0 = PASS / SKIP / warn-only, 1 = REJECT (>=1 HIGH finding), 2 = driver error.
Env seams: HAL_SEMGREP_BIN, HAL_GITLEAKS_BIN, HAL_SECURITY_RULES, HAL_GITLEAKS_CONFIG,
HAL_SECURITY_LINT_GATE (0 disables the gate entirely).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RULES = SCRIPT_DIR / "semgrep-rules.yml"
DEFAULT_GITLEAKS_CONFIG = SCRIPT_DIR / "gitleaks.toml"
_SEV_MAP = {"ERROR": "HIGH", "WARNING": "MEDIUM"}


def _repo_root():
    return Path(__file__).resolve().parents[4]


def _emit_skip_event(reason, semgrep_bin, gitleaks_bin, env, *, ts=None):
    if env.get("HAL_SECURITY_LINT_SKIP_EMIT") == "0":
        return
    log = env.get("HAL_SECURITY_LINT_EVENT_LOG", str(_repo_root() / "SHARED/state/build-events.jsonl"))
    helper = env.get("HAL_JSONL_APPEND_SH", str(_repo_root() / "SYSTEM/lib/jsonl-append.sh"))
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = json.dumps({
        "event_type": "security_lint_skipped",
        "ts": ts,
        "payload": {
            "reason": "tools-missing",
            "detail": reason,
            "semgrep_bin": semgrep_bin,
            "gitleaks_bin": gitleaks_bin,
            "source": "security-lint",
        },
    }, sort_keys=True)
    if not os.path.isfile(helper):
        return
    try:
        subprocess.run(
            ["bash", "-c", 'source "$1" && jsonl_append "$2" "$3"', "_", str(helper), str(log), line],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"security-lint: skip-event emit failed: {exc}", file=sys.stderr)


def run_semgrep(files, env):
    rules = env.get("HAL_SECURITY_RULES", str(DEFAULT_RULES))
    if not os.path.isfile(rules):
        return None, f"rules file not found: {rules}"
    bin_path = env.get("HAL_SEMGREP_BIN", "semgrep")
    try:
        proc = subprocess.run(
            [bin_path, "scan", "--config", rules, "--json", "--quiet", "--metrics=off", *files],
            capture_output=True, text=True, timeout=120,
        )
    except OSError as exc:
        return None, f"semgrep exec failed: {exc}"
    if proc.returncode != 0:
        return None, f"semgrep exited {proc.returncode}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "semgrep JSON parse failure"
    findings = []
    for r in data.get("results", []):
        sev = _SEV_MAP.get(r.get("extra", {}).get("severity"))
        if sev is None:
            continue
        check_id = r.get("check_id")
        rule_id = check_id.rsplit(".", 1)[-1] if check_id else check_id
        findings.append({"severity": sev, "rule_id": rule_id,
                          "path": r.get("path"), "line": r.get("start", {}).get("line"),
                          "source": "semgrep"})
    return findings, None


def run_gitleaks(files, env):
    config = env.get("HAL_GITLEAKS_CONFIG", str(DEFAULT_GITLEAKS_CONFIG))
    if not os.path.isfile(config):
        return None, f"gitleaks config not found: {config}"
    bin_path = env.get("HAL_GITLEAKS_BIN", "gitleaks")
    findings = []
    for f in files:
        fd, report_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            proc = subprocess.run(
                [bin_path, "dir", f, "--config", config, "--no-banner", "--report-format", "json",
                 "--report-path", report_path, "--exit-code", "99"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                continue
            if proc.returncode != 99:
                return None, f"gitleaks exited {proc.returncode} for {f}"
            with open(report_path) as fh:
                report = json.load(fh)
            for item in report:
                findings.append({"severity": "HIGH", "rule_id": item.get("RuleID"),
                                  "path": item.get("File"), "line": item.get("StartLine"),
                                  "source": "gitleaks"})
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"gitleaks report failure for {f}: {exc}"
        finally:
            try:
                os.unlink(report_path)
            except OSError:
                pass
    return findings, None


def _load_except_pass_allowlist(env):
    """GH825: <relpath> <count> lines (blank/#-lines ignored); missing file = empty."""
    path = env.get("HAL_EXCEPT_PASS_ALLOWLIST", str(SCRIPT_DIR / "except-pass-allowlist.txt"))
    allowlist = {}
    if not os.path.isfile(path):
        return allowlist
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        relpath, count = parts[0], parts[1]
        try:
            allowlist[relpath] = allowlist.get(relpath, 0) + int(count)
        except ValueError:
            continue
    return allowlist


def apply_except_pass_allowlist(findings, env):
    """GH825: downgrade up to N allowlisted hal-silent-except-pass HIGHs to MEDIUM."""
    allowlist = _load_except_pass_allowlist(env)
    if not allowlist:
        return findings
    remaining = dict(allowlist)
    out = []
    for f in findings:
        if f["severity"] == "HIGH" and f.get("rule_id") == "hal-silent-except-pass":
            path = f.get("path") or ""
            for relpath, count in remaining.items():
                if count > 0 and path.endswith(relpath):
                    f = dict(f, severity="MEDIUM")
                    remaining[relpath] = count - 1
                    break
        out.append(f)
    return out


def classify(findings):
    highs = [f for f in findings if f["severity"] == "HIGH"]
    meds = [f for f in findings if f["severity"] == "MEDIUM"]
    return ("REJECT" if highs else "PASS"), highs, meds


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--json", action="store_true", dest="json_mode")
    args = parser.parse_args(argv)
    out = sys.stderr if args.json_mode else sys.stdout

    def skip(reason):
        print(f"SECURITY-LINT: SKIP ({reason})", file=out)
        if args.json_mode:
            print(json.dumps({"verdict": "SKIP", "findings": []}))
        return 0

    env = os.environ
    if env.get("HAL_SECURITY_LINT_GATE") == "0":
        return skip("gate disabled")
    if not args.files:
        return skip("no files")

    existing = [f for f in args.files if os.path.exists(f)]
    for f in args.files:
        if f not in existing:
            print(f"WARN path not found, skipping: {f}", file=out)
    if not existing:
        return skip("no files")

    semgrep_bin = env.get("HAL_SEMGREP_BIN", "semgrep")
    gitleaks_bin = env.get("HAL_GITLEAKS_BIN", "gitleaks")
    have_semgrep = shutil.which(semgrep_bin) is not None
    have_gitleaks = shutil.which(gitleaks_bin) is not None
    if not have_semgrep and not have_gitleaks:
        reason = f"semgrep={semgrep_bin}, gitleaks={gitleaks_bin}"
        print(f"SECURITY-LINT: SKIP WARNING — security tooling absent, gate evaporated ({reason})",
              file=sys.stderr)
        _emit_skip_event(reason, semgrep_bin, gitleaks_bin, env)
        if args.json_mode:
            print(json.dumps({"verdict": "SKIP", "findings": []}))
        return 0

    findings = []
    for available, runner, name, bin_name in (
        (have_semgrep, run_semgrep, "semgrep", semgrep_bin),
        (have_gitleaks, run_gitleaks, "gitleaks", gitleaks_bin),
    ):
        if not available:
            print(f"SECURITY-LINT: SKIP ({name} not found: {bin_name})", file=out)
            continue
        result, err = runner(existing, env)
        if err:
            print(f"SECURITY-LINT: ERROR {err}", file=sys.stderr)
            return 2
        findings.extend(result)

    findings = apply_except_pass_allowlist(findings, env)
    verdict, highs, meds = classify(findings)
    for f in highs:
        print(f"HIGH {f['rule_id']} {f['path']}:{f['line']}", file=out)
    for f in meds:
        print(f"WARN {f['rule_id']} {f['path']}:{f['line']}", file=out)

    if verdict == "REJECT":
        print(f"SECURITY-LINT: REJECT {len(highs)} HIGH finding(s)", file=out)
        rc = 1
    elif meds:
        print(f"SECURITY-LINT: PASS ({len(meds)} MEDIUM warn-only; flip-by:2026-07-19 gh-issue-341)", file=out)
        rc = 0
    else:
        print("SECURITY-LINT: PASS", file=out)
        rc = 0

    if args.json_mode:
        print(json.dumps({"verdict": verdict, "findings": highs + meds}))
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"security-lint: driver error: {exc}", file=sys.stderr)
        sys.exit(2)
