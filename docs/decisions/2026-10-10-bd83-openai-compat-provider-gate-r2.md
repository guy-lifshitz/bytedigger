# bd#83 gate r2 - spec + RED audit (OpenAI-compatible provider, phase_6 review without file tools)

Anchor: not hand-written. The anchor tool stamps the sha256 of the spec and RED files below this line
after this file is written.

Audited (read-only):
- Spec: docs/decisions/2026-10-10-bd83-openai-compat-provider.md (DRAFT r2)
- RED: engine_py/tests/test_bd83_openai_compat.py (93 tests, all failing today)
- Siblings: engine_py/tests/test_bd83_openai_compat_guards.py (10 guards, passing today by design),
  engine_py/tests/test_bd83_openai_compat_live_ollama.py (marker `live_ollama`, pinned)
- Previous gate: docs/decisions/2026-10-10-bd83-openai-compat-provider-gate-r1.md (REJECTED)
- Context checked against the code: llm_subprocess.py (`_resolve_backend` :658, `_effective_model` :1268,
  `_billing_mode_of` :1462, `_cost_events` :1505, `_dispatch_backend` step 0 :1650-1690, R3.3 check
  :1341-1380, `register_backend` :2792 (`effective_model=` kwarg), `reset_backends` :2853),
  config_provider.py (`env_mapping` :396, BD_/BYTEDIGGER_ alias fallback), run.py:49-69,
  pydantic_openai.py:565/:809, phase_6_review.py:745-1040, review_schema/canonical.py,
  error_codes.py (`harvest_codes`, `check`, `render_markdown`), both ERROR_CODES.md,
  conformance/class_i_lint.py + class_i_inventory.json, flags_catalog.py, engine_py/pyproject.toml,
  engine_py/tests/conftest.py, .github/workflows/ci.yml, test_bd71_agent_sdk_observations.py:203-233,
  test_bd89_p3b1_single_reviewer_only.py:321-378, test_bd89_p3b1b_ii_aggregation_helper.py:297,
  test_bd150_class_i_inventory.py:609-621.

## r1 findings - resolution check (against the code, not the spec's claims)

