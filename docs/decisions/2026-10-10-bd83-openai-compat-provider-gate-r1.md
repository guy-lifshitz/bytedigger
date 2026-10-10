# bd#83 gate r1 - spec + RED audit (OpenAI-compatible provider, phase_6 review without file tools)

Anchor: not hand-written. The sha256 anchor of the spec and RED files is stamped by the anchor tool
after this file is written.

Audited (read-only):
- Spec: docs/decisions/2026-10-10-bd83-openai-compat-provider.md (DRAFT r1)
- RED: engine_py/tests/test_bd83_openai_compat.py (66 tests, all failing today),
  engine_py/tests/test_bd83_openai_compat_live_ollama.py (marker `live_ollama`)
- Sibling: engine_py/tests/test_bd83_openai_compat_guards.py (9 guards, passing today by design)
- Context: llm_subprocess.py (`_resolve_backend`, `_cost_events`, `_billing_mode_of`, `_dispatch_backend`,
  `_effective_model`, `_tool_restriction_refusal`, `register_backend`, `reset_backends`,
  `_REFERENCE_BACKEND_INSTALL_HINTS`), run.py:49-69, lib/reference_backends/anthropic_api.py,
  pydantic_openai.py, workflows/phase_6_review.py (`_build_review_prompt`, `_invoke_review_llm`,
  `_aggregate_review_findings`, `_write_review_artifact`, fix and satisfaction prompts),
  lib/plugins/review_schema/canonical.py, error_codes.py, conformance/bd_l3.py, flags_catalog.py,
  core_manifest.json, core_boundary.py, scripts/flag_owner_lint.py, scripts/commit_subject_lint.py,
  engine_py/pyproject.toml, engine_py/tests/conftest.py, and the sibling tests named below.

## Step 1 - spec-internal consistency (literal-token drift)

- Env names `HAL_OPENAI_BASE_URL`, `HAL_OPENAI_API_KEY`, `HAL_OPENAI_MODEL_<ALIAS>` are spelled the same in
  3.1, 3.4 and the AC table; the RED uses the same literals. No drift.
- Codes `E_LLM_BASE_URL_INVALID`, `E_LLM_BASE_URL_MISSING`, `E_LLM_API_HTTP`, `E_LLM_TIMEOUT`,
  `E_LLM_API_NETWORK`, `E_LLM_API_BAD_RESPONSE`, `E_LLM_BACKEND_UNKNOWN` match between 3.1 and AC1/AC8/AC9
  and the RED. The two `E_LLM_BASE_URL_*` codes are new and are not registered anywhere (finding 2).
- Contradiction inside 3.1.3 (finding 8): "Anything else (`file://`, `ftp://`, no host, **blank**) ->
  `E_LLM_BASE_URL_INVALID`" and, in the next sentence, "Unset or **blank** env -> `E_LLM_BASE_URL_MISSING`".
  (The contradiction sits inside 3.1.3; 3.3 does not mention the URL.) AC8 only names "unset" for MISSING.
  The RED pins blank (`"   "`) -> MISSING (`test_ac8_base_url_missing_at_call_time[blank]`).
  **Binding resolution for GREEN:** a value that is empty after `strip()` is MISSING; INVALID applies only
  to a non-blank value that fails the scheme/host check. This matches 3.1.4 (register() treats blank as
  unset) and the RED. The word "blank" in the INVALID parenthetical is a drafting error to be removed in r2.
- 3.3 names the replaced line as `OUTPUT - Use the Write tool ...`; the code uses an em dash
  (`phase_6_review.py:1016`, `"OUTPUT — Use the Write tool ..."`). Cosmetic, but GREEN must match on the
  code literal.
- 3.3 says `_resolve_backend(None, os.environ, role="judge")`; the dispatcher resolves with
  `config_provider.env_mapping()` (`llm_subprocess.py:1972-1974`) and phase_6 already does the same at
  `phase_6_review.py:2269`. Two env sources for one decision (finding 11).
- 3.3 says "the two file-writing instructions". The built prompt has three (finding 1).

