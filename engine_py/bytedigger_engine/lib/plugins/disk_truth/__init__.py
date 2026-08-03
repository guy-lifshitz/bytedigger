"""disk_truth — engine-state from git/disk/exit-code, not LLM markdown.

Re-exports all public symbols for convenient import:
    from plugins.disk_truth import (
        GitDiffPort, get_git_diff, set_default_git_diff_factory,
        reset_default_git_diff_factory, default_git_diff,
        git_diff_files, git_status_porcelain, resolve_pre_phase_sha,
        run_test_command, TestRunResult, test_subprocess_env,
        TestRunner, get_test_runner, set_default_test_runner_factory,
        reset_default_test_runner_factory, default_test_runner,
        parse_single_token_verdict, parse_structured_block,
        SpecVerdict, ValidationVerdict, SatisfactionVerdict, FixVerdict, SynthesizerVerdict, SchemaViolation, enforce,
    )
"""
from .git_diff import GitDiffPort, get_git_diff, set_default_git_diff_factory, reset_default_git_diff_factory, default_git_diff  # noqa: F401
from .git_diff import git_diff_files, git_status_porcelain, resolve_pre_phase_sha  # noqa: F401
from .test_runner import run_test_command, TestRunResult, test_subprocess_env  # noqa: F401
from .test_runner import TestRunner, get_test_runner, set_default_test_runner_factory, reset_default_test_runner_factory, default_test_runner  # noqa: F401
from .verdict_parser import parse_single_token_verdict, parse_structured_block  # noqa: F401
from .schema import SpecVerdict, ValidationVerdict, SatisfactionVerdict, FixVerdict, SynthesizerVerdict, SchemaViolation, enforce  # noqa: F401
