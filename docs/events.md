# ByteDigger Observability Events

## Overview

ByteDigger's gate engine emits structured JSONL events to stderr, providing real-time observability into pipeline phase transitions and gate verdicts. Events are prefixed with `[bytedigger:event]` and follow a consistent schema.

**Key Properties:**

- **JSONL Format:** Each event is one complete JSON object per line
- **Stderr Destination:** All events written to stderr (not stdout)
- **Never-Throw Contract:** Event emission is void and swallows all errors. Gate correctness never depends on emission succeeding. Errors are logged as `[bytedigger:emit-warn]` lines (distinct from event lines).
- **Config-Controlled:** Disabled via `observability.enabled=false` in `bytedigger.json` (default: true)
- **HAL-Forwarded:** Phase-start and phase-done events optionally forwarded to HAL subprocess when `HAL_DIR` env var is set

## Event Types

Five event types represent the full build lifecycle:

| Event | Purpose | Emitted When |
|-------|---------|--------------|
| `phase-start` | Phase began executing | dispatchPhase entry (after global pre-phase checks) |
| `phase-end` | Phase completed with verdict | dispatchPhase exit (before returning verdict) |
| `phase-skip` | Phase skipped due to complexity/tier | SIMPLE tier skips phases 2–4; TRIVIAL tier skips phase 6 |
| `gate-result` | Gate verdict (pass/block) | After phase-specific checks complete |
| `build-complete` | Build finished (success/failure/fatal) | mainCLI exit (all phases done or error caught) |

## Common Payload Schema

All events follow this TypeScript interface:

```typescript
interface EmitPayload {
  readonly event: EmitEventName;
  readonly phase?: string;
  readonly status?: string;
  readonly duration_ms?: number;
  readonly metadata?: Record<string, unknown>;
  readonly timestamp: string;
}
```

- **event** (required) — Event type string (phase-start, phase-end, …)
- **phase** (optional) — Phase identifier (0, 0.5, 1, 2, …, 7, 8)
- **status** (optional) — Context-dependent string (gate verdict, complexity tier, or skip reason)
- **duration_ms** (optional) — Elapsed time in milliseconds
- **metadata** (optional) — Key-value pairs for contextual details
- **timestamp** (required) — ISO 8601 UTC timestamp

## Per-Event Schema

### phase-start

Emitted when a phase begins (after global pre-phase checks).

**Payload:**

```json
{
  "event": "phase-start",
  "phase": "5",
  "timestamp": "2026-04-16T14:23:45.123Z"
}
```

**Fields:**

- `phase` — Phase being started (e.g., "5", "0.5", "6")
- No `status`, `duration_ms`, or `metadata` (phase-start captures entry point)

---

### phase-end

Emitted when a phase completes (all checks done, verdict determined).

**Payload (Pass):**

```json
{
  "event": "phase-end",
  "phase": "5",
  "status": "pass",
  "duration_ms": 2341,
  "timestamp": "2026-04-16T14:26:26.456Z"
}
```

**Payload (Block):**

```json
{
  "event": "phase-end",
  "phase": "6",
  "status": "block",
  "duration_ms": 1205,
  "metadata": {
    "severity": "soft",
    "reason": "phase_6_findings_fixed=0; "
  },
  "timestamp": "2026-04-16T14:28:01.789Z"
}
```

**Fields:**

- `phase` — Phase that completed
- `status` — "pass" or "block"
- `duration_ms` — Milliseconds from dispatchPhase entry to verdict
- `metadata` (optional) — severity ("soft" or "hard"), reason, source (global/global-merge)

---

### phase-skip

Emitted when a phase is skipped (not executed due to complexity tier or build mode).

**Payload:**

```json
{
  "event": "phase-skip",
  "phase": "2",
  "metadata": {
    "reason": "SIMPLE complexity skips exploratory phases"
  },
  "timestamp": "2026-04-16T14:23:50.234Z"
}
```

**Fields:**

- `phase` — Phase being skipped
- `metadata.reason` (optional) — Explanation (e.g., "SIMPLE complexity", "TRIVIAL skip")

---

### gate-result

Emitted after phase-specific gate checks (before global-merge, before duration calculation on phase-end).

**Payload (Pass):**

```json
{
  "event": "gate-result",
  "phase": "5.1",
  "status": "pass",
  "timestamp": "2026-04-16T14:24:12.567Z"
}
```

**Payload (Soft Block):**

```json
{
  "event": "gate-result",
  "phase": "4.5",
  "status": "soft-block",
  "metadata": {
    "reason": "plan_review=pass; "
  },
  "timestamp": "2026-04-16T14:24:45.890Z"
}
```

**Payload (Hard Block):**

```json
{
  "event": "gate-result",
  "phase": "5.3",
  "status": "hard-block",
  "metadata": {
    "source": "global",
    "reason": "complexity downgrade detected"
  },
  "timestamp": "2026-04-16T14:24:50.123Z"
}
```

**Fields:**

