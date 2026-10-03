# S3: spec numeric/threshold ACs backed by real samples, circular-fixture check (script, SHADOW by default)

**Status: DRAFT r2** (gate r1 REJECTED, 3 MAJOR + 12 MINOR: `2026-10-03-s3-gate-r1.md`) · **Tier:** 2 (new standalone `scripts/*.py`, no engine registry, no `flags_catalog` entry) · **Class:** PROCESS (vacuous-green by circular spec: class 1668)
**Chokepoint:** `scripts/spec_sample_check.py` `main()`: the only place a verdict is produced; exit code + JSON are the verdict.
**Provenance:** task 1668, A/B #2208 r3 (bd 1/7 hidden stubs, C0 7/7): the spec froze "prose share < 0.25" without measuring real stubs (~190 chars of intro put them above it) and the suite was 74 tests on synthetic fixtures the lot wrote itself; every LLM gate passed. Audit hal#2320 finding 2 / §6 row 1668: rules 1b (live baseline), 1l, 1y are prompt-only, no deterministic layer. Issue bd#231. Plan approved by Guy 2026-10-03 (audit repair step 3).
**Principles:** script first, no LLM, provider-agnostic (no `claude`, no API key, no HAL path, no bun), RED first, nothing added to the engine registry. Wiring into `build-gate.sh` / the engine is a separate follow-up (needs the gate-point decision, same as S4/M1).

## 1. Contract

`python3 scripts/spec_sample_check.py --root DIR --spec FILE --red F1[,F2...] --base REF [--today YYYY-MM-DD] [--shadow-log FILE]`

- `--root` a git work tree; `--spec` the frozen spec (path relative to root or absolute under root); `--red` comma list of RED test files (same path rules); `--base` is REQUIRED (the commit the lot branched from; there is no default, because with `HEAD` a lot that already committed its fixtures would see them as real); `--today` default current UTC date (test seam for the expiry check).
- Stdout: one JSON object `{"status", "mode", "would_block", "findings", "verified", "unverified", "flag", "reason"}`; each finding `{code, ac, detail}` (`ac` is the AC id or `""`).
- `status`: `clean` (no findings), `flagged` (>=1 finding), `unavailable` (fail-closed, F-rules), `flag_expired`.
- Exit: 0 = clean or flagged-in-SHADOW; 1 = flagged in ENFORCE; 2 = unavailable; 3 = `flag_expired`.

## 2. Flag: `HAL_SPEC_SAMPLE_ENFORCE`

- Read by the script only (`os.environ`). Exactly `1` = ENFORCE; any other value or unset = SHADOW (`mode` reports which). SHADOW: findings are reported (`status: flagged`, `would_block: true`), exit 0; if `--shadow-log` is given one JSON line `{ts, spec, mode, would_block, codes}` is appended per run (evidence for the flip decision, audit finding 1).
- Constants in the script: `FLAG_OWNER = "s3-bytedigger (MGR)"`, `FLAG_EXPIRES = "2026-10-17"` (+14 days). The JSON `flag` object is `{name, owner, expires, expired}`.
- **Expiry has teeth:** in SHADOW, `today > FLAG_EXPIRES` gives `status: flag_expired`, exit 3 (flip to ENFORCE or extend `FLAG_EXPIRES` by an edit); the findings are still listed. In ENFORCE the expiry is irrelevant. `today == FLAG_EXPIRES` is not expired.

## 3. Definitions

