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
 */

import { existsSync, readFileSync } from "node:fs";

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function matchLines(yamlPath: string, field: string): string[] | null {
  if (!existsSync(yamlPath)) return null;
  let content: string;
  try {
    content = readFileSync(yamlPath, "utf8");
  } catch {
    return null;
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
 */
export function readStateField(
  yamlPath: string,
  field: string,
  mode: "first" | "last" = "first",
): string | null {
  const matches = matchLines(yamlPath, field);
  if (matches === null) return null;
  if (matches.length === 0) return null;
  const rawLine = mode === "last" ? matches[matches.length - 1]! : matches[0]!;
  return stripKeyAndQuotes(rawLine, field);
}
