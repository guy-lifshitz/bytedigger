/**
 * config-reader.ts — ByteDigger config path resolver.
 *
 * Resolves bytedigger.json path in priority order:
 *   1. BYTEDIGGER_CONFIG env var (absolute path to file)
 *   2. CLAUDE_PLUGIN_ROOT/bytedigger.json
 *   3. ../../../bytedigger.json relative to this file
 *      (walks lib → ts → scripts → repo root)
 *
 * Parsing lives in build-phase-gate.ts::loadConfig so that failures can
 * log to stderr with the right context instead of being silently swallowed
 * by a "never throws" helper.
 */

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