- **Lot-authored set A** = `git diff --no-renames --name-only --diff-filter=ACMRT <base>` union `git ls-files --others --exclude-standard` (run in root), plus `--spec` and `--red` files.
- **Real sample** = a regular, non-empty file that is either (i) tracked at `<base>` (`git cat-file -e <base>:<path>`) and NOT in A, or (ii) an absolute path outside `--root`. Anything else is not real.
- **Numeric AC**: a spec line matching `^\s*(?:[-*+]\s+|\d+[.)]\s+)?\**(AC[0-9A-Za-z_-]*)\**\s*[:.)(—-]` is an AC; its block is that line plus following lines up to the next AC line or a `#` heading. After removing `sample:` / `measured:` segments, the block is *numeric* iff it has a **threshold**: a comparison, in any of these forms (N is a decimal number `-?\d+(\.\d+)?`, a trailing `%` allowed): symbols `<`, `<=`, `>`, `>=`, `≤`, `≥`, `==` followed by N; words mapped to operators: `<` = `less than`, `below`, `under`, `fewer than`, `меньше`, `ниже`; `<=` = `at most`, `no more than`, `not more than`, `up to`, `max`, `не более`; `>` = `more than`, `greater than`, `above`, `over`, `exceeds`, `больше`, `выше`; `>=` = `at least`, `not less than`, `min`, `не менее`; a reversed symbol form `N <op> <identifier>` (for example `0.25 > share`) is the flipped operator; or a unit number `N (%|ms|s|sec|seconds|KB|MB|chars|characters|tokens|lines|rows|files)` followed by a non-word character or end of line (so `1668 shows` is not a unit number). A bare `under N`/`over N` with a unit is the word form plus the unit. Fenced code blocks (``` lines) are skipped when finding AC lines and headings. The AC id regex is `AC[-_]?\d[0-9A-Za-z_-]*` (so `ACME:` is not an AC). `N` is **non-trivial** iff it is a non-integer or an integer with absolute value >= 10 (so `0`, `1`, `3`, `>= 5` counts are exempt). An AC whose only thresholds are trivial is not numeric.
- The AC's **comparisons** are its operator forms with a non-trivial N, in order of appearance (unit-only numbers have none). Pairing with `measured`: exactly one comparison, every measured value must satisfy it; k>1 comparisons, `measured` must have exactly k values and the i-th pairs with the i-th comparison; any other count is not checked, the AC is listed in `unverified` with `ambiguous_pairing` (never a contradiction). Trivial comparisons (`>= 1`, `== 0`) are never used for the contradiction check.
- Citations in the block: `sample: <path>` (one or more, comma separated, optional backticks; relative paths resolve against `--root`) and `measured: <n>[, <n>...]` (a trailing `%` is stripped).

## 4. Checks (finding codes)

| code | trigger |
|---|---|
| `NO_SAMPLE` | numeric AC without any `sample:` |
| `SAMPLE_NOT_REAL` | a cited sample is missing, empty, not a regular file, a symlink, lot-authored (in A), or untracked at base and inside root |
| `NO_MEASURED` | numeric AC with a valid sample but no `measured:` |
| `MEASURED_CONTRADICTS` | the AC has a comparison and some `measured` value does not satisfy it (`==` compares with 1e-9 tolerance); the threshold was not calibrated on the real data it cites (the 1668 shape: 0.25 vs real 0.31) |
| `RED_EXPECTED_FROM_LOT_FIXTURE` | a non-trivial number asserted in a RED file occurs as a whole numeric token in a lot-authored data file (ext in .json .jsonl .ndjson .csv .tsv .txt .yaml .yml .xml .log .html, not the spec or a RED file) and is *calibrated nowhere* (below) |
| `RED_EXPECTED_FROM_INLINE_FIXTURE` | same, but the number occurs as a whole token inside a string literal of length >= 8 in a RED `.py` file |

- **Asserted numbers:** for `.py` RED files, numeric constants inside `assert` statements and inside any `approx(...)` call (AST; bools excluded); other RED files: numeric tokens on lines containing `assert`, `expect`, `toBe`, `toEqual` or `==`. Only non-trivial numbers are considered. A `.py` RED file that does not parse is `unavailable`.
- **Calibrated somewhere (ok-set):** the number occurs as a whole token in the text of a *valid* cited real sample, or equals a `measured` value or the threshold of an AC with no finding of its own.
- A whole numeric token: not preceded by `[\w.]`, not followed by `\w` or by `.digit`.
- `verified` lists `{ac, sample, measured}` for ACs that passed all AC checks and whose every measured value occurs literally in the sample; ACs that passed but whose measured value is not literal in the sample (ratios, derived values) are listed in `unverified`. The script does not recompute measurements (limit stated; recomputation is the caller's `measure` command, out of scope).
- A finding's `detail` contains the offending number text and, for sample findings, the path. Non-UTF-8 samples/fixtures are read with replacement (still real). A run that would be both `unavailable` and past expiry is `unavailable` (exit 2). `--shadow-log` is appended in every mode and for `flagged` and `clean` runs.
- **Stated limits (the spec side is the real catcher):** the RED side fires only on a literal echo of the number (a ratio derived from synthetic text, a test asserting the threshold itself, a fixture built by concatenation are not seen); a lot can cite an unrelated committed file with an invented `measured:` or an outside-repo absolute path (the reviewer/gate sees the citation, the script cannot judge relevance); assert messages and docstrings count as inline string literals (false positives are tuned in SHADOW).
- Spec with no numeric AC: the AC checks yield nothing; the RED checks still run with an empty ok-set.

## 5. Fail-closed rules

| # | case | result |
|---|---|---|
| F1 | `--root` not a git work tree or not its top level (realpath of `git rev-parse --show-toplevel` differs), `--base` required but missing is F3, `--base` does not resolve, any git command fails | exit 2 `git_failed` (not top level: `bad_input`) |
| F6 | `--spec` is tracked at `<base>` (the base already contains the lot's spec, so every lot file would look real) | exit 2 `base_contains_lot` |
| F2 | `--spec` / `--red` missing, unreadable, outside root (relative `..` or absolute not under root), empty `--red` list, non-UTF-8 | exit 2 `bad_input` |
| F3 | bad `--today` (not `YYYY-MM-DD`), missing `--base`/`--spec`/`--red`, unknown option | exit 2 `bad_usage`, JSON on stdout (never a bare argparse trace) |
| F4 | `.py` RED file with a syntax error | exit 2 `red_unparseable` |
| F5 | any uncaught exception | top-level handler: exit 2 `internal_error` JSON, never exit 1 |

## 6. Acceptance criteria (RED file `tests/test_s3_spec_sample_check.py`)

All tests hermetic: git repos under `tmp_path` (`git init`, base commit with real sample files, then lot changes uncommitted), script run as subprocess with `sys.executable`, env without `HAL_*` except what the test sets, PATH without `claude`/`bun`, `ANTHROPIC_API_KEY` absent. Nothing imports the script (collection succeeds; failure at assert time, §1q).

- AC1: spec AC with (every run passes `--base <base-sha>`) `< 0.25`, `sample:` = base-tracked file, `measured: 0.31` -> finding `MEASURED_CONTRADICTS` (the 1668 shape); with `measured: 0.1` -> no finding, AC in `unverified`; with `measured` literally present in the sample -> in `verified`.
- AC2: numeric AC without `sample:` -> `NO_SAMPLE`; trivial-only AC (`>= 1`, `== 0`, `>= 5`) -> no finding; unit AC (`within 200 ms`) without sample -> `NO_SAMPLE`.
- AC3: `SAMPLE_NOT_REAL` for four cases: missing path, empty file, file added by the lot (untracked), file tracked at base but modified by the lot; a base-tracked unmodified file and an absolute outside-root file are real.
- AC4: `NO_MEASURED` with a valid sample and no `measured:`; `measured: 31%` is parsed as 31.
- AC5: RED asserts `== 0.31`, a lot-authored `fixtures/x.json` contains `0.31`, no real sample contains it -> `RED_EXPECTED_FROM_LOT_FIXTURE`; the same number present in a cited real sample -> no finding; equal to a `measured` value of a clean AC -> no finding; inside `0.310` or `10.31` (not a whole token) -> no finding.
- AC6: RED `.py` with inline fixture a single string literal of length >= 8 containing the asserted number -> `RED_EXPECTED_FROM_INLINE_FIXTURE`; asserted trivial numbers (`0`, `1`, `3`, `-1`) never flagged; a number only in an `approx(...)` call is considered; numbers outside assert/approx are ignored.
- AC7 (flag): no env -> `mode: shadow`, flagged spec exits 0 with `would_block: true`; `HAL_SPEC_SAMPLE_ENFORCE=1` -> exit 1 on the same input; `=true` / `=yes` / `=0` -> shadow; clean spec exits 0 in both modes; `--shadow-log` gets exactly one JSON line per run with `codes`.
- AC8 (expiry): `--today 2026-10-17` -> not expired; `2026-10-18` in shadow -> exit 3 `flag_expired` with findings still listed; `2026-10-18` with ENFORCE=1 -> normal verdict; JSON `flag` carries `name`, `owner` (non-empty), `expires == "2026-10-17"`, `expired`.
- AC9 (fail-closed F1-F5): non-git root, bad `--base`, missing spec, spec outside root, empty `--red`, bad `--today`, RED syntax error -> exit 2 with the exact reason; a forced internal error (unreadable spec via chmod 000 is `bad_input`; `internal_error` is asserted through the static top-level handler check in AC10).
- AC10 (static): the script imports nothing from `bytedigger_engine`, has no `subprocess` argv containing `claude`/`bun`/`.ts`, reads only `HAL_SPEC_SAMPLE_ENFORCE` from the environment, declares `FLAG_OWNER`/`FLAG_EXPIRES`, `main()` wraps its body in a catch-all returning 2, exit codes limited to {0,1,2,3}, and `flags_catalog.py` is unchanged (no registry entry).
- AC11 (side-effect): the script writes nothing under `--root` (tree hash before == after) except `--shadow-log` when it points inside root.
- AC13 (grammar): phrase forms `below 0.25`, `less than 0.25`, `0.25 > share`, `не более 0.25`, `up to 200`, `exceeds 40` each are numeric ACs (`NO_SAMPLE` without a sample; `MEASURED_CONTRADICTS` with a violating `measured`); `task 1668 shows` and `ACME: 12 apples` are not numeric/AC; AC heading and fenced-code boundary.
- AC14 (several comparisons): `count >= 1 and p95 < 200 ms`, `measured: 300` -> contradiction on `< 200` (trivial comparison ignored); two non-trivial comparisons with two measured pair in order, with one measured -> `unverified` `ambiguous_pairing`, no finding.
- AC15 (lot-authored set): lot already committed on top of `--base` -> its fixtures are not real; spec tracked at base -> exit 2 `base_contains_lot`; a `git mv`-renamed and edited fixture counts as lot-authored; `--root` a subdirectory of the repo -> exit 2 `bad_input`; missing `--base` -> exit 2 `bad_usage`; a symlinked sample is `SAMPLE_NOT_REAL`.
- AC12 (1668 end-to-end, real production side effect §1l): a repo whose base holds a real stub sample, a lot spec with `prose share < 0.25` citing it with `measured: 0.31`, plus lot-authored synthetic fixtures and a RED asserting `0.25`-calibrated values -> ENFORCE exit 1 with both (variant without any `sample:`/`measured:` -> `NO_SAMPLE`, the literal 1668 shape), `MEASURED_CONTRADICTS` and a RED circularity finding; the corrected spec (`measured: 0.1`, real sample cited, RED numbers from the real sample) -> exit 0 `clean`.

## 7. Live baseline (§1b)

No behaviour floor or timing threshold here: the numbers are definitional (token rules, the 14-day expiry). The tunables ("non-trivial = non-integer or integer >= 10", string length >= 8) are uncalibrated noise filters chosen so small counts (line numbers, `== 3`) never flag; their false-positive rate is measured in SHADOW via `--shadow-log` before any flip, which is the flip precondition .

## 8. Files NOT in scope / out of scope

`engine_py/**`, `flags_catalog.py`, `ERROR_CODES.md`, `scripts/build-gate.sh`, `commands/build.md`, `CHANGELOG.md` (S4/M1 added none either). No shared file is touched. Out of scope: wiring into the engine or gate, recomputing measurements, a waiver syntax (needed before the ENFORCE flip; flip precondition together with the shadow-log evidence), GREEN-side checks (GREEN never edits tests; shared fixtures are covered because both sides consume lot-authored fixtures), LLM semantic review of the spec.

## 9. Sibling audit (§1a)

New files only: `scripts/spec_sample_check.py`, `tests/test_s3_spec_sample_check.py`; a grep of `tests/`, `engine_py/tests/`, `packaging/` and the root manifests for collective `scripts/*` globbing finds only the per-script S4/M1 tests (`test_s4_m1_devops_scan.py`, `test_bd89_p1_devops_dropped.py`), which name `devops_scan` explicitly; no sibling needs edits.

## 10. Op <-> AC map (§1w)

parse ACs -> AC1,2,4,13,14 · sample reality / lot set -> AC3,15 · RED circularity -> AC5,6,12 · flag/shadow -> AC7 · expiry -> AC8 · fail-closed -> AC9 · isolation/side-effect -> AC10,11.
