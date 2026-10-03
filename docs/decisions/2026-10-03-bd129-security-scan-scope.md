---
red_tests:
  - tests/security-scan-scope.bats
paths:
  - scripts/security-scan.sh
  - phases/phase-05-inject.md
  - phases/phase-45-spec.md
---

# bd#129: security scan classifies almost every repo as HIGH

**Status: DRAFT r1** · **Tier:** 2 · **Class:** deterministic shell check (no model call)
**Chokepoint:** `scripts/security-scan.sh` (the only writer of `security_classification` / `security_patterns_found`) and the two phase docs that call it.

## 0. Verified premises (live, base 43d3b49)

- `bash scripts/security-scan.sh --files "$(git ls-files | tr '\n' ',')"` on this repo (1239 files) prints `security_classification: HIGH` / `security_patterns_found: AUTH,CRYPTO,SECRETS`. Confirmed.
- `phases/phase-05-inject.md:32` falls back to `git ls-files` when the diff is empty, which is always true in Phase 0.5. Confirmed.
- Patterns at `security-scan.sh:47-49` are unanchored case-insensitive substrings (`session`, `hash`, `sign`, `token`, `.env`, `auth` ...). Confirmed.
- Kill switch: no such flag exists (the issue marks it unverified). `grep` finds no `BD_SECURITY_SCAN_*` anywhere. This lot adds one.
- Consumers: `phase_6_review.py:430` reads only `== "HIGH"`; `build-state.yaml` keys keep their names and value domain. `tests/security-scan.bats` (8 cells) is not run by `ci.yml`, so it is run by hand here.

## 1. Contract

`security-scan.sh` gains `--task TEXT` and a kill switch; patterns and provenance change. Always exit 0, unknown argument still exits 0.

1. **Sources.** The scan classifies the union of: the task text (`--task`), the contents of each existing file in `--files`, and the path string of each `--files` entry (also when the file does not exist yet: a planned `src/auth/login.ts` must count).
2. **Patterns (default mode).** Case-insensitive, every term bounded by a non-alphanumeric character or line edge, using `(^|[^[:alnum:]])(...)([^[:alnum:]]|$)` (portable to BSD and GNU grep; no `\b`, no `-P`).
   - AUTH: `auth`, `authenticat\w*`, `authoriz\w*`, `login`, `jwt`, `oauth2?`, `saml`, `rbac`, `passw(or)?d`, `credentials?`
   - CRYPTO: `encrypt\w*`, `decrypt\w*`, `crypto\w*`, `cipher`, `hmac`, `bcrypt`, `argon2`, `pbkdf2`, `(private|public)[_-]?key`, `certificate`
   - SECRETS: `api[_-]?key`, `secrets?`, `(access|auth|bearer)[_-]?token`, `keychain`, `vault`, and a `.env` file reference (`.env` preceded by whitespace, a quote, `/`, `(` or `=`, so `process.env` does not count)
   - Dropped as bare terms: `session`, `hash`, `sign`, `token`, `key.*gen`, `api.key` with a free separator. DATA and INFRA patterns are unchanged.
3. **Classification.** Unchanged: any of AUTH/CRYPTO/SECRETS -> HIGH; else DATA -> MEDIUM; else LOW. Fail-closed MEDIUM `unanalyzed` only when there is neither `--files` nor `--task`. Task text only (no files), no hit -> LOW with `security_patterns_found: task_only` (records that no file was scanned).
4. **Provenance.** One new line `security_triggers: [AUTH=src/a.ts:3, SECRETS=task]` (flow list, first trigger per category: `<file>:<line>` for content, `<file>` for a path hit, `task` for task text; `[]` when none). It is printed to stdout and written to `--state-file`, with the same remove-then-append replacement as the two existing keys (a second run never duplicates it).
5. **Kill switch.** Env `BD_SECURITY_SCAN_LEGACY` exactly `1` restores the old behaviour byte-for-byte for classification: old substring patterns, `--task` ignored, path strings not scanned. Any other value (`0`, `true`, empty, `yes`) is default mode. `security_triggers` is still written in legacy mode. Owner: bytedigger (MGR); remove once default mode has shipped one release without complaints.
6. **Phase docs.**
   - `phase-05-inject.md` 0.5.2: no diff -> do NOT fall back to `git ls-files`; run the scan with `--task "$TASK"` (and the diff files when there are any). The `git ls-files` fallback survives only under `BD_SECURITY_SCAN_LEGACY=1`.
   - `phase-45-spec.md`: after the spec exists, re-run the scan with `--task` plus `--files` from the spec's `Files` list (CREATE and MODIFY paths) and the same `--state-file`, so the classification is re-derived from what the task will touch before Phase 5/6 read it.

## 2. Acceptance criteria (RED `tests/security-scan-scope.bats`, hermetic temp dirs)

- AC1 (reference case): a file with `session token hash sign` plus a benign `fetch` line, scanned via `--files`, is not HIGH and patterns lack AUTH, CRYPTO, SECRETS.
- AC2 (real hits stay HIGH, with provenance): `jwt.verify(...)` on line 3 -> HIGH, AUTH, `security_triggers` contains `AUTH=<file>:3`; `encrypt(data, key)` -> CRYPTO with `:<line>`; `API_KEY=abc` -> SECRETS with `:<line>`.
- AC3 (state file): `security_triggers:` is written; two runs leave exactly one `security_triggers:`, one `security_classification:` and one `security_patterns_found:`; the second run's values win.
- AC4 (task text): `--task "add JWT login to the api"` with no files -> HIGH, AUTH, trigger `AUTH=task`; `--task "consolidate a config-file reader"` with no files -> LOW, `task_only`, exit 0; no files and no task -> MEDIUM `unanalyzed` (unchanged).
- AC5 (union): benign task plus file containing `jwt.verify` -> HIGH; auth task plus benign file -> HIGH.
- AC6 (path): `--files src/auth/login.ts` (file absent) -> HIGH, AUTH, trigger `AUTH=src/auth/login.ts`; `--files src/config/reader.ts` (absent) -> not HIGH.
- AC7 (word boundaries): content `the author signed the design; hashtable; tokenizer; sessionStorage; process.env.NODE_ENV` -> not HIGH. `# password` in a file -> AUTH.
- AC8 (kill switch): `BD_SECURITY_SCAN_LEGACY=1` on the AC1 file -> HIGH with `AUTH,CRYPTO,SECRETS`; with `--task` "add jwt" and no files -> MEDIUM `unanalyzed`; values `0`, `true`, empty, `yes` -> AC1 result (not HIGH).
- AC9 (docs): `phase-05-inject.md` passes `--task` to the scan and mentions `BD_SECURITY_SCAN_LEGACY`; the only `git ls-files` in it sits in the legacy branch (the line holding `git ls-files` is inside a block that names the kill switch); `phase-45-spec.md` contains a `security-scan.sh` re-run with both `--task` and `--files`.
- AC10 (guard): the 8 cells of `tests/security-scan.bats` stay green unchanged.

## 3. Out of scope

Phase 4/6 reviewer wiring, engine code, `commands/build.md`, the DATA/INFRA patterns, CI wiring of bats, auto-deriving the `Files` list from the spec in a script, engine flags catalog (this is a shell env switch, not an engine flag).

## 4. Known limits (accepted, go into the PR body)

Keyword matching stays heuristic: a task phrased without any auth vocabulary that edits an auth file under a neutral path is caught only by the file content at the Phase 4.5 re-scan. A repo whose spec `Files` list names real auth code is HIGH, as intended.
