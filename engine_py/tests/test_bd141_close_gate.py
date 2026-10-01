"""RED tests for bd#141 item 7 -- close gate (``close_gate``).

Spec: docs/decisions/2026-10-01-bd141-close-gate.md (ACs G1-G22, r2).

The module under test does not exist yet. Every AC reaches it through a real
``python -m bytedigger_engine.close_gate`` subprocess or a lazy ``_cg()`` import
inside the test, so the file collects and each AC fails at assert time
independently (workflows.md 1q). Nothing here mocks the unit under test.
The latch AC (G15) is anchored on the real on-disk O_EXCL file; the state is
pre-staged by running the CLI sequentially, never by racing (workflows.md 1i).
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ENGINE_PY = Path(__file__).resolve().parents[1]
MODULE_FILE = ENGINE_PY / "bytedigger_engine" / "close_gate.py"

DASH = "—"


def _cg() -> Any:
    """Lazy import of the unit under test (fails at assert time, not collect)."""
    return importlib.import_module("bytedigger_engine.close_gate")


# --------------------------------------------------------------------------
# Transcript builders (Claude Code transcript shape)
# --------------------------------------------------------------------------

def _user(text: str, **extra: Any) -> dict:
    return {"type": "user", "message": {"content": text}, **extra}


def _assistant(text: str | None = None, tool_uses: list[dict] | None = None,
               **extra: Any) -> dict:
    blocks: list[dict] = []
    if text is not None:
        blocks.append({"type": "text", "text": text})
    blocks.extend(tool_uses or [])
    return {"type": "assistant", "message": {"content": blocks}, **extra}


def _result(tool_id: str, content: Any = "ok", is_error: bool = False, **extra: Any) -> dict:
    return {
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": tool_id,
            "content": content, "is_error": is_error,
        }]},
        **extra,
    }


def _step(block: dict, err: bool = False, side: bool = False) -> dict:
    return {"block": block, "err": err, "side": side}


def _bash_step(tid: str, side: bool = False) -> dict:
    return _step({"type": "tool_use", "id": tid, "name": "Bash",
                  "input": {"command": "echo hi"}}, side=side)


def _tool_step(tid: str, name: str, path: str, err: bool = False) -> dict:
    key = "notebook_path" if name == "NotebookEdit" else "file_path"
    return _step({"type": "tool_use", "id": tid, "name": name,
                  "input": {key: path}}, err=err)


def _bashes(n: int, prefix: str = "b") -> list[dict]:
    return [_bash_step(f"{prefix}{i}") for i in range(n)]


def _with(steps: list[dict], index: int, replacement: dict) -> list[dict]:
    """Replace the step at 0-based ``index``."""
    out = list(steps)
    out[index] = replacement
    return out


def _transcript(steps: list[dict], final: str) -> list[dict]:
    entries: list[dict] = [_user("please finish the work")]
    for s in steps:
        extra = {"isSidechain": True} if s["side"] else {}
        entries.append(_assistant(tool_uses=[s["block"]], **extra))
        entries.append(_result(s["block"]["id"], "ok", s["err"], **extra))
    entries.append(_assistant(final))
    return entries


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _spec(tmp_path: Path, name: str = "spec.md") -> Path:
    spec = tmp_path / name
    spec.write_text("# spec\n", encoding="utf-8")
    return spec


# --------------------------------------------------------------------------
# CLI helpers (real subprocess)
# --------------------------------------------------------------------------

def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.close_gate", *args],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=60,
    )


def _verdict(proc: subprocess.CompletedProcess[str], label: str) -> dict:
    assert proc.returncode == 0, f"{label}: rc 0, got {proc.returncode}; stderr={proc.stderr[-300:]!r}"
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, f"{label}: exactly one stdout line, got {lines!r}"
    return json.loads(lines[0])


def _run(tmp_path: Path, entries: list[dict], spec: Path, *extra: str,
         label: str = "run") -> dict:
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, entries)
    return _verdict(_cli("--transcript", str(transcript), "--spec", str(spec), *extra), label)


# --------------------------------------------------------------------------
# G1-G4 counting
# --------------------------------------------------------------------------

def test_G1_untouched_spec_ten_calls_fires(tmp_path: Path) -> None:
    """G1: spec never touched, 10 calls, 'Done.' -> fire."""
    spec = _spec(tmp_path)
    v = _run(tmp_path, _transcript(_bashes(10), "Done."), spec, label="G1")
    assert v == {"outcome": "fire", "phrase": "Done", "calls": 10, "threshold": 10}, f"G1: {v!r}"


def test_G2_nine_calls_clears(tmp_path: Path) -> None:
    """G2: boundary, 9 calls -> clear."""
    spec = _spec(tmp_path)
    v = _run(tmp_path, _transcript(_bashes(9), "Done."), spec, label="G2")
    # Red if: the comparison were `calls >= threshold - 1` (9 would fire). The opposite regression,
    # `calls > threshold`, is NOT caught here (9 clears either way); G1 (10 calls must fire) catches it.
    assert v["outcome"] == "clear" and v["calls"] == 9, f"G2: {v!r}"


def test_G3_successful_edit_resets_count(tmp_path: Path) -> None:
    """G3: Edit at call 5 of 12 -> clear/7; Edit at call 2 -> fire/10."""
    spec = _spec(tmp_path)
    edit = lambda: _tool_step("e1", "Edit", str(spec))  # noqa: E731
    v5 = _run(tmp_path, _transcript(_with(_bashes(12), 4, edit()), "Done."), spec, label="G3a")
    # Red if: the count ignored spec touches (12 calls -> fire) or counted the touch itself (8).
    assert v5["outcome"] == "clear" and v5["calls"] == 7, f"G3: edit at 5 -> {v5!r}"
    v2 = _run(tmp_path, _transcript(_with(_bashes(12), 1, edit()), "Done."), spec, label="G3b")
    assert v2["outcome"] == "fire" and v2["calls"] == 10, f"G3: edit at 2 -> {v2!r}"


def test_G4_write_multiedit_notebookedit_reset_read_does_not(tmp_path: Path) -> None:
    """G4: Write/MultiEdit/NotebookEdit reset; Read at call 11 does not."""
    spec = _spec(tmp_path)
    for name in ("Write", "MultiEdit", "NotebookEdit"):
        steps = _with(_bashes(12), 1, _tool_step("x1", name, str(spec)))
        v = _run(tmp_path, _transcript(steps, "Done."), spec, label=f"G4-{name}")
        # Red if: the touch-tool set omitted this tool name or read the wrong input key.
        assert v["calls"] == 10 and v["outcome"] == "fire", f"G4: {name} -> {v!r}"
    steps = _with(_bashes(12), 1, _tool_step("x1", "Edit", str(spec)))
    steps = _with(steps, 10, _tool_step("r1", "Read", str(spec)))
    v = _run(tmp_path, _transcript(steps, "Done."), spec, label="G4-read")
    # Red if: Read (or any tool naming the spec) counted as a touch (calls would be 1).
    assert v["calls"] == 10, f"G4: Read must not reset, got {v!r}"


# --------------------------------------------------------------------------
# G5-G6 path resolution, touch validity
# --------------------------------------------------------------------------

def test_G5_relative_path_resolved_against_cwd(tmp_path: Path) -> None:
    """G5: relative file_path joined to --cwd matches the spec."""
    work = tmp_path / "work"
    work.mkdir()
    spec = _spec(work)
    steps = _with(_bashes(12), 1, _tool_step("e1", "Edit", "spec.md"))
    v = _run(tmp_path, _transcript(steps, "Done."), spec, "--cwd", str(work), label="G5-rel")
    # Red if: relative paths were joined to the process cwd (ENGINE_PY) instead of --cwd.
    assert v["calls"] == 10, f"G5: relative path must match via --cwd, got {v!r}"


def test_G5_symlinked_directory_matches_real_path_both_ways(tmp_path: Path) -> None:
    """G5: a spec reached through a symlinked dir matches the real path (and vice versa)."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    real_spec = _spec(real)
    # spec given via link, tool used the real path
    steps = _with(_bashes(12), 1, _tool_step("e1", "Edit", str(real_spec)))
    v1 = _run(tmp_path, _transcript(steps, "Done."), link / "spec.md", label="G5-link-spec")
    # Red if: paths were compared lexically (no os.path.realpath on one side).
    assert v1["calls"] == 10, f"G5: spec via link, tool via real -> {v1!r}"
    # spec given via real path, tool used the link
    steps = _with(_bashes(12), 1, _tool_step("e1", "Edit", str(link / "spec.md")))
    v2 = _run(tmp_path, _transcript(steps, "Done."), real_spec, label="G5-link-tool")
    assert v2["calls"] == 10, f"G5: spec via real, tool via link -> {v2!r}"


