# bd#105: validation gate r1 (spec r1 + RED)

Audited: `docs/decisions/2026-10-11-bd105-restricted-setting-sources.md`, `engine_py/tests/test_bd105_restricted_setting_sources.py` (49 tests; preflight facts: 0 pass / 49 fail on dd6c89f, all 16 siblings pass).
Code read: `llm_subprocess.py` L187-191 (`_BACKEND_CAPABILITIES`), L1126-1156 (`_backend_capabilities`, `available_tools`), L1614-1819 (`_dispatch_backend`), L1822-1857 (`_fallback_hang_attempts`), L1998-2151 (`invoke_llm_subprocess`, fallback re-dispatch L2133), L2154-2247 (`_invoke_subprocess`, argv build L2196-2212, `Popen` without `cwd=` L2241), L2821-2898 (`register_backend`, `reset_backends`), L3919 (`_emit_safe`); `lib/reference_backends/agent_sdk.py` L334-344 (`_tool_options`), L378-475 (root, `option_kwargs`), L772-791 (`register`); installed `claude_agent_sdk` 0.2.120 (`types.py` L1769 `strict_mcp_config: bool = False`, L1953 `setting_sources: list[SettingSource] | None = None`; `subprocess_cli._build_command` L282-451: `--strict-mcp-config` L401, `--setting-sources=<joined>` L413-414); `core_boundary.py`, `engine_py/core_manifest.json`, `scripts/flag_owner_lint.py`, `test_register_backend_A60F1FE3.py` L436-442, `phase_5_implement.py` L7025-7036.
Not read: GitHub issue #105 itself (this agent has no shell, so `gh` is unavailable). The issue-side drift check below is against the scope summary in the gate brief.

## Step 1: spec-internal consistency (literal-token drift)

- Event names `setting_sources_isolated`, `setting_sources_isolation_skipped`, `setting_sources_config_error`: identical in Design steps 2-5, the AC table and RED `_EVENT_NAMES`. Consistent.
- Reasons `backend_unsupported`, `auth_in_settings`; the marker `"<source>:<unreadable>"`; sources `user|project|local`: identical in spec and RED (`_AUTH_CASES`, AC7). Consistent.
- Capability `setting_sources_isolation`, keyword `isolate_setting_sources`, helpers `_setting_sources_isolation` / `_settings_auth_keys`, config key `restricted_roles.setting_sources`, values `inherit|isolated`, `expected: ["inherit","isolated"]`, `value` = `repr` cut at 40: identical in spec and RED (AC5 asserts `"'bogus'"` and `"5"`). Consistent.
- Argv tokens: subprocess `["--setting-sources", "", "--strict-mcp-config"]` (spec Applying + AC2 = RED `_SUB_ISOLATION_TAIL`); agent-sdk `--setting-sources=` and `--strict-mcp-config` (spec AC1 = RED `_SDK_ISOLATION_TOKENS` = SDK `_build_command` L401, L414 with `setting_sources=[]`). The two backends use different argv forms (`=` vs a separate `""`); both are what the respective producer emits. Consistent.
- Literal pins (spec section "Literal pins") vs RED constants: `_SUB_PATTERNED`, `_SUB_READ`, `_SDK_PATTERNED`, `_SDK_READ` are character-identical to the spec. Cross-checked against code: `_claude_build_argv` gives `claude -p --model <m>`, `stream_flags` + `--forward-subagent-text` (fixture deletes `HAL_FORWARD_SUBAGENT_TEXT`, default on), the `--allowed-tools`/`--tools` pair L2208-2212, no effort (no `claude` key in the test models file), no stable prefix; SDK order `cli, --output-format, stream-json, --verbose, --system-prompt '', --tools, --allowedTools, --model, --permission-mode, ..., --input-format stream-json` matches `_build_command`. Byte-identical by construction.
- Code line references in the spec (agent_sdk l.334, l.465; `_invoke_subprocess` l.2208; `phase_5_implement.py:7033`) match the tree at dd6c89f.
- `step_name` in payloads: RED pins the dispatch `step_name` (`_STEP = "bd105"`) while the run context step is `"s"`. The spec says only `step_name`. `_dispatch_backend`'s own events use the dispatch value, but `agent_sdk.py` uses `run_ctx.step_name or step_name` (L434, L747), which would give `"s"`. Drift risk for GREEN, see MINOR-5.
- Issue vs spec (against the brief): not isolated when user/project/local settings carry `apiKeyHelper` or auth keys in `env`, one event with key names only (step 4, AC6, AC11); settings read only for detection, read/parse failure counts as found (probe text, AC6 unparsable/list); `strict_mcp_config` on agent-sdk too (flagged as a refinement in the spec); default `inherit` (Principle 4, AC3); another value gives one config-error event and inherit (step 2, AC5). The out-of-scope items (the default, roles without a patterned entry, other backends, the matcher, role tool sets, `_resolve_backend`) are not touched by the design. No drift found.

