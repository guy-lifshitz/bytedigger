"""bd#197 (group 1) -- pins for the ADV-3 vacuity-lint adapter operation.

These pin behaviour that must hold both before and after GREEN (omitted op stays
`not_executed`, CLI usage error, pinned harness surface). They pass today by
design; the failing RED tests live in `test_bd197_adapter_vacuity_lint.py`.

`conformance.*` imports are deferred into test bodies. Nothing under
`bytedigger_engine/` is put on sys.path.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _oracle():
    from bytedigger_engine.conformance import oracle  # noqa: PLC0415

    return oracle


class _Base:
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


class NoLint(_Base):
    pass


class NoneLint(_Base):
    lint_vacuity = None


@pytest.mark.parametrize("cls", [NoLint, NoneLint])
def test_ac5_omitted_operation_is_not_executed_and_nothing_called(cls):
    h = _h()
    a = cls()
    assert h.run_adversaries(only=("ADV-3",), adapter=a) == {"ADV-3": "not_executed"}
    assert a.calls == []

    full = h.run_adversaries(adapter=cls())
    assert set(full) == set(h.ADVERSARIES)
    assert len(full) == 9
    assert full["ADV-1"] == h.OUTCOME_DEFENDED
    assert full["ADV-2"] == h.OUTCOME_DEFENDED
    assert full["ADV-3"] == "not_executed"


_FACTORY_SRC = textwrap.dedent('''
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

    def make_host():
        return Host()
''')

_MOD = "bd197_cli_factories"


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
        cmd, cwd=str(ENGINE_PY), env=env, capture_output=True, text=True, timeout=60
    )


def test_ac10_cli_adapter_ops_invalid_is_usage_error(tmp_path):
    r = _cli(tmp_path, "--adapter-ops", "bogus")
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stdout == ""
    assert "adapter-ops" in r.stderr


def test_ac11_surface_unchanged():
    h = _h()
    assert set(h.__all__) == {
        "ADVERSARIES", "OUTCOME_DEFENDED", "OUTCOME_UNDEFENDED", "OUTCOME_ERRORED",
        "OUTCOME_INDETERMINATE", "AdapterState", "adapter_identity",
        "run_adversaries", "build_attestation", "validate_attestation",
    }
    assert len(h.__all__) == len(set(h.__all__)) == 10


def test_ac11_harness_source_names_no_provider():
    h = _h()
    src = Path(h.__file__).read_text(encoding="utf-8").lower()
    for bad in ("anthropic", "claude", "openai", "subscription", "api-token", "api_key"):
        assert bad not in src, bad


def test_ac11_no_adapter_run_is_unchanged():
    h = _h()
    nine = {n: "defended" for n in
            ("ADV-1", "ADV-2", "ADV-3", "ADV-4", "ADV-5", "ADV-6", "ADV-7",
             "ADV-8", "ADV-10")}
    assert h.run_adversaries() == nine
    assert h.run_adversaries(adapter=None) == nine
