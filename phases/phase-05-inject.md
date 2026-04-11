# Phase 0.5: Pre-Build Gate + Security Scan

**State update:** `current_phase: "0.5"`

## 0.5.1 Pre-Build Gate (MANDATORY — blocks pipeline)

Run the pre-build gate to enforce worktree policy and detect session collisions:

```
bash scripts/pre-build-gate.sh \
  --complexity "$COMPLEXITY" \
  --session-file .bytedigger-sessions.json
```

- **Exit 0** → proceed to next step
- **Exit 1** → STOP PIPELINE (non-negotiable). Show error to user.

Gate checks:
1. **Worktree enforcement:** FEATURE/COMPLEX on main/master = hard block. SIMPLE/TRIVIAL = warn only.
2. **Session collision:** Active build on same branch = hard block. Stale sessions (>24h) auto-cleaned.

## 0.5.2 Security Scan (background, non-blocking)

Get file list via git diff (available before build-spec exists):

```
FILES=$(git diff --name-only main...HEAD 2>/dev/null || git diff --name-only HEAD 2>/dev/null || echo "")
```

If no git diff available (fresh repo, no commits), scan all tracked files:
```
FILES=${FILES:-$(git ls-files 2>/dev/null || echo "")}
```

Run security scan in background:
```
bash scripts/security-scan.sh \
  --cwd "$(pwd)" \
  --files "$(echo "$FILES" | tr '\n' ',')" \
  --state-file ./build-state.yaml
```

Results written to build-state.yaml:
- `security_classification: HIGH|MEDIUM|LOW`
- `security_patterns_found: [categories]`

**HIGH** classification triggers:
- Security architect agent in Phase 4
- Security reviewer agent in Phase 6

## State Log

```yaml
pre_build_gate: pass
security_classification: HIGH|MEDIUM|LOW
security_patterns_found: [...]
```
