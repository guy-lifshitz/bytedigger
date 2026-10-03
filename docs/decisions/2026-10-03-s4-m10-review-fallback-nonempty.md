# S4/M10: fail-closed check that the review stdout-fallback path produced findings

**Status: DRAFT r2 (gate r1 REJECT 4 MAJOR: `-gate-r1.md`)** · **Tier:** 2 (engine self-mod, one function + one error code, no flag) · **Class:** COVERAGE (review verdict integrity)
**Note:** SUSPECT on the fallback path means "no JSON structured block and no PARTIAL/FAIL marker"; it also covers real `### SEVERITY:` blocks (the prompt's own format), which `_persist_fix_feed` parses and feeds to the fix worker. So SUSPECT alone is NOT "empty" (gate r1 M4); the rule below keys on findings actually parsed.
**Chokepoint:** `phase_6_review._write_review_artifact`, stdout-fallback branch (after `_derive_fallback_verdict`): the only place a fallback review becomes `status=ok`.
**Provenance:** audit hal#2320 §6 row M10 (P3b1b-ii 56fbbfa folded aggregation into `write_review_artifact`; absent composite = event `role_report_missing`, status ok, fallback to stdout/disk). Plan approved by Guy 2026-10-03 (audit repair step 4).

## 0. Consumer analysis (evidence, decides the shape of the fix)

Premise of the audit row ("zero findings read as clean") is not literally true at the verdict level: `_derive_fallback_verdict` returns `SUSPECT`, never `PASS`, for an empty body, whitespace, prose ("Looks good to me."), `VERDICT: PASS` without a structured block, `## Findings` + `[]` without the structured fence. But **nothing blocks on SUSPECT**:

| consumer | what it does with the review verdict |
|---|---|
| `_build_fix_prompt` (L2322/2348) | prints `REVIEW VERDICT: SUSPECT` into the fix-worker prompt; the only skip rule is `verdict PASS`, so the worker is told "every finding passed confidence >= 80, fix all" over an empty doc |
| `_persist_fix_feed` | forwards (empty) verified/suspect lists |
| satisfaction gate (L3453) | verdict-agnostic: `_decide_satisfaction_passed` + `_review_all_findings_suspect`, which can only RELAX a FAIL, never block |
| `write_decorr_artifact` (L5615) | blocks on its OWN SUSPECT only under `decorrelated_verify_enforce` (default off); a different verdict |
| `error_codes.py` | no code for an empty review |
| `phase_7_synthesize` (L458) | marks the review PRESENT iff the review doc exists, and phase 7 runs after a phase-6 abort: a kept doc would be reported as a review that happened (gate r1 M1) |
| `verify_findings` (helper.py ~421), `semantic_verifier` (~380), `_write_satisfaction_doc` (~2697) | read the verdict/doc downstream of the point where the check sits; none blocks on SUSPECT |

Conclusion: an empty or unstructured fallback review proceeds with `status=ok` and the build can ship with no review having happened. Fix = deterministic fail-closed check.

## 1. Contract

Definition. Fallback content is **findingless** iff ALL hold: (a) `extract_structured_findings(content)` is None, or a list whose entries yield zero finding objects with a recognised severity (see 2); (b) the fix-feed parse of the SAME bytes, `_parse_finding_blocks(_doc_section_body(content, "## Aggregated Findings"))` plus the suspect section, yields zero blocks (the identical call `_persist_fix_feed` makes); (c) `_derive_fallback_verdict(content)` is SUSPECT (so an explicit `VERDICT: PARTIAL/FAIL` marker, or a structured PASS/PARTIAL/FAIL, is never findingless).

In the fallback branch, BEFORE anything is written to `doc_path` by the engine and before `_persist_fix_feed`:

- If findingless -> emit `review_empty_fallback` `{phase: 6, reason, bytes}` where `bytes` = len of the pre-normalisation text (`_resolve_review_content` result) UTF-8; `reason` first match of: `body_empty` (pre-normalisation text blank after strip; normalisation would turn it into "(no findings)", so the check runs on the pre-normalisation text), `unrecognised_severity` (structured block with >= 1 entry but none with a recognised severity), `no_findings_parsed` (everything else).
- Evidence preservation (gate r1 M1): the diagnosis bytes (pre-normalisation text; if the reviewer already wrote `doc_path` on disk, that file's bytes) are written to `<scratchpad>/reviews/build-review.rejected.md` (os.replace of an existing doc_path, else write_text of the content) so that `doc_path` (`reviews/build-review.md`) does NOT exist after the failure and phase 7 reports `review: MISSING`. A stale `build-review-fix.md` from a previous cycle is removed (`unlink(missing_ok=True)`). No new `read_text`/disk read in `_write_review_artifact` (class_i_inventory pin): `os.replace`/`write_text`/`unlink` only.
- Return `StepResult(status="error", error_code="E_REVIEW_EMPTY_FALLBACK", recoverable=False, step_name="write_review_artifact", error=<names the rejected file and the reason>)`.
- Remedy class (GH1399 registry): **terminal**. Why not re-ask: the review result is cached per run (`invoke_review_llm` sentinel replays on restart and the fallback re-reads the same bytes), so a same-run retry cannot obtain a different review; a bounded paid re-ask is out of scope (§1c cancellation of GH1399 retries). Operator recovery: read `reviews/build-review.rejected.md`, then re-run phase 6 in a fresh run. `_CLASS_REGISTRY` in `test_GH1399_advisory_format_terminal.py` gets `E_REVIEW_EMPTY_FALLBACK: {remedy: "terminal", why, missing}` and the code is added to `error_codes.py` (+ both `ERROR_CODES.md`).
- Not blocked (unchanged): any content with a structured block with a recognised severity, any `### SEVERITY:` block parsed by the fix-feed parser, any explicit `VERDICT: PARTIAL/FAIL` marker.
- Accepted gap (named): a bare `VERDICT: PARTIAL/FAIL` with no findings anywhere stays ok (explicit reviewer claim); structured entries that are not JSON objects are filtered out by the extractor and count as zero.

## 2. Adjacent hole in the same function

`_derive_fallback_verdict`: a structured block with >= 1 finding object but none with a recognised severity (CRITICAL/HIGH/MEDIUM/LOW, case-insensitive, stripped) currently counts as all-zero -> `PASS`. Fail-closed: it yields `SUSPECT`; the findingless rule then rejects it with reason `unrecognised_severity` unless (b)/(c) say otherwise (an explicit `VERDICT: FAIL` marker alongside such a block: marker wins -> FAIL, not an error). Empty list `[]` stays `PASS`. Mixed recognised/unrecognised keeps the recognised-severity verdict.

## 3. Out of scope

Aggregator path (`aggregated_content` present): a SUSPECT there carries the fail-open suspect section by design (CA50885D) and keeps flowing. 0-byte composite file goes through the aggregator parse path (separate watch item). Aggregator errors that fall through to the fallback are reported under this code with the rejected file naming the bytes (accepted). No change to `_normalize_to_aggregated_findings`, satisfaction, decorr.

## 4. Acceptance criteria (RED `engine_py/tests/test_s4_m10_review_fallback_nonempty.py`)

Drive `_write_review_artifact` with `prev.data = {raw_response, doc_path, spec_path, red_log_path, green_log_path}`, no `aggregated_content`.

- AC1: raw in {"", "   \n", "Looks good to me.", "VERDICT: PASS", "## Findings\n[]" and similar no-fence JSON}: `status=error`, `error_code=E_REVIEW_EMPTY_FALLBACK`, `recoverable=False`, `step_name=write_review_artifact`, event with the right `reason` and int `bytes`, no fix doc, **`build-review.md` absent, `build-review.rejected.md` present holding the raw bytes**, and a phase-7 style check (`<reviews>/build-review.md` missing) reads MISSING.
- AC1b: reviewer already wrote `doc_path` on disk (real-LLM disk-first shape) with findingless text: after the failure `build-review.md` is gone and `build-review.rejected.md` holds those bytes; a pre-existing stale `build-review-fix.md` is gone.
- AC2: structured block with one MEDIUM -> ok `PARTIAL`; CRITICAL -> ok `FAIL`; `VERDICT: FAIL` + prose, no block -> ok `FAIL`; `VERDICT: PARTIAL` + prose -> ok `PARTIAL` (accepted gap pinned). Unchanged.
- AC2b (gate r1 M4): content with real `### SEVERITY: HIGH - title` finding blocks as the prompt requires, no JSON block, no marker -> `status=ok`, fix doc contains those findings (same as today), no event.
- AC3: valid structured empty block -> ok `PASS`, no event.
- AC4: block with one finding severity `INFO` / missing / non-string -> error, reason `unrecognised_severity`, recoverable False, step_name set; `[INFO, LOW]` -> ok `PARTIAL`; ` high ` -> `FAIL`; block with only `INFO` plus `VERDICT: FAIL` marker -> ok `FAIL`.
- AC4b: non-object entries only (`["CRITICAL: x"]`) in a structured fence -> error `no_findings_parsed`; malformed JSON / dict-root inside a structured fence -> error `no_findings_parsed`; reviewer echoing the prompt's JSON template with no recognised severities -> error.
- AC5: aggregator path with SUSPECT and a suspect list -> still ok (non-regression).
- AC6: code registered in `ERROR_CODES` with a description; `_CLASS_REGISTRY` entry exists with remedy `terminal`.
- AC7: behavioural purity (no LLM subprocess, no disk read beyond existing; the class_i_inventory read_text pin test stays green).

## 5. Existing tests that GREEN must update (spec change, not test gaming)

Each asserts `ok` on findingless fallback input; rewrite with a structured block (intent kept) or expect the new error where the input IS findingless: `test_bd89_p3b1_single_reviewer_only.py` ac9b (~595-600); `test_F7830037_insession_review_normalize.py` (~193, ~240); `test_GH1399_advisory_format_terminal.py` (~440, ~499, ~780) and its `_CLASS_REGISTRY` (add the code); `test_bd92_per_cycle_artifacts.py` (~154-170); `test_bd89_p3b1b_ii_aggregation_helper.py::test_ac5_missing_composite_emits_event_and_uses_stdout`; `test_phase_6_review_return_discipline_CF838E6F.py` ac6 (~307), ac9 (~479). Re-check `test_phase_6_stdout_fallback_verdict_4E0BAC38.py` (a block with all-unrecognised severities expecting PASS). Regenerate both `ERROR_CODES.md` (guards `test_ac7_guard_error_codes_md_identical`, `error_codes --check`).
