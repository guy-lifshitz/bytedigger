# bd#192 gate r2: post-freeze fixture fix in `_run_green`

## Scope

Narrow round. It audits one change made to a frozen RED test file after round 1 approved it and after the implementation landed (`09c082a`): commit `48da292`, two lines in the test helper `_run_green` of `engine_py/tests/test_bd192_tail_source_id.py`.

```
-    runs.mkdir(exist_ok=True)
-    work.mkdir(exist_ok=True)
+    runs.mkdir(parents=True, exist_ok=True)
+    work.mkdir(parents=True, exist_ok=True)
```

Everything else in round 1 (`docs/decisions/2026-10-10-bd192-tail-source-id-gate-r1.md`) stands and is not re-audited here. Method: code reading only. No test and no git command was run; the limits this puts on the conclusions are stated in "Not verified by reading".

## 1. Was the AC9 failure a fixture defect?

Yes.

- `test_ac9_auth_modes_give_identical_declarations` passes `run_dir = tmp_path / "t1"` to the helper (test file L662-663). Nothing creates `t1` before that call: the only earlier filesystem work in the test is `_run_probe(tmp_path / "probe", ...)` (L657), which creates `probe/work/tests` (L319-320).
- The helper then builds `runs = tmp_path / "runs"` and `work = tmp_path / "work"` (L214-215) and creates them. With the pre-fix form, `Path.mkdir(exist_ok=True)` on `<tmp>/t1/runs` raises `FileNotFoundError` because the parent `t1` is missing; `exist_ok` does not cover a missing parent.
- The raise happens at L216, before `monkeypatch.setattr(p5, "run_test_command", ...)` (L233) and before the call into production code, `p5._verify_green_passing(ctx, prev)` (L252). No production line runs first, so no implementation, correct or not, could have changed the outcome.
- Every other caller passes the pytest `tmp_path` itself, which exists: L432, L438, L462, L501, L510, L512, L523. For those the old and new forms behave identically. The second call in `test_ac3_on_and_off_share_the_findings_text` (L512) reuses existing directories and was already covered by `exist_ok=True`.
- Corroboration of the pre-fix text: the sibling helper in `engine_py/tests/test_bd192_tail_source_id_pins.py` L165-166 still has the old form `runs.mkdir(exist_ok=True)` / `work.mkdir(exist_ok=True)`. Its two callers (L340, L356) pass `tmp_path` directly, so it has no defect and needs no change.

Consequence for round 1: its statement that every RED test "reaches its assertions" was wrong for AC9. Before the implementation existed, AC9 failed in the fixture at L216, not at its intended assertion (L665, `green_test_chunks missing`). See F1.

## 2. Does the change weaken any assertion?

No.

- The two directories are fixture-owned on both sides of the replaced boundary. `runs/` holds the capture files that the fake `run_test_command` hands back (L218-223, L225-231); in production those files are written by the real test runner, which is the boundary the spec allows the tests to replace (spec "Acceptance" preamble). `work/` is only the value of `org_config["git_cwd"]` (L236).
- `_verify_green_passing` creates neither directory and is not specified to. It resolves `git_cwd` (`engine_py/bytedigger_engine/workflows/phase_5_implement.py` L5332), calls `run_test_command` (L5360, L5408), and reads the capture file through `_read_text_or_empty` (L5438; definition L4863-4869, read-only, returns `""` on `OSError`). The new code on this path only copies the path string (L5441-5442) and computes chunks from strings in memory (L5569-5577).
- No acceptance criterion in the spec asserts that a directory is created, is absent, or is rejected. A search of the test file finds no assertion on directory existence and no test that expects a failure from a missing directory. So there is no test that the added `parents=True` could turn from failing into vacuously passing.
- The fix does not create anything the production code is expected to create. The three other fixtures in the same file already use `parents=True` (L320, L353, L354); the helper is now consistent with them.
- AC9 is not made easier. After the fix it still requires `green_test_chunks` (L664-665), the bound record with the chunks closing it (L667, L675), a successful dispatch with one attested event per prompt (L668, L676), and equality of the two per-mode summaries (L680). In the second loop iteration the same `t1` files are rewritten with the same text, which is what the comment at L662 intends: equal source ids in both modes.
- With the fix applied and the implementation absent, AC9 fails at L665 on the missing key, which is the reason round 1 expected.

## 3. Does the diff contain anything else?

No, as far as the supplied diff shows. It has one hunk in one file, two removed and two added lines, and the only difference in each pair is the added `parents=True,` argument. No assertion, expected value, parameter matrix, fixture data or import changes. The current file at L216-217 matches the post-image of the hunk, and the test count in the file is still 31, as round 1 recorded.

## 4. Is the spec unchanged since round 1?

Consistent with unchanged, by reading. `docs/decisions/2026-10-10-bd192-tail-source-id.md` still carries every wording that round 1 quoted and asked to have corrected: the status line `r1, TIER3`; "`BD_HAL...` alias" in AC3; groups "(`py`, `bun`)" in AC1; "in a temp git repo" in the Acceptance preamble; "`collect-probe:` + path" in AC6; "longest suffix of that part's text that survived" and "A single-source tail (T2) is one chunk with the same rule" in the chunk rule. None of the round 1 advisory edits has been applied, and nothing else in the text departs from what round 1 describes.

## Not verified by reading

- Byte identity of the spec with the round 1 state. Reading shows no difference; only a content hash compared with the round 1 anchor proves it.
- That commit `48da292` touches no file other than the one in the supplied diff. A file list of the commit settles this.
- Test outcomes. The claims above about where each test fails or passes come from the code, not from a run.

For the same reason this report carries no hand-written hash. The anchor block is to be produced by the hashing step from the files on disk; the RED file hash necessarily differs from round 1, the spec hash must equal it.

## Adversarial edges

none. The change touches fixture setup only and introduces no new behaviour to probe. The edges listed in round 1 are unaffected.

## Findings

1. F1 (MINOR): Round 1 missed that AC9 failed in fixture setup rather than at an assertion, so the RED evidence for AC9 was a failure for the wrong reason. The fix restores the intended failure point (L665). No action beyond this record.
2. F2 (MINOR): The sibling helper in `test_bd192_tail_source_id_pins.py` L165-166 keeps the form without `parents=True`. It is correct for its current callers; align it only if a future caller passes a nested directory.
3. F3 (MINOR): Confirm by hash that the spec equals its round 1 state and by file list that `48da292` changes only the one test file, before relying on this approval.

No MAJOR finding.

## Checklist

Carried from round 1; only AC9 is affected by the audited change and was re-examined.

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd192-tail-source-id.md sha256:805525e7bc44a8186ccb0ca23b7526d7ce7fb02810313705c7c5526732bd1b84
red: engine_py/tests/test_bd192_tail_source_id.py sha256:25245d75fb34a9366e92a129960f5a3614cead0c565fc0967355abb38bf8172d
-->
