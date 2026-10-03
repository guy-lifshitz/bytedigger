// bd#127 AC4 + AC5 (TS gate): workers write their own deliverables, the gate
// checks them on disk. Bash twin is covered by tests/test_worker_deliverables.py.
// Shadow mode compares stdout byte-for-byte, so reason strings are asserted exactly.
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
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

function put(rel: string, content: string): void {
  const p = join(scratch, rel);
  mkdirSync(join(p, ".."), { recursive: true });
  writeFileSync(p, content);
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
// retired together with phases 1-4. Only the AC5 (phase 7) block remains.

describe("AC5 — phase 7 gate: learnings-raw.md deliverable", () => {
  test("AC5 — review_complete pass + learnings-raw.md missing → soft block, exact reason", () => {
    phase7State({ review_complete: "pass" });
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe(`missing deliverable: ${scratch}/reviews/learnings-raw.md; `);
  });

  test("AC5 — zero-byte learnings-raw.md → soft block", () => {
    phase7State({ review_complete: "pass" });
    put("reviews/learnings-raw.md", "");
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe(`missing deliverable: ${scratch}/reviews/learnings-raw.md; `);
  });

  // R1 parity: checkPhase7 collects entries and joins with joinMissing, so the
  // reason is byte-identical to bash `printf '%s; '` (trailing "; " after every entry).
  test("AC5 — both missing → exact joined reason (bash parity, joinMissing)", () => {
    phase7State();
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe(
      `review_complete=pass (got: <missing>); missing deliverable: ${scratch}/reviews/learnings-raw.md; `,
    );
  });

  // R1: review_complete-only reason now ends with "; " like every other joinMissing phase.
  test("AC5 — review_complete missing, learnings-raw.md present → reason ends with '; ' (joinMissing)", () => {
    phase7State();
    put("reviews/learnings-raw.md", "- [testing] --- a lesson\n");
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe("review_complete=pass (got: <missing>); ");
  });

  // Guard (passes on main): no scratchpad_dir → no deliverable entry.
  test("AC5 guard — phase 7 without scratchpad_dir, review_complete pass → pass", () => {
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

  test("AC5 — header-only learnings-raw.md (## New Learnings) passes", () => {
    phase7State({ review_complete: "pass" });
    put("reviews/learnings-raw.md", "## New Learnings\n");
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });

  test("AC5 — TRIVIAL + learnings-raw.md missing → pass (not checked)", () => {
    phase7State({ complexity: "TRIVIAL" });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });

  // Guard (passes on main).
  test("AC5 guard — learnings-raw.md present + review_complete pass → pass", () => {
    phase7State({ review_complete: "pass" });
    put("reviews/learnings-raw.md", "- [testing] --- a lesson\n");
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });
});

describe("Rev 3 — C2 spaced scratchpad path", () => {
  // C2 guard: TS already handles spaces (bash is fixed to match).
  test("C2 guard — phase 7 with spaced scratchpad path, file present → pass", () => {
    scratch = join(dir, "my scratch");
    mkdirSync(scratch, { recursive: true });
    phase7State({ review_complete: "pass" });
    put("reviews/learnings-raw.md", "- [testing] --- a lesson\n");
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });
});
