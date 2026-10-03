#!/usr/bin/env python3
"""Provenance: HAL GH#342 A+B (PR #360), bd#176/892bb0f removed the engine stage, hal#2320 audit M1.

Deterministic devops scan (trivy config + hadolint), fail-closed. Standalone,
stdlib only. Spec: docs/decisions/2026-10-03-s4-m1-devops-scan-script.md.

Exit code is the verdict: 0 clean / nothing_to_scan / all gating waived,
1 blocked, 2 unavailable (fail-closed). Stdout is exactly one JSON object.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

EXIT_CLEAN = 0
EXIT_BLOCKED = 1
EXIT_UNAVAILABLE = 2

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
HADOLINT_LEVELS = {"error": "HIGH", "warning": "MEDIUM", "info": "LOW", "style": "LOW"}
IAC_DIRS = {"k8s", "kubernetes", "helm", "charts", "manifests"}
MISSING = "<missing>"
PRAGMAS = ("trivy:ignore", "tfsec:ignore", "hadolint ignore=")
DEFAULT_TIMEOUT = 60.0
MAX_KILL_BY_DAYS = 365


class UsageError(Exception):
    pass


class ScanError(Exception):
    """A scanner run failed; args[0] is the reason token."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # never print usage / exit: main emits a JSON verdict
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="devops_scan.py", add_help=False, allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--files", default=None)
    p.add_argument("--fail-on", dest="fail_on", default="CRITICAL,HIGH")
    p.add_argument("--allowlist", default=None)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return p


def _verdict(status, gating=(), waived=(), nongating=(), reason=""):
    return {
        "status": status,
        "gating": list(gating),
        "waived": list(waived),
        "nongating": list(nongating),
        "reason": reason,
    }


def _finding(scanner, file, fid, severity, description):
    return {
        "scanner": scanner,
        "file": file,
        "id": fid,
        "severity": severity,
        "description": description,
    }


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


# ------------------------------------------------------------ enumeration --


def _rel_in_root(entry: str, root_real: str):
    """Root-relative path for an existing in-root file entry, else None."""
    np = os.path.normpath(os.path.join(root_real, entry))
    rel = os.path.relpath(np, root_real)
    if rel == ".." or rel.startswith(".." + os.sep):
        real = os.path.realpath(np)
        if not _under(real, root_real):
            return None
        rel = os.path.relpath(real, root_real)
    elif not _under(os.path.realpath(np), root_real):
        return None
    if not os.path.isfile(os.path.join(root_real, rel)):
        return None
    return rel


def _git_files(root_real: str, timeout: float):
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root_real,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [os.fsdecode(b) for b in proc.stdout.split(b"\0") if b]


def _classify(rel: str):
    base = os.path.basename(rel)
    low = base.lower()
    if not low.endswith(".dockerignore") and (
        low == "dockerfile"
        or low.startswith("dockerfile.")
        or low.endswith(".dockerfile")
        or low.startswith("containerfile")
    ):
        return "docker"
    if low.endswith((".tf", ".tf.json", ".tfvars")):
        return "iac"
    if fnmatch.fnmatchcase(low, "docker-compose*.y*ml") or fnmatch.fnmatchcase(low, "compose.y*ml"):
        return "iac"
    if base == "Chart.yaml":
        return "iac"
    parts = os.path.dirname(rel).split(os.sep)
    if IAC_DIRS.intersection(parts) and fnmatch.fnmatchcase(low, "*.y*ml"):
        return "iac"
    return None


# -------------------------------------------------------------- allowlist --


def _today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _parse_allowlist(text: str, today: datetime.date):
    entries = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = line.split("::")
        if len(parts) != 3:
            continue
        pattern, agr, kb = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not pattern or not re.fullmatch(r"[0-9A-Fa-f]{8}", agr):
            continue
        m = re.fullmatch(r"kill-by:([0-9]{4}-[0-9]{2}-[0-9]{2})", kb)
        if not m:
            continue
        try:
            kill_by = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if kill_by > today + datetime.timedelta(days=MAX_KILL_BY_DAYS):
            continue
        entries.append((pattern, kill_by))
    return entries


def _waived(f, entries, today) -> bool:
    joined = "|".join((f["scanner"], f["file"], f["id"], f["description"]))
    return any(pat in joined and kb >= today for pat, kb in entries)


# ---------------------------------------------------------------- scanning --


