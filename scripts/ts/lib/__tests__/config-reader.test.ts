// RED — Phase 5.1. Tests fail until config-reader.ts is implemented.
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readConfigField, resolveConfigPath } from "../config-reader.ts";

let dir: string;
const savedEnv = { ...process.env };

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "config-reader-"));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  process.env = { ...savedEnv };
});

describe("readConfigField", () => {
  test("U9 — missing config file returns undefined, never throws", () => {
    process.env.BYTEDIGGER_CONFIG = join(dir, "no-such-file.json");
    expect(() => readConfigField("gate_backend")).not.toThrow();
    expect(readConfigField("gate_backend")).toBeUndefined();
  });

  test("reads gate_backend from BYTEDIGGER_CONFIG path", () => {
    const cfg = join(dir, "bytedigger.json");
    writeFileSync(cfg, JSON.stringify({ gate_backend: "ts" }));
    process.env.BYTEDIGGER_CONFIG = cfg;
    expect(readConfigField<string>("gate_backend")).toBe("ts");
  });

  test("missing field returns undefined", () => {
    const cfg = join(dir, "bytedigger.json");
    writeFileSync(cfg, JSON.stringify({ gates_enabled: true }));
    process.env.BYTEDIGGER_CONFIG = cfg;
    expect(readConfigField("gate_backend")).toBeUndefined();
  });

  test("unparseable JSON returns undefined, never throws", () => {
    const cfg = join(dir, "bytedigger.json");
    writeFileSync(cfg, "{not valid json");
    process.env.BYTEDIGGER_CONFIG = cfg;
    expect(() => readConfigField("gate_backend")).not.toThrow();
    expect(readConfigField("gate_backend")).toBeUndefined();
  });

  test("resolveConfigPath honors BYTEDIGGER_CONFIG", () => {
    process.env.BYTEDIGGER_CONFIG = "/tmp/custom-bytedigger.json";
    expect(resolveConfigPath()).toBe("/tmp/custom-bytedigger.json");
  });

  test("resolveConfigPath honors CLAUDE_PLUGIN_ROOT when BYTEDIGGER_CONFIG unset", () => {
    delete process.env.BYTEDIGGER_CONFIG;
    process.env.CLAUDE_PLUGIN_ROOT = "/opt/plugin";
    expect(resolveConfigPath()).toBe("/opt/plugin/bytedigger.json");
  });
});
