"""Host-callable deterministic preflight with a tree-state receipt (bd#164).

One function runs a fixed list of deterministic steps over a work tree and writes a
receipt; one function tells a host whether that receipt still describes the tree. No
model is ever called. Stdlib plus the engine's own building blocks.

CLI (dispatched from ``run.py``)::

    bytedigger-engine preflight --spec PATH --phase red|green [--json] [--base REF]
        [--tier T] [--classifier-cmd JSON-ARGV] [--classifier-timeout-s F]
        [--known-reds PATH] [--test-timeout-s N] [--cwd DIR]
    bytedigger-engine preflight --verify --phase red|green [--cwd DIR]

Exit codes: 0 every step ok; 1 a step is red (the receipt is written); 2 usage, spec or
git problem (no receipt is left behind). ``--verify`` prints one of
``fresh | stale | red | missing`` and exits 0 only for ``fresh``.

Spec front-matter (between a first ``---`` line and the next ``---`` line) carries the
keys ``red_tests``, ``sibling_tests``, ``paths``, ``red_pins`` and ``tier``; a value is a
block list, an inline ``[a, b]`` list or a scalar.

Steps, in fixed order (``STEPS``): syntax, tier, cite, stub, scoped, siblings, facts,
prescreen. After the first red step every later step is recorded as skipped. The first
seven fail closed: an exception inside one is a red step with detail
``internal error: ...``. The prescreen step is advisory: it is never red and never
changes ``ok``.

Receipt schema (``RECEIPT_SCHEMA`` = 1), a JSON document stored at
``<git dir>/bytedigger-preflight/receipt.json`` (per work tree, outside the tree, so
writing it never changes the state hash)::

    {
      "schema": 1,
      "phase": "red" | "green",
      "ok": true | false,            # no step is red
      "head": "<commit sha>",
      "base": "<base ref as given>",
      "state_hash": "<sha256 hex>",  # taken BEFORE any step ran
      "spec": "<absolute spec path>",
      "steps": [{"name", "status", "ms", "detail"}, ...],   # 8 entries, STEPS order
      "facts_path": "<path of facts.md>" | null,
      "prescreen": {"status", "label", "confidence", "ms"} | null,
      "ts": "<UTC ISO-8601>"
    }

Step status vocabulary: ok | red | skipped | off | error (off and error only on
prescreen). The state hash covers HEAD, the porcelain status, the staged and unstaged
binary diffs against HEAD, and a blob hash of every untracked non-ignored file; so any
edit, staging change, new or deleted untracked file or new commit changes it, while
ignored files do not. A host calls ``verify_receipt`` before spawning a gate or
reviewer: only ``fresh`` means the receipt is green and describes the tree as it is now.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Sequence, TypedDict, cast

from bytedigger_engine import (
    check_ladder,
    facts_pack,
    known_reds_ledger,
    spec_cite,
    stub_passability,
    tier_gate,
)
from bytedigger_engine.lib.util.engine_owned import drop_engine_owned, engine_owned_pathspecs

STEPS = ("syntax", "tier", "cite", "stub", "scoped", "siblings", "facts", "prescreen")
STEP_CODES = {
    "syntax": "E_PREFLIGHT_SYNTAX",
    "tier": "E_PREFLIGHT_TIER",
    "cite": "E_PREFLIGHT_CITE",
    "stub": "E_PREFLIGHT_STUB",
    "scoped": "E_PREFLIGHT_SCOPED",
    "siblings": "E_PREFLIGHT_SIBLINGS",
    "facts": "E_PREFLIGHT_FACTS",
    "prescreen": "E_PREFLIGHT_PRESCREEN",
}
RECEIPT_SCHEMA = 1
RECEIPT_DIRNAME = "bytedigger-preflight"

_CODE_SPEC_FIELDS = "E_PREFLIGHT_SPEC_FIELDS"
_CODE_GIT = "E_PREFLIGHT_GIT"
_CODE_USAGE = "E_PREFLIGHT_USAGE"

_PHASES = ("red", "green")
_FIELD_KEYS = ("red_tests", "sibling_tests", "paths", "red_pins", "tier")
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
_ITEM_RE = re.compile(r"^\s*-\s*(.*)$")
INVENTORY_LINT_TESTS = (
    "engine_py/tests/test_bd94_engine_owned_paths.py",
    "engine_py/tests/test_bd150_class_i_inventory.py",
    "engine_py/tests/test_bd152_output_digest.py",
    "engine_py/tests/test_bd206_class_m_sites.py",
    "engine_py/tests/test_bd89_p3c_deterministic_synthesize_report.py",
    "engine_py/tests/test_bd89_p3b1b_ii_aggregation_helper.py",
)
_JS_TEST_SUFFIXES = (".test.ts", ".test.tsx", ".test.js")
_JS_SYNTAX_SUFFIXES = (".ts", ".tsx", ".js")


class PreflightResult(TypedDict):
    exit_code: int
    receipt: dict[str, Any] | None
    error_code: str | None
    error: str | None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def _git(cwd: str | Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run ``git --no-optional-locks <args>``; non-zero exit or OS error raises RuntimeError."""
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=str(cwd), input=stdin, capture_output=True, check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"git {args[0] if args else ''}: {exc}") from exc
    if proc.returncode != 0:
        why = _first_line(proc.stderr.decode("utf-8", errors="replace"))
        raise RuntimeError(f"git {args[0] if args else ''} failed: {why}")
    return proc.stdout


