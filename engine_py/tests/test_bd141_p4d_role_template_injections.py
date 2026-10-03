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
AC2  test_ac2_builders_use_attributable_reader (Call nodes only, incl. alias /
       getattr / globals()[...]; imports and re-exports allowed; helper kept
       in common and equals `_role_template(ctx).content or ""`)
AC3  test_ac3_every_builder_records_role_template_verbatim[<11 producers>]
       (the builders; the two `str` builders through their step wrappers)
AC4  test_ac4_prompt_bytes_unchanged[<15 producers>]           (guard: green today)
AC4b test_ac4b_restricted_writer_does_not_inherit_role_template (guard: green today)
     test_ac4b_cycle2_reviewer_inherits_role_template_like_cycle1 (bd#89 P3a)
AC5  test_ac5_every_dispatch_declares_injections
     test_ac5_dispatch_call_count_is_pinned                    (guard: green today)
     test_ac5_dispatch_hidden_behind_executor_submit_declares  (the COMPLEX
       satisfaction pool path hands `invoke_llm_subprocess` to `executor.submit`:
       it is the 20th dispatch, counted separately from the 19 direct calls)
AC5b test_ac5b_dispatch_declares_role_template_behaviourally[<12 cases>]
       (real producer output -> real invoke step -> module-attribute spy; covers
       the pool path with every submitted call, both phase_45_spec review
       dispatches, and phase_2's existing one as a guard; the
       two retries are the AC8 tests)
AC6  (retired, bd#89 P2a: clarify/architect deleted)
AC7  (retired, bd#89 P2a: clarify/architect deleted)
AC8  test_ac8_green_retry_declares_same_injections
     test_ac8_fix_retry_declares_same_injections
AC9  (retired, bd#89 P2a: phase_2_explore deleted)

Teeth (spec section 4): AC1 red if the record drops `source_id` or
`_declared_injections` raises on a non-dict. AC2 red if any builder keeps
calling the bare-string helper (directly, via alias, getattr or globals()).
AC3 red if one producer omits `role_template`, records
`rt.content.rstrip()` or `.resolve()`s the path (the fixture reaches the
template through a symlinked directory so `.resolve()` is observable). AC4 red
if a builder adds a separator. AC4b red if a role-less branch spreads
`prev.data` and inherits a stale record. AC5 red if any dispatch loses
`injections=` or passes a literal. AC5b red if a dispatch passes the StepResult
instead of its dict (`_declared_injections(prev)` -> `()`), passes `()`, or the
pool path leaves `injections` defaulted. AC6 red if the clarify/architect dispatch
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

_LEGACY = "_maybe_role_template"


def _legacy_aliases(tree: ast.AST) -> set:
    """Local names bound to the bare-string helper (import-as, `x = helper`)."""
    names = {_LEGACY}
    for _ in range(2):
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                names |= {a.asname for a in n.names if a.name in names and a.asname}
            elif isinstance(n, ast.Assign):
                v = n.value
                ref = v.id if isinstance(v, ast.Name) else v.attr if isinstance(v, ast.Attribute) else None
                if ref in names:
                    names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return names


def _legacy_call_sites(tree: ast.AST) -> list:
    """Line numbers where the bare-string helper is CALLED or looked up by
    string. Plain imports / re-exports / monkeypatch-by-name are not calls."""
    aliases = _legacy_aliases(tree)
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id in aliases) or (
                    isinstance(f, ast.Attribute) and f.attr in aliases):
                hits.append(n.lineno)
            elif any(isinstance(a, ast.Constant) and a.value == _LEGACY for a in n.args):
                hits.append(n.lineno)  # getattr(m, "_maybe_role_template"), d.get(...), importlib
        elif (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                and n.slice.value == _LEGACY):
            hits.append(n.lineno)  # globals()["_maybe_role_template"]
    return hits


def test_ac2_builders_use_attributable_reader(tmp_path) -> None:
    offenders: list[str] = []
    for path in sorted(_workflows_dir().glob("*.py")):
        if path.name == "phase_workflows_common.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.name}:{ln}" for ln in _legacy_call_sites(tree)]
    assert not offenders, f"_maybe_role_template still called outside common at: {offenders}"

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

def _write_cycle1_review(env, text: str) -> None:
    rel = _mod("phase_45_spec")._review_cycle_relpath(1)
    target = env.scratch / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


_STRUCTURED_REVIEW = (
    "# Review\n\n## Findings (structured)\n```json\n"
    '[{"id": "F1", "type": "gap", "evidence": "e", "required_action": "a"}]\n'
    "```\n"
)

_STRUCTURED_FINDINGS = [{"id": "F1", "type": "gap", "evidence": "e", "required_action": "a"}]


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


def _d_fix_integrity(env):
    fix_sha = env.commit_a_txt()
    return _mod("phase_6_fix_integrity")._build_fix_integrity_prompt(
        env.ctx(pre_fix_sha=env.base_sha, fix_commit_sha=fix_sha, diff_patterns=["*.txt"]), None)


_DRIVERS = {
    "phase_45_spec_writer": _d_spec_writer,
    "phase_45_spec_review": _d_spec_review,
    "phase_5_red": _d_red,
    "phase_5_validation": _d_validation,
    "phase_5_green": _d_green,
    "phase_5_integrity": _d_integrity,
    "phase_6_review": _d_review,
    "phase_6_fix": _d_fix,
    "phase_6_satisfaction": _d_satisfaction,
    "phase_6_fix_integrity": _d_fix_integrity,
}
assert len(_DRIVERS) == 10


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
    """Guard: 15 after bd#89 P2a (phases 1-4 dropped: 4 sites removed, + the dropped phase_2_explore).
    bd#91: −1 (phase_45_spec spec-review re-poll removed).
    bd#89 P3c: −1 (phase_7_synthesize synthesizer dispatch removed).
    bd#89 P3b2: −1 (the decorrelated-verifier dispatch removed).
    A new call site is a visible change."""
    rows = _scan_dispatches()
    assert len(rows) == 12, f"direct dispatch count changed: {len(rows)} (expected 12): {rows}"


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
# AC4b - role-less branches never inherit a stale record
# ---------------------------------------------------------------------------

class _InvokeSpy:
    """Module-attribute spy for `invoke_llm_subprocess`: records kwargs, returns a
    canned ok StepResult whose data merges `extra_data` like the real backend."""

    def __init__(self, raw: str = "canned response\n") -> None:
        self.raw = raw
        self.calls: list = []

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        data = {"raw_response": self.raw, "worker_written_paths": []}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0,
                          step_name=kwargs.get("step_name", "spy"))


def _stale_record() -> dict:
    return {"source_id": "/stale/earlier-step/role.md", "content": ROLE_CONTENT}


def test_ac4b_restricted_writer_does_not_inherit_role_template(tmp_path) -> None:
    """Guard (green today): the phase_45_spec cycle-2 revise writer (delta
    branch, taken whenever structured findings are threaded) carries no
    role, so its data must not carry the record a previous step left in
    `prev.data`. Re-pointed from the dropped SIMPLE-only writer by bd#89 P2b."""
    role_path, _ = _make_role_file(tmp_path)
    env = _Env(tmp_path / "run", role_path)
    spec_file = env.scratch / _mod("phase_45_spec").SPEC_DOC_RELPATH
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("## Context\nbase spec body\n", encoding="utf-8")
    res = _mod("phase_45_spec")._build_spec_prompt(
        env.ctx(), _prev(cycle=2, findings="f", structured_findings=_STRUCTURED_FINDINGS,
                         role_template=_stale_record()))
    assert res.status == "ok" and res.data.get("delta_retry") is True, "fixture precondition"
    assert res.data.get("role_template") is None
    assert ROLE_CONTENT not in res.data["prompt"]


def test_ac4b_cycle2_reviewer_inherits_role_template_like_cycle1(tmp_path, monkeypatch) -> None:
    """bd#89 P3a: phase_45_spec `_build_review_prompt` cycle 2 with structured
    findings is the cycle-1 reviewer prompt (the restricted reviewer is dropped),
    so it carries the configured role template in its data and its dispatch."""
    mod = _mod("phase_45_spec")
    role_path, source_id = _make_role_file(tmp_path)
    env = _Env(tmp_path / "run", role_path)
    _write_cycle1_review(env, _STRUCTURED_REVIEW)
    res = mod._build_review_prompt(
        env.ctx(), _prev(cycle=2, spec_path=env.path("spec.md"), role_template=_stale_record()))
    assert res.status == "ok", "fixture precondition"
    assert res.data.get("role_template") == {"source_id": source_id, "content": ROLE_CONTENT}
    assert ROLE_CONTENT in res.data["prompt"]
    spy = _InvokeSpy()
    monkeypatch.setattr(mod, "invoke_llm_subprocess", spy)
    mod._invoke_review_llm(env.ctx(), res)
    assert len(spy.calls) >= 1
    for call in spy.calls:
        injections = tuple(call.get("injections") or ())
        assert injections and injections[0].source_id == source_id


# ---------------------------------------------------------------------------
# AC5b - behavioural dispatch matrix: producer output -> real invoke step -> spy
# ---------------------------------------------------------------------------
# A structural scan (AC5) cannot tell `injections=_declared_injections(prev.data)`
# from `_declared_injections(prev)` (a StepResult -> `()`), nor a defaulted pool
# parameter from a wired one. Here each dispatch is reached through its real
# producer and real invoke step; only `invoke_llm_subprocess` (the module
# attribute the step resolves at call time) is replaced by a recording spy.

def _d_satisfaction_complex(env):
    return _mod("phase_6_review")._build_satisfaction_prompt(
        env.ctx(complexity="COMPLEX"),
        _prev(spec_path=env.path("spec.md"), review_doc_path=env.path("review.md"),
              fix_doc_path=env.path("fix.md")))


_SHIP = "## Verdict\n\nSHIP\n"
_REVISE = "## Verdict\n\nREVISE\n"

# bd#150 AC13a: the fragment blocks (spec 2.1) each call site declares besides the role template.
# F1 = evaluator fragment, F2 = producer fragment, F3 = secure-codegen fragment.
_F1, _F2, _F3 = "F1", "F2", "F3"


def _real_fragment_files() -> dict:
    """key -> (expected source_id or None when it is the resolved default path, real file)."""
    pkg = _workflows_dir().parent
    ah = pkg / "lib" / "plugins" / "anti_hallucination"
    return {
        _F1: ("bytedigger_engine/lib/plugins/anti_hallucination/prompt_fragment.md",
              ah / "prompt_fragment.md"),
        _F2: ("bytedigger_engine/lib/plugins/anti_hallucination/producer_prompt_fragment.md",
              ah / "producer_prompt_fragment.md"),
        _F3: (None, pkg / "security" / "secure-codegen-fragment.md"),
    }


def _assert_declares_role_plus_fragments(declared: tuple, source_id: str, frag_keys: tuple, what: str) -> None:
    """Exactly one role template (first), then exactly the named fragment blocks in order,
    each matched by source_id and by sha256 of the real fragment file. Nothing else."""
    declared = tuple(declared)
    assert len(declared) == 1 + len(frag_keys), (
        f"{what}: expected role template + {list(frag_keys)}, declared={declared!r}")
    assert declared[0] == InjectedBlock(source_id=source_id, content=ROLE_CONTENT), (
        f"{what}: first declared block must be the role template, got {declared[0]!r}")
    assert sum(1 for b in declared if b.source_id == source_id) == 1, (
        f"{what}: role template must be declared exactly once")
    files = _real_fragment_files()
    for blk, key in zip(declared[1:], frag_keys):
        want_id, real = files[key]
        real_text = real.read_text(encoding="utf-8")
        if want_id is None:
            assert Path(blk.source_id).name == real.name, f"{what}: {key} source_id {blk.source_id!r}"
        else:
            assert blk.source_id == want_id, f"{what}: {key} source_id {blk.source_id!r} != {want_id!r}"
        assert hashlib.sha256(blk.content.encode("utf-8")).hexdigest() == \
            hashlib.sha256(real_text.encode("utf-8")).hexdigest(), (
            f"{what}: {key} content is not the real fragment file {real}")


# case -> (build, module, invoke step, ctx extras, spy raw, min calls, stub ensure_graph, fragments declared)
_MATRIX = {
    "phase_45_spec:1357": (_d_spec_writer, "phase_45_spec", "_invoke_spec_llm", {}, "x", 1, False, ()),
    "phase_45_spec:4201": (_d_spec_review, "phase_45_spec", "_invoke_review_llm", {}, _SHIP, 1, False, (_F1,)),
    "phase_5_implement:1553": (_d_red, "phase_5_implement", "_invoke_red_llm", {}, "x", 1, False, (_F2, _F3)),
    "phase_5_implement:6664": (_d_validation, "phase_5_implement", "_invoke_validation_llm", {}, "x", 1, False, (_F1,)),
    "phase_5_implement:7556": (_d_green, "phase_5_implement", "_invoke_green_llm", {}, "x", 1, False, (_F2, _F3)),
    "phase_5_integrity:424": (_d_integrity, "phase_5_integrity", "_invoke_integrity_llm", {}, "x", 1, False, (_F1,)),
    "phase_6_review:1119": (_d_review, "phase_6_review", "_invoke_review_llm", {}, "x", 1, False, (_F1,)),
    "phase_6_review:2550": (_d_fix, "phase_6_review", "_invoke_fix_llm", {}, "x", 1, False, ()),
    "phase_6_review:3249": (_d_satisfaction, "phase_6_review", "_invoke_satisfaction_llm", {}, "x", 1, False, (_F1,)),
    "phase_6_review:3044-pool": (_d_satisfaction_complex, "phase_6_review", "_invoke_satisfaction_llm", {"complexity": "COMPLEX"}, "x", 3, False, (_F1,)),
    "phase_6_fix_integrity:600": (_d_fix_integrity, "phase_6_fix_integrity", "_invoke_fix_integrity_llm", {}, "x", 1, False, (_F1,)),
}


@pytest.mark.parametrize("case", sorted(_MATRIX))
def test_ac5b_dispatch_declares_role_template_behaviourally(case, tmp_path, monkeypatch) -> None:
    build, modname, invoke_name, ctx_extra, raw, min_calls, stub_graph, frag_keys = _MATRIX[case]
    role_path, source_id = _make_role_file(tmp_path)
    env = _Env(tmp_path / "run", role_path)
    built = build(env)
    assert isinstance(built, StepResult) and built.status == "ok" and isinstance(built.data, dict) \
        and built.data.get("prompt"), (
        f"fixture precondition ({case}): producer must return an ok prompt, got "
        f"status={getattr(built, 'status', None)!r} error_code={getattr(built, 'error_code', None)!r}")
    mod = _mod(modname)
    spy = _InvokeSpy(raw)
    monkeypatch.setattr(mod, "invoke_llm_subprocess", spy)
    getattr(mod, invoke_name)(env.ctx(**ctx_extra), built)
    assert len(spy.calls) >= min_calls, (
        f"{case}: expected >= {min_calls} dispatch(es), the invoke step made {len(spy.calls)}")
    got = [tuple(c.get("injections") or ()) for c in spy.calls]
    for i, g in enumerate(got):
        _assert_declares_role_plus_fragments(g, source_id, frag_keys, f"{case} dispatch {i}")


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


# AC6/AC7 (clarify/architect attestation through the real engine) retired by
# bd#89 P2a: phase_3_clarify / phase_4_architect are deleted.


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


def _assert_retry_matches(calls: list, source_id: str, what: str, frag_keys: tuple = ()) -> None:
    assert len(calls) == 2, f"fixture precondition ({what}): expected first dispatch + retry, got {len(calls)}"
    first = tuple(calls[0].get("injections") or ())
    retry = tuple(calls[1].get("injections") or ())
    _assert_declares_role_plus_fragments(first, source_id, frag_keys, f"{what} first dispatch")
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
    _assert_retry_matches(calls, source_id, "GREEN retry", (_F2, _F3))


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


# AC9 (phase_2 helper == _declared_injections) retired by bd#89 P2a: phase_2_explore is deleted.
