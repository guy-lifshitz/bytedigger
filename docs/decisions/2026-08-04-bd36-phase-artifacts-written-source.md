# bd#36 — `phase_artifacts.written`: the manifest as a SOURCE, not a filter

**Class:** the instrument answers a question it was never asked, and its refusal looks
like a success. `written: []` together with `write_tracking: "git-delta"` reads as "the phase wrote
nothing", while it actually means "my writes were outside the observation window".

**Chokepoint:** `engine.py:493` (`self._written |= delta`) — the only place where
`written` is filled; `engine.py:787-791` (`write_tracking`);
`llm_subprocess.py:_validate_manifest_or_raise` (the alphabet of paths).

**§1b live base**, taken BEFORE the freeze, on `a2d901c`, from `engine_py`, after wiping
`build`/`__pycache__`: **`4489 passed / 39 skipped / 0 failed`**, 291 s.

---

## §0. The subject is ALIVE — measured by a run, not by reading

The probe ran a real `WorkflowEngine` (the UUT is not mocked), the step genuinely wrote a file, the manifest
was delivered in the regular `StepResult.data` form. Three cases:

| case | writes | the manifest declares | `written` | `write_tracking` | `files_touched` |
|---|---|---|---|---|---|
| A | `a.txt`, `b.txt` in the repo | only `a.txt` | **`['a.txt','b.txt']`** | `git-delta` | `('manifest', ['a.txt'])` |
| B (control) | `c.txt` in the repo | — (no manifest) | `['c.txt']` | `git-delta` | `('scan', ['c.txt'])` |
| C | `scratchpad/note.md` OUTSIDE the repo | that path | **`[]`** | **`git-delta`** | **`[]`** |

The file in C was physically created (`exists() == True`), the worker reported it — and it is invisible.

### §0.1. The measurement is sharper than the issue: for `written` the manifest is not a filter, it DOES NOT PARTICIPATE

The issue puts it as: the manifest is "used only as a narrowing intersection with an already
obtained delta" as regards `written`. The measurement (case A) **refutes** this:
the manifest declared one path, and `written` contains **both**. The cause is the order in the code:
`engine.py:493` merges the raw delta into `self._written` **before** and **outside** any
manifest logic, while the intersection at `:507-517` governs only the payload of the
`files_touched` event.

⇒ Hence a second defect the issue does not name: **`written` and `files_touched.paths`
describe the writes of one and the same step by two DIFFERENT rules and diverge** — in A
`written` is wider than `files_touched` by exactly the path the manifest rejected. A consumer
cross-checking the two events gets a contradiction, and neither one is marked as the less
trustworthy.

### §0.2. The shape of the refusal is already forbidden by the frozen spec — but by a rule that does not catch it

`EMISSIONS_SPEC.md` `[G18r3:EDGE-4]` names `written: []` next to `"git-delta"`
an **"overclaim shape"** and requires `"not-observed"` in that situation. AC-E3b states
the principle: "`write_tracking` never overclaims".

But AC-E3b ties the rule to the question **"was a delta computed"** rather than
**"did the observation window cover the phase's writes"**. In case C the delta was computed successfully
(the step ran, the snapshots were taken), so the letter of AC-E3b is observed while the meaning it
declares is violated. **That is precisely the superclass: the instrument answers "I ran git diff", while its
signature claims "I observed the writes".**

### §0.3. Who actually produces the manifest — the issue's premise holds, but not everywhere

| producer | `manifest_source` | what it builds from | does it carry paths outside the repo |
|---|---|---|---|
| `lib/reference_backends/*` (`pydantic_openai`, `pydantic_anthropic`, `agent_sdk`) | `git_diff` | `_manifest_since` = `git diff --name-only` + untracked **inside `root`** | **no, never** |
| `llm_subprocess` (production, pin `claude-subprocess`) | `harness_tool_record` | `_written_paths_from_events`: `block["input"]["file_path"]` of `Write/Edit/MultiEdit/NotebookEdit` | **yes** — taken VERBATIM from the transcript |

For the reference backends the union with the manifest adds NOTHING: their manifest is itself
the git delta of the same tree. The issue's premise ("the worker reports what git does not see")
holds **only for the production path**, and there it holds completely.

