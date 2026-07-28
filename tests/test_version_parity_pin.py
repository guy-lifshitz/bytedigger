"""RED tests for the pointer dependency PIN check (bytedigger#11, lot-bd11).

`scripts/version_parity.py` reads every declaration's own `version`, but
today reads no `dependencies` pin -- so `packaging/pypi-pointer/pyproject.toml`
can pin `bytedigger-engine==X` while its own `[project].version` (and every
other declaration) reads Y, and `--check` still exits 0. This file is the
new, separate PIN_DECLARATIONS registry's RED suite (spec §2.1); it never
touches the existing `DECLARATIONS`/`--list-declarations` contract.

Per workflows.md §1q, nothing here resolves at import/collection time.
`_source_pin_registry()` returns `[]` (never raises) when the UUT does not
yet declare `PIN_DECLARATIONS` -- every test that needs the registry calls
`_require_pin_registry()` itself and gets a clear assert-time message
instead of a collection error.

The UUT is never imported or mocked, only subprocess-invoked via the
sibling suite's `_run` (explicit --root, neutral cwd). This module reuses
`test_version_parity`'s helpers (`_make_tmp_repo`, `_run`, `_canonical_version`,
`DECL_RELPATHS`, `SCRIPT`, `REPO_ROOT`, `NEUTRAL_CWD`) rather than
re-transcribing them -- pytest's own rootdir import mechanics make this a
plain top-level `import`, not a `sys.path` mutation performed by this file.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import test_version_parity as tvp

_PROJECT_HEADER_RE = re.compile(r"^\[project\]\s*$")
_TABLE_HEADER_RE = re.compile(r"^\[")
_DEP_KEY_RE = re.compile(r"^dependencies\s*=")
_DEP_LINE_RE = re.compile(r"(?m)^dependencies = .*\n")


def _source_pin_registry() -> list:
    """(relpath, required package name) pairs read out of the UUT's own
    `PIN_DECLARATIONS` literal via `ast` -- never imported, never
    transcribed. Returns `[]` (not an assertion) when the shape is absent
    or not yet the closed 2-tuple-of-strings form spec §2.1 describes, so
    collection never fails; callers assert non-emptiness themselves."""
    tree = ast.parse(tvp.SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PIN_DECLARATIONS"
        ):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            return []
        pairs = []
        for element in node.value.elts:
            if not (
                isinstance(element, (ast.Tuple, ast.List))
                and len(element.elts) == 2
            ):
                return []
            path_node, pkg_node = element.elts
            if not (
                isinstance(path_node, ast.Constant)
                and isinstance(path_node.value, str)
                and isinstance(pkg_node, ast.Constant)
                and isinstance(pkg_node.value, str)
            ):
                return []
            pairs.append((path_node.value, pkg_node.value))
        return pairs
    return []


def _require_pin_registry() -> list:
    registry = _source_pin_registry()
    assert registry, (
        f"{tvp.SCRIPT} declares no PIN_DECLARATIONS registry yet (spec §2.1) "
        f"-- the pin check has not been added"
    )
    return registry


def _pointer_relpath() -> str:
    """The one non-canonical toml-project-version declaration already
    registered in the existing `DECLARATIONS` -- derived structurally
    (never a hardcoded relpath string), matching spec §2.1's target file."""
    candidates = [
        p
        for p in tvp.DECL_RELPATHS
        if tvp.DECL_KINDS[p] == tvp.KIND_TOML_PROJECT_VERSION
        and p != tvp.CANONICAL_RELPATH
    ]
    assert len(candidates) == 1, (
        f"expected exactly one non-canonical toml-project-version "
        f"declaration (the pypi pointer) in DECLARATIONS; found "
        f"{candidates!r}"
    )
    return candidates[0]


