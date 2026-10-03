"""bd#218 step 1 RED -- preflight receipt rung in front of the validation gate + shadow reject-log evidence.

Spec: docs/decisions/2026-10-03-bd218-s1-preflight-rung.md (ACs AC1-AC17, spec r4).

New symbols (imported lazily so each test fails at assert/attribute time, not collection):
  preflight.receipt_rung, reject_stats.ladder_table, reject_log.record_validation_reject(preflight=...),
  phase_5_implement._invoke_validation_llm -> `preflight_receipt` event + extra_data["preflight"],
  phase_5_implement._log_validation_reject -> forwards prev.data["preflight"].

AC2 uses a REAL run_preflight writing a REAL receipt in a tmp git repo (no mocking of preflight /
verify_receipt). The phase-5 tests (AC3/AC7/AC8) also use real receipts; only the LLM call and the
event sink are replaced (never the unit under test). No singleton resource is contended
(workflows.md 1i): every state is pre-staged deterministically in its own tmp repo.
AC10 (bd141 incl. L16 / bd164 / other siblings pass unchanged; L16's harness has no git_cwd so it is
ambient-skipped, see AC14) is verified by running those files, not by a test here.
receipt_rung is patched via the `preflight.receipt_rung` module attribute (spec op2 binding).
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(180)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL_KNOWN_REDS_TODAY", raising=False)
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


CALC = "def add(a, b):\n    return a + b\n"
RED_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"
GREEN_SRC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
OK_BODY = "# spec\n\n`calc.py` defines `add`.\n"
BAD_CITE_BODY = "# spec\n\n`calc.py` defines `nonexistent_fn_zz`.\n"
STATUSES = ("fresh", "stale", "red", "missing")


def _pf() -> Any:
    return importlib.import_module("bytedigger_engine.preflight")


def _p5() -> Any:
    return importlib.import_module("bytedigger_engine.workflows.phase_5_implement")


def _g(res: Any, key: str) -> Any:
    return res[key] if isinstance(res, dict) else getattr(res, key)


# --------------------------------------------------------------------------
# Real temp git repo + real preflight receipts
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mk(tmp_path: Path, name: str, *, test_src: str = RED_SRC, body: str = OK_BODY) -> tuple[Path, Path]:
    """Temp repo (calc.py + tests) and a spec outside the tree. Returns (repo, spec)."""
    root = tmp_path / name
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "calc.py", CALC)
    _write(repo / "tests" / "test_calc.py", test_src)
    _write(repo / ".gitignore", "ignored/\n__pycache__/\n.pytest_cache/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    spec = root / "spec" / "spec.md"
    _write(spec, "---\nred_tests: [tests/test_calc.py]\npaths: [calc.py]\n---\n" + body)
    return repo.resolve(), spec


def _preflight(repo: Path, spec: Path, phase: str = "red") -> Any:
    return _pf().run_preflight(str(spec), phase, cwd=str(repo))


def _staged_repo(tmp_path: Path, state: str) -> Path:
    """Pre-stage a repo whose receipt state for phase 'red' is exactly `state`."""
    if state == "red":
        repo, spec = _mk(tmp_path, "r_red", body=BAD_CITE_BODY)
        assert _g(_preflight(repo, spec), "exit_code") == 1
        return repo
    repo, spec = _mk(tmp_path, f"r_{state}")
    if state == "missing":
        return repo
    assert _g(_preflight(repo, spec), "exit_code") == 0
    if state == "stale":
        (repo / "calc.py").write_text(CALC + "# edit\n", encoding="utf-8")
    return repo


# --------------------------------------------------------------------------
# AC1 receipt_rung contract
# --------------------------------------------------------------------------

def test_AC1_receipt_rung_shape_and_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1: five-status record; non-git / no receipt / corrupt -> missing; raising verify -> error."""
    pf = _pf()
    rung = pf.receipt_rung  # absent today -> AttributeError
    allowed = {"fresh", "stale", "red", "missing", "error"}

    plain = tmp_path / "plain"
    plain.mkdir()
    res = rung("red", str(plain))
    assert res == {"status": "missing", "phase": "red", "red_step": None}

    repo, spec = _mk(tmp_path, "ac1")
    assert rung("red", str(repo)) == {"status": "missing", "phase": "red", "red_step": None}
    assert _g(_preflight(repo, spec), "exit_code") == 0
    ok = rung("red", str(repo))
    assert set(ok) == {"status", "phase", "red_step"} and ok["status"] in allowed
    pf.receipt_path(repo).write_text("{not json", encoding="utf-8")
    assert rung("red", str(repo)) == {"status": "missing", "phase": "red", "red_step": None}

    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("verify exploded")

    monkeypatch.setattr(pf, "verify_receipt", boom)
    err = rung("red", str(repo))
    assert err["status"] == "error" and err["phase"] == "red"


