// RED — Phase 5.1. Tests fail until build-phase-gate.ts is ported from HAL.
// Covers spec §7.3 U1–U10 + the four shadow-mode fix regressions (§6.4):
//   - 42f72651 EAGAIN retry / fail-closed bun
//   - f4feb1b2 Phase 0.5 alignment / posix_spawn ENOENT
//   - 30583611 volume reduction (only mismatches)
//   - c33d8a93 complexity downgrade hard block
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { dispatchPhase } from "../build-phase-gate.ts";

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

// ---------------------------------------------------------------------------
// U1–U3 covered in lib/__tests__/state-reader.test.ts
// ---------------------------------------------------------------------------

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

  test("U8 — phase 7 with disablePhase7 unset returns pass (Phase 7 stubbed in Phase 1)", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
      plan_review: "approved",
      opus_validation: "pass",
      phase_53_green: "true",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
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

  test("phase 0.5 with pre_build_gate=pass and learnings complete → pass", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "0.5",
      last_updated: nowIso(),
      pre_build_gate: "pass",
      phase_05_learnings: "complete",
      phase_05_constitution: "none",
      phase_05_security: "LOW",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
  });
});

describe("dispatchPhase — phase 5.3 hardness exception (HAL line 795)", () => {
  test("ALL missing 5.3 fields produce HARD blocks (not soft)", () => {
    const fields = ["phase_53_green", "phase_53_minimal", "phase_53_passing"];
    for (const f of fields) {
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
    }
  });
});

describe("CLI invocation — fail-closed posix_spawn ENOENT (commit 42f72651/f4feb1b2)", () => {
  test("CLI exits non-zero when invoked as a script (RED stub returns 99; GREEN must return ≥0 with valid verdict)", () => {
    // This test simply asserts the CLI is invokable via bun. Once GREEN, it must
    // produce a JSON verdict on stdout for known state files. RED stub exits 99.
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
    // GREEN expectation: exit code 2 (soft block, missing build-architecture.md)
    expect(res.status).toBe(2);
  });
});