def test_G6_errored_edit_and_other_file_edit_are_not_touches(tmp_path: Path) -> None:
    """G6: is_error edit of the spec and an edit of another file do not reset."""
    spec = _spec(tmp_path)
    other = _spec(tmp_path, "other.md")
    bad = _with(_bashes(12), 4, _tool_step("e1", "Edit", str(spec), err=True))
    v = _run(tmp_path, _transcript(bad, "Done."), spec, label="G6-error")
    # Red if: the paired tool_result's is_error flag were ignored (count would reset to 7).
    assert v["calls"] == 12 and v["outcome"] == "fire", f"G6: errored edit is not a touch, got {v!r}"
    elsewhere = _with(_bashes(12), 4, _tool_step("e1", "Edit", str(other)))
    v = _run(tmp_path, _transcript(elsewhere, "Done."), spec, label="G6-other")
    # Red if: any Edit counted as a touch regardless of which file it names.
    assert v["calls"] == 12, f"G6: edit of another file is not a touch, got {v!r}"


# --------------------------------------------------------------------------
# G7-G8 main chain, whole transcript
# --------------------------------------------------------------------------

def test_G7_sidechain_calls_are_not_counted(tmp_path: Path) -> None:
    """G7: 6 main + 6 sidechain calls -> clear, calls == 6."""
    spec = _spec(tmp_path)
    steps: list[dict] = []
    for i in range(6):
        steps.append(_bash_step(f"m{i}"))
        steps.append(_bash_step(f"s{i}", side=True))
    v = _run(tmp_path, _transcript(steps, "Done."), spec, label="G7")
    # Red if: the isSidechain filter were dropped (12 calls -> fire).
    assert v["outcome"] == "clear" and v["calls"] == 6, f"G7: {v!r}"


