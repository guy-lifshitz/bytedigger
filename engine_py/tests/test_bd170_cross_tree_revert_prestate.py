"""RED tests for bd#170 -- cross-tree auto-revert never resets a path that was
dirty in the main checkout before the worker ran.

Frozen spec: docs/decisions/2026-10-02-bd170-cross-tree-revert-prestate.md

Real git (main checkout + secondary worktree), real production functions; the
only monkeypatched seam is `_emit_safe` (and, for AC5(c), `git_port.git_read`).
`_snapshot_main_checkout_state` does not exist yet: it is resolved with
getattr inside test bodies so this file always collects. The new `pre_state`
kwarg of `_maybe_emit_cross_tree_warning` is passed only when the signature
accepts it, so today's failures are value assertions (file reset / event
missing), never a TypeError from the kwarg.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import subprocess
import types
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult
from bytedigger_engine.workflows import phase_workflows_common


# ---------------------------------------------------------------- fixtures --


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True, capture_output=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "-C", str(repo), "add", relpath], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", msg,
        ],
        check=True, capture_output=True,
    )


def _build_repo_pair(tmp_path: Path, name: str, files: "dict[str, str]") -> "tuple[Path, Path]":
    main = Path(os.path.realpath(str(tmp_path / f"{name}_main")))
    _init_repo(main)
    for relpath, body in files.items():
        _commit_file(main, relpath, body, f"init {relpath}")
    wt = Path(os.path.realpath(str(tmp_path / f"{name}_wt")))
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-b", f"{name}-feat", str(wt)],
        check=True, capture_output=True,
    )
    return main, wt


def _append_uncommitted(path: Path, extra_text: str) -> None:
    path.write_bytes(path.read_bytes() + extra_text.encode("utf-8"))


def _record_events(monkeypatch, module) -> "list[dict]":
    captured: list[dict] = []

    def _recorder(event_type, payload=None, **kw):
        captured.append({"type": str(event_type), "payload": dict(payload or {}), "kw": dict(kw)})
        return None

    monkeypatch.setattr(module, "_emit_safe", _recorder)
    return captured


def _make_result(data: dict) -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_red_llm")


def _call_wrapper(result, wt, pre_state) -> StepResult:
    """Pass pre_state only when the signature accepts it (so today's failure
    is the value assertion, not a TypeError from the new kwarg)."""
    fn = phase_workflows_common._maybe_emit_cross_tree_warning
    if "pre_state" in inspect.signature(fn).parameters:
        return fn(result, wt, pre_state=pre_state)
    return fn(result, wt)


def _snapshot(main: Path, wt: Path) -> dict:
    """Real snapshot helper when it exists. Until it does, a hand-built
    snapshot (user-dirty paths of `main`) keeps the failure on the value
    assertion; AC5 independently requires the real helper."""
    snap_fn = getattr(phase_workflows_common, "_snapshot_main_checkout_state", None)
    if snap_fn is not None:
        return snap_fn(wt)
    out = subprocess.run(
        ["git", "-C", str(main), "status", "--porcelain"], capture_output=True, text=True, check=True,
    ).stdout
    dirty = {ln[3:].strip(): None for ln in out.splitlines() if ln and not ln.startswith("??")}
    return {"ok": True, "main_repo_root": str(main), "dirty": dirty}


def _hash_object(repo: Path, relpath: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", "--", relpath], capture_output=True, text=True, check=True,
    ).stdout.strip()


X = "src/x.py"
COMMITTED = "# x v1\n"


def _run_ac1_scenario(tmp_path, monkeypatch, name, manifest_source):
    main, wt = _build_repo_pair(tmp_path, name, {X: COMMITTED})
    xp = main / X
    _append_uncommitted(xp, "USER\n")
    snap = _snapshot(main, wt)
    _append_uncommitted(xp, "WORKER\n")
    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({"worker_written_paths": [str(xp)], "manifest_source": manifest_source})
    out = _call_wrapper(result, wt, snap)
    return xp, events, out


def _assert_ac1_outcome(xp, events, out, label):
    assert xp.read_bytes() == (COMMITTED + "USER\nWORKER\n").encode(), (
        f"{label}: expected nothing reset (committed+USER+WORKER); actual {xp.read_bytes()!r}"
    )
    refused = [e for e in events if e["type"] == "cross_tree_revert_prestate_refused"]
    assert len(refused) == 1, f"{label}: expected one prestate_refused event; actual {[e['type'] for e in events]!r}"
    pl = refused[0]["payload"]
    assert pl.get("reason") == "dirty_at_start", f"{label}: reason {pl.get('reason')!r}"
    assert pl.get("files") == [X], f"{label}: files {pl.get('files')!r}"
    assert pl.get("changed_since_start") == {X: True}, f"{label}: changed_since_start {pl.get('changed_since_start')!r}"
    assert out.data.get("cross_tree_prestate_refused_files") == [X], (
        f"{label}: result.data key {out.data.get('cross_tree_prestate_refused_files')!r}"
    )


# -------------------------------------------------------------------- ACs --


def test_ac1_dirty_before_run_survives_and_refusal_event_emitted(tmp_path, monkeypatch):
    """AC1. Fails today: wrapper resets the owned file to HEAD (user edit lost)
    and no cross_tree_revert_prestate_refused event exists."""
    xp, events, out = _run_ac1_scenario(tmp_path, monkeypatch, "ac1", "harness_tool_record")
    _assert_ac1_outcome(xp, events, out, "AC1")
    assert not [e for e in events if e["type"] == "cross_tree_edit_reverted"], "AC1: nothing may be reverted"


def test_ac2_clean_before_run_reverted_as_today(tmp_path, monkeypatch):
    """AC2. Revert behaviour itself already works today; this test fails today
    only because it requires the real snapshot helper (absent) to produce the
    clean-main pre_state. Post-GREEN it guards that clean paths still revert."""
    main, wt = _build_repo_pair(tmp_path, "ac2", {X: COMMITTED})
    xp = main / X
    snap_fn = getattr(phase_workflows_common, "_snapshot_main_checkout_state", None)
    assert snap_fn is not None, "AC2: expected _snapshot_main_checkout_state to exist"
    snap = snap_fn(wt)
    assert snap.get("ok") is True and snap.get("dirty") == {}, f"AC2: clean main snapshot, actual {snap!r}"
    _append_uncommitted(xp, "WORKER\n")
    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({"worker_written_paths": [str(xp)], "manifest_source": "harness_tool_record"})
    _call_wrapper(result, wt, snap)
    assert xp.read_bytes() == COMMITTED.encode(), f"AC2: expected reset to committed; actual {xp.read_bytes()!r}"
    assert [e for e in events if e["type"] == "cross_tree_edit_reverted"], "AC2: expected cross_tree_edit_reverted"
    assert not [e for e in events if e["type"] == "cross_tree_revert_prestate_refused"], "AC2: no refusal expected"


def test_ac3_mixed_dirty_at_start_kept_clean_at_start_reverted(tmp_path, monkeypatch):
    """AC3. Fails today: a.py (user edit) is reset too, and no refusal event."""
    a, b = "src/a.py", "src/b.py"
    main, wt = _build_repo_pair(tmp_path, "ac3", {a: "# a\n", b: "# b\n"})
    _append_uncommitted(main / a, "USER\n")
    snap = _snapshot(main, wt)
    _append_uncommitted(main / a, "WORKER\n")
    _append_uncommitted(main / b, "WORKER\n")
    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({
        "worker_written_paths": [str(main / a), str(main / b)], "manifest_source": "harness_tool_record",
    })
    _call_wrapper(result, wt, snap)
    assert (main / b).read_bytes() == b"# b\n", f"AC3: b.py must be reverted; actual {(main / b).read_bytes()!r}"
    assert (main / a).read_bytes() == b"# a\nUSER\nWORKER\n", (
        f"AC3: a.py must be kept; actual {(main / a).read_bytes()!r}"
    )
    refused = [e for e in events if e["type"] == "cross_tree_revert_prestate_refused"]
    assert len(refused) == 1, f"AC3: expected one refusal event; actual {[e['type'] for e in events]!r}"
    assert refused[0]["payload"].get("files") == [a], f"AC3: files {refused[0]['payload'].get('files')!r}"
    reverted = [e for e in events if e["type"] == "cross_tree_edit_reverted"]
    assert reverted and reverted[0]["payload"].get("files") == [b], "AC3: reverted event must list only b.py"


@pytest.mark.parametrize("pre_state", [None, {"ok": False, "reason": "x"}], ids=["none", "not_ok"])
def test_ac4_no_snapshot_fails_closed(tmp_path, monkeypatch, pre_state):
    """AC4. Fails today: owned file is reset (no fail-closed) and no
    pre_state_unavailable event."""
    main, wt = _build_repo_pair(tmp_path, "ac4", {X: COMMITTED})
    xp = main / X
    _append_uncommitted(xp, "WORKER\n")
    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({"worker_written_paths": [str(xp)], "manifest_source": "harness_tool_record"})
    _call_wrapper(result, wt, pre_state)
    assert xp.read_bytes() == (COMMITTED + "WORKER\n").encode(), f"AC4: file must not be reset; actual {xp.read_bytes()!r}"
    refused = [e for e in events if e["type"] == "cross_tree_revert_prestate_refused"]
    assert len(refused) == 1, f"AC4: expected one refusal event; actual {[e['type'] for e in events]!r}"
    pl = refused[0]["payload"]
    assert pl.get("reason") == "pre_state_unavailable", f"AC4: reason {pl.get('reason')!r}"
    assert pl.get("changed_since_start") == {X: None}, f"AC4: changed_since_start {pl.get('changed_since_start')!r}"


def _require_snapshot():
    fn = getattr(phase_workflows_common, "_snapshot_main_checkout_state", None)
    assert fn is not None, "expected phase_workflows_common._snapshot_main_checkout_state to exist; absent today"
    return fn


def test_ac5a_snapshot_clean_main(tmp_path):
    """AC5(a). Fails today: helper does not exist."""
    snap_fn = _require_snapshot()
    main, wt = _build_repo_pair(tmp_path, "ac5a", {X: COMMITTED})
    snap = snap_fn(wt)
    assert snap.get("ok") is True and snap.get("dirty") == {}, f"AC5a: actual {snap!r}"
    assert Path(snap["main_repo_root"]).resolve() == main.resolve(), f"AC5a: main_repo_root {snap.get('main_repo_root')!r}"


def test_ac5b_snapshot_dirty_tracked_blob_and_untracked_ignored(tmp_path):
    """AC5(b). Fails today: helper does not exist."""
    snap_fn = _require_snapshot()
    main, wt = _build_repo_pair(tmp_path, "ac5b", {"a.py": "# a\n"})
    _append_uncommitted(main / "a.py", "USER\n")
    (main / "u.py").write_text("untracked\n")
    snap = snap_fn(wt)
    assert snap.get("ok") is True, f"AC5b: actual {snap!r}"
    assert set(snap["dirty"]) == {"a.py"}, f"AC5b: dirty keys {set(snap['dirty'])!r}"
    assert snap["dirty"]["a.py"] == _hash_object(main, "a.py"), f"AC5b: blob {snap['dirty']['a.py']!r}"


@pytest.mark.parametrize("mode", ["raises_oserror", "raises_runtimeerror", "status_rc128"])
def test_ac5c_snapshot_git_failure_returns_not_ok_no_exception(tmp_path, monkeypatch, mode):
    """AC5(c). Fails today: helper does not exist."""
    snap_fn = _require_snapshot()
    main, wt = _build_repo_pair(tmp_path, "ac5c", {"a.py": "# a\n"})
    real = phase_workflows_common.git_port.git_read

    def _fake(args, *a, **kw):
        if mode == "raises_oserror":
            raise OSError("boom")
        if mode == "raises_runtimeerror":
            raise RuntimeError("boom")
        if args and args[0] == "status":
            return types.SimpleNamespace(returncode=128, stdout="", stderr="fatal")
        return real(args, *a, **kw)

    monkeypatch.setattr(phase_workflows_common.git_port, "git_read", _fake)
    snap = snap_fn(wt)
    assert snap.get("ok") is False, f"AC5c[{mode}]: expected ok False; actual {snap!r}"
    assert isinstance(snap.get("reason"), str), f"AC5c[{mode}]: expected str reason; actual {snap!r}"


def test_ac5d_snapshot_worktree_is_main_root(tmp_path):
    """AC5(d). Fails today: helper does not exist."""
    snap_fn = _require_snapshot()
    main, _wt = _build_repo_pair(tmp_path, "ac5d", {"a.py": "# a\n"})
    _append_uncommitted(main / "a.py", "USER\n")  # dirty, but no cross-tree possible
    snap = snap_fn(main)
    assert snap.get("ok") is True and snap.get("dirty") == {}, f"AC5d: actual {snap!r}"
    assert Path(snap["main_repo_root"]).resolve() == main.resolve(), f"AC5d: main_repo_root {snap.get('main_repo_root')!r}"


def test_ac5e_hash_object_failure_keeps_path_dirty_with_none_blob(tmp_path, monkeypatch):
    """AC5(e). Fails today: helper does not exist."""
    snap_fn = _require_snapshot()
    main, wt = _build_repo_pair(tmp_path, "ac5e", {"a.py": "# a\n"})
    _append_uncommitted(main / "a.py", "USER\n")
    real = phase_workflows_common.git_port.git_read

    def _fake(args, *a, **kw):
        if args and args[0] == "hash-object":
            return types.SimpleNamespace(returncode=128, stdout="", stderr="fatal")
        return real(args, *a, **kw)

    monkeypatch.setattr(phase_workflows_common.git_port, "git_read", _fake)
    snap = snap_fn(wt)
    assert snap.get("ok") is True, f"AC5e: expected ok True; actual {snap!r}"
    assert snap.get("dirty") == {"a.py": None}, f"AC5e: expected dirty {{'a.py': None}}; actual {snap.get('dirty')!r}"


def test_ac1b_unchanged_since_start_held_back_changed_false_and_metadata_mirror(tmp_path, monkeypatch):
    """AC1b. Fails today: file reset, no event, no result keys."""
    main, wt = _build_repo_pair(tmp_path, "ac1b", {X: COMMITTED})
    xp = main / X
    _append_uncommitted(xp, "USER\n")
    snap = _snapshot(main, wt)  # worker then changes nothing
    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({"worker_written_paths": [str(xp)], "manifest_source": "harness_tool_record"})
    out = _call_wrapper(result, wt, snap)
    assert xp.read_bytes() == (COMMITTED + "USER\n").encode(), f"AC1b: must not reset; actual {xp.read_bytes()!r}"
    refused = [e for e in events if e["type"] == "cross_tree_revert_prestate_refused"]
    assert len(refused) == 1, f"AC1b: expected one refusal event; actual {[e['type'] for e in events]!r}"
    assert refused[0]["payload"].get("changed_since_start") == {X: False}, (
        f"AC1b: actual {refused[0]['payload'].get('changed_since_start')!r}"
    )
    assert out.data.get("cross_tree_prestate_refused_files") == [X], f"AC1b: data {out.data!r}"
    assert out.metadata.get("cross_tree_prestate_refused_files") == [X], f"AC1b: metadata {out.metadata!r}"


@pytest.mark.parametrize("shape", ["other_root", "no_dirty", "dirty_list", "string"])
def test_ac4b_unusable_snapshot_shapes_fail_closed(tmp_path, monkeypatch, shape):
    """AC4b. Fails today: owned file is reset and no event is emitted."""
    main, wt = _build_repo_pair(tmp_path, "ac4b", {X: COMMITTED})
    other = tmp_path / "other_dir"
    other.mkdir()
    pre_state = {
        "other_root": {"ok": True, "main_repo_root": str(other), "dirty": {}},
        "no_dirty": {"ok": True, "main_repo_root": str(main)},
        "dirty_list": {"ok": True, "main_repo_root": str(main), "dirty": []},
        "string": "x",
    }[shape]
    xp = main / X
    _append_uncommitted(xp, "WORKER\n")
    events = _record_events(monkeypatch, phase_workflows_common)
    result = _make_result({"worker_written_paths": [str(xp)], "manifest_source": "harness_tool_record"})
    _call_wrapper(result, wt, pre_state)  # must not raise
    assert xp.read_bytes() == (COMMITTED + "WORKER\n").encode(), f"AC4b[{shape}]: reset; actual {xp.read_bytes()!r}"
    refused = [e for e in events if e["type"] == "cross_tree_revert_prestate_refused"]
    assert len(refused) == 1, f"AC4b[{shape}]: expected one refusal; actual {[e['type'] for e in events]!r}"
    assert refused[0]["payload"].get("reason") == "pre_state_unavailable", (
        f"AC4b[{shape}]: reason {refused[0]['payload'].get('reason')!r}"
    )


@pytest.mark.parametrize("manifest_source", ["harness_tool_record", "orchestrator_observed"])
def test_ac6_backend_independence(tmp_path, monkeypatch, manifest_source):
    """AC6. 'git_diff' is not in _ALLOWED_MANIFEST_SOURCES without a backend
    registration, so the second source is 'orchestrator_observed' (spec
    fallback). Same outcome as AC1 for both. Fails today: file reset."""
    xp, events, out = _run_ac1_scenario(tmp_path, monkeypatch, "ac6", manifest_source)
    _assert_ac1_outcome(xp, events, out, f"AC6[{manifest_source}]")


_CALLERS = [
    ("bytedigger_engine.workflows.phase_5_implement", "_invoke_red_llm"),
    ("bytedigger_engine.workflows.phase_5_implement", "_invoke_green_llm"),
    ("bytedigger_engine.workflows.phase_6_review", "_invoke_fix_llm"),
]


def _call_name(node: ast.Call) -> "str | None":
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _shallow_nodes(fdef: ast.AST) -> "list[ast.AST]":
    """All nodes of fdef's own body, not descending into nested def/lambda."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fdef))
    while stack:
        n = stack.pop()
        out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


