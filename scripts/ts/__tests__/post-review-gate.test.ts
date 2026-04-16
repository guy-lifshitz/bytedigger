/**
 * post-review-gate.test.ts — RED tests for F3 (Post-Review Gate) + F10 (Reviewers Config)
 *
 * All tests WILL FAIL until build-phase-gate.ts is modified to implement
 * F3 (semantic skip / Boy Scout enforcement) and F10 (reviewers.mode config).
 * Tests verify spec §6 Group 3 (P6-F3-01..16, P6-F10-01..07, P6-regress-01..02).
 *
 * Helpers mirror build-phase-gate.test.ts pattern (writeState, temp dirs).
 */

import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  chmodSync,
  rmSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { dispatchPhase, loadConfig } from "../build-phase-gate.ts";

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

let dir: string;
const savedCwd = process.cwd();

/**
 * Write build-state.yaml in the temp dir.
 * Mirrors the helper from build-phase-gate.test.ts exactly.
 */
function writeState(fields: Record<string, string>): void {
  const lines = Object.entries(fields).map(([k, v]) =>
    /^(true|false|\d+(\.\d+)?)$/.test(v) ? `${k}: ${v}` : `${k}: "${v}"`,
  );
  writeFileSync(join(dir, "build-state.yaml"), lines.join("\n") + "\n");
}

/**
 * Write a review markdown file in the temp dir.
 */
function writeReviewFile(name: string, content: string): void {
  writeFileSync(join(dir, name), content, "utf8");
}

/**
 * Write a bytedigger.json config; set BYTEDIGGER_CONFIG env var.
 * Returns cleanup function.
 */
function writeConfig(cfg: Record<string, unknown>): () => void {
  const cfgDir = mkdtempSync(join(tmpdir(), "prg-cfg-"));
  const cfgPath = join(cfgDir, "bytedigger.json");
  writeFileSync(cfgPath, JSON.stringify(cfg), "utf8");
  const prev = process.env.BYTEDIGGER_CONFIG;
  process.env.BYTEDIGGER_CONFIG = cfgPath;
  return () => {
    if (prev === undefined) {
      delete process.env.BYTEDIGGER_CONFIG;
    } else {
      process.env.BYTEDIGGER_CONFIG = prev;
    }
    rmSync(cfgDir, { recursive: true, force: true });
  };
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

/** Standard Phase 6 state fields (all findings resolved). */
function writePhase6State(overrides: Record<string, string> = {}): void {
  writeState({
    task: "test-task",
    complexity: "FEATURE",
    mode: "AUTONOMOUS",
    current_phase: "6",
    last_updated: nowIso(),
    ...overrides,
  });
}

/** Read state file as raw string. */
function readState(): string {
  return readFileSync(join(dir, "build-state.yaml"), "utf8");
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "post-review-gate-"));
  process.chdir(dir);
});

afterEach(() => {
  process.chdir(savedCwd);
  rmSync(dir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// F3 Tests: P6-F3-01 to P6-F3-16
// ---------------------------------------------------------------------------

describe("F3 — Post-Review Gate: Boy Scout Rule", () => {
  test("P6-F3-01 — clean pass: no review files, no findings → post_review_gate:pass written", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
    // F3 must write post_review_gate: pass to state
    const state = readState();
    expect(state).toMatch(/^post_review_gate: pass$/m);
  });

  test("P6-F3-02 — findings_skipped=1 → hard block (reason mentions phase_6_findings_skipped)", () => {
    writePhase6State({
      phase_6_findings_total: "3",
      phase_6_findings_fixed: "2",
      phase_6_findings_skipped: "1",
    });

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
    expect(v.reason).toMatch(/phase_6_findings_skipped/);
  });

  test("P6-F3-03 — Boy Scout: total=3, fixed=2, skipped=0 → hard block (Boy Scout Rule)", () => {
    writePhase6State({
      phase_6_findings_total: "3",
      phase_6_findings_fixed: "2",
      phase_6_findings_skipped: "0",
    });

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
    expect(v.reason).toMatch(/Boy Scout Rule/i);
    // semantic_skip_check: fail must be written
    const state = readState();
    expect(state).toMatch(/^semantic_skip_check: fail$/m);
  });

  test("P6-F3-04 — Boy Scout: total=3, fixed=3, skipped=0 → no Boy Scout block", () => {
    writePhase6State({
      phase_6_findings_total: "3",
      phase_6_findings_fixed: "3",
      phase_6_findings_skipped: "0",
    });

    const v = dispatchPhase({ cwd: dir });

    // Should NOT be a Boy Scout hard block — may pass or soft-block for other reasons
    if (v.decision === "block") {
      expect(v.reason).not.toMatch(/Boy Scout Rule/i);
    }
  });

  test("P6-F3-15 — FEATURE, total=0, fixed=0, skipped=0, no review files → pass (zero totals not blocked)", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });

    const v = dispatchPhase({ cwd: dir });

    // numTotal=0 means 0 > 0 is false → no Boy Scout block
    expect(v.decision).toBe("pass");
  });
});

