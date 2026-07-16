# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's advisory flow: the
Security tab of this repository, then "Report a vulnerability". Do not open a
public issue for anything exploitable -- issues are visible immediately,
advisories are not.

You can expect an acknowledgment within a few days. Coordinated disclosure is
fine; say in the report if you have a deadline.

## Scope

The published package is the engine core: the state machine, the event log,
the deterministic gates, the reference backends, and the Claude Code plugin
files in this repository. Anything not in this repository is out of scope.

One boundary worth being explicit about: the deterministic gates
(stub-passability, scope-inverse, test-integrity and the rest) exist to catch
an agent gaming its own acceptance signal. They are not a sandbox and were
never designed to contain hostile code. If your finding is "generated code can
do X on the host", read [docs/security.md](docs/security.md) first -- running
generated code in an isolated environment is the documented deployment
assumption, and reports that assume otherwise will likely be closed as
by-design. Findings that break the gates themselves (a RED test that passes
the stub-passability lint while mocking its UUT, a scope-inverse bypass, an
event-log forgery path) are very much in scope and welcome.
