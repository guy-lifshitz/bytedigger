"""bytedigger port note: upstream ACs bound to the host provider module and
host artifacts are omitted here — AC1/AC2c for the host provider's
home-anchored opinion, AC3/AC3b (host bootstrap-snapshot layout), AC4b/AC4c's
memory_db_unavailable branch (host memory.db default semantics) and AC7's
success half (drives the host's real inject-learnings.ts CLI).

RED-phase tests for GH1471 — learning injection: canonical injector path +
non-silent failure.

Spec: SHARED/memory/Decisions/gh1471_learning_inject_path_and_visibility_spec.md
AC1-AC7 below. AC8 (no guard weakened) is already covered by the existing
sibling suites (test_gh878_seam_rename.py, test_GH1468_inject_failure_diagnosability.py,
test_09F250F4_inject_callout.py) and is deliberately NOT duplicated here.

Module trap (OFI 74b135e9): engine_py/ AND engine_py/bytedigger_engine/workflows/ are BOTH on
sys.path, so phase_05_inject exists as TWO distinct module objects — a bare
top-level "phase_05_inject" (resolved because engine_py/bytedigger_engine/workflows/ is itself
on sys.path) and "workflows.phase_05_inject" (resolved because engine_py/
is on sys.path and workflows/ is a real package — workflows/__init__.py does
`from .phase_05_inject import phase_05_inject_workflow`). run.py's real engine
only ever touches the SECOND one. Every import/monkeypatch/assert below
targets `workflows.phase_05_inject` explicitly (aliased locally as
`phase_05_inject` for readability) — never the bare top-level name, which a
different test module elsewhere in the suite may have already cached in
sys.modules["phase_05_inject"] as an independent, un-patched copy.

config_provider.py and hal_config_provider.py each have exactly ONE module
location (engine_py/bytedigger_engine/config_provider.py, engine_py/bytedigger_engine/hal_config_provider.py —
neither is duplicated under engine_py/bytedigger_engine/workflows/), so a bare `import
config_provider` / `import hal_config_provider` IS the single canonical copy
that workflows.phase_05_inject itself binds via
`from config_provider import get_config` — there is no second copy to
diverge from, unlike phase_05_inject itself.

§1i: every test below that installs a fake provider factory via
config_provider.set_default_config_provider_factory restores it with
config_provider.reset_default_config_provider_factory() in a try/finally, so
no test leaks global provider state to its siblings.

§1l: AC7 is the live side-effect AC — it runs the REAL workflow engine
against a REAL temp events.jsonl and a REAL temp sqlite memory.db, and reads
both the events.jsonl and hal-memory.md back off disk afterwards. Nothing in
AC7 mocks the unit under test.

§1q / D1CF5FDF: `inject_learnings_ts_path` does not exist on either provider
class yet; it is never imported by name at module level — every reference is
a runtime attribute access inside a test body, so missing-symbol failures
surface at assert time, not at collection time.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3 as _stdlib_sqlite3
import stat
import sys
import textwrap
from pathlib import Path

import pytest

# conftest-import-time singleton already installed engine_py + workflows/ on
# sys.path (§1q / 81F97F3D) — no module-level sys.path manipulation here.

from bytedigger_engine.workflows import phase_05_inject as phase_05_inject  # noqa: E402 — the module the real engine executes
from bytedigger_engine import config_provider  # noqa: E402 — single canonical copy
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402

_HAVE_BUN = bool(shutil.which("bun"))


def test_module_identity_matches_the_module_the_real_engine_executes():
    """Sanity guard for the module trap itself: confirms the module object we
    import/patch throughout this file IS sys.modules["bytedigger_engine.workflows.phase_05_inject"]
    — the same one workflows/__init__.py wires into the real WorkflowEngine.
    """
    assert sys.modules["bytedigger_engine.workflows.phase_05_inject"] is phase_05_inject


# ─── shared fixture helpers (mirrors test_GH1468 / test_09F250F4 harness) ────


def _write_minimal_checklist(scratchpad: Path, session_id: str) -> None:
    scratchpad.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "session_id": session_id,
        "complexity": "SIMPLE",
        "mode": "AUTONOMOUS",
        "cwd": str(scratchpad.parent),
        "pre_build_gate_version": "1.0.0",
        "written_at_ts": 1750000000,
    }
    (scratchpad / ".orchestrator-checklist.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _make_ctx(scratchpad: Path, session_id: str = "test-session-GH1471", **org_extra) -> WorkflowContext:
    _write_minimal_checklist(scratchpad, session_id)
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="task",
        session_id=session_id,
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_fixture_db(tmp_path: Path, rows: list[dict], name: str = "memory.db") -> Path:
    """Minimal memories + memories_fts fixture DB — schema identical to the
    GH1468 / 09F250F4 harness."""
    db_path = tmp_path / name
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = _stdlib_sqlite3.connect(str(db_path))
    con.execute(
        """CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'semantic',
            category TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            hash TEXT NOT NULL UNIQUE,
            agent_id TEXT,
            run_id TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            metadata TEXT
        )"""
    )
    con.execute(
        """CREATE VIRTUAL TABLE memories_fts USING fts5(
            content,
            category,
            content=memories,
            content_rowid=id,
            tokenize='porter unicode61'
        )"""
    )
    for row in rows:
        cur = con.execute(
            "INSERT INTO memories (id, user_id, content, type, hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 0)",
            (row["id"], "test", row["content"], row.get("type", "learning"), row["hash"]),
        )
        rowid = cur.lastrowid
        con.execute(
            "INSERT INTO memories_fts (rowid, content, category) VALUES (?, ?, ?)",
            (rowid, row["content"], row.get("category", "")),
        )
    con.commit()
    con.close()
    return db_path


def _write_fake_bun(tmp_path: Path, dirname: str, plan: list[dict]) -> Path:
    """Pre-stage a fake `bun` binary driven by a python3 script reading its
    stdout/stderr/exit_code from an on-disk JSON plan (GH1468 pattern —
    content never has to survive shell quoting).

    plan[i] = {"stdout": str, "stderr": str, "exit_code": int}
    Invocation N (0-based) uses plan[N], clamped to len(plan)-1 for repeats
    (lets a single-entry plan answer both the --present and --absent retry
    calls identically).

    §1i: written to disk BEFORE any workflow invocation reads it.
    """
    fake_dir = tmp_path / dirname
    fake_dir.mkdir(exist_ok=True)
    plan_path = fake_dir / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    counter_path = fake_dir / "counter.txt"
    fake_bun = fake_dir / "bun"

    script = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        PLAN_PATH = {str(plan_path)!r}
        COUNTER_PATH = {str(counter_path)!r}

        with open(PLAN_PATH, "r", encoding="utf-8") as f:
            plan = json.load(f)

        n = 0
        if os.path.exists(COUNTER_PATH):
            with open(COUNTER_PATH, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                n = int(raw) if raw else 0
        with open(COUNTER_PATH, "w", encoding="utf-8") as f:
            f.write(str(n + 1))

        idx = min(n, len(plan) - 1)
        entry = plan[idx]

        sys.stdout.write(entry.get("stdout", ""))
        sys.stderr.write(entry.get("stderr", ""))
        sys.exit(entry.get("exit_code", 0))
        """)
    fake_bun.write_text(script, encoding="utf-8")
    fake_bun.chmod(fake_bun.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake_bun


@pytest.fixture(autouse=True)
def _cwd_outside_engine_checkout(tmp_path, monkeypatch):
    """bytedigger: the engine-repo root is the checkout root, so a cwd inside it
    takes the recursive-build branch (which wants a MANIFEST.md there). Run
    every test from tmp_path, as a foreign project would; a test that needs a
    different cwd sets its own."""
    monkeypatch.chdir(tmp_path)


def _run_workflow(
    tmp_path: Path,
    db_path: Path,
    task_description: str,
    log_path: Path,
    session_id: str = "test-session-GH1471",
    **org_extra,
):
    """Execute the REAL workflows.phase_05_inject workflow via WorkflowEngine
    against a DEDICATED log file. Returns (result, all_events, scratchpad)."""
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("p05", phase_05_inject.phase_05_inject_workflow())
    scratchpad = log_path.parent / f"scratch_{log_path.stem}"
    result, _ = eng.execute(
        "p05",
        _make_ctx(
            scratchpad,
            session_id=session_id,
            task_description=task_description,
            memory_db_path=str(db_path),
            **org_extra,
        ),
    )
    events = EventLog(log_path).read_all()
    return result, events, scratchpad


# ─── AC1: provider seam carries the injector path ────────────────────────────


def test_ac1_default_provider_inject_learnings_ts_path_returns_empty_string():
    """AC1: _DefaultConfigProvider.inject_learnings_ts_path() returns "" — the
    neutral 'no host opinion' default; OSS/neutral behaviour unchanged.

    RED: inject_learnings_ts_path does not exist on _DefaultConfigProvider —
    AttributeError raised at THIS call, not at import time (the class itself
    already exists and imports fine today).
    """
    provider = config_provider._DefaultConfigProvider()
    result = provider.inject_learnings_ts_path()
    assert result == "", (
        f"_DefaultConfigProvider.inject_learnings_ts_path() must return '' "
        f"(no host opinion), got {result!r}"
    )


# ─── MAJOR-1 guard + MAJOR-3 (AC1b): Protocol isolation / minimal-provider safety ──


def test_ac1_guard_9ab32375_isinstance_surface_unaffected_by_off_protocol_addition():
    """MAJOR-1 guard: inject_learnings_ts_path() lives OFF the runtime_checkable
    ConfigProvider Protocol (mirrors foreign_state_dirname, config_provider.py:
    222-224). A 6-method concrete class -- the exact ConcreteProvider shape
    from test_9AB32375_config_provider.py:76-98 -- must still satisfy
    isinstance(obj, ConfigProvider) is True.

    This is a REGRESSION GUARD, not a RED: it must PASS both before and
    after GREEN. If a future GREEN mistakenly adds inject_learnings_ts_path
    to the Protocol's abstract method list, this isinstance check flips to
    False and this guard starts failing -- exactly the MAJOR-1 gate finding.
    """
    obj_cls_methods = config_provider.ConfigProvider

    class ConcreteProvider:
        def gate_enabled(self, env_var: str) -> bool:
            return True

        def flag(self, env_var: str) -> bool:
            return False

        def timeout_ms(self, env_var: str, default: int) -> int:
            return default

        def binary(self, env_var: str, default: str) -> str:
            return default

        def hal_root(self) -> Path:
            return Path("/fake/hal")

        def path(self, env_var: str, default: Path) -> Path:
            return default

    obj = ConcreteProvider()
    assert isinstance(obj, obj_cls_methods) is True, (
        "A 6-method-only ConcreteProvider (identical to the 9AB32375 guard "
        "surface) must satisfy ConfigProvider -- inject_learnings_ts_path "
        "must stay off-Protocol"
    )
    assert not hasattr(ConcreteProvider, "inject_learnings_ts_path")


def test_ac1b_minimal_six_method_provider_does_not_raise_on_injector_path_resolution(monkeypatch):
    """AC1b (MAJOR-3): a provider implementing ONLY the 6 Protocol methods
    (no inject_learnings_ts_path override at all) must resolve the injector
    path without raising AttributeError -- access must go through
    getattr(get_config(), "inject_learnings_ts_path", None), falling back to
    "" per config_provider.py:222-224's foreign_state_dirname() pattern. A
    naive `get_config().inject_learnings_ts_path()` direct call would raise
    AttributeError here and invert the spec's non-fatality decision.

    Falls through to the __file__-relative default (the minimal provider's
    implicit "no opinion"). Not expected to fail today -- today's resolver
    never touches inject_learnings_ts_path() at all, so no crash is possible
    yet; this guards the getattr-based access pattern once AC2's provider
    seam is wired in GREEN.
    """
    monkeypatch.delenv("HAL_INJECT_LEARNINGS_TS", raising=False)

    class _MinimalSixMethodProvider:
        def gate_enabled(self, env_var: str) -> bool:
            return True

        def flag(self, env_var: str) -> bool:
            return False

        def timeout_ms(self, env_var: str, default: int) -> int:
            return default

        def binary(self, env_var: str, default: str) -> str:
            return default

        def hal_root(self) -> Path:
            return Path("/fake/hal")

        def path(self, env_var: str, default: Path) -> Path:
            val = os.environ.get(env_var, "")
            return Path(val) if val else default

    config_provider.set_default_config_provider_factory(_MinimalSixMethodProvider)
    try:
        try:
            result = phase_05_inject._resolve_inject_ts_path()
        except AttributeError as exc:  # pragma: no cover — this IS the failure we assert against
            pytest.fail(
                f"minimal 6-method provider raised AttributeError resolving "
                f"the injector path: {exc}"
            )
    finally:
        config_provider.reset_default_config_provider_factory()

    build_dir = Path(phase_05_inject.__file__).resolve().parent.parent.parent.parent  # the same four levels the resolver climbs
    expected = build_dir / "inject-learnings.ts"
    assert Path(result) == expected, (
        f"minimal provider (no opinion) must fall through to the "
        f"__file__-derived default, got {result!r}"
    )


# ─── AC2: resolution precedence ───────────────────────────────────────────────


class _FakeProviderWithInjectPath(config_provider._DefaultConfigProvider):
    def inject_learnings_ts_path(self) -> str:
        return "/fake/provider/inject-learnings.ts"


class _FakeProviderNoOpinion(config_provider._DefaultConfigProvider):
    def inject_learnings_ts_path(self) -> str:
        return ""


def test_ac2a_env_var_wins_over_provider_opinion(monkeypatch):
    """AC2(a) / §1h: HAL_INJECT_LEARNINGS_TS env, when set, wins over the
    provider's inject_learnings_ts_path() UNCONDITIONALLY — even though the
    pinned path ('/env/wins/inject-learnings.ts') does NOT exist on disk.
    The AC2c existence-preference (added below) applies ONLY to the provider
    opinion leg (b); the env override leg (a) is never existence-gated, per
    the spec's explicit precedence order and GH1468's reliance on pointing
    HAL_INJECT_LEARNINGS_TS at never-created paths.

    Not expected to fail today; guards the precedence order post-GREEN.
    """
    assert not Path("/env/wins/inject-learnings.ts").exists()  # §1i: unconditional-ness precondition
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", "/env/wins/inject-learnings.ts")
    config_provider.set_default_config_provider_factory(_FakeProviderWithInjectPath)
    try:
        result = phase_05_inject._resolve_inject_ts_path()
    finally:
        config_provider.reset_default_config_provider_factory()
    assert str(result) == "/env/wins/inject-learnings.ts"


def test_ac2b_provider_opinion_used_when_env_unset(monkeypatch, tmp_path):
    """AC2(b) + AC2c: with env unset, get_config().inject_learnings_ts_path()
    wins over the __file__-derived default WHEN it resolves to a file that
    genuinely EXISTS on disk (AC2c's existence-preference — the provider
    opinion is a preference, not a blind override).

    A stub .ts file is pre-staged under tmp_path (§1i) so the provider
    opinion resolves to a real, existing file, isolating this assertion from
    AC2c's separate nonexistent-opinion fallback case below.

    Not expected to fail today: today's GREEN already uses the provider
    opinion unconditionally whenever non-empty, so an existing-file opinion
    already wins. This guards that the existence-preference GREEN (AC2c)
    does not regress the existing-file case.
    """
    monkeypatch.delenv("HAL_INJECT_LEARNINGS_TS", raising=False)
    stub_ts = tmp_path / "provider-inject-learnings.ts"
    stub_ts.write_text("// stub GH1471 provider-opinion injector\n", encoding="utf-8")
    assert stub_ts.exists()  # §1i precondition

    class _ExistingOpinionProvider(config_provider._DefaultConfigProvider):
        def inject_learnings_ts_path(self) -> str:
            return str(stub_ts)

    config_provider.set_default_config_provider_factory(_ExistingOpinionProvider)
    try:
        result = phase_05_inject._resolve_inject_ts_path()
    finally:
        config_provider.reset_default_config_provider_factory()
    assert str(result) == str(stub_ts), (
        f"expected the provider's inject_learnings_ts_path() to win when env "
        f"is unset and the resolved file exists, got {result!r} — resolver "
        f"still ignores the provider seam"
    )


def test_ac2c_falls_back_to_file_derived_default_when_provider_has_no_opinion(monkeypatch):
    """AC2(c): env unset AND provider.inject_learnings_ts_path() == "" ->
    falls back to the existing Path(__file__).resolve().parents[2] /
    'inject-learnings.ts' default. Not expected to fail today (this is
    today's only behaviour); guards the fallback leg post-GREEN.
    """
    monkeypatch.delenv("HAL_INJECT_LEARNINGS_TS", raising=False)
    config_provider.set_default_config_provider_factory(_FakeProviderNoOpinion)
    try:
        result = phase_05_inject._resolve_inject_ts_path()
    finally:
        config_provider.reset_default_config_provider_factory()
    build_dir = Path(phase_05_inject.__file__).resolve().parent.parent.parent.parent  # the same four levels the resolver climbs
    expected = build_dir / "inject-learnings.ts"
    assert Path(result) == expected


# ─── AC2 refinements: empty-string env alias handling + provider ~-expanduser ─


def test_ac2_empty_string_env_override_falls_through_never_becomes_empty_path(monkeypatch):
    """AC2(a) refinement: HAL_INJECT_LEARNINGS_TS='' (set but EMPTY, not
    unset) must be treated as unset -- resolution must fall through to the
    __file__-derived default, never literally construct Path(''). This must
    happen because resolution reads the override via cfg.path() (which
    already treats '' as unset via `_aliased_env_get(...) or ""`), NEVER via
    a direct os.environ.get() read (a direct read returns '' verbatim for a
    set-but-empty var, which would NOT fall through).

    Not expected to fail today: today's resolver already delegates entirely
    to get_config().path(...), which already has this "" => unset behavior.
    Included as an explicit regression guard against a GREEN that swaps in a
    direct os.environ read while adding the provider-seam precedence.
    """
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", "")
    config_provider.set_default_config_provider_factory(_FakeProviderNoOpinion)
    try:
        result = phase_05_inject._resolve_inject_ts_path()
    finally:
        config_provider.reset_default_config_provider_factory()

    assert str(result) != "", "resolver must never resolve to an empty path"
    build_dir = Path(phase_05_inject.__file__).resolve().parent.parent.parent.parent  # the same four levels the resolver climbs
    expected = build_dir / "inject-learnings.ts"
    assert Path(result) == expected, (
        f"empty-string env override must fall through to the __file__-derived "
        f"default, got {result!r}"
    )


def test_ac2_provider_opinion_with_tilde_literal_gets_expanduser(monkeypatch, tmp_path):
    """AC2(b) refinement + AC2c: when the provider's inject_learnings_ts_path()
    returns a '~'-literal (e.g. '~/custom/inject-learnings.ts'), the
    resolver must expanduser it before returning -- a raw '~' would break
    every downstream Path()/subprocess consumer that doesn't itself
    expanduser. HOME is monkeypatched to tmp_path (§1i) and the expanded
    target is pre-staged so it genuinely EXISTS -- otherwise AC2c's
    existence-preference would (correctly) reroute this to the
    __file__-derived default instead, which is not what this test targets.

    Not expected to fail today: today's GREEN already expanduser's the
    provider opinion unconditionally. This guards that the AC2c existence
    check does not regress the tilde-expansion behaviour for a genuinely
    existing target.
    """
    monkeypatch.delenv("HAL_INJECT_LEARNINGS_TS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    stub_dir = tmp_path / "custom-gh1471"
    stub_dir.mkdir()
    stub_ts = stub_dir / "inject-learnings.ts"
    stub_ts.write_text("// stub GH1471 tilde-literal injector\n", encoding="utf-8")

    class _TildeProvider(config_provider._DefaultConfigProvider):
        def inject_learnings_ts_path(self) -> str:
            return "~/custom-gh1471/inject-learnings.ts"

    config_provider.set_default_config_provider_factory(_TildeProvider)
    try:
        result = phase_05_inject._resolve_inject_ts_path()
    finally:
        config_provider.reset_default_config_provider_factory()

    expected = Path(os.path.expanduser("~/custom-gh1471/inject-learnings.ts"))
    assert expected.exists()  # §1i precondition
    assert Path(result) == expected, (
        f"expected the provider's '~'-literal expanded via os.path.expanduser, "
        f"got {result!r}"
    )
    assert "~" not in str(result), "resolved path must not contain a literal '~'"


def test_ac2d_discarded_provider_opinion_named_in_callout_failed_payload(tmp_path, monkeypatch):
    """AC2d: when the provider's inject_learnings_ts_path() opinion is
    non-empty but DISCARDED because the file does not exist (AC2c), AND the
    run subsequently fails, the emitted learning_inject_callout_failed
    payload must carry the discarded path under 'discarded_injector_opinion'.
    Without this the diagnostic names only the __file__-derived snapshot
    path -- the exact misleading string that misdirected issue #1471's own
    diagnosis for two weeks. The operator must be able to see which
    canonical path was looked for and missed.

    §1i: both halves of the discard are pre-staged deterministically before
    the workflow runs -- the provider-opinion path is asserted absent, and
    workflows.phase_05_inject.__file__ is monkeypatched into a simulated
    bootstrap-snapshot dir (mirrors the AC3 fixture) that ALSO has no
    inject-learnings.ts, so the AC2c __file__-derived fallback the resolver
    falls through to is genuinely nonexistent too -- the resolved final path
    is nonexistent either way. A pre-staged fake bun stub (GH1468 pattern,
    never the real bun binary) then fails deterministically, forcing exactly
    one learning_inject_callout_failed emission to inspect -- no reliance on
    real-bun timing or availability.

    RED: today's _emit_callout_failed (phase_05_inject.py:115-191) has no
    knowledge of the discarded provider opinion at all -- its payload has no
    'discarded_injector_opinion' key, so this assertion fails against the
    real on-disk events.jsonl.
    """
    monkeypatch.delenv("HAL_INJECT_LEARNINGS_TS", raising=False)

    discarded_opinion = tmp_path / "discarded-opinion-gh1471ac2d" / "inject-learnings.ts"
    assert not discarded_opinion.exists()  # §1i precondition

    class _DiscardedOpinionProvider(config_provider._DefaultConfigProvider):
        def inject_learnings_ts_path(self) -> str:
            return str(discarded_opinion)

    snapshot_root = tmp_path / "hal-build-bootstrap-AC2D"
    snapshot_engine_workflows = snapshot_root / "engine_py" / "workflows"
    snapshot_engine_workflows.mkdir(parents=True)
    fake_module_file = snapshot_engine_workflows / "phase_05_inject.py"
    assert not (snapshot_root / "inject-learnings.ts").exists()  # §1i precondition

    monkeypatch.setattr(phase_05_inject, "__file__", str(fake_module_file))

    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac2d-learn-1",
        "content": "LESSON: discarded injector opinion must be named in the diagnostic",
        "type": "learning",
        "hash": "gh1471ac2dhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac2d", [
        {"stdout": "", "stderr": "simulated cli crash", "exit_code": 1},
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    config_provider.set_default_config_provider_factory(_DiscardedOpinionProvider)
    try:
        # Sanity: confirm the resolver genuinely discards the opinion (AC2c,
        # already GREEN) before asserting on the diagnostic payload -- if
        # this ever regresses to using the opinion directly, this test
        # should fail HERE rather than mask a different defect.
        resolved = phase_05_inject._resolve_inject_ts_path()
        assert Path(resolved) != discarded_opinion, (
            "precondition: the provider opinion must genuinely be discarded "
            f"(AC2c) for this test to exercise AC2d -- got resolved={resolved!r}"
        )

        log_path = tmp_path / "events_ac2d.jsonl"
        result, events, scratchpad = _run_workflow(
            tmp_path, db_path,
            "discarded injector opinion must be named in the diagnostic",
            log_path,
        )
    finally:
        config_provider.reset_default_config_provider_factory()

    failed = [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    assert failed, (
        f"expected 'learning_inject_callout_failed' in the real events.jsonl "
        f"at {log_path}; got event types: {[e['event_type'] for e in events]}"
    )
    assert failed[-1]["payload"].get("discarded_injector_opinion") == str(discarded_opinion), (
        f"expected the discarded provider opinion {str(discarded_opinion)!r} "
        f"under 'discarded_injector_opinion' in the callout-failed payload, "
        f"got {failed[-1]['payload']!r}"
    )


def test_ac2d_key_absent_when_env_override_wins(tmp_path, monkeypatch):
    """AC2d hole (gate pass 4, MINOR-3), shape (a): env override wins
    outright, so no provider opinion is ever consulted -- there is nothing to
    discard. Asserts 'discarded_injector_opinion' is missing from the
    callout-failed payload entirely (`not in payload`), not merely None --
    per AC2d's own wording: 'included in the payload ONLY when an opinion was
    actually discarded -- no schema noise on normal runs'.

    This is NOT a RED: today's _emit_callout_failed already gates insertion
    on `if discarded_injector_opinion is not None` (phase_05_inject.py:
    161-162, 198-199), so this test is expected to PASS against the current
    implementation. It is a forward shield against a future over-inclusion
    regression (the exact inert-guard hole gate pass 4 flagged), not a
    forcing-function RED.

    Split from the original combined shape (a)/(b) test (which crashed with
    sqlite3.OperationalError: table memories already exists because it
    reused ONE tmp_path/db filename across two _make_fixture_db calls): this
    function gets its own pytest-fresh tmp_path and calls _make_fixture_db
    exactly once against the default 'memory.db' filename under it, so there
    is no collision.

    §1i: the provider is swapped for one with NO opinion at all
    (_FakeProviderNoOpinion, defined above) before the env override is read,
    so there is nothing to discard. The run is then forced to fail via a
    pre-staged fake bun (GH1468 pattern, exit_code=1) so
    learning_inject_callout_failed is genuinely emitted to inspect -- no
    reliance on real-bun timing.

    §1i (module trap, D1CF5FDF): the provider-factory swap is restored in a
    try/finally via config_provider.reset_default_config_provider_factory()
    so this test does not leak global provider state to sibling tests.
    """
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", "/env/wins/no-discard/inject-learnings.ts")
    config_provider.set_default_config_provider_factory(_FakeProviderNoOpinion)
    try:
        resolved, discarded = phase_05_inject._resolve_inject_ts_path_diag()
    finally:
        config_provider.reset_default_config_provider_factory()
    assert discarded is None, (
        "precondition: a no-opinion provider must never populate "
        f"discarded_opinion, got {discarded!r}"
    )
    assert str(resolved) == "/env/wins/no-discard/inject-learnings.ts"

    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac2d-nodiscard-a-learn-1",
        "content": "LESSON: env override wins outright, no discard to report",
        "type": "learning",
        "hash": "gh1471ac2dnodiscardahash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac2d_nodiscard_a", [
        {"stdout": "", "stderr": "simulated cli crash", "exit_code": 1},
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    config_provider.set_default_config_provider_factory(_FakeProviderNoOpinion)
    try:
        _, events, _ = _run_workflow(
            tmp_path, db_path,
            "env override wins outright no discard to report",
            tmp_path / "events_ac2d_nodiscard_a.jsonl",
            session_id="test-session-GH1471-ac2d-nodiscard-a",
        )
    finally:
        config_provider.reset_default_config_provider_factory()

    failed = [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    assert failed, (
        f"expected 'learning_inject_callout_failed' in the real events.jsonl; "
        f"got event types: {[e['event_type'] for e in events]}"
    )
    assert "discarded_injector_opinion" not in failed[-1]["payload"], (
        f"env-override-wins/no-opinion shape must NOT carry "
        f"'discarded_injector_opinion' at all, got "
        f"{failed[-1]['payload']!r}"
    )


def test_ac2d_key_absent_when_provider_opinion_exists_and_is_used(tmp_path, monkeypatch):
    """AC2d hole (gate pass 4, MINOR-3), shape (b): the provider's
    inject_learnings_ts_path() opinion resolves to a file that genuinely
    EXISTS on disk, so the resolver USES it (AC2c) instead of discarding it
    -- nothing was discarded. Asserts 'discarded_injector_opinion' is
    missing from the callout-failed payload entirely (`not in payload`), not
    merely None.

    This is NOT a RED: today's _emit_callout_failed already gates insertion
    on `if discarded_injector_opinion is not None` (phase_05_inject.py:
    161-162, 198-199), so this test is expected to PASS against the current
    implementation. It is a forward shield against a future over-inclusion
    regression, not a forcing-function RED.

    Split from the original combined shape (a)/(b) test (which crashed with
    sqlite3.OperationalError: table memories already exists because it
    reused ONE tmp_path/db filename across two _make_fixture_db calls): this
    function gets its own pytest-fresh tmp_path and calls _make_fixture_db
    exactly once against the default 'memory.db' filename under it, so there
    is no collision.

    §1i: a stub, opinion-bearing provider (_UsedOpinionProvider, defined
    inline) is installed whose inject_learnings_ts_path() points at a
    pre-staged .ts stub file that is asserted to exist BEFORE the workflow
    runs, so its opinion is used, not discarded. The run is then forced to
    fail via a pre-staged fake bun (GH1468 pattern, exit_code=1) so
    learning_inject_callout_failed is genuinely emitted to inspect -- no
    reliance on real-bun timing.

    §1i (module trap, D1CF5FDF): the provider-factory swap is restored in a
    try/finally via config_provider.reset_default_config_provider_factory()
    so this test does not leak global provider state to sibling tests.
    """
    monkeypatch.delenv("HAL_INJECT_LEARNINGS_TS", raising=False)

    used_opinion = tmp_path / "used-opinion-gh1471ac2d" / "inject-learnings.ts"
    used_opinion.parent.mkdir(parents=True)
    used_opinion.write_text("// stub injector CLI for AC2d shape (b)\n")
    assert used_opinion.exists()  # §1i precondition

    class _UsedOpinionProvider(config_provider._DefaultConfigProvider):
        def inject_learnings_ts_path(self) -> str:
            return str(used_opinion)

    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac2d-nodiscard-b-learn-1",
        "content": "LESSON: provider opinion exists and is used, no discard to report",
        "type": "learning",
        "hash": "gh1471ac2dnodiscardbhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac2d_nodiscard_b", [
        {"stdout": "", "stderr": "simulated cli crash", "exit_code": 1},
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    config_provider.set_default_config_provider_factory(_UsedOpinionProvider)
    try:
        resolved, discarded = phase_05_inject._resolve_inject_ts_path_diag()
        assert discarded is None, (
            f"precondition: an existing, used provider opinion must never "
            f"populate discarded_opinion, got {discarded!r}"
        )
        assert Path(resolved) == used_opinion

        _, events, _ = _run_workflow(
            tmp_path, db_path,
            "provider opinion exists and is used no discard to report",
            tmp_path / "events_ac2d_nodiscard_b.jsonl",
            session_id="test-session-GH1471-ac2d-nodiscard-b",
        )
    finally:
        config_provider.reset_default_config_provider_factory()

    failed = [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    assert failed, (
        f"expected 'learning_inject_callout_failed' in the real events.jsonl; "
        f"got event types: {[e['event_type'] for e in events]}"
    )
    assert "discarded_injector_opinion" not in failed[-1]["payload"], (
        f"used-existing-opinion shape must NOT carry "
        f"'discarded_injector_opinion' at all, got "
        f"{failed[-1]['payload']!r}"
    )


# ─── AC3: bootstrap-snapshot regression (the actual production bug) ──────────


# ─── AC4: failure marker distinguishable from genuine zero-match ─────────────


def test_ac4_inject_callout_failure_gets_distinct_marker_not_no_match(tmp_path, monkeypatch):
    """AC4: hal-memory.md gets <!-- HAL_MEMORY:INJECT_FAILED --> when the
    injector broke — never the <!-- HAL_MEMORY:NO_MATCH --> marker reserved
    for a genuine zero-match query. §1i: fake bun + fixture DB pre-staged
    before the workflow runs.

    RED: today EVERY "no rows returned" branch (including a callout failure)
    collapses onto the same NO_MATCH marker — this assertion fails on the
    actual marker text written to the real on-disk hal-memory.md.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac4-learn-1",
        "content": "LESSON: marker distinguishability matters for injector failures",
        "type": "learning",
        "hash": "gh1471ac4hash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac4", [
        {"stdout": "", "stderr": "simulated cli crash", "exit_code": 1},
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    _, _, scratchpad = _run_workflow(
        tmp_path, db_path, "marker distinguishability matters", tmp_path / "events_ac4.jsonl"
    )

    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    assert "<!-- HAL_MEMORY:INJECT_FAILED -->" in body, (
        f"expected the INJECT_FAILED marker for a genuine callout failure, "
        f"got body: {body!r}"
    )
    assert "<!-- HAL_MEMORY:NO_MATCH -->" not in body, (
        "INJECT_FAILED and NO_MATCH markers must never collide on a real callout failure"
    )


def test_ac4_genuine_zero_match_still_gets_no_match_marker(tmp_path, monkeypatch):
    """AC4 companion guard: a real zero-FTS-hit query (no callout failure at
    all — the CLI ran fine and just found nothing) must keep the plain
    NO_MATCH marker. Not expected to fail today; protects the non-collision
    half of AC4 post-GREEN.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac4b-learn-1",
        "content": "totally unrelated content that will not match",
        "type": "learning",
        "hash": "gh1471ac4bhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac4b", [
        {
            "stdout": json.dumps({
                "rows": [], "fts_total_hits": 0, "top_bm25": None,
                "returned_count": 0, "mode": "absent",
            }),
            "stderr": "",
            "exit_code": 0,
        },
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    _, _, scratchpad = _run_workflow(
        tmp_path, db_path, "zzz nonmatching keywordzz", tmp_path / "events_ac4b.jsonl"
    )

    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    assert "<!-- HAL_MEMORY:NO_MATCH -->" in body
    assert "<!-- HAL_MEMORY:INJECT_FAILED -->" not in body


# ─── AC4b/AC4c: every classification branch, memory_db_unavailable event ─────
#
# The full table (AC4b):
#   [no_keywords_extracted]              -> no_match  (this section, new)
#   [fts_hits=0]                         -> no_match  (already covered above
#                                            by test_ac6_step_result_carries_
#                                            learning_injection_no_match)
#   [...build_hits=0...]                 -> no_match  (this section, new)
#   [memory_db_unavailable]              -> failed    (this section, new; AC4c)
#   [inject_cli_failed:<reason>]         -> failed    (already covered above
#                                            by test_ac6_step_result_carries_
#                                            learning_injection_failed)


def test_ac4b_classification_no_keywords_extracted_is_no_match(tmp_path, monkeypatch):
    """AC4b classification table: '[no_keywords_extracted]' -> 'no_match'.
    A stop-words-only task description never reaches the FTS query at all --
    _sanitize_fts_query returns None before any subprocess spawns.

    RED: learning_injection key absent from result.data today.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac4b-nk-learn-1",
        "content": "irrelevant fixture row",
        "type": "learning",
        "hash": "gh1471ac4bnkhash01",
    }])

    result, _, scratchpad = _run_workflow(
        tmp_path, db_path, "the a of it is", tmp_path / "events_ac4b_nk.jsonl"
    )

    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "no_match", (
        f"expected 'no_match' for [no_keywords_extracted], got "
        f"{result.data.get('learning_injection')!r}"
    )
    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    assert "[no_keywords_extracted]" in body


def test_ac4b_classification_build_hits_zero_after_type_filter_is_no_match(tmp_path, monkeypatch):
    """AC4b classification table: '[fts_hits=N, build_hits=0, type_filter=...]'
    (case c: FTS matched rows but the type filter dropped them all) ->
    'no_match'. Simulated via a fake inject-learnings.ts reporting
    fts_total_hits > 0 with an empty rows list (mirrors what the real CLI
    emits when every FTS hit is a non-build type like 'preference').

    RED: learning_injection key absent from result.data today.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac4b-bh-learn-1",
        "content": "irrelevant fixture row",
        "type": "learning",
        "hash": "gh1471ac4bbhhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac4b_bh", [
        {
            "stdout": json.dumps({
                "rows": [], "fts_total_hits": 3, "top_bm25": None,
                "returned_count": 0, "mode": "absent",
            }),
            "stderr": "",
            "exit_code": 0,
        },
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    result, _, scratchpad = _run_workflow(
        tmp_path, db_path, "build hits zero classification", tmp_path / "events_ac4b_bh.jsonl"
    )

    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "no_match", (
        f"expected 'no_match' for the fts_hits=N/build_hits=0 branch, got "
        f"{result.data.get('learning_injection')!r}"
    )
    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    assert "build_hits=0" in body


def test_ac4c_ts_error_kind_memory_db_unavailable_emits_exactly_once_not_twice(
    tmp_path, monkeypatch
):
    """AC4c companion (gate pass 2 MINOR fix): drives the OTHER carrier of the
    literal string 'memory_db_unavailable' -- the TS error-kind branch at
    phase_05_inject.py:557-568, reached when inject-learnings.ts itself
    reports {"error": "memory_db_unavailable"} in its JSON stdout (as opposed
    to the Python-side preflight check at :441-442 covered by the sibling
    test above). This branch ALREADY emits its own
    'learning_inject_callout_failed' at :567 today. A GREEN that keys the new
    AC4c emission on a substring of the resulting
    "[inject_cli_failed:memory_db_unavailable]" suffix -- instead of on the
    :441-442 preflight branch specifically -- would emit a SECOND event here.
    Exactly one is correct.

    §1i: fake bun pre-staged before the workflow runs.

    Not a RED on the event-count half: only one emission (the pre-existing
    :567 call) happens on this path today, so len(failed) == 1 already
    holds -- this half is a forward regression guard against a
    substring-keyed GREEN, per the spec's explicit double-emit warning. The
    learning_injection classification assertion below IS the forcing half:
    the key is absent from result.data today.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac4c-tskind-learn-1",
        "content": "irrelevant fixture row",
        "type": "learning",
        "hash": "gh1471ac4ctskindhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac4c_tskind", [
        {
            "stdout": json.dumps({"error": "memory_db_unavailable"}),
            "stderr": "",
            "exit_code": 1,
        },
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    result, events, scratchpad = _run_workflow(
        tmp_path, db_path, "ts error kind memory db unavailable double emit guard",
        tmp_path / "events_ac4c_tskind.jsonl",
    )

    failed = [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    assert len(failed) == 1, (
        f"expected exactly ONE 'learning_inject_callout_failed' event on the "
        f"TS-error-kind memory_db_unavailable path (the pre-existing :567 "
        f"emission) -- a substring-keyed GREEN would double-emit here; got "
        f"{len(failed)}: {[e['event_type'] for e in events]}"
    )
    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "failed", (
        f"expected 'failed' classification for the "
        f"[inject_cli_failed:memory_db_unavailable] suffix per the AC4b "
        f"table, got {result.data.get('learning_injection')!r}"
    )
    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    assert "[inject_cli_failed:memory_db_unavailable]" in body, (
        f"expected the [inject_cli_failed:memory_db_unavailable] suffix in "
        f"the real hal-memory.md, got body: {body!r}"
    )


def test_ac4_marker_selection_ignores_literal_inject_failed_substring_in_row_content(
    tmp_path, monkeypatch
):
    """AC4: marker selection is driven by the AC4b classification value,
    NEVER by substring-scanning the body text. A genuine successful match
    whose row content happens to contain the literal string 'INJECT_FAILED'
    must still produce the MATCHED marker -- not get miscategorized by an
    implementation that naively checks `"INJECT_FAILED" in hal_memory`.

    Two assertions here: (1) the marker-collision shield itself, which
    already passes today (today's marker logic checks NO_LEARNINGS_SENTINEL
    equality, not body substring-scanning) -- protects against a plausible-
    but-wrong GREEN; (2) the AC6 'learning_injection' == 'ok' check, which
    IS expected to fail today (key absent).
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac4-collision-learn-1",
        "content": "LESSON: watch out for INJECT_FAILED strings in learning content",
        "type": "learning",
        "hash": "gh1471ac4collhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac4_coll", [
        {
            "stdout": json.dumps({
                "rows": [{"content": "LESSON: watch out for INJECT_FAILED strings in learning content",
                          "confidence": 0.8, "score": -1.5}],
                "fts_total_hits": 1, "top_bm25": -1.5, "returned_count": 1, "mode": "absent",
            }),
            "stderr": "",
            "exit_code": 0,
        },
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    result, _, scratchpad = _run_workflow(
        tmp_path, db_path, "watch out for INJECT_FAILED strings", tmp_path / "events_ac4_coll.jsonl"
    )

    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    assert body.count("<!-- HAL_MEMORY:") == 1, f"expected exactly one marker comment, got body: {body!r}"
    assert "<!-- HAL_MEMORY:MATCHED" in body, (
        f"a genuine match whose content contains the literal 'INJECT_FAILED' "
        f"must still get the MATCHED marker, got body: {body!r}"
    )
    assert "<!-- HAL_MEMORY:INJECT_FAILED -->" not in body
    assert result.data.get("learning_injection") == "ok", (
        f"expected 'ok' on this genuine-match-with-collision path, got "
        f"{result.data.get('learning_injection')!r}"
    )


# ─── AC5: failure is visible on stderr (never stdout) ─────────────────────────


def test_ac5_soft_failure_writes_warning_line_to_stderr_never_stdout(tmp_path, monkeypatch, capsys):
    """AC5: on any soft-failure branch the phase writes ONE explicit line to
    stderr (never stdout — safety.md hook-stdout rule) of the form
    'WARNING: learning injection unavailable (<reason>); build proceeds
    without learnings'.

    RED: today _write_injection_files writes no such line to either stream
    on the callout-failure branch — both substring checks fail.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac5-learn-1",
        "content": "LESSON: stderr visibility matters for silent failures",
        "type": "learning",
        "hash": "gh1471ac5hash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac5", [
        {"stdout": "", "stderr": "simulated cli crash", "exit_code": 1},
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    _run_workflow(tmp_path, db_path, "stderr visibility matters", tmp_path / "events_ac5.jsonl")

    captured = capsys.readouterr()
    assert "WARNING: learning injection unavailable" in captured.err, (
        f"expected the WARNING line on stderr; got stderr={captured.err!r} "
        f"stdout={captured.out!r}"
    )
    assert "build proceeds without learnings" in captured.err
    assert "WARNING: learning injection unavailable" not in captured.out, (
        "the WARNING line must never be written to stdout (safety.md hook-stdout rule)"
    )


def test_ac5_soft_failure_writes_exactly_one_stderr_line_not_a_substring_match(
    tmp_path, monkeypatch, capsys
):
    """AC5 (MINOR gate fix): the WARNING must be exactly ONE line on stderr
    -- a line-COUNT check, not merely a substring occurrence. A substring
    check alone would pass even if the implementation printed extra blank
    lines, a traceback, or repeated/duplicated the warning across two
    logger.warning + sys.stderr.write calls (logger.warning escapes capsys
    on its own handler but a MIXED implementation could still leak partial
    text). Splitting captured stderr on newlines and asserting length == 1
    pins the direct-single-write contract precisely.

    RED: today _write_injection_files emits nothing to stderr at all on
    this branch -- captured.err is empty, so len(lines) == 0 != 1.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac5b-learn-1",
        "content": "LESSON: exact single-line stderr visibility",
        "type": "learning",
        "hash": "gh1471ac5bhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac5b", [
        {"stdout": "", "stderr": "simulated cli crash", "exit_code": 1},
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    _run_workflow(tmp_path, db_path, "exact single line stderr", tmp_path / "events_ac5b.jsonl")

    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1, (
        f"expected exactly ONE stderr line for the WARNING, got {len(lines)} "
        f"line(s): {lines!r}"
    )
    assert lines[0].startswith("WARNING: learning injection unavailable"), (
        f"expected the single stderr line to be the WARNING itself, got {lines[0]!r}"
    )


# ─── AC6: StepResult carries learning_injection status ───────────────────────


def test_ac6_step_result_carries_learning_injection_ok(tmp_path, monkeypatch):
    """AC6: on a genuine match, StepResult.data['learning_injection'] == 'ok'.

    RED: the key does not exist today (result.data.get(...) is None != 'ok').
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac6-learn-1",
        "content": "LESSON: step result visibility for synthesizers",
        "type": "learning",
        "hash": "gh1471ac6hash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac6ok", [
        {
            "stdout": json.dumps({
                "rows": [{"content": "LESSON: step result visibility for synthesizers",
                          "confidence": 0.8, "score": -2.0}],
                "fts_total_hits": 1, "top_bm25": -2.0, "returned_count": 1, "mode": "absent",
            }),
            "stderr": "",
            "exit_code": 0,
        },
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    result, _, _ = _run_workflow(
        tmp_path, db_path, "step result visibility", tmp_path / "events_ac6ok.jsonl"
    )

    assert result.status == "ok", (result.error_code, result.error, result.step_name)
    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "ok", (
        f"expected StepResult.data['learning_injection'] == 'ok' on a genuine "
        f"match, got {result.data.get('learning_injection')!r} (data keys: "
        f"{sorted(result.data.keys())})"
    )


def test_ac6_step_result_carries_learning_injection_failed(tmp_path, monkeypatch):
    """AC6: on a genuine callout failure, StepResult.data['learning_injection']
    == 'failed', and status stays 'ok' (non-fatal per the spec's fatality
    decision).

    RED: the key does not exist today.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac6f-learn-1",
        "content": "LESSON: step result failure visibility",
        "type": "learning",
        "hash": "gh1471ac6fhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac6fail", [
        {"stdout": "", "stderr": "simulated cli crash", "exit_code": 1},
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    result, _, _ = _run_workflow(
        tmp_path, db_path, "step result failure visibility", tmp_path / "events_ac6fail.jsonl"
    )

    assert result.status == "ok", "injection failure must stay non-fatal (spec fatality decision)"
    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "failed", (
        f"expected 'failed' on a genuine callout failure, got "
        f"{result.data.get('learning_injection')!r}"
    )


def test_ac6_step_result_carries_learning_injection_no_match(tmp_path, monkeypatch):
    """AC6: on a genuine zero-hit query (CLI ran fine, found nothing),
    StepResult.data['learning_injection'] == 'no_match'.

    RED: the key does not exist today.
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac6nm-learn-1",
        "content": "totally unrelated content only, no overlap at all",
        "type": "learning",
        "hash": "gh1471ac6nmhash01",
    }])
    fake_bun = _write_fake_bun(tmp_path, "fake_bin_ac6nm", [
        {
            "stdout": json.dumps({
                "rows": [], "fts_total_hits": 0, "top_bm25": None,
                "returned_count": 0, "mode": "absent",
            }),
            "stderr": "",
            "exit_code": 0,
        },
    ])
    monkeypatch.setenv("HAL_BUN_BIN", str(fake_bun))

    result, _, _ = _run_workflow(
        tmp_path, db_path, "zzz nonmatching keywordzz", tmp_path / "events_ac6nm.jsonl"
    )

    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "no_match", (
        f"expected 'no_match' on a genuine zero-hit query, got "
        f"{result.data.get('learning_injection')!r}"
    )


# ─── AC6b: §1ab — learning_injection defined on EVERY entry path ─────────────


def test_ac6b_learning_injection_present_on_hal_memory_block_entry_path(tmp_path):
    """AC6b (§1ab): result.data['learning_injection'] must carry the exact
    value 'no_match' (never absent/None, and never merely truthy) even on
    the hal_memory_block entry path, where cfg.get('hal_memory_block') is
    explicitly supplied and _query_memory_learnings is never called at all.

    RED: today result.data has no 'learning_injection' key on ANY entry
    path -- this one included.
    """
    scratchpad = tmp_path / "scratch_ac6b_block"
    ctx = _make_ctx(scratchpad, session_id="test-session-ac6b-block",
                     hal_memory_block="pre-formatted block text")
    log = EventLog(tmp_path / "events_ac6b_block.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p05", phase_05_inject.phase_05_inject_workflow())
    result, _ = eng.execute("p05", ctx)

    assert result.status == "ok", (result.error_code, result.error, result.step_name)
    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "no_match", (
        "learning_injection must be exactly 'no_match' on the "
        f"hal_memory_block entry path, got "
        f"{result.data.get('learning_injection')!r} (data keys: "
        f"{sorted(result.data.keys())})"
    )


def test_ac6b_learning_injection_present_when_task_description_is_none(tmp_path):
    """AC6b (§1ab): case (d) -- task_description is None (orchestrator never
    asked for a query, and no hal_memory_block was supplied either) --
    result.data['learning_injection'] must carry the exact value 'no_match',
    never absent/None, and never merely truthy.

    RED: today result.data has no 'learning_injection' key on this path.
    """
    scratchpad = tmp_path / "scratch_ac6b_none"
    ctx = _make_ctx(scratchpad, session_id="test-session-ac6b-none")
    log = EventLog(tmp_path / "events_ac6b_none.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p05", phase_05_inject.phase_05_inject_workflow())
    result, _ = eng.execute("p05", ctx)

    assert result.status == "ok", (result.error_code, result.error, result.step_name)
    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "no_match", (
        "learning_injection must be exactly 'no_match' on the "
        f"task_description=None (case d) path, got "
        f"{result.data.get('learning_injection')!r} (data keys: "
        f"{sorted(result.data.keys())})"
    )


# ─── AC7: §1l live side-effect — real engine, real events.jsonl, real db ─────


def test_ac7_real_side_effect_failure_path_emits_callout_failed_and_marks_inject_failed(
    tmp_path, monkeypatch
):
    """AC7 (§1l, live side-effect, failure half). The SAME kind of real
    phase_05_inject run, pointed at a genuinely missing injector
    (HAL_INJECT_LEARNINGS_TS -> a path pre-staged to never exist;
    HAL_BUN_BIN left unset so the real system bun is what actually spawns
    and fails — mirroring the spec's measured production failure mode
    verbatim), must emit 'learning_inject_callout_failed' in the real
    on-disk events.jsonl AND StepResult.data['learning_injection'] ==
    'failed' AND the real hal-memory.md must carry the INJECT_FAILED marker.
    Reads both files back off disk; the unit under test is never mocked.

    Runs unconditionally (no bun skip): even if bun is genuinely absent from
    PATH, subprocess.run raises OSError, which today's production code
    ALREADY routes to the identical 'sentinel' callout-failure branch —
    mirrors test_GH1468's no-skip convention for this exact real-bun +
    missing-ts-path scenario.

    RED: learning_injection and the INJECT_FAILED marker are both absent from
    production today (marker collapses to NO_MATCH; StepResult carries no
    such key at all).
    """
    db_path = _make_fixture_db(tmp_path, [{
        "id": "ac7-fail-learn-1",
        "content": "LESSON: real failure path coverage for injector path",
        "type": "learning",
        "hash": "gh1471ac7failhash01",
    }])

    missing_ts = tmp_path / "definitely_absent_inject_GH1471.ts"
    assert not missing_ts.exists()  # §1i: pre-staged precondition
    monkeypatch.setenv("HAL_INJECT_LEARNINGS_TS", str(missing_ts))
    monkeypatch.delenv("HAL_BUN_BIN", raising=False)  # real system bun (or genuinely absent)

    log_path = tmp_path / "events_ac7_fail.jsonl"
    result, events, scratchpad = _run_workflow(
        tmp_path, db_path, "real failure path coverage", log_path
    )

    failed = [e for e in events if e["event_type"] == "learning_inject_callout_failed"]
    assert failed, (
        f"expected 'learning_inject_callout_failed' in the real events.jsonl "
        f"at {log_path}; got event types: {[e['event_type'] for e in events]}"
    )

    assert isinstance(result.data, dict)
    assert result.data.get("learning_injection") == "failed", (
        f"expected StepResult.data['learning_injection'] == 'failed' on the "
        f"real missing-injector path, got {result.data.get('learning_injection')!r}"
    )

    body = (scratchpad / "injection" / "hal-memory.md").read_text(encoding="utf-8")
    assert "<!-- HAL_MEMORY:INJECT_FAILED -->" in body, (
        f"expected INJECT_FAILED marker in the real hal-memory.md, got body: {body!r}"
    )
    assert "<!-- HAL_MEMORY:NO_MATCH -->" not in body
