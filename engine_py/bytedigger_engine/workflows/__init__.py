"""HAL workflow registry — register all known workflows with the engine."""
from .echo import echo_workflow
from .phase_0_research import phase_0_research_workflow
from .phase_05_inject import phase_05_inject_workflow
from .phase_0_6_artifact_detect import phase_0_6_artifact_detect_workflow
from .phase_1_discovery import phase_1_discovery_workflow
from .phase_2_explore import phase_2_explore_workflow
from .phase_3_clarify import phase_3_clarify_workflow
from .phase_4_architect import phase_4_architect_workflow
from .phase_45_spec import phase_45_spec_workflow
from .phase_45_spec_lite import phase_45_spec_lite_workflow
from .phase_5_implement import phase_5_implement_workflow
from .phase_5_devops_scan import phase_5_devops_scan_workflow
from .phase_devops_pipeline import phase_devops_pipeline_workflow
from .phase_5_integration_canary import phase_5_integration_canary_workflow
from .phase_5_integrity import phase_5_integrity_workflow
from .phase_6_fix_integrity import phase_6_fix_integrity_workflow
from .phase_6_review import phase_6_review_workflow
from .phase_6_review_simple_fastpath import phase_6_review_simple_fastpath_workflow
from .phase_6_smoke import phase_6_smoke_workflow
from .phase_7_synthesize import phase_7_synthesize_workflow
from .phase_8_post_deploy import phase_8_post_deploy_workflow


def register_all(engine) -> None:
    engine.register("echo", echo_workflow())
    engine.register("phase_0_research", phase_0_research_workflow())
    engine.register("phase_05_inject", phase_05_inject_workflow())
    engine.register("phase_0_6_artifact_detect", phase_0_6_artifact_detect_workflow())
    engine.register("phase_1_discovery", phase_1_discovery_workflow())
    engine.register("phase_2_explore", phase_2_explore_workflow())
    engine.register("phase_3_clarify", phase_3_clarify_workflow())
    engine.register("phase_4_architect", phase_4_architect_workflow())
    engine.register("phase_45_spec", phase_45_spec_workflow())
    engine.register("phase_45_spec_lite", phase_45_spec_lite_workflow())
    engine.register("phase_5_implement", phase_5_implement_workflow())
    engine.register("phase_5_devops_scan", phase_5_devops_scan_workflow())
    engine.register("phase_devops_pipeline", phase_devops_pipeline_workflow())
    engine.register("phase_5_integration_canary", phase_5_integration_canary_workflow())
    engine.register("phase_5_integrity", phase_5_integrity_workflow())
    engine.register("phase_6_fix_integrity", phase_6_fix_integrity_workflow())
    engine.register("phase_6_review", phase_6_review_workflow())
    engine.register("phase_6_review_simple_fastpath", phase_6_review_simple_fastpath_workflow())
    engine.register("phase_6_smoke", phase_6_smoke_workflow())
    engine.register("phase_7_synthesize", phase_7_synthesize_workflow())
    engine.register("phase_8_post_deploy", phase_8_post_deploy_workflow())
