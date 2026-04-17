// Unit tests for build-phase-gate.ts — per-phase dispatch contract.
// Covers spec §7.3 U4–U12 + the four shadow-mode fix regressions (§6.4):
//   - 42f72651 EAGAIN retry / fail-closed bun
//   - f4feb1b2 Phase 0.5 alignment / posix_spawn ENOENT
//   - 30583611 volume reduction (only mismatches)
//   - c33d8a93 complexity downgrade hard block
// (U1–U3 live in lib/__tests__/state-reader.test.ts.)
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, readFileSync, rmSync, utimesSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { dispatchPhase, loopPreventionCLI } from "../build-phase-gate.ts";
import type { GateVerdict } from "../build-phase-gate.ts";
import { StateReadError } from "../lib/state-reader.ts";
// checkPhase4 + checkPhase53 are exported — namespace import gives us a single
// import point for both named and namespace-style access. Tests F4-3 and F4-4
// exercise those exports directly.
import * as _gate from "../build-phase-gate.ts";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _gateAny = _gate as any;

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
    // F5: resolve script path relative to this test file, not a hard-coded worktree name.
    const scriptPath = join(
      new URL(import.meta.url).pathname,
      "..",  // __tests__
      "..",  // ts
      "build-phase-gate.ts",
    );
    const res = spawnSync("bun", ["run", scriptPath], {
      cwd: dir,
      encoding: "utf8",
      timeout: 15000,
    });
    expect(res.status).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// F2: TRIVIAL complexity skip in checkPhase7
