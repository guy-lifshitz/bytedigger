# bd#116 — a core skill and a local companion: `specializes:`, overridable sections declared by the core

**Status: FROZEN** (spec only; implementation decided after review) · **Class:** SYSTEMATIC ·
**Chokepoint:** `skill_companion.resolve` (`engine_py/bytedigger_engine/skill_companion.py`),
the one place anything decides whether a host's local text may extend a ByteDigger core skill,
and what the merged text is.

Source: harvest of Warp OSS / Oz, finding #1 (`oz-for-oss` `specs/GH251/tech.md`,
`.agents/skills/review-pr-local/SKILL.md`); HAL note `SHARED/notes/2026-09-30_harvest-warp-oz.md`
(HAL `b09cf8509`); `.bytedigger/learnings/skills.md`. Neighbour: bd#115 (verification-skill
registry, `docs/decisions/2026-09-30-bd115-verification-registry-spec-scores.md`) owns the
frontmatter parser; this spec reuses it and touches none of its files before it merges.

## §1 Problem (measured on `aa87816`)

1. **The only host lever on BD prompts is unbounded.** `org_config["role_template_path"]` is
   read by `_maybe_role_template`, defined **8 times** (`phase_workflows_common.py:582`,
   `phase_2_explore.py:177`, `phase_3_clarify.py:128`, `phase_45_spec.py:465`,
   `phase_45_spec_lite.py:260`, `phase_5_integrity.py:219`, `phase_6_fix_integrity.py:247`,
   `phase_7_synthesize.py:286`), 16 call sites, plus inline reads at
   `phase_1_discovery.py:348`, `phase_2_explore.py:201`, `phase_4_architect.py:216`. Each
   inserts the whole file into the prompt. Nothing limits what that file says: it can countermand the output schema, the
   verdict tokens or the safety rules, and nobody would know. A missing file is skipped
   silently. The key is not in `docs/configuration.md`.
2. **There is no notion of "this part of a core skill may be changed locally".**
   `grep -rn specializes` = 0 in BD and in HAL. `skills/bytedigger/SKILL.md` has no
   extension point; a host that wants its own conventions in `/build` edits the core file.