def _read_pin(path: Path) -> list:
    """Own reader for a pointer's `dependencies` array (spec §2.2 grammar):
    find [project], then the first `^dependencies =` line before the next
    `^[` table header, accumulating physical lines until `]`. Entries are
    extracted as double-quoted substrings. Independent of the UUT -- a test
    asserting the pin landed never asks the writer to confirm its own work.
    """
    text = path.read_text()
    in_project = False
    buf = None
    for line in text.splitlines():
        if _PROJECT_HEADER_RE.match(line):
            in_project = True
            continue
        if not in_project:
            continue
        if buf is None:
            if _TABLE_HEADER_RE.match(line):
                break
            if _DEP_KEY_RE.match(line):
                buf = line.split("=", 1)[1]
                if "]" in buf:
                    break
        else:
            buf += "\n" + line
            if "]" in line:
                break
    if buf is None:
        return []
    return re.findall(r'"([^"]*)"', buf)


def _pkg_name() -> str:
    """The pinned package name, derived from the REAL pointer file's own
    dependencies entry -- never a literal 'bytedigger-engine' typed here."""
    relpath = _pointer_relpath()
    entries = _read_pin(tvp.REPO_ROOT / relpath)
    assert entries, (
        f"real {relpath} has no parsable dependencies entry -- cannot "
        f"derive the pinned package name (fixture assumption broken)"
    )
    return entries[0].split("==", 1)[0]


def _uniform_versions(version: str) -> dict:
    """Every registered declaration set to the same `version` -- avoids
    the current-version literal entirely (spec §2.3)."""
    return {p: version for p in tvp.DECL_RELPATHS}


def _set_pointer_pin_raw(repo: Path, relpath: str, raw_value: str) -> None:
    """Overwrite the fixture's single-line `dependencies = ...` with an
    arbitrary raw TOML value, for malformed/mismatched pin fixtures.
    Requires the baseline fixture (built by `_write_declaration`, which
    already emits this line for the toml kind) to carry exactly one."""
    path = repo / relpath
    text = path.read_text()
    new_text, n = _DEP_LINE_RE.subn(f"dependencies = {raw_value}\n", text, count=1)
    assert n == 1, (
        f"fixture {relpath} does not carry a single-line 'dependencies = "
        f"...' to replace: text={text!r}"
    )
    path.write_text(new_text)


def _remove_pointer_dependencies_key(repo: Path, relpath: str) -> None:
    path = repo / relpath
    text = path.read_text()
    new_text, n = _DEP_LINE_RE.subn("", text, count=1)
    assert n == 1, (
        f"fixture {relpath} does not carry a single 'dependencies = ...' "
        f"line to remove: text={text!r}"
    )
    path.write_text(new_text)