def test_G8_count_spans_whole_transcript_not_turn_slice(tmp_path: Path) -> None:
    """G8: 6 calls before the last real user entry, 4 after -> fire, calls == 10."""
    spec = _spec(tmp_path)
    entries = _transcript(_bashes(6, "a"), "interim")[:-1]
    entries.append(_user("second real request"))
    for s in _bashes(4, "c"):
        entries.append(_assistant(tool_uses=[s["block"] if "block" in s else s]))
        entries.append(_result(s["block"]["id"]))
    entries.append(_assistant("Done."))
    v = _run(tmp_path, entries, spec, label="G8")
    # Red if: calls were counted only inside slice_current_turn (would be 4 -> clear).
    assert v["outcome"] == "fire" and v["calls"] == 10, f"G8: {v!r}"


# --------------------------------------------------------------------------
# G9-G10 claim, mention
# --------------------------------------------------------------------------

def test_G9_non_claims_are_no_claim(tmp_path: Path) -> None:
    """G9: unclaimed, negated, downgraded, and fenced-only text -> no-claim, calls None."""
    spec = _spec(tmp_path)
    cases = {
        "plain": "Still working on it.",
        "negated": "Not done yet.",
        "downgrade": "Done, except 1 failing test.",
        "fenced": "Output:\n```\nDone\n```\nmore work follows",
    }
    for label, text in cases.items():
        v = _run(tmp_path, _transcript(_bashes(12), text), spec, label=f"G9-{label}")
        # Red if: close_gate re-implemented claim detection without negation/downgrade/quote stripping.
        assert v["outcome"] == "no-claim" and v["calls"] is None, f"G9: {label} -> {v!r}"


