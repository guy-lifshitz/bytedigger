/**
 * emit.test.ts — RED tests for F7 (Observability Events)
 *
 * All tests WILL FAIL until scripts/ts/lib/emit.ts is implemented.
 * Tests verify spec §6 Group 1 (E1-E10).
 *
 * Strategy: spy on process.stderr.write to capture output; no temp files needed.
 */

import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  emitEvent,
  emitPhaseStart,
  emitPhaseEnd,
  emitPhaseSkip,
  emitGateResult,
  emitBuildComplete,
} from "../lib/emit.ts";
import type { PhaseEndMetadata } from "../lib/emit.ts";

// ---------------------------------------------------------------------------
// Spy infrastructure — capture stderr writes per-test
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
// Tests
// ---------------------------------------------------------------------------

describe("F7 — emit.ts: JSONL stderr output", () => {
  beforeEach(() => {
    installStderrSpy();
  });

  afterEach(() => {
    uninstallStderrSpy();
  });

  test("E1 — emitEvent writes exactly one JSONL line to stderr", () => {
    emitEvent({ event: "phase-start", phase: "6" }, { disableHal: true });

    const lines = getEventLines();
    expect(lines).toHaveLength(1);
    // Must be valid JSON after stripping prefix
    const json = parseEventLine(lines[0]!);
    expect(json).toBeDefined();
  });

  test("E2 — output includes [bytedigger:event] prefix with trailing space", () => {
    emitEvent({ event: "gate-result", phase: "6", status: "pass" }, { disableHal: true });

    expect(capturedWrites.length).toBeGreaterThanOrEqual(1);
    const eventLine = capturedWrites.find((l) => l.startsWith("[bytedigger:event] "));
    expect(eventLine).toBeTruthy();
  });

  test("E3 — timestamp field is ISO8601 UTC (millisecond precision)", () => {
    emitEvent({ event: "phase-end", phase: "4", status: "pass", duration_ms: 42 }, { disableHal: true });

    const lines = getEventLines();
    expect(lines).toHaveLength(1);
    const json = parseEventLine(lines[0]!);
    const ts = json["timestamp"] as string;
    expect(ts).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$/);
  });

  test("E4 — emitPhaseStart emits event='phase-start' with correct phase", () => {
    emitPhaseStart("6");

    const lines = getEventLines();
    expect(lines).toHaveLength(1);
    const json = parseEventLine(lines[0]!);
    expect(json["event"]).toBe("phase-start");
    expect(json["phase"]).toBe("6");
  });

  test("E5 — emitPhaseEnd emits event='phase-end' with duration_ms and status", () => {
    emitPhaseEnd("4", "pass", 123);

    const lines = getEventLines();
    expect(lines).toHaveLength(1);
    const json = parseEventLine(lines[0]!);
    expect(json["event"]).toBe("phase-end");
    expect(json["status"]).toBe("pass");
    expect(typeof json["duration_ms"]).toBe("number");
    expect(json["duration_ms"] as number).toBeGreaterThanOrEqual(0);
  });

  test("E6 — emitPhaseSkip emits event='phase-skip'", () => {
    emitPhaseSkip("2", "early-phase");

    const lines = getEventLines();
    expect(lines).toHaveLength(1);
    const json = parseEventLine(lines[0]!);
    expect(json["event"]).toBe("phase-skip");
    expect(json["phase"]).toBe("2");
  });

  test("E7 — emitEvent never throws even if stderr.write throws (EPIPE)", () => {
    // Override spy to throw EPIPE
    // @ts-ignore
    process.stderr.write = (): never => {
      throw new Error("EPIPE: broken pipe");
    };

    // Must not throw — emitEvent is a no-throw contract
    expect(() => {
      emitEvent({ event: "phase-start", phase: "1" }, { disableHal: true });
    }).not.toThrow();
  });
});

