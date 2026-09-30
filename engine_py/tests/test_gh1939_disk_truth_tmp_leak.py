"""bytedigger port note: AC7 (two module instances of disk_truth under the flat
and package import names) is omitted — the package layout has one import name.

RED tests for GH1939 — disk_truth test_runner leaks one `disk_truth_*`
temp dir per `run_test_command` call, forever (Option-D spec, frozen at
SHARED/memory/Decisions/gh1939_disk_truth_tmp_leak_spec.md).

Every test launches a REAL `sys.executable` subprocess with a fresh, empty
TMPDIR, imports the real production module (no mocking of the unit under
test), drives it via `run_test_command`, and asserts on (a) JSON the driver
reports on stdout and (b) the `disk_truth_*` listing of TMPDIR after the
subprocess has exited. No module-level sys.path manipulation is needed here
(conftest.py already installs the conftest-import-time singleton per §1q);
this file only uses `sys` / `json` / `os` / `subprocess` at call time.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

TIMEOUT = 60

# Path fragments the driver subprocess needs on ITS OWN sys.path (separate
# process → separate interpreter → must set this up itself; this is not the
# forbidden "module-level sys.path mutation in the RED test file" pattern,
# since it lives inside a driver script written to disk, not in this module).
_ENGINE_ROOT = str(Path(__file__).parent.parent)
_LIB_DIR = str(Path(__file__).parent.parent / "lib")

_DRIVER_PREAMBLE = f"""
import sys
sys.path.insert(0, {_ENGINE_ROOT!r})
sys.path.insert(0, {_LIB_DIR!r})
import json, os
from bytedigger_engine.lib.plugins.disk_truth.test_runner import run_test_command
"""


def _disk_truth_entries(tmpdir: Path) -> list[str]:
    """Every TMPDIR-top-level entry name starting with disk_truth_."""
    return sorted(p.name for p in tmpdir.iterdir() if p.name.startswith("disk_truth_"))


def _run_driver(tmp_path: Path, body: str) -> tuple[dict, Path]:
    """Write a driver script combining the preamble + body, run it with a
    fresh empty TMPDIR, return (parsed stdout JSON, the TMPDIR path)."""
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER_PREAMBLE + textwrap.dedent(body))

    env = dict(os.environ)
    env["TMPDIR"] = str(tmpdir)

    proc = subprocess.run(
        [sys.executable, str(driver)],
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert proc.returncode == 0, (
        f"driver failed rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    return out, tmpdir


class TestGH1939DiskTruthTmpLeak:
    """AC1-6 of the frozen spec: each call's temp dir must live under ONE
    per-process root and that root must be gone after the process exits."""

    def test_ac1_zero_disk_truth_entries_after_process_exit(self, tmp_path: Path) -> None:
        """AC1: 3 calls, all stdout_paths read successfully inside the
        process, then normal exit -> zero disk_truth_* entries remain.
        FAILS TODAY: current code never removes tmp_dir, so each of the
        3 mkdtemp() calls leaves one directory behind forever."""
        body = """
            results = []
            for i in range(3):
                r = run_test_command([sys.executable, "-c", "print('hi')"], os.getcwd(), timeout=30)
                results.append(r)
                assert open(r.stdout_path).read() == "hi\\n"
            print(json.dumps({"ok": True}))
        """
        out, tmpdir = _run_driver(tmp_path, body)
        assert out["ok"] is True
        entries = _disk_truth_entries(tmpdir)
        assert entries == [], f"expected zero disk_truth_* entries after exit, found {entries}"

    def test_ac2_single_root_while_alive_with_distinct_call_dirs(self, tmp_path: Path) -> None:
        """AC2: while alive, after 3 calls, TMPDIR has exactly ONE
        disk_truth_* entry (the shared root), and all 3 stdout_paths are
        distinct and live under it. FAILS TODAY: current code creates one
        disk_truth_* dir PER CALL directly in TMPDIR, so this listing shows
        THREE top-level disk_truth_* entries, not one."""
        # All "while alive" checks (existence, under-root) are computed and
        # reported by the DRIVER ITSELF, synchronously, right after the 3
        # calls and before the process exits -- never re-checked from the
        # test process after the driver has already exited (that would race
        # against post-exit root cleanup and is not what AC2 asserts: AC2 is
        # about the state while the owning process is alive).
        body = """
            stdout_paths = []
            for i in range(3):
                r = run_test_command([sys.executable, "-c", "print('hi')"], os.getcwd(), timeout=30)
                stdout_paths.append(r.stdout_path)
            root_entries = sorted(
                n for n in os.listdir(os.environ["TMPDIR"]) if n.startswith("disk_truth_")
            )
            root_dir = (
                os.path.join(os.environ["TMPDIR"], root_entries[0])
                if len(root_entries) == 1 else None
            )
            existence = [os.path.exists(p) for p in stdout_paths]
            under_root = [
                (root_dir is not None and p.startswith(root_dir)) for p in stdout_paths
            ]
            print(json.dumps({
                "root_entries": root_entries,
                "stdout_paths": stdout_paths,
                "existence": existence,
                "under_root": under_root,
            }))
        """
        out, _tmpdir = _run_driver(tmp_path, body)
        root_entries = out["root_entries"]
        assert len(root_entries) == 1, (
            f"expected exactly one disk_truth_* root while alive, found {root_entries}"
        )
        paths = out["stdout_paths"]
        assert len(set(paths)) == 3, f"expected 3 distinct stdout_paths, got {paths}"
        assert all(out["existence"]), f"not all stdout_paths existed while alive: {out}"
        assert all(out["under_root"]), f"not all stdout_paths located under the single root: {out}"

    def test_ac3_per_call_output_isolation(self, tmp_path: Path) -> None:
        """AC3: stdout_path/stderr_path content matches exactly what each
        call produced; call 2's files don't contain call 1's output.
        Passes on current code today too (green-on-current: regression
        guard) — current code already isolates per-call content since each
        call gets its own mkdtemp(); this AC protects that behavior across
        the GREEN refactor to a shared root."""
        body = """
            r1 = run_test_command(
                [sys.executable, "-c", "import sys; print('AAA'); sys.stderr.write('EEE')"],
                os.getcwd(), timeout=30,
            )
            r2 = run_test_command(
                [sys.executable, "-c", "import sys; print('BBB'); sys.stderr.write('FFF')"],
                os.getcwd(), timeout=30,
            )
            out1 = open(r1.stdout_path).read()
            out2 = open(r2.stdout_path).read()
            err1 = open(r1.stderr_path).read()
            err2 = open(r2.stderr_path).read()
            print(json.dumps({
                "out1": out1, "out2": out2, "err1": err1, "err2": err2,
            }))
        """
        out, _tmpdir = _run_driver(tmp_path, body)
        assert out["out1"] == "AAA\n"
        assert out["out2"] == "BBB\n"
        assert out["err1"] == "EEE"
        assert out["err2"] == "FFF"
        assert "BBB" not in out["out1"]
        assert "AAA" not in out["out2"]

    def test_ac4_timeout_path_leaves_zero_entries_after_exit(self, tmp_path: Path) -> None:
        """AC4: timeout=1 against a 5s-sleeping command -> exit_code 124,
        stdout/stderr paths lie under the SINGLE disk_truth_* root present
        while the process is alive (captured in-process, right after the
        call returns), and after process exit zero disk_truth_* entries
        remain.
        FAILS TODAY: the TimeoutExpired branch returns early but never
        removes tmp_dir either — leaked dir survives exit regardless of
        which return path is taken; also current code has no shared root at
        all (one dir per call), so the 'exactly one root while alive'
        premise itself fails."""
        body = """
            r = run_test_command(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                os.getcwd(), timeout=1,
            )
            root_entries = sorted(
                n for n in os.listdir(os.environ["TMPDIR"]) if n.startswith("disk_truth_")
            )
            print(json.dumps({
                "exit_code": r.exit_code,
                "stdout_path": r.stdout_path,
                "stderr_path": r.stderr_path,
                "root_entries": root_entries,
            }))
        """
        out, tmpdir = _run_driver(tmp_path, body)
        assert out["exit_code"] == 124
        root_entries = out["root_entries"]
        assert len(root_entries) == 1, (
            f"expected exactly one disk_truth_* root while alive, found {root_entries}"
        )
        root_dir = str(tmpdir / root_entries[0])
        assert out["stdout_path"].startswith(root_dir), "stdout_path not under the single root"
        assert out["stderr_path"].startswith(root_dir), "stderr_path not under the single root"
        entries = _disk_truth_entries(tmpdir)
        assert entries == [], f"expected zero disk_truth_* entries after timeout+exit, found {entries}"

    def test_ac5_fork_safety_parent_stdout_survives_child_exit(self, tmp_path: Path) -> None:
        """AC5: parent calls once; forks; child calls once and exits via
        sys.exit(0) (so atexit runs in the child); after the child is
        reaped, the parent's FIRST stdout_path must still exist and be
        readable, and after the parent itself exits zero disk_truth_*
        entries remain.
        FAILS TODAY: current code has no atexit cleanup at all, so this
        specific victim (a child's atexit handler deleting the PARENT's
        shared root out from under it) cannot be exercised — GREEN must add
        the pid-guard (design step 3) for the parent's file to survive; a
        naive `atexit: rmtree(root)` with no pid check would delete the
        parent's live file when the child's atexit runs, redenning this
        assertion post-naive-GREEN too (that's the whole point of AC5)."""
        body = """
            r_parent = run_test_command(
                [sys.executable, "-c", "print('parent-call')"], os.getcwd(), timeout=30,
            )
            pid = os.fork()
            if pid == 0:
                # child
                run_test_command([sys.executable, "-c", "print('child-call')"], os.getcwd(), timeout=30)
                sys.exit(0)
            else:
                _, status = os.waitpid(pid, 0)
                survived = os.path.exists(r_parent.stdout_path)
                content = open(r_parent.stdout_path).read() if survived else None
                print(json.dumps({
                    "child_status": status,
                    "survived": survived,
                    "content": content,
                }))
        """
        out, tmpdir = _run_driver(tmp_path, body)
        assert os.WIFEXITED(out["child_status"]) and os.WEXITSTATUS(out["child_status"]) == 0, (
            f"child exit status must be 0, got raw waitpid status {out['child_status']}"
        )
        assert out["survived"] is True, "parent's stdout_path was removed by child's atexit"
        assert out["content"] == "parent-call\n"
        entries = _disk_truth_entries(tmpdir)
        assert entries == [], f"expected zero disk_truth_* entries after parent exit, found {entries}"

    def test_ac6_external_removal_of_root_recovers_on_next_call(self, tmp_path: Path) -> None:
        """AC6: after one call, the test rmtree's the single disk_truth_*
        root out from under the process; the NEXT call must still succeed
        and return a readable stdout_path, and after exit zero disk_truth_*
        entries remain.
        FAILS TODAY: current code has no shared root at all (one dir per
        call), so this AC exercises a code path (`os.path.isdir(root)`
        recreate-if-missing) that does not exist yet; on current code the
        rmtree just deletes call 1's own private dir and call 2 gets an
        entirely independent fresh dir regardless — but the FINAL assertion
        (zero disk_truth_* entries after exit) still fails today because
        call 2's own leaked dir survives the process exit."""
        body = """
            import shutil
            r1 = run_test_command([sys.executable, "-c", "print('one')"], os.getcwd(), timeout=30)
            root_entries_before = sorted(
                n for n in os.listdir(os.environ["TMPDIR"]) if n.startswith("disk_truth_")
            )
            # Remove the (single, per spec design) root out from under the process.
            for name in root_entries_before:
                shutil.rmtree(os.path.join(os.environ["TMPDIR"], name), ignore_errors=True)
            r2 = run_test_command([sys.executable, "-c", "print('two')"], os.getcwd(), timeout=30)
            content2 = open(r2.stdout_path).read()
            print(json.dumps({
                "root_entries_before": root_entries_before,
                "content2": content2,
            }))
        """
        out, tmpdir = _run_driver(tmp_path, body)
        assert out["content2"] == "two\n"
        entries = _disk_truth_entries(tmpdir)
        assert entries == [], f"expected zero disk_truth_* entries after exit, found {entries}"

