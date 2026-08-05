"""ac_dsl.py — AC-checks DSL: schema, parser, admission, mechanical runner.

Ship: GH517 Ship A1 (frozen spec: Decisions/
2026-07-11_GH517A1_ac_dsl_ship_spec.md). Sole source of the `### AC-checks`
schema — the registry `CHECK_TYPES` mirrors the pattern of
`flags_catalog.FLAGS` / `error_codes.ERROR_CODES` (hand-written dict, closed
by AC1's registry-closure test).

No env reads at import time. `verdict_verify` is imported as a plain sibling
module (both live in engine_py/, both are on sys.path via conftest.py /
the spec-compile.py CLI shim).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bytedigger_engine import verdict_verify

# ---------------------------------------------------------------------------
# §1.1 CHECK_TYPES registry
# ---------------------------------------------------------------------------


def _req(**extra):
    d = {"required": True}
    d.update(extra)
    return d


def _opt(**extra):
    d = {"required": False}
    d.update(extra)
    return d


CHECK_TYPES: dict[str, dict] = {
    "file_contains": {
        "description": "Assert a file on disk does/doesn't contain a pattern.",
        "params": {
            "path": _req(),
            "pattern": _opt(),
            "kind": _opt(enum=["fixed", "regex"], default="fixed"),
            "expect": _opt(enum=["present", "absent"], default="present"),
        },
    },
    "cmd_exit": {
        "description": "Run a command and assert its exit code (and optional stdout/stderr regex).",
        "params": {
            "argv": _req(),
            "cwd": _opt(),
            "env": _opt(),
            "exit_code": _req(),
            "stdout_re": _opt(),
            "stderr_re": _opt(),
            "timeout_s": _opt(default=60),
        },
    },
    "event_emitted": {
        "description": "Assert a named event appears (or is absent) in a jsonl stream.",
        "params": {
            "stream": _req(),
            "name": _req(),
            "fields_subset": _opt(),
            "min_count": _opt(default=1),
        },
    },
    "json_field": {
        "description": "Assert a dot-path field in a JSON/JSONL file equals/matches/is-absent.",
        "params": {
            "file": _req(),
            "pointer": _req(),
            "op": _req(enum=["equals", "matches", "absent"]),
            "value": _opt(),
        },
    },
    "sha_anchor": {
        "description": "Assert a verdict-anchor block verifies and covers the given files.",
        "params": {
            "doc": _req(),
            "files": _req(),
        },
    },
    "count_delta": {
        "description": "Assert a counter (cmd stdout or jsonl line count) moved by expected_delta.",
        "params": {
            "counter": _req(),
            "kind": _req(enum=["cmd", "stream"]),
            "baseline": _req(),
            "expected_delta": _req(),
        },
    },
    "monotonic": {
        "description": "Assert a field across a jsonl stream is non-decreasing.",
        "params": {
            "stream": _req(),
            "field": _req(),
        },
    },
    "llm_judged": {
        "description": "Defer to LLM judgement against a rubric (not mechanically executed).",
        "params": {
            "rubric": _req(),
        },
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CheckSpec:
    ac_id: str
    type: str
    params: dict


@dataclass
class AdmissionReport:
    result: str
    per_ac: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


@dataclass
class CheckResult:
    ac_id: str
    type: str
    status: str
    reason: str


# ---------------------------------------------------------------------------
# AC-id normalization (local, to avoid importing verdict_verify's private symbol)
# ---------------------------------------------------------------------------


def _normalize_ac_id(raw: str) -> str:
    raw = str(raw).strip()
    if raw.upper().startswith("AC"):
        return "AC" + raw[2:]
    return f"AC{raw}"


_AC_CHECKS_HEADER_RE = re.compile(r"^### AC-checks\s*$", re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^#{1,3}\s")
_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)


def parse_ac_checks(spec_text: str) -> dict[str, CheckSpec]:
    """Parse the `### AC-checks` section's single yaml block into CheckSpecs."""
    header_match = _AC_CHECKS_HEADER_RE.search(spec_text)
    if not header_match:
        raise ValueError("AC-checks section not found (heading '### AC-checks' missing)")

    lines = spec_text.splitlines()
    # find the line index of the header
    header_line_idx = spec_text.count("\n", 0, header_match.start())

    end_idx = len(lines)
    for i in range(header_line_idx + 1, len(lines)):
        if _NEXT_HEADING_RE.match(lines[i]):
            end_idx = i
            break

    section_text = "\n".join(lines[header_line_idx:end_idx])

    fence_match = _YAML_FENCE_RE.search(section_text)
    if fence_match:
        yaml_text = fence_match.group(1)
    else:
        # bare yaml: everything after the header line
        yaml_text = "\n".join(lines[header_line_idx + 1:end_idx])

    # lazy import: PyYAML is absent in clean uvx environments (canary.sh runs
    # spec tests via `uvx --from pytest`); a top-level import used to break collection
    # of 12 modules through the phase_45_spec -> ac_dsl chain
    import yaml

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"AC-checks yaml unparseable: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("AC-checks yaml root is not a mapping")

    out: dict[str, CheckSpec] = {}
    for raw_id, block in data.items():
        ac_id = _normalize_ac_id(raw_id)
        if not isinstance(block, dict):
            raise ValueError(f"AC-checks block for {raw_id} is not a mapping")
        block = dict(block)
        ctype = block.pop("type", None)
        out[ac_id] = CheckSpec(ac_id=ac_id, type=ctype, params=block)
    return out


