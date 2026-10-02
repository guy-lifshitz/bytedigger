/**
 * GH1200 fixture (AC24) — a scope file whose extension has NO data-cell
 * extractor per spec §1.3.1. Selecting `--channels data-cell` on it must be
 * loud (`W_NO_EXTRACTOR`), never a silent clean (#1186 class).
 */

export function OpaqueWidget() {
  return null;
}
