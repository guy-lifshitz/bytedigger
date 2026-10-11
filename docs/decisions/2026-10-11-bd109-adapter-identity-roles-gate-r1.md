# bd#109: validation gate r1 (spec r1 + RED)

Audited: `docs/decisions/2026-10-11-bd109-adapter-identity-roles.md`, `engine_py/tests/test_bd109_adapter_identity_roles.py` (20 tests; preflight facts: 0 pass / 20 fail on 43e8556, all 5 siblings pass).
Code read: `engine.py` L354 (emit), L1457-1463 (`_resolve_adapter_identity`), `logger` L65; `llm_subprocess.py` L155-160 (`_DEFAULT_BACKEND`, `_BACKEND_ENV_VAR`, `_ROLES`), L690-718 (`_resolve_backend`), L2000-2031 (role check, resolve, `runner_backend_resolved` emit); `config_provider._AliasEnviron`; `EMISSIONS_SPEC.md` section 4 (L161-228); `test_bd82_role_backend_effort.py` spy pattern (L43-71); format reference: the bd277 s9 gate r1.

## Step 1: spec-internal consistency (literal-token drift)

- `_resolve_adapter_identity`, `engine.py:354` / `:1457` / l.1462, `_ROLES` l.160, role check l.2000, resolve l.2007: all match the code. Consistent.
- Env names: `HAL_RUNNER_BACKEND`, `_JUDGE`, `_WORKER`, `_AUDITOR` identical in spec and RED constants; code builds `f"{_BACKEND_ENV_VAR}_{role.upper()}"`, so `_AUDITOR` resolves for the patched role.
- Event names `run_identity`, `runner_backend_resolved`; source set `env-role`, `env`, `default` (code returns exactly these plus `kwarg`, excluded by design item 3). AC-E2e / AC-E2b / AC-E2d naming consistent between spec, RED and the existing EMISSIONS_SPEC (E2d is indeed taken, new entry E2e).
- Minor wording drift: the header table says "an environment read and a pure lookup, twice per `execute()`", while Design item 3 says one snapshot per call and the function is called once per `execute()` (one read, three lookups). Cosmetic, MINOR-1.
- Spec Design 5 says the warning names "the role and the exception type"; AC8 says "one warning naming `judge`"; RED additionally asserts `RuntimeError` in the message. Consistent with Design 5.

## Step 1.5: rule-overlap simulation

`_resolve_backend(None, env, role=r)` branches in order: kwarg (None, skipped) -> role var (stripped, non-empty -> `env-role`) -> global (stripped, non-empty -> `env`) -> default.

| case | role var | global | first match | expected | RED |
|---|---|---|---|---|---|
| reference | judge, worker set | unset | role | env-role each; top level default | AC1, AC6, AC9, AC12 |
| global only | unset | set | global | env each | AC2, AC7 global-only |
| nothing | unset | unset | default | default each | AC3, AC10 (worker) |
| mixed | judge set | set | judge role, worker global | AC4a |
| worker only | worker set | unset | worker role, judge default | AC4b |
| blank judge, padded worker | "   " / "  x  " | set | judge falls to global, worker stripped env-role | AC5 |
| patched role "auditor" | set | unset | role | AC10 |

Top-level pair (role None) skips the role branch in every row: the pair is the same as today. Every row yields the spec outcome at the first matching branch.

## Step 2: section 2 (Design) vs section 3 (AC) cross-check

- Item 1 (top level unchanged, key order) -> AC1, AC6. Item 2 (one entry per `_ROLES`, call-time, order) -> AC10, AC1. Item 3 (same seam, snapshot, source set) -> AC7, AC2-AC5. Item 4 (validity not checked, no fallback rebind) -> AC1 (`claude-in-session`/`anthropic-api` unregistered-or-not recorded as named), AC2. Item 5 (degrade) -> AC8a, AC8b. Item 6 (no side effect) -> AC11. Item 7 (annotation/docstring) -> not testable, fine. EMISSIONS_SPEC change -> AC13. Live per execute -> AC12. Credentials -> AC9. Siblings -> AC14 (preflight).
- The one terminal/error path (per-role failure) has a producing Design item (5) and AC8a/8b; no orphan AC, no un-ACed terminal branch other than the top-level non-guarded path, which the spec declares unchanged (adversarial 1).