describe("F7 — emit.ts: HAL integration opt-out", () => {
  const savedHalDir = process.env.HAL_DIR;

  beforeEach(() => {
    installStderrSpy();
  });

  afterEach(() => {
    uninstallStderrSpy();
    if (savedHalDir === undefined) {
      delete process.env.HAL_DIR;
    } else {
      process.env.HAL_DIR = savedHalDir;
    }
  });

  test("E8 — disableHal:true suppresses HAL subprocess even when HAL_DIR is set", () => {
    process.env.HAL_DIR = "/tmp/fake-hal-dir-does-not-exist";

    emitEvent({ event: "phase-start", phase: "6" }, { disableHal: true });

    // No [bytedigger:emit-warn] should appear — HAL fork was suppressed
    const warnLines = capturedWrites.filter((l) => l.includes("[bytedigger:emit-warn]"));
    expect(warnLines).toHaveLength(0);
    // Event line still written
    const eventLines = getEventLines();
    expect(eventLines).toHaveLength(1);
  });

  test("E9 — missing HAL_DIR → stderr-only, no subprocess spawned (no emit-warn)", () => {
    delete process.env.HAL_DIR;

    emitEvent({ event: "phase-start", phase: "4" }, { disableHal: false });

    // No emit-warn lines (no subprocess attempted without HAL_DIR)
    const warnLines = capturedWrites.filter((l) => l.includes("[bytedigger:emit-warn]"));
    expect(warnLines).toHaveLength(0);
    // Event line written normally
    expect(getEventLines()).toHaveLength(1);
  });
});

describe("F7 — emit.ts: observability.enabled=false suppresses output", () => {
  const savedConfig = process.env.BYTEDIGGER_CONFIG;

  beforeEach(() => {
    installStderrSpy();
  });

  afterEach(() => {
    uninstallStderrSpy();
    if (savedConfig === undefined) {
      delete process.env.BYTEDIGGER_CONFIG;
    } else {
      process.env.BYTEDIGGER_CONFIG = savedConfig;
    }
  });

  test("E10 — observability.enabled:false in config suppresses all [bytedigger:event] lines", () => {
    const cfgDir = mkdtempSync(join(tmpdir(), "emit-cfg-"));
    const cfgPath = join(cfgDir, "bytedigger.json");
    writeFileSync(cfgPath, JSON.stringify({ observability: { enabled: false } }));
    process.env.BYTEDIGGER_CONFIG = cfgPath;

    try {
      emitEvent({ event: "phase-start", phase: "6" }, { disableHal: true });

      // observability disabled — no [bytedigger:event] lines expected
      const eventLines = getEventLines();
      expect(eventLines).toHaveLength(0);
    } finally {
      rmSync(cfgDir, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// E11, E13 — PhaseEndMetadata typed metadata tests
// ---------------------------------------------------------------------------

describe("F7 — emit.ts: PhaseEndMetadata typed metadata (E11, E13)", () => {
  beforeEach(() => {
    installStderrSpy();
  });

  afterEach(() => {
    uninstallStderrSpy();
  });

  test("E11 — emitPhaseEnd JSONL includes missingFields when metadata has non-empty missingFields", () => {
    emitPhaseEnd("test-phase", "block", 100, {
      severity: "soft",
      missingFields: ["fieldA", "fieldB"],
    });

    const lines = getEventLines();
    expect(lines).toHaveLength(1);
    const payload = parseEventLine(lines[0]!);
    const metadata = payload["metadata"] as Record<string, unknown>;
    expect(Array.isArray(metadata["missingFields"])).toBe(true);
    expect(metadata["missingFields"]).toEqual(["fieldA", "fieldB"]);
  });

  test("E13 — PhaseEndMetadata rejects invalid severity at compile time (runtime shape assertion)", () => {
    // @ts-expect-error: "medium" is not assignable to PhaseEndSeverity
    const badMeta: PhaseEndMetadata = { severity: "medium" };
    // Runtime assertion: valid shapes still serialize correctly
    const goodMeta: PhaseEndMetadata = { severity: "soft", source: "test" };
    expect(goodMeta.severity).toBe("soft");
    // Suppress unused variable warning
    void badMeta;
  });
});
