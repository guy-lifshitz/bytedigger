"""check_ladder.py -- a pre-screen rung in front of the validation gate (bd#141 item 3).

The ladder orders checks by cost: script, classifier, llm, gate. A cheap rung may
only send work back (``reject``) or pass it up (``escalate``); it never approves.
The gate stays the only rung that approves. The classifier is any command that
reads one JSON document on stdin and prints one JSON line; which binary to run,
its credentials and the policy on a verdict belong to the host.

Public surface (spec: docs/decisions/2026-10-01-bd141-check-ladder.md):

    run_classifier(cmd, payload, timeout_s) -> ClassifierResult
    prescreen(findings, gate_input, classifier_cmd=None, mode="shadow",
              threshold=0.9, timeout_s=30) -> Verdict

CLI: ``python -m bytedigger_engine.check_ladder prescreen --gate-input PATH
[--findings PATH] [--classifier-cmd JSON] [--mode shadow|enforce] [--threshold F]
[--timeout-s F] [--log PATH]`` prints one JSON line and exits 0 on any verdict;
usage errors exit 2 with a message on stderr only.

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, cast

__all__ = [
    "RUNGS",
    "OUTCOMES",
    "MODES",
    "DEFAULT_THRESHOLD",
    "DEFAULT_TIMEOUT_S",
    "run_classifier",
    "prescreen",
    "main",
]

RUNGS = ("script", "classifier", "llm", "gate")
OUTCOMES = ("reject", "escalate")
MODES = ("shadow", "enforce")
DEFAULT_THRESHOLD = 0.9
DEFAULT_TIMEOUT_S = 30

Finding = Dict[str, Any]
ClassifierResult = Dict[str, Any]
Verdict = Dict[str, Any]

_SEVERITIES = ("MAJOR", "MINOR")
_LABELS = ("reject", "pass")


def _as_real(value: Any) -> Optional[float]:
    """The value as a float when it is a finite real number (not bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _result(status: str, ms: int = 0, label: Optional[str] = None,
            confidence: Optional[float] = None, reasons: Optional[List[str]] = None,
            rc: Optional[int] = None, cost_usd: Optional[float] = None) -> ClassifierResult:
    return {
        "status": status, "label": label, "confidence": confidence, "reasons": reasons,
        "rc": rc, "ms": ms, "cost_usd": cost_usd,
    }


def _valid_cmd(cmd: Any) -> bool:
    if not isinstance(cmd, list):
        return False
    return len(cmd) > 0 and all(isinstance(x, str) for x in cmd)


def _parse_answer(stdout: str, ms: int) -> ClassifierResult:
    line = ""
    for raw in stdout.splitlines():
        if raw.strip():
            line = raw.strip()
            break
    bad = _result("error", ms=ms, rc=0)
    if not line:
        return bad
    try:
        doc: Any = json.loads(line)
    except ValueError:
        return bad
    if not isinstance(doc, dict):
        return bad
    obj = cast(Dict[str, Any], doc)
    label: Any = obj.get("label")
    conf = _as_real(obj.get("confidence"))
    if label not in _LABELS or conf is None or not 0 <= conf <= 1:
        return bad
    raw_reasons: Any = obj.get("reasons")
    reasons: List[str] = []
    if isinstance(raw_reasons, list):
        if all(isinstance(x, str) for x in raw_reasons):
            reasons = [str(x) for x in raw_reasons]
    cost = _as_real(obj.get("cost_usd"))
    cost_usd: Optional[float] = cost if cost is not None and cost >= 0 else None
    return _result("ok", ms=ms, label=str(label), confidence=conf, reasons=reasons,
                   rc=0, cost_usd=cost_usd)


def run_classifier(cmd: Optional[List[str]], payload: Any, timeout_s: float) -> ClassifierResult:
    """Run the classifier command once; never raises."""
    if cmd is None:
        return _result("off")
    t0 = time.monotonic()

    def _ms() -> int:
        return max(0, int((time.monotonic() - t0) * 1000))

    try:
        if not _valid_cmd(cmd):
            return _result("error", ms=_ms())
        data = json.dumps(payload).encode("utf-8")
        try:
            proc = subprocess.run(
                cmd, input=data, capture_output=True, timeout=timeout_s, shell=False, check=False,
            )
        except subprocess.TimeoutExpired:
            return _result("timeout", ms=_ms())
        except OSError:
            return _result("error", ms=_ms())
        ms = _ms()
        if proc.returncode != 0:
            return _result("error", ms=ms, rc=proc.returncode)
        return _parse_answer(proc.stdout.decode("utf-8", errors="replace"), ms)
    except Exception:  # noqa: BLE001 -- op1 never raises
        return _result("error", ms=_ms())


def _validate(findings: Any, classifier_cmd: Any, mode: Any, threshold: Any, timeout_s: Any) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    thr = _as_real(threshold)
    if thr is None or not 0 <= thr <= 1:
        raise ValueError(f"threshold must be a finite number in [0, 1], got {threshold!r}")
    tmo = _as_real(timeout_s)
    if tmo is None or tmo <= 0:
        raise ValueError(f"timeout_s must be a finite positive number, got {timeout_s!r}")
    if classifier_cmd is not None and not _valid_cmd(classifier_cmd):
        raise ValueError("classifier_cmd must be a non-empty list of strings")
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    for item in findings:
        if not isinstance(item, dict):
            raise ValueError("each finding must be an object")
        f = cast(Dict[str, Any], item)
        if not isinstance(f.get("rule"), str) or not isinstance(f.get("detail"), str):
            raise ValueError("finding rule and detail must be strings")
        if f.get("severity") not in _SEVERITIES:
            raise ValueError("finding severity must be MAJOR or MINOR")


def prescreen(findings: List[Finding], gate_input: str,
              classifier_cmd: Optional[List[str]] = None, mode: str = "shadow",
              threshold: float = DEFAULT_THRESHOLD,
              timeout_s: float = DEFAULT_TIMEOUT_S) -> Verdict:
    """Turn (script findings, gate input, classifier config) into a pre-screen verdict."""
    _validate(findings, classifier_cmd, mode, threshold, timeout_s)
    majors = [f for f in findings if f["severity"] == "MAJOR"]
    if classifier_cmd is None:
        clf = _result("off")
    elif mode == "enforce" and majors:
        clf = _result("skipped")
    else:
        clf = run_classifier(classifier_cmd, {"gate_input": gate_input, "findings": findings},
                             timeout_s)
    if majors:
        return {"outcome": "reject", "rung": "script", "mode": mode,
                "findings": majors, "classifier": clf}
    if (mode == "enforce" and clf["status"] == "ok" and clf["label"] == "reject"
            and clf["confidence"] >= threshold):
        return {"outcome": "reject", "rung": "classifier", "mode": mode,
                "findings": [], "classifier": clf}
    return {"outcome": "escalate", "rung": None, "mode": mode, "findings": [], "classifier": clf}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class _Usage(Exception):
    """A usage error: message goes to stderr, exit code 2."""


def _read_json_file(path: str, what: str) -> Any:
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise _Usage(f"{what}: cannot read or parse {path}: {exc}") from exc


def _journal(path: str, verdict: Verdict, gate_bytes: bytes) -> None:
    logged = dict(verdict)
    clf = dict(cast(Dict[str, Any], verdict["classifier"]))
    reasons = clf.get("reasons")
    clf["reasons"] = len(reasons) if isinstance(reasons, list) else None
    logged["classifier"] = clf
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "verdict": logged,
        "gate_input_sha256": hashlib.sha256(gate_bytes).hexdigest(),
    }
    line = (json.dumps(record) + "\n").encode("utf-8")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check_ladder", description="Pre-screen rung in front of the validation gate.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prescreen", help="print a pre-screen verdict as one JSON line")
    p.add_argument("--gate-input", required=True, metavar="PATH")
    p.add_argument("--findings", metavar="PATH")
    p.add_argument("--classifier-cmd", metavar="JSON")
    p.add_argument("--mode", choices=MODES, default="shadow")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--timeout-s", type=float, default=float(DEFAULT_TIMEOUT_S))
    p.add_argument("--log", metavar="PATH")
    return parser


