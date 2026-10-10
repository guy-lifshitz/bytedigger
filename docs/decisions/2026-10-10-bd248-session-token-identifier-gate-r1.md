# bd#248 gate review r1: session token identifier rule

Reviewed: `docs/decisions/2026-10-10-bd248-session-token-identifier.md` and `tests/security-scan-compound-id.bats` (20 cells, 9 failing on current code), against `scripts/security-scan.sh`, `tests/security-scan-scope.bats` and `tests/security-scan.bats`.

## Method

- Read the spec, the RED file and the scan script, then compared literal tokens between them.
- Walked the proposed `PAT_SECRETS_ID` through the real paths: `scan_file` (`scripts/security-scan.sh:92-109`) and `scan_text` (`:112-122`), both under `LC_ALL=C`, with plain `grep -E`.
- Searched all `security-scan*.bats` files for the identifier forms.

## 1. Spec-internal consistency

- AC numbering, inputs and expected labels agree between spec section 2 and the RED cells: AC1-AC9, the same task strings, the same `SECRETS=task`, `SECRETS=<file>:3` and `SECRETS=<path>` triggers, the same guard inputs.
- Env var `BD_SECURITY_SCAN_LEGACY` and the output keys match the script (`:35`, `:204-206`).
- Finding 1 (MINOR) covers stale line references in the spec.

## 2. Simulation of the ERE

Pattern: `(^|[^[:alnum:]])([Ss]ession_?[Tt]oken|SESSION_?TOKEN)(s?)([^[:lower:]]|$)`

| Input | Result | Note |
|---|---|---|
| `rotate the sessionToken` | match | space before, line end after |
| `session_token`, `SESSION_TOKEN`, `SessionToken` | match | |
| `sessionTokens` | match | `s?` takes the `s`, then `$` |
| `sessionTokenHash` | match | `H` is not lower-case |
| `sessionTokenizer`, `sessionTokenize` | no match | `i` follows, `s?` cannot help |
| `session_tokenizer` | no match | same reason |
| `SessionTokenX` | match | upper-case continuation, consistent with the stated rule |
| `SESSION_TOKENIZER` | match | upper-case continuation, so a constant of this name is HIGH |
| `sessionTOKEN`, `Session_TOKEN` | no match | mixed case is outside both alternatives |
| `sessiontoken` | match | matches spec section 4 |
| `mysessionToken`, `getSessionToken`, `mySessionToken` | no match | alnum before the term |
| `my_session_token`, `x.sessionToken` | match | `_` and `.` are non-alnum |
| CRLF line `const sessionToken = 1;\r` | match | `\r` is not lower-case; `$` is not needed |
| `session token`, `session-token`, `session.token`, `session/tokenizer.ts` | no match | |
| `src/sessionToken/index.ts` (directory name) | match, HIGH, trigger is the path | consistent with the path rule, but no AC covers it |
| `sessionStorage` | no match | |

Class and bytes: with `LC_ALL=C` bytes above 127 count as non-alnum, so UTF-8 neighbours do not block a match. Line edges are handled by `^` and `$`.

Case-sensitivity: the existing loops use `grep -iE` on both raw and split text (`:100-101`, `:117-118`). The new rule must not go through those loops. Run with `-i`, `[^[:lower:]]` stops discriminating, so `sessionTokenHash` may fail and prose may leak. The spec says "case-sensitively against the raw text only" and AC5 (`sessionTokenHash`) would catch an `-i` implementation. The RED therefore constrains the mechanism well enough.

## 3. RED adequacy

- Failing cells on current code: AC1 (1), AC2 (2), AC3 (2), AC4 (2), AC5 (2) = 9. I walked each through the current patterns: the split text turns `sessionToken` into `session Token`, no existing term matches, and the result is LOW, so each fails at assertion time on the real script. No doubles or stubs: every cell runs `bash scripts/security-scan.sh` in a temp dir.
- Passing cells: AC6 (existing `auth[_ -]?tokens?`), AC7 guards, AC8 (legacy ignores the task, giving MEDIUM `unanalyzed`) and AC9 (existing suites). These are regression and guard cells, and they discriminate against an over-broad rule (`sessionTokenizer`, `sessionStorage`, the `session/tokenizer.ts` path).
- RED asserts match the spec text for every AC.
- Exact label checks: `assert_trigger` anchors the end of the entry, but not the start (`[[,]? ?` is optional). Weak, but sufficient here.

## 4. Existing cells

No cell in `tests/security-scan.bats` or `tests/security-scan-scope.bats` contains the identifier forms (grep above). The only hits are the prose `session token hash sign` (scope `AC1` at `:55`, `AC15_GUARD_reference_session_token_hash_sign_not_high` at `:386`), the AC12 `sessionId` and `SessionMiddleware` cells, and the `sessionStorage` guards at `:190` and `:389`. None of them can match the new rule because it requires an underscore or case-joined `Token` with a non-lower follower, and the rule runs on raw text only. They stay green.

## 5. Class and chokepoint

Class: deterministic shell check. Chokepoint: `scripts/security-scan.sh`. Both are stated in the spec header.

## Adversarial edges not covered by an AC

- `sessionTOKEN` and `Session_TOKEN` (no match by the pattern) and `sessiontoken` (match, claimed in spec section 4) have no cell.
- `SESSION_TOKENIZER` and `SessionTokenX` are HIGH by the rule, with no cell fixing that behaviour.
- `getSessionToken` and `mySessionToken` are missed (alnum before the term). These are common identifiers.
- A path with `sessionToken` as a directory (`src/sessionToken/index.ts`) is HIGH, with no cell.
- CRLF content and a match on the first or last line of a file have no cell.

## Findings

1. MINOR: stale line references in the spec. Section 0 puts `PAT_SECRETS` at `scripts/security-scan.sh:51` and `split_ids` at `:60-64`; in the tree they are at `:48` and `:53-56`.
2. MINOR: the spec says the rule is "added in the default-mode term lists", but the existing loops use `-iE` and the split text. The implementation needs a separate case-sensitive raw-text pass. State this in the spec so it is not folded into `PAT_SECRETS`.
3. MINOR: section 4 (known limits) does not list prefixed camelCase such as `getSessionToken` or `mySessionToken`, which the ERE misses by design (`my_session_token` is caught, `mySessionToken` is not). Add it to the PR body limits, or widen the leading boundary and add a cell.
4. MINOR: no cells for the edges listed above (`sessionTOKEN`, `sessiontoken`, `SESSION_TOKENIZER`, directory name, CRLF). Optional.

No MAJOR finding. Every AC is checkable on the real script, the 9 failing cells fail for the stated reason on current code, and existing cells are unaffected.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS

VERDICT: APPROVED
