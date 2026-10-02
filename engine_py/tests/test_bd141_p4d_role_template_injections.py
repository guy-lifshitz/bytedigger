"""bd#141 item 4(d) RED: every phase declares its role template on the
`injections` channel (R3.2).

Spec: docs/decisions/2026-10-02-bd141-p4d-role-template-injections.md

Today only `phase_2_explore` declares its role-template block. The other 16
builders prepend the template through `_maybe_role_template(ctx) -> str`, which
discards `source_id`, so their dispatches carry no `injections=` and R3.2 stays
`not-checked` on those steps.

AC -> test map
--------------
AC1  test_ac1_role_template_record
     test_ac1_declared_injections
     test_ac1_role_template_loads_through_real_loader
AC2  test_ac2_builders_use_attributable_reader (helper kept, only in common)
AC3  test_ac3_every_builder_records_role_template_verbatim[<16 builders>]
AC4  test_ac4_prompt_bytes_unchanged[<16 builders>]            (guard: green today)
AC5  test_ac5_every_dispatch_declares_injections
     test_ac5_dispatch_call_count_is_pinned                    (guard: green today)
     test_ac5_dispatch_hidden_behind_executor_submit_declares  (spec gap: the
       COMPLEX satisfaction fan-out hands `invoke_llm_subprocess` to
       `executor.submit`, so it is a 22nd dispatch the spec's "21" misses)
AC6  test_ac6_attestation_carries_role_template[clarify|architect]
AC7  test_ac7_no_template_no_declaration[clarify|architect]    (guard: green today)
AC8  test_ac8_green_retry_declares_same_injections
     test_ac8_fix_retry_declares_same_injections
AC9  test_ac9_phase_2_helper_equals_declared_injections

Teeth (spec section 4): AC1 red if the record drops `source_id` or
`_declared_injections` raises on a non-dict. AC2 red if any builder keeps the
bare-string helper. AC3 red if one builder omits `role_template`, records
`rt.content.rstrip()` or `.resolve()`s the path (the fixture reaches the
template through a symlinked directory so `.resolve()` is observable). AC4 red
if a builder adds a separator. AC5 red if any of the dispatches loses
`injections=` or passes a literal. AC6 red if the clarify/architect dispatch
drops `injections=`. AC7 red if a missing template yields a block with an empty
`source_id`. AC8 red if a retry passes `()` or the first dispatch's extra_data
does not carry the record forward.

Fixtures are real: temp git repo + temp scratchpad, the real loader, the real
chokepoint (`invoke_llm_subprocess`, `_injection_refusal`, attestation). Only
the backend adapter (AC6/AC7) and the `invoke_llm_subprocess` module attribute
(AC8, per spec) are doubled. No singleton-resource timing is involved.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.conformance.attest import InjectedBlock
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.role_template import RoleTemplate

ROLE_FILE_TEXT = "ROLE TEMPLATE LINE 1\nROLE TEMPLATE LINE 2\n   \n"
ROLE_CONTENT = ROLE_FILE_TEXT.rstrip() + "\n\n"
EVENT_TYPE = "model_invocation_attested"


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def sha256_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mod(name: str):
    return importlib.import_module(f"bytedigger_engine.workflows.{name}")


def _common():
    return _mod("phase_workflows_common")


def _helper(name: str):
    """Lazy lookup so collection succeeds on the base commit and the test fails
    at assertion time with a precise message."""
    fn = getattr(_common(), name, None)
    assert fn is not None, f"phase_workflows_common.{name} is not defined (spec 2.1)"
    return fn


def _workflows_dir() -> Path:
    spec = importlib.util.find_spec("bytedigger_engine.workflows")
    assert spec is not None and spec.submodule_search_locations
    return Path(list(spec.submodule_search_locations)[0])


def _make_role_file(root: Path) -> tuple[Path, str]:
    """Role file reached through a symlinked directory, so a `.resolve()` in
    production would change `source_id`. Returns (path, expected source_id)."""
    real = root / "real_roles"
    real.mkdir(parents=True)
    (real / "role.md").write_text(ROLE_FILE_TEXT, encoding="utf-8")
    link = root / "linked_roles"
    os.symlink(real, link)
    role_path = link / "role.md"
    return role_path, str(Path(str(role_path)).expanduser())


def _prev(**data) -> StepResult:
    return StepResult(status="ok", data=dict(data), duration_ms=0, step_name="prev")


_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True, env=_GIT_ENV,
    )
    return out.stdout.strip()


class _Env:
    """One minimal real fixture: a git repo holding a scratchpad directory."""

    def __init__(self, root: Path, role_path: "Path | None") -> None:
        self.repo = root / "repo"
        self.repo.mkdir(parents=True)
        self.scratch = self.repo / "scratch"
        _git(self.repo, "init", "-q")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "base")
        self.base_sha = _git(self.repo, "rev-parse", "HEAD")
        self.scratch.mkdir()
        self.cfg: dict = {
            "scratchpad_dir": str(self.scratch),
            "model": "sonnet",
            "current_worktree_path": str(self.repo),
            "git_cwd": str(self.repo),
        }
        if role_path is not None:
            self.cfg["role_template_path"] = str(role_path)

    def ctx(self, **extra) -> WorkflowContext:
        return WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={**self.cfg, **extra},
            question="bd141 p4d feature request",
            session_id="bd141-p4d", persona="hal", framework=None, domain=None,
        )

    def dirty_a_txt(self) -> None:
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")

    def commit_a_txt(self) -> str:
        self.dirty_a_txt()
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "fix")
        return _git(self.repo, "rev-parse", "HEAD")

    def path(self, rel: str) -> str:
        return str(self.scratch / rel)


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """Backend registry and telemetry slot are process-wide singletons: pre-stage
    a known baseline before the body and restore after (workflows.md 1i)."""
    noop = lambda *a, **kw: None  # noqa: E731
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", noop)
    for modname in (
        "bytedigger_engine.lib.git_cwd",
        "bytedigger_engine.lib.project_root",
        "bytedigger_engine.lib.observability.emit_resolver",
    ):
        try:
            monkeypatch.setattr(importlib.import_module(modname), "emit_resolver_resolved", noop, raising=False)
        except ImportError:
            pass
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


# ---------------------------------------------------------------------------
# AC1 - helpers
# ---------------------------------------------------------------------------

def test_ac1_role_template_record() -> None:
    record = _helper("_role_template_record")
    assert record(None) is None
    rt = RoleTemplate(source_id="/some/where/role.md", content="BODY\n\n")
    assert record(rt) == {"source_id": "/some/where/role.md", "content": "BODY\n\n"}


def test_ac1_declared_injections() -> None:
    declared = _helper("_declared_injections")
    for empty in (None, "role_template", ["role_template"], 7, {}, {"role_template": None}, {"role_template": {}}):
        got = declared(empty)
        assert got == (), f"_declared_injections({empty!r}) must be (), got {got!r}"
        assert isinstance(got, tuple)
    got = declared({"role_template": {"source_id": "/r/role.md", "content": "C\n\n"}, "prompt": "x"})
    assert got == (InjectedBlock(source_id="/r/role.md", content="C\n\n"),)
    assert isinstance(got, tuple)


def test_ac1_role_template_loads_through_real_loader(tmp_path) -> None:
    """`_role_template` goes through the real `load_role_template`: the spelling
    (expanduser, rstrip + two newlines) is the loader's, not re-derived."""
    role_template = _helper("_role_template")
    role_path, source_id = _make_role_file(tmp_path)
    with_role = _Env(tmp_path / "with", role_path).ctx()
    without_role = _Env(tmp_path / "without", None).ctx()
    got = role_template(with_role)
    assert got == RoleTemplate(source_id=source_id, content=ROLE_CONTENT)
    assert role_template(without_role) is None