## Step 1.5: rule-overlap simulation

Decision order (Design steps 1-5, first match decides): (1) no patterned entry -> inherit, silent; (2) value missing/unreadable/malformed/`inherit` -> inherit, silent; any other value -> inherit + `config_error`; (3) `isolated` + backend without capability -> inherit + `skipped/backend_unsupported, keys []`; (4) `isolated` + auth found -> inherit + `skipped/auth_in_settings, keys`; (5) isolate + `isolated`.

| tools | config | backend capability | settings | first matching step, outcome | RED |
|---|---|---|---|---|---|
| `["Read"]` | isolated / bogus / 5 | yes | clean | 1: inherit, no event | AC4 x6, AC10-cap leg 2 |
| patterned | other_key / inherit / missing / malformed / list | yes | clean | 2: inherit, no event | AC3 x10, AC10-cap leg 3 |
| patterned | bogus / 5 | yes | clean | 2: inherit, one config_error | AC5 x4, AC10-cap leg 4 |
| patterned | bogus / 5 | no | clean | 2: one config_error, no skip (step 3 never reached) | AC10-strict first leg (bogus, 5) |
| patterned | isolated | no | clean | 3: one skip, backend_unsupported, keys [] | AC7, AC10-strict both legs |
| patterned | isolated | no | auth present | 3 (not 4): backend_unsupported, keys [] | not tested (adversarial 1) |
| patterned | isolated | yes | auth / unreadable / JSON list | 4: one skip, auth_in_settings, names only | AC6 x8 |
| patterned | isolated | yes | allow rules + non-auth env / non-object env | 5: isolated | AC6 x4 |
| patterned | isolated | yes | clean, env creds in process | 5: isolated, env ignored | AC8 x6, AC1, AC2 |
| patterned | isolated | agent-sdk fake (cap) hangs, fallback to subprocess | clean | 5 on primary, 5 again on fallback (re-decides) | AC9 |

Every row yields the spec outcome at the first matching branch, with one exception in the RED, not in the spec: `test_ac10_strict_signature_backend_never_gets_new_keyword[value-isolated]`. Its first leg already matches step 3 and emits one `setting_sources_isolation_skipped`; the contrast leg (also `isolated`) emits a second; the test then asserts `len(skipped) == 1` over the whole log. A correct GREEN produces 2. See MAJOR-1.

## Step 2: section 2 (design) vs section 3 (AC) cross-check

- Step 1 -> AC4 (and AC10-cap leg 2). Step 2 silent branch -> AC3 (missing, malformed, list, other key, `inherit`). Step 2 error branch -> AC5 (string and non-string). Step 3 -> AC7, AC10. Step 4 -> AC6 (apiKeyHelper user, env key project, unparsable local, JSON list project), AC11. Step 5 -> AC1, AC2, AC8. Fallback re-decision -> AC9. "Other backends never get the keyword" -> AC7, AC10. Non-object `env` ignored -> AC6 negative case.
- The terminal/degrade branches (config error, capability skip, auth skip, unreadable file) each have an AC and tests. No orphan AC.
- Two design statements have no AC: (a) the agent-sdk capability is registered only when the installed `ClaudeAgentOptions` has both fields (an older SDK degrades through step 3); the installed SDK has both, so no test can reach the other branch without faking the SDK. (b) "nothing is read" at step 1. Both advisory (adversarial 2, 3).
- Placement inside `_dispatch_backend` is given only as "before the handler is called". The chokepoint contract (docstring items 1-2, `[bd10:27]`: a refusal emits nothing) is not addressed; see MINOR-4.

