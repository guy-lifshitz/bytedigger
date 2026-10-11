---
red_tests:
  - engine_py/tests/test_bd105_restricted_setting_sources.py
sibling_tests:
  - engine_py/tests/test_bd82_tool_restriction.py
  - engine_py/tests/test_bd82_role_backend_effort.py
  - engine_py/tests/test_bd82_fresh_sessions_thread_ctx.py
  - engine_py/tests/test_bd82_semantic_verifier_chokepoint.py
  - engine_py/tests/test_bd101_fresh_default.py
  - engine_py/tests/test_bd103_effective_model_hook.py
  - engine_py/tests/test_bd107_effort_config_fail_closed.py
  - engine_py/tests/test_llm_subprocess_allowed_tools.py
  - engine_py/tests/test_register_backend_A60F1FE3.py
  - engine_py/tests/test_gh1082_backend_registry_isolation.py
  - engine_py/tests/test_gh1169_agent_sdk_hang_recovery.py
  - engine_py/tests/test_gh933_agent_sdk_stderr_outage.py
  - engine_py/tests/test_bd71_agent_sdk_observations.py
  - engine_py/tests/test_bd10_l3_authorship.py
  - engine_py/tests/test_bd141_check_ladder.py
  - engine_py/tests/test_core_boundary.py
  - engine_py/tests/test_phase_5_graphfirst_DA48BEAC.py
paths:
  - engine_py/bytedigger_engine/llm_subprocess.py
  - engine_py/bytedigger_engine/lib/reference_backends/agent_sdk.py
  - engine_py/tests/test_register_backend_A60F1FE3.py
  - docs/configuration.md
  - CHANGELOG.md
---
# bd#105: a restricted call with a patterned approve entry can run with its setting sources isolated

**Status: r1, TIER3** (a permission boundary). Follow-up to #82 part 1 (#96).
**Class:** SYSTEMATIC: the declared tool list is meant to be the upper bound of what a restricted role runs, but a role with a patterned entry (`Bash(x:*)`) needs the whole tool available, so any `permissions.allow` rule or MCP allow in the operator's settings widens it. Any future patterned role would inherit the same hole.
**Chokepoint:** `llm_subprocess._dispatch_backend`, the one function that hands optional keywords to every backend and is used for the primary dispatch and the agent-sdk to claude-subprocess fallback. The decision is made there once per dispatch; the two backend builders only apply a boolean.
**Side of the seam:** engine. **Owner:** bd. **Expires:** 2026-11-10: by then the switch has run in `isolated` on a real pipeline and there is one decision on flipping the default (a separate issue, not this lot).

| | |
|---|---|
| **LLM calls added** | None. A config read, a bounded read of up to three settings files, at most one event. |

## Principles (Guy's, for bd)

1. **Deterministic first.** The decision is a pure function of the declared tool list, one config value, the backend's declared capability and the presence of a closed list of keys in the settings files. No model, no probe of the CLI.
2. **Provider-agnostic: a path degrades, it does not fail or stay silent.** A backend that cannot isolate (no `setting_sources_isolation` capability, an SDK too old to carry the option) runs as `inherit` and one `setting_sources_isolation_skipped` event says why. A bad config value is one config-error event.
3. **Works on a subscription and on API tokens.** Subscription login (OAuth / keychain) and `ANTHROPIC_API_KEY` / cloud credentials in the process environment are not setting sources and survive isolation (AC8 runs under both). Auth that lives only in a settings file (`apiKeyHelper`, auth variables in the `env` block) would be lost, so such a call is **not isolated** (AC6). Forwarding those through `--settings` is a separate follow-up, not this lot.
4. **Off first, on later by a separate decision.** The default is `inherit`; the lot does not change it.

## Problem (measured on dd6c89f)

