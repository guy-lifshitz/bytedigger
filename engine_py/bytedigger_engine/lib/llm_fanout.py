"""bd#318: the one sanctioned way to fan a model call out over N concurrent jobs.

A fan-out of "independent reviewers" whose jobs carry one prompt and one input
is N copies of the same call. ``run_distinct_llm_jobs`` refuses to build it:
``assert_distinct`` raises ``IdenticalFanoutError`` BEFORE any model call when
two jobs compose to the same normalized invoked prompt. The role label never
distinguishes jobs. ``class_i_lint.fanout_findings`` flags a model call handed
to a pool outside this module.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess


@dataclass(frozen=True)
class FanoutJob:
    role: str
    prompt: str
    input_text: str = ""


class IdenticalFanoutError(ValueError):
    """Two or more jobs compose to the same normalized prompt."""


def composed(job: FanoutJob) -> str:
    """The exact prompt that is invoked for *job*."""
    if not job.input_text:
        return job.prompt
    return job.prompt + "\n\n" + job.input_text


def _identity_key(job: FanoutJob) -> str:
    return " ".join(composed(job).split()).casefold()


def assert_distinct(jobs: list[FanoutJob]) -> None:
    """Raise ValueError for an empty list / blank role / blank composed prompt,
    IdenticalFanoutError when two jobs share an identity key."""
    if not jobs:
        raise ValueError("fan-out needs at least one job")
    for i, job in enumerate(jobs):
        if not job.role.strip():
            raise ValueError(f"job {i} has a blank role")
        if not composed(job).strip():
            raise ValueError(f"job {i} (role {job.role!r}) has a blank prompt")
    seen: dict[str, int] = {}
    for i, job in enumerate(jobs):
        key = _identity_key(job)
        if key in seen:
            first = seen[key]
            raise IdenticalFanoutError(
                f"jobs {first} (role {jobs[first].role!r}) and {i} (role {job.role!r}) "
                "compose to the same prompt; a fan-out of identical model calls is refused"
            )
        seen[key] = i


def run_distinct_llm_jobs(
    jobs: list[FanoutJob],
    *,
    model: str,
    timeout_sec: int,
    extra_data: dict | None = None,
    **invoke_kwargs: Any,
) -> list[StepResult]:
    """Run the jobs concurrently, one model call each; results in job order."""
    job_list = list(jobs)
    assert_distinct(job_list)
    parent_ctx = telemetry_ctx.get_current_run()
    with ThreadPoolExecutor(max_workers=len(job_list)) as executor:
        futures = [
            executor.submit(
                telemetry_ctx.run_with_current_run,
                parent_ctx,
                invoke_llm_subprocess,
                prompt=composed(job),
                model=model,
                timeout_sec=timeout_sec,
                extra_data=extra_data,
                **invoke_kwargs,
            )
            for job in job_list
        ]
    return [f.result() for f in futures]
