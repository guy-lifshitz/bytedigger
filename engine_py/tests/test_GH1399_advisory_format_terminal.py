"""RED tests for GH1399 — deterministic review-format recovery is selected by
backend IDENTITY today, not by the PROPERTY that makes it safe to apply, and
`phase_5_integrity`'s re-ask loop must never degrade into verdict synthesis.

Spec: SHARED/memory/Decisions/2026-07-29_GH1399_advisory_format_failure_terminal_spec.md

Chokepoint under test: the non-conformant branch in
`phase_6_review._write_review_artifact` (workflows/phase_6_review.py:2028-2035),
which today reads `_backend = _resolve_backend(None, os.environ)[0]` and
compares it to the string literal `"claude-in-session"` to decide whether to
apply the already-total, already-idempotent `_normalize_to_aggregated_findings`
helper (phase_6_review.py:1873-1884) or fall through to a paid LLM retry that
terminates in `E_REVIEW_FORMAT_DRIFT` (recoverable=False).

★ §1d DISCREPANCY WITH THE FROZEN SPEC — MEASURED, NOT INFERRED.

The spec (§"ДВА ЭКЗЕМПЛЯРА" and AC5) states that in `phase_5_integrity`
"there is NO retry at all (:557-566 goes to recoverable=False without a single
attempt)". That is FALSE as of this commit. Lines 557-566 are the *classify*
step, which sits DOWNSTREAM of `_invoke_integrity_llm`, and that step already
carries GH786's bounded completeness re-ask (phase_5_integrity.py:468-490,
budget from `_resolve_integrity_verdict_retries`, default 1, env
HAL_INTEGRITY_VERDICT_RETRY_MAX). The spec measured the wrong function.

Consequence for the AC table: AC5's stated reddening condition ("a refusal without
a single attempt — today's behaviour") does not hold, so AC5 is GREEN at
HEAD and is carried here as a SHIELD, not as a RED. It is NOT "verified" in
the sense of a change proven — it pins pre-existing behaviour. The cheap fix
the spec expected to find in phase_5_integrity was already shipped by GH786;
what remains open there is only the prohibition side (AC6/AC7).

A naive "normalize both call-sites" implementation of GH1399 could still
synthesize a VERDICT there, which would be a safety regression (§1l / the
spec's "ЗАПРЕТ" section). AC5-AC8 pin that this stays correct.

Per §1q: UUTs are imported INSIDE each test body (local `import phase_6_review
as p6` / `import phase_5_integrity as p5`), never at module top level. No
`sys.path` mutation here — conftest.py's conftest-import-time singleton
(engine_py root + workflows dir) already covers this per 81F97F3D. Fixture
helpers (`_make_ctx`, `_prev_nonconformant`, `_mock_invoke_ok_raw`) are
re-implemented locally in this shape and style, mirroring
`test_F7830037_insession_review_normalize.py` and
`test_GH786_integrity_completeness_gate.py` — not imported across test files.

§1i: no singleton/time-dependent resource under test — `invoke_llm_subprocess`
and `_emit_safe` are monkeypatched deterministically (plain call counters /
canned responses), never raced against real timing or a real subprocess.

Labeling (per AC, in each test's docstring) — measured, `pytest -q` on HEAD
7aeb5b8 gives exactly `5 failed, 7 passed`:
  RED    — fails at ASSERT time on current HEAD (§1q):
           AC1, AC2a, AC2b, AC4, AC9
  SHIELD — green BEFORE the change; pins PRODUCTION behaviour GREEN must not
           break, and is therefore NOT evidence that anything was verified:
           AC3, AC5, AC6, AC7, AC8, and
           test_1n_closed_error_code_set_with_remedy_per_code.
           Every shield here carries its own liveness proof — a mutant
           fixture reproducing the violation it claims to catch — because a
           guard that cannot be shown to fire is indistinguishable from one
           with nothing to check.
  LIVENESS — green, and touches NO production file at all: it exercises this
           module's own extractor against a mutant fixture to prove the
           closure scan can return a non-empty answer. Labelled apart from
           SHIELD on purpose (gate r1 MINOR-2): a shield pins prod, a
           liveness test pins the instrument. Filing them under one label
           would let "7 shields" read as "7 pins on production" when one of
           them cannot fail for any production reason:
           test_1n_registry_scan_is_not_blind.
           AC2b additionally carries in-test liveness fixtures for FIVE
           violation shapes (equality, membership, call+subscript,
           call-through-attribute, call+subscript membership) and THREE
           negative controls — the call forms were added after gate r1
           MAJOR-1 showed the ast.Name-only predicate was blind to
           `_resolve_backend(None, os.environ)[0] == "…"`, an idiom already
           written at phase_6_review.py:975.

★ AC2a and AC8a are in tension by design, and the tension is the spec's:
AC2a demands that EVERY backend recover deterministically, which makes the
paid-retry branch (and hence `E_REVIEW_FORMAT_DRIFT` from it) UNREACHABLE
after a correct GREEN. AC8 is therefore written so it does not depend on
that branch surviving — see its docstring. A GREEN that leaves the branch
reachable for some backend fails AC2a.
"""
from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402

ENGINE_ROOT = Path(__file__).parent.parent
PHASE_6_REVIEW_PATH = ENGINE_ROOT / "bytedigger_engine/workflows" / "phase_6_review.py"
PHASE_5_INTEGRITY_PATH = ENGINE_ROOT / "bytedigger_engine/workflows" / "phase_5_integrity.py"


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixture helpers (phase_6_review side) — mirrors
# test_F7830037_insession_review_normalize.py's shapes, re-implemented locally.
# ═══════════════════════════════════════════════════════════════════════════


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="GH1399 — property-based deterministic format recovery",
        session_id="test-GH1399",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_spec(scratchpad: Path) -> Path:
    spec = scratchpad / "specs/build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## AC1\nGH1399 fixture spec.\n", encoding="utf-8")
    return spec


def _seed_logs(scratchpad: Path):
    red_log = scratchpad / "tests/build-red-output.log"
    red_log.parent.mkdir(parents=True, exist_ok=True)
    red_log.write_text("RED COMPLETE\n", encoding="utf-8")
    green_log = scratchpad / "tests/build-green-output.log"
    green_log.write_text("GREEN COMPLETE\n", encoding="utf-8")
    return red_log, green_log


