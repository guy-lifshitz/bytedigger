"""review_schema — canonical schema constants for phase_6 review.

Re-exports all public symbols for convenient import:
    from plugins.review_schema import (
        PER_ROLE_SCHEMA_TEMPLATE,
        STRUCTURED_FINDINGS_JSON_TEMPLATE,
        STRUCTURED_FINDINGS_DIRECTIVE_SHORT,
        ROLE_FINDINGS_COUNT_MARKER_RE,
        PARALLEL_DISPATCH_FRAMING_TEMPLATE,
    )
"""
from .canonical import PER_ROLE_SCHEMA_TEMPLATE  # noqa: F401
from .canonical import STRUCTURED_FINDINGS_JSON_TEMPLATE  # noqa: F401
from .canonical import STRUCTURED_FINDINGS_DIRECTIVE_SHORT  # noqa: F401
from .canonical import ROLE_FINDINGS_COUNT_MARKER_RE  # noqa: F401
from .canonical import PARALLEL_DISPATCH_FRAMING_TEMPLATE  # noqa: F401
