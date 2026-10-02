# bd#150: inventory every file read that reaches a prompt, declare the class-I ones, lint the rest

**Status:** r3 (gate r1 REJECTED 4 MAJOR / 8 MINOR; gate r2 REJECTED 1 MAJOR / 9 MINOR; all folded in below, map in "r3 changes") · **Tier:** Option D, formally Tier 2 (the scope is ~9 prod files, a label only: engine_py prod forces Option D; new prod `conformance/class_i_lint.py`; prod `workflows/phase_45_spec.py`, `phase_5_implement.py`,
`phase_5_integrity.py`, `phase_6_fix_integrity.py`, `phase_6_review.py`, `phase_7_synthesize.py`,
`lib/plugins/anti_hallucination/helper.py`; new data file `conformance/class_i_inventory.json`; normative
`conformance/AUTHORSHIP_SPEC.md` §4 "not yet declared" list; Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** two. The declaration chokepoint is the existing #147 pair
`_injected_blocks_record` / `_declared_injections` (`phase_workflows_common.py:707-745`), which every
builder already reaches. The detection chokepoint is new: one AST lint over `workflows/` and `lib/` that
compares every file-read call site with a checked-in inventory, so an unclassified read reddens.
**Source:** bd#150 (follow-up of #147, S10).
**Not in scope (§1v):** `engine.py`, `phases/*.md` (bd#89); `llm_subprocess.py` (the chokepoint is
unchanged: `_injection_refusal` and the attestation shape stay as they are); class-M carried model output (`findings_sidecar.py`, semantic-verifier findings, bd#152);
root-level modules outside `workflows/` and `lib/` (for example `role_template.py`, `facts_pack.py`; a declared lint limit, §2.6);
the two carried subprocess-output tails in §2.5 (deferred to bd#192, see §2.5).

## r3 changes (gate r2 finding → resolving section)

| Gate r2 | Resolution |
|---|---|
| MAJOR-1 `git_read` missing from wrappers | §2.6 wrapper list + "git_read family" rule; AC11 (h)-(i) + grammar; AC16 |
| MINOR-1 on-disk vs in-memory | §3 preamble, AC15 reworded: attestation captured in memory at `event_log.append` |
| MINOR-2 AC13 siblings, missing §5 | AC13 list (6 added); new §5 scope/sibling list incl. bd#81 SPEC_LINTS |
| MINOR-3 grammar corners | §2.6 "Scope (lexical)" pins alias, comprehension, duplicate-qualname, default/decorator rules; AC11 grammar cases |
| MINOR-4 stale-key problem format | §2.6 problem-string rule scoped to site-bearing problems; AC11 (b) |
| MINOR-5 re-measure / drift | §1.5 counts re-measured; `:883`→`:887`; Tier label |
| MINOR-6 "192 only on T1/T2", carrier rule | AC12 second half; §2.5 class-carrier rule |
| MINOR-7 §2.7 untested, AC14 regex | §2.7 anchors preserved; AC14 widened + AC14b |
| MINOR-8 AC12 selector | §2.1/§2.2 pin "read sits lexically in the named function"; AC12 |
| MINOR-9 AST rule trigger | AC15 AST rule widened to `"prompt"`; residual declared in §4 |

## §1 Problem (measured on `ccfbee6`)

1. #147 declared the segments it named and every read inside the functions it edited
   (`docs/decisions/2026-10-02-bd147-injected-segments.md` §1.3). That survey was scoped, not exhaustive.
2. #185/#187 deleted `phase_45_spec_lite.py` and `surgical_revise.py`, so the issue's spec_lite sites and
   `build_surgical_revise_prompt` are gone. Three `spec.md` inlines remain in `_build_spec_prompt`, all
   undeclared:
   - **S10a** cycle>=2 delta path: `phase_45_spec.py:1055` reads `spec.md` whole; `build_delta_retry_prompt`
     inlines it via `build_writer_prompt` (`restricted_writer_prompt.py:73`, `{spec}`). The data dict at
     `:1077-1087` sets `role_template: None` and has no `injected_blocks`.
   - **S10b** cycle>=2 restricted writer: `:1199` reads `spec.md` whole and inlines it the same way. The
     record at `:1255` covers only the decision doc.
   - **S10c** prior SHIP base (scaffold path, every cycle, `:1155`): `_prior_ship_base_block` reads the prior `spec.md` (`:989`) and
     inlines `spec_text.strip()` (`:1005`). Not listed in #147.
3. Packaged prompt fragments are class I too (§4: "It does not matter who wrote the file"), and none is
   declared:
   - **F1** `prompt_fragment.md` via `helper.get_prompt_fragment` (`helper.py:53`), inlined whole in
     `phase_5_integrity._build_integrity_prompt` (`:379`; also the `_integrity_stable_prefix` hint, `:273`),
     `phase_6_fix_integrity._build_fix_integrity_prompt` (`:549`), `phase_45_spec._build_review_prompt`
     (`:3843`), `phase_5_implement._build_validation_prompt` (`:6470`), `phase_6_review._build_review_prompt`
     (`:1028`) and `phase_6_review._build_satisfaction_prompt` (`:3025`).
   - **F2** `producer_prompt_fragment.md` (`helper.py:58`), in `phase_5_implement._build_red_prompt`
     (`:1415`), `_build_green_prompt` (`:7333`) and `phase_7_synthesize._build_synthesizer_prompt` (`:537`).
   - **F3** the secure-codegen fragment, `phase_5_implement._get_security_fragment` (`:988`, path
     overridable via `org_config["security_fragment_path"]`), in `_build_red_prompt` (`:1418`) and
     `_build_green_prompt` (`:7336`).
4. **R1** `phase_6_review._load_postfix_pytest_report` (`:658`) reads the post-fix pytest report (whole, or
   the last 16 KB) and `_build_review_prompt` appends it raw (`:887`). Undeclared.
5. Nothing detects a new undeclared class-I read. `test_bd141_p4d` scans only role-template call sites and
   `test_bd147` covers only the declared producers. Re-measured at the r3 head (AST count by bare/attribute callee name, no alias resolution; GREEN's lint
   resolves aliases, so its count may differ by a few): 142 raw file-read/subprocess sites, 50 wrapper-callee sites
   (`bounded_run`, `run_test_command`, `_read_text_or_empty`, `_read_or_empty`, `_git_read`) and **88 `git_read`
   sites** (68 `git_port.git_read`, 3 `git_port_mod.git_read`, 17 bare `git_read`, across 18 files; the bare ones
   include the bodies of the `git_port` helpers) under `workflows/` and `lib/`. The gate's "about 80" is the
   same family.

## §2 Design

### 2.1 Fragment blocks (F1, F2, F3): one named helper per source
- `helper.py` gains `get_prompt_fragment_block() -> dict` and `get_producer_prompt_fragment_block() -> dict`,
  each returning `{"source_id": <id>, "content": <text>}` from **one** read. `get_prompt_fragment()` /
  `get_producer_prompt_fragment()` stay and return `...block()["content"]` (one expression, no drift).
- Packaged-asset `source_id` = the package-relative POSIX path, so it is stable across install locations:
  `bytedigger_engine/lib/plugins/anti_hallucination/prompt_fragment.md` and
  `.../producer_prompt_fragment.md`.
- `phase_5_implement` gains `_security_fragment_block(cfg) -> dict` with `source_id = str(path)` (the path
  actually read, whether the org override or the default asset, matching the decision-doc convention
  `str(resolved)`). The asymmetry with F1/F2 is deliberate: an override is an operator path and must stay
  absolute, so the default asset uses the same form rather than a second id scheme. `_get_security_fragment(cfg)` returns its `["content"]`; the
  `FileNotFoundError`-fails-closed contract (`E_SEC_FRAGMENT_MISSING`) is unchanged.
- **Lexical placement (normative, AC12).** The single `read_text` of F1/F2 sits lexically in
  `get_prompt_fragment_block` / `get_producer_prompt_fragment_block`, and F3's in `_security_fragment_block`; the
  accessors may not delegate the read to a shared private helper.
- Each builder in §1.3 appends `block["content"]` where it appends the text today (the prompt bytes are
  unchanged) and adds the block to its record list.

### 2.2 spec.md blocks (S10a, S10b, S10c)
- **Lexical placement (normative, AC12).** The S10a and S10b `spec.md` reads stay lexically in `_build_spec_prompt`;
  the S10c read sits in `_prior_ship_base_inline` (or `_prior_ship_base_block`).
- `source_id = str(<the Path that was read>)`; content = the exact run that reaches the prompt:
  S10a/S10b `spec_text` (whole), S10c `spec_text.strip()`. An empty or unreadable spec declares nothing.
- `_prior_ship_base_block` returns `(block_text, records)` through a sibling, in the same way #147 split
  `_decision_doc_inline` from `_read_decision_doc_block`: a new `_prior_ship_base_inline(spec_path)` returns
  the pair, and `_prior_ship_base_block` stays as `[0]` for existing callers.
- The delta-path data dict gains `"injected_blocks": _injected_blocks_record(prompt, <S10a records>)`.
- The scaffold record at `:1255` declares, in prompt order: decision-doc chunks, S10c, then S10b. On a cycle>=2
  scaffold with a valid SHIP sidecar both S10c and S10b inline the same `spec.md`: the record then carries two
  chunks with the same `source_id`, `spec_text.strip()` first, the whole text second.

### 2.3 Post-fix pytest report (R1)
- `_load_postfix_pytest_report` keeps its return shape; `_build_review_prompt` declares
  `{"source_id": str(report_path), "content": _postfix[0]}` (whole or tail, the run that is appended raw).
  An empty decoded text declares nothing.

### 2.4 Binding
- Each builder in §1.3-§1.4 stores `"injected_blocks": _injected_blocks_record(prompt, records)` on the
  same dict as `"prompt"`, and its existing dispatch site already passes `_declared_injections(prev.data)`
  (verified on `ccfbee6`: every builder in §1.3-§1.4 reaches one of the `injections=` sites listed in
  `phase_*.py`). Where a builder already stores a record (spec scaffold, fix prompt), the lists are
  concatenated, not replaced. A builder that forwards a prior record (`phase_6_review.py:2620`) keeps
  forwarding it unchanged.
- **Retry paths (§1ab).** A retry that rebuilds its dict from `extra_data` must carry the record too.
  `_invoke_green_llm`'s `extra_data` (`phase_5_implement.py:7568`) forwards `role_template` but not
  `injected_blocks`, so the `GREEN_NO_MARKER` retry in `_write_green_artifact` (`:7682`) would declare only the
  role template. It gains `"injected_blocks": prev.data.get("injected_blocks")`. Rule, enforced by AC15: every
  dict literal under `workflows/` that has a `"role_template"` key also has an `"injected_blocks"` key.
- No builder mutates `prompt` after the record is computed. If one did, `_declared_injections` would drop
  the record (hash mismatch, #147 behaviour), and the AC for that builder reddens.

### 2.5 Deferred, with an explicit ruling (not silently skipped)
- **T1** `phase_5_implement.py:5153` (key `workflows/phase_5_implement.py::_verify_green_passing::_read_text_or_empty#0`): each failing group's pytest stdout tail is joined, left-truncated to
  `FINDINGS_MAX_CHARS`, carried in `prev.data` and rendered in the GREEN "TEST FAILURES" block (`:7427`).
- **T2** `phase_5_implement.py:3579` (key `workflows/phase_5_implement.py::_red_collect_probe::bounded_run#0`): the `pytest --co` output `[-400:]`, carried in a finding string into
  the RED retry.
- Ruling: both are class I (§4: a contiguous slice of a subprocess's output, and "parsed records take the
  class of where their field values came from"). Declaring them needs the `source_id` carried through the
  findings record and a chunk rule for the cross-group truncation. That is a findings-record schema change,
  and it shares machinery with bd#152 (carried output). They go to bd#192, and the inventory
  records them as `I-deferred` with `"issue": "192"` (§2.6). The lint does not know issue state; the
  deterministic tie is AC14. **Class-carrier rule:** when a spawn's output is read back through a second keyed
  call, the class sits on the call whose return value holds the bytes that reach the prompt (T1: the
  `_read_text_or_empty#0` read-back, not the spawn `_verify_green_passing::run_test_command#0`); the spawn is
  `not-prompt` ("output read back via <key>"), and `issue == "192"` appears on T1/T2 only (AC12). The tie is
  AC14: every `I-deferred` issue number appears as `bd#<N>` in `AUTHORSHIP_SPEC.md` §4
  "not yet declared", and every bd number listed there has an `I-deferred` entry. The kill criterion lives on
  bd#192.

### 2.6 Inventory + lint (the detection chokepoint)
- New `engine_py/bytedigger_engine/conformance/class_i_inventory.json`:
  `{"version": 1, "sites": {"<key>": {"class": ..., "note": ...}}}`.
  - `key` = `"<path relative to bytedigger_engine, POSIX>::<enclosing qualname or <module>>::<callee>#<n>"`,
    with `callee` and `n` fixed by the matching rule below. Keys carry no line numbers.
  - **Matching rule (normative).** A call is a site when its callee is, in canonical form:
    - `read_text`, `read_bytes`, `.open`: any `<expr>.read_text(...)`, `<expr>.read_bytes(...)`,
      `<expr>.open(...)`, whatever the receiver (`Path.open`, `io.open`, `gzip.open`, `os.open` all key as `.open`);
    - `open`: a call to the bare name `open` (builtin);
    - `json.load`, `subprocess.run`, `subprocess.check_output`, `subprocess.Popen`: resolved through the
      module's import bindings, so `import json as j; j.load(...)` and `from subprocess import run as r; r(...)`
      both match and key in canonical form;
    - **wrapper callees, keyed at each caller:** `bounded_run`, `run_test_command`, `_read_text_or_empty`,
      `_read_or_empty`, `_git_read`, **`git_read`**, `rev_parse`, `status_porcelain`, `ls_files_others`,
      `worktree_list_porcelain`, matched by bare name or attribute name, and also through import bindings (so
      `from ...bounded_spawn import bounded_run as br; br(...)` keys as `bounded_run`). These are the engine's
      subprocess and read wrappers (`lib/bounded_spawn.py:16`, `lib/plugins/disk_truth/test_runner.py:331`,
      `phase_5_implement.py:4604`, `phase_6_review.py:1275`, `phase_workflows_common.py:66`, and the
      `lib/git_port.py` read port: `git_read` `:150` plus its helpers `:172-226`). Their own raw
      call inside the wrapper body is keyed as well, as `not-prompt` ("wrapper body; callers keyed").
      The list is a module constant `WRAPPER_CALLEES`; a new wrapper must be added to it (declared limit).
    - **The `git_read` family (MAJOR-1).** `git_port.git_read` is the engine's real git subprocess chokepoint:
      all 88 call sites collapse to the single raw key `lib/git_port.py::_git_read_subprocess::bounded_run#0`,
      so each caller is keyed instead (`<path>::<scope>::git_read#n`; the helpers' bodies key as
      `lib/git_port.py::rev_parse::git_read#0` etc.). The four helpers have no caller outside `git_port.py`
      today (`status_porcelain` also collides by name with `disk_truth/git_diff.py:182`, which keys harmlessly as
      `not-prompt`). **Classification of the existing sites:** GREEN generates the inventory entries for them by
      script and sets class `not-prompt` with a per-key note (the command and what is done with the result); this
      is justified by measurement: no `git_read` result is inlined into a prompt on this tree, and the two
      `git_read` calls inside prompt builders hand the diff over by path (`phase_5_integrity.py:292`, written at
      `:331`, path at `:371`; `phase_6_fix_integrity.py:461`, written at `:493`) so their notes must say "by path".
      A future `git_read(...).stdout` inlined into a prompt must be an `I-declared` key plus a declaration; the
      lint forces the classification decision, not its correctness (declared limit).
  - **Scope (lexical, computed by the lint, never from runtime `__qualname__`, so it is Python-version
    independent).** The chain of enclosing `def` / `class` / `lambda` names: `f`, `Cls.m`, `outer.<locals>.inner`,
    `outer.<locals>.<lambda>`; `async def` as `def`; a class body read keys under `Cls`; module level is
    `<module>`. Corners, pinned (AC11): list/dict/set comprehensions and generator expressions open **no**
    scope, a read inside one belongs to the enclosing scope; default-argument values, decorators, base classes
    and class keywords belong to the scope **containing** the `def`/`class` statement, not to it; several defs
    sharing one qualname in a module (conditional `def` in `try`/`except`, property getter and setter,
    `@overload`) **pool** their sites under that qualname. `n` is the 0-based ordinal of that canonical callee
    within that scope, ordered by `(lineno, col_offset)`; nested scopes count separately.
  - **Undetectable (declared limits):** bound-method aliasing (`r = p.read_text; r()`), `getattr`/`exec`
    calls, wrappers not in `WRAPPER_CALLEES`, and reads in root-level modules outside `workflows/` and `lib/`
    (for example `role_template.py`, reached from every builder; it is declared by bd#141). The
    resolved-import rule covers module-level and function-level `import`/`from` bindings only.
  - **Ordinal churn (declared limit).** A new site inserted above an existing one in the same scope takes the
    old key. To make that visible, every site-bearing problem string `check` reports (unkeyed site, constraint violation on
    a site) names the key **and** its `file:line`; a stale-key problem has no site and names the key only (and
    the string `stale`). The inventory `note` must name what is read; review compares them.
  - `class` ∈ `I-declared`, `I-deferred`, `E`, `M`, `not-prompt`. `I-deferred` requires
    `"issue": "<N>"`; every other class forbids it. `note` is a non-empty string (what is read, where it goes).
  - `not-prompt`: the engine never places these bytes, or a slice of them, into a prompt string. That covers
    reads for gating, hashing, counting, sentinels and git refs; writer-side reads; files copied to disk for
    the model to open with its own tools (`phase_05_inject.py:1278/1308`), since the model's tool reads are
    outside R3.2's engine-inlined scope; and a backend's tool responses (`reference_backends/pydantic_openai.py:224`).
- New prod module `engine_py/bytedigger_engine/conformance/class_i_lint.py` (pure AST + JSON, no I/O beyond
  reading the source tree, deterministic), API:
  - `call_sites(engine_root: Path) -> list[str]`: every key, sorted;
  - `check(engine_root: Path, inventory: dict) -> list[str]`: problem strings, empty when clean; each problem
    names the key it is about (site-bearing ones also `file:line`, see above);
  - `load_inventory() -> dict`: the packaged `class_i_inventory.json`.
  `engine_py/tests/test_bd150_class_i_inventory.py` asserts `check(<real bytedigger_engine>, load_inventory()) == []`.
  `check` enforces:
  - every call site found under `workflows/**/*.py` and `lib/**/*.py` has an inventory key;
  - every inventory key matches a call site (no stale entries);
  - the `class` / `issue` / `note` constraints above hold.
  A new raw read therefore reddens until someone classifies it. Classifying a read as `I-declared` without
  declaring it is caught by the behavioural ACs for the named sites, and by review for new ones (declared
  limit: the lint proves classification exists, not that it is right).

### 2.7 Normative text
`AUTHORSHIP_SPEC.md` §4 "not yet declared" list: S10 is replaced by T1/T2 (bd#192); the standards-context
entry (#151) is removed, because bd#89 P1 (#176) deleted `_standards_context.py` and nothing inlines it now
(the R3.2 row in the §2 table and the "standards context" paragraph near line 531 are reconciled the same way); the two literal anchors
`**Not yet declared:**` and `Any class-I segment` that bracket the list stay verbatim (AC14 reads between them),
and the removed entry leaves no `(#151)` / `bd#151` behind between them, nor in the R3.2 table row (AC14b);
a sentence names
`conformance/class_i_inventory.json` and its lint as the R3.2 detection layer (Principle C: the enforcement
layer is the lint test, run in the canonical suite).

### 2.8 Error codes / events
None new. Undeclared blocks are not a runtime error: the chokepoint behaviour is unchanged.

## §3 Acceptance criteria (`engine_py/tests/test_bd150_class_i_inventory.py`)

Behavioural ACs reuse the `test_bd147_injected_segments.py` harness: build through the real builder, dispatch
through the public chokepoint with a recording backend, then read the `model_invocation_attested` event the real
chokepoint emits through `telemetry_ctx`, captured **in memory at `event_log.append`** (not read back from disk;
§1l side-effect anchor). "Declares X" means the attested
`injections` list contains `{source_id: X.source_id, sha256: sha256(X.content)}` and the block content is a
substring of the dispatched prompt.

- **AC1** F1: for each of the 6 builders in §1.3 F1 (parametrized), the attestation declares
  `bytedigger_engine/lib/plugins/anti_hallucination/prompt_fragment.md` with the sha256 of that file's bytes.
- **AC2** F2: same for the 3 producer builders and `producer_prompt_fragment.md`.
- **AC3** F3: RED and GREEN prompts declare the security fragment, `source_id == str(path)`; with
  `org_config["security_fragment_path"]` pointing at a tmp file, the `source_id` is that tmp path and the
  sha256 is of its content.
- **AC4** S10a: cycle 2, delta path enabled, structured findings, `spec.md` on disk → the delta prompt's
  attestation declares `str(spec_path)` with sha256 of the whole spec text. Empty `spec.md` → no block with
  that `source_id`, and the dispatch is not refused.
- **AC5** S10b: cycle 2, delta disabled, structured findings → the scaffold attestation declares the
  writer's `spec.md` path, and the decision-doc block (when configured) still precedes it.
  **AC5b** same, plus a valid SHIP sidecar → two blocks with that `source_id`, in order: sha256 of
  `spec_text.strip()`, then of the whole text.
- **AC6** S10c: cycle 1, prior spec + matching SHIP sidecar → declares the prior spec path with sha256 of
  `spec_text.strip()`; stale sidecar → no such block.
- **AC7** R1: post-fix report present (small, and > 16 KB) → `_build_review_prompt` attestation declares
  `str(report_path)` with sha256 of the appended text (whole, then the tail). Absent → no block.
- **AC8** Prompt bytes are unchanged: for one builder per source (F1 via `phase_5_integrity`, F2 via
  `phase_7_synthesize`, S10c, R1), the `prompt` built with the declaration in place equals the `prompt`
  built when `_injected_blocks_record` is monkeypatched to return `None` (the record is data beside the
  prompt, never part of it). The existing prompt-shape suites in AC13 pin the rest.
- **AC9** `get_prompt_fragment()` == `get_prompt_fragment_block()["content"]`, same for the producer
  fragment and `_get_security_fragment(cfg)`; a missing security fragment still raises `FileNotFoundError`.
- **AC10** Lint: the real tree passes (every site keyed, no stale key, constraints hold).
- **AC11** Lint reddens (run `check` on a synthetic tree in `tmp_path`): (a) a new `Path(...).read_text()`
  in a `workflows/` file with no key; (b) a stale key; (c) `I-deferred` without `issue`; (d) `E` with an
  `issue`; (e) an unknown `class`; (f) an empty `note`; (g) an unkeyed `bounded_run(...)` call; (h) an unkeyed `git_port.git_read(...)` and an
  unkeyed bare `git_read(...)` each redden; (i) an unkeyed `br(...)` where `br` is an import alias of
  `bounded_run`. Each site-bearing problem string ((a), (c)-(i)) contains the key and `file:line`; the stale-key
  problem (b) contains the key and `stale` and no `file:line`. Key grammar (`call_sites` on synthetic files): two `read_text`
  calls in one function → `#0`, `#1`; `p.open()` → `.open`, builtin `open()` → `open`; `import json as j;
  j.load(f)` → `json.load`; `from subprocess import run as r; r(...)` → `subprocess.run`; a read in a nested
  def → `outer.<locals>.inner`; in a lambda → `outer.<locals>.<lambda>`; at module level → `<module>`; `git_port.git_read([...])` and bare `git_read(...)` → `::git_read#n`
  (also with `from ...git_port import git_read as gr; gr(...)`); `br(...)` alias → `bounded_run`; a read inside a
  list comprehension and inside a generator expression keys under the enclosing function; a read in a default
  argument and in a decorator expression keys under the scope containing the `def`; two defs with one qualname
  (try/except) → ordinals pool (`#0`, `#1`).
- **AC12** The inventory classifies S10a/S10b/S10c, F1/F2/F3 and R1 sites as `I-declared` (selected by the
  lexical placement of §2.1/§2.2: the `read_text`/`read_bytes` sites in the named functions), and the exact
  T1/T2 keys of §2.5 as `I-deferred` with `issue == "192"`, and `issue == "192"` occurs on **no other key**
  (other `I-deferred` entries are not constrained here).
- **AC14** Every `I-deferred` issue number appears as `bd#<N>` in `AUTHORSHIP_SPEC.md` §4 "not yet declared",
  and every `bd#<N>` in that list has at least one `I-deferred` entry. The set of issue numbers in the list is
  read with a regex that accepts both `bd#<N>` and `(#<N>)`, between the two literal anchors of §2.7.
  **AC14b** (§2.7) the list contains no `151` in either form, and the R3.2 table row no longer names the standards
  context (#151).
- **AC15** GREEN retry: drive `_invoke_green_llm` with a recording backend that returns no GREEN marker, so
  `_write_green_artifact` retries once; both attestations (captured in memory, per the preamble) carry the same F2 and F3 entries. Plus an
  AST check: every dict literal under `workflows/` that has a `"role_template"` key **or a `"prompt"` key**
  also has `"injected_blocks"` (widened from r2: it covers forwarding dicts such as `phase_6_review.py:1086`
  `extra`). The `injected_blocks` value may be `None`.
- **AC16** (MAJOR-1) `git_read` family: the real inventory has a key for every `git_read` call site found by
  `call_sites` (covered by AC10); every `git_read` key is `not-prompt` or `I-*` (never missing a note); every
  `git_read` key whose scope is `_build_integrity_prompt` (`phase_5_integrity.py`) or
  `_build_fix_integrity_prompt` (`phase_6_fix_integrity.py`) is `not-prompt` with a note containing
  "path"; and no module under `workflows/` or `lib/` other than `lib/git_port.py` calls
  `rev_parse` / `ls_files_others` / `worktree_list_porcelain` (the limit of §2.6 is checked, not assumed).
- **AC13** Sibling suites stay green: `test_bd147_injected_segments.py`, `test_bd141_p4d_role_template_injections.py`,
  `test_bd89_p3a_surgical_revise_dropped.py`, `test_phase_45_spec.py`, `test_phase_5_implement.py`,
  `test_phase_5_integrity.py`, `test_phase_6_review.py`, `test_phase_7_synthesize.py`, and the six added at r3:
  `test_phase_6_fix_integrity.py`, `test_phase_6_postfix_and_polish_3F5599A6.py`, `test_gh443_delta_retry.py`,
  `test_phase_6_ship_b4_remove_short_variants_52C6F42F.py`, `test_bd10_l3_authorship.py`,
  `test_bd119_role_template.py` (§1a, from the §5 scope list).

## §4 Residuals (issue comment)
- T1/T2 deferred to bd#192 (§2.5).
- The lint proves that a classification exists, not that it is correct (§2.6).
- An empty security-fragment override file yields `""` and no block (fail-open on empty, unchanged from base).
- bd#151 looks obsolete: its premise (`_standards_context.py`) was deleted by #176.
- Coincidental containment and repeated inlining still verify (#147 §4 declared limits, unchanged).
- The AC15 AST rule keys on dict literals with `"role_template"` or `"prompt"`; a forwarding dict that carries
  prompt text under another key is not covered (declared limit; no such dict is known).
- `git_read` sites are bulk-classified `not-prompt` by measurement, not by per-site review; a later inlining of a
  `git_read` result is caught only if its key is classified, not if the classification is wrong (§2.6).

## §5 Scope and sibling-test list (§1a)
- Prod: `conformance/class_i_lint.py` (new), `conformance/class_i_inventory.json` (new data),
  `lib/plugins/anti_hallucination/helper.py`, `workflows/phase_45_spec.py`, `phase_5_implement.py`,
  `phase_5_integrity.py`, `phase_6_fix_integrity.py`, `phase_6_review.py`, `phase_7_synthesize.py`; normative
  `conformance/AUTHORSHIP_SPEC.md`.
- The AST rule (AC15) forces an `injected_blocks` key into six more in-scope literals: `phase_6_fix_integrity.py:511`,
  `phase_5_integrity.py:345`, `phase_5_implement.py:1315/6502`, `phase_6_review.py:3066/5562` (value `None` is fine).
- Editing `AUTHORSHIP_SPEC.md` (a `*_SPEC.md`) triggers the bd#81 `SPEC_LINTS` at precommit
  (`precommit_lints.py:56`); GREEN runs them before commit.
- Sibling suites: the AC13 list.

## §6 r3.1 addendum: sibling assertions that pin the pre-#150 block set (found at AC13 run, post-GREEN)

The AC13 run after GREEN showed 13 sibling failures. All are green on base `ccfbee6`. Each fails because the sibling
asserts the *exact* pre-#150 injection set, and #150 adds F1/F2/F3 declarations by design (§2.1). These are sibling
over-constraints that §1a should have listed. They are not GREEN defects:

- `test_bd141_p4d_role_template_injections.py::test_ac5b_dispatch_declares_role_template_behaviourally` (11 cases)
  and `::test_ac8_green_retry_declares_same_injections` assert `injections == (role_template,)`.
- `test_bd147_injected_segments.py::test_ac7_forwarded_stale_record_dispatches_ok_through_chokepoint` asserts the fix
  builder's record has exactly 2 blocks (precondition) and the later attest has `injections == []`.

Amendment (test-only, RED phase; production code unchanged):

- **AC13a** bd141 ac5b/ac8: each dispatch declares the role template exactly once, and every other declared block is
  one of the fragment blocks §2.1 assigns to that call site (F1 / F2 / F3, matched by `source_id` and sha256 of the
  real fragment file). No other block is allowed. The check must not degrade to "contains the role template".
- **AC13b** bd147 ac7: the precondition counts the fix builder's test-file blocks (2) separately from the §2.1
  fragment blocks. The final assertion becomes: the later attest declares no test-file block; only the §2.1
  fragment blocks for that site remain.
- AC13 (all other suites) is unchanged and must stay green.
- **AC13b (r3.1 clarification)** The AC15 forced literal at `_build_decorr_prompt` (`phase_6_review.py:5562`, now
  :5580) sets `injected_blocks: None` explicitly. A stale fix-builder record can therefore no longer be forwarded to the
  decorr dispatch, and bd147 ac7's precondition "the stale record is forwarded" no longer holds. ac7 becomes: the fix
  builder's record has the 2 test-file blocks; the decorr builder's data has `injected_blocks is None` (the stale record
  does not leak); the dispatch through the chokepoint is ok; and the attest declares no test-file block (`[]`, since
  the decorr site has no §2.1 fragment).
