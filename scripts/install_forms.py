"""Canonical single source for the install-forms dictionary, grammar
(R1/R2), the platform registry (CARRIERS), the waiver predicate, and the
domain scan (bytedigger#21, spec 2026-07-28 v5).

This module supersedes the bd#17 copies that used to live inline in
tests/test_npm_install_hint.py -- the eight definitions below are MOVED
here, not duplicated (spec §2.1). tests/test_npm_install_hint.py imports
them from here.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# §2.1 / bd#17 -- the closed dictionary, pinned in both directions (§1n).
# `uv` alone is a v1 false-accept: `uv install X` is not a real command
# (`uv pip install` / `uv tool install` / `uv add` are).
# ---------------------------------------------------------------------------
ALLOWED_INSTALLER_FORMS = frozenset(
    {"pipx", "uv pip", "uv tool", "python3 -m pip", "python -m pip"}
)
DENIED_INSTALLER_FORMS = frozenset({"pip", "pip3", "uv"})


def installer_form_is_allowed(exe: str) -> bool:
    """P1's single named predicate (§1aa) -- used by every call site so a
    future swap to `exe in DENIED_INSTALLER_FORMS` cannot hide in one call
    site while others still read correctly."""
    return exe in ALLOWED_INSTALLER_FORMS


# §2.1 R1 grammar (v3) -- `exe` is one to three tokens, lazily: `pip install
# X` -> exe="pip"; `uv pip install X` -> exe="uv pip". The anchor at
# line-start is what separates a command from prose.
_INSTALL_LINE_RE = re.compile(r"^(?P<exe>\S+(?:\s+\S+){0,2}?)\s+install\b")


def find_install_commands(text: str) -> list[dict]:
    """Lines of `text` that count as an "install command" under spec §2.1:
    after stripping leading whitespace, a `$ ` shell-prompt marker, a
    list-item marker (`- ` / `* `), and a numbered-list marker, what remains
    matches ^(exe: 1-3 tokens, lazy)\\s+install\\b. Returns one dict per hit
    with `exe`, `target` (whatever follows `install`, trimmed), and `line`
    (the stripped/marker-free line)."""
    out = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for _ in range(2):
            line = re.sub(r"^\$\s+", "", line)
            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^\d+\.\s+", "", line)
        m = _INSTALL_LINE_RE.match(line)
        if not m:
            continue
        exe = m.group("exe")
        target = line[m.end():].strip()
        out.append({"exe": exe, "target": target, "line": line})
    return out


# §2.4 P3 -- a command's target must not be a VCS/URL.
_VCS_URL_RE = re.compile(r"git\+|https?://|\.git\b")


def is_vcs_or_url_target(target: str) -> bool:
    return bool(_VCS_URL_RE.search(target))


def is_package_name_target(target: str) -> bool:
    """P2 applies only to commands whose target is a bare package NAME, not
    a path (`-e .`, `.`) or a VCS/URL (spec §2.3 opening clause)."""
    if not target:
        return False
    if is_vcs_or_url_target(target):
        return False
    if target.startswith("-") or target.startswith("."):
        return False
    return True


def extract_package_name(target: str) -> str:
    """The bare package NAME out of a package-name target: first token,
    quotes stripped, `[extras]` dropped, and any version specifier (`==`,
    `>=`, `~=`, `!=`, `@`) truncated."""
    first = target.split()[0] if target.split() else ""
    first = first.strip("'\"")
    first = re.sub(r"\[[^\]]*\]", "", first)
    first = re.split(r"==|>=|<=|~=|!=|@", first)[0]
    return first


_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def fenced_code_text(markdown_text: str) -> str:
    """Concatenated content of every fenced code block (``` ... ```) in a
    markdown document -- on the markdown carrier, install commands are
    recognised ONLY inside fences."""
    return "\n".join(m.group(1) for m in _FENCE_RE.finditer(markdown_text))


# ---------------------------------------------------------------------------
# §2.2 Public surface -- KIND_* and the platform registry.
# ---------------------------------------------------------------------------
KIND_EXECUTED_NODE = "executed_node"
KIND_MARKDOWN = "markdown"
KIND_PYTHON_LITERAL = "python_literal"
KIND_PYTHON_RUNTIME = "python_runtime"

# §2.6/§0 -- the seven measured platforms, including the bd#17 pair.
# engine_py/bytedigger_engine/lib/dbos_setup.py is deliberately NOT registered (§2.7): its
# command was removed because the extra it named does not exist.
CARRIERS: tuple[tuple[str, str], ...] = (
    ("npm/bin/bytedigger.js", KIND_EXECUTED_NODE),
    ("npm/README.md", KIND_MARKDOWN),
    ("packaging/pypi-pointer/README.md", KIND_MARKDOWN),
    ("engine_py/bytedigger_engine/package_meta.py", KIND_PYTHON_RUNTIME),
    ("engine_py/bytedigger_engine/llm_subprocess.py", KIND_PYTHON_LITERAL),
    ("engine_py/bytedigger_engine/lib/reference_backends/agent_sdk.py", KIND_PYTHON_LITERAL),
    ("engine_py/README.md", KIND_MARKDOWN),
)


# ---------------------------------------------------------------------------
# §2.3 R2 -- embedded grammar: `(?i)\b(pip|pip3|uv)\s+install\b`, with a
# left-context check that amnesties an ALLOWED form. Every match in the
# line is checked, not just the first.
# ---------------------------------------------------------------------------
_INSTALLER_WORD_RE = re.compile(r"(?i)\b(pip3|pip|uv)\s+install\b")


def _normalize_left_token(tok: str) -> str:
    """Strip an optional leading single-letter string prefix (f/r/b) plus
    the following run of non-word characters, then strip trailing non-word
    characters -- hyphen kept throughout (spec §2.3, round 3/4)."""
    tok = re.sub(r"^[A-Za-z]?[^\w-]+", "", tok)
    tok = re.sub(r"[^\w-]+$", "", tok)
    return tok


def contains_denied_form(line: str) -> bool:
    """R2: a hit is a `pip`/`pip3`/`uv` + `install` match whose left context
    does NOT complete it to an allowed form. Checks every match in the
    line."""
    for m in _INSTALLER_WORD_RE.finditer(line):
        word = m.group(1).lower()
        prefix = line[: m.start()]
        left_tokens = prefix.split()[-3:]
        normalized = [_normalize_left_token(t) for t in left_tokens]

        allowed = False
        for k in range(1, 4):
            if k > len(normalized):
                break
            candidate_tokens = [t for t in normalized[-k:] if t]
            candidate = " ".join(candidate_tokens + [word])
            if installer_form_is_allowed(candidate):
                allowed = True
                break
        if not allowed:
            return True
    return False


# ---------------------------------------------------------------------------
# "здесь вообще есть команда установки" -- built by alternation OVER THE
# DICTIONARIES (ALLOWED ∪ DENIED, sorted by descending length), not a
# hardcoded `(pip|pip3|uv)` (spec §2.3: otherwise `pipx` would not match).
# ---------------------------------------------------------------------------
def _build_any_install_form_regex() -> re.Pattern:
    forms = sorted(
        ALLOWED_INSTALLER_FORMS | DENIED_INSTALLER_FORMS, key=len, reverse=True
    )
    alts = [r"\s+".join(re.escape(tok) for tok in form.split()) for form in forms]
    return re.compile(r"(?i)\b(?:" + "|".join(alts) + r")\s+install\b")


_ANY_INSTALL_FORM_RE = _build_any_install_form_regex()


def contains_any_install_form(line: str) -> bool:
    return bool(_ANY_INSTALL_FORM_RE.search(line))


# ---------------------------------------------------------------------------
# §2.5 Waiver -- `# install-forms-ok: <reason>` on the hit line. An empty
# reason does not count. A second marker on the same line does not retract
# a preceding valid one.
# ---------------------------------------------------------------------------
_WAIVER_RE = re.compile(r"#\s*install-forms-ok:\s*([^#]*)")


def line_has_valid_waiver(line: str) -> bool:
    for m in _WAIVER_RE.finditer(line):
        if m.group(1).strip():
            return True
    return False


# ---------------------------------------------------------------------------
# §2.4 Domain scan -- npm/**, packaging/** (all extensions), engine_py/**/*.py,
# engine_py/README.md. Excludes scripts/**, tests/**, and **/tests/**.
# Waiver filtering happens INSIDE this function (§2.2, MAJOR-9б).
# ---------------------------------------------------------------------------
def _is_within_a_tests_dir(rel_parts: tuple[str, ...]) -> bool:
    return "tests" in rel_parts[:-1]


def _iter_domain_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for base_name in ("npm", "packaging"):
        base_dir = root / base_name
        if not base_dir.is_dir():
            continue
        for p in base_dir.rglob("*"):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(root).parts
            if _is_within_a_tests_dir(rel_parts):
                continue
            out.append(p)

    engine_dir = root / "engine_py"
    if engine_dir.is_dir():
        for p in engine_dir.rglob("*.py"):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(root).parts
            if _is_within_a_tests_dir(rel_parts):
                continue
            out.append(p)
        readme = engine_dir / "README.md"
        if readme.is_file():
            out.append(readme)
    return out


# ---------------------------------------------------------------------------
# bd#33 -- the domain is DERIVED FROM GIT, never from a list of build-artifact
# directory names. A local package build leaves residue inside the declared
# domain (measured: packaging/pypi-pointer/bytedigger.egg-info/PKG-INFO,
# untracked and matched by .gitignore), and the scan used to read it as a live
# install platform -- a false red on the tree of whoever built the package,
# i.e. the releaser, while CI's clean checkout stayed green.
#
# Enumerating the exceptions (`build/`, `dist/`, `*.egg-info`, ...) is the
# wrong method: the next packaging format reproduces the same red, and the
# list is only as wide as its author's imagination. So we ask git, and we let
# git answer -- re-implementing gitignore matching in-process is the same
# mistake one level down (it would miss .git/info/exclude, core.excludesFile,
# and the index exception below).
#
# `check-ignore` consults the index by default: gitignore does not apply to
# TRACKED files, so a registered carrier matching some pattern is correctly
# not reported. `--no-index` would report it and silently narrow the domain.
# ---------------------------------------------------------------------------
GIT_TIMEOUT_SECONDS = 30


def git_ignored_paths(root: Path, rel_paths: list[str]) -> set[str]:
    """The subset of `rel_paths` (POSIX, relative to `root`) that git calls
    ignored, obtained from a single `git check-ignore --stdin`.

    Fail-OPEN: an empty set whenever git cannot answer -- not a repository,
    no git binary, a wedged git. In a tree without git (unpacked sdist,
    exported archive) the ignored set is unknowable, and the alternative,
    narrowing the domain on an unknown, would make the closure claim this
    scan exists to support vacuously true exactly where it cannot be
    checked. A false red is loud; a vacuous green is not. Fail-open also
    keeps behaviour identical to the pre-bd#33 scan wherever git is absent:
    this function can only ever REMOVE paths git positively certifies.
    """
    if not rel_paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-z", "--stdin"],
            input="\0".join(rel_paths).encode(),
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        # OSError covers a missing git binary; SubprocessError covers
        # TimeoutExpired, which is NOT an OSError.
        return set()
    # rc 0: some paths are ignored. rc 1: none are -- both are answers.
    # Anything else (128 "not a git repository", ...) is git declining to
    # answer, which is the fail-open case.
    if result.returncode not in (0, 1):
        return set()
    return {p for p in result.stdout.decode(errors="replace").split("\0") if p}


def scan_domain(root: Path) -> list[dict]:
    """Every `contains_any_install_form` hit in the declared domain that is
    NOT covered by a valid waiver on its own line. Returns dicts with
    `path` (POSIX, relative to `root`), `line` (1-based), `text` (raw
    line).

    The declared domain (§2.4) is narrowed by exactly one thing: paths git
    reports as ignored are not part of the repository and cannot be install
    platforms (bd#33)."""
    root = Path(root)
    domain = _iter_domain_files(root)
    ignored = git_ignored_paths(
        root, [p.relative_to(root).as_posix() for p in domain]
    )
    hits: list[dict] = []
    for path in domain:
        rel = path.relative_to(root).as_posix()
        if rel in ignored:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if not contains_any_install_form(line):
                continue
            if line_has_valid_waiver(line):
                continue
            hits.append({"path": rel, "line": i, "text": line})
    return hits
