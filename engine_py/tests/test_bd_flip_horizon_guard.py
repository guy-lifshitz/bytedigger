"""RED tests: self-expiring guard for dated rollout horizons (flip-by / kill-by / retire-by).

Spec: docs/decisions/2026-10-03-bd-flip-horizon-guard.md (AC1..AC8).
Unit under test: scripts/flip_horizon.py (loaded by path; scripts/ is not a package).
"""
import datetime
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "flip_horizon.py"
LEDGER = ROOT / "scripts" / "flip_horizon_ledger.json"

TODAY = datetime.date(2026, 10, 3)


@pytest.fixture(scope="module")
def fh():
    assert SCRIPT.is_file(), f"missing production script: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("flip_horizon_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _flags(**descs):
    return {k: {"kind": "env", "default": "0", "module": "m", "description": v}
            for k, v in descs.items()}


def _entry(until, ref="hal#1", reason="why not flipped"):
    return {"until": until, "ref": ref, "reason": reason}


def _lines_for(lines, flag, code=None):
    return [ln for ln in lines
            if flag in ln and (code is None or ln.startswith(code))]


# ---------------------------------------------------------------- AC1
def test_ac1_find_tokens_all_three_kinds(fh):
    flags = _flags(
        A="warn-only; flip-by:2026-08-01 then enforce",
        B="temp shim kill-by:2026-09-01",
        C="legacy retire-by:2026-12-31 .",
        D="no token here, mentions flip-by soon and 2026-08-01",
    )
    got = set(fh.find_tokens(flags))
    assert got == {
        ("A", "flip-by", "2026-08-01"),
        ("B", "kill-by", "2026-09-01"),
        ("C", "retire-by", "2026-12-31"),
    }


# ---------------------------------------------------------------- AC2
def test_ac2_uncovered_then_covered(fh):
    flags = _flags(A="flip-by:2026-08-01")
    out = fh.check(flags, {}, TODAY)
    assert len(out) == 1
    assert out[0].startswith("UNCOVERED") and "A" in out[0]
    assert fh.check(flags, {"A": _entry("2026-10-20")}, TODAY) == []


# ---------------------------------------------------------------- AC3
def test_ac3_lapsed_fires_once(fh):
    flags = _flags(A="flip-by:2026-08-01")
    out = fh.check(flags, {"A": _entry("2026-10-02")}, TODAY)
    assert len(_lines_for(out, "A", "LAPSED")) == 1
    assert len(out) == 1


def test_ac3_too_far_boundary_45_vs_46(fh):
    flags = _flags(A="flip-by:2026-08-01")
    ok = TODAY + datetime.timedelta(days=45)
    bad = TODAY + datetime.timedelta(days=46)
    assert fh.check(flags, {"A": _entry(ok.isoformat())}, TODAY) == []
    out = fh.check(flags, {"A": _entry(bad.isoformat())}, TODAY)
    assert len(_lines_for(out, "A", "TOO_FAR")) == 1
    assert len(out) == 1


def test_ac3_no_reason_blank_and_missing_ref(fh):
    flags = _flags(A="flip-by:2026-08-01", B="kill-by:2026-08-01")
    ledger = {
        "A": {"until": "2026-10-20", "ref": "hal#1", "reason": "   "},
        "B": {"until": "2026-10-20", "reason": "has reason"},
    }
    out = fh.check(flags, ledger, TODAY)
    assert len(_lines_for(out, "A", "NO_REASON")) == 1
    assert len(_lines_for(out, "B", "NO_REASON")) == 1
    assert len(out) == 2


def test_ac3_bad_date_fires_once(fh):
    flags = _flags(A="flip-by:2026-08-01")
    out = fh.check(flags, {"A": _entry("2026-02-31")}, TODAY)
    assert len(_lines_for(out, "A", "BAD_DATE")) == 1
    assert len(out) == 1


# ---------------------------------------------------------------- AC4
def test_ac4_stale_future_token_and_absent_token(fh):
    flags = _flags(F="flip-by:2026-12-01", N="no token at all")
    ledger = {"F": _entry("2026-10-20"), "N": _entry("2026-10-20")}
    out = fh.check(flags, ledger, TODAY)
    assert len(_lines_for(out, "F", "STALE")) == 1
    assert len(_lines_for(out, "N", "STALE")) == 1


# ---------------------------------------------------------------- AC5
def test_ac5_bad_token_no_exception(fh):
    flags = _flags(A="flip-by:2026-13-40")
    out = fh.check(flags, {}, TODAY)
    assert len(_lines_for(out, "A", "BAD_TOKEN")) == 1
    toks = fh.find_tokens(flags)
    assert [t[1] for t in toks] == ["flip-by"]


# ---------------------------------------------------------------- AC6
def test_ac6_exit_0_clean_missing_ledger(fh, tmp_path):
    missing = tmp_path / "absent.json"
    assert fh.main(["--check", "--today", "2026-01-01"], ledger_path=missing) == 0


def test_ac6_exit_0_clean_empty_ledger(fh, tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text("{}")
    assert fh.main(["--check", "--today", "2026-01-01"], ledger_path=p) == 0


def test_ac6_exit_1_on_problem_with_line_on_stdout(fh, tmp_path, capsys):
    p = tmp_path / "ledger.json"
    p.write_text("{}")
    rc = fh.main(["--check", "--today", "2099-01-01"], ledger_path=p)
    assert rc == 1
    out = capsys.readouterr().out
    assert "UNCOVERED" in out


def test_ac6_exit_2_on_malformed_ledger(fh, tmp_path, capsys):
    p = tmp_path / "ledger.json"
    p.write_text("{not json")
    rc = fh.main(["--check", "--today", "2026-01-01"], ledger_path=p)
    assert rc == 2
    assert capsys.readouterr().err.strip() != ""


# ---------------------------------------------------------------- AC7
def _run_cli(*extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--check", *extra],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )


def test_ac7_real_catalog_real_ledger_real_today_clean():
    r = _run_cli()
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


def test_ac7_far_future_fails_with_uncovered_or_lapsed():
    r = _run_cli("--today", "2099-01-01")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert any(ln.startswith(("UNCOVERED", "LAPSED")) for ln in r.stdout.splitlines())


# ---------------------------------------------------------------- AC8
def test_ac8_committed_ledger_valid_and_subset_of_token_flags(fh):
    ledger = json.loads(LEDGER.read_text())
    assert ledger, "committed ledger must not be empty"
    for flag, e in ledger.items():
        assert isinstance(e.get("reason"), str) and e["reason"].strip(), flag
        assert isinstance(e.get("ref"), str) and e["ref"].strip(), flag
        datetime.date.fromisoformat(e["until"])
    from bytedigger_engine.flags_catalog import FLAGS
    token_flags = {t[0] for t in fh.find_tokens(FLAGS)}
    assert set(ledger) <= token_flags


# ---------------------------------------------------------------- §4 edges
def test_edge_today_equal_token_date_not_overdue(fh):
    flags = _flags(A="flip-by:2026-10-03")
    assert fh.check(flags, {}, TODAY) == []


def test_edge_one_overdue_one_future_needs_coverage(fh):
    flags = _flags(A="flip-by:2026-08-01 and kill-by:2026-12-01")
    out = fh.check(flags, {}, TODAY)
    assert len(_lines_for(out, "A", "UNCOVERED")) == 1


def test_edge_ledger_key_not_in_flags_is_stale(fh):
    out = fh.check(_flags(A="nothing"), {"GONE": _entry("2026-10-20")}, TODAY)
    assert len(_lines_for(out, "GONE", "STALE")) == 1
