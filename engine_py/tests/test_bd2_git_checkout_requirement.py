"""RED tests — bd#2: declare the suite's git-checkout requirement and skip honestly.

Three tests in `test_gh1220_ambient_cwd_commit_refusal.py` resolve the project
by climbing from the test file up to an entry named `.git`. `git archive`, an
installed wheel and a release tarball all ship tracked files without one, so
those three fail for anyone who installs the package, with a message
("expected the live worktree root to resolve") that reads as a defect in the
tool rather than as a missing precondition.

bd#102 hit the same shape on a different axis — the suite silently required
host *binaries* it never declared — and was fixed by declaring the requirement
and skipping honestly when it is absent. This is that treatment for the second
axis: the *shape of the tree* the suite runs in.

It is a separate declaration rather than a fake `HOST_TOOLS` entry named
`.git`, because `host_tool_skip_reason` renders "requires host tool '<name>'",
and claiming a missing binary for a missing checkout would be the same class
of misleading message this issue is about.

AC1  helpers.live_repo exposes GIT_CHECKOUT_REQUIREMENT and skip_without_git_checkout
AC2  with the frozen map saying "no checkout", skip_without_git_checkout() skips,
     and the reason names the requirement and points at the docs
AC3  with the frozen map saying "checkout present", it does NOT skip
     (negative control: this must not degrade into a blanket skip)
AC4  availability is frozen once, from conftest's existing pytest_configure —
     helpers/live_repo.py must NOT define its own pytest_configure
AC5  the frozen map is actually populated in this session (the mechanism is
     wired, not merely defined)
AC6  each of the three checkout-requiring tests calls the guard, and does so as
     the first statement of its body
AC7  end to end: a test that calls the guard with no checkout is reported
     `skipped`, not `failed`, and the reason renders
AC8  docs/host-requirements.md declares the requirement
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

# AC7 needs the bundled pytester plugin for an end-to-end, real-report-path
# exercise. Same wiring as tests/test_bd102_host_tool_contract.py.
pytest_plugins = "pytester"

try:  # pre-GREEN the module does not exist; keep every AC an assert, not a
    from helpers import live_repo  # noqa: E402  collection error (§1q)
except ImportError:  # pragma: no cover — pre-GREEN only
    live_repo = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _module_present():
    assert live_repo is not None, (
        "expected engine_py/tests/helpers/live_repo.py to exist — the declared "
        "counterpart of helpers/host_tools.py for the tree-shape axis"
    )


_GH1220 = HERE / "test_gh1220_ambient_cwd_commit_refusal.py"

# The set re-derived rather than taken from the issue. Every `find_repo_root`
# call site in the suite lives in test_gh1220_ambient_cwd_commit_refusal.py;
# AC18-AC21 monkeypatch it to a tmp_path repo and are checkout-independent;
# these three call it unpatched against the live root. AC32, listed in the
# issue's four, is a different defect entirely (see bd#3) and is deliberately
# NOT here — guarding it would hide that bug behind a skip.
_CHECKOUT_REQUIRING_TESTS = (
    "test_ac16_cheap_head_read_matches_git_rev_parse_on_live_repo",
    "test_ac17_baseline_capture_matches_git_rev_list_count_on_live_repo",
    "test_ac31_sentinel_state_restored_after_tmp_repo_configure",
)


def _function_body(source: str, name: str) -> list[ast.stmt]:
    """The real body of `name`, via the parse tree.

    bd#102 learned this the hard way: an indentation heuristic ends the body at
    the closing paren of a multi-line signature. Asserts exactly one match, so
    a future same-named method in a second class cannot silently supply the
    wrong body.
    """
    tree = ast.parse(source)
    matches = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one definition named {name!r}; found {len(matches)}"
    )
    return matches[0].body


def _first_real_statement(body: list[ast.stmt]) -> ast.stmt:
    """First statement, skipping the docstring."""
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1]
    return body[0]


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_live_repo_helper_declares_the_requirement():
    """AC1. Pre-GREEN FAIL: helpers/live_repo.py does not exist."""
    assert hasattr(live_repo, "GIT_CHECKOUT_REQUIREMENT"), (
        "expected a declared, quotable requirement string"
    )
    assert callable(getattr(live_repo, "skip_without_git_checkout", None)), (
        "expected a callable skip_without_git_checkout()"
    )
    reason = live_repo.GIT_CHECKOUT_REQUIREMENT
    assert "git checkout" in reason, (
        f"the reason must say what is missing in the reader's words; actual {reason!r}"
    )
    assert "docs/host-requirements.md" in reason, (
        f"the reason must point at the declaration; actual {reason!r}"
    )


# ── AC2 ──────────────────────────────────────────────────────────────────────

def test_ac2_skips_when_the_frozen_map_says_no_checkout(monkeypatch):
    """AC2: the skip fires, and the reason renders.

    Drives the frozen map via monkeypatch.setitem — never a live probe, and
    never a raw assignment: bd#102's own RED leaked `False` into the parent
    session and would have converted every later FileNotFoundError in the
    suite into a skip. setitem restores on teardown.

    Pre-GREEN FAIL: module absent.
    """
    monkeypatch.setitem(live_repo._GIT_CHECKOUT_AVAILABLE, "resolved", False)

    # `pytest.skip.Exception` (Skipped) derives from BaseException, not
    # Exception — `pytest.raises(Exception)` here would let the skip propagate
    # and this test would report as skipped, asserting nothing while looking
    # fine in the summary.
    with pytest.raises(pytest.skip.Exception) as excinfo:
        live_repo.skip_without_git_checkout()

    assert excinfo.typename == "Skipped", (
        f"expected pytest to record a skip, not a failure; actual "
        f"{excinfo.typename}: {excinfo.value!r}"
    )
    assert live_repo.GIT_CHECKOUT_REQUIREMENT in str(excinfo.value), (
        f"expected the declared reason in the skip text; actual {excinfo.value!r}"
    )


# ── AC3: negative control ────────────────────────────────────────────────────

def test_ac3_does_not_skip_when_a_checkout_is_present(monkeypatch):
    """AC3: with a checkout, the guard is a no-op.

    Without this, `skip_without_git_checkout` could be `pytest.skip(...)`
    unconditionally and AC2 would still pass — three real tests would then be
    dead on every machine, including CI, and nothing would say so.

    Pre-GREEN FAIL: module absent.
    """
    monkeypatch.setitem(live_repo._GIT_CHECKOUT_AVAILABLE, "resolved", True)
    live_repo.skip_without_git_checkout()  # must return normally


# ── AC4 ──────────────────────────────────────────────────────────────────────

def test_ac4_helper_defines_no_pytest_configure():
    """AC4: helpers/live_repo.py is not registered as a plugin, so a
    `pytest_configure` defined in it would never run — the map would stay
    empty and the whole mechanism would silently no-op while every other AC
    passed. bd#102 closed this exact hole for host_tools; same hole, same
    guard.

    Pre-GREEN FAIL: module absent.
    """
    assert not hasattr(live_repo, "pytest_configure"), (
        "helpers/live_repo.py must not define pytest_configure — freeze from "
        "conftest's existing one, as helpers/host_tools.py does"
    )
    source = (HERE / "helpers" / "live_repo.py").read_text(encoding="utf-8")
    assert "def pytest_configure" not in source, (
        "no pytest_configure in this module, not even an unexported one"
    )

    conftest_src = (HERE / "conftest.py").read_text(encoding="utf-8")
    assert "freeze_git_checkout_availability()" in conftest_src, (
        "conftest.pytest_configure must call freeze_git_checkout_availability()"
    )


# ── AC5 ──────────────────────────────────────────────────────────────────────

def test_ac5_frozen_map_is_populated_in_this_session():
    """AC5: the mechanism is wired, not merely defined. If the freeze call
    were dropped or shadowed, the map would be empty and every guard call a
    silent no-op.

    Pre-GREEN FAIL: module absent.
    """
    assert "resolved" in live_repo._GIT_CHECKOUT_AVAILABLE, (
        f"expected the frozen map populated at pytest_configure time; actual "
        f"{live_repo._GIT_CHECKOUT_AVAILABLE!r}"
    )
    assert isinstance(live_repo._GIT_CHECKOUT_AVAILABLE["resolved"], bool)


# ── AC6 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("test_name", _CHECKOUT_REQUIRING_TESTS)
def test_ac6_each_checkout_requiring_test_guards_first(test_name):
    """AC6: the guard is wired into each of the three, as the first statement.

    First, not merely present: AC16 and AC17 assert `root is not None`
    immediately, so a guard placed after that line would never be reached on
    the machine that needs it.

    Pre-GREEN FAIL: no guard call in any of the three.
    """
    source = _GH1220.read_text(encoding="utf-8")
    body = _function_body(source, test_name)
    first = _first_real_statement(body)

    rendered = ast.dump(first)
    assert "skip_without_git_checkout" in rendered, (
        f"expected {test_name} to call skip_without_git_checkout() as its "
        f"first statement; actual first statement: "
        f"{ast.unparse(first)!r}"
    )


# ── AC7: end to end ──────────────────────────────────────────────────────────

def test_ac7_pytester_reports_skipped_not_failed(pytester, monkeypatch):
    """AC7: a real pytest run of a guarded test with no checkout reports
    `skipped` and renders the reason.

    An AC that only calls the helper directly cannot tell a working
    mechanism from one whose skip is swallowed somewhere in the report path.

    The sub-run is in-process, so the frozen map is shared with this session —
    driven through monkeypatch.setitem so teardown restores it (bd#102 rev4).

    Pre-GREEN FAIL: module absent.
    """
    monkeypatch.setitem(live_repo._GIT_CHECKOUT_AVAILABLE, "resolved", False)
    monkeypatch.syspath_prepend(str(HERE))

    pytester.makepyfile(
        test_guarded="""
        import sys
        from helpers import live_repo

        def test_needs_a_checkout():
            live_repo.skip_without_git_checkout()
            assert False, "must never be reached without a checkout"
        """
    )
    result = pytester.runpytest("-rs")
    result.assert_outcomes(skipped=1, failed=0)
    result.stdout.fnmatch_lines(["*git checkout*"])


# ── AC8 ──────────────────────────────────────────────────────────────────────

def test_ac8_docs_declare_the_checkout_requirement():
    """AC8: the prose declaration exists and names both what is required and
    which tests require it. bd#102's doc is the precedent and the same file is
    the right home — the suite's environment contract belongs in one place.

    Pre-GREEN FAIL: docs/host-requirements.md says nothing about a checkout.
    """
    repo_root = HERE.parents[1]
    doc = (repo_root / "docs" / "host-requirements.md").read_text(encoding="utf-8")

    assert "git checkout" in doc, (
        "expected the checkout requirement declared alongside the host tools"
    )
    assert "helpers/live_repo.py" in doc or "helpers.live_repo" in doc, (
        "expected the doc to name its canonical machine-readable counterpart, "
        "as it does for HOST_TOOLS (§1g)"
    )
    for name in _CHECKOUT_REQUIRING_TESTS:
        short = name.split("_")[1]  # ac16 / ac17 / ac31
        assert short.upper() in doc or short in doc, (
            f"expected the doc to name which tests require a checkout; {short} "
            f"is missing"
        )
