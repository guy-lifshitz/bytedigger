"""RED tests for the pointer dependency PIN check (bytedigger#11, lot-bd11), v2.

`packaging/pypi-pointer/pyproject.toml` declares `dependencies = ["bytedigger-
engine==X.Y.Z"]`. `version_parity.py` reads every declaration's own
`[project].version` but reads no `dependencies` pin at all -- so the pointer
can pin a stale (or wrong-package) engine version while its own `version`
key still reads correctly, and `--check` still exits 0. This file is the
new, separate `PIN_DECLARATIONS` registry's RED suite (spec §2.1); it never
touches the existing `DECLARATIONS`/`--list-declarations` contract.

Per workflows.md §1q, nothing here resolves at import/collection time.
`_source_pin_registry()` returns `[]` (never raises) when the UUT does not
yet declare `PIN_DECLARATIONS` -- every test that needs the registry calls
`_require_pin_registry()` itself and gets a clear assert-time message
instead of a collection error.

The UUT is never imported or mocked, only subprocess-invoked via the
sibling suite's `_run` (explicit --root, neutral cwd). This module reuses
`test_version_parity`'s helpers (`_make_tmp_repo`, `_run`, `_canonical_version`,
`DECL_RELPATHS`, `SCRIPT`, `REPO_ROOT`, `_extract_job_block`) rather than
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
_BARE_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


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


def _companion_relpath(exclude: set) -> str:
    """A declaration path, derived from the registry (never transcribed),
    usable as an INDEPENDENT second divergence for accumulate assertions."""
    candidates = sorted(p for p in tvp.DECL_RELPATHS if p not in exclude)
    assert candidates, "no companion declaration available in the registry"
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
    arbitrary raw TOML value (may itself span multiple physical lines), for
    malformed/mismatched/multiline pin fixtures. Requires the baseline
    fixture (built by `_write_declaration`, which already emits this line
    for the toml kind) to carry exactly one."""
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


def _build_realistic_pointer_text(
    version: str, pin_version: str, canonical_old: str, pkg: str
) -> str:
    """A pointer pyproject.toml resembling the REAL file (MAJOR-2 of the
    gate): a comment block ABOVE `dependencies` naming a historical
    version in prose, plus a second string array (`classifiers`) inside
    [project]. A naive whole-file `text.replace(old_version, new_version)`
    surgical writer corrupts the historical version named in the comment
    prose; only a position-anchored writer survives byte-equality here.
    `pkg` is the derived registered package name (never a transcribed
    'bytedigger-engine' literal) -- AC33 compares its --write output
    against the same derived `_pkg_name()`, so the fixture must be built
    from it too."""
    return (
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "bytedigger-engine"\n'
        f'version = "{version}"\n'
        'description = "x"\n'
        "classifiers = [\n"
        '  "Development Status :: 3 - Alpha",\n'
        '  "Programming Language :: Python :: 3",\n'
        "]\n"
        f"# historical: pin was bumped from {canonical_old} to {version} "
        f"after a drift incident\n"
        f'dependencies = ["{pkg}=={pin_version}"]\n'
    )


