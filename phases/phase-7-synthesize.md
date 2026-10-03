> This is Phase 7 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 7: SYNTHESIZE

**First ACTION — Update current_phase:**
```bash
python3 -c "import os,re,datetime,pathlib;f=pathlib.Path('build-state.yaml');tmp=pathlib.Path('build-state.yaml.tmp');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"7\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);tmp.write_text(t);os.replace(tmp,f);print('current_phase → 7')"
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

**TRIVIAL bypass:** If `complexity: TRIVIAL` in `build-state.yaml`, skip both checks above — TRIVIAL builds do not run Phase 6 review or Phase 5 implementation, so neither field will be set. Proceed directly to synthesis and state cleanup.

If either field is missing (and complexity is NOT TRIVIAL) → **STOP. Previous phase incomplete.**

Document results and summarize the build.

**WORKER AGENT CONSTRAINTS (include in every agent prompt):**
- You are a worker inside /build pipeline. Your tools are read-only (Read/Glob/Grep) plus Write for the one deliverable path named in your prompt — write nothing else.
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

1. The engine writes `post-deploy/post-deploy-report.md` from the event log. There is no agent to launch and no deliverable to verify.

**Orchestrator flow:**
1. Read `mode` from build-state.yaml — strip surrounding quotes before comparing (`grep '^mode:' build-state.yaml | sed "s/^mode:[[:space:]]*//;s/^['\"]//;s/['\"]$//"`)
2. If mode == "AUTONOMOUS": log the report to scratchpad, proceed immediately to the next step — do NOT pause to present to user
3. If mode == "SUPERVISED": present summary to user (What was built + concerns), wait for acknowledgement, then proceed to the next step

2. Present summary (SUPERVISED only — see flow above):
   - What was built (3-5 bullets)
   - Key decisions and trade-offs

4. **Update documentation** (recommended):

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

## 7.5 SHIP Protocol (if --pr flag)

Runs BEFORE State Cleanup, so a refused ship leaves the state to resume from.

If `--pr` flag was passed in the build invocation:

1. Run: `bash scripts/ship.sh --pr --state ./build-state.yaml` (it runs `scripts/readiness check --stage ship` first: verdict + consumption, before any git mutation)
2. Verify: `ship_complete: true` exists in build-state.yaml
3. Log PR URL from `ship_pr_url` field
4. If ship.sh fails (exit non-zero):
   - **`required: true`** (readiness policy, read in Phase 0): STOP. Set `current_phase: awaiting_approval` and `awaiting_stage: ship`, keep `build-state.yaml` and `build-metadata.json`, skip State Cleanup. Print the verdict line from ship.sh's stderr and, for `no_spec_record` / `label_predates_spec`, the recovery: `scripts/readiness post --spec ./build-spec.md`, then a human adds `<label>`, then `/build continue` (re-runs only this SHIP step, then the rest of Phase 7).
   - **`required: false`**: log warning, continue synthesis (SHIP is best-effort, not a gate)

ship.sh ships every commit ahead of the base plus tracked changes (no hand-maintained file list needed), and writes the PR title and body from the spec and the review fields in `build-state.yaml`.

**State log:** `ship_complete: true|false | ship_pr_url: <url>`

## State Cleanup

- Delete `build-state.yaml` from CWD (build complete)
- Delete `build-tests.md` from CWD if it exists (Gherkin artifact, no longer needed)
- Delete `build-red-output.log` from CWD if it exists (TDD RED phase artifact)
- Delete `build-green-output.log` from CWD if it exists (TDD GREEN phase artifact)
- Delete `build-opus-validation.md` from CWD if it exists (Opus validation artifact)
- Delete `build-plan-review.md` from CWD if it exists (Phase 4.5 plan review artifact)
- Delete `build-*-cycle*.md` from CWD if it exists (per-cycle plan review / Opus validation copies)
- Delete `.bytedigger-sessions.json` from CWD if it exists (Phase 0.5 session file)
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
- [ ] build-state.yaml cleaned up (unless stopped awaiting approval)
