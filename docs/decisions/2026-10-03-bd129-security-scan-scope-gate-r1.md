# bd#129 validation gate, round 1: security-scan scope (spec + RED audit)

<!-- verdict-anchor
spec: docs/decisions/2026-10-03-bd129-security-scan-scope.md sha256:b8c1dab6244e0de4e8819480324f2e7894e65b0449f4936185233cc84ae701b8
-->

- Spec: `docs/decisions/2026-10-03-bd129-security-scan-scope.md` (DRAFT r1)
- RED: `tests/security-scan-scope.bats` (22 cells)
- UUT: `scripts/security-scan.sh`, `phases/phase-05-inject.md`, `phases/phase-45-spec.md`; guard `tests/security-scan.bats` (T01-T08)
- Preflight: `facts.md` OK (HEAD 43d3b49, prescreen clean, no siblings, no known-reds)
- Orchestrator RED run on base: 20 not ok / 2 ok (AC4_GUARD, AC6_GUARD). This matches my per-cell simulation below.

## Step 1: spec-internal consistency (literal-token drift)

Literal tokens checked across §0-§4 and the RED file. All are consistent: `BD_SECURITY_SCAN_LEGACY`, `security_triggers`, `security_classification`, `security_patterns_found`, `task_only`, `unanalyzed`, `--task`, `--files`, `--state-file`, and the trigger forms `<file>:<line>` / `<file>` / `task`. The RED file uses the same literals.

One internal inconsistency (finding 1): §1.2 says the patterns are "portable to BSD and GNU grep; no `\b`, no `-P`", but the term lists use `\w*` (`authenticat\w*`, `authoriz\w*`, `encrypt\w*`, `decrypt\w*`, `crypto\w*`). `\w` is a GNU/enhanced-regex extension, not POSIX ERE. Where it is not supported, `\w*` reads as "zero or more `w`", and then the trailing `([^[:alnum:]]|$)` boundary rejects `authenticated`, `encrypted`, `cryptography`. No RED cell uses a suffixed form (AC2 uses `encrypt(`, which matches with zero-length `\w*`), so a non-portable regex would pass every cell.

## Step 1.5: rule-overlap simulation (classifier branches)

Branch order, from the spec: AUTH/CRYPTO/SECRETS -> HIGH; else DATA -> MEDIUM; else no files and no task -> MEDIUM `unanalyzed`; else task only with no hit -> LOW `task_only`; else LOW (+INFRA).

| Input | First matching branch | Expected | OK |
|---|---|---|---|
| AC1 file (`session token hash sign` + `fetch`) default | DATA (`fetch`) | MEDIUM DATA | yes (RED is stricter than AC1 text but agrees with §1.3) |
| AC4 `add JWT login to the api`, no files | AUTH (`jwt`, `login`) | HIGH AUTH | yes; `api` alone is not SECRETS |
| AC4 `consolidate a config-file reader` | no hit in any category (DATA substrings checked: none) | LOW `task_only` | yes |
| no args | unanalyzed | MEDIUM | yes (GUARD, green on base) |
| AC6 absent `src/auth/login.ts` | AUTH by path (`/auth/`, `login.`) | HIGH | yes |
| AC6 absent `src/config/reader.ts` | none -> LOW | LOW | yes (GUARD) |
| AC7 `author signed design; hashtable; tokenizer; sessionStorage; process.env.NODE_ENV` | every term checked: `author` fails the alnum boundary after `auth`, `authoriz` does not match `author`; `sign`/`hash`/`token`/`session` dropped; `.env` preceded by `s`. DATA/INFRA substrings: none | LOW none | yes |
| Legacy=1, AC1 file | old substrings: session->AUTH, hash/sign->CRYPTO, token->SECRETS | HIGH AUTH,CRYPTO,SECRETS | yes |
| Legacy=1, `--task` only | task ignored -> unanalyzed | MEDIUM | yes |
| Task only, DATA hit (e.g. "fix the bug where ...") | DATA | MEDIUM DATA (not `task_only`) | not specified; see finding 5 |
| Task only, INFRA hit (e.g. "deploy helper") | LOW | `task_only` or `INFRA` or both? | not specified; finding 5 |
| Task and file both hit AUTH | HIGH | which source is "first trigger"? | not specified; finding 6 |