# ---------------------------------------------------------------------------
# admit
# ---------------------------------------------------------------------------


def admit(spec_text: str) -> AdmissionReport:
    per_ac: dict = {}
    reasons: list = []

    spec_ac_ids = verdict_verify.parse_spec_ac_ids(spec_text)
    if not spec_ac_ids:
        return AdmissionReport(result="REJECT", per_ac={}, reasons=["no ACs found"])

    try:
        checks = parse_ac_checks(spec_text)
    except ValueError as exc:
        return AdmissionReport(
            result="REJECT",
            per_ac={},
            reasons=[f"AC-checks section missing/unparseable: {exc}"],
        )

    check_ids = set(checks.keys())

    for ac_id in spec_ac_ids:
        if ac_id not in check_ids:
            per_ac[ac_id] = {"status": "REJECT", "reason": "missing check block"}

    for ac_id in check_ids:
        if ac_id not in spec_ac_ids:
            per_ac[ac_id] = {"status": "REJECT", "reason": "orphan check block (no §3 AC)"}
            reasons.append(f"orphan check block (no §3 AC): {ac_id}")

    # Type + param validation for every AC that has a check block and is not
    # already rejected as orphan-only (orphan ACs still get validated too, but
    # their status is already REJECT — validation may add more detail via reasons).
    for ac_id, spec in checks.items():
        if ac_id in per_ac and per_ac[ac_id]["status"] == "REJECT" and "orphan" in per_ac[ac_id]["reason"]:
            # still validate for informative reasons, but don't overwrite orphan status
            pass

        ctype = spec.type
        if ctype is None or ctype not in CHECK_TYPES:
            reason = f"unknown check type: {ctype}"
            if ac_id not in per_ac or per_ac[ac_id]["status"] != "REJECT":
                per_ac[ac_id] = {"status": "REJECT", "reason": reason}
            continue

        type_schema = CHECK_TYPES[ctype]["params"]
        param_reason = None
        for pname in spec.params:
            if pname not in type_schema:
                param_reason = f"unknown parameter: {pname}"
                break
        if param_reason is None:
            for pname, pschema in type_schema.items():
                if pschema.get("required") and pname not in spec.params:
                    param_reason = f"missing required parameter: {pname}"
                    break
        if param_reason is not None:
            if ac_id not in per_ac or per_ac[ac_id]["status"] != "REJECT":
                per_ac[ac_id] = {"status": "REJECT", "reason": param_reason}
            continue

        if ctype == "llm_judged":
            rubric = spec.params.get("rubric")
            if not isinstance(rubric, str) or not rubric.strip():
                if ac_id not in per_ac or per_ac[ac_id]["status"] != "REJECT":
                    per_ac[ac_id] = {"status": "REJECT", "reason": "llm_judged without rubric"}
                continue

    # Presence-gate: event_emitted with min_count==0 requires a paired positive
    # (min_count>=1, same stream) somewhere in the check set.
    for ac_id, spec in checks.items():
        if spec.type != "event_emitted":
            continue
        if ac_id in per_ac and per_ac[ac_id]["status"] == "REJECT":
            continue
        min_count = spec.params.get("min_count", 1)
        if min_count != 0:
            continue
        stream = spec.params.get("stream")
        has_pair = False
        for other_id, other in checks.items():
            if other_id == ac_id or other.type != "event_emitted":
                continue
            other_min = other.params.get("min_count", 1)
            if other.params.get("stream") == stream and other_min != 0:
                has_pair = True
                break
        if not has_pair:
            per_ac[ac_id] = {
                "status": "REJECT",
                "reason": "absence check without paired positive",
            }

    for ac_id in check_ids:
        if ac_id in spec_ac_ids and ac_id not in per_ac:
            per_ac[ac_id] = {"status": "OK", "reason": ""}

    result = "ACCEPT" if all(v["status"] == "OK" for v in per_ac.values()) else "REJECT"
    return AdmissionReport(result=result, per_ac=per_ac, reasons=reasons)


