# bd#129 validation gate, round 2 (final): security-scan scope, delta r2 (spec §5 + RED cells 23-62)

<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd129-security-scan-scope.md sha256:32366bbe26a24cd886704aa079a25d16e68ba3b15838642d24ca0551ed14998d
-->

- Scope: spec §5 "Delta r2" (DRAFT r2) and RED cells 23-62 of `tests/security-scan-scope.bats` (AC11-AC18, 40 cells). §1-§4 were approved in round 1 (`-gate-r1.md`) and are not re-audited.
- UUT as audited: `scripts/security-scan.sh` (HEAD 0059e54, r1 GREEN), `phases/phase-05-inject.md`, `phases/phase-45-spec.md`.
- Spec sha256: I could not verify it, because this gate has no shell. The anchor is the orchestrator's value.
- Orchestrator RED run on base: "31 of the new 40 not ok, 9 AC15 + 2 AC18 ok". Those numbers do not add up (31 + 11 = 42). My per-cell simulation is in Step 3.

## Step 1: spec-internal consistency (literal-token drift)

The literals are consistent between §5 and the RED file: `security_classification`, `security_patterns_found`, `security_triggers`, `BD_SECURITY_SCAN_LEGACY`, the `TASK=` line text, `build-state.yaml`, `k: v`.

Inconsistencies inside §5:

1. §5.1 contradicts §5.3 (finding M1). §5.1 splits "a lowercase/**digit** followed by an uppercase letter". The canonical spelling `2FA` therefore becomes `2 FA`, and the §5.3 term `2fa` can never match it. Only lowercase `2fa` still matches. §5.1 also splits "an uppercase run followed by Upper+lower". A one-letter run counts, so `OAuth2` becomes `O Auth2`. After that, `oauth2?` cannot match (the word is broken), and `auth` cannot match either (it is followed by `2`, an alnum). In r1, `OAuth2` was HIGH through `oauth2?`. In r2 it is LOW. The delta exists to close fail-opens, and here it opens one.
2. §5.3 `hijack\w*` repeats r1 finding 1 (`\w` is not POSIX ERE). r1 GREEN already uses `[[:alnum:]_]*`. Write it that way in the spec (finding m1).
3. §5.7 AC18 says the legacy `authService.ts` content "is not split into hits", but the content (`const x = 1;`) contains no identifier that could be split (finding m4).

## Step 1.5: rule-overlap simulation (every AC11-AC15 input after §5.1 splitting)

Matching is on the split text: bounded, case-insensitive, first category hit wins per category. Classification order: AUTH/CRYPTO/SECRETS -> HIGH, else DATA -> MEDIUM, else LOW.

