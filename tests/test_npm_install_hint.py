"""RED tests for bd#17 (spec 2026-07-28, v3): the npm pointer package's
"engine not found" hint must print a working install command, and that
property must be enforced by a test, not just prose.

Per workflows.md §1q, nothing here resolves paths or subprocess-invokes at
module import time -- every test resolves/executes lazily inside its own
body, so collection always succeeds and failure happens at assert time.

Per §1l, AC1/AC2/AC4/AC5/AC6/AC8 assert on stderr produced by REALLY
EXECUTING `npm/bin/bytedigger.js` with an isolated PATH (only `python3` and
`which`/`where`; `bytedigger-engine` absent) -- never on the wrapper's
source text. `node` is invoked by its ABSOLUTE path: once PATH is replaced,
a bare `"node"` argv0 fails to resolve (measured, spec §2.5). Absence of
`node` on the host is a hard failure of this suite, never a skip (spec
§2.5) -- skip is exactly the rot mechanism bd#17 exists to stop (AC10
enforces this about this file's own source).

AC4's engine-probe-name extraction is the sole exception to "execute, don't
read": it is bound to the `if (has(X)) { ... spawnSync(X, ...) ... }` block
in npm/bin/bytedigger.js (spec §2.3), not to "the only has() in the file" --
a future `if (has("pipx")) { ... }` improvement with no spawnSync inside
must not qualify.

Repo root is the parent of this tests/ directory.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "npm" / "bin" / "bytedigger.js"
README = REPO_ROOT / "npm" / "README.md"
PYPROJECT = REPO_ROOT / "engine_py" / "pyproject.toml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Located once, at import time -- this is a HOST TOOL lookup (node's own
# location), not resolution of anything under test, so it cannot make
# collection fail; absence is asserted inside the test body (spec §2.5).
NODE = shutil.which("node")

# ---------------------------------------------------------------------------
# §2.1 grammar (v3, MAJOR-2 fix) -- the ONE named helper every AC in this
# file goes through (§1aa). `exe` is one to three tokens, lazily: `pip
# install X` -> exe="pip"; `uv pip install X` -> exe="uv pip". The anchor at
# line-start is what separates a command from prose ("...prints the pip
# install instructions..." is never matched: more than three tokens precede
# "install"). Measured on the current, unpatched content of the scope files
# (v3 re-measurement per §4.0): the executed wrapper's stderr yields 1 hit,
# npm/README.md yields 2 hits (`pip install git+...`, `pip install -e .`),
# and NEITHER npm/README.md:9 ("...the editable install below needs pip
# 21.3+...") NOR :27 ("...prints the pip install instructions...") is
# matched -- both have far more than 3 tokens before "install". No false
# positive was found on any prose line in the scope files.
# ---------------------------------------------------------------------------
_INSTALL_LINE_RE = re.compile(r"^(?P<exe>\S+(?:\s+\S+){0,2}?)\s+install\b")


def find_install_commands(text: str) -> list[dict]:
    """Lines of `text` that count as an "install command" under spec §2.1
    (v3): after stripping leading whitespace, a `$ ` shell-prompt marker,
    and a list-item marker (`- ` / `* `), what remains matches
    ^(exe: 1-3 tokens, lazy)\\s+install\\b. Returns one dict per hit with
    `exe` (the installer form), `target` (whatever follows `install`,
    trimmed), and `line` (the stripped/marker-free line)."""
    out = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for _ in range(2):
            line = re.sub(r"^\$\s+", "", line)
            line = re.sub(r"^[-*]\s+", "", line)
        m = _INSTALL_LINE_RE.match(line)
        if not m:
            continue
        exe = m.group("exe")
        target = line[m.end():].strip()
        out.append({"exe": exe, "target": target, "line": line})
    return out


# §2.2 closed dictionary (v3) -- pinned in both directions (§1n). `uv` alone
# is a v1 false-accept: `uv install X` is not a real command (`uv pip
# install` / `uv tool install` / `uv add` are).
ALLOWED_INSTALLER_FORMS = frozenset({"pipx", "uv pip", "uv tool", "python3 -m pip", "python -m pip"})
DENIED_INSTALLER_FORMS = frozenset({"pip", "pip3", "uv"})


def _installer_form_is_allowed(exe: str) -> bool:
    """P1's single named predicate (§1aa) -- used by AC2, AC3 and AC7 alike;
    no inline `exe in ALLOWED_INSTALLER_FORMS` duplicates anywhere else in
    this file, so a future swap to `exe in DENIED_INSTALLER_FORMS` (which
    would look almost identical but flip the decision rule) cannot hide in
    one call site while the others still read correctly."""
    return exe in ALLOWED_INSTALLER_FORMS


# §2.4 P3 -- a command's target must not be a VCS/URL.
_VCS_URL_RE = re.compile(r"git\+|https?://|\.git\b")


def _is_vcs_or_url_target(target: str) -> bool:
    return bool(_VCS_URL_RE.search(target))


def _is_package_name_target(target: str) -> bool:
    """P2 applies only to commands whose target is a bare package NAME, not
    a path (`-e .`, `.`) or a VCS/URL (spec §2.3 opening clause)."""
    if not target:
        return False
    if _is_vcs_or_url_target(target):
        return False
    if target.startswith("-") or target.startswith("."):
        return False
    return True


def _extract_package_name(target: str) -> str:
    """The bare package NAME out of a package-name target (spec §2.3,
    MINOR-5): first token, quotes stripped, `[extras]` dropped, and any
    version specifier (`==`, `>=`, `~=`, `!=`, `@`) truncated. Comparing the
    whole tail of the line (v1) pinned STATE -- `pipx install
    "bytedigger-engine"`, `... bytedigger-engine==0.1.2`, `...
    bytedigger-engine --python python3.11` are all still correct and must
    compare equal to the bare name."""
    first = target.split()[0] if target.split() else ""
    first = first.strip("'\"")
    first = re.sub(r"\[[^\]]*\]", "", first)
    first = re.split(r"==|>=|<=|~=|!=|@", first)[0]
    return first


def _engine_probe_name() -> str:
    """The engine package name the wrapper actually SPAWNS, read out of
    npm/bin/bytedigger.js's own source (the one exception to "execute,
    don't read", for AC4 only). Bound to the `if (has(X)) { ...
    spawnSync(X, ...) ... }` block specifically (spec §2.3, A7 of the
    gate) -- NOT to "the only has() call in the file": a natural
    improvement like `if (has("pipx")) { ...suggest pipx... }` with no
    spawnSync inside must not make this qualify (and must not turn AC4
    red on a correct edit)."""
    text = WRAPPER.read_text(encoding="utf-8")
    blocks = re.findall(
        r"if\s*\(\s*has\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\)\s*\{([^{}]*)\}",
        text,
    )
    qualifying = [
        probe
        for probe, body in blocks
        if re.search(r"spawnSync\(\s*['\"]" + re.escape(probe) + r"['\"]", body)
    ]
    assert qualifying, (
        f"no `if (has(X)) {{ ... spawnSync(X, ...) ... }}` block found in {WRAPPER}"
    )
    assert len(set(qualifying)) == 1, (
        f"expected exactly one has(X)+spawnSync(X) engine-probe block in "
        f"{WRAPPER}, found {sorted(set(qualifying))!r}"
    )
    return qualifying[0]


def _parse_project_name(text: str) -> str | None:
    """[project].name out of a pyproject.toml, parsed the same minimal way
    tests/test_version_parity.py parses [project].version."""
    in_project = False
    for line in text.splitlines():
        if re.match(r"^\[project\]\s*$", line):
            in_project = True
            continue
        if in_project:
            if re.match(r"^\[", line):
                break
            m = re.match(r'^name\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    return None


def _isolated_engine_missing_path(tmp_path: Path) -> Path:
    """A PATH directory holding ONLY symlinks to the host's real python3 and
    which/where -- `bytedigger-engine` is absent. Built fresh per test under
    tmp_path (never shared)."""
    bin_dir = tmp_path / "isolated_bin"
    bin_dir.mkdir()

    python3 = shutil.which("python3")
    assert python3, (
        "python3 not found on the host PATH -- required to build the "
        "isolated-PATH fixture"
    )
    (bin_dir / "python3").symlink_to(python3)

    if sys.platform == "win32":
        which_name = "where"
        which_target = shutil.which("where")
    else:
        which_name = "which"
        which_target = shutil.which("which") or "/usr/bin/which"
    assert which_target and Path(which_target).exists(), (
        f"{which_name} not found on the host -- required to build the "
        "isolated-PATH fixture"
    )
    (bin_dir / which_name).symlink_to(which_target)

    return bin_dir


def _run_wrapper_isolated(tmp_path: Path) -> subprocess.CompletedProcess:
    """Really execute npm/bin/bytedigger.js with the isolated, engine-missing
    PATH. `node` is invoked by ABSOLUTE path -- a bare "node" argv0 fails to
    resolve once PATH is replaced (measured, spec §2.5). No `node` on the
    host is a hard test failure, never a skip (spec §2.5)."""
    assert NODE, (
        "node not found on this machine's PATH -- this test cannot execute "
        "the wrapper and must fail loudly rather than skip (spec §2.5)"
    )
    bin_dir = _isolated_engine_missing_path(tmp_path)
    env = {"PATH": str(bin_dir)}
    return subprocess.run(
        [NODE, str(WRAPPER)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


# ---------------------------------------------------------------------------
# AC9 helpers -- effective working-directory chain and "can this step
# actually fail the job" (MAJOR-3, home precedent
# tests/test_ci_main_heartbeat.py::test_ac12).
# ---------------------------------------------------------------------------


def _strip_comment_lines(run_str: str) -> str:
    """Comment lines inside a `run: |` block must not count as command text
    (spec AC9) -- applied before scanning for pytest invocations or
    `|| true`."""
    return "\n".join(
        line for line in run_str.splitlines() if not line.strip().startswith("#")
    )


def _effective_step_cwd(workflow_data: dict, job: dict, step: dict):
    """Effective working-directory chain (spec AC9, MINOR-9): step ->
    job -> workflow -> repo root, with `None` at any level falling through
    to the next level rather than crashing."""
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


def _run_covers_npm_install_hint(run_str: str) -> bool:
    """Whether a step's `run:` invokes pytest over a path covering this
    file -- literal path, or the bare `tests/` directory -- excluding
    comment lines and excluding invocations that explicitly carve this file
    back out via `--ignore`/`--deselect`."""
    cleaned = _strip_comment_lines(run_str)
    if not re.search(r"pytest\b", cleaned):
        return False
    covers = (
        "tests/test_npm_install_hint.py" in cleaned
        or bool(re.search(r"(?<![\w./])tests/(?:\s|$)", cleaned))
    )
    if not covers:
        return False
    excluded = re.search(
        r"--(?:ignore|deselect)[= ]\S*test_npm_install_hint\.py", cleaned
    )
    return not excluded


def _step_can_fail_the_job(step: dict, job: dict) -> tuple[bool, str]:
    """MAJOR-3: a step that structurally cannot fail its job enforces
    nothing. Rejects `|| true` in run (comment lines stripped first),
    `continue-on-error: true` on step or job, and any `if:` narrowing on
    step or job."""
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


class TestNpmInstallHint:
    def test_ac1_engine_missing_exits_1_and_prints_an_install_command(self, tmp_path):
        """AC1 (§1l, real side-effect): the wrapper, executed with the
        isolated PATH, exits 1 and prints >=1 install command (grammar
        §2.1) on stderr."""
        result = _run_wrapper_isolated(tmp_path)
        assert result.returncode == 1, (
            f"engine-missing path must exit 1; got {result.returncode}, "
            f"stderr={result.stderr!r}"
        )
        cmds = find_install_commands(result.stderr)
        assert cmds, (
            "the wrapper must print at least one install command (grammar "
            f"§2.1) on stderr when the engine is absent; stderr={result.stderr!r}"
        )

    def test_ac2_every_printed_install_command_uses_an_allowed_installer_form(self, tmp_path):
        """AC2 (P1 on the real executed output, via the named predicate).
        Red today: the shipped hint uses bare `pip`, which
        `_installer_form_is_allowed` rejects."""
        result = _run_wrapper_isolated(tmp_path)
        cmds = find_install_commands(result.stderr)
        assert cmds, f"no install command found in stderr={result.stderr!r}"
        bad = [c for c in cmds if not _installer_form_is_allowed(c["exe"])]
        assert not bad, (
            f"install command(s) use a non-allowed installer form: {bad!r}; "
            f"allowed forms are {sorted(ALLOWED_INSTALLER_FORMS)!r}"
        )

    def test_ac3_the_predicate_itself_is_correct_in_both_directions(self):
        """AC3 (P1, the PREDICATE is pinned both ways, not set membership as
        data): every DENIED form must be rejected and every ALLOWED form
        accepted by `_installer_form_is_allowed` directly. A v1-style test
        that only checks `exe in ALLOWED`/`exe in DENIED` as data would stay
        green even if the predicate itself were swapped to `exe in
        DENIED_INSTALLER_FORMS` (MINOR-8) -- calling the actual predicate
        catches that."""
        for denied in DENIED_INSTALLER_FORMS:
            assert _installer_form_is_allowed(denied) is False, (
                f"_installer_form_is_allowed({denied!r}) must be False"
            )
        for allowed in ALLOWED_INSTALLER_FORMS:
            assert _installer_form_is_allowed(allowed) is True, (
                f"_installer_form_is_allowed({allowed!r}) must be True"
            )

    def test_ac4_package_name_agrees_and_is_not_vacuous(self, tmp_path):
        """AC4 (P2, three sources converge + non-emptiness, MAJOR-1): on
        the executed wrapper's stderr there must be >=1 install command
        whose target is a bare package name (otherwise the property is
        vacuous -- a GREEN that only prints `python3 -m pip install -e .`
        would leave this whole suite green while shipping a hint that
        installs nothing for a new user). For each such command, the
        extracted package name must equal both the name the wrapper
        actually spawns (has(X)+spawnSync(X)) and [project].name in
        engine_py/pyproject.toml. Red today: no command targets a bare
        package name at all (the sole target is a git URL)."""
        probe_name = _engine_probe_name()
        canonical_name = _parse_project_name(PYPROJECT.read_text(encoding="utf-8"))
        assert canonical_name, f"{PYPROJECT} has no [project].name"
        assert probe_name == canonical_name, (
            f"the wrapper spawns {probe_name!r} but engine_py/pyproject.toml "
            f"declares [project].name = {canonical_name!r} -- these must agree"
        )

        result = _run_wrapper_isolated(tmp_path)
        cmds = find_install_commands(result.stderr)
        pkg_cmds = [c for c in cmds if _is_package_name_target(c["target"])]
        assert pkg_cmds, (
            "no printed install command targets a bare package name -- the "
            "property 'installs the package the wrapper looks for' is "
            f"vacuous without one; stderr={result.stderr!r}"
        )
        mismatched = [
            c for c in pkg_cmds if _extract_package_name(c["target"]) != probe_name
        ]
        assert not mismatched, (
            f"printed install command(s) target a package name that "
            f"disagrees with the spawned name {probe_name!r} / pyproject "
            f"name {canonical_name!r}: {mismatched!r}"
        )

    def test_ac5_no_printed_install_command_targets_a_vcs_url(self, tmp_path):
        """AC5 (P3 on the real executed output). Red today: the shipped
        hint's target is `git+https://...#subdirectory=engine_py`."""
        result = _run_wrapper_isolated(tmp_path)
        cmds = find_install_commands(result.stderr)
        assert cmds, f"no install command found in stderr={result.stderr!r}"
        bad = [c for c in cmds if _is_vcs_or_url_target(c["target"])]
        assert not bad, f"install command(s) target a VCS/URL, forbidden by P3: {bad!r}"

    def test_ac6_a_url_line_exists_and_is_not_flagged_as_a_command(self, tmp_path):
        """AC6 (P3 is not all-eating -- a PROPERTY, not a transcribed
        literal, MINOR-6): stderr must contain some line with `https?://`
        that the grammar does NOT parse as an install command. v1
        transcribed the literal `Docs: https://github.com/guy-lifshitz/
        bytedigger` -- a repository move would have redenned a correct
        edit."""
        result = _run_wrapper_isolated(tmp_path)
        hit = None
        for line in result.stderr.splitlines():
            if re.search(r"https?://", line) and not find_install_commands(line):
                hit = line
                break
        assert hit is not None, (
            "expected a URL-bearing line in stderr that the grammar does "
            f"NOT parse as an install command; stderr={result.stderr!r}"
        )

    def test_ac7_readme_install_commands_satisfy_p1_p2_p3_and_are_not_vacuous(self):
        """AC7 (§2.6: same P1-P3 + non-emptiness, one test, README as the
        second carrier, using the same named helpers). Red today (both
        README lines): line 12 shares the wrapper's non-allowed `pip` + git
        URL, line 20 is bare `pip install -e .` (P1 violation), and no line
        targets a bare package name at all (P2 non-emptiness fails too)."""
        text = README.read_text(encoding="utf-8")
        cmds = find_install_commands(text)
        assert cmds, f"grammar found no install commands in {README}"

        probe_name = _engine_probe_name()
        canonical_name = _parse_project_name(PYPROJECT.read_text(encoding="utf-8"))

        bad_exe = [c for c in cmds if not _installer_form_is_allowed(c["exe"])]
        assert not bad_exe, (
            f"README install command(s) use a non-allowed installer form: {bad_exe!r}"
        )

        bad_vcs = [c for c in cmds if _is_vcs_or_url_target(c["target"])]
        assert not bad_vcs, f"README install command(s) target a VCS/URL: {bad_vcs!r}"

        pkg_cmds = [c for c in cmds if _is_package_name_target(c["target"])]
        assert pkg_cmds, (
            f"no install command in {README} targets a bare package name -- "
            "the property would be vacuous"
        )
        mismatched = [
            c
            for c in pkg_cmds
            if _extract_package_name(c["target"]) not in (probe_name, canonical_name)
        ]
        assert not mismatched, (
            f"README install command(s) target the wrong package name: {mismatched!r}"
        )

    def test_ac8_grammar_is_non_empty_round_trips_and_is_not_escaped(self, tmp_path):
        """AC8, three parts (v3):
        1. non-emptiness: each carrier (executed wrapper stderr; README)
           yields >=1 install command;
        2. round-trip (§1ab): every ALLOWED_INSTALLER_FORMS form, embedded
           as `f"{form} install pkg"`, is recognised by the grammar with
           exe == form -- the dictionary and the grammar are mutually
           obligated;
        3. decoy: `uv pip install git+https://x.git` IS recognised by the
           grammar (exe == "uv pip") -- the exact escape hatch that let any
           two-token form through unmatched in v1 (MAJOR-2)."""
        result = _run_wrapper_isolated(tmp_path)
        wrapper_cmds = find_install_commands(result.stderr)
        assert wrapper_cmds, (
            f"grammar found zero install commands in the executed wrapper's "
            f"stderr={result.stderr!r}"
        )

        readme_cmds = find_install_commands(README.read_text(encoding="utf-8"))
        assert readme_cmds, f"grammar found zero install commands in {README}"

        for form in ALLOWED_INSTALLER_FORMS:
            line = f"{form} install pkg"
            cmds = find_install_commands(line)
            assert cmds and cmds[0]["exe"] == form, (
                f"round-trip failed: {line!r} -> {cmds!r}, expected exe == {form!r}"
            )

        decoy = "uv pip install git+https://x.git"
        decoy_cmds = find_install_commands(decoy)
        assert decoy_cmds and decoy_cmds[0]["exe"] == "uv pip", (
            f"grammar must recognise the two-token decoy {decoy!r}, got {decoy_cmds!r}"
        )
        assert _is_vcs_or_url_target(decoy_cmds[0]["target"]), (
            f"decoy target must be recognised as a VCS/URL by P3: {decoy_cmds!r}"
        )

    def test_ac9_some_root_scoped_ci_step_can_actually_fail_on_this_file(self):
        """AC9 (§1l shield + MAJOR-3: the covering step must be able to
        fail the job, home precedent tests/test_ci_main_heartbeat.py::
        test_ac12): a job's `run:` string alone does not prove coverage --
        `defaults.run.working-directory` (workflow, job, or per-step)
        rebases every relative path, so a bare `pytest tests/` inside a job
        effectively scoped to engine_py/ runs engine_py/tests/, never the
        repo-root tests/ this file lives in. And a covering step that has
        `|| true`, `continue-on-error: true`, or a narrowing `if:` cannot
        actually fail CI on this file. Red today: the `pytest` job's
        `tests/` is scoped to engine_py/, and the root-scoped `manifests`
        job does not yet name this file."""
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
                if not _run_covers_npm_install_hint(step["run"]):
                    reasons.append("run does not cover tests/test_npm_install_hint.py")
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
            "no job in ci.yml has a root-scoped step covering "
            "tests/test_npm_install_hint.py that is actually able to fail "
            f"the job; jobs considered and why each was rejected: {considered!r}"
        )

    def test_ac10_this_suite_never_skips_or_conditionally_disables_itself(self):
        """AC10 (A6 of the gate): this file's own source contains none of
        the mechanisms that let a test silently not run. Absence of
        `node`/`pyyaml` must fail this suite loudly, never switch it off --
        that silent-skip is exactly the rot mechanism bd#17 targets.
        Markers are built by concatenation so this assertion does not flag
        its own source."""
        source = Path(__file__).read_text(encoding="utf-8")
        forbidden = (
            "pytest" + ".skip",
            "import" + "orskip",
            "mark" + ".skipif",
        )
        for marker in forbidden:
            assert marker not in source, (
                f"{marker!r} found in this suite's own source -- a layer that "
                "can silently not run is not a layer (spec AC10)"
            )
