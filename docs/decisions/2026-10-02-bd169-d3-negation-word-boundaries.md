# bd#169: D3 prohibition gate reads negations on word boundaries

**Status:** r3 (gate r2 APPROVED; r2 gate r1 REJECTED: 1 MAJOR verb widening too broad, 3 MINOR; see `2026-10-02-bd169-gate-r1.md`) · **Tier:** 2 (one engine prod `.py`, Option D) · **Class:** LOCAL ·
**Chokepoint:** `phase_45_spec._D3_NEGATION_RE` / `_D3_MUTATION_VERB_RE`, the only matchers `_prohibited_paths` uses.
**Source:** bd#169 (parity with hal-v2#2091).

## §1 Problem (measured on `24dbd13`)

`_D3_NEGATION_RE = re.compile(r"do\s+not|don't|must\s+not|never|no(?=\s)", re.IGNORECASE)` (`:4622`) has
no word boundaries:

- "Whenever you edit `src/app.py`, run the tests." matches `never` inside "Whenever"; the window after it
  holds a mutation verb ("edit") and a path, so `src/app.py` becomes prohibited.
- "casino ", "piano " match `no(?=\s)`. "nevertheless", "do nothing" match `never` / `do\s+not` too.
- The opposite miss, found while writing RED: `_D3_MUTATION_VERB_RE` (`:4623`) is
  `\b(?:modify|edit|change|touch|add|write|append)\b`, base forms only. "no changes to `src/z.py`" (the
  issue's own must-prohibit example), "no edits to", "no modifications to" have no verb hit, so today they
  prohibit nothing.
- `_prohibition_gate` then turns an LLM SHIP into REVISE on a spec that `MODIFY src/app.py` exactly as the
  task asks, and every retry repeats it until `spec_gates` runs out (`E_REVIEW_FAILED`).

## §2 Design

Negation regex:

```python
_D3_NEGATION_RE = re.compile(r"\b(?:do\s+not|don't|must\s+not|never|no(?=\s))\b", re.IGNORECASE)
```

- Leading `\b`: no match inside a longer word ("Whenever", "casino", "piano").
- Trailing `\b`: no match on a longer word's prefix ("nevertheless", "do nothing", "notable", "nobody").
- `no` keeps its existing `(?=\s)` lookahead. The issue's suggested `\bno\b` would newly match "no-op" and
  "no." (`\b` sits between `o` and `-`/`.`), so "make a no-op edit to `src/x.py`" would start prohibiting a
  path it does not prohibit today. Keeping `(?=\s)` holds that behaviour; AC2 pins it.
- Verb regex adds only the noun forms the issue needs ("no changes / edits / modifications to"):

```python
_D3_MUTATION_VERB_RE = re.compile(
    r"\b(?:modify|modifications?|edit|edits|change|changes|touch|add|write|append)\b",
    re.IGNORECASE,
)
```

  Past tense, participles and `-ing` forms stay out on purpose (gate r1 MAJOR): bug-report prose such as
  "the cache is never written to `src/cache.py`" or "`total` is never modified in `src/calc.py`" describes
  the file to fix, and prohibiting it would be the same false-REVISE class this issue removes (AC3c pins it).
  Known limit: "changes" / "edits" are also 3rd-person verbs, so "`x` never changes in `src/a.py`" still
  prohibits `src/a.py`, as "`x` never change" does today; the issue asks for "no changes to" to count. Same for exception-scoped phrasing:
  "No changes outside `src/a.py`." / "No changes needed except in `src/a.py`." prohibit `src/a.py`, the
  window logic's existing limit ("do not modify anything except `src/a.py`" does it today); fixing it needs
  exception scoping, out of scope (gate r2 F1).
  Whole-word: "editor", "changelog", "address" do not match (AC3b controls).
- The `no(?=\s)` choice gets a one-line code comment so it is not "simplified" to `\bno\b` later.
- Nothing else changes: clause split, window logic, path normalisation, routing.

**Design constraints from the issue.** The gate is a regex (deterministic, no model step). No provider, LLM
or Jev call is on this path, so "provider down" and "subscription vs API backend" do not change it; the
gate runs before and independent of the LLM verdict it can override. AC4 pins that it never reaches the
LLM dispatch seam.

Not in scope: curly apostrophes ("don’t"), new negation forms, the HAL port (drift tool, hal-v2#2091).

## §3 Acceptance criteria

Unit tests call `phase_45_spec._prohibited_paths(td)`; end-to-end tests call
`phase_45_spec._gate_on_review(ctx, prev)` with a real spec file, as in `tests/test_gh1600_d3_prohibition_gate.py`.

- **AC1 (no prohibition on look-alike words)** For each of these, `_prohibited_paths` returns `{}`:
  "Whenever you edit `src/app.py`, run the tests.", "The casino edit touches `src/a.py`.",
  "A piano edit to `src/b.py`.", "nobody will edit `src/c.py`", "A notable change to `src/d.py`.",
  "nevertheless edit `src/e.py`", "do nothing but edit `src/f.py`".
- **AC2 (no-op stays unprohibited)** "Make a no-op edit to `src/x.py`." → `{}` (pins today's behaviour).
- **AC3 (real negations still prohibit)** "do not touch `src/x.py`" → `src/x.py` in the result;
  "never edit `src/y.py`" → `src/y.py`; "Don't modify `src/w.py`."
  → `src/w.py`; "You must not change `src/v.py`." → `src/v.py`; "DO NOT modify `src/u.py`" → `src/u.py`.
- **AC3b (noun forms)** "no changes to `src/z.py`", "no edits to `src/q.py`",
  "no modifications to `src/r.py`", "no modification to `src/s.py`" → that path prohibited. Whole-word control:
  "do not open the editor for `src/t.py`", "never read the changelog `CHANGELOG.md`",
  "do not use the address in `src/addr.py`" → `{}`.
- **AC3c (descriptive negation stays unprohibited)** "the cache is never written to `src/cache.py`",
  "`total` is never modified in `src/calc.py`", "`parse()` never writes the header in `src/p.py`",
  "no tests were added for `src/x.py`", "it was never edited in `src/e2.py`" → `{}` (pins today's behaviour).
- **AC4 (end to end, "Whenever")** task_description "Whenever you edit `src/app.py`, run the tests.",
  spec `- MODIFY src/app.py`, LLM verdict SHIP → `_gate_on_review` result `status == "ok"`
  (SHIP stands). During the call `phase_45_spec.invoke_llm_subprocess` (the name the module imports, `:82`) is patched to
  raise and is never hit.
- **AC5 (end to end, real prohibition)** task_description "never edit `src/y.py`", spec `- MODIFY src/y.py`,
  SHIP → `status == "error"`, `error_code == "E_VALIDATION_RETRY"`.
- **AC6 (regression)** `tests/test_gh1600_d3_prohibition_gate.py` and `tests/test_phase_45_spec.py` stay green.
