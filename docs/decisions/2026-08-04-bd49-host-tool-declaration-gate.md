# bd#49 — a derived declaration gate on the host tool

**Class:** an enumerative allowlist passing itself off as a policy.
**Chokepoint:** `engine_py/tests/helpers/host_tools.py` (`HOST_TOOLS`, `skip_without`,
`pytest_runtest_makereport`) + the new derived gate
`engine_py/tests/test_bd49_host_tool_declaration_gate.py`.
The enumerative layer being displaced: `_C4_BUN_CALL_SITES` / `_C5_SEMGREP_CALL_SITES` /
`_C6_DOCTOR_GIT_CALL_SITES` in `test_bd102_host_tool_contract.py`.

---

## §0. The measurement that rewrote the issue's assignment

Issue bd#49 proposes the enforcement layer verbatim as: "scan test bodies for
`shutil.which("<tool>")` for `tool ∈ HOST_TOOLS` and require of each such body
EITHER `skip_without` OR a declared refusal marker".

**Live measurement (AST, transitive closure of calls within the file, corpus at `b95e48a`):**

| set | number of `test_*` bodies |
|---|---|
| reach `which(<host tool>)` — the domain of the proposed gate | **37** |
| reach a host tool by an argv literal (`subprocess.run(["git", …])`) with **not a single** `which` | **610** (79 files) |
| both traits | 28 (included in the 37) |
| `which` only | 9 |

**A gate keyed on `which` sees 37 of 647 reachable bodies — 5.7%.** If one counts
it as the enforcement layer of the policy "no tool ⇒ declare yourself", it is exactly the class
the issue indicts: the set of the obliged is defined by a trait that most calling
sites simply do not contain. The exemption is silent once again.

**But the 610 are not a hole, and here is why.** `host_tools.py::pytest_runtest_makereport` is already
**total** over that set: any test that fails with `FileNotFoundError` whose
`basename(filename) ∈ HOST_TOOLS` under a frozen unavailability is converted to
`skipped` regardless of how it invoked the tool. `docs/host-requirements.md`
records the same by number: "without git ~500 tests raise `FileNotFoundError('git')`"
and they become skips automatically, requiring not a single line of declaration.

⇒ **The gate's domain is not "everyone who touches the tool" but "everyone who INTERCEPTS
the automatic conversion by probing availability themselves".** A pre-emptive probe is
the only way out from under the total mechanism: a body that called `which` and
took a decision before the `FileNotFoundError` could fly is invisible to the mechanism.
There are exactly **37** such bodies, and that is the **complete** set of escapes, not a sample.

The statement of the policy the gate enforces is therefore this:

> A test body that **probes for itself** the availability of a host tool must
> declare what it does with the answer: either a sanctioned `skip_without(tool)`
> or an explicit refusal marker with an argument. There is no silent third path.

## §1. What the measurement found beyond the issue: not two orders but five

The issue names two orders. In the corpus there are **five**, and three of them the issue does not see:

| # | form | where | frozen map? |
|---|---|---|---|
| 1 | `skip_without(tool)` | 9 sites, `host_tools.py` | **yes** |
| 2 | a hard `assert which(...) is not None` with an argument | `test_gh1338_corpus_parity_gate.py::_build_parity_fixture` L107 | no (deliberately) |
| 3 | an inline `if which("git") is None: pytest.skip(...)` | `test_GH294_constitution_worktree_telemetry.py` L91 | **no — a live `which`** |
| 4 | a local helper `_skip_if_no_git()` | `test_BF7890C8_collect_probe_parent_venv.py` L61 | **no — a live `which`** |
| 5 | `@pytest.mark.skipif(not _has_git(), …)` | `test_phase_45_spec_D02C615D.py` (12), `test_phase_45_spec_DA5330E9_suffix_map.py` (12) | **no — a live `which`, at import** |

Forms 3/4/5 are **not a stylistic assortment but a violation of a documented
invariant**. `host_tools.py` states in its own docstring: "Neither this hook nor
`skip_without` may call `shutil.which` live afterward: two tests monkeypatch
`shutil.which` process-wide, which would otherwise make a live lookup report a tool
absent on a machine that has it". Forms 3/4/5 do exactly a live `which` — that is,
they are **forgeable by the very monkeypatch** for whose sake the freeze was introduced.
Form 5 is worse than the rest: `skipif` is evaluated at module import, i.e. before
`pytest_configure`, and therefore cannot consult the frozen map in
principle.

