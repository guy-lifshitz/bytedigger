# engine_py E_* error-code catalogue

## E_BAD

- `E_BAD_CTX` — run.py: ctx payload malformed or missing required fields

## E_BASELINE

- `E_BASELINE_DELTA` — phase_5_implement: §1r baseline-delta gate — new test fails outside baseline+ledger (GH561)

## E_BOUNDARY

- `E_BOUNDARY_SCAN_FAILED` — phase_5/6: authored-boundary scan tooling itself failed to run
- `E_BOUNDARY_SUPPRESSION` — phase_5/6: RED/GREEN suppresses or disables the boundary scan

## E_CANARY

- `E_CANARY_BAD_CONFIG` — phase_5_integration_canary: canary config file malformed or missing keys
- `E_CANARY_EVENTS_MISSING` — phase_5_integration_canary: expected canary events.jsonl not found
- `E_CANARY_NO_MATCH` — phase_5_integration_canary: expected event pattern not found in canary run

## E_CLARIFY

- `E_CLARIFY_BLOCKED` — phase_3_clarify: subagent returned BLOCKED verdict, cannot proceed
- `E_CLARIFY_NEEDS_CONTEXT` — phase_3_clarify: subagent requested more context to proceed
- `E_CLARIFY_NO_MARKER` — phase_3_clarify: subagent output missing required completion marker

## E_CLEANUP

- `E_CLEANUP_REPORT_WRITE_FAILED` — phase_8_post_deploy: failed writing the post-ship cleanup report

## E_CONFIG

- `E_CONFIG_INVALID` — dbos_setup: engine or phase config value is invalid/out of range

## E_CORPUS

- `E_CORPUS_DIVERGENCE` — lib/corpus_parity: current test corpus diverges from the baseline snapshot beyond the declared removal allowance (GH1338)
- `E_CORPUS_PARITY` — phase_5_implement: baseline-delta verdict BLOCKED — stale base or test-corpus divergence (GH1338)
- `E_CORPUS_UNKNOWN` — lib/corpus_parity: corpus-parity precondition could not be evaluated — baseline or current corpus data unavailable (GH1338)

## E_CTX

- `E_CTX_MALFORMED` — dbos_setup: ctx JSON payload could not be parsed or is shape-invalid
- `E_CTX_MISSING_FIELDS` — phase_05_inject: injected ctx is missing required fields

## E_DECORR

- `E_DECORR_INVOKE_FAILED` — phase_6_review: decorrelated-verifier invocation failed to run
- `E_DECORR_VERIFY_SUSPECT` — phase_6_review: decorrelated verifier flagged the fix as suspect

## E_DEVOPS

- `E_DEVOPS_SCAN_BLOCKED` — phase_5_devops_scan: devops/security scan blocked the build
- `E_DEVOPS_SCAN_UNAVAILABLE` — phase_5_devops_scan: scan tooling not available on this host

## E_DIFF

- `E_DIFF_CMD_MISSING` — phase_5/6 integrity: git diff command binary not found
- `E_DIFF_EXIT` — phase_5/6 integrity: git diff process exited non-zero
- `E_DIFF_TIMEOUT` — phase_5/6 integrity: git diff process timed out

## E_DURABLE

- `E_DURABLE_BACKEND_UNKNOWN` — lib/phase_sentinel: HAL_ENGINE_DURABLE_BACKEND names an unknown durable backend (legal: dbos | native)

## E_EMPTY

- `E_EMPTY_RUN` — lib/corpus_parity: --results log had a parseable summary reporting zero tests — a zero-test run is not proof of comparability (GH1338 §10 MAJOR-3)

## E_EXPLORE

- `E_EXPLORE_BLOCKED` — phase_2_explore: subagent returned BLOCKED verdict, cannot proceed
- `E_EXPLORE_NEEDS_CONTEXT` — phase_2_explore: subagent requested more context to proceed
- `E_EXPLORE_NO_MARKER` — phase_2_explore: subagent output missing required completion marker

## E_FILE

- `E_FILE_NOT_FOUND` — run.py: referenced ctx/spec file does not exist on disk

## E_FIX