def test_G10_spec_basename_in_raw_text_is_mentioned(tmp_path: Path) -> None:
    """G10: basename in raw final text (backticks, any case) -> mentioned."""
    spec = _spec(tmp_path)
    for label, text in (("code", f"Done {DASH} updated `spec.md` progress."),
                        ("upper", f"Done {DASH} see SPEC.MD")):
        v = _run(tmp_path, _transcript(_bashes(10), text), spec, label=f"G10-{label}")
        # Red if: the mention check ran on stripped text (inline code removed) or was case-sensitive.
        assert v["outcome"] == "mentioned", f"G10: {label} -> {v!r}"
        assert v["calls"] is None and v["phrase"] == "Done", f"G10: {label} fields -> {v!r}"
    v = _run(tmp_path, _transcript(_bashes(10), "Still working on spec.md."), spec, label="G10-no-claim")
    # Red if: the mention check ran before claim detection (would answer mentioned).
    assert v["outcome"] == "no-claim" and v["calls"] is None, f"G10: unclaimed mention -> {v!r}"


# --------------------------------------------------------------------------
# G11-G12 no-turn, no-spec
# --------------------------------------------------------------------------

def test_G11_no_turn_cases(tmp_path: Path) -> None:
    """G11: missing transcript, empty file, no real user entry -> no-turn."""
    spec = _spec(tmp_path)
    missing = _verdict(_cli("--transcript", str(tmp_path / "absent.jsonl"), "--spec", str(spec)), "G11-missing")
    assert missing["outcome"] == "no-turn", f"G11: missing -> {missing!r}"
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    v = _verdict(_cli("--transcript", str(empty), "--spec", str(spec)), "G11-empty")
    assert v["outcome"] == "no-turn", f"G11: empty -> {v!r}"
    no_user = [_assistant(tool_uses=[_bash_step("b0")["block"]]), _assistant("Done.")]
    v = _run(tmp_path, no_user, spec, label="G11-no-user")
    # Red if: an empty turn slice fell through to claim detection instead of returning no-turn.
    assert v["outcome"] == "no-turn" and v["phrase"] is None and v["calls"] is None, f"G11: {v!r}"


def test_G12_no_spec_checked_before_transcript(tmp_path: Path) -> None:
    """G12: absent spec or directory spec -> no-spec, even with a missing transcript."""
    absent = _verdict(_cli("--transcript", str(tmp_path / "absent.jsonl"),
                           "--spec", str(tmp_path / "nope.md")), "G12-absent")
    # Red if: the transcript were read first (would answer no-turn).
    assert absent["outcome"] == "no-spec", f"G12: absent spec -> {absent!r}"
    d = tmp_path / "adir"
    d.mkdir()
    v = _run(tmp_path, _transcript(_bashes(10), "Done."), d, label="G12-dir")
    # Red if: the spec test were os.path.exists instead of isfile (a directory would reach fire).
    assert v["outcome"] == "no-spec", f"G12: directory spec -> {v!r}"


# --------------------------------------------------------------------------
# G13-G14 vocab, threshold
# --------------------------------------------------------------------------

def test_G13_extended_vocab_makes_new_claim_fire(tmp_path: Path) -> None:
    """G13: --vocab adding claim 'shipped' -> 'Shipped.' fires; without -> no-claim."""
    spec = _spec(tmp_path)
    vocab = tmp_path / "vocab.json"
    vocab.write_text('{"claim": ["shipped"]}', encoding="utf-8")
    entries = _transcript(_bashes(10), "Shipped.")
    without = _run(tmp_path, entries, spec, label="G13-without")
    assert without["outcome"] == "no-claim", f"G13: default vocab -> {without!r}"
    with_v = _run(tmp_path, entries, spec, "--vocab", str(vocab), label="G13-with")
    # Red if: --vocab were parsed but not passed through to claim_phrase.
    # Also red if: the phrase were case-folded (matched text is reported as written).
    assert with_v["outcome"] == "fire" and with_v["phrase"] == "Shipped", f"G13: {with_v!r}"