describe("F3 — Post-Review Gate: Semantic phrase detection", () => {
  test("P6-F3-05 — forbidden phrase 'out of scope' in build-review-foo.md, skipped=0 → hard block", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });
    writeReviewFile("build-review-foo.md", "Finding 1: This is out of scope for now.\n");

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.exit_code).toBe(1);
    expect(v.reason).toMatch(/SEMANTIC SKIP/i);
    const state = readState();
    expect(state).toMatch(/^semantic_skip_check: fail$/m);
  });

  test("P6-F3-06 — smart quote: file contains won\\u2019t fix, skipped=0 → hard block (normalized)", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });
    // U+2019 is RIGHT SINGLE QUOTATION MARK — must match "won't fix" after normalization
    writeReviewFile("build-review-smart.md", "This is something we won\u2019t fix right now.\n");

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.reason).toMatch(/SEMANTIC SKIP/i);
  });

  test("P6-F3-07 — forbidden phrase in review file BUT skipped=1 → no semantic block (approved skip)", () => {
    writePhase6State({
      phase_6_findings_total: "1",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "1",
    });
    writeReviewFile("build-review-ok.md", "This finding is acceptable risk per approval.\n");

    // skipped=1 triggers the hard block for explicit skip — verify it's the skip block, not semantic
    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("block");
    // Must be the findings_skipped block, not SEMANTIC SKIP
    expect(v.reason).toMatch(/phase_6_findings_skipped/);
    expect(v.reason).not.toMatch(/SEMANTIC SKIP/i);
  });

  test("P6-F3-07b — forbidden phrase + skipped=0 but total=3, fixed=3 → semantic scan runs, phrase blocked", () => {
    // When total==fixed, Boy Scout passes, then semantic scan runs
    writePhase6State({
      phase_6_findings_total: "3",
      phase_6_findings_fixed: "3",
      phase_6_findings_skipped: "0",
    });
    writeReviewFile("build-review-b.md", "Note: this is technical debt we accept.\n");

    const v = dispatchPhase({ cwd: dir });

    // Semantic phrase "technical debt" must trigger hard block
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.reason).toMatch(/SEMANTIC SKIP/i);
  });

  test("P6-F3-08 — case-insensitive: 'NOT OUR RESPONSIBILITY' (UPPER) triggers hard block", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });
    writeReviewFile("build-review-upper.md", "This is NOT OUR RESPONSIBILITY to handle.\n");

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.reason).toMatch(/SEMANTIC SKIP/i);
  });

  test("P6-F3-09 — review file unreadable (chmod 000) → scan records failure", () => {
    // Skip if running as root
    if (process.getuid?.() === 0) return;

    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });
    const reviewPath = join(dir, "build-review-locked.md");
    writeFileSync(reviewPath, "out of scope\n");
    chmodSync(reviewPath, 0o000);

    try {
      // Must not throw
      const v = dispatchPhase({ cwd: dir });
      // The unreadable file increments count (count++), so behavior depends on implementation.
      // Either soft block or hard block — but no exception thrown.
      expect(v).toBeDefined();
      expect(v.decision).toBeDefined();
    } finally {
      chmodSync(reviewPath, 0o644); // restore for cleanup
    }
  });

  test("P6-F3-10 — review file in subdirectory (depth 2) found by walk", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });
    // Create subdirectory with review file
    const subDir = join(dir, "review-artifacts");
    mkdirSync(subDir, { recursive: true });
    writeFileSync(join(subDir, "review-detailed.md"), "This is pre-existing technical debt.\n");

    const v = dispatchPhase({ cwd: dir });

    // "pre-existing" and "technical debt" are forbidden phrases — must be detected
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    expect(v.reason).toMatch(/SEMANTIC SKIP/i);
  });

  test("P6-F3-11 — no forbidden phrases in review file → pass, semantic_skip_check:pass written", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });
    writeReviewFile("build-review-clean.md", "All findings have been addressed and fixed.\n");

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("pass");
    const state = readState();
    expect(state).toMatch(/^semantic_skip_check: pass$/m);
    expect(state).toMatch(/^semantic_skip_phrases_found: 0$/m);
  });
});

