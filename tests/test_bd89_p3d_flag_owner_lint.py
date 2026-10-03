"""RED tests for bd#89 P3d (errata r5): flag owner + provenance lint, dead flag deleted.

Frozen spec: docs/decisions/2026-10-03-bd89-p3d-flags-owner-expiry-lint.md (section 10,
Errata r5, supersedes the expiry parts; section 9 deletion/provenance still applies).
Expiry is NOT this lint's job: scripts/flip_horizon.py + its ledger own it.

Per workflows.md section 1q nothing here imports the script under test at module
import time. The script path is resolved lazily inside each test body (import by
path via importlib, or a subprocess with sys.executable), so collection always
succeeds and failure happens at assert time. No mocks: synthetic catalogs are real
files under tmp_path, live ACs read the real catalog by file path.

Lifecycle pins (gate r1 lessons): this file runs on every CI push, so there is no
"stray flag" check, ROLLOUT_NAMES is checked as a subset, and no date appears in an
assertion about the live catalog.

Repo root is the parent of this tests/ directory.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "flag_owner_lint.py"
CATALOG = REPO_ROOT / "engine_py" / "bytedigger_engine" / "flags_catalog.py"
LEDGER = REPO_ROOT / "scripts" / "flip_horizon_ledger.json"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

DEAD_FLAG = "HAL_ORPHAN_CALLSITE_ENFORCE"

# AC2 (GUARD): literal {name: default} table of the rollout entries of the
# current catalog (measured on rebased main 9cb8310 + #223/#225/#227, minus the
# deleted flag), plus HAL_ORPHAN_CALLSITE_GATE (not a rollout entry: no _ENFORCE
# suffix, no dated token). Pins zero behaviour change; unpinned names are free.
PINNED_DEFAULTS = {
    "HAL_RED_COLLECT_PROBE_ENFORCE": "1",
    "HAL_RED_MASS_DELETION_ENFORCE": "1",
    "HAL_RED_TEST_INTEGRITY_ENFORCE": "1",
    "HAL_SPEC_REENTRY_ENFORCE": "0",
    "HAL_SPEC_HELPER_EXTRACTION_ENFORCE": "0",
    "HAL_SPEC_LINT_BATCH_ENFORCE": "0",
    "HAL_AC_DSL_GATE_ENFORCE": "1",
    "HAL_SPEC_CITE_PRELINT_ENFORCE": "0",
    "HAL_VERDICT_GATE_LINT_ENFORCE": "0",
    "HAL_FLAG_UNREGISTERED_ENFORCE": "0",
    "HAL_BASELINE_DELTA_ENFORCE": "0",
    "HAL_CORPUS_PARITY_ENFORCE": "0",
    "HAL_KNOWN_REDS_KILL_BY_ENFORCE": "0",
    "HAL_KNOWN_REDS_SCOPE_ENFORCE": "0",
    "HAL_KNOWN_REDS_OWNER_ENFORCE": "0",
    "HAL_KNOWN_REDS_RED_SHAPE_ENFORCE": "0",
    "HAL_SPEC_PREFLIGHT_BATCH": "0",
    "HAL_SPEC_DEFECT_REROUTE": "1",
    "HAL_SIBLING_AUDIT_GATE": "1",
    "HAL_ORPHAN_CALLSITE_GATE": "1",
}

# The rollout set (is_rollout true) of the current catalog: every *_ENFORCE entry,
# the dated flip-by/retire-by entries, and HAL_SIBLING_AUDIT_GATE (explicit extra).
# HAL_ORPHAN_CALLSITE_ENFORCE is excluded (deleted); HAL_ORPHAN_CALLSITE_GATE is
# not a rollout entry.
ROLLOUT_NAMES = frozenset(PINNED_DEFAULTS) - {"HAL_ORPHAN_CALLSITE_GATE"}


def _load_script():
    assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
    spec = importlib.util.spec_from_file_location("flag_owner_lint_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live_flags() -> dict:
    """Read the real catalog by file path, no package import, no env read."""
    spec = importlib.util.spec_from_file_location("flags_catalog_p3d_probe", CATALOG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FLAGS


def _run(cwd, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
    )


def _write_catalog(tmp_path: Path, flags: dict, name: str = "flags_catalog.py") -> Path:
    path = tmp_path / name
    path.write_text("FLAGS = " + repr(flags) + "\n", encoding="utf-8")
    return path


_PROV = "introduced: GH1 - synthetic - stays"


def _flag(owner="guy-lifshitz", provenance=_PROV, description="synthetic entry.", **extra) -> dict:
    entry = {
        "kind": "flag",
        "default": "0",
        "module": "x.py",
        "description": description,
    }
    if owner is not None:
        entry["owner"] = owner
    if provenance is not None:
        entry["provenance"] = provenance
    entry.update(extra)
    return entry


def _stderr_lines(result) -> list:
    return [ln for ln in result.stderr.splitlines() if ln.strip()]


def _named(result, name: str) -> bool:
    return any(ln.startswith(name) for ln in _stderr_lines(result))


def _extract_job_block(text: str, job_name: str) -> str:
    m = re.search(rf"(?m)^  {re.escape(job_name)}:\s*\n", text)
    assert m, f"no top-level '{job_name}:' job in ci.yml"
    start = m.end()
    m2 = re.search(r"(?m)^  \S", text[start:])
    end = start + m2.start() if m2 else len(text)
    return text[start:end]


class TestBd89P3dFlagOwnerLint:
    # ---- data ACs (read the real catalog) ---------------------------------

    def test_ac1_dead_flag_deleted_everywhere(self):
        """AC1: HAL_ORPHAN_CALLSITE_ENFORCE is gone from FLAGS, from
        scripts/flip_horizon_ledger.json, and from every text file under
        engine_py/ and scripts/ (CHANGELOG, docs/decisions and tests/ are outside
        these roots)."""
        flags = _live_flags()
        assert DEAD_FLAG not in flags, f"{DEAD_FLAG} is still in FLAGS"
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        assert DEAD_FLAG not in ledger, f"{DEAD_FLAG} still has a flip-horizon ledger entry"
        hits = []
        for root in (REPO_ROOT / "engine_py", REPO_ROOT / "scripts"):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if any(p in ("__pycache__", ".git", "node_modules") for p in path.parts):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if DEAD_FLAG in text:
                    hits.append(str(path.relative_to(REPO_ROOT)))
        assert not hits, f"{DEAD_FLAG} still mentioned in: {hits}"

    def test_ac2_remaining_flags_keep_their_defaults(self):
        """AC2 (GUARD): zero behaviour change. Every pinned entry is still in
        FLAGS with its default unchanged. No check for unpinned names."""
        flags = _live_flags()
        for name, default in PINNED_DEFAULTS.items():
            assert name in flags, f"{name} vanished from FLAGS"
            assert flags[name].get("default") == default, (
                f"{name} default changed: {flags[name].get('default')!r} != {default!r}"
            )

    def test_ac3_every_rollout_entry_has_owner_and_provenance(self):
        """AC3: by the script's own is_rollout, the live rollout set contains
        ROLLOUT_NAMES (superset, new flags allowed) and every rollout entry has
        a non-blank str owner and provenance."""
        mod = _load_script()
        flags = mod.load_flags(CATALOG)
        rollout = {n for n, e in flags.items() if mod.is_rollout(n, e)}
        assert ROLLOUT_NAMES <= rollout, (
            f"rollout set lost entries: missing={sorted(ROLLOUT_NAMES - rollout)}"
        )
        for name in sorted(rollout):
            entry = flags[name]
            owner = entry.get("owner")
            assert isinstance(owner, str) and owner.strip(), f"{name}: owner missing/blank"
            prov = entry.get("provenance")
            assert isinstance(prov, str) and prov.strip(), (
                f"{name}: provenance missing/blank/non-str: {prov!r}"
            )
            # Same rule as the lint (errata r6 F1): prefix + non-blank text.
            stripped = prov.strip()
            assert stripped.startswith("introduced:") and stripped[len("introduced:"):].strip(), (
                f"{name}: provenance must start with 'introduced:' and have text: {prov!r}"
            )
        # Errata r6 F1: content derived from the entry's own description, only
        # for the migration set. A reference token in the description must
        # appear in the provenance; no token -> the fallback phrase.
        for name in sorted(ROLLOUT_NAMES):
            desc = flags[name].get("description")
            desc = desc if isinstance(desc, str) else ""
            prov = flags[name]["provenance"]
            refs = [m.group(1) for m in re.finditer(r"(?:GH|#)(\d{2,})", desc)]
            refs += re.findall(r"\b([0-9A-F]{8})\b", desc)
            if refs:
                assert any(r in prov for r in refs), (
                    f"{name}: provenance {prov!r} cites none of the description refs {refs}"
                )
            else:
                assert "no provenance found in this repo" in prov, (
                    f"{name}: description has no ref; provenance must say so: {prov!r}"
                )

    def test_ac4_sibling_audit_gate_stays_default_on_gate_with_fields(self):
        """AC4: HAL_SIBLING_AUDIT_GATE keeps kind gate and default "1" and
        carries owner and provenance."""
        sib = _live_flags()["HAL_SIBLING_AUDIT_GATE"]
        assert sib.get("kind") == "gate" and sib.get("default") == "1"
        assert isinstance(sib.get("owner"), str) and sib["owner"].strip(), "owner missing"
        assert isinstance(sib.get("provenance"), str) and sib["provenance"].strip(), (
            "provenance missing"
        )

    # ---- CLI on the live catalog ------------------------------------------

    def test_ac5_cli_live_catalog_ok(self, tmp_path):
        """AC5: the CLI on the live catalog (default path resolved from the
        script, cwd elsewhere) exits 0 and prints OK:."""
        r = _run(tmp_path)
        assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr!r}"
        assert "OK:" in r.stdout, r.stdout

    # ---- CLI on synthetic catalogs ----------------------------------------

    def test_ac6_lint_ignores_dates(self, tmp_path):
        """AC6: a synthetic entry with owner+provenance and a long-expired
        flip-by: token passes; expiry belongs to flip_horizon, not this lint."""
        cat = _write_catalog(tmp_path, {
            "HAL_OLD_ENFORCE": _flag(
                description="Default-OFF rollout, warn-only until flip-by:2020-01-01.",
            ),
            "HAL_OLD_RETIRE": _flag(description="Kill-switch, retire-by:2020-01-01."),
        })
        r = _run(tmp_path, "--catalog", str(cat))
        assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr!r}"
        assert "OK:" in r.stdout, r.stdout

    def test_ac7_missing_or_blank_fields_reported_by_name(self, tmp_path):
        """AC7: missing/blank owner and missing/blank provenance are each
        reported by flag name; exit 1; a fully valid entry is not reported."""
        cat = _write_catalog(tmp_path, {
            "HAL_NO_OWNER_ENFORCE": _flag(owner=None),
            "HAL_BLANK_OWNER_ENFORCE": _flag(owner="   "),
            "HAL_NO_PROV_ENFORCE": _flag(provenance=None),
            "HAL_BLANK_PROV_ENFORCE": _flag(provenance="  "),
            "HAL_TBD_PROV_ENFORCE": _flag(provenance="TBD"),
            "HAL_EMPTY_INTRO_ENFORCE": _flag(provenance="introduced:   "),
            "HAL_GOOD_ENFORCE": _flag(),
        })
        r = _run(tmp_path, "--catalog", str(cat))
        assert r.returncode == 1, f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
        for name in (
            "HAL_NO_OWNER_ENFORCE", "HAL_BLANK_OWNER_ENFORCE",
            "HAL_NO_PROV_ENFORCE", "HAL_BLANK_PROV_ENFORCE",
            "HAL_TBD_PROV_ENFORCE", "HAL_EMPTY_INTRO_ENFORCE",
        ):
            assert _named(r, name), f"{name} not reported: {r.stderr!r}"
        assert not _named(r, "HAL_GOOD_ENFORCE"), r.stderr

    def test_ac8_new_default_off_enforce_flag_needs_owner_and_provenance(self, tmp_path):
        """AC8: a NEW HAL_FOO_ENFORCE (kind flag, default 0) without fields
        fails by name; the same entry with both fields passes."""
        bare = _write_catalog(
            tmp_path, {"HAL_FOO_ENFORCE": _flag(owner=None, provenance=None)}, "bare.py"
        )
        r_bad = _run(tmp_path, "--catalog", str(bare))
        assert r_bad.returncode == 1, f"rc={r_bad.returncode} stderr={r_bad.stderr!r}"
        assert _named(r_bad, "HAL_FOO_ENFORCE"), r_bad.stderr
        full = _write_catalog(tmp_path, {"HAL_FOO_ENFORCE": _flag()}, "full.py")
        r_ok = _run(tmp_path, "--catalog", str(full))
        assert r_ok.returncode == 0, f"rc={r_ok.returncode} stderr={r_ok.stderr!r}"
        assert "OK:" in r_ok.stdout

    def test_ac9_malformed_entries_degrade_without_traceback(self, tmp_path):
        """AC9: non-dict entries and non-str owner/provenance each yield a
        violation line starting with the flag name; exit 1; no traceback."""
        bad = {
            "HAL_NONE_ENFORCE": None,
            "HAL_LIST_ENFORCE": [],
            "HAL_STR_ENFORCE": "oops",
            "HAL_LIST_OWNER_ENFORCE": _flag(owner=["x"]),
            "HAL_INT_OWNER_ENFORCE": _flag(owner=5),
            "HAL_LIST_PROV_ENFORCE": _flag(provenance=["x"]),
            "HAL_INT_PROV_ENFORCE": _flag(provenance=7),
            # Non-dict entry in _EXTRA_ROLLOUT without the _ENFORCE suffix.
            "HAL_SIBLING_AUDIT_GATE": None,
        }
        flags = dict(bad)
        flags["HAL_CONTROL_ENFORCE"] = _flag()
        cat = _write_catalog(tmp_path, flags)
        r = _run(tmp_path, "--catalog", str(cat))
        assert r.returncode == 1, f"rc={r.returncode} stderr={r.stderr!r}"
        assert "Traceback" not in r.stderr and "Traceback" not in r.stdout
        for name in bad:
            assert _named(r, name), f"{name} not reported: {r.stderr!r}"
        assert not _named(r, "HAL_CONTROL_ENFORCE"), r.stderr

        # Errata r6 F5: malformed fields inside a dict entry (no _ENFORCE) must
        # not crash; a non-str / absent description counts as no dated token.
        odd = _write_catalog(tmp_path, {
            "HAL_NODESC_NOSUFFIX": {"kind": "flag", "default": "0", "module": "x.py"},
            "HAL_NONEDESC_NOSUFFIX": {
                "kind": "flag", "default": "0", "module": "x.py", "description": None,
            },
            "HAL_CONTROL_ENFORCE": _flag(),
        }, "odd.py")
        r2 = _run(tmp_path, "--catalog", str(odd))
        assert r2.returncode in (0, 1), f"rc={r2.returncode} stderr={r2.stderr!r}"
        assert "Traceback" not in r2.stderr and "Traceback" not in r2.stdout

    def test_ac10_no_false_positives_and_dated_description_enters_rollout(self, tmp_path):
        """AC10: a non-rollout path entry without fields is not reported; a
        kind=flag entry whose description has flip-by: (or kill-by:) but no
        _ENFORCE suffix is in the rollout set even without fields."""
        plain = _write_catalog(tmp_path, {
            "HAL_PLAIN_PATH": {
                "kind": "path", "default": None, "module": "x.py",
                "description": "Override path.",
            },
            "HAL_PLAIN_INT": {
                "kind": "int", "default": 3, "module": "x.py",
                "description": "A cap.",
            },
        }, "plain.py")
        r_plain = _run(tmp_path, "--catalog", str(plain))
        assert r_plain.returncode == 0, f"rc={r_plain.returncode} stderr={r_plain.stderr!r}"
        assert not _named(r_plain, "HAL_PLAIN_PATH"), r_plain.stderr

        # Errata r6 F2: an entry with no suffix and no dated token is in the
        # rollout set when it carries only `owner` (e.g. after a flip removed
        # the token), so its missing provenance is reported by name.
        owner_only = _write_catalog(tmp_path, {
            "HAL_OWNER_ONLY": _flag(provenance=None),
        }, "owner_only.py")
        r_own = _run(tmp_path, "--catalog", str(owner_only))
        assert r_own.returncode == 1, f"rc={r_own.returncode} stderr={r_own.stderr!r}"
        assert _named(r_own, "HAL_OWNER_ONLY"), r_own.stderr

        for token in ("flip-by:2026-12-01", "kill-by:2026-12-01"):
            cat = _write_catalog(tmp_path, {
                "HAL_ROLLOUT_NOSUFFIX": {
                    "kind": "flag", "default": "0", "module": "x.py",
                    "description": f"Opt-in rollout, {token}.",
                },
            }, "dated.py")
            r = _run(tmp_path, "--catalog", str(cat))
            assert r.returncode == 1, f"{token}: rc={r.returncode} stderr={r.stderr!r}"
            assert _named(r, "HAL_ROLLOUT_NOSUFFIX"), r.stderr

    def test_ac11_unloadable_catalog_exits_2_one_line(self, tmp_path):
        """AC11: unreadable path, syntax error, no FLAGS, FLAGS = [] and an
        import-time raise each exit 2 with one stderr line and no traceback."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
        missing = _run(tmp_path, "--catalog", str(tmp_path / "nope.py"))
        assert missing.returncode == 2, f"rc={missing.returncode} stderr={missing.stderr!r}"
        assert "Traceback" not in missing.stderr
        assert len(_stderr_lines(missing)) == 1, missing.stderr

        shapes = {
            "broken.py": "FLAGS = {\n",
            "no_flags.py": "OTHER = {}\n",
            "flags_list.py": "FLAGS = []\n",
            "raises.py": "raise RuntimeError('boom at import')\n",
        }
        for fname, body in shapes.items():
            p = tmp_path / fname
            p.write_text(body, encoding="utf-8")
            res = _run(tmp_path, "--catalog", str(p))
            assert res.returncode == 2, f"{fname}: rc={res.returncode} stderr={res.stderr!r}"
            assert "Traceback" not in res.stderr, f"{fname}: {res.stderr!r}"
            assert len(_stderr_lines(res)) == 1, f"{fname}: {res.stderr!r}"

    # ---- CI / guards / docs -----------------------------------------------

    def test_ac12_ci_manifests_job_runs_lint_and_its_tests(self):
        """AC12: the manifests job (root tests/) has a step running the lint
        script and a step running this test file."""
        block = _extract_job_block(CI_YML.read_text(encoding="utf-8"), "manifests")
        steps = re.split(r"(?m)^(?=\s*- )", block)
        assert any(
            re.search(r"run:[\s\S]*?scripts/flag_owner_lint\.py", s) for s in steps
        ), "manifests job has no step running scripts/flag_owner_lint.py"
        assert any(
            re.search(r"run:[\s\S]*?tests/test_bd89_p3d_flag_owner_lint\.py", s) for s in steps
        ), "manifests job has no step running tests/test_bd89_p3d_flag_owner_lint.py"

    def test_ac13_guard_stable_output_compiles_and_cyrillic_clean(self, tmp_path):
        """AC13 (GUARD): two runs on the live catalog give identical non-empty
        stdout, the script byte-compiles, and cyrillic-prose-lint exits 0."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
        a = _run(tmp_path)
        b = _run(tmp_path)
        assert a.returncode == 0 and "OK:" in a.stdout, f"rc={a.returncode} {a.stderr!r}"
        assert a.stdout == b.stdout, "stdout is not stable across runs"
        comp = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(SCRIPT)],
            capture_output=True, text=True, timeout=60,
        )
        assert comp.returncode == 0, comp.stdout + comp.stderr
        cyr = subprocess.run(
            [sys.executable, "cyrillic-prose-lint.py"],
            capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT),
        )
        assert cyr.returncode == 0, cyr.stdout[-600:] + cyr.stderr[-600:]

    def test_ac14_docs_flag_lifecycle_and_changelog(self):
        """AC14: CONTRIBUTING.md has a Flag lifecycle heading; CHANGELOG
        [Unreleased] has an Added bullet naming flag_owner_lint and a Removed
        bullet naming the deleted orphan flag."""
        contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        assert re.search(r"(?m)^#{1,6}\s+.*Flag lifecycle", contributing), (
            "CONTRIBUTING.md has no 'Flag lifecycle' heading"
        )
        spec = importlib.util.spec_from_file_location(
            "bd212_changelog_helper",
            REPO_ROOT / "engine_py" / "tests" / "helpers" / "changelog.py",
        )
        cl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cl)
        unreleased = [s for s in cl.parse_sections(cl.read_changelog()) if cl.is_unreleased(s)]
        assert unreleased, "CHANGELOG has no [Unreleased] section"
        parts = re.split(r"(?m)^(?=### )", unreleased[0].body)
        added = [s for s in parts if s.startswith("### Added")]
        removed = [s for s in parts if s.startswith("### Removed")]
        assert any("flag_owner_lint" in s for s in added), (
            "no Added bullet in [Unreleased] names flag_owner_lint"
        )
        assert any(DEAD_FLAG in s for s in removed), (
            f"no Removed bullet in [Unreleased] names {DEAD_FLAG}"
        )
