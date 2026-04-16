/**
 * emit.ts — ByteDigger structured observability event emission.
 *
 * Emits JSONL lines to stderr with [bytedigger:event] prefix.
 * Optionally forwards phase-start and phase-done events to HAL's forge/emit
 * script when HAL_DIR is set (fire-and-forget, synchronous with 500ms timeout cap).
 *
 * Contract: emitEvent() is void and NEVER throws. All errors are caught
 * and written as [bytedigger:emit-warn] lines. Gate correctness must not
 * depend on emission succeeding.
 *
 * Config dependency: reads BYTEDIGGER_CONFIG env var directly to check observability.enabled (independent of build-phase-gate.ts::loadConfig).
 * Tests can disable output by pointing BYTEDIGGER_CONFIG at a temp file with
 * { "observability": { "enabled": false } }.
 */

import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join } from "node:path";

export type EmitEventName =
  | "phase-start"
  | "phase-end"
  | "phase-skip"
  | "gate-result"
  | "build-complete";

export interface EmitPayload {
  readonly event: EmitEventName;
  readonly phase?: string;
  readonly status?: string;
  readonly duration_ms?: number;
  readonly metadata?: Record<string, unknown>;
  readonly timestamp: string;
}

export interface EmitOptions {
  readonly disableHal?: boolean;
}

function nowIso(): string {
  return new Date().toISOString();
}

function isObservabilityEnabled(): boolean {
  try {
    const cfgPath = process.env.BYTEDIGGER_CONFIG;
    if (!cfgPath) return true;
    if (!existsSync(cfgPath)) return true;
    const parsed = JSON.parse(readFileSync(cfgPath, "utf8")) as Record<string, unknown>;
    const obs = parsed.observability as Record<string, unknown> | undefined;
    if (obs === undefined) return true;
    const enabled = obs.enabled;
    if (enabled === false || enabled === "false") return false;
    return true;
  } catch (err) {
    try {
      process.stderr.write(`[bytedigger:emit-warn] isObservabilityEnabled failed, defaulting to true: ${err instanceof Error ? err.message : String(err)}\n`);
    } catch { /* EPIPE */ }
    return true;
  }
}

function writeWarn(msg: string): void {
  try {
    process.stderr.write(`[bytedigger:emit-warn] ${msg}\n`);
  } catch {
    // Last resort: if stderr itself is broken (EPIPE, EAGAIN, or any other error), there is nowhere to report. Swallow intentionally.
  }
}

export function emitEvent(
  payload: Omit<EmitPayload, "timestamp">,
  options?: EmitOptions,
): void {
  try {
    if (!isObservabilityEnabled()) return;

    const timestamp = nowIso();

    const jsonObj: Record<string, unknown> = { event: payload.event };
    if (payload.phase !== undefined) jsonObj.phase = payload.phase;
    if (payload.status !== undefined) jsonObj.status = payload.status;
    if (payload.duration_ms !== undefined) jsonObj.duration_ms = payload.duration_ms;
    if (payload.metadata !== undefined && Object.keys(payload.metadata).length > 0) {
      jsonObj.metadata = payload.metadata;
    }
    jsonObj.timestamp = timestamp;

    let jsonStr: string;
    try {
      jsonStr = JSON.stringify(jsonObj);
    } catch (jsonErr) {
      delete jsonObj.metadata;
      jsonStr = JSON.stringify(jsonObj);
      writeWarn(`JSON.stringify failed for metadata: ${jsonErr instanceof Error ? jsonErr.message : String(jsonErr)}`);
    }

    try {
      process.stderr.write(`[bytedigger:event] ${jsonStr}\n`);
    } catch {
      // EPIPE — stderr broken, continue to HAL subprocess attempt
    }

    const halDir = process.env.HAL_DIR;
    if (halDir && !(options?.disableHal)) {
      const emitScript = join(halDir, "SYSTEM", "cli", "forge", "emit");
      let halCmd: string | null = null;

      if (payload.event === "phase-start" && payload.phase) {
        halCmd = "phase-start";
      } else if (payload.event === "phase-end" && payload.status === "pass" && payload.phase) {
        halCmd = "phase-done";
      }

      if (halCmd !== null) {
        try {
          const result = spawnSync("bash", [emitScript, halCmd, payload.phase!], {
            timeout: 500,
            stdio: "ignore",
          });
          if (result.status !== 0 || result.error) {
            const reason = result.error
              ? result.error.message
              : `exit ${String(result.status ?? "unknown")}`;
            writeWarn(`HAL fork failed: ${reason}`);
          }
        } catch (spawnErr) {
          writeWarn(`HAL fork failed: ${spawnErr instanceof Error ? spawnErr.message : String(spawnErr)}`);
        }
      }
    }
  } catch (outerErr) {
    writeWarn(`emitEvent catch-all: ${outerErr instanceof Error ? outerErr.message : String(outerErr)}`);
  }
}

export function emitPhaseStart(phase: string, metadata?: Record<string, unknown>): void {
  emitEvent({ event: "phase-start", phase, metadata });
}

export function emitPhaseEnd(
  phase: string,
  status: "pass" | "block",
  duration_ms: number,
  metadata?: Record<string, unknown>,
): void {
  emitEvent({ event: "phase-end", phase, status, duration_ms, metadata });
}

export function emitPhaseSkip(phase: string, reason?: string): void {
  emitEvent({
    event: "phase-skip",
    phase,
    metadata: reason !== undefined ? { reason } : undefined,
  });
}

export function emitGateResult(
  phase: string,
  verdict: "pass" | "soft-block" | "hard-block",
  metadata?: Record<string, unknown>,
): void {
  emitEvent({ event: "gate-result", phase, status: verdict, metadata });
}

export function emitBuildComplete(
  complexity: string,
  duration_ms?: number,
  metadata?: Record<string, unknown>,
): void {
  emitEvent({
    event: "build-complete",
    status: complexity,
    duration_ms,
    metadata,
  });
}