# --------------------------------------------------------------------------
# AC2 production side effect (real preflight, real receipt)
# --------------------------------------------------------------------------

def test_AC2_real_receipt_fresh_stale_red_missing(tmp_path: Path) -> None:
    """AC2: real run_preflight receipt -> fresh; edit tracked -> stale; red preflight -> red; wrong phase -> missing."""
    pf = _pf()
    repo, spec = _mk(tmp_path, "ac2_a")
    assert _g(_preflight(repo, spec, "red"), "exit_code") == 0
    assert pf.receipt_path(repo).is_file()
    assert pf.receipt_rung("red", str(repo)) == {"status": "fresh", "phase": "red", "red_step": None}

    (repo / "calc.py").write_text(CALC + "# edit\n", encoding="utf-8")
    stale = pf.receipt_rung("red", str(repo))
    assert stale["status"] == "stale" and stale["red_step"] is None

    repo_b, spec_b = _mk(tmp_path, "ac2_b", body=BAD_CITE_BODY)
    assert _g(_preflight(repo_b, spec_b, "red"), "exit_code") == 1
    red = pf.receipt_rung("red", str(repo_b))
    assert red == {"status": "red", "phase": "red", "red_step": "cite"}

    repo_c, spec_c = _mk(tmp_path, "ac2_c", test_src=GREEN_SRC)
    assert _g(_preflight(repo_c, spec_c, "green"), "exit_code") == 0
    assert pf.verify_receipt("green", str(repo_c)) == "fresh"
    miss = pf.receipt_rung("red", str(repo_c))
    assert miss["status"] == "missing" and miss["red_step"] is None


# --------------------------------------------------------------------------
# Phase-5 harness (LLM call + event sink replaced; unit under test is real)
# --------------------------------------------------------------------------

def _ctx(**org_extra: Any) -> Any:
    from bytedigger_engine.contracts import WorkflowContext
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"complexity": "SIMPLE", **org_extra},
        question="bd218 s1", session_id="s", persona="hal",
        framework=None, domain=None,
    )


def _prev(tmp_path: Path, prompt: str = "PROMPT-218") -> Any:
    from bytedigger_engine.contracts import StepResult
    data = {
        "prompt": prompt, "doc_path": str(tmp_path / "d.md"),
        "spec_path": str(tmp_path / "s.md"), "red_log_path": str(tmp_path / "r.log"),
        "cycle": 1, "stable_prefix": "",
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="check_red_executable")


def _drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, _raw: str = "VERDICT: PASS\n",
           _prev_obj: Any = None, **org: Any):
    """Returns (result, llm_calls, events, order). `order` interleaves ('emit', type) and ('llm',)."""
    from bytedigger_engine.contracts import StepResult
    p5 = _p5()
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []
    order: list[tuple] = []

    def fake_llm(**kw: Any) -> Any:
        calls.append(kw)
        order.append(("llm",))
        return StepResult(status="ok", data={"raw_response": _raw, **kw["extra_data"]},
                          duration_ms=0, step_name="invoke_validation_llm")

    def fake_emit(event_type: str, payload: dict, severity: str = "info") -> None:
        events.append((event_type, payload))
        order.append(("emit", event_type))

    monkeypatch.setattr(p5, "invoke_llm_subprocess", fake_llm)
    monkeypatch.setattr(p5, "_emit_safe", fake_emit)
    # s2 added an engine-side receipt producer ahead of the rung; s1 tests pre-stage their own receipts.
    from bytedigger_engine import preflight as _pf
    monkeypatch.setattr(_pf, "run_engine_preflight", lambda *a, **k: None, raising=False)
    result = p5._invoke_validation_llm(_ctx(**org), _prev_obj if _prev_obj is not None else _prev(tmp_path))
    return result, calls, events, order


def _rung_events(events: list) -> list[dict]:
    return [p for (t, p) in events if t == "preflight_receipt"]


_BASE_EXTRA_KEYS = {"doc_path", "spec_path", "red_log_path", "red_test_paths", "cycle", "red_commit_sha"}