## Step 1.5 - rule-overlap simulation

Registration (3.1.4), branch order: env blank/unset -> False; else register(overwrite=True) -> True.

| AC | First matching branch | Outcome | Matches |
|---|---|---|---|
| AC1 (None, "", "   ") | blank/unset | False, not in `_KNOWN_BACKENDS`, dispatch -> E_LLM_BACKEND_UNKNOWN (`llm_subprocess.py:1997-2006`) | yes |
| AC2 | set | True twice (idempotent), caps contain no_tools | yes |
| AC8 invalid (`file:///x`) | set (registration only checks blank) | True; call-time check -> INVALID | yes |

Call-time URL check (3.1.3 with the Step 1 resolution): strip -> empty -> MISSING; parse -> scheme not in
{http, https} or empty host -> INVALID; else I/O.

| AC8 input | First matching branch | Outcome |
|---|---|---|
| unset / `"   "` | empty after strip | MISSING, no socket |
| `file:///x`, `ftp://h` | scheme | INVALID |
| `http://`, `https://` | empty host | INVALID |
| `localhost:11434` | urlparse gives scheme `localhost` | INVALID |
| `/v1` | empty scheme | INVALID |

Error classifier (3.1.6). Python class nesting: `HTTPError` < `URLError` < `OSError`; `TimeoutError` <
`OSError`; `http.client.RemoteDisconnected` < `ConnectionResetError` < `OSError`. A correct order is
HTTPError -> TimeoutError -> URLError -> parse errors -> catch-all.

| AC9 case | First matching branch (correct order) | Outcome | Matches |
|---|---|---|---|
| 500 | HTTPError | E_LLM_API_HTTP, recoverable | yes |
| bound, not listening | URLError(ConnectionRefusedError), raised from `h.request` | E_LLM_API_NETWORK | yes |
| blocked server | TimeoutError raised raw from `getresponse()` (urllib wraps only `h.request`) | E_LLM_TIMEOUT | yes |
| `not json` / `{}` / `[1,2,3]` / content `""`, `null`, `42` / `{"choices":[{}]}` / `"choices":"x"` | parse/shape | E_LLM_API_BAD_RESPONSE, not recoverable | yes |

If HTTPError is caught after URLError (a plausible slip), the 500 case returns NETWORK; the RED catches it.
Overlaps the spec does not decide (finding 14): a connect timeout arrives as `URLError(reason=TimeoutError)`
(NETWORK or TIMEOUT?), and `RemoteDisconnected` / `ConnectionResetError` from `getresponse()` is not a
URLError, so "any other exception" makes it a non-recoverable BAD_RESPONSE.

`_judge_can_write` (3.3), branch order: resolve the judge backend -> caps lookup -> `no_tools` and not
`tool_allowlist` -> False; anything else -> True.

| AC10 case | Outcome | Matches |
|---|---|---|
| `{"no_tools"}` | False | yes |
| claude-subprocess (caps include tool_allowlist) | True | yes |
| default resolution (conftest pins HAL_RUNNER_BACKEND=claude-subprocess) | True | yes |
| `{"no_tools","tool_allowlist"}` | True | yes |
| unknown name | caps empty -> True | yes |
| registered with `()` | True | yes |
| real openai-compat registration | False | yes |

Side effect outside the AC table: `anthropic-api` registers `{"no_tools", "billing:metered", "effort:*"}`
without `tool_allowlist` (`anthropic_api.py:310-312`), so an existing anthropic-api judge becomes "cannot
write" and its review prompt changes (finding 13).

Usage mapping (3.1.5): every `test_ac6_usage_copied_only_for_valid_counts` row (absent, bool/negative,
str/float, non-dict, partial, zero, None) gives the expected output under "non-bool int >= 0, copy per
field, omit `usage` when empty". Consistent.

## Step 2 - section 3 (change) vs section 5 (AC) cross-check

