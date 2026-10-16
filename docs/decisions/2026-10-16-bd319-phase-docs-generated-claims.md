---
red_tests:
  - engine_py/tests/test_bd319_phase_claims.py
sibling_tests:
  - tests/test_bd89_p3b1c_single_reviewer_md_gates.py
  - tests/test_bd190_build_md_hook_truth.py
  - tests/test_worker_write_bash.py
  - engine_py/tests/test_phase_6_satisfaction_multi_evaluator.py
paths:
  - engine_py/bytedigger_engine/phase_claims.py
  - engine_py/bytedigger_engine/workflows/phase_6_review.py
  - phases/phase-6-review.md
  - phases/phase-0-classify.md
  - CHANGELOG.md
---
# bd#319: phases/*.md claims are rendered from code, not typed by hand

Refs #319 (slice of #89). Base: origin/main @ e027c49. Tier 2. Owner's rule (comment 16.10): PREVENT, do not check. The primary fix is a generator; the test is insurance.

## 0. Step 0 (reproduction on fresh origin/main)

Reproduces. `phases/phase-6-review.md` l.69-80 and l.225-247 and `phases/phase-0-classify.md` l.290 state tier thresholds (80/85/90), evaluator count (3), dimension counts (3/5/5), reviewer count (1) and the model as typed prose. Sources: `bytedigger.json` `satisfaction_thresholds` (read by nobody but the docs), and `n=3` as a bare literal at `phase_6_review.py:3454` (also the default at l.3214). No test relates the prose to either. `tests/test_bd190_build_md_hook_truth.py` reads `commands/build.md` only (its AC4 even asserts it does not touch `phases/`).

## 1. Class and chokepoint (§1-PREFLIGHT)