def _expected_kwargs(tmp_path: Path) -> dict:
    """Today's (pre-change) invoke_llm_subprocess kwargs for _prev(tmp_path) + SIMPLE config, spelled out."""
    p5 = _p5()
    prev = _prev(tmp_path)
    return {
        "prompt": "PROMPT-218",
        "model": p5._default_validation_model(),
        "timeout_sec": p5._resolve_validation_timeout_sec({"complexity": "SIMPLE"}),
        "step_name": "invoke_validation_llm",
        "extra_data": {
            "doc_path": str(tmp_path / "d.md"), "spec_path": str(tmp_path / "s.md"),
            "red_log_path": str(tmp_path / "r.log"), "red_test_paths": [], "cycle": 1,
            "red_commit_sha": None,
        },
        "hard_gate": True,
        "gate_label": "validation",
        "allowed_tools": ["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"],
        "stable_prefix": "",
        "injections": p5._declared_injections(prev.data),
    }


def _assert_passthrough(result: Any, call: dict) -> None:
    assert result.status == "ok" and result.step_name == "invoke_validation_llm"
    assert result.data == {"raw_response": "VERDICT: PASS\n", **call["extra_data"]}


# --------------------------------------------------------------------------
# AC3
# --------------------------------------------------------------------------

@pytest.mark.parametrize("state", STATUSES)
def test_AC3_default_config_emits_one_preflight_receipt_before_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """AC3: one preflight_receipt event (status/phase 5/cycle/gate) before the single gate call."""
    repo = _staged_repo(tmp_path, state)
    result, calls, events, order = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    evs = _rung_events(events)
    assert len(evs) == 1, f"exactly one preflight_receipt event, got {len(evs)}"
    ev = evs[0]
    assert ev["status"] == state
    assert ev["phase"] == 5 and ev["cycle"] == 1 and ev["gate"] == "validation"
    want_step = "cite" if state == "red" else None
    assert ev["red_step"] == want_step
    assert len(calls) == 1, "gate runs exactly once whatever the rung says"
    # Record is exactly {status, red_step} (it rides in every reject row, 4096-byte cap).
    assert calls[0]["extra_data"]["preflight"] == {"status": state, "red_step": want_step}
    assert order.index(("emit", "preflight_receipt")) < order.index(("llm",))
    _assert_passthrough(result, calls[0])


# --------------------------------------------------------------------------
# AC4
# --------------------------------------------------------------------------

def test_AC4_receipt_rung_raising_emits_error_and_gate_still_runs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: receipt_rung raising -> event status 'error', gate called once, result unchanged."""
    pf = _pf()

    def boom(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("rung exploded")

    monkeypatch.setattr(pf, "receipt_rung", boom)             # absent today -> AttributeError
    result, calls, events, _order = _drive(monkeypatch, tmp_path, git_cwd=str(tmp_path))
    evs = _rung_events(events)
    assert len(evs) == 1 and evs[0]["status"] == "error"
    assert calls[0]["extra_data"]["preflight"] == {"status": "error", "red_step": None}
    assert len(calls) == 1
    _assert_passthrough(result, calls[0])


# --------------------------------------------------------------------------
# AC5
# --------------------------------------------------------------------------

def test_AC5_mode_off_is_todays_behaviour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: {"mode":"off"} -> no event, receipt_rung not called, extra_data has no preflight key."""
    pf = _pf()
    seen: list[tuple] = []

    def spy(*a: Any, **k: Any) -> dict:
        seen.append((a, k))
        return {"status": "fresh", "phase": "red", "red_step": None}

    monkeypatch.setattr(pf, "receipt_rung", spy)              # absent today -> AttributeError
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(tmp_path),
                                       preflight_rung={"mode": "off"})
    assert not _rung_events(events) and not seen
    assert len(calls) == 1
    assert calls[0] == _expected_kwargs(tmp_path), "off mode: full kwargs equal today's"
    _assert_passthrough(result, calls[0])
    # Forcing: the same run in default mode does call the rung and emits.
    seen.clear()
    _r2, calls2, events2, _o2 = _drive(monkeypatch, tmp_path, git_cwd=str(tmp_path))
    assert len(seen) == 1 and len(_rung_events(events2)) == 1
    assert calls2[0]["extra_data"]["preflight"] == {"status": "fresh", "red_step": None}


# --------------------------------------------------------------------------
# AC6
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cfg", [{"mode": "bogus"}, {"mode": 7}, "x", 5, []],
                         ids=["unknown-str", "mode-int", "str", "int", "list"])
