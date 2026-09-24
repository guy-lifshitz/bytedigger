"""bd#79 — the tree is English-only, and a lint keeps it that way.

#75/#76/#77 translated most prose and stopped short: 316 Cyrillic characters
across 12 files survived on `bea3f68`. Translating the remainder is a one-time
act; the reason it has to be done three times is that nothing ever refused the
next occurrence. So these ACs pin two things at once — the tree is clean, and
the check that says so is wired into the lint registry and into CI.

Cyrillic in THIS file is built with `chr()` from code points, deliberately: a RED test
whose fixtures are literal Cyrillic would need an allowlist entry of its own,
and an allowlist that has to cover the detector is not a narrow allowlist.

PRE-GREEN (`bea3f68`):
  AC1-AC4  FAIL — `bytedigger_engine.cyrillic_scan` does not exist.
  AC5      FAIL — 316 Cyrillic characters in 12 tracked files.
  AC6-AC7  FAIL — `cyrillic-prose-lint.py` does not exist.
  AC8      FAIL — `precommit_lints` has no `TEXT_LINTS` and no `driver_path`.
  AC9-AC10 FAIL — `scripts/commit_subject_lint.py` and `githooks/commit-msg`
                  do not exist.
  AC11     FAIL — `.github/workflows/ci.yml` has no `language` job.
  AC12     FAIL — the driver does not exist, so nothing is asserted about where
                  it lives.
  AC13     PASSES vacuously — the branch had no commits yet, so there were no
           subjects to read. Recorded rather than hidden: it becomes
           load-bearing the moment a commit lands, and 12/13 is the RED.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve()
_ENGINE_PY_ROOT = _THIS.parents[1]          # …/engine_py/
_REPO_ROOT = _THIS.parents[2]               # repository root

# The detector's own alphabet, spelled independently of the module under test.
# If the two ever disagree, that is a finding, not a convenience.
_CYRILLIC = re.compile("[" + chr(0x0400) + "-" + chr(0x04FF) + "]")

# A Cyrillic sample built from code points: the six letters of the Russian
# word for "hello".
_SAMPLE = "".join(chr(c) for c in (0x043F, 0x0440, 0x0438, 0x0432, 0x0435, 0x0442))

# The four files whose Cyrillic is behaviour rather than prose, and the exact
# number of Cyrillic characters each is allowed to carry. Spelled here so that
# the production allowlist is checked against a second declaration rather than
# against itself.
_EXPECTED_ALLOWLIST = {
    "engine_py/bytedigger_engine/scripts/lib/mutation_two_sidedness_verifier.py": 118,
    "engine_py/bytedigger_engine/scripts/lib/closure_evidence_verifier.py": 77,
    "engine_py/tests/test_phase_45_spec_decision_doc_injection.py": 30,
    "engine_py/tests/test_gh933_agent_sdk_stderr_outage.py": 15,
}

_DRIVER = _REPO_ROOT / "cyrillic-prose-lint.py"
_SUBJECT_LINT = _REPO_ROOT / "scripts" / "commit_subject_lint.py"


def _scan_module():
    """Import the scanner, or fail the AC with the reason rather than erroring."""
    try:
        from bytedigger_engine import cyrillic_scan  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - the pre-GREEN path
        pytest.fail(f"bytedigger_engine.cyrillic_scan is not importable: {exc}")
    return cyrillic_scan


def _run(argv, cwd=None):
    return subprocess.run(
        [sys.executable] + [str(a) for a in argv],
        cwd=str(cwd or _REPO_ROOT),
        capture_output=True,
        text=True,
    )


# ─── AC1-AC2: the scanner reports position, and stays quiet on ASCII ─────────

def test_ac1_scan_text_reports_every_cyrillic_character_with_its_position():
    scan = _scan_module()
    text = "clean line\nprefix " + _SAMPLE + " suffix\n"

    hits = list(scan.scan_text(text))

    assert len(hits) == len(_SAMPLE), (
        f"expected one hit per Cyrillic character, got {len(hits)}: {hits!r}"
    )
    first = hits[0]
    assert (first.line, first.column, first.char) == (2, 8, _SAMPLE[0]), (
        "a hit must carry a 1-based line, a 1-based column and the offending "
        f"character so the operator can find it; got {first!r}"
    )


def test_ac2_scan_text_is_silent_on_ascii_and_on_other_non_ascii():
    scan = _scan_module()
    # Greek and accented Latin are not Cyrillic. A detector that fires on
    # "any non-ASCII" would make the allowlist carry every degree sign and
    # em dash in the tree, and this repository's prose is full of both.
    assert list(scan.scan_text("plain ASCII — éè αβ °")) == []


# ─── AC3-AC4: the allowlist is well-formed, current, and narrow ──────────────

def test_ac3_every_allowlist_entry_declares_a_reason_and_a_character_budget():
    scan = _scan_module()

    assert scan.ALLOWLIST, "an empty allowlist would make AC4 and AC6 vacuous"
    for path, entry in scan.ALLOWLIST.items():
        assert isinstance(entry.get("reason"), str) and entry["reason"].strip(), (
            f"{path}: an allowlist entry without a stated reason is an "
            "unexplained exemption"
        )
        assert isinstance(entry.get("chars"), int) and entry["chars"] > 0, (
            f"{path}: an entry must pin the exact number of Cyrillic characters "
            "it licenses, otherwise new Cyrillic hides behind an old exemption"
        )


def test_ac4_every_allowlisted_file_exists_and_carries_exactly_its_budget():
    scan = _scan_module()

    for path, entry in scan.ALLOWLIST.items():
        target = _REPO_ROOT / path
        assert target.is_file(), (
            f"{path} is allowlisted and does not exist — a stale exemption "
            "silently widens the next file that takes its place"
        )
        actual = len(_CYRILLIC.findall(target.read_text(encoding="utf-8")))
        assert actual == entry["chars"], (
            f"{path}: allowlist licenses {entry['chars']} Cyrillic characters, "
            f"the file carries {actual}. Either the file gained prose that must "
            "be translated, or it lost characters and the budget must shrink."
        )


def test_ac6_the_allowlist_covers_exactly_the_four_declared_behaviour_files():
    scan = _scan_module()

    assert dict(sorted((p, e["chars"]) for p, e in scan.ALLOWLIST.items())) == dict(
        sorted(_EXPECTED_ALLOWLIST.items())
    ), (
        "the allowlist must be exactly the two spec-lint vocabularies and the "
        "two multibyte UTF-8 fixtures. Anything else is prose that was exempted "
        "instead of translated."
    )


# ─── AC5: the load-bearing one — the tracked tree is clean ──────────────────

def test_ac5_no_tracked_file_carries_unlicensed_cyrillic():
    scan = _scan_module()

    violations = scan.scan_tree(_REPO_ROOT)

    rendered = "\n".join(
        f"  {v.path}:{v.line}:{v.column} {v.char!r} U+{ord(v.char):04X}"
        for v in violations[:40]
    )
    assert not violations, (
        f"{len(violations)} unlicensed Cyrillic character(s) in "
        f"{len({v.path for v in violations})} tracked file(s):\n{rendered}"
    )


# ─── AC7: the driver's exit ladder ───────────────────────────────────────────

def test_ac7_driver_exit_ladder_is_zero_one_two(tmp_path):
    assert _DRIVER.is_file(), f"{_DRIVER} does not exist"

    clean = tmp_path / "clean.txt"
    clean.write_text("all ascii here\n", encoding="utf-8")
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("prose " + _SAMPLE + "\n", encoding="utf-8")

    ok = _run([_DRIVER, clean])
    assert ok.returncode == 0, f"clean file must exit 0, got {ok.returncode}: {ok.stderr}"

    bad = _run([_DRIVER, dirty])
    assert bad.returncode == 1, f"violating file must exit 1, got {bad.returncode}"
    assert "dirty.txt:1:7" in bad.stdout, (
        f"the violation must be printed with its position; got {bad.stdout!r}"
    )

    missing = _run([_DRIVER, tmp_path / "nope.txt"])
    assert missing.returncode == 2, (
        "an unreadable path is a driver error, not a clean run — a lint that "
        f"cannot read its input must not report success; got {missing.returncode}"
    )


# ─── AC8: the name is in the registry and the registry can build its command ─

def test_ac8_registry_declares_the_lint_and_resolves_a_driver_that_exists():
    from bytedigger_engine import precommit_lints  # noqa: PLC0415

    text_lints = getattr(precommit_lints, "TEXT_LINTS", None)
    assert text_lints == ["cyrillic-prose-lint"], (
        f"TEXT_LINTS must declare the lint; got {text_lints!r}"
    )

    classified = precommit_lints.classify_staged(
        ["README.md", "engine_py/tests/test_x.py", "engine_py/img.png"]
    )
    assert classified["texts"] == ["README.md", "engine_py/tests/test_x.py"], (
        "classify_staged must route text files to the new lane and leave binary "
        f"files alone; got {classified.get('texts')!r}"
    )
    assert not precommit_lints.nothing_to_lint(classified), (
        "a staged text file is now something to lint"
    )

    cmds = precommit_lints.build_lint_commands(
        specs=[], tests=[], build_dir="/nonexistent/build", texts=["README.md"]
    )
    text_cmds = [c for c in cmds if c["lint"] == "cyrillic-prose-lint"]
    assert len(text_cmds) == 1, f"expected one command, got {text_cmds!r}"
    argv = text_cmds[0]["argv"]
    assert Path(argv[0]).is_file(), (
        f"the registry resolved the driver to {argv[0]!r}, which is not on disk. "
        "A declared name whose driver cannot be executed is the defect #78 is "
        "about; the driver directory must be declared, not inherited from a "
        "build_dir that does not hold it."
    )
    assert argv[-1] == "README.md"


# ─── AC9-AC10: Cyrillic commit subjects are refused ─────────────────────────

def test_ac9_commit_subject_lint_refuses_cyrillic_and_accepts_ascii(tmp_path):
    assert _SUBJECT_LINT.is_file(), f"{_SUBJECT_LINT} does not exist"

    good = tmp_path / "good.txt"
    good.write_text("lang: English-only tree\n\nbody\n", encoding="utf-8")
    assert _run([_SUBJECT_LINT, good]).returncode == 0

    bad = tmp_path / "bad.txt"
    bad.write_text("bd#79: " + _SAMPLE + "\n\nbody\n", encoding="utf-8")
    refused = _run([_SUBJECT_LINT, bad])
    assert refused.returncode == 1, (
        f"a Cyrillic subject must be refused; got {refused.returncode}"
    )
    assert "bd#79" in refused.stdout + refused.stderr, (
        "the refusal must quote the subject it refused"
    )

    # Only the subject. The body is not the subject, and widening the check to
    # the whole message would refuse a commit that legitimately quotes a
    # Cyrillic pattern it is changing.
    body_only = tmp_path / "body.txt"
    body_only.write_text("ascii subject\n\nquoted: " + _SAMPLE + "\n", encoding="utf-8")
    assert _run([_SUBJECT_LINT, body_only]).returncode == 0, (
        "Cyrillic in the body is not a subject violation"
    )


def test_ac10_commit_msg_hook_is_versioned_executable_and_calls_the_lint():
    hook = _REPO_ROOT / "githooks" / "commit-msg"
    assert hook.is_file(), f"{hook} does not exist"
    assert "commit_subject_lint" in hook.read_text(encoding="utf-8"), (
        "the hook must delegate to the lint rather than reimplement it"
    )

    if not (_REPO_ROOT / ".git").exists():
        pytest.skip(
            "no .git in this tree (clean-room ships via `git archive`), so the "
            "index mode of githooks/commit-msg has no corpus to read here"
        )
    mode = subprocess.run(
        ["git", "ls-files", "-s", "githooks/commit-msg"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    ).stdout.split()
    assert mode and mode[0] == "100755", (
        "git skips a non-executable hook without a word, which reads as a "
        f"passing commit with no check at all; index mode is {mode[:1]!r}"
    )


# ─── AC11: CI runs both linters, so the check is live on merge ──────────────

def test_ac11_ci_has_a_language_job_running_both_linters():
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert re.search(r"^  language:$", ci, re.M), (
        "ci.yml must carry a `language` job — the git hook is inert until it is "
        "installed, so CI is the only enforcement that is live on merge"
    )
    assert "cyrillic-prose-lint.py" in ci, "the language job must run the tree lint"
    assert "commit_subject_lint.py" in ci, "the language job must run the subject lint"


# ─── AC12: the driver cannot break the packaging invariants ────────────────

def test_ac12_driver_is_outside_the_package_and_the_library_has_no_path_hacks():
    package = _ENGINE_PY_ROOT / "bytedigger_engine"

    assert not _DRIVER.is_relative_to(package), (
        "the driver name is hyphenated (the registry's convention), and a "
        "hyphenated *.py inside the package is unimportable — CI's import "
        "smoke globs `bytedigger_engine/*.py` and would fail on it. The "
        "driver belongs beside core-boundary-lint.py at the repo root."
    )

    library = package / "cyrillic_scan.py"
    assert library.is_file(), f"{library} does not exist"

    # By AST, not by substring: the module's own prose explains why the
    # bootstrap is not here, and a substring check would fire on that sentence
    # while missing `getattr(sys, "path")`.
    tree = ast.parse(library.read_text(encoding="utf-8"))
    touches_path = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    ]
    assert not touches_path, (
        "bd#44 AC7 forbids sys.path calls inside the package; the bootstrap "
        f"belongs in the root driver. Offending line(s): {touches_path}"
    )


# ─── AC13: the enforcement is run against real data, not only fixtures ─────

def test_ac13_no_commit_on_this_branch_has_a_cyrillic_subject():
    if not (_REPO_ROOT / ".git").exists():
        pytest.skip("no .git in this tree, so there are no commit subjects to read")

    base = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if base.returncode != 0:
        pytest.skip(f"no origin/main to diff against: {base.stderr.strip()}")

    subjects = subprocess.run(
        ["git", "log", "--format=%s", f"{base.stdout.strip()}..HEAD"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    offenders = [s for s in subjects if _CYRILLIC.search(s)]
    assert not offenders, (
        f"commit subject(s) on this branch carry Cyrillic: {offenders!r}"
    )