Guard simulation for T01-T08 under the new patterns, with path strings scanned (absolute `$TMPDIR` paths):
- T01/T07 `jwt.verify(token, secret)`: `jwt` is bounded -> AUTH HIGH. Pass.
- T02 `encrypt(data, key)`: CRYPTO HIGH. Pass.
- T03 `const api_key = process.env.SECRET;`: `api_key` matches `api[_-]?key`, and `SECRET` matches `secrets?` between `.` and `;` -> SECRETS HIGH. Pass.
- T04 `fetch('/api/users')`: DATA MEDIUM. No HIGH term in the content or the path. Pass.
- T05 Dockerfile: no new HIGH term, and INFRA keeps it LOW. Pass.
- T06 `console.log('hello world')`: LOW. Pass.
- T08 `--files ""`, no task: unanalyzed MEDIUM. Pass.
The guard stays feasible. The one residual risk is path-string scanning of absolute temp paths (adversarial edge E1).

## Step 2: §2-vs-§3 cross-check (contract vs ACs; here §1 = contract, §2 = ACs)

| Contract terminal/side-effect path | AC | RED |
|---|---|---|
| stdout 3 lines incl. `security_triggers` | AC2/4/6 | yes |
| state-file remove-then-append for 3 keys | AC3 | yes (2 runs, counts and last value) |
| fail-closed `unanalyzed` | AC4 guard | yes |
| `task_only` LOW | AC4 | yes |
| kill switch exactly `1` vs other values | AC8 | yes (0/true/empty/yes, plus unset via `env -u`) |
| legacy still writes triggers | AC8 | presence only; content unconstrained (finding 7) |
| phase-05 no `git ls-files` fallback outside legacy | AC9 | yes |
| phase-45 re-run with `--task` + `--files` | AC9 | yes; "same `--state-file`" is not asserted (finding 8) |
| always exit 0 / unknown argument exits 0 | none new | covered only by base behaviour (status 0 checks in every cell) |

No contract path lacks an AC and no AC lacks a contract path. No MAJOR mismatch.

## Step 3: RED adequacy

- Coverage: AC1(1), AC2(3), AC3(2), AC4(3), AC5(2), AC6(2), AC7(2), AC8(4), AC9(3) = 22 cells. AC10 is the existing suite, run by hand. This is acceptable, but it must be run explicitly before GREEN is declared done, because `ci.yml` does not run bats (spec §0).
- Fails at assert time, not collect time: each of the 20 RED cells was simulated against base. Cells that pass `--task` fail because base prints `Unknown argument` and exits 0 with no output, so `out_has_line` fails. Provenance cells fail on the missing `security_triggers` line. AC1 and AC8-values fail on HIGH != MEDIUM. AC9 fails on doc content. None fails on syntax or setup. The two GUARDs pass on base, as designed.
- No self-mocking: every cell runs the real script via `bash "$SCRIPT"` in a hermetic `mktemp -d`, with the kill switch explicitly unset in `scan()`.
- Stub-passability: no cell can be passed vacuously without real behaviour. Some cells are weak:
  - `assert_trigger` regex `[[,]? ?ENTRY(,|\])` has an optional left anchor, so `XAUTH=src/a.ts:3` would match. Negligible.
  - The AC9 block-check does not require the legacy `git ls-files` fallback to survive (`seen` is never asserted). A GREEN that simply deletes it passes. The spec says "survives only under legacy", which this is compatible with.
  - Per-term coverage is thin. No positive cell for `.env`, `vault`, `cipher`, `hmac`, `bcrypt`, `oauth2`, `saml`, `credentials`, `(access|auth|bearer)token`, `(private|public)key`, or any `\w*`-suffixed form. A GREEN with only a subset of §1.2 terms passes (finding 2).

## Step 4: reachability (§1y)