| r1 | Sev | Status | Evidence |
|---|---|---|---|
| 1 AC11 framing template | MAJOR | RESOLVED in text, new conflict | 3.3 now names all three instructions (`_role_line` :770-774, OUTPUT :1016 with the em dash, `SINGLE_REVIEW_FRAMING_TEMPLATE` canonical.py:39-49 used at :868-875). grep confirms these are the only three write instructions in the review prompt. RED `_WRITE_MARKERS` covers all three. But the canonical.py edit collides with a sha256 pin (new finding N2). The r1 note on `PER_ROLE_SCHEMA_TEMPLATE` "Required last line of the role file" is not addressed (N9). |
| 2 error codes | MAJOR | RESOLVED | `error_codes.py` and both `ERROR_CODES.md` are in paths and section 4. `test_ac19_new_error_codes_are_registered_and_documented` checks the registry, the byte equality of both files with `render_markdown()` and `check(package_dir)` clean. The existing codes (`E_LLM_API_HTTP` :103, `_NETWORK` :105, `_BAD_RESPONSE` :101, `E_LLM_TIMEOUT` :121, `E_LLM_BACKEND_UNKNOWN` :108) are already registered. |
| 3 bd71 observed_tools | MAJOR | RESOLVED | 3.1.9 decides `observed_tools: []`, and `test_ac19_success_data_has_empty_observed_tools` pins it. test_bd71:220 matches the double-quoted literals `'"observed_tools"'` and `'"observed_model"'` in the source, so GREEN must write both keys with double quotes (N10). `[]` is not adjudicated as an escape by R3.5/R3.6. |
| 4 install hint | MAJOR | RESOLVED | Section 4 says explicitly that no `_REFERENCE_BACKEND_INSTALL_HINTS` entry is added. |
| 5 effective_model | MAJOR | RESOLVED, two gaps | 3.1.8 registers the hook. `register_backend(..., effective_model=)` exists (:2799), `_dispatch_backend` reads it at step 0 and emits `effective_model_remapped` (:1674-1681). The RED pins the hook's return values and the remap event through the chokepoint. Gaps: the hard-gate consequence the r1 finding asked for is still not stated (N6), and the hook reading the env at call time is not tested (N5). |
| 6 redirects | MAJOR | RESOLVED, with a new sibling break | 3.1.7 adds the no-redirect opener, and the AC17 RED parametrises 301/302/307. The opener's `.open(` call is an unclassified class-I site (new finding N1). The second server's observation channel only records POST (N4). |
| 7 live test in red_tests | MAJOR | RESOLVED | Moved to `sibling_tests` and `red_pins`. AC14's config half stays hermetic (`test_ac14_live_ollama_marker_is_declared_and_deselected_by_default`). |
| 8 blank contradiction | MINOR | RESOLVED | 3.1.3: blank -> MISSING, non-blank bad scheme/host -> INVALID. Consistent with AC8 and RED. |
| 9 key-leak AC | MINOR | RESOLVED | AC18 last sentence; `test_ac18_api_key_never_appears_in_result_or_logs` (3 cases) plus the AC17 redirect assertion. |
| 10 literal sentence | MINOR | RESOLVED, assertion weak | 3.3 quotes the sentence. RED pins it exactly. The "VERDICT / ## Aggregated Findings kept" assertions are vacuous (N7). |
| 11 env source | MINOR | RESOLVED | 3.1.1 and 3.3 both use `config_provider.env_mapping()`. |
| 12 other prompts | MINOR | RESOLVED | 3.3 puts the fix and satisfaction prompts out of scope, with the reason. |
| 13 anthropic-api judges | MINOR | PARTIAL | 3.3 states the change is intended. The suggested AC10 case for an anthropic-api judge was not added (N11). |
| 14 error map | MINOR | RESOLVED, one gap | `URLError(timeout)` -> TIMEOUT, reset/`RemoteDisconnected` -> NETWORK, 4xx not recoverable except 408/429. `http.client.IncompleteRead` / `HTTPException` is still unmapped (N8). |
| 15 timeout / size | MINOR | RESOLVED | 16 MiB cap + "per socket operation" stated for docs/backends.md. Boundary weakly tested (N3). |
| 16 flags_catalog | MINOR | RESOLVED | Mandatory `str` entries, prefix documented in docs/backends.md. |
| 17 CHANGELOG | MINOR | RESOLVED | Section 4: one bullet in the existing `###` block. |
| 18 key drift | MINOR | RESOLVED (documented) | 3.1.1 (billing data follows the call-time key) + 3.1.4 (capability frozen at registration). |
| 19 golden after change | MINOR | RESOLVED | The guard `test_ac11_default_prompt_pins_the_three_write_instructions_verbatim` pins the three literals as they are on main. |
| 20 gate_label | MINOR | RESOLVED | `test_gate_label_is_echoed_into_data`. |
| 21 blind judge | MINOR | PARTIAL | 3.3 now says "the step logs that once", which is a new section 3 branch with no AC and no RED (N12). |
| 22 proxy | MINOR | RESOLVED | Non-goals + docs/backends.md `NO_PROXY`. |

## Step 1 - spec-internal consistency (literal-token drift)

- Env names, the error codes, `openai-compat`, `api_text_response`, `no_tools`, `billing:metered`,
  `effective_model_remapped`, `observed_tools` and `wall_ms` are spelled identically across 3.1-3.4, the AC
  table and the RED. No drift.