// Spec: when complexity=TRIVIAL, checkPhase7 must return PASS even without
// review_complete. regression: SIMPLE still soft-blocks. disablePhase7 wins.
// ---------------------------------------------------------------------------
describe("checkPhase7 — TRIVIAL complexity skip (F2)", () => {
  test("U9 — complexity=TRIVIAL returns PASS even without review_complete", () => {
    writeState({
      task: "x",
      complexity: "TRIVIAL",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
      // review_complete intentionally absent
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });

  test("U10 — complexity=TRIVIAL with review_complete=fail still returns pass", () => {
    writeState({
      task: "x",
      complexity: "TRIVIAL",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
      review_complete: "fail",
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });

  test("U12 — complexity=SIMPLE without review_complete still soft-blocks (regression)", () => {
    writeState({
      task: "x",
      complexity: "SIMPLE",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
      // review_complete intentionally absent
    });
    const v = dispatchPhase({ cwd: dir });
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("soft");
    expect(v.exit_code).toBe(2);
  });

  test("U11 — disablePhase7=true takes priority over TRIVIAL check (returns PASS via config skip)", () => {
    // disablePhase7=true in config means checkPhase7 returns pass() immediately,
    // regardless of complexity. This test verifies the short-circuit path.
    // We write complexity=SIMPLE (would soft-block under normal path) and set
    // BYTEDIGGER_CONFIG to a file with disablePhase7:true.
    const cfgDir = mkdtempSync(join(tmpdir(), "bpg-cfg-"));
    const cfgPath = join(cfgDir, "bytedigger.json");
    writeFileSync(cfgPath, JSON.stringify({ disablePhase7: true }));
    const savedBytediggerConfig = process.env.BYTEDIGGER_CONFIG;
    process.env.BYTEDIGGER_CONFIG = cfgPath;
    try {
      writeState({
        task: "x",
        complexity: "SIMPLE",
        mode: "AUTONOMOUS",
        current_phase: "7",
        last_updated: nowIso(),
        // review_complete intentionally absent — would soft-block without disablePhase7
      });
      const v = dispatchPhase({ cwd: dir });
      expect(v.decision).toBe("pass");
      expect(v.exit_code).toBe(0);
    } finally {
      if (savedBytediggerConfig === undefined) {
        delete process.env.BYTEDIGGER_CONFIG;
      } else {
        process.env.BYTEDIGGER_CONFIG = savedBytediggerConfig;
      }
      rmSync(cfgDir, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// F7 — emit wiring: stderr observability events fire on lifecycle
//
// These tests assert that dispatchPhase/mainCLI emit structured JSONL events
// to stderr (phase-start, phase-end, gate-result, phase-skip, build-complete)
// at each lifecycle transition.
//
// RED CONTRACT: all tests FAIL until scripts/ts/build-phase-gate.ts is wired
// to call the emit.ts API. dispatchPhase currently emits nothing.
//
// Spy infrastructure: mirrors emit.test.ts:30-52. Scoped strictly to this
// describe block so the existing 81 tests see no stderr side-effects.
// ---------------------------------------------------------------------------

describe("F7 — emit wiring: stderr observability events fire on lifecycle", () => {
  // ---- spy infrastructure (scoped to this block) --------------------------
  // Note: spy is scoped strictly to this describe block. Pre-existing tests
  // outside F7 see real stderr output — this is intentional; it keeps noise
  // visible during debugging and avoids silent swallowing of unexpected writes.
  let wireWrites: string[] = [];
  let wireOriginalWrite: typeof process.stderr.write;
  let wireSavedConfig: string | undefined;
  let wireSavedHalDir: string | undefined;

  function wireInstallSpy(): void {
    wireWrites = [];
    wireOriginalWrite = process.stderr.write.bind(process.stderr);
    // @ts-ignore — intentional monkey-patch for test isolation
    process.stderr.write = (chunk: unknown): boolean => {
      wireWrites.push(String(chunk));
      return true;
    };
  }

  function wireUninstallSpy(): void {
    // @ts-ignore — restore original
    process.stderr.write = wireOriginalWrite;
  }

  function wireGetEventLines(): string[] {
    return wireWrites.filter((l) => l.startsWith("[bytedigger:event]"));
  }

  function wireParseEventLine(line: string): Record<string, unknown> {
    const jsonPart = line.replace(/^\[bytedigger:event\]\s+/, "");
    return JSON.parse(jsonPart) as Record<string, unknown>;
  }

  // ---- per-test setup / teardown ------------------------------------------

  beforeEach(() => {
    wireSavedConfig = process.env.BYTEDIGGER_CONFIG;
    wireSavedHalDir = process.env.HAL_DIR;
    delete process.env.HAL_DIR;
    wireInstallSpy();
  });

  afterEach(() => {
    wireUninstallSpy();
    if (wireSavedConfig === undefined) {
      delete process.env.BYTEDIGGER_CONFIG;
    } else {
      process.env.BYTEDIGGER_CONFIG = wireSavedConfig;
    }
    if (wireSavedHalDir === undefined) {
      delete process.env.HAL_DIR;
    } else {
      process.env.HAL_DIR = wireSavedHalDir;
    }
  });

  // ---- WIRE-1: phase-start + phase-end(pass) on a passing phase -----------

  test("WIRE-1 — dispatchPhase emits phase-start then phase-end(pass) on a passing phase", () => {
    // Phase 7 TRIVIAL → always pass, no required state fields beyond complexity.
    writeState({
      task: "x",
      complexity: "TRIVIAL",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
    });
    dispatchPhase({ cwd: dir });

    const events = wireGetEventLines();
    const parsed = events.map(wireParseEventLine);

    const phaseStart = parsed.find((e) => e["event"] === "phase-start");
    expect(phaseStart).toBeDefined();
    expect(phaseStart!["phase"]).toBe("7");

    const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
    expect(phaseEnd).toBeDefined();
    expect(phaseEnd!["phase"]).toBe("7");
    expect(phaseEnd!["status"]).toBe("pass");

    // WP-4 pass branch: gate-result must also be emitted with status=pass.
    const gateResult = parsed.find((e) => e["event"] === "gate-result");
    expect(gateResult).toBeDefined();
    expect(gateResult!["status"]).toBe("pass");
    expect(gateResult!["phase"]).toBe("7");
  });

  // ---- WIRE-2: gate-result(hard-block) + phase-end(block) on global hardBlock

  test("WIRE-2 — dispatchPhase emits gate-result(hard-block) + phase-end(block) on global hardBlock", () => {
    // Complexity downgrade (BYPASS #4): metadata says COMPLEX, state says SIMPLE → hard block.
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
    dispatchPhase({ cwd: dir });

    const events = wireGetEventLines();
    const parsed = events.map(wireParseEventLine);

    const gateResult = parsed.find((e) => e["event"] === "gate-result");
    expect(gateResult).toBeDefined();
    expect(gateResult!["status"]).toBe("hard-block");
    expect(gateResult!["phase"]).toBe("5");

    const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
    expect(phaseEnd).toBeDefined();
    expect(phaseEnd!["status"]).toBe("block");
    const meta = phaseEnd!["metadata"] as Record<string, unknown> | undefined;
    expect(meta?.["severity"]).toBe("hard");
  });

  // ---- WIRE-3: soft-block on stale-artifact downgrade ---------------------

  test("WIRE-3 — dispatchPhase emits gate-result(soft-block) + phase-end(block) on stale artifact", () => {
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "5",
      last_updated: nowIso(),
      plan_review: "approved",
    });
    const stale = join(dir, "build-opus-validation.md");
    writeFileSync(stale, "stale\n");
    const past = new Date(Date.now() - 3600 * 1000);
    utimesSync(stale, past, past);
    const future = new Date();
    utimesSync(join(dir, "build-state.yaml"), future, future);

    dispatchPhase({ cwd: dir });

    const events = wireGetEventLines();
    const parsed = events.map(wireParseEventLine);

    const gateResult = parsed.find((e) => e["event"] === "gate-result");
    expect(gateResult).toBeDefined();
    expect(gateResult!["status"]).toBe("soft-block");

    const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
    expect(phaseEnd).toBeDefined();
    expect(phaseEnd!["status"]).toBe("block");
    const meta = phaseEnd!["metadata"] as Record<string, unknown> | undefined;
    expect(meta?.["severity"]).toBe("soft");
  });

  // ---- WIRE-4: phase-end(pass) for early-pass phases 0, 1, 2, 3 ----------

  test("WIRE-4 — dispatchPhase emits phase-end(pass) for each of phases 0, 1, 2, 3", () => {
    for (const earlyPhase of ["0", "1", "2", "3"]) {
      // Reset spy captures between iterations
      wireWrites = [];
      writeState({
        task: "x",
        complexity: "FEATURE",
        mode: "AUTONOMOUS",
        current_phase: earlyPhase,
        last_updated: nowIso(),
      });
      dispatchPhase({ cwd: dir });

      const events = wireGetEventLines();
      const parsed = events.map(wireParseEventLine);

      const phaseStart = parsed.find((e) => e["event"] === "phase-start");
      expect(phaseStart).toBeDefined();
      expect(phaseStart!["phase"]).toBe(earlyPhase);

      const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
      expect(phaseEnd).toBeDefined();
      expect(phaseEnd!["phase"]).toBe(earlyPhase);
      expect(phaseEnd!["status"]).toBe("pass");
    }
  });

  // ---- WIRE-5: phase-end(pass) for phase 8 and default branch -------------

  test("WIRE-5 — dispatchPhase emits phase-end(pass) for phase 8 and unknown default", () => {
    for (const earlyPhase of ["8", "99"]) {
      wireWrites = [];
      writeState({
        task: "x",
        complexity: "FEATURE",
        mode: "AUTONOMOUS",
        current_phase: earlyPhase,
        last_updated: nowIso(),
      });
      dispatchPhase({ cwd: dir });

      const parsed = wireGetEventLines().map(wireParseEventLine);

      const phaseStart = parsed.find((e) => e["event"] === "phase-start");
      expect(phaseStart).toBeDefined();
      expect(phaseStart!["phase"]).toBe(earlyPhase);

      const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
      expect(phaseEnd).toBeDefined();
      expect(phaseEnd!["phase"]).toBe(earlyPhase);
      expect(phaseEnd!["status"]).toBe("pass");
    }
  });

  // ---- WIRE-6: checkPhase6 semantic-skip emits phase-skip before hard-block

  test("WIRE-6 — checkPhase6 semantic-skip emits phase-skip event before hard-block", () => {
    // Test the semantic-skip branch: skipped=0, total=fixed=0, scan finds a forbidden phrase.
    // (phase_6_findings_skipped > 0 would return hardBlock before the scan — not the path we want.)
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "6",
      last_updated: nowIso(),
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });
    // Write a review file with a semantic-skip phrase that the scanner will detect.
    // Load actual phrases to pick a real one; surface load failures so setup bugs are visible.
    const phrasesPath = join(
      new URL(import.meta.url).pathname,
      "..", "..", "lib", "semantic-skip-phrases.json",
    );
    let skipPhrase = "this will be addressed later";
    try {
      const phrasesData = JSON.parse(readFileSync(phrasesPath, "utf8")) as { phrases: string[] };
      if (phrasesData.phrases.length > 0) skipPhrase = phrasesData.phrases[0]!;
    } catch (err) {
      throw new Error(`WIRE-6 setup: failed to load ${phrasesPath}: ${err instanceof Error ? err.message : String(err)}`);
    }

    writeFileSync(join(dir, "review-scan-test.md"), `# Review\n${skipPhrase}\n`);

    dispatchPhase({ cwd: dir });

    const parsed = wireGetEventLines().map(wireParseEventLine);
    const phaseSkip = parsed.find((e) => e["event"] === "phase-skip");
    expect(phaseSkip).toBeDefined();
    expect(phaseSkip!["phase"]).toBe("6");

    const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
    expect(phaseEnd).toBeDefined();
    expect(phaseEnd!["status"]).toBe("block");
  });

  // ---- WIRE-7: duration_ms is non-negative number -------------------------

  test("WIRE-7 — duration_ms in phase-end event is a non-negative number", () => {
    writeState({
      task: "x",
      complexity: "TRIVIAL",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
    });
    dispatchPhase({ cwd: dir });

    const parsed = wireGetEventLines().map(wireParseEventLine);
    const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
    expect(phaseEnd).toBeDefined();
    expect(typeof phaseEnd!["duration_ms"]).toBe("number");
    expect(phaseEnd!["duration_ms"] as number).toBeGreaterThanOrEqual(0);
  });

  // ---- WIRE-8: observability.enabled=false suppresses all emits -----------
  // RED: first confirm that with observability enabled (default) at least one
  // event fires — this assertion fails until wiring lands. The suppression
  // half is tested in the same block to keep setup symmetric.

  test("WIRE-8 — observability.enabled=false in BYTEDIGGER_CONFIG suppresses all emit events", () => {
    writeState({
      task: "x",
      complexity: "TRIVIAL",
      mode: "AUTONOMOUS",
      current_phase: "7",
      last_updated: nowIso(),
    });

    // Step 1: observability enabled (no BYTEDIGGER_CONFIG set) — events MUST fire.
    // This assertion ensures the test fails in RED state (no wiring yet).
    dispatchPhase({ cwd: dir });
    const enabledEvents = wireGetEventLines();
    expect(enabledEvents.length).toBeGreaterThan(0);

    // Step 2: observability disabled — events must be suppressed.
    wireWrites = [];
    const cfgDir = mkdtempSync(join(tmpdir(), "wire8-cfg-"));
    const cfgPath = join(cfgDir, "bytedigger.json");
    writeFileSync(cfgPath, JSON.stringify({ observability: { enabled: false } }));
    process.env.BYTEDIGGER_CONFIG = cfgPath;
    try {
      dispatchPhase({ cwd: dir });
      const disabledEvents = wireGetEventLines();
      expect(disabledEvents).toHaveLength(0);
    } finally {
      rmSync(cfgDir, { recursive: true, force: true });
    }
  });

  // ---- WIRE-9: CLI smoke test emits build-complete on pass exit (spawnSync) -

  test("WIRE-9 — CLI smoke test: build-complete event appears in stderr on pass exit", () => {
    writeFileSync(join(dir, "build-state.yaml"), [
      'task: "x"',
      'complexity: "TRIVIAL"',
      'mode: "AUTONOMOUS"',
      'current_phase: "7"',
      `last_updated: "${nowIso()}"`,
    ].join("\n") + "\n");

    const scriptPath = join(
      new URL(import.meta.url).pathname,
      "..",   // __tests__
      "..",   // ts
      "build-phase-gate.ts",
    );

    // Uninstall spy so the child's stderr flows freely through spawnSync capture.
    wireUninstallSpy();
    try {
      const childEnv = { ...process.env };
      delete childEnv.HAL_DIR;
      delete childEnv.BYTEDIGGER_CONFIG;
      const res = spawnSync("bun", ["run", scriptPath], {
        cwd: dir,
        encoding: "utf8",
        timeout: 15000,
        env: childEnv,
      });

      expect(res.status).toBe(0);

      const stderrOutput = res.stderr ?? "";
      const buildCompleteLines = stderrOutput
        .split("\n")
        .filter((l) => l.startsWith("[bytedigger:event]"));

      const parsedEvents = buildCompleteLines.map((l) => {
        const jsonPart = l.replace(/^\[bytedigger:event\]\s+/, "");
        return JSON.parse(jsonPart) as Record<string, unknown>;
      });

      const buildComplete = parsedEvents.find((e) => e["event"] === "build-complete");
      expect(buildComplete).toBeDefined();
    } finally {
      // Reinstall spy so afterEach teardown can call wireUninstallSpy safely.
      wireInstallSpy();
    }
  });

  // ---- WIRE-10: dispatchPhase never throws on matrix of state fixtures -----
  // Primary assertion: never-throw regression guard (always-GREEN — safety net).
  // Secondary assertion (RED contract): at least one [bytedigger:event] line must
  // appear across the matrix once wiring lands. Fails in RED because dispatchPhase
  // emits nothing yet.

  test("WIRE-10 — dispatchPhase never throws on matrix of state fixtures (never-throw regression)", () => {
    type StateFixture = Record<string, string>;
    const fixtures: StateFixture[] = [
      // Phase 0–3 early pass paths
      { current_phase: "0", complexity: "FEATURE" },
      { current_phase: "1", complexity: "TRIVIAL" },
      { current_phase: "2", complexity: "SIMPLE" },
      { current_phase: "3", complexity: "COMPLEX" },
      // Phase 8 and default
      { current_phase: "8", complexity: "FEATURE" },
      { current_phase: "99", complexity: "FEATURE" },
      // Phase 7 TRIVIAL pass
      { current_phase: "7", complexity: "TRIVIAL" },
      // Phase 7 soft block (missing review_complete)
      { current_phase: "7", complexity: "FEATURE" },
      // Phase 5.3 hard block (missing phase_53_green)
      { current_phase: "5.3", complexity: "FEATURE", plan_review: "approved", opus_validation: "pass" },
      // Phase 5.5 hard block (assertion gaming)
      { current_phase: "5.5", complexity: "FEATURE", assertion_gaming_detected: "true" },
    ];

    for (const fixture of fixtures) {
      const fields: Record<string, string> = {
        task: "test",
        mode: "AUTONOMOUS",
        last_updated: nowIso(),
        ...fixture,
      };
      writeState(fields);
      wireWrites = [];

      // Primary (always-GREEN): dispatchPhase must never throw regardless of state.
      expect(() => dispatchPhase({ cwd: dir })).not.toThrow();

      // Secondary (RED until wiring lands): per-fixture, assert both:
      //   - a phase-start event exists, AND
      //   - at least one of phase-end or phase-skip exists
      // This is stronger than a total count check and catches partial emission.
      const parsed = wireGetEventLines().map(wireParseEventLine);
      expect(parsed.some((e) => e["event"] === "phase-start")).toBe(true);
      expect(
        parsed.some((e) => e["event"] === "phase-end" || e["event"] === "phase-skip"),
      ).toBe(true);
    }
  });

  // ---- WIRE-2b: gate-result(hard-block) from verdict-level hard block (non-global) ---

  test("WIRE-2b — dispatchPhase emits gate-result(hard-block) from verdict-level hard block (non-global)", () => {
    // Phase 5.3 with missing phase_53_green → hard block from checkPhase53, not global checks.
    // Distinguished from WIRE-2 (global path) by: no metadata.source field in gate-result.
    writeState({
      task: "x",
      complexity: "FEATURE",
      mode: "AUTONOMOUS",
      current_phase: "5.3",
      last_updated: nowIso(),
      plan_review: "approved",
      opus_validation: "pass",
      // phase_53_green intentionally absent → checkPhase53 returns hardBlock
    });
    dispatchPhase({ cwd: dir });

    const parsed = wireGetEventLines().map(wireParseEventLine);

    const gateResult = parsed.find((e) => e["event"] === "gate-result");
    expect(gateResult).toBeDefined();
    expect(gateResult!["status"]).toBe("hard-block");
    expect(gateResult!["phase"]).toBe("5.3");
    // No metadata.source — this is a verdict-level hard block, not a global one.
    const grMeta = gateResult!["metadata"] as Record<string, unknown> | undefined;
    expect(grMeta?.["source"]).toBeUndefined();

    const phaseEnd = parsed.find((e) => e["event"] === "phase-end");
    expect(phaseEnd).toBeDefined();
    expect(phaseEnd!["status"]).toBe("block");
    const peMeta = phaseEnd!["metadata"] as Record<string, unknown> | undefined;
    expect(peMeta?.["severity"]).toBe("hard");
  });

  // ---- WIRE-11: mainCLI soft-block emits build-complete(soft-block) --------

  test("WIRE-11 — mainCLI emits build-complete(soft-block) when Phase 4 gate soft-blocks", () => {
    // Phase 4 with missing build-architecture.md → soft block (exit 2).
    writeFileSync(join(dir, "build-state.yaml"), [
      'task: "x"',
      'complexity: "FEATURE"',
      'mode: "AUTONOMOUS"',
      'current_phase: "4"',
      `last_updated: "${nowIso()}"`,
    ].join("\n") + "\n");

    const scriptPath = join(
      new URL(import.meta.url).pathname,
      "..",   // __tests__
      "..",   // ts
      "build-phase-gate.ts",
    );

    wireUninstallSpy();
    try {
      const childEnv = { ...process.env };
      delete childEnv.HAL_DIR;
      delete childEnv.BYTEDIGGER_CONFIG;
      const res = spawnSync("bun", ["run", scriptPath], {
        cwd: dir,
        encoding: "utf8",
        timeout: 15000,
        env: childEnv,
      });

      expect(res.status).toBe(2);

      const stderrLines = (res.stderr ?? "").split("\n").filter((l) => l.startsWith("[bytedigger:event]"));
      const parsedEvents = stderrLines.map((l) => {
        const jsonPart = l.replace(/^\[bytedigger:event\]\s+/, "");
        return JSON.parse(jsonPart) as Record<string, unknown>;
      });

      const buildComplete = parsedEvents.find((e) => e["event"] === "build-complete");
      expect(buildComplete).toBeDefined();
      const meta = buildComplete!["metadata"] as Record<string, unknown> | undefined;
      expect(meta?.["outcome"]).toBe("soft-block");
      expect(meta?.["reason"]).toBeDefined();
    } finally {
      wireInstallSpy();
    }
  });

  // ---- WIRE-12: fatal path emits build-complete(fatal) when phrases file missing -----
  // The import.meta.main try/catch in build-phase-gate.ts wraps mainCLI() to emit
  // build-complete(fatal) on unhandled errors. The most reliable way to trigger this
  // path in a subprocess: point BYTEDIGGER_PHRASES_PATH at a non-existent file so
  // loadSemanticSkipPhrases() throws during module evaluation. In Bun this causes the
  // top-level error handler to fire (exit 1, FATAL message to stderr) before
  // import.meta.main runs — so no build-complete event is emitted via that path.
  //
  // This test therefore verifies the complementary invariant: the fatal module-load
  // error is surfaced (exit != 0, stderr contains "FATAL"), and documents that the
  // import.meta.main catch path exists in the code for errors thrown AFTER module load.

  test("WIRE-12 — fatal module-load error surfaces in stderr when phrases file is missing (catches unhandled throw pattern)", () => {
    writeFileSync(join(dir, "build-state.yaml"), [
      'task: "x"',
      'complexity: "FEATURE"',
      'mode: "AUTONOMOUS"',
      'current_phase: "6"',
      `last_updated: "${nowIso()}"`,
      'phase_6_findings_total: 0',
      'phase_6_findings_fixed: 0',
      'phase_6_findings_skipped: 0',
    ].join("\n") + "\n");

    const scriptPath = join(
      new URL(import.meta.url).pathname,
      "..",   // __tests__
      "..",   // ts
      "build-phase-gate.ts",
    );

    wireUninstallSpy();
    try {
      const childEnv = { ...process.env };
      delete childEnv.HAL_DIR;
      delete childEnv.BYTEDIGGER_CONFIG;
      // Point at a non-existent phrases file — loadSemanticSkipPhrases() throws FATAL.
      childEnv.BYTEDIGGER_PHRASES_PATH = join(dir, "nonexistent-phrases.json");
      const res = spawnSync("bun", ["run", scriptPath], {
        cwd: dir,
        encoding: "utf8",
        timeout: 15000,
        env: childEnv,
      });

      // Module-level throw → Bun exits 1, fatal message to stderr.
      expect(res.status).toBe(1);
      expect(res.stderr ?? "").toContain("FATAL");
    } finally {
      wireInstallSpy();
    }
  });
});

// ---------------------------------------------------------------------------
// F4 TOCTOU wiring — 5 scenarios (all GREEN)
//
// Wires readStateFieldOrThrow + StateReadError into build-phase-gate.ts.
// checkPhase4 and checkPhase53 are exported; dispatchPhase throws StateReadError
// for chmod-000 YAML; mainCLI emits "state file unreadable" to stderr.
// ---------------------------------------------------------------------------

describe("F4 TOCTOU wiring", () => {
  let f4Dir: string;
  let f4StatePath: string;

  beforeEach(() => {
    f4Dir = mkdtempSync(join(tmpdir(), "f4-toctou-"));
    f4StatePath = join(f4Dir, "build-state.yaml");
  });

  afterEach(() => {
    // Permissions are always restored inside each test's try/finally, but
    // we add a best-effort restore here as a safety net before cleanup.
    try { chmodSync(f4StatePath, 0o644); } catch (_) { /* already restored or file absent */ }
    rmSync(f4Dir, { recursive: true, force: true });
  });

  // F4-1: dispatchPhase with chmod-000 YAML must throw StateReadError.
  // After GREEN: line 707 uses readStateFieldOrThrow; StateReadError bubbles out of dispatchPhase.
  // RED: readStateField returns null silently — dispatchPhase returns pass(), does not throw.
  test("F4-1 — dispatchPhase with chmod-000 build-state.yaml throws StateReadError with correct filePath", () => {
    writeFileSync(f4StatePath, [
      'task: "x"',
      'complexity: "FEATURE"',
      'mode: "AUTONOMOUS"',
      'current_phase: "4"',
    ].join("\n") + "\n");
    chmodSync(f4StatePath, 0o000);
    try {
      try {
        dispatchPhase({ cwd: f4Dir });
        expect.unreachable("expected StateReadError to be thrown");
      } catch (err) {
        expect(err).toBeInstanceOf(StateReadError);
        expect((err as StateReadError).filePath).toBe(f4StatePath);
      }
    } finally {
      chmodSync(f4StatePath, 0o644);
    }
  });

  // F4-2a: defense-in-depth regression guard for the existsSync early-return at :720
  // that precedes the readStateFieldOrThrow at :727. A missing state file must never
  // throw — dispatchPhase must return a pass() verdict.
  test("F4-2a — dispatchPhase with missing build-state.yaml returns pass verdict (defense-in-depth regression guard)", () => {
    // f4StatePath does NOT exist — we deliberately skip writeFileSync.
    const verdict = dispatchPhase({ cwd: f4Dir });
    expect(verdict.decision).toBe("pass");
  });

  // F4-2b: export-contract check — checkPhase4 must be exported in GREEN state.
  test("F4-2b — checkPhase4 is exported (export contract)", () => {
    expect(typeof _gateAny.checkPhase4).toBe("function");
  });

  // F4-3: checkPhase4 with chmod-000 YAML must return a hard-block verdict with
  // reason containing "scratchpad_dir".
  // Exercises the try/catch at readStateFieldOrThrow via direct checkPhase4 invocation —
  // hard-blocks with reason referencing the unreadable field (scratchpad_dir).
  test("F4-3 — checkPhase4 with chmod-000 build-state.yaml returns hard-block verdict naming scratchpad_dir", () => {
    writeFileSync(f4StatePath, [
      'task: "x"',
      'complexity: "FEATURE"',
      'current_phase: "4"',
    ].join("\n") + "\n");
    chmodSync(f4StatePath, 0o000);
    let verdict: GateVerdict;
    try {
      verdict = _gateAny.checkPhase4(f4Dir) as GateVerdict;
    } finally {
      chmodSync(f4StatePath, 0o644);
    }
    expect(verdict.decision).toBe("block");
    expect(verdict.severity).toBe("hard");
    expect(verdict.reason).toMatch(/scratchpad_dir/);
  });

  // F4-4: checkPhase53 with chmod-000 YAML must return a hard-block verdict with
  // reason containing "phase_53_green".
  // Exercises the try/catch at readStateFieldOrThrow via direct checkPhase53 invocation —
  // hard-blocks with reason referencing the unreadable field (phase_53_green).
  test("F4-4 — checkPhase53 with chmod-000 build-state.yaml returns hard-block verdict naming phase_53_green", () => {
    writeFileSync(f4StatePath, [
      'task: "x"',
      'complexity: "FEATURE"',
      'current_phase: "5.3"',
    ].join("\n") + "\n");
    chmodSync(f4StatePath, 0o000);
    let verdict: GateVerdict;
    try {
      verdict = _gateAny.checkPhase53(f4Dir) as GateVerdict;
    } finally {
      chmodSync(f4StatePath, 0o644);
    }
    expect(verdict.decision).toBe("block");
    expect(verdict.severity).toBe("hard");
    expect(verdict.reason).toMatch(/phase_53_green/);
  });

  // F4-5: CLI (spawnSync) with chmod-000 YAML must exit 1, emit "state file unreadable"
  // to stderr, and write a hard-block JSON verdict to stdout.
  // After GREEN: import.meta.main catch adds StateReadError branch → emitFatalBlock
  // with message "state file unreadable: <path>: <err.message>".
  // RED: mainCLI returns pass() (readStateField null-tolerant) → exit 0, no stderr message.
  test("F4-5 — CLI with chmod-000 build-state.yaml exits 1, stderr matches /state file unreadable/, stdout contains hard-block JSON", () => {
    writeFileSync(f4StatePath, [
      'task: "x"',
      'complexity: "FEATURE"',
      'mode: "AUTONOMOUS"',
      'current_phase: "4"',
    ].join("\n") + "\n");
    chmodSync(f4StatePath, 0o000);

    const scriptPath = join(
      new URL(import.meta.url).pathname,
      "..",   // __tests__
      "..",   // ts
      "build-phase-gate.ts",
    );

    let res: ReturnType<typeof spawnSync>;
    try {
      const childEnv = { ...process.env };
      delete childEnv.HAL_DIR;
      delete childEnv.BYTEDIGGER_CONFIG;
      res = spawnSync("bun", ["run", scriptPath], {
        cwd: f4Dir,
        encoding: "utf8",
        timeout: 15000,
        env: childEnv,
      });
    } finally {
      chmodSync(f4StatePath, 0o644);
    }

    expect(res.status).toBe(1);
    expect(res.stderr ?? "").toMatch(/state file unreadable/);

    // stdout must contain a valid JSON object with decision=block, severity=hard.
    const stdoutTrimmed = (res.stdout ?? "").trim();
    expect(stdoutTrimmed).toBeTruthy();
    const parsed = JSON.parse(stdoutTrimmed) as Record<string, unknown>;
    expect(parsed["decision"]).toBe("block");
    expect(parsed["severity"]).toBe("hard");

    // Observability contract: stderr must contain a build-complete event with
    // outcome="fatal" and a filePath field (guards emitBuildComplete payload shape).
    const stderrLines = (res.stderr ?? "").split("\n");
    const eventLines = stderrLines.filter((l) => l.startsWith("[bytedigger:event]"));
    const parsedEvents = eventLines.map((l) => {
      try {
        return JSON.parse(l.replace(/^\[bytedigger:event\]\s+/, "")) as Record<string, unknown>;
      } catch {
        return null;
      }
    }).filter(Boolean) as Record<string, unknown>[];
    const buildComplete = parsedEvents.find((e) => e["event"] === "build-complete");
    expect(buildComplete).toBeDefined();
    const meta = buildComplete!["metadata"] as Record<string, unknown> | undefined;
    expect(meta?.["outcome"]).toBe("fatal");
    expect(meta?.["filePath"]).toBeDefined();
  });
});
