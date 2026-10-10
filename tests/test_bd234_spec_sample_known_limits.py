"""RED tests for bd#234: known limits of scripts/spec_sample_check.py and the
per-AC waiver (spec docs/decisions/2026-10-10-bd234-spec-sample-known-limits.md,
AC1-AC17).

Nothing here imports the script: every test invokes it as a subprocess
(sys.executable) inside the test body, so collection succeeds and failure
happens at assert time. Hermetic like tests/test_s3_spec_sample_check.py (the
helpers are copied, not imported): git repos under tmp_path with a base commit
holding the real samples and the lot left uncommitted, --base <sha>, an
environment built from scratch, --today 2026-10-01. No singleton resources and
no timing: the git failure cases pre-stage a wrapper executable on PATH
(workflows.md section 1i), nothing is raced.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "spec_sample_check.py"
FLAGS_CATALOG = REPO_ROOT / "engine_py" / "bytedigger_engine" / "flags_catalog.py"

NEW_KEYS = {
    "status", "mode", "would_block", "findings", "waived",
    "verified", "unverified", "flag", "reason",
}
FLAG_NAME = "HAL_SPEC_SAMPLE_ENFORCE"
DEFAULT_TODAY = "2026-10-01"
ENFORCE = {FLAG_NAME: "1"}

PLAIN = "alpha beta gamma delta\n"
WITH01 = "prose share 0.1 observed\n"
LAT = "p95 observed 1,250 ms\n"
LOT_FIXTURE = "fixtures/data.json"
REASON = "long enough reason"


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


_BASES: dict = {}


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
    changes (spec, RED, fixtures) written but NOT committed."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    base_files = {
        "samples/plain.txt": PLAIN,
        "samples/with01.txt": WITH01,
        "samples/lat.txt": LAT,
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
    _BASES[str(root)] = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(tmp_path),
    ).stdout.strip()
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


def _raw_spec(*lines: str) -> str:
    return "# Spec\n\n## Acceptance criteria\n\n" + "\n".join(lines) + "\n"


def _exec(tmp_path: Path, cmd: list, env_extra: dict | None = None):
    assert SCRIPT.exists(), f"{SCRIPT} does not exist"
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
    cmd = [sys.executable, str(SCRIPT), "--root", str(root), "--spec", str(spec), "--red", str(red)]
    if today:
        cmd += ["--today", today]
    cmd += ["--base", base if base is not None else _BASES[str(root)]]
    cmd += [str(x) for x in extra]
    return _exec(tmp_path, cmd, env_extra)


def _load(r) -> dict:
    assert "Traceback" not in r.stderr, r.stderr
    try:
        v = json.loads(r.stdout)
    except ValueError as e:
        raise AssertionError(f"stdout is not a single JSON object: {r.stdout!r} ({e})")
    assert isinstance(v, dict) and set(v) == NEW_KEYS, f"bad verdict keys: {v!r}"
    assert isinstance(v["waived"], list), v
    return v


def _go(tmp_path, spec, enforce=False, red="def test_x():\n    assert 1 == 1\n",
        red_name="tests/test_red.py", lot=None, base=None, name="repo", extra=(), env_extra=None):
    root = _lot(tmp_path, spec, red=red, red_name=red_name, lot=lot, base=base, name=name)
    env = dict(ENFORCE) if enforce else {}
    env.update(env_extra or {})
    r = _run(tmp_path, root, red=red_name, extra=extra, env_extra=env)
    return root, r, _load(r)


def _codes(v: dict) -> list:
    return sorted(f["code"] for f in v["findings"])


def _ac_ids(v: dict) -> list:
    return sorted({f["ac"] for f in v["findings"]})


def _waived_codes(v: dict) -> list:
    return sorted(w["code"] for w in v.get("waived", []))


def _install_git_wrapper(tmp_path: Path, probe: str) -> None:
    """Executable `git` first on PATH: delegates to the real git, but exits 128
    for cat-file / ls-tree / show when any argument contains `probe`."""
    real = shutil.which("git")
    assert real, "git not found"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = (
        "#!/bin/sh\n"
        f"REAL={shlex.quote(real)}\n"
        f"PROBE={shlex.quote(probe)}\n"
        "hit=0\n"
        'for a in "$@"; do\n'
        '  case "$a" in cat-file|ls-tree|show) hit=1;; esac\n'
        "done\n"
        'if [ "$hit" = 1 ]; then\n'
        '  for a in "$@"; do\n'
        '    case "$a" in *"$PROBE"*) exit 128;; esac\n'
        "  done\n"
        "fi\n"
        'exec "$REAL" "$@"\n'
    )
    p = bindir / "git"
    p.write_text(script, encoding="utf-8")
    p.chmod(0o755)


class TestBd234SpecSampleKnownLimits:
    # ------------------------------------------------------------- AC1 ---
    def test_ac1_grouped_threshold_contradicts_and_prints_canonical(self, tmp_path):
        spec = _spec(_ac(1, "p95 < 1,000 ms", "samples/lat.txt", "1,250"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v
        detail = v["findings"][0]["detail"]
        assert "1000" in detail and "1,000" not in detail, detail

    @pytest.mark.parametrize("thr", ["1, 000", "1,0001"])
    def test_ac1_ungrouped_forms_are_not_one_number(self, tmp_path, thr):
        spec = _spec(_ac(1, f"p95 > {thr} ms", "samples/plain.txt", "500"))
        _root, _r, v = _go(tmp_path, spec)
        assert "MEASURED_CONTRADICTS" not in _codes(v), v

    def test_ac1_measured_without_space_is_one_value(self, tmp_path):
        spec = _spec(_ac(1, "p95 > 150000 ms", "samples/plain.txt", "150,200"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == [], v

    def test_ac1_measured_with_space_is_two_values(self, tmp_path):
        spec = _spec(_ac(1, "p50 < 160 ms; p95 < 250 ms", "samples/plain.txt", "150, 200"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == [], v

    def test_ac1_asserted_1000_matches_grouped_fixture(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red="def test_x():\n    assert compute() == 1000\n",
            lot={LOT_FIXTURE: '{"limit": "1,000"}\n'},
        )
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v

    def test_ac1_asserted_1000_ignores_other_grouped_numbers(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red="def test_x():\n    assert compute() == 1000\n",
            lot={LOT_FIXTURE: '{"a": "11,000", "b": "1,0001"}\n'},
        )
        assert _codes(v) == [], v

    # ------------------------------------------------------------- AC2 ---
    @pytest.mark.parametrize(
        "phrase,measured,contradicts",
        [
            ("less than or equal to", "250", False),
            ("less than or equal to", "251", True),
            ("greater than or equal to", "250", False),
            ("greater than or equal to", "249", True),
            ("less than", "250", True),
        ],
    )
    def test_ac2_long_phrases(self, tmp_path, phrase, measured, contradicts):
        spec = _spec(_ac(1, f"p95 {phrase} 250 ms", "samples/plain.txt", measured))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == (["MEASURED_CONTRADICTS"] if contradicts else []), v

    # ------------------------------------------------------------- AC3 ---
    def test_ac3_leading_dot_threshold_contradicts(self, tmp_path):
        spec = _spec(_ac(1, "prose share < .25", "samples/with01.txt", "0.31"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v

    def test_ac3_negative_leading_dot_parses(self, tmp_path):
        spec = _spec(_ac(1, "drift > -.25", "samples/plain.txt", "-0.31"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v

    def test_ac3_word_attached_dots_are_not_numbers(self, tmp_path):
        spec = _spec(_ac(1, "tag v1.25 s and x.25 s shipped"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == [], v

    def test_ac3_fixture_zero_matches_asserted_dot_form(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red="expect(compute()).toBe(.25);\n",
            red_name="tests/red.test.ts",
            lot={LOT_FIXTURE: '{"share": 0.25}\n'},
        )
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v

    def test_ac3_fixture_dot_form_matches_asserted_zero(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red="def test_x():\n    assert compute() == 0.25\n",
            lot={LOT_FIXTURE: '{"share": .25}\n'},
        )
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v

    def test_ac3_asserted_dot_form_still_matches_fixture_zero(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red="def test_x():\n    assert compute() == .25\n",
            lot={LOT_FIXTURE: '{"share": 0.25}\n'},
        )
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v

    def test_ac3_fixture_with_word_attached_dot_is_not_a_match(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red="def test_x():\n    assert compute() == 0.25\n",
            lot={LOT_FIXTURE: '{"ver": "v1.25"}\n'},
        )
        assert _codes(v) == [], v

    # ------------------------------------------------------------- AC4 ---
    @pytest.mark.parametrize("text", ["p95 = 200 ms", "target 200 = p95"])
    def test_ac4_bare_equals_is_a_comparison(self, tmp_path, text):
        spec = _spec(_ac(1, text, "samples/plain.txt", "250"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v

    @pytest.mark.parametrize("text", ["p95 = 200 ms", "target 200 = p95"])
    def test_ac4_bare_equals_holds_when_equal(self, tmp_path, text):
        spec = _spec(_ac(1, text, "samples/plain.txt", "200"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == [], v

    def test_ac4_unspaced_equals_is_not_a_comparison(self, tmp_path):
        spec = _spec(_ac(1, "timeout=200 ms", "samples/plain.txt", "250"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == [], v

    @pytest.mark.parametrize("op", ["==", "<="])
    def test_ac4_two_char_operators_stay_one_comparison(self, tmp_path, op):
        spec = _spec(_ac(1, f"p95 {op} 200 ms", "samples/plain.txt", "250"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v

    def test_ac4_not_equal_is_not_a_comparison(self, tmp_path):
        spec = _spec(_ac(1, "p95 != 200 ms", "samples/plain.txt", "250"))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == [], v

    # ------------------------------------------------------------- AC5 ---
    def test_ac5_tilde_fence_hides_ac_line(self, tmp_path):
        spec = _raw_spec("~~~", "- AC1: latency < 200 ms", "~~~")
        _root, _r, v = _go(tmp_path, spec)
        assert _ac_ids(v) == [], v

    @pytest.mark.parametrize(
        "lines,expected",
        [
            # backtick fence inside a tilde fence does not close it
            (["~~~", "```", "- AC1: latency < 200 ms", "~~~", "", "- AC2: latency < 300 ms"], ["AC2"]),
            # tilde line inside a backtick fence does not close it
            (["```", "~~~", "- AC1: latency < 200 ms", "```", "", "- AC2: latency < 300 ms"], ["AC2"]),
            # info string inside an open fence does not close it
            (["```", "```python", "- AC1: latency < 200 ms", "```", "", "- AC2: latency < 300 ms"], ["AC2"]),
            # a shorter closer does not close a longer opener
            (["````", "```", "- AC1: latency < 200 ms", "````", "", "- AC2: latency < 300 ms"], ["AC2"]),
            (["~~~~", "~~~", "- AC1: latency < 200 ms", "~~~~", "", "- AC2: latency < 300 ms"], ["AC2"]),
            # a longer closer closes
            (["```", "- AC1: latency < 200 ms", "`````", "", "- AC2: latency < 300 ms"], ["AC2"]),
            # an unclosed fence runs to the end of the file
            (["~~~", "- AC1: latency < 200 ms", "", "- AC2: latency < 300 ms"], []),
            (["```", "- AC1: latency < 200 ms", "", "- AC2: latency < 300 ms"], []),
        ],
    )
    def test_ac5_fence_open_and_close_rules(self, tmp_path, lines, expected):
        _root, _r, v = _go(tmp_path, _raw_spec(*lines))
        assert _ac_ids(v) == expected, v

    @pytest.mark.parametrize("fence", ["```", "~~~"])
    def test_ac5_fenced_threshold_under_an_ac_line_still_counts(self, tmp_path, fence):
        spec = _raw_spec(
            "- AC1: prose share is stable",
            "  sample: samples/with01.txt",
            "  measured: 0.31",
            f"  {fence}",
            "  prose share < 0.25",
            f"  {fence}",
        )
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v

    @pytest.mark.parametrize("fence", ["```", "~~~"])
    def test_ac5_hash_line_inside_a_fence_does_not_end_the_block(self, tmp_path, fence):
        spec = _raw_spec(
            "- AC1: prose share < 0.25",
            f"  {fence}",
            "  # comment line",
            f"  {fence}",
            "  sample: samples/with01.txt",
            "  measured: 0.31",
        )
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"], v

    # ------------------------------------------------------------- AC6 ---
    def test_ac6_bytes_literal_is_an_inline_fixture(self, tmp_path):
        red = 'def test_x():\n    assert compute() == 4321\n    PAYLOAD = b"count=4321 items"\n'
        _root, _r, v = _go(tmp_path, _spec(_ac(1, "plain statement")), red=red)
        assert _codes(v) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"], v
        assert "inline bytes literal" in v["findings"][0]["detail"], v

    def test_ac6_non_utf8_bytes_literal_is_decoded_with_replace(self, tmp_path):
        red = 'def test_x():\n    assert compute() == 4321\n    PAYLOAD = b"\\xff count=4321"\n'
        _root, _r, v = _go(tmp_path, _spec(_ac(1, "plain statement")), red=red)
        assert _codes(v) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"], v
        assert "inline bytes literal" in v["findings"][0]["detail"], v

    def test_ac6_fstring_static_parts_are_an_inline_fixture(self, tmp_path):
        red = 'def test_x():\n    assert compute() == 4321\n    MSG = f"count={n} total=4321"\n'
        _root, _r, v = _go(tmp_path, _spec(_ac(1, "plain statement")), red=red)
        assert _codes(v) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"], v
        assert "inline f-string literal" in v["findings"][0]["detail"], v

    def test_ac6_string_literal_detail_is_unchanged(self, tmp_path):
        red = 'def test_x():\n    assert compute() == 4321\n    PAYLOAD = "count=4321 items"\n'
        _root, _r, v = _go(tmp_path, _spec(_ac(1, "plain statement")), red=red)
        assert _codes(v) == ["RED_EXPECTED_FROM_INLINE_FIXTURE"], v
        assert "inline string literal" in v["findings"][0]["detail"], v

    @pytest.mark.parametrize(
        "literal",
        ['b"4321"', 'f"{a}4321"'],
    )
    def test_ac6_short_literals_are_not_fixtures(self, tmp_path, literal):
        red = f"def test_x():\n    assert compute() == 4321\n    PAYLOAD = {literal}\n"
        _root, _r, v = _go(tmp_path, _spec(_ac(1, "plain statement")), red=red)
        assert _codes(v) == [], v

    def test_ac6_number_does_not_fuse_across_a_replacement_field(self, tmp_path):
        red = 'def test_x():\n    assert compute() == 55\n    MSG = f"total={a}5{b}5 units"\n'
        _root, _r, v = _go(tmp_path, _spec(_ac(1, "plain statement")), red=red)
        assert _codes(v) == [], v

    # ------------------------------------------------------------- AC7 ---
    RED_4321 = "def test_x():\n    assert compute() == 4321\n"

    def test_ac7_ignored_lot_fixture_counts_as_lot_authored(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red=self.RED_4321,
            lot={".gitignore": "fixtures/\n", LOT_FIXTURE: '{"n": 4321}\n'},
        )
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v
        assert LOT_FIXTURE in v["findings"][0]["detail"], v

    def test_ac7_fixture_under_node_modules_is_not_counted(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red=self.RED_4321,
            lot={".gitignore": "node_modules/\n", "node_modules/pkg/data.json": '{"n": 4321}\n'},
        )
        assert _codes(v) == [], v

    def test_ac7_cited_ignored_sample_is_lot_authored(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "latency < 200 ms", "fixtures/real.txt", "150")),
            lot={".gitignore": "fixtures/\n", "fixtures/real.txt": "latency 150 ms observed\n"},
        )
        assert _codes(v) == ["SAMPLE_NOT_REAL"], v
        assert "lot-authored" in v["findings"][0]["detail"], v

    # ------------------------------------------------------------- AC8 ---
    CLEAN_SAMPLE_SPEC = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.1"))

    def test_ac8a_git_failure_on_spec_path_is_git_failed(self, tmp_path):
        root = _lot(tmp_path, self.CLEAN_SAMPLE_SPEC)
        _install_git_wrapper(tmp_path, "docs/spec.md")
        r = _run(tmp_path, root)
        v = _load(r)
        assert r.returncode == 2, (r.returncode, v)
        assert v["status"] == "unavailable" and v["reason"] == "git_failed", v

    def test_ac8b_git_failure_on_sample_path_is_git_failed(self, tmp_path):
        root = _lot(tmp_path, self.CLEAN_SAMPLE_SPEC)
        _install_git_wrapper(tmp_path, "samples/plain.txt")
        spec = _spec(_ac(1, "latency < 200 ms", "samples/plain.txt", "150"))
        (root / "docs/spec.md").write_text(spec, encoding="utf-8")
        r = _run(tmp_path, root)
        v = _load(r)
        assert r.returncode == 2, (r.returncode, v)
        assert v["status"] == "unavailable" and v["reason"] == "git_failed", v
        assert "SAMPLE_NOT_REAL" not in r.stdout

    def test_ac8c_passive_wrapper_gives_the_normal_verdict(self, tmp_path):
        root = _lot(tmp_path, self.CLEAN_SAMPLE_SPEC)
        _install_git_wrapper(tmp_path, "no/such/probe-path")
        r = _run(tmp_path, root)
        v = _load(r)
        assert r.returncode == 0 and v["status"] == "clean", (r.returncode, v)

    def test_ac8d_genuinely_absent_sample_is_untracked_at_base(self, tmp_path):
        # An ignored directory that the lot-authored set skips, so the only
        # remaining answer for the cited file is "not a blob at base".
        spec = _spec(_ac(1, "latency < 200 ms", "node_modules/pkg/nope.txt", "150"))
        root = _lot(
            tmp_path,
            spec,
            lot={".gitignore": "node_modules/\n", "node_modules/pkg/nope.txt": "latency 150 ms\n"},
        )
        _install_git_wrapper(tmp_path, "samples/plain.txt")
        r = _run(tmp_path, root)
        v = _load(r)
        assert v["status"] == "flagged", v
        assert _codes(v) == ["SAMPLE_NOT_REAL"], v
        assert "untracked at base" in v["findings"][0]["detail"], v

    def test_ac8e_failure_listing_ignored_files_is_git_failed(self, tmp_path):
        root = _lot(tmp_path, self.CLEAN_SAMPLE_SPEC)
        real = shutil.which("git")
        p = tmp_path / "bin" / "git"
        p.write_text(
            "#!/bin/sh\n"
            'for a in "$@"; do\n'
            '  [ "$a" = "--ignored" ] && exit 128\n'
            "done\n"
            f"exec {shlex.quote(real)} \"$@\"\n",
            encoding="utf-8",
        )
        p.chmod(0o755)
        r = _run(tmp_path, root)
        v = _load(r)
        assert r.returncode == 2, (r.returncode, v)
        assert v["status"] == "unavailable" and v["reason"] == "git_failed", v

    @pytest.mark.parametrize(
        "ignore,paths",
        [
            ("__pycache__/\n.venv/\n", ["pkg/__pycache__/d.json", ".venv/lib/d.json"]),
            ("venv/\n.tox/\n.pytest_cache/\n", ["venv/d.json", ".tox/d.json", ".pytest_cache/d.json"]),
            (".mypy_cache/\n.ruff_cache/\n", [".mypy_cache/d.json", ".ruff_cache/d.json"]),
        ],
    )
    def test_ac7_all_skip_components_are_not_counted(self, tmp_path, ignore, paths):
        lot = {".gitignore": ignore}
        lot.update({p: '{"n": 4321}\n' for p in paths})
        _root, _r, v = _go(
            tmp_path, _spec(_ac(1, "plain statement")), red=self.RED_4321, lot=lot
        )
        assert _codes(v) == [], v

    def test_ac7_component_must_match_exactly(self, tmp_path):
        _root, _r, v = _go(
            tmp_path,
            _spec(_ac(1, "plain statement")),
            red=self.RED_4321,
            lot={".gitignore": "my_node_modules_x/\n", "my_node_modules_x/f.json": '{"n": 4321}\n'},
        )
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v

    # ------------------------------------------------------------- AC9 ---
    @pytest.mark.parametrize(
        "thr,measured,contradicts",
        [
            ("<25%", "0.31", True),
            ("<25%", "0.1", False),
            ("<0.25", "31%", True),
            ("<0.25", "10%", False),
            ("<40", "45%", True),
            ("<40", "35%", False),
            ("<25%", "31", True),
            ("<25%", "20", False),
        ],
    )
    def test_ac9_percent_normalisation(self, tmp_path, thr, measured, contradicts):
        spec = _spec(_ac(1, f"prose share {thr}", "samples/with01.txt", measured))
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == (["MEASURED_CONTRADICTS"] if contradicts else []), v

    @pytest.mark.parametrize(
        "asserted,fixture,flagged",
        [("31", "31", False), ("0.31", "0.31", False), ("0.32", "0.32", True)],
    )
    def test_ac9_percent_measured_calibrates_red_ok_set(self, tmp_path, asserted, fixture, flagged):
        spec = _spec(_ac(1, "prose share < 40%", "samples/with01.txt", "31%"))
        _root, _r, v = _go(
            tmp_path,
            spec,
            red=f"def test_x():\n    assert compute() == {asserted}\n",
            lot={LOT_FIXTURE: '{"n": %s}\n' % fixture},
        )
        assert _codes(v) == (["RED_EXPECTED_FROM_LOT_FIXTURE"] if flagged else []), v

    # ------------------------------------------------------------ AC10 ---
    WAIVABLE = {
        "NO_SAMPLE": ["- AC1: latency < 200 ms"],
        "SAMPLE_NOT_REAL": [
            "- AC1: latency < 200 ms", "  sample: samples/missing.txt", "  measured: 150",
        ],
        "NO_MEASURED": ["- AC1: latency < 200 ms", "  sample: samples/plain.txt"],
        "MEASURED_CONTRADICTS": [
            "- AC1: latency < 200 ms", "  sample: samples/plain.txt", "  measured: 250",
        ],
        "AMBIGUOUS_PAIRING": [
            "- AC1: p50 < 160 ms, p95 < 250 ms, p99 < 400 ms",
            "  sample: samples/plain.txt", "  measured: 150, 200",
        ],
    }

    @pytest.mark.parametrize("code", sorted(WAIVABLE))
    def test_ac10_valid_waiver_moves_finding_to_waived(self, tmp_path, code):
        lines = list(self.WAIVABLE[code])
        _root, _r, v0 = _go(tmp_path, _raw_spec(*lines), name="plain")
        assert _codes(v0) == [code], v0  # the fixture really raises the code
        lines.append(f"  waive: {code} reason: baseline gap tracked elsewhere")
        _root, r, v = _go(tmp_path, _raw_spec(*lines), enforce=True, name="waived")
        assert v["status"] == "clean" and r.returncode == 0, (r.returncode, v)
        assert v["findings"] == [] and v["would_block"] is False, v
        assert len(v["waived"]) == 1, v
        w = v["waived"][0]
        assert set(w) == {"code", "ac", "detail", "reason"}, w
        assert w["code"] == code and w["ac"] == "AC1", w
        assert isinstance(w["detail"], str) and w["detail"], w
        assert w["reason"] == "baseline gap tracked elsewhere", w

    def test_ac10_one_line_waives_several_codes(self, tmp_path):
        lines = [
            "- AC1: p50 < 160 ms, p95 < 250 ms, p99 < 400 ms",
            "  sample: samples/missing.txt, samples/with01.txt",
            "  measured: 150, 200",
        ]
        _root, _r, v0 = _go(tmp_path, _raw_spec(*lines), name="plain")
        assert _codes(v0) == ["AMBIGUOUS_PAIRING", "SAMPLE_NOT_REAL"], v0
        lines.append(f"  waive: AMBIGUOUS_PAIRING, SAMPLE_NOT_REAL reason: {REASON}")
        _root, _r, v = _go(tmp_path, _raw_spec(*lines), name="waived")
        assert v["status"] == "clean" and v["findings"] == [], v
        assert _waived_codes(v) == ["AMBIGUOUS_PAIRING", "SAMPLE_NOT_REAL"], v

    def test_ac10_waived_ac_calibrates_nothing_and_is_not_verified(self, tmp_path):
        spec = _raw_spec(
            "- AC1: count < 4321 files",
            "  sample: samples/with01.txt",
            "  measured: 9999",
            f"  waive: MEASURED_CONTRADICTS reason: {REASON}",
        )
        _root, _r, v = _go(
            tmp_path,
            spec,
            red="def test_x():\n    assert count() == 4321\n",
            lot={LOT_FIXTURE: '{"n": 4321}\n'},
        )
        assert _waived_codes(v) == ["MEASURED_CONTRADICTS"], v
        assert _codes(v) == ["RED_EXPECTED_FROM_LOT_FIXTURE"], v
        assert v["verified"] == [] and v["unverified"] == [], v

    # ------------------------------------------------------------ AC11 ---
    def test_ac11_waiver_covers_only_its_own_ac(self, tmp_path):
        spec = _raw_spec(
            "- AC1: latency < 200 ms",
            "  sample: samples/plain.txt",
            "  measured: 250",
            f"  waive: MEASURED_CONTRADICTS reason: {REASON}",
            "",
            "- AC2: latency < 300 ms",
            "  sample: samples/plain.txt",
            "  measured: 350",
        )
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == ["MEASURED_CONTRADICTS"] and _ac_ids(v) == ["AC2"], v
        assert [(w["code"], w["ac"]) for w in v["waived"]] == [("MEASURED_CONTRADICTS", "AC1")], v

    def test_ac11_waiver_covers_only_listed_codes(self, tmp_path):
        spec = _raw_spec(
            "- AC1: p50 < 160 ms, p95 < 250 ms, p99 < 400 ms",
            "  sample: samples/missing.txt, samples/with01.txt",
            "  measured: 150, 200",
            f"  waive: AMBIGUOUS_PAIRING reason: {REASON}",
        )
        _root, r, v = _go(tmp_path, spec, enforce=True)
        assert _codes(v) == ["SAMPLE_NOT_REAL"], v
        assert _waived_codes(v) == ["AMBIGUOUS_PAIRING"], v
        assert v["status"] == "flagged" and r.returncode == 1, (r.returncode, v)

    @pytest.mark.parametrize(
        "code_list",
        ["RED_EXPECTED_FROM_LOT_FIXTURE", "*", "NO_SAMPLE, *", "WAIVER_INVALID", "WAIVER_NO_REASON, NO_SAMPLE"],
    )
    def test_ac11_non_waivable_codes_reject_the_whole_line(self, tmp_path, code_list):
        spec = _raw_spec("- AC1: latency < 200 ms", f"  waive: {code_list} reason: {REASON}")
        _root, r, v = _go(tmp_path, spec, enforce=True)
        assert "WAIVER_INVALID" in _codes(v), v
        assert "NO_SAMPLE" in _codes(v), v
        assert v.get("waived", []) == [], v
        assert r.returncode == 1, (r.returncode, v)

    def test_ac11_unused_waiver_is_silent(self, tmp_path):
        spec = _raw_spec(
            "- AC1: prose share < 0.25",
            "  sample: samples/with01.txt",
            "  measured: 0.1",
            f"  waive: NO_SAMPLE reason: {REASON}",
        )
        _root, _r, v = _go(tmp_path, spec)
        assert v["status"] == "clean" and v["findings"] == [], v
        assert v.get("waived") == [], v

    # ------------------------------------------------------------ AC12 ---
    @pytest.mark.parametrize(
        "line,code",
        [
            ("  waive: NO_SAMPLE", "WAIVER_NO_REASON"),
            ("  waive: NO_SAMPLE reason:", "WAIVER_NO_REASON"),
            ("  waive: NO_SAMPLE reason: 1234567", "WAIVER_NO_REASON"),
            ("  waive: NO_SAMPLE reason:   short  ", "WAIVER_NO_REASON"),
            ("  waive: reason: " + REASON, "WAIVER_INVALID"),
            ("  waive: NO_SAMPLE,,NO_MEASURED reason: " + REASON, "WAIVER_INVALID"),
            ("  waive: BOGUS_CODE reason: " + REASON, "WAIVER_INVALID"),
            ("  waive: no_sample reason: " + REASON, "WAIVER_INVALID"),
            ("  waive: NO_SAMPLE, bogus reason: " + REASON, "WAIVER_INVALID"),
        ],
    )
    def test_ac12_malformed_waiver_waives_nothing(self, tmp_path, line, code):
        spec = _raw_spec("- AC1: latency < 200 ms", line)
        _root, r, v = _go(tmp_path, spec, enforce=True)
        assert code in _codes(v), v
        assert "NO_SAMPLE" in _codes(v), v
        assert v.get("waived", []) == [], v
        assert r.returncode == 1, (r.returncode, v)
        if code == "WAIVER_NO_REASON":
            f = [x for x in v["findings"] if x["code"] == code][0]
            assert f["ac"] == "AC1", f

    def test_ac12_eight_character_reason_is_enough(self, tmp_path):
        spec = _raw_spec("- AC1: latency < 200 ms", "  waive: NO_SAMPLE reason:   abcdefgh  ")
        _root, _r, v = _go(tmp_path, spec)
        assert v["status"] == "clean", v
        assert [w["reason"] for w in v["waived"]] == ["abcdefgh"], v

    @pytest.mark.parametrize("sep", [" ", "; ", ", "])
    def test_ac12_separator_before_reason(self, tmp_path, sep):
        spec = _raw_spec("- AC1: latency < 200 ms", f"  waive: NO_SAMPLE{sep}reason: {REASON}")
        _root, _r, v = _go(tmp_path, spec)
        assert v["status"] == "clean", v
        assert _waived_codes(v) == ["NO_SAMPLE"], v

    def test_ac12_numbers_in_reason_create_no_threshold(self, tmp_path):
        spec = _raw_spec(
            "- AC1: prose share is stable",
            "  sample: samples/with01.txt",
            "  waive: NO_SAMPLE reason: p95 was under 300 ms and < 500 last week",
        )
        _root, _r, v = _go(tmp_path, spec)
        assert _codes(v) == [], v

    # ------------------------------------------------------------ AC13 ---
    @pytest.mark.parametrize(
        "lines",
        [
            ["- AC1: latency < 200 ms", f"  noted elsewhere, waive: NO_SAMPLE reason: {REASON}"],
            [f"- AC1: latency < 200 ms waive: NO_SAMPLE reason: {REASON}"],
            ["- AC1: latency < 200 ms", "  ```", f"  waive: NO_SAMPLE reason: {REASON}", "  ```"],
            ["- AC1: latency < 200 ms", f"  Note: `waive: NO_SAMPLE reason: {REASON}`"],
            ["- AC1: latency < 200 ms", f"  `waive: NO_SAMPLE reason: {REASON}`"],
            [f"waive: NO_SAMPLE reason: {REASON}", "", "- AC1: latency < 200 ms"],
            ["- AC1: latency < 200 ms", "", "## Notes", "", f"waive: NO_SAMPLE reason: {REASON}"],
        ],
    )
    def test_ac13_not_a_waiver_leaves_finding_and_raises_nothing(self, tmp_path, lines):
        _root, _r, v = _go(tmp_path, _raw_spec(*lines))
        assert _codes(v) == ["NO_SAMPLE"], v
        assert v.get("waived", []) == [], v

    @pytest.mark.parametrize(
        "line",
        [
            f"  WAIVE: NO_SAMPLE REASON: {REASON}",
            f"  - waive: NO_SAMPLE reason: {REASON}",
            f"  **waive: NO_SAMPLE reason: {REASON}",
            f"  * **waive: NO_SAMPLE reason: {REASON}",
            f"  1. waive: NO_SAMPLE reason: {REASON}",
        ],
    )
    def test_ac13_accepted_line_forms(self, tmp_path, line):
        _root, _r, v = _go(tmp_path, _raw_spec("- AC1: latency < 200 ms", line))
        assert v["status"] == "clean", v
        assert _waived_codes(v) == ["NO_SAMPLE"], v

    # ------------------------------------------------------------ AC14 ---
    def test_ac14_clean_verdict_keys(self, tmp_path):
        spec = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.1"))
        _root, _r, v = _go(tmp_path, spec)
        assert v["status"] == "clean" and set(v) == NEW_KEYS, v
        assert v["waived"] == [], v

    def test_ac14_flagged_verdict_keys(self, tmp_path):
        spec = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.31"))
        _root, _r, v = _go(tmp_path, spec)
        assert v["status"] == "flagged" and set(v) == NEW_KEYS, v
        assert v["waived"] == [], v
        assert all(set(f) == {"code", "ac", "detail"} for f in v["findings"]), v

    def test_ac14_unavailable_verdict_keys(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(1, "plain statement")))
        r = _run(tmp_path, root, base="0" * 40)
        v = _load(r)
        assert r.returncode == 2 and v["status"] == "unavailable", (r.returncode, v)
        assert set(v) == NEW_KEYS and v["waived"] == [], v

    def test_ac14_flag_expired_verdict_keys(self, tmp_path):
        root = _lot(tmp_path, _spec(_ac(1, "plain statement")))
        r = _run(tmp_path, root, today="2026-10-20")
        v = _load(r)
        assert r.returncode == 3 and v["status"] == "flag_expired", (r.returncode, v)
        assert set(v) == NEW_KEYS and v["waived"] == [], v

    def test_ac14_shadow_log_record_keys(self, tmp_path):
        log = tmp_path / "logs" / "shadow.jsonl"
        log.parent.mkdir()
        flagged = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.31"))
        _go(tmp_path, flagged, name="flagged", extra=["--shadow-log", log])
        waived = _raw_spec(
            "- AC1: prose share < 0.25",
            "  sample: samples/with01.txt",
            "  measured: 0.31",
            f"  waive: MEASURED_CONTRADICTS reason: {REASON}",
        )
        _go(tmp_path, waived, name="waived", extra=["--shadow-log", log])
        first, second = [json.loads(x) for x in log.read_text().splitlines()]
        keys = {"ts", "spec", "mode", "would_block", "codes", "waived"}
        assert set(first) == keys and set(second) == keys, (first, second)
        assert first["codes"] == ["MEASURED_CONTRADICTS"] and first["waived"] == [], first
        assert second["codes"] == [] and second["waived"] == ["MEASURED_CONTRADICTS"], second
        assert second["would_block"] is False, second

    # ------------------------------------------------------------ AC15 ---
    def test_ac15_flag_constants_in_verdict(self, tmp_path):
        spec = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.31"))
        _root, _r, v = _go(tmp_path, spec)
        assert v["flag"]["name"] == FLAG_NAME, v
        assert v["flag"]["expires"] == "2026-10-17", v
        assert v["flag"]["owner"] == "s3-bytedigger (MGR)", v

    def test_ac15_flags_catalog_has_no_entry(self, tmp_path):
        _root, _r, v = _go(tmp_path, _spec(_ac(1, "plain statement")))
        assert v["waived"] == [], v  # the script under test emits the new shape
        catalog = FLAGS_CATALOG.read_text(encoding="utf-8")
        assert FLAG_NAME not in catalog and "spec_sample" not in catalog

    def test_ac15_shadow_flagged_exits_0_and_enforce_flagged_exits_1(self, tmp_path):
        spec = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.31"))
        _root, r, v = _go(tmp_path, spec, name="shadow")
        assert v["status"] == "flagged" and v["mode"] == "shadow" and r.returncode == 0, v
        _root, r, v = _go(tmp_path, spec, enforce=True, name="enforce")
        assert v["status"] == "flagged" and v["mode"] == "enforce" and r.returncode == 1, v

    @pytest.mark.parametrize(
        "env",
        [
            {FLAG_NAME: "true"},
            {FLAG_NAME: "0"},
            {"HAL_SPEC_SAMPLE_WAIVE": "1", "HAL_SPEC_SAMPLE_STRICT": "1", "HAL_WAIVER_ENFORCE": "1"},
        ],
    )
    def test_ac15_only_the_one_flag_switches_enforcement(self, tmp_path, env):
        spec = _spec(_ac(1, "prose share < 0.25", "samples/with01.txt", "0.31"))
        _root, r, v = _go(tmp_path, spec, env_extra=env)
        assert v["mode"] == "shadow" and r.returncode == 0, (r.returncode, v)

    # ------------------------------------------------------------ AC16 ---
    E2E_LINE = "p95 less than or equal to {n} ms"

    def test_ac16_end_to_end(self, tmp_path):
        bad = _spec(_ac(1, self.E2E_LINE.format(n="1,000"), "samples/lat.txt", "1,250"))
        _root, r, v = _go(tmp_path, bad, enforce=True, name="bad")
        assert r.returncode == 1 and _codes(v) == ["MEASURED_CONTRADICTS"], (r.returncode, v)

        waived = _raw_spec(
            f"- AC1: {self.E2E_LINE.format(n='1,000')}",
            "  sample: `samples/lat.txt`",
            "  measured: 1,250",
            f"  waive: MEASURED_CONTRADICTS reason: {REASON}",
        )
        _root, r, v = _go(tmp_path, waived, enforce=True, name="waived")
        assert r.returncode == 0 and v["status"] == "clean", (r.returncode, v)
        assert _waived_codes(v) == ["MEASURED_CONTRADICTS"], v

        good = _spec(_ac(1, self.E2E_LINE.format(n="1,500"), "samples/lat.txt", "1,250"))
        _root, r, v = _go(tmp_path, good, enforce=True, name="good")
        assert r.returncode == 0 and v["status"] == "clean" and v["findings"] == [], (r.returncode, v)
        assert [(e["ac"], e["measured"]) for e in v["verified"]] == [("AC1", ["1250"])], v
        assert v["unverified"] == [], v

    # ------------------------------------------------------------ AC17 ---
    def _stub_tools(self, tmp_path: Path) -> Path:
        markers = tmp_path / "markers"
        markers.mkdir(exist_ok=True)
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        for tool in ("claude", "bun"):
            p = bindir / tool
            p.write_text(f'#!/bin/sh\necho called > "{markers / tool}"\nexit 0\n', encoding="utf-8")
            p.chmod(0o755)
        return markers

    @pytest.mark.parametrize("enforce", [False, True])
    def test_ac17_provider_and_billing_environment_do_not_matter(self, tmp_path, enforce):
        spec = _spec(_ac(1, "p95 < 1,000 ms", "samples/lat.txt", "1,250"))
        root = _lot(tmp_path, spec)
        markers = self._stub_tools(tmp_path)
        base_env = dict(ENFORCE) if enforce else {}
        env_b = dict(base_env)
        env_b["ANTHROPIC_API_KEY"] = "sk-dummy-not-a-real-key"
        env_b["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"
        ra = _run(tmp_path, root, env_extra=base_env)
        rb = _run(tmp_path, root, env_extra=env_b)
        assert ra.stdout == rb.stdout and ra.returncode == rb.returncode, (ra, rb)
        assert ra.returncode == (1 if enforce else 0), ra
        assert _codes(_load(ra)) == ["MEASURED_CONTRADICTS"], ra.stdout
        assert list(markers.iterdir()) == [], list(markers.iterdir())
