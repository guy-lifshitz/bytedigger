# bd#167 — cost ledger: price cache tokens, record billing mode per LLM call

**Gap.** `_derive_cost_usd` prices only `in`/`out`; cache-read and cache-write
(≈88% of HAL spend, hal-v2#2208) are ignored. No call says whether it was paid
by an API key or a subscription, so bd's per-run cost cannot sit next to HAL's
ledger in the A/B. Backends that are not claude-subprocess / agent-sdk /
in-session emit no cost observation at all.

**Chokepoint.** `llm_subprocess._dispatch_backend` — every backend (in-session
included, it is in `_BACKENDS`) returns through it. The new observation is
emitted there, once per dispatch, and nowhere else. The per-backend events
(`subprocess_exited`, `runner_result_consumed`) and the existing flat rollup
totals are unchanged (back-compat).

Out of scope: `engine.py`, `phases/*.md` (blocked by #89), the HAL-side wiring
of `HAL_AB_LEDGER_CMD`.

## §1. Pure module `bytedigger_engine/lib/llm_cost.py` (stdlib only, never raises)

- `BILLING_MODES = ("metered", "subscription", "unknown")`.
- `normalize_usage(data) -> dict | None`. Reads `data["usage"]` (a dict) when
  present, else the flat keys of `data`. Accepted keys:
  `input_tokens`|`tokens_in`, `output_tokens`|`tokens_out`,
  `cache_read_input_tokens`|`cache_read_tokens`,
  `cache_creation_input_tokens`|`cache_write_tokens`, and the 1h part
  `cache_creation.ephemeral_1h_input_tokens`|`cache_write_1h_tokens`.
  Returns `{"tokens_in","tokens_out","cache_read_tokens","cache_write_tokens",
  "cache_write_1h_tokens"}`; a count that is not an int/float (bool excluded),
  negative or non-finite is `None`. The 1h part is clamped to the cache-write
  total. Returns `None` when ALL five are `None` (= "no usage reported").
- `price_usage(usage, model, rates_table) -> float | None`. `rates_table` is
  `{alias: {"in","out","cache_read"?,"cache_write_5m"?,"cache_write_1h"?}}`
  per MTok (`"cache_write"` accepted as an alias of `cache_write_5m`). Model
  lookup = same rule as `_price_for_model` (lowercase exact alias, then family
  substring). Cost = in·in + out·out + read·cache_read + (write − write_1h)·5m
  + write_1h·1h, / 1e6. `None` when: usage is `None`; model unknown; or a token
  class with a NONZERO count has no numeric rate in the entry. **Never 0 for
  unknown; no guessed multipliers** (provider-agnostic: rates come only from
  config).
- `cost_kind(billing_mode) -> "metered" | "notional" | "unknown"`
  (subscription ⇒ notional).
- `billing_mode_from_auth(api_key_source, env) -> str` — for the Claude CLI /
  agent-sdk adapters only (never called by the chokepoint). Ladder:
  `api_key_source` reported by the runtime (`"none"` ⇒ subscription; any other
  non-empty string ⇒ metered) → env (`ANTHROPIC_API_KEY` non-empty, or
  `CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX` = "1" ⇒ metered;
  `CLAUDE_CODE_OAUTH_TOKEN` non-empty ⇒ subscription) → `"unknown"`.
- `divergence(derived, reported, threshold_pct) -> dict | None` — when both are
  numbers and `|d − r| > threshold_pct/100 · max(d, r)` and `|d − r| > 1e-6`,
  returns `{"derived_cost_usd","reported_cost_usd","delta_usd","threshold_pct"}`.

## §2. Pricing config

`llm_subprocess._load_pricing_rates()` reads the same file as
`_load_pricing_table` (`claude.pricing`) and keeps the optional cache rates.
`_load_pricing_table` / `_price_for_model` / `_derive_cost_usd` keep their
signatures and behaviour (existing tests unchanged). Load failure ⇒ `{}` +
the existing warning ⇒ every price is `None`.

## §3. The observation — `llm_cost_observed`, emitted in `_dispatch_backend`

After the backend returns (after `_emit_attestation`, before the refusals),
through `_emit_safe`, only with a run context. A failure inside the cost step is
caught and logged; it never changes the StepResult. Payload:

`backend, model` (result `observed_model` or dispatched model), `billing_mode`,
`cost_kind`, `usage_reported` (bool), the five token fields (or `None`),
`derived_cost_usd`, `reported_cost_usd` (`data["cost_usd"]` when numeric),
`cost_usd` (derived when not `None`, else reported, else `None`), `cost_source`
(`"derived"|"reported"|None`), `phase`, `step_name`, `cycle`.

- `billing_mode` = `data["billing_mode"]` when it is one of `BILLING_MODES`;
  else the registry capability `billing:<mode>`; else `"unknown"`. An invalid
  value in data is not trusted (⇒ falls through).
- `"billing_mode"` and `"usage"` join `RESERVED_OBSERVATION_FIELDS` (bd#145):
  a caller's `extra_data` cannot forge them.
- `usage_reported` false ⇒ costs `None` + one `logger.warning` naming the
  backend. Error results (`data` None) are observed the same way.
- Divergence (§1) ⇒ an extra `llm_cost_divergence` event with the dict plus
  `backend, model, step_name`. Threshold: env `HAL_COST_DIVERGENCE_PCT` via
  `config_provider.env_opt` (float > 0), default 10; malformed ⇒ default. The
  observation still records BOTH values (no silent pick).

## §4. Backend adapters (each puts `billing_mode` and, where it has it, `usage`
into `result.data` on success)

| backend | billing_mode | usage source |
|---|---|---|
| claude-subprocess | `billing_mode_from_auth(init-event apiKeySource, child env)` | last result event's `usage` dict, raw (keeps `cache_creation`) |
| agent-sdk | `billing_mode_from_auth(init SystemMessage apiKeySource, env)` | `ResultMessage.usage` raw |
| anthropic-api | `"metered"` (x-api-key) | response `usage` |
| pydantic-anthropic | `"subscription"` (OAuth) | RunUsage → `input/output_tokens`, `cache_read_tokens`, `cache_write_tokens` when present |
| pydantic-openai | `"metered"` | same RunUsage reader |
| claude-in-session | not reported ⇒ registry/`unknown` | runner-result `tokens_in/out` |

Registry: `anthropic-api` and `pydantic-openai` also declare `billing:metered`,
`pydantic-anthropic` declares `billing:subscription`.

## §5. Rollup + A/B export (`lib/cost_rollup.py`)

- `compute_cost_rollup` adds `by_billing_mode` built from `llm_cost_observed`
  only: `{mode: {calls, tokens_in, tokens_out, cache_read_tokens,
  cache_write_tokens, cache_write_1h_tokens, cost_usd, unpriced_calls,
  cost_kind}}` and `cost_divergences` (count of `llm_cost_divergence`).
  `unpriced_calls` = observations with `cost_usd` `None`. Flat fields unchanged.
- `export_ab_ledger(events_path, run_id=None) -> dict` — the document shape
  `ab-bench.ts` reads from its ledger command: `{"schema":1, "marker",
  "totals": {"usd","unpriced","input","output","cache_read","cache_write",
  "cache_write_1h","records"}, "by_billing_mode": {...}}`. `marker`: `"OK"` if
  ≥1 record, `"EMPTY_CORPUS"` if the file is missing/empty,
  `"NO_RECORDS_FOR_RUN"` otherwise. `run_id=None` ⇒ every run in the file.
- CLI `python -m bytedigger_engine.lib.cost_rollup --events P [--run-id R] --json`
  prints that document; unknown args (the harness passes `--branch`, `--since`,
  …) are ignored; exit 0 for OK, 3 otherwise.

## §6. Acceptance criteria (tests in `tests/test_bd167_cost_cache_billing.py`)

- **AC1** `price_usage` prices read, write-5m and write-1h separately (one
  numeric case per class + a mixed case); unknown model ⇒ `None`; nonzero cache
  tokens with no cache rate ⇒ `None`, zero cache tokens with no cache rate ⇒
  priced; 1h clamped to the write total; `normalize_usage` accepts both key
  dialects and returns `None` for no usage.
- **AC2** One `llm_cost_observed` per dispatch through
  `invoke_llm_subprocess` with a registered fake backend, for: a metered
  backend (`billing_mode` metered, `cost_kind` metered), a subscription backend
  (notional), a backend reporting no usage at all (`usage_reported` false, all
  costs `None`, a warning logged), a backend returning an invalid
  `billing_mode` (⇒ `unknown`), `extra_data={"billing_mode": ...}` cannot forge
  it.
- **AC2b** `billing_mode_from_auth` ladder: reported source wins over env;
  env-only both ways; nothing ⇒ `unknown`.
- **AC3** Reported cost diverging beyond the threshold ⇒ one
  `llm_cost_divergence`; within ⇒ none; env threshold honoured; malformed env ⇒
  default; both values kept in the observation.
- **AC4** Rollup `by_billing_mode` totals per mode + `unpriced_calls`; flat
  totals byte-identical to before for an event file without the new events;
  `export_ab_ledger` markers (OK / EMPTY_CORPUS / NO_RECORDS_FOR_RUN), the
  `totals.usd` / `totals.unpriced` keys `ab-bench.ts` reads; CLI exit codes and
  tolerance of unknown args.
- **AC5 (degrade, provider down)** pricing file missing ⇒ observation emitted,
  costs `None`, no raise; event log whose `append` raises ⇒ the StepResult is
  returned unchanged; an exception inside the cost step ⇒ StepResult unchanged.
- **AC6 (adapters)** anthropic-api (urlopen faked) puts `billing_mode`
  metered + `usage`; claude-subprocess `_tokens_and_cost_from_events` keeps the
  raw usage with `cache_creation`, and its billing comes from the init event's
  `apiKeySource` (subscription case `"none"` and metered case) — at least one
  subscription-mode and one API-key-mode backend exercised end to end.

## §7. Amendment after gate 1 (FAIL, 7 MAJOR) — overrides §1-§6 where they differ

- **A1 (F1, bd#145).** `billing_mode` and `usage` join `RESERVED_OBSERVATION_FIELDS`;
  this amends bd#145 AC1. `tests/test_bd145_reserved_observation_fields.py`
  `NAMES` gains both names, with sentinels so its forge/leak loops stay meaningful.
- **A2 (F3).** The chokepoint wraps the whole cost step in `try/except Exception`;
  on failure it logs `logger.warning(COST_STEP_FAILED_LOG_PREFIX + repr(exc))`,
  `COST_STEP_FAILED_LOG_PREFIX = "llm cost observation failed: "` (module constant
  in `llm_subprocess`), emits nothing and returns the StepResult unchanged. Test
  seam: monkeypatch `llm_cost.normalize_usage` (or `_load_pricing_rates`) to raise.
- **A3 (F4, agent-sdk).** The agent-sdk stream loop collects
  `SystemMessage(subtype="init").data["apiKeySource"]` (`data` is a dict) and
  passes it to `billing_mode_from_auth`; `ResultMessage.usage` goes to
  `data["usage"]` raw. AC6 adds agent-sdk via a fake `claude_agent_sdk` (as in
  test_GH901): `apiKeySource "none"` ⇒ subscription, `"ANTHROPIC_API_KEY"` ⇒ metered.
- **A4 (F5, pydantic).** pydantic-ai `RunUsage.input_tokens` INCLUDES cache read and
  write. The shared reader returns `tokens_in = max(0, input_tokens −
  cache_read_tokens − cache_write_tokens)`, `cache_read_tokens`,
  `cache_write_tokens` (missing attrs ⇒ 0 for cache, None for in/out). Pure unit test
  on an attrs object. Registry: pydantic-openai `billing:metered`, pydantic-anthropic
  `billing:subscription`, pinned by a test with a fake `pydantic_ai` module.
- **A5 (F6, F13, reported cost).** `reported_cost_usd` is read from `data["cost_usd"]`
  ONLY when the backend declares capability `reports_cost` (declarative, no name
  branch). `claude-subprocess` and `agent-sdk` declare it; `claude-subprocess` puts
  the last result event's `total_cost_usd` into success `data["cost_usd"]`. In-session
  does not declare it (its `cost_usd` is bd's own derivation). A reported value counts
  only if it is a finite non-negative int/float (bool excluded).
- **A6 (F9).** claude-subprocess raw usage = `_find_last_result_event(events)["usage"]`
  into `data["usage"]`; `_tokens_and_cost_from_events` and the `subprocess_exited`
  payload are UNCHANGED.
- **A7 (F10, F11, F12, F14, edges 5/7/8/9).** Refusal before dispatch ⇒ no
  `llm_cost_observed` (pinned). Two or more `billing:*` capabilities ⇒ `unknown`.
  `delta_usd = reported − derived` (signed). In `price_usage` a `None` token class
  counts as 0 (all-None usage is already `None` from `normalize_usage`), and the 1h
  part is clamped to the write total there too (missing write total ⇒ write total =
  1h). `HAL_COST_DIVERGENCE_PCT` must be finite and > 0, else default 10.
  `billing_mode_from_auth`: `CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX` = "1"
  ⇒ metered FIRST, then the reported source, then the rest of the env ladder.
- **A8 (edges 2/3/4/12, A/B poisoning).** Observations with `usage_reported` false
  are counted as `no_usage_calls`, NOT `unpriced_calls`; `unpriced_calls` (and
  `totals.unpriced`) = usage reported but `cost_usd` None (mirrors token-cost.ts
  "tokens and model without a price"). The no-usage warning is logged once per
  backend per process.
- **A9 (F8, F18).** `export_ab_ledger(events_path, run_id=None, since=None,
  until=None)`; rows filtered by the event `ts` (ISO, inclusive) when given. CLI
  honours `--run-id`, `--since`, `--until`, ignores the rest. `totals` gains
  `metered_usd` and `notional_usd`; `totals.usd` = their sum = metered-equivalent
  cost (documented). The A/B wrapper must pass `--events <file>` (and `--run-id`).
- **A10 (deployment).** Cache rates are a config prerequisite: until
  `claude.pricing.<alias>` carries `cache_read`/`cache_write_5m`/`cache_write_1h`,
  every call with cache tokens is unpriced (honest `None`, by design). Multi-model CLI
  sessions may emit divergence events (total includes helper models) — that is the
  signal working, not noise to suppress.
- **A11 (RED form, F2/F7/F15/F16).** No assert in the autouse fixture; an `_lc()`
  accessor in tests that use `llm_cost`; every test must FAIL (not ERROR) on base.
  Flat-totals test compares old vs new output, not literals. Env clearing covers
  `HAL_`, `BD_`, `BYTEDIGGER_` prefixes. No vacuous asserts.

## §8. Amendment after gate 2 (FAIL, 3 MAJOR) — overrides §1-§7 where they differ

- **B1 (N1).** The no-usage warning text starts with
  `NO_USAGE_LOG_PREFIX = "llm cost: backend reported no usage: "` (module constant in
  `llm_subprocess`) followed by the backend name. bd#145's `_drop_warnings` and its AC9
  filter select by `startswith(RESERVED_DROP_LOG_PREFIX)` (edit authorised here).
  `reset_backends()` also clears the once-per-backend de-dup set.
- **B2 (N2, S8).** `totals` gains `unknown_usd` and `no_usage`;
  `usd = metered_usd + notional_usd + unknown_usd`.
- **B3 (N3).** Frozen expectations UPDATED, not weakened (exact equality kept):
  `test_register_backend_A60F1FE3.py` claude-subprocess capabilities gain
  `reports_cost`; `test_2A6986ED_anthropic_api_backend.py` anthropic-api capabilities
  gain `billing:metered`.
- **B4 (S1).** The shared pydantic reader is
  `pydantic_openai._usage_for_ledger(agent_result) -> {tokens_in, tokens_out,
  cache_read_tokens, cache_write_tokens}`; it keeps `_extract_usage_tokens`'s
  callable-or-property `.usage` handling; pydantic-anthropic imports it.
- **B5 (S2).** The straggler synthetic-ok `data` carries no `billing_mode`/`usage`; its
  observation is no-usage, billing from the registry or `unknown`.
- **B6 (S3).** Time filter: both sides parsed as instants (`Z` = `+00:00`; naive or
  date-only bound = UTC midnight/as-is in UTC). A row whose `ts` is missing or
  unparseable is excluded when any bound is given. An unparseable bound ⇒ CLI exit 2
  (usage error), `export_ab_ledger` raises `ValueError`.
- **B7 (S4, S6, S9).** The chokepoint calls `llm_cost.<fn>` through the module
  attribute. agent-sdk reads the init message duck-typed (`getattr(msg, "subtype",
  None) == "init"` and a dict `data`), never `claude_agent_sdk.SystemMessage`. Only
  VALID `billing:<mode>` tokens count; exactly one valid ⇒ that mode, otherwise
  `unknown`. The cost step computes everything first, then emits (A2 failure ⇒
  nothing emitted).
