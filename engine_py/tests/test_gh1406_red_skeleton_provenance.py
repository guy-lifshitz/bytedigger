"""GH1406 RED — RED-skeleton commit + dirty-tree guard resolved by PROVENANCE.

Frozen spec (r2, post-gate-round-1): 2026-07-29_9CE8E906_gh1406_red_skeleton_provenance_spec.md
Agreement: 9CE8E906 · Issue: #1406 · Method: Option-D (spec → RED → Opus gate → GREEN).

Covers spec §9 (AC1-AC14, S1-S4), §9b (AC15-AC24) and §9c (AC25-AC30).
UUT-A = `lib/red_skeleton.py` · UUT-B = `workflows/phase_5_implement.py`.

r3 contract: `red_commit_sha` is NOT moved. The skeleton HEAD lands in a
separate key `red_skeleton_sha` with exactly ONE reader
(`_detect_green_complete_resume`), so the RED skeleton stays inside the
green-lint / security-lint / typecheck / suppression-scan windows that all
diff from `red_commit_sha` (spec §3.3a). AC26 proves that radius by parsing
the engine source; AC27 proves it by real git diff.

§1q / D1CF5FDF collectability: `lib/red_skeleton.py` does NOT exist today, and
neither `_commit_red_skeleton` nor `_dirty_tree_block_message` exists on
`phase_5_implement`. Every reference to either is DEFERRED into a test body
(via `_red_skeleton()` / `_p5_attr()`), never at module scope, so this file
COLLECTS cleanly and fails at ASSERT time.

§1q / 81F97F3D: no module-level `sys.path` mutation and no `from conftest
import` — `tests/conftest.py` (lines 23-28) is the conftest-import-time
singleton that puts engine_py root / `workflows/` / `lib/` on `sys.path`.

§1l anchoring: every behavioural AC drives the REAL production functions
(`_commit_red_tests`, `_verify_red_fails_mechanically`, `_invoke_red_llm`,
`_commit_red_skeleton`) against a REAL temporary git repository and asserts the
REAL side effect — `git status --porcelain`, `git show --stat HEAD`, the
on-disk snapshot file. Only the LLM subprocess (and, for AC17, the git write
port) is mocked; the unit under test never is.

Reference-from-outside (§11b): the behavioural shield tests stage the pre-RED
snapshot as RAW JSON via stdlib, not via `red_skeleton.persist_pre_red_dirty` —
a shield whose reference comes from inside the artifact it checks goes
vacuously green when that artifact's serializer is wrong. The one value that
CANNOT come from outside is the stamp's per-process nonce
(`red_skeleton.PROCESS_TOKEN`, spec §3.1b): it is an opaque secret of the
running process, so `_stamp_for()` reads it through `build_stamp` when the
module exists and substitutes a placeholder before GREEN, keeping today's red
BEHAVIOURAL rather than a missing-module red. The file's SHAPE and the `paths`
CONTENT stay stdlib-staged, and AC3 pins that shape through a real round-trip.

No AC classifies anything by reading a production file's text or AST — the
method spec §2 rejects outright (#1400: six rounds, ~$57, zero GREEN).

Do NOT implement the contract here — RED-only file (§1s: GREEN must not edit it).
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine.workflows import phase_5_implement as p5
from bytedigger_engine.contracts import StepResult, WorkflowContext

ENGINE_ROOT = Path(__file__).resolve().parents[1]

PRE_RED_DIRTY_RELPATH = "integrity/pre-red-dirty.json"
OLD_REFUSAL_CLAIM = "operator restart likely left GREEN uncommitted"
NEW_REFUSAL_CLAIM = "NOT attributable to this run's RED invocation"

# The RED-authored production stub: `doubled` returns a literal, so the
# behavioural RED below FAILS.
STUB_MOD_BODY = '"""STUB — GREEN implements the real doubling."""\n\n\ndef doubled(n):\n    return None\n'

# The anti-weakening fixture (S4/AC20/AC21): a REAL implementation that makes
# the RED test PASS. Its shape is irrelevant — what is asserted is the OBSERVED
# run outcome, never the file's text.
IMPL_MOD_BODY = (
    "def doubled(n):\n"
    "    total = 0\n"
    "    for _ in range(2):\n"
    "        total += n\n"
    "    return total\n"
)

# A behavioural RED expressed as a `.test.sh` group (runner: `bash <path>`).
# It exercises pkg.mod for real, so it fails against the stub and passes
# against the implementation — the provenance question is decided by git
# history, never by this file's contents.
RED_SH_BODY = (
    "#!/bin/bash\n"
    'cd "$(dirname "$0")/.." || exit 1\n'
    "sys.exit(0 if m.doubled(3) == 6 else 1)' >/dev/null 2>&1; then\n"
    '    echo "PASS: doubled(3) == 6"\n'
    "    exit 0\n"
    "fi\n"
    'echo "FAIL: doubled(3) == 6"\n'
    "exit 1\n"
)


# ═══════════════════════════════════════════════════════════════════════════
# deferred-import helpers (§1q — never at module scope)
# ═══════════════════════════════════════════════════════════════════════════


def _red_skeleton():
    """Bare import of lib/red_skeleton.py — PURE-UNIT TESTS ONLY.

    bd#44 PORT NOTE: under the bytedigger package layout a bare
    `import red_skeleton` is impossible — flat spellings were killed and the
    only import is `from bytedigger_engine.lib import red_skeleton`, which is
    EXACTLY what production uses. So there is one module instance, not two,
    the two-token hazard described below cannot arise here, and AC32 (identity
    pin) degenerates to a tautology in this repo. Kept for parity, but it is
    NOT load-bearing on bytedigger — the hazard it guards is HAL-layout-only.

    Original HAL rationale follows.
    A bare `import red_skeleton` registers `sys.modules["red_skeleton"]`, while
    `phase_5_implement` imports its lib modules as `from bytedigger_engine.lib import X`
    (`:123-126`, `:157`) → `sys.modules["lib.X"]`. Those are TWO executions of
    the module body with two different `PROCESS_TOKEN` values. Any behavioural
    test that stamped a snapshot through this instance would be unsatisfiable
    against an idiomatic GREEN, and would redden with a message about
    `git status --porcelain` that says nothing about imports — an unsatisfiable
    assertion indistinguishable from an honest red, whose cheapest "fix" is to
    make the token a constant (the very weakening AC31 exists to block).

    So: this helper is for AC1-AC8, AC18-unit, AC28 and AC31 only. Every
    behavioural test resolves the module through PRODUCTION
    (`_prod_red_skeleton()`), exactly as the sibling suite does
    (test_dirty_tree_guard.py:37 bare for units, `p5.dirty_tree_guard...` for
    wiring). AC32 pins the identity so drift is named, not confusing.
    """
    try:
        from bytedigger_engine.lib import red_skeleton  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover — the RED state
        pytest.fail(
            f"lib/red_skeleton.py not implemented yet (GH1406 GREEN pending): {e}"
        )
    return red_skeleton


def _prod_red_skeleton():
    """The `red_skeleton` module object PRODUCTION actually uses (§9d MAJOR-2).

    Whatever import form GREEN chooses, `phase_5_implement` must expose the
    module it stamps and reads snapshots with; the behavioural fixtures bind to
    THAT object so the stamps agree by construction.
    """
    mod = getattr(p5, "red_skeleton", None)
    assert mod is not None, (
        "phase_5_implement does not expose `red_skeleton` yet (GH1406 GREEN "
        "pending) — the behavioural fixtures must stamp snapshots with the "
        "SAME module object production reads them with, never a second bare "
        "import with its own PROCESS_TOKEN"
    )
    return mod


def _p5_attr(name: str):
    fn = getattr(p5, name, None)
    assert fn is not None, (
        f"phase_5_implement.{name} does not exist yet (GH1406 GREEN pending); "
        f"spec §1c names it as a chokepoint"
    )
    return fn


def _stamp_for(ctx, cycle: int = 1) -> dict:
    """The stamp a production `build_stamp(ctx, cycle)` would produce (§3.1b).

    Deliberately NOT a guess: the stamp carries `PROCESS_TOKEN`, a per-process
    `uuid4` nonce that by construction has no external reference. Before GREEN
    the module is absent, so a placeholder is returned — that keeps the
    behavioural ACs failing for a BEHAVIOURAL reason instead of an ImportError.
    The snapshot's file SHAPE and its `paths` content are still staged from
    outside the artifact (stdlib json), which is what the shield needs.

    §9d MAJOR-2: the token is taken from the PRODUCTION module instance
    (`p5.red_skeleton`), never from a bare `import red_skeleton` — a second
    execution of the module body carries a different `PROCESS_TOKEN`, and every
    shield would then redden for an import reason wearing a git-status
    message.
    """
    mod = getattr(p5, "red_skeleton", None)
    if mod is None:
        return {"process_token": "gh1406-red-phase-placeholder", "run_id": None,
                "cycle": cycle}
    return mod.build_stamp(ctx, cycle)


def _write_snapshot(scratchpad: Path, paths: list, stamp: dict) -> Path:
    """Stage `integrity/pre-red-dirty.json` with STDLIB ONLY (§11b)."""
    snapshot = Path(scratchpad) / PRE_RED_DIRTY_RELPATH
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps({"paths": list(paths), "stamp": dict(stamp)}), encoding="utf-8"
    )
    return snapshot


_SKELETON_ARG_ORDER = (
    "ctx", "git_cwd", "git_cwd_source", "spec_path", "scratchpad",
    "red_test_paths", "cycle", "red_commit_sha", "pre_red_sha", "prev",
)


def _call_commit_red_skeleton(pool: dict):
    """Call the real `_commit_red_skeleton`, adapting to whatever signature
    GREEN chose. The spec fixes the helper's behaviour and its return value
    (the new HEAD) but NOT its parameter names or order, so a conforming GREEN
    must never fail this AC for a naming reason (gate r2).

    Strategy: bind by NAME for every parameter the pool knows; for a required
    parameter whose name the pool does not know, fall back to the value at the
    same POSITION in `_SKELETON_ARG_ORDER`; and if the resulting call still
    raises TypeError, retry positionally. The function itself is never stubbed
    — this only adapts the call shape.
    """
    fn = _p5_attr("_commit_red_skeleton")
    sig = inspect.signature(fn)
    positional = [pool[k] for k in _SKELETON_ARG_ORDER if k in pool]
    kwargs: dict = {}
    params = [
        (n, p) for n, p in sig.parameters.items()
        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]
    for idx, (pname, param) in enumerate(params):
        if pname in pool:
            kwargs[pname] = pool[pname]
        elif param.default is inspect.Parameter.empty and idx < len(positional):
            kwargs[pname] = positional[idx]
    try:
        return fn(**kwargs)
    except TypeError:
        n_required = sum(
            1 for _n, p in params if p.default is inspect.Parameter.empty
        )
        return fn(*positional[:max(n_required, 1)])


# ═══════════════════════════════════════════════════════════════════════════
# real-git-repo harness (§1l — no mocking of the UUT)
# ═══════════════════════════════════════════════════════════════════════════


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _porcelain(repo: Path) -> str:
    return _git(repo, "status", "--porcelain")


def _head_stat(repo: Path) -> str:
    return _git(repo, "show", "--stat", "--oneline", "HEAD")


def _write(repo: Path, rel: str, body: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _write_spec(tmp_path: Path, name: str, files, *, has_files_section: bool = True) -> str:
    """Frozen-spec fixture using the `## Files` run_allowlist grammar (same
    shape as tests/test_dirty_tree_guard.py::_write_spec_with_files)."""
    if has_files_section:
        lines = ["# gh1406 fixture spec\n", "\n## Files\n"]
        lines += [f"- {f}\n" for f in files]
    else:
        lines = ["# gh1406 fixture spec\n", "\nNo files section at all.\n"]
    p = tmp_path / name
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)


def _record_events(monkeypatch) -> "list[dict]":
    captured: list[dict] = []

    def _recorder(event_type, payload=None, **kw):
        captured.append({"type": str(event_type), "payload": dict(payload or {})})
        return None

    monkeypatch.setattr(p5, "_emit_safe", _recorder)
    return captured


def _events_of(captured, name: str) -> list:
    return [e for e in captured if e["type"] == name]


def _make_ctx(scratchpad: Path, git_cwd) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(git_cwd)},
        question="gh1406", session_id="test-gh1406", persona="hal",
        framework=None, domain=None,
    )


def _broken_llm(**kwargs) -> StepResult:
    """The ONLY mocked seam on the `_invoke_red_llm` path (§1l)."""
    return StepResult(
        status="error", data={"exit_code": 1, "stderr_tail": "boom"},
        duration_ms=0, step_name="invoke_red_llm",
        error="simulated LLM failure", error_code="E_LLM_FAILED",
    )


def _llm_prev(scratchpad: Path, spec_path: str) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "noop-prompt",
            "log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": spec_path,
            "cycle": 1,
            "stable_prefix": "",
        },
        duration_ms=0,
        step_name="build_red_prompt",
    )


def _stage_red_cycle(
    tmp_path: Path,
    name: str,
    *,
    mod_body: str = STUB_MOD_BODY,
    pre_red_dirty: "list | None" = None,
    stamp: "dict | None" = None,
    spec_files=("pkg/mod.py",),
    spec_has_files: bool = True,
    mod_committed: bool = False,
    delete_mod: bool = False,
    cycle: int = 1,
):
    """Pre-stage a complete, deterministic RED cycle (§1i — no racing).

    The base commit tracks `pkg/__init__.py` and `tests/.gitkeep` so that plain
    `git status --porcelain` lists `pkg/mod.py` INDIVIDUALLY instead of
    collapsing a wholly-untracked directory into a single `?? pkg/` line —
    otherwise every per-path assertion below would be vacuous.

    `pre_red_dirty is None` => NO snapshot file at all (the fail-closed leg).
    Otherwise it is staged with stdlib json at the spec'd relpath/shape (§11b).
    """
    repo = Path(os.path.realpath(str(tmp_path / name)))  # §1j
    _init_repo(repo)
    _write(repo, "pkg/__init__.py", "")
    _write(repo, "tests/.gitkeep", "")
    _write(repo, "src/placeholder.py", f"# placeholder {name}\n")
    base_add = ["pkg/__init__.py", "tests/.gitkeep", "src/placeholder.py"]
    if mod_committed:
        _write(repo, "pkg/mod.py", mod_body)
        base_add.append("pkg/mod.py")
    subprocess.run(["git", "add", *base_add], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"init {name}"], cwd=repo, check=True)

    # RED-authored artifacts, left UNCOMMITTED on disk.
    if delete_mod:
        (repo / "pkg" / "mod.py").unlink()
    elif not mod_committed:
        _write(repo, "pkg/mod.py", mod_body)
    red_sh = _write(repo, "tests/red_check.test.sh", RED_SH_BODY)
    red_sh.chmod(0o755)

    scratchpad = Path(os.path.realpath(str(tmp_path / f"{name}-scratch")))
    scratchpad.mkdir(parents=True, exist_ok=True)
    spec_path = _write_spec(
        tmp_path, f"{name}-spec.md", spec_files, has_files_section=spec_has_files
    )
    ctx = _make_ctx(scratchpad, repo)
    if pre_red_dirty is not None:
        _write_snapshot(
            scratchpad, pre_red_dirty,
            stamp if stamp is not None else _stamp_for(ctx, cycle),
        )

    prev = StepResult(
        status="ok",
        data={
            "cycle": cycle,
            "spec_path": spec_path,
            "red_test_paths": None,
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )
    return repo, scratchpad, ctx, prev


def _require_commit_ok(result: StepResult) -> None:
    assert result.status == "ok", (
        f"fixture precondition: _commit_red_tests must succeed; actual "
        f"status={result.status!r} code={result.error_code!r} "
        f"error={getattr(result, 'error', '')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC1-AC8 + AC18(unit) — UUT-A: lib/red_skeleton.py (spec §3.1, §3.1a)
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_snapshot_dirty_paths_preserves_xy_status_on_real_repo(tmp_path):
    """AC1 (r3): on a REAL repo with one modified tracked file and one
    untracked file, `snapshot_dirty_paths` returns
    `([(xy_code, path), ...], None)` — the XY column is PRESERVED VERBATIM
    (§3.1a: the classifier must be able to tell ` D` from `??`), paths sorted,
    no mocks. `addable_paths` over those entries yields both paths.

    The XY codes are asserted as WHOLE two-character codes, not by character
    membership: `ADDABLE_XY` is a whitelist of whole codes in r3 (§3.1a), and
    a per-character predicate is exactly what let `AA` through (AC28).
    """
    m = _red_skeleton()
    repo = Path(os.path.realpath(str(tmp_path / "ac1")))
    _init_repo(repo)
    _write(repo, "a.py", "# v1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    _write(repo, "a.py", "# v2 modified\n")
    _write(repo, "b.py", "# untracked\n")

    entries, err = m.snapshot_dirty_paths(str(repo))

    assert err is None, f"expected err is None on a healthy repo; actual {err!r}"
    assert [p for _xy, p in entries] == ["a.py", "b.py"], (
        f"expected the sorted dirty paths ['a.py', 'b.py']; actual entries="
        f"{entries!r} (porcelain was {_porcelain(repo)!r})"
    )
    by_path = {p: xy for xy, p in entries}
    assert by_path["a.py"] == " M", (
        f"a worktree-modified tracked file must keep the WHOLE porcelain code "
        f"' M'; actual {by_path['a.py']!r}"
    )
    assert by_path["b.py"] == "??", (
        f"an untracked file must keep the WHOLE porcelain code '??'; actual "
        f"{by_path['b.py']!r}"
    )
    assert m.addable_paths(entries) == ["a.py", "b.py"], (
        f"'M' and '??' are both addable; actual {m.addable_paths(entries)!r}"
    )


def test_ac2_snapshot_dirty_paths_fail_open_outside_a_repo(tmp_path):
    """AC2: on a git failure (cwd is not a repo) `snapshot_dirty_paths` returns
    ([], "<non-empty string>") and NEVER raises (spec §3.1 module contract)."""
    m = _red_skeleton()
    not_a_repo = tmp_path / "ac2_not_a_repo"
    not_a_repo.mkdir()

    entries, err = m.snapshot_dirty_paths(str(not_a_repo))  # must not raise

    assert entries == [], f"expected [] entries on a git failure; actual {entries!r}"
    assert isinstance(err, str) and err, (
        f"expected a non-empty error string on a git failure; actual {err!r}"
    )


def test_ac3_persist_read_round_trip_with_stamp(tmp_path):
    """AC3 (r2): `persist_pre_red_dirty(scratchpad, paths, stamp)` ->
    `read_pre_red_dirty(scratchpad, stamp)` round-trips the same list; the
    artifact lands at `integrity/pre-red-dirty.json`; and the ON-DISK SHAPE is
    `{"paths": [...], "stamp": {...}}`.

    This is the single anchor for the raw-JSON staging used by every
    behavioural shield test below: they stage from outside the artifact, and
    THIS test is what keeps that staging tied to the real module's contract
    instead of a guess (§11b).
    """
    m = _red_skeleton()
    scratchpad = tmp_path / "ac3-scratch"
    scratchpad.mkdir()
    payload = ["pkg/mod.py", "src/other.py"]
    stamp = m.build_stamp(_make_ctx(scratchpad, tmp_path), 1)

    ok = m.persist_pre_red_dirty(scratchpad, payload, stamp)
    assert ok is True, f"expected persist_pre_red_dirty -> True; actual {ok!r}"

    artifact = scratchpad / PRE_RED_DIRTY_RELPATH
    assert artifact.is_file(), (
        f"expected the snapshot at {PRE_RED_DIRTY_RELPATH}; actual scratchpad "
        f"contents={[str(p.relative_to(scratchpad)) for p in scratchpad.rglob('*')]!r}"
    )
    assert m.read_pre_red_dirty(scratchpad, stamp) == payload, (
        f"expected the round-tripped list {payload!r}; actual "
        f"{m.read_pre_red_dirty(scratchpad, stamp)!r}"
    )
    on_disk = json.loads(artifact.read_text(encoding="utf-8"))
    assert set(on_disk) == {"paths", "stamp"}, (
        f"expected the on-disk shape {{'paths': [...], 'stamp': {{...}}}} "
        f"(spec §3.1); actual keys={sorted(on_disk)!r}"
    )
    assert on_disk["paths"] == payload, (
        f"expected paths=={payload!r} on disk; actual {on_disk['paths']!r}"
    )
    assert on_disk["stamp"].get("process_token") == m.PROCESS_TOKEN, (
        "the persisted stamp must carry this process's PROCESS_TOKEN (§3.1b); "
        f"actual {on_disk['stamp']!r}"
    )


def test_ac4_read_pre_red_dirty_distinguishes_none_from_empty_list(tmp_path):
    """AC4 (r2): absent file -> None; garbage file -> None; a present snapshot
    holding `{"paths": [], "stamp": <matching>}` -> [] (an EMPTY LIST,
    explicitly NOT None).

    `None` means "snapshot unavailable" and forces the caller fail-closed; `[]`
    means "it was clean before RED" and is a fully valid, permissive value —
    conflating them collapses the entire provenance discriminator.
    """
    m = _red_skeleton()
    stamp = m.build_stamp(_make_ctx(tmp_path, tmp_path), 1)

    absent = tmp_path / "ac4-absent"
    absent.mkdir()
    assert m.read_pre_red_dirty(absent, stamp) is None, (
        f"absent snapshot must read as None; actual "
        f"{m.read_pre_red_dirty(absent, stamp)!r}"
    )

    garbage = tmp_path / "ac4-garbage"
    (garbage / "integrity").mkdir(parents=True)
    (garbage / PRE_RED_DIRTY_RELPATH).write_text("{not json at all", encoding="utf-8")
    assert m.read_pre_red_dirty(garbage, stamp) is None, (
        f"unparseable snapshot must read as None; actual "
        f"{m.read_pre_red_dirty(garbage, stamp)!r}"
    )

    empty = tmp_path / "ac4-empty"
    _write_snapshot(empty, [], stamp)
    got = m.read_pre_red_dirty(empty, stamp)
    assert got is not None, (
        "a PRESENT snapshot that was clean must NOT read as None; actual "
        f"{got!r}"
    )
    assert got == [], f"expected [] for a present-but-clean snapshot; actual {got!r}"


def test_ac5_red_authored_paths_fail_closed_when_snapshot_unavailable():
    """AC5: `red_authored_paths(None, ["p.py"], ["p.py"], [])` -> [] —
    fail-closed. A missing snapshot means RED authorship cannot be
    established, so the RED-authored set is EMPTY and the guard keeps its
    byte-for-byte legacy behaviour (spec §2)."""
    m = _red_skeleton()
    got = m.red_authored_paths(None, ["p.py"], ["p.py"], [])
    assert got == [], (
        f"expected [] (fail-closed) when pre_red_dirty is None; actual {got!r}"
    )


def test_ac6_red_authored_paths_scopes_to_allowlist():
    """AC6: `red_authored_paths([], ["p.py", "x.py"], ["p.py"], [])` ->
    ["p.py"] — a dirty path outside the spec allowlist is never RED-authored."""
    m = _red_skeleton()
    got = m.red_authored_paths([], ["p.py", "x.py"], ["p.py"], [])
    assert got == ["p.py"], (
        f"expected ['p.py'] (x.py is outside the allowlist); actual {got!r}"
    )


def test_ac7_red_authored_paths_subtracts_pre_red_dirty():
    """AC7: `red_authored_paths(["p.py"], ["p.py"], ["p.py"], [])` -> [] —
    the path was ALREADY dirty before invoke_red_llm (operator restart /
    uncommitted GREEN from a previous cycle), so it is not RED-authored. This
    subtraction IS the provenance discriminator (spec §2)."""
    m = _red_skeleton()
    got = m.red_authored_paths(["p.py"], ["p.py"], ["p.py"], [])
    assert got == [], (
        f"expected [] — 'p.py' was dirty BEFORE this RED invocation; actual {got!r}"
    )


def test_ac8_red_authored_paths_excludes_red_tests_and_test_segments():
    """AC8: RED test paths and test-segment paths are subtracted:
    `red_authored_paths([], ["t/tests/x.py", "p.py"], [both], ["t/tests/x.py"])`
    -> ["p.py"]."""
    m = _red_skeleton()
    got = m.red_authored_paths(
        [], ["t/tests/x.py", "p.py"], ["t/tests/x.py", "p.py"], ["t/tests/x.py"]
    )
    assert got == ["p.py"], (
        f"expected ['p.py'] — RED test paths and test segments are excluded; "
        f"actual {got!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# S1-S4 — the shield, driven end-to-end through REAL production functions
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_s1_shield_pass_red_authored_skeleton_is_committed(tmp_path, monkeypatch):
    """S1 (§1l, real production side effect): pre-RED snapshot is PRESENT and
    EMPTY, so the RED-authored `pkg/mod.py` is provably authored by THIS RED
    invocation. The real `_commit_red_tests` must commit it in its own
    skeleton commit — asserted on ACTUAL GIT STATE (`git status --porcelain`
    no longer shows it; `git show --stat HEAD` names it) — emit
    `red_skeleton_committed` with paths == ["pkg/mod.py"], and the subsequent
    real `_verify_red_fails_mechanically` must NOT return E_RED_WORKTREE_DIRTY.

    Also pins the r3 key split (§3.3a): `red_commit_sha` is NOT moved — it
    stays the RED-TESTS commit, so the skeleton remains inside the windows of
    green-lint, security-lint, typecheck and the suppression boundary scan,
    all of which diff from it. The skeleton HEAD lands in the SEPARATE key
    `red_skeleton_sha`. Moving `red_commit_sha` (the r2 attempt) would have
    exempted RED-authored production files from every one of those gates —
    this lot's own defect class, recreated by its own fix.

    Pre-GREEN FAIL: `_commit_red_skeleton` does not exist; `pkg/mod.py` stays
    dirty and the guard kills the run — the exact inverted-failure-direction
    this lot exists to close.
    """
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "s1", pre_red_dirty=[])

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    porcelain = _porcelain(repo)
    assert "pkg/mod.py" not in porcelain, (
        "expected the RED-authored skeleton to be COMMITTED (absent from "
        f"`git status --porcelain`); actual porcelain={porcelain!r}"
    )
    head_stat = _head_stat(repo)
    assert "pkg/mod.py" in head_stat, (
        "expected HEAD to be the skeleton commit naming pkg/mod.py; actual "
        f"`git show --stat HEAD`={head_stat!r}"
    )

    committed = _events_of(captured, "red_skeleton_committed")
    assert len(committed) == 1, (
        f"expected exactly 1 'red_skeleton_committed' event; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert committed[0]["payload"].get("paths") == ["pkg/mod.py"], (
        f"expected paths==['pkg/mod.py']; actual {committed[0]['payload']!r}"
    )

    head_sha = _git(repo, "rev-parse", "HEAD").strip()
    red_tests_sha = _git(repo, "rev-parse", "HEAD~1").strip()
    assert commit_result.data.get("red_skeleton_sha") == head_sha, (
        "§3.3a: the skeleton HEAD must land in the SEPARATE key "
        f"red_skeleton_sha; actual {commit_result.data.get('red_skeleton_sha')!r} "
        f"vs HEAD {head_sha!r}"
    )
    assert commit_result.data.get("red_commit_sha") == red_tests_sha, (
        "§3.3a: red_commit_sha must NOT be moved — it stays the RED-TESTS "
        "commit so the skeleton remains inside the green-lint / security-lint "
        "/ typecheck / suppression-scan windows; actual "
        f"{commit_result.data.get('red_commit_sha')!r} vs RED-tests commit "
        f"{red_tests_sha!r} (HEAD is {head_sha!r})"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code != "E_RED_WORKTREE_DIRTY", (
        "a behaviourally-failing RED whose skeleton was committed must NOT be "
        f"killed by the dirty-tree guard; actual error={verify_result.error!r}"
    )
    assert verify_result.status == "ok", (
        f"expected the RED gate to pass (the .test.sh group fails against the "
        f"stub); actual status={verify_result.status!r} "
        f"code={verify_result.error_code!r} error={getattr(verify_result, 'error', '')!r}"
    )


def test_s2_shield_block_path_dirty_before_red_is_not_committed(tmp_path, monkeypatch):
    """S2 (provenance block): the SAME fixture, except `pkg/mod.py` is present
    in the pre-RED snapshot (a dragged-in GREEN from a previous cycle —
    incident class GH961/GH1039). It must NOT be committed (still shown by
    `git status --porcelain`, not named by `git show --stat HEAD`), no
    `red_skeleton_committed` event may fire, and the guard must return
    E_RED_WORKTREE_DIRTY / recoverable is False with a `red_dirty_tree_blocked`
    event naming the path.

    GREEN-AT-RED REGRESSION GUARD, declared openly (§11b): this test PASSES
    today, because today the guard blocks EVERYTHING. Its whole value is that
    it must STILL pass after GREEN narrows the guard to non-RED-authored
    paths. It does not demonstrate its own force by running; that force is
    demonstrated by the §12 mutation — remove the `pre_red_dirty` subtraction
    from `red_authored_paths` and S2 MUST redden. Without that mutation S2 is
    indistinguishable from a vacuum, which is why it is stated here rather
    than left to inference.
    """
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "s2", pre_red_dirty=["pkg/mod.py"]
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    porcelain = _porcelain(repo)
    assert "pkg/mod.py" in porcelain, (
        "a path dirty BEFORE invoke_red_llm must NOT be committed by "
        f"commit_red_skeleton; actual porcelain={porcelain!r}"
    )
    head_stat = _head_stat(repo)
    assert "pkg/mod.py" not in head_stat, (
        f"HEAD must not be a skeleton commit for a non-RED-authored path; "
        f"actual `git show --stat HEAD`={head_stat!r}"
    )
    assert _events_of(captured, "red_skeleton_committed") == [], (
        f"no 'red_skeleton_committed' event may fire; actual events="
        f"{[e['type'] for e in captured]!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"expected E_RED_WORKTREE_DIRTY; actual {verify_result.error_code!r} "
        f"(status={verify_result.status!r})"
    )
    assert verify_result.recoverable is False, (
        f"expected recoverable is False; actual {verify_result.recoverable!r}"
    )
    blocked = _events_of(captured, "red_dirty_tree_blocked")
    assert blocked and "pkg/mod.py" in (blocked[-1]["payload"].get("paths") or []), (
        f"expected a red_dirty_tree_blocked event naming pkg/mod.py; actual "
        f"{blocked!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_s3_shield_block_fail_closed_when_snapshot_missing(tmp_path, monkeypatch):
    """S3 (fail-closed): no snapshot file exists at all. Nothing may be
    committed and the guard must return E_RED_WORKTREE_DIRTY — byte-for-byte
    legacy behaviour. §12 mutation target: dropping the `None` fail-closed
    branch must redden exactly this test.

    Pre-GREEN FAIL: `_commit_red_skeleton` is absent, so the presence
    assertion below fails first, at assert time.
    """
    captured = _record_events(monkeypatch)
    _p5_attr("_commit_red_skeleton")
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "s3", pre_red_dirty=None)
    assert not (scratchpad / PRE_RED_DIRTY_RELPATH).exists(), (
        "fixture precondition: the pre-RED snapshot file must be absent"
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)
    assert "pkg/mod.py" in _porcelain(repo), (
        "with no snapshot the RED-authored set is EMPTY (fail-closed) — "
        f"nothing may be committed; actual porcelain={_porcelain(repo)!r}"
    )
    assert _events_of(captured, "red_skeleton_committed") == [], (
        f"no 'red_skeleton_committed' event may fire when the snapshot is "
        f"unavailable; actual events={[e['type'] for e in captured]!r}"
    )
    skipped = _events_of(captured, "red_skeleton_commit_skipped")
    assert skipped and skipped[-1]["payload"].get("reason") == "snapshot_unavailable", (
        f"expected red_skeleton_commit_skipped reason=='snapshot_unavailable'; "
        f"actual {[e['payload'] for e in skipped]!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"fail-closed must reproduce legacy behaviour; actual "
        f"{verify_result.error_code!r} (status={verify_result.status!r})"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_s4_shield_block_behavioural_anti_weakening(tmp_path, monkeypatch):
    """S4 (anti-weakening), at DEFAULT gate settings: the RED-authored
    `pkg/mod.py` carries a REAL implementation, so the RED tests PASS.
    Provenance is correct, so the skeleton IS committed and the dirty-tree
    guard is silent — but the run must still be stopped, by
    `E_RED_NOT_FAILING`.

    The assertion is on the OBSERVED OUTCOME OF THE TEST RUN, never on the
    text or AST of pkg/mod.py: classification by reading the source is the
    method spec §2 rejects outright.

    r2: the earlier revision pinned `HAL_GREEN_COMPLETE_RESUME_GATE=0`, which
    asserted a property FALSE in the shipped configuration (gate MAJOR-1). No
    env pin here — the §3.3 boundary move (`red_commit_sha` onto the skeleton
    commit) is what makes `E_RED_NOT_FAILING` genuinely reachable at defaults,
    and §10 requires the branch order in `_decide_red_verdict` to be TRAVERSED,
    not assumed.

    Pre-GREEN FAIL: `_commit_red_skeleton` does not exist, so `pkg/mod.py`
    stays dirty and the run dies on E_RED_WORKTREE_DIRTY, not on the
    behavioural verdict.
    """
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "s4", mod_body=IMPL_MOD_BODY, pre_red_dirty=[]
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)
    assert "pkg/mod.py" not in _porcelain(repo), (
        "fixture precondition: provenance is correct, so the file must be "
        f"committed; actual porcelain={_porcelain(repo)!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)

    assert verify_result.error_code != "E_RED_WORKTREE_DIRTY", (
        "the dirty-tree guard must be silent here — the shield that stops "
        f"this run is the behavioural one; actual error={verify_result.error!r}"
    )
    assert verify_result.error_code == "E_RED_NOT_FAILING", (
        "a 'skeleton' that is actually an implementation makes the RED pass; "
        "the run must still be stopped by the OBSERVED run outcome; actual "
        f"{verify_result.error_code!r} (status={verify_result.status!r}, "
        f"error={getattr(verify_result, 'error', '')!r}, "
        f"events={[e['type'] for e in captured]!r})"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC9-AC14 — kill-switches, refusal text, snapshot liveness, registrations
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac9_kill_switch_red_skeleton_commit_zero_restores_legacy(tmp_path, monkeypatch):
    """AC9 (strengthened in r2): `HAL_RED_SKELETON_COMMIT=0` + the S1 scenario
    -> `pkg/mod.py` is NOT committed, `red_skeleton_commit_skipped` fires with
    `payload["reason"] == "gate_off"` (the EVENT TYPE alone cannot tell gate-off
    apart from allowlist-unavailable), and the guard kills the run."""
    monkeypatch.setenv("HAL_RED_SKELETON_COMMIT", "0")
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "ac9", pre_red_dirty=[])

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)
    assert "pkg/mod.py" in _porcelain(repo), (
        "with the gate OFF nothing may be committed; actual porcelain="
        f"{_porcelain(repo)!r}"
    )
    skipped = _events_of(captured, "red_skeleton_commit_skipped")
    assert skipped, (
        f"expected a 'red_skeleton_commit_skipped' event; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert skipped[-1]["payload"].get("reason") == "gate_off", (
        f"expected reason=='gate_off'; actual {skipped[-1]['payload']!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"gate OFF must reproduce legacy behaviour; actual "
        f"{verify_result.error_code!r} (status={verify_result.status!r})"
    )


def test_ac10_dirty_tree_guard_kill_switch_still_works(tmp_path, monkeypatch):
    """AC10: `HAL_DIRTY_TREE_GUARD=0` + the S2 scenario -> no
    E_RED_WORKTREE_DIRTY. The existing escape hatch must survive this lot.

    Green-at-RED regression guard, same class as S2 (§11b): its force is
    demonstrated by the §12 mutation (disable the kill-switch branch and this
    test must redden), not by today's run.
    """
    monkeypatch.setenv("HAL_DIRTY_TREE_GUARD", "0")
    _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac10", pre_red_dirty=["pkg/mod.py"]
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code != "E_RED_WORKTREE_DIRTY", (
        "HAL_DIRTY_TREE_GUARD=0 must bypass the guard entirely; actual "
        f"{verify_result.error_code!r} error={getattr(verify_result, 'error', '')!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac11_refusal_text_attributes_what_was_actually_measured(tmp_path, monkeypatch):
    """AC11 (second defect of the issue): the S2 refusal message must NOT
    claim the single unmeasured cause "operator restart likely left GREEN
    uncommitted", and MUST state what the guard actually established — that
    the paths are NOT attributable to this run's RED invocation — while
    keeping the path itself and the HAL_DIRTY_TREE_GUARD escape token that
    sibling tests (test_dirty_tree_guard.py:247,248,277) assert on."""
    _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac11", pre_red_dirty=["pkg/mod.py"]
    )
    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"fixture precondition: the guard must fire; actual "
        f"{verify_result.error_code!r}"
    )
    msg = verify_result.error or ""

    assert OLD_REFUSAL_CLAIM not in msg, (
        "the refusal must stop asserting an unmeasured single cause; actual "
        f"message={msg!r}"
    )
    assert NEW_REFUSAL_CLAIM in msg, (
        f"the refusal must state what was actually measured; actual message={msg!r}"
    )
    assert "pkg/mod.py" in msg, f"the refusal must name the path; actual {msg!r}"
    assert "HAL_DIRTY_TREE_GUARD" in msg, (
        f"the refusal must keep the escape token; actual {msg!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac12_both_guard_call_sites_share_one_message_source(tmp_path, monkeypatch):
    """AC12 (§1g): `_verify_red_dirty_tree_guard` and `_build_validation_prompt`
    must produce LETTER-FOR-LETTER identical refusal text for identical
    violations, differing only in the step name — one canonical source
    (`_dirty_tree_block_message`), not two hand-copied literals that have
    already drifted once.

    §12 mutation for this pair: make `_dirty_tree_block_message` return a
    sentinel string; AC11 AND AC12 must both redden. Two hand-copied identical
    literals plus an unused helper would otherwise satisfy both.
    """
    _p5_attr("_dirty_tree_block_message")
    _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac12", pre_red_dirty=["pkg/mod.py"]
    )
    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    red_gate = p5._verify_red_fails_mechanically(ctx, commit_result)
    validation = p5._build_validation_prompt(ctx, commit_result)

    assert red_gate.error_code == "E_RED_WORKTREE_DIRTY", (
        f"fixture precondition (RED gate); actual {red_gate.error_code!r}"
    )
    assert validation.error_code == "E_RED_WORKTREE_DIRTY", (
        f"fixture precondition (validation entry); actual {validation.error_code!r}"
    )

    a = (red_gate.error or "").replace("verify_red_fails_mechanically", "<STEP>")
    b = (validation.error or "").replace("build_validation_prompt", "<STEP>")
    assert a == b, (
        "the two guard call-sites must render from ONE source (§1g); actual\n"
        f"  red_gate  : {a!r}\n"
        f"  validation: {b!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac13_pre_red_snapshot_taken_even_when_the_red_llm_fails(tmp_path, monkeypatch):
    """AC13: the snapshot is taken on EVERY `invoke_red_llm`, including one
    whose LLM call fails — the snapshot precedes the LLM and must never be
    conditional on its success. Asserted on the REAL FILE ON DISK.

    Only the LLM subprocess is mocked (§1l); `_invoke_red_llm` itself is the
    real production function.
    """
    _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "ac13", pre_red_dirty=None)
    snapshot = scratchpad / PRE_RED_DIRTY_RELPATH
    assert not snapshot.exists(), "fixture precondition: no snapshot yet"

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _broken_llm)
    result = p5._invoke_red_llm(ctx, _llm_prev(scratchpad, prev.data["spec_path"]))

    assert result.status == "error", (
        f"fixture precondition: the mocked LLM must fail; actual {result.status!r}"
    )
    assert snapshot.exists(), (
        f"the pre-RED dirty snapshot must be persisted even on a FAILING RED "
        f"invocation; actual scratchpad contents="
        f"{[str(p.relative_to(scratchpad)) for p in scratchpad.rglob('*')]!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac14_registrations_flag_lint_and_docs_have_no_drift():
    """AC14: `flags_catalog.FLAGS["HAL_RED_SKELETON_COMMIT"]["default"] == "1"`;
    `mutating_git_lint` classifies `_commit_red_skeleton` as a guarded write
    site (it calls `git add`/`git commit`, so the lint fails the build
    otherwise); `docs/FLAGS.md` is regenerated (`gen_flag_catalog.py --check`
    exits 0)."""
    from bytedigger_engine import flags_catalog  # noqa: PLC0415
    from bytedigger_engine.lib import mutating_git_lint  # noqa: PLC0415

    assert "HAL_RED_SKELETON_COMMIT" in flags_catalog.FLAGS, (
        "HAL_RED_SKELETON_COMMIT missing from flags_catalog.FLAGS"
    )
    entry = flags_catalog.FLAGS["HAL_RED_SKELETON_COMMIT"]
    assert str(entry.get("default")) == "1", (
        f"HAL_RED_SKELETON_COMMIT must default ON ('1'); actual {entry.get('default')!r}"
    )
    assert entry.get("kind") == "gate", (
        f"HAL_RED_SKELETON_COMMIT must be kind 'gate'; actual {entry.get('kind')!r}"
    )

    assert "_commit_red_skeleton" in mutating_git_lint.GUARDED_WRITE_SITES, (
        "_commit_red_skeleton runs `git add`/`git commit` and must be declared "
        "in mutating_git_lint.GUARDED_WRITE_SITES; actual keys="
        f"{sorted(mutating_git_lint.GUARDED_WRITE_SITES)!r}"
    )

    proc = subprocess.run(
        [sys.executable, "scripts/gen_flag_catalog.py", "--check", "docs/FLAGS.md"],
        cwd=str(ENGINE_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "docs/FLAGS.md has drifted from flags_catalog.py — regenerate it; "
        f"rc={proc.returncode} stdout={proc.stdout[-800:]!r} stderr={proc.stderr[-800:]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# §9b — AC15-AC24, added in r2 by the gate verdict
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac15_snapshot_records_what_was_actually_dirty_before_red(tmp_path, monkeypatch):
    """AC15 (gate MAJOR-2) — THE snapshot-content assertion, two-sided.

    Nothing else in this file asserts the snapshot's CONTENT, only that the
    file exists. A GREEN that persists a constant `[]` — e.g. by resolving
    git_cwd differently from `_commit_red_tests`, so the snapshot looks at
    another tree — would satisfy every other AC while making the RED-authored
    set maximally permissive, re-opening the exact GH961/GH1039 incident the
    guard exists for.

    Real repo: `pre.py` is committed and then MODIFIED (dirty before RED);
    `clean.py` is committed and left untouched. After the real
    `_invoke_red_llm`, the persisted `paths` must CONTAIN `pre.py` and must
    NOT contain `clean.py`. Only the LLM subprocess is mocked.
    """
    _record_events(monkeypatch)
    repo = Path(os.path.realpath(str(tmp_path / "ac15")))
    _init_repo(repo)
    _write(repo, "pre.py", "# v1\n")
    _write(repo, "clean.py", "# untouched\n")
    subprocess.run(["git", "add", "pre.py", "clean.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init ac15"], cwd=repo, check=True)
    _write(repo, "pre.py", "# v2 — dirty BEFORE invoke_red_llm\n")

    scratchpad = Path(os.path.realpath(str(tmp_path / "ac15-scratch")))
    scratchpad.mkdir()
    spec_path = _write_spec(tmp_path, "ac15-spec.md", ["pre.py", "clean.py"])
    ctx = _make_ctx(scratchpad, repo)

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _broken_llm)
    p5._invoke_red_llm(ctx, _llm_prev(scratchpad, spec_path))

    snapshot = scratchpad / PRE_RED_DIRTY_RELPATH
    assert snapshot.exists(), (
        "fixture precondition: the snapshot must have been persisted; actual "
        f"scratchpad={[str(p.relative_to(scratchpad)) for p in scratchpad.rglob('*')]!r}"
    )
    paths = json.loads(snapshot.read_text(encoding="utf-8")).get("paths")
    assert isinstance(paths, list), f"expected a 'paths' list; actual {paths!r}"
    assert "pre.py" in paths, (
        "the snapshot must record the path that was ACTUALLY dirty before RED "
        f"— an empty/constant snapshot is the permissive defect; actual "
        f"paths={paths!r} (porcelain was {_porcelain(repo)!r})"
    )
    assert "clean.py" not in paths, (
        "the snapshot must NOT record a clean path — otherwise every RED "
        f"authorship claim is suppressed; actual paths={paths!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac16_spec_allowlist_unavailable_skips_the_skeleton_commit(tmp_path, monkeypatch):
    """AC16 (gate MAJOR-3): a spec with NO `## Files` section + a dirty prod
    file -> the skeleton is NOT committed and `red_skeleton_commit_skipped`
    carries `reason == "spec_allowlist_unavailable"`.

    The guard leg asserts the SHIPPED behaviour: when the allowlist is
    unavailable, `_verify_red_dirty_tree_guard` (phase_5_implement.py:2594-2599)
    emits `red_dirty_tree_guard_error{spec_allowlist_unavailable}` and fails
    OPEN — it does not block. That branch is pinned by a sibling
    (test_dirty_tree_guard.py, the no-`## Files` case), so requiring a block
    here would force GREEN to break the sibling. The spec's r3 amendment
    (§9c note 1) corrected the AC to this, precisely because an unsatisfiable
    assertion is indistinguishable by exit code from an honest red.
    """
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac16", pre_red_dirty=[], spec_has_files=False
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    assert "pkg/mod.py" in _porcelain(repo), (
        "with no allowlist the RED-authored set is empty — nothing may be "
        f"committed; actual porcelain={_porcelain(repo)!r}"
    )
    assert _events_of(captured, "red_skeleton_committed") == [], (
        f"no 'red_skeleton_committed' may fire; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    skipped = _events_of(captured, "red_skeleton_commit_skipped")
    assert skipped, (
        f"expected a 'red_skeleton_commit_skipped' event; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert skipped[-1]["payload"].get("reason") == "spec_allowlist_unavailable", (
        f"expected reason=='spec_allowlist_unavailable'; actual "
        f"{skipped[-1]['payload']!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    guard_errors = _events_of(captured, "red_dirty_tree_guard_error")
    assert any(
        "spec_allowlist_unavailable" in str(e["payload"].get("error", ""))
        for e in guard_errors
    ), (
        "the guard's shipped allowlist-unavailable branch must still fire "
        f"fail-open; actual guard events={guard_errors!r}"
    )
    assert verify_result.error_code != "E_RED_WORKTREE_DIRTY", (
        "the shipped guard is fail-OPEN when the allowlist is unavailable "
        f"(sibling-pinned); actual {verify_result.error_code!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac17_git_failure_leaves_step_ok_and_tree_dirty(tmp_path, monkeypatch):
    """AC17 (gate MAJOR-3): when the skeleton's git op fails,
    `red_skeleton_commit_failed` is emitted, `_commit_red_tests` still returns
    `status == "ok"` (no new terminal code), the path is STILL in
    `git status --porcelain`, and the next step blocks with
    E_RED_WORKTREE_DIRTY. Four independent assertions — fail-closed.

    The git write port is mocked ONLY for argv naming pkg/mod.py; the RED-test
    commit runs for real through the same helper, so the fixture cannot pass
    by disabling git wholesale.
    """
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "ac17", pre_red_dirty=[])

    real_op = p5._git_op_with_lock_retry

    def _failing_for_skeleton(cmd, *, cwd, timeout=30):
        if any("pkg/mod.py" in str(part) for part in cmd):
            return SimpleNamespace(
                returncode=1, stdout="", stderr="simulated git failure (GH1406 AC17)",
                timed_out=False,
            ), "non_lock_error"
        return real_op(cmd, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(p5, "_git_op_with_lock_retry", _failing_for_skeleton)

    commit_result = p5._commit_red_tests(ctx, prev)

    assert commit_result.status == "ok", (
        "a skeleton git failure must NOT introduce a terminal code — the step "
        f"stays ok; actual status={commit_result.status!r} "
        f"code={commit_result.error_code!r} error={getattr(commit_result, 'error', '')!r}"
    )
    failed = _events_of(captured, "red_skeleton_commit_failed")
    assert failed, (
        f"expected a 'red_skeleton_commit_failed' event; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert "pkg/mod.py" in _porcelain(repo), (
        "the failed skeleton commit must leave the path dirty; actual "
        f"porcelain={_porcelain(repo)!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        "a dirty tree after a failed skeleton commit must be blocked by the "
        f"guard (fail-closed); actual {verify_result.error_code!r}"
    )


def test_ac18_deletion_is_never_a_skeleton(tmp_path, monkeypatch):
    """AC18 (gate MAJOR-4): a DELETED in-allowlist production file has perfect
    RED provenance yet must never be committed as a "skeleton" — otherwise the
    engine would auto-record the removal of production code, after which the
    tree is clean and the guard is silent. `addable_paths` admits only the
    WHOLE codes in `ADDABLE_XY` (r3 whitelist).

    Unit leg + end-to-end leg. §12 mutation: accept any XY and this test must
    redden.
    """
    m = _red_skeleton()
    assert m.addable_paths([(" D", "pkg/mod.py"), ("??", "new.py")]) == ["new.py"], (
        "a deletion is not addable; actual "
        f"{m.addable_paths([(' D', 'pkg/mod.py'), ('??', 'new.py')])!r}"
    )
    assert m.addable_paths(
        [("R ", "renamed.py"), ("C ", "copied.py"), ("UU", "conflict.py"),
         ("DD", "both_deleted.py"), ("T ", "typechanged.py")]
    ) == [], (
        "renames, copies, unmerged, both-deleted and typechange entries are "
        "all rejected; actual "
        f"{m.addable_paths([('R ', 'renamed.py'), ('C ', 'copied.py'), ('UU', 'conflict.py'), ('DD', 'both_deleted.py'), ('T ', 'typechanged.py')])!r}"
    )

    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac18", pre_red_dirty=[], mod_committed=True, delete_mod=True
    )
    assert "pkg/mod.py" in _porcelain(repo), (
        f"fixture precondition: the deletion must be visible; actual "
        f"porcelain={_porcelain(repo)!r}"
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    assert "pkg/mod.py" in _porcelain(repo), (
        "the deletion must remain UNCOMMITTED; actual porcelain="
        f"{_porcelain(repo)!r}"
    )
    assert "pkg/mod.py" not in _head_stat(repo), (
        f"HEAD must not record the deletion; actual {_head_stat(repo)!r}"
    )
    assert _events_of(captured, "red_skeleton_committed") == [], (
        f"no 'red_skeleton_committed' may fire for a deletion; actual events="
        f"{[e['type'] for e in captured]!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"the still-dirty deletion must be blocked; actual "
        f"{verify_result.error_code!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac19_stale_stamp_snapshot_is_treated_as_unavailable(tmp_path, monkeypatch):
    """AC19 (gate MAJOR-6): a snapshot whose `stamp.process_token` belongs to a
    DIFFERENT process (the durable-resume scenario of §3.1b, where
    `invoke_red_llm` is served from the sentinel cache and the snapshot is
    never refreshed) must be treated as `None` — the skeleton is not committed
    and the guard blocks.

    Two-sided by construction: the SAME fixture with THIS process's own stamp
    must commit. Without the second leg the test would be green for any
    reason at all. §12 mutation: drop the stamp comparison and this reddens.
    """
    # (a) foreign token -> snapshot rejected -> fail-closed.
    captured_a = _record_events(monkeypatch)
    repo_a, scratch_a, ctx_a, prev_a = _stage_red_cycle(
        tmp_path, "ac19a", pre_red_dirty=[],
        stamp={"process_token": "some-other-process", "run_id": None, "cycle": 1},
    )
    result_a = p5._commit_red_tests(ctx_a, prev_a)
    _require_commit_ok(result_a)
    assert "pkg/mod.py" in _porcelain(repo_a), (
        "a snapshot stamped by another process must be rejected (fail-closed) "
        f"— nothing may be committed; actual porcelain={_porcelain(repo_a)!r}"
    )
    assert _events_of(captured_a, "red_skeleton_committed") == [], (
        f"no commit event may fire on a stale stamp; actual events="
        f"{[e['type'] for e in captured_a]!r}"
    )
    verify_a = p5._verify_red_fails_mechanically(ctx_a, result_a)
    assert verify_a.error_code == "E_RED_WORKTREE_DIRTY", (
        f"a rejected snapshot must degrade to legacy blocking; actual "
        f"{verify_a.error_code!r}"
    )

    # (b) own token, same fixture -> the skeleton IS committed.
    captured_b = _record_events(monkeypatch)
    repo_b, scratch_b, ctx_b, prev_b = _stage_red_cycle(
        tmp_path, "ac19b", pre_red_dirty=[]
    )
    result_b = p5._commit_red_tests(ctx_b, prev_b)
    _require_commit_ok(result_b)
    assert "pkg/mod.py" not in _porcelain(repo_b), (
        "CONTROL LEG: with this process's own stamp the skeleton must commit "
        f"— otherwise leg (a) proves nothing; actual porcelain={_porcelain(repo_b)!r}"
    )
    assert _events_of(captured_b, "red_skeleton_committed"), (
        f"CONTROL LEG: expected 'red_skeleton_committed'; actual events="
        f"{[e['type'] for e in captured_b]!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac20_red_not_failing_reached_at_default_resume_gate(tmp_path, monkeypatch):
    """AC20 (gate MAJOR-1): the S4 scenario with NO env pins at all. The
    skeleton is committed, the RED passes, and the run must stop with
    `E_RED_NOT_FAILING` — `green_complete_resume_detected` must NOT be emitted
    and `data["green_complete_resume"]` must be absent.

    This is the AC that forces the §3.3 boundary move. Without moving
    `red_commit_sha` onto the skeleton commit, `_detect_green_complete_resume`
    (which diffs FROM `red_commit_sha`) sees the skeleton on every cycle,
    returns `status == "ok"` with `green_complete_resume`, GREEN's LLM step is
    skipped entirely and the RED agent's smuggled implementation ships as GREEN
    with no RED certification — the inverted gradient moved one step down and
    made cheaper. `HAL_GREEN_COMPLETE_RESUME_GATE` is ON by default, so this is
    the DEFAULT configuration, not an exotic one.

    §12 mutation: stop moving `red_commit_sha` and this must redden.
    """
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac20", mod_body=IMPL_MOD_BODY, pre_red_dirty=[]
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)

    assert verify_result.error_code == "E_RED_NOT_FAILING", (
        "at the DEFAULT HAL_GREEN_COMPLETE_RESUME_GATE the run must stop with "
        f"E_RED_NOT_FAILING; actual {verify_result.error_code!r} "
        f"(status={verify_result.status!r}, events={[e['type'] for e in captured]!r})"
    )
    assert _events_of(captured, "green_complete_resume_detected") == [], (
        "the committed skeleton must NOT be mistaken for a resumed GREEN; "
        f"actual events={[e['type'] for e in captured]!r}"
    )
    assert not (verify_result.data or {}).get("green_complete_resume"), (
        f"green_complete_resume must be absent; actual data={verify_result.data!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac21_legitimate_green_complete_resume_still_fires(tmp_path, monkeypatch):
    """AC21 — the regression guard for AC20's fix. A GENUINELY uncommitted
    GREEN (the prod file is dirty BEFORE invoke_red_llm, i.e. present in the
    pre-RED snapshot) is NOT RED-authored, so the skeleton is not committed,
    the file stays in the diff from `red_commit_sha`, and GH483's
    `green_complete_resume_detected` must still fire with `status == "ok"`.
    Without this test, AC20's boundary move could silently kill GH483.

    `HAL_DIRTY_TREE_GUARD=0` is set deliberately: with the guard armed this
    scenario is stopped one shield earlier (that is S2's job), so the GH483
    branch would be unreachable and the AC vacuous. Isolating the branch is
    the point — the resume gate itself stays at its DEFAULT, unlike r1's S4
    which switched off the very gate whose behaviour it asserted.

    FORCE (§12, corrected in r3): this test is green today, so its strength
    comes from a mutation — but NOT from one it survives. The mutation that
    reddens it is "`_detect_green_complete_resume` always returns `[]`", i.e.
    the GH483 branch killed outright. Under that mutation AC21 MUST redden
    while AC20 stays green; under the opposite mutation (the resume detector
    ignores `red_skeleton_sha`) AC20 reddens while AC21 stays green. The pair
    reddens from OPPOSITE mutations — that, and only that, makes it two-sided.
    """
    monkeypatch.setenv("HAL_DIRTY_TREE_GUARD", "0")
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac21", mod_body=IMPL_MOD_BODY, pre_red_dirty=["pkg/mod.py"]
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)
    assert "pkg/mod.py" in _porcelain(repo), (
        "fixture precondition: a pre-existing dirty GREEN must NOT be "
        f"committed as a skeleton; actual porcelain={_porcelain(repo)!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)

    assert _events_of(captured, "green_complete_resume_detected"), (
        "GH483 must still detect a genuinely uncommitted GREEN; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert verify_result.status == "ok", (
        f"expected status=='ok' on the GH483 resume branch; actual "
        f"{verify_result.status!r} code={verify_result.error_code!r}"
    )
    assert (verify_result.data or {}).get("green_complete_resume") is True, (
        f"expected green_complete_resume=True in the result data; actual "
        f"{verify_result.data!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac22_commit_red_skeleton_refuses_ambient_git_cwd(tmp_path, monkeypatch):
    """AC22 (gate MAJOR-3): `_commit_red_skeleton` must be safe when called
    DIRECTLY with an ambient-resolved git_cwd — it commits nothing and emits
    `red_skeleton_commit_skipped` with `reason == "ambient_git_cwd"`.
    `mutating_git_lint` requires the registry entry (AC14); this pins the
    behaviour behind it.

    Return contract (r4): `_commit_red_skeleton` returns `str | None` — a
    40-hex sha on a successful skeleton commit, `None` otherwise. The refusal
    must therefore return exactly `None`, not an empty container."""
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "ac22", pre_red_dirty=[])
    head_before = _git(repo, "rev-parse", "HEAD").strip()
    porcelain_before = _porcelain(repo)

    out = _call_commit_red_skeleton({
        "ctx": ctx,
        "prev": prev,
        "git_cwd": str(repo),
        "git_cwd_source": "cwd",          # the canonical ambient label
        "spec_path": prev.data["spec_path"],
        "scratchpad": scratchpad,
        "red_test_paths": ["tests/red_check.test.sh"],
        "cycle": 1,
        "red_commit_sha": head_before,
        "pre_red_sha": head_before,
    })

    skipped = _events_of(captured, "red_skeleton_commit_skipped")
    assert skipped, (
        f"expected a 'red_skeleton_commit_skipped' event; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert skipped[-1]["payload"].get("reason") == "ambient_git_cwd", (
        f"expected reason=='ambient_git_cwd'; actual {skipped[-1]['payload']!r}"
    )
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before, (
        "an ambient git_cwd must produce NO commit; HEAD moved"
    )
    assert _porcelain(repo) == porcelain_before, (
        f"the working tree must be untouched; actual {_porcelain(repo)!r} vs "
        f"before {porcelain_before!r}"
    )
    assert out is None, (
        "the contract is `str | None` (a 40-hex sha on success, None "
        f"otherwise), so the ambient refusal must return None; actual {out!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac23_snapshot_events_are_emitted_on_both_outcomes(tmp_path, monkeypatch):
    """AC23 (§3.2 telemetry, r1 gap): a successful snapshot emits
    `red_pre_dirty_snapshot`; a snapshot whose git call fails emits
    `red_pre_dirty_snapshot_failed` and NEVER blocks the RED invocation."""
    # (a) healthy repo -> success event.
    captured_ok = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "ac23", pre_red_dirty=None)
    monkeypatch.setattr(p5, "invoke_llm_subprocess", _broken_llm)
    p5._invoke_red_llm(ctx, _llm_prev(scratchpad, prev.data["spec_path"]))
    assert _events_of(captured_ok, "red_pre_dirty_snapshot"), (
        f"expected a 'red_pre_dirty_snapshot' event; actual events="
        f"{[e['type'] for e in captured_ok]!r}"
    )

    # (b) git_cwd is not a repo -> failure event, RED still proceeds.
    captured_fail = _record_events(monkeypatch)
    not_a_repo = Path(os.path.realpath(str(tmp_path / "ac23-not-a-repo")))
    not_a_repo.mkdir()
    scratch_fail = Path(os.path.realpath(str(tmp_path / "ac23-scratch-fail")))
    scratch_fail.mkdir()
    ctx_fail = _make_ctx(scratch_fail, not_a_repo)
    result = p5._invoke_red_llm(
        ctx_fail, _llm_prev(scratch_fail, prev.data["spec_path"])
    )
    assert _events_of(captured_fail, "red_pre_dirty_snapshot_failed"), (
        f"expected a 'red_pre_dirty_snapshot_failed' event; actual events="
        f"{[e['type'] for e in captured_fail]!r}"
    )
    assert result.error_code != "E_RED_WORKTREE_DIRTY", (
        "a snapshot failure must NEVER block the RED invocation; actual "
        f"{result.error_code!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac24_degraded_route_with_uncommitted_red_tests_skips_skeleton(tmp_path, monkeypatch):
    """AC24 (gate MINOR-6): on the degraded route — every `red_test_paths`
    entry is gitignored, so `red_commit_sha == pre_red_sha` and NO RED test
    reached git — the skeleton must NOT be committed
    (`reason == "red_tests_uncommitted"`). Otherwise a production file would
    land in history with no committed test behind it, and GREEN's boundary
    would balloon out to `pre_red_sha`. Safe failure direction: the guard blocks.
    """
    captured = _record_events(monkeypatch)
    repo = Path(os.path.realpath(str(tmp_path / "ac24")))
    _init_repo(repo)
    _write(repo, ".gitignore", "tests/\n")
    _write(repo, "pkg/__init__.py", "")
    _write(repo, "src/placeholder.py", "# placeholder ac24\n")
    subprocess.run(
        ["git", "add", ".gitignore", "pkg/__init__.py", "src/placeholder.py"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "init ac24"], cwd=repo, check=True)
    pre_red_sha = _git(repo, "rev-parse", "HEAD").strip()

    _write(repo, "pkg/mod.py", STUB_MOD_BODY)
    red_sh = _write(repo, "tests/red_check.test.sh", RED_SH_BODY)
    red_sh.chmod(0o755)

    scratchpad = Path(os.path.realpath(str(tmp_path / "ac24-scratch")))
    scratchpad.mkdir()
    # The gitignored RED test is invisible to `git diff`, so it reaches
    # `_commit_red_tests` through the persisted-paths fallback — exactly the
    # production shape of the degraded route.
    p5._persist_red_test_paths(scratchpad, ["tests/red_check.test.sh"])
    spec_path = _write_spec(tmp_path, "ac24-spec.md", ["pkg/mod.py"])
    ctx = _make_ctx(scratchpad, repo)
    _write_snapshot(scratchpad, [], _stamp_for(ctx, 1))
    prev = StepResult(
        status="ok",
        data={"cycle": 1, "spec_path": spec_path, "red_test_paths": None,
              "red_log_path": str(scratchpad / "tests/build-red-output.log")},
        duration_ms=0, step_name="write_red_artifact",
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)
    assert commit_result.data.get("red_commit_sha") == pre_red_sha, (
        "fixture precondition: the degraded route leaves red_commit_sha at "
        f"pre_red_sha; actual {commit_result.data.get('red_commit_sha')!r} vs "
        f"{pre_red_sha!r}"
    )

    assert "pkg/mod.py" in _porcelain(repo), (
        "no RED test reached git, so no skeleton may be committed; actual "
        f"porcelain={_porcelain(repo)!r}"
    )
    assert _events_of(captured, "red_skeleton_committed") == [], (
        f"no 'red_skeleton_committed' may fire on the degraded route; actual "
        f"events={[e['type'] for e in captured]!r}"
    )
    skipped = _events_of(captured, "red_skeleton_commit_skipped")
    assert skipped, (
        f"expected a 'red_skeleton_commit_skipped' event; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert skipped[-1]["payload"].get("reason") == "red_tests_uncommitted", (
        f"expected reason=='red_tests_uncommitted'; actual {skipped[-1]['payload']!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"the still-dirty prod file must be blocked; actual "
        f"{verify_result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# §9c — AC25-AC30, added in r3 by the gate verdict on r2
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac25_full_chokepoint_path_one_ctx_one_scratchpad(tmp_path, monkeypatch):
    """AC25 (gate MAJOR-B) — the ONLY test that walks BOTH chokepoints.

    Every other shield test hand-stages the snapshot, and the three tests that
    drive the real `_invoke_red_llm` never call `_commit_red_tests`. So the two
    `build_stamp` call sites (§3.2 writes, §3.3 reads) are never shown to
    AGREE. A GREEN whose two stamps disagree — a different `run_id` source, a
    different cycle, a second token — makes the feature INERT on the production
    path while every other test in this file stays green. That is the vacuum
    this AC closes.

    Contract here: the test stages NOT ONE BYTE of the snapshot. The real
    `_invoke_red_llm` writes it; the real `_commit_red_tests` reads it; both
    run on the SAME `ctx` and the SAME scratchpad, exactly as production wires
    them. `pre.py` is dirty BEFORE the RED invocation; the mocked LLM writes
    `skel.py` and the RED test DURING it. Only the LLM subprocess is mocked.

    Expected: `skel.py` committed (RED-authored), `pre.py` NOT committed
    (pre-existing dirt), guard blocks naming `pre.py`.
    """
    captured = _record_events(monkeypatch)
    repo = Path(os.path.realpath(str(tmp_path / "ac25")))
    _init_repo(repo)
    _write(repo, "pre.py", "# v1\n")
    _write(repo, "pkg/__init__.py", "")
    _write(repo, "tests/.gitkeep", "")
    subprocess.run(
        ["git", "add", "pre.py", "pkg/__init__.py", "tests/.gitkeep"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "init ac25"], cwd=repo, check=True)
    # Dirty BEFORE invoke_red_llm — the operator-restart / stale-GREEN shape.
    _write(repo, "pre.py", "# v2 — dirty before RED\n")

    scratchpad = Path(os.path.realpath(str(tmp_path / "ac25-scratch")))
    scratchpad.mkdir()
    spec_path = _write_spec(tmp_path, "ac25-spec.md", ["pre.py", "pkg/skel.py"])
    ctx = _make_ctx(scratchpad, repo)

    def _llm_that_writes_red(**kwargs):
        """The RED agent's real effect: a prod skeleton + a behavioural test.
        Returns an error result so `_invoke_red_llm` skips the unrelated
        GH1179 write-boundary machinery — the snapshot is written BEFORE the
        LLM either way, which is the seam under test."""
        _write(repo, "pkg/skel.py", STUB_MOD_BODY)
        sh = _write(repo, "tests/red_check.test.sh", RED_SH_BODY)
        sh.chmod(0o755)
        return _broken_llm()

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _llm_that_writes_red)

    p5._invoke_red_llm(ctx, _llm_prev(scratchpad, spec_path))

    snapshot = scratchpad / PRE_RED_DIRTY_RELPATH
    assert snapshot.exists(), (
        "chokepoint 1 must have written the snapshot itself — this test stages "
        "none of it; actual scratchpad="
        f"{[str(p.relative_to(scratchpad)) for p in scratchpad.rglob('*')]!r}"
    )

    prev = StepResult(
        status="ok",
        data={"cycle": 1, "spec_path": spec_path, "red_test_paths": None,
              "red_log_path": str(scratchpad / "tests/build-red-output.log")},
        duration_ms=0, step_name="write_red_artifact",
    )
    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    porcelain = _porcelain(repo)
    assert "pkg/skel.py" not in porcelain, (
        "the file RED authored during this invocation must be committed — if "
        "the two build_stamp call sites disagree the snapshot is rejected and "
        f"nothing is committed; actual porcelain={porcelain!r}"
    )
    assert "pkg/skel.py" in _head_stat(repo), (
        f"HEAD must name the skeleton; actual {_head_stat(repo)!r}"
    )
    assert "pre.py" in porcelain, (
        "the path dirty BEFORE the RED invocation must NOT be committed; "
        f"actual porcelain={porcelain!r}"
    )
    committed = _events_of(captured, "red_skeleton_committed")
    assert committed and committed[-1]["payload"].get("paths") == ["pkg/skel.py"], (
        f"expected red_skeleton_committed paths==['pkg/skel.py']; actual "
        f"{[e['payload'] for e in committed]!r}"
    )

    verify_result = p5._verify_red_fails_mechanically(ctx, commit_result)
    assert verify_result.error_code == "E_RED_WORKTREE_DIRTY", (
        f"the guard must block on the pre-existing dirt; actual "
        f"{verify_result.error_code!r}"
    )
    assert "pre.py" in (verify_result.error or ""), (
        f"the refusal must name pre.py; actual {verify_result.error!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac26_red_skeleton_sha_has_exactly_one_reader(tmp_path):
    """AC26 (gate MAJOR-A): the new key's blast radius is proved BY A RUN, not
    by a promise. `red_skeleton_sha` must be READ in exactly one place —
    `_detect_green_complete_resume` — while writes may occur anywhere.

    Twice in this lot the radius of a moved diff base was reasoned about BY
    HAND and twice a consumer was missed (spec §3.3a). The deterministic cure
    is to make the radius mechanically checkable, so this parses the engine
    module's AST and classifies each occurrence as a read (`.get("...")` or a
    Load-context subscript) or a write.

    THE READ MUST BE INLINE IN `_detect_green_complete_resume` — this is
    deliberate, not an accident of how the check is written. Extracting the
    lookup into a §1g helper would put the read in that helper's scope and
    REDDEN this test on purpose: the whole guarantee is that the key's reader
    set is enumerable by a mechanical pass, and one level of indirection is
    exactly what made the r2 radius analysis wrong twice. An implementer who
    hits this failure should inline the lookup, not widen the check.

    The key must also be ABSENT from every other production file in the §5
    scope list — a second module reading it re-opens the same radius question
    from a place this AST pass does not look.

    This is a META-check on the new key's reader set, not a classification of
    a production file by the shape of its body — the method rejected in §2
    applies to deciding whether a file is a "stub", which nothing here does.
    """
    src_path = ENGINE_ROOT / "workflows" / "phase_5_implement.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    parents: dict = {}
    func_of: dict = {}

    def _walk(node, current_fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current_fn = node.name
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
            func_of[id(child)] = current_fn
            _walk(child, current_fn)

    _walk(tree, None)

    readers: set = set()
    occurrences = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and node.value == "red_skeleton_sha"):
            continue
        occurrences += 1
        parent = parents.get(id(node))
        is_read = False
        if isinstance(parent, ast.Call):
            fn = parent.func
            if isinstance(fn, ast.Attribute) and fn.attr == "get" and node in parent.args:
                is_read = True
        elif isinstance(parent, ast.Subscript) and isinstance(
            getattr(parent, "ctx", None), ast.Load
        ):
            is_read = True
        if is_read:
            readers.add(func_of.get(id(node)))

    assert occurrences > 0, (
        f"'red_skeleton_sha' does not appear in {src_path} at all — the r3 key "
        f"split (§3.3a) is not implemented"
    )
    assert readers == {"_detect_green_complete_resume"}, (
        "spec §3.3a: `red_skeleton_sha` must have EXACTLY ONE reader, and the "
        "read must be INLINE in `_detect_green_complete_resume` (a §1g helper "
        "extraction is deliberately forbidden — it moves the read out of the "
        "one scope this pass can enumerate). Any other reader silently widens "
        "the blast radius that the r2 attempt got wrong twice; actual readers="
        f"{sorted(r or '<module>' for r in readers)!r}"
    )

    # The key must not leak into any other production file in the §5 scope.
    for rel in (
        "lib/red_skeleton.py",
        "lib/dirty_tree_guard.py",
        "lib/mutating_git_lint.py",
        "flags_catalog.py",
    ):
        other = ENGINE_ROOT / rel
        if not other.exists():
            continue
        assert "red_skeleton_sha" not in other.read_text(encoding="utf-8"), (
            f"`red_skeleton_sha` must not appear in {rel} — the key has ONE "
            f"reader, in phase_5_implement.py, and a second module touching it "
            f"re-opens the radius question outside this AST pass (§3.3a)"
        )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac27_skeleton_stays_inside_the_green_gate_window(tmp_path, monkeypatch):
    """AC27 (gate MAJOR-A), two-sided, on a real git diff.

    Every phase-5 GREEN gate — green-lint, security-lint, typecheck scope, and
    `authored_boundary.scan_suppression_paths` for E_BOUNDARY_SUPPRESSION —
    takes `red_commit_sha` as its diff base. So the committed skeleton MUST
    still appear in the diff from `red_commit_sha` (it keeps being scanned),
    and MUST NOT appear in the diff from `red_skeleton_sha` (which is what
    GH483's resume detector uses, so the skeleton stops being mistaken for a
    resumed GREEN).

    Both directions are asserted: "in the red_commit_sha diff" alone would be
    satisfied by never introducing `red_skeleton_sha` at all, and "absent from
    the red_skeleton_sha diff" alone would be satisfied by moving the boundary
    — the r2 mistake.
    """
    _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "ac27", pre_red_dirty=[])

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    red_commit_sha = commit_result.data.get("red_commit_sha")
    red_skeleton_sha = commit_result.data.get("red_skeleton_sha")
    assert red_skeleton_sha, (
        f"fixture precondition: red_skeleton_sha must be set; actual data="
        f"{commit_result.data!r}"
    )
    assert red_skeleton_sha != red_commit_sha, (
        "the two keys must be distinct commits — a single key cannot serve "
        f"both windows; actual both == {red_commit_sha!r}"
    )

    from_red = p5.git_diff_files(
        red_commit_sha, str(repo), untracked=True, segment_filter=None
    )
    from_skeleton = p5.git_diff_files(
        red_skeleton_sha, str(repo), untracked=True, segment_filter=None
    )

    assert "pkg/mod.py" in from_red, (
        "the skeleton must REMAIN in the diff from red_commit_sha so green-lint, "
        "security-lint, typecheck and the suppression scan still see it — "
        "moving the boundary exempts RED-authored production code from every "
        f"one of them (§3.3a); actual diff={from_red!r}"
    )
    assert "pkg/mod.py" not in from_skeleton, (
        "the skeleton must be ABSENT from the diff taken at red_skeleton_sha — "
        "that is the base GH483's resume detector uses, and the whole point of "
        f"the separate key; actual diff={from_skeleton!r}"
    )


def test_ac28_addable_xy_is_a_whitelist_of_whole_codes(tmp_path):
    """AC28 (gate MINOR-a): `AA` — unmerged "both added" — carries no `D`, no
    `R`, no `C` and no `U`, so the r2 per-character predicate `frozenset("?AM")`
    admitted it. It is reachable from a conflicted `git stash pop`, where
    MERGE_HEAD is absent so `E_GIT_BAD_STATE` (phase_5_implement.py:2026-2039)
    does not fire either — the engine would commit a file full of conflict
    markers as a "skeleton".

    Lesson #1400 in miniature: closure of a set of VALUES does not hold under a
    predicate over CHARACTERS. The XY code set is finite and documented by git,
    so it is enumerated whole.
    """
    m = _red_skeleton()

    assert m.addable_paths([("AA", "conflict.py"), ("??", "ok.py")]) == ["ok.py"], (
        "'AA' (unmerged both-added) must be rejected exactly like 'UU'/'D'; "
        f"actual {m.addable_paths([('AA', 'conflict.py'), ('??', 'ok.py')])!r}"
    )
    assert set(m.ADDABLE_XY) == {"??", " M", "M ", "MM", "A ", "AM"}, (
        "ADDABLE_XY must be a whitelist of WHOLE two-character codes (§3.1a); "
        f"actual {set(m.ADDABLE_XY)!r}"
    )
    for code in m.ADDABLE_XY:
        assert len(code) == 2, (
            f"every ADDABLE_XY member is a whole 2-char porcelain code, so a "
            f"per-character predicate cannot be reintroduced silently; actual "
            f"{code!r}"
        )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac29_dirty_snapshot_failure_skips_the_skeleton_commit(tmp_path, monkeypatch):
    """AC29 (gate MINOR-b): when the skeleton's own `snapshot_dirty_paths` call
    fails, nothing is committed and `red_skeleton_commit_skipped` carries
    `reason == "dirty_snapshot_failed"` — the fifth of the six declared
    reasons, each of which must be distinguishable from the others.

    `snapshot_dirty_paths` is stubbed on the PRODUCTION module instance (§9d
    MAJOR-2) — a collaborator of `_commit_red_skeleton`, not the unit under
    test — rather than `git_port.git_read`, because the latter is a shared
    module attribute whose replacement would break `_commit_red_tests`'s own
    reads and make the fixture prove nothing.
    """
    m = _prod_red_skeleton()
    captured = _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(tmp_path, "ac29", pre_red_dirty=[])

    monkeypatch.setattr(
        m, "snapshot_dirty_paths",
        lambda *a, **kw: ([], "simulated git status failure (GH1406 AC29)"),
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    assert "pkg/mod.py" in _porcelain(repo), (
        "a failed dirty snapshot must commit nothing; actual porcelain="
        f"{_porcelain(repo)!r}"
    )
    assert _events_of(captured, "red_skeleton_committed") == [], (
        f"no 'red_skeleton_committed' may fire; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    skipped = _events_of(captured, "red_skeleton_commit_skipped")
    assert skipped, (
        f"expected a 'red_skeleton_commit_skipped' event; actual events="
        f"{[e['type'] for e in captured]!r}"
    )
    assert skipped[-1]["payload"].get("reason") == "dirty_snapshot_failed", (
        f"expected reason=='dirty_snapshot_failed'; actual {skipped[-1]['payload']!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac30_cycle_two_commits_skeleton_and_sidecar_keeps_red_commit_sha(
    tmp_path, monkeypatch
):
    """AC30 (gate r2 edge 3): the same scenario at `cycle: 2` behaves
    identically, AND the GH1034 per-cycle sidecar records the RED-TESTS commit
    (`red_commit_sha`), never the skeleton sha.

    That sidecar is what `_attempt_red_cycle_restore` checks out to recover a
    degenerate later cycle (`git checkout <c_prev_sha> -- <red_test_paths>`).
    Recording the skeleton sha there would still contain the tests, so a
    checkout-only assertion could not tell the two apart — the sidecar VALUE is
    asserted directly instead.
    """
    _record_events(monkeypatch)
    repo, scratchpad, ctx, prev = _stage_red_cycle(
        tmp_path, "ac30", pre_red_dirty=[], cycle=2
    )

    commit_result = p5._commit_red_tests(ctx, prev)
    _require_commit_ok(commit_result)

    assert "pkg/mod.py" not in _porcelain(repo), (
        "cycle 2 must commit the RED-authored skeleton exactly like cycle 1; "
        f"actual porcelain={_porcelain(repo)!r}"
    )
    red_commit_sha = commit_result.data.get("red_commit_sha")
    red_skeleton_sha = commit_result.data.get("red_skeleton_sha")
    assert red_skeleton_sha and red_skeleton_sha != red_commit_sha, (
        f"fixture precondition: distinct keys; actual red_commit_sha="
        f"{red_commit_sha!r} red_skeleton_sha={red_skeleton_sha!r}"
    )

    sidecar = p5._read_red_cycle_sha(scratchpad, 2)
    assert sidecar == red_commit_sha, (
        "the GH1034 per-cycle sidecar must record the RED-TESTS commit so a "
        "later degeneracy restore reproduces cycle-2 tests, not the skeleton; "
        f"actual sidecar={sidecar!r} red_commit_sha={red_commit_sha!r} "
        f"red_skeleton_sha={red_skeleton_sha!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# §9d — AC31-AC32, added in r4 by the gate verdict on r3 (class: VACUUM)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac31_process_token_actually_varies_between_processes():
    """AC31 (gate MAJOR-1 r3) — the nonce must actually be a nonce.

    The ENTIRE substance of the §3.1b fix is that `PROCESS_TOKEN` DIFFERS
    between processes: that is the only thing standing between a durable-resume
    re-entry and acceptance of a stale snapshot, i.e. the literal GH961/GH1039
    let-through. Nothing else in this file can tell a real nonce from a
    constant — AC3 compares the persisted token against `PROCESS_TOKEN` in the
    SAME process, AC19's rejection leg uses a hardcoded foreign literal, and
    the §12 stamp mutation only proves that a COMPARISON exists, never that the
    compared value varies. `PROCESS_TOKEN = "hal-red-skeleton"` would pass
    every one of them while silently reverting the fix.

    So: observe the value in two genuinely separate interpreters and require
    all three (both children and this process) to differ, each a 32-char uuid4
    hex. §12 mutation: pin the token to a constant and this MUST redden.
    """
    in_process = _red_skeleton().PROCESS_TOKEN

    env = dict(os.environ)
    lib_dir = str(ENGINE_ROOT / "lib")
    env["PYTHONPATH"] = (
        lib_dir + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else lib_dir
    )

    observed: list = []
    for i in range(2):
        proc = subprocess.run(
            [sys.executable, "-c",
             "import red_skeleton; print(red_skeleton.PROCESS_TOKEN)"],
            capture_output=True, text=True, env=env, cwd=str(ENGINE_ROOT),
        )
        assert proc.returncode == 0, (
            f"child {i} could not import red_skeleton (rc={proc.returncode}); "
            f"stderr={proc.stderr[-500:]!r}"
        )
        observed.append(proc.stdout.strip())

    for i, token in enumerate(observed):
        assert len(token) == 32 and all(c in "0123456789abcdef" for c in token), (
            f"child {i} token must be a uuid4().hex (32 lowercase hex chars) — "
            f"a readable constant is exactly the weakening this AC blocks; "
            f"actual {token!r}"
        )
    assert observed[0] != observed[1], (
        "two separate processes must observe DIFFERENT PROCESS_TOKEN values — "
        "identical values mean the token is a constant and a stale snapshot "
        f"survives durable resume (§3.1b); actual both == {observed[0]!r}"
    )
    assert in_process not in observed, (
        "this process's PROCESS_TOKEN must differ from both children's; "
        f"actual in_process={in_process!r} children={observed!r}"
    )


@pytest.mark.skip(reason="hal#1145 phase-1 declared gap: asserts GH1406 wiring inside workflows/phase_5_implement.py (UUT-B) — _commit_red_skeleton, _dirty_tree_block_message, the p5.red_skeleton export and the resulting event stream. Phase 1 ships lib/red_skeleton.py only; editing existing product files is phase B. These ACs ARE the wiring's proof — un-skip them in phase B, do not delete. AC31/AC32 additionally degenerate under the bd#44 package layout (see _red_skeleton docstring).")
def test_ac32_tests_and_production_share_one_red_skeleton_module(tmp_path):
    """AC32 (gate MAJOR-2 r3) — one module object, named explicitly.

    `phase_5_implement` imports lib modules as `from bytedigger_engine.lib import X`, registering
    `sys.modules["lib.X"]`; a bare `import red_skeleton` registers a SECOND,
    independently executed copy with its own `PROCESS_TOKEN`. If the shield
    fixtures stamped snapshots through that second copy, S1, S4, AC19b, AC20,
    AC27 and AC30 would all redden against a perfectly correct GREEN, with
    messages about `git status --porcelain` that never mention imports — and
    the cheapest way out for an implementer would be to make the token a
    constant, silently undoing the fix AC31 guards.

    This AC names the invariant instead: the object the fixtures stamp with IS
    the object production reads with. The token equality is the load-bearing
    half — two module instances agree on identity only if there is one.
    """
    prod = _prod_red_skeleton()
    assert prod is getattr(p5, "red_skeleton"), (
        "production must expose a stable `red_skeleton` attribute; the "
        "fixtures bind to exactly that object"
    )

    scratchpad = tmp_path / "ac32-scratch"
    scratchpad.mkdir()
    ctx = _make_ctx(scratchpad, tmp_path)
    fixture_stamp = _stamp_for(ctx, 1)

    assert fixture_stamp.get("process_token") == prod.PROCESS_TOKEN, (
        "the fixtures' stamp must carry PRODUCTION's PROCESS_TOKEN — a "
        "mismatch means the test harness bound a SECOND execution of "
        "red_skeleton (bare `import red_skeleton` vs `from bytedigger_engine.lib import "
        "red_skeleton`), which would make every shield unsatisfiable for a "
        f"reason no failure message mentions; actual fixture="
        f"{fixture_stamp.get('process_token')!r} production="
        f"{prod.PROCESS_TOKEN!r}"
    )
    assert fixture_stamp == prod.build_stamp(ctx, 1), (
        "the fixture stamp must be byte-identical to production's own "
        f"build_stamp for the same (ctx, cycle); actual {fixture_stamp!r} vs "
        f"{prod.build_stamp(ctx, 1)!r}"
    )
