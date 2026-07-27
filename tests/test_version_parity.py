"""RED tests for scripts/version_parity.py (bd#99, agreement 6A92C040), v2.

The script under test does not exist yet. Per workflows.md §1q, nothing here
imports it at module import time -- every test resolves the script path (and
subprocess-invokes it, or reads its source text) lazily, inside the test
body, so collection always succeeds and failure happens at assert time.

Repo root is the parent of this tests/ directory (this file lives at
<repo>/tests/test_version_parity.py, NOT under engine_py/).

Every subprocess invocation passes --root EXPLICITLY (v1 gate MAJOR-6):
resolution must never be inferred from subprocess cwd. No test invokes
--write against the real worktree; --write always targets a tmp fixture
root built under tmp_path (AC15 covers the real-tree-untouched guarantee).
"""
from __future__ import annotations

import ast
import json
import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "version_parity.py"

# Neutral, empty, non-repo cwd for every subprocess invocation by default.
# No test needs the ambient cwd to be the repo root; tests that legitimately
# target the real tree pass --root REPO_ROOT explicitly. This prevents a
# GREEN that honours --root on the check path but drops it on the write
# path from silently rewriting the real five declarations via inherited cwd.
# Created ONCE at module scope (not from the per-test tmp_path fixture),
# genuinely empty (contains none of the five declaration relpaths, so a
# CWD-resolving script fails loudly instead of partially resolving), and
# NOT nested inside any fixture root built by _make_tmp_repo. realpath()
# strips the /var/folders symlink macOS puts under mkdtemp() output, so
# path-equality assertions elsewhere never mismatch on it.
NEUTRAL_CWD = Path(os.path.realpath(tempfile.mkdtemp(prefix="version_parity_neutral_")))