def _prev_nonconformant(tmp_path: Path, raw_response: str | None = None) -> tuple[StepResult, Path]:
    """Build a non-conformant prev StepResult (no aggregated_content, no doc_path file).

    Returns (prev, doc_path).
    """
    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    doc_path = reviews_dir / "build-review.md"
    # doc_path intentionally NOT written — file absent

    spec_doc = _seed_spec(scratchpad)
    red_log, green_log = _seed_logs(scratchpad)

    if raw_response is None:
        raw_response = "summary only, no header\nVERDICT: PASS"

    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "prompt": "review the implementation",
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )
    return prev, doc_path


def _mock_invoke_ok_raw(raw_response: str = "OK") -> MagicMock:
    """Mock that returns a successful StepResult with a given raw_response."""
    return MagicMock(return_value=StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "response_bytes": len(raw_response),
            "command": ["x"],
        },
        duration_ms=0,
        step_name="mock_invoke",
    ))


def _patch_emit(monkeypatch, module) -> list[tuple]:
    """Capture every _emit_safe call on `module`; return list of (event_type, payload)."""
    captured: list[tuple] = []

    def _capture(event_type, payload, **kw):
        captured.append((event_type, payload))

    monkeypatch.setattr(module, "_emit_safe", _capture)
    return captured


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixture helpers (phase_5_integrity side) — mirrors
# test_GH786_integrity_completeness_gate.py's shapes, re-implemented locally.
# ═══════════════════════════════════════════════════════════════════════════


def _make_integrity_ctx(scratchpad: Path) -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    fake_worktree = scratchpad.parent / "fake_worktree"
    fake_worktree.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "current_worktree_path": str(fake_worktree),
        },
        question="GH1399 — integrity re-ask must never synthesize a verdict",
        session_id="test-GH1399-integrity",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_integrity_prev(tmp_path: Path) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "REVIEW THIS DIFF AND EMIT A VERDICT.",
            "stable_prefix": "STABLE-PREFIX-GH1399",
            "doc_path": str(tmp_path / "reviews" / "build-integrity-review.md"),
            "diff_path": str(tmp_path / "integrity" / "test-diff.patch"),
        },
        duration_ms=0,
        step_name="build_integrity_prompt",
    )


# ═══════════════════════════════════════════════════════════════════════════
# AST helper for AC2(b) — backend-identity string-literal comparison scan.
# ═══════════════════════════════════════════════════════════════════════════