# ---------------------------------------------------------------------------
# run_checks — mechanical executors
# ---------------------------------------------------------------------------


def _read_jsonl_objs(path: Path) -> list[dict]:
    objs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            objs.append(obj)
    return objs


def _exec_file_contains(params: dict, repo_root: Path) -> tuple[str, str]:
    path = repo_root / params["path"]
    pattern = params.get("pattern")
    kind = params.get("kind", "fixed")
    expect = params.get("expect", "present")

    if pattern is None:
        found = path.exists()
    else:
        if not path.exists():
            found = False
        else:
            text = path.read_text(encoding="utf-8")
            if kind == "regex":
                found = re.search(pattern, text) is not None
            else:
                found = pattern in text

    ok = found if expect == "present" else not found
    return ("PASS", "") if ok else ("FAIL", f"file_contains expect={expect} found={found}")


def _exec_cmd_exit(params: dict, repo_root: Path) -> tuple[str, str]:
    argv = params["argv"]
    cwd = repo_root / params["cwd"] if params.get("cwd") else repo_root
    env_overlay = params.get("env") or {}
    run_env = dict(os.environ)
    run_env.update(env_overlay)
    timeout_s = params.get("timeout_s", 60)
    proc = subprocess.run(
        argv, cwd=str(cwd), env=run_env, timeout=timeout_s,
        capture_output=True, text=True,
    )
    ok = proc.returncode == params["exit_code"]
    if ok and params.get("stdout_re"):
        ok = re.search(params["stdout_re"], proc.stdout) is not None
    if ok and params.get("stderr_re"):
        ok = re.search(params["stderr_re"], proc.stderr) is not None
    if ok:
        return ("PASS", "")
    return ("FAIL", f"cmd_exit returncode={proc.returncode} expected={params['exit_code']}")


def _exec_event_emitted(params: dict, repo_root: Path) -> tuple[str, str]:
    stream = repo_root / params["stream"]
    name = params["name"]
    fields_subset = params.get("fields_subset") or {}
    min_count = params.get("min_count", 1)

    if not stream.exists():
        if min_count == 0:
            return ("PASS", "")
        return ("FAIL", f"event_emitted: stream not found: {stream}")

    try:
        objs = _read_jsonl_objs(stream)
    except OSError as exc:
        if min_count == 0:
            return ("PASS", "")
        return ("FAIL", f"driver: {exc}")

    count = 0
    for obj in objs:
        if obj.get("name") != name:
            continue
        if all(obj.get(k) == v for k, v in fields_subset.items()):
            count += 1

    if min_count == 0:
        ok = count == 0
    else:
        ok = count >= min_count
    if ok:
        return ("PASS", "")
    return ("FAIL", f"event_emitted count={count} min_count={min_count}")


def _resolve_pointer(obj, pointer: str):
    node = obj
    for seg in pointer.split("."):
        if isinstance(node, list):
            idx = int(seg)
            node = node[idx]
        elif isinstance(node, dict):
            if seg not in node:
                raise KeyError(seg)
            node = node[seg]
        else:
            raise KeyError(seg)
    return node


