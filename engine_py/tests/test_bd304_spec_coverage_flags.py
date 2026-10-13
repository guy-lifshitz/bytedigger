"""bd#304: spec-coverage-lint.py --taxonomy / --finalize flags (JSON mode).

Drives the real root driver via subprocess. The opt-in flags append the raw
scanner finding dicts (scan_error_taxonomy / scan_finalize_coverage) to the
{"findings": [...]} payload. Text-mode output of those findings is out of
scope (tracked in #305).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[2] / "spec-coverage-lint.py"

TAXONOMY_RULE = "spec_taxonomy_missing_disposition"
FINALIZE_RULE = "spec_finalize_branch_uncovered"

NEW_CODE = "E_BD304_NOVEL_CODE"

_AC_TABLE = """\
## 3. Acceptance criteria

| AC | Criterion |
|----|-----------|
| AC1 | Parse input rejects an empty payload |
"""

CLEAN_SPEC = """\
# Spec

## 2. Design

- **Parse input** reads the payload.

""" + _AC_TABLE

TAXONOMY_UNDISPOSED_SPEC = """\
# Spec

## 2. Design

- **Parse input** reads the payload.
- A malformed payload raises {code}.

""".format(code=NEW_CODE) + _AC_TABLE

TAXONOMY_DISPOSED_SPEC = """\
# Spec

## 2. Design

- **Parse input** reads the payload.
- A malformed payload raises {code}.
- {code}: OWN - raised by the parser.

""".format(code=NEW_CODE) + _AC_TABLE

FINALIZE_UNCOVERED_SPEC = """\
# Spec

## 2. Design

- **Parse input** reads the payload.
- A cleanup branch removes the temp directory afterwards.

""" + _AC_TABLE


def _run(tmp_path: Path, spec_text: str, *flags: str) -> tuple[int, dict]:
    spec = tmp_path / "spec.md"
    spec.write_text(spec_text, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(DRIVER), "--spec", str(spec), "--json", *flags],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode in (0, 1), (proc.returncode, proc.stderr)
    return proc.returncode, json.loads(proc.stdout)


def _rules(payload: dict) -> list[str]:
    return [f.get("rule") for f in payload["findings"]]


class TestSpecCoverageFlags:
    def test_ac1_taxonomy_flag_reports_undisposed_code(self, tmp_path):
        rc0, base = _run(tmp_path, TAXONOMY_UNDISPOSED_SPEC)
        assert TAXONOMY_RULE not in _rules(base)
        assert rc0 == 0

        rc, payload = _run(tmp_path, TAXONOMY_UNDISPOSED_SPEC, "--taxonomy")
        assert rc == 1
        hits = [f for f in payload["findings"] if f.get("rule") == TAXONOMY_RULE]
        assert len(hits) == 1
        assert hits[0]["token"] == NEW_CODE

    def test_ac2_taxonomy_flag_accepts_disposed_code(self, tmp_path):
        rc, payload = _run(tmp_path, TAXONOMY_DISPOSED_SPEC, "--taxonomy")
        assert rc == 0
        assert TAXONOMY_RULE not in _rules(payload)
        assert payload["findings"] == []

    def test_ac3_finalize_flag_reports_uncovered_branch(self, tmp_path):
        rc0, base = _run(tmp_path, FINALIZE_UNCOVERED_SPEC)
        assert FINALIZE_RULE not in _rules(base)
        assert rc0 == 0

        rc, payload = _run(tmp_path, FINALIZE_UNCOVERED_SPEC, "--finalize")
        assert rc == 1
        hits = [f for f in payload["findings"] if f.get("rule") == FINALIZE_RULE]
        assert len(hits) == 1
        assert hits[0]["group"] == "cleanup"

    def test_ac4_clean_spec_is_clean_with_both_flags(self, tmp_path):
        rc, payload = _run(tmp_path, CLEAN_SPEC, "--taxonomy", "--finalize")
        assert rc == 0
        assert payload["findings"] == []

    def test_ac5_flags_are_independent(self, tmp_path):
        both = (
            TAXONOMY_UNDISPOSED_SPEC.replace(
                "- A malformed payload raises",
                "- A cleanup branch removes the temp directory.\n"
                "- A malformed payload raises",
            )
        )
        _, fin = _run(tmp_path, both, "--finalize")
        assert FINALIZE_RULE in _rules(fin)
        assert TAXONOMY_RULE not in _rules(fin)

        _, tax = _run(tmp_path, both, "--taxonomy")
        assert TAXONOMY_RULE in _rules(tax)
        assert FINALIZE_RULE not in _rules(tax)
