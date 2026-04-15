#!/usr/bin/env bun
/**
 * build-phase-gate.ts — ByteDigger TypeScript phase gate.
 *
 * Mirrors scripts/build-gate.sh bit-for-bit for exit codes, verdict shape,
 * and reason strings (including doc-ref suffixes), plus a small number of
 * TS-only extensions tested by the bun unit suite:
 *   - BYPASS #12: stale-artifact freshness check (build-opus-validation.md vs
 *     build-state.yaml mtime) — TS-only, soft block.
 *   - Phase 0.5 gate (bash exits 0 for phases 0-3; TS adds 0.5 handling).
 *   - Phase 7 stub — always passes in Phase 1 of the ByteDigger/HALForge
 *     unification; learning-DB validation lands in unification Phase 2.
 *     The `disablePhase7` config flag is reserved for that wiring.
 *
 * Exit codes (CLI and exit_code field on Verdict):
 *   0 — pass / gates disabled / not a build session / bypass
 *   1 — hard block (downgrade, 5.3 green, 5.5 assertion gaming, 6 findings_skipped,
 *       6 post_review_gate, 7 review_complete != pass)
 *   2 — soft block (missing fields, stale artifact)
 *
 * Safety: never reads stdin — the harness pipes JSON to the hook, and draining
 * it in-process would stall the harness. scripts/gate-dispatcher.sh drains on
 * our behalf before invoking bun.
 */

import {
  appendFileSync,
  existsSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join, dirname, basename } from "node:path";
import { readStateField } from "./lib/state-reader.ts";
import { resolveConfigPath } from "./lib/config-reader.ts";

// ---------------------------------------------------------------------------
// Types — discriminated union GateVerdict keeps illegal states unrepresentable.
// ---------------------------------------------------------------------------

// Aliases below are referenced by GateVerdict arms — single source of truth
// for the decision / severity / exit-code vocabulary. Changing e.g. Severity
// here propagates to the discriminated union and to hardBlock's Extract<>.
export type Decision = "pass" | "block";
export type Severity = "soft" | "hard";
export type ExitCode = 0 | 1 | 2;

export type GateVerdict =
  | {
      readonly decision: Extract<Decision, "pass">;
      readonly phase?: string;
      readonly exit_code: Extract<ExitCode, 0>;
    }
  | {
      readonly decision: Extract<Decision, "block">;
      readonly severity: Extract<Severity, "soft">;
      readonly reason: string;
      readonly phase?: string;
      readonly exit_code: Extract<ExitCode, 2>;
    }
  | {
      readonly decision: Extract<Decision, "block">;
      readonly severity: Extract<Severity, "hard">;
      readonly reason: string;
      readonly phase?: string;
      readonly exit_code: Extract<ExitCode, 1>;
    };

export interface DispatchInput {
  readonly cwd: string;
}

// ---------------------------------------------------------------------------
// Verdict smart constructors — exported so Phase 2+ consumers never hand-roll.
// ---------------------------------------------------------------------------

export function pass(phase?: string): Extract<GateVerdict, { decision: "pass" }> {
  return { decision: "pass", phase, exit_code: 0 };
}

export function softBlock(
  reason: string,
  phase?: string,
): Extract<GateVerdict, { severity: "soft" }> {
  return { decision: "block", severity: "soft", reason, phase, exit_code: 2 };
}

export function hardBlock(
  reason: string,
  phase?: string,
): Extract<GateVerdict, { severity: "hard" }> {
  return { decision: "block", severity: "hard", reason, phase, exit_code: 1 };
}

// ---------------------------------------------------------------------------
// Config (bytedigger.json) — used by CLI; dispatchPhase itself is pure.
// ---------------------------------------------------------------------------

interface ByteDiggerConfig {
  readonly gates_enabled: boolean;
  readonly tdd_mandatory: boolean;
  readonly simple_reviewers: number;
  readonly feature_reviewers: number;
  readonly complex_reviewers: number;
  /**
   * disablePhase7 — when true, skips the bash-parity `review_complete`
   * soft-block in `checkPhase7` (see lines ~540-560). Reserved for projects
   * that will opt out of the learning-DB hook in unification Phase 2.
   * Default false: Phase 7 enforces `review_complete == pass`, mirroring
   * `gate_phase_7` in build-gate.sh.
   */
  readonly disablePhase7: boolean;
}