def test_G14_threshold_flag(tmp_path: Path) -> None:
    """G14: --threshold 3 with 3 calls fires, with 2 clears; threshold echoed."""
    spec = _spec(tmp_path)
    v3 = _run(tmp_path, _transcript(_bashes(3), "Done."), spec, "--threshold", "3", label="G14-3")
    assert v3["outcome"] == "fire" and v3["threshold"] == 3, f"G14: {v3!r}"
    v2 = _run(tmp_path, _transcript(_bashes(2), "Done."), spec, "--threshold", "3", label="G14-2")
    # Red if: --threshold were ignored in favour of the default 10 (3 calls would clear).
    assert v2["outcome"] == "clear" and v2["calls"] == 2 and v2["threshold"] == 3, f"G14: {v2!r}"


# --------------------------------------------------------------------------
# G15 latch (side effect on disk)
# --------------------------------------------------------------------------

def _latch_name(run_id: str) -> str:
    return "close-gate-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16] + ".latch"


def test_G15_latch_once_per_run_on_disk(tmp_path: Path) -> None:
    """G15: second identical fire is latched; exactly one latch file; other run id fires again."""
    spec = _spec(tmp_path)
    state = tmp_path / "state"
    entries = _transcript(_bashes(10), "Done.")
    args = ("--state-dir", str(state), "--run-id", "run-1")
    first = _run(tmp_path, entries, spec, *args, label="G15-first")
    assert first["outcome"] == "fire", f"G15: first -> {first!r}"
    assert (state / _latch_name("run-1")).is_file(), "G15: latch file named by sha256(run_id)[:16]"
    second = _run(tmp_path, entries, spec, *args, label="G15-second")
    # Red if: latch() returned True on an existing file (no O_EXCL) or was never consulted.
    assert second["outcome"] == "latched", f"G15: second -> {second!r}"
    assert second["calls"] == 10, f"G15: latched keeps calls, got {second!r}"
    assert len(list(state.glob("*.latch"))) == 1, "G15: exactly one latch file"
    other = _run(tmp_path, entries, spec, "--state-dir", str(state), "--run-id", "run-2", label="G15-other")
    assert other["outcome"] == "fire", f"G15: different run id -> {other!r}"
    assert len(list(state.glob("*.latch"))) == 2, "G15: second run id gets its own file"


def test_G15_run_id_never_reaches_path_verbatim(tmp_path: Path) -> None:
    """G15: --run-id '../escape' stays inside the state dir."""
    spec = _spec(tmp_path)
    parent = tmp_path / "parent"
    state = parent / "state"
    state.mkdir(parents=True)
    v = _run(tmp_path, _transcript(_bashes(10), "Done."), spec,
             "--state-dir", str(state), "--run-id", "../escape", label="G15-escape")
    assert v["outcome"] == "fire", f"G15: {v!r}"
    # Red if: the run id were joined into the path unhashed (file lands in parent/).
    assert sorted(p.name for p in parent.iterdir()) == ["state"], "G15: nothing created in parent"
    assert [p.name for p in state.iterdir()] == [_latch_name("../escape")], "G15: file inside state dir"


def test_G15_clear_verdict_creates_no_latch_and_nested_dir_is_created(tmp_path: Path) -> None:
    """G15: only fire consults the latch; a nested missing state dir is created on fire."""
    spec = _spec(tmp_path)
    state = tmp_path / "x" / "y" / "z"
    clear = _run(tmp_path, _transcript(_bashes(9), "Done."), spec,
                 "--state-dir", str(state), "--run-id", "r", label="G15-clear")
    assert clear["outcome"] == "clear", f"G15: {clear!r}"
    # Red if: the latch were created before the threshold check (clear would leave a file).
    assert not list(tmp_path.rglob("*.latch")), "G15: clear must not create a latch file"
    fire = _run(tmp_path, _transcript(_bashes(10), "Done."), spec,
                "--state-dir", str(state), "--run-id", "r", label="G15-nested")
    assert fire["outcome"] == "fire", f"G15: {fire!r}"
    assert len(list(state.glob("*.latch"))) == 1, "G15: nested state dir created with one latch"


