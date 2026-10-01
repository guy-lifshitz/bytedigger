"""RED tests for bd#141 op2 -- bytedigger_engine.loop_detector.

Spec: docs/decisions/2026-10-01-bd141-claim-evidence-loop-detector.md
(AC L1-L17, B1/B2 for loop_detector.py only).

The module under test does not exist yet. It is imported lazily inside each
test (helper ``_ld``) so the file collects and every AC fails at assert time,
independently of the others.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ENGINE_PY = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_PY.parent
MODULE_SRC = ENGINE_PY / "bytedigger_engine" / "loop_detector.py"


def _ld() -> Any:
    return importlib.import_module("bytedigger_engine.loop_detector")


def _run(seq: list[tuple[str, dict, bool]], th: Any = None) -> list[tuple[dict, dict | None]]:
    """Fold a call sequence through observe(); return (state, alert) per call."""
    ld = _ld()
    th = th if th is not None else ld.Thresholds()
    state = ld.empty_state()
    out: list[tuple[dict, dict | None]] = []
    for tool, tin, failed in seq:
        state, alert = ld.observe(state, tool, tin, failed, th)
        out.append((state, alert))
    return out


def _alerts(res: list[tuple[dict, dict | None]]) -> list[tuple[int, dict]]:
    return [(i + 1, a) for i, (_, a) in enumerate(res) if a is not None]


def _payload(sid: str = "s1", tool: str = "Bash", tin: Any = None, event: str = "PostToolUse", **extra: Any) -> dict:
    p: dict[str, Any] = {
        "session_id": sid,
        "tool_name": tool,
        "tool_input": tin if tin is not None else {"command": "ls"},
        "hook_event_name": event,
    }
    p.update(extra)
    return p


def _cli_env(**over: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_LOOP_")}
    env.update(over)
    return env


def _cli(args: list[str], stdin: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.loop_detector", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(ENGINE_PY),
        env=env,
        timeout=60,
    )


# ---------------------------------------------------------------- L1
def test_l1_call_hash_key_order_insensitive() -> None:
    """AC L1: call_hash ignores key order, differs for a different value."""
    ld = _ld()
    assert ld.call_hash({"a": 1, "b": 2}) == ld.call_hash({"b": 2, "a": 1}), "key order must not change hash"
    assert ld.call_hash({"a": 1, "b": 2}) != ld.call_hash({"a": 1, "b": 3}), "different value must change hash"


# ---------------------------------------------------------------- L2
def test_l2_repeat_alert_on_third_identical_call() -> None:
    """AC L2: 3 identical calls -> repeat alert on the 3rd, detail has x3."""
    res = _run([("Read", {"p": "a"}, False)] * 3)
    assert res[0][1] is None and res[1][1] is None, "no alert before the 3rd identical call"
    alert = res[2][1]
    assert alert is not None, "3rd identical call must alert"
    assert alert["kind"] == "repeat", f"kind must be repeat, got {alert['kind']!r}"
    assert "x3" in alert["detail"], f"detail must contain x3: {alert['detail']!r}"
    assert set(alert) == {"kind", "key", "detail"}, f"alert keys: {sorted(alert)}"


# ---------------------------------------------------------------- L3
def test_l3_oscillation_abab_alerts_on_fourth() -> None:
    """AC L3: A,B,A,B -> oscillation on the 4th call."""
    a = ("Read", {"p": "a"}, False)
    b = ("Grep", {"p": "b"}, False)
    res = _run([a, b, a, b])
    assert [i for i, _ in _alerts(res)] == [4], f"alert only on call 4, got {_alerts(res)}"
    assert _alerts(res)[0][1]["kind"] == "oscillation", "kind must be oscillation"
    assert "alternate a-b-a-b" in _alerts(res)[0][1]["detail"], "detail wording"


def test_l3_identical_run_is_not_oscillation() -> None:
    """AC L3: A,A,A,A does not yield oscillation."""
    res = _run([("Read", {"p": "a"}, False)] * 4)
    kinds = [a["kind"] for _, a in _alerts(res)]
    assert kinds == ["repeat"], f"A,A,A,A must give only the repeat alert, got {kinds}"


# ---------------------------------------------------------------- L4
def test_l4_thrash_three_failures_in_five_calls() -> None:
    """AC L4: 5 calls of one tool, different inputs, 3 failed -> thrash."""
    seq = [("Bash", {"c": i}, i < 3) for i in range(5)]
    res = _run(seq)
    al = _alerts(res)
    assert [i for i, _ in al] == [5], f"thrash must alert on call 5 only, got {al}"
    assert al[0][1]["kind"] == "thrash", "kind must be thrash"
    assert al[0][1]["detail"] == "Bash 5 of the last 8, 3 failed", f"detail: {al[0][1]['detail']!r}"


def test_l4_thrash_two_failures_no_alert() -> None:
    """AC L4: only 2 failed -> no alert."""
    seq = [("Bash", {"c": i}, i < 2) for i in range(5)]
    assert _alerts(_run(seq)) == [], "2 failures must not thrash"


# ---------------------------------------------------------------- L5
def test_l5_thrash_outranks_repeat() -> None:
    """AC L5: a call that is both repeat and thrash alerts as thrash."""
    x = ("Bash", {"c": "x"}, True)
    seq = [x, ("Bash", {"c": "z1"}, True), x, ("Bash", {"c": "z2"}, True), x]
    res = _run(seq)
    al = _alerts(res)
    assert len(al) == 1 and al[0][0] == 5, f"single alert on call 5, got {al}"
    assert al[0][1]["kind"] == "thrash", f"thrash must outrank repeat, got {al[0][1]['kind']!r}"


# ---------------------------------------------------------------- L6
def test_l6_one_alert_per_episode() -> None:
    """AC L6: continuing the same repeat for 10 more calls -> no further alert."""
    res = _run([("Read", {"p": "a"}, False)] * 13)
    al = _alerts(res)
    assert [i for i, _ in al] == [3], f"exactly one alert (call 3), got {[i for i, _ in al]}"


# ---------------------------------------------------------------- L7
def test_l7_cooldown_suppresses_new_key_and_does_not_record_it() -> None:
    """AC L7: new hit within cooldown -> no alert, key absent from live; after cooldown -> alert."""
    ld = _ld()
    x = ("Read", {"p": "x"}, False)
    y = ("Read", {"p": "y"}, False)
    res = _run([x, x, x, y, y, y, y, y])  # alert at n=3; y hits at n=6,7 (cooldown), n=8 (>4 after 3)
    al = _alerts(res)
    assert [i for i, _ in al] == [3, 8], f"alerts expected at calls 3 and 8, got {[i for i, _ in al]}"
    assert al[1][1]["kind"] == "repeat", "post-cooldown alert is the y repeat"
    y_key = "rep:Read:" + ld.call_hash({"p": "y"})
    st6 = res[5][0]  # n=6: y is a hit inside cooldown
    assert st6["n"] == 6, "state n must be 6 after 6 calls"
    assert y_key not in st6["live"], "cooldown branch must NOT record the key in live"
    assert st6["last_alert_n"] == 3, "cooldown branch must not move last_alert_n"
    st7 = res[6][0]
    assert y_key not in st7["live"], "still in cooldown at n=7 (n-3 == 4 is not > cooldown)"
    assert y_key in res[7][0]["live"], "after the alert the key is live"


# ---------------------------------------------------------------- L8
def test_l8_episode_expires_after_window_and_can_realert() -> None:
    """AC L8: a key not refreshed for > window calls is dropped and can alert again."""
    ld = _ld()
    x = ("Read", {"p": "x"}, False)
    filler = [("Glob", {"p": i}, False) for i in range(20)]  # n=4..23
    res = _run([x, x, x, *filler, x, x, x])
    al = _alerts(res)
    assert [i for i, _ in al] == [3, 26], f"alerts expected at 3 and 26, got {[i for i, _ in al]}"
    x_key = "rep:Read:" + ld.call_hash({"p": "x"})
    assert x_key in res[22][0]["live"], "n=23: 23-3 == 20 is not > window, key still live"
    assert x_key not in res[23][0]["live"] or res[23][0]["live"][x_key] == 24, "n=24: stale key must be expired"


def test_l4_thrash_counts_only_last_span_entries() -> None:
    """Gate note 7: 3 failed Bash, 9 other calls, 2 Bash -> only 2 Bash in last 8 -> no alert."""
    seq = [("Bash", {"c": i}, True) for i in range(3)]
    seq += [("Glob", {"p": i}, False) for i in range(9)]
    seq += [("Bash", {"c": 100 + i}, False) for i in range(2)]
    assert _alerts(_run(seq)) == [], "thrash must be counted over the last thrash_span entries only"


def test_l6_mixed_episode_refreshes_new_key_without_alert() -> None:
    """Gate note 7: a live key plus a new hit key -> new key refreshed into live, no alert."""
    x = ("Bash", {"c": "x"}, True)
    res = _run([x] * 5)
    al = _alerts(res)
    assert [i for i, _ in al] == [3], f"only the repeat alert at call 3, got {[i for i, _ in al]}"
    assert res[4][0]["live"].get("thr:Bash") == 5, f"thrash key must be refreshed into live: {res[4][0]['live']}"


# ---------------------------------------------------------------- L9
def test_l9_observe_is_pure_and_window_is_capped() -> None:
    """AC L9: observe does not mutate its input; window length capped at `window`."""
    ld = _ld()
    th = ld.Thresholds(window=5)
    state = ld.empty_state()
    for i in range(12):
        before = copy.deepcopy(state)
        new_state, _ = ld.observe(state, "Read", {"p": i}, False, th)
        assert state == before, f"observe mutated its input state at call {i + 1}"
        assert new_state is not state, "observe must return a new state object"
        state = new_state
    assert state["n"] == 12, f"n must be 12, got {state['n']}"
    assert len(state["win"]) == 5, f"win must be capped at 5, got {len(state['win'])}"
    assert state["win"][-1]["h"] == ld.call_hash({"p": 11}), "newest entry kept at the end"
    assert set(state["win"][-1]) == {"t", "h", "f"}, "win item keys"


# ---------------------------------------------------------------- L10
def test_l10_load_state_rejects_bad_shapes(tmp_path: Path) -> None:
    """AC L10: missing / invalid JSON / v:2 / n:true / bad win item -> empty_state()."""
    ld = _ld()
    empty = ld.empty_state()
    assert empty == {"v": 1, "n": 0, "win": [], "last_alert_n": None, "live": {}}, f"empty_state shape: {empty}"
    assert ld.load_state(tmp_path / "missing.json") == empty, "missing file"
    cases = {
        "invalid.json": "{not json",
        "v2.json": json.dumps({"v": 2, "n": 1, "win": [], "last_alert_n": None, "live": {}}),
        "booln.json": json.dumps({"v": 1, "n": True, "win": [], "last_alert_n": None, "live": {}}),
        "badf.json": json.dumps({"v": 1, "n": 1, "win": [{"t": "a", "h": "b", "f": "x"}], "last_alert_n": None, "live": {}}),
    }
    for name, text in cases.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        assert ld.load_state(p) == empty, f"{name} must load as empty_state()"


def test_l10_save_state_failure_removes_tmp_and_reraises(tmp_path: Path) -> None:
    """Gate note 3: os.replace fails (target is a directory) -> error propagates, no tmp left."""
    ld = _ld()
    target = tmp_path / "s.json"
    target.mkdir()
    raised = False
    try:
        ld.save_state(target, ld.empty_state())
    except OSError:
        raised = True
    assert raised, "save_state must re-raise the write failure"
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"], "no *.tmp file may be left behind"


def test_l10_save_then_load_round_trips(tmp_path: Path) -> None:
    """AC L10 control: a valid state survives save_state/load_state; parent dirs made; no tmp left."""
    ld = _ld()
    res = _run([("Read", {"p": "a"}, False)] * 3)
    state = res[-1][0]
    p = tmp_path / "deep" / "er" / "s.json"
    ld.save_state(p, state)
    assert ld.load_state(p) == state, "round trip must preserve state"
    assert state["live"], "control state must have a live episode"
    assert [f.name for f in p.parent.iterdir()] == ["s.json"], "no tmp file left behind"


# ---------------------------------------------------------------- L11
def test_l11_process_event_persists_state_and_sanitizes_sid(tmp_path: Path) -> None:
    """AC L11: <state_dir>/<sid>.json written, n increments, sid sanitized, no tmp."""
    ld = _ld()
    sd = tmp_path / "state"
    ld.process_event(_payload(sid="a/b..c"), sd)
    f = sd / "abc.json"
    assert f.is_file(), f"state file abc.json expected, found {sorted(p.name for p in sd.iterdir())}"
    assert json.loads(f.read_text(encoding="utf-8"))["n"] == 1, "n must be 1 after first call"
    ld.process_event(_payload(sid="a/b..c"), sd)
    assert json.loads(f.read_text(encoding="utf-8"))["n"] == 2, "n must be 2 after second call"
    assert [p.name for p in sd.iterdir()] == ["abc.json"], "only abc.json, no *.tmp"
    ld.process_event(_payload(sid="///"), sd)
    assert (sd / "nosession.json").is_file(), "empty sanitized sid falls back to nosession"


# ---------------------------------------------------------------- L12
def test_l12_ignored_payloads_touch_no_disk(tmp_path: Path) -> None:
    """AC L12: subagent / missing tool_name / non-dict -> None, no file created."""
    ld = _ld()
    sd = tmp_path / "state"
    sub = _payload()
    sub["agent_id"] = "agent-7"
    no_tool = _payload()
    del no_tool["tool_name"]
    for label, p in (("subagent", sub), ("no tool_name", no_tool), ("list", [1, 2]), ("str", "x")):
        assert ld.process_event(p, sd) is None, f"{label} must return None"
        assert not sd.exists(), f"{label} must not create the state dir"
    # control: the same payload without agent_id does create state (not a blanket no-op)
    ld.process_event(_payload(), sd)
    assert sd.exists(), "control payload must create state"


# ---------------------------------------------------------------- L13
def test_l13_journal_row_has_exact_keys_and_no_raw_input(tmp_path: Path) -> None:
    """AC L13: one JSONL row on alert, exact keys, no raw input; unwritable log still alerts."""
    ld = _ld()
    marker = "SECRET-MARKER-9f3a71"
    sd = tmp_path / "state"
    log = tmp_path / "logs" / "loop.jsonl"
    outs = [ld.process_event(_payload(tin={"command": marker}), sd, log_path=log) for _ in range(3)]
    assert outs[0] is None and outs[1] is None, "no advisory before the 3rd call"
    assert outs[2] is not None and "[LOOP DETECTED] repeat:" in outs[2], f"advisory on 3rd call: {outs[2]!r}"
    assert log.is_file(), "journal file must exist after an alert"
    text = log.read_text(encoding="utf-8")
    rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    assert len(rows) == 1, f"exactly one journal row, got {len(rows)}"
    assert set(rows[0]) == {"ts", "session_id", "kind", "tool", "n", "key_sha256"}, f"row keys: {sorted(rows[0])}"
    assert rows[0]["kind"] == "repeat" and rows[0]["tool"] == "Bash" and rows[0]["n"] == 3, f"row: {rows[0]}"
    assert marker not in text, "raw tool input must not appear in the journal"

    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    bad_log = blocker / "sub" / "loop.jsonl"
    sd2 = tmp_path / "state2"
    outs2 = [ld.process_event(_payload(sid="s2", tin={"command": marker}), sd2, log_path=bad_log) for _ in range(3)]
    assert outs2[2] is not None and "[LOOP DETECTED]" in outs2[2], "journal failure must not suppress the alert"


def test_l13_journal_creates_missing_dir_and_logs_sanitized_sid(tmp_path: Path) -> None:
    """AC L13: log_path in a not-yet-existing dir is created; session_id is the sanitized sid."""
    ld = _ld()
    log = tmp_path / "new" / "dir" / "loop.jsonl"
    assert not log.parent.exists(), "precondition: log dir does not exist"
    outs = [ld.process_event(_payload(sid="a/b..c"), tmp_path / "state", log_path=log) for _ in range(3)]
    assert outs[2] is not None, "3rd identical call must alert"
    assert log.parent.is_dir(), "journal parent dir must be created"
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 1, f"exactly one journal row, got {len(rows)}"
    assert rows[0]["session_id"] == "abc", f"session_id must be the sanitized sid, got {rows[0]['session_id']!r}"


# ---------------------------------------------------------------- L14
def test_l14_state_save_failure_returns_none(tmp_path: Path) -> None:
    """AC L14: state_dir is a file -> None, no exception (while a real dir would alert)."""
    ld = _ld()
    blocker = tmp_path / "iamafile"
    blocker.write_text("x", encoding="utf-8")
    for i in range(3):
        assert ld.process_event(_payload(), blocker) is None, f"call {i + 1}: save failure must give None"
    good = tmp_path / "good"
    outs = [ld.process_event(_payload(), good) for _ in range(3)]
    assert outs[2] is not None, "control: the same 3 calls in a writable dir must alert"


# ---------------------------------------------------------------- L15
def test_l15_thresholds_from_env() -> None:
    """AC L15: BD_LOOP_REPEAT honored; 0 / -1 / x -> default; HAL_LOOP_REPEAT ignored."""
    ld = _ld()
    d = ld.Thresholds()
    assert (d.window, d.repeat, d.thrash_span, d.thrash_calls, d.thrash_fails, d.cooldown) == (20, 3, 8, 5, 3, 4), "defaults"
    assert ld.thresholds_from_env({"BD_LOOP_REPEAT": "2"}).repeat == 2, "BD_LOOP_REPEAT=2 honored"
    assert ld.thresholds_from_env({"BD_LOOP_REPEAT": " 7 "}).repeat == 7, "value is stripped"
    for bad in ("0", "-1", "x", ""):
        assert ld.thresholds_from_env({"BD_LOOP_REPEAT": bad}).repeat == 3, f"{bad!r} -> default"
    assert ld.thresholds_from_env({"HAL_LOOP_REPEAT": "2"}).repeat == 3, "HAL_LOOP_REPEAT must be ignored"
    t = ld.thresholds_from_env({
        "BD_LOOP_WINDOW": "30", "BD_LOOP_THRASH_SPAN": "9", "BD_LOOP_THRASH_CALLS": "6",
        "BD_LOOP_THRASH_FAILS": "4", "BD_LOOP_COOLDOWN": "5",
    })
    assert (t.window, t.thrash_span, t.thrash_calls, t.thrash_fails, t.cooldown) == (30, 9, 6, 4, 5), "all env names"


# ---------------------------------------------------------------- L16
def test_l16_cli_third_identical_payload_prints_advisory(tmp_path: Path) -> None:
    """AC L16: CLI via real subprocess; 3rd identical payload prints advisory, rc 0."""
    sd = tmp_path / "cli-state"
    stdin = json.dumps(_payload())
    results = [_cli(["--state-dir", str(sd)], stdin, _cli_env()) for _ in range(3)]
    assert all(r.returncode == 0 for r in results), f"rc must be 0: {[r.returncode for r in results]} {results[0].stderr[-300:]}"
    assert results[0].stdout == "" and results[1].stdout == "", "no output before the 3rd call"
    out = results[2].stdout
    assert out.startswith("<system-reminder>[LOOP DETECTED] repeat:"), f"stdout: {out!r}"
    assert out.endswith("\n") and out.count("\n") == 1, "exactly one line"


def test_l16_cli_invalid_stdin_is_silent_rc0(tmp_path: Path) -> None:
    """AC L16: invalid stdin -> empty stdout, rc 0."""
    r = _cli(["--state-dir", str(tmp_path / "s")], "{{ not json", _cli_env())
    assert r.returncode == 0, f"rc must be 0, got {r.returncode}; stderr={r.stderr[-300:]}"
    assert r.stdout == "", f"stdout must be empty, got {r.stdout!r}"
    assert "No module named" not in r.stderr, "module must exist (rc 0 alone could come from an import failure)"


def test_l16_cli_kill_switch_writes_nothing(tmp_path: Path) -> None:
    """AC L16: BD_LOOP_DETECTOR=0 -> empty stdout, state dir not created."""
    sd = tmp_path / "never-created"
    stdin = json.dumps(_payload())
    for _ in range(3):
        r = _cli(["--state-dir", str(sd)], stdin, _cli_env(BD_LOOP_DETECTOR="0"))
        assert r.returncode == 0, f"rc must be 0, got {r.returncode}; stderr={r.stderr[-300:]}"
        assert "No module named" not in r.stderr, "module must exist"
        assert r.stdout == "", f"stdout must be empty, got {r.stdout!r}"
    assert not sd.exists(), "state dir must not be created when disabled"


# ---------------------------------------------------------------- L17
def test_l17_post_tool_use_failure_marks_window_entry(tmp_path: Path) -> None:
    """AC L17: PostToolUseFailure -> f true in the saved window; PostToolUse -> false."""
    ld = _ld()
    sd = tmp_path / "state"
    ld.process_event(_payload(sid="f1", event="PostToolUseFailure"), sd)
    ld.process_event(_payload(sid="f2", event="PostToolUse"), sd)
    w1 = json.loads((sd / "f1.json").read_text(encoding="utf-8"))["win"]
    w2 = json.loads((sd / "f2.json").read_text(encoding="utf-8"))["win"]
    assert w1[-1]["f"] is True, f"failure event must set f true: {w1}"
    assert w2[-1]["f"] is False, f"success event must set f false: {w2}"


# ---------------------------------------------------------------- B1
def test_b1_registered_in_manifest_and_strict_list_and_boundary_lint() -> None:
    """AC B1: loop_detector.py in core_modules and mypy-strict-modules.txt; boundary lint ok."""
    manifest = json.loads((ENGINE_PY / "core_manifest.json").read_text(encoding="utf-8"))
    assert "loop_detector.py" in manifest["core_modules"], "loop_detector.py missing from core_modules"
    strict = (ENGINE_PY / "bytedigger_engine" / "mypy-strict-modules.txt").read_text(encoding="utf-8")
    entries = [ln.strip() for ln in strict.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert "loop_detector.py" in entries, "loop_detector.py missing from mypy-strict-modules.txt"
    assert MODULE_SRC.is_file(), "module file must exist"
    r = subprocess.run(
        [sys.executable, "core-boundary-lint.py", "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120,
    )
    data = json.loads(r.stdout)
    assert data["ok"] is True, f"core-boundary-lint not ok: {data}"


# ---------------------------------------------------------------- B2
def test_b2_no_cyrillic_and_no_hal_prefix_in_source() -> None:
    """AC B2: module source has no U+0400-U+04FF char and no 'HAL_' substring."""
    assert MODULE_SRC.is_file(), f"{MODULE_SRC} must exist"
    src = MODULE_SRC.read_text(encoding="utf-8")
    cyrillic = "[" + chr(0x400) + "-" + chr(0x4FF) + "]"
    assert re.search(cyrillic, src) is None, "Cyrillic char found in loop_detector.py"
    assert "HAL_" not in src, "HAL_ substring found in loop_detector.py"