- `E_FIX_BLOCKED` — phase_6_review: fix-cycle subagent returned BLOCKED verdict
- `E_FIX_COMMIT_FAILED` — phase_6_review: git commit of the fix diff failed
- `E_FIX_INTEGRITY_ASSERTION_GAMING` — phase_6_fix_integrity: fix diff appears to game assertions rather than fix code
- `E_FIX_INTEGRITY_NO_MARKER` — phase_6_fix_integrity: fix diff missing required completion marker
- `E_FIX_LLM_NO_PROGRESS` — phase_6_review: fix-cycle LLM subprocess made no forward progress
- `E_FIX_NO_MARKER` — phase_6_review: fix subagent output missing required completion marker
- `E_FIX_TEST_COMMIT_FAILED` — phase_6_review: git commit of the fix test diff failed
- `E_FIX_UNCOMMITTED_CHANGES` — phase_6_fix_integrity: fix cycle left dirty uncommitted changes in the tree
- `E_FIX_WRITE_FAILED` — phase_6_review: writing the fix artifact/doc to disk failed

## E_FLAG

- `E_FLAG_UNREGISTERED` — phase_5_implement: GREEN diff reads a HAL_* flag with no flags_catalog entry (GH529)
- `E_FLAG_UNREGISTERED_CAP2` — phase_5_implement: flag-registration gate retry cap exhausted (GH529)

## E_FRESHNESS

- `E_FRESHNESS_UNKNOWN` — lib/corpus_parity: base freshness could not be determined — merge-base/ancestry check failed or is unavailable (GH1338)

## E_GIT

- `E_GIT_BAD_STATE` — phase_5_implement: git working tree in an unexpected/bad state
- `E_GIT_COMMIT_FAILED` — phase_5_implement: git commit invocation failed
- `E_GIT_CWD_AMBIENT` — phase_5/6: git_cwd resolved from the ambient process CWD — refusing to run a mutating git op
- `E_GIT_LOCKED` — phase_5/6: git index.lock held, repository busy
- `E_GIT_OS_ERROR` — phase_5/6: git subprocess raised an OS-level error
- `E_GIT_TIMEOUT` — phase_5/6: git subprocess timed out

## E_GREEN

- `E_GREEN_BLOCKED` — phase_5_implement: GREEN subagent returned BLOCKED verdict
- `E_GREEN_COMMIT_FAILED` — phase_5_implement: git commit of GREEN code failed
- `E_GREEN_CWD_GONE` — phase_5_implement: GREEN subprocess working directory disappeared mid-run
- `E_GREEN_LINT_BAD_JSON` — phase_5_implement: GREEN lint tool emitted malformed JSON output
- `E_GREEN_LINT_FAIL_CAP2` — phase_5_implement: GREEN lint retry cap exhausted, still failing
- `E_GREEN_LINT_PATH_ESCAPE` — phase_5_implement: GREEN lint target path escaped the allowed scope
- `E_GREEN_LINT_RETRY` — phase_5_implement: GREEN lint failed, retry cycle triggered
- `E_GREEN_LINT_SEMGREP_MISSING` — phase_5_implement: semgrep binary unavailable for GREEN lint step
- `E_GREEN_LINT_TIMEOUT` — phase_5_implement: GREEN lint subprocess timed out
- `E_GREEN_NOT_PASSING` — phase_5_implement: GREEN test suite not fully passing
- `E_GREEN_NOT_REPRODUCIBLE` — phase_5_implement: GREEN result not reproducible on re-run
- `E_GREEN_NO_MARKER` — phase_5_implement: GREEN subagent output missing required completion marker
- `E_GREEN_TEST_RUNNER_MISSING` — phase_5_implement: test runner binary unavailable for GREEN verification
- `E_GREEN_TEST_TIMEOUT` — phase_5_implement: GREEN test suite subprocess timed out
- `E_GREEN_TYPECHECK_FAIL_CAP2` — phase_5_implement: GREEN typecheck retry cap exhausted, still failing
- `E_GREEN_TYPECHECK_MYPY_MISSING` — phase_5_implement: mypy binary unavailable for GREEN typecheck step
- `E_GREEN_TYPECHECK_PATH_ESCAPE` — phase_5_implement: GREEN typecheck target path escaped the allowed scope
- `E_GREEN_TYPECHECK_RETRY` — phase_5_implement: GREEN typecheck failed, retry cycle triggered
- `E_GREEN_TYPECHECK_TIMEOUT` — phase_5_implement: GREEN typecheck subprocess timed out
- `E_GREEN_WATCHDOG` — phase_5_implement: GREEN watchdog detected stalled subprocess
- `E_GREEN_WATCHDOG_ESCALATE` — phase_5_implement: GREEN watchdog escalated after repeated stalls

