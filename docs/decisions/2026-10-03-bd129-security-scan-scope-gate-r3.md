# bd#129 validation gate, round 3 (single authorized round): security-scan scope, spec §5 r3 + RED cells 23-72

<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd129-security-scan-scope.md sha256:06cb78228d14dc58db628fbdee22f83c0f00b4fbb1e96c28d33064e258b47a95
-->

- Scope: spec §5 (DRAFT r3) and cells 23-72 of `tests/security-scan-scope.bats` (AC11-AC21, 50 cells). §1-§4 were approved in r1 and are not re-audited.
- UUT as audited: `scripts/security-scan.sh` (HEAD 0059e54, r1 GREEN), `phases/phase-05-inject.md`, `phases/phase-45-spec.md`.
- Spec sha256: I could not verify it because this gate has no shell. The anchor holds the orchestrator's value.
- Cell count: AC11 11, AC12 5, AC13 4, AC14 9, AC15 9, AC16 2, AC17 2, AC18 2, AC19 3, AC20 1, AC21 2, which makes 50, plus 22 r1 cells = 72. That matches the orchestrator's run. I simulated which cells pass on base: AC14 `upgrade OAuth2 flow`, AC15 x9, AC17 stale-key, AC18 x2, AC19 `OAuth2` and AC20, so 15 shields and 35 not ok. That agrees with the orchestrator's list.

## r2 MAJOR closure

| r2 MAJOR | Spec closure | RED cell a stub cannot satisfy | Status |
|---|---|---|---|
| M1: the split kills `2fa` and `oauth2?`, and MFA masked it | §5.1: raw plus split union. The split is narrowed: no digit rule, and the upper-run rule `([[:upper:]]{2,})([[:upper:]][[:lower:]])` needs 3 or more capitals before a lowercase letter. | `AC14_task_enable_2FA` fails under any digit split without a raw pass. `AC14_task_require_MFA` is now separate. `AC19` `2FA` and `OAuth2` fail under a single-letter-run split without raw. `AC19` `JWTToken` fails without the upper-run split. | closed |
| M2: AC16 stub | §5.5 gives the exact line. AC16 requires an exact compare plus two `task:` values. | `grep -cxF "$TASK_LINE"` must equal 1, `^TASK=` must equal 1, and the line is evaluated against `Add user authentication` and `Fix the login page`. A hardcoded `TASK="..."` fails the exact compare and the second value. I checked that the bats `$'...'` literal decodes byte-for-byte to the §5.5 line. | closed |
| M3: BSD sed fails open on non-UTF-8 | §5.7 AC20: "Default mode pins `LC_ALL=C` for both the split step and the grep", and the raw+split union floors the result at r1 behaviour. | AC20 (`\xff` on line 1, `password` on line 2, UTF-8 locale forced) checks HIGH and `AUTH=src/bad.txt:2`. It binds the raw floor, and it binds a single-stream implementation where raw and split share one sed. It does not bind `LC_ALL=C` on a separate split sed (m1). | closed by r2's own criterion ("pin `LC_ALL=C` ... or the raw+split union", plus the E2 `password` shield). The split-only residual is advisory. |

## Step 1: spec-internal consistency (literal-token drift)

- The literals are consistent between §5 and the RED file: `security_classification`, `security_patterns_found`, `security_triggers`, `BD_SECURITY_SCAN_LEGACY`, the §5.5 `TASK=` line, `build-state.yaml`, `k: v`, `LC_ALL=C`, `en_US.UTF-8`.
- The §5.1 prose "uppercase run of two or more followed by a lowercase letter" reads as the run before the last capital, which matches the regex and the "no single-letter-run split" sentence. The regex is the binding literal, and either reading still yields HIGH for `OAuth2` through the raw pass. No defect.
- Label drift (m4): the §5.7 heading says "AC11-AC15", but the list runs to AC21. The bats section comment says "AC11-AC18" and the file header says "AC1-AC9".
- §5.3 `hijack[[:alnum:]_]*` is now POSIX (r2 m1 closed).

