<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd83-openai-compat-provider.md sha256:7ae6d73f370c25c4f97f8551bf22521590aded2b4d9ff77360fa6b8ffbd3fc03
red: engine_py/tests/test_bd83_openai_compat.py sha256:9bd38282666d4e1ced793f8df6605b91b6ebaf09f2ecfc92b5546a1f5724424c
-->
# bd#83 gate r3 - spec + RED audit (OpenAI-compatible provider, phase_6 review without file tools)

Anchor: not hand-written. The anchor tool stamps the sha256 of the spec and RED files below this line
after this file is written.

Audited (read-only):
- Spec: docs/decisions/2026-10-10-bd83-openai-compat-provider.md (DRAFT r3)
- RED: engine_py/tests/test_bd83_openai_compat.py (102 tests, 0 pass / 102 fail today per the facts file)
- Siblings: engine_py/tests/test_bd83_openai_compat_guards.py (12 guards, 12 pass today by design),
  engine_py/tests/test_bd83_openai_compat_live_ollama.py (marker `live_ollama`, pinned)
- Previous gates: ...-gate-r1.md (REJECTED), ...-gate-r2.md (REJECTED, N1/N2 MAJOR)
- Checked against the code: conformance/class_i_lint.py (`_callee` :91-113, `_SCAN_DIRS` :43,
  `VALID_CLASSES` :45, `_entry_problems` :215-231, `check` :234-251), conformance/class_i_inventory.json
  (reference_backends entries :80-83), conformance/tree_scan_lint.py, test_bd150_class_i_inventory.py:618,
  test_bd89_p3b1b_ii_aggregation_helper.py:296-313, test_bd89_p3b1_single_reviewer_only.py:359-361,
  review_schema/canonical.py:18-49, phase_6_review.py (:116, :745-1034, :1053-1133, :1575-1607, :2269),
  llm_subprocess.py (`_resolve_backend` :658, `_capability_enforcement` :1110, `_tool_restriction_refusal`
  :1127, `_effective_model` :1268, `_cost_events` :1505, `_dispatch_backend` :1650-1747,
  `register_backend` :2792, `reset_backends` :2853), anthropic_api.py:197-310, flags_catalog.py,
  scripts/flag_owner_lint.py (`is_rollout`), test_bd71_agent_sdk_observations.py:203-233,
  test_engine_path_closure.py:426, engine_py/pyproject.toml (no `[tool.pytest.ini_options]` today),
  .github/workflows/ci.yml.

## r2 findings - resolution check (against the code)