# --------------------------------------------------------------------------
# G16-G17 CLI contract
# --------------------------------------------------------------------------

def test_G16_cli_usage_errors_exit_2(tmp_path: Path) -> None:
    """G16: usage errors -> rc 2, empty stdout, stderr message."""
    spec = _spec(tmp_path)
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, _transcript(_bashes(10), "Done."))
    bad_vocab = tmp_path / "bad.json"
    bad_vocab.write_text('{"bogus": []}', encoding="utf-8")
    t, s = str(transcript), str(spec)
    cases = {
        "no-transcript": ["--spec", s],
        "no-spec": ["--transcript", t],
        "threshold-0": ["--transcript", t, "--spec", s, "--threshold", "0"],
        "threshold-x": ["--transcript", t, "--spec", s, "--threshold", "x"],
        "state-dir-only": ["--transcript", t, "--spec", s, "--state-dir", str(tmp_path / "st")],
        "run-id-only": ["--transcript", t, "--spec", s, "--run-id", "r"],
        "bad-vocab": ["--transcript", t, "--spec", s, "--vocab", str(bad_vocab)],
    }
    for label, argv in cases.items():
        proc = _cli(*argv)
        # Red if: the validation for this specific flag were dropped (rc 0 or a traceback rc 1).
        assert proc.returncode == 2, f"G16: {label}: rc 2, got {proc.returncode}; stderr={proc.stderr[-200:]!r}"
        assert proc.stdout == "", f"G16: {label}: stdout must be empty"
        assert proc.stderr.strip() != "", f"G16: {label}: stderr message required"


def test_G17_cli_stdout_is_one_json_line_with_exact_keys(tmp_path: Path) -> None:
    """G17: one JSON line, keys exactly calls/outcome/phrase/threshold, for six outcomes."""
    spec = _spec(tmp_path)
    work = _transcript(_bashes(10), "Done.")
    scenarios = {
        "no-spec": (work, tmp_path / "missing.md"),
        "no-turn": ([], spec),
        "no-claim": (_transcript(_bashes(10), "Still working."), spec),
        "mentioned": (_transcript(_bashes(10), "Done, see `spec.md`."), spec),
        "clear": (_transcript(_bashes(2), "Done."), spec),
        "fire": (work, spec),
    }
    for expected, (entries, spec_path) in scenarios.items():
        transcript = tmp_path / f"{expected}.jsonl"
        _write_jsonl(transcript, entries)
        proc = _cli("--transcript", str(transcript), "--spec", str(spec_path))
        v = _verdict(proc, f"G17-{expected}")
        # Red if: any outcome path added or dropped a key (e.g. 'runner', or omitted 'calls' when None).
        assert set(v) == {"calls", "outcome", "phrase", "threshold"}, f"G17: {expected} keys {sorted(v)}"
        assert v["outcome"] == expected, f"G17: expected {expected}, got {v!r}"
        assert v["threshold"] == 10, f"G17: {expected} threshold echoed"
        assert proc.stdout.endswith("\n") and proc.stdout.count("\n") == 1, f"G17: {expected} one line"
    # latched: pre-stage the latch with a first firing run, then the second run is latched.
    state = tmp_path / "latch-state"
    transcript = tmp_path / "fire.jsonl"
    args = ("--transcript", str(transcript), "--spec", str(spec),
            "--state-dir", str(state), "--run-id", "g17")
    assert _verdict(_cli(*args), "G17-latch-prime")["outcome"] == "fire"
    proc = _cli(*args)
    v = _verdict(proc, "G17-latched")
    # Red if: the latched path built its dict with different keys or printed more than one line.
    assert set(v) == {"calls", "outcome", "phrase", "threshold"}, f"G17: latched keys {sorted(v)}"
    assert v["outcome"] == "latched" and v["threshold"] == 10, f"G17: latched -> {v!r}"
    assert proc.stdout.endswith("\n") and proc.stdout.count("\n") == 1, "G17: latched one line"