Change -> AC (every terminal or error branch in section 3 has an AC):
- 3.1.3 INVALID / MISSING -> AC8. 3.1.4 False / True -> AC1 / AC2 / AC13. 3.1.5 success data -> AC6.
  3.1.6 HTTP / timeout / network / bad response -> AC9. 3.2 -> AC7. 3.3 -> AC10-AC12. 3.4 -> AC14.
- Not covered: 3.1.7 `gate_label` echo (no RED; finding 20). 3.1.6 "effective timeout = min(timeout_sec,
  positive idle_timeout_sec)" is covered by `test_ac9_idle_timeout_shortens_the_deadline` even though no
  AC names it.

AC -> producing change:
- **AC11 has no complete producing path (MAJOR, finding 1).** AC11 asserts the no-tools prompt contains
  neither `Use the Write tool` nor the role-composite.md write instruction, and the RED enforces
  `"Use the Write tool"`, `"write your findings"` and `"write the aggregated review into"` all absent.
  3.3 replaces only `_role_line` (`phase_6_review.py:770-774`) and the OUTPUT line (`:1015-1020`). The
  prompt also contains `SINGLE_REVIEW_FRAMING_TEMPLATE` (`lib/plugins/review_schema/canonical.py:39-49`,
  formatted in at `phase_6_review.py:868-875`):
  `"Use the Write tool to write your findings to ONE file:\n    {abs_reviews_dir}/role-composite.md\n"`.
  A GREEN that follows 3.3 word for word fails `test_ac11_no_tools_judge_prompt_has_no_write_instructions`
  and `test_ac12_no_tools_judge_reply_is_written_as_the_review` (`"Use the Write tool" not in sent`).
  `PER_ROLE_SCHEMA_TEMPLATE` also says "Required last line of the role file", which assumes a written file.
- The tests that enforce "no API key in the result, the logs or the event log"
  (`test_api_key_never_appears_in_result_or_logs`, 3 cases) map to no AC and no section 3 sentence
  (finding 9).

## Step 3 - RED adequacy

- Collect time: module-level imports (`llm_subprocess._BACKEND_CAPABILITIES`, `_BACKEND_MANIFEST_SOURCE`,
  `register_backend`, `reset_backends`, `phase_6_review`, `EventLog`, `telemetry_ctx`) all exist today. The
  new module is imported lazily in `_module()`. Preflight confirms 66 collected and 66 failing, at call time.
- Stub-passability: no test mocks the unit under test. HTTP goes to a real `ThreadingHTTPServer` on
  127.0.0.1:0, and refused/slow are staged deterministically (bound but not listening socket; Event-blocked
  handler). The one monkeypatch of the unit under test (`_judge_can_write` forced True in the AC11 golden)
  needs the attribute to exist and sits next to the no-tools test, which kills an always-True stub.
  AC1 (False) and AC2 (True) kill each other's constant stubs, and AC7 `walled` kills a `_cost_events`
  that forwards `wall_ms` only for openai-compat.
- Victim coupling per AC: every AC1-AC15 test fails today on the absent module, the absent
  `_judge_can_write`, the unchanged prompt, the missing pytest marker or the missing `wall_ms` forwarding.
  AC12's stdout fallback (`_write_review_artifact` -> `_resolve_review_content` -> `_derive_fallback_verdict`)
  already works today. Its only victim assertion is the prompt (`"Use the Write tool" not in sent`) plus
  the registration. That is enough for AC12 as written.
- Weak spots (MINOR): the AC11 golden is computed after the change, so a GREEN that edits shared prompt
  text in both branches still passes (finding 19). AC13 checks that the backend is absent, not that the
  registry equals the pre-change snapshot as AC13 words it. That is enough for the kill switch.
- Wording assertions: `"final answer"`, `"do not write files"`, `"complete aggregated review"`
  (lowercased) all occur in 3.3's paraphrase "reply with the complete aggregated review as your final
  answer; do not write files". So they are specified, but not as a quoted literal (finding 10).
- Live test placement (MAJOR, finding 7).

## Step 4 - reachability