def test_AC6_bad_config_emits_config_error_gate_runs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: Any) -> None:
    """AC6: unknown mode / non-dict config -> event status 'config-error', gate called once."""
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(tmp_path), preflight_rung=cfg)
    evs = _rung_events(events)
    assert len(evs) == 1 and evs[0]["status"] == "config-error"
    assert len(calls) == 1
    _assert_passthrough(result, calls[0])


# --------------------------------------------------------------------------
# AC7
# --------------------------------------------------------------------------

def test_AC7_rung_starts_no_llm_and_no_classifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC7: classifier_cmd unset -> the only LLM call is the gate's; prescreen never invoked."""
    cl = importlib.import_module("bytedigger_engine.check_ladder")
    invoked: list[int] = []

    def no_prescreen(*_a: Any, **_k: Any) -> Any:
        invoked.append(1)
        raise AssertionError("classifier must not run")

    monkeypatch.setattr(cl, "prescreen", no_prescreen)
    repo = _staged_repo(tmp_path, "fresh")
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    assert len(_rung_events(events)) == 1, "rung must run (forcing)"
    assert len(calls) == 1 and calls[0]["gate_label"] == "validation"
    assert not invoked
    assert not [t for (t, _p) in events if t == "prescreen_verdict"]


# --------------------------------------------------------------------------
# AC8
# --------------------------------------------------------------------------

