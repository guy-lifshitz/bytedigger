# bd#87 (P6) — cite-lint fails closed when it is blind, and reads what a spec introduces

**Status: FROZEN** · **Class:** SYSTEMATIC · **Chokepoint:** `spec_cite.lint_spec`
(`engine_py/bytedigger_engine/spec_cite.py:519`), the only place that turns citation findings
into an exit code.

## §1 Problem (measured on `3b643fc`)

`lint_spec` fails open in two ways, and fails closed where it should not:

1. **Blind = green.** `_iter_scannable_lines` (`spec_cite.py:139`) drops every line inside a
   non-python fence. A spec the writer wrapped whole in a markdown fence therefore yields
   `scan_citations(...) == []`, and `lint_spec` returns `(0, [])`. The same holds for any
   non-empty spec that cites nothing: zero evidence reads as "clean".
2. **Invented path = green.** `check_citation` returns `missing_file` for a cited file that does
   not exist and was not declared a CREATE target (`spec_cite.py:486`), and `lint_spec` counts
   only `unresolved_symbol` toward the exit code (`spec_cite.py:551`). A citation of a real
   symbol against a fabricated path passes.
3. **Declared new symbol = red.** The only "this symbol is new" signals are prose heuristics
   (`_NEW_CONTEXT_RE`, declaration-position lines). A spec that lists the symbols it introduces
   in a dedicated section is not read at all, so a citation of a not-yet-existing symbol on a
   line without a create/add/new verb is `unresolved_symbol` → `E_SPEC_CITE_LINT_FAIL`
   (the upstream HAL #1893 failure, lots 1878/1896).

## §2 Design

### op1 — declarative introduced-symbol allowlist (ported from the HAL #1893 frozen design)

`declared_introduced_symbols(spec_text) -> set[str]`, same name and contract as the HAL fix so
the two codebases converge:

- **Line form** `_INTRODUCES_LINE_RE`: optional `-`/`*` bullet, literal `INTRODUCES:` at line
  start, then the rest of the line.
- **Section form** `_INTRODUCES_HEADING_RE`: `(?i)^#{1,6}\s*Symbols this spec INTRODUCES`; the
  body runs to the next line starting with `#`. Only bullet lines (`^\s*[-*]\s`) of the body are
  declarations, and a body line containing any `_CODE_FILE_RE` match is discarded whole: a line
  with a citation is never a declaration (otherwise a last-in-document section would allowlist
  a typo cited anywhere below it).
- Tokens come **only** from backticks. A token is accepted if `_is_valid_symbol` (normalized via
  `removesuffix("()")`), else the leading identifier if `_SIG_PREFIX_RE` matches.
- Lines are walked through `_iter_scannable_lines`, so neither form works inside a non-python
  fence (one fence rule per module).

### op2 — wiring in `lint_spec`

One more disjunct in the existing downgrade condition:
`f.symbol.removesuffix("()") in introduced`. No new downgrade status: `new_symbol` is reused,
and like the existing disjuncts it downgrades `wrong_file` too.

### op3 — blindness and invented paths block

- `BLOCKING_STATUSES = frozenset({"unresolved_symbol", "missing_file", "no_citations"})`,
  public, and `lint_spec`'s exit code is `1` iff any finding's status is in it.
- **Blindness** appends `Finding(file="", symbol="", status="no_citations")`. The lint is blind
  when the spec text is not blank, it produced no (file, symbol) citation, and no scannable line
  (per `_iter_scannable_lines`) carries an anchored citation `<path>:<line>` or
  `<path>:"snippet"` — nothing in the spec is checkable by any gate. Anchored citations are not
  blind: they belong to the citation verifier, and rule 5 of the writer contract steers toward
  them. A bare path mention ("modify `x.py`") checks nothing and does not count (review finding
  after GREEN, AC21). A source file in a language this lint does not index (`.go`, `.rs`,
  `.java`, ...) makes the spec out of reach, not blind (AC23): the lint cannot check it, and
  failing every spec of a Go project would be a false positive, not fail-closed.
- `scan_citations` drops `_CODE_FILE_RE` tokens that are not files: a capitalized,
  directory-less `X.js` product name (`Node.js`) and URL tails starting with `//` (AC24).
  Harmless while `missing_file` was advisory; blocking now.
- `declared_created_files` walks `_iter_scannable_lines`: a `CREATE:` line quoted in a
  non-python fence declares nothing (AC25). A blank spec is not this gate's concern (spec completeness owns
  it).
