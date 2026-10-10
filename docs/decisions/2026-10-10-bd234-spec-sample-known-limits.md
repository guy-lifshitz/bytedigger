---
red_tests:
  - tests/test_bd234_spec_sample_known_limits.py
sibling_tests:
  - tests/test_s3_spec_sample_check.py
paths:
  - scripts/spec_sample_check.py
red_pins:
  - tests/test_s3_spec_sample_check.py
---

# bd#234: spec_sample_check known limits and per-AC waiver

**Status: DRAFT r1** · **Tier:** 2 (edits one standalone script, no engine registry, no `flags_catalog` entry) · **Class:** PROCESS (a deterministic check that fails open on input forms it does not recognise; follow-up of bd#231 / bd#232, class 1668)
**Chokepoints:** `_analyze` and `_satisfies` (items 1-4, 9), `_parse_blocks` (item 5), `_check_red` and the lot-set builder in `_scan_inner` (items 6, 7), a new `_tracked_at` helper (item 8), `_check_acs` and `main()` verdict assembly (waiver).
**Provenance:** issue bd#234 (known limits listed after the first spec-sample-check lot, `2026-10-03-s3-spec-sample-check.md`). The shadow-log window for `HAL_SPEC_SAMPLE_ENFORCE` ends on `FLAG_EXPIRES` (2026-10-17); a waiver syntax was named there as a precondition of the flip.
**Principles:** (1) deterministic first: code, then review, then a model; this change adds no model call. (2) Provider-agnostic: it degrades, it does not fail; no `claude`, no API key, no `bun`. (3) Works the same on a subscription and on API tokens; tested with both environments. (4) Shadow first, then enforce: every new behaviour sits behind the existing switch, `HAL_SPEC_SAMPLE_ENFORCE` (unset or anything but `1` = SHADOW). No new environment variable.

## 1. Scope

Close the known parser and RED-check limits of `scripts/spec_sample_check.py` and add a per-AC waiver. Out of scope: wiring into the engine or the gate, flipping the flag, changing `FLAG_EXPIRES` (the flip-or-extend decision is for the manager), recomputing measurements.

Unchanged: exit codes {0,1,2,3}, flag name / owner / expiry, SHADOW unless the environment value is exactly `1`, fail-closed rules F1-F6 of the first spec, the single environment variable read, no `claude` / `bun` / `.ts` in any subprocess argv, no writes under `--root` (except `--shadow-log`), `flags_catalog.py`.

## 2. Contract additions

1. **Thousands separators.** A number is `\d{1,3}(,\d{3})+` with an optional fraction: a comma between digits, exactly three digits after each comma, and the group not followed by another digit. `1, 000` and `1,0001` are not grouped. Applies to thresholds, unit numbers and `measured:` values. In `measured:` the text `150,200` (no space) is the single value 150200; two values are written `150, 200`. The canonical text (what findings print) has the commas removed. When matching a number against samples and RED fixtures the grouped form (`1,000`) is also tried, so an asserted `1000` against a fixture `1,000` is flagged.
2. **Long phrases.** `less than or equal to` maps to `<=`, `greater than or equal to` to `>=`; leftmost-longest at each position, word boundaries on both sides. `less than 250` with `measured: 250` still contradicts.
3. **Leading-dot decimals.** `.25` and `-.25` are 0.25 and -0.25 (non-trivial). The dot must not follow a word character or another dot (`v1.25` and `x.25` are not numbers). For sample and fixture matching `.25` and `0.25` match each other in both directions.
4. **Bare `=`.** `= N` is `==` when the `=` is preceded by whitespace or the start of the block text and is not part of `==`, `<=`, `>=`, `!=`, `=>`. The reversed form `N = identifier` follows the same rule. `timeout=200` (no preceding whitespace) is deliberately not a comparison.
5. **Fences.** A fence opens on a stripped line starting with three or more backticks or three or more tildes (indent unlimited, as before). It closes only on the same character, at least the opener's length, with only whitespace after it. A backtick fence inside a tilde fence (and the reverse) does not close it; an info string (```` ```python ````) on a line inside an open fence does not close it; an unclosed fence runs to the end of the file. What a fence does is unchanged and now applies to both fence kinds alike: lines inside a fence are never treated as AC lines or headings, but they stay part of the enclosing AC block and are parsed for thresholds, `sample:` and `measured:` like any other line of the block (so a `#` line inside a fence does not end the block).
6. **RED inline fixtures.** Besides string literals, bytes literals (decoded as UTF-8 with `errors=replace`) and f-strings (the static parts joined, each replacement field standing for one `\x00`, length >= 8 on the joined text) count as inline fixtures. The code stays `RED_EXPECTED_FROM_INLINE_FIXTURE`; the detail says `inline string literal` (unchanged), `inline bytes literal` or `inline f-string literal`. A number cannot fuse across a replacement field.
7. **Gitignored lot fixtures.** The lot-authored set also includes `git ls-files --others --ignored --exclude-standard -z`, except paths with a component in `node_modules`, `__pycache__`, `.venv`, `venv`, `.tox`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`. A cited sample that is ignored and lot-authored is `SAMPLE_NOT_REAL`.
8. **git errors are not "untracked".** One helper `_tracked_at(root, base, rel)` answers "is `rel` a regular blob at `base`": True or False. Any git failure (a nonzero status that does not mean "absent", unparseable output) becomes the existing fail-closed `git_failed` (exit 2, `unavailable`). It replaces the two `cat-file -e` call sites (spec tracked at base; cited sample tracked at base). A genuinely absent path still yields `SAMPLE_NOT_REAL: untracked at base`. Suggested implementation: `git --literal-pathspecs ls-tree -z <base> -- <rel>` (nonzero = failure, empty = absent), because `cat-file -e base:missing` exits 128 and cannot tell absent from broken. A failure listing ignored files is also `git_failed`. No new reason value.
9. **Percent.** Each threshold and each measured value carries a percent flag. Same flag on both or neither: compare raw. Exactly one percent: if the plain side has `|value| <= 1`, the percent side is divided by 100, else compare raw. Examples: `<25%` vs measured `0.31` contradicts; vs `0.1` holds; `<0.25` vs `31%` contradicts; `<40` vs `45%` contradicts; vs `35%` holds; `<25%` vs plain `31` contradicts. For the RED ok-set a percent value contributes both its raw and its /100 form. Triviality is unchanged (as written).
10. **Waiver.** A dedicated line inside an AC block, not inside a fence, at line start (after an optional list marker and `**`):

    `waive: CODE[, CODE...] reason: <text>`

    Keywords are case-insensitive; the separator before `reason:` is whitespace, `;` or `,`. The line is removed from the block before parsing (numbers in the reason create no threshold).
    - Waivable codes: `NO_SAMPLE`, `SAMPLE_NOT_REAL`, `NO_MEASURED`, `MEASURED_CONTRADICTS`, `AMBIGUOUS_PAIRING`, only for the AC that carries the line. `RED_*` and any wildcard are not waivable.
    - The reason is mandatory, at least 8 characters trimmed; otherwise finding `WAIVER_NO_REASON` (`ac` = the AC id) and the line waives nothing.
    - An empty, unknown, non-waivable or lowercase code gives `WAIVER_INVALID` and the whole line is rejected.
    - `WAIVER_*` findings are blocking and not waivable.
    - Waived findings move to a new verdict key `waived`: a list of `{code, ac, detail, reason}`. `would_block` and `status` look at `findings` only, so a spec whose only findings are waived is `clean`.
    - A waived AC is still not clean: it calibrates nothing in the RED ok-set and is in neither `verified` nor `unverified`.
    - An unused waiver is silent. A waiver mid-line, inside a fence, in prose backticks or outside any AC is not a waiver (the finding stays; no `WAIVER_*` for a non-line-start mention).
    - The shadow-log record gains `waived` (the list of waived codes). Verdict keys become `{status, mode, would_block, findings, waived, verified, unverified, flag, reason}`; `unavailable` carries `waived: []`.

## 3. Acceptance criteria (RED file `tests/test_bd234_spec_sample_known_limits.py`)

Hermetic, like the first lot's tests: git repos under `tmp_path`, `--base <sha>`, `sys.executable` subprocess, environment built from scratch, `--today 2026-10-01`; the helpers are copied, the earlier test module is not imported. Each limit is tested with a recognised form and a near miss.

- AC1 (thousands): `< 1,000 ms` with `measured: 1,250` contradicts; the finding prints `1000`; `1, 000` and `1,0001` are not grouped; `measured: 150,200` is one value, `150, 200` is two; an asserted `1000` against a fixture containing `1,000` is flagged.
- AC2 (phrases): `less than or equal to N` and `greater than or equal to N` are `<=` / `>=` (a measured equal to the bound holds); `less than 250` with `measured: 250` contradicts.
- AC3 (leading dot): `< .25` with `measured: 0.31` contradicts; `-.25` parses; `v1.25` and `x.25` are not thresholds; a fixture `0.25` matches an asserted `.25` and the reverse.
- AC4 (bare `=`): `p95 = 200 ms` and `200 = p95` are numeric comparisons; `timeout=200` is not; `==` and `<=` stay a single comparison each; `!=` is not a comparison.
- AC5 (fences): a `~~~` fence hides an AC line exactly as a backtick fence does; a threshold inside a fence under a real AC line still counts, for both fence kinds (parity); a `#` line inside either fence kind does not end the block; a backtick fence inside a tilde fence does not close it (and the reverse); ```` ```python ```` inside an open fence does not close it; a longer closer closes, a shorter one does not; an unclosed fence runs to the end.
- AC6 (RED literals): a number asserted in a RED file and present in a bytes literal or an f-string static part (length >= 8) is flagged with the matching detail; a short literal (< 8) is not; `f"{a}5{b}"` style fusion across a replacement field is not flagged.
- AC7 (gitignored fixtures): with `.gitignore` holding `fixtures/`, an ignored lot fixture counts as lot-authored (RED circularity flagged); a file under an ignored `node_modules` is not counted; a cited ignored sample is `SAMPLE_NOT_REAL`.
- AC8 (git errors): a `git` wrapper first on `PATH`, delegating to the real git (found with `shutil.which`) and exiting 128 for the subcommands `cat-file`, `ls-tree`, `show` when any argument contains the probe path. (a) probe = the spec path: exit 2 `git_failed`. (b) probe = `samples/plain.txt`: exit 2 `git_failed` and no `SAMPLE_NOT_REAL`. (c) a passive wrapper: normal verdict. (d) the absent path `samples/nope.txt`: `SAMPLE_NOT_REAL`, `untracked at base`. (e) a wrapper that exits 128 whenever the argument list contains `--ignored`: exit 2 `git_failed` (item 7 listing failure).
- AC9 (percent): the six examples of item 9, both directions; and the RED ok-set rule: a RED file asserting `31` with a lot fixture containing `31` is not flagged when a clean AC has `measured: 31%`, and one asserting `0.31` is not flagged for the same measured value (raw and /100 forms), while an asserted `0.32` with the same fixture shape is still flagged.
- AC10 (waiver applies): a valid waiver moves each waivable code to `waived` with its reason; the verdict is `clean`; the waived AC is in neither `verified` nor `unverified` and calibrates nothing in the RED ok-set.
- AC11 (waiver scope): a waiver covers only its own AC and only the listed codes; `RED_*` and wildcards give `WAIVER_INVALID`; unused waiver is silent.
- AC12 (waiver reason): missing reason or reason under 8 characters gives `WAIVER_NO_REASON` and waives nothing; empty, unknown or lowercase code gives `WAIVER_INVALID`, whole line rejected; numbers inside a reason create no threshold.
- AC13 (waiver placement): mid-line, inside a fence, inside prose backticks, and outside any AC are not waivers and do not raise `WAIVER_*`; keywords are case-insensitive, a list marker or `**` before the line is accepted.
- AC14 (verdict keys): exact key sets for clean, flagged, `unavailable` (`waived: []`) and `flag_expired`; the shadow-log record has `{ts, spec, mode, would_block, codes, waived}`.
- AC15 (flag untouched): `FLAG_EXPIRES == "2026-10-17"`, `flags_catalog.py` unchanged, SHADOW flagged exits 0, ENFORCE flagged exits 1, the script reads only `HAL_SPEC_SAMPLE_ENFORCE`.
- AC16 (end to end): `p95 less than or equal to 1,000 ms` with a real sample containing `1,250` and `measured: 1,250`: ENFORCE exit 1; with a valid waiver exit 0 and `waived` set; with the threshold corrected: clean.
- AC17 (provider and billing independence): environment A (no `ANTHROPIC_*`) against environment B (a dummy `ANTHROPIC_API_KEY` and an `ANTHROPIC_BASE_URL` on a closed port), stub `claude` and `bun` on `PATH` writing markers: identical stdout and exit code in SHADOW and in ENFORCE, no marker written.

## 4. Live baseline (§1b)

Corpus baseline taken before the change over the 98 historic specs in `docs/decisions` (gate, manager, escalation and post-mortem notes excluded; base = parent of the commit that added the spec; placeholder RED file): 98 runs, 0 unavailable, 19 would-block; codes `NO_SAMPLE` 25, `MEASURED_CONTRADICTS` 2, `AMBIGUOUS_PAIRING` 1, `SAMPLE_NOT_REAL` 1. This is a count, not a false-positive rate. After GREEN the same corpus is rerun and each changed AC is inspected by hand (true gap or false positive); the numbers go in the PR.

## 5. Files

In scope: `scripts/spec_sample_check.py`; new `tests/test_bd234_spec_sample_known_limits.py`; two key-set assertions in `tests/test_s3_spec_sample_check.py` (`VERDICT_KEYS` gains `waived`; the shadow-log record keys gain `waived`); this spec and its gate rounds under `docs/decisions/`; `CHANGELOG.md` `[Unreleased]`. Out of scope: `engine_py/**`, `flags_catalog.py`, `scripts/build-gate.sh`, `commands/**`, any wiring, flipping the flag.

## 6. Sibling audit (§1a)

A grep of `spec_sample_check` and `HAL_SPEC_SAMPLE` across `tests/`, `engine_py/`, `scripts/`, `packaging/`, `commands/`, `hooks/`, `.github/` finds only the script, the first lot's spec / gate notes and `tests/test_s3_spec_sample_check.py`. No other consumer of the verdict keys exists, so adding `waived` breaks nothing besides the two assertions above.

## 7. Op <-> AC map (§1w)

number grammar (thousands, phrases, leading dot, bare `=`, percent) -> AC1, 2, 3, 4, 9 · fences -> AC5 · RED literal forms and lot set -> AC6, 7 · git failure handling -> AC8 · waiver -> AC10, 11, 12, 13 · verdict and shadow-log shape -> AC14 · flag untouched -> AC15 · whole path -> AC16 · provider independence -> AC17.

## 8. Remaining known limits

Unspaced `p95=200`; `1_000` and exponent forms; fixtures built by concatenation or derived at run time; assert messages and docstrings counted as inline literals; an invented `measured:` for an unrelated real file; `150,200` without a space in `measured:` read as one number; whether a waiver reason is true cannot be audited by the script (it is only made visible in the report).