def _find_function(tree: ast.AST, func_name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node  # type: ignore[return-value]
    return None


def _str_constants_under(node: ast.AST) -> list[str]:
    """Every str Constant reachable from `node`, including inside a
    tuple/list/set literal — so `x in ("a", "b")` is not a blind spot."""
    return [
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _str_literal_operand(node: ast.AST) -> list[str]:
    """String literals that this operand IS, as a comparison right-hand side.

    Deliberately narrow — only a bare str Constant (`== "x"`) or a
    tuple/list/set display of str Constants (`in ("a", "b")`). NOT any string
    reachable anywhere inside the operand: `cfg["expected"]` carries the
    literal `"expected"` as a dict KEY, and scoring that as "compared against
    a backend literal" would make the predicate fire on legitimate
    `_resolve_backend(...)[0] == cfg["expected"]` code. A guard that also
    condemns the correct shape cannot earn its green.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [
            e.value for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


def _backend_operand_label(node: ast.AST) -> str | None:
    """If `node` is an expression that YIELDS a backend identity, return a
    human-readable label for it; else None.

    Three forms, all of which occur or could trivially occur in
    `phase_6_review.py`:

      * `_backend`                              -> ast.Name
      * `_resolve_backend(None, os.environ)`    -> ast.Call, func named …backend…
      * `_resolve_backend(None, os.environ)[0]` -> ast.Subscript over the above

    The call/subscript form is NOT hypothetical: `phase_6_review.py:975`
    already reads `_resolve_backend(None, os.environ)[0] == "claude-in-session"`
    in the straggler-abort path, i.e. it is an idiom of this very file. A
    `ast.Name`-only predicate would be silently satisfied by a GREEN that
    merely drops the `_backend = …` binding and compares in place, keeping the
    defect GH1399 exists to remove. Attribute funcs (`backends.resolve(env)`)
    are covered for the same reason.
    """
    if isinstance(node, ast.Name):
        return node.id if "backend" in node.id.lower() else None
    if isinstance(node, ast.Subscript):
        inner = _backend_operand_label(node.value)
        return f"{inner}[…]" if inner else None
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and "backend" in fn.id.lower():
            return f"{fn.id}(…)"
        if isinstance(fn, ast.Attribute) and "backend" in fn.attr.lower():
            return f"{fn.attr}(…)"
        return None
    if isinstance(node, ast.Attribute):
        return f".{node.attr}" if "backend" in node.attr.lower() else None
    return None


def _find_backend_literal_compares(tree: ast.AST, func_name: str) -> list[str]:
    """Return human-readable hits for `Compare` nodes inside function
    `func_name` that pair a backend-yielding operand with a str literal — via
    `==`/`!=` OR via `in`/`not in` against a str-literal container.

    Scoped to the named function only (not the whole module) so unrelated
    backend comparisons elsewhere in the file (e.g. straggler_abort handling)
    don't pollute the result.

    §1n note on the container form: `_backend in ("claude-in-session",)` is
    the cheapest evasion of an `==`-only predicate and would re-introduce
    exactly the defect GH1399 exists to remove, so it is caught here.

    Literals are harvested from the OTHER operands only — never from inside
    the backend-yielding operand itself — so `_resolve_backend(None, env)` is
    not scored against its own arguments.
    """
    target = _find_function(tree, func_name)
    if target is None:
        return [f"function {func_name!r} not found in module"]

    hits: list[str] = []
    for node in ast.walk(target):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        labels = [(i, _backend_operand_label(op)) for i, op in enumerate(operands)]
        labels = [(i, lbl) for i, lbl in labels if lbl]
        if not labels:
            continue
        backend_idx = {i for i, _ in labels}
        consts = [
            c for i, op in enumerate(operands)
            if i not in backend_idx
            for c in _str_literal_operand(op)
        ]
        if consts:
            ops = "/".join(type(o).__name__ for o in node.ops)
            hits.append(f"line {node.lineno}: {labels[0][1]} {ops} {consts!r}")
    return hits


def _stepresult_data_keys(tree: ast.AST) -> tuple[int, list[str]]:
    """Scan every `StepResult(...)` call in `tree`. Return
    (number_of_StepResult_calls_seen, hits) where a hit is any call whose
    `data=` argument is a dict literal carrying a `retry_from_step` key.

    `retry_from_step` is the ONLY thing engine.py's retry predicate
    (engine.py:520-524 and the composite mirror at ~L940-946) keys on:

        result.recoverable
        and isinstance(result.data, dict)
        and result.data.get("retry_from_step") is not None

    so its absence module-wide is a structural proof that no step of this
    module can drive engine recursion — independently of `recoverable`.
    """
    seen = 0
    hits: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "StepResult"):
            continue
        seen += 1
        for kw in node.keywords:
            if kw.arg != "data":
                continue
            for c in _str_constants_under(kw.value):
                if c == "retry_from_step":
                    hits.append(f"line {node.lineno}: StepResult(data={{... 'retry_from_step' ...}})")
    return seen, hits


def _error_codes_returned_by(tree: ast.AST, src: str, func_name: str) -> set[str]:
    """Every `error_code="E_..."` string literal written inside `func_name`."""
    target = _find_function(tree, func_name)
    if target is None:
        raise AssertionError(f"function {func_name!r} not found — test is measuring nothing")
    codes: set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "error_code" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    codes.add(kw.value.value)
    return codes


# ═══════════════════════════════════════════════════════════════════════════
# AC1 (RED) — non-conformant under agent-sdk recovers deterministically.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_agent_sdk_non_conformant_recovers_deterministically(tmp_path, monkeypatch):
    """AC1: RED.

    backend=agent-sdk (today's real default per llm_subprocess._DEFAULT_BACKEND)
    with a non-conformant raw_response must be recovered by the SAME
    deterministic `_normalize_to_aggregated_findings` path already proven
    total for claude-in-session — zero LLM calls, status='ok'.

    FAILS today: the conformance branch dispatches on `_backend ==
    "claude-in-session"` (phase_6_review.py:2030); agent-sdk falls into the
    `else` paid-retry branch and calls invoke_llm_subprocess.
    """
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(p6, "_resolve_backend", lambda *a, **kw: ("agent-sdk", "default"))

    prev, doc_path = _prev_nonconformant(tmp_path)
    ctx = _make_ctx(tmp_path / "scratch")
    mock_invoke = _mock_invoke_ok_raw("## Aggregated Findings\n\nVERDICT: PASS\n")

    with patch.object(p6, "invoke_llm_subprocess", mock_invoke):
        result = p6._write_review_artifact(ctx, prev)

    assert mock_invoke.call_count == 0, (
        f"AC1: expected 0 LLM retry calls for backend='agent-sdk', got "
        f"{mock_invoke.call_count}. FAILS today: selection is by backend "
        "identity, not by the conformance property."
    )
    assert result.status == "ok", (
        f"AC1: expected status='ok', got {result.status!r} "
        f"(error={getattr(result, 'error', None)!r}, "
        f"error_code={getattr(result, 'error_code', None)!r})"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC2 (RED) — selection by PROPERTY, not backend identity.
# Split into 2a (behavioural) and 2b (source) as SEPARATE tests: run as one,
# 2a's failure short-circuits 2b and 2b's red would never be demonstrated.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac2a_every_backend_normalizes_deterministically(tmp_path, monkeypatch):
    """AC2a: RED (behavioural).

    EVERY backend — registered or not — must normalize deterministically on
    non-conformant content: zero LLM calls, status ok. The unregistered
    backend in the list is the point: the decision may not depend on
    membership in any backend enumeration, only on the response's own
    conformance property.

    FAILS today: backends other than 'claude-in-session' call
    invoke_llm_subprocess (phase_6_review.py:2029-2030).
    """
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    # The last name is generated per RUN, so the observed set is NOT enumerable
    # at authoring time. A whitelist covering exactly the four fixed names —
    # even one hoisted into a foreign module (evasion form G10) — passes both
    # guards while the identity-based selection defect is still live. Lesson
    # #1400: closedness of a set of VALUES is not held by a whitelist.
    backends = ["agent-sdk", "claude-subprocess", "claude-in-session",
                "some-unregistered-backend", f"backend-{uuid.uuid4().hex}"]
    for backend in backends:
        monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
        monkeypatch.setattr(p6, "_resolve_backend", lambda *a, _b=backend, **kw: (_b, "test"))
        # The declared world must say "this backend" through EVERY channel, not
        # just the patched resolver: an implementation that reads the env var
        # directly would otherwise see an empty env, fall back to its own
        # default, and rescue everything only under test conditions.
        monkeypatch.setenv("HAL_LLM_BACKEND", backend)
        sentinel = f"SENTINEL-AC2A-{backend}"
        prev, doc_path = _prev_nonconformant(
            tmp_path / backend,
            raw_response=f"{sentinel}\nsummary only, no header\nVERDICT: PASS",
        )
        ctx = _make_ctx(tmp_path / backend / "scratch")
        mock_invoke = _mock_invoke_ok_raw("## Aggregated Findings\n\nVERDICT: PASS\n")

        with patch.object(p6, "invoke_llm_subprocess", mock_invoke):
            result = p6._write_review_artifact(ctx, prev)

        assert mock_invoke.call_count == 0, (
            f"AC2a: backend={backend!r} must normalize deterministically with "
            f"0 LLM calls, got {mock_invoke.call_count}. FAILS today: selection "
            "is gated on the backend string literal, not on non-conformance."
        )
        assert result.status == "ok", (
            f"AC2a: backend={backend!r} expected status='ok', got "
            f"{result.status!r} (error_code={getattr(result, 'error_code', None)!r})."
        )
        # ── OBSERVED OUTPUT, not the absence of a symptom (gate r2 BLOCKING-2).
        # "did not retry" ≠ "was rescued": an implementation that normalizes NO
        # backend passes the two asserts above, because non-conformant content
        # simply falls through to persist untouched. These two asserts pin the
        # bytes that actually reached disk, so any selection that skips a
        # backend — by name, by helper, by env read, by ANY form — reddens here
        # on that backend, without the test having to enumerate the forms.
        assert doc_path.is_file(), (
            f"AC2a: backend={backend!r} — review artifact was not persisted at "
            f"{doc_path}; a rescued run must reach persist."
        )
        disk = doc_path.read_text(encoding="utf-8")
        assert any(line.rstrip("\r\n") == "## Aggregated Findings" for line in disk.splitlines()), (
            f"AC2a: backend={backend!r} — persisted artifact carries NO canonical "
            f"'## Aggregated Findings' header line, i.e. this backend was not "
            f"normalized at all. Got: {disk[:300]!r}"
        )
        assert sentinel in disk, (
            f"AC2a: backend={backend!r} — body sentinel {sentinel!r} absent from the "
            f"persisted artifact; normalization must preserve the body verbatim. "
            f"Got: {disk[:300]!r}"
        )


def test_ac2b_no_backend_identity_literal_governs_the_branch():
    """AC2b: RED (source guard).

    `_write_review_artifact` must contain NO `Compare` node pairing a
    'backend'-named operand with a str literal — neither `==` nor membership
    in a literal container.

    Guard liveness is proven against frozen fixture snippets reproducing the
    violation shapes — NOT against the live file — so the guard's correctness
    does not depend on production still containing the violation it exists to
    catch; a negative control proves it is not simply always-firing.

    FAILS today: phase_6_review.py:2030 contains `_backend ==
    "claude-in-session"` inside `_write_review_artifact`.
    """
    # Two violation SHAPES, both proven to fire. The membership form is the
    # cheapest evasion of an `==`-only predicate, so it gets its own proof.
    _LIVENESS_FIXTURES = {
        "equality": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if not _is_review_conformant(content):\n"
            "        _backend = _resolve_backend(None, os.environ)[0]\n"
            "        if _backend == \"claude-in-session\":\n"
            "            pass\n"
        ),
        "membership": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if not _is_review_conformant(content):\n"
            "        _backend = _resolve_backend(None, os.environ)[0]\n"
            "        if _backend in (\"claude-in-session\", \"claude-subprocess\"):\n"
            "            pass\n"
        ),
        # The binding-free form. NOT hypothetical: phase_6_review.py:975
        # already writes exactly this shape in the straggler-abort path, so a
        # GREEN that drops the `_backend = …` line and compares in place is
        # the single cheapest way to green an ast.Name-only predicate while
        # keeping the defect. Gate r1 MAJOR-1.
        "call_subscript": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if not _is_review_conformant(content):\n"
            "        if _resolve_backend(None, os.environ)[0] == \"claude-in-session\":\n"
            "            pass\n"
        ),
        # Same evasion one step further: no subscript, and the resolver reached
        # through an attribute rather than a module-level name.
        "call_attribute": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if not _is_review_conformant(content):\n"
            "        if backends.resolve_backend(os.environ) != \"claude-in-session\":\n"
            "            pass\n"
        ),
        # And the membership variant of the binding-free form, so closing the
        # `==` hole does not simply move the evasion one operator sideways.
        "call_subscript_membership": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if not _is_review_conformant(content):\n"
            "        if _resolve_backend(None, os.environ)[0] in (\"claude-in-session\",):\n"
            "            pass\n"
        ),
    }
    for shape, fixture_src in _LIVENESS_FIXTURES.items():
        liveness_hits = _find_backend_literal_compares(
            ast.parse(fixture_src), "_write_review_artifact"
        )
        assert liveness_hits, (
            f"AC2b: guard is BLIND on the {shape!r} shape — the AST predicate "
            "found zero backend-literal compares against a fixture that "
            "deliberately reproduces that violation shape. The predicate "
            "itself is broken; fix the predicate, not the test."
        )

    # Negative controls: 'no hits' must be EARNABLE, and must stay earnable
    # after the predicate was widened to call/subscript operands (gate r1
    # MAJOR-1) — a widened predicate that fires on everything is the same
    # vacuum wearing the opposite sign.
    _CLEAN_FIXTURES = {
        # The target shape: decision taken on the response's own property.
        "property_branch": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if not _is_review_conformant(content):\n"
            "        content = _normalize_to_aggregated_findings(content)\n"
        ),
        # A literal compare against a NON-backend call — must not be swept up
        # just because the widened predicate now looks at calls at all.
        "non_backend_call_literal": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if _resolve_model(cfg, \"review_model\")[0] == \"opus\":\n"
            "        pass\n"
        ),
        # A backend-yielding operand compared to something that is NOT a
        # literal — legitimate (e.g. comparing two resolved values), and the
        # defect is specifically the hard-coded identity.
        "backend_vs_non_literal": (
            "def _write_review_artifact(ctx, prev):\n"
            "    if _resolve_backend(None, os.environ)[0] == cfg[\"expected\"]:\n"
            "        pass\n"
        ),
    }
    for shape, clean_src in _CLEAN_FIXTURES.items():
        assert not _find_backend_literal_compares(
            ast.parse(clean_src), "_write_review_artifact"
        ), (
            f"AC2b: predicate fires on the clean {shape!r} fixture — it is not "
            "specific, so its 'no hits' verdict against production would be "
            "unearned."
        )

    real_source = PHASE_6_REVIEW_PATH.read_text(encoding="utf-8")
    real_hits = _find_backend_literal_compares(ast.parse(real_source), "_write_review_artifact")
    assert not real_hits, (
        f"AC2b: _write_review_artifact must not gate the conformance branch on "
        f"a backend-identity string literal. Found: {real_hits!r}. "
        "FAILS today: phase_6_review.py:2030 compares `_backend == "
        "\"claude-in-session\"`."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC4 (RED) — normalization is a COUNTED event, for every backend.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac4_normalization_is_counted_event_for_agent_sdk(tmp_path, monkeypatch):
    """AC4: RED.

    `_emit_safe("review_stdout_normalized_deterministic", ...)` already fires
    on the claude-in-session lane (phase_6_review.py:2032-2034). It must ALSO
    fire when the SAME deterministic recovery is applied under backend
    'agent-sdk', with a payload that names the resolved backend.

    FAILS today: agent-sdk takes the `else` (paid-retry) branch and never
    reaches the `_emit_safe("review_stdout_normalized_deterministic", ...)`
    call, so the event list is empty.
    """
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    captured = _patch_emit(monkeypatch, p6)
    monkeypatch.setattr(p6, "_resolve_backend", lambda *a, **kw: ("agent-sdk", "default"))

    prev, _doc_path = _prev_nonconformant(tmp_path)
    ctx = _make_ctx(tmp_path / "scratch")
    # Return conformant content if a retry IS made today — assertion is about
    # the EVENT, not about whether the run completes.
    mock_invoke = _mock_invoke_ok_raw("## Aggregated Findings\n\nVERDICT: PASS\n")

    with patch.object(p6, "invoke_llm_subprocess", mock_invoke):
        p6._write_review_artifact(ctx, prev)

    normalize_events = [
        (et, pl) for et, pl in captured
        if et == "review_stdout_normalized_deterministic"
    ]
    assert len(normalize_events) == 1, (
        f"AC4: expected exactly 1 'review_stdout_normalized_deterministic' "
        f"event for backend='agent-sdk'. Got {len(normalize_events)} in "
        f"captured events: {captured!r}. FAILS today: event only fires on "
        "the claude-in-session branch."
    )
    _et, payload = normalize_events[0]
    assert payload.get("backend") == "agent-sdk", (
        f"AC4: normalize event payload must carry the resolved backend "
        f"'agent-sdk'. Got payload={payload!r}."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC9 (RED, §1l live side-effect) — artifact FILE persisted with header+body.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac9_review_artifact_persisted_with_header_and_body_agent_sdk(tmp_path, monkeypatch):
    """AC9: RED (§1l live side-effect — asserted against the real file on
    disk, not against the return value).

    A non-conformant reviewer response under backend='agent-sdk' must reach
    persist: the review artifact FILE must exist at doc_path afterwards, and
    its bytes must contain BOTH the '## Aggregated Findings' header AND the
    original body text verbatim.

    Retry mock deliberately returns a STILL non-conformant response, so this
    proves recovery does not depend on a lucky retry outcome — it proves the
    deterministic path is reached at all for this backend.

    FAILS today: agent-sdk's retry-then-die path returns
    E_REVIEW_FORMAT_DRIFT (recoverable=False) BEFORE the persist step
    (phase_6_review.py:2086+) is ever reached — doc_path.is_file() is False.
    """
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(p6, "_resolve_backend", lambda *a, **kw: ("agent-sdk", "default"))

    sentinel = "SENTINEL-GH1399-BODY-VERBATIM"
    prev, doc_path = _prev_nonconformant(tmp_path, raw_response=f"{sentinel}\nno header on first attempt")
    ctx = _make_ctx(tmp_path / "scratch")
    still_bad = f"{sentinel}\nstill no header on retry"
    mock_invoke = _mock_invoke_ok_raw(still_bad)

    with patch.object(p6, "invoke_llm_subprocess", mock_invoke):
        p6._write_review_artifact(ctx, prev)

    assert doc_path.is_file(), (
        f"AC9: expected review artifact persisted at {doc_path}, but the run "
        "died before persist. FAILS today: agent-sdk's non-conformant "
        "content dies via E_REVIEW_FORMAT_DRIFT before the write step."
    )
    disk = doc_path.read_text(encoding="utf-8")
    conformant = any(
        line.rstrip("\n\r") == "## Aggregated Findings"
        for line in disk.splitlines(keepends=True)
    )
    assert conformant, (
        f"AC9: persisted file must contain a line exactly "
        f"'## Aggregated Findings'. Got disk content: {disk[:300]!r}."
    )
    assert sentinel in disk, (
        f"AC9: persisted file must preserve the original body verbatim "
        f"(sentinel {sentinel!r} missing). Got disk content: {disk[:300]!r}."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC3 (SHIELD) — body preserved verbatim through normalization.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac3_body_preserved_verbatim_through_normalization(tmp_path, monkeypatch):
    """AC3: SHIELD: green before the change; pins behaviour GREEN must not break.

    `_normalize_to_aggregated_findings` preserves the body verbatim when
    called directly, and the same holds end-to-end through
    `_write_review_artifact` on the one lane (claude-in-session) that already
    reaches the deterministic path today.
    """
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    sentinel = "SENTINEL-AC3-BODY-GH1399"

    normalized = p6._normalize_to_aggregated_findings(f"{sentinel}\nno header")
    assert sentinel in normalized, (
        f"AC3: direct helper call must preserve body verbatim. Got: {normalized!r}"
    )

    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(p6, "_resolve_backend", lambda *a, **kw: ("claude-in-session", "test"))

    prev, doc_path = _prev_nonconformant(tmp_path, raw_response=f"{sentinel}\nno header")
    ctx = _make_ctx(tmp_path / "scratch")
    mock_invoke = _mock_invoke_ok_raw("unused — must not be called on this lane")

    with patch.object(p6, "invoke_llm_subprocess", mock_invoke):
        result = p6._write_review_artifact(ctx, prev)

    assert result.status == "ok", f"AC3: expected status='ok', got {result.status!r}"
    disk = doc_path.read_text(encoding="utf-8")
    assert sentinel in disk, (
        f"AC3: persisted file must preserve body verbatim. Got: {disk[:300]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC5 (SHIELD) — phase_5_integrity DOES re-ask before failing.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac5_integrity_reasks_before_failing(tmp_path, monkeypatch):
    """AC5: SHIELD: green before the change; pins behaviour GREEN must not break.

    With two consecutive VERDICT-less replies and the default retry budget
    (`_resolve_integrity_verdict_retries` default=1), `invoke_llm_subprocess`
    must be called exactly twice before the run terminates with
    E_INTEGRITY_NO_MARKER — GH786's bounded re-ask loop already exists.
    """
    from bytedigger_engine.workflows import phase_5_integrity as p5  # noqa: PLC0415

    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        extra = kwargs.get("extra_data") or {}
        return StepResult(
            status="ok",
            data={
                "raw_response": "prose with no standalone VERDICT line at all",
                "doc_path": extra.get("doc_path"),
                "diff_path": extra.get("diff_path"),
            },
            duration_ms=0,
            step_name="invoke_integrity_llm",
        )

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _fake)
    ctx = _make_integrity_ctx(tmp_path / "scratch")
    prev = _make_integrity_prev(tmp_path)

    step2 = p5._invoke_integrity_llm(ctx, prev)
    step3 = p5._classify_diff_verdict(ctx, step2)

    assert len(calls) == 2, (
        f"AC5: expected exactly 2 invoke_llm_subprocess calls (1 initial + "
        f"1 re-ask) before terminal failure, got {len(calls)}."
    )
    assert step3.error_code == "E_INTEGRITY_NO_MARKER", (
        f"AC5: expected terminal error_code='E_INTEGRITY_NO_MARKER' after "
        f"re-ask exhaustion, got {step3.error_code!r}."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC6 (SHIELD, prohibition) — verdict is NEVER synthesized.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac6_integrity_never_synthesizes_a_verdict(tmp_path, monkeypatch):
    """AC6 (prohibition): SHIELD: green before the change; pins behaviour
    GREEN must not break.

    After re-ask exhaustion the outcome is an ERROR with
    data["verdict"] == VERDICT_UNKNOWN — never a synthesized valid marker.
    Also: `_classify_diff_verdict`'s body must not directly assign a
    VERDICT_* constant literal into data["verdict"] anywhere outside the
    `_parse_verdict(raw)` derivation (the only sanctioned source) and the
    explicit `verdict_override` passthrough (explicit caller input, not
    synthesis).
    """
    from bytedigger_engine.workflows import phase_5_integrity as p5  # noqa: PLC0415

    def _fake(**kwargs):
        extra = kwargs.get("extra_data") or {}
        return StepResult(
            status="ok",
            data={
                "raw_response": "still nothing resembling a verdict marker",
                "doc_path": extra.get("doc_path"),
                "diff_path": extra.get("diff_path"),
            },
            duration_ms=0,
            step_name="invoke_integrity_llm",
        )

    monkeypatch.setattr(p5, "invoke_llm_subprocess", _fake)
    ctx = _make_integrity_ctx(tmp_path / "scratch")
    prev = _make_integrity_prev(tmp_path)

    step2 = p5._invoke_integrity_llm(ctx, prev)
    step3 = p5._classify_diff_verdict(ctx, step2)

    assert step3.status == "error", (
        f"AC6: expected status='error' on exhaustion, got {step3.status!r}"
    )
    assert step3.data["verdict"] == p5.VERDICT_UNKNOWN, (
        f"AC6: verdict must remain UNKNOWN, never synthesized. Got "
        f"{step3.data.get('verdict')!r}."
    )

    # ── source guard, with liveness proof BEFORE it is trusted ──────────────
    _SYNTHESIS_PATTERN = r'"verdict":\s*VERDICT_\w+'
    _MUTANT_SRC = (
        'def _classify_diff_verdict(_ctx, prev):\n'
        '    if verdict == VERDICT_UNKNOWN:\n'
        '        common = {"verdict": VERDICT_LEGITIMATE_REFACTOR}\n'
        '        return StepResult(status="ok", data=common)\n'
    )
    assert re.findall(_SYNTHESIS_PATTERN, _MUTANT_SRC), (
        "AC6: guard is BLIND — the synthesis pattern found nothing in a mutant "
        "that literally fabricates a verdict on the UNKNOWN path. This is the "
        "exact fail-open the spec's ЗАПРЕТ section forbids; fix the pattern."
    )
    assert not re.findall(_SYNTHESIS_PATTERN, 'common = {"verdict": _parse_verdict(raw)}'), (
        "AC6: guard fires on the sanctioned _parse_verdict derivation — not specific."
    )

    src = PHASE_5_INTEGRITY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = _find_function(tree, "_classify_diff_verdict")
    assert fn is not None, "AC6: _classify_diff_verdict not found — measuring nothing"
    fn_src = ast.get_source_segment(src, fn) or ""
    assert "VERDICT_UNKNOWN" in fn_src, (
        "AC6: _classify_diff_verdict no longer mentions VERDICT_UNKNOWN — the "
        "scanned region is not the region this AC is about."
    )
    forbidden = re.findall(_SYNTHESIS_PATTERN, fn_src)
    assert not forbidden, (
        f"AC6: found a direct VERDICT_* literal assigned into data['verdict'] "
        f"inside _classify_diff_verdict (a synthesis path): {forbidden!r}. "
        "The only sanctioned sources are _parse_verdict(raw) and the explicit "
        "verdict_override passthrough."
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC7 (SHIELD, prohibition) — E_INTEGRITY_ASSERTION_GAMING stays terminal.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_assertion_gaming_gate_stays_terminal_and_unsynthesizable(tmp_path):
    """AC7 (prohibition): SHIELD: green before the change; pins behaviour
    GREEN must not break.

    A gaming verdict is terminal (status='error', recoverable=False,
    error_code='E_INTEGRITY_ASSERTION_GAMING'). A no-marker reply must NEVER
    be reachable as ASSERTION_GAMING — it must classify as
    E_INTEGRITY_NO_MARKER instead. Any "normalize the ambiguous case" GREEN
    implementation that blurs these two would break this test.
    """
    from bytedigger_engine.workflows import phase_5_integrity as p5  # noqa: PLC0415

    ctx = _make_integrity_ctx(tmp_path / "scratch")

    prev_gaming = StepResult(
        status="ok",
        data={
            "doc_path": str(tmp_path / "reviews" / "r.md"),
            "diff_path": str(tmp_path / "integrity" / "d.patch"),
            "raw_response": "VERDICT: ASSERTION_GAMING",
            "verdict_completeness_retries": 0,
        },
        duration_ms=0,
        step_name="invoke_integrity_llm",
    )
    step3_gaming = p5._classify_diff_verdict(ctx, prev_gaming)
    assert step3_gaming.status == "error", (
        f"AC7: gaming verdict must terminate with status='error', got "
        f"{step3_gaming.status!r}"
    )
    assert step3_gaming.error_code == "E_INTEGRITY_ASSERTION_GAMING", (
        f"AC7: expected error_code='E_INTEGRITY_ASSERTION_GAMING', got "
        f"{step3_gaming.error_code!r}"
    )
    assert step3_gaming.recoverable is False, (
        f"AC7: E_INTEGRITY_ASSERTION_GAMING must be recoverable=False, got "
        f"{step3_gaming.recoverable!r}"
    )

    prev_no_marker = StepResult(
        status="ok",
        data={
            "doc_path": str(tmp_path / "reviews" / "r2.md"),
            "diff_path": str(tmp_path / "integrity" / "d2.patch"),
            "raw_response": "no marker of any kind in this reply",
            "verdict_completeness_retries": 0,
        },
        duration_ms=0,
        step_name="invoke_integrity_llm",
    )
    step3_no_marker = p5._classify_diff_verdict(ctx, prev_no_marker)
    assert step3_no_marker.error_code != "E_INTEGRITY_ASSERTION_GAMING", (
        "AC7: a no-marker reply must never be classified as "
        f"ASSERTION_GAMING (synthesis). Got error_code="
        f"{step3_no_marker.error_code!r}"
    )
    assert step3_no_marker.error_code == "E_INTEGRITY_NO_MARKER", (
        f"AC7: no-marker reply must classify as E_INTEGRITY_NO_MARKER, got "
        f"{step3_no_marker.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC8 (SHIELD) — no double retry at the engine level.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8_format_drift_error_does_not_satisfy_engine_retry_predicate(tmp_path, monkeypatch):
    """AC8: SHIELD: green before the change; pins behaviour GREEN must not break.

    "Don't retry AGAIN" and "kill the run" are two decisions that today share
    one flag (`recoverable=False`, decision 291189A0). This AC pins the FIRST
    of them so that a GREEN which legitimately separates them — e.g. by
    flipping `recoverable` so the run is no longer killed — cannot silently
    hand engine-level recursion back on top of the inline retry.

    §1d MEASURED, and it revises 291189A0's stated rationale: engine.py's
    retry predicate (engine.py:520-524, mirrored in the composite loop at
    ~L940-946) is

        result.recoverable
        and isinstance(result.data, dict)
        and result.data.get("retry_from_step") is not None

    — since B07BC910 the error_code whitelist is gone and `retry_from_step`
    is the sole trigger. So `recoverable` ALONE never drove the double retry;
    what `recoverable=False` actually buys today is the terminal stuck-report
    at engine.py:313. The load-bearing invariant is therefore about
    `retry_from_step`, not about the flag.

    Two clauses, both non-vacuous by construction:

      (a) LIVE — a real `_write_review_artifact` run on non-conformant content
          makes at most 2 LLM calls (cap-2) and returns a result carrying no
          `retry_from_step`. True at HEAD (2 calls, drift) and true after a
          correct GREEN (0 calls, deterministic recovery), so it can never be
          satisfied by the branch simply disappearing.
      (b) STRUCTURAL — no `StepResult(...)` anywhere in phase_6_review.py sets
          `retry_from_step`, proven non-blind against a mutant fixture that
          does, and proven non-vacuous by counting the StepResult calls seen.
    """
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(p6, "_resolve_backend", lambda *a, **kw: ("claude-subprocess", "test"))

    prev, _doc_path = _prev_nonconformant(tmp_path)
    ctx = _make_ctx(tmp_path / "scratch")
    still_bad = "<still non-conformant — no header even after retry>"
    mock_invoke = _mock_invoke_ok_raw(still_bad)

    with patch.object(p6, "invoke_llm_subprocess", mock_invoke):
        result = p6._write_review_artifact(ctx, prev)

    # ── (a) live clause ─────────────────────────────────────────────────────
    assert mock_invoke.call_count <= 1, (
        f"AC8a: non-conformant content must cost AT MOST ONE inline retry "
        f"(cap-2 total LLM calls counting the caller's original); got "
        f"{mock_invoke.call_count} calls from _write_review_artifact alone. "
        "A second inline retry, or an engine retry layered on top, is the "
        "double-retry 291189A0 removed."
    )
    assert not (isinstance(result.data, dict) and result.data.get("retry_from_step") is not None), (
        f"AC8a: the result of a non-conformant review run must not carry "
        f"'retry_from_step' — that is the sole trigger of engine recursion. "
        f"Got data={result.data!r}, recoverable={result.recoverable!r}."
    )

    # ── (b) structural clause, liveness-proven ──────────────────────────────
    _MUTANT_SRC = (
        'def _write_review_artifact(ctx, prev):\n'
        '    return StepResult(status="error", data={"retry_from_step": 0,\n'
        '                      "cycle_count": 1}, duration_ms=0,\n'
        '                      step_name="write_review_artifact",\n'
        '                      error_code="E_REVIEW_FORMAT_DRIFT", recoverable=True)\n'
    )
    _mut_seen, _mut_hits = _stepresult_data_keys(ast.parse(_MUTANT_SRC))
    assert _mut_seen == 1 and _mut_hits, (
        "AC8b: detector is BLIND — it did not flag a mutant that hands the "
        f"engine a retry target (seen={_mut_seen}, hits={_mut_hits!r}). Fix "
        "the detector, not the test."
    )

    seen, hits = _stepresult_data_keys(ast.parse(PHASE_6_REVIEW_PATH.read_text(encoding="utf-8")))
    assert seen >= 10, (
        f"AC8b: only {seen} StepResult constructions found in "
        f"{PHASE_6_REVIEW_PATH.name} — the scan is measuring nothing; a "
        "'no hits' verdict from it would be unearned."
    )
    assert not hits, (
        f"AC8b: phase_6_review.py must not hand the engine a retry target. "
        f"Found: {hits!r}. Splitting 'don't retry again' from 'kill the run' "
        "is done by leaving `retry_from_step` absent, not by adding it."
    )


# ═══════════════════════════════════════════════════════════════════════════
# §1n (RED) — CLOSED set of error codes for "advisory-phase output failure",
# each with its remedy. Without the closed set the third instance arrives
# under a third code and nobody notices it belongs to this class.
# ═══════════════════════════════════════════════════════════════════════════

# §1g ANCHOR — measured, not assumed: engine_py ALREADY has a canonical closed
# set of error codes, `error_codes.ERROR_CODES` (engine_py/bytedigger_engine/error_codes.py:28+),
# rendered to ERROR_CODES.md and enforced deterministically by
# tests/test_error_codes.py (`check()` returns both `unregistered` and `dead`
# sets; test_ac2_no_dead_codes_in_real_tree fails on either drift).
#
# So §1n's "write the closed set down" is HALF-DONE in the tree already. What
# the canonical registry does NOT carry — and what the two instances in this
# issue needed — is the REMEDY dimension: the registry's one-line docstring
# says what triggered a code, never whether the missing thing is
# deterministically reconstructible. `_CLASS_REGISTRY` below adds exactly that
# axis and is anchored to the canonical registry (see the anchor clause in the
# test), rather than becoming a second competing list of codes.
#
# Corollary for GREEN, flagged here because the deterministic layer will
# enforce it: a GREEN that deletes the paid-retry branch makes
# E_REVIEW_FORMAT_DRIFT DEAD, and test_error_codes.py will fail until the code
# is dropped from ERROR_CODES and ERROR_CODES.md is regenerated.
#
# remedy vocabulary — the distinction the whole lot is about:
#   "normalize"  — the missing thing is pure FORM and is deterministically
#                  reconstructible with the body preserved verbatim.
#   "re-ask"     — the missing thing is SEMANTIC. Never synthesizable; the
#                  only legitimate recovery is asking the model again, and
#                  exhaustion means refusal, not a fabricated value.
#   "terminal"   — not a failure of output shape at all: a real verdict, or a
#                  wiring/IO fault. No recovery belongs here.
_CLASS_REGISTRY: dict[str, dict[str, str]] = {
    "E_REVIEW_FORMAT_DRIFT": {
        "remedy": "normalize",
        "missing": "form: the '## Aggregated Findings' header",
        "why": "_normalize_to_aggregated_findings is total and body-preserving "
               "(phase_6_review.py:1873-1884); GH1399 routes every backend to it.",
    },
    "E_INTEGRITY_NO_MARKER": {
        "remedy": "re-ask",
        "missing": "semantics: the VERDICT itself",
        "why": "A verdict cannot be reconstructed from a reply that does not "
               "contain one. GH786's bounded re-roll "
               "(phase_5_integrity.py:468-490) is the sanctioned recovery; on "
               "exhaustion the answer is refusal (AC6).",
    },
    "E_INTEGRITY_ASSERTION_GAMING": {
        "remedy": "terminal",
        "missing": "nothing — this IS the reviewer's answer",
        "why": "A real gaming verdict. Retrying it would be re-rolling for a "
               "nicer answer to a safety gate (AC7).",
    },
    "E_MISSING_PREV_DATA": {
        "remedy": "terminal",
        "missing": "nothing from the model — upstream step wiring",
        "why": "Engine-internal contract violation, not model output drift.",
    },
    "E_REVIEW_WRITE_FAILED": {
        "remedy": "terminal",
        "missing": "nothing from the model — filesystem",
        "why": "OSError on persist. Neither normalization nor re-ask applies.",
    },
}

# §1n SCOPE OF CLOSURE — stated so the green verdict is not read wider than it
# is. Closure is declared over these THREE chokepoint functions of the class
# (the decision points where a model-output shape failure becomes an
# error_code), NOT over the engine as a whole. A fourth function — or
# `phase_6_fix_integrity.py`, which imports `phase_6_review` — could grow a
# code of this class without tripping the scan. Widening the tuple is a
# strictly larger lot: it pulls unrelated codes into _CLASS_REGISTRY and would
# turn this SHIELD red on HEAD. Gate r1 MINOR-1.
_CHOKEPOINT_FUNCS = (
    (lambda: PHASE_6_REVIEW_PATH, "_write_review_artifact"),
    (lambda: PHASE_5_INTEGRITY_PATH, "_classify_diff_verdict"),
    (lambda: PHASE_5_INTEGRITY_PATH, "_invoke_integrity_llm"),
)


def test_1n_closed_error_code_set_with_remedy_per_code():
    """§1n: SHIELD (honest label — this test is GREEN on HEAD).

    Every error_code emitted at the three chokepoint functions of this class
    must be REGISTERED above with an explicit remedy. A new code added to any
    of them fails this test until someone states which side of the
    normalize/re-ask line it falls on — which is the whole point: the two
    instances in the issue were the SAME class wearing two codes, and nothing
    forced anyone to notice.

    It does NOT fail today, and pretending otherwise would be theatre: §1n
    asks the LOT to write the closed set down, and the registry above is that
    writing-down. What earns the test its keep is the closure scan, which
    turns the list from prose into a gate — proven to fire by the companion
    liveness test below.

    ★ Closure is declared over the THREE chokepoints listed in
    `_CHOKEPOINT_FUNCS`, not over the engine as a whole — see the comment
    there. Read the green as "no unregistered code at these three decision
    points", never as "this class cannot appear anywhere else".
    """
    remedies = {v["remedy"] for v in _CLASS_REGISTRY.values()}
    assert remedies <= {"normalize", "re-ask", "terminal"}, (
        f"§1n: unknown remedy vocabulary in the registry: {remedies!r}"
    )

    # Every registered code names its remedy AND says why — no bare entries.
    for code, entry in _CLASS_REGISTRY.items():
        assert entry.get("why"), f"§1n: {code} registered without a rationale"
        assert entry.get("missing"), f"§1n: {code} registered without naming what is missing"

    # The load-bearing distinction, restated as an assertion so a future edit
    # that "normalizes both" trips here too.
    assert _CLASS_REGISTRY["E_REVIEW_FORMAT_DRIFT"]["remedy"] == "normalize"
    assert _CLASS_REGISTRY["E_INTEGRITY_NO_MARKER"]["remedy"] == "re-ask", (
        "§1n: E_INTEGRITY_NO_MARKER must NEVER be remedied by normalization — "
        "that fabricates a VERDICT for the anti-assertion-gaming gate and "
        "turns fail-closed into fail-open (spec ЗАПРЕТ / AC6)."
    )

    # Closure scan: no unregistered code may be emitted at a chokepoint.
    unregistered: dict[str, set[str]] = {}
    total_codes = 0
    for path_fn, func_name in _CHOKEPOINT_FUNCS:
        path = path_fn()
        src = path.read_text(encoding="utf-8")
        codes = _error_codes_returned_by(ast.parse(src), src, func_name)
        total_codes += len(codes)
        extra = codes - set(_CLASS_REGISTRY)
        if extra:
            unregistered[f"{path.name}:{func_name}"] = extra
    assert total_codes >= 5, (
        f"§1n: only {total_codes} error codes found across the chokepoints — "
        "the scan is measuring nothing and its 'all registered' verdict would "
        "be unearned."
    )
    assert not unregistered, (
        f"§1n: error codes emitted at a chokepoint of this class but absent "
        f"from _CLASS_REGISTRY: {unregistered!r}. Add each one with an "
        "explicit remedy (normalize / re-ask / terminal) — an unregistered "
        "code is the third instance arriving unnoticed."
    )

    # §1g anchor: the remedy axis must hang off the CANONICAL registry, never
    # float beside it. Every code still emitted at a chokepoint must exist in
    # error_codes.ERROR_CODES — so a rename there cannot leave this table
    # quietly describing a code that no longer exists.
    from bytedigger_engine import error_codes  # noqa: PLC0415

    emitted: set[str] = set()
    for path_fn, func_name in _CHOKEPOINT_FUNCS:
        src = path_fn().read_text(encoding="utf-8")
        emitted |= _error_codes_returned_by(ast.parse(src), src, func_name)
    unanchored = emitted - set(error_codes.ERROR_CODES)
    assert not unanchored, (
        f"§1n/§1g: {unanchored!r} is emitted at a chokepoint but missing from "
        "the canonical error_codes.ERROR_CODES registry."
    )


def test_1n_registry_scan_is_not_blind():
    """§1n liveness: the closure scan must actually fire on an unregistered
    code, otherwise its green verdict above proves nothing (guard evaluated
    BY ITS BRANCHES — a scan that can only ever return the empty set is not
    a guard)."""
    mutant = (
        'def _write_review_artifact(ctx, prev):\n'
        '    return StepResult(status="error", data=None, duration_ms=0,\n'
        '                      step_name="write_review_artifact",\n'
        '                      error_code="E_REVIEW_SHAPE_UNKNOWN_THIRD_INSTANCE")\n'
    )
    codes = _error_codes_returned_by(ast.parse(mutant), mutant, "_write_review_artifact")
    assert codes == {"E_REVIEW_SHAPE_UNKNOWN_THIRD_INSTANCE"}, (
        f"§1n: extractor did not see the mutant's error_code; got {codes!r}"
    )
    assert codes - set(_CLASS_REGISTRY), (
        "§1n: closure check is blind — a brand-new unregistered code did not "
        "come back as unregistered."
    )
