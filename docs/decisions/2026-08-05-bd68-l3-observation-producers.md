# bd#68 — BD-L3: the observation channel gets a producer

**Class:** an observation channel with no producer. The same class bd#59 closed for
BD-L2 — and all this time it has been living under BD-L3, under my own lots bd#28 and bd#63.

**Chokepoint:** `llm_subprocess._invoke_subprocess`, the `data` assembly point (`:2242-2250`),
where `worker_written_paths` and `mcp_server_losses` are already extracted from the transcript.
The tool observation is extracted **from those same events**, not from a new source.

---

## §1. §1b live base — both corpora, `dc98a35`

| corpus | result |
|---|---|
| **pytest** | **5457 passed / 47 skipped / 1 xfailed / 0 failed**, 364 s |
| **clean-room suite** | **5370 passed / 69 skipped / 1 xfailed / 0 failed**, 215 s, `PASS` |

## §2. The measurement — and it is against me

| field | who reads it | producers |
|---|---|---|
| `observed_tools` | `bd_l3._r36` (R3.6) and `bd_l3._r35` (R3.5, **my bd#63**) | **ZERO** — neither `_invoke_subprocess`, nor `_invoke_in_session`, nor any of the six reference backends |
| `observed_model` | `bd_l3._r33` (R3.3), `_pin_mismatch_refusal` | **ONE** — `_invoke_in_session` (my wiring, bd#29) |

⇒ On a real log **R3.5 and R3.6 are permanently `not-checked`**; **R3.3 is observable only for
`claude-in-session`** — not for the default `agent-sdk` and not for the pinned production path.

**Against myself.** The bd#59 lesson is recorded verbatim: "a negative leg proves that the
function CAN refuse; it does not prove that anyone will ever ASK it". I recorded it
and **did not apply it to L3** — I built bd#28 and bd#63 on top of an empty channel. Both lots are correct
as functions and both were inert in production. L3 had no gate against recurrence, so the
defect survived three lots in a row.

## §3. Measurement: the evidence already exists, no new source is needed

`_written_paths_from_events` (`:2855-2887`) already walks the stream-json transcript,
filters `block["type"] == "tool_use"` and reads `block["name"]`, selecting four
write tools. **The names of ALL invoked tools are in those same blocks.**

⇒ `observed_tools` is obtained by the same walk, from the same events, **with no new host
instrumentation** — exactly as in bd#63, where the refutation was found in the same record.
That is what makes the lot cheap.

## §4. Decisions

**D1 — `_observed_tools_from_events(events)`**: a sorted, deduplicated list
of `tool_use` block names. A separate function (§1aa) rather than an extension of
`_written_paths_from_events`: that one has its own contract (write paths) and its own tests, and mixing
two meanings into one walk is the next subject for somebody's archaeology.

**D2 — `observed_tools` is placed into `data` where `worker_written_paths` is**, by the same
disciplined route (after the `extra_data` merge, the name reserved), and it is
**absent on every error branch** — as with its neighbours.

**D3 — a gate against recurrence for L3**, modelled on `bd_l2.AWAITING_PRODUCER`: a requirement
declared observable must have a producer; it fails in both directions. The absence of
such a gate is why the defect lived through three lots.

**D4 — `observed_model` for `_invoke_subprocess` is NOT added by this lot.** In the
transcript the name of the actually dispatched model does not sit next to the tool blocks; its
source must be established separately, with its own exposure. R3.3 remains declared
partially observable — honestly, not silently.

## §5. Scope

**Edited:** `llm_subprocess.py` (+`_observed_tools_from_events`, +one line in
`data`), `conformance/bd_l3.py` (+`AWAITING_PRODUCER`, +`__all__`).
**New:** `tests/test_bd68_l3_observation_producers.py`.
**§1v — NOT in scope:** `observed_model` for subprocess (D4); the reference backends;
`_written_paths_from_events` (its own contract); `bd_l2`; `harness`.

## §6. §1a Sibling audit

`test_bd28_bd_l3_checker.py`, `test_bd63_r35_enforcement_falsifiable.py`,
`test_bd10_l3_authorship.py`, `test_4961254A_commit_manifest_inversion.py` (the same
transcript walk — **direct risk**), `test_bd58_adversary_harness.py`,
`test_bd59_enforcement_map.py`. To be checked by a run.

## §7. Acceptance criteria

- **AC1 (the producer exists).** `_observed_tools_from_events` on a transcript with
  `Read` and `Bash` returns `["Bash", "Read"]` — sorted and without duplicates.
- **AC2 (NEGATIVE — not always empty).** A transcript with no tool blocks gives `[]`,
  a transcript with blocks gives a non-empty list. A producer that always writes the same thing
  is as inert as its absence.
- **AC3 (the field reaches `data`).** A successful `_invoke_subprocess` puts
  `observed_tools` into `StepResult.data`.
- **AC4 (the end-to-end evidence → verdict link).** An escape present only in the transcript
  is caught by `bd_l3` as an R3.6 violation — the chain is closed, not broken off at telemetry.
- **AC5 (THE GATE AGAINST RECURRENCE, both sides).** An L3 requirement outside `AWAITING_PRODUCER`
  must have a producer; a requirement that has acquired a producer must leave
  the registry.
- **AC6 (D4 is declared).** `R3.3` is listed as partially observable rather than passed off as full.
- **AC7 (the neighbouring contract is untouched).** `_written_paths_from_events` continues to
  return only the paths of write tools.
- **AC8 (surface = `__all__`).**

## §8. What the PR does NOT claim

- It does not make `observed_model` observable for subprocess (D4).
- It does not touch the reference backends: they still produce no observations, and that is declared.
- It does not claim the transcript is complete: it records invocations through the harness, not everything the
  process might have done outside it.
