"""RED tests for bd#141 op1 -- claim-vs-evidence check (``claim_evidence``).

Spec: docs/decisions/2026-10-01-bd141-claim-evidence-loop-detector.md
(ACs C1-C18, plus B1/B2 for ``claim_evidence.py`` only).

The module under test does not exist yet. It is reached lazily through
``_ce()`` inside every test, so this file collects and each AC fails at
assert time independently of the others (workflows.md 1q). Nothing here
mocks the unit under test; C17 runs a real subprocess.
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import pytest
from pathlib import Path
from typing import Any

ENGINE_PY = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_PY.parent
MODULE_FILE = ENGINE_PY / "bytedigger_engine" / "claim_evidence.py"


def _ce() -> Any:
    """Lazy import of the unit under test (fails at assert time, not collect)."""
    return importlib.import_module("bytedigger_engine.claim_evidence")


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


def _bash(tool_id: str, command: str, **inp: Any) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Bash",
            "input": {"command": command, **inp}}


def _result(tool_id: str, content: Any, is_error: bool = False, **extra: Any) -> dict:
    return {
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": tool_id,
            "content": content, "is_error": is_error,
        }]},
        **extra,
    }


def _turn(final: str, *run_pairs: tuple[str, str, str, bool]) -> list[dict]:
    """One user prompt, then (id, command, output, is_error) runs, then final text."""
    entries: list[dict] = [_user("please finish the work")]
    for tid, cmd, out, err in run_pairs:
        entries.append(_assistant(tool_uses=[_bash(tid, cmd)]))
        entries.append(_result(tid, out, err))
    entries.append(_assistant(final))
    return entries


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _claim(text: str) -> Any:
    ce = _ce()
    return ce.claim_phrase(text, ce.DEFAULT_VOCABULARY)


# --------------------------------------------------------------------------
# C1-C6 claim detection
# --------------------------------------------------------------------------

def test_c1_claim_phrase_matches_plain_claims() -> None:
    """C1: plain claims return the matched text in original case."""
    assert _claim("All tests pass.") == "All tests pass", "C1: 'All tests pass.' phrase"
    assert _claim("Done") == "Done", "C1: bare 'Done' phrase"


def test_c2_negated_claims_return_none() -> None:
    """C2: negation in the clause before the claim suppresses it."""
    assert _claim("This is not done yet") is None, "C2: 'not done' negated"
    assert _claim("I will make the tests pass") is None, "C2: 'will ... pass' negated"
    assert _claim("Not yet. Done.") == "Done", "C2: negation is clause-scoped, not whole-prefix"


def test_c3_questions_return_none() -> None:
    """C3: a claim inside a clause ending with '?' is skipped."""
    assert _claim("Is it done?") is None, "C3: question clause"
    assert _claim("Done? No.") is None, "C3: 'Done?' clause"


def test_c4_downgrades_return_none() -> None:
    """C4: an honest downgrade anywhere voids the claim."""
    assert _claim("Done, except 1 failing test") is None, "C4: except + failing test"
    assert _claim("all green (known red #12)") is None, "C4: known red"
    assert _claim("done; 2 failed on main") is None, "C4: N failed"


def test_c5_word_boundary() -> None:
    """C5: claim words embedded in longer words do not match."""
    assert _claim("abandoned") is None, "C5: 'abandoned' must not match done"
    assert _claim("undone") is None, "C5: 'undone' must not match done"


def test_c6_strip_quoted_text_hides_quoted_claims() -> None:
    """C6: fenced blocks, quote lines and inline code are removed before matching."""
    ce = _ce()
    fenced = "text\n```\nall tests pass\n```\nend"
    tilde = "text\n~~~\ndone\n~~~\nend"
    quoted = "> done\nnothing else"
    inline = "the word `done` here"
    for label, txt in (("fence", fenced), ("tilde", tilde), ("quote", quoted), ("inline", inline)):
        stripped = ce.strip_quoted_text(txt)
        assert ce.claim_phrase(stripped, ce.DEFAULT_VOCABULARY) is None, (
            f"C6: claim inside {label} must be stripped, got {stripped!r}")
    assert "done" in ce.strip_quoted_text("really done"), "C6: unquoted text is kept"


# --------------------------------------------------------------------------
# C7-C9 runner / red detection
# --------------------------------------------------------------------------

def test_c7_runner_id() -> None:
    """C7: runner_id parses env assignments, timeout, pipes; rejects look-alikes."""
    ce = _ce()
    assert ce.runner_id("cd x && CI=1 timeout 60s bun test a.ts") == "bun test", "C7: chain"
    assert ce.runner_id("echo pytest") is None, "C7: echo is not a runner"
    assert ce.runner_id("python3 -m pytest -q | tail") == "python3 -m pytest", "C7: pipe"
    assert ce.runner_id("bun testx") is None, "C7: trailing word char rejects"


def test_c8_text_is_red_true_cases() -> None:
    """C8: red patterns."""
    ce = _ce()
    for line in (" 3 fail", "(fail) suite > case [1.2ms]",
                 "==== 2 failed, 5 passed in 1s ====", "FAILED tests/x.py::t",
                 "Exit code 1", "ci\tfail\t2m", '"conclusion": "failure"'):
        assert ce.text_is_red(line) is True, f"C8: {line!r} must be red"


def test_c8_text_is_red_false_cases() -> None:
    """C8: non-red patterns."""
    ce = _ce()
    for line in (" 0 fail", "(pass) a (fail) b", "12 pass",
                 "3 FAIL", "failed to fetch", "EXIT CODE 1", "Conclusion: Failure"):
        assert ce.text_is_red(line) is False, f"C8: {line!r} must not be red"


def test_c9_bun_fail_identity() -> None:
    """C9: failure identity extraction with and without gh log prefix."""
    ce = _ce()
    assert ce.bun_fail_identity("(fail) a > b [3.10ms]") == "a > b", "C9: plain"
    assert ce.bun_fail_identity("job\tstep\t2026-10-01T00:00:00Z (fail) x") == "x", "C9: gh prefix"
    assert ce.bun_fail_identity("(fail) x") == "x", "C9: ms suffix is optional"


# --------------------------------------------------------------------------
# C10-C15 evaluate_turn
# --------------------------------------------------------------------------

def test_c10_claim_with_red_runner_fires() -> None:
    """C10: claim + red bun test result in the turn -> fire."""
    ce = _ce()
    entries = _turn("All tests pass.", ("t1", "bun test", "(fail) a > b [1ms]\n 1 fail", False))
    assert ce.evaluate_turn(entries) == {
        "outcome": "fire", "phrase": "All tests pass", "runner": "bun test",
    }, "C10: expected fire verdict"


def test_c11_green_rerun_clears_and_other_runner_red_fires() -> None:
    """C11: later green rerun replaces red; red pytest + green bun test -> fire pytest."""
    ce = _ce()
    rerun = _turn("Done", ("t1", "bun test", "(fail) a [1ms]", False),
                  ("t2", "bun test", "12 pass\n 0 fail", False))
    assert ce.evaluate_turn(rerun)["outcome"] == "clear", "C11: green rerun clears"
    mixed = _turn("Done", ("t1", "pytest -q", "FAILED tests/x.py::t", False),
                  ("t2", "bun test", "12 pass\n 0 fail", False))
    verdict = ce.evaluate_turn(mixed)
    assert verdict["outcome"] == "fire", "C11: red pytest still fires"
    assert verdict["runner"] == "pytest", "C11: runner is pytest"


def test_c12_non_registering_runs_yield_no_runner() -> None:
    """C12: background run, non-Bash tool, orphan result id -> no-runner."""
    ce = _ce()
    red = "(fail) a [1ms]\n 1 fail"
    background = [_user("go"),
                  _assistant(tool_uses=[_bash("t1", "bun test", run_in_background=True)]),
                  _result("t1", red), _assistant("Done")]
    assert ce.evaluate_turn(background)["outcome"] == "no-runner", "C12: background"
    non_bash = [_user("go"),
                _assistant(tool_uses=[{"type": "tool_use", "id": "t1", "name": "Read",
                                       "input": {"command": "bun test"}}]),
                _result("t1", red), _assistant("Done")]
    assert ce.evaluate_turn(non_bash)["outcome"] == "no-runner", "C12: non-Bash tool"
    orphan = [_user("go"), _result("zzz", red), _assistant("Done")]
    assert ce.evaluate_turn(orphan)["outcome"] == "no-runner", "C12: orphan result"


def test_c13_is_error_semantics() -> None:
    """C13: gh is_error with pending text is clear; bun test is_error with empty text fires."""
    ce = _ce()
    gh = _turn("All tests pass.", ("t1", "gh pr checks 12", "build pending", True))
    assert ce.evaluate_turn(gh)["outcome"] == "clear", "C13: gh is_error pending -> clear"
    bun = _turn("All tests pass.", ("t1", "bun test", "", True))
    verdict = ce.evaluate_turn(bun)
    assert verdict["outcome"] == "fire", "C13: bun is_error empty text -> fire"
    assert verdict["runner"] == "bun test", "C13: runner reported"


def test_c14_turn_boundary() -> None:
    """C14: red in a previous turn is ignored; tool_result-only user entry is not a boundary."""
    ce = _ce()
    previous = [
        _user("first request"),
        _assistant(tool_uses=[_bash("t1", "bun test")]),
        _result("t1", "(fail) a [1ms]\n 1 fail"),
        _assistant("looking into it"),
        _user("second real request"),
        _assistant("Done"),
    ]
    assert ce.evaluate_turn(previous)["outcome"] == "no-runner", "C14: previous-turn red ignored"
    same_turn = _turn("Done", ("t1", "bun test", "(fail) a [1ms]\n 1 fail", False))
    assert ce.evaluate_turn(same_turn)["outcome"] == "fire", "C14: tool_result keeps the turn"
    assert ce.turn_boundary_index(previous) == 4, "C14: boundary is last real user entry"
    assert ce.turn_boundary_index([_assistant("x")]) == -1, "C14: no user -> -1"


def test_c15_sidechain_no_claim_no_turn_and_keys() -> None:
    """C15: sidechain ignored; no-claim; no-turn; exact keys."""
    ce = _ce()
    red = "(fail) a [1ms]\n 1 fail"
    entries = [
        _user("go"),
        _assistant(tool_uses=[_bash("s1", "bun test")], isSidechain=True),
        _result("s1", red, isSidechain=True),
        _assistant("Done"),
    ]
    assert ce.evaluate_turn(entries)["outcome"] == "no-runner", "C15: sidechain runner ignored"
    sidechain_claim = [_user("go"), _assistant("working on it"),
                       _assistant("Done", isSidechain=True)]
    assert ce.evaluate_turn(sidechain_claim)["outcome"] == "no-claim", "C15: sidechain claim ignored"
    assert ce.slice_current_turn([]) == [], "C15: empty slice"
    empty = ce.evaluate_turn([])
    assert empty == {"outcome": "no-turn", "phrase": None, "runner": None}, "C15: empty -> no-turn"
    plain = ce.evaluate_turn([_user("go"), _assistant("here is some text")])
    assert plain["outcome"] == "no-claim", "C15: no claim"
    assert set(plain) == {"outcome", "phrase", "runner"}, "C15: exact keys"
    assert ce.final_assistant_text([_assistant("a"), _assistant("b")]) == "b", "C15: last text"


# --------------------------------------------------------------------------
# C16 vocabulary
# --------------------------------------------------------------------------

# U+0433 U+043E U+0442 U+043E U+0432 U+043E, written as escapes so this file stays ASCII.
_HOST_TOKEN_ESCAPED = "\\u0433\\u043e\\u0442\\u043e\\u0432\\u043e"
_HOST_TOKEN = json.loads('"' + _HOST_TOKEN_ESCAPED + '"')


def test_c16_extended_keeps_order_and_drops_duplicates() -> None:
    """C16: Vocabulary.extended ordering/dedupe."""
    ce = _ce()
    base = ce.Vocabulary(claim=("a", "b"), downgrade=("d",), negation=("n",), runner=("r",))
    other = ce.Vocabulary(claim=("b", "c"), downgrade=("d", "e"), negation=(), runner=("r2",))
    merged = base.extended(other)
    assert merged.claim == ("a", "b", "c"), "C16: claim order + dedupe"
    assert merged.downgrade == ("d", "e"), "C16: downgrade dedupe"
    assert merged.negation == ("n",), "C16: empty other keeps base"
    assert merged.runner == ("r", "r2"), "C16: runner appended"


def test_c16_load_vocabulary_adds_non_ascii_host_claim(tmp_path: Path) -> None:
    """C16: a host claim token from JSON (\\u escape) is not default and then fires."""
    ce = _ce()
    vocab_path = tmp_path / "vocab.json"
    vocab_path.write_text('{"claim": ["' + _HOST_TOKEN_ESCAPED + '"]}', encoding="ascii")
    assert ce.claim_phrase(_HOST_TOKEN, ce.DEFAULT_VOCABULARY) is None, (
        "C16: host token is not in the default vocabulary")
    vocab = ce.load_vocabulary(vocab_path)
    assert vocab.claim[-len(ce.DEFAULT_VOCABULARY.claim) - 1:][:len(ce.DEFAULT_VOCABULARY.claim)] \
        == ce.DEFAULT_VOCABULARY.claim, "C16: defaults come first"
    assert ce.claim_phrase(_HOST_TOKEN, vocab) == _HOST_TOKEN, "C16: host token now claims"
    entries = _turn(_HOST_TOKEN, ("t1", "bun test", "(fail) a [1ms]", False))
    assert ce.evaluate_turn(entries, vocab)["outcome"] == "fire", "C16: host claim fires"


def test_c16_load_vocabulary_rejects_bad_input(tmp_path: Path) -> None:
    """C16: ValueError for bad JSON, unknown key, non-list value, uncompilable regex."""
    ce = _ce()
    bad_payloads = {
        "bad-json": "{not json",
        "non-object": "[1, 2]",
        "unknown-key": '{"bogus": ["x"]}',
        "non-list": '{"claim": "done"}',
        "non-string-item": '{"claim": [1]}',
        "bad-regex": '{"claim": ["(unclosed"]}',
    }
    for label, payload in bad_payloads.items():
        path = tmp_path / f"{label}.json"
        path.write_text(payload, encoding="utf-8")
        try:
            ce.load_vocabulary(path)
        except ValueError:
            continue
        raise AssertionError(f"C16: {label} must raise ValueError")
    try:
        ce.load_vocabulary(tmp_path / "missing.json")
    except ValueError:
        pass
    else:
        raise AssertionError("C16: unreadable file must raise ValueError")


@pytest.mark.skipif(sys.version_info < (3, 11), reason="non-leading inline flags are an error only on 3.11+")
def test_c16_load_vocabulary_rejects_pattern_invalid_only_when_joined(tmp_path: Path) -> None:
    """C16 r3 — spec load_vocabulary: joined field pattern must compile."""
    ce = _ce()
    vocab_path = tmp_path / "vocab.json"
    vocab_path.write_text('{"claim": ["(?i)shipped"]}', encoding="utf-8")
    with pytest.raises(ValueError):
        ce.load_vocabulary(vocab_path)


# --------------------------------------------------------------------------
# C17 CLI (real subprocess), C18 transcript reader
# --------------------------------------------------------------------------

def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.claim_evidence", *args],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=60,
    )


def test_c17_cli_matches_evaluate_transcript(tmp_path: Path) -> None:
    """C17: CLI stdout is one JSON line equal to evaluate_transcript, rc 0."""
    ce = _ce()
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, _turn("All tests pass.",
                                   ("t1", "bun test", "(fail) a [1ms]\n 1 fail", False)))
    proc = _cli("--transcript", str(transcript))
    assert proc.returncode == 0, f"C17: rc 0, stderr={proc.stderr!r}"
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, f"C17: exactly one stdout line, got {lines!r}"
    assert json.loads(lines[0]) == ce.evaluate_transcript(transcript), "C17: equals library verdict"
    assert json.loads(lines[0])["outcome"] == "fire", "C17: fire verdict"


def test_c17_cli_missing_file_is_no_turn(tmp_path: Path) -> None:
    """C17: missing transcript -> no-turn, rc 0."""
    proc = _cli("--transcript", str(tmp_path / "absent.jsonl"))
    assert proc.returncode == 0, f"C17: rc 0, stderr={proc.stderr!r}"
    assert json.loads(proc.stdout)["outcome"] == "no-turn", "C17: missing file -> no-turn"


def test_c17_cli_bad_vocab_exits_2(tmp_path: Path) -> None:
    """C17: bad --vocab -> rc 2, empty stdout, stderr message."""
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [_user("go"), _assistant("Done")])
    vocab = tmp_path / "bad.json"
    vocab.write_text('{"bogus": []}', encoding="utf-8")
    proc = _cli("--transcript", str(transcript), "--vocab", str(vocab))
    assert proc.returncode == 2, f"C17: rc 2, got {proc.returncode}"
    assert proc.stdout == "", "C17: stdout empty on bad vocab"
    assert proc.stderr.strip() != "", "C17: stderr message present"


def test_c17_cli_missing_transcript_arg_exits_2() -> None:
    """C17: no --transcript -> rc 2, empty stdout, stderr message."""
    proc = _cli()
    assert proc.returncode == 2, f"C17: rc 2, got {proc.returncode}; stderr={proc.stderr[-300:]!r}"
    assert proc.stdout == "", "C17: stdout empty without --transcript"
    assert proc.stderr.strip() != "", "C17: stderr message present"


def test_c18_evaluate_transcript_skips_blank_and_non_json_lines(tmp_path: Path) -> None:
    """C18: blank lines, non-JSON lines and non-object lines are skipped."""
    ce = _ce()
    good = _turn("All tests pass.", ("t1", "bun test", "(fail) a [1ms]\n 1 fail", False))
    lines = [json.dumps(good[0]), "", "this is not json", "[1, 2]", "   "]
    lines += [json.dumps(e) for e in good[1:]]
    path = tmp_path / "noisy.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ce.evaluate_transcript(path)["outcome"] == "fire", "C18: noise lines skipped"


# --------------------------------------------------------------------------
# B1 / B2
# --------------------------------------------------------------------------

def test_b1_registered_in_manifest_mypy_list_and_boundary_lint_ok() -> None:
    """B1: listed in core_modules and mypy-strict-modules.txt; boundary lint ok."""
    manifest = json.loads((ENGINE_PY / "core_manifest.json").read_text(encoding="utf-8"))
    assert "claim_evidence.py" in manifest["core_modules"], "B1: missing from core_modules"
    strict = (ENGINE_PY / "bytedigger_engine" / "mypy-strict-modules.txt").read_text(
        encoding="utf-8").splitlines()
    assert "claim_evidence.py" in [ln.strip() for ln in strict], "B1: missing from mypy strict list"
    proc = subprocess.run(
        [sys.executable, "core-boundary-lint.py", "--json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    report = json.loads(proc.stdout)
    assert report["ok"] is True, f"B1: boundary lint not ok: {report.get('violations')}"


def test_b2_no_cyrillic_and_no_hal_prefix() -> None:
    """B2: module source has no U+0400-U+04FF char and no 'HAL_' substring."""
    assert MODULE_FILE.exists(), "B2: claim_evidence.py must exist"
    source = MODULE_FILE.read_text(encoding="utf-8")
    cyrillic = "[" + chr(0x400) + "-" + chr(0x4FF) + "]"
    assert re.search(cyrillic, source) is None, "B2: Cyrillic char in module"
    assert "HAL_" not in source, "B2: HAL_ substring in module"
