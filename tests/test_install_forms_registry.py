"""RED tests for bd#21 v3 (spec 2026-07-28, gate round 1: 10 MAJOR/11 MINOR) --
a single canonical install-forms module (`scripts/install_forms.py`) plus an
enumerable platform registry (`CARRIERS`), so a brand-new platform carrying
ANY install-command form (allowed or denied) fails the build instead of
silently going unregistered.

Per workflows.md §1q, `scripts/install_forms.py` is never imported at module
scope -- every test loads it lazily, inside its own body, via
`importlib.util.spec_from_file_location` (never `sys.path` mutation, never
`from conftest import ...`), so collection always succeeds and failure on
its absence happens at assert time with a readable message. `import yaml`
at module scope is the one deliberate hard dependency (AC13 needs it to
parse ci.yml; its absence must be a loud collection error, never a skip).

Per §4.0, AC1-AC9/AC12 have no reason of their own to fail today besides the
canonical module's absence (this file loads it first in every test body);
AC10 (reads tests/test_npm_install_hint.py), AC11 (reads
engine_py/lib/dbos_setup.py) and AC13 (reads ci.yml) each have their own,
independent red-today reason.

Nothing here reimplements R1/R2, the domain scan, or the waiver predicate --
those are read out of `scripts/install_forms.py` (once GREEN creates it),
never transcribed from the spec.

Repo root is the parent of this tests/ directory.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_FORMS_RELPATH = "scripts/install_forms.py"
INSTALL_FORMS_PATH = REPO_ROOT / INSTALL_FORMS_RELPATH
PACKAGE_META_PATH = REPO_ROOT / "engine_py" / "package_meta.py"
ENGINE_PY_DIR = REPO_ROOT / "engine_py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
THIS_FILE_RELPATH = "tests/test_install_forms_registry.py"

# spec §2.1 -- ALL EIGHT bd#17 definitions that must live exactly once, in
# scripts/install_forms.py (AC1, MINOR-3 of the gate: v2 pinned only 3/8).
CANONICAL_DEFINITION_NAMES = (
    "ALLOWED_INSTALLER_FORMS",
    "DENIED_INSTALLER_FORMS",
    "installer_form_is_allowed",
    "find_install_commands",
    "fenced_code_text",
    "extract_package_name",
    "is_vcs_or_url_target",
    "is_package_name_target",
)


# ---------------------------------------------------------------------------
# Module loading (no sys.path mutation, no conftest import -- §1q).
# ---------------------------------------------------------------------------
def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot build an import spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_install_forms_module():
    assert INSTALL_FORMS_PATH.is_file(), (
        f"{INSTALL_FORMS_RELPATH} does not exist yet -- the canonical source of "
        f"ALLOWED_INSTALLER_FORMS / DENIED_INSTALLER_FORMS / CARRIERS / "
        f"scan_domain has not been created (spec §2.1/§2.2)"
    )
    return _load_module_from_path("install_forms_uut", INSTALL_FORMS_PATH)


def _load_package_meta_module():
    assert PACKAGE_META_PATH.is_file(), f"{PACKAGE_META_PATH} missing -- fixture broken"
    return _load_module_from_path("package_meta_uut", PACKAGE_META_PATH)


# ---------------------------------------------------------------------------
# AC1 -- single-source check: a bounded repo walk for module-level
# definitions of the eight names that are supposed to live in exactly one
# place (spec §2.1).
# ---------------------------------------------------------------------------
_EXCLUDE_DIR_NAMES = {
    ".git", "node_modules", "dist", "build", "__pycache__",
    ".venv", "venv", ".tox", ".pytest_cache", ".bytedigger",
}


def _bounded_repo_python_files() -> list[Path]:
    out = []
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if any(
            part in _EXCLUDE_DIR_NAMES or part.endswith(".egg-info")
            for part in rel.parts[:-1]
        ):
            continue
        out.append(path)
    return out


def _module_level_definition_sites(name: str) -> list[str]:
    """Every relpath where `name` is defined as a module-level assignment
    or a module-level function, found via `ast` (not text/regex, so a
    reformatted or reindented duplicate cannot hide from this check)."""
    hits = []
    for path in _bounded_repo_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                hits.append(str(path.relative_to(REPO_ROOT)))
                break
            if isinstance(node, ast.Assign):
                if any(
                    isinstance(t, ast.Name) and t.id == name for t in node.targets
                ):
                    hits.append(str(path.relative_to(REPO_ROOT)))
                    break
    return hits


# ---------------------------------------------------------------------------
# Carrier-line extraction, dispatched by KIND (spec §2.2/§2.3). R1 kinds
# (executed-node, markdown) are scoped to the text actually printed/
# rendered; R2 kinds (python-literal, python-runtime) use the carrier's RAW
# PHYSICAL source lines -- the same unit `scan_domain` uses (MAJOR-6 of the
# gate: v2 checked R2 over AST literals in one AC and raw lines in another).
# ---------------------------------------------------------------------------
_CONSOLE_ERROR_RE = re.compile(r'console\.error\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _js_console_error_strings(text: str) -> list[str]:
    """Every string literal argument of a `console.error("...")` call in a
    node source file -- the actual text this platform prints (KIND_EXECUTED_NODE),
    read statically since these calls carry only literal arguments."""
    return [
        m.group(1).encode().decode("unicode_escape")
        for m in _CONSOLE_ERROR_RE.finditer(text)
    ]


def _carrier_scan_lines(install_forms, relpath: str, kind: str) -> list[str]:
    path = REPO_ROOT / relpath
    assert path.is_file(), f"registered carrier {relpath!r} does not exist"
    text = path.read_text(encoding="utf-8")
    if kind == install_forms.KIND_MARKDOWN:
        return install_forms.fenced_code_text(text).splitlines()
    if kind == install_forms.KIND_EXECUTED_NODE:
        return _js_console_error_strings(text)
    if kind in (install_forms.KIND_PYTHON_LITERAL, install_forms.KIND_PYTHON_RUNTIME):
        return text.splitlines()
    raise AssertionError(f"unknown carrier kind {kind!r} for {relpath}")


def _make_ac5_domain_fixture(tmp_path: Path) -> Path:
    """The exact five-file fixture spec §3/AC5 demands (MAJOR-10 of the
    gate): a hit outside any registry with a DENIED form, a hit outside any
    registry with an ALLOWED form (proves MAJOR-4: allowed-form platforms
    must be caught too), a markdown hit outside engine_py/** (proves the
    domain is not narrowed to engine_py/**), a hit with a VALID waiver
    (must not count), and a hit with an EMPTY-reason waiver (must still
    count -- proves the waiver check isn't all-eating)."""
    root = tmp_path / "ac5_domain_fixture"
    (root / "engine_py").mkdir(parents=True)
    (root / "packaging" / "newthing").mkdir(parents=True)
    (root / "npm").mkdir(parents=True)

    (root / "engine_py" / "new_backend.py").write_text(
        'HINT = "pip install foo-bar to get going"\n', encoding="utf-8"
    )
    (root / "packaging" / "newthing" / "README.md").write_text(
        "Quickstart: run `pip install newthing` to get going.\n", encoding="utf-8"
    )
    (root / "npm" / "new_hint.md").write_text(
        "Quickstart: run `pipx install something-new` to get going.\n",
        encoding="utf-8",
    )
    (root / "engine_py" / "waived.py").write_text(
        "# historical note: pip install waived-thing"
        "  # install-forms-ok: historical example, not a live hint\n",
        encoding="utf-8",
    )
    (root / "engine_py" / "waived_empty.py").write_text(
        "# historical note: pip install waived-thing  # install-forms-ok:\n",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# AC7 -- real `doctor.check_optional_deps()` execution (spec §1l/§2.8): a
# subprocess with engine_py placed on PYTHONPATH, never an import at module
# scope of this file.
# ---------------------------------------------------------------------------
def _run_doctor_check_optional_deps() -> dict:
    code = (
        "import sys, json\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import doctor\n"
        "print(json.dumps(doctor.check_optional_deps()))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(ENGINE_PY_DIR)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"doctor.check_optional_deps() subprocess failed: rc={result.returncode}, "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# AC13 helpers -- same form as tests/test_npm_install_hint.py::test_ac9,
# re-pointed at THIS file.
# ---------------------------------------------------------------------------
def _strip_comment_lines(run_str: str) -> str:
    return "\n".join(
        line for line in run_str.splitlines() if not line.strip().startswith("#")
    )


def _effective_step_cwd(workflow_data: dict, job: dict, step: dict):
    workflow_cwd = ((workflow_data.get("defaults") or {}).get("run") or {}).get(
        "working-directory"
    )
    job_cwd = ((job.get("defaults") or {}).get("run") or {}).get("working-directory")
    if job_cwd is None:
        job_cwd = workflow_cwd
    step_cwd = step.get("working-directory")
    if step_cwd is None:
        step_cwd = job_cwd
    return step_cwd


def _run_covers_this_file(run_str: str) -> bool:
    cleaned = _strip_comment_lines(run_str)
    if not re.search(r"pytest\b", cleaned):
        return False
    covers = (
        THIS_FILE_RELPATH in cleaned
        or bool(re.search(r"(?<![\w./])tests/(?:\s|$)", cleaned))
    )
    if not covers:
        return False
    excluded = re.search(
        r"--(?:ignore|deselect)[= ]\S*test_install_forms_registry\.py", cleaned
    )
    return not excluded


def _step_can_fail_the_job(step: dict, job: dict) -> tuple[bool, str]:
    run_str = _strip_comment_lines(step.get("run", ""))
    if re.search(r"\|\|\s*true\b", run_str):
        return False, "run contains `|| true`"
    if step.get("continue-on-error") is True:
        return False, "step has continue-on-error: true"
    if job.get("continue-on-error") is True:
        return False, "job has continue-on-error: true"
    if step.get("if") not in (None, ""):
        return False, f"step has a narrowing if: {step.get('if')!r}"
    if job.get("if") not in (None, ""):
        return False, f"job has a narrowing if: {job.get('if')!r}"
    return True, ""


class TestInstallFormsRegistry:
    # -- AC1 -------------------------------------------------------------
    def test_ac1_all_eight_canonical_definitions_live_exactly_once(self):
        """AC1 (§1g, MINOR-3 of the gate): all eight bd#17 definitions must
        be defined exactly once in the repo, at scripts/install_forms.py.
        Red today: scripts/install_forms.py does not exist, so the module
        load fails first."""
        install_forms = _load_install_forms_module()
        for name in CANONICAL_DEFINITION_NAMES:
            assert hasattr(install_forms, name), (
                f"{INSTALL_FORMS_RELPATH} has no top-level {name!r}"
            )
            sites = _module_level_definition_sites(name)
            assert sites == [INSTALL_FORMS_RELPATH], (
                f"{name!r} must be defined exactly once, in "
                f"{INSTALL_FORMS_RELPATH!r}; found module-level definition(s) "
                f"at {sites!r}"
            )

    # -- AC2 ---------------------------------------------------------------
    def test_ac2_every_r1_carrier_in_the_registry_uses_an_allowed_form(self):
        """AC2: for every CARRIERS entry of kind KIND_EXECUTED_NODE or
        KIND_MARKDOWN, every command the R1 grammar finds uses an allowed
        installer form."""
        install_forms = _load_install_forms_module()
        r1_kinds = (install_forms.KIND_EXECUTED_NODE, install_forms.KIND_MARKDOWN)
        checked_any = False
        bad = []
        for relpath, kind in install_forms.CARRIERS:
            if kind not in r1_kinds:
                continue
            checked_any = True
            text = "\n".join(_carrier_scan_lines(install_forms, relpath, kind))
            for cmd in install_forms.find_install_commands(text):
                if not install_forms.installer_form_is_allowed(cmd["exe"]):
                    bad.append((relpath, cmd))
        assert checked_any, "no KIND_EXECUTED_NODE/KIND_MARKDOWN entry in CARRIERS"
        assert not bad, f"R1 violation(s) in registered carrier(s): {bad!r}"

    # -- AC3 ---------------------------------------------------------------
    def test_ac3_every_r2_carrier_in_the_registry_carries_no_denied_form(self):
        """AC3: for every CARRIERS entry of kind KIND_PYTHON_LITERAL or
        KIND_PYTHON_RUNTIME, `contains_denied_form` is False on every RAW
        PHYSICAL LINE of the carrier (spec §2.3 MAJOR-6: the same unit
        AC5/scan_domain uses -- not an AST-literal scan, so it also catches
        the dbos_setup.py:193 comment form)."""
        install_forms = _load_install_forms_module()
        r2_kinds = (install_forms.KIND_PYTHON_LITERAL, install_forms.KIND_PYTHON_RUNTIME)
        checked_any = False
        hits = []
        for relpath, kind in install_forms.CARRIERS:
            if kind not in r2_kinds:
                continue
            checked_any = True
            for line in _carrier_scan_lines(install_forms, relpath, kind):
                if install_forms.contains_denied_form(line):
                    hits.append((relpath, line))
        assert checked_any, "no KIND_PYTHON_LITERAL/KIND_PYTHON_RUNTIME entry in CARRIERS"
        assert not hits, f"R2 violation(s) in registered carrier(s): {hits!r}"

    # -- AC4 -----------------------------------------------------------
    def test_ac4_every_registered_carrier_is_load_bearing(self):
        """AC4: every CARRIERS entry must carry >=1 install command by
        `contains_any_install_form` -- NOT by the R1 grammar (spec MAJOR-3:
        R1 is start-anchored and, after a correct GREEN, finds nothing on
        agent_sdk.py:52 because the command there is embedded mid-sentence
        -- exactly why R2/contains_any_install_form exists)."""
        install_forms = _load_install_forms_module()
        assert install_forms.CARRIERS, "CARRIERS is empty"
        dead = []
        for relpath, kind in install_forms.CARRIERS:
            lines = _carrier_scan_lines(install_forms, relpath, kind)
            if not any(install_forms.contains_any_install_form(line) for line in lines):
                dead.append(relpath)
        assert not dead, f"registered carrier(s) with zero install commands: {dead!r}"

    # -- AC5 -----------------------------------------------------------
    def test_ac5_scan_domain_flags_every_hit_outside_carriers_without_a_valid_waiver(
        self, tmp_path
    ):
        """AC5 -- the heart of the lot: `scan_domain` returns ALL hits of
        `contains_any_install_form` (allowed forms included -- MAJOR-4: a
        brand-new platform written in an already-allowed form must still be
        registered). Every hit must lie either in CARRIERS or on a line
        with a valid waiver. Five-file fixture (MAJOR-10) closes stub
        `scan_domain -> []`, domain narrowed to engine_py/**, an all-eating
        waiver, and an empty-reason waiver all at once."""
        install_forms = _load_install_forms_module()
        fixture_root = _make_ac5_domain_fixture(tmp_path)

        violations = install_forms.scan_domain(fixture_root)
        for v in violations:
            assert {"path", "line", "text"} <= set(v), (
                f"scan_domain hit missing contract keys {{'path','line','text'}}: {v!r}"
            )
            assert not v["path"].startswith("/") and "\\" not in v["path"], (
                f"scan_domain path must be POSIX-relative: {v['path']!r}"
            )

        # No test-side waiver filtering (spec §2.2, gate MAJOR-9б): scan_domain
        # itself must call line_has_valid_waiver and drop only the validly
        # waived hit. A scan that ignores waivers entirely would still pass
        # this test if filtering happened here instead -- that vacuum is
        # exactly what the gate rejected v2 for.
        carrier_paths = {relpath for relpath, _kind in install_forms.CARRIERS}
        hit_paths = {v["path"] for v in violations if v["path"] not in carrier_paths}
        assert hit_paths == {
            "engine_py/new_backend.py",
            "packaging/newthing/README.md",
            "npm/new_hint.md",
            "engine_py/waived_empty.py",
        }, (
            f"scan_domain must itself drop only the validly-waived hit "
            f"(engine_py/waived.py) and flag the rest; got {sorted(hit_paths)}"
        )

    # -- AC6 -----------------------------------------------------------
    def test_ac6_waiver_marker_with_empty_reason_is_not_a_valid_waiver(self):
        """AC6: `# install-forms-ok:` with no reason after the colon must
        NOT count as a waiver -- fed synthetically in both directions."""
        install_forms = _load_install_forms_module()
        assert install_forms.line_has_valid_waiver(
            "x = 1  # install-forms-ok: prose about pip install layout, not a hint"
        ) is True, "a waiver marker WITH a reason must be accepted"
        for empty_variant in (
            "x = 1  # install-forms-ok:",
            "x = 1  # install-forms-ok:   ",
            "x = 1",
        ):
            assert install_forms.line_has_valid_waiver(empty_variant) is False, (
                f"{empty_variant!r} must NOT count as a valid waiver"
            )

    # -- AC7 -----------------------------------------------------------
    def test_ac7_doctor_optional_deps_detail_uses_an_allowed_form_naming_dist_name(
        self,
    ):
        """AC7 (§1l/MINOR-10): the doctor subprocess is run BEFORE the
        canonical module is loaded, so the harness is provably alive even
        before scripts/install_forms.py exists. Its printed `detail` must
        name an allowed installer form and PACKAGE_DIST_NAME, transcribing
        neither."""
        check = _run_doctor_check_optional_deps()
        assert check.get("name") == "optional-deps", f"unexpected check: {check!r}"
        detail = check.get("detail", "")
        assert "pydantic_ai" in detail, (
            f"expected the pydantic_ai probe to be missing (measured, spec "
            f"§2.8) so its install hint is present in detail: {detail!r}"
        )

        install_forms = _load_install_forms_module()
        package_meta = _load_package_meta_module()

        assert not install_forms.contains_denied_form(detail), (
            f"doctor's printed detail still names a denied installer form: {detail!r}"
        )
        allowed_hit = next(
            (
                form
                for form in install_forms.ALLOWED_INSTALLER_FORMS
                if re.search(re.escape(form) + r"\s+install\b", detail)
            ),
            None,
        )
        assert allowed_hit is not None, (
            f"doctor's printed detail names no allowed installer form: {detail!r}"
        )
        assert package_meta.PACKAGE_DIST_NAME in detail, (
            f"doctor's printed detail must name {package_meta.PACKAGE_DIST_NAME!r}: "
            f"{detail!r}"
        )

    # -- AC8 -----------------------------------------------------------
    def test_ac8_r2_catches_the_embedded_form_that_r1_cannot(self):
        """AC8 (discriminating): on `"Install it via: pip install
        claude-agent-sdk"`, R1 (start-anchored) must find NOTHING and R2
        (embedded substring, left-context-aware) must find the denied form."""
        install_forms = _load_install_forms_module()
        synthetic = "Install it via: pip install claude-agent-sdk"

        r1_hits = install_forms.find_install_commands(synthetic)
        assert not r1_hits, (
            f"R1 (start-anchored grammar) must find NOTHING in embedded prose "
            f"{synthetic!r}, got {r1_hits!r}"
        )
        assert install_forms.contains_denied_form(synthetic) is True, (
            f"R2 (embedded substring search) must catch {synthetic!r}"
        )

    # -- AC9 -----------------------------------------------------------
    def test_ac9_r2_round_trips_against_the_dictionary_in_both_directions(self):
        """AC9 (NEW, MAJOR-1 of the gate): `contains_denied_form` must be
        False for every ALLOWED_INSTALLER_FORMS form embedded as
        `f"{form} install pkg"` (v2's bare `\\b(pip|pip3)\\s+install\\b` was
        a substring of `python3 -m pip install` / `uv pip install` and
        would have wrongly flagged its own allowed forms), True for every
        DENIED_INSTALLER_FORMS form, and True for the three named decoys."""
        install_forms = _load_install_forms_module()

        for form in install_forms.ALLOWED_INSTALLER_FORMS:
            line = f"{form} install pkg"
            assert install_forms.contains_denied_form(line) is False, (
                f"R2 must NOT flag an allowed form with correct left context: {line!r}"
            )

        for form in install_forms.DENIED_INSTALLER_FORMS:
            line = f"{form} install pkg"
            assert install_forms.contains_denied_form(line) is True, (
                f"R2 must flag a denied form: {line!r}"
            )

        for decoy in (
            "sudo pip install pkg",
            "Install it via: pip install X",
            "PIP INSTALL X",
        ):
            assert install_forms.contains_denied_form(decoy) is True, (
                f"R2 must flag decoy {decoy!r}"
            )

    # -- AC10 ----------------------------------------------------------
    def test_ac10_bd17_test_file_imports_the_canonical_definitions(self):
        """AC10: tests/test_npm_install_hint.py must import the canonical
        definitions from scripts.install_forms rather than defining its
        own -- the behavioural half (bd#17's 10 ACs staying green) is
        checked by the orchestrator's full-suite delta, not by this test.
        Red today for its own reason (§4.0): no such import exists yet."""
        text = (REPO_ROOT / "tests" / "test_npm_install_hint.py").read_text(
            encoding="utf-8"
        )
        imports_canonical = bool(
            re.search(r"(?m)^\s*from\s+scripts\.install_forms\s+import\b", text)
            or re.search(r"(?m)^\s*from\s+scripts\s+import\s+install_forms\b", text)
            or re.search(r"(?m)^\s*import\s+scripts\.install_forms\b", text)
        )
        assert imports_canonical, (
            "tests/test_npm_install_hint.py must import the canonical "
            "definitions from scripts.install_forms (spec §2.1) instead of "
            "defining its own copies"
        )

    # -- AC11 ----------------------------------------------------------
    def test_ac11_dbos_setup_carries_no_installer_form_and_is_not_registered(self):
        """AC11 (NEW, postcondition of §2.7): once dbos_setup.py's command
        is removed (the extra it names does not exist), the file must carry
        no installer form at all AND must be dropped from CARRIERS -- else
        AC4 ('every entry is alive') would redden after a correct GREEN.
        Red today for its own reason (§4.0): dbos_setup.py:422 still says
        `pip install with the [dbos] extra` and :193 still comments the
        same, independent of whether the canonical module exists."""
        install_forms = _load_install_forms_module()
        dbos_path = REPO_ROOT / "engine_py" / "lib" / "dbos_setup.py"
        assert dbos_path.is_file(), f"{dbos_path} missing -- fixture broken"

        hits = [
            (i + 1, line)
            for i, line in enumerate(dbos_path.read_text(encoding="utf-8").splitlines())
            if install_forms.contains_any_install_form(line)
        ]
        assert not hits, (
            f"engine_py/lib/dbos_setup.py must carry no installer form at all: {hits!r}"
        )

        carrier_paths = {relpath for relpath, _kind in install_forms.CARRIERS}
        assert "engine_py/lib/dbos_setup.py" not in carrier_paths, (
            "engine_py/lib/dbos_setup.py must not be registered in CARRIERS once "
            "its command is removed (spec §2.7)"
        )

    # -- AC12 ----------------------------------------------------------
    def test_ac12_every_measured_fixing_platform_is_registered(self):
        """AC12 (NEW, gate edge case 5): every platform in spec §0's table
        with decision "чиню" must be present in CARRIERS -- otherwise the
        registry could be shrunk to two entries and AC2/AC3/AC4/AC5 would
        all stay green."""
        install_forms = _load_install_forms_module()
        carrier_paths = {relpath for relpath, _kind in install_forms.CARRIERS}
        expected_fixing_platforms = {
            "packaging/pypi-pointer/README.md",
            "engine_py/package_meta.py",
            "engine_py/llm_subprocess.py",
            "engine_py/lib/reference_backends/agent_sdk.py",
            "engine_py/README.md",
        }
        missing = expected_fixing_platforms - carrier_paths
        assert not missing, (
            f"platform(s) measured in spec §0 with decision 'чиню' must be "
            f"registered in CARRIERS: missing={sorted(missing)}"
        )

    # -- AC13 ----------------------------------------------------------
    def test_ac13_some_root_scoped_ci_step_can_fail_on_this_file_and_kind_count_is_four(
        self,
    ):
        """AC13 (same form as tests/test_npm_install_hint.py::test_ac9,
        plus MINOR-7): a job's `run:` string alone does not prove coverage
        -- the effective working directory must resolve to repo root, and
        the covering step must be structurally able to fail its job (no
        `|| true`, no continue-on-error, no narrowing if:). Plus exactly 4
        distinct KIND_* values. Red today for its own reason (§4.0): no job
        in ci.yml names tests/test_install_forms_registry.py."""
        text = CI_YML.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert isinstance(data, dict) and isinstance(data.get("jobs"), dict), (
            f"{CI_YML} does not parse as a well-formed YAML jobs document"
        )

        considered = []
        covering_hits = []
        for job_name, job in data["jobs"].items():
            if not isinstance(job, dict):
                continue
            run_steps = [
                s
                for s in (job.get("steps") or [])
                if isinstance(s, dict) and isinstance(s.get("run"), str)
            ]
            if not run_steps:
                considered.append((job_name, "no run: steps"))
                continue

            reasons = []
            qualified = False
            for step in run_steps:
                cwd = _effective_step_cwd(data, job, step)
                if cwd not in (None, ".", ""):
                    reasons.append(f"step cwd={cwd!r} (not repo root)")
                    continue
                if not _run_covers_this_file(step["run"]):
                    reasons.append(f"run does not cover {THIS_FILE_RELPATH}")
                    continue
                ok, why_not = _step_can_fail_the_job(step, job)
                if not ok:
                    reasons.append(f"covers the file but cannot fail the job: {why_not}")
                    continue
                covering_hits.append((job_name, step["run"]))
                qualified = True
            considered.append(
                (job_name, "qualifying step found" if qualified else "; ".join(reasons))
            )

        assert covering_hits, (
            f"no job in ci.yml has a root-scoped step covering {THIS_FILE_RELPATH} "
            f"that is actually able to fail the job; jobs considered and why each "
            f"was rejected: {considered!r}"
        )

        install_forms = _load_install_forms_module()
        kind_values = {
            install_forms.KIND_EXECUTED_NODE,
            install_forms.KIND_MARKDOWN,
            install_forms.KIND_PYTHON_LITERAL,
            install_forms.KIND_PYTHON_RUNTIME,
        }
        assert len(kind_values) == 4, (
            f"expected exactly 4 distinct KIND_* values, got {kind_values!r}"
        )
