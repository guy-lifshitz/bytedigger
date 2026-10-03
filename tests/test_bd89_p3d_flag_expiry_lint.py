"""RED tests for bd#89 P3d: flag owner + expires, dead flag deleted, CI expiry lint.

Frozen spec: docs/decisions/2026-10-03-bd89-p3d-flags-owner-expiry-lint.md

Per workflows.md section 1q nothing here imports the script under test at
module import time. The script path is resolved lazily inside each test body
(import-by-path via importlib, or a subprocess with sys.executable), so
collection always succeeds and failure happens at assert time. No mocks:
synthetic catalogs are real files under tmp_path, live ACs read the real
catalog by file path.

Repo root is the parent of this tests/ directory.
"""
from __future__ import annotations

import datetime
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "flag_expiry_lint.py"
CATALOG = REPO_ROOT / "engine_py" / "bytedigger_engine" / "flags_catalog.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

DEAD_FLAG = "HAL_ORPHAN_CALLSITE_ENFORCE"

# AC2: literal {name: default} table measured on base 1e391ed (zero behaviour
# change). The deleted dead flag is deliberately absent. HAL_ORPHAN_CALLSITE_GATE
# is pinned here but is NOT part of the rollout set (no _ENFORCE suffix, no
# flip-by text, no owner/expires), so it is not required to carry the new fields.
PINNED_DEFAULTS = {
    "HAL_RED_COLLECT_PROBE_ENFORCE": "0",
    "HAL_RED_MASS_DELETION_ENFORCE": "0",
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

# The rollout set (is_rollout true) in the current catalog after deleting the
# dead flag: every remaining *_ENFORCE entry, the flip-by/retire-by entries, and
# HAL_SIBLING_AUDIT_GATE (explicit extra).
ROLLOUT_NAMES = frozenset(PINNED_DEFAULTS) - {"HAL_ORPHAN_CALLSITE_GATE"}

# Entries whose description date is later than 2026-11-02 keep their own date.
LATE_DATED = {
    "HAL_AC_DSL_GATE_ENFORCE": "2027-01-15",
    "HAL_SPEC_DEFECT_REROUTE": "2027-01-15",
}
# Every other rollout entry was expired (date before 2026-11-02) or undated
# (HAL_SIBLING_AUDIT_GATE) and is renewed to 2026-11-02.
PREVIOUSLY_EXPIRED = sorted(ROLLOUT_NAMES - set(LATE_DATED))

BASE_DATE = "2026-10-03"


def _load_script():
    assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
    spec = importlib.util.spec_from_file_location("flag_expiry_lint_under_test", SCRIPT)
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


def _flag(owner="guy-lifshitz", expires="2026-12-01", **extra) -> dict:
    entry = {
        "kind": "flag",
        "default": "0",
        "module": "x.py",
        "description": "synthetic entry.",
    }
    if owner is not None:
        entry["owner"] = owner
    if expires is not None:
        entry["expires"] = expires
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


class TestBd89P3dFlagExpiryLint:
    # ---- data ACs (read the real catalog) ---------------------------------

    def test_ac1_dead_flag_deleted_and_token_absent_from_code(self):
        """AC1: HAL_ORPHAN_CALLSITE_ENFORCE is gone from FLAGS and from every
        text file under engine_py/ and scripts/ (CHANGELOG and docs/decisions
        are outside these roots)."""
        assert DEAD_FLAG not in _live_flags(), f"{DEAD_FLAG} is still in FLAGS"
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
        """AC2: zero behaviour change. Every pinned remaining rollout entry is
        still in FLAGS with its default unchanged, and no *_ENFORCE name other
        than the deleted one exists outside the pinned table."""
        flags = _live_flags()
        for name, default in PINNED_DEFAULTS.items():
            assert name in flags, f"{name} vanished from FLAGS"
            assert flags[name].get("default") == default, (
                f"{name} default changed: {flags[name].get('default')!r} != {default!r}"
            )
        stray = sorted(
            n for n in flags
            if n.endswith("_ENFORCE") and n != DEAD_FLAG and n not in PINNED_DEFAULTS
        )
        assert not stray, f"unpinned *_ENFORCE entries: {stray}"

    def test_ac3_every_rollout_entry_has_owner_and_future_expires(self):
        """AC3: by the script's own is_rollout, every live rollout entry has a
        non-blank owner and an ISO expires not before 2026-10-03."""
        mod = _load_script()
        flags = mod.load_flags(CATALOG)
        rollout = {n for n, e in flags.items() if mod.is_rollout(n, e)}
        assert rollout == ROLLOUT_NAMES, (
            f"rollout set drifted: extra={sorted(rollout - ROLLOUT_NAMES)} "
            f"missing={sorted(ROLLOUT_NAMES - rollout)}"
        )
        floor = datetime.date.fromisoformat(BASE_DATE)
        for name in sorted(rollout):
            entry = flags[name]
            owner = entry.get("owner")
            assert isinstance(owner, str) and owner.strip(), f"{name}: owner missing/blank"
            exp = entry.get("expires")
            assert isinstance(exp, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp), (
                f"{name}: expires missing/not ISO: {exp!r}"
            )
            assert datetime.date.fromisoformat(exp) >= floor, f"{name}: expires {exp} is past"

    def test_ac4_sibling_audit_gate_and_renewed_dates(self):
        """AC4: HAL_SIBLING_AUDIT_GATE stays a default-1 gate with owner and
        expires; the two late-dated entries keep 2027-01-15; every previously
        expired entry is renewed to 2026-11-02."""
        flags = _live_flags()
        sib = flags["HAL_SIBLING_AUDIT_GATE"]
        assert sib.get("default") == "1" and sib.get("kind") == "gate"
        assert isinstance(sib.get("owner"), str) and sib["owner"].strip()
        assert isinstance(sib.get("expires"), str) and sib["expires"]
        for name, date in LATE_DATED.items():
            assert flags[name].get("expires") == date, (
                f"{name}: expires {flags[name].get('expires')!r} != {date}"
            )
        for name in PREVIOUSLY_EXPIRED:
            assert flags[name].get("expires") == "2026-11-02", (
                f"{name}: expires {flags[name].get('expires')!r} != '2026-11-02'"
            )

    # ---- CLI on the live catalog ------------------------------------------

    def test_ac5_cli_live_catalog_ok(self, tmp_path):
        """AC5: --today 2026-10-03 on the live catalog exits 0 and prints OK:."""
        r = _run(tmp_path, "--today", BASE_DATE)
        assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr!r}"
        assert "OK:" in r.stdout, r.stdout

    def test_ac6_cli_expiry_and_boundary(self, tmp_path):
        """AC6: after 2026-11-02 the renewed entries are expired (exit 1, named
        on stderr); on 2026-11-02 itself nothing is expired (exit 0)."""
        late = _run(tmp_path, "--today", "2026-11-03")
        assert late.returncode == 1, f"rc={late.returncode} stderr={late.stderr!r}"
        assert "HAL_SIBLING_AUDIT_GATE" in late.stderr
        edge = _run(tmp_path, "--today", "2026-11-02")
        assert edge.returncode == 0, f"expires == today must pass; stderr={edge.stderr!r}"

    # ---- CLI on synthetic catalogs ----------------------------------------

    def test_ac7_missing_or_blank_fields_reported_by_name(self, tmp_path):
        """AC7: missing owner, missing expires and blank owner are each
        reported by flag name; exit 1; a fully valid entry is not reported."""
        cat = _write_catalog(tmp_path, {
            "HAL_NO_OWNER_ENFORCE": _flag(owner=None),
            "HAL_NO_EXPIRES_ENFORCE": _flag(expires=None),
            "HAL_BLANK_OWNER_ENFORCE": _flag(owner="   "),
            "HAL_GOOD_ENFORCE": _flag(),
        })
        r = _run(tmp_path, "--catalog", str(cat), "--today", BASE_DATE)
        assert r.returncode == 1, f"rc={r.returncode} stdout={r.stdout!r}"
        for name in ("HAL_NO_OWNER_ENFORCE", "HAL_NO_EXPIRES_ENFORCE", "HAL_BLANK_OWNER_ENFORCE"):
            assert _named(r, name), f"{name} not reported: {r.stderr!r}"
        assert not _named(r, "HAL_GOOD_ENFORCE"), r.stderr

    def test_ac8_new_default_off_enforce_flag_needs_owner_and_expires(self, tmp_path):
        """AC8: a NEW HAL_FOO_ENFORCE (kind flag, default 0) without fields
        fails by name; the same entry with owner and a future expires passes."""
        bare = _write_catalog(tmp_path, {"HAL_FOO_ENFORCE": _flag(owner=None, expires=None)}, "bare.py")
        r_bad = _run(tmp_path, "--catalog", str(bare), "--today", BASE_DATE)
        assert r_bad.returncode == 1, f"rc={r_bad.returncode} stderr={r_bad.stderr!r}"
        assert _named(r_bad, "HAL_FOO_ENFORCE"), r_bad.stderr
        full = _write_catalog(tmp_path, {"HAL_FOO_ENFORCE": _flag()}, "full.py")
        r_ok = _run(tmp_path, "--catalog", str(full), "--today", BASE_DATE)
        assert r_ok.returncode == 0, f"rc={r_ok.returncode} stderr={r_ok.stderr!r}"
        assert "OK:" in r_ok.stdout

    def test_ac9_malformed_entries_degrade_without_traceback(self, tmp_path):
        """AC9: non-dict entries and bad owner/expires values each yield a
        violation line starting with the flag name; exit 1; no traceback."""
        bad = {
            "HAL_NONE_ENFORCE": None,
            "HAL_LIST_ENFORCE": [],
            "HAL_STR_ENFORCE": "oops",
            "HAL_INT_EXP_ENFORCE": _flag(expires=5),
            "HAL_BADDATE_ENFORCE": _flag(expires="2026-13-45"),
            "HAL_SOON_ENFORCE": _flag(expires="soon"),
            "HAL_LIST_OWNER_ENFORCE": _flag(owner=["x"]),
        }
        flags = dict(bad)
        flags["HAL_CONTROL_ENFORCE"] = _flag()
        cat = _write_catalog(tmp_path, flags)
        r = _run(tmp_path, "--catalog", str(cat), "--today", BASE_DATE)
        assert r.returncode == 1, f"rc={r.returncode} stderr={r.stderr!r}"
        assert "Traceback" not in r.stderr and "Traceback" not in r.stdout
        for name in bad:
            assert _named(r, name), f"{name} not reported: {r.stderr!r}"
        assert not _named(r, "HAL_CONTROL_ENFORCE"), r.stderr

    def test_ac10_no_false_positives_and_flip_by_enters_rollout(self, tmp_path):
        """AC10: a non-rollout path entry without owner/expires is not
        reported; a kind=flag entry with flip-by: text but no _ENFORCE suffix
        is in the rollout set even without fields."""
        plain = _write_catalog(tmp_path, {
            "HAL_PLAIN_PATH": {
                "kind": "path", "default": None, "module": "x.py",
                "description": "Override path.",
            },
        }, "plain.py")
        r_plain = _run(tmp_path, "--catalog", str(plain), "--today", BASE_DATE)
        assert r_plain.returncode == 0, f"rc={r_plain.returncode} stderr={r_plain.stderr!r}"
        assert not _named(r_plain, "HAL_PLAIN_PATH"), r_plain.stderr

        flipby = _write_catalog(tmp_path, {
            "HAL_ROLLOUT_NOSUFFIX": {
                "kind": "flag", "default": "0", "module": "x.py",
                "description": "Opt-in rollout, flip-by:2026-12-01.",
            },
        }, "flipby.py")
        r_flip = _run(tmp_path, "--catalog", str(flipby), "--today", BASE_DATE)
        assert r_flip.returncode == 1, f"rc={r_flip.returncode} stderr={r_flip.stderr!r}"
        assert _named(r_flip, "HAL_ROLLOUT_NOSUFFIX"), r_flip.stderr

    def test_ac11_unloadable_catalog_and_bad_today_exit_2(self, tmp_path):
        """AC11: unreadable path and syntax-error catalog exit 2 with a
        one-line message and no traceback; --today garbage exits 2."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
        missing = _run(tmp_path, "--catalog", str(tmp_path / "nope.py"), "--today", BASE_DATE)
        assert missing.returncode == 2, f"rc={missing.returncode} stderr={missing.stderr!r}"
        assert "Traceback" not in missing.stderr
        assert len(_stderr_lines(missing)) == 1, missing.stderr

        broken = tmp_path / "broken.py"
        broken.write_text("FLAGS = {\n", encoding="utf-8")
        syn = _run(tmp_path, "--catalog", str(broken), "--today", BASE_DATE)
        assert syn.returncode == 2, f"rc={syn.returncode} stderr={syn.stderr!r}"
        assert "Traceback" not in syn.stderr
        assert len(_stderr_lines(syn)) == 1, syn.stderr

        garbage = _run(tmp_path, "--today", "garbage")
        assert garbage.returncode == 2, f"rc={garbage.returncode} stderr={garbage.stderr!r}"
        assert "Traceback" not in garbage.stderr

    # ---- CI / guards / docs -----------------------------------------------

    def test_ac12_ci_manifests_job_runs_lint_and_its_tests(self):
        """AC12: the manifests job (root tests/) has a step running the lint
        script and a step running this test file."""
        block = _extract_job_block(CI_YML.read_text(encoding="utf-8"), "manifests")
        steps = re.split(r"(?m)^(?=\s*- )", block)
        assert any(
            re.search(r"run:[\s\S]*?scripts/flag_expiry_lint\.py", s) for s in steps
        ), "manifests job has no step running scripts/flag_expiry_lint.py"
        assert any(
            re.search(r"run:[\s\S]*?tests/test_bd89_p3d_flag_expiry_lint\.py", s) for s in steps
        ), "manifests job has no step running tests/test_bd89_p3d_flag_expiry_lint.py"

    def test_ac13_guard_stable_output_compiles_and_cyrillic_clean(self, tmp_path):
        """AC13 (GUARD): two runs on the live catalog give identical non-empty
        stdout, the script byte-compiles, and cyrillic-prose-lint exits 0."""
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
        a = _run(tmp_path, "--today", BASE_DATE)
        b = _run(tmp_path, "--today", BASE_DATE)
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
        """AC14 (GUARD): CONTRIBUTING.md has a Flag lifecycle heading and the
        CHANGELOG [Unreleased] section mentions the lint and the removed flag."""
        contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        assert re.search(r"(?m)^#{1,6}\s+.*Flag lifecycle", contributing), (
            "CONTRIBUTING.md has no 'Flag lifecycle' heading"
        )
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        m = re.search(r"(?ms)^## \[Unreleased\]\s*\n(.*?)(?=^## \[)", changelog)
        assert m, "CHANGELOG has no [Unreleased] section"
        section = m.group(1)
        assert "flag_expiry_lint" in section, "[Unreleased] does not mention flag_expiry_lint"
        assert DEAD_FLAG in section, f"[Unreleased] does not mention {DEAD_FLAG}"