function loadConfig(): ByteDiggerConfig {
  const defaults: ByteDiggerConfig = {
    gates_enabled: true,
    tdd_mandatory: true,
    simple_reviewers: 3,
    feature_reviewers: 6,
    complex_reviewers: 6,
    disablePhase7: false,
  };
  let path: string;
  try {
    path = resolveConfigPath();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(`[gate] WARN: failed to resolve config path: ${msg} — using defaults\n`);
    return defaults;
  }
  if (!existsSync(path)) return defaults;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
    return {
      gates_enabled: parsed.gates_enabled !== false,
      tdd_mandatory: parsed.tdd_mandatory !== false,
      simple_reviewers: Number(parsed.simple_reviewers ?? 3),
      feature_reviewers: Number(parsed.feature_reviewers ?? 6),
      complex_reviewers: Number(parsed.complex_reviewers ?? 6),
      disablePhase7: parsed.disablePhase7 === true,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(
      `[gate] WARN: failed to parse ${path}: ${msg} — using defaults\n`,
    );
    return defaults;
  }
}

// ---------------------------------------------------------------------------
// Global pre-phase checks (complexity downgrade + freshness)
// ---------------------------------------------------------------------------

interface GlobalResult {
  readonly missingFields: readonly string[];
  readonly hardBlock?: Extract<GateVerdict, { severity: "hard" }>;
}

export function runGlobalPrePhaseChecks(cwd: string): GlobalResult {
  const statePath = join(cwd, "build-state.yaml");
  const missingFields: string[] = [];

  // BYPASS #4: Complexity downgrade detection.
  const metadataPath = join(cwd, "build-metadata.json");
  let trustedComplexity = "";
  if (existsSync(metadataPath)) {
    try {
      const meta = JSON.parse(readFileSync(metadataPath, "utf8")) as { complexity?: string };
      trustedComplexity = (meta.complexity ?? "").toString().trim();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      process.stderr.write(
        `[gate] WARN: failed to parse build-metadata.json: ${msg}\n`,
      );
      return {
        missingFields,
        hardBlock: hardBlock(
          "failed to parse build-metadata.json — cannot verify complexity",
        ),
      };
    }
  }
  if (trustedComplexity) {
    const yamlComplexity = (readStateField(statePath, "complexity") ?? "").trim();
    if (yamlComplexity && trustedComplexity !== yamlComplexity) {
      return {
        missingFields,
        hardBlock: hardBlock(
          `complexity downgrade detected: metadata=${trustedComplexity} yaml=${yamlComplexity} (BYPASS #4)`,
        ),
      };
    }
  }

  // BYPASS #12: Artifact freshness — artifacts must postdate build-state.yaml.
  if (existsSync(statePath)) {
    let stateMtime = 0;
    try {
      const s = statSync(statePath);
      stateMtime = Math.floor((s.birthtimeMs || s.mtimeMs) / 1000);
      if (stateMtime <= 0) stateMtime = Math.floor(s.mtimeMs / 1000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      process.stderr.write(
        `[gate] WARN: stat build-state.yaml failed: ${msg}\n`,
      );
      stateMtime = 0;
    }
    if (stateMtime > 0) {
      const ARTIFACTS = [
        "build-opus-validation.md",
        "build-tests.md",
        "build-red-output.log",
        "build-architecture.md",
        "build-plan-review.md",
      ];
      for (const a of ARTIFACTS) {
        const p = join(cwd, a);
        if (!existsSync(p)) continue;
        try {
          const artMtime = Math.floor(statSync(p).mtimeMs / 1000);
          if (artMtime < stateMtime) {
            missingFields.push(
              `${a} is STALE (predates build-state.yaml creation — BYPASS #12 freshness check). Regenerate for current session.`,
            );
          }
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          // Convert silent miss into an explicit soft finding.
          missingFields.push(`${a} stat failed: ${msg}`);
        }
      }
    }
  }

  return { missingFields };
}

// ---------------------------------------------------------------------------
// Phase handlers
// ---------------------------------------------------------------------------

function getComplexity(cwd: string): string {
  const statePath = join(cwd, "build-state.yaml");
  const metaPath = join(cwd, "build-metadata.json");
  if (existsSync(metaPath)) {
    try {
      const meta = JSON.parse(readFileSync(metaPath, "utf8")) as { complexity?: string };
      const mc = (meta.complexity ?? "").toString().trim().toUpperCase();
      if (mc) return mc;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      process.stderr.write(`[gate] WARN: getComplexity parse failed: ${msg}\n`);
    }
  }
  return (readStateField(statePath, "complexity") ?? "").trim().toUpperCase();
}

function checkPhase05(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  const missing: string[] = [];

  const preBuildGate = (readStateField(statePath, "pre_build_gate") ?? "").trim();
  if (preBuildGate !== "pass") missing.push("pre_build_gate: pass");

  const learnings = (readStateField(statePath, "phase_05_learnings") ?? "").trim();
  if (!learnings) missing.push("phase_05_learnings");

  if (missing.length > 0) return softBlock(joinMissing(missing), "0.5");
  return pass("0.5");
}

function fieldMissing(
  statePath: string,
  field: string,
  expected: string,
): string | null {
  const v = (readStateField(statePath, field) ?? "").trim();
  if (v !== expected) {
    return `${field}=${expected} (got: ${v || "<missing>"})`;
  }
  return null;
}

function checkPhase4(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  const missing: string[] = [];

  const m = fieldMissing(statePath, "phase_4_architect", "complete");
  if (m) missing.push(m);

  // scratchpad_stale: research dir must contain findings-*.md
  const scratchpad = (readStateField(statePath, "scratchpad_dir") ?? "").trim();
  if (scratchpad) {
    const researchDir = join(scratchpad, "research");
    let hasFindings = false;
    if (existsSync(researchDir)) {
      try {
        const entries = readdirSync(researchDir);
        hasFindings = entries.some((f) => /^findings-.*\.md$/.test(f));
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        // Permission error is fail-closed (triggers hard block below) but
        // surface the cause so operators can diagnose.
        process.stderr.write(
          `[gate] WARN: readdir ${researchDir} failed: ${msg}\n`,
        );
        hasFindings = false;
      }
    }
    if (!hasFindings) {
      // Mirror bash strip-then-append: if a prior scratchpad_stale line exists,
      // remove it and re-append so the persisted state is `scratchpad_stale: true`.
      try {
        const content = readFileSync(statePath, "utf8");
        const filtered = content
          .split("\n")
          .filter((l) => !/^scratchpad_stale:/.test(l))
          .join("\n");
        const normalized = filtered.endsWith("\n") ? filtered : filtered + "\n";
        writeFileAtomic(statePath, normalized + "scratchpad_stale: true\n");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        process.stderr.write(
          `[gate] WARN: failed to persist scratchpad_stale: ${msg}\n`,
        );
      }
      return hardBlock(
        `scratchpad_stale: no findings-*.md found in ${researchDir} — Phase 2 exploration must complete before Phase 4`,
        "4",
      );
    }
  }

  if (missing.length > 0) return softBlock(joinMissing(missing), "4");
  return pass("4");
}

// Bash uses `printf '%s; ' "${MISSING_FIELDS[@]}"` which appends `"; "` after
// every entry including the last — shadow mode compares stdout byte-for-byte,
// so the trailing separator is load-bearing.
function joinMissing(fields: readonly string[]): string {
  return fields.map((f) => `${f}; `).join("");
}

function writeFileAtomic(path: string, content: string): void {
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, content);
  renameSync(tmp, path);
}

