"""bd#102 RED tests — host-tool contract (helpers.host_tools.HOST_TOOLS + skip conversion).

Rev 4. Covers AC1-AC13, AC1b, AC4b, AC4c, AC4d, AC5b from
SHARED/memory/Decisions/2026-07-26_293DC703_bd102_host_tool_coupling_spec.md
(gate pass 1 REJECTED MAJOR-1..7 -> rev 2; gate-2 found a harness bug in rev 2's
AC9-11 -> rev 3; gate-2 pass 2 REJECTED MAJOR-1..4 -> rev 4: AC9-11 now restore
`_HOST_TOOL_AVAILABLE` via monkeypatch instead of leaking the mutation into the
parent session; AC5 driven by the frozen map, never live shutil.which; AC12
proves the map is actually populated at session start; AC13 guards the
FFEA1913 burn-guard against pytest_configure shadowing; AC4d guards the
no-filename edge; AC5b now parses each named test function's own body, like
AC7, instead of grepping the whole file). All of M1 —
`HOST_TOOLS`, `_HOST_TOOL_AVAILABLE`, `host_tool_skip_reason`, and the
`pytest_runtest_makereport` hookwrapper body — lives in
`tests/helpers/host_tools.py`; `tests/conftest.py` only imports and re-exports
the hook. AC1-AC4c now target `helpers.host_tools` directly, plus one cheap
re-export check that `conftest.pytest_runtest_makereport is
helpers.host_tools.pytest_runtest_makereport`.

D1CF5FDF collectability: none of the above, nor `helpers.host_tools.skip_without`
nor `helpers.git_repo.init_repo`, exist yet. They are fetched via
`getattr(...)` or a deferred `import` INSIDE each test body (never
imported/referenced at module top) so this file always COLLECTS cleanly and
fails at assert time, never at collection time.

AC9-AC11 use the `pytester` fixture for an end-to-end, real-hook exercise.
`pytest_plugins = "pytester"` below requires the bundled plugin for this
module only (supported in test modules, not only root conftest.py). The
pytester sub-run's generated conftest.py imports `helpers.host_tools` (NOT
`conftest` — pytest would import the sub-run's own generated conftest.py
under that exact name, self-importing partially-initialised, per gate rev-2
finding) and assigns its `pytest_runtest_makereport` object directly — it
reuses the real hookwrapper object, it does not reimplement it — then forces
the target tool "absent" by writing `host_tools._HOST_TOOL_AVAILABLE[tool] =
False` on that same shared module object (never by monkeypatching
`shutil.which` in the parent process, which gate MAJOR-5 identified as
spoofable via test_phase_5_p1a_suite_safety_585E30E3.py's process-wide
`shutil.which` patch).

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest_plugins = "pytester"


# ─── AC1: conftest.HOST_TOOLS declared with the exact 3-key set, zsh excluded ─


def test_ac1_host_tools_key_set_is_git_bun_semgrep_and_excludes_zsh() -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    host_tools_map = getattr(host_tools, "HOST_TOOLS", None)
    assert host_tools_map is not None, (
        "helpers.host_tools.HOST_TOOLS not declared yet (expected RED)"
    )
    assert isinstance(host_tools_map, dict), (
        f"helpers.host_tools.HOST_TOOLS must be a dict, got {type(host_tools_map)!r}"
    )
    assert set(host_tools_map.keys()) == {"git", "bun", "semgrep"}, (
        f"HOST_TOOLS key set = {set(host_tools_map.keys())!r}, expected exactly "
        "{'git', 'bun', 'semgrep'}"
    )
    assert "zsh" not in host_tools_map, (
        "'zsh' must NOT be a HOST_TOOLS key — declaring it would silence "
        "test_360F99CE_phase6_smoke_graceful.py's zsh_unavailable regression "
        f"guard (gate MAJOR-4); got keys={set(host_tools_map.keys())!r}"
    )
    for tool, reason in host_tools_map.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"HOST_TOOLS[{tool!r}] reason must be a non-empty str, got {reason!r}"
        )


def test_ac1b_conftest_reexports_the_real_helpers_host_tools_hookwrapper() -> None:
    import conftest  # noqa: PLC0415
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    conftest_hook = getattr(conftest, "pytest_runtest_makereport", None)
    real_hook = getattr(host_tools, "pytest_runtest_makereport", None)
    assert real_hook is not None, (
        "helpers.host_tools.pytest_runtest_makereport not implemented yet (expected RED)"
    )
    assert conftest_hook is real_hook, (
        "tests/conftest.py must re-export the SAME hookwrapper object as "
        f"helpers.host_tools.pytest_runtest_makereport — got conftest hook "
        f"{conftest_hook!r} vs real hook {real_hook!r}"
    )


# ─── AC2-AC4c: conftest.host_tool_skip_reason — driven by the FROZEN map, ──
# ─── never a live shutil.which() call (gate MAJOR-5)                      ─


def test_ac2_frozen_availability_false_yields_frozen_literal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    fn = getattr(host_tools, "host_tool_skip_reason", None)
    assert fn is not None, (
        "helpers.host_tools.host_tool_skip_reason not implemented yet (expected RED)"
    )
    available = getattr(host_tools, "_HOST_TOOL_AVAILABLE", None)
    assert available is not None, (
        "helpers.host_tools._HOST_TOOL_AVAILABLE not implemented yet (expected RED) — "
        "availability must be frozen at pytest_configure time, not read live"
    )

    monkeypatch.setitem(available, "git", False)
    exc = FileNotFoundError(2, "No such file or directory")
    exc.filename = "git"

    reason = fn(exc)
    assert reason == "requires host tool 'git' — see docs/host-requirements.md", (
        f"expected the frozen literal reason string, got {reason!r}"
    )


def test_ac3_frozen_availability_true_leaves_outcome_failed_no_blanket_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    fn = getattr(host_tools, "host_tool_skip_reason", None)
    assert fn is not None, (
        "helpers.host_tools.host_tool_skip_reason not implemented yet (expected RED)"
    )
    available = getattr(host_tools, "_HOST_TOOL_AVAILABLE", None)
    assert available is not None, (
        "helpers.host_tools._HOST_TOOL_AVAILABLE not implemented yet (expected RED)"
    )

    monkeypatch.setitem(available, "git", True)
    exc = FileNotFoundError(2, "No such file or directory")
    exc.filename = "git"

    reason = fn(exc)
    assert reason is None, (
        f"frozen availability says git IS present — must NOT convert to a blanket skip, "
        f"got {reason!r}"
    )


def test_ac4_undeclared_binary_leaves_outcome_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    fn = getattr(host_tools, "host_tool_skip_reason", None)
    assert fn is not None, (
        "helpers.host_tools.host_tool_skip_reason not implemented yet (expected RED)"
    )
    available = getattr(host_tools, "_HOST_TOOL_AVAILABLE", None)
    assert available is not None, (
        "helpers.host_tools._HOST_TOOL_AVAILABLE not implemented yet (expected RED)"
    )

    exc = FileNotFoundError(2, "No such file or directory")
    exc.filename = "frobnicate"

    reason = fn(exc)
    assert reason is None, (
        f"'frobnicate' is not a declared HOST_TOOLS key — must not be swallowed, got {reason!r}"
    )


def test_ac4b_non_filenotfounderror_is_never_converted() -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    fn = getattr(host_tools, "host_tool_skip_reason", None)
    assert fn is not None, (
        "helpers.host_tools.host_tool_skip_reason not implemented yet (expected RED)"
    )

    exc = AssertionError("a real assertion failure")
    reason = fn(exc)
    assert reason is None, (
        f"a non-FileNotFoundError must never be converted to a skip, got {reason!r}"
    )


def test_ac4c_fires_on_absolute_path_basename_and_via_cause_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    fn = getattr(host_tools, "host_tool_skip_reason", None)
    assert fn is not None, (
        "helpers.host_tools.host_tool_skip_reason not implemented yet (expected RED)"
    )
    available = getattr(host_tools, "_HOST_TOOL_AVAILABLE", None)
    assert available is not None, (
        "helpers.host_tools._HOST_TOOL_AVAILABLE not implemented yet (expected RED)"
    )
    monkeypatch.setitem(available, "git", False)

    # absolute-path filename must basename-match the declared key.
    abs_exc = FileNotFoundError(2, "No such file or directory")
    abs_exc.filename = "/usr/bin/git"
    abs_reason = fn(abs_exc)
    assert abs_reason == "requires host tool 'git' — see docs/host-requirements.md", (
        f"absolute-path filename '/usr/bin/git' must basename-match 'git', got {abs_reason!r}"
    )

    # FileNotFoundError reached only via __cause__ (raise ... from fnf).
    fnf = FileNotFoundError(2, "No such file or directory")
    fnf.filename = "git"
    try:
        try:
            raise fnf
        except FileNotFoundError as _fnf:
            raise RuntimeError("wrapped") from _fnf
    except RuntimeError as chained:
        chained_reason = fn(chained)

    assert chained_reason == "requires host tool 'git' — see docs/host-requirements.md", (
        f"must walk __cause__ to find the FileNotFoundError, got {chained_reason!r}"
    )


# ─── AC5: tests/helpers/host_tools.py::skip_without ────────────────────────


def test_ac5_skip_without_raises_skipped_when_absent_and_noop_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Driven by the FROZEN _HOST_TOOL_AVAILABLE map — never a live shutil.which
    # patch (gate-2 MAJOR-2): skip_without must read the same frozen map M1
    # freezes, not re-probe shutil.which itself.
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    skip_without = getattr(host_tools, "skip_without", None)
    assert skip_without is not None, (
        "helpers.host_tools.skip_without not implemented yet (expected RED)"
    )
    available = getattr(host_tools, "_HOST_TOOL_AVAILABLE", None)
    assert available is not None, (
        "helpers.host_tools._HOST_TOOL_AVAILABLE not implemented yet (expected RED)"
    )

    monkeypatch.setitem(available, "semgrep", False)
    with pytest.raises(pytest.skip.Exception):
        skip_without("semgrep")

    monkeypatch.setitem(available, "semgrep", True)
    result = skip_without("semgrep")
    assert result is None, f"skip_without must be a no-op when the tool is present, got {result!r}"


def test_ac4d_no_filename_returns_none_without_raising() -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    fn = getattr(host_tools, "host_tool_skip_reason", None)
    assert fn is not None, (
        "helpers.host_tools.host_tool_skip_reason not implemented yet (expected RED)"
    )

    exc = FileNotFoundError()  # no filename set -> exc.filename is None
    reason = fn(exc)
    assert reason is None, (
        f"a FileNotFoundError with no filename must return None without raising "
        f"(os.path.basename(None) would TypeError inside a report hook), got {reason!r}"
    )


def test_ac12_availability_map_populated_for_the_real_session() -> None:
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    available = getattr(host_tools, "_HOST_TOOL_AVAILABLE", None)
    assert available is not None, (
        "helpers.host_tools._HOST_TOOL_AVAILABLE not implemented yet (expected RED)"
    )
    host_tools_map = getattr(host_tools, "HOST_TOOLS", None)
    assert host_tools_map is not None, (
        "helpers.host_tools.HOST_TOOLS not implemented yet (expected RED)"
    )
    assert set(available) == set(host_tools_map), (
        f"_HOST_TOOL_AVAILABLE keys {set(available)!r} != HOST_TOOLS keys "
        f"{set(host_tools_map)!r} — the map is not populated for the real session; "
        "conftest.py's pytest_configure must call freeze_host_tool_availability()"
    )
    for tool, val in available.items():
        assert isinstance(val, bool), (
            f"_HOST_TOOL_AVAILABLE[{tool!r}] must be a bool, got {val!r}"
        )


def test_ac13_burn_guard_survives_host_tool_wiring() -> None:
    import os  # noqa: PLC0415

    guard_dir = os.environ.get("HAL_LLM_BURN_GUARD")
    assert guard_dir, (
        "HAL_LLM_BURN_GUARD not set — conftest's FFEA1913 burn-guard pytest_configure "
        "did not run; a second pytest_configure defined for host-tool wiring may have "
        "shadowed it"
    )
    claude_path = shutil.which("claude")
    assert claude_path is not None and os.path.dirname(claude_path) == guard_dir, (
        f"expected shutil.which('claude') to resolve inside the burn-guard dir "
        f"{guard_dir!r}, got {claude_path!r} — the burn-guard PATH prepend was shadowed"
    )


# ─── shared helper: extract ONE function/method body by name (used by AC5b/AC7) ─


def _extract_function_body(source: str, func_name: str) -> str:
    """AST-based extraction of a function/method's body (rev 5, gate pass 3
    fix): the previous indentation-based approach recorded the `def` line's
    indent and collected only lines with strictly greater indentation — for a
    MULTI-LINE signature the closing-paren/return-annotation continuation
    line has indentation <= the def line's, so the loop stopped before the
    real body and returned the argument list instead. That made AC5b
    permanently unsatisfiable for any such call site regardless of what
    GREEN wrote. `ast.parse` + `FunctionDef.body` sidesteps signature
    line-wrapping, decorators, and nested-def hazards entirely, not by a
    file-wide regex (gate MINOR-3 / gate-2 MINOR-3 / gate-3 defect).

    Collects ALL matching FunctionDef/AsyncFunctionDef nodes (rev 6, gate
    pass 4 advisory): silently returning the first `ast.walk` hit would keep
    working today (all 9 names in play are unique per file) but would
    silently pick the wrong body the day a same-named method is added to a
    second class — asserting exactly one match makes that failure loud
    instead of silent."""
    tree = ast.parse(source)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name
    ]
    assert len(matches) == 1, (
        f"expected exactly one def {func_name}(...) in source, found {len(matches)}"
    )
    node = matches[0]
    assert node.body, f"def {func_name}(...) has an empty body"
    start_line = node.body[0].lineno
    end_line = node.body[-1].end_lineno
    assert end_line is not None, f"could not resolve end line for {func_name}"
    lines = source.splitlines()
    return "\n".join(lines[start_line - 1:end_line])


# ─── AC5b: all 8 M2 call sites wired, asserted per test-function body ──────

_C4_BUN_CALL_SITES = (
    ("test_phase_5_step2_verify_red.py", "test_event_group_kind_matches_inferred_plan"),
    ("test_phase_5_step3_verify_green.py", "test_emits_green_test_outcome_event_per_group"),
)
_C5_SEMGREP_CALL_SITES = (
    ("test_gh595_red_lint_preflight_batch.py", "test_ac3_clean_fixture_happy_path_preserves_prev_data"),
)
_C6_DOCTOR_GIT_CALL_SITES = (
    ("test_doctor.py", "test_ac1_doctor_json_exits_0_and_parses_with_status_and_checks"),
    ("test_doctor.py", "test_ac2_json_checks_cover_contract_names_and_statuses_ok_or_warn"),
    ("test_doctor.py", "test_ac3_human_mode_exits_0_with_summary_and_per_check_lines"),
    ("test_doctor_v2.py", "test_ac7_json_mode_reports_13_checks_including_all_new_v2_names"),
    ("test_doctor_v2.py", "test_ac8_human_mode_reports_at_least_13_status_lines_and_summary"),
)


def test_ac5b_m2_call_sites_wired_per_test_function_body() -> None:
    tests_dir = Path(__file__).parent

    def _body(filename: str, func_name: str) -> str:
        source = (tests_dir / filename).read_text(encoding="utf-8")
        return _extract_function_body(source, func_name)

    for filename, func_name in _C4_BUN_CALL_SITES:
        body = _body(filename, func_name)
        assert re.search(r"""skip_without\(\s*["']bun["']\s*\)""", body), (
            f"{filename}::{func_name} does not call skip_without('bun') in its own body (C4)"
        )

    for filename, func_name in _C5_SEMGREP_CALL_SITES:
        body = _body(filename, func_name)
        assert re.search(r"""skip_without\(\s*["']semgrep["']\s*\)""", body), (
            f"{filename}::{func_name} does not call skip_without('semgrep') in its own body (C5)"
        )

    for filename, func_name in _C6_DOCTOR_GIT_CALL_SITES:
        body = _body(filename, func_name)
        assert re.search(r"""skip_without\(\s*["']git["']\s*\)""", body), (
            f"{filename}::{func_name} does not call skip_without('git') in its own body (C6)"
        )


# ─── AC6: tests/helpers/git_repo.py::init_repo — real git, real side-effect ─
# The commit is made by a SEPARATE subprocess.run() call carrying no `-c`
# flags at all — the point is that the product's own commits inherit none of
# a test's per-invocation config, only repo-local config written by init_repo.


def test_ac6_init_repo_commit_by_separate_no_dash_c_process_succeeds_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # bd#49: this file — the one that enforces the sanctioned form on eight
    # OTHER call sites — carried the unsanctioned form itself, as a
    # `@pytest.mark.skipif(not shutil.which("git"), ...)`. That decorator is
    # evaluated at IMPORT time, before `pytest_configure` freezes availability,
    # so it could not consult the frozen map even in principle.
    # Deferred import, never module-top: D1CF5FDF collectability (see module
    # docstring) requires this file to collect cleanly and fail at assert time.
    from helpers.host_tools import skip_without  # noqa: PLC0415

    skip_without("git")

    from helpers.git_repo import init_repo  # noqa: PLC0415 — does not exist yet

    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Isolate ambient global git config — init_repo must not rely on it, and
    # the commit below must not inherit anything but repo-local config.
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_home / "gitconfig"))

    init_repo(str(repo_dir))
    (repo_dir / "f.txt").write_text("x", encoding="utf-8")
    add = subprocess.run(["git", "add", "f.txt"], cwd=repo_dir, capture_output=True, text=True)
    assert add.returncode == 0, f"git add failed rc={add.returncode}: {add.stderr!r}"

    # No -c flags here at all — the product's own `commit_red_tests` git commit
    # inherits nothing from a test's per-invocation config either.
    commit = subprocess.run(
        ["git", "commit", "-m", "isolated commit"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    assert commit.returncode == 0, (
        "git commit (no -c flags, separate process) failed with HOME/GIT_CONFIG_GLOBAL "
        "pointed at an empty tmp dir — init_repo did not write a repo-local identity: "
        f"rc={commit.returncode} stderr={commit.stderr!r}"
    )


# ─── AC7: the _init_repo definition in gh1034 specifically ─────────────────


def test_ac7_gh1034_init_repo_definition_delegates_to_helpers_git_repo() -> None:
    gh1034_path = Path(__file__).parent / "test_gh1034_red_cycle2_degeneracy.py"
    source = gh1034_path.read_text(encoding="utf-8")

    assert "from helpers.git_repo import init_repo" in source, (
        "test_gh1034_red_cycle2_degeneracy.py does not import init_repo from "
        "helpers.git_repo yet"
    )

    body = _extract_function_body(source, "_init_repo")
    assert "init_repo(" in body, (
        f"_init_repo body does not call the shared init_repo() helper:\n{body!r}"
    )
    assert '"init"' not in body and "'init'" not in body, (
        f"_init_repo body still invokes a bare git init directly instead of delegating "
        f"to helpers.git_repo.init_repo:\n{body!r}"
    )


# ─── AC8: docs/host-requirements.md ─────────────────────────────────────────


def test_ac8_host_requirements_doc_names_git_identity_zsh_bun_semgrep() -> None:
    repo_root = Path(__file__).parent.parent.parent  # engine_py/tests -> engine_py -> repo root
    doc_path = repo_root / "docs" / "host-requirements.md"
    assert doc_path.exists(), f"{doc_path} does not exist yet (expected RED)"

    text = doc_path.read_text(encoding="utf-8")
    for term in ("git", "identity", "zsh", "bun", "semgrep", "2.28", "helpers.host_tools.HOST_TOOLS"):
        assert term in text, f"docs/host-requirements.md does not mention {term!r}"


# ─── AC9-AC11: end-to-end via pytester, exercising the REAL hookwrapper ────


def _forced_absent_conftest_source(tool: str) -> str:
    """conftest.py for the pytester sub-run. Imports `helpers.host_tools`
    (the REAL module engine_py/tests/helpers/host_tools.py, same object
    already in sys.modules — never a reimplementation) — NOT `conftest`:
    pytest imports the sub-run's own generated conftest.py under the exact
    module name `conftest`, so a sub-run conftest that tries `import conftest`
    gets *itself*, partially initialised, and dies with an AttributeError
    (verified: this is exactly how RED rev 2 failed here, per gate finding).
    Reassigns the real `pytest_runtest_makereport` hookwrapper object under
    this name so pytest's plugin manager registers the SAME function object
    for the sub-run. Forces `tool` absent via the real module's own frozen
    `_HOST_TOOL_AVAILABLE` map — never via a live shutil.which() patch
    (gate MAJOR-5)."""
    real_tests_dir = str(Path(__file__).parent)
    return (
        "import sys\n"
        f"sys.path.insert(0, {real_tests_dir!r})\n"
        "from helpers import host_tools\n"
        f"host_tools._HOST_TOOL_AVAILABLE[{tool!r}] = False\n"
        "pytest_runtest_makereport = host_tools.pytest_runtest_makereport\n"
    )


def _force_git_absent_in_parent_session(monkeypatch: pytest.MonkeyPatch):
    """Gate-2 MAJOR-3 fix: the pytester sub-run below is IN-PROCESS, so it
    shares the same `helpers.host_tools` module object as the parent session.
    Mutating `_HOST_TOOL_AVAILABLE` directly (as the rev-2 generated conftest
    did) permanently poisons the parent's map for the ~3900 remaining tests in
    this session. `monkeypatch.setitem` restores it at teardown; the generated
    conftest's own assignment is kept too (harmless in-process, needed if the
    run is ever switched to subprocess mode)."""
    from helpers import host_tools  # noqa: PLC0415 — does not exist yet

    available = getattr(host_tools, "_HOST_TOOL_AVAILABLE", None)
    assert available is not None, (
        "helpers.host_tools._HOST_TOOL_AVAILABLE not implemented yet (expected RED)"
    )
    monkeypatch.setitem(available, "git", False)


def test_ac9_pytester_call_phase_filenotfounderror_yields_one_skip(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_git_absent_in_parent_session(monkeypatch)
    pytester.makeconftest(_forced_absent_conftest_source("git"))
    pytester.makepyfile(
        "def test_needs_git():\n"
        "    raise FileNotFoundError(2, 'No such file or directory', 'git')\n"
    )
    result = pytester.runpytest()
    result.assert_outcomes(skipped=1)


def test_ac10_pytester_setup_phase_filenotfounderror_yields_skip_not_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_git_absent_in_parent_session(monkeypatch)
    pytester.makeconftest(_forced_absent_conftest_source("git"))
    pytester.makepyfile(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def broken_fixture():\n"
        "    raise FileNotFoundError(2, 'No such file or directory', 'git')\n"
        "\n"
        "def test_needs_git(broken_fixture):\n"
        "    pass\n"
    )
    result = pytester.runpytest()
    result.assert_outcomes(skipped=1, errors=0)


def test_ac11_skip_reason_rendered_via_rs_flag_longrepr_3tuple(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_git_absent_in_parent_session(monkeypatch)
    pytester.makeconftest(_forced_absent_conftest_source("git"))
    pytester.makepyfile(
        "def test_needs_git():\n"
        "    raise FileNotFoundError(2, 'No such file or directory', 'git')\n"
    )
    result = pytester.runpytest("-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(
        ["*requires host tool 'git' — see docs/host-requirements.md*"]
    )
