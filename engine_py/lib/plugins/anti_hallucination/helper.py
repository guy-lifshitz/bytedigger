"""Anti-hallucination plugin — engine-side helpers.

Public API:
    verify_findings(ctx, prev: StepResult) -> StepResult
        Post-processor that parses ## Aggregated Findings in the review doc,
        verifies each evidence citation, demotes unverified findings to a
        ## Unverified Findings (Auto-Filtered) section, and rewrites the doc
        atomically.

    verify_validation_doc(ctx, prev: StepResult) -> StepResult
        Phase_5_validation post-processor. Parses Forward Map / Reverse Map /
        Spec Compliance / Quality Findings sections of build-opus-validation.md.
        For each evidence-quote line `> path:line: code`, calls check_citation.
        Demotes findings with invalid citations to a ## Unverified Citations
        section appended to the doc. Also checks that every path in
        red_test_paths (from prev.data) is mentioned in Reverse Map; any
        missing path appends a finding to ## Validation Completeness Issues and
        demotes verdict to FAIL. Returns counters {verify_verified,
        verify_unverified, verify_uncited, verify_completeness_issues}.

    get_prompt_fragment() -> str
        Returns the EVALUATOR (reviewer) anti-fabrication prompt fragment
        from prompt_fragment.md in this package directory.

    get_producer_prompt_fragment() -> str
        Returns the PRODUCER anti-fabrication prompt fragment from
        producer_prompt_fragment.md. Use in phases that produce docs/code
        (explore, architect, synthesize, RED/GREEN workers) — separate from
        evaluator rules because the failure modes differ.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from review_schema.canonical import SEVERITY_HDR_LINE_RE  # GH970: tolerant SEVERITY-header parse

# Regexes for citation parsing.
_SEVERITY_HDR_RE = SEVERITY_HDR_LINE_RE
_EVIDENCE_QUOTE_RE = re.compile(r"^>\s+(\S+):(\d+):\s+(.+)$")

_PLUGIN_DIR = Path(__file__).parent

VERDICT_PASS = "PASS"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_FAIL = "FAIL"


def get_prompt_fragment() -> str:
    """Read and return the EVALUATOR (reviewer) anti-fabrication prompt fragment."""
    return (_PLUGIN_DIR / "prompt_fragment.md").read_text(encoding="utf-8")


def get_producer_prompt_fragment() -> str:
    """Read and return the PRODUCER anti-fabrication prompt fragment."""
    return (_PLUGIN_DIR / "producer_prompt_fragment.md").read_text(encoding="utf-8")


_BEHAVIORAL_ASSERTION_RUBRIC = """\
## BEHAVIORAL ASSERTION RUBRIC
Tests must assert observable behavior, not source-file structure. A test
is BEHAVIORAL when it (a) invokes the production function/command and
(b) asserts on the resulting state: return value, written file, emitted
event, ref state, exit code, log line.