**This changes the shape of the check:** a RED written on a reference backend would be green
both after the fix and before it. The negative leg must strike `harness_tool_record` specifically.

### §0.4. "Repo-relative" — an unenforced claim of a docstring

`llm_subprocess.py:392` declares `worker_written_paths` to be repo-relative.
`_validate_manifest_or_raise` (`:400-424`) checks **only** `isinstance(list)` and
`isinstance(str)` of each element. **There is neither normalisation nor a relativity check.**
`_written_paths_from_events` puts `file_path` in as is.

⇒ A union today would glue two alphabets together (the repo-relative paths of the git delta and the
arbitrary — in practice absolute — paths of the transcript), and **not a single layer would
notice**. This is exactly item 3 of the issue, now with both alphabets named.

---

## §1. Decisions the issue requires to be taken EXPLICITLY

**D1 — union, not intersection.** `written = git_delta_paths ∪ normalise(manifest_paths)`.

**D2 — `manifest is None` stays a DEFER** to the delta, unchanged (§2 D2, case B —
the control leg).

**D3 — normalisation (the rule for reducing to one namespace).**
The single namespace is **a repo-relative POSIX path when the path lies inside
`git_cwd`; otherwise an absolute POSIX path**. The reduction:
1. `Path(p)`; if not absolute — treat as repo-relative, anchor `git_cwd`;
2. `resolve()` both; if the result is inside `git_cwd` — return `relative_to(git_cwd)`;
3. otherwise — return the absolute path.
The rule is total (any input yields exactly one form) and idempotent. **The price is named:**
absolute paths leak into the event, i.e. `written` ceases to be uniformly
repo-relative. This is deliberate — the alternative (discarding everything outside the repo) is
precisely today's blindness.

**D4 — trust in the manifest.** Paths from the manifest are included **without an existence
check**. The argument: the manifest is the harness's record of its own tool calls, not
the worker's self-report (`4961254A`), and an existence check would introduce a race (the file could have been
legitimately deleted by a later step). **The price is named:** a worker that reported an unwritten
path will land in `written`; today the intersection suppressed that. The compensation is D5.

**D5 — a new value of `write_tracking`.** Today there are two: `git-delta`, `not-observed`.
**`git-delta+manifest`** is added — "some of the paths were observed only by the worker's report,
git never saw them". The value must appear **only** when the union actually
added something beyond the delta. Without it the change of source would dissolve silently into
`git-delta`, which the issue directly forbids.

**D6 — `not-observed` is not softened.** If the delta was not computed (missing/non-git `git_cwd`,
a step failure), `write_tracking` stays `not-observed` **even with a non-empty manifest**.
Otherwise the change of source would silently turn `not-observed` into an observation — directly forbidden by
the issue and by `[G18r3:EDGE-4]`.

**D7 — `files_touched` is not touched.** The intersection at `:507-517` stays as it is.
The divergence from §0.1 is named, but removing it is a separate decision: `files_touched` is
per-step and manifest-filtered by its own spec `3F5599A6 A3`, and changing it in the same
PR would mean fixing two subjects with one diff.

---

## §2. §5 Scope

**Edited**
- `engine_py/bytedigger_engine/engine.py` — `_written` is filled from the union;
  `write_tracking` gains a third value; path normalisation.

**New files**
- `engine_py/tests/test_bd36_written_source.py` — RED.

**§1v — NOT in scope**
- `llm_subprocess.py` — the manifest contract and the validator are not changed. Normalisation
  is done on the consumer's side (the engine), because the anchor (`git_cwd`) is known
  only to it. Tightening the validator to check relativity would break the production path,
  where paths are absolute by construction.
- `files_touched` (D7).
- `lib/reference_backends/*` — their manifest is git-derived, there is nothing to fix.
- `EMISSIONS_SPEC.md` — frozen; the new `write_tracking` value requires an amendment to the
  spec, and that is a **question for the dispatcher** (see §5), not an edit taken unilaterally.

## §3. §1a Sibling audit

The consumers of the three signals, from the §5 list, to be run with `--require-clean`:

