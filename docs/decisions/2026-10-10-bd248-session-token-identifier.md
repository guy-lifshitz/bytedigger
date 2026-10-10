---
red_tests:
  - tests/security-scan-compound-id.bats
paths:
  - scripts/security-scan.sh
---

# bd#248: `sessionToken` / `session_token` stay LOW in the security scan

**Status: DRAFT r1** · **Tier:** 2 · **Class:** deterministic shell check (no model call, no provider, so there is nothing to degrade and the same result on a subscription or an API key)
**Chokepoint:** `scripts/security-scan.sh`, the only writer of `security_classification` / `security_patterns_found` / `security_triggers`. One rule is added in the default-mode term lists; the identifier split stays the single normalisation step and gets no per-identifier cases.

## 0. Verified premises (live, base 0777ae9)

- `--task "rotate the sessionToken"` prints `security_classification: LOW`; `--task "rotate the session_token"` too; `--task "rotate the authToken"` prints HIGH (`auth[_ -]?tokens?` in `PAT_SECRETS`, `scripts/security-scan.sh:51`). Confirmed by running the script.
- Why the split cannot be used: `split_ids` (`:60-64`) turns `sessionToken` into `session Token`, which is the same text as the prose `session token`. The prose phrase must stay not-HIGH (cells `AC1` and `AC15_GUARD_reference_session_token_hash_sign_not_high` in `tests/security-scan-scope.bats`, unchanged by this lot). So the new rule has to look at the raw text, where the identifier form is still visible.
- The reference text in the issue ("rotate the sessionToken on login failure") is already HIGH through the word `login`; the miss shows on text without another auth word. The cells below use text without one.

## 1. Contract

Default mode only. A new term list `PAT_SECRETS_ID` is matched **case-sensitively against the raw text only** (task text, path string, file content lines), never against the split text. A hit marks category SECRETS, with the usual first-trigger label (`task`, `<path>`, `<file>:<line>`).

`PAT_SECRETS_ID` = `(^|[^[:alnum:]])([Ss]ession_?[Tt]oken|SESSION_?TOKEN)(s?)([^[:lower:]]|$)` (portable ERE, no `\b`, no `-P`). Consequences, all by the identifier form and not by a list of names:

- HIGH (SECRETS): `sessionToken`, `SessionToken`, `session_token`, `SESSION_TOKEN`, `sessionTokens`, `sessionTokenHash` (next char upper-case), `my_session_token`.
- Not matched: the prose `session token` (a space), `session-token`, `session.token`, `sessionTokenizer`, `sessionTokenize` (lower-case continuation), `sessionStorage`, bare `token(s)` / `session(s)`.
- `authToken` stays HIGH through the existing term; no change.

Classification, state file, output format, exit code 0: unchanged. Existing term lists, `bound`, `split_ids`, DATA/INFRA: unchanged.

**Kill switch / rollout.** The existing `BD_SECURITY_SCAN_LEGACY=1` mode keeps the old matching and does not get the new rule (legacy never scans task text and already matches substring `session`/`token`). No new flag: the rule is one added match in an informational scan that never blocks, and the existing switch already restores the old behaviour. Shadow-then-enforce does not apply, as the scan has no enforce step; the output only raises the risk class that downstream steps already read.

## 2. Acceptance criteria (RED `tests/security-scan-compound-id.bats`, hermetic temp dirs, real script, default mode with `BD_SECURITY_SCAN_LEGACY` unset)

- AC1: `--task "rotate the sessionToken"`, no files → `security_classification: HIGH`, patterns `SECRETS`, triggers contain `SECRETS=task`.
- AC2: same for `--task "rotate the session_token"` and `--task "rotate the SESSION_TOKEN"`.
- AC3: a file whose line 3 is `const sessionToken = read();` (other lines benign) via `--files` → HIGH, `SECRETS`, trigger `SECRETS=<file>:3`; same for a `session_token = read()` line.
- AC4: planned path, file absent: `--files src/sessionToken.ts` → HIGH, trigger `SECRETS=src/sessionToken.ts`; `--files src/session_token.py` → HIGH.
- AC5: continuations: `--task "log sessionTokens"` and `--task "persist sessionTokenHash"` → HIGH.
- AC6 (regression, behaviour kept): `--task "rotate the authToken"` → HIGH; a file with `authToken` → HIGH.
- AC7 (guards, never HIGH, exit 0): tasks `session token hash sign`, `use sessionStorage`, `fix the sessionTokenizer`, `list sessions and tokens`, `a session-token note`; path `src/session/tokenizer.ts` (absent).
- AC8 (kill switch): `BD_SECURITY_SCAN_LEGACY=1` with `--task "rotate the sessionToken"`, no files → MEDIUM `unanalyzed` (task ignored, as before).
- AC9 (guard): `tests/security-scan.bats` and `tests/security-scan-scope.bats` pass with no edit to them.

## 3. Out of scope

CI for `.bats` (bd#256), the existing cells and phrases, other compound identifiers (`refreshToken` etc. are already covered by the split step), `session.token` property access, phase docs, engine code.

## 4. Known limits (go into the PR body)

The rule is a raw-text identifier form for this one pair. Dot access (`session.token`), kebab (`session-token`) and a mixed form such as `sessionToken` split by the author across a line break are not caught. A flat all-lower `sessiontoken` is caught (no space, identifier form).
