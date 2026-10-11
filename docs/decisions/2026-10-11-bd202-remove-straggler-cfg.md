# bd#202: remove the deprecated `straggler_cfg` kwarg

**Status: FROZEN r1** · **Tier:** 2 (engine prod `.py`, public backend seam; breaking) · **Class:** PROCESS (dead-argument removal, closes the #89 P3b1b-i deprecation window early; kill-by 2027-01-31 was the old deadline).
**Chokepoint:** `invoke_llm_subprocess` (`engine_py/bytedigger_engine/llm_subprocess.py`), the single public entry. It forwards the kwarg to `_resolve_backend_and_invoke`, which passes `straggler_cfg=None` to every backend through the `LLMBackend` protocol. Removing it at the entry, the dispatcher and the protocol removes it everywhere.
**Side of the seam:** engine, `examples/library/custom_backend.py`, CHANGELOG, `docs/backends.md`, tests. No orchestrator md, no TS, no data/prompt/config.

## §1 Problem

`straggler_cfg` has been inert since #89 P3b1b-i. It remains:
- an accepted kwarg of `invoke_llm_subprocess`; a non-None value emits `straggler_cfg_ignored`;
- a required keyword of `LLMBackend.__call__`, of the five reference backends (`agent_sdk`, `anthropic_api`, `openai_compat`, `pydantic_anthropic`, `pydantic_openai`) and of `examples/library/custom_backend.py`;
- an internal parameter of `_invoke_subprocess`, `_dispatch`-side helpers and the registry wrappers in `llm_subprocess.py`;
- passed as `straggler_cfg=None` in ~110 test call sites across ~35 test files.

## §2 Change

1. `invoke_llm_subprocess(..., straggler_cfg=...)` -> `TypeError` (unexpected keyword). The `straggler_cfg_ignored` event and its emit block are deleted.
2. `LLMBackend.__call__`, the claude-subprocess handler, the dispatcher call and every internal helper drop the parameter. The dispatcher no longer passes the keyword to any backend.
3. The five reference backends and `examples/library/custom_backend.py` drop the parameter (and its docstring mentions).
4. `docs/backends.md` stops saying the keyword is passed; CHANGELOG gets a **BREAKING** entry: out-of-tree backends that declare `straggler_cfg` as a required keyword must drop it (the dispatcher no longer supplies it); callers passing it get `TypeError`.
5. The `abort` capability token is kept (out of scope, still accepted).
6. Tests: occurrences of `straggler_cfg=None` in sibling tests (call kwargs, fake-backend signatures, assertions on it) are removed mechanically by one script. `test_bd89_p3b1b_i_straggler_removed.py` asserts the old deprecation behaviour (AC5/AC6/AC7/AC9/AC10) and is rewritten by hand to the new contract.

## §3 Acceptance criteria (RED file `engine_py/tests/test_bd202_straggler_cfg_removed.py`)

- AC1: `invoke_llm_subprocess(..., straggler_cfg=None)` and with a dict raise `TypeError`; `"straggler_cfg"` is not in the signature.
- AC2: `"straggler_cfg"` is not a parameter of `LLMBackend.__call__`, of the claude-subprocess handler, nor of the five reference backends' call functions.
- AC3: a call through the dispatcher to a recording fake backend registered via `register_backend` (fake signature has no `straggler_cfg`) succeeds, and the recorded kwargs contain no `straggler_cfg`.
- AC4: provider-agnostic: AC3 holds for the subscription path (`claude-subprocess` handler, argv/Popen mocked as existing tests do) and the API-token path (an API-style reference backend with a faked client); neither path sees the keyword.
- AC5: no `straggler_cfg_ignored` string in `llm_subprocess.py`; no event emitted on a normal call.
- AC6: `examples/library/custom_backend.py` has no `straggler_cfg` and its backend is callable by the dispatcher.
- AC7: CHANGELOG top section mentions `straggler_cfg` with "BREAKING" and `#202`; `docs/backends.md` no longer claims the keyword is passed.
- AC8 (GUARD): the `abort` token stays in `_DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"]`.

## §4 Non-goals

No change to watchdog/idle-timeout behaviour, `abort` capability, historical decision docs and old CHANGELOG entries (history stays as written).

## §5 Rollback

Revert the PR. No kill switch: the lot removes a dead mechanism and introduces none.