# --------------------------------------------------------------------------
# G18 registration
# --------------------------------------------------------------------------

def test_G18_registered_clean_and_reuses_claim_phrase() -> None:
    """G18: manifest + mypy list; no Cyrillic / HAL_; claim_phrase is claim_evidence's."""
    manifest = json.loads((ENGINE_PY / "core_manifest.json").read_text(encoding="utf-8"))
    assert "close_gate.py" in manifest["core_modules"], "G18: missing from core_modules"
    strict = (ENGINE_PY / "bytedigger_engine" / "mypy-strict-modules.txt").read_text(
        encoding="utf-8").splitlines()
    assert "close_gate.py" in [ln.strip() for ln in strict], "G18: missing from mypy strict list"
    assert MODULE_FILE.exists(), "G18: close_gate.py must exist"
    source = MODULE_FILE.read_text(encoding="utf-8")
    cyrillic = "[" + chr(0x400) + "-" + chr(0x4FF) + "]"
    assert re.search(cyrillic, source) is None, "G18: Cyrillic char in module"
    assert "HAL_" not in source, "G18: HAL_ substring in module"
    ce = importlib.import_module("bytedigger_engine.claim_evidence")
    cg = _cg()
    names = ("Vocabulary", "DEFAULT_VOCABULARY", "load_vocabulary", "slice_current_turn",
             "final_assistant_text", "strip_quoted_text", "claim_phrase")
    # Red if: close_gate re-implemented any of these (a second claim pipeline) instead of importing it.
    assert hasattr(cg, "claim_phrase"), "G18: close_gate must expose claim_phrase"
    for name in names:
        if hasattr(cg, name):
            assert getattr(cg, name) is getattr(ce, name), f"G18: {name} must be the claim_evidence object"
    proc = subprocess.run(
        [sys.executable, "core-boundary-lint.py", "--json"],
        cwd=str(ENGINE_PY.parent), capture_output=True, text=True, timeout=120,
    )
    report = json.loads(proc.stdout)
    # Red if: close_gate.py breaks a core-boundary rule (non-stdlib import, HAL coupling).
    assert "close_gate.py" not in json.dumps(report.get("violations", [])), (
        f"G18: boundary violation for close_gate.py: {report.get('violations')}")


# --------------------------------------------------------------------------
# G19-G22 error paths and counting unit (real CLI subprocess)
# --------------------------------------------------------------------------

def test_G19_latch_oserror_other_than_exists_still_fires(tmp_path: Path) -> None:
    """G19: state dir under a regular file -> exit 0, fire, no traceback."""
    spec = _spec(tmp_path)
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, _transcript(_bashes(10), "Done."))
    proc = _cli("--transcript", str(transcript), "--spec", str(spec),
                "--state-dir", str(blocker / "sub"), "--run-id", "r")
    v = _verdict(proc, "G19")
    # Red if: latch() let the OSError (NotADirectoryError) escape -> rc 1 and a traceback.
    assert v["outcome"] == "fire" and v["calls"] == 10, f"G19: {v!r}"
    assert "Traceback" not in proc.stderr, f"G19: traceback on stderr: {proc.stderr[-300:]!r}"