def _git_text(cwd: str | Path, *args: str) -> str:
    return _git(cwd, *args).decode("utf-8", errors="replace").strip()


def _unquote(item: str) -> str:
    s = item.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


# --------------------------------------------------------------------------
# op1 parse_spec_fields
# --------------------------------------------------------------------------

def parse_spec_fields(text: str) -> dict[str, list[str]]:
    """Front-matter fields of a spec as lists; missing or malformed -> empty. Never raises."""
    out: dict[str, list[str]] = {k: [] for k in _FIELD_KEYS}
    try:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return out
        end = -1
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end = idx
                break
        if end < 0:
            return out
        front = lines[1:end]
        i = 0
        while i < len(front):
            m = _KEY_RE.match(front[i])
            i += 1
            if m is None:
                continue
            key, value = m.group(1), m.group(2).strip()
            if key not in out:
                continue
            if value.startswith("["):
                close = value.rfind("]")
                inner = value[1:close] if close > 0 else ""
                out[key] = [_unquote(p) for p in inner.split(",") if _unquote(p)]
            elif value == "":
                items: list[str] = []
                while i < len(front):
                    im = _ITEM_RE.match(front[i])
                    if im is not None:
                        item = _unquote(im.group(1))
                        if item:
                            items.append(item)
                        i += 1
                    elif front[i].strip() == "" or front[i].lstrip().startswith("#"):
                        i += 1
                    else:
                        break
                out[key] = items
            else:
                out[key] = [_unquote(value)] if _unquote(value) else []
    except Exception:  # noqa: BLE001 -- parse never raises
        return {k: [] for k in _FIELD_KEYS}
    return out


# --------------------------------------------------------------------------
# op2 / op3 state hash and receipt path
# --------------------------------------------------------------------------

def compute_state_hash(toplevel: str | Path) -> str:
    """sha256 over HEAD, status, both binary diffs and the untracked non-ignored blobs."""
    top = str(toplevel)
    digest = hashlib.sha256()
    excl = ("--", ".", *engine_owned_pathspecs())
    for args in (
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1", "--untracked-files=all", *excl),
        ("diff", "--binary", "HEAD", *excl),
        ("diff", "--binary", "--cached", "HEAD", *excl),
    ):
        digest.update(_git(top, *args))
        digest.update(b"\0")
    raw = _git(top, "ls-files", "-z", "--others", "--exclude-standard", *excl)
    names = sorted(n for n in raw.split(b"\0") if n)
    hashable: list[bytes] = []
    for name in names:
        if name.endswith(b"/"):
            continue
        if b"\n" in name:
            raise RuntimeError("untracked path contains a newline")
        hashable.append(name)
    blobs: dict[bytes, bytes] = {}
    if hashable:
        shas = _git(top, "hash-object", "--stdin-paths", stdin=b"\n".join(hashable) + b"\n").split()
        if len(shas) != len(hashable):
            raise RuntimeError("git hash-object returned an unexpected number of hashes")
        blobs = dict(zip(hashable, shas))
    for name in names:
        digest.update(blobs.get(name, b"dir") + b" " + name + b"\n")
    digest.update(b"\0")
    return digest.hexdigest()


def receipt_path(toplevel: str | Path) -> Path:
    git_dir = _git_text(toplevel, "rev-parse", "--absolute-git-dir")
    return Path(git_dir) / RECEIPT_DIRNAME / "receipt.json"