class TestVersionParityPin:
    def test_ac30_pin_matches_canonical_passes_then_diverges_fails_naming_pointer(
        self, tmp_path
    ):
        """AC30 (property, discriminating): canon=V, pin=bytedigger-engine==V
        -> --check == 0. Same repo with canonical shifted to V2 while the
        pin is LEFT at V (and the pointer's own [project].version is ALSO
        moved to V2, so the pre-existing per-declaration check alone stays
        happy) -> --check == 1, naming the pointer path. On today's UUT
        (no pin-check) the second case is indistinguishable from the first
        and returns 0 -- this half is red until GREEN."""
        pointer_rel = _pointer_relpath()
        pkg = _pkg_name()
        V, V2 = "3.4.5", "7.2.9"

        repo_a = tvp._make_tmp_repo(tmp_path / "a", _uniform_versions(V))
        result_a = tvp._run(repo_a, "--check")
        assert result_a.returncode == 0, (
            f"agreeing pin ({V}) must pass --check, got {result_a.returncode}, "
            f"stdout={result_a.stdout!r} stderr={result_a.stderr!r}"
        )

        repo_b = tvp._make_tmp_repo(tmp_path / "b", _uniform_versions(V2))
        _set_pointer_pin_raw(repo_b, pointer_rel, f'["{pkg}=={V}"]')
        result_b = tvp._run(repo_b, "--check")
        assert result_b.returncode == 1, (
            f"pin left at {V} while canonical moved to {V2} must fail "
            f"--check (today's UUT has no pin-check and returns 0): got "
            f"{result_b.returncode}, stdout={result_b.stdout!r}"
        )
        combined_b = result_b.stdout + result_b.stderr
        assert pointer_rel in combined_b, (
            f"the divergence must name the pointer path {pointer_rel!r}: "
            f"{combined_b!r}"
        )

    def test_ac31_real_pointer_pin_equals_real_canonical_version(self):
        """AC31 (production side-effect, §1l): the REAL pointer's pin, read
        by the suite's own independent reader, equals the real canonical
        version. This instance's incident is already corrected, so this is
        expected to hold today too -- it is the correctness guard that
        stays load-bearing once GREEN adds the enforcement no one runs."""
        pointer_rel = _pointer_relpath()
        canonical = tvp._canonical_version(tvp.REPO_ROOT)
        entries = _read_pin(tvp.REPO_ROOT / pointer_rel)
        assert len(entries) == 1, (
            f"real {pointer_rel} dependencies must carry exactly one "
            f"entry, got {entries!r}"
        )
        pkg = entries[0].split("==", 1)[0]
        assert entries[0] == f"{pkg}=={canonical}", (
            f"real pointer pin {entries[0]!r} does not equal canonical "
            f"{canonical!r}"
        )

    def test_ac32_pin_failure_forms_no_traceback_and_accumulates(self, tmp_path):
        """AC32 (§1n): each table-2.4 condition -> exit 1, path + reason,
        no traceback; plus accumulate -- an independent divergence
        elsewhere in the same run is ALSO reported."""
        pointer_rel = _pointer_relpath()
        pkg = _pkg_name()
        V = "3.4.5"

        repo_a = tvp._make_tmp_repo(tmp_path / "a", _uniform_versions(V))
        _remove_pointer_dependencies_key(repo_a, pointer_rel)
        result_a = tvp._run(repo_a, "--check")
        combined_a = result_a.stdout + result_a.stderr
        assert result_a.returncode == 1
        assert pointer_rel in combined_a and "no dependencies key" in combined_a, (
            f"missing dependencies key must be reported: {combined_a!r}"
        )
        assert "Traceback" not in combined_a

        repo_b = tvp._make_tmp_repo(tmp_path / "b", _uniform_versions(V))
        _set_pointer_pin_raw(repo_b, pointer_rel, '"not-an-array"')
        result_b = tvp._run(repo_b, "--check")
        combined_b = result_b.stdout + result_b.stderr
        assert result_b.returncode == 1
        assert (
            pointer_rel in combined_b
            and "dependencies is not an array of strings" in combined_b
        ), f"non-array pin must be reported: {combined_b!r}"
        assert "Traceback" not in combined_b

        repo_c = tvp._make_tmp_repo(tmp_path / "c", _uniform_versions(V))
        _set_pointer_pin_raw(
            repo_c, pointer_rel, f'["{pkg}=={V}", "other-package==1.0.0"]'
        )
        result_c = tvp._run(repo_c, "--check")
        combined_c = result_c.stdout + result_c.stderr
        assert result_c.returncode == 1
        assert (
            pointer_rel in combined_c
            and "dependencies has 2 entries, expected exactly 1" in combined_c
        ), f"multi-entry pin must be reported: {combined_c!r}"
        assert "Traceback" not in combined_c

        versions_d = _uniform_versions(V)
        versions_d[".claude-plugin/plugin.json"] = "9.9.8"  # independent divergence
        repo_d = tvp._make_tmp_repo(tmp_path / "d", versions_d)
        _set_pointer_pin_raw(repo_d, pointer_rel, f'["{pkg}==9.0.0"]')
        result_d = tvp._run(repo_d, "--check")
        combined_d = result_d.stdout + result_d.stderr
        assert result_d.returncode == 1
        assert (
            pointer_rel in combined_d
            and f"pin is {pkg}==9.0.0, expected {pkg}=={V}" in combined_d
        ), f"single wrong pin entry must be reported with the expected form: {combined_d!r}"
        assert ".claude-plugin/plugin.json" in combined_d, (
            f"accumulate must still hold -- the independent divergence must "
            f"ALSO be reported: {combined_d!r}"
        )
        assert "Traceback" not in combined_d

    def test_ac33_write_updates_pin_surgically_and_check_passes_after(self, tmp_path):
        """AC33: --write X.Y.Z updates the pointer's pin to
        pkg==X.Y.Z, byte-surgical over the rest of the file (snapshot
        before/after, as AC12), and a following --check on the same
        --root exits 0."""
        pointer_rel = _pointer_relpath()
        pkg = _pkg_name()
        V, V2 = "3.4.5", "8.1.0"
        repo = tvp._make_tmp_repo(tmp_path, _uniform_versions(V))
        pointer_path = repo / pointer_rel
        before_text = pointer_path.read_text()

        result = tvp._run(repo, "--write", V2)
        assert result.returncode == 0, f"--write failed: {result.stderr!r}"

        after_text = pointer_path.read_text()
        expected_text = before_text.replace(
            f'version = "{V}"', f'version = "{V2}"'
        ).replace(f'"{pkg}=={V}"', f'"{pkg}=={V2}"')
        assert after_text == expected_text, (
            f"--write must update the pointer's version AND its pin, "
            f"byte-surgically: got={after_text!r} expected={expected_text!r}"
        )

        check_result = tvp._run(repo, "--check")
        assert check_result.returncode == 0, (
            f"--check after --write must pass, got {check_result.returncode}, "
            f"stdout={check_result.stdout!r} stderr={check_result.stderr!r}"
        )

    def test_ac34_no_hardcoded_current_pin_literal_in_source(self):
        """AC34 (anti-hal#1300 lint): the UUT source contains no
        `<pkg>==<digit>` literal -- the expected pin is always assembled
        from the read canonical version, never written as a state literal."""
        source = tvp.SCRIPT.read_text()
        pkg = _pkg_name()
        offending = re.findall(rf"{re.escape(pkg)}==\d", source)
        assert not offending, (
            f"scripts/version_parity.py must never hardcode a version-"
            f"bearing pin literal for {pkg!r}: found {offending!r}"
        )

    def test_ac35_each_pin_declaration_is_load_bearing_on_check(self, tmp_path):
        """AC35 (mirrors AC29): breaking any ONE registered pin, on its own,
        makes --check exit 1 and name that path. Quantified over the
        registry (via `_require_pin_registry`), not transcribed."""
        registry = _require_pin_registry()
        V = "3.4.5"
        for i, (relpath, pkg) in enumerate(registry):
            repo = tvp._make_tmp_repo(tmp_path / f"pin-{i}", _uniform_versions(V))
            _set_pointer_pin_raw(repo, relpath, f'["{pkg}==0.0.1"]')
            result = tvp._run(repo, "--check")
            assert result.returncode == 1, (
                f"breaking the pin at {relpath} alone must fail --check, "
                f"got {result.returncode}, stdout={result.stdout!r}"
            )
            assert relpath in result.stdout, (
                f"--check must name the broken pin path {relpath!r}: "
                f"{result.stdout!r}"
            )

    def test_ac36_pin_registry_is_subset_of_declarations_and_missing_reports_once(
        self, tmp_path
    ):
        """AC36: every PIN_DECLARATIONS path is also in DECLARATIONS, and a
        missing pointer file is named EXACTLY ONCE -- the pin-pass must stay
        silent when the main declarations-pass has already reported
        'missing' for the same path (no duplicate line)."""
        registry = _require_pin_registry()
        for relpath, _pkg in registry:
            assert relpath in tvp.DECL_RELPATHS, (
                f"PIN_DECLARATIONS path {relpath!r} is not registered in "
                f"DECLARATIONS -- every pin path must also be a known "
                f"declaration (spec §2.1 invariant)"
            )

        pointer_rel = _pointer_relpath()
        repo = tvp._make_tmp_repo(tmp_path, _uniform_versions("3.4.5"))
        (repo / pointer_rel).unlink()
        result = tvp._run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        occurrences = combined.count(pointer_rel)
        assert occurrences == 1, (
            f"a missing pointer file must be named exactly once -- found "
            f"{occurrences} occurrences: {combined!r}"
        )