# ---------------------------------------------------------------------------
# AC2 - no bare-string helper left
# ---------------------------------------------------------------------------

def test_ac2_builders_use_attributable_reader(tmp_path) -> None:
    offenders: list[str] = []
    for path in sorted(_workflows_dir().glob("*.py")):
        if path.name == "phase_workflows_common.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            hit = (
                (isinstance(node, ast.Name) and node.id == "_maybe_role_template")
                or (isinstance(node, ast.Attribute) and node.attr == "_maybe_role_template")
                or (isinstance(node, ast.ImportFrom)
                    and any(a.name == "_maybe_role_template" for a in node.names))
            )
            if hit:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"_maybe_role_template still used outside common at: {offenders}"

    common = _common()
    legacy = getattr(common, "_maybe_role_template", None)
    assert legacy is not None, "_maybe_role_template must stay defined in phase_workflows_common"
    role_template = _helper("_role_template")
    role_path, _ = _make_role_file(tmp_path)
    for name, rp in (("with", role_path), ("without", None)):
        ctx = _Env(tmp_path / name, rp).ctx()
        rt = role_template(ctx)
        assert legacy(ctx) == (rt.content if rt else "")


# ---------------------------------------------------------------------------
# AC3 / AC4 - the 16 builders, driven for real
# ---------------------------------------------------------------------------

