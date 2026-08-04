#!/usr/bin/env python3
"""Verified-TDD loop on a toy repo -- keyless, no API required.

Drives the real engine (WorkflowEngine + append-only event log + replay)
through a spec -> RED -> lint -> verify-RED -> verdict -> GREEN -> verify
loop against toyrepo/. LLM output is scripted via the public backend seam
(register_backend), so the demo runs offline; the deterministic gates are
the real engine modules and actually decide the run:

  - scope_inverse       lints the frozen spec (files-in-scope needs an inverse)
  - stub_passability    rejects a RED test that mocks its own unit under test
  - subprocess unittest RED must FAIL before GREEN, PASS after

Two attempts share one event log. Attempt 1 submits a vacuous RED (patches
slugify itself) and dies at the lint. Attempt 2 submits an honest RED and
goes green. The final replay shows both runs, derived purely from the log.

Run from anywhere after `pip install -e engine_py`:

    python3 examples/verified-tdd-run/run_demo.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# When running from a checkout without pip install, put engine_py on the path.
_ENGINE = HERE.parents[1] / "engine_py"
if _ENGINE.is_dir():
    sys.path.insert(0, str(_ENGINE))

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition  # noqa: E402
from bytedigger_engine.derive_state import replay                                                      # noqa: E402
from bytedigger_engine.engine import WorkflowEngine                                                    # noqa: E402
from bytedigger_engine.event_log import EventLog                                                       # noqa: E402
from bytedigger_engine.event_sink import get_event_sink                                                # noqa: E402
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, register_backend, reset_backends   # noqa: E402
from bytedigger_engine.scope_inverse import scan_scope_inverse                                         # noqa: E402
from bytedigger_engine.stub_passability import scan_stub_passability                                   # noqa: E402

SPEC = (HERE / "spec.md").read_text(encoding="utf-8")

# Spec section 4 allowlist -- the only paths GREEN may touch.
FILES_IN_SCOPE = {"slugify.py", "test_slugify.py"}

# ---------------------------------------------------------------------------
# Canned LLM output. In a real run these come from your model; here they are
# scripted so the demo is reproducible and keyless. The vacuous RED is the
# classic reward-hack: it patches slugify itself, so it would pass with no
# implementation at all.
# ---------------------------------------------------------------------------

VACUOUS_RED = '''\
import unittest
from unittest.mock import patch

from slugify import slugify


class TestSlugify(unittest.TestCase):
    def test_lowercases_and_hyphenates(self):
        with patch("slugify.slugify", return_value="hello-world") as fake:
            self.assertEqual(fake("Hello World"), "hello-world")


if __name__ == "__main__":
    unittest.main()
'''

HONEST_RED = '''\
import unittest

from slugify import slugify


class TestSlugify(unittest.TestCase):
    def test_lowercases_and_hyphenates(self):          # AC1
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_underscores_become_hyphens(self):         # AC2
        self.assertEqual(slugify("a_b c"), "a-b-c")

    def test_symbols_are_dropped(self):                # AC3
        self.assertEqual(slugify("a!@#b"), "ab")

    def test_hyphen_runs_collapse(self):               # AC4
        self.assertEqual(slugify("a  -  b"), "a-b")

    def test_edges_are_trimmed(self):                  # AC5
        self.assertEqual(slugify("  hi  "), "hi")

    def test_symbol_only_input_is_empty(self):         # AC6
        self.assertEqual(slugify("!!!"), "")


if __name__ == "__main__":
    unittest.main()
'''

GREEN_IMPL = '''\
"""URL slug helper. Written by the GREEN step against the frozen spec."""
import re


def slugify(text):
    text = text.lower()
    text = re.sub(r"[\\s_]+", "-", text)
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")
'''

VERDICT = "VERDICT: APPROVED -- RED covers AC1-AC6, no assertion weakening found."


class ScriptedBackend:
    """LLMBackend protocol implementation returning canned responses.

    `responses` maps step_name to the text a model would have produced.
    Swap this class for a real API client and nothing else changes.
    """

    def __init__(self):
        self.responses = {}

    def __call__(self, *, prompt, model, step_name, **kw):
        answer = self.responses[step_name]
        return StepResult(
            status="ok",
            data={"raw_response": answer, "response_bytes": len(answer)},
            duration_ms=0,
            step_name=step_name,
        )


def ask_llm(step_name):
    """Route one call through the engine's backend seam."""
    result = invoke_llm_subprocess(
        prompt=f"(see spec.md) produce output for step {step_name}",
        model="scripted-model",
        timeout_sec=30,
        step_name=step_name,
        backend="scripted-demo",
    )
    return result.data["raw_response"]


def run_unittest(worktree):
    """Run the toy repo's tests. Returns (returncode, tail_of_output)."""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "test_slugify", "-v"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        timeout=60,
    )
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:]
    return proc.returncode, (tail[0] if tail else "")


def ok(step, data=None):
    return StepResult(status="ok", data=data, duration_ms=0, step_name=step)


def fail(step, code, msg):
    return StepResult(status="error", data=None, duration_ms=0,
                      step_name=step, error=msg, error_code=code)