function checkPhase45(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  if (getComplexity(cwd) === "SIMPLE") return pass("4.5");
  const planReview = (readStateField(statePath, "plan_review") ?? "").trim();
  if (planReview !== "pass") return softBlock("plan_review=pass", "4.5");
  return pass("4.5");
}

function checkPhase51(cwd: string): GateVerdict {
  const redLog = join(cwd, "build-red-output.log");
  const missing: string[] = [];
  if (!existsSync(redLog) || statSync(redLog).size === 0) {
    missing.push("missing artifact: build-red-output.log");
  } else {
    let content = "";
    let readError: string | null = null;
    try {
      content = readFileSync(redLog, "utf8");
    } catch (err) {
      readError = err instanceof Error ? err.message : String(err);
    }
    if (readError) {
      missing.push(`build-red-output.log unreadable: ${readError}`);
    } else if (!/FAIL|ERROR|FAILED|not ok/.test(content)) {
      missing.push("build-red-output.log contains no failures (tests must be RED)");
    }
  }
  if (missing.length > 0) return softBlock(joinMissing(missing), "5.1");
  return pass("5.1");
}

function checkPhase52(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  const missing: string[] = [];
  const opus = (readStateField(statePath, "opus_validation") ?? "").trim();
  if (opus !== "pass") missing.push("opus_validation=pass");
  const gherkin = (readStateField(statePath, "phase_52a_gherkin") ?? "").trim();
  if (gherkin !== "complete") missing.push("phase_52a_gherkin=complete");
  if (missing.length > 0) return softBlock(joinMissing(missing), "5.2");
  return pass("5.2");
}

