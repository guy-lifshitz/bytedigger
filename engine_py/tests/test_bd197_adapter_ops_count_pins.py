"""bd#197 (group 6) -- pins for the opt-in "count" mode of `adapter_ops`.

Spec: `docs/decisions/2026-10-15-bd197-adapter-ops-count.md`.

These pin behaviour that must hold both before and after GREEN: the default
stays "shadow" (AC7, the guard for the owner decision not to flip it here),
"off" is the kill switch, an unknown mode raises, the harness surface is
unchanged and names no provider (AC12). They pass today by design; the failing
RED tests live in `test_bd197_adapter_ops_count.py`.

`conformance.*` imports are deferred into test bodies. Nothing under
`bytedigger_engine/` is put on sys.path.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent

OPTIONAL = ("ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-7", "ADV-8", "ADV-10")
OPTIONAL_METHODS = ("lint_vacuity", "record_oracle_step", "record_gate_step",
                    "admit_ledger_row", "attest_step", "attribute_block")


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _oracle():
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    return oracle


class Full:
    identity = {"backend": "stub-backend", "source": "stub-source"}

    def __init__(self):
        self.calls = []

    def _digest(self, root, members):
        rels = [str(Path(p).relative_to(root)) for p in members]
        return _oracle().compute_digest(root, rels)

    def freeze(self, paths, *, root):
        self.calls.append("freeze")
        return self._digest(root, list(paths))

    def evaluate(self, state):
        self.calls.append("evaluate")
        O = _oracle().OracleOutcome
        now = self._digest(state.root, list(state.members))
        return O.ACCEPTED if now == state.frozen else O.REJECTED

    def lint_vacuity(self, path):
        self.calls.append("lint_vacuity")
        from bytedigger_engine import stub_passability  # noqa: PLC0415

        return stub_passability.lint_red_file(str(path)).has_violation()

    def record_oracle_step(self, fault):
        self.calls.append("record_oracle_step")
        return {"status": "passed"} if fault is None else {"status": "load_failure"}

    def record_gate_step(self, fault):
        self.calls.append("record_gate_step")
        return {"status": "passed"} if fault is None else {"status": "failed"}

    def admit_ledger_row(self, row, as_of):
        self.calls.append("admit_ledger_row")
        return {"accepted": bool(row["issue"].strip()) and row["kill_by"] >= as_of}

    def attest_step(self, fault):
        self.calls.append("attest_step")
        used = ["Read", "Bash"] + (["Write"] if fault == "tool_escape" else [])
        return {
            "model_requested": "alpha-large-2",
            "model_observed": (
                "beta-small-1" if fault == "model_drift" else "alpha-large-2"
            ),
            "tools_declared": ["Read", "Bash(x:*)"],
            "tools_observed": used,
        }

    def attribute_block(self, content):
        self.calls.append("attribute_block")
        return {
            "source_id": "host-src-1",
            "digest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }


# --------------------------------------------------------------------------
# AC7: the default is shadow, pinned
# --------------------------------------------------------------------------

def test_ac7_run_adversaries_default_adapter_ops_is_shadow():
    h = _h()
    assert inspect.signature(h.run_adversaries).parameters["adapter_ops"].default == "shadow"


def test_ac7_default_run_reports_shadow_outcomes_and_bd_l1():
    h = _h()
    out = h.run_adversaries(adapter=Full())
    assert out["ADV-1"] == "defended" and out["ADV-2"] == "defended"
    for n in OPTIONAL:
        assert out[n] == "shadow:defended", n
    att = h.build_attestation(
        out, level_claimed="BD-L1", engine_version="0.0.0-test",
        adapter_identity="test-adapter", host_identity="test-host",
        timestamp="2026-10-15T00:00:00Z",
    )
    assert att["level_achieved"] == "BD-L1"


# --------------------------------------------------------------------------
# AC8: kill switch and unknown values
# --------------------------------------------------------------------------

def test_ac8_off_never_calls_an_optional_method():
    h = _h()
    a = Full()
    off = h.run_adversaries(adapter=a, adapter_ops="off")
    for m in OPTIONAL_METHODS:
        assert m not in a.calls, m
    for n in OPTIONAL:
        assert off[n] == "not_executed", n
    shadow = h.run_adversaries(adapter=Full(), adapter_ops="shadow")
    assert off["ADV-1"] == shadow["ADV-1"] == "defended"
    assert off["ADV-2"] == shadow["ADV-2"] == "defended"


@pytest.mark.parametrize("bad", ["on", "COUNT", "Count", " count", "enforce", "",
                                 None, 1, True])
def test_ac8_unknown_adapter_ops_value_raises(bad):
    h = _h()
    with pytest.raises(ValueError):
        h.run_adversaries(adapter=Full(), adapter_ops=bad)


# --------------------------------------------------------------------------
# AC11: CLI shadow, off, and usage error
# --------------------------------------------------------------------------

_FACTORY_SRC = textwrap.dedent('''
    import hashlib
    from pathlib import Path
    from bytedigger_engine import stub_passability
    from bytedigger_engine.conformance import oracle as _o

    class Host:
        identity = {"backend": "cli-backend", "source": "cli-source"}

        def _digest(self, root, members):
            rels = [str(Path(p).relative_to(root)) for p in members]
            return _o.compute_digest(root, rels)

        def freeze(self, paths, *, root):
            return self._digest(root, list(paths))

        def evaluate(self, state):
            O = _o.OracleOutcome
            now = self._digest(state.root, list(state.members))
            return O.ACCEPTED if now == state.frozen else O.REJECTED

        def lint_vacuity(self, path):
            return stub_passability.lint_red_file(str(path)).has_violation()

        def record_oracle_step(self, fault):
            if fault is None:
                return {"status": "passed"}
            return {"status": "load_failure"}

        def record_gate_step(self, fault):
            return {"status": "passed"} if fault is None else {"status": "failed"}

        def admit_ledger_row(self, row, as_of):
            ok = bool(row["issue"].strip()) and row["kill_by"] >= as_of
            return {"accepted": ok}

        def attest_step(self, fault):
            used = ["Read", "Bash"] + (["Write"] if fault == "tool_escape" else [])
            return {
                "model_requested": "alpha-large-2",
                "model_observed": (
                    "beta-small-1" if fault == "model_drift" else "alpha-large-2"
                ),
                "tools_declared": ["Read", "Bash(x:*)"],
                "tools_observed": used,
            }

        def attribute_block(self, content):
            return {
                "source_id": "cli-src-1",
                "digest": "sha256:" + hashlib.sha256(
                    content.encode("utf-8")).hexdigest(),
            }

    def make_host():
        return Host()
''')

_MOD = "bd197_count_cli_factories_pins"


def _cli(tmp_path, *extra):
    (tmp_path / f"{_MOD}.py").write_text(_FACTORY_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_PY) + os.pathsep + str(tmp_path)
    cmd = [
        sys.executable, "-m", "bytedigger_engine.conformance.harness",
        "--adapter", f"{_MOD}:make_host",
        "--level-claimed", "BD-L1",
        "--engine-version", "0.0.0-test",
        "--host-identity", "test-host",
        "--timeout-s", "5",
        *extra,
    ]
    return subprocess.run(
        cmd, cwd=str(ENGINE_PY), env=env, capture_output=True, text=True, timeout=120
    )


def test_ac7_cli_without_the_flag_is_shadow_and_bd_l1(tmp_path):
    r = _cli(tmp_path)
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["level_achieved"] == "BD-L1"
    for n in OPTIONAL:
        assert att["adversaries"][n] == "shadow:defended", n


def test_ac11_cli_explicit_shadow_matches_the_default(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "shadow")
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    assert att["level_achieved"] == "BD-L1"
    assert att["adversaries"]["ADV-8"] == "shadow:defended"


def test_ac11_cli_off_is_not_executed(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "off")
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    for n in OPTIONAL:
        assert att["adversaries"][n] == "not_executed", n


def test_ac11_cli_bogus_adapter_ops_is_a_usage_error(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "bogus")
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stdout == ""
    assert "adapter-ops" in r.stderr


# --------------------------------------------------------------------------
# AC12: pinned surface
# --------------------------------------------------------------------------

def test_ac12_all_is_unchanged():
    h = _h()
    assert set(h.__all__) == {
        "ADVERSARIES", "OUTCOME_DEFENDED", "OUTCOME_UNDEFENDED", "OUTCOME_ERRORED",
        "OUTCOME_INDETERMINATE", "AdapterState", "adapter_identity",
        "run_adversaries", "build_attestation", "validate_attestation",
    }
    assert len(h.__all__) == len(set(h.__all__)) == 10


def test_ac12_harness_source_names_no_provider():
    h = _h()
    src = Path(h.__file__).read_text(encoding="utf-8").lower()
    for bad in ("anthropic", "claude", "openai", "subscription", "api-token", "api_key"):
        assert bad not in src, bad


def test_ac12_no_adapter_run_is_unchanged():
    h = _h()
    nine = {n: "defended" for n in h.ADVERSARIES}
    assert h.run_adversaries() == nine
    assert h.run_adversaries(adapter=None) == nine