| Side-effect AC | Point | Host | Test path |
|---|---|---|---|
| AC3 triggers in state file | new `printf 'security_triggers: ...' >> "$STATE_FILE"` beside `security-scan.sh:112-113`, sed delete at :111 extended | top-level "State file update" block | `scan --state-file "$WORK/build-state.yaml"` -> file grep |
| AC2/4/6 stdout trigger | new `echo "security_triggers: ..."` beside :102-103 | top-level "Output" block | `run ... ; out_has_line / assert_trigger` |
| AC4 task / AC6 path | new scan of `$TASK` and of each `$f` string ahead of the `[[ ! -f "$f" ]] && continue` at :45 (the path scan must come before that skip) | file loop :39-52 plus new task block | `scan --task` / `scan --files <absent>` |
| AC8 kill switch | new `${BD_SECURITY_SCAN_LEGACY:-}` test (must use `:-` under `set -u`) | top of the pattern section | `env BD_SECURITY_SCAN_LEGACY=...` |
| AC9 docs | `phase-05-inject.md:30-40`, new block in `phase-45-spec.md` | doc text | awk/grep over the doc |

Everything is reachable. Note that `--cwd` is parsed but never used (`security-scan.sh:18`). Relative `--files` therefore resolve against the process cwd, and the RED relies on `cd "$WORK"` in `setup()`. This works, and the phase docs run from the repo root, but see E1.

## Adversarial edges (not covered by the §2 AC table)

- E1 (absolute-path decoy). Path strings are scanned verbatim. When the caller passes absolute paths, as T01-T08 do with `$TMPDIR/...`, the checkout location joins the classification. A repo cloned under `~/src/oauth-proxy/`, or a `TMPDIR` under an `auth-*` directory, makes every file HIGH. `_` counts as a boundary, so macOS `/var/folders/xx/<id>_<id>/T/` segments are split on `_`. A hit by chance is unlikely, but the result becomes machine-dependent. Fix: scan only the part of the path relative to `--cwd` (strip a leading `$CWD/`). Also say whether DATA/INFRA substring patterns apply to path strings. As written, `scripts/update-x.sh` or `src/api/request.ts` would be MEDIUM by path.
- E2 (root `.env` regression). The §1.2 `.env` rule lists whitespace, quote, `/`, `(` and `=` as the allowed predecessors, but not line start. So `--files .env` (the most sensitive file there is, planned or modified at repo root) gets no SECRETS hit by path, and neither does a `.gitignore`/config line that begins with `.env`. No trailing boundary is defined either: `.envrc` and `.environment` would count. Add `^` to the predecessor set and define the trailing boundary (`.env` followed by end, `.`, quote, or whitespace).
- E3 (camelCase false negatives). A non-alphanumeric boundary means `isAuthenticated`, `requireAuth`, `userPassword`, `jwtSecret`, `loginUser`, `verifyJwt` and `getAuthToken` (also as a path, e.g. `src/middleware/requireAuth.ts`) never match. In JS/TS this is the dominant identifier style. §4 Known limits does not mention it, but it is the main new false-negative class of this lot. Either add a lower-to-upper camel boundary, e.g. treat `[a-z]Auth` as a boundary for the capitalised term, or record it in §4 and the PR body.
- E4 (pipefail/SIGPIPE and binary files). Provenance needs a line number. `grep -n ... | head -1` under `set -o pipefail` can return 141 on large files, and `set -e` then aborts with partial output and a non-zero exit, breaking "always exit 0". On binary files, `grep -n` prints `Binary file ... matches` with no line number. Now that the `git ls-files` fallback is gone, images in a diff are realistic. Use `grep -m1 -n -I` (or `-a`) with `|| true`, and never use `head` in a pipeline.
- E5 (task text with special characters). If GREEN feeds the task via `echo "$TASK"`, a task such as `-n`, `-e foo` or `a\cb` is mangled. Use `printf '%s\n' "$TASK"` or a here-string. A multi-line task is fine because grep is line-based. Bash `[[ =~ ]]` in the task path must not use `\w` either (see finding 1).
- E6 (empty `--task ""`). Phase 0.5 with an empty `$TASK` passes `--task ""`. The spec says "neither `--files` nor `--task`". Define presence as non-empty (as T08 does for `--files ""`) so the result stays MEDIUM `unanalyzed` and not LOW `task_only`.
- E7 (flag without value). `--task` or `--files` as the last argument trips `set -u` on `$2` (and `shift 2` fails), so the script exits 1. This existed before for the old flags, but the new flag inherits it and it violates "always exit 0". Use `"${2:-}"` and `shift $(( $# > 1 ? 2 : 1 ))`.
- E8 (comma and YAML-special paths). `--files` is comma-separated, so a path containing `,` cannot be expressed. Accept this and note it. Paths containing `[`/`]` (Next.js `app/[slug]/login/page.tsx`), `#` or `, ` go raw into the flow list `security_triggers: [AUTH=app/[slug]/login/page.tsx]`, which is invalid YAML flow syntax. The current consumers are line-based (`scripts/ts/lib/state-reader.ts`, and `scripts/ship_pr_text.py:59-72` tolerates malformed lists), so nothing breaks today. Quoting entries, or documenting the field as a raw string, would harden it. A file literally named `task` collides with the `task` provenance token. This is cosmetic.
- E9 (state-file replacement under legacy and on a missing file). AC3 covers default mode only. A legacy run after a default run must also replace `security_triggers` (one sed for all three keys covers it). `--state-file` pointing to a missing file keeps the current silent skip (`-f` check at :109). It should not be created.

