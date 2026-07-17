"""RED tests for GH906 — spec_cite_lint: extract identifiers from quoted
def/class signature citations (declared_symbols).

Spec: SHARED/memory/Decisions/2026-07-16_GH906_cite_signature_declared_spec.md
§3 AC1-AC8.

conftest.py (engine_py/tests/conftest.py) already puts engine_py/ onto
sys.path at collection time, so a plain top-level `import spec_cite` /
`from spec_cite import declared_symbols, lint_spec` is safe (§1q) — both
symbols already exist today; only the NEW quoted-signature extraction
behavior inside declared_symbols is missing pre-GREEN.

Expected pre-GREEN result:
  - AC1, AC2, AC3, AC4 FAIL — quoted def/class signatures are not yet
    extracted into declared_symbols().
  - AC5 PASSES already (negative/anti-over-broadening control — nothing new
    to extract from prose lines with no leading quote char).
  - AC6 FAILS — the e2e rescue never fires (declared_symbols misses the
    quoted signature), so lint_spec rc == 1 / unresolved_symbol instead of
    the expected rc == 0 / new_symbol.
  - AC7 PASSES already (mutation control: with the signature-quote line
    removed, the pre-existing unresolved_symbol/rc==1 behavior is
    unaffected by GH906 — pins the gate still fires).
  - AC8 PASSES already (regression pin: existing anchored def/class forms
    are untouched by the new quoted-signature extractors).
"""
from __future__ import annotations

from pathlib import Path

from spec_cite import declared_symbols, lint_spec

# Symbol name deliberately kept out of any fixture .py body under a tmp
# repo_root in AC6/AC7 -- only ever appears inside spec.md fixture text.
_TARGET_SYMBOL = "load_retired_tech"


# ── AC1: quoted def signature (with type hints/spaces) extracted ───────────


def test_ac1_quoted_def_signature_with_types_extracted() -> None:
    spec_text = (
        'bark/posture/retired_tech.py:'
        '"def load_retired_tech(org_config: Mapping) -> list[dict]:"\n'
    )
    declared = declared_symbols(spec_text)
    assert _TARGET_SYMBOL in declared, (
        f"AC1: expected {_TARGET_SYMBOL!r} in declared_symbols output, "
        f"got {declared}"
    )


# ── AC2: quoted class signature extracted ───────────────────────────────────


def test_ac2_quoted_class_signature_extracted() -> None:
    spec_text = 'bark/posture/retired_tech.py: "class RetiredTechStore:"\n'
    declared = declared_symbols(spec_text)
    assert "RetiredTechStore" in declared, (
        f"AC2: expected 'RetiredTechStore' in declared_symbols output, "
        f"got {declared}"
    )


# ── AC3: quoted async def signature extracted ───────────────────────────────


def test_ac3_quoted_async_def_signature_extracted() -> None:
    spec_text = (
        'bark/posture/retired_tech.py: '
        '"async def fetch_retired(org_config: Mapping) -> list[dict]:"\n'
    )
    declared = declared_symbols(spec_text)
    assert "fetch_retired" in declared, (
        f"AC3: expected 'fetch_retired' in declared_symbols output, "
        f"got {declared}"
    )


# ── AC4: backtick-quoted signature extracted ────────────────────────────────


def test_ac4_backtick_quoted_signature_extracted() -> None:
    spec_text = (
        "bark/posture/retired_tech.py: "
        "`def load_retired_tech(org_config: Mapping) -> list[dict]:`\n"
    )
    declared = declared_symbols(spec_text)
    assert _TARGET_SYMBOL in declared, (
        f"AC4: expected {_TARGET_SYMBOL!r} in declared_symbols output "
        f"(backtick-quoted signature), got {declared}"
    )


# ── AC5: anti-over-broadening negative control (no leading quote char) ─────


def test_ac5_anti_over_broadening_no_quote_char_not_extracted() -> None:
    spec_text = (
        "call load_other in the loop\n"
        "we then def something here\n"
    )
    declared = declared_symbols(spec_text)
    assert "load_other" not in declared, (
        f"AC5: expected 'load_other' NOT in declared_symbols output "
        f"(bare prose mention, no leading quote char), got {declared}"
    )
    assert "something" not in declared, (
        f"AC5: expected 'something' NOT in declared_symbols output "
        f"(bare 'def something' without a preceding quote char), "
        f"got {declared}"
    )


# ── AC6: e2e repro — signature-quote line rescues the bare-mention line ────


def _write_ac6_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """bark/posture/retired_tech.py stays ABSENT (planned net-new file).
    An existing sibling .py file (no relation to the target symbol) is
    cited on the bare-mention line so scan_citations pairs `load_retired_tech`
    against a real, existing file that does NOT contain the symbol.
    """
    root = tmp_path
    pkg = root / "bark" / "posture"
    pkg.mkdir(parents=True)
    other = pkg / "other_module.py"
    other.write_text("def unrelated_thing():\n    pass\n")

    spec_path = root / "spec.md"
    spec_path.write_text(
        'bark/posture/retired_tech.py: '
        '"def load_retired_tech(org_config: Mapping) -> list[dict]:"\n'
        "See `bark/posture/other_module.py` for `load_retired_tech` usage.\n"
    )
    return spec_path, root


def test_ac6_e2e_rescue_via_quoted_signature_line() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        spec_path, root = _write_ac6_fixture(tmp_path)
        exit_code, findings = lint_spec(spec_path, root)
        assert exit_code == 0, (
            f"AC6: expected lint_spec exit_code=0, got {exit_code}; "
            f"findings={findings}"
        )
        target = [f for f in findings if f.symbol == _TARGET_SYMBOL]
        assert target, f"AC6: expected a finding for {_TARGET_SYMBOL!r}, got {findings}"
        assert all(f.status == "new_symbol" for f in target), (
            f"AC6: expected status='new_symbol' for {_TARGET_SYMBOL!r} "
            f"findings, got {[(f.file, f.status) for f in target]}"
        )


# ── AC7: mutation control — remove the signature-quote line, gate still fires ──


def test_ac7_mutation_control_signature_line_removed_still_blocks() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        root = tmp_path
        pkg = root / "bark" / "posture"
        pkg.mkdir(parents=True)
        other = pkg / "other_module.py"
        other.write_text("def unrelated_thing():\n    pass\n")

        spec_path = root / "spec.md"
        spec_path.write_text(
            "See `bark/posture/other_module.py` for `load_retired_tech` usage.\n"
        )

        exit_code, findings = lint_spec(spec_path, root)
        assert exit_code == 1, (
            f"AC7: expected lint_spec exit_code=1 (no rescue line present), "
            f"got {exit_code}; findings={findings}"
        )
        target = [f for f in findings if f.symbol == _TARGET_SYMBOL]
        assert target and all(f.status == "unresolved_symbol" for f in target), (
            f"AC7: expected status='unresolved_symbol' for {_TARGET_SYMBOL!r} "
            f"findings (rescue line removed), got "
            f"{[(f.file, f.status) for f in target]}"
        )


# ── AC8: regression — existing anchored def/class forms still extracted ────


def test_ac8_regression_anchored_def_and_class_still_extracted() -> None:
    spec_text = (
        "def old_fn(x):\n"
        "class OldCls:\n"
    )
    declared = declared_symbols(spec_text)
    assert "old_fn" in declared, (
        f"AC8: expected 'old_fn' (line-anchored def) in declared_symbols "
        f"output, got {declared}"
    )
    assert "OldCls" in declared, (
        f"AC8: expected 'OldCls' (line-anchored class) in declared_symbols "
        f"output, got {declared}"
    )
