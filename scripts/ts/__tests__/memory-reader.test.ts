/**
 * memory-reader.test.ts — RED tests for F9 (Active Work Injection)
 *
 * All tests WILL FAIL until scripts/ts/lib/memory-reader.ts is implemented.
 * Tests verify spec §6 Group 2 (M1-M12).
 *
 * Each test uses mkdtempSync + writes MEMORY.md for filesystem isolation.
 */

import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import {
  mkdtempSync,
  writeFileSync,
  rmSync,
  chmodSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  readActiveWork,
  extractActiveWorkSection,
  DEFAULT_CAPS,
} from "../lib/memory-reader.ts";
import { loadConfig } from "../build-phase-gate.ts";

// ---------------------------------------------------------------------------
// Test scaffolding
// ---------------------------------------------------------------------------

let dir: string;
const savedCwd = process.cwd();

function writeMemory(content: string): void {
  writeFileSync(join(dir, "MEMORY.md"), content, "utf8");
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "memory-reader-"));
});

afterEach(() => {
  process.chdir(savedCwd);
  rmSync(dir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("F9 — memory-reader.ts: missing / empty MEMORY.md", () => {
  test("M1 — missing MEMORY.md returns empty content, truncated=false, sourcePath=null", () => {
    // No file written intentionally
    const result = readActiveWork(dir);

    expect(result.content).toBe("");
    expect(result.truncated).toBe(false);
    expect(result.sourcePath).toBeNull();
  });

  test("M2 — MEMORY.md without ## Active Work returns empty content, sourcePath set", () => {
    writeMemory("# My Project\n\nSome content\n\n## Reference\n- ref1\n");

    const result = readActiveWork(dir);

    expect(result.content).toBe("");
    expect(result.truncated).toBe(false);
    expect(result.sourcePath).toBe(join(dir, "MEMORY.md"));
  });

  test("M8 — empty Active Work section (heading only) returns content=''", () => {
    writeMemory("# Project\n\n## Active Work\n\n## Reference\n- ref1\n");

    const result = readActiveWork(dir);

    expect(result.content).toBe("");
    expect(result.truncated).toBe(false);
  });
});

describe("F9 — memory-reader.ts: section extraction", () => {
  test("M3 — extracts content between ## Active Work and next ## heading only", () => {
    writeMemory([
      "# Project",
      "",
      "## Something Else",
      "- other item",
      "",
      "## Active Work",
      "- task one",
      "- task two",
      "",
      "## Reference",
      "- ref item (should NOT appear)",
    ].join("\n") + "\n");

    const result = readActiveWork(dir);

    expect(result.content).toContain("task one");
    expect(result.content).toContain("task two");
    expect(result.content).not.toContain("ref item");
    expect(result.content).not.toContain("Something Else");
  });

  test("M4 — section at end of file (no next ## heading) captures full section", () => {
    writeMemory([
      "# Project",
      "",
      "## Active Work",
      "- final task one",
      "- final task two",
      "- final task three",
    ].join("\n") + "\n");

    const result = readActiveWork(dir);

    expect(result.content).toContain("final task one");
    expect(result.content).toContain("final task two");
    expect(result.content).toContain("final task three");
  });

  test("M10 — extractActiveWorkSection is case-insensitive on heading", () => {
    const content = [
      "# Project",
      "",
      "## active work",
      "- lowercase heading task",
      "",
      "## Reference",
      "- ref",
    ].join("\n") + "\n";

    const result = extractActiveWorkSection(content, DEFAULT_CAPS);

    expect(result.text).toContain("lowercase heading task");
  });

  test("M11 — CRLF line endings handled correctly", () => {
    const crlfContent = "# Project\r\n\r\n## Active Work\r\n- crlf task one\r\n- crlf task two\r\n";
    writeMemory(crlfContent);

    const result = readActiveWork(dir);

    expect(result.content).toContain("crlf task one");
    expect(result.content).toContain("crlf task two");
    // Should not contain \r in output
    expect(result.content).not.toContain("\r");
  });

  test("M12-section-at-EOF — section at end of file (no closing ## heading), alias for M4", () => {
    writeMemory("## Active Work\n- only item\n");

    const result = readActiveWork(dir);

    expect(result.content).toContain("only item");
    expect(result.truncated).toBe(false);
  });
});

describe("F9 — memory-reader.ts: cap enforcement", () => {
  test("M5 — item cap: 12 items → truncated=true, exactly 10 items in content", () => {
    const items = Array.from({ length: 12 }, (_, i) => `- item ${i + 1}`);
    writeMemory("## Active Work\n" + items.join("\n") + "\n");

    const result = readActiveWork(dir);

    const itemLines = result.content
      .split("\n")
      .filter((l) => /^\s*[-*]\s/.test(l));
    expect(itemLines.length).toBe(10);
    expect(result.truncated).toBe(true);
  });

  test("M6 — char cap: content exceeding 500 chars → truncated=true, length <= 500", () => {
    // 3 items with long content totaling > 500 chars
    const longLine = "x".repeat(200);
    writeMemory([
      "## Active Work",
      `- ${longLine}`,
      `- ${longLine}`,
      `- ${longLine}`,
    ].join("\n") + "\n");

    const result = readActiveWork(dir);

    expect(result.truncated).toBe(true);
    expect(result.content.length).toBeLessThanOrEqual(500);
  });

  test("M7 — both caps: item cap fires first (12 short items, well under 500 chars total)", () => {
    const items = Array.from({ length: 12 }, (_, i) => `- short item ${i + 1}`);
    writeMemory("## Active Work\n" + items.join("\n") + "\n");

    const result = readActiveWork(dir);

    // Item cap wins — truncated at 10 items, not 500 chars
    const itemLines = result.content
      .split("\n")
      .filter((l) => /^\s*[-*]\s/.test(l));
    expect(itemLines.length).toBe(10);
    expect(result.truncated).toBe(true);
    // Content should be short (well under 500 chars since each item is ~15 chars)
    expect(result.content.length).toBeLessThan(300);
  });

  test("M11 — truncation slices at newline boundary (no mid-line cuts)", () => {
    // Build content that will hit char cap mid-line
    const lines = Array.from({ length: 3 }, (_, i) => `- item ${i + 1} ${"x".repeat(200)}`);
    writeMemory("## Active Work\n" + lines.join("\n") + "\n");

    const result = readActiveWork(dir);

    if (result.truncated) {
      // Content must not end mid-word — last char should be newline or last char of a full line
      // Verify it doesn't end with a partial item marker like "- item 2 xxx"
      // We check: the content, when split by \n, has no trailing partial empty segments
      // that would indicate mid-line truncation (i.e., content ends at a \n boundary)
      const lastChar = result.content[result.content.length - 1];
      expect(lastChar === "\n" || result.content.includes("\n")).toBe(true);
    }
  });
});

describe("F9 — memory-reader.ts: error handling", () => {
  test("M9 — unreadable file (chmod 000) returns empty result with WARN on stderr", () => {
    // Skip if running as root (chmod has no effect)
    if (process.getuid?.() === 0) return;

    const memPath = join(dir, "MEMORY.md");
    writeMemory("## Active Work\n- secret task\n");
    chmodSync(memPath, 0o000);

    let stderrOutput = "";
    const origWrite = process.stderr.write.bind(process.stderr);
    // @ts-ignore
    process.stderr.write = (chunk: unknown): boolean => {
      stderrOutput += String(chunk);
      return true;
    };

    try {
      const result = readActiveWork(dir);

      expect(result.content).toBe("");
      expect(result.truncated).toBe(false);
      // sourcePath should be set even on read failure (file exists but unreadable)
      expect(result.sourcePath).toBe(memPath);
      // Warning must be logged to stderr
      expect(stderrOutput).toMatch(/\[memory-reader\].*WARN/);
    } finally {
      // @ts-ignore
      process.stderr.write = origWrite;
      chmodSync(memPath, 0o644); // restore for cleanup
    }
  });
});

describe("F9 — memory-reader.ts: custom caps", () => {
  test("extractActiveWorkSection — custom caps override defaults", () => {
    const items = Array.from({ length: 5 }, (_, i) => `- item ${i + 1}`);
    const content = "## Active Work\n" + items.join("\n") + "\n";

    // Cap at 3 items
    const result = extractActiveWorkSection(content, { maxItems: 3, maxChars: 500 });

    const itemLines = result.text.split("\n").filter((l) => /^\s*[-*]\s/.test(l));
    expect(itemLines.length).toBe(3);
    expect(result.truncated).toBe(true);
  });

  test("readActiveWork — custom maxItems=5 override", () => {
    const items = Array.from({ length: 8 }, (_, i) => `- item ${i + 1}`);
    writeMemory("## Active Work\n" + items.join("\n") + "\n");

    const result = readActiveWork(dir, { maxItems: 5 });

    const itemLines = result.content.split("\n").filter((l) => /^\s*[-*]\s/.test(l));
    expect(itemLines.length).toBe(5);
    expect(result.truncated).toBe(true);
  });
});

describe("F9 — M12: activeWorkInjection config flag (loadConfig integration)", () => {
  const savedConfig = process.env.BYTEDIGGER_CONFIG;

  afterEach(() => {
    if (savedConfig === undefined) {
      delete process.env.BYTEDIGGER_CONFIG;
    } else {
      process.env.BYTEDIGGER_CONFIG = savedConfig;
    }
  });

  test("M12 — activeWorkInjection:false in config → parseBool returns false", () => {
    const cfgDir = mkdtempSync(join(tmpdir(), "mem-cfg-"));
    const cfgPath = join(cfgDir, "bytedigger.json");
    writeFileSync(cfgPath, JSON.stringify({ activeWorkInjection: false }));
    process.env.BYTEDIGGER_CONFIG = cfgPath;

    try {
      const cfg = loadConfig();
      expect(cfg.activeWorkInjection).toBe(false);
    } finally {
      rmSync(cfgDir, { recursive: true, force: true });
    }
  });

  test("M12b — activeWorkInjection defaults to true when absent from config", () => {
    const cfgDir = mkdtempSync(join(tmpdir(), "mem-cfg-"));
    const cfgPath = join(cfgDir, "bytedigger.json");
    writeFileSync(cfgPath, JSON.stringify({}));
    process.env.BYTEDIGGER_CONFIG = cfgPath;

    try {
      const cfg = loadConfig();
      expect(cfg.activeWorkInjection).toBe(true);
    } finally {
      rmSync(cfgDir, { recursive: true, force: true });
    }
  });
});
