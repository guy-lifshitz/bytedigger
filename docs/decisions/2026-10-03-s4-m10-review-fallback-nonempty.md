# S4/M10: fail-closed check that the review stdout-fallback path produced findings

**Status: DRAFT r1 (pre-gate; RED written, 11 fail / 7 guard-green)** · **Tier:** 2 (engine self-mod, one function + one error code, no flag) · **Class:** COVERAGE (review verdict integrity)
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

Conclusion: an empty or unstructured fallback review proceeds with `status=ok` and the build can ship with no review having happened. Fix = deterministic fail-closed check.

## 1. Contract

In the fallback branch, after the doc is persisted (kept for diagnosis) and `verdict = _derive_fallback_verdict(content)`:

- If `verdict == VERDICT_SUSPECT` -> emit `review_empty_fallback` `{phase: 6, reason, bytes}` and return `StepResult(status="error", error_code="E_REVIEW_EMPTY_FALLBACK", recoverable=False, step_name="write_review_artifact")`; `_persist_fix_feed` is NOT called.
- `reason` in {`body_empty` (content blank/whitespace), `no_structured_block` (structured extraction returned None), `unrecognised_severity` (see 2)}.
- Not blocked (unchanged): verdict `PASS` (explicit structured empty block), `PARTIAL`, `FAIL`.
- New code `E_REVIEW_EMPTY_FALLBACK` added to `error_codes.py` with description; no flag, no `flags_catalog` entry.

## 2. Adjacent hole closed in the same function

`_derive_fallback_verdict`: a structured block with >= 1 finding but zero findings carrying a recognised severity (CRITICAL/HIGH/MEDIUM/LOW, case-insensitive, stripped) currently counts as all-zero -> `PASS`. Fail-closed: such a block yields `SUSPECT` (reason `unrecognised_severity`). An empty list `[]` stays `PASS`. A block mixing recognised and unrecognised severities keeps its recognised-severity verdict.

## 3. Out of scope

Aggregator path (`aggregated_content` present): a SUSPECT there carries the fail-open suspect section by design (CA50885D) and must keep flowing. Empty 0-byte composite file goes through the aggregator parse path and is a separate watch item. No change to `_normalize_to_aggregated_findings`, to the satisfaction gate, to decorr, to docs beyond this file.

## 4. Acceptance criteria (RED `engine_py/tests/test_s4_m10_review_fallback_nonempty.py`)

Drive `_write_review_artifact` with `prev.data = {raw_response, doc_path, spec_path, red_log_path, green_log_path}` and no `aggregated_content`.

- AC1: raw in {"", "   \n", "Looks good to me.", "VERDICT: PASS", "## Findings\n[]", fenced json `[]` without the structured fence} -> `status=error`, `error_code=E_REVIEW_EMPTY_FALLBACK`, event `review_empty_fallback` with the right `reason`, no fix doc written, review doc persisted.
- AC2: valid structured block with one MEDIUM -> ok, `PARTIAL`; one CRITICAL -> ok, `FAIL`; marker `VERDICT: FAIL` with prose findings and no block -> ok, `FAIL`. Unchanged.
- AC3: valid structured empty block (real PASS shape) -> ok, `PASS`, no event.
- AC4: structured block with one finding severity `INFO` (or missing/non-string) -> error `E_REVIEW_EMPTY_FALLBACK`, reason `unrecognised_severity`; block with `[INFO, LOW]` -> ok `PARTIAL`; lowercase ` high ` counts as HIGH -> `FAIL`.
- AC5: aggregator path with `aggregated_content` that yields SUSPECT and a suspect finding list -> still `status=ok` (non-regression, CA50885D).
- AC6: `E_REVIEW_EMPTY_FALLBACK` present in the error-code registry with a non-empty description; the existing registry/docs consistency tests stay green.
- AC7 (static): no new import, no LLM call, no flag read in the new branch; the check is a pure function of `content`.

## 5. Known conflicts with existing tests (GREEN updates them; spec change, not test gaming)

Existing tests assert `status=ok` for a fallback whose verdict is SUSPECT (prose or `VERDICT: PASS` without a structured block). Under this spec those inputs fail closed, so GREEN rewrites each to use a structured block (keeps its intent) or to expect `E_REVIEW_EMPTY_FALLBACK` where the input IS the unstructured case: `test_bd89_p3b1_single_reviewer_only.py` ac9b (~595-600); `test_F7830037_insession_review_normalize.py` (~193, ~240); `test_GH1399_advisory_format_terminal.py` (~440, ~499, ~780); re-check `test_phase_6_review_return_discipline_CF838E6F.py`, `test_phase_6_stdout_fallback_verdict_4E0BAC38.py`. Policy note: GH1399 made normalization total to avoid paid retries on format drift; it did not promise that an unstructured review proceeds. Regenerate both `ERROR_CODES.md` files (guards `test_ac7_guard_error_codes_md_identical`, `error_codes --check`). `body_empty` must test the PRE-normalisation text (normalisation turns blank into "(no findings)").
