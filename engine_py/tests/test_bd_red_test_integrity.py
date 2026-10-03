"""RED tests for bd#226 - RED-side test integrity gate.

Spec: docs/decisions/2026-10-03-bd-red-test-integrity.md (AC1..AC10).

Every behavioural test drives the REAL `_commit_red_tests` on a tmp git repo
(the unit under test is never mocked; only the telemetry sink `_emit_safe` is
wrapped to observe events). The pure module
`bytedigger_engine.lib.test_integrity` does not exist yet: it is imported
INSIDE the unit tests (never at module top) so this file collects cleanly and
fails at assertion/import time for the right reason.

No sys.path manipulation (81F97F3D / section 1q). RED-only file.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests
from bytedigger_engine.contracts import WorkflowContext

ENV_TOKENS = (
    "HAL_RED_TEST_INTEGRITY_GATE",
    "HAL_RED_TEST_INTEGRITY_ENFORCE",
    "HAL_RED_TEST_INTEGRITY_MAX_DELETED_FILES",
    "HAL_RED_TEST_INTEGRITY_MAX_REMOVED_TESTS",
    "HAL_RED_TEST_INTEGRITY_MAX_ADDED_SKIPS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for tok in ENV_TOKENS:
        monkeypatch.delenv(tok, raising=False)
        suffix = tok[len("HAL_"):]
        for prefix in ("BD_", "BYTEDIGGER_"):
            monkeypatch.delenv(prefix + suffix, raising=False)


# --- helpers (copied from test_bd_flip_red_mass_deletion.py) -----------------


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _lines(n: int, prefix: str = "L") -> str:
    return "".join(f"{prefix}{i}\n" for i in range(n))


def _funcs(names, extra: str = "") -> str:
    return "".join(f"def {n}():\n    assert True\n\n\n" for n in names) + extra


def _make_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Add foo to bar", session_id="test-session", persona="hal",
        framework=None, domain=None,
    )


def _make_prev(spec_path: str):
    prev = MagicMock()
    prev.data = {"red_test_paths": "<missing>", "cycle": 1, "spec_path": spec_path}
    return prev


def _write_spec(tmp_path: Path, authorized: "list[str]") -> Path:
    spec = tmp_path / "spec.md"
    body = "# Spec\n\n## Files\n- src/placeholder.py\n\n"
    if authorized:
        body += "authorized-test-edits:\n" + "".join(f"- {p}\n" for p in authorized)
    spec.write_text(body + "\n## End\n")
    return spec


def _add_new_red(repo: Path) -> None:
    """RED always adds a brand-new test file (keeps red_test_paths non-empty)."""
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_new_red.py").write_text("def test_brand_new_red():\n    assert False\n")


def _run(tmp_path: Path, monkeypatch, repo: Path, spec: Path):
    from bytedigger_engine.workflows import phase_5_implement

    captured: "list[tuple[str, dict]]" = []

    def fake_emit(event_name, payload, severity=None):
        captured.append((event_name, dict(payload)))

    monkeypatch.setattr(phase_5_implement, "_emit_safe", fake_emit)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(exist_ok=True)
    result = _commit_red_tests(_make_ctx(scratchpad, str(repo)), _make_prev(str(spec)))
    return result, captured


def _events(captured, name):
    return [p for (n, p) in captured if n == name]


def _deletion_fixture(tmp_path: Path, authorized: bool = False, name: str = "repo"):
    """Base has a 40-line test file; RED deletes it from the tree."""
    repo = _make_repo(tmp_path, name)
    _commit_file(repo, "tests/test_old.py", _lines(40), "base")
    (repo / "tests" / "test_old.py").unlink()
    _add_new_red(repo)
    spec = _write_spec(tmp_path, ["tests/test_old.py"] if authorized else [])
    return repo, spec


# ===========================================================================
# AC1 - catalog + error code
# ===========================================================================


def test_ac1_catalog_entries_and_error_code_registered() -> None:
    from bytedigger_engine import flags_catalog, error_codes

    for gate in ("HAL_RED_TEST_INTEGRITY_GATE", "HAL_RED_TEST_INTEGRITY_ENFORCE"):
        entry = flags_catalog.FLAGS[gate]
        assert entry["kind"] == "gate"
        assert entry["default"] == "1"
    for thr in (
        "HAL_RED_TEST_INTEGRITY_MAX_DELETED_FILES",
        "HAL_RED_TEST_INTEGRITY_MAX_REMOVED_TESTS",
        "HAL_RED_TEST_INTEGRITY_MAX_ADDED_SKIPS",
    ):
        entry = flags_catalog.FLAGS[thr]
        assert entry["kind"] == "int"
        assert entry["default"] == 0
    for tok in ENV_TOKENS:
        desc = flags_catalog.FLAGS[tok]["description"]
        for horizon in ("flip-by", "kill-by", "retire-by"):
            assert horizon not in desc, f"{tok}: horizon token {horizon!r} in {desc!r}"
    assert "E_RED_TEST_INTEGRITY" in error_codes.ERROR_CODES


# ===========================================================================
# AC2 - production side effect: unauthorized small test-file deletion blocks
# ===========================================================================


def test_ac2_unauthorized_deletion_of_small_test_file_blocks(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _deletion_fixture(tmp_path)

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.status == "error"
    assert result.error_code == "E_RED_TEST_INTEGRITY"
    assert result.recoverable is False
    assert len(_events(captured, "red_test_integrity_blocked")) == 1
    assert "tests/test_old.py" in (result.error or "")


# ===========================================================================
# AC3 - authorized deletion is exempt and listed
# ===========================================================================


def test_ac3_authorized_deletion_not_blocked_and_listed_exempted(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _deletion_fixture(tmp_path, authorized=True)

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1
    assert "tests/test_old.py" in json.dumps(checks[0]["exempted"])
    assert checks[0]["violations_n"] == 0


# ===========================================================================
# AC4 - pragma exempts removed tests; authorization alone does not
# ===========================================================================


def _removal_fixture(tmp_path: Path, pragma: bool, name: str):
    repo = _make_repo(tmp_path, name)
    names = ["test_a", "test_b", "test_c", "test_d", "test_e"]
    _commit_file(repo, "tests/test_old.py", _funcs(names), "base")
    extra = "# red-mass-deletion: allow\n" if pragma else ""
    (repo / "tests" / "test_old.py").write_text(_funcs(["test_a", "test_b", "test_c"], extra))
    _add_new_red(repo)
    spec = _write_spec(tmp_path, ["tests/test_old.py"])
    return repo, spec


def test_ac4_removed_tests_pragma_exempts(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _removal_fixture(tmp_path, pragma=True, name="repo_p")

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1 and checks[0]["violations_n"] == 0


def test_ac4_removed_tests_authorized_without_pragma_blocks_and_names_them(
    tmp_path: Path, monkeypatch
) -> None:
    repo, spec = _removal_fixture(tmp_path, pragma=False, name="repo_np")

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.status == "error"
    assert result.error_code == "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1
    blob = json.dumps(checks[0]["violations"])
    assert "test_d" in blob and "test_e" in blob
    assert "test_a" not in blob.replace("tests/test_old.py", "")


# ===========================================================================
# AC5 - added skip marker blocks; threshold override passes it
# ===========================================================================


def _skip_fixture(tmp_path: Path, name: str):
    repo = _make_repo(tmp_path, name)
    _commit_file(repo, "tests/test_old.py", _funcs(["test_a", "test_b"]), "base")
    (repo / "tests" / "test_old.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip\n" + _funcs(["test_a"]) + _funcs(["test_b"])
    )
    _add_new_red(repo)
    spec = _write_spec(tmp_path, ["tests/test_old.py"])
    return repo, spec


def test_ac5_added_skip_blocks(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _skip_fixture(tmp_path, "repo_s1")

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.status == "error"
    assert result.error_code == "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1
    assert "tests/test_old.py" in json.dumps(checks[0]["violations"])


def test_ac5_max_added_skips_one_lets_it_pass(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _skip_fixture(tmp_path, "repo_s2")
    monkeypatch.setenv("HAL_RED_TEST_INTEGRITY_MAX_ADDED_SKIPS", "1")

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1 and checks[0]["enforced"] is True


# ===========================================================================
# AC6 - no false positives
# ===========================================================================


def test_ac6_new_file_only_and_pragma_rename_no_violation(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    _commit_file(repo, "tests/test_old.py", _funcs(["test_a", "test_b"]), "base")
    (repo / "tests" / "test_old.py").write_text(
        _funcs(["test_a", "test_b_renamed"], "# red-mass-deletion: allow\n")
    )
    _add_new_red(repo)
    spec = _write_spec(tmp_path, ["tests/test_old.py"])

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1
    assert checks[0]["violations_n"] == 0
    assert checks[0]["enforced"] is True


def test_ac6_test_moved_to_new_file_is_not_a_removal(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    _commit_file(repo, "tests/test_a.py", _funcs(["test_one", "test_two", "test_three"]), "base")
    (repo / "tests" / "test_a.py").write_text(_funcs(["test_one"]))
    (repo / "tests" / "test_b.py").write_text(_funcs(["test_two", "test_three"]))
    spec = _write_spec(tmp_path, ["tests/test_a.py"])

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1
    assert checks[0]["violations_n"] == 0
    assert checks[0]["enforced"] is True


# ===========================================================================
# AC7 - ENFORCE=0 warn-only; GATE=0 silent
# ===========================================================================


def test_ac7_enforce_zero_is_warn_only(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _deletion_fixture(tmp_path)
    monkeypatch.setenv("HAL_RED_TEST_INTEGRITY_ENFORCE", "0")

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1
    assert checks[0]["enforced"] is False
    assert checks[0]["violations_n"] >= 1


def test_ac7_gate_zero_emits_no_check_event(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _deletion_fixture(tmp_path)
    monkeypatch.setenv("HAL_RED_TEST_INTEGRITY_GATE", "0")

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    assert _events(captured, "red_test_integrity_check") == []
    # Discriminator vs today: GATE=0 must also not silently hide a GH1600 D1
    # refusal path that the new gate would have pre-empted -- the new-gate
    # events are simply absent, nothing else is asserted about the result.
    assert _events(captured, "red_test_integrity_blocked") == []


# ===========================================================================
# AC8 - aggregate threshold
# ===========================================================================


def _multi_deletion(tmp_path: Path, n: int, name: str):
    repo = _make_repo(tmp_path, name)
    for i in range(n):
        _commit_file(repo, f"tests/test_old{i}.py", _lines(30, f"F{i}_"), f"base{i}")
    for i in range(n):
        (repo / "tests" / f"test_old{i}.py").unlink()
    _add_new_red(repo)
    return repo, _write_spec(tmp_path, [])


def test_ac8_two_deleted_files_pass_with_max_two(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _multi_deletion(tmp_path, 2, "repo_two")
    monkeypatch.setenv("HAL_RED_TEST_INTEGRITY_MAX_DELETED_FILES", "2")

    result, captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code != "E_RED_TEST_INTEGRITY"
    checks = _events(captured, "red_test_integrity_check")
    assert len(checks) == 1 and checks[0]["violations_n"] == 0


def test_ac8_three_deleted_files_block_with_max_two(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _multi_deletion(tmp_path, 3, "repo_three")
    monkeypatch.setenv("HAL_RED_TEST_INTEGRITY_MAX_DELETED_FILES", "2")

    result, _captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.status == "error"
    assert result.error_code == "E_RED_TEST_INTEGRITY"


# ===========================================================================
# AC9 - pure module unit tests (real git repo, no mocking of the UUT)
# ===========================================================================


def _no(_p: str) -> bool:
    return False


def test_ac9_unit_documented_keys_for_deleted_removed_and_skipped(tmp_path: Path) -> None:
    from bytedigger_engine.lib import test_integrity

    repo = _make_repo(tmp_path)
    _commit_file(repo, "tests/test_gone.py", _funcs(["test_g1"]), "gone")
    _commit_file(repo, "tests/test_shrunk.py", _funcs(["test_s1", "test_s2"]), "shrunk")
    _commit_file(repo, "tests/test_skipped.py", _funcs(["test_k1"]), "skipped")
    base = _head(repo)
    (repo / "tests" / "test_gone.py").unlink()
    (repo / "tests" / "test_shrunk.py").write_text(_funcs(["test_s1"]))
    (repo / "tests" / "test_skipped.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip\n" + _funcs(["test_k1"])
    )

    out = test_integrity.compute_test_integrity(
        base, str(repo), is_authorized=_no, has_pragma=_no
    )

    for key in ("deleted_files", "removed_tests", "added_skips", "skip_reason"):
        assert key in out
    assert out["deleted_files"] == ["tests/test_gone.py"]
    assert out["removed_tests"] == [{"path": "tests/test_shrunk.py", "names": ["test_s2"]}]
    assert out["added_skips"] == [{"path": "tests/test_skipped.py", "n": 1}]
    assert out["skip_reason"] is None


def test_ac9_unit_exemptions_via_callbacks(tmp_path: Path) -> None:
    from bytedigger_engine.lib import test_integrity

    repo = _make_repo(tmp_path)
    _commit_file(repo, "tests/test_gone.py", _funcs(["test_g1"]), "gone")
    _commit_file(repo, "tests/test_shrunk.py", _funcs(["test_s1", "test_s2"]), "shrunk")
    base = _head(repo)
    (repo / "tests" / "test_gone.py").unlink()
    (repo / "tests" / "test_shrunk.py").write_text(_funcs(["test_s1"]))

    out = test_integrity.compute_test_integrity(
        base, str(repo),
        is_authorized=lambda p: p == "tests/test_gone.py",
        has_pragma=lambda p: p == "tests/test_shrunk.py",
    )

    assert out["deleted_files"] == []
    assert out["removed_tests"] == []


def test_ac9_unit_empty_base_sets_no_base_sha(tmp_path: Path) -> None:
    from bytedigger_engine.lib import test_integrity

    repo = _make_repo(tmp_path)

    out = test_integrity.compute_test_integrity(
        "", str(repo), is_authorized=_no, has_pragma=_no
    )

    assert out["skip_reason"] == "no_base_sha"
    assert out["deleted_files"] == [] and out["removed_tests"] == [] and out["added_skips"] == []


def test_ac9_unit_bad_base_sha_fails_open(tmp_path: Path) -> None:
    from bytedigger_engine.lib import test_integrity

    repo = _make_repo(tmp_path)
    _commit_file(repo, "tests/test_x.py", _funcs(["test_x"]), "x")
    (repo / "tests" / "test_x.py").unlink()

    out = test_integrity.compute_test_integrity(
        "0123456789abcdef0123456789abcdef01234567", str(repo),
        is_authorized=_no, has_pragma=_no,
    )

    assert out["deleted_files"] == [] and out["removed_tests"] == [] and out["added_skips"] == []
    assert out["skip_reason"]


# ===========================================================================
# AC10 - ordering vs GH282 and GH1600 D1
# ===========================================================================


def test_ac10_mass_deletion_keeps_precedence_over_integrity(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    _commit_file(repo, "tests/test_big.py", _lines(300), "base")
    (repo / "tests" / "test_big.py").write_text(_lines(100))
    _add_new_red(repo)
    spec = _write_spec(tmp_path, [])

    result, _captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.status == "error"
    assert result.error_code == "E_RED_MASS_DELETION"


def test_ac10_small_deletion_returns_integrity_not_d1(tmp_path: Path, monkeypatch) -> None:
    repo, spec = _deletion_fixture(tmp_path)

    result, _captured = _run(tmp_path, monkeypatch, repo, spec)

    assert result.error_code == "E_RED_TEST_INTEGRITY"
    assert result.error_code != "E_RED_TESTS_IN_EXISTING_FILE"