- 3.3 says the three instructions are replaced "with the same sentence" and, two sentences later, that
  "the VERDICT and `## Aggregated Findings` directives that sat on the OUTPUT line are kept in the
  replacement text". So the OUTPUT replacement is the sentence plus the directives, not "the same sentence".
  It also leaves open whether the `_role_line` replacement keeps "ROLE: You are the sole reviewer ...
  VERIFICATION-ONLY — do NOT edit code or test files" (the RED allows both readings). Drafting ambiguity;
  advisory (N7).
- 3.3 cites `canonical.py:39-49` as "replaced". Section 4 lists canonical.py as edited. The spec does not say
  whether the existing constant is edited or a sibling constant is added. This fork is load-bearing (N2).
- RED module docstring still says "AC1-AC15". Cosmetic.

## Step 1.5 - rule-overlap simulation

URL check (3.1.3): unset/blank -> MISSING; `file:///x`, `ftp://h` -> scheme -> INVALID; `http://`, `https://`
-> empty host -> INVALID; `localhost:11434` -> urlparse scheme `localhost` -> INVALID; `/v1` -> empty scheme ->
INVALID. All match AC8 and the RED.

Error classifier (3.1.6 + 3.1.7). Class nesting: `HTTPError` < `URLError` < `OSError`; `socket.timeout` ==
`TimeoutError` (3.10+; on 3.9 `socket.timeout` is an `OSError` subclass but not a `TimeoutError`, and
`requires-python = ">=3.9"`); `RemoteDisconnected` < `ConnectionResetError` < `OSError` and also <
`HTTPException`; `IncompleteRead` < `HTTPException` only. A correct order is HTTPError -> (TimeoutError,
socket.timeout) -> URLError with a timeout reason -> URLError -> OSError -> parse/size -> catch-all.

| Case | First matching branch | Outcome | RED |
|---|---|---|---|
| 301/302 + no-redirect handler | `redirect_request` returns None -> default handler raises `HTTPError(3xx)` | HTTP, not recoverable | AC17 |
| 307 POST | stock urllib already refuses (raises HTTPError 307) | HTTP, not recoverable | AC17 (307 does not discriminate; 301/302 do) |
| 401/403/404 | HTTPError | HTTP, not recoverable | AC17 |
| 408/429/5xx | HTTPError | HTTP, recoverable | AC17 / AC9 |
| `URLError(socket.timeout)` / `URLError(TimeoutError)` | URLError with a timeout reason | TIMEOUT | AC18 |
| raw `socket.timeout` from `getresponse()` (blocked server) | TimeoutError | TIMEOUT | AC9 |
| `URLError("dns failure")`, refused | URLError | NETWORK | AC18 / AC9 |
| reset before the response | `ConnectionResetError` / `RemoteDisconnected` from `getresponse()` (urllib wraps only `h.request`) | NETWORK | AC18 `before` |
| reset mid-body (SO_LINGER 0) | `ConnectionResetError` from `read()` | NETWORK | AC18 `mid` |
| EOF mid-body without RST | `IncompleteRead` (HTTPException, not OSError) | catch-all -> BAD_RESPONSE per the spec | none (N8) |
| `RuntimeError` | catch-all | BAD_RESPONSE, not recoverable | AC18 |
| body > 16 MiB | size check | BAD_RESPONSE | AC18 |

If HTTPError is caught after URLError, the 3xx/4xx/5xx cases return NETWORK; the RED catches that.

`_judge_can_write` (3.3): resolve with `_resolve_backend(None, env_mapping(), role="judge")` -> caps ->
`no_tools` and not `tool_allowlist` -> False; else True. Every AC10 case and the real openai-compat
registration give the expected value. `anthropic-api` (`{"no_tools", "billing:metered", "effort:*"}`)
-> False, which is intended by 3.3 but has no RED case (N11).

Billing (3.1.4 + `_billing_mode_of` :1462): keyless -> no `billing_mode`, no `billing:*` cap -> `unknown`;
keyed -> data `metered`. Matches AC4/AC15.

