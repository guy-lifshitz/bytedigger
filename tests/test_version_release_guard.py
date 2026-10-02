"""RED tests for the release-version guard (bd#211).

`version_parity.py --check` only compares the declarations with each other;
nothing compares the canonical version with a release tag. Tag `v1.1.0`
shipped while every declaration said `0.2.0`. This suite drives the new
`--check-release [--tag TAG]` mode purely by subprocess (via the sibling
suite's `_run`); the UUT is never imported or mocked.

Per workflows.md section 1q nothing here resolves at import/collection time:
every failure happens at assert time with a clear message. The module-level
work done by `test_version_parity` (registry parse) is the sibling suite's
own, and is reused through a plain top-level import.

Tmp git repos are hermetic: `git init -q`, local user.name/email, lightweight
tags only, GIT_CONFIG_GLOBAL=/dev/null and GIT_CONFIG_NOSYSTEM=1.

What turns each negative AC red (the code change that must NOT be made):
- AC1: reverting the 1.1.1 bump of the declarations (tag v1.1.0 is then
  ahead of canonical), or the tag floor ignoring real tags.
- AC3: dropping the tag-floor comparison (or comparing with `<` instead of
  `>`), so an ahead tag is never reported.
- AC4: comparing tags/versions lexically (as strings) instead of as integer
  triples; "0.10.0" < "0.9.0" lexically, so a lexical compare passes it.
- AC5: counting non-semver tags (`v9.0`, `v9.0.0-rc1`, `release-9.0.0`) as
  release tags (dropping the `^v\\d+\\.\\d+\\.\\d+$` filter), or treating an
  equal tag as ahead (`>=` instead of `>`).
- AC6: skipping the `--tag` comparison, accepting a tag without the `v`
  prefix (no malformed check), or comparing only part of the string.
- AC7: swallowing a failed `git tag -l` as "zero tags" (fail-open) instead
  of reporting `git tags: unreadable`.
- AC8: allowing `--tag` without `--check-release`, or adding
  `--check-release` outside the mutually exclusive mode group.
- AC9: omitting `tags: ["v*"]`, `fetch-depth: 0`, the `--check-release` step
  with the ref name, or the pytest step for this file from ci.yml.
- AC10: returning after the first problem instead of collecting all of them.
- AC11: picking the "newest" tag by git's lexical listing order (last of
  `sorted(tags)`) instead of the integer maximum.
- AC12: comparing only major.minor (patch ignored).
- AC13: catching only a non-zero git exit and letting FileNotFoundError /
  OSError (git absent from PATH) escape as a Traceback or fail open.
- AC14: not aborting on an unreadable canonical (continuing with an
  undefined C), or int-parsing a non-X.Y.Z canonical (ValueError).
- AC9 env part: interpolating `${{ github.ref_name }}` straight into the
  `run:` string instead of passing it through a step `env:` variable.
All UUT/git subprocesses run through a local hermetic runner (GIT_DIR,
GIT_WORK_TREE, GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, GIT_COMMON_DIR scrubbed).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import test_version_parity as tvp

_SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


_SCRUB = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
)


def _git_env() -> dict:
    env = dict(os.environ)
    for key in _SCRUB:
        env.pop(key, None)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _uut(root, *args, env=None, python=None) -> subprocess.CompletedProcess:
    """Local hermetic runner for the UUT (tvp._run has no env parameter).
    Explicit --root, neutral cwd, scrubbed git env."""
    return subprocess.run(
        [python or tvp.sys.executable, str(tvp.SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tvp.NEUTRAL_CWD),
        env=env if env is not None else _git_env(),
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=_git_env(),
    )


def _git_ok(repo: Path, *args: str) -> None:
    result = _git(repo, *args)
    assert result.returncode == 0, (
        f"fixture git {' '.join(args)} failed: rc={result.returncode} "
        f"stderr={result.stderr!r}"
    )


def _uniform(version: str) -> dict:
    return {p: version for p in tvp.DECL_RELPATHS}


def _make_git_repo(tmp_path: Path, version: str, tags=()) -> Path:
    """Declarations all agreeing at `version`, committed in a fresh git repo,
    then each of `tags` created as a lightweight tag on that commit."""
    repo = tvp._make_tmp_repo(tmp_path, _uniform(version))
    _git_ok(repo, "init", "-q")
    _git_ok(repo, "config", "user.name", "Test User")
    _git_ok(repo, "config", "user.email", "test@example.com")
    _git_ok(repo, "config", "commit.gpgsign", "false")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-q", "-m", "fixture")
    for tag in tags:
        _git_ok(repo, "tag", tag)
    return repo


def _out(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


def _load_ci() -> dict:
    import yaml

    text = (tvp.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict) and "jobs" in parsed, (
        "ci.yml does not parse as a well-formed YAML jobs document"
    )
    return parsed


class TestVersionReleaseGuard:
    def test_ac1_real_repo_check_release_passes(self):
        """AC1 (production side effect): `--check-release --root REPO_ROOT`
        exits 0 on the real tree (the 1.1.1 bump makes canonical >= every
        release tag, incl. v1.1.0) and prints the OK line for the real
        canonical version."""
        canonical = tvp._canonical_version(tvp.REPO_ROOT)
        result = _uut(tvp.REPO_ROOT, "--check-release")
        assert result.returncode == 0, (
            f"--check-release on the real repo must exit 0, got "
            f"{result.returncode}, stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        assert f"OK: release version {canonical}" in result.stdout, (
            f"expected 'OK: release version {canonical}': {result.stdout!r}"
        )

    def test_ac2_no_tags_agreeing_declarations_passes(self, tmp_path):
        """AC2: tmp git repo, declarations agree at C, no tags -> exit 0 and
        stdout has `OK: release version C`."""
        C = "0.2.0"
        repo = _make_git_repo(tmp_path, C)
        result = _uut(repo, "--check-release")
        assert result.returncode == 0, (
            f"zero tags must be fine: rc={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert f"OK: release version {C}" in result.stdout, (
            f"missing OK line: {result.stdout!r}"
        )

    def test_ac3_incident_tag_ahead_of_canonical_fails_naming_both(self, tmp_path):
        """AC3: the incident -- C=0.2.0 with tag v1.1.0 -> exit 1, output
        names v1.1.0 and 0.2.0 in the frozen `git tags:` line."""
        C = "0.2.0"
        repo = _make_git_repo(tmp_path, C, tags=["v1.1.0"])
        result = _uut(repo, "--check-release")
        combined = _out(result)
        assert result.returncode == 1, (
            f"a tag ahead of canonical must fail: rc={result.returncode} "
            f"out={combined!r}"
        )
        assert f"git tags: newest release tag v1.1.0 is ahead of {C}" in combined, (
            f"expected the frozen ahead-of line naming v1.1.0 and {C}: "
            f"{combined!r}"
        )
        assert "Traceback" not in combined

    def test_ac4_integer_compare_not_lexical(self, tmp_path):
        """AC4: C=0.9.0 with tag v0.10.0 -> exit 1 (0.10.0 > 0.9.0 as integer
        triples; a lexical compare would let it pass)."""
        C = "0.9.0"
        repo = _make_git_repo(tmp_path, C, tags=["v0.10.0"])
        result = _uut(repo, "--check-release")
        combined = _out(result)
        assert result.returncode == 1, (
            f"v0.10.0 is ahead of 0.9.0 numerically and must fail: "
            f"rc={result.returncode} out={combined!r}"
        )
        assert "v0.10.0" in combined and C in combined, (
            f"output must name v0.10.0 and {C}: {combined!r}"
        )

    def test_ac5_equal_and_older_tags_pass_non_semver_tags_ignored(self, tmp_path):
        """AC5: tags v0.1.0 and v0.2.0 with C=0.2.0 -> exit 0 (equal is fine);
        non-semver tags v9.0, v9.0.0-rc1, release-9.0.0 alongside are
        ignored -> still exit 0."""
        C = "0.2.0"
        repo_a = _make_git_repo(tmp_path / "a", C, tags=["v0.1.0", "v0.2.0"])
        result_a = _uut(repo_a, "--check-release")
        assert result_a.returncode == 0, (
            f"equal/older tags must pass: rc={result_a.returncode} "
            f"out={_out(result_a)!r}"
        )
        assert f"OK: release version {C}" in result_a.stdout, (
            f"missing OK line: {result_a.stdout!r}"
        )

        repo_b = _make_git_repo(
            tmp_path / "b",
            C,
            tags=["v0.1.0", "v0.2.0", "v9.0", "v9.0.0-rc1", "release-9.0.0"],
        )
        # Precondition: the non-semver tags really exist in the fixture.
        listed = _git(repo_b, "tag", "-l").stdout.split()
        for name in ("v9.0", "v9.0.0-rc1", "release-9.0.0"):
            assert name in listed, f"fixture tag {name} missing: {listed!r}"
        assert not any(
            _SEMVER_TAG_RE.match(t) and t not in ("v0.1.0", "v0.2.0") for t in listed
        )
        result_b = _uut(repo_b, "--check-release")
        assert result_b.returncode == 0, (
            f"non-semver tags must be ignored: rc={result_b.returncode} "
            f"out={_out(result_b)!r}"
        )
        assert f"OK: release version {C}" in result_b.stdout, (
            f"missing OK line: {result_b.stdout!r}"
        )

    def test_ac6_tag_argument_match_mismatch_and_malformed(self, tmp_path):
        """AC6: --tag v0.2.0 with C=0.2.0 -> 0; --tag v0.2.1 -> 1 naming both;
        --tag 0.2.0 -> 1 with `malformed`."""
        C = "0.2.0"
        repo = _make_git_repo(tmp_path, C)

        ok = _uut(repo, "--check-release", "--tag", "v0.2.0")
        assert ok.returncode == 0, (
            f"--tag v0.2.0 must match C={C}: rc={ok.returncode} out={_out(ok)!r}"
        )
        assert f"OK: release version {C}" in ok.stdout, (
            f"missing OK line: {ok.stdout!r}"
        )

        bad = _uut(repo, "--check-release", "--tag", "v0.2.1")
        bad_out = _out(bad)
        assert bad.returncode == 1, (
            f"--tag v0.2.1 must fail: rc={bad.returncode} out={bad_out!r}"
        )
        assert f"--tag: v0.2.1 does not match {C}" in bad_out, (
            f"expected the frozen mismatch line naming v0.2.1 and {C}: "
            f"{bad_out!r}"
        )

        malformed = _uut(repo, "--check-release", "--tag", "0.2.0")
        mal_out = _out(malformed)
        assert malformed.returncode == 1, (
            f"--tag 0.2.0 (no v prefix) must fail: rc={malformed.returncode} "
            f"out={mal_out!r}"
        )
        assert "malformed" in mal_out, (
            f"the reason must say 'malformed': {mal_out!r}"
        )

    def test_ac7_root_not_a_git_repo_reports_tags_unreadable(self, tmp_path):
        """AC7: root is not a git repo -> exit 1 and `git tags: unreadable`.
        The tmp root must be truly outside any git repo (the UUT runs git
        itself), asserted explicitly so a false pass is impossible."""
        C = "0.2.0"
        repo = tvp._make_tmp_repo(tmp_path, _uniform(C))
        probe = _git(repo, "rev-parse", "--git-dir")
        assert probe.returncode != 0, (
            f"precondition broken: fixture root {repo} is inside a git "
            f"repo ({probe.stdout!r}); the not-a-repo case is not exercised"
        )
        result = _uut(repo, "--check-release")
        combined = _out(result)
        assert result.returncode == 1, (
            f"an unreadable tag list must fail closed: rc={result.returncode} "
            f"out={combined!r}"
        )
        assert "git tags: unreadable" in combined, (
            f"expected `git tags: unreadable`: {combined!r}"
        )
        assert "Traceback" not in combined

    def test_ac8_usage_errors_exit_2(self, tmp_path):
        """AC8: `--tag v1.0.0` without --check-release -> 2; `--check-release
        --write 1.0.0` -> 2. The messages must come from argparse's own
        rules for these flags, not from an 'unrecognized arguments' failure
        of a script that has no such mode."""
        repo = _make_git_repo(tmp_path, "0.2.0")

        lone_tag = _uut(repo, "--tag", "v1.0.0")
        assert lone_tag.returncode == 2, (
            f"--tag without --check-release must exit 2, got "
            f"{lone_tag.returncode}"
        )
        assert "unrecognized" not in lone_tag.stderr, (
            f"--tag must be a known option rejected by its own rule: "
            f"{lone_tag.stderr!r}"
        )
        assert "--tag" in lone_tag.stderr and "--check-release" in lone_tag.stderr, (
            f"the error must name --tag and --check-release: {lone_tag.stderr!r}"
        )

        both = _uut(repo, "--check-release", "--write", "1.0.0")
        assert both.returncode == 2, (
            f"--check-release with --write must exit 2, got {both.returncode}"
        )
        assert "not allowed with" in both.stderr, (
            f"expected argparse mutually-exclusive error: {both.stderr!r}"
        )
        # Nothing was written by the rejected invocation.
        assert tvp._parse_project_version(
            (repo / tvp.CANONICAL_RELPATH).read_text()
        ) == "0.2.0"

    def test_ac9_ci_yml_runs_release_guard(self):
        """AC9: ci.yml has on.push.tags containing `v*`; the `manifests`
        checkout has fetch-depth 0; a manifests step runs
        `version_parity.py --check-release` and references the tag ref name;
        a step runs pytest on this file."""
        parsed = _load_ci()

        # PyYAML parses the bare key `on` as boolean True; accept both.
        on_block = parsed.get("on", parsed.get(True))
        assert isinstance(on_block, dict), f"ci.yml has no `on:` mapping: {on_block!r}"
        push = on_block.get("push")
        assert isinstance(push, dict), f"ci.yml on.push is not a mapping: {push!r}"
        tags = push.get("tags")
        assert isinstance(tags, list) and "v*" in tags, (
            f"on.push.tags must contain 'v*': {tags!r}"
        )

        job = parsed["jobs"].get("manifests")
        assert isinstance(job, dict), "no `manifests` job in ci.yml"
        steps = job.get("steps")
        assert isinstance(steps, list) and steps, "manifests job has no steps"

        checkouts = [
            s for s in steps
            if isinstance(s, dict) and str(s.get("uses", "")).startswith("actions/checkout")
        ]
        assert checkouts, "manifests job has no actions/checkout step"
        assert any(
            str((c.get("with") or {}).get("fetch-depth")) == "0" for c in checkouts
        ), f"manifests checkout must set fetch-depth: 0 (tags present): {checkouts!r}"

        release_steps = [
            s for s in steps
            if isinstance(s, dict)
            and re.search(r"version_parity\.py[^\n]*--check-release", str(s.get("run", "")))
        ]
        assert release_steps, (
            "no manifests step runs `version_parity.py --check-release`"
        )
        def _env_carries_ref(step: dict) -> bool:
            env = step.get("env")
            return isinstance(env, dict) and any(
                "github.ref_name" in str(v) for v in env.values()
            )

        assert any(
            _env_carries_ref(s) and "${{" not in str(s.get("run", ""))
            for s in release_steps
        ), (
            f"the --check-release step must receive the tag ref name via a "
            f"step env: mapping referencing github.ref_name, with no "
            f"`${{{{` interpolation inside run: (script injection): "
            f"{release_steps!r}"
        )

        pytest_steps = [
            s for s in steps
            if isinstance(s, dict)
            and re.search(
                r"pytest[^\n]*tests/test_version_release_guard\.py",
                str(s.get("run", "")),
            )
        ]
        assert pytest_steps, (
            "no manifests step runs pytest on tests/test_version_release_guard.py"
        )

    def test_ac11_newest_tag_is_integer_max_over_several_tags(self, tmp_path):
        """AC11: tags v0.9.0 and v0.10.0, C=0.9.0 -> exit 1 and the output
        names v0.10.0 (an alphabetical 'newest' picks v0.9.0)."""
        C = "0.9.0"
        repo = _make_git_repo(tmp_path, C, tags=["v0.9.0", "v0.10.0"])
        result = _uut(repo, "--check-release")
        combined = _out(result)
        assert result.returncode == 1, (
            f"v0.10.0 is the newest tag and is ahead of {C}: "
            f"rc={result.returncode} out={combined!r}"
        )
        assert f"newest release tag v0.10.0 is ahead of {C}" in combined, (
            f"output must name v0.10.0 as the newest tag: {combined!r}"
        )

    def test_ac12_patch_component_counts(self, tmp_path):
        """AC12: C=0.2.0, tag v0.2.1 -> exit 1 (a major.minor-only compare
        lets it pass)."""
        C = "0.2.0"
        repo = _make_git_repo(tmp_path, C, tags=["v0.2.1"])
        result = _uut(repo, "--check-release")
        combined = _out(result)
        assert result.returncode == 1, (
            f"v0.2.1 is ahead of {C} by patch only and must fail: "
            f"rc={result.returncode} out={combined!r}"
        )
        assert "v0.2.1" in combined and C in combined, (
            f"output must name v0.2.1 and {C}: {combined!r}"
        )

    def test_ac13_git_absent_from_path_reports_tags_unreadable(self, tmp_path):
        """AC13: PATH without git (a dir holding only a `python3` symlink to
        sys.executable) -> exit 1, `git tags: unreadable`, no Traceback."""
        C = "0.2.0"
        repo = _make_git_repo(tmp_path / "fx", C)  # built WITH git, before PATH is stripped
        bindir = tmp_path / "nogit-bin"
        bindir.mkdir()
        (bindir / "python3").symlink_to(tvp.sys.executable)
        assert shutil.which("git", path=str(bindir)) is None, (
            f"precondition broken: git is resolvable on PATH={bindir}"
        )
        env = _git_env()
        env["PATH"] = str(bindir)
        result = _uut(repo, "--check-release", env=env, python=str(bindir / "python3"))
        combined = _out(result)
        assert result.returncode == 1, (
            f"git not runnable must fail closed: rc={result.returncode} "
            f"out={combined!r}"
        )
        assert "git tags: unreadable" in combined, (
            f"expected `git tags: unreadable`: {combined!r}"
        )
        assert "Traceback" not in combined, f"uncaught exception: {combined!r}"

    def test_ac14_unreadable_or_non_xyz_canonical_aborts_cleanly(self, tmp_path):
        """AC14: canonical file deleted -> exit 1 naming engine_py/pyproject.toml;
        canonical `version = "1.2"` -> exit 1 naming `1.2` and `X.Y.Z`."""
        canonical = tvp.CANONICAL_RELPATH
        repo_a = _make_git_repo(tmp_path / "a", "0.2.0")
        (repo_a / canonical).unlink()
        res_a = _uut(repo_a, "--check-release")
        out_a = _out(res_a)
        assert res_a.returncode == 1, (
            f"missing canonical must exit 1: rc={res_a.returncode} out={out_a!r}"
        )
        assert canonical in out_a, f"must name {canonical}: {out_a!r}"
        assert "Traceback" not in out_a

        repo_b = _make_git_repo(tmp_path / "b", "0.2.0")
        tvp._write_declaration(
            repo_b / canonical, tvp.DECL_KINDS[canonical], "1.2"
        )
        res_b = _uut(repo_b, "--check-release")
        out_b = _out(res_b)
        assert res_b.returncode == 1, (
            f"non-X.Y.Z canonical must exit 1: rc={res_b.returncode} out={out_b!r}"
        )
        assert "1.2" in out_b and "X.Y.Z" in out_b, (
            f"must name the offending value and X.Y.Z: {out_b!r}"
        )
        assert "Traceback" not in out_b

    def test_ac10_all_problems_reported_in_one_run(self, tmp_path):
        """AC10: C=0.2.0, tag v1.1.0, `--tag v3.0.0` -> exit 1 and stdout has
        BOTH the tag-floor line and the --tag mismatch line."""
        C = "0.2.0"
        repo = _make_git_repo(tmp_path, C, tags=["v1.1.0"])
        result = _uut(repo, "--check-release", "--tag", "v3.0.0")
        assert result.returncode == 1, (
            f"expected exit 1: rc={result.returncode} out={_out(result)!r}"
        )
        assert f"git tags: newest release tag v1.1.0 is ahead of {C}" in result.stdout, (
            f"tag-floor problem missing (run aborted on the other?): "
            f"{result.stdout!r}"
        )
        assert f"--tag: v3.0.0 does not match {C}" in result.stdout, (
            f"--tag problem missing (run aborted on the first?): "
            f"{result.stdout!r}"
        )
