# Project Constitution: [Project Name]

> Created by `/build --init`. Edit to match your project. Remove sections that don't apply.

## Stack
- Language: [e.g., TypeScript, Python, Swift, Java]
- Framework: [e.g., React, FastAPI, SwiftUI, Spring Boot]
- Runtime: [e.g., Bun, Node 20, Python 3.11, JDK 21]
- Package manager: [e.g., npm, pip, cargo, maven]
- Test framework: [e.g., Jest, pytest, XCTest, JUnit]
- Test command: [e.g., npm test, python3 -m pytest, swift test]
- Build command: [e.g., npm run build, python3 -m build]

## Principles
1. Tests before code — TDD is mandatory, no exceptions
2. Fail explicitly — never silently swallow errors or return defaults on failure
3. One pattern per concern — don't mix approaches (e.g., one error handling strategy, not three)
4. Minimal dependencies — prefer stdlib over external packages unless clear benefit
5. Names describe behavior — functions say what they do, variables say what they hold

## Patterns (Follow These)
1. Error handling: [e.g., return null for not-found, throw for invalid input, Result type for operations]
2. Naming: [e.g., camelCase functions, PascalCase types, UPPER_SNAKE constants]
3. File structure: [e.g., src/services/, src/routes/, tests/]
4. Logging: [e.g., structured JSON, use project logger not console.log]
5. Data validation: [e.g., validate at boundaries, trust internal calls]

## Anti-Patterns (Avoid These)
1. No silent catch — never `catch (e) {}` without logging or re-throwing
2. No any/unknown without reason — type everything, document exceptions
3. No business logic in handlers/controllers — extract to services
4. No hardcoded secrets — use environment variables or vault
5. No test-and-code coupling — tests validate behavior, not implementation details

## Testing Rules
- Every acceptance criterion must have a test
- Every edge case (null, empty, boundary, error) must have a test
- Negative tests: verify things that should NOT work
- No shared mutable state between tests
- Test names describe the scenario, not the function

## Security
- No secrets in code or config files
- Validate all external input (user input, API responses)
- Use parameterized queries, never string concatenation for queries
- Principle of least privilege for all access

## Constraints
- [Hard constraint, e.g., must run offline, max 100ms response time]
- [Hard constraint, e.g., no external API calls in tests]

## Out of Scope
- [What this project does NOT do]
- [Features explicitly excluded]
