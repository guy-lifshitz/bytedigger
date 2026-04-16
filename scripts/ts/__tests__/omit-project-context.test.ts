// Unit tests for omitProjectContext config field (Phase 2 Sprint A F1).
// Spec: loadConfig must expose omitProjectContext, defaulting to false,
// honouring boolean true and coercing string "true" to true.
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "../build-phase-gate.ts";

let dir: string;
const savedEnv = { ...process.env };

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "omit-ctx-"));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  // Restore env (in case a test mutated BYTEDIGGER_CONFIG).
  for (const key of Object.keys(process.env)) {
    if (!(key in savedEnv)) {
      delete process.env[key];
    }
  }
  for (const [key, val] of Object.entries(savedEnv)) {
    process.env[key] = val;
  }
});

function writeConfig(content: Record<string, unknown>): string {
  const cfgPath = join(dir, "bytedigger.json");
  writeFileSync(cfgPath, JSON.stringify(content));
  process.env.BYTEDIGGER_CONFIG = cfgPath;
  return cfgPath;
}

describe("loadConfig — omitProjectContext (F1)", () => {
  test("C1 — omitProjectContext defaults to false when not set in config", () => {
    writeConfig({ gates_enabled: true });
    const cfg = loadConfig();
    expect(cfg.omitProjectContext).toBe(false);
  });

  test("C2 — omitProjectContext: true is honoured as boolean true", () => {
    writeConfig({ omitProjectContext: true });
    const cfg = loadConfig();
    expect(cfg.omitProjectContext).toBe(true);
  });

  test("C3 — omitProjectContext: \"true\" (string) is coerced to boolean true", () => {
    writeConfig({ omitProjectContext: "true" });
    const cfg = loadConfig();
    expect(cfg.omitProjectContext).toBe(true);
  });

  test("C4 — omitProjectContext: non-boolean non-\"true\" value is coerced to false", () => {
    writeConfig({ omitProjectContext: "yes" });
    const cfg = loadConfig();
    expect(cfg.omitProjectContext).toBe(false);
  });
});
