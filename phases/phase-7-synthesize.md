> This is Phase 7 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 7: SYNTHESIZE

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"7\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 7')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

## Entry Gate (MANDATORY)

Before starting Phase 7, orchestrator MUST verify in `build-state.yaml`:
- `review_complete: pass` — Phase 6 quality review passed
- `phase_5_implement: complete` — Implementation finished

If either field is missing → **STOP. Previous phase incomplete.**

Document results and summarize the build.

**WORKER AGENT CONSTRAINTS (include in every agent prompt):**
- You are a worker inside /build pipeline. Use Read/Edit/Write/Bash directly.
- NEVER call Skill tool (you don't have access, attempts waste turns).
- NEVER invoke /build, /bugfix, or any slash command.
- If stuck, report what's blocking you — don't try to delegate or escalate via tools.

## What You Receive

- Original feature request
- `build-state.yaml` — source of truth for files modified, review verdicts, scores
- Architecture spec path (from Phase 4)
- Constitution (optional)

**IMPORTANT:** Read `build-state.yaml` and spec files directly. Do NOT rely on inline summaries from the orchestrator — they rot with context compression.

## Actions

1. Launch Haiku synthesizer agent (use agent definition: `agents/synthesizer.md`) with: original request, path to `build-state.yaml`, path to architecture spec. Agent reads files itself — do NOT inline review verdicts or scores in the prompt.
2. **After synthesizer returns, extract learnings** (before any cleanup):
   ```bash
   SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
   bash scripts/learning-store.sh extract "$SCRATCHPAD" || true
   ```
   This persists `{scratchpad}/reviews/learnings-raw.md` entries to `.bytedigger/learnings/`.
3. Present summary:
   - What was built (3-5 bullets)
   - Key decisions and trade-offs

3. **Update documentation** (recommended):

   **Living Documents checklist (check ALL that apply)**
   Ask: what did this build change? Then update accordingly:
   - [ ] New/changed API endpoint? → Update API docs (OpenAPI, README API section, Postman)
   - [ ] New/changed data model or migration? → Update data model docs, ERD, schema docs
   - [ ] Architecture decision made? → Create or update ADR / design doc
   - [ ] New config option or env var? → Update setup/deployment docs, .env.example
   - [ ] New CLI command or flag? → Update CLI help text, README usage section
   - [ ] Changed behavior of existing feature? → Update relevant user-facing docs
   - [ ] New dependency added? → Update installation/setup docs
   If NONE apply, explicitly state: "No living documents affected."

## State Cleanup

- Delete `build-state.yaml` from CWD (build complete)
- Delete `build-tests.md` from CWD if it exists (Gherkin artifact, no longer needed)
- Delete `build-red-output.log` from CWD if it exists (TDD RED phase artifact)
- Delete `build-green-output.log` from CWD if it exists (TDD GREEN phase artifact)
- Delete `build-opus-validation.md` from CWD if it exists (Opus validation artifact)
- Delete `build-plan-review.md` from CWD if it exists (Phase 4.5 plan review artifact)
- Delete `build-metadata.json` from CWD if it exists (build metadata — on FAILED, keep for `/build continue`)
- Delete scratchpad transient subdirs only (preserves `.bytedigger/learnings/` for future builds):
  ```bash
  SCRATCHPAD_DIR=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
  # Safety guard: only clean dirs whose basename is .bytedigger (works for both relative and absolute paths)
  if [[ -n "$SCRATCHPAD_DIR" && "$(basename "$SCRATCHPAD_DIR")" == ".bytedigger" ]]; then
    for subdir in research architecture specs tests reviews; do
      rm -rf "${SCRATCHPAD_DIR}/${subdir}"
    done
  fi
  ```
  Do NOT `rm -rf` the entire scratchpad dir — that would destroy `.bytedigger/learnings/`.
- Delete `.bytedigger-orchestrator-pid` from CWD
- If pipeline FAILED: leave for `/build continue`

## Final Checkpoint

```
Done: [feature]
Files: [list]
Review: [verdicts]
Docs: [updated / N docs refreshed]
Next: [manual test / PR / done]
```

## 7.5 SHIP Protocol (if --pr flag)

If `--pr` flag was passed in the build invocation:

1. Run: `bash scripts/ship.sh --pr --state ./build-state.yaml`
2. Verify: `ship_complete: true` exists in build-state.yaml
3. Log PR URL from `ship_pr_url` field
4. If ship.sh fails (exit non-zero) → log warning, continue synthesis (SHIP is best-effort, not a gate)

**State log:** `ship_complete: true|false | ship_pr_url: <url>`

## Model Selection

Always **Haiku** — summary extraction is simple.

**Output Schema:** This phase is primarily output — the Final Checkpoint and summary sections are the intended output. Use the Output Schema fields at the end of the Final Checkpoint block: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`.

## Agent Status Protocol

Return status footer as LAST output:
```
---
STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
CONCERNS: [list concerns, only if DONE_WITH_CONCERNS]
BLOCKED_ON: [description, only if BLOCKED]
CONTEXT_NEEDED: [what's missing, only if NEEDS_CONTEXT]
---
```

## Exit Criteria

- [ ] Summary presented (3-5 bullets)
- [ ] Living documents checklist evaluated (all applicable items addressed)
- [ ] build-state.yaml cleaned up
