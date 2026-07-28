"""RED tests for bd#17 (spec 2026-07-28): the npm pointer package's
"engine not found" hint must print a working install command, and that
property must be enforced by a test, not just prose.

Per workflows.md §1q, nothing here resolves paths or subprocess-invokes at
module import time -- every test resolves/executes lazily inside its own
body, so collection always succeeds and failure happens at assert time.

Per §1l, AC1/AC2/AC4/AC5/AC6 assert on stderr produced by REALLY EXECUTING
`npm/bin/bytedigger.js` with an isolated PATH (only `python3` and
`which`/`where`; `bytedigger-engine` absent) -- never on the wrapper's
source text. `node` is invoked by its ABSOLUTE path: once PATH is replaced,
a bare `"node"` argv0 fails to resolve (measured, spec §2.5). Absence of
`node` on the host is a hard failure of this suite, never a skip (spec
§2.5) -- skip is exactly the rot mechanism bd#17 exists to stop.

AC4 is the sole exception to "execute, don't read": the engine package name
the wrapper probes for is read out of its own `has(...)` call via regex
(spec §2.3), never transcribed as a string literal in this file.

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
# §2.1 grammar -- the ONE named helper every AC in this file goes through
# (§1aa). Measured on the current, unpatched content of the scope files:
# it finds 1 line in the executed wrapper's stderr and 2 lines in
# npm/README.md, and does NOT match "Docs: https://..." or the
# `bytedigger-engine` CLI sentence (AC6/AC8).
# ---------------------------------------------------------------------------
_INSTALL_LINE_RE = re.compile(r"^(?P<exe>\S+(?:\s+-m\s+\S+)?)\s+install\b")


def find_install_commands(text: str) -> list[dict]:
    """Lines of `text` that count as an "install command" under spec §2.1:
    after stripping leading whitespace, a `$ ` shell-prompt marker, and a
    list-item marker (`- ` / `* `), what remains matches
    ^(exe)(?:\\s+-m\\s+\\S+)?\\s+install\\b. Returns one dict per hit with
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


# §2.2 closed dictionary -- pinned in both directions (§1n): ALLOWED is the
# only set that satisfies P1; DENIED names the exact regression (bare pip /
# pip3) this spec exists to catch.
ALLOWED_INSTALLER_FORMS = frozenset({"pipx", "uv", "python3 -m pip", "python -m pip"})
DENIED_INSTALLER_FORMS = frozenset({"pip", "pip3"})

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


def _engine_probe_name() -> str:
    """The engine package name the wrapper actually probes for via
    `has(...)`, read out of npm/bin/bytedigger.js's own source by regex --
    the one exception (AC4) to "execute, don't read" for this file. The
    `python3` probe is excluded; there must be exactly one other distinct
    `has(...)` argument."""
    text = WRAPPER.read_text(encoding="utf-8")
    args = re.findall(r"has\(\s*[\'\"]([^\'\"]+)[\'\"]\s*\)", text)
    candidates = [a for a in args if a != "python3"]
    assert candidates, f"no non-python3 has(...) probe found in {WRAPPER}: args={args!r}"
    assert len(set(candidates)) == 1, (
        f"expected exactly one distinct non-python3 has(...) probe argument in "
        f"{WRAPPER}, found {sorted(set(candidates))!r}"
    )
    return candidates[0]


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


