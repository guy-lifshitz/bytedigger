# bd#91: pre-GREEN gate is strict-AND, covers every AC, and does not soften by vote

**Status: r5 (APPROVED_WITH_FIXES by gate r4, fixes applied; r1, r2, r3 REJECTED by Opus gate; no round 5 gate, proceed to RED).** Branch `bd91-pregreen-strict-and`, base `origin/main` `56fbbfa`.

| | |
|---|---|
| **Issue** | bd#91 (HAL engine_py @456d46e97 incident family) |
| **Class** | SYSTEMATIC |
| **Chokepoint** | Two: (1) `phase_5_implement._gate_on_validation` together with the new pure resolver `_resolve_gate_passed(markdown_verdict, structured, ac_coverage)` — the only place that decides "pre-GREEN gate passed"; (2) `phase_45_spec._invoke_review_llm` — the only place where the reviewer's verdict becomes SHIP/REVISE. No calling code makes the decision around them. |
| **Tier** | Option D (manual RED -> Opus gate -> GREEN): production engine code, not `/build` and not MICRO. |
| **Rev** | r5 |
| **Enforcement layer** | Deterministic: RED tests `engine_py/tests/test_bd91_*.py` (including a per-entry `flags_catalog` check; there is NO repo-wide lint for expired `flip-by:`). The prompt edit is a secondary layer and does not count as enforcement on its own (Principle C). |
| **Side of the seam** | engine |

## §1 Problem (measured at `56fbbfa`)