| Side effect | Point (prod) | Host | Test path |
|---|---|---|---|
| Backend registered | new `openai_compat.register()` -> `register_backend` (`llm_subprocess.py:2792`) | `run.py` guarded block (new, next to `:49-69`) | AC2, AC13 (`importlib.reload(run)`) |
| HTTP call + data | new backend impl | `_dispatch_backend` -> `_BACKENDS[name]` | AC2-AC6, AC8, AC9 through `invoke_llm_subprocess` |
| `wall_ms` in event | `_cost_events` (`llm_subprocess.py:1505-1540`) | `_observe_cost` (`:1543`) called from `_dispatch_backend` | AC7, AC15 via `telemetry_ctx.set_current_run` + EventLog |
| Prompt swap | `_build_review_prompt` (`phase_6_review.py:745`) | the same function | AC11, AC12 |
| Review doc from stdout | `_write_review_artifact` stdout branch (`:2222-2368`) | `_aggregate_and_write_review_artifact` (`:5979`) | AC12 calls aggregate + write separately (same composition as `:5981-5987`) |

AC12 reachability, verified: `_invoke_review_llm` passes `role="judge"` (`:1116`), so
`HAL_RUNNER_BACKEND_JUDGE=openai-compat` is honoured. It passes `allowed_tools=[..., "Write"]`, but
`_capability_enforcement` returns `no-tools` for a `no_tools` backend, so `_tool_restriction_refusal`
returns None (`llm_subprocess.py:1110-1146`). `hard_gate` is False, so neither the floor nor an effort
refusal fires. The composite file is missing, so `aggregated_content=None` (`:1600-1607`). `doc_path` was
unlinked before the call (`:1095`), so `_resolve_review_content` returns stdout. The structured HIGH
finding makes the verdict FAIL. No `E_MISSING_PREV_DATA` (raw_response is forwarded through `forwarded`).
Reachable.

Sibling surfaces that change with this lot (this is where it breaks):

- `error_codes` drift gate: the two new `E_LLM_BASE_URL_*` literals in a production `.py` are harvested by
  `error_codes.harvest_codes`. `error_codes.check()` / `main(["--check"])` are asserted clean by
  test_bd166_one_sided_predicate.py:552, test_bd10_l3_authorship.py:1085 and :2308,
  test_gh514_spend_limit_classify.py:174, test_bd107_effort_config_fail_closed.py:356,
  test_gh1591_fix_gate_boundary.py:744 and test_bd8_l1_oracle.py:1288. Both ERROR_CODES.md must equal
  `render_markdown()` (test_bd166:546-551, test_bd8_l1_oracle.py:1302). None of error_codes.py or the two
  ERROR_CODES.md files is in the spec's paths (finding 2).
- test_bd71_agent_sdk_observations.py::test_ac7 (`:203-233`) globs `lib/reference_backends/*.py`. A file
  without both `"observed_tools"` and `"observed_model"` literals must be listed in
  `conformance/bd_l3.py::SILENT_BACKENDS` (`:78-80`). openai_compat.py writes `observed_model` (3.1.5) and
  never `observed_tools`, so it is "silent" and undeclared, and the test fails (finding 3).
- test_gh898_backend_pip_hint.py:29-37 pins `_REFERENCE_BACKEND_INSTALL_HINTS` keys to exactly four. 4
  says "edit `_REFERENCE_BACKEND_INSTALL_HINTS` entry", which breaks it (finding 4).
- Tests that enumerate backends: test_4C03CCED_ship1b:126, test_gh1082:132 and test_register_backend_A60F1FE3:372
  compare the registry after `reset_backends()`. openai-compat is removed by reset, so they are unaffected,
  and the kill switch keeps `run` import unchanged with the env unset. No conflict.
- `wall_ms` vs existing `llm_cost_observed` payload tests: no test asserts the exact key set of that
  payload (test_bd167 asserts per field; the exact-key tests found are for other events). The addition is
  conditional, and no existing backend sets `data["wall_ms"]` (grep: no producer under bytedigger_engine).
  The guards file pins it for claude-subprocess and anthropic-api. No conflict.

