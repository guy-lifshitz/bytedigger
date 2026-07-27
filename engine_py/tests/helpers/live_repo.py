"""bd#2 — declare the suite's git-checkout requirement + honest skip.

`helpers/host_tools.py` (bd#102) declares one axis of the suite's environment:
which *binaries* are on `PATH`. This module declares the second: the *shape of
the tree* the suite runs in. A handful of tests resolve the project by climbing
to an entry named `.git`, and `git archive`, an installed wheel and a release
tarball all ship tracked files without one. Those tests are not wrong about
what they assert; they assert it against a subject that exists only inside a
git checkout of this project.

Deliberately NOT a `HOST_TOOLS` entry named `.git`: `host_tool_skip_reason`
renders "requires host tool '<name>'", and reporting a missing *binary* for a
missing *checkout* would be the same misleading message this exists to remove.

Like `host_tools`, availability is frozen ONCE from `conftest.pytest_configure`
and never probed live afterward — `test_gh1220_ambient_cwd_commit_refusal.py`
monkeypatches `_live_repo_sentinel.find_repo_root` process-wide for AC18-AC21,
so a live lookup would report "no checkout" inside those tests on a machine
that has one. And, for the same reason as `host_tools`, this module must never
define `pytest_configure`: it is not registered as a plugin, so such a hook
would never run and would leave the map empty, silently no-opping the guard.

Resolution is delegated to `_live_repo_sentinel.find_repo_root` rather than
reimplemented (§1g) — one climb algorithm, in the module that owns it.
"""
from __future__ import annotations

import pytest

GIT_CHECKOUT_REQUIREMENT = (
    "requires a git checkout of this repository (this tree has no '.git') — "
    "see docs/host-requirements.md"
)

# Frozen once at pytest_configure time (freeze_git_checkout_availability).
# Never populated by a live find_repo_root() call from the guard.
_GIT_CHECKOUT_AVAILABLE: dict[str, bool] = {}


def freeze_git_checkout_availability() -> None:
    """Populate `_GIT_CHECKOUT_AVAILABLE` once, from a real climb.

    Called from inside conftest.py's existing `pytest_configure` — never from
    a second `pytest_configure` defined here.

    Fails OPEN on an unimportable sentinel (records "checkout present"): in
    that case the tests this guards fail on their own `import` line, which is
    a genuine defect and must stay visible rather than become a skip.
    """
    try:
        import _live_repo_sentinel as sentinel  # noqa: PLC0415
    except ImportError:
        _GIT_CHECKOUT_AVAILABLE["resolved"] = True
        return
    _GIT_CHECKOUT_AVAILABLE["resolved"] = sentinel.find_repo_root() is not None


def skip_without_git_checkout() -> None:
    """Skip the current test when this tree is not a git checkout.

    Reads the frozen map — never a live climb — so it cannot be spoofed by the
    `find_repo_root` monkeypatches some tests install. A map that was never
    frozen (`{}`) does not skip: an unwired mechanism must not silently pass
    the suite by skipping it.
    """
    if _GIT_CHECKOUT_AVAILABLE.get("resolved") is False:
        pytest.skip(GIT_CHECKOUT_REQUIREMENT)