def _is_snapshot_value(node: ast.AST, *, allow_none: bool) -> bool:
    if isinstance(node, ast.Call):
        return _call_name(node) == "_snapshot_main_checkout_state"
    if isinstance(node, ast.Constant):
        return allow_none and node.value is None
    if isinstance(node, ast.IfExp):
        return _is_snapshot_value(node.body, allow_none=True) and _is_snapshot_value(node.orelse, allow_none=True)
    return False


@pytest.mark.parametrize("modname,funcname", _CALLERS, ids=[c[1] for c in _CALLERS])
def test_ac7_callers_take_snapshot_before_worker_call_and_pass_pre_state(modname, funcname):
    """AC7. Fails today: no caller calls _snapshot_main_checkout_state or
    passes pre_state=."""
    mod = importlib.import_module(modname)
    tree = ast.parse(Path(inspect.getsourcefile(mod)).read_text(encoding="utf-8"))
    fdef = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == funcname), None,
    )
    assert fdef is not None, f"AC7: {funcname} not found in {modname}"
    all_calls = [n for n in ast.walk(fdef) if isinstance(n, ast.Call)]
    shallow = _shallow_nodes(fdef)  # excludes nested def / lambda bodies (gate F4)
    calls = [n for n in shallow if isinstance(n, ast.Call)]
    invoke_lines = sorted(c.lineno for c in all_calls if _call_name(c) == "invoke_llm_subprocess")
    snap_lines = sorted(c.lineno for c in calls if _call_name(c) == "_snapshot_main_checkout_state")
    warn_calls = [c for c in calls if _call_name(c) == "_maybe_emit_cross_tree_warning"]
    assert invoke_lines, f"AC7: no invoke_llm_subprocess call in {funcname}"
    assert snap_lines, f"AC7: {funcname} never calls _snapshot_main_checkout_state"
    assert snap_lines[0] < invoke_lines[0], (
        f"AC7: snapshot (line {snap_lines[0]}) must precede first invoke_llm_subprocess (line {invoke_lines[0]})"
    )
    assert warn_calls, f"AC7: {funcname} has no _maybe_emit_cross_tree_warning call"
    for c in warn_calls:
        kws = {k.arg: k.value for k in c.keywords}
        assert "pre_state" in kws, f"AC7: warning call at line {c.lineno} lacks pre_state= keyword"
        val = kws["pre_state"]
        assert isinstance(val, ast.Name), (
            f"AC7/F4: pre_state at line {c.lineno} must be a Name bound from the snapshot call; actual {ast.dump(val)}"
        )
        assigns = [
            n for n in shallow
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == val.id for t in n.targets)
        ]
        assert assigns, f"AC7/F4: no assignment of {val.id!r} in {funcname}'s own body"
        assert any(_is_snapshot_value(a.value, allow_none=False) for a in assigns), (
            f"AC7/F4: {val.id!r} is never assigned from _snapshot_main_checkout_state(...)"
        )
        assert all(_is_snapshot_value(a.value, allow_none=True) for a in assigns), (
            f"AC7/F4: every assignment of {val.id!r} must be the snapshot call, a conditional of it/None, or None"
        )
