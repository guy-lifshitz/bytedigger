"""RED tests for GH962+GH952 — ground-truth-DDL lint FP class kill
(classify_db_token, rewired find_missing_ground_truth check 1, directed-repair guidance).

Frozen spec: SHARED/memory/Decisions/2026-07-17_GH962_ground_truth_ddl_fp_spec.md

`classify_db_token` does not exist yet in `ground_truth_verifier` -- its import
is deferred to inside the relevant test bodies (D1CF5FDF) so this file COLLECTS
cleanly and each test FAILS at assert time, never at collect time.

§1i: no singleton/time resources. §1l: UUT (`find_missing_ground_truth`,
`classify_db_token`, `directed_repair._build_prompt`, `lint_spec.py`) is never
mocked or monkeypatched.
"""
from __future__ import annotations

import subprocess

from helpers.engine_subprocess import engine_env
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_LIB_DIR = str(_ENGINE_ROOT / "bytedigger_engine" / "scripts" / "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from bytedigger_engine.scripts.lib.ground_truth_verifier import find_missing_ground_truth  # noqa: E402


# ─── shared fixture texts (per §3 AC table) ─────────────────────────────────

_FP1_TEXT = (
    "## §2 Design\n\n"
    "Validation: writer receives d.db but the spy intercepts before any DB call.\n\n"
    "## §3 Acceptance Criteria\n"
)

_FP2_TEXT = (
    "## §2 Design\n\n"
    "Inline citation: `compile_posture_raw(args.db, out_path)`\n\n"
    "## §3 Acceptance Criteria\n"
)

_FP3_TEXT = (
    "## §2 Design\n\n"
    "the CLI stores the path in args.db before dispatch\n\n"
    "## §3 Acceptance Criteria\n"
)

_AC4_TEXT = (
    "## §2 Design\n\n"
    "Some prose about pipeline-output/ppba-v2.db with no ground truth section.\n\n"
    "## §3 Acceptance Criteria\n"
)

_AC5_TEXT = (
    "## §2 Design\n\n"
    'fixture runs sqlite3.connect("cache.db") to seed rows\n\n'
    "## §3 Acceptance Criteria\n"
)

_AC6_TEXT = (
    "## §2 Design\n\n"
    'the report mentions "results.db" in passing\n\n'
    "## §3 Acceptance Criteria\n"
)

_AC7_TEXT = _AC4_TEXT.replace(
    "## §3 Acceptance Criteria",
    "ground-truth: never-opened pipeline-output/ppba-v2.db\n\n## §3 Acceptance Criteria",
)

_AC8_TEXT = (
    "## §2 Design\n\n"
    "## Data-Model Ground Truth\n\n"
    "No fence here, no opt-out marker.\n\n"
    "## §3 Acceptance Criteria\n"
)

_AC12_TEXT = (
    "## §2 Design\n\n"
    "First args.db then pipeline-output/ppba-v2.db mentioned in this line.\n\n"
    "## §3 Acceptance Criteria\n"
)


# ─── AC1 ─────────────────────────────────────────────────────────────────

def test_ac1_fp1_placeholder_db_no_finding() -> None:
    """AC1: FP1 corpus (`d.db` prose placeholder, no header) -> []."""
    result = find_missing_ground_truth(_FP1_TEXT, Path("/dummy"))
    assert result == [], f"expected [], got {result!r}"


# ─── AC2 ─────────────────────────────────────────────────────────────────

def test_ac2_fp2_verbatim_citation_args_db_no_finding() -> None:
    """AC2: FP2 corpus (`args.db` inside verbatim citation, no header) -> []."""
    result = find_missing_ground_truth(_FP2_TEXT, Path("/dummy"))
    assert result == [], f"expected [], got {result!r}"


# ─── AC3 ─────────────────────────────────────────────────────────────────

def test_ac3_fp3_prose_args_db_no_finding() -> None:
    """AC3: FP3 corpus (`args.db` in explanatory prose, no header) -> []."""
    result = find_missing_ground_truth(_FP3_TEXT, Path("/dummy"))
    assert result == [], f"expected [], got {result!r}"


# ─── AC4 ─────────────────────────────────────────────────────────────────

def test_ac4_existing_tp_path_like_db_still_flagged() -> None:
    """AC4 (regression pin): path-like `.db` token, no header -> 1 finding."""
    result = find_missing_ground_truth(_AC4_TEXT, Path("/dummy"))
    assert len(result) == 1, f"expected 1 finding, got {result!r}"
    assert result[0].rule_id == "missing-ground-truth-ddl", f"got {result[0]!r}"
    assert result[0].evidence == "pipeline-output/ppba-v2.db", f"got {result[0]!r}"


# ─── AC5 ─────────────────────────────────────────────────────────────────

def test_ac5_quoted_filename_with_open_signal_flagged() -> None:
    """AC5: quoted bare filename with an open-signal (sqlite3.connect) in the
    window -> 1 finding, evidence=='cache.db'."""
    result = find_missing_ground_truth(_AC5_TEXT, Path("/dummy"))
    assert len(result) == 1, f"expected 1 finding, got {result!r}"
    assert result[0].evidence == "cache.db", f"got {result[0]!r}"


# ─── AC6 ─────────────────────────────────────────────────────────────────

def test_ac6_quoted_filename_no_open_signal_anywhere_no_finding() -> None:
    """AC6: quoted bare filename with NO open-signal anywhere -> []."""
    result = find_missing_ground_truth(_AC6_TEXT, Path("/dummy"))
    assert result == [], f"expected [], got {result!r}"


# ─── AC7 ─────────────────────────────────────────────────────────────────

def test_ac7_never_opened_pragma_exempts_token() -> None:
    """AC7: AC4 text + per-token `ground-truth: never-opened <token>` pragma
    -> [] (pragma exemption)."""
    result = find_missing_ground_truth(_AC7_TEXT, Path("/dummy"))
    assert result == [], f"expected pragma to exempt, got {result!r}"


# ─── AC8 ─────────────────────────────────────────────────────────────────

def test_ac8_header_present_no_fence_still_empty_finding() -> None:
    """AC8 (regression pin, check 2 untouched): header w/o fence/opt-out ->
    1 finding rule_id=='empty-ground-truth-ddl'."""
    result = find_missing_ground_truth(_AC8_TEXT, Path("/dummy"))
    assert len(result) == 1, f"expected 1 finding, got {result!r}"
    assert result[0].rule_id == "empty-ground-truth-ddl", f"got {result[0]!r}"


# ─── AC9 ─────────────────────────────────────────────────────────────────

def test_ac9_classify_db_token_identifier_vs_pathlike() -> None:
    """AC9 (§1aa forcing fn): classify_db_token importable from
    ground_truth_verifier; True on AC4's path-like match, False on AC3's
    identifier-shaped unquoted match."""
    from bytedigger_engine.scripts.lib.ground_truth_verifier import classify_db_token, DB_REF_RE  # ImportError -> FAIL

    ac4_match = DB_REF_RE.search(_AC4_TEXT)
    assert ac4_match is not None
    assert classify_db_token(_AC4_TEXT, ac4_match) is True, (
        f"expected path-like token to classify as artifact: {ac4_match.group(0)!r}"
    )

    ac3_match = DB_REF_RE.search(_FP3_TEXT)
    assert ac3_match is not None
    assert classify_db_token(_FP3_TEXT, ac3_match) is False, (
        f"expected identifier-shaped unquoted token to classify as non-artifact: "
        f"{ac3_match.group(0)!r}"
    )


# ─── AC10 ─────────────────────────────────────────────────────────────────

def test_ac10_directed_repair_prompt_carries_ground_truth_guidance() -> None:
    """AC10: _build_prompt for gate 'spec_lint' with a
    missing-ground-truth-ddl finding contains 'never-opened' AND
    'Data-Model Ground Truth'; a differently-ruled finding contains neither."""
    from bytedigger_engine.lib.directed_repair import _build_prompt  # symbol exists, guidance is new

    prompt_with_guidance = _build_prompt(
        "spec_lint",
        "/s.md",
        "body",
        [{"rule": "missing-ground-truth-ddl", "path": "s.md", "line": 5, "evidence": "d.db"}],
    )
    assert "never-opened" in prompt_with_guidance, (
        f"expected 'never-opened' guidance, got prompt={prompt_with_guidance!r}"
    )
    assert "Data-Model Ground Truth" in prompt_with_guidance, (
        f"expected 'Data-Model Ground Truth' guidance, got prompt={prompt_with_guidance!r}"
    )

    prompt_without_guidance = _build_prompt(
        "spec_lint",
        "/s.md",
        "body",
        [{
            "rule": "unverified-function-citation",
            "path": "s.md",
            "line": 5,
            "evidence": "foo()",
        }],
    )
    assert "never-opened" not in prompt_without_guidance, (
        f"unexpected 'never-opened' guidance leaked, got {prompt_without_guidance!r}"
    )
    assert "Data-Model Ground Truth" not in prompt_without_guidance, (
        f"unexpected 'Data-Model Ground Truth' guidance leaked, got "
        f"{prompt_without_guidance!r}"
    )


# ─── AC11 ─────────────────────────────────────────────────────────────────

def test_ac11_lint_spec_subprocess_no_longer_flags_fp_corpus(tmp_path) -> None:
    """AC11 e2e: lint_spec.py subprocess on FP1+FP2+FP3 text (no header) ->
    exit 0, no 'missing-ground-truth-ddl' in stdout; on AC4 text -> exit 1,
    stdout contains 'missing-ground-truth-ddl:pipeline-output/ppba-v2.db'."""
    lint_spec = _ENGINE_ROOT / "bytedigger_engine" / "scripts" / "spec_lint" / "lint_spec.py"
    assert lint_spec.exists(), f"lint_spec.py driver not found at {lint_spec}"

    fp_text = "## §2 Design\n\n" + "\n".join(
        [
            "Validation: writer receives d.db but the spy intercepts before any DB call.",
            "Inline citation: `compile_posture_raw(args.db, out_path)`",
            "the CLI stores the path in args.db before dispatch",
        ]
    ) + "\n\n## §3 Acceptance Criteria\n"

    fp_spec = tmp_path / "fp_spec.md"
    fp_spec.write_text(fp_text)
    proc_fp = subprocess.run(
        [sys.executable, str(lint_spec), str(fp_spec), "--hal-root", str(tmp_path)], env=engine_env(),
        capture_output=True, text=True,
    )
    assert proc_fp.returncode == 0, (
        f"expected exit 0, got {proc_fp.returncode}; stdout={proc_fp.stdout!r}"
    )
    assert "missing-ground-truth-ddl" not in proc_fp.stdout, (
        f"expected no FP token, got stdout={proc_fp.stdout!r}"
    )

    ac4_spec = tmp_path / "ac4_spec.md"
    ac4_spec.write_text(_AC4_TEXT)
    proc_ac4 = subprocess.run(
        [sys.executable, str(lint_spec), str(ac4_spec), "--hal-root", str(tmp_path)], env=engine_env(),
        capture_output=True, text=True,
    )
    assert proc_ac4.returncode == 1, (
        f"expected exit 1, got {proc_ac4.returncode}; stdout={proc_ac4.stdout!r}"
    )
    assert "missing-ground-truth-ddl:pipeline-output/ppba-v2.db" in proc_ac4.stdout, (
        f"expected TP token, got stdout={proc_ac4.stdout!r}"
    )


# ─── AC12 ─────────────────────────────────────────────────────────────────

def test_ac12_multi_token_first_artifact_wins_not_first_token() -> None:
    """AC12: text has a non-artifact token (`args.db`) BEFORE an artifact
    token (`pipeline-output/ppba-v2.db`) -> exactly 1 finding, evidence is
    the artifact token, not the first lexical token."""
    result = find_missing_ground_truth(_AC12_TEXT, Path("/dummy"))
    assert len(result) == 1, f"expected exactly 1 finding, got {result!r}"
    assert result[0].evidence == "pipeline-output/ppba-v2.db", f"got {result[0]!r}"