- `missing_file` becomes blocking. A file is exempt only when declared: a `CREATE:` line or the
  body of a "Files this spec CREATES" section (both already parsed by
  `declared_created_files`), which yields the advisory `planned_file`.
- **`_CREATE_LINE_RE` accepts the colon-less form** `CREATE \`path.py\``: case-sensitive
  `CREATE` at line start followed by `(?::\s*|\s+)` (so `CREATE:path.py` keeps working), then the
  path. That is the shape real
  specs use (the GH382 B4 corpus mirrors a live spec), and it is still a line-anchored
  declaration, not prose inference. A line like `CREATE \`sym\` in \`a/c.py\`` (symbol first)
  is not a declaration.

### op4 — consumers in `phase_45_spec.py`

- `_parse_cite_unresolved` is renamed `_parse_cite_blocking` and keeps every finding whose status is in `BLOCKING_STATUSES` (imported,
  not re-listed), so the rc==1 branch gets non-empty findings and directed repair runs for the
  new failure kinds too, instead of "(no unresolved citation parsed)".
- New pure helper `_cite_finding_evidence(finding)` renders the evidence line per status
  (`unresolved_symbol` keeps its current text; `missing_file` names the file and says it does
  not exist and is not declared by a CREATE line; `no_citations` says the lint found no
  citations to check; any other status is named as such, never called "unresolved"). Used by
  both rc==1 consumers (`_verify_spec_cite_lint` and
  `_collect_spec_gate_findings`), replacing their inline strings. Both consumers keep one finding
  per distinct evidence line; `missing_file` evidence names only the file, so N symbols cited
  against one invented path are one finding (AC27).
- `_emit_cite_status_telemetry` gains a `no_citations` count.
- `_collect_spec_gate_findings`: rc==1 with no parseable blocking finding still yields one
  `E_SPEC_CITE_LINT_FAIL` finding (before this lot it yielded none and the batch passed;
  `_verify_spec_cite_lint` already failed in that case).
- `_grounded_citation_contract` gains rule 10 (the example lines are written bare, exactly as the
  parsers read them — AC26): introduced symbols are listed as bullets under
  `## Symbols this spec INTRODUCES` or on an `INTRODUCES: \`sym\`` line; files that do not exist
  yet are declared with `CREATE: path`. The allowlist is the enforcement; the prompt is the hint.

### Out of scope (owned elsewhere)

