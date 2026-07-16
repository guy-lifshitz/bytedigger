from pathlib import Path


def resolve_worktree_root(ctx, scratchpad: Path) -> Path:
    """Resolve the worktree root the worker should treat as its edit boundary.

    Single source-of-truth (4217B211). Preference order:
      1. ``ctx.org_config["current_worktree_path"]`` (matches phase_8_post_deploy).
      2. Climb from ``scratchpad`` until ``.git`` (file or dir) is found — that
         directory IS a checkout root (worktree or main repo).
      3. Fallback to ``Path.cwd()``.
    """
    cfg = ctx.org_config or {}
    raw = cfg.get("current_worktree_path")
    if raw:
        return Path(raw).expanduser().resolve()
    try:
        cur = scratchpad.resolve()
    except OSError:
        return Path.cwd().resolve()
    for parent in [cur, *cur.parents]:
        if (parent / ".git").exists():
            return parent
    return Path.cwd().resolve()
