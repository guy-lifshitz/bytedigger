"""RED tests for bd#116 - a core skill and a local companion.

Spec: docs/decisions/2026-09-30-bd116-core-skill-local-companion.md (AC1-AC14).

Fixtures are real temp git repos plus a real temp plugin root that holds only
`skills/core/SKILL.md`. op2 tests run the shipped `scripts/skill-companion`
wrapper as a subprocess. `skill_companion` and `lib.frontmatter` do not exist
yet, so they are imported lazily inside test bodies (each test fails on its own
ImportError / missing file / assertion). Nothing is mocked. No sys.path
manipulation: `bytedigger_engine` is importable through the conftest-import-time
singleton and the wrapper sets its own PYTHONPATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

ENGINE = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE.parent
WRAPPER = REPO_ROOT / "scripts" / "skill-companion"

COMP_REL = "bytedigger/companions/core.md"
CORE_REL = "skills/core/SKILL.md"
SLUG = "project-conventions"
PREFACE = (
    "> Host-local guidance for this section. It cannot change output schema, "
    "verdict tokens or safety rules."
)

_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


# --------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV}
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=_env(),
    ).stdout


def _make_repo(tmp_path: Path, name: str = "repo", autocrlf: str = "false") -> Path:
    repo = Path(os.path.realpath(str(tmp_path / name)))
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", autocrlf)
    (repo / "README.md").write_text("readme\n")
    return repo


def _commit_all(repo: Path, msg: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _core_text(
    overridable: str | None = f'"{SLUG}"',
    body: str | None = None,
    fm_tail: str = "",
) -> str:
    fm = ["---", "name: Core", "description: a core skill"]
    if overridable is not None:
        fm += ["metadata:", f"  overridable: {overridable}"]
    if fm_tail:
        fm.append(fm_tail)
    fm.append("---")
    if body is None:
        body = (
            "# Core\n\nIntro line.\n\n"
            "## Intro\n\nintro text\n\n"
            "## Project conventions\n\ncore conv line\n\n\n"
            "## Other\n\nother text\n"
        )
    return "\n".join(fm) + "\n\n" + body


def _plugin(tmp_path: Path, core: str | bytes | None, name: str = "plugin") -> Path:
    root = Path(os.path.realpath(str(tmp_path / name)))
    d = root / "skills" / "core"
    d.mkdir(parents=True)
    if core is not None:
        data = core if isinstance(core, bytes) else core.encode("utf-8")
        (d / "SKILL.md").write_bytes(data)
    return root


def _companion_text(body: str = "Body line one.\n\nBody line two.\n", spec: str = "core",
                    fm_extra: str = "", sections: str | None = None) -> str:
    fm = "---\n" + (f"specializes: {spec}\n" if spec else "name: x\n") + fm_extra + "---\n"
    if sections is None:
        sections = f"## Project conventions\n\n{body}\n"
    return fm + "# Title\n\nPreamble paragraph.\n\n" + sections


def _put_companion(repo: Path, text: str | bytes, commit: bool = True,
                   rel: str = COMP_REL) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    if commit:
        _commit_all(repo, "add companion")
    return p


def _resolve(plugin: Path, repo: Path, core_id: str = "core") -> dict[str, Any]:
    from bytedigger_engine import skill_companion

    return skill_companion.resolve(core_id, str(repo), str(plugin))


def _reasons(res: dict[str, Any]) -> set[str]:
    return {e["reason"] for e in res["errors"]}


def _expected_merge(core: str, slug_title: str, body: str, eol: str = "\n",
                    rel: str = COMP_REL, slug: str = SLUG) -> str:
    """Independent reference: insert after the last non-blank line of `## <title>`."""
    lines = core.split(eol)
    start = len(lines) - 1 - lines[::-1].index(f"## {slug_title}")  # last occurrence
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^ {0,3}#{1,2} +", lines[i]):
            end = i
            break
    last = max(i for i in range(start, end) if lines[i].strip())
    block = [
        "",
        f"<!-- bd:local begin {slug} {rel} -->",
        PREFACE,
        "",
        *body.strip("\n").split("\n"),
        f"<!-- bd:local end {slug} -->",
    ]
    return eol.join(lines[: last + 1] + block + lines[last + 1:])


def _run(args: list[str], cwd: Path | None = None,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(WRAPPER), *args], cwd=cwd, capture_output=True,
        env=env if env is not None else _env(), timeout=60,
    )


def _plain_dir(tmp_path: Path, monkeypatch, name: str = "plain") -> Path:
    """A directory proven outside any git work tree (ceiling + rev-parse check)."""
    root = Path(os.path.realpath(str(tmp_path)))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(root))
    plain = root / name
    plain.mkdir()
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=plain, capture_output=True, env=_env(),
    )
    assert probe.returncode != 0, "fixture dir must not be inside a git work tree"
    return plain


def _cli(cmd: str, plugin: Path, repo: Path, *extra: str, core: str = "core") -> subprocess.CompletedProcess:
    return _run([cmd, "--core", core, "--plugin-root", str(plugin), "--repo", str(repo), *extra])


def _basic(tmp_path: Path, companion: str | bytes | None = None, core: str | None = None):
    core_txt = core if core is not None else _core_text()
    plugin = _plugin(tmp_path, core_txt)
    repo = _make_repo(tmp_path)
    _commit_all(repo, "init")
    if companion is not None:
        _put_companion(repo, companion)
    return plugin, repo, core_txt


# --------------------------------------------------------------------------
# AC1-AC3, AC6 - merge semantics
# --------------------------------------------------------------------------


def test_ac1_no_companion_text_equals_core_bytes(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    res = _resolve(plugin, repo)
    assert res["text"].encode("utf-8") == (plugin / CORE_REL).read_bytes()
    assert res["companion"] is None
    assert res["errors"] == []
    assert res["companion_sha256"] is None


def test_ac2_companion_section_appended_inside_markers(tmp_path):
    plugin, repo, core = _basic(tmp_path, _companion_text())
    res = _resolve(plugin, repo)
    assert res["errors"] == []
    assert res["companion"] is not None
    assert SLUG in res["sections"]
    expected = _expected_merge(core, "Project conventions", "Body line one.\n\nBody line two.")
    assert res["text"] == expected
    assert "Preamble paragraph." not in res["text"]
    assert "# Title" not in res["text"]
    # core bytes outside the insertion are unchanged and in order
    insertion = (
        "\n<!-- bd:local begin project-conventions " + COMP_REL + " -->\n"
        + PREFACE + "\n\nBody line one.\n\nBody line two.\n"
        + "<!-- bd:local end project-conventions -->\n"
    )
    assert res["text"].count(insertion) == 1
    assert res["text"].replace(insertion, "", 1) == core


def test_ac3_non_overridable_section_rejects_whole_companion(tmp_path):
    sections = (
        "## Project conventions\n\nvalid sibling\n\n"
        "## Other\n\nnot overridable\n"
    )
    plugin, repo, core = _basic(tmp_path, _companion_text(sections=sections))
    res = _resolve(plugin, repo)
    assert _reasons(res) == {"section_not_overridable"}
    assert res["text"] == core
    assert "valid sibling" not in res["text"]


def test_ac6_core_without_overridable_key_rejects_any_section(tmp_path):
    core = _core_text(overridable=None)
    plugin, repo, _ = _basic(tmp_path, _companion_text(), core=core)
    res = _resolve(plugin, repo)
    assert _reasons(res) == {"section_not_overridable"}
    assert res["text"] == core


# --------------------------------------------------------------------------
# AC4 - exact reason set per fixture
# --------------------------------------------------------------------------

_SEC = "## Project conventions\n\nbody text\n"
_CORE_DEFAULT = None  # use _core_text()

_AC4_ROWS = [
    # id, core text (None = default), companion text (None = none), expected reasons
    ("missing_specializes", None, _companion_text(spec=""), {"missing_specializes"}),
    ("specializes_mismatch", None, _companion_text(spec="other"), {"specializes_mismatch"}),
    ("duplicate_section", None,
     _companion_text(sections=_SEC + "\n" + _SEC), {"duplicate_section"}),
    ("invalid_section_title", None,
     _companion_text(sections="## !!!\n\nbody text\n"), {"invalid_section_title"}),
    ("heading_level_invalid", None,
     _companion_text(sections=_SEC + "\n# Bad H1\n\nmore\n"), {"heading_level_invalid"}),
    ("heading_level_invalid_indented", None,
     _companion_text(sections=_SEC + "\n  # Bad H1\n\nmore\n"), {"heading_level_invalid"}),
    ("setext_companion", None,
     _companion_text(sections="## Project conventions\n\nSome title\n==========\n"),
     {"setext_heading"}),
    ("setext_companion_indented_dashes", None,
     _companion_text(sections="## Project conventions\n\ntext\n  ---\n"), {"setext_heading"}),
    ("setext_core", _core_text(body="# Core\n\n## Project conventions\n\nx\n\n## Other\n\nSetext\n---\n"),
     None, {"setext_heading"}),
    ("unclosed_fence", None,
     _companion_text(sections="## Project conventions\n\n```\ncode\n"), {"unclosed_fence"}),
    ("forbidden_markup_open", None,
     _companion_text(sections="## Project conventions\n\nx <!-- y\n"), {"forbidden_markup"}),
    ("forbidden_markup_close", None,
     _companion_text(sections="## Project conventions\n\nx --> y\n"), {"forbidden_markup"}),
    ("empty_section", None,
     _companion_text(sections="## Project conventions\n\n\n\n"), {"empty_section"}),
    ("core_section_missing",
     _core_text(overridable='"project-conventions,nonexistent"'), None, {"core_section_missing"}),
    ("ambiguous_core_section",
     _core_text(body="# Core\n\n## Project conventions\n\na\n\n## Project  Conventions\n\nb\n"),
     None, {"ambiguous_core_section"}),
    ("invalid_overridable_empty", _core_text(overridable='""'), None,
     {"invalid_overridable_entry"}),
    ("invalid_overridable_trailing_comma", _core_text(overridable='"project-conventions,"'),
     None, {"invalid_overridable_entry"}),
    ("unsupported_frontmatter_core",
     "---\nname: Core\nmetadata: {overridable: x}\n---\n\n# Core\n\n## Project conventions\n\nx\n",
     None, {"unsupported_frontmatter"}),
    ("unsupported_frontmatter_companion", None,
     _companion_text(fm_extra="metadata: {verification: true}\n"), {"unsupported_frontmatter"}),
    ("companion_sets_verification", None,
     _companion_text(fm_extra="metadata:\n  verification: false\n"),
     {"companion_sets_verification"}),
    # negative row: frontmatter `---` directly under a key, no setext in the body
    ("core_frontmatter_close_under_key_is_not_setext",
     "---\nname: Core\nmetadata:\n  overridable: \"project-conventions\"\ndescription: d\n---\n\n"
     "# Core\n\n## Project conventions\n\ncore conv\n", None, set()),
]


@pytest.mark.parametrize(
    "core,companion,expected", [pytest.param(c, k, e, id=i) for i, c, k, e in _AC4_ROWS],
)
def test_ac4_exact_reason_set(tmp_path, core, companion, expected):
    core_txt = core if core is not None else _core_text()
    plugin, repo, _ = _basic(tmp_path, companion, core=core_txt)
    res = _resolve(plugin, repo)
    assert _reasons(res) == expected
    assert res["text"] == core_txt
    if core is not None and expected:
        core_errs = [e for e in res["errors"] if e["path"].endswith(CORE_REL)]
        assert core_errs, "core defects are reported on the core path"


def test_ac4_unknown_core_has_empty_text(tmp_path):
    plugin = _plugin(tmp_path, None)
    repo = _make_repo(tmp_path)
    _commit_all(repo, "init")
    res = _resolve(plugin, repo, core_id="missing")
    assert _reasons(res) == {"unknown_core"}
    assert res["text"] == ""


# --------------------------------------------------------------------------
# AC5 - fences, BOM + CRLF, core line endings
# --------------------------------------------------------------------------


def test_ac5_fenced_heading_in_core_is_not_a_section(tmp_path):
    body = (
        "# Core\n\n## Notes\n\n```\n## Project conventions\n```\n\n"
        "## Project conventions\n\ncore conv line\n\n## Other\n\nother\n"
    )
    core = _core_text(body=body)
    plugin, repo, _ = _basic(tmp_path, _companion_text(), core=core)
    res = _resolve(plugin, repo)
    assert res["errors"] == []
    assert res["text"] == _expected_merge(
        core, "Project conventions", "Body line one.\n\nBody line two.",
    )
    assert res["text"].count("<!-- bd:local begin") == 1


def test_ac5_only_fenced_heading_means_section_missing(tmp_path):
    body = "# Core\n\n## Notes\n\n~~~~\n## Project conventions\n~~~~\n"
    plugin, repo, core = _basic(tmp_path, None, core=_core_text(body=body))
    res = _resolve(plugin, repo)
    assert _reasons(res) == {"core_section_missing"}


def test_ac5_bom_crlf_companion_resolves_like_lf_twin(tmp_path):
    lf = _companion_text()
    (tmp_path / "lf").mkdir()
    (tmp_path / "bom").mkdir()
    twin_plugin, twin_repo, _ = _basic(tmp_path / "lf", lf)
    bom_crlf = b"\xef\xbb\xbf" + lf.replace("\n", "\r\n").encode("utf-8")
    plugin, repo, _ = _basic(tmp_path / "bom", bom_crlf)
    a = _resolve(twin_plugin, twin_repo)
    b = _resolve(plugin, repo)
    assert a["errors"] == [] and b["errors"] == []
    assert b["text"] == a["text"]
    assert b["sections"] == a["sections"]


def test_ac5_backtick_line_with_inline_backticks_is_not_a_fence(tmp_path):
    # CommonMark: a backtick fence's info string may not contain backticks.
    line = "```x``` is the syntax"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    # (a) in a companion section body: the following H2 must still be a section
    core_a = _core_text(
        overridable='"project-conventions,review-focus"',
        body="# Core\n\n## Project conventions\n\ncore conv\n\n## Review focus\n\ncore rf\n",
    )
    sections = f"## Project conventions\n\n{line}\n\n## Review focus\n\nrf body\n"
    plugin, repo, _ = _basic(tmp_path / "a", _companion_text(sections=sections), core=core_a)
    res = _resolve(plugin, repo)
    assert res["errors"] == []
    assert res["text"].count("<!-- bd:local begin") == 2
    assert "bd:local begin review-focus" in res["text"]
    assert line in res["text"]
    # (b) in the core, before the declared section: the section is still found
    core_b = _core_text(body=f"# Core\n\n{line}\n\n## Project conventions\n\ncore conv\n")
    plugin, repo, _ = _basic(tmp_path / "b", None, core=core_b)
    res = _resolve(plugin, repo)
    assert "core_section_missing" not in _reasons(res)
    assert res["errors"] == []


def test_ac5_crlf_core_every_inserted_line_ends_crlf(tmp_path):
    core = _core_text().replace("\n", "\r\n")
    plugin = _plugin(tmp_path, core.encode("utf-8"))
    repo = _make_repo(tmp_path)
    _commit_all(repo, "init")
    _put_companion(repo, _companion_text())
    res = _resolve(plugin, repo)
    assert res["errors"] == []
    assert res["text"] == _expected_merge(
        core, "Project conventions", "Body line one.\n\nBody line two.", eol="\r\n",
    )
    assert "\n" not in res["text"].replace("\r\n", "")


# --------------------------------------------------------------------------
# AC7 - CLI (shipped wrapper, real subprocess)
# --------------------------------------------------------------------------

_LINE_RE = re.compile(r"^E_SKILL_COMPANION_INVALID (\S+) (\S+)$")


def test_ac7_wrapper_is_executable():
    assert WRAPPER.is_file()
    assert os.access(WRAPPER, os.X_OK)


def test_ac7_render_valid_stdout_equals_resolve_text(tmp_path):
    plugin, repo, _ = _basic(tmp_path, _companion_text())
    assert os.access(WRAPPER, os.X_OK)
    cp = _cli("render", plugin, repo)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.decode("utf-8") == _resolve(plugin, repo)["text"]
    assert b"bd:local begin" in cp.stdout


def test_ac7_render_invalid_exit3_stderr_lines(tmp_path):
    sections = "## Project conventions\n\nok\n\n## Other\n\nnope\n"
    plugin, repo, _ = _basic(tmp_path, _companion_text(sections=sections))
    cp = _cli("render", plugin, repo)
    assert cp.returncode == 3
    assert cp.stdout == b""
    lines = [ln for ln in cp.stderr.decode().splitlines() if ln.strip()]
    assert lines and all(_LINE_RE.match(ln) for ln in lines), lines
    assert f"E_SKILL_COMPANION_INVALID section_not_overridable {COMP_REL}" in lines


def test_ac7_core_error_reported_on_plugin_relative_core_path(tmp_path):
    plugin, repo, _ = _basic(tmp_path, None, core=_core_text(overridable='"project-conventions,nope"'))
    cp = _cli("render", plugin, repo)
    assert cp.returncode == 3
    assert f"E_SKILL_COMPANION_INVALID core_section_missing {CORE_REL}" in cp.stderr.decode().splitlines()


def test_ac7_check_json_valid_has_sha_and_no_text(tmp_path):
    plugin, repo, _ = _basic(tmp_path, _companion_text())
    cp = _cli("check", plugin, repo, "--json")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert "text" not in data
    assert data["core"] == "core" and data["errors"] == []
    blob = subprocess.run(
        ["git", "show", f"HEAD:{COMP_REL}"], cwd=repo, capture_output=True, check=True, env=_env(),
    ).stdout
    assert data["companion_sha256"] == hashlib.sha256(blob).hexdigest()
    assert data["companion"] is not None
    assert SLUG in data["sections"]


def test_ac7_check_json_invalid_exit3_with_errors(tmp_path):
    plugin, repo, _ = _basic(tmp_path, _companion_text(spec="other"))
    cp = _cli("check", plugin, repo, "--json")
    assert cp.returncode == 3
    data = json.loads(cp.stdout)
    assert "text" not in data
    assert {e["reason"] for e in data["errors"]} == {"specializes_mismatch"}


def test_ac7_check_plain_valid_prints_nothing(tmp_path):
    plugin, repo, _ = _basic(tmp_path)
    cp = _cli("check", plugin, repo)
    assert cp.returncode == 0
    assert cp.stdout == b""


def test_ac7_usage_errors_exit_2(tmp_path):
    plugin, repo, _ = _basic(tmp_path)
    sub = repo / "sub"
    sub.mkdir()
    (sub / "f.txt").write_text("x\n")
    _commit_all(repo, "sub")
    cases = [
        _cli("render", plugin, repo, "--bogus"),
        _cli("render", plugin, repo, core="../x"),
        _cli("render", plugin, sub),
    ]
    for cp in cases:
        assert cp.returncode == 2, cp.stderr
        assert cp.stdout == b""


# --------------------------------------------------------------------------
# AC8 - companion must be committed
# --------------------------------------------------------------------------


def _not_committed(tmp_path, res, core):
    assert _reasons(res) == {"companion_not_committed"}
    assert res["text"] == core


def test_ac8_untracked(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    _put_companion(repo, _companion_text(), commit=False)
    _not_committed(tmp_path, _resolve(plugin, repo), core)


def test_ac8_committed_then_edited(tmp_path):
    plugin, repo, core = _basic(tmp_path, _companion_text())
    (repo / COMP_REL).write_text(_companion_text(body="edited body\n"))
    _not_committed(tmp_path, _resolve(plugin, repo), core)


def test_ac8_edited_and_staged(tmp_path):
    plugin, repo, core = _basic(tmp_path, _companion_text())
    (repo / COMP_REL).write_text(_companion_text(body="edited body\n"))
    _git(repo, "add", COMP_REL)
    _not_committed(tmp_path, _resolve(plugin, repo), core)


def test_ac8_edited_under_skip_worktree(tmp_path):
    plugin, repo, core = _basic(tmp_path, _companion_text())
    _git(repo, "update-index", "--skip-worktree", COMP_REL)
    (repo / COMP_REL).write_text(_companion_text(body="edited body\n"))
    _not_committed(tmp_path, _resolve(plugin, repo), core)


def test_ac8_committed_symlink_to_committed_file(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    _put_companion(repo, _companion_text(), commit=False, rel="bytedigger/companions/real.md")
    os.symlink("real.md", repo / COMP_REL)
    _commit_all(repo, "symlink")
    assert _git(repo, "ls-files", "-s", COMP_REL).startswith("120000")
    _not_committed(tmp_path, _resolve(plugin, repo), core)


def test_ac8_symlinked_companions_directory(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    _put_companion(repo, _companion_text(), commit=False, rel="elsewhere/core.md")
    (repo / "bytedigger").mkdir()
    os.symlink("../elsewhere", repo / "bytedigger" / "companions")
    _commit_all(repo, "dir symlink")
    assert _git(repo, "ls-files", "-s", "bytedigger/companions").startswith("120000")
    _not_committed(tmp_path, _resolve(plugin, repo), core)


def test_ac8_not_a_git_repo(tmp_path, monkeypatch):
    core = _core_text()
    plugin = _plugin(tmp_path, core)
    plain = _plain_dir(tmp_path, monkeypatch)
    _put_companion(plain, _companion_text(), commit=False)
    _not_committed(tmp_path, _resolve(plugin, plain), core)


def test_ac8_unborn_head(tmp_path):
    core = _core_text()
    plugin = _plugin(tmp_path, core)
    repo = _make_repo(tmp_path)
    _put_companion(repo, _companion_text(), commit=False)
    _git(repo, "add", COMP_REL)
    _not_committed(tmp_path, _resolve(plugin, repo), core)


def test_ac8_autocrlf_true_checkout_of_committed_lf_file_is_valid(tmp_path):
    core = _core_text()
    plugin = _plugin(tmp_path, core)
    repo = _make_repo(tmp_path, autocrlf="true")
    _commit_all(repo, "init")
    _put_companion(repo, _companion_text())
    (repo / COMP_REL).unlink()
    _git(repo, "checkout", "--", COMP_REL)
    assert b"\r\n" in (repo / COMP_REL).read_bytes(), "fixture must be a CRLF working tree"
    res = _resolve(plugin, repo)
    assert res["errors"] == []
    assert "bd:local begin project-conventions" in res["text"]


# --------------------------------------------------------------------------
# AC9 - nothing stale
# --------------------------------------------------------------------------


def test_ac9_deleted_companion_leaves_no_stale_text(tmp_path):
    plugin, repo, _ = _basic(tmp_path, _companion_text())
    first = _cli("render", plugin, repo)
    assert first.returncode == 0 and b"bd:local begin" in first.stdout
    (repo / COMP_REL).unlink()
    _commit_all(repo, "delete companion")
    second = _cli("render", plugin, repo)
    assert second.returncode == 0, second.stderr
    assert second.stdout == (plugin / CORE_REL).read_bytes()


# --------------------------------------------------------------------------
# AC10 - shipped core
# --------------------------------------------------------------------------


def test_ac10_shipped_core_declares_project_conventions():
    from bytedigger_engine.lib.frontmatter import parse_frontmatter

    text = (REPO_ROOT / "skills" / "bytedigger" / "SKILL.md").read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm is not None
    assert fm["metadata"]["overridable"] == SLUG
    assert len(re.findall(r"(?m)^## Project conventions[ \t]*$", text)) == 1


def test_ac10_real_plugin_root_check_exits_0():
    from helpers import live_repo

    live_repo.skip_without_git_checkout()
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=REPO_ROOT, capture_output=True,
        text=True, check=True, env=_env(),
    ).stdout.strip()
    cp = _run(["check", "--core", "bytedigger", "--plugin-root", str(REPO_ROOT), "--repo", top])
    assert cp.returncode == 0, cp.stderr


# --------------------------------------------------------------------------
# AC11 - `.claude/skills/<core>-local` is not read
# --------------------------------------------------------------------------


def test_ac11_claude_skills_local_skill_has_no_effect(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    p = repo / ".claude" / "skills" / "core-local" / "SKILL.md"
    p.parent.mkdir(parents=True)
    p.write_text(_companion_text())
    _commit_all(repo, "oz-style companion")
    res = _resolve(plugin, repo)
    assert res["text"] == core
    assert res["companion"] is None
    assert res["errors"] == []


# --------------------------------------------------------------------------
# AC12 - prompt contract
# --------------------------------------------------------------------------

LITERAL = (
    'BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; '
    '"${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger'
)
TAIL = 'skill-companion" render --core bytedigger'
OLD_FORM = "${CLAUDE_PLUGIN_ROOT:-"  # measured broken in plugin mode (spec s1 item 4)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, heading_re: str) -> str:
    m = re.search(heading_re, text, re.M)
    assert m, f"heading not found: {heading_re}"
    nxt = re.search(r"(?m)^## ", text[m.end():])
    return text[m.start(): m.end() + (nxt.start() if nxt else len(text))]


def _assert_rules(block: str, where: str) -> None:
    lines = block.splitlines()
    assert LITERAL in block, f"{where}: invocation literal missing"
    assert any("bd:local" in ln and "0" in ln for ln in lines), f"{where}: exit-0 rule"
    assert any("3" in ln and "STOP" in ln for ln in lines), f"{where}: exit-3 STOP rule"
    assert "W_SKILL_COMPANION_UNAVAILABLE" in block, f"{where}: fallback rule"
    assert OLD_FORM not in block, f"{where}: carries the measured-broken invocation"


def test_ac12_phase0_render_precedes_build_state():
    text = _read("phases/phase-0-classify.md")
    assert LITERAL in text
    assert text.index(TAIL) < text.index("Create build-state.yaml")
    assert OLD_FORM not in text


def test_ac12_resumable_renders_before_read():
    text = _read("commands/build.md")
    m = re.search(r"\*\*Resumable:\*\*.*?(?:\n\s*\n|\Z)", text, re.S)
    assert m
    para = m.group(0)
    assert TAIL in para
    assert OLD_FORM not in para
    first_read = re.search(r"\bread\b", para)
    assert first_read and para.index(TAIL) < first_read.start()


def test_ac12_build_md_phase0_rules():
    _assert_rules(_section(_read("commands/build.md"), r"^## PHASE 0"), "commands/build.md Phase 0")


def test_ac12_skill_load_pipeline_rules():
    _assert_rules(
        _section(_read("skills/bytedigger/SKILL.md"), r"^## CRITICAL: Load Pipeline"),
        "skills/bytedigger/SKILL.md Load Pipeline",
    )


def test_ac12_manual_install_example_rules():
    _assert_rules(_read("examples/claude-code-skill/SKILL.md"), "examples/claude-code-skill/SKILL.md")


# --------------------------------------------------------------------------
# AC13 - registries and docs
# --------------------------------------------------------------------------


def test_ac13_error_code_registered_everywhere():
    for rel in (
        "engine_py/bytedigger_engine/error_codes.py",
        "engine_py/ERROR_CODES.md",
        "engine_py/bytedigger_engine/ERROR_CODES.md",
    ):
        assert "E_SKILL_COMPANION_INVALID" in _read(rel), rel


def test_ac13_core_manifest_lists_module():
    assert "skill_companion.py" in _read("engine_py/core_manifest.json")


def test_ac13_configuration_docs():
    text = _read("docs/configuration.md")
    assert "bytedigger/companions/" in text
    assert "role_template_path" in text


def test_ac13_changelog_unreleased_mentions_116():
    from helpers.changelog import require_entry

    text = _read("CHANGELOG.md")
    require_entry(text, "**Skill companions (#116).**")


def test_ac13_core_boundary_lint_clean_with_new_module():
    assert (ENGINE / "bytedigger_engine" / "skill_companion.py").is_file()
    cp = subprocess.run(
        ["python3", str(REPO_ROOT / "core-boundary-lint.py")],
        cwd=REPO_ROOT, capture_output=True, text=True, env=_env(), timeout=120,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr


# --------------------------------------------------------------------------
# AC14 - shared parser
# --------------------------------------------------------------------------


def test_ac14_parser_lives_in_lib_and_is_reexported():
    from bytedigger_engine import verification_registry as vr
    from bytedigger_engine.lib import frontmatter as fmod

    assert vr.parse_frontmatter is fmod.parse_frontmatter
    assert vr.FrontmatterError is fmod.FrontmatterError


def test_ac14_discover_registers_bom_crlf_skill(tmp_path):
    from bytedigger_engine import verification_registry as vr

    repo = _make_repo(tmp_path)
    d = repo / ".claude" / "skills" / "x"
    d.mkdir(parents=True)
    text = "---\nname: x\ndescription: d\nmetadata:\n  verification: true\n---\n\n# body\n"
    (d / "SKILL.md").write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    _commit_all(repo, "skill")
    reg = vr.discover(repo)
    assert [s.name for s in reg.skills] == ["x"]


def test_ac14_unreadable_overridable_metadata_raises():
    from bytedigger_engine.lib.frontmatter import FrontmatterError, parse_frontmatter

    with pytest.raises(FrontmatterError):
        parse_frontmatter("---\nname: c\nmetadata: {overridable: x}\n---\n\n# c\n")


# --------------------------------------------------------------------------
# AC15 - production invocation shape, skip rules, edge cases (impl gate r1)
# --------------------------------------------------------------------------

SHIPPED_CORE = REPO_ROOT / "skills" / "bytedigger" / "SKILL.md"
BD_COMP_REL = "bytedigger/companions/bytedigger.md"
_BODY = "Body line one.\n\nBody line two."


def _shipped_literal() -> str:
    """The invocation exactly as the shipped skill file carries it."""
    m = re.search(
        r'BD_ROOT="\$\{CLAUDE_PLUGIN_ROOT\}"; "\$\{BD_ROOT:-\$BYTEDIGGER_HOME\}/scripts/'
        r'skill-companion" render --core bytedigger',
        SHIPPED_CORE.read_text(encoding="utf-8"),
    )
    assert m, "skills/bytedigger/SKILL.md does not carry the AC12 invocation literal"
    return m.group(0)


def _shipped_fixture(tmp_path: Path, with_companion: bool = True):
    core = SHIPPED_CORE.read_text(encoding="utf-8")
    repo = _make_repo(tmp_path)
    _commit_all(repo, "init")
    expected = core
    if with_companion:
        _put_companion(repo, _companion_text(spec="bytedigger"), rel=BD_COMP_REL)
        assert re.search(r"(?m)^## Project conventions[ \t]*$", core), (
            "shipped core has no `## Project conventions` section"
        )
        eol = "\r\n" if "\r\n" in core else "\n"
        expected = _expected_merge(core, "Project conventions", _BODY, eol=eol, rel=BD_COMP_REL)
    return repo, core, expected


def _env_with_plugin_root(value: str | None) -> dict[str, str]:
    env = _env()
    if value is not None:
        env["CLAUDE_PLUGIN_ROOT"] = value
    return env


@pytest.mark.parametrize("root_env", [None, ""], ids=["unset", "empty"])
def test_ac15_no_flags_no_companion_returns_shipped_core(tmp_path, root_env):
    repo, core, _ = _shipped_fixture(tmp_path, with_companion=False)
    cp = _run(["render", "--core", "bytedigger"], cwd=repo, env=_env_with_plugin_root(root_env))
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == SHIPPED_CORE.read_bytes()


@pytest.mark.parametrize("root_env", [None, ""], ids=["unset", "empty"])
def test_ac15_no_flags_companion_merges_into_shipped_empty_section(tmp_path, root_env):
    repo, core, expected = _shipped_fixture(tmp_path)
    cp = _run(["render", "--core", "bytedigger"], cwd=repo, env=_env_with_plugin_root(root_env))
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.decode("utf-8") == expected
    assert cp.stdout.count(b"<!-- bd:local begin project-conventions ") == 1


def test_ac15_nonempty_env_plugin_root_used(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    env = _env_with_plugin_root(str(plugin))
    cp = _run(["render", "--core", "core"], cwd=repo, env=env)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == (plugin / CORE_REL).read_bytes()


def test_ac15_flag_overrides_env(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    other = tmp_path / "other-dir"
    other.mkdir()
    env = _env_with_plugin_root(str(other))
    cp = _run(["render", "--core", "core", "--plugin-root", str(plugin)], cwd=repo, env=env)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == (plugin / CORE_REL).read_bytes()


def test_ac15_literal_in_plugin_mode_bash(tmp_path):
    literal = _shipped_literal()
    repo, _, expected = _shipped_fixture(tmp_path)
    cmd = literal.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO_ROOT))  # what Claude Code does
    env = _env()
    env.pop("BYTEDIGGER_HOME", None)
    cp = subprocess.run(["bash", "-c", cmd], cwd=repo, capture_output=True, env=env, timeout=60)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.decode("utf-8") == expected


def test_ac15_literal_in_manual_mode_bash(tmp_path):
    literal = _shipped_literal()
    repo, _, expected = _shipped_fixture(tmp_path)
    env = _env()
    env["BYTEDIGGER_HOME"] = str(REPO_ROOT)
    cp = subprocess.run(["bash", "-c", literal], cwd=repo, capture_output=True, env=env, timeout=60)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.decode("utf-8") == expected


def test_ac15_no_prompt_file_carries_broken_invocation():
    for rel in (
        "phases/phase-0-classify.md",
        "commands/build.md",
        "skills/bytedigger/SKILL.md",
        "examples/claude-code-skill/SKILL.md",
    ):
        text = _read(rel)
        assert LITERAL in text, f"{rel}: invocation literal missing"
        assert OLD_FORM not in text, f"{rel}: carries ${{CLAUDE_PLUGIN_ROOT:- form"


def _paragraphs(text: str) -> list[str]:
    paras: list[list[str]] = [[]]
    fence = None
    for ln in text.splitlines():
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", ln)
        if m:
            fence = None if fence and ln.strip().startswith(fence) else (fence or m.group(1)[0] * 3)
        if not ln.strip() and not fence:
            paras.append([])
        else:
            paras[-1].append(ln)
    return ["\n".join(p) for p in paras if p]


@pytest.mark.parametrize("rel", ["commands/build.md", "phases/phase-0-classify.md"])
def test_ac15_read_only_files_name_the_absolute_root(rel):
    allp = _paragraphs(_read(rel))
    idx = [i for i, p in enumerate(allp) if LITERAL in p]
    assert idx, f"{rel}: invocation literal missing"
    assert any(
        re.search(r"\babsolute\b", allp[j], re.I)
        for i in idx for j in (i - 1, i) if j >= 0
    ), f"{rel}: neither the literal's paragraph nor the one before it mentions the absolute root"


# ---- skip rules (spec op1 Evaluation order) ----


def test_ac15_unsupported_frontmatter_skips_later_frontmatter_checks(tmp_path):
    # no `specializes` at all, but the unreadable metadata is reported alone
    plugin, repo, core = _basic(
        tmp_path, _companion_text(spec="", fm_extra="metadata: {verification: true}\n"),
    )
    res = _resolve(plugin, repo)
    assert _reasons(res) == {"unsupported_frontmatter"}
    assert res["text"] == core


@pytest.mark.parametrize(
    "overridable,expected",
    [
        pytest.param('""', {"invalid_overridable_entry"}, id="invalid_entry"),
        pytest.param('"nonexistent"', {"core_section_missing"}, id="section_missing"),
    ],
)
def test_ac15_unusable_overridable_set_skips_stage_7(tmp_path, overridable, expected):
    core = _core_text(overridable=overridable)
    plugin, repo, _ = _basic(tmp_path, _companion_text(), core=core)
    res = _resolve(plugin, repo)
    assert _reasons(res) == expected  # no section_not_overridable
    assert res["text"] == core


def test_ac15_invalid_overridable_entry_is_not_also_section_missing(tmp_path):
    core = _core_text(overridable='"Bad_Entry,project-conventions"')
    plugin, repo, _ = _basic(tmp_path, None, core=core)
    res = _resolve(plugin, repo)
    assert _reasons(res) == {"invalid_overridable_entry"}


def test_ac15_not_committed_skips_stages_5_to_7(tmp_path):
    plugin, repo, core = _basic(tmp_path)
    bad = _companion_text(spec="other", sections="## Other\n\nx <!-- y\n\n## !!!\n\nz\n")
    _put_companion(repo, bad, commit=False)
    res = _resolve(plugin, repo)
    assert _reasons(res) == {"companion_not_committed"}
    assert res["text"] == core


# ---- CLI / resolve edge cases ----


def test_ac15_symlinked_repo_top_level_is_accepted(tmp_path):
    plugin, repo, _ = _basic(tmp_path, _companion_text())
    link = tmp_path / "link-to-repo"
    os.symlink(repo, link)
    assert os.path.realpath(link) == str(repo) and str(link) != str(repo)
    cp = _cli("render", plugin, link)
    assert cp.returncode == 0, cp.stderr
    assert b"bd:local begin" in cp.stdout


def test_ac15_no_repo_no_companion_cli_returns_core_bytes(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, _core_text())
    plain = _plain_dir(tmp_path, monkeypatch)
    cp = _cli("render", plugin, plain)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == (plugin / CORE_REL).read_bytes()


def test_ac15_ambient_git_env_decoys_do_not_change_resolve(tmp_path, monkeypatch):
    plugin, repo, _ = _basic(tmp_path, _companion_text())
    (tmp_path / "decoy_root").mkdir()
    decoy = _make_repo(tmp_path / "decoy_root", name="decoy")
    _commit_all(decoy, "decoy init")
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    res = _resolve(plugin, repo)
    assert res["errors"] == []
    assert "bd:local begin project-conventions" in res["text"]


def test_ac15_bom_core_keeps_bom_in_text(tmp_path):
    core = _core_text()
    plugin = _plugin(tmp_path, b"\xef\xbb\xbf" + core.encode("utf-8"))
    repo = _make_repo(tmp_path)
    _commit_all(repo, "init")
    _put_companion(repo, _companion_text())
    res = _resolve(plugin, repo)
    assert res["errors"] == []
    assert res["text"].encode("utf-8").startswith(b"\xef\xbb\xbf")
    assert res["text"] == "\N{ZERO WIDTH NO-BREAK SPACE}" + _expected_merge(
        core, "Project conventions", "Body line one.\n\nBody line two.",
    )
