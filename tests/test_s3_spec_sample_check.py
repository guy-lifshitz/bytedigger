"""RED tests for scripts/spec_sample_check.py (S3, spec
docs/decisions/2026-10-03-s3-spec-sample-check.md, AC1-AC12).

The script under test does not exist yet. Per workflows.md section 1q nothing
here imports it: every test resolves the path lazily and invokes it as a
subprocess (sys.executable) inside the test body, so collection succeeds and
failure happens at assert time. Repo root is the parent of this tests/ dir.

Hermetic: git repos are built under tmp_path (git init, user config, a base
commit holding real sample files, then the "lot" changes left uncommitted).
The env is built from scratch: PATH is tmp bin dir plus /usr/bin:/bin (no
claude, no bun), ANTHROPIC_API_KEY absent, HOME is a tmp dir, and only the
tests that need it set HAL_SPEC_SAMPLE_ENFORCE. --today is always passed
explicitly (default 2026-10-01, before the 2026-10-17 flag expiry) so no test
depends on the wall clock. No singleton resources, no timing races
(workflows.md section 1i: nothing contested is used).
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "spec_sample_check.py"
FLAGS_CATALOG = REPO_ROOT / "engine_py" / "bytedigger_engine" / "flags_catalog.py"

VERDICT_KEYS = {"status", "mode", "would_block", "findings", "verified", "unverified", "flag", "reason"}
FLAG_KEYS = {"name", "owner", "expires", "expired"}
FLAG_NAME = "HAL_SPEC_SAMPLE_ENFORCE"
DEFAULT_TODAY = "2026-10-01"

PLAIN = "alpha beta gamma delta\n"  # contains no digits at all
WITH01 = "prose share 0.1 observed\n"  # contains the whole token 0.1


# ---------------------------------------------------------------- helpers --


def _write(root: Path, rel: str, content) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")


def _git_env(tmp_path: Path) -> dict:
    return {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
    }


def _git(tmp_path: Path, root: Path, *args: str) -> None:
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, env=_git_env(tmp_path)
    )


def _lot(
    tmp_path: Path,
    spec: str,
    red: str = "def test_x():\n    assert 1 == 1\n",
    base: dict | None = None,
    lot: dict | None = None,
    red_name: str = "tests/test_red.py",
    name: str = "repo",
) -> Path:
    """Real git repo: base commit with real sample files, then the lot's
    changes (spec, RED, fixtures, edits) written but NOT committed."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    base_files = {
        "samples/plain.txt": PLAIN,
        "samples/with01.txt": WITH01,
        "samples/empty.txt": "",
        "samples/mod.txt": "baseline text\n",
    }
    base_files.update(base or {})
    for rel, content in base_files.items():
        _write(root, rel, content)
    _git(tmp_path, root, "init", "-q")
    _git(tmp_path, root, "config", "user.name", "Test User")
    _git(tmp_path, root, "config", "user.email", "test@example.com")
    _git(tmp_path, root, "config", "commit.gpgsign", "false")
    _git(tmp_path, root, "add", "-A")
    _git(tmp_path, root, "commit", "-q", "-m", "base")
    lot_files = {"docs/spec.md": spec, red_name: red}
    lot_files.update(lot or {})
    for rel, content in lot_files.items():
        _write(root, rel, content)
    return root


def _ac(n, text: str, sample: str | None = None, measured=None) -> str:
    lines = [f"- AC{n}: {text}"]
    if sample is not None:
        lines.append(f"  sample: `{sample}`")
    if measured is not None:
        lines.append(f"  measured: {measured}")
    return "\n".join(lines)


def _spec(*acs: str) -> str:
    return "# Spec\n\n## Acceptance criteria\n\n" + "\n\n".join(acs) + "\n"


CONTRA_SPEC = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.31"))
CLEAN_SPEC = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.1"))


def _cmd(root, spec, red, today, base, extra) -> list:
    cmd = [sys.executable, str(SCRIPT), "--root", str(root), "--spec", str(spec), "--red", str(red)]
    if today:
        cmd += ["--today", today]
    if base:
        cmd += ["--base", base]
    return cmd + [str(x) for x in extra]


def _exec(tmp_path: Path, cmd: list, env_extra: dict | None = None):
    # An absent script makes the interpreter itself exit 2, which would
    # satisfy every fail-closed case by accident; fail loudly instead.
    assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
    (tmp_path / "bin").mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    cwd = tmp_path / "neutral-cwd"
    cwd.mkdir(exist_ok=True)
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LANG": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
    }
    env.update(env_extra or {})
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(cwd), env=env)


def _run(
    tmp_path: Path,
    root,
    spec="docs/spec.md",
    red="tests/test_red.py",
    today=DEFAULT_TODAY,
    base=None,
    extra=(),
    env_extra=None,
):
    return _exec(tmp_path, _cmd(root, spec, red, today, base, extra), env_extra)


def _run_raw(tmp_path: Path, *args: str):
    """Run the script with exactly `args` (no implicit options): usage errors."""
    return _exec(tmp_path, [sys.executable, str(SCRIPT), *args])