Class: a machine-checkable claim (threshold, count of evaluators/reviewers/dimensions, model name) typed by hand in a phase doc, beside a code source of truth, with nothing tying them (bd#128 reviewer count 7/9; phase-6 evaluators, #317).
Chokepoint: ONE generator, `engine_py/bytedigger_engine/phase_claims.py`. It owns the claim registry and renders the blocks; the docs carry only generated blocks between markers. §1g: one canonical source per fact: thresholds and model from `bytedigger.json`, tier policy from the module-level literal `SATISFACTION_TIER_POLICY` in `phase_6_review.py`. The engine itself derives the COMPLEX evaluator count from that same literal (`n=SATISFACTION_TIER_POLICY["COMPLEX"]["evaluators"]`), so changing the number in code is the only way to change it, and the doc follows by regeneration.

## 2. Change

A. `phase_6_review.py`: add module-level literal (only literals: str/int/tuple/dict, so an AST `literal_eval` can read it without importing the module):
```
SATISFACTION_TIER_POLICY = {
    "SIMPLE":  {"reviewers": 1, "evaluators": 1, "dimensions": ("Spec compliance", "Test quality", "Code quality")},
    "FEATURE": {"reviewers": 1, "evaluators": 1, "dimensions": (<those three>, "Completeness", "Boy Scout compliance")},
    "COMPLEX": {"reviewers": 1, "evaluators": 3, "dimensions": (<the same five>)},
}
```
and replace the two bare `3`s (`n: int = 3` default l.3214, call `n=3` l.3454) by values taken from `SATISFACTION_TIER_POLICY["COMPLEX"]["evaluators"]`. No behaviour change.

B. New `engine_py/bytedigger_engine/phase_claims.py` (stdlib only, no import of the engine's workflows):
- `load_claims(repo_root) -> dict`: reads `bytedigger.json` (`satisfaction_thresholds`, `validation_model`) with `json`, and `SATISFACTION_TIER_POLICY` from `workflows/phase_6_review.py` with `ast.parse` + `ast.literal_eval` of the assignment value. A missing file, a missing assignment, a non-literal value, a missing tier, a non-int threshold, or a tier missing from either source raises `PhaseClaimsError` (never a silent default).
- `BLOCKS`: registry `{block_id: (doc_path, render_fn)}` with three ids: `phase6-tier-policy` (the table of phase-6 l.71-80: rows for the three tiers and the three "+ HIGH security" rows), `phase6-satisfaction-steps` (the three `###` tier headings of Step 3 with their `Dimensions:` and `Threshold:` lines, and the evaluator-count line), `phase0-satisfaction-row` (the `| 6 Satisfaction |` row and the `| 6 Review |` row of the phase-0 table).
- Block markers: `<!-- BEGIN GENERATED <id> -->` and `<!-- END GENERATED <id> -->` (each on its own line). `render_doc(text, claims) -> str` replaces the content between the markers of every registered id found in that doc; it is idempotent.
- CLI `python3 -m bytedigger_engine.phase_claims --write | --check [--repo-root P]`: `--write` regenerates the docs in place; `--check` exits 1 and names each stale block id when a doc differs from its render, exit 0 otherwise. Not wired into pre-commit in this lot (out of scope).

C. Docs: wrap the three places in markers and run `--write`. Rewrite the remaining prose that carried the numbers, without numbers: phase-6 l.19 "COMPLEX 3-Opus evaluators" becomes "the COMPLEX evaluators"; Step 3 COMPLEX items 3-6 (majority vote / median of the scores / strictest / threshold) say "the majority of the evaluators", "the median score", "the threshold in the table above" (item 1 and the heading stay in the generated block, together with the literals `3-Agent Majority Vote` and `Launch 3 Opus agents in parallel`, which `test_bd89_p3b1c` AC11 pins; they are now rendered from `evaluators`); phase-0 l.222 placeholder `[1 composite reviewer]` becomes `[reviewer count, from the phase 6 policy table]`. Gate r1 fixes: (1) the three lines that say "Phase 6 reviewers" (phase-6 l.52, l.149, l.180) are reworded to "the reviewers of this phase" so the AC6 pattern has no false positive. (2) Step 3 COMPLEX shape: the generated block ends with the line `1. Launch N Opus agents in parallel.`; the hand-typed "each with different focus" clause and the three `Evaluator A/B/C` bullets are DELETED (the code runs identical calls, so the claim is false and with N != 3 it would contradict the count; #317 removes the whole section later). Items 2-6 follow the block with the numbers removed as above. AC4 asserts no line outside the blocks matches `Evaluator [A-Z]\b` or `different focus`.

D. CHANGELOG line.

## 3. Acceptance criteria

- AC1: `load_claims` on the repo returns thresholds `{SIMPLE:80, FEATURE:85, COMPLEX:90}`, `validation_model "opus"`, and for each tier `reviewers`/`evaluators`/`dimensions` equal to the `SATISFACTION_TIER_POLICY` literal.
- AC2: `render_doc` over the three docs reproduces them byte for byte (no stale block) on main after GREEN; each registered id occurs exactly once between one BEGIN and one END.
- AC3 (precedent case): set `satisfaction_thresholds.FEATURE` to 86 in a copy of `bytedigger.json` and run `--check` on the copy: exit 1, stdout names `phase6-tier-policy` and `phase6-satisfaction-steps` and does NOT name `phase0-satisfaction-row` (it carries no threshold). After `--write` the copy passes `--check` and the phase-6 table contains `>=86%`.
- AC4: change `evaluators` for COMPLEX to 5 in a copy of `phase_6_review.py` and run `--check` on the copy: exit 1, names `phase6-satisfaction-steps` and `phase0-satisfaction-row` and `phase6-tier-policy`; after `--write` the text `Launch 5 Opus agents in parallel` is in phase-6 and `5x Opus voting` in phase-0.
- AC5: editing a number inside a generated block by hand (80 to 81 in phase-6) fails `--check`; editing text outside the blocks does not.
- AC6 (prose guard, the insurance): over every `phases/*.md`, outside generated blocks, no match of: `>=\s*(80|85|90)\s*%`, `\b\d+\s*[x×]\s*(opus|sonnet|haiku)\b` (case-insensitive), `\b\d+-(agent|opus)\b`, `\b\d+/\d+\s+PASS\b`, `median of \d`, `launch \d+ `, `\b\d+ (composite |parallel )?(reviewers?|evaluators?|dimensions?)\b`; the bracketed placeholder `[1 composite reviewer]` is such a match and must be gone. Fed a doc with an extra line `Launch 3 Opus agents` outside the block, the guard function reports it with file and line (negative case on a tmp copy).
- AC7: a `BEGIN GENERATED <id>` marker with an id not in `BLOCKS`, an unbalanced BEGIN/END, or a block id of the registry that is absent from its doc: `--check` exits 1 naming it (claim without a source is an error).
- AC8: `load_claims` raises `PhaseClaimsError` for: missing `bytedigger.json`; `satisfaction_thresholds` lacking a tier; a non-int threshold (`"90"`, `True`); `SATISFACTION_TIER_POLICY` absent; a non-literal value (a name or call); a tier lacking `evaluators`. No default is substituted.
- AC8b: the CLI turns `PhaseClaimsError` into exit code 2, the message on stderr, nothing written (never exit 0). `--write` on a doc with unbalanced, nested or duplicated markers also exits 2 and leaves every doc byte-identical. `--check` prints the stale/invalid block ids on stdout, one per line.
- AC9b: `n` is read from `SATISFACTION_TIER_POLICY` at call time inside the function (no definition-time default argument bound to the literal), which AC9's monkeypatch exercises.
- AC9: the engine derives n from the literal: with the module's `SATISFACTION_TIER_POLICY["COMPLEX"]["evaluators"]` monkeypatched to 2, the COMPLEX satisfaction path dispatches exactly 2 evaluator calls (reuse the call-count stub style of the existing multi-evaluator tests in `engine_py/tests/test_phase_6_satisfaction_multi_evaluator.py`); SIMPLE/FEATURE still dispatch one.
- AC10 (pin, green today): `tests/test_bd89_p3b1c_single_reviewer_md_gates.py` and the bd#190 test pass unchanged.

## Known limitations

Enforcement gap (gate r1): the engine gates satisfaction on a flat `cfg["satisfaction_threshold"] or 80`; `satisfaction_thresholds` in `bytedigger.json` is read by no engine code, so the docs now agree with the config, not with the runtime gate. Only `COMPLEX.evaluators` is bound to engine behaviour; `reviewers`, SIMPLE/FEATURE evaluators and `dimensions` stay a declared second copy. An OFI is filed for wiring the thresholds into the gate. No range check on thresholds and no check for extra tiers in this lot.

Model names in prose outside the blocks (the word "Opus" in running text) are not checked: the model is rendered only inside blocks; a prose model claim has no machine-readable form here. Dimension descriptions in the evaluator prompt (`phase_6_review.py` ~l.3054) are still hand-typed in code; the doc matches `SATISFACTION_TIER_POLICY`, which is the doc-facing source. #317 will remove the multi-evaluator path; it then edits one literal and regenerates.

## 4. Out of scope

`commands/build.md`, the other phase docs (they carry no such claims today, the AC6 guard covers them), pre-commit/CI wiring of `--check`, `bd-drift-check.py` and `phases-docstring` (unreliable, HAL #1866/#2325).