def test_AC8_shadow_and_rung_events_precede_gate_and_gate_not_skipped(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC8: with classifier_cmd set, prescreen_verdict and preflight_receipt both precede the gate call."""
    script = tmp_path / "clf.py"
    script.write_text(
        "import sys, json\nsys.stdin.read()\n"
        "print(json.dumps({'label': 'reject', 'confidence': 1.0, 'reasons': ['x']}))\n",
        encoding="utf-8")
    repo = _staged_repo(tmp_path, "fresh")
    cfg = {"classifier_cmd": [sys.executable, str(script)], "timeout_s": 20}
    _r, calls, events, order = _drive(monkeypatch, tmp_path, git_cwd=str(repo), prescreen=cfg)
    assert len(_rung_events(events)) == 1
    assert len([t for (t, _p) in events if t == "prescreen_verdict"]) == 1
    llm_at = order.index(("llm",))
    assert order.index(("emit", "prescreen_verdict")) < llm_at
    assert order.index(("emit", "preflight_receipt")) < llm_at
    assert len(calls) == 1, "shadow reject verdict must not skip the gate"


# --------------------------------------------------------------------------
# AC9a / AC9b reject-log row
# --------------------------------------------------------------------------

def _rows(log: Path) -> list[dict]:
    if not log.is_file():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _with_run(fn: Any) -> Any:
    from bytedigger_engine import telemetry_ctx
    telemetry_ctx.clear_current_run()
    telemetry_ctx.set_current_run(event_log=None, run_id="bd218-run", step_name="gate_on_validation")
    try:
        return fn()
    finally:
        telemetry_ctx.clear_current_run()


def test_AC9a_record_validation_reject_preflight_in_detail(tmp_path: Path) -> None:
    """AC9a: preflight=... lands in detail.preflight; absent -> null and every other field equals today's row."""
    rl = importlib.import_module("bytedigger_engine.reject_log")
    with_pf, without = tmp_path / "with.jsonl", tmp_path / "without.jsonl"
    pf_rec = {"status": "fresh", "red_step": None}
    raw = "## Quality Findings\n- some finding\n"

    _with_run(lambda: rl.record_validation_reject("FAIL", 2, raw, path=without))
    _with_run(lambda: rl.record_validation_reject("FAIL", 2, raw, path=with_pf, preflight=pf_rec))
    (a,), (b,) = _rows(without), _rows(with_pf)
    assert b["detail"]["preflight"] == pf_rec
    assert "preflight" in a["detail"] and a["detail"]["preflight"] is None
    for row in (a, b):
        row["detail"] = {k: v for k, v in row["detail"].items() if k != "preflight"}
        row.pop("ts")
    assert a == b


def _gate_prev(preflight: Any = "ABSENT") -> Any:
    from bytedigger_engine.contracts import StepResult
    p5 = _p5()
    data: dict[str, Any] = {
        "verdict": "FAIL", "cycle": p5.MAX_VALIDATION_CYCLES,
        "validation_doc_path": "/tmp/v.md", "spec_path": "/tmp/s.md", "red_log_path": "/tmp/r.log",
        "validation_raw": "## Quality Findings\n- a finding\n", "red_commit_sha": "deadbeef",
        "red_test_paths": [],
    }
    if preflight != "ABSENT":
        data["preflight"] = preflight
    return StepResult(status="ok", data=data, duration_ms=0, step_name="write_validation_doc")


def test_AC9b_gate_forwards_prev_preflight_into_reject_row(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC9b: rejected-gate path writes detail.preflight from prev.data; missing -> null; log failure harmless."""
    p5 = _p5()
    rl = importlib.import_module("bytedigger_engine.reject_log")
    log = tmp_path / "rr.jsonl"
    monkeypatch.setenv("HAL_REJECT_LOG", str(log))
    rec = {"status": "red", "red_step": "cite"}

    res = _with_run(lambda: p5._gate_on_validation(None, _gate_prev(rec)))
    assert res.status == "error" and res.error_code == "E_VALIDATION_FAILED"
    res2 = _with_run(lambda: p5._gate_on_validation(None, _gate_prev()))
    assert res2.error_code == "E_VALIDATION_FAILED"
    rows = _rows(log)
    assert len(rows) == 2
    assert rows[0]["detail"]["preflight"] == rec
    assert "preflight" in rows[1]["detail"] and rows[1]["detail"]["preflight"] is None

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(rl, "record_validation_reject", boom)
    res3 = _with_run(lambda: p5._gate_on_validation(None, _gate_prev(rec)))
    assert (res3.status, res3.error_code) == (res.status, res.error_code)


# --------------------------------------------------------------------------
# AC9c ladder_table
# --------------------------------------------------------------------------

def _row(reason: str, pf: Any, head: str | None = None, phase: str = "phase_5_implement") -> dict:
    detail: dict[str, Any] = {"cycle": 1, "verdict": "FAIL", "preflight": pf,
                              "findings_head": [head] if head else []}
    return {"ts": "2026-10-03T10:00:00Z", "build_id": "b", "phase": phase,
            "reason_code": reason, "axes": [], "detail": detail}


def test_AC9c_ladder_table_groups_counts_and_caps_heads() -> None:
    """AC9c: groups by (preflight.status|none, red_step), sorted by rejects desc, heads capped at 3, noise ignored.

    Reading rule (spec 2): only fresh/red carry information; stale/missing/none are "no evidence". That is
    informational for the reader of the table -- ladder_table itself still counts every group, so the counts
    and caps below are unchanged.
    """
    rs = importlib.import_module("bytedigger_engine.reject_stats")
    fresh = {"status": "fresh", "red_step": None}
    red_cite = {"status": "red", "red_step": "cite"}
    stale = {"status": "stale", "red_step": None}
    missing = {"status": "missing", "red_step": None}
    rows: list[Any] = (
        [_row("VALIDATION_FAILED", fresh, f"h{i}") for i in range(1, 6)]          # 5
        + [_row("VALIDATION_AC_GAP", red_cite, f"c{i}") for i in range(1, 5)]    # 4
        + [_row("VALIDATION_SPEC_DEFECT", stale) for _ in range(3)]               # 3
        + [_row("VALIDATION_UNKNOWN", missing) for _ in range(2)]                 # 2
        + [_row("VALIDATION_FAILED", None)]                                       # 1 -> status "none"
        # ignored: non-validation reason / other phase
        + [_row("PLAN_REVIEW_REVISE", fresh) for _ in range(9)]
        + [_row("VALIDATION_FAILED", fresh, phase="phase_6_review") for _ in range(9)]
        + [_row("SATISFACTION_FAIL", red_cite, phase="phase_6_review") for _ in range(9)]
        # malformed
        + [None, "junk", 5, {}, {"phase": "phase_5_implement"}, {"reason_code": "VALIDATION_FAILED"}]
    )
    table = rs.ladder_table(rows)
    assert [(g["status"], g["red_step"], g["rejects"]) for g in table] == [
        ("fresh", None, 5), ("red", "cite", 4), ("stale", None, 3), ("missing", None, 2), ("none", None, 1),
    ]
    for g in table:
        assert set(g) == {"status", "red_step", "rejects", "informative", "findings_heads"}
        assert g["informative"] is (g["status"] in ("fresh", "red"))
        assert len(g["findings_heads"]) <= 3
    heads = {(g["status"], g["red_step"]): g["findings_heads"] for g in table}
    assert len(heads[("fresh", None)]) == 3 and set(heads[("fresh", None)]) <= {f"h{i}" for i in range(1, 6)}
    assert len(heads[("red", "cite")]) == 3 and set(heads[("red", "cite")]) <= {f"c{i}" for i in range(1, 5)}
    assert heads[("stale", None)] == []
    assert rs.ladder_table([]) == []


# --------------------------------------------------------------------------
# AC11 add-only: default-mode kwargs minus extra_data["preflight"] == off-mode kwargs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["fresh", "red"])
def test_AC11_default_kwargs_minus_record_equal_off_kwargs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """AC11: the rung changes nothing the gate sees except the added extra_data["preflight"] key."""
    repo = _staged_repo(tmp_path, state)
    _r, on_calls, on_events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo))
    _r2, off_calls, off_events, _o2 = _drive(monkeypatch, tmp_path, git_cwd=str(repo),
                                             preflight_rung={"mode": "off"})
    assert len(on_calls) == 1 and len(off_calls) == 1, "gate once in both modes"
    assert len(_rung_events(on_events)) == 1 and not _rung_events(off_events)
    on = dict(on_calls[0])
    on_extra = dict(on["extra_data"])
    assert on_extra.pop("preflight")["status"] == state, "forcing: the record must be present in default mode"
    on["extra_data"] = on_extra
    assert on == off_calls[0]
    assert off_calls[0] == _expected_kwargs(tmp_path)


# --------------------------------------------------------------------------
# AC12 reachability: invoke -> write_validation_doc -> verify citations -> gate -> reject row
# --------------------------------------------------------------------------

_FAIL_RAW = "## Quality Findings\n- a finding\n\nVERDICT: FAIL\n"


def _run_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **org: Any) -> tuple[Any, Any, Any, Any]:
    from bytedigger_engine.contracts import StepResult
    p5 = _p5()
    prev = StepResult(status="ok", duration_ms=0, step_name="check_red_executable", data={
        "prompt": "PROMPT-218", "doc_path": str(tmp_path / "out" / "v.md"),
        "spec_path": str(tmp_path / "s.md"), "red_log_path": str(tmp_path / "r.log"),
        "cycle": p5.MAX_VALIDATION_CYCLES, "stable_prefix": "",
    })
    (tmp_path / "s.md").write_text("# spec\n", encoding="utf-8")
    ctx = _ctx(**org)
    r1, _calls, _events, _o = _drive(monkeypatch, tmp_path, _raw=_FAIL_RAW, _prev_obj=prev, **org)
    r2 = p5._write_validation_doc(ctx, r1)
    r3 = p5._verify_validation_citations(ctx, r2)
    gate = _with_run(lambda: p5._gate_on_validation(ctx, r3))
    return r1, r2, r3, gate


def test_AC12_record_reaches_reject_row_through_real_step_chain(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC12: FAIL at cap -> real reject-log row has detail.preflight == the record; no record -> no key in steps."""
    log = tmp_path / "rr.jsonl"
    monkeypatch.setenv("HAL_REJECT_LOG", str(log))
    repo = _staged_repo(tmp_path, "red")
    rec = {"status": "red", "red_step": "cite"}
    r1, r2, r3, gate = _run_chain(monkeypatch, tmp_path, git_cwd=str(repo))
    assert r1.data["preflight"] == rec
    assert r2.data["preflight"] == rec, "_write_validation_doc must forward the record"
    assert r3.data["preflight"] == rec, "_verify_validation_citations must forward the record"
    assert gate.status == "error" and gate.error_code == "E_VALIDATION_FAILED"
    (row,) = _rows(log)
    assert row["detail"]["preflight"] == rec

    # No record (mode off): no 'preflight' key in any intermediate step, row carries null.
    log2 = tmp_path / "rr2.jsonl"
    monkeypatch.setenv("HAL_REJECT_LOG", str(log2))
    s1, s2, s3, gate2 = _run_chain(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung={"mode": "off"})
    assert "preflight" not in s1.data and "preflight" not in s2.data and "preflight" not in s3.data
    assert gate2.error_code == "E_VALIDATION_FAILED"
    (row2,) = _rows(log2)
    assert "preflight" in row2["detail"] and row2["detail"]["preflight"] is None


# --------------------------------------------------------------------------
# AC13 mode dispatch: verify / {} / None behave as default
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cfg", [{"mode": "verify"}, {}, None], ids=["verify", "empty-dict", "none"])
def test_AC13_verify_empty_and_none_behave_as_default(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: Any) -> None:
    """AC13: only {"mode":"off"} opts out; verify / {} / None run the rung."""
    repo = _staged_repo(tmp_path, "fresh")
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(repo), preflight_rung=cfg)
    evs = _rung_events(events)
    assert len(evs) == 1 and evs[0]["status"] == "fresh"
    assert len(calls) == 1
    assert calls[0]["extra_data"]["preflight"] == {"status": "fresh", "red_step": None}
    _assert_passthrough(result, calls[0])


# --------------------------------------------------------------------------
# AC14 ambient git cwd
# --------------------------------------------------------------------------

def test_AC14_ambient_cwd_skips_receipt_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC14: no git_cwd / current_worktree_path / scratchpad repo -> ambient-skip, receipt_rung never called."""
    pf = _pf()
    seen: list[tuple] = []

    def spy(*a: Any, **k: Any) -> dict:
        seen.append((a, k))
        return {"status": "fresh", "phase": "red", "red_step": None}

    monkeypatch.setattr(pf, "receipt_rung", spy)                  # absent today -> AttributeError
    result, calls, events, _o = _drive(monkeypatch, tmp_path)     # no git_cwd: ambient process cwd
    evs = _rung_events(events)
    assert len(evs) == 1 and evs[0]["status"] == "ambient-skip"
    assert evs[0]["phase"] == 5 and evs[0]["gate"] == "validation" and evs[0]["cycle"] == 1
    assert not seen, "an ambient repo's receipt must not be read"
    assert len(calls) == 1
    assert "preflight" not in calls[0]["extra_data"]
    _assert_passthrough(result, calls[0])


def test_AC14_relative_explicit_git_cwd_is_ambient_too(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC14: a RELATIVE explicit git_cwd counts as ambient -> ambient-skip, rung not read, no record, gate once."""
    pf = _pf()
    repo = _staged_repo(tmp_path, "fresh")  # a real fresh receipt exists; it must still not be read
    seen: list[tuple] = []

    def spy(*a: Any, **k: Any) -> dict:
        seen.append((a, k))
        return {"status": "fresh", "phase": "red", "red_step": None}

    monkeypatch.setattr(pf, "receipt_rung", spy)                  # absent today -> AttributeError
    monkeypatch.chdir(repo.parent)
    result, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=repo.name)
    evs = _rung_events(events)
    assert len(evs) == 1 and evs[0]["status"] == "ambient-skip" and evs[0]["cycle"] == 1
    assert not seen
    assert len(calls) == 1 and "preflight" not in calls[0]["extra_data"]
    _assert_passthrough(result, calls[0])


