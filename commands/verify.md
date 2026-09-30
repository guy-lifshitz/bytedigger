# ByteDigger verify — run every check this repo declares

Report only. This command edits no tracked file: it runs the repo's verification
skills and writes one report to the scratchpad.

## What counts as a verification skill

A `SKILL.md` under `skills/*/`, `.claude/skills/*/` or a directory listed in
`verification_skill_dirs` whose frontmatter carries:

```yaml
metadata:
  verification: true
  verify_command: 'python3 scripts/check.py'   # optional
```

- With a non-empty `verify_command` the skill is a `command` skill: the engine runs it.
- Without one it is an `agent` skill: the engine lists it and this command runs it
  through an agent (step 2).

## Steps

1. Run the engine registry:

   ```bash
   python3 -m bytedigger_engine.run verify --repo <repo> [--extra-dir DIR]... [--timeout SEC]
   ```

   Pass one `--extra-dir` per entry of `verification_skill_dirs`, so the command sees
   the same registry as phase 5. `--list` only lists the skills and runs nothing.
   Exit `0` = every check passed, `1` = a check failed (or an error row), `2` = usage error.
   The JSON report goes to stdout; each skill has a `status`: `pass`, `fail`, `timeout`,
   `error`, `mutated` (the check changed the working tree), `agent` or `listed`.

2. For every skill with status `agent`, spawn one read-only agent (Read, Grep, Glob
   only) that follows that skill's `SKILL.md` against the current working tree and
   returns findings. The agent must not edit files.

3. Write one combined report to `$SCRATCHPAD/reviews/verification-report.md`: the
   engine summary, one section per command skill (status, exit code, output tail) and
   one section per agent skill (its findings).

## Rules

- Never edit, stage or commit a tracked file. A `mutated` status is reported, never
  repaired.
- A check must not leave non-ignored files behind (build artefacts, caches): any new
  file that `.gitignore` does not cover counts as `mutated`.
- Phase 5 runs the same registry automatically (step `verify_registered_skills`);
  this command is the on-demand twin that also covers `agent` skills.
