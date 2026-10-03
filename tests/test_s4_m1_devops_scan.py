"""RED tests for scripts/devops_scan.py (S4/M1, spec
docs/decisions/2026-10-03-s4-m1-devops-scan-script.md, AC1-AC9).

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

def _shim(bin_dir: Path, name: str, out: str = "", rc: int = 0, sleep=None) -> None:
    """Write an executable fake scanner. Logs its argv to <name>.calls."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / f"{name}.out").write_text(out)
    lines = ["#!/bin/sh", f'echo "$@" >> "$(dirname "$0")/{name}.calls"']
    if sleep:
        lines.append(f"exec sleep {sleep}")
    lines += [f'cat "$(dirname "$0")/{name}.out"', f"exit {rc}"]
    path = bin_dir / name
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)


def _calls(bin_dir: Path, name: str) -> list:
    f = bin_dir / f"{name}.calls"
    return f.read_text().splitlines() if f.exists() else []


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
    }


def _run(tmp_path: Path, root, *args, bin_dir=None):
    # An absent script makes the interpreter itself exit 2, which would
    # satisfy every F-case by accident; fail loudly instead.
    assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
    bin_dir = bin_dir if bin_dir is not None else tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    cwd = tmp_path / "neutral-cwd"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
        env=_env(tmp_path, bin_dir),
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


TODAY = datetime.date.today()
FUTURE = (TODAY + datetime.timedelta(days=30)).isoformat()
PAST = (TODAY - datetime.timedelta(days=1)).isoformat()

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
        assert "-f" in words and "json" in words, calls

    def test_ac1_hadolint_no_findings_is_clean_exit0(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", "[]", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE})
        r = _run(tmp_path, root)
        v = _verdict(r)
        assert r.returncode == 0 and v["status"] == "clean"
        assert _calls(bin_dir, "hadolint"), "hadolint must have run (not nothing_to_scan)"

    @pytest.mark.parametrize(
        "name", ["Dockerfile", "Dockerfile.dev", "app.Dockerfile", "sub/Dockerfile.prod"]
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
            "prod.tfvars",
            "docker-compose.yml",
            "docker-compose.prod.yaml",
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

    def test_ac4_f3_wrong_json_shape_exit2(self, tmp_path):
        bin_dir = tmp_path / "bin"
        _shim(bin_dir, "hadolint", '{"not": "a list"}', rc=0)
        _shim(bin_dir, "trivy", "[]", rc=0)
        root = _make_repo(tmp_path, {"Dockerfile": DOCKERFILE, "main.tf": MAIN_TF})
        r = _run(tmp_path, root)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
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
        allow = _allow(tmp_path, f"DL3006 :: ABCDEF12 :: kill-by:{TODAY.isoformat()}")
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
            f"DL3006 :: kill-by:{FUTURE}",
            f"DL3006 :: NOTHEX!! :: kill-by:{FUTURE}",
            "DL3006 :: ABCDEF12 :: kill-by:not-a-date",
            "DL3006 :: ABCDEF12 :: kill-by:2099-13-45",
            "DL3006 :: ABCDEF12",
        ],
    )
    def test_ac5_malformed_line_waives_nothing_never_raises(self, tmp_path, line):
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
        assert "Traceback" not in r.stderr

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
        assert _verdict(r)["status"] == "nothing_to_scan"
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
        assert "Traceback" not in r.stderr

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
        codes = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in {"exit", "SystemExit", "_exit"} and n.args:
                    a = n.args[0]
                    if isinstance(a, ast.Constant) and isinstance(a.value, int):
                        codes.add(a.value)
        for n in ast.walk(mains[0]):
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant):
                if isinstance(n.value.value, int) and not isinstance(n.value.value, bool):
                    codes.add(n.value.value)
        assert codes, "main()/exit paths must use literal exit codes"
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
