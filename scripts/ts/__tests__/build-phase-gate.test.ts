// Unit tests for build-phase-gate.ts — per-phase dispatch contract.
// Covers spec §7.3 U4–U10 + the four shadow-mode fix regressions (§6.4):
//   - 42f72651 EAGAIN retry / fail-closed bun
//   - f4feb1b2 Phase 0.5 alignment / posix_spawn ENOENT
//   - 30583611 volume reduction (only mismatches)
//   - c33d8a93 complexity downgrade hard block
// (U1–U3 live in lib/__tests__/state-reader.test.ts.)
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, readFileSync, rmSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { dispatchPhase, loopPreventionCLI } from "../build-phase-gate.ts";
import type { GateVerdict } from "../build-phase-gate.ts";

// Compile-time regression: the discriminated union must reject illegal
// exit_code / severity / mutation states. These lines exist solely to fail
// `tsc --noEmit` if the union ever loosens; if any @ts-expect-error stops
// triggering, the test file itself will fail to type-check.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const _illegalPassExit: GateVerdict =
  // @ts-expect-error exit_code 1 is illegal on a pass verdict
  { decision: "pass", exit_code: 1 };
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const _blockMissingSeverity: GateVerdict =
  // @ts-expect-error block verdict requires a severity discriminator
  { decision: "block", exit_code: 1, reason: "x" };
function _mutateVerdict(v: GateVerdict): void {
  // @ts-expect-error decision is readonly
  v.decision = "pass";
}

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

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "build-phase-gate-"));
  process.chdir(dir);
});

afterEach(() => {
  process.chdir(savedCwd);
  rmSync(dir, { recursive: true, force: true });
});

describe("dispatchPhase — phase routing & exit code contract", () => {
  test("U4 — phase 4 with missing build-architecture.md returns soft block", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "4",
      last_updated: nowIso(),
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("soft");
    expect(v.exit_code).toBe(2);
  });

  test("U5 — phase 5.3 missing phase_53_green returns HARD block (not soft)", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "5.3",
      last_updated: nowIso(),
      plan_review: "approved",
      opus_validation: "pass",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
  });

  test("U6 — BYPASS #4: metadata complexity=COMPLEX, state complexity=SIMPLE → hard block (commit c33d8a93)", () => {
    writeState({
      task: "x",
      complexity: "SIMPLE",
      mode: "AUTONOMOUS",
      current_phase: "5",
      last_updated: nowIso(),
    });
    writeFileSync(
      join(dir, "build-metadata.json"),
      JSON.stringify({ complexity: "COMPLEX", classified_at: "2026-04-10T10:00:00Z" }),
    );
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
    expect(v.reason || "").toMatch(/complexity|downgrade|bypass.*4/i);
  });

  test("U7 — BYPASS #12: build-opus-validation.md mtime older than build-state.yaml → soft block", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "5",
      last_updated: nowIso(),
      plan_review: "approved",
    });
    const stale = join(dir, "build-opus-validation.md");
    writeFileSync(stale, "stale validation\n");
    const past = new Date(Date.now() - 3600 * 1000);
    utimesSync(stale, past, past);
    // Touch state to be newer
    const future = new Date();
    utimesSync(join(dir, "build-state.yaml"), future, future);
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("soft");
    expect(v.exit_code).toBe(2);
    expect(v.reason || "").toMatch(/stale|freshness|bypass.*12/i);
  });

  test("U8 — phase 7 with review_complete=pass returns pass (Phase 7 stubbed, parity with bash gate_phase_7)", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
      plan_review: "approved",
      opus_validation: "pass",
      phase_53_green: "true",
      review_complete: "pass",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });

  test("U8b — phase 7 missing review_complete → soft block (bash parity)", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("soft");
    expect(v.exit_code).toBe(2);
  });

  test("U10 — phase 5.5 with assertion_gaming_detected:true → hard block", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "5.5",
      last_updated: nowIso(),
      plan_review: "approved",
      opus_validation: "pass",
      phase_53_green: "true",
      assertion_gaming_detected: "true",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
  });
});

describe("dispatchPhase — Phase 0.5 alignment (commit f4feb1b2)", () => {
  test("phase 0.5 missing pre_build_gate=pass → block", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "0.5",
      last_updated: nowIso(),
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
  });

  test("phase 0.5 with pre_build_gate=fail explicitly → soft block naming pre_build_gate", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "0.5",
      last_updated: nowIso(),
      pre_build_gate: "fail",
      phase_05_learnings: "complete",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("soft");
    expect(v.reason || "").toMatch(/pre_build_gate/);
  });

  test("phase 0.5 with pre_build_gate=pass but phase_05_learnings unset → soft block naming phase_05_learnings", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "0.5",
      last_updated: nowIso(),
      pre_build_gate: "pass",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("soft");
    expect(v.reason || "").toMatch(/phase_05_learnings/);
  });

  test("phase 0.5 with pre_build_gate=pass and learnings complete → pass", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "0.5",
      last_updated: nowIso(),
      pre_build_gate: "pass",
      phase_05_learnings: "complete",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });
});

