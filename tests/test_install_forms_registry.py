"""RED tests for bd#21 (spec 2026-07-28): a single canonical install-forms
module (`scripts/install_forms.py`) plus an enumerable platform registry
(`CARRIERS`), so a brand-new platform carrying a denied `pip install` form
fails the build instead of silently going unnoticed (bd#17's `WRAPPER`
constant covered exactly one platform).

Per workflows.md §1q, `scripts/install_forms.py` is never imported at module
scope -- every test loads it lazily, inside its own body, via
`importlib.util.spec_from_file_location` (never `sys.path` mutation, never
`from conftest import ...`), so collection always succeeds and failure on
its absence happens at assert time with a readable message. `import yaml`
at module scope is the one deliberate hard dependency (AC10 needs it to
parse ci.yml; its absence must be a loud collection error, never a skip).

Nothing here reimplements the domain scan, the waiver predicate, or the
registry itself -- those are read out of `scripts/install_forms.py` (once
GREEN creates it), never transcribed from the spec (task instruction:
"домен скана и waiver -- из канонического источника, не переопределяй их в
тесте"). This file's own helpers are limited to: (a) loading modules by
path, (b) extracting install-relevant text out of each carrier KIND so the
canonical grammar/predicates can be applied to it, and (c) the CI-shape
check that mirrors tests/test_npm_install_hint.py::test_ac9 for this new
file (AC10).

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
        f"ALLOWED_INSTALLER_FORMS / DENIED_INSTALLER_FORMS / find_install_commands / "
        f"CARRIERS has not been created (spec §2.1/§2.2)"
    )
    return _load_module_from_path("install_forms_uut", INSTALL_FORMS_PATH)


def _load_package_meta_module():
    assert PACKAGE_META_PATH.is_file(), f"{PACKAGE_META_PATH} missing -- fixture broken"
    return _load_module_from_path("package_meta_uut", PACKAGE_META_PATH)


# ---------------------------------------------------------------------------
# AC1 -- single-source check: a bounded repo walk for module-level
# definitions of the three names that are supposed to live in exactly one
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
# Carrier-text extraction, dispatched by KIND (spec §2.2/§2.3). Each helper
# turns a registered carrier into the text the canonical grammar/predicates
# from `install_forms` are applied to -- none of it reimplements R1/R2/the
# domain scan/the waiver rule, all of which are read off the loaded module.
# ---------------------------------------------------------------------------
_CONSOLE_ERROR_RE = re.compile(r'console\.error\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _js_console_error_strings(text: str) -> list[str]:
    """Every string literal argument of a `console.error("...")` call in a
    node source file -- the actual text this platform prints (spec §2.2's
    KIND_EXECUTED_NODE), read statically since these particular calls carry
    only literal arguments (no template interpolation)."""
    return [
        m.group(1).encode().decode("unicode_escape")
        for m in _CONSOLE_ERROR_RE.finditer(text)
    ]


def _python_string_literals(path: Path) -> list[str]:
    """Every string constant (docstrings included) in a Python source file,
    via `ast` -- not a line/regex scan, so a literal split across lines or
    inside an f-string segment is not silently skipped."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _carrier_scan_text(install_forms, relpath: str, kind: str) -> str:
    """R1 carriers (KIND_EXECUTED_NODE, KIND_MARKDOWN): the text the R1
    line-anchored grammar is applied to."""
    path = REPO_ROOT / relpath
    assert path.is_file(), f"registered carrier {relpath!r} does not exist"
    text = path.read_text(encoding="utf-8")
    if kind == install_forms.KIND_MARKDOWN:
        return install_forms._fenced_code_text(text)
    if kind == install_forms.KIND_EXECUTED_NODE:
        return "\n".join(_js_console_error_strings(text))
    raise AssertionError(f"{relpath!r} kind {kind!r} is not an R1 kind")


def _carrier_r2_texts(install_forms, package_meta, relpath: str, kind: str) -> list[str]:
    """R2 carriers (KIND_PYTHON_LITERAL, KIND_PYTHON_RUNTIME): the list of
    strings the R2 embedded-substring rule is applied to -- string literals
    for the literal kind, and the module's own rendered command(s) for the
    runtime kind (spec §2.2: KIND_PYTHON_RUNTIME is "проверяется
    ИСПОЛНЕНИЕМ", not a static literal)."""
    path = REPO_ROOT / relpath
    assert path.is_file(), f"registered carrier {relpath!r} does not exist"
    if kind == install_forms.KIND_PYTHON_LITERAL:
        return _python_string_literals(path)
    if kind == install_forms.KIND_PYTHON_RUNTIME:
        assert relpath == "engine_py/package_meta.py", (
            f"no runtime-probe convention known for {relpath!r} -- teach "
            f"this helper how to execute it"
        )
        return [package_meta.install_hint(), package_meta.install_hint("test")]
    raise AssertionError(f"{relpath!r} kind {kind!r} is not an R2 kind")