This is a defect in its own right, not cosmetics, and it is fixed by this same PR (M2).

## §2. A correction to the issue's table

The issue's line "files in `_C4_BUN_CALL_SITES` — **5**, listed as `test_doctor.py`,
`test_doctor_v2.py`, `test_gh595_…`, `test_phase_5_step2_…`, `test_phase_5_step3_…`"
is **wrong**. Measured from the source of `test_bd102_host_tool_contract.py:353-366`:
`_C4_BUN_CALL_SITES` has **2** entries (`test_phase_5_step2_verify_red.py`,
`test_phase_5_step3_verify_green.py`); `_C5_SEMGREP_CALL_SITES` has 1;
`_C6_DOCTOR_GIT_CALL_SITES` has 5. In total 8 sites across three constants. In the issue five files
belonging to three different constants are collapsed into one. The substance of the issue is unchanged by the
correction — the enumerativeness remains — but the figure "5 bun files" is not to be repeated in reports.

## §3. §1b — the live base

Taken by me at `b95e48a`, worktree `bytedigger-wt/lot-bd49`, from the `engine_py` directory,
after `rm -rf build engine_py/build` and wiping `__pycache__`:

```
python3 -m pytest tests/ -q -p no:cacheprovider --timeout=120
```

The base result: **`4467 passed / 39 skipped / 0 failed`** in 274 s.

## §4. §1a — sibling audit

The files M2 edits, and the tests that read them as data:

- `test_bd102_host_tool_contract.py::test_ac5b_m2_call_sites_wired_per_test_function_body`
  reads the sources of the 8 enumerated files and requires `skip_without` in the body. M2 touches not one
  of those 8 ⇒ AC5b must stay green without an edit. Checked explicitly (AC9).
- `test_ac8_host_requirements_doc_names_git_identity_zsh_bun_semgrep` reads
  `docs/host-requirements.md`. M3 appends a section to that doc ⇒ run it targeted.
- `test_ac1_host_tools_key_set_is_git_bun_semgrep_and_excludes_zsh` pins the keys of
  `HOST_TOOLS`. M1/M2/M3 do not change the keys.

## §5. Scope

**New files**
- `engine_py/tests/test_bd49_host_tool_declaration_gate.py` — the gate (M1, RED).
- `engine_py/tests/helpers/host_tool_probes.py` — the scanner (M1, GREEN). A separate
  module rather than a test body: the negative legs are fed source TEXT, so the
  scanner must be callable with a `str` (§1aa). `host_tools.py` is meanwhile not
  touched (§1v) — the new module is built alongside it.

**Edited (M2 — corpus conformance)**
- `engine_py/tests/test_GH294_constitution_worktree_telemetry.py` (form 3 → 1)
- `engine_py/tests/test_BF7890C8_collect_probe_parent_venv.py` (form 4 → 1)
- `engine_py/tests/test_phase_45_spec_D02C615D.py` (form 5 → 1)
- `engine_py/tests/test_phase_45_spec_DA5330E9_suffix_map.py` (form 5 → 1)
- `engine_py/tests/test_gh1338_corpus_parity_gate.py` (form 2 → form 2 **declared**:
  one marker comment, behaviour unchanged)
- `engine_py/tests/test_bd102_host_tool_contract.py::test_ac6` — reaches `git` via
  `helpers.git_repo.init_repo`; check whether it falls in the domain, and declare it if so.

**Edited (M3 — docs)**
- `docs/host-requirements.md` — a section about the two declarations and about the gate's domain.

**§1v — NOT in scope**
- `engine_py/tests/helpers/host_tools.py` — the mechanism is not changed by a single line.
  The gate is built on top; the freeze/hook wrapper stay as they are.
- The `_C4/_C5/_C6` constants are **not deleted** by this PR. They enforce a stronger
  property on their 8 sites (the form must be `skip_without` specifically), which the derived
  gate does not cover. Displacing them is a separate decision for the dispatcher.
- `test_gh1338_…` is **not rewritten** under `skip_without` (the issue's argument is accepted:
  a silent skip would certify corpus parity without ever having run it).
- Product code `bytedigger_engine/**` — zero edits.

## §6. Acceptance criteria

The gate lives in `test_bd49_host_tool_declaration_gate.py`. The scanner is extracted as a named
function (§1aa) `undeclared_host_tool_probes(source: str) -> list[tuple[str, str]]`,
taking **source text** rather than a path — otherwise there is nothing to feed the negative leg.

