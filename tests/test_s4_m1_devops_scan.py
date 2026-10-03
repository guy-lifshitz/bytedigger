"""RED tests for scripts/devops_scan.py (S4/M1, spec r3
docs/decisions/2026-10-03-s4-m1-devops-scan-script.md, AC1-AC11; gate r1/r2 fixes
in docs/decisions/2026-10-03-s4-m1-gate-r1.md and -gate-r2.md).

The script under test does not exist yet. Per workflows.md §1q nothing here
imports it: every test resolves the path lazily and invokes it as a
subprocess (sys.executable) inside the test body, so collection succeeds and
failure happens at assert time. Repo root is the parent of this tests/ dir.

Hermetic: fake `trivy` / `hadolint` shell shims live in tmp_path/bin; PATH is
that dir plus /usr/bin:/bin (no claude, no bun); ANTHROPIC_API_KEY is absent;
HOME is a tmp dir. Git repos are built under tmp_path (git init + add).
No singleton resources or timing races: the timeout case uses a shim that
`exec sleep 30` against `--timeout 1` (the shim can never finish first).
"""
from __future__ import annotations

import ast
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "devops_scan.py"

VERDICT_KEYS = {"status", "gating", "waived", "nongating", "reason"}
FINDING_KEYS = {"scanner", "file", "id", "severity", "description"}
STATUS_RC = {"clean": 0, "nothing_to_scan": 0, "blocked": 1, "unavailable": 2}


# ---------------------------------------------------------------- helpers --

_SHIM_HEAD = """#!/bin/sh
D="$(dirname "$0")"
echo "$@" >> "$D/@N@.calls"
echo "cwd=$(pwd -P) home=$HOME" >> "$D/@N@.env"
echo "--run--" >> "$D/@N@.fullenv"
/usr/bin/env >> "$D/@N@.fullenv"
prev=""
for a in "$@"; do
  if [ "$prev" = "--ignorefile" ]; then
    if [ -f "$a" ]; then
      echo "ignorefile $a size $(wc -c < "$a" | tr -d ' ')" >> "$D/@N@.env"
    else
      echo "ignorefile $a missing" >> "$D/@N@.env"
    fi
  fi
  prev="$a"
done
"""