def _d_phase_1(env):
    return _mod("phase_1_discovery")._build_discovery_prompt(env.ctx(), _prev(skipped=False))


def _d_phase_3(env):
    return _mod("phase_3_clarify")._build_clarify_prompt(env.ctx(), _prev(skipped=False))


def _d_phase_4(env):
    return _mod("phase_4_architect")._build_architect_prompt(env.ctx(), _prev(skipped=False))


def _d_spec_lite_review(env):
    return _mod("phase_45_spec_lite")._build_review_prompt(
        env.ctx(), _prev(cycle=1, spec_path=env.path("spec.md")))


def _d_spec_writer(env):
    return _mod("phase_45_spec")._build_spec_prompt(env.ctx(), _prev())


def _d_spec_review(env):
    return _mod("phase_45_spec")._build_review_prompt(
        env.ctx(), _prev(cycle=1, spec_path=env.path("spec.md")))


def _d_red(env):
    return _mod("phase_5_implement")._build_red_prompt(env.ctx(), None)


def _d_validation(env):
    return _mod("phase_5_implement")._build_validation_prompt(
        env.ctx(), _prev(red_log_path=env.path("red.md"), spec_path=env.path("spec.md")))


def _d_green(env):
    return _mod("phase_5_implement")._build_green_prompt(
        env.ctx(), _prev(spec_path=env.path("spec.md"), red_log_path=env.path("red.md"),
                         validation_doc_path=env.path("validation.md")))


def _d_integrity(env):
    env.dirty_a_txt()
    return _mod("phase_5_integrity")._build_integrity_prompt(
        env.ctx(pre_red_ref=env.base_sha, diff_patterns=["*.txt"]), None)


def _d_review(env):
    ref = env.scratch / "integrity" / "pre-red-ref.txt"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(env.base_sha, encoding="utf-8")
    return _mod("phase_6_review")._build_review_prompt(env.ctx(), None)


def _d_fix(env):
    review_doc = Path(env.path("review.md"))
    review_doc.write_text("# Review\n\n## Aggregated Findings\n\n", encoding="utf-8")
    return _mod("phase_6_review")._build_fix_prompt(
        env.ctx(), _prev(spec_path=env.path("spec.md"), review_doc_path=str(review_doc),
                         verdict="CHANGES_REQUESTED"))


