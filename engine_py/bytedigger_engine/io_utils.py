"""Shared low-level IO helpers for engine_py workflows."""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """Write content atomically via temp+rename so a mid-write kill leaves
    the target file either unchanged (pre-existing) or fully written.
    Promoted from per-phase duplicates (phase_45_spec/_lite) to shared
    utility under agreement 3ECCFF8E.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