| AC | Input | After split | Matching term | Result |
|---|---|---|---|---|
| 11 | `passwords table` | same | `passw(or)?ds?` | HIGH |
| 11 | `hashed_passwords` | same | `passwords` (`_` boundary) | HIGH |
| 11 | `user sessions` | same | `(user\|...)[_ -]?sessions?` | HIGH |
| 11 | `API keys` | same (no lower->Upper) | `api[_ -]?keys?` | HIGH |
| 11 | `bearer tokens` | same | `(...bearer...)[_ -]?tokens?` | HIGH |
| 11 | `private key` | same | `(private\|public)[_ -]?keys?` | HIGH |
| 11 | `BEGIN RSA PRIVATE KEY` | same (all caps, no Upper+lower) | private-key term | HIGH |
| 11 | `id_rsa` | same | `id[_-]?(rsa\|...)` | HIGH |
| 11 | `ssh key` | same | `ssh[_ -]?keys?` | HIGH |
| 11 | `authz` | same | `authz` | HIGH |
| 11 | `ldap bind` | same | `ldap` | HIGH |
| 12 | `refresh_token` | same | `(refresh\|...)[_ -]?tokens?` | HIGH |
| 12 | `jwtToken` | `jwt Token` | `jwt` | HIGH |
| 12 | `sessionId` | `session Id` | `session[_ -]?(id\|...)` | HIGH |
| 12 | `SessionMiddleware` | `Session Middleware` | `session[_ -]?middleware` | HIGH |
| 12 | `authMiddleware` | `auth Middleware` | `auth` | HIGH |
| 13 | `src/authService.ts` | `src/auth Service.ts` | `auth` | HIGH |
| 13 | `src/AuthService.ts` | `src/Auth Service.ts` | `auth` | HIGH |
| 13 | `src/token_store.py` | same | `token[_ -]?(store\|...)` | HIGH |
| 13 | `src/session_manager.rb` | same | `session[_ -]?manager` | HIGH |
| 14 | `hash passwords with salt` | same | `passwords` | HIGH |
| 14 | `rotate API keys` | same | `api keys` | HIGH |
| 14 | `store user token in cookie` | same | `user[_ -]?tokens?` | HIGH |
| 14 | `sign webhook payloads` | same | `sign[_ -]?webhook` | HIGH |
| 14 | `add 2FA / MFA` | `add 2 FA / MFA` | **`mfa` only**. `2fa` is dead (M1) | HIGH, but only through MFA |
| 14 | `fix CSRF` | same | `csrf` | HIGH |
| 15 | `session token hash sign` | same | none (`session token` is not in the session suffix list, the token prefix set has no `session`, and `sign` has no object after it); no DATA | LOW |
| 15 | `tokens` / `sessions` | same | none (the qualified forms need a prefix) | LOW |
| 15 | `sessionStorage` | `session Storage` | none (`store` needs an `e` after `stor`, the input has `a`) | LOW |
| 15 | `author authority` | same | none (`auth` boundary fails, and `authoriz` vs `authorit`) | LOW |
| 15 | `process.env.NODE_ENV` | same (all caps, `.` boundaries) | none (`.env` is preceded by `s`) | LOW |
| 15 | `tokenizer hashtable` | same | none | LOW |
| 15 | path `src/config/reader.ts` | same | none | LOW |
| 15 | task `consolidate a config-file reader` | same | none, no DATA substring | LOW |

The existing cells still hold under §5. AC1 stays MEDIUM DATA (`fetch`). AC7 stays LOW `none` (`signed the`: `the` is not a sign object, and `session Storage` as above). The AC2 line numbers are unchanged, because a per-line sed preserves lines.

False-positive hazards under the widened terms. None of them breaks an AC15 guard. They are precision issues (HIGH only adds review), listed in m2 and in the adversarial edges:
- `(private|public)[_ -]?keys?` now accepts a space. TS/Java/C# members such as `private key: string;`, `private keys = new Map()` and `public keys()` become HIGH CRYPTO.
- `user[_ -]?tokens?`: in LLM tooling, `user tokens` / `userTokens` is a token count, not a credential.
- `id[_ -]?tokens?`: in lexer/parser code, `idToken` means identifier token.
- `sign...`: `design token(s)` / `designToken` is safe, because `e` before `sign` fails the boundary. `sign-in request` and `signup request` are safe. `signed message(s)` in chat code becomes HIGH, which is acceptable.
- `ldap`, `mfa`, `totp`, `csrf`, `xsrf`, `2fa` (bounded) carry no realistic ordinary-word hazard.
- Splitting itself: `useSessionStorage` -> `use Session Storage`, safe. `userSessionStorage` -> `user Session ...` -> HIGH, arguably intended. `getAuthor` -> `get Author`, safe. Random mixed-case strings (base64, lockfile `integrity` hashes, minified JS, `mktemp` suffixes) are the real new hazard. Every case transition becomes a boundary, so `xJwtQ` -> `x Jwt Q` -> HIGH. Under r1, a random alnum run could not hit because it had no internal boundaries.

## Step 2: contract (§5.1-§5.6) vs ACs

