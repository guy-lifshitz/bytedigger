"""RED tests for bd#44 — the engine ships as ONE package, not 56 top-level names.

Spec: docs/decisions/2026-08-03-bd44-package-namespace.md

Defect (measured by BUILDING the wheel, not by reading pyproject): the published
distribution lays 51 flat modules plus 5 directories straight into site-packages,
taking generic names — `contracts`, `config_provider`, `ctx_floor`, `engine`,
`io_utils`, `run`, and the directories `lib`, `scripts`, `security`, `workflows`.
A public package that owns `lib/` in site-packages will collide, silently, in an
order that depends on install order. Meanwhile neither `import bytedigger` nor
`import bytedigger_engine` exists, so an installed package offers no guessable
entry point.

Acceptance is a REAL INSTALL, not a green CI: these tests build the wheel from the
repository and install it into a throwaway venv created with `--without-pip`-free
defaults, then interrogate what actually landed.

Cost note: building + installing takes tens of seconds, so the wheel is built ONCE
per session and shared (module-scoped fixture). Every assertion below reads the
artifact a user would get.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine_py"

# Measured on the pre-move wheel (spec §0). These are the names a public package
# must not own at top level.
# Names a venv legitimately carries regardless of our distribution. Matched
# EXACTLY, not by prefix — a prefix filter would hide a module called e.g.
# "pipeline" that we shipped by accident.
VENV_OWN = {"pip", "setuptools", "pkg_resources", "_distutils_hack",
            "distutils-precedence.pth", "__pycache__", "wheel"}

# Measured on the pre-move wheel by running `bytedigger-engine --list` in a clean
# venv (spec §1c.1): exactly 21, rc=0. This is a live baseline, not a number read
# off the issue.
EXPECTED_WORKFLOWS = 21


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900, **kw)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    """Build the real wheel from the repository."""
    out = tmp_path_factory.mktemp("wheel")
    # `pip wheel .` builds IN-TREE and leaves `engine_py/build/` behind — a second,
    # stale copy of every module. Other tests walk the tree and then see each site
    # twice (that is what turned `gh1220::ac37` and `gh795::ac10` red in CI, and it
    # was this test that put the residue there). So the build happens on a COPY,
    # outside the repository, and the working tree is never written to.
    import shutil as _sh
    src = tmp_path_factory.mktemp("src") / "engine_py"
    _sh.copytree(
        ENGINE, src,
        ignore=_sh.ignore_patterns("build", "__pycache__", "*.egg-info", ".venv", "tests"),
    )
    proc = _run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-q", "-w", str(out)],
                cwd=str(src))
    assert proc.returncode == 0, f"wheel build failed:\n{proc.stderr[-2000:]}"
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def venv(tmp_path_factory, wheel: Path) -> Path:
    """A CLEAN venv with the wheel installed — the acceptance surface."""
    root = tmp_path_factory.mktemp("venv") / "v"
    proc = _run([sys.executable, "-m", "venv", str(root)])
    assert proc.returncode == 0, f"venv creation failed:\n{proc.stderr[-2000:]}"
    pip = root / "bin" / "pip"
    # --no-deps: the fixture must not need the network, or a setup-time ERROR would
    # masquerade as a failing assertion (§1q).
    proc = _run([str(pip), "install", "-q", "--no-deps", str(wheel)])
    assert proc.returncode == 0, f"install failed:\n{proc.stderr[-2000:]}"
    return root


def _site_packages(venv_root: Path) -> Path:
    matches = list((venv_root / "lib").glob("python*/site-packages"))
    assert len(matches) == 1, f"could not locate site-packages: {matches}"
    return matches[0]


def _top_level(venv_root: Path) -> list[str]:
    """Everything the distribution added at the top level of site-packages."""
    return sorted(
        e.name for e in _site_packages(venv_root).iterdir()
        if e.name not in VENV_OWN and not e.name.endswith(".dist-info")
    )


# ── AC1 — the guessable entry point exists ─────────────────────────────────


def test_ac1_import_bytedigger_engine_works(venv: Path):
    """`pip install` then `import bytedigger_engine` — today a ModuleNotFoundError."""
    proc = _run([str(venv / "bin" / "python"), "-c", "import bytedigger_engine"])
    assert proc.returncode == 0, (
        "AC1: an installed package must offer a guessable import; "
        f"`import bytedigger_engine` failed:\n{proc.stderr[-1500:]}"
    )


# ── AC2 — the user-facing CLI does not regress ─────────────────────────────


def test_ac2_console_script_still_lists_21_workflows(venv: Path):
    """The console script is the shipped interface — the move must not touch it."""
    proc = _run([str(venv / "bin" / "bytedigger-engine"), "--list"])
    assert proc.returncode == 0, f"AC2: `--list` exited {proc.returncode}:\n{proc.stderr[-1500:]}"
    # Isolate the JSON payload: a stray banner line must not decide this AC.
    line = next((l for l in proc.stdout.splitlines() if l.lstrip().startswith("[")), None)
    assert line is not None, f"AC2: no JSON array on stdout:\n{proc.stdout[-800:]}"
    names = json.loads(line)
    assert len(names) == EXPECTED_WORKFLOWS, (
        f"AC2: expected {EXPECTED_WORKFLOWS} workflows (live baseline measured on the "
        f"pre-move wheel), got {len(names)}: {names}"
    )


# ── AC3 — the subject: the distribution owns exactly ONE top-level name ────


def _distribution_top_level(wheel_path: Path) -> list[str]:
    """What the DISTRIBUTION itself claims to own, read from the wheel."""
    with zipfile.ZipFile(wheel_path) as z:
        names = z.namelist()
        tl = [n for n in names if n.endswith(".dist-info/top_level.txt")]
        if tl:
            return sorted(x for x in z.read(tl[0]).decode().split() if x)
        return sorted({n.split("/")[0] for n in names if not n.endswith(".dist-info")})


def test_ac3_distribution_owns_exactly_one_top_level_name(wheel: Path, venv: Path):
    """rev1 checked a whitelist of 13 measured names; an implementation that left
    the other 43 generic names in place passed it. 'None of those listed' is not
    'does not own the top level'."""
    claimed = _distribution_top_level(wheel)
    assert claimed == ["bytedigger_engine"], (
        f"AC3: the distribution must own exactly one top-level name; it claims {claimed}"
    )

    # and the same, observed on the installed side rather than on the metadata
    installed = _top_level(venv)
    assert installed == ["bytedigger_engine"], (
        f"AC3: site-packages carries more than the package: {installed}"
    )


def test_ac3b_the_package_is_real_not_a_namespace_stub(venv: Path):
    """An empty directory would satisfy AC3 — so the package must be substantial."""
    pkg = _site_packages(venv) / "bytedigger_engine"
    assert (pkg / "__init__.py").is_file(), "AC3b: no __init__.py — namespace stub"
    mods = list(pkg.rglob("*.py"))
    assert len(mods) >= 40, f"AC3b: only {len(mods)} modules moved into the package"


# ── AC4 — non-empty corpus, measured from the artifact ─────────────────────


def test_ac4_corpus_is_measured_not_assumed(venv: Path):
    """'No collisions' over an empty listing proves nothing. Both quantities come
    from the installed artifact — rev1 compared two of its own constants."""
    top = _top_level(venv)
    pkg = _site_packages(venv) / "bytedigger_engine"
    mods = list(pkg.rglob("*.py")) if pkg.is_dir() else []
    print(f"bd44 corpus: {len(top)} top-level entries {top}; {len(mods)} modules in package")
    assert len(top) > 0
    assert len(mods) > 0


# ── AC6 — the breaking change is declared EVERYWHERE it is declared ────────


def test_ac6_version_is_declared_breaking_and_in_parity(wheel: Path):
    """Four sites declare the version and `scripts/version_parity.py` polices them.
    Bumping only pyproject leaves `pip install bytedigger` — the pointer package —
    serving 0.1.2, which is the documented 0.1.0->0.1.1 incident all over again."""
    with zipfile.ZipFile(wheel) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        text = z.read(meta).decode()
    version = next(l.split(": ", 1)[1].strip() for l in text.splitlines()
                   if l.startswith("Version: "))
    assert version.startswith("0.2."), f"AC6: expected 0.2.x, wheel says {version}"

    parity = _run([sys.executable, "scripts/version_parity.py"], cwd=str(REPO))
    assert parity.returncode == 0, (
        f"AC6: version parity failed:\n{parity.stdout[-1200:]}{parity.stderr[-1200:]}"
    )

    pointer = (REPO / "packaging" / "pypi-pointer" / "pyproject.toml").read_text()
    assert "bytedigger-engine==0.2." in pointer, (
        "AC6: the pointer package still pins a pre-move engine version"
    )


# ── AC7 — the runtime path hack is gone (counted by AST, not by substring) ──


def test_ac7_installed_package_makes_no_sys_path_calls(venv: Path):
    """Measured on main: 63 sys.path.insert/append CALLS across 42 files — the entry
    point is only two of them. Leaving any in place lets the package keep working by
    path hack while the collision class survives.

    Counted by parsing the AST: the same characters appear as STRING LITERALS in the
    product's own prompt text (7 occurrences in 3 files), so a substring check would
    demand editing the product to satisfy a test."""
    import ast as _ast

    pkg = _site_packages(venv) / "bytedigger_engine"
    # Non-empty corpus, or this AC passes by iterating over nothing — the very
    # trap this lot's own spec warns about.
    assert pkg.is_dir(), "AC7: no bytedigger_engine/ to scan — nothing was checked"
    scanned = list(pkg.rglob("*.py"))
    assert len(scanned) >= 40, f"AC7: only {len(scanned)} file(s) to scan"
    offenders: list[str] = []
    literals = 0
    for f in scanned:
        try:
            tree = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # a file we cannot parse is NOT a file we cleared
            offenders.append(f"{f.name}: unparseable — not scanned")
            continue
        # `from sys import path` makes a BARE `path` the real thing; without that
        # import, a local named `path` is just a variable.
        # Per-file aliases. rev4 was blind to two spellings the repo already uses:
        #   `from sys import path; path.insert(…)`   — Call base is a Name, not Attribute
        #   `import sys as _sys; _sys.path[:0] = […]` — Assign required Name('sys') exactly
        # Bound NAMES, not the literal "path": `from sys import path as p` binds `p`.
        bare_path_names = {
            (a.asname or a.name)
            for n in _ast.walk(tree) if isinstance(n, _ast.ImportFrom) and n.module == "sys"
            for a in n.names if a.name == "path"
        }
        sys_aliases = {"sys"} | {
            a.asname or a.name
            for n in _ast.walk(tree) if isinstance(n, _ast.Import)
            for a in n.names if a.name == "sys"
        }
        for node in _ast.walk(tree):
            # Every spelling, not just `sys.path.insert(...)`: rev2 was blind to
            # `.extend`, `sys.path[:0] = …`, `sys.path += …`, `from sys import path`
            # and `site.addsitedir`, and a GREEN using any of them passed the corpus
            # with zero imports rewritten.
            if (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                    and node.func.attr in ("insert", "append", "extend")
                    and isinstance(node.func.value, _ast.Attribute)
                    and node.func.value.attr == "path"):
                offenders.append(f"{f.name}:{node.lineno} sys.path.{node.func.attr}")
            elif (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                    and node.func.attr in ("insert", "append", "extend")
                    and isinstance(node.func.value, _ast.Name)
                    and node.func.value.id in bare_path_names):
                offenders.append(f"{f.name}:{node.lineno} path.{node.func.attr} (from sys import path)")
            elif (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                    and node.func.attr == "addsitedir"):
                offenders.append(f"{f.name}:{node.lineno} site.addsitedir")
            elif isinstance(node, (_ast.Assign, _ast.AugAssign)):
                # Bound to `sys` SPECIFICALLY. rev3 flagged any `path = …` and any
                # `.path = …`, which is every Path-handling line in the engine —
                # a false red on any implementation, and a recidivism of exactly
                # the literal-vs-code trap §1c.5 warns about.
                targets = node.targets if isinstance(node, _ast.Assign) else [node.target]
                for tgt in targets:
                    base = tgt.value if isinstance(tgt, _ast.Subscript) else tgt
                    is_sys_path = (
                        isinstance(base, _ast.Attribute) and base.attr == "path"
                        and isinstance(base.value, _ast.Name)
                        and base.value.id in sys_aliases
                    )
                    is_imported_path = (
                        isinstance(base, _ast.Name) and base.id in bare_path_names
                    )
                    if is_sys_path or is_imported_path:
                        offenders.append(f"{f.name}:{node.lineno} sys.path assignment")
            if (isinstance(node, _ast.Constant) and isinstance(node.value, str)
                    and "sys.path.insert" in node.value):
                literals += 1
    print(f"bd44 AC7: scanned {len(scanned)} file(s); {literals} string-literal mention(s) ignored by design")
    assert offenders == [], f"AC7: installed package still calls sys.path: {offenders}"


def test_ac7b_flat_generic_names_do_not_resolve(venv: Path):
    """The observable consequence, probed on the RIGHT side: rev1 probed
    `phase_2_explore` (a workflows/ module), so an implementation that hid only
    workflows/ while leaving the flat generic names passed it."""
    code = (
        "import bytedigger_engine\n"
        "import importlib\n"
        "leaked = []\n"
        "for m in ('contracts', 'config_provider', 'ctx_floor', 'io_utils', 'phase_2_explore'):\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except ModuleNotFoundError:\n"
        "        pass\n"
        "    else:\n"
        "        leaked.append(m)\n"
        "raise SystemExit('leaked top-level: %s' % leaked if leaked else 0)\n"
    )
    proc = _run([str(venv / "bin" / "python"), "-c", code])
    assert proc.returncode == 0, f"AC7b: {proc.stdout[-500:]}{proc.stderr[-800:]}"


# ── AC8 — the string-named imports moved with everything else ──────────────


def test_ac8_doctor_string_named_modules_resolve(venv: Path):
    """`doctor.py` holds module names as STRINGS and `__import__`s them, which no
    AST-based rewrite can see. If they were missed, the failure surfaces in the
    user's diagnostic tool at runtime rather than in any test."""
    code = (
        "from bytedigger_engine.doctor import check_gates_importable, check_engine_imports\n"
        "r = [check_gates_importable(), check_engine_imports()]\n"
        "print(getattr(r, 'detail', r))\n"
        "raise SystemExit(0 if 'missing module' not in str(r) else 'gates unimportable: %s' % (r,))\n"
    )
    proc = _run([str(venv / "bin" / "python"), "-c", code])
    assert proc.returncode == 0, (
        f"AC8: doctor's string-named gate modules did not move:\n"
        f"{proc.stdout[-800:]}{proc.stderr[-1500:]}"
    )


