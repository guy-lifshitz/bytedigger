# Phase 8: Post-Deploy Housekeeping

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

This script runs three steps, all informational (never blocks):

### Security Scan
- Runs `gitleaks detect` and `trivy fs` if installed
- Writes `phase_8_security_scan: pass|fail|skipped` to state
- Findings are logged, never block the pipeline

### SBOM Generation
- Runs `trivy fs --format cyclonedx` if installed
- Output: `.bytedigger/sbom.cdx.json` (CycloneDX standard)
- Writes `phase_8_sbom: generated|skipped` to state

### Cleanup
- Prunes gone branches (tracking deleted remotes)
- Removes temp files (`.bytedigger-*` older than 24h)
- Removes merged worktrees
- Writes `phase_8_cleanup: complete` to state

## 8.2 Report

Log summary:
```
Phase 8: security_scan=[pass|fail|skipped] sbom=[generated|skipped] cleanup=complete
```

## State Log

```yaml
phase_8_security_scan: pass|fail|skipped
phase_8_sbom: generated|skipped
phase_8_cleanup: complete
```