def _source_registry() -> list:
    """(path, kind) pairs read out of the UUT's own `DECLARATIONS` literal.

    Derived, never transcribed. A hand-copied list of declaration relpaths is
    what bd#13 was: the repo grew a sixth declaration
    (`packaging/pypi-pointer/pyproject.toml`, added when the PyPI pointer
    package was tracked in-repo and 0.1.1 was cut) and every fixture repo in
    this suite silently stopped covering the full set, so seven tests failed
    with `packaging/pypi-pointer/pyproject.toml: missing` while the tool itself
    was correct on the real tree. Reading the registry from the source removes
    the transcription entirely rather than re-transcribing it one longer.

    Parsed with `ast`, not imported: this suite's standing rule is that the UUT
    is never imported or mocked, only invoked as a subprocess. `ast.parse`
    executes nothing. It is also deliberately NOT read via
    `--list-declarations` -- AC16 asserts that the shipped
    `--list-declarations` output agrees with this independently-parsed source
    registry, and deriving both sides from the same subprocess would make that
    comparison circular.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    kind_consts: dict = {}
    decls_node = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id.startswith("KIND_") and isinstance(node.value, ast.Constant):
            kind_consts[target.id] = node.value.value
        elif target.id == "DECLARATIONS":
            decls_node = node.value

    assert decls_node is not None, (
        f"{SCRIPT} declares no module-level DECLARATIONS list -- this suite "
        f"derives every fixture from it"
    )
    assert isinstance(decls_node, (ast.List, ast.Tuple)), (
        f"DECLARATIONS in {SCRIPT} is not a list/tuple literal: "
        f"{type(decls_node).__name__}"
    )

    pairs = []
    for element in decls_node.elts:
        assert isinstance(element, (ast.Tuple, ast.List)) and len(element.elts) == 2, (
            f"DECLARATIONS entry is not a 2-element (path, kind) literal: "
            f"{ast.dump(element)}"
        )
        path_node, kind_node = element.elts
        assert isinstance(path_node, ast.Constant) and isinstance(path_node.value, str), (
            f"DECLARATIONS path is not a string literal: {ast.dump(path_node)}"
        )
        if isinstance(kind_node, ast.Name):
            assert kind_node.id in kind_consts, (
                f"DECLARATIONS references unknown kind constant {kind_node.id!r}"
            )
            kind = kind_consts[kind_node.id]
        else:
            assert isinstance(kind_node, ast.Constant) and isinstance(
                kind_node.value, str
            ), f"DECLARATIONS kind is neither a KIND_* name nor a string: {ast.dump(kind_node)}"
            kind = kind_node.value
        pairs.append((path_node.value, kind))

    assert pairs, f"DECLARATIONS in {SCRIPT} is empty"
    return pairs


SOURCE_REGISTRY = _source_registry()
DECL_RELPATHS = [path for path, _kind in SOURCE_REGISTRY]
DECL_KINDS = {path: kind for path, kind in SOURCE_REGISTRY}

ALL_AGREE_VERSIONS = {p: "0.1.1" for p in DECL_RELPATHS}


def _run(root, *args, cwd=NEUTRAL_CWD):
    """Invoke the script as a real subprocess with an EXPLICIT --root.

    Never imports/mocks the UUT. `root` is always given -- resolution is
    never inferred from subprocess cwd (v1 gate MAJOR-6). `cwd` defaults to
    a NEUTRAL, empty, non-repo directory for EVERY invocation -- never the
    real repo root -- so any test targeting the real tree must pass
    `root=REPO_ROOT` explicitly; ambient cwd can never smuggle in the real
    worktree as a fallback resolution target.
    """
    if "--write" in args:
        assert Path(root).resolve() != REPO_ROOT, (
            "--write must never target the real repo root -- got REPO_ROOT "
            "as `root`; a __file__-rooted GREEN could rewrite the real "
            "manifests before any assertion catches it"
        )
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(cwd),
    )


def _parse_project_version(text: str) -> str | None:
    """Canonical parse rule, pinned identically to spec §2.2/[G-10/T3]:
    find the [project] table header, then the first
    ^version = "..." line before the next ^[ table header."""
    in_project = False
    for line in text.splitlines():
        if re.match(r"^\[project\]\s*$", line):
            in_project = True
            continue
        if in_project:
            if re.match(r"^\[", line):
                break
            m = re.match(r'^version\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    return None


KIND_TOML_PROJECT_VERSION = "toml-project-version"
KIND_JSON_FLAT = "json-flat"
KIND_JSON_NESTED = "json-nested"

#: The closed `kind` vocabulary of spec §2.4. A registry entry whose kind is
#: absent here is a shape this suite does not know how to build or read, and
#: every helper below fails loudly rather than skipping it.
KNOWN_KINDS = frozenset(
    {KIND_TOML_PROJECT_VERSION, KIND_JSON_FLAT, KIND_JSON_NESTED}
)


def _read_declared_version(path: Path, kind: str) -> str | None:
    """Read the version out of `path` the way its registered `kind` stores it.

    Independent of the UUT: this is the suite's own reader, so a test asserting
    that `--write` landed is not asking the writer to confirm its own work.
    """
    assert kind in KNOWN_KINDS, (
        f"unknown declaration kind {kind!r} for {path} -- teach "
        f"_read_declared_version and _write_declaration the new shape "
        f"(KNOWN_KINDS is the closed vocabulary of spec §2.4)"
    )
    if kind == KIND_TOML_PROJECT_VERSION:
        return _parse_project_version(path.read_text())
    data = json.loads(path.read_text())
    if kind == KIND_JSON_FLAT:
        return data.get("version")
    return data["plugins"][0].get("version")


def _canonical_version(repo_root: Path) -> str:
    """Read the real canonical declaration directly (not via the script)."""
    text = (repo_root / "engine_py" / "pyproject.toml").read_text()
    version = _parse_project_version(text)
    assert version, "engine_py/pyproject.toml has no [project].version -- fixture broken"
    return version


def _make_tmp_repo(tmp_path: Path, versions: dict) -> Path:
    """Build a minimal copy of the five declaration files under tmp_path,
    with caller-supplied versions, so --write tests never touch the real tree."""
    root = tmp_path / "repo"
    (root / "engine_py").mkdir(parents=True)
    (root / "npm").mkdir(parents=True)
    (root / ".claude-plugin").mkdir(parents=True)

    (root / "engine_py" / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "bytedigger-engine"\n'
        f'version = "{versions["engine_py/pyproject.toml"]}"\n'
        'description = "x"\n'
    )
    (root / "package.json").write_text(
        json.dumps(
            {"name": "bytedigger", "version": versions["package.json"], "private": True},
            indent=2,
        )
        + "\n"
    )
    (root / "npm" / "package.json").write_text(
        json.dumps(
            {"name": "bytedigger", "version": versions["npm/package.json"]}, indent=2
        )
        + "\n"
    )
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {"name": "bytedigger", "version": versions[".claude-plugin/plugin.json"]},
            indent=2,
        )
        + "\n"
    )
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "bytedigger",
                "plugins": [
                    {
                        "name": "bytedigger",
                        "version": versions[".claude-plugin/marketplace.json"],
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )
    return root


EXCLUDE_DIR_NAMES = {
    ".git", "node_modules", "dist", "build", "__pycache__",
    ".venv", "venv", ".tox", ".pytest_cache", ".bytedigger",
}
EXCLUDE_REL_DIRS = {"engine_py/tests", "tests"}
EXCLUDE_REL_FILES = {"build-metadata.json"}


def _bounded_semver_scan(root: Path) -> set:
    """Test-time bounded repo walk (spec §2.6/[G-3b]): every *.json and
    pyproject.toml under root carrying a semver 'version' value, excluding
    the declared directories. Structural (parses JSON/TOML), never a
    substring match over file text."""
    semver_re = re.compile(r"^\d+\.\d+\.\d+$")
    found = set()

    def _has_semver(obj) -> bool:
        if isinstance(obj, dict):
            v = obj.get("version")
            if isinstance(v, str) and semver_re.match(v):
                return True
            return any(_has_semver(x) for x in obj.values())
        if isinstance(obj, list):
            return any(_has_semver(x) for x in obj)
        return False

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(
            part in EXCLUDE_DIR_NAMES or part.endswith(".egg-info")
            for part in rel.parts[:-1]
        ):
            continue
        rel_str = str(rel)
        if rel_str in EXCLUDE_REL_FILES:
            continue
        if any(rel_str == d or rel_str.startswith(d + "/") for d in EXCLUDE_REL_DIRS):
            continue
        if path.name == "pyproject.toml":
            if _parse_project_version(path.read_text(errors="ignore")):
                found.add(rel_str)
            continue
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
            if _has_semver(data):
                found.add(rel_str)
    return found


def _extract_job_block(text: str, job_name: str) -> str:
    m = re.search(rf"(?m)^  {re.escape(job_name)}:\s*\n", text)
    assert m, f"no top-level '{job_name}:' job in ci.yml"
    start = m.end()
    m2 = re.search(r"(?m)^  \S", text[start:])
    end = start + m2.start() if m2 else len(text)
    return text[start:end]


def _runs_on_labels(runs_on) -> set:
    """Every label a `runs-on:` value schedules against, in any of the
    three shapes GitHub accepts: a bare string, a list of labels, or a
    `{group, labels}` mapping. An unrecognised shape yields no labels
    rather than raising -- the YAML parse in AC10 is what guards
    well-formedness; this helper only answers "which labels"."""
    if isinstance(runs_on, str):
        return {runs_on}
    if isinstance(runs_on, list):
        return {x for x in runs_on if isinstance(x, str)}
    if isinstance(runs_on, dict):
        labels = runs_on.get("labels", [])
        labels = [labels] if isinstance(labels, str) else labels
        group = runs_on.get("group")
        return {x for x in [*labels, group] if isinstance(x, str)}
    return set()


class TestVersionParity:
    def test_ac1_check_passes_when_all_five_agree_names_count(self, tmp_path):
        """AC1: --check exits 0 when all five agree, success line names count 5."""
        repo = _make_tmp_repo(tmp_path, ALL_AGREE_VERSIONS)
        result = _run(repo, "--check")
        assert result.returncode == 0, (
            f"expected exit 0 on agreeing declarations, got {result.returncode}, "
            f"stderr={result.stderr!r}"
        )
        assert re.search(r"\b5\b", result.stdout) and "declaration" in result.stdout.lower(), (
            f"success line must name the count 5: stdout={result.stdout!r}"
        )

    def test_ac2_check_fails_when_any_declaration_diverges(self, tmp_path):
        """AC2: --check exits 1 when any declaration diverges from canonical."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"
        repo = _make_tmp_repo(tmp_path, versions)
        result = _run(repo, "--check")
        assert result.returncode == 1, (
            f"expected exit 1 on a diverging declaration, got {result.returncode}"
        )

    def test_ac3_check_lists_every_diverging_file_not_only_first(self, tmp_path):
        """AC3: --check output must name every diverging file, not stop at the first."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"
        versions[".claude-plugin/marketplace.json"] = "0.1.0"
        repo = _make_tmp_repo(tmp_path, versions)
        result = _run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert ".claude-plugin/plugin.json" in combined
        assert ".claude-plugin/marketplace.json" in combined, (
            f"second divergent file not named -- only first reported?: {combined!r}"
        )

    def test_ac3b_divergence_line_matches_frozen_format(self, tmp_path):
        """AC3b: the divergence line format is frozen as
        'path: found X, expected Y' -- assert the FORMAT via regex, not
        three independent substring checks, since the spec pins this as a
        literal frozen shape."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"
        repo = _make_tmp_repo(tmp_path, versions)
        result = _run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        m = re.search(r"(?m)^\S+: found \S+, expected \S+$", combined)
        assert m, (
            f"no line matches the frozen 'path: found X, expected Y' format: {combined!r}"
        )
        assert ".claude-plugin/plugin.json" in m.group(0), (
            f"the matched divergence line must name the diverging file: {m.group(0)!r}"
        )
        assert "0.1.0" in m.group(0) and "0.1.1" in m.group(0), (
            f"the matched divergence line must carry both found and expected values: "
            f"{m.group(0)!r}"
        )

    def test_ac4_write_sets_all_five_individually_then_check_passes(self, tmp_path):
        """AC4: --write X.Y.Z sets ALL FIVE files (asserted individually);
        a following --check then exits 0."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"
        versions[".claude-plugin/marketplace.json"] = "0.1.0"
        repo = _make_tmp_repo(tmp_path, versions)

        write_result = _run(repo, "--write", "9.9.9")
        assert write_result.returncode == 0, (
            f"--write should succeed, got {write_result.returncode}, "
            f"stderr={write_result.stderr!r}"
        )

        pyproject_text = (repo / "engine_py" / "pyproject.toml").read_text()
        assert _parse_project_version(pyproject_text) == "9.9.9", "pyproject not written"

        pkg = json.loads((repo / "package.json").read_text())
        assert pkg["version"] == "9.9.9", "package.json not written"

        npm_pkg = json.loads((repo / "npm" / "package.json").read_text())
        assert npm_pkg["version"] == "9.9.9", "npm/package.json not written"

        plugin = json.loads((repo / ".claude-plugin" / "plugin.json").read_text())
        assert plugin["version"] == "9.9.9", "plugin.json not written"

        marketplace = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text())
        assert marketplace["plugins"][0]["version"] == "9.9.9", "marketplace.json not written"

        check_result = _run(repo, "--check")
        assert check_result.returncode == 0, (
            f"--check after --write should exit 0, got {check_result.returncode}, "
            f"stdout={check_result.stdout!r} stderr={check_result.stderr!r}"
        )

    def test_ac5_no_tomllib_source_is_py39_parseable_and_path_precision(self, tmp_path):
        """AC5: script imports no tomllib, its source parses under
        feature_version=(3,9) and carries `from __future__ import
        annotations`, AND --check reports divergence with path PRECISION
        (not a bare rc-in-{0,1} check, which any script naming every file
        divergent would also satisfy).

        Path-precision is asserted against a FIXTURE with exactly two
        deliberately stale declarations -- NOT the real repo (AC9 requires
        those two real files to be fixed to canonical by GREEN, so a
        real-repo assertion here would become permanently unsatisfiable
        together with AC9 once the instance fix lands). This property is
        deterministic and survives every future bump.
        """
        assert SCRIPT.exists(), "scripts/version_parity.py does not exist yet"
        source = SCRIPT.read_text()
        assert not re.search(r"(?m)^\s*import\s+tomllib\b", source)
        assert not re.search(r"(?m)^\s*from\s+tomllib\s+import\b", source)
        assert "from __future__ import annotations" in source, (
            "script must declare `from __future__ import annotations` for "
            "py3.9-clean syntax"
        )
        try:
            ast.parse(source, feature_version=(3, 9))
        except SyntaxError as e:
            raise AssertionError(f"script does not parse under py3.9 grammar: {e}")

        # Path precision on a controlled fixture: exactly two stale.
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"
        versions[".claude-plugin/marketplace.json"] = "0.1.0"
        repo = _make_tmp_repo(tmp_path, versions)
        result = _run(repo, "--check")
        combined = result.stdout + result.stderr
        assert ".claude-plugin/plugin.json" in combined, (
            f"deliberately-stale plugin.json must be named as divergent: {combined!r}"
        )
        assert ".claude-plugin/marketplace.json" in combined, (
            f"deliberately-stale marketplace.json must be named as divergent: {combined!r}"
        )
        assert "package.json" not in combined.replace("npm/package.json", ""), (
            f"correct package.json must NOT be reported as divergent: {combined!r}"
        )
        assert "npm/package.json" not in combined, (
            f"correct npm/package.json must NOT be reported as divergent: {combined!r}"
        )
        # NOTE: does not assert the canonical path is absent from output --
        # the spec does not forbid naming it as context in a success/summary
        # line, so that would over-constrain GREEN.

        # Post-instance-fix property: --check against the REAL repo exits 0
        # once GREEN has aligned all five (fails today: script absent).
        real_result = _run(REPO_ROOT, "--check")
        assert real_result.returncode == 0, (
            f"--check against the real repo (post instance-fix) must exit 0, "
            f"got {real_result.returncode}, stderr={real_result.stderr!r}"
        )

    def test_ac6_missing_declaration_file_exits_1_naming_file(self, tmp_path):
        """AC6: a missing declaration file -> exit 1 naming the file (fail loud)."""
        repo = _make_tmp_repo(tmp_path, ALL_AGREE_VERSIONS)
        (repo / "npm" / "package.json").unlink()
        result = _run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "npm/package.json" in combined

    def test_ac7a_version_key_absent_exits_1_naming_file_and_reason(self, tmp_path):
        """AC7a: 'version' key absent (json-flat) -> exit 1, path + 'no version key'."""
        repo = _make_tmp_repo(tmp_path, ALL_AGREE_VERSIONS)
        pkg_path = repo / "package.json"
        data = json.loads(pkg_path.read_text())
        del data["version"]
        pkg_path.write_text(json.dumps(data, indent=2) + "\n")

        result = _run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert re.search(r"(?m)^package\.json\b", combined) and "no version key" in combined, (
            f"error must name root package.json (not npm/package.json) + "
            f"'no version key': {combined!r}"
        )
        assert "Traceback" not in combined

    def test_ac7b_non_semver_value_exits_1_naming_file_and_value(self, tmp_path):
        """AC7b: non-semver value -> exit 1, path + the offending value."""
        repo = _make_tmp_repo(tmp_path, ALL_AGREE_VERSIONS)
        pkg_path = repo / "package.json"
        data = json.loads(pkg_path.read_text())
        data["version"] = "not-a-version"
        pkg_path.write_text(json.dumps(data, indent=2) + "\n")

        result = _run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert re.search(r"(?m)^package\.json\b", combined), (
            f"error must name root package.json (not npm/package.json): {combined!r}"
        )
        assert "not-a-version" in combined
        assert "Traceback" not in combined

    def test_ac7c_nested_marketplace_error_cases(self, tmp_path):
        """AC7c: nested cases -- plugins absent / plugins empty / plugins[0]
        without version -- each exits 1 naming file + reason, never a raw
        traceback."""
        # Case 1: "plugins" key absent.
        repo1 = _make_tmp_repo(tmp_path / "c1", ALL_AGREE_VERSIONS)
        mkt_path1 = repo1 / ".claude-plugin" / "marketplace.json"
        data1 = json.loads(mkt_path1.read_text())
        del data1["plugins"]
        mkt_path1.write_text(json.dumps(data1, indent=2) + "\n")
        result1 = _run(repo1, "--check")
        combined1 = result1.stdout + result1.stderr
        assert result1.returncode == 1
        assert ".claude-plugin/marketplace.json" in combined1 and "no plugins key" in combined1, (
            f"missing 'no plugins key' reason: {combined1!r}"
        )
        assert "Traceback" not in combined1

        # Case 2: "plugins" present but empty.
        repo2 = _make_tmp_repo(tmp_path / "c2", ALL_AGREE_VERSIONS)
        mkt_path2 = repo2 / ".claude-plugin" / "marketplace.json"
        data2 = json.loads(mkt_path2.read_text())
        data2["plugins"] = []
        mkt_path2.write_text(json.dumps(data2, indent=2) + "\n")
        result2 = _run(repo2, "--check")
        combined2 = result2.stdout + result2.stderr
        assert result2.returncode == 1
        assert ".claude-plugin/marketplace.json" in combined2 and "plugins is empty" in combined2, (
            f"missing 'plugins is empty' reason: {combined2!r}"
        )
        assert "Traceback" not in combined2

        # Case 3: plugins[0] lacks "version".
        repo3 = _make_tmp_repo(tmp_path / "c3", ALL_AGREE_VERSIONS)
        mkt_path3 = repo3 / ".claude-plugin" / "marketplace.json"
        data3 = json.loads(mkt_path3.read_text())
        del data3["plugins"][0]["version"]
        mkt_path3.write_text(json.dumps(data3, indent=2) + "\n")
        result3 = _run(repo3, "--check")
        combined3 = result3.stdout + result3.stderr
        assert result3.returncode == 1
        assert (
            ".claude-plugin/marketplace.json" in combined3
            and "plugins[0] has no version" in combined3
        ), f"missing 'plugins[0] has no version' reason: {combined3!r}"
        assert "Traceback" not in combined3

    def test_ac7d_two_simultaneous_errors_both_reported_in_one_run(self, tmp_path):
        """AC7d: two independent errors present at once -> BOTH are reported
        in the same run (accumulate, not abort-on-first)."""
        repo = _make_tmp_repo(tmp_path, ALL_AGREE_VERSIONS)
        pkg_path = repo / "package.json"
        pkg_data = json.loads(pkg_path.read_text())
        del pkg_data["version"]
        pkg_path.write_text(json.dumps(pkg_data, indent=2) + "\n")

        plugin_path = repo / ".claude-plugin" / "plugin.json"
        plugin_data = json.loads(plugin_path.read_text())
        plugin_data["version"] = "not-a-version"
        plugin_path.write_text(json.dumps(plugin_data, indent=2) + "\n")

        result = _run(repo, "--check")
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert re.search(r"(?m)^package\.json\b", combined) and "no version key" in combined, (
            f"first error (root package.json, not npm/package.json) missing: {combined!r}"
        )
        assert ".claude-plugin/plugin.json" in combined and "not-a-version" in combined, (
            f"second error missing -- run aborted on the first?: {combined!r}"
        )

    def test_ac8_registry_completeness_structural_via_list_declarations(self):
        """AC8: every semver-carrying file found by the bounded repo walk
        appears in --list-declarations JSON output (structural comparison,
        not a substring test over script source)."""
        assert SCRIPT.exists(), "scripts/version_parity.py does not exist yet"
        result = _run(REPO_ROOT, "--list-declarations")
        assert result.returncode == 0, (
            f"--list-declarations failed: {result.stderr!r}"
        )
        registry = json.loads(result.stdout)
        registered_paths = {entry["path"] for entry in registry}

        found = _bounded_semver_scan(REPO_ROOT)
        missing = found - registered_paths
        assert not missing, (
            f"manifest(s) carrying a semver version not in --list-declarations "
            f"registry: {missing}"
        )

    def test_ac9_all_five_repo_declarations_read_canonical_version(self):
        """AC9 (production side-effect): all five REAL declarations must read
        the same version as canonical engine_py/pyproject.toml. Derived from
        the canonical file, not hardcoded, so it survives future bumps."""
        canonical = _canonical_version(REPO_ROOT)

        pkg = json.loads((REPO_ROOT / "package.json").read_text())
        assert pkg["version"] == canonical

        npm_pkg = json.loads((REPO_ROOT / "npm" / "package.json").read_text())
        assert npm_pkg["version"] == canonical

        plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
        assert plugin["version"] == canonical, (
            f".claude-plugin/plugin.json version {plugin['version']!r} != "
            f"canonical {canonical!r}"
        )

        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert marketplace["plugins"][0]["version"] == canonical, (
            f".claude-plugin/marketplace.json plugins[0].version "
            f"{marketplace['plugins'][0]['version']!r} != canonical {canonical!r}"
        )

    def test_ac10_ci_yml_manifests_job_shape(self):
        """AC10: ci.yml has a 'manifests:' job whose block invokes
        version_parity.py --check on a run: line, has no working-directory:,
        and has a step invoking tests/test_version_parity.py; ci.yml still
        parses as YAML."""
        ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert ci_path.exists(), "ci.yml missing -- fixture broken"
        text = ci_path.read_text()

        block = _extract_job_block(text, "manifests")

        assert "working-directory:" not in block, (
            "manifests job must not set working-directory (script lives at repo root)"
        )

        step_chunks = re.split(r"(?m)^(?=\s*- )", block)
        check_steps = [
            s for s in step_chunks
            if re.search(r"run:[\s\S]*?version_parity\.py\s+--check", s)
        ]
        assert check_steps, (
            "no step in the manifests job invokes version_parity.py --check "
            "on a run: line"
        )
        test_steps = [
            s for s in step_chunks
            if re.search(r"run:[\s\S]*?tests/test_version_parity\.py", s)
        ]
        assert test_steps, (
            "no step in the manifests job invokes tests/test_version_parity.py"
        )

        # Spec now installs pyyaml in CI, so a silent skip on ImportError is
        # a dead declared check -- require the import to succeed.
        import yaml
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict) and "jobs" in parsed, (
            "ci.yml does not parse as a well-formed YAML jobs document"
        )

    def test_ac11_write_reads_and_writes_nested_marketplace_path(self, tmp_path):
        """AC11: nested marketplace.json plugins[0].version is read AND
        written, not just top-level keys."""
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))
        result = _run(repo, "--write", "3.4.5")
        assert result.returncode == 0, f"--write failed: {result.stderr!r}"
        marketplace = json.loads(
            (repo / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert marketplace["plugins"][0]["version"] == "3.4.5"

    def test_ac21_no_job_uses_a_self_hosted_runner(self):
        """AC21: no job in ANY workflow schedules onto a self-hosted runner.

        This used to pin the manifests job to the literal two-label form
        `[self-hosted, bytedigger]`, to keep it off the `heavy` pool that
        the pytest job used. That pinned the STATE rather than the
        PROPERTY: the moment the repo went public the labels were the
        wrong answer and the assertion fired on a correct change instead
        of a regression.

        The property that actually matters on a public repo is the
        opposite of what was pinned. GitHub-hosted runners are free and
        unmetered here, so self-hosted buys nothing -- while a
        self-hosted runner on a public repo executes pull-request code
        from any contributor on a personal machine. Assert that, for
        every job in every workflow, so neither a new job nor a new
        workflow file can reintroduce it. (The original label-pin covered
        one job; three in ci.yml and three more in clean-room.yml carried
        the labels -- and clean-room's were the ones that mattered, since
        that workflow hands the job a docker daemon.)
        """
        import yaml
        wf_dir = REPO_ROOT / ".github" / "workflows"
        workflows = sorted(
            p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml")
        )
        assert workflows, f"no workflow files found under {wf_dir}"
        offenders = {
            f"{p.name}:{name}": spec.get("runs-on")
            for p in workflows
            for name, spec in (yaml.safe_load(p.read_text())["jobs"]).items()
            if "self-hosted" in _runs_on_labels(spec.get("runs-on"))
        }
        assert not offenders, (
            f"self-hosted runners are not allowed on a public repo -- "
            f"PR code would run on a personal machine. Offending jobs: "
            f"{offenders}"
        )

    def test_ac22_check_and_write_together_exits_2(self, tmp_path):
        """AC22: --check and --write passed together is a usage error ->
        exit 2 (argparse mutually-exclusive-group behaviour).

        A missing script also makes the interpreter itself exit 2 ("can't
        open file"), which would satisfy a bare returncode==2 assertion
        without argparse ever running -- so SCRIPT.exists() is asserted
        first, matching the idiom in AC5/AC8/AC16.
        """
        assert SCRIPT.exists(), "scripts/version_parity.py does not exist yet"
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))
        result = _run(repo, "--check", "--write", "1.2.3")
        assert result.returncode == 2, (
            f"--check and --write together must exit 2 (usage error), "
            f"got {result.returncode}"
        )

    def test_ac12_write_is_surgical_preserves_inline_array_and_indent(self, tmp_path):
        """AC12: --write preserves every byte except the version value --
        including an inline JSON array and non-2-space indent. A
        json.load/json.dump round-trip implementation MUST fail this test."""
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))

        # Hand-craft npm/package.json with 4-space indent + an inline array,
        # mirroring the real file's keywords/files inline arrays.
        npm_path = repo / "npm" / "package.json"
        before_text = (
            "{\n"
            '    "name": "bytedigger",\n'
            '    "version": "0.1.1",\n'
            '    "keywords": ["tdd", "agents"],\n'
            '    "files": ["bin/", "README.md"]\n'
            "}\n"
        )
        npm_path.write_text(before_text)

        result = _run(repo, "--write", "7.8.9")
        assert result.returncode == 0, f"--write failed: {result.stderr!r}"

        after_text = npm_path.read_text()
        expected_text = before_text.replace('"version": "0.1.1"', '"version": "7.8.9"')
        assert after_text == expected_text, (
            "--write must change only the version value byte-for-byte; "
            f"got:\n{after_text!r}\nexpected:\n{expected_text!r}"
        )

    def test_ac13_contributing_md_no_longer_says_runs_two_jobs(self):
        """AC13: CONTRIBUTING.md no longer contains 'runs two jobs', contains
        'version_parity.py --write' (Releasing section), and names
        engine_py/pyproject.toml as the canonical source."""
        contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text()
        assert "runs two jobs" not in contributing, (
            "CONTRIBUTING.md still claims 'runs two jobs' -- now false with "
            "the manifests job added"
        )
        assert "version_parity.py --write" in contributing, (
            "CONTRIBUTING.md must document the bump command "
            "'version_parity.py --write'"
        )
        assert "engine_py/pyproject.toml" in contributing, (
            "CONTRIBUTING.md's Releasing section must name "
            "engine_py/pyproject.toml as the canonical source"
        )

    def test_ac14_write_invalid_version_arg_rejects_and_writes_nothing(self, tmp_path):
        """AC14: --write not-a-version exits 1, names the rejected argument,
        and leaves ALL FIVE files byte-identical (all-or-nothing)."""
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))
        rel_paths = [
            "engine_py/pyproject.toml",
            "package.json",
            "npm/package.json",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
        ]
        before_bytes = {rel: (repo / rel).read_bytes() for rel in rel_paths}

        result = _run(repo, "--write", "not-a-version")
        assert result.returncode == 1, (
            f"expected exit 1 for invalid --write argument, got {result.returncode}"
        )
        combined = result.stdout + result.stderr
        assert "not-a-version" in combined, (
            f"error must name the rejected argument: {combined!r}"
        )

        for rel in rel_paths:
            after = (repo / rel).read_bytes()
            assert after == before_bytes[rel], (
                f"{rel} was mutated despite invalid --write argument (all-or-nothing violated)"
            )

    def test_ac15_root_flag_resolves_declarations_against_root_real_tree_untouched(
        self, tmp_path
    ):
        """AC15: --root PATH resolves declarations against PATH -- NOT the
        subprocess cwd. Invoked with cwd set to a THIRD, empty directory
        (neither REPO_ROOT nor the fixture root), so a script that silently
        falls back to CWD resolution has nowhere valid to resolve against
        and must fail this test. Using --root on a fixture tree writes the
        fixture and leaves the REAL worktree byte-identical."""
        real_rel_paths = DECL_RELPATHS
        real_before = {rel: (REPO_ROOT / rel).read_bytes() for rel in real_rel_paths}

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))
        write_result = _run(repo, "--write", "1.2.3", cwd=elsewhere)
        assert write_result.returncode == 0, (
            f"--write against the fixture root should succeed regardless of "
            f"an unrelated ambient cwd, got {write_result.returncode}, "
            f"stderr={write_result.stderr!r}"
        )
        fixture_pyproject = (repo / "engine_py" / "pyproject.toml").read_text()
        assert _parse_project_version(fixture_pyproject) == "1.2.3", (
            "--root did not resolve declarations against the fixture path "
            "(script may be resolving against cwd instead)"
        )

        for rel in real_rel_paths:
            after = (REPO_ROOT / rel).read_bytes()
            assert after == real_before[rel], (
                f"real worktree file {rel} was mutated by a --root-scoped --write "
                "-- root resolution leaked to the real tree"
            )

    def test_zero_argument_default_behaves_as_check(self, tmp_path):
        """MINOR: zero mode-flag arguments (only --root given) behaves as
        --check against that root, not a no-op or a --write."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"
        repo = _make_tmp_repo(tmp_path, versions)

        default_result = _run(repo)
        explicit_check_result = _run(repo, "--check")
        assert default_result.returncode == explicit_check_result.returncode == 1, (
            f"default (no mode flag) must behave as --check: default rc="
            f"{default_result.returncode}, --check rc={explicit_check_result.returncode}"
        )

    def test_ac16_list_declarations_emits_exactly_the_five_entries(self, tmp_path):
        """AC16: --list-declarations exits 0, emits valid JSON, and contains
        EXACTLY the five {path, kind} pairs, against the spec's closed
        `kind` vocabulary (toml-project-version, json-flat, json-nested) --
        not a superset, not a subset, not a hardcoded stub list, and not
        merely a non-empty-kind check."""
        expected_kinds = {
            "engine_py/pyproject.toml": "toml-project-version",
            "package.json": "json-flat",
            "npm/package.json": "json-flat",
            ".claude-plugin/plugin.json": "json-flat",
            ".claude-plugin/marketplace.json": "json-nested",
        }
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))
        result = _run(repo, "--list-declarations")
        assert result.returncode == 0, (
            f"--list-declarations must exit 0, got {result.returncode}, "
            f"stderr={result.stderr!r}"
        )
        try:
            registry = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"--list-declarations stdout is not valid JSON: {result.stdout!r} ({e})"
            )
        assert isinstance(registry, list) and len(registry) == 5, (
            f"expected exactly 5 declaration entries, got: {registry!r}"
        )
        actual_pairs = {entry.get("path"): entry.get("kind") for entry in registry}
        assert actual_pairs == expected_kinds, (
            f"registry {{path: kind}} pairs {actual_pairs!r} != expected {expected_kinds!r}"
        )

    def test_ac17_missing_file_and_divergence_share_exit_1(self, tmp_path):
        """AC17: a MISSING declaration and a DIVERGING declaration in the
        same run must still yield a single determinate exit code 1 (not an
        argparse/usage 2, not an uncaught-exception code), naming both
        problems and never a raw traceback."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"  # diverges
        repo = _make_tmp_repo(tmp_path, versions)
        (repo / "npm" / "package.json").unlink()  # missing

        result = _run(repo, "--check")
        assert result.returncode == 1, (
            f"expected exactly exit 1 for mixed missing+divergence, got "
            f"{result.returncode}, stderr={result.stderr!r}"
        )
        combined = result.stdout + result.stderr
        assert "npm/package.json" in combined, (
            f"missing-file problem not named: {combined!r}"
        )
        assert ".claude-plugin/plugin.json" in combined, (
            f"diverging-file problem not named: {combined!r}"
        )
        assert "Traceback" not in combined

    def test_ac18_malformed_json_exits_1_naming_file_unreadable_and_others_visited(
        self, tmp_path
    ):
        """AC18: a declaration with malformed JSON -> exit 1, path named,
        reason 'unreadable', no traceback, and accumulate still holds --
        another declaration diverging at the same time is ALSO reported."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"  # also diverges
        repo = _make_tmp_repo(tmp_path, versions)
        pkg_path = repo / "package.json"
        pkg_path.write_text("{not valid json,,,")

        result = _run(repo, "--check")
        assert result.returncode == 1, (
            f"expected exit 1 for malformed JSON, got {result.returncode}"
        )
        combined = result.stdout + result.stderr
        assert re.search(r"(?m)^package\.json\b", combined) and "unreadable" in combined, (
            f"malformed-JSON error must name root package.json (not "
            f"npm/package.json) + 'unreadable': {combined!r}"
        )
        assert "Traceback" not in combined
        assert ".claude-plugin/plugin.json" in combined, (
            f"accumulate must still hold -- the other diverging file must "
            f"also be reported: {combined!r}"
        )

    def test_ac19_canonical_missing_aborts_second_problem_not_reported(self, tmp_path):
        """AC19: canonical (engine_py/pyproject.toml) missing or unparsable
        -> exit 1 naming the canonical path, ABORTING rather than
        accumulating -- the one control path that deliberately diverges
        from the accumulate rule (there is no expected value to compare
        the rest against).

        Abort vs. accumulate are indistinguishable from outside if the
        broken canonical is the ONLY problem present, so a SECOND,
        independent problem is staged at the same time: npm/package.json
        is ALSO deleted. If the script accumulated (wrongly), it would
        visit npm/package.json too and report it missing. The absence of
        that second problem from the output is the only observable proof
        of abort-before-accumulate.
        """
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))
        (repo / "engine_py" / "pyproject.toml").unlink()
        (repo / "npm" / "package.json").unlink()  # second, independent problem

        result = _run(repo, "--check")
        assert result.returncode == 1, (
            f"expected exit 1 when canonical is missing, got {result.returncode}"
        )
        combined = result.stdout + result.stderr
        assert "engine_py/pyproject.toml" in combined, (
            f"canonical-missing error must name the canonical path: {combined!r}"
        )
        assert "npm/package.json" not in combined, (
            "the second, independent problem must NOT be reported -- the "
            f"run must abort before reaching it (accumulate would report it): {combined!r}"
        )
        assert "Traceback" not in combined

    def test_ac20_write_with_unresolvable_declaration_writes_nothing(self, tmp_path):
        """AC20: --write where a declaration fails to resolve (e.g. one
        file missing) -> exit 1, ALL FIVE fixture files left byte-identical
        (snapshot bytes before, compare after -- same rigor as AC14, which
        covers only the bad-argument half of all-or-nothing)."""
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))
        rel_paths = [
            "engine_py/pyproject.toml",
            "package.json",
            "npm/package.json",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
        ]
        (repo / "npm" / "package.json").unlink()
        # Snapshot existence/bytes for all five up front -- the deliberately
        # missing one's "before" state is simply "does not exist".
        before_exists = {rel: (repo / rel).exists() for rel in rel_paths}
        before_bytes = {
            rel: (repo / rel).read_bytes() for rel in rel_paths if before_exists[rel]
        }

        result = _run(repo, "--write", "5.5.5")
        assert result.returncode == 1, (
            f"expected exit 1 when a declaration fails to resolve, got "
            f"{result.returncode}, stderr={result.stderr!r}"
        )
        combined = result.stdout + result.stderr
        assert "npm/package.json" in combined, (
            f"the unresolvable declaration's path must be named in the "
            f"output (§2.5 -- every message names the manifest path): {combined!r}"
        )
        assert "Traceback" not in combined

        for rel in rel_paths:
            after_exists = (repo / rel).exists()
            assert after_exists == before_exists[rel], (
                f"{rel} existence changed despite an unresolvable declaration "
                f"(all-or-nothing violated): before={before_exists[rel]} "
                f"after={after_exists}"
            )
            if after_exists:
                after = (repo / rel).read_bytes()
                assert after == before_bytes[rel], (
                    f"{rel} was mutated despite an unresolvable declaration "
                    "(all-or-nothing violated)"
                )

    def test_ac23_shape_errors_no_traceback_and_accumulate(self, tmp_path):
        """AC23: §2.5 enumerates errors by KEY PRESENCE only -- type/shape
        mismatches (wrong Python type for an existing key) must still be
        fail-loud, not an uncaught exception surfacing a script-line
        traceback. Three shape failures, each with a second, independent
        diverging declaration staged to prove accumulate still holds."""
        # (a) "plugins" present but a dict, not a list.
        versions_a = dict(ALL_AGREE_VERSIONS)
        versions_a["package.json"] = "0.1.0"  # second, independent problem
        repo_a = _make_tmp_repo(tmp_path / "a", versions_a)
        mkt_a = repo_a / ".claude-plugin" / "marketplace.json"
        data_a = json.loads(mkt_a.read_text())
        data_a["plugins"] = {"name": "bytedigger", "version": "0.1.1"}
        mkt_a.write_text(json.dumps(data_a, indent=2) + "\n")
        result_a = _run(repo_a, "--check")
        combined_a = result_a.stdout + result_a.stderr
        assert result_a.returncode == 1
        assert ".claude-plugin/marketplace.json" in combined_a
        assert "Traceback" not in combined_a
        assert re.search(r"(?m)^package\.json\b", combined_a), (
            f"accumulate must still hold -- second problem (root package.json, "
            f"not npm/package.json) missing: {combined_a!r}"
        )

        # (b) plugins[0] present but not a dict (a string).
        versions_b = dict(ALL_AGREE_VERSIONS)
        versions_b["package.json"] = "0.1.0"
        repo_b = _make_tmp_repo(tmp_path / "b", versions_b)
        mkt_b = repo_b / ".claude-plugin" / "marketplace.json"
        data_b = json.loads(mkt_b.read_text())
        data_b["plugins"] = ["not-a-plugin-object"]
        mkt_b.write_text(json.dumps(data_b, indent=2) + "\n")
        result_b = _run(repo_b, "--check")
        combined_b = result_b.stdout + result_b.stderr
        assert result_b.returncode == 1
        assert ".claude-plugin/marketplace.json" in combined_b
        assert "Traceback" not in combined_b
        assert re.search(r"(?m)^package\.json\b", combined_b), (
            f"accumulate must still hold -- second problem (root package.json, "
            f"not npm/package.json) missing: {combined_b!r}"
        )

        # (c) "version" present but a non-string (the int 1).
        versions_c = dict(ALL_AGREE_VERSIONS)
        versions_c["package.json"] = "0.1.0"
        repo_c = _make_tmp_repo(tmp_path / "c", versions_c)
        mkt_c = repo_c / ".claude-plugin" / "marketplace.json"
        data_c = json.loads(mkt_c.read_text())
        data_c["plugins"][0]["version"] = 1
        mkt_c.write_text(json.dumps(data_c, indent=2) + "\n")
        result_c = _run(repo_c, "--check")
        combined_c = result_c.stdout + result_c.stderr
        assert result_c.returncode == 1
        assert ".claude-plugin/marketplace.json" in combined_c
        assert "Traceback" not in combined_c
        assert re.search(r"(?m)^package\.json\b", combined_c), (
            f"accumulate must still hold -- second problem (root package.json, "
            f"not npm/package.json) missing: {combined_c!r}"
        )

    def test_ac24_project_anchored_parse_ignores_decoy_version_before_project_table(
        self, tmp_path
    ):
        """AC24: the canonical parse rule is [project]-table-anchored, not
        "first ^version = anywhere in the file". A decoy `version =` line
        inside [build-system], BEFORE [project], must be ignored; the real
        [project].version must be treated as canonical."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"  # stale, forces a divergence line
        repo = _make_tmp_repo(tmp_path, versions)
        pyproject_path = repo / "engine_py" / "pyproject.toml"
        pyproject_path.write_text(
            '[build-system]\n'
            'requires = ["setuptools>=68"]\n'
            'build-backend = "setuptools.build_meta"\n'
            'version = "0.0.9"\n'  # decoy -- wrong table, must not be canonical
            "\n"
            "[project]\n"
            'name = "bytedigger-engine"\n'
            'version = "0.1.1"\n'
            'description = "x"\n'
        )
        result = _run(repo, "--check")
        combined = result.stdout + result.stderr
        assert result.returncode == 1
        assert re.search(r"expected 0\.1\.1\b", combined), (
            f"[project].version (0.1.1) must be treated as canonical: {combined!r}"
        )
        assert "0.0.9" not in combined, (
            f"decoy [build-system] version must never surface as canonical: {combined!r}"
        )

    def test_ac25_root_default_resolves_against_cwd_when_omitted(self, tmp_path):
        """AC25: with NO --root given, the script defaults to Path.cwd().
        This is the one test that legitimately overrides cwd away from
        NEUTRAL_CWD -- pointed at a fixture root, never at REPO_ROOT."""
        versions = dict(ALL_AGREE_VERSIONS)
        versions[".claude-plugin/plugin.json"] = "0.1.0"
        repo = _make_tmp_repo(tmp_path, versions)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo),
        )
        assert result.returncode == 1, (
            f"expected exit 1 (fixture root has a diverging declaration) "
            f"with --root omitted and cwd set to the fixture root, got "
            f"{result.returncode}"
        )
        combined = result.stdout + result.stderr
        assert ".claude-plugin/plugin.json" in combined

    def test_ac26_write_surgical_across_pyproject_and_marketplace(self, tmp_path):
        """AC26: --write's surgical-write contract is uniform across ALL
        THREE formats (§2.4), not just npm/package.json (AC12). Extends
        byte-equality to engine_py/pyproject.toml (comment header +
        [build-system] block preserved) and .claude-plugin/marketplace.json
        (nested write)."""
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))

        pyproject_path = repo / "engine_py" / "pyproject.toml"
        pyproject_before = (
            "# OSS packaging comment header\n"
            '[build-system]\n'
            'requires = ["setuptools>=68"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "bytedigger-engine"\n'
            'version = "0.1.1"\n'
            'description = "x"\n'
        )
        pyproject_path.write_text(pyproject_before)

        marketplace_path = repo / ".claude-plugin" / "marketplace.json"
        marketplace_before = marketplace_path.read_text()

        result = _run(repo, "--write", "2.3.4")
        assert result.returncode == 0, f"--write failed: {result.stderr!r}"

        pyproject_after = pyproject_path.read_text()
        pyproject_expected = pyproject_before.replace(
            'version = "0.1.1"', 'version = "2.3.4"'
        )
        assert pyproject_after == pyproject_expected, (
            "pyproject.toml must change only the version value byte-for-byte "
            f"-- comment header + [build-system] block must survive: "
            f"got={pyproject_after!r}"
        )

        marketplace_after = marketplace_path.read_text()
        marketplace_expected = marketplace_before.replace('"0.1.1"', '"2.3.4"')
        assert marketplace_after == marketplace_expected, (
            f"marketplace.json nested write must be byte-surgical: got={marketplace_after!r}"
        )

    # -- bd#13: the fixture builder and the registry may never drift apart ----

    def test_ac27_fixture_builder_covers_every_registered_declaration(self, tmp_path):
        """AC27 (bd#13, the durable guard): `_make_tmp_repo` must materialise a
        file for EVERY declaration the UUT registers, and for no other path.

        This is the invariant bd#13 violated. The builder hand-wrote five files
        while the registry grew to six, so every fixture repo was missing one
        declaration and the tool -- correctly -- refused it with
        `packaging/pypi-pointer/pyproject.toml: missing`. Seven tests went red
        for a reason that had nothing to do with what any of them asserted.

        Asserted as set equality against the registry rather than as a count,
        so a seventh declaration cannot reproduce the same wedge: the builder
        either covers it or this test names exactly what it missed.
        """
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))

        registered = set(DECL_RELPATHS)
        materialised = {
            rel for rel in registered if (repo / rel).is_file()
        }
        assert materialised == registered, (
            f"_make_tmp_repo must create every registered declaration; "
            f"missing={sorted(registered - materialised)}"
        )

        # And the fixture is a faithful subject: the tool accepts it.
        result = _run(repo, "--check")
        assert result.returncode == 0, (
            f"a fixture covering every declaration must pass --check, got "
            f"{result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

    def test_ac28_registry_is_closed_and_every_entry_is_actually_written(
        self, tmp_path
    ):
        """AC28 (bd#13): the declaration registry is CLOSED over what --write
        touches -- asserted as a property, with no count anywhere.

        Replaces the count half of the old AC16 ("exactly five entries"), which
        pinned a STATE: it had to be re-tuned by hand every time the repo
        legitimately gained a declaration, and re-tuning it was the only thing
        it ever measured. The property that actually matters is that the
        registry and the set of files the writer rewrites are the same set --
        no entry declared and then ignored by --write, and no file rewritten
        without being declared.
        """
        repo = _make_tmp_repo(tmp_path, dict(ALL_AGREE_VERSIONS))

        before = {
            rel: (repo / rel).read_bytes()
            for rel in DECL_RELPATHS
            if (repo / rel).is_file()
        }
        assert set(before) == set(DECL_RELPATHS), (
            f"fixture is missing declarations, cannot measure the write set: "
            f"missing={sorted(set(DECL_RELPATHS) - set(before))}"
        )

        result = _run(repo, "--write", "9.9.9")
        assert result.returncode == 0, (
            f"--write over a complete fixture must exit 0, got "
            f"{result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

        changed = {
            rel for rel, was in before.items() if (repo / rel).read_bytes() != was
        }
        assert changed == set(DECL_RELPATHS), (
            f"--write must rewrite exactly the registered declarations; "
            f"declared-but-untouched={sorted(set(DECL_RELPATHS) - changed)}"
        )

        # Every rewritten file now reads back the requested version, by its
        # registered `kind` -- so `kind` is load-bearing, not decorative.
        for rel in DECL_RELPATHS:
            assert _read_declared_version(repo / rel, DECL_KINDS[rel]) == "9.9.9", (
                f"{rel} (kind={DECL_KINDS[rel]}) does not read back the written "
                f"version"
            )
