/**
 * config-reader.ts — ByteDigger config resolver.
 *
 * Resolves bytedigger.json path in priority order:
 *   1. BYTEDIGGER_CONFIG env var (absolute path to file)
 *   2. CLAUDE_PLUGIN_ROOT/bytedigger.json
 *   3. ../../bytedigger.json relative to this file (worktree root)
 *
 * readConfigField never throws — missing file, malformed JSON, and missing
 * keys all return undefined. Fail-closed safe for dispatcher use.
 */

import { existsSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";

export function resolveConfigPath(): string {
  if (process.env.BYTEDIGGER_CONFIG) return process.env.BYTEDIGGER_CONFIG;
  if (process.env.CLAUDE_PLUGIN_ROOT) {
    return join(process.env.CLAUDE_PLUGIN_ROOT, "bytedigger.json");
  }
  // Resolve relative to this source file: scripts/ts/lib/config-reader.ts
  const here = dirname(new URL(import.meta.url).pathname);
  return join(here, "..", "..", "..", "bytedigger.json");
}

export function readConfigField<T = unknown>(key: string): T | undefined {
  try {
    const path = resolveConfigPath();
    if (!existsSync(path)) return undefined;
    const raw = readFileSync(path, "utf8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const v = parsed[key];
    return v === undefined ? undefined : (v as T);
  } catch {
    return undefined;
  }
}