## Findings

1. MINOR. Spec §1.2 portability claim vs `\w*` (Step 1). Replace `\w*` with `[[:alnum:]_]*` in the spec, or at least in GREEN, and add one RED-shield cell (e.g. `isAuthenticated` is out of scope, but `authenticated(` or `encrypted.` should be HIGH) so a non-portable regex cannot pass silently.
2. MINOR. RED does not cover each §1.2 term (Step 3). Recommended additions: `.env` positive (`source .env` / `"./.env"`), one `\w*`-suffixed term, `vault` or `bcrypt`. These are advisory, because the AC table does not demand per-term cells.
3. MINOR. E2: the `.env` rule excludes line start and has no trailing boundary, so the root `.env` path is missed. Add `^` in the spec text.
4. MINOR. E1: path strings should be scanned relative to `--cwd`, and the spec should say whether DATA/INFRA apply to path strings and to task prose. Unchanged DATA substrings on English task text ("where", "update", "request") will make many tasks MEDIUM. This is harmless for `phase_6_review.py:430`, which checks only HIGH, but it should be stated.
5. MINOR. §1.3 does not specify a task-only run with a DATA hit (presumably MEDIUM DATA) or an INFRA-only hit (`task_only` vs `INFRA`). State it.
6. MINOR. §1.4 "first trigger per category" does not define the order when task, path and content all hit the same category, or the order of entries in the list. Fix it, e.g. task, then files in `--files` order, path before content, entries in AUTH, CRYPTO, SECRETS order.
7. MINOR. Legacy-mode `security_triggers` content is unspecified, and RED accepts `[]`. State whether legacy fills `<file>:<line>` from the old patterns.
8. MINOR. Phase 4.5 "same `--state-file`" (§1.6) is not asserted by AC9. Add `--state-file` to the AC9 phase-45 check, or accept the gap.
9. MINOR. E3: camelCase false negatives are a new, unacknowledged limit. Add it to §4 Known limits and the PR body, or add a camel boundary.
10. MINOR. E4/E5/E6/E7: GREEN implementation guidance. Use `grep -m1 -n -I ... || true` with no `head` pipeline, use `${BD_SECURITY_SCAN_LEGACY:-}`, use `printf '%s\n' "$TASK"`, treat an empty `--task` as absent, and use `"${2:-}"` for flag values. Run `tests/security-scan.bats` by hand for AC10, since CI does not run bats.

No MAJOR findings. The spec is feasible and internally coherent apart from finding 1, which is fixable in GREEN without changing the contract's intent. The RED file fails at assert time on base for all 20 non-guard cells and is not stub-passable. T01-T08 stay green under the specified patterns.

VERDICT: APPROVED