3. **That is how HAL and BD drifted into two forks.** HAL's `engine_py` is the upstream; BD is
   kept in step by parity sweeps (bd#114: 26 upstream PRs in one sweep) and
   `bd-drift-check.py`. Host-specific prose has no home except the core file, so every HAL
   customisation is either drift or a leak that `core-boundary-lint.py` has to catch.
   The decision "OSS = one core (BD = core, HAL = local companion)" has no mechanism.

4. **Not measured (§1b, open):** whether the orchestrator's Bash tool sees
   `CLAUDE_PLUGIN_ROOT` in an installed-plugin session. BD uses it only in hook processes
   (`hooks/hooks.json`, `scripts/build-gate.sh`, `scripts/gate-dispatcher.sh`,
   `scripts/ts/lib/config-reader.ts`); the manual install (`examples/claude-code-skill/SKILL.md:54,60`)
   uses `$BYTEDIGGER_HOME` and has no plugin root at all. A live probe (nested `claude
   --plugin-dir`) was refused by this session's auto-mode policy. op3 is designed to be
   safe either way; the implementation lot measures it before its RED freeze and records it
   here. **If negative** (plugin-mode Bash sees neither variable), that lot changes the
   invocation — and AC12's pinned literal — before RED freeze; shipping with a permanent
   `W_SKILL_COMPANION_UNAVAILABLE` line is not acceptable.

   **Measured by the implementation lot (2026-09-30, before RED freeze): NEGATIVE.** Nested
   `claude -p --plugin-dir <fixture plugin>`: (a) the Bash tool's `$CLAUDE_PLUGIN_ROOT` is
   empty; (b) the exact text `${CLAUDE_PLUGIN_ROOT}` in a plugin command and in a plugin
   `SKILL.md` is substituted with the absolute plugin root when the file is loaded; (c)
   `${CLAUDE_PLUGIN_ROOT:-$BYTEDIGGER_HOME}` is **not** substituted (left literal), so in Bash
   it falls to `$BYTEDIGGER_HOME` — unset in plugin mode ⇒ exit 127 on every build. Files the
   orchestrator opens with Read (`commands/build.md`, `phases/*.md`) get no substitution.
   **Invocation changed accordingly** (op3, AC12):
   `BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger`
   — in a loaded plugin skill/command the first assignment becomes the absolute root; in
   manual install (no substitution, empty in Bash) it falls to `$BYTEDIGGER_HOME`. Read-only
   files (`commands/build.md`, `phases/phase-0-classify.md`) carry the same line plus the
   instruction to use the absolute root the loaded skill shows when `${CLAUDE_PLUGIN_ROOT}` is
   not substituted in front of them.

What Oz does and why it is not enough for us: the core skill lists "Repository-specific
overrides" categories in prose; the companion `<core>-local/SKILL.md` declares
`specializes: <core>`; the prompt builder appends a path reference plus the sentence "may
override only the categories your core skill marks as overridable". The limit is enforced
**only by the model reading that sentence** (Principle C: codified ≠ enforced). We take the
shape and add a deterministic enforcement layer.

## §2 Design

### Terms

- **Core skill** — a `SKILL.md` shipped by BD: `<plugin-root>/skills/<core-id>/SKILL.md`. Its
  id is the directory name (`bytedigger`), not the display `name:` (`ByteDigger`):
  `[a-z0-9-]+` (a `--core` that does not match is an argparse usage error, exit 2 — no path
  traversal). `<plugin-root>` = `--plugin-root` flag, else a **non-empty** `$CLAUDE_PLUGIN_ROOT`, else the
  parent of the wrapper's own directory.
- **Section** — ATX H2 only: `^ {0,3}## +(.+?)(?: +#+)? *$`, outside fenced blocks (a fence
  opens with ≥3 backticks or tildes and closes with the same char, at least the same length).
  A section ends at the next ATX H1/H2 outside a fence, or at EOF. All line rules apply to
  the body **after** the frontmatter block and outside fences. ATX H1: `^ {0,3}# +`.
  Setext underline: `^ {0,3}(=+|-+)[ \t]*$` directly under a non-blank line. **Slug:** lowercase; runs
  of non-`[a-z0-9]` → `-`; trimmed. An empty slug is an error, never a match.
- **Overridable set** — declared by the core: `metadata.overridable: "project-conventions,
  review-focus"` (one comma-separated scalar, inside bd#115's parser subset). Split on `,`,
  strip; every entry must match `[a-z0-9-]+` (so `""`, a trailing comma or a blank entry is
  `invalid_overridable_entry`) and name exactly one H2 of the core. Key absent ⇒ nothing is
  overridable.
- **Companion** — a **committed** host file `<repo>/bytedigger/companions/<core-id>.md` (one
  fixed root in v1). Frontmatter: top-level `specializes: <core-id>`, equal to the file stem.
  Body: an optional preamble (anything before the first H2 — title, intro; never merged),
  then H2 sections; H3+ allowed inside them.
- **Why not `.claude/skills/<core>-local/` (Oz's layout).** Claude Code loads every
  `.claude/skills/*/SKILL.md` as an invocable skill, so a companion there would reach a model
  raw, outside any check (DesignReview F1), and bd#115 would scan it. `bytedigger/companions/`
  is read by no loader BD or Claude Code ships (gate r1 verified: `.claude-plugin/plugin.json`
  declares no custom dirs, `hooks/hooks.json` has no reader, BD has no `CLAUDE.md`/`@`-import).
  A host that `@`-imports the file into its own `CLAUDE.md` bypasses this by its own choice;
  `docs/configuration.md` says not to. Core modules also may not carry `.claude/` literals
  (`core_manifest.json` `forbidden.path_literals`).

### op1 — `skill_companion.py` (core module, stdlib only) — the chokepoint

`resolve(core_id, repo, plugin_root) -> dict` →
`{"core", "companion" | None, "sections": [slug…], "text", "companion_sha256" | None, "errors": [{path, reason, detail}]}`.
Parsing strips a UTF-8 BOM and reads CRLF as LF (a BOM or CRLF file is still a companion —
DesignReview F6). `companion_sha256` = sha256 of the companion's `HEAD` blob bytes (`None` when there is no
companion); it is informational (`check --json`, AC7) and nothing in BD consumes it.

**Evaluation order.** Stages run in order and errors accumulate, except that a stage whose
input failed is skipped:
- an `unsupported_frontmatter` on a file skips that file's later frontmatter checks (no
  `missing_specializes` after it);
- any stage-2 error that makes the overridable set unusable — `unsupported_frontmatter`,
  `invalid_overridable_entry`, `core_section_missing`, `ambiguous_core_section` on the core —
  skips stage 7;
- per entry/section: an overridable entry that is `invalid_overridable_entry` is not also
  checked for `core_section_missing`; a companion section whose title fails stage 6
  (`invalid_section_title`) is not also checked in stage 7;
- a stage-4 failure (`companion_not_committed`) skips stages 5–7: their input would not be
  the text that is merged. Stages 1–2 are about the core and run
with or without a companion, so `check` on BD's own core is meaningful.

0. **Repo** (before everything, lazily like bd#115) — `git rev-parse --show-toplevel` in
   `--repo` under the scrubbed env. Fails ⇒ no repo: the run proceeds with
   `<toplevel> = realpath(--repo)`; a companion file there then yields
   `companion_not_committed` (stage 4), no file yields exit 0 with the core bytes. Succeeds
   and the top level ≠ `realpath(--repo)` ⇒ usage error, exit 2 (a subdirectory would
   silently miss the companion).
1. **Core present** — no `<plugin-root>/skills/<core-id>/SKILL.md` ⇒ `unknown_core`, `text = ""`,
   stop.
2. **Core valid** — reported on the core path: `unsupported_frontmatter` (bd#115's
   `FrontmatterError`; the shared parser's guard token set grows from `verification` to
   `verification, overridable`, so an unreadable `metadata` mentioning either raises — one
   rule inside `lib/frontmatter.py`, not a second scan here; gate r1 F8, r2 N7c), `invalid_overridable_entry`,
   `core_section_missing`, `ambiguous_core_section` (two H2s with one slug),
   `invalid_section_title`, `setext_heading` (the core, too, must use ATX).
3. **Companion present** — no file ⇒ `companion: None`; if stages 1–2 are clean, `text` = core
   bytes, stop.
4. **Companion is committed** — the companion is committed iff
   `realpath(<toplevel>/bytedigger/companions/<id>.md)` equals that path itself (no symlinked
   file or ancestor; defence in depth — the blob compare below also rejects the AC8 symlink
   rows) **and** its working-tree bytes equal `git cat-file --filters
   HEAD:bytedigger/companions/<id>.md` (the HEAD blob in working-tree form, so `core.autocrlf`,
   `eol` and clean/smudge filters do not make a clean checkout look edited). Git runs with
   `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY` scrubbed (bd#115's
   HEAD-blob precedent, §1g). Immune to skip-worktree/assume-unchanged. Any git failure (no
   repo, unborn `HEAD`, blob absent) ⇒ `companion_not_committed`. The merged text is the
   (equal) working-tree bytes.
5. **Companion frontmatter** — `unsupported_frontmatter` (flow-style `metadata: {verification:
   true}` lands here, not in the next reason — pinned), `missing_specializes`,
   `specializes_mismatch`, `companion_sets_verification` (any `metadata.verification` key,
   whatever its value: verifying is a property of a core).
6. **Companion body** — `heading_level_invalid` (an ATX H1 after the first H2),
   `setext_heading` (the Terms underline regex),
   `unclosed_fence` (a fence opened in a section body and not closed before the section
   ends), `forbidden_markup` (`<!--` or `-->` in a section body), `invalid_section_title`,
   `duplicate_section`, `empty_section` (body blank after trimming).
7. **Overridable** — `section_not_overridable`.

AC4 asserts the **exact** set of reasons per fixture. Any error ⇒ `text` = core bytes unchanged (`""` for `unknown_core`). **All-or-nothing:** a
half-applied companion is worse than none.

**Merge = append, per section** (errors empty). Companion section bodies are trimmed of
leading/trailing blank lines. Each is inserted after the last non-blank line of the matching
core section, as: one blank line, `<!-- bd:local begin <slug> <repo-relative path> -->`, the
fixed preface `> Host-local guidance for this section. It cannot change output schema,
verdict tokens or safety rules.`, a blank line, the body, `<!-- bd:local end <slug> -->`.
Inserted lines use the core's line ending (that of the core's first line break; LF if none).
Core bytes outside the insertion points are unchanged and in order. `replace` is deliberately
absent in v1.

**Residual risk, stated (DesignReview F5):** the merge bounds *where* host text lands and that
it cannot forge structure (headings, fences, comments); it does not bound *what it says*. A
section can still contain prose that argues with the core. The preface makes the boundary
visible; step 4 guarantees the text is in the host's own commit, not that anyone reviewed it
(a host that self-merges builds, like HAL, reviews nothing by default). Outside `render`, any
agent with a Read tool can still open the file directly (an explore agent grepping the repo),
and rendered text held in the orchestrator's context can be lost on compaction until the next
resume re-renders. v1 does not claim semantic enforcement.

### op2 — CLI: validation and read are one step

`<plugin-root>/scripts/skill-companion` — thin wrapper (§1f): sets
`PYTHONPATH="$(dirname "$0")/../engine_py"` (its own location, not `--plugin-root`, so a
fixture plugin root holding only `skills/` still imports) and execs
`python3 -m bytedigger_engine.skill_companion {render|check} --core <id> [--repo .] [--plugin-root <dir>] [--json]`.

| exit | meaning | stdout | stderr |
|---|---|---|---|
| 0 | valid | `render`: merged text (core bytes when no companion); `check`: nothing, or `--json` | — |
| 3 | invalid | `render`: nothing; `check --json`: the dict, `errors` included | one line per error: `E_SKILL_COMPANION_INVALID <reason> <path>` — the companion path repo-relative, the core path plugin-root-relative (`skills/<id>/SKILL.md`) |
| 2 | usage error (argparse; bad `--core`; `--repo` not a top level) | nothing | usage |
| other | crash, `python3` missing | — | — |

Exit 3, not 2, so a usage error can never be read as "invalid companion" (gate r1 F4).
`check --json` prints `resolve`'s dict without `text`. Nothing is cached: no merged file to go
stale, outlive a deleted companion, or be rewritten by an agent (DesignReview F3).

### op3 — the one read path

- Edit points: `phases/phase-0-classify.md` gains a step **before** its current `:6` "First
  ACTION — Create build-state.yaml"; `commands/build.md` Phase 0 and its "Resumable"
  paragraph; `skills/bytedigger/SKILL.md` "CRITICAL: Load Pipeline";
  `examples/claude-code-skill/SKILL.md` (manual install). Each says: run
  `BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger`
  (§1 item 4; files opened with Read also say to use the absolute root the loaded skill shows
  when `${CLAUDE_PLUGIN_ROOT}` was not substituted in front of them), before `build-state.yaml` or `build-metadata.json` is written, and branch on its exit:
  - **0** ⇒ keep following the skill file already loaded (its paths stay authoritative — in a
    manual install the example skill's `$BYTEDIGGER_HOME/...` paths) and apply each
    `bd:local begin/end` block from stdout as an addition to the section it names; no block ⇒
    no companion, nothing to apply;
  - **3** ⇒ STOP, report stderr (a refused build registers nothing — DesignReview F7);
  - **anything else** (127: wrapper not found — no substituted root and `$BYTEDIGGER_HOME` unset; 2; 1; crash) ⇒ use the
    core `SKILL.md` exactly as today and print one visible line
    `W_SKILL_COMPANION_UNAVAILABLE exit=<n> — host companion NOT applied`. Deliberate (gate
    r2 N1c, r3 P4): only a *verdict* — on the core or on the companion — may stop a build; an
    unrunnable checker must not refuse every `/build`, and falling back to the core never
    lets unchecked host text in. A core defect STOPs every host; AC10 in BD's own CI is the
    shield that keeps a defective core from shipping.
  `/build continue` runs it again **before** it reads `build-state.yaml` (the `**Resumable:**`
  paragraph, `commands/build.md:41`).
- Every tier runs Phase 0 (TRIVIAL and SIMPLE included), so the read path has no tier gap.
- **Enforcement layers, named (Principle C).** "Only declared sections, no forged structure,
  only committed text" — `resolve`, deterministic, one chokepoint. "The orchestrator loads the
  skill through `render`" — prompt instruction only; its failure mode is safe: skipping
  `render` (or an unrunnable one) means the core alone — the companion is not loaded by any
  BD or Claude Code loader — not unchecked host text through the pipeline (residual risk
  above for direct reads). "A companion is valid in the host repo" — the host's own CI may run
  `check` (HAL wires it in its own lot).
- **Why no in-build gate backstop.** The r1 draft added a `gate_phase_4` check and a
  `companion_sha256` pin in `build-state.yaml`. Gate r1 F1/F2 showed that SIMPLE/TRIVIAL never
  reach phase 4, that `build-state.yaml` is agent-writable, and that the SubagentStop gate
  exits 0 on state older than 600 s (`build-gate.sh:111`). The text is read once per Phase 0 /
  resume, so a mid-build edit changes nothing already read; on resume, step 4 refuses an
  uncommitted edit. A companion edit that a build *commits* is in the build's own diff. Dropped rather than half-covered.
- **Reach (DesignReview F4):** v1 changes what the `/build` orchestrator reads. Phase agents
  and `engine_py` prompts do not read `SKILL.md`; their host lever stays `role_template_path`
  (#119).

### op4 — the first declared extension point

`skills/bytedigger/SKILL.md` gains an empty `## Project conventions` section and
`metadata.overridable: "project-conventions"`. That is the whole v1 surface. HAL moves its
`/build` conventions into `~/.claude/bytedigger/companions/bytedigger.md` in a separate HAL
lot.

### Coordination with bd#115 (lot-1874)

- One parser: `parse_frontmatter` from `verification_registry.py` (bd#115). #116's
  implementation starts after #115 merges, moves the parser to `lib/frontmatter.py` with a
  re-export in `verification_registry.py` (§1g), and adds BOM-strip + CRLF→LF there (shared
  fix: a BOM `SKILL.md` is invisible to #115 today).
- No edit to bd#115's discovery: companions live outside `skills/` and `.claude/skills/`.
- Shared schema: top-level `name`, `description`, `specializes`; `metadata.verification`,
  `metadata.verify_command` (#115), `metadata.overridable` (#116). No other keys are read.

### Registries and docs

`E_SKILL_COMPANION_INVALID` in `error_codes.py` and both `ERROR_CODES.md`; `skill_companion.py`
in `core_manifest.json`; `docs/configuration.md`: the companion layout, the "do not
`@`-import a companion" note, and the existing `role_template_path` (undocumented today,
DesignReview F11); `CHANGELOG.md` `[Unreleased]`.

### Out of scope (§1v)

`role_template_path` and its readers keep their behaviour (legacy input, §1x) — follow-up
**#119** (8 helper copies, 16 calls, 3 inline reads). `agents/*.md` and `phases/*.md` as cores
(v2). `replace` merge mode. An in-build gate backstop (see op3). Self-tuning of companions
from human corrections (harvest finding #4). `scripts/build-gate.sh`,
`scripts/ts/build-phase-gate.ts`, `scripts/gate-dispatcher.sh`, `hooks/`; bd#115's files
before #115 merges; `npm/`, `packaging/`; HAL's tree.

## §3 DesignReview

**Verdict: APPROVE-WITH-CHANGES** (Opus, against `aa87816`). All §1 counts confirmed.

1. **HIGH — companion under `.claude/skills/` is an invocable skill (applied).** Moved to
   `bytedigger/companions/<core-id>.md`; AC11.
2. **HIGH — `pre-build-gate.sh` is not reached by the pipeline (applied).** Only
   `phase-05-inject.md:10` calls it. Enforcement is `render` at the one read path (op3).
3. **HIGH — `.resolved.md` goes stale / outlives a deleted companion / is agent-writable
   (applied).** No cache; `render` validates at read time; committed-only (op1 step 4); AC9.
4. **MED — merged text reaches only the orchestrator (applied as stated reach).**
5. **MED — append bounds place, not content; H1 and `-->` escape the marker (applied).** Plus
   setext and unclosed fences (gate r1 F3); residual risk stated.
6. **MED — heading/encoding rules; BOM/CRLF (applied).** AC5.
7. **MED — CLI seams (applied).** Plugin-root resolution, wrapper-relative `PYTHONPATH`,
   `extra_dirs` dropped, refusal before `build-state.yaml`; the event is dropped.
8. **MED — bd#115 parser order (dissolved for `verification`; `overridable` handled in op1
   step 2).**
9. **LOW — `text_outside_section` rejects Oz's format (applied).** Preamble allowed, never merged.
10. **MED — missing ACs (applied).**
11. **LOW — document `role_template_path`; cite a follow-up (applied).** #119.

### Gate r1 (Opus) — REJECTED → changes applied

1. **MAJOR — backstop unreachable for SIMPLE/TRIVIAL; no host for "later phase".** Backstop
   and sha pin removed; the read path runs in Phase 0 for every tier; op3 "Why no in-build
   gate backstop".
2. **MAJOR — sha pin undefined and agent-writable.** Removed; replaced by committed-only
   (op1 step 4), which needs no pin.
3. **MAJOR — unclosed fence / setext forge structure.** `unclosed_fence`, `setext_heading`.
4. **MAJOR — non-{0,2} outcomes, argparse collision, no §5.** Exit table (invalid = 3; the
   "any non-zero ⇒ STOP" part is superseded by r2 N1); §5 added. The gate files are now out of scope, so their bats/TS
   siblings are not touched.
5. **MINOR — `PYTHONPATH` vs fixture plugin root.** Wrapper-relative; plugin-root precedence.
6. **MINOR — repo-relative wrapper path; only exit 2 stops.** `${CLAUDE_PLUGIN_ROOT}/…` (both
   superseded by r2 N1: `${CLAUDE_PLUGIN_ROOT:-$BYTEDIGGER_HOME}`, 0/3/else rule).
7. **MINOR — no phase 0 exit table; `:6` contradicts render-first.** Real edit points named;
   AC12 checks order and resume.
8. **MINOR — flow `overridable` in a core silently ignored.** op1 step 2; flow
   `verification` in a companion pinned to `unsupported_frontmatter`.
9. **MINOR — no evaluation order.** op1 stages 1–7.
10. **MINOR — merge byte semantics.** Insertion point, line ending, trimming, `empty_section`.
11. **MINOR — inline `role_template_path` readers.** §1 item 1; #119.
12. **MINOR — TS parity ACs weak.** Moot: no gate-backend change.
13. **MINOR — "inert" overstated; symlink/untracked.** Wording narrowed to shipped loaders +
    `@`-import note; `companion_not_committed`.
14. **MINOR — missing ACs (`check --json`, stderr, docs).** AC7, AC13.

### Gate r2 (Opus) — REJECTED (1 MAJOR) → changes applied

r1 MAJORs 1–4 confirmed closed; removal of the backstop and pin judged sound.

1. **N1 MAJOR — invocation unresolvable in manual install, unmeasured in plugin mode.**
   `${CLAUDE_PLUGIN_ROOT:-$BYTEDIGGER_HOME}`; `examples/claude-code-skill/SKILL.md` in §5;
   exit rule 0 use / 3 STOP / else core + `W_SKILL_COMPANION_UNAVAILABLE`; the unmeasured env
   recorded in §1 item 4 as an implementation-lot precondition (live probe refused here); AC12.
2. **N2 — committed-check mechanics.** HEAD-blob compare, realpath, scrubbed `GIT_*`,
   `--show-toplevel`; AC8 rows (skip-worktree, committed symlink, symlinked dir, no repo,
   unborn HEAD).
3. **N3 — `check --json` on exit 3.** Exit table; AC7 invalid row; stderr path bases.
4. **N4 — setext regex.** Pinned, after frontmatter, ` {0,3}` for H1 too; AC4 rows.
5. **N5 — `companion_sha256`.** Defined (HEAD blob), informational, asserted in AC7.
6. **N6 — cascades.** Parse-failure skips dependent stages; AC4 exact reason set.
7. **N7 — overclaims; shared parser.** Residual risk expanded; guard token set in
   `lib/frontmatter.py`; AC14 on `discover`.
8. **N8 — AC12 resume order; §5 siblings.** Both applied; `ERROR_CODES.md` regenerated.
9. **N9 — `--core` validation.** Usage error exit 2; AC7 row.

### Gate r3 (Opus) — APPROVED-WITH-CHANGES → changes applied

No MAJOR. N1, N3–N5, N7–N9 closed; N2/N6 partly, finished here.

1. **P1 — `--repo` check placement.** Stage 0 (lazy, before stage 3); no-repo path defined;
   AC10 passes an explicit top level.
2. **P2 — "immune to filters" false.** Compare with `git cat-file --filters`; autocrlf
   positive row; fixtures pin `core.autocrlf=false`.
3. **P3 — skip rule.** Stage-2 set named; stage-4 failure skips 5–7.
4. **P4 — core defect STOPs.** Principle reworded to "core or companion"; AC10 is the shield.
5. **P5 — manual mode stdout.** Exit 0 applies only the `bd:local` blocks; loaded skill's
   paths stay authoritative (both modes).
6. **P6 — negative probe.** Rule added to §1 item 4.
7. **P7 — advisory.** Applied (r1 items 4/6 superseded by r2 N1; setext pointer; wording;
   Resumable anchor; symlink defence-in-depth; AC14 frozen post-#115; AC13 before AC14).

## §4 Acceptance (RED: `engine_py/tests/test_bd116_skill_companion.py`)

Fixtures: a real temp git repo (`git init`, companion committed) plus a real temp plugin root
with a core `skills/core/SKILL.md`; `render`/`check` run as subprocesses through the real
`scripts/skill-companion` with `--plugin-root <fixture>`. `skill_companion` is imported lazily
inside tests (absent at `aa87816`, §1q). No mocks of `skill_companion`.

| AC | op | assertion |
|---|---|---|
| AC1 | op1 | no companion → `text` equals the core file bytes; `companion is None`; `errors == []` |
| AC2 | op1 | a committed companion with a preamble (`# Title` + paragraph) and one overridable section → the trimmed body sits after the section's last non-blank line inside `bd:local begin/end` with the preface; the preamble is absent; core bytes outside the insertion are identical and in order |
| AC3 | op1 | one overridable + one non-overridable section → `section_not_overridable`; `text` equals core bytes (the valid sibling is not applied) |
| AC4 | op1 | exact reason set per fixture; one fixture and one row per reason: `unknown_core` (`text == ""`), `missing_specializes`, `specializes_mismatch`, `duplicate_section`, `invalid_section_title`, `heading_level_invalid`, `setext_heading` (companion and core), `unclosed_fence`, `forbidden_markup`, `empty_section`, `core_section_missing`, `ambiguous_core_section`, `invalid_overridable_entry` (`""`, trailing comma), `unsupported_frontmatter` (core flow `{overridable: …}` → on the core path; companion flow `{verification: true}` → this reason, not `companion_sets_verification`), `companion_sets_verification` (`verification: false`); plus: an indented `  ---` setext underline in a companion section → `setext_heading`; an indented `  # H1` → `heading_level_invalid`; a core whose frontmatter `---` closes directly under a key and has no setext in its body → no error |
| AC5 | op1 | an `## Project conventions` line inside a ``` fence of the core is not a section; a BOM + CRLF companion resolves like its LF twin; in a CRLF core every inserted line ends in CRLF |
| AC6 | op1 | a core without `metadata.overridable` + any companion section → `section_not_overridable` |
| AC7 | op2 | invalid companion → exit 3, empty stdout, stderr lines match `^E_SKILL_COMPANION_INVALID <reason> <path>$`; valid → exit 0, stdout == `resolve(...)["text"]`; `check --json` → the dict without `text`, on a valid fixture (`companion_sha256` == sha256 of `git show HEAD:<path>`) **and** on an invalid one (exit 3, `errors` present); exit 2 for an unknown flag, for `--core ../x`, and for `--repo` pointing at a subdirectory |
| AC8 | op1 | `companion_not_committed` for each of: untracked file; committed then edited (working tree); edited and staged; edited under `git update-index --skip-worktree`; a **committed** symlink (mode 120000) to a committed file; a symlinked `bytedigger/companions` directory; not a git repo; unborn `HEAD`. `text` equals core bytes in each. Positive row: committed LF companion checked out under `core.autocrlf=true` → no error. All other fixtures run with `-c core.autocrlf=false` |
| AC9 | op2 | after a valid `render`, delete the companion (commit the deletion) and `render` again → exit 0, stdout == core bytes (nothing stale) |
| AC10 | op4 | the shipped `skills/bytedigger/SKILL.md` declares `project-conventions`, has exactly one such H2, and `check --core bytedigger --repo <git top level of the BD checkout>` against the real plugin root exits 0 (runs in BD CI) |
| AC11 | Terms | a `.claude/skills/bytedigger-local/SKILL.md` fixture with `specializes: bytedigger` has no effect on `text` (only `bytedigger/companions/` is read) |
| AC12 | op3 | prompt contract: in `phases/phase-0-classify.md` the `skill-companion" render --core bytedigger` step appears before "Create build-state.yaml"; in `commands/build.md` the `**Resumable:**` paragraph contains it before the word `read`; `commands/build.md` Phase 0, `skills/bytedigger/SKILL.md` "Load Pipeline" and `examples/claude-code-skill/SKILL.md` contain the invocation `BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger` (changed from `${CLAUDE_PLUGIN_ROOT:-$BYTEDIGGER_HOME}` by the §1 item 4 measurement), the exit-0 "apply the `bd:local` blocks" rule, the exit-3 STOP rule and the `W_SKILL_COMPANION_UNAVAILABLE` fallback rule |
| AC13 | registry | `E_SKILL_COMPANION_INVALID` in `error_codes.py` and both `ERROR_CODES.md`; `skill_companion.py` in `core_manifest.json`; `docs/configuration.md` names `bytedigger/companions/` and `role_template_path`; `CHANGELOG.md` `[Unreleased]` mentions #116; `core-boundary-lint.py` clean |
| AC14 | coord | RED frozen on the post-#115 base; after the parser move: `verification_registry.discover` on a BOM + CRLF `SKILL.md` with `metadata.verification: true` registers it (a deliberate behaviour change for #115, from invisible to registered); an unreadable `metadata: {overridable: x}` in a core raises `FrontmatterError` from `lib/frontmatter.py` |
| AC15 | op2/op3 (impl gate r1) | the real wrapper with **neither** `--plugin-root` nor `--repo` (cwd = repo top level), `CLAUDE_PLUGIN_ROOT` unset and set to `""` → plugin root = wrapper parent; a companion merged into the shipped core's empty `## Project conventions` section; the AC12 literal run through `bash -c` with `CLAUDE_PLUGIN_ROOT` substituted (plugin mode) and with `BYTEDIGGER_HOME` only (manual mode) → exit 0; the three skip rules; a symlinked / non-realpath `--repo` top level → exit 0; no repo + no companion via CLI → exit 0 + core bytes; ambient `GIT_DIR`/`GIT_INDEX_FILE` decoys do not change `resolve`; a BOM core keeps its BOM in `text`; no prompt file carries `${CLAUDE_PLUGIN_ROOT:-` |

§1l production side-effect: AC7/AC9/AC15 run the shipped wrapper as a subprocess against a real
git repo, and its stdout is exactly what op3 feeds the orchestrator.

## §5 Files (for the implementation lot)

**New:** `engine_py/bytedigger_engine/skill_companion.py`, `engine_py/bytedigger_engine/lib/frontmatter.py`,
`scripts/skill-companion`, `engine_py/tests/test_bd116_skill_companion.py`.
**Changed:** `engine_py/bytedigger_engine/verification_registry.py` (parser → re-export; after
#115), `skills/bytedigger/SKILL.md`, `commands/build.md`, `phases/phase-0-classify.md`, `examples/claude-code-skill/SKILL.md`,
`engine_py/bytedigger_engine/error_codes.py`, both `ERROR_CODES.md` (regenerated with
`error_codes.py --markdown`, never hand-edited), `engine_py/core_manifest.json`,
`docs/configuration.md`, `CHANGELOG.md`.
**Sibling tests (§1a, run with `--require-clean`):** `engine_py/tests/test_bd115_*` (parser
move); `core_manifest` readers — `test_core_boundary.py`,
`test_gh1111_core_boundary_blind_spots.py`, `test_bd48_core_boundary_cli_exit_ladder.py`,
`test_bd22_contracts.py`, `test_bd86_fact_pack.py`, `test_GH374_step_sentinel_primitive.py`;
`error_codes.py --check` consumers — `test_gh1067_ignored_dir_exclusion.py`,
`test_bd66_precommit_enforcement.py`, `test_bd8_l1_oracle.py`, `test_bd10_l3_authorship.py`,
`test_gh1406_red_skeleton_provenance.py`, `test_gh1591_fix_gate_boundary.py`,
`test_gh514_spend_limit_classify.py`, `test_GH1674_injection_missing.py`,
`test_gh1626d_orphan_green_recovery.py`; tree-wide CI steps — import smoke
(`ci.yml:43-64`), manifest parity (`ci.yml:66-82`), `cyrillic-prose-lint.py`. No test
at `aa87816` reads `commands/build.md`, `skills/bytedigger/SKILL.md` or
`phases/phase-0-classify.md` (grep of `tests/`, `engine_py/tests/`, `scripts/ts/__tests__/`).