def _exec(cmd, env, cwd, timeout):
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        raise ScanError("subprocess_error")
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.SubprocessError:
            pass
        raise ScanError("timeout")
    return proc.returncode, out.decode("utf-8", errors="replace")


def _load_json(out: str):
    if not out.strip():
        raise ScanError("json_decode_error")
    try:
        return json.loads(out)
    except ValueError:
        raise ScanError("json_decode_error")


def _run_hadolint(binary, rel, root_real, env, cwd, timeout):
    cmd = [binary, "--format", "json", "--no-color", "--disable-ignore-pragma",
           os.path.join(root_real, rel)]
    rc, out = _exec(cmd, env, cwd, timeout)
    if rc not in (0, 1):
        raise ScanError("subprocess_error")
    if rc == 1 and not out.strip():
        raise ScanError("subprocess_error")
    data = _load_json(out)
    if not isinstance(data, list):
        raise ScanError("json_shape_error")
    found = []
    for item in data:
        if not isinstance(item, dict):
            raise ScanError("json_shape_error")
        code = item.get("code")
        fid = code if isinstance(code, str) and code else MISSING
        level = item.get("level")
        sev = HADOLINT_LEVELS.get(level, "HIGH") if isinstance(level, str) else "HIGH"
        msg = item.get("message")
        found.append(_finding("hadolint", rel, fid, sev, msg if isinstance(msg, str) else ""))
    return found


