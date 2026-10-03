# S4 wiring: devops_scan in scripts/build-gate.sh (SHADOW, +14 days)

**Status: FROZEN r2 (gate r2 PASS, minors in `-gate-r2.md`) · was DRAFT r2 (gate r1 REJECT 4 MAJOR: `-gate-r1.md`)** · **Tier:** 2 (gate script change, flag with owner/expiry) · **Class:** SECURITY wiring
**Chokepoint:** `gate_phase_55` in `scripts/build-gate.sh`: the last gate before the LLM review phase; the only place the scan verdict meets the pipeline.
**Provenance:** S4/M1 (#229) added `scripts/devops_scan.py` (exit 0 clean, 1 blocked, 2 unavailable) and explicitly left wiring out. MGR decision 2026-10-03: call it in `build-gate.sh` before the LLM review, flag SHADOW by default (warn + event), owner + expiry +14 days. Audit hal#2320 §6 row M1.

## 1. Contract

New function `run_devops_scan` called at the end of `gate_phase_55` (after `check_deliverables "5.5"`), phase 5.5 only. Scan = `python3 <script> --root "$CWD" --timeout 3`, where `<script>` = sibling `scripts/devops_scan.py`, or env `DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE` when set (test seam, `_TEST_OVERRIDE` naming as in the dispatcher). No `--files`. rc is authoritative; the JSON `status` is only used for the reason text (rc/status disagreement -> rc wins).

**Time budget (gate r1 M1).** The hook runs under a 15 s harness timeout. The scan runs in the background of the gate shell with a total budget of 8 s (constant `DEVOPS_SCAN_BUDGET_S=8`); on overrun the gate kills it and treats the result as rc 2 reason `budget_exceeded`. The gate's other output (deliverable soft verdict) is NOT lost to a slow scan: the scan runs last and a budget overrun is bounded well below the hook timeout.

Flag: env `DEVOPS_SCAN_ENFORCE`; constants `DEVOPS_SCAN_OWNER="s4-bytedigger (MGR)"`, `DEVOPS_SCAN_EXPIRES="2026-10-17"`. Mode = `enforce` iff the value is exactly `1`, else `shadow`. "Today" = UTC `date -u +%F`, overridable by `BD_GATE_TODAY_TEST_OVERRIDE` (tests only); an unparseable override is ignored.

| case | SHADOW | ENFORCE |
|---|---|---|
| rc 0 (clean / nothing_to_scan) | silent, no event | silent, no event |
| rc 1 (blocked) | stderr `WARN: devops_scan SHADOW blocked: <n> gating finding(s)`; event; gate continues; **stdout stays empty** | `hard_block "devops_scan blocked: <n> gating finding(s): <ids>"` |
| rc 2 / other rc / script missing / python3 missing / non-JSON / `budget_exceeded` | stderr `WARN: devops_scan SHADOW unavailable: <reason>`; event; continues | `hard_block "devops_scan unavailable: <reason>"` |
| today > EXPIRES, SHADOW | the flag is expired: stderr `WARN: DEVOPS_SCAN_ENFORCE SHADOW window expired <EXPIRES> (owner <OWNER>): flip to 1 or extend`, event field `flag.expired=true`; the scan still runs and behaves as SHADOW. **No block** (gate r1 M3: degrade, do not fall over; a past-expiry block would hit every plugin user and CI) | normal ENFORCE |

Messages put into `hard_block` and the event line are sanitised (backslash, double quote and control characters removed from scanner-derived text), so output stays valid JSON.

Event: one JSON line appended to `$CWD/.bytedigger/devops-scan-shadow.jsonl`, written with printf (no python needed), for rc != 0 or an expired flag, in both modes: `{"ts","mode","rc","status","gating","reason","flag":{"name","owner","expires","expired"}}`. A failed write never changes the gate outcome. Known limits (accepted): the file is unbounded and not git-ignored in user projects.

Hard block uses the existing `hard_block` (not soft MISSING_FIELDS), so loop prevention cannot bypass an ENFORCE verdict. Early exits (gates disabled, stale state), other phases, other gate functions unchanged.

**Backends (gate r1 M2).** This lot changes the bash gate only. The TS gate (`gate_backend=ts`, `scripts/ts/build-phase-gate.ts`) is out of scope: in SHADOW exit codes and stdout are identical to today (the scan only writes stderr and the event file), so the dispatcher's shadow comparison does not fire. In ENFORCE the TS backend would silently not run the scan and `gate_backend=shadow` would log mismatches; therefore **a TS port with a parity test is a precondition for flipping `DEVOPS_SCAN_ENFORCE` anywhere** (recorded in the PR body and as an OFI). A guard cell asserts the SHADOW exit code and stdout of 5.5 are byte-identical with and without the scan finding.

## 2. Acceptance criteria (RED `tests/build-gate-devops-scan.bats`, hermetic: fake script via `DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE`; build-state.yaml `current_phase: 5.5`)

- AC1: SHADOW (env unset), fake rc 1 -> exit 0, stdout empty, stderr contains `devops_scan SHADOW blocked`, event with `mode":"shadow"`, `rc":1`, flag owner and expires `2026-10-17`; fake ran once with `--root <cwd> --timeout 3`, no `--files`.
- AC2: SHADOW rc 2 -> exit 0, WARN names the reason, event present. rc 0 -> no WARN, no event file, fake ran.
- AC3: ENFORCE=1: rc 1 -> exit 1, `{"decision":"block"`, `HARD BLOCK: devops_scan blocked`, ids and count; rc 2 -> `HARD BLOCK: devops_scan unavailable`; rc 0 -> exit 0; rc 1 with `gate_block_counter: 9` still blocks.
- AC4: `DEVOPS_SCAN_ENFORCE` = `true`, `0`, empty, `yes`, `01` -> SHADOW semantics.
- AC5: script missing / non-JSON stdout / rc 7 / python3 absent: SHADOW -> exit 0 + WARN + event; ENFORCE -> hard block unavailable.
- AC6 (expiry, M3): `BD_GATE_TODAY_TEST_OVERRIDE=2026-10-17` SHADOW -> no expiry WARN; `2026-10-18` SHADOW -> exit 0 (NOT blocked), expiry WARN names flag, owner, date, event `flag.expired==true`, the scan still ran; `2026-10-18` ENFORCE=1 -> normal ENFORCE verdict; malformed override ignored (real date used).
- AC7: phases 5.3, 6, 7, `gates_enabled:false`, stale state never invoke the scan.
- AC8: event-write failure (`.bytedigger` is a file) does not change the exit code in either mode.
- AC9 (budget, M1): fake sleeps 30 s -> gate returns within 12 s; SHADOW exit 0 + WARN `budget_exceeded` + event; ENFORCE -> hard block unavailable `budget_exceeded`; the fake process is not left running.
- AC10 (shield, M4): (a) 5.5 deliverables missing (no `test_integrity_check`) + SHADOW rc 1 -> still exit 2 with the soft block message; (b) SHADOW stdout byte-identical with and without a finding; (c) `DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE`, `BD_GATE_TODAY_TEST_OVERRIDE` and `DEVOPS_SCAN_ENFORCE` all unset: no unbound-variable crash under `set -u`, the real sibling script is used (its argv/`--root` observable via a PATH shim for python3 or by `--help`-free dry check) and exit is 0 on a clean tree; (d) rc 1 with a finding id containing `"` and a backslash: ENFORCE output is valid JSON (`python3 -c json.loads`); (e) rc 1 JSON `status` "clean": rc wins.
- AC11: `tests/test_bd136_gate_counter_table_parser.py` and `build-gate.bats`/`gate-dispatcher.bats` stay green; GREEN pins `DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE` to a clean fake in the bd136 5.5 cells if they would otherwise run the real scanner.

## 3. Out of scope

Changing `devops_scan.py`, phase-specific file lists (`--files` from the diff), engine registry, docs sweep, TS gate port (precondition for ENFORCE, see Backends), flipping the flag (owner decision at expiry), `commands/build.md` text.

## 4. Gate r2 clarifications (binding on GREEN)

- Event field types: `rc` and `gating` JSON numbers, `flag.expired` JSON boolean, others strings.
- rc 1 with non-JSON stdout: still rc 1 (blocked, count unknown -> `gating` 0, reason `non_json_output`); rc wins.
- The gate's own kill on budget overrun (rc 143/137) maps to reason `budget_exceeded`; an EXIT trap kills a still-running background scan and removes its temp file.
- Path-derived text in `hard_block` messages is sanitised the same way as ids.
- Code comment at `run_devops_scan` states the ENFORCE precondition (TS gate port + parity test before flipping anywhere).
- Accepted limits (PR body): an orphaned hadolint/trivy child after a budget kill; repos with 3+ scanner targets will routinely exceed 8 s (input to the owner's flip decision); event file unbounded.
