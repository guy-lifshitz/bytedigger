"""Token vocabulary — single source of truth for requirement verdicts and
adversary status (AC-C3). The hyphen/underscore split is deliberate: the
requirement verdicts are hyphenated, the adversary status is underscored
(bd#7 [G2:9]) — do not unify them.
"""

REQUIREMENT_PASSED = "passed"
REQUIREMENT_FAILED = "failed"
REQUIREMENT_NOT_CHECKED = "not-checked"
ADVERSARY_NOT_EXECUTED = "not_executed"