def _verdict(r) -> dict:
    """Parse stdout as exactly ONE JSON verdict object; check shape and that
    the exit code agrees with status and mode."""
    assert "Traceback" not in r.stderr, r.stderr
    try:
        v = json.loads(r.stdout)
    except ValueError as e:
        raise AssertionError(f"stdout is not a single JSON object: {r.stdout!r} ({e})")
    assert isinstance(v, dict) and set(v) == VERDICT_KEYS, f"bad verdict keys: {v!r}"
    assert isinstance(v["reason"], str)
    assert isinstance(v["findings"], list)
    for f in v["findings"]:
        assert set(f) == {"code", "ac", "detail"}, f
    status = v["status"]
    if status == "unavailable":
        assert r.returncode == 2, (r.returncode, v)
    elif status == "flag_expired":
        assert r.returncode == 3, (r.returncode, v)
    elif status == "clean":
        assert r.returncode == 0 and v["findings"] == [], (r.returncode, v)
    elif status == "flagged":
        assert v["findings"], v
        mode = str(v["mode"]).lower()
        assert mode in {"shadow", "enforce"}, v
        assert r.returncode == (1 if mode == "enforce" else 0), (r.returncode, v)
    else:
        raise AssertionError(f"unknown status: {v!r}")
    return v


def _codes(v: dict) -> list:
    return sorted(f["code"] for f in v["findings"])


def _snapshot(root: Path) -> dict:
    snap = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if ".git" in rel.parts or not p.is_file():
            continue
        snap[str(rel)] = p.read_bytes()
    return snap


def _red_eq(n: str) -> str:
    return f"def test_x():\n    assert compute() == {n}\n"


def _flagged_case(tmp_path: Path) -> Path:
    return _lot(tmp_path, CONTRA_SPEC)


def _clean_case(tmp_path: Path) -> Path:
    return _lot(tmp_path, CLEAN_SPEC)


