---
red_tests:
  - engine_py/tests/test_bd125_role_template_content_pin.py
red_pins: []
sibling_tests:
  - engine_py/tests/test_bd119_role_template.py
  - engine_py/tests/test_bd116_skill_companion.py
  - engine_py/tests/test_phase_45_spec.py
  - engine_py/tests/test_phase_5_integrity.py
paths:
  - engine_py/bytedigger_engine/role_template.py
  - engine_py/bytedigger_engine/lib/step_sentinel.py
  - engine_py/bytedigger_engine/error_codes.py
  - engine_py/bytedigger_engine/ERROR_CODES.md
  - engine_py/ERROR_CODES.md
  - docs/configuration.md
  - CHANGELOG.md
---
# bd#125: the role template is bounded by the bd#116 contract, pinned per run, and hashed by its bytes

**Status: r2 (after gate r1 REJECTED), TIER2.** Issue #125 (follow-up to bd#119 / PR #126; dependency bd#116 / PR #123 is merged).
**Class:** SYSTEMATIC: `org_config["role_template_path"]` is read by one reader (`role_template.load_role_template`) but only its size is bounded; what it may say is not, a workspace file can change between steps of a run, and the resume hash folds the path, not the bytes.
**Chokepoints:** `role_template.load_role_template` (items 1, 2) and `step_sentinel.compute_ctx_hash` (item 3).
**Side of the seam:** engine, host-config reader. **Owner:** bd.

| | |
|---|---|
| **LLM calls added** | None. String and hash work over one local file. |

## Principles

1. **Deterministic first.** Every verdict is a pure function of (template bytes, core skill text, run id).
2. **Reuse, no second parser.** Section scanning, slugs and fence rules come from `skill_companion._Doc` / `_heading_errors` / `_slug` and `lib/frontmatter.parse_frontmatter`.
3. **Opt-in, fail-closed.** Nothing changes while `role_template_path` is unset. When set, every failure is a `RoleTemplateError` (`CodedStepError`, code `E_ROLE_TEMPLATE_INVALID`), converted by `engine._execute_step`. No switch (per issue); rollback is reverting the commit.
4. **Provider-agnostic.** No model or provider is consulted. No run context (direct call) means no pin, not an error.
5. **Not in scope:** the 17 prompt builders, prompt bytes, `skills/bytedigger/SKILL.md` content, `skill_companion.py` (imported only).

## Design

### Item 1: content bounding by the core's overridable set

`load_role_template` keeps its seam (`RoleTemplate(source_id, content)`, `content` bytes-for-bytes as today) and, after the bd#119 byte checks, validates the text.

**Core.** `ROLE_CORE_ID = "bytedigger"`; the core text is `<plugin_root>/skills/bytedigger/SKILL.md`, `plugin_root = skill_companion.default_plugin_root()` (read at call time: `$CLAUDE_PLUGIN_ROOT`, else the repo root). The overridable set is the slugs of `metadata.overridable` (today `project-conventions`), read with `skill_companion._check_core`; its own errors count as an unusable set. The core file missing, unreadable, or with an unusable or empty set raises `core_unavailable` (fail-closed: the bound cannot be checked). Nothing is cached.

**Template shape.** Let `H` be the H2 sections of the template (`_Doc`).
- Frontmatter: `parse_frontmatter(text)` returns a dict or raises `FrontmatterError` -> `frontmatter_not_allowed`.
- `H` empty: the whole text is the body of ONE section. It is accepted iff the core declares exactly one overridable section; otherwise `section_required`. Checks below run on the text with `## <that slug>` prepended, so fence and markup rules apply to the whole text.
- `H` non-empty: any non-blank line before the first H2 -> `text_outside_section`; each H2 slug must be in the overridable set, else `section_not_overridable` (detail: the slug, capped to 64 chars).
- Structural rules, the same as `_check_companion`'s stages 5-7, each its own reason: `forbidden_markup` (a line with `<!--` or `-->`, which could forge a `bd:local` marker), `unclosed_fence`, `heading_level_invalid` (an H1 after an H2), `setext_heading`, `invalid_section_title` (an H2 with an empty slug), `duplicate_section`, `empty_section`.
- A plain text (no H2) is NOT exempt from these rules: a leading H1, `---`, `===`, an open fence or `<!-- -->` in it is rejected with the matching reason.
- Several errors: the first in the order above is the reason; `detail` ends `; +N more` for the rest. The message is one line, carries the key, the reason, the configured path and slugs only, never template text.