describe("dispatchPhase — phase 5.3 hardness exception (HAL line 795)", () => {
  test("missing phase_53_green produces HARD block (exit 1)", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "5.3",
      last_updated: nowIso(),
      plan_review: "approved",
      opus_validation: "pass",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
  });

  test("phase_53_green=complete → pass", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "5.3",
      last_updated: nowIso(),
      plan_review: "approved",
      opus_validation: "pass",
      phase_53_green: "complete",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });
});

// ---------------------------------------------------------------------------
// GAP_FILL: loopPreventionCLI — 35 LOC of counter-based bypass logic that
// previously had zero unit coverage. Reviewer 5 flagged this as BLOCKING (G1).
// These tests close the gap — not RED-regressions, net-new coverage.
// ---------------------------------------------------------------------------
describe("loopPreventionCLI — counter + bypass (GAP_FILL, reviewer 5 G1)", () => {
  function statePath(): string {
    return join(dir, "build-state.yaml");
  }

  test("count=0 increments to 1, no bypass", () => {
    writeState({ current_phase: "4" });
    const bypassed = loopPreventionCLI(statePath(), "4");
    expect(bypassed).toBe(false);
    const content = readFileSync(statePath(), "utf8");
    expect(content).toMatch(/^gate_block_counter: 1$/m);
    expect(content).not.toMatch(/gate_bypass:/);
  });

  test("count=3 increments to 4, writes gate_bypass, returns true", () => {
    writeState({ current_phase: "5.1", gate_block_counter: "3" });
    const bypassed = loopPreventionCLI(statePath(), "5.1");
    expect(bypassed).toBe(true);
    const content = readFileSync(statePath(), "utf8");
    expect(content).toMatch(/^gate_block_counter: 4$/m);
    expect(content).toMatch(/^gate_bypass: true$/m);
    expect(content).toMatch(/^gate_bypass_phase: 5\.1$/m);
  });

  test("counter is rewritten, not duplicated (strip + append)", () => {
    writeState({ current_phase: "4", gate_block_counter: "1" });
    loopPreventionCLI(statePath(), "4");
    const content = readFileSync(statePath(), "utf8");
    const occurrences = content.match(/^gate_block_counter:/gm) || [];
    expect(occurrences.length).toBe(1);
    expect(content).toMatch(/^gate_block_counter: 2$/m);
  });

  test("quoted counter \"2\" parses correctly", () => {
    // Build state file with explicitly quoted counter value.
    writeFileSync(
      statePath(),
      'current_phase: "4"\ngate_block_counter: "2"\n',
    );
    const bypassed = loopPreventionCLI(statePath(), "4");
    expect(bypassed).toBe(false);
    const content = readFileSync(statePath(), "utf8");
    expect(content).toMatch(/^gate_block_counter: 3$/m);
  });

  test("malformed counter (non-numeric) falls through to 0 → 1", () => {
    writeFileSync(
      statePath(),
      'current_phase: "4"\ngate_block_counter: abc\n',
    );
    const bypassed = loopPreventionCLI(statePath(), "4");
    expect(bypassed).toBe(false);
    const content = readFileSync(statePath(), "utf8");
    expect(content).toMatch(/^gate_block_counter: 1$/m);
  });

  test("atomic write preserves other state fields", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      current_phase: "4",
      gate_block_counter: "1",
    });
    loopPreventionCLI(statePath(), "4");
    const content = readFileSync(statePath(), "utf8");
    expect(content).toMatch(/task: "x"/);
    expect(content).toMatch(/complexity: "FEATURE"/);
    expect(content).toMatch(/^gate_block_counter: 2$/m);
  });
});

describe("CLI invocation — fail-closed posix_spawn ENOENT (commit 42f72651/f4feb1b2)", () => {
  test("CLI smoke test: Phase 4 + no build-architecture.md must exit 2 (soft block)", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "4",
      last_updated: nowIso(),
    });
    const scriptPath = join(
      savedCwd.includes("phase1-ts-gates") ? savedCwd : "/Users/guylifshitz/Projects/bytedigger/.batch-worktrees/phase1-ts-gates",
      "scripts/ts/build-phase-gate.ts",
    );
    const res = spawnSync("bun", ["run", scriptPath], {
      cwd: dir,
      encoding: "utf8",
      timeout: 15000,
    });
    expect(res.status).toBe(2);
  });
});