Effective model (3.1.8 + `_dispatch_backend` :1650-1690): hook("sonnet") with `HAL_OPENAI_MODEL_SONNET` ->
`local-big-model` != requested -> `effective_model_remapped` emitted, floor (if hard gate) on
`local-big-model` -> `_assert_hard_gate_opus` refuses. Without an override the hook returns the requested
alias (e.g. `opus`), so the floor passes on the alias while the server answers with whatever it maps that
name to. The R3.3 pin check only compares Claude families, so a non-Claude `observed_model` is
`not-checked` (:1349-1354). See adversarial edge 1.

## Step 2 - section 3 (change) vs section 5 (AC) cross-check

Change -> AC:
- 3.1.1-3.1.6 -> AC1-AC9, AC17, AC18. 3.1.7 -> AC17. 3.1.8 -> AC16. 3.1.9 -> AC19. 3.1.10 `gate_label`
  -> `test_gate_label_is_echoed_into_data` (no AC number, covered). 3.2 -> AC7, AC15. 3.3 -> AC10-AC12.
  3.4 -> AC14.
- 3.3 "a no-tools judge reviews from file paths only (it cannot Read); the step logs that once" is a
  side-effect branch with no AC and no RED (N12, MINOR: advisory logging only).
- 3.3 "the VERDICT and `## Aggregated Findings` directives ... are kept" has no discriminating assertion
  (N7).

AC -> producing change:
- AC11 / AC12: produced by 3.3, but only safely if the canonical.py change adds a new constant instead of
  editing `SINGLE_REVIEW_FRAMING_TEMPLATE`. Both readings satisfy the RED; one of them breaks sibling
  guards (N2, MAJOR).
- AC17: produced by 3.1.7, but the producing code (an opener's `.open(...)` call in
  `lib/reference_backends/openai_compat.py`) cannot land without a file that is not in scope (N1, MAJOR).
- All other ACs have a producing path in section 3.

## Step 3 - RED adequacy

- Collect time: module-level imports all exist today (`_BACKEND_CAPABILITIES`, `_BACKEND_MANIFEST_SOURCE`,
  `register_backend`, `reset_backends`, `phase_6_review`, `EventLog`, `telemetry_ctx`, `StepResult`,
  `WorkflowContext`). The new module is imported lazily in `_module()`. The facts package shows 93
  collected / 93 failing at call time.
- Victim coupling, AC16-AC19:
  - AC16: fails today on the missing module, then `_BACKEND_EFFECTIVE_MODEL[BACKEND]` KeyError. A backend
    without a hook fails the hook tests (KeyError) and the chokepoint test (no `effective_model_remapped`
    event, since `_effective_model` returns the requested model when there is no hook, :1273-1275).
    A backend that remaps only inside its impl is therefore killed. Discriminating.
  - AC17: a stock `urlopen` follows 301/302 POST as GET to the second server. The second server has no
    `do_GET`, answers 501, and records nothing (only `do_POST` appends to `requests`). The victim is still
    killed, but only because 501 maps to `recoverable=True` while the test asserts False. The headline
    assertion "the second server sees no request (no Authorization)" is blind to the GET it would
    receive (N4). 307 does not discriminate (stock urllib already refuses a POST 307).
  - AC18: reset/timeout/size/key cases fail today on the missing module, and each kills a plausible slip
    (catch-all BAD_RESPONSE for resets, NETWORK for `URLError(timeout)`, unbounded `read()`).
  - AC19: fails today on `code in ERROR_CODES`. The byte-equality assertions kill a GREEN that registers
    the codes but forgets to regenerate either catalogue. `observed_tools == []` kills a `None`/absent
    value.