| r2 | Sev | Status | Evidence |
|---|---|---|---|
| N1 class-I inventory | MAJOR | RESOLVED | `conformance/class_i_inventory.json` is in the frontmatter `paths` and in section 4 ("one entry, 3.1.7"). 3.1.7 names the site (`opener.open(...)`, keyed `.open` by `_callee` :109-110) and mandates `json.loads(resp.read())` (not in `_STDLIB_CALLEES`; `json.load` is). `build_opener`, `Request`, `resp.read`, `json.loads` are not inventoried callees, so a spec-literal module yields exactly one key `lib/reference_backends/openai_compat.py::<qualname>::.open#0`. Any entry with a class in `VALID_CLASSES` (no `issue` unless `I-deferred`) and a non-empty note makes `check()` clean, so test_bd150 AC10 and the test_bd89 p3b1b_ii AC12 CLI guard stay green. Class and qualname are not named (M1, advisory). |
| N2 canonical constants | MAJOR | RESOLVED | 3.3 now says `SINGLE_REVIEW_FRAMING_TEMPLATE` and `PER_ROLE_SCHEMA_TEMPLATE` stay byte-identical, cites the bd89 pin, keeps `role-composite.md` in the former, and makes the no-tools framing a NEW constant selected only when `_judge_can_write` is False. The guards file adds `test_ac11_canonical_review_constants_stay_byte_identical` with the same hex digests as test_bd89_p3b1:360-361 (`f42cf190...`, `b99ae1ec...`); it passes today (facts: 12/12). No test enumerates canonical.py's names, so adding a constant is safe. The new constant is not named (M2, advisory). |
| N3 cap semantics | MINOR | RESOLVED in RED, partial in spec | `test_ac18_cap_is_on_bytes_actually_read_at_the_16_mib_boundary` brackets the cap at +-1 KiB for `length`, `close` and `chunked` framing; a header-only cap and a 2 MiB cap are both killed. The spec text still does not say "bounded read" (M3). |
| N4 AC17 observation channel | MINOR | RESOLVED | `_serve` records every method (`_other_method` bound to GET/HEAD/PUT/DELETE/PATCH/OPTIONS); a followed 301/302 is now seen directly. |
| N5 hook call-time read | MINOR | RESOLVED | `test_ac16_hook_reads_the_env_at_call_time` sets and deletes `HAL_OPENAI_MODEL_SONNET` after `register()`. |
| N6 hard-gate consequence | MINOR | RESOLVED | 3.1.8 states the floor sees the requested alias without an override, the backend cannot satisfy tool-based gates, and this is accepted and documented in docs/backends.md. |
| N7 directives kept | MINOR | PARTIAL | 3.3 now says the `VERDICT:` and `## Aggregated Findings` directives stay "as separate text next to the sentence"; the RED assertion is still non-discriminating (`## Aggregated Findings` is always present via :1006). Advisory only. |
| N8 IncompleteRead | MINOR | RESOLVED | 3.1.7: `HTTPException` / `IncompleteRead` -> `E_LLM_API_NETWORK` (recoverable); RED row `http.client.IncompleteRead(b"ab", 10)` pins it at the opener seam. |
| N9 "role file" wording | MINOR | PARTIAL | Implicitly answered by "PER_ROLE_SCHEMA_TEMPLATE stays byte-identical"; whether the new framing embeds it is not stated (M2). |
| N10 bd71 source literals | MINOR | OPEN | 3.1.9 still does not say the keys appear as double-quoted literals (test_bd71:220). A conventional `data["observed_tools"] = []` satisfies it (M4). |
| N11 anthropic-api AC10 case | MINOR | OPEN | No RED case; 3.3 states the behaviour is intended. Advisory. |
| N12 "the step logs that once" | MINOR | OPEN | Still no AC/RED. Note the existing `role_report_missing` event (:1601) already fires once per no-tools review; the spec could name it (M5). |
| N13 inifile discovery | MINOR | OPEN | 3.4 unchanged; CI runs from `engine_py`, so it holds there. Advisory. |

## Step 1 - spec-internal consistency (literal-token drift)

- Env names, error codes, `openai-compat`, `api_text_response`, `no_tools`, `billing:metered`,
  `effective_model`, `observed_tools`, `wall_ms`, the literal sentence: identical across 3.1-3.4, the AC
  table, the RED and the guards. No drift.
- 3.3 says `_judge_can_write` resolves with `_resolve_backend(None, os.environ, role="judge")` and, later,
  that it "reads the env through `config_provider.env_mapping()`". The existing sibling call at
  phase_6_review.py:2269 uses `env_mapping()`. Both satisfy the RED (monkeypatch.setenv is seen by both);
  drift only (M6).
- 3.1.7 says "stdlib urllib only; no `http.client`" and, two sentences later, "Any `HTTPException` /
  `IncompleteRead` -> `E_LLM_API_NETWORK`". Catching those types needs `import http.client` (or the
  class objects). Read in context the ban is on making the request with `http.client`; no lint forbids the
  import (grep: no `http.client` rule in engine_py). Drift only (M6).
- 3.1.7 says the body is parsed with `json.loads(resp.read())`; 3.1.6 caps the body at 16 MiB. The
  parenthetical is about the class-I lint (`json.loads` vs `json.load`), but read literally it prescribes
  an unbounded `read()` (M3).
- RED module docstring still says "AC1-AC15". Cosmetic.

## Step 1.5 - rule-overlap simulation

URL check (3.1.3), first match wins: unset/blank -> MISSING; `file:///x`, `ftp://h` -> scheme -> INVALID;
`http://`, `https://` -> empty host -> INVALID; `localhost:11434` -> scheme `localhost` -> INVALID; `/v1` ->
empty scheme -> INVALID. Matches AC8 and the RED, no socket touched.

Transport classifier (3.1.6 + 3.1.7). Correct order: `HTTPError` -> (`TimeoutError`, `socket.timeout`) ->
`URLError` with a timeout reason -> `URLError` -> `HTTPException` (incl. `IncompleteRead`) / `OSError` ->
parse / shape / size -> catch-all.

