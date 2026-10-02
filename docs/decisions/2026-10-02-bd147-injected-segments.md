# bd#147: the R3.2 boundary, and the file-sourced prompt segments that cross it

**Status:** r2 (gate r1 REJECTED: 3 MAJOR + 4 MINOR + 1 NIT, `2026-10-02-bd147-gate-r1.md`; r2 closes all, disposition in §9) · **Tier:** 2 (one shared helper extended, three producers, three dispatch sites, one
spec section; Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `llm_subprocess._injection_refusal` stays the only R3.2 check; on the declare side
`phase_workflows_common._declared_injections` stays the only reader of a builder's declarations (bd#141 4(d)). This lot widens
what that one reader returns. It adds no second reader and no second check.
**Side of the seam (decision 2026-07-26 §7.4, "injected-content attribution"):** engine. The one segment whose source lives on
the host side (standards context) is deferred for exactly that reason (§6).
**Source:** bd#147 (follow-up from PR #148 / bd#141 4(d)); bd#10 §4 (R3.2 scope boundary, re-open criterion).

## §1 Problem (measured on `d464f6f`)

1. `AUTHORSHIP_SPEC.md` §4 says R3.2 governs blocks the engine assembles through `injections`. Text a phase writes by string
   concatenation is covered whole by R3.1's prompt hash. The re-open criterion is "the first phase that inlines
   **file-sourced** content into a prompt without routing it through `injections`". After bd#141 4(d) the role template is
   declared everywhere, but other non-literal segments still meet the criterion. bd#147 lists them. Nothing yet says which
   of them count as "injected", so a RED for any of them would have to choose the boundary itself.
2. **Survey, every segment bd#147 names plus one it does not (`ctx.question`).** "Bytes come from" means the place the
   prompt's bytes are read from at build time.

   | # | Segment | Producer | Bytes come from | Inlined as | Site |
   |---|---|---|---|---|---|
   | S1 | decision doc | `phase_45_spec._read_decision_doc_block` (`:398`) | file `org_config["decision_doc"]`, resolved by `skip_logic._resolve_decision_doc_path`, read `utf-8-sig` | whole, or (over 80 000 UTF-8 bytes **and** over 75 000 chars) `text[:60000]` + engine marker + `text[-15000:]`; always inside an engine wrapper `## DECISION DOC (full file at {resolved} — text inlined below)\n\n{body}\n` | `_build_spec_prompt` `:1184-1187` → `_invoke_spec_llm` dispatch `:1361` |
   | S2 | in-scope test files | `phase_6_review._inline_inscope_test_files` (`:1353`) | files from `git_diff_files(pre-red-ref, worktree)`, filtered, read `utf-8, errors="replace"` | per file `### {rel}\n```python\n{content}\n```` where `content` is `text[:20000]` + engine marker `\n[TRUNCATED — …]` when over the cap | `_build_fix_prompt` `:2446` → dispatch `:2573`, retry `:2700` |
   | S3 | directed-repair artifact | `lib/directed_repair._build_prompt` (`:279`) | file `artifact_path`, read `utf-8, errors="replace"` (`:513`) | whole, between `--- BEGIN ARTIFACT ---\n` and `\n--- END ARTIFACT ---` | dispatch `:521`, no `injections` |
   | S4 | standards context | `_standards_context.get_standards_context` (`:42`) | stdout of a **host** shim `bun <hal_root>/SYSTEM/cli/build/devops-prompt-context.ts` (not in this repo) | prepended; `rstrip("\n") + "\n\n"` when not already so terminated | spec `:1292`, RED `phase_5_implement:1448`, GREEN `:7460` |
   | S5 | facts pack | `facts_pack.facts_block` / `render` (`:301`) | **engine-computed** from repo files, ledger and graph (possibly served from the engine's own cache file `:356-359`); rendered with `FACTS_HEADER`, a per-audience lead, line cap with `(truncated)` | engine schema over extracted fields; individual fields (e.g. KNOWN REDS ledger cells, `:326-327`) are verbatim, no file body or slice is | spec `:1191`, p5 `:1356/:6439/:7313`, p6 `:838` |
   | S6 | `task_description` | `_task_description.normalize_task_description` (`:17`) | `org_config` string, `.strip()` | verbatim under `TASK CONTEXT:` / `FEATURE REQUEST:` | `phase_1_discovery:378-390` only (spec_lite does **not** inline it; bd#147's text is stale on that point) |
   | S6′ | `ctx.question` | run input | run parameter | verbatim, many prompts | many |
   | S7 | prior-step findings | `prev.data["findings"]` | **model output** of an earlier invocation, held in memory (the structured-findings sidecar read back from disk at `phase_45_spec:1051` is a separate segment, S10) | verbatim after an engine header | spec `:1273`, spec_lite `:425`, RED `:1442`, fix `:2436` |
   | S8 | semantic-verifier findings | `semantic_verifier.py:129` | reviewer **model output** fields (`severity/file/line/quote/claim`) | interpolated | dispatch `:180` |
   | S9 | directed-repair findings | `directed_repair._render_findings` (`:232`) | **engine** (deterministic gate) fields; `evidence` may quote the artifact verbatim (`:241-243`) | rendered `- {path}:{line} [{rule}]: {evidence}` | `:279` |
| S10 | scratchpad round-trips (gate r1 F1) | `phase_45_spec:1064` reads `spec.md` → `build_surgical_revise_prompt` (`:1082`, `surgical_revise.py:85-86`), `build_delta_retry_prompt` (`:1113`), `_restricted_writer_prompt` (`:1250-1257`); `load_findings_thread` (`:1051`, `findings_sidecar.py:36-37`); spec_lite review doc + spec (`:388`, `:397-402`, `:570-573`) | files in the run's scratchpad, written earlier in the run (by a model or by the engine from model output) | whole file bodies | those builders; **not migrated by this lot** (§6) |

3. **Survey scope and method (gate r1 F1).** The table covers every segment bd#147 names, plus every file read whose text reaches
   a prompt inside the functions this lot edits (`_build_spec_prompt` and its siblings in `phase_45_spec.py`, `_build_fix_prompt`,
   `attempt_directed_repair`) and in the spec_lite builders the gate cited. It is **not** an exhaustive inventory of `workflows/`
   and `lib/`. That inventory is the first task of the class-I follow-up issue (§6), and the boundary in §2 is written so the
   inventory can classify every hit without a new decision.
4. The event log does not record model **output** (no output hash or text on `model_invocation_attested`; `raw_response` lives in
   `StepResult.data` and scratchpad docs). That matters for S7/S8 (§2.1 class M).

## §2 The boundary (normative; recorded in `AUTHORSHIP_SPEC.md` §4 by this lot)

### §2.1 Four classes

A prompt segment belongs to exactly one class. The test is **where its bytes are read from when the prompt is built**. It is not
who wrote them originally.

The rule is **structural** (gate r1 F2): it asks what *unit* of read bytes is inlined, not whether any byte is verbatim.

- **Class I — injected (R3.2).** A **file body, or a contiguous slice of one** (a fixed-size prefix, suffix, or head and tail),
  or the **whole stdout of a subprocess**, read at build time and inlined as one unit. The writer of the file does not matter:
  an operator's file, a repo file a model edited, and a scratchpad file a model wrote earlier in this run are all class I.
  These bytes are not fixed anywhere else in the event log, and a declaration `{path, sha256(inlined text)}` is true about them
  whoever wrote them. **Every class-I segment is required to be declared on `injections`; the ones not yet declared are listed
  by name in §6 with a follow-up issue, and nothing else is exempt.** Declared here: role template (bd#141 4(d)), S1, S2, S3.
  Not yet declared: S4, S10.
- **Class E — engine-authored (R3.1).** Text the engine's code composes under **its own schema**, by extracting named fields,
  tokens, rows or lines and re-rendering them, each field bounded. Individual fields may be verbatim (a ledger cell, a gate's
  `evidence` line, a path). A field is not a file body or a slice, so it is not class I. An engine-written cache of the engine's
  own computation (the facts-pack cache) is class E. Members: S5, S9, and every wrapper, header, marker and instruction around a
  class-I segment. R3.1's whole-prompt hash covers them.
- **Class P — run parameters (R3.1).** Values the invoker passes to the run: S6′ `ctx.question` and `org_config` strings
  inlined as text, S6 `task_description`. They are the principal's instruction, not content injected into it. A `source_id`
  could only be synthetic (`org_config.task_description`). Its digest would be the digest of a substring of a prompt that
  R3.1 already hashes, so declaring it adds no information. A file **path** in `org_config` is not a class-P value. The file it
  names is class I.
- **Class M — carried model output (R3.1, declared limit).** Text a previous model invocation returned, passed along **in memory**
  in `prev.data` and never read back from a file: S7, S8. Once the same text has been written to a file and read back, the read
  is a class-I file body (S10). Gate r1 F1 proposed widening M to cover scratchpad round-trips instead. That would classify by
  writer, which this section rules out, and it would split S3 into two classes at a single site, because `artifact_path` is
  `spec.md` in the scratchpad for the phase_45 callers (`phase_45_spec.py:1847` and siblings) and a repo file for the phase_5
  callers (`phase_5_implement.py:3866`). It is not file-sourced. It is also not attested anywhere, because the event log records no output.
  Declaring it here would attest a provenance the log cannot back. **Declared limit:** a prompt injection that reaches a model
  through an earlier model's output is covered only by R3.1's hash of the later prompt. **Re-open criterion:** the event log
  records an output digest per invocation (follow-up issue, §6). Then a class-M block can carry `source_id =
  "invocation:<step_name>:<invocation id>"` and a digest a consumer can match.

### §2.2 Chunk rule (how a class-I segment is declared)

- One `InjectedBlock` per **maximal run of source text that reaches the prompt unmodified**. The engine's wrappers and
  truncation markers are class E and are in **no** block.
- `content` is the string **as inlined**, after the site's own decode (`utf-8-sig` BOM drop, `errors="replace"`). The
  digest is over the injected string, never over the raw file bytes. This is the same posture as the role template's
  `rstrip() + "\n\n"` (bd#10 AC-I7).
- All chunks of one source carry the **same** `source_id` and are declared **in prompt order**. A truncated S1 is therefore two
  blocks, head then tail. A consumer recovers which is which from the order and the pinned slice rule.
- A chunk whose `content` is `""` is not declared (AC10). `"" in prompt` is vacuously true, so the declaration would attest nothing.
- `source_id` is **the identifier the inlining site already shows the model**, so the declaration can be checked against
  the prompt. S1: `str(resolved)`, as printed in the wrapper. S2: `rel_path`, as printed in `### {rel}`. S3: `artifact_path`,
  as printed in `Artifact path:`.

### §2.3 Declared limits carried forward unchanged

bd#10 §4 lists two limits that still apply. A block declared once but inlined twice verifies. Prompt text that happens to contain
a block's content also verifies. S2 makes the second one likelier, because a fix prompt may quote a test line in the findings.
The re-open criterion is unchanged.

## §3 Design

### §3.1 Shared helpers (`phase_workflows_common.py`, §1aa named helpers)

- `_injected_blocks_record(prompt: str, blocks: list[dict]) -> dict | None`
  - Returns `None` when `blocks` is empty.
  - Otherwise returns `{"prompt_sha256": hash_text(prompt), "blocks": [{"source_id", "content"}, …]}` in the given order.
- `_declared_injections(data)` returns the role-template block **as today**, followed by each block of
  `data["injected_blocks"]`.
  - The record's blocks are included **only when `record["prompt_sha256"] == hash_text(data.get("prompt") or "")`**.
  - **Why the prompt binding (stale-forward guard).** 121 sites under `workflows/` spread `prev.data` into the next
    step's data (`{**prev.data, …}`, `{**_prev_data, …}`, `dict(prev.data)`).
    - Without the binding, a builder further down the chain would inherit S2's blocks. Its dispatch would then declare test
      files its prompt does not contain, and `_injection_refusal` would hard-fail a real run with `E_INJECT_UNATTRIBUTED`.
    - With the binding, blocks are declared only alongside the prompt they were built with. A forwarded record whose `prompt`
      has since been replaced is ignored. It is not deleted.
    - The role template does not need the binding, because every builder sets `role_template` explicitly (bd#141 4(d)).
  - **Containers are inert.** A non-dict `data`, a missing or falsy record, a non-dict record, a non-list `blocks`, or a
    `data["prompt"]` that is not a `str` (it is treated as a hash mismatch) each yields the role-only result.
  - **Elements fail closed (gate r1 F5).** Inside a bound record, each element becomes
    `InjectedBlock(el.get("source_id"), el.get("content"))` when it is a dict, and `InjectedBlock(None, None)` when it is not.
    A malformed element is therefore passed to the chokepoint, which refuses it with `E_INJECT_UNATTRIBUTED`. It is never
    dropped. The helper does not raise on any input.
  - **What the ignore branch hides (gate r1 F4).** A builder that binds its record to the wrong prompt (for example, it hashes
    before the standards block is prepended) hits the same branch as a forwarded record. It loses its declarations without a
    refusal. No event is emitted, because forwarded records reach this branch on most downstream steps and an event there
    would be noise. Mis-binding is caught by tests instead: AC1b binds S1 against a non-empty standards block, and AC6 covers
    every migrated site through the real chokepoint.

### §3.2 Producers

- **S1.** Add `_decision_doc_inline(cfg) -> tuple[str, list[dict]]`. It returns the block text exactly as
  `_read_decision_doc_block` builds it today, plus the chunk records (§2.2): one record `{str(resolved), text}` when the text is
  inlined whole, or two records `{str(resolved), text[:60000]}`, `{str(resolved), text[-15000:]}` when it is truncated.
  - `_read_decision_doc_block(cfg)` becomes `return _decision_doc_inline(cfg)[0]`. Its tests are unchanged.
  - `_build_spec_prompt` calls `_decision_doc_inline`. It stores `"injected_blocks": _injected_blocks_record(prompt,
    records)` in its returned data, computed over the **final** `prompt`, after the standards block is prepended at `:1292-1294` (AC1b).
- **S2.** Add `_inscope_test_files_inline(ctx, scratchpad) -> tuple[str, int, int, list[dict]]`, the current body plus one
  record per inlined file `{rel_path, content_before_marker}`.
  - `_inline_inscope_test_files` becomes `[:3]` of it. Its signature and its tests are unchanged.
  - `_build_fix_prompt` stores `injected_blocks` over its final `prompt`.
  - The fix invoke step's `extra_data` forwards `"injected_blocks": prev.data.get("injected_blocks")`, next to `prompt` and
    `role_template`, so the retry (`:2700`) declares the same blocks for the same bytes.
- **S3.** `attempt_directed_repair` passes `injections=(InjectedBlock(artifact_path, artifact_text),) if artifact_text else
  ()` to `invoke_llm_subprocess` at `:521`. There is no data dict, so the declaration is built inline at the one site that has
  both values. A shared helper would add an indirection with a single caller.

**Prompt bytes are unchanged at every site.** The lot adds declarations only (AC6).

## §4 Acceptance criteria

The RED file is `engine_py/tests/test_bd147_injected_segments.py`. It reuses the bd#141 4(d) harness pattern (`_Env`, `_prev`,
`_InvokeSpy`, `_RecordingAdapter`, `_FakeEventLog`), redefined locally because the test files are read-only to each other.

- **AC1 (S1 whole, behavioural).**
  - Setup: a decision doc under the cap, containing a distinctive sentinel line, configured via `org_config["decision_doc"]`.
  - `_build_spec_prompt` runs, then the spec invoke step runs with a spy. The spy's `injections` equal
    `(InjectedBlock(str(resolved), text),)`. With a role template also configured, the role-template block comes first and
    the decision-doc block follows it.
- **AC1b (S1 binding over the final prompt, gate r1 F3).** `phase_45_spec.get_standards_context` is monkeypatched to return a
  non-empty block. It is a dependency of the builder, not the unit under test. The S1 block is declared at the spy, and through
  the real chokepoint it is attested and the dispatch is `ok`. A GREEN that binds before the prepend reddens.
- **AC2 (S1 truncated).**
  - A doc of over 80 000 bytes and over 75 000 chars, with distinct head, middle and tail sentinels, declares exactly two blocks
    with the same `source_id`. They are head `text[:60000]` and then tail `text[-15000:]`.
  - No block contains the string `"... (truncated —"`, and no block contains the middle sentinel.
  - The F2 multi-byte case (over the byte cap but at most 75 000 chars) declares **one** block, the whole text.
- **AC3 (S2).** A git fixture has a `pre-red-ref.txt` and two changed test files. One is under the cap; one is over it and
  carries a tail sentinel past 20 000 chars.
  - The fix dispatch declares two blocks, in `surviving` order: `rel_path` with the whole text, then `rel_path` with
    `text[:20000]`.
  - No block contains `"[TRUNCATED"` or the tail sentinel.
  - A non-test changed file and a file under an excluded prefix are not declared.
- **AC4 (S2 retry).** The retry dispatch declares blocks identical to the first attempt's. The fixture drives the retry
  branch as `test_bd141_p4d` does for `:2700`.
- **AC5 (S3).**
  - `attempt_directed_repair` with a real artifact file sends `injections == (InjectedBlock(artifact_path, text),)` to its
    `invoke_llm_subprocess`.
  - When the artifact cannot be read (`artifact_text == ""`), it sends `()`.
- **AC6 (byte identity, §1l anchor).** For S1 whole, S1 truncated and S2, the dispatched prompt **contains** the literal block
  the test composes from the pinned formats in §1.2 (containment, gate r1 F8: the rest of the prompt is engine text other suites pin). It does not equal the output of the helpers under test. Each case also runs
  through the **real** `_dispatch_backend` with a `_RecordingAdapter` and a `_FakeEventLog`.
  - The emitted `model_invocation_attested` event's `injections` lists `{source_id, sha256}` per block. Each `sha256` is
    recomputed by the test from the text it wrote.
  - The adapter is called exactly once with status `ok`. This is the production side effect: a refusal would show up as
    status `error` and 0 adapter calls.
- **AC7 (stale-forward guard).** `_declared_injections` is given a data dict whose `injected_blocks.prompt_sha256` does not match
  `hash_text(data["prompt"])`, and returns only the role-template block (or `()` without one).
  - Positive control on the same dict with `prompt` set to the bound prompt: the blocks are returned in order.
  - End to end: a forwarded S2 record under a later builder's data, run through the real chokepoint, dispatches `ok`
    instead of failing `E_INJECT_UNATTRIBUTED`.
- **AC8 (malformed records).** Containers: `data` not a dict, `injected_blocks` set to `None`, a list, or a dict with no
  `blocks`, `blocks` not a list, and a non-str `data["prompt"]` each yield the role-only result without raising. Positive
  control: a well-formed bound record yields its blocks. Elements: a bound record whose `blocks` holds a non-dict element and
  one holding `{"content": …}` without `source_id` each yield a tuple containing an unattributed `InjectedBlock`. Through the
  real chokepoint that tuple is refused with `E_INJECT_UNATTRIBUTED` and the adapter is called 0 times.
- **AC9 (legacy views).** `_read_decision_doc_block(cfg) == _decision_doc_inline(cfg)[0]` and `_inline_inscope_test_files(...) ==
  _inscope_test_files_inline(...)[:3]` on AC1–AC3's fixtures. The existing suites
  `test_phase_45_spec_decision_doc_injection.py` and `test_phase_6_fix_inline_head_tests_65EA1B86.py` pass unmodified.

- **AC10 (empty chunks, gate r1 F6).** A decision doc that is only a UTF-8 BOM (`text == ""`) declares no S1 block. A changed
  test file that is empty declares no block, while a non-empty sibling in the same fixture is still declared.

## §5 Negative-test teeth (which GREEN mutation reddens which AC)

| Mutation | Reddens |
|---|---|
| declare the whole wrapper block instead of the body chunk(s) | AC1, AC2 (`content` mismatch; the marker appears in a block) |
| declare one block of `head + marker + tail` | AC2 |
| declare tail before head | AC2 |
| declare the marker-suffixed S2 content | AC3 |
| `source_id = str(full_path)` for S2 | AC3 |
| no `injected_blocks` forwarded in fix `extra_data` | AC4 |
| drop the `if artifact_text` guard (declare `""`) | AC5 (empty case) |
| any prompt-byte change at the three sites | AC6 literal |
| omit the prompt binding in `_declared_injections` | AC7 (stale record declared, real chokepoint refuses) |
| index the record without type checks | AC8 |
| bind S1 before the standards prepend | AC1b |
| drop malformed elements instead of passing them to the chokepoint | AC8 (elements) |
| declare `""` chunks | AC10 |

## §6 Not in scope (declared; follow-ups filed with this PR)

- **S4 standards context.** It is class I, but its sources are chosen by a **host** shim the engine cannot see. A `source_id`
  naming the shim invocation would attribute the bytes to a process rather than to the files that shaped them. The shim has
  to report its sources (a host-side contract, seam §7.4), so this gets its own issue.
- **S10 and any further class-I hit (gate r1 F1).** One follow-up issue does the exhaustive inventory of `workflows/` and `lib/`
  (§1.3) and migrates every class-I segment it finds. S10 is the known starting list. These segments carry the same R3.2
  status as the role template had before bd#141 4(d): undeclared, covered only by R3.1.
- **S7/S8 class M.** They need output attestation first (§2.1). That is a separate issue against R3.1/R3.2.
- S5, S6, S6′ and S9 are classes E and P, so nothing is migrated. The decision is recorded in the spec.
- The label value for R3.2 in `AUTHORSHIP_SPEC.md` (`injections-channel-only`) is unchanged. Its note is updated to cite the
  class boundary and the two follow-ups.

## §7 Scope (files)

- `engine_py/bytedigger_engine/workflows/phase_workflows_common.py`: `_injected_blocks_record`, `_declared_injections`.
- `engine_py/bytedigger_engine/workflows/phase_45_spec.py`: `_decision_doc_inline`, `_build_spec_prompt` data.
- `engine_py/bytedigger_engine/workflows/phase_6_review.py`: `_inscope_test_files_inline`, `_build_fix_prompt` data, the fix
  invoke `extra_data`.
- `engine_py/bytedigger_engine/lib/directed_repair.py`: `injections=` at the dispatch.
- `engine_py/bytedigger_engine/conformance/AUTHORSHIP_SPEC.md` §4 and the §3 label note.
- `engine_py/tests/test_bd147_injected_segments.py` (RED, new). `CHANGELOG.md`.

**Files NOT in scope (§1v):** `llm_subprocess.py` (the chokepoint is unchanged), `conformance/attest.py`, `_standards_context.py`,
`facts_pack.py`, `semantic_verifier.py`, `phase_1_discovery.py`, and every other dispatch site. The other sites keep calling
`_declared_injections(prev.data)`. For them the new branch is inert, because no other builder writes `injected_blocks`, and
AC7 covers the case where the record is forwarded to them.

## §8 Behaviour change and risk

- Three dispatch sites now declare more blocks. If a declared chunk is ever **not** in the prompt (a future edit to a wrapper
  that also changes the body), the run fails closed with `E_INJECT_UNATTRIBUTED` instead of dispatching. That is R3.2's
  intended posture. AC6 pins the current bytes, so such an edit reddens in CI before it ships.
- **Data-dict size (gate r1 F7).** The record holds full chunk content: up to 75 000 chars for S1, and N × 20 000 chars for S2.
  Each downstream `{**prev.data}` spread forwards it by reference, not by copy, until a builder replaces the data dict. It is
  never written to the event log. A step that serialises its whole data dict would write it to disk; this lot does not audit
  for such steps, and the same holds for `prompt` today, which carries the same bytes. The bytes were already in memory as part of
  `prompt`, so the added cost is one more reference to text that is already held.
- Attestation events grow by one `{source_id, sha256}` per chunk. The event payload size is bounded by the number of files, not
  by their size.

## §9 Gate r1 disposition

| Finding | Disposition |
|---|---|
| F1 MAJOR, scratchpad round-trips undecided | Decided as class I by the structural rule. The gate proposed widening M; that is declined, with the reason in §2.1. They are listed as S10, the survey's scope and method is stated (§1.3), and they are deferred by name to the class-I follow-up (§6). AUTHORSHIP_SPEC mirrors this and no longer claims the class is migrated. |
| F2 MAJOR, class E criterion | Replaced with the structural rule (file body or slice vs. extracted fields under the engine's schema). The S5 and S9 rows are corrected. |
| F3 MAJOR, S1 binding untested | AC1b added. |
| F4 MINOR, ignore hides mis-binding | Documented in §3.1. No event is emitted, with the reason stated. AC1b and AC6 are the detection. |
| F5 MINOR, element robustness | Elements fail closed at the chokepoint, and a non-str prompt counts as a mismatch. AC8 is extended. |
| F6 MINOR, empty-chunk rule | AC10 added. |
| F7 MINOR, size | §8 extended. |
| F8 NIT | AC6 wording changed to containment, the S1 dispatch cite is now `:1361`, and AUTHORSHIP_SPEC :461-462 is updated. |