Repo lints checked:
- flags_catalog / scripts/flag_owner_lint.py: entries of kind `str` without flip-by/retire-by/kill-by
  tokens and without owner/provenance are not rollout entries, so the lint ignores them. The pipeline's
  `E_FLAG_UNREGISTERED` green lint (`flags_catalog.py:26-34`) flags literal
  `os.environ.get("HAL_OPENAI_BASE_URL")` reads that have no FLAGS entry. An f-string alias read is not
  matched. Finding 16.
- core_manifest.json: run.py, llm_subprocess.py and workflows/phase_6_review.py are core, and `env_prefixes:
  ["HAL_"]` forbids literal HAL_ env reads there (core_boundary.py:257-300). openai_compat.py is not core.
  `_judge_can_write` passes a mapping, which is OK. GREEN must keep every `HAL_OPENAI_*` literal inside
  openai_compat.py.
- Cyrillic: none in the spec, RED or sibling files. commit_subject_lint checks subjects at commit time
  only.
- CHANGELOG duplicate-block lint (test_bd212_changelog_helper.py:160): the entry must go into the existing
  `### Added`/`### Changed` block under `[Unreleased]`, not a second one (finding 17).
- test_gh499 SHARED/ residue: scans only helper.py and semantic_verifier.py. core_manifest `string_refs`
  forbids `SHARED/` in core files. Nothing in this lot needs it. No conflict.
- install_forms scan (tests/test_install_forms_registry.py) covers llm_subprocess.py. A stdlib backend
  needs no `pip install` string. No conflict unless a hint is added (finding 4).

## Adversarial edges (not covered by the AC table)

1. Redirect with credentials: the server answers 302 `Location: http://other-host/...`. urllib's default
   opener re-issues a POST 301/302/303 as GET and copies every header set through `Request(headers=...)`,
   `Authorization` included, to any host. Redirect schemes accepted by urllib include `ftp`, which
   bypasses the 3.1.3 allow-list.
2. Connect timeout to a black-holed address arrives as `URLError(reason=TimeoutError)`.
3. Server drops the connection after the request (model loading, crash): `RemoteDisconnected` /
   `ConnectionResetError` raised raw from `getresponse()`.
4. Slow-drip body: each `recv` lands inside the socket timeout, so the total wall time is unbounded by
   `timeout_sec`. No response-size cap either.
5. Base URL with userinfo (`http://user:pass@host/v1`) or a query (`...?x=1`): the URL must not be echoed
   into errors, and appending `/chat/completions` after a query string builds a wrong URL.
6. `http://:8080/v1`: netloc is non-empty but `hostname` is None. The host check must use `hostname`, not
   `netloc`. IPv6 `http://[::1]:11434/v1` must be valid.
7. Key drift between registration and call: a key set after `register()` gives data
   `billing_mode=metered` but no `billing:metered` capability. A key removed after registration gives
   capability metered, and `_billing_mode_of` reports `metered` for a keyless call.
8. Process-wide proxies: the production `urlopen` honours `HTTP(S)_PROXY`. A local server without
   `no_proxy` gets the request, and the bearer key, through the proxy.
9. Hard gate on the judge role with an alias remap (`HAL_OPENAI_MODEL_OPUS=<local>`): floor and
   attestation read the requested alias (finding 5).
10. Existing anthropic-api judge: prompt flips to the no-tools form (finding 13).
11. A no-tools judge gets only paths ("SPEC (read this file)", "Read SPEC, RED, and GREEN files by path"),
    so it reviews without reading the code. The same is already true for an anthropic-api judge.
12. 4xx semantics: 404 unknown model and 401 bad key are `E_LLM_API_HTTP` recoverable, so retries cannot
    help.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: FAIL
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS

## Findings