## Step 1.5: rule-overlap simulation (raw OR split, bounded, case-insensitive, LC_ALL=C)

The split rules are `s/([[:lower:]])([[:upper:]])/\1 \2/g` and then `s/([[:upper:]]{2,})([[:upper:]][[:lower:]])/\1 \2/g`.

| AC | Input | Split | Hit (pass) | Result |
|---|---|---|---|---|
| 11 | the 11 phrase cells | unchanged (no lower->Upper; `BEGIN RSA PRIVATE KEY` has no lowercase) | as in r2 (raw) | HIGH x11 |
| 12 | `refresh_token` | same | `refresh[_ -]?tokens?` (raw) | HIGH |
| 12 | `jwtToken` | `jwt Token` | `jwt` (split; raw fails, `T` is alnum) | HIGH |
| 12 | `sessionId` | `session Id` | `session[_ -]?(id..)` (split) | HIGH |
| 12 | `SessionMiddleware` | `Session Middleware` | session middleware (split) | HIGH |
| 12 | `authMiddleware` | `auth Middleware` | `auth` (split) | HIGH |
| 13 | `src/authService.ts`, `src/AuthService.ts` | `src/auth Service.ts`, `src/Auth Service.ts` | `auth` (split) | HIGH |
| 13 | `src/token_store.py`, `src/session_manager.rb` | same | raw | HIGH |
| 14 | `hash passwords with salt` / `rotate API keys` / `store user token in cookie` / `sign webhook payloads` | same | `passwords` / `api keys` / `user token` / `sign webhook` (raw) | HIGH |
| 14 | `enable 2FA` | same (no digit rule) | `2fa` (raw) | HIGH |
| 14 | `require MFA` / `fix CSRF` | same | `mfa` / `csrf` (raw) | HIGH |
| 14 | `rename getApiKey helper` | `rename get Api Key helper` | `api[_ -]?keys?` with a space separator (split) | HIGH |
| 14 | `upgrade OAuth2 flow` | same (`OA` is 2 capitals, below the threshold of 3; no lower->Upper) | `oauth2?` (raw) | HIGH |
| 19 | `2FA` / `OAuth2` | same | raw | HIGH |
| 19 | `JWTToken` | `JWT Token` | `jwt` (split) | HIGH |
| 21 | path `src/authService.ts` | `src/auth Service.ts` | AUTH only. The label is the original `$f`, so the result is exactly `[AUTH=src/authService.ts]`. | pass |
| 21 | `getApiKey()` on line 3 of `src/c.ts` | `get Api Key()` | SECRETS only, at the original line 3. The path `src/c.ts` has no hit. The result is exactly `[SECRETS=src/c.ts:3]`. | pass |
| 15 | `session token hash sign` | same | none: no session suffix, no `session` prefix in the token set, `sign` has no object, no DATA | LOW |
| 15 | `tokens` / `sessions` | same | none | LOW |
| 15 | `sessionStorage` | `session Storage` | none (`stor` is followed by `a`, so `store` fails) | LOW |
| 15 | `author authority` | same | none (`auth` boundary fails; `authorit` is not `authoriz`) | LOW |
| 15 | `process.env.NODE_ENV` | same (all caps, `NODE` is followed by `_`, not a lowercase letter) | none (`.env` is preceded by `s`) | LOW |
| 15 | `tokenizer hashtable` | same | none | LOW |
| 15 | path `src/config/reader.ts`, task `consolidate a config-file reader` | same | none, and no DATA substring in the task | LOW |