No backend isolates setting sources: `grep setting_sources|--setting-sources|strict-mcp` finds nothing in the engine. agent-sdk builds `ClaudeAgentOptions` in `agent_sdk._tool_options` (l.334) and `option_kwargs` (l.465); with `setting_sources=None` the SDK passes no flag and the CLI loads user, project and local settings. claude-subprocess builds argv in `_invoke_subprocess` (l.2208: `--allowed-tools`, `--tools`). The only production call with a patterned entry is the validation gate, `phase_5_implement.py:7033`, `allowed_tools=["Read","Grep","Glob","Bash(graphify-shim.sh:*)"]`. Settings carry `permissions.allow` rules and MCP allows that widen it; the details are in the step-0 comment on #105.

## Design

**Key.** `restricted_roles.setting_sources` in `config/models.json` (the file `config_provider.models_config_path()` names, read best-effort like `claude.effort`): `"inherit"` (default) or `"isolated"`. The org config is not visible at the chokepoint, which is why the models config is used.

**Decision**, `_setting_sources_isolation(...)` called from `_dispatch_backend` after every refusal check and immediately before the `optional` kwargs are assembled (so a refused dispatch emits no decision event); event `step_name` is the dispatch's own `step_name` argument; result goes into the `optional` kwargs as `isolate_setting_sources=True` **only when isolating**. Steps, in this order; the first that applies decides:

1. **No patterned entry** in `allowed_tools` (`None`, `[]`, or every entry a plain tool name; a patterned entry is one with `(` in it, the same test `available_tools` uses): `inherit`. Nothing is read, nothing is emitted.
2. Read the value. Missing file, unreadable or malformed file, missing key, `"inherit"`: `inherit`, no event (best-effort, as for the other keys of this file). Any other value (another string, a non-string): `inherit` and **one** event `setting_sources_config_error` `{step_name, value, expected: ["inherit","isolated"]}` (`value` is `repr`, cut at 40 characters).
3. `"isolated"` and the resolved backend lacks the capability `setting_sources_isolation`: `inherit` and one event `setting_sources_isolation_skipped` `{backend, step_name, reason: "backend_unsupported", keys: []}`.
4. `"isolated"` and the settings carry auth: `inherit` and one `setting_sources_isolation_skipped` with `reason: "auth_in_settings"` and `keys` = the **names** found (see below), never a value.
5. Otherwise: isolate, and emit one `setting_sources_isolated` `{backend, step_name}`.

**Auth probe**, `_settings_auth_keys(root)`, a small separate helper in `llm_subprocess.py`, **only** detects presence: it returns a list of `"<source>:<key>"` strings (empty list = clean), in the file order below, and never returns or logs a value. Files, in order: user `<CLAUDE_CONFIG_DIR or ~/.claude>/settings.json`, project `<root>/.claude/settings.json`, local `<root>/.claude/settings.local.json`; `root` is `extra_data["workspace_root"]` or the current directory. The probe reads under `.claude`, a path segment the core-boundary lint forbids as a literal in a core module; the probe carries the `# core-boundary: allow <reason>` pragma (precedent `verification_registry.py:57`). A missing file is clean. A file that exists but cannot be read, parsed or is not a JSON object counts as **found** (fail-safe: do not isolate), reported as `"<source>:<unreadable>"`. Keys are reported as `"<source>:<key>"` (`source` is `user`, `project` or `local`). Closed list:
- top-level: `apiKeyHelper`, `awsAuthRefresh`, `awsCredentialExport`, `gcpAuthRefresh`;
- inside `env` (a non-object `env` is ignored): `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_BEARER_TOKEN_BEDROCK`, `AWS_PROFILE`, `GOOGLE_APPLICATION_CREDENTIALS`.

