"""bd#206: review-invocation provenance for class-M carries (M2, M3).

ONE writer/reader pair. `write_review_provenance` records, next to the review
doc, which `invoke_review_llm` attestation produced it; `review_source_id`
returns the matching `source_id` for a later step to declare, or None.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bytedigger_engine.io_utils import atomic_write

PROVENANCE_FILENAME = "review_invocation.json"
REVIEW_STEP_NAME = "invoke_review_llm"
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def provenance_path(review_doc_path: str) -> Path:
    return Path(review_doc_path).parent / PROVENANCE_FILENAME


def write_review_provenance(review_doc_path: str, run_id: str, invocation_id: str) -> None:
    path = provenance_path(review_doc_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps({
            "step_name": REVIEW_STEP_NAME,
            "invocation_id": invocation_id,
            "run_id": run_id,
            "review_doc_path": review_doc_path,
        }),
    )


def review_source_id(review_doc_path: "str | None", run_id: "str | None") -> "str | None":
    """`invocation:invoke_review_llm:<id>` or None; never raises."""
    try:
        if not review_doc_path or not run_id:
            return None
        body = json.loads(provenance_path(review_doc_path).read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            return None
        inv = body.get("invocation_id")
        if (
            body.get("run_id") != run_id
            or body.get("review_doc_path") != review_doc_path
            or body.get("step_name") != REVIEW_STEP_NAME
            or not isinstance(inv, str)
            or not _ID_RE.fullmatch(inv)
        ):
            return None
        return f"invocation:{REVIEW_STEP_NAME}:{inv}"
    except Exception:  # noqa: BLE001 - reader contract: every failure is None
        return None