function checkPhase53(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  const green = (readStateField(statePath, "phase_53_green") ?? "").trim();
  if (green !== "complete") {
    return hardBlock(
      "phase_53_green not complete — GREEN phase must pass before proceeding",
      "5.3",
    );
  }
  return pass("5.3");
}

function checkPhase5(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  if (getComplexity(cwd) === "SIMPLE") return pass("5");
  const missing: string[] = [];
  for (const [field, expected] of [
    ["phase_4_architect", "complete"],
    ["plan_review", "pass"],
    ["phase_5_implement", "complete"],
    ["opus_validation", "pass"],
  ] as const) {
    const v = (readStateField(statePath, field) ?? "").trim();
    if (v !== expected) missing.push(`${field}=${expected}`);
  }
  if (missing.length > 0) return softBlock(joinMissing(missing), "5");
  return pass("5");
}

function checkPhase55(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  const gaming = (readStateField(statePath, "assertion_gaming_detected") ?? "").trim();
  if (gaming === "true") {
    return hardBlock(
      "assertion_gaming_detected — tests were written to pass without real implementation",
      "5.5",
    );
  }
  const integrity = (readStateField(statePath, "test_integrity_check") ?? "").trim();
  if (!integrity) return softBlock("test_integrity_check has no value", "5.5");
  return pass("5.5");
}

const SKIP_PHRASES: readonly string[] = [
  "not our responsibility",
  "not related",
  "acceptable risk",
  "pre-existing",
  "out of scope",
  "known issue",
  "fix later",
  "will address in follow-up",
  "good enough",
  "wont fix",
  "defer",
  "low severity, skip",
  "low priority, skip",
  "cosmetic",
  "won't fix",
  "wontfix",
  "technical debt",
  "acceptable for",
];

function scanSemanticSkip(cwd: string): string[] {
  const hits: string[] = [];
  // Mirror bash find(1) parity: depth-2 walk matching both build-review-*.md
  // and *review*.md — the second pattern is a superset of the first.
  const matches: string[] = [];
  const walk = (dir: string, depth: number): void => {
    if (depth > 2) return;
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Convert silent miss into a soft-block finding — attackers could
      // otherwise suppress the semantic skip scan with a chmod.
      hits.push(`semantic skip scan FAILED on ${dir}: ${msg}`);
      return;
    }
    for (const e of entries) {
      const p = join(dir, e.name);
      if (e.isDirectory()) {
        walk(p, depth + 1);
      } else if (e.isFile()) {
        if (/review.*\.md$/.test(e.name)) {
          matches.push(p);
        }
      }
    }
  };
  walk(cwd, 1);

  for (const file of matches) {
    let content = "";
    try {
      content = readFileSync(file, "utf8");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      hits.push(`semantic skip scan FAILED on ${file}: ${msg}`);
      continue;
    }
    const lc = content.toLowerCase();
    for (const phrase of SKIP_PHRASES) {
      if (lc.includes(phrase.toLowerCase())) {
        hits.push(`semantic skip detected: '${phrase}' in ${basename(file)}`);
      }
    }
  }
  return hits;
}

function checkPhase6(cwd: string): GateVerdict {
  const statePath = join(cwd, "build-state.yaml");
  const missing: string[] = [];

  const totalRaw = (readStateField(statePath, "phase_6_findings_total") ?? "").trim();
  const fixedRaw = (readStateField(statePath, "phase_6_findings_fixed") ?? "").trim();
  const total = /^[0-9]+$/.test(totalRaw) ? parseInt(totalRaw, 10) : 0;
  const fixed = /^[0-9]+$/.test(fixedRaw) ? parseInt(fixedRaw, 10) : 0;
  if (total > 0 && fixed < total) {
    missing.push(`unfixed findings: ${fixed}/${total} fixed`);
  }

  const skippedRaw = (readStateField(statePath, "phase_6_findings_skipped") ?? "").trim();
  if (/^[0-9]+$/.test(skippedRaw) && parseInt(skippedRaw, 10) > 0) {
    return hardBlock(
      `phase_6_findings_skipped=${skippedRaw} — all findings must be fixed, zero exceptions (build.md:137)`,
      "6",
    );
  }

  const postReview = (readStateField(statePath, "post_review_gate") ?? "").trim();
  if (postReview && postReview !== "pass") {
    return hardBlock(
      `post_review_gate=${postReview} — must be 'pass' before proceeding to Phase 7 (build.md:137, phase-6-review.md:271)`,
      "6",
    );
  }

  // Semantic skip scan
  missing.push(...scanSemanticSkip(cwd));

  if (missing.length > 0) return softBlock(joinMissing(missing), "6");
  return pass("6");
}