def _carrier_is_alive(install_forms, package_meta, relpath: str, kind: str) -> bool:
    """Whether a registered carrier yields >=1 install command under the
    grammar (spec AC4) -- applies find_install_commands generically,
    regardless of whether the installer form it carries is allowed or
    denied, since liveness (does it carry a command at all) is a separate
    question from correctness (is that command's form allowed)."""
    if kind in (install_forms.KIND_EXECUTED_NODE, install_forms.KIND_MARKDOWN):
        text = _carrier_scan_text(install_forms, relpath, kind)
        return bool(install_forms.find_install_commands(text))
    texts = _carrier_r2_texts(install_forms, package_meta, relpath, kind)
    return any(install_forms.find_install_commands(t) for t in texts)


def _make_domain_fixture(tmp_path: Path) -> Path:
    """A minimal domain fixture (§2.4) holding one brand-new, UNREGISTERED
    file that carries a denied installer form -- the synthetic proof that
    the domain scan genuinely walks the tree rather than being satisfiable
    by a stub that always returns no violations."""
    root = tmp_path / "domain_fixture"
    (root / "engine_py").mkdir(parents=True)
    decoy = root / "engine_py" / "new_backend.py"
    decoy.write_text(
        'HINT = "you can pip install foo-bar to get going"\n', encoding="utf-8"
    )
    return root


