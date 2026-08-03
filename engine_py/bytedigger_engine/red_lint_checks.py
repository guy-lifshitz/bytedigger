"""red_lint_checks — deterministic RED-side lints for bash-emit and field-coverage (§1o).

AC1-5: scan_bash_emit() and scan_field_coverage() detect common emit/assertion gaps.
AC12: §1f — no top-level side effects; clean import.
"""
import re


def scan_bash_emit(text: str) -> list[dict]:
    """Detect inline dict in $(python[3] -c ...) or `python[3] -c ...` — brace-expansion trap.

    Returns list of findings: [{"line": int, "reason": str}, ...]
    Each line in text is checked (1-based).
    Detects both command-substitution $(...) and backtick-substitution `...` forms.
    """
    findings = []
    pattern = r'(?:\$\(|`).*python3?\s+-c.*\{.*\}.*(?:\)|`)'

    for line_num, line in enumerate(text.split('\n'), start=1):
        if re.search(pattern, line):
            findings.append({
                "line": line_num,
                "reason": "inline dict in command-substitution — brace-expansion trap; use printf"
            })

    return findings


def scan_field_coverage(text: str, required_fields: list[str]) -> list[dict]:
    """Check that assert lines reference all required fields (§1o).

    Returns list of findings: [{"line": int, "reason": str}, ...]
    - If required_fields is empty → return []
    - Find all lines with 'assert' and collect substrings matching required_fields
    - If found_set is empty (no required field on any assert) → return [] (not an emit-assertion)
    - If found_set is non-empty and strict subset of required_fields → emit ONE finding
    """
    if not required_fields:
        return []

    # Collect all lines with 'assert' and track which required fields appear
    assert_lines = []
    found_fields = set()

    for line_num, line in enumerate(text.split('\n'), start=1):
        if 'assert' in line:
            assert_lines.append(line_num)
            for field in required_fields:
                if field in line:
                    found_fields.add(field)

    # Empty set → not an emit-assertion → no finding
    if not found_fields:
        return []

    # Strict subset (some required fields missing) → emit ONE finding
    required_set = set(required_fields)
    if found_fields < required_set:
        missing = required_set - found_fields
        # Anchor on the first assert line
        first_assert_line = assert_lines[0] if assert_lines else 1
        missing_str = ", ".join(sorted(missing))
        return [{
            "line": first_assert_line,
            "reason": f"emit-assertion references only a subset of required fields (§1o); assert all: {missing_str}"
        }]

    # Full coverage → no finding
    return []