def test_G20_malformed_lines_are_skipped_not_counted(tmp_path: Path) -> None:
    """G20: junk lines interleaved in a firing fixture -> fire, calls == 10."""
    spec = _spec(tmp_path)
    lines = [json.dumps(e) for e in _transcript(_bashes(10), "Done.")]
    junk = ["this is not json {", json.dumps([1, 2]), json.dumps({"type": "assistant"}),
            json.dumps({"type": "assistant", "message": {"content": "plain string"}})]
    mixed: list[str] = []
    for i, ln in enumerate(lines):
        mixed.append(ln)
        if i < len(junk) * 3 and i % 3 == 1:
            mixed.append(junk[(i // 3) % len(junk)])
    mixed.extend(j for j in junk if j not in mixed)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(mixed) + "\n", encoding="utf-8")
    proc = _cli("--transcript", str(transcript), "--spec", str(spec))
    v = _verdict(proc, "G20")
    # Red if: a bad line crashed the reader or was counted as a call.
    assert v["outcome"] == "fire" and v["calls"] == 10, f"G20: {v!r}"
    assert "Traceback" not in proc.stderr, "G20: traceback on stderr"


def test_G21_unpaired_spec_edit_is_a_touch(tmp_path: Path) -> None:
    """G21: Edit with no tool_result counts as a touch only when it names the spec."""
    spec = _spec(tmp_path)
    other = _spec(tmp_path, "other.md")

    def unpaired(target: Path) -> list[dict]:
        steps = _with(_bashes(12), 1, _tool_step("e1", "Edit", str(target)))
        entries = _transcript(steps, "Done.")
        return [e for e in entries
                if not (e["type"] == "user" and isinstance(e["message"]["content"], list)
                        and e["message"]["content"][0].get("tool_use_id") == "e1")]

    v = _run(tmp_path, unpaired(spec), spec, label="G21-spec")
    # Red if: a touch required a paired non-error tool_result (count would stay 12).
    assert v["outcome"] == "fire" and v["calls"] == 10, f"G21: unpaired spec edit -> {v!r}"
    v = _run(tmp_path, unpaired(other), spec, label="G21-other")
    # Red if: any unpaired Edit were treated as a touch regardless of file (calls would be 10).
    assert v["outcome"] == "fire" and v["calls"] == 12, f"G21: unpaired other edit -> {v!r}"


def test_G22_blocks_are_counted_not_entries(tmp_path: Path) -> None:
    """G22: 2 assistant entries x 5 Bash blocks -> calls == 10."""
    spec = _spec(tmp_path)
    entries: list[dict] = [_user("please finish the work")]
    for e in range(2):
        blocks = [_bash_step(f"p{e}-{i}")["block"] for i in range(5)]
        entries.append(_assistant(tool_uses=blocks))
        for b in blocks:
            entries.append(_result(b["id"]))
    entries.append(_assistant("Done."))
    v = _run(tmp_path, entries, spec, label="G22")
    # Red if: entries containing tool_use were counted instead of blocks (calls would be 2).
    assert v["outcome"] == "fire" and v["calls"] == 10, f"G22: {v!r}"


# --------------------------------------------------------------------------
# Direct calls to the chokepoint API
# --------------------------------------------------------------------------

def test_evaluate_direct_call_returns_verdict(tmp_path: Path) -> None:
    """evaluate(): the chokepoint returns the verdict dict without the CLI."""
    spec = _spec(tmp_path)
    cg = _cg()
    v = cg.evaluate(_transcript(_bashes(10), "Done."), str(spec))
    # Red if: the logic lived only in main() (no evaluate) or the Verdict shape differed.
    assert dict(v) == {"outcome": "fire", "phrase": "Done", "calls": 10, "threshold": 10}, f"{v!r}"
    v3 = cg.evaluate(_transcript(_bashes(2), "Done."), str(spec), threshold=3)
    assert dict(v3) == {"outcome": "clear", "phrase": "Done", "calls": 2, "threshold": 3}, f"{v3!r}"


def test_latch_direct_call_true_then_false(tmp_path: Path) -> None:
    """latch(): True when it creates the file, False on the same run id afterwards."""
    cg = _cg()
    state = tmp_path / "a" / "b"
    # Red if: latch() returned True twice (no O_EXCL) or never created the file.
    assert cg.latch(str(state), "run-x") is True
    assert (state / _latch_name("run-x")).is_file(), "latch file named by sha256(run_id)[:16]"
    assert cg.latch(str(state), "run-x") is False
    assert cg.latch(str(state), "run-y") is True