## E_HARD

- `E_HARD_GATE_MODEL_DOWNGRADE` — llm_subprocess: hard-gated model downgraded below the configured floor

## E_INSUFFICIENT

- `E_INSUFFICIENT_FANOUT` — phase_6_review: reviewer fan-out produced fewer results than required

## E_INTEGRITY

- `E_INTEGRITY_ASSERTION_GAMING` — phase_5_integrity: RED/GREEN diff appears to game assertions
- `E_INTEGRITY_NO_MARKER` — phase_5_integrity: integrity check output missing required marker

## E_INVALID

- `E_INVALID_PREV_DATA` — phase_5_implement: forwarded previous-step data is malformed/invalid

## E_LLM

- `E_LLM_API_BAD_RESPONSE` — anthropic_api backend: API response body was not the expected shape
- `E_LLM_API_DEPS_MISSING` — pydantic_openai backend: pydantic_ai not installed (pip extra agentic-pydantic)
- `E_LLM_API_HTTP` — anthropic_api backend: API returned a non-2xx HTTP status
- `E_LLM_API_KEY_MISSING` — anthropic_api backend: required API key not present in environment
- `E_LLM_API_NETWORK` — anthropic_api backend: network-level failure calling the API
- `E_LLM_API_TIMEOUT` — pydantic_openai backend: agent run exceeded wall-clock timeout
- `E_LLM_BACKEND_NO_MANIFEST` — llm_subprocess: backend produced no manifest file for the run
- `E_LLM_BACKEND_UNKNOWN` — llm_subprocess: requested LLM backend name not registered
- `E_LLM_CMD_MISSING` — llm_subprocess/dbos_setup: LLM CLI command binary not found or ctx malformed
- `E_LLM_EXIT` — llm_subprocess/dbos_setup: LLM subprocess exited with non-zero status
- `E_LLM_MANIFEST_INVALID_SOURCE` — llm_subprocess: manifest source field references an invalid/unknown source
- `E_LLM_MANIFEST_MALFORMED` — llm_subprocess: manifest file could not be parsed as expected JSON
- `E_LLM_MANIFEST_MISSING` — llm_subprocess: expected manifest file for the LLM run was not found
- `E_LLM_MANIFEST_MISSING_AT_CONSUMER` — phase_5/6: downstream consumer could not find the expected LLM manifest
- `E_LLM_NO_PROGRESS` — llm_subprocess/dbos_setup: LLM subprocess made no forward progress before timeout
- `E_LLM_NO_RESULT_EVENT` — llm_subprocess/dbos_setup: no result event emitted by the LLM subprocess
- `E_LLM_RESULT_ERROR` — llm_subprocess/dbos_setup: LLM subprocess result event reported an error
- `E_LLM_RESULT_MALFORMED` — llm_subprocess: LLM subprocess result event payload was malformed
- `E_LLM_RUN_ID_MISSING` — llm_subprocess: expected run-id field missing from the LLM invocation
- `E_LLM_SPEND_LIMIT` — llm_subprocess: account spend/usage limit hit — environmental, pausable/resumable after human action
- `E_LLM_TIMEOUT` — llm_subprocess/dbos_setup/anthropic_api: LLM call exceeded its configured timeout
- `E_LLM_WATCHDOG_UNSUPPORTED` — llm_subprocess: watchdog capability requested but unsupported by this backend

## E_MANIFEST

- `E_MANIFEST_MISSING` — phase_05_inject: required manifest file for injection not found
- `E_MANIFEST_NO_GIT` — pydantic_openai backend: workspace root is not a git repository (git-diff manifest impossible)

## E_MEMORY

- `E_MEMORY_DB_EMPTY` — phase_05_inject: memory.db resolved but contains no rows
- `E_MEMORY_DB_NOT_FOUND` — phase_05_inject: memory.db file could not be located
- `E_MEMORY_DB_PATH_UNRESOLVED` — phase_05_inject: memory.db path could not be resolved from config
- `E_MEMORY_DB_SCHEMA` — phase_05_inject: memory.db schema does not match expected shape
- `E_MEMORY_DB_UNREADABLE` — phase_05_inject: memory.db exists but could not be opened/read

## E_MISSING