- The `OpenerDirector.open` patch (AC18 transport parametrisation): it requires the HTTP call to go
  through `urllib.request.OpenerDirector.open` (a `build_opener()` opener or `urlopen` both do). A GREEN on
  `http.client` directly would fail these 6 TIMEOUT/NETWORK rows (the call would hit 127.0.0.1:9 and get
  a refusal). This constrains GREEN's call path, but only to what 3.1 already mandates ("stdlib `urllib`
  only", "an opener with a redirect handler"). It is consistent with the spec, so it is acceptable. Only
  exceptions raised from `open()` are exercised there; exceptions from `read()` are covered by the
  live-socket reset tests. Advisory: say in 3.1 that the request goes through
  `OpenerDirector.open`, so the seam is a spec decision and not a test accident.
- 16 MiB cap: `test_ac18_body_over_16_mib_is_a_bad_response` (17 MiB) and
  `test_ac18_body_just_under_the_cap_is_accepted` (about 1 MiB) bracket a 15 MiB window. A 2 MiB cap
  passes both, and so does a cap that checks only the `Content-Length` header (the server always sends
  one), which leaves a chunked or length-less body unbounded (N3).
- `effective_model` hook wiring: correct against the real seam (`register_backend(effective_model=)` ->
  `_BACKEND_EFFECTIVE_MODEL` -> `_effective_model` -> step 0). `reset_backends()` restores the map
  (:2860), so the autouse teardown cleans it. The tests set `HAL_OPENAI_MODEL_SONNET` before
  `register()`, so a hook that captures the alias table at registration passes, although 3.1.1 says the
  env is read at call time (N5).
- Stub-passability: no test mocks the unit under test. The AC11 golden monkeypatches `_judge_can_write`
  to True, but sits next to the no-tools test, and the guard now pins the literal default text from
  main. The AC18 transport patch replaces a stdlib seam, not the backend.
- Guards file - `test_ac11_default_prompt_pins_the_three_write_instructions_verbatim`: it passes today for
  the right reason. It builds the default prompt (conftest pins `HAL_RUNNER_BACKEND=claude-subprocess`,
  conftest.py:172-179; the guard deletes `HAL_RUNNER_BACKEND_JUDGE`), and asserts the three literals as
  they are on main plus the absence of the no-tools sentence. After GREEN, 3.3 requires the default
  prompt to stay byte-identical when `_judge_can_write` is True, so the guard stays green. It is a guard
  GREEN must not change, not a test GREEN must edit. No BLOCKER. It also kills a GREEN that edits shared
  prompt text in both branches, or that appends the no-tools sentence unconditionally.
  `test_ac11_tool_capable_judge_keeps_the_write_instructions` is likewise green before and after.
- Live test: the sibling placement is correct. Note: the deselection depends on the invocation. pytest
  only picks `engine_py/pyproject.toml` as the inifile when the common ancestor of the arguments is at or
  under `engine_py/` (CI runs from `engine_py`, so it holds there). A repo-root run over `engine_py/tests`
  and `tests/` together finds no inifile, and the live test then runs and skips when no server is
  reachable. Advisory (N13).

## Step 4 - reachability

| Side effect | Point (prod) | Host | Test path |
|---|---|---|---|
| Registration + hook | new `openai_compat.register()` -> `register_backend(..., effective_model=...)` (:2792) | `run.py` guarded block (next to :49-69) | AC2, AC13 (`_reload_run`), AC16 |
| HTTP call, data, errors | new backend impl via an opener | `_dispatch_backend` -> `_BACKENDS[name]` | AC2-AC9, AC17-AC19 through `invoke_llm_subprocess` |
| Remap event | `_dispatch_backend` :1674-1681 (existing) fed by the new hook | `invoke_llm_subprocess` | `test_ac16_chokepoint_records_the_remap_and_the_request_carries_it` |
| `wall_ms` in the cost event | `_cost_events` :1505-1540 | `_observe_cost` :1543 from `_dispatch_backend` | AC7, AC15 |
| Prompt swap | `_build_review_prompt` :770 / :868-875 / :1015-1020 | same function, gated by new `_judge_can_write` | AC11, AC12 |
| Review doc from stdout | `_write_review_artifact` stdout branch | `_aggregate_and_write_review_artifact` | AC12 (same composition, verified in r1) |
| Error codes catalogue | `error_codes.ERROR_CODES` + both md | `error_codes --check` / `render_markdown` | AC19 |

