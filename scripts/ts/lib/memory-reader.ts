/**
 * memory-reader.ts — ByteDigger MEMORY.md active work section extractor.
 *
 * Reads MEMORY.md from the project CWD and extracts the ## Active Work section.
 * Caps output at 10 items and 500 characters. Returns empty result on any error.
 *
 * Contract: readActiveWork() NEVER throws. All errors are logged to stderr
 * as [memory-reader] WARN: lines and swallowed.
 *
 * This module has no ByteDigger dependencies — it is a standalone utility.
 * Config wiring (activeWorkInjection flag) is parsed in build-phase-gate.ts::loadConfig() but not yet consumed -- integration into agent prompt injection is pending.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ActiveWorkResult {
  /** Extracted + capped text; "" if missing/disabled/section not found. */
  readonly content: string;
  /** True if item or char cap was applied. */
  readonly truncated: boolean;
  /** Absolute path read; null if MEMORY.md was not found. */
  readonly sourcePath: string | null;
}

export interface ActiveWorkCaps {
  /** Maximum number of list items (lines starting with - or *). Default: 10. */
  readonly maxItems: number;
  /** Maximum total character count of the returned string. Default: 500. */
  readonly maxChars: number;
}

export const DEFAULT_CAPS: ActiveWorkCaps = {
  maxItems: 10,
  maxChars: 500,
};

// ---------------------------------------------------------------------------
// Section extraction
// ---------------------------------------------------------------------------

/**
 * Extract ## Active Work section from raw MEMORY.md content.
 * Exported for unit testing without filesystem interaction.
 */
export function extractActiveWorkSection(
  content: string,
  caps: ActiveWorkCaps,
): { text: string; truncated: boolean } {
  // Strip \r for CRLF compat before processing
  const normalized = content.replace(/\r/g, "");
  const lines = normalized.split("\n");

  // Find first line matching ## Active Work (case-insensitive)
  const startIdx = lines.findIndex((l) => /^## Active Work/i.test(l));
  if (startIdx === -1) {
    return { text: "", truncated: false };
  }

  // Collect subsequent lines until next ## heading or EOF
  const sectionLines: string[] = [];
  for (let i = startIdx + 1; i < lines.length; i++) {
    const line = lines[i]!;
    if (/^## /.test(line)) break;
    sectionLines.push(line);
  }

  // Trim leading/trailing blank lines
  while (sectionLines.length > 0 && sectionLines[0]!.trim() === "") {
    sectionLines.shift();
  }
  while (sectionLines.length > 0 && sectionLines[sectionLines.length - 1]!.trim() === "") {
    sectionLines.pop();
  }

  if (sectionLines.length === 0) {
    return { text: "", truncated: false };
  }

  // Apply item cap first
  let truncated = false;
  const capped: string[] = [];
  let itemCount = 0;

  for (const line of sectionLines) {
    if (/^\s*[-*]\s/.test(line)) {
      if (itemCount >= caps.maxItems) {
        truncated = true;
        break;
      }
      itemCount++;
    }
    capped.push(line);
  }

  // Join with newlines
  let text = capped.join("\n");

  // Apply char cap — slice at last newline before maxChars
  if (text.length > caps.maxChars) {
    truncated = true;
    const sliced = text.slice(0, caps.maxChars);
    const lastNewline = sliced.lastIndexOf("\n");
    if (lastNewline > 0) {
      text = sliced.slice(0, lastNewline);
    } else {
      text = sliced;
    }
    // Trim trailing blank lines after char slice
    while (text.endsWith("\n\n")) {
      text = text.slice(0, -1);
    }
  }

  return { text, truncated };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Read MEMORY.md from cwd and extract the ## Active Work section.
 * Fail-open: never throws, returns empty result on any error.
 */
export function readActiveWork(
  cwd: string,
  caps?: Partial<ActiveWorkCaps>,
): ActiveWorkResult {
  const effectiveCaps: ActiveWorkCaps = {
    maxItems: caps?.maxItems ?? DEFAULT_CAPS.maxItems,
    maxChars: caps?.maxChars ?? DEFAULT_CAPS.maxChars,
  };

  const memoryPath = join(cwd, "MEMORY.md");

  if (!existsSync(memoryPath)) {
    return { content: "", truncated: false, sourcePath: null };
  }

  try {
    const raw = readFileSync(memoryPath, "utf8");
    const { text, truncated } = extractActiveWorkSection(raw, effectiveCaps);
    return { content: text, truncated, sourcePath: memoryPath };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(`[memory-reader] WARN: cannot read ${memoryPath}: ${msg}\n`);
    return { content: "", truncated: false, sourcePath: memoryPath };
  }
}
