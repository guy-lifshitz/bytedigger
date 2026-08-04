"""Semantic verifier for phase_6_review findings.

W15 implementation of W14 spec. See:
  2026-04-30_W14_semantic_verifier_spec.md
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from bytedigger_engine.lib.plugins.review_schema.canonical import SEVERITY_HDR_LINE_RE  # GH970: tolerant SEVERITY-header parse

MAX_SEMANTIC_VERIFY_FINDINGS = 20
SEMANTIC_VERIFIER_TIMEOUT_SEC = 60
SEMANTIC_VERIFIER_OPUS_TIMEOUT_SEC = 120

# `--model` tier aliases the claude CLI resolves to the *current* per-tier
# default. Pinning a versioned id (e.g. "claude-opus-4-7") is fragile: once the
# CLI's default advances, an unrecognised id is SILENTLY downgraded to the
# session default instead of erroring — which sent the whole haiku-tier verifier
# fleet to Opus 4.8 (2026-06-20 runaway, ~$270). Aliases never go stale; anything
# outside this set is rejected fail-closed before a subprocess is spawned.
_ACCEPTED_MODEL_ALIASES = frozenset({"opus", "sonnet", "haiku"})

VERDICT_REPRODUCED = "REPRODUCED"
VERDICT_REFUTED = "REFUTED"
VERDICT_UNVERIFIED = "UNVERIFIED"

_SEVERITY_HDR_RE = SEVERITY_HDR_LINE_RE

_LOW_TRUST_TAGS = ("[UNCITED]", "[UNVERIFIED CITATION]")

_REQUIRED_KEYS = {
    VERDICT_REPRODUCED: ("file", "evidence"),
    VERDICT_REFUTED: ("reason", "rationale"),
    VERDICT_UNVERIFIED: ("reason",),
}

logger = logging.getLogger(__name__)

# Lazy import shim — engine_py modules live one directory above this plugin
# subtree (lib/plugins/anti_hallucination/ → engine_py/). Insert on import.

try:
    from bytedigger_engine.event_log import EventLog  # type: ignore[import]
except ImportError:  # pragma: no cover — engine_py layout always provides it
    EventLog = None  # type: ignore[assignment,misc]

from bytedigger_engine.lib.verdict_parse import find_last_standalone_marker  # type: ignore[import]  # noqa: E402
from bytedigger_engine.lib.bounded_spawn import bounded_run  # noqa: E402
from bytedigger_engine.lib.model_config import get_claude_critical, get_claude_fallback  # type: ignore[import]  # noqa: E402


def parse_verdict(raw: str) -> dict[str, Any]:
    """Parse a verifier-agent response per W14 spec §3.

    Last-marker-wins (W11 line-anchor pattern). Routes through lib/verdict_parse.py
    P4 (EEFD480F) — single-source of truth. Returns dict with ``verdict`` plus
    key:value pairs from the verdict block. Multiple ``repro:`` lines collapse
    into a ``repros`` list.
    """
    last = find_last_standalone_marker(raw, ("REPRODUCED", "REFUTED", "UNVERIFIED"))
    if last is None:
        return {"verdict": VERDICT_UNVERIFIED, "reason": "no_verdict_marker"}

    verdict = last.group(0).strip().rstrip(":")
    body = raw[last.end():]

    kv: dict[str, str] = {}
    repros: list[str] = []
    seen_kv = False
    for line in body.split("\n"):
        if not line.strip():
            if seen_kv:
                break
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        seen_kv = True
        if k == "repro":
            repros.append(v)
        else:
            kv[k] = v

    required = _REQUIRED_KEYS[verdict]
    for key in required:
        if key not in kv:
            return {"verdict": VERDICT_UNVERIFIED, "reason": "malformed_output"}
    if verdict == VERDICT_REPRODUCED and not repros:
        return {"verdict": VERDICT_UNVERIFIED, "reason": "malformed_output"}

    result: dict[str, Any] = {"verdict": verdict, **kv}
    if repros:
        result["repros"] = repros
    return result


def _sanitise_for_unverified_block(text: str) -> str:
    """Collapse newlines + drop colons to prevent kv-injection into UNVERIFIED blocks.

    Stderr / exception text interpolated into a synthetic ``UNVERIFIED:\\n
    reason: agent_error: <text>`` block can contain attacker-controlled
    ``\\nreason: ...`` lines. parse_verdict's last-key-wins kv loop would let
    the injected line overwrite the legitimate ``reason``. Sanitise by
    collapsing newlines to spaces and stripping colons so no second ``key:``
    pair can survive interpolation.
    """
    return text.replace("\n", " ").replace(":", "").strip()


def _invoke_verifier_agent(finding: dict, model_tier: str = "haiku") -> str:
    """Invoke independent verifier subagent. Returns raw stdout.

    On timeout/error returns a synthetic UNVERIFIED: block per W14 spec §4.
    """
    timeout = (
        SEMANTIC_VERIFIER_OPUS_TIMEOUT_SEC
        if model_tier == "opus"
        else SEMANTIC_VERIFIER_TIMEOUT_SEC
    )
    model = get_claude_critical() if model_tier == "opus" else get_claude_fallback()
    if model not in _ACCEPTED_MODEL_ALIASES:
        # Fail closed. An unrecognised --model value is silently downgraded to
        # the session default by the CLI (often a pricier model). Refuse to
        # spawn rather than burn an unintended model tier.
        raise ValueError(
            f"semantic_verifier: refusing to spawn verifier — model {model!r} "
            f"is not an accepted tier alias {sorted(_ACCEPTED_MODEL_ALIASES)}. "
            "Check the models config (config_provider.models_config_path())."
        )

    system_prompt = (
        "ROLE: You are an independent code-fact verifier. A reviewer claimed a bug "
        "exists. Your job is to either REPRODUCE the bug (file:line:quote + runnable "
        "command demonstrating the failure) or REFUTE the finding (rationale why the "
        "claim misreads the code).\n\n"
        "You have NO knowledge of the reviewer's chain of reasoning. Treat each "
        "finding as a hypothesis to falsify."
    )
    user_prompt = (
        f"FINDING (severity: {finding.get('severity', 'UNKNOWN')}):\n"
        f"File: {finding.get('file', '?')}:{finding.get('line', '?')}\n"
        f"Quote: {finding.get('quote', '?')}\n"
        f"Claim: {finding.get('claim', '?')}\n\n"
        "INSTRUCTIONS:\n"
        "1. Read the file (use Read tool); verify the quote.\n"
        "2. Trace the logic. Output ONE OF:\n"
        "   REPRODUCED:\n"
        "   file: <path>:<line>\n"
        "   repro: <command>\n"
        "   repro: <command>\n"
        "   evidence: <one-line proof>\n"
        "   ---\n"
        "   REFUTED:\n"
        "   reason: <one-sentence summary>\n"
        "   rationale: <up to 100 words>\n"
        "   ---\n"
        "   UNVERIFIED:\n"
        "   reason: <why you cannot decide>\n"
        "3. Output ONLY one verdict starting at line begin. No prose before/after.\n"
        "4. Do NOT spawn nested subagents. Do NOT modify files."
    )

    cmd = ["claude", "-p", "--model", model, "--max-turns", "10"]
    full_prompt = f"{system_prompt}\n\n{user_prompt}"

    try:
        result = bounded_run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 124:
            return "UNVERIFIED:\nreason: agent_timeout\n"
        if result.returncode != 0:
            stderr_tail = _sanitise_for_unverified_block(
                (result.stderr or "")[-200:]
            )
            return f"UNVERIFIED:\nreason: agent_error {stderr_tail}\n"
        stdout = result.stdout or ""
        if not stdout.strip():
            return "UNVERIFIED:\nreason: empty_response\n"
        return stdout
    except (OSError, FileNotFoundError) as exc:
        sanitised = _sanitise_for_unverified_block(str(exc))
        return f"UNVERIFIED:\nreason: agent_error {sanitised}\n"


# ──────────────────────────────────────────────────────────────────────────────
# Review-doc parsing helpers
# ──────────────────────────────────────────────────────────────────────────────


def _split_findings(section_body: str) -> list[dict[str, Any]]:
    """Split an Aggregated-Findings section body into finding dicts.

    Each finding starts with a ``### SEVERITY: <LEVEL> — <text>`` header and
    ends at the next such header (or section end). Pre-header content is
    discarded (intro paragraphs / blank lines belong to the section, not to
    any finding).
    """
    lines = section_body.splitlines(keepends=True)
    findings: list[dict[str, Any]] = []
    current: list[str] = []
    in_finding = False
    for line in lines:
        if _SEVERITY_HDR_RE.match(line.rstrip("\n\r")):
            if in_finding and current:
                findings.append(_make_finding(current))
            current = [line]
            in_finding = True
        elif in_finding:
            current.append(line)
    if in_finding and current:
        findings.append(_make_finding(current))
    return findings


def _make_finding(block_lines: list[str]) -> dict[str, Any]:
    raw_block = "".join(block_lines)
    header_line = block_lines[0].rstrip("\n\r")
    body_text = "".join(block_lines[1:])
    severity_match = _SEVERITY_HDR_RE.match(header_line)
    severity = severity_match.group(1) if severity_match else "UNKNOWN"
    is_low_trust = any(tag in header_line for tag in _LOW_TRUST_TAGS)

    # Best-effort file/line/quote extraction from a `> path:line: quote` line.
    file_path = ""
    line_no = ""
    quote = ""
    for ln in block_lines[1:]:
        m = re.match(r"^>\s+(\S+):(\d+):\s+(.+)$", ln.rstrip("\n\r"))
        if m:
            file_path = m.group(1)
            line_no = m.group(2)
            quote = m.group(3)
            break

    claim = ""
    for ln in block_lines[1:]:
        stripped = ln.strip()
        if stripped.startswith("Claim:"):
            claim = stripped[len("Claim:"):].strip()
            break

    return {
        "header_text": header_line,
        "body_text": body_text,
        "raw_block": raw_block,
        "severity": severity,
        "is_already_low_trust": is_low_trust,
        "file": file_path,
        "line": line_no,
        "quote": quote,
        "claim": claim,
    }


def _locate_section(lines: list[str], header: str) -> tuple[int | None, int]:
    """Return (start_index_of_header, end_index_exclusive)."""
    start: int | None = None
    end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n\r")
        if stripped == header:
            start = i
        elif start is not None and stripped.startswith("## ") and i != start:
            end = i
            break
    return start, end


def _has_critical_or_high_findings(review_doc_path: Path) -> bool:
    """C2565639: scan ``## Aggregated Findings`` for CRITICAL or HIGH severity.

    Returns True iff at least one ``### SEVERITY: (CRITICAL|HIGH) —`` header
    appears inside the Aggregated Findings section. Refuted-section findings
    are excluded (already demoted). Conservative on read failure / missing
    section: returns False so the expensive verifier is skipped, matching the
    spirit of the FAIL-skip gate (cost not justified when we can't even parse).
    """
    try:
        doc_text = review_doc_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
        return False

    lines = doc_text.splitlines(keepends=True)
    agg_start, agg_end = _locate_section(lines, "## Aggregated Findings")
    if agg_start is None:
        return False

    for ln in lines[agg_start + 1:agg_end]:
        m = _SEVERITY_HDR_RE.match(ln.rstrip("\n\r"))
        if m and m.group(1) in ("CRITICAL", "HIGH"):
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Main step
# ──────────────────────────────────────────────────────────────────────────────


def verify_findings_semantic(ctx, prev) -> object:
    """Semantic re-verification of phase_6 review findings.

    For each finding in ``## Aggregated Findings``, spawn an independent
    verifier subagent. REFUTED findings move to a new ``## Refuted (Semantic)``
    section. UNVERIFIED findings keep place but get their severity prefixed
    with ``[UNVERIFIED]``. Already-low-trust findings (UNCITED / UNVERIFIED
    CITATION from step 4) skip the agent call. Cap of
    ``MAX_SEMANTIC_VERIFY_FINDINGS`` per phase invocation; overflow gets
    ``[UNVERIFIED OVERFLOW]`` tag and counts as UNVERIFIED.
    """
    from bytedigger_engine.contracts import StepResult  # type: ignore[import]

    started_ms = int(time.monotonic() * 1000)

    if not hasattr(prev, "data") or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="verify_findings_semantic",
            error="prev step did not produce data",
            error_code="E_MISSING_PREV_DATA",
        )

    forwarded = dict(prev.data)
    counters = {
        "semantic_reproduced": 0,
        "semantic_refuted": 0,
        "semantic_unverified": 0,
        "semantic_skipped": 0,
    }
    model_haiku_calls = 0
    model_opus_calls = 0

    # F60FED11 + C2565639: when phase_6 verdict is FAIL AND no CRITICAL/HIGH
    # findings exist, the build is heading to fix iteration on low-severity
    # noise — skip semantic verifier (saves ~$0.025 × N + ~6min wall). When
    # CRIT/HIGH are present, verifier cost is justified: fabricated CRIT/HIGH
    # must be refuted at this gate, not propagated into expensive fix cycles.
    # PASS/PARTIAL/UNKNOWN/missing always run. Future refute-rate alert MUST
    # filter on semantic_skipped_due_to_fail to avoid baseline pollution.
    if prev.data.get("verdict") == "FAIL":
        review_doc_path_str = prev.data.get("review_doc_path")
        has_crit_high = bool(
            review_doc_path_str
            and _has_critical_or_high_findings(Path(review_doc_path_str))
        )
        if not has_crit_high:
            skip_duration_ms = int(time.monotonic() * 1000) - started_ms
            if EventLog is not None:
                try:
                    EventLog().append(
                        "phase_6_semantic_verify_skipped",
                        {
                            "step_name": "verify_findings_semantic",
                            "reason": "verdict_fail",
                            "has_critical_or_high": False,
                            "duration_ms": skip_duration_ms,
                        },
                        run_id=getattr(ctx, "session_id", None),
                    )
                except (OSError, ValueError, TypeError) as exc:
                    logger.warning("event_log emit failed: %s", exc, exc_info=True)
            return StepResult(
                status="ok",
                data={
                    **forwarded,
                    **counters,
                    "semantic_refute_rate": 0.0,
                    "semantic_skipped_due_to_fail": True,
                },
                duration_ms=skip_duration_ms,
                step_name="verify_findings_semantic",
            )

    review_doc_path_str = prev.data.get("review_doc_path")
    if not review_doc_path_str:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="verify_findings_semantic",
            error="prev step did not provide review_doc_path",
            error_code="E_MISSING_REVIEW_DOC_PATH",
        )
    review_doc_path = Path(review_doc_path_str)

    try:
        doc_text = review_doc_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError) as exc:
        return StepResult(
            status="error",
            data={**forwarded, **counters},
            duration_ms=0,
            step_name="verify_findings_semantic",
            error=str(exc),
            error_code="E_SEMANTIC_VERIFY_READ_FAILED",
        )

    lines = doc_text.splitlines(keepends=True)
    agg_start, agg_end = _locate_section(lines, "## Aggregated Findings")

    if agg_start is None:
        return StepResult(
            status="ok",
            data={
                **forwarded, **counters,
                "review_doc_path": str(review_doc_path),
                "semantic_refute_rate": 0.0,
            },
            duration_ms=int(time.monotonic() * 1000) - started_ms,
            step_name="verify_findings_semantic",
        )

    section_body = "".join(lines[agg_start + 1:agg_end])
    findings = _split_findings(section_body)

    # Escalation to Opus is suppressed when the total finding count already
    # saturates the per-phase budget (len(findings) > MAX). Token budget is
    # tighter than semantic completeness in that regime.
    escalation_allowed = len(findings) <= MAX_SEMANTIC_VERIFY_FINDINGS

    refuted_blocks: list[str] = []
    new_finding_blocks: list[str] = []

    for idx, finding in enumerate(findings):
        if idx >= MAX_SEMANTIC_VERIFY_FINDINGS:
            tagged_header = "[UNVERIFIED OVERFLOW] " + finding["header_text"] + "\n"
            new_finding_blocks.append(tagged_header + finding["body_text"])
            counters["semantic_unverified"] += 1
            continue

        if finding["is_already_low_trust"]:
            counters["semantic_skipped"] += 1
            new_finding_blocks.append(finding["raw_block"])
            continue

        raw = _invoke_verifier_agent(finding, model_tier="haiku")
        model_haiku_calls += 1
        parsed = parse_verdict(raw)

        if (
            escalation_allowed
            and parsed.get("verdict") == VERDICT_UNVERIFIED
            and parsed.get("reason") == "cannot_decide"
        ):
            raw = _invoke_verifier_agent(finding, model_tier="opus")
            model_opus_calls += 1
            parsed = parse_verdict(raw)

        verdict = parsed.get("verdict", VERDICT_UNVERIFIED)

        if verdict == VERDICT_REFUTED:
            counters["semantic_refuted"] += 1
            reason = parsed.get("reason", "")
            rationale = parsed.get("rationale", "")
            refuted_blocks.append(
                f"### {finding['header_text'].lstrip('#').strip()}\n\n"
                f"**Reason:** {reason}\n\n"
                f"**Rationale:** {rationale}\n\n"
            )
        elif verdict == VERDICT_REPRODUCED:
            counters["semantic_reproduced"] += 1
            new_finding_blocks.append(finding["raw_block"])
        else:
            counters["semantic_unverified"] += 1
            tagged_header = re.sub(
                r"^### SEVERITY:\s+",
                "### SEVERITY: [UNVERIFIED] ",
                finding["header_text"],
            ) + "\n"
            new_finding_blocks.append(tagged_header + finding["body_text"])

    needs_rewrite = (
        counters["semantic_refuted"] > 0
        or counters["semantic_unverified"] > 0
    )

    if needs_rewrite:
        # C1 fix: Refuted (Semantic) goes AFTER Aggregated, BEFORE Unverified.
        # Step 4 (helper.py:243-246) appends "## Unverified Findings" at end-of-doc,
        # so the natural section order produced upstream is:
        #   Aggregated → Unverified
        # We insert Refuted between them. Final order (highest→lowest signal):
        #   Aggregated (verified) → Refuted (demoted) → Unverified (low-trust)
        new_doc_lines: list[str] = list(lines[:agg_start])

        new_doc_lines.append("## Aggregated Findings\n")
        # Preserve any pre-finding content (blank lines, intro paragraph).
        pre_finding_chars = ""
        for ln in lines[agg_start + 1:agg_end]:
            if _SEVERITY_HDR_RE.match(ln.rstrip("\n\r")):
                break
            pre_finding_chars += ln
        if pre_finding_chars:
            new_doc_lines.append(pre_finding_chars)
        for block in new_finding_blocks:
            new_doc_lines.append(block)

        if refuted_blocks:
            # Ensure separator before Refuted header.
            if new_doc_lines and not new_doc_lines[-1].endswith("\n\n"):
                new_doc_lines.append("\n")
            new_doc_lines.append("## Refuted (Semantic)\n\n")
            for block in refuted_blocks:
                new_doc_lines.append(block)
            if not new_doc_lines[-1].endswith("\n"):
                new_doc_lines.append("\n")

        new_doc_lines.extend(lines[agg_end:])
        new_doc = "".join(new_doc_lines)

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
                step_name="verify_findings_semantic",
                error=str(exc),
                error_code="E_SEMANTIC_VERIFY_WRITE_FAILED",
            )

    denominator = max(1, counters["semantic_reproduced"] + counters["semantic_refuted"])
    refute_rate = counters["semantic_refuted"] / denominator

    duration_ms = int(time.monotonic() * 1000) - started_ms

    if EventLog is not None:
        try:
            EventLog().append(
                "phase_6_semantic_verify_complete",
                {
                    "step_name": "verify_findings_semantic",
                    **counters,
                    "semantic_refute_rate": refute_rate,
                    "model_haiku_calls": model_haiku_calls,
                    "model_opus_calls": model_opus_calls,
                    "duration_ms": duration_ms,
                },
                run_id=getattr(ctx, "session_id", None),
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("event_log emit failed: %s", exc, exc_info=True)

    return StepResult(
        status="ok",
        data={
            **forwarded,
            **counters,
            "review_doc_path": str(review_doc_path),
            "semantic_refute_rate": refute_rate,
        },
        duration_ms=duration_ms,
        step_name="verify_findings_semantic",
    )