| # | Hole | Where it is now |
|---|---|---|
| 1 | The validator may record an AC without a RED test as `[GREEN-regression-deferral]` in Quality Findings instead of FAIL | `phase_5_implement.py:6606-6614`, item 2 "TDD-pure GREEN-regression deferral" inside `_build_validation_prompt` (`:6464`) |
| 2 | `structured.approve` overrides the markdown verdict in both directions. A mismatch only warns (`validation_verdict_drift`, `:6866-6875`); `passed = bool(structured.approve)` (`:7011-7012`). The docstring of `_VALIDATION_STABLE_PREFIX` meanwhile claims "Verdict line is the authoritative gate" — the code contradicts it. | `_write_validation_doc`, `_gate_on_validation`, `_canonical_gate_verdict` (`:446`) |
| 3 | `HAL_SPEC_DEFECT_REROUTE` default is `"0"`, flip-by 2026-08-14 expired. Without the flag, SPEC_DEFECT goes down the legacy TEST_GAP branch (one more test retry on a defective spec). | `flags_catalog.py:664`, `phase_5_implement.py:7037` (`get_config().flag(...)`) |
| 4 | When `ACCEPT != result`, `_verify_spec_ac_dsl` only emits `spec_ac_dsl_warn` and lets the spec through; only `HAL_AC_DSL_GATE_ENFORCE=1` blocks. Default `"0"`, flip-by 2026-07-25 expired. | `flags_catalog.py:524`, `phase_45_spec.py:3178,3212` |
| 4a | **Flag mechanics (gate r1 #1).** `get_config().flag()` is true only when the env value is exactly `"1"` (`config_provider.py:56-58`); the catalog default is not read. Changing the catalog alone enables nothing. The canonical "enabled unless `0`" predicate is `gate_enabled` (`:52-54`). | `phase_5_implement.py:7037`, `phase_45_spec.py:3178,3212` |
| 5 | `_frozen_revise_repoll`: after REVISE the reviewer is polled N more times (`spec_frozen_review_repolls`/`spec_review_repolls`, default 2), and a strict majority of SHIP cancels the REVISE. | `phase_45_spec.py:3895-3941`, call at `:3977` |

**Checking the issue's "warm-resumed session" premise.** In bd it is wrong: `invoke_review_llm` runs with `hard_gate=True`, and `_dispatch_backend` (`llm_subprocess.py:~1728`) forces `fresh_session` for any `hard_gate`/judge (bd#82, bd#101; warm resume is allowed only for writer steps from `_WARM_RESUME_STEPS`). So the re-poll already runs in a fresh session. The real defect is the voting itself: an independent, qualified REVISE is cancelled by the number of repeated queries to a non-deterministic judge. The decision below rests on that, not on the session API.

## §2 Design

### §2.0 Decisions on the open questions of r1 (fixed)

| # | Decision |
|---|---|
| D1 | Forward Map: the prompt prescribes a fixed format, one bullet per AC: `` - AC<n>: `test_name` `` or `` - AC<n>: `MISSING` ``. The parser is tolerant (see §2.1). Blocking, no shadow phase. |
| D2 | `retire-by:2027-01-15 Refs #91` accepted. The flags remain kill-switches (`"0"` = legacy byte-for-byte) and are removed only after a clean window. |
| D3 | `ac_gap` consumes a cycle (it is a FAIL like any other, bounded by `_resolve_validation_cycle_cap`). Uncovered ids go at the HEAD of `findings` for the directed RED round of the next cycle (GH706). |
| D4 | The re-poll removal stands. |

### §2.1 AC coverage: deterministic

The single computation point is `_write_validation_doc` (it has `validation_raw` and `prev.data["spec_path"]`). `spec_ac_ids` is NOT threaded through `_invoke_validation_llm`: `extra_data` there is an explicit whitelist (`:6738-6746`, out of scope), the ids would be lost, and coverage would become a no-op. The chain AC1 must traverse: `_write_validation_doc` -> `_verify_validation_citations` (passes data through unchanged, `helper.py:479`) -> `_gate_on_validation`. `_write_validation_doc` rebuilds `data` from a whitelist (`:6891-6906`), so the `ac_coverage` key is added there explicitly.

Code (all in `phase_5_implement.py` next to `_build_validation_prompt`/`_write_validation_doc`, i.e. BELOW line 6240; imports are deferred only, inside functions):

- **Spec ids: `verdict_verify.parse_spec_ac_ids`** (§1g, one parser; section-scoped, also the source for `ac_dsl.admit`). No second parser and no whole-file regex are introduced (otherwise phantom ACs come from §5 tables and foreign matrices). It returns a set: the order for the report is natural sort. Id comparison is case-insensitive on the suffix (`AC1a` == `AC1A`; `_normalize_ac_id` uppercases only the `AC` prefix, `ac_dsl.py:145-149`).
- **Header extension (`verdict_verify` only, gate r3 #3).** `_AC_SECTION_HEADER_RE` (`verdict_verify.py:40`) requires "acceptance criteria"; headers like `## §3 Acceptance` yield an empty set and coverage is silently switched off. The header search in `verdict_verify.parse_spec_ac_ids` becomes two-pass (gate r2 #4): pass 1 = the current `^#{2,6}\s.*acceptance criteria` (byte-for-byte behavior); ONLY if no header is found, pass 2 = `^#{2,6}\s.*\bacceptance\b` with `re.IGNORECASE`, like pass 1. This way headers such as `### Acceptance-test harness` or `## change-vs-acceptance` do not steal the section from specs that have "acceptance criteria". In pass 2 ONLY table rows `| AC<n> |` are taken; the numbered-list fallback (`_AC_NUMBERED_RE`) is disabled so that a numbered list under a bare `## Acceptance` does not yield phantom `AC1..ACn`. **The copy `phase_6_review._parse_spec_ac_ids` (`:3219`; header regex `:3207`) is NOT changed (decision r4).** It feeds the hard gate of phase 6: `_verify_ac_checklist` (`phase_6_review.py:3298`, GH388, default on; fail -> `ac_checklist_fail`) and the satisfaction prompt (`:2806-2807`, `:2846-2847`: "If the spec has no ## Acceptance Criteria section, write `- none`"). Extending its header would move `## §N Acceptance` from `skip/no_spec_acs` to enforced and create a prompt conflict (`- none` versus a list of ids), i.e. it would change phase 6 behavior, which is out of scope for #91. The divergence between the two copies for `## §N Acceptance` is DELIBERATE and is pinned by the AC29 pinning test and a line in the docstring of `verdict_verify.parse_spec_ac_ids` (the only docstring/comment edits: here and in the comments at `verdict_verify.py:18` and `:39`). A side consumer of the extension: `verdict_gate.run_gate` / the GH517 parity lint (`verify_ac_parity`, seam `phase_5_implement.py:~7195`, warn-only without `HAL_VERDICT_GATE_LINT_ENFORCE`) for specs with `## §N Acceptance (RED …)` (about 15 in `docs/decisions`) moves from SKIP to a checked state; declared in §5.
- `_forward_map_coverage(validation_raw, ac_ids, red_texts: list[str] | None) -> {"status": "covered"|"uncovered"|"unverifiable", "uncovered": [...], "reasons": {id: code}, "bullets": {id: text}, "unverifiable_reason": str|None}` (gate r3 #4). The function is pure and reads no files: `red_texts` = texts of the RED files that were read; `None` or `[]` = `unverifiable` with `unverifiable_reason="red_files_unreadable"`. AC2 passes `red_texts` directly. The files are read by the caller (`_write_validation_doc`) through the named helper `_read_red_texts(ctx, prev) -> list[str] | None` (§1aa); see path resolution below.
  - Section `## Forward Map`; an id is a word with the prefix `AC[- ]?` (like `verdict_verify._AC_TABLE_RE`), `AC1` does not match `AC10`. Ids are taken ONLY from the bullet head: from the start of the bullet (or the first cell of a table row `| AC… |` inside the Forward Map section) up to the first separator `:`, `->`, `→`, `—`, `–`, `=` or the first backtick. Lists and ranges are expanded only inside the head. AC tokens after the head are neither ids nor citations (rule (a)).
  - Explicit lists and ranges are expanded: `AC1, AC2`, `AC1–AC3`, `AC1..AC3`.
  - **RED file path resolution (gate r3 #1).** In production `red_test_paths` are repo-relative (`_derive_red_paths_via_git_diff`, `phase_5_implement.py:540-568`, from `git diff --name-only`); the other consumers join them with `git_cwd`. `_read_red_texts`: an absolute entry is used as-is; a relative one is resolved as `Path(_resolve_git_cwd(ctx, prev)) / entry` (§1g; `_resolve_git_cwd` is at `:2063`, only CALLED, not edited). The process working directory is not used directly (only as the last fallback inside `_resolve_git_cwd`, `:2067`). Reading happens inside a `try` (`OSError`/`UnicodeDecodeError` -> the file is skipped); a list that is empty/missing/with no readable file -> `None` -> `unverifiable` (`red_files_unreadable`) plus an event; a partially unreadable list is checked against the readable files.
  - **Citation candidates (gate r3 #2, language-agnostic).** A candidate = the content of ANY backtick span in the bullet after the id (any language: `test_x`, `TestX`, `testX`, `path::name`, `file.test.ts > name`, a test description `it("rejects empty input")`), with normalisation, in order: (1) if the span has the form `it("…")`/`test("…")`/`describe("…")`, the candidate is the string inside the quotes (a description); (2) take the last segment after ` > `; (3) strip surrounding quotes `"`/`'`; (4) if the span contains no spaces: take the LAST segment after `::` (pytest `path::Cls::name`, Rust `mod::tests::name`); then, if it has the form `A.B.c` with identifier segments (not a file name with an extension from the `path_classifier` test-extension list), the last segment; then cut a trailing `[…]` (pytest param id) and `(…)`/`()`; for `Test\w+/…` (Go subtest), the part before the first `/`. The result is an identifier (rule (c) and the identifier boundary apply) or a description (rule (d) and substring match). A bare (no backtick) token matching `(?<![\w])(test_\w+|Test\w+|test[A-Z]\w*)(?![\w])` is also a candidate (the legacy format `- AC1 -> test_one`, which `_VALIDATION_STABLE_PREFIX` requires and bd139 uses). `MISSING` = the whole word in upper case.
  - **What is never a citation (gate r3 #6).** A candidate is discarded (-> `no_test_cited` if no others remain) if it: (a) equals an AC id (`^AC[\w-]*$`, case-insensitive); (b) is in the reserved set {`n/a`, `na`, `none`, `tbd`, `deferred`, `missing`, `todo`, `-`, `?`} case-insensitively or equals `[GREEN-regression-deferral]`/`[passes-pre-fix]`; (c) is an identifier (`^[A-Za-z_]\w*$`) shorter than 6 characters or without `_`, a digit, or an upper-case letter after the first (so `assert`, `def`, `test`, `pytest` fail by shape); (d) is a description string (contains non-identifier characters) shorter than 8 characters.
  - **The citation must exist, as a whole token (gate r2 #3, r3 #6).** The remaining candidate counts only if it occurs in the text of at least one read RED file as a WHOLE token: an identifier — with identifier boundaries `(?<![A-Za-z0-9_])tok(?![A-Za-z0-9_])` (so `test_a` is NOT found inside `test_ac1_x`), case preserved; a description string — an exact substring match after whitespace normalisation (quotes `"`, `'`, `` ` `` are allowed in the file). Language syntax is not parsed. Not found -> `citation_not_found`.
  - **Known limitation (deliberate, deterministic-first).** Any candidate from the bullet that is found in a RED file counts, including an identifier mentioned in prose; the second layer is the validator's markdown+structured vote and the format prescribed by the prompt, `- AC<n>: \`test\``. Also: `red_test_paths` is empty in `prev` but persisted on disk — coverage silently degrades to `unverifiable` (acceptable).
  - An AC is covered iff there is a bullet with the id, a citation found in it (by the rule above), and no `MISSING`. Duplicates (gate r2 edge 7): if there are several bullets for an id, the AC is uncovered when ANY of them contains `MISSING`; otherwise it is covered when at least one contains a found citation.
  - Spec Compliance: status = the first word after the id, skipping the separators `:`, `-`, `->`, `→`, `—`, `–`, `=`, spaces and markdown emphasis `*`/`_` (real format `AC3 → partial: …`, bd139 `:351-354`), case-insensitive. An AC is uncovered only if the status is `missing`/`partial` ("no missing negatives" does not count).
  - Reason codes (§1n, complete list): `absent` (no bullet), `MISSING`, `no_test_cited`, `citation_not_found`, `spec_compliance_missing`, `spec_compliance_partial`.
- `data["ac_coverage"]` is always written. **A missing `ac_coverage` key in `prev.data` = `unverifiable` (NOT fail, NOT uncovered, no KeyError)**: about 6 sibling files call `_gate_on_validation` with a hand-assembled `prev` (see §5), plus resume with an old sentinel. It is read only through `.get`.
- **`unverifiable`** (event `ac_coverage_unverifiable`, `reason`; gate r3 #6: emitted EXACTLY ONCE per chain invocation (per validation cycle): `_write_validation_doc` emits when it computed the status `unverifiable`; `_gate_on_validation` emits ONLY when the `ac_coverage` key is absent from `prev.data` (`reason="missing_key"`) and NEVER emits again if the key is present (including with status `unverifiable`); the next cycle is a new event): a spec with no parseable ACs (legacy), an unreadable file (reading inside `try`), a parser exception, no `spec_path`. The markdown+structured pair decides. An empty AC list is a separate visible state, not "covered".
- **Uncovered AC = FAIL** with reason `ac_gap`. Output fields (definition of "reject_reason", gate r1 #5): in r1 `reject_reason` was a field of the validator's structured block, not an engine field. Here: (a) the `reason` from `_resolve_gate_passed` = `"ac_gap"` is written to `data["gate_reason"]` and to the reject-log line (`_log_validation_reject`) with `reason_code="VALIDATION_AC_GAP"` (the reject-log dictionary is `VALIDATION_*`, `reject_log.py:216-222`; the literal `ac_gap` is not written to `reason_code`); (b) the ids are written to `data["ac_gap_ids"]`, to the head of `data.findings` in the GH706 form `ENGINE AC-COVERAGE GAP (cycle N): AC3, AC4`, and to the payload of the event `ac_coverage_gap` (`uncovered`, `cycle`, `bullets` for calibration). `gate_reason` is present on THREE returns of `_gate_on_validation`: PASS (`phase_5_implement.py:~7215`, values `and`/`md_only`, gate r3 #5), below cap, and terminal; `ac_gap_ids` and the head of `findings` — on both fail returns: below cap and terminal (`cycle == cap`, `phase_5_implement.py:~7163-7177`, where `E_VALIDATION_FAILED` builds its own dict; the implementer adds the fields there too, AC25).

**Prompt (secondary layer).** Item 2 "TDD-pure GREEN-regression deferral" is removed; replaced with: "Every AC must have a test in the Forward Map, exactly one bullet per AC, in the format `- AC<n>: \`test_name\`` or `- AC<n>: \`MISSING\``. An AC whose behavior is already satisfied before the fix is not exempt: list its test and mark it in Quality Findings as `[passes-pre-fix]` (informational). No test = `MISSING` + FAIL." The token `[GREEN-regression-deferral]` disappears from code and prompt. The requirement "every AC fails in RED" is not introduced (RED-gate policy is out of scope); the loophole of turning a missing test into a note disappears.

### §2.2 Strict-AND: `_resolve_gate_passed(markdown_verdict, structured, ac_coverage) -> (passed, reason)`

Rules are applied STRICTLY in order, the first match decides. The table is total over 4 (markdown PASS/FAIL/UNKNOWN/PARTIAL) x 4 (structured: approve true / approve false / absent / unparsed; absent and unparsed = "no second voice") x 2 (coverage: uncovered / not uncovered, where unverifiable and a missing key = not uncovered) = 32 cells.

| # | Condition | passed | reason |
|---|---|---|---|
| 1 | markdown == FAIL (any structured, any coverage) | no | `markdown_fail` |
| 2 | markdown == UNKNOWN (any structured, including absent; any coverage) | no | `markdown_missing` (fail-closed, contract "UNKNOWN treated as FAIL") |
| 2b | markdown == PARTIAL or any token other than PASS/FAIL/UNKNOWN (any structured; any coverage) | no | `markdown_partial` (fail-closed) |
| 3 | markdown == PASS, structured approve false | no | `structured_veto` |
| 4 | markdown == PASS, coverage uncovered (structured approve true/absent/unparsed) | no | `ac_gap` |
| 5 | markdown == PASS, structured approve true, coverage not uncovered | yes | `and` |
| 6 | markdown == PASS, structured absent/unparsed, coverage not uncovered | yes | `md_only` |

**PARTIAL (gate r2 #1, policy fixed).** The citation verifier sets `forwarded["verdict"] = "PARTIAL"` (`lib/plugins/anti_hallucination/helper.py:589-590`; `verify_completeness_issues > 0` gives FAIL, `:587-588`), and this value reaches `_gate_on_validation` via `prev.data["verdict"]`. Fail-closed is chosen: PARTIAL never passes under strict-AND, just like UNKNOWN (rule 2b; the numbering 2b keeps the numbers 3-6). A behavior change relative to today: PARTIAL + `approve:true` passes today (structured overrides), after r3 it does not; one unverified quote line costs a cycle and is terminal at cap — a deliberate price (no voice overrides a demoted verdict). Sibling edit: `test_phase_5_gate_verdict_canonical_gh349.py:276` (`test_canonical_gate_verdict_helper_invariant_matrix`, a loop over `"PARTIAL"`) — checks only `_canonical_gate_verdict`, stays green; confirmed by a run.

**Who decides the category.** `verdict_category` is taken from the validator's structured block (`_resolve_verdict_category`), including `SPEC_DEFECT`; the engine forces `TEST_GAP` ONLY when `reason == "ac_gap"`. Since `structured_veto` (rule 3) comes before `ac_gap` (rule 4), a validator SPEC_DEFECT verdict (always approve false) is never suppressed by `ac_gap`. `ac_gap` is not `SPEC_DEFECT`: reroute into the spec happens only on the validator's own decision.

Fail-closed on "markdown missing + approve true": the price is one extra cycle, bounded by the cap. Structured absent/`json_error`/`schema_violation` = markdown + coverage decide (provider-agnostic).

`gate_verdict` is built from `passed` through `_canonical_gate_verdict` (invariant `PASS in token == passed`). `validation_verdict_drift` remains, gains a `resolved` field and `severity="error"` on mismatch.

### §2.3 Reroute ON by default

- **Mechanics (gate r1 #1).** The call `get_config().flag("HAL_SPEC_DEFECT_REROUTE")` at `phase_5_implement.py:7037` is replaced with `get_config().gate_enabled("HAL_SPEC_DEFECT_REROUTE")` (unset = enabled, exactly `"0"` = disabled). Catalog entry: `kind:"gate"`, `default:"1"`, description with `retire-by:2027-01-15 Refs #91` instead of the expired `flip-by`. Any value other than `"0"` (`false`, `off`, empty string) is read as enabled; this is pinned by a test (AC20).
- The preconditions `:7037-7125` (durable budget, `no_progress`, `_MAX_SPEC_DEFECT_REROUTES`) do not change; when in doubt the path falls back to legacy TEST_GAP.
- Explicit `=0`: legacy byte-for-byte + event `spec_defect_reroute_disabled` (`severity="warning"`, `cycle`, `source="explicit"`).
- Real reroute outputs (not `phase_reroute`/`reroute_to`): `StepResult.error_code == "E_SPEC_DEFECT"`, `data.reroute_attempt` (>=1), `data.spec_defect_reason` (non-empty), `data.spec_sha`; routing into phase_45_spec is done by `lib/task_resume.py:58,68` (`REROUTE_TARGET`/`_REROUTE_CODE`), not by the gate. Durable side effect: `scratchpad/resume/spec-defect-reroutes-<run_id>.json` (written by `lib/spec_defect_ledger.py:75-96`) contains `spec_sha`.

### §2.4 AC checklist gate blocks by default

- **Mechanics.** `phase_45_spec.py:3178` and `:3212` switch to `get_config().gate_enabled("HAL_AC_DSL_GATE_ENFORCE")`. Catalog entry: `kind:"gate"`, `default:"1"`, `retire-by:2027-01-15 Refs #91`; the `flip-by` tokens are removed from the description and from code comments (`phase_45_spec.py:3212`, `flags_catalog.py:528`). `HAL_AC_DSL_GATE=0` (-> `env_skip`) is unchanged; `HAL_AC_DSL_GATE_ENFORCE=0` -> never blocks (old warn-only); for specs WITHOUT `### AC-checks` the legacy-branch payload (below, AC21) applies regardless of ENFORCE; for specs with the section, behavior is byte-for-byte legacy.
- **Legacy branch (gate r1 #2).** Without it, ENFORCE-by-default blocks all specs without `### AC-checks`: `ac_dsl.admit` returns REJECT for both "no ACs found" and "AC-checks section missing" (`ac_dsl.py:215-226`); in the repository 0 of 47 specs in `docs/decisions/` have `### AC-checks`. Decision: `ac_dsl.py` gains a public predicate `has_ac_checks_section(spec_text) -> bool` (based on the existing `_AC_CHECKS_HEADER_RE`, `:152`), and `_verify_spec_ac_dsl` checks it BEFORE calling `admit`. No section -> emit `spec_ac_dsl_warn` (`reason="legacy_no_ac_checks"`), return `ok` with `data["spec_ac_dsl_skipped"]="legacy_no_ac_checks"`. It relies on a structural predicate, not on a message substring. Only specs where the section EXISTS but is not accepted (`ACCEPT != result`) are blocked: `_spec_gate_retry`/directed-repair (GH634), `E_SPEC_AC_UNCOMPILABLE`.
- **Infrastructure degrades, does not block.** The spec read (`phase_45_spec.py:3173`, currently outside `try`) moves INSIDE the `try`; `OSError`/`UnicodeDecodeError`/an `admit()` exception -> `spec_ac_dsl_driver_error` (`severity="error"`) + `data["spec_ac_dsl_unverified"]=True`, step `ok`. This changes behavior under `ENFORCE=1` (currently fail-closed, `test_gh634`/`GH517A2 ac9`) — the edits are declared in §5.

### §2.5 Re-poll is removed

- The call to `_frozen_revise_repoll` (`phase_45_spec.py:3977`) is removed: the single REVISE of a qualified reviewer (hard_gate, fresh session — `llm_subprocess.py:1728-1729`, `allowed_tools=["Read"]`) is final. `_frozen_revise_repoll` (`:3895-3941`), `_ship_reachable` (`:3886-3892`) and the event `phase_45_spec_review_repoll` are deleted.
- Why not "re-poll in an independent session": more repeats of the same non-deterministic judge — the same voice by volume.
- **Legacy input.** The org_config keys `spec_frozen_review_repolls`/`spec_review_repolls` > 0 are ignored; `spec_review_repoll_ignored` (`key`, `value`) is emitted once per (process, `run_id`, `key`) pair — a module-level set; there is no dedupe across processes (each phase is a separate `run.py`), which is accepted. `0`/absent — no event. The check is in `_invoke_review_llm` on every call (regardless of verdict); both keys are checked independently of `is_frozen`; the event is emitted for each key with an int value > 0.
- Provider-agnostic; if a fresh session cannot be obtained, `invoke_llm_subprocess` returns `status="error"`, the gate stays REVISE/error, and SHIP is not inferred from the volume.

### §2.6 Fence (#94) and class-I

- New functions and imports (`verdict_verify`, `ac_dsl`) go NO higher than line 6240 of `phase_5_implement.py`: imports are deferred, inside functions; helpers sit next to `_build_validation_prompt`/`_write_validation_doc`. `_resolve_gate_passed` is placed next to `_gate_on_validation` (below 6240).
- `_resolve_git_cwd` is defined above 6240 (`phase_5_implement.py:2063`), but the new code only CALLS it (from `_read_red_texts`); the definition and the fence #94 lines are not edited.
- Class-I (bd#150, gate r3 #4): exactly TWO new read sites, keys `<rel>::<qualname>::read_text#<ordinal by line>` (`class_i_lint.py:201-206`): (1) `workflows/phase_5_implement.py::_write_validation_doc::read_text#0` — the spec, `not-prompt`, note "spec read for AC-id parse; only derived AC ids (never spec bytes) reach next-cycle findings"; (2) `workflows/phase_5_implement.py::_read_red_texts::read_text#0` — RED files, `not-prompt`, note "substring citation check only; no bytes reach a prompt". These are ALL the new keys of `class_i_inventory.json`; the implementer verifies the actual ordinals via `class_i_lint.call_sites`, runs `class_i_lint.py` (rc0) and `test_bd150_class_i_inventory.py`. The r1 key in `_build_validation_prompt` is NOT introduced.

## §3 Acceptance (RED: `engine_py/tests/test_bd91_pregreen_strict_and.py`, `engine_py/tests/test_bd91_spec_review_verdict.py`)

All RED tests import the new symbols (`_resolve_gate_passed`, `_forward_map_coverage`, `has_ac_checks_section`) LATE, inside the test body (§1q): failure at assert time, not at collection.

| AC | What fails before GREEN |
|---|---|
| AC1 | The real chain without a manual `prev.ac_coverage`: a spec AC1..AC3 on disk, `validation_raw` with a Forward Map for AC1, AC2, `Verdict: PASS` + `approve:true` -> `_write_validation_doc` -> `_verify_validation_citations` -> `_gate_on_validation`: not passed, `data.gate_reason=="ac_gap"`, `data.ac_gap_ids==["AC3"]`, head of `data.findings` = `ENGINE AC-COVERAGE GAP (cycle N): AC3`, event `ac_coverage_gap` (`uncovered`, `bullets`) |
| AC2 | `_forward_map_coverage(validation_raw, ac_ids, red_texts)` unit (`red_texts` passed directly): `` `N/A` ``, `` `TBD` ``, `` `none` ``, `` `[GREEN-regression-deferral]` `` and an invented test name = uncovered (`no_test_cited` for reserved words, `citation_not_found` for the invented one); `` `test_a` `` against a text with only `test_ac1_x` = `citation_not_found` (identifier boundary), `` `AC3` `` = not a citation, `` `assert` ``/`` `def` `` = not a citation (shape), `` `none` `` in a RED file docstring does not make an AC covered; Go `` `TestFoo` ``, Swift `` `testFoo` ``, TS description `` `"rejects empty input"` `` and `` `it("rejects empty input")` ``, `` `file.test.ts > rejects empty input` ``, present in the text = covered; bare `- AC1 -> test_one` with `test_one` in the text = covered; `red_texts=None`/`[]` = `unverifiable`/`red_files_unreadable`; Spec Compliance `AC3 → partial: …` (arrow `→`) = uncovered; a name really present in the RED file (including with a `path::` prefix) = covered; duplicate bullets (one `MISSING`) = uncovered; `AC1` does not match `AC10`; a `MISSING` bullet and a bullet with no citation = uncovered; lists/ranges `AC1, AC2` / `AC1–AC3` / `AC1..AC3` are expanded; `AC1a`==`AC1A`; Spec Compliance `partial` = uncovered, while "no missing negatives" is not; `[GREEN-regression-deferral]` does not exempt; all 6 reason codes (`no_test_cited` is reachable for reserved words too); (gate r4 #1) covered when `test_m`/`it_works`/`TestFoo` is present in the text: `` `tests/x.py::TestCls::test_m` ``, `` `mod::tests::it_works` ``, `` `TestCls.test_m` ``, `` `test_m[case1]` ``, `` `test_m()` ``, `` `TestFoo/empty_input` ``; (gate r4 #2) `` - AC5: `MISSING` (unlike AC1) `` does not make AC1 uncovered; `` - AC1: `test_x` (see also AC5) `` does not cover AC5; the Forward Map table row `` | AC1 | `test_x` | `` = covered |
| AC3 | `approve=True`, markdown `Verdict: FAIL` -> `passed=False`, `reason=="markdown_fail"`, `gate_verdict` without `PASS`, LoopRunner re-iterates up to the cap; also protects the citation-demotion case (`helper.py:457-460`) + `approve:true` |
| AC4 | `approve=True`, markdown UNKNOWN -> `markdown_missing` (fail-closed); also UNKNOWN + structured absent |
| AC5 | `approve=False`, markdown PASS -> `structured_veto` (regression shield, already green) |
| AC6 | structured absent / `json_error` / `schema_violation`, markdown PASS, coverage ok -> `md_only` (shield) |
| AC7 | Full parameterized matrix of the 32 cells of §2.2 (4 markdown x 4 structured x 2 coverage; `PARTIAL` -> `markdown_partial` for all 8 cells) over `_resolve_gate_passed` with the expected `(passed, reason)` per rule order, plus the invariant `PASS in canonical == passed`; includes PASS+approve false+uncovered -> `structured_veto` and the category rule: `TEST_GAP` is forced only on `ac_gap`, a validator `SPEC_DEFECT` is preserved |
| AC8 | `_build_validation_prompt` does not contain `GREEN-regression-deferral`/`TDD-pure`; contains the bullet format `- AC<n>:` and `passes-pre-fix` |
| AC9 | Clean environment (flag unset), SPEC_DEFECT from the validator, §2.3 preconditions met -> `StepResult.error_code=="E_SPEC_DEFECT"`, `data.reroute_attempt>=1`, non-empty `data.spec_defect_reason` (and not legacy TEST_GAP); `get_config().gate_enabled("HAL_SPEC_DEFECT_REROUTE")` True without env |
| AC10 | **Production side effect:** the same scenario with no env overrides: on disk `scratchpad/resume/spec-defect-reroutes-<run_id>.json` contains the spec's `spec_sha`. Fixture modeled on `test_bd139_single_reviewer.py:470 _spec_defect_setup` WITHOUT `HAL_SPEC_DEFECT_REROUTE=1`: a non-null `telemetry_ctx.get_current_run()`, a readable `spec_path`, `org_config.scratchpad_dir` |
| AC11 | `HAL_SPEC_DEFECT_REROUTE=0` -> legacy TEST_GAP byte-for-byte + `spec_defect_reroute_disabled` |
| AC12 | A spec with an INVALID but present `### AC-checks`, no env -> `error`/`E_SPEC_AC_UNCOMPILABLE` (block); the fixture sets `org_config['directed_repair_skip']=True` (directed repair is default ON, `lib/directed_repair.py:118-128`, otherwise the test would spawn a repair LLM) |
| AC13 | `admit()` raises an exception, no env -> `status="ok"`, `spec_ac_dsl_unverified`, `spec_ac_dsl_driver_error` |
| AC14 | A spec without `### AC-checks` and `HAL_AC_DSL_GATE=0` -> `ok`/`env_skip` (shield) |
| AC15 | REVISE from the reviewer; a fake backend returns SHIP on subsequent calls -> result REVISE, exactly 1 backend call, no event `phase_45_spec_review_repoll` |
| AC16 | `spec_frozen_review_repolls=2` -> ignored: 1 call, one `spec_review_repoll_ignored`; a repeated call in the same process/`run_id` — no event (dedupe scope = process + run_id + key); a different `run_id` — event again; when `telemetry_ctx.get_current_run() is None` the dedupe key is `(None, key)`, i.e. one event per process and key (not "always emit"); `0` -> no event |
| AC17 | `flags_catalog` per-entry (not a general lint): both entries `kind=="gate"`, `default=="1"`, no `flip-by:` in the description, a `retire-by:` with a date, a reference to #91. The date is compared with a FIXED test constant (`retire-by` == `2027-01-15`), not with "today": the test does not turn red for unrelated PRs on 2027-01-16; expiry is caught by review, not CI (deliberate, gate r2 edge 6) |
| AC18 | A spec with no parseable AC lines through the real write -> citations -> gate chain: exactly ONE event `ac_coverage_unverifiable` (the gate does not emit again when the key is present), markdown+structured decide |
| AC19 | The header `## §3 Acceptance` (without "criteria") yields non-empty ids via `verdict_verify.parse_spec_ac_ids`; an AC table outside the section (in §5) yields no phantom ids; a spec with an early `### Acceptance-test harness` AND a late `## Acceptance Criteria` takes the criteria section (pass 1); a bare `## Acceptance` with a numbered list yields an empty set (no phantoms); `_write_validation_doc` does not duplicate the parser: a sentinel monkeypatch of `verdict_verify.parse_spec_ac_ids` (idiom GH1065 AC10, `test_gh1065...py:481-501`) records the call. Parity with `phase_6_review` is NOT checked (the copy does not change, see AC29) |
| AC20 | **RED** (gate r3 #5; before GREEN `flag()` is true only for exactly `"1"`, `config_provider.py:56-58`): asserted through the call sites: `HAL_SPEC_DEFECT_REROUTE` = `false`/`off`/empty -> `_gate_on_validation` gives `E_SPEC_DEFECT`; `HAL_AC_DSL_GATE_ENFORCE` = `false`/`off`/empty -> `_verify_spec_ac_dsl` blocks; exactly `0` — legacy. The default (unset) is additionally covered by AC9/AC12; ENFORCE half: the fixture sets `org_config['directed_repair_skip']=True` (`lib/directed_repair.py:118-128`) |
| AC21 | Legacy branch: a spec with an AC table, no `### AC-checks`, no env -> `_verify_spec_ac_dsl` returns `ok`, exactly one `spec_ac_dsl_warn` with `reason=="legacy_no_ac_checks"` AND `reasons` containing the string `"AC-checks section not found"` (full payload: `{reason, reasons: ["AC-checks section not found (legacy_no_ac_checks)"], spec_path}`; this keeps `GH517A2 test_ac12:423-428` green without editing the test), `data["spec_ac_dsl_skipped"]=="legacy_no_ac_checks"`, `admit` not called |
| AC22 | **Outcome shield (no KeyError even today), RED only on the observable:** `prev.data` without the `ac_coverage` key (hand-assembled), PASS + `approve:true` -> `_gate_on_validation` does not raise, passes, `data.gate_reason=="and"` (a field on the PASS return, §2.1) and exactly one event `ac_coverage_unverifiable` with `reason=="missing_key"` (the observable exists only after GREEN) |
| AC23 | An unreadable spec/parser exception in `_write_validation_doc` -> `ac_coverage.status=="unverifiable"` + event, no exception; an unreadable spec in `_verify_spec_ac_dsl` -> `ok`, `spec_ac_dsl_unverified` (read inside `try`) |
| AC24 | `validation_verdict_drift` on mismatch contains `resolved` and `severity=="error"` |
| AC25 | `ac_gap` on the terminal path (`cycle == cap`): `StepResult.error_code=="E_VALIDATION_FAILED"`, `data.gate_reason=="ac_gap"`, `data.ac_gap_ids` and the head of `data.findings` present (not only below cap, as in AC1); the reject-log line has `reason_code=="VALIDATION_AC_GAP"` (read from the real reject log on disk, in both AC1 and here) |
| AC26 | Source and resolution of RED files through `_read_red_texts`/the real chain: (a) `red_test_paths` set but all files unreadable -> `ac_coverage.status=="unverifiable"`, `unverifiable_reason=="red_files_unreadable"`, exactly one event, no exception (degradation, not fail); (b) one readable, one not -> checked against the readable one; (c) PRODUCTION SHAPE (gate r3 #1): RELATIVE `red_test_paths` + `org_config['git_cwd']=<tmp repo>` + process working directory elsewhere (`monkeypatch.chdir`) -> the citation is checked (covered / `citation_not_found`), NOT `unverifiable`; (d) worktree case: `org_config` without `git_cwd`, with `current_worktree_path=<tmp repo>`, process cwd elsewhere -> relative entries resolve against `current_worktree_path` (precedence of `_resolve_git_cwd`; `prev.data['git_cwd']` is absent at this site because of the `:6738-6746` whitelist); (e) an absolute entry is used as-is |
| AC27 | The AC1 fixture permits the real `verify_validation_doc` to pass: the Reverse Map is complete over `red_test_paths`, there are no quote lines `> path:line:` (otherwise the verdict is demoted to FAIL/PARTIAL and `gate_reason` will not be `ac_gap`) — checked as a fixture precondition (`verdict=="PASS"` after `_verify_validation_citations`) |
| AC28 | `PARTIAL` from the citation verifier + `approve:true`, coverage ok -> `passed=False`, `reason=="markdown_partial"` through the real chain (one unverifiable quote line in `validation_raw`), `gate_verdict` without `PASS` |
| AC29 | **Pinning the divergence, phase 6 does not change (gate r3 #3):** a spec with `## §3 Acceptance` and an AC table AC1, AC2: `verdict_verify.parse_spec_ac_ids` gives `{AC1, AC2}` (RED before GREEN: currently empty); `phase_6_review._parse_spec_ac_ids` still returns `[]`, and `_verify_ac_checklist(spec, answer without ## AC Checklist)` returns `skip`/`no_spec_acs` (shield: the phase 6 hard gate is unchanged both before and after GREEN); the docstring note about the divergence in `verdict_verify` is present |

**Shields (green before GREEN, not RED):** AC5, AC6, AC14, AC22 (by outcome; RED only on the observable `gate_reason=="and"` and the event), the phase-6 half of AC29. The remaining ACs fail before GREEN: AC1-AC4, AC7-AC13, AC15-AC21, AC23-AC26, AC28, the verdict_verify half of AC29 (AC27 is a fixture precondition).

Anchors: AC1 (real write -> citations -> gate chain on disk), AC10 (durable ledger); AC15 — a real UUT call with a fake backend (the backend is not the UUT).

## §4 Degrade-not-fail

- No AC ids / the parser crashed / the file is unreadable / no `ac_coverage` key -> coverage `unverifiable` with an event, the gate does not fail (AC18, AC22, AC23).
- No structured block or a broken one -> markdown + coverage decide (AC6).
- `admit()` crashed or the spec is unreadable in `_verify_spec_ac_dsl` -> step `ok`, `spec_ac_dsl_unverified` (AC13, AC23).
- A spec without `### AC-checks` -> warn-pass (AC21).
- Unreadable RED files during the citation existence check -> `unverifiable`, not fail (AC26).
- Obsolete re-poll keys -> ignored with an event (AC16).
- No independent reviewer assessment -> REVISE/error stands.
- A telemetry error never changes the verdict (`_emit_safe`).

## §5 Sibling tests (checked by grep over `engine_py/tests`)

**Need a declared edit in the RED phase:**
- `test_gh514p1_frozen_review_repoll.py`, `test_gh541_nonfrozen_review_repoll.py`, `test_gh707_reviewer_repoll_cap.py` — deleted, replaced by AC15/AC16.
- `test_bd141_p4d_role_template_injections.py:747` — the case `"phase_45_spec:4150-repoll"` (`_REVISE`, min calls 2) breaks when the re-poll is removed: the case is deleted or min calls = 1.
- `test_phase_5_gate_structured_verdict.py` — only `test_gate_proceeds_when_markdown_fail_but_structured_approve` breaks (changes to not-pass); AC6/AC7 of this file stay green.
- `test_phase_5_gate_verdict_canonical_gh349.py` — `test_gate_inverse_drift_proceed_canonicalizes_verdict` and `test_gate_unknown_markdown_with_structured_approve_canonicalizes_to_pass` break; `test_gate_terminal_drift_at_cap_...` (PASS + approve false) stays green, as do `test_gate_core_drift_loop_continue`, `test_gate_marker_equivalence_on_drift`, `test_canonical_gate_verdict_helper_invariant_matrix`.
- `test_phase_45_ac_dsl_wiring_GH517A2.py` — `test_ac5` (warn-only with env unset) breaks from the default change; `test_ac8` (admit raises, env unset) does NOT break: the outcome stays `ok` + one `spec_ac_dsl_driver_error` with `error`; `test_ac9` (fail-closed on exception) changes to degradation (§2.4); `test_ac10` (`kind=="flag"`, `:386`, default `"0"`) -> `kind=="gate"`, `"1"`; `test_ac6`/`test_ac7` (env ENFORCE=1 explicitly) stay; `test_ac12` (no AC-checks section, expects exactly one `spec_ac_dsl_warn` with `reasons` containing `"AC-checks section not found"`, `:423-428`) stays green ONLY thanks to the legacy branch of §2.4 WITH `reasons` in the payload (AC21).
- `test_gh634_ac_uncompilable_directed_repair.py` — `test_ac9_enforce_off_regression_ok_and_repair_not_called` (env unset, uncompilable, expects `ok`; `:327`) breaks because of the default change, not because of exception handling. The test sets no env at all (`:327-343`); edit in the RED phase: add `monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "0")`. The rest stay green.

**Run by list; they stay green only if the §2.1 pin "no key = unverifiable" holds (AC22):** direct calls of `_gate_on_validation` with a hand-assembled `prev`: `test_GH1674_injection_missing.py` (including the AC24 PASS branch with `"## Verdict\nPASS\n"`), `test_GH706_validate_cap_directed_reject.py`, `test_phase_5_implement_W13.py`, `test_phase_5_g1_band_aid_9_verify_green.py`. (`test_gh1018_orphan_green_cycle_resolution.py` and `test_gh1626d_orphan_green_recovery.py` do NOT call `_gate_on_validation`; they are moved to "stay green" only as unaffected.)

**Full phase_45 workflow tests** (fixtures without `### AC-checks`: `test_phase_45_spec.py`, `test_E6602155_frozen_spec_ingest.py`, `test_phase_45_spec_EECB919C.py`, `test_gh557_resume_seam_flips.py`): green thanks to the legacy branch; confirmed by a run.

**Calls of `_build_validation_prompt`/`_write_validation_doc` with nonexistent spec files** (`test_phase_5_graphfirst_DA48BEAC.py`, `test_gh705_callsite_stable_prefix.py`, `test_GH897_sentinel_input_hash.py`, `test_phase_5_b4d83b40_red_rubric.py`): green as long as degradation on an unreadable spec holds (AC23).

**`ac_dsl.admit` (`ac_dsl.py:215`), gate r4 #7:** a spec with `## §N Acceptance` + `### AC-checks` moves from REJECT "no ACs found" to real checking; run-confirmation: GH517A2, gh634.

**Consumers of the extended AC-section header (gate r2 #4; `verdict_verify` only):** `verdict_gate.run_gate` / GH517 parity lint (`verify_ac_parity`), the host audit-gate hook; run-confirmations: `test_GH749*` (`_classify_parity`), `test_gh751*`, `test_gh1065*` AC12, plus any `parse_spec_ac_ids` test; for the two-pass search (pass 1 byte-for-byte) they are expected green. The implementer runs them with `--require-clean`; for specs `## §N Acceptance (RED …)` SKIP -> checked state (warn only until `HAL_VERDICT_GATE_LINT_ENFORCE` is set).

**`phase_6_review` is NOT changed (gate r3 #3):** `_parse_spec_ac_ids` (`:3219`; header regex `:3207`), `_verify_ac_checklist` (`:3298`, GH388, `ac_checklist_fail`), `_ac_checklist_ids_directive` (GH1065) and the satisfaction prompt (`:2806-2807`, `:2846-2847`) stay as they are; run-confirmations without edits: `test_gh388_ac_checklist_satisfaction.py`, `test_gh1065_satisfaction_ac_ids.py` (`--require-clean`).

**Stay green:** `test_gh1018_orphan_green_cycle_resolution.py`, `test_gh1626d_orphan_green_recovery.py`, `test_bd139_single_reviewer.py` (flag explicitly `=1`; it is also the model for the AC10 fixture), `test_phase_5_step4_validation_schema.py`, `test_gh925_terminal_fail_sentinel_invalidation.py`, `test_gh963_validation_execution_failure.py`. The implementer confirms by a `--require-clean` run; there is no pin of the deferral prompt text in tests (grep is clean).

## §6 Scope

**Touched:** `workflows/phase_5_implement.py` (`_build_validation_prompt` text only, `_write_validation_doc`, `_gate_on_validation`, new `_forward_map_coverage`, `_read_red_texts`, `_resolve_gate_passed`; everything below 6240); `workflows/phase_45_spec.py` (`_verify_spec_ac_dsl`, `_invoke_review_llm`, repoll removal, `gate_enabled`); `ac_dsl.py` (only `has_ac_checks_section`); `verdict_verify.py` (only the two-pass header search in `parse_spec_ac_ids` and a docstring about the divergence from phase 6, plus updating the "DUPLICATED … parity source" comments at `verdict_verify.py:18` and `:39` (no longer true for pass 2)); `flags_catalog.py` (two entries); `conformance/class_i_inventory.json`; the tests from §5; `error_codes`/event registry, if events are registered centrally. `_invoke_validation_llm` is NOT touched.

**NOT touched:** `phase_6_review.py` entirely (including `_parse_spec_ac_ids` `:3219`, `_verify_ac_checklist` `:3298`, prompt `:2806`/`:2846-2847`; decision r4); `_resolve_git_cwd` (`phase_5_implement.py:2063`) — only called. **NOT touched** (parallel lots): #94 engine-owned paths (`phase_5_implement.py` lines <6240 and `lib/util/engine_owned.py`); #92 per-cycle artifact invalidation; #192/#206 class-M; #211 release version; `phases/`, `llm_subprocess.py`, `lib/task_resume.py`, `lib/spec_defect_ledger.py`.

## §7 Open questions

None (closed in §2.0).

## §8 Changelog r1 -> r2 (response to gate r1: REJECTED)

| Finding | Sev | Fix in r2 |
|---|---|---|
| 1 `flag()` == "1" only | BLOCKER | `gate_enabled` at 3 call sites, both catalog entries `kind:"gate"`; AC9/AC17/AC20 rewritten (§2.3, §2.4) |
| 2 ENFORCE blocks all specs without AC-checks | BLOCKER | legacy branch `has_ac_checks_section` before `admit`, AC21 (§2.4) |
| 3 `spec_ac_ids` lost in the whitelist | MAJOR | ids computed in `_write_validation_doc`, class-I key moved, AC1 through the real chain (§2.1, §2.6) |
| 4 §2.2 not total | MAJOR | ordered rules, UNKNOWN rows, 24 cells, category owner; AC7 (§2.2) |
| 5 Token drift | MAJOR | `E_SPEC_DEFECT`, `reroute_attempt`, `spec-defect-reroutes-<run_id>.json`; `reject_reason` defined as `gate_reason` + `findings` + payload (§2.1, §2.3) |
| 6 §5 without bd141_p4d:747 | MAJOR | added; §5 descriptions corrected (including gh634 AC9) |
| 7 No `ac_coverage` key | MAJOR | = unverifiable, AC22 (§2.1) |
| 8 Second AC-id parser | MAJOR | `verdict_verify.parse_spec_ac_ids`, header extension in one place, AC19 |
| 9 §5 descriptions inaccurate | MINOR | corrected (§5) |
| 10 No ACs for degrade branches | MINOR | AC23, AC24; spec read inside `try` (`phase_45_spec.py:3173`) |
| 11 Fence #94 | MINOR | deferred imports, placement below 6240, removal of `flip-by` comments (§2.4, §2.6) |
| 12 False "catalog lint" | MINOR | claim removed, AC17 per-entry |
| 13 Deferred imports / dedupe | MINOR | §3 preamble; AC16 defines the scope (process + run_id + key) |
| Open Q 1-3 | - | D1-D3 in §2.0 |

## §8b Changelog r2 -> r3 (response to gate r2: REJECTED)

| Finding | Sev | Fix in r3 |
|---|---|---|
| 1 PARTIAL outside the matrix | MAJOR | rule 2b, fail-closed `markdown_partial`; matrix of 32 cells; behavior change declared; AC7, AC28 (§2.2) |
| 2 Legacy payload breaks GH517A2 `test_ac12` | MAJOR | `reasons` with "AC-checks section not found" added to the payload; assert in AC21; §5 clarified |
| 3 Citation = any backtick token | MAJOR | the token must occur verbatim in `red_test_paths`; `citation_not_found`; degradation `red_files_unreadable`; AC2, AC26 (§2.1) |
| 4 Side effects of the header extension | MINOR | two-pass search, table mode without numbered fallback in pass 2, `phase_6_review` changes unconditionally + parity in AC19, `verdict_gate` consumer in §5 |
| 5 AC20/AC22 green today | MINOR | declared as shields; AC20 through call sites; AC22 with the observable `gate_reason=="and"`; shields list in §3 |
| 6 §5 descriptions | MINOR | gh634 ac9: add `setenv`; GH517A2 `ac8` does not break; gh1018/gh1626d do not call the gate |
| 7 Reject-log literal | MINOR | `VALIDATION_AC_GAP`, assert in AC1/AC25 |
| 8 ac_gap on the terminal path | MINOR | both returns carry the fields; AC25 |
| 9 Wording | MINOR | AC19 sentinel monkeypatch; AC16 dedupe `(None, key)`; AC17 fixed date (not a CI bomb); AC27 AC1 fixture precondition |

## §8c Changelog r3 -> r4 (response to gate r3: REJECTED; last round, cap 4)

| Finding | Sev | Fix in r4 |
|---|---|---|
| 1 RED file path not defined | MAJOR | relative entries resolve through `_resolve_git_cwd(ctx, prev)` (call only), absolute as-is, process cwd not used; unreadable -> `unverifiable`; helper `_read_red_texts`; AC26 (c)-(e) (§2.1) |
| 2 Citation grammar Python-only | MAJOR | candidate = any backtick span + bare `test_*`/`Test*`/`test[A-Z]*`; normalisation of `path::`, ` > `, `it("…")`; AC2: Go/Swift/TS/legacy bullet |
| 3 Extending `phase_6_review` changes the hard gate | MAJOR | DECISION: the phase 6 copy is NOT changed; the extension only in `verdict_verify`; the parity part of AC19 removed, AC29 pinning added (the divergence is intentional); §2.1/§5/§6 updated, fence bullet |
| 4 Signature and class-I | MINOR | `_forward_map_coverage(validation_raw, ac_ids, red_texts)` (pure); two new class-I keys: `_write_validation_doc::read_text#0`, `_read_red_texts::read_text#0` (complete list) (§2.1, §2.6) |
| 5 AC20 is RED, `gate_reason` on PASS | MINOR | AC20 moved to RED, shields updated; `gate_reason` on three returns (PASS, below cap, terminal), AC22 relies on it |
| 6 Matching precision / dedupe / arrows / id normalisation | MINOR | whole token with identifier boundaries, reserved words, minimal shape, AC id is not a citation; `ac_coverage_unverifiable` exactly once per call (the gate emits only when the key is absent, `missing_key`); Spec Compliance status skips `: - -> → — – =`; id normalisation for parity removed together with the parity part of AC19 |
| Wording | - | fence bullet: `_resolve_git_cwd` is defined above 6240 (`:2063`), only called, not edited |

## §8d Changelog r4 -> r5 (response to gate r4: APPROVED_WITH_FIXES; text-only edits, no round 5)

| Finding | Sev | Fix in r5 |
|---|---|---|
| 1 Citation normalisation falsely rejects common forms | REQUIRED | §2.1 "Citation candidates": 4-step normalisation (last segment of `::`/`.`, strip `[…]`/`()`, Go subtests); AC2 +6 cases |
| 2 Forward Map id scope not bounded | REQUIRED | §2.1: ids only from the bullet head / first cell of a table row; AC2 +3 cases |
| 3 AC26(d) and "cwd not used" | MINOR | AC26(d) rewritten (`current_worktree_path`, whitelist `:6738-6746`); wording on cwd as the `:2067` fallback |
| 4 Line drift | MINOR | `:3219` (+regex `:3207`), `:2846-2847`, `:3895-3941`/`:3886-3892`, `:587-588`, `:6866-6875`; all verified against the code |
| 5 Class-I note | MINOR | §2.6 item 1: note about derived AC ids in next-cycle findings |
| 6 Directed repair isolation | MINOR | AC12 and AC20: `directed_repair_skip=True` |
| 7 §2.4 wording | MINOR | ENFORCE=0 / legacy branch clarified; `ac_dsl.admit` in the §5 consumers |
| 8 Known fail-open | MINOR | §2.1: "Known limitation" paragraph (+ empty `red_test_paths` -> `unverifiable`) |
| 9 Scope/comment completeness | MINOR | §6: `_read_red_texts`, comments `verdict_verify.py:18`/`:39`; IGNORECASE in the second pass |
| 10 Legacy re-poll event | MINOR | §2.5: check on every call, both keys independent of `is_frozen` |
