# Phase 8: Post-Deploy Cleanup

**State update:** `current_phase: "8"`

## Entry Gate

Verify build-state.yaml contains:
- `review_complete: pass`

If missing → skip Phase 8 (pipeline incomplete, nothing to clean up).

## 8.1 Run Post-Deploy

Execute the post-deploy script:

```
bash scripts/post-deploy.sh --cwd "$(pwd)" --state-file ./build-state.yaml
```

This script runs cleanup, informational only (never blocks):

### Cleanup
- Prunes gone branches (tracking deleted remotes)
- Removes temp files (`.bytedigger-*` older than 24h)
- Removes merged worktrees
- Writes `phase_8_cleanup: complete` to state

## 8.2 Report

Log summary:
```
Phase 8: cleanup=complete
```

## State Log

```yaml
phase_8_cleanup: complete
```