def _run_trivy(binary, scan_dir, root_real, cache_dir, ignorefile, env, cwd, timeout):
    cmd = [binary, "config", "--format", "json", "--quiet",
           "--severity", ",".join(SEVERITIES),
           "--cache-dir", cache_dir,
           "--ignorefile", ignorefile,
           scan_dir]
    rc, out = _exec(cmd, env, cwd, timeout)
    if rc != 0:
        raise ScanError("subprocess_error")
    data = _load_json(out)
    if not isinstance(data, dict):
        raise ScanError("json_shape_error")
    results = data.get("Results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise ScanError("json_shape_error")
    found = []
    for res in results:
        if not isinstance(res, dict):
            raise ScanError("json_shape_error")
        miscs = res.get("Misconfigurations")
        if miscs is None:
            continue
        if not isinstance(miscs, list):
            raise ScanError("json_shape_error")
        target = res.get("Target")
        target = target if isinstance(target, str) else ""
        if os.path.isabs(target) and _under(os.path.normpath(target), root_real):
            target = os.path.relpath(os.path.normpath(target), root_real)
        for m in miscs:
            if not isinstance(m, dict):
                raise ScanError("json_shape_error")
            mid = m.get("ID")
            fid = mid if isinstance(mid, str) and mid else MISSING
            sev = m.get("Severity")
            sev = sev.upper() if isinstance(sev, str) else ""
            if sev not in SEV_RANK:
                sev = "HIGH"
            desc = m.get("Description")
            if not (isinstance(desc, str) and desc):
                desc = m.get("Title")
            found.append(_finding("trivy", target, fid, sev, desc if isinstance(desc, str) else ""))
    return found


def _trivy_cache_dir() -> str:
    explicit = os.environ.get("TRIVY_CACHE_DIR")
    if explicit:
        return os.path.abspath(explicit)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        base = xdg
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Caches")
    else:
        base = os.path.join(home, ".cache")
    return os.path.abspath(os.path.join(base, "trivy"))


def _grep_pragmas(root_real: str, rel: str):
    with open(os.path.join(root_real, rel), "rb") as fh:
        text = fh.read().decode("utf-8", errors="replace").lower()
    hits = [p for p in PRAGMAS if p in text]
    if not hits:
        return None
    return _finding("pragma", rel, "inline_ignore_pragma", "HIGH",
                    "inline suppression pragma found: " + ", ".join(hits))


def _dedup(findings):
    seen = set()
    out = []
    for f in findings:
        key = (f["scanner"], f["file"], f["id"], f["severity"], f["description"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _parse_fail_on(raw: str):
    toks = [t.strip().upper() for t in raw.split(",")]
    if not toks or any(t not in SEV_RANK for t in toks):
        return None
    return set(toks)


def _scan(argv):
    try:
        args = _parser().parse_args(argv)
    except UsageError as exc:
        return _verdict("unavailable", reason="usage_error: " + str(exc))
    if args.timeout <= 0:
        return _verdict("unavailable", reason="usage_error: --timeout must be positive")

    root_abs = os.path.abspath(args.root)
    if not os.path.isdir(root_abs):
        return _verdict("unavailable", reason="root_not_found")
    root_real = os.path.realpath(root_abs)

    fail_on = _parse_fail_on(args.fail_on)
    if fail_on is None:
        return _verdict("unavailable", reason="bad_fail_on")

    today = _today()
    allow = []
    if args.allowlist is not None:
        try:
            with open(args.allowlist, "r", encoding="utf-8", errors="replace") as fh:
                allow = _parse_allowlist(fh.read(), today)
        except OSError:
            return _verdict("unavailable", reason="allowlist_unreadable")

    dropped = 0
    rels = []
    if args.files is not None:
        for entry in args.files.split(","):
            if not entry:
                continue
            rel = _rel_in_root(entry, root_real)
            if rel is None:
                dropped += 1
            else:
                rels.append(rel)
    else:
        listed = _git_files(root_real, args.timeout)
        if listed is None:
            return _verdict("unavailable", reason="git_ls_files_failed")
        for entry in listed:
            if os.path.isfile(os.path.join(root_real, entry)):
                rels.append(entry)
    note = f"dropped_entries={dropped}" if dropped else ""

    dockerfiles = sorted({r for r in rels if _classify(r) == "docker"})
    iacfiles = sorted({r for r in rels if _classify(r) == "iac"})
    if not dockerfiles and not iacfiles:
        return _verdict("nothing_to_scan", reason="; ".join(x for x in ("no_candidates", note) if x))

    path_env = os.environ.get("PATH", os.defpath)
    hadolint = shutil.which("hadolint", path=path_env) if dockerfiles else None
    trivy = shutil.which("trivy", path=path_env) if iacfiles else None
    missing = []
    if dockerfiles and not hadolint:
        missing.append("hadolint_not_found")
    if iacfiles and not trivy:
        missing.append("trivy_not_found")
    if missing:
        return _verdict("unavailable", reason=",".join(missing))

    findings = []
    for rel in dockerfiles + iacfiles:
        pf = _grep_pragmas(root_real, rel)
        if pf is not None:
            findings.append(pf)

    reasons = []
    base_tmp = tempfile.gettempdir()
    if _under(os.path.realpath(base_tmp), root_real):
        base_tmp = "/tmp"
    work = tempfile.mkdtemp(prefix="devops_scan_", dir=base_tmp)
    try:
        home = os.path.join(work, "home")
        cwd = os.path.join(work, "cwd")
        tmpd = os.path.join(work, "tmp")
        for d in (home, cwd, tmpd):
            os.mkdir(d)
        ignorefile = os.path.join(work, "empty.ignore")
        open(ignorefile, "w").close()
        env = {"PATH": path_env, "HOME": home, "LANG": "C", "TMPDIR": tmpd}

        if hadolint:
            for rel in dockerfiles:
                try:
                    findings.extend(_run_hadolint(hadolint, rel, root_real, env, cwd, args.timeout))
                except ScanError as exc:
                    reasons.append(exc.args[0])
        if trivy:
            cache_dir = _trivy_cache_dir()
            dirs = sorted({os.path.join(root_real, os.path.dirname(r)).rstrip(os.sep) or root_real
                           for r in iacfiles})
            for d in dirs:
                try:
                    findings.extend(_run_trivy(trivy, d, root_real, cache_dir, ignorefile,
                                               env, cwd, args.timeout))
                except ScanError as exc:
                    reasons.append(exc.args[0])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    findings = _dedup(findings)
    gating, waived, nongating = [], [], []
    for f in findings:
        if f["severity"] in fail_on:
            (waived if _waived(f, allow, today) else gating).append(f)
        else:
            nongating.append(f)

    uniq = []
    for r in reasons:
        if r not in uniq:
            uniq.append(r)
    if uniq:
        status = "unavailable"
    elif gating:
        status = "blocked"
    else:
        status = "clean"
    reason = "; ".join(x for x in (",".join(uniq), note) if x)
    return _verdict(status, gating, waived, nongating, reason)


def main(argv=None) -> int:
    try:
        verdict = _scan(sys.argv[1:] if argv is None else argv)
    except Exception:
        verdict = _verdict("unavailable", reason="internal_error")
    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()
    status = verdict["status"]
    if status == "blocked":
        return EXIT_BLOCKED
    if status == "unavailable":
        return EXIT_UNAVAILABLE
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
