# S4/M1: deterministic devops scan script (trivy + hadolint, fail-closed)

**Status: DRAFT r2 (gate r1 REJECT: 7 MAJOR, see `2026-10-03-s4-m1-gate-r1.md`)** · **Tier:** 2 (new standalone `scripts/*.py`, no engine registry, no flag) · **Class:** SECURITY (IaC/Dockerfile severity gate)
**Chokepoint:** `scripts/devops_scan.py` main(): the only place a scan verdict is produced; exit code is the verdict.
**Provenance:** HAL GH#342 A+B (PR #360, PM GH342B 2026-07-05): the severity gate was fail-open (findings and scanner malfunction both swallowed as ok), fixed to fail-closed. bd#176 (892bb0f, bd#89 P1) deleted the engine stage `phase_5_devops_scan`; the audit hal#2320 §6 row M1 found the class (CRITICAL/HIGH in Dockerfile/IaC) left with no layer, verdict "в скрипт". Plan approved by Guy 2026-10-03 (audit repair step 4).
**Principles:** script first, no LLM reviewer, provider-agnostic (no `claude`, no API key, no HAL path, no bun), RED first, nothing added to the engine registry or `flags_catalog`.

## 1. Contract

`python3 scripts/devops_scan.py --root DIR [--files a,b,...] [--fail-on CRITICAL,HIGH] [--allowlist FILE] [--timeout SEC]`

- No `--files`: scan candidates = `git ls-files -z` under `--root`, entries deleted on disk skipped; git missing / not a repo / rc != 0 -> exit 2 `git_ls_files_failed` (G1, fail-closed, never an empty list); with `--files`: that list (paths relative to root; missing/outside-root entries are ignored, never crash).
- Classification by basename/extension: **Dockerfile** (`Dockerfile`, `Dockerfile.*`, `*.Dockerfile`, `Containerfile*`; but a name ending `.dockerignore` is not a Dockerfile) -> `hadolint --format json`; **IaC** (`*.tf`, `*.tfvars`, `docker-compose*.y*ml`, `compose.y*ml`, `*.y*ml` under a dir named `k8s`/`kubernetes`/`helm`/`charts`/`manifests`, `Chart.yaml`; a path matching Dockerfile class is Dockerfile only) -> `trivy config --format json --quiet <dir-of-files>`.
- Stdout: one JSON object `{"status": "clean"|"blocked"|"unavailable"|"nothing_to_scan", "gating": [...], "waived": [...], "nongating": [...], "reason": str}`. Each finding: `{scanner, file, id, severity, description}`.
- Exit code: 0 = clean / nothing_to_scan / all gating waived; **1 = blocked** (>=1 gating, non-waived finding); **2 = unavailable** (fail-closed).

## 2. Fail-closed rules (GH#342 B)

| # | case | result |
|---|---|---|
| F1 | candidates contain a Dockerfile and `hadolint` is not on PATH | exit 2, reason `hadolint_not_found` |
| F2 | candidates contain IaC and `trivy` is not on PATH | exit 2, reason `trivy_not_found` |
| F3 | scanner timeout (60 s default, `--timeout`), non-zero rc outside the scanner's documented findings rc, or stdout not valid JSON / wrong shape | exit 2, reasons `timeout` / `subprocess_error` / `json_decode_error` |
| F4 | no candidate of either class | exit 0, `nothing_to_scan` (legitimate; no binary needed, a missing binary is NOT an error here) |
| F5 | unknown/missing `--root`, unreadable allowlist path given explicitly, usage error | exit 2 with a JSON verdict `unavailable` on stdout (no silent default, never a bare argparse trace) |

| F6 | hadolint rc 0 with a non-empty findings list, or rc 1 with empty/whitespace stdout | rc 0: findings still parsed and gate (stdout is the truth); rc 1 + empty stdout: exit 2 `subprocess_error` (never read as `[]`) |
| F7 | trivy JSON shape: `Results` absent or null = clean (real trivy behaviour); `Results` present but not a list, `Misconfigurations` present but not list/null, hadolint stdout not a JSON list, a finding that is not an object | exit 2 `json_shape_error` |
| F8 | `--fail-on` empty, or any token not in {CRITICAL,HIGH,MEDIUM,LOW,UNKNOWN} (case-insensitive) | exit 2 `bad_fail_on` (never an empty gating set) |
| F9 | several scanner runs, one fails | exit 2 even if others are clean; priority `unavailable` > `blocked` > `clean`; gating findings from the other runs still listed |
| F10 | a finding whose severity/level is missing, not a string, or unknown (either scanner) | treated HIGH (fail-closed); hadolint mapping applies only to its 4 known levels |

