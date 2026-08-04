"""schema.py — strict dataclass-style validators for engine verdicts.

Public API:
    SpecVerdict         — dataclass with ship: bool, findings: list[dict]
    ValidationVerdict   — dataclass with approve: bool, reject_reason: str | None
    SatisfactionVerdict — dataclass with satisfied: bool, fixes_required: list[dict]
    FixVerdict          — dataclass with fix_complete: bool, remaining: list[dict]
    SynthesizerVerdict  — dataclass with synthesized: bool, needs_context: bool, concerns: list[dict]
    SchemaViolation     — Exception raised on validation failure
    enforce(payload, schema_class) -> instance
"""
from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type


class SchemaViolation(Exception):
    """Raised when a payload fails schema validation."""
    pass


@dataclass
class SpecVerdict:
    ship: bool
    findings: List[dict]


# GH767 §2.1: safe back-compat default (not Optional[None]) — an older/
# sloppy validator payload omitting verdict_category must degrade to today's
# behaviour (TEST_GAP), never to a masked error. Not a #221 fail-open: absence
# maps to the pre-existing legacy path, not to a silently-approved state.
VERDICT_CATEGORY_NONE = "NONE"


@dataclass
class ValidationVerdict:
    approve: bool
    reject_reason: Optional[str]
    verdict_category: str = VERDICT_CATEGORY_NONE


@dataclass
class SatisfactionVerdict:
    satisfied: bool
    fixes_required: List[dict]


@dataclass
class FixVerdict:
    fix_complete: bool
    remaining: List[dict]


@dataclass
class SynthesizerVerdict:
    synthesized: bool
    needs_context: bool
    concerns: List[dict]


def _is_optional(annotation: Any) -> tuple[bool, Any]:
    """Return (is_optional, inner_type). Works for Optional[X] = Union[X, None]."""
    origin = getattr(annotation, "__origin__", None)
    if origin is typing.Union:
        args = annotation.__args__
        if type(None) in args:
            inner_args = [a for a in args if a is not type(None)]
            inner = inner_args[0] if len(inner_args) == 1 else typing.Union[tuple(inner_args)]
            return True, inner
    return False, annotation


def _check_type(value: Any, annotation: Any, field: str) -> None:
    """Raise SchemaViolation if value does not match annotation."""
    is_opt, inner = _is_optional(annotation)

    if is_opt and value is None:
        return  # None is valid for Optional fields

    # Resolve the effective type to check against
    check_ann = inner if is_opt else annotation
    origin = getattr(check_ann, "__origin__", None)

    if check_ann is bool or check_ann == bool:
        # Strict bool check — do NOT accept int 0/1
        if not isinstance(value, bool):
            raise SchemaViolation(
                f"Field '{field}': expected bool, got {type(value).__name__}"
            )
    elif check_ann is str or check_ann == str:
        if not isinstance(value, str):
            raise SchemaViolation(
                f"Field '{field}': expected str, got {type(value).__name__}"
            )
    elif origin is list or check_ann is list:
        if not isinstance(value, list):
            raise SchemaViolation(
                f"Field '{field}': expected list, got {type(value).__name__}"
            )
    # Other types: skip deep checking for simplicity


def enforce(payload: Dict[str, Any], schema_class: Type) -> Any:
    """Validate payload against schema_class and return an instance.

    Rules:
    - Every required annotated field must be present (Optional fields default to None if absent).
    - Unknown/extra keys raise SchemaViolation mentioning the unknown field name.
    - Type mismatches raise SchemaViolation mentioning the field name.
    - bool fields accept only True/False, NOT 0/1.
    """
    hints = typing.get_type_hints(schema_class)

    # Check for unknown keys
    for key in payload:
        if key not in hints:
            raise SchemaViolation(
                f"Unknown field '{key}' not in schema {schema_class.__name__}"
            )

    # GH767 §2.1: a dataclass field carrying an explicit default (e.g.
    # ValidationVerdict.verdict_category) must fall back to THAT default when
    # the payload omits the key entirely — a back-compat safe default, not
    # the Optional-field None behavior below.
    field_defaults: Dict[str, Any] = {
        f.name: f.default
        for f in dataclasses.fields(schema_class)
        if f.default is not dataclasses.MISSING
    }

    # Build validated kwargs
    kwargs: Dict[str, Any] = {}
    for field, annotation in hints.items():
        is_opt, _ = _is_optional(annotation)
        if field not in payload:
            if field in field_defaults:
                kwargs[field] = field_defaults[field]
            elif is_opt:
                kwargs[field] = None
            else:
                raise SchemaViolation(
                    f"Missing required field '{field}' in {schema_class.__name__}"
                )
        else:
            value = payload[field]
            _check_type(value, annotation, field)
            kwargs[field] = value

    return schema_class(**kwargs)