FORBIDDEN — pattern-presence-only tests as primary contracts.
  ANTI-EXAMPLE 1 (do NOT do this — shell shape):
      grep -q "success.*update" coordinator.sh && echo PASS
  This passes whenever the string appears anywhere in the source —
  including comments, dead code, or unrelated functions. It cannot
  distinguish working impl from dead code, which is exactly the W1
  batch-coordinator failure mode.

  ANTI-EXAMPLE 2 (also FORBIDDEN — Python shape):
      assert "git_cas_update" in open("coordinator.py").read()
  Same failure mode in pytest dressing — the string lives in source
  text, not in observable runtime behavior.

  ANTI-EXAMPLE 3 (FORBIDDEN — hardcoded verdict literal):
      test_result "name" "false"
      test_result "name" "true"
  Verdict is a hardcoded literal, not derived from production output.
  The test cannot transition PASS/FAIL based on what the code does —
  it ALWAYS reports the same verdict. Verdict MUST come from a
  variable set by production invocation: test_result "name" "$ok".

  ANTI-EXAMPLE 4 (FORBIDDEN — both branches identical literal):
      if [ "$result" = "ok" ]; then
        test_result "name" "false"
      else
        test_result "name" "false"
      fi
  Both branches of the if/else hardcode the same verdict — broken
  contract. The conditional evaluates production behavior but the
  verdict ignores it. Test is structurally incapable of producing
  PASS regardless of what production code does.

  ANTI-EXAMPLE 5 (FORBIDDEN — heredoc fixture grep tautology):
      cat > "$F" <<EOF
      payload=blocked
      EOF
      grep -q "blocked" "$F"
  The heredoc writes "blocked" to $F, then grep finds "blocked" in
  $F. Production code is never invoked. The grep is tautological —
  it succeeds because the heredoc placed the string there, not
  because any function under test produced it. Invoke production
  code, capture its output, grep on the OUTPUT — not the fixture.

  ANTI-EXAMPLE 6 (FORBIDDEN — spec AC cites unverified function name):
      ## Acceptance Criteria
      AC1: --resume <run-id> flag parses correctly.
        Validation: source SYSTEM/cli/build/batch-build.sh && parse_args --resume X
  Spec validation source-calls a function name (`parse_args`) that
  does not exist as a function — batch-build.sh's argument loop is a
  top-level `while/case` block at L1033-1127, not a named function.
  When RED writes tests against this AC, `parse_args: command not
  found`. Spec writers MUST grep-verify cited identifiers exist
  (`grep -nE "^\\s*<fn>\\s*\\(\\)" <file>` or `grep -nE "function
  <fn>" <file>`) before including them in Validation lines. If the
  identifier doesn't exist, replace with a behavior validation:
      Validation: ./batch-build --resume bogus-id 2>&1 | grep "no such run-id"
  Behavior validations invoke the production binary end-to-end, so
  they cannot fabricate non-existent internal symbols. Source: 2026-
  05-08 E4B6E9D7 attempt 2 phase_1 spec writer fabricated parse_args
  → cap-2 REVISE → terminal abort. spec_lint (D04A3BA8) catches this
  mechanically pre-Opus; this rubric is the cause-fix.

REQUIRED — function call + observable side-effect.
  POSITIVE EXAMPLE:
      out=$(coordinator.sh update "$worker_sha")
      [ "$out" = "OK" ] && [ "$(git rev-parse HEAD)" = "$worker_sha" ]
  The test invokes coordinator.sh and asserts on its return value AND
  ref state — both observable side-effects of running the function.