Sibling surfaces re-checked for r2:

- **class-I inventory lint (new, breaks).** `conformance/class_i_lint.py` scans every `.py` under
  `workflows/` and `lib/` (`_SCAN_DIRS`, :43, `rglob`). Any attribute call named `open` is an inventoried
  site (`_callee`, :109-110 -> `".open"`), and so is `json.load`. The opener 3.1.7 mandates is called as
  `opener.open(req, timeout=...)` in `lib/reference_backends/openai_compat.py`. That yields the key
  `lib/reference_backends/openai_compat.py::<qualname>::.open#0` with no entry in
  `conformance/class_i_inventory.json`. `test_bd150_class_i_inventory.py::test_ac10_real_tree_passes_lint`
  (:618-621) asserts `lint.check(<engine root>, load_inventory()) == []`. `preflight.py` reads the same
  inventory. The JSON is not in the spec's paths and section 4 ends "Nothing else", so GREEN cannot add
  the entry. The only ways round it are a process-global `install_opener` + `urlopen` (it would change
  `anthropic-api`'s urlopen behaviour too) or `http.client` (it fails the AC18 OpenerDirector seam). (N1)
- **canonical.py sha256 pins (new, conditional break).** `test_bd89_p3b1_single_reviewer_only.py:359-373`
  pins sha256 of `SINGLE_REVIEW_FRAMING_TEMPLATE` (`f42cf190...`) and `PER_ROLE_SCHEMA_TEMPLATE`
  (`b99ae1ec...`) ("spec: byte-identical"). `test_bd89_p3b1b_ii_aggregation_helper.py:297` asserts
  `"role-composite.md" in SINGLE_REVIEW_FRAMING_TEMPLATE`. `test_bd89_p3b1::test_ac4_single_prompt_is_byte_exact_render`
  (:346-356) renders the constant into the default prompt. The only other consumer of the constant is
  `phase_6_review.py:869`. A GREEN that turns the write line of the existing constant into a placeholder,
  or edits it in place, breaks the sha pin. A GREEN that adds a sibling constant (or does the swap in
  phase_6_review) keeps all three green. The spec does not choose. (N2)
- error_codes drift gate and the ERROR_CODES.md byte-equality tests listed in r1: covered by scoping + AC19.
  If GREEN also rewords the existing `E_LLM_API_*` descriptions ("anthropic_api backend: ..."), both md
  files must be regenerated. AC19 enforces this.
- test_bd71::test_ac7: satisfied by 3.1.9 if both keys appear as double-quoted literals.
- test_gh898: untouched (no hint entry).
- flags_catalog: no test checks for dead flags. Reads through `env_mapping().get(...)` are not matched by
  `_TOKEN_PATTERN`, so `E_FLAG_UNREGISTERED` is not triggered. Entries are documentary. OK.
- core_boundary: `HAL_OPENAI_*` literals must stay in openai_compat.py (not core). `_judge_can_write`
  passes a mapping. OK.
- CI receipt step (`ci.yml:139-164`, grep `^8 passed`): output gains "N deselected" only after "8 passed",
  so `-k` already deselects there. OK.

## Adversarial edges (not covered by the AC table)

1. Hard gate without an alias override: `effective_model("opus") == "opus"`, so the floor passes, and the
   server (LM Studio and some vLLM setups ignore or alias the model name) answers with a local model.
   `observed_model` (e.g. `llama3`) has no Claude family, so R3.3 records `not-checked` and the gate
   passes on a model that is not Opus. The hook only closes the remapped case.
2. Hook freezing: a hook that captures `HAL_OPENAI_MODEL_*` at `register()` floor-checks a stale id after
   the env changes, while the backend (call-time read) sends the new one.
3. Mid-body EOF without RST (proxy or server crash with FIN): `http.client.IncompleteRead` is not an
   `OSError`, so under 3.1.6 it is a non-recoverable BAD_RESPONSE, while the RST variant is a recoverable
   NETWORK error. The same failure gets two recoverability classes depending on TCP teardown.
4. A chunked or length-less body over 16 MiB against a cap that reads `Content-Length` only. Also a
   `Content-Length` larger than the body.
5. A followed GET redirect is invisible to the RED's second server (no `do_GET`). The key could leak on a
   GET without the headline assertion seeing it.
6. Env alias fallback: `config_provider.env_mapping()` serves `HAL_<X>` from `BD_<X>` / `BYTEDIGGER_<X>`.
   The RED's autouse fixture deletes only `HAL_OPENAI_*`, so a developer shell with `BD_OPENAI_BASE_URL`
   set turns the AC1/AC8/AC13 "unset" cases into "set" (local non-hermeticity, not a CI risk).
7. Python 3.9 (`requires-python >=3.9`): `socket.timeout` is not `TimeoutError`, so a classifier that
   checks only `TimeoutError` passes on 3.11 CI and misroutes timeouts on 3.9. The RED parametrises both,
   but CI runs one interpreter.

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
- AC16: PASS
- AC17: FAIL
- AC18: PASS
- AC19: PASS

## Findings

N1. MAJOR - The mandated no-redirect opener breaks the class-I inventory lint, and the fix is out of
    scope. 3.1.7 requires an opener. Its `.open(...)` call in `lib/reference_backends/openai_compat.py` is
    an inventoried site (`class_i_lint._callee` :109-110, scan dirs `workflows`/`lib`) with no entry in
    `conformance/class_i_inventory.json`, so
    `test_bd150_class_i_inventory.py::test_ac10_real_tree_passes_lint` fails after a spec-literal GREEN.
    The AC18 `OpenerDirector.open` seam rules out the `http.client` workaround, and a global
    `install_opener` would also change `anthropic-api`. Fix: add
    `engine_py/bytedigger_engine/conformance/class_i_inventory.json` to paths and section 4, with one
    entry, class `not-prompt` or `M` as the inventory rules dictate, for
    `lib/reference_backends/openai_compat.py::<qualname>::.open#0`, and name the qualname. Tell GREEN to
    parse with `json.loads` (not inventoried), not `json.load(resp)`.
N2. MAJOR - canonical.py edit vs the sha256-pinned templates. 3.3/section 4 tell GREEN to edit canonical.py
    and "replace" `SINGLE_REVIEW_FRAMING_TEMPLATE`, but do not say the existing constant stays
    byte-identical. `test_bd89_p3b1_single_reviewer_only.py:359-373` pins its sha256 (and
    `PER_ROLE_SCHEMA_TEMPLATE`'s), `:346-356` renders it into the default prompt, and
    `test_bd89_p3b1b_ii_aggregation_helper.py:297` requires `role-composite.md` in it. Both GREEN readings
    pass the bd83 RED, and one breaks three sibling guards. Fix: state that `SINGLE_REVIEW_FRAMING_TEMPLATE`
    and `PER_ROLE_SCHEMA_TEMPLATE` stay byte-identical (cite the pins), and that the no-tools framing is a
    new constant in canonical.py (name it), selected in `_build_review_prompt` when `_judge_can_write` is
    False. Alternatively drop canonical.py from paths and do the swap in phase_6_review.
N3. MINOR - 16 MiB cap semantics and boundary. Say that the body is read with a hard bound (read at most
    16*1024*1024 + 1 bytes regardless of `Content-Length`; over the bound -> BAD_RESPONSE), and that
    "16 MiB" means 16,777,216 bytes of raw body. The "just under the cap" test uses about 1 MiB, so a
    2 MiB cap or a header-only check passes. Advisory RED: one body at exactly the cap is accepted, and
    one at cap+1 without `Content-Length` (chunked) is refused.
N4. MINOR - AC17 observation channel. `_serve` records only `do_POST`, so a followed 301/302 (re-issued as
    GET) is invisible to `seen_by_second`. The test still kills the stock opener, but only through the 501
    recoverability. Record every method (e.g. `do_GET = do_POST` with header capture) so "the second
    server sees no request" is measured directly.
N5. MINOR - The hook is not tested for a call-time env read. Add one case: register, then set
    `HAL_OPENAI_MODEL_SONNET`, then `hook("sonnet")` returns the new value (3.1.1 says call time).
N6. MINOR - The hard-gate consequence is still unstated (r1 finding 5). State in 3.1.8 / docs/backends.md:
    with a remap to a non-Claude id the floor refuses every hard gate. Without a remap the floor checks
    the requested alias only, and R3.3 cannot check a non-Claude served model (adversarial edge 1).
    Consider refusing hard gates on `openai-compat` outright, or record the decision as a non-goal.
N7. MINOR - The "directives kept" assertions are vacuous, and "same sentence" contradicts "directives
    kept". `"VERDICT:"` is always present through `PER_ROLE_SCHEMA_TEMPLATE`, and `"## Aggregated Findings"`
    through `phase_6_review.py:1006`. Neither depends on the OUTPUT replacement. Quote the exact OUTPUT
    replacement text in 3.3 (sentence + `VERDICT: <PASS|PARTIAL|FAIL>` + leading `## Aggregated
    Findings`), say what the `_role_line` replacement keeps (VERIFICATION-ONLY or not), and assert the
    OUTPUT replacement line in the RED.
N8. MINOR - `http.client.IncompleteRead` / `HTTPException` is unmapped (edge 3). Map it to
    `E_LLM_API_NETWORK` (recoverable) next to `RemoteDisconnected`, or state the BAD_RESPONSE choice.
N9. MINOR - `PER_ROLE_SCHEMA_TEMPLATE` still says "Required last line of the role file" in the no-tools
    prompt (r1 finding 1, second half). Its bytes are pinned (N2), so the spec should say it is left as is
    deliberately (the stdout path ignores the role-file count) rather than leave it unaddressed.
N10. MINOR - test_bd71:220 matches source literals. Say in 3.1.9 that `"observed_tools"` and
    `"observed_model"` appear as double-quoted string keys in openai_compat.py, or the conformance gate
    reports the backend as silent.
N11. MINOR - No AC10 case for an `anthropic-api` judge (r1 finding 13). Add
    `_judge_can_write is False` for the real anthropic-api capability set.
N12. MINOR - "the step logs that once" (3.3) has no AC/RED. Name the record (logger message or event
    type) and add one assertion, or drop the sentence.
N13. MINOR - Advisory: AC14's "the plain `pytest` run reports it deselected" holds only when the inifile is
    discovered (invocation at or under `engine_py/`). Say so in 3.4, or note that a repo-root run skips it
    instead.

Summary: all seven r1 MAJORs are resolved against the code. The redirect, effective_model, error-code,
observed_tools, install-hint and live-test fixes are real, and the RED for AC16-AC19 is discriminating and
fails at assert time. The guard that pins the three write-instruction literals passes for the right reason
and must stay green after GREEN (default prompt byte-identical); it is not a blocker. Two new MAJORs
block GREEN. The opener that 3.1.7 mandates is an unclassified class-I site, and the inventory file that
would fix it is out of scope (test_bd150 AC10 fails). The canonical.py edit is not constrained to keep
`SINGLE_REVIEW_FRAMING_TEMPLATE` byte-identical, although three sibling guards pin it. Both are fixed by
spec text plus one path; the RED needs no change for either (N3-N5 and N7 are optional RED hardening).

VERDICT: REJECTED
