---
red_tests:
  - engine_py/tests/test_bd83_openai_compat.py
sibling_tests:
  - engine_py/tests/test_bd83_openai_compat_guards.py
# reference case (opt-in, -m live_ollama): engine_py/tests/test_bd83_openai_compat_live_ollama.py
red_pins:
  - engine_py/tests/test_bd83_openai_compat_live_ollama.py
paths:
  - engine_py/bytedigger_engine/lib/reference_backends/openai_compat.py
  - engine_py/bytedigger_engine/llm_subprocess.py
  - engine_py/bytedigger_engine/run.py
  - engine_py/bytedigger_engine/workflows/phase_6_review.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - engine_py/bytedigger_engine/error_codes.py
  - engine_py/ERROR_CODES.md
  - engine_py/bytedigger_engine/ERROR_CODES.md
  - engine_py/bytedigger_engine/lib/plugins/review_schema/canonical.py
  - engine_py/bytedigger_engine/conformance/class_i_inventory.json
  - engine_py/pyproject.toml
  - docs/backends.md
  - CHANGELOG.md
---
# bd#83: OpenAI-compatible provider with a base_url, and a phase_6 review path without file tools

**Issue:** guy-lifshitz/bytedigger#83. **Class:** SYSTEMATIC. **Chokepoint:** the provider registry, `register_backend` / `_BACKENDS` in `engine_py/bytedigger_engine/llm_subprocess.py` (every role call goes through `invoke_llm_subprocess` -> `_dispatch_backend` -> `_BACKENDS[name]`).
**Status:** DRAFT r3 (r1 gate: REJECTED, 7 MAJOR + 15 MINOR; r2 gate: REJECTED, 2 MAJOR; both folded below).

## 1. Problem

The engine can run a role only on the default CLI backend or on the Azure-only `pydantic-openai`. Nothing accepts a `base_url`, so when the default provider is unavailable there is no path to a local or third-party OpenAI-compatible server (Ollama, LM Studio, vLLM, mlx). phase_6 review is also written for a worker that can use the Write tool; a text-only backend gets told to write files it cannot write.

## 2. Principles this lot follows

1. Deterministic first: URL validation, usage mapping, wall time and the capability decision are plain code. The model only produces text.
2. Provider-agnostic, degrade not crash: a refused, down or malformed provider returns an error `StepResult` with a code; it never raises out of the backend.
3. Both billing modes: the default subscription path (`claude-subprocess`) is unchanged and tested unchanged; the new backend is tested keyless (local) and with an API key (`metered`).
4. Opt-in with a kill switch: nothing is registered and nothing changes without `HAL_OPENAI_BASE_URL`.

## 3. Change

### 3.1 New backend `openai-compat` (`lib/reference_backends/openai_compat.py`, stdlib `urllib` only)

Registered as `openai-compat`, `manifest_source="api_text_response"`, capabilities `{"no_tools"}` plus `billing:metered` only when a key is configured at registration (see 3.1.4).