# --------------------------------------------------------------------------
# op5 verify_receipt
# --------------------------------------------------------------------------

def verify_receipt(phase: str, toplevel: str | Path) -> str:
    """One of fresh | stale | red | missing. Read-only; never raises."""
    try:
        doc = json.loads(receipt_path(toplevel).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- absent, unreadable, corrupt or non-git all mean missing
        return "missing"
    if not isinstance(doc, dict):
        return "missing"
    rec = cast(dict[str, Any], doc)
    if rec.get("schema") != RECEIPT_SCHEMA or rec.get("phase") != phase:
        return "missing"
    try:
        head = _git_text(toplevel, "rev-parse", "HEAD")
        current = compute_state_hash(toplevel)
    except Exception:  # noqa: BLE001 -- a hash that cannot be computed is not fresh
        return "stale"
    if rec.get("head") != head or rec.get("state_hash") != current:
        return "stale"
    if rec.get("ok") is not True:
        return "red"
    return "fresh"


def _first_red_step(top: str | Path) -> str | None:
    """Name of the first step with status red in the stored receipt, or None. Never raises."""
    try:
        doc = json.loads(receipt_path(top).read_text(encoding="utf-8"))
        steps = doc.get("steps") if isinstance(doc, dict) else None
        if not isinstance(steps, list):
            return None
        for step in cast(list[Any], steps):
            if isinstance(step, dict) and step.get("status") == "red":
                name = step.get("name")
                return name if isinstance(name, str) else None
    except Exception:  # noqa: BLE001 -- unreadable receipt carries no red step
        return None
    return None


def receipt_rung(phase: str, toplevel: str | Path) -> dict[str, Any]:
    """bd#218 s1: rung record {status, phase, red_step} for a gate step. Read-only; never raises.

    status is fresh | stale | red | missing | error. red_step is the first red step of the
    stored receipt (kept when the receipt is stale), None when missing, unreadable or ok.
    """
    try:
        top = _git_text(toplevel, "rev-parse", "--show-toplevel")
    except Exception:  # noqa: BLE001 -- not a git work tree: no receipt
        return {"status": "missing", "phase": phase, "red_step": None}
    try:
        status = verify_receipt(phase, top)
        red_step = _first_red_step(top) if status in ("stale", "red") else None
    except Exception:  # noqa: BLE001 -- the rung never raises
        return {"status": "error", "phase": phase, "red_step": None}
    return {"status": status, "phase": phase, "red_step": red_step}


# --------------------------------------------------------------------------
# JUnit reading and test running
# --------------------------------------------------------------------------

def _to_int(value: str | None) -> int:
    try:
        return int(value or "0")
    except ValueError:
        return 0


def _parse_junit(path: str) -> tuple[int, int, list[str]] | None:
    """(passed, failed, failed names) from a JUnit XML file, or None when unusable."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    passed = 0
    failed = 0
    names: list[str] = []
    for case in root.iter("testcase"):
        tags = {child.tag for child in case}
        if "failure" in tags or "error" in tags:
            failed += 1
            names.append(case.get("name") or "?")
        elif "skipped" in tags:
            continue
        else:
            passed += 1
    declared = 0
    for suite in root.iter("testsuite"):
        declared += _to_int(suite.get("errors")) + _to_int(suite.get("failures"))
    return passed, max(failed, declared), names


def _is_runnable(rel: str) -> bool:
    return rel.endswith(".py") or rel.endswith(_JS_TEST_SUFFIXES)


class _Run:
    """State of one preflight run; one method per step."""

    def __init__(
        self,
        *,
        top: Path,
        spec_abs: Path,
        spec_text: str,
        phase: str,
        fields: dict[str, list[str]],
        tier: str,
        head: str,
        merge_base: str,
        receipt_dir: Path,
        classifier_cmd: list[str] | None,
        classifier_timeout_s: float,
        known_reds: str | Path | None,
        test_timeout_s: float,
    ) -> None:
        self.top = top
        self.spec_abs = spec_abs
        self.spec_text = spec_text
        self.phase = phase
        self.fields = fields
        self.tier = tier
        self.head = head
        self.merge_base = merge_base
        self.receipt_dir = receipt_dir
        self.classifier_cmd = classifier_cmd
        self.classifier_timeout_s = classifier_timeout_s
        self.known_reds = known_reds
        self.test_timeout_s = test_timeout_s
        self.results: list[dict[str, Any]] = []
        self.facts_path: str | None = None
        self.facts_text = ""
        self.prescreen_info: dict[str, Any] | None = None
        self._tokens: list[str] | None = None

    def abs(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.top / p

    # -- helpers ----------------------------------------------------------

    def _changed(self) -> set[str]:
        diff = _git(self.top, "diff", "--name-only", "-z", "--diff-filter=d", self.merge_base)
        other = _git(self.top, "ls-files", "-z", "--others", "--exclude-standard")
        names = {
            n.decode("utf-8", errors="replace")
            for n in (diff.split(b"\0") + other.split(b"\0"))
            if n
        }
        kept = drop_engine_owned(sorted(names), self.top, step="preflight_changed", content_scan=True)
        return {n for n in kept if (self.top / n).is_file()}

    def _run_tests(self, rel: str) -> tuple[int, int, list[str]] | None:
        tmpdir = tempfile.mkdtemp(prefix="bd-preflight-")
        try:
            xml = os.path.join(tmpdir, "report.xml")
            target = str(self.abs(rel))
            if rel.endswith(".py"):
                cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       "--junitxml", xml, target]
            else:
                cmd = ["bun", "test", target, "--reporter=junit", "--reporter-outfile", xml]
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            try:
                subprocess.run(
                    cmd, cwd=str(self.top), env=env, capture_output=True,
                    timeout=self.test_timeout_s, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                return None
            return _parse_junit(xml)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _active_tokens(self) -> list[str]:
        if self._tokens is not None:
            return self._tokens
        path = self.abs(str(self.known_reds)) if self.known_reds else self.top / "known-reds.md"
        tokens: list[str] = []
        if path.is_file():
            rows = known_reds_ledger.parse_table_rows(path.read_text(encoding="utf-8", errors="replace"))
            active, _inactive = known_reds_ledger.partition_by_kill_by(
                rows, known_reds_ledger.resolve_today())
            for _lineno, cells in active:
                tokens.extend(known_reds_ledger.red_match_tokens(cells))
        self._tokens = tokens
        return tokens

    # -- steps ------------------------------------------------------------

    def step_syntax(self) -> tuple[str, str]:
        targets = self._changed()
        for rel in self.fields["red_tests"]:
            if self.abs(rel).is_file():
                try:
                    targets.add(str(self.abs(rel).relative_to(self.top)))
                except ValueError:
                    targets.add(str(self.abs(rel)))
        has_bun = shutil.which("bun") is not None
        checked = 0
        unchecked = 0
        failures: list[str] = []
        for rel in sorted(targets):
            path = self.abs(rel)
            err: str | None = None
            try:
                if rel.endswith(".py"):
                    checked += 1
                    compile(path.read_bytes(), str(path), "exec")
                elif rel.endswith(".sh"):
                    checked += 1
                    proc = subprocess.run(["bash", "-n", str(path)], capture_output=True,
                                          text=True, timeout=60, check=False)
                    if proc.returncode != 0:
                        err = _first_line(proc.stderr) or "bash -n failed"
                elif rel.endswith(".json"):
                    checked += 1
                    json.loads(path.read_text(encoding="utf-8"))
                elif rel.endswith(_JS_SYNTAX_SUFFIXES):
                    if not has_bun:
                        unchecked += 1
                    else:
                        checked += 1
                        out_dir = tempfile.mkdtemp(prefix="bd-preflight-js-")
                        try:
                            proc = subprocess.run(
                                ["bun", "build", "--no-bundle", str(path), "--outdir", out_dir],
                                capture_output=True, text=True, timeout=120, check=False)
                        finally:
                            shutil.rmtree(out_dir, ignore_errors=True)
                        if proc.returncode != 0:
                            err = _first_line(proc.stderr or proc.stdout) or "bun build failed"
            except Exception as exc:  # noqa: BLE001 -- any parse failure is a syntax finding
                err = _first_line(str(exc)) or type(exc).__name__
            if err is not None:
                failures.append(f"syntax error in {rel}: {err}")
        if failures:
            return "red", "; ".join(failures)
        detail = f"{checked} file(s) checked"
        if unchecked:
            detail += f", {unchecked} unchecked (no bun)"
        return "ok", detail

    def step_tier(self) -> tuple[str, str]:
        if self.tier != "MICRO":
            return "ok", f"skip {self.tier or 'none'}"
        paths = [str(self.abs(p)) for p in self.fields["paths"]]
        result = tier_gate.lint_paths(paths, "MICRO")
        if result.error is not None:
            return "red", f"tier lint error: {_first_line(result.error)}"
        if result.findings:
            named = ", ".join(f.path for f in result.findings)
            return "red", f"MICRO tier on engine production path(s): {named}"
        return "ok", "MICRO, no engine production path"

    def step_cite(self) -> tuple[str, str]:
        spec_cite._REPO_INDEX_CACHE.pop(str(self.top.resolve()), None)
        _code, findings = spec_cite.lint_spec(self.spec_abs, self.top)
        blocking = [f for f in findings if f.status in spec_cite.BLOCKING_STATUSES]
        if blocking:
            listed = "; ".join(f"{f.status} {f.file} {f.symbol}" for f in blocking[:5])
            return "red", listed
        return "ok", f"{len(findings)} citation(s) checked"

    def step_stub(self) -> tuple[str, str]:
        files = [r for r in self.fields["red_tests"] if r.endswith(".py") and self.abs(r).is_file()]
        if not files:
            return "ok", "no py"
        problems: list[str] = []
        for rel in files:
            res = stub_passability.lint_red_file(str(self.abs(rel)))
            if res.error is not None:
                problems.append(f"{rel}: lint error: {_first_line(res.error)}")
            elif res.findings:
                syms = ", ".join(f.symbol for f in res.findings)
                problems.append(f"{rel}: mocks its own unit under test ({syms})")
        if problems:
            return "red", "; ".join(problems)
        return "ok", f"{len(files)} file(s) clean"

    def step_scoped(self) -> tuple[str, str]:
        problems: list[str] = []
        oks: list[str] = []
        for rel in self.fields["red_tests"]:
            if not _is_runnable(rel):
                continue
            res = self._run_tests(rel)
            if res is None:
                problems.append(f"{rel}: no test report")
                continue
            passed, failed, names = res
            if self.phase == "red":
                if passed > 0:
                    problems.append(f"{rel}: {passed} passing test(s) in red phase")
                elif failed == 0:
                    problems.append(f"{rel}: no failing test (vacuous RED)")
                else:
                    oks.append(f"{rel}: {passed} pass, {failed} fail")
            elif failed > 0:
                problems.append(f"{rel}: {failed} failing ({', '.join(names[:3])})")
            else:
                oks.append(f"{rel}: {passed} pass, {failed} fail")
        if problems:
            return "red", "; ".join(problems)
        return "ok", "; ".join(oks) or "no runnable test files"

    def step_siblings(self) -> tuple[str, str]:
        pins = set(self.fields["red_pins"])
        problems: list[str] = []
        notes: list[str] = []
        ran = 0
        rels = list(self.fields["sibling_tests"])
        added = 0
        if any(
            p.startswith("engine_py/bytedigger_engine/") and p.endswith(".py")
            for p in self._changed()
        ):
            have = {os.path.normpath(r) for r in rels}
            for lint in INVENTORY_LINT_TESTS:
                if os.path.normpath(lint) in have or not self.abs(lint).is_file():
                    continue
                rels.append(lint)
                have.add(os.path.normpath(lint))
                added += 1
        for rel in rels:
            if not _is_runnable(rel):
                continue
            ran += 1
            pinned = self.phase == "red" and rel in pins
            res = self._run_tests(rel)
            if res is None:
                if pinned:
                    notes.append(f"pinned: {rel}")
                else:
                    problems.append(f"{rel}: no test report")
                continue
            passed, failed, names = res
            if failed == 0:
                notes.append(f"{rel}: {passed} pass, 0 fail")
                continue
            if pinned:
                notes.append(f"pinned: {rel}")
                continue
            tokens = self._active_tokens()
            untolerated = [n for n in names if not any(t in f"{rel}::{n}" for t in tokens)]
            if failed > len(names):
                untolerated.append("<collection>")
            if untolerated:
                problems.append(f"{rel}: {len(untolerated)} failing ({', '.join(untolerated[:3])})")
            else:
                notes.append(f"{rel}: {failed} failing, tolerated by known-reds")
        if problems:
            return "red", "; ".join(problems)
        if ran == 0:
            return "ok", "no siblings"
        detail = "; ".join(notes)
        if added:
            detail += f"; inventory-lint: {added} file(s)"
        return "ok", detail

    def step_facts(self) -> tuple[str, str]:
        try:
            branch = _git_text(self.top, "branch", "--show-current")
        except RuntimeError:
            branch = ""
        header = [
            f"phase: {self.phase}",
            f"branch: {branch or 'unknown'}",
            f"HEAD: {self.head}",
            f"tier: {self.tier or 'none'}",
        ]
        header += [f"{r['name']}: {r['status']} {r['detail']}" for r in self.results]
        body = facts_pack.render(facts_pack.collect(self.top, self.spec_text), self.phase)
        text = "\n".join(header) + "\n\n" + body + "\n"
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        path = self.receipt_dir / "facts.md"
        path.write_text(text, encoding="utf-8")
        self.facts_path = str(path)
        self.facts_text = text
        return "ok", str(path)

    def step_prescreen(self) -> tuple[str, str]:
        if self.phase == "green":
            return "off", "skip green"
        if self.classifier_cmd is None:
            return "off", "no classifier"
        try:
            verdict = check_ladder.prescreen(
                findings=[],
                gate_input=self.facts_text,
                classifier_cmd=self.classifier_cmd,
                mode="shadow",
                timeout_s=self.classifier_timeout_s,
            )
            clf = cast(dict[str, Any], verdict["classifier"])
            status = str(clf.get("status"))
            self.prescreen_info = {
                "status": status,
                "label": clf.get("label"),
                "confidence": clf.get("confidence"),
                "ms": clf.get("ms"),
            }
            if status == "ok":
                return "ok", f"label={clf.get('label')} confidence={clf.get('confidence')}"
            return "error", status
        except Exception as exc:  # noqa: BLE001 -- advisory step: never red
            self.prescreen_info = None
            return "error", f"internal error: {_first_line(str(exc)) or type(exc).__name__}"


# --------------------------------------------------------------------------
# op4 run_preflight
# --------------------------------------------------------------------------

def _failure(code: str, message: str) -> PreflightResult:
    return {"exit_code": 2, "receipt": None, "error_code": code, "error": message}


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".receipt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run_preflight(
    spec: str | Path,
    phase: str,
    *,
    cwd: str | Path = ".",
    base: str = "origin/main",
    tier: str | None = None,
    classifier_cmd: list[str] | None = None,
    classifier_timeout_s: float = check_ladder.DEFAULT_TIMEOUT_S,
    known_reds: str | Path | None = None,
    test_timeout_s: float = 600,
) -> PreflightResult:
    """Run the fixed step list, write the receipt, return the result (see module docstring)."""
    cwd_abs = os.path.abspath(str(cwd))
    try:
        top = Path(_git_text(cwd_abs, "rev-parse", "--show-toplevel"))
        receipt_file = receipt_path(top)
    except RuntimeError as exc:
        return _failure(_CODE_GIT, str(exc))
    receipt_dir = receipt_file.parent
    for stale in (receipt_file, receipt_dir / "facts.md"):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return _failure(_CODE_GIT, f"cannot remove old receipt file: {exc}")

    if phase not in _PHASES:
        return _failure(_CODE_USAGE, f"phase must be one of {', '.join(_PHASES)}, got {phase!r}")
    try:
        head = _git_text(top, "rev-parse", "HEAD")
        merge_base = _git_text(top, "merge-base", base, "HEAD")
    except RuntimeError as exc:
        return _failure(_CODE_GIT, str(exc))

    spec_abs = Path(spec) if Path(spec).is_absolute() else Path(cwd_abs) / spec
    try:
        spec_text = spec_abs.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _failure(_CODE_SPEC_FIELDS, f"spec unreadable: {exc}")
    fields = parse_spec_fields(spec_text)
    if not fields["red_tests"] or not fields["paths"]:
        return _failure(_CODE_SPEC_FIELDS, "spec front-matter needs non-empty red_tests and paths")

    try:
        state_hash = compute_state_hash(top)
    except RuntimeError as exc:
        return _failure(_CODE_GIT, str(exc))

    tier_eff = (tier or (fields["tier"][0] if fields["tier"] else "")).strip().upper()
    run = _Run(
        top=top, spec_abs=spec_abs, spec_text=spec_text, phase=phase, fields=fields,
        tier=tier_eff, head=head, merge_base=merge_base, receipt_dir=receipt_dir,
        classifier_cmd=classifier_cmd, classifier_timeout_s=classifier_timeout_s,
        known_reds=known_reds, test_timeout_s=test_timeout_s,
    )
    return _run_steps_and_write(
        run, STEPS, receipt_file=receipt_file, state_hash=state_hash, base=base,
        spec_value=str(spec_abs), extra=None,
    )


def _run_steps_and_write(
    run: _Run,
    names: Sequence[str],
    *,
    receipt_file: Path,
    state_hash: str,
    base: str,
    spec_value: str | None,
    extra: dict[str, Any] | None,
) -> PreflightResult:
    """Shared by run_preflight and run_engine_preflight: run ``names`` in order, write the receipt."""
    steps: dict[str, Callable[[], tuple[str, str]]] = {
        "syntax": run.step_syntax,
        "tier": run.step_tier,
        "cite": run.step_cite,
        "stub": run.step_stub,
        "scoped": run.step_scoped,
        "siblings": run.step_siblings,
        "facts": run.step_facts,
        "prescreen": run.step_prescreen,
    }
    first_red: str | None = None
    first_red_detail = ""
    for name in names:
        if first_red is not None:
            run.results.append({"name": name, "status": "skipped", "ms": 0, "detail": ""})
            continue
        started = time.monotonic()
        try:
            status, detail = steps[name]()
        except Exception as exc:  # noqa: BLE001 -- fail closed
            msg = _first_line(str(exc)) or type(exc).__name__
            status = "error" if name == "prescreen" else "red"
            detail = f"internal error: {msg}"
        ms = max(0, int((time.monotonic() - started) * 1000))
        run.results.append({"name": name, "status": status, "ms": ms, "detail": detail})
        if status == "red":
            first_red = name
            first_red_detail = detail

    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "phase": run.phase,
        "ok": first_red is None,
        "head": run.head,
        "base": base,
        "state_hash": state_hash,
        "spec": spec_value,
        "steps": run.results,
        "facts_path": run.facts_path,
        "prescreen": run.prescreen_info,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if extra:
        receipt.update(extra)
    try:
        _write_atomic(receipt_file, json.dumps(receipt, indent=2))
    except OSError as exc:
        return _failure(_CODE_GIT, f"cannot write receipt: {exc}")
    if first_red is None:
        return {"exit_code": 0, "receipt": receipt, "error_code": None, "error": None}
    return {
        "exit_code": 1,
        "receipt": receipt,
        "error_code": STEP_CODES[first_red],
        "error": _first_line(first_red_detail),
    }


# --------------------------------------------------------------------------
# bd#218 s2 run_engine_preflight
# --------------------------------------------------------------------------

_ENGINE_STEPS = ("syntax", "stub", "facts")
_ENGINE_NOT_RUN = ["cite", "tier", "scoped", "siblings", "prescreen"]


def run_engine_preflight(
    top: str | Path,
    red_tests: Any,
    spec_text: str,
    *,
    base: str | None = None,
) -> PreflightResult:
    """bd#218 s2: run syntax, stub and facts on explicit fields and write the phase-red receipt.

    No test run, no LLM. Never raises; a failure result leaves no receipt file behind.
    """
    receipt_file: Path | None = None
    try:
        cwd_abs = os.path.abspath(str(top))
        try:
            toplevel = Path(_git_text(cwd_abs, "rev-parse", "--show-toplevel"))
            receipt_file = receipt_path(toplevel)
        except RuntimeError as exc:
            return _failure(_CODE_GIT, str(exc))
        receipt_dir = receipt_file.parent
        for stale in (receipt_file, receipt_dir / "facts.md"):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                return _failure(_CODE_GIT, f"cannot remove old receipt file: {exc}")

        if not isinstance(red_tests, list) or not red_tests:
            return _failure(_CODE_SPEC_FIELDS, "red_tests must be a non-empty list")
        top_real = Path(os.path.realpath(str(toplevel)))
        rebased: list[str] = []
        for entry in cast(list[Any], red_tests):
            if not isinstance(entry, str) or not entry:
                return _failure(_CODE_SPEC_FIELDS, "red_tests entry is not a path string")
            given = Path(entry)
            real = Path(os.path.realpath(str(given if given.is_absolute() else Path(cwd_abs) / given)))
            if not real.is_file():
                return _failure(_CODE_SPEC_FIELDS, f"red test is not a file: {entry}")
            try:
                rebased.append(str(real.relative_to(top_real)))
            except ValueError:
                rebased.append(str(real))

        base_used: str | None = None
        merge_base = ""
        candidates = [base] if base else ["origin/main", "main", "HEAD"]
        for cand in candidates:
            try:
                _git(toplevel, "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}")
                merge_base = _git_text(toplevel, "merge-base", cand, "HEAD")
            except RuntimeError:
                continue
            base_used = cand
            break
        if base_used is None:
            return _failure(_CODE_GIT, "no base ref resolvable")
        head = _git_text(toplevel, "rev-parse", "HEAD")
        state_hash = compute_state_hash(toplevel)

        fields: dict[str, list[str]] = {k: [] for k in _FIELD_KEYS}
        fields["red_tests"] = rebased
        run = _Run(
            top=toplevel, spec_abs=Path(""), spec_text=spec_text if isinstance(spec_text, str) else "",
            phase="red", fields=fields, tier="", head=head, merge_base=merge_base,
            receipt_dir=receipt_dir, classifier_cmd=None,
            classifier_timeout_s=float(check_ladder.DEFAULT_TIMEOUT_S),
            known_reds=None, test_timeout_s=600,
        )
        return _run_steps_and_write(
            run, _ENGINE_STEPS, receipt_file=receipt_file, state_hash=state_hash, base=base_used,
            spec_value=None, extra={"producer": "engine", "not_run": list(_ENGINE_NOT_RUN)},
        )
    except Exception as exc:  # noqa: BLE001 -- the producer never raises
        if receipt_file is not None:
            try:
                receipt_file.unlink()
            except OSError:
                pass
        return _failure(_CODE_GIT, _first_line(str(exc)) or type(exc).__name__)


# --------------------------------------------------------------------------
# op6 CLI
# --------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bytedigger-engine preflight")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--spec", help="path of the spec whose front-matter drives the run")
    group.add_argument("--verify", action="store_true",
                       help="print fresh|stale|red|missing for the stored receipt")
    p.add_argument("--phase", required=True, help="red or green")
    p.add_argument("--json", dest="as_json", action="store_true", help="emit one JSON document")
    p.add_argument("--base", default="origin/main", help="base ref for the merge-base")
    p.add_argument("--tier", default=None, help="tier override")
    p.add_argument("--classifier-cmd", dest="classifier_cmd", default=None,
                   help="classifier argv as a JSON list of strings")
    p.add_argument("--classifier-timeout-s", dest="classifier_timeout_s", type=float,
                   default=float(check_ladder.DEFAULT_TIMEOUT_S))
    p.add_argument("--known-reds", dest="known_reds", default=None, help="known-reds ledger path")
    p.add_argument("--test-timeout-s", dest="test_timeout_s", type=float, default=600.0)
    p.add_argument("--cwd", default=".", help="directory inside the work tree")
    return p


def _emit_error(as_json: bool, code: str, message: str) -> None:
    if as_json:
        print(json.dumps({"ok": False, "error_code": code, "error": message}))
    else:
        sys.stderr.write(f"preflight: error {code} {message}\n")


def _parse_classifier_cmd(raw: str) -> list[str] | None:
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(doc, list) or not doc:
        return None
    if not all(isinstance(x, str) for x in doc):
        return None
    return [str(x) for x in doc]


def preflight_main(argv: Sequence[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _build_parser().parse_args(args_in)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 2)

    classifier_cmd: list[str] | None = None
    if args.classifier_cmd is not None:
        classifier_cmd = _parse_classifier_cmd(args.classifier_cmd)
        if classifier_cmd is None:
            _emit_error(args.as_json, _CODE_USAGE,
                        "--classifier-cmd must be a non-empty JSON list of strings")
            return 2

    if args.verify:
        try:
            top = _git_text(os.path.abspath(args.cwd), "rev-parse", "--show-toplevel")
        except RuntimeError:
            print("missing")
            return 1
        word = verify_receipt(args.phase, top)
        print(word)
        return 0 if word == "fresh" else 1

    result = run_preflight(
        args.spec, args.phase, cwd=args.cwd, base=args.base, tier=args.tier,
        classifier_cmd=classifier_cmd, classifier_timeout_s=args.classifier_timeout_s,
        known_reds=args.known_reds, test_timeout_s=args.test_timeout_s,
    )
    receipt = result["receipt"]
    if receipt is None:
        _emit_error(args.as_json, result["error_code"] or _CODE_USAGE, result["error"] or "")
        return result["exit_code"]
    if args.as_json:
        print(json.dumps(receipt))
    else:
        for step in receipt["steps"]:
            detail = str(step["detail"]).replace("\n", " ")
            print(f"{step['name']}: {step['status']} {detail}".rstrip())
        if result["exit_code"] == 0:
            print("preflight: ok")
        else:
            print(f"preflight: red {result['error_code']} {result['error']}")
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(preflight_main())
