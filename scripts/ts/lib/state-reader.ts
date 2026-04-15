/**
 * state-reader.ts — bash-parity YAML field extractor.
 *
 * Ported from HAL SYSTEM/cli/build/lib/state-reader.ts. Replicates the
 * grep + sed pipeline used by scripts/build-gate.sh (anchored line-regex
 * extract, strip key prefix, strip surrounding quotes and whitespace).
 *
 * Missing file returns null. Missing field returns null. Empty value -> "".
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";

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

/**
 * Read multiple fields in a single pass. Useful when batching many field
 * lookups to avoid re-reading the file.
 */
export function readStateFields(
  yamlPath: string,
  spec: string[] | { first?: string[]; last?: string[] },
): Record<string, string | null> {
  const firstFields: string[] = Array.isArray(spec) ? spec : (spec.first ?? []);
  const lastFields: string[] = Array.isArray(spec) ? [] : (spec.last ?? []);
  const allFields = [...firstFields, ...lastFields];

  const result: Record<string, string | null> = {};
  if (!existsSync(yamlPath)) {
    for (const f of allFields) result[f] = null;
    return result;
  }
  let content: string;
  try {
    content = readFileSync(yamlPath, "utf8");
  } catch {
    for (const f of allFields) result[f] = null;
    return result;
  }
  content = content.replace(/\r/g, "");
  const lines = content.split("\n");

  const matchMap: Record<string, string[]> = {};
  for (const f of allFields) matchMap[f] = [];
  for (const line of lines) {
    for (const f of allFields) {
      if (line.startsWith(f + ":")) matchMap[f]!.push(line);
    }
  }
  const lastSet = new Set(lastFields);
  for (const f of allFields) {
    const matches = matchMap[f]!;
    if (matches.length === 0) {
      result[f] = null;
    } else if (lastSet.has(f)) {
      result[f] = stripKeyAndQuotes(matches[matches.length - 1]!, f);
    } else {
      result[f] = stripKeyAndQuotes(matches[0]!, f);
    }
  }
  return result;
}

export function readStateFileMtime(yamlPath: string): number | null {
  if (!existsSync(yamlPath)) return null;
  try {
    return Math.floor(statSync(yamlPath).mtimeMs / 1000);
  } catch {
    return null;
  }
}

export function stateFileSha256(yamlPath: string): string | null {
  if (!existsSync(yamlPath)) return null;
  try {
    return createHash("sha256").update(readFileSync(yamlPath)).digest("hex");
  } catch {
    return null;
  }
}