**Applying it.**
- **agent-sdk** (`agent_sdk_backend(..., isolate_setting_sources: bool = False)`): when true, `option_kwargs` also carries `setting_sources=[]` and `strict_mcp_config=True`. Both are native SDK options (0.2.120), so the real transport emits `--setting-sources=` and `--strict-mcp-config`. When false nothing is added: the options are exactly as today. The capability is registered only when the installed `ClaudeAgentOptions` has both fields, so an older SDK degrades through step 3, never through the existing "cannot build restricted options" error. *Refinement to the issue text:* the issue lists `strict_mcp_config` only for the subprocess; it is set on agent-sdk too so both backends isolate the same way.
- **claude-subprocess** (`_invoke_subprocess(..., isolate_setting_sources: bool = False)`): when true, `["--setting-sources", "", "--strict-mcp-config"]` is appended right after the `--allowed-tools` / `--tools` pair; the pair itself is unchanged. When false the argv is exactly as today. The capability is declared in `_BACKEND_CAPABILITIES`.
- **Other backends** never get the keyword (it is passed only to backends declaring the capability), so strict-signature backends and test doubles stay compatible.

**What each source carries, after `setting_sources=[]` / `--setting-sources ""`** (from the step-0 comment): subscription login, process-environment credentials, the model (always passed explicitly), the tool list and approvals of the call: kept. Settings-file `apiKeyHelper` and auth `env`: dropped, hence step 4. Hooks, `permissions.*`, CLAUDE.md / memory: dropped; the call gets its instructions from the prompt. MCP servers: dropped by `strict_mcp_config`.

## Acceptance criteria

Real paths only. The subprocess ACs run `invoke_llm_subprocess(backend="claude-subprocess")` and read the argv `subprocess.Popen` receives; the agent-sdk ACs run `invoke_llm_subprocess(backend="agent-sdk")` with only `claude_agent_sdk.query` replaced, and read the argv the REAL SDK transport builds from the options the backend passed; events are read from a real `EventLog`; the config is a real `models.json` under a temporary hal root, the settings files are real files under a temporary home and project.

| AC | Case | Expectation |
|---|---|---|
| AC1 | agent-sdk, patterned list, `isolated` | argv carries `--setting-sources=` and `--strict-mcp-config`; `--tools Read,Grep,Glob,Bash`, `--allowedTools ...Bash(graphify-shim.sh:*)` and `--permission-mode dontAsk` unchanged; options object has `setting_sources == []` |
| AC2 | subprocess, patterned list, `isolated` | argv equals today's argv for that list with `--setting-sources`, `""`, `--strict-mcp-config` appended after `--tools Read,Grep,Glob,Bash`; nothing else changed |
| AC3 | both backends, patterned list, key unset / `"inherit"` / models file missing or malformed | argv (and, for agent-sdk, argv from the real transport) **equal literal pins** of today's output (below); no event |
| AC4 | both backends, `["Read"]`, `isolated` | same literal pins as today; no event, no config read needed |
| AC5 | patterned list, value `"bogus"` and a non-string value, both backends | exactly one `setting_sources_config_error` per call; argv equals the AC3 pins |
| AC6 | patterned list, `isolated`, each of: `apiKeyHelper` in user settings; `ANTHROPIC_API_KEY` in project `env`; an unparsable local file; a settings file that is a JSON list | not isolated (AC3 pins); one `setting_sources_isolation_skipped`, `reason: "auth_in_settings"`, `keys` names only; the secret value written in the file appears nowhere in the events or the argv; a file with only `permissions.allow` and a non-auth `env` key does **not** block isolation |
| AC7 | patterned list, `isolated`, backend registered without the capability | dispatched without the keyword; one `setting_sources_isolation_skipped`, `reason: "backend_unsupported"` |
| AC8 | AC1 and AC2 under three environments: none, `ANTHROPIC_API_KEY` set, `CLAUDE_CODE_OAUTH_TOKEN` set | isolation applied in each; the environment does not alter the argv or the events |
| AC9 | the agent-sdk to claude-subprocess hang fallback, patterned list, `isolated` | the fallback dispatch carries `--setting-sources ""` too |
| AC10 | `isolate_setting_sources` reaches only a backend that declares the capability | a strict-signature registered backend without it is called with no new keyword under every config value |
| AC11 | the probe never returns values | `_settings_auth_keys` on a file with a secret value returns names only |

