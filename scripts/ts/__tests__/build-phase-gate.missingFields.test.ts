/**
 * build-phase-gate.missingFields.test.ts — RED tests E12 and E14.
 *
 * Tests dispatcher integration: verifies that missingFields from
 * runGlobalPrePhaseChecks() are surfaced (or omitted) in the phase-end
 * JSONL event emitted by dispatchPhase().
 *
 * All tests WILL FAIL until build-phase-gate.ts is wired to pass
 * missingFields into emitPhaseEnd metadata (per build-spec.md §Files).
 *
 * Strategy: real tmpdir setup that produces a stale artifact (BYPASS #12)
 * which causes runGlobalPrePhaseChecks to return non-empty missingFields.
 * The stderr spy captures [bytedigger:event] JSONL output from emitPhaseEnd.
 */

import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { dispatchPhase } from "../build-phase-gate.ts";

// ---------------------------------------------------------------------------
// Stderr spy infrastructure — mirrors emit.test.ts pattern
// ---------------------------------------------------------------------------

let capturedWrites: string[] = [];
let originalWrite: typeof process.stderr.write;

function installStderrSpy(): void {
  capturedWrites = [];
  originalWrite = process.stderr.write.bind(process.stderr);
  // @ts-ignore — intentional monkey-patch for test isolation
  process.stderr.write = (chunk: unknown): boolean => {
    capturedWrites.push(String(chunk));
    return true;
  };
}

function uninstallStderrSpy(): void {
  // @ts-ignore — restore original
  process.stderr.write = originalWrite;
}

function getEventLines(): string[] {
  return capturedWrites.filter((l) => l.startsWith("[bytedigger:event]"));
}

function parseEventLine(line: string): Record<string, unknown> {
  const jsonPart = line.replace(/^\[bytedigger:event\]\s+/, "");
  return JSON.parse(jsonPart) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Shared test setup helpers
// ---------------------------------------------------------------------------

let dir: string;
const savedCwd = process.cwd();

function writeState(fields: Record<string, string>): void {
  const lines = Object.entries(fields).map(([k, v]) =>
    /^(true|false|\d+(\.\d+)?)$/.test(v) ? `${k}: ${v}` : `${k}: "${v}"`,
  );
  writeFileSync(join(dir, "build-state.yaml"), lines.join("\n") + "\n");
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

/**
 * Sets up a phase-5 FEATURE session with a stale build-architecture.md
 * artifact, so runGlobalPrePhaseChecks() returns missingFields containing
 * the stale artifact message (BYPASS #12).
 * Phase 5 has a gate that checks plan_review; we set it to "approved" so
 * the phase verdict itself is "pass" — triggering the global-merge soft-block
 * arm at line 767 of build-phase-gate.ts (global.missingFields.length > 0
 * AND verdict.decision === "pass").
 */
function setupStaleArtifactSession(): void {
  writeState({
    task: "test-task",
    complexity: "FEATURE",
    mode: "AUTONOMOUS",
    current_phase: "5",
    last_updated: nowIso(),
    plan_review: "approved",
  });

  // Write a stale build-architecture.md (mtime in the past)
  const staleArtifact = join(dir, "build-architecture.md");
  writeFileSync(staleArtifact, "# Architecture\n");
  const past = new Date(Date.now() - 3600 * 1000);
  utimesSync(staleArtifact, past, past);

  // Touch build-state.yaml to be newer than the artifact
  const now = new Date();
  utimesSync(join(dir, "build-state.yaml"), now, now);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("F7 — build-phase-gate dispatcher: missingFields in phase-end JSONL (E12, E14)", () => {
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "bpg-missingFields-"));
    process.chdir(dir);
    installStderrSpy();
  });

  afterEach(() => {
    uninstallStderrSpy();
    process.chdir(savedCwd);
    rmSync(dir, { recursive: true, force: true });
  });

  test("E14 — dispatcher emits missingFields in phase-end JSONL when runGlobalPrePhaseChecks returns them", () => {
    // Arrange: stale artifact triggers runGlobalPrePhaseChecks to return non-empty missingFields
    setupStaleArtifactSession();

    // Act: dispatch the phase — this internally calls runGlobalPrePhaseChecks
    dispatchPhase({ cwd: dir });

    // Assert: the phase-end JSONL event contains metadata.missingFields
    const eventLines = getEventLines();
    const phaseEndLine = eventLines.find((l) => l.includes('"event":"phase-end"'));
    expect(phaseEndLine).toBeDefined();

    const payload = parseEventLine(phaseEndLine!);
    const metadata = payload["metadata"] as Record<string, unknown> | undefined;
    expect(metadata).toBeDefined();
    expect(Array.isArray(metadata!["missingFields"])).toBe(true);
    // The stale artifact name must appear in missingFields
    const missingFields = metadata!["missingFields"] as string[];
    expect(missingFields.length).toBeGreaterThan(0);
    expect(missingFields.some((f) => f.includes("build-architecture.md"))).toBe(true);
  });

  test("E12 — dispatcher does NOT emit missingFields in phase-end JSONL when runGlobalPrePhaseChecks returns empty missingFields", () => {
    // Arrange: clean session — no stale artifacts, so runGlobalPrePhaseChecks
    // returns missingFields: [] (empty). We use phase "1" which is in the
    // default arm (phases 0,1,2,3,8) — dispatchPhase returns pass early
    // without running any phase-specific gate, so global.missingFields stays [].
    writeState({
      task: "test-task",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "1",
      last_updated: nowIso(),
    });

    // Act
    dispatchPhase({ cwd: dir });

    // Assert: phase-end event exists but metadata does NOT contain missingFields key
    const eventLines = getEventLines();
    const phaseEndLine = eventLines.find((l) => l.includes('"event":"phase-end"'));
    expect(phaseEndLine).toBeDefined();

    const payload = parseEventLine(phaseEndLine!);
    // Either metadata is absent entirely (no fields) or does not have missingFields
    const metadata = payload["metadata"] as Record<string, unknown> | undefined;
    if (metadata !== undefined) {
      expect(Object.prototype.hasOwnProperty.call(metadata, "missingFields")).toBe(false);
    }
    // If metadata is undefined, the key is certainly absent — test passes
  });
});
