/**
 * state-reader.ts — bash-parity YAML field extractor.
 *
 * Originally ported from HAL (SYSTEM/cli/build/lib/state-reader.ts) — no
 * longer tracked upstream; this file is the canonical ByteDigger copy.
 * Replicates the grep + sed pipeline used by scripts/build-gate.sh
 * (anchored line-regex extract, strip key prefix, strip surrounding quotes
 * and whitespace).
 *
 * Missing file returns null. Missing field returns null. Empty value -> "".
 *
 * F4 additions:
 *   - StateReadError — thrown when a file exists but cannot be read/parsed.
 *     Includes filePath property for operator diagnostics.
 *   - readStateFieldOrThrow — same as readStateField but distinguishes
 *     missing-file (returns null) from unreadable-file (throws StateReadError).
 */

import { existsSync, readFileSync } from "node:fs";

// ---------------------------------------------------------------------------
// StateReadError — F4: missing vs unreadable distinction at library layer.
// ---------------------------------------------------------------------------

/**
 * Thrown by readStateFieldOrThrow when a file exists but cannot be read
 * (e.g. permission denied) or cannot be parsed.
 * Carries filePath so callers can surface actionable diagnostics.
 */
export class StateReadError extends Error {
  readonly filePath: string;

  constructor(filePath: string, reason: string, originalError?: unknown) {
    super(`StateReadError: cannot read ${filePath}: ${reason}`, originalError ? { cause: originalError } : undefined);
    this.name = "StateReadError";
    this.filePath = filePath;
  }
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Internal helper: returns matching lines, or null if file is missing.
 * Silently returns null on read errors (used by readStateField).
 */
function matchLinesOrNull(yamlPath: string, field: string): string[] | null {
  if (!existsSync(yamlPath)) return null;
  let content: string;
  try {
    content = readFileSync(yamlPath, "utf8");
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(`[state-reader] WARN: ${yamlPath} exists but read failed: ${msg}\n`);
    return null;
  }
  content = content.replace(/\r/g, "");
  return content.split("\n").filter((line) => line.startsWith(field + ":"));
}

/**
 * Internal helper: returns matching lines, or null if file is missing.
 * Throws StateReadError if the file exists but cannot be read.
 */
function matchLinesOrThrow(yamlPath: string, field: string): string[] | null {
  if (!existsSync(yamlPath)) return null;
  let content: string;
  try {
    content = readFileSync(yamlPath, "utf8");
  } catch (err) {
    if (err instanceof Error && 'code' in err && (err as NodeJS.ErrnoException).code === 'ENOENT') {
      return null;  // File deleted between existsSync and readFileSync
    }
    const reason = err instanceof Error ? err.message : String(err);
    throw new StateReadError(yamlPath, reason, err);
  }
  content = content.replace(/\r/g, "");
  return content.split("\n").filter((line) => line.startsWith(field + ":"));
}

function stripKeyAndQuotes(rawLine: string, field: string): string {
  const prefixRe = new RegExp(`^${escapeRegExp(field)}:[\t \v\f]*`);
  let val = rawLine.replace(prefixRe, "");
  if (val.startsWith("'") || val.startsWith('"')) val = val.slice(1);
  if (val.endsWith("'") || val.endsWith('"')) val = val.slice(0, -1);
  val = val.replace(/^[\t \v\f]+/, "");
  val = val.replace(/[\t \v\f]+$/, "");
  return val;
}

/**
 * Read a single field from a build-state.yaml-style file.
 * Returns null for both missing-file and unreadable-file (original behavior).
 */
export function readStateField(
  yamlPath: string,
  field: string,
  mode: "first" | "last" = "first",
): string | null {
  const matches = matchLinesOrNull(yamlPath, field);
  if (matches === null) return null;
  if (matches.length === 0) return null;
  const rawLine = mode === "last" ? matches[matches.length - 1]! : matches[0]!;
  return stripKeyAndQuotes(rawLine, field);
}

/**
 * Read a single field from a build-state.yaml-style file.
 * Returns null if the file does not exist (not a build session).
 * Throws StateReadError if the file exists but cannot be read or parsed —
 * callers that need to distinguish "missing" from "broken" should use this.
 */
export function readStateFieldOrThrow(
  yamlPath: string,
  field: string,
  mode: "first" | "last" = "first",
): string | null {
  const matches = matchLinesOrThrow(yamlPath, field);
  if (matches === null) return null;
  if (matches.length === 0) return null;
  const rawLine = mode === "last" ? matches[matches.length - 1]! : matches[0]!;
  return stripKeyAndQuotes(rawLine, field);
}