def _non_docstring_string_constants(source: str) -> list:
    """Every string literal in the CODE, excluding docstrings -- comments
    are never ast nodes at all, so scanning `ast.Constant` already cuts by
    code rather than raw text (MINOR-2 of the gate: a GREEN documenting the
    pin grammar in a docstring example must not trip this lint). Docstring
    nodes (module/class/function/async-function body's first bare string
    `Expr`) are identified by node identity and excluded; f-string literal
    segments (`JoinedStr` constant pieces) are ordinary `ast.Constant`
    nodes and so are included automatically."""
    tree = ast.parse(source)
    docstring_ids = set()

    def _mark(body):
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstring_ids.add(id(body[0].value))

    _mark(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            _mark(node.body)

    values = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        ):
            values.append(node.value)
    return values


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

    def test_ac30b_pin_with_correct_version_but_wrong_package_name_is_rejected(
        self, tmp_path
    ):
        """AC30b (MAJOR-1): a pin with the CORRECT version but a DIFFERENT
        package name (`other-engine==canonical`) must still fail, and the
        message must name the REGISTERED package (from PIN_DECLARATIONS),
        not merely echo the entry's own name. An implementation that reads
        the package name out of the pin entry itself (and only compares
        the version) is exactly the failure class of the original
        incident -- it is green on every other AC here."""
        pointer_rel = _pointer_relpath()
        pkg = _pkg_name()
        V = "3.4.5"
        repo = tvp._make_tmp_repo(tmp_path, _uniform_versions(V))
        _set_pointer_pin_raw(repo, pointer_rel, f'["other-engine=={V}"]')
        result = tvp._run(repo, "--check")
        combined = result.stdout + result.stderr
        assert result.returncode == 1, (
            f"a pin of a different package (correct version) must still "
            f"fail --check, got {result.returncode}, stdout={combined!r}"
        )
        assert f"pin is other-engine=={V}, expected {pkg}=={V}" in combined, (
            f"the failure must name the REGISTERED package ({pkg!r}), not "
            f"just accept whatever package name the entry itself carries: "
            f"{combined!r}"
        )

    def test_ac30c_multiline_pin_array_parsed_correctly_and_write_preserves_layout(
        self, tmp_path
    ):
        """AC30c (MAJOR-3 / §1x): the multiline array form (the most
        defect-prone branch of the parser -- 'accumulate until ]'). A
        correct multiline pin passes --check; an incorrect one fails with
        the normal divergence reason; --write updates it and PRESERVES the
        multiline layout byte-for-byte (not collapsed to one line)."""
        pointer_rel = _pointer_relpath()
        pkg = _pkg_name()
        V, V2 = "3.4.5", "6.6.6"

        repo_ok = tvp._make_tmp_repo(tmp_path / "ok", _uniform_versions(V))
        _set_pointer_pin_raw(repo_ok, pointer_rel, f'[\n  "{pkg}=={V}",\n]')
        result_ok = tvp._run(repo_ok, "--check")
        assert result_ok.returncode == 0, (
            f"a correct multiline pin must pass --check, got "
            f"{result_ok.returncode}, stdout={result_ok.stdout!r} "
            f"stderr={result_ok.stderr!r}"
        )

        repo_bad = tvp._make_tmp_repo(tmp_path / "bad", _uniform_versions(V))
        _set_pointer_pin_raw(repo_bad, pointer_rel, f'[\n  "{pkg}==9.9.9",\n]')
        result_bad = tvp._run(repo_bad, "--check")
        combined_bad = result_bad.stdout + result_bad.stderr
        assert result_bad.returncode == 1
        assert f"pin is {pkg}==9.9.9, expected {pkg}=={V}" in combined_bad, (
            f"an incorrect multiline pin must be reported with the normal "
            f"divergence reason: {combined_bad!r}"
        )

        repo_write = tvp._make_tmp_repo(tmp_path / "write", _uniform_versions(V))
        _set_pointer_pin_raw(repo_write, pointer_rel, f'[\n  "{pkg}=={V}",\n]')
        pointer_path = repo_write / pointer_rel
        before_text = pointer_path.read_text()
        result_write = tvp._run(repo_write, "--write", V2)
        assert result_write.returncode == 0, f"--write failed: {result_write.stderr!r}"
        after_text = pointer_path.read_text()
        expected_text = before_text.replace(
            f'version = "{V}"', f'version = "{V2}"'
        ).replace(f'"{pkg}=={V}"', f'"{pkg}=={V2}"')
        assert after_text == expected_text, (
            f"--write over a multiline pin must update the version AND "
            f"preserve the multiline layout byte-for-byte: got="
            f"{after_text!r} expected={expected_text!r}"
        )

        # Re-gate edge 1: a correct multiline pin followed by
        # [project.urls], whose values are ALSO double-quoted. All prior
        # multiline fixtures leave `dependencies` as the last construct in
        # the file, so an "accumulate until EOF, then findall by quotes"
        # parser passes every case above and only breaks here -- on the
        # first real pointer converted to multiline form, which the URLs
        # table would misparse as extra pin entries.
        repo_urls = tvp._make_tmp_repo(tmp_path / "urls", _uniform_versions(V))
        pointer_path_urls = repo_urls / pointer_rel
        pointer_path_urls.write_text(
            "[build-system]\n"
            'requires = ["setuptools>=68"]\n'
            'build-backend = "setuptools.build_meta"\n\n'
            "[project]\n"
            'name = "bytedigger-engine"\n'
            f'version = "{V}"\n'
            'description = "x"\n'
            "dependencies = [\n"
            f'  "{pkg}=={V}",\n'
            "]\n\n"
            "[project.urls]\n"
            'Homepage = "https://example.com"\n'
            'Source = "https://example.com/src"\n'
        )
        result_urls = tvp._run(repo_urls, "--check")
        assert result_urls.returncode == 0, (
            f"a correct multiline pin followed by [project.urls] (double-"
            f"quoted values) must still pass --check -- an 'accumulate "
            f"until EOF' parser would misparse the URLs table's quoted "
            f"values as extra pin entries: got {result_urls.returncode}, "
            f"stdout={result_urls.stdout!r} stderr={result_urls.stderr!r}"
        )

    def test_ac31_real_pointer_pin_equals_canonical_and_check_passes(self):
        """AC31 (real-tree invariant, MINOR-6 -- explicitly NOT a §1l
        production side-effect claim: nothing is written here, only read
        via the suite's own independent reader). This instance's incident
        is already corrected, so this is expected to hold today -- it is
        the correctness guard that stays load-bearing once GREEN adds the
        enforcement no one runs today. Plus: --check against the real
        REPO_ROOT already exits 0 (the one place in this file the new
        layer is proven green on the real tree)."""
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

        check_result = tvp._run(tvp.REPO_ROOT, "--check")
        assert check_result.returncode == 0, (
            f"--check against the real repo root must exit 0, got "
            f"{check_result.returncode}, stdout={check_result.stdout!r} "
            f"stderr={check_result.stderr!r}"
        )

    def test_ac32_pin_failure_forms_no_traceback_and_accumulates(self, tmp_path):
        """AC32 (§1n): each table-2.4 condition -> exit 1, path + reason,
        no traceback; the N=0 boundary; the §2.2 grammar fence (a
        non-string element, single-quoted entries); anchoring (a decoy
        `dependencies` in a table ABOVE [project], mirroring AC24); and
        accumulate with a companion path DERIVED from the registry."""
        pointer_rel = _pointer_relpath()
        pkg = _pkg_name()
        companion_rel = _companion_relpath({pointer_rel, tvp.CANONICAL_RELPATH})
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

        repo_b2 = tvp._make_tmp_repo(tmp_path / "b2", _uniform_versions(V))
        _set_pointer_pin_raw(repo_b2, pointer_rel, "[]")
        result_b2 = tvp._run(repo_b2, "--check")
        combined_b2 = result_b2.stdout + result_b2.stderr
        assert result_b2.returncode == 1
        assert (
            pointer_rel in combined_b2
            and "dependencies has 0 entries, expected exactly 1" in combined_b2
        ), f"the N=0 boundary must report 0-entries, not any other reason: {combined_b2!r}"
        assert "Traceback" not in combined_b2

        repo_b3 = tvp._make_tmp_repo(tmp_path / "b3", _uniform_versions(V))
        _set_pointer_pin_raw(repo_b3, pointer_rel, f'["{pkg}=={V}", 1]')
        result_b3 = tvp._run(repo_b3, "--check")
        combined_b3 = result_b3.stdout + result_b3.stderr
        assert result_b3.returncode == 1
        assert (
            pointer_rel in combined_b3
            and "dependencies is not an array of strings" in combined_b3
        ), (
            f"a non-string element among the entries must be caught by the "
            f"grammar fence, not silently ignored (giving 1 entry): {combined_b3!r}"
        )
        assert "Traceback" not in combined_b3

        repo_b4 = tvp._make_tmp_repo(tmp_path / "b4", _uniform_versions(V))
        _set_pointer_pin_raw(repo_b4, pointer_rel, f"['{pkg}=={V}']")
        result_b4 = tvp._run(repo_b4, "--check")
        combined_b4 = result_b4.stdout + result_b4.stderr
        assert result_b4.returncode == 1
        assert (
            pointer_rel in combined_b4
            and "dependencies is not an array of strings" in combined_b4
        ), (
            f"single-quoted entries are not TOML strings -- must fail "
            f"closed with the array-shape reason, not a misleading one: "
            f"{combined_b4!r}"
        )
        assert "Traceback" not in combined_b4

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
        versions_d[companion_rel] = "9.9.8"
        repo_d = tvp._make_tmp_repo(tmp_path / "d", versions_d)
        _set_pointer_pin_raw(repo_d, pointer_rel, f'["{pkg}==9.0.0"]')
        result_d = tvp._run(repo_d, "--check")
        combined_d = result_d.stdout + result_d.stderr
        assert result_d.returncode == 1
        assert (
            pointer_rel in combined_d
            and f"pin is {pkg}==9.0.0, expected {pkg}=={V}" in combined_d
        ), f"single wrong pin entry must be reported with the expected form: {combined_d!r}"
        assert companion_rel in combined_d, (
            f"accumulate must still hold -- the independent divergence at "
            f"{companion_rel} (derived from the registry) must ALSO be "
            f"reported: {combined_d!r}"
        )
        assert "Traceback" not in combined_d

        repo_e = tvp._make_tmp_repo(tmp_path / "e", _uniform_versions(V))
        (repo_e / pointer_rel).write_text(
            "[build-system]\n"
            'requires = ["setuptools>=68"]\n'
            'build-backend = "setuptools.build_meta"\n'
            f'dependencies = ["decoy-package=={V}"]\n'
            "\n"
            "[project]\n"
            'name = "bytedigger-engine"\n'
            f'version = "{V}"\n'
            'description = "x"\n'
            f'dependencies = ["{pkg}=={V}"]\n'
        )
        result_e = tvp._run(repo_e, "--check")
        combined_e = result_e.stdout + result_e.stderr
        assert result_e.returncode == 0, (
            f"the real [project].dependencies pin agrees with canonical -- "
            f"a decoy 'dependencies =' line in [build-system] (mirrors "
            f"AC24) must never be treated as the pin: got "
            f"{result_e.returncode}, stdout={combined_e!r}"
        )
        assert "decoy-package" not in combined_e

    def test_ac33_write_is_surgical_on_realistic_fixture_idempotent_and_over_diverged_pin(
        self, tmp_path
    ):
        """AC33 (MAJOR-2): the fixture is built BY HAND to resemble the
        real file -- a comment block above `dependencies` naming a
        historical version, plus a second array (`classifiers`). --write
        must apply EXACTLY two legitimate substitutions ([project].version
        and the pin's version), leaving the comment prose (which itself
        names the pre-write version as history) byte-identical. A dumb
        whole-file `text.replace(old, new)` corrupts that prose and must
        fail this assertion. Plus: --write applied twice is idempotent, and
        --write over an ALREADY-diverged pin still lands it on target."""
        pointer_rel = _pointer_relpath()
        pkg = _pkg_name()
        V, V2 = "3.4.5", "8.1.0"

        repo = tvp._make_tmp_repo(tmp_path / "a", _uniform_versions(V))
        pointer_path = repo / pointer_rel
        before_text = _build_realistic_pointer_text(
            version=V, pin_version=V, canonical_old="0.1.0", pkg=pkg
        )
        pointer_path.write_text(before_text)

        result = tvp._run(repo, "--write", V2)
        assert result.returncode == 0, f"--write failed: {result.stderr!r}"
        after_text = pointer_path.read_text()

        expected_text = before_text.replace(
            f'version = "{V}"', f'version = "{V2}"', 1
        ).replace(f'"{pkg}=={V}"', f'"{pkg}=={V2}"', 1)
        assert after_text == expected_text, (
            f"--write must apply EXACTLY the two legitimate substitutions "
            f"(project.version + pin version), preserving the comment "
            f"prose and classifiers array byte-for-byte -- a whole-file "
            f"text.replace would also corrupt the historical version named "
            f"in the comment: got={after_text!r} expected={expected_text!r}"
        )

        result2 = tvp._run(repo, "--write", V2)
        assert result2.returncode == 0, f"second --write failed: {result2.stderr!r}"
        after_text2 = pointer_path.read_text()
        assert after_text2 == after_text, (
            f"--write X.Y.Z applied twice must be idempotent -- the second "
            f"run changed the file: {after_text2!r} != {after_text!r}"
        )

        repo2 = tvp._make_tmp_repo(tmp_path / "b", _uniform_versions(V2))
        pointer_path2 = repo2 / pointer_rel
        pointer_path2.write_text(
            _build_realistic_pointer_text(
                version=V2, pin_version=V, canonical_old="0.1.0", pkg=pkg
            )
        )
        V3 = "9.1.2"
        result3 = tvp._run(repo2, "--write", V3)
        assert result3.returncode == 0, (
            f"--write over an already-diverged pin must succeed: "
            f"{result3.stderr!r}"
        )
        entries3 = _read_pin(pointer_path2)
        assert entries3 == [f"{pkg}=={V3}"], (
            f"--write over an already-diverged pin must land it on the "
            f"newly requested version: {entries3!r}"
        )

        # Re-gate edge 2: all-or-nothing on the WRITE path. A pointer with
        # NO dependencies key at all must make --write fail closed BEFORE
        # any declaration file is touched -- the pin has to be pre-resolved
        # in cmd_write's first pass, alongside every version, or a writer
        # that assumes the key exists crashes mid-second-loop and leaves
        # the tree partially rewritten. AC14/AC20 never see this: their
        # fixtures always carry a valid pin.
        repo3 = tvp._make_tmp_repo(tmp_path / "c", _uniform_versions(V))
        _remove_pointer_dependencies_key(repo3, pointer_rel)
        before_bytes = {rel: (repo3 / rel).read_bytes() for rel in tvp.DECL_RELPATHS}
        result4 = tvp._run(repo3, "--write", V2)
        assert result4.returncode == 1, (
            f"--write over a pointer with no dependencies key must fail "
            f"closed (all-or-nothing), got {result4.returncode}, "
            f"stdout={result4.stdout!r} stderr={result4.stderr!r}"
        )
        for rel in tvp.DECL_RELPATHS:
            after_bytes = (repo3 / rel).read_bytes()
            assert after_bytes == before_bytes[rel], (
                f"{rel} was mutated despite a --write failure -- all-or-"
                f"nothing violated by resolving the pin too late in "
                f"cmd_write: before={before_bytes[rel]!r} "
                f"after={after_bytes!r}"
            )

    def test_ac34_no_hardcoded_pin_or_semver_literal_in_code(self):
        """AC34 (anti-hal#1300 lint, hardened per MINOR-2/MINOR-3, and
        again per the mutation-proof gate finding): scans CODE string
        constants only (via `ast`, docstrings excluded, comments never
        appear as ast nodes at all) -- never the raw file text -- for any
        constant SEGMENT carrying (1) a `<pkg>==<digit...>` pin literal,
        (2) an `==` immediately followed by a semver-shaped value
        (`==\\s*\\d+\\.\\d+\\.\\d+`), or (3) a bare semver-shaped substring
        ANYWHERE inside the constant (`\\d+\\.\\d+\\.\\d+`, a SUBSTRING
        search, not a full-string match).

        The substring form (not `fullmatch`) is load-bearing: an f-string
        like `f"{pkg}=={canonical}"` mutated to `f"{pkg}==0.1.1"` compiles
        to a JoinedStr whose literal segment is only `"==0.1.1"` -- never
        the whole `"bytedigger-engine==0.1.1"` string and never a bare
        `"0.1.1"` constant on its own, so neither a `<pkg>==\\d` search
        keyed on the interpolated `pkg` nor a `^\\d+\\.\\d+\\.\\d+$`
        FULL-match would ever see it; a substring search over `==0.1.1`
        does. Verified against the real source: `VERSION_RE`'s pattern
        constant is the literal characters `^\\d+\\.\\d+\\.\\d+$` (escaped
        backslash-d, not actual digits), so it contains no digit substring
        and is not flagged -- the real UUT stays green.
        """
        source = tvp.SCRIPT.read_text()
        pkg = _pkg_name()
        constants = _non_docstring_string_constants(source)

        pin_offenders = [
            v for v in constants if re.search(rf"{re.escape(pkg)}==\d", v)
        ]
        assert not pin_offenders, (
            f"scripts/version_parity.py must never hardcode a version-"
            f"bearing pin literal for {pkg!r} in code (docstrings "
            f"excluded): found {pin_offenders!r}"
        )

        eq_semver_offenders = [
            v for v in constants if re.search(r"==\s*\d+\.\d+\.\d+", v)
        ]
        assert not eq_semver_offenders, (
            f"scripts/version_parity.py must never carry an '==' "
            f"immediately followed by a semver-shaped value in a code "
            f"string constant (including an f-string's own literal "
            f"segment, e.g. f\"{{pkg}}==0.1.1\") -- found "
            f"{eq_semver_offenders!r}"
        )

        semver_offenders = [v for v in constants if _BARE_SEMVER_RE.search(v)]
        assert not semver_offenders, (
            f"scripts/version_parity.py must never carry a semver-shaped "
            f"substring in ANY code string constant (e.g. a split '_PIN = "
            f'"0.1.1"\' literal later joined into a pin) -- found '
            f"{semver_offenders!r}"
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
        """AC36: every PIN_DECLARATIONS path is also in DECLARATIONS; a
        missing pointer file is named EXACTLY ONCE; and (MINOR-4) a
        pointer with NO [project] table at all is ALSO named exactly once
        (`no version key`) -- the pin-pass must be skipped whenever the
        main resolve for that path has already failed, not only on
        'missing'."""
        registry = _require_pin_registry()
        for relpath, _pkg in registry:
            assert relpath in tvp.DECL_RELPATHS, (
                f"PIN_DECLARATIONS path {relpath!r} is not registered in "
                f"DECLARATIONS -- every pin path must also be a known "
                f"declaration (spec §2.1 invariant)"
            )

        pointer_rel = _pointer_relpath()

        repo = tvp._make_tmp_repo(tmp_path / "missing", _uniform_versions("3.4.5"))
        (repo / pointer_rel).unlink()
        result = tvp._run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert combined.count(pointer_rel) == 1, (
            f"a missing pointer file must be named exactly once -- found "
            f"{combined.count(pointer_rel)} occurrences: {combined!r}"
        )

        repo2 = tvp._make_tmp_repo(tmp_path / "no-project", _uniform_versions("3.4.5"))
        (repo2 / pointer_rel).write_text(
            "[build-system]\n"
            'requires = ["setuptools>=68"]\n'
            'build-backend = "setuptools.build_meta"\n'
        )
        result2 = tvp._run(repo2, "--check")
        assert result2.returncode == 1
        combined2 = result2.stdout + result2.stderr
        assert "no version key" in combined2
        assert combined2.count(pointer_rel) == 1, (
            f"a pointer with no [project] table must be named exactly once "
            f"(the main-resolve failure) -- the pin-pass must stay silent "
            f"for a path whose main resolve already failed: found "
            f"{combined2.count(pointer_rel)} occurrences: {combined2!r}"
        )

    def test_ac37_manifests_job_runs_the_pin_test_file(self):
        """AC37 (MAJOR-4, the enforcement layer itself): the `manifests`
        job in ci.yml must execute tests/test_version_parity_pin.py (or a
        glob that covers it). Same assertion shape as the existing AC10.
        Today the job only names tests/test_version_parity.py -- red until
        GREEN edits ci.yml (§5a)."""
        ci_path = tvp.REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert ci_path.exists(), "ci.yml missing -- fixture broken"
        text = ci_path.read_text()

        block = tvp._extract_job_block(text, "manifests")
        step_chunks = re.split(r"(?m)^(?=\s*- )", block)
        pin_steps = [
            s
            for s in step_chunks
            if re.search(r"run:[\s\S]*?tests/test_version_parity_pin\.py", s)
            or re.search(r"run:[\s\S]*?tests/test_version_parity\*\.py", s)
        ]
        assert pin_steps, (
            "no step in the manifests job invokes tests/test_version_parity_pin.py "
            "(or a glob covering it) -- the new pin layer is not enforced in CI"
        )

        import yaml

        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict) and "jobs" in parsed, (
            "ci.yml does not parse as a well-formed YAML jobs document"
        )