## Step 3: RED adequacy

- Count: AC1 1, AC2 1, AC3 1, AC4 2, AC5 1, AC6 5 (parametrized), AC7 2 (parametrized), AC8 2, AC9 1, AC10 1, AC11 1, AC12 1, AC13 1 = 20. Matches facts.
- Assert time, not collect time: imports are module-level but only of existing modules; facts confirm 20 failures with `roles` missing / no AC-E2e entry, not collection errors.
- Not vacuous: the UUT (`_resolve_adapter_identity`) is never patched or called. AC8 wraps `llm_subprocess._resolve_backend` (a dependency) with a delegating wrapper; AC10 replaces the `_ROLES` constant. Real `execute()`, real `EventLog`, read back from the file.
- Correct-GREEN walk:
  - AC1/2/3/4/5: pass with `{role: _resolve_backend(None, env, role=role)}` for `role in llm_subprocess._ROLES`; `claude-in-session` etc. are only recorded, not validated. The conftest pin is overridden by `_isolated_env` (deletes HAL_/BD_/BYTEDIGGER_ selectors) and `_apply_env`.
  - AC6: key list `["backend","source","roles"]` requires `roles` appended last; `_resolve_backend(None, dict)` compared as tuple equals the pair; payload keys unchanged. Satisfiable.
  - AC7: mirrors the bd82 spy pattern (`manifest_source="git_diff"`, `tool_allowlist`), real `invoke_llm_subprocess(role=...)` under a run context with an event log; `emit_resolver_resolved` stubbed by the autouse fixture. The call path yields `runner_backend_resolved` with the role (l.2026-2031); a name not in the default set is not "default", so no `default-fallback` rebind. Context cleared before the `execute()`. Satisfiable; global-only case gives `env` for both.
  - AC8a: engine calls `llm_subprocess._resolve_backend` by attribute (import is `from bytedigger_engine import llm_subprocess`, l.41), so the setattr reaches it. Top level has role None and delegates. Exactly one WARNING containing "judge" is produced by a GREEN that logs once per failed role with role and `type(exc).__name__` (or `%r`); `logger` exists (l.65) and propagates to caplog. A GREEN catching `Exception` passes; a narrower except fails (correctly, spec says "if it raises"). Satisfiable.
  - AC8b: same, `roles == {}` required, so the key must exist on degrade. Satisfiable.
  - AC9: identical identity under three credential environments; satisfiable by any GREEN that does not read credentials.
  - AC10: `_ROLES` read through the module attribute at call time; `_AUDITOR` var reaches `env.get("HAL_RUNNER_BACKEND_AUDITOR")` through the alias mapping (HAL_ key exact). Order checked via `list(roles)`. Second leg: `("worker",)` -> only worker, default pair. Satisfiable.
  - AC11: event-type sequence compared between a no-role-var run and a reference run on separate logs; the only difference is payload content. Environment dict equality holds for a read-only function. Satisfiable.
  - AC12: two `execute()` on one engine with different `run_id`; second payload read by occurrence 1 from the file. Satisfiable (AC-E2d already requires per-execute emission).
  - AC13: `_entries` parser: top-level `- **AC-` lines or `## ` end an entry; sub-bullets (indented) belong to the entry; first `## 5.` is line 230 (`## 5. Emission 3`; the other `## 5.x` headings come later, no earlier inline `## 5.`). Current E2d starts with the asserted literal and contains no "roles". Satisfiable by an E2e entry placed after E2d, and an E2b sentence containing `AC-E2e`.
- Stub / wrong-implementation probes caught: constant or hard-coded role list (AC10, AC1); roles built from top level only (AC1, AC4); own resolution rule with different stripping (AC5, AC7); a private re-implementation that does not go through `llm_subprocess._resolve_backend` (AC8a: no judge omission); unguarded loop (AC8a/b); guard that drops all roles (AC8a); missing `roles` on total failure (AC8b); `roles` inserted before the pair (AC6); cached identity (AC12); emitting `runner_backend_resolved` from the builder (AC11); credential branching (AC9); import-time `_ROLES` copy (AC10); AC-E2d overwritten / missing pointer (AC13).
- Not caught (advisory): see adversarial edges.

## Step 4: reachability (section 1y)