- `E_MISSING_FIX_BOUNDARY` — phase_5/6: fix diff missing required boundary marker
- `E_MISSING_PREV_DATA` — phase pipeline: required previous-step data was not forwarded to this step
- `E_MISSING_RED_BOUNDARY` — phase_5_implement: RED diff missing required boundary marker
- `E_MISSING_REVIEW_DOC_PATH` — anti_hallucination helper/semantic_verifier: review doc path not supplied
- `E_MISSING_SCRATCHPAD` — phase_6_review: expected scratchpad artifact for review was not found

## E_NO

- `E_NO_ROLE_FILES` — phase_6_review: no reviewer role files found to drive the fan-out

## E_NOT

- `E_NOT_REGISTERED` — run.py: requested phase/workflow name not registered in the runner

## E_ORCHESTRATOR

- `E_ORCHESTRATOR_CHECKLIST_MALFORMED` — phase_05_inject: orchestrator checklist file could not be parsed
- `E_ORCHESTRATOR_CHECKLIST_MISSING` — phase_05_inject: orchestrator checklist file not found

## E_POST

- `E_POST_FIX_PYTEST_FAILED` — phase_6_review: post-fix pytest run reported real test failures
- `E_POST_FIX_PYTEST_INFRA` — phase_6_review: post-fix pytest run failed for infra reasons, not test content
- `E_POST_FIX_TYPECHECK_PATH_ESCAPE` — phase_6_review: post-fix typecheck target path escaped allowed scope
- `E_POST_FIX_TYPECHECK_REGRESSION` — phase_6_review: post-fix typecheck introduced a new regression

## E_PROTECTED

- `E_PROTECTED_OVER_BUDGET` — memory_compact: protected anchor block alone exceeds the configured memory_md_bytes_max budget

## E_PYTEST

- `E_PYTEST_MISSING` — phase_5_implement: pytest binary not available on this host

## E_RED

- `E_RED_1Q_EXEC_IMPORT` — phase_5_implement: RED test file uses spec_from_file_location/exec_module without a '# 1q: allow' pragma (§1q non-collectable RED risk)
- `E_RED_COLLECT_FAILED` — phase_5_implement: RED test file failed pytest collection
- `E_RED_COLLECT_PROBE` — phase_5_implement: RED test file(s) failed the §1q pytest --co collect-probe (non-collectable RED, D1CF5FDF hang class)
- `E_RED_CRASHED` — phase_5_implement: RED test run crashed (signal / zero tests executed / test-executable error) without reporting any assertion failure
- `E_RED_EMPTY_FILES` — phase_5_implement: RED test files were empty/contained no tests
- `E_RED_FIXTURE_SCHEMA_DRIFT` — phase_5_implement: RED fixture CREATE TABLE columns are not a subset of the spec's Data-Model Ground Truth reference DDL (GH891 fixture-fiction class)
- `E_RED_FIXTURE_SCHEMA_NOT_COMPARABLE` — phase_5_implement: the spec's Data-Model Ground Truth reference DDL for this table could not be derived in full (unbalanced parens, an unrecognised element, or sqlite3 itself rejects the body) — comparison against the fixture is not performed, and that is reported rather than silently skipped (GH1350)
- `E_RED_FIXTURE_SCHEMA_UNPARSEABLE` — phase_5_implement: a RED fixture's CREATE TABLE column list could not be parsed (unbalanced parens) — distinct from E_RED_FIXTURE_SCHEMA_DRIFT since nothing was compared (GH1350)
- `E_RED_LINT_BAD_JSON` — phase_5_implement: RED lint tool emitted malformed JSON output
- `E_RED_LINT_F1` — phase_5_implement: RED lint flagged an F1-class stub-passability violation
- `E_RED_LINT_FAIL_CAP2` — phase_5_implement: RED lint preflight retry cap exhausted, still failing
- `E_RED_LINT_PATH_ESCAPE` — phase_5_implement: RED lint target path escaped the allowed scope
- `E_RED_LINT_SEMGREP_MISSING` — phase_5_implement: semgrep binary unavailable for RED lint step
- `E_RED_LINT_TARGET_UNREADABLE` — phase_5_implement: RED lint target could not be read, so 'no violations' is not established for it (GH1373 rev3)
- `E_RED_LINT_TIMEOUT` — phase_5_implement: RED lint subprocess timed out
- `E_RED_MASS_DELETION` — phase_5_implement: RED diff mass-deleted a pre-existing file beyond threshold (GH282 guard)
- `E_RED_NOT_EXECUTABLE` — phase_5_implement: RED test file could not be executed at all
- `E_RED_NOT_FAILING` — phase_5_implement: RED tests unexpectedly passed instead of failing
- `E_RED_NO_MARKER` — phase_5_implement: RED subagent output missing required completion marker
- `E_RED_NO_PATHS` — phase_5_implement: no RED test file paths were supplied to verify
- `E_RED_ONE_SIDED_PREDICATE` — phase_5_implement: RED test contains a one-sided negative code-exit predicate with no live positive control in the same test block (Rule P, GH1373)
- `E_RED_PYTEST_TIMEOUT` — phase_5_implement: RED pytest subprocess timed out
- `E_RED_SCOPE_VIOLATION` — phase_5_implement: RED diff touched files outside declared scope
- `E_RED_STUB_PASSABLE` — phase_5_implement: RED test mocks its own UUT, making it vacuously passable
- `E_RED_SUITE_UNSAFE` — phase_5_implement: RED suite considered unsafe to execute as-is
- `E_RED_TESTS_TAMPERED` — phase_5/6: RED test file content was tampered with after freeze
- `E_RED_TEST_RUNNER_TIMEOUT` — phase_5_implement: RED test runner subprocess timed out
- `E_RED_WORKTREE_DIRTY` — phase_5_implement: uncommitted production changes at RED-gate/validation entry — tree must be clean before RED certification
- `E_RED_WROTE_OUTSIDE_WORKTREE` — phase_5_implement: RED subagent wrote a test-shaped file into the MAIN checkout instead of the build worktree (GH1179 write-boundary gate)

