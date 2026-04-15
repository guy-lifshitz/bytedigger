// Unit tests for resolveConfigPath (ByteDigger config discovery).
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveConfigPath } from "../config-reader.ts";

let dir: string;
const savedEnv = { ...process.env };

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "config-reader-"));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  process.env = { ...savedEnv };
});

describe("resolveConfigPath", () => {
  test("honors BYTEDIGGER_CONFIG", () => {
    process.env.BYTEDIGGER_CONFIG = "/tmp/custom-bytedigger.json";
    expect(resolveConfigPath()).toBe("/tmp/custom-bytedigger.json");
  });

  test("honors CLAUDE_PLUGIN_ROOT when BYTEDIGGER_CONFIG unset", () => {
    delete process.env.BYTEDIGGER_CONFIG;
    process.env.CLAUDE_PLUGIN_ROOT = "/opt/plugin";
    expect(resolveConfigPath()).toBe("/opt/plugin/bytedigger.json");
  });

  test("falls back to repo-root relative path when no env vars set", () => {
    delete process.env.BYTEDIGGER_CONFIG;
    delete process.env.CLAUDE_PLUGIN_ROOT;
    const p = resolveConfigPath();
    expect(p.endsWith("/bytedigger.json")).toBe(true);
  });
});
