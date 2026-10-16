---
red_tests:
  - engine_py/tests/test_bd109_build_failure_classes.py
sibling_tests:
  - engine_py/tests/test_bd109_class1_interpreter_pin.py
  - engine_py/tests/test_gh1626c_worker_interpreter.py
  - engine_py/tests/test_bd92_per_cycle_artifacts.py
  - engine_py/tests/test_bd85_retry_budgets.py
  - engine_py/tests/test_phase_45_spec.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_45_spec.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - CHANGELOG.md
---
# bd#109: three /build 15.10 failure classes, checked on bytedigger

Refs #109. Base: origin/main @ 766cddb. Tier 2. Fix budget: at most 3 files of production code; one verdict per class.

## 1. Measurement (code read before the spec)

| # | Class (HAL 15.10) | bd today | Verdict |
|---|---|---|---|
| 1 | fresh worktree has no venv python | `lib/interpreter.py::resolve_project_python` climbs `git rev-parse --git-common-dir` to the main checkout's `.venv`; with none anywhere it returns `("", "none")` and emits `interpreter_unresolved` (warning), never raises; workers are told the absence is infrastructure | bd does not have it. Pin with a test on a real `git worktree add` |
| 2 | writer overflows context and overwrites a finished spec with a stub | `_invoke_spec_llm` UNLINKS `specs/build-spec.md` before every writer run, so the cycle-1 spec is gone before cycle 2 starts; `_write_spec_doc` then persists whatever the writer left (`worker_file`) with no size comparison. `_verify_spec_completeness` only requires >= 80 lines and `## Context`, so a 101-line stub passes | DEFECT |
| 3 | review cap exhausted, nothing usable left | at the cap `_gate_on_review` returns terminal `E_REVIEW_FAILED`; the spec file, the per-cycle spec copy, the review doc and the persisted findings thread stay on disk. The terminal result data names `review_path` but not `spec_path` | bd keeps the artifacts; gap: the terminal result does not point at the spec |

## 2. Change (class 2 and the class 3 gap)

A. `_invoke_spec_llm`: when cycle >= 2 and the guard is on and `doc_path` holds non-whitespace text, copy it to `<doc_path parent>/.build-spec.prior.md` BEFORE the unlink, and add `prior_spec_path` to the step data. Cycle 1 snapshots nothing (a fresh run must not be held to an old run's size).

B. `_write_spec_doc`: after `_resolve_spec_source`, when `prior_spec_path` is present and readable, the guard is on, and `len(raw.encode("utf-8")) < SPEC_SHRINK_RATIO * prior_bytes` (`SPEC_SHRINK_RATIO = 0.5`): restore the prior to the canonical path (atomic, byte-exact: copy / `read_bytes`, no newline translation), do NOT write the per-cycle copy, emit `spec_shrink_blocked` (`{cycle, prior_bytes, new_bytes}`), and return `_spec_gate_retry(...)` with `error_code="E_SPEC_INCOMPLETE"`, `terminal_error_code="E_SPEC_INCOMPLETE_FATAL"`, `retry_from_step_idx=0`, findings text naming both sizes and "REWRITE the FULL spec body". No new error code. A new text at or above the ratio (exactly 0.5 included) is written as before. A missing or unreadable prior fails open to today's write.
Implementation notes: `new_bytes` is measured on the resolved text BEFORE `_autoprefix_bare_citations`; the block return forwards `gate_attempts` the way the existing incomplete-return does; `prior_spec_path` must be added to the hand-built `extra_data` in `_invoke_spec_llm` (it is not carried over automatically). With the guard off, or a blank/absent canonical at cycle 2, no snapshot is made and no `prior_spec_path` is reported (a stale `.build-spec.prior.md` on disk is ignored).

C. Kill switch `HAL_SPEC_SHRINK_GUARD` (`kind: gate`, default `1`, `=0` restores today's behaviour), registered in `flags_catalog.py`.

D. `_gate_on_review` terminal branch: add `spec_path` (copied from `prev.data` when present) to `terminal_data`.

## 3. Acceptance criteria

AC1/AC2 are class 1 pins (GREEN today) and live in `engine_py/tests/test_bd109_class1_interpreter_pin.py`; AC3-AC9 are the RED tests in `test_bd109_build_failure_classes.py`.

- AC1: a venv-less linked worktree of a checkout that owns `.venv` resolves to that `.venv` python, `source != "none"`.
- AC2: a linked worktree with no venv in it or in the main checkout returns `("", "none")`, does not raise, and emits `interpreter_unresolved` with `severity == "warning"`.
- AC3: `_invoke_spec_llm` at cycle 2 leaves the old spec text in `.build-spec.prior.md` and reports `prior_spec_path`; at cycle 1 it creates no prior file.
- AC4: cycle 2, prior 300 lines, writer leaves a 101-line stub: `_write_spec_doc` returns a recoverable error `E_SPEC_INCOMPLETE`, the canonical file holds the prior text byte for byte, no `build-spec-cycle-2.md` exists, the event `spec_shrink_blocked` was emitted.
- AC5: same inputs with `HAL_SPEC_SHRINK_GUARD=0`: the stub is written (status ok, old behaviour).
- AC6: cycle 2, prior 300 lines, new text 280 lines (above the ratio): written, status ok.
- AC7: shrink at the retry cap (attempts spent) returns terminal `E_SPEC_INCOMPLETE_FATAL` with the prior text still on disk.
- AC8: `_gate_on_review`, REVISE at the cap: `E_REVIEW_FAILED`, spec file, review doc and findings thread still on disk, and `data["spec_path"]` equals the spec path.
- AC9: `HAL_SPEC_SHRINK_GUARD` is present in `flags_catalog.FLAGS`-style registry with default `"1"` (use the registry's real accessor; the flag-owner lint passes).

- AC10: prior 1000 constant-width lines: a 450-line new text is blocked, a 520-line text is written (pins the 0.5 ratio).
- AC11: new_bytes exactly 0.5 * prior_bytes is written (guard strictly `<`); one line less is blocked.
- AC12: raw_response source with a blank/absent canonical and no prior_spec_path: the short raw_response is written as before, no `spec_shrink_blocked`, no `prior_spec_path` in step data.
- AC13: `prior_spec_path` pointing at a missing or unreadable file fails open to today's write, no block.
- AC14: `HAL_SPEC_SHRINK_GUARD=0` at cycle 2: no `.build-spec.prior.md` is created, step data has no `prior_spec_path`.
- AC15: blank canonical at cycle 2 with a stale `.build-spec.prior.md` on disk: step data does not report it as `prior_spec_path`.
- AC4 uses a CRLF prior (written with `write_bytes`), so the restore must be byte-exact.

## Known limitations

A legitimate rewrite below 50% of the prior is blocked, retried once, then terminal `E_SPEC_INCOMPLETE_FATAL`. Escape: set `HAL_SPEC_SHRINK_GUARD=0` before the run. The `spec_gates` budget (`recoverable_once`, cap 1) is shared, so an earlier spec-gate spend makes the first shrink terminal.

## 4. Out of scope

Class 1 needs no production change. The Write tool's own overflow behaviour is not touched; this guards the engine's persist step.