Structural greps are acceptable ONLY as supplementary smoke checks
alongside ≥1 behavioral assertion per contract.
"""


def get_behavioral_assertion_rubric() -> str:
    """Return the centralized BEHAVIORAL ASSERTION RUBRIC fragment.

    Injected into RED worker, Opus validator, phase_6 reviewer, and spec
    writer prompts. Single source of truth — all 4 surfaces call this
    accessor. Do NOT inline copies; DRY guard in test T08 enforces it.
    """
    return _BEHAVIORAL_ASSERTION_RUBRIC


_OUT_OF_ROLE_BLOCK = (
    "OUT OF ROLE: do NOT run `bun run-phase.ts`, engine_py/run.py, /build, /bugfix, "
    "git commit/push, or read orchestrator memory files. If blocked, STOP and report."
)


def get_out_of_role_block() -> str:
    """Return the OUT-OF-ROLE block fragment.

    Injected into every phase prompt builder. Prevents subagents from
    recursively invoking the pipeline or mutating git state. Single source
    of truth — all 12 prompt builders call this accessor (F9F7E4FD).
    """
    return _OUT_OF_ROLE_BLOCK


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_citation(path_str: str, line_num: int, quote_text: str, ctx) -> str | None:
    """Verify a single path:line evidence citation. Returns tag string on failure, None on pass."""
    # Tilde-prefixed citations (e.g. `~/.claude/SYSTEM/foo.ts`) come from
    # sub-reviewer agents that surface paths in their own home-relative form.
    # Without explicit expansion, `Path(cwd) / "~/.claude/..."` produces
    # `<cwd>/~/.claude/...` which never resolves — false UNVERIFIED tag.
    # See agreement 467E0B3B / forge-1777619936-4d345d8e (2026-05-01).
    if path_str.startswith("~"):
        file_path = Path(path_str).expanduser()
    elif path_str.startswith("/"):
        file_path = Path(path_str)
    else:
        cwd = getattr(ctx, "cwd", None) or os.getcwd()
        file_path = Path(cwd) / path_str

    try:
        file_content = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "[verify: suspect-file-not-found]"
    except (PermissionError, UnicodeDecodeError):
        return "[verify: suspect-file-not-found]"
    except OSError:
        return "[verify: suspect-file-not-found]"

    file_lines = file_content.splitlines()
    if line_num <= 0 or line_num > len(file_lines):
        return "[verify: suspect-file-not-found]"

    actual_line = file_lines[line_num - 1]

    # 1F39FB1A (design item 6a): strip outer backtick pair from quote_text BEFORE
    # normalized-substring match. Sub-reviewer prompts that wrap quote content in
    # markdown inline-code (`...`) would otherwise produce false UNVERIFIED tags on
    # findings whose on-disk content has no wrapping backticks. Internal backticks
    # preserved (only outer pair stripped). The lstrip() step handles the
    # leading-whitespace+outer-backtick variant from Incident 2 that escapes a naive
    # startswith("`") test (forge-1778762066-9e350777).
    _qt = quote_text.lstrip()
    if len(_qt) >= 2 and _qt.startswith("`") and _qt.endswith("`"):
        quote_text = _qt[1:-1]

    if _normalize_ws(quote_text) not in _normalize_ws(actual_line):
        return "[verify: suspect-no-match]"

    return None


def verify_findings(ctx, prev) -> object:
    """Pure-Python post-processor. No LLM call.

    Parses ## Aggregated Findings from review_doc_path. For each finding:
    - No evidence-quote line → keep in place, tag [UNCITED].
    - Evidence-quote present → check path exists, line in bounds, quote matches.
      Pass → verify_verified++. Fail → demote to ## Unverified Findings, verify_unverified++.
    Rewrites review_doc_path atomically when any finding is demoted or tagged [UNCITED].
    Recomputes verdict when at least one finding is demoted.
    """
    import sys as _sys
    _engine_root = str(Path(__file__).parent.parent.parent.parent)
    if _engine_root not in _sys.path:
        _sys.path.insert(0, _engine_root)
    from contracts import StepResult  # type: ignore[import]

    if not hasattr(prev, "data") or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="verify_findings",
            error="prev step did not produce data",
            error_code="E_MISSING_PREV_DATA",
        )

    forwarded = dict(prev.data)
    counters = {
        "verify_verified": 0,
        "verify_unverified": 0,
        "verify_uncited": 0,
        "verify_unparseable": 0,
    }

    review_doc_path_str = prev.data.get("review_doc_path")
    if not review_doc_path_str:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="verify_findings",
            error="prev step did not provide review_doc_path",
            error_code="E_MISSING_REVIEW_DOC_PATH",
        )
    review_doc_path = Path(review_doc_path_str)
    try:
        doc_text = review_doc_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_findings",
            error=str(exc),
            error_code="E_VERIFY_READ_FAILED",
        )
    except (PermissionError, UnicodeDecodeError, OSError) as exc:
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_findings",
            error=str(exc),
            error_code="E_VERIFY_READ_FAILED",
        )
    lines = doc_text.splitlines(keepends=True)

    # Locate ## Aggregated Findings section boundaries.
    agg_start: int | None = None
    agg_end: int = len(lines)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n\r")
        if stripped == "## Aggregated Findings":
            agg_start = i
        elif agg_start is not None and stripped.startswith("## ") and i != agg_start:
            agg_end = i
            break

    if agg_start is None:
        result_data = {**forwarded, **counters, "verify_unparseable": 1}
        return StepResult(status="ok", data=result_data, duration_ms=0, step_name="verify_findings")

    section_lines = lines[agg_start + 1 : agg_end]

    # Separate pre-finding content (blank lines between section header and first finding).
    first_finding_offset = len(section_lines)
    for j, sl in enumerate(section_lines):
        if _SEVERITY_HDR_RE.match(sl.rstrip("\n\r")):
            first_finding_offset = j
            break

    pre_finding_lines = section_lines[:first_finding_offset]

    # Parse individual findings delimited by ### SEVERITY: headers.
    findings: list[list[str]] = []
    current: list[str] = []
    for sl in section_lines[first_finding_offset:]:
        if _SEVERITY_HDR_RE.match(sl.rstrip("\n\r")) and current:
            findings.append(current)
            current = []
        current.append(sl)
    if current:
        findings.append(current)

    if not findings:
        # Section exists but has no canonical findings — valid for PASS reviews.
        return StepResult(
            status="ok", data={**forwarded, **counters},
            duration_ms=0, step_name="verify_findings"
        )

    agg_new_lines: list[str] = []
    unverified_lines: list[str] = []

    for finding in findings:
        # Find the first evidence-quote line within this finding.
        first_quote_idx: int | None = None
        quote_path: str = ""
        quote_line_num: int = 0
        quote_text: str = ""
        for j, fl in enumerate(finding):
            m = _EVIDENCE_QUOTE_RE.match(fl.rstrip("\n\r"))
            if m:
                first_quote_idx = j
                quote_path = m.group(1)
                quote_line_num = int(m.group(2))
                quote_text = m.group(3)
                break

        if first_quote_idx is None:
            counters["verify_uncited"] += 1
            modified = list(finding)
            modified[0] = modified[0].rstrip("\n\r") + " [UNCITED]\n"
            agg_new_lines.extend(modified)
            continue

        tag = check_citation(quote_path, quote_line_num, quote_text, ctx)
        if tag is None:
            counters["verify_verified"] += 1
            agg_new_lines.extend(finding)
        else:
            counters["verify_unverified"] += 1
            modified = list(finding)
            modified[0] = modified[0].rstrip("\n\r") + f" {tag}\n"
            unverified_lines.extend(modified)

    needs_rewrite = counters["verify_unverified"] > 0 or counters["verify_uncited"] > 0
    if not needs_rewrite:
        return StepResult(
            status="ok", data={**forwarded, **counters}, duration_ms=0, step_name="verify_findings"
        )

    # Rebuild document with updated sections.
    new_doc_lines: list[str] = list(lines[: agg_start + 1])
    new_doc_lines.extend(pre_finding_lines)
    new_doc_lines.extend(agg_new_lines)
    if unverified_lines:
        new_doc_lines.append("\n")
        new_doc_lines.append("## Unverified Findings (Auto-Filtered)\n")
        new_doc_lines.extend(unverified_lines)
    new_doc_lines.extend(lines[agg_end:])
    new_doc = "".join(new_doc_lines)

    # Atomic write via temp-file + os.replace.
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=review_doc_path.parent, delete=False, suffix=".tmp"
        ) as tf:
            tmp_name = tf.name
            tf.write(new_doc)
        os.replace(tmp_name, str(review_doc_path))
        tmp_name = None
    except OSError as exc:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_findings",
            error=str(exc),
            error_code="E_VERIFY_WRITE_FAILED",
        )

    # Recompute verdict when findings were demoted; uncited-only changes do not affect verdict.
    if counters["verify_unverified"] > 0:
        surviving: set[str] = set()
        for fl in agg_new_lines:
            m = _SEVERITY_HDR_RE.match(fl.rstrip("\n\r"))
            if m:
                surviving.add(m.group(1))
        if not surviving:
            forwarded["verdict"] = VERDICT_PASS
        elif "CRITICAL" in surviving or "HIGH" in surviving:
            forwarded["verdict"] = VERDICT_FAIL
        else:
            forwarded["verdict"] = VERDICT_PARTIAL

    return StepResult(
        status="ok", data={**forwarded, **counters}, duration_ms=0, step_name="verify_findings"
    )


# Regex to detect evidence-quote lines in phase_5_validation doc sections.
# Same format as phase_6 but used in a broader set of sections.
_VALIDATION_EVIDENCE_QUOTE_RE = re.compile(r"^>\s+(\S+):(\d+):\s+(.+)$")

# Regex to detect a Reverse Map bullet mentioning a test path.
# A bullet starts with optional whitespace + "- " or "* " or a numbered list.
_REVERSE_MAP_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$")


def verify_validation_doc(ctx, prev) -> object:
    """Phase_5_validation post-processor. No LLM call.

    Reads validation_doc_path from prev.data. For each evidence-quote line
    `> path:line: code` found in any section of the doc, calls check_citation.
    Invalid citations are collected and appended as a ## Unverified Citations
    section; valid ones increment verify_verified.

    Also performs a completeness check: reads red_test_paths from prev.data
    and confirms every path is mentioned (by substring) somewhere in the
    ## Reverse Map section. Missing paths are appended as ## Validation
    Completeness Issues and force-demote the verdict to FAIL.

    Rewrites the doc atomically when any citation is invalid or any completeness
    issue is found.

    Returns counters: verify_verified, verify_unverified, verify_uncited,
    verify_completeness_issues.
    """
    import sys as _sys
    _engine_root = str(Path(__file__).parent.parent.parent.parent)
    if _engine_root not in _sys.path:
        _sys.path.insert(0, _engine_root)
    from contracts import StepResult  # type: ignore[import]

    if not hasattr(prev, "data") or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="verify_validation_citations",
            error="prev step did not produce data",
            error_code="E_MISSING_PREV_DATA",
        )

    forwarded = dict(prev.data)
    counters: dict[str, int] = {
        "verify_verified": 0,
        "verify_unverified": 0,
        "verify_uncited": 0,
        "verify_completeness_issues": 0,
    }

    doc_path = Path(prev.data.get("validation_doc_path", ""))
    if not doc_path.name:
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_validation_citations",
            error="validation_doc_path missing from prev.data",
            error_code="E_MISSING_PREV_DATA",
        )

    try:
        doc_text = doc_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_validation_citations",
            error=str(exc),
            error_code="E_VERIFY_READ_FAILED",
        )
    except (PermissionError, UnicodeDecodeError, OSError) as exc:
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_validation_citations",
            error=str(exc),
            error_code="E_VERIFY_READ_FAILED",
        )

    lines = doc_text.splitlines(keepends=True)
    red_test_paths: list[str] = prev.data.get("red_test_paths", [])

    # ── Pass 1: citation verification ─────────────────────────────────────────
    unverified_citation_lines: list[str] = []
    new_lines: list[str] = list(lines)

    for i, line in enumerate(lines):
        m = _VALIDATION_EVIDENCE_QUOTE_RE.match(line.rstrip("\n\r"))
        if not m:
            continue
        path_str = m.group(1)
        line_num = int(m.group(2))
        quote_text = m.group(3)
        tag = check_citation(path_str, line_num, quote_text, ctx)
        if tag is None:
            counters["verify_verified"] += 1
        else:
            counters["verify_unverified"] += 1
            tagged = line.rstrip("\n\r") + f"  {tag}\n"
            new_lines[i] = ""  # ← REMOVED from body; will appear only under section
            unverified_citation_lines.append(tagged)

    # ── Pass 2: Reverse Map completeness ─────────────────────────────────────
    reverse_map_text: str | None = None  # None means section absent
    in_reverse_map = False
    for line in lines:
        stripped = line.rstrip("\n\r")
        if stripped == "## Reverse Map":
            in_reverse_map = True
            reverse_map_text = ""
            continue
        if in_reverse_map:
            if stripped.startswith("## ") and stripped != "## Reverse Map":
                break
            reverse_map_text = (reverse_map_text or "") + line

    missing_paths: list[str] = []
    if reverse_map_text is not None and red_test_paths:
        for rtp in red_test_paths:
            if rtp and rtp not in reverse_map_text:
                missing_paths.append(rtp)
                counters["verify_completeness_issues"] += 1

    needs_rewrite = counters["verify_unverified"] > 0 or counters["verify_completeness_issues"] > 0
    if not needs_rewrite:
        return StepResult(
            status="ok",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_validation_citations",
        )

    # ── Rebuild doc ───────────────────────────────────────────────────────────
    new_doc_lines = list(new_lines)

    if unverified_citation_lines:
        new_doc_lines.append("\n")
        new_doc_lines.append("## Unverified Citations (Auto-Filtered)\n")
        new_doc_lines.extend(unverified_citation_lines)

    if missing_paths:
        new_doc_lines.append("\n")
        new_doc_lines.append("## Validation Completeness Issues\n")
        for mp in missing_paths:
            new_doc_lines.append(f"- Reverse Map missing entry for: {mp}\n")

    # Demote verdict when completeness issues found; citations alone → PARTIAL.
    if counters["verify_completeness_issues"] > 0:
        forwarded["verdict"] = VERDICT_FAIL
    elif counters["verify_unverified"] > 0:
        forwarded["verdict"] = VERDICT_PARTIAL

    new_doc = "".join(new_doc_lines)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=doc_path.parent, delete=False, suffix=".tmp"
        ) as tf:
            tmp_name = tf.name
            tf.write(new_doc)
        os.replace(tmp_name, str(doc_path))
        tmp_name = None
    except OSError as exc:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_validation_citations",
            error=str(exc),
            error_code="E_VERIFY_WRITE_FAILED",
        )

    return StepResult(
        status="ok",
        data={**forwarded, **counters},
        duration_ms=0,
        step_name="verify_validation_citations",
    )
