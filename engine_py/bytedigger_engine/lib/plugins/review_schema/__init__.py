"""review_schema — canonical schema constants for phase_6 review.

Re-exports all public symbols for convenient import:
    from plugins.review_schema import (
        PER_ROLE_SCHEMA_TEMPLATE,
        STRUCTURED_FINDINGS_JSON_TEMPLATE,
        STRUCTURED_FINDINGS_DIRECTIVE_SHORT,
        ROLE_FINDINGS_COUNT_MARKER_RE,
        PARALLEL_DISPATCH_FRAMING_TEMPLATE,
        SEVERITY_HDR_CORE,
        SEVERITY_HDR_LINE_RE,
        SEVERITY_HDR_MULTILINE_RE,
        SEVERITY_MALFORMED_LINE_RE,
        lint_role_report,
    )
"""
from .canonical import PER_ROLE_SCHEMA_TEMPLATE  # noqa: F401
from .canonical import STRUCTURED_FINDINGS_JSON_TEMPLATE  # noqa: F401
from .canonical import STRUCTURED_FINDINGS_DIRECTIVE_SHORT  # noqa: F401
from .canonical import ROLE_FINDINGS_COUNT_MARKER_RE  # noqa: F401
from .canonical import PARALLEL_DISPATCH_FRAMING_TEMPLATE  # noqa: F401
from .canonical import SEVERITY_HDR_CORE  # noqa: F401
from .canonical import SEVERITY_HDR_LINE_RE  # noqa: F401
from .canonical import SEVERITY_HDR_MULTILINE_RE  # noqa: F401
from .canonical import SEVERITY_MALFORMED_LINE_RE  # noqa: F401
from .canonical import lint_role_report  # noqa: F401