## Step 3: RED adequacy

- Count: AC1 1, AC2 1, AC3 10, AC4 6, AC5 4, AC6 8 + 4, AC7 1, AC8 6, AC9 1, AC10 1 + 5, AC11 1 = 49; matches the facts.
- Assert time, not collect time: module imports are existing names only (`_settings_auth_keys` is reached as an attribute inside AC11's body); SDK imports are lazy. Facts: 49 fail, none at collection.
- Fails today for the right reason: AC1/AC2/AC6-negative/AC8/AC9 on the missing isolation tokens; AC3/AC4/AC10-strict pass their first leg today and fail on the contrast leg (no isolation, no event); AC5 and AC7 on the missing event; AC6 auth cases on the missing skip event; AC10-cap on the missing keyword; AC11 on the missing helper.
- Not vacuous: only `subprocess.Popen` and `claude_agent_sdk.query` are replaced; argv for agent-sdk is built by the real `SubprocessCLITransport._build_command` from the options the real backend built; events from a real `EventLog`; config and settings are real files. Fake backends (AC7, AC9, AC10) stand in for the dispatched handler, not for the unit under test (the chokepoint decision).
- Contrast legs (AC3, AC4, AC10-strict). Soundness: the default-path legs still pin the literal argv and zero decision events, and are evaluated first, so a GREEN that changes a default path fails the first leg. The contrast legs reuse the same root and log and only flip the config, which is a fair proof that the switch is live. Passability by a correct GREEN: AC3 (all 10 params) yes; AC4 (all 6) yes, since step 1 is silent for every value; AC10-strict yes for `inherit`, `bogus`, `5`, `missing` (0 or one config_error, then exactly one skip), NO for `isolated` (MAJOR-1).
- Default-path pins are truly byte-identical: AC3/AC4/AC5/AC6-auth compare the full argv list to the literal pins (not a subset), and the agent-sdk pin is produced by the real transport, so an added option such as `strict_mcp_config=False` (no flag), `setting_sources=None` (no flag) is harmless while any emitted flag fails. The isolated-path check for agent-sdk removes exactly the two tokens and compares the rest to the pin, so nothing else may change.
- Over-pinning: event payloads are checked as subsets (extra keys allowed); argv tail position for the subprocess is pinned only to "after the pair", which coincides with the end here (no effort, no stable prefix). AC1 pins the full rest-of-argv, which is stricter than the AC1 row wording but follows from AC3's "nothing else changes". The `step_name` value pin is a real requirement that the spec does not state explicitly (MINOR-5). Nothing else beyond the spec.
- Correct-GREEN walk (spec Design + Applying): AC1, AC2 pass (capability present on both shipped backends after GREEN; `register()` re-run in `_run` picks up the new capability). AC5 config_error payload subset passes. AC6 skip payload incl. `backend` passes; the secret is never in argv and a names-only probe never writes it to an event. AC7: `tool_allowlist` on the fake avoids the bd#82 refusal; step 3 fires. AC8: env vars are not setting sources; one `isolated` event. AC9: `_fallback_hang_attempts` conjuncts hold (resolved `agent-sdk`, `E_LLM_API_TIMEOUT`, `hang_attempts` 1, fallback env var deleted, idle disabled); the fallback re-decides and the subprocess argv carries the tail. AC10-cap passes. AC11: `CLAUDE_CONFIG_DIR` from the fixture, sorted comparison. AC10-strict `isolated` fails (MAJOR-1).
- Wrong-implementation probes caught: decision only on the primary dispatch (AC9); keyword sent to every backend (AC7, AC10, strict signature raises); isolation on a plain list (AC4); isolation on a bad value (AC5); values in `keys` (AC6, AC11); env credentials treated as settings (AC8); a non-object `env` crashing or counting as found (AC6 negative); `strict_mcp_config` omitted on agent-sdk (AC1).

## Step 4: reachability (section 1y)

- AC1, AC2, AC3, AC4, AC5, AC6, AC8. Point: the new `_setting_sources_isolation(...)` call in `_dispatch_backend` (between the injection refusal L1749 and the `optional` assembly L1755) and `optional["isolate_setting_sources"] = True`; the appended tail in `_invoke_subprocess` after L2212; `setting_sources=[]` / `strict_mcp_config=True` in `agent_sdk_backend` `option_kwargs` (L458-465). Hosts: `_dispatch_backend`, `_invoke_subprocess`, `agent_sdk_backend`. Test path: `invoke_llm_subprocess(backend=...)` -> `_dispatch_backend` -> registered handler -> `Popen` argv / `query(options=...)` -> real `_build_command`. Reachable.
- AC7, AC10. Point: the capability check in the decision and the conditional keyword. Host: `_dispatch_backend`. Test path: `register_backend` fakes via `invoke_llm_subprocess`. Reachable.
- AC9. Point: the second `_dispatch_backend("claude-subprocess", ...)` at L2133. Host: `invoke_llm_subprocess`. Test path: `agent-sdk` overwritten with a hanging fake, real `claude-subprocess` handler, patched `Popen`. Reachable.
- AC11. Point: `_settings_auth_keys`. Host: module function. Test path: direct call. Reachable.

## Sibling audit (section 1a)

1. Capability-set equality: `test_register_backend_A60F1FE3.py` L436-442 asserts EXACT equality of `_BACKEND_CAPABILITIES["claude-subprocess"]` after reset with `{"manifest","progress_since","abort","tool_allowlist","effort","reports_cost"}`. Adding `setting_sources_isolation` breaks it. The spec says such a test "is migrated in the same PR and listed in it" but does not name it. It is the only exact-equality pin found (`test_bd167` L1006-1012, `test_bd10_l3_authorship` L1941, `test_bd82_*` use membership). `test_gh1082_backend_registry_isolation.py` has no equality on shipped sets. MINOR-1.
2. Core-boundary lint: `llm_subprocess.py` is in `core_manifest.json` `core_modules`; `forbidden.path_literals` holds `"~/.claude"` and `".claude/"`, and the segment-anchored rule (`core_boundary._segment_anchored_patterns`) flags any non-docstring string constant equal to or segment-containing `.claude` (e.g. `Path(root) / ".claude" / "settings.json"`, `Path.home() / ".claude"`). The probe the spec prescribes cannot be written in `llm_subprocess.py` without either the `# core-boundary: allow <reason>` pragma on the line (precedent: `verification_registry.py:57`) or a host seam. `test_core_boundary.py` / `test_gh1111_core_boundary_blind_spots.py` are not in `sibling_tests`. `CLAUDE_CONFIG_DIR` itself is not a `HAL_` env read, so the env rule is not hit. MINOR-2.
3. Strict-signature fakes: dozens of tests register strict or `claude-subprocess`-named fakes (e.g. `test_bd206_class_m_sites.py` CAPS, `test_bd141_*`, `test_bd150_*`); none declares `setting_sources_isolation` and none sets `restricted_roles`, so the keyword is never sent (step 1/2 first). Unaffected.
4. Tests counting events: with no models key the decision is silent at steps 1-2, so no existing event count changes. Unaffected.
5. Packaging `py-modules`: no new module (both helpers in `llm_subprocess.py`). Unaffected.
6. `flag_owner_lint` / `flip_horizon`: the switch is a models-config key, not a `FLAGS` catalog entry; neither lint applies. The spec's Expires date has no machine owner; advisory only.
7. `docs/configuration.md`: in `paths`, but the spec does not say where the row goes or what it says; the file documents models-config keys only in prose (L172-179). No drift test covers it. Advisory (MINOR-7).
8. Siblings listed in the spec: all 16 green at RED; the default path is byte-identical, so they stay green, except item 1 (not in the list) and item 2 (not in the list).

## Security reasoning

- Settings scope: `--setting-sources ""` / `setting_sources=[]` drops user, project and local settings (permissions, hooks, `defaultMode`, `enabledPlugins`, CLAUDE.md for the SDK), and `--strict-mcp-config` drops MCP servers from `.mcp.json` and `~/.claude.json` user/local scope. With nothing else on the argv (`--settings`, `--mcp-config`, `--add-dir`, `--plugin-dir`, `extra_args`, `skills` all unset, verified in `_build_command` and the subprocess argv), the declared list is the upper bound. On agent-sdk `dontAsk` denies unapproved calls; on the subprocess `-p` without an approving rule denies. Sound.
- Managed (policy) settings still apply: listed as a known limit. They are operator policy, so this is not a widening by the call.
- Environment: the process environment is inherited unchanged by both backends (no `env=` on `Popen`, SDK passes `os.environ`). Environment variables do not carry permission rules, so no widening path. `CLAUDE_CONFIG_DIR` read by the probe is the same one the child sees. An empty `CLAUDE_CONFIG_DIR` falls back to `~/.claude` per the spec wording; a value with a literal `~` is not specified (advisory).
- Symlinks: the probe follows symlinks as the CLI does; a dangling symlink is "missing" for both; a directory or unreadable target is "found" (fail-safe). Sound, provided "missing" means `FileNotFoundError` only and every other `OSError` counts as found (spec wording supports this; no test for a permission-denied file, adversarial 4).
- Probe root vs where the CLI runs: `_invoke_subprocess` spawns without `cwd=` (L2241), so the subprocess CLI reads project settings from the process cwd, not `extra_data["workspace_root"]`. The spec's "the directory the backend runs in" is accurate for agent-sdk (L381) but not for claude-subprocess. Failure direction: under isolation the CLI loads no project settings at all, so a wrong root cannot widen anything; it can only (a) miss auth that lives in the cwd's project settings, giving a loud auth failure, or (b) refuse isolation because of the workspace root's settings (fail-safe). MINOR-3.
- Isolation also drops operator safety hooks and `permissions.deny`. The upper bound stays the declared list, but an operator who relies on a PreToolUse hook as an audit or guard loses it for the isolated call. Not stated in Known limits. MINOR-6.
- Auth key list: covers the documented settings auth helpers (`apiKeyHelper`, `awsAuthRefresh`, `awsCredentialExport`, `gcpAuthRefresh`) and the main env routes (Anthropic key/token/base URL, OAuth token, Bedrock/Vertex/Foundry switches, AWS keys/profile, Google credentials). Not covered: `ANTHROPIC_CUSTOM_HEADERS` (gateway auth headers), `ANTHROPIC_FOUNDRY_API_KEY`, proxy/TLS settings (`HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS`, `CLAUDE_CODE_CLIENT_CERT`/`_KEY`). A miss fails loudly (auth or network error), it never widens. Advisory, MINOR-6.
- No value leaks: the probe returns names only and the unreadable marker carries no exception text; the RED checks events and argv. Logging is not checked (adversarial 5).

## Adversarial edges (not covered by the section 3 AC table)

1. **Step 3 vs step 4 overlap**: `isolated`, backend without the capability, AND auth in settings. First match is step 3: `backend_unsupported` with `keys: []`, and the settings files must not be read at all. A GREEN that probes first and reports `auth_in_settings` (or fills `keys`) is not caught.
2. **Older SDK branch**: `ClaudeAgentOptions` without `setting_sources` or `strict_mcp_config` -> `register()` must omit the capability, so an `isolated` call degrades through step 3 instead of the `cannot build restricted options` RuntimeError. Testable by registering against a monkeypatched `ClaudeAgentOptions` dataclass lacking one field. No test.
3. **No read at step 1**: a plain list with a models file that raises on open (or a settings file that would be "found") must stay silent and unread. AC4 uses a readable file; a GREEN that reads config first and then checks the list passes.
4. **Probe error classes**: a settings file that exists but is not readable (mode 000) or is a directory must count as found; `restricted_roles` present but not an object (`"restricted_roles": "isolated"`) is unspecified (inherit silent, or config error?). No test.
5. **Value hygiene beyond events**: a GREEN that logs the parsed settings dict or the `JSONDecodeError` text at warning level would leak `apiKeyHelper` commands; no `caplog` assertion.
6. **Multi-file order**: spec pins the order user, project, local and the RED only checks one file per case (AC6) or a sorted list (AC11); an unordered or set-based result is not caught.
7. **Refused dispatch + isolated config**: a hard gate refused by the model floor or the bd#82 tool refusal must emit no decision event (`[bd10:27]`). Not tested.
8. **Decoy step_name**: covered implicitly (run ctx `"s"` vs dispatch `"bd105"`), but no test for a `None`/missing run context, where the decision must still apply the keyword and simply emit nothing.

## Findings

1. MAJOR-1: `test_ac10_strict_signature_backend_never_gets_new_keyword[value-isolated]` cannot pass a correct GREEN. Spec step 3 makes the first leg (config `isolated`, backend without the capability, patterned list) emit one `setting_sources_isolation_skipped`; the contrast leg emits a second; `assert len(skipped) == 1` counts the whole log and gets 2. The other four parameters pass. Fix in the RED (no spec change): take `before = len(_events(log, "setting_sources_isolation_skipped"))` after the first leg and assert `== before + 1` after the contrast leg; preferably also assert the first-leg decision events per parameter (`isolated`: one skip `backend_unsupported`; `bogus`/`5`: one `setting_sources_config_error`, no skip; `inherit`/missing: none), which makes the first leg prove step order as well.
2. MINOR-1: `test_register_backend_A60F1FE3.py` L436-442 (exact frozenset equality for `claude-subprocess`) will fail after GREEN; the spec's sibling audit should name it and state the migration (add `"setting_sources_isolation"` to `expected_caps` with the "UPDATED, not weakened" note, as bd#167 did) and who makes the edit, and add the file to `sibling_tests`.
3. MINOR-2: core-boundary lint. The probe's `.claude` path segments in `llm_subprocess.py` (a core module) are `path_literal` violations. The spec should prescribe the `# core-boundary: allow <reason>` pragma on those lines (precedent `verification_registry.py:57`) or a seam, and list `test_core_boundary.py` in `sibling_tests`.
4. MINOR-3: for claude-subprocess the CLI runs in the process cwd (`Popen` without `cwd=`), not in `extra_data["workspace_root"]`; correct the spec's "(the directory the backend runs in)" and add a Known-limits line (fail directions are safe: no widening; worst case a loud auth failure). The RED pins `workspace_root`, so do not switch to cwd without changing AC6.
5. MINOR-4: pin the placement of the decision in `_dispatch_backend`: after all refusals (floor, tool refusal, effort config, effort pin, injections) and immediately before the `optional` assembly, so a refused dispatch emits no decision event (`[bd10:27]`).
6. MINOR-5: state in the spec that every decision event's `step_name` is the dispatch's `step_name` (not `run_ctx.step_name`); the RED already pins this.
7. MINOR-6: Known limits should also say that isolation drops operator hooks and `permissions.deny` for the call, and that the auth list is closed (gateway headers, Foundry key, proxy/TLS env in settings are not detected and would fail loudly). Optionally extend the list with `ANTHROPIC_CUSTOM_HEADERS` and `ANTHROPIC_FOUNDRY_API_KEY`.
8. MINOR-7: `docs/configuration.md` is in `paths` without a stated row; name the section and the content (key, values, default, events, the auth limit).

One MAJOR. The spec is consistent and in scope, the decision order is unambiguous, the default-path pins are literal and byte-identical, and every other test is passable by a correct GREEN and fails today for the right reason. The single RED defect is a counting error in one parameter of one test, but as written it fails a correct implementation, so the gate cannot pass it.

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
- AC10: FAIL
- AC11: PASS

VERDICT: REJECTED

VERDICT: REVISE. Conditions for r2: (1) fix MAJOR-1 in `test_ac10_strict_signature_backend_never_gets_new_keyword` (delta count on the contrast leg; per-parameter first-leg event assertions recommended); (2) name `test_register_backend_A60F1FE3.py` L436-442 and its migration in the spec and `sibling_tests` (MINOR-1); (3) prescribe the core-boundary pragma for the settings paths and list `test_core_boundary.py` (MINOR-2). MINOR-3 to MINOR-7 are advisory spec-text edits that should ride along with the r2 revision.