## E_REMOTE

- `E_REMOTE_UNREACHABLE` — lib/corpus_parity: could not reach the remote to verify a remote-tracking base_ref (git ls-remote failed/timed out) — fail-closed, never treated as fresh (GH1338 §10 MAJOR-1)

## E_REQUIRED

- `E_REQUIRED_CTX_MISSING` — engine.py: a required ctx field was not present for this phase

## E_RESTART

- `E_RESTART_CAP` — restart_governor: restart attempt cap for this workflow was exceeded
- `E_RESTART_SHORT_CIRCUIT` — restart_governor: restart short-circuited due to repeated identical failure

## E_RESULTS

- `E_RESULTS_UNPARSEABLE` — lib/corpus_parity: --results log carried no recognizable run summary (empty/truncated/collector crash) — 'nothing to check' is never certified as a match (GH1338 §10 MAJOR-3)

## E_RETRY

- `E_RETRY_FORWARDED_DATA_MISSING` — phase_5_implement: data expected to be forwarded across a retry cycle is missing

## E_REVIEW

- `E_REVIEW_DEGRADED` — phase_6_review: review ran in a degraded mode (reduced fan-out/evaluators)
- `E_REVIEW_FAILED` — phase_45_spec/phase_6_review: review subagent returned a FAILED verdict
- `E_REVIEW_FIX_FEED_DIVERGENCE` — phase_6_review: fix feed does not cover the review's aggregated findings
- `E_REVIEW_UNPARSEABLE` — phase_45_spec/phase_45_spec_lite: review verdict output could not be parsed
- `E_REVIEW_WRITE_FAILED` — phase_6_review: writing the review artifact to disk failed

## E_RUNNER

- `E_RUNNER` — run.py: the phase runner itself raised an unhandled error

## E_SATISFACTION

- `E_SATISFACTION_AC_CHECKLIST` — phase_6_review: satisfaction AC-checklist cross-check failed
- `E_SATISFACTION_BELOW_THRESHOLD` — phase_6_review: satisfaction score fell below the configured threshold
- `E_SATISFACTION_WRITE_FAILED` — phase_6_review: writing the satisfaction verdict artifact to disk failed

## E_SCHEMA

- `E_SCHEMA_SMOKE_DRYRUN_ERROR` — phase_5_integrity: schema-smoke dry-run artifact failed for a reason unrelated to schema mismatch (category FIXTURE_SCHEMA_MISMATCH n/a)
- `E_SCHEMA_SMOKE_MISMATCH` — phase_5_integrity: schema-smoke dry-run detected a real schema mismatch (category FIXTURE_SCHEMA_MISMATCH)
- `E_SCHEMA_SMOKE_UNAVAILABLE` — phase_5_integrity: schema-smoke target snapshot unavailable (missing schema, restore failure, or timeout)