| Case | First matching branch | Outcome | RED |
|---|---|---|---|
| 301/302/303/307/308 with the refusing handler | `redirect_request` -> None -> default handler raises `HTTPError` | HTTP, not recoverable | AC17 (301/302/307) |
| 401/403/404 | HTTPError | HTTP, not recoverable | AC17 |
| 408/429/500/503 | HTTPError | HTTP, recoverable | AC9/AC17 |
| `getresponse()` blocks past the timeout | raw `TimeoutError` (urllib wraps only `h.request`) | TIMEOUT | AC9 (both timeouts) |
| `URLError(socket.timeout)` / `URLError(TimeoutError)` | URLError + timeout reason | TIMEOUT | AC18 |
| `URLError("dns failure")`, refused port | URLError | NETWORK | AC9/AC18 |
| `RemoteDisconnected` | `ConnectionResetError` and `HTTPException` (both NETWORK) | NETWORK | AC18 |
| `IncompleteRead` | HTTPException | NETWORK | AC18 |
| `OSError("other")` | OSError | NETWORK | AC18 |
| `RuntimeError` | catch-all | BAD_RESPONSE | AC18 |
| RST mid-body (Content-Length) | `ConnectionResetError` from `read()` | NETWORK | AC18 `mid` |
| body CAP-1 KiB (length / close / chunked) | parse ok | ok | AC18 boundary |
| body CAP+1 KiB (length / close / chunked) | size branch | BAD_RESPONSE | AC18 boundary |
| shape errors (`not json`, `{}`, `[]` choices, `""`/`null`/`42` content, `[1,2,3]`) | parse/shape | BAD_RESPONSE | AC9 |

urllib framing check (chunked / close vs `http.client`): the test server answers HTTP/1.0;
`HTTPResponse.begin()` sets `chunked` from `Transfer-Encoding` regardless of version, `read()` and
`read(amt)` both de-chunk, and with no `Content-Length` and `Connection: close` `read()` runs to EOF. A
bounded `read(CAP + 1)` and an unbounded `read()` followed by a length check both pass all six boundary
cases; a `Content-Length`-only check fails `close` and `chunked`. Discriminating as intended.

`_judge_can_write`: caps of the resolved judge backend; `no_tools` and not `tool_allowlist` -> False, else
True. `{"no_tools"}` -> False; `claude-subprocess` (no `no_tools`) -> True; `{"no_tools",
"tool_allowlist"}` -> True; unknown name -> empty caps -> True; empty caps -> True; real openai-compat
`{"no_tools"}` -> False. All AC10 rows match.

Dispatcher with a no-tools judge (AC12): `_invoke_review_llm` passes `allowed_tools=[Read, Grep, Glob,
Write]`, not a hard gate. `_capability_enforcement` -> `"no-tools"`, so `_tool_restriction_refusal`
returns None. `stable_prefix` is forwarded only when non-empty and 3.1.10 accepts it; `effort` and
`fresh_session` are not forwarded (no `effort*` / `warm_resume` cap). The call reaches the backend.

## Step 2 - section 3 (change) vs section 5 (AC) cross-check

Change -> AC: 3.1.1-3.1.6 -> AC1-AC9, AC17, AC18. 3.1.7 -> AC17, AC18 (`IncompleteRead`), class-I entry
-> sibling test_bd150 AC10 (no bd83 AC; it is a guard GREEN must keep green, acceptable). 3.1.8 -> AC16.
3.1.9 -> AC19. 3.1.10 `gate_label` -> `test_gate_label_is_echoed_into_data`. 3.2 -> AC7, AC15 + guards.
3.3 -> AC10-AC12 + the canonical sha guard + the default-prompt literal guard. 3.4 -> AC14.
Uncovered section 3 branches: "the step logs that once" (3.3, advisory, M5).

AC -> producing change: every AC has a producing path in section 3; the two r2 producer gaps (AC11 via
canonical.py, AC17 via the opener) now have an in-scope, lint-clean producer.

## Step 3 - RED adequacy

- Collect time: module-level imports all exist today; the new module is imported lazily in `_module()`.
  Facts: 102 collected, 102 failing at call time, guards 12/12 green.