def _shim(bin_dir: Path, name: str, out: str = "", rc: int = 0, sleep=None, body=None) -> None:
    """Write an executable fake scanner. Logs argv to <name>.calls and
    cwd/HOME/--ignorefile state to <name>.env. `body` (sh) replaces the
    default `cat <name>.out; exit rc` tail (used for argv-switching shims)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / f"{name}.out").write_text(out)
    text = _SHIM_HEAD.replace("@N@", name)
    if sleep:
        text += f"exec sleep {sleep}\n"
    text += body if body is not None else f'cat "$D/{name}.out"\nexit {rc}\n'
    path = bin_dir / name
    path.write_text(text)
    path.chmod(0o755)


def _calls(bin_dir: Path, name: str) -> list:
    f = bin_dir / f"{name}.calls"
    return f.read_text().splitlines() if f.exists() else []


def _envlog(bin_dir: Path, name: str) -> list:
    f = bin_dir / f"{name}.env"
    return f.read_text().splitlines() if f.exists() else []


def _fullenv(bin_dir: Path, name: str) -> list:
    """One dict per shim invocation: the full environment the scanner received."""
    f = bin_dir / f"{name}.fullenv"
    runs: list = []
    if f.exists():
        for ln in f.read_text().splitlines():
            if ln == "--run--":
                runs.append({})
            elif "=" in ln and runs:
                k, _, val = ln.partition("=")
                runs[-1][k] = val
    return runs


def _under(path: str, root: Path) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(str(root))
    return p == r or p.startswith(r + os.sep)


def _hl(*items) -> str:
    """hadolint -f json output: items are (code, level)."""
    return json.dumps(
        [
            {"code": c, "level": lvl, "message": f"msg {c}", "file": "Dockerfile", "line": 1, "column": 1}
            for c, lvl in items
        ]
    )


def _tv(*items, target="main.tf") -> str:
    """trivy config --format json output: items are (id, severity)."""
    return json.dumps(
        {
            "Results": [
                {
                    "Target": target,
                    "Class": "config",
                    "Misconfigurations": [
                        {"ID": i, "Severity": s, "Title": f"t {i}", "Description": f"desc {i}"}
                        for i, s in items
                    ],
                }
            ]
        }
    )


def _hl_raw(*items) -> str:
    """hadolint json output from raw dicts (for malformed/missing fields)."""
    return json.dumps(list(items))


def _tv_raw(*miscs, target="main.tf") -> str:
    """trivy json output from raw Misconfiguration dicts."""
    return json.dumps({"Results": [{"Target": target, "Class": "config", "Misconfigurations": list(miscs)}]})


def _make_repo(tmp_path: Path, files: dict, git: bool = True, add: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        if add:
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    return root


def _env(tmp_path: Path, bin_dir: Path) -> dict:
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LANG": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
    }


def _run(tmp_path: Path, root, *args, bin_dir=None, env_extra=None):
    # An absent script makes the interpreter itself exit 2, which would
    # satisfy every F-case by accident; fail loudly instead.
    assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
    bin_dir = bin_dir if bin_dir is not None else tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    cwd = tmp_path / "neutral-cwd"
    cwd.mkdir(exist_ok=True)
    env = _env(tmp_path, bin_dir)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
        env=env,
    )


def _run_raw(tmp_path: Path, *args):
    """Run the script with exactly `args` (no implicit --root): usage errors."""
    assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
    (tmp_path / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    cwd = tmp_path / "neutral-cwd"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
        env=_env(tmp_path, tmp_path / "bin"),
    )


def _verdict(r) -> dict:
    """Parse stdout as exactly ONE JSON verdict object; check shape and that
    exit code agrees with status."""
    assert "Traceback" not in r.stderr, r.stderr
    try:
        v = json.loads(r.stdout)
    except ValueError as e:
        raise AssertionError(f"stdout is not a single JSON object: {r.stdout!r} ({e})")
    assert isinstance(v, dict) and set(v) == VERDICT_KEYS, f"bad verdict keys: {v!r}"
    assert v["status"] in STATUS_RC, v
    assert isinstance(v["reason"], str)
    for k in ("gating", "waived", "nongating"):
        assert isinstance(v[k], list), v
        for f in v[k]:
            assert set(f) == FINDING_KEYS, f"bad finding keys: {f!r}"
    assert r.returncode == STATUS_RC[v["status"]], (r.returncode, v)
    return v


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if ".git" in rel.parts:
            continue
        h.update(str(rel).encode() + (b"/D" if p.is_dir() else b"/F"))
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _allow(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "allow.txt"
    p.write_text("\n".join(lines) + "\n")
    return p


class _UtcDay:
    """UTC date + delta days, evaluated when formatted (inside the test body),
    never at import time (gate r2 minor 9; spec: "today" is the UTC date)."""

    def __init__(self, delta: int):
        self.delta = delta

    def __str__(self) -> str:
        now = datetime.datetime.now(datetime.timezone.utc).date()
        return (now + datetime.timedelta(days=self.delta)).isoformat()

    def __format__(self, spec: str) -> str:
        return format(str(self), spec)


TODAY = _UtcDay(0)
FUTURE = _UtcDay(30)
EDGE = _UtcDay(365)
LONG = _UtcDay(400)
PAST = _UtcDay(-1)

DOCKERFILE = "FROM ubuntu:latest\n"
MAIN_TF = 'resource "aws_s3_bucket" "b" {}\n'


class TestS4M1DevopsScan:
    # ------------------------------------------------------------- AC1 ---
    def test_ac1_dockerfile_hadolint_error_blocks_exit1_high(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "blocked"
        assert len(v["gating"]) == 1 and not v["waived"] and not v["nongating"]
        f = v["gating"][0]
        assert f["severity"] == "HIGH"
        assert f["id"] == "DL3006" and f["scanner"] == "hadolint"
        assert "Dockerfile" in f["file"]
        calls = _calls(bin_dir, "hadolint")
        assert calls, "hadolint was never invoked"
        words = calls[0].split()
        assert ("-f" in words or "--format" in words) and "json" in words, calls

    def test_ac1_hadolint_no_findings_is_clean_exit0(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        v = _verdict(r)
        assert r.returncode == 0 and v["status"] == "clean"
        assert _calls(bin_dir, "hadolint"), "hadolint must have run (not nothing_to_scan)"

    @pytest.mark.parametrize(
        "name",
        [
            "Dockerfile",
            "Dockerfile.dev",
            "app.Dockerfile",
            "sub/Dockerfile.prod",
            "Containerfile",
            "Containerfile.prod",
        ],
    )
    def test_ac1_dockerfile_name_variants_routed_to_hadolint(self, tmp_path, name):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {name: DOCKERFILE})
        r = _run(tmp_path, root)
        v = _verdict(r)
        assert r.returncode == 1 and v["status"] == "blocked", (name, r.stdout)
        assert _calls(bin_dir, "hadolint")

    # ------------------------------------------------------------- AC2 ---
    def test_ac2_iac_trivy_critical_blocks_exit1(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-AWS-0001", "CRITICAL")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "blocked"
        assert [f["id"] for f in v["gating"]] == ["AVD-AWS-0001"]
        assert v["gating"][0]["severity"] == "CRITICAL"
        assert v["gating"][0]["scanner"] == "trivy"
        assert "main.tf" in v["gating"][0]["file"]

    def test_ac2_trivy_medium_only_is_nongating_exit0(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-AWS-0002", "MEDIUM")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "clean"
        assert not v["gating"] and not v["waived"]
        assert [f["id"] for f in v["nongating"]] == ["AVD-AWS-0002"]
        assert _calls(bin_dir, "trivy"), "trivy must have run"

    @pytest.mark.parametrize(
        "name",
        [
            "main.tf",
            "main.tf.json",
            "infra/net.tf.json",
            "prod.tfvars",
            "docker-compose.yml",
            "docker-compose.prod.yaml",
            "compose.yaml",
            "compose.yml",
            "k8s/deploy.yaml",
            "kubernetes/svc.yml",
            "helm/values.yaml",
            "charts/app/templates/dep.yaml",
            "manifests/a.yml",
            "Chart.yaml",
        ],
    )
    def test_ac2_iac_path_variants_routed_to_trivy(self, tmp_path, name):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=0)
        root = _make_repo(tmp_path, {name: "x: 1\n"})
        r = _run(tmp_path, root)
        v = _verdict(r)
        assert r.returncode == 1 and v["status"] == "blocked", (name, r.stdout)
        assert _calls(bin_dir, "trivy")

    def test_ac2_trivy_called_with_config_json_quiet_and_file_dir(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "LOW")), rc=0)
        root = _make_repo(tmp_path, {"infra/main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        _verdict(r)
        calls = _calls(bin_dir, "trivy")
        assert calls, "trivy was never invoked"
        words = calls[0].split()
        assert words[0] == "config", calls
        assert "--format" in words and "json" in words and "--quiet" in words, calls
        assert any(w.rstrip("/").endswith("infra") for w in words), (
            f"trivy must be pointed at the directory of the file: {calls}"
        )

    @pytest.mark.parametrize("name", ["a.py", "README.md", "config.yaml", "docs/x.yml", "src/app.yaml"])
    def test_ac2_non_iac_files_are_not_scanned(self, tmp_path, name):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=0)
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {name: "x: 1\n"})
        r = _run(tmp_path, root)
        v = _verdict(r)
        assert r.returncode == 0 and v["status"] == "nothing_to_scan", (name, r.stdout)
        assert not _calls(bin_dir, "trivy") and not _calls(bin_dir, "hadolint")

    # ------------------------------------------------------------- AC3 ---
    def test_ac3_f1_dockerfile_without_hadolint_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"] == "hadolint_not_found"

    def test_ac3_f1_even_when_trivy_present_and_iac_clean(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(), rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE, "main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout)
        v = _verdict(r)
        assert v["reason"] == "hadolint_not_found"

    def test_ac3_f2_iac_without_trivy_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"] == "trivy_not_found"

    def test_ac3_f2_even_when_hadolint_present(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE, "main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout)
        assert _verdict(r)["reason"] == "trivy_not_found"

    def test_ac3_f4_only_python_nothing_to_scan_without_binaries(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        root = _make_repo(tmp_path, {"a.py": "print(1)\n"})
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "nothing_to_scan"
        assert not v["gating"] and not v["waived"] and not v["nongating"]

    # ------------------------------------------------------------- AC4 ---
    def test_ac4_f3_hadolint_timeout_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0, sleep=30)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root, "--timeout", "1")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "timeout" in v["reason"]

    def test_ac4_f3_trivy_timeout_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(), rc=0, sleep=30)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root, "--timeout", "1")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "timeout" in _verdict(r)["reason"]

    def test_ac4_f3_hadolint_rc3_subprocess_error_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=3)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "subprocess_error" in v["reason"]
        assert not v["gating"], "findings from a failed scanner run must not be trusted"

    def test_ac4_f3_trivy_rc1_is_outside_documented_rc_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=1)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "subprocess_error" in _verdict(r)["reason"]

    def test_ac4_f3_hadolint_not_json_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "not json", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "json_decode_error" in v["reason"]

    def test_ac4_f3_trivy_not_json_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", "not json", rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "json_decode_error" in _verdict(r)["reason"]

    @pytest.mark.parametrize("out", ['{"not": "a list"}', '"x"', '["x"]', "[1]", "[null]"])
    def test_ac4_f3_f7_hadolint_wrong_shape_alone_exit2(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", out, rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (out, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "json_shape_error" in v["reason"], v

    @pytest.mark.parametrize(
        "out",
        [
            '{"Results": {}}',
            '{"Results": "x"}',
            '{"Results": [{"Target": "main.tf", "Misconfigurations": "x"}]}',
            '{"Results": [{"Target": "main.tf", "Misconfigurations": {}}]}',
            '{"Results": [{"Target": "main.tf", "Misconfigurations": ["str"]}]}',
        ],
    )
    def test_ac4_f3_f7_trivy_wrong_shape_alone_exit2(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", out, rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (out, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "json_shape_error" in v["reason"], v

    @pytest.mark.parametrize("out", ["[]", '"x"', "7"])
    def test_ac4_f3_trivy_non_object_toplevel_alone_exit2(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", out, rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (out, r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "unavailable"

    # ------------------------------------------------------------- AC5 ---
    def _blocked_dockerfile_repo(self, tmp_path, *items):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(*(items or (("DL3006", "error"),))), rc=1)
        return _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})

    def test_ac5_unexpired_matching_waiver_exit0_listed_under_waived(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(tmp_path, f"DL3006 :: ABCDEF12 :: kill-by:{FUTURE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "clean"
        assert not v["gating"]
        assert [f["id"] for f in v["waived"]] == ["DL3006"], "waived findings must be listed, never dropped"

    def test_ac5_comments_and_blank_lines_skipped_valid_line_still_waives(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(
            tmp_path, "# a comment", "", "   ", f"DL3006 :: ABCDEF12 :: kill-by:{FUTURE}"
        )
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert [f["id"] for f in _verdict(r)["waived"]] == ["DL3006"]

    def test_ac5_kill_by_today_still_waives_inclusive(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(tmp_path, f"DL3006 :: ABCDEF12 :: kill-by:{TODAY}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["waived"]

    def test_ac5_pattern_matches_substring_of_joined_fields(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(tmp_path, f"hadolint|Dockerfile|DL3006 :: ABCDEF12 :: kill-by:{FUTURE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["waived"]

    def test_ac5_expired_kill_by_waives_nothing_exit1(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(tmp_path, f"DL3006 :: ABCDEF12 :: kill-by:{PAST}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "blocked" and v["gating"] and not v["waived"]

    @pytest.mark.parametrize(
        "line",
        [
            "garbage line without separators",
            "DL3006 :: kill-by:{FUTURE}",
            "DL3006 :: NOTHEX!! :: kill-by:{FUTURE}",
            "DL3006 :: ABCDEF12 :: kill-by:not-a-date",
            "DL3006 :: ABCDEF12 :: kill-by:2099-13-45",
            "DL3006 :: ABCDEF12",
            " :: ABCDEF12 :: kill-by:{FUTURE}",
            "    :: ABCDEF12 :: kill-by:{FUTURE}",
            "DL3006 :: ABCDEF1 :: kill-by:{FUTURE}",
            "DL3006 :: ABCDEF123 :: kill-by:{FUTURE}",
            "DL3006 :: ABCDEF12 :: kill-by:{LONG}",
        ],
    )
    def test_ac5_malformed_line_waives_nothing_never_raises(self, tmp_path, line):
        line = line.format(FUTURE=FUTURE, LONG=LONG)  # dates computed at test time
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(tmp_path, line)
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 1, (line, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "blocked" and v["gating"] and not v["waived"]

    def test_ac5_non_matching_pattern_waives_nothing_exit1(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(tmp_path, f"DL9999 :: ABCDEF12 :: kill-by:{FUTURE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["gating"]

    def test_ac5_waiver_is_per_finding_other_gating_still_blocks(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path, ("DL3006", "error"), ("DL3007", "error"))
        allow = _allow(tmp_path, f"DL3006 :: ABCDEF12 :: kill-by:{FUTURE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [f["id"] for f in v["gating"]] == ["DL3007"]
        assert [f["id"] for f in v["waived"]] == ["DL3006"]

    def test_ac5_waiver_does_not_apply_to_nongating_findings(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path, ("DL3008", "warning"))
        allow = _allow(tmp_path, f"DL3008 :: ABCDEF12 :: kill-by:{FUTURE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [f["id"] for f in v["nongating"]] == ["DL3008"] and not v["waived"]

    def test_ac5_f5_unreadable_explicit_allowlist_exit2(self, tmp_path):
        root = self._blocked_dockerfile_repo(tmp_path)
        r = _run(tmp_path, root, "--allowlist", str(tmp_path / "does-not-exist.txt"))
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)  # F5: a JSON verdict, not a bare argparse/usage error
        assert v["status"] == "unavailable" and v["reason"], v

    # ------------------------------------------------------------- AC6 ---
    def test_ac6_unknown_trivy_severity_treated_high_exit1(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-9", "WEIRD")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [f["severity"] for f in v["gating"]] == ["HIGH"]

    def test_ac6_fail_on_critical_only_high_is_nongating_exit0(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "HIGH")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root, "--fail-on", "CRITICAL")
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert not v["gating"] and [f["id"] for f in v["nongating"]] == ["AVD-X-1"]
        # control: the same finding gates under the default set
        r2 = _run(tmp_path, root)
        assert r2.returncode == 1, (r2.returncode, r2.stdout)

    def test_ac6_fail_on_is_case_insensitive(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "HIGH")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root, "--fail-on", "critical,high")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["gating"]

    def test_ac6_hadolint_level_mapping(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(
            bin_dir,
            "hadolint",
            _hl(("DL1", "error"), ("DL2", "warning"), ("DL3", "info"), ("DL4", "style")),
            rc=1,
        )
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert {f["id"]: f["severity"] for f in v["gating"]} == {"DL1": "HIGH"}
        assert {f["id"]: f["severity"] for f in v["nongating"]} == {
            "DL2": "MEDIUM",
            "DL3": "LOW",
            "DL4": "LOW",
        }

    def test_ac6_hadolint_warning_gates_when_fail_on_medium(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL2", "warning")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root, "--fail-on", "MEDIUM")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert [f["severity"] for f in _verdict(r)["gating"]] == ["MEDIUM"]

    # ------------------------------------------------------------- AC7 ---
    def test_ac7_files_readme_only_nothing_to_scan_despite_bad_dockerfile(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE, "README.md": "hi\n"})
        r = _run(tmp_path, root, "--files", "README.md")
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "nothing_to_scan"
        assert not _calls(bin_dir, "hadolint")
        # control: without --files the same repo blocks
        r2 = _run(tmp_path, root)
        assert r2.returncode == 1, (r2.returncode, r2.stdout)

    def test_ac7_files_selects_listed_dockerfile_without_git(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE, "README.md": "x\n"}, git=False)
        r = _run(tmp_path, root, "--files", "README.md,Dockerfile")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "blocked"

    def test_ac7_files_outside_root_and_missing_ignored_no_crash(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=0)
        (tmp_path / "Dockerfile").write_text(DOCKERFILE)  # the "../Dockerfile" target
        (tmp_path / "outside.tf").write_text(MAIN_TF)
        root = _make_repo(tmp_path, {"README.md": "x\n"}, git=False)
        r = _run(
            tmp_path,
            root,
            "--files",
            f"../Dockerfile,../outside.tf,{tmp_path / 'outside.tf'},Dockerfile.nope,missing/main.tf",
        )
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "nothing_to_scan"
        assert "5" in v["reason"], f"reason must state the dropped-entry count (5): {v['reason']!r}"
        assert not _calls(bin_dir, "hadolint") and not _calls(bin_dir, "trivy")

    def test_ac7_no_files_scans_only_git_tracked_candidates(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE}, git=True, add=False)
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "nothing_to_scan"
        assert not _calls(bin_dir, "hadolint")
        subprocess.run(["git", "add", "Dockerfile"], cwd=root, check=True, capture_output=True)
        r2 = _run(tmp_path, root)
        assert r2.returncode == 1, (r2.returncode, r2.stdout)

    def test_ac7_f5_nonexistent_root_exit2(self, tmp_path):
        r = _run(tmp_path, tmp_path / "no-such-root")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"], v

    @pytest.mark.parametrize("extra", [["--bogus-flag"], ["--timeout", "abc"], ["--root"]])
    def test_ac7_f5_usage_error_emits_unavailable_verdict(self, tmp_path, extra):
        r = _run_raw(tmp_path, *extra)
        assert r.returncode == 2, (extra, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"], v

    def test_ac7_f5_missing_root_argument_emits_unavailable_verdict(self, tmp_path):
        r = _run_raw(tmp_path)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and v["reason"], v

    # ------------------------------------------------------------- AC8 ---
    def test_ac8_static_no_engine_import_no_claude_bun_ts_no_hal_env_exit_codes_closed(self):
        assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.startswith("bytedigger_engine"), a.name
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("bytedigger_engine"), node.module
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value
                assert s not in {"claude", "bun"}, f"forbidden argv literal {s!r}"
                assert not s.endswith(".ts"), f"forbidden .ts literal {s!r}"
                assert not s.startswith("HAL_"), f"forbidden HAL_ env literal {s!r}"

        mains = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"]
        assert len(mains) == 1, "exactly one main() must produce the verdict"
        # module-level int constants (EXIT_BLOCKED = 1 / EXIT_BLOCKED: int = 1)
        consts = {}
        for n in tree.body:
            tgts, val = [], None
            if isinstance(n, ast.Assign):
                tgts, val = n.targets, n.value
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                tgts, val = [n.target], n.value
            if isinstance(val, ast.Constant) and isinstance(val.value, int) and not isinstance(val.value, bool):
                for t in tgts:
                    if isinstance(t, ast.Name):
                        consts[t.id] = val.value

        def _code(a):
            if isinstance(a, ast.Constant) and isinstance(a.value, int) and not isinstance(a.value, bool):
                return a.value
            if isinstance(a, ast.Name) and a.id in consts:
                return consts[a.id]
            return None

        codes = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in {"exit", "SystemExit", "_exit"} and n.args:
                    c = _code(n.args[0])
                    if c is not None:
                        codes.add(c)
        for n in ast.walk(mains[0]):
            if isinstance(n, ast.Return) and n.value is not None:
                c = _code(n.value)
                if c is not None:
                    codes.add(c)
        assert codes, "main()/exit paths must use int exit codes (literals or module constants)"
        assert codes <= {0, 1, 2}, f"exit codes outside {{0,1,2}}: {sorted(codes)}"

    def test_ac8_dynamic_runs_without_claude_bun_or_api_key_and_emits_one_verdict(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        env = _env(tmp_path, bin_dir)
        assert "ANTHROPIC_API_KEY" not in env
        for tool in ("claude", "bun"):
            assert not any(
                (Path(d) / tool).exists() for d in env["PATH"].split(":")
            ), f"test env must not contain {tool}"
        r = _run(tmp_path, root)
        v = _verdict(r)  # stdout is exactly one JSON object
        assert v["status"] == "blocked" and r.returncode == 1

    def test_ac8_dynamic_exit_code_set_is_exactly_0_1_2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        seen = set()
        for out, rc in (("[]", 0), (_hl(("DL3006", "error")), 1), ("[]", 3)):
            _shim(bin_dir, "hadolint", out, rc=rc)
            r = _run(tmp_path, root)
            _verdict(r)
            seen.add(r.returncode)
        r = _run(tmp_path, root, "--files", "README.md")
        _verdict(r)
        seen.add(r.returncode)
        assert seen == {0, 1, 2}, seen

    # ------------------------------------------------------------- AC9 ---
    def test_ac9_script_writes_nothing_under_root(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error"), ("DL3008", "warning")), rc=1)
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL"), ("AVD-X-2", "LOW")), rc=0)
        root = _make_repo(
            tmp_path,
            {
                "Dockerfile": DOCKERFILE,
                "infra/main.tf": MAIN_TF,
                "a.py": "x = 1\n",
                "allow.txt": f"DL3006 :: ABCDEF12 :: kill-by:{FUTURE}\n",
            },
        )
        before = _tree_hash(root)
        r = _run(tmp_path, root, "--allowlist", str(root / "allow.txt"))
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)  # non-vacuous: AVD-X-1 still gates
        v = _verdict(r)
        assert [f["id"] for f in v["waived"]] == ["DL3006"]
        assert [f["id"] for f in v["gating"]] == ["AVD-X-1"]
        assert _tree_hash(root) == before, "script modified the tree under --root"

    # ------------------------------------------------------------ AC10 ---
    # G1 (gate r1 M1)
    def test_ac10_g1_git_ls_files_failure_without_files_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE}, git=False)
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "git_ls_files_failed" in v["reason"], v
        assert not _calls(bin_dir, "hadolint")

    def test_ac10_git_tracked_file_deleted_on_disk_is_skipped(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        (root / "Dockerfile").unlink()
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "nothing_to_scan"
        assert not _calls(bin_dir, "hadolint")

    def test_ac10_git_ls_files_z_non_ascii_path_scanned(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=0)
        root = _make_repo(tmp_path, {"é/main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "blocked"
        assert _calls(bin_dir, "trivy")

    # F7 trivy shapes (M2)
    @pytest.mark.parametrize(
        "out",
        [
            "{}",
            '{"Results": null}',
            '{"Results": []}',
            '{"Results": [{"Target": "main.tf"}]}',
            '{"Results": [{"Target": "main.tf", "Misconfigurations": null}]}',
            '{"Results": [{"Target": "main.tf", "Misconfigurations": []}]}',
        ],
    )
    def test_ac10_f7_real_trivy_clean_shapes_are_clean_exit0(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", out, rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 0, (out, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "clean" and not v["gating"] and not v["nongating"], v
        assert _calls(bin_dir, "trivy"), "trivy must have run (clean, not nothing_to_scan)"

    # F6 (M3)
    def test_ac10_f6_hadolint_rc0_with_error_finding_still_gates(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "blocked" and [f["id"] for f in v["gating"]] == ["DL3006"]

    def test_ac10_f6_hadolint_rc0_warning_gates_with_fail_on_medium(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL2", "warning")), rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root, "--fail-on", "MEDIUM")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)

    @pytest.mark.parametrize("out", ["", "  \n\t\n"])
    def test_ac10_f6_hadolint_rc1_empty_stdout_is_subprocess_error(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", out, rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (out, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "subprocess_error" in v["reason"], v

    # F8 (M4)
    @pytest.mark.parametrize("val", ["", "CRTICAL", ",", "CRITICAL,,HIGH", "CRITICAL,BOGUS", " "])
    def test_ac10_f8_bad_fail_on_exit2(self, tmp_path, val):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root, "--fail-on", val)
        assert r.returncode == 2, (val, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "bad_fail_on" in v["reason"], v
        assert not v["gating"] and not v["nongating"]

    def test_ac10_f8_valid_tokens_incl_unknown_low_mixed_case_accepted(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "LOW")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root, "--fail-on", "Unknown,low")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "blocked"

    # allowlist (M5)
    @pytest.mark.parametrize("agr", ["abcdef12", "AbCdEf12"])
    def test_ac10_allowlist_lowercase_or_mixed_hex_agreement_is_valid(self, tmp_path, agr):
        root = TestS4M1DevopsScan._blocked_dockerfile_repo(self, tmp_path)
        allow = _allow(tmp_path, f"DL3006 :: {agr} :: kill-by:{FUTURE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (agr, r.returncode, r.stdout, r.stderr)
        assert [f["id"] for f in _verdict(r)["waived"]] == ["DL3006"]

    def test_ac10_allowlist_kill_by_exactly_365_days_ahead_still_valid(self, tmp_path):
        root = TestS4M1DevopsScan._blocked_dockerfile_repo(self, tmp_path)
        allow = _allow(tmp_path, f"DL3006 :: ABCDEF12 :: kill-by:{EDGE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["waived"]

    def test_ac10_allowlist_empty_pattern_line_does_not_waive_valid_neighbour_only(self, tmp_path):
        root = TestS4M1DevopsScan._blocked_dockerfile_repo(
            self, tmp_path, ("DL3006", "error"), ("DL3007", "error")
        )
        allow = _allow(
            tmp_path,
            f" :: ABCDEF12 :: kill-by:{FUTURE}",
            f"DL3006 :: ABCDEF12 :: kill-by:{FUTURE}",
        )
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [f["id"] for f in v["gating"]] == ["DL3007"]
        assert [f["id"] for f in v["waived"]] == ["DL3006"]

    # F10 (M6)
    @pytest.mark.parametrize(
        "extra",
        [
            {},  # level missing
            {"level": "fatal"},
            {"level": None},
            {"level": 7},
            {"level": ""},
        ],
    )
    def test_ac10_f10_hadolint_missing_or_unknown_level_is_high(self, tmp_path, extra):
        bin_dir = tmp_path / "bin"
        item = {"code": "DL9", "message": "m", "file": "Dockerfile", "line": 1, "column": 1, **extra}
        _shim(bin_dir, "hadolint", _hl_raw(item), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (extra, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [(f["id"], f["severity"]) for f in v["gating"]] == [("DL9", "HIGH")], v

    @pytest.mark.parametrize(
        "extra",
        [
            {},  # Severity missing
            {"Severity": None},
            {"Severity": 5},
            {"Severity": ""},
            {"Severity": "WEIRD"},
        ],
    )
    def test_ac10_f10_trivy_missing_or_non_string_severity_is_high(self, tmp_path, extra):
        bin_dir = tmp_path / "bin"
        item = {"ID": "AVD-X-7", "Title": "t", "Description": "d", **extra}
        _shim(bin_dir, "trivy", _tv_raw(item), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (extra, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [(f["id"], f["severity"]) for f in v["gating"]] == [("AVD-X-7", "HIGH")], v

    # scanner-native suppression disabled (M7)
    def test_ac10_native_suppression_disabled_argv_cwd_home(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=0)
        root = _make_repo(
            tmp_path,
            {
                "Dockerfile": "# hadolint ignore=DL3006\nFROM ubuntu:latest\n",
                ".hadolint.yaml": "ignored:\n  - DL3006\n",
                ".trivyignore": "AVD-X-1\n",
                "trivy.yaml": "ignorefile: .trivyignore\n",
                "infra/main.tf": MAIN_TF + "#trivy:ignore:AVD-X-1\n",
            },
        )
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        # the inline pragmas in the fixture files are themselves gating findings (spec r3)
        assert {f["id"] for f in v["gating"]} == {"DL3006", "AVD-X-1", "inline_ignore_pragma"}

        hcalls, tcalls = _calls(bin_dir, "hadolint"), _calls(bin_dir, "trivy")
        assert hcalls and tcalls
        for c in hcalls:
            w = c.split()
            assert "--disable-ignore-pragma" in w and "--no-color" in w, c
            assert any(x.startswith("/") and _under(x, root) and x.endswith("Dockerfile") for x in w), (
                f"files must be passed by absolute path: {c}"
            )
        for c in tcalls:
            w = c.split()
            assert "--ignorefile" in w, c
            assert any(x.startswith("/") and _under(x, root) for x in w), c

        harness_cwd = os.path.realpath(str(tmp_path / "neutral-cwd"))
        harness_home = os.path.realpath(str(tmp_path / "home"))
        import re

        for name in ("hadolint", "trivy"):
            log = _envlog(bin_dir, name)
            envs = [m for m in (re.match(r"cwd=(\S+) home=(\S+)$", ln) for ln in log) if m]
            assert envs, (name, log)
            for m in envs:
                cwd, home = m.group(1), m.group(2)
                assert not _under(cwd, root), f"{name} cwd inside --root: {cwd}"
                assert not _under(home, root), f"{name} HOME inside --root: {home}"
                assert os.path.realpath(cwd) != harness_cwd, "cwd must be a fresh temp dir"
                assert os.path.realpath(home) != harness_home, "HOME must be a fresh temp dir"
        ign = [ln for ln in _envlog(bin_dir, "trivy") if ln.startswith("ignorefile ")]
        assert ign, "trivy must receive --ignorefile pointing at an existing file"
        for ln in ign:
            parts = ln.split()
            assert parts[-2:] == ["size", "0"], f"ignorefile must exist and be empty: {ln}"
            assert not _under(parts[1], root), ln

    # F9 partial failure (gate minor 8)
    def test_ac10_f9_two_dockerfiles_one_run_fails_exit2_other_gating_listed(self, tmp_path):
        bin_dir = tmp_path / "bin"
        body = 'case "$*" in *zzbadzz*) exit 3;; esac\ncat "$D/hadolint.out"\nexit 1\n'
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), body=body)
        root = _make_repo(
            tmp_path, {"zzbadzz/Dockerfile": DOCKERFILE, "zzgoodzz/Dockerfile": DOCKERFILE}
        )
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "subprocess_error" in v["reason"], v
        assert [f["id"] for f in v["gating"]] == ["DL3006"], "other run's gating finding must be listed"
        assert "zzgoodzz" in v["gating"][0]["file"]

    def test_ac10_f9_two_trivy_dirs_one_run_fails_exit2_other_gating_listed(self, tmp_path):
        bin_dir = tmp_path / "bin"
        body = 'case "$*" in *zzbadzz*) exit 3;; esac\ncat "$D/trivy.out"\nexit 0\n'
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), body=body)
        root = _make_repo(tmp_path, {"zzbadzz/main.tf": MAIN_TF, "zzgoodzz/main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "subprocess_error" in v["reason"], v
        assert [f["id"] for f in v["gating"]] == ["AVD-X-1"]

    def test_ac10_f9_blocked_hadolint_plus_failed_trivy_is_unavailable_with_findings(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        _shim(bin_dir, "trivy", "", rc=3)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE, "main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable"
        assert [f["id"] for f in v["gating"]] == ["DL3006"]

    # classification (gate minor 18/19)
    def test_ac10_dockerfile_dockerignore_not_scanned(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL1000", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile.dockerignore": "node_modules\n"})
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _verdict(r)["status"] == "nothing_to_scan"
        assert not _calls(bin_dir, "hadolint")

    def test_ac10_dockerignore_skipped_real_dockerfile_still_scanned(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        root = _make_repo(
            tmp_path, {"Dockerfile": DOCKERFILE, "Dockerfile.dockerignore": "node_modules\n"}
        )
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        calls = _calls(bin_dir, "hadolint")
        assert calls and not any(".dockerignore" in c for c in calls), calls

    def test_ac10_dockerfile_class_wins_over_k8s_yaml(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL")), rc=0)
        root = _make_repo(tmp_path, {"k8s/Dockerfile.yaml": "FROM x\n"})
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert _calls(bin_dir, "hadolint") and not _calls(bin_dir, "trivy")

    # trivy finding path handling (spec §2 findings rc paragraph)
    def test_ac10_trivy_finding_on_unlisted_target_is_kept_gating(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL"), target="other/unlisted.tf"), rc=0)
        root = _make_repo(tmp_path, {"infra/main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [f["id"] for f in v["gating"]] == ["AVD-X-1"]
        assert "unlisted.tf" in v["gating"][0]["file"]

    def test_ac10_trivy_absolute_target_under_root_reported_relative_else_as_is(self, tmp_path):
        bin_dir = tmp_path / "bin"
        root = _make_repo(tmp_path, {"infra/main.tf": MAIN_TF})
        under = str(root.resolve() / "infra" / "main.tf")
        outside = "/elsewhere-zz/x.tf"
        out = json.dumps(
            {
                "Results": [
                    {"Target": under, "Misconfigurations": [{"ID": "A-1", "Severity": "CRITICAL", "Title": "t", "Description": "d"}]},
                    {"Target": outside, "Misconfigurations": [{"ID": "A-2", "Severity": "CRITICAL", "Title": "t", "Description": "d"}]},
                ]
            }
        )
        _shim(bin_dir, "trivy", out, rc=0)
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        files = {f["id"]: f["file"] for f in _verdict(r)["gating"]}
        assert files == {"A-1": "infra/main.tf", "A-2": outside}, files

    # ------------------------------------------------------------ AC11 ---
    # (spec r3 / gate r2). Every test runs the real script as a subprocess;
    # no singleton resource or timing is involved (workflows.md 1i n/a).

    # M1: constructed scanner env
    def test_ac11_m1_scanner_env_is_whitelist_only(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        _shim(bin_dir, "trivy", "{}", rc=0)
        xdg = tmp_path / "xdg"
        xdg.mkdir()
        (xdg / "hadolint.yaml").write_text("ignored:\n  - DL3006\n")
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE, "main.tf": MAIN_TF})
        leak = {
            "HADOLINT_IGNORE": "DL3006",
            "HADOLINT_OVERRIDE_INFO": "DL3006",
            "TRIVY_SEVERITY": "LOW",
            "TRIVY_SKIP_DIRS": "infra",
            "XDG_CONFIG_HOME": str(xdg),
            "XDG_CACHE_HOME": str(tmp_path / "xdgcache"),
            "ZZ_SENTINEL": "1",
            "LANG": "en_US.UTF-8",
            "TMPDIR": str(tmp_path),
        }
        r = _run(tmp_path, root, env_extra=leak)
        v = _verdict(r)
        assert r.returncode == 0 and v["status"] == "clean", (r.stdout, r.stderr)
        allowed = {"PATH", "HOME", "LANG", "TMPDIR", "PWD", "OLDPWD", "SHLVL", "_"}
        for name in ("hadolint", "trivy"):
            runs = _fullenv(bin_dir, name)
            assert runs, f"{name} never ran"
            for env in runs:
                assert not set(env) - allowed, f"{name} got non-whitelisted vars: {sorted(set(env) - allowed)}"
                assert {"PATH", "HOME", "TMPDIR"} <= set(env), (name, sorted(env))
                assert env.get("LANG") == "C", (name, env.get("LANG"))

    def test_ac11_m1_trivy_argv_full_severity_list_and_cache_dir(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", "{}", rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root, env_extra={"TRIVY_SEVERITY": "LOW"})
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        calls = _calls(bin_dir, "trivy")
        assert calls, "trivy never ran"
        for c in calls:
            w = c.split()
            assert "--severity" in w, c
            sev = w[w.index("--severity") + 1]
            assert set(sev.split(",")) == {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}, c
            assert "--cache-dir" in w, c
            cache = w[w.index("--cache-dir") + 1]
            assert os.path.isabs(cache) and not _under(cache, root), c
            # the invoking user's real cache dir (under the harness HOME), not the fresh scanner HOME
            assert _under(cache, tmp_path / "home"), f"cache dir must derive from the invoking HOME: {c}"

    # M2: inline suppression pragmas become gating findings
    @pytest.mark.parametrize(
        "rel,content",
        [
            ("main.tf", MAIN_TF + "#trivy:ignore:AVD-X-1\n"),
            ("main.tf", MAIN_TF + "#tfsec:ignore:aws-s3-x\n"),
            ("main.tf", MAIN_TF + "// TRIVY:IGNORE:AVD-X-1\n"),
            ("k8s/dep.yaml", "# trivy:ignore:AVD-KSV-0001\nkind: Pod\n"),
            ("Dockerfile", "# hadolint ignore=DL3008\nFROM ubuntu:latest\n"),
            ("Dockerfile", "FROM ubuntu:latest\n# HADOLINT IGNORE=DL3008\n"),
        ],
    )
    def test_ac11_m2_inline_ignore_pragma_is_gating_finding(self, tmp_path, rel, content):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        _shim(bin_dir, "trivy", "{}", rc=0)
        root = _make_repo(tmp_path, {rel: content})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (rel, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "blocked" and len(v["gating"]) == 1, v
        f = v["gating"][0]
        assert (f["scanner"], f["id"], f["severity"], f["file"]) == (
            "pragma",
            "inline_ignore_pragma",
            "HIGH",
            rel,
        ), f

    def test_ac11_m2_pragma_waivable_only_by_allowlist(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", "{}", rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF + "#trivy:ignore:AVD-X-1\n"})
        allow = _allow(tmp_path, f"inline_ignore_pragma :: ABCDEF12 :: kill-by:{FUTURE}")
        r = _run(tmp_path, root, "--allowlist", str(allow))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert not v["gating"] and [f["id"] for f in v["waived"]] == ["inline_ignore_pragma"], v

    def test_ac11_m2_no_pragma_text_and_non_candidates_are_not_flagged(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", "{}", rc=0)
        root = _make_repo(
            tmp_path,
            {
                "main.tf": MAIN_TF + "# please ignore the trivy docs\n",
                "a.py": "# trivy:ignore:AVD-X-1\n# hadolint ignore=DL3008\n",
            },
        )
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "clean" and not v["gating"] and not v["waived"], v

    # M3: empty/whitespace stdout is never clean, whatever rc 0
    @pytest.mark.parametrize("out", ["", "  \n\t\n"])
    def test_ac11_m3_hadolint_rc0_empty_stdout_exit2(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", out, rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (out, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "json_decode_error" in v["reason"], v

    @pytest.mark.parametrize("out", ["", "  \n\t\n"])
    def test_ac11_m3_trivy_rc0_empty_stdout_exit2(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", out, rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (out, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "json_decode_error" in v["reason"], v

    # M4: identity fields never skipped; non-object Results entries
    @pytest.mark.parametrize(
        "misc,sev",
        [
            ({"Severity": "CRITICAL", "Title": "t", "Description": "d"}, "CRITICAL"),
            ({"Title": "t", "Description": "d"}, "HIGH"),
        ],
    )
    def test_ac11_m4_trivy_misconfiguration_without_id_gates_as_missing(self, tmp_path, misc, sev):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv_raw(misc), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (misc, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [(f["id"], f["severity"]) for f in v["gating"]] == [("<missing>", sev)], v

    def test_ac11_m4_hadolint_finding_without_code_gates_as_missing(self, tmp_path):
        bin_dir = tmp_path / "bin"
        item = {"level": "error", "message": "m", "file": "Dockerfile", "line": 1, "column": 1}
        _shim(bin_dir, "hadolint", _hl_raw(item), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert [(f["id"], f["severity"]) for f in v["gating"]] == [("<missing>", "HIGH")], v

    @pytest.mark.parametrize("out", ['{"Results": ["x"]}', '{"Results": [null]}', '{"Results": [7]}'])
    def test_ac11_m4_trivy_non_object_results_entry_exit2(self, tmp_path, out):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", out, rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (out, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "json_shape_error" in v["reason"], v

    # M5: run shape
    def test_ac11_m5_hadolint_once_per_dockerfile_file_is_relative_input_path(self, tmp_path):
        bin_dir = tmp_path / "bin"
        # the shim's own "file" field says "Dockerfile": it must be ignored
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"a/Dockerfile": DOCKERFILE, "b/Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert sorted(f["file"] for f in v["gating"]) == ["a/Dockerfile", "b/Dockerfile"], v
        calls = _calls(bin_dir, "hadolint")
        assert len(calls) == 2, calls
        for c in calls:
            assert sum(1 for w in c.split() if w.endswith("Dockerfile")) == 1, c

    def test_ac11_m5_trivy_once_per_distinct_directory(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", "{}", rc=0)
        root = _make_repo(
            tmp_path, {"infra/a.tf": MAIN_TF, "infra/b.tf": MAIN_TF, "prod/c.tf": MAIN_TF}
        )
        r = _run(tmp_path, root)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert len(_calls(bin_dir, "trivy")) == 2, _calls(bin_dir, "trivy")

    # MINOR: de-dup (same file|id|description)
    def test_ac11_dedup_identical_findings_in_one_run(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL"), ("AVD-X-1", "CRITICAL")), rc=0)
        root = _make_repo(tmp_path, {"main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert [f["id"] for f in _verdict(r)["gating"]] == ["AVD-X-1"]

    def test_ac11_dedup_nested_scan_dirs_report_once(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "trivy", _tv(("AVD-X-1", "CRITICAL"), target="infra/main.tf"), rc=0)
        root = _make_repo(tmp_path, {"infra/main.tf": MAIN_TF, "infra/mod/x.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert len(_calls(bin_dir, "trivy")) == 2, "one trivy run per distinct directory"
        assert [f["id"] for f in _verdict(r)["gating"]] == ["AVD-X-1"], "duplicate must be reported once"

    # M6: absolute --files entries
    def test_ac11_m6_absolute_in_root_files_entry_is_scanned(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", _hl(("DL3006", "error")), rc=1)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE}, git=False)
        r = _run(tmp_path, root, "--files", f"{root / 'Dockerfile'},../nope,missing.tf")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "blocked" and "Dockerfile" in v["gating"][0]["file"]
        assert not os.path.isabs(v["gating"][0]["file"]), "file must be root-relative"
        assert "2" in v["reason"], f"reason must state 2 dropped entries: {v['reason']!r}"

    # *.tf.json routing is covered by test_ac2_iac_path_variants_routed_to_trivy

    # G1 minor: git absent from PATH
    def test_ac11_g1_git_missing_from_path_without_files_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        nogit = tmp_path / "nogit-bin"
        nogit.mkdir()
        r = _run(tmp_path, root, env_extra={"PATH": str(nogit)})
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert v["status"] == "unavailable" and "git_ls_files_failed" in v["reason"], v
        assert not _calls(bin_dir, "hadolint")

    # UTC today (minor 9): TZ is set so a local-date implementation disagrees with UTC
    @pytest.mark.parametrize(
        "tz,kill_by,expected_rc",
        [
            ("Pacific/Kiritimati", TODAY, 0),  # UTC+14: local date is ahead of UTC for 14h/day
            ("Etc/GMT+12", PAST, 1),  # UTC-12: local date is behind UTC for 12h/day
        ],
    )
    def test_ac11_today_is_utc_not_local_date(self, tmp_path, tz, kill_by, expected_rc):
        root = self._blocked_dockerfile_repo(tmp_path)
        allow = _allow(tmp_path, f"DL3006 :: ABCDEF12 :: kill-by:{kill_by}")
        r = _run(tmp_path, root, "--allowlist", str(allow), env_extra={"TZ": tz})
        assert r.returncode == expected_rc, (tz, r.returncode, r.stdout, r.stderr)
        v = _verdict(r)
        assert bool(v["waived"]) == (expected_rc == 0), v

    # top-level exception handler
    def test_ac11_internal_error_unreadable_candidate_exit2_not_1(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        target = root / "Dockerfile"
        target.chmod(0)
        try:
            if os.access(target, os.R_OK):
                pytest.skip("cannot make a file unreadable here (running as root?)")
            r = _run(tmp_path, root)
        finally:
            target.chmod(0o644)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        v = _verdict(r)  # one JSON verdict, no Traceback on stderr
        assert v["status"] == "unavailable" and "internal_error" in v["reason"], v