- `phase` — Phase whose gate was evaluated
- `status` — "pass", "soft-block", or "hard-block"
- `metadata` (optional) — reason, source (global/global-merge), severity (soft/hard)

---

### build-complete

Emitted at mainCLI exit (final outcome of entire build).

**Payload (Pass):**

```json
{
  "event": "build-complete",
  "status": "FEATURE",
  "duration_ms": 1234567,
  "metadata": {
    "phase": "7",
    "outcome": "pass"
  },
  "timestamp": "2026-04-16T14:45:30.456Z"
}
```

**Payload (Hard Block):**

```json
{
  "event": "build-complete",
  "status": "SIMPLE",
  "duration_ms": 456789,
  "metadata": {
    "phase": "5",
    "outcome": "hard-block",
    "reason": "phase_53_green not complete"
  },
  "timestamp": "2026-04-16T14:52:00.111Z"
}
```

**Payload (Fatal):**

```json
{
  "event": "build-complete",
  "status": "UNKNOWN",
  "metadata": {
    "outcome": "fatal",
    "error": "failed to parse build-state.yaml"
  },
  "timestamp": "2026-04-16T14:52:10.222Z"
}
```

**Fields:**

- `status` — Complexity tier (SIMPLE, FEATURE, COMPLEX, TRIVIAL) or "UNKNOWN" on fatal
- `duration_ms` (optional) — Total build time in milliseconds (missing on fatal)
- `metadata.outcome` — "pass", "soft-block", "soft-bypass", "hard-block", or "fatal"
- `metadata.phase` — Last phase executed before completion
- `metadata.reason` (optional) — Block reason string
- `metadata.error` (optional) — Fatal error message

## Metadata Vocabulary

Metadata objects contain contextual fields depending on event type. Common keys:

| Key | Values | Meaning |
|-----|--------|---------|
| `source` | "global", "global-merge" | Where gate verdict originated (global pre-phase checks vs. phase-specific + global merge) |
| `reason` | string | Human-readable gate failure reason |
| `severity` | "soft", "hard" | Block severity (soft = 2-point gate can retry, hard = 1-point terminal) |
| `outcome` | "pass", "soft-block", "soft-bypass", "hard-block", "fatal" | Build final outcome |
| `missing` | array of strings | Missing field errors (phase checks) |
| `error` | string | Exception message (fatal blocks) |

## Configuration

### Enabling/Disabling Events

Events are enabled by default. Control via `bytedigger.json`:

```json
{
  "observability": {
    "enabled": true
  }
}
```

Set `enabled: false` to suppress all event emission.

### HAL Forwarding

When `HAL_DIR` environment variable is set, the gate engine also forwards events to HAL's forge emit subprocess:

```bash
export HAL_DIR=/path/to/hal
bun run gate ...
```

Forwarded events:
- **phase-start** → Calls `$HAL_DIR/SYSTEM/cli/forge/emit phase-start <phase>`
- **phase-end (pass)** → Calls `$HAL_DIR/SYSTEM/cli/forge/emit phase-done <phase>`

Other event types are not forwarded. Forwarding is **lossy by design**: only phase-start and successful phase-end (pass verdict) reach HAL; blocks, skips, and gate-result are internal to ByteDigger.

**Timeout:** Each HAL subprocess invocation has a 500ms timeout cap. If the subprocess hangs or fails, a `[bytedigger:emit-warn]` message is logged but the build continues — HAL subprocess failures never block the gate.

## Wiring Architecture

Events are emitted at 12 wire points in `scripts/ts/build-phase-gate.ts`. Each point captures a discrete phase transition or gate decision:

1. **dispatchPhase entry** (line 704) — `emitPhaseStart(phase)` before global pre-phase checks
2. **Global hard-block** (line 709) — `emitGateResult(phase, "hard-block", { source: "global", reason })` if complexity downgrade or artifact freshness failed
3. **Early-pass phases** (line 758) — `emitPhaseEnd(phase, "pass", ...)` for phases 0, 1, 2, 3, 8 (no phase-specific checks)
4. **Phase check success** (line 784) — `emitGateResult(phase, "pass")` after phase-specific checks pass
5. **Phase-end on pass** (line 785) — `emitPhaseEnd(phase, "pass", ...)` after gate-result emission
6. **Soft-merge** (line 768 & 776) — `emitGateResult(phase, "soft-block", { source: "global-merge", missing })` when phase passes but global has soft findings
7. **Phase-end on soft-merge** (line 769 & 777) — `emitPhaseEnd(phase, "block", ..., { severity: "soft" })` after soft-merge emission
8. **Checkphase6 skip** (line 645) — `emitPhaseSkip("6", "semantic-skip")` when Boy Scout Rule is violated
9. **Phase-end on soft-block** (line 788) — `emitPhaseEnd(phase, "block", ..., { severity: "soft" })` on phase-specific soft block
10. **Phase-end on hard-block** (line 791) — `emitPhaseEnd(phase, "block", ..., { severity: "hard" })` on phase-specific hard block
11. **mainCLI build-complete (pass)** (line 948) — `emitBuildComplete(complexity, duration, { phase, outcome: "pass" })` on pass verdict
12. **mainCLI build-complete (block/fatal)** (lines 955, 962, 965, 975) — `emitBuildComplete(complexity, duration, { outcome })` on soft-block, soft-bypass, hard-block, or fatal