- No test mocks the unit under test. The AC18 parametrisation patches `urllib.request.OpenerDirector.open`,
  a stdlib seam that 3.1.7 now mandates (`build_opener` returns an `OpenerDirector`), so it is a spec
  decision, not a test accident.
- New r3 cases are discriminating: the boundary test kills a header-only cap and a far-off cap; the
  all-method recorder kills a following opener directly; the hook call-time test kills a hook that freezes
  the alias table at `register()`; the `IncompleteRead` row kills a catch-all BAD_RESPONSE.
- Weak spots (advisory): the AC11 "directives kept" assertions remain non-discriminating (N7); 303 and 308
  are not in the AC17 parametrisation (adversarial edge 1); the AC14 config test uses
  `importorskip("tomllib")`, so it cannot fail on Python < 3.11.

## Step 4 - reachability

| Side effect | Point (prod) | Host | Test path |
|---|---|---|---|
| Registration + hook | new `openai_compat.register()` -> `register_backend(..., effective_model=)` (:2792) | `run.py` guarded block | AC2, AC13 (`_reload_run`), AC16 |
| HTTP call, data, errors | new impl via `build_opener(...).open` | `_dispatch_backend` -> `_BACKENDS[name]` (:1734) | AC2-AC9, AC17-AC19 through `invoke_llm_subprocess` |
| Remap event | `_dispatch_backend` :1674-1681 fed by the hook | `invoke_llm_subprocess` | AC16 chokepoint test |
| `wall_ms` in the cost event | `_cost_events` :1505-1540 | `_observe_cost` from `_dispatch_backend` | AC7, AC15, guards |
| Prompt swap | `_build_review_prompt` :770 / :868-875 / :1015-1020 + new canonical constant | gated by new `_judge_can_write` | AC11, AC12, guards |
| Review doc from stdout | `_aggregate_review_findings` missing-composite branch (:1600-1607) -> `_write_review_artifact` stdout path | phase_6 step chain | AC12 |
| Class-I key | one `.open` call in openai_compat.py | `class_i_lint.check` | test_bd150 AC10, test_bd89 p3b1b_ii AC12 |
| Error codes | `error_codes.ERROR_CODES` + both md | `render_markdown` / `check` | AC19 |

Sibling surfaces re-checked: tree_scan_lint (no glob/rglob/iterdir/git in the new code; phase_6 edits add
no inventoried call, so no ordinal shift); flags_catalog (kind `str` is not a rollout kind for
`flag_owner_lint.is_rollout`; `module` must point at an existing file, test_bd89_p1:239-247 - it will after
GREEN); test_engine_path_closure AC5c (new module needs `from __future__ import annotations` if it uses
`X | Y` annotations - a GREEN-time hygiene point, not a spec gap); test_gh898 (no hint entry);
test_bd71 AC7 (M4); CI receipt step (`^8 passed` prefix survives a `deselected` suffix).

## Adversarial edges (not covered by the AC table)

1. 303 redirect: a handler that overrides only `http_error_301`/`_302` lets stock urllib follow a POST 303
   as GET, and `redirect_request` copies every non-content header, `Authorization` included, to the
   second host. AC17 parametrises 301/302/307 only (307 POST is refused by stock urllib anyway).
2. FIN without RST mid-body under `Content-Length`: `HTTPResponse.read(amt)` returns the short body
   silently (no `IncompleteRead`), so a bounded-read GREEN reports BAD_RESPONSE (not recoverable) while an
   unbounded-read GREEN reports NETWORK (recoverable). The `mid` RED relies on the RST surfacing as
   `ConnectionResetError`.
3. Unbounded body: a GREEN that follows `json.loads(resp.read())` literally holds an arbitrarily large
   chunked or length-less body in memory before the 16 MiB check.
4. Non-numeric or malformed port (`http://127.0.0.1:abc/v1`): passes the scheme/host check; `http.client`
   raises `InvalidURL` (an `HTTPException`) and 3.1.7 maps it to NETWORK, recoverable, so a config error is
   retried. A malformed IPv6 literal (`http://[::1/v1`) makes `urlparse` raise `ValueError`; if the check
   is not inside the never-raise guard, the backend raises.