| §5 item | AC / RED | Gap |
|---|---|---|
| 5.1 split: content | AC12 | none |
| 5.1 split: path | AC13 | trigger label not asserted (m3) |
| 5.1 split: task text | none (no camelCase task input in AC14) | m5 |
| 5.1 "line numbers preserved" | no AC12/13 cell checks `:<line>` on a split hit | m3 |
| 5.1 split vs `2FA`/`OAuth2` | none. AC14 masks it with MFA | **M1** |
| 5.1 feasibility on binary / non-UTF-8 content | none | **M3** |
| 5.2 plurals | AC11, AC14 | none |
| 5.3 new terms | partial (m5) | advisory |
| 5.4 still-LOW list | AC15 | none |
| 5.5 `$TASK` line | AC16 | stub-passable (**M2**) |
| 5.6 state-file newline | AC17 | none |
| legacy untouched | AC18 | cell 1 does not discriminate (m4) |

## Step 3: RED adequacy

- Assert-time failures on base, simulated per cell: AC11 11/11, AC12 5/5, AC13 4/4 and AC14 6/6 are not ok, because every input misses the r1 bounded terms (`passwords`/`tokens`/`keys` plurals, a space separator, a camel suffix after an alnum). AC16 2/2 are not ok: no `^TASK=` line exists in either doc. AC17 is not ok if macOS `sed -i` preserves the missing final newline (current FreeBSD-derived sed does, so `k: vsecurity_classification: ...` fails `grep -qxF 'k: v'`). AC15 9/9 and AC18 2/2 are ok by design. Total: 29 not ok, 11 ok. If BSD `sed -i` appends the newline, AC17 is green on base and works as a shield, not a RED. No cell fails at collection: helpers are defined before use, and the inputs are single-quoted literals.
- No self-mocking: every cell runs the real script in a hermetic `mktemp -d`.
- **AC16 is stub-passable (M2).** The cell checks exactly one `^TASK=` line and then evaluates it against a state file that always holds `Add user authentication`. The doc line `TASK="Add user authentication"` (or `TASK='Add user authentication'`) passes both cells without reading `build-state.yaml`. The cell has to either assert the literal §5.5 line (`grep -qxF -- "TASK=\$(sed -n 's/^task: *//p' build-state.yaml | head -1 | sed 's/^\"//; s/\"\$//')"`), or evaluate the line twice against two different `task:` values.
- **AC14 `add 2FA / MFA` is masked (M1).** It passes through `mfa` alone, so a dead `2fa` term goes unnoticed. Split it into `enable 2FA` and `require MFA`, and add `configure OAuth2` (task) or `OAuth2` (content).
- AC18 cell 1 is effectively a duplicate of `AC8_legacy_1_does_not_scan_path_strings`. A legacy run that did split identifiers would still pass it (m4).

## Step 4: reachability (§1y)

| Side effect | Point | Host | Test path |
|---|---|---|---|
| split of file content | the `grep -m1 -n -a -iE` at `security-scan.sh:79` must read split output (e.g. `LC_ALL=C sed -E ... -- "$file" \| grep ...`, inside the existing `\|\| true`) | `scan_file` | AC12 `assert_content_high` |
| split of path / task | the `printf '%s\n' "$text" \| grep` at :91 | `scan_text` (called at :97 for the task, :110 for paths) | AC13 `assert_path_high`, AC14 `assert_task_high` |
| widened terms | default branch :46-48 | top level | AC11-AC15 |
| legacy untouched | legacy branch :40-44, and splitting must be gated on `LEGACY -eq 0` | top level | AC18 |
| newline before append | new check between the sed at :187 and the printf at :188 | state-file block | AC17 |
| `$TASK` | new column-0 line before `phase-05-inject.md:44` and `phase-45-spec.md:78` | doc text | AC16 |

