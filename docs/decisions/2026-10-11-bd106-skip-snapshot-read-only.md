---
red_tests:
  - engine_py/tests/test_bd106_skip_snapshot_read_only.py
red_pins:
  - engine_py/tests/test_bd106_snapshot_kept_guards.py
sibling_tests:
  - engine_py/tests/test_bd106_snapshot_kept_guards.py
  - engine_py/tests/test_bd71_agent_sdk_observations.py
  - engine_py/tests/test_gh1169_agent_sdk_hang_recovery.py
  - engine_py/tests/test_gh933_agent_sdk_stderr_outage.py
  - engine_py/tests/test_gh1157_agent_sdk_retry.py
  - engine_py/tests/test_bd82_tool_restriction.py
  - engine_py/tests/test_bd105_restricted_setting_sources.py
  - engine_py/tests/test_gh780_manifest_nondict.py
paths:
  - engine_py/bytedigger_engine/lib/reference_backends/agent_sdk.py
  - docs/configuration.md
  - CHANGELOG.md
---
# bd#106: agent-sdk does not snapshot git state for a call that cannot write

**Status: r1, TIER2.** Follow-up to #82 part 3 (#98).
**Class:** SYSTEMATIC: the manifest (`worker_written_paths`) is built from a before/after git snapshot that costs `rev-parse`, porcelain, untracked listing, one `hash-object` per dirty path and a `diff`, and it runs inside the call's timeout. For a call whose tool list cannot write, nobody reads the manifest, and the cost grows with the dirty files of the worktree (semantic verifier: up to ~40 calls per phase 6 run, 60s timeout on haiku).
**Chokepoint:** `agent_sdk_backend`, the one function that takes the pre snapshot (l.403) and builds the manifest on the timeout path (l.575) and the success path (l.667).
**Side of the seam:** engine. **Owner:** bd.

| | |
|---|---|
| **LLM calls added** | None. One pure predicate over `allowed_tools`, one best-effort config read. |

## Principles

1. **Deterministic first.** The decision is a pure function of the declared tool list and one config value.
2. **Fail toward the old behaviour.** An unknown, empty, absent or non-list tool list, and a switch value other than absent or `false`, keep the snapshot. An absent key, or a missing or malformed models file, is the unconfigured default: skip.
3. **Provider-agnostic.** Only `agent_sdk.py` changes; other backends and their manifest code are untouched. Subscription and API-token paths run the same code (the snapshot is local git, credentials are not involved).

## Design

`_call_can_write(allowed_tools) -> bool`, in `agent_sdk.py`:
- `None` (unrestricted worker), `[]` or any non-list: **True** (can write; keeps the snapshot).
- Otherwise the base name of each entry (`available_tools`, so `Bash(x:*)` is `Bash`) is compared with `{Write, Edit, Bash, MultiEdit, NotebookEdit}`; any hit: **True**. `MultiEdit` and `NotebookEdit` are write tools too (a deliberate superset of the issue's three names: a missed write tool would silently drop a manifest, a spurious one only costs a snapshot).
- Else **False**.

`_snapshot_enabled()`: reads `restricted_roles.skip_read_only_snapshot` from the models config (`config_provider.models_config_path()`, best-effort like the sibling keys). Unset, missing or unreadable file: **skip enabled** (the new default). Literal `false`: **disabled**, the unconditional snapshot is restored. Any other value: disabled (fail toward the old behaviour).

Skipping applies when `not _call_can_write(allowed_tools)` and the switch is enabled. A skipped call takes no `pre` snapshot and builds no manifest: `worker_written_paths` is `[]`, `manifest_source` stays `"git_diff"` (the closed enum is not widened), on both the success and the timeout path. A call that can write is byte-for-byte as before. The `_is_git_repo` guard before the snapshot is not part of the snapshot and stays.

## Citations

- `engine_py/bytedigger_engine/lib/reference_backends/agent_sdk.py` `agent_sdk_backend` takes the snapshot and builds the manifest on both result paths.
- `engine_py/bytedigger_engine/lib/reference_backends/agent_sdk.py` adds `_call_can_write` and `_snapshot_enabled` (new, introduced by this lot).
- `engine_py/bytedigger_engine/lib/reference_backends/pydantic_openai.py` `_snapshot_pre_state`, `_manifest_since` and `_is_git_repo` are unchanged.
- `engine_py/bytedigger_engine/llm_subprocess.py` `available_tools` gives the base name of each entry.

## Acceptance criteria

Real paths only: a real temporary git tree with tracked-modified and untracked dirty files; git launches are counted by wrapping the real `Popen` that `_git_run` uses (`pydantic_openai._REAL_POPEN`), delegating to it. Only `claude_agent_sdk.query` is replaced. Every call goes through `invoke_llm_subprocess(backend="agent-sdk")`.

| AC | Case | Expectation |
|---|---|---|
| AC1 | `["Read","Grep","Glob"]`, dirty tree | the only git launch is the `rev-parse --is-inside-work-tree` guard: none of `diff`, `status`, `ls-files`, `hash-object`, `rev-parse HEAD`; `worker_written_paths == []`; status ok |
| AC1b | same read-only list, the call times out | no snapshot launches either; `worker_written_paths == []` on the timeout result |
| AC2 | `["Read","Write"]`, a file written during the call | manifest lists exactly that file; snapshot commands run (diff and hash-object launched) |
| AC2b | `["Read","Bash(git:*)"]` and `["Edit"]` | treated as can-write: snapshot commands run |
| AC3 | switch `false` in a real models file, read-only list | snapshot commands run, as before |
| AC3b | switch absent / malformed file / `"yes"` | absent and malformed file: skipped; `"yes"`: snapshot runs |
| AC4 | `allowed_tools=None` and `[]` | snapshot commands run |
| AC5 | the same read-only call on subscription (`CLAUDE_CODE_OAUTH_TOKEN`) and API-token (`ANTHROPIC_API_KEY`) environments | identical outcome: no snapshot launches |

## Docs

`docs/configuration.md`, section "Model pinning (engine)": one paragraph after the `restricted_roles.setting_sources` paragraph.

## Known limits

- A skipped call reports `worker_written_paths: []`. Callers that treat a read-only call's empty manifest as "wrote nothing" are correct; a tool that is not one of the five names and still writes (an MCP tool) is not covered, as for the issue.
- The `_is_git_repo` guard still spawns one git command per call.

## Kill switch

`restricted_roles.skip_read_only_snapshot: false` in the models config restores the unconditional snapshot. Removing the switch removes the branch; nothing else depends on it.