- The r1 cells still hold. AC1 stays MEDIUM DATA. AC7 (`the author signed the design; ...; sessionStorage; process.env.NODE_ENV`) stays LOW `none`: `design` fails the boundary, `signed the` has no object, and `session Storage` misses as above.
- AC18 `getApiKey()` under legacy: `api.key` needs one character between `api` and `key`, and `apiKey` has none, so the result is LOW. A leaked split would produce `Api Key`, which matches `api.key`, so the cell discriminates.
- New false-positive hazards from the union, checked hard:
  - `useSessionStorage`, `sessionStorageKey`, `getTokenizer`, `idTokenizer`, `IDTokenizer`, `getAuthor`, `hasAuthority`, `designToken`, `loadEnv` and `signInRequest` are all safe. The token suffix fails the boundary, the qualifier is not in a term list, or `sign` is not directly followed by an object.
  - `NODE_ENV`-style constants are never split.
  - New HIGHs: `userTokenCount`, `idToken`, `CertificateAuthority`, `PublicKeyCredential`, `signRequest`, `sessionStore`, `isAuthorized`. These are intended or already accepted in §5.9.
  - The union is a superset of r1's raw hits, so it cannot lose an r1 HIGH.
  - Random mixed-case text (base64, lockfiles, mktemp suffixes) can produce false HIGHs. §5.9 accepts this, and the per-position probability of a bounded `Jwt`/`Mfa`/`Auth` segment is around 1e-5.
  - No AC15 guard is broken.

## Step 2: §5 contract vs ACs

| §5 item | AC | Gap |
|---|---|---|
| 5.1 split of content / path / task | AC12 / AC13 / AC14 `getApiKey` | none |
| 5.1 raw half of the union | AC14 and AC19 `2FA`/`OAuth2` pass under narrowed split-only too | raw half not bound (m2) |
| 5.1 labels and original line | AC21 x2 | none |
| 5.2 plurals | AC11, AC14 | none |
| 5.3 new terms | partial | r2 m5 carried (m6) |
| 5.4 still-LOW list | AC15 | none |
| 5.5 `$TASK` | AC16 | "before the scan call" not asserted (m5) |
| 5.6 newline | AC17 cell 2. On base, BSD and GNU `sed -i` both keep the missing final newline, so `k: vsecurity_classification...` joins and the cell is not ok. Cell 1 is a shield on both seds: deleting the unterminated key line leaves `other: x\n`. | none |
| 5.7 AC20 `LC_ALL=C` | AC20 | split-sed locale not bound (m1) |
| legacy untouched | AC18 | none |

No contract item lacks an AC on a terminal or error path, and no AC lacks a producing contract item.

## Step 3: RED adequacy

- Every new cell runs the real script, or evals the real doc line, in a hermetic `mktemp -d`. There is no self-mocking.
- Every failure is assert-time. Helpers are defined before use, the inputs are single-quoted literals, and `TASK_LINE` is a valid `$'...'` literal. No cell fails at collection.
- Stub-passability, cell by cell:
  - AC16 is now bound (exact compare, a second value, a single `^TASK=`).
  - AC21 asserts the whole trigger line, so a split-text label fails.
  - AC18 cell 1 now discriminates.
  - AC17 cell 2 forces the join defect.
  - The residual gaps are m1 and m2. Both are spec clauses that a GREEN could skip and still pass. Neither re-opens an r1 fail-open, because the raw floor is at least r1 behaviour.

## Step 4: reachability (§1y) and feasibility

| Side effect | Point | Host | Test path |
|---|---|---|---|
| raw+split for content | `security-scan.sh:79`, a second pass `LC_ALL=C sed -E '<2 rules>' -- "$file" \| LC_ALL=C grep -m1 -n -a -iE -e "$pat"` inside the existing `\|\| true` | `scan_file` | AC12, AC19, AC20, AC21 c.ts |
| raw+split for path and task | `:91`, the same pipe on `printf '%s\n' "$text"` | `scan_text` (:97 task, :110 path) | AC13, AC14, AC21 path |
| `LC_ALL=C` | the split sed and the greps, default branch only | `scan_file`, `scan_text` | AC20 |
| newline | between the sed at :187 and the printf at :188, `[ -s f ] && [ -n "$(tail -c1 f)" ] && printf '\n' >> f` | state-file block | AC17 |
| `$TASK` line | phase-05 fence at :43-49 (column 0); phase-45 :77-83, dedented to column 0 | doc text | AC16 |