- AC1-AC6, AC9-AC12. Point: the rewritten body of `_resolve_adapter_identity` (`engine.py:1457`). Host: `_resolve_adapter_identity`, called from `WorkflowEngine.execute` at `engine.py:354`. Test path: `_engine(log).execute("wf", ctx, run_id=...)` -> `run_identity` event read from the log file. Reachable.
- AC7. Point: same builder (identity side) plus the unchanged `invoke_llm_subprocess` resolve at `llm_subprocess.py:2007-2031` (call side). Test path: `invoke_llm_subprocess(role=...)` with a spy backend and `execute()`. Reachable.
- AC8. Point: the new per-role try/except and `logger.warning` inside `_resolve_adapter_identity`. Test path: `execute()` with a patched `llm_subprocess._resolve_backend`. Reachable.
- AC13. Point: new E2e text in `EMISSIONS_SPEC.md`. Host: the document. Test path: file read in the test. Reachable.

## The seven points of the request

1. AC7: correct GREEN passes (above); not self-satisfying, since the call-side event is produced by the real, unmodified call path and compared to the identity side.
2. AC8 exactly-one-warning: satisfiable (one `logger.warning` per failing role); a double-logging GREEN fails, a silent skip fails. Message must contain the role name and the exception type name; that is within Design 5.
3. AC10 `_ROLES` patched: the patch targets the module attribute the spec says to read at call time; correct GREEN passes, hard-coded or import-bound list fails.
4. AC11: satisfiable; separate logs avoid ordering coupling; env equality is a real read-only check.
5. AC12: satisfiable and non-vacuous (different env between two executes with distinct expected values).
6. Wrong/stub implementations cannot pass (probe list above).
7. Sibling list: `grep` set in the spec's §1a audit is consistent with the five siblings; preflight shows them passing on unmodified code.

## Adversarial edges (not covered by the section 3 AC table)

1. **Top-level resolution raising**: Design 5 says it is not guarded and keeps today's behaviour; no test pins that the top-level failure still propagates (a GREEN wrapping the whole function in a try/except and returning a partial identity would pass every test). Spec-declared out of scope; advisory.
2. **Env alias seam**: all tests set `HAL_RUNNER_BACKEND*` names. A GREEN reading `os.environ` (or `dict(os.environ)`) instead of `config_provider.env_mapping()` passes everything, although Design 3 requires the one snapshot from `env_mapping()`. A `BD_RUNNER_BACKEND_JUDGE` / `BYTEDIGGER_` alias case would pin it.
3. **Role name with a non-identifier character or odd case** in a patched `_ROLES` (e.g. `"Auditor"`, `"a-b"`): upper-casing / variable composition is the resolver's, the identity would key by the role as given; untested.
4. **`_ROLES = ()`** (empty): `roles == {}` without any failure; not tested (AC8b covers empty roles only via failure).
5. **`_ROLES` as a list or mutated in place between executes**: call-time read of the same object; AC10 replaces the attribute only.
6. **Resolver returning a non-pair** for a role (wrapper returning a 3-tuple or None): spec only guards "raises"; behaviour unspecified, untested.
7. **Inner key order** `{backend, source}` per role entry is not pinned (mapping equality ignores order); only the outer list is checked.
8. **E2e heading shape**: AC13 requires the exact literal `- **AC-E2e**` at line start (as E2d's `- **AC-E2d**`); the spec says "a new **AC-E2e**" without pinning the bullet form, so a GREEN writing `- **AC-E2e.**` or a sub-bullet would fail a correct-in-substance edit. MINOR; the test failure message is explicit.
9. **CHANGELOG** line is in the spec's scope but has no AC; advisory (convention, not behaviour).

## Findings

1. MINOR-1: header "twice per `execute()`" vs Design 3 one snapshot per call (cosmetic).
2. MINOR-2: env alias seam unpinned (adversarial 2): a GREEN bypassing `env_mapping()` would pass. Suggest a `BD_RUNNER_BACKEND_JUDGE` case; advisory.
3. MINOR-3: AC13 bullet form (adversarial 8) not pinned in the spec; GREEN should copy the `- **AC-E2d** ` form.
4. MINOR-4: top-level-unguarded behaviour has no test (adversarial 1); documented as unchanged.

No MAJOR. Every AC has at least one failing assertion at assert time, no test is vacuous, none can fail a correct GREEN per Design 1-7.

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

VERDICT: PASS
