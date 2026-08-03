"""reproducibility.py — 5-run reproducibility gate for GREEN test verification.

Runs a test command N times and checks that pass/fail counts are identical
across all runs. A non-reproducible suite indicates flaky tests or ordering
dependencies that must be resolved before Phase 2 delta enforcement.

Part of 585E30E3 P1b — engine-deterministic reproducibility gate.
"""
from __future__ import annotations


_REPRODUCIBILITY_RUNS = 5  # council-fixed, no env override


def verify_count_reproducible(
    run_fn,
    argv: list[str],
    git_cwd: str,
    *,
    n_runs: int = _REPRODUCIBILITY_RUNS,
    timeout: int = 120,
) -> tuple[bool, list[tuple[int, int]]]:
    """Run run_fn(argv, git_cwd, timeout=timeout) n_runs times.

    Returns (is_reproducible: bool, counts: list[tuple[int,int]]).
    counts[i] = (n_passed_i, n_failed_i). is_reproducible = all tuples identical.
    Any run raising → is_reproducible=False, that slot recorded as (-1,-1).
    """
    counts: list[tuple[int, int]] = []
    for _ in range(n_runs):
        try:
            result = run_fn(argv, git_cwd, timeout=timeout)
            counts.append((result.n_passed, result.n_failed))
        except FileNotFoundError:
            raise
        except Exception:
            counts.append((-1, -1))

    is_reproducible = (-1, -1) not in counts and len(set(counts)) == 1
    return is_reproducible, counts


def _pin_pytest_collection(argv: list[str]) -> list[str]:
    """Insert -p no:cacheprovider immediately after the pytest entrypoint token.

    For a pytest argv, inserts '-p no:cacheprovider' immediately after the
    pytest token if not already present; returns non-pytest argv unchanged.
    Idempotent: calling twice produces the same result as calling once.
    """
    # Find the pytest entrypoint token index.
    pytest_idx: int | None = None
    for i, token in enumerate(argv):
        if token == "pytest":
            pytest_idx = i
            break

    if pytest_idx is None:
        return argv

    # Check if -p no:cacheprovider is already present.
    for i in range(len(argv) - 1):
        if argv[i] == "-p" and argv[i + 1] == "no:cacheprovider":
            return argv

    # Insert immediately after the pytest token.
    result = list(argv)
    result.insert(pytest_idx + 1, "no:cacheprovider")
    result.insert(pytest_idx + 1, "-p")
    return result