1. MAJOR - Section 3.3 cannot produce AC11 (Step 2). The no-tools prompt still carries
   `SINGLE_REVIEW_FRAMING_TEMPLATE`'s "Use the Write tool to write your findings to ONE file: .../role-composite.md"
   (canonical.py:39-49, used at phase_6_review.py:868-875). 3.3 replaces only `_role_line` and the
   OUTPUT line. A spec-literal GREEN fails the AC11 no-tools test and AC12. r2 must name the framing
   template (and whether canonical.py gets a no-tools variant or phase_6_review swaps the block). It must
   also say what replaces the per-role schema's "role file" wording, and add canonical.py to paths if it
   is edited.
2. MAJOR - New error codes are unregistered and out of scope. `E_LLM_BASE_URL_INVALID` /
   `E_LLM_BASE_URL_MISSING` break the error_codes drift gate and the ERROR_CODES.md byte-equality tests
   (sites listed in Step 4). Add `engine_py/bytedigger_engine/error_codes.py`,
   `engine_py/ERROR_CODES.md` and `engine_py/bytedigger_engine/ERROR_CODES.md` (regenerated by
   `--markdown`) to 4 and to the front-matter paths.
3. MAJOR - test_bd71::test_ac7 breaks. openai_compat.py writes `observed_model` but not `observed_tools`,
   so it must be declared in `conformance/bd_l3.py::SILENT_BACKENDS`, which is not in scope. Alternatively
   decide that it writes `observed_tools` (for a text-only backend, `[]` is a truthful observation) and
   say so. Either way, r2 must decide and list the file.
4. MAJOR - `_REFERENCE_BACKEND_INSTALL_HINTS` entry conflicts with test_gh898:29-37 (exactly four keys).
   It is also semantically wrong: `_unknown_backend_error_message` says "deps are not importable ... Fix:
   <hint>", but openai-compat is absent because the env is unset. Drop the entry from 4. If an
   env-unset hint is wanted, specify a separate message and scope the gh898 change.
5. MAJOR - Hard-gate floor and attestation overclaim. 3.1.1 remaps models through
   `HAL_OPENAI_MODEL_<ALIAS>`, but the backend registers no `effective_model` hook. `_dispatch_backend` then
   floor-checks and attests the requested alias (`llm_subprocess.py:1650-1690`, `_assert_hard_gate_opus(
   _build_claude_argv(effective))`) while a different model answers. The sibling `pydantic-openai`
   registers `effective_model=_effective_deployment` for exactly this (pydantic_openai.py:636, :809). r2
   must register the hook (alias-resolved id) and state the consequence: hard gates on a non-family id
   are refused by the floor. Add an AC + RED: judge hard gate with an alias -> `effective_model_remapped`
   event and floor decision on the remapped id.
6. MAJOR - Redirects defeat the URL allow-list and leak the key (edge 1). The spec relies on plain
   `urlopen` (the anthropic-api pattern, a fixed host). With an operator-supplied base URL, a 3xx sends
   `Authorization: Bearer` to the redirect target, including another host, and allows an `ftp:`
   redirect. r2 must specify "redirects are not followed; 3xx -> E_LLM_API_HTTP" (an opener with a
   no-redirect handler) or at least `add_unredirected_header` for Authorization plus a same-origin rule.
   Add RED: a 302 to a second local server; assert the second server sees no request with an
   `authorization` header and the result is an error.
7. MAJOR - The live test is listed in `red_tests`. Its outcome depends on a local server and a pulled
   `gemma4:26b` (it skips in CI, and the preflight "1 fail" reflects one machine). After GREEN, the
   lot's own `addopts = -m "not live_ollama"` deselects it, so a scoped run reports 0 tests (pytest exit 5).
   It cannot serve as a deterministic RED/GREEN signal. Move it to `sibling_tests` (or a dedicated
   non-gating key). AC14's config half is already covered hermetically by
   `test_ac14_live_ollama_marker_is_declared_and_deselected_by_default`.
8. MINOR - 3.1.3 blank contradiction. Resolved above (blank -> MISSING, binding). Remove "blank" from the
   INVALID parenthetical and add "blank at call time -> MISSING" to AC8.