def _d_satisfaction(env):
    return _mod("phase_6_review")._build_satisfaction_prompt(
        env.ctx(), _prev(spec_path=env.path("spec.md"), review_doc_path=env.path("review.md"),
                         fix_doc_path=env.path("fix.md")))


def _d_decorr(env):
    return _mod("phase_6_review")._build_decorr_prompt(env.ctx(), _prev())


def _d_fix_integrity(env):
    fix_sha = env.commit_a_txt()
    return _mod("phase_6_fix_integrity")._build_fix_integrity_prompt(
        env.ctx(pre_fix_sha=env.base_sha, fix_commit_sha=fix_sha, diff_patterns=["*.txt"]), None)


def _d_synthesizer(env):
    return _mod("phase_7_synthesize")._build_synthesizer_prompt(env.ctx(), None)


_DRIVERS = {
    "phase_1_discovery": _d_phase_1,
    "phase_3_clarify": _d_phase_3,
    "phase_4_architect": _d_phase_4,
    "phase_45_spec_lite_review": _d_spec_lite_review,
    "phase_45_spec_writer": _d_spec_writer,
    "phase_45_spec_review": _d_spec_review,
    "phase_5_red": _d_red,
    "phase_5_validation": _d_validation,
    "phase_5_green": _d_green,
    "phase_5_integrity": _d_integrity,
    "phase_6_review": _d_review,
    "phase_6_fix": _d_fix,
    "phase_6_satisfaction": _d_satisfaction,
    "phase_6_decorr": _d_decorr,
    "phase_6_fix_integrity": _d_fix_integrity,
    "phase_7_synthesizer": _d_synthesizer,
}
assert len(_DRIVERS) == 16


def _drive(name: str, root: Path, role_path: "Path | None") -> StepResult:
    result = _DRIVERS[name](_Env(root, role_path))
    assert isinstance(result, StepResult)
    assert result.status == "ok" and isinstance(result.data, dict) and result.data.get("prompt"), (
        f"fixture precondition ({name}): builder must return an ok prompt, got "
        f"status={result.status!r} error_code={result.error_code!r} error={result.error!r}"
    )
    return result


@pytest.mark.parametrize("name", sorted(_DRIVERS))
def test_ac3_every_builder_records_role_template_verbatim(name, tmp_path) -> None:
    role_path, source_id = _make_role_file(tmp_path)

    with_role = _drive(name, tmp_path / "with", role_path).data
    assert with_role.get("role_template") == {"source_id": source_id, "content": ROLE_CONTENT}, (
        f"{name}: data['role_template'] must be the loader's record "
        f"{{'source_id': str(Path(p).expanduser()), 'content': text.rstrip() + '\\n\\n'}}; "
        f"got {with_role.get('role_template')!r}"
    )
    assert with_role["role_template"]["content"] in with_role["prompt"], (
        f"{name}: the declared content must occur verbatim in the prompt"
    )

    without_role = _drive(name, tmp_path / "without", None).data
    assert without_role.get("role_template") is None, (
        f"{name}: without role_template_path the record must be absent/None, "
        f"got {without_role.get('role_template')!r}"
    )


@pytest.mark.parametrize("name", sorted(_DRIVERS))
def test_ac4_prompt_bytes_unchanged(name, tmp_path) -> None:
    """Guard (green today, must stay green): the template already sits verbatim
    in the prompt, with no extra separator after it."""
    role_path, _ = _make_role_file(tmp_path)
    prompt = _drive(name, tmp_path / "with", role_path).data["prompt"]
    assert ROLE_CONTENT in prompt
    assert ROLE_CONTENT + "\n" not in prompt, f"{name}: extra separator after the role template"


# ---------------------------------------------------------------------------
# AC5 - structural: every dispatch declares
# ---------------------------------------------------------------------------

