# bd#105: validation gate r2 (spec r2 + RED, last round)

Audited: `docs/decisions/2026-10-11-bd105-restricted-setting-sources.md` (revised), `engine_py/tests/test_bd105_restricted_setting_sources.py` (49 tests; refreshed preflight facts: 0 pass / 49 fail on dd6c89f, all 17 listed siblings pass, `test_core_boundary.py` 14/14 now among them).
Re-read for r2: `llm_subprocess.py` L1690-1819 (`_dispatch_backend`, refusal order and `optional` assembly), `core_boundary.py` L125-140 (`_PRAGMA_MARKER`, reason required), `verification_registry.py:57` (pragma precedent), `test_register_backend_A60F1FE3.py` L427-442, `docs/configuration.md` headings, every test that indexes `_BACKEND_CAPABILITIES` / `_DEFAULT_BACKEND_CAPABILITIES`.
Not read: GitHub issue #105 itself (no shell). The issue-side check is against the issue text and maintainer decision in the gate brief.

## Step 1: spec-internal consistency (literal-token drift)

- Event names, reasons, `"<source>:<unreadable>"`, sources, capability `setting_sources_isolation`, keyword `isolate_setting_sources`, helpers `_setting_sources_isolation` / `_settings_auth_keys`, key `restricted_roles.setting_sources`, values, `expected`, `value` repr: unchanged from r1 and still identical in spec and RED. Consistent.
- Literal pins (`_SUB_PATTERNED`, `_SUB_READ`, `_SDK_PATTERNED`, `_SDK_READ`, `_SUB_ISOLATION_TAIL`, `_SDK_ISOLATION_TOKENS`): character-identical to the spec, unchanged since r1.
- New text in r2: pragma literal `# core-boundary: allow <reason>` matches `core_boundary._PRAGMA_MARKER` and the reason requirement (L139-140); precedent `verification_registry.py:57` exists and has that form. Docs section "Model pinning (engine)" exists in `docs/configuration.md` (L146). `test_register_backend_A60F1FE3.py` l.436-442 matches the tree (exact frozenset at L436-439). The `step_name` rule ("the dispatch's own `step_name` argument") matches the RED's `_STEP` pin against the run-context decoy `"s"`.
- Header still reads `Status: r1, TIER3` although the document is the r2 revision. Cosmetic drift, MINOR-A.
- Issue vs spec: unchanged scope. Isolated only for patterned lists; agent-sdk `setting_sources=[]` + `strict_mcp_config=True` (maintainer decision), subprocess `--setting-sources "" --strict-mcp-config`; auth in settings skips isolation with names only; read/parse failure counts as found; default `inherit`; other value one config-error event + inherit. Out-of-scope items untouched. No drift.

## Step 1.5: rule-overlap simulation

