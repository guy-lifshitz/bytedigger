"""bd#49 — a derived declaration gate on the host tool.

Spec: `docs/decisions/2026-08-04-bd49-host-tool-declaration-gate.md`.

WHAT THIS GATE ENFORCES, AND WHY EXACTLY THAT.

`helpers/host_tools.py::pytest_runtest_makereport` is already **total** over the ordinary
path: any test that fails with `FileNotFoundError` on a declared and genuinely
absent tool automatically becomes `skipped` — regardless of
how it invoked the tool. Measured on the corpus at `b95e48a`: **610** `test_*`
bodies reach a host tool by an argv literal (`subprocess.run(["git", …])`)
with no `which` at all, and all of them are covered by that mechanism (`docs/host-requirements.md`
reports ~500 such on `git` alone).

There is exactly one way out from under the total mechanism — **probe
availability yourself** and take a decision before the `FileNotFoundError` can fly.
There are **37** such bodies, and that is the COMPLETE set of escapes, not a sample. The gate's domain is
precisely them.

Hence the policy the gate enforces:

    a test body that PROBES host-tool availability ITSELF must
    declare what it does with the answer — either `skip_without(tool)` or
    `# host-tool-hard-fail: <argument>`. There is no silent third path.

THE BOUNDARY OF HONESTY (AC11). The scanner catches pre-emption done through `which`, and
does NOT catch it done otherwise — `os.path.exists("/usr/bin/git")`, a custom
`except FileNotFoundError: pytest.skip(...)`. This is not an oversight but a declared
boundary: it was measured that there are **zero** such forms in the corpus (`except FileNotFoundError`
next to a `skip` — 0 hits, `exists()` over binary paths — 0). Should a first one
appear, the boundary must move, and this paragraph is its place in the source.

WHAT THE GATE DOES NOT CLAIM: that the corpus as a whole is inert-safe without a tool
(totality is provided by the hook wrapper, the gate closes only the escapes), and that
`_C4/_C5/_C6_*_CALL_SITES` in `test_bd102_host_tool_contract.py` are no longer needed
— those enforce a stronger property (the form must be exactly
`skip_without`) on their 8 sites.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

TESTS_DIR = Path(__file__).parent


def _probes(source: str) -> list[tuple[str, str]]:
    """An import inside the body is this corpus's idiom for a symbol that does not yet exist
    (cf. `test_bd102_host_tool_contract.py::test_ac6`). A module-level import would break
    COLLECTION, and RED would be a collection error rather than assert-time (§1q)."""
    from helpers.host_tool_probes import (  # noqa: PLC0415
        undeclared_host_tool_probes,
    )

    return undeclared_host_tool_probes(source)


def _scan(source: str) -> list[tuple[str, str]]:
    return _probes(textwrap.dedent(source))


# ─── AC1: §1l — anchored to the live corpus, not to a fixture ─────────────

def test_ac1_live_corpus_has_no_undeclared_host_tool_probes() -> None:
    """Every body in the REAL `engine_py/tests/**` that probes host-tool
    availability itself declares the form it chose.

    This is a claim about the shipped corpus, not about a synthetic string —
    the spec's §1l anchor. At the time of RED it is false exactly 37 times.
    """
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for func_name, tool in _probes(source):
            offenders.append(f"{path.relative_to(TESTS_DIR).as_posix()}::{func_name} probes {tool!r}")

    assert offenders == [], (
        "bodies probe host-tool availability without declaring a form "
        f"({len(offenders)}):\n  " + "\n  ".join(offenders) + "\n"
        "Each must either call skip_without(<tool>) or carry the comment "
        "`# host-tool-hard-fail: <argument>` in its own body."
    )


# ─── AC2/AC3: positive legs — both forms are accepted ─────────────────────

def test_ac2_skip_without_form_is_accepted() -> None:
    assert _scan('''
        import shutil
        from helpers.host_tools import skip_without

        def test_thing():
            skip_without("bun")
            bun = shutil.which("bun")
            assert bun
    ''') == []


def test_ac3_declared_hard_fail_marker_is_accepted() -> None:
    assert _scan('''
        import shutil

        def test_thing():
            # host-tool-hard-fail: corpus parity cannot be certified
            # without ever running it — a silent skip here costs more than a red.
            bun = shutil.which("bun")
            assert bun is not None, "hard AC failure, not a skip"
    ''') == []


# ─── AC4/AC5: NEGATIVE LEGS — a gate that does not fail on a new site is inert ─

def test_ac4_undeclared_direct_probe_is_reported() -> None:
    """A new calling site that declared nothing MUST come back as an entry.

    Without this AC the gate is decoration: it would stay equally silent on a clean corpus
    and on a corpus full of undeclared probes.
    """
    assert _scan('''
        import shutil

        def test_thing():
            bun = shutil.which("bun")
            assert bun is not None
    ''') == [("test_thing", "bun")]


def test_ac5_undeclared_probe_reached_through_helper_is_reported() -> None:
    """A probe hidden in a helper that the test body calls.

    This is NOT hypothetical: `test_gh1338_corpus_parity_gate.py` keeps
    `shutil.which("bun")` in `_build_parity_fixture` rather than in the bodies, and that is
    exactly how eight of its ACs — ac11/12/13/23/26/37/38/39 — escape a direct
    scanner. A scanner keyed only on a direct call would miss exactly
    the subject bd#49 was raised for.
    """
    assert _scan('''
        import shutil

        def _build_parity_fixture(tmp_path):
            bun = shutil.which("bun")
            assert bun is not None
            return bun

        def test_thing(tmp_path):
            _build_parity_fixture(tmp_path)
    ''') == [("test_thing", "bun")]


# ─── AC6/AC7/AC8: the declaration must be real, its own, and about that tool ─

def test_ac6_marker_without_a_reason_is_not_a_declaration() -> None:
    """An empty marker is a silent exemption in the guise of a declaration."""
    assert _scan('''
        import shutil

        def test_thing():
            # host-tool-hard-fail:
            bun = shutil.which("bun")
            assert bun is not None
    ''') == [("test_thing", "bun")]


def test_ac7_declaration_does_not_leak_between_bodies_in_one_file() -> None:
    """Catches a scanner that looks for the marker/call across the whole file instead of the body."""
    assert _scan('''
        import shutil
        from helpers.host_tools import skip_without

        def test_declared():
            skip_without("bun")
            assert shutil.which("bun")

        def test_undeclared():
            assert shutil.which("bun")
    ''') == [("test_undeclared", "bun")]


def test_ac8_declaration_is_bound_to_the_probed_tool() -> None:
    """Catches a scanner that checks the fact of a `skip_without` call instead of its argument."""
    assert _scan('''
        import shutil
        from helpers.host_tools import skip_without

        def test_thing():
            skip_without("git")
            assert shutil.which("bun")
    ''') == [("test_thing", "bun")]


# ─── AC10: aliases — _shutil, _sh, _shutil_real live in the corpus ────────

def test_ac10_aliased_and_bare_which_are_both_recognised() -> None:
    assert _scan('''
        import shutil as _sh

        def test_aliased_module():
            assert _sh.which("bun")
    ''') == [("test_aliased_module", "bun")]

    assert _scan('''
        from shutil import which

        def test_bare_name():
            assert which("bun")
    ''') == [("test_bare_name", "bun")]


# ─── AC9: §1a sibling — bd#102 AC5b untouched by the M2 conformance pass ──

def test_ac9_bd102_call_site_contract_is_untouched_by_this_pr() -> None:
    """M2 edits only forms 3/4/5; none of bd#102's 8 sites is among them.

    Checked against the source rather than by a run: the files listed in
    `_C4/_C5/_C6` must still carry `skip_without` in their bodies.
    """
    from test_bd102_host_tool_contract import (  # noqa: PLC0415
        _C4_BUN_CALL_SITES,
        _C5_SEMGREP_CALL_SITES,
        _C6_DOCTOR_GIT_CALL_SITES,
    )

    sites = (
        [(f, n, "bun") for f, n in _C4_BUN_CALL_SITES]
        + [(f, n, "semgrep") for f, n in _C5_SEMGREP_CALL_SITES]
        + [(f, n, "git") for f, n in _C6_DOCTOR_GIT_CALL_SITES]
    )
    assert len(sites) == 8, f"bd#102 lists {len(sites)} sites, not 8"

    for filename, func_name, tool in sites:
        source = (TESTS_DIR / filename).read_text(encoding="utf-8")
        reported = dict(_probes(source))
        assert reported.get(func_name) != tool, (
            f"{filename}::{func_name} lost its declaration for {tool!r} — "
            "the M2 conformance pass disturbed a bd#102 site, which it must not"
        )
