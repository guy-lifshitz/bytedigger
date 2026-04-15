// RED — Phase 5.1. Tests fail until state-reader.ts is ported from HAL.
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readStateField } from "../state-reader.ts";

let dir: string;
let yaml: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "state-reader-"));
  yaml = join(dir, "build-state.yaml");
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("readStateField — line-regex extraction (HAL parity)", () => {
  test("U1 — handles CRLF line endings without \\r contamination", () => {
    writeFileSync(
      yaml,
      "task: \"x\"\r\ncomplexity: FEATURE\r\ncurrent_phase: \"4\"\r\n",
    );
    expect(readStateField(yaml, "complexity")).toBe("FEATURE");
    expect(readStateField(yaml, "current_phase")).toBe("4");
  });

  test("U2 — mode:'last' returns last occurrence of duplicate key", () => {
    writeFileSync(
      yaml,
      [
        "current_phase: \"4\"",
        "current_phase: \"5\"",
        "current_phase: \"5.3\"",
        "",
      ].join("\n"),
    );
    expect(readStateField(yaml, "current_phase", "last")).toBe("5.3");
  });

  test("U2b — mode:'first' (default) returns first occurrence", () => {
    writeFileSync(
      yaml,
      ["current_phase: \"4\"", "current_phase: \"5\"", ""].join("\n"),
    );
    expect(readStateField(yaml, "current_phase")).toBe("4");
    expect(readStateField(yaml, "current_phase", "first")).toBe("4");
  });

  test("U3 — missing field returns null (not undefined, not throw)", () => {
    writeFileSync(yaml, "task: \"x\"\ncomplexity: FEATURE\n");
    expect(readStateField(yaml, "nonexistent_field")).toBeNull();
  });

  test("U3b — missing file returns null", () => {
    expect(readStateField(join(dir, "does-not-exist.yaml"), "anything")).toBeNull();
  });

  test("U3c — strips surrounding double quotes", () => {
    writeFileSync(yaml, "task: \"hello world\"\n");
    expect(readStateField(yaml, "task")).toBe("hello world");
  });

  test("U3d — unquoted scalars returned as-is", () => {
    writeFileSync(yaml, "complexity: FEATURE\n");
    expect(readStateField(yaml, "complexity")).toBe("FEATURE");
  });
});