9. MINOR - The key-leak tests have no AC. Add an AC: the error text, data, logs and events never contain
   the key, the response body, request headers or base-URL userinfo. Note that `HTTPError.reason` is
   server-controlled text.
10. MINOR - Quote the replacement sentence literally in 3.3 (the RED pins three lowercase substrings). Say
    whether the OUTPUT line's "end with `VERDICT: <PASS|PARTIAL|FAIL>`" and "begin with `## Aggregated
    Findings`" directives survive. They are not load-bearing (the stdout fallback normalises the header
    and takes the verdict from the structured block), but the reply contract must be explicit.
11. MINOR - `_judge_can_write` should resolve with `config_provider.env_mapping()`, the same source as the
    dispatcher (`llm_subprocess.py:1973`) and phase_6_review.py:2269. Otherwise a config provider that
    differs from `os.environ` can make the prompt and the dispatch disagree.
12. MINOR - Scope of the other phase_6 prompts. The fix prompt (`:2562`, plus `:2457-2459` Edit/Write) and
    the satisfaction prompts (`:2925`, `:2997`) also say "Use the Write tool", but their calls pass no
    `role=` (default worker), so `_judge_can_write` does not govern them. That is consistent with the RED.
    The satisfaction path already falls back to `raw_response` (`_resolve_satisfaction_source`, `:2701`),
    and a fix worker cannot work without tools. State this in section 6 so the scope is a decision, not an
    omission.
13. MINOR - anthropic-api judges change behaviour (no_tools without tool_allowlist). This is probably the
    right outcome, but "the default path is untouched" does not cover it. State it and add one AC10 case
    for anthropic-api.
14. MINOR - Error map gaps (Step 1.5): `URLError(reason=TimeoutError)` should be E_LLM_TIMEOUT.
    `RemoteDisconnected` / `ConnectionResetError` / `http.client.HTTPException` should be
    E_LLM_API_NETWORK (recoverable), not the catch-all BAD_RESPONSE. Consider 4xx (except 408/429) as
    `recoverable=False`.
15. MINOR - Timeout is per socket operation, not a total deadline. There is no body-size cap (edge 4).
    Advisory: state the limit or bound the read.
16. MINOR - flags_catalog: make the entries mandatory, not "if the catalog lists backend env vars":
    `HAL_OPENAI_BASE_URL`, `HAL_OPENAI_API_KEY` (kind `str`, module `lib/reference_backends/openai_compat.py`,
    no rollout tokens, so flag_owner_lint stays OK). Say how the dynamic `HAL_OPENAI_MODEL_<ALIAS>` family is
    documented. Drop the unexplained "weight/service file per hook".
17. MINOR - CHANGELOG: append to the existing `[Unreleased]` `###` block (duplicate-block lint,
    test_bd212:160).
18. MINOR - Registration-time vs call-time key drift (edge 7). Either read the key for the capability at
    call time too, or document that registration freezes the billing capability.
19. MINOR - The AC11 golden is built after the change. Consider pinning a hash of the default prompt for
    fixed inputs, taken on the base commit.
20. MINOR - 3.1.7 `gate_label` echo has no RED.
21. MINOR - Advisory: a no-tools judge reviews blind (edge 11). Consider an event (for example
    `review_judge_no_tools`) so a PASS from a judge that saw only paths is visible. Out of scope for this lot.
22. MINOR - Advisory: document proxy behaviour for local base URLs (edge 8).

Summary: the RED is hermetic, non-vacuous, fails at assert time and reaches the real chokepoint. The
registry kill switch and the `wall_ms` addition are safe for existing tests. The lot is rejected for five
reasons: section 3.3 does not produce AC11 (a third Write instruction lives in the framing template);
three sibling breakages are not scoped (error_codes registry + both catalogues, bd_l3 SILENT_BACKENDS,
the gh898 hint-key pin); an alias remap without an `effective_model` hook lets a hard gate pass the floor
on a model that did not answer; redirects bypass the scheme allow-list and forward the bearer key; and
an environment-dependent live test is listed as a RED test.

VERDICT: REJECTED
