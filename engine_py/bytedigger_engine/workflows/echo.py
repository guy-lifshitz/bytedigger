"""Echo workflow — returns ctx.question. Plumbing-test for Bun↔Python bridge."""
from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition


def _echo(ctx, _prev):
    return StepResult(
        status="ok",
        data={"echoed": ctx.question},
        duration_ms=0,
        step_name="echo",
    )


def echo_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="echo",
        steps=[StepContract(name="echo", execute=_echo)],
    )