| file | tests | what it reads |
|---|---|---|
| `test_bd18_emissions.py` | 45 | `phase_artifacts`, `write_tracking` — **direct risk** (AC-E3/E3b pin the keys and the values) |
| `test_bd8_l1_oracle.py` | 44 | `phase_artifacts`, `write_tracking` — pins `{"write_tracking": "git-delta", "written": []}` **literally** |
| `test_engine.py` | 19 | `phase_artifacts`, `files_touched` |
| `test_error_locus_CCA65EB0.py` | 12 | `files_touched` |
| `test_3F5599A6_a2a3_residue.py` | 11 | `files_touched` (the manifest intersection) |
| `test_gh1082_engine_scan_cwd.py` | 9 | `files_touched` |
| `test_gh780_manifest_nondict.py` | 7 | `files_touched`, an invalid manifest |
| `test_files_touched.py` | 6 | `files_touched` |
| `test_event_log_replay_e2e.py` | 3 | `phase_artifacts` |

**156 tests in total.** `test_bd8_l1_oracle.py:19` is a known risk: it pins the pair
`git-delta` + `written: []` literally; D5/D6 must NOT disturb it (its delta is
computed and there is no manifest ⇒ branch D2).

## §4. Acceptance criteria

Every leg goes through a real `WorkflowEngine`, the step genuinely writes a file (§1l, the UUT is not mocked).

- **AC1 (the subject, §1l).** A step writes a file OUTSIDE `git_cwd` and declares it in a
  `harness_tool_record` manifest ⇒ the path IS in `written`. Red today (the §0 measurement, case C).
- **AC2 (the control leg, D2).** A step WITHOUT a manifest ⇒ `written` equals EXACTLY the git delta.
  Catches a "fix" satisfied by having stopped intersecting altogether.
- **AC3 (NEGATIVE LEG, D6).** `git_cwd` missing/non-git + a non-empty manifest ⇒
  `write_tracking == "not-observed"`. A gate that lets an observation through here is inert:
  it would permit silently turning "did not look" into "observed".
- **AC4 (D5, the new value on the merits).** A write only outside the repo ⇒
  `write_tracking == "git-delta+manifest"`; a write only inside the repo with a manifest that
  added nothing ⇒ `"git-delta"`. Both sides, otherwise the new value either
  never appears or appears always.
- **AC5 (D3, normalisation).** One and the same file inside the repo, declared by the manifest with an
  ABSOLUTE path and by the git delta with a relative one, yields **one** entry in `written`,
  repo-relative. Catches the gluing of two alphabets.
- **AC6 (D3, idempotence).** A path outside the repo yields the same string on repeated
  normalisation.
- **AC7 (D4, the price is named).** The manifest declares a non-existent path ⇒ it IS INCLUDED in
  `written`. Pins the decision taken, so that a future "optimisation" with an existence
  check cannot pass silently.
- **AC8 (§1a).** `test_bd8_l1_oracle.py` and `test_bd18_emissions.py` are green without edits.

## §5. QUESTION FOR THE DISPATCHER (does not block RED, blocks the merge)

`EMISSIONS_SPEC.md` is a **frozen** conformance spec, and AC-E3 pins
`write_tracking` together with an exact set of keys. D5 introduces a third value ⇒ the spec
must be amended, not circumvented. Two paths:
- **(a)** extend AC-E3b by enumerating the three values — honest, but it edits a frozen
  document;
- **(b)** do not introduce the new value, keep `git-delta` — then the change of source
  becomes invisible, which the issue directly forbids ("the new value must be
  named, not folded into the existing two").

I am going with **(a)** as the only one that preserves the issue's requirement; I am preparing the spec
edit as a separate commit so that it is visible and easy to revert. If the dispatcher decides otherwise —
D5 and AC4 change, everything else holds.

## §6. What this PR does NOT claim

- It does not claim that `written` is now complete: a path the worker wrote and did NOT declare
  remains invisible outside the repo. Totality is provided only by the manifest, and that is the
  harness's record of its own tool calls, not of arbitrary writes by the process.
- It does not fix the `written` ↔ `files_touched` divergence (§0.1, D7) — named, deferred.
- It does not touch bd#8: its set is taken from `scratchpad/<run_id>/`, and this PR changes nothing
  there.