def _exec_json_field(params: dict, repo_root: Path) -> tuple[str, str]:
    path = repo_root / params["file"]
    pointer = params["pointer"]
    op = params["op"]
    value = params.get("value")

    if str(path).endswith(".jsonl"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        text = lines[-1] if lines else "{}"
        data = json.loads(text)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    try:
        node = _resolve_pointer(data, pointer)
        resolved = True
    except (KeyError, IndexError, ValueError, TypeError):
        node = None
        resolved = False

    if op == "absent":
        ok = not resolved
    elif op == "equals":
        ok = resolved and node == value
    elif op == "matches":
        ok = resolved and re.search(str(value), str(node)) is not None
    else:
        ok = False

    if ok:
        return ("PASS", "")
    return ("FAIL", f"json_field op={op} resolved={resolved} node={node!r}")


def _exec_sha_anchor(params: dict, repo_root: Path) -> tuple[str, str]:
    doc = repo_root / params["doc"]
    text = doc.read_text(encoding="utf-8")
    result = verdict_verify.verify_anchor(text, repo_root)
    if result.result != "VERIFIED":
        return ("FAIL", f"sha_anchor result={result.result} reason={result.reason}")

    anchor_paths = {relpath for _kind, relpath, _sha in verdict_verify.parse_anchor(text)}
    for f in params["files"]:
        if f not in anchor_paths:
            return ("FAIL", f"sha_anchor: file {f} not present in anchor block")
    return ("PASS", "")


def _exec_count_delta(params: dict, repo_root: Path) -> tuple[str, str]:
    kind = params["kind"]
    counter = params["counter"]
    baseline = params["baseline"]
    expected_delta = params["expected_delta"]

    if kind == "cmd":
        proc = subprocess.run(
            counter, cwd=str(repo_root), capture_output=True, text=True, timeout=60
        )
        m = re.search(r"-?\d+", proc.stdout)
        current = int(m.group(0)) if m else 0
    else:
        path = repo_root / counter
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        current = len(lines)

    delta = current - baseline
    if delta == expected_delta:
        return ("PASS", "")
    return ("FAIL", f"count_delta current={current} baseline={baseline} delta={delta} expected={expected_delta}")


def _exec_monotonic(params: dict, repo_root: Path) -> tuple[str, str]:
    stream = repo_root / params["stream"]
    field_name = params["field"]
    objs = _read_jsonl_objs(stream)
    values = [obj[field_name] for obj in objs if field_name in obj]
    if not values:
        return ("FAIL", "monotonic: no values found")
    ok = all(values[i] <= values[i + 1] for i in range(len(values) - 1))
    if ok:
        return ("PASS", "")
    return ("FAIL", f"monotonic: not non-decreasing: {values}")


_EXECUTORS = {
    "file_contains": _exec_file_contains,
    "cmd_exit": _exec_cmd_exit,
    "event_emitted": _exec_event_emitted,
    "json_field": _exec_json_field,
    "sha_anchor": _exec_sha_anchor,
    "count_delta": _exec_count_delta,
    "monotonic": _exec_monotonic,
}


def run_checks(checks: dict, repo_root, env=None) -> list:
    repo_root = Path(repo_root)
    results = []
    for ac_id, spec in checks.items():
        if spec.type == "llm_judged":
            results.append(CheckResult(ac_id=ac_id, type=spec.type, status="JUDGE", reason=""))
            continue
        executor = _EXECUTORS.get(spec.type)
        if executor is None:
            results.append(
                CheckResult(ac_id=ac_id, type=spec.type, status="FAIL", reason=f"unknown type: {spec.type}")
            )
            continue
        try:
            status, reason = executor(spec.params, repo_root)
        except Exception as exc:  # noqa: BLE001 - fail-closed driver errors (§lesson 221)
            status, reason = "FAIL", f"driver: {exc}"
        results.append(CheckResult(ac_id=ac_id, type=spec.type, status=status, reason=reason))
    return results


# ---------------------------------------------------------------------------
# render_checklist
# ---------------------------------------------------------------------------


def render_checklist(results: list) -> str:
    lines = []
    for r in results:
        if r.status == "JUDGE":
            lines.append(f"- {r.ac_id}: LLM_JUDGED — rubric deferred")
        elif r.status == "PASS":
            lines.append(f"- {r.ac_id}: PASS")
        else:
            lines.append(f"- {r.ac_id}: FAIL ({r.reason})")
    return "\n".join(lines) + ("\n" if lines else "")