Feasibility on bash 3.2, BSD `sed -E`, `set -euo pipefail`:
- BSD `sed -E` supports backrefs and `{2,}`.
- A pure `s///g` script (no `N`/`D`) is 1:1 per line, so `grep -n` on the sed output gives original line numbers.
- When `grep -m1` exits early, sed may die with 141. Under pipefail that status is absorbed by the `|| true` inside `$(...)`, and grep's output is kept.
- Under `LC_ALL=C`, the classes are ASCII and there is no illegal-byte-sequence abort. Binary content goes through sed as bytes. Whether BSD sed preserves embedded NUL lines is unverified, but the raw `grep -a` pass floors it.
- No bash-4 features are needed.

## Adversarial edges (not covered by the §5.7 AC table)

- E1 A split-only hit after an invalid byte: line 1 `\xff`, line 2 `authService` (or `jwtToken`), with a UTF-8 locale forced. If the split sed lacks `LC_ALL=C`, BSD sed aborts at line 1 and the `|| true` hides it, giving LOW. AC20 uses `password`, which the raw pass catches, so AC20 cannot see this.
- E2 Raw-only identifiers that the split destroys: `keyChain` becomes `key Chain`, `passWord` becomes `pass Word`, `idRsa` becomes `id Rsa` (`id[_-]?` has no space), `bCrypt` becomes `b Crypt`. They are HIGH only through the raw half. A narrowed split-only GREEN passes every RED cell and misses them.
- E3 First-trigger ordering across passes: a raw hit on line 5 and a split-only hit on line 2 in the same category. "First trigger per category" does not say whether the label is `:2` or `:5`, and a raw-then-split GREEN reports `:5`.
- E4 Upper-run rule on a trailing plural: `JWTs` becomes `JW Ts`, and `URLs` becomes `UR Ls`. Neither is a hit raw or split, so this is not a regression versus r1, but `JWTs` stays LOW.
- E5 The column-0 `TASK=` line in phase-45 must also come before the scan call. The cell does not check position, so a line after the fenced scan block passes AC16.
- E6 CRLF `build-state.yaml` (r2 m7): TASK keeps `"\r`. This has no effect on classification.

## Findings

1. MINOR (m1). AC20 does not bind `LC_ALL=C` on the split sed (E1). In GREEN, implement the clause as written. Optional: add a cell with `authService` on line 2 after `\xff`, and have the review check the `LC_ALL=C` prefix on the split.
2. MINOR (m2). The raw half of the §5.1 union is not bound by any RED cell (E2). In GREEN, keep the raw `grep -a` pass unchanged and add the split as a second pass. Optional cell: content `keyChain` or `idRsa` should be HIGH.
3. MINOR (m3). Trigger ordering across the raw and split passes is unspecified (E3). Suggest the lower line number, or record raw-first in the PR body.
4. MINOR (m4). Label drift: the §5.7 heading says "AC11-AC15", the bats section comment says "AC11-AC18", and the bats header says "AC1-AC9".
5. MINOR (m5). AC16 does not check that the `TASK=` line comes before the scan call (E5). For phase-45, dedenting to column 0 ends the list item visually. Place the fence at column 0 directly after the bullet. This is cosmetic.
6. MINOR (m6). These r2 items carry over: m5 (no cells for `authn`, `xsrf`, `totp`, `csrf/id/api token`, `token vault/secret`, `session cookie/secret/fixation/hijack`, `sign ... jwt/token/cookie/message/request`) and m7 (CRLF / single quotes in the `$TASK` one-liner). Both are advisory and fit in the PR body.
7. MINOR (m7). The spec sha256 in the anchor was not verified by this gate (no shell). The orchestrator should confirm it matches the file on disk before GREEN.

All three r2 MAJOR findings are closed in the spec, and each has a RED cell that fails or shields as designed. Every AC11-AC14 and AC19-AC21 input matches a term under raw OR split. Every AC15 guard and the #129 reference case stay non-HIGH under the union. The AC16 line is satisfiable by the real one-liner at column 0 in both docs. AC17 cell 2 forces the newline defect on BSD and GNU sed. The design is feasible on bash 3.2 with BSD `sed -E` under `set -euo pipefail` and `LC_ALL=C`. No MAJOR finding.

VERDICT: APPROVED