Each wire point ensures:
- Phase context (phase string) is captured at the right time
- Duration is measured from dispatchPhase entry (or mainCLI entry for build-complete)
- Global vs. phase-specific origin is distinguished
- Severity (soft/hard) is documented in metadata
- Events are emitted **after** verdicts are finalized (no mid-flight emissions)

## Never-Throw Contract

The `emitEvent()` function (and all higher-level emit functions) never throw. All errors are caught and logged as `[bytedigger:emit-warn]` lines:

```
[bytedigger:emit-warn] isObservabilityEnabled failed, defaulting to true: ENOENT /path/to/config
[bytedigger:emit-warn] JSON.stringify failed for metadata: circular reference in metadata object
[bytedigger:emit-warn] HAL fork failed: timeout
```

These warnings are distinct from `[bytedigger:event]` lines. Consumers parsing events should:

1. Filter by `^\[bytedigger:event\]` to extract events
2. Use a separate filter for `^\[bytedigger:emit-warn\]` if diagnostics are needed
3. Never fail the build if event emission fails

This contract ensures gate verdicts are **always** emitted to stdout (JSON block/pass), independent of stderr observability success.

## Consumer Examples

### Extract and parse all events

```bash
bun run gate ... 2>&1 | grep '^\[bytedigger:event\]' | sed 's/^\[bytedigger:event\] //' | jq
```

### Filter events by type

```bash
bun run gate ... 2>&1 | grep '^\[bytedigger:event\]' | sed 's/^\[bytedigger:event\] //' | jq 'select(.event == "phase-end")'
```

### Extract phase durations

```bash
bun run gate ... 2>&1 | grep '^\[bytedigger:event\]' | sed 's/^\[bytedigger:event\] //' | \
  jq -s 'map(select(.event == "phase-end") | {phase, status, duration_ms})'
```

### Monitor build progress in real-time

```bash
bun run gate ... 2>&1 | while IFS= read -r line; do
  if [[ $line =~ ^\[bytedigger:event\] ]]; then
    json="${line#\[bytedigger:event\] }"
    phase=$(echo "$json" | jq -r '.phase // "global"')
    event=$(echo "$json" | jq -r '.event')
    echo "[$(date +%H:%M:%S)] $phase / $event"
  fi
done
```

## Out-of-Scope (Sprint C)

The following enhancements are documented in agreement **94AF6D1F** (Sprint C backlog):

- **Narrower metadata types** — `Record<string, unknown>` will be split into event-specific TypeScript types (e.g., `PhaseEndMetadata`, `GateResultMetadata`) for better IDE support and schema validation
- **global.missingFields enrichment** — Missing field entries will include field type, expected value, and actual value for easier debugging
- **emitVerdict helper** — A convenience function to emit gate-result + phase-end atomically, reducing wiring code duplication

See MEMORY.md or `bash SYSTEM/cli/agreements/show 94AF6D1F` for details.

---

## PhaseEndMetadata & missingFields (addendum)

### `missingFields` on `phase-end` metadata

When `runGlobalPrePhaseChecks()` returns a non-empty `missingFields` array, the dispatcher propagates it into the `phase-end` event `metadata` under the key `missingFields`:

```json
{"event":"phase-end","phase":"5","status":"block","duration_ms":36,"metadata":{"severity":"soft","source":"global-merge","missingFields":["billing.plan","user.tier"]},"timestamp":"..."}
```

Key properties:
- `missingFields` is emitted only when the array is non-empty. The `global.missingFields.length > 0` guard inside `dispatchPhase` ensures empty arrays are never serialized.
- This key exists on `phase-end` metadata only. It enables a single `jq` filter on `phase-end` without having to correlate with the preceding `gate-result` event.
- See `PhaseEndMetadata` in `scripts/ts/lib/emit.ts` for the authoritative type.

### `missing` on `gate-result` is UNCHANGED

The `missing` key on `gate-result` events (emitted via `emitGateResult`) is **not renamed** and remains `missing`. Both keys coexist:

| Event | Key | Type |
|---|---|---|
| `gate-result` | `missing` | `string[]` |
| `phase-end` | `metadata.missingFields` | `string[]` |

Do not conflate `gate-result.missing` with `phase-end.metadata.missingFields` — they carry the same field names but are on separate event types. The naming divergence is a known wart that will not be reconciled because `gate-result.missing` is a stable public key.

### `emitVerdict` → `emitGateResult` (spec term mapping)

The spec term `emitVerdict` used in earlier design documents maps to the existing implementation symbol `emitGateResult` (exported from `scripts/ts/lib/emit.ts`). No rename or new function was introduced.

`emitGateResult` is invoked in `dispatchPhase` at every terminal branch — hard-block (global and phase-level), soft-block (global-merge and phase-level), and pass — and nowhere else.