Everything is reachable. Feasibility of BSD `sed -E` on bash 3.2 under `set -euo pipefail`:
- Backreferences and `{2,}` work in BSD `sed -E`. `--` before the file is accepted (getopt). A per-line `s///g` with no `N`/`D` preserves line numbers.
- SIGPIPE: `grep -m1` exits early, and sed may then die with 141. Under pipefail the pipeline returns 141, which the existing `|| true` inside `$(...)` absorbs while keeping grep's output. Do not drop the `|| true`, and do not add `head`.
- **Locale (M3).** Under a UTF-8 locale (the macOS default, inherited by bats), BSD sed stops with `RE error: illegal byte sequence` (exit 1) at the first non-UTF-8 byte. The `|| true` hides this, and everything after that byte goes unscanned, including plain `password`. That undoes 0059e54 ("scan binary files as text") for Latin-1 sources and binaries. Fix: pin `LC_ALL=C` for the split. `[[:upper:]]`/`[[:lower:]]` then become ASCII-only, which is what the identifier rule wants. Embedded NUL handling by BSD sed is unverified. Scanning the union of the raw line (the existing `grep -a`) and the split line keeps r1 behaviour as a floor.
- bash 3.2: no `${x,,}`, no `[[ =~ ]]` with `\w`. Doing the split with sed/awk is the right call. A pure-bash loop over lines would also break `-a`/NUL handling.

## Adversarial edges (not covered by the §5.7 AC table)

