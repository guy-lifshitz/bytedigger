"""RED tests for bd#243 - GREEN cannot start without an approving gate verdict on
the current spec revision.

Spec: docs/decisions/2026-10-03-bd243-green-entry-guard.md (AC1-AC37, r3).

Unit under test (ABSENT pre-GREEN):
  - engine_py/bytedigger_engine/green_entry_guard.py
      check(root: str, staged: list[str], env: Mapping[str, str]) -> list[str]
  - engine_py/bytedigger_engine/precommit_enforce.py::main calls it (AC15).
  - error_codes.py / both ERROR_CODES.md / flags_catalog.py (AC17).

Fixtures are REAL temp git repos (base branch `main`, lot branch, spec and gate
docs committed on the lot branch, a non-test source file staged). The anchor is
the sha256 of the spec bytes on disk, written as the verdict-anchor block that
`verdict_verify.ANCHOR_RE` already defines.

Pre-GREEN every test FAILS at assert time: the module is imported INSIDE each
test (after an explicit existence assertion), never at collection. AC17 fails
because the five codes / two flags are not yet catalogued.

Hermetic: GIT_CONFIG_GLOBAL/SYSTEM neutralised; everything under tmp_path.
No sys.path mutation, no `from conftest import`. No timing or contended
resource anywhere (workflows.md section 1i): every state is pre-staged on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import error_codes, precommit_lints

PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
ENGINE_ROOT = PACKAGE_DIR.parent
REPO_ROOT = ENGINE_ROOT.parent

GUARD_MODULE = PACKAGE_DIR / "green_entry_guard.py"
INSTALLER = REPO_ROOT / "scripts" / "install_git_hooks.py"
GITHOOKS_DIR = REPO_ROOT / "githooks"
FLAG_OWNER_LINT = REPO_ROOT / "scripts" / "flag_owner_lint.py"
FLAGS_CATALOG = PACKAGE_DIR / "flags_catalog.py"
ERROR_CODES_MD = (ENGINE_ROOT / "ERROR_CODES.md", PACKAGE_DIR / "ERROR_CODES.md")

C_MISSING = "E_GREEN_GATE_MISSING"
C_REJECTED = "E_GREEN_GATE_REJECTED"
C_STALE = "E_GREEN_GATE_STALE"
C_UNREADABLE = "E_GREEN_GATE_UNREADABLE"
C_NO_REASON = "E_GREEN_GATE_BYPASS_NO_REASON"
ALL_CODES = (C_MISSING, C_REJECTED, C_STALE, C_UNREADABLE, C_NO_REASON)

SRC = "src/app.py"
STEM = "2026-10-03-widget"
KILL = "HAL_GREEN_GATE_GUARD"
REASON = "HAL_GREEN_GATE_BYPASS_REASON"

_GIT_ENV_KEYS = ("HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM")


# --- fixture helpers ---------------------------------------------------------


def _hermetic_git_env(tmp_path: Path) -> dict:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    empty_global = fake_home / ".gitconfig"
    empty_global.write_text("")
    env = dict(os.environ)
    for key in ("PYTHONPATH", "BD66_LINT_DIR", KILL, REASON):
        env.pop(key, None)
    env["HOME"] = str(fake_home)
    env["GIT_CONFIG_GLOBAL"] = str(empty_global)
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args, cwd, env):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True
    )


def _git_ok(args, cwd, env):
    r = _git(args, cwd, env)
    assert r.returncode == 0, f"git {args} failed: {r.stdout!r} {r.stderr!r}"
    return r


def _new_repo(
    tmp_path: Path,
    name: str = "repo",
    main_files: dict | None = None,
    base_branch: str = "main",
):
    """Repo with one commit on the base branch (default `main`, optionally
    carrying main_files), then a lot branch checked out. Returns (repo, env)."""
    env = _hermetic_git_env(tmp_path)
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True)
    _git_ok(["init", "-q", "-b", base_branch], repo, env)
    for key, value in (
        ("user.email", "bd243@example.com"),
        ("user.name", "bd243 tester"),
        ("commit.gpgsign", "false"),
    ):
        _git_ok(["config", key, value], repo, env)
    (repo / "README.md").write_text("base\n")
    for rel, data in (main_files or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git_ok(["add", "-A"], repo, env)
    _git_ok(["commit", "-q", "-m", "base"], repo, env)
    _git_ok(["checkout", "-q", "-b", "lot-243"], repo, env)
    return repo, env


def _spec_rel(stem: str) -> str:
    return f"docs/decisions/{stem}.md"


def _write_spec(repo: Path, stem: str, text: str) -> bytes:
    path = repo / _spec_rel(stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode()
    path.write_bytes(data)
    return data


def _anchor(stem: str, spec_bytes: bytes) -> str:
    digest = hashlib.sha256(spec_bytes).hexdigest()
    return f"<!-- verdict-anchor\nspec: {_spec_rel(stem)} sha256:{digest}\n-->\n"


def _write_gate(
    repo: Path,
    stem: str,
    n: int,
    verdict: str | None,
    anchor_for: bytes | None = None,
    eol: str = "\n",
    trailing_newline: bool = True,
    file_stem: str | None = None,
) -> Path:
    """Gate doc; the verdict (if any) is the LAST line. `file_stem` names the
    FILE (e.g. the key form `<date>-bdN`) while the anchor still binds `stem`."""
    body = f"# gate r{n} for {stem}\n\nReview prose. Mentions spec r{n}.\n\n"
    if anchor_for is not None:
        body += _anchor(stem, anchor_for) + "\n"
    if verdict is not None:
        body += f"VERDICT: {verdict}" + (eol if trailing_newline else "")
    path = repo / "docs" / "decisions" / f"{file_stem or stem}-gate-r{n}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode())
    return path


def _commit_docs(repo: Path, env: dict, msg: str = "docs") -> None:
    _git_ok(["add", "-A", "docs"], repo, env)
    _git_ok(["commit", "-q", "-m", msg], repo, env)


def _stage_source(repo: Path, env: dict, rel: str = SRC) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n")
    _git_ok(["add", rel], repo, env)


def _import_guard():
    assert GUARD_MODULE.is_file(), (
        f"bd#243: {GUARD_MODULE} must exist (spec section 2). Pre-GREEN it does "
        f"not - FAIL expected here, as an assertion and not an import accident."
    )
    from bytedigger_engine import green_entry_guard  # noqa: PLC0415

    return green_entry_guard


def _check(repo, env, monkeypatch, staged=None, extra=None):
    guard = _import_guard()
    for key in _GIT_ENV_KEYS:
        monkeypatch.setenv(key, env[key])
    monkeypatch.delenv(KILL, raising=False)
    monkeypatch.delenv(REASON, raising=False)
    monkeypatch.chdir(repo)
    mapping = {k: env[k] for k in _GIT_ENV_KEYS}
    mapping.update(extra or {})
    result = guard.check(str(repo), list(staged) if staged is not None else [SRC], mapping)
    assert isinstance(result, list) and all(isinstance(x, str) for x in result), (
        f"bd#243: check() must return list[str], got {result!r}"
    )
    return result


def _code(line: str) -> str:
    return line.split(":", 1)[0].strip()


def _assert_single(lines, code, spec_stem=STEM, gate_name=None):
    assert len(lines) == 1, f"expected exactly one refusal line, got {lines!r}"
    line = lines[0]
    assert _code(line) == code, f"expected {code}, got {line!r}"
    assert f"spec={_spec_rel(spec_stem)}" in line or _spec_rel(spec_stem) in line, (
        f"refusal must name the spec {_spec_rel(spec_stem)}: {line!r}"
    )
    if gate_name is not None:
        assert gate_name in line, f"refusal must name gate doc {gate_name}: {line!r}"


def _bypass_lines(repo: Path, env: dict) -> list[dict]:
    common = _git_ok(["rev-parse", "--git-common-dir"], repo, env).stdout.strip()
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = repo / common_path
    log = common_path / "bytedigger" / "bypass.log"
    if not log.is_file():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def _rejected_lot(tmp_path, name="rej"):
    """Lot with a spec and a single REJECTED gate carrying a current anchor."""
    repo, env = _new_repo(tmp_path, name)
    spec = _write_spec(repo, STEM, "# widget spec\n\nbody v1\n")
    _write_gate(repo, STEM, 1, "REJECTED", anchor_for=spec)
    _commit_docs(repo, env)
    _stage_source(repo, env)
    return repo, env, spec


def _approved_lot(tmp_path, name="ok", verdict="APPROVED"):
    repo, env = _new_repo(tmp_path, name)
    spec = _write_spec(repo, STEM, "# widget spec\n\nbody v1\n")
    _write_gate(repo, STEM, 1, verdict, anchor_for=spec)
    _commit_docs(repo, env)
    _stage_source(repo, env)
    return repo, env, spec


# --- AC1 ---------------------------------------------------------------------


def test_ac1_newest_rejected_gate_refuses_after_spec_edit(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    v1 = _write_spec(repo, STEM, "# widget spec\n\nbody v1\n")
    _write_gate(repo, STEM, 1, "REJECTED", anchor_for=v1)
    _write_gate(repo, STEM, 2, "REJECTED", anchor_for=v1)
    _commit_docs(repo, env, "spec v1 + gates")
    _write_spec(repo, STEM, "# widget spec\n\nbody v2 (edited after reject)\n")
    _commit_docs(repo, env, "spec v2")
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    _assert_single(lines, C_REJECTED, gate_name=f"{STEM}-gate-r2.md")
    assert f"{STEM}-gate-r1.md" not in lines[0], (
        f"newest gate is r2, the line must not name r1: {lines[0]!r}"
    )


# --- AC2 ---------------------------------------------------------------------


def test_ac2_approved_with_current_anchor_is_allowed(tmp_path, monkeypatch):
    repo, env, _ = _approved_lot(tmp_path)

    assert _check(repo, env, monkeypatch) == []

    # Positive control: the guard is live on this very fixture - changing the
    # spec bytes (anchor now stale) turns the allow into a refusal.
    _write_spec(repo, STEM, "# widget spec\n\nbody v1 EDITED\n")
    _commit_docs(repo, env, "edit")
    lines = _check(repo, env, monkeypatch)
    _assert_single(lines, C_STALE)


# --- AC3 ---------------------------------------------------------------------


def test_ac3_approved_but_anchor_of_old_spec_is_stale(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    old = _write_spec(repo, STEM, "# widget spec\n\nbody v1\n")
    _write_gate(repo, STEM, 1, "APPROVED", anchor_for=old)
    _commit_docs(repo, env)
    _write_spec(repo, STEM, "# widget spec\n\nbody v2\n")
    _commit_docs(repo, env, "edit spec")
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    _assert_single(lines, C_STALE, gate_name=f"{STEM}-gate-r1.md")


# --- AC4 ---------------------------------------------------------------------


def test_ac4_approved_without_anchor_is_stale(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    _write_spec(repo, STEM, "# widget spec\n\nbody v1\n")
    _write_gate(repo, STEM, 1, "APPROVED", anchor_for=None)
    _commit_docs(repo, env)
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    _assert_single(lines, C_STALE)


# --- AC5 ---------------------------------------------------------------------


def test_ac5_escalation_marker_passes_and_is_logged(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    v1 = _write_spec(repo, STEM, "# widget spec\n\nbody v1\n")
    _write_gate(repo, STEM, 1, "REJECTED", anchor_for=v1)
    _write_gate(repo, STEM, 2, "REJECTED", anchor_for=v1)
    _commit_docs(repo, env)
    _write_spec(repo, STEM, "# widget spec\n\nbody v3 after reject\n")
    _commit_docs(repo, env, "edit")
    _stage_source(repo, env)

    # Control (same fixture, no marker yet): refused, nothing logged.
    before = _check(repo, env, monkeypatch)
    _assert_single(before, C_REJECTED)
    assert _bypass_lines(repo, env) == []

    (repo / "docs" / "decisions" / f"{STEM}-escalation.md").write_text(
        "# escalation\n\nESCALATION: owner accepted risk\n"
    )
    _commit_docs(repo, env, "escalation")

    assert _check(repo, env, monkeypatch) == []

    entries = _bypass_lines(repo, env)
    assert len(entries) == 1, f"exactly one bypass log line expected: {entries!r}"
    entry = entries[0]
    assert entry["kind"] == "escalation"
    assert STEM in str(entry["spec"])
    assert "owner accepted risk" in entry["reason"]
    assert isinstance(entry["ts"], str) and entry["ts"]


# --- AC6 ---------------------------------------------------------------------


@pytest.mark.parametrize("marker_line", ["ESCALATION:", "ESCALATION:   "])
def test_ac6_blank_escalation_marker_falls_through_to_gate_rules(
    tmp_path, monkeypatch, marker_line
):
    # Newest gate REJECTED -> REJECTED.
    repo, env, _ = _rejected_lot(tmp_path, "rej")
    (repo / "docs" / "decisions" / f"{STEM}-escalation.md").write_text(
        f"# escalation\n\n{marker_line}\n"
    )
    _commit_docs(repo, env, "blank escalation")
    _assert_single(_check(repo, env, monkeypatch), C_REJECTED)
    assert _bypass_lines(repo, env) == []

    # No gate doc at all -> MISSING.
    repo2, env2 = _new_repo(tmp_path, "missing")
    _write_spec(repo2, STEM, "# widget spec\n")
    (repo2 / "docs" / "decisions" / f"{STEM}-escalation.md").write_text(
        f"{marker_line}\n"
    )
    _commit_docs(repo2, env2)
    _stage_source(repo2, env2)
    _assert_single(_check(repo2, env2, monkeypatch), C_MISSING)
    assert _bypass_lines(repo2, env2) == []


# --- AC7 ---------------------------------------------------------------------


def test_ac7_spec_without_any_gate_doc_is_missing(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    _write_spec(repo, STEM, "# widget spec\n")
    _commit_docs(repo, env)
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    _assert_single(lines, C_MISSING)
    assert "gate=-" in lines[0], f"no gate doc exists, expected 'gate=-': {lines[0]!r}"


# --- AC8 ---------------------------------------------------------------------


def test_ac8_r10_is_newer_than_r9_numeric_order(tmp_path, monkeypatch):
    # r2 APPROVED+current, r10 REJECTED: r10 is newest -> REJECTED.
    repo, env = _new_repo(tmp_path, "a")
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, 2, "APPROVED", anchor_for=spec)
    _write_gate(repo, STEM, 10, "REJECTED", anchor_for=spec)
    _commit_docs(repo, env)
    _stage_source(repo, env)
    _assert_single(
        _check(repo, env, monkeypatch), C_REJECTED, gate_name=f"{STEM}-gate-r10.md"
    )

    # Mirror: r9 REJECTED, r10 APPROVED+current: lexical order would pick r9.
    repo2, env2 = _new_repo(tmp_path, "b")
    spec2 = _write_spec(repo2, STEM, "# widget spec\n")
    _write_gate(repo2, STEM, 9, "REJECTED", anchor_for=spec2)
    _write_gate(repo2, STEM, 10, "APPROVED", anchor_for=spec2)
    _commit_docs(repo2, env2)
    _stage_source(repo2, env2)
    assert _check(repo2, env2, monkeypatch) == []


# --- AC9 ---------------------------------------------------------------------


def test_ac9_newest_gate_without_verdict_line_is_unreadable(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, 1, "APPROVED", anchor_for=spec)
    _write_gate(repo, STEM, 2, None, anchor_for=spec)
    _commit_docs(repo, env)
    _stage_source(repo, env)

    _assert_single(
        _check(repo, env, monkeypatch), C_UNREADABLE, gate_name=f"{STEM}-gate-r2.md"
    )

    # Positive control: giving r2 a verdict line makes the same lot pass.
    _write_gate(repo, STEM, 2, "APPROVED", anchor_for=spec)
    _commit_docs(repo, env, "r2 verdict")
    assert _check(repo, env, monkeypatch) == []


# --- AC10 --------------------------------------------------------------------


def test_ac10_only_tests_and_docs_staged_is_not_green_entry(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)
    # Drop the staged source; stage only tests and docs.
    _git_ok(["reset", "-q", "HEAD", "--", SRC], repo, env)
    non_source = {
        "tests/test_widget.py": "def test_x():\n    assert True\n",
        "pkg/test_other.py": "def test_y():\n    assert True\n",
        "web/__tests__/a.test.ts": "test('a', () => {});\n",
        "docs/notes.md": "# notes\n",
        "README2.md": "# readme\n",
    }
    for rel, text in non_source.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        _git_ok(["add", rel], repo, env)

    assert _check(repo, env, monkeypatch, staged=list(non_source)) == []

    # Positive control: same lot, a source path added to the staged set refuses.
    lines = _check(repo, env, monkeypatch, staged=[*non_source, SRC])
    _assert_single(lines, C_REJECTED)


# --- AC11 --------------------------------------------------------------------


def test_ac11_edit_of_preexisting_spec_is_not_a_lot_spec(tmp_path, monkeypatch):
    old_stem = "2026-01-01-oldthing"
    repo, env = _new_repo(
        tmp_path,
        main_files={_spec_rel(old_stem): b"# old spec\n"},
    )
    old = _write_spec(repo, old_stem, "# old spec\n\nedited on the lot branch\n")
    _write_gate(repo, old_stem, 1, "REJECTED", anchor_for=old)
    _commit_docs(repo, env)
    _stage_source(repo, env)

    assert _check(repo, env, monkeypatch) == []

    # Positive control: a spec ADDED on this lot is enforced, and only it.
    _write_spec(repo, STEM, "# new spec\n")
    _commit_docs(repo, env, "new spec")
    lines = _check(repo, env, monkeypatch)
    _assert_single(lines, C_MISSING, spec_stem=STEM)
    assert old_stem not in lines[0]


# --- AC12 --------------------------------------------------------------------


def test_ac12_kill_switch_with_reason_allows_and_logs(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)

    # Control: no kill switch -> refused.
    _assert_single(_check(repo, env, monkeypatch), C_REJECTED)
    assert _bypass_lines(repo, env) == []

    extra = {KILL: "0", REASON: "hotfix approved by owner"}
    assert _check(repo, env, monkeypatch, extra=extra) == []

    entries = _bypass_lines(repo, env)
    assert len(entries) == 1, f"exactly one bypass log line expected: {entries!r}"
    assert entries[0]["kind"] == "kill_switch"
    assert entries[0]["spec"] is None
    assert "hotfix approved by owner" in entries[0]["reason"]
    assert isinstance(entries[0]["ts"], str) and entries[0]["ts"]


# --- AC13 --------------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "   \t"])
def test_ac13_kill_switch_without_reason_is_refused(tmp_path, monkeypatch, reason):
    repo, env, _ = _rejected_lot(tmp_path)
    extra = {KILL: "0"}
    if reason is not None:
        extra[REASON] = reason

    lines = _check(repo, env, monkeypatch, extra=extra)

    assert len(lines) == 1 and _code(lines[0]) == C_NO_REASON, (
        f"expected exactly one {C_NO_REASON} line (checks must not run), got {lines!r}"
    )
    assert _bypass_lines(repo, env) == [], "a refused bypass must not be logged"


def test_ac13_control_guard_unset_with_reason_still_checks(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)

    lines = _check(repo, env, monkeypatch, extra={REASON: "irrelevant"})

    _assert_single(lines, C_REJECTED)
    assert _bypass_lines(repo, env) == []


# --- AC14 --------------------------------------------------------------------


def test_ac14_two_specs_one_approved_one_stale_reports_only_the_stale(
    tmp_path, monkeypatch
):
    good_stem = "2026-10-03-good"
    stale_stem = "2026-10-03-stale"
    repo, env = _new_repo(tmp_path)
    good = _write_spec(repo, good_stem, "# good spec\n")
    _write_gate(repo, good_stem, 1, "APPROVED", anchor_for=good)
    stale_v1 = _write_spec(repo, stale_stem, "# stale spec v1\n")
    _write_gate(repo, stale_stem, 1, "APPROVED", anchor_for=stale_v1)
    _commit_docs(repo, env)
    _write_spec(repo, stale_stem, "# stale spec v2\n")
    _commit_docs(repo, env, "edit stale")
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    _assert_single(lines, C_STALE, spec_stem=stale_stem)
    assert good_stem not in lines[0]


def test_ac14_every_failing_spec_is_reported_without_early_exit(tmp_path, monkeypatch):
    a, b = "2026-10-03-aaa", "2026-10-03-bbb"
    repo, env = _new_repo(tmp_path)
    _write_spec(repo, a, "# a\n")  # no gate -> MISSING
    sb = _write_spec(repo, b, "# b\n")
    _write_gate(repo, b, 1, "REJECTED", anchor_for=sb)  # -> REJECTED
    _commit_docs(repo, env)
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    assert len(lines) == 2, f"one line per failing spec expected: {lines!r}"
    by_code = {_code(ln): ln for ln in lines}
    assert set(by_code) == {C_MISSING, C_REJECTED}
    assert a in by_code[C_MISSING] and b in by_code[C_REJECTED]


# --- AC15: end to end through the installed hook -----------------------------


def _hooked_lot(tmp_path, name, verdict, binary=False):
    repo, env = _new_repo(tmp_path, name)
    spec = _write_spec(repo, STEM, "# widget spec\n\nbody v1\n")
    _write_gate(repo, STEM, 1, verdict, anchor_for=spec)
    _commit_docs(repo, env)
    # Expose the REAL production layer at the paths the hook execs.
    (repo / "engine_py").mkdir()
    os.symlink(PACKAGE_DIR, repo / "engine_py" / "bytedigger_engine", target_is_directory=True)
    os.symlink(GITHOOKS_DIR, repo / "githooks", target_is_directory=True)
    os.symlink(REPO_ROOT / "scripts", repo / "scripts", target_is_directory=True)
    drivers = tmp_path / f"drivers-{name}"
    drivers.mkdir()
    env["BD66_LINT_DIR"] = str(drivers)
    install = subprocess.run(
        [sys.executable, str(INSTALLER), "--install", "--root", str(repo)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, (
        f"arrange: installer failed rc={install.returncode} {install.stderr!r}"
    )
    if binary:
        # Only a binary/unclassified source path: nothing_to_lint is True, so a
        # guard placed after that early return in main() is never reached.
        (repo / "assets").mkdir()
        (repo / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n bd243 fixture")
        _git_ok(["add", "assets/logo.png"], repo, env)
    else:
        _stage_source(repo, env)
    return repo, env


def test_ac15_real_commit_refused_on_rejected_lot(tmp_path):
    repo, env = _hooked_lot(tmp_path, "rej", "REJECTED")
    head_before = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()

    result = _git(["commit", "-m", "green start"], repo, env)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"commit must be refused: {output!r}"
    head_after = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()
    assert head_after == head_before, "no new commit may exist after a refusal"
    assert C_REJECTED in output, f"expected {C_REJECTED} in hook output: {output!r}"


def test_ac15_real_commit_succeeds_on_approved_lot(tmp_path):
    repo, env = _hooked_lot(tmp_path, "ok", "APPROVED")
    head_before = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()

    result = _git(["commit", "-m", "green start"], repo, env)
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"commit must succeed: {output!r}"
    head_after = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()
    assert head_after != head_before, "exactly one new commit expected"
    count = _git_ok(["rev-list", "--count", f"{head_before}..HEAD"], repo, env)
    assert count.stdout.strip() == "1"
    # The guard must actually exist for this to be more than a no-op pass.
    assert GUARD_MODULE.is_file()


# --- AC16 --------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["crlf_verdict", "whole_doc_crlf", "bare_cr_at_eof"])
def test_ac16_crlf_verdict_line_is_tolerated(tmp_path, monkeypatch, shape):
    repo, env = _new_repo(tmp_path)
    spec = _write_spec(repo, STEM, "# widget spec\n")
    gate = _write_gate(
        repo, STEM, 1, "APPROVE", anchor_for=spec, eol="\r\n",
        trailing_newline=(shape != "bare_cr_at_eof"),
    )
    raw = gate.read_bytes()
    if shape == "whole_doc_crlf":
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    elif shape == "bare_cr_at_eof":
        raw = raw + b"\r"
    gate.write_bytes(raw)
    assert raw.endswith(b"APPROVE\r\n") or raw.endswith(b"APPROVE\r")
    if shape == "whole_doc_crlf":
        assert b"-->\r\n" in raw, "arrange: anchor lines must be CRLF too"
    _commit_docs(repo, env)
    _stage_source(repo, env)

    assert _check(repo, env, monkeypatch) == []

    # Positive control: a CRLF REJECT verdict on the same shape is refused.
    _write_gate(repo, STEM, 1, "REJECT", anchor_for=spec, eol="\r\n")
    _commit_docs(repo, env, "reject")
    _assert_single(_check(repo, env, monkeypatch), C_REJECTED)


# --- AC17: catalog / doc parity ----------------------------------------------


def test_ac17_error_codes_registered_in_error_codes_py():
    for code in ALL_CODES:
        assert code in error_codes.ERROR_CODES, f"{code} missing from error_codes.ERROR_CODES"
        assert error_codes.ERROR_CODES[code].strip(), f"{code} needs a description"


@pytest.mark.parametrize("md_path", ERROR_CODES_MD, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_ac17_error_codes_documented_in_both_error_codes_md(md_path):
    text = md_path.read_text()
    for code in ALL_CODES:
        assert re.search(rf"^- `{code}` ", text, re.MULTILINE), (
            f"{code} missing from {md_path} (format: - `CODE` - description)"
        )


def test_ac17_flags_catalogued_and_flag_owner_lint_passes():
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    flags = flags_catalog.FLAGS
    assert KILL in flags and REASON in flags, "both kill-switch vars must be catalogued"
    guard = flags[KILL]
    assert guard.get("kind") in ("flag", "gate"), "must be a rollout kind so the lint covers it"
    assert isinstance(guard.get("owner"), str) and guard["owner"].strip()
    assert str(guard.get("provenance", "")).startswith("introduced: bd#243")
    assert str(flags[REASON].get("description", "")).strip()

    result = subprocess.run(
        [sys.executable, str(FLAG_OWNER_LINT)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"flag_owner_lint rc={result.returncode} {result.stdout!r} {result.stderr!r}"
    )


# =============================================================================
# r2 additions: AC18-AC30 (gate r1 findings folded into the spec)
# =============================================================================


def _common_dir(repo: Path, env: dict) -> Path:
    common = Path(_git_ok(["rev-parse", "--git-common-dir"], repo, env).stdout.strip())
    return common if common.is_absolute() else repo / common


# --- AC18: unreadable spec / gate file fails closed --------------------------


@pytest.mark.parametrize("victim", ["spec", "gate"])
def test_ac18_unreadable_spec_or_gate_file_is_unreadable(tmp_path, monkeypatch, victim):
    repo, env, _ = _approved_lot(tmp_path)
    assert _check(repo, env, monkeypatch) == [], "control: the intact lot is allowed"

    name = f"{STEM}.md" if victim == "spec" else f"{STEM}-gate-r1.md"
    path = repo / "docs" / "decisions" / name
    saved = path.read_bytes()
    path.unlink()
    path.mkdir()  # a directory in its place: unreadable even when running as root

    lines = _check(repo, env, monkeypatch)
    assert len(lines) == 1 and _code(lines[0]) == C_UNREADABLE, (
        f"unreadable {victim} must refuse with {C_UNREADABLE}, got {lines!r}"
    )

    path.rmdir()
    path.write_bytes(saved)
    assert _check(repo, env, monkeypatch) == [], "restored lot is allowed again"


# --- AC19: bypass log write failure refuses ----------------------------------


def test_ac19_bypass_log_unwritable_on_escalation_pass_refuses(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)
    (repo / "docs" / "decisions" / f"{STEM}-escalation.md").write_text(
        "ESCALATION: owner accepted risk\n"
    )
    _commit_docs(repo, env, "escalation")
    blocker = _common_dir(repo, env) / "bytedigger"
    blocker.write_text("not a directory\n")  # a regular file where the dir must go

    lines = _check(repo, env, monkeypatch)

    assert len(lines) == 1 and _code(lines[0]) == C_UNREADABLE, (
        f"a bypass that cannot be recorded must be refused, got {lines!r}"
    )

    # Positive control: with the blocker removed the same lot is allowed + logged.
    blocker.unlink()
    assert _check(repo, env, monkeypatch) == []
    assert len(_bypass_lines(repo, env)) == 1


def test_ac19_bypass_log_unwritable_on_kill_switch_skip_refuses(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)
    (_common_dir(repo, env) / "bytedigger").write_text("not a directory\n")

    lines = _check(repo, env, monkeypatch, extra={KILL: "0", REASON: "owner says go"})

    assert len(lines) == 1 and _code(lines[0]) == C_UNREADABLE, (
        f"an unrecordable kill-switch skip must be refused, got {lines!r}"
    )


# --- AC20: no base ref resolvable --------------------------------------------


def test_ac20_no_origin_main_and_no_main_allows_silently(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path, base_branch="trunk")
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, 1, "REJECTED", anchor_for=spec)
    _commit_docs(repo, env)
    _stage_source(repo, env)
    refs = _git_ok(["for-each-ref", "--format=%(refname)"], repo, env).stdout.split()
    assert "refs/heads/main" not in refs and "refs/remotes/origin/main" not in refs

    assert _check(repo, env, monkeypatch) == []

    # Positive control: once `main` exists (at the trunk tip) the lot is enforced.
    _git_ok(["branch", "main", "trunk"], repo, env)
    _assert_single(_check(repo, env, monkeypatch), C_REJECTED)


# --- AC21: origin/main is preferred over main --------------------------------


def test_ac21_origin_main_is_preferred_over_stale_local_main(tmp_path, monkeypatch):
    other = "2026-10-02-othersurface"
    repo, env = _new_repo(tmp_path)
    old_base = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()
    _git_ok(["checkout", "-q", "main"], repo, env)
    _write_spec(repo, other, "# merged by another lot\n")  # no gate doc at all
    _commit_docs(repo, env, "other lot merged on main")
    _git_ok(["update-ref", "refs/remotes/origin/main", old_base], repo, env)
    _git_ok(["checkout", "-q", "-B", "lot-243", "main"], repo, env)
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, 1, "APPROVED", anchor_for=spec)
    _commit_docs(repo, env, "lot docs")
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    # Computed against origin/main (old_base): the other lot's spec counts as added.
    _assert_single(lines, C_MISSING, spec_stem=other)
    assert STEM not in lines[0]
    assert "base=origin/main" in lines[0] and "git fetch" in lines[0], (
        f"a refusal computed against origin/main must carry the fetch hint: {lines[0]!r}"
    )

    # Control: without origin/main it falls back to `main`, where only the lot spec
    # is added and it is approved.
    _git_ok(["update-ref", "-d", "refs/remotes/origin/main"], repo, env)
    assert _check(repo, env, monkeypatch) == []


# --- AC22: committing on the base branch itself ------------------------------


def test_ac22_staging_on_the_base_branch_itself_is_allowed(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path, "onmain")
    _git_ok(["checkout", "-q", "main"], repo, env)
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, 1, "REJECTED", anchor_for=spec)
    _commit_docs(repo, env, "docs on main")
    _stage_source(repo, env)

    assert _check(repo, env, monkeypatch) == []

    # Positive control: identical docs on a lot branch are enforced.
    repo2, env2, _ = _rejected_lot(tmp_path, "onlot")
    _assert_single(_check(repo2, env2, monkeypatch), C_REJECTED)


# --- AC23: key-form gate docs ------------------------------------------------

KEY_STEM = "2026-10-03-bd243-green-entry-guard"
KEY = "2026-10-03-bd243"


def _key_lot(tmp_path, name, gates):
    """gates: list of (file_stem, n, verdict, anchored)."""
    repo, env = _new_repo(tmp_path, name)
    spec = _write_spec(repo, KEY_STEM, "# guard spec\n")
    for file_stem, n, verdict, anchored in gates:
        _write_gate(
            repo, KEY_STEM, n, verdict,
            anchor_for=spec if anchored else None, file_stem=file_stem,
        )
    _commit_docs(repo, env)
    _stage_source(repo, env)
    return repo, env


def test_ac23_key_form_gate_docs_are_honoured(tmp_path, monkeypatch):
    repo, env = _key_lot(tmp_path, "a", [(KEY, 1, "REJECTED", True)])
    _assert_single(
        _check(repo, env, monkeypatch), C_REJECTED,
        spec_stem=KEY_STEM, gate_name=f"{KEY}-gate-r1.md",
    )

    repo, env = _key_lot(tmp_path, "b", [(KEY, 1, "APPROVED", True)])
    assert _check(repo, env, monkeypatch) == []


def test_ac23_union_of_stem_and_key_forms_newest_n_wins(tmp_path, monkeypatch):
    # stem r1 APPROVED, key r2 REJECTED: newest across the union is r2.
    repo, env = _key_lot(
        tmp_path, "a", [(KEY_STEM, 1, "APPROVED", True), (KEY, 2, "REJECTED", True)]
    )
    _assert_single(
        _check(repo, env, monkeypatch), C_REJECTED,
        spec_stem=KEY_STEM, gate_name=f"{KEY}-gate-r2.md",
    )

    # Equal N: the stem form wins (stem r1 APPROVED beats key r1 REJECTED).
    repo, env = _key_lot(
        tmp_path, "b", [(KEY_STEM, 1, "APPROVED", True), (KEY, 1, "REJECTED", True)]
    )
    assert _check(repo, env, monkeypatch) == []


def test_ac23_decoys_other_key_and_short_stem_do_not_bind(tmp_path, monkeypatch):
    # A different lot's key must not bind this spec.
    repo, env = _key_lot(tmp_path, "a", [("2026-10-03-bd244", 1, "APPROVED", True)])
    _assert_single(_check(repo, env, monkeypatch), C_MISSING, spec_stem=KEY_STEM)

    # A three-segment stem uses the stem form only: the gate doc
    # `2026-10-03-gate-r1.md` (a three-segment prefix of the stem) must not bind.
    short = "2026-10-03-short"
    repo, env = _new_repo(tmp_path, "b")
    spec = _write_spec(repo, short, "# short\n")
    _write_gate(repo, short, 1, "APPROVED", anchor_for=spec, file_stem="2026-10-03")
    _commit_docs(repo, env)
    _stage_source(repo, env)
    _assert_single(_check(repo, env, monkeypatch), C_MISSING, spec_stem=short)

    # Only prefixes with at least four segments count, also for a longer stem:
    # the three-segment prefix `2026-10-03` of KEY_STEM must not bind either.
    repo, env = _key_lot(tmp_path, "c", [("2026-10-03", 1, "APPROVED", True)])
    _assert_single(_check(repo, env, monkeypatch), C_MISSING, spec_stem=KEY_STEM)


# --- AC24: non-spec decoys and the Gate-exempt line --------------------------


def test_ac24_decoy_docs_are_not_specs_and_exemption_is_logged(tmp_path, monkeypatch):
    repo, env, _ = _approved_lot(tmp_path)
    decoys = [
        f"{STEM}-inventory.md",
        f"{STEM}-acceptance.md",
        f"{STEM}-close-gate.md",
        f"{STEM}-gate-verdict-r1.md",
        f"{STEM}-post-mortem.md",
    ]
    for name in decoys:
        (repo / "docs" / "decisions" / name).write_text("# decoy\n\nno verdict here\n")
    exempt = "2026-10-03-exemptnote"
    (repo / "docs" / "decisions" / f"{exempt}.md").write_text(
        "# note\n\nGate-exempt: docs only note\n"
    )
    _commit_docs(repo, env, "decoys")

    assert _check(repo, env, monkeypatch) == []

    entries = _bypass_lines(repo, env)
    assert len(entries) == 1, f"exactly one gate_exempt line expected: {entries!r}"
    assert entries[0]["kind"] == "gate_exempt"
    assert exempt in str(entries[0]["spec"])
    assert "docs only note" in entries[0]["reason"]


def test_ac24_control_blank_or_late_exemption_is_still_a_spec(tmp_path, monkeypatch):
    repo, env, _ = _approved_lot(tmp_path)
    blank, late = "2026-10-03-blankexempt", "2026-10-03-lateexempt"
    (repo / "docs" / "decisions" / f"{blank}.md").write_text("# n\n\nGate-exempt:\n")
    (repo / "docs" / "decisions" / f"{late}.md").write_text(
        "# n\n" + "filler\n" * 24 + "Gate-exempt: too late to count\n"
    )
    _commit_docs(repo, env, "controls")

    lines = _check(repo, env, monkeypatch)

    assert len(lines) == 2, f"both docs are lot specs without gate docs: {lines!r}"
    assert all(_code(ln) == C_MISSING for ln in lines)
    assert any(blank in ln for ln in lines) and any(late in ln for ln in lines)
    assert not any(e["kind"] == "gate_exempt" for e in _bypass_lines(repo, env))


# --- AC25: git mv-ed spec counts as added ------------------------------------


def test_ac25_renamed_spec_is_still_a_lot_spec(tmp_path, monkeypatch):
    body = b"# widget draft\n\n" + b"enough shared content for rename detection\n" * 6
    repo, env = _new_repo(
        tmp_path, main_files={"docs/decisions/2026-09-01-draft-widget.md": body}
    )
    _git_ok(
        ["mv", "docs/decisions/2026-09-01-draft-widget.md", _spec_rel(STEM)], repo, env
    )
    _git_ok(["commit", "-q", "-m", "rename spec"], repo, env)
    status = _git_ok(["diff", "--name-status", "main", "HEAD"], repo, env).stdout
    assert status.startswith("R"), f"arrange: git must see a rename, got {status!r}"
    _stage_source(repo, env)

    lines = _check(repo, env, monkeypatch)

    _assert_single(lines, C_MISSING)


# --- AC26: directory exclusions ----------------------------------------------


def test_ac26_directory_rule_excludes_non_test_names_under_docs_tests(
    tmp_path, monkeypatch
):
    repo, env, _ = _rejected_lot(tmp_path)
    # Names that would be SOURCE paths if the directory rule were missing.
    dir_only = [
        "docs/x/helper.py",
        "tests/helpers.py",
        "tests/data.json",
        "web/__tests__/util.ts",
        "docs/diagram.svg",
        "pkg/tests/fixtures/conf.yaml",
        "a/docs/b/tool.sh",
    ]
    for rel in dir_only:
        assert _check(repo, env, monkeypatch, staged=[rel]) == [], (
            f"{rel} is excluded by the directory rule and must not be GREEN entry"
        )
    # Positive controls: names that merely contain 'test'/'docs' are real sources.
    for rel in ("src/contest.py", "src/latest/main.py", "docsy/readme.py"):
        _assert_single(_check(repo, env, monkeypatch, staged=[rel]), C_REJECTED)


# --- AC27: kill switch is evaluated first ------------------------------------


def test_ac27_kill_switch_with_reason_skips_docs_only_commit_and_logs(
    tmp_path, monkeypatch
):
    repo, env, _ = _rejected_lot(tmp_path)

    # Control: switch off, docs-only is not GREEN entry -> [] and no log.
    assert _check(repo, env, monkeypatch, staged=["docs/notes.md"]) == []
    assert _bypass_lines(repo, env) == []

    extra = {KILL: "0", REASON: "shell export"}
    assert _check(repo, env, monkeypatch, staged=["docs/notes.md"], extra=extra) == []
    entries = _bypass_lines(repo, env)
    assert len(entries) == 1, f"one kill_switch line even for docs-only: {entries!r}"
    assert entries[0]["kind"] == "kill_switch" and entries[0]["spec"] is None


def test_ac27_kill_switch_without_reason_refuses_docs_only_commit(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)

    lines = _check(
        repo, env, monkeypatch, staged=["docs/notes.md"], extra={KILL: "0"}
    )

    assert len(lines) == 1 and _code(lines[0]) == C_NO_REASON, lines
    assert _bypass_lines(repo, env) == []


# --- AC28: binary-only source through the real hook --------------------------


def test_ac28_binary_only_source_on_rejected_lot_is_refused_by_hook(tmp_path):
    repo, env = _hooked_lot(tmp_path, "rejbin", "REJECTED", binary=True)
    head_before = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()

    result = _git(["commit", "-m", "binary only"], repo, env)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"must be refused even with nothing to lint: {output!r}"
    assert _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip() == head_before
    assert C_REJECTED in output, output


def test_ac28_control_binary_only_source_on_approved_lot_commits(tmp_path):
    repo, env = _hooked_lot(tmp_path, "okbin", "APPROVED", binary=True)
    head_before = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()

    result = _git(["commit", "-m", "binary only"], repo, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip() != head_before
    assert GUARD_MODULE.is_file()


# --- AC29: ESCALATION marker is single-line ----------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "ESCALATION:\nowner accepted risk\n",
        "ESCALATION:\n\n   \nreason far below\n",
        "ESCALATION:   \n\treason on a tab line\n",
    ],
    ids=["next_line", "blank_gap", "indented_next"],
)
def test_ac29_escalation_text_on_next_line_is_not_a_marker(tmp_path, monkeypatch, content):
    repo, env, _ = _rejected_lot(tmp_path)
    (repo / "docs" / "decisions" / f"{STEM}-escalation.md").write_text(content)
    _commit_docs(repo, env, "escalation")

    _assert_single(_check(repo, env, monkeypatch), C_REJECTED)
    assert _bypass_lines(repo, env) == []


def test_ac29_control_same_line_tab_separated_reason_is_a_marker(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)
    (repo / "docs" / "decisions" / f"{STEM}-escalation.md").write_text(
        "ESCALATION:\tshort reason\n"
    )
    _commit_docs(repo, env, "escalation")

    assert _check(repo, env, monkeypatch) == []
    assert len(_bypass_lines(repo, env)) == 1


# --- AC30: sibling suites stay green -----------------------------------------


def test_ac30_bd66_and_bd94_sibling_suites_stay_green():
    # Depends on the guard existing: only then does the sibling run exercise the
    # wired layer (bd66 package-copy fixture, bd94 tree-scan inventory).
    assert GUARD_MODULE.is_file(), f"{GUARD_MODULE} must exist (spec 2.7)"
    tests_dir = REPO_ROOT / "engine_py" / "tests"
    targets = [
        str(tests_dir / "test_bd66_precommit_enforcement.py"),
        str(tests_dir / "test_bd94_engine_owned_paths.py")
        + "::test_ac6_real_tree_passes_tree_scan_lint",
    ]
    env = dict(os.environ)
    env.pop("BD66_LINT_DIR", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=1200,
    )
    assert result.returncode == 0, (
        f"sibling suites must pass, rc={result.returncode}\n"
        f"{result.stdout[-3000:]}\n{result.stderr[-1000:]}"
    )


# =============================================================================
# r3 additions: AC17 extension, AC19 gate_exempt, AC31-AC37
# =============================================================================


@pytest.mark.parametrize("md_path", ERROR_CODES_MD, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_ac17_error_codes_md_byte_identical_to_render_markdown(md_path):
    rendered = error_codes.render_markdown()
    for code in ALL_CODES:
        assert f"- `{code}` " in rendered, f"{code} missing from render_markdown()"
    assert md_path.read_bytes() == rendered.encode("utf-8"), (
        f"{md_path} must be byte-identical to render_markdown() "
        f"(regenerate with python -m bytedigger_engine.error_codes --markdown)"
    )


def test_ac17_each_code_is_a_quoted_literal_in_the_guard_module():
    assert GUARD_MODULE.is_file(), f"{GUARD_MODULE} must exist first (dead-code checks)"
    text = GUARD_MODULE.read_text()
    for code in ALL_CODES:
        assert re.search(rf"[\"']{code}[\"']", text), (
            f"{code} must appear as a quoted string literal in {GUARD_MODULE.name}"
        )


# --- AC19 (extension): gate_exempt log-write failure refuses -----------------


def test_ac19_gate_exempt_log_write_failure_refuses(tmp_path, monkeypatch):
    repo, env, _ = _approved_lot(tmp_path)
    exempt = "2026-10-03-exemptnote"
    (repo / "docs" / "decisions" / f"{exempt}.md").write_text(
        "# note\n\nGate-exempt: docs only note\n"
    )
    _commit_docs(repo, env, "exempt doc")
    blocker = _common_dir(repo, env) / "bytedigger"
    blocker.write_text("not a directory\n")

    lines = _check(repo, env, monkeypatch)

    assert len(lines) == 1 and _code(lines[0]) == C_UNREADABLE, (
        f"an unrecordable gate_exempt must be refused, got {lines!r}"
    )

    blocker.unlink()
    assert _check(repo, env, monkeypatch) == []
    assert [e["kind"] for e in _bypass_lines(repo, env)] == ["gate_exempt"]


# --- AC31: corrupt index fails closed ----------------------------------------


def test_ac31_corrupt_git_index_is_unreadable(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)
    _assert_single(_check(repo, env, monkeypatch), C_REJECTED)  # control: guard live

    git_dir = Path(_git_ok(["rev-parse", "--absolute-git-dir"], repo, env).stdout.strip())
    (git_dir / "index").write_bytes(b"this is not a git index\x00\xff" * 8)

    lines = _check(repo, env, monkeypatch)

    assert len(lines) == 1 and _code(lines[0]) == C_UNREADABLE, (
        f"a git failure while listing must refuse, never allow: {lines!r}"
    )


# --- AC32: merge-base failure with a resolvable base fails closed ------------


def test_ac32_orphan_lot_branch_with_main_present_is_unreadable(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    _git_ok(["checkout", "-q", "--orphan", "orphan-lot"], repo, env)
    _git_ok(["rm", "-rf", "-q", "."], repo, env)
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, 1, "APPROVED", anchor_for=spec)
    _commit_docs(repo, env, "root commit of unrelated history")
    _stage_source(repo, env)
    mb = _git(["merge-base", "HEAD", "main"], repo, env)
    assert mb.returncode != 0, "arrange: histories must be unrelated"

    lines = _check(repo, env, monkeypatch)

    assert len(lines) == 1 and _code(lines[0]) == C_UNREADABLE, (
        f"merge-base failure after a base resolved must be fail closed: {lines!r}"
    )

    # Control: with no base at all (probe failure, not merge-base failure) it allows.
    _git_ok(["branch", "-D", "main"], repo, env)
    assert _check(repo, env, monkeypatch) == []


# --- AC33: sub-lot prefix naming ---------------------------------------------

SUB_STEM = "2026-10-03-bd218-s1-preflight-rung"


def _stem_lot(tmp_path, name, stem, gates):
    """gates: list of (file_stem, n, verdict, anchored)."""
    repo, env = _new_repo(tmp_path, name)
    spec = _write_spec(repo, stem, "# sub-lot spec\n")
    for file_stem, n, verdict, anchored in gates:
        _write_gate(
            repo, stem, n, verdict,
            anchor_for=spec if anchored else None, file_stem=file_stem,
        )
    _commit_docs(repo, env)
    _stage_source(repo, env)
    return repo, env


def test_ac33_sub_lot_prefix_gate_binds(tmp_path, monkeypatch):
    s1 = "2026-10-03-bd218-s1"
    repo, env = _stem_lot(tmp_path, "a", SUB_STEM, [(s1, 1, "APPROVED", True)])
    assert _check(repo, env, monkeypatch) == []

    repo, env = _stem_lot(tmp_path, "b", SUB_STEM, [(s1, 1, "REJECTED", True)])
    _assert_single(
        _check(repo, env, monkeypatch), C_REJECTED,
        spec_stem=SUB_STEM, gate_name=f"{s1}-gate-r1.md",
    )

    # The shorter lot-level prefix is in the union too: bd218 r2 REJECTED beats s1 r1.
    repo, env = _stem_lot(
        tmp_path, "c",
        SUB_STEM,
        [(s1, 1, "APPROVED", True), ("2026-10-03-bd218", 2, "REJECTED", True)],
    )
    _assert_single(
        _check(repo, env, monkeypatch), C_REJECTED,
        spec_stem=SUB_STEM, gate_name="2026-10-03-bd218-gate-r2.md",
    )


def test_ac33_sibling_sub_lot_s2_gate_never_binds(tmp_path, monkeypatch):
    s1, s2 = "2026-10-03-bd218-s1", "2026-10-03-bd218-s2"
    # Only the sibling's gate exists: MISSING.
    repo, env = _stem_lot(tmp_path, "a", SUB_STEM, [(s2, 1, "APPROVED", True)])
    _assert_single(_check(repo, env, monkeypatch), C_MISSING, spec_stem=SUB_STEM)

    # The sibling's higher-N APPROVED must not mask this lot's REJECTED.
    repo, env = _stem_lot(
        tmp_path, "b", SUB_STEM, [(s1, 1, "REJECTED", True), (s2, 5, "APPROVED", True)]
    )
    _assert_single(
        _check(repo, env, monkeypatch), C_REJECTED,
        spec_stem=SUB_STEM, gate_name=f"{s1}-gate-r1.md",
    )


# --- AC34: non-integer revision is not a gate doc ----------------------------


def test_ac34_fractional_revision_is_ignored(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path, "a")
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, 2, "REJECTED", anchor_for=spec)
    _write_gate(repo, STEM, "3.1", "APPROVED", anchor_for=spec)
    assert (repo / "docs" / "decisions" / f"{STEM}-gate-r3.1.md").is_file()
    _commit_docs(repo, env)
    _stage_source(repo, env)
    _assert_single(
        _check(repo, env, monkeypatch), C_REJECTED, gate_name=f"{STEM}-gate-r2.md"
    )

    # Alone, r3.1 is not a gate doc at all.
    repo, env = _new_repo(tmp_path, "b")
    spec = _write_spec(repo, STEM, "# widget spec\n")
    _write_gate(repo, STEM, "3.1", "APPROVED", anchor_for=spec)
    _commit_docs(repo, env)
    _stage_source(repo, env)
    _assert_single(_check(repo, env, monkeypatch), C_MISSING)


# --- AC35: only the exact string "0" is the kill switch ----------------------


@pytest.mark.parametrize("value", ["false", "00", " 0", "0 ", "off"])
def test_ac35_other_kill_switch_values_keep_the_guard_on(tmp_path, monkeypatch, value):
    repo, env, _ = _rejected_lot(tmp_path)

    lines = _check(repo, env, monkeypatch, extra={KILL: value, REASON: "has a reason"})

    _assert_single(lines, C_REJECTED)
    assert _bypass_lines(repo, env) == [], "no kill_switch line for a non-switch value"


def test_ac35_control_exact_zero_is_the_switch(tmp_path, monkeypatch):
    repo, env, _ = _rejected_lot(tmp_path)

    assert _check(repo, env, monkeypatch, extra={KILL: "0", REASON: "has a reason"}) == []
    assert [e["kind"] for e in _bypass_lines(repo, env)] == ["kill_switch"]


# --- AC36: first anchor block only -------------------------------------------


def _two_anchor_gate(repo, first: bytes, second: bytes, verdict="APPROVED"):
    body = (
        f"# gate r1 for {STEM}\n\nProse.\n\n"
        + _anchor(STEM, first)
        + "\nMore prose, then a second block.\n\n"
        + _anchor(STEM, second)
        + f"\nVERDICT: {verdict}\n"
    )
    (repo / "docs" / "decisions" / f"{STEM}-gate-r1.md").write_text(body)


def test_ac36_only_the_first_anchor_block_is_read(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path, "a")
    stale = b"# widget spec OLD\n"
    current = _write_spec(repo, STEM, "# widget spec\n")
    _two_anchor_gate(repo, first=stale, second=current)
    _commit_docs(repo, env)
    _stage_source(repo, env)
    _assert_single(_check(repo, env, monkeypatch), C_STALE)

    # Control: first block matching, second stale -> allowed (not "any"/"last").
    repo, env = _new_repo(tmp_path, "b")
    current = _write_spec(repo, STEM, "# widget spec\n")
    _two_anchor_gate(repo, first=current, second=stale)
    _commit_docs(repo, env)
    _stage_source(repo, env)
    assert _check(repo, env, monkeypatch) == []


# --- AC37: first commit staging spec and source together ---------------------


def test_ac37_spec_and_source_staged_together_is_missing(tmp_path, monkeypatch):
    repo, env = _new_repo(tmp_path)
    _write_spec(repo, STEM, "# widget spec\n")
    _git_ok(["add", _spec_rel(STEM)], repo, env)
    _stage_source(repo, env)
    head = _git_ok(["rev-parse", "HEAD"], repo, env).stdout.strip()
    main = _git_ok(["rev-parse", "main"], repo, env).stdout.strip()
    assert head == main, "arrange: HEAD is the base tip (first commit on the lot branch)"

    lines = _check(repo, env, monkeypatch)

    _assert_single(lines, C_MISSING)