# ── AC9 — the declared package data travels with the package ───────────────


# Measured on the SOURCE TREE at 385b1ed, before the move (spec §1b): the three
# declared package-data roots hold this many asset files. The reference is the
# measurement, NOT `[tool.setuptools.package-data]` — GREEN rewrites that very
# block in the chokepoint file, so checking against it lets a deleted key pass.
ASSET_BASELINE = {
    "lib/plugins/anti_hallucination": 3,
    "security": 5,
    "scripts/red_lint": 1,
}


def test_ac9_declared_package_data_actually_travels(wheel: Path):
    """Every asset root measured before the move must still deliver at least as
    many files, and all of them under the package."""
    with zipfile.ZipFile(wheel) as z:
        names = [n for n in z.namelist() if "dist-info" not in n]
    assets = [n for n in names if n.endswith((".yaml", ".yml", ".md", ".toml", ".txt"))]
    assert assets, "AC9: the wheel carries no package data at all"

    stray = [a for a in assets if not a.startswith("bytedigger_engine/")]
    assert stray == [], f"AC9: package data landed outside the package: {stray[:10]}"

    short = {}
    for root, expected in ASSET_BASELINE.items():
        prefix = f"bytedigger_engine/{root}/"
        got = len([a for a in assets if a.startswith(prefix)])
        if got < expected:
            short[root] = f"{got} < {expected}"
    print(f"bd44 AC9: {len(assets)} data file(s); baseline roots {list(ASSET_BASELINE)}")
    assert short == {}, f"AC9: declared asset roots lost files: {short}"