5. Env alias fallback (carried from r2): `env_mapping()` serves `HAL_OPENAI_*` from `BD_`/`BYTEDIGGER_`
   aliases; the RED fixture deletes only `HAL_OPENAI_*`, so a developer shell can turn "unset" cases into
   "set". Not a CI risk.

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
- AC11: PASS
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS
- AC16: PASS
- AC17: PASS
- AC18: PASS
- AC19: PASS

## BLOCKER

None.

## MAJOR

None. Both r2 MAJORs are resolved against the code (see the table above).

## MINOR (advisory, do not block GREEN)

M1. Class-I entry: name the class (`not-prompt`, with a note such as "HTTP response of the model call,
    returned as raw_response; consumers carry it as class M") and the qualname of the function holding
    `opener.open`, so GREEN does not invent them. Any valid class passes the lint today.
M2. Name the new canonical constant and state its content: `SINGLE_REVIEW_FRAMING_TEMPLATE` with only the
    "Use the Write tool to write your findings to ONE file: ... role-composite.md" lines replaced by the
    3.3 sentence, keeping `{per_role_schema}` and `{dispatch_table}`. Say whether "Required last line of
    the role file" in `PER_ROLE_SCHEMA_TEMPLATE` is deliberately left (r2 N9).
M3. Say the body is read with a hard bound (`read(16 * 1024 * 1024 + 1)`; more than 16,777,216 bytes ->
    BAD_RESPONSE) and that `json.loads(resp.read())` in 3.1.7 only means "json.loads, not json.load".
    The RED accepts both readings; the bound matters for memory (edge 3) and for edge 2.
M4. 3.1.9: state that `"observed_tools"` and `"observed_model"` appear as double-quoted string keys in
    openai_compat.py (test_bd71:220 greps the source; `dict(observed_tools=[])` would mark the backend
    silent and fail that sibling).
M5. 3.3 "the step logs that once": name the record (the existing `role_report_missing` event at
    phase_6_review.py:1601 already fires once per no-tools review) or drop the sentence.
M6. Literal drift: 3.3 `os.environ` vs `env_mapping()` (use `env_mapping()`, as :2269 does); 3.1.7 "no
    `http.client`" vs catching `HTTPException` (say: no request via `http.client`; importing it for the
    exception types is fine).
M7. Add 303 (and 308) to the AC17 parametrisation (edge 1); validate the port in 3.1.3 (access `.port`
    inside the check; a `ValueError` -> `E_LLM_BASE_URL_INVALID`) so edge 4 is a non-recoverable config
    error and never raises.
M8. Carried advisories: r2 N7 (assert the exact OUTPUT replacement line), N11 (anthropic-api AC10 case),
    N13 (deselection holds only when pytest discovers `engine_py/pyproject.toml`).

Simulation of a word-for-word GREEN: a module that validates the URL, builds the request, calls
`build_opener(<HTTPRedirectHandler subclass whose redirect_request returns None>).open(req,
timeout=min(timeout_sec, positive idle))`, reads and size-checks the body, parses with `json.loads`,
classifies exceptions in the Step 1.5 order, merges `extra_data` first and sets the reserved keys after,
registers with `{"no_tools"}` (+ `billing:metered` when keyed) and an env-reading `effective_model` hook;
plus the `_cost_events` `wall_ms` branch, `_judge_can_write` and the new framing constant selected in
`_build_review_prompt`, the guarded `run.py` call, both error codes regenerated into both md files, one
class-I entry and the pytest marker table. Against this, every RED test passes (including the six cap
boundary cases and the all-method redirect check), and all 12 guards stay green (default prompt
byte-identical, canonical sha256 unchanged, `wall_ms` only for valid ints, tool-capable judge unchanged).
No sibling regression found: class-I lint clean with the one entry, tree-scan lint untouched, test_bd89
pins untouched, flag-owner lint unaffected (kind `str`), test_gh898 untouched.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd83-openai-compat-provider.md sha256:7ae6d73f370c25c4f97f8551bf22521590aded2b4d9ff77360fa6b8ffbd3fc03
red: engine_py/tests/test_bd83_openai_compat.py sha256:9bd38282666d4e1ced793f8df6605b91b6ebaf09f2ecfc92b5546a1f5724424c
-->
