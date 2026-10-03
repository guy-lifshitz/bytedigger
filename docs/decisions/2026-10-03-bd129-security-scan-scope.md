---
red_tests:
  - tests/security-scan-scope.bats
paths:
  - scripts/security-scan.sh
  - phases/phase-05-inject.md
  - phases/phase-45-spec.md
---

# bd#129: security scan classifies almost every repo as HIGH

**Status: DRAFT r3 (gate r2 REJECTED the r2 delta, 3 MAJOR: `-gate-r2.md`; §5.9 folds them; MGR authorized exactly one gate round r3, REJECTED = stop). Was DRAFT r2 (delta §5 after MGR pre-merge review of PR #245; gate r1 APPROVED the r1 contract, r2 gates the delta only)** · **Tier:** 2 · **Class:** deterministic shell check (no model call)
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

## 5. Delta r2: fail-open fixes (MGR review of PR #245)

Applies to default mode only; legacy mode (`BD_SECURITY_SCAN_LEGACY=1`) is unchanged. Everything in sections 1-4 stays, except where this section widens a pattern.

1. **Identifier splitting, raw plus split (gate r2 M1).** In default mode a line (file content, path string, task text) matches when the **raw** text matches a term **or** the **split** text does. The split form puts a space at a lowercase-to-uppercase boundary (`([[:lower:]])([[:upper:]])`: `authService` -> `auth Service`, `jwtToken` -> `jwt Token`, `SessionMiddleware` -> `Session Middleware`) and before the last capital of an uppercase run of two or more followed by a lowercase letter (`([[:upper:]]{2,})([[:upper:]][[:lower:]])`: `JWTToken` -> `JWT Token`). There is no digit-based split and no single-letter-run split, so `2FA` and `OAuth2` are matched by the raw pass. `_`, `-`, `.`, `/` were already boundaries. All-caps names (`NODE_ENV`) are not split. Trigger labels always name the original path or `task`, never split text; the line number is the original line.
2. **Optional plurals.** `passw(or)?ds?`, `api[_ -]?keys?`, `secrets?`, `credentials?`, `(private|public)[_ -]?keys?` (the separator may now be a space), `certificates?`.
3. **New terms.**
   - AUTH: `authz`, `authn`, `ldap`, `csrf`, `xsrf`, `2fa`, `mfa`, `totp`, `user[_ -]?tokens?`, `(user|login|web|http)[_ -]?sessions?`, `session[_ -]?(id|ids|cookie|secret|store|middleware|manager|fixation|hijack[[:alnum:]_]*)`.
   - SECRETS: `(refresh|access|auth|bearer|csrf|id|api)[_ -]?tokens?`, `token[_ -]?(store|vault|secret)`, `id[_-]?(rsa|ed25519|ecdsa)`, `ssh[_ -]?keys?`, `BEGIN [A-Z ]*PRIVATE KEY` (covered by the private-key term).
   - CRYPTO: `sign(s|ed|ing)?[_ -]?(webhook|payload|request|jwt|token|cookie|message)s?`.
4. **Deliberately still LOW (the #129 reference case):** bare `session`, `sessions`, `token`, `tokens`, `hash`, `sign`, `sessionStorage`, `hashtable`, `tokenizer`, `author`, `authority`. The review listed bare `tokens` as a miss; it is not changed because `session token hash sign` must stay LOW. Qualified forms above are HIGH.
5. **`$TASK` definition.** `phase-05-inject.md` and `phase-45-spec.md` each contain, before the scan call, at column 0 inside a fenced block (dedent the phase-45 block if needed), exactly one line `TASK=$(sed -n 's/^task: *//p' build-state.yaml | head -1 | sed 's/^"//; s/"$//')`. Run in a dir whose `build-state.yaml` has `task: "Add user authentication"` it must set `TASK` to exactly `Add user authentication`.
6. **State file newline.** After the existing `sed` delete of the three keys and before the appends, if the state file is non-empty and its last byte is not `\n`, the scan appends one `\n`. The existing last line is never altered.
7. **Acceptance (RED cells appended to `tests/security-scan-scope.bats`, AC11-AC15):**
   - AC11 content plurals/phrases, each its own file, expected `security_classification: HIGH`: `passwords table`, `hashed_passwords`, `user sessions`, `API keys`, `bearer tokens`, `private key`, `BEGIN RSA PRIVATE KEY`, `id_rsa`, `ssh key`, `authz`, `ldap bind`.
   - AC12 identifiers in content -> HIGH: `refresh_token`, `jwtToken`, `sessionId`, `SessionMiddleware`, `authMiddleware`.
   - AC13 planned paths (files absent) -> HIGH: `authService.ts`, `AuthService.ts`, `token_store.py`, `session_manager.rb`.
   - AC14 task text, no files -> HIGH: `hash passwords with salt`, `rotate API keys`, `store user token in cookie`, `sign webhook payloads`, `enable 2FA`, `require MFA`, `fix CSRF`, `rename getApiKey helper` (split task text), `upgrade OAuth2 flow` (raw pass).
   - AC15 guards, each LOW or MEDIUM, never HIGH: the #129 reference file `session token hash sign` (no other words), bare `tokens` and `sessions`, `sessionStorage`, `author authority`, `process.env.NODE_ENV`, `tokenizer hashtable`; path `src/config/reader.ts`; task `consolidate a config-file reader`.
   - AC16 `$TASK`: each phase file contains exactly one column-0 line equal to the §5.5 line (exact string compare, so a hardcoded `TASK="..."` fails); eval that line in a temp dir against two different `build-state.yaml` `task:` values (`Add user authentication`, `Fix the login page`) and assert each result exactly.
   - AC17 state file without trailing newline (`printf 'k: v' > state`): after a run the file contains the exact line `k: v`, and each of the three keys on its own line, once.
   - AC18 legacy parity: with `BD_SECURITY_SCAN_LEGACY=1` file content `getApiKey()` (LOW under the old patterns, would be HIGH only if splitting leaked into legacy) stays LOW, and the old reference result `AUTH,CRYPTO,SECRETS` on `session token hash sign` is unchanged.
   - AC19 raw pass: file content `2FA`, `OAuth2` and `JWTToken` each -> HIGH.
   - AC20 (gate r2 M3 shield): a file whose line 1 is the invalid UTF-8 byte `\xff` and line 2 is `password` -> HIGH, trigger `AUTH=<file>:2`, exit 0, with the shell locale forced to a UTF-8 one (`LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8`). Default mode pins `LC_ALL=C` for both the split step and the grep.
   - AC21 trigger label: a planned path `src/authService.ts` -> trigger `AUTH=src/authService.ts` exactly; file content `getApiKey()` on line 3 of `c.ts` -> `SECRETS=<path>:3` with the original path.
8. **Out of scope:** running `.bats` in CI (separate issue; noted in the PR report), DATA/INFRA patterns, a general tokenizer for other naming styles (kebab-case already splits on `-`).
9. **Gate r2 fold-in (r3).** M1 -> §5.1 (raw plus split, narrowed split), AC14 cells `enable 2FA`/`require MFA` replace `add 2FA / MFA`, AC19. M2 -> AC16 exact line plus two task values. M3 -> `LC_ALL=C` for split and grep, AC20. Minors folded: portable `hijack` term, trigger-label cell AC21, AC18 uses `getApiKey()`, phase-45 block at column 0, newline check ordering. Accepted limits (PR body): extra HIGHs from TS/Java member names like `private key:`, `user tokens` meaning LLM tokens, `idToken` in lexers, and random-word hits inside base64, minified or lockfile text; bare `tokens` stays LOW (MGR accepted).