_INVOKE = "invoke_llm_subprocess"
_DECLARE_HELPERS = {"_declared_injections", "_role_template_injections"}
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _parents(tree: ast.AST) -> dict:
    out: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _scope_chain(node, parents) -> list:
    chain = []
    while node in parents:
        node = parents[node]
        if isinstance(node, _FUNCS):
            chain.append(node)
    return chain  # innermost first


def _is_invoke_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == _INVOKE) or (isinstance(f, ast.Attribute) and f.attr == _INVOKE)


def _declaring_value(expr) -> bool:
    """`injections=` value must come from a declare helper (or a name bound to
    one); a literal `()`/`None`/`[]` does not declare anything."""
    if isinstance(expr, ast.Call):
        f = expr.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        return name in _DECLARE_HELPERS
    return isinstance(expr, (ast.Name, ast.Attribute))


def _dict_expr_declares(expr) -> bool:
    if isinstance(expr, ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value == "injections" and _declaring_value(v)
                   for k, v in zip(expr.keys, expr.values))
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "dict":
        return any(kw.arg == "injections" and _declaring_value(kw.value) for kw in expr.keywords)
    return False


def _name_declares(name: str, fn, tree, depth: int = 0) -> bool:
    """Does the dict bound to `name` inside `fn` carry an `injections` key?"""
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name and _dict_expr_declares(n.value):
                    return True
                if (isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == name and isinstance(tgt.slice, ast.Constant)
                        and tgt.slice.value == "injections" and _declaring_value(n.value)):
                    return True
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update"
              and isinstance(n.func.value, ast.Name) and n.func.value.id == name):
            if any(kw.arg == "injections" and _declaring_value(kw.value) for kw in n.keywords):
                return True
            if any(_dict_expr_declares(a) for a in n.args):
                return True
    params = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
    if name in params and depth < 2:
        return _param_declares(fn, name, params, tree, depth + 1)
    return False


def _param_declares(fn, name: str, params: list, tree, depth: int) -> bool:
    """`name` is a parameter of `fn`: every in-module caller must pass a dict that declares."""
    parents = _parents(tree)
    callers = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == fn.name]
    if not callers:
        return False
    idx = params.index(name)
    for call in callers:
        arg = next((kw.value for kw in call.keywords if kw.arg == name), None)
        if arg is None and idx < len(call.args):
            arg = call.args[idx]
        if arg is None:
            return False
        if _dict_expr_declares(arg):
            continue
        if isinstance(arg, ast.Name):
            owners = _scope_chain(call, parents)
            if not any(_name_declares(arg.id, o, tree, depth) for o in owners):
                return False
            continue
        return False
    return True


def _call_declares(call: ast.Call, scopes: list, tree) -> bool:
    for kw in call.keywords:
        if kw.arg == "injections":
            return _declaring_value(kw.value)
    for kw in call.keywords:
        if kw.arg is None:  # **expanded
            if _dict_expr_declares(kw.value):
                return True
            if isinstance(kw.value, ast.Name) and any(
                    _name_declares(kw.value.id, s, tree) for s in scopes):
                return True
    return False