class TestNpmInstallHint:
    def test_ac1_engine_missing_exits_1_and_prints_an_install_command(self, tmp_path):
        """AC1 (§1l, real side-effect): the wrapper, executed with the
        isolated PATH, exits 1 and prints >=1 install command (grammar
        §2.1) on stderr. Discriminates the empty/broken "engine not found"
        path."""
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
        """AC2 (P1 on the real executed output). Red today: the shipped hint
        uses bare `pip`, which is not in ALLOWED_INSTALLER_FORMS."""
        result = _run_wrapper_isolated(tmp_path)
        cmds = find_install_commands(result.stderr)
        assert cmds, f"no install command found in stderr={result.stderr!r}"
        bad = [c for c in cmds if c["exe"] not in ALLOWED_INSTALLER_FORMS]
        assert not bad, (
            f"install command(s) use a non-allowed installer form: {bad!r}; "
            f"allowed forms are {sorted(ALLOWED_INSTALLER_FORMS)!r}"
        )

    def test_ac3_bare_pip_and_pip3_are_rejected_by_the_dictionary(self):
        """AC3 (P1, reverse direction of the dictionary). Feeds the grammar
        + dictionary synthetic lines directly -- without this AC the
        dictionary could be widened to swallow `pip`/`pip3` and AC2 would
        stay green on the exact regression this spec targets."""
        for line in ("pip install bytedigger-engine", "pip3 install bytedigger-engine"):
            cmds = find_install_commands(line)
            assert cmds, f"grammar did not parse synthetic line {line!r}"
            exe = cmds[0]["exe"]
            assert exe in ("pip", "pip3")
            assert exe in DENIED_INSTALLER_FORMS, f"{exe!r} must be in DENIED_INSTALLER_FORMS"
            assert exe not in ALLOWED_INSTALLER_FORMS, (
                f"{exe!r} must never be an allowed installer form -- a "
                "dictionary widened to swallow it would let AC2 stay green "
                "on the exact regression this spec exists to catch"
            )

    def test_ac4_package_name_agrees_across_probe_output_and_pyproject(self, tmp_path):
        """AC4 (P2, three sources converge, none transcribed): the has(...)
        probe argument in npm/bin/bytedigger.js == [project].name in
        engine_py/pyproject.toml == the package-name target of any printed
        install command. Green today: the shipped hint's target is a git
        URL (excluded from P2 by its own path/URL clause), so this is a
        vacuous shield until GREEN makes the target a bare package name --
        AC7/AC8 guard that this AC cannot go vacuous silently."""
        probe_name = _engine_probe_name()
        canonical_name = _parse_project_name(PYPROJECT.read_text(encoding="utf-8"))
        assert canonical_name, f"{PYPROJECT} has no [project].name"
        assert probe_name == canonical_name, (
            f"the wrapper probes for has({probe_name!r}) but "
            f"engine_py/pyproject.toml declares [project].name = "
            f"{canonical_name!r} -- these must agree"
        )

        result = _run_wrapper_isolated(tmp_path)
        cmds = find_install_commands(result.stderr)
        pkg_cmds = [c for c in cmds if _is_package_name_target(c["target"])]
        mismatched = [c for c in pkg_cmds if c["target"] != probe_name]
        assert not mismatched, (
            f"printed install command(s) target a package name that "
            f"disagrees with the has(...) probe {probe_name!r} / pyproject "
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

    def test_ac6_docs_url_line_is_present_but_not_flagged_by_p3(self, tmp_path):
        """AC6 (P3 is not all-eating): the `Docs: https://...` line is
        present in the output and must NOT be parsed as an install command
        -- otherwise AC5 could be satisfied by banning every URL in the
        whole output, which would break this legitimate line."""
        result = _run_wrapper_isolated(tmp_path)
        assert "Docs: https://github.com/guy-lifshitz/bytedigger" in result.stderr, (
            f"wrapper must still print a Docs: URL line; stderr={result.stderr!r}"
        )
        cmds = find_install_commands(result.stderr)
        docs_hits = [c for c in cmds if c["line"].startswith("Docs:")]
        assert not docs_hits, (
            "the Docs: https://... line must never be parsed as an install "
            f"command -- grammar over-matched: {docs_hits!r}"
        )

    def test_ac7_readme_install_commands_satisfy_p1_p2_p3(self):
        """AC7 (§2.6: same P1-P3, one test, README as the second carrier).
        Red today (both README lines): line 12 is the same non-allowed
        `pip` + git URL as the wrapper, line 20 is bare `pip install -e .`
        (P1 violation; P2/P3 do not apply to a path target)."""
        text = README.read_text(encoding="utf-8")
        cmds = find_install_commands(text)
        assert cmds, f"grammar found no install commands in {README}"

        probe_name = _engine_probe_name()
        canonical_name = _parse_project_name(PYPROJECT.read_text(encoding="utf-8"))

        bad_exe = [c for c in cmds if c["exe"] not in ALLOWED_INSTALLER_FORMS]
        assert not bad_exe, (
            f"README install command(s) use a non-allowed installer form: {bad_exe!r}"
        )

        bad_vcs = [c for c in cmds if _is_vcs_or_url_target(c["target"])]
        assert not bad_vcs, f"README install command(s) target a VCS/URL: {bad_vcs!r}"

        pkg_cmds = [c for c in cmds if _is_package_name_target(c["target"])]
        mismatched = [
            c for c in pkg_cmds if c["target"] not in (probe_name, canonical_name)
        ]
        assert not mismatched, (
            f"README install command(s) target the wrong package name: {mismatched!r}"
        )

    def test_ac8_grammar_is_non_empty_and_not_all_eating_on_both_carriers(self, tmp_path):
        """AC8: the §2.1 grammar finds >=1 install command on each carrier
        (executed wrapper stderr; README) and does NOT match the
        known non-command lines (measured §2.1). Guards against the
        grammar rotting into either "matches nothing" (P1-P3 vacuously
        green forever) or "matches everything"."""
        result = _run_wrapper_isolated(tmp_path)
        wrapper_cmds = find_install_commands(result.stderr)
        assert wrapper_cmds, (
            f"grammar found zero install commands in the executed wrapper's "
            f"stderr={result.stderr!r}"
        )

        readme_text = README.read_text(encoding="utf-8")
        readme_cmds = find_install_commands(readme_text)
        assert len(readme_cmds) >= 2, (
            f"grammar must find >=2 install commands in {README} (measured "
            f"§2.1), found {len(readme_cmds)}: {readme_cmds!r}"
        )

        non_command_lines = [
            "Docs: https://github.com/guy-lifshitz/bytedigger",
            "Then re-run this command, or use `bytedigger-engine` directly.",
        ]
        for line in non_command_lines:
            hits = find_install_commands(line)
            assert not hits, f"grammar wrongly matched a non-command line {line!r}: {hits!r}"

    def test_ac9_some_ci_job_runs_pytest_over_a_path_covering_this_file(self):
        """AC9 (§1l shield, MAJOR-4 / bd#11 re-wedge): a job's `run:` string
        alone does not prove coverage -- `defaults.run.working-directory`
        (job-level or per-step) rebases every relative path in that job, so
        a bare `pytest tests/` inside a job scoped to engine_py/ runs
        engine_py/tests/, never the repo-root tests/ this file lives in.
        Requires a job whose EFFECTIVE working directory is the repo root
        AND that has a step running pytest over a path covering
        tests/test_npm_install_hint.py (literal path or the bare tests/
        directory). Red today: the only root-scoped job (`manifests`) names
        its sibling test files literally and does not yet name this one;
        `pytest` runs `tests/` but is scoped to engine_py/."""
        text = CI_YML.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert isinstance(data, dict) and isinstance(data.get("jobs"), dict), (
            f"{CI_YML} does not parse as a well-formed YAML jobs document"
        )

        considered = []  # (job_name, verdict_reason) for every job examined
        covering_hits = []

        for job_name, job in data["jobs"].items():
            if not isinstance(job, dict):
                continue
            job_cwd = (job.get("defaults") or {}).get("run", {}).get("working-directory")
            steps = job.get("steps") or []
            run_steps = [
                step
                for step in steps
                if isinstance(step, dict) and isinstance(step.get("run"), str)
            ]
            if not run_steps:
                considered.append((job_name, "no run: steps"))
                continue

            job_hits = []
            job_cwds_seen = set()
            for step in run_steps:
                step_cwd = step.get("working-directory", job_cwd)
                job_cwds_seen.add(step_cwd)
                run_str = step["run"]
                if step_cwd not in (None, ".", ""):
                    # Effective cwd is not the repo root -- this step's
                    # relative paths cannot cover the repo-root tests/.
                    continue
                if re.search(r"pytest\b", run_str) and (
                    "tests/test_npm_install_hint.py" in run_str
                    or re.search(r"(?<![\w./])tests/(?:\s|$)", run_str)
                ):
                    job_hits.append(run_str)

            if job_hits:
                covering_hits.extend((job_name, hit) for hit in job_hits)
                considered.append((job_name, f"root-scoped pytest hit(s): {job_hits!r}"))
            else:
                considered.append((
                    job_name,
                    f"effective working-directory(s) seen={sorted(str(c) for c in job_cwds_seen)!r}, "
                    "no root-scoped pytest step covers tests/test_npm_install_hint.py",
                ))

        assert covering_hits, (
            "no job in ci.yml has an effective (working-directory-aware) "
            "root-scoped step running pytest over a path covering "
            "tests/test_npm_install_hint.py -- a new test file with no CI "
            "coverage repeats bd#11 (MAJOR-4); jobs considered and why each "
            f"was rejected: {considered!r}"
        )
