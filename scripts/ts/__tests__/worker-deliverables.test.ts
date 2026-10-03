// bd#127 AC4 + AC5 (TS gate): workers write their own deliverables, the gate
// checks them on disk. Bash twin is covered by tests/test_worker_deliverables.py.
// Shadow mode compares stdout byte-for-byte, so reason strings are asserted exactly.
// bd#89 P3c: phase 7 no longer has a worker deliverable (learnings-raw.md retired);
// the phase 7 gate checks review_complete only.
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { dispatchPhase } from "../build-phase-gate.ts";
import type { GateVerdict } from "../build-phase-gate.ts";

let dir: string;
let scratch: string;
const savedCwd = process.cwd();
const savedConfig = process.env.BYTEDIGGER_CONFIG;

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

function writeState(fields: Record<string, string>): void {
  const lines = Object.entries(fields).map(([k, v]) =>
    /^(true|false|\d+(\.\d+)?)$/.test(v) ? `${k}: ${v}` : `${k}: "${v}"`,
  );
  writeFileSync(join(dir, "build-state.yaml"), lines.join("\n") + "\n");
}

function phase7State(extra: Record<string, string> = {}): void {
  writeState({
    task: "x",
    complexity: "FEATURE",
    mode: "AUTONOMOUS",
    current_phase: "7",
    last_updated: nowIso(),
    scratchpad_dir: scratch,
    ...extra,
  });
}

function softReason(v: GateVerdict): string {
  expect(v.decision).toBe("block");
  if (v.decision !== "block") throw new Error("unreachable");
  expect(v.severity).toBe("soft");
  expect(v.exit_code).toBe(2);
  return v.reason;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "worker-deliverables-"));
  scratch = join(dir, "scratch");
  mkdirSync(scratch, { recursive: true });
  delete process.env.BYTEDIGGER_CONFIG;
  process.chdir(dir);
});

afterEach(() => {
  process.chdir(savedCwd);
  if (savedConfig === undefined) delete process.env.BYTEDIGGER_CONFIG;
  else process.env.BYTEDIGGER_CONFIG = savedConfig;
  rmSync(dir, { recursive: true, force: true });
});

// bd#89 P2a: the AC4 block (phase 4 gate: findings / approach deliverables) is
// retired together with phases 1-4. bd#89 P3c: the AC5 learnings-raw.md deliverable
// block is retired; the cases below pin the review_complete-only phase 7 gate.

describe("phase 7 gate: review_complete only (no deliverable)", () => {
  test("review_complete pass + no learnings-raw.md -> pass", () => {
    phase7State({ review_complete: "pass" });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });

  // joinMissing parity: the reason ends with "; " like every other joinMissing phase.
  test("review_complete missing -> reason is the review_complete entry only", () => {
    phase7State();
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe("review_complete=pass (got: <missing>); ");
  });

  test("phase 7 without scratchpad_dir, review_complete pass -> pass", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
      review_complete: "pass",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });

  test("TRIVIAL without review_complete -> pass (not checked)", () => {
    phase7State({ complexity: "TRIVIAL" });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });
});

describe("Rev 3 — C2 spaced scratchpad path", () => {
  test("C2 guard — phase 7 with spaced scratchpad path, review_complete pass -> pass", () => {
    scratch = join(dir, "my scratch");
    mkdirSync(scratch, { recursive: true });
    phase7State({ review_complete: "pass" });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });
});