1. **Config, read at call time through `config_provider.env_mapping()` (the source the dispatcher uses), not `os.environ`:** `HAL_OPENAI_BASE_URL` (required), `HAL_OPENAI_API_KEY` (optional; read once per call, billing data follows the key seen at call time), `HAL_OPENAI_MODEL_<ALIAS>` (optional alias override; the alias is the requested model lowercased then uppercased; no override passes the model string through verbatim, e.g. `gemma4:26b`).
2. **Request:** `POST <base_url without trailing slash>/chat/completions`, JSON body `{"model", "messages":[{"role":"user","content":prompt}], "stream": false}`. `Authorization: Bearer <key>` only when the key is non-blank. No `Authorization` header otherwise.
3. **URL check (code, before any I/O):** the base URL must parse with scheme `http` or `https` and a non-empty host. A value that is empty after `strip()` (or unset) -> `E_LLM_BASE_URL_MISSING`, `recoverable=False`. A non-blank value that fails the scheme or host check (`file://`, `ftp://`, no host, no scheme) -> `E_LLM_BASE_URL_INVALID`, `recoverable=False`. Both codes are registered in `error_codes.py` and in both `ERROR_CODES.md` files (the `error_codes --check` generator output must stay byte-equal).
4. **Registration and kill switch:** `register()` returns `False` and registers nothing when `HAL_OPENAI_BASE_URL` is unset or blank; otherwise it registers (idempotent, `overwrite=True`) and returns `True`. The `billing:metered` capability is declared only when `HAL_OPENAI_API_KEY` is non-blank. `run.py` calls `register()` in a guarded `try/except` block like the other reference backends. With the env unset, `_KNOWN_BACKENDS` is byte-identical to today.
5. **Success data:** `raw_response` (the first choice's `message.content`, string), `response_bytes`, `command: ["openai-compat", "chat.completions", <model id>]`, `wall_ms` (int, monotonic wall time of the HTTP call), `usage: {"input_tokens": prompt_tokens, "output_tokens": completion_tokens}` copied only for counts that are non-bool ints >= 0 (a server that omits `usage` leaves it out; no guessing), `observed_model` from the response `model` when it is a non-blank string, and `billing_mode: "metered"` only when a key was sent. `extra_data` is merged first and the reserved keys above are set after it (a caller cannot shadow them), same rule as `anthropic-api`.
6. **Errors, never raised:** HTTP 408, 429 and 5xx -> `E_LLM_API_HTTP` (recoverable); every other HTTP status incl. 3xx, 401, 403, 404 -> `E_LLM_API_HTTP` (not recoverable, a retry cannot help); timeout, including a `URLError` whose reason is a timeout -> `E_LLM_TIMEOUT` (recoverable); connection refused / DNS / `URLError` / `RemoteDisconnected` / `ConnectionResetError` / other `OSError` -> `E_LLM_API_NETWORK` (recoverable); non-JSON body, no `choices`, empty or non-string content, a body larger than 16 MiB, or any other exception -> `E_LLM_API_BAD_RESPONSE` (not recoverable). The timeout is per socket operation, not a total deadline; this is stated in `docs/backends.md`. `duration_ms` is set on every result. The effective timeout follows `anthropic-api` (min of `timeout_sec` and a positive `idle_timeout_sec`).
7. **No redirects.** The request uses `urllib.request.build_opener` with a redirect handler that refuses every 3xx (stdlib urllib only; no `http.client`, no `install_opener`, so `anthropic-api` is unaffected), so `Authorization` is never sent to a second host and `ftp:`/`file:` redirect targets are never opened. A 3xx response -> `E_LLM_API_HTTP` (not recoverable).
   The `opener.open(...)` call is a class-I site: `conformance/class_i_inventory.json` gets exactly one entry for it, and the body is parsed with `json.loads(resp.read())`, not `json.load(resp)` (the lint tracks the latter), so `test_bd150_class_i_inventory::test_ac10_real_tree_passes_lint` stays green. Any `HTTPException` / `IncompleteRead` -> `E_LLM_API_NETWORK` (recoverable).
8. **Effective model.** The backend registers an `effective_model` hook returning the model that is actually sent (after the `HAL_OPENAI_MODEL_<ALIAS>` remap), as `pydantic-openai` does (`pydantic_openai.py:809`), so the hard-gate floor and attestation check the served model, not the requested Claude alias. The hook reads the env at call time. Hard gates: with no override the floor sees the requested alias; the backend is text-only and cannot satisfy tool-based gates, which is accepted and documented in `docs/backends.md`.
9. **Conformance.** The success data also sets `observed_tools: []` (a text-only call uses no tools) next to `observed_model`, so `test_bd71::test_ac7` and `conformance/bd_l3.py` need no `SILENT_BACKENDS` change.
10. Accepted and ignored kwargs (same as `anthropic-api`): `allowed_tools`, `run_ctx`, `hard_gate`, `straggler_cfg`, `stable_prefix`, `effort`, `gate_label` only echoed into data.

### 3.2 Event log carries wall time

`_cost_events` adds `wall_ms` to the `llm_cost_observed` payload only when the result data holds `wall_ms` as a non-bool int >= 0. Existing backends do not set it, so their payload is unchanged. Tokens already flow through `llm_cost.normalize_usage` (`input_tokens` / `output_tokens`).

### 3.3 phase_6 review without file tools

`phase_6_review._judge_can_write(ctx)` (new, pure): resolves the judge backend with `_resolve_backend(None, os.environ, role="judge")` and returns `False` exactly when the resolved backend's registered capabilities contain `no_tools` and not `tool_allowlist`; `True` for every other case including an unknown or unresolvable backend (so the default path is untouched). When it returns `False`, `_build_review_prompt` replaces all three file-writing instructions with the same sentence "Reply with the complete aggregated review as your final answer; do not write files.": the `_role_line` (~770), the `OUTPUT` line (~1016, an em dash in the code) and the composite framing `SINGLE_REVIEW_FRAMING_TEMPLATE` (`lib/plugins/review_schema/canonical.py:39-49`, used at `phase_6_review.py:868-875`, "Use the Write tool to write your findings to ONE file: .../role-composite.md"). The existing `SINGLE_REVIEW_FRAMING_TEMPLATE` and `PER_ROLE_SCHEMA_TEMPLATE` constants stay byte-identical (`test_bd89_p3b1_single_reviewer_only` pins their sha256, and `role-composite.md` must stay in the former); the no-tools framing is a NEW named constant in `canonical.py`, chosen only when `_judge_can_write` is `False`. The `VERDICT:` and `## Aggregated Findings` directives that sat on the OUTPUT line stay in the no-tools prompt as separate text next to the sentence. `_judge_can_write` reads the env through `config_provider.env_mapping()`. It also applies to `anthropic-api` judges (text-only too); this is intended. The fix (~2562) and satisfaction (~2925/2997) prompts do not run with `role="judge"` and are out of scope. A no-tools judge reviews from file paths only (it cannot Read); the step logs that once. The single judge call and the existing stdout fallback of `_write_review_artifact` / `_resolve_review_content` then carry the review. No Agent tool, no Write tool, no new step. When `_judge_can_write` is `True` the prompt is byte-identical to today.

### 3.4 Live test behind a marker

`engine_py/tests/test_bd83_openai_compat_live_ollama.py`, marker `live_ollama`, deselected by default. Runs one `invoke_llm_subprocess(backend="openai-compat", model="gemma4:26b", ...)` against `HAL_OPENAI_BASE_URL` (default `http://localhost:11434/v1` in the test only) and asserts `status == "ok"`, non-empty `raw_response`, `usage` tokens > 0 and `wall_ms > 0` in the `llm_cost_observed` event. Skips (not fails) when the server is unreachable. The live test is a sibling test (its result depends on the machine), not a RED test. The marker is declared in `engine_py/pyproject.toml` `[tool.pytest.ini_options]` with `addopts = -m "not live_ollama"`. If that table already exists the lines are merged; the `-m` expression must not override an existing `addopts`.

## 4. Files

- new: `engine_py/bytedigger_engine/lib/reference_backends/openai_compat.py`
- edit: `engine_py/bytedigger_engine/llm_subprocess.py` (`_cost_events` only; no `_REFERENCE_BACKEND_INSTALL_HINTS` entry, `test_gh898` pins four keys), `engine_py/bytedigger_engine/run.py` (guarded register), `engine_py/bytedigger_engine/workflows/phase_6_review.py` (3.3), `engine_py/pyproject.toml` (marker), `docs/backends.md` (table row + section), `CHANGELOG.md` (one bullet in the existing `###` block of `[Unreleased]`, bd#212 lint), `engine_py/bytedigger_engine/flags_catalog.py` (mandatory entries, kind `str`, for `HAL_OPENAI_BASE_URL` and `HAL_OPENAI_API_KEY`; `HAL_OPENAI_MODEL_<ALIAS>` documented as a prefix in `docs/backends.md`), `engine_py/bytedigger_engine/error_codes.py`, `engine_py/ERROR_CODES.md`, `engine_py/bytedigger_engine/ERROR_CODES.md` (two new codes), `engine_py/bytedigger_engine/lib/plugins/review_schema/canonical.py` (3.3, new constant only), `engine_py/bytedigger_engine/conformance/class_i_inventory.json` (one entry, 3.1.7)
- tests: `engine_py/tests/test_bd83_openai_compat.py` (RED, hermetic, local `http.server` on 127.0.0.1:0, no external network), `engine_py/tests/test_bd83_openai_compat_guards.py` (guards that pass before and after), `engine_py/tests/test_bd83_openai_compat_live_ollama.py` (marker, sibling)
- Nothing else.

## 5. Acceptance criteria

- AC1: with `HAL_OPENAI_BASE_URL` unset or blank, `register()` is `False`, `"openai-compat"` is not in `_KNOWN_BACKENDS`, and `invoke_llm_subprocess(backend="openai-compat")` gives `E_LLM_BACKEND_UNKNOWN`.
- AC2: with it set, `register()` is `True` and the backend dispatches through `invoke_llm_subprocess`; manifest source is `api_text_response`; capabilities contain `no_tools`.
- AC3: request goes to `<base>/chat/completions` (trailing slash stripped), body has `model`, one user message with the prompt, `stream: false`.
- AC4: keyless: no `Authorization` header, no `billing_mode` in data, no `billing:metered` capability. With a key: `Authorization: Bearer <key>`, `billing_mode == "metered"`, capability `billing:metered`.
- AC5: model alias override `HAL_OPENAI_MODEL_<ALIAS>` applies; an unaliased model (`gemma4:26b`) goes through verbatim.
- AC6: success data has `raw_response`, `response_bytes`, `command`, `wall_ms` (int >= 0), `usage.input_tokens` / `usage.output_tokens` from `prompt_tokens` / `completion_tokens`; missing or invalid `usage` leaves `usage` out; `extra_data` cannot shadow reserved keys.
- AC7: the `llm_cost_observed` event for an `openai-compat` call carries `tokens_in`, `tokens_out` and `wall_ms`; for `claude-subprocess` and `anthropic-api` the payload has no `wall_ms` key.
- AC8: base URL `file:///x`, `ftp://h`, `http://` (no host), `localhost:11434` (no scheme) -> `E_LLM_BASE_URL_INVALID`, no socket opened. Unset at call time -> `E_LLM_BASE_URL_MISSING`.
- AC9: server returns 500 -> `E_LLM_API_HTTP`; connection refused -> `E_LLM_API_NETWORK`; slow server beyond timeout -> `E_LLM_TIMEOUT`; body `not json`, `{}`, `{"choices":[]}`, `{"choices":[{"message":{"content":""}}]}`, `{"choices":[{"message":{"content":null}}]}` -> `E_LLM_API_BAD_RESPONSE`. None raises; all set `duration_ms`.
  - Post-gate amendment (after gate r3): the engine passes `idle_timeout_sec` only when a caller sets it explicitly (default `None`; phase_6 leaves it off), so the backend does not declare `progress_since` and an explicit `idle_timeout_sec > 0` fails loud with `E_LLM_WATCHDOG_UNSUPPORTED` from the dispatcher; the test pins that. The refused-connection test connects to a port that was bound, read and closed (refused on macOS and Linux), not to a bound-but-not-listening socket.
- AC10: `_judge_can_write` is `False` for a backend registered with `{"no_tools"}`, `True` for `claude-subprocess`, `True` for a backend with `{"no_tools", "tool_allowlist"}`, `True` for an unknown backend name.
- AC11: `_build_review_prompt` with a no-tools judge backend contains neither `Use the Write tool` nor the `role-composite.md` write instruction (all three instructions of 3.3 are gone) and does contain the literal sentence of 3.3; with the default backend the prompt is byte-identical to the pre-change prompt (golden text pinned in the guards file, taken from `main`).
- AC12: end to end, a no-tools judge returning a review as `raw_response` makes `_write_review_artifact` write the review doc from stdout and continue to the next step (no `E_MISSING_PREV_DATA`, no empty-review fallback).
- AC13: the default path is untouched: with the env unset, the registry snapshot after `import run` equals the snapshot taken before this change (the set of names `{claude-subprocess, claude-in-session, + the existing reference backends that import}`), and `reset_backends()` removes `openai-compat`.
- AC14 (marker `live_ollama`, deselected by default): real call to local Ollama `gemma4:26b` returns ok, non-empty text, tokens > 0, `wall_ms` > 0 in the event log. The plain `pytest` run reports it deselected, not skipped-failed.
- AC15: Both billing modes run through the same chokepoint in one test: `claude-subprocess` stubbed as subscription and `openai-compat` keyed as metered produce `llm_cost_observed` events with `billing_mode` `subscription` and `metered` respectively.
- AC16: with `HAL_OPENAI_MODEL_<ALIAS>` set, the `effective_model` hook returns the remapped model and the request body carries it; without an override it returns the requested model.
- AC17: a 302 to a second local server is not followed; the result is `E_LLM_API_HTTP` and the second server sees no request (so no `Authorization`). A 401 is not recoverable, a 429 and a 503 are recoverable.
- AC18: a connection reset mid-response and a `URLError` wrapping a timeout map to `E_LLM_API_NETWORK` and `E_LLM_TIMEOUT`; a body over 16 MiB gives `E_LLM_API_BAD_RESPONSE`. The key never appears in a result, a log record or an event.
- AC19: the two new error codes are registered and `error_codes --check` stays clean; the `observed_tools` field is present on success.

## 6. Non-goals

Agentic tools (read/grep) for the new backend; streaming; tool calling; per-role backend config (P1, already shipped as `HAL_RUNNER_BACKEND_<ROLE>`); changing `pydantic-openai`; pricing local models; the fix and satisfaction prompts; proxy handling (the backend uses urllib defaults, `docs/backends.md` says to set `NO_PROXY` for local URLs).
