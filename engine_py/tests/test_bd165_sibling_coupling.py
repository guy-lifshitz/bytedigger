"""RED tests for bd#165 -- port of the seven-channel section-1a sibling-coupling detector.

Spec: docs/decisions/2026-10-02-bd165-sibling-coupling-port.md (section 4).
Oracle for the P group: HAL sibling-test-audit-scope-coupling.test.ts (AC ids kept).

Every P test drives the CLI as a subprocess over tests/fixtures/sibling_coupling/.
Fails at ASSERT time: nothing here imports bytedigger_engine.sibling_coupling at
module scope (it does not exist yet); only paths/constants live at top level.
Determinism (workflows.md 1i): every scope run passes --test-glob or --corpus-root
at a pre-staged tree; no graph unless a test names one; tmp roots are realpath'd.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE_DIR = str(Path(__file__).resolve().parent.parent)
TESTS_DIR = os.path.join(ENGINE_DIR, "tests")
FX = os.path.join(TESTS_DIR, "fixtures", "sibling_coupling")
TESTS_GLOB = os.path.join(FX, "tests", "test_*.py")
NOMATCH_GLOB = os.path.join(FX, "nomatch_*.py")

PROD_SOURCE_READ = os.path.join(FX, "prod", "source_read_target.py")
PROD_VALUE_CONST = os.path.join(FX, "prod", "value_const.py")
PROD_KNOWN_REDS = os.path.join(FX, "prod", "known_reds_fixture.md")
PROD_CALLEE = os.path.join(FX, "prod", "callee_mod.py")
PROD_CLEAN = os.path.join(FX, "prod", "clean_target.py")
PROD_CLI_TARGET = os.path.join(FX, "prod", "cli_target.sh")
PROD_OPAQUE = os.path.join(FX, "prod", "opaque.tsx")
PROD_OPAQUE_SWIFT = os.path.join(FX, "prod", "opaque.swift")
PROD_MIXED_LITERAL = os.path.join(FX, "prod", "mixed_literal.py")
SPEC_COMPLETE = os.path.join(FX, "spec_complete.md")
SPEC_INCOMPLETE = os.path.join(FX, "spec_incomplete.md")
GRAPH_ZERO_EDGES = os.path.join(FX, "graph_zero_edges.json")

MODULE_PATH = os.path.join(ENGINE_DIR, "bytedigger_engine", "sibling_coupling.py")
PHASE5_PATH = os.path.join(ENGINE_DIR, "bytedigger_engine", "workflows", "phase_5_implement.py")

CHANNEL_NAMES = [
    "import", "source-read", "exec-invocation", "path-literal",
    "value-literal", "data-cell", "call-site",
]
PUBLIC_LIB_NAMES = [
    "extract_constant_values", "extract_data_cells", "extract_defined_symbols",
    "detect_source_read", "detect_imports", "detect_call_sites",
    "reconcile_with_spec", "collect_test_corpus", "classify_path_family",
    "is_distinctive_key", "grep_keys_batched",
]
F_FILE, F_LINE, F_TOKEN, F_CHANNEL, F_VERDICT = 0, 1, 3, 4, 5

ALL_SCOPE_FLAGS = [
    "--scope-file", PROD_SOURCE_READ,
    "--scope-file", PROD_VALUE_CONST,
    "--scope-file", PROD_KNOWN_REDS,
    "--scope-file", PROD_CALLEE,
    "--scope-file", PROD_CLEAN,
]


def _cli(args, cwd=ENGINE_DIR, pythonpath=ENGINE_DIR, env_extra=None, path=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    if path is not None:
        env["PATH"] = path
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.sibling_coupling"] + list(args),
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )


def _rows(proc):
    return [ln.split("\t") for ln in proc.stdout.split("\n") if ln.strip() != ""]


def _base(p):
    return p.split("/")[-1]


def _files(rows):
    return sorted({_base(r[F_FILE]) for r in rows})


# ---------------------------------------------------------------- P group

def test_p01_source_read_row_for_test_source_read():
    r = _cli(["--scope-file", PROD_SOURCE_READ, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if f[F_CHANNEL] == "source-read" and _base(f[F_FILE]) == "test_source_read.py"]
    assert len(hits) >= 1
    assert "source_read_target.py" in hits[0][F_TOKEN]


def test_p02_value_literal_row_for_test_value_pin():
    r = _cli(["--scope-file", PROD_VALUE_CONST, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if f[F_CHANNEL] == "value-literal" and _base(f[F_FILE]) == "test_value_pin.py"]
    assert len(hits) >= 1
    assert any(f[F_TOKEN].startswith("_PIP_EXTRA_HINT=") for f in hits)


def test_p03_interior_segment_pin_still_emitted():
    r = _cli(["--scope-file", PROD_VALUE_CONST, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if f[F_CHANNEL] == "value-literal" and _base(f[F_FILE]) == "test_value_pin_segment.py"]
    assert len(hits) >= 1
    seg = [f for f in hits if f[F_TOKEN].startswith("_PIP_EXTRA_HINT=")]
    assert len(seg) >= 1
    assert all("install the anthropic" not in f[F_TOKEN] for f in seg)


def test_p04_data_cell_row_exact_token():
    r = _cli(["--scope-file", PROD_KNOWN_REDS, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if f[F_CHANNEL] == "data-cell"]
    assert len(hits) >= 1
    cells = [f for f in hits if f[F_TOKEN] == "gh1165-oracle-baseline-cell"]
    assert len(cells) >= 1
    assert "test_data_cell.py" in [_base(f[F_FILE]) for f in cells]


def test_p05_call_site_true_callers_only():
    r = _cli(["--scope-file", PROD_CALLEE, "--channels", "call-site", "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    rows = _rows(r)
    assert len(rows) >= 3
    assert all(f[F_CHANNEL] == "call-site" for f in rows)
    assert all(f[F_TOKEN] == "resolve_thing" for f in rows)
    files = _files(rows)
    assert files == ["test_caller_a.py", "test_caller_b.py", "test_caller_c.py"]
    for nc in ("test_noncaller_a.py", "test_noncaller_b.py", "test_noncaller_c.py"):
        assert nc not in files


def test_p06_dotless_import_channel_token():
    r = _cli(["--scope-file", PROD_CALLEE, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if f[F_CHANNEL] == "import" and _base(f[F_FILE]) == "test_import_only.py"]
    assert len(hits) >= 1
    assert hits[0][F_TOKEN] == "callee_mod"


def test_p07_spec_reconcile_cited_vs_missing():
    r = _cli([
        "--scope-file", PROD_SOURCE_READ, "--scope-file", PROD_VALUE_CONST,
        "--spec", SPEC_INCOMPLETE, "--test-glob", TESTS_GLOB,
    ])
    assert r.returncode == 0, r.stderr
    rows = _rows(r)
    cited = [f for f in rows if _base(f[F_FILE]) == "test_source_read.py"]
    assert len(cited) >= 1
    assert all(f[F_VERDICT] == "cited" for f in cited)
    missing = [f for f in rows if f[F_CHANNEL] == "value-literal" and _base(f[F_FILE]) == "test_value_pin.py"]
    assert len(missing) >= 1
    assert all(f[F_VERDICT] == "MISSING" for f in missing)


def test_p08_complete_spec_require_clean_is_clean():
    r = _cli(ALL_SCOPE_FLAGS + ["--spec", SPEC_COMPLETE, "--require-clean", "--test-glob", TESTS_GLOB])
    rows = _rows(r)
    assert len(rows) >= 1
    assert [f for f in rows if f[F_VERDICT] == "MISSING"] == []
    assert r.returncode == 0, r.stderr


def test_p09_incomplete_spec_require_clean_exit_1():
    r = _cli([
        "--scope-file", PROD_SOURCE_READ, "--scope-file", PROD_VALUE_CONST,
        "--spec", SPEC_INCOMPLETE, "--require-clean", "--test-glob", TESTS_GLOB,
    ])
    assert "No module named" not in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert any(f[F_VERDICT] == "MISSING" for f in _rows(r) if len(f) == 6)
    assert r.returncode == 1, r.stderr


def test_p10_clean_scope_file_silent_with_positive_control():
    plain = _cli(["--scope-file", PROD_CLEAN, "--test-glob", TESTS_GLOB])
    assert plain.returncode == 0, plain.stderr
    assert _rows(plain) == []
    gated = _cli(["--scope-file", PROD_CLEAN, "--require-clean", "--test-glob", TESTS_GLOB])
    assert gated.returncode == 0, gated.stderr
    assert _rows(gated) == []
    control = _cli(["--scope-file", PROD_CALLEE, "--test-glob", TESTS_GLOB])
    assert control.returncode == 0, control.stderr
    assert len(_rows(control)) >= 1


def test_p11_channels_filter_value_literal():
    r = _cli(ALL_SCOPE_FLAGS + ["--channels", "value-literal", "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    rows = _rows(r)
    assert len(rows) >= 1
    assert all(f[F_CHANNEL] == "value-literal" for f in rows)


def test_p12_unknown_channel_fail_closed_enumerates():
    r = _cli(["--scope-file", PROD_VALUE_CONST, "--channels", "bogus", "--test-glob", TESTS_GLOB])
    assert r.returncode == 2
    assert "E_UNKNOWN_CHANNEL" in r.stderr
    for name in CHANNEL_NAMES:
        assert name in r.stderr


def test_p13_unreadable_scope_file_named():
    r = _cli(["--scope-file", "/nonexistent/gh1200/x.py", "--test-glob", TESTS_GLOB])
    assert r.returncode == 2
    assert "E_SCOPE_FILE_UNREADABLE" in r.stderr


def test_p15_help_names_flags_and_channels():
    r = _cli(["--help"])
    assert r.returncode == 0, r.stderr
    flags = [
        "--scope-file", "--test-glob", "--channels", "--spec", "--min-literal-len",
        "--max-keys", "--max-corpus", "--max-key-files", "--require-clean",
        "--corpus-root", "--graph", "--trace", "--json",
    ]
    for flag in flags:
        assert flag in r.stdout
    for name in CHANNEL_NAMES:
        assert name in r.stdout


def test_p16_row_shape_sorted_deduped():
    r = _cli(ALL_SCOPE_FLAGS + ["--test-glob", TESTS_GLOB], env_extra={"LC_ALL": "C"})
    assert r.returncode == 0, r.stderr
    rows = _rows(r)
    assert len(rows) >= 2
    for f in rows:
        assert len(f) == 6
        int(f[F_LINE])
    for prev, cur in zip(rows, rows[1:]):
        assert prev[F_FILE] <= cur[F_FILE]
        if prev[F_FILE] == cur[F_FILE]:
            assert int(cur[F_LINE]) >= int(prev[F_LINE])
    keys = [(f[F_FILE], f[F_LINE], f[F_CHANNEL], f[F_TOKEN]) for f in rows]
    assert len(set(keys)) == len(keys)


def test_p18_noise_floor_min_literal_len():
    default = _cli(["--scope-file", PROD_VALUE_CONST, "--channels", "value-literal", "--test-glob", TESTS_GLOB])
    assert default.returncode == 0, default.stderr
    assert [f for f in _rows(default) if f[F_TOKEN].startswith("STATE_OK=")] == []
    lowered = _cli([
        "--scope-file", PROD_VALUE_CONST, "--channels", "value-literal",
        "--min-literal-len", "2", "--test-glob", TESTS_GLOB,
    ])
    assert lowered.returncode == 0, lowered.stderr
    assert len([f for f in _rows(lowered) if f[F_TOKEN].startswith("STATE_OK=")]) >= 1


def test_p19_exec_invocation_row():
    r = _cli(["--scope-file", PROD_CLI_TARGET, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if f[F_CHANNEL] == "exec-invocation" and _base(f[F_FILE]) == "test_exec_invoke.py"]
    assert len(hits) >= 1
    assert "cli_target.sh" in hits[0][F_TOKEN]


def test_p20_bare_path_literal_not_silent():
    r = _cli(["--scope-file", PROD_CLI_TARGET, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if _base(f[F_FILE]) == "test_path_mention_only.py"]
    assert len(hits) >= 1
    assert any(f[F_CHANNEL] == "path-literal" for f in hits)


def test_p21_dogfood_own_scope_file_finds_this_test():
    r = _cli(["--scope-file", MODULE_PATH, "--corpus-root", TESTS_DIR])
    assert r.returncode == 0, r.stderr
    assert "test_bd165_sibling_coupling.py" in _files(_rows(r))


def test_p22_module_object_source_read():
    r = _cli(["--scope-file", PROD_CALLEE, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    hits = [f for f in _rows(r) if f[F_CHANNEL] == "source-read" and _base(f[F_FILE]) == "test_module_file_read.py"]
    assert len(hits) >= 1
    assert any(f[F_TOKEN] == "callee_mod.__file__" for f in hits)


def test_p23_empty_corpus_is_loud():
    r = _cli(["--scope-file", PROD_VALUE_CONST, "--test-glob", NOMATCH_GLOB])
    assert r.returncode == 2
    assert "E_NO_TEST_CORPUS" in r.stderr


def test_p24_no_extractor_warn_and_partial_gate():
    warn = _cli(["--scope-file", PROD_OPAQUE, "--channels", "data-cell", "--test-glob", TESTS_GLOB])
    assert warn.returncode == 0, warn.stderr
    assert "W_NO_EXTRACTOR" in warn.stderr
    gated = _cli([
        "--scope-file", PROD_OPAQUE, "--channels", "data-cell",
        "--test-glob", TESTS_GLOB, "--require-clean",
    ])
    assert gated.returncode == 2
    assert "E_PARTIAL_CHANNELS_GATE" in gated.stderr
    assert "W_NO_EXTRACTOR" in gated.stderr


def test_p27_key_cap_loud_and_gates():
    warn = _cli([
        "--scope-file", PROD_VALUE_CONST, "--channels", "value-literal",
        "--max-keys", "1", "--test-glob", TESTS_GLOB,
    ])
    assert warn.returncode == 0, warn.stderr
    assert "W_KEY_CAP" in warn.stderr
    gated = _cli([
        "--scope-file", PROD_VALUE_CONST, "--max-keys", "1",
        "--test-glob", TESTS_GLOB, "--require-clean",
    ])
    assert gated.returncode == 2
    assert "W_KEY_CAP" in gated.stderr
    assert "E_PARTIAL_CHANNELS_GATE" not in gated.stderr


def test_p28_partial_channel_set_never_passes_gate():
    r = _cli([
        "--scope-file", PROD_VALUE_CONST, "--channels", "value-literal",
        "--require-clean", "--test-glob", TESTS_GLOB,
    ])
    assert r.returncode == 2
    assert "E_PARTIAL_CHANNELS_GATE" in r.stderr


def test_p30_tests_to_spec_direction_uncoupled_warning():
    r = _cli(ALL_SCOPE_FLAGS + ["--spec", SPEC_COMPLETE, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    assert "W_SPEC_UNCOUPLED" in r.stderr
    assert "test_noncaller_a.py" in r.stderr


def test_p31_default_corpus_walk_over_staged_tree(tmp_path):
    root = os.path.realpath(str(tmp_path))
    os.makedirs(os.path.join(root, "__tests__"))
    os.makedirs(os.path.join(root, "node_modules"))
    scope = os.path.join(root, "prod_target.py")
    Path(scope).write_text('MARKER_VALUE = "gh1200-corpus-marker"\n')
    mention = "reference to prod_target.py\n"
    Path(root, "test_root.py").write_text("# " + mention)
    Path(root, "__tests__", "inner.test.ts").write_text("// " + mention)
    Path(root, "outer.test.sh").write_text("# " + mention)
    Path(root, "node_modules", "test_skip.py").write_text("# " + mention)
    r = _cli(["--scope-file", scope, "--corpus-root", root])
    assert r.returncode == 0, r.stderr
    files = _files(_rows(r))
    assert "test_root.py" in files
    assert "inner.test.ts" in files
    assert "outer.test.sh" in files
    assert "test_skip.py" not in files


def test_p32_batched_scan_trace(tmp_path):
    trace = os.path.join(os.path.realpath(str(tmp_path)), "trace.log")
    channels = ["import", "source-read", "value-literal"]
    r = _cli([
        "--scope-file", PROD_VALUE_CONST, "--scope-file", PROD_CALLEE,
        "--channels", ",".join(channels), "--test-glob", TESTS_GLOB, "--trace", trace,
    ])
    assert r.returncode == 0, r.stderr
    assert os.path.exists(trace)
    lines = [ln for ln in Path(trace).read_text().split("\n") if ln.strip() != ""]
    assert 1 <= len(lines) <= 3
    key_counts = []
    by_channel = {}
    for ln in lines:
        m = re.match(r"^grep (\S+) keys=(\d+) files=(\d+)$", ln.strip())
        assert m is not None, ln
        assert m.group(1) in channels
        assert int(m.group(3)) >= 1
        n = int(m.group(2))
        key_counts.append(n)
        by_channel[m.group(1)] = by_channel.get(m.group(1), 0) + n
    assert max(key_counts) >= 2
    assert by_channel.get("import") == 2
    assert sum(key_counts) >= 2
    assert sum(key_counts) >= len(lines)


def test_p33_dropped_key_named_on_stderr():
    r = _cli(["--scope-file", PROD_KNOWN_REDS, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    rows = _rows(r)
    assert len([f for f in rows if f[F_TOKEN] == "gh1165-oracle-baseline-cell"]) >= 1
    assert [f for f in rows if f[F_TOKEN] == "pytest"] == []
    assert "W_KEY_NONDISTINCTIVE pytest" not in r.stderr
    assert "W_KEY_DROPPED pytest" in r.stderr


def test_p35_eleven_public_names_callable():
    code = "\n".join([
        "import bytedigger_engine.sibling_coupling as m",
        "names = %s" % json.dumps(PUBLIC_LIB_NAMES),
        "missing = [n for n in names if not callable(getattr(m, n, None))]",
        "print('MISSING:' + ','.join(missing))",
    ])
    env = dict(os.environ)
    env["PYTHONPATH"] = ENGINE_DIR
    r = subprocess.run([sys.executable, "-c", code], cwd=ENGINE_DIR, env=env,
                       capture_output=True, text=True, timeout=60)
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 0
    assert r.stdout.strip() == "MISSING:"


def test_p36_zero_edge_graph_does_not_suppress_grep():
    r = _cli([
        "--scope-file", PROD_CALLEE, "--channels", "call-site",
        "--test-glob", TESTS_GLOB, "--graph", GRAPH_ZERO_EDGES,
    ])
    assert r.returncode == 0, r.stderr
    assert _files(_rows(r)) == ["test_caller_a.py", "test_caller_b.py", "test_caller_c.py"]


def test_p37_unsupported_extension_fail_closed():
    warn = _cli(["--scope-file", PROD_OPAQUE_SWIFT, "--test-glob", TESTS_GLOB])
    assert warn.returncode == 0, warn.stderr
    assert "W_UNSUPPORTED_SCOPE_EXT" in warn.stderr
    assert "opaque.swift" in warn.stderr
    gated = _cli(["--scope-file", PROD_OPAQUE_SWIFT, "--test-glob", TESTS_GLOB, "--require-clean"])
    assert gated.returncode == 2
    assert "W_UNSUPPORTED_SCOPE_EXT" in gated.stderr
    assert "E_PARTIAL_CHANNELS_GATE" not in gated.stderr


def test_p38_corpus_cap_loud_and_gates(tmp_path):
    root = os.path.realpath(str(tmp_path))
    scope = os.path.join(root, "capped_target.py")
    Path(scope).write_text('CAP_MARKER = "gh1200-corpus-cap-marker"\n')
    for name in ("test_cap_a.py", "test_cap_b.py", "test_cap_c.py"):
        Path(root, name).write_text("# mentions capped_target.py once\n")
    cap_args = ["--scope-file", scope, "--max-corpus", "2", "--corpus-root", root]
    warn = _cli(cap_args)
    assert warn.returncode == 0, warn.stderr
    assert "W_CORPUS_CAP" in warn.stderr
    assert len(_rows(warn)) >= 1
    gated = _cli(cap_args + ["--require-clean"])
    assert gated.returncode == 2
    assert "W_CORPUS_CAP" in gated.stderr
    assert "E_PARTIAL_CHANNELS_GATE" not in gated.stderr
    uncapped = _cli(["--scope-file", scope, "--corpus-root", root])
    assert uncapped.returncode == 0, uncapped.stderr
    assert "W_CORPUS_CAP" not in uncapped.stderr
    assert len(_files(_rows(uncapped))) == 3


def test_p39_partially_literal_constant():
    r = _cli(["--scope-file", PROD_MIXED_LITERAL, "--channels", "value-literal", "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    rows = _rows(r)
    hits = [f for f in rows if f[F_CHANNEL] == "value-literal" and _base(f[F_FILE]) == "test_mixed_literal_pin.py"]
    assert len(hits) >= 1
    assert any(f[F_TOKEN].startswith("MIXED_HINT=") for f in hits)
    assert "W_PARTIAL_LITERAL MIXED_HINT" in r.stderr
    assert all("_suffix" not in f[F_TOKEN] for f in rows)
    assert all("computed-at-runtime" not in f[F_TOKEN] for f in rows)


def test_p40_max_key_files_nondistinctive_drop():
    argv = ["--scope-file", PROD_VALUE_CONST, "--channels", "value-literal", "--test-glob", TESTS_GLOB]
    baseline = _cli(argv)
    assert baseline.returncode == 0, baseline.stderr
    base_files = _files(_rows(baseline))
    assert "test_value_pin.py" in base_files
    assert "test_value_pin_segment.py" in base_files
    assert "W_KEY_NONDISTINCTIVE" not in baseline.stderr
    capped = _cli(argv + ["--max-key-files", "1"])
    assert capped.returncode == 0, capped.stderr
    assert "W_KEY_NONDISTINCTIVE" in capped.stderr
    capped_files = _files(_rows(capped))
    assert "test_value_pin.py" in capped_files
    assert "test_value_pin_segment.py" not in capped_files


def test_p42_exec_beats_read_one_path_family_row():
    path_family = ["exec-invocation", "source-read", "path-literal"]
    r = _cli(["--scope-file", PROD_CLI_TARGET, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    rows = _rows(r)
    mine = [f for f in rows if _base(f[F_FILE]) == "test_exec_and_read.py" and f[F_CHANNEL] in path_family]
    assert len(mine) == 1
    assert mine[0][F_CHANNEL] == "exec-invocation"
    assert "cli_target.sh" in mine[0][F_TOKEN]
    bare = [f for f in rows if _base(f[F_FILE]) == "test_path_mention_only.py"]
    assert len(bare) >= 1
    assert any(f[F_CHANNEL] == "path-literal" for f in bare)


def test_p43_import_channel_no_prose_overdetect():
    r = _cli(["--scope-file", PROD_CALLEE, "--test-glob", TESTS_GLOB])
    assert r.returncode == 0, r.stderr
    import_files = _files([f for f in _rows(r) if f[F_CHANNEL] == "import"])
    assert "test_import_only.py" in import_files
    assert "test_mentions_module_prose.py" not in import_files


# ---------------------------------------------------------------- B group

def test_b1_installed_layout_outside_git(tmp_path):
    tp = os.path.realpath(str(tmp_path))
    site = os.path.join(tp, "site")
    os.makedirs(site)
    shutil.copytree(
        os.path.join(ENGINE_DIR, "bytedigger_engine"),
        os.path.join(site, "bytedigger_engine"),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    r = _cli(["--scope-file", PROD_VALUE_CONST, "--test-glob", TESTS_GLOB], cwd=tp, pythonpath=site)
    assert r.returncode == 0, r.stderr
    assert any(f[F_CHANNEL] == "value-literal" for f in _rows(r))
    env = dict(os.environ)
    env["PYTHONPATH"] = site
    h = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.run", "sibling-audit", "--help"],
        cwd=tp, env=env, capture_output=True, text=True, timeout=60,
    )
    assert h.returncode == 0, h.stderr
    assert "--scope-file" in h.stdout


def test_b2_json_output_matches_tsv():
    base_args = ["--scope-file", PROD_VALUE_CONST, "--test-glob", TESTS_GLOB]
    tsv = _cli(base_args)
    js = _cli(base_args + ["--json"])
    assert tsv.returncode == 0, tsv.stderr
    assert js.returncode == 0, js.stderr
    obj = json.loads(js.stdout)
    assert obj["schema"] == 1
    json_rows = [tuple(str(row[k]) for k in ("file", "line", "func", "token", "channel", "verdict"))
                 for row in obj["rows"]]
    tsv_rows = [tuple(f) for f in _rows(tsv)]
    assert len(tsv_rows) >= 1
    assert json_rows == tsv_rows
    assert obj["exit"] == 0
    gated = _cli([
        "--scope-file", PROD_SOURCE_READ, "--scope-file", PROD_VALUE_CONST,
        "--spec", SPEC_INCOMPLETE, "--require-clean", "--json", "--test-glob", TESTS_GLOB,
    ])
    assert gated.returncode == 1, gated.stderr
    gobj = json.loads(gated.stdout)
    assert gobj["exit"] == 1
    assert gobj["missing"] >= 1


def test_b3_graph_optional_call_site():
    base_args = ["--scope-file", PROD_CALLEE, "--channels", "call-site", "--test-glob", TESTS_GLOB, "--json"]
    nog = _cli(base_args)
    assert nog.returncode == 0, nog.stderr
    assert json.loads(nog.stdout)["call_site"] == {"source": "grep", "reason": "no_graph"}

    bad = _cli(base_args + ["--graph", "/nonexistent.json"])
    assert bad.returncode == 0, bad.stderr
    assert json.loads(bad.stdout)["call_site"] == {"source": "grep", "reason": "graph_unreadable"}
    assert "W_GRAPH_UNREADABLE" in bad.stderr

    clean = _cli([
        "--scope-file", PROD_CLEAN, "--test-glob", TESTS_GLOB,
        "--graph", "/nonexistent.json", "--require-clean",
    ])
    assert clean.returncode == 0, clean.stderr

    zero = _cli(base_args + ["--graph", GRAPH_ZERO_EDGES])
    assert zero.returncode == 0, zero.stderr
    zobj = json.loads(zero.stdout)
    assert zobj["call_site"]["source"] == "graph+grep"
    assert _files([[r["file"], r["line"], r["func"], r["token"], r["channel"], r["verdict"]]
                   for r in zobj["rows"]]) == ["test_caller_a.py", "test_caller_b.py", "test_caller_c.py"]


def test_b4_no_host_binary_on_path(tmp_path):
    empty = os.path.join(os.path.realpath(str(tmp_path)), "emptybin")
    os.makedirs(empty)
    args = ["--scope-file", PROD_VALUE_CONST, "--test-glob", TESTS_GLOB]
    normal = _cli(args)
    bare = _cli(args, path=empty)
    assert normal.returncode == 0, normal.stderr
    assert bare.returncode == 0, bare.stderr
    assert len(_rows(normal)) >= 1
    assert _rows(bare) == _rows(normal)


# ---- B5: phase-5 helper (in-process) --------------------------------------

_DISTINCT = "bd165-distinctive-banner-value"


def _git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, timeout=60)


def _stage_repo(tmp_path, spec_text):
    root = os.path.realpath(str(tmp_path))
    os.makedirs(os.path.join(root, "pkg"))
    os.makedirs(os.path.join(root, "tests"))
    Path(root, "pkg", "mod.py").write_text('PINNED_BANNER = "%s"\n' % _DISTINCT)
    Path(root, "tests", "test_pinner.py").write_text(
        'def test_pin():\n    assert "%s"\n' % _DISTINCT)
    Path(root, "tests", "test_new.py").write_text(
        'def test_new():\n    assert "%s"\n' % _DISTINCT)
    spec = os.path.join(root, "spec.md")
    Path(spec).write_text(spec_text)
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return root, spec


_FILES_BOTH = "## Files\n\n- `pkg/mod.py`\n- `tests/test_new.py`\n"
_SPEC_UNCITED = "# spec\n\n" + _FILES_BOTH
_SPEC_CITED = "# spec\n\n" + _FILES_BOTH + "\n## Sibling tests\n\n- `tests/test_pinner.py`\n"
_SPEC_TESTS_ONLY = "# spec\n\n## Files\n\n- `tests/test_new.py`\n"


def _run_helper(monkeypatch, root, spec_path):
    from bytedigger_engine.workflows import phase_5_implement as p5
    events = []

    def fake_emit(event_type, payload, severity="warning"):
        events.append((event_type, payload, severity))

    monkeypatch.setattr(p5, "_emit_safe", fake_emit)
    monkeypatch.delenv("HAL_SIBLING_AUDIT_BIN", raising=False)
    ret = p5._sibling_audit_warn([os.path.join(root, "tests", "test_new.py")], root, spec_path=spec_path)
    return ret, [e for e in events if e[0].startswith("red_sibling_audit")]


def test_b5a_uncited_pinner_yields_one_warn(tmp_path, monkeypatch):
    root, spec = _stage_repo(tmp_path, _SPEC_UNCITED)
    ret, events = _run_helper(monkeypatch, root, spec)
    assert ret is None
    warns = [e for e in events if e[0] == "red_sibling_audit_warn"]
    assert len(warns) == 1
    payload = warns[0][1]
    assert payload["count"] >= 1
    hits = payload["hits"]
    pinner = [h for h in hits if "tests/test_pinner.py" in h.split("\t")[0]]
    assert len(pinner) >= 1
    assert all(h.split("\t")[-1] == "MISSING" for h in pinner)
    assert not any("tests/test_new.py" in h.split("\t")[0] for h in hits)


def test_b5b_cited_pinner_yields_clean(tmp_path, monkeypatch):
    root, spec = _stage_repo(tmp_path, _SPEC_CITED)
    ret, events = _run_helper(monkeypatch, root, spec)
    assert ret is None
    kinds = [e[0] for e in events]
    assert "red_sibling_audit_clean" in kinds
    assert "red_sibling_audit_warn" not in kinds


def test_b5c_no_spec_path_skipped(tmp_path, monkeypatch):
    root, _spec = _stage_repo(tmp_path, _SPEC_UNCITED)
    ret, events = _run_helper(monkeypatch, root, None)
    assert ret is None
    assert [(e[0], e[1].get("reason")) for e in events] == [("red_sibling_audit_skipped", "no_spec")]


def test_b5d_tests_only_files_section_no_scope_files(tmp_path, monkeypatch):
    root, spec = _stage_repo(tmp_path, _SPEC_TESTS_ONLY)
    ret, events = _run_helper(monkeypatch, root, spec)
    assert ret is None
    assert [(e[0], e[1].get("reason")) for e in events] == [("red_sibling_audit_skipped", "no_scope_files")]


def test_b5e_detector_exception_degrades_to_skipped(tmp_path, monkeypatch):
    root, spec = _stage_repo(tmp_path, _SPEC_UNCITED)
    from bytedigger_engine import sibling_coupling

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(sibling_coupling, "audit", boom)
    ret, events = _run_helper(monkeypatch, root, spec)
    assert ret is None
    assert len(events) == 1
    assert events[0][0] == "red_sibling_audit_skipped"
    assert events[0][1]["reason"] == "detector_error"
    assert events[0][1]["error"] == "RuntimeError"


def test_b5f_always_returns_none_never_script_missing(tmp_path_factory, monkeypatch):
    cases = [
        (_SPEC_UNCITED, True), (_SPEC_CITED, True),
        (_SPEC_UNCITED, False), (_SPEC_TESTS_ONLY, True),
    ]
    seen = 0
    for spec_text, use_spec in cases:
        root, spec = _stage_repo(tmp_path_factory.mktemp("b5f"), spec_text)
        ret, events = _run_helper(monkeypatch, root, spec if use_spec else None)
        assert ret is None
        assert len(events) >= 1
        for _name, payload, _sev in events:
            assert payload.get("reason") != "script_missing"
        seen += 1
    assert seen == len(cases)


# ---- B6 / B7: static --------------------------------------------------------

def test_b6a_phase5_and_flags_cleaned():
    from bytedigger_engine import flags_catalog
    src = Path(PHASE5_PATH).read_text(encoding="utf-8")
    for needle in ("sibling-test-audit.sh", "HAL_SIBLING_AUDIT_BIN", "_red_import_symbols"):
        assert needle not in src
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_sibling_audit_warn"]
    assert len(calls) >= 2
    for c in calls:
        assert "spec_path" in [k.arg for k in c.keywords]
    assert "HAL_SIBLING_AUDIT_BIN" not in flags_catalog.FLAGS


_STDLIB_FALLBACK = {
    "__future__", "argparse", "ast", "bisect", "collections", "dataclasses", "fnmatch",
    "functools", "glob", "io", "itertools", "json", "os", "pathlib", "re", "sys",
    "subprocess", "tokenize", "typing", "shlex", "textwrap", "string", "operator",
}


def test_b6b_module_is_stdlib_only_and_agnostic():
    src = Path(MODULE_PATH).read_text(encoding="utf-8")
    tree = ast.parse(src)
    stdlib = getattr(sys, "stdlib_module_names", None) or _STDLIB_FALLBACK
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in stdlib, a.name
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert (node.module or "").split(".")[0] in stdlib, node.module
            if node.module == "os":
                assert not any(a.name in ("environ", "getenv") for a in node.names)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                assert node.attr not in ("environ", "getenv")
    sub_calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    assert len(sub_calls) <= 1
    for c in sub_calls:
        assert "rev-parse" in ast.dump(c)
    assert re.search(r"(?i)anthropic|claude|openai|jev|pydantic|sonnet|opus|haiku", src) is None


def test_b7_core_manifest_and_escape_allowlist():
    manifest = json.loads(Path(ENGINE_DIR, "core_manifest.json").read_text(encoding="utf-8"))
    assert "sibling_coupling.py" in manifest["core_modules"]
    tree = ast.parse(Path(TESTS_DIR, "test_engine_path_closure.py").read_text(encoding="utf-8"))
    allow = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ESCAPE_ALLOWLIST" for t in node.targets):
            allow = ast.literal_eval(node.value)
    assert allow is not None
    assert "sibling-test-audit.sh" not in allow
