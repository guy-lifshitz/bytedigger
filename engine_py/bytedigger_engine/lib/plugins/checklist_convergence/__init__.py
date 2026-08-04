"""checklist_convergence — restricted cycle-2 convergence plugin.

Re-exports all public symbols for convenient import:
    from plugins.checklist_convergence import (
        Finding, ReviewerVerdict,
        extract_findings_section, extract_structured_findings, extract_structured_findings_raw,
        extract_findings_for_writer,
        build_writer_prompt,
        build_reviewer_prompt,
        parse_per_finding_verdicts,
    )
"""
from .findings_extractor import (  # noqa: F401
    Finding,
    extract_findings_for_writer,
    extract_findings_section,
    extract_structured_findings,
    extract_structured_findings_raw,
)
from .restricted_reviewer_prompt import build_reviewer_prompt  # noqa: F401
from .restricted_writer_prompt import build_writer_prompt  # noqa: F401
from .surgical_revise import (  # noqa: F401  GH592
    apply_surgical_patches,
    build_surgical_revise_prompt,
    extract_surgical_patches,
)
from .verdict_parser import ReviewerVerdict, parse_per_finding_verdicts  # noqa: F401