## E_SEC

- `E_SEC_FRAGMENT_MISSING` — phase_5_implement: security-codegen prompt fragment file not found
- `E_SEC_LINT` — phase_5_implement: security lint step reported findings
- `E_SEC_LINT_PATH_ESCAPE` — phase_5_implement: security lint target path escaped the allowed scope
- `E_SEC_LINT_SELF_EXEMPT` — phase_5_implement: security-lint pragma added in the fix diff — self-exemption rejected
- `E_SEC_LINT_TIMEOUT` — phase_5_implement: security lint subprocess timed out
- `E_SEC_LINT_UNAVAILABLE` — phase_5_implement: security lint tooling not available on this host

## E_SECURITY

- `E_SECURITY_RULES_MISSING` — phase_05_inject: security rules fragment file not found for injection

## E_SEMANTIC

- `E_SEMANTIC_VERIFY_READ_FAILED` — semantic_verifier: reading the review doc for semantic verification failed
- `E_SEMANTIC_VERIFY_WRITE_FAILED` — semantic_verifier: writing the semantic verification result failed

## E_SHIP

- `E_SHIP_DIRTY_TREE` — phase_8_post_deploy: working tree was dirty at ship time
- `E_SHIP_FULL_SUITE_REGRESSION` — phase_8_post_deploy: full-suite verify showed a regression vs baseline
- `E_SHIP_PHANTOM_DELETION` — phase_8_post_deploy: PR diff would delete files the branch never touched
- `E_SHIP_PR_FAILED` — phase_8_post_deploy: creating the ship pull request failed
- `E_SHIP_PUSH_FAILED` — phase_8_post_deploy: git push at ship time failed
- `E_SHIP_REBASE_CONFLICT` — phase_8_post_deploy: rebase onto origin/main conflicted at ship time
- `E_SHIP_UNALLOWLISTED_RED` — phase_8_post_deploy: ship-time suite showed a red not covered by the allowlist

## E_SMOKE

- `E_SMOKE_FAILED` — phase_6_smoke: smoke test run reported a failure
- `E_SMOKE_TIMEOUT` — phase_6_smoke: smoke test subprocess timed out

## E_SPEC

- `E_SPEC_AC_UNCOMPILABLE` — phase_45_spec: GH517 A2 AC-DSL admission (ac_dsl.admit) rejected the spec under enforce
- `E_SPEC_CITATION_FATAL` — phase_45_spec: spec citation lint failed fatally after retries exhausted
- `E_SPEC_CITATION_MALFORMED` — phase_45_spec: spec citation reference was malformed
- `E_SPEC_CITE_LINT_FAIL` — phase_45_spec: spec cite-lint step reported findings
- `E_SPEC_CITE_LINT_TIMEOUT` — phase_45_spec: spec cite-lint subprocess timed out
- `E_SPEC_CITE_LINT_UNAVAILABLE` — phase_45_spec: spec cite-lint driver missing in bootstrap — fail-closed (GH594)
- `E_SPEC_CITE_LINT_UNEXPECTED_RC` — phase_45_spec: spec cite-lint subprocess exited with an unexpected return code
- `E_SPEC_CITE_PRELINT_RETRY` — phase_45_spec: cite pre-lint enforce triggered a bounded writer re-prompt (GH681)
- `E_SPEC_COVERAGE` — phase_45_spec: spec AC/op coverage check found gaps
- `E_SPEC_COVERAGE_FATAL` — phase_45_spec: spec coverage check failed fatally after retries exhausted
- `E_SPEC_DEFECT` — phase_5_implement: Opus validator flagged the spec itself as unsatisfiable/self-contradictory; reroute to phase_45_spec (GH767)
- `E_SPEC_DEFECT_BUDGET` — phase_5_implement: spec-defect reroute budget exhausted or no forward progress; human decision required (GH767)
- `E_SPEC_FILE_MISSING` — phase_45_spec: spec file expected on disk was not found
- `E_SPEC_HELPER_EXTRACTION` — phase_45_spec: spec helper-extraction (§1aa) lint found unguarded inline-math/inline-emit
- `E_SPEC_HELPER_EXTRACTION_FATAL` — phase_45_spec: spec helper-extraction check failed fatally after retries exhausted
- `E_SPEC_INCOMPLETE` — phase_45_spec: spec document missing required sections/content
- `E_SPEC_INCOMPLETE_FATAL` — phase_45_spec: spec incompleteness persisted after retries exhausted
- `E_SPEC_LINT_BATCH` — phase_45_spec: batched spec-lint (token-consistency/presence-triad/format-conversion) found findings under enforce
- `E_SPEC_LINT_BATCH_FATAL` — phase_45_spec: batched spec-lint findings persisted after retries exhausted
- `E_SPEC_LINT_DRIVER_ERROR` — phase_45_spec: spec lint driver itself raised an error (fail-closed)
- `E_SPEC_LINT_FAIL` — dbos_setup/phase_45_spec: spec lint step reported findings
- `E_SPEC_LINT_TIMEOUT` — phase_45_spec: spec lint subprocess timed out
- `E_SPEC_LINT_UNAVAILABLE` — phase_45_spec: spec lint driver missing in bootstrap — fail-closed (GH594)
- `E_SPEC_LINT_UNEXPECTED_RC` — phase_45_spec: spec lint subprocess exited with an unexpected return code
- `E_SPEC_PREFLIGHT_BATCH` — phase_45_spec: spec pre-flight gate-batch reported findings (GH747)
- `E_SPEC_REENTRY` — phase_45_spec: spec re-entry AC (§1ab/§1ac) check found a gap
- `E_SPEC_REENTRY_FATAL` — phase_45_spec: spec re-entry AC check failed fatally after retries exhausted
- `E_SPEC_REVISE_EXHAUSTED` — phase_45_spec: spec revise-retry budget was exhausted without approval
- `E_SPEC_SCOPE_INVERSE` — phase_45_spec: spec scope-inverse (files-not-in-scope) check found a gap
- `E_SPEC_SCOPE_INVERSE_FATAL` — phase_45_spec: spec scope-inverse check failed fatally after retries exhausted
- `E_SPEC_UPSTREAM_REVISE` — phase_45_spec: an upstream phase requested a spec revise cycle

