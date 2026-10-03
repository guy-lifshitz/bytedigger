# S4 wiring: devops_scan in scripts/build-gate.sh (SHADOW, +14 days)

**Status: DRAFT r1 (pre-gate)** · **Tier:** 2 (gate script change, flag with owner/expiry) · **Class:** SECURITY wiring
**Chokepoint:** `gate_phase_55` in `scripts/build-gate.sh`: the last gate before the LLM review phase; the only place the scan verdict meets the pipeline.
**Provenance:** S4/M1 (#229) added `scripts/devops_scan.py` (exit 0 clean, 1 blocked, 2 unavailable) and explicitly left wiring out. MGR decision 2026-10-03: call it in `build-gate.sh` before the LLM review, flag SHADOW by default (warn + event), owner + expiry +14 days. Audit hal#2320 §6 row M1.

## 1. Contract

New function `run_devops_scan` called at the end of `gate_phase_55` (after `check_deliverables "5.5"`), phase 5.5 only. Scan = `python3 <scripts dir>/devops_scan.py --root "$CWD"`, stdout captured, stderr passed through, rc captured; no `--files` (full tracked tree), default `--fail-on`.

Flag: env `DEVOPS_SCAN_ENFORCE`. Constants in the script: `DEVOPS_SCAN_OWNER="s4-bytedigger (MGR)"`, `DEVOPS_SCAN_EXPIRES="2026-10-17"`. Mode = `enforce` iff the env value is exactly `1`, otherwise `shadow`. "Today" = UTC date from `date -u +%F`, overridable by env `BD_GATE_TODAY` (tests only).

| case | SHADOW | ENFORCE |
|---|---|---|
| rc 0 (clean / nothing_to_scan) | silent, no event | silent, no event |
| rc 1 (blocked) | stderr `WARN: devops_scan SHADOW blocked: <reason>`; event line; gate continues | `hard_block "devops_scan blocked: <n> gating finding(s): <ids>"` |
| rc 2 (unavailable, fail-closed) | stderr WARN naming the reason; event line; continues | `hard_block "devops_scan unavailable: <reason>"` |
| rc other / script missing / `python3` missing / output not JSON | treated as rc 2 | same as rc 2 |
| today > EXPIRES and mode shadow | `hard_block "DEVOPS_SCAN_ENFORCE SHADOW window expired <EXPIRES> (owner <OWNER>): set DEVOPS_SCAN_ENFORCE=1 or extend the flag"`, BEFORE running the scan | n/a (ENFORCE ignores expiry) |

Event: one JSON line appended to `$CWD/.bytedigger/devops-scan-shadow.jsonl` (dir created if absent; failure to write never changes the gate outcome, fails open for telemetry only): `{"ts": <utc iso>, "mode": "shadow"|"enforce", "rc": n, "status": <script status or "unavailable">, "gating": <count>, "reason": <str>, "flag": {"name": "DEVOPS_SCAN_ENFORCE", "owner": ..., "expires": ...}}`. Written for rc != 0 in both modes.

Hard block uses the existing `hard_block` (not soft MISSING_FIELDS, so loop prevention cannot bypass a security verdict). The gate stays a no-op when gates are disabled or the state is stale (existing early exits unchanged). Other phases untouched. The scan runs once per gate invocation; no caching.

## 2. Acceptance criteria (RED `tests/build-gate-devops-scan.bats`, hermetic: fake `devops_scan.py` selected via env `DEVOPS_SCAN_SCRIPT` override that defaults to the sibling script; build-state.yaml with `current_phase: 5.5`)

- AC1: SHADOW (env unset), fake script rc 1 -> gate exit 0, stderr contains `devops_scan SHADOW blocked`, event line present with `mode":"shadow"`, `rc":1`, flag owner and expires `2026-10-17`.
- AC2: SHADOW rc 2 -> exit 0, WARN names the reason, event present. rc 0 -> no stderr WARN, no event file.
- AC3: `DEVOPS_SCAN_ENFORCE=1`, rc 1 -> exit 1 with `{"decision":"block"` and `HARD BLOCK: devops_scan blocked`; rc 2 -> `HARD BLOCK: devops_scan unavailable`; rc 0 -> exit 0.
- AC4: ENFORCE with `DEVOPS_SCAN_ENFORCE=true` or `0` or empty -> SHADOW semantics (exactly `1` enforces).
- AC5: script missing / python3 absent from PATH / non-JSON stdout / rc 7: SHADOW -> exit 0 + WARN + event; ENFORCE -> hard block unavailable.
- AC6: expiry: `BD_GATE_TODAY=2026-10-17` SHADOW -> normal; `2026-10-18` SHADOW -> exit 1 hard block naming flag, owner, expiry, and the fake script was NOT executed; `2026-10-18` ENFORCE=1 -> normal ENFORCE verdict.
- AC7: phases other than 5.5 (5.3, 6, 7) never invoke the scan (fake script drops a marker file; absent). `gates_enabled:false` and stale state also never invoke it.
- AC8: event-write failure (`.bytedigger` is a file) does not change the exit code in either mode.
- AC9 (static): no `claude`/`bun`/network call added; the existing build-gate.bats and gate-dispatcher.bats suites stay green; `check_deliverables`/other gate functions byte-identical.

## 3. Out of scope

Changing `devops_scan.py`, phase-specific file lists (`--files` from the diff), engine registry, docs sweep, flipping the flag (owner decision at expiry), `commands/build.md` text.