- **AC1 (§1l, anchored to a real side effect).** Run over the real directory
  `engine_py/tests/**/test_*.py`, the gate returns an **empty** list of undeclared
  probes. This is a claim about the live corpus, not about a fixture.
- **AC2 (positive leg, the `skip_without` form).** A source with a body calling
  `which("bun")` and `skip_without("bun")` yields an empty list.
- **AC3 (positive leg, the refusal marker).** A source with a body calling
  `which("bun")` and carrying the comment `# host-tool-hard-fail: <argument>` yields an empty
  list.
- **AC4 (NEGATIVE LEG — a gate that does not fail on a new site is inert).**
  A source with a body calling `which("bun")` and carrying **neither** of the two forms
  is returned by the scanner as exactly one entry `(func, "bun")`. Without this AC the gate is
  decoration.
- **AC5 (negative leg, transitive).** The same, but `which("bun")` is hidden in the
  helper `_fixture()` that the test body calls: the entry is returned all the same.
  A direct-only scanner would today miss **8 ACs of `gh1338`** — precisely the ones
  the issue names as the subject.
- **AC6 (the marker must carry an argument).** `# host-tool-hard-fail:` with an empty tail
  does **not count** as a declaration — the entry is returned. An empty marker = a silent
  exemption in the guise of a declaration, i.e. the original class.
- **AC7 (a declaration does not leak between bodies).** Two bodies in one file: the first
  declared, the second not ⇒ exactly one entry, and it is the second. Catches a scanner that looks for the
  marker across the whole file instead of the body.
- **AC8 (the declaration is bound to the tool).** A body calls `which("bun")` but carries
  `skip_without("git")` ⇒ the entry is returned. Catches a scanner that checks the fact of the call
  instead of the argument.
- **AC9 (§1a, sibling).** `test_bd102_host_tool_contract.py::test_ac5b_…` passes at
  the branch head without edits — M2 disturbed none of the 8 enumerated sites.
- **AC10 (aliases).** `import shutil as _sh; _sh.which("bun")` and
  `from shutil import which; which("bun")` are both recognised. Three aliases live in the corpus
  (`_shutil`, `_sh`, `_shutil_real`) — the scanner must key on the attribute/name
  itself, not on the module.
- **AC11 (the gate's boundary of honesty).** The scanner is **not** obliged to catch pre-emption done
  otherwise than through `which` (for example `os.path.exists("/usr/bin/git")` or a custom
  `except FileNotFoundError: pytest.skip(...)`). Measured: there are **zero** such cases in the corpus
  (`except FileNotFoundError` next to a `skip` — 0 hits; `exists()` over binary paths
  — 0). The gate must **name** this boundary in its docstring rather than pass over it in silence.

## §7. What the PR does NOT claim

- It does not claim the corpus as a whole is inert-safe in the absence of a tool:
  totality is provided by the hook wrapper; the gate closes only the escapes from under it.
- It does not claim that `_C4/_C5/_C6` are no longer needed — see §1v.
- It does not claim that form 2 (hard failure) is wrong. It remains legitimate,
  but becomes declared.

## §8. State as of the gate's verdict

- **Base** (`b95e48a`, my own run): `4467 passed / 39 skipped / 0 failed`, 274 s.
- **Instrument** (`test_bd49_host_tool_declaration_gate.py`, ACs 2-10): **9/9 green**.
  All six of the instrument's traps (AC4 direct, AC5 transitive, AC6 empty marker,
  AC7 leakage between bodies, AC8 the wrong tool, AC10 aliases) are killed.
- **Subject** (AC1, the live corpus): **red, exactly 37 entries** — agreeing with the
  independent §0 measurement taken by a different script before the instrument was written.
- **A defect in the instrument, caught by its own AC3:** the body's span started at
  `body[0].lineno`, and a comment is not an AST node ⇒ a marker standing on the first
  line fell outside the span and the declaration was not counted. The start was moved to
  `node.lineno`. **The positive leg caught a defect that all six
  negative ones would have missed** — both sides are needed.

**Next step (M2, not done):** corpus conformance — forms 3/4/5 → `skip_without`,
form 2 → declared. AC1 must reach zero by an edit to the corpus, NOT by weakening
the scanner. Then a full run and a delta against the base above.