describe("F3 — Post-Review Gate: State writes (writeStateField idempotency)", () => {
  test("P6-F3-12 — post_review_gate written on pass; re-run writes field exactly once", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });

    // Run gate twice
    dispatchPhase({ cwd: dir });
    dispatchPhase({ cwd: dir });

    const state = readState();
    // post_review_gate must appear exactly once (strip-then-append = idempotent)
    const occurrences = state.match(/^post_review_gate:/gm) ?? [];
    expect(occurrences.length).toBe(1);
    expect(state).toMatch(/^post_review_gate: pass$/m);
  });

  test("P6-F3-13 — semantic_skip_phrases_found:0 written on clean pass", () => {
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
    });

    dispatchPhase({ cwd: dir });

    const state = readState();
    expect(state).toMatch(/^semantic_skip_phrases_found: 0$/m);
  });

  test("P6-F3-14 — semantic_skip_phrases_found written with count when phrases detected but skipped=1", () => {
    // skipped=1 triggers the findings_skipped hard block — can't test semantic pass path this way.
    // Instead: total=2, fixed=2, skipped=0 + 1 review file with 2 forbidden phrases → semantic block
    // To get semantic_skip_phrases_found written on pass, we need total=fixed, no explicit skip,
    // but a review file where skipped > 0... that is only possible in the pass path when phrases
    // are detected BUT skipped > 0 (scan completes, count written, no semantic block).
    // This requires findings_skipped == 0 but numSkipped derived from skippedRaw to be 0 too —
    // actually the only way to get phrases_found written with count > 0 AND pass is impossible
    // when skipped=0 (phrase found + skipped=0 = hard block). So this test is about the state
    // write order on the block path.
    writePhase6State({
      phase_6_findings_total: "2",
      phase_6_findings_fixed: "2",
      phase_6_findings_skipped: "0",
    });
    writeReviewFile("build-review-multi.md", "Issue A: out of scope. Issue B: known issue.\n");

    const v = dispatchPhase({ cwd: dir });

    // Hard block because phrases found and skipped=0
    expect(v.decision).toBe("block");
    expect(v.severity).toBe("hard");
    const state = readState();
    // semantic_skip_phrases_found must be written with the count (2 phrases)
    expect(state).toMatch(/^semantic_skip_phrases_found: [1-9]\d*$/m);
    expect(state).toMatch(/^semantic_skip_check: fail$/m);
  });

  test("P6-F3-16 — semantic-skip-phrases.json missing → gate hard-blocks at module load (FATAL)", () => {
    // This test MUST use spawnSync — module-load failures can't be caught in-process.
    // Strategy: set BYTEDIGGER_PHRASES_OVERRIDE env var pointing to a nonexistent file
    // (the implementation must check this env var before loading from default path).
    // If the implementation doesn't have this env var, we can't easily test it in-process.
    // We use spawnSync with a temp config and a script that renames the phrases file.

    // Attempt: run gate CLI with a temp cwd that has a build-state.yaml for phase 6
    // and a BYTEDIGGER_PHRASES_PATH env that points to nonexistent file.
    const testCwd = mkdtempSync(join(tmpdir(), "prg-f16-"));

    try {
      // Write minimal phase 6 state
      writeFileSync(
        join(testCwd, "build-state.yaml"),
        'current_phase: "6"\ncomplexity: "FEATURE"\nmode: "AUTONOMOUS"\nlast_updated: "2026-04-16T00:00:00Z"\ntask: "test"\n',
      );

      const scriptPath = join(
        new URL(import.meta.url).pathname,
        "..",
        "..",
        "build-phase-gate.ts",
      );

      // Set env var to point phrases file to nonexistent path
      const res = spawnSync("bun", ["run", scriptPath], {
        cwd: testCwd,
        encoding: "utf8",
        timeout: 15000,
        env: {
          ...process.env,
          BYTEDIGGER_PHRASES_PATH: "/nonexistent/phrases-file-does-not-exist.json",
        },
      });

      // On module-load FATAL, the gate must exit non-zero
      // Either exit 1 (hard block) or non-zero exit code from uncaught exception
      expect(res.status).not.toBe(0);
    } finally {
      rmSync(testCwd, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// F10 Tests: P6-F10-01 to P6-F10-07
// ---------------------------------------------------------------------------

describe("F10 — Reviewers Config: parseReviewerMode + loadConfig", () => {
  test("P6-F10-01 — no reviewers.mode field in config → default 'auto'", () => {
    const cleanup = writeConfig({});
    try {
      const cfg = loadConfig();
      expect(cfg.reviewers).toBeDefined();
      expect(cfg.reviewers.mode).toBe("auto");
    } finally {
      cleanup();
    }
  });

  test("P6-F10-02 — reviewers.mode: 'toolkit' loads correctly", () => {
    const cleanup = writeConfig({ reviewers: { mode: "toolkit" } });
    try {
      const cfg = loadConfig();
      expect(cfg.reviewers.mode).toBe("toolkit");
    } finally {
      cleanup();
    }
  });

  test("P6-F10-03 — reviewers.mode: 'generic' loads correctly", () => {
    const cleanup = writeConfig({ reviewers: { mode: "generic" } });
    try {
      const cfg = loadConfig();
      expect(cfg.reviewers.mode).toBe("generic");
    } finally {
      cleanup();
    }
  });

  test("P6-F10-04 — reviewers.mode: 'auto' explicit → loads correctly", () => {
    const cleanup = writeConfig({ reviewers: { mode: "auto" } });
    try {
      const cfg = loadConfig();
      expect(cfg.reviewers.mode).toBe("auto");
    } finally {
      cleanup();
    }
  });

  test("P6-F10-05 — unknown value 'custom' falls back to 'auto' (no warning required)", () => {
    const cleanup = writeConfig({ reviewers: { mode: "custom" } });
    try {
      const cfg = loadConfig();
      expect(cfg.reviewers.mode).toBe("auto");
    } finally {
      cleanup();
    }
  });

  test("P6-F10-06 — old-style reviewers object (SIMPLE/FEATURE/COMPLEX, no mode) → default 'auto'", () => {
    const cleanup = writeConfig({
      reviewers: { SIMPLE: 3, FEATURE: 6, COMPLEX: 6 },
    });
    try {
      const cfg = loadConfig();
      expect(cfg.reviewers.mode).toBe("auto");
    } finally {
      cleanup();
    }
  });

  test("P6-F10-07 — loadConfig() exposes cfg.reviewers.mode for downstream consumers (type check)", () => {
    // This test exists to verify that cfg.reviewers.mode is accessible without 'as any' cast.
    // At runtime it just verifies the field is a string.
    const cleanup = writeConfig({ reviewers: { mode: "toolkit" } });
    try {
      const cfg = loadConfig();
      // TypeScript compile: if this line doesn't type-check, the interface is wrong
      const mode: "toolkit" | "generic" | "auto" = cfg.reviewers.mode;
      expect(["toolkit", "generic", "auto"]).toContain(mode);
    } finally {
      cleanup();
    }
  });
});

// ---------------------------------------------------------------------------
// Regression tests: P6-regress-01, P6-regress-02
// ---------------------------------------------------------------------------

describe("Phase 6 regression tests (post-F3 behavior)", () => {
  test("P6-regress-01 — Phase 6 pass: total=3, fixed=3, skipped=0, no review files → pass", () => {
    writePhase6State({
      phase_6_findings_total: "3",
      phase_6_findings_fixed: "3",
      phase_6_findings_skipped: "0",
    });

    const v = dispatchPhase({ cwd: dir });

    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
  });

  test("P6-regress-02 — pre-existing post_review_gate:fail in state is now IGNORED (F3 writes, not reads)", () => {
    // Before F3: post_review_gate:fail in state would cause a hard block.
    // After F3: the read-check is removed. Gate writes the field, ignores old values.
    // This test verifies the deliberate behavior change (D3 from arch-phase6.md).
    writePhase6State({
      phase_6_findings_total: "0",
      phase_6_findings_fixed: "0",
      phase_6_findings_skipped: "0",
      // Old "fail" value from a previous run — F3 must ignore this
      post_review_gate: "fail",
    });

    const v = dispatchPhase({ cwd: dir });

    // F3 removes the read-check: pre-existing "fail" is irrelevant.
    // Gate re-runs the checks and writes "pass" if all checks pass.
    expect(v.decision).toBe("pass");
    expect(v.exit_code).toBe(0);
    // New "pass" must be written, overwriting the old "fail"
    const state = readState();
    expect(state).toMatch(/^post_review_gate: pass$/m);
    // Only one occurrence — strip-then-append is idempotent
    const occurrences = state.match(/^post_review_gate:/gm) ?? [];
    expect(occurrences.length).toBe(1);
  });
});
