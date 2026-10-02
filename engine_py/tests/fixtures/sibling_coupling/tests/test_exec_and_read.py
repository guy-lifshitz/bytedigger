"""GH1200 fixture (AC42, gate round-2 findings N8/N9) — carries BOTH a spawn
primitive and a read primitive against the same production artifact.

Spec §1.3 fixes the path-family label per FILE with precedence exec > read >
bare, so this file must resolve to `exec-invocation`, not `source-read`.

It also exercises the `bash "` spawn token (the shape used 33× by the real
`sibling-test-audit.test.sh`), which no other fixture carries.

The artifact's basename appears on EXACTLY ONE line (the `TARGET =` line), so
"exactly one path-family row for this file" is a property of the labelling, not
of how many times the name happens to be typed.
"""

import subprocess
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "prod" / "cli_target.sh"

# The quoted-bash spawn shape, verbatim: bash "$TARGET"
SHELL_FORM = 'bash "$TARGET"'


def test_spawn_takes_precedence_over_read():
    proc = subprocess.run(["bash", str(TARGET)], capture_output=True)
    assert proc.returncode == 0
    body = TARGET.read_text()
    assert body
    assert SHELL_FORM
