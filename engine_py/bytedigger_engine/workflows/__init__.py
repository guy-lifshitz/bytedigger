"""HAL workflow registry — register all known workflows with the engine."""
from .echo import echo_workflow
from .phase_0_research import phase_0_research_workflow
from .phase_05_inject import phase_05_inject_workflow
from .phase_45_spec import phase_45_spec_workflow
from .phase_5_implement import phase_5_implement_workflow
from .phase_5_integrity import phase_5_integrity_workflow
from .phase_6_fix_integrity import phase_6_fix_integrity_workflow
from .phase_6_review import phase_6_review_workflow
from .phase_7_synthesize import phase_7_synthesize_workflow
from .phase_8_post_deploy import phase_8_post_deploy_workflow


def register_all(engine) -> None:
    engine.register("echo", echo_workflow())
    engine.register("phase_0_research", phase_0_research_workflow())
    engine.register("phase_05_inject", phase_05_inject_workflow())
    engine.register("phase_45_spec", phase_45_spec_workflow())
    engine.register("phase_5_implement", phase_5_implement_workflow())
    engine.register("phase_5_integrity", phase_5_integrity_workflow())
    engine.register("phase_6_fix_integrity", phase_6_fix_integrity_workflow())
    engine.register("phase_6_review", phase_6_review_workflow())
    engine.register("phase_7_synthesize", phase_7_synthesize_workflow())
    engine.register("phase_8_post_deploy", phase_8_post_deploy_workflow())