Findings rc: hadolint rc 0/1 and trivy rc 0 are "ran" (stdout JSON is the truth, see F6); anything else is F3. Trivy findings whose target is not among the listed files are kept (gating); `file` = path relative to root when under root, else as reported.

**Scanner-native suppression is disabled** (gate r1 M7): both scanners run with cwd and HOME set to a fresh empty temp dir outside `--root`; hadolint gets `--disable-ignore-pragma` and `--no-color`; trivy gets `--ignorefile <empty temp file>`; only the `--allowlist` (agreement + kill-by) can waive. Files are passed by absolute path.

## 3. Severity and waivers

- Gating set = `--fail-on` (default `CRITICAL,HIGH`), case-insensitive. hadolint levels map: `error`->HIGH, `warning`->MEDIUM, `info`/`style`->LOW. trivy severities are used as is; an unknown severity string is treated as HIGH (fail-closed).
- Allowlist line: `<pattern> :: <AGREEMENT-8HEX> :: kill-by:YYYY-MM-DD`; `#`/blank skipped. Waives a gating finding iff `pattern` is a substring of `scanner|file|id|description` AND kill-by >= today. An expired or malformed line waives nothing (malformed = skipped, never raises; an empty pattern, an agreement id that is not 8 hex chars of either case, a kill-by more than 365 days ahead are malformed). Waived findings are listed under `waived`, never dropped.

## 4. Acceptance criteria (RED file `tests/test_s4_m1_devops_scan.py`)

All tests are hermetic: fake `trivy`/`hadolint` shell shims in a tmp dir placed first on PATH; `ANTHROPIC_API_KEY` removed; PATH without `claude`/`bun`.

- AC1: Dockerfile + hadolint shim emitting an `error` finding -> exit 1, `status=blocked`, finding in `gating`, severity HIGH.
- AC2: IaC `main.tf` + trivy shim emitting CRITICAL -> exit 1; MEDIUM only -> exit 0, finding in `nongating`.
- AC3: F1 and F2: relevant file, shim absent from PATH -> exit 2, exact reason. F4: only `a.py` -> exit 0 `nothing_to_scan` even with no binaries on PATH.
- AC4: F3 three cases: shim sleeps past `--timeout 1`; shim exits 3; shim prints `not json` -> exit 2 with the matching reason each.
- AC5: allowlist: matching unexpired line -> exit 0 and the finding under `waived`; expired kill-by -> exit 1; malformed line -> exit 1; non-matching pattern -> exit 1.
- AC6: unknown severity from trivy -> treated HIGH -> exit 1. `--fail-on CRITICAL` with a HIGH finding -> exit 0 (nongating).
- AC7: `--files` honoured: repo with a bad Dockerfile, `--files README.md` -> `nothing_to_scan`; `--files ../outside` ignored, no crash. F5: nonexistent `--root` -> exit 2.
- AC8 (static): `scripts/devops_scan.py` has no import of `bytedigger_engine`, no `subprocess` call with `claude`/`bun`/`.ts` in argv, no `HAL_` env read; exactly one scan verdict is produced by `main()` (exit codes limited to {0,1,2}).
- AC10 (gate r1 M1-M7): git failure without `--files` -> exit 2 (G1); trivy `{}` and `{"Results": null}` -> clean exit 0 while `{"Results": []}`-shape violations (`Results` = `{}`/string) -> exit 2; hadolint shim exit 0 with an error finding -> exit 1; hadolint exit 1 with empty stdout -> exit 2; `--fail-on ""` and `--fail-on CRTICAL` -> exit 2 `bad_fail_on`; allowlist empty pattern, 7-hex id, kill-by +400d -> waive nothing; missing/unknown hadolint level and missing trivy Severity -> HIGH -> exit 1; repo with `.hadolint.yaml`/`.trivyignore`/inline ignore pragma: the shim records the argv/env and the test asserts `--disable-ignore-pragma`, `--ignorefile`, cwd and HOME outside root; two Dockerfiles with one scanner run failing -> exit 2 and the other's gating finding still listed; `Dockerfile.dockerignore` not scanned, `compose.yaml` and `Containerfile` scanned.
- AC9 (side-effect): the script writes nothing under `--root` (tree hash before == after).

## 5. Out of scope

Wiring into `build-gate.sh`/`commands/build.md` (separate follow-up, needs the gate-point decision from MGR/Guy), engine registry, flags, error codes, docs sweep beyond this file, Terraform plan-level checks, secret scanning (`security-scan.sh` owns it).
