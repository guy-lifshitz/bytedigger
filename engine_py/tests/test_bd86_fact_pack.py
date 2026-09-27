"""RED tests for bd#86 (P5) — a fact pack before every model step, and a
spec-vs-reality gate that also runs on frozen specs.

Spec: docs/decisions/2026-09-27-bd86-fact-pack.md §2, §4.

`facts_pack` does not exist yet, so it is imported inside the test bodies:
the file collects cleanly and every test fails at run time. Every repo fact
comes from a hermetic fixture repo under `tmp_path`, never the real checkout.
No unit under test is patched; only the telemetry seam `_emit_safe` is
replaced to record events.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.workflows import phase_45_spec, phase_5_implement, phase_6_review

_ENGINE_ROOT = Path(__file__).resolve().parents[1]


def _fp():
    return importlib.import_module("bytedigger_engine.facts_pack")


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    for var in ("GRAPHIFY_OUT", "HAL_FACTS_PACK", "HAL_SPEC_REALITY_GATE"):
        monkeypatch.delenv(var, raising=False)


_SEEDLESS = "Improve the module"


# ─── fixtures ─────────────────────────────────────────────────────────────────


def _repo(root: Path, *, graph: bool = False, ledger: str | None = None) -> Path:
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True, exist_ok=True)
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "pkg" / "mod.py").write_text(
        "def existing_helper(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def other():\n"
        "    return existing_helper(1)\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_mod.py").write_text(
        "from pkg.mod import existing_helper\n"
        "\n"
        "\n"
        "def test_existing_helper_works():\n"
        "    assert existing_helper(1) == 2\n",
        encoding="utf-8",
    )
    if graph:
        out = repo / "graphify-out"
        out.mkdir()
        (out / "graph.json").write_text(json.dumps({
            "nodes": [
                {"id": "pkg_mod_existing_helper", "label": "existing_helper()",
                 "file_type": "code", "source_file": "pkg/mod.py", "source_location": "L1"},
                {"id": "pkg_mod_caller_fn", "label": "caller_fn()",
                 "file_type": "code", "source_file": "pkg/mod.py", "source_location": "L5"},
            ],
            "links": [
                {"source": "pkg_mod_caller_fn", "target": "pkg_mod_existing_helper",
                 "relation": "calls"},
            ],
        }), encoding="utf-8")
    if ledger is not None:
        (repo / "known-reds.md").write_text(ledger, encoding="utf-8")
    return repo


_LEDGER = (
    "| Suite | Red | Scope | Issue | Kill-by | Class |\n"
    "|---|---|---|---|---|---|\n"
    "| pytest | tests/test_mod.py::test_existing_helper_works | all | #1 | 2099-01-01 | flaky |\n"
    "| pytest | tests/test_unrelated.py::test_x | all | #2 | 2099-01-01 | flaky |\n"
)


def _ctx(scratch: Path, repo: Path, *, question: str = "Change `existing_helper`", **extra) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    inj = scratch / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "hal_root": str(repo), **extra},
        question=question, session_id="bd86", persona="p", framework=None, domain=None,
    )


def _write_spec(scratch: Path, text: str) -> Path:
    spec = scratch / "specs" / "build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(text, encoding="utf-8")
    return spec


_SPEC = (
    "## Context\n"
    "Change `existing_helper` in `pkg/mod.py`.\n"
    "\n"
    "## Acceptance\n"
    "| AC | criterion |\n"
    "|---|---|\n"
    "| AC1 | `existing_helper` in `pkg/mod.py` returns x + 2 |\n"
)


# ─── op1: collect ─────────────────────────────────────────────────────────────


def test_ac1_seed_tokens():
    fp = _fp()
    text = (
        "Touch `beta_fn` and `alpha_fn()` in `pkg/mod.py`; also call gamma_fn() "
        "from ./lib/util.ts. `not a symbol` stays out."
    )
    seeds = fp.seed_tokens(text)
    assert seeds.symbols == ("alpha_fn", "beta_fn", "gamma_fn"), seeds
    assert seeds.files == ("lib/util.ts", "pkg/mod.py"), seeds
    many = " ".join(f"`sym_{i:03d}`" for i in range(100))
    assert len(fp.seed_tokens(many).symbols) == 40
    many_files = " ".join(f"d/f{i:03d}.py" for i in range(100))
    assert len(fp.seed_tokens(many_files).files) == 20
    assert fp.seed_tokens(text) == seeds


def test_ac2_symbols_and_unresolved(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    pack = fp.collect(repo, "Use `existing_helper` and `existing_helpr` in `pkg/mod.py`")
    by_name = {s["name"]: s for s in pack["symbols"]}
    assert by_name["existing_helper"]["defined_at"] == ["pkg/mod.py:1"], pack["symbols"]
    unresolved = {u["name"]: u for u in pack["unresolved"]}
    assert "existing_helpr" in unresolved, pack["unresolved"]
    assert "existing_helper" in unresolved["existing_helpr"]["near"]
    assert pack["files"] == [{"path": "pkg/mod.py", "exists": True}]
    assert pack["version"] == 1


def test_ac2b_seed_cap_keeps_function_names():
    fp = _fp()
    text = " ".join(f"`E_CODE_{i:02d}`" for i in range(45)) + " `zeta_fn`"
    assert "zeta_fn" in fp.seed_tokens(text).symbols


def test_ac3_existing_tests(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    pack = fp.collect(repo, "Change `existing_helper`")
    tests = {t["file"]: t for t in pack["tests"]}
    assert "tests/test_mod.py" in tests, pack["tests"]
    entry = tests["tests/test_mod.py"]
    assert "existing_helper" in entry["references"]
    assert entry["test_ids"] == ["test_existing_helper_works"]
    # a seed file's stem also pulls in the module's tests
    by_file = fp.collect(repo, "Refactor `pkg/mod.py`")
    assert "tests/test_mod.py" in {t["file"] for t in by_file["tests"]}


def test_ac4_graph(tmp_path, monkeypatch):
    fp = _fp()
    monkeypatch.delenv("GRAPHIFY_OUT", raising=False)
    repo = _repo(tmp_path, graph=True)
    pack = fp.collect(repo, "Change `existing_helper`")
    assert pack["graph"]["status"] == "ok", pack["graph"]
    node = next(n for n in pack["graph"]["nodes"] if n["symbol"] == "existing_helper")
    assert "caller_fn()" in node["callers"], node
    bare = _repo(tmp_path / "bare")
    assert fp.collect(bare, "Change `existing_helper`")["graph"]["status"] == "absent"


def test_ac4b_unreadable_graph(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    (repo / "graphify-out").mkdir()
    (repo / "graphify-out" / "graph.json").write_text("{not json", encoding="utf-8")
    pack = fp.collect(repo, "Change `existing_helper`")
    assert pack["graph"]["status"] == "unreadable", pack["graph"]
    assert pack["symbols"] and pack["tests"]


def test_ac5_known_reds(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path, ledger=_LEDGER)
    pack = fp.collect(repo, "Change `existing_helper`")
    kr = pack["known_reds"]
    assert kr["status"] == "ok"
    joined = json.dumps(kr["rows"])
    assert "tests/test_mod.py::test_existing_helper_works" in joined
    assert "test_unrelated" not in joined
    bare = _repo(tmp_path / "bare")
    assert fp.collect(bare, "Change `existing_helper`")["known_reds"]["status"] == "absent"


def test_ac6_deterministic(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path, graph=True, ledger=_LEDGER)
    text = "Change `existing_helper` and `ghost_helper` in `pkg/mod.py`"
    a = json.dumps(fp.collect(repo, text), sort_keys=True)
    b = json.dumps(fp.collect(repo, text), sort_keys=True)
    assert a == b


# ─── op1: render ──────────────────────────────────────────────────────────────


_SECTIONS = ("SYMBOLS", "NOT FOUND IN REPO", "FILES", "EXISTING TESTS", "GRAPH", "KNOWN REDS")


def test_ac7_render_sections_and_audiences(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    pack = fp.collect(repo, "Change `existing_helper`")
    leads = set()
    for audience in ("spec", "red", "gate", "green", "review"):
        out = fp.render(pack, audience)
        assert out.startswith(fp.FACTS_HEADER), out[:120]
        for sec in _SECTIONS:
            assert sec in out, (audience, sec)
        assert "(none)" in out  # nothing unresolved, no graph, no ledger
        leads.add(out.splitlines()[1])
    assert len(leads) == 5, leads


def test_ac7b_render_is_capped(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    pack = fp.collect(repo, "x")
    pack["unresolved"] = [{"name": f"ghost_{i:04d}_" + "x" * 40, "near": []} for i in range(200)]
    out = fp.render(pack, "spec")
    assert len(out) <= 8000, len(out)
    assert out.rstrip().splitlines()[-1].strip() == "(truncated)"


# ─── op1: facts_block ─────────────────────────────────────────────────────────


def _record(monkeypatch, fp) -> list:
    events: list = []
    monkeypatch.setattr(fp, "_emit_safe", lambda name, payload: events.append((name, payload)))
    return events


def test_ac8_facts_block_caches(tmp_path, monkeypatch):
    fp = _fp()
    events = _record(monkeypatch, fp)
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    scratch.mkdir()
    out1 = fp.facts_block(scratch, repo, "Change `existing_helper`", "red")
    assert fp.FACTS_HEADER in out1
    files = list((scratch / "facts").glob("red-*.json"))
    assert len(files) == 1, files
    out2 = fp.facts_block(scratch, repo, "Change `existing_helper`", "red")
    assert out2 == out1
    collected = [p for n, p in events if n == "facts_pack_collected"]
    assert [p["cached"] for p in collected] == [False, True], collected
    assert collected[0]["audience"] == "red"


def test_ac8b_corrupt_cache_is_recollected(tmp_path, monkeypatch):
    fp = _fp()
    _record(monkeypatch, fp)
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    scratch.mkdir()
    fp.facts_block(scratch, repo, "Change `existing_helper`", "red")
    (cache,) = (scratch / "facts").glob("red-*.json")
    cache.write_text('{"version": 1, "sym', encoding="utf-8")
    out = fp.facts_block(scratch, repo, "Change `existing_helper`", "red")
    assert fp.FACTS_HEADER in out
    json.loads(cache.read_text(encoding="utf-8"))


def test_ac9_kill_switch(tmp_path, monkeypatch):
    fp = _fp()
    monkeypatch.setenv("HAL_FACTS_PACK", "0")
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    scratch.mkdir()
    assert fp.facts_block(scratch, repo, "Change `existing_helper`", "spec") == ""
    assert not (scratch / "facts").exists()


def test_ac10_failure_is_visible_not_fatal(tmp_path, monkeypatch):
    fp = _fp()
    events = _record(monkeypatch, fp)

    def boom(*a, **k):
        raise RuntimeError("walk exploded")

    monkeypatch.setattr(fp, "collect", boom)
    scratch = tmp_path / "s"
    scratch.mkdir()
    assert fp.facts_block(scratch, _repo(tmp_path), "Change `existing_helper`", "gate") == ""
    failed = [p for n, p in events if n == "facts_pack_failed"]
    assert failed and failed[0]["audience"] == "gate" and "walk exploded" in failed[0]["error"]


# ─── op2: wiring ──────────────────────────────────────────────────────────────


def _prompt(result: StepResult) -> str:
    assert result.status == "ok", result
    return result.data["prompt"]


def test_ac11_spec_writer_prompt_carries_facts(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path / "s", repo, question="Rename `ghost_helper` to use `existing_helper`")
    prompt = _prompt(phase_45_spec._build_spec_prompt(ctx, None))
    assert fp.FACTS_HEADER in prompt
    facts = prompt[prompt.index(fp.FACTS_HEADER):]
    not_found = facts[facts.index("NOT FOUND IN REPO"):]
    assert "ghost_helper" in not_found.split("FILES")[0]
    assert "pkg/mod.py:1" in facts
    contract = phase_45_spec._grounded_citation_contract()
    assert prompt.index(fp.FACTS_HEADER) < prompt.index(contract.splitlines()[0])


def test_ac12_red_prompt_carries_facts(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question=_SEEDLESS)
    _write_spec(scratch, _SPEC)
    prompt = _prompt(phase_5_implement._build_red_prompt(ctx, None))
    assert fp.FACTS_HEADER in prompt
    facts = prompt[prompt.index(fp.FACTS_HEADER):]
    assert "pkg/mod.py:1" in facts and "tests/test_mod.py" in facts


def _phase5_prev(scratch: Path, **extra) -> StepResult:
    spec = _write_spec(scratch, _SPEC)
    return StepResult(status="ok", data={
        "spec_path": str(spec),
        "red_log_path": str(scratch / "tests" / "build-red-output.log"),
        "red_test_paths": ["tests/test_mod.py"],
        "cycle": 1,
        **extra,
    }, duration_ms=0, step_name="prev")


def test_ac13_validation_prompt_carries_facts(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question=_SEEDLESS)
    prompt = _prompt(phase_5_implement._build_validation_prompt(ctx, _phase5_prev(scratch)))
    assert fp.FACTS_HEADER in prompt
    assert "pkg/mod.py:1" in prompt[prompt.index(fp.FACTS_HEADER):]


def test_ac14_green_prompt_carries_facts(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question=_SEEDLESS)
    val_doc = scratch / "validation" / "validation.md"
    val_doc.parent.mkdir(parents=True, exist_ok=True)
    val_doc.write_text("VERDICT: PASS\n")
    prompt = _prompt(phase_5_implement._build_green_prompt(
        ctx, _phase5_prev(scratch, validation_doc_path=str(val_doc)),
    ))
    assert fp.FACTS_HEADER in prompt
    assert "pkg/mod.py:1" in prompt[prompt.index(fp.FACTS_HEADER):]
    pack = fp.collect(repo, "x")
    green_lead = fp.render(pack, "green").splitlines()[1]
    assert green_lead in prompt


def test_ac15_review_prompt_carries_facts(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question=_SEEDLESS)
    _write_spec(scratch, _SPEC)
    prompt = _prompt(phase_6_review._build_review_prompt(ctx, None))
    assert fp.FACTS_HEADER in prompt
    assert "pkg/mod.py:1" in prompt[prompt.index(fp.FACTS_HEADER):]


# ─── op3: verify_spec_reality ─────────────────────────────────────────────────


def _reality(tmp_path, monkeypatch, spec_text: str, *, frozen: bool, events: list | None = None) -> StepResult:
    sink = events if events is not None else []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda name, payload: sink.append((name, payload)))
    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo)
    spec = _write_spec(scratch, spec_text)
    prev = StepResult(status="ok", data={
        "spec_path": str(spec), "is_frozen": frozen, "cycle": 1,
    }, duration_ms=0, step_name="verify_spec_ac_dsl")
    return phase_45_spec._verify_spec_reality(ctx, prev)


_GHOST_SPEC = (
    "## Context\n"
    "Call `ghost_helper` in `pkg/mod.py`.\n"
    "\n"
    "## Acceptance\n"
    "| AC | criterion |\n"
    "|---|---|\n"
    "| AC1 | `existing_helper` in `pkg/mod.py` returns x + 2 |\n"
)


def test_ac16_nonexistent_symbol_retries_the_writer(tmp_path, monkeypatch):
    events: list = []
    r = _reality(tmp_path, monkeypatch, _GHOST_SPEC, frozen=False, events=events)
    assert r.status == "error" and r.error_code == "E_SPEC_REALITY_FAIL", r
    assert r.recoverable is True, r
    assert r.data["retry_source"] == phase_45_spec.SPEC_GATES_RETRY_SOURCE
    assert "ghost_helper" in r.data["findings"]
    checked = [p for n, p in events if n == "spec_reality_checked"]
    assert checked and checked[-1]["status"] == "fail", events


def test_ac16b_finding_suggests_near_names(tmp_path, monkeypatch):
    spec = _GHOST_SPEC.replace("`ghost_helper`", "`existing_helpr`")
    r = _reality(tmp_path, monkeypatch, spec, frozen=True)
    assert r.error_code == "E_SPEC_REALITY_FAIL", r
    hit = [f for f in r.data["spec_reality_findings"] if "existing_helpr" in f]
    assert hit and "near: existing_helper" in hit[0], r.data["spec_reality_findings"]


def test_ac16c_abbreviated_path_is_not_invented(tmp_path, monkeypatch):
    ok = _SPEC.replace("`pkg/mod.py`", "`mod.py`")
    assert _reality(tmp_path / "a", monkeypatch, ok, frozen=True).status == "ok"
    bad = _GHOST_SPEC.replace("`pkg/mod.py`", "`mod.py`")
    r = _reality(tmp_path / "b", monkeypatch, bad, frozen=True)
    assert r.error_code == "E_SPEC_REALITY_FAIL", r
    assert any("ghost_helper" in f for f in r.data["spec_reality_findings"])


def test_ac17_frozen_spec_is_checked_and_terminal(tmp_path, monkeypatch):
    r = _reality(tmp_path, monkeypatch, _GHOST_SPEC, frozen=True)
    assert r.status == "error" and r.error_code == "E_SPEC_REALITY_FAIL", r
    assert r.recoverable is False, r
    assert r.data["spec_reality_findings"], r.data
    assert "ghost_helper" in r.error


def test_ac18_clean_spec_passes(tmp_path, monkeypatch):
    r = _reality(tmp_path, monkeypatch, _SPEC, frozen=True)
    assert r.status == "ok", r
    assert r.data["is_frozen"] is True and r.data["spec_path"].endswith("build-spec.md")


_MOCK_ONLY = (
    "## Context\n"
    "Change `existing_helper` in `pkg/mod.py`.\n"
    "\n"
    "## Acceptance\n"
    "| AC | criterion |\n"
    "|---|---|\n"
    "| AC1 | with `existing_helper` mocked, `mock_send` is called once |\n"
    "| AC2 | patch `existing_helper`; the MagicMock returns 3 |\n"
    "| AC3 | `fake_store` records one write |\n"
)


def test_ac19_all_mock_criteria_rejected(tmp_path, monkeypatch):
    r = _reality(tmp_path, monkeypatch, _MOCK_ONLY, frozen=True)
    assert r.status == "error" and r.error_code == "E_SPEC_REALITY_FAIL", r
    for ac in ("AC1", "AC2", "AC3"):
        assert ac in r.error, (ac, r.error)


def test_ac20_one_anchored_criterion_passes(tmp_path, monkeypatch):
    spec = _MOCK_ONLY + "| AC4 | `existing_helper` in `pkg/mod.py` returns x + 2 on real input |\n"
    r = _reality(tmp_path, monkeypatch, spec, frozen=True)
    assert r.status == "ok", r


def test_ac21_criterion_without_mock_word_is_anchored(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    spec = "## Acceptance\n| AC | c |\n|---|---|\n| AC1 | the output file contains three rows |\n"
    assert fp.unanchored_criteria(spec, repo) == []


def test_ac22_introduced_symbol_anchors_a_mock_criterion(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    spec = (
        "## Symbols this spec INTRODUCES\n"
        "- `new_runner`\n"
        "\n"
        "## Acceptance\n| AC | c |\n|---|---|\n"
        "| AC1 | with `existing_helper` mocked, `new_runner` returns 3 |\n"
    )
    assert fp.unanchored_criteria(spec, repo) == []
    undeclared = spec.split("## Acceptance")[1]
    got = fp.unanchored_criteria("## Acceptance" + undeclared, repo)
    assert [g["id"] for g in got] == ["AC1"], got
    mocked = "## Acceptance\n| AC | c |\n|---|---|\n| AC1 | patch `existing_helper`; it returns 3 |\n"
    got = fp.unanchored_criteria(mocked, repo)
    assert [g["id"] for g in got] == ["AC1"], got


def test_ac23_step_order():
    names = [s.name for s in phase_45_spec.phase_45_spec_workflow().steps]
    i = names.index("verify_spec_ac_dsl")
    assert names[i + 1] == "verify_spec_reality", names
    assert names.index("verify_spec_reality") < names.index("build_review_prompt")


def test_ac24_gate_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_REALITY_GATE", "0")
    r = _reality(tmp_path, monkeypatch, _GHOST_SPEC, frozen=True)
    assert r.status == "skip", r


def test_ac25_registries():
    from bytedigger_engine import error_codes, flags_catalog  # noqa: PLC0415

    assert "E_SPEC_REALITY_FAIL" in error_codes.ERROR_CODES
    for flag in ("HAL_FACTS_PACK", "HAL_SPEC_REALITY_GATE"):
        assert flags_catalog.FLAGS[flag]["default"] == "1", flag
    manifest = json.loads((_ENGINE_ROOT / "core_manifest.json").read_text(encoding="utf-8"))
    assert "facts_pack.py" in json.dumps(manifest)
    _fp()


def test_ac22b_mock_window(tmp_path):
    fp = _fp()
    repo = _repo(tmp_path)
    head = "## Acceptance\n| AC | c |\n|---|---|\n"
    # a participle does not reach forward: `other` stays an unmocked, real anchor
    assert fp.unanchored_criteria(head + "| AC1 | with `existing_helper` mocked, `other` returns 3 |\n", repo) == []
    got = fp.unanchored_criteria(head + "| AC1 | patch `existing_helper` and assert 3 |\n", repo)
    assert got and got[0]["mocked"] == ["existing_helper"], got
    got = fp.unanchored_criteria(head + "| AC1 | `fake_store` holds one row |\n", repo)
    assert [g["id"] for g in got] == ["AC1"], got