# --------------------------------------------------------------------------
# AC15 ladder_table legacy rows, malformed preflight, tie order
# --------------------------------------------------------------------------

def _legacy(reason: str = "VALIDATION_FAILED", head: str | None = None) -> dict:
    row = _row(reason, None, head)
    del row["detail"]["preflight"]
    return row


def test_AC15_ladder_table_legacy_rows_malformed_preflight_and_tie_order() -> None:
    """AC15: missing key -> 'none'; non-dict / non-str status -> skipped; ties ordered by status then red_step."""
    rs = importlib.import_module("bytedigger_engine.reject_stats")
    legacy = [_legacy(head=f"l{i}") for i in range(3)]
    malformed = [
        _row("VALIDATION_FAILED", "fresh"), _row("VALIDATION_FAILED", ["fresh"]),
        _row("VALIDATION_FAILED", {"red_step": "cite"}), _row("VALIDATION_FAILED", {"status": 5}),
        _row("VALIDATION_FAILED", {"status": None, "red_step": None}),
    ]
    table = rs.ladder_table(legacy + malformed)
    assert [(g["status"], g["red_step"], g["rejects"]) for g in table] == [("none", None, 3)]
    assert sorted(table[0]["findings_heads"]) == ["l0", "l1", "l2"]

    def rec(status: str, step: str | None) -> dict:
        return {"status": status, "red_step": step}

    groups = [rec("red", "lint"), rec("missing", None), rec("red", "cite"), rec("fresh", None), rec("red", None)]
    rows = [_row("VALIDATION_FAILED", g) for g in reversed(groups)] + [_row("VALIDATION_FAILED", g) for g in groups]
    tied = rs.ladder_table(rows)
    assert [(g["status"], g["red_step"], g["rejects"]) for g in tied] == [
        ("fresh", None, 2), ("missing", None, 2), ("red", None, 2), ("red", "cite", 2), ("red", "lint", 2),
    ]
    assert rs.ladder_table(list(reversed(rows))) == tied, "deterministic regardless of input order"
    assert [g["informative"] for g in tied] == [True, False, True, True, True]
    assert table[0]["informative"] is False  # "none" carries no evidence