# ---------------------------------------------------------------------------
# AC7 -- real `doctor.check_optional_deps()` execution (spec §1l/§2.8): a
# subprocess with engine_py placed on PYTHONPATH, never an import at module
# scope of this file (doctor pulls in the rest of engine_py at import time).
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
# AC10 helpers -- same form as tests/test_npm_install_hint.py::test_ac9,
# re-pointed at THIS file (spec AC10).
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
    def test_ac1_canonical_definitions_live_exactly_once_in_scripts_install_forms(
        self,
    ):
        """AC1 (§1g): ALLOWED_INSTALLER_FORMS, DENIED_INSTALLER_FORMS and
        find_install_commands must be defined exactly once in the repo, at
        scripts/install_forms.py. Red today: scripts/install_forms.py does
        not exist, so the module load fails first."""
        install_forms = _load_install_forms_module()
        for name in (
            "ALLOWED_INSTALLER_FORMS",
            "DENIED_INSTALLER_FORMS",
            "find_install_commands",
        ):
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
        installer form. Red today (once the module exists): a
        `pip install bytedigger` line in packaging/pypi-pointer/README.md."""
        install_forms = _load_install_forms_module()
        r1_kinds = (install_forms.KIND_EXECUTED_NODE, install_forms.KIND_MARKDOWN)
        checked_any = False
        bad = []
        for relpath, kind in install_forms.CARRIERS:
            if kind not in r1_kinds:
                continue
            checked_any = True
            text = _carrier_scan_text(install_forms, relpath, kind)
            for cmd in install_forms.find_install_commands(text):
                if not install_forms._installer_form_is_allowed(cmd["exe"]):
                    bad.append((relpath, cmd))
        assert checked_any, "no KIND_EXECUTED_NODE/KIND_MARKDOWN entry in CARRIERS"
        assert not bad, f"R1 violation(s) in registered carrier(s): {bad!r}"

    # -- AC3 ---------------------------------------------------------------
    def test_ac3_every_r2_carrier_in_the_registry_carries_no_denied_form(self):
        """AC3: for every CARRIERS entry of kind KIND_PYTHON_LITERAL or
        KIND_PYTHON_RUNTIME, no string (literal or rendered at runtime)
        contains a denied form ANYWHERE inside it. Red today (once the
        module exists): package_meta.py, llm_subprocess.py, agent_sdk.py
        and dbos_setup.py all still carry bare `pip install`."""
        install_forms = _load_install_forms_module()
        package_meta = _load_package_meta_module()
        r2_kinds = (install_forms.KIND_PYTHON_LITERAL, install_forms.KIND_PYTHON_RUNTIME)
        checked_any = False
        hits = []
        for relpath, kind in install_forms.CARRIERS:
            if kind not in r2_kinds:
                continue
            checked_any = True
            for text in _carrier_r2_texts(install_forms, package_meta, relpath, kind):
                if install_forms.contains_denied_form(text):
                    hits.append((relpath, text))
        assert checked_any, "no KIND_PYTHON_LITERAL/KIND_PYTHON_RUNTIME entry in CARRIERS"
        assert not hits, f"R2 violation(s) in registered carrier(s): {hits!r}"

    # -- AC4 -----------------------------------------------------------
    def test_ac4_every_registered_carrier_is_load_bearing(self):
        """AC4: every CARRIERS entry must carry >=1 install command --
        otherwise the registry entry is dead and should be removed."""
        install_forms = _load_install_forms_module()
        package_meta = _load_package_meta_module()
        assert install_forms.CARRIERS, "CARRIERS is empty"
        dead = [
            relpath
            for relpath, kind in install_forms.CARRIERS
            if not _carrier_is_alive(install_forms, package_meta, relpath, kind)
        ]
        assert not dead, f"registered carrier(s) with zero install commands: {dead!r}"

    # -- AC5 -----------------------------------------------------------
    def test_ac5_domain_scan_finds_nothing_outside_carriers_without_a_waiver(
        self, tmp_path
    ):
        """AC5 -- the heart of the lot: the §2.4 domain scan must find no
        denied installer form in any file that is both (a) absent from
        CARRIERS and (b) not covered by a valid waiver. Red today: real-tree
        evidence is engine_py/config_provider.py's docstring, which trips R2
        and carries no waiver yet. A second, synthetic assertion plants a
        brand-new unregistered file so a stub `scan_domain` that always
        returns [] cannot satisfy this test post-fix."""
        install_forms = _load_install_forms_module()

        violations = install_forms.scan_domain(REPO_ROOT)
        carrier_paths = {relpath for relpath, _kind in install_forms.CARRIERS}
        outside = [v for v in violations if v.get("path") not in carrier_paths]
        assert not outside, (
            f"domain scan found denied installer form(s) outside CARRIERS with "
            f"no valid waiver: {outside!r}"
        )

        fixture_root = _make_domain_fixture(tmp_path)
        fixture_violations = install_forms.scan_domain(fixture_root)
        fixture_hit_paths = {v.get("path") for v in fixture_violations}
        assert "engine_py/new_backend.py" in fixture_hit_paths, (
            f"a brand-new, unregistered file with a denied installer form must "
            f"be flagged by the domain scan (this is what makes a new platform "
            f"fail the build): got {fixture_violations!r}"
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
        """AC7 (§1l): really execute doctor.check_optional_deps() (no
        dependencies required -- pydantic_ai is absent, measured spec §2.8)
        and assert its printed `detail` names an allowed installer form and
        the canonical PACKAGE_DIST_NAME, transcribing neither. Red today:
        the shipped detail contains bare `pip install "..."`."""
        install_forms = _load_install_forms_module()
        package_meta = _load_package_meta_module()

        check = _run_doctor_check_optional_deps()
        assert check.get("name") == "optional-deps", f"unexpected check: {check!r}"
        detail = check.get("detail", "")
        assert "pydantic_ai" in detail, (
            f"expected the pydantic_ai probe to be missing (measured, spec "
            f"§2.8) so its install hint is present in detail: {detail!r}"
        )

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
        (embedded substring) must find the denied form -- without this, R2
        could be silently narrowed to R1 and platforms 6/7 would go
        invisible again."""
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
    def test_ac9_bd17_test_file_imports_the_canonical_definitions(self):
        """AC9: tests/test_npm_install_hint.py must import
        ALLOWED_INSTALLER_FORMS / DENIED_INSTALLER_FORMS /
        find_install_commands (etc.) from scripts.install_forms rather than
        defining its own -- the behavioural half (bd#17's 10 ACs staying
        green) is checked by the orchestrator's full-suite delta, not by
        this test."""
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

    # -- AC10 ----------------------------------------------------------
    def test_ac10_some_root_scoped_ci_step_can_actually_fail_on_this_file(self):
        """AC10 (same form as tests/test_npm_install_hint.py::test_ac9): a
        job's `run:` string alone does not prove coverage -- the effective
        working directory must resolve to repo root, and the covering step
        must be structurally able to fail its job (no `|| true`, no
        continue-on-error, no narrowing if:). Red today: no job in ci.yml
        names tests/test_install_forms_registry.py."""
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