Decision order unchanged (steps 1-5, first match decides). The r1 table still holds row for row. The only row that failed r1 was `test_ac10_strict_signature_backend_never_gets_new_keyword[value-isolated]`; re-simulated below for all five parameters of the revised test against a correct GREEN (strict fake, capabilities `{"tool_allowlist"}` only, patterned list, so the bd#82 tool refusal does not fire and step 1 never matches).

| param | first leg: first matching step | first-leg asserts | contrast leg (`isolated`) | contrast asserts | result |
|---|---|---|---|---|---|
| `isolated` | 3: skip `backend_unsupported`, keyword not sent | `skipped1` len 1, reason ok, `errors1 == []`, no `isolated` | 3 again: second skip | `len(skipped2) == 1 + 1`, last reason `backend_unsupported`, `calls == 2` | PASS |
| `inherit` | 2: silent | `_decision_events == []`, no `isolated` | 3: one skip | `1 == 0 + 1` | PASS |
| `bogus` | 2: one `config_error`, step 3 not reached | `errors1` len 1, `skipped1 == []` | 3: one skip | `1 == 0 + 1` | PASS |
| `5` (int) | 2: one `config_error` | same as `bogus` | 3: one skip | `1 == 0 + 1` | PASS |
| `missing` | 2: silent (no models file) | `_decision_events == []` | 3: one skip | `1 == 0 + 1` | PASS |

In every leg the strict signature receives no `isolate_setting_sources` (capability absent), so the call does not raise `TypeError`; `len(calls)` is 1 then 2. MAJOR-1 is fixed, and the per-parameter first-leg asserts now also prove step order (2 before 3 for `bogus`/`5`). Today each parameter still fails at assert time for the right reason: `isolated` on `len(skipped1) == 1`, `bogus`/`5` on `len(errors1) == 1`, `inherit`/`missing` on the contrast-leg delta.

## Step 2: section 2 (design) vs section 3 (AC) cross-check

- Mapping unchanged from r1: step 1 -> AC4; step 2 silent -> AC3; step 2 error -> AC5; step 3 -> AC7, AC10; step 4 -> AC6, AC11; step 5 -> AC1, AC2, AC8; fallback re-decision -> AC9. No orphan AC, no unmapped terminal branch.
- New design text (placement "after every refusal check and immediately before the `optional` kwargs are assembled") is consistent with the code: all pre-dispatch refusals (model resolution L1695, opus floor L1717-1725, tool refusal L1726-1731, effort config L1732-1738, effort not applied L1742-1748, injections L1749-1751) precede L1755. Two refusals (`_pin_mismatch_refusal` L1809, `_capability_escape_refusal` L1814) run after the handler call and cannot precede the decision; "every refusal check" literally includes them. "Immediately before the `optional` kwargs" disambiguates, so no GREEN is misled; wording advisory, MINOR-B.
- The new Known-limits lines (cwd vs `workspace_root`, hooks/`permissions.deny` dropped, closed auth list, fallback second event) are statements of limits, not branches; none needs an AC.

## Step 3: RED adequacy

- Count: 49 (AC10-strict still 5 parameters); facts 0 pass / 49 fail, none at collection.
- MAJOR-1 fix verified above. The rest of the file is unchanged from r1 (AC1-AC9, AC10-cap, AC11 bodies identical), so the r1 correct-GREEN walk, vacuity check and wrong-implementation probes still apply.
- New over-pinning check on the revised test: the first-leg asserts pin only what spec steps 2-3 prescribe (count and reason; `errors1 == []` for `isolated` is step-order, not an extra requirement). The delta form no longer counts across legs. No new over-pinning.
- Passable by a correct GREEN: every test, all parameters.

## Step 4: reachability (section 1y)

Unchanged from r1: Point `_setting_sources_isolation(...)` in `_dispatch_backend` between L1751 and L1755 plus the keyword in `optional`; `_invoke_subprocess` tail after the tool pair; `agent_sdk_backend` `option_kwargs`. Hosts `_dispatch_backend`, `_invoke_subprocess`, `agent_sdk_backend`, `_settings_auth_keys`. Test paths through `invoke_llm_subprocess` (and the L2133 fallback for AC9; direct call for AC11). Reachable.

## Sibling audit (section 1a), r2 deltas

1. Test migration section: names `test_register_backend_A60F1FE3.py` l.436-442, prescribes adding `setting_sources_isolation` with exact equality kept (bd#167 precedent, the in-file comment style at L427-435 supports it), and adds the file to both `sibling_tests` and `paths`. Re-grep of every `_BACKEND_CAPABILITIES[...]` / `_DEFAULT_BACKEND_CAPABILITIES[...]` use in tests: all other `claude-subprocess` / `agent-sdk` checks are membership; `test_bd83_openai_compat.py` L814-818 compares before/after registration of a third backend (not a literal), unaffected; no test pins the exact `agent-sdk` set, so the conditional agent-sdk capability breaks nothing. The claim "the only exact-equality pin on the shipped capability sets" holds. MINOR-1 resolved.
2. Core boundary: pragma prescribed with the correct literal; `test_core_boundary.py` in `sibling_tests` (14/14 green at RED). No test counts or ratchets pragmas (grep of `core-boundary: allow` in core-boundary tests: none). GREEN must put the pragma with a reason on every physical line that carries a `.claude` / `~/.claude` literal (a formatter split would need it on each line); the lint enforces this, so it self-checks. MINOR-2 resolved.
3. `CHANGELOG.md` in `paths`: file exists; no lint pins its content for this lot. `docs/configuration.md`: section named. Fine.
4. Strict-signature fakes and event-counting siblings: unaffected (r1 items 3-4 unchanged).

## MINOR follow-up (r1 MINOR-1..7)

| r1 | status | where |
|---|---|---|
| MINOR-1 capability-set migration | addressed | "Test migration"; frontmatter `sibling_tests`, `paths` |
| MINOR-2 core-boundary pragma | addressed | Auth probe paragraph; `test_core_boundary.py` sibling |
| MINOR-3 cwd vs `workspace_root` | addressed | Auth probe (`root` = `workspace_root` or cwd; the "directory the backend runs in" wording removed) + Known limits line 4 |
| MINOR-4 placement / no event on refusal | addressed | Decision paragraph (wording nit, MINOR-B) |
| MINOR-5 `step_name` source | addressed | Decision paragraph |
| MINOR-6 hooks/deny + closed auth list | addressed | Known limits lines 5-6 (list not extended, stated as closed; acceptable) |
| MINOR-7 docs row | addressed | "Docs" section |

## Security reasoning

Unchanged from r1; the revision only adds limits text. The added Known-limits statements are accurate: under isolation the CLI loads no project settings, so a probe-root vs cwd mismatch can only fail on missing auth; an undetected auth variable fails loudly; neither widens the declared list.

## Adversarial edges (not covered by the section 3 AC table)

1. Step 3 vs step 4 overlap: `isolated`, backend without the capability, auth present -> must be `backend_unsupported`, `keys: []`, no settings read. Still untested.
2. Older SDK: `ClaudeAgentOptions` lacking `setting_sources` or `strict_mcp_config` -> `register()` omits the capability, degrade via step 3. Still untested.
3. No read at step 1: a plain list with an unreadable models file must stay silent and unread. Still untested.
4. Probe error classes: a settings path that is a directory or mode 000 must count as found; `restricted_roles` as a non-object is unspecified (spec step 2 "malformed" reasonably covers it as silent inherit).
5. Value hygiene beyond events: no `caplog` assertion that the probe never logs parsed settings or exception text.
6. Multi-file order: the spec pins user, project, local; AC11 compares a sorted list, so an unordered result is not caught.
7. Refused dispatch + `isolated`: a dispatch refused by the tool refusal or opus floor must emit no decision event; now pinned in the spec text but not tested.
8. `run_ctx` None: the keyword must still be applied with no event emitted.

None of these is a defect in the RED; all are advisory hardening.

## Findings

1. MINOR-A: the spec header still says `Status: r1`; bump to r2 when the spec is frozen.
2. MINOR-B: "after every refusal check" should read "after every pre-dispatch refusal check" (`_pin_mismatch_refusal` and `_capability_escape_refusal` run after the handler); "immediately before the `optional` kwargs" already fixes the location, so GREEN is not misled.
3. Advisory: adversarial edges 1, 3 and 7 are cheap to add as GREEN-side regression tests if the implementer wants extra cover.

MAJOR-1 is fixed: each of the five parameters of `test_ac10_strict_signature_backend_never_gets_new_keyword` passes a correct GREEN step by step and fails today at assert time. All seven r1 MINORs are addressed in the spec text. The revision added no token drift, no new over-pinning and no test a correct GREEN cannot pass; the test migration section and the new sibling/paths entries are accurate against the tree. No MAJOR remains.

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

VERDICT: APPROVED

VERDICT: PASS

<!-- verdict-anchor
spec: docs/decisions/2026-10-11-bd105-restricted-setting-sources.md sha256:a1a3b4bd50193761138bdcdc59ec8201bad8b3215b616d5ade4113a6e9d3cdbe
red: engine_py/tests/test_bd105_restricted_setting_sources.py sha256:c166d5599dfd7ca0eda473e5671a7e25a53987829c8b86002ba956bd662e8008
-->
