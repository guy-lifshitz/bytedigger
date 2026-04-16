// Unit tests for state-reader.ts — bash-parity YAML field extraction.
// Covers spec §7.3 U1–U3 + Phase 2 Sprint A F4 (U4–U8).
import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readStateField, readStateFieldOrThrow, StateReadError } from "../state-reader.ts";

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

// ---------------------------------------------------------------------------
// F4: readStateFieldOrThrow — missing vs unreadable distinction at library layer
// Tests U4–U8 (Phase 2 Sprint A spec).
// ---------------------------------------------------------------------------
describe("readStateFieldOrThrow — missing vs unreadable distinction (F4)", () => {
  test("U4 — returns null for missing file (file does not exist)", () => {
    // Missing file must return null (not throw), same as readStateField.
    const result = readStateFieldOrThrow(join(dir, "does-not-exist.yaml"), "anything");
    expect(result).toBeNull();
  });

  test("U5 — throws StateReadError for unreadable file (chmod 000)", () => {
    // Unreadable file (permission denied) must throw StateReadError,
    // NOT return null. This is the key distinction vs readStateField.
    writeFileSync(yaml, "task: x\n");
    chmodSync(yaml, 0o000);
    try {
      expect(() => readStateFieldOrThrow(yaml, "task")).toThrow(StateReadError);
    } finally {
      // Restore permissions so afterEach rmSync can clean up.
      chmodSync(yaml, 0o644);
    }
  });

  test("U6 — returns field value for valid readable file", () => {
    writeFileSync(yaml, 'complexity: "FEATURE"\n');
    const result = readStateFieldOrThrow(yaml, "complexity");
    expect(result).toBe("FEATURE");
  });

  test("U7 — StateReadError message contains the file path", () => {
    writeFileSync(yaml, "task: x\n");
    chmodSync(yaml, 0o000);
    try {
      let caught: unknown = null;
      try {
        readStateFieldOrThrow(yaml, "task");
      } catch (err) {
        caught = err;
      }
      expect(caught).toBeInstanceOf(StateReadError);
      if (caught instanceof StateReadError) {
        expect(caught.message).toContain(yaml);
      }
    } finally {
      chmodSync(yaml, 0o644);
    }
  });

  test("U8 — readStateField (original) still returns null for unreadable file (regression)", () => {
    // readStateField must NOT be changed to throw — it must remain null-returning
    // for unreadable files. This test ensures F4 does not regress existing behavior.
    writeFileSync(yaml, "task: x\n");
    chmodSync(yaml, 0o000);
    try {
      const result = readStateField(yaml, "task");
      expect(result).toBeNull();
    } finally {
      chmodSync(yaml, 0o644);
    }
  });
});
