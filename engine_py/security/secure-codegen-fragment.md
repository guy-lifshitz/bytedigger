## SECURE CODING DEFAULTS (SECBUILD L1) — apply to ALL generated code.
- Allowlist-validate ALL external input (args, env, files, tool/LLM output); schema-check parsed data; safe loaders. [V2][V15]
- Escape for the target interpreter (shell, SQL, log, filename); parameterized queries. [V1]
- Subprocesses: argv list, no shell; set timeouts; check exit codes and signal deaths; propagate failures. [V1][V16]
- Containment-check external paths against allowed base; least-privilege perms; exclusive temp creates; reuse vetted helpers. [V1][V15]
- No secrets in source, argv, or logs — env/secure store only. [V13]
- Security gates fail CLOSED: on error or ambiguity, deny — never continue. [V15]
- Catch specific exceptions; caller errors generic, detail logged; sanitize log values; roll back partial state. [V16]