- E1 Canonical `2FA` / `OAuth2` after splitting: `enable 2FA`, `configure OAuth2`, content `OAuth2`. Default mode is LOW under §5.1 as written, and HIGH in r1 for OAuth2 (M1).
- E2 Non-UTF-8 byte before a hit: line 1 `printf '\xff\n'`, line 2 `password`. Under a UTF-8 locale with BSD sed, the hit is silently lost (M3). Shield cell: expect HIGH with trigger `AUTH=src/probe.txt:2`. A camelCase variant on line 2 (`authService`) proves the split runs past the byte.
- E3 Legacy parity that discriminates: under `BD_SECURITY_SCAN_LEGACY=1`, content `apiKey` is LOW today (`api.key` needs 7 chars, and `apiKey` has 6). If splitting leaked into legacy, `api Key` would match `api.key` and become HIGH SECRETS. This is a better AC18 cell 1 than `const x = 1;`.
- E4 Trigger label on a split hit: `--files src/authService.ts` must give `security_triggers: [AUTH=src/authService.ts]`, the original path, not `src/auth Service.ts`. The content variant must keep `<file>:<line>` with the original filename and the right line, e.g. `jwtToken` on line 3 -> `AUTH=src/probe.txt:3`.
- E5 Task-text splitting: `--task "wire the authService"` -> HIGH. §5.1 claims it, and no cell covers it.
- E6 Absolute mixed-case paths: `/Users/x/Projects/MyAuthTool/src/a.ts` -> `My Auth Tool` -> HIGH for every file, which extends r1 E1. In T01-T08, the macOS `mktemp` suffix (`tmp.aBcDeFgHiJ`) is now split into random 1-3 letter segments, a small chance of a flaky HIGH on `Jwt`/`Mfa`. The phase docs pass relative paths, so production is unaffected.
- E7 Base64 / minified / lockfile content (`package-lock.json` integrity hashes in a MODIFY list): case transitions become boundaries. The chance of a random `Jwt`/`Mfa`/`Auth` segment rises by orders of magnitude over r1. This causes a false HIGH, not a fail-open.
- E8 TS/Java/C# member `private key: string;` / `public keys()` -> HIGH CRYPTO through the space-separated private-key term.
- E9 `$TASK` with CRLF state file: `task: "Add"\r` -> `s/"$//` misses, and TASK becomes `Add"\r`. Classification is unaffected (both characters are non-alnum), but the value is not exact. A single-quoted YAML value keeps its quotes. A missing `build-state.yaml` prints a sed error and gives an empty TASK. The script then treats it as absent (unanalyzed when there are no files), which is correct.
- E10 `phase-45-spec.md` scan block is an indented list-item fence (`  ```` / `  bash ...`). AC16 greps `^TASK=`, so the line must sit at column 0. Putting it inside the indented fence fails AC16, and putting it unindented breaks the list rendering. Dedent the block, or relax the cell to `^[[:space:]]*TASK=` (in both the count and the extract).

## Findings

1. **MAJOR (M1). §5.1 splitting defeats the §5.3 term `2fa` and regresses `oauth2?`. RED masks it.** The digit -> Upper rule turns `2FA` into `2 FA`. The single-letter upper-run rule turns `OAuth2` into `O Auth2`, which no term matches (r1: HIGH). The AC14 cell `add 2FA / MFA` passes through `MFA` alone. Required:
   - Amend §5.1 in one of two ways. Either scan the union of the raw line and the split line (recommended, and it also floors M3), or narrow the rules: digit splits only before `[[:upper:]][[:lower:]]`, and the upper-run rule needs a run of 2 or more, `([[:upper:]]{2,})([[:upper:]][[:lower:]])`, which keeps `OAuth` whole and still splits `IDToken`, `APIKey`, `HTTPServer`.
   - Replace the AC14 cell with `enable 2FA` and `require MFA`, and add an `OAuth2` cell (task or content).
2. **MAJOR (M2). AC16 is stub-passable.** A hardcoded `TASK="Add user authentication"` doc line passes both AC16 cells. Assert the exact §5.5 line, or evaluate the extracted line against two different `task:` values.
3. **MAJOR (M3). The splitting step is fail-open on non-UTF-8 content under BSD sed, with no shield.** In a UTF-8 locale, BSD `sed` aborts at the first invalid byte, and the existing `|| true` hides it. The rest of the file goes unscanned, which reverses 0059e54 in this same lot. Required:
   - §5.1 must pin `LC_ALL=C` for the split (or the raw+split union from M1).
   - Add the E2 shield cell: invalid byte on line 1, `password` on line 2 -> HIGH, trigger `:2`.
4. MINOR (m1). `hijack\w*` in §5.3: write `hijack[[:alnum:]_]*` (r1 finding 1).
5. MINOR (m2). Precision hazards E6-E8 (`private key` members, `user tokens` as an LLM count, `idToken` in lexers, base64/minified splitting). Record them in §4 Known limits and the PR body. None breaks AC15.
6. MINOR (m3). No cell asserts the trigger label or line number for a split hit (E4). Add `assert_trigger "AUTH=src/authService\\.ts"` to AC13, and a `:<line>` check to one AC12 cell.
7. MINOR (m4). The AC18 cell 1 input does not discriminate. Use legacy `apiKey` content -> LOW (E3).
8. MINOR (m5). Thin term coverage. There are no cells for task-text splitting (E5), `authn`, `xsrf`, `totp`, `(web|http|login) session`, `session cookie/secret/fixation/hijack...`, `token vault/secret`, `id_ed25519`, `sign... jwt/token/cookie/message/request`, `csrf/id/api token`. These are advisory.
9. MINOR (m6). The `phase-45-spec.md` column-0 `TASK=` line conflicts with the indented list fence (E10).
10. MINOR (m7). The `$TASK` one-liner leaves `"\r` on a CRLF file and keeps single quotes. This is harmless for classification. Optionally add `tr -d '\r'`, or accept it in the PR body.
11. MINOR (m8). The orchestrator's RED count is inconsistent (31 + 11 != 40). My simulation is 29 not ok / 11 ok, assuming macOS `sed -i` preserves the missing final newline. Re-check AC17's base status when you run it.
12. MINOR (m9). In GREEN, run the newline check (`[ -s f ] && [ -n "$(tail -c1 f)" ]`) after the sed delete at :187 and before the appends, so a deleted unterminated last key cannot leave a join.

The three MAJOR findings all have one-line spec amendments plus small RED edits. AC11-AC13, AC15, AC17 and the rest of AC14 are sound: every listed input matches a §5 term after splitting, every guard stays non-HIGH, and the #129 reference case stays LOW. Legacy mode is untouched by the contract.

VERDICT: REJECTED