def build_workflow(worktree):
    """The demo pipeline as a real engine workflow, closed over one worktree."""

    def lint_spec(_ctx, _prev):
        findings = scan_scope_inverse(SPEC)
        if findings:
            return fail("lint_spec", "E_SPEC_SCOPE", findings[0]["reason"])
        print("  lint_spec            PASS  files-in-scope has its NOT-in-scope inverse")
        return ok("lint_spec")

    def write_red(_ctx, _prev):
        source = ask_llm("write_red")
        (worktree / "test_slugify.py").write_text(source, encoding="utf-8")
        print("  write_red            ok    test_slugify.py written by scripted backend")
        return ok("write_red", {"source": source})

    def lint_red(_ctx, prev):
        findings = scan_stub_passability(prev.data["source"], uut=["slugify"])
        if findings:
            f = findings[0]
            msg = (f"RED patches its own unit under test "
                   f"('{f.symbol}', import line {f.import_line}, patch line {f.patch_line})")
            print(f"  lint_red             REJECT  {msg}")
            return fail("lint_red", "E_RED_STUB_PASSABLE", msg)
        print("  lint_red             PASS  no self-mocking found")
        return ok("lint_red")

    def verify_red_fails(_ctx, _prev):
        rc, tail = run_unittest(worktree)
        if rc == 0:
            return fail("verify_red_fails", "E_RED_PASSES",
                        "RED passed before implementation -- vacuous test suite")
        print(f"  verify_red_fails     PASS  suite fails pre-implementation ({tail})")
        return ok("verify_red_fails")

    def validation_verdict(_ctx, _prev):
        verdict = ask_llm("validation_verdict")
        if "APPROVED" not in verdict:
            return fail("validation_verdict", "E_GATE_REJECT", verdict)
        print(f"  validation_verdict   PASS  {verdict}")
        return ok("validation_verdict")

    def write_green(_ctx, _prev):
        impl = ask_llm("write_green")
        target = "slugify.py"
        if target not in FILES_IN_SCOPE:
            return fail("write_green", "E_SCOPE_VIOLATION",
                        f"{target} is outside the spec allowlist")
        (worktree / target).write_text(impl, encoding="utf-8")
        print(f"  write_green          ok    {target} written (allowlist-checked)")
        return ok("write_green")

    def verify_green_passes(_ctx, _prev):
        rc, tail = run_unittest(worktree)
        if rc != 0:
            return fail("verify_green_passes", "E_GREEN_FAILS", tail)
        print(f"  verify_green_passes  PASS  ({tail})")
        return ok("verify_green_passes")

    steps = [lint_spec, write_red, lint_red, verify_red_fails,
             validation_verdict, write_green, verify_green_passes]
    return WorkflowDefinition(
        name="verified_tdd_demo",
        steps=[StepContract(name=s.__name__, execute=s) for s in steps],
    )


def make_ctx():
    return WorkflowContext(
        tenant_id="demo", scope=None, db_path=None, org_config=None,
        question="implement slugify per spec.md", session_id="demo-session",
        persona="demo", framework=None, domain=None,
    )


def main():
    backend = ScriptedBackend()
    register_backend("scripted-demo", backend,
                     manifest_source="orchestrator_observed", overwrite=True)

    log_path = Path(tempfile.mkdtemp(prefix="verified-tdd-demo-")) / "events.jsonl"
    print(f"event log: {log_path}\n")

    attempts = [
        ("attempt-1-vacuous-red", VACUOUS_RED,
         "attempt 1: the model submits a RED that mocks slugify itself"),
        ("attempt-2-honest-red", HONEST_RED,
         "attempt 2: honest RED against the real import"),
    ]

    for run_id, red_source, title in attempts:
        print(title)
        backend.responses = {
            "write_red": red_source,
            "validation_verdict": VERDICT,
            "write_green": GREEN_IMPL,
        }
        worktree = Path(tempfile.mkdtemp(prefix=f"toyrepo-{run_id}-"))
        for f in (HERE / "toyrepo").iterdir():
            shutil.copy(f, worktree / f.name)

        engine = WorkflowEngine(event_log=get_event_sink(str(log_path)))
        engine.register("verified_tdd_demo", build_workflow(worktree))
        result, _ctx = engine.execute("verified_tdd_demo", make_ctx(), run_id=run_id)
        print(f"  => run '{run_id}' finished: {result.status}"
              + (f" ({result.error_code}: {result.error})" if result.status != "ok" else ""))
        if result.status == "ok":
            print(f"  => implemented file: {worktree / 'slugify.py'}")
        print()

    print("replaying the event log (derived state, no other source of truth):")
    state = replay(EventLog(log_path).read_all())
    final_ok = True
    for run_id, run in sorted(state["runs"].items()):
        steps = ", ".join(s["step_name"] for s in run["steps"])
        print(f"  {run_id}: {run['status']}  [{steps}]")
        if run_id.startswith("attempt-2") and run["status"] != "ok":
            final_ok = False
    reset_backends()

    if not final_ok:
        return 1
    expected = {"attempt-1-vacuous-red": "error", "attempt-2-honest-red": "ok"}
    got = {rid: state["runs"][rid]["status"] for rid in expected if rid in state["runs"]}
    return 0 if got == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