class TestS3SpecSampleCheck:
    # ------------------------------------------------------------- AC1 ---
    def test_ac1_1668_shape_threshold_vs_real_measured_contradicts(self, tmp_path):
        root = _flagged_case(tmp_path)
        r = _run(tmp_path, root)
        v = _verdict(r)
        assert v["status"] == "flagged", v
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v
        f = v["findings"][0]
        assert f["ac"] == "AC1" and isinstance(f["detail"], str)
        assert r.returncode == 0  # SHADOW by default

    def test_ac1_measured_satisfies_threshold_is_clean_and_unverified(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(1, "prose share < 0.25", "samples/plain.txt", "0.1")))
        v = _verdict(_run(tmp_path, root))
        assert v["status"] == "clean" and v["findings"] == [], v
        assert [e["ac"] for e in v["unverified"]] == ["AC1"], v
        assert v["verified"] == []

    def test_ac1_measured_literally_in_sample_is_verified(self, tmp_path):
        root = _clean_case(tmp_path)  # samples/with01.txt contains the token 0.1
        v = _verdict(_run(tmp_path, root))
        assert v["status"] == "clean", v
        assert [e["ac"] for e in v["verified"]] == ["AC1"], v
        assert v["unverified"] == []

    @pytest.mark.parametrize(
        "text,measured,contradicts",
        [
            ("count >= 20", "15", True),
            ("count >= 20", "25", False),
            ("count <= 50", "60", True),
            ("count <= 50", "50", False),
            ("count > 10", "10", True),
            ("count > 10", "11", False),
            ("count < 10.5", "10.5", True),
            ("share == 0.5", "0.6", True),
            ("share == 0.5", "0.5000000001", False),
            ("share ≥ 0.25", "0.1", True),
            ("share ≤ 0.25", "0.1", False),
        ],
    )
    def test_ac1_comparison_operators_against_measured(self, tmp_path, text, measured, contradicts):
        root = _lot(tmp_path, _spec(_ac(1, text, "samples/plain.txt", measured)))
        v = _verdict(_run(tmp_path, root))
        if contradicts:
            assert _codes(v) == ["MEASURED_CONTRADICTS"], (text, measured, v)
        else:
            assert v["findings"] == [], (text, measured, v)

    def test_ac1_any_measured_value_violating_the_comparison_contradicts(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(1, "prose share < 0.25", "samples/plain.txt", "0.1, 0.31")))
        v = _verdict(_run(tmp_path, root))
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v

    # ------------------------------------------------------------- AC2 ---
    def test_ac2_numeric_ac_without_sample_is_no_sample(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(2, "prose share < 0.25")))
        v = _verdict(_run(tmp_path, root))
        assert _codes(v) == ["NO_SAMPLE"], v
        assert v["findings"][0]["ac"] == "AC2"

    @pytest.mark.parametrize(
        "text",
        [
            "rows >= 10",
            "latency <= 12.5",
            "share ≥ 0.25",
            "count == 100",
            "at least 20 files",
            "at most 15 lines",
            "not more than 40 tokens",
            "not less than 11 rows",
            "within 200 ms",
            "payload under 50 KB",
            "coverage 80%",
        ],
    )
    def test_ac2_threshold_and_unit_forms_without_sample_are_no_sample(self, tmp_path, text):
        root = _lot(tmp_path, _spec(_ac(1, text)))
        v = _verdict(_run(tmp_path, root))
        assert _codes(v) == ["NO_SAMPLE"], (text, v)

    @pytest.mark.parametrize(
        "text",
        ["count >= 1", "count == 0", "count >= 5", "at least 3 files", "at most 9 rows", "within 5 s", "x < 9"],
    )
    def test_ac2_trivial_only_ac_is_not_numeric_but_neighbour_still_flags(self, tmp_path, text):
        root = _lot(tmp_path, _spec(_ac(1, text), _ac(2, "latency within 200 ms")))
        v = _verdict(_run(tmp_path, root))
        assert [(f["code"], f["ac"]) for f in v["findings"]] == [("NO_SAMPLE", "AC2")], (text, v)

    @pytest.mark.parametrize(
        "fmt",
        ["- AC7: share < 0.25", "* AC7. share < 0.25", "1. AC7: share < 0.25", "2) AC7: share < 0.25", "**AC7**: share < 0.25"],
    )
    def test_ac2_ac_line_forms_are_recognised(self, tmp_path, fmt):
        root = _lot(tmp_path, "# Spec\n\n## Acceptance criteria\n\n" + fmt + "\n")
        v = _verdict(_run(tmp_path, root))
        assert [(f["code"], f["ac"]) for f in v["findings"]] == [("NO_SAMPLE", "AC7")], (fmt, v)

    def test_ac2_ac_block_ends_at_heading(self, tmp_path):
        spec = "# Spec\n\n- AC1: count >= 1\n\n## Notes\n\nlatency within 200 ms is only prose here\n\n- AC2: width > 15.5\n"
        root = _lot(tmp_path, spec)
        v = _verdict(_run(tmp_path, root))
        assert [(f["code"], f["ac"]) for f in v["findings"]] == [("NO_SAMPLE", "AC2")], v

    # ------------------------------------------------------------- AC3 ---
    @pytest.mark.parametrize(
        "path,lot",
        [
            ("samples/nope.txt", {}),  # missing
            ("samples/empty.txt", {}),  # empty, base-tracked
            ("samples/new.txt", {"samples/new.txt": "fresh sample 5\n"}),  # added by the lot
            ("samples/mod.txt", {"samples/mod.txt": "changed by the lot\n"}),  # tracked but modified
            ("samples", {}),  # not a regular file
        ],
    )
    def test_ac3_sample_not_real(self, tmp_path, path, lot):
        root = _lot(tmp_path, _spec(_ac(1, "share < 0.25", path, "0.1")), lot=lot)
        v = _verdict(_run(tmp_path, root))
        assert "SAMPLE_NOT_REAL" in _codes(v), (path, v)
        assert [f["ac"] for f in v["findings"] if f["code"] == "SAMPLE_NOT_REAL"] == ["AC1"]

    def test_ac3_base_tracked_unmodified_sample_is_real(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(1, "share < 0.25", "samples/plain.txt", "0.1")))
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac3_absolute_path_outside_root_is_real(self, tmp_path):
        outside = tmp_path / "outside" / "real.txt"
        outside.parent.mkdir()
        outside.write_text("observed prose share 0.1\n")
        root = _lot(tmp_path, _spec(_ac(1, "share < 0.25", str(outside), "0.1")))
        v = _verdict(_run(tmp_path, root))
        assert v["findings"] == [], v

    def test_ac3_absolute_outside_but_empty_is_not_real(self, tmp_path):
        outside = tmp_path / "outside" / "empty.txt"
        outside.parent.mkdir()
        outside.write_text("")
        root = _lot(tmp_path, _spec(_ac(1, "share < 0.25", str(outside), "0.1")))
        assert "SAMPLE_NOT_REAL" in _codes(_verdict(_run(tmp_path, root)))

    def test_ac3_comma_separated_samples_one_bad_flags(self, tmp_path):
        root = _lot(
            tmp_path, _spec(_ac(1, "share < 0.25", "samples/plain.txt, samples/nope.txt", "0.1"))
        )
        assert "SAMPLE_NOT_REAL" in _codes(_verdict(_run(tmp_path, root)))

    # ------------------------------------------------------------- AC4 ---
    def test_ac4_valid_sample_without_measured_is_no_measured(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(1, "share < 0.25", "samples/plain.txt")))
        v = _verdict(_run(tmp_path, root))
        assert _codes(v) == ["NO_MEASURED"], v
        assert v["findings"][0]["ac"] == "AC1"

    def test_ac4_percent_suffix_is_stripped_and_read_as_31(self, tmp_path):
        # 31 contradicts "< 25"; had it been read as 0.31 it would satisfy it
        root = _lot(tmp_path, _spec(_ac(1, "share < 25", "samples/plain.txt", "31%")))
        assert _codes(_verdict(_run(tmp_path, root))) == ["MEASURED_CONTRADICTS"]

    def test_ac4_percent_suffix_31_satisfies_greater_than_25(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(1, "share > 25", "samples/plain.txt", "31%")))
        assert _verdict(_run(tmp_path, root))["findings"] == []

    # ------------------------------------------------------------- AC5 ---
    def test_ac5_red_number_only_in_lot_fixture_is_flagged(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=_red_eq("0.31"),
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        v = _verdict(_run(tmp_path, root))
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v
        assert "0.31" in v["findings"][0]["detail"]

    @pytest.mark.parametrize(
        "fixture",
        ['{"share": 0.31}', "share=0.31\n", "0.31", "[0.31, 2]", '"0.31"', "ends 0.31."],
    )
    def test_ac5_whole_token_positions_that_count(self, tmp_path, fixture):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=_red_eq("0.31"),
            lot={"fixtures/x.json": fixture},
        )
        assert _codes(_verdict(_run(tmp_path, root))) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], fixture

    @pytest.mark.parametrize("fixture", ["0.310", "10.31", "v0.31", "x0.31", "0.31.2", "0.31a", "1_0.31"])
    def test_ac5_non_whole_token_occurrence_is_not_flagged(self, tmp_path, fixture):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=_red_eq("0.31"),
            lot={"fixtures/x.json": fixture + "\n"},
        )
        assert _verdict(_run(tmp_path, root))["findings"] == [], fixture

    def test_ac5_number_present_in_cited_real_sample_is_calibrated(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "share < 0.5", "samples/real31.txt", "0.1")),
            red=_red_eq("0.31"),
            base={"samples/real31.txt": "observed share 0.31 on real data\n"},
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        v = _verdict(_run(tmp_path, root))
        assert v["findings"] == [], v

    def test_ac5_uncited_real_file_does_not_calibrate(self, tmp_path):
        # 0.31 is in a base-tracked file, but no valid AC cites it
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=_red_eq("0.31"),
            base={"samples/real31.txt": "observed share 0.31 on real data\n"},
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        assert _codes(_verdict(_run(tmp_path, root))) == ["RED_EXPECTED_FROM_LOT_FIXTURE"]

    def test_ac5_number_equal_to_measured_of_clean_ac_is_calibrated(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "share < 0.5", "samples/plain.txt", "0.31")),
            red=_red_eq("0.31"),
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac5_number_equal_to_threshold_of_clean_ac_is_calibrated(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "share < 0.31", "samples/plain.txt", "0.1")),
            red=_red_eq("0.31"),
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac5_threshold_of_ac_with_own_finding_does_not_calibrate(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "share < 0.31")),  # NO_SAMPLE: its threshold is not calibrated
            red=_red_eq("0.31"),
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        assert _codes(_verdict(_run(tmp_path, root))) == ["NO_SAMPLE", "RED_EXPECTED_FROM_LOT_FIXTURE"]

    def test_ac5_non_data_extension_in_lot_is_not_a_fixture(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=_red_eq("0.31"),
            lot={"fixtures/data.py": "X = 0.31\n", "fixtures/notes.md": "0.31\n"},
        )
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac5_base_tracked_unmodified_data_file_is_not_lot_authored(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=_red_eq("0.31"),
            base={"fixtures/old.json": '{"share": 0.31}\n'},
        )
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac5_lot_modified_tracked_data_file_is_lot_authored(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=_red_eq("0.31"),
            base={"fixtures/old.json": '{"share": 1}\n'},
            lot={"fixtures/old.json": '{"share": 0.31}\n'},
        )
        assert _codes(_verdict(_run(tmp_path, root))) == ["RED_EXPECTED_FROM_LOT_FIXTURE"]

    def test_ac5_non_python_red_expect_line_is_checked(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red="test('x', () => {\n  expect(compute()).toBe(0.31);\n});\n",
            red_name="tests/red.test.ts",
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        v = _verdict(_run(tmp_path, root, red="tests/red.test.ts"))
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v

    def test_ac5_non_python_red_number_off_assert_lines_is_ignored(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red="const n = 0.31;\ntest('x', () => {\n  expect(compute()).toBe(3);\n});\n",
            red_name="tests/red.test.ts",
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        assert _verdict(_run(tmp_path, root, red="tests/red.test.ts"))["findings"] == []

    # ------------------------------------------------------------- AC6 ---
    def test_ac6_inline_string_fixture_with_asserted_number_is_flagged(self, tmp_path):
        red = 'def test_x():\n    data = "stub stub stub 0.31"\n    assert score(data) == 0.31\n'
        root = _lot(tmp_path, _spec(_ac(1, "output is valid")), red=red)
        v = _verdict(_run(tmp_path, root))
        assert _codes(v) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"], v

    def test_ac6_short_string_literal_under_8_chars_is_ignored(self, tmp_path):
        red = 'def test_x():\n    data = "x 0.31"\n    assert score(data) == 0.31\n'
        root = _lot(tmp_path, _spec(_ac(1, "output is valid")), red=red)
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac6_nontrivial_integer_in_inline_fixture_is_flagged(self, tmp_path):
        red = 'def test_x():\n    data = "records 42 rows total"\n    assert count(data) == 42\n'
        root = _lot(tmp_path, _spec(_ac(1, "output is valid")), red=red)
        assert _codes(_verdict(_run(tmp_path, root))) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"]

    def test_ac6_trivial_asserted_numbers_are_never_flagged(self, tmp_path):
        red = (
            "def test_x():\n"
            '    data = "padding 0 1 3 -1 9 text"\n'
            "    assert a(data) == 0\n"
            "    assert b(data) == 1\n"
            "    assert c(data) == 3\n"
            "    assert d(data) == -1\n"
            "    assert e(data) == 9\n"
        )
        root = _lot(
            tmp_path,
            _spec(_ac(1, "output is valid")),
            red=red,
            lot={"fixtures/x.json": "[0, 1, 3, -1, 9]\n"},
        )
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac6_number_only_inside_approx_call_is_considered(self, tmp_path):
        red = (
            "def test_x():\n"
            '    data = "stub stub stub 0.31"\n'
            "    want = approx(0.31)\n"
            "    assert score(data) == want\n"
        )
        root = _lot(tmp_path, _spec(_ac(1, "output is valid")), red=red)
        assert _codes(_verdict(_run(tmp_path, root))) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"]

    def test_ac6_attribute_approx_call_is_considered(self, tmp_path):
        red = (
            "import pytest\n"
            "def test_x():\n"
            '    data = "stub stub stub 0.31"\n'
            "    want = pytest.approx(0.31)\n"
            "    assert score(data) == want\n"
        )
        root = _lot(tmp_path, _spec(_ac(1, "output is valid")), red=red)
        assert _codes(_verdict(_run(tmp_path, root))) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"]

    def test_ac6_numbers_outside_assert_and_approx_are_ignored(self, tmp_path):
        red = (
            "LIMIT = 0.31\n"
            "def test_x():\n"
            '    data = "stub stub stub 0.31"\n'
            "    assert score(data) == 3\n"
        )
        root = _lot(tmp_path, _spec(_ac(1, "output is valid")), red=red)
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac6_inline_number_calibrated_in_real_sample_is_ok(self, tmp_path):
        red = 'def test_x():\n    data = "stub stub stub 0.1"\n    assert score(data) == 0.1\n'
        root = _lot(tmp_path, CLEAN_SPEC, red=red)  # with01.txt contains 0.1
        assert _verdict(_run(tmp_path, root))["findings"] == []

    def test_ac6_bool_constants_are_not_asserted_numbers(self, tmp_path):
        red = 'def test_x():\n    data = "stub stub stub True 10"\n    assert ok(data) is True\n'
        root = _lot(tmp_path, _spec(_ac(1, "output is valid")), red=red)
        assert _verdict(_run(tmp_path, root))["findings"] == []

    # ------------------------------------------------------------- AC7 ---
    def test_ac7_default_is_shadow_flagged_exits_0_would_block(self, tmp_path):
        root = _flagged_case(tmp_path)
        r = _run(tmp_path, root)
        v = _verdict(r)
        assert r.returncode == 0
        assert v["status"] == "flagged" and str(v["mode"]).lower() == "shadow"
        assert v["would_block"] is True

    def test_ac7_enforce_1_exits_1_on_same_input(self, tmp_path):
        root = _flagged_case(tmp_path)
        r = _run(tmp_path, root, env_extra={FLAG_NAME: "1"})
        v = _verdict(r)
        assert r.returncode == 1
        assert v["status"] == "flagged" and str(v["mode"]).lower() == "enforce"
        assert v["would_block"] is True

    @pytest.mark.parametrize("value", ["true", "yes", "0", "", "11", " 1", "on"])
    def test_ac7_non_exact_one_values_are_shadow(self, tmp_path, value):
        root = _flagged_case(tmp_path)
        r = _run(tmp_path, root, env_extra={FLAG_NAME: value})
        v = _verdict(r)
        assert r.returncode == 0, (value, r.stdout)
        assert str(v["mode"]).lower() == "shadow" and v["would_block"] is True

    @pytest.mark.parametrize("env", [{}, {FLAG_NAME: "1"}])
    def test_ac7_clean_spec_exits_0_in_both_modes(self, tmp_path, env):
        root = _clean_case(tmp_path)
        r = _run(tmp_path, root, env_extra=env)
        v = _verdict(r)
        assert r.returncode == 0 and v["status"] == "clean" and v["would_block"] is False
        assert str(v["mode"]).lower() == ("enforce" if env else "shadow")

    def test_ac7_shadow_log_gets_exactly_one_json_line_per_run(self, tmp_path):
        root = _flagged_case(tmp_path)
        log = tmp_path / "logs" / "shadow.jsonl"
        log.parent.mkdir()
        _verdict(_run(tmp_path, root, extra=["--shadow-log", log]))
        lines = log.read_text().splitlines()
        assert len(lines) == 1, lines
        rec = json.loads(lines[0])
        assert set(rec) == {"ts", "spec", "mode", "would_block", "codes"}, rec
        assert rec["codes"] == ["MEASURED_CONTRADICTS"]
        assert rec["would_block"] is True and str(rec["mode"]).lower() == "shadow"
        assert isinstance(rec["ts"], str) and rec["ts"] and "spec.md" in str(rec["spec"])
        _verdict(_run(tmp_path, root, extra=["--shadow-log", log]))
        assert len(log.read_text().splitlines()) == 2

    def test_ac7_shadow_log_records_clean_run_with_empty_codes(self, tmp_path):
        root = _clean_case(tmp_path)
        log = tmp_path / "shadow.jsonl"
        _verdict(_run(tmp_path, root, extra=["--shadow-log", log]))
        rec = json.loads(log.read_text().splitlines()[0])
        assert rec["codes"] == [] and rec["would_block"] is False

    def test_ac7_no_shadow_log_flag_creates_no_log_file(self, tmp_path):
        root = _flagged_case(tmp_path)
        before = set(tmp_path.rglob("*.jsonl"))
        _verdict(_run(tmp_path, root))
        assert set(tmp_path.rglob("*.jsonl")) == before

    # ------------------------------------------------------------- AC8 ---
    def test_ac8_today_equal_to_expiry_is_not_expired(self, tmp_path):
        root = _flagged_case(tmp_path)
        r = _run(tmp_path, root, today="2026-10-17")
        v = _verdict(r)
        assert r.returncode == 0 and v["status"] == "flagged"
        assert v["flag"]["expired"] is False

    def test_ac8_day_after_expiry_in_shadow_is_flag_expired_exit_3_with_findings(self, tmp_path):
        root = _flagged_case(tmp_path)
        r = _run(tmp_path, root, today="2026-10-18")
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "flag_expired"
        assert _codes(v) == ["MEASURED_CONTRADICTS"]
        assert v["flag"]["expired"] is True

    def test_ac8_expired_shadow_with_clean_spec_still_exit_3(self, tmp_path):
        root = _clean_case(tmp_path)
        r = _run(tmp_path, root, today="2026-10-18")
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "flag_expired"

    def test_ac8_expiry_irrelevant_in_enforce(self, tmp_path):
        flagged = _flagged_case(tmp_path)
        r = _run(tmp_path, flagged, today="2026-10-18", env_extra={FLAG_NAME: "1"})
        assert r.returncode == 1 and _verdict(r)["status"] == "flagged"
        clean = _lot(tmp_path, CLEAN_SPEC, name="repo2")
        r2 = _run(tmp_path, clean, today="2026-10-18", env_extra={FLAG_NAME: "1"})
        assert r2.returncode == 0 and _verdict(r2)["status"] == "clean"

    def test_ac8_flag_object_shape(self, tmp_path):
        root = _clean_case(tmp_path)
        v = _verdict(_run(tmp_path, root))
        flag = v["flag"]
        assert set(flag) == FLAG_KEYS, flag
        assert flag["name"] == FLAG_NAME
        assert isinstance(flag["owner"], str) and flag["owner"].strip()
        assert flag["expires"] == "2026-10-17"
        assert flag["expired"] is False

    # ------------------------------------------------------------- AC9 ---
    def test_ac9_f1_non_git_root(self, tmp_path):
        root = tmp_path / "plain"
        _write(root, "docs/spec.md", CLEAN_SPEC)
        _write(root, "tests/test_red.py", "def test_x():\n    assert 1 == 1\n")
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"] == "git_failed"

    def test_ac9_f1_bad_base_ref(self, tmp_path):
        root = _clean_case(tmp_path)
        r = _run(tmp_path, root, base="no-such-ref-xyz")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"] == "git_failed"

    def test_ac9_f2_missing_spec(self, tmp_path):
        root = _clean_case(tmp_path)
        r = _run(tmp_path, root, spec="docs/missing.md")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    def test_ac9_f2_missing_red_file(self, tmp_path):
        root = _clean_case(tmp_path)
        r = _run(tmp_path, root, red="tests/test_red.py,tests/missing.py")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    def test_ac9_f2_spec_outside_root_relative_dotdot(self, tmp_path):
        root = _clean_case(tmp_path)
        (tmp_path / "outside.md").write_text(CLEAN_SPEC)
        r = _run(tmp_path, root, spec="../outside.md")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    def test_ac9_f2_spec_outside_root_absolute(self, tmp_path):
        root = _clean_case(tmp_path)
        outside = tmp_path / "outside.md"
        outside.write_text(CLEAN_SPEC)
        r = _run(tmp_path, root, spec=str(outside))
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    def test_ac9_f2_red_outside_root(self, tmp_path):
        root = _clean_case(tmp_path)
        outside = tmp_path / "outside_red.py"
        outside.write_text("def test_x():\n    assert 1 == 1\n")
        r = _run(tmp_path, root, red=str(outside))
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    def test_ac9_f2_empty_red_list(self, tmp_path):
        root = _clean_case(tmp_path)
        r = _run(tmp_path, root, red="")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    def test_ac9_f2_non_utf8_spec(self, tmp_path):
        root = _lot(tmp_path, CLEAN_SPEC, lot={"docs/spec.md": b"\xff\xfe- AC1: x < 0.25\n"})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    @pytest.mark.skipif(os.geteuid() == 0, reason="chmod 000 is readable by root")
    def test_ac9_f2_unreadable_spec_is_bad_input(self, tmp_path):
        root = _clean_case(tmp_path)
        spec = root / "docs" / "spec.md"
        spec.chmod(0)
        try:
            r = _run(tmp_path, root)
        finally:
            spec.chmod(0o644)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_input"

    @pytest.mark.parametrize("today", ["2026-13-45", "20261001", "abc", "2026-10-1"])
    def test_ac9_f3_bad_today(self, tmp_path, today):
        root = _clean_case(tmp_path)
        r = _run(tmp_path, root, today=today)
        assert r.returncode == 2, (today, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"] == "bad_usage"

    def test_ac9_f3_unknown_option_emits_json_not_argparse_trace(self, tmp_path):
        r = _run_raw(tmp_path, "--bogus-flag")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"] == "bad_usage"

    def test_ac9_f3_missing_required_args_emits_json(self, tmp_path):
        r = _run_raw(tmp_path)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["reason"] == "bad_usage"

    def test_ac9_f4_red_syntax_error(self, tmp_path):
        root = _lot(tmp_path, CLEAN_SPEC, red="def broken(:\n    pass\n")
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"] == "red_unparseable"

    # ------------------------------------------------------------ AC10 ---
    def test_ac10_static_isolation_flag_constants_catchall_exit_codes_no_registry(self):
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

        consts = {}
        for n in tree.body:
            tgts, val = [], None
            if isinstance(n, ast.Assign):
                tgts, val = n.targets, n.value
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                tgts, val = [n.target], n.value
            if isinstance(val, ast.Constant):
                for t in tgts:
                    if isinstance(t, ast.Name):
                        consts[t.id] = val.value
        assert consts.get("FLAG_OWNER") == "s3-bytedigger (MGR)", consts.get("FLAG_OWNER")
        assert consts.get("FLAG_EXPIRES") == "2026-10-17", consts.get("FLAG_EXPIRES")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.startswith("bytedigger_engine"), a.name
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("bytedigger_engine"), node.module
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value
                assert s not in {"claude", "bun"}, f"forbidden argv literal {s!r}"
                assert not s.endswith(".ts"), f"forbidden .ts literal {s!r}"
                if s.startswith("HAL_"):
                    assert s == FLAG_NAME, f"only {FLAG_NAME} may be read, found {s!r}"

        # every os.environ / getenv read names only the one flag
        read_names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant):
                fn = n.func
                if isinstance(fn, ast.Attribute) and (
                    fn.attr == "getenv"
                    or (fn.attr == "get" and isinstance(fn.value, ast.Attribute) and fn.value.attr == "environ")
                ):
                    read_names.add(n.args[0].value)
            if (
                isinstance(n, ast.Subscript)
                and isinstance(n.value, ast.Attribute)
                and n.value.attr == "environ"
                and isinstance(n.slice, ast.Constant)
            ):
                read_names.add(n.slice.value)
        assert read_names == {FLAG_NAME}, read_names

        mains = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"]
        assert len(mains) == 1, "exactly one main() must produce the verdict"
        main = mains[0]

        def _is_two(a) -> bool:
            if isinstance(a, ast.Constant):
                return a.value == 2 and not isinstance(a.value, bool)
            return isinstance(a, ast.Name) and consts.get(a.id) == 2

        catchalls = [
            h
            for t in ast.walk(main)
            if isinstance(t, ast.Try)
            for h in t.handlers
            if h.type is None or (isinstance(h.type, ast.Name) and h.type.id in {"Exception", "BaseException"})
        ]
        assert catchalls, "main() must wrap its body in a catch-all handler"
        assert any(
            any(isinstance(x, ast.Return) and x.value is not None and _is_two(x.value) for x in ast.walk(h))
            or any(
                isinstance(x, ast.Call)
                and getattr(x.func, "attr", getattr(x.func, "id", "")) in {"exit", "SystemExit", "_exit"}
                and x.args
                and _is_two(x.args[0])
                for x in ast.walk(h)
            )
            for h in catchalls
        ), "catch-all handler must return/exit 2"
        assert any(
            isinstance(x, ast.Constant) and x.value == "internal_error"
            for h in catchalls
            for x in ast.walk(h)
        ) or "internal_error" in {v for v in consts.values() if isinstance(v, str)}, "internal_error reason missing"

        def _code(a):
            if isinstance(a, ast.Constant) and isinstance(a.value, int) and not isinstance(a.value, bool):
                return a.value
            if isinstance(a, ast.Name) and isinstance(consts.get(a.id), int) and not isinstance(consts.get(a.id), bool):
                return consts[a.id]
            return None

        codes = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in {"exit", "SystemExit", "_exit"} and n.args:
                    c = _code(n.args[0])
                    if c is not None:
                        codes.add(c)
        for n in ast.walk(main):
            if isinstance(n, ast.Return) and n.value is not None:
                c = _code(n.value)
                if c is not None:
                    codes.add(c)
        assert codes, "main()/exit paths must use int exit codes"
        assert codes <= {0, 1, 2, 3}, f"exit codes outside {{0,1,2,3}}: {sorted(codes)}"

        catalog = FLAGS_CATALOG.read_text(encoding="utf-8")
        assert FLAG_NAME not in catalog and "spec_sample" not in catalog, "no flags_catalog entry allowed"

    def test_ac10_dynamic_runs_without_claude_bun_or_api_key(self, tmp_path):
        root = _flagged_case(tmp_path)
        for tool in ("claude", "bun"):
            assert not any(
                (Path(d) / tool).exists() for d in f"{tmp_path / 'bin'}:/usr/bin:/bin".split(":")
            ), f"test env must not contain {tool}"
        v = _verdict(_run(tmp_path, root))
        assert v["status"] == "flagged"  # real verdict from a real run

    # ------------------------------------------------------------ AC11 ---
    def test_ac11_script_writes_nothing_under_root(self, tmp_path):
        root = _lot(
            tmp_path,
            _spec(
                _ac(1, "prose share < 0.25", "samples/with01.txt", "0.31"),
                _ac(2, "latency within 200 ms"),
            ),
            red='def test_x():\n    data = "stub stub stub 0.31"\n    assert score(data) == 0.31\n',
            lot={"fixtures/x.json": '{"share": 0.31}\n'},
        )
        before = _snapshot(root)
        log = tmp_path / "outside-shadow.jsonl"
        r = _run(tmp_path, root, extra=["--shadow-log", log])
        v = _verdict(r)
        assert v["status"] == "flagged" and len(v["findings"]) >= 3, v  # non-vacuous
        assert _snapshot(root) == before, "script modified the tree under --root"

    def test_ac11_only_shadow_log_inside_root_may_appear(self, tmp_path):
        root = _flagged_case(tmp_path)
        before = _snapshot(root)
        log = root / "out" / "shadow.jsonl"
        log.parent.mkdir()
        v = _verdict(_run(tmp_path, root, extra=["--shadow-log", log]))
        assert v["status"] == "flagged"
        after = _snapshot(root)
        assert log.exists() and len(log.read_text().splitlines()) == 1
        after.pop(str(log.relative_to(root)))
        assert after == before, "script wrote something other than --shadow-log"

    # ------------------------------------------------------------ AC12 ---
    def test_ac12_1668_end_to_end_circular_spec_blocks_corrected_spec_passes(self, tmp_path):
        real_sample = "samples/real_stub.md"
        real_text = "real stub intro\nprose share 0.1\nintro chars 190\n"

        # the 1668 shape: threshold 0.25 vs real 0.31, synthetic lot fixtures drive the RED
        bad_root = _lot(
            tmp_path,
            _spec(_ac(1, "prose share < 0.25", real_sample, "0.31")),
            red=(
                "def test_synthetic():\n"
                "    share, chars = measure()\n"
                "    assert share == 0.2\n"
                "    assert chars == 150\n"
            ),
            base={real_sample: real_text},
            lot={"fixtures/synthetic_stub.json": '{"prose_share": 0.2, "chars": 150}\n'},
            name="bad",
        )
        r = _run(tmp_path, bad_root, env_extra={FLAG_NAME: "1"})
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "flagged" and str(v["mode"]).lower() == "enforce"
        assert v["would_block"] is True
        codes = set(_codes(v))
        assert "MEASURED_CONTRADICTS" in codes, v
        assert codes & {"RED_EXPECTED_FROM_LOT_FIXTURE", "RED_EXPECTED_FROM_INLINE_FIXTURE"}, v

        # the corrected spec: measured from the real sample, RED numbers from it too
        good_root = _lot(
            tmp_path,
            _spec(_ac(1, "prose share < 0.25", real_sample, "0.1")),
            red=(
                "def test_real():\n"
                "    share, chars = measure()\n"
                "    assert share == 0.1\n"
                "    assert chars == 190\n"
            ),
            base={real_sample: real_text},
            lot={"fixtures/synthetic_stub.json": '{"prose_share": 0.1, "chars": 190}\n'},
            name="good",
        )
        r2 = _run(tmp_path, good_root, env_extra={FLAG_NAME: "1"})
        assert r2.returncode == 0, (r2.returncode, r2.stdout, r2.stderr)
        v2 = _verdict(r2)
        assert v2["status"] == "clean" and v2["findings"] == [] and v2["would_block"] is False
        assert [e["ac"] for e in v2["verified"]] == ["AC1"], v2