### Item 2: per-run pin (threat T1)

`RoleTemplate` gains a third field `sha256: str = ""` (hex of the raw file bytes). A module-level pin map `run_id -> {expanded_path: sha256}` under a lock, at most 64 runs (oldest dropped). `run_id = telemetry_ctx.get_current_run().run_id`; with no current run the pin is skipped.
Order inside `load_role_template`: byte checks (bd#119) -> pin check -> content check -> record pin. On a pin hit with a different digest: `pin_drift` (detail `sha256 changed during the run`). A first read that fails any check records no pin. A different run id has its own pin. Every later read in the run, including the gate-prompt builders (validator, review, integrity), goes through the same function, so the first read after a rewrite fails before a gate prompt is built.

### Item 3: resume hash folds the bytes

New `role_template.role_template_digest(org_config) -> str | None`: `None` while the key is unset/None/`""`; else `sha256(raw bytes).hexdigest()` after the bd#119 byte checks (no content or pin check); on any `RoleTemplateError` the string `"!" + reason`. It never raises.
`compute_ctx_hash`: when the digest is not `None`, (a) the early `return None` gate also requires no template (a context with only a template key now gets a hash), (b) `b"\0" + digest` is appended to the hashed bytes, in the TypeError-degrade branch too. With the key unset every hash is byte-identical to today. Deploy note: hashes of contexts that set the key change once (a resume re-executes the step once, the safe direction).

### Amended sibling tests (explicit, mechanical)

The content rules need `skill_companion`, `lib.frontmatter` and `telemetry_ctx` inside `role_template.py` (imported lazily inside functions, to avoid cycles). Therefore GREEN amends, in the sibling suite and by the red agent only:
- `test_bd119_role_template.py::test_role_template_module_contract` (lines ~719-745): the import allowlist additionally permits exactly `skill_companion`, `lib.frontmatter`, `telemetry_ctx` (plus `threading`, `hashlib` if absent); every other import is still forbidden.
- `test_bd119_role_template.py:463`: the comparison with the old 9-token `REASONS` becomes "`ROLE_TEMPLATE_REASONS` starts with the 9 old tokens".
- `test_phase_45_spec.py:512` and `test_phase_5_integrity.py:472`: the plain-text fixture starts with an H1 (`# Read-only role`); the heading marker is removed (`Read-only role`), nothing else changes.
No other existing fixture is affected (gate r1 grep).

## Reason tokens

`ROLE_TEMPLATE_REASONS` keeps its 9 tokens and appends, in this order: `core_unavailable`, `frontmatter_not_allowed`, `section_required`, `text_outside_section`, `section_not_overridable`, `forbidden_markup`, `unclosed_fence`, `heading_level_invalid`, `setext_heading`, `invalid_section_title`, `duplicate_section`, `empty_section`, `pin_drift`. The `E_ROLE_TEMPLATE_INVALID` description (in `error_codes.py` and both `ERROR_CODES.md`) is extended to say "outside the core's overridable sections, malformed, or changed during the run".

## Citations

- `engine_py/bytedigger_engine/role_template.py`: `load_role_template`, `RoleTemplate`, `ROLE_TEMPLATE_REASONS`.
- `engine_py/bytedigger_engine/skill_companion.py`: `_Doc`, `_check_core`, `_heading_errors`, `_slug`, `default_plugin_root`; `skills/bytedigger/SKILL.md` `metadata.overridable`.
- `engine_py/bytedigger_engine/lib/step_sentinel.py:65` `compute_ctx_hash`.
- `engine_py/bytedigger_engine/telemetry_ctx.py` `get_current_run` (run identity).
- `engine_py/bytedigger_engine/workflows/phase_workflows_common.py` `_role_template` (the one call seam; unchanged).

## Acceptance criteria

Real code paths; fixtures write real files in `tmp_path`; the plugin root is the real repo unless a case sets `CLAUDE_PLUGIN_ROOT` to a `tmp_path` plugin.

| AC | Case | Expectation |
|---|---|---|
| AC1 | template `## project-conventions` + body | accepted; `content` is the body text stripped plus `"\n\n"` |
| AC2 | plain text, no H2 | accepted, same `content` rule |
| AC3 | `## output-schema` section | `RoleTemplateError`, `reason == "section_not_overridable"`, `code == E_ROLE_TEMPLATE_INVALID`, a `CodedStepError`; message names the slug, one line, no body text |
| AC4 | text before the first H2 | `text_outside_section` |
| AC5 | each of: frontmatter, `<!-- x -->`, unclosed fence, H1 after H2, setext heading, duplicate H2, empty H2 | the matching reason, one case each |
| AC6 | `CLAUDE_PLUGIN_ROOT` = empty dir | `core_unavailable` |
| AC7 | tmp plugin whose core overrides `a, b`: `## b` accepted; plain text -> `section_required`; `## project-conventions` -> `section_not_overridable` | as stated (the set comes from the core, not a constant) |
| AC8 | run `r1` pinned, file rewritten to other VALID bytes | second read `pin_drift`; message has key and path, no template text |
| AC9 | in ONE test: run `r1` read, rewrite, `r1` read raises `pin_drift`; then run `r2` reads the rewritten file | `r2` accepted (own pin, not a path-only global pin) |
| AC10 | in ONE test: run `r1` read, rewrite, `r1` read raises `pin_drift`; then the same file read with no current run | accepted, no raise (no pin without a run) |
| AC11 | pinned, then rewritten to a content-invalid file | `pin_drift` (pin before content) |
| AC12 | first read content-invalid (no pin), file fixed, same run | accepted |
| AC13 | `RoleTemplate.sha256` | equals `hashlib.sha256(raw).hexdigest()` | Sibling `engine_py/tests/test_bd141_p4d_role_template_injections.py::test_ac1_role_template_loads_through_real_loader` expects the digest too (amended by RED after CI, PR #289).
| AC14 | `_role_template(ctx)` (common seam) with bad content and with drift | raises `RoleTemplateError` |
| AC15 | `compute_ctx_hash` same path, bytes A vs B, `task_description` set | different hashes; same bytes -> same hash |
| AC16 | key unset or `""` | hash equals the pre-bd#125 formula (`sha256(q \0 task \0 doc \0 ctx_cfg_sha8)[:12]`, computed in the test) |
| AC17 | only `role_template_path` set (no task, no decision_doc) | hash is not `None`, differs by bytes |
| AC18 | template file missing | `compute_ctx_hash` does not raise, returns a stable str |
| AC19 | `role_template_digest` | `None` unset; 64-hex for a file; `"!missing"` for a missing path; never raises |
| AC21 | template with two violations (frontmatter and an unclosed fence; and a case with `<!-- -->` plus a duplicate H2) | `reason` is the first in the documented order; message has `; +1 more` |
| AC22 | `## !!!` (empty slug) | `invalid_section_title` |
| AC23 | tmp plugin whose core has no `overridable` metadata | `core_unavailable` |
| AC24 | plain text starting with an H1; plain text with `---` first line; plain text with `<!-- x -->` | `heading_level_invalid`, `frontmatter_not_allowed`, `forbidden_markup` |
| AC20 | `ROLE_TEMPLATE_REASONS` | the 9 old tokens then the 13 new ones, in order |

**Note (RED scope):** guards for AC1, AC2, AC16 already pass on the current code and lot-preflight rejects passing tests in RED, so they are not in the RED file; they are covered indirectly (AC1 by the AC8/12/13/15/17 tests, AC2 by the bd#119 suite, AC16 by the GH1050 formula tests). AC9 and AC10 are folded into tests that fail today.

## Rollback

Revert the commit. Operators whose templates carry H2 sections other than the core's overridable ones, comments, or frontmatter must rewrite them; plain-text templates keep working only when they carry none of those; a plain text with a leading H1, `---`, `===`, an open fence or a comment is now rejected (breaking, noted in CHANGELOG).