# --------------------------------------------------------------------------
# AC16 stale receipt that was red
# --------------------------------------------------------------------------

def test_AC16_stale_but_was_red_keeps_first_red_step(tmp_path: Path) -> None:
    """AC16: red receipt then a tracked edit -> status 'stale', red_step = the receipt's first red step."""
    pf = _pf()
    repo, spec = _mk(tmp_path, "ac16", body=BAD_CITE_BODY)
    assert _g(_preflight(repo, spec, "red"), "exit_code") == 1
    assert pf.receipt_rung("red", str(repo))["status"] == "red"
    (repo / "calc.py").write_text(CALC + "# edit\n", encoding="utf-8")
    assert pf.receipt_rung("red", str(repo)) == {"status": "stale", "phase": "red", "red_step": "cite"}


# --------------------------------------------------------------------------
# AC17 silences (gate r2 MINOR-3)
# --------------------------------------------------------------------------

def test_AC17_error_path_record_and_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC17: rung raising -> event status 'error' AND extra_data["preflight"] == {"status":"error","red_step":None}."""
    pf = _pf()

    def boom(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("rung exploded")

    monkeypatch.setattr(pf, "receipt_rung", boom)             # absent today -> AttributeError
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(tmp_path))
    (ev,) = _rung_events(events)
    assert ev["status"] == "error" and ev["cycle"] == 1 and ev["phase"] == 5
    assert len(calls) == 1
    assert calls[0]["extra_data"]["preflight"] == {"status": "error", "red_step": None}


@pytest.mark.parametrize("cfg", [{"mode": "bogus"}, "x", 5], ids=["unknown-str", "str", "int"])
def test_AC17_config_error_event_without_record(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: Any) -> None:
    """AC17: config-error -> event emitted, no extra_data["preflight"], rung not read, gate once."""
    pf = _pf()
    seen: list[int] = []

    def spy(*_a: Any, **_k: Any) -> dict:
        seen.append(1)
        return {"status": "fresh", "phase": "red", "red_step": None}

    monkeypatch.setattr(pf, "receipt_rung", spy)              # absent today -> AttributeError
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(tmp_path), preflight_rung=cfg)
    (ev,) = _rung_events(events)
    assert ev["status"] == "config-error" and ev["cycle"] == 1
    assert len(calls) == 1 and "preflight" not in calls[0]["extra_data"]
    assert not seen


def test_AC17_ladder_table_informative_flag() -> None:
    """AC17: informative is True only for fresh/red groups."""
    rs = importlib.import_module("bytedigger_engine.reject_stats")
    recs = [{"status": "fresh", "red_step": None}, {"status": "red", "red_step": "cite"},
            {"status": "stale", "red_step": None}, {"status": "missing", "red_step": None},
            {"status": "error", "red_step": None}, {"status": "config-error", "red_step": None},
            {"status": "ambient-skip", "red_step": None}, None]
    table = rs.ladder_table([_row("VALIDATION_FAILED", r) for r in recs])
    got = {g["status"]: g["informative"] for g in table}
    assert got == {"fresh": True, "red": True, "stale": False, "missing": False, "error": False,
                   "config-error": False, "ambient-skip": False, "none": False}
    assert all(g["informative"] is True or g["informative"] is False for g in table)


def test_AC17_findings_heads_flatten_dedup_first_three() -> None:
    """AC17: findings_heads = rows' findings_head lists flattened in input order, de-duplicated, first 3."""
    rs = importlib.import_module("bytedigger_engine.reject_stats")
    fresh = {"status": "fresh", "red_step": None}
    lists = [["a", "b"], ["b", "c"], ["d", "a"], []]
    rows = []
    for lst in lists:
        row = _row("VALIDATION_FAILED", fresh)
        row["detail"]["findings_head"] = lst
        rows.append(row)
    (g,) = rs.ladder_table(rows)
    assert g["rejects"] == 4
    assert g["findings_heads"] == ["a", "b", "c"]


def test_AC17_subdirectory_git_cwd_reads_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC17: a sub-directory of a repo holding a real fresh receipt reads 'fresh', directly and via phase 5."""
    pf = _pf()
    repo = _staged_repo(tmp_path, "fresh")
    sub = repo / "tests"
    assert sub.is_dir()
    assert pf.receipt_rung("red", str(sub)) == {"status": "fresh", "phase": "red", "red_step": None}
    _r, calls, events, _o = _drive(monkeypatch, tmp_path, git_cwd=str(sub))
    (ev,) = _rung_events(events)
    assert ev["status"] == "fresh"
    assert calls[0]["extra_data"]["preflight"] == {"status": "fresh", "red_step": None}