function checkPhase7(cwd: string): GateVerdict {
  // Phase 7 stubbed until learning DB lands (unification Phase 2).
  //
  // Parity with bash: bash gate_phase_7 pushes to MISSING_FIELDS when
  // review_complete != "pass" → soft block. TS mirrors that behavior.
  // The disablePhase7 escape hatch skips the check entirely (reserved for
  // future learning-DB wiring — see ByteDiggerConfig.disablePhase7).
  const cfg = loadConfig();
  if (cfg.disablePhase7) return pass("7");
  const statePath = join(cwd, "build-state.yaml");
  const v = (readStateField(statePath, "review_complete") ?? "").trim();
  if (v !== "pass") {
    return softBlock(`review_complete=pass (got: ${v || "<missing>"})`, "7");
  }
  return pass("7");
}

// ---------------------------------------------------------------------------
// Dispatcher — synchronous, used by both unit tests and CLI.
// ---------------------------------------------------------------------------

export function dispatchPhase(input: DispatchInput): GateVerdict {
  const { cwd } = input;
  const statePath = join(cwd, "build-state.yaml");

  if (!existsSync(statePath)) {
    // No state → not a build session, pass-through.
    return pass();
  }

  const phase = (readStateField(statePath, "current_phase") ?? "").trim();
  if (!phase) {
    return pass();
  }

  // Global checks first (complexity downgrade is hard, artifact freshness is soft)
  const global = runGlobalPrePhaseChecks(cwd);
  if (global.hardBlock) return { ...global.hardBlock, phase };

  let verdict: GateVerdict;
  switch (phase) {
    case "0":
    case "1":
    case "2":
    case "3":
      return pass(phase);
    case "0.5":
    case "05":
      verdict = checkPhase05(cwd);
      break;
    case "4":
      verdict = checkPhase4(cwd);
      break;
    case "4.5":
    case "45":
      verdict = checkPhase45(cwd);
      break;
    case "5":
      verdict = checkPhase5(cwd);
      break;
    case "5.1":
    case "51":
      verdict = checkPhase51(cwd);
      break;
    case "5.2":
    case "52":
      verdict = checkPhase52(cwd);
      break;
    case "5.3":
    case "53":
      verdict = checkPhase53(cwd);
      break;
    case "5.5":
    case "55":
      verdict = checkPhase55(cwd);
      break;
    case "6":
      verdict = checkPhase6(cwd);
      break;
    case "7":
      verdict = checkPhase7(cwd);
      break;
    case "8":
      return pass(phase);
    default:
      return pass(phase);
  }

  // Merge global soft findings into the phase verdict.
  if (global.missingFields.length > 0) {
    if (verdict.decision === "pass") {
      return softBlock(joinMissing(global.missingFields), phase);
    } else if (verdict.severity === "soft") {
      const combined = [
        ...(verdict.reason ? [verdict.reason.replace(/; $/, "")] : []),
        ...global.missingFields,
      ];
      return softBlock(joinMissing(combined), phase);
    }
    // hard block: return as-is
  }
  return verdict;
}

// ---------------------------------------------------------------------------
// CLI wrapper — matches scripts/build-gate.sh contract byte-for-byte.
// ---------------------------------------------------------------------------

function cliResolveCwd(): string {
  if (process.env.BYTEDIGGER_CONFIG) {
    return dirname(process.env.BYTEDIGGER_CONFIG);
  }
  return process.cwd();
}