**Literal pins of today's output** (measured on dd6c89f, model `claude-3-haiku-20240307`, `idle_timeout_sec=0` for the subprocess; the SDK argv is built with `cli_path` set to a non-existent binary, as in bd#82 AC1):
- subprocess, patterned: `["claude","-p","--model","claude-3-haiku-20240307","--output-format","stream-json","--verbose","--forward-subagent-text","--allowed-tools","Read Grep Glob Bash(graphify-shim.sh:*)","--tools","Read,Grep,Glob,Bash"]`
- subprocess, `["Read"]`: the same prefix, then `"--allowed-tools","Read","--tools","Read"`
- agent-sdk, patterned: `['/nonexistent/claude','--output-format','stream-json','--verbose','--system-prompt','','--tools','Read,Grep,Glob,Bash','--allowedTools','Read,Grep,Glob,Bash(graphify-shim.sh:*)','--model','opus','--permission-mode','dontAsk','--input-format','stream-json']`
- agent-sdk, `["Read"]`: `--tools Read`, `--allowedTools Read`, otherwise the same.

## Sibling audit (§1a)

Tests that pin the argv or the options of a restricted call (`test_bd82_tool_restriction.py` AC1, AC6, AC6b, AC6c; `test_llm_subprocess_allowed_tools.py`; `test_bd101_fresh_default.py`; `test_gh1169_agent_sdk_hang_recovery.py`, which drives the fallback) stay green because every default path is byte-identical. Tests that compare a backend's capability set (`test_register_backend_A60F1FE3.py`, `test_gh1082_backend_registry_isolation.py`, `test_bd10_l3_authorship.py`) are checked for equality on the two registered sets; any that pins the exact `claude-subprocess` or `agent-sdk` set is migrated in the same PR and listed in it. Fake backends with strict signatures are untouched (AC10).

## Test migration

`test_register_backend_A60F1FE3.py` (l.436-442) pins the exact capability set of `claude-subprocess`. This PR updates that literal by adding `setting_sources_isolation` (UPDATED, not weakened: the check stays an exact frozenset equality; bd#167 precedent). It is the only exact-equality pin on the shipped capability sets, and the only existing test file the PR edits. `test_core_boundary.py` is a sibling run for the pragma.

## Docs

`docs/configuration.md`, section "Model pinning (engine)": one paragraph after the existing models.json keys describing `restricted_roles.setting_sources` (`inherit` default, `isolated`; applies only to a call whose tool list has a patterned entry; not isolated when settings carry auth, with the events named; temporary switch, retire-by 2026-11-10).

## Known limits

- A call whose settings carry auth is not isolated until the follow-up forwards auth through `--settings`.
- Managed (policy) settings always apply; `--setting-sources` does not touch them.
- Isolation relies on a CLI that knows `--setting-sources` and `--strict-mcp-config` (the CLI bundled with SDK 0.2.120 does).
- claude-subprocess starts the CLI without `cwd=`, so it runs in the process directory, not in `workspace_root`; the probe reads the project and local settings under `workspace_root` when given. A mismatch can only make the CLI fail on missing auth, never widen the call.
- Isolation also drops the operator's hooks and `permissions.deny` for that call.
- The auth key list is closed: other variables (`ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_FOUNDRY_API_KEY`, proxy and TLS variables) are not detected; a miss fails loudly and never widens the call.
- The fallback re-decides, so a fallback dispatch can emit a second decision event.

## Kill switch

`restricted_roles.setting_sources`: `inherit` (default) or `isolated`; unset, and every other value, runs as `inherit`. Removing the switch after the flip decision removes the branch; nothing else depends on it.
