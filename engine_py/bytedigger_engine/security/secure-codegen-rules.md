# Secure Coding Defaults for Generated Code

Purpose: these are the security patterns generated code follows BY DEFAULT. Each rule states the default the implementation uses, distilled from OWASP ASVS 5.0 and recurring real-world failure classes; calibrated for CLI tools, build/automation pipelines, subprocess orchestration, and filesystem code.

## Input & Data Validation

- All external input (CLI args, stdin, env vars, file contents, network responses) is validated against an expected type, range, and format before use. [V2]
- Validation is allowlist-based: input is checked against what is permitted, not scanned for known-bad patterns. [V2]
- Output of LLMs, subprocesses, and other tools is treated as untrusted input and validated before any code acts on it (branches, paths, commands). [V2]
- Parsed structured data (JSON, YAML, TOML) is validated against an expected schema before its fields are read. [V2]
- Numeric limits, length caps, and count bounds on input are enforced explicitly rather than assumed. [V2]
- Business-logic steps run in their required order and reject out-of-sequence or replayed requests. [V2]
- Deserialization uses safe, data-only loaders (e.g. safe-load equivalents); constructors that instantiate arbitrary types are never the default. [V15]
- Text destined for a different interpreter (shell, SQL, HTML, log line, filename) is encoded or escaped for that specific context at the point of output. [V1]
- Regex assembled from variable or user-supplied fragments has those fragments escaped at construction time. [V1]
- Database access, when present, uses parameterized queries or a query builder; values are never concatenated into query strings. [V1]

## Subprocess & Shell Execution

- Subprocesses are launched with an argument vector (list form), never by interpolating variables into a shell string. [V1]
- Shell interpretation is off by default; a shell is invoked only when a specific need requires it and the command is fully controlled. [V1]
- Executables are resolved by absolute path or from a controlled PATH, so the intended binary is the one that runs. [V15]
- Subprocess exit handling distinguishes normal exit codes from signal deaths: a SIGTERM/SIGKILL (negative rc or signal branch) is handled explicitly, not collapsed into a generic nonzero check. [V16]
- A nonzero or signal exit from a subprocess propagates as a failure; its return code is inspected rather than ignored. [V16]
- Subprocess calls set an explicit timeout and handle the timeout branch. [V16]
- User- or tool-derived values passed to a subprocess are validated and passed as discrete arguments, never spliced into the command line. [V1]

## Filesystem & Path Handling

- Paths built from external input are normalized and confirmed to resolve inside an allowed base directory before any read or write. [V1]
- Path traversal sequences and absolute-path injection are neutralized by resolution-and-containment checks, not string filtering. [V1]
- Files are created with least-privilege permissions by default; new files are not world-readable or world-writable. [V15]
- Temporary and scratch files are created in a dedicated, access-restricted scratch directory with tight permissions, never in a shared world-readable temp location. [V15]
- Temp files are created atomically (exclusive-create or secure temp APIs) to avoid predictable-name and symlink races. [V15]
- Symlinks in untrusted paths are resolved and validated before the target is opened. [V1]
- File operations check for existence and readability before reading, and handle the missing/unreadable case explicitly. [V16]

## Secrets & Configuration

- Secrets are read from a secure store or injected environment at runtime; they are never hardcoded in source, defaults, or test fixtures. [V13]
- Secrets are never passed on the command line (argv is visible to other processes) and never printed in env dumps, debug output, or logs. [V13]
- Configuration has secure defaults; enabling a less-safe option requires an explicit, visible opt-in. [V13]
- Configuration values that alter security behavior are validated on load, and an invalid value fails loudly rather than silently degrading. [V13]
- Credentials and tokens are scoped to the minimum privilege the task needs. [V13]
- Numeric thresholds that gate behavior (byte caps, timeouts, counts) are calibrated against the live environment and read from configuration; environment-sensitive values are never hardcoded as exact literals. [V13]

## Error Handling & Logging

- Gates, validators, and security checks fail CLOSED: on driver error, ambiguous state, or unexpected exception, the default outcome is deny/abort, never allow/continue. [V15]
- Errors are caught by specific type and handled deliberately; broad catch-alls do not silently swallow failures. [V16]
- Error messages returned to callers are generic; stack traces, internal paths, and system details are logged internally, not surfaced to output that may leave the machine. [V16]
- Logs record security-relevant events (auth, access decisions, validation failures) with enough context to investigate, and without secrets or sensitive input. [V16]
- Log output is neutralized against injection: newlines and control characters in logged values are encoded so entries cannot be forged. [V16]
- On any failure path, partial state is cleaned up or rolled back so the system is left consistent. [V15]
- A security or quality signal that is produced but not enforced (telemetry-only gate, warn-and-continue) is treated as a defect: every gate signal has an enforcement path, or an explicit expiry date by which enforcement flips on. [V15]

## Dependencies & Architecture

- Only necessary dependencies are added, pinned to explicit versions, and sourced from trusted registries. [V15]
- New code reuses existing vetted helpers for security-sensitive operations (path resolution, escaping, subprocess launch) rather than reimplementing them. [V15]
- Components run with least privilege and trust boundaries are explicit: data crossing a boundary is validated on entry. [V15]
- Security-relevant defaults are chosen so that the safe path is the path of least resistance for callers. [V15]