export function loopPreventionCLI(statePath: string, phase: string): boolean {
  // Returns true if bypass triggered (should exit 0), false if still blocking.
  let countRaw = "";
  try {
    const content = readFileSync(statePath, "utf8");
    const m = content.match(/^gate_block_counter:\s*(\S*)/m);
    countRaw = m ? m[1]!.replace(/["']/g, "") : "";
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(
      `[gate] WARN: loopPreventionCLI read failed: ${msg}\n`,
    );
    countRaw = "";
  }
  const count = parseInt(countRaw, 10) || 0;
  const newCount = count + 1;

  // Atomic rewrite: tempfile + rename, so a crash mid-write cannot corrupt
  // build-state.yaml. NOTE: still not concurrency-safe across parallel
  // gate processes — defer full flock to unification Phase 2 / batch-build.
  try {
    const content = readFileSync(statePath, "utf8");
    const filtered = content
      .split("\n")
      .filter((l) => !/^gate_block_counter:/.test(l))
      .join("\n");
    const normalized = filtered.endsWith("\n") ? filtered : filtered + "\n";
    writeFileAtomic(statePath, normalized + `gate_block_counter: ${newCount}\n`);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    // best-effort: state-file update is advisory, primary verdict must still emit
    process.stderr.write(
      `[gate] WARN: loopPreventionCLI write failed: ${msg}\n`,
    );
  }

  if (newCount > 3) {
    try {
      appendFileSync(statePath, `gate_bypass: true\ngate_bypass_phase: ${phase}\n`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      process.stderr.write(
        `[gate] WARN: loopPreventionCLI append gate_bypass failed: ${msg}\n`,
      );
    }
    return true;
  }
  return false;
}

/**
 * Wire-format payload emitted on stdout for a block verdict. Centralizes the
 * `HARD BLOCK:` prefix rule so cliOutputBlock, emitFatalBlock, and the
 * bun-not-found branch of gate-dispatcher.sh all agree on the shape.
 *
 * PARITY NOTE: bash build-gate.sh emits `{"decision","reason"}` on soft
 * blocks and `{"decision","severity":"hard","reason"}` on hard blocks
 * (the `severity` field was only added on the hard path for operator
 * visibility). TS must mirror that exactly or shadow-mode byte-compare
 * in gate-dispatcher.sh will flag every soft block as a divergence.
 * Hence `severity` is conditionally omitted for soft verdicts.
 */
type WirePayload =
  | { readonly decision: "block"; readonly reason: string }
  | {
      readonly decision: "block";
      readonly severity: "hard";
      readonly reason: string;
    };

function toWirePayload(
  v: Extract<GateVerdict, { decision: "block" }>,
): WirePayload {
  if (v.severity === "hard") {
    return {
      decision: "block",
      severity: "hard",
      reason: `HARD BLOCK: ${v.reason}`,
    };
  }
  return { decision: "block", reason: v.reason };
}

function cliOutputBlock(
  verdict: Extract<GateVerdict, { decision: "block" }>,
): never {
  const payload = toWirePayload(verdict);
  process.stdout.write(JSON.stringify(payload) + "\n");
  process.exit(verdict.exit_code);
}

function emitFatalBlock(msg: string): never {
  // Top-level fatal: emit JSON hard block to stdout so the harness sees a
  // real verdict, not an empty pipe. Matches dispatcher bun-not-found path
  // and cliOutputBlock shape (via toWirePayload).
  const payload = toWirePayload(hardBlock(`gate fatal: ${msg}`));
  process.stdout.write(JSON.stringify(payload) + "\n");
  process.stderr.write(`[build-phase-gate] fatal: ${msg}\n`);
  process.exit(1);
}

function mainCLI(): void {
  // Safety: never read stdin — gate-dispatcher.sh drains on our behalf.
  const cfg = loadConfig();
  if (!cfg.gates_enabled) {
    process.exit(0);
  }

  const cwd = cliResolveCwd();
  const statePath = join(cwd, "build-state.yaml");

  if (!existsSync(statePath)) {
    process.exit(0);
  }

  // Stale check: if mtime > 600s old, skip.
  try {
    const now = Math.floor(Date.now() / 1000);
    const mtime = Math.floor(statSync(statePath).mtimeMs / 1000);
    if (now - mtime > 600) process.exit(0);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(
      `[gate] WARN: stat build-state.yaml for staleness check failed: ${msg}\n`,
    );
  }

  const phase = (readStateField(statePath, "current_phase") ?? "").trim();
  if (!phase) {
    process.stderr.write(
      "WARN: build-state.yaml has no current_phase — skipping gate\n",
    );
    process.exit(0);
  }

  // Dispatch (with global checks embedded)
  const verdict = dispatchPhase({ cwd });

  if (verdict.decision === "pass") {
    return process.exit(0);
  }
  // verdict now narrows to a block arm (soft | hard).

  // Hard blocks exit immediately — bypass loop prevention.
  if (verdict.severity === "hard") {
    cliOutputBlock(verdict);
  }

  // Soft block: apply loop prevention.
  const bypassed = loopPreventionCLI(statePath, phase);
  if (bypassed) {
    return process.exit(0);
  }
  cliOutputBlock(verdict);
}

if (import.meta.main) {
  try {
    mainCLI();
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    emitFatalBlock(msg);
  }
}
