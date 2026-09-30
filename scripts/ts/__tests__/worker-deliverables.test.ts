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

function phase4State(extra: Record<string, string> = {}): void {
  writeState({
    task: "x",
    complexity: "FEATURE",
    mode: "AUTONOMOUS",
    current_phase: "4",
    last_updated: nowIso(),
    scratchpad_dir: scratch,
    ...extra,
  });
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

describe("AC4 — phase 4 gate: findings must be non-empty, approach deliverable checked", () => {
  test("AC4a — only zero-byte findings-*.md → hard block with non-empty wording + scratchpad_stale: true", () => {
    phase4State({ phase_4_architect: "complete" });
    put("research/findings-a.md", "");
    put("architecture/approach-a.md", "# approach\n");
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    if (v.decision !== "block") throw new Error("unreachable");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
    expect(v.reason).toBe(
      `scratchpad_stale: no non-empty findings-*.md found in ${scratch}/research — Phase 2 exploration must complete before Phase 4`,
    );
    const state = readFileSync(join(dir, "build-state.yaml"), "utf8");
    expect(state).toContain("scratchpad_stale: true");
  });

  test("AC4b — non-empty findings, no architecture/approach-*.md → soft block with deliverable entry", () => {
    phase4State({ phase_4_architect: "complete" });
    put("research/findings-a.md", "# findings\n");
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe(`missing deliverable: ${scratch}/architecture/approach-*.md; `);
  });

  test("AC4b — zero-byte approach-*.md only → same soft block", () => {
    phase4State({ phase_4_architect: "complete" });
    put("research/findings-a.md", "# findings\n");
    put("architecture/approach-a.md", "");
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe(`missing deliverable: ${scratch}/architecture/approach-*.md; `);
  });

  test("AC4b — deliverable entry comes AFTER phase_4_architect entry when both missing", () => {
    phase4State();
    put("research/findings-a.md", "# findings\n");
    const v = dispatchPhase({ cwd: dir });
    expect(softReason(v)).toBe(
      `phase_4_architect=complete (got: <missing>); missing deliverable: ${scratch}/architecture/approach-*.md; `,
    );
  });

  test("AC4b — one non-empty approach among several empty ones satisfies the check → pass", () => {
    phase4State({ phase_4_architect: "complete" });
    put("research/findings-a.md", "# findings\n");
    put("architecture/approach-empty.md", "");
    put("architecture/approach-real.md", "# approach\n");
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });

  // R1 precedence: hard block wins over the soft approach entry. The TS verdict reason
  // has no prefix; toWirePayload adds "HARD BLOCK: " on the wire, matching bash hard_block().
  // Guard-ish on main (hard block already fires there, but with the old wording → RED on text).
  test("AC4a — zero-byte findings AND no approach → hard block wins (exit 1, not soft)", () => {
    phase4State({ phase_4_architect: "complete" });
    put("research/findings-a.md", "");
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    if (v.decision !== "block") throw new Error("unreachable");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
    expect(v.reason).toBe(
      `scratchpad_stale: no non-empty findings-*.md found in ${scratch}/research — Phase 2 exploration must complete before Phase 4`,
    );
  });

  // Guard (passes on main): unset scratchpad_dir → no deliverable entry.
  test("AC4 guard — scratchpad_dir unset and phase_4_architect complete → pass", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "4",
      last_updated: nowIso(),
      phase_4_architect: "complete",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });
});

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
