"""RED tests for bd#90 - phase_6 _run_pytest_post_fix scope is never empty.

Spec: docs/decisions/2026-10-03-bd90-postfix-test-scope.md (AC1-AC10).

The scope is the ordered, de-duplicated union of RED paths (prev.data list, else
the persisted integrity/red-test-paths.txt), manifest test files, and sibling
tracked tests of changed source files (max 50, sorted). The on-disk existence
filter applies only to the persisted RED leg and to siblings; the prev.data list
and manifest legs pass through unchanged. A `post_fix_pytest_scope` event is emitted with
{n_red, n_manifest, n_sibling, n_total} before pytest is invoked.

Hermetic: tmp_path, a real tiny git repo (git init + git add, no commits needed),
run_test_command and _emit_safe stubbed. No sys.path mutation.
"""
from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

from bytedigger_engine.workflows import phase_6_review

_VALID_SHA = "a" * 40
_SCOPE_EVENT = "post_fix_pytest_scope"


@dataclass
class _FakeRun:
    exit_code: int
    n_passed: int
    n_failed: int
    stdout_path: str


class _Env:
    """A tiny git repo + scratchpad with stubbed pytest runner and event capture."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.scratch = tmp_path / "scratch"
        (self.scratch / "reviews").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)
        self.events = []  # type: list
        self.run_calls = []  # type: list
        for var in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE"):
            monkeypatch.delenv(var, raising=False)
        stdout = tmp_path / "stdout.txt"
        stdout.write_text("1 passed in 0.1s")

        def _fake_run(argv, cwd, timeout=180):
            self.run_calls.append(list(argv))
            return _FakeRun(exit_code=0, n_passed=1, n_failed=0, stdout_path=str(stdout))

        monkeypatch.setattr(phase_6_review, "run_test_command", _fake_run)
        monkeypatch.setattr(
            phase_6_review, "_emit_safe",
            lambda et, p, **kw: self.events.append({"type": et, "payload": p}),
        )

    def write(self, rel: str, tracked: bool = True) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# fixture\n")
        if tracked:
            subprocess.run(["git", "add", rel], cwd=str(self.repo), check=True)

    def persist_red(self, lines) -> None:
        f = self.scratch / "integrity" / "red-test-paths.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines))

    def ctx(self):
        from bytedigger_engine.contracts import WorkflowContext
        return WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={"scratchpad_dir": str(self.scratch), "git_cwd": str(self.repo)},
            question="bd90", session_id="test-session-bd90", persona="hal",
            framework=None, domain=None,
        )

    def prev(self, manifest, **extra):
        prev = MagicMock()
        prev.data = {
            "cycle": 1, "pre_fix_sha": _VALID_SHA,
            "worker_written_paths": list(manifest),
            "manifest_source": "harness_tool_record",
            **extra,
        }
        return prev

    def run(self, manifest, **extra):
        return phase_6_review._run_pytest_post_fix(self.ctx(), self.prev(manifest, **extra))

    def scope_events(self):
        return [e["payload"] for e in self.events if e["type"] == _SCOPE_EVENT]

    def test_args(self):
        """Test file paths in the single pytest argv (test_*.py / *_test.py only)."""
        assert len(self.run_calls) == 1, "pytest must be invoked exactly once: %r" % (self.run_calls,)
        out = []
        for a in self.run_calls[0]:
            base = a.replace("\\", "/").rsplit("/", 1)[-1]
            if fnmatch.fnmatch(base, "test_*.py") or fnmatch.fnmatch(base, "*_test.py"):
                out.append(a)
        return out


def _has(args, rel: str) -> bool:
    return any(a == rel or a.replace("\\", "/").endswith("/" + rel) for a in args)


class TestPostFixTestScope:

    def test_ac1_red_path_from_persisted_file_joins_scope(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_red_one.py")
        env.persist_red(["tests/test_red_one.py"])

        result = env.run(["pkg/module.py"])

        assert result.status == "ok"
        assert _has(env.test_args(), "tests/test_red_one.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, "expected one post_fix_pytest_scope event: %r" % (env.events,)
        assert scopes[0]["n_red"] >= 1
        for key in ("n_red", "n_manifest", "n_sibling", "n_total"):
            assert key in scopes[0], scopes[0]

    def test_ac2_sibling_tests_pulled_in_for_changed_source(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_module_extra.py")   # stem match, different directory
        env.write("pkg/test_neighbor.py")      # same directory
        env.write("tests/test_unrelated.py")   # must NOT be pulled in

        result = env.run(["pkg/module.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_module_extra.py"), env.run_calls
        assert _has(args, "pkg/test_neighbor.py"), env.run_calls
        assert not _has(args, "tests/test_unrelated.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 2
        assert scopes[0]["n_total"] == 2

    def test_ac2_siblings_capped_at_50_sorted(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        names = ["pkg/test_c%02d.py" % i for i in range(60)]
        for n in names:
            env.write(n)

        result = env.run(["pkg/module.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert len(args) == 50, "expected 50 capped sibling paths, got %d" % len(args)
        for n in names[:50]:
            assert _has(args, n), "sorted first-50 sibling missing: %s" % n
        assert not _has(args, names[50]), "sibling beyond the cap must be dropped"
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 50
        assert scopes[0]["n_total"] == 50

    def test_ac3_empty_scope_skips_without_running_pytest(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")  # no RED file, no manifest tests, no siblings

        result = env.run(["pkg/module.py"])

        assert result.status == "ok"
        assert env.run_calls == [], "pytest must not be invoked on empty scope"
        skips = [e["payload"] for e in env.events if e["type"] == "post_fix_pytest_skipped"]
        assert len(skips) == 1 and skips[0].get("reason") == "no_test_scope", env.events
        assert env.scope_events() == [] or env.scope_events()[0]["n_total"] == 0

    def test_ac4_missing_persisted_red_path_not_passed_to_pytest(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_present.py")
        # tests/test_gone.py is never created on disk
        env.persist_red(["tests/test_gone.py", "tests/test_present.py"])

        result = env.run(["pkg/module.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert not _has(args, "tests/test_gone.py"), env.run_calls
        assert _has(args, "tests/test_present.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_red"] == 1

    def test_ac4_converse_phantom_path_in_prev_data_list_passes_through(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_present.py")

        result = env.run(
            ["pkg/module.py"],
            red_test_paths=["tests/test_gone.py", "tests/test_present.py"],
        )

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_gone.py"), "phantom prev.data path must pass through: %r" % (env.run_calls,)
        assert _has(args, "tests/test_present.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_red"] == 2

    def test_ac5_prev_data_red_paths_win_over_persisted_and_manifest(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_from_prev.py")
        env.write("tests/test_from_file.py")
        env.write("tests/test_manifest_only.py")
        env.persist_red(["tests/test_from_file.py"])

        result = env.run(
            ["pkg/module.py", "tests/test_manifest_only.py"],
            red_test_paths=["tests/test_from_prev.py"],
        )

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_from_prev.py"), env.run_calls
        assert not _has(args, "tests/test_from_file.py"), env.run_calls
        assert not _has(args, "tests/test_manifest_only.py"), (
            "manifest tests must NOT be in scope when red_test_paths list is present: %r" % (env.run_calls,)
        )
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_red"] == 1
        assert scopes[0]["n_manifest"] == 0

    def test_ac6_union_dedupes_and_counts_match_argv(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_touched.py")      # manifest only
        env.write("tests/test_red_both.py")     # in RED and in manifest
        env.write("tests/test_red_only.py")     # RED only
        env.write("pkg/test_neighbor.py")       # sibling
        env.persist_red(["tests/test_red_both.py", "tests/test_red_only.py"])

        result = env.run(["pkg/module.py", "tests/test_touched.py", "tests/test_red_both.py"])

        assert result.status == "ok"
        args = env.test_args()
        for rel in ("tests/test_touched.py", "tests/test_red_both.py",
                    "tests/test_red_only.py", "pkg/test_neighbor.py"):
            assert _has(args, rel), "%s missing from argv: %r" % (rel, env.run_calls)
        assert len([a for a in args if _has([a], "tests/test_red_both.py")]) == 1, "dup must appear once"
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        s = scopes[0]
        assert s["n_red"] == 2
        assert s["n_manifest"] == 1, "n_manifest must exclude the RED duplicate: %r" % (s,)
        assert s["n_sibling"] == 1
        assert s["n_total"] == len(args) == 4

    def test_ac7_cap_never_drops_red_or_manifest(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_red_keep.py")
        env.write("tests/test_manifest_keep.py")
        env.persist_red(["tests/test_red_keep.py"])
        names = ["pkg/test_c%02d.py" % i for i in range(60)]
        for n in names:
            env.write(n)

        result = env.run(["pkg/module.py", "tests/test_manifest_keep.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_red_keep.py"), "RED path dropped by cap"
        assert _has(args, "tests/test_manifest_keep.py"), "manifest path dropped by cap"
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 50
        assert scopes[0]["n_total"] == len(args) == 52

    def test_ac8_whitespace_stripped_and_escaping_path_dropped(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_padded.py")
        # A real file outside the git cwd that the escaping path would resolve to.
        outside = tmp_path / "x"
        outside.mkdir()
        (outside / "test_a.py").write_text("# outside\n")
        env.persist_red(["  tests/test_padded.py  ", "../x/test_a.py"])

        result = env.run(["pkg/module.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_padded.py"), env.run_calls
        assert not any("test_a.py" in a for a in args), "escaping path must be dropped: %r" % (args,)
        assert not any(a != a.strip() for a in env.run_calls[0]), "argv entries must be stripped"
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_red"] == 1

    def test_ac9_git_ls_files_failure_degrades_to_other_legs(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("pkg/test_neighbor.py")
        env.write("tests/test_red_ok.py")
        env.persist_red(["tests/test_red_ok.py"])
        # Corrupt the index so a real `git ls-files` fails, whatever git seam is used.
        (env.repo / ".git" / "index").write_bytes(b"not a git index")

        result = env.run(["pkg/module.py"])

        assert result.status == "ok", "ls-files failure must degrade, not error: %r" % (result,)
        args = env.test_args()
        assert _has(args, "tests/test_red_ok.py"), env.run_calls
        assert not _has(args, "pkg/test_neighbor.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 0
        assert scopes[0]["n_red"] == 1

    # ---- AC10: edges -------------------------------------------------------

    def test_ac10_empty_red_list_means_no_manifest_no_persisted_but_siblings(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_manifest_t.py")
        env.write("tests/test_persisted.py")
        env.write("tests/test_module_extra.py")
        env.persist_red(["tests/test_persisted.py"])

        result = env.run(["pkg/module.py", "tests/test_manifest_t.py"], red_test_paths=[])

        assert result.status == "ok"
        args = env.test_args()
        assert not _has(args, "tests/test_manifest_t.py"), env.run_calls
        assert not _has(args, "tests/test_persisted.py"), env.run_calls
        assert _has(args, "tests/test_module_extra.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert (scopes[0]["n_red"], scopes[0]["n_manifest"], scopes[0]["n_sibling"], scopes[0]["n_total"]) == (0, 0, 1, 1)

    def test_ac10_root_level_source_has_no_same_dir_siblings(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("utilities.py")
        env.write("test_unrelated_root.py")        # same (root) directory: must not match
        env.write("tests/test_utilities_x.py")     # stem match: must match

        result = env.run(["utilities.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert not _has(args, "test_unrelated_root.py"), env.run_calls
        assert _has(args, "tests/test_utilities_x.py"), env.run_calls

    def test_ac10_init_and_short_stem_have_no_stem_match(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/__init__.py")
        env.write("pkg/mod.py")                    # stem "mod" < 4 chars
        env.write("tests/test_mod_x.py")
        env.write("tests/test___init__.py")
        env.write("tests/test_red_runs.py")        # keeps scope non-empty so pytest runs
        env.persist_red(["tests/test_red_runs.py"])

        result = env.run(["pkg/__init__.py", "pkg/mod.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert not _has(args, "tests/test_mod_x.py"), env.run_calls
        assert not _has(args, "tests/test___init__.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 0

    def test_ac10_non_py_and_test_files_in_manifest_add_no_siblings(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("docs/notes_guide.md")
        env.write("docs/test_docs_neighbor.py")    # same dir as a non-.py change
        env.write("tests/test_alpha_thing.py")     # manifest test file
        env.write("tests/test_other_thing.py")     # same dir as a manifest test file

        result = env.run(["docs/notes_guide.md", "tests/test_alpha_thing.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_alpha_thing.py"), env.run_calls
        assert not _has(args, "docs/test_docs_neighbor.py"), env.run_calls
        assert not _has(args, "tests/test_other_thing.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 0

    def test_ac10_tracked_but_deleted_sibling_is_dropped(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("pkg/test_alive.py")
        env.write("pkg/test_deleted.py")
        (env.repo / "pkg" / "test_deleted.py").unlink()  # still in the index

        result = env.run(["pkg/module.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "pkg/test_alive.py"), env.run_calls
        assert not _has(args, "pkg/test_deleted.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 1

    def test_ac10_path_spelling_dedupe(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/module.py")
        env.write("tests/test_a.py")
        env.persist_red(["./tests/test_a.py"])

        result = env.run(["pkg/module.py", "tests/test_a.py"])

        assert result.status == "ok"
        args = env.test_args()
        hits = [a for a in args if a.replace("\\", "/").endswith("tests/test_a.py")]
        assert len(hits) == 1, "spelling variants must dedupe to one entry: %r" % (env.run_calls,)
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert (scopes[0]["n_red"], scopes[0]["n_manifest"], scopes[0]["n_total"]) == (1, 0, 1)

    def test_ac10_events_doc_has_scope_row(self):
        events_md = Path(__file__).resolve().parents[2] / "docs" / "events.md"
        text = events_md.read_text(encoding="utf-8")
        assert _SCOPE_EVENT in text, "docs/events.md must document %s" % _SCOPE_EVENT