def _scan_dispatches() -> list:
    """-> [(file, lineno, declared)] for every direct `invoke_llm_subprocess(...)` call."""
    rows = []
    for path in sorted(_workflows_dir().glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parents(tree)
        for node in ast.walk(tree):
            if _is_invoke_call(node):
                scopes = _scope_chain(node, parents)
                rows.append((path.name, node.lineno, _call_declares(node, scopes, tree)))
    return rows


def test_ac5_every_dispatch_declares_injections() -> None:
    offenders = [f"{f}:{ln}" for f, ln, ok in _scan_dispatches() if not ok]
    assert not offenders, (
        f"{len(offenders)} invoke_llm_subprocess call(s) pass no `injections` "
        f"(directly or through `**dict`): {offenders}"
    )


def test_ac5_dispatch_call_count_is_pinned() -> None:
    """Guard: 20 in spec 1.4 + phase_2_explore:379. A new call site is a visible change."""
    rows = _scan_dispatches()
    assert len(rows) == 21, f"direct dispatch count changed: {len(rows)} (expected 21): {rows}"


def test_ac5_dispatch_hidden_behind_executor_submit_declares() -> None:
    """A dispatch handed to `executor.submit(telemetry_ctx.run_with_current_run,
    ctx, invoke_llm_subprocess, prompt=..., ...)` is still a dispatch (the COMPLEX
    satisfaction fan-out, phase_6_review `_run_satisfaction_evaluators_parallel`)."""
    offenders = []
    for path in sorted(_workflows_dir().glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and not _is_invoke_call(node)
                    and any(isinstance(a, ast.Name) and a.id == _INVOKE for a in node.args)):
                declared = any(kw.arg == "injections" and _declaring_value(kw.value) for kw in node.keywords)
                if not declared:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"dispatch via a passed-in invoke_llm_subprocess declares no injections: {offenders}"


# ---------------------------------------------------------------------------
# AC6 / AC7 - the real side effect, through the real engine
# ---------------------------------------------------------------------------

class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def attests(self) -> list:
        return [p for (t, p, _) in self.events if t == EVENT_TYPE]

    def types(self) -> list:
        return [t for (t, _, _) in self.events]


class _RecordingAdapter:
    """Merges `extra_data` into the result exactly as the real backend does."""

    def __init__(self, raw_response: str) -> None:
        self.raw_response = raw_response
        self.calls: list = []

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        data = {"raw_response": self.raw_response, "worker_written_paths": [],
                "manifest_source": "harness_tool_record"}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0,
                          step_name=kwargs.get("step_name", "rec"))


def _register(adapter) -> None:
    llm_subprocess.register_backend(
        "claude-subprocess", adapter, manifest_source="harness_tool_record",
        capabilities=frozenset(("manifest", "progress_since", "abort")), overwrite=True,
    )


def _run_workflow(kind: str, env: _Env):
    if kind == "clarify":
        wf = _mod("phase_3_clarify").phase_3_clarify_workflow()
        raw = "clarified\n\nSTATUS: DONE\n"
    else:
        wf = _mod("phase_4_architect").phase_4_architect_workflow()
        raw = "architecture\n\nSTATUS: DONE\n"
    _register(_RecordingAdapter(raw))
    log = _FakeEventLog()
    engine = WorkflowEngine(event_log=log)
    engine.register(kind, wf)
    result, _ = engine.execute(kind, env.ctx())
    assert result.status == "ok", (
        f"fixture precondition: the {kind} workflow must complete; got status={result.status!r} "
        f"error_code={result.error_code!r} error={result.error!r}"
    )
    return log


@pytest.mark.parametrize("kind", ["clarify", "architect"])
def test_ac6_attestation_carries_role_template(kind, tmp_path) -> None:
    role_path, source_id = _make_role_file(tmp_path)
    log = _run_workflow(kind, _Env(tmp_path / "run", role_path))
    attests = log.attests()
    assert len(attests) == 1, f"{kind}: expected one {EVENT_TYPE!r} event, got {len(attests)} ({sorted(set(log.types()))})"
    assert attests[0]["injections"] == [{"source_id": source_id, "sha256": sha256_of(ROLE_CONTENT)}], (
        f"{kind}: the role template must be attributed; got {attests[0]['injections']!r}"
    )


@pytest.mark.parametrize("kind", ["clarify", "architect"])
def test_ac7_no_template_no_declaration(kind, tmp_path) -> None:
    """Guard (green today, must stay green): no template -> no empty-source block."""
    log = _run_workflow(kind, _Env(tmp_path / "run", None))
    attests = log.attests()
    assert len(attests) == 1
    assert attests[0]["injections"] == []


# ---------------------------------------------------------------------------
# AC8 - retries declare what the first attempt declared
# ---------------------------------------------------------------------------