def _run_prescreen(args: argparse.Namespace) -> int:
    try:
        with open(args.gate_input, "rb") as fh:
            gate_bytes = fh.read()
        gate_text = gate_bytes.decode("utf-8")
    except (OSError, ValueError) as exc:
        raise _Usage(f"--gate-input: cannot read {args.gate_input}: {exc}") from exc
    findings: Any = []
    if args.findings is not None:
        findings = _read_json_file(args.findings, "--findings")
        if not isinstance(findings, list):
            raise _Usage("--findings: must be a JSON array")
    cmd: Optional[List[str]] = None
    if args.classifier_cmd is not None:
        try:
            parsed: Any = json.loads(args.classifier_cmd)
        except ValueError as exc:
            raise _Usage(f"--classifier-cmd: not valid JSON: {exc}") from exc
        if not _valid_cmd(parsed):
            raise _Usage("--classifier-cmd: must be a non-empty JSON array of strings")
        cmd = cast(List[str], parsed)
    try:
        verdict = prescreen(cast(List[Finding], findings), gate_text, classifier_cmd=cmd,
                            mode=args.mode, threshold=args.threshold, timeout_s=args.timeout_s)
    except ValueError as exc:
        raise _Usage(str(exc)) from exc
    print(json.dumps(verdict))
    if args.log:
        try:
            _journal(args.log, verdict, gate_bytes)
        except OSError as exc:
            print(f"check_ladder: journal write failed: {exc}", file=sys.stderr)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _run_prescreen(args)
    except _Usage as exc:
        print(f"check_ladder: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
