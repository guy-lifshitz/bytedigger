"""GH1200 fixture (AC19) — EXECUTES the production CLI artifact.

Names the artifact's basename and spawns it; it never reads its text, so a
read-primitive-only path family (spec v1) is blind to this coupling.
"""

import subprocess
from pathlib import Path


def test_cli_target_emits_its_mode():
    target = Path(__file__).resolve().parent.parent / "prod" / "cli_target.sh"
    proc = subprocess.run(["bash", str(target)], capture_output=True)
    assert proc.returncode == 0
