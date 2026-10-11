# bd#202 gate r1 (TIER2)

Spec: docs/decisions/2026-10-11-bd202-remove-straggler-cfg.md. RED: engine_py/tests/test_bd202_straggler_cfg_removed.py (18 fail at assert time, 31 siblings green per preflight).

## Findings

Sound: RED asserts rejection and non-passing (TypeError, signature absence, kwargs spy), not mere mention removal. Subscription (real claude-subprocess handler, Popen mocked) and API-token (real anthropic-api, urlopen faked) both covered. Recording wrappers delegate to the real UUT, so they are not vacuous. A `**kwargs` spy and an explicit-signature fake are both used.

1. MAJOR: AC8 (GUARD, `abort` stays in `_DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"]`) has no test. The RED file contains no `abort` reference. The guard was meant to move out of test_bd89_p3b1b_i, whose AC5/6/7/9/10 GREEN will rewrite, so nothing would protect it. Add the assertion to the RED file.
2. MINOR: AC1 does not cover a positional pass, because the entry point is keyword-only or not. Add one test that asserts `straggler_cfg` is absent from `invoke_llm_subprocess` and that surplus positional or keyword arguments raise TypeError.
3. MINOR: AC2 checks `_invoke_subprocess` and the handler only. The spec also names the internal helpers, `_resolve_backend_and_invoke` and the registry wrappers in `llm_subprocess.py`. Strengthen AC5 by asserting `"straggler_cfg" not in` the whole `llm_subprocess.py` source, which would catch leftovers.
4. MINOR: AC7 says "top CHANGELOG section" but `entry_sections` searches every unreleased and release section. Old entries that mention straggler_cfg could in principle match. Constrain to the first section or require BREAKING and #202 near straggler_cfg.
5. MINOR: Spec section 2 point 4 and AC2 say nothing about out-of-tree backends that still declare the keyword as required. They now get TypeError from the dispatcher, which is the documented BREAKING change, so there is no gap beyond the CHANGELOG assertion.
6. MINOR: Mention in the GREEN notes that other callers in workflows/*.py and the docs/TS mentions need a grep sweep. The preflight grep outside tests, decisions and CHANGELOG finds only llm_subprocess.py, the 5 backends, the example and docs/backends.md, so none exist today.

## Required fixes

1. Add an AC8 test: `"abort"` is in `llm_subprocess._DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"]`.
2. Optional but recommended: findings 2 to 4.

VERDICT: RETURN