## E_STALE

- `E_STALE_BASE` — lib/corpus_parity: base_ref is not an ancestor of head_ref (git merge-base --is-ancestor failed) — corpus-parity precondition fails closed (GH1338)
- `E_STALE_REMOTE_REF` — lib/corpus_parity: local remote-tracking base_ref sha does not match the TRUE remote (git ls-remote) — locally-fresh ancestry is insufficient when the tracking ref itself was never fetched (GH1338 §10 MAJOR-1)

## E_STEP

- `E_STEP_TIMEOUT` — contracts/dbos_setup: a DBOS workflow step exceeded its configured timeout

## E_SYNTHESIZER

- `E_SYNTHESIZER_BLOCKED` — phase_7_synthesize: synthesizer subagent returned BLOCKED verdict
- `E_SYNTHESIZER_NEEDS_CONTEXT` — phase_7_synthesize: synthesizer subagent requested more context
- `E_SYNTHESIZER_NO_MARKER` — phase_7_synthesize: synthesizer output missing required completion marker

## E_TEST

- `E_TEST_RUNNER_MISSING` — phase_5_implement: configured test runner binary was not found

## E_VALIDATION

- `E_VALIDATION_EXECUTION_FAILURE` — phase pipeline: validator failed to execute (zero tool calls / inputs not read) after retry budget — infra failure, NOT a test gap
- `E_VALIDATION_EXEC_RETRY` — phase pipeline: validator self-reported non-execution — bounded fresh-subprocess retry
- `E_VALIDATION_FAILED` — phase pipeline: structured verdict validation failed for this step's output
- `E_VALIDATION_RETRY` — phase pipeline/dbos_setup: structured verdict validation triggered a retry

## E_VERDICT

- `E_VERDICT_GATE_LINT` — phase_5/audit-gate hook: deterministic verdict-gate lint contradicts an APPROVED verdict (enforce mode)

## E_VERIFY

- `E_VERIFY_READ_FAILED` — anti_hallucination helper: reading the artifact to verify failed
- `E_VERIFY_WRITE_FAILED` — anti_hallucination helper: writing the verification result failed

## E_WORKTREE

- `E_WORKTREE_HEAD_MOVED` — phase_5_implement: worktree HEAD moved during phase_5 (external merge/reset) — frozen pre-red SHA no longer reachable or not an ancestor of HEAD (agreement 6604CC4B)