- **Fence stripping** — the output normalizer is P3 (#84). This lot does not unwrap fences; the
  zero-citation rule makes a fenced spec fail closed until the normalizer lands, and it keeps
  failing closed if the normalizer misses a shape.
- **Recoverability** of `E_SPEC_CITE_LINT_FAIL` and the terminal `driver_missing` branch — P4
  (#85); the driver location itself — #81. The `_verify_spec_cite_lint` rc dispatch is unchanged.
- `_inphase_unresolved_symbols` (the warn-only symbol re-prompt) keeps listing symbols only; its
  directive is about symbols.
- HAL's op3 (`_verify_spec_citations` exempting `CREATE:` targets) is not part of P6.

## §3 DesignReview (condensed; dimensions without surface here marked n/a)

| dimension | score | note |
|---|---|---|
| clarity / component design | 4 | One chokepoint; blocking set named once and imported by the consumer. |
| external integrations | n/a | pure library + JSON contract of the CLI driver (driver absent, #81). |
| security | n/a | |
| performance / resilience | 5 | No new I/O; the repo index is still built once per call. |
| data management | n/a | |
| maintainability | 4 | Same function names as HAL #1893, so the port back is a diff of one file. |
| risks | 3 | Tightening: specs that cite a basename (`spec_cite.py` instead of the repo path) now fail. This is intended (an unresolvable path is exactly the invented-path hole), but it will raise first-run failures until P4 makes the gate recoverable. The colon-less CREATE widening lowers the risk for new files. |
| readability | 4 | |

**Forks decided:** (a) zero citations is a finding, not a separate return value — the CLI JSON
and every existing consumer already speak findings; (b) colon-less `CREATE` accepted rather than
amending the B4 corpus, since B4 mirrors a live spec; (c) GH382 B2 is amended: its line
`CREATE \`shared_sym\` in \`a/c.py\`` does not declare `a/c.py`, and under this lot an
undeclared missing file blocks. B2's subject (the cross-line planned set) is preserved by
declaring the file (`CREATE \`a/c.py\` ...`) and accepting `planned_file` in its assertion.
**Verdict: APPROVE WITH CONDITIONS** — the amendment to B2 is called out in the PR.

**Accepted risks (recorded by the gate, not fixed here):**
- A spec that references only non-code files (`.md`, `.json`, `.yaml`) is blind by this
  definition and fails. Such a spec has nothing for this lint to verify; failing loudly is the
  intended trade.
- A "Files this spec CREATES" section placed last runs to EOF, so a citation line in that
  section's body still exempts its path from `missing_file` (the fence case is fixed, AC25). Narrowing that parser changes GH631 semantics and
  is left to a follow-up; HAL #1893 op3 already avoids it for its own exemption.
- An invented path mentioned without a symbol or an anchor is not looked up (only
  (file, symbol) pairs produce `missing_file`). Checking every bare path mention would flag
  basenames and paths relative to other roots across ordinary prose; left to a follow-up.
- The INTRODUCES allowlist is spec-wide, not per-file, as in the upstream design: a declared
  symbol cited against an existing file is downgraded there too. `new_marked_symbols` already
  has the same scope.
- The `INTRODUCES:` line form reads every backtick symbol on the line, even with a file path on
  it; the keyword makes the declaration explicit (as upstream), unlike a section body line.
- A `#` comment inside a python fence in the INTRODUCES section body ends the section early
  (fail-closed direction: later bullets are not allowlisted).
- `_inphase_unresolved_symbols` (the warn-only re-prompt) still lists only unresolved symbols.
- Blindness is all-or-nothing: one prose line outside a fence that names a code file (a
  preamble in front of a fenced spec body) makes the spec "not blind", and the fenced body is
  still unchecked. Unwrapping the fence is the P3 normalizer's job (#84); this rule only
  guarantees a spec with no visible code reference cannot pass.

## §4 Acceptance (RED: `engine_py/tests/test_bd87_cite_fail_closed.py`)

Every `lint_spec` AC uses a hermetic fixture repo under `tmp_path` (one `mod.py` with
`def existing_helper`), never the real checkout: `_repo_symbol_index` walks the whole
`repo_root`, and a symbol found anywhere becomes `wrong_file`, not `unresolved_symbol`.

| AC | assertion | op | pre-GREEN |
|---|---|---|---|
| AC1 | section `## Symbols this spec INTRODUCES` bullet `JOB_STATUS_COMPLETED` + a citation of it against `mod.py` → exit 0, `new_symbol` | op1/op2 | FAIL |
| AC2 | same spec plus a typo'd existing symbol cited after the next heading → exit 1, typo `unresolved_symbol`, declared symbol still `new_symbol` | op2 | FAIL |
| AC3 | line form `- INTRODUCES: \`SOME_NEW_FLAG\`` → exit 0 | op1 | FAIL |
| AC4 | line form inside a markdown fence → not in the allowlist, exit 1 | op1 | FAIL (AttributeError) |
| AC4b | section heading + bullet inside a markdown fence → not in the allowlist | op1 | FAIL (AttributeError) |
| AC5 | section last in the document; a typo citation bullet in its body → not allowlisted, exit 1 | op1 | FAIL (AttributeError) |
| AC6 | signature form `INTRODUCES: \`make_job(cfg)\`` → `make_job` allowlisted, exit 0 | op1 | FAIL |
| AC7 | whole spec inside a markdown fence (citations only in the fence) → exit 1, a `no_citations` finding | op3 | FAIL |
| AC8 | non-empty prose spec with no citation → exit 1, `no_citations` | op3 | FAIL |
| AC9 | whitespace-only spec → exit 0, no findings | op3 | PASS (pin) |
| AC10 | real symbol cited against an undeclared, non-existent path → exit 1, `missing_file` | op3 | FAIL |
| AC11 | same citation, path declared by `CREATE: pkg/new_mod.py` and by `CREATE:pkg/new_mod.py` → exit 0, `planned_file` | op3 | PASS (pin) |
| AC12 | colon-less `CREATE \`pkg/new_mod.py\` with \`new_fn\`` → exit 0, `planned_file` | op3 | FAIL |
| AC13 | symbol-first line `CREATE \`new_fn\` in \`pkg/new_mod.py\`` does not declare the file → exit 1, `missing_file` | op3 | FAIL |
| AC13b | lowercase prose `Create \`pkg/new_mod.py\` with \`new_fn\`` is not a declaration → exit 1, `missing_file` | op3 | FAIL |
| AC14 | `BLOCKING_STATUSES == {"unresolved_symbol","missing_file","no_citations"}` | op3 | FAIL |
| AC15 | `_parse_cite_blocking` on JSON with one finding of each status keeps exactly the three blocking ones | op4 | FAIL |
| AC16 | `_cite_finding_evidence`: `no_citations` text mentions "no citations"; `missing_file` names the file and "CREATE"; `unresolved_symbol` keeps "unresolved citation: symbol" | op4 | FAIL |
| AC17 | `_grounded_citation_contract()` contains "Symbols this spec INTRODUCES", "INTRODUCES:" and still rule 9 | op4 | FAIL |
| AC18 | `_collect_spec_gate_findings` with `bounded_run` stubbed (cite rc=1, `no_citations` JSON) → its spec-cite-lint finding's evidence mentions no citations | op4 | FAIL |
| AC19 | `_verify_spec_cite_lint` with the driver present and rc=1 `missing_file` JSON → `attempt_directed_repair` is called with a non-empty finding naming the file, the error names the file, and the telemetry payload has `no_citations` | op4 | FAIL |
| AC21 | a spec whose only code references are a bare path mention and a `CREATE:` line → exit 1, `no_citations` | op3 | FAIL (added after review) |
| AC22 | preflight with cite rc=1 and unparseable stdout → one `E_SPEC_CITE_LINT_FAIL` finding | op4 | FAIL (added after review) |
| AC16b | `_cite_finding_evidence` on an unknown status names it and does not say "unresolved" | op4 | FAIL (added after review) |
| AC23 | `Change \`Handler\` in \`server/main.go\`` → exit 0, no findings | op3 | FAIL (added after review) |
| AC24 | `Node.js` and `https://example.com/app.js` on citation lines are not cited files | op3 | FAIL (added after review) |
| AC25 | a `CREATE:` line inside a markdown fence does not declare the path → `missing_file` | op3 | FAIL (added after review) |
| AC26 | rule 10's example lines appear bare and parse as declarations | op4 | FAIL (added after review) |
| AC27 | three symbols cited against one missing file → one preflight finding | op4 | FAIL (added after review) |
| AC20 | snippet-only spec (`mod.py:"def existing_helper"`, no backtick symbol) → exit 0, no findings | op3 | PASS (pin) |

Siblings run in full after GREEN: every `engine_py/tests` file that mentions `cite`
(probe on `3b643fc` with op3 applied: 858 passed, 2 failed — GH382 B2 and B4, handled by §2 op3
and fork (c)). The B2 amendment is committed with the RED, before GREEN.