# ── AC10 — the documented library API in examples/ still works ─────────────


def test_ac10_public_examples_import_through_the_package(venv: Path):
    """`examples/**` are the documented way to use the library — a public surface,
    exactly like the console script.

    rev2 reproduced the whitelist defect this very lot is about: its 11-name list
    missed `event_sink` and `derive_state`, and it only looked at
    `examples/library/`. The name set is now DERIVED from what actually shipped,
    and the corpus is every example."""
    import ast as _ast

    pkg = _site_packages(venv) / "bytedigger_engine"
    assert pkg.is_dir(), "AC10: nothing installed — cannot derive the name set"
    engine_names = {f.stem for f in pkg.glob("*.py")} | {
        d.name for d in pkg.iterdir() if d.is_dir() and d.name != "__pycache__"
    }
    engine_names.discard("__init__")
    assert len(engine_names) >= 40, f"AC10: derived only {len(engine_names)} names"

    examples = sorted((REPO / "examples").rglob("*.py"))
    assert examples, "AC10: no examples found — corpus is empty"

    offenders: list[str] = []
    for f in examples:
        try:
            tree = _ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in engine_names:
                    offenders.append(f"{f.relative_to(REPO)}:{node.lineno} from {node.module}")
            elif isinstance(node, _ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in engine_names:
                        offenders.append(f"{f.relative_to(REPO)}:{node.lineno} import {a.name}")
    print(f"bd44 AC10: {len(examples)} example(s) vs {len(engine_names)} shipped name(s)")
    assert offenders == [], (
        f"AC10: public examples still import engine names at top level: {offenders[:10]}"
    )


# ── AC11 — the move ITSELF is forced, not just the shape of the tree ───────


def test_ac11_installed_modules_do_not_import_siblings_by_bare_name(venv: Path):
    """The decisive AC. Every other check constrains the SHAPE of the installed
    tree, and a `package_dir` remap plus a differently-spelled path hack satisfies
    all of them with ZERO of the 758 intra-package imports rewritten — the package
    would still resolve its own modules through the top level.

    So the imports themselves are asserted: no module inside the package may import
    a sibling by a bare top-level name. Packaging tricks cannot fake this."""
    import ast as _ast

    pkg = _site_packages(venv) / "bytedigger_engine"
    assert pkg.is_dir(), "AC11: no package to scan"
    files = list(pkg.rglob("*.py"))
    assert len(files) >= 40, f"AC11: only {len(files)} file(s) — corpus too small"

    # Recursive, not depth-1: rev3 missed `model_config`, `plugins.*`, `util.*`,
    # so a bare `from util.x import y` inside the package went unseen.
    siblings = {f.stem for f in pkg.rglob("*.py")} | {
        d.name for d in pkg.rglob("*") if d.is_dir() and d.name != "__pycache__"
    }
    siblings.discard("__init__")

    offenders: list[str] = []
    for f in files:
        try:
            tree = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            offenders.append(f"{f.name}: unparseable")
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in siblings:
                    offenders.append(f"{f.relative_to(pkg)}:{node.lineno} from {node.module}")
            elif isinstance(node, _ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in siblings:
                        offenders.append(f"{f.relative_to(pkg)}:{node.lineno} import {a.name}")
    print(f"bd44 AC11: scanned {len(files)} file(s) against {len(siblings)} sibling name(s)")
    assert offenders == [], (
        f"AC11: {len(offenders)} bare sibling import(s) survive (first 10): {offenders[:10]}"
    )


def test_ac11b_cli_invocation_does_not_widen_sys_path(venv: Path, tmp_path: Path):
    """The hacks live on the path the CONSOLE SCRIPT takes, so the moment of
    measurement decides. This AC went vacuous in FOUR different ways across four
    rounds, which says the shape was wrong rather than the details:

      1. `run.main(['--list'])` — `main()` takes no parameters; TypeError into a
         bare `except`, nothing ran;
      2. run under `runpy`, but `hard_exit` calls `os._exit(0)` — the interpreter
         dies before any in-process assertion;
      3. "at least 20 imports" — satisfied by `json` + `runpy` pulling in the
         stdlib before the module fails to load;
      4. the `sys.path` leg still compared two snapshots, the second of which was
         written past the `os._exit` point — and `if p0 and p1:` turned missing
         evidence into clean evidence.

    Everything therefore travels one STREAMED, line-buffered channel: each import
    is logged WITH the `sys.path` in force at that moment, and the produced
    workflow list is logged too. `os._exit` cannot erase what is already flushed,
    and absence of evidence is treated as failure, never as a pass."""
    log = tmp_path / "probe.log"
    code = (
        "import sys, json, runpy\n"
        f"log = open({str(log)!r}, 'w', buffering=1)\n"
        "def emit(kind, payload):\n"
        "    try:\n"
        "        log.write(kind + ' ' + json.dumps(payload) + '\\n')\n"
        "    except Exception as e:\n"
        "        sys.stderr.write('PROBE-WRITE-FAILED %r\\n' % (e,))\n"
        "emit('PATH', sys.path)\n"
        "def hook(event, args):\n"
        "    if event == 'import':\n"
        "        emit('IMPORT', [str(args[0]), list(sys.path)])\n"
        "sys.addaudithook(hook)\n"
        "sys.argv = ['bytedigger-engine', '--list']\n"
        "class Tee:\n"
        "    def __init__(self, s): self.s = s\n"
        "    def write(self, d):\n"
        "        emit('OUT', d)\n"   # every write, whitespace included: dropping them\n"
        "        return self.s.write(d)\n"
        "    def flush(self): self.s.flush()\n"
        "sys.stdout = Tee(sys.stdout)\n"
        "try:\n"
        "    runpy.run_module('bytedigger_engine.run', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "emit('DONE', True)\n"
    )
    probe = _run([str(venv / "bin" / "python"), "-c", code])
    assert "PROBE-WRITE-FAILED" not in probe.stderr, (
        f"AC11b: the evidence channel itself failed: {probe.stderr[-500:]}"
    )

    assert log.exists(), (
        f"AC11b: the probe produced no log — nothing was measured "
        f"(rc={probe.returncode}): {probe.stderr[-500:]}"
    )
    recs = []
    for line in log.read_text().splitlines():
        kind, _, payload = line.partition(" ")
        try:
            recs.append((kind, json.loads(payload)))
        except Exception:
            # A truncated tail is LOST EVIDENCE. Recorded rather than dropped so the
            # last observation cannot vanish quietly.
            recs.append(("TRUNCATED", line[:120]))

    imports = [v[0] for k, v in recs if k == "IMPORT"]
    ours = [m for m in imports if m.split(".")[0] == "bytedigger_engine"]
    assert len(ours) >= 5, (
        f"AC11b: the engine never got underway — {len(ours)} bytedigger_engine import(s); "
        f"the run proves nothing"
    )

    # The run must have REACHED ITS RESULT. rev5 wrote a DONE marker and never read
    # it, asserted no rc, and let `ours >= 5` be satisfied by run.py's prologue —
    # so an engine that died halfway still passed.
    outs = "".join(v for k, v in recs if k == "OUT")
    produced = None
    for chunk in outs.splitlines():
        if chunk.lstrip().startswith("["):
            try:
                produced = json.loads(chunk)
            except Exception:
                pass
    assert produced is not None, (
        f"AC11b: the entry point produced no workflow list — it did not complete. "
        f"Log tail: {[k for k, _ in recs][-8:]}"
    )
    assert len(produced) == EXPECTED_WORKFLOWS, (
        f"AC11b: entry point produced {len(produced)} workflows, expected {EXPECTED_WORKFLOWS}"
    )

    flat = {"contracts", "config_provider", "ctx_floor", "io_utils", "engine",
            "event_log", "phase_2_explore", "workflows", "lib", "security", "scripts"}
    leaked = sorted({m for m in imports if m.split(".")[0] in flat})
    assert leaked == [], f"AC11b: the run attempted top-level engine imports: {leaked}"

    # sys.path is carried WITH every import record, so the last one observed is the
    # state in force just before the process died — no post-mortem snapshot needed.
    truncated = [v for k, v in recs if k == "TRUNCATED"]
    # Chronological by construction: the log is append-only and read in order.
    paths = [v for k, v in recs if k == "PATH"] + [v[1] for k, v in recs if k == "IMPORT"]
    assert len(paths) >= 2, "AC11b: fewer than two sys.path observations — nothing to compare"
    grew = [x for x in paths[-1] if x not in paths[0]]
    print(f"bd44 AC11b: {len(imports)} import(s), {len(paths)} sys.path observation(s)")
    assert grew == [], f"AC11b: the run widened sys.path with {grew}"
    assert truncated == [], f"AC11b: evidence lines were truncated: {truncated[:3]}"