def _two_call_spy(calls: list):
    """First call: ok, no completion marker, extra_data merged as the real
    backend does. Second call (the retry): error, so the artifact step returns
    right after it and no downstream gate runs."""
    def spy(**kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            data = {"raw_response": "did some work but forgot the completion marker",
                    "worker_written_paths": [], **(kwargs.get("extra_data") or {})}
            return StepResult(status="ok", data=data, duration_ms=0, step_name=kwargs["step_name"])
        return StepResult(status="error", data=None, duration_ms=0, step_name=kwargs["step_name"],
                          error="spy retry stop", error_code="E_SPY_STOP")
    return spy


def _assert_retry_matches(calls: list, source_id: str, what: str) -> None:
    assert len(calls) == 2, f"fixture precondition ({what}): expected first dispatch + retry, got {len(calls)}"
    expected = (InjectedBlock(source_id=source_id, content=ROLE_CONTENT),)
    first = tuple(calls[0].get("injections") or ())
    retry = tuple(calls[1].get("injections") or ())
    assert first == expected, f"{what}: first dispatch must declare the role template, got {first!r}"
    assert retry == first, f"{what}: the retry must declare exactly what its first attempt did, got {retry!r}"


def test_ac8_green_retry_declares_same_injections(tmp_path, monkeypatch) -> None:
    p5 = _mod("phase_5_implement")
    role_path, source_id = _make_role_file(tmp_path)
    env = _Env(tmp_path / "run", role_path)
    ctx = env.ctx()
    built = p5._build_green_prompt(
        ctx, _prev(spec_path=env.path("spec.md"), red_log_path=env.path("red.md"),
                   validation_doc_path=env.path("validation.md")))
    assert built.status == "ok", f"fixture precondition: {built.error_code} {built.error}"
    calls: list = []
    monkeypatch.setattr(p5, "invoke_llm_subprocess", _two_call_spy(calls))
    first = p5._invoke_green_llm(ctx, built)
    assert first.status == "ok"
    p5._write_green_artifact(ctx, first)
    _assert_retry_matches(calls, source_id, "GREEN retry")


def test_ac8_fix_retry_declares_same_injections(tmp_path, monkeypatch) -> None:
    p6 = _mod("phase_6_review")
    role_path, source_id = _make_role_file(tmp_path)
    env = _Env(tmp_path / "run", role_path)
    ctx = env.ctx()
    review_doc = Path(env.path("review.md"))
    review_doc.write_text("# Review\n\n## Aggregated Findings\n\n", encoding="utf-8")
    built = p6._build_fix_prompt(
        ctx, _prev(spec_path=env.path("spec.md"), review_doc_path=str(review_doc),
                   verdict="CHANGES_REQUESTED"))
    assert built.status == "ok", f"fixture precondition: {built.error_code} {built.error}"
    calls: list = []
    monkeypatch.setattr(p6, "invoke_llm_subprocess", _two_call_spy(calls))
    first = p6._invoke_fix_llm(ctx, built)
    assert first.status == "ok"
    p6._write_fix_artifact(ctx, first)
    _assert_retry_matches(calls, source_id, "fix retry")


# ---------------------------------------------------------------------------
# AC9 - phase_2 stays on the one implementation
# ---------------------------------------------------------------------------

def test_ac9_phase_2_helper_equals_declared_injections() -> None:
    declared = _helper("_declared_injections")
    p2 = _mod("phase_2_explore")
    record = {"source_id": "/r/role.md", "content": "C\n\n"}
    for data in ({"role_template": record, "prompt": "p"}, {"role_template": None}, {}, None):
        prev = StepResult(status="ok", data=data, duration_ms=0, step_name="prev")
        assert p2._role_template_injections(prev) == declared(data), f"diverged for data={data!r}"
    assert p2._role_template_injections(_prev(role_template=record)) == (
        InjectedBlock(source_id="/r/role.md", content="C\n\n"),)
