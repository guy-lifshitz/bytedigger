# bd#141 item 4(d): every phase declares its role template on the `injections` channel (R3.2)

**Status:** r2 (gate r1 REJECTED: 3 MAJOR + 3 MINOR + 1 NIT, see `2026-10-02-bd141-p4d-gate-r1.md`; r2 closes all of them, disposition in §8. r1.1 = RED-author findings: 22nd dispatch via `executor.submit`, `_maybe_role_template` kept) · **Tier:** 2/3 boundary (one shared helper plus mechanical call-site edits in 10 workflow modules, Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `llm_subprocess._injection_refusal` (already the single R3.2 check, called by every
dispatch). This lot adds no new check. It adds the missing **declarations**: one shared declare helper in
`workflows/phase_workflows_common.py`, used by every builder that prepends the role template and by every invoke step that
dispatches that builder's prompt.
**Side of the seam (decision 2026-07-26 §7.4, "injected-content attribution"):** engine. HAL has no host-side implementation,
so nothing is thinned on the HAL side and no host-controls registry entry is needed.
**Source:** bd#141 item 4(d); follow-up bd#147; bd#10 (chokepoint, AC-I5 on phase_2), bd#119 (role-template reader), bd#73 (R3.2 verdict), PR #146 finding.

## §1 Problem (measured on `083a158`)

1. `invoke_llm_subprocess(injections=...)` exists and `_injection_refusal` (`llm_subprocess.py:1014`) fails closed with
   `E_INJECT_UNATTRIBUTED` on a block with no `source_id` or whose `content` is not in the prompt. The attestation event records
   each declared block as `{source_id, sha256}` (`:1212`).
2. Only `phase_2_explore` declares anything (`_role_template_injections`, `phase_2_explore.py:179`, pinned by bd#10 AC-I5).
3. The org-configured role template (`org_config["role_template_path"]`, an external file) is prepended by **17 other producers** (16 `_maybe_role_template` call sites plus spec_lite's free-rewrite branch, which embeds phase_1's)
   through `_maybe_role_template(ctx) -> str` (`phase_workflows_common.py:583`). That helper discards `source_id`, so none of
   these 16 prompts declares the block. On these steps R3.2 is `not-checked`, and the attestation does not say which file shaped
   the prompt.
4. **Builder → dispatch map (static baseline, every `_maybe_role_template` caller and every `invoke_llm_subprocess(` under `workflows/`):**

   | Builder (`role = _maybe_role_template`) | Dispatch(es) |
   |---|---|
   | `phase_1_discovery.py:352` (`_build_prompt`, returns `str`; step wrapper `:430`) | `:470` invoke_discovery_llm |
   | `phase_45_spec_lite.py:421-449` `_maybe_rewrite_simple_spec_prompt`, free-rewrite branch: embeds phase_1 `_build_prompt` (17th producer, gate r1 F1). Its restricted-writer branch `:400-418` carries no role | `phase_45_spec_lite.py:481` maybe_invoke_spec_rewrite |
   | `phase_3_clarify.py:183` | `:244` |
   | `phase_4_architect.py:217` (`_build_prompt`, returns `str`; step wrapper `:280`) | `:319` |
   | `phase_45_spec_lite.py:586` | `:630` |
   | `phase_45_spec.py:1153` | `:1357` |
   | `phase_45_spec.py:4060` | `:4150`, `:4201` (`**invoke_kwargs`) |
   | `phase_5_implement.py:1329` | `:1553` |
   | `phase_5_implement.py:6411` | `:6664` |
   | `phase_5_implement.py:7284` | `:7556`, retry `:7655` |
   | `phase_5_integrity.py:351` | `:424` |
   | `phase_6_review.py:800` | `:1119` |
   | `phase_6_review.py:2408` | `:2550`, retry `:2676` |
   | `phase_6_review.py:2818` | `:3249`, and the pool path `:3044` (`executor.submit(..., invoke_llm_subprocess, ...)`, not a `invoke_llm_subprocess(` call, missed by the first grep) |
   | `phase_6_review.py:5464` | `:5514` |
   | `phase_6_fix_integrity.py:520` | `:600` |
   | `phase_7_synthesize.py:438` | `:576` |

5. **Verbatim baseline.** All 16 builders place the role identically: `parts.append(role.rstrip()); parts.append("")`, then at least
   one more part, joined with `"\n"`. The loader returns `content = text.rstrip() + "\n\n"` (`role_template.py:131`). So the prompt
   already contains `rt.content` byte for byte, followed by the next part. Declaring `rt.content` therefore passes the
   chokepoint's `content in prompt` check **without changing a single prompt byte** (`prompt_sha256` unchanged for every step).
   The RED file re-measures this per builder (AC3) instead of trusting this reading.

## §2 Design

### §2.1 Shared helpers (`phase_workflows_common.py`, §1aa named helpers)
- `_role_template(ctx) -> RoleTemplate | None`: `load_role_template(ctx.org_config)`. It raises `RoleTemplateError` exactly as
  today; no new error path.
- `_role_template_record(rt) -> dict | None`: `None` for `None`, else `{"source_id": rt.source_id, "content": rt.content}`. This is
  the shape phase_2 already stores under `prev.data["role_template"]`.
- `_declared_injections(data) -> tuple[InjectedBlock, ...]`: reads `data.get("role_template")` from a builder's data dict and returns
  `()` when absent/falsy or when `data` is not a dict, else one `InjectedBlock(source_id, content)`. This is phase_2's
  `_role_template_injections` body lifted into the shared module.
- `_maybe_role_template(ctx) -> str` is **kept** as the string view, re-implemented as `rt = _role_template(ctx); return rt.content if rt else ""` (one reader). Reason: bd#119's tests pin its semantics and its single definition (`test_bd119_role_template.py:282,577,593,771,829`, `test_261_PH56SPLIT_stage0_common.py:37`), and four tests monkeypatch it by name (`test_34AEB235`:674,717, `test_subagent_return_discipline_fix_DACC8E2B`:237,302, `test_phase_6_verified_only_gate_65695203`:301,380, `test_subagent_return_discipline_satisfaction_1A07C325`:361,418,462). Removing it would force ~10 sibling-test edits for no attribution gain. After this lot no builder calls it; those four patches become no-ops and still pass, because their ctx has no `role_template_path`. phase_2's `_role_template_injections(prev)` becomes
  a thin call to `_declared_injections(prev.data)`, so there is one implementation.

### §2.2 Producers (17)
**One read per build (gate r1 F1).** The record and the prompt bytes come from the same `RoleTemplate` object; no producer calls
`_role_template(ctx)` twice for one prompt.
- A builder that returns a `StepResult` replaces `role = _maybe_role_template(ctx)` with `rt = _role_template(ctx)` and
  `role = rt.content if rt else ""` (placement untouched, so prompt bytes are unchanged), and sets `"role_template": _role_template_record(rt)`.
- The two `str`-returning builders (`phase_1_discovery._build_prompt`, `phase_4_architect._build_prompt`) gain a keyword-only
  `role_template` parameter. Their callers (the step wrappers at `phase_1:430`, `phase_4:280`, and spec_lite's free-rewrite branch)
  read `rt = _role_template(ctx)` once, pass it in, and record that same `rt`. Omitting the keyword keeps today's behaviour for any other
  caller: the builder reads it itself.
- **Explicit on every branch (gate r1 F4).** Every data dict a producer returns that carries `"prompt"` sets `"role_template"`
  explicitly, to the record or to `None` on a role-less branch (restricted writer/reviewer, surgical delta, etc.). This applies even where the
  dict is built by spreading `prev.data` (`{**prev.data, ...}`), so a record from an earlier step can never leak onto a prompt that does not
  contain it.
- **No hiding (gate r1 F2).** A producer must not reach the bare-string helper through `getattr`, `globals()`, a star import or an alias.

### §2.3 Dispatches (21 sites, plus phase_2's existing one)
Each `invoke_llm_subprocess(` call under `workflows/` passes `injections=_declared_injections(<the dict that carried the prompt>)`.
The argument is a **dict** (`prev.data`, `prev_data`), never the `StepResult` (`_declared_injections(prev)` would return `()`; the
behavioural matrix AC5b turns that red). The pool path (`phase_6_review.py:3044`, `executor.submit`) passes the same tuple to every submitted call.
For `phase_45_spec.py:4150/:4201` the key goes into `invoke_kwargs`. For the two retries (`phase_5_implement.py:7655`,
`phase_6_review.py:2676`), which re-send a stored prompt, the injections come from the same dict the prompt comes from, so a retry
declares exactly what its first attempt declared. `phase_45_spec_lite.py:481` takes them from `_maybe_rewrite_simple_spec_prompt`'s data dict. A dispatch whose builder has no role template (no `role_template_path`) passes `()`, which is today's behaviour.

## §3 Acceptance criteria

- **AC1 (helpers)** `_role_template_record(None) is None`; for a `RoleTemplate(s, c)` it is `{"source_id": s, "content": c}`.
  `_declared_injections` returns `()` for `None`, a non-dict, `{}`, `{"role_template": None}`, and returns exactly
  `(InjectedBlock(s, c),)` for a record.
- **AC2 (builders use the attributable reader)** No module under `workflows/` other than `phase_workflows_common` **calls**
  `_maybe_role_template` (AST scan of `Call` nodes, including `getattr(..., "_maybe_role_template")` and `globals()[...]`). Imports and
  re-exports stay allowed: `test_261_PH56SPLIT_stage0_common.py:110-123` pins phase_5/phase_6 re-export identity, and
  `test_34AEB235_green_typecheck_gate.py:674,717` monkeypatches the name without `raising=False`. Both therefore stay green **unedited** (gate r1 F2). It stays defined in `phase_workflows_common` and equals `_role_template(ctx).content` (or `""`)
  for a configured and an unconfigured ctx.
- **AC3 (every producer records, verbatim)** For **each of the 17 producers** in §1.4 (the `str` builders through their step wrapper; spec_lite's
  free-rewrite branch at cycle 2 with a prior review that has no structured findings), called with `role_template_path` pointing at a
  temp file whose text has trailing whitespace: the returned `data["role_template"] == {"source_id": str(Path(p).expanduser()),
  "content": text.rstrip() + "\n\n"}`, and `data["role_template"]["content"] in data["prompt"]`. Without `role_template_path`:
  `data.get("role_template") is None`. A builder that cannot be driven without git or a subprocess may use the minimum real
  fixture (temp git repo, temp scratchpad). Mocking the builder itself, `_role_template` or `load_role_template` is rejected (§1l).
- **AC4 (prompt bytes unchanged)** For the AC3 builders that can also be run at `083a158` semantics, the prompt with the template equals
  the prompt that today's `role.rstrip()` + `""` placement produces. Expressed as: the prompt contains `text.rstrip() + "\n\n"` and
  does **not** contain `text.rstrip() + "\n\n\n"` (no extra separator introduced).
- **AC5 (every dispatch declares, structural)** AST scan of every module under `bytedigger_engine/workflows/`: every call to
  `invoke_llm_subprocess` passes an `injections` keyword, either directly or as a key of the dict literal / dict assignment
  expanded through `**`. The scan reports the file:line of each offender. The expected count of direct calls is pinned (21: the 20 direct calls in §1.4 plus phase_2_explore:379). The `executor.submit` pool path at `phase_6_review.py:3044` passes `injections=` among the submit keywords (separate test) so that a
  new call site, or a call hidden behind an alias, is a visible change.
- **AC5b (behavioural dispatch matrix, gate r1 F3)** For **every one of the 22 dispatches**, feed the real output of its producer (the AC3
  driver, with `role_template_path` set) into the real invoke step, spying on `invoke_llm_subprocess` at the module attribute (the AC8 pattern;
  the spy returns a canned ok StepResult). Assert the kwarg `injections == (InjectedBlock(source_id, ROLE_CONTENT),)`. The pool path runs
  `_invoke_satisfaction_llm` with `complexity="COMPLEX"`, and every submitted call is asserted. Where a dispatch is reached only after
  intermediate steps (retries, spec_lite :481), the test drives the real intermediate steps; it does not hand-build `prev`.
- **AC4b (no stale-record leak, gate r1 F4)** Run a role-less branch (`phase_45_spec_lite._build_review_prompt` at cycle 2 with structured
  findings, and spec_lite's restricted-writer branch) with a `prev.data` that already carries a `role_template` record, and assert the
  output's `role_template is None`.
- **AC6 (production side effect, §1l)** Drive the real `phase_3_clarify` workflow and the real `phase_4_architect` workflow through
  `WorkflowEngine.execute` with a recording `claude-subprocess` adapter and a fake event log, in the style of bd#10 AC-I5
  (`test_bd10_l3_authorship.py:1088`). With `role_template_path` set: `result.status == "ok"` and the single
  `model_invocation_attested` event has `injections == [{"source_id": str(Path(p).expanduser()), "sha256": sha256(text.rstrip() + "\n\n")}]`.
  `invoke_llm_subprocess`, `_injection_refusal` and the attestation are NOT mocked; only the backend binary/adapter is.
- **AC7 (no template, no declaration)** Same as AC6 without `role_template_path`: the attestation's `injections == []`, status ok.
- **AC8 (retries declare the same)** For `phase_5_implement`'s GREEN retry and `phase_6_review`'s fix retry, the injections passed on the
  retry dispatch equal those of the first dispatch (spy on `invoke_llm_subprocess` kwargs at the module attribute; the spy forwards nothing
  and returns a canned StepResult, which is acceptable here because AC6 already anchors the real side effect).
- **AC9 (phase_2 unchanged)** bd#10 AC-I5 and AC-I6 still pass untouched; `_role_template_injections(prev)` returns the same tuple as
  `_declared_injections(prev.data)`.

## §4 Negative-test teeth (which code change turns each AC red)
AC1 goes red if the record drops `source_id` or `_declared_injections` raises on a non-dict. AC2 goes red if any builder keeps calling the
bare-string helper (it cannot return `source_id`). AC3 goes red if any single builder omits the `role_template` key, records `rt.content.rstrip()` (not in the
loader's spelling, so the sha differs from phase_2), or `.resolve()`s the path. AC4 goes red if a builder "fixes" placement by adding a
separator. AC5 goes red if any one of the 21 direct dispatches or the pool submit loses `injections=`, and on a new uncounted call. AC5b goes red
if any dispatch passes the `StepResult` instead of its dict, passes `()`, or leaves the pool path's `injections` defaulted. AC4b goes red
if a role-less branch inherits `role_template` through `{**prev.data}`. AC6 goes red if the clarify or
architect dispatch drops `injections=`, or the builder stops recording. AC7 goes red if a missing template yields a `None`/empty
`source_id` block (the chokepoint would refuse). AC8 goes red if a retry passes `()`.

## §5 Scope
- `engine_py/bytedigger_engine/workflows/phase_workflows_common.py` (three helpers; `_maybe_role_template` re-implemented on `_role_template`)
- `engine_py/bytedigger_engine/workflows/{phase_1_discovery,phase_2_explore,phase_3_clarify,phase_4_architect,phase_45_spec,phase_45_spec_lite,phase_5_implement,phase_5_integrity,phase_6_review,phase_6_fix_integrity,phase_7_synthesize}.py` (call-site edits only)
- `engine_py/tests/test_bd141_p4d_role_template_injections.py` (new RED file)
- `engine_py/bytedigger_engine/conformance/AUTHORSHIP_SPEC.md` (R3.2 "Why" cell at :371, "one of eight ... migrated", and the re-open
  paragraph at :457-461: all role-template sites migrated; remaining segments in bd#147). The label value `injections-channel-only` is unchanged.
- `CHANGELOG.md`
- **No sibling test is edited** (gate r1 F2 is closed by narrowing AC2 to calls, not by editing siblings).

§1a sibling audit (RED author greps and lists in the RED report): `test_bd10_l3_authorship.py`, `test_bd119_*`, `test_bd73_*`,
`test_bd28_bd_l3_checker.py`, every `test_phase_*.py`, and every test that patches `invoke_llm_subprocess` and asserts its exact kwarg set
or that patches or pins `_maybe_role_template` (kept, so these must stay green unedited; listed in §2.1).

## §6 Not in scope (declared, follow-up bd#147)
Other non-literal prompt segments found by the 4(d) survey. They are real R3.2 candidates but need their own source-id and verbatim
decisions, so they go to a follow-up lot rather than this one:
- `phase_45_spec` decision doc (`_read_decision_doc_block`, truncated head+tail over the cap, so `content in prompt` needs a per-chunk rule);
- standards context (`_standards_context.get_standards_context`, produced by a bun subprocess; source id is unclear);
- facts pack (`facts_pack.spec_facts_block`, generated, synthetic id);
- `task_description` (org_config string), prior-step findings, inlined in-scope test files (`phase_6_review._inline_inscope_test_files`, truncated);
- `lib/directed_repair.py` artifact text and `semantic_verifier` interpolated findings (no role template, outside `workflows/`).
Also not in scope: any change to `_injection_refusal`, `InjectedBlock`, `attest.assemble`, or prompt placement; HAL-side changes.

## §7 Behaviour change and risk
No prompt byte changes (§1.5), so `prompt_sha256` and model behaviour are unchanged. The one observable change: with
`role_template_path` set, every phase's attestation now carries an `injections` entry, and R3.2 goes from `not-checked` to `passed`
on those steps. The risk is a fail-closed refusal (`E_INJECT_UNATTRIBUTED`, non-recoverable) if a builder's placement ever stops
containing `rt.content` verbatim; AC3 guards each builder against exactly that, and AC4 against a separator drift.

### §7.1 Known not-checked path (gate r1 F6)
Re-entry into `_write_green_artifact` from the engine's reconstructed plain dict (`phase_5_implement.py:7616-7621`, built at `engine.py:663-682`)
does not carry `role_template`, so a GREEN retry reached through orphan-GREEN recovery declares `()`. R3.2 is `not-checked` there, not
violated: the chokepoint does not refuse an empty declaration. Carrying the record through the engine's re-entry dict changes `engine.py` and
is left to bd#147.

## §8 Gate r1 disposition
| r1 | Disposition |
|---|---|
| F1 MAJOR | §1.4 lists spec_lite's free-rewrite branch as producer 17; §2.2 adds the single-read rule and the keyword param for the two `str` builders; AC3 covers it |
| F2 MAJOR | AC2 narrowed to calls; re-exports and name-patches allowed, so `test_261` and `test_34AEB235` stay green unedited; "no hiding" rule in §2.2 |
| F3 MAJOR | AC5b behavioural matrix over all 22 dispatches, including COMPLEX satisfaction pool; §2.3 requires the dict argument |
| F4 MINOR | §2.2 explicit-on-every-branch; AC4b |
| F5 MINOR | AUTHORSHIP_SPEC in §5; follow-up named bd#147 |
| F6 MINOR | §7.1 |
| F7 NIT | line ref fixed; RED docstring updated in rev2 |
